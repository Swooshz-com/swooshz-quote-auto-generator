from __future__ import annotations

import http.cookies
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import unittest
from unittest import mock

from tests.test_webapp import LocalRunnerServer
from webapp import server as webapp


class _VerifiedClaimsAdapter:
    def __init__(self, claims=None):
        self.claims = claims or {
            "sub": "stable-google-subject",
            "email": "admin@example.test",
            "email_verified": True,
        }
        self.calls = []

    def exchange_and_verify(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.claims)


class InternalGoogleAuthWebappTest(unittest.TestCase):
    def setUp(self):
        webapp.INTERNAL_AUTH_STATE.reset()

    def tearDown(self):
        webapp.INTERNAL_AUTH_STATE.reset()

    def internal_env(self, **overrides):
        values = {
            "APP_MODE": "deploy",
            "AUTH_REQUIRED": "true",
            "SQAG_AUTH_MODE": "internal_google",
            "SESSION_SECRET": "synthetic-session-secret-with-enough-entropy",
            "SQAG_TRACKING_HMAC_KEY": "synthetic-tracking-key",
            "SQAG_TRACKING_HMAC_KEY_VERSION": "synthetic-v1",
            "SQAG_TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
            "SQAG_PLATFORM_LAUNCH_MODE": "disabled",
            "SQAG_PUBLIC_BASE_URL": "https://quote.swooshz.com",
            "SQAG_INTERNAL_WORKSPACE_ID": "workspace-internal-alpha",
            "SQAG_INTERNAL_ALLOWED_EMAILS": "admin@example.test,operator@example.test",
            "SQAG_INTERNAL_ADMIN_EMAILS": "admin@example.test",
            "SQAG_INTERNAL_OPERATOR_EMAILS": "operator@example.test",
            "OIDC_ISSUER_URL": "https://accounts.google.com",
            "OIDC_CLIENT_ID": "synthetic-client-id",
            "OIDC_CLIENT_SECRET": "synthetic-client-secret",
            "OIDC_REDIRECT_URI": "https://quote.swooshz.com/callback",
            "OIDC_AUTHORIZE_URL": "https://accounts.google.com/o/oauth2/v2/auth",
            "OIDC_TOKEN_URL": "https://oauth2.googleapis.com/token",
        }
        values.update(overrides)
        return values

    def platform_env(self, **overrides):
        values = {
            "APP_MODE": "deploy",
            "AUTH_REQUIRED": "true",
            "SQAG_AUTH_MODE": "platform",
            "SESSION_SECRET": "synthetic-session-secret-with-enough-entropy",
            "SQAG_TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
            "SQAG_PLATFORM_LAUNCH_MODE": "platform",
            "SQAG_PLATFORM_BASE_URL": "https://swooshz.com",
            "SQAG_PUBLIC_BASE_URL": "https://quote.swooshz.com",
            "SQAG_PLATFORM_SERVICE_SECRET": "synthetic-platform-service-secret-value",
        }
        values.update(overrides)
        return values

    def no_redirect_opener(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect,
        )

    def http(self, runner, method, path, *, headers=None, body=None):
        parsed = urllib.parse.urlparse(runner.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = (response.status, response.headers, payload)
        connection.close()
        return result

    def cookie_value(self, headers, name):
        for raw in headers.get_all("Set-Cookie") or []:
            parsed = http.cookies.SimpleCookie()
            parsed.load(raw)
            if name in parsed:
                return parsed[name].value
        return ""

    def start_login(self, runner):
        status, headers, _body = self.http(runner, "GET", "/login")
        self.assertEqual(status, 302)
        location = headers["Location"]
        transaction_cookie = self.cookie_value(
            headers,
            webapp.OIDC_STATE_COOKIE_NAME,
        )
        self.assertTrue(transaction_cookie)
        return location, transaction_cookie

    def finish_login(self, runner, location, transaction_cookie, verifier=None):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
        verifier = verifier or _VerifiedClaimsAdapter()
        callback = (
            f"{runner.base_url}/callback?"
            + urllib.parse.urlencode(
                {
                    "state": params["state"][0],
                    "code": "synthetic-authorization-code",
                }
            )
        )
        callback_path = urllib.parse.urlparse(callback).path + "?" + urllib.parse.urlparse(callback).query
        with mock.patch.object(
            webapp,
            "google_oidc_verifier",
            return_value=verifier,
        ):
            status, headers, _body = self.http(
                runner,
                "GET",
                callback_path,
                headers={
                    "Cookie": f"{webapp.OIDC_STATE_COOKIE_NAME}={transaction_cookie}",
                },
            )
        self.assertEqual(status, 302)
        session_cookie = self.cookie_value(
            headers,
            webapp.SESSION_COOKIE_NAME,
        )
        self.assertTrue(session_cookie)
        return session_cookie, verifier

    def test_mode_selection_is_explicit_and_mixed_modes_fail_closed(self):
        cases = {
            "missing": self.internal_env(SQAG_AUTH_MODE=""),
            "unknown": self.internal_env(SQAG_AUTH_MODE="unknown"),
            "local": self.internal_env(SQAG_AUTH_MODE="local"),
            "internal_plus_platform_mode": self.internal_env(
                SQAG_PLATFORM_LAUNCH_MODE="platform"
            ),
            "internal_plus_platform_secret": self.internal_env(
                SQAG_PLATFORM_SERVICE_SECRET="synthetic-platform-secret"
            ),
            "platform_plus_internal": {
                **self.platform_env(),
                "SQAG_INTERNAL_WORKSPACE_ID": "workspace-internal-alpha",
                "SQAG_INTERNAL_ALLOWED_EMAILS": "admin@example.test",
                "SQAG_INTERNAL_ADMIN_EMAILS": "admin@example.test",
            },
            "unauthenticated_fallback": self.internal_env(AUTH_REQUIRED="false"),
        }
        for name, env in cases.items():
            with self.subTest(name=name), mock.patch.dict(
                os.environ,
                env,
                clear=True,
            ):
                self.assertTrue(webapp.deploy_requires_auth_guard())

    def test_valid_modes_are_mutually_exclusive(self):
        with mock.patch.dict(os.environ, self.internal_env(), clear=True):
            self.assertTrue(webapp.internal_google_config_complete())
            self.assertFalse(webapp.platform_launch_mode_enabled())
            self.assertFalse(webapp.deploy_requires_auth_guard())
            self.assertFalse(webapp.deploy_requires_platform_workspace_guard())
        with mock.patch.dict(os.environ, self.platform_env(), clear=True):
            self.assertTrue(webapp.platform_launch_config_complete())
            self.assertTrue(webapp.platform_launch_mode_enabled())
            self.assertFalse(webapp.internal_google_mode_enabled())
            self.assertFalse(webapp.deploy_requires_auth_guard())

    def test_internal_configuration_rejects_wrong_origin_redirect_and_http(self):
        cases = {
            "wrong_public_origin": self.internal_env(
                SQAG_PUBLIC_BASE_URL="https://wrong.example.test",
            ),
            "wrong_redirect": self.internal_env(
                OIDC_REDIRECT_URI="https://wrong.example.test/callback",
            ),
            "http_redirect": self.internal_env(
                OIDC_REDIRECT_URI="http://quote.swooshz.com/callback",
            ),
            "wrong_issuer": self.internal_env(
                OIDC_ISSUER_URL="https://issuer.example.test",
            ),
        }
        for name, env in cases.items():
            with self.subTest(name=name), mock.patch.dict(
                os.environ,
                env,
                clear=True,
            ):
                self.assertFalse(webapp.internal_google_config_complete())
                self.assertTrue(webapp.deploy_requires_auth_guard())

    def test_callback_query_is_strict_bounded_and_parsed_once(self):
        valid = webapp.parse_internal_google_callback_query(
            "state=synthetic-state&code=synthetic-code"
        )
        self.assertEqual(valid["state"], "synthetic-state")
        cases = {
            "missing_state": "code=synthetic-code",
            "duplicate_state": "state=one&state=two&code=synthetic-code",
            "unknown_parameter": "state=one&code=two&redirect=https%3A%2F%2Fevil.test",
            "code_and_error": "state=one&code=two&error=access_denied",
            "no_result": "state=one",
            "malformed": "state",
            "oversized": "state=one&code=" + ("x" * 5000),
        }
        for name, query in cases.items():
            with self.subTest(name=name), self.assertRaises(webapp.OidcAuthError):
                webapp.parse_internal_google_callback_query(query)

    def test_internal_session_cookie_is_secure_httponly_host_only_and_bounded(self):
        with mock.patch.dict(os.environ, self.internal_env(), clear=True):
            cookie = webapp.cookie_header_value(
                webapp.SESSION_COOKIE_NAME,
                "synthetic-cookie-value",
                max_age=webapp.SESSION_COOKIE_MAX_AGE_SECONDS,
            )
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Path=/", cookie)
        self.assertIn(
            f"Max-Age={webapp.SESSION_COOKIE_MAX_AGE_SECONDS}",
            cookie,
        )
        self.assertNotIn("Domain=", cookie)

    def test_internal_login_uses_public_redirect_nonce_and_s256_not_proxy_url(self):
        audit = []
        with mock.patch.dict(os.environ, self.internal_env(), clear=True), mock.patch.object(
            webapp,
            "write_local_log",
            side_effect=lambda event, details, **_kwargs: audit.append((event, details)),
        ):
            with LocalRunnerServer(canonical_origin=False) as runner:
                location, _cookie = self.start_login(runner)
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(
            parsed.geturl().split("?", 1)[0],
            "https://accounts.google.com/o/oauth2/v2/auth",
        )
        self.assertEqual(
            params["redirect_uri"],
            ["https://quote.swooshz.com/callback"],
        )
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertTrue(params["state"][0])
        self.assertTrue(params["nonce"][0])
        self.assertNotIn(runner.base_url, location)
        self.assertIn(
            "internal_google_login_initiated",
            {details["reason"] for _event, details in audit},
        )

    def test_callback_without_origin_creates_rotated_secure_host_only_session(self):
        audit = []
        with mock.patch.dict(os.environ, self.internal_env(), clear=True), mock.patch.object(
            webapp,
            "write_local_log",
            side_effect=lambda event, details, **_kwargs: audit.append((event, details)),
        ):
            with LocalRunnerServer() as runner:
                location, transaction_cookie = self.start_login(runner)
                session_cookie, verifier = self.finish_login(
                    runner,
                    location,
                    transaction_cookie,
                )
                status, _headers, raw = self.http(
                    runner,
                    "GET",
                    "/api/session",
                    headers={
                        "Cookie": f"{webapp.SESSION_COOKIE_NAME}={session_cookie}",
                    },
                )
                self.assertEqual(status, 200)
                body = json.loads(raw.decode("utf-8"))
        self.assertEqual(len(verifier.calls), 1)
        self.assertTrue(verifier.calls[0]["code_verifier"])
        self.assertTrue(verifier.calls[0]["nonce"])
        self.assertTrue(body["authenticated"])
        self.assertEqual(body["user"]["subject"], "stable-google-subject")
        self.assertEqual(body["user"]["account"], "workspace-internal-alpha")
        self.assertEqual(body["permissions"]["role"], "admin")
        log_text = json.dumps(audit)
        self.assertNotIn("admin@example.test", log_text)
        self.assertNotIn("synthetic-authorization-code", log_text)
        self.assertNotIn(verifier.calls[0]["nonce"], log_text)
        self.assertIn(
            "internal_google_authentication_succeeded",
            {details["reason"] for _event, details in audit},
        )

    def test_callback_state_replay_and_nonce_failure_are_one_time(self):
        with mock.patch.dict(os.environ, self.internal_env(), clear=True), mock.patch.object(
            webapp,
            "write_local_log",
            return_value=True,
        ):
            with LocalRunnerServer() as runner:
                location, transaction_cookie = self.start_login(runner)
                self.finish_login(runner, location, transaction_cookie)
                params = urllib.parse.parse_qs(
                    urllib.parse.urlparse(location).query
                )
                callback = (
                    f"{runner.base_url}/callback?"
                    + urllib.parse.urlencode(
                        {
                            "state": params["state"][0],
                            "code": "synthetic-authorization-code",
                        }
                    )
                )
                parsed_callback = urllib.parse.urlparse(callback)
                replay_status, _headers, replay_body = self.http(
                    runner,
                    "GET",
                    parsed_callback.path + "?" + parsed_callback.query,
                    headers={
                        "Cookie": f"{webapp.OIDC_STATE_COOKIE_NAME}={transaction_cookie}",
                    },
                )
        self.assertEqual(replay_status, 400)
        self.assertIn(
            "Authentication could not be completed",
            replay_body.decode("utf-8"),
        )

    def test_allowlist_removal_and_role_change_revoke_on_next_request(self):
        with mock.patch.object(webapp, "write_local_log", return_value=True):
            with mock.patch.dict(os.environ, self.internal_env(), clear=True):
                with LocalRunnerServer() as runner:
                    location, transaction_cookie = self.start_login(runner)
                    session_cookie, _verifier = self.finish_login(
                        runner,
                        location,
                        transaction_cookie,
                    )
                    with mock.patch.dict(
                        os.environ,
                        self.internal_env(
                            SQAG_INTERNAL_ALLOWED_EMAILS="operator@example.test",
                            SQAG_INTERNAL_ADMIN_EMAILS="",
                        ),
                        clear=True,
                    ):
                        denied_status, _headers, _body = self.http(
                            runner,
                            "GET",
                            "/api/session",
                            headers={
                                "Cookie": f"{webapp.SESSION_COOKIE_NAME}={session_cookie}",
                            },
                        )
        self.assertEqual(denied_status, 401)

    def test_logout_is_post_only_csrf_checked_and_server_side_revoked(self):
        with mock.patch.dict(os.environ, self.internal_env(), clear=True), mock.patch.object(
            webapp,
            "write_local_log",
            return_value=True,
        ):
            with LocalRunnerServer() as runner:
                location, transaction_cookie = self.start_login(runner)
                session_cookie, _verifier = self.finish_login(
                    runner,
                    location,
                    transaction_cookie,
                )
                cookie = f"{webapp.SESSION_COOKIE_NAME}={session_cookie}"
                unsafe_status, _headers, _body = self.http(
                    runner,
                    "GET",
                    "/logout",
                )
                session_status, _headers, session_raw = self.http(
                    runner,
                    "GET",
                    "/api/session",
                    headers={"Cookie": cookie},
                )
                self.assertEqual(session_status, 200)
                session_body = json.loads(session_raw.decode("utf-8"))
                rejected_status, _headers, _body = self.http(
                    runner,
                    "POST",
                    "/logout",
                    headers={"Cookie": cookie, "Origin": runner.base_url},
                    body=b"",
                )
                logout_status, logout_headers, _body = self.http(
                    runner,
                    "POST",
                    "/logout",
                    headers={
                        "Cookie": cookie,
                        "Origin": runner.base_url,
                        session_body["csrf_header"]: session_body["csrf_token"],
                    },
                    body=b"",
                )
                self.assertEqual(logout_status, 204)
                self.assertEqual(
                    logout_headers["X-SQAG-Logout-Location"],
                    "/signed-out",
                )
                revoked_status, _headers, _body = self.http(
                    runner,
                    "GET",
                    "/api/session",
                    headers={"Cookie": cookie},
                )
        self.assertEqual(unsafe_status, 405)
        self.assertEqual(rejected_status, 403)
        self.assertEqual(revoked_status, 401)

    def test_mode_routes_are_unavailable_and_platform_routes_are_disabled(self):
        with mock.patch.object(webapp, "write_local_log", return_value=True):
            with mock.patch.dict(os.environ, self.platform_env(), clear=True):
                with LocalRunnerServer() as runner:
                    callback_status, _headers, _body = self.http(
                        runner,
                        "GET",
                        "/callback?state=x&code=y",
                    )
                self.assertEqual(callback_status, 404)
            with mock.patch.dict(os.environ, self.internal_env(), clear=True):
                with LocalRunnerServer() as runner:
                    platform_status, _headers, _body = self.http(
                        runner,
                        "POST",
                        webapp.PLATFORM_LAUNCH_ENDPOINT,
                        body=b"",
                    )
                self.assertEqual(platform_status, 404)
            with mock.patch.dict(
                os.environ,
                {
                    "APP_MODE": "local",
                    "AUTH_REQUIRED": "true",
                    "SQAG_AUTH_MODE": "local",
                    "SESSION_SECRET": "synthetic-local-session-secret",
                },
                clear=True,
            ):
                with LocalRunnerServer() as runner:
                    local_status, _headers, _body = self.http(
                        runner,
                        "GET",
                        "/login",
                    )
                self.assertEqual(local_status, 404)

    def test_readiness_never_credits_internal_google_for_public_production(self):
        with mock.patch.dict(os.environ, self.internal_env(), clear=True):
            status = webapp.production_readiness_status()
        self.assertEqual(status["sqag_auth_mode"], "internal_google")
        self.assertFalse(status["production_ready"])
        self.assertIn(
            "temporary_internal_auth_not_public_release_ready",
            {item["id"] for item in status["production_blockers"]},
        )


if __name__ == "__main__":
    unittest.main()
