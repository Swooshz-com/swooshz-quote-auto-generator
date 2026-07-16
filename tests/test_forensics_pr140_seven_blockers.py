import contextlib
import datetime as dt
import json
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from webapp.forensics import ForensicStore
from webapp import server as webapp


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql"


class Pr140SevenBlockerRegressionTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(MIGRATION.read_text(encoding="utf-8"))

    def tearDown(self):
        self.connection.close()

    def store(self, workspace_id="workspace-seven", actor="pid-v1-seven-user"):
        return ForensicStore(self.connection, workspace_id, actor)

    def test_atomic_publication_store_open_failure_returns_failed_result(self):
        @contextlib.contextmanager
        def unavailable_store():
            raise RuntimeError("synthetic forensic store open failure")
            yield

        for durable_mode in ("database", "object"):
            with (
                self.subTest(durable_mode=durable_mode),
                mock.patch.object(webapp, "configured_app_mode", return_value="internal-uat"),
                mock.patch.object(
                    webapp, "forensic_store_for_auth_session", return_value=unavailable_store()
                ),
                mock.patch.object(webapp, "write_local_log"),
            ):
                result = webapp.finish_generation_forensics(
                    f"run-store-open-{durable_mode}",
                    {"job_id": f"job-store-open-{durable_mode}", "status": "completed", "files": [{"name": "quotation.xlsx", "url": "/private-download"}]},
                    {"synthetic": True},
                    publication_storage=mock.Mock(storage_mode=durable_mode),
                    publication_session_id=f"quote-store-open-{durable_mode}",
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["generation_run_id"], f"run-store-open-{durable_mode}")
            self.assertNotIn("files", result)
            self.assertNotIn("download", json.dumps(result).lower())

        with (
            mock.patch.object(webapp, "configured_app_mode", return_value="internal-uat"),
            mock.patch.object(webapp, "forensic_store_for_auth_session", return_value=unavailable_store()),
            mock.patch.object(webapp, "write_local_log"),
        ):
            local_result = webapp.finish_generation_forensics(
                "run-store-open-local",
                {"job_id": "job-store-open-local", "status": "completed", "files": [{"name": "quotation.xlsx"}]},
                {"synthetic": True},
            )
        self.assertEqual(local_result["status"], "completed")
        self.assertEqual(local_result["generation_run_id"], "run-store-open-local")
        self.assertEqual(local_result["files"], [{"name": "quotation.xlsx"}])

    def test_mismatched_run_and_session_prefers_validated_session(self):
        store = self.store()
        run_id = store.record_run_started("generate", {"synthetic": True})
        store.finish_run(run_id, "blocked", quote_session_id="quote-session-a", canonical_manifest={"artifacts": []})

        context = store.feedback_context(run_id=run_id, validated_session_id="quote-session-b")
        self.assertEqual(context["link_type"], "quote_session")
        self.assertEqual(context["session_id"], "quote-session-b")
        self.assertEqual(context["run_id"], "")
        self.assertEqual(store.feedback_context()["link_type"], "none")
        self.assertEqual(store.feedback_context()["run_id"], "")

        submitted = store.submit_feedback({
            "category": "bug",
            "title": "Synthetic stale run pair",
            "message": "The selected quote session must win over a stale run.",
            "run_id": run_id,
            "validated_session_id": "quote-session-b",
        })
        row = self.connection.execute(
            "select run_id, session_id from sqag_feedback where feedback_id = ?",
            (submitted["feedback_id"],),
        ).fetchone()
        self.assertIsNone(row["run_id"])
        self.assertEqual(row["session_id"], "quote-session-b")

    def test_client_generation_context_is_transitioned_and_persisted_as_a_pair(self):
        source = (ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function transitionGenerationContext", source)
        transition = source.split("function transitionGenerationContext", 1)[1].split("function randomQuoteSessionToken", 1)[0]
        snapshot = source.split("function buildSessionSnapshot()", 1)[1].split("function clearSessionState()", 1)[0]
        restore = source.split("async function applyQuoteSessionSnapshot", 1)[1].split("function quoteOutputProgressForNavigation", 1)[0]
        feedback = source.split("async function loadFeedbackContext()", 1)[1].split("async function openFeedbackModal", 1)[0]
        reset = source.split("function resetCurrentQuoteDraftState()", 1)[1].split("function clearQuote", 1)[0]
        self.assertIn("generationContext:", snapshot)
        self.assertIn("transitionGenerationContext", restore)
        self.assertIn("transitionGenerationContext", reset)
        self.assertIn("state.feedbackContextRequestId += 1", transition)
        self.assertIn("state.feedbackContext = null", transition)
        self.assertIn("loadFeedbackContext()", transition)
        self.assertIn("currentGenerationContext", feedback)

    def test_feedback_retention_deletes_mixed_feedback_run_audits(self):
        store = self.store()
        old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=old)
        store.finish_run(run_id, "completed", quote_session_id="quote-mixed-audit", canonical_manifest={"artifacts": []}, now=old)
        submitted = store.submit_feedback({
            "category": "bug",
            "title": "Synthetic mixed audit",
            "message": "Mixed feedback and run audit ownership.",
            "run_id": run_id,
            "validated_session_id": "quote-mixed-audit",
        })
        store.update_feedback_status(submitted["support_reference"], "resolved", resolution_note="Synthetic closure", now=old)
        feedback_id = submitted["feedback_id"]
        self.connection.execute(
            "update sqag_feedback set retention_expires_at = ?, original_retention_expires_at = ? where feedback_id = ?",
            ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", feedback_id),
        )
        self.connection.commit()
        item = dict(self.connection.execute("select * from sqag_feedback where feedback_id = ?", (feedback_id,)).fetchone())
        store._delete_retention_graph("feedback", item, feedback_id, "2024-01-02T00:00:00Z")
        self.connection.commit()

        self.assertEqual(
            self.connection.execute("select count(*) from sqag_audit_events where feedback_id = ?", (feedback_id,)).fetchone()[0],
            0,
        )

    def test_mixed_audit_holds_preserve_then_release_complete_graph(self):
        store = self.store()
        old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        run_id = store.record_run_started("generate", {"synthetic": True}, now=old)
        store.finish_run(
            run_id,
            "completed",
            quote_session_id="quote-mixed-held",
            canonical_manifest={"artifacts": []},
            now=old,
        )
        submitted = store.submit_feedback({
            "category": "bug",
            "title": "Synthetic held mixed audit",
            "message": "Mixed graph hold coverage.",
            "run_id": run_id,
            "validated_session_id": "quote-mixed-held",
        })
        store.update_feedback_status(
            submitted["support_reference"], "resolved", resolution_note="Synthetic closure", now=old
        )
        feedback_id = submitted["feedback_id"]
        self.connection.execute(
            "update sqag_feedback set retention_expires_at = ?, original_retention_expires_at = ? "
            "where feedback_id = ?",
            ("2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", feedback_id),
        )
        mixed_event_id = self.connection.execute(
            "select event_id from sqag_audit_events "
            "where workspace_id = ? and feedback_id = ? and run_id = ? order by event_id limit 1",
            ("workspace-seven", feedback_id, run_id),
        ).fetchone()[0]
        other_store = self.store("workspace-other")
        other_event_id = other_store.append_audit(
            "synthetic_wrong_workspace_feedback_link",
            {"synthetic": True},
            feedback_id=feedback_id,
            now=old,
        )
        store.set_legal_hold("sqag_audit_events", "event_id", mixed_event_id, True, now=old)
        held_audit = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1
        )
        self.assertGreaterEqual(held_audit.held, 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_feedback where feedback_id = ?", (feedback_id,)).fetchone()[0], 1)

        store.set_legal_hold("sqag_audit_events", "event_id", mixed_event_id, False, now=old)
        store.set_legal_hold("sqag_generation_runs", "run_id", run_id, True, now=old)
        held_run = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1
        )
        self.assertGreaterEqual(held_run.held, 1)
        store.set_legal_hold("sqag_generation_runs", "run_id", run_id, False, now=old)

        deleted_feedback = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1
        )
        self.assertGreaterEqual(deleted_feedback.deleted, 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_feedback where feedback_id = ?", (feedback_id,)).fetchone()[0], 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_audit_events where workspace_id = 'workspace-seven' and feedback_id = ?", (feedback_id,)).fetchone()[0], 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_audit_events where workspace_id = 'workspace-other' and event_id = ?", (other_event_id,)).fetchone()[0], 1)

        deleted_run = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1
        )
        self.assertGreaterEqual(deleted_run.deleted, 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0], 0)
        repeated = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=10
        )
        self.assertEqual(repeated.failed, 0)

    def test_expired_deletion_receipt_is_bounded_workspace_scoped_and_nonrecursive(self):
        rows = (
            ("delete-expired", "workspace-seven", "run-expired", "2021-01-01T00:00:00Z"),
            ("delete-future", "workspace-seven", "run-future", "2028-01-01T00:00:00Z"),
            ("delete-other", "workspace-other", "run-other", "2021-01-01T00:00:00Z"),
        )
        for receipt_id, workspace_id, record_id, expiry in rows:
            self.connection.execute(
                "insert into sqag_deletion_receipts (receipt_id, workspace_id, record_type, record_id, reason, deleted_at, original_retention_expires_at, created_at, retention_expires_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (receipt_id, workspace_id, "sqag_generation_runs", record_id, "retention_expired", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", expiry),
            )
        self.connection.commit()
        store = self.store()
        now = dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc)

        dry_run = store.enforce_retention(now=now, batch_size=1, apply=False)
        self.assertEqual(dry_run.receipt_examined, 1)
        self.assertEqual(dry_run.receipt_deleted, 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_deletion_receipts").fetchone()[0], 3)

        result = store.enforce_retention(now=now, batch_size=1)
        self.assertLessEqual(result.receipt_examined, result.scan_limit)
        self.assertEqual(result.receipt_deleted, 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_deletion_receipts where workspace_id = 'workspace-seven'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_deletion_receipts where workspace_id = 'workspace-other'"
            ).fetchone()[0],
            1,
        )
        repeated = store.enforce_retention(now=now, batch_size=1)
        self.assertEqual(repeated.receipt_deleted, 0)
        self.assertEqual(self.connection.execute("select count(*) from sqag_deletion_receipts where receipt_id = 'delete-expired'").fetchone()[0], 0)

    def test_receipt_and_standalone_candidates_are_fairly_interleaved(self):
        store = self.store()
        old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        event_id = store.append_audit("synthetic_receipt_fairness", {"synthetic": True}, now=old)
        self.connection.execute(
            "insert into sqag_deletion_receipts "
            "(receipt_id, workspace_id, record_type, record_id, reason, deleted_at, "
            "original_retention_expires_at, created_at, retention_expires_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "delete-fairness",
                "workspace-seven",
                "sqag_generation_runs",
                "run-fairness",
                "retention_expired",
                "2020-01-01T00:00:00Z",
                "2020-01-01T00:00:00Z",
                "2020-01-01T00:00:00Z",
                "2021-01-01T00:00:00Z",
            ),
        )
        self.connection.commit()
        first = store.enforce_retention(now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1)
        second = store.enforce_retention(now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1)
        self.assertEqual(first.standalone_deleted + second.standalone_deleted, 1)
        self.assertEqual(first.receipt_deleted + second.receipt_deleted, 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_audit_events where event_id = ?", (event_id,)).fetchone()[0], 0)

    def test_held_standalone_prefix_does_not_starve_later_expired_audit(self):
        store = self.store()
        old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        held_records = []
        for index in range(17):
            event_id = store.append_audit(f"synthetic_held_{index:02d}", {"index": index}, now=old)
            self.connection.execute(
                "insert into sqag_legal_holds (hold_id, workspace_id, target_type, target_id, enabled, reason_code, actor_tracking_id, actor_key_version, created_at) values (?, ?, 'audit_event', ?, 1, 'synthetic_hold', ?, 'v1', ?)",
                (f"hold-{index:02d}", "workspace-seven", event_id, "pid-v1-seven-user", "2020-01-01T00:00:00Z"),
            )
            held_records.append((f"hold-{index:02d}", event_id))
        eligible = store.append_audit("synthetic_eligible_after_held_prefix", {"eligible": True}, now=old + dt.timedelta(days=1))
        self.connection.commit()

        first = store.enforce_retention(now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1)
        second = store.enforce_retention(now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1)

        self.assertGreaterEqual(first.standalone_held + second.standalone_held, 17)
        self.assertEqual(first.standalone_failed + second.standalone_failed, 0)
        self.assertEqual(first.standalone_deleted + second.standalone_deleted, 1)
        self.assertEqual(self.connection.execute("select count(*) from sqag_audit_events where event_id = ?", (eligible,)).fetchone()[0], 0)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events where event_id in ({})".format(
                    ",".join("?" for _ in held_records)
                ),
                tuple(event_id for _, event_id in held_records),
            ).fetchone()[0],
            17,
        )
        cursor_before = tuple(self.connection.execute(
            "select last_retention_expires_at, last_record_id from sqag_retention_scan_cursors "
            "where workspace_id = ? and candidate_type = 'standalone_audit'",
            ("workspace-seven",),
        ).fetchone())
        dry_run = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1, apply=False
        )
        cursor_after = tuple(self.connection.execute(
            "select last_retention_expires_at, last_record_id from sqag_retention_scan_cursors "
            "where workspace_id = ? and candidate_type = 'standalone_audit'",
            ("workspace-seven",),
        ).fetchone())
        self.assertEqual(cursor_after, cursor_before)
        self.assertGreaterEqual(dry_run.standalone_held, 1)
        release_hold, released_event = min(held_records, key=lambda item: item[1])
        self.connection.execute("update sqag_legal_holds set enabled = 0 where hold_id = ?", (release_hold,))
        self.connection.commit()
        released = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc), batch_size=1
        )
        self.assertEqual(released.standalone_deleted, 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events where event_id = ?", (released_event,)
            ).fetchone()[0],
            0,
        )

    def test_failed_standalone_audit_rotates_and_is_retried_after_later_work(self):
        store = self.store()
        old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        event_ids = sorted([
            store.append_audit("synthetic_failed_rotation_a", {"index": 1}, now=old),
            store.append_audit("synthetic_failed_rotation_b", {"index": 2}, now=old),
        ])
        original_authorize = store._authorize_delete
        failed_once = False

        def fail_first_once(record_type, record_id):
            nonlocal failed_once
            if record_id == event_ids[0] and not failed_once:
                failed_once = True
                raise RuntimeError("synthetic standalone delete failure")
            return original_authorize(record_type, record_id)

        now = dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc)
        with mock.patch.object(store, "_authorize_delete", side_effect=fail_first_once):
            first = store.enforce_retention(now=now, batch_size=1)
            second = store.enforce_retention(now=now, batch_size=1)
            third = store.enforce_retention(now=now, batch_size=1)

        self.assertEqual(first.standalone_failed, 1)
        self.assertEqual(second.standalone_deleted, 1)
        self.assertEqual(third.standalone_deleted, 1)
        self.assertEqual(
            self.connection.execute(
                "select count(*) from sqag_audit_events where event_id in (?, ?)", tuple(event_ids)
            ).fetchone()[0],
            0,
        )

    def test_pre_generator_manifest_is_privacy_minimized_for_invalid_input(self):
        payload = {
            "images": [{
                "name": "broken-render.png",
                "type": "image/png",
                "size": "not-a-number",
                "data_url": "data:image/png;base64,not-valid-base64",
            }],
            "profile_id": "profile-private-synthetic",
            "pricing_reference_id": "pricing-private-synthetic",
            "prompt": "private synthetic prompt that must not be retained",
            "customer": {"name": "private synthetic customer"},
        }
        manifest = webapp.pre_generator_terminal_manifest(
            "job-invalid-image-red",
            payload,
            terminal_status="blocked",
            error_category="generation_validation_failed",
        )
        serialized = json.dumps(manifest, sort_keys=True)
        self.assertFalse(manifest["generator_executed"])
        self.assertEqual(manifest["artifacts"], [])
        self.assertFalse(manifest["inputs"][0]["content_valid"])
        self.assertEqual(manifest["inputs"][0]["declared_size_bytes"], 0)
        self.assertNotIn("broken-render.png", serialized)
        self.assertNotIn("profile-private-synthetic", serialized)
        self.assertNotIn("pricing-private-synthetic", serialized)
        self.assertNotIn("not-valid-base64", serialized)
        self.assertNotIn("private synthetic prompt", serialized)
        self.assertNotIn("private synthetic customer", serialized)

    def test_async_validation_categories_store_canonical_manifests(self):
        store = self.store()
        image_payload = {
            "images": [{
                "name": "render.png",
                "type": "image/png",
                "size": 1,
                "data_url": "data:image/png;base64,WA==",
            }]
        }
        common = (
            mock.patch.object(
                webapp,
                "forensic_store_for_auth_session",
                side_effect=lambda *_args, **_kwargs: contextlib.nullcontext(store),
            ),
            mock.patch.object(webapp, "configured_app_mode", return_value="internal-uat"),
            mock.patch.object(webapp, "write_local_log"),
        )
        with common[0], common[1], common[2]:
            too_many_images = {
                "images": [
                    {**image_payload["images"][0], "name": f"render-{index}.png"}
                    for index in range(webapp.MAX_REFERENCE_IMAGES + 1)
                ]
            }
            image_limit = webapp.create_job(
                "generate", too_many_images, requested_job_id="job-image-limit-red"
            )
            with mock.patch.object(
                webapp, "quote_detail_missing_fields", return_value=["Project title"]
            ):
                missing_details = webapp.create_job(
                    "generate", image_payload, requested_job_id="job-missing-details-red"
                )
            with (
                mock.patch.object(webapp, "quote_detail_missing_fields", return_value=[]),
                mock.patch.object(
                    webapp, "pricing_reference_selection_error",
                    return_value="Select a valid pricing reference before generating a quote.",
                ),
                mock.patch.object(webapp, "log_database_pricing_reference_resolution_block"),
            ):
                invalid_pricing = webapp.create_job(
                    "generate", image_payload, requested_job_id="job-invalid-pricing-red"
                )
            with (
                mock.patch.object(webapp, "quote_detail_missing_fields", return_value=[]),
                mock.patch.object(webapp, "pricing_reference_selection_error", return_value=""),
                mock.patch.object(
                    webapp, "profile_selection_error",
                    return_value="Select a valid company profile before generating a quote.",
                ),
                mock.patch.object(webapp, "log_database_profile_resolution_block"),
            ):
                invalid_profile = webapp.create_job(
                    "generate", image_payload, requested_job_id="job-invalid-profile-red"
                )

        for result in (image_limit, missing_details, invalid_pricing, invalid_profile):
            self.assertEqual(result["status"], "blocked")
            verified = store.verify_run_evidence(
                result["generation_run_id"],
                reason_code="support_investigation",
                privileged=True,
            )
            self.assertTrue(verified["integrity_ok"], json.dumps(verified, sort_keys=True))
            self.assertFalse(verified["manifest"]["generator_executed"])

    def test_direct_storage_posture_block_stores_manifest_and_preaccept_failure_does_not(self):
        store = self.store()
        payload = {"quote_session": {"session_id": "quote-storage-posture-red"}}
        with (
            mock.patch.object(
                webapp,
                "forensic_store_for_auth_session",
                side_effect=lambda *_args, **_kwargs: contextlib.nullcontext(store),
            ),
            mock.patch.object(
                webapp, "generation_payload_with_profile_defaults",
                side_effect=lambda value, **_kwargs: value,
            ),
            mock.patch.object(webapp, "validate_generation_payload", return_value=[]),
            mock.patch.object(
                webapp, "payload_with_database_pricing_reference_detail",
                side_effect=lambda value, **_kwargs: value,
            ),
            mock.patch.object(
                webapp, "quote_session_storage_for_auth_session",
                side_effect=webapp.SqagStorageAccessError(
                    "Synthetic storage posture block.",
                    status=503,
                    reason="storage_posture_blocked",
                ),
            ),
            mock.patch.object(webapp, "configured_app_mode", return_value="internal-uat"),
            mock.patch.object(webapp, "write_local_log"),
        ):
            blocked = webapp.run_quote_job(payload, job_id="job-storage-posture-red")
        verified = store.verify_run_evidence(
            blocked["generation_run_id"],
            reason_code="support_investigation",
            privileged=True,
        )
        self.assertTrue(verified["integrity_ok"], json.dumps(verified, sort_keys=True))
        self.assertFalse(verified["manifest"]["generator_executed"])
        self.assertEqual(verified["manifest"]["lifecycle_stage"], "storage_posture")

        before = self.connection.execute("select count(*) from sqag_generation_runs").fetchone()[0]
        with (
            mock.patch.object(
                webapp, "forensic_store_for_auth_session",
                side_effect=RuntimeError("synthetic pre-acceptance storage failure"),
            ),
            mock.patch.object(webapp, "configured_app_mode", return_value="internal-uat"),
            mock.patch.object(webapp, "write_local_log"),
        ):
            preaccept = webapp.create_job("generate", {}, requested_job_id="job-preaccept-red")
        self.assertNotIn("generation_run_id", preaccept)
        self.assertEqual(self.connection.execute("select count(*) from sqag_generation_runs").fetchone()[0], before)

    def test_validation_blocked_async_and_direct_runs_store_verifiable_manifests(self):

        store = self.store()
        with (
            mock.patch.object(webapp, "forensic_store_for_auth_session", side_effect=lambda *_args, **_kwargs: contextlib.nullcontext(store)),
            mock.patch.object(webapp, "configured_app_mode", return_value="internal-uat"),
            mock.patch.object(webapp, "write_local_log"),
        ):
            async_result = webapp.create_job("generate", {}, requested_job_id="job-blocked-async-red")
            direct_result = webapp.run_quote_job({}, job_id="job-blocked-direct-red")
            invalid_payload = {
                "images": [{
                    "name": "broken-render.png",
                    "type": "image/png",
                    "size": 999,
                    "data_url": "data:image/png;base64,not-valid-base64",
                }]
            }
            with (
                mock.patch.object(
                    webapp, "generation_payload_with_profile_defaults",
                    side_effect=lambda value, **_kwargs: value,
                ),
                mock.patch.object(webapp, "validate_generation_payload", return_value=["Invalid image upload."]),
            ):
                invalid_image = webapp.run_quote_job(invalid_payload, job_id="job-invalid-image-route-red")

        for result in (async_result, direct_result, invalid_image):
            self.assertEqual(result["status"], "blocked")
            run_id = result["generation_run_id"]
            verified = store.verify_run_evidence(
                run_id, reason_code="support_investigation", privileged=True
            )
            self.assertTrue(verified["integrity_ok"], json.dumps(verified, sort_keys=True))
            manifest = verified["manifest"]
            self.assertEqual(manifest["job_type"], "generate")
            self.assertEqual(manifest["lifecycle_stage"], "request_validation")
            self.assertEqual(manifest["terminal_status"], "blocked")
            self.assertFalse(manifest["generator_executed"])
            self.assertEqual(manifest["artifacts"], [])
            self.assertNotIn("errors", manifest)

    def test_feedback_audit_hold_uses_feedback_graph_lock_identity(self):
        store = self.store()
        submitted = store.submit_feedback(
            {"category": "bug", "title": "Synthetic", "message": "Synthetic"}
        )
        event_id = store.append_audit(
            "synthetic_feedback_only_event",
            {"synthetic": True},
            feedback_id=submitted["feedback_id"],
        )

        identity = store._legal_hold_graph_identity(
            "sqag_audit_events", "event_id", event_id
        )
        self.assertEqual(identity, ("feedback", submitted["feedback_id"]))

    def test_oversized_forensic_request_evidence_is_terminally_tracked_after_acceptance(self):
        payload = {
            "images": [{"type": "image/png", "data_url": "data:image/png;base64,AA=="}],
            "line_items": [{"description": "x" * (webapp.MAX_FORENSIC_REQUEST_EVIDENCE_BYTES + 1)}],
        }
        with (
            mock.patch.object(
                webapp, "begin_generation_forensics",
                side_effect=["run-evidence-async", "run-evidence-direct"],
            ) as begin,
            mock.patch.object(
                webapp, "finish_generation_forensics",
                side_effect=lambda run_id, result, *_args, **_kwargs: {**result, "generation_run_id": run_id},
            ) as finish,
        ):
            async_result = webapp.create_job(
                "generate", payload, requested_job_id="job-evidence-oversize-red"
            )
            direct_result = webapp.run_quote_job(payload, job_id="job-evidence-direct-red")

        self.assertEqual(async_result["status"], "blocked")
        self.assertEqual(direct_result["status"], "blocked")
        self.assertEqual(async_result["generation_run_id"], "run-evidence-async")
        self.assertEqual(direct_result["generation_run_id"], "run-evidence-direct")
        self.assertEqual(begin.call_count, 2)
        self.assertEqual(finish.call_count, 2)


if __name__ == "__main__":
    unittest.main()
