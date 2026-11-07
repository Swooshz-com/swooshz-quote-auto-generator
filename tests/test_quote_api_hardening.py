"""Offline regressions for access-log privacy and secondary admission guards."""

from email.message import Message
import json
import os
import unittest
from unittest import mock

from tests import test_quote_api_auth as fixtures
from tests import test_internal_password_auth_webapp as staff_fixtures
from webapp import quote_api_auth as auth
from webapp import server


class QuoteApiHardeningTest(unittest.TestCase):
    def service_admit(self, registry):
        headers = Message()
        headers[auth.SERVICE_HEADER] = fixtures.TOKEN
        return auth.QuoteApiAdmission(registry=registry, clock=lambda: 1000).admit(
            operation="basis", headers=headers, service_enabled=True, staff_enabled=False,
            reference="ERR-0123ABCD", singleton_header=server.singleton_http_header,
            rate_limited=lambda: False,
            staff_context=mock.Mock(side_effect=AssertionError("staff fallback")),
            staff_mutation_guard=mock.Mock(side_effect=AssertionError("service CSRF")))

    def test_registry_record_id_must_match_presented_id(self):
        # Bypass the fake registry's own ID filter to exercise the admission check.
        registry = mock.Mock(lookup=mock.Mock(return_value=fixtures.credential()))
        self.assertEqual(self.service_admit(registry).auth_mode, "service")
        registry.lookup.return_value = fixtures.credential(
            credential_id=fixtures.encoded(bytes([42]) * 16))
        with self.assertRaises(auth.AdmissionError) as caught:
            self.service_admit(registry)
        self.assertEqual(caught.exception.code, "AUTH_REQUIRED")
        registry.lookup.assert_called_with(fixtures.CREDENTIAL_ID)

    def test_registry_exception_has_closed_capacity_code(self):
        registry = mock.Mock(lookup=mock.Mock(side_effect=RuntimeError("registry-private-canary")))
        with self.assertRaises(auth.AdmissionError) as caught:
            self.service_admit(registry)
        error = caught.exception
        self.assertEqual((error.code, error.status, error.retryable),
                         ("CAPACITY_UNAVAILABLE", 503, True))
        self.assertEqual(error.payload("ERR-0123ABCD"), {"error": {
            "code": "CAPACITY_UNAVAILABLE", "message": "Quote operations are unavailable.",
            "reference": "ERR-0123ABCD", "retryable": True}})
        self.assertNotIn("registry-private-canary", str(error))

    def test_verifier_known_answer_preserves_domain_and_bytes(self):
        # Independently fixed SHA-256 of the documented domain + bytes 0..31.
        expected = "d60944cb8425546a7f8aef4168af7e39f34483653d1964c0954d8b91b5ce5ffc"
        actual = auth.secret_verifier(bytes(range(32)))
        self.assertIs(type(actual), bytes)
        self.assertEqual(len(actual), 32)
        self.assertEqual(actual.hex(), expected)

    def test_platform_session_denied_when_launch_mode_is_off(self):
        session = {"auth_mode": "platform", "user": {
            "subject": "operator.test", "account": "workspace.test", "platform": {
                "outcome": "consumed", "user": {"userId": "operator.test"},
                "workspace": {"workspaceId": "workspace.test"}, "app": {"appKey": "sqag"},
                "membershipRole": "owner", "validationGrantId": "grant.test"}}}
        handler = object.__new__(server.QuoteRunnerHandler)
        handler.current_auth_session = lambda: session
        with mock.patch.dict(os.environ, {"SQAG_DISABLE_DOTENV": "1", "APP_MODE": "local",
                "SQAG_AUTH_MODE": "platform", "SQAG_PLATFORM_LAUNCH_MODE": "platform"}, clear=True):
            self.assertTrue(server.platform_auth_session_complete(session))
            self.assertIsNotNone(handler.current_quote_staff_context("ERR-0123ABCD"))
            with mock.patch.dict(os.environ, {"SQAG_PLATFORM_LAUNCH_MODE": "disabled"}):
                self.assertFalse(server.platform_launch_mode_enabled())
                self.assertIsNone(handler.current_quote_staff_context("ERR-0123ABCD"))

    def test_internal_role_outside_allowlist_denied(self):
        session = {"auth_mode": server.INTERNAL_AUTH_MODE, "policy_fingerprint": "policy.test",
                   "user": {"subject": "operator.test", "account": "workspace.test"}}
        handler = object.__new__(server.QuoteRunnerHandler)
        handler.current_auth_session = lambda: session
        for role in ("admin", "operator", "viewer", "owner", "unexpected-role"):
            with self.subTest(role=role):
                session["user"]["internal_role"] = role
                context = handler.current_quote_staff_context("ERR-0123ABCD")
                if role in {"admin", "operator"}:
                    self.assertIsNotNone(context)
                else:
                    self.assertIsNone(context)

    def staff_fixture(self):
        fixture = staff_fixtures.InternalPasswordAuthWebappTest(methodName="runTest")
        fixture.setUp(extra_env={auth.STAFF_FLAG: "true"})
        self.addCleanup(fixture.doCleanups)
        fixture.stack.enter_context(mock.patch.object(server, "safe_stderr"))
        status, headers, _ = fixture.login()
        self.assertEqual(status, 302)
        cookie = server.SESSION_COOKIE_NAME + "=" + fixture.cookie(headers, server.SESSION_COOKIE_NAME)
        _, _, raw = fixture.http("GET", "/api/session", {"Cookie": cookie})
        session = json.loads(raw)
        return fixture, cookie, (session["csrf_header"], session["csrf_token"])

    def test_staff_two_cookie_headers_denied(self):
        fixture, cookie, _ = self.staff_fixture()
        port = fixture.server.server_port
        host = server.urlparse(staff_fixtures.ORIGIN).netloc
        path = "/api/v1/quote-requests/qr-test"
        self.assertEqual(fixtures.http_request(port, "GET", path, [("Cookie", cookie)], host=host)[0], 503)
        status, _, raw = fixtures.http_request(port, "GET", path,
            [("Cookie", cookie), ("Cookie", cookie)], host=host)
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(raw)["error"]["code"], "AUTH_REQUIRED")

    def test_staff_post_without_origin_or_referer_has_csrf_invalid(self):
        fixture, cookie, csrf = self.staff_fixture()
        port = fixture.server.server_port
        host = server.urlparse(staff_fixtures.ORIGIN).netloc
        path = "/api/v1/quote-requests"
        headers = [("Cookie", cookie), csrf]
        # Both supported same-origin sources remain positive controls.
        for name, value in (("Origin", staff_fixtures.ORIGIN),
                            ("Referer", staff_fixtures.ORIGIN + "/quote")):
            self.assertEqual(fixtures.http_request(port, "POST", path,
                headers + [(name, value)], b"{}", host)[0], 503)
        status, _, raw = fixtures.http_request(port, "POST", path, headers, b"{}", host)
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(raw)["error"]["code"], "CSRF_INVALID")

    def test_v1_normalised_targets_drop_ids_and_query_canaries(self):
        for method, path, operation in fixtures.ROUTES:
            for prefix in ("/", "//", "///", "http://authority.example.test/"):
                for suffix in ("?notes=" + fixtures.TOKEN,
                               "?" + fixtures.TOKEN + "=private-canary", "#" + fixtures.TOKEN):
                    target = prefix + path.lstrip("/") + suffix
                    with self.subTest(method=method, prefix=prefix, operation=operation, suffix=suffix[:1]):
                        line = server.redact_request_line_for_log(method + " " + target + " HTTP/1.0")
                        self.assertEqual(line, method + " /api/v1/" + operation + " HTTP/1.0")
                        self.assertNotIn(fixtures.TOKEN, line)
        # Normalisation for v1 must not alter unrelated route logging.
        self.assertEqual(server.redact_request_target_for_log("//ordinary/path?note=kept"),
                         "//ordinary/path?note=kept")

    def test_actual_http_v1_logs_drop_normalised_target_canaries(self):
        fixture = fixtures.QuoteApiHttpTest(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with mock.patch.object(server, "safe_stderr") as access:
            for prefix in ("//", "///", "http://authority.example.test/"):
                target = prefix + "api/v1/jobs/job-id-canary?notes=" + fixtures.TOKEN
                status, _, raw = fixture.request("GET", target, [(auth.SERVICE_HEADER, fixtures.TOKEN)])
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(raw)["error"]["code"], "INVALID_REQUEST")
            self.assertEqual(access.call_count, 3)
            for call in access.call_args_list:
                self.assertIn("GET /api/v1/poll HTTP/1.0", call.args[0])
                self.assertNotIn("job-id-canary", call.args[0])
                self.assertNotIn(fixtures.TOKEN, call.args[0])
        self.assertNotIn(fixtures.TOKEN, str(fixture.logs.call_args_list))


if __name__ == "__main__":
    unittest.main()
