"""Synthetic admission checks; each test names the forbidden effect it prevents."""

import base64
import contextlib
from dataclasses import replace
from email.message import Message
import http.client
import json
import os
import threading
import unittest
from unittest import mock

from webapp import quote_api_auth as auth
from webapp import server
from webapp.quote_context import QUOTE_OPERATIONS
from tests import test_internal_password_auth_webapp as staff_fixtures


def encoded(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


# Reserved deterministic synthetic bytes, never a generated or usable live key.
SECRET = bytes(range(32))
CREDENTIAL_ID = encoded(bytes(range(16)))
TOKEN = "sqagq1." + CREDENTIAL_ID + "." + encoded(SECRET)
ROUTES = (
    ("POST", "/api/v1/quote-requests", "create"),
    ("POST", "/api/v1/quote-requests/qr-test/drafts", "draft"),
    ("POST", "/api/v1/quote-steps/price", "price"),
    ("POST", "/api/v1/quote-requests/qr-test/exports", "export"),
    ("GET", "/api/v1/jobs/job-test", "poll"),
    ("GET", "/api/v1/quote-requests/qr-test", "basis"),
    ("GET", "/api/v1/quote-requests/qr-test/result", "result"),
    ("GET", "/api/v1/quote-requests/qr-test/artifacts/xlsx", "artifact"),
    ("GET", "/api/v1/quote-requests/qr-test/artifacts/pdf", "artifact"),
)


def credential(**changes):
    return replace(auth.ServiceCredential(
        CREDENTIAL_ID, auth.secret_verifier(SECRET), "tenant.test", "workspace.test",
        "caller.test", "service-owner.test", "credential-1", "authority-1",
        QUOTE_OPERATIONS, 2000,
    ), **changes)


class FakeRegistry:
    """Test adapter only; no file/database or environment registry fallback."""
    def __init__(self, record=None):
        self.record = record if record is not None else credential()
        self.lookups = []

    def lookup(self, credential_id):
        self.lookups.append(credential_id)
        return self.record if self.record and self.record.credential_id == credential_id else None


class PersistentRegistryAdmissionTest(unittest.TestCase):
    def test_injected_registry_committed_rotation_and_revoke_deny_old_tokens_without_cache(self):
        from tests import test_quote_api_registry as fixtures
        fixture=fixtures.RegistryFixture()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        admission=auth.QuoteApiAdmission(registry=fixture.adapter,clock=lambda:120)
        token='sqagq1.'+fixtures.CID+'.'+encoded(SECRET)
        before=admission._service_context(token,'synthetic-reference')
        with fixture.transaction() as c:
            fixtures.operator_change(c,kind='rotate',verifier=auth.secret_verifier(b'z'*32))
        with self.assertRaises(auth.AdmissionError) as denied:
            admission._service_context(token,'synthetic-reference')
        self.assertEqual(denied.exception.code,'AUTH_REQUIRED')
        token='sqagq1.'+fixtures.CID+'.'+encoded(b'z'*32)
        after=admission._service_context(token,'synthetic-reference')
        self.assertEqual(before.owner_subject,after.owner_subject)
        self.assertNotEqual(before.auth_revision,after.auth_revision)
        with fixture.transaction() as c: fixtures.operator_change(c,kind='revoke',expected=2)
        with self.assertRaises(auth.AdmissionError) as denied:
            admission._service_context(token,'synthetic-reference')
        self.assertEqual(denied.exception.code,'AUTH_REQUIRED')


def http_request(port, method, path, headers=(), body=b"", host=None):
    """Coalesce the small synthetic request, including body, into one write.

    Admission refuses before consuming a body. Sending headers and body in
    separate writes races that deliberate early close on Windows loopback.
    No retry, timeout increase or response assertion is hidden here.
    """
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.connect()
        fields = [("Host", host or "127.0.0.1:" + str(port)), *headers, ("Content-Length", str(len(body)))]
        raw = (method + " " + path + " HTTP/1.0\r\n" + "".join(
            name + ": " + value + "\r\n" for name, value in fields) + "\r\n").encode("latin-1") + body
        connection.sock.sendall(raw)
        response = http.client.HTTPResponse(connection.sock)
        response.begin()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


class QuoteApiAuthTest(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRegistry()
        self.admission = auth.QuoteApiAdmission(registry=self.registry, clock=lambda: 1000)

    def admit(self, token=TOKEN, **changes):
        headers = Message()
        headers[auth.SERVICE_HEADER] = token
        values = dict(operation="basis", headers=headers, service_enabled=True, staff_enabled=False,
                      reference="ERR-0123ABCD", singleton_header=server.singleton_http_header,
                      rate_limited=lambda: False, staff_context=mock.Mock(side_effect=AssertionError("staff fallback")),
                      staff_mutation_guard=mock.Mock(side_effect=AssertionError("service CSRF")))
        values.update(changes)
        return self.admission.admit(**values)

    def test_exact_route_method_allowlist_prevents_internal_dispatch(self):
        from tests.test_quote_api_contract import SPEC
        paths = {(m.upper(), p.replace("{id}", "qr-test").replace("{job_id}", "job-test").replace("{kind}", "xlsx"))
                 for p, methods in SPEC["paths"].items() for m in methods}
        self.assertEqual(paths, {(m, p) for m, p, _ in ROUTES if not p.endswith("/pdf")})
        for method, path, operation in ROUTES:
            self.assertEqual(auth.quote_operation(method, path), operation)
            for wrong in {"GET", "POST", "DELETE", "OPTIONS", "PUT", "HEAD"} - {method}:
                self.assertIsNone(auth.quote_operation(wrong, path))
        for path in ("/api/jobs/job-test", "/api/v1/quote-requests/", "/api/v1/quote-requests/%71r-test",
                     "/api/v1/quote-requests/qr-test/other", "/api/v1/quote-requests/_bad",
                     "/api/v1/quote-requests/" + "a" * 129, "/api/v1/quote-requests/qr-test\n"):
            self.assertIsNone(auth.quote_operation("GET", path))

    def test_strict_token_encoding_prevents_alternate_spellings(self):
        self.assertEqual(auth.parse_service_token(TOKEN), (CREDENTIAL_ID, SECRET))
        malformed = ("", " " + TOKEN, TOKEN + " ", TOKEN + "\n", TOKEN + "=", TOKEN.upper(),
                     TOKEN.replace("sqagq1", "sqagq2"), TOKEN[:-1], TOKEN + "a", TOKEN.replace(".", ",", 1),
                     "sqagq1." + CREDENTIAL_ID[:-1] + "x." + encoded(SECRET),
                     TOKEN[:-1] + "9", TOKEN[:-1] + "+", TOKEN[:-1] + "\u00e9")
        for value in malformed:
            with self.subTest(value_index=malformed.index(value)):
                self.assertIsNone(auth.parse_service_token(value))
                with self.assertRaisesRegex(auth.AdmissionError, "AUTH_REQUIRED"):
                    self.admit(value)

    def test_unknown_and_malformed_tokens_compare_fixed_dummy_without_disclosure(self):
        unknown = "sqagq1." + encoded(bytes([42]) * 16) + "." + encoded(SECRET)
        for token, expected in ((unknown, auth.DUMMY_VERIFIER), ("", auth.DUMMY_VERIFIER),
                                (TOKEN[:-43] + encoded(bytes(32)), self.registry.record.verifier)):
            with self.subTest(case=len(token)), mock.patch.object(auth.hmac, "compare_digest", wraps=auth.hmac.compare_digest) as compare:
                with self.assertRaises(auth.AdmissionError) as caught:
                    self.admit(token)
                self.assertEqual(caught.exception.code, "AUTH_REQUIRED")
                self.assertEqual(caught.exception.payload("ERR-0123ABCD")["error"]["message"], "Authentication is required.")
                if token:
                    compare.assert_called_once()
                    self.assertEqual(compare.call_args.args[1], expected)
                    self.assertEqual([len(v) for v in compare.call_args.args], [32, 32])
        headers = Message()
        with mock.patch.object(auth.hmac, "compare_digest", wraps=auth.hmac.compare_digest) as compare:
            with self.assertRaisesRegex(auth.AdmissionError, "AUTH_REQUIRED"):
                self.admit(headers=headers)
            compare.assert_called_once()
            self.assertEqual(compare.call_args.args[1], auth.DUMMY_VERIFIER)

    def test_registry_rotation_revocation_and_exact_expiry_prevent_cached_authority(self):
        original = self.admit()
        for changes in ({"enabled": False}, {"tenant_enabled": False}, {"revoked": True}, {"expires_at": 1000}, {"expires_at": 999}):
            self.registry.record = credential(**changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(auth.AdmissionError, "AUTH_REQUIRED"):
                self.admit()
        self.registry.record = credential(expires_at=1001)
        self.assertEqual(self.admit().owner_subject, original.owner_subject)
        new_secret = bytes([43]) * 32
        self.registry.record = credential(verifier=auth.secret_verifier(new_secret), revision="credential-2")
        with self.assertRaisesRegex(auth.AdmissionError, "AUTH_REQUIRED"):
            self.admit()
        rotated = self.admit(TOKEN[:-43] + encoded(new_secret))
        self.assertEqual(rotated.owner_subject, original.owner_subject)
        self.assertEqual(rotated.caller_id, original.caller_id)
        self.assertNotEqual(rotated.auth_revision, original.auth_revision)

    def test_scope_and_flags_prevent_reads_replay_and_artifact_bypass(self):
        for operation in QUOTE_OPERATIONS:
            with self.subTest(operation=operation):
                self.registry.lookups.clear()
                with self.assertRaisesRegex(auth.AdmissionError, "NOT_FOUND"):
                    self.admit(operation=operation, service_enabled=False, staff_enabled=True)
                self.assertEqual(self.registry.lookups, [])
                self.registry.record = credential(operations=QUOTE_OPERATIONS - {operation})
                with self.assertRaisesRegex(auth.AdmissionError, "SCOPE_DENIED"):
                    self.admit(operation=operation)
        for value in (None, "", "false", "1", "TRUE", " true", "true ", "invalid"):
            self.assertFalse(auth.flag_enabled(value))
        self.assertTrue(auth.flag_enabled("true"))

    def test_rate_denial_precedes_lookup_and_hash_preventing_unbounded_work(self):
        with mock.patch.object(auth, "secret_verifier", side_effect=AssertionError("hash after throttle")):
            with self.assertRaisesRegex(auth.AdmissionError, "RATE_LIMITED"):
                self.admit(rate_limited=lambda: True)
        self.assertEqual(self.registry.lookups, [])

    def test_unknown_operations_never_reach_registry_or_staff(self):
        for operation in (None, "admin", "launch", "replay", "CREATE", ""):
            with self.subTest(operation=operation), self.assertRaisesRegex(auth.AdmissionError, "NOT_FOUND"):
                self.admit(operation=operation)
        self.assertEqual(self.registry.lookups, [])

    def test_missing_or_failed_registry_never_falls_back_or_leaks_exception(self):
        self.admission = auth.QuoteApiAdmission()
        with self.assertRaisesRegex(auth.AdmissionError, "CAPACITY_UNAVAILABLE"):
            self.admit()
        self.admission = auth.QuoteApiAdmission(registry=mock.Mock(lookup=mock.Mock(side_effect=RuntimeError("private-canary"))))
        with self.assertRaises(auth.AdmissionError) as caught:
            self.admit()
        self.assertNotIn("private-canary", str(caught.exception))

    def test_invalid_registry_records_cannot_supply_mutable_or_short_verifiers(self):
        for changes in ({"verifier": b"short"}, {"verifier": bytearray(32)}, {"expires_at": float("nan")},
                        {"enabled": "yes"}, {"operations": {"staff_admin"}}, {"owner_subject": ""}):
            with self.subTest(fields=list(changes)), self.assertRaises(ValueError):
                credential(**changes)
        self.assertNotIn(self.registry.record.verifier.hex(), repr(self.registry.record))


class QuoteApiHttpTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, {"SQAG_DISABLE_DOTENV": "1", "APP_MODE": "local",
            auth.SERVICE_FLAG: "true", auth.STAFF_FLAG: "true"}, clear=True))
        self.logs = self.stack.enter_context(mock.patch.object(server, "write_local_log"))
        self.stack.enter_context(mock.patch.object(server, "safe_stderr"))
        server.RATE_LIMIT_BUCKETS.clear()
        server.RATE_LIMIT_OVERFLOW_BUCKETS.clear()
        self.registry = FakeRegistry()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.QuoteRunnerHandler,
            quote_api_admission=auth.QuoteApiAdmission(registry=self.registry, clock=lambda: 1000))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(3)

    def request(self, method, path, headers=(), body=None):
        if body is None:
            body = b"unread-body-canary" if method == "POST" else b""
        return http_request(self.httpd.server_port, method, path, headers, body)

    def test_service_v1_all_routes_and_replays_never_accept_or_touch_engine(self):
        before = dict(server.JOBS)
        with mock.patch.object(server.QuoteRunnerHandler, "read_json", side_effect=AssertionError("body read")), mock.patch.object(
                server, "create_job", side_effect=AssertionError("accepted work")):
            for method, path, _ in ROUTES:
                for attempt in range(2):
                    with self.subTest(method=method, path=path, attempt=attempt):
                        status, headers, raw = self.request(method, path, [(auth.SERVICE_HEADER, TOKEN), ("Idempotency-Key", "same-test-key")])
                        self.assertEqual(status, 503)
                        error = json.loads(raw)["error"]
                        self.assertEqual(error["code"], "CAPACITY_UNAVAILABLE")
                        self.assertEqual(headers.get_all("Cache-Control"), ["private, no-store"])
                        self.logs.assert_any_call("security_event", {
                            "reason": "quote_api_admission", "path": "/api/v1/admission", "status": 503,
                            "error_reference": error["reference"],
                        })
                        self.assertFalse(headers.get_all("Set-Cookie"))
                        self.assertNotIn(TOKEN.encode(), raw)
                        self.assertNotIn(self.registry.record.verifier.hex().encode(), raw)
        self.assertEqual(before, server.JOBS)
        self.assertNotIn(TOKEN, str(self.logs.call_args_list))
        self.assertNotIn("unread-body-canary", str(self.logs.call_args_list))

    def test_service_header_never_supplies_local_internal_staff_authority(self):
        routes = (("GET", "/api/session"), ("GET", "/api/settings/profiles"), ("GET", "/api/settings/pricing-references"),
                  ("GET", "/api/jobs/job-test"), ("GET", "/api/quote-sessions"), ("POST", "/api/jobs"),
                  ("POST", "/api/draft"), ("POST", "/api/generate"), ("POST", "/api/line-items/normalize"),
                  ("POST", "/api/quote-sessions/pricing-selections"), ("POST", "/api/log"),
                  ("GET", "/api/support/feedback"), ("DELETE", "/api/settings/profiles/profile-test"),
                  ("POST", "/login"), ("POST", "/logout"), ("OPTIONS", "/api/session"))
        with mock.patch.object(server.QuoteRunnerHandler, "read_json", side_effect=AssertionError("internal body")):
            for method, path in routes:
                with self.subTest(method=method, path=path):
                    self.assertEqual(self.request(method, path, [(auth.SERVICE_HEADER, TOKEN)])[0], 401)
        self.assertEqual(self.registry.lookups, [])

    def test_header_singleton_mixed_and_browser_closure_prevents_staff_fallback(self):
        invalid = (
            [(auth.SERVICE_HEADER, TOKEN), (auth.SERVICE_HEADER, TOKEN)],
            [(auth.SERVICE_HEADER, TOKEN + "," + TOKEN)], [(auth.SERVICE_HEADER, "")],
            [(auth.SERVICE_HEADER, TOKEN + " ")], [(auth.SERVICE_HEADER, TOKEN), ("Cookie", "swooshz_quote_session=invalid")],
            [(auth.SERVICE_HEADER, TOKEN), ("Cookie", "")],
        ) + tuple([(auth.SERVICE_HEADER, TOKEN), (name, value)] for name, value in (
            ("Origin", "http://localhost"), ("Referer", ""), ("Sec-Fetch-Site", "none"),
            ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "empty"), ("Sec-Fetch-User", "?1")))
        for headers in invalid:
            with self.subTest(case=invalid.index(headers)):
                self.assertEqual(self.request("GET", ROUTES[5][1], headers)[0], 401)
        self.assertEqual(self.registry.lookups, [])

    def test_flags_off_preserve_legacy_session_and_never_accept_v1(self):
        for values in ({}, {auth.SERVICE_FLAG: "invalid", auth.STAFF_FLAG: "invalid"}):
            with mock.patch.dict(os.environ, {"SQAG_DISABLE_DOTENV": "1", "APP_MODE": "local", **values}, clear=True):
                self.assertEqual(self.request("GET", "/api/session")[0], 200)
                for method, path, _ in ROUTES:
                    for headers in ([], [(auth.SERVICE_HEADER, TOKEN)]):
                        with self.subTest(method=method, path=path, auth=bool(headers)):
                            self.assertEqual(self.request(method, path, headers)[0], 404)
        self.assertEqual(self.registry.lookups, [])

    def test_host_and_deploy_guards_precede_service_lookup(self):
        for guard in ("deploy_requires_trusted_proxy_guard", "deploy_requires_auth_guard"):
            with mock.patch.object(server, guard, return_value=True):
                self.assertEqual(self.request("GET", ROUTES[5][1], [(auth.SERVICE_HEADER, TOKEN)])[0], 503)
        with mock.patch.object(server, "is_allowed_host_header", return_value=False):
            self.assertEqual(self.request("GET", ROUTES[5][1], [(auth.SERVICE_HEADER, TOKEN)])[0], 403)
        self.assertEqual(self.registry.lookups, [])

    def test_admission_budget_is_shared_across_ids_methods_and_trusted_clients(self):
        headers = [(auth.SERVICE_HEADER, TOKEN)]
        with mock.patch.dict(server.POST_RATE_LIMITS, {"/api/v1/admission": 2}):
            self.assertEqual(self.request("GET", ROUTES[5][1], headers)[0], 503)
            self.assertEqual(self.request("POST", ROUTES[0][1], headers)[0], 503)
            status, response, raw = self.request("GET", ROUTES[4][1], headers)
            self.assertEqual(status, 429)
            self.assertEqual(response["Retry-After"], "60")
            self.assertEqual(json.loads(raw)["error"]["code"], "RATE_LIMITED")
            # Untrusted forwarded headers cannot reset the direct-client bucket.
            self.assertEqual(self.request("GET", ROUTES[5][1], headers + [("X-Forwarded-For", "192.0.2.1")])[0], 429)
        self.assertEqual(len(self.registry.lookups), 2)
        server.RATE_LIMIT_BUCKETS.clear()
        with mock.patch.dict(os.environ, {"SQAG_TRUSTED_PROXY_CIDRS": "127.0.0.1/32"}), mock.patch.dict(
                server.POST_RATE_LIMITS, {"/api/v1/admission": 1}):
            first = headers + [("X-Forwarded-For", "192.0.2.1")]
            second = headers + [("X-Forwarded-For", "192.0.2.2")]
            self.assertEqual(self.request("GET", ROUTES[5][1], first)[0], 503)
            self.assertEqual(self.request("POST", ROUTES[0][1], first)[0], 429)
            self.assertEqual(self.request("GET", ROUTES[5][1], second)[0], 503)

    def test_v1_local_staff_requires_real_session_and_unknown_query_is_closed(self):
        for method, path, _ in ROUTES:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path)[0], 401)
        for suffix in ("?view=internal", "?", "?tenant_id=foreign.test"):
            self.assertEqual(self.request("GET", ROUTES[5][1] + suffix, [(auth.SERVICE_HEADER, TOKEN)])[0], 400)

    def test_platform_launch_keeps_independent_secret_owner(self):
        with mock.patch.object(server.QuoteRunnerHandler, "handle_platform_launch") as launch:
            # Adapter-only dispatch proof; existing launch suites exercise verification.
            launch.side_effect = lambda: None
            handler = object.__new__(server.QuoteRunnerHandler)
            handler.command = "POST"
            handler.headers = Message()
            handler.headers[auth.SERVICE_HEADER] = TOKEN
            self.assertFalse(handler.dispatch_quote_admission(server.urlparse(server.PLATFORM_LAUNCH_ENDPOINT)))
            launch.assert_not_called()

    def test_v1_access_logs_drop_all_submitted_ids_and_query_canaries(self):
        for method, path, operation in ROUTES:
            for suffix in ("?notes=" + TOKEN, "?" + TOKEN + "=private-canary", "#" + TOKEN):
                projected = server.redact_request_line_for_log(method + " " + path + suffix + " HTTP/1.1")
                self.assertEqual(projected, method + " /api/v1/" + operation + " HTTP/1.1")
                self.assertNotIn(TOKEN, projected)


