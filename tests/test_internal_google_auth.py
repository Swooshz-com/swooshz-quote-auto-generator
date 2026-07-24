from __future__ import annotations

import datetime as dt
import email.message
import io
import json
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from webapp.internal_google_auth import (
    GOOGLE_ISSUER,
    INTERNAL_AUTH_MODE,
    INTERNAL_SESSION_TTL_SECONDS,
    OIDC_TRANSACTION_TTL_SECONDS,
    BoundedOidcHttpClient,
    GoogleOidcVerifier,
    InternalAuthConfigError,
    InternalAuthPolicy,
    InternalAuthState,
    InternalAuthStateError,
    OidcProtocolError,
    OidcRuntimeConfig,
)


class _BoundedResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str,
        content_type: str = "application/json",
        status: int = 200,
        declared_length: int | None = None,
    ):
        self._body = io.BytesIO(body)
        self._url = url
        self.status = status
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        if declared_length is not None:
            self.headers["Content-Length"] = str(declared_length)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return self._url

    def read(self, limit=-1):
        return self._body.read(limit)


class BoundedOidcHttpClientTest(unittest.TestCase):
    def request(self):
        return urllib.request.Request("https://accounts.example.test/data")

    def test_accepts_only_exact_bounded_json_response(self):
        client = BoundedOidcHttpClient(timeout_seconds=999)
        response = _BoundedResponse(
            b'{"ok":true}',
            url=self.request().full_url,
            declared_length=11,
        )
        with mock.patch.object(client._opener, "open", return_value=response) as opened:
            payload = client.json_request(
                self.request(),
                expected_mime_types=frozenset({"application/json"}),
            )
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(opened.call_args.kwargs["timeout"], 30)

    def test_rejects_timeout_redirect_oversize_mime_and_malformed_json(self):
        request = self.request()
        cases = {
            "timeout": TimeoutError("synthetic timeout"),
            "redirect": urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                email.message.Message(),
                None,
            ),
            "changed_url": _BoundedResponse(
                b"{}",
                url="https://redirected.example.test/data",
            ),
            "declared_oversize": _BoundedResponse(
                b"{}",
                url=request.full_url,
                declared_length=128 * 1024 + 1,
            ),
            "actual_oversize": _BoundedResponse(
                b"x" * (128 * 1024 + 1),
                url=request.full_url,
            ),
            "mime": _BoundedResponse(
                b"{}",
                url=request.full_url,
                content_type="text/html",
            ),
            "malformed_json": _BoundedResponse(
                b"{",
                url=request.full_url,
            ),
            "json_array": _BoundedResponse(
                b"[]",
                url=request.full_url,
            ),
        }
        for name, result in cases.items():
            client = BoundedOidcHttpClient()
            side_effect = result if isinstance(result, BaseException) else None
            return_value = None if side_effect else result
            with (
                self.subTest(name=name),
                mock.patch.object(
                    client._opener,
                    "open",
                    side_effect=side_effect,
                    return_value=return_value,
                ),
                self.assertRaises(OidcProtocolError),
            ):
                client.json_request(
                    request,
                    expected_mime_types=frozenset({"application/json"}),
                )


