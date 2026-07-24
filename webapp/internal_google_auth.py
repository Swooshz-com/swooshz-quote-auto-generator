"""Fail-closed internal Google OIDC policy, transaction, and session support."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode, urlparse

import jwt


INTERNAL_AUTH_MODE = "internal_google"
GOOGLE_ISSUER = "https://accounts.google.com"
GOOGLE_DISCOVERY_URL = GOOGLE_ISSUER + "/.well-known/openid-configuration"
OIDC_TRANSACTION_TTL_SECONDS = 10 * 60
INTERNAL_SESSION_TTL_SECONDS = 8 * 60 * 60
OIDC_CLOCK_SKEW_SECONDS = 60
MAX_OIDC_TRANSACTIONS = 1024
MAX_INTERNAL_SESSIONS = 4096
MAX_PROVIDER_RESPONSE_BYTES = 128 * 1024
MAX_ID_TOKEN_BYTES = 32 * 1024
MAX_CALLBACK_VALUE_CHARS = 4096
SUPPORTED_INTERNAL_ROLES = frozenset({"admin", "operator"})
WORKSPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$")
EMAIL_PATTERN = re.compile(
    r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
OPAQUE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,256}$")


class InternalAuthConfigError(ValueError):
    """Raised when internal-alpha configuration is ambiguous or unsafe."""


class InternalAuthStateError(ValueError):
    """Raised when transaction or session state fails closed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason.replace("_", " "))
        self.reason = reason


class OidcProtocolError(RuntimeError):
    """Privacy-safe OIDC failure with a bounded audit category."""

    def __init__(self, reason: str, *, status: int = 400) -> None:
        super().__init__("Authentication could not be completed.")
        self.reason = reason
        self.status = status


def canonical_email(value: Any) -> str:
    candidate = str(value or "").strip().casefold()
    if (
        not candidate
        or len(candidate) > 254
        or "*" in candidate
        or not EMAIL_PATTERN.fullmatch(candidate)
    ):
        raise InternalAuthConfigError("internal_email_invalid")
    return candidate


def _parse_email_list(value: Any, *, field: str, required: bool) -> tuple[str, ...]:
    raw = str(value or "")
    if not raw.strip():
        if required:
            raise InternalAuthConfigError(f"{field}_required")
        return ()
    entries = raw.split(",")
    if any(not entry.strip() for entry in entries):
        raise InternalAuthConfigError(f"{field}_empty_entry")
    canonical = tuple(canonical_email(entry) for entry in entries)
    if len(set(canonical)) != len(canonical):
        raise InternalAuthConfigError(f"{field}_duplicate")
    return canonical


