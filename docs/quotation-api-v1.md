# Quotation API v1

> Contract proposal, not implemented. This document and its machine-readable spec describe future behaviour only.

## Purpose and boundary

Swooshz Design's own server calls this service, receives estimate data, and passes
the safe view to the customer's hosted page. Customers never call this API directly.
Start with synthetic sandbox/test tenants. The main platform creates accounts;
account creation, billing and public exposure are outside this contract slice.
The [OpenAPI spec](quotation-api-v1.openapi.json) defines the external service
schemas and reserves the staff-authenticated price schema for S3a. Both files are
directly maintained; contract tests check their agreement.
Its `x-` extensions record non-schema policies, including unknown-query rejection,
state transitions, confirmation, money, replay, quotas and retention. A generic
OpenAPI validator does not enforce those extensions or cross-field arithmetic.

One future quote application service owns admission, immutable authority snapshots,
state, worker claims, pricing and publication. HTTP adapters call that service,
never other HTTP handlers. Inject the clock, queue, storage and AI gateway.
In S3a the staff UI must use the same `/api/v1` endpoints and application service
as external callers for draft, match, price and XLSX/PDF output, including polling
and artifact delivery. Transport changes must preserve the current UI and workflow.
The server derives the caller view from the authenticated credential, never a client
flag. A validated staff session selects the authorised internal projection; a service
credential selects only CustomerEstimateView. Staff mutations retain same-origin
and session-bound CSRF checks, plus current role/owner/edit and grant checks.
Settings, pricing imports, saved-session management, basis chat, review choices,
profiles, AI spend, admin, audit and operational tools stay on internal endpoints;
they are never exposed by the external API.

### Staff UI migration in S3a

Every move below is mandatory and uses the same application service. Existing
staff payload/response mapping is retained by authenticated adapters, with richer
staff draft/export/read schemas completed in S3a. There is no second matcher or
generation engine and no failed-request fallback to legacy routes.

| Staff UI call today | Required destination or retained internal route |
| --- | --- |
| Draft `startJob`, recovery `type=draft` via `POST /api/jobs` | Move to `/api/v1/quote-requests/{id}/drafts`, sharing create/draft orchestration. |
| `generate`/`generate_pdf` jobs and recovery | Move to `/api/v1/quote-requests/{id}/exports`; keep Excel default and explicit PDF. |
| Quote-job `pollJob`, normal and restored polling via `GET /api/jobs/<id>` | Move to `/api/v1/jobs/{job_id}`; restored job type selects routing. Basis-chat polling stays internal. |
| All three `/api/line-items/normalize` calls, including duplicate/Modify before first save | Move to `/api/v1/quote-steps/price`; preserve editable rows and grant checks without saving a new quote or granting copied authority. |
| Output/download/PDF-view anchors, dashboard actions and saved-list/detail output URLs | Move to v1 proof-bound artifact URLs; retain staff PDF inline disposition. These include navigations, not only fetch calls. |
| `basis_chat` dispatch and lookups via old POST/GET jobs | Stay internal, selected by fixed operation allowlist; no generic external job-type passthrough. |
| `/api/quote-sessions/pricing-selections` | Stay internal; `apply_pricing_selections` admits authority, then uses the shared pricing service. |
| `/api/quote-sessions` save/list/detail/delete/retry | Stay internal for autosave, dashboard, restoration, duplicate/Modify and spend; share repositories and publication reader. |
| `/api/settings/profiles...`, `/api/profiles` save/delete/export/read | Stay internal. |
| `/api/settings/pricing-references...`, `/api/pricing-reference/template.xlsx` detail/preview/save/delete/import/download | Stay internal. |
| `/api/feedback...`, `/api/session`, `/api/log`, `/api/health`, `/logout` | Stay internal; health retains its existing public policy. |
| AI spend/admin/audit/support and Platform launch/finalisation | Stay internal; no new UI fetch is implied. |

## Authentication and tenant isolation

