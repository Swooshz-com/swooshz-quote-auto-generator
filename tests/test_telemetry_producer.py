import contextlib
import datetime as dt
import io
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
    apply_telemetry_attempt_semantics_migration,
)


TELEMETRY_MIGRATION = ROOT / "migrations" / "009_telemetry_events.sql"
TELEMETRY_ATTEMPT_MIGRATION = ROOT / "migrations" / "010_telemetry_attempt_semantics.sql"
FORENSIC_MIGRATION = ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql"
FIXED_NOW = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc)


def connection_with_telemetry() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(FORENSIC_MIGRATION.read_text(encoding="utf-8"))
    connection.executescript(TELEMETRY_MIGRATION.read_text(encoding="utf-8"))
    apply_telemetry_attempt_semantics_migration(connection)
    return connection


class TelemetryProducerTest(unittest.TestCase):
    def test_draft_failure_metadata_retains_only_safe_param_and_shape_hash(self):
        diagnostics = server.draft_provider_error_diagnostics({"error": {
            "type": "invalid_request_error", "code": "invalid_value",
            "param": "input[0].content[1].file_data",
            "message": "PRIVATE_PROVIDER_BODY customer@example.invalid",
        }})
        diagnostics.update({
            "failure_boundary": "provider_http", "attempt_number": 1,
            "request_shape_sha256": "a" * 64,
            "prompt": "PRIVATE_PROMPT", "headers": "PRIVATE_AUTHORIZATION",
            "data_url": "PRIVATE_MEDIA", "filename": "PRIVATE_FILENAME",
        })
        error = server.OpenAIAnalysisError("OpenAI analysis failed with HTTP 400.", diagnostics=diagnostics)
        metadata = server.ai_failure_metadata(error, provider="openai", error_reference="ERR-ABCDEF12")
        self.assertEqual(metadata["provider_error_param"], "input[0].content[1].file_data")
        self.assertEqual(metadata["request_shape_sha256"], "a" * 64)
        self.assertEqual(metadata["attempt_number"], 1)
        self.assertNotIn("PRIVATE", json.dumps(metadata))
        for invalid in ("input[0].private_customer", "https://private.invalid", "a" * 101):
            self.assertNotIn("provider_error_param", server.safe_ai_output_diagnostics({"provider_error_param": invalid}))

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
        with self.assertRaises(TelemetryConflictError):
            self.append("event-attempt-duplicate", attempt_number=1, **common)
        state = self.connection.execute(
            "select next_source_sequence, high_watermark from sqag_telemetry_source_state where workspace_id = ?",
            ("workspace-alpha",),
        ).fetchone()
        self.assertEqual(tuple(state), (3, 2))

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
        auth_session = {
            "auth_mode": server.INTERNAL_AUTH_MODE,
            "user": {
                "subject": "telemetry-test",
                "account": "workspace-alpha",
                "internal_role": "owner",
            },
        }
        record = {
            "feature": "basis_chat",
            "provider": "OpenAI",
            "model": "gpt-6-luna",
            "reasoning_level": "high",
            "operation_route": "/api/ai/basis-chat",
            "status": "success",
            "retry_lineage_id": "retry-ai-alpha",
            "attempt_number": 1,
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
        self.assertEqual(row["attempt_number"], 1)
        self.assertEqual(row["usage_available"], 1)
        self.assertEqual(row["cost_available"], 1)
        self.assertEqual(row["deployment_revision"], "run-356-revision")
        self.assertNotIn("prompt", row)
        self.assertNotIn("output", row)
        self.assertNotIn("request", row)
        self.assertNotIn("private prompt must not persist", json.dumps(row, sort_keys=True))

    def test_zero_send_validation_persists_only_canonical_semantics(self):
        auth_session = {"auth_mode": "platform"}
        record = {
            "feature": "basis_chat",
            "provider": "openai",
            "model": "model\\nPRIVATE_CANARY",
            "status": "failed",
            "retry_lineage_id": "retry-validation-alpha",
            "attempt_number": 0,
            "failure_boundary": "request_validation",
            "failure_kind": "configuration",
            "usage_available": 1,
            "input_tokens": 11,
            "estimated_cost_usd": 0.4,
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
        self.assertEqual(row["event_type"], "validation")
        self.assertEqual(row["event_status"], "blocked")
        self.assertEqual(row["attempt_number"], 0)
        self.assertEqual(row["purpose"], "request_validation")
        self.assertEqual(row["failure_class"], "configuration")
        self.assertIsNone(row["model"])
        self.assertEqual(row["usage_available"], 0)
        self.assertEqual(row["cost_available"], 0)
        for field in (
            "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
            "cache_write_tokens", "estimated_cost", "actual_cost", "currency", "cost_version",
        ):
            self.assertIsNone(row[field])
        self.assertNotIn("PRIVATE_CANARY", json.dumps(row, sort_keys=True))
        nullable_fields = ("purpose", "failure_class", "usage_available", "cost_available")
        columns = tuple(row)
        placeholders = ",".join("?" for _ in columns)
        for index, field in enumerate(nullable_fields, start=1):
            malformed = dict(row)
            malformed.update({
                "event_id": f"event-invalid-zero-null-{field}",
                "source_sequence": int(row["source_sequence"]) + index,
                "retry_lineage_id": None,
                field: None,
            })
            with self.subTest(field=field):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(
                        f"insert into sqag_telemetry_events ({','.join(columns)}) values ({placeholders})",
                        tuple(malformed[column] for column in columns),
                    )

    def test_transport_operation_clears_when_telemetry_append_fails(self):
        auth_session = {
            "auth_mode": server.INTERNAL_AUTH_MODE,
            "user": {"subject": "telemetry-test", "account": "workspace-alpha", "internal_role": "owner"},
        }
        with server.ai_log_tracking_scope({}, auth_session=auth_session):
            operation = server.begin_ai_transport_operation(
                "basis_chat",
                server.AI_PROVIDER_OPENAI,
                server.OPENAI_BASIS_LINE_MODEL,
            )
            attempt_number = server.next_ai_transport_send_number(
                "basis_chat",
                server.AI_PROVIDER_OPENAI,
                server.OPENAI_BASIS_LINE_MODEL,
            )
            with mock.patch.object(server, "write_local_log", return_value=True), mock.patch.object(
                server,
                "append_ai_attempt_telemetry",
                side_effect=RuntimeError("telemetry persistence failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "telemetry persistence failed"):
                    server.log_ai_call_attempt(
                        feature="basis_chat",
                        provider=server.AI_PROVIDER_OPENAI,
                        model=server.OPENAI_BASIS_LINE_MODEL,
                        status="failed",
                        retry_lineage_id=operation["retry_lineage_id"],
                        attempt_number=attempt_number,
                    )
            self.assertIsNone(getattr(server.AI_LOG_TRACKING_CONTEXT, "transport_operation", None))

    def test_recursive_model_projection_keeps_only_supported_provider_labels(self):
        value = {
            "provider": "openai",
            "model": "gpt-6-luna",
            "provider_attempts": [
                {"provider": "deepseek", "model": "deepseek-v4-flash"},
                {"provider": "openai", "model": "gpt-6-sol"},
                {
                    "from_provider": "openai",
                    "from_model": "gpt-6-luna",
                    "to_provider": "openai",
                    "to_model": "model\nPRIVATE_CANARY",
                },
                {"provider": "openai", "model": "model\\nPRIVATE_CANARY"},
                {"provider": "openai", "model": "model\\\\nPRIVATE_CANARY"},
                {"provider": "openai", "model": "!!!"},
                {"provider": "openai", "model": "private@example.invalid"},
                {"provider": "openai", "model": "PRIVATE_CANARY/customer confidential note"},
            ],
        }
        projected = server.project_provider_model_fields(value)
        encoded = json.dumps(projected, sort_keys=True)
        self.assertIn("gpt-6-luna", encoded)
        self.assertIn("deepseek-v4-flash", encoded)
        self.assertNotIn("gpt-6-sol", encoded)
        self.assertNotIn("PRIVATE_CANARY", encoded)
        self.assertNotIn("private@example.invalid", encoded)
        self.assertNotIn("customer confidential note", encoded)
        self.assertNotIn("!!!", encoded)

    def test_later_invalid_model_preserves_prior_actual_send_and_stops_fallback(self):
        auth_session = {
            "auth_mode": server.INTERNAL_AUTH_MODE,
            "user": {
                "subject": "telemetry-test",
                "account": "workspace-alpha",
                "internal_role": "owner",
            },
        }
        candidates = [
            {"provider": "deepseek", "model": server.DEEPSEEK_PRO_MODEL},
            {"provider": "deepseek", "model": "deepseek-v4-pro\nPRIVATE_CANARY"},
        ]

        def env_value(name: str) -> str:
            return "sk-test-redacted" if name == server.DEEPSEEK_API_KEY_ENV_NAME else ""

        def fail_with_http_error(request, **kwargs):
            raise server.urllib.error.HTTPError(
                "https://api.deepseek.com/chat/completions",
                400,
                "bad request",
                {},
                io.BytesIO(b"{}"),
            )

        with (
            mock.patch.object(server, "read_dotenv_value", side_effect=env_value),
            mock.patch.object(server, "basis_chat_provider_model_candidates", return_value=candidates),
            mock.patch.object(server.urllib.request, "urlopen", side_effect=fail_with_http_error) as urlopen,
            mock.patch.object(server, "write_local_log") as write_local_log,
            mock.patch.object(
                server,
                "forensic_store_for_auth_session",
                return_value=contextlib.nullcontext(self.store),
            ),
            server.ai_log_tracking_scope({}, auth_session=auth_session),
        ):
            with self.assertRaises(server.OpenAIAnalysisError) as raised:
                server.request_configured_basis_chat({
                    "basis_chat": {
                        "question": "what does this mean?",
                        "scope": "quote",
                        "field": "",
                        "line_index": -1,
                        "line": "",
                    }
                })

        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(raised.exception.diagnostics["failure_boundary"], "request_validation")
        self.assertNotIn("PRIVATE_CANARY", str(raised.exception) + json.dumps(raised.exception.diagnostics))
        rows = [
            dict(row)
            for row in self.connection.execute(
                "select event_type, event_status, attempt_number, retry_lineage_id, provider, model, "
                "usage_available, cost_available from sqag_telemetry_events order by source_sequence"
            )
        ]
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            [(row["event_type"], row["event_status"], row["attempt_number"]) for row in rows],
            [("ai_provider_attempt", "failed", 1), ("validation", "blocked", 0)],
        )
        self.assertEqual(rows[0]["model"], server.DEEPSEEK_PRO_MODEL)
        self.assertIsNone(rows[1]["model"])
        self.assertNotEqual(rows[0]["retry_lineage_id"], rows[1]["retry_lineage_id"])
        self.assertEqual((rows[1]["usage_available"], rows[1]["cost_available"]), (0, 0))
        retry_details = [
            server.project_provider_model_fields(call.args[1])
            for call in write_local_log.call_args_list
            if len(call.args) > 1 and isinstance(call.args[1], dict)
        ]
        self.assertNotIn("PRIVATE_CANARY", json.dumps(retry_details, sort_keys=True))

    def test_sqlite_010_rebuild_preserves_existing_rows_and_rejects_unbounded_zero(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(FORENSIC_MIGRATION.read_text(encoding="utf-8"))
        connection.executescript(TELEMETRY_MIGRATION.read_text(encoding="utf-8"))
        legacy_store = ForensicStore(connection, "workspace-upgrade", "pid-v1-upgrade")
        legacy_store.append_telemetry_event(
            "ai_provider_attempt",
            "success",
            event_id="event-before-010",
            retry_lineage_id="lineage-before-010",
            attempt_number=1,
            provider="openai",
            model="gpt-6-luna",
            now=FIXED_NOW,
        )
        before = dict(
            connection.execute(
                "select * from sqag_telemetry_events where event_id = ?",
                ("event-before-010",),
            ).fetchone()
        )

        apply_telemetry_attempt_semantics_migration(connection)
        apply_telemetry_attempt_semantics_migration(connection)
        after = dict(
            connection.execute(
                "select * from sqag_telemetry_events where event_id = ?",
                ("event-before-010",),
            ).fetchone()
        )
        self.assertEqual(after, before)

        invalid = dict(after)
        invalid.update({
            "event_id": "event-invalid-zero",
            "source_sequence": 2,
            "attempt_number": 0,
            "event_type": "ai_provider_attempt",
            "event_status": "failed",
            "purpose": "basis_chat",
            "failure_class": "provider_error",
            "model": "gpt-6-luna",
            "immutable_metadata_digest": "a" * 64,
        })
        columns = tuple(invalid)
        placeholders = ",".join("?" for _ in columns)
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                f"insert into sqag_telemetry_events ({','.join(columns)}) values ({placeholders})",
                tuple(invalid[column] for column in columns),
            )
        connection.close()

    def test_sqlite_010_preserves_caller_transaction_ownership(self):
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(FORENSIC_MIGRATION.read_text(encoding="utf-8"))
            connection.executescript(TELEMETRY_MIGRATION.read_text(encoding="utf-8"))
            connection.execute("create table caller_state (value text not null)")
            connection.execute(
                "insert into caller_state (value) values (?)",
                ("uncommitted-upgrade",),
            )
            self.assertTrue(connection.in_transaction)
            with self.assertRaisesRegex(RuntimeError, "active transaction"):
                apply_telemetry_attempt_semantics_migration(connection)
            self.assertTrue(connection.in_transaction)
            self.assertEqual(
                connection.execute("select value from caller_state").fetchone()[0],
                "uncommitted-upgrade",
            )
            connection.rollback()
            self.assertEqual(connection.execute("select value from caller_state").fetchall(), [])

            apply_telemetry_attempt_semantics_migration(connection)
            connection.execute(
                "insert into caller_state (value) values (?)",
                ("uncommitted-canonical-noop",),
            )
            self.assertTrue(connection.in_transaction)
            apply_telemetry_attempt_semantics_migration(connection)
            self.assertTrue(connection.in_transaction)
            connection.rollback()
            self.assertEqual(connection.execute("select value from caller_state").fetchall(), [])
        finally:
            connection.close()

    def test_sqlite_010_rejects_partial_attempt_zero_schema_marker(self):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            "create table sqag_telemetry_events (attempt_number integer, purpose text, check ("
            "attempt_number is null or attempt_number >= 1 or ("
            "attempt_number = 0 and purpose = 'request_validation')))"
        )
        with self.assertRaisesRegex(RuntimeError, "attempt constraint drifted"):
            apply_telemetry_attempt_semantics_migration(connection)
        connection.close()

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
