"""Disabled quote admission boundary. No registry implementation or work engine.

Only synthetic tests inject a registry. No files, database, positive cache, token
issuance or deployed fallback live here. Tokens are high-entropy machine secrets,
not passwords. Admission is not permission to dispatch work.
"""

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import math
import re
import time
from typing import Protocol

from webapp.quote_context import QuoteContext, QUOTE_OPERATIONS


SERVICE_HEADER = "X-SQAG-Service-Authorization"
SERVICE_FLAG = "SQAG_QUOTE_API_SERVICE_ENABLED"
STAFF_FLAG = "SQAG_QUOTE_API_STAFF_TRANSPORT_ENABLED"
TOKEN_DOMAIN = b"sqag-quote-api-secret-v1\0"
DUMMY_VERIFIER = hashlib.sha256(TOKEN_DOMAIN + b"unknown-credential-dummy").digest()
_TOKEN_RE = re.compile(r"sqagq1\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{43})", re.ASCII)
_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_ROUTES = (
    ("POST", r"/api/v1/quote-requests", "create"),
    ("POST", rf"/api/v1/quote-requests/{_ID}/drafts", "draft"),
    ("POST", r"/api/v1/quote-steps/price", "price"),
    ("POST", rf"/api/v1/quote-requests/{_ID}/exports", "export"),
    ("GET", rf"/api/v1/jobs/{_ID}", "poll"),
    ("GET", rf"/api/v1/quote-requests/{_ID}", "basis"),
    ("GET", rf"/api/v1/quote-requests/{_ID}/result", "result"),
    ("GET", rf"/api/v1/quote-requests/{_ID}/artifacts/(?:xlsx|pdf)", "artifact"),
)
_ERRORS = {
    "AUTH_REQUIRED": (401, "Authentication is required.", False),
    "SCOPE_DENIED": (403, "This operation is not permitted.", False),
    "CSRF_INVALID": (403, "The session request could not be verified.", False),
    "NOT_FOUND": (404, "Not found.", False),
    "INVALID_REQUEST": (400, "The request is invalid.", False),
    "RATE_LIMITED": (429, "Too many requests. Try again later.", True),
    "CAPACITY_UNAVAILABLE": (503, "Quote operations are unavailable.", True),
}


class AdmissionError(Exception):
    def __init__(self, code):
        self.code = code
        self.status, self.message, self.retryable = _ERRORS[code]
        super().__init__(code)

    def payload(self, reference):
        return {"error": {"code": self.code, "message": self.message,
                          "reference": reference, "retryable": self.retryable}}


def flag_enabled(value):
    """Only the documented exact value enables a transport; no permissive parser."""
    return value == "true"


def quote_operation(method, path):
    return next((operation for verb, pattern, operation in _ROUTES
                 if method == verb and re.fullmatch(pattern, path, re.ASCII)), None)


def secret_verifier(secret):
    if type(secret) is not bytes or len(secret) != 32:
        raise ValueError("Invalid secret length")
    return hashlib.sha256(TOKEN_DOMAIN + secret).digest()


def parse_service_token(value):
    """Strict unpadded canonical base64url; never trim an alternate spelling."""
    if type(value) is not str or len(value) != 73:
        return None
    match = _TOKEN_RE.fullmatch(value)
    if not match:
        return None
    parts = []
    for text, size in zip(match.groups(), (16, 32)):
        raw = base64.b64decode(text + "=" * (-len(text) % 4), altchars=b"-_", validate=True)
        if len(raw) != size or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != text:
            return None
        parts.append(raw)
    return match[1], parts[1]