Require TLS in a later authorised environment. Service callers supply exactly one
nonblank `X-SQAG-Service-Authorization` header. Reject duplicates, comma combinations,
mixed staff/service authentication and browser-origin service requests. Never put
service credentials in a browser, body, URL, artifact link or log. The platform launch
credential is not quote authority. Compare fixed-length credential verifiers in
constant time; absent and unknown credentials follow an equivalent denial path.

Use one independently revocable credential per caller and tenant, bound to a stable
service subject, permitted operations, enabled state and expiry. Rotation replaces
the verifier atomically while retaining that subject and ownership; the old
credential stops working. Revocation blocks admission, polling, replay and downloads.
No credential is created or changed by this proposal.

The reserved StaffSession and StaffCsrf schemes describe S3a, not implemented
authentication. Read operations require the staff session; every staff POST also
requires its bound CSRF header. Global security applies to every operation with no
anonymous override. Reject mixed staff/service credentials before schema selection.
The price request's schema union is documentation, not authority: select its branch
from authenticated identity before validation, never by testing which body fits.
Staff request/response reservations are completed in S3a before their types freeze;
the external service schemas cannot be widened by that work.

Derive tenant and caller solely from validated credentials. Scope every request,
job, receipt and artifact lookup to both tenant and exact service owner. Foreign
and missing IDs produce the same 404. Staff authority does not grant service
ownership or vice versa. There is no cross-caller sharing or tenant override.
The tenant admin picks the active price book and profile. Service callers cannot select,
upload or replace pricing authority. Capture the selected `pricing-catalog.json`,
`quotation-layout.xlsx` and `layout-rules.json` revisions before analysis.
Missing or ambiguous configuration blocks work without a bundled/default fallback.

## Design and confirmation

Require booth renders as images or render-containing PDFs, not a text-only item list.
When missing, return DESIGN_REQUIRED with this exact message:

Please upload the booth render images first so I can analyze the design and prepare the quote.

Analysis runs automatically after create. Return a safe basis with a revision,
measurements, materials, inclusions and explicit unknowns. Swooshz Design must
confirm that basis before pricing, by sending equal current `basis_revision` and
`confirmed_basis_revision` values to the price step. Confirmation is not inferred
from successful analysis. Exports require successful pricing of that same confirmed
revision. BasisView exposes nullable `priced_basis_revision`: null before pricing,
then the current revision after success, even when some prices remain unresolved.
A changed basis, book or layout resets it and invalidates prior confirmation, prices
and links; stale commands return BASIS_STALE. Revise notes and queue another draft
to resolve scope; no internal brief file is exposed for user approval.

## Endpoints

