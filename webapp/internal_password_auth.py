"""Internal group-password access with stable alias ownership and revocable sessions."""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

INTERNAL_AUTH_MODE = "internal_password"
INTERNAL_SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_NONCE_TTL_SECONDS = 10 * 60
MAX_INTERNAL_SESSIONS = 4096
MAX_LOGIN_NONCES = 1024
MAX_INTERNAL_IDENTITIES = 100
MAX_INTERNAL_IDENTITIES_JSON_BYTES = 16 * 1024
MAX_PASSWORD_BYTES = 1024
PASSWORD_KDF_ITERATIONS = 600_000
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


def password_bytes(value):
    if not isinstance(value, str) or len(value) > MAX_PASSWORD_BYTES:
        raise InternalAuthConfigError("password_invalid")
    try:
        raw = value.encode("utf-8")
    except UnicodeError as exc:
        raise InternalAuthConfigError("password_invalid") from exc
    if not 12 <= len(raw) <= MAX_PASSWORD_BYTES or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise InternalAuthConfigError("password_invalid")
    return raw


def password_digest(value, *, salt):
    return hashlib.pbkdf2_hmac("sha256", password_bytes(value), salt, PASSWORD_KDF_ITERATIONS, dklen=32)


@dataclasses.dataclass(frozen=True)
class InternalIdentity:
    user_id: str
    email: str
    role: str

def parse_allowlist(value: str) -> set[str]:
    if not isinstance(value, str) or len(value) > MAX_INTERNAL_IDENTITIES_JSON_BYTES or not value.isascii():
        raise InternalAuthConfigError("internal_allowlist_invalid")
    if not value:
        return set()
    parts = value.split(";")
    if len(parts) > MAX_INTERNAL_IDENTITIES:
        raise InternalAuthConfigError("internal_allowlist_count")
    emails = {canonical_email(part) for part in parts}
    if len(emails) != len(parts):
        raise InternalAuthConfigError("internal_email_duplicate")
    return emails


def stable_alias_id(workspace_id: str, email: str) -> str:
    material = f"sqag-internal-alias-v1\0{workspace_id}\0{email}".encode("ascii")
    return "internal-v1:" + hashlib.sha256(material).hexdigest()


def project_binding_id(user_id: str) -> str:
    # Valid binding IDs contain only tracking-ID characters. Preserve the
    # existing storage projection for subjects above the tracking-ID limit.
    if len(user_id) <= 128:
        return user_id
    return "sha256:" + hashlib.sha256(user_id.encode("ascii")).hexdigest()[:16]


