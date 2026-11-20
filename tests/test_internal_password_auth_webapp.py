"""Synthetic actual HTTP checks for the internal password boundary; no provider calls."""
import contextlib
import http.client
import http.cookies
import json
import os
import re
import socket
import threading
import unittest
import urllib.parse
from unittest import mock
from types import SimpleNamespace
from webapp import server as webapp
from webapp import internal_password_auth as auth
from tests.test_internal_password_auth import email, policy_values, password_for

ORIGIN = "https://internal-alpha.example.test"

def internal_env():
    values=policy_values()
    return {"SQAG_DISABLE_DOTENV":"1", "APP_MODE":"deploy", "AUTH_REQUIRED":"true", "SQAG_AUTH_MODE":"internal_password",
            "SESSION_SECRET":"synthetic-session-secret-with-enough-entropy", "SQAG_TRUSTED_PROXY_CIDRS":"127.0.0.1/32",
            "SQAG_PLATFORM_LAUNCH_MODE":"disabled", "SQAG_PUBLIC_BASE_URL":ORIGIN,
            "SQAG_INTERNAL_WORKSPACE_ID":values["workspace_id"], "SQAG_INTERNAL_ALLOWED_EMAILS":values["allowed_emails"],
            "SQAG_INTERNAL_ALLOWED_EMAILS_ADMIN":values["allowed_admin_emails"],
            "SQAG_INTERNAL_ADMIN_LOGIN_PASSWORD":values["admin_password"],
            "SQAG_INTERNAL_IDENTITY_BINDINGS_JSON":values["bindings_json"], "SQAG_INTERNAL_LOGIN_PASSWORD":values["password"]}

class OptionalIdentityBindingsDeployTest(unittest.TestCase):
    def deploy_env(self):
        return internal_env() | {
            "SQAG_TRACKING_HMAC_KEY": "synthetic-tracking-key",
            "SQAG_TRACKING_HMAC_KEY_VERSION": "synthetic-v1",
            "SQAG_STORAGE_MODE": "database", "SQAG_ARTIFACT_STORAGE_MODE": "object",
            "SQAG_DATABASE_URL": "postgresql://runtime:synthetic@127.0.0.1/sqag_test",
            "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "http://127.0.0.1:9000",
            "SQAG_OBJECT_STORAGE_BUCKET": "synthetic-bucket",
            "SQAG_OBJECT_STORAGE_REGION": "synthetic-region",
            "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
            "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-secret-key",
            **dict.fromkeys(("QUOTE_DATA_ROOT", "QUOTE_OUTPUT_ROOT", "QUOTE_TMP_ROOT", "QUOTE_LOG_ROOT"),
                            "synthetic-host-runtime-root"),
        }

    @contextlib.contextmanager
    def offline_dependencies(self, env):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
            stack.enter_context(mock.patch.object(webapp, "postgres_driver_available", return_value=True))
            stack.enter_context(mock.patch.object(webapp, "configured_object_storage_status", return_value={
                "provider": "s3_compatible", "runtime_backend_available": True, "blockers": [],
            }))
            stack.enter_context(mock.patch.object(webapp, "path_is_outside_project", return_value=True))
            yield

    def assert_startup(self, ready):
        # Exercise main's real auth guard; replace all later dependency/filesystem/server effects.
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(webapp, "parse_args", return_value=SimpleNamespace(
                check_deploy_uat_env=False, check_production_readiness=False, host="127.0.0.1", port=0)))
            stack.enter_context(mock.patch.object(webapp, "deploy_requires_storage_guard", return_value=False))
            health = stack.enter_context(mock.patch.object(webapp, "health_status", return_value={"status": "ok"}))
            for name in ("configured_output_root", "configured_tmp_root", "reconcile_forensic_runs_on_startup",
                         "safe_stdout", "safe_stderr"):
                stack.enter_context(mock.patch.object(webapp, name))
            factory = stack.enter_context(mock.patch.object(webapp, "ThreadingHTTPServer"))
            self.assertEqual(webapp.main(), 0 if ready else 2)
            if ready:
                factory.assert_called_once()
                factory.return_value.serve_forever.assert_called_once()
                factory.return_value.server_close.assert_called_once()
                health.assert_called_once_with(force_dependency_probe=True)
            else:
                factory.assert_not_called()
                health.assert_not_called()

    def test_absent_empty_and_empty_array_bindings_pass_preflight_and_startup(self):
        fingerprints = set()
        for label, value in (("absent", None), ("empty", ""), ("empty-array", "[]")):
            env = self.deploy_env()
            key = webapp.INTERNAL_IDENTITY_BINDINGS_ENV_NAME
            env.pop(key)
            if value is not None:
                env[key] = value
            with self.subTest(binding=label), self.offline_dependencies(env):
                self.assertEqual(key in os.environ, value is not None)
                self.assertEqual(webapp.deploy_uat_preflight_status()["status"], "ready")
                self.assertFalse(webapp.deploy_requires_auth_guard())
                identities, _ = auth.InternalAuthPolicy.validate_values(**webapp.internal_auth_policy_values())
                self.assertEqual({i.email: (i.user_id, i.role) for i in identities}, {
                    email(): (auth.stable_alias_id(env["SQAG_INTERNAL_WORKSPACE_ID"], email()), "admin"),
                    email("operator"): (auth.stable_alias_id(env["SQAG_INTERNAL_WORKSPACE_ID"], email("operator")), "operator"),
                })
                fingerprints.add(auth.InternalAuthPolicy.fingerprint_for_values(**webapp.internal_auth_policy_values()))
                self.assert_startup(True)
        self.assertEqual(len(fingerprints), 1)

    def test_supplied_legacy_bindings_keep_ids_and_invalid_bindings_block_startup(self):
        rows = json.loads(policy_values()["bindings_json"])
        cases = (("legacy", json.dumps(rows), True), ("malformed", "{", False),
                 ("duplicate", json.dumps(rows + [rows[0]]), False),
                 ("oversized", " " * (auth.MAX_INTERNAL_IDENTITIES_JSON_BYTES + 1), False))
        for label, value, ready in cases:
            with self.subTest(binding=label), self.offline_dependencies(
                self.deploy_env() | {webapp.INTERNAL_IDENTITY_BINDINGS_ENV_NAME: value}
            ):
                self.assertEqual(webapp.deploy_uat_preflight_status()["status"], "ready" if ready else "blocked")
                self.assertEqual(webapp.deploy_requires_auth_guard(), not ready)
                if ready:
                    identities, _ = auth.InternalAuthPolicy.validate_values(**webapp.internal_auth_policy_values())
                    self.assertEqual({i.email: i.user_id for i in identities}, {r["email"]: r["user_id"] for r in rows})
                self.assert_startup(ready)

    def test_platform_mode_still_rejects_nonempty_internal_bindings(self):
        env = self.deploy_env()
        for key in tuple(env):
            if key.startswith("SQAG_INTERNAL_"):
                del env[key]
        env.update(SQAG_AUTH_MODE="platform", SQAG_PLATFORM_LAUNCH_MODE="platform",
                   SQAG_PUBLIC_BASE_URL=webapp.PRODUCTION_SQAG_ORIGIN,
                   SQAG_PLATFORM_BASE_URL=webapp.PRODUCTION_PLATFORM_ORIGIN,
                   SQAG_PLATFORM_SERVICE_SECRET="synthetic-platform-service-secret-with-enough-entropy")
        for value, ready in ((None, True), ("", True), ("[]", False), (policy_values()["bindings_json"], False)):
            candidate = dict(env)
            if value is not None:
                candidate[webapp.INTERNAL_IDENTITY_BINDINGS_ENV_NAME] = value
            with self.subTest(binding="absent" if value is None else "supplied"), self.offline_dependencies(candidate):
                self.assertEqual(webapp.deploy_uat_preflight_status()["status"], "ready" if ready else "blocked")
                self.assertEqual(webapp.deploy_requires_auth_guard(), not ready)
                self.assert_startup(ready)


