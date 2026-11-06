"""Policy matrix and actual loopback HTTP admission; all identities are fake."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from webapp.profile_access import ProfilePolicy, access_state
from webapp import server as s
from tests import test_internal_password_auth_webapp as auth_http


class ProfilePermissionsTest(unittest.TestCase):
    def test_role_state_matrix(self):
        personal = {"_access": {"version": 1, "owner_user_id": "staff-a", "visibility": "user"}}
        shared = {"_access": {"version": 1, "owner_user_id": "staff-a", "visibility": "shared"}}
        actors = [("staff-a", "operator", True, True), ("staff-b", "operator", False, False),
                  ("staff-a", "admin", True, True), ("support", "admin", True, True),
                  ("staff-a", "viewer", True, False), ("", "blocked", False, False)]
        for user, role, read, update in actors:
            policy = ProfilePolicy(user, role)
            with self.subTest(user=user, role=role):
                self.assertEqual(policy.can_read(personal), read)
                self.assertEqual(policy.can_update(personal), update)
                self.assertEqual(policy.can_read(shared), bool(user))
                self.assertEqual(policy.can_update(shared), role == "admin")
                self.assertEqual(policy.can_read({}), role != "blocked")
                self.assertEqual(policy.can_update({}), role == "admin")
                for invalid in ({"_access": None}, {"_access": {}}, {"_sqag_deleted": True},
                                {"_access": {"version": True, "owner_user_id": user, "visibility": "user"}},
                                {"_access": {"version": 1, "owner_user_id": user, "visibility": []}}):
                    self.assertFalse(policy.can_read(invalid))
                    self.assertFalse(policy.can_update(invalid))

    def test_http_member_roundtrip_shared_denial_csrf_and_pricing(self):
        with tempfile.TemporaryDirectory() as directory:
            url = "sqlite:///" + (Path(directory) / "http.sqlite3").as_posix()
            s.apply_sqag_storage_migrations(url)
            host = auth_http.InternalPasswordAuthWebappTest()
            # The login fixture runs in deploy mode, which also requires audit identity config.
            host.setUp({"SQAG_STORAGE_MODE": "database", "SQAG_ARTIFACT_STORAGE_MODE": "database", "SQAG_DATABASE_URL": url,
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
                access = {"operation": "access", "id": identity, "revision": 1,
                          "access": {"version": 1, "owner_user_id": None, "visibility": "shared"}}
                status, _, raw = host.http("POST", "/api/settings/profiles", admin_headers, json.dumps(access))
                self.assertEqual(status, 200, raw)
                revision = json.loads(raw)["profile"]["revision"]
                self.assertEqual(host.http("GET", export_route, member_headers)[0], 200)
                self.assertEqual(host.http("DELETE", f"/api/settings/profiles/{identity}?revision={revision}", member_headers)[0], 404)
                self.assertEqual(host.http("DELETE", f"/api/settings/profiles/{identity}?revision={revision}", admin_headers)[0], 200)
            finally:
                host.doCleanups()


if __name__ == "__main__":
    unittest.main()
