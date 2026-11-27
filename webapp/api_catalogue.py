"""The Quotation API feature catalogue: one entry per operation, the single source.

The v1 routes, the operation set, the read operations and the stored permission
bits are derived from these entries, and so is the OpenAPI spec
(`scripts/build_api_spec.py`). Audience and caller policy are declared here and
not yet enforced: v1 admission still answers 503 for every operation, and a later
adapter enforces them. Entries are reviewed data; nothing is exposed by inference
(#306, owner decision INT-SQAG-202611270002).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType

from webapp.ai_response_contracts import Field, obj
from webapp.quote_states import REQUEST_STATES


AUDIENCES = frozenset({"service", "staff", "platform"})
SCOPES = frozenset({"quote.write", "quote.read"})
# confirm_first: the basis is confirmed before pricing (the staff app's rule).
# straight_through: a caller key priced without a confirm call (owner decision INT-SQAG-202611270009).
CALLER_POLICIES = frozenset({"confirm_first", "straight_through"})
KINDS = frozenset({"job", "sync"})  # job: 202 then poll; sync: answered in the request.
STABILITIES = frozenset({"stable", "preview"})  # preview stays outside the v1 compatibility promise.
METHODS = frozenset({"GET", "POST"})
# Live sqag_quote_api_callers.operations_mask is a smallint checked 0..255 (migration 012),
# so a stored permission bit is 0..7. A bit's meaning never changes once a key stores it.
MASK_BITS = 8
PATH_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
ARTIFACT_KINDS = ("xlsx", "pdf")
# Field names no caller-facing body may carry: internal authority, cost, AI and audit detail.
FORBIDDEN_FIELDS = (
    "internal_override", "role", "tenant", "tenant_id", "workspace_id", "company_id", "account_id", "grant",
    "view", "confidence", "scores", "prompt", "proposal", "suggestions", "review_choices", "internal_cost",
    "gst", "markup", "supplier", "supplier_notes", "ai_spend", "profile", "admin", "audit", "ledger", "seal",
    "matches", "raw_source", "private_path", "provider_response",
)
# Limits the API sets for itself; the app's own upload limits come from webapp/server.py.
API_LIMITS = MappingProxyType({
    "image_bytes": 12 * 1024 * 1024,
    "notes_utf8_bytes": 16384,
    "basis_lines": 500,
})

_NAME = re.compile(r"[a-z][a-z_]{0,31}", re.ASCII)
_PATH = re.compile(r"/api/v1(?:/(?:[a-z][a-z-]*|\{[a-z_]+\}))+", re.ASCII)
_PARAM = re.compile(r"\{([a-z_]+)\}", re.ASCII)
_HANDLER = re.compile(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
                      re.ASCII)
_COMPONENT = re.compile(r"[A-Z][A-Za-z]{0,63}", re.ASCII)


@dataclass(frozen=True)
class Operation:
    name: str
    scope: str
    method: str
    path: str
    # "module:attribute" of the code that answers the operation today.
    handler: str
    kind: str
    summary: str
    stability: str = "preview"
    description: str = ""
    # Request states the operation may start from; empty when it has no state rule.
    from_states: tuple[str, ...] = ()
    # Stored permission bit (migration 012); None for an operation no stored key can hold.
    mask_bit: int | None = None
    path_params: tuple[tuple[str, Field], ...] = ()
    # Body shapes; None where the shape is not in the catalogue yet (no body, binary, or a later slice).
    request: Field | None = None
    response: Field | None = None
    # Spec component names for the bodies; the staff request is the staff session's schema.
    request_schema: str | None = None
    staff_request_schema: str | None = None
    response_schema: str | None = None
    audiences: frozenset[str] = frozenset({"staff"})
    caller_policies: frozenset[str] = frozenset({"confirm_first"})

    def __post_init__(self):
        audiences, policies = frozenset(self.audiences), frozenset(self.caller_policies)
        object.__setattr__(self, "audiences", audiences)
        object.__setattr__(self, "caller_policies", policies)
        object.__setattr__(self, "from_states", tuple(self.from_states))
        if (type(self.name) is not str or not _NAME.fullmatch(self.name) or self.scope not in SCOPES
                or self.method not in METHODS or self.kind not in KINDS or self.stability not in STABILITIES
                or type(self.handler) is not str or not _HANDLER.fullmatch(self.handler)
                or not audiences or not audiences <= AUDIENCES
                or not policies or not policies <= CALLER_POLICIES):
            raise ValueError(f"Invalid catalogue entry: {self.name!r}")
        # A write changes state, so it is a POST and carries the staff CSRF check; a read is a GET.
        if (self.scope == "quote.write") != (self.method == "POST"):
            raise ValueError(f"Write scope and POST go together: {self.name!r}")
        for text, limit in ((self.summary, 120), (self.description, 1024)):
            if type(text) is not str or len(text) > limit or text != text.strip():
                raise ValueError(f"Invalid text: {self.name!r}")
        if not self.summary:
            raise ValueError(f"Missing summary: {self.name!r}")
        if (len(set(self.from_states)) != len(self.from_states)
                or not set(self.from_states) <= set(REQUEST_STATES)):
            raise ValueError(f"Invalid states: {self.name!r}")
        if self.mask_bit is not None and (type(self.mask_bit) is not int or not 0 <= self.mask_bit < MASK_BITS):
            raise ValueError(f"Invalid permission bit: {self.name!r}")
        if type(self.path) is not str or not _PATH.fullmatch(self.path):
            raise ValueError(f"Invalid path: {self.name!r}")
        names = _PARAM.findall(self.path)
        if (names != [key for key, _ in self.path_params] or len(set(names)) != len(names)
                or any(type(field) is not Field or field.kind != "string" or field.nullable
                       for _, field in self.path_params)):
            raise ValueError(f"Invalid path parameters: {self.name!r}")
        for shape in (self.request, self.response):
            if shape is not None and (type(shape) is not Field or shape.kind != "object"):
                raise ValueError(f"Invalid body shape: {self.name!r}")
        for component in (self.request_schema, self.staff_request_schema, self.response_schema):
            if component is not None and (type(component) is not str or not _COMPONENT.fullmatch(component)):
                raise ValueError(f"Invalid component name: {self.name!r}")
        if self.method == "GET" and (self.request is not None or self.request_schema or self.staff_request_schema):
            raise ValueError(f"A GET operation has no body: {self.name!r}")
        if self.method == "POST" and not self.request_schema:
            raise ValueError(f"A POST operation names its request schema: {self.name!r}")
        if self.staff_request_schema and self.request_schema == self.staff_request_schema:
            raise ValueError(f"Staff and service request schemas differ: {self.name!r}")

    def segments(self) -> tuple[tuple[str, str | Field], ...]:
        """("literal", text) or ("param", field) per path segment after the leading slash."""
        params = dict(self.path_params)
        return tuple(("param", params[part[1:-1]]) if part.startswith("{") else ("literal", part)
                     for part in self.path.split("/")[1:])

    def route_pattern(self) -> str:
        """Exact path grammar: literal segments, the shared ID grammar or a closed enum."""
        params = dict(self.path_params)
        pieces = _PARAM.split(self.path)
        return "".join(
            re.escape(piece) if index % 2 == 0
            else ("(?:" + "|".join(map(re.escape, params[piece].enum)) + ")" if params[piece].enum else PATH_ID)
            for index, piece in enumerate(pieces))


def _segment_accepts(field, text):
    return text in field.enum if field.enum else re.fullmatch(PATH_ID, text, re.ASCII) is not None


def _segments_overlap(left, right):
    (left_kind, left_value), (right_kind, right_value) = left, right
    if left_kind == right_kind == "literal":
        return left_value == right_value
    if left_kind == "literal":
        return _segment_accepts(right_value, left_value)
    if right_kind == "literal":
        return _segment_accepts(left_value, right_value)
    if left_value.enum and right_value.enum:
        return bool(set(left_value.enum) & set(right_value.enum))
    values = left_value.enum or right_value.enum
    return not values or any(re.fullmatch(PATH_ID, value, re.ASCII) for value in values)


def templates_overlap(left: Operation, right: Operation) -> bool:
    """True when one request could match both entries, so routing would depend on entry order."""
    a, b = left.segments(), right.segments()
    return left.method == right.method and len(a) == len(b) and all(map(_segments_overlap, a, b))


class ApiCatalogue:
    """Validated, immutable set of operations and the views derived from it."""

    def __init__(self, operations):
        self._operations = tuple(operations)
        if not self._operations or any(type(op) is not Operation for op in self._operations):
            raise ValueError("Invalid catalogue")
        names = [op.name for op in self._operations]
        bits = [op.mask_bit for op in self._operations if op.mask_bit is not None]
        if len(set(names)) != len(names) or len(set(bits)) != len(bits):
            raise ValueError("Duplicate catalogue entry")
        for index, left in enumerate(self._operations):
            for right in self._operations[index + 1:]:
                if templates_overlap(left, right):
                    raise ValueError(f"Overlapping paths: {left.name!r} and {right.name!r}")
        self._routes = tuple((op.method, re.compile(op.route_pattern(), re.ASCII), op.name)
                             for op in self._operations)
        self._by_name = {op.name: op for op in self._operations}

    @property
    def operations(self) -> tuple[Operation, ...]:
        return self._operations

    def get(self, name) -> Operation | None:
        return self._by_name.get(name)

    def names(self, *, scope=None) -> frozenset[str]:
        return frozenset(op.name for op in self._operations if scope is None or op.scope == scope)

    def stored_operations(self) -> tuple[str, ...]:
        """Operation names holding a stored permission bit, in bit order."""
        return tuple(op.name for op in sorted((op for op in self._operations if op.mask_bit is not None),
                                              key=lambda op: op.mask_bit))

    def operations_from_mask(self, mask) -> frozenset[str]:
        """Read a stored operations_mask; bits no entry holds grant nothing."""
        if type(mask) is not int or not 0 <= mask < 1 << MASK_BITS:
            raise ValueError("Invalid operations mask")
        return frozenset(op.name for op in self._operations
                         if op.mask_bit is not None and mask & (1 << op.mask_bit))

    def match(self, method, path) -> str | None:
        return next((name for verb, pattern, name in self._routes
                     if method == verb and pattern.fullmatch(path)), None)


_REQUEST_ID = ("id", Field("string", nonblank=True))
_REVISION = Field("integer", minimum=1)
_ACCEPTED_JOB = obj(
    quote_request_id=Field("string", nonblank=True),
    job_id=Field("string", nonblank=True),
    status=Field("string", enum=("queued",)),
    links=Field("object", fields=tuple(
        (key, Field("string", nonblank=True, optional=True)) for key in ("request", "job", "result", *ARTIFACT_KINDS))),
)
# Every operation is answered today by the disabled admission boundary (503 or 404).
_ADMISSION = "webapp.server:QuoteRunnerHandler.dispatch_quote_admission"
# Design-tool keys run create, draft and the reads; price and export stay staff-only (owner, revision 3).
_SERVICE_AND_STAFF = frozenset({"service", "staff"})
_BOTH_POLICIES = frozenset({"confirm_first", "straight_through"})

CATALOGUE = ApiCatalogue((
    Operation(
        "create", "quote.write", "POST", "/api/v1/quote-requests", _ADMISSION, "job",
        "Create request and queue analysis", "stable", mask_bit=0,
        request=obj(
            design_files=Field("array", item=obj(name=Field("string", nonblank=True), data_url=Field("string"))),
            notes=Field("string", optional=True),
            client_reference=Field("string", nonblank=True, optional=True),
        ),
        response=_ACCEPTED_JOB, request_schema="CreateQuoteRequest", response_schema="AcceptedJob",
        audiences=_SERVICE_AND_STAFF, caller_policies=_BOTH_POLICIES),
    Operation(
        "draft", "quote.write", "POST", "/api/v1/quote-requests/{id}/drafts", _ADMISSION, "job",
        "Queue a new basis revision", "stable",
        from_states=("awaiting_confirmation", "completed", "needs_input", "failed"),
        mask_bit=1, path_params=(_REQUEST_ID,), request=obj(notes=Field("string", optional=True)),
        response=_ACCEPTED_JOB, request_schema="QueueDraftRequest", response_schema="AcceptedJob",
        audiences=_SERVICE_AND_STAFF, caller_policies=_BOTH_POLICIES),
    Operation(
        "price", "quote.write", "POST", "/api/v1/quote-steps/price", _ADMISSION, "job",
        "Confirm basis and queue pricing", "stable",
        description=("Staff and service callers must use this same endpoint and QuoteApplicationService for "
                     "match/normalise/price. Derive the request schema and response view from authenticated "
                     "credentials, never body fields. Staff edits do not create a saved quote or grant copied "
                     "authority."),
        from_states=("awaiting_confirmation",), mask_bit=2,
        request=obj(quote_request_id=Field("string", nonblank=True), basis_revision=_REVISION,
                    confirmed_basis_revision=_REVISION),
        response=_ACCEPTED_JOB, request_schema="PriceRequest", staff_request_schema="StaffPriceRequest",
        response_schema="AcceptedJob"),
    Operation(
        "export", "quote.write", "POST", "/api/v1/quote-requests/{id}/exports", _ADMISSION, "job",
        "Queue estimate exports", "stable", from_states=("awaiting_confirmation",),
        mask_bit=3, path_params=(_REQUEST_ID,),
        request=obj(basis_revision=_REVISION, confirmed_basis_revision=_REVISION,
                    formats=Field("array", item=Field("string", enum=ARTIFACT_KINDS))),
        response=_ACCEPTED_JOB, request_schema="ExportRequest", response_schema="AcceptedJob"),
    Operation(
        "poll", "quote.read", "GET", "/api/v1/jobs/{job_id}", _ADMISSION, "sync", "Poll job", "stable",
        mask_bit=4, path_params=(("job_id", Field("string", nonblank=True)),), response_schema="JobView",
        audiences=_SERVICE_AND_STAFF, caller_policies=_BOTH_POLICIES),
    Operation(
        "basis", "quote.read", "GET", "/api/v1/quote-requests/{id}", _ADMISSION, "sync", "Read safe basis",
        "stable", mask_bit=5, path_params=(_REQUEST_ID,), response_schema="BasisView",
        audiences=_SERVICE_AND_STAFF, caller_policies=_BOTH_POLICIES),
    Operation(
        "result", "quote.read", "GET", "/api/v1/quote-requests/{id}/result", _ADMISSION, "sync",
        "Read committed estimate", "stable", mask_bit=6, path_params=(_REQUEST_ID,),
        response_schema="CustomerEstimateView", audiences=_SERVICE_AND_STAFF, caller_policies=_BOTH_POLICIES),
    Operation(
        "artifact", "quote.read", "GET", "/api/v1/quote-requests/{id}/artifacts/{kind}", _ADMISSION, "sync",
        "Read authenticated estimate artifact", "stable", mask_bit=7,
        path_params=(_REQUEST_ID, ("kind", Field("string", enum=ARTIFACT_KINDS))),
        audiences=_SERVICE_AND_STAFF, caller_policies=_BOTH_POLICIES),
))
