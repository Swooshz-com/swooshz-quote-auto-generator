"""Synthetic actual HTTP checks for the internal password boundary; no provider calls."""
import contextlib
import http.client
import http.cookies
import json
import os
import re
import threading
import unittest
import urllib.parse
from unittest import mock
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
            self.assertEqual(self.http("POST","/logout",dict(Cookie=cookie,Origin=ORIGIN,**{session["csrf_header"]:session["csrf_token"]}))[0],204)
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
