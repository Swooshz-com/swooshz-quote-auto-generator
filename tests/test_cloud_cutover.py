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
            "launchTokenExpiresAt": "2999-01-01T00:00:00.000Z",
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
                side_effect = None if outcome == "success" else RuntimeError("synthetic failure")
                with (
                    mock.patch.dict(os.environ, env),
                    mock.patch.object(webapp, "_run_quote_job", return_value={"status": "completed"}, side_effect=side_effect),
                ):
                    if side_effect:
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
            with (
                mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}),
                mock.patch.object(
                    webapp,
                    "_run_quote_job",
                    return_value={
                        "status": "completed",
                        "_durable_publication_committed": True,
                    },
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

            with (
                mock.patch.dict(os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}),
                mock.patch.object(webapp, "_run_quote_job", return_value={"status": "completed"}),
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
