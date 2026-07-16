import datetime as dt
import hashlib
import json
import os
import sqlite3
import contextlib
import unittest
from pathlib import Path
from unittest import mock

from webapp import server as webapp
from webapp.forensics import ForensicStore, trusted_workspace_id


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql"


class Pr140HardeningTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("pragma foreign_keys = on")
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.a = ForensicStore(self.connection, "tenant:acme", "pid-test-v1-" + "a" * 24)
        self.b = ForensicStore(self.connection, "org.example", "pid-test-v1-" + "b" * 24)

    def tearDown(self):
        self.connection.close()

    def test_workspace_validation_is_exact_and_fail_closed(self):
        for value in ("tenant:acme", "org.example", "org_example", "org-example"):
            self.assertEqual(trusted_workspace_id(value), value)
        for value in ("", " local", "../tenant", "local-workspace", "tenant\nother"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                trusted_workspace_id(value)
        self.assertEqual(trusted_workspace_id("local-workspace", allow_local=True), "local-workspace")

    def test_cross_workspace_run_feedback_hold_and_retention_do_not_disclose(self):
        run_id = self.a.record_run_started("generate", {"synthetic": True})
        feedback = self.a.submit_feedback({"category": "bug", "title": "Synthetic", "message": "Synthetic"})
        self.assertEqual(self.b.feedback_context(run_id=run_id)["link_type"], "none")
        with self.assertRaises(LookupError):
            self.b.get_feedback(feedback["feedback_id"])
        self.assertFalse(self.b.set_legal_hold("sqag_generation_runs", "run_id", run_id, True))
        self.assertEqual(self.b.enforce_retention(now=dt.datetime(2100, 1, 1, tzinfo=dt.timezone.utc)).deleted, 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0], 1)

    def test_feedback_parent_and_child_hold_preserve_complete_graph(self):
        report = self.a.submit_feedback({"category": "bug", "title": "Synthetic", "message": "Synthetic"})
        self.a.update_feedback_status(report["feedback_id"], "triaged")
        past = "2020-01-01T00:00:00Z"
        self.connection.execute("update sqag_feedback set retention_expires_at = ? where feedback_id = ?", (past, report["feedback_id"]))
        self.connection.execute("update sqag_feedback_status_history set retention_expires_at = ? where feedback_id = ?", (past, report["feedback_id"]))
        self.connection.commit()
        self.a.set_legal_hold("sqag_feedback", "feedback_id", report["feedback_id"], True, reason_code="legal_process", case_reference="CASE-123")
        result = self.a.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
        self.assertEqual(result.deleted, 0)
        self.assertGreater(self.connection.execute("select count(*) from sqag_feedback_status_history where feedback_id = ?", (report["feedback_id"],)).fetchone()[0], 0)

    def test_open_feedback_retains_linked_run_without_foreign_key_enforcement(self):
        self.connection.execute("pragma foreign_keys = off")
        past = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        run_id = self.a.record_run_started("generate", {"synthetic": True}, now=past)
        self.a.finish_run(run_id, "failed", now=past)
        self.a.submit_feedback({"category": "bug", "title": "Synthetic", "message": "Synthetic", "run_id": run_id})
        result = self.a.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
        self.assertEqual(result.held, 1)
        self.assertEqual(result.failed, 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0], 1)


    def test_hold_and_release_are_idempotent_and_keep_original_expiry(self):
        run_id = self.a.record_run_started("generate", {"synthetic": True})
        original = self.connection.execute("select original_retention_expires_at from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0]
        self.assertTrue(self.a.set_legal_hold("sqag_generation_runs", "run_id", run_id, True, case_reference="CASE-1"))
        self.assertTrue(self.a.set_legal_hold("sqag_generation_runs", "run_id", run_id, True, case_reference="CASE-1"))
        self.assertTrue(self.a.set_legal_hold("sqag_generation_runs", "run_id", run_id, False))
        self.assertTrue(self.a.set_legal_hold("sqag_generation_runs", "run_id", run_id, False))
        self.assertEqual(self.connection.execute("select original_retention_expires_at from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0], original)
        hold = dict(self.connection.execute("select * from sqag_legal_holds where target_id = ?", (run_id,)).fetchone())
        self.assertEqual(hold["case_reference"], "CASE-1")
        self.assertIsNotNone(hold["released_at"])

    def test_integrity_verification_detects_content_tampering(self):
        run_id = self.a.record_run_started("generate", {"approved_basis": [{"id": "one"}]})
        manifest = {"approved_basis": [{"id": "one"}], "output_rows": [{"description": "A"}], "artifacts": []}
        self.a.finish_run(run_id, "completed", canonical_manifest=manifest)
        result = self.a.verify_run_evidence(run_id, reason_code="support_investigation", privileged=True)
        self.assertTrue(result["integrity_ok"])
        self.connection.execute("drop trigger sqag_generation_evidence_no_update")
        self.connection.execute("update sqag_generation_evidence set evidence_json = '{}' where run_id = ? and evidence_type = 'generation_manifest'", (run_id,))
        self.connection.commit()
        tampered = self.a.verify_run_evidence(run_id, reason_code="support_investigation", privileged=True)
        self.assertFalse(tampered["integrity_ok"])

    def test_database_and_object_metadata_hashes_are_authoritative(self):
        content = b"synthetic-customer-ready-xlsx"
        digest = hashlib.sha256(content).hexdigest()
        files = webapp.quote_session_result_files({"exports": {"xlsx": {"exists": True, "url": "/api/quote-sessions/quote-test/download/xlsx", "size_bytes": len(content), "sha256": digest}}})
        self.assertEqual(files[0]["sha256"], digest)
        self.assertNotEqual(digest, hashlib.sha256(b"other").hexdigest())

    def test_privileged_artifact_verifier_rechecks_durable_bytes(self):
        content = b"synthetic-customer-ready-xlsx"
        digest = hashlib.sha256(content).hexdigest()

        class Storage:
            def quote_session_export_artifact(self, session_id, kind):
                self.last_request = (session_id, kind)
                return {"content": content, "sha256": digest, "size_bytes": len(content)}

        storage = Storage()
        verifier = webapp.forensic_artifact_verifier_for_session(storage, "quote-test")
        expected = {"name": "quotation.xlsx", "sha256": digest, "size_bytes": len(content)}
        self.assertTrue(verifier(expected))
        self.assertEqual(storage.last_request, ("quote-test", "xlsx"))
        self.assertFalse(verifier({**expected, "sha256": "0" * 64}))
    def test_deploy_tracking_requires_dedicated_key_and_version(self):
        with mock.patch.dict(os.environ, {"APP_MODE": "deploy", "SESSION_SECRET": "session-only"}, clear=True):
            with self.assertRaises(ValueError):
                webapp.privacy_safe_audit_tracking_id("user-123")
        env = {"APP_MODE": "deploy", "SQAG_TRACKING_HMAC_KEY": "dedicated-key", "SQAG_TRACKING_HMAC_KEY_VERSION": "2026-v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            first = webapp.privacy_safe_audit_tracking_id("user-123")
            second = webapp.privacy_safe_audit_tracking_id("user-123")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("pid-2026-v1-"))
        self.assertNotIn("user-123", first)

    def test_tracking_rotation_changes_pseudonym_and_preserves_version(self):
        values = []
        for version, key in (("v1", "key-one"), ("v2", "key-two")):
            with mock.patch.dict(os.environ, {"APP_MODE": "deploy", "SQAG_TRACKING_HMAC_KEY": key, "SQAG_TRACKING_HMAC_KEY_VERSION": version}, clear=True):
                values.append(webapp.privacy_safe_audit_tracking_id("user-123"))
        self.assertNotEqual(values[0], values[1])
        self.assertTrue(values[0].startswith("pid-v1-"))
        self.assertTrue(values[1].startswith("pid-v2-"))

    def test_support_access_is_privileged_and_separately_audited(self):
        run_id = self.a.record_run_started("generate", {"synthetic": True})
        self.a.finish_run(run_id, "completed", canonical_manifest={"artifacts": []})
        report = self.a.submit_feedback({"category": "bug", "title": "Synthetic", "message": "Synthetic", "run_id": run_id})
        detail = self.a.get_feedback(report["support_reference"], audit_access=True)
        self.assertEqual(detail["report"]["run_id"], run_id)
        self.a.verify_run_evidence(run_id, reason_code="support_investigation", privileged=True)
        events = [row[0] for row in self.connection.execute("select event_type from sqag_audit_events where workspace_id = ?", (self.a.workspace_id,))]
        self.assertIn("feedback_report_accessed", events)
        self.assertIn("forensic_evidence_accessed", events)
        with self.assertRaises(PermissionError):
            self.a.verify_run_evidence(run_id, reason_code="support_investigation", privileged=False)

    def test_retention_partial_failure_is_retryable_and_has_no_receipt(self):
        past = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        run_id = self.a.record_run_started("generate", {"synthetic": True}, now=past)
        self.a.finish_run(run_id, "failed", result_summary={"synthetic": True}, now=past)
        failed = self.a.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), artifact_delete=lambda _row, _finalize: False)
        self.assertEqual(failed.failed, 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_deletion_receipts where record_id = ?", (run_id,)).fetchone()[0], 0)
        def delete_on_retry(_row, finalize):
            finalize(self.connection, require_session_exclusive=True)
            return True

        retried = self.a.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            artifact_delete=delete_on_retry,
        )
        self.assertEqual(retried.parents_processed, 1)

    def test_retention_deletes_artifacts_before_parent_and_creates_receipt_after_success(self):
        past = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        run_id = self.a.record_run_started("generate", {"synthetic": True}, now=past)
        self.a.finish_run(run_id, "completed", quote_session_id="quote-retention", now=past)
        calls = []

        def delete_artifacts(row, finalize):
            calls.append((row["run_id"], row["quote_session_id"]))
            finalize(self.connection, require_session_exclusive=True)
            return True

        result = self.a.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            artifact_delete=delete_artifacts,
        )
        self.assertEqual(calls, [(run_id, "quote-retention")])
        self.assertEqual(result.parents_processed, 1)
        self.assertEqual(
            self.connection.execute("select count(*) from sqag_deletion_receipts where record_id = ?", (run_id,)).fetchone()[0],
            1,
        )

    def test_retention_admin_deletes_database_session_artifacts_idempotently(self):
        self.connection.executescript(
            """
            create table sqag_quote_sessions (
              workspace_id text not null, session_id text not null, primary key (workspace_id, session_id)
            );
            create table sqag_quote_artifacts (
              workspace_id text not null, session_id text not null, artifact_kind text not null
            );
            create table sqag_quote_publication_versions (
              workspace_id text not null, session_id text not null,
              run_id text not null, legal_hold integer not null default 0,
              primary key (workspace_id, run_id)
            );
            create table sqag_quote_publication_artifacts (
              workspace_id text not null, session_id text not null,
              run_id text not null
            );
            """
        )
        self.connection.execute(
            "insert into sqag_quote_sessions (workspace_id, session_id) values (?, ?)",
            ("tenant:acme", "quote-retention"),
        )
        self.connection.execute(
            "insert into sqag_quote_artifacts (workspace_id, session_id, artifact_kind) values (?, ?, ?)",
            ("tenant:acme", "quote-retention", "xlsx"),
        )
        self.connection.commit()
        storage = webapp.DatabaseSqagStorage("sqlite:///synthetic", "tenant:acme", role="admin", user_id="retention-worker")
        storage.connection = lambda: contextlib.nullcontext(self.connection)
        with mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "database"}, clear=True):
            self.assertTrue(storage.delete_quote_session_for_retention("quote-retention"))
            self.assertTrue(storage.delete_quote_session_for_retention("quote-retention"))
        self.assertEqual(self.connection.execute("select count(*) from sqag_quote_sessions").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_quote_artifacts").fetchone()[0], 0)

    def test_retention_receipt_failure_rolls_back_database_artifact_and_graph(self):
        past = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        now = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        run_id = self.a.record_run_started("generate", {"synthetic": True}, now=past)
        self.a.finish_run(
            run_id,
            "completed",
            quote_session_id="quote-atomic-retention",
            now=past,
        )
        self.connection.executescript(
            """
            create table sqag_quote_sessions (
              workspace_id text not null, session_id text not null, primary key (workspace_id, session_id)
            );
            create table sqag_quote_artifacts (
              workspace_id text not null, session_id text not null, artifact_kind text not null
            );
            """
        )
        self.connection.execute(
            "insert into sqag_quote_sessions (workspace_id, session_id) values (?, ?)",
            ("tenant:acme", "quote-atomic-retention"),
        )
        self.connection.execute(
            "insert into sqag_quote_artifacts (workspace_id, session_id, artifact_kind) values (?, ?, ?)",
            ("tenant:acme", "quote-atomic-retention", "xlsx"),
        )
        self.connection.commit()
        storage = webapp.DatabaseSqagStorage(
            "sqlite:///synthetic",
            "tenant:acme",
            role="admin",
            user_id="retention-worker",
        )
        storage.connection = lambda: contextlib.nullcontext(self.connection)

        def delete_artifacts(item, finalize):
            return storage.delete_quote_session_for_retention(
                item["quote_session_id"],
                finalize_graph=finalize,
            )

        with (
            mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "database"}, clear=True),
            mock.patch.object(self.a, "_receipt", side_effect=RuntimeError("synthetic receipt failure")),
        ):
            result = self.a.enforce_retention(now=now, artifact_delete=delete_artifacts)

        self.assertEqual(result.failed, 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0], 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_quote_sessions where session_id = 'quote-atomic-retention'").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_quote_artifacts where session_id = 'quote-atomic-retention'").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_deletion_receipts where record_id = ?", (run_id,)).fetchone()[0], 0)

    def test_manifest_round_trip_preserves_order_and_dependency_checksums(self):
        manifest = {
            "approved_basis": [{"id": "b1"}, {"id": "b2"}],
            "output_rows": [{"description": "one"}, {"description": "two"}],
            "profile": {"sha256": "a" * 64},
            "pricing_reference": {"sha256": "b" * 64},
            "layout_template": {"sha256": "c" * 64},
            "inputs": [{"sha256": "d" * 64}],
            "artifacts": [{"sha256": "e" * 64}],
        }
        run_id = self.a.record_run_started("generate", {"synthetic": True})
        self.a.finish_run(run_id, "completed", canonical_manifest=manifest)
        row = self.connection.execute("select evidence_json from sqag_generation_evidence where run_id = ? and evidence_type = 'generation_manifest'", (run_id,)).fetchone()
        restored = json.loads(row[0])
        self.assertEqual([item["id"] for item in restored["approved_basis"]], ["b1", "b2"])
        self.assertEqual([item["description"] for item in restored["output_rows"]], ["one", "two"])
        self.assertEqual(restored["profile"]["sha256"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
