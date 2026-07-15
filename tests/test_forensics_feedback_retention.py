import datetime as dt
import json
import sqlite3
import unittest
from pathlib import Path

from webapp.forensics import ForensicStore, add_calendar_years, iso_timestamp


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql"


class ForensicsFeedbackRetentionTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.store = ForensicStore(self.connection, "workspace-a", "pid-v1-user-a")

    def tearDown(self):
        self.connection.close()

    def test_calendar_year_retention_handles_leap_day(self):
        start = dt.datetime(2024, 2, 29, tzinfo=dt.timezone.utc)
        self.assertEqual(add_calendar_years(start), dt.datetime(2027, 2, 28, tzinfo=dt.timezone.utc))

    def test_run_records_hashed_evidence_and_terminal_audit(self):
        run_id = self.store.record_run_started("generate", {"image_count": 2})
        self.store.finish_run(run_id, "blocked", error_category="images_missing", result_summary={"artifact_count": 0})

        run = dict(self.connection.execute("select * from sqag_generation_runs where run_id = ?", (run_id,)).fetchone())
        evidence = [dict(row) for row in self.connection.execute("select * from sqag_generation_evidence where run_id = ?", (run_id,))]
        events = [row[0] for row in self.connection.execute("select event_type from sqag_audit_events where run_id = ?", (run_id,))]

        self.assertEqual(run["status"], "blocked")
        self.assertEqual(len(evidence), 2)
        self.assertTrue(all(len(row["evidence_sha256"]) == 64 for row in evidence))
        self.assertGreaterEqual(set(events), {"generation_received", "generation_blocked"})

    def test_unknown_run_cannot_receive_a_fabricated_terminal_state(self):
        with self.assertRaisesRegex(ValueError, "could not be finalized"):
            self.store.finish_run("run-does-not-exist", "completed")
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_evidence").fetchone()[0], 0)

    def test_feedback_returns_support_reference_and_does_not_copy_evidence(self):
        run_id = self.store.record_run_started("generate", {"image_count": 1})
        original_expiry = self.connection.execute(
            "select retention_expires_at from sqag_generation_runs where run_id = ?", (run_id,)
        ).fetchone()[0]

        result = self.store.submit_feedback(
            {
                "category": "incorrect_output",
                "title": "Synthetic mismatch",
                "message": "Synthetic description that must stay out of audit metadata",
                "impact": "high",
                "run_id": run_id,
                "validated_session_id": "quote-synthetic",
                "include_link": True,
                "diagnostic_metadata": {"current_route": "/", "browser_family_major": "Chromium 123"},
            }
        )
        row = dict(self.connection.execute("select * from sqag_feedback where feedback_id = ?", (result["feedback_id"],)).fetchone())
        audit = "\n".join(
            item[0] for item in self.connection.execute("select event_json from sqag_audit_events where event_type = 'feedback_submitted'")
        )

        self.assertRegex(result["support_reference"], r"^SQAG-FB-[A-F0-9]{10}$")
        self.assertEqual(row["run_id"], run_id)
        self.assertEqual(row["session_id"], "quote-synthetic")
        self.assertNotIn(row["message"], audit)
        self.assertEqual(json.loads(row["diagnostic_metadata_json"])["current_route"], "/")
        self.assertEqual(
            self.connection.execute("select retention_expires_at from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0],
            original_expiry,
        )

    def test_manual_reference_never_resolves_another_actor_run(self):
        other = ForensicStore(self.connection, "workspace-a", "pid-v1-user-b")
        other_run = other.record_run_started("generate", {"image_count": 1})
        result = self.store.submit_feedback(
            {
                "category": "bug",
                "title": "Reference check",
                "message": "Synthetic reference check",
                "manual_reference": other_run,
                "include_link": False,
            }
        )
        row = dict(self.connection.execute("select * from sqag_feedback where feedback_id = ?", (result["feedback_id"],)).fetchone())
        self.assertEqual(row["manual_reference_text"], other_run)
        self.assertEqual(row["manual_reference_status"], "unresolved")
        self.assertIsNone(row["run_id"])
        self.assertIsNone(row["resolved_reference_id"])

    def test_context_falls_back_only_to_the_same_actor_latest_run(self):
        other = ForensicStore(self.connection, "workspace-a", "pid-v1-user-b")
        other.record_run_started("generate", {"image_count": 1})
        own_run = self.store.record_run_started("generate", {"image_count": 2})
        context = self.store.feedback_context()
        self.assertEqual(context["run_id"], own_run)
        self.assertEqual(context["source"], "recent_generation")

    def test_legal_hold_is_audited_without_resetting_original_expiry(self):
        run_id = self.store.record_run_started("generate", {"image_count": 1})
        original = self.connection.execute(
            "select original_retention_expires_at from sqag_generation_runs where run_id = ?", (run_id,)
        ).fetchone()[0]
        self.assertTrue(self.store.set_legal_hold("sqag_generation_runs", "run_id", run_id, True))
        after = self.connection.execute(
            "select original_retention_expires_at from sqag_generation_runs where run_id = ?", (run_id,)
        ).fetchone()[0]
        events = self.connection.execute("select count(*) from sqag_audit_events where event_type = 'legal_hold_changed'").fetchone()[0]
        self.assertEqual(after, original)
        self.assertEqual(events, 1)

    def test_parent_run_is_not_deleted_while_a_child_is_retained(self):
        run_id = self.store.record_run_started("generate", {"image_count": 1})
        past = iso_timestamp(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
        now = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        self.connection.execute("update sqag_generation_runs set retention_expires_at = ? where run_id = ?", (past, run_id))
        self.connection.commit()

        first = self.store.enforce_retention(now=now)
        self.assertGreaterEqual(first.held, 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0], 1)

        self.connection.execute("update sqag_generation_evidence set retention_expires_at = ? where run_id = ?", (past, run_id))
        self.connection.execute("update sqag_audit_events set retention_expires_at = ? where run_id = ?", (past, run_id))
        self.connection.commit()
        second = self.store.enforce_retention(now=now)
        self.assertGreaterEqual(second.deleted, 3)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
