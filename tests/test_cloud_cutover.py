import base64
import contextlib
import json
import os
import sqlite3
import shutil
import unittest
from pathlib import Path
from unittest import mock

from webapp import server as webapp


@contextlib.contextmanager
def writable_test_root():
    root = (
        webapp.PROJECT_ROOT
        / "_tmp"
        / f"cloud-cutover-{webapp.secrets.token_hex(6)}"
    )
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root)

class CloudCutoverTest(unittest.TestCase):


    def object_env(self, database_url: str) -> dict[str, str]:
        return {
            "APP_MODE": "deploy",
            "SQAG_STORAGE_MODE": "database",
            "SQAG_ARTIFACT_STORAGE_MODE": "object",
            "SQAG_DATABASE_URL": database_url,
            "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test",
            "SQAG_OBJECT_STORAGE_BUCKET": "synthetic-bucket",
            "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
            "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-secret-key",
        }

    def auth_session(self, workspace_id: str, user_id: str = "cloud-user"):
        launch_expiry = (
            webapp.dt.datetime.now(webapp.dt.timezone.utc) + webapp.dt.timedelta(minutes=5)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        context = webapp.safe_platform_launch_context({
            "outcome": "consumed",
            "user": {
                "userId": user_id,
                "email": f"{user_id}@example.test",
                "displayName": "Synthetic User",
                "status": "active",
            },
            "workspace": {
                "workspaceId": workspace_id,
                "workspaceSlug": workspace_id,
                "workspaceName": "Synthetic Workspace",
            },
            "app": {"appKey": "sqag", "appName": "SQAG"},
            "membershipRole": "owner",
            "launchTokenExpiresAt": launch_expiry,
            "validationGrantId": f"synthetic-grant-{workspace_id}-{user_id}",
        })
        return {
            "user": webapp.user_from_platform_launch_context(context),
            "platform": context,
        }

    def test_object_mode_draft_uploads_store_only_metadata_in_database(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            image_bytes = b"synthetic-image-bytes"
            pdf_bytes = b"%PDF-1.4 synthetic"
            payload = {}
            payload["quote_session"] = {
                "session_id": "quote-cloud-uploads",
                "draft_state": {"activeSidePanel": "images"},
                "draft_files": [
                    {
                        "session_file_key": "image-key",
                        "name": "render.png",
                        "type": "image/png",
                        "size": len(image_bytes),
                        "data_url": "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii"),
                    },
                    {
                        "session_file_key": "pdf-key",
                        "name": "reference.pdf",
                        "type": "application/pdf",
                        "size": len(pdf_bytes),
                        "data_url": "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii"),
                    },
                ],
            }
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                owner = webapp.app_storage_for_auth_session(self.auth_session("workspace-cloud-a"))
                other = webapp.app_storage_for_auth_session(self.auth_session("workspace-cloud-b"))
                owner.create_or_update_quote_session(payload)
                restored = owner.get_quote_session("quote-cloud-uploads", include_draft_state=True)
                self.assertIsNone(other.get_quote_session("quote-cloud-uploads", include_draft_state=True))
                with contextlib.closing(sqlite3.connect(root / "sqag.sqlite3")) as connection:
                    draft_json = connection.execute(
                        "select draft_files_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                        ("workspace-cloud-a", "quote-cloud-uploads"),
                    ).fetchone()[0]
                    object_rows = connection.execute(
                        "select owner_type, artifact_kind from sqag_object_artifacts where workspace_id = ? order by artifact_kind",
                        ("workspace-cloud-a",),
                    ).fetchall()
                    blob_rows = connection.execute("select count(*) from sqag_file_artifacts").fetchone()[0]
                    quote_blob_rows = connection.execute("select count(*) from sqag_quote_artifacts").fetchone()[0]
                self.assertNotIn("data_url", draft_json)
                self.assertNotIn(base64.b64encode(image_bytes).decode("ascii"), draft_json)
                self.assertEqual(blob_rows, 0)
                self.assertEqual(quote_blob_rows, 0)
                self.assertEqual([row[0] for row in object_rows], ["uploaded_reference", "uploaded_reference"])
                self.assertEqual(restored["draft_files"][0]["data_url"], payload["quote_session"]["draft_files"][0]["data_url"])
                self.assertEqual(restored["draft_files"][1]["data_url"], payload["quote_session"]["draft_files"][1]["data_url"])
                original_object_keys = set(backend._objects)
                replacement_bytes = b"synthetic-replacement-image"
                payload["quote_session"]["draft_files"] = [{
                    "session_file_key": "image-key",
                    "name": "render-v2.png",
                    "type": "image/png",
                    "size": len(replacement_bytes),
                    "data_url": "data:image/png;base64,"
                    + base64.b64encode(replacement_bytes).decode("ascii"),
                }]
                owner.create_or_update_quote_session(payload)
                replaced = owner.get_quote_session(
                    "quote-cloud-uploads", include_draft_state=True,
                )
                with contextlib.closing(sqlite3.connect(root / "sqag.sqlite3")) as connection:
                    active_upload_rows = connection.execute(
                        "select count(*) from sqag_object_artifacts where workspace_id = ? and owner_type = ? and status = ? and deleted_at is null",
                        ("workspace-cloud-a", "uploaded_reference", "active"),
                    ).fetchone()[0]
                self.assertEqual(active_upload_rows, 1)
                self.assertEqual(len(backend._objects), 1)
                self.assertTrue(original_object_keys.isdisjoint(set(backend._objects)))
                self.assertEqual(len(replaced["draft_files"]), 1)
                self.assertEqual(replaced["draft_files"][0]["data_url"], payload["quote_session"]["draft_files"][0]["data_url"])
                self.assertTrue(owner.delete_quote_session("quote-cloud-uploads"))
                self.assertEqual(backend._objects, {})

    def test_object_mode_job_wrapper_cleans_scratch_on_success_and_failure(self):
        with writable_test_root() as root:
            tmp_root = root / "tmp"
            output_root = root / "output"
            env = {"SQAG_ARTIFACT_STORAGE_MODE": "object"}
            for outcome in ("success", "failure"):
                job_id = f"job-cleanup-{outcome}"
                job_tmp = tmp_root / job_id
                output_dir = output_root / job_id
                job_tmp.mkdir(parents=True)
                output_dir.mkdir(parents=True)
                (job_tmp / "upload.pdf").write_bytes(b"scratch")
                (output_dir / "pricing_matches.csv").write_text("scratch", encoding="utf-8")
                def run_with_owned_scratch(*_args, scratch_ownership=None, **_kwargs):
                    scratch_ownership.update({"job_tmp": True, "output_dir": True})
                    if outcome == "failure":
                        raise RuntimeError("synthetic failure")
                    return {"status": "completed"}
                with (
                    mock.patch.dict(os.environ, env),
                    mock.patch.object(webapp, "_run_quote_job", side_effect=run_with_owned_scratch),
                ):
                    if outcome == "failure":
                        with self.assertRaises(RuntimeError):
                            webapp.run_quote_job({}, output_root, tmp_root, job_id)
                    else:
                        self.assertEqual(webapp.run_quote_job({}, output_root, tmp_root, job_id)["status"], "completed")
                self.assertFalse(job_tmp.exists())
                self.assertFalse(output_dir.exists())

    def test_cleanup_failure_does_not_rewrite_committed_publication(self):
        cleanup_error = webapp.SqagStorageAccessError(
            webapp.QUOTE_ARTIFACT_STORAGE_UNAVAILABLE_MESSAGE,
            status=503,
            reason="object_artifact_staging_cleanup_failed",
        )
        with writable_test_root() as root:
            def committed_with_owned_scratch(*_args, scratch_ownership=None, **_kwargs):
                scratch_ownership.update({"job_tmp": True, "output_dir": True})
                return {
                    "status": "completed",
                    "_durable_publication_committed": True,
                }

            with (
                mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}),
                mock.patch.object(
                    webapp,
                    "_run_quote_job",
                    side_effect=committed_with_owned_scratch,
                ) as runner,
                mock.patch.object(
                    webapp,
                    "cleanup_object_mode_job_scratch",
                    side_effect=cleanup_error,
                ),
                mock.patch.object(webapp, "write_local_log") as logger,
            ):
                result = webapp.run_quote_job(
                    {}, root / "output", root / "tmp", "job-committed-cleanup"
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["cleanup_warning"]["status"], "pending_maintenance")
            self.assertNotIn("_durable_publication_committed", result)
            runner.assert_called_once()
            logger.assert_called_once()
            self.assertEqual(
                logger.call_args.args[1]["terminal_outcome"],
                "durable_publication_committed",
            )
            self.assertNotIn(str(root), json.dumps(logger.call_args.args[1]))

            def uncommitted_with_owned_scratch(*_args, scratch_ownership=None, **_kwargs):
                scratch_ownership.update({"job_tmp": True, "output_dir": True})
                return {"status": "completed"}

            with (
                mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}),
                mock.patch.object(webapp, "_run_quote_job", side_effect=uncommitted_with_owned_scratch),
                mock.patch.object(
                    webapp,
                    "cleanup_object_mode_job_scratch",
                    side_effect=cleanup_error,
                ),
            ):
                with self.assertRaises(webapp.SqagStorageAccessError) as precommit:
                    webapp.run_quote_job(
                        {}, root / "output", root / "tmp", "job-uncommitted-cleanup"
                    )
            self.assertEqual(precommit.exception.reason, "object_artifact_staging_cleanup_failed")

    def test_idempotent_replay_never_cleans_another_invocation_scratch(self):
        with writable_test_root() as root:
            tmp_root = root / "tmp"
            output_root = root / "output"
            job_id = "job-concurrent-replay"
            original_started = webapp.threading.Event()
            allow_original_finish = webapp.threading.Event()
            original_saw_intact_scratch = webapp.threading.Event()
            results = {}
            calls = 0
            calls_lock = webapp.threading.Lock()

            def concurrent_runner(*_args, scratch_ownership=None, **_kwargs):
                nonlocal calls
                with calls_lock:
                    calls += 1
                    invocation = calls
                if invocation != 1:
                    return {
                        "job_id": job_id,
                        "status": "blocked",
                        "idempotent_replay": True,
                    }
                job_tmp = tmp_root / job_id
                output_dir = output_root / job_id
                job_tmp.mkdir(parents=True)
                output_dir.mkdir(parents=True)
                (job_tmp / "brief.json").write_text("synthetic", encoding="utf-8")
                (output_dir / "quotation.xlsx").write_bytes(b"synthetic")
                scratch_ownership.update({"job_tmp": True, "output_dir": True})
                original_started.set()
                self.assertTrue(allow_original_finish.wait(5))
                if (job_tmp / "brief.json").is_file() and (output_dir / "quotation.xlsx").is_file():
                    original_saw_intact_scratch.set()
                return {
                    "job_id": job_id,
                    "status": "completed",
                    "_durable_publication_committed": True,
                }

            def run_original():
                results["original"] = webapp.run_quote_job(
                    {}, output_root, tmp_root, job_id,
                )

            with (
                mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}),
                mock.patch.object(webapp, "_run_quote_job", side_effect=concurrent_runner),
            ):
                thread = webapp.threading.Thread(target=run_original)
                thread.start()
                self.assertTrue(original_started.wait(5))
                replay = webapp.run_quote_job({}, output_root, tmp_root, job_id)
                self.assertTrue((tmp_root / job_id / "brief.json").is_file())
                self.assertTrue((output_root / job_id / "quotation.xlsx").is_file())
                allow_original_finish.set()
                thread.join(timeout=5)

                self.assertFalse(thread.is_alive())
                self.assertTrue(replay["idempotent_replay"])
                self.assertTrue(original_saw_intact_scratch.is_set())
                self.assertEqual(results["original"]["status"], "completed")
                self.assertFalse((tmp_root / job_id).exists())
                self.assertFalse((output_root / job_id).exists())

                unrelated_tmp = tmp_root / job_id
                unrelated_output = output_root / job_id
                unrelated_tmp.mkdir(parents=True)
                unrelated_output.mkdir(parents=True)
                (unrelated_tmp / "unrelated").write_text("keep", encoding="utf-8")
                (unrelated_output / "unrelated").write_text("keep", encoding="utf-8")
                replay_after_completion = webapp.run_quote_job(
                    {}, output_root, tmp_root, job_id,
                )
                self.assertTrue(replay_after_completion["idempotent_replay"])
                self.assertTrue((unrelated_tmp / "unrelated").is_file())
                self.assertTrue((unrelated_output / "unrelated").is_file())

            self.assertEqual(calls, 3)

    def test_cleanup_attempts_both_scratch_directories_before_failing(self):
        with writable_test_root() as root:
            tmp_root = root / "tmp"
            output_root = root / "output"
            job_tmp = tmp_root / "job-partial-cleanup"
            output_dir = output_root / "job-partial-cleanup"
            job_tmp.mkdir(parents=True)
            output_dir.mkdir(parents=True)
            with (
                mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}),
                mock.patch.object(
                    webapp.shutil,
                    "rmtree",
                    side_effect=[OSError("locked"), None],
                ) as remove,
            ):
                with self.assertRaises(webapp.SqagStorageAccessError):
                    webapp.cleanup_object_mode_job_scratch(
                        job_tmp, output_dir, tmp_root, output_root,
                    )
            self.assertEqual(remove.call_count, 2)

    def test_cleanup_removes_only_explicitly_owned_scratch(self):
        with writable_test_root() as root:
            tmp_root = root / "tmp"
            output_root = root / "output"
            job_tmp = tmp_root / "job-owned-paths"
            output_dir = output_root / "job-owned-paths"
            job_tmp.mkdir(parents=True)
            output_dir.mkdir(parents=True)
            (job_tmp / "other-invocation").write_text("keep", encoding="utf-8")
            (output_dir / "owned").write_text("remove", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}):
                webapp.cleanup_object_mode_job_scratch(
                    job_tmp,
                    output_dir,
                    tmp_root,
                    output_root,
                    owns_job_tmp=False,
                    owns_output_dir=True,
                )
            self.assertTrue((job_tmp / "other-invocation").is_file())
            self.assertFalse(output_dir.exists())

    def test_deploy_pdf_page_debug_images_do_not_write_local_files(self):
        with writable_test_root() as tmp_root:
            images = [{"page": 1, "renderer": "synthetic", "data_url": "data:image/png;base64," + base64.b64encode(b"image").decode("ascii")}]
            with (
                mock.patch.dict(os.environ, {"APP_MODE": "deploy", "QUOTE_TMP_ROOT": str(tmp_root)}),
                mock.patch.object(webapp, "configured_tmp_root", return_value=tmp_root),
            ):
                self.assertEqual(webapp.persist_pdf_page_debug_images(images, "reference.pdf", "a" * 64), images)
            self.assertEqual(list(tmp_root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