class QuoteApiStaffHttpTest(unittest.TestCase):
    def test_real_staff_sessions_flags_csrf_and_mixed_auth_prevent_authority_promotion(self):
        fixture = staff_fixtures.InternalPasswordAuthWebappTest(methodName="runTest")
        fixture.setUp(extra_env={auth.STAFF_FLAG: "true", auth.SERVICE_FLAG: "true"})
        self.addCleanup(fixture.doCleanups)
        fixture.stack.enter_context(mock.patch.object(server, "is_rate_limited", return_value=False))
        fixture.stack.enter_context(mock.patch.object(server, "safe_stderr"))
        fixture.server.quote_api_admission = auth.QuoteApiAdmission(registry=FakeRegistry(), clock=lambda: 1000)
        status, headers, _ = fixture.login()
        self.assertEqual(status, 302)
        cookie = server.SESSION_COOKIE_NAME + "=" + fixture.cookie(headers, server.SESSION_COOKIE_NAME)
        _, _, raw = fixture.http("GET", "/api/session", {"Cookie": cookie})
        session = json.loads(raw)
        staff_headers = {"Cookie": cookie, "Origin": staff_fixtures.internal_env()["SQAG_PUBLIC_BASE_URL"],
                         session["csrf_header"]: session["csrf_token"]}
        def request(method, path, values, body=None):
            return http_request(fixture.server.server_port, method, path, list(values.items()),
                (body or "").encode("utf-8"), server.urlparse(staff_headers["Origin"]).netloc)
        for method, path, _ in ROUTES:
            with self.subTest(method=method, path=path):
                body = "{}" if method == "POST" else None
                self.assertEqual(request(method, path, staff_headers, body)[0], 503)
                mixed = {**staff_headers, auth.SERVICE_HEADER: TOKEN}
                self.assertEqual(request(method, path, mixed, body)[0], 401)
        for change in ({session["csrf_header"]: "wrong"}, {"Origin": "https://foreign.example.test"},
                       {"Referer": "https://foreign.example.test"}):
            self.assertEqual(request("POST", ROUTES[0][1], {**staff_headers, **change}, "{}")[0], 403)
        status, second_headers, _ = fixture.login()
        self.assertEqual(status, 302)
        second_cookie = server.SESSION_COOKIE_NAME + "=" + fixture.cookie(second_headers, server.SESSION_COOKIE_NAME)
        self.assertEqual(request("POST", ROUTES[0][1], {**staff_headers, "Cookie": second_cookie}, "{}")[0], 403)
        with mock.patch.dict(os.environ, {auth.SERVICE_FLAG: "false"}):
            self.assertEqual(fixture.http("GET", ROUTES[5][1], {"Cookie": cookie})[0], 503)
        with mock.patch.dict(os.environ, {auth.STAFF_FLAG: "false"}):
            self.assertEqual(fixture.http("GET", ROUTES[5][1], {"Cookie": cookie})[0], 404)
        opaque = server.verified_cookie_payload(fixture.cookie(headers, server.SESSION_COOKIE_NAME))
        admitted = fixture.server.internal_auth.validate_session(opaque,
            policy_fingerprint=fixture.server.internal_policy.fingerprint, current_mode=server.INTERNAL_AUTH_MODE)
        handler = object.__new__(server.QuoteRunnerHandler)
        handler.current_auth_session = lambda: admitted
        context = handler.current_quote_staff_context("ERR-0123ABCD")
        self.assertEqual(context.owner_subject, admitted["user"]["subject"])
        self.assertEqual(context.workspace_id, admitted["user"]["account"])
        fixture.server.internal_auth.revoke_session(admitted["session_id"])
        self.assertEqual(fixture.http("GET", ROUTES[5][1], {"Cookie": cookie})[0], 401)


if __name__ == "__main__":
    unittest.main()
