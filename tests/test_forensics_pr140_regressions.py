import datetime as dt
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from webapp.forensics import ForensicStore, iso_timestamp
from webapp import server as webapp


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql"


class Pr140RegressionTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))

    def tearDown(self):
        self.connection.close()

    def store(self, workspace_id="workspace-a", actor="pid-v1-user-a"):
        return ForensicStore(self.connection, workspace_id, actor)

    def test_valid_platform_workspace_ids_remain_exact_and_distinct(self):
        values = ("tenant:acme", "org.example", "org_example", "org-example")
        self.assertEqual([self.store(value).workspace_id for value in values], list(values))

    def test_parent_hold_preserves_expired_run_children(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(run_id, "failed", result_summary={"synthetic": True})
        past = iso_timestamp(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
        for table in ("sqag_generation_runs", "sqag_generation_evidence", "sqag_audit_events"):
            self.connection.execute(f"update {table} set retention_expires_at = ?", (past,))
        self.connection.commit()
        store.set_legal_hold("sqag_generation_runs", "run_id", run_id, True)

        store.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))

        self.assertGreater(
            self.connection.execute(
                "select count(*) from sqag_generation_evidence where run_id = ?", (run_id,)
            ).fetchone()[0],
            0,
        )
        self.assertGreater(
            self.connection.execute(
                "select count(*) from sqag_audit_events where run_id = ?", (run_id,)
            ).fetchone()[0],
            0,
        )

    def test_existing_job_retry_does_not_begin_another_forensic_run(self):
        job_id = "job-existing123"
        existing = {
            "job_id": job_id,
            "type": "generate",
            "status": "completed",
            "created_at": "2026-07-15T00:00:00Z",
            "updated_at": "2026-07-15T00:00:01Z",
            "result": {"status": "completed"},
            "errors": [],
            "owner": {},
            "generation_run_id": "run-original123",
        }
        with webapp.JOBS_LOCK:
            previous = webapp.JOBS.get(job_id)
            webapp.JOBS[job_id] = existing
        try:
            with mock.patch.object(webapp, "begin_generation_forensics", return_value="run-orphan") as begin:
                result = webapp.create_job("generate", {}, requested_job_id=job_id)
            self.assertEqual(result["generation_run_id"], "run-original123")
            begin.assert_not_called()
        finally:
            with webapp.JOBS_LOCK:
                if previous is None:
                    webapp.JOBS.pop(job_id, None)
                else:
                    webapp.JOBS[job_id] = previous

    def test_hosted_quote_session_result_files_retain_sha256(self):
        digest = "a" * 64
        files = webapp.quote_session_result_files(
            {
                "exports": {
                    "xlsx": {
                        "filename": "quotation.xlsx",
                        "exists": True,
                        "url": "/api/quote-sessions/quote-123/download/xlsx",
                        "size_bytes": 42,
                        "sha256": digest,
                    }
                }
            }
        )
        self.assertEqual(files[0]["sha256"], digest)

    def test_request_evidence_preserves_basis_and_output_values_in_order(self):
        payload = {
            "quote_basis_sections": [
                {"id": "basis-1", "title": "Structure", "lines": [{"id": "line-1", "tag": "Include", "text": "Wall A"}]},
                {"id": "basis-2", "title": "Graphics", "lines": [{"id": "line-2", "tag": "Confirm", "text": "Logo B"}]},
            ],
            "line_items": [
                {"source_id": "line-1", "description": "Wall A", "quantity": 2, "unit": "sqm", "unit_price": 100},
                {"source_id": "line-2", "description": "Logo B", "quantity": 1, "unit": "no", "unit_price": 50},
            ],
        }
        evidence = webapp.forensic_request_summary(payload)
        self.assertEqual(evidence["approved_basis"][0]["id"], "basis-1")
        self.assertEqual(evidence["output_rows"][1]["description"], "Logo B")

    def test_terminal_states_do_not_collapse_to_failed(self):
        for status in ("needs_review", "degraded"):
            with self.subTest(status=status):
                store = self.store()
                run_id = store.record_run_started("generate", {"status": status})
                store.finish_run(run_id, status, result_summary={"status": status})
                actual = self.connection.execute(
                    "select status from sqag_generation_runs where run_id = ?", (run_id,)
                ).fetchone()[0]
                self.assertEqual(actual, status)

    def test_feedback_closure_sets_calendar_expiry_from_first_closure(self):
        store = self.store()
        feedback = store.submit_feedback(
            {"category": "bug", "title": "Synthetic", "message": "Synthetic", "impact": "low"}
        )
        closed_at = dt.datetime(2024, 2, 29, tzinfo=dt.timezone.utc)
        store.update_feedback_status(
            feedback["feedback_id"], "resolved", resolution_note="Synthetic resolution", now=closed_at
        )
        row = self.connection.execute(
            "select closed_at, retention_expires_at from sqag_feedback where feedback_id = ?",
            (feedback["feedback_id"],),
        ).fetchone()
        self.assertEqual(row["closed_at"], "2024-02-29T00:00:00Z")
        self.assertEqual(row["retention_expires_at"], "2027-02-28T00:00:00Z")

    def test_evidence_and_audit_rows_reject_ordinary_mutation(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        evidence_id = self.connection.execute(
            "select evidence_id from sqag_generation_evidence where run_id = ?", (run_id,)
        ).fetchone()[0]
        event_id = self.connection.execute(
            "select event_id from sqag_audit_events where run_id = ?", (run_id,)
        ).fetchone()[0]
        with self.assertRaises(sqlite3.DatabaseError):
            self.connection.execute(
                "update sqag_generation_evidence set evidence_json = '{}' where evidence_id = ?", (evidence_id,)
            )
        self.connection.rollback()
        with self.assertRaises(sqlite3.DatabaseError):
            self.connection.execute(
                "update sqag_audit_events set event_json = '{}' where event_id = ?", (event_id,)
            )

    def test_retention_worker_is_bounded(self):
        store = self.store()
        past = iso_timestamp(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
        for index in range(3):
            run_id = store.record_run_started("generate", {"index": index})
            store.finish_run(run_id, "failed", result_summary={"index": index})
        for table in ("sqag_generation_runs", "sqag_generation_evidence", "sqag_audit_events"):
            self.connection.execute(f"update {table} set retention_expires_at = ?", (past,))
        self.connection.commit()

        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), batch_size=1
        )

        self.assertEqual(result.parents_processed, 1)
        self.assertGreater(
            self.connection.execute("select count(*) from sqag_generation_runs").fetchone()[0], 0
        )


if __name__ == "__main__":
    unittest.main()