All paths use `/api/v1`. POST operations return 202 only after durable admission,
with Location, Retry-After and safe links. Poll first; there are no webhooks.
No query parameters are accepted, including `view=internal`. Unknown JSON fields
are rejected recursively within the selected auth schema. The four external request
schemas are closed service contracts; richer staff contracts cannot relax them.
Schema checks supplement, never replace, authorisation.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/quote-requests` | Create request and queue analysis. |
| POST | `/api/v1/quote-requests/{id}/drafts` | Queue a new basis revision. |
| POST | `/api/v1/quote-steps/price` | Confirm basis and queue pricing. |
| POST | `/api/v1/quote-requests/{id}/exports` | Queue estimate exports. |
| GET | `/api/v1/jobs/{job_id}` | Poll job. |
| GET | `/api/v1/quote-requests/{id}` | Read safe basis. |
| GET | `/api/v1/quote-requests/{id}/result` | Read committed estimate. |
| GET | `/api/v1/quote-requests/{id}/artifacts/{kind}` | Read authenticated estimate artifact. |

For service callers the price step accepts only the request ID and persisted basis
revision with explicit confirmation, never editable rows, prices, grants or catalog
overrides. For staff-authenticated callers only, the same endpoint and service must
accept staff-edited basis rows with quantities, units and selection references via
StaffPriceRequest. Selection references convey no authority: revalidate previously
admitted selections and grants against current owner, row and price-book revision.
Staff pricing before the first save is non-persisting and cannot grant copied
authority. Full staff field mapping is completed in S3a under existing checks.
Exports request XLSX, PDF or both; the first external slice offers JSON plus both
formats. PDF conversion already exists internally, but external estimate safety
and fidelity still need qualification. Artifact GET is an authenticated byte stream
with the declared XLSX or PDF media type, not a JSON body. It rechecks owner,
current publication proof, authority revision, expiry and exact bytes. Links are
not bearer authority. A result before committed publication returns RESULT_NOT_READY.
JSON-only use still requires an export requesting at least one XLSX/PDF artifact;
this is intentional for v1. Result JSON is committed with that publication.
All JSON, errors and downloads use `Cache-Control: private, no-store`.

## JSON examples

All values are reserved synthetic examples. The tiny PNG is a schema fixture,
not proof of a usable booth design. Each labelled example names its spec schema.
Headers supply credentials securely outside these bodies, and every POST also
requires an `Idempotency-Key`, for example `request-example-001`.

### StaffPriceRequest

```json
{
  "rows": [
    {
      "row_id": "row-example",
      "description": "Synthetic edited surface",
      "quantity": 12,
      "unit": "sqm",
      "selections": []
    }
  ]
}
```

### CreateQuoteRequest

```json
{
  "design_files": [
    {
      "name": "render.test.png",
      "data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
    }
  ],
  "notes": "Synthetic booth render; confirm the measured scope.",
  "client_reference": "design.test-001"
}
```

### QueueDraftRequest

```json
{
  "notes": "Confirm the displayed dimensions before pricing."
}
```

### PriceRequest

```json
{
  "quote_request_id": "qr-example",
  "basis_revision": 1,
  "confirmed_basis_revision": 1
}
```

### ExportRequest

```json
{
  "basis_revision": 1,
  "confirmed_basis_revision": 1,
  "formats": [
    "xlsx",
    "pdf"
  ]
}
```

### AcceptedJob

```json
{
  "quote_request_id": "qr-example",
  "job_id": "job-example",
  "status": "queued",
  "links": {
    "job": "/api/v1/jobs/job-example",
    "request": "/api/v1/quote-requests/qr-example"
  }
}
```

### BasisView

```json
{
  "quote_request_id": "qr-example",
  "state": "awaiting_confirmation",
  "basis_revision": 1,
  "basis": [
    {
      "description": "Synthetic measured surface",
      "quantity": 12,
      "unit": "sqm",
      "measurements": [
        "12 sqm"
      ],
      "materials": [],
      "inclusions": [],
      "needs_confirmation": [
        "Confirm material and measured area."
      ]
    }
  ],
  "uploads_expire_at": "2026-11-13T00:00:00Z",
  "expires_at": "2026-12-06T00:00:00Z",
  "links": {
    "request": "/api/v1/quote-requests/qr-example"
  },
  "priced_basis_revision": null
}
```

### JobView

```json
{
  "job_id": "job-example",
  "quote_request_id": "qr-example",
  "status": "completed",
  "request_state": "awaiting_confirmation",
  "created_at": "2026-11-06T00:00:00Z",
  "updated_at": "2026-11-06T00:00:01Z",
  "expires_at": "2026-12-06T00:00:00Z",
  "poll_after_seconds": 2,
  "error": null,
  "links": {
    "request": "/api/v1/quote-requests/qr-example"
  }
}
```

### CustomerEstimateView

```json
{
  "quote_request_id": "qr-example",
  "status": "completed",
  "document_type": "estimate",
  "notice": "Estimate only. Subject to scope and price confirmation.",
  "currency": "SGD",
  "currency_decimal_places": 2,
  "lines": [
    {
      "description": "Synthetic measured surface",
      "quantity": 12,
      "unit": "sqm",
      "needs_confirmation": [],
      "price": {
        "status": "priced",
        "unit_price": "10.00",
        "amount": "120.00"
      }
    },
    {
      "description": "Synthetic custom feature",
      "quantity": 1,
      "unit": "set",
      "needs_confirmation": [],
      "price": {
        "status": "unresolved",
        "unit_price": null,
        "amount": null,
        "display": "price to be discussed"
      }
    }
  ],
  "totals": {
    "status": "partial",
    "known_subtotal": "120.00",
    "subtotal": null,
    "total": null,
    "unresolved_line_count": 1,
    "label": "Known-priced subtotal; excludes unresolved items and tax"
  },
  "tax": null,
  "expires_at": "2026-12-06T00:00:00Z",
  "links": {
    "xlsx": "/api/v1/quote-requests/qr-example/artifacts/xlsx",
    "pdf": "/api/v1/quote-requests/qr-example/artifacts/pdf"
  }
}
```

### Error

```json
{
  "error": {
    "code": "PRICING_REFERENCE_UNAVAILABLE",
    "message": "The selected pricing reference is unavailable.",
    "reference": "ERR-0123ABCD",
    "retryable": false
  }
}
```

## CustomerEstimateView and artifact policy

Build fresh objects from the recursive schema allowlist. Never copy a staff payload
and remove a few fields. Allow only estimate identity/status/notice, currency and
precision, safe descriptions, quantities, units, confirmation needs, approved sale
prices, totals, authorised tax summary, expiry and authenticated links.
Basis, job, error and nested objects use their own closed schemas too.
Only server-derived staff capability can select the existing internal projection;
no service flag can do it. Keep caches and response models separate by auth mode.

### Explicit forbidden-field list

Never emit these names or their information, including in prose, hidden workbook
parts, document metadata, errors or nested fields: final quotation, internal cost,
supplier, supplier_notes, internal_cost, GST, markup, internal suggestions,
review choices, confidence, scores, prompts, AI proposals, AI spend, profile/admin/audit
data, raw analysis, pricing matches, grants, seals, ledger, private paths, raw source
files, provider responses and diagnostic details. The sole tax exception is the
tenant-admin-authorised customer tax summary; it reveals no private calculation data.

### Export safety

JSON, XLSX and PDF follow the same allowlist and estimate wording. Preserve the
selected profile layout and formatting rules: bold headers, readable quantity
widths, centred quantities, thousands separators, dynamic totals and a header
logo/details group within the print area. Use `sqm`, not `m2`. Strip disallowed
content rather than merely hiding columns or sheets. Treat all free text as
untrusted: leading `=`, `+`, `-`, `@` stays literal in spreadsheet/CSV cells.
Only trusted computed totals may become formulas. XLSX generation continues via
`scripts/generate_quote.py` without third-party dependencies; external rendering
policy is future work. No runtime or UI change is part of this slice.

## Money and unresolved lines

Money uses nonnegative fixed-decimal strings and decimal arithmetic, with the
captured currency's `currency_decimal_places` (0 to 4). No binary-float money,
exponent notation, NaN, Infinity, implicit currency or FX fallback. Round each
quantity-times-unit-price amount to that precision using decimal half-up, then
sum line amounts. The safe sale price must come from verified tenant authority.

Unresolved price fields are null and display exactly `price to be discussed`.
They never become zero or disappear. Unknown quantity/unit remains null with a
confirmation need and an unresolved price. A genuine verified zero price is
allowed only as a priced line, never as an unknown placeholder.
`known_subtotal` sums priced lines only; it is null if none is priced.
Always label it `Known-priced subtotal; excludes unresolved items and tax`.
If any line is unresolved, totals status is partial, full `subtotal` and `total`
are null, and the unresolved count is exact. Configured tax may show its approved
label/rate but its amount stays null on a partial estimate.

Tax follows the tenant admin's captured setting, never caller input. Blank tax
means `tax: null` and no tax line in either document. With all lines priced,
subtotal is the sum; configured tax amount is subtotal times rate rounded half-up;
total is subtotal plus that amount, or subtotal when tax is blank. A complete
total remains an estimate. Notice text is always estimate wording and conveys no
acceptance or payment obligation. Partial examples above have no full total.

## Idempotency and recovery

Every mutation requires a single `Idempotency-Key`: 1 to 128 ASCII characters,
starting alphanumeric, then alphanumeric, dot, underscore or hyphen. No whitespace,
commas or duplicate headers. Scope keys by tenant, caller, HTTP method and exact
resource path. Validate and authorise before checking replay. Canonicalise validated
JSON with sorted keys and decoded file SHA-256 digests. Persist that digest,
captured authority revision and original job/request identity atomically before
enqueue. Same key/body returns the original 202 receipt without another work quota
or gateway call; changed body gives 409 IDEMPOTENCY_CONFLICT. Rotation preserves
the scope. A changed authority revision never silently reprices a replay.

Receipts last 24 hours from admission; at the boundary they are expired. An owned
stale key returns RESULT_EXPIRED rather than silently creating work. After receipt
purge, clients must use a fresh key; longer duplicate detection is not guaranteed.
An owned minimal expiry marker may be retained only within result retention.
Authenticate and check owner again on replay, even after restart or revocation.
One in-flight mutation per request revision, durable compare-and-swap/leases and
atomic publication prevent racing exports. Reconcile uncertain provider or
publication outcomes; RECOVERY_REQUIRED never triggers a second paid call.

## States and transitions

The following persisted service-request edges are closed; unknown states/edges
are rejected. Confirmation and persisted-revision policies describe service callers;
staff pre-save normalisation remains non-persisting under its existing checks.
Creation enters drafting with a queued job. Jobs never restart from a terminal state.
The future application service owns these transitions; test fakes do not implement it.

| Request state | Allowed next states |
| --- | --- |
| drafting | awaiting_confirmation, needs_input, failed, expired |
| awaiting_confirmation | drafting, pricing, generating, expired |
| pricing | awaiting_confirmation, needs_input, failed, expired |
| generating | completed, needs_input, failed, expired |
| completed | drafting, expired |
| needs_input | drafting, expired |
| failed | drafting, expired |
| expired | none |

| Job state | Allowed next states |
| --- | --- |
| queued | running, failed |
| running | completed, failed |
| completed | none |
| failed | none |

Draft commands are allowed only from awaiting_confirmation, completed, needs_input
or failed. Price commands start only from awaiting_confirmation with explicit
current confirmation. Pricing returns there on success; export may then enter
generating only with that priced revision. Completion may contain unresolved
prices when scope is valid. Missing scope becomes needs_input. Reads never advance
state. Expiry invalidates active work and links; late workers cannot resurrect it.
An error inside a failed job uses the same safe error detail as an HTTP failure.

## Limits and sandbox quotas

Preserve 8 files, 12 MiB per image, 48 MiB per PDF, 96 PDF pages across the entire
request and 128 MiB aggregate decoded source bytes. MiB means 1,048,576 bytes.
Check encoded-body caps before decode and MIME/signature, base64, dimensions,
decompression and page limits before analysis. Remote URLs are rejected.
Notes are at most 16 KiB UTF-8 (the schema character limit is only a preliminary
bound), client references 128 characters and basis/results 500 rows.

Owner-approved provisional sandbox limits are 15 creates/minute/tenant, 2 active
jobs/tenant, 100 accepted requests per UTC day/tenant and 120 polls/minute/caller.
Use shared atomic counters, retain effective-IP abuse limits and a bounded global
worker cap. Replays consume no second work quota. Return 429 with Retry-After for
rate/quota rejection; 503 for unavailable capacity. Wait at least 2 seconds between
polls, then use capped backoff/jitter up to 60 seconds. These are policy limits, not
measured throughput. A global worker count and encoded-body cap need qualification
before implementation; this spec does not invent measured capacity.

## Stable errors

Errors have only `error: {code, message, reference, retryable}`. Use a stable code,
safe bounded message and `ERR-` plus eight uppercase hexadecimal characters. Match
that reference to a content-free server event; never return raw exceptions or
provider text. Retryable describes permitted recovery, not permission to replay
uncertain work. Failed job polls return HTTP 200 with the same safe error detail.
HTTP codes and meanings are fixed as follows.

| HTTP | Machine-readable codes |
| --- | --- |
| 400 | INVALID_REQUEST |
| 401 | AUTH_REQUIRED |
| 403 | SCOPE_DENIED, CSRF_INVALID |
| 404 | NOT_FOUND |
| 409 | BASIS_STALE, IDEMPOTENCY_CONFLICT, RESULT_NOT_READY, STATE_CONFLICT |
| 410 | RESULT_EXPIRED |
| 413 | UPLOAD_LIMIT_EXCEEDED |
| 415 | UNSUPPORTED_MEDIA |
| 422 | DESIGN_REQUIRED, NEEDS_INPUT, PRICING_REFERENCE_UNAVAILABLE |
| 429 | RATE_LIMITED, QUOTA_EXCEEDED |
| 502 | ANALYSIS_FAILED |
| 503 | CAPACITY_UNAVAILABLE, RECOVERY_REQUIRED |

Use INVALID_REQUEST for malformed/unknown fields or keys; AUTH_REQUIRED for absent,
invalid or revoked credentials; SCOPE_DENIED for disallowed operations. CSRF_INVALID
is reserved for the later staff adapter. BASIS_STALE covers revision mismatch;
STATE_CONFLICT covers otherwise inadmissible operations. RESULT_EXPIRED is visible
only to the owner of an expiry receipt; foreign IDs stay NOT_FOUND. Missing design,
missing scope and missing pricing authority fail before expensive work. Map typed
causes; never parse provider prose. Error schemas constrain shape, while safe text
selection and content-free logging require later runtime tests.

## Retention and versioning

Owner policy is 7-day uploads, 30-day results (JSON, XLSX and PDF), and 24-hour
replay receipts. This proposal anchors uploads/results to request admission and
receipts to mutation admission; confirm these timer anchors before implementation.
Expiry is inclusive at the deadline and returned in UTC `expires_at` fields (uploads use
`uploads_expire_at`). Reads do not extend retention. Deny links immediately on
expiry; reconciliation must cover objects, metadata, scratch, evidence and backups.
Legal holds and provider/backup deletion windows remain unresolved launch gates;
no promise of complete erasure is made. A later scoped deletion API is required
before real external retention is offered; DELETE is deliberately absent here.

Freeze v1 types, enums and error meanings. Additive optional fields require
consumer tests. Clients must ignore unknown response fields; server projection
schemas remain closed allowlists, including recursively, and request schemas stay
closed. New fields still need projection/privacy and consumer tests. Breaking changes use
`/api/v2`. Never fall back silently to unversioned endpoints.

## Security invariants and qualification boundary

Admission, ownership, quota and publication proof checks precede every success,
including cached results and replay. Untrusted notes/media/model output never
grant scope or authority. No default-tenant fallback, forged price, role claim or
internal projection flag is admitted. Service callers cannot submit arbitrary rows;
staff-edited rows pass the authenticated staff contract and existing authority checks.
Check publication freshness again on download; a post-commit read failure retries
the read, not generation.
Keep audit events content-free: pseudonymous identity, operation, state/code,
reference, timings and byte/page/quota counts only. No request or result content,
credential, cookie or raw error enters ordinary logs.

The in-test schema checker and fake gateway/clock/storage verify contract examples,
closed fields, money, replay and transition rules without importing the app.
They do not prove HTTP admission, durable recovery, credentials, tenancy, artifact
privacy/rendering, retention execution or installed behaviour. Those belong to later
implementation and independent review. This slice authorises no implementation,
public exposure, provider calls, database, deployment, credentials or UI changes.