class InternalGoogleAuthContractTest(unittest.TestCase):
    def policy(self, **overrides):
        values = {
            "workspace_id": "workspace-internal-alpha",
            "allowed_emails": "admin@example.test,operator@example.test",
            "admin_emails": "admin@example.test",
            "operator_emails": "operator@example.test",
        }
        values.update(overrides)
        return InternalAuthPolicy.from_values(**values)

    def test_internal_mode_constant_is_explicit(self):
        self.assertEqual(INTERNAL_AUTH_MODE, "internal_google")

    def test_policy_canonicalizes_case_without_alias_transformations(self):
        policy = self.policy(
            allowed_emails="Test.User+alpha@example.test",
            admin_emails="test.user+ALPHA@example.test",
            operator_emails="",
        )
        self.assertEqual(policy.role_for("TEST.USER+ALPHA@example.test"), "admin")
        self.assertEqual(policy.role_for("testuser+alpha@example.test"), "")
        self.assertEqual(policy.role_for("test.user@example.test"), "")

    def test_policy_rejects_invalid_allowlist_and_role_shapes(self):
        invalid_cases = {
            "empty_entry": {"allowed_emails": "admin@example.test,"},
            "wildcard": {
                "allowed_emails": "*@example.test",
                "admin_emails": "*@example.test",
                "operator_emails": "",
            },
            "domain_only": {
                "allowed_emails": "example.test",
                "admin_emails": "example.test",
                "operator_emails": "",
            },
            "malformed": {
                "allowed_emails": "not-an-email",
                "admin_emails": "not-an-email",
                "operator_emails": "",
            },
            "duplicate_casefolded": {
                "allowed_emails": "ADMIN@example.test,admin@example.test",
            },
            "role_not_subset": {"admin_emails": "outsider@example.test"},
            "multiple_roles": {"operator_emails": "admin@example.test"},
            "missing_role": {"operator_emails": ""},
            "workspace_missing": {"workspace_id": ""},
            "workspace_malformed": {"workspace_id": "workspace value"},
        }
        for name, overrides in invalid_cases.items():
            with self.subTest(name=name), self.assertRaises(InternalAuthConfigError):
                self.policy(**overrides)

    def test_policy_fingerprint_changes_for_allowlist_role_or_workspace(self):
        baseline = self.policy()
        changed_role = self.policy(
            admin_emails="operator@example.test",
            operator_emails="admin@example.test",
        )
        changed_workspace = self.policy(workspace_id="workspace-other-alpha")
        self.assertNotEqual(baseline.fingerprint, changed_role.fingerprint)
        self.assertNotEqual(baseline.fingerprint, changed_workspace.fingerprint)

    def test_state_nonce_and_pkce_transaction_is_one_time_and_bounded(self):
        state_store = InternalAuthState()
        transaction = state_store.begin_transaction(now=100)
        self.assertNotEqual(transaction.state, transaction.nonce)
        self.assertNotEqual(transaction.code_verifier, transaction.code_challenge)
        self.assertEqual(
            transaction.expires_at - transaction.issued_at,
            OIDC_TRANSACTION_TTL_SECONDS,
        )
        consumed = state_store.consume_transaction(
            transaction.state,
            transaction.browser_binding,
            now=101,
        )
        self.assertEqual(consumed.nonce, transaction.nonce)
        self.assertEqual(consumed.code_verifier, transaction.code_verifier)
        with self.assertRaises(InternalAuthStateError) as replay:
            state_store.consume_transaction(
                transaction.state,
                transaction.browser_binding,
                now=102,
            )
        self.assertEqual(replay.exception.reason, "oidc_state_missing_or_replayed")

    def test_state_rejects_missing_malformed_expired_and_wrong_browser_binding(self):
        for name, state_value, binding_value, now, expected in (
            ("missing", "", "x" * 24, 101, "oidc_state_malformed"),
            ("malformed", "*", "x" * 24, 101, "oidc_state_malformed"),
        ):
            store = InternalAuthState()
            store.begin_transaction(now=100)
            with self.subTest(name=name), self.assertRaises(InternalAuthStateError) as caught:
                store.consume_transaction(state_value, binding_value, now=now)
            self.assertEqual(caught.exception.reason, expected)
        store = InternalAuthState()
        expired = store.begin_transaction(now=100)
        with self.assertRaises(InternalAuthStateError) as caught:
            store.consume_transaction(
                expired.state,
                expired.browser_binding,
                now=expired.expires_at + 1,
            )
        self.assertEqual(caught.exception.reason, "oidc_state_expired")
        store = InternalAuthState()
        mismatched = store.begin_transaction(now=100)
        with self.assertRaises(InternalAuthStateError) as caught:
            store.consume_transaction(
                mismatched.state,
                "different-browser-binding-value",
                now=101,
            )
        self.assertEqual(caught.exception.reason, "oidc_state_browser_mismatch")

    def test_session_binds_identity_policy_and_restart_state(self):
        policy = self.policy()
        state_store = InternalAuthState()
        session = state_store.create_session(
            google_sub="stable-google-subject",
            email="ADMIN@example.test",
            policy=policy,
            now=100,
        )
        self.assertEqual(session["google_sub"], "stable-google-subject")
        self.assertEqual(session["user"]["subject"], "stable-google-subject")
        self.assertEqual(session["email"], "admin@example.test")
        self.assertEqual(session["workspace_id"], policy.workspace_id)
        self.assertEqual(session["role"], "admin")
        self.assertEqual(
            session["expires_at"] - session["issued_at"],
            INTERNAL_SESSION_TTL_SECONDS,
        )
        self.assertEqual(
            state_store.validate_session(
                session,
                policy=policy,
                current_mode=INTERNAL_AUTH_MODE,
                now=101,
            )["session_id"],
            session["session_id"],
        )
        state_store.reset()
        with self.assertRaises(InternalAuthStateError) as caught:
            state_store.validate_session(
                session,
                policy=policy,
                current_mode=INTERNAL_AUTH_MODE,
                now=102,
            )
        self.assertEqual(
            caught.exception.reason,
            "internal_session_revoked_or_restarted",
        )

    def test_session_rejects_expiry_logout_mode_workspace_role_and_policy_changes(self):
        policy = self.policy()
        cases = []
        for reason in (
            "expired",
            "logout",
            "mode",
            "allowlist",
            "role",
            "fingerprint",
            "binding",
        ):
            store = InternalAuthState()
            session = store.create_session(
                google_sub="stable-google-subject",
                email="admin@example.test",
                policy=policy,
                now=100,
            )
            effective_policy = policy
            current_mode = INTERNAL_AUTH_MODE
            now = 101
            candidate = dict(session)
            if reason == "expired":
                now = session["expires_at"] + 1
            elif reason == "logout":
                store.revoke_session(session["session_id"])
            elif reason == "mode":
                current_mode = "platform"
            elif reason == "allowlist":
                effective_policy = self.policy(
                    allowed_emails="operator@example.test",
                    admin_emails="",
                    operator_emails="operator@example.test",
                )
            elif reason == "role":
                effective_policy = self.policy(
                    admin_emails="",
                    operator_emails="admin@example.test,operator@example.test",
                )
            elif reason == "fingerprint":
                candidate["policy_fingerprint"] = "0" * 64
            elif reason == "binding":
                candidate["google_sub"] = "different-google-subject"
            cases.append(
                (reason, store, candidate, effective_policy, current_mode, now)
            )
        for reason, store, session, effective_policy, current_mode, now in cases:
            with self.subTest(reason=reason), self.assertRaises(InternalAuthStateError):
                store.validate_session(
                    session,
                    policy=effective_policy,
                    current_mode=current_mode,
                    now=now,
                )

    def test_session_rotation_rejects_fixed_previous_identifier(self):
        store = InternalAuthState()
        policy = self.policy()
        first = store.create_session(
            google_sub="stable-google-subject",
            email="admin@example.test",
            policy=policy,
            now=100,
        )
        second = store.create_session(
            google_sub="stable-google-subject",
            email="admin@example.test",
            policy=policy,
            previous_session_id=first["session_id"],
            now=101,
        )
        self.assertNotEqual(first["session_id"], second["session_id"])
        with self.assertRaises(InternalAuthStateError):
            store.validate_session(
                first,
                policy=policy,
                current_mode=INTERNAL_AUTH_MODE,
                now=102,
            )

    def test_same_email_with_different_subject_has_distinct_primary_identity(self):
        store = InternalAuthState()
        policy = self.policy()
        first = store.create_session(
            google_sub="google-subject-one",
            email="admin@example.test",
            policy=policy,
            now=100,
        )
        second = store.create_session(
            google_sub="google-subject-two",
            email="admin@example.test",
            policy=policy,
            now=101,
        )
        self.assertNotEqual(first["google_sub"], second["google_sub"])
        self.assertNotEqual(first["user"]["subject"], second["user"]["subject"])
        self.assertNotEqual(first["session_id"], second["session_id"])


