"""First object-mode PDF completion uses durable bytes after owned scratch cleanup."""
import copy
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_webapp as existing


webapp = existing.webapp


class ObjectPdfCompletionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = existing.WebappServerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_object_review_without_workbook_keeps_pricing_result_and_skips_publication(self):
        import io
        import sys
        from scripts import generate_quote as generator

        backend = webapp.InMemoryObjectStorageBackend()
        auth_session = self.fixture.platform_auth_session("workspace-object-review")
        session_id, job_id = "quote-object-review", "job-object-review"
        with tempfile.TemporaryDirectory(dir=existing.test_temp_root()) as temporary:
            root = Path(temporary)
            database_url = f"sqlite:///{(root / 'storage.sqlite3').as_posix()}"
            output_root, tmp_root = root / "output", root / "scratch"
            env = existing.isolated_env(**{
                **self.fixture.platform_launch_env(),
                **self.fixture.hosted_storage_env(SQAG_DATABASE_URL=database_url),
                "QUOTE_DATA_ROOT": str(root / "data"),
                "QUOTE_OUTPUT_ROOT": str(output_root),
                "QUOTE_TMP_ROOT": str(tmp_root),
                "QUOTE_LOG_ROOT": str(existing.ROOT / "_logs" / "app" / "object-review" / root.name),
            })
            payload = self.fixture._run466_payload(
                session_id, "Synthetic catalogue review", 0, quote_generated=False
            )
            generated = []

            def real_generator(command, **kwargs):
                captured = io.StringIO()
                with mock.patch.object(sys, "argv", ["generate_quote.py", *command[2:]]), \
                     mock.patch("sys.stdout", captured):
                    code = generator.main()
                generated.append(code)
                return subprocess.CompletedProcess(command, code, stdout=captured.getvalue(), stderr="")

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(webapp, "read_dotenv_value", side_effect=existing.env_only_dotenv_value),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
                mock.patch.object(webapp, "validated_platform_auth_session", side_effect=lambda session: session),
                mock.patch.object(webapp.urllib.request, "urlopen", side_effect=AssertionError("EXTERNAL_NETWORK_DENIED")),
                mock.patch.object(webapp.subprocess, "run", side_effect=real_generator),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.app_storage_for_auth_session(auth_session)
                existing.bind_database_publication_authority(storage, payload, suffix="object-review")
                storage.create_or_update_quote_session(copy.deepcopy(payload))
                with mock.patch.object(webapp.DatabaseSqagStorage, "_stage_versioned_quote_publication", wraps=storage._stage_versioned_quote_publication) as stage:
                    result = webapp.run_quote_job(copy.deepcopy(payload), output_root=output_root,
                        tmp_root=tmp_root, job_id=job_id, auth_session=auth_session)
                self.assertEqual(generated, [2])
                self.assertEqual((result["status"], result["return_code"]), ("needs_confirmation", 2), result)
                stage.assert_not_called()
                self.assertFalse(result.get("files"))
                self.assertFalse(result.get("error_reference"))
                self.assertTrue(result["output_rows"]["rows"])
                self.assertTrue(all(row["status"] == "unmatched" for row in result["output_rows"]["rows"]))
                self.assertIsNone(storage.quote_session_export_artifact(session_id, "xlsx"))
                self.assertFalse((output_root / job_id).exists())
                self.assertFalse((tmp_root / job_id).exists())
                with storage.connection() as connection:
                    count = connection.execute("select count(*) as n from sqag_quote_publication_versions where workspace_id = ? and session_id = ?", (storage.workspace_id, session_id)).fetchone()["n"]
                self.assertEqual(count, 0)
                # A claimed completion must still be rejected if no XLSX exists.
                with self.assertRaises(webapp.SqagStorageAccessError) as missing:
                    storage.create_or_update_quote_session(copy.deepcopy(payload),
                        result={"status": "completed", "files": []}, output_dir=root / "absent",
                        publish=False, generation_run_id=result["generation_run_id"], generation_job_id=job_id)
                self.assertEqual(missing.exception.reason, "object_artifact_storage_unavailable")

                # An explicit operator price resolves review without borrowing a
                # catalogue grant; the real generator and object publication run.
                corrected = copy.deepcopy(payload)
                row = corrected["line_items"][0]
                row.update(source_basis_line_id="operator-reviewed-row", pricing_keyword="",
                    price_mode="Priced", unit_price_override=25)
                row["pricing_authority"] = webapp.build_pricing_authority(
                    "manual", row, price=25,
                    reference_authority=webapp.exact_pricing_reference_authority(corrected, auth_session=auth_session),
                )
                corrected["quote_session"]["draft_state"]["outputRows"] = [copy.deepcopy(row)]
                storage.create_or_update_quote_session(copy.deepcopy(corrected))
                accepted = webapp.run_quote_job(corrected, output_root=output_root,
                    tmp_root=tmp_root, job_id="job-object-reviewed", auth_session=auth_session)
                self.assertEqual(generated, [2, 0])
                self.assertEqual((accepted["status"], accepted["return_code"]), ("completed", 0), accepted)
                artifact = storage.quote_session_export_artifact(session_id, "xlsx")
                self.assertIsNotNone(artifact)
                import zipfile
                with zipfile.ZipFile(io.BytesIO(artifact["content"])) as workbook:
                    self.assertIsNone(workbook.testzip())
                self.assertEqual(hashlib.sha256(artifact["content"]).hexdigest(), artifact["sha256"])
                self.assertFalse((output_root / "job-object-reviewed").exists())
                self.assertFalse((tmp_root / "job-object-reviewed").exists())

    def test_first_pdf_object_completion_remains_durable_after_owned_scratch_cleanup(self):
        backend = webapp.InMemoryObjectStorageBackend()
        auth_session = self.fixture.platform_auth_session("workspace-first-object-pdf")
        session_id = "quote-first-object-pdf"
        job_id = "job-first-object-pdf"
        xlsx_bytes = b"PK\x03\x04synthetic-first-object-workbook"
        pdf_bytes = b"%PDF-1.4\nsynthetic first object PDF\n%%EOF\n"
        dispatched = []

        with tempfile.TemporaryDirectory(dir=existing.test_temp_root()) as temporary:
            root = Path(temporary)
            output_root, tmp_root = root / "output", root / "scratch"
            database_url = f"sqlite:///{(root / 'storage.sqlite3').as_posix()}"
            env = existing.isolated_env(**{
                **self.fixture.platform_launch_env(),
                **self.fixture.hosted_storage_env(SQAG_DATABASE_URL=database_url),
                "QUOTE_DATA_ROOT": str(root / "data"),
                "QUOTE_OUTPUT_ROOT": str(output_root),
                "QUOTE_TMP_ROOT": str(tmp_root),
                "QUOTE_LOG_ROOT": str(existing.ROOT / "_logs" / "app" / "first-object-pdf" / root.name),
            })
            payload = self.fixture._run466_payload(
                session_id, "Synthetic first PDF surface", 0, quote_generated=False
            )
            output_dir, job_tmp = output_root / job_id, tmp_root / job_id

            def synthetic_generator(command, **kwargs):
                existing.write_generator_diagnostic_fixture(command)
                self.assertEqual(Path(command[1]), webapp.GENERATOR_PATH)
                self.assertEqual(command[command.index("--pdf-mode") + 1], "workbook")
                self.assertEqual(Path(command[command.index("--out") + 1]), output_dir)
                self.assertEqual(webapp.get_job(job_id, auth_session)["status"], "running")
                self.assertTrue(job_tmp.is_dir())
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "quotation.xlsx").write_bytes(xlsx_bytes)
                (output_dir / "quotation.pdf").write_bytes(pdf_bytes)
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.dict(webapp.JOBS, {}, clear=True),
                mock.patch.object(webapp, "read_dotenv_value", side_effect=existing.env_only_dotenv_value),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
                mock.patch.object(webapp, "validated_platform_auth_session", side_effect=lambda session: session),
                mock.patch.object(webapp.urllib.request, "urlopen", side_effect=AssertionError("EXTERNAL_NETWORK_DENIED")),
                mock.patch.object(webapp.subprocess, "run", side_effect=synthetic_generator) as generator,
            ):
                self.assertEqual(webapp.configured_artifact_storage_mode(), "object")
                self.assertIs(webapp.configured_object_storage_backend(), backend)
                webapp.apply_sqag_storage_migrations(database_url)
                storage = webapp.app_storage_for_auth_session(auth_session)
                existing.bind_database_publication_authority(storage, payload, suffix="first-object-pdf")
                initial = storage.create_or_update_quote_session(copy.deepcopy(payload))
                self.assertFalse(initial["exports"]["pdf"]["exists"])
                self.assertIsNone(storage.quote_session_export_artifact(session_id, "pdf"))

                # Control only scheduling; admission, worker and job-state functions run unchanged.
                with mock.patch.object(webapp.threading, "Thread") as thread:
                    queued = webapp.create_job(
                        "generate_pdf", payload, auth_session=auth_session, requested_job_id=job_id
                    )
                    self.assertEqual(queued["status"], "running", queued)
                    thread.return_value.start.assert_called_once_with()
                    dispatched.append(thread.call_args.kwargs)
                self.assertEqual(len(dispatched), 1)
                self.assertIs(dispatched[0]["target"], webapp.run_job_worker)
                self.assertIs(dispatched[0]["args"][0], webapp.finish_generate_pdf_job)
                publication_admitted = False
                pdf_reads_after_admission = []
                original_publish = webapp.DatabaseSqagStorage.publish_quote_session_forensic_transaction
                original_resolve = webapp.DatabaseSqagStorage.quote_session_export_artifact
                def publish(storage, *args, **kwargs):
                    nonlocal publication_admitted
                    metadata = original_publish(storage, *args, **kwargs)
                    publication_admitted = True
                    return metadata
                def resolve(storage, session_id, kind, **kwargs):
                    if publication_admitted and kind == "pdf":
                        pdf_reads_after_admission.append(kind)
                    return original_resolve(storage, session_id, kind, **kwargs)
                with mock.patch.object(webapp.DatabaseSqagStorage, "publish_quote_session_forensic_transaction", new=publish), mock.patch.object(webapp.DatabaseSqagStorage, "quote_session_export_artifact", new=resolve):
                    dispatched[0]["target"](*dispatched[0]["args"])
                    self.assertTrue(publication_admitted)
                    self.assertEqual(pdf_reads_after_admission, [])
                    immediate = copy.deepcopy(webapp.JOBS[job_id]["result"])
                    self.assertTrue(immediate["quote_session"]["exports"]["pdf"]["view_url"])
                    self.assertEqual(immediate["quote_session"]["exports"]["pdf"]["filename"], "quotation.pdf")
                    self.assertTrue(immediate["quote_session"]["exports"]["pdf"]["verified"])
                    completed = webapp.get_job(job_id, auth_session)
                    self.assertEqual(pdf_reads_after_admission, ["pdf"])
                self.assertEqual(completed["status"], "completed", completed)
                generator.assert_called_once()
                result = completed["result"]
                self.assertEqual(result["status"], "completed", result)
                pdf_url = result["quote_session"]["exports"]["pdf"]["url"]
                self.assertRegex(pdf_url, rf"^/api/quote-sessions/{session_id}/download/pdf\?publication=[0-9a-f]{{64}}$")
                pdf_view_url = result["quote_session"]["exports"]["pdf"]["view_url"]
                self.assertEqual(pdf_view_url, pdf_url.replace("/download/pdf?", "/view/pdf?"))
                pdf_files = [item for item in result["files"] if item["name"] == "quotation.pdf"]
                self.assertEqual(len(pdf_files), 1)
                self.assertEqual(pdf_files[0]["url"], pdf_url)
                self.assertEqual(pdf_files[0]["sha256"], hashlib.sha256(pdf_bytes).hexdigest())
                self.assertEqual(int(pdf_files[0]["bytes"]), len(pdf_bytes))
                self.assertNotIn("/api/jobs/", str(result["files"]))
                exported = result["quote_session"]["exports"]["pdf"]
                self.assertTrue(exported["exists"])
                self.assertFalse(exported["stale"])
                self.assertEqual(exported["url"], pdf_url)
                self.assertEqual(exported["sha256"], hashlib.sha256(pdf_bytes).hexdigest())
                self.assertEqual(exported["size_bytes"], len(pdf_bytes))
                self.assertFalse(output_dir.exists())
                self.assertFalse(job_tmp.exists())

                reopened_storage = webapp.app_storage_for_auth_session(auth_session)
                reopened = reopened_storage.get_quote_session(session_id)
                self.assertTrue(reopened["exports"]["pdf"]["exists"])
                self.assertFalse(reopened["exports"]["pdf"]["stale"])
                self.assertEqual(reopened["exports"]["pdf"]["view_url"], pdf_view_url)
                self.assertEqual(pdf_files[0]["view_url"], pdf_view_url)
                artifact = reopened_storage.quote_session_export_artifact(session_id, "pdf")
                self.assertEqual(artifact["content"], pdf_bytes)
                with reopened_storage.connection() as connection:
                    object_row = connection.execute(
                        "select * from sqag_object_artifacts where workspace_id = ? "
                        "and owner_type = ? and owner_id = ? and artifact_kind = ? "
                        "and status = ? and retention_status = ? and deleted_at is null",
                        (reopened_storage.workspace_id, "generated_quote_version",
                         completed["generation_run_id"], "pdf", "active", "active"),
                    ).fetchone()
                self.assertIsNotNone(object_row)
                object_metadata = reopened_storage._object_metadata_from_row(object_row)
                self.assertEqual(object_metadata.storage_key, object_row["object_key_ref"])
                self.assertTrue(object_metadata.storage_key)
                self.assertEqual(object_metadata.checksum_sha256, hashlib.sha256(pdf_bytes).hexdigest())
                self.assertEqual(object_metadata.size_bytes, len(pdf_bytes))
                self.assertTrue(backend.verify_metadata(object_metadata, workspace_id=reopened_storage.workspace_id))
                self.assertEqual(
                    backend.retrieve_artifact(object_metadata, workspace_id=reopened_storage.workspace_id),
                    pdf_bytes,
                )
                cookie = f"{webapp.SESSION_COOKIE_NAME}={webapp.signed_cookie_value(auth_session)}"
                with existing.LocalRunnerServer() as runner:
                    response = existing.local_http_get_bytes(runner, pdf_url, headers={"Cookie": cookie})
                    inline_response = existing.local_http_get_bytes(runner, pdf_view_url, headers={"Cookie": cookie})
                    unauthenticated = existing.local_http_get_bytes(runner, pdf_url)
                self.assertEqual(response, (200, pdf_bytes))
                self.assertEqual(inline_response, (200, pdf_bytes))
                self.assertIn(unauthenticated[0], {401, 403})
                self.assertFalse(output_dir.exists())
                self.assertFalse(job_tmp.exists())


if __name__ == "__main__":
    unittest.main()
