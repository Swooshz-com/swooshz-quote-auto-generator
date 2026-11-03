"""A17/A18 source qualification with real password admission and disposable SQLite.

Pinned legacy contract: 8fa9439 internal_google_auth.InternalAuthState.create_session
stored user.subject=identity.sub and user.account=policy.workspace_id. That base's
server platform_workspace_id_from_auth_session recognised its admitted internal
mode, so browser recovery used [platform, subject, workspace]. No legacy code or
obsolete cookie is used to admit a current request here.
"""
import contextlib
import copy
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest import mock
from webapp import server as webapp
from tests import test_webapp as fixtures
from tests import test_internal_password_auth_webapp as http_fixtures
from tests.test_internal_password_auth import email, policy_values


class InternalPasswordIdentityContinuityTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        # Compose the existing fixtures; do not inherit their concrete test cases.
        self.data_fixture = fixtures.WebappServerTest(methodName="runTest")
        self.data_fixture.setUp()
        self.stack.callback(self.data_fixture.doCleanups)
        self.http_fixture = http_fixtures.InternalPasswordAuthWebappTest(methodName="runTest")
        self.root = fixtures.test_temp_root() / ("identity-" + str(time.time_ns()))
        self.root.mkdir(parents=True)
        self.database_url = "sqlite:///" + (self.root / "identity.sqlite3").as_posix()
        rows = json.loads(policy_values()["bindings_json"])
        rows.append(dict(user_id="stable-peer-operator-subject", email=email("peer"), role="operator"))
        env = {
            "SQAG_STORAGE_MODE": "database", "SQAG_ARTIFACT_STORAGE_MODE": "database",
            "SQAG_DATABASE_URL": self.database_url,
            "QUOTE_DATA_ROOT": str(self.root / "data"),
            "SQAG_INTERNAL_ALLOWED_EMAILS": ";".join(row["email"] for row in rows if row["role"] == "operator"),
            "SQAG_INTERNAL_IDENTITY_BINDINGS_JSON": json.dumps(rows),
            "SQAG_TRACKING_HMAC_KEY": "synthetic-identity-tracking-hmac-key-value",
            "SQAG_TRACKING_HMAC_KEY_VERSION": "test-v1",
            "OPENROUTER_API_KEY": "synthetic-key", **fixtures.SYNTHETIC_DRAFT_CONFIG,
        }
        self.http_fixture.setUp(extra_env=env)
        self.stack.callback(self.http_fixture.doCleanups)
        webapp.apply_sqag_storage_migrations(self.database_url)
        self.job_ids = []
        self.stack.callback(self.remove_owned_jobs)

    def remove_owned_jobs(self):
        with webapp.JOBS_LOCK:
            for job_id in self.job_ids:
                webapp.JOBS.pop(job_id, None)

    def admit(self, alias="operator"):
        status, headers, _ = self.http_fixture.login(email(alias))
        self.assertEqual(status, 302)
        raw = self.http_fixture.cookie(headers, webapp.SESSION_COOKIE_NAME)
        cookie = webapp.SESSION_COOKIE_NAME + "=" + raw
        opaque = webapp.verified_cookie_payload(raw)
        session = self.http_fixture.server.internal_auth.validate_session(
            opaque, policy_fingerprint=self.http_fixture.server.internal_policy.fingerprint, current_mode=webapp.INTERNAL_AUTH_MODE,
        )
        status, _, body = self.http_fixture.http("GET", "/api/session", {"Cookie": cookie})
        self.assertEqual(status, 200)
        return cookie, session, json.loads(body)

    def get(self, cookie, path):
        return self.http_fixture.http("GET", path, {"Cookie": cookie})

    def seed_saved_data(self, storage):
        session_id = "quote-identity-owned"
        payload, result = fixtures.eligible_database_publication_fixture(storage, session_id, suffix="identity")
        output = self.root / "output"
        output.mkdir()
        self.workbook = fixtures.xlsx_with_rows([["Synthetic identity continuity"]])
        (output / "quotation.xlsx").write_bytes(self.workbook)
        storage.create_or_update_quote_session(payload, result=result, output_dir=output)
        self.profile_id = "publication-profile-identity"
        self.payload = payload
        early = fixtures.valid_payload()
        early["quote_session"] = {"session_id": "quote-identity-early", "draft_state": {"activeSidePanel": "customer"}}
        storage.create_or_update_quote_session(early)
        return session_id

    def create_owned_job(self, payload, session):
        # Stop only computation; real admission, snapshot, ownership and registration run.
        with mock.patch.object(webapp.threading, "Thread") as worker:
            result = webapp.create_job("draft", copy.deepcopy(payload), auth_session=session)
        self.assertIn("job_id", result, result)
        worker.return_value.start.assert_called_once()
        self.job_ids.append(result["job_id"])
        return result["job_id"]

    def saved_snapshot(self, storage, session_id):
        with storage.connection() as connection:
            row = connection.execute(
                "select metadata_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                (storage.workspace_id, session_id),
            ).fetchone()
        profile = storage.company_profile_export_payload(self.profile_id)
        profile.pop("exported_at")  # Generated transport timestamp is not stored profile data.
        return row["metadata_json"], profile, storage.quote_session_export_artifact(session_id, "xlsx")["content"]

    def assert_actor_access(self, cookie, session, session_id, job_id, *, quote, early, job, profile):
        storage = webapp.app_storage_for_auth_session(session)
        status, _, body = self.get(cookie, "/api/quote-sessions/" + session_id)
        self.assertEqual(status, 200 if quote else 404)
        listed = json.loads(self.get(cookie, "/api/quote-sessions")[2])["quote_sessions"]
        self.assertEqual(session_id in {item["session_id"] for item in listed}, quote)
        self.assertEqual(self.get(cookie, "/api/quote-sessions/quote-identity-early")[0], 200 if early else 404)
        status, _, content = self.get(cookie, "/api/quote-sessions/" + session_id + "/download/xlsx")
        self.assertEqual(status, 200 if quote else 404)
        if quote:
            self.assertEqual(content, self.workbook)
        else:
            self.assertNotEqual(content, self.workbook)
        self.assertEqual(self.get(cookie, "/api/jobs/" + job_id)[0], 200 if job else 404)
        self.assertEqual(webapp.job_visible_to_auth_session(webapp.JOBS[job_id], session), job)
        profiles = json.loads(self.get(cookie, "/api/profiles")[2])["company_profiles"]
        self.assertEqual(self.profile_id in {item["id"] for item in profiles}, profile)
        self.assertEqual(storage.company_profile_export_payload(self.profile_id) is not None, profile)
        # A viewable admin quote is still not editable by that other subject.
        metadata = storage._read_quote_session_metadata_for_workspace(session_id)[0]
        self.assertEqual(storage._quote_session_editable_by_current_user(metadata), job)
        self.assertEqual(self.get(cookie, "/api/jobs/" + job_id + "/files/quotation.xlsx")[0], 404)

    def test_A17_new_http_login_owner_role_workspace_consumers(self):
        owner_cookie, owner, _ = self.admit()
        storage = webapp.app_storage_for_auth_session(owner)
        session_id = self.seed_saved_data(storage)
        job_id = self.create_owned_job(self.payload, owner)
        original = self.saved_snapshot(storage, session_id)
        cases = [
            ("operator", None, True, True, True, True),
            ("peer", None, False, False, False, True),
            ("admin", None, True, False, False, True),
            ("operator", "workspace-identity-other", False, False, False, False),
            ("admin", "workspace-identity-other", False, False, False, False),
        ]
        for alias, workspace, quote, early, job, profile in cases:
            with self.subTest(alias=alias, workspace=workspace):
                changes = {} if workspace is None else {"SQAG_INTERNAL_WORKSPACE_ID": workspace}
                with mock.patch.dict(os.environ, changes), self.http_fixture.fresh_server_if(changes):
                    cookie, session, _ = self.admit(alias)
                    self.assert_actor_access(cookie, session, session_id, job_id, quote=quote, early=early, job=job, profile=profile)
        self.assertEqual(self.saved_snapshot(storage, session_id), original)
        self.assertEqual(self.get(owner_cookie, "/api/session")[0], 200)

    def test_staff_dashboard_projects_only_workspace_summaries_and_verified_creators(self):
        owner_cookie, owner, owner_public = self.admit()
        peer_cookie, peer, peer_public = self.admit("peer")
        admin_cookie, _, admin_public = self.admit("admin")
        owner_storage = webapp.app_storage_for_auth_session(owner)
        peer_storage = webapp.app_storage_for_auth_session(peer)
        ids = ("quote-staff-owner", "quote-staff-peer", "quote-staff-legacy")
        payload = fixtures.valid_payload()
        for session_id, storage in zip(ids, (owner_storage, peer_storage, owner_storage)):
            payload["quote_session"] = {
                "session_id": session_id, "draft_state": {"activeSidePanel": "customer"},
                "owner": {"user_id": "client-supplied-owner"}, "created_by": email("forged"),
            }
            storage.create_or_update_quote_session(payload)
        with owner_storage.connection() as connection:
            row = connection.execute(
                "select metadata_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                (owner_storage.workspace_id, ids[2]),
            ).fetchone()
            legacy = json.loads(row["metadata_json"])
            legacy.pop("owner")
            connection.execute(
                "update sqag_quote_sessions set metadata_json = ? where workspace_id = ? and session_id = ?",
                (json.dumps(legacy), owner_storage.workspace_id, ids[2]),
            )
            connection.commit()
        for cookie, expected, alias, public in (
            (owner_cookie, {ids[0]: email("operator")}, "operator", owner_public),
            (peer_cookie, {ids[1]: email("peer")}, "peer", peer_public),
            (admin_cookie, dict(zip(ids, (email("operator"), email("peer"), ""))), "admin", admin_public),
        ):
            with self.subTest(alias=alias):
                role = "admin" if alias == "admin" else "operator"
                self.assertEqual(public["staff_identity"], {"email": email(alias), "role": role})
                self.assertIsNone(public["user"])
                status, _, body = self.get(cookie, "/api/quote-sessions?role=admin&owner_id=any")
                self.assertEqual(status, 200)
                sessions = json.loads(body)["quote_sessions"]
                self.assertEqual({row["session_id"]: row["created_by"] for row in sessions}, expected)
                self.assertEqual(len(sessions), len(expected))
                for session in sessions:
                    self.assertNotIn("owner", session)
                    if alias == "admin":
                        self.assertTrue(session["dashboard_summary_only"])
                        self.assertTrue({"draft_state", "draft_files", "exports", "pricing_ledger"}.isdisjoint(session))
                headers = {"Cookie": cookie, "Origin": http_fixtures.ORIGIN,
                           public["csrf_header"]: public["csrf_token"], "Content-Type": "application/json"}
                for session_id in set(ids) - ({ids[0]} if alias == "operator" else {ids[1]} if alias == "peer" else set()):
                    path = "/api/quote-sessions/" + session_id
                    self.assertEqual(self.get(cookie, path)[0], 404)
                    self.assertEqual(self.get(cookie, path + "/download/xlsx")[0], 404)
                    if alias != "admin":
                        self.assertEqual(self.http_fixture.http("DELETE", path, headers)[0], 404)
        # Existing save recovery issues a fresh caller-owned ID on an ownership
        # conflict. It must leave the requested session and its owner untouched.
        before = owner_storage._read_quote_session_metadata_for_workspace(ids[0])
        payload["quote_session"]["session_id"] = ids[0]
        headers = {"Cookie": admin_cookie, "Origin": http_fixtures.ORIGIN, "Content-Type": "application/json",
                   admin_public["csrf_header"]: admin_public["csrf_token"]}
        status, _, body = self.http_fixture.http("POST", "/api/quote-sessions", headers, json.dumps(payload))
        self.assertEqual(status, 200)
        new_id = json.loads(body)["quote_session"]["session_id"]
        self.assertNotIn(new_id, ids)
        self.assertEqual(owner_storage._read_quote_session_metadata_for_workspace(ids[0]), before)
        new_metadata, _ = owner_storage._read_quote_session_metadata_for_workspace(new_id)
        self.assertEqual(new_metadata["owner"], {"user_id": "stable-google-subject"})
        self.assertNotIn("created_by", new_metadata)
        changes = {"SQAG_INTERNAL_WORKSPACE_ID": "workspace-identity-other"}
        with mock.patch.dict(os.environ, changes), self.http_fixture.fresh_server_if(changes):
            other, _, other_public = self.admit("admin")
            self.assertEqual(json.loads(self.get(other, "/api/quote-sessions")[2])["quote_sessions"], [])
            self.assertEqual(self.get(other, "/api/quote-sessions/" + ids[0])[0], 404)
            other_headers = headers | {"Cookie": other, other_public["csrf_header"]: other_public["csrf_token"]}
            self.assertEqual(self.http_fixture.http("DELETE", "/api/quote-sessions/" + ids[0], other_headers)[0], 404)

        def delete_as(cookie, public, session_id, expected_status, outcome):
            audit = self.http_fixture.logs
            audit.reset_mock()
            delete_headers = headers | {"Cookie": cookie, public["csrf_header"]: public["csrf_token"]}
            status, _, response = self.http_fixture.http("DELETE", "/api/quote-sessions/" + session_id, delete_headers)
            self.assertEqual(status, expected_status)
            records = [call.args[1] for call in audit.call_args_list if call.args[0] == "quote_session_delete_attempt"]
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["outcome"], outcome)
            for field in ("actor_tracking_id", "workspace_tracking_id", "quote_tracking_id"):
                self.assertRegex(record[field], r"^pid-test-v1-[0-9a-f]{24}$")
            self.assertNotIn(session_id, str(record))
            self.assertNotIn("@", str(record))
            return json.loads(response)

        # A storage failure is logged once without including its exception.
        with mock.patch.object(webapp.DatabaseSqagStorage, "delete_quote_session",
                               side_effect=RuntimeError("synthetic-private-exception")):
            failed = delete_as(admin_cookie, admin_public, ids[0], 500, "error")
            self.assertRegex(failed["error_reference"], r"^ERR-[0-9A-F]{8}$")
            self.assertNotIn("synthetic-private-exception", str(self.http_fixture.logs.call_args_list))
        with mock.patch.object(webapp.DatabaseSqagStorage, "_session_deletion_blocked_by_hold", return_value=True):
            delete_as(admin_cookie, admin_public, ids[0], 404, "rejected")
        delete_as(peer_cookie, peer_public, ids[0], 404, "rejected")
        delete_as(peer_cookie, peer_public, ids[1], 200, "success")
        # A dashboard bulk confirmation calls DELETE once per selected quote.
        for session_id in (ids[0], ids[2]):
            delete_as(admin_cookie, admin_public, session_id, 200, "success")
            self.assertEqual(owner_storage._read_quote_session_metadata_for_workspace(session_id), ({}, []))
        self.assertEqual(json.loads(self.get(admin_cookie, "/api/quote-sessions")[2])["quote_sessions"][0]["session_id"], new_id)
        status, _, response = self.http_fixture.http("POST", "/api/log", headers, json.dumps({
            "event": "QUOTE SESSION DELETE ATTEMPT", "details": {"outcome": "success"},
        }))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(response), {"status": "ignored"})

    def test_A18_legacy_owner_mapping_recovery_and_saved_data_are_preserved(self):
        values = policy_values()
        legacy_subject = "stable-google-operator-subject"
        legacy_workspace = values["workspace_id"]
        legacy_storage = webapp.DatabaseSqagStorage(self.database_url, legacy_workspace, role="operator", user_id=legacy_subject)
        session_id = self.seed_saved_data(legacy_storage)
        original = self.saved_snapshot(legacy_storage, session_id)
        cookie, admitted, public = self.admit()
        self.assertEqual(admitted["user"]["subject"], legacy_subject)
        self.assertEqual(admitted["user"]["account"], legacy_workspace)
        expected_material = json.dumps(["platform", legacy_subject, legacy_workspace], ensure_ascii=True, separators=(",", ":")).encode()
        expected_scope = hmac.new(os.environ["SESSION_SECRET"].encode(), b"sqag-browser-recovery-v1\0" + expected_material, hashlib.sha256).hexdigest()
        self.assertEqual(public["browser_recovery_scope"], expected_scope)
        self.assertEqual(webapp.job_owner_context_from_auth_session(admitted), {"user_id": legacy_subject, "workspace_id": legacy_workspace})
        job_id = self.create_owned_job(self.payload, admitted)
        self.assert_actor_access(cookie, admitted, session_id, job_id, quote=True, early=True, job=True, profile=True)
        _, _, fresh = self.admit()
        self.assertEqual(fresh["browser_recovery_scope"], expected_scope)
        self.assertEqual(self.saved_snapshot(legacy_storage, session_id), original)

    def test_A18_unknown_changed_identity_and_old_cookie_are_denied(self):
        cookie, original_session, public = self.admit()
        storage = webapp.app_storage_for_auth_session(original_session)
        session_id = self.seed_saved_data(storage)
        job_id = self.create_owned_job(self.payload, original_session)
        original = self.saved_snapshot(storage, session_id)
        self.assertEqual(self.http_fixture.login(email("unknown"))[0], 403)
        rows = json.loads(os.environ["SQAG_INTERNAL_IDENTITY_BINDINGS_JSON"])
        for row in rows:
            if row["email"] == email("operator"):
                row["user_id"] = "changed-synthetic-operator-subject"
        cases = [
            ({"SQAG_INTERNAL_IDENTITY_BINDINGS_JSON": json.dumps(rows)}, True),
            ({"SQAG_INTERNAL_WORKSPACE_ID": "workspace-identity-changed"}, False),
        ]
        for changes, shared_profile in cases:
            with self.subTest(changes=tuple(changes)), mock.patch.dict(os.environ, changes):
                self.assertEqual(self.get(cookie, "/api/session")[0], 401)
                with self.http_fixture.fresh_server_if(changes):
                    changed_cookie, changed_session, changed_public = self.admit()
                    self.assertNotEqual(changed_public["browser_recovery_scope"], public["browser_recovery_scope"])
                    self.assert_actor_access(changed_cookie, changed_session, session_id, job_id, quote=False, early=False, job=False, profile=shared_profile)
        old = webapp.signed_cookie_value({"auth_mode": "internal_google", "user": {"subject": "stable-google-operator-subject", "account": policy_values()["workspace_id"]}})
        old_cookie = webapp.SESSION_COOKIE_NAME + "=" + old
        self.assertEqual(self.get(old_cookie, "/api/session")[0], 401)
        self.assertEqual(self.get(old_cookie, "/api/quote-sessions/" + session_id)[0], 401)
        self.assertEqual(self.http_fixture.http("GET", "/callback?code=synthetic")[0], 404)
        self.assertEqual(self.saved_snapshot(storage, session_id), original)
