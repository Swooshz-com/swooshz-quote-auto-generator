"""Shared publication journeys; SQL always runs against a real database engine.

SQLite is the offline control only. The PostgreSQL suite supplies the migrated,
disposable CI database and authenticates as sqag_runtime, never a SQL recorder.
"""
import copy
import hashlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from unittest import mock

from tests import test_webapp as existing
from tests import test_object_storage_provider_config as provider
from webapp import object_storage

s = existing.webapp


class BoundedS3Client(provider.FakeS3Client):
    """The SDK boundary accepts only the adapter's known request shapes."""

    def put_object(self, **kwargs):
        if set(kwargs) != {"Bucket", "Key", "Body", "ContentType", "Metadata"}:
            raise AssertionError("Unexpected S3 upload request")
        if len(kwargs["Body"]) > 4 * 1024 * 1024:
            raise AssertionError("Synthetic object exceeds fixture bound")
        return super().put_object(**kwargs)

    def head_object(self, **kwargs):
        self._check_key_request(kwargs)
        return super().head_object(**kwargs)

    def get_object(self, **kwargs):
        self._check_key_request(kwargs)
        return super().get_object(**kwargs)

    def delete_object(self, **kwargs):
        self._check_key_request(kwargs)
        return super().delete_object(**kwargs)

    @staticmethod
    def _check_key_request(kwargs):
        if set(kwargs) != {"Bucket", "Key"}:
            raise AssertionError("Unexpected S3 object request")


