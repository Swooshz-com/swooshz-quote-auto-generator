import contextlib
import datetime as dt
import os
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webapp.forensics import ForensicStore
from webapp import server as webapp
from scripts import enforce_log_retention


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql"


class Pr140SixBlockerRedTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))

    def tearDown(self):
        self.connection.close()

    def store(self, workspace_id="workspace-six", actor="pid-v1-six-user"):
        return ForensicStore(self.connection, workspace_id, actor)

    def test_session_only_feedback_resolves_unique_artifact_free_run(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(
            run_id,
            "blocked",
            quote_session_id="quote-session-only",
            canonical_manifest={"artifacts": []},
        )
        feedback = store.submit_feedback(
            {
                "category": "failed_process",
                "title": "Synthetic session-only feedback",
                "message": "Synthetic session-only feedback.",
                "validated_session_id": "quote-session-only",
            }
        )
        with (
            mock.patch.object(webapp, "require_support_forensics"),
            mock.patch.object(
                webapp,
                "forensic_store_for_auth_session",
                return_value=contextlib.nullcontext(store),
            ),
        ):
            result = webapp.support_feedback_evidence_for_auth_session(
                feedback["support_reference"],
                "support_investigation",
                {"synthetic": True},
            )
        self.assertTrue(result["integrity_ok"])
        self.assertEqual(result["run_id"], run_id)

    def test_internal_uat_atomic_publication_failure_is_not_false_success(self):
        class Store:
            connection = mock.Mock()

            def finish_run(self, *_args, **_kwargs):
                raise RuntimeError("synthetic finalisation failure")

        store = Store()
        with (
            mock.patch.object(webapp, "configured_app_mode", return_value="internal-uat"),
            mock.patch.object(
                webapp,
                "forensic_store_for_auth_session",
                return_value=contextlib.nullcontext(store),
            ),
            mock.patch.object(webapp, "write_local_log"),
        ):
            result = webapp.finish_generation_forensics(
                "run-publication-red",
                {"status": "completed", "files": [{"name": "quotation.xlsx"}]},
                {"synthetic": True},
                canonical_manifest={"artifacts": []},
                publication_storage=mock.Mock(),
                publication_session_id="quote-publication-red",
                publication_files=[],
            )
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("files", result)

    def test_support_artifact_verification_uses_narrow_privileged_reader(self):
        self.assertTrue(
            hasattr(webapp, "support_forensic_artifact_reader_for_auth_session"),
            "support evidence has no narrow workspace-scoped owner-bypass boundary",
        )

    def test_partial_forensic_schema_missing_legal_hold_fails_readiness(self):
        self.connection.execute(
            "alter table sqag_audit_events drop column legal_hold"
        )
        storage = object.__new__(webapp.DatabaseSqagStorage)
        storage.database_family = "sqlite"
        storage.connection = lambda: contextlib.nullcontext(self.connection)
        with self.assertRaises(webapp.SqagStorageAccessError):
            storage._ensure_schema(
                webapp.SQAG_FORENSIC_REQUIRED_COLUMNS,
                reason="storage_forensics_database_not_migrated",
            )
    def test_expired_standalone_audit_is_selected_and_deleted(self):
        store = self.store()
        event_id = store.append_audit(
            "synthetic_standalone",
            {"synthetic": True},
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc)
        )
        self.assertGreaterEqual(result.deleted, 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events where event_id = ?",
                (event_id,),
            ).fetchone()[0],
            0,
        )

    def test_evidence_read_has_dedicated_route_limit(self):
        key = webapp.rate_limit_path_key(
            "/api/support/feedback/SUP-REDACTED/evidence"
        )
        self.assertIn(key, webapp.POST_RATE_LIMITS)
        self.assertLess(webapp.POST_RATE_LIMITS[key], 30)

    def test_session_resolution_prefers_exact_published_run_among_history(self):
        store = self.store()
        older = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(older, "completed", quote_session_id="quote-history", canonical_manifest={"artifacts": []})
        current = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(current, "completed", quote_session_id="quote-history", canonical_manifest={"artifacts": []})
        resolved = store.resolve_feedback_evidence_run(
            {"session_id": "quote-history"},
            publication_context_factory=lambda: {"state": "published", "run_id": current},
        )
        self.assertEqual(resolved, current)

    def test_session_resolution_rejects_ambiguity_and_staged_publication(self):
        store = self.store()
        first = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(first, "completed", quote_session_id="quote-ambiguous", canonical_manifest={"artifacts": []})
        second = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(second, "completed", quote_session_id="quote-ambiguous", canonical_manifest={"artifacts": []})
        with self.assertRaises(LookupError):
            store.resolve_feedback_evidence_run({"session_id": "quote-ambiguous"})
        with self.assertRaises(LookupError):
            store.resolve_feedback_evidence_run(
                {"session_id": "quote-ambiguous"},
                publication_context_factory=lambda: {"state": "staged", "run_id": second},
            )

    def test_session_resolution_is_workspace_scoped_and_missing_is_unavailable(self):
        other = self.store("workspace-other")
        run_id = other.record_run_started("generate", {"synthetic": True})
        other.finish_run(run_id, "completed", quote_session_id="quote-private", canonical_manifest={"artifacts": []})
        with self.assertRaisesRegex(LookupError, "Forensic evidence is not available"):
            self.store().resolve_feedback_evidence_run({"session_id": "quote-private"})
        with self.assertRaisesRegex(LookupError, "Forensic evidence is not available"):
            self.store().resolve_feedback_evidence_run({"session_id": "quote-missing"})

    def test_forensic_schema_readiness_requires_indexes_and_triggers(self):
        storage = object.__new__(webapp.DatabaseSqagStorage)
        storage.database_family = "sqlite"
        storage.connection = lambda: contextlib.nullcontext(self.connection)
        storage._ensure_schema(
            webapp.SQAG_FORENSIC_REQUIRED_COLUMNS,
            reason="storage_forensics_database_not_migrated",
        )
        self.connection.execute("drop index sqag_generation_runs_workspace_idempotency_uidx")
        with self.assertRaises(webapp.SqagStorageAccessError):
            storage._ensure_schema(
                webapp.SQAG_FORENSIC_REQUIRED_COLUMNS,
                reason="storage_forensics_database_not_migrated",
            )
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.connection.execute("drop trigger sqag_audit_events_guard_delete")
        with self.assertRaises(webapp.SqagStorageAccessError):
            storage._ensure_schema(
                webapp.SQAG_FORENSIC_REQUIRED_COLUMNS,
                reason="storage_forensics_database_not_migrated",
            )

    def test_postgres_contract_contains_every_required_forensic_object(self):
        sql = (ROOT / "migrations" / "004_generation_forensics_feedback_retention_postgres.sql").read_text(encoding="utf-8").lower()
        sql += (ROOT / "migrations" / "005_forensic_postgres_delete_guards.sql").read_text(encoding="utf-8").lower()
        for table, columns in webapp.SQAG_FORENSIC_REQUIRED_COLUMNS.items():
            self.assertIn(table.lower(), sql)
            for column in columns:
                self.assertIn(column.lower(), sql, f"{table}.{column}")
        for name in webapp.SQAG_FORENSIC_REQUIRED_INDEXES:
            self.assertIn(name.lower(), sql)
        for name in webapp.SQAG_FORENSIC_REQUIRED_TRIGGERS:
            self.assertIn(name.lower(), sql)
        for name in webapp.SQAG_FORENSIC_POSTGRES_REQUIRED_ROUTINES:
            self.assertIn(name.lower(), sql)

    def test_standalone_audit_retention_respects_expiry_hold_release_and_workspace(self):
        store = self.store()
        now = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        held_id = store.append_audit("synthetic_held", {"synthetic": True}, now=now)
        future_id = store.append_audit(
            "synthetic_future", {"synthetic": True},
            now=dt.datetime(2023, 1, 2, tzinfo=dt.timezone.utc),
        )
        self.connection.execute(
            "insert into sqag_legal_holds (hold_id, workspace_id, target_type, target_id, enabled, reason_code, actor_tracking_id, actor_key_version, created_at) values (?, ?, 'audit_event', ?, 1, 'synthetic_hold', ?, 'v1', ?)",
            ("hold-synthetic", "workspace-six", held_id, "actor-synthetic", "2020-01-01T00:00:00Z"),
        )
        result = store.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
        self.assertEqual(result.standalone_deleted, 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_audit_events where event_id = ?", (future_id,)).fetchone()[0], 1)
        self.connection.execute(
            "update sqag_legal_holds set enabled = 0 where hold_id = 'hold-synthetic'"
        )
        result = store.enforce_retention(now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc))
        self.assertEqual(result.standalone_deleted, 1)
        self.assertEqual(self.store("workspace-other").enforce_retention(now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc)).standalone_deleted, 0)

    def test_evidence_rate_limit_blocks_next_request_then_resets_by_window(self):
        webapp.RATE_LIMIT_BUCKETS.clear()
        webapp.RATE_LIMIT_OVERFLOW_BUCKETS.clear()
        path = "/api/support/feedback/SUP-REDACTED/evidence"
        limit = webapp.POST_RATE_LIMITS[webapp.rate_limit_path_key(path)]
        for _ in range(limit):
            self.assertFalse(webapp.is_rate_limited("198.51.100.25", path, now=1000))
        self.assertTrue(webapp.is_rate_limited("198.51.100.25", path, now=1001))
        self.assertFalse(webapp.is_rate_limited("198.51.100.26", path, now=1001))
        self.assertFalse(
            webapp.is_rate_limited(
                "198.51.100.25", path,
                now=1000 + webapp.RATE_LIMIT_WINDOW_SECONDS + 1,
            )
        )

    def test_retention_interleaves_kinds_behind_open_feedback_backlog(self):
        store = self.store()
        for index in range(17):
            feedback = store.submit_feedback(
                {
                    "category": "bug",
                    "title": f"Synthetic backlog {index}",
                    "message": "Synthetic expired open feedback.",
                }
            )
            self.connection.execute(
                "update sqag_feedback set retention_expires_at = ?, original_retention_expires_at = ? where feedback_id = ?",
                ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", feedback["feedback_id"]),
            )
        standalone_id = store.append_audit(
            "synthetic_standalone_behind_feedback",
            {"synthetic": True},
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        self.connection.commit()
        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1
        )
        self.assertEqual(result.standalone_deleted, 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events where event_id = ?", (standalone_id,)
            ).fetchone()[0],
            0,
        )

    def test_held_run_preserves_session_only_feedback_graph(self):
        store = self.store()
        old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=old)
        store.finish_run(
            run_id,
            "completed",
            quote_session_id="quote-held-session-only",
            canonical_manifest={"artifacts": []},
            now=old,
        )
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic session-only held graph",
                "message": "Synthetic session-only held graph.",
                "validated_session_id": "quote-held-session-only",
            }
        )
        store.update_feedback_status(
            feedback["support_reference"], "resolved", resolution_note="Synthetic closure", now=old
        )
        store.set_legal_hold("sqag_generation_runs", "run_id", run_id, True, now=old)
        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc)
        )
        self.assertGreaterEqual(result.held, 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_feedback where feedback_id = ?",
                (feedback["feedback_id"],),
            ).fetchone()[0],
            1,
        )

    def test_custom_log_retention_root_requires_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unrelated = root / "unrelated.md"
            unrelated.write_text("synthetic documentation", encoding="utf-8")
            old_timestamp = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp()
            os.utime(unrelated, (old_timestamp, old_timestamp))
            base_args = [
                "enforce_log_retention.py", "--mode", "production", "--apply",
                "--now", "2026-01-01T00:00:00Z", "--log-root", str(root),
            ]
            with mock.patch.object(sys, "argv", base_args), mock.patch("builtins.print"):
                self.assertEqual(enforce_log_retention.main(), 2)
            self.assertTrue(unrelated.exists())
            confirmed_args = [*base_args, "--expected-log-root", str(root)]
            with mock.patch.object(sys, "argv", confirmed_args), mock.patch("builtins.print"):
                self.assertEqual(enforce_log_retention.main(), 0)
            self.assertFalse(unrelated.exists())

if __name__ == "__main__":
    unittest.main()
