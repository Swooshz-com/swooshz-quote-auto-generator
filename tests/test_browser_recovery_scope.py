import unittest
from pathlib import Path
from unittest.mock import patch

from webapp import server as webapp


class BrowserRecoveryScopeTest(unittest.TestCase):
    def platform_auth_session(self, workspace_id: str, user_id: str) -> dict:
        context = webapp.safe_platform_launch_context({
            "outcome": "consumed",
            "user": {
                "userId": user_id,
                "email": f"{user_id}@example.test",
                "displayName": f"Synthetic {user_id}",
                "status": "active",
            },
            "workspace": {
                "workspaceId": workspace_id,
                "workspaceSlug": workspace_id,
                "workspaceName": f"Synthetic {workspace_id}",
            },
            "app": {"appKey": "sqag", "appName": "SQAG"},
            "membershipRole": "owner",
            "launchTokenExpiresAt": "2999-01-01T00:00:00.000Z",
        })
        return {"user": webapp.user_from_platform_launch_context(context)}

    def test_scope_is_stable_and_user_workspace_bound(self):
        workspace_a_user_a = self.platform_auth_session("workspace-a", "user-a")
        workspace_a_user_b = self.platform_auth_session("workspace-a", "user-b")
        recovery_scope = getattr(webapp, "browser_recovery_scope", None)
        self.assertIsNotNone(recovery_scope, "Browser recovery scope control is missing.")
        workspace_b_user_a = self.platform_auth_session("workspace-b", "user-a")

        first = recovery_scope(workspace_a_user_a)

        self.assertRegex(first, r"^[a-f0-9]{64}$")
        self.assertEqual(first, recovery_scope(workspace_a_user_a))
        self.assertNotEqual(first, recovery_scope(workspace_a_user_b))
        self.assertNotEqual(first, recovery_scope(workspace_b_user_a))
        self.assertNotIn("user-a", first)
        self.assertNotIn("workspace-a", first)

    def test_scope_is_keyed_by_the_session_secret(self):
        session = self.platform_auth_session("workspace-a", "user-a")

        with patch.dict("os.environ", {"SESSION_SECRET": "a" * 40}):
            first = webapp.browser_recovery_scope(session)
        with patch.dict("os.environ", {"SESSION_SECRET": "b" * 40}):
            second = webapp.browser_recovery_scope(session)

        self.assertNotEqual(first, second)



    def test_authenticated_csrf_token_is_stable_across_process_tokens(self):
        session = self.platform_auth_session("workspace-a", "user-a")
        with patch.dict("os.environ", {"SESSION_SECRET": "replica-stable-session-secret-" + "x" * 16}):
            cookie = webapp.signed_cookie_value(session)
            cookie_header = f"{webapp.SESSION_COOKIE_NAME}={cookie}"
            with patch.object(webapp, "configured_csrf_token", return_value="a" * 40):
                first = webapp.csrf_token_for_cookie_header(cookie_header)
            with patch.object(webapp, "configured_csrf_token", return_value="b" * 40):
                second = webapp.csrf_token_for_cookie_header(cookie_header)

        self.assertEqual(first, second)
        self.assertRegex(first, r"^[a-f0-9]{64}$")

    def test_client_logging_stops_after_recovery_scope_transition_begins_unload(self):
        app_js = Path(__file__).resolve().parents[1] / "webapp" / "static" / "app.js"
        source = app_js.read_text(encoding="utf-8")
        body = source.split("function logClientEvent(event, details = {}) {", 1)[1].split(
            "async function initializeSession", 1
        )[0]

        guard = "if (state.isPageUnloading) return;"
        self.assertIn(guard, body)
        self.assertLess(body.index(guard), body.index('fetch("/api/log"'))

if __name__ == "__main__":
    unittest.main()