class _FakeOidcHttpClient:
    def __init__(self, *, discovery, token, jwks):
        self.discovery = discovery
        self.token = token
        self.jwks = jwks
        self.requests = []

    def json_request(self, request, *, expected_mime_types):
        self.requests.append((request, expected_mime_types))
        if request.full_url.endswith("openid-configuration"):
            return self.discovery
        if request.full_url == self.discovery["token_endpoint"]:
            return self.token
        if request.full_url == self.discovery["jwks_uri"]:
            return self.jwks
        raise AssertionError(f"Unexpected synthetic OIDC URL: {request.full_url}")


class GoogleOidcVerifierTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(cls.private_key.public_key())
        )
        cls.jwk.update({"kid": "synthetic-rsa-key", "alg": "RS256", "use": "sig"})

    def config(self):
        return OidcRuntimeConfig(
            issuer_url=GOOGLE_ISSUER,
            client_id="synthetic-client-id",
            client_secret="synthetic-client-secret",
            redirect_uri="https://quote.swooshz.com/callback",
            authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
        )

    def discovery(self, **overrides):
        value = {
            "issuer": GOOGLE_ISSUER,
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            "code_challenge_methods_supported": ["S256"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        value.update(overrides)
        return value

    def claims(self, **overrides):
        now = int(time.time())
        value = {
            "iss": GOOGLE_ISSUER,
            "aud": "synthetic-client-id",
            "exp": now + 300,
            "iat": now,
            "sub": "stable-google-subject",
            "nonce": "synthetic-nonce",
            "email": "admin@example.test",
            "email_verified": True,
        }
        value.update(overrides)
        return value

    def token(self, claims=None, *, key=None, algorithm="RS256", kid="synthetic-rsa-key"):
        return jwt.encode(
            claims or self.claims(),
            key or self.private_key,
            algorithm=algorithm,
            headers={"kid": kid},
        )

    def verifier(self, token, *, discovery=None, jwks=None):
        discovery = discovery or self.discovery()
        http = _FakeOidcHttpClient(
            discovery=discovery,
            token={"id_token": token},
            jwks=jwks or {"keys": [self.jwk]},
        )
        verifier = GoogleOidcVerifier(
            self.config(),
            http_client=http,
            discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        )
        return verifier, http

    def test_authorize_redirect_uses_exact_redirect_nonce_and_s256_pkce(self):
        transaction = InternalAuthState().begin_transaction(now=100)
        redirect = self.config().authorize_redirect(transaction)
        parsed = urllib.parse.urlparse(redirect)
        params = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.geturl().split("?", 1)[0], self.config().authorize_url)
        self.assertEqual(params["redirect_uri"], [self.config().redirect_uri])
        self.assertEqual(params["state"], [transaction.state])
        self.assertEqual(params["nonce"], [transaction.nonce])
        self.assertEqual(params["code_challenge"], [transaction.code_challenge])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertNotIn("redirect", params)

    def test_valid_code_nonce_pkce_and_rs256_claim_flow(self):
        verifier, http = self.verifier(self.token())
        claims = verifier.exchange_and_verify(
            code="synthetic-authorization-code",
            code_verifier="synthetic-code-verifier-value",
            nonce="synthetic-nonce",
        )
        self.assertEqual(claims["sub"], "stable-google-subject")
        token_request = http.requests[1][0]
        token_body = urllib.parse.parse_qs(token_request.data.decode("utf-8"))
        self.assertEqual(token_body["redirect_uri"], [self.config().redirect_uri])
        self.assertEqual(token_body["code_verifier"], ["synthetic-code-verifier-value"])
        self.assertEqual(token_body["grant_type"], ["authorization_code"])
        self.assertNotIn("access_token", token_body)

    def test_discovery_requires_exact_issuer_endpoints_s256_and_rs256(self):
        invalid_discovery = (
            self.discovery(issuer="https://wrong.example"),
            self.discovery(token_endpoint="https://wrong.example/token"),
            self.discovery(code_challenge_methods_supported=["plain"]),
            self.discovery(id_token_signing_alg_values_supported=["HS256"]),
            self.discovery(jwks_uri="http://www.googleapis.com/keys"),
        )
        for discovery in invalid_discovery:
            verifier, _http = self.verifier(self.token(), discovery=discovery)
            with self.subTest(discovery=discovery), self.assertRaises(OidcProtocolError):
                verifier.exchange_and_verify(
                    code="synthetic-authorization-code",
                    code_verifier="synthetic-code-verifier-value",
                    nonce="synthetic-nonce",
                )

    def test_token_response_requires_bounded_id_token(self):
        discovery = self.discovery()
        for token_response in ({}, {"id_token": ""}, {"id_token": "x" * (33 * 1024)}):
            http = _FakeOidcHttpClient(
                discovery=discovery,
                token=token_response,
                jwks={"keys": [self.jwk]},
            )
            verifier = GoogleOidcVerifier(self.config(), http_client=http)
            with self.subTest(token_response=bool(token_response)), self.assertRaises(OidcProtocolError) as caught:
                verifier.exchange_and_verify(
                    code="synthetic-authorization-code",
                    code_verifier="synthetic-code-verifier-value",
                    nonce="synthetic-nonce",
                )
            self.assertEqual(caught.exception.reason, "oidc_token_missing_id_token")

    def test_rejects_algorithm_key_signature_issuer_audience_time_nonce_and_claim_failures(self):
        now = int(time.time())
        cases = {
            "unsupported_algorithm": self.token(
                self.claims(),
                key="synthetic-hmac-key-with-at-least-thirty-two-bytes",
                algorithm="HS256",
            ),
            "unknown_key": self.token(self.claims(), kid="unknown-key"),
            "invalid_signature": self.token(self.claims(), key=self.other_private_key),
            "wrong_issuer": self.token(self.claims(iss="https://wrong.example")),
            "wrong_audience": self.token(self.claims(aud="wrong-client")),
            "expired": self.token(self.claims(exp=now - 120)),
            "future_issued": self.token(self.claims(iat=now + 120)),
            "missing_sub": self.token({key: value for key, value in self.claims().items() if key != "sub"}),
            "missing_email": self.token({key: value for key, value in self.claims().items() if key != "email"}),
            "unverified_email": self.token(self.claims(email_verified=False)),
            "wrong_nonce": self.token(self.claims(nonce="wrong-nonce")),
        }
        for name, token in cases.items():
            verifier, _http = self.verifier(token)
            with self.subTest(name=name), self.assertRaises(OidcProtocolError):
                verifier.exchange_and_verify(
                    code="synthetic-authorization-code",
                    code_verifier="synthetic-code-verifier-value",
                    nonce="synthetic-nonce",
                )


if __name__ == "__main__":
    unittest.main()