@dataclasses.dataclass(frozen=True)
class InternalAuthPolicy:
    workspace_id: str
    identities: tuple[InternalIdentity, ...]
    fingerprint: str = dataclasses.field(repr=False)
    credentials: Mapping[str, tuple[bytes, bytes]] = dataclasses.field(repr=False)

    @classmethod
    def validate_values(cls, *, workspace_id, allowed_emails, allowed_admin_emails,
                        bindings_json, password, admin_password, salt_key):
        """Validate the complete current configuration without deriving a credential."""
        if not isinstance(workspace_id, str) or not WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
            raise InternalAuthConfigError("internal_workspace_invalid")
        operators = parse_allowlist(allowed_emails)
        admins = parse_allowlist(allowed_admin_emails)
        emails = operators | admins
        if not 1 <= len(emails) <= MAX_INTERNAL_IDENTITIES:
            raise InternalAuthConfigError("internal_allowlist_count")
        if not isinstance(bindings_json, str) or len(bindings_json) > MAX_INTERNAL_IDENTITIES_JSON_BYTES:
            raise InternalAuthConfigError("internal_bindings_invalid")
        try:
            if len(bindings_json.encode("utf-8")) > MAX_INTERNAL_IDENTITIES_JSON_BYTES:
                raise InternalAuthConfigError("internal_bindings_too_large")
            rows = json.loads(bindings_json or "[]", object_pairs_hook=strict_object)
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise InternalAuthConfigError("internal_bindings_invalid") from exc
        if not isinstance(rows, list) or len(rows) > MAX_INTERNAL_IDENTITIES:
            raise InternalAuthConfigError("internal_bindings_invalid")
        bindings = {}
        projected_ids = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"user_id", "email", "role"}:
                raise InternalAuthConfigError("internal_binding_keys")
            if any(not isinstance(v, str) or v != v.strip() for v in row.values()):
                raise InternalAuthConfigError("internal_binding_types")
            uid, legacy_role = row["user_id"], row["role"]
            email = canonical_email(row["email"])
            if not USER_ID_PATTERN.fullmatch(uid) or legacy_role not in {"admin", "operator"}:
                raise InternalAuthConfigError("internal_binding_invalid")
            projected = project_binding_id(uid)
            if projected in projected_ids or email in bindings:
                raise InternalAuthConfigError("internal_binding_duplicate")
            projected_ids.add(projected)
            bindings[email] = uid

        identities = []
        for email in sorted(emails):
            uid = bindings.get(email)
            if uid is None:
                uid = stable_alias_id(workspace_id, email)
                if uid in projected_ids:
                    raise InternalAuthConfigError("internal_binding_duplicate")
                projected_ids.add(uid)
            identities.append(InternalIdentity(uid, email, "admin" if email in admins else "operator"))

        if not isinstance(salt_key, bytes) or len(salt_key) < 32:
            raise InternalAuthConfigError("password_salt_key_invalid")
        passwords = {}
        for role, required, value in (
            ("operator", bool(operators - admins), password),
            ("admin", bool(admins), admin_password),
        ):
            if required or value != "":
                passwords[role] = password_bytes(value)
        if len(passwords) == 2 and hmac.compare_digest(passwords["operator"], passwords["admin"]):
            raise InternalAuthConfigError("internal_passwords_equal")
        return tuple(identities), passwords

    @classmethod
    def policy_material(cls, values):
        identities, passwords = cls.validate_values(**values)
        material = json.dumps(
            [values["workspace_id"], [dataclasses.astuple(i) for i in identities]],
            separators=(",", ":"),
        ).encode("ascii")
        # A keyed fingerprint detects config changes without password derivation
        # on protected requests. The credentials themselves remain server-only.
        secret_material = json.dumps(
            {role: raw.decode("utf-8") for role, raw in passwords.items()},
            sort_keys=True, separators=(",", ":"),
        ).encode("ascii")
        fingerprint = hmac.digest(
            values["salt_key"], b"sqag-internal-policy-v2\0" + material + b"\0" + secret_material, "sha256",
        ).hex()
        return identities, passwords, material, fingerprint

    @classmethod
    def fingerprint_for_values(cls, **values):
        return cls.policy_material(values)[3]

    @classmethod
    def from_values(cls, **values):
        identities, passwords, material, fingerprint = cls.policy_material(values)
        credentials = {}
        for role, raw in passwords.items():
            salt = hmac.digest(values["salt_key"], b"sqag-internal-password-verifier-v2\0" + role.encode() + b"\0" + material, "sha256")
            credentials[role] = password_digest(raw.decode("utf-8"), salt=salt), salt
        return cls(values["workspace_id"], identities, fingerprint, MappingProxyType(credentials))

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
        try:
            canonical = canonical_email(email)
        except InternalAuthConfigError:
            canonical = ""
        identity = next((i for i in policy.identities if i.email == canonical), None)
        # Unknown aliases and malformed passwords take the same single KDF path.
        digest, salt = policy.credentials[identity.role] if identity else next(iter(policy.credentials.values()))
        valid_password_input = True
        try:
            supplied = password_digest(password, salt=salt)
        except InternalAuthConfigError:
            valid_password_input = False
            supplied = password_digest("invalid-login-input", salt=salt)
        matches = self._verifier(supplied, digest)
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

    def validate_session(self, session: Mapping[str, Any], *, policy_fingerprint, current_mode):
        sid = session.get("session_id")
        if not isinstance(sid, str) or not OPAQUE_VALUE_PATTERN.fullmatch(sid):
            raise InternalAuthStateError("session_invalid")
        with self._lock:
            record = self._sessions.get(sid)
            if record is None:
                raise InternalAuthStateError("session_revoked_or_restarted")
            if current_mode != INTERNAL_AUTH_MODE or session.get("auth_mode") != INTERNAL_AUTH_MODE or record["expires_at"] <= self._clock() or record["policy_fingerprint"] != policy_fingerprint:
                self._sessions.pop(sid, None)
                raise InternalAuthStateError("session_revoked")
            return {**record, "user": dict(record["user"])}

    def revoke_session(self, sid):
        with self._lock:
            return self._sessions.pop(sid, None) is not None