@dataclasses.dataclass(frozen=True)
class InternalAuthPolicy:
    workspace_id: str
    email_roles: Mapping[str, str]
    fingerprint: str

    @classmethod
    def from_values(
        cls,
        *,
        workspace_id: Any,
        allowed_emails: Any,
        admin_emails: Any,
        operator_emails: Any,
    ) -> "InternalAuthPolicy":
        workspace = str(workspace_id or "").strip()
        if not WORKSPACE_ID_PATTERN.fullmatch(workspace):
            raise InternalAuthConfigError("internal_workspace_invalid")
        allowed = _parse_email_list(
            allowed_emails,
            field="internal_allowed_emails",
            required=True,
        )
        admins = _parse_email_list(
            admin_emails,
            field="internal_admin_emails",
            required=False,
        )
        operators = _parse_email_list(
            operator_emails,
            field="internal_operator_emails",
            required=False,
        )
        allowed_set = set(allowed)
        admin_set = set(admins)
        operator_set = set(operators)
        if not admin_set.issubset(allowed_set) or not operator_set.issubset(allowed_set):
            raise InternalAuthConfigError("internal_role_not_allowlisted")
        if admin_set & operator_set:
            raise InternalAuthConfigError("internal_role_conflict")
        assigned = admin_set | operator_set
        if assigned != allowed_set:
            raise InternalAuthConfigError("internal_role_missing")
        roles = {
            email: ("admin" if email in admin_set else "operator")
            for email in sorted(allowed_set)
        }
        fingerprint_material = json.dumps(
            {"workspace_id": workspace, "email_roles": roles},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        fingerprint = hashlib.sha256(fingerprint_material).hexdigest()
        return cls(workspace_id=workspace, email_roles=roles, fingerprint=fingerprint)

    def role_for(self, email: Any) -> str:
        try:
            normalized = canonical_email(email)
        except InternalAuthConfigError:
            return ""
        return self.email_roles.get(normalized, "")


def _token_urlsafe(byte_count: int = 32) -> str:
    return secrets.token_urlsafe(byte_count)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")


@dataclasses.dataclass(frozen=True)
class OidcTransaction:
    state: str
    browser_binding: str
    nonce: str
    code_verifier: str
    code_challenge: str
    issued_at: int
    expires_at: int


@dataclasses.dataclass(frozen=True)
class _StoredOidcTransaction:
    browser_binding_hash: str
    nonce: str
    code_verifier: str
    issued_at: int
    expires_at: int


class InternalAuthState:
    """Process-local one-time transactions and restart-invalidated sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._transactions: dict[str, _StoredOidcTransaction] = {}
        self._sessions: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        with self._lock:
            self._transactions.clear()
            self._sessions.clear()

    def _prune(self, now: int) -> None:
        self._transactions = {
            key: value
            for key, value in self._transactions.items()
            if value.expires_at >= now
        }
        self._sessions = {
            key: value
            for key, value in self._sessions.items()
            if int(value.get("expires_at") or 0) >= now
        }

    def begin_transaction(self, *, now: int | None = None) -> OidcTransaction:
        issued_at = int(time.time() if now is None else now)
        state = _token_urlsafe()
        browser_binding = _token_urlsafe()
        nonce = _token_urlsafe()
        code_verifier = _token_urlsafe(48)
        transaction = OidcTransaction(
            state=state,
            browser_binding=browser_binding,
            nonce=nonce,
            code_verifier=code_verifier,
            code_challenge=_pkce_challenge(code_verifier),
            issued_at=issued_at,
            expires_at=issued_at + OIDC_TRANSACTION_TTL_SECONDS,
        )
        with self._lock:
            self._prune(issued_at)
            if len(self._transactions) >= MAX_OIDC_TRANSACTIONS:
                raise InternalAuthStateError("oidc_transaction_capacity")
            self._transactions[_sha256_text(state)] = _StoredOidcTransaction(
                browser_binding_hash=_sha256_text(browser_binding),
                nonce=nonce,
                code_verifier=code_verifier,
                issued_at=issued_at,
                expires_at=transaction.expires_at,
            )
        return transaction

    def consume_transaction(
        self,
        state: Any,
        browser_binding: Any,
        *,
        now: int | None = None,
    ) -> OidcTransaction:
        current = int(time.time() if now is None else now)
        supplied_state = str(state or "").strip()
        supplied_binding = str(browser_binding or "").strip()
        if (
            not OPAQUE_VALUE_PATTERN.fullmatch(supplied_state)
            or not OPAQUE_VALUE_PATTERN.fullmatch(supplied_binding)
        ):
            raise InternalAuthStateError("oidc_state_malformed")
        key = _sha256_text(supplied_state)
        with self._lock:
            stored = self._transactions.pop(key, None)
        if stored is None:
            raise InternalAuthStateError("oidc_state_missing_or_replayed")
        if stored.expires_at < current:
            raise InternalAuthStateError("oidc_state_expired")
        if not secrets.compare_digest(
            stored.browser_binding_hash,
            _sha256_text(supplied_binding),
        ):
            raise InternalAuthStateError("oidc_state_browser_mismatch")
        return OidcTransaction(
            state=supplied_state,
            browser_binding=supplied_binding,
            nonce=stored.nonce,
            code_verifier=stored.code_verifier,
            code_challenge=_pkce_challenge(stored.code_verifier),
            issued_at=stored.issued_at,
            expires_at=stored.expires_at,
        )

    def create_session(
        self,
        *,
        google_sub: Any,
        email: Any,
        policy: InternalAuthPolicy,
        previous_session_id: Any = "",
        now: int | None = None,
    ) -> dict[str, Any]:
        issued_at = int(time.time() if now is None else now)
        subject = str(google_sub or "").strip()
        canonical = canonical_email(email)
        role = policy.role_for(canonical)
        if not subject or len(subject) > 255 or any(char.isspace() for char in subject):
            raise InternalAuthStateError("oidc_subject_invalid")
        if role not in SUPPORTED_INTERNAL_ROLES:
            raise InternalAuthStateError("internal_admission_denied")
        session_id = _token_urlsafe()
        expires_at = issued_at + INTERNAL_SESSION_TTL_SECONDS
        record = {
            "auth_mode": INTERNAL_AUTH_MODE,
            "google_sub": subject,
            "email": canonical,
            "workspace_id": policy.workspace_id,
            "role": role,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "session_id": session_id,
            "policy_fingerprint": policy.fingerprint,
            "user": {
                "subject": subject,
                "email": canonical,
                "account": policy.workspace_id,
                "internal_role": role,
                "auth_mode": INTERNAL_AUTH_MODE,
            },
        }
        previous = str(previous_session_id or "").strip()
        with self._lock:
            self._prune(issued_at)
            if previous:
                self._sessions.pop(previous, None)
            if len(self._sessions) >= MAX_INTERNAL_SESSIONS:
                raise InternalAuthStateError("internal_session_capacity")
            self._sessions[session_id] = dict(record)
        return record

    def validate_session(
        self,
        session: Mapping[str, Any],
        *,
        policy: InternalAuthPolicy,
        current_mode: str,
        now: int | None = None,
    ) -> dict[str, Any]:
        current = int(time.time() if now is None else now)
        session_id = str(session.get("session_id") or "").strip()
        if not OPAQUE_VALUE_PATTERN.fullmatch(session_id):
            raise InternalAuthStateError("internal_session_malformed")
        with self._lock:
            stored = self._sessions.get(session_id)
        if not stored:
            raise InternalAuthStateError("internal_session_revoked_or_restarted")
        if int(stored.get("expires_at") or 0) < current:
            self.revoke_session(session_id)
            raise InternalAuthStateError("internal_session_expired")
        if current_mode != INTERNAL_AUTH_MODE or stored.get("auth_mode") != current_mode:
            self.revoke_session(session_id)
            raise InternalAuthStateError("internal_session_mode_mismatch")
        email = str(stored.get("email") or "")
        role = policy.role_for(email)
        if not role:
            self.revoke_session(session_id)
            raise InternalAuthStateError("internal_session_allowlist_revoked")
        if (
            stored.get("policy_fingerprint") != policy.fingerprint
            or stored.get("workspace_id") != policy.workspace_id
            or stored.get("role") != role
        ):
            self.revoke_session(session_id)
            raise InternalAuthStateError("internal_session_policy_revoked")
        binding_names = (
            "auth_mode",
            "google_sub",
            "email",
            "workspace_id",
            "role",
            "issued_at",
            "expires_at",
            "session_id",
            "policy_fingerprint",
        )
        if any(session.get(name) != stored.get(name) for name in binding_names):
            self.revoke_session(session_id)
            raise InternalAuthStateError("internal_session_binding_mismatch")
        return dict(stored)

    def revoke_session(self, session_id: Any) -> bool:
        normalized = str(session_id or "").strip()
        if not normalized:
            return False
        with self._lock:
            return self._sessions.pop(normalized, None) is not None


@dataclasses.dataclass(frozen=True)
class OidcRuntimeConfig:
    issuer_url: str
    client_id: str
    client_secret: str
    redirect_uri: str
    authorize_url: str
    token_url: str

    def authorize_redirect(
        self,
        transaction: OidcTransaction,
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": transaction.state,
            "nonce": transaction.nonce,
            "code_challenge": transaction.code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.authorize_url}?{urlencode(params)}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BoundedOidcHttpClient:
    def __init__(self, *, timeout_seconds: int = 10) -> None:
        self.timeout_seconds = max(1, min(int(timeout_seconds), 30))
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def json_request(
        self,
        request: urllib.request.Request,
        *,
        expected_mime_types: frozenset[str],
    ) -> dict[str, Any]:
        try:
            with self._opener.open(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                status = int(getattr(response, "status", 200))
                final_url = str(response.geturl())
                mime_type = str(response.headers.get_content_type()).casefold()
                declared_length = response.headers.get("Content-Length")
                if declared_length and int(declared_length) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise OidcProtocolError(
                        "oidc_provider_response_too_large",
                        status=502,
                    )
                raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if 300 <= int(exc.code) < 400:
                raise OidcProtocolError("oidc_provider_redirect_rejected", status=502) from exc
            raise OidcProtocolError("oidc_provider_http_error", status=502) from exc
        except OidcProtocolError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise OidcProtocolError("oidc_provider_request_failed", status=502) from exc
        if status != 200 or final_url != request.full_url:
            raise OidcProtocolError("oidc_provider_redirect_rejected", status=502)
        if mime_type not in expected_mime_types:
            raise OidcProtocolError("oidc_provider_mime_mismatch", status=502)
        if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
            raise OidcProtocolError("oidc_provider_response_too_large", status=502)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OidcProtocolError("oidc_provider_invalid_json", status=502) from exc
        if not isinstance(payload, dict):
            raise OidcProtocolError("oidc_provider_invalid_json", status=502)
        return payload


class GoogleOidcVerifier:
    def __init__(
        self,
        config: OidcRuntimeConfig,
        *,
        http_client: BoundedOidcHttpClient | None = None,
        discovery_url: str = GOOGLE_DISCOVERY_URL,
    ) -> None:
        self.config = config
        self.http_client = http_client or BoundedOidcHttpClient()
        self.discovery_url = discovery_url

    def _discovery(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.discovery_url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        discovery = self.http_client.json_request(
            request,
            expected_mime_types=frozenset({"application/json"}),
        )
        if (
            discovery.get("issuer") != self.config.issuer_url
            or discovery.get("authorization_endpoint") != self.config.authorize_url
            or discovery.get("token_endpoint") != self.config.token_url
        ):
            raise OidcProtocolError("oidc_discovery_mismatch", status=502)
        jwks_uri = str(discovery.get("jwks_uri") or "").strip()
        parsed_jwks = urlparse(jwks_uri)
        if parsed_jwks.scheme != "https" or not parsed_jwks.netloc:
            raise OidcProtocolError("oidc_discovery_jwks_invalid", status=502)
        pkce_methods = discovery.get("code_challenge_methods_supported")
        algorithms = discovery.get("id_token_signing_alg_values_supported")
        if (
            not isinstance(pkce_methods, list)
            or "S256" not in pkce_methods
            or not isinstance(algorithms, list)
            or "RS256" not in algorithms
        ):
            raise OidcProtocolError("oidc_discovery_capability_mismatch", status=502)
        return discovery

    def _exchange_code(
        self,
        code: str,
        code_verifier: str,
        token_url: str,
    ) -> str:
        if (
            not code
            or len(code) > MAX_CALLBACK_VALUE_CHARS
            or not OPAQUE_VALUE_PATTERN.fullmatch(code_verifier)
        ):
            raise OidcProtocolError("oidc_callback_parameter_invalid")
        body = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "code_verifier": code_verifier,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            token_url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        response = self.http_client.json_request(
            request,
            expected_mime_types=frozenset({"application/json"}),
        )
        id_token = response.get("id_token")
        if not isinstance(id_token, str) or not id_token or len(id_token.encode("utf-8")) > MAX_ID_TOKEN_BYTES:
            raise OidcProtocolError("oidc_token_missing_id_token", status=502)
        return id_token

    def _jwks(self, jwks_uri: str) -> jwt.PyJWKSet:
        request = urllib.request.Request(
            jwks_uri,
            headers={"Accept": "application/jwk-set+json, application/json"},
            method="GET",
        )
        payload = self.http_client.json_request(
            request,
            expected_mime_types=frozenset(
                {"application/jwk-set+json", "application/json"}
            ),
        )
        try:
            return jwt.PyJWKSet.from_dict(payload)
        except (jwt.PyJWKError, ValueError, TypeError) as exc:
            raise OidcProtocolError("oidc_jwks_invalid", status=502) from exc

    def _verify_id_token(
        self,
        id_token: str,
        *,
        nonce: str,
        jwks: jwt.PyJWKSet,
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OidcProtocolError("oidc_id_token_malformed", status=403) from exc
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if algorithm != "RS256":
            raise OidcProtocolError("oidc_id_token_algorithm_rejected", status=403)
        if not isinstance(key_id, str) or not key_id or len(key_id) > 128:
            raise OidcProtocolError("oidc_id_token_key_unknown", status=403)
        matching_keys = [
            key
            for key in jwks.keys
            if key.key_id == key_id and key.algorithm_name == "RS256"
        ]
        if len(matching_keys) != 1:
            raise OidcProtocolError("oidc_id_token_key_unknown", status=403)
        try:
            claims = jwt.decode(
                id_token,
                matching_keys[0],
                algorithms=["RS256"],
                audience=self.config.client_id,
                issuer=self.config.issuer_url,
                leeway=OIDC_CLOCK_SKEW_SECONDS,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "exp",
                        "iat",
                        "sub",
                        "nonce",
                        "email",
                        "email_verified",
                    ],
                    "strict_aud": True,
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise OidcProtocolError("oidc_id_token_expired", status=403) from exc
        except jwt.ImmatureSignatureError as exc:
            raise OidcProtocolError("oidc_id_token_future_issued", status=403) from exc
        except jwt.InvalidIssuerError as exc:
            raise OidcProtocolError("oidc_id_token_wrong_issuer", status=403) from exc
        except jwt.InvalidAudienceError as exc:
            raise OidcProtocolError("oidc_id_token_wrong_audience", status=403) from exc
        except jwt.InvalidAlgorithmError as exc:
            raise OidcProtocolError("oidc_id_token_algorithm_rejected", status=403) from exc
        except jwt.InvalidSignatureError as exc:
            raise OidcProtocolError("oidc_id_token_invalid_signature", status=403) from exc
        except jwt.MissingRequiredClaimError as exc:
            raise OidcProtocolError("oidc_id_token_missing_claim", status=403) from exc
        except jwt.PyJWTError as exc:
            raise OidcProtocolError("oidc_id_token_invalid", status=403) from exc
        if not secrets.compare_digest(str(claims.get("nonce") or ""), nonce):
            raise OidcProtocolError("oidc_id_token_nonce_mismatch", status=403)
        if claims.get("email_verified") is not True:
            raise OidcProtocolError("oidc_email_unverified", status=403)
        subject = claims.get("sub")
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 255
            or any(char.isspace() for char in subject)
        ):
            raise OidcProtocolError("oidc_id_token_subject_invalid", status=403)
        try:
            canonical_email(claims.get("email"))
        except InternalAuthConfigError as exc:
            raise OidcProtocolError("oidc_id_token_email_invalid", status=403) from exc
        return claims

    def exchange_and_verify(
        self,
        *,
        code: str,
        code_verifier: str,
        nonce: str,
    ) -> dict[str, Any]:
        discovery = self._discovery()
        id_token = self._exchange_code(
            code,
            code_verifier,
            str(discovery["token_endpoint"]),
        )
        jwks = self._jwks(str(discovery["jwks_uri"]))
        return self._verify_id_token(id_token, nonce=nonce, jwks=jwks)
