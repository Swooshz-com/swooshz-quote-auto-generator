"""Internal group-password access with stable alias ownership and revocable sessions."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Mapping
from typing import Any

INTERNAL_AUTH_MODE = "internal_password"
INTERNAL_SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_NONCE_TTL_SECONDS = 10 * 60
MAX_INTERNAL_SESSIONS = 4096
MAX_LOGIN_NONCES = 1024
MAX_INTERNAL_IDENTITIES = 100
MAX_INTERNAL_IDENTITIES_JSON_BYTES = 16 * 1024
MAX_PASSWORD_BYTES = 1024
WORKSPACE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}")
USER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{5,254}")
EMAIL_PATTERN = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
OPAQUE_VALUE_PATTERN = re.compile(r"[A-Za-z0-9_-]{20,256}")

class InternalAuthConfigError(ValueError):
    """Invalid server-only access policy; messages never contain supplied values."""

class InternalAuthStateError(ValueError):
    def __init__(self, reason):
        super().__init__("Authentication could not be completed.")
        self.reason = reason


def canonical_email(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 254 or not value.isascii():
        raise InternalAuthConfigError("internal_email_invalid")
    candidate = value.strip().casefold()
    if not candidate or "*" in candidate or any(ord(c) < 32 or ord(c) == 127 for c in value) or not EMAIL_PATTERN.fullmatch(candidate):
        raise InternalAuthConfigError("internal_email_invalid")
    return candidate


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InternalAuthConfigError("duplicate_key")
        result[key] = value
    return result


def password_digest(value):
    if not isinstance(value, str):
        raise InternalAuthConfigError("password_invalid")
    try:
        raw = value.encode("utf-8")
    except UnicodeError as exc:
        raise InternalAuthConfigError("password_invalid") from exc
    if not 12 <= len(raw) <= MAX_PASSWORD_BYTES or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InternalAuthConfigError("password_invalid")
    return hashlib.sha256(raw).digest()


@dataclasses.dataclass(frozen=True)
class InternalIdentity:
    user_id: str
    email: str
    role: str

@dataclasses.dataclass(frozen=True)
class InternalAuthPolicy:
    workspace_id: str
    identities: tuple[InternalIdentity, ...]
    fingerprint: str = dataclasses.field(repr=False)
    credential_digest: bytes = dataclasses.field(repr=False)

    @classmethod
    def from_values(cls, *, workspace_id, allowed_emails, bindings_json, password):
        if not isinstance(workspace_id, str) or not WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
            raise InternalAuthConfigError("internal_workspace_invalid")
        if not isinstance(allowed_emails, str):
            raise InternalAuthConfigError("internal_allowlist_invalid")
        try:
            allowlist_bytes = allowed_emails.encode("utf-8")
        except UnicodeError:
            raise InternalAuthConfigError("internal_allowlist_invalid") from None
        if len(allowlist_bytes) > MAX_INTERNAL_IDENTITIES_JSON_BYTES:
            raise InternalAuthConfigError("internal_allowlist_invalid")
        parts = allowed_emails.split(";")
        if not 1 <= len(parts) <= MAX_INTERNAL_IDENTITIES:
            raise InternalAuthConfigError("internal_allowlist_count")
        emails = [canonical_email(part) for part in parts]
        if len(set(emails)) != len(emails):
            raise InternalAuthConfigError("internal_email_duplicate")
        if not isinstance(bindings_json, str):
            raise InternalAuthConfigError("internal_bindings_invalid")
        try:
            if len(bindings_json.encode("utf-8")) > MAX_INTERNAL_IDENTITIES_JSON_BYTES:
                raise InternalAuthConfigError("internal_bindings_too_large")
            rows = json.loads(bindings_json, object_pairs_hook=strict_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(InternalAuthConfigError("invalid_constant")))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise InternalAuthConfigError("internal_bindings_invalid") from exc
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_INTERNAL_IDENTITIES:
            raise InternalAuthConfigError("internal_bindings_invalid")
        identities = []
        ids, bound_emails = set(), set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"user_id", "email", "role"}:
                raise InternalAuthConfigError("internal_binding_keys")
            if any(not isinstance(v, str) or v != v.strip() for v in row.values()):
                raise InternalAuthConfigError("internal_binding_types")
            uid, role = row["user_id"], row["role"]
            email = canonical_email(row["email"])
            if not USER_ID_PATTERN.fullmatch(uid) or role not in {"admin", "operator"}:
                raise InternalAuthConfigError("internal_binding_invalid")
            if uid in ids or email in bound_emails:
                raise InternalAuthConfigError("internal_binding_duplicate")
            ids.add(uid)
            bound_emails.add(email)
            identities.append(InternalIdentity(uid, email, role))
        if bound_emails != set(emails):
            raise InternalAuthConfigError("internal_binding_set_mismatch")
        identities = tuple(sorted(identities, key=lambda identity: identity.user_id))
        digest = password_digest(password)
        material = json.dumps([workspace_id, [dataclasses.astuple(i) for i in identities]], separators=(",", ":"), ensure_ascii=True).encode()
        return cls(workspace_id, identities, hashlib.sha256(material + digest).hexdigest(), digest)

    def identity_for_subject(self, subject):
        return next((i for i in self.identities if i.user_id == subject), None)


class InternalAuthState:
    """One server's atomic nonce and session registry; restart invalidates both."""
    def __init__(self, *, clock=time.time, token=secrets.token_urlsafe, verifier=secrets.compare_digest, lock=None):
        self._clock, self._token, self._verifier = clock, token, verifier
        self._lock = lock if lock is not None else threading.RLock()
        self._nonces = {}
        self._sessions = {}

    def _prune(self, now):
        self._nonces = {k: v for k, v in self._nonces.items() if v[1] > now}
        self._sessions = {k: v for k, v in self._sessions.items() if v["expires_at"] > now}

    def begin_login(self):
        now = int(self._clock())
        nonce, binding = self._token(32), self._token(32)
        with self._lock:
            self._prune(now)
            if len(self._nonces) >= MAX_LOGIN_NONCES:
                raise InternalAuthStateError("login_capacity")
            self._nonces[nonce] = (hashlib.sha256(binding.encode()).digest(), now + LOGIN_NONCE_TTL_SECONDS)
        return nonce, binding

    def consume_login(self, nonce, binding):
        if not isinstance(nonce, str) or not isinstance(binding, str) or not OPAQUE_VALUE_PATTERN.fullmatch(nonce) or not OPAQUE_VALUE_PATTERN.fullmatch(binding):
            raise InternalAuthStateError("login_nonce_invalid")
        with self._lock:
            stored = self._nonces.pop(nonce, None)
        if stored is None or stored[1] <= self._clock() or not secrets.compare_digest(stored[0], hashlib.sha256(binding.encode()).digest()):
            raise InternalAuthStateError("login_nonce_invalid")

    def authenticate(self, *, email, password, policy, previous_session_id=""):
        # Always perform the fixed-size shared-credential comparison, including unknown aliases.
        valid_password_input = True
        try:
            supplied = password_digest(password)
        except InternalAuthConfigError:
            valid_password_input = False
            supplied = hashlib.sha256(b"invalid-login-input").digest()
        matches = self._verifier(supplied, policy.credential_digest)
        try:
            canonical = canonical_email(email)
        except InternalAuthConfigError:
            canonical = ""
        identity = next((i for i in policy.identities if i.email == canonical), None)
        if not valid_password_input or not matches or identity is None:
            raise InternalAuthStateError("login_denied")
        now = int(self._clock())
        record = {"auth_mode": INTERNAL_AUTH_MODE, "session_id": self._token(32),
                  "issued_at": now, "expires_at": now + INTERNAL_SESSION_TTL_SECONDS,
                  "policy_fingerprint": policy.fingerprint,
                  "user": {"subject": identity.user_id, "account": policy.workspace_id,
                           "internal_role": identity.role, "auth_mode": INTERNAL_AUTH_MODE}}
        with self._lock:
            self._prune(now)
            previous = self._sessions.get(previous_session_id)
            if previous:
                self._sessions.pop(previous_session_id)
            if len(self._sessions) >= MAX_INTERNAL_SESSIONS:
                raise InternalAuthStateError("session_capacity")
            self._sessions[record["session_id"]] = record
        return self.cookie_payload(record)

    @staticmethod
    def cookie_payload(record):
        return {"auth_mode": INTERNAL_AUTH_MODE, "session_id": record["session_id"]}

    def validate_session(self, session: Mapping[str, Any], *, policy, current_mode):
        sid = session.get("session_id")
        if not isinstance(sid, str) or not OPAQUE_VALUE_PATTERN.fullmatch(sid):
            raise InternalAuthStateError("session_invalid")
        with self._lock:
            record = self._sessions.get(sid)
            if record is None:
                raise InternalAuthStateError("session_revoked_or_restarted")
            if current_mode != INTERNAL_AUTH_MODE or session.get("auth_mode") != INTERNAL_AUTH_MODE or record["expires_at"] <= self._clock() or record["policy_fingerprint"] != policy.fingerprint:
                self._sessions.pop(sid, None)
                raise InternalAuthStateError("session_revoked")
            return {**record, "user": dict(record["user"])}

    def revoke_session(self, sid):
        with self._lock:
            return self._sessions.pop(sid, None) is not None