class InternalPasswordAuthWebappTest(unittest.TestCase):
    def setUp(self, extra_env=None):
        self.stack=contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, internal_env() | (extra_env or {}), clear=True))
        self.logs=self.stack.enter_context(mock.patch.object(webapp,"write_local_log"))
        webapp.RATE_LIMIT_BUCKETS.clear(); webapp.RATE_LIMIT_OVERFLOW_BUCKETS.clear()
        self.server=webapp.ThreadingHTTPServer(("127.0.0.1",0),webapp.QuoteRunnerHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)

    @contextlib.contextmanager
    def fresh_server_if(self, changes):
        if not changes:
            yield
            return
        original, original_thread = self.server, self.thread
        self.server = webapp.ThreadingHTTPServer(("127.0.0.1", 0), webapp.QuoteRunnerHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        try:
            yield
        finally:
            self.stop()
            self.server, self.thread = original, original_thread

    def test_pricing_management_role_and_cross_password_matrix(self):
        routes = (
            ("GET", "/api/settings/pricing-references"),
            ("GET", "/api/settings/pricing-references/example"),
            ("GET", "/api/settings/pricing-references/example/export.xlsx"),
            ("GET", "/api/pricing-reference/template.xlsx"),
            ("POST", "/api/pricing-reference/validate"),
            ("POST", "/api/settings/pricing-references/import-preview"),
            ("POST", "/api/settings/pricing-references"),
            ("DELETE", "/api/settings/pricing-references/example?source=company"),
        )
        for alias in (email(), email("operator")):
            admin = alias == email()
            with mock.patch.object(auth.hashlib, "pbkdf2_hmac", wraps=auth.hashlib.pbkdf2_hmac) as derive:
                status, _, body = self.login(alias, password_for(email("operator") if admin else email()))
                self.assertEqual(status, 403)
                reference = re.search(rb"reference (ERR-[0-9A-F]{8})", body)
                self.assertIsNotNone(reference)
                self.logs.assert_any_call("security_event", {
                    "reason": "login_denied", "path": "/login", "status": 403,
                    "error_reference": reference.group(1).decode("ascii"),
                })
                status, headers, _ = self.login(alias)
                self.assertEqual(status, 302)
                self.assertEqual(derive.call_count, 2)
            cookie = webapp.SESSION_COOKIE_NAME + "=" + self.cookie(headers, webapp.SESSION_COOKIE_NAME)
            with mock.patch.object(auth.hashlib, "pbkdf2_hmac", side_effect=AssertionError("request KDF")):
                status, _, body = self.http("GET", "/api/session", {"Cookie": cookie})
                self.assertEqual(status, 200)
            session = json.loads(body)
            self.assertTrue(session["permissions"]["canSelectPricingReference"])
            self.assertTrue(session["permissions"]["canGenerateQuote"])
            headers = {"Cookie": cookie, "Origin": ORIGIN, "Content-Type": "application/json",
                       session["csrf_header"]: session["csrf_token"]}
            # Prove the HTTP permission decision before any storage or provider effect.
            with mock.patch.object(webapp.QuoteRunnerHandler, "current_app_storage", return_value=None), mock.patch.object(
                webapp, "pricing_reference_import_preview", return_value={}
            ):
                if admin:
                    self.assertEqual(self.http("GET", routes[3][1], headers)[0], 200)
                    self.assertEqual(self.http("POST", routes[5][1], headers, "{}")[0], 200)
                else:
                    for method, route in routes:
                        with self.subTest(method=method, route=route):
                            self.assertEqual(self.http(method, route, headers, "{}" if method == "POST" else None)[0], 403)

    def http(self, method, path, headers=None, body=None):
        conn=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        conn.request(method,path,body=body,headers={"Host":urllib.parse.urlparse(ORIGIN).netloc,**(headers or {})})
        response=conn.getresponse(); result=response.status,response.headers,response.read(); conn.close(); return result

    def cookie(self,headers,name):
        for raw in headers.get_all("Set-Cookie",[]):
            cookie=http.cookies.SimpleCookie(raw)
            if name in cookie: return cookie[name].value
        return ""

    def start(self):
        status,headers,body=self.http("GET","/login"); self.assertEqual(status,200)
        nonce=re.search(rb'name="nonce" value="([^"]+)"',body).group(1).decode()
        return nonce,"sqag_login_binding="+self.cookie(headers,"sqag_login_binding")

    def login(self, alias=None, password=None, previous=""):
        nonce,cookie=self.start()
        body=urllib.parse.urlencode(dict(nonce=nonce,email=alias or email(),password=password if password is not None else password_for(alias or email())))
        status,headers,response=self.http("POST","/login",dict(Origin=ORIGIN,Cookie=cookie+("; "+previous if previous else ""),**{"Content-Type":"application/x-www-form-urlencoded"}),body)
        return status,headers,response

    def test_A01_A19_incomplete_mixed_and_old_mode_fail_closed(self):
        for key in ["SQAG_INTERNAL_ADMIN_LOGIN_PASSWORD","SQAG_INTERNAL_LOGIN_PASSWORD","SQAG_INTERNAL_WORKSPACE_ID","SESSION_SECRET","SQAG_TRUSTED_PROXY_CIDRS"]:
            with self.subTest(key=key), mock.patch.dict(os.environ,{key:""}): self.assertFalse(webapp.internal_password_config_complete())
        for key in ["OIDC_CLIENT_ID", "SQAG_PLATFORM_SERVICE_SECRET"]:
            with self.subTest(key=key),mock.patch.dict(os.environ,{key:"synthetic-unused-value"}): self.assertFalse(webapp.internal_password_config_complete())
        retired_keys = ("SQAG_INTERNAL_GOOGLE_IDENTITIES_JSON", "SQAG_INTERNAL_ADMIN_EMAILS",
                        "SQAG_INTERNAL_OPERATOR_EMAILS")
        with mock.patch.dict(os.environ, dict.fromkeys(retired_keys, "synthetic-obsolete-setting")):
            self.assertTrue(webapp.internal_password_config_complete())
            self.assertEqual(self.login()[0], 302)
        with mock.patch.dict(os.environ,{"SQAG_AUTH_MODE":"internal_google"}): self.assertTrue(webapp.deploy_requires_auth_guard())
        self.assertEqual(self.http("GET","/callback?code=synthetic")[0],404)

    def test_A06_A09_A10_A11_A13_A14_real_login_rotation_and_logout(self):
        self.assertEqual(self.http("GET","/")[0],401)
        for alias,password in [(email(),"wrong-password-value"),(email("unknown"),policy_values()["password"])]:
            self.assertEqual(self.login(alias,password)[0],403)
        for alias in [email(),email("operator")]:
            status,headers,body=self.login(alias); self.assertEqual(status,302)
            raw=self.cookie(headers,webapp.SESSION_COOKIE_NAME); cookie=webapp.SESSION_COOKIE_NAME+"="+raw
            self.assertNotIn(email().encode(),raw.encode())
            cookie_header=next(h for h in headers.get_all("Set-Cookie") if h.startswith(webapp.SESSION_COOKIE_NAME+"="))
            for flag in ["Secure","HttpOnly","SameSite=Lax","Path=/"]: self.assertIn(flag,cookie_header)
            self.assertNotIn("Domain=",cookie_header)
            status,_,data=self.http("GET","/api/session",dict(Cookie=cookie)); self.assertEqual(status,200)
            session=json.loads(data); self.assertIsNone(session["user"])
            self.assertTrue(session["authenticated"])
            self.assertEqual(self.http("GET","/logout",dict(Cookie=cookie))[0],405)
            self.assertEqual(self.http("POST","/logout",dict(Cookie=cookie,Origin=ORIGIN))[0],403)
            status, logout_headers, _ = self.http("POST", "/logout", dict(
                Cookie=cookie, Origin=ORIGIN, **{session["csrf_header"]: session["csrf_token"]}))
            self.assertEqual(status, 204)
            for name in (webapp.SESSION_COOKIE_NAME, webapp.OIDC_STATE_COOKIE_NAME):
                self.assertTrue(any(value.startswith(name + "=") and "Max-Age=0" in value
                                    for value in logout_headers.get_all("Set-Cookie", [])))
            self.assertEqual(self.http("GET","/api/session",dict(Cookie=cookie))[0],401)
        status,headers,_=self.login(); first=webapp.SESSION_COOKIE_NAME+"="+self.cookie(headers,webapp.SESSION_COOKIE_NAME)
        status,headers,_=self.login(previous=first); self.assertEqual(status,302)
        self.assertEqual(self.http("GET","/api/session",dict(Cookie=first))[0],401)

    def test_A07_A08_A12_guards_precede_verification(self):
        verify=mock.Mock(side_effect=AssertionError("credential verification must not run"))
        with mock.patch.object(self.server.internal_auth,"authenticate",verify), mock.patch.object(
            auth.hashlib, "pbkdf2_hmac", side_effect=AssertionError("credential derivation must not run")
        ) as derive:
            for headers,body in [({},""),({"Origin":"https://other.example.test"},""),({"Origin":ORIGIN,"Content-Type":"application/json"},"{}"),({"Origin":ORIGIN,"Content-Type":"application/x-www-form-urlencoded"},"email=x&email=y&password=z&nonce=n"),({"Origin":ORIGIN,"Content-Type":"application/x-www-form-urlencoded"},"nonce=bad&email=x&password=z")]:
                with self.subTest(index=len(body)): self.assertIn(self.http("POST","/login",headers,body)[0],[403,429])
            nonce,cookie=self.start()
            body=urllib.parse.urlencode(dict(nonce=nonce,email=email(),password=password_for(email())))
            with mock.patch.object(webapp,"is_rate_limited",return_value=True):
                self.assertEqual(self.http("POST","/login",dict(Origin=ORIGIN,Cookie=cookie,**{"Content-Type":"application/x-www-form-urlencoded"}),body)[0],429)
        verify.assert_not_called()
        derive.assert_not_called()
        self.assertEqual(self.http("POST","/login",{"Host":"other.example.test"})[0],403)

    def test_A06_GET_nonce_rate_limit_precedes_allocation_and_is_separate_from_POST(self):
        begin=self.server.internal_auth.begin_login
        with mock.patch.object(self.server.internal_auth,"begin_login",wraps=begin) as allocate:
            budget=webapp.POST_RATE_LIMITS["/login/nonce"]
            first_nonce=first_cookie=""
            for index in range(budget):
                nonce,cookie=self.start()
                if index==0:first_nonce,first_cookie=nonce,cookie
            self.assertEqual(self.http("GET","/login")[0],429)
            self.assertEqual(allocate.call_count,budget)
            # A different trusted-proxy-resolved client still gets a form.
            self.assertEqual(self.http("GET","/login",{"X-Forwarded-For":"192.0.2.2"})[0],200)
            self.assertEqual(allocate.call_count,budget+1)
            # Issuance exhaustion does not consume the password-attempt bucket.
            body=urllib.parse.urlencode(dict(nonce=first_nonce,email=email(),password=password_for(email())))
            status,_,_=self.http("POST","/login",dict(Origin=ORIGIN,Cookie=first_cookie,**{"Content-Type":"application/x-www-form-urlencoded"}),body)
            self.assertEqual(status,302)
        with mock.patch.object(self.server.internal_auth,"begin_login",side_effect=AssertionError("allocation must not run")),mock.patch.object(webapp,"is_rate_limited",return_value=True):
            self.assertEqual(self.http("GET","/login")[0],429)

    def test_A08_login_url_logs_keep_only_canonical_effective_route(self):
        targets=(
            "/login?unknown_name=PRIVATE_QUERY_VALUE",
            "/login?PRIVATE_QUERY_NAME=value",
            "/login?email=PRIVATE_EMAIL&password=PRIVATE_PASSWORD",
            "/login?%50RIVATE_QUERY_NAME=%50RIVATE_QUERY_VALUE",
            "/login#PRIVATE_FRAGMENT",
            "https://PRIVATE_USER:PRIVATE_PASSWORD@private.example.test/login?PRIVATE_NAME=PRIVATE_VALUE#PRIVATE_FRAGMENT",
            "https://PRIVATE_USER:PRIVATE_PASSWORD@private.example.test/login#PRIVATE_FRAGMENT",
            "//login?PRIVATE_NAME=PRIVATE_VALUE",
            "///login#PRIVATE_FRAGMENT",
        )
        for method in ("GET", "POST"):
            for target in targets:
                with self.subTest(method=method, target=target):
                    self.assertEqual(webapp.redact_request_target_for_log(target),"/login")
                    self.assertEqual(webapp.redact_request_line_for_log(method+" "+target+" HTTP/1.1"),method+" /login HTTP/1.1")
        for target in ("/api/session?view=summary", "//api/session?view=summary", "https://other.example.test/api/session?view=summary#section"):
            self.assertEqual(webapp.redact_request_target_for_log(target),target)
        self.assertNotIn("PRIVATE_CALLBACK",webapp.redact_request_target_for_log("/callback?code=PRIVATE_CALLBACK"))
        # Use the actual installed Python handler, including its request-target normalisation.
        for target in targets:
            with self.subTest(actual_target=target),mock.patch.object(webapp,"safe_stderr") as stderr:
                status,_,_=self.http("GET",target)
            self.assertEqual(status,400 if "?" in target else 200)
            self.assertNotIn("PRIVATE",str(stderr.call_args_list)+str(self.logs.call_args_list))

    def test_A15_policy_password_change_and_old_cookie_refused(self):
        _,headers,_=self.login(); cookie=webapp.SESSION_COOKIE_NAME+"="+self.cookie(headers,webapp.SESSION_COOKIE_NAME)
        for alias, key in ((email(), "SQAG_INTERNAL_ADMIN_LOGIN_PASSWORD"),
                           (email("operator"), "SQAG_INTERNAL_LOGIN_PASSWORD")):
            changes = {key: "rotated-synthetic-password"}
            with self.subTest(key=key), mock.patch.dict(os.environ, changes):
                self.assertEqual(self.http("GET", "/api/session", {"Cookie": cookie})[0], 401)
                self.assertEqual(self.login(alias, changes[key])[0], 403)
                with self.fresh_server_if(changes):
                    self.assertEqual(self.login(alias)[0], 403)
                    self.assertEqual(self.login(alias, changes[key])[0], 302)
        old=webapp.signed_cookie_value({"auth_mode":"internal_google","user":{"subject":"stable-google-subject"}})
        self.assertEqual(self.http("GET","/api/session",dict(Cookie=webapp.SESSION_COOKIE_NAME+"="+old))[0],401)

    def test_retired_callback_refuses_without_provider_calls_or_session_cookies(self):
        with mock.patch.object(webapp.urllib.request, "urlopen", side_effect=AssertionError("provider call")) as provider:
            for target in ("/callback", "/callback?state=old-state&code=old-code", "/callback?error=access_denied"):
                with self.subTest(target=target):
                    status, headers, body = self.http("GET", target)
                    self.assertEqual(status, 404)
                    self.assertEqual(json.loads(body), {"error": "Not found"})
                    self.assertFalse(headers.get_all("Set-Cookie"))
            # The existing host guard refuses invalid deploy configuration first.
            for changes in ({"SQAG_TRUSTED_PROXY_CIDRS": ""}, {"OIDC_CLIENT_ID": "retired-client"}):
                with self.subTest(changes=changes), mock.patch.dict(os.environ, changes):
                    self.assertEqual(self.http("GET", "/callback?code=old-code")[0], 403)
                    # Exercise the retained callback wrapper after host admission too.
                    with mock.patch.object(webapp, "is_allowed_host_header", return_value=True):
                        self.assertEqual(self.http("GET", "/callback?code=old-code")[0], 503)
        provider.assert_not_called()

    def test_retired_settings_cannot_enable_provider_login(self):
        retired = {
            "OIDC_ISSUER_URL": "https://issuer.example.test",
            "OIDC_CLIENT_ID": "synthetic-client", "OIDC_CLIENT_SECRET": "synthetic-secret",
            "OIDC_REDIRECT_URI": ORIGIN + "/callback",
            "OIDC_AUTHORIZE_URL": "https://issuer.example.test/authorize",
            "OIDC_TOKEN_URL": "https://issuer.example.test/token",
            "OIDC_USERINFO_URL": "https://issuer.example.test/userinfo",
            "OIDC_LOGOUT_URL": "https://issuer.example.test/logout",
            "AUTH_ALLOWED_EMAILS": "synthetic@example.test", "AUTH_ALLOWED_DOMAINS": "example.test",
            "AUTH_ALLOW_ANY_AUTHENTICATED_USER": "true", "AUTH_APPROVED_TESTER_ROLE": "admin",
        }
        with mock.patch.object(webapp.urllib.request, "urlopen", side_effect=AssertionError("provider call")) as provider:
            for key, value in retired.items():
                with self.subTest(key=key), mock.patch.dict(os.environ, {key: value}):
                    self.assertFalse(webapp.internal_password_config_complete())
                    for route in ("/login", "/callback?state=old&code=old"):
                        self.assertEqual(self.http("GET", route)[0], 403)
            local = internal_env() | retired | {"APP_MODE": "local", "SQAG_AUTH_MODE": "local", "USER_TYPE": "ADMIN"}
            with mock.patch.dict(os.environ, local, clear=True):
                for route in ("/login", "/callback?state=old&code=old"):
                    status, headers, body = self.http("GET", route, {"Host": "127.0.0.1"})
                    self.assertEqual(status, 404)
                    self.assertEqual(json.loads(body), {"error": "Not found"})
                    self.assertFalse(headers.get_all("Set-Cookie"))
        provider.assert_not_called()

    def test_legacy_cookie_identity_and_local_session_boundaries_are_unchanged(self):
        legacy_user = {"subject": "stable-google-subject", "email": email(),
                       "name": "Synthetic legacy user", "account": "legacy-account"}
        for mode in ("internal_google", "internal_password", None):
            payload = {"user": legacy_user}
            if mode:
                payload["auth_mode"] = mode
            cookie = webapp.SESSION_COOKIE_NAME + "=" + webapp.signed_cookie_value(payload)
            with self.subTest(mode=mode):
                self.assertEqual(self.http("GET", "/api/session", {"Cookie": cookie})[0], 401)
                self.assertIsNone(webapp.session_from_cookie_header(cookie, auth_state=self.server.internal_auth))
                self.assertEqual(webapp.csrf_token_for_cookie_header(cookie, auth_state=self.server.internal_auth),
                                 webapp.configured_csrf_token())
            with mock.patch.dict(os.environ, {"APP_MODE": "local", "SQAG_AUTH_MODE": "local", "USER_TYPE": "ADMIN"}):
                accepted = webapp.session_from_cookie_header(cookie)
                if mode:
                    self.assertIsNone(accepted)
                else:
                    self.assertEqual(accepted["user"], legacy_user)
                    self.assertTrue(webapp.permissions_for_auth_session(accepted)["canGenerateQuote"])


ENDED_NOTE = "Your session has ended. Sign in again to continue."
STANDARD_NOTE = "Enter your approved staff email and the password for your role."
HOUR = 60 * 60
# 400 days, the longest lifetime browsers keep (RFC 6265bis), so an ended session's
# cookie still reaches the server however long staff stay away.
EXPECTED_COOKIE_MAX_AGE = 400 * 24 * HOUR


class InternalPasswordIdleSessionWebappTest(unittest.TestCase):
    """#251 over real HTTP: idle sign-out, cookie renewal, ended-session note, sign_in_id."""
    http = InternalPasswordAuthWebappTest.http
    cookie = InternalPasswordAuthWebappTest.cookie
    start = InternalPasswordAuthWebappTest.start
    login = InternalPasswordAuthWebappTest.login
    stop = InternalPasswordAuthWebappTest.stop

    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, internal_env(), clear=True))
        self.logs = self.stack.enter_context(mock.patch.object(webapp, "write_local_log"))
        self.reset_rate_limits()
        self.now = [1_800_000_000]
        self.server = webapp.ThreadingHTTPServer(
            ("127.0.0.1", 0), webapp.QuoteRunnerHandler,
            internal_auth=auth.InternalAuthState(clock=lambda: self.now[0]))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)

    def reset_rate_limits(self):
        webapp.RATE_LIMIT_BUCKETS.clear(); webapp.RATE_LIMIT_OVERFLOW_BUCKETS.clear()

    def advance(self, seconds):
        self.now[0] += seconds

    def signed_in(self, alias=None, previous=""):
        status, headers, _ = self.login(alias, previous=previous)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/")
        return webapp.SESSION_COOKIE_NAME + "=" + self.cookie(headers, webapp.SESSION_COOKIE_NAME), headers

    def session_cookie_headers(self, headers):
        return [h for h in headers.get_all("Set-Cookie", []) if h.startswith(webapp.SESSION_COOKIE_NAME + "=")]

    def assert_secure_session_cookie(self, header, value):
        parsed = http.cookies.SimpleCookie(header)[webapp.SESSION_COOKIE_NAME]
        self.assertEqual(parsed.value, value)
        self.assertEqual(parsed["max-age"], str(EXPECTED_COOKIE_MAX_AGE))
        self.assertEqual(parsed["path"], "/")
        self.assertEqual(parsed["samesite"], "Lax")
        self.assertTrue(parsed["httponly"])
        self.assertTrue(parsed["secure"])
        self.assertEqual(parsed["domain"], "")
        self.assertNotIn("Domain=", header)

    def session_json(self, cookie):
        status, headers, body = self.http("GET", "/api/session", {"Cookie": cookie})
        return status, headers, json.loads(body)

    def login_page_message(self, cookie=None):
        self.reset_rate_limits()
        status, headers, body = self.http("GET", "/login", {"Cookie": cookie} if cookie else {})
        self.assertEqual(status, 200)
        self.assertEqual(self.session_cookie_headers(headers), [])
        message = re.search(rb'<p id="authPageMessage">([^<]*)</p>', body).group(1).decode()
        self.assertIn(b'name="nonce"', body)
        return message

    def test_H1_login_cookie_lifetime_and_flags(self):
        cookie, headers = self.signed_in()
        [header] = self.session_cookie_headers(headers)
        self.assert_secure_session_cookie(header, cookie.split("=", 1)[1])
        tampered = cookie[:-2] + ("AA" if not cookie.endswith("AA") else "BB")
        self.assertEqual(self.http("GET", "/api/session", {"Cookie": tampered})[0], 401)

    def test_H2_use_keeps_session_and_renews_identical_cookie(self):
        cookie, _ = self.signed_in()
        status, headers, first = self.session_json(cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.session_cookie_headers(headers), [])  # just issued
        for step in range(1, 7):  # used once every 20 h for 120 h in total
            self.advance(20 * HOUR)
            status, headers, current = self.session_json(cookie)
            with self.subTest(hours=20 * step):
                self.assertEqual(status, 200)
                self.assertTrue(current["authenticated"])
                # CSRF tokens never rotate mid-session: the cookie value is unchanged.
                self.assertEqual(current["csrf_token"], first["csrf_token"])
                [renewal] = self.session_cookie_headers(headers)
                self.assert_secure_session_cookie(renewal, cookie.split("=", 1)[1])
        # Renewal is throttled: no header again within five minutes.
        self.advance(299)
        status, headers, _ = self.session_json(cookie)
        self.assertEqual((status, self.session_cookie_headers(headers)), (200, []))
        self.advance(1)
        status, headers, _ = self.http("GET", "/", {"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.session_cookie_headers(headers)), 1)

    def test_H3_H4_idle_session_ends_with_auth_required_and_no_renewal(self):
        cookie, _ = self.signed_in()
        self.advance(48 * HOUR)
        status, _, session = self.session_json(cookie)
        self.assertEqual(status, 200)
        self.advance(72 * HOUR - 1)
        self.assertEqual(self.session_json(cookie)[0], 200)
        self.advance(72 * HOUR)
        headers = {"Cookie": cookie, "Origin": ORIGIN, session["csrf_header"]: session["csrf_token"]}
        with mock.patch.object(webapp.QuoteRunnerHandler, "current_app_storage",
                               side_effect=AssertionError("storage must not be reached")):
            for method, path in (("DELETE", "/api/quote-sessions/quote-idle-fixture"),
                                 ("GET", "/api/session"), ("POST", "/api/quote-sessions")):
                with self.subTest(method=method, path=path):
                    status, response_headers, body = self.http(method, path, headers, "{}" if method == "POST" else None)
                    self.assertEqual(status, 401)
                    self.assertEqual(json.loads(body)["status"], "auth_required")
                    self.assertEqual(json.loads(body)["login_url"], "/login")
                    self.assertEqual(self.session_cookie_headers(response_headers), [])
        status, response_headers, body = self.http("GET", "/", {"Cookie": cookie})
        self.assertEqual(status, 401)
        self.assertIn(b"Sign in required", body)
        self.assertEqual(self.session_cookie_headers(response_headers), [])

    def test_H5_login_page_note_matrix(self):
        active, _ = self.signed_in()
        idle, _ = self.signed_in(email("operator"))
        revoked, _ = self.signed_in()
        restart_lost, _ = self.signed_in(email("operator"))
        status, _, session = self.session_json(revoked)
        self.assertEqual(self.http("POST", "/logout", {"Cookie": revoked, "Origin": ORIGIN,
                                                      session["csrf_header"]: session["csrf_token"]})[0], 204)
        self.server.internal_auth._sessions.pop(
            webapp.verified_cookie_payload(restart_lost.split("=", 1)[1], allow_expired=True)["session_id"])
        self.advance(71 * HOUR)
        self.assertEqual(self.session_json(active)[0], 200)
        self.advance(1 * HOUR)
        forged_payload = webapp.b64url_encode(json.dumps(
            {"auth_mode": "internal_password", "session_id": "f" * 43, "exp": 0}).encode())
        other_mode = webapp.signed_cookie_value({"auth_mode": "internal_google", "user": {"subject": "stable-google-subject"}})
        cases = (
            ("active session", active, STANDARD_NOTE),
            ("idle 72 h", idle, ENDED_NOTE),
            ("signed out elsewhere", revoked, ENDED_NOTE),
            ("server restarted", restart_lost, ENDED_NOTE),
            ("no cookie", None, STANDARD_NOTE),
            ("forged signature", webapp.SESSION_COOKIE_NAME + "=" + forged_payload + ".AAAA", STANDARD_NOTE),
            ("garbage", webapp.SESSION_COOKIE_NAME + "=garbage", STANDARD_NOTE),
            ("empty", webapp.SESSION_COOKIE_NAME + "=", STANDARD_NOTE),
            ("other auth mode", webapp.SESSION_COOKIE_NAME + "=" + other_mode, STANDARD_NOTE),
            ("unrelated cookie", "other=value", STANDARD_NOTE),
        )
        for label, cookie, expected in cases:
            with self.subTest(label):
                self.assertEqual(self.login_page_message(cookie), expected)
        # Restart: a whole new registry also yields the note for every earlier cookie.
        self.server.internal_auth = auth.InternalAuthState(clock=lambda: self.now[0])
        self.assertEqual(self.login_page_message(active), ENDED_NOTE)
        # The note page still signs in normally and opens the app root.
        self.reset_rate_limits()
        cookie, _ = self.signed_in(previous=active)
        self.assertEqual(self.session_json(cookie)[0], 200)

    def test_H6_cookie_writing_responses_never_carry_a_renewal(self):
        cookie, _ = self.signed_in()
        self.advance(10 * 60)
        # GET /login while signed in: only the login-binding cookie.
        self.assertEqual(self.login_page_message(cookie), STANDARD_NOTE)
        # POST /login rotating a session that is due for renewal: only the new cookie.
        self.reset_rate_limits()
        status, headers, _ = self.login(previous=cookie)
        self.assertEqual(status, 302)
        [new_cookie_header] = self.session_cookie_headers(headers)
        rotated = webapp.SESSION_COOKIE_NAME + "=" + self.cookie(headers, webapp.SESSION_COOKIE_NAME)
        self.assertNotEqual(rotated, cookie)
        self.assert_secure_session_cookie(new_cookie_header, rotated.split("=", 1)[1])
        # Logout of a session that is due for renewal: only the clearing cookie.
        _, _, session = self.session_json(rotated)
        self.advance(10 * 60)
        status, headers, _ = self.http("POST", "/logout", {"Cookie": rotated, "Origin": ORIGIN,
                                                          session["csrf_header"]: session["csrf_token"]})
        self.assertEqual(status, 204)
        [cleared] = self.session_cookie_headers(headers)
        self.assertIn("Max-Age=0", cleared)
        self.assertEqual(http.cookies.SimpleCookie(cleared)[webapp.SESSION_COOKIE_NAME].value, "")

    def test_H7_sign_in_id_marks_each_new_sign_in(self):
        first, _ = self.signed_in()
        _, _, a = self.session_json(first)
        self.advance(HOUR)
        _, _, b = self.session_json(first)
        self.reset_rate_limits()
        second, _ = self.signed_in(previous=first)
        _, _, c = self.session_json(second)
        other, _ = self.signed_in(email("operator"))
        _, _, d = self.session_json(other)
        self.assertRegex(a["sign_in_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(a["sign_in_id"], b["sign_in_id"])
        self.assertEqual(len({a["sign_in_id"], c["sign_in_id"], d["sign_in_id"]}), 3)
        # One person's recovery scope stays the same across sign-ins; sign_in_id does not.
        self.assertEqual(a["browser_recovery_scope"], c["browser_recovery_scope"])
        for cookie, data in ((first, a), (second, c)):
            sid = webapp.verified_cookie_payload(cookie.split("=", 1)[1], allow_expired=True)["session_id"]
            self.assertNotIn(sid, data["sign_in_id"])
            self.assertNotIn(data["sign_in_id"], (data["csrf_token"], cookie, sid))
        status, _, body = self.http("GET", "/api/session")
        self.assertEqual(status, 401)
        self.assertNotIn(b"sign_in_id", body)

    def test_H9_ended_note_shows_after_any_absence(self):
        # The browser drops a cookie Max-Age seconds after the last Set-Cookie it got.
        # Model that: the note shows only while that cookie is still held.
        for absence_hours in (96 + 1, 120, 14 * 24, 180 * 24):
            with self.subTest(absence_hours=absence_hours):
                self.reset_rate_limits()
                cookie, headers = self.signed_in()
                self.advance(10 * HOUR)
                _, renewed, _ = self.session_json(cookie)  # last use, renews the cookie
                [last_set] = self.session_cookie_headers(renewed)
                max_age = int(http.cookies.SimpleCookie(last_set)[webapp.SESSION_COOKIE_NAME]["max-age"])
                self.advance(absence_hours * HOUR)
                self.assertGreater(max_age, absence_hours * HOUR, "Browser dropped the cookie, so no ended note")
                self.assertEqual(self.login_page_message(cookie), ENDED_NOTE)
                self.assertEqual(self.http("GET", "/api/session", {"Cookie": cookie})[0], 401)

    def raw_request_line(self, line):
        with socket.create_connection(self.server.server_address, timeout=5) as conn:
            conn.sendall(line + b"\r\n\r\n")
            reply = b""
            while chunk := conn.recv(4096):
                reply += chunk
        return reply

    def test_H10_malformed_request_lines_get_their_error_reply(self):
        cookie, _ = self.signed_in()
        for line, status in ((b"GET / FOO/1.1", b"400"), (b"GET / HTTP/2.0", b"505"),
                             (b"GET / HTTP/1.1 extra", b"400")):
            with self.subTest(line=line):
                reply = self.raw_request_line(line)
                # Python 3.12 answers these in HTTP/0.9 style (error page, no status
                # line); newer versions add "HTTP/1.0 <code>". Both carry the code.
                self.assertIn(b"Error code: " + status, reply, reply[:80])
                if reply.startswith(b"HTTP/"):
                    self.assertTrue(reply.startswith(b"HTTP/1.0 " + status), reply[:80])
                self.assertNotIn(webapp.SESSION_COOKIE_NAME.encode() + b"=", reply)
        # The server still serves a signed-in request afterwards.
        self.assertEqual(self.session_json(cookie)[0], 200)

    def test_H8_platform_cookie_lifetime_is_unchanged(self):
        self.assertEqual(webapp.SESSION_COOKIE_MAX_AGE_SECONDS, 8 * HOUR)
        self.assertEqual(auth.INTERNAL_SESSION_COOKIE_MAX_AGE_SECONDS, EXPECTED_COOKIE_MAX_AGE)
