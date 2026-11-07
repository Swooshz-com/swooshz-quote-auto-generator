"""Policy matrix and actual loopback HTTP admission; all identities are fake."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from webapp.profile_access import ProfilePolicy, access_state
from webapp import server as s
from tests import test_internal_password_auth_webapp as auth_http


class ProfilePermissionsTest(unittest.TestCase):
    def test_role_state_matrix(self):
        personal = {"_access": {"version": 1, "owner_user_id": "staff-a", "visibility": "user"}}
        shared = {"_access": {"version": 1, "owner_user_id": "staff-a", "visibility": "shared"}}
        actors = [("staff-a", "operator", True, True), ("staff-b", "operator", False, False),
                  ("staff-a", "admin", True, True), ("support", "admin", False, False),
                  ("staff-a", "management", True, True), ("support", "management", False, False),
                  ("staff-a", "viewer", True, False), ("", "blocked", False, False)]
        for user, role, read, update in actors:
            policy = ProfilePolicy(user, role)
            with self.subTest(user=user, role=role):
                self.assertEqual(policy.can_read(personal), read)
                self.assertEqual(policy.can_update(personal), update)
                self.assertEqual(policy.capabilities(personal), {
                    "canRead": read, "canUpdate": update, "canDelete": update, "canExport": read})
                self.assertEqual(policy.can_read(shared), bool(user))
                self.assertEqual(policy.can_update(shared), role in {"admin", "management"})
                self.assertEqual(policy.can_read({}), role != "blocked")
                self.assertEqual(policy.can_update({}), role in {"admin", "management"})
                for invalid in ({"_access": None}, {"_access": {}}, {"_sqag_deleted": True},
                                {"_access": {"version": True, "owner_user_id": user, "visibility": "user"}},
                                {"_access": {"version": 1, "owner_user_id": user, "visibility": []}}):
                    self.assertFalse(policy.can_read(invalid))
                    self.assertFalse(policy.can_update(invalid))

    def test_http_member_roundtrip_shared_denial_csrf_and_pricing(self):
        for mode in ("database", "object"):
            with self.subTest(mode=mode), mock.patch.object(
                s, "configured_object_storage_backend", return_value=s.InMemoryObjectStorageBackend()
            ):
                self.exercise_http_profile_matrix(mode)

    def exercise_http_profile_matrix(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite:///" + (Path(directory) / "http.sqlite3").as_posix()
            s.apply_sqag_storage_migrations(url)
            host = auth_http.InternalPasswordAuthWebappTest()
            # The login fixture runs in deploy mode, which also requires audit identity config.
            host.setUp({"SQAG_STORAGE_MODE": "database", "SQAG_ARTIFACT_STORAGE_MODE": mode, "SQAG_DATABASE_URL": url,
                        "SQAG_TRACKING_HMAC_KEY": "synthetic-profile-audit-tracking-key",
                        "SQAG_TRACKING_HMAC_KEY_VERSION": "synthetic-v1"})
            try:
                def login(alias):
                    status, headers, _ = host.login(alias)
                    self.assertEqual(status, 302)
                    cookie = s.SESSION_COOKIE_NAME + "=" + host.cookie(headers, s.SESSION_COOKIE_NAME)
                    status, _, raw = host.http("GET", "/api/session", {"Cookie": cookie})
                    self.assertEqual(status, 200)
                    session = json.loads(raw)
                    return {"Cookie": cookie, "Origin": auth_http.ORIGIN, "Content-Type": "application/json",
                            session["csrf_header"]: session["csrf_token"]}, session
                member_headers, session = login(auth_http.email("operator"))
                self.assertTrue(session["permissions"]["canManageProfiles"])
                self.assertFalse(session["permissions"]["canManagePricingReferences"])
                body = {"operation": "create", "id": "imported-forged-target", "label": "Synthetic profile", "defaults": {}}
                status, _, raw = host.http("POST", "/api/settings/profiles", member_headers, json.dumps(body))
                self.assertEqual(status, 200, raw)
                saved = json.loads(raw)["profile"]
                identity = saved["id"]
                self.assertNotEqual(identity, body["id"])
                # Owning a profile does not grant authority to change its access.
                inspection = s.DatabaseSqagStorage(url, os.environ["SQAG_INTERNAL_WORKSPACE_ID"], "admin", "support")
                before = inspection._read_payload("sqag_profiles", "profile_id", identity)
                owner_id = before["_access"]["owner_user_id"]
                for action, access in (
                    ("share", {"version": 1, "owner_user_id": owner_id, "visibility": "shared"}),
                    ("unshare", {"version": 1, "owner_user_id": owner_id, "visibility": "user"}),
                    ("transfer", {"version": 1, "owner_user_id": "another-member", "visibility": "user"}),
                ):
                    with self.subTest(action=action):
                        status, _, raw = host.http("POST", "/api/settings/profiles", member_headers, json.dumps(
                            {"operation": "access", "id": identity, "revision": saved["revision"], "access": access}))
                        self.assertEqual(status, 403, raw)
                        self.assertEqual(inspection._read_payload("sqag_profiles", "profile_id", identity), before)
                export_route = f"/api/settings/profiles/{identity}/export.json"
                status, _, raw = host.http("GET", export_route, member_headers)
                self.assertEqual(status, 200, raw)
                exported = json.loads(raw)
                status, _, raw = host.http("POST", "/api/settings/profiles", member_headers, json.dumps(exported))
                self.assertEqual(status, 200, raw)
                imported = json.loads(raw)["profile"]
                self.assertNotEqual(imported["id"], identity)
                self.assertEqual(host.http("DELETE", f"/api/settings/profiles/{imported['id']}?revision={imported['revision']}", member_headers)[0], 200)
                status, _, _ = host.http("POST", "/api/settings/profiles", {"Cookie": member_headers["Cookie"], "Origin": auth_http.ORIGIN}, json.dumps(body))
                self.assertEqual(status, 403)
                self.assertEqual(host.http("GET", "/api/settings/pricing-references", member_headers)[0], 403)
                admin_headers, _ = login(auth_http.email())
                self.assertEqual(host.http("GET", "/api/settings/pricing-references", admin_headers)[0], 200)
                # Admins cannot discover or operate on another member's personal profile.
                for route in ("/api/profiles", "/api/settings", "/api/settings/profiles"):
                    status, _, raw = host.http("GET", route, admin_headers)
                    self.assertEqual(status, 200, raw)
                    self.assertNotIn(identity, raw.decode())
                denied = host.http("GET", export_route, admin_headers)
                absent = host.http("GET", export_route.replace(identity, "profile-missing"), admin_headers)
                self.assertEqual(denied[0], 404, denied[2])
                self.assertEqual(absent[0], 404, absent[2])
                denied_body, absent_body = json.loads(denied[2]), json.loads(absent[2])
                self.assertTrue(denied_body.pop("error_reference"))
                self.assertTrue(absent_body.pop("error_reference"))
                self.assertEqual(denied_body, absent_body)
                before = inspection._read_payload("sqag_profiles", "profile_id", identity)
                for method, route, body in (
                    ("POST", "/api/settings/profiles", {"operation": "update", "id": identity, "revision": 1, "defaults": {}}),
                    ("POST", "/api/settings/profiles", {"operation": "access", "id": identity, "revision": 1,
                        "access": {"version": 1, "owner_user_id": owner_id, "visibility": "shared"}}),
                    ("DELETE", f"/api/settings/profiles/{identity}?revision=1", None),
                ):
                    status, _, raw = host.http(method, route, admin_headers, json.dumps(body) if body else None)
                    self.assertEqual(status, 404, raw)
                    self.assertEqual(inspection._read_payload("sqag_profiles", "profile_id", identity), before)
                # The admin's own copy can still be loaded, exported, updated and shared.
                body = {"operation": "create", "id": "synthetic-admin-profile", "label": "Synthetic admin profile", "defaults": {}}
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps(body))
                self.assertEqual(status, 200, raw)
                own = json.loads(raw)["profile"]
                identity = own["id"]
                self.assertTrue(all(own["permissions"].values()))
                export_route = f"/api/settings/profiles/{identity}/export.json"
                self.assertEqual(host.http("GET", export_route, admin_headers)[0], 200)
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps({
                    "operation": "update", "id": identity, "revision": own["revision"], "label": "Synthetic updated profile", "defaults": {}}))
                self.assertEqual(status, 200, raw)
                own = json.loads(raw)["profile"]
                # Sharing can assign ownership; authority is checked on the existing row.
                access = {"operation": "access", "id": identity, "revision": own["revision"],
                          "access": {"version": 1, "owner_user_id": owner_id, "visibility": "shared"}}
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps(access))
                self.assertEqual(status, 200, raw)
                revision = json.loads(raw)["profile"]["revision"]
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps({
                    "operation": "update", "id": identity, "revision": revision, "label": "Synthetic shared update", "defaults": {}}))
                self.assertEqual(status, 200, raw)
                revision = json.loads(raw)["profile"]["revision"]
                before = inspection._read_payload("sqag_profiles", "profile_id", identity)
                access.update(revision=revision, access={"version": 1, "owner_user_id": owner_id, "visibility": "user"})
                self.assertEqual(host.http("POST", "/api/settings/profiles", member_headers, json.dumps(access))[0], 403)
                self.assertEqual(inspection._read_payload("sqag_profiles", "profile_id", identity), before)
                self.assertEqual(host.http("GET", export_route, member_headers)[0], 200)
                self.assertEqual(host.http("DELETE", f"/api/settings/profiles/{identity}?revision={revision}", member_headers)[0], 404)
                self.assertEqual(host.http("DELETE", f"/api/settings/profiles/{identity}?revision={revision}", admin_headers)[0], 200)
                # An admin can unshare a shared row, then loses access to its new private state.
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps(body))
                self.assertEqual(status, 200, raw)
                own = json.loads(raw)["profile"]
                access.update(id=own["id"], revision=own["revision"], access={
                    "version": 1, "owner_user_id": owner_id, "visibility": "shared"})
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps(access))
                self.assertEqual(status, 200, raw)
                access.update(revision=json.loads(raw)["profile"]["revision"], access={
                    "version": 1, "owner_user_id": owner_id, "visibility": "user"})
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps(access))
                self.assertEqual(status, 200, raw)
                self.assertFalse(any(json.loads(raw)["profile"]["permissions"].values()))
                self.assertEqual(host.http("GET", f"/api/settings/profiles/{own['id']}/export.json", admin_headers)[0], 404)
                # Legacy metadata is deliberately unchanged until an approved backfill.
                legacy = inspection.save_profile(s.normalize_profile_payload({"id": "legacy-profile", "defaults": {}}))
                legacy_payload = inspection._read_payload("sqag_profiles", "profile_id", legacy["id"])
                legacy_payload.pop("_access")
                legacy_payload.pop("_revision")
                inspection._upsert_payload("sqag_profiles", "profile_id", legacy["id"], legacy_payload)
                for headers in (member_headers, admin_headers):
                    status, _, raw = host.http("GET", "/api/settings/profiles", headers)
                    self.assertEqual(status, 200, raw)
                    self.assertIn(legacy["id"], [p["id"] for p in json.loads(raw)["company_profiles"]])
                    self.assertEqual(host.http("GET", f"/api/settings/profiles/{legacy['id']}/export.json", headers)[0], 200)
                update = {"operation": "update", "id": legacy["id"], "revision": 0, "defaults": {}}
                self.assertEqual(host.http("POST", "/api/settings/profiles", member_headers, json.dumps(update))[0], 404)
                self.assertEqual(host.http("DELETE", f"/api/settings/profiles/{legacy['id']}?revision=0", member_headers)[0], 404)
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps(update))
                self.assertEqual(status, 200, raw)
                self.assertNotIn("_access", inspection._read_payload("sqag_profiles", "profile_id", legacy["id"]))
                revision = json.loads(raw)["profile"]["revision"]
                self.assertEqual(host.http("DELETE", f"/api/settings/profiles/{legacy['id']}?revision={revision}", admin_headers)[0], 200)
            finally:
                host.doCleanups()

    def test_http_local_internal_login_boot_and_saved_profile_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            host = auth_http.InternalPasswordAuthWebappTest()
            host.setUp({"APP_MODE": "local", "SQAG_STORAGE_MODE": "local", "QUOTE_DATA_ROOT": directory,
                        "SQAG_PUBLIC_BASE_URL": "http://127.0.0.1",
                        "TEMP": tempfile.gettempdir(), "TMP": tempfile.gettempdir(), "TMPDIR": tempfile.gettempdir(),
                        "PYTHONDONTWRITEBYTECODE": "1"})
            try:
                host.stack.enter_context(mock.patch.object(auth_http, "ORIGIN", "http://127.0.0.1"))
                for alias in (auth_http.email(), auth_http.email("operator")):
                    status, headers, _ = host.login(alias)
                    self.assertEqual(status, 302)
                    cookie = s.SESSION_COOKIE_NAME + "=" + host.cookie(headers, s.SESSION_COOKIE_NAME)
                    status, _, raw = host.http("GET", "/api/session", {"Cookie": cookie})
                    self.assertEqual(status, 200)
                    session = json.loads(raw)
                    headers = {"Cookie": cookie, "Origin": auth_http.ORIGIN, "Content-Type": "application/json",
                               session["csrf_header"]: session["csrf_token"]}
                    status, _, raw = host.http("GET", "/api/profiles", headers)
                    self.assertEqual(status, 200, raw)
                    self.assertEqual(json.loads(raw)["company_profiles"], [])
                    self.assertTrue(json.loads(raw)["profiles"])
                    self.assertEqual(host.http("GET", "/api/quote-sessions", headers)[0], 200)
                    self.assertEqual(host.http("GET", "/api/settings/profiles", headers)[0], 200)
                    errors = []
                    for method, route, body in (
                        ("POST", "/api/settings/profiles", {"operation": "create", "label": "Synthetic", "defaults": {}}),
                        ("POST", "/api/settings/profiles", {"operation": "update", "id": "synthetic", "revision": 1, "defaults": {}}),
                        ("DELETE", "/api/settings/profiles/synthetic?revision=1", None),
                        ("GET", "/api/settings/profiles/synthetic/export.json", None),
                    ):
                        with self.subTest(alias=alias, method=method, route=route):
                            status, _, raw = host.http(method, route, headers, json.dumps(body) if body else None)
                            self.assertEqual(status, 403, raw)
                            error = json.loads(raw)
                            self.assertTrue(error.pop("error_reference"))
                            errors.append(error)
                    self.assertTrue(all(error == errors[0] for error in errors))
            finally:
                host.doCleanups()


if __name__ == "__main__":
    unittest.main()
