import contextlib
import datetime as dt
import hashlib
import inspect
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from webapp.forensics import ForensicStore, RetentionGraphHeld, iso_timestamp
from webapp import server as webapp
from scripts import enforce_forensic_retention


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

    def test_expired_feedback_linked_to_held_run_preserves_report_and_history(self):
        store = self.store()
        opened = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        closed = dt.datetime(2020, 2, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=opened)
        store.finish_run(run_id, "failed", canonical_manifest={"artifacts": []}, now=opened)
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic held-run feedback",
                "message": "Synthetic held-run feedback.",
                "run_id": run_id,
            }
        )
        store.update_feedback_status(
            feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=closed,
        )
        store.set_legal_hold("sqag_generation_runs", "run_id", run_id, True)

        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        )

        self.assertGreaterEqual(result.held, 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_feedback where feedback_id = ?",
                (feedback["feedback_id"],),
            ).fetchone()[0],
            1,
            "feedback linked to a legally held run was deleted",
        )
        self.assertGreater(
            self.connection.execute(
                "select count(*) from sqag_feedback_status_history where feedback_id = ?",
                (feedback["feedback_id"],),
            ).fetchone()[0],
            0,
            "status history linked to held-run feedback was deleted",
        )

    def test_batch_size_one_reaches_feedback_behind_dependency_blocked_run(self):
        store = self.store()
        run_time = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        close_time = dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc)
        now = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=run_time)
        store.finish_run(run_id, "failed", canonical_manifest={"artifacts": []}, now=run_time)
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic progress feedback",
                "message": "Synthetic progress feedback.",
                "run_id": run_id,
            }
        )
        store.update_feedback_status(
            feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=close_time,
        )

        first = store.enforce_retention(now=now, batch_size=1)
        second = store.enforce_retention(now=now, batch_size=1)

        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_feedback where feedback_id = ?",
                (feedback["feedback_id"],),
            ).fetchone()[0],
            0,
            f"bounded passes made no progress: first={first}, second={second}",
        )
    def test_retention_and_legal_hold_are_serialized_without_orphaned_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "forensics.sqlite3"
            retention_connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
            retention_connection.row_factory = sqlite3.Row
            retention_connection.executescript(MIGRATION.read_text(encoding="utf-8"))
            hold_connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
            hold_connection.row_factory = sqlite3.Row
            try:
                retention_store = ForensicStore(
                    retention_connection,
                    "workspace-race",
                    "pid-v1-retention",
                )
                hold_store = ForensicStore(
                    hold_connection,
                    "workspace-race",
                    "pid-v1-counsel",
                )
                run_id = retention_store.record_run_started(
                    "generate",
                    {"synthetic": True},
                    now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
                )
                retention_store.finish_run(
                    run_id,
                    "failed",
                    now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
                )
                past = iso_timestamp(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
                for table in (
                    "sqag_generation_runs",
                    "sqag_generation_evidence",
                    "sqag_audit_events",
                ):
                    retention_connection.execute(
                        f"update {table} set retention_expires_at = ?",
                        (past,),
                    )
                retention_connection.commit()
                window_open = threading.Event()
                hold_attempted = threading.Event()
                hold_result = []
                original_check = retention_store._run_graph_held
                checks = 0

                def checked(run_id_value):
                    nonlocal checks
                    checks += 1
                    result = original_check(run_id_value)
                    if checks == 2:
                        window_open.set()
                        self.assertTrue(hold_attempted.wait(2))
                    return result

                def apply_hold():
                    self.assertTrue(window_open.wait(2))
                    hold_attempted.set()
                    hold_result.append(
                        hold_store.set_legal_hold(
                            "sqag_generation_runs",
                            "run_id",
                            run_id,
                            True,
                        )
                    )

                retention_store._run_graph_held = checked
                thread = threading.Thread(target=apply_hold)
                thread.start()
                result = retention_store.enforce_retention(
                    now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
                )
                thread.join(5)

                self.assertFalse(thread.is_alive())
                self.assertEqual(result.parents_processed, 1)
                self.assertEqual(hold_result, [False])
                self.assertEqual(retention_connection.execute("select count(*) from sqag_legal_holds").fetchone()[0], 0)
            finally:
                hold_connection.close()
                retention_connection.close()

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

    def test_generation_run_cannot_be_claimed_or_finished_by_another_actor(self):
        owner = self.store(actor="pid-v1-owner")
        attacker = self.store(actor="pid-v1-attacker")
        run_id = owner.record_run_started(
            "generate",
            {"synthetic": True},
            job_id="job-ownedrun123",
        )

        self.assertFalse(
            attacker.set_run_state(run_id, "running", expected_status="received")
        )
        with self.assertRaises(ValueError):
            attacker.finish_run(
                run_id,
                "completed",
                result_summary={"synthetic": True},
            )

        row = self.connection.execute(
            "select status, completed_at from sqag_generation_runs where run_id = ?",
            (run_id,),
        ).fetchone()
        self.assertEqual(row["status"], "received")
        self.assertIsNone(row["completed_at"])

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

    def test_reopen_then_reclose_sets_a_fresh_closure_and_expiry(self):
        store = self.store()
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic reopen lifecycle",
                "message": "Synthetic reopen lifecycle.",
            }
        )
        first_close = dt.datetime(2020, 2, 29, tzinfo=dt.timezone.utc)
        reopen = dt.datetime(2023, 3, 1, tzinfo=dt.timezone.utc)
        second_close = dt.datetime(2024, 2, 29, tzinfo=dt.timezone.utc)
        store.update_feedback_status(
            feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic first closure.",
            now=first_close,
        )
        reopened = store.update_feedback_status(
            feedback["feedback_id"],
            "in_progress",
            resolution_note="Synthetic reopen reason.",
            now=reopen,
        )
        self.assertIsNone(
            reopened["closed_at"],
            "reopening left the prior closure active on the current report",
        )
        reclosed = store.update_feedback_status(
            feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic second closure.",
            now=second_close,
        )

        self.assertEqual(reclosed["closed_at"], "2024-02-29T00:00:00Z")
        self.assertEqual(reclosed["retention_expires_at"], "2027-02-28T00:00:00Z")
        closure_history = self.connection.execute(
            "select created_at from sqag_feedback_status_history "
            "where feedback_id = ? and to_status = 'resolved' order by created_at",
            (feedback["feedback_id"],),
        ).fetchall()
        self.assertEqual(
            [row["created_at"] for row in closure_history],
            ["2020-02-29T00:00:00Z", "2024-02-29T00:00:00Z"],
        )

    def test_feedback_transition_lock_precedes_authoritative_status_read(self):
        store = self.store()
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic transition serialization",
                "message": "Synthetic transition serialization.",
            }
        )
        events = []
        acquire_locks = store._acquire_transaction_locks
        read_feedback = store._feedback

        def record_lock(*identities):
            events.append(("lock", identities))
            return acquire_locks(*identities)

        def record_read(reference):
            events.append(("read", reference))
            return read_feedback(reference)

        with mock.patch.object(store, "_acquire_transaction_locks", side_effect=record_lock), mock.patch.object(
            store, "_feedback", side_effect=record_read
        ):
            updated = store.update_feedback_status(
                feedback["support_reference"],
                "triaged",
                now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            )

        self.assertEqual(updated["status"], "triaged")
        self.assertEqual(events[0], ("lock", (("feedback", feedback["feedback_id"]),)))
        self.assertEqual(events[1], ("read", feedback["feedback_id"]))

    def test_retention_rechecks_reopened_feedback_after_transaction_lock(self):
        store = self.store()
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic deletion race",
                "message": "Synthetic deletion race.",
            }
        )
        store.update_feedback_status(
            feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        stale = dict(
            self.connection.execute(
                "select * from sqag_feedback where feedback_id = ?",
                (feedback["feedback_id"],),
            ).fetchone()
        )
        self.connection.execute(
            "update sqag_feedback set status = 'in_progress', closed_at = null, "
            "retention_expires_at = ? where feedback_id = ?",
            ("2030-01-01T00:00:00Z", feedback["feedback_id"]),
        )
        self.connection.commit()

        with self.assertRaises(RetentionGraphHeld):
            store._delete_retention_graph(
                "feedback",
                stale,
                feedback["feedback_id"],
                "2025-01-01T00:00:00Z",
            )
        self.connection.rollback()

        current = self.connection.execute(
            "select status, retention_expires_at from sqag_feedback where feedback_id = ?",
            (feedback["feedback_id"],),
        ).fetchone()
        self.assertEqual(
            dict(current),
            {
                "status": "in_progress",
                "retention_expires_at": "2030-01-01T00:00:00Z",
            },
        )

    def test_artifact_free_support_evidence_does_not_open_artifact_storage(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(run_id, "blocked", canonical_manifest={"artifacts": []})
        feedback = store.submit_feedback(
            {
                "category": "failed_process",
                "title": "Synthetic artifact-free run",
                "message": "Synthetic artifact-free run.",
                "run_id": run_id,
            }
        )
        with (
            mock.patch.object(webapp, "require_support_forensics"),
            mock.patch.object(
                webapp,
                "forensic_store_for_auth_session",
                return_value=contextlib.nullcontext(store),
            ),
            mock.patch.object(
                webapp,
                "quote_session_storage_for_auth_session",
                side_effect=AssertionError(
                    "artifact-free verification opened artifact storage"
                ),
            ) as open_artifact_storage,
        ):
            result = webapp.support_feedback_evidence_for_auth_session(
                feedback["support_reference"],
                "support_investigation",
                {"synthetic": True},
            )

        self.assertTrue(result["integrity_ok"])
        open_artifact_storage.assert_not_called()
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events "
                "where run_id = ? and event_type = 'forensic_evidence_accessed'",
                (run_id,),
            ).fetchone()[0],
            1,
        )
    def test_run_hold_release_and_independent_feedback_hold_are_separate(self):
        store = self.store()
        opened = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        closed = dt.datetime(2020, 2, 1, tzinfo=dt.timezone.utc)
        now = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=opened)
        store.finish_run(run_id, "failed", canonical_manifest={"artifacts": []}, now=opened)
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic independent hold",
                "message": "Synthetic independent hold.",
                "run_id": run_id,
            }
        )
        store.update_feedback_status(
            feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=closed,
        )
        feedback_dates = self.connection.execute(
            "select closed_at, original_retention_expires_at, "
            "submission_retention_expires_at, retention_expires_at "
            "from sqag_feedback where feedback_id = ?",
            (feedback["feedback_id"],),
        ).fetchone()
        run_expiry = self.connection.execute(
            "select retention_expires_at from sqag_generation_runs where run_id = ?",
            (run_id,),
        ).fetchone()[0]
        store.set_legal_hold("sqag_generation_runs", "run_id", run_id, True)
        store.set_legal_hold("sqag_feedback", "feedback_id", feedback["feedback_id"], True)
        self.assertGreaterEqual(store.enforce_retention(now=now).held, 1)

        store.set_legal_hold("sqag_generation_runs", "run_id", run_id, False)
        self.assertGreaterEqual(store.enforce_retention(now=now).held, 1)
        retained_dates = self.connection.execute(
            "select closed_at, original_retention_expires_at, "
            "submission_retention_expires_at, retention_expires_at "
            "from sqag_feedback where feedback_id = ?",
            (feedback["feedback_id"],),
        ).fetchone()
        self.assertEqual(tuple(retained_dates), tuple(feedback_dates))
        self.assertEqual(
            self.connection.execute(
                "select retention_expires_at from sqag_generation_runs where run_id = ?",
                (run_id,),
            ).fetchone()[0],
            run_expiry,
        )

        store.set_legal_hold("sqag_feedback", "feedback_id", feedback["feedback_id"], False)
        store.enforce_retention(now=now)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_feedback where feedback_id = ?",
                (feedback["feedback_id"],),
            ).fetchone()[0],
            0,
        )

    def test_linked_run_hold_added_after_selection_blocks_feedback_delete(self):
        store = self.store()
        opened = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=opened)
        store.finish_run(run_id, "failed", canonical_manifest={"artifacts": []}, now=opened)
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic late hold",
                "message": "Synthetic late hold.",
                "run_id": run_id,
            }
        )
        store.update_feedback_status(
            feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=opened,
        )
        original_delete = store._delete_retention_graph
        inserted_hold = False

        def add_hold_then_delete(kind, item, record_id, now_text, **kwargs):
            nonlocal inserted_hold
            if kind == "feedback" and not inserted_hold:
                inserted_hold = True
                store.set_legal_hold("sqag_generation_runs", "run_id", run_id, True)
            return original_delete(kind, item, record_id, now_text, **kwargs)

        store._delete_retention_graph = add_hold_then_delete
        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        )

        self.assertTrue(inserted_hold)
        self.assertGreaterEqual(result.held, 1)
        self.assertEqual(result.parents_processed, 0)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_feedback where feedback_id = ?",
                (feedback["feedback_id"],),
            ).fetchone()[0],
            1,
        )

    def test_retention_metrics_distinguish_review_held_and_actionable_parents(self):
        store = self.store()
        past = "2020-01-01T00:00:00Z"
        open_feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic review required",
                "message": "Synthetic review required.",
            }
        )
        closed_feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic eligible feedback",
                "message": "Synthetic eligible feedback.",
            }
        )
        store.update_feedback_status(
            closed_feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        held_run = store.record_run_started(
            "generate",
            {"synthetic": True},
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        store.finish_run(
            held_run,
            "failed",
            canonical_manifest={"artifacts": []},
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        store.set_legal_hold("sqag_generation_runs", "run_id", held_run, True)
        self.connection.execute(
            "update sqag_feedback set retention_expires_at = ? where feedback_id = ?",
            (past, open_feedback["feedback_id"]),
        )
        self.connection.commit()

        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            batch_size=2,
        )

        self.assertGreaterEqual(result.examined, 3)
        self.assertEqual(result.review_required, 1)
        self.assertEqual(result.held, 1)
        self.assertEqual(result.parents_processed, 1)
        self.assertEqual(result.failed, 0)
        self.assertFalse(result.scan_exhausted)

    def test_failed_retention_candidate_rotates_behind_unexamined_work(self):
        store = self.store()
        first_time = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        second_time = dt.datetime(2020, 1, 2, tzinfo=dt.timezone.utc)
        now = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        first_run = store.record_run_started("generate", {"index": 1}, now=first_time)
        store.finish_run(
            first_run,
            "failed",
            quote_session_id="quote-failure-rotation-one",
            canonical_manifest={"artifacts": []},
            now=first_time,
        )
        second_run = store.record_run_started("generate", {"index": 2}, now=second_time)
        store.finish_run(
            second_run,
            "failed",
            quote_session_id="quote-failure-rotation-two",
            canonical_manifest={"artifacts": []},
            now=second_time,
        )

        def delete_artifacts(item, finalize):
            if item["run_id"] == first_run:
                raise RuntimeError("synthetic temporary deletion failure")
            finalize(self.connection, require_session_exclusive=True)
            return True

        first = store.enforce_retention(
            now=now,
            batch_size=1,
            artifact_delete=delete_artifacts,
        )
        second = store.enforce_retention(
            now=now,
            batch_size=1,
            artifact_delete=delete_artifacts,
        )

        self.assertEqual(first.parents_processed, 1)
        self.assertEqual(first.failed, 1)
        self.assertEqual(second.parents_processed, 1)
        self.assertEqual(second.failed, 0)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_generation_runs where run_id = ?",
                (first_run,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_generation_runs where run_id = ?",
                (second_run,),
            ).fetchone()[0],
            0,
        )

    def test_bounded_scan_cursor_reaches_work_beyond_review_required_rows(self):
        store = self.store()
        past = "2020-01-01T00:00:00Z"
        for index in range(17):
            feedback = store.submit_feedback(
                {
                    "category": "bug",
                    "title": f"Synthetic open {index}",
                    "message": "Synthetic open feedback.",
                }
            )
            self.connection.execute(
                "update sqag_feedback set retention_expires_at = ? where feedback_id = ?",
                (past, feedback["feedback_id"]),
            )
        eligible = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic eligible after scan cap",
                "message": "Synthetic eligible after scan cap.",
            }
        )
        store.update_feedback_status(
            eligible["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        self.connection.execute(
            "update sqag_feedback set deletion_claimed_at = ? where feedback_id = ?",
            ("2019-01-01T00:00:00Z", eligible["feedback_id"]),
        )
        self.connection.commit()

        first = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            batch_size=1,
        )
        second = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            batch_size=1,
        )

        self.assertEqual(first.examined, first.scan_limit)
        self.assertTrue(first.scan_exhausted)
        self.assertEqual(first.parents_processed, 0)
        self.assertEqual(second.parents_processed, 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_feedback where feedback_id = ?",
                (eligible["feedback_id"],),
            ).fetchone()[0],
            0,
        )

    def test_retention_dry_run_classifies_without_mutating_cursor_or_rows(self):
        store = self.store()
        past = "2020-01-01T00:00:00Z"
        open_feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic dry-run review",
                "message": "Synthetic dry-run review.",
            }
        )
        closed_feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic dry-run eligible",
                "message": "Synthetic dry-run eligible.",
            }
        )
        store.update_feedback_status(
            closed_feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        self.connection.execute(
            "update sqag_feedback set retention_expires_at = ? where feedback_id = ?",
            (past, open_feedback["feedback_id"]),
        )
        self.connection.commit()

        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            batch_size=1,
            apply=False,
        )

        self.assertEqual(result.review_required, 1)
        self.assertEqual(result.parents_processed, 1)
        self.assertEqual(result.deleted, 0)
        rows = self.connection.execute(
            "select deletion_state, deletion_claimed_at from sqag_feedback "
            "where feedback_id in (?, ?) order by feedback_id",
            (open_feedback["feedback_id"], closed_feedback["feedback_id"]),
        ).fetchall()
        self.assertEqual(
            [(row["deletion_state"], row["deletion_claimed_at"]) for row in rows],
            [("active", None), ("active", None)],
        )

    def test_artifact_bearing_support_evidence_requires_storage_and_audits_outage(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(
            run_id,
            "failed",
            quote_session_id="quote-artifact-bearing",
            canonical_manifest={
                "artifacts": [
                    {
                        "name": "quotation.xlsx",
                        "sha256": "a" * 64,
                        "size_bytes": 1,
                    }
                ]
            },
        )
        feedback = store.submit_feedback(
            {
                "category": "failed_process",
                "title": "Synthetic artifact-bearing run",
                "message": "Synthetic artifact-bearing run.",
                "run_id": run_id,
            }
        )
        outage = webapp.SqagStorageAccessError(
            "SQAG artifact storage is unavailable.",
            status=503,
            reason="storage_artifact_database_not_migrated",
        )
        with (
            mock.patch.object(webapp, "require_support_forensics"),
            mock.patch.object(
                webapp,
                "forensic_store_for_auth_session",
                return_value=contextlib.nullcontext(store),
            ),
            mock.patch.object(
                webapp,
                "support_forensic_artifact_reader_for_auth_session",
                side_effect=outage,
            ) as open_artifact_storage,
        ):
            with self.assertRaises(webapp.SqagStorageAccessError):
                webapp.support_feedback_evidence_for_auth_session(
                    feedback["support_reference"],
                    "support_investigation",
                    {"synthetic": True},
                )

        open_artifact_storage.assert_called_once()
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events where run_id = ? "
                "and event_type = 'forensic_evidence_verification_failed'",
                (run_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events where run_id = ? "
                "and event_type = 'forensic_evidence_accessed'",
                (run_id,),
            ).fetchone()[0],
            0,
        )

    def test_failed_artifact_free_evidence_skips_verifier_factory(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(run_id, "failed", canonical_manifest={"artifacts": []})
        factory = mock.Mock(
            side_effect=AssertionError(
                "artifact-free direct verification created an artifact verifier"
            )
        )

        result = store.verify_run_evidence(
            run_id,
            reason_code="support_investigation",
            privileged=True,
            artifact_verifier_factory=factory,
        )

        self.assertTrue(result["integrity_ok"])
        factory.assert_not_called()

    def test_artifact_bearing_evidence_without_session_linkage_fails_closed(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(
            run_id,
            "failed",
            canonical_manifest={
                "artifacts": [
                    {
                        "name": "quotation.xlsx",
                        "sha256": "a" * 64,
                        "size_bytes": 1,
                    }
                ]
            },
        )
        feedback = store.submit_feedback(
            {
                "category": "failed_process",
                "title": "Synthetic missing session",
                "message": "Synthetic missing session.",
                "run_id": run_id,
            }
        )
        with (
            mock.patch.object(webapp, "require_support_forensics"),
            mock.patch.object(
                webapp,
                "forensic_store_for_auth_session",
                return_value=contextlib.nullcontext(store),
            ),
            mock.patch.object(
                webapp,
                "quote_session_storage_for_auth_session",
            ) as open_artifact_storage,
        ):
            with self.assertRaises(LookupError):
                webapp.support_feedback_evidence_for_auth_session(
                    feedback["support_reference"],
                    "support_investigation",
                    {"synthetic": True},
                )

        open_artifact_storage.assert_not_called()
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events where run_id = ? "
                "and event_type = 'forensic_evidence_verification_failed'",
                (run_id,),
            ).fetchone()[0],
            1,
        )
    def test_repeated_reopens_are_audited_without_extending_linked_run(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(run_id, "failed", canonical_manifest={"artifacts": []})
        run_expiry = self.connection.execute(
            "select retention_expires_at from sqag_generation_runs where run_id = ?",
            (run_id,),
        ).fetchone()[0]
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic repeated reopen",
                "message": "Synthetic repeated reopen.",
                "run_id": run_id,
            }
        )
        for cycle in range(3):
            store.update_feedback_status(
                feedback["feedback_id"],
                "resolved",
                resolution_note=f"Synthetic closure {cycle}.",
                now=dt.datetime(2020 + cycle * 2, 1, 1, tzinfo=dt.timezone.utc),
            )
            store.update_feedback_status(
                feedback["feedback_id"],
                "in_progress",
                resolution_note=f"Synthetic reopen {cycle}.",
                now=dt.datetime(2021 + cycle * 2, 1, 1, tzinfo=dt.timezone.utc),
            )

        row = self.connection.execute(
            "select retention_policy_version, closed_at from sqag_feedback "
            "where feedback_id = ?",
            (feedback["feedback_id"],),
        ).fetchone()
        latest = self.connection.execute(
            "select event_json from sqag_audit_events where run_id = ? "
            "and event_type = 'feedback_status_changed' order by created_at desc, event_id desc limit 1",
            (run_id,),
        ).fetchone()
        details = json.loads(latest["event_json"])
        self.assertEqual(row["retention_policy_version"], "sqag.feedback-retention.v3")
        self.assertIsNone(row["closed_at"])
        self.assertEqual(details["reopen_count"], 3)
        self.assertTrue(details["unusual_reopen_activity"])
        self.assertEqual(
            self.connection.execute(
                "select retention_expires_at from sqag_generation_runs where run_id = ?",
                (run_id,),
            ).fetchone()[0],
            run_expiry,
        )
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

    def test_sqlite_delete_authorization_is_single_use_and_rollback_safe(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(run_id, "failed", result_summary={"synthetic": True})
        evidence_id = self.connection.execute(
            "select evidence_id from sqag_generation_evidence where run_id = ? order by evidence_id limit 1",
            (run_id,),
        ).fetchone()[0]
        audit_id = self.connection.execute(
            "select event_id from sqag_audit_events where run_id = ? order by event_id limit 1",
            (run_id,),
        ).fetchone()[0]

        with self.assertRaises(sqlite3.DatabaseError):
            self.connection.execute(
                "delete from sqag_generation_evidence where workspace_id = ? and evidence_id = ?",
                (store.workspace_id, evidence_id),
            )
        self.connection.rollback()
        with self.assertRaises(sqlite3.DatabaseError):
            self.connection.execute(
                "delete from sqag_audit_events where workspace_id = ? and event_id = ?",
                (store.workspace_id, audit_id),
            )
        self.connection.rollback()

        self.connection.execute(
            "insert into sqag_retention_delete_authorizations (authorization_id, workspace_id, record_type, record_id, created_at) values (?, ?, ?, ?, ?)",
            ("retention-auth-test", store.workspace_id, "sqag_generation_evidence", evidence_id, "2026-07-15T00:00:00Z"),
        )
        self.connection.commit()
        self.connection.execute(
            "delete from sqag_generation_evidence where workspace_id = ? and evidence_id = ?",
            (store.workspace_id, evidence_id),
        )
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_retention_delete_authorizations where authorization_id = ?",
                ("retention-auth-test",),
            ).fetchone()[0],
            0,
        )
        self.connection.rollback()
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_generation_evidence where evidence_id = ?",
                (evidence_id,),
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_retention_delete_authorizations where authorization_id = ?",
                ("retention-auth-test",),
            ).fetchone()[0],
            1,
        )

    def test_feedback_linked_only_by_session_preserves_quote_session_artifacts(self):
        deleting_run = self.store().record_run_started("generate", {"synthetic": True})
        self.store().submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic session-only link",
                "message": "Synthetic retained feedback.",
                "validated_session_id": "quote-session-only123",
            }
        )
        self.assertTrue(
            enforce_forensic_retention.session_has_retained_forensic_links(
                self.connection,
                "workspace-a",
                "quote-session-only123",
                deleting_run,
            )
        )
        self.assertFalse(
            enforce_forensic_retention.session_has_retained_forensic_links(
                self.connection,
                "workspace-b",
                "quote-session-only123",
                deleting_run,
            )
        )

    def test_session_only_feedback_retains_run_until_later_graph_cleanup(self):
        store = self.store()
        run_time = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        feedback_time = dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc)
        first_pass = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)
        final_pass = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=run_time)
        store.finish_run(
            run_id,
            "completed",
            quote_session_id="quote-session-lifecycle123",
            now=run_time,
        )
        feedback = store.submit_feedback(
            {
                "category": "bug",
                "title": "Synthetic lifecycle",
                "message": "Synthetic session-only lifecycle.",
                "validated_session_id": "quote-session-lifecycle123",
            }
        )
        store.update_feedback_status(
            feedback["feedback_id"],
            "resolved",
            resolution_note="Synthetic closure.",
            now=feedback_time,
        )
        artifact_visible = True
        calls = []

        def delete_artifacts(item, finalize):
            nonlocal artifact_visible
            calls.append(item["run_id"])
            finalize(self.connection, require_session_exclusive=True)
            artifact_visible = False
            return True

        first = store.enforce_retention(
            now=first_pass,
            artifact_delete=delete_artifacts,
        )
        self.assertEqual(first.held, 1)
        self.assertTrue(artifact_visible)
        self.assertEqual(calls, [])

        second = store.enforce_retention(
            now=final_pass,
            artifact_delete=delete_artifacts,
        )
        self.assertEqual(second.parents_processed, 2)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_feedback where feedback_id = ?",
                (feedback["feedback_id"],),
            ).fetchone()[0],
            0,
        )
        self.assertFalse(artifact_visible)
        self.assertEqual(calls, [run_id])
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_generation_runs where run_id = ?",
                (run_id,),
            ).fetchone()[0],
            0,
        )

    def test_retention_worker_masks_unexpected_storage_errors(self):
        args = mock.Mock(
            apply=True,
            dry_run=False,
            database_url="sqlite:///synthetic",
            use_configured_database=False,
            now=None,
            batch_size=1,
            workspace_id="workspace-a",
        )
        output = io.StringIO()
        with (
            mock.patch.object(enforce_forensic_retention, "parse_args", return_value=args),
            mock.patch.object(
                webapp.DatabaseSqagStorage,
                "ensure_ready",
                side_effect=RuntimeError("private-provider-marker"),
            ),
            contextlib.redirect_stdout(output),
        ):
            result = enforce_forensic_retention.main()
        rendered = output.getvalue()
        self.assertEqual(result, 1)
        self.assertIn("retention_storage_unavailable", rendered)
        self.assertNotIn("private-provider-marker", rendered)

    def test_late_sibling_run_aborts_retention_before_artifact_deletion(self):
        store = self.store()
        old_time = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=old_time)
        store.finish_run(
            run_id,
            "completed",
            quote_session_id="quote-late-sibling123",
            now=old_time,
        )
        past = iso_timestamp(old_time)
        for table in (
            "sqag_generation_runs",
            "sqag_generation_evidence",
            "sqag_audit_events",
        ):
            self.connection.execute(
                f"update {table} set retention_expires_at = ? where run_id = ?",
                (past, run_id),
            )
        self.connection.commit()
        artifact_visible = True
        sibling_run_id = ""

        def delete_artifacts(_item, finalize):
            nonlocal sibling_run_id, artifact_visible
            sibling_run_id = store.record_run_started(
                "generate",
                {"synthetic": "late sibling"},
                job_id="job-latesibling123",
            )
            store.finish_run(
                sibling_run_id,
                "completed",
                quote_session_id="quote-late-sibling123",
            )
            finalize(self.connection, require_session_exclusive=True)
            artifact_visible = False
            return True

        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            artifact_delete=delete_artifacts,
        )

        self.assertEqual(result.held, 1)
        self.assertTrue(artifact_visible)
        self.assertTrue(sibling_run_id)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0], 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (sibling_run_id,)).fetchone()[0], 1)

    def test_existing_sibling_keeps_session_but_allows_expired_run_cleanup(self):
        store = self.store()
        old_time = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        old_run_id = store.record_run_started("generate", {"synthetic": "old"}, now=old_time)
        store.finish_run(
            old_run_id,
            "completed",
            quote_session_id="quote-shared-session123",
            now=old_time,
        )
        sibling_run_id = store.record_run_started(
            "generate",
            {"synthetic": "sibling"},
            job_id="job-sharedsession123",
        )
        store.finish_run(
            sibling_run_id,
            "completed",
            quote_session_id="quote-shared-session123",
        )
        past = iso_timestamp(old_time)
        for table in (
            "sqag_generation_runs",
            "sqag_generation_evidence",
            "sqag_audit_events",
        ):
            self.connection.execute(
                f"update {table} set retention_expires_at = ? where run_id = ?",
                (past, old_run_id),
            )
        self.connection.commit()
        artifact_visible = True

        def retain_shared_session(item, finalize):
            self.assertTrue(
                enforce_forensic_retention.session_has_retained_forensic_links(
                    self.connection,
                    "workspace-a",
                    item["quote_session_id"],
                    item["run_id"],
                )
            )
            finalize(self.connection)
            return True

        result = store.enforce_retention(
            now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
            artifact_delete=retain_shared_session,
        )

        self.assertEqual(result.parents_processed, 1)
        self.assertTrue(artifact_visible)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (old_run_id,)).fetchone()[0], 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (sibling_run_id,)).fetchone()[0], 1)

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

    def test_quote_session_listing_metadata_does_not_load_artifact_blobs(self):
        source = inspect.getsource(webapp.DatabaseSqagStorage._quote_artifact_metadata)
        self.assertNotIn("content_blob", source)
        self.assertIn("metadata_json", source)

    def test_forensic_and_feedback_failure_events_are_loggable(self):
        self.assertTrue(
            {
                "feedback_context_failed",
                "feedback_submission_failed",
                "forensic_persistence_failed",
                "forensic_reconciliation_failed",
                "quote_publication_compensation_failed",
                "quote_session_update_failed",
            }.issubset(webapp.ALLOWED_LOG_EVENTS)
        )

    def test_support_feedback_evidence_uses_nested_report_run_and_session(self):
        class Store:
            def get_feedback(self, reference, *, audit_access):
                self.reference = reference
                self.audit_access = audit_access
                return {
                    "report": {
                        "feedback_id": "feedback-linked123",
                        "run_id": "run-linked123",
                        "session_id": "quote-linked123",
                    },
                    "status_history": [],
                }

            def resolve_feedback_evidence_run(self, report, **_kwargs):
                self.resolved_report = report
                return report["run_id"]

            def verify_run_evidence(self, run_id, **kwargs):
                verifier = kwargs["artifact_verifier_factory"]()
                self.verified = (run_id, {**kwargs, "artifact_verifier": verifier})
                return {"integrity_ok": True, "run_id": run_id}

        store = Store()
        reader = mock.Mock()
        reader.verify_artifact = object()
        with (
            mock.patch.object(webapp, "require_support_forensics"),
            mock.patch.object(
                webapp,
                "forensic_store_for_auth_session",
                return_value=contextlib.nullcontext(store),
            ),
            mock.patch.object(
                webapp,
                "support_forensic_artifact_reader_for_auth_session",
                return_value=reader,
            ) as make_reader,
        ):
            result = webapp.support_feedback_evidence_for_auth_session(
                "SUP-123", "support_investigation", {"synthetic": True}
            )

        self.assertTrue(result["integrity_ok"])
        make_reader.assert_called_once()
        reader.bind_run.assert_called_once_with("run-linked123")
        self.assertEqual(store.resolved_report["session_id"], "quote-linked123")
        self.assertEqual(store.verified[0], "run-linked123")
        self.assertEqual(store.verified[1]["reason_code"], "support_investigation")
    def test_direct_generation_validation_block_starts_and_finishes_one_run(self):
        with (
            mock.patch.object(
                webapp,
                "generation_payload_with_profile_defaults",
                side_effect=lambda payload, **_kwargs: payload,
            ),
            mock.patch.object(
                webapp,
                "validate_generation_payload",
                return_value=[webapp.MISSING_IMAGES_MESSAGE],
            ),
            mock.patch.object(
                webapp, "begin_generation_forensics", return_value="run-direct123"
            ) as begin,
            mock.patch.object(
                webapp,
                "finish_generation_forensics",
                side_effect=lambda run_id, result, *_args, **_kwargs: {
                    **result,
                    "generation_run_id": run_id,
                },
            ) as finish,
        ):
            result = webapp.run_quote_job({}, job_id="job-direct123")

        begin.assert_called_once()
        self.assertEqual(finish.call_count, 1)
        self.assertEqual(finish.call_args.args[0], "run-direct123")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["generation_run_id"], "run-direct123")

    def test_direct_profile_and_pricing_blocks_each_finish_the_started_run(self):
        cases = (
            ("profile", [webapp.PROFILE_SELECTION_ERROR_MESSAGE]),
            ("pricing", [webapp.PRICING_REFERENCE_SELECTION_ERROR_MESSAGE]),
        )
        for label, errors in cases:
            with (
                mock.patch.object(
                    webapp,
                    "generation_payload_with_profile_defaults",
                    side_effect=lambda payload, **_kwargs: payload,
                ),
                mock.patch.object(webapp, "validate_generation_payload", return_value=errors),
                mock.patch.object(
                    webapp,
                    "begin_generation_forensics",
                    return_value=f"run-{label}123",
                ) as begin,
                mock.patch.object(
                    webapp,
                    "finish_generation_forensics",
                    side_effect=lambda run_id, result, *_args, **_kwargs: {
                        **result,
                        "generation_run_id": run_id,
                    },
                ) as finish,
            ):
                result = webapp.run_quote_job({}, job_id=f"job-{label}123")
            begin.assert_called_once()
            self.assertEqual(finish.call_args.args[0], f"run-{label}123")
            self.assertEqual(result["status"], "blocked")

    def test_direct_storage_block_finishes_the_started_run(self):
        unavailable = webapp.SqagStorageAccessError(
            "Synthetic storage unavailable.",
            status=503,
            reason="storage_artifact_database_not_migrated",
        )
        with (
            mock.patch.object(
                webapp,
                "generation_payload_with_profile_defaults",
                side_effect=lambda payload, **_kwargs: payload,
            ),
            mock.patch.object(webapp, "validate_generation_payload", return_value=[]),
            mock.patch.object(
                webapp,
                "payload_with_database_pricing_reference_detail",
                side_effect=lambda payload, **_kwargs: payload,
            ),
            mock.patch.object(webapp, "configured_artifact_storage_mode", return_value="database"),
            mock.patch.object(
                webapp,
                "ensure_quote_artifact_storage_available_for_auth_session",
                side_effect=unavailable,
            ),
            mock.patch.object(
                webapp, "begin_generation_forensics", return_value="run-storage123"
            ) as begin,
            mock.patch.object(
                webapp,
                "finish_generation_forensics",
                side_effect=lambda run_id, result, *_args, **_kwargs: {
                    **result,
                    "generation_run_id": run_id,
                },
            ) as finish,
        ):
            result = webapp.run_quote_job({}, job_id="job-storage123")
        begin.assert_called_once()
        self.assertEqual(finish.call_args.args[0], "run-storage123")
        self.assertEqual(result["status"], "failed")

    def test_async_generation_run_id_does_not_start_a_second_run(self):
        with (
            mock.patch.object(
                webapp,
                "generation_payload_with_profile_defaults",
                side_effect=lambda payload, **_kwargs: payload,
            ),
            mock.patch.object(webapp, "validate_generation_payload", return_value=[webapp.MISSING_IMAGES_MESSAGE]),
            mock.patch.object(webapp, "begin_generation_forensics") as begin,
            mock.patch.object(
                webapp,
                "finish_generation_forensics",
                side_effect=lambda run_id, result, *_args, **_kwargs: {
                    **result,
                    "generation_run_id": run_id,
                },
            ),
        ):
            result = webapp.run_quote_job(
                {"_generation_run_id": "run-async123"}, job_id="job-async123"
            )
        begin.assert_not_called()
        self.assertEqual(result["generation_run_id"], "run-async123")

    def test_postgres_delete_guard_consumes_authorization_atomically(self):
        sql = (
            ROOT / "migrations" / "005_forensic_postgres_delete_guards.sql"
        ).read_text(encoding="utf-8").lower()
        self.assertIn("delete from sqag_retention_delete_authorizations", sql)
        self.assertIn("returning", sql)
        self.assertNotIn(
            "if not exists (\n    select 1 from sqag_retention_delete_authorizations",
            sql,
        )

    def _assert_atomic_publication(self, artifact_mode):
        content = f"synthetic-{artifact_mode}-published-xlsx".encode("ascii")
        digest = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            output_dir = root / "out"
            output_dir.mkdir(parents=True)
            (output_dir / "quotation.xlsx").write_bytes(content)
            env = {
                "APP_MODE": "deploy",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": artifact_mode,
                "SQAG_DATABASE_URL": database_url,
            }
            backend = webapp.InMemoryObjectStorageBackend()
            backend_context = (
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend)
                if artifact_mode == "object"
                else contextlib.nullcontext()
            )
            with mock.patch.dict(os.environ, env, clear=False), backend_context:
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    "workspace-publication",
                    role="admin",
                    user_id="synthetic-user",
                )
                with storage.connection() as connection:
                    store = ForensicStore(
                        connection,
                        "workspace-publication",
                        "pid-test-v1-" + "p" * 24,
                    )
                    run_id = store.record_run_started(
                        "generate", {"synthetic": True}, job_id="job-publication123"
                    )

                payload = {"quote_session": {"session_id": "quote-publication123"}}
                staged = storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output_dir,
                    publish=False,
                    generation_run_id=run_id,
                    generation_job_id="job-publication123",
                )
                self.assertFalse(staged["status"]["quote_generated"])
                self.assertFalse(staged["exports"]["xlsx"]["exists"])
                self.assertIsNone(
                    storage.quote_session_export_artifact("quote-publication123", "xlsx")
                )
                self.assertTrue(
                    storage.mark_quote_session_publication_failed(
                        "quote-publication123", run_id, "synthetic_retry"
                    )
                )
                self.assertIsNone(
                    storage.quote_session_export_artifact("quote-publication123", "xlsx")
                )
                (output_dir / "quotation.xlsx").write_bytes(content)
                retried = storage.create_or_update_quote_session(
                    payload,
                    result={"status": "completed"},
                    output_dir=output_dir,
                    publish=False,
                    generation_run_id=run_id,
                    generation_job_id="job-publication123",
                )
                self.assertFalse(retried["status"]["quote_generated"])
                with storage.connection() as connection:
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                            ("workspace-publication", "quote-publication123"),
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            (
                                "select count(*) from sqag_object_artifacts "
                                "where workspace_id = ? and owner_type = ? and owner_id = ?"
                                if artifact_mode == "object"
                                else "select count(*) from sqag_quote_publication_artifacts "
                                "where workspace_id = ? and run_id = ?"
                            ),
                            (
                                ("workspace-publication", "generated_quote_version", run_id)
                                if artifact_mode == "object"
                                else ("workspace-publication", run_id)
                            ),
                        ).fetchone()[0],
                        1,
                    )
                evidence_files = storage.quote_session_evidence_files(
                    "quote-publication123", run_id
                )
                self.assertEqual(evidence_files[0]["sha256"], digest)
                self.assertEqual(evidence_files[0]["bytes"], len(content))

                with storage.connection() as connection:
                    store = ForensicStore(
                        connection,
                        "workspace-publication",
                        "pid-test-v1-" + "p" * 24,
                    )
                    store.finish_run(
                        run_id,
                        "completed",
                        quote_session_id="quote-publication123",
                        result_summary={"artifacts": evidence_files},
                        canonical_manifest={"artifacts": evidence_files},
                        commit=False,
                    )
                    storage.publish_quote_session_forensic_transaction(
                        connection,
                        "quote-publication123",
                        run_id,
                        evidence_files,
                    )
                    connection.commit()

                with storage.connection() as connection:
                    store = ForensicStore(
                        connection,
                        "workspace-publication",
                        "pid-test-v1-" + "p" * 24,
                    )
                    self.assertFalse(
                        store.finish_run(
                            run_id,
                            "completed",
                            quote_session_id="quote-publication123",
                            result_summary={"artifacts": evidence_files},
                            canonical_manifest={"artifacts": evidence_files},
                            commit=False,
                        )
                    )
                    storage.publish_quote_session_forensic_transaction(
                        connection,
                        "quote-publication123",
                        run_id,
                        evidence_files,
                    )
                    connection.commit()
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_generation_evidence where workspace_id = ? and run_id = ? and evidence_type = ?",
                            ("workspace-publication", run_id, "generation_manifest"),
                        ).fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_audit_events where workspace_id = ? and run_id = ? and event_type = ?",
                            ("workspace-publication", run_id, "generation_completed"),
                        ).fetchone()[0],
                        1,
                    )

                published = storage.get_quote_session("quote-publication123")
                artifact = storage.quote_session_export_artifact(
                    "quote-publication123", "xlsx"
                )
                self.assertTrue(published["status"]["quote_generated"])
                self.assertTrue(published["exports"]["xlsx"]["exists"])
                self.assertEqual(artifact["content"], content)
                self.assertEqual(artifact["size_bytes"], len(content))
                self.assertEqual(artifact["sha256"], digest)

    def test_database_artifact_publication_is_atomic_with_terminal_forensics(self):
        self._assert_atomic_publication("database")

    def test_object_artifact_publication_is_atomic_with_terminal_forensics(self):
        self._assert_atomic_publication("object")

    def _assert_tampered_staged_bytes_block_publication(self, artifact_mode):
        content = f"synthetic-{artifact_mode}-durable-content".encode("ascii")
        tampered = b"x" * len(content)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            output_dir = root / "out"
            output_dir.mkdir(parents=True)
            (output_dir / "quotation.xlsx").write_bytes(content)
            env = {
                "APP_MODE": "internal-uat",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": artifact_mode,
                "SQAG_DATABASE_URL": database_url,
            }
            backend = webapp.InMemoryObjectStorageBackend()
            backend_context = (
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend)
                if artifact_mode == "object"
                else contextlib.nullcontext()
            )
            with mock.patch.dict(os.environ, env, clear=False), backend_context:
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.DatabaseSqagStorage(
                    database_url, "workspace-tamper", role="admin", user_id="synthetic-user"
                )
                with storage.connection() as connection:
                    store = ForensicStore(connection, "workspace-tamper", "pid-test-v1-" + "t" * 24)
                    run_id = store.record_run_started("generate", {"synthetic": True})
                storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": "quote-tamper123"}},
                    result={"status": "completed"},
                    output_dir=output_dir,
                    publish=False,
                    generation_run_id=run_id,
                    generation_job_id="job-tamper123",
                )
                evidence_files = storage.quote_session_evidence_files("quote-tamper123")
                if artifact_mode == "object":
                    key = next(iter(backend._objects))
                    backend._objects[key] = tampered
                else:
                    with storage.connection() as connection:
                        connection.execute(
                            "update sqag_quote_artifacts set content_blob = ? where workspace_id = ? and session_id = ? and artifact_kind = 'xlsx'",
                            (tampered, "workspace-tamper", "quote-tamper123"),
                        )
                        connection.commit()
                with storage.connection() as connection:
                    with self.assertRaises(ValueError):
                        storage.publish_quote_session_forensic_transaction(
                            connection, "quote-tamper123", run_id, evidence_files
                        )
                    connection.rollback()
                session = storage.get_quote_session("quote-tamper123")
                self.assertFalse(session["status"]["quote_generated"])
                self.assertIsNone(storage.quote_session_export_artifact("quote-tamper123", "xlsx"))

    def test_database_publication_rehashes_staged_durable_bytes(self):
        self._assert_tampered_staged_bytes_block_publication("database")

    def test_object_publication_rehashes_staged_durable_bytes(self):
        self._assert_tampered_staged_bytes_block_publication("object")
    def test_manifest_and_terminal_audit_failures_roll_back_publication(self):
        for failure_point in ("manifest", "audit"):
            with self.subTest(failure_point=failure_point), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
                output_dir = root / "out"
                output_dir.mkdir(parents=True)
                (output_dir / "quotation.xlsx").write_bytes(b"synthetic-rollback-xlsx")
                env = {
                    "APP_MODE": "deploy",
                    "SQAG_STORAGE_MODE": "database",
                    "SQAG_ARTIFACT_STORAGE_MODE": "database",
                    "SQAG_DATABASE_URL": database_url,
                }
                with mock.patch.dict(os.environ, env, clear=False):
                    webapp.apply_sqag_storage_migrations(database_url)
                    storage = webapp.DatabaseSqagStorage(
                        database_url,
                        "workspace-rollback",
                        role="admin",
                        user_id="synthetic-user",
                    )
                    with storage.connection() as connection:
                        store = ForensicStore(
                            connection,
                            "workspace-rollback",
                            "pid-test-v1-" + "r" * 24,
                        )
                        run_id = store.record_run_started(
                            "generate",
                            {"synthetic": True},
                            job_id=f"job-{failure_point}123",
                        )
                    storage.create_or_update_quote_session(
                        {"quote_session": {"session_id": "quote-rollback123"}},
                        result={"status": "completed"},
                        output_dir=output_dir,
                        publish=False,
                        generation_run_id=run_id,
                        generation_job_id=f"job-{failure_point}123",
                    )
                    evidence_files = storage.quote_session_evidence_files(
                        "quote-rollback123"
                    )

                    with storage.connection() as connection:
                        store = ForensicStore(
                            connection,
                            "workspace-rollback",
                            "pid-test-v1-" + "r" * 24,
                        )
                        original_evidence = store.append_evidence
                        original_audit = store.append_audit

                        def append_evidence(run_id_value, evidence_type, evidence, **kwargs):
                            if failure_point == "manifest" and evidence_type == "generation_manifest":
                                raise RuntimeError("synthetic manifest insert failure")
                            return original_evidence(run_id_value, evidence_type, evidence, **kwargs)

                        def append_audit(event_type, details, **kwargs):
                            if failure_point == "audit" and event_type == "generation_completed":
                                raise RuntimeError("synthetic terminal audit insert failure")
                            return original_audit(event_type, details, **kwargs)

                        store.append_evidence = append_evidence
                        store.append_audit = append_audit
                        with self.assertRaises(RuntimeError):
                            try:
                                store.finish_run(
                                    run_id,
                                    "completed",
                                    quote_session_id="quote-rollback123",
                                    result_summary={"artifacts": evidence_files},
                                    canonical_manifest={"artifacts": evidence_files},
                                    commit=False,
                                )
                                storage.publish_quote_session_forensic_transaction(
                                    connection,
                                    "quote-rollback123",
                                    run_id,
                                    evidence_files,
                                )
                                connection.commit()
                            except Exception:
                                connection.rollback()
                                raise

                    session = storage.get_quote_session("quote-rollback123")
                    self.assertFalse(session["status"]["quote_generated"])
                    self.assertIsNone(
                        storage.quote_session_export_artifact(
                            "quote-rollback123", "xlsx"
                        )
                    )
                    with storage.connection() as connection:
                        run = connection.execute(
                            "select status, completed_at from sqag_generation_runs where workspace_id = ? and run_id = ?",
                            ("workspace-rollback", run_id),
                        ).fetchone()
                        self.assertEqual(run["status"], "received")
                        self.assertIsNone(run["completed_at"])

    def test_terminal_forensic_failure_keeps_staged_artifact_unavailable(self):
        class SyntheticStorage(webapp.DatabaseSqagStorage):
            def __init__(self):
                self.published = False
                self.content = b"synthetic-staged-xlsx"

            def create_or_update_quote_session(self, _payload, **kwargs):
                self.published = bool(kwargs.get("publish", True))
                return {
                    "session_id": "quote-staged123",
                    "exports": {
                        "xlsx": {
                            "filename": "quotation.xlsx",
                            "exists": self.published,
                            "url": (
                                "/api/quote-sessions/quote-staged123/download/xlsx"
                                if self.published
                                else None
                            ),
                            "size_bytes": len(self.content),
                            "sha256": "a" * 64,
                        }
                    },
                }

            def quote_session_evidence_files(self, _session_id, _run_id=""):
                return [
                    {
                        "name": "quotation.xlsx",
                        "bytes": len(self.content),
                        "sha256": "a" * 64,
                    }
                ]

            def quote_session_export_artifact(self, _session_id, _kind):
                if not self.published:
                    return None
                return {
                    "content": self.content,
                    "size_bytes": len(self.content),
                    "sha256": "a" * 64,
                }

            def mark_quote_session_publication_failed(self, _session_id, _run_id, _error_code):
                raise RuntimeError("synthetic compensation failure")

        storage = SyntheticStorage()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = root / "layout.xlsx"
            layout.write_bytes(b"synthetic-layout")
            rules = root / "layout-rules.json"
            rules.write_text("{}", encoding="utf-8")
            pricing = root / "pricing.json"
            pricing.write_text(json.dumps({"items": []}), encoding="utf-8")

            def fake_generator(command, **_kwargs):
                output_dir = Path(command[command.index("--out") + 1])
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "quotation.xlsx").write_bytes(storage.content)
                return webapp.subprocess.CompletedProcess(command, 0, "", "")

            payload = {"quote_session": {"session_id": "quote-staged123"}}

            class SyntheticProfile(dict):
                def __init__(self):
                    super().__init__(id="synthetic-profile", label="Synthetic Profile")
                    self.quotation_layout_path = layout
                    self.layout_rules_path = rules

            profile = SyntheticProfile()
            failure = {
                "status": "failed",
                "errors": ["Generation evidence storage is unavailable."],
                "generation_run_id": "run-staged123",
            }
            with (
                mock.patch.dict(os.environ, {"APP_MODE": "deploy"}, clear=False),
                mock.patch.object(
                    webapp,
                    "generation_payload_with_profile_defaults",
                    side_effect=lambda value, **_kwargs: value,
                ),
                mock.patch.object(webapp, "validate_generation_payload", return_value=[]),
                mock.patch.object(
                    webapp,
                    "payload_with_database_pricing_reference_detail",
                    side_effect=lambda value, **_kwargs: value,
                ),
                mock.patch.object(
                    webapp,
                    "quote_session_storage_for_auth_session",
                    return_value=storage,
                ),
                mock.patch.object(
                    webapp, "ensure_quote_artifact_storage_available_for_auth_session"
                ),
                mock.patch.object(
                    webapp, "configured_artifact_storage_mode", return_value="database"
                ),
                mock.patch.object(webapp, "configured_storage_mode", return_value="local"),
                mock.patch.object(
                    webapp, "begin_generation_forensics", return_value="run-staged123"
                ),
                mock.patch.object(webapp, "load_profile_pack", return_value=profile),
                mock.patch.object(
                    webapp, "pricing_catalog_path_for_payload", return_value=pricing
                ),
                mock.patch.object(webapp, "save_uploaded_images", return_value=[]),
                mock.patch.object(webapp, "payload_to_brief", return_value={}),
                mock.patch.object(webapp.subprocess, "run", side_effect=fake_generator),
                mock.patch.object(webapp, "read_pricing_matches", return_value=[]),
                mock.patch.object(webapp, "read_export_status", return_value=""),
                mock.patch.object(
                    webapp, "embedded_layout_rules_from_xlsx_bytes", return_value={}
                ),
                mock.patch.object(
                    webapp, "finish_generation_forensics", return_value=failure
                ),
                mock.patch.object(
                    webapp, "write_local_log"
                ) as write_log,
            ):
                result = webapp.run_quote_job(
                    payload,
                    output_root=root / "out",
                    tmp_root=root / "tmp",
                    job_id="job-staged123",
                )

        self.assertEqual(result["status"], "failed")
        self.assertIsNone(
            storage.quote_session_export_artifact("quote-staged123", "xlsx")
        )
        compensation_logs = [
            call for call in write_log.call_args_list
            if call.args and call.args[0] == "quote_publication_compensation_failed"
        ]
        self.assertEqual(len(compensation_logs), 1)
        self.assertRegex(compensation_logs[0].args[1]["error_reference"], r"^ERR-[A-F0-9]{8}$")


if __name__ == "__main__":
    unittest.main()