class PublicationStorageCases:
    """Same assertions for both object adapters and both database engines."""

    @contextmanager
    def case(self, adapter):
        fixture = existing.WebappServerTest()
        fixture.setUp()
        with ExitStack() as stack:
            stack.callback(fixture.doCleanups)
            self.root = Path(stack.enter_context(tempfile.TemporaryDirectory(
                prefix="pub-", dir=existing.test_temp_root())))
            self.fixture = fixture
            self.session_id = "quote-publication-" + adapter
            # Subcases share the CI database, so each gets a fresh workspace.
            self.auth = fixture.platform_auth_session("workspace-publication-" + self.root.name)
            self.client = BoundedS3Client() if adapter == "s3" else None
            self.backend = (object_storage.S3CompatibleObjectStorageBackend(
                bucket="synthetic-publication", client=self.client) if self.client
                else s.InMemoryObjectStorageBackend())
            database_url = self.database_url or "sqlite:///" + (self.root / "db.sqlite3").as_posix()
            env = existing.isolated_env(**{
                **fixture.platform_launch_env(),
                **fixture.hosted_storage_env(SQAG_DATABASE_URL=database_url),
                "QUOTE_DATA_ROOT": str(self.root / "data"),
                "QUOTE_OUTPUT_ROOT": str(self.root / "out"),
                "QUOTE_TMP_ROOT": str(self.root / "scratch"),
                "QUOTE_LOG_ROOT": str(existing.ROOT / "_logs" / "app" / "publication-storage" / self.root.name),
            })
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            stack.enter_context(mock.patch.dict(s.JOBS, {}, clear=True))
            stack.enter_context(mock.patch.object(s, "read_dotenv_value", side_effect=existing.env_only_dotenv_value))
            stack.enter_context(mock.patch.object(s, "configured_object_storage_backend", return_value=self.backend))
            stack.enter_context(mock.patch.object(s, "validated_platform_auth_session", side_effect=lambda value: value))
            stack.enter_context(mock.patch.object(s.urllib.request, "urlopen", side_effect=AssertionError("External transport denied")))
            if self.database_url is None:
                s.apply_sqag_storage_migrations(database_url)
            self.storage = s.app_storage_for_auth_session(self.auth)
            if self.database_url:
                self.assertEqual(self.storage.database_family, "postgres_compatible")
                with self.storage.connection() as connection:
                    row = connection.execute("select session_user, current_user, current_setting('server_version_num') as version").fetchone()
                self.assertEqual((row["session_user"], row["current_user"]), ("sqag_runtime", "sqag_runtime"))
                self.assertEqual(int(row["version"]) // 10000, 17)
            self.payload = fixture._run466_payload(self.session_id, "Synthetic publication row", 0, quote_generated=False)
            existing.bind_database_publication_authority(self.storage, self.payload, suffix=adapter)
            self.storage.create_or_update_quote_session(copy.deepcopy(self.payload))
            self.outputs = {}
            self.generator = stack.enter_context(mock.patch.object(s.subprocess, "run", side_effect=fixture._run466_fake_generator(self.outputs)))
            # Reuse the HTTP harness, binding only within this lane's port range.
            http_server = s.ThreadingHTTPServer
            def bound_server(address, handler):
                for port in range(18830, 18840):
                    try:
                        return http_server(("127.0.0.1", port), handler)
                    except OSError as error:
                        if error.errno not in {48, 98, 10048}:
                            raise
                raise AssertionError("Publication test port range is occupied")
            with mock.patch.object(s, "ThreadingHTTPServer", side_effect=bound_server):
                self.runner = stack.enter_context(existing.LocalRunnerServer())
            yield

    def reopen(self):
        return s.app_storage_for_auth_session(self.auth)

    def metadata(self):
        return self.reopen()._read_quote_session_metadata(self.session_id)[0]

    def request(self, url, auth=True):
        headers = {"Cookie": f"{s.SESSION_COOKIE_NAME}={s.signed_cookie_value(self.auth)}"} if auth is True else None
        if isinstance(auth, dict):
            headers = {"Cookie": f"{s.SESSION_COOKIE_NAME}={s.signed_cookie_value(auth)}"}
        return existing.local_http_get_bytes(self.runner, url, headers=headers)

    def attempt(self, suffix, *, pdf=True, real_generator=False):
        job_id = "job-publication-" + suffix
        content = {"xlsx": ("PK-synthetic-workbook-" + suffix).encode(),
                   "pdf": b"%PDF-1.4\n% Synthetic publication PDF\n%%EOF\n"}
        self.outputs[job_id] = (content["xlsx"], content["pdf"] if pdf else None)
        with mock.patch.object(s.threading, "Thread") as thread:
            queued = s.create_job("generate_pdf" if pdf else "generate", copy.deepcopy(self.payload),
                auth_session=self.auth, requested_job_id=job_id)
            self.assertEqual(queued["status"], "running", queued)
            thread.return_value.start.assert_called_once_with()
            dispatch = thread.call_args.kwargs
        with ExitStack() as stack:
            if real_generator:
                from scripts import generate_quote
                def generate(command, **kwargs):
                    captured = io.StringIO()
                    with mock.patch.object(sys, "argv", ["generate_quote.py", *command[2:]]), mock.patch("sys.stdout", captured):
                        code = generate_quote.main()
                    path = self.root / "out" / job_id / "quotation.xlsx"
                    if path.exists():
                        content["xlsx"] = path.read_bytes()
                    return subprocess.CompletedProcess(command, code, stdout=captured.getvalue(), stderr="")
                stack.enter_context(mock.patch.object(s.subprocess, "run", side_effect=generate))
            dispatch["target"](*dispatch["args"])
        return job_id, s.get_job(job_id, self.auth), content

    def assert_downloads(self, result, content, *, pdf=True, projection_warning=False):
        self.assertEqual(result["status"], "completed", result)
        metadata = self.metadata()
        reopened = self.reopen().get_quote_session(self.session_id)
        self.assertEqual(metadata["publication"]["run_id"], result["generation_run_id"])
        for kind in (("xlsx", "pdf") if pdf else ("xlsx",)):
            exported = reopened["exports"][kind]
            expected_url = existing.expected_publication_url(metadata, kind, session_id=self.session_id,
                workspace_id=self.storage.workspace_id, owner_id=self.storage.user_id)
            self.assertEqual(exported["url"], expected_url)
            if projection_warning:
                self.assertIsNone(result["result"]["quote_session"]["exports"][kind]["url"])
                committed_file = next(item for item in result["result"]["committed_files"] if item["name"] == "quotation." + kind)
                self.assertEqual(committed_file["url"], expected_url)
            else:
                self.assertEqual(result["result"]["quote_session"]["exports"][kind]["url"], expected_url)
            self.assertEqual(self.request(expected_url), (200, content[kind]))
            self.assertIn(self.request(expected_url, auth=False)[0], {401, 403})
            outsider = self.fixture.platform_auth_session("workspace-publication-other")
            self.assertEqual(self.request(expected_url, auth=outsider)[0], 404)
            artifact = self.reopen().quote_session_export_artifact(self.session_id, kind)
            self.assertEqual(artifact["content"], content[kind])
            self.assertEqual(exported["sha256"], hashlib.sha256(content[kind]).hexdigest())
            self.assertEqual(exported["size_bytes"], len(content[kind]))
        if pdf:
            self.assertEqual(self.request(reopened["exports"]["pdf"]["view_url"]), (200, content["pdf"]))
        self.assertFalse((self.root / "out" / result["job_id"]).exists())
        self.assertFalse((self.root / "scratch" / result["job_id"]).exists())

    def test_first_pdf_after_real_generated_xlsx_survives_reopen(self):
        for adapter in ("memory", "s3"):
            with self.subTest(adapter=adapter), self.case(adapter):
                row = self.payload["line_items"][0]
                row.update(source_basis_line_id="operator-row", pricing_keyword="", price_mode="Priced", unit_price_override=25)
                row["pricing_authority"] = s.build_pricing_authority("manual", row, price=25,
                    reference_authority=s.exact_pricing_reference_authority(self.payload, auth_session=self.auth))
                self.payload["quote_session"]["draft_state"]["outputRows"] = [copy.deepcopy(row)]
                self.storage.create_or_update_quote_session(copy.deepcopy(self.payload))
                first_id, first, first_bytes = self.attempt("xlsx", pdf=False, real_generator=True)
                self.assert_downloads(first, first_bytes, pdf=False)
                with zipfile.ZipFile(io.BytesIO(first_bytes["xlsx"])) as workbook:
                    self.assertIsNone(workbook.testzip())
                    self.assertIn("xl/workbook.xml", workbook.namelist())
                self.assertIsNone(self.reopen().get_quote_session(self.session_id)["exports"]["pdf"]["url"])
                self.assertIsNone(self.reopen().quote_session_export_artifact(self.session_id, "pdf"))
                old_url = first["result"]["quote_session"]["exports"]["xlsx"]["url"]
                self.outputs.clear()
                second_id, second, second_bytes = self.attempt("first-pdf")
                self.assertNotEqual(first_id, second_id)
                self.assert_downloads(second, second_bytes)
                self.assertEqual(self.request(old_url)[0], 404)

    def test_precommit_failures_keep_previous_publication(self):
        for adapter in ("memory", "s3"):
            for failure in ("missing_staging", "invalid_metadata", "upload", "commit", "publication_commit"):
                with self.subTest(adapter=adapter, failure=failure), self.case(adapter):
                    _, prior, content = self.attempt("prior")
                    self.assert_downloads(prior, content)
                    prior_publication = copy.deepcopy(self.metadata()["publication"])
                    injected = []
                    with ExitStack() as stack:
                        if failure == "missing_staging":
                            original = s.DatabaseSqagStorage._stage_versioned_quote_publication
                            def missing(storage, payload, result, output_dir, *args, **kwargs):
                                injected.append(True)
                                (output_dir / "quotation.xlsx").unlink()
                                return original(storage, payload, result, output_dir, *args, **kwargs)
                            stack.enter_context(mock.patch.object(s.DatabaseSqagStorage, "_stage_versioned_quote_publication", new=missing))
                        elif failure in {"upload", "invalid_metadata"}:
                            original = self.backend.store_artifact
                            def upload(**kwargs):
                                if kwargs["owner_type"] == "generated_quote_version" and kwargs["artifact_kind"] == "pdf":
                                    injected.append(True)
                                    if failure == "upload":
                                        raise s.ObjectStorageContractError("Synthetic upload failure")
                                    return replace(original(**kwargs), size_bytes=1)
                                return original(**kwargs)
                            stack.enter_context(mock.patch.object(self.backend, "store_artifact", side_effect=upload))
                        else:
                            method = ("publish_quote_session_forensic_transaction" if failure == "publication_commit"
                                      else "_execute_stage_publication_version")
                            original = getattr(s.DatabaseSqagStorage, method)
                            def persist(storage, connection, *args, **kwargs):
                                result = original(storage, connection, *args, **kwargs)
                                injected.append(True)
                                # Actual SQL has executed; fail the following commit before
                                # it reaches the engine, keeping rollback/savepoints real.
                                original_commit = connection.commit
                                def fail_commit():
                                    connection.commit = original_commit
                                    raise s.ObjectStorageContractError("Synthetic commit failure")
                                connection.commit = fail_commit
                                return result
                            stack.enter_context(mock.patch.object(s.DatabaseSqagStorage, method, new=persist))
                        _, failed, _ = self.attempt("failed-" + failure)
                    self.assertTrue(injected)
                    self.assertEqual(failed["status"], "failed", failed)
                    self.assertFalse(failed["result"].get("files"))
                    for exported in failed["result"].get("quote_session", {}).get("exports", {}).values():
                        self.assertIsNone(exported.get("url"))
                        self.assertIsNone(exported.get("view_url"))
                    self.assertEqual(self.metadata()["publication"]["run_id"], prior_publication["run_id"])
                    with self.reopen().connection() as connection:
                        row = connection.execute("select count(*) as n from sqag_quote_publication_versions where workspace_id = ? and run_id = ? and state = ?",
                            (self.storage.workspace_id, failed["generation_run_id"], "published")).fetchone()
                    self.assertEqual(row["n"], 0)
                    self.assert_downloads(prior, content)

    def test_corrupt_object_body_and_remote_metadata_deny_download(self):
        for adapter in ("memory", "s3"):
            for damage in (("body", "remote_metadata") if adapter == "s3" else ("body",)):
                with self.subTest(adapter=adapter, damage=damage), self.case(adapter):
                    job_id, result, content = self.attempt("corrupt")
                    url = result["result"]["quote_session"]["exports"]["pdf"]["url"]
                    with self.storage.connection() as connection:
                        row = connection.execute("select * from sqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
                            (self.storage.workspace_id, "generated_quote_version", result["generation_run_id"], "pdf")).fetchone()
                    key = row["object_key_ref"]
                    if self.client:
                        remote = self.client.objects[(self.backend.bucket, key)]
                        if damage == "body":
                            remote["body"] = b"!" * len(content["pdf"])
                        else:
                            remote["metadata"]["sqag-checksum-sha256"] = "0" * 64
                    else:
                        self.backend._objects[key] = b"!" * len(content["pdf"])
                    self.assertEqual(self.request(url)[0], 404)
                    self.assertIsNone(self.reopen().get_quote_session(self.session_id)["exports"]["pdf"]["url"])
                    projected = s.get_job(job_id, self.auth)
                    self.assertIsNone(projected["result"]["quote_session"]["exports"]["pdf"]["url"])

    def test_saved_edit_and_catalogue_change_invalidate_publication(self):
        for adapter in ("memory", "s3"):
            for change in ("draft", "catalogue"):
                with self.subTest(adapter=adapter, change=change), self.case(adapter):
                    _, first, _ = self.attempt("stale")
                    if change == "draft":
                        draft = self.payload["quote_session"]["draft_state"]
                        draft["outputRevision"] += 1
                        draft["outputRows"][0]["description"] = "Synthetic changed row"
                        self.storage.create_or_update_quote_session(copy.deepcopy(self.payload))
                    else:
                        reference = self.storage.pricing_reference_detail(self.payload["pricing_reference_id"], source="company")
                        reference["items"][0]["internal_cost"] += 1
                        self.storage.save_pricing_reference(reference)
                    url = first["result"]["quote_session"]["exports"]["pdf"]["url"]
                    self.assertEqual(self.request(url)[0], 404)
                    self.assertIsNone(self.reopen().get_quote_session(self.session_id)["exports"]["pdf"]["url"])

    def test_postcommit_projection_failure_keeps_committed_bytes(self):
        for adapter in ("memory", "s3"):
            with self.subTest(adapter=adapter), self.case(adapter):
                committed = []
                publish = s.DatabaseSqagStorage.publish_quote_session_forensic_transaction
                project = s.DatabaseSqagStorage.get_quote_session
                stage = s.DatabaseSqagStorage._stage_versioned_quote_publication
                def publish_during_save(storage, *args, **kwargs):
                    # Exercise the public storage API's publish=True branch.
                    # The worker normally defers publication to forensic finish.
                    kwargs["publish"] = True
                    return stage(storage, *args, **kwargs)
                def publish_then_break(storage, *args, **kwargs):
                    result = publish(storage, *args, **kwargs)
                    committed.append(True)
                    return result
                def projection(storage, *args, **kwargs):
                    if committed:
                        raise OSError("Synthetic projection failure")
                    return project(storage, *args, **kwargs)
                with mock.patch.object(s.DatabaseSqagStorage, "_stage_versioned_quote_publication", new=publish_during_save), mock.patch.object(s.DatabaseSqagStorage, "publish_quote_session_forensic_transaction", new=publish_then_break), mock.patch.object(s.DatabaseSqagStorage, "get_quote_session", new=projection):
                    _, result, content = self.attempt("projection")
                self.assertTrue(committed)
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["result"]["post_commit_projection"]["code"], "post_commit_projection_failed")
                self.assert_downloads(result, content, projection_warning=True)

    def test_database_engine_rejects_unknown_sql(self):
        with self.case("memory"):
            if self.database_url:
                import psycopg
                database_error = psycopg.Error
            else:
                database_error = sqlite3.DatabaseError
            with self.assertRaises(database_error):
                self.storage._run_storage_transaction(lambda connection: connection.execute(
                    "select fixture_value from sqag_unknown_publication_fixture"))
            # A fresh independent connection still reads the saved draft after rollback.
            self.assertEqual(self.reopen().get_quote_session(self.session_id)["session_id"], self.session_id)