@dataclass(frozen=True, slots=True)
class ServiceCredential:
    credential_id: str
    verifier: bytes = field(repr=False)
    tenant_id: str
    workspace_id: str
    caller_id: str
    owner_subject: str
    revision: str
    authority_revision: str
    operations: frozenset[str]
    expires_at: float
    enabled: bool = True
    tenant_enabled: bool = True
    revoked: bool = False

    def __post_init__(self):
        if type(self.verifier) is not bytes or len(self.verifier) != 32:
            raise ValueError("Invalid credential record")
        if not re.fullmatch(r"[A-Za-z0-9_-]{22}", self.credential_id, re.ASCII):
            raise ValueError("Invalid credential record")
        for value in (self.tenant_id, self.workspace_id, self.caller_id, self.owner_subject,
                      self.revision, self.authority_revision):
            if type(value) is not str or not value or len(value) > 512:
                raise ValueError("Invalid credential record")
        if (type(self.expires_at) not in (int, float) or not math.isfinite(self.expires_at)
                or any(type(v) is not bool for v in (self.enabled, self.tenant_enabled, self.revoked))):
            raise ValueError("Invalid credential record")
        ops = frozenset(self.operations)
        if not ops <= QUOTE_OPERATIONS:
            raise ValueError("Invalid credential record")
        object.__setattr__(self, "operations", ops)


class CredentialRegistry(Protocol):
    def lookup(self, credential_id: str) -> ServiceCredential | None: ...


class QuoteApiAdmission:
    """Own verification dependencies. Read registry state anew on every request."""

    def __init__(self, *, registry: CredentialRegistry | None = None, clock=time.time):
        self._registry = registry
        self._clock = clock

    def admit(self, *, operation, headers, service_enabled, staff_enabled, reference,
              singleton_header, rate_limited, staff_context, staff_mutation_guard):
        if operation not in QUOTE_OPERATIONS:
            raise AdmissionError("NOT_FOUND")
        service_values = headers.get_all(SERVICE_HEADER, [])
        service = bool(service_values)
        if not (service_enabled if service else (staff_enabled or service_enabled)):
            raise AdmissionError("NOT_FOUND")
        # Shared fixed admission bucket: changing IDs, auth modes or operations
        # cannot bypass the effective-client budget. Runs before registry/hash.
        if rate_limited():
            raise AdmissionError("RATE_LIMITED")
        if service:
            value, valid = singleton_header(headers, SERVICE_HEADER, reject_comma=True)
            # The legacy singleton helper trims; compare the original too.
            if (not valid or value != service_values[0] or headers.get_all("Cookie", [])
                    or any(name.lower() in {"origin", "referer"} or name.lower().startswith("sec-fetch-")
                           for name in headers.keys())):
                raise AdmissionError("AUTH_REQUIRED")
            context = self._service_context(value, reference)
        else:
            if not staff_enabled:
                if headers.get_all("Cookie", []):
                    raise AdmissionError("NOT_FOUND")
                self._service_context("", reference)  # Absent credentials take the dummy path.
                raise AdmissionError("AUTH_REQUIRED")
            if len(headers.get_all("Cookie", [])) != 1:
                self._service_context("", reference)
                raise AdmissionError("AUTH_REQUIRED")
            context = staff_context(reference)
            if context is None or context.auth_mode != "staff":
                raise AdmissionError("AUTH_REQUIRED")
        if operation not in context.operations:
            raise AdmissionError("SCOPE_DENIED")
        if not service and operation in {"create", "draft", "price", "export"}:
            if not staff_mutation_guard():
                raise AdmissionError("CSRF_INVALID")
        return context

    def _service_context(self, token, reference):
        parsed = parse_service_token(token)
        record = None
        if parsed and self._registry is not None:
            try:
                record = self._registry.lookup(parsed[0])
            except Exception:
                # Never expose a registry exception or use another auth mode.
                raise AdmissionError("CAPACITY_UNAVAILABLE") from None
        supplied = secret_verifier(parsed[1] if parsed else bytes(32))
        expected = record.verifier if isinstance(record, ServiceCredential) else DUMMY_VERIFIER
        matches = hmac.compare_digest(supplied, expected)
        if not parsed:
            raise AdmissionError("AUTH_REQUIRED")
        if self._registry is None:
            raise AdmissionError("CAPACITY_UNAVAILABLE")
        if (not isinstance(record, ServiceCredential) or not matches or record.credential_id != parsed[0]
                or not record.enabled or not record.tenant_enabled or record.revoked
                or self._clock() >= record.expires_at):
            raise AdmissionError("AUTH_REQUIRED")
        return QuoteContext(
            "service", record.owner_subject, record.tenant_id, record.workspace_id,
            record.caller_id, record.revision, record.operations, "CustomerEstimateView",
            record.authority_revision, (("registry_revision", record.authority_revision),), reference,
        )
