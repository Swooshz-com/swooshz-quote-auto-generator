"""Current publication projections use verified bytes and immutable comparisons."""
import copy
import http.client
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

from tests import test_webapp as existing

s = existing.webapp


class PublicationProjectionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = existing.WebappServerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    @contextmanager
    def case(self, mode):
        with tempfile.TemporaryDirectory(prefix="qp-", dir=existing.test_temp_root()) as temporary, ExitStack() as stack:
            root = Path(temporary)
            self.mode, self.root = mode, root
            self.session_id = "quote-projection-" + mode
            self.workspace = "workspace-projection-" + mode
            self.auth = self.fixture.platform_auth_session(self.workspace) if mode != "local" else None
            self.backend = s.InMemoryObjectStorageBackend()
            self.outputs = {}
            self.payload = self.fixture._run466_payload(self.session_id, "Synthetic original row", 0, quote_generated=False)
            env = existing.isolated_env(
                QUOTE_DATA_ROOT=str(root / "data"), QUOTE_OUTPUT_ROOT=str(root / "out"),
                QUOTE_TMP_ROOT=str(root / "tmp"),
                QUOTE_LOG_ROOT=str(existing.ROOT / "_logs" / "app" / "publication-projection" / root.name),
            )
            if self.auth:
                env.update(self.fixture.platform_launch_env())
                env.update(self.fixture.hosted_storage_env(
                    SQAG_DATABASE_URL="sqlite:///" + (root / "db.sqlite3").as_posix(),
                    SQAG_ARTIFACT_STORAGE_MODE=mode,
                ))
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            stack.enter_context(mock.patch.dict(s.JOBS, {}, clear=True))
            stack.enter_context(mock.patch.object(s, "read_dotenv_value", side_effect=existing.env_only_dotenv_value))
            stack.enter_context(mock.patch.object(s, "configured_object_storage_backend", return_value=self.backend))
            stack.enter_context(mock.patch.object(s, "validated_platform_auth_session", side_effect=lambda value: value))
            stack.enter_context(mock.patch.object(s.urllib.request, "urlopen", side_effect=AssertionError("EXTERNAL_NETWORK_DENIED")))
            self.generator = stack.enter_context(mock.patch.object(s.subprocess, "run", side_effect=self.fixture._run466_fake_generator(self.outputs)))
            if self.auth:
                s.apply_sqag_storage_migrations(env["SQAG_DATABASE_URL"])
                self.storage = s.app_storage_for_auth_session(self.auth)
                existing.bind_database_publication_authority(self.storage, self.payload, suffix="projection")
            else:
                self.storage = s.LocalSqagStorage()
            self.storage.create_or_update_quote_session(copy.deepcopy(self.payload))
            self.runner = stack.enter_context(existing.LocalRunnerServer())
            yield

    def headers(self, auth=None):
        auth = self.auth if auth is None else auth
        return {"Cookie": f"{s.SESSION_COOKIE_NAME}={s.signed_cookie_value(auth)}"} if auth else None

    def request(self, path, *, auth=None, unauthenticated=False):
        return existing.local_http_get_bytes(self.runner, path, headers=None if unauthenticated else self.headers(auth))

    def post_generate(self, job_id):
        headers = self.headers() or {}
        headers.update({"Content-Type": "application/json", "Idempotency-Key": job_id})
        headers[s.configured_csrf_header_name()] = s.csrf_token_for_cookie_header(headers.get("Cookie", ""))
        connection = http.client.HTTPConnection(*self.runner.server.server_address[:2], timeout=3)
        try:
            connection.request("POST", "/api/generate", body=json.dumps(self.payload), headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def assert_public_privacy(self, body):
        for private in (b"_quote_publication_identity", b"_committed_quote_metadata", b"quote-publication-comparison-v1", b"commercial_state_digest", b"object_key_ref", b"content_blob"):
            self.assertNotIn(private, body)

    def metadata(self):
        return self.storage._read_quote_session_metadata(self.session_id)[0] if self.auth else s.read_quote_session_metadata(self.session_id)

    def publish(self, suffix="first", *, pdf=True):
        job_id = "job-projection-" + self.mode + "-" + suffix
        content = {"xlsx": ("PK-synthetic-xlsx-" + suffix).encode(), "pdf": ("%PDF-synthetic-" + suffix).encode()}
        self.outputs[job_id] = (content["xlsx"], content["pdf"] if pdf else None)
        with mock.patch.object(s.threading, "Thread") as thread:
            queued = s.create_job("generate_pdf", copy.deepcopy(self.payload), auth_session=self.auth, requested_job_id=job_id)
            self.assertEqual(queued["status"], "running", queued)
            thread.return_value.start.assert_called_once_with()
            call = thread.call_args.kwargs
        after_commit = False
        post_commit_pdf_reads = []
        original_finish = s.finish_generation_forensics
        storage_type = type(self.storage)
        original_resolve = storage_type.quote_session_export_artifact
        def finish(*args, **kwargs):
            nonlocal after_commit
            completed = original_finish(*args, **kwargs)
            if kwargs.get("publication_storage") is not None and completed.get("status") == "completed":
                after_commit = True
            return completed
        def resolve(storage, session_id, kind, **kwargs):
            if after_commit and kind == "pdf":
                post_commit_pdf_reads.append(kind)
            return original_resolve(storage, session_id, kind, **kwargs)
        with mock.patch.object(s, "finish_generation_forensics", side_effect=finish), mock.patch.object(storage_type, "quote_session_export_artifact", new=resolve):
            call["target"](*call["args"])
        if self.auth:
            self.assertTrue(after_commit)
            self.assertEqual(post_commit_pdf_reads, [])
        immediate = copy.deepcopy(s.JOBS[job_id]["result"])
        if pdf:
            exported = immediate["quote_session"]["exports"]["pdf"]
            self.assertEqual(exported["filename"], "quotation.pdf")
            self.assertTrue(exported["verified"])
            self.assertEqual(exported["view_url"], exported["url"].replace("/download/pdf?", "/view/pdf?"))
            self.assertEqual(next(item for item in immediate["files"] if item["name"] == "quotation.pdf")["view_url"], exported["view_url"])
        result = s.get_job(job_id, self.auth)
        self.assertEqual(result["status"], "completed", result)
        return job_id, result, content

    def links(self, result):
        exports = result["result"]["quote_session"]["exports"]
        return [exports["xlsx"]["url"], exports["pdf"]["url"], exports["pdf"]["view_url"]]

    def assert_no_pdf(self, result):
        self.assertEqual(result["status"], "completed")
        exported = result["result"]["quote_session"]["exports"]["pdf"]
        self.assertIsNone(exported["url"])
        self.assertIsNone(exported["view_url"])
        self.assertFalse(exported["verified"])
        self.assertNotIn("quotation.pdf", [item["name"] for item in result["result"].get("files", [])])

    def test_real_replacement_closes_both_kind_links_and_old_job_surfaces(self):
        for mode in ("local", "database", "object"):
            with self.subTest(mode=mode), self.case(mode):
                first_id, first, old_bytes = self.publish()
                old_urls = self.links(first)
                for url, kind in zip(old_urls, ("xlsx", "pdf", "pdf")):
                    self.assertRegex(url, r"\?publication=[0-9a-f]{64}$")
                    self.assertEqual(self.request(url), (200, old_bytes[kind]))
                stored_first = copy.deepcopy(s.JOBS[first_id])
                self.payload["quote_session"]["draft_state"]["outputRows"][0]["description"] = "Synthetic replacement row"
                self.payload["quote_session"]["draft_state"]["outputRevision"] = 1
                _second_id, second, new_bytes = self.publish("second-longer")
                for old_url in old_urls:
                    self.assertEqual(self.request(old_url)[0], 404)
                for url, kind in zip(self.links(second), ("xlsx", "pdf", "pdf")):
                    self.assertEqual(self.request(url), (200, new_bytes[kind]))
                    self.assertEqual(self.request(urlsplit(url).path), (200, new_bytes[kind]))
                with mock.patch.object(s.threading, "Thread") as worker, mock.patch.object(s, "begin_generation_forensics", side_effect=AssertionError("READ_MUST_NOT_WRITE")):
                    self.assert_no_pdf(s.get_job(first_id, self.auth))
                    self.assert_no_pdf(s.create_job("generate_pdf", copy.deepcopy(self.payload), auth_session=self.auth, requested_job_id=first_id))
                worker.assert_not_called()
                status, body = self.request("/api/jobs/" + first_id)
                self.assertEqual(status, 200)
                self.assert_no_pdf(json.loads(body))
                self.assertEqual(s.JOBS[first_id], stored_first)
                self.assertEqual(self.generator.call_count, 2)

    def test_saved_edit_and_missing_or_corrupt_body_clear_current_pdf_everywhere(self):
        for mode in ("local", "database", "object"):
            for failure in ("edit", "missing", "corrupt"):
                with self.subTest(mode=mode, failure=failure), self.case(mode):
                    job_id, first, content = self.publish()
                    before = copy.deepcopy(s.JOBS[job_id])
                    metadata = self.metadata()
                    if failure == "edit":
                        self.payload["quote_session"]["draft_state"]["outputRows"][0]["description"] = "Substantively edited row"
                        self.payload["quote_session"]["draft_state"]["outputRevision"] = 1
                        self.storage.create_or_update_quote_session(copy.deepcopy(self.payload))
                    elif mode == "local":
                        path = s.quote_session_recorded_export_path(self.session_id, "pdf", metadata)
                        path.unlink() if failure == "missing" else path.write_bytes(b"corrupt synthetic body")
                    elif mode == "database":
                        with self.storage.connection() as connection:
                            if failure == "missing":
                                connection.execute("delete from sqag_quote_publication_artifacts where run_id = ? and artifact_kind = 'pdf'", (first["generation_run_id"],))
                            else:
                                connection.execute("update sqag_quote_publication_artifacts set content_blob = ? where run_id = ? and artifact_kind = 'pdf'", (b"corrupt synthetic body", first["generation_run_id"]))
                            connection.commit()
                    else:
                        with self.storage.connection() as connection:
                            row = connection.execute("select * from sqag_object_artifacts where owner_type = 'generated_quote_version' and owner_id = ? and artifact_kind = 'pdf'", (first["generation_run_id"],)).fetchone()
                        artifact = self.storage._object_metadata_from_row(row)
                        if failure == "missing":
                            self.backend.delete_artifact(artifact, workspace_id=self.storage.workspace_id)
                        else:
                            # Existing backend contract rejects checksum disagreement on retrieval.
                            self.backend._objects[artifact.storage_key] = b"corrupt synthetic body"
                    saved = self.storage.get_quote_session(self.session_id)
                    self.assertIsNone(saved["exports"]["pdf"]["url"])
                    self.assertIsNone(saved["exports"]["pdf"]["view_url"])
                    self.assertEqual(saved["exports"]["pdf"]["availability"], "unverified")
                    self.assertIsNone(saved["exports"]["pdf"]["missing"])
                    if failure != "edit":
                        self.assertTrue(saved["exports"]["xlsx"]["exists"])
                    self.assert_no_pdf(s.get_job(job_id, self.auth))
                    self.assert_no_pdf(s.create_job("generate_pdf", copy.deepcopy(self.payload), auth_session=self.auth, requested_job_id=job_id))
                    for url in self.links(first)[1:]:
                        self.assertEqual(self.request(url)[0], 404)
                    self.assertEqual(s.JOBS[job_id], before)
                    self.generator.assert_called_once()

    def test_invalid_query_and_wrong_kind_deny_before_artifact_fetch(self):
        for mode in ("local", "database", "object"):
            with self.subTest(mode=mode), self.case(mode):
                _job_id, first, content = self.publish()
                xlsx, pdf, inline = self.links(first)
                correct = urlsplit(pdf).query.split("=", 1)[1]
                invalid = ("", "xyz", correct.upper(), "f" * 64)
                target = type(self.storage)
                with mock.patch.object(target, "quote_session_export_artifact", wraps=self.storage.quote_session_export_artifact) as resolver:
                    for query in ("publication=", "publication=xyz", "publication=" + correct + "&publication=" + correct):
                        self.assertEqual(self.request(urlsplit(inline).path + "?" + query)[0], 404)
                    resolver.assert_not_called()
                if mode == "local":
                    body_owner, method = Path, "read_bytes"
                    original = Path.read_bytes
                    def forbid_body(path):
                        if path.name in s.QUOTE_SESSION_EXPORT_KINDS.values():
                            raise AssertionError("ARTIFACT_BODY_READ_DENIED")
                        return original(path)
                else:
                    body_owner, method = s.DatabaseSqagStorage, "_publication_version_artifact"
                    def forbid_body(*args, **kwargs):
                        raise AssertionError("ARTIFACT_BODY_READ_DENIED")
                with mock.patch.object(body_owner, method, new=forbid_body):
                    for value in invalid:
                        self.assertEqual(self.request(urlsplit(inline).path + "?publication=" + value)[0], 404)
                    self.assertEqual(self.request(urlsplit(inline).path + "?" + urlsplit(xlsx).query)[0], 404)
                self.assertEqual(self.request(inline), (200, content["pdf"]))

    def test_read_counts_lock_order_snapshot_privacy_and_list_share_facts(self):
        for mode in ("local", "database", "object"):
            with self.subTest(mode=mode), self.case(mode):
                job_id, first, _content = self.publish()
                target = type(self.storage)
                original = target.quote_session_export_artifact
                counts = []
                def resolve(storage, session_id, kind, **kwargs):
                    self.assertFalse(s.JOBS_LOCK.locked(), "Storage read inside JOBS_LOCK")
                    counts.append(kind)
                    return original(storage, session_id, kind, **kwargs)
                before = copy.deepcopy(s.JOBS[job_id])
                with mock.patch.object(target, "quote_session_export_artifact", new=resolve):
                    for operation in (
                        lambda: self.storage.get_quote_session(self.session_id),
                        lambda: self.storage.list_quote_sessions(),
                        lambda: s.get_job(job_id, self.auth),
                        lambda: s.create_job("generate_pdf", copy.deepcopy(self.payload), auth_session=self.auth, requested_job_id=job_id),
                    ):
                        counts.clear()
                        operation()
                        self.assertEqual(counts.count("pdf"), 1)
                    counts.clear()
                    snapshot = copy.deepcopy(before)
                    snapshot["_quote_publication_identity"]["pdf"] = ("wrong identity",)
                    view = s.QuotePublicationReadView(self.storage, job_snapshot=snapshot)
                    self.assert_no_pdf(view.project_job())
                    self.assertEqual(counts.count("pdf"), 0)
                self.assertEqual(s.JOBS[job_id], before)
                self.session_id += "-second"
                self.payload["quote_session"]["session_id"] = self.session_id
                self.storage.create_or_update_quote_session(copy.deepcopy(self.payload))
                self.publish("list-second")
                with mock.patch.object(target, "quote_session_export_artifact", new=resolve):
                    counts.clear()
                    self.assertEqual(len(self.storage.list_quote_sessions()), 2)
                    self.assertEqual(counts.count("pdf"), 2)
                    self.assertEqual(counts.count("xlsx"), 0)
                serialised = json.dumps(s.get_job(job_id, self.auth))
                for private in ("_quote_publication_identity", "quote-publication-comparison-v1", "commercial_state_digest", "object_key_ref", "content_blob", "local-user"):
                    self.assertNotIn(private, serialised)

    def test_role_changes_and_admin_keep_binding_auth_remains_separate(self):
        with self.case("object"):
            job_id, first, content = self.publish()
            inline = self.links(first)[2]
            metadata = self.metadata()
            identity = s.quote_publication_projection_identity(metadata, self.storage, "pdf")
            for role, user, workspace, allowed in (
                ("viewer", "platform-user-123", self.workspace, True),
                ("admin", "synthetic-admin", self.workspace, True),
                ("viewer", "synthetic-viewer", self.workspace, False),
                ("admin", "synthetic-admin", "workspace-foreign", False),
            ):
                auth = self.fixture.platform_auth_session(workspace, role, user)
                with self.subTest(role=role, user=user, workspace=workspace):
                    reads = []
                    original = self.backend.retrieve_artifact
                    def retrieve(artifact, **kwargs):
                        if artifact.owner_type == "generated_quote_version":
                            reads.append(artifact.artifact_kind)
                        return original(artifact, **kwargs)
                    with mock.patch.object(self.backend, "retrieve_artifact", side_effect=retrieve):
                        status, body = self.request(inline, auth=auth)
                    if allowed:
                        self.assertEqual((status, body), (200, content["pdf"]))
                        self.assertEqual(reads, ["pdf"])
                        current = s.app_storage_for_auth_session(auth)
                        self.assertEqual(s.quote_publication_projection_identity(current._read_quote_session_metadata(self.session_id)[0], current, "pdf"), identity)
                        if user != "platform-user-123":
                            self.assertIsNone(s.get_job(job_id, auth))
                    else:
                        self.assertEqual(status, 404)
                        self.assertEqual(reads, [])
            with mock.patch.object(self.backend, "retrieve_artifact", side_effect=AssertionError("NO_UNAUTH_BODY")):
                self.assertIn(self.request(inline, unauthenticated=True)[0], (401, 403))

    def test_config_lookup_occurs_outside_job_lock_without_reading_real_dotenv(self):
        synthetic_job = {"job_id": "job-projection-config", "type": "generate_pdf", "status": "running", "result": None}
        config_reads = []
        original_exists = Path.exists
        original_read_text = Path.read_text
        def exists(path):
            if path.name == ".env":
                self.assertFalse(s.JOBS_LOCK.locked())
                config_reads.append("exists")
                return True
            return original_exists(path)
        def read_text(path, *args, **kwargs):
            if path.name == ".env":
                self.assertFalse(s.JOBS_LOCK.locked())
                config_reads.append("read_text")
                return "APP_MODE=local\nSQAG_STORAGE_MODE=local\nSQAG_ARTIFACT_STORAGE_MODE=local\nSQAG_PLATFORM_LAUNCH_MODE=disabled\n"
            return original_read_text(path, *args, **kwargs)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.dict(s.JOBS, {synthetic_job["job_id"]: synthetic_job}, clear=True), mock.patch.object(Path, "exists", new=exists), mock.patch.object(Path, "read_text", new=read_text):
            self.assertEqual(s.get_job(synthetic_job["job_id"])["status"], "running")
            self.assertEqual(s.create_job("generate_pdf", {}, requested_job_id=synthetic_job["job_id"])["status"], "running")
        self.assertIn("read_text", config_reads)

    def test_direct_generate_handler_never_serialises_private_capture(self):
        for mode in ("local", "database", "object"):
            with self.subTest(mode=mode), self.case(mode):
                job_id = "job-projection-" + mode + "-direct"
                self.outputs[job_id] = (b"PK-synthetic-direct-xlsx", None)
                status, body = self.post_generate(job_id)
                self.assertEqual(status, 200, body)
                result = json.loads(body)
                self.assertEqual(result["status"], "completed", result)
                self.assertTrue(result["quote_session"]["exports"]["xlsx"]["url"])
                self.assert_public_privacy(body)

    def test_second_resume_branch_copies_outside_lock_and_preserves_admission_sequence(self):
        with self.case("object"):
            job_id, _result, _content = self.publish()
            stored = copy.deepcopy(s.JOBS[job_id])
            class RacingJobs(dict):
                checks = 0
                def get(mapping, key, default=None):
                    if key == job_id:
                        mapping.checks += 1
                        if mapping.checks == 1:
                            return None
                    return super().get(key, default)
            jobs = RacingJobs({job_id: copy.deepcopy(stored)})
            original_resolve = s.DatabaseSqagStorage.quote_session_export_artifact
            def resolve(storage, *args, **kwargs):
                self.assertFalse(s.JOBS_LOCK.locked())
                return original_resolve(storage, *args, **kwargs)
            with mock.patch.object(s, "JOBS", jobs), mock.patch.object(s.threading, "Thread") as worker, mock.patch.object(s, "begin_generation_forensics", return_value="") as admission, mock.patch.object(s.DatabaseSqagStorage, "quote_session_export_artifact", new=resolve):
                resumed = s.create_job("generate_pdf", copy.deepcopy(self.payload), auth_session=self.auth, requested_job_id=job_id)
            self.assertEqual(resumed["status"], "completed")
            self.assertTrue(resumed["result"]["quote_session"]["exports"]["pdf"]["url"])
            self.assertEqual(jobs[job_id], stored)
            self.assertEqual(jobs.checks, 2)
            worker.assert_not_called()
            # Existing second-check admission ordering is deliberately retained.
            admission.assert_called_once()

    def test_running_and_unbound_legacy_snapshots_have_no_artifact_projection_reads(self):
        env = existing.isolated_env(SQAG_DISABLE_DOTENV="true")
        snapshots = {
            "job-projection-running": {"job_id": "job-projection-running", "type": "generate_pdf", "status": "running", "result": None},
            "job-projection-legacy": {"job_id": "job-projection-legacy", "type": "generate", "status": "completed", "result": {"status": "completed", "files": [{"name": "quotation.xlsx", "url": "/api/jobs/job-projection-legacy/files/quotation.xlsx"}]}},
        }
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.dict(s.JOBS, snapshots, clear=True), mock.patch.object(s, "quote_session_storage_for_auth_session", side_effect=AssertionError("NO_ARTIFACT_VIEW_FOR_UNBOUND_JOB")):
            self.assertEqual(s.get_job("job-projection-running")["status"], "running")
            legacy = s.get_job("job-projection-legacy")
            self.assertEqual(legacy["result"]["files"], snapshots["job-projection-legacy"]["result"]["files"])

    def test_direct_failure_and_reachable_local_warning_have_no_private_capture(self):
        for mode in ("local", "database", "object"):
            with self.subTest(mode=mode, outcome="failure"), self.case(mode):
                with mock.patch.object(s.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="")):
                    status, body = self.post_generate("job-projection-" + mode + "-direct-failure")
                self.assertEqual(status, 500, body)
                self.assertEqual(json.loads(body)["status"], "failed")
                self.assert_public_privacy(body)
        with self.case("local"):
            job_id = "job-projection-local-direct-warning"
            self.outputs[job_id] = (b"PK-synthetic-warning-xlsx", None)
            committed = False
            original_commit, original_projection = s.commit_local_quote_publication, s.public_quote_session
            def commit(*args, **kwargs):
                nonlocal committed
                metadata = original_commit(*args, **kwargs)
                committed = True
                return metadata
            def project(*args, **kwargs):
                if committed:
                    raise OSError("synthetic post-commit projection failure")
                return original_projection(*args, **kwargs)
            with mock.patch.object(s, "commit_local_quote_publication", side_effect=commit), mock.patch.object(s, "public_quote_session", side_effect=project):
                status, body = self.post_generate(job_id)
            self.assertEqual(status, 200, body)
            result = json.loads(body)
            self.assertEqual(result["post_commit_projection"]["code"], "post_commit_projection_failed")
            self.assertTrue(result["committed_files"])
            self.assert_public_privacy(body)
            self.assertEqual(self.request(result["committed_files"][0]["url"]), (200, b"PK-synthetic-warning-xlsx"))

    def test_unexpected_projection_error_uses_shared_safe_reference_without_history_change(self):
        with self.case("object"):
            job_id, _first, _content = self.publish()
            before = copy.deepcopy(s.JOBS[job_id])
            with mock.patch.object(s.DatabaseSqagStorage, "quote_session_export_artifact", side_effect=RuntimeError("private-canary-never-return")), mock.patch.object(s, "write_local_log") as logged:
                status, body = self.request("/api/jobs/" + job_id)
            self.assertEqual(status, 500)
            payload = json.loads(body)
            reference = payload["error_reference"]
            self.assertTrue(reference)
            self.assertNotIn(b"private-canary-never-return", body)
            self.assertEqual(logged.call_args.args[1]["error_reference"], reference)
            self.assertNotIn("private-canary-never-return", repr(logged.call_args))
            self.assertEqual(s.JOBS[job_id], before)

    def test_failed_attempt_keeps_prior_saved_pair_but_advertises_no_attempt_outputs(self):
        for mode in ("local", "database", "object"):
            with self.subTest(mode=mode), self.case(mode):
                _first_id, first, content = self.publish()
                job_id = "job-projection-" + mode + "-failed"
                self.payload["quote_session"]["status"]["quote_generated"] = True
                with mock.patch.object(s.threading, "Thread") as thread:
                    queued = s.create_job("generate_pdf", copy.deepcopy(self.payload), auth_session=self.auth, requested_job_id=job_id)
                    self.assertEqual(queued["status"], "running")
                    call = thread.call_args.kwargs
                with mock.patch.object(s.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="")):
                    call["target"](*call["args"])
                failed = s.get_job(job_id, self.auth)
                self.assertEqual(failed["status"], "failed")
                self.assertFalse(failed["result"].get("files"))
                exports = failed["result"].get("quote_session", {}).get("exports", {})
                for exported in exports.values():
                    self.assertIsNone(exported.get("url"))
                    self.assertIsNone(exported.get("view_url"))
                saved = self.storage.get_quote_session(self.session_id)
                self.assertEqual(saved["exports"]["pdf"]["url"], self.links(first)[1])
                self.assertEqual(self.request(saved["exports"]["pdf"]["view_url"]), (200, content["pdf"]))

    def test_xlsx_only_atomic_completion_has_no_pdf_link_or_file(self):
        for mode in ("local", "database", "object"):
            with self.subTest(mode=mode), self.case(mode):
                _job_id, result, _content = self.publish("xlsx-only", pdf=False)
                self.assert_no_pdf(result)
                self.assertTrue(result["result"]["quote_session"]["exports"]["xlsx"]["url"])

    def test_xlsx_metadata_reread_cannot_borrow_changed_publication_with_same_bytes(self):
        for mode in ("database", "object"):
            with self.subTest(mode=mode), self.case(mode):
                _job_id, _first, _content = self.publish()
                metadata = self.metadata()
                expected = s.quote_publication_projection_identity(metadata, self.storage, "xlsx")
                changed = copy.deepcopy(metadata)
                changed["publication"]["job_id"] = "job-projection-other-publication"
                with mock.patch.object(self.storage, "_read_quote_session_metadata_for_workspace", return_value=(changed, [])), mock.patch.object(self.storage, "_publication_version_artifact", side_effect=AssertionError("NO_NEW_PAIR_METADATA")):
                    self.assertIsNone(self.storage._quote_artifact_metadata(self.session_id, "xlsx", expected_projection_identity=expected))

    def test_identity_shape_and_raced_metadata_deny_without_body_or_retry(self):
        for mode in ("local", "database", "object"):
            with self.subTest(mode=mode), self.case(mode):
                _job_id, first, _content = self.publish()
                metadata = self.metadata()
                identity = s.quote_publication_projection_identity(metadata, self.storage, "pdf")
                paths = (
                    ("publication", "proof", "subject", "workspace_id"),
                    ("publication", "proof", "subject", "owner_id"), ("session_id",),
                    ("publication", "active_publication_id"), ("publication", "run_id"), ("publication", "job_id"),
                    ("publication", "proof", "output_revision"), ("publication", "proof", "commercial_state_schema"),
                    ("publication", "proof", "commercial_state_digest"), ("exports", "pdf", "filename"),
                    ("exports", "pdf", "publication_id"), ("exports", "pdf", "run_id"),
                    ("exports", "pdf", "sha256"), ("exports", "pdf", "size_bytes"),
                )
                for path in paths:
                    candidate = copy.deepcopy(metadata)
                    target = candidate
                    for key in path[:-1]:
                        target = target[key]
                    value = target.get(path[-1], "")
                    target[path[-1]] = value + 1 if type(value) is int else value + "-different"
                    self.assertIsNone(s.quote_publication_projection_identity(candidate, self.storage, "pdf"), path)
                    if mode == "local":
                        owner, reader, returned = s, "read_quote_session_metadata", candidate
                    else:
                        owner, reader, returned = self.storage, "_read_quote_session_metadata", (candidate, [])
                    with mock.patch.object(owner, reader, return_value=returned):
                        self.assertIsNone(self.storage.quote_session_export_artifact(
                            self.session_id, "pdf", require_current_proof=True,
                            expected_publication=s.quote_publication_binding(identity),
                        ), path)
                snapshot = {"type": "generate_pdf", "status": "completed", "result": first["result"], "_quote_publication_identity": {"pdf": identity}}
                changed = copy.deepcopy(metadata)
                changed["publication"]["proof"]["output_revision"] += 1
                if mode == "local":
                    owner, reader = s, "read_quote_session_metadata"
                    reads = [metadata, changed]
                    body_owner, body_method = Path, "read_bytes"
                    original_body = Path.read_bytes
                    def body(path):
                        if path.name in s.QUOTE_SESSION_EXPORT_KINDS.values():
                            raise AssertionError("RACED_BODY_READ")
                        return original_body(path)
                else:
                    owner, reader = self.storage, "_read_quote_session_metadata"
                    reads = [(metadata, []), (changed, [])]
                    body_owner, body_method = self.storage, "_publication_version_artifact"
                    def body(*args, **kwargs):
                        raise AssertionError("RACED_BODY_READ")
                with mock.patch.object(owner, reader, side_effect=reads) as reread, mock.patch.object(body_owner, body_method, new=body):
                    view = s.QuotePublicationReadView(self.storage, job_snapshot=snapshot)
                    self.assert_no_pdf(view.project_job())
                    self.assertEqual(reread.call_count, 2)


if __name__ == "__main__":
    unittest.main()
