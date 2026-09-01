import contextlib
import datetime as dt
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]

from webapp import server
from webapp.forensics import (
    ForensicStore,
    TELEMETRY_EVENT_TYPES,
    TelemetryConflictError,
    TelemetryUnavailableError,
)


TELEMETRY_MIGRATION = ROOT / "migrations" / "009_telemetry_events.sql"
FORENSIC_MIGRATION = ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql"
FIXED_NOW = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


def connection_with_telemetry() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(FORENSIC_MIGRATION.read_text(encoding="utf-8"))
    connection.executescript(TELEMETRY_MIGRATION.read_text(encoding="utf-8"))
    return connection


class TelemetryProducerTest(unittest.TestCase):
    def setUp(self):
        self.connection = connection_with_telemetry()
        self.store = ForensicStore(self.connection, "workspace-alpha", "pid-v1-alpha")
        self.other_store = ForensicStore(self.connection, "workspace-beta", "pid-v1-beta")

    def tearDown(self):
        self.connection.close()

    def append(self, event_id: str, event_type: str = "generation", status: str = "started", **fields):
        fields.setdefault("now", FIXED_NOW)
        return self.store.append_telemetry_event(
            event_type,
            status,
            event_id=event_id,
            **fields,
        )

    def test_migration_is_repeatable_and_schema_is_metadata_only(self):
        self.connection.executescript(TELEMETRY_MIGRATION.read_text(encoding="utf-8"))
        tables = {
            row["name"]
            for row in self.connection.execute(
                "select name from sqlite_master where type = 'table'"
            )
        }
        self.assertIn("sqag_telemetry_source_state", tables)
        self.assertIn("sqag_telemetry_events", tables)
        columns = {
            row["name"]
            for row in self.connection.execute("pragma table_info(sqag_telemetry_events)")
        }
        self.assertNotIn("payload", columns)
        self.assertNotIn("content_json", columns)
        self.assertNotIn("prompt", columns)
        self.assertNotIn("output", columns)
        trigger_names = {
            row["name"]
            for row in self.connection.execute(
                "select name from sqlite_master where type = 'trigger'"
            )
        }
        self.assertGreaterEqual(
            trigger_names,
            {
                "sqag_telemetry_source_state_no_delete",
                "sqag_telemetry_events_no_update",
                "sqag_telemetry_events_guard_delete",
            },
        )

    def test_schema_guards_protect_immutable_events_and_source_state(self):
        self.append("event-immutable")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "update sqag_telemetry_events set event_status = 'failed' "
                "where workspace_id = ? and event_id = ?",
                ("workspace-alpha", "event-immutable"),
            )
        self.connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "delete from sqag_telemetry_events where workspace_id = ? and event_id = ?",
                ("workspace-alpha", "event-immutable"),
            )
        self.connection.rollback()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "delete from sqag_telemetry_source_state where workspace_id = ? and source_product = 'sqag'",
                ("workspace-alpha",),
            )
        self.connection.rollback()

    def test_strict_classification_and_provider_metadata_validation(self):
        with self.assertRaises(ValueError):
            self.append("event-invalid-type", event_type="customer_payload")
        with self.assertRaises(ValueError):
            self.append("event-invalid-status", status="accepted")
        with self.assertRaises(ValueError):
            self.append("event-invalid-provider", event_type="ai_provider_attempt", provider="unknown")
        with self.assertRaises(ValueError):
            self.append("event-invalid-reasoning", event_type="ai_provider_attempt", reasoning_level="freeform")
        with self.assertRaises(ValueError):
            self.append("event-invalid-decision", quota_decision="maybe")
        with self.assertRaises(TypeError):
            self.store.append_telemetry_event(
                "generation",
                "started",
                event_id="event-arbitrary-field",
                payload={"customer": "must-not-be-stored"},
            )

    def test_append_sequence_workspace_isolation_and_idempotent_digest_replay(self):
        first = self.append(
            "event-sequence-a",
            action_reference="job-telemetry-a",
            operation_route="generation",
            purpose="generation_input",
        )
        replay = self.append(
            "event-sequence-a",
            action_reference="job-telemetry-a",
            operation_route="generation",
            purpose="generation_input",
        )
        second = self.append("event-sequence-b")
        other = self.other_store.append_telemetry_event(
            "generation",
            "started",
            event_id="event-sequence-other",
            now=FIXED_NOW,
        )

        self.assertEqual(first["source_sequence"], 1)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(second["source_sequence"], 2)
        self.assertEqual(other["source_sequence"], 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_telemetry_events where workspace_id = ? and event_id = ?",
                ("workspace-alpha", "event-sequence-a"),
            ).fetchone()[0],
            1,
        )
        with self.assertRaises(TelemetryConflictError):
            self.append("event-sequence-a", status="failed")
        self.connection.rollback()
        with self.assertRaises(TelemetryConflictError):
            self.append(
                "event-sequence-a",
                immutable_metadata_digest="0" * 64,
                action_reference="job-conflicting",
            )
        self.connection.rollback()

    def test_concurrent_appends_allocate_unique_monotonic_workspace_sequences(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            database_path = Path(raw_dir) / "telemetry.sqlite3"
            seed = sqlite3.connect(database_path)
            seed.row_factory = sqlite3.Row
            seed.executescript(FORENSIC_MIGRATION.read_text(encoding="utf-8"))
            seed.executescript(TELEMETRY_MIGRATION.read_text(encoding="utf-8"))
            seed.commit()
            seed.close()

            barrier = threading.Barrier(2)
            results = []
            errors = []
            result_lock = threading.Lock()

            def append_from_connection(index: int) -> None:
                connection = sqlite3.connect(database_path, timeout=10, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                try:
                    barrier.wait(timeout=5)
                    result = ForensicStore(
                        connection,
                        "workspace-concurrent",
                        f"pid-v1-concurrent-{index}",
                    ).append_telemetry_event(
                        "generation",
                        "started",
                        event_id=f"event-concurrent-{index}",
                        now=FIXED_NOW,
                    )
                    with result_lock:
                        results.append(result)
                except BaseException as exc:
                    with result_lock:
                        errors.append(exc)
                finally:
                    connection.close()

            threads = [
                threading.Thread(target=append_from_connection, args=(index,))
                for index in (1, 2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(
                sorted(result["source_sequence"] for result in results),
                [1, 2],
            )

            check = sqlite3.connect(database_path)
            check.row_factory = sqlite3.Row
            try:
                state = check.execute(
                    "select next_source_sequence, high_watermark "
                    "from sqag_telemetry_source_state where workspace_id = ?",
                    ("workspace-concurrent",),
                ).fetchone()
                self.assertEqual(dict(state), {"next_source_sequence": 3, "high_watermark": 2})
            finally:
                check.close()

    def test_retry_lineage_distinguishes_attempts_and_rejects_duplicate_attempt(self):
        common = {
            "event_type": "ai_provider_attempt",
            "status": "success",
            "provider": "openai",
            "model": "gpt-5.6",
            "reasoning_level": "high",
            "retry_lineage_id": "retry-lineage-alpha",
        }
        first = self.append("event-attempt-1", attempt_number=1, **common)
        second = self.append("event-attempt-2", attempt_number=2, **common)
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_telemetry_events where retry_lineage_id = ?",
                ("retry-lineage-alpha",),
            ).fetchone()[0],
            2,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.append("event-attempt-duplicate", attempt_number=1, **common)
        self.connection.rollback()

    def test_usage_and_cost_remain_nullable_unless_truthfully_available(self):
        unavailable = self.append("event-no-usage", event_type="ai_provider_attempt", provider="openai")
        available = self.append(
            "event-usage",
            event_type="ai_provider_attempt",
            provider="openai",
            input_tokens=12,
            output_tokens=3,
            total_tokens=15,
            actual_cost=0,
            currency="USD",
        )
        explicit_unavailable = self.append(
            "event-explicit-no-usage",
            event_type="ai_provider_attempt",
            provider="openai",
            usage_available=0,
            cost_available=0,
        )
        self.assertIsNone(unavailable["usage_available"])
        self.assertIsNone(unavailable["cost_available"])
        self.assertEqual(available["usage_available"], 1)
        self.assertEqual(available["cost_available"], 1)
        self.assertEqual(available["actual_cost"], 0)
        self.assertEqual(explicit_unavailable["usage_available"], 0)
        self.assertEqual(explicit_unavailable["cost_available"], 0)

    def test_feed_is_workspace_scoped_exclusive_high_watermark_and_read_only(self):
        self.append("event-feed-a")
        self.append("event-feed-b", event_type="validation", status="success")
        state_before = dict(
            self.connection.execute(
                "select * from sqag_telemetry_source_state where workspace_id = ?",
                ("workspace-alpha",),
            ).fetchone()
        )
        first_page = self.store.feed_telemetry_events(limit=1)
        state_after = dict(
            self.connection.execute(
                "select * from sqag_telemetry_source_state where workspace_id = ?",
                ("workspace-alpha",),
            ).fetchone()
        )
        second_page = self.store.feed_telemetry_events(first_page["next_cursor"], limit=10)
        self.assertEqual([item["event_id"] for item in first_page["events"]], ["event-feed-a"])
        self.assertEqual([item["event_id"] for item in second_page["events"]], ["event-feed-b"])
        self.assertEqual(first_page["high_watermark"], 2)
        self.assertEqual(state_before, state_after)
        self.assertNotIn("event-sequence-other", {item["event_id"] for item in first_page["events"]})

        self.connection.execute(
            "update sqag_telemetry_source_state set high_watermark = 0, next_source_sequence = 1 "
            "where workspace_id = ? and source_product = 'sqag'",
            ("workspace-alpha",),
        )
        self.connection.commit()
        with self.assertRaises(TelemetryUnavailableError):
            self.store.feed_telemetry_events()

    def test_retention_hold_delete_and_source_state_survival(self):
        expired = self.store.append_telemetry_event(
            "retention",
            "completed",
            event_id="event-retention",
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        self.assertTrue(
            self.store.set_legal_hold(
                "sqag_telemetry_events",
                "event_id",
                expired["event_id"],
                True,
                now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            )
        )
        held = self.store.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
        self.assertGreaterEqual(held.telemetry_held, 1)
        self.assertIsNotNone(
            self.connection.execute(
                "select 1 from sqag_telemetry_events where workspace_id = ? and event_id = ?",
                ("workspace-alpha", "event-retention"),
            ).fetchone()
        )
        self.assertTrue(
            self.store.set_legal_hold(
                "sqag_telemetry_events",
                "event_id",
                expired["event_id"],
                False,
                now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            )
        )
        deleted = self.store.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
        self.assertGreaterEqual(deleted.telemetry_deleted, 1)
        self.assertIsNone(
            self.connection.execute(
                "select 1 from sqag_telemetry_events where workspace_id = ? and event_id = ?",
                ("workspace-alpha", "event-retention"),
            ).fetchone()
        )
        state = dict(
            self.connection.execute(
                "select * from sqag_telemetry_source_state where workspace_id = ?",
                ("workspace-alpha",),
            ).fetchone()
        )
        self.assertEqual(state["source_product"], "sqag")
        self.assertGreaterEqual(state["high_watermark"], 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_deletion_receipts "
                "where workspace_id = ? and record_type = 'sqag_telemetry_events' and record_id = ?",
                ("workspace-alpha", "event-retention"),
            ).fetchone()[0],
            1,
        )

    def test_generation_special_states_and_event_class_contract(self):
        for index, status in enumerate(("cancelled", "timed_out", "abandoned", "superseded"), start=1):
            run_id = self.store.record_run_started(
                "generate",
                {"synthetic": True},
                run_id=f"run-special-{index}",
                now=FIXED_NOW,
            )
            self.assertTrue(self.store.finish_run(run_id, status, now=FIXED_NOW))
        event_types = {
            row["event_type"]
            for row in self.connection.execute(
                "select event_type from sqag_telemetry_events where workspace_id = ?",
                ("workspace-alpha",),
            )
        }
        self.assertGreaterEqual(
            event_types,
            {"generation", "cancellation", "timeout", "abandonment", "supersession"},
        )
        self.assertTrue(
            {
                "generation",
                "validation",
                "ai_provider_attempt",
                "pricing_change",
                "profile_change",
                "publication",
                "download",
                "feedback",
                "security",
                "rate_limit",
                "abuse",
                "cancellation",
                "timeout",
                "abandonment",
                "supersession",
                "storage_staging",
                "storage_finalization",
                "storage_compensation",
                "configuration",
                "operator_action",
                "reconciliation",
                "retention",
                "legal_hold",
                "deletion",
                "backup",
                "restore",
            }.issubset(TELEMETRY_EVENT_TYPES)
        )
        source = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts" / "verify_database_backup_restore.py").read_text(encoding="utf-8")
        for event_type in (
            "pricing_change", "profile_change", "publication", "download", "security",
            "rate_limit", "abuse", "storage_staging", "storage_finalization",
            "storage_compensation", "configuration", "operator_action",
        ):
            self.assertIn(f'"{event_type}"', source)
        self.assertIn('"backup"', verifier)
        self.assertIn('"restore"', verifier)

    def test_ai_attempt_adapter_is_metadata_only_and_preserves_available_evidence(self):
        auth_session = {"auth_mode": "platform"}
        record = {
            "feature": "basis_chat",
            "provider": "OpenAI",
            "model": "gpt-5.6",
            "reasoning_level": "high",
            "operation_route": "/api/ai/basis-chat",
            "status": "success",
            "retry_lineage_id": "retry-ai-alpha",
            "attempt_index": 2,
            "batch_index": 3,
            "duration_ms": 125,
            "usage_available": 1,
            "input_tokens": 12,
            "output_tokens": 8,
            "total_tokens": 20,
            "estimated_cost_usd": 0.02,
            "actual_cost_usd": 0.03,
            "cost_version": "synthetic-v1",
            "quota_decision": "allowed",
            "rate_limit_decision": "not_evaluated",
            "abuse_decision": "allowed",
            "deployment_revision": "run-356-revision",
            "prompt": "private prompt must not persist",
            "output": "private model output must not persist",
            "request": {"private": True},
        }
        with mock.patch.object(
            server,
            "forensic_store_for_auth_session",
            return_value=contextlib.nullcontext(self.store),
        ):
            result = server.append_ai_attempt_telemetry(auth_session, record)
        row = dict(
            self.connection.execute(
                "select * from sqag_telemetry_events where event_id = ?",
                (result["event_id"],),
            ).fetchone()
        )
        self.assertEqual(row["provider"], "openai")
        self.assertEqual(row["reasoning_level"], "high")
        self.assertEqual(row["operation_route"], "/api/ai/basis-chat")
        self.assertEqual(row["attempt_number"], 2003)
        self.assertEqual(row["usage_available"], 1)
        self.assertEqual(row["cost_available"], 1)
        self.assertEqual(row["deployment_revision"], "run-356-revision")
        self.assertNotIn("prompt", row)
        self.assertNotIn("output", row)
        self.assertNotIn("request", row)
        self.assertNotIn("private prompt must not persist", json.dumps(row, sort_keys=True))

    def test_feed_cursor_auth_tenant_binding_and_query_validation(self):
        self.append("event-cursor")
        session = {
            "auth_mode": "platform",
            "user": {
                "subject": "platform-user-alpha",
                "account": "workspace-alpha",
                "platform": {
                    "outcome": "consumed",
                    "user": {"userId": "platform-user-alpha"},
                    "workspace": {"workspaceId": "workspace-alpha"},
                    "app": {"appKey": "sqag"},
                    "membershipRole": "owner",
                    "validationGrantId": "grant-alpha",
                },
            },
        }
        with mock.patch.dict(
            os.environ,
            {
                "APP_MODE": "deploy",
                "SQAG_AUTH_MODE": "platform",
                "SESSION_SECRET": "synthetic-session-secret-for-telemetry",
            },
            clear=True,
        ), mock.patch.object(
            server,
            "forensic_store_for_auth_session",
            return_value=contextlib.nullcontext(self.store),
        ):
            result = server.telemetry_feed_for_auth_session(session, "limit=1")
            cursor = result["next_cursor"]
            self.assertEqual(server.decode_telemetry_cursor(cursor, "workspace-alpha"), (1, "event-cursor"))
            with self.assertRaises(ValueError):
                server.decode_telemetry_cursor(cursor, "workspace-beta")
            with self.assertRaises(ValueError):
                server.decode_telemetry_cursor(cursor[:-1] + ("A" if cursor[-1] != "A" else "B"), "workspace-alpha")
            for query in (
                "workspace_id=workspace-beta",
                "limit=0",
                "limit=501",
                "limit=1&limit=2",
                "cursor=",
            ):
                with self.assertRaises(ValueError):
                    server.parse_telemetry_feed_query(query)
            self.assertEqual(server.parse_telemetry_feed_query(""), ("", 100))
            self.assertEqual(server.parse_telemetry_feed_query("limit=500"), ("", 500))

            member = dict(session)
            member["user"] = dict(session["user"])
            member["user"]["platform"] = dict(session["user"]["platform"])
            member["user"]["platform"]["membershipRole"] = "member"
            with self.assertRaises(PermissionError):
                server.telemetry_feed_for_auth_session(member, "")
        with self.assertRaises(PermissionError):
            server.telemetry_feed_for_auth_session(None, "")

    def test_feed_fails_closed_when_source_state_is_inconsistent(self):
        self.append("event-unavailable")
        self.connection.execute(
            "update sqag_telemetry_source_state set reconciliation_state = 'inconsistent' "
            "where workspace_id = ? and source_product = 'sqag'",
            ("workspace-alpha",),
        )
        self.connection.commit()
        with self.assertRaises(TelemetryUnavailableError):
            self.store.feed_telemetry_events()


if __name__ == "__main__":
    unittest.main()
