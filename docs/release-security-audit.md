# SQAG Release Security Audit

Audit date: 2026-07-02

Branch: `codex/release-security-audit`

Base evidence ref: `origin/main` at `29de611ee1723ae1e9d1755c32b013efdbc4511e`

## 2026-07-11 Object Lifecycle And Delete Integrity Addendum

Remediation base: `origin/main` at
`ddc973aad5b1bb680624c0543ff616994471c44e`.

Production readiness remains false. This focused remediation closes one
independently confirmed High launch blocker without live credentials, live
provider calls, customer data, deployment, migrations, or visible product UI
changes.

| Severity | Confirmed blocker | Closure |
| --- | --- | --- |
| High | Object-provider deletion errors were swallowed while quote-session metadata was tombstoned and the session delete returned success. Profile and pricing-reference deletes removed only owner rows, leaving active artifact metadata and bytes; recreating a profile with the same ID could therefore inherit its deleted layout. Content-addressed replacements also overwrote the metadata pointer without deleting the superseded object, and successful replacements retained artifact kinds omitted from the new pricing/quote payload. | Object-backed owner deletion now deletes each remote object first, tombstones that exact metadata row only after confirmed provider success, and stops with a generic HTTP 503 while preserving the owner and failed artifact on any provider/configuration error. Database-backed profile, pricing, and quote artifacts are deleted with their owner records. Profile, pricing, and generated-quote replacements share a guarded path that removes superseded and omitted objects and restores the prior same-kind object if the replacement write or metadata update fails. |

Successful partial object deletion is recorded one artifact at a time. If a
later artifact fails, the owner remains and a retry processes only the still
active artifacts; already confirmed deletions are not falsely reactivated.
Object-mode Postgres deletion never queries the SQLite-only BLOB artifact
tables. Quote replacement reconciliation starts only after a valid XLSX is
available to persist, so a routine `needs_confirmation` result keeps prior
bytes but marks the prior export stale and unavailable.
Delete/replacement failures do not log provider values, object keys, artifact
bytes, or customer content.

Focused validation evidence on the remediation branch:

- The five-test pre-fix lifecycle set failed all five vulnerable assertions:
  quote deletion did not fail closed, profile/pricing artifacts survived owner
  deletion, same-ID layouts rebound, superseded objects remained, and
  database-backed artifacts survived owner deletion.
- Four independent follow-up regressions then failed all four omitted-kind and
  object/Postgres assertions before the final repair. A fifth follow-up RED
  regression proved that `needs_confirmation` without an XLSX could otherwise
  delete the last customer-ready quote.
- The final focused lifecycle matrix passes: 12 tests. An independent reviewer
  separately reran the five follow-up edge cases and found no remaining
  confirmed High/Critical issue in scope.
- The adjacent database/object lifecycle and workspace-isolation set passes:
  24 tests.
- The complete `tests.test_webapp` module passes: 495 tests.
- The complete Python suite passes: 734 tests. Initial runs encountered
  Windows-only 10-second worker/HTTP timing failures while synthetic workbook
  preparation was slow; the named worker test passed unchanged in isolation,
  and the final fail-fast full-suite runner completed all 734 tests with `OK`.
- Python syntax compilation, the internal-UAT deploy-template verifier, the
  sensitive-fixture fail-on-review scan, dynamic-pricing guard, local-PDF
  dependency guard, `pip-audit`, and `npm audit` pass.
- A fresh local backend with isolated synthetic runtime roots returned HTTP 200
  and `status=ok` from `/api/health`. The mocked Playwright AI stress and
  full smoke suites pass with no console or network problems.
- The production-readiness checker was run with local/provider-disabled values,
  contacted no live service, and remained intentionally blocked with
  `internal_alpha_ready=false`, `production_ready=false`, and eight blockers.

Hosted/live evidence remains a separate gate. These local SQLite and synthetic
in-memory object checks do not claim live provider retention/delete evidence,
and `production_ready=false` remains unchanged.

## 2026-07-11 Coolify Proxy And Bounded Rate-Limit State Addendum

Remediation base: `origin/main` at
`ae6738263292507da6113301793848ef0ad3c274`.

Production readiness remains false. This focused remediation closes two
independently confirmed High launch blockers without live credentials, live
provider calls, customer data, deployment, or visible product UI changes.

| Severity | Confirmed blocker | Closure |
| --- | --- | --- |
| High | Behind Coolify/Traefik, Platform launch rate limiting used only the direct socket peer and charged the bucket before launch-token validation. Twenty cheap missing-token requests could consume the shared proxy bucket and make a legitimate launch return HTTP 429 before Platform consume was called. | Deploy mode now requires an explicit trusted-proxy CIDR boundary. Forwarding data is accepted only from a trusted direct peer, parsed as valid unscoped IP addresses, bounded by raw size and hop count, and resolved right-to-left to the first untrusted hop. Missing, malformed, duplicate, oversized, over-hop, scoped, or untrusted forwarding data falls back to the socket peer. The same effective client identity is used for Platform launch and normal mutable-route rate limiting, while same-client limits remain enforced. |
| High | The process-global normal rate-limit dictionary retained every unique client/route key indefinitely; only timestamps for the currently accessed key were filtered. Rotating proxied IPv4 or IPv6 identities could therefore grow process memory without a cardinality bound. | Each process now caps ordinary client/normalized-route buckets at 4,096. Global stale pruning runs at most once every 15 seconds under the existing lock. At capacity, unseen identities share one fixed-window overflow bucket per normalized configured route; overflow is capped at the 14 configured routes and fails closed rather than evicting active ordinary clients or disabling throttling. |

Deploy preflight, normal deploy startup, and handler-level deploy paths fail
closed when `SQAG_TRUSTED_PROXY_CIDRS` is missing or malformed. Catch-all
networks and empty CIDR entries are rejected. Deployment examples use a
placeholder only; operators must configure the exact direct Coolify/Traefik
proxy networks in the host environment. Raw forwarding headers and client IP
values are not added to logs.

Focused validation evidence on the remediation branch:

- The original three-test pre-fix regression reproduced the blocker with two
  failures and one unaffected direct-client spoof control; the same three tests
  pass after the repair.
- Expanded RED coverage reproduced trusted-proxy isolation and configuration
  failures, including duplicate forwarding fields and scoped IPv6 forms.
- The bounded-state RED set produced one stale-cleanup assertion failure and
  four missing-cap/overflow errors across seven tests while two preserved
  behavior controls passed. The tightened mutable-route bounded-state test also
  failed before the implementation existed.
- Right-to-left spoof-chain resolution, malformed and bounded-header fallback,
  direct-client spoof denial, same-forwarded-client throttling, and mutable-route
  isolation controls pass.
- The combined bounded-state and trusted-proxy adversarial set passes: 17 tests.
- The complete `tests.test_webapp` module passes: 484 tests.
- The complete Python suite passes: 723 tests.
- Python syntax compilation, the internal-UAT deploy-template verifier, the
  sensitive-fixture fail-on-review scan, dynamic-pricing guard, local-PDF
  dependency guard, `npm audit`, and `pip-audit` pass.
- A fresh local backend restart returned HTTP 200 from `/api/health`; the mocked
  Playwright AI stress and full smoke suites pass with no console or network
  problems. The smoke harness now waits for dashboard refresh completion before
  changing its local draft identity, removing a reproducible test-only race.
- The production-readiness checker remains intentionally blocked with
  `internal_alpha_ready=false`, `production_ready=false`, eight blockers, and
  its blocked exit code.

Hosted/live evidence remains a separate gate. These local and synthetic checks
do not claim live Platform, proxy-network, TLS, database, object-provider,
monitoring, backup/restore, retention/delete, or deployment evidence.

## 2026-07-10 Independent Launch-Blocker Remediation Addendum

Remediation base: `origin/main` at
`7a6de268a0a6fe84fcb052d4d757f6b4a2704443`.

Production readiness remains false. This focused remediation closes three
independently confirmed High launch blockers without using live credentials,
calling live providers, applying migrations, or changing the visible product
UI.

| Severity | Confirmed blocker | Closure |
| --- | --- | --- |
| High | Deploy startup accepted standalone OIDC authentication even though every hosted database and object-storage operation requires a trusted Swooshz Platform workspace. Login could succeed, but all workspace-backed workflows then failed closed at runtime. | Deploy startup and deploy preflight now require complete Platform launch configuration. Standalone OIDC remains available only for local component testing; OIDC claims are not promoted into Platform workspace authority. |
| High | After switching startup to Platform-only, an unexpired signed OIDC session cookie from before the upgrade could still be accepted as authenticated and inherit the configured deploy tester role. It could reach unpermissioned and AI import-preview routes without Platform workspace or entitlement provenance. | Deploy sessions now require complete signed Platform provenance: consumed outcome, Platform user, workspace, SQAG app key, and supported membership role. Legacy or malformed cookies are treated as unauthenticated and receive blocked permissions; Platform mode rejects the OIDC callback before provider calls, while logout still clears cookies and returns to the validated Platform URL. |
| High | Startup and `/api/health` could report success without proving the configured database schema, object-artifact metadata schema, or object-storage bucket was reachable. Coolify could route traffic to an operationally unusable replica. | Deploy startup now performs fresh, read-only dependency probes before binding. Readiness checks cover both database schemas and a read-only object bucket probe; `/api/health` returns HTTP 503 when blocked and uses a short, lock-protected cache for runtime probes. No startup migration or object write is performed. |

Focused validation evidence on the remediation branch:

- The pre-fix regression set reproduced the defects with two assertion failures
  and six errors across eight focused tests.
- The same eight focused tests pass after the repair.
- A second five-test provenance set reproduced the legacy-cookie path with four
  failures and one unaffected local-mode control before the repair; all five
  pass afterward.
- Independent post-fix adversarial checks reject legacy and malformed Platform
  cookies on session, validation, logging, and AI import-preview routes while
  preserving valid Platform sessions and session-bound CSRF.
- Twenty-two adjacent deploy, OIDC, Platform, and storage tests pass.
- The complete `tests.test_webapp` module passes: 467 tests.
- The complete Python suite passes: 706 tests.
- Python syntax compilation, the dynamic-pricing source guard, the
  internal-UAT deploy-template verifier, the sensitive-fixture scan, and the
  architecture-fallback audit pass.
- Synthetic hosted observability and hosted smoke verifiers pass with all 11
  smoke checks true; both retain `synthetic_only=true` and
  `production_ready=false`.
- The production-readiness checker remains intentionally blocked with
  `internal_alpha_ready=false`, `production_ready=false`, and eight remaining
  hosted/live evidence blockers.

Hosted/live evidence remains a separate gate. The synthetic readiness probes
and tests do not claim live Swooshz Platform, database, object-provider,
Coolify, TLS/proxy, monitoring, backup/restore, retention/delete, or graceful
shutdown evidence.

## Executive Verdict

Swooshz Quote Auto Generator (SQAG) remains suitable for local UAT by default. The readiness checker now
keeps hosted/protected/deploy and production readiness blocked when generated
artifact bytes are stored in local runtime paths or database BLOB rows, even if
synthetic backup/restore, hosted observability, and hosted smoke evidence pass.

| Gate | Verdict | Reason |
| --- | --- | --- |
| `local_uat_supported` | Yes | Local localhost mode, local runtime storage, seeded test setup, and current CI remain supported. |
| `internal_alpha_ready` | No | Kept as a legacy compatibility field but always false; neither local runtime, DB/BLOB artifact mode, nor synthetic object evidence grants launch-readiness credit. |
| `production_ready` | No | Immutable quote-session snapshot groundwork, stale/deleted artifact route hardening, deterministic delete/export/download race evidence, synthetic/stubbed object lifecycle evidence, live provider evidence, live DB+object backup/restore evidence, an opt-in live retention/delete verifier path, and a source-level Platform integration contract audit exist, but live retention/delete pass evidence, live Platform integration evidence, hosted logging/monitoring and alert delivery, hosted smoke evidence, production deployment/operations evidence, final session/business hardening, and final production audit are not complete. |

This update does not claim production readiness. It removes the old DB-artifact
hosted launch posture from readiness credit:
workspace-owned database rows remain the SQAG app-data path, while generated
XLSX/PDF bytes require object storage before hosted/protected/deploy or
production readiness can be claimed. It does not deploy anything live, add
secrets, or prove production operations.

Canonical naming update: the repository has been renamed to
`Swooshz-com/swooshz-quote-auto-generator`, and SQAG is the canonical
operator-facing product/runtime name for current object-storage provider and
live-evidence configuration. The live object-storage path now requires
`SQAG_*` object-storage env names; legacy `SQAG_*` object-storage names are not
aliases and do not silently satisfy live-provider evidence. Existing
database/table names and non-object storage compatibility variables remain out
of scope for this cleanup.

Highest-priority remaining blockers:

- Medium: database artifact/BLOB storage is local-UAT/synthetic evidence only
  and cannot satisfy hosted/protected/deploy or production readiness; generated
  XLSX/PDF bytes require object storage plus the remaining live retention/delete
  and operations evidence.
- Medium: live hosted deployment/operations evidence is still missing; the new
  scaffold is a runbook and synthetic validation path only.
- Medium: live Swooshz Platform integration evidence is still missing; the
  source-level contract has been audited against current Platform main, but no
  live Platform-to-SQAG deployment smoke is claimed.

Load Sample status: product-visible Load Sample UI/API/JS paths are gone after PR #86. No Load Sample button, product API, or Playwright smoke dependency is part of the sellable path. Remaining sample/Kent references are test-only or historical audit references.

PR #88 pricing-reference isolation update: database/platform pricing-reference
list/detail/export/generation now resolve only workspace-owned database rows.
Local and bundled pricing packs remain local-UAT-only behavior, not a hosted,
protected, deploy, or production fallback.

PR #89 profile/layout isolation update: database/platform quote generation now
requires the selected workspace-owned DB profile row and stored DB layout
artifact. Missing/deleted profiles or missing layout artifacts block generation
instead of falling back to bundled/default/local profile packs. The release gate
remained closed because legacy job downloads, AI draft fallback, object storage,
and backup/restore blockers were still unresolved at that point; AI draft
fallback was resolved by PR #92.

Tenancy data correction: Koncept Images Pte Ltd profile, pricing, and layout
packs are tenant/workspace-imported data, not app defaults, bundled defaults,
global seeds, or fallback data for every organization. A new workspace starts
without any real Koncept pack. In protected/database/platform/deploy modes,
profile/pricing/layout reads must use only workspace-owned persisted records.
If a workspace has not explicitly imported or seeded a pack, settings surfaces
must show an empty state and generation must block/fail closed instead of
reading local-drive, bundled, or synthetic fixture packs.

PR #91 legacy job artifact lockdown update: hosted/database/platform/deploy
mode disables direct `/api/jobs/{job}/files/{filename}` downloads from the
legacy output root, and `/api/jobs/{job}` status/result responses are bound to
the creating platform user/workspace. Local-UAT local storage mode keeps the
legacy direct download route for localhost workflows. The release gate remains
closed because object storage, hosted smoke evidence, and other non-AI
production blockers are still unresolved; backup/restore and hosted
observability now have synthetic evidence paths only.

AI draft fallback policy update: protected modes now block AI draft generation
when the real OpenAI draft path is missing, unconfigured, unavailable, or
returns unusable output. Local-UAT/local-development mode still keeps the
intentional local starter draft fallback for localhost testing only.

Quote-session local runtime storage policy update: protected modes now block
quote-session list, detail, save, delete, download, and generate-session
persistence when workspace-owned database quote-session storage is unavailable.
Local-UAT/local-development mode still keeps the local filesystem quote-session
runtime store for localhost dashboard testing only.

Immutable quote-session snapshot update: generated quote sessions now store a
privacy-minimized `generation_snapshot` in quote-session metadata. The snapshot
contains the selected workspace profile/pricing ids, display labels, safe
digests of those summary fields, workspace scope, generation timestamp, and
storage mode metadata. It intentionally does not add raw pricing catalog rows,
generated quote contents, uploaded file contents, artifact bytes, object keys,
local paths, DB URLs, secrets, customer payloads, staff emails, OAuth values,
cookies, or tokens. Existing sessions without snapshots remain readable. Draft
edits can update current session state, but the generated snapshot remains tied
to the generated artifact/audit metadata. Workspace isolation is unchanged:
database/platform modes read snapshots only through workspace-owned
`sqag_quote_sessions` rows, and missing/deleted current profile/pricing rows do
not fall back to local, bundled, or synthetic fixture packs for generated-session
display. This is groundwork only; production still needs final session/business
hardening, live object-storage evidence, and deployment/operations evidence.

Session artifact race hardening update: generated artifact downloads now
revalidate current workspace-owned database/object metadata before returning
bytes. Database artifact route tests cover successful download before deletion,
quote-session deletion making session/artifact downloads inaccessible, stale
draft edits removing download URLs, generated snapshot labels remaining tied to
the original generated output, and legacy direct job-file downloads staying
locked in protected/database/platform/deploy modes. Object-mode tests use only
the stubbed in-memory backend and cover active owner downloads, tombstoned or
deleted metadata denial, missing/corrupt remote object denial, wrong-workspace
denial through existing metadata checks, and deterministic mid-retrieve
tombstone races. This route/race evidence is synthetic/local test evidence only;
it is not live provider retention/delete or production operations evidence, and
live DB+object backup/restore evidence is tracked separately.

Local artifact storage policy update: protected generate paths now block before
creating or returning local `QUOTE_OUTPUT_ROOT` quote artifacts when database
artifact storage is not enabled. Protected profile layout uploads and pricing
visual uploads also block before local filesystem artifact writes. Database
artifact mode remains local-UAT/synthetic evidence only. It does not satisfy
hosted/protected/deploy readiness and is not final production object storage.

Database backup/restore evidence update: `scripts/verify_database_backup_restore.py`
now performs a synthetic SQLite drill for database rows plus database-artifact
BLOB rows. It applies the reviewed migrations,
seeds synthetic workspace/profile/pricing/session/artifact rows, backs up and
restores database rows plus BLOB artifacts together, compares row-count and
checksum metadata, verifies workspace/session ownership metadata survives, and
proves rollback to a prior known-good synthetic state. The output is
metadata-only and omits DB URLs, local paths, artifact bytes, generated quote
contents, customer details, pricing/profile payloads, staff emails, OAuth
values, cookies, tokens, and API keys. This is not hosted launch readiness,
production object storage, external hosted logging wiring, or hosted smoke
evidence.

Hosted observability evidence update: `scripts/verify_hosted_observability.py`
now performs a synthetic structured-logging and health/readiness drill. It
checks event allowlisting, metadata-only log records, support-traceable error
references, sensitive-value omission, the machine-readable policy in
`docs/hosted-observability-policy.json`, and path-free health metadata. The
output is metadata-only and omits DB URLs, local paths, artifact bytes,
generated quote contents, customer details, pricing/profile payloads, staff
emails, OAuth values, cookies, tokens, API keys, and raw provider responses.
This is synthetic metadata-only evidence; external vendor wiring, alert
delivery, hosted smoke checks, object storage, and production readiness remain
separate.

Hosted smoke evidence update: `scripts/verify_hosted_smoke.py` now performs a
synthetic hosted-like smoke drill on `127.0.0.1` only. It runs
deploy mode with database storage, database artifact storage, a synthetic SQLite
database, and a synthetic platform/workspace session. It verifies path-free
health metadata, unauthenticated route blocking, synthetic platform launch,
workspace-owned profile/pricing save-and-use, quote generation, quote-session
persistence, authorized XLSX/PDF quote-session artifact downloads, delete,
logout redirect behavior, and legacy direct job-file lockdown. The output is
metadata-only and omits DB URLs, paths, generated quote contents, customer
details, pricing/profile payloads, artifact bytes, staff emails, OAuth values,
cookies, tokens, API keys, callback query values, and provider responses. This
does not call Swooshz Platform, prove a live hosted deployment, add object
storage, satisfy hosted/protected/deploy readiness, or claim production
readiness.

Blocked hosted validation scaffold update:
`docs/internal-uat-coolify-deploy.md`,
`deploy/internal-uat/coolify/sqag.uat.env.example`, and
`scripts/verify_internal_alpha_hosted_validation.py` now document and validate
the metadata-only negative check for the old DB/BLOB artifact hosted posture.
The scaffold lists env names and placeholders only, requires workspace-owned
database app records, requires generated XLSX/PDF bytes to use object storage
for launch readiness, and requires host-secret-manager handling for the DB URL
and session/OIDC/platform values. The validation bundle composes synthetic
backup/restore, hosted observability, hosted smoke, and readiness-checker
evidence and verifies `database_blob_artifact_storage_not_launch_ready` remains
present for DB/BLOB artifact mode. It does not prove a live VPS/Coolify
deployment, live Platform integration, real OIDC, real object storage, alert
delivery, production operations, hosted launch readiness, or production
readiness.

Platform integration contract audit update:
`docs/platform-integration-contract.md` records the current SQAG-side
expectations for Swooshz Platform launch, auth, workspace, role, app-gating,
and tenant isolation. The audit cites SQAG functions in `webapp/server.py`, the
existing SQAG platform/storage regression coverage, and Swooshz Platform
`origin/main` at `5bce4d52e4273762375d97149b1d77e5716189b2`, including the
Platform SQAG integration, app-access, auth/session security, route-contract,
and launch-token consume surfaces. This is source-contract evidence only. It
does not change runtime behavior, prove live Platform deployment behavior, add
secrets, or claim production readiness.

Object-storage contract evidence update: PR #98 added `webapp/object_storage.py`,
a provider-neutral artifact backend contract for generated quote XLSX/PDF
artifacts, uploaded references, profile layout assets, and pricing visual
assets. The contract requires workspace-scoped owner metadata, content type,
byte size, SHA-256 checksum, timestamps, retrieve/delete operations, and
workspace authorization checks. `scripts/verify_object_storage_contract.py`
exercises this contract with a synthetic in-memory backend only; it does not
configure AWS, GCP, Azure, R2, MinIO, S3-compatible endpoints, or credentials.
When `SQAG_ARTIFACT_STORAGE_MODE=object` is selected at runtime, SQAG fails
closed with the generic artifact-storage-unavailable message until a usable
provider backend is available.

Object-storage provider configuration update: PR #99 added strict
metadata-only configuration validation for the production object-storage
provider boundary. The recognized object provider setting is
`SQAG_OBJECT_STORAGE_PROVIDER`; unset, `disabled`, `none`, `off`, `false`, or
`0` mean disabled. `s3_compatible` is the credentialed provider family for
AWS S3, Cloudflare R2, MinIO, or similar S3-compatible APIs and requires these
environment names to be present:
`SQAG_OBJECT_STORAGE_ENDPOINT_URL`, `SQAG_OBJECT_STORAGE_BUCKET`,
`SQAG_OBJECT_STORAGE_REGION`, `SQAG_OBJECT_STORAGE_ACCESS_KEY_ID`, and
`SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY`. The checker reports only provider
type, required field names, missing field names, adapter status, and
runtime availability booleans; it never prints endpoint URLs, bucket values,
access keys, secret keys, object keys, DB URLs, paths, artifact bytes, quote
contents, or customer data. A `synthetic` provider is accepted only as
test/verifier metadata and is never production-credited.

Object-storage runtime integration update: this PR begins the real runtime
boundary for generated quote XLSX/PDF artifacts. It adds a credentialed
S3-compatible adapter implementation that supports store, retrieve, delete,
workspace/owner metadata checks, and SHA-256 integrity validation. Unit tests
use a fake S3-compatible client only; no live AWS/R2/MinIO account, endpoint,
bucket, or credential is configured or claimed. The database migration
`migrations/003_object_artifact_metadata.sql` adds generated object-artifact
metadata rows with opaque artifact IDs, workspace/session/owner linkage,
content type, byte size, checksum, provider type, internal object key reference,
status, retention status, and tombstone timestamp fields. Object mode stores
generated artifact bytes in the object backend and DB metadata in
`sqag_object_artifacts`; it does not store raw artifact bytes in database rows
and does not expose object keys in public API responses. Authorized downloads
continue to use `/api/quote-sessions/{id}/download/{kind}`. Production still
requires live provider evidence, DB+object backup/restore evidence,
retention/delete evidence against the real object backend, production
deployment/operations evidence, live Swooshz Platform integration audit,
observability export/alert delivery, supply-chain hardening, and final audit
before readiness can be claimed.

Object artifact lifecycle update: object-mode quote-session deletion now
tombstones generated object artifact metadata and attempts object-backend
deletion through the configured backend without exposing object keys. Deleted,
tombstoned, missing, wrong-workspace, or integrity-mismatched object artifacts
remain inaccessible through quote-session download routes and never fall back to
local output files or database BLOB artifact rows. If object storage fails after
the generator has produced a local staging file, the response is a generic
storage failure and local `/api/jobs/.../files/...` links are removed rather
than returned as a fallback. After a successful object store, generated
XLSX/PDF staging files under the immediate generator output directory are
removed so they are not treated as durable local runtime storage.
`scripts/verify_object_artifact_lifecycle.py` adds a synthetic SQLite plus
stubbed in-memory object backend drill for DB metadata backup/restore,
restored-object retrieval, missing-object detection, checksum mismatch
detection, tombstone behavior after restore, wrong-workspace denial, and local
staging cleanup. The verifier output is metadata-only and omits paths, DB URLs,
object keys, artifact bytes, quote contents, customer data, pricing/profile
payloads, staff emails, OAuth values, cookies, tokens, API keys, and provider
responses. This is not live provider retention/delete evidence or real
DB+object backup/restore evidence.

Live object-storage provider evidence update: PR #108 added
`scripts/verify_live_object_storage_provider.py` and a readiness-checker flag,
`--with-live-object-storage-provider-evidence`. The verifier is explicit
opt-in through `SQAG_LIVE_OBJECT_STORAGE_EVIDENCE` plus complete
S3-compatible provider configuration env names. PR #108 also pins the
repo-controlled S3 SDK dependency set in `requirements.txt` (`boto3`,
`botocore`, and compatible transitive packages) so the live provider path does
not rely on undeclared host-level manual installs. It stores, retrieves, checks,
and deletes safe synthetic XLSX/PDF bytes; it checks checksum, content type,
byte size, wrong-workspace denial, tombstone/delete behavior, and
missing-object fail-closed behavior. Output is metadata-only and reports only
provider family, status booleans, required/missing env names, and check
results. It must not print endpoint values, bucket values, object keys,
credentials, DB URLs, private paths, generated artifact bytes, customer data,
uploaded content, pricing/profile payloads, cookies, tokens, or provider
responses. No live provider values or live evidence are committed. No live
provider evidence is claimed unless an operator runs it with real provider
configuration supplied outside Git; test-injected or synthetic backends are
never credited as live provider evidence.

The old `SQAG_*` object-storage provider env names are intentionally not
accepted by this live-evidence path. Operators must use the canonical `SQAG_*`
object-storage names for current SQAG provider evidence.

Postgres metadata storage adapter update: SQAG now has a real
Postgres/Neon-compatible runtime adapter boundary for workspace-scoped
profiles, pricing references, quote sessions, and object-artifact metadata.
The adapter uses a repo-pinned modern Postgres driver, performs dialect-aware
schema checks, binds workspace IDs in metadata queries, and fails closed for
unsupported URL schemes, missing drivers, connection failures, missing schema,
missing workspace context, and Postgres DB-BLOB artifact mode. It does not
store generated XLSX/PDF bytes in the database, does not fall back to SQLite,
local files, bundled/sample data, DB BLOBs, or object-storage substitutes, and
does not print DB URLs, hostnames, usernames, passwords, object keys, artifact
bytes, or tenant data. `scripts/verify_production_database_provider.py` remains
metadata-only by default; it performs live schema and synthetic metadata CRUD
checks only when operators explicitly enable `SQAG_LIVE_DATABASE_EVIDENCE`.
A sanitized operator run on 2026-07-07 applied the existing guarded SQAG
metadata migrations after an initial schema-missing result, then passed with
`status=passed`, `database_family=postgres_compatible`,
`live_database_evidence_enabled=true`, `test_injected_backend=false`,
`live_database_evidence_supported=true`,
`production_database_evidence_supported=true`, `connection_attempted=true`,
runtime schema available, synthetic CRUD verified, two-workspace isolation
verified, object artifact metadata pairing verified, cleanup completed, and
`db_blob_artifact_rows_written=0`. The verifier did not touch R2/object storage,
did not rerun live object-storage evidence, and did not commit or print DB URLs,
hostnames, usernames, passwords, connection strings, provider values, object
keys, private paths, tenant data, generated quote contents, or artifact bytes.
`production_ready=false` remains.

## Threat Model

Protected assets:

- Workspace profile defaults, layout rules, profile layout workbook, logos, and company profile export JSON.
- Workspace pricing references, pricing catalog rows, visual references, match terms, and imported source metadata.
- Quote sessions, draft state, uploaded booth/render images, generated XLSX/PDF artifacts, and pricing review output.
- Auth/session cookies, platform launch tokens, OIDC state/code exchange data, CSRF token, provider API keys, database URL, and local runtime roots.
- CI credentials and repository integrity.

Primary actors:

- Local UAT operator running trusted localhost flows.
- Approved internal tester launched from the future platform workspace.
- Cross-workspace internal user trying to see or use another workspace's profile, pricing, session, or artifact data.
- Network attacker or malicious site attempting CSRF, host-header, same-origin, or callback abuse.
- Malicious or malformed upload provider supplying XLSX/CSV/MD/PDF/image data.
- Repository contributor accidentally committing private data, fixture data, generated quotes, or secrets.

Security boundaries reviewed:

- HTTP route authentication, permission, CSRF, same-origin, and host guards.
- Workspace ID and owner checks in database storage.
- Local runtime folders versus workspace-scoped database rows and artifact BLOB tables.
- Pricing/profile import validation and spreadsheet formula hardening.
- Artifact generation, direct download, quote-session download, and temp/output roots.
- Logs, errors, scanner output, docs, tests, fixtures, dependency install scripts, and CI.

## Evidence Sources

Manual review focused on:

- `webapp/server.py`: route handler, auth/session helpers, platform launch flow, storage classes, import/upload parsing, generation path, job/artifact downloads, logs/errors.
- `webapp/static/app.js` and `webapp/static/index.html`: product-visible Load Sample absence, upload/generate/session flows.
- `scripts/generate_quote.py`: XLSX/PDF generation and formula handling.
- `scripts/audit_architecture_fallbacks.py`, `scripts/check_production_readiness.py`, `scripts/scan_sensitive_fixtures.py`.
- `tests/test_webapp.py`, `tests/test_production_readiness.py`, `tests/test_architecture_fallback_audit.py`, `tests/test_generate_quote.py`.
- `.github/workflows/ci.yml`, `package.json`, `package-lock.json`, `requirements.txt`.
- Prior audits: `docs/production-readiness-audit.md` and `docs/architecture-dead-code-fallback-audit.md`.

Safe metadata-only scanner snapshot from:

```powershell
python scripts/audit_architecture_fallbacks.py --max-hits-per-pattern 1 --max-possible-unused 5
```

The scanner reports only relative paths, line numbers, pattern names, categories, and counts. It does not echo source lines, secrets, absolute private paths, runtime data, generated quote contents, or private pricing/profile contents.

| Category | Hits |
| --- | ---: |
| `sample_or_bundled_data` | 2534 |
| `exception_or_default_boundary` | 1400 |
| `local_storage_dependency` | 872 |
| `fallback_path` | 299 |
| `pricing_reference_boundary` | 267 |
| `legacy_or_compatibility` | 90 |
| `storage_mode_boundary` | 56 |
| `artifact_download_boundary` | 53 |
| `load_sample_surface` | 32 |
| `profile_boundary` | 24 |
| `broad_exception_boundary` | 20 |

These counts are intentionally noisy. They guided manual review; they are not standalone findings.

## Route/API Security Matrix

Global route controls:

- `QuoteRunnerHandler.do_GET`, `do_POST`, and `do_DELETE` call `block_untrusted_host()` before route dispatch (`webapp/server.py:13385`, `13608`, `13789`).
- POST and DELETE use `block_unsafe_post()` for same-origin, optional Referer, CSRF header, and rate-limit checks (`webapp/server.py:13617`, `13795`, `14072`).
- Deploy authentication is enforced through `block_unauthenticated_request()` unless the route is static, `/privacy`, or `/api/health` (`webapp/server.py:13944`).
- Deploy mode fails closed if auth is required but OIDC/platform launch is incomplete (`webapp/server.py:13949`, `14319`).

| Route | Method | Auth | Workspace | Session/owner | Role/permission | CSRF | Sensitivity | Audit verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | GET | Required in deploy | Platform session indirectly | N/A | Any authenticated user | N/A | App shell | Acceptable route gate. |
| `/privacy` | GET | Public | N/A | N/A | None | N/A | Public legal page | Acceptable. |
| `/static/*` | GET | Public | N/A | N/A | None | N/A | Static assets | Path containment check exists at `webapp/server.py:14194`. |
| `/api/health` | GET | Public | N/A | N/A | None | N/A | Low, metadata-only health signal | Path-free health payload now reports status and boolean checks without exposing generator/runtime paths. |
| `/api/session` | GET | Required in deploy | Platform session returned when present | Current session | Any authenticated user | N/A | Auth context, CSRF token | Acceptable for app bootstrap; keep payload privacy-minimized. |
| `/login` | GET | Public entry | Platform mode blocks local OIDC login | N/A | Approved tester through OIDC or platform | N/A | Auth boundary | Acceptable fail-closed direction. |
| `/callback` | GET | Public callback | No workspace unless OIDC mode | State cookie | OIDC allowlist | N/A | OIDC code/state | State check exists; logs redact callback query values. |
| `/logout`, `/signed-out` | GET | Session optional | N/A | Current cookies | Any | N/A | Session termination | Acceptable. |
| `/api/platform/launch` | POST | Platform token | Required in launch context | Platform user and workspace become signed session | Platform membership role mapped to local role | Exempt from CSRF by design, rate-limited | Launch token/session boundary | Acceptable shape, but not verified against live Platform repo. |
| `/api/profiles` | GET | Required in deploy | Uses current storage workspace | N/A | Any authenticated user | N/A | Profile and pricing summaries | Pricing list is workspace-strict in DB mode after PR #88; profile list is workspace-strict in DB mode after PR #89. |
| `/api/settings` | GET | Required in deploy | Uses current storage workspace | N/A | `canManageSettings` | N/A | Settings, profiles, pricing refs | Role-gated; DB pricing/profile lists are workspace-strict after PR #88/#89. |
| `/api/settings/pricing-references` | GET | Required in deploy | DB rows keyed by workspace | N/A | `canManagePricingReferences` | N/A | Pricing metadata | PR #88 removes local/bundled fallback in DB mode. |
| `/api/settings/pricing-references/{id}` | GET | Required in deploy | DB rows keyed by workspace | N/A | `canManagePricingReferences` | N/A | Pricing rows/catalog | PR #88 returns not found for local/bundled/missing DB references. |
| `/api/settings/pricing-references/{id}/export.xlsx` | GET | Required in deploy | DB rows keyed by workspace | N/A | `canManagePricingReferences` | N/A | Pricing export workbook | PR #88 exports only workspace-owned DB references. |
| `/api/pricing-reference/template.xlsx` | GET | Required in deploy | N/A | N/A | Any authenticated user | N/A | Blank template | Acceptable. |
| `/api/pricing-reference/validate` | POST | Required in deploy | N/A | N/A | Any authenticated user | Yes | Uploaded pricing file | Medium: validation exists, but import surface should be retested with hostile workbooks before production. |
| `/api/settings/pricing-references/import-preview` | POST | Required in deploy | N/A until save | N/A | `canImportPricingReferences` | Yes | Uploaded pricing source and AI normalization | Medium: parser limits exist; AI import must remain privacy-minimized. |
| `/api/settings/pricing-references` | POST | Required in deploy | Saves DB rows by workspace in DB mode | N/A | `canManagePricingReferences` | Yes | Pricing rows/catalog | Save path is workspace-scoped after PR #88, including unchanged responses. |
| `/api/settings/pricing-references/{id}` | DELETE | Required in deploy | Deletes DB row by workspace in DB mode | N/A | `canManagePricingReferences` | Yes | Pricing reference deletion | DB delete is workspace-scoped; local mode remains local-UAT only. |
| `/api/settings/profiles` | GET | Required in deploy | DB profiles keyed by workspace | N/A | `canManageProfiles` | N/A | Profile metadata | PR #89 removes bundled/default/local profile mixing in DB mode. |
| `/api/settings/profiles/{id}/export.json` | GET | Required in deploy | DB row export in DB mode; local helper can export pack assets in local mode | N/A | `canManageProfiles` | N/A | Profile defaults/layout assets | Medium/high depending mode: profile asset export needs object-storage ownership before hosted/protected/deploy use. |
| `/api/settings/profiles` | POST | Required in deploy | Saves DB row by workspace in DB mode | N/A | `canManageProfiles` | Yes | Profile defaults/layout assets | Save is scoped; DB generation consumes stored profile layout artifacts after PR #89. |
| `/api/settings/profiles/{id}` | DELETE | Required in deploy | Deletes DB row by workspace in DB mode | N/A | `canManageProfiles` | Yes | Profile deletion | DB delete is workspace-scoped; confirm artifact cleanup in follow-up. |
| `/api/quote-sessions` | GET | Required in deploy | DB rows keyed by workspace | Owner visibility in DB mode | Any authenticated user | N/A | Quote session summaries | Protected modes block local runtime quote-session listing when database storage is unavailable; local mode remains local-UAT only. |
| `/api/quote-sessions/{id}` | GET | Required in deploy | DB row keyed by workspace | Owner/admin visibility in DB mode | Any authenticated user | N/A | Draft state, filenames, session metadata | Protected modes block local runtime quote-session detail reads when database storage is unavailable; local mode remains local-UAT only. |
| `/api/quote-sessions/{id}` | DELETE | Required in deploy | DB row keyed by workspace | Owner editable in DB mode | `canGenerateQuote` | Yes | Session deletion | Protected modes block local runtime quote-session deletion when database storage is unavailable; DB artifact downloads become inaccessible after session delete, and object metadata is tombstoned in object mode. Live provider retention/delete still needs follow-up. |
| `/api/quote-sessions/{id}/download/{kind}` | GET | Required in deploy | DB/object artifact keyed by workspace/session in DB artifact or object mode | Owner/admin visibility through metadata lookup | Any authenticated user | N/A | Generated XLSX/PDF | Protected modes block local runtime quote-session downloads when database storage is unavailable. Stale/deleted/missing/corrupt DB/object artifact states fail closed without local or DB-BLOB fallback in object mode. Production still needs live object storage and retention evidence. |
| `/api/jobs` | POST | Required in deploy | Auth session passed to worker | Job owner context stored for protected modes | Any authenticated user; generation checks later | Yes | Async draft/generate jobs | PR #91 stores privacy-safe owner/workspace context for hosted job visibility. |
| `/api/jobs/{job}` | GET | Required in deploy | Owner workspace in protected modes | Creating platform user/workspace in protected modes | Any authenticated user | N/A | Job status/result/files | PR #91 blocks cross-user/cross-workspace job status/result reads in hosted/database/platform/deploy mode. |
| `/api/jobs/{job}/files/{filename}` | GET | Required in deploy | Disabled in protected modes | Disabled in protected modes | Any authenticated user | N/A | Generated XLSX/PDF direct file | PR #91 disables legacy output-root downloads in deploy/database/platform/database-artifact mode; local-UAT local mode remains supported. |
| `/api/line-items/normalize` | POST | Required in deploy | Uses selected pricing path in payload; DB mode attaches workspace-owned pricing detail | N/A | `canGenerateQuote` | Yes | Quote basis/line item normalization | Pricing fallback is blocked in DB mode after PR #88. |
| `/api/draft` | POST | Required in deploy | Payload/state only | N/A | Any authenticated user today | Yes | Uploaded images/PDFs, quote details, AI draft | AI draft fallback now fails closed in protected modes; role expectations still need hosted/protected/deploy review. |
| `/api/generate` | POST | Required in deploy | Payload/session storage may be DB-scoped | Session update through storage if supplied | Any authenticated user today | Yes | Quote generation, temp/output files | Pricing references are workspace-strict in DB mode after PR #88; profile/layout resolution is workspace-strict after PR #89. |
| `/api/log` | POST | Required in deploy | Current log context only | N/A | Any authenticated user | Yes | Client diagnostics | Logs are sanitized, but hosted logging/retention is not productionized. |
| `OPTIONS *` | OPTIONS | Host guard | N/A | N/A | None | N/A | CORS preflight | Blocks CORS preflight. |

Fail-open and client-trusting routes:

- Local-UAT `/api/jobs/{job}/files/{filename}` remains a localhost convenience route; PR #91 disables it in hosted/database/platform/deploy paths.
- `/api/quote-sessions` local runtime list/detail/save/delete/download paths now block in protected modes when workspace-owned database quote-session storage is unavailable.
- `/api/generate` accepts payload-selected `profile_id`; in DB/platform mode, profile/pricing/layout existence must be satisfied only by workspace-owned persisted records, with no local/bundled fallback.
- `/api/draft` blocks instead of returning local fallback draft data when remote AI is missing or failed in protected modes.

## Workspace And Tenant Isolation Matrix

| Surface | Workspace-bound proof | Gap | Severity | Release gate |
| --- | --- | --- | --- | --- |
| DB profiles | `sqag_profiles` primary key includes `(workspace_id, profile_id)` and DB profile reads use the current workspace. PR #89 makes DB profile lists workspace-row-only and generation consume workspace DB profile defaults/layout artifacts. | Object storage is still missing for final production profile asset storage. | Medium | Keep regression coverage; production object-storage blocker remains. |
| DB pricing references | `sqag_pricing_references` primary key includes `(workspace_id, reference_id)`; PR #88 list/detail/export/generation read only current-workspace DB rows. | Uploaded/reference assets still need final object-storage productionization. | Medium | Keep regression tests; production object-storage blocker remains. |
| DB quote sessions | `sqag_quote_sessions` key includes `(workspace_id, session_id)` (`webapp/server.py:6823`), reads include workspace predicate (`webapp/server.py:7437`). | Protected modes block local runtime quote sessions when DB storage is unavailable. Admin can view other-user DB sessions only under visibility conditions (`webapp/server.py:7405`). | Medium | Keep protected-mode local-runtime block and DB owner tests green; add hosted policy before launch. |
| DB artifacts | `sqag_quote_artifacts` and `sqag_file_artifacts` keys include `workspace_id`. PR #89 uses stored profile layout artifacts for DB-mode generation. | This is SQLite/BLOB mode, not object storage for generated bytes. | Medium/High | Local-UAT/synthetic evidence only; hosted/protected/deploy and production readiness require real object-storage provider wiring. |
| Object-storage contract | `webapp/object_storage.py` defines workspace-scoped object metadata, checksums, retrieve/delete, and wrong-workspace denial. | Synthetic in-memory evidence only; no live cloud/object provider is wired. Runtime `object` mode fails closed. | Medium | Use `scripts/verify_object_storage_contract.py` as provider-neutral evidence; add a real provider adapter, DB metadata integration, DB+object backup/restore, and retention/delete evidence before production. |
| Local profile/pricing/session roots | Local roots are shared by process and company/default identifiers. | Not tenant-isolated. | High in hosted mode | Allowed only for local UAT/test harness. |
| Platform session | `safe_platform_session_context()` requires consumed outcome, user id, workspace id, app key, and supported role (`webapp/server.py:6944`). | Source contract has been audited against Swooshz Platform `origin/main` at `5bce4d52e4273762375d97149b1d77e5716189b2`, but live Platform-to-SQAG deployment behavior is not verified. | Medium | Keep `docs/platform-integration-contract.md` current and complete live Platform smoke before production. |

Cross-workspace leak paths:

- Pricing references: PR #88 blocks DB/platform list, detail, export, and generation fallback to local/bundled pricing packs.
- Profile layout/defaults: PR #89 blocks database-mode generation unless the selected workspace DB profile row and DB layout artifact are present.
- Legacy job files: PR #91 disables direct output-root file downloads in hosted/database/platform/deploy paths. Local-UAT local storage keeps the route as a localhost workflow convenience.

## Artifact Lifecycle And Security Matrix

| Artifact path | Storage | Download route | Controls present | Gap | Severity |
| --- | --- | --- | --- | --- | --- |
| Async job output | `QUOTE_OUTPUT_ROOT/{job_id}` | `/api/jobs/{job}/files/{filename}` | Safe filename allowlist and output-root containment; PR #91 disables the route in hosted/database/platform/deploy paths. PR #94 blocks protected generate paths before local output artifacts are created or returned when artifact mode is local; object artifact mode now also fails closed until a real provider adapter exists. | Local-UAT local mode still uses this convenience route; hosted/protected/deploy paths require object storage evidence before readiness. | Low in local-UAT only |
| Quote-session XLSX/PDF in local mode | `QUOTE_DATA_ROOT/quote-sessions/{session}/exports` | `/api/quote-sessions/{id}/download/{kind}` | Safe session id, expected filename, stale checks (`webapp/server.py:12963`), and PR #93 blocks protected local quote-session routes when database storage is unavailable. | Local mode has no tenant boundary and remains local-UAT only. | Medium |
| Quote-session XLSX/PDF in DB artifact mode | `sqag_quote_artifacts` | `/api/quote-sessions/{id}/download/{kind}` | Workspace/session/artifact-kind query and owner visibility through session metadata (`webapp/server.py:7355`). Synthetic SQLite backup/restore/rollback verification covers DB rows and BLOB artifacts together. | SQLite/BLOB cannot satisfy hosted/protected/deploy readiness; generated bytes require object storage and live DB+object backup/restore evidence. | Medium/High |
| Uploaded booth/render images | Request payload and draft session files | Stored in draft files and temp job directory | MIME from data URL/name, base64 decode, size limits (`webapp/server.py:8388`, `8422`, `12320`). Protected generate paths now block before temp upload copies when artifact mode is local. | MIME sniff is partial; persistent object ownership is not final. | Medium |
| Pricing visual assets | Local pricing pack or DB file artifacts | Export/reference rendering | Filename sanitization, image data URL parse, byte limits (`webapp/server.py:7287`). Protected settings saves block visual uploads before local filesystem artifact writes when database artifact storage is unavailable. | DB artifacts are not final object storage; local pack path is shared in local-UAT mode only. | Medium/High |
| Profile layout workbook | Local profile pack in local mode; DB file artifacts in database mode | Profile export/generation | XLSX zip validation, safe filenames, workspace-scoped DB artifact lookup after PR #89. Protected settings saves block layout uploads before local filesystem artifact writes when database artifact storage is unavailable. | Object storage is still missing for production storage. | Medium |

Path traversal review:

- Static files enforce containment under `STATIC_DIR`.
- Legacy job downloads enforce filename allowlist and output-root containment, and PR #91 disables the route in hosted/database/platform/deploy paths. This audit did not confirm arbitrary file read.
- Quote-session local path helpers validate generated safe session IDs and expected filenames.
- The main artifact release blocker is authorization/ownership, not raw traversal.

## Import/Upload Risk Matrix

| Surface | Accepted input | Controls | Gap | Severity |
| --- | --- | --- | --- | --- |
| Pricing template validation | `.xlsx`, `.csv` | Data URL base64 decode max 10 MB; extension check; ZIP entry and total uncompressed limits; row and column limits; formula text sanitization on save. | MIME/content-type is driven by data URL and filename. Needs hostile workbook corpus before production. | Medium |
| Pricing import preview | `.xlsx`, `.csv`, `.md` | Same decode limits; parser exceptions become sanitized validation errors; AI timing logs are metadata-only. | AI normalization can see source rows; hosted privacy posture depends on provider config and data handling policy. | Medium |
| Profile save/import | JSON payload with optional embedded layout workbook/rules | Profile id/filename sanitization, layout XLSX validation, rules JSON limit, formula-neutralized defaults. | Profile layout assets stored in DB artifact mode but not consumed from workspace artifacts during generation. | High |
| Booth/render images | Data URL images/PDFs | Max files, max bytes, base64 decode, basic PDF and image checks. | MIME sniffing is partial; temp/local roots not production storage. | Medium |
| Client log events | JSON event/details | Event allowlist/category and sanitize log values. Synthetic hosted observability verifier now checks metadata-only output, sensitive-value omission, and error references. | External hosted logging backend wiring, alert delivery, and live hosted smoke evidence are not productionized. | Medium |

Formula injection:

- Profile defaults call `sanitize_profile_default_value()` and pricing reference payloads call `sanitize_formula_text()`.
- `scripts/generate_quote.py` tests cover XLSX and CSV formula-neutralization. Keep these tests in the release gate.

Malformed workbook risk:

- Pricing XLSX parsing checks ZIP member size and total uncompressed size.
- Profile layout validation checks workbook structure.
- Continue adding hostile XLSX/ZIP regression fixtures that are synthetic and metadata-only.

Original filename risk:

- Profile layout filenames use `Path(...).name`, suffix allowlist, and sanitized stems.
- Download filenames use `safe_segment()`.
- Visual reference filenames use sanitized generated names.

## Auth/Session Boundary Review

Positive controls:

- Deploy mode refuses to start without a complete auth boundary when auth is required.
- Session cookies are signed and `Secure` is set in deploy mode.
- OIDC callback validates state before exchanging code.
- Platform launch consumes a launch token server-to-server and requires workspace, user, app key, role, and expiry context.
- CSRF/same-origin checks apply to POST and DELETE after platform launch handling.
- Host guard blocks untrusted hosts.
- CORS preflight is denied.
- Security headers include no-store, nosniff, DENY framing, same-origin policies, restrictive CSP, and permissions policy.

Release blockers and gaps:

- Auth/session does not compensate for data paths that resolve local/bundled pricing/profile assets in DB/platform mode.
- `/api/draft` and `/api/generate` do not currently require an explicit generate permission at every path. `/api/line-items/normalize` and quote-session save do, but draft/generate should be reviewed for hosted/protected/deploy role expectations.
- Local-UAT job status/download behavior remains local-only; PR #91 owner-binds job status and disables legacy file downloads in hosted/database/platform/deploy modes.
- Platform-owned auth/workspace source contract has been audited against the
  current Swooshz Platform main contract, but live Platform-to-SQAG deployment
  behavior remains unverified.

## Fallback/Fail-Open Audit

Confirmed Load Sample posture:

- Product code no longer exposes `/api/samples`, `DEFAULT_SAMPLE_ID`, `setSampleDetails`, or the Load Sample button.
- Remaining Load Sample/sample/Kent references are in tests, scanner terms, or historical audit docs.

Disallowed in database/platform/deploy/production mode:

- Load Sample product CTA or route/API.
- Synthetic/Kent/sample/demo defaults as product data.
- Bundled private-like pricing/profile fallback.
- Local profile/pricing pack fallback.
- Fake success from sample or local data when real workspace data is missing.
- Legacy direct artifact download without workspace/session authorization; PR #91 disables the legacy route in hosted/database/platform/deploy modes.
- Broad exception handling that hides storage/auth/artifact/AI failure and returns success.

Current fallback blockers:

| Fallback | Evidence | Risk | Severity |
| --- | --- | --- | --- |
| DB pricing list/detail/export/generation local/bundled fallback | Resolved in PR #88 | Database/platform mode now returns only workspace-owned DB pricing references and blocks same-id local/bundled fallback after delete. | Resolved High |
| Profile/layout generation local fallback | Fixed by PR #89 | Missing workspace profile assets now block database-mode generation instead of using bundled/default/local layout. | Resolved for DB/platform mode |
| Legacy job-file download authorization | Fixed by PR #91 | Hosted/database/platform/deploy mode disables `/api/jobs/{job}/files/{filename}` legacy output-root downloads; job status/result is owner/workspace-bound in protected modes. | Resolved High |
| AI draft local fallback | Resolved in PR #92 | Protected modes now require the real OpenAI draft path and return blocked status with a generic message if remote AI is missing, unconfigured, unavailable, or returns unusable output. Local-UAT mode keeps the local starter fallback only for localhost testing. | Resolved High |
| Quote-session local runtime storage | Resolved in PR #93 | Protected modes now return a generic blocked/failed response instead of listing, reading, saving, deleting, downloading, or generate-persisting quote sessions through `QUOTE_DATA_ROOT/quote-sessions` when database storage is unavailable. | Resolved High |
| Local quote artifact storage | Resolved in PR #94 for protected generate and artifact-upload paths | Protected modes now return a generic failed response before creating or returning local `QUOTE_OUTPUT_ROOT` quote artifacts, profile layout uploads, or pricing visual uploads when database artifact storage is unavailable. Database artifact mode is local-UAT/synthetic evidence only, not hosted/protected/deploy readiness or production object storage. | Resolved High for protected local artifact success path |
| Job/session summary local pack fallback | Resolved for newly generated quote sessions by immutable `generation_snapshot` metadata | Generated session display/audit labels come from saved workspace-owned profile/pricing summaries instead of current local/bundled pack loaders. Existing legacy sessions without snapshots remain readable with backward-compatible summaries. | Low/legacy |
| Broad defensive exception handlers | `webapp/server.py:13365`, `13764`, `13803` | Mostly privacy-safe failure handling, but review each before hosted release. | Medium |

## Secrets, Privacy, And Logging Review

Secret/private-data search families reviewed:

- API keys and token-looking strings.
- DB URL examples.
- OAuth callback query parameters.
- Staff/customer/private data markers.
- Local runtime root examples.
- Generated quote/output references.

Findings:

- No committed real secret was confirmed in this audit.
- Synthetic provider-key placeholders, synthetic callback query values, and example database URLs remain in tests/docs and are covered by redaction assertions.
- `safe_error_messages()` scrubs secret-looking text (`webapp/server.py:570`).
- Request log redaction hides callback query values and sensitive query keys (`webapp/server.py:542`).
- `write_local_log()` sanitizes details before JSONL output (`webapp/server.py:1182`).
- `run_quote_job()` includes stdout/stderr/brief/output paths only when not in deploy mode (`webapp/server.py:13372`).

Gaps:

- Hosted logging backend vendor/export wiring and alert delivery are not productionized in this repo.
- Local logs are acceptable for local UAT only.
- Future scanner/report output must remain metadata-only and must not echo source lines containing private values.

## Dependency And Supply-Chain Review

Repository supply-chain surfaces:

- Python dependencies are pinned in `requirements.txt`.
- Node dependency surface is small: `playwright` dev dependency only.
- CI runs Gitleaks through a Docker image, `npm ci`, `npm audit --audit-level=high`, Python syntax checks, local PDF guard, dynamic pricing guard, full unit tests, and Playwright smoke/stress (`.github/workflows/ci.yml`).
- GitHub Actions permissions are read-only repository contents.
- No deployment job is configured.
- No package install script was found in `package.json`.

Supply-chain gaps:

- CodeQL is not enabled.
- Python dependency audit command is not documented in CI.
- Gitleaks uses `ghcr.io/gitleaks/gitleaks:latest` rather than a pinned digest/tag.
- Branch protection requirements are not documented as complete.
- Dependency review/advisory evidence is limited to local `npm audit` and CI's dependency-audit job.

Local dependency validation results are recorded later in this document.

## OWASP Mapping

| OWASP-style category | SQAG evidence | Release verdict |
| --- | --- | --- |
| Broken Access Control | Workspace DB rows exist; PR #88/#89 fixed pricing/profile DB fallbacks and PR #91 disables legacy job-file downloads in protected modes. Remaining access-control work is mainly hosted role policy and artifact lifecycle/object storage. | Medium gaps remain. |
| Cryptographic Failures / Sensitive Data Exposure | Signed cookies, secure deploy cookies, redaction helpers, and formula hardening exist. Local logs/storage remain UAT-only. | Medium gaps remain. |
| Injection | JSON parsing, safe IDs, safe filenames, formula neutralization, XLSX XML limits, no SQL string interpolation for user IDs in core queries. | No confirmed critical injection, but hostile workbook testing should expand. |
| Insecure Design | Local UAT architecture remains mixed with hosted/platform goals. Local/bundled fallbacks are product-shape blockers. | High blockers remain. |
| Security Misconfiguration | Deploy fail-closed guard exists; CI has read-only permissions. CodeQL absent; Gitleaks image not pinned. | Medium gaps remain. |
| Vulnerable/Outdated Components | `npm audit` is part of CI; local result is tracked below. Python audit not present. | Evidence incomplete. |
| Identification/Auth Failures | OIDC state, allowlist, platform launch validation, and signed cookies exist. Live platform contract not verified. | Medium gap. |
| Software/Data Integrity Failures | CI validates syntax/tests and security gates, but external actions/images are not fully pinned. | Medium gap. |
| Logging/Monitoring Failures | Privacy-safe local logging exists, and synthetic hosted observability evidence now checks metadata-only logs, event allowlisting, support references, and health/readiness metadata. External vendor wiring and alert delivery are host-specific. | Medium gap. |
| SSRF / External Fetch Risk | OIDC and platform launch outbound URLs come from env config; AI provider requests go to configured providers. No arbitrary user-supplied URL fetch was confirmed. | Low/Medium, keep config allowlists strict. |
| Business Logic Vulnerabilities | Mixed workspace/local pricing and profile generation fallbacks are fixed for DB/platform mode; legacy job files are disabled in protected modes; disabled/deleted artifact lifecycle cases still need tests. | Medium gaps remain. |

## Business Logic Security Findings

| Scenario | Result | Severity | Required follow-up |
| --- | --- | --- | --- |
| Generate quote with mixed workspace profile/session state | PR #89 makes database-mode generation use workspace-owned profile defaults/layout artifacts only. Pricing references are workspace-strict in DB mode after PR #88; legacy job downloads are disabled in protected modes after PR #91. | Resolved for profile/pricing and legacy direct downloads. | Keep workspace profile/pricing/session and job-route isolation tests green. |
| Disabled/deleted pricing reference still usable | PR #88 blocks same-id local/bundled fallback after DB reference deletion. | Resolved High | Keep regression coverage while adding future disabled/reference lifecycle states. |
| Deleted sessions/artifacts restored/exported/downloaded | Object-mode generated artifact metadata is tombstoned on quote-session delete, stubbed object deletion is exercised, deleted/tombstoned/missing/corrupt object artifacts do not download, local staging cleanup has synthetic evidence, and quote-session DB/object download routes revalidate current metadata before returning bytes. Legacy job files remain disabled in protected modes after PR #91. | Medium | Add live provider retention/delete evidence and production DB+object backup/restore drills. |
| Edited past session leaks current/default settings | Generated sessions store privacy-minimized immutable generation snapshots, and draft edits or profile/pricing rename/delete do not mutate generated snapshot labels or revive local/bundled fallbacks. | Low/legacy | Existing legacy sessions without snapshots remain readable with backward-compatible summaries; complete final generated audit evidence before production. |
| Cross-user dashboard visibility | DB mode owner visibility exists; ownerless sessions remain visible. | Medium | Define migration/owner policy before hosted/protected/deploy use. |
| Race/deletion/export edge cases | Deterministic tests now cover delete/download, stale export, missing/corrupt object, deleted metadata, wrong-workspace, and mid-retrieve tombstone paths for generated quote-session artifacts. | Medium | Add live provider race evidence, broader concurrent stress, and final production audit coverage. |

## Findings Table

| Severity | Finding | Evidence | Impact | Required fix |
| --- | --- | --- | --- | --- |
| Critical | None confirmed in the audited source and metadata-only scans. | N/A | N/A | Keep Critical gate open for any confirmed cross-workspace private data leak, unauthorized artifact byte access, production auth bypass, committed secret, or arbitrary file read/write. |
| High, resolved in PR #88 | DB/platform pricing references could include shared local/bundled packs. | Regression coverage in `tests/test_webapp.py` | Workspace users can no longer list/detail/export/generate from non-owned local/bundled references in DB mode. | Keep DB pricing isolation tests in the release gate. |
| High, resolved in PR #89 | Profile/layout generation still resolved local/default profile packs. | Regression coverage in `tests/test_webapp.py` | Missing workspace profile assets now block DB/platform generation. | Keep DB profile/layout isolation tests in the release gate; object storage remains separate. |
| High, resolved in PR #91 | Legacy job-file downloads were not workspace/session-owner bound. | Regression coverage in `tests/test_webapp.py` | Hosted/database/platform/deploy mode now returns a generic not-found response instead of legacy output-root bytes; local-UAT local mode remains supported. | Prefer quote-session artifact downloads and add object storage before production. |
| High, resolved in PR #92 | Local AI draft fallback returned product-visible drafted result when remote AI was missing/failed. | Regression coverage in `tests/test_webapp.py` | Protected-mode users now receive a blocked draft result with a generic message, and no local starter draft/result is returned. | Keep protected-mode fail-closed draft tests in the release gate; local-UAT fallback remains local only. |
| High, resolved in PR #93 | Local quote-session runtime storage could be product-visible in protected modes when database storage was unavailable. | Regression coverage in `tests/test_webapp.py` | Protected-mode quote-session routes and generate-session persistence now return a generic storage-unavailable response instead of using local runtime session files. | Keep protected-mode quote-session storage tests in the release gate; local-UAT fallback remains local only. |
| High, resolved in PR #94 | Local artifact storage could be product-visible in protected generate and artifact-upload paths when database artifact storage was unavailable. | Regression coverage in `tests/test_webapp.py` | Protected-mode generate paths, profile layout uploads, and pricing visual uploads now fail with a generic artifact-storage-unavailable response before local output or upload artifact files are created or returned. | Keep protected-mode artifact storage tests green; database artifact mode is local-UAT/synthetic evidence only, and production object storage remains required. |
| Medium, evidence path added in PR #95 | DB/DB-artifact backup, restore, retention, and rollback had no safe verifier. | `scripts/verify_database_backup_restore.py`, `docs/internal-alpha-retention-policy.json`, `tests/test_database_backup_restore_verifier.py` | Synthetic SQLite rows and BLOB artifacts can be backed up, restored, checksum-verified, and rolled back together without private data. | This is local-UAT/synthetic evidence only; hosted/protected/deploy and production readiness still require object storage and hosted operations evidence. |
| Medium, evidence path added in PR #96 | Hosted logging/monitoring evidence had no safe verifier. | `scripts/verify_hosted_observability.py`, `docs/hosted-observability-policy.json`, `tests/test_hosted_observability_verifier.py` | Synthetic structured logs, event categories, support error references, and health metadata can be checked without private data or an external vendor dependency. | This is synthetic metadata-only evidence; alert delivery, vendor/export wiring, and production object storage remain separate. |
| Medium, evidence path added in PR #97 | Hosted smoke evidence had no safe verifier. | `scripts/verify_hosted_smoke.py`, `tests/test_hosted_smoke_verifier.py` | Synthetic deploy/database/database-artifact smoke coverage verifies platform launch, auth gate, workspace profile/pricing use, quote generation, session persistence, XLSX/PDF artifact download, delete, logout, and legacy direct job-file lockdown without private data or live Platform dependency. | This is synthetic metadata-only evidence; live Swooshz Platform integration, object storage, and production deployment/operations evidence remain separate. |
| Medium, evidence path added in PR #98 | Object-storage artifact contract was missing. | `webapp/object_storage.py`, `scripts/verify_object_storage_contract.py`, `tests/test_object_storage_contract_verifier.py` | Synthetic in-memory contract evidence now covers store/retrieve/delete, checksum verification, workspace metadata enforcement, wrong-workspace denial, and metadata-only output for generated quote artifacts, uploaded references, profile layouts, and pricing visuals. Runtime object mode fails closed because no real provider adapter is wired. | This is provider-neutral contract evidence only; production still requires real object-storage provider wiring, DB+object backup/restore, retention/delete evidence, and deployment/operations evidence. |
| Medium, scaffold added in PR #99 | Real object-storage provider configuration was not validated. | `webapp/object_storage.py`, `webapp/server.py`, `tests/test_object_storage_provider_config.py`, `tests/test_production_readiness.py`, `tests/test_webapp.py` | Object mode now reports disabled, S3-compatible, synthetic, and unsupported provider status as metadata only. Missing S3-compatible config is listed by environment variable name only, and incomplete configuration remains fail-closed. | Production still requires live provider evidence, DB+object backup/restore, retention/delete evidence, and production operations evidence. |
| Medium, runtime groundwork added in PR #100 | Generated quote artifacts had no real object-mode runtime integration boundary. | `webapp/object_storage.py`, `webapp/server.py`, `migrations/003_object_artifact_metadata.sql`, `tests/test_object_storage_provider_config.py`, `tests/test_webapp.py` | A credentialed S3-compatible adapter boundary can store/retrieve/delete through injected fake clients, generated artifacts are stored in the object backend with safe DB metadata, and authorized quote-session downloads use DB metadata plus object retrieval. | Live provider evidence, uploaded/reference/profile object wiring, DB+object backup/restore, and retention/delete evidence remained blockers. |
| Medium, lifecycle evidence added in this PR | Object-mode generated artifact lifecycle, staging cleanup, and DB+object restore behavior had only partial coverage. | `webapp/server.py`, `scripts/verify_object_artifact_lifecycle.py`, `tests/test_webapp.py`, `tests/test_object_artifact_lifecycle_verifier.py`, `tests/test_production_readiness.py` | Quote-session deletion tombstones object metadata, stubbed object deletion is attempted, deleted/missing/corrupt/wrong-workspace object artifacts fail closed, local staging files are cleaned after object persistence, and synthetic SQLite+stubbed-object backup/restore evidence is metadata-only. | This is synthetic/stubbed evidence only; live provider retention/delete, production operations, and final audit remain blockers. |
| Medium, live-provider evidence path added in this PR | Live S3-compatible object-storage provider proof had no safe metadata-only verifier path or repo-declared SDK dependency. | `requirements.txt`, `scripts/verify_live_object_storage_provider.py`, `scripts/check_production_readiness.py`, `webapp/server.py`, `tests/test_live_object_storage_provider_verifier.py`, `tests/test_production_readiness.py`, `tests/test_webapp.py` | Operators can run an explicit opt-in verifier for synthetic XLSX/PDF store/retrieve/checksum/content-type/byte-size/wrong-workspace/delete/tombstone/missing-object checks from repo-pinned S3 SDK dependencies; no-env, incomplete-env, and test-injected backends fail closed or remain non-crediting. A sanitized operator run on 2026-07-07 passed the live verifier with `test_injected_backend=false` and all live checks true, without committing provider values, object keys, artifact bytes, DB URLs, private paths, uploaded content, tenant data, or secrets. | Production still requires live retention/delete, operations, observability, supply-chain hardening, live Platform smoke, session/business hardening, and final audit before `production_ready` can become true. |
| Medium, Postgres metadata adapter boundary added in PR #112 and live DB evidence documented in this PR | The production DB path needed a real Postgres/Neon-compatible runtime boundary and operator-run live metadata evidence instead of only a readiness scaffold. | `requirements.txt`, `webapp/server.py`, `scripts/verify_production_database_provider.py`, `scripts/check_production_readiness.py`, `tests/test_postgres_metadata_storage.py`, `tests/test_production_database_provider_verifier.py`, `tests/test_production_readiness.py`, `tests/test_webapp.py`, `docs/platform-scoped-storage-mode.md`, `docs/production-readiness-audit.md` | Postgres-compatible DB URLs are recognized without printing values; the runtime uses a pinned Psycopg dependency, dialect-aware schema checks, workspace-bound metadata queries, and fail-closed handling for missing drivers, connection failures, missing schema, missing workspace context, unsupported schemes, and Postgres DB-BLOB artifact mode. A sanitized operator run on 2026-07-07 applied the existing guarded metadata migrations, then passed live Postgres-compatible evidence with schema available, synthetic profile/pricing/session/object metadata CRUD, two-workspace isolation, object artifact metadata pairing, cleanup completed, and `db_blob_artifact_rows_written=0`. SQLite remains local-UAT/synthetic only, DB rows store metadata only, generated XLSX/PDF bytes stay in object storage, and R2/object storage was not touched. | Production still requires live retention/delete, operations, hosted logging/smoke, live Platform smoke, session/business hardening, and final audit. |
| Medium, live DB+object backup/restore drill path added and live evidence documented in this PR | The production DB+object gate needed an opt-in operator drill that proves synthetic metadata and object bytes can be restored together without exposing private values or touching real tenant data. | `scripts/verify_live_db_object_backup_restore.py`, `scripts/check_production_readiness.py`, `webapp/server.py`, `tests/test_live_db_object_backup_restore_verifier.py`, `tests/test_production_readiness.py`, `docs/platform-scoped-storage-mode.md`, `docs/production-readiness-audit.md` | The drill remains fail-closed on missing env, non-isolated restore DB/object targets, restore DB visibility of active synthetic rows, restore object visibility of the active synthetic object, missing backup ownership/restore-window decisions, DB/object write/read failures, checksum mismatch, metadata/object pairing mismatch, or cleanup failure. A sanitized operator-run drill on 2026-07-07 passed with `test_injected_backend=false`, `live_db_object_backup_restore_evidence_supported=true`, active DB/object write-read verified, restore DB/object write-read verified, restore DB unable to read active synthetic rows before restore, restore object unable to read the active synthetic object before restore, checksum/content type/byte size matched, DB+object metadata pairing verified, workspace isolation preserved, cleanup completed, `active_db_synthetic_rows_written=7`, `active_object_synthetic_objects_written=1`, `restore_db_synthetic_rows_written=7`, `restore_object_synthetic_objects_written=1`, and `db_blob_artifact_rows_written=0`. The run used synthetic namespaced DB rows and one tiny synthetic generated artifact object only, restored only into isolated targets, performed no destructive restore over active live targets, and did not print or commit DB URLs, hostnames, usernames, passwords, connection strings, endpoints, bucket names, provider values, object keys, access keys, secret keys, OAuth values, cookies/tokens, private paths, tenant data, generated quote contents, artifact bytes, backup dumps, restore dumps, or secrets/private values. | The passing non-test-injected operator run can remove only `db_object_backup_restore_live_evidence_missing`; no unrelated blocker is removed. `production_ready=false` remains until live retention/delete, hosted logging/monitoring and alert delivery, hosted smoke, production deployment operations, live Platform-to-SQAG launch smoke, session/business hardening, and final production audit are complete. |
| Medium, live retention/delete verifier path added in this PR | The production object-mode retention/delete gate needed an opt-in operator drill for active DB metadata plus live object deletion without exposing private values or touching real tenant data. | `scripts/verify_live_retention_delete.py`, `scripts/check_production_readiness.py`, `webapp/server.py`, `tests/test_live_retention_delete_verifier.py`, `tests/test_production_readiness.py`, `docs/platform-scoped-storage-mode.md`, `docs/production-readiness-audit.md` | The drill remains fail-closed unless `SQAG_LIVE_RETENTION_DELETE_EVIDENCE=1`, `SQAG_DATABASE_URL`, `SQAG_STORAGE_MODE=database`, `SQAG_ARTIFACT_STORAGE_MODE=object`, and the canonical `SQAG_OBJECT_STORAGE_*` env names are present. It validates runtime modes before writing synthetic rows or objects. It uses synthetic namespaced DB rows and one tiny synthetic generated artifact object only. It verifies active DB metadata, active object write/read, checksum/content type/byte size, DB+object metadata pairing, active runtime export download through `quote_session_export_artifact()` before tombstone/delete, tombstone/delete behavior, denied deleted downloads afterward, missing object fail-closed behavior, wrong-workspace denial, repeated delete safety, and cleanup. Output is sanitized booleans/counts/status fields and blocker IDs only; it must not print or commit DB URLs, hostnames, usernames, passwords, connection strings, endpoints, bucket names, provider values, object keys, access keys, secret keys, OAuth values, cookies/tokens, private paths, tenant/customer/staff/profile/pricing data, generated quote contents, artifact bytes, backup dumps, restore dumps, or secrets/private values. Test-injected backends exercise logic only and do not count as live evidence. | Passing non-test-injected evidence can remove only `object_retention_delete_live_evidence_missing`; no unrelated blocker is removed. No live retention/delete pass evidence is claimed in this PR, and `production_ready=false` remains until hosted logging/monitoring and alert delivery, hosted smoke, production deployment operations, live Platform-to-SQAG launch smoke, session/business hardening, and final production audit are complete. |
| Medium, route/race hardening added in this PR | Generated quote-session download routes needed focused delete/export/download race and stale/deleted artifact coverage. | `webapp/server.py`, `tests/test_webapp.py`, `tests/test_production_readiness.py` | DB and object artifact downloads revalidate current workspace-owned metadata before returning bytes; tests cover DB delete/download, stale DB exports, object mid-retrieve tombstone, missing/corrupt object content, generated snapshot digest stability, and readiness reporting. | This is deterministic local/stubbed evidence only; live object-provider race evidence, operations evidence, and final audit remain blockers. |
| Medium, blocked-readiness guard added in this PR | The old DB + DB-artifact hosted posture could still be read as launch evidence. | `docs/internal-uat-coolify-deploy.md`, `scripts/verify_internal_alpha_hosted_validation.py`, `tests/test_internal_alpha_hosted_validation_verifier.py` | Operators now have placeholder-only env names and a synthetic validation bundle that verifies `database_blob_artifact_storage_not_launch_ready` remains present without printing secrets, paths, DB URLs, hostnames, quote contents, or artifact bytes. | This is not live deployment evidence. Production still requires retention/delete evidence, production operations, Platform integration, observability export/alerts, supply-chain hardening, and final audit. |
| Medium, source-contract audit added in this PR | Live Swooshz Platform integration expectations needed to be checked against the current Platform repo contract. | `docs/platform-integration-contract.md`, `docs/README.md`, this audit | SQAG's expected header-only launch token, safe consume context, role mapping, workspace isolation, and fail-closed storage assumptions are documented against current Platform main source evidence. | This is not live Platform deployment evidence. Production still requires live Platform-to-SQAG smoke, production operations, object storage, observability export/alerts, supply-chain hardening, and final audit. |
| Medium, resolved in PR #91 | Async job status/result was random-ID gated, not owner-bound. | Regression coverage in `tests/test_webapp.py` | Hosted/database/platform/deploy job status/result reads require the creating platform user/workspace. | Keep job owner visibility tests in the release gate. |
| Medium | Import/upload validation is good but hostile-corpus evidence is incomplete. | `webapp/server.py:3713`, `4728`, `6322`, `8422` | Malformed XLSX/PDF/image edge cases could cause parser failure or resource pressure. | Add synthetic hostile upload fixtures and regression tests. |
| Medium | Hosted alert delivery and production observability wiring are not productionized. | `webapp/server.py:1182`, docs | Synthetic evidence proves local schema/privacy properties, but not a host/vended log pipeline. | Add host-specific export/alert wiring before treating this as production observability. |
| Medium | Supply-chain evidence is incomplete. | `.github/workflows/ci.yml`, `package.json` | CI has useful gates but no CodeQL/Python audit and unpinned Gitleaks image. | Add CodeQL/dependency review or documented equivalent before production. |
| Low | Historical docs still mention Load Sample/sample paths as audit evidence. | `docs/architecture-dead-code-fallback-audit.md`, `docs/production-readiness-audit.md` | Could confuse future readers if not superseded by this doc. | Link this audit as the current release gate and trim historical wording later. |

## Hosted/Protected/Deploy Release Gate

Do not treat a hosted, protected, or deploy-mode SQAG environment as launch-ready until all are true:

- No product-visible Load Sample/sample/demo/fake seeded path exists.
- Database/platform mode cannot list, detail, export, delete, or generate from local/bundled private-like pricing references. PR #88 satisfies this pricing-reference gate; keep it covered by regression tests.
- Generation resolves profile defaults and layout workbook from workspace-owned profile assets. PR #89 satisfies this gate for DB/platform mode; keep it covered by regression tests.
- New workspaces have no real Koncept Images profile/pricing/layout pack by default; Koncept packs become available only after explicit import/seed into the intended workspace.
- Legacy `/api/jobs/{job}/files/{filename}` is disabled in hosted modes or authorized by workspace/session ownership. PR #91 satisfies this by disabling the route in deploy/database/platform/database-artifact modes.
- `/api/draft` does not return local fallback success in protected modes; PR #92 satisfies that gate with protected-mode regression coverage.
- Quote-session routes do not use local runtime storage in protected modes; PR #93 satisfies that local-runtime fail-closed gate.
- Protected generate paths and artifact-bearing settings uploads do not create or return local quote/profile/pricing artifacts when database artifact storage is unavailable; PR #94 satisfies that protected local-artifact fail-closed gate.
- Quote sessions must use workspace-owned database storage.
- Generated XLSX/PDF bytes must use object storage; DB/BLOB artifact mode must report `database_blob_artifact_storage_not_launch_ready`.
- Live provider evidence must be explicit and metadata-only; no-env, incomplete-env, synthetic, test-injected, local, or DB/BLOB evidence must not satisfy hosted/protected/deploy readiness.
- Backup/restore/rollback is documented and tested for DB rows plus object storage, not only synthetic SQLite/DB-BLOB rows.
- Hosted smoke covers platform launch, workspace profile save/use, pricing save/use, quote generation, session persistence, authorized object-backed XLSX/PDF artifact download, delete, logout, and legacy direct job-file lockdown.
- Logs remain privacy-minimized and support-traceable without raw prompts, uploads, provider responses, secrets, or generated quote contents.
- The hosted validation scaffold is a metadata-only negative check for the old DB/BLOB posture; it is not live host evidence.
- Runtime paths must use the configured object backend and DB metadata only, with no local/DB-BLOB fallback.
- Codex Security standard scan is complete or any incomplete status is explicitly disclosed.

## Production Release Gate

Do not claim production readiness until all hosted/protected/deploy gates plus these are true:

- A real object-storage provider is wired for generated XLSX/PDF, uploaded images/PDFs, profile layout assets, and pricing visual assets.
- Object provider configuration is present, validated, non-secret in diagnostics, and backed by a usable runtime adapter with live provider evidence.
- DB rows store object keys, checksums, byte sizes, content types, owner/workspace/session metadata, retention state, and audit metadata.
- Downloads stream or sign objects only after workspace/session/owner authorization.
- Backup and restore drills prove DB and object storage recover together against the live provider, not only the synthetic in-memory backend.
- Retention/deletion policies are implemented and tested against the live provider.
- Production deployment/operations evidence, alert delivery, and live host smoke checks are completed.
- CodeQL or equivalent static analysis is enabled.
- Python dependency audit/advisory check is documented.
- CI branch protection and required checks are documented as complete.
- No unresolved Critical/High findings remain, unless a written risk acceptance exists for a narrow internal-only exception. No such acceptance exists in this audit.

## Recommended Implementation PR Sequence

1. Pricing-reference isolation: completed in PR #88 for database/platform/deploy list/detail/export/generation fallback removal and same-id delete regression coverage.
2. Workspace profile/layout resolution: completed in PR #89 for DB/platform mode; generation now uses workspace-scoped profile defaults/layout artifacts and fails clearly if missing.
3. Legacy job route lockdown: completed in PR #91 by disabling `/api/jobs/{job}/files/{filename}` in hosted/database/platform/deploy paths and owner-binding `/api/jobs/{job}` status/result reads in protected modes.
4. AI fallback policy: completed in PR #92 for protected modes; local starter draft fallback is kept only for local-UAT/local-development behavior.
5. Quote-session local runtime policy: completed in PR #93 for protected modes; local quote-session filesystem storage is kept only for local-UAT/local-development behavior.
6. Local artifact storage policy: completed in PR #94 for protected generate paths and artifact-bearing settings uploads; local output/upload artifact storage is kept only for local-UAT/local-development behavior.
7. DB/DB-artifact backup evidence: completed in PR #95 for synthetic local-UAT coverage only; it no longer satisfies hosted/protected/deploy readiness.
8. Hosted observability evidence: completed in PR #96 for synthetic privacy-minimized structured logs, support references, event categories, and health metadata.
9. Hosted smoke evidence: completed in PR #97 for synthetic deploy/database/database-artifact smoke coverage on `127.0.0.1`; live Platform and object-storage verification remain separate.
10. Artifact object-storage contract: completed in PR #98 as provider-neutral contract and synthetic in-memory evidence only.
11. Object-storage provider configuration validation: completed in PR #99 as S3-compatible provider env-name validation without credentials or production readiness claims.
12. Real object-storage provider integration groundwork: completed in PR #100 with the credentialed S3-compatible adapter boundary, mocked adapter tests, generated artifact object metadata, and authorized quote-session retrieval; live provider evidence, uploaded/reference/profile object wiring, DB+object backup/restore, and retention/delete evidence remained.
13. Object artifact lifecycle groundwork: this PR adds generated object artifact tombstones, stubbed delete evidence, local staging cleanup, and synthetic DB+object backup/restore lifecycle verification; live provider retention/delete and real DB+object backup/restore remain.
14. Session and business-logic hardening: immutable profile/pricing snapshots, stale/deleted artifact route hardening, and deterministic delete/export/download race tests are in place; broader live/provider race evidence remains.
15. Live object-storage provider evidence path: PR #108 added an opt-in metadata-only verifier, readiness flag, and repo-pinned S3 SDK dependency set; operator-run live provider evidence remains required before production.
16. Blocked hosted validation scaffold: this PR updates placeholder-only env docs and a synthetic validation bundle so DB/DB-artifact mode stays blocked for launch readiness; live host evidence remains separate.
17. Live DB+object backup/restore drill path and evidence: completed for the current synthetic operator drill path with sanitized non-test-injected evidence. The run proved synthetic active DB/object writes, restore-target synthetic visibility probes before restore writes, isolated restore DB/object writes, DB+object pairing verification, workspace isolation, cleanup, and readiness credit for only `db_object_backup_restore_live_evidence_missing`.
18. Hosted production operations: host-specific logging export, alert delivery, production deployment evidence, live host smoke evidence, retention/delete evidence, and final audit.
19. Supply-chain hardening: CodeQL/equivalent, Python dependency audit, pinned security scanner image, branch protection docs.
20. Platform integration audit: source-contract audit added in this PR against current Swooshz Platform main; live Platform-to-SQAG smoke remains required before production.

## Codex Security Scan

Status: setup workspace timed out before scan start.

Requested mode: standard whole-repo scan.

Workspace/session ID: `699e9dd1-7d12-4943-bc13-17ba8c44a15f`

Scan ID: not issued. `await_codex_security_scan_start` returned `timed_out` before the user/app setup produced a durable scan ID.

Report finalization: not completed. No Codex Security findings, SARIF, or final markdown report are available from this attempted run.

Severity summary from plugin: unavailable because the scan did not start. This audit therefore does not claim a completed clean Codex Security scan.

## Validation Results

| Command | Result |
| --- | --- |
| `python scripts/verify_live_db_object_backup_restore.py` with live DB+object env removed from the subprocess | Expected nonzero blocked result. Reported `status=blocked`, `live_db_object_backup_restore_evidence_supported=false`, missing env names only, `blocked_isolated_restore_target_missing`, `blocked_backup_restore_decision_missing`, metadata-only privacy booleans, zero synthetic rows/objects written, no connection/write/read/restore attempts, and `production_ready=false`. |
| `python scripts/verify_live_db_object_backup_restore.py` with operator-supplied live DB+object env and isolated restore targets | Passed. Reported `status=passed`, `test_injected_backend=false`, `live_db_object_backup_restore_evidence_supported=true`, active DB/object write-read verified, restore DB/object write-read verified, restore DB could not read active synthetic rows before restore, restore object target could not read the active synthetic object before restore, checksum/content type/byte size matched, DB+object metadata pairing verified, workspace isolation preserved, cleanup completed, `active_db_synthetic_rows_written=7`, `active_object_synthetic_objects_written=1`, `restore_db_synthetic_rows_written=7`, `restore_object_synthetic_objects_written=1`, `db_blob_artifact_rows_written=0`, and `production_ready=false`. Output was sanitized booleans/counts/status fields only; no DB URLs, provider values, object keys, artifact bytes, tenant data, generated quote contents, backup dumps, restore dumps, or secrets/private values were printed or committed. |
| `python -m py_compile webapp/server.py webapp/object_storage.py scripts/check_production_readiness.py scripts/verify_live_db_object_backup_restore.py` | Passed. |
| `python -m unittest tests.test_live_db_object_backup_restore_verifier tests.test_database_backup_restore_verifier tests.test_object_artifact_lifecycle_verifier tests.test_production_readiness` | Passed: 58 tests OK, covering blocked live DB+object backup/restore preflight, configured and live synthetic restore-target isolation checks, missing decision markers, mocked successful drill logic, DB/object write failures, checksum mismatch, metadata/object pairing mismatch, cleanup failure, metadata-only output, synthetic backup/restore, synthetic object lifecycle, and readiness dropping only the live DB+object backup/restore blocker after passing evidence. |
| `python -m pip install --dry-run --ignore-installed --only-binary=:all: -r requirements.txt --disable-pip-version-check` | Passed. Resolver would install the pinned repo dependency set: `pypdfium2-5.9.0`, `Pillow-12.2.0`, `boto3-1.43.40`, `botocore-1.43.40`, `jmespath-1.1.0`, `python-dateutil-2.9.0.post0`, `s3transfer-0.19.0`, `six-1.17.0`, and `urllib3-2.7.0`. |
| `python -m pip install --only-binary=:all: -r requirements.txt --disable-pip-version-check` | Passed. Installed/confirmed the repo-pinned Python dependency set locally, including `boto3-1.43.40` and `botocore-1.43.40`, without provider credentials, endpoints, bucket names, object keys, or live evidence values. |
| `python -m unittest tests.test_live_object_storage_provider_verifier tests.test_object_storage_provider_config tests.test_production_readiness` | Passed: 41 tests OK, covering live-provider verifier no-env/incomplete/test-injected behavior, simulated real-provider metadata cleanup, object provider metadata-only status, readiness credit boundaries, and legacy `SQAG_*` object-storage env names not satisfying SQAG live evidence. |
| `python -m unittest tests.test_webapp.WebappServerTest.test_object_artifact_store_failure_does_not_fallback_to_local_links_or_db_blob tests.test_webapp.WebappServerTest.test_object_artifact_storage_mode_fails_closed_without_runtime_backend tests.test_webapp.WebappServerTest.test_object_artifact_storage_mode_with_incomplete_provider_config_fails_closed tests.test_webapp.WebappServerTest.test_object_artifact_storage_saves_db_metadata_and_downloads_through_authorized_route tests.test_webapp.WebappServerTest.test_object_artifact_storage_deleted_metadata_fails_closed tests.test_webapp.WebappServerTest.test_object_artifact_storage_tombstone_during_retrieve_fails_closed tests.test_webapp.WebappServerTest.test_object_artifact_storage_corrupt_or_missing_remote_object_fails_closed tests.test_webapp.WebappServerTest.test_object_artifact_storage_delete_session_tombstones_metadata_and_backend_object tests.test_webapp.WebappServerTest.test_object_artifact_delete_failure_preserves_session_and_active_metadata tests.test_webapp.WebappServerTest.test_object_artifact_storage_cleans_local_staging_files_after_persist` | Passed: 10 tests OK, covering object-mode fail-closed runtime behavior, no local job-file link fallback after store failure, no DB/BLOB fallback, wrong-workspace/corrupt/missing/tombstoned denial, tombstone-on-delete, and local staging cleanup. |
| `python -m py_compile webapp/server.py webapp/object_storage.py scripts/check_production_readiness.py scripts/verify_live_object_storage_provider.py scripts/verify_object_artifact_lifecycle.py` | Passed. |
| `python scripts/verify_live_object_storage_provider.py` | Expected nonzero exit 1. Reported `status=failed`, `live_provider_evidence_supported=false`, provider family `disabled`, missing env names `SQAG_LIVE_OBJECT_STORAGE_EVIDENCE` and `SQAG_OBJECT_STORAGE_PROVIDER`, all checks false, and metadata-only privacy booleans true. |
| `SQAG_LIVE_OBJECT_STORAGE_EVIDENCE=1 SQAG_OBJECT_STORAGE_PROVIDER=s3_compatible SQAG_OBJECT_STORAGE_ENDPOINT_URL=<redacted> SQAG_OBJECT_STORAGE_BUCKET=<redacted> SQAG_OBJECT_STORAGE_REGION=<redacted> SQAG_OBJECT_STORAGE_ACCESS_KEY_ID=<redacted> python scripts/verify_live_object_storage_provider.py` | Expected nonzero exit 1. Reported `status=failed`, `live_provider_evidence_supported=false`, provider family `s3_compatible`, missing env name `SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY`, provider blocker `missing_provider_config`, and no provider values, object keys, artifact bytes, or secrets in output. |
| `SQAG_LIVE_OBJECT_STORAGE_EVIDENCE=1 SQAG_OBJECT_STORAGE_PROVIDER=s3_compatible SQAG_OBJECT_STORAGE_ENDPOINT_URL=<redacted> SQAG_OBJECT_STORAGE_BUCKET=<redacted> SQAG_OBJECT_STORAGE_REGION=<redacted> SQAG_OBJECT_STORAGE_ACCESS_KEY_ID=<redacted> SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY=<redacted> python scripts/verify_live_object_storage_provider.py` | Expected nonzero exit 1. Reported `status=failed`, `live_provider_evidence_supported=false`, provider family `disabled`, missing env names `SQAG_LIVE_OBJECT_STORAGE_EVIDENCE` and `SQAG_OBJECT_STORAGE_PROVIDER`, and canonical `SQAG_*` required env names only. |
| `python scripts/verify_live_object_storage_provider.py` with canonical `SQAG_*` live provider env supplied by the operator environment | Passed. Reported `status=passed`, `test_injected_backend=false`, `live_provider_evidence_supported=true`, `missing_env_names=[]`, provider family `s3_compatible`, configured/runtime backend available true, `synthetic_only=false`, and all checks true: store, retrieve, checksum, content type, byte size, wrong-workspace denial, delete, tombstone, and missing-object fail-closed. Privacy booleans reported provider values, object keys, artifact bytes, and private values were not printed. |
| `python scripts/verify_production_database_provider.py` with `SQAG_DATABASE_URL` and `SQAG_LIVE_DATABASE_EVIDENCE=1` supplied by the operator environment | Initial expected nonzero exit 1 reported `database_family=postgres_compatible`, `live_database_evidence_enabled=true`, `connection_attempted=true`, `postgres_driver_available=true`, `app_runtime_postgres_supported=true`, and blocker `postgres_schema_missing`; no DB values were printed. |
| `python scripts/migrate_sqag_storage.py` with `SQAG_DATABASE_URL` supplied by the operator environment | Passed. Applied the existing guarded SQAG storage migrations and printed only `SQAG storage migrations applied.` |
| `python scripts/verify_production_database_provider.py` with `SQAG_DATABASE_URL` and `SQAG_LIVE_DATABASE_EVIDENCE=1` supplied by the operator environment after migration | Passed. Reported `status=passed`, `database_family=postgres_compatible`, `live_database_evidence_enabled=true`, `test_injected_backend=false`, `live_database_evidence_supported=true`, `production_database_evidence_supported=true`, `connection_attempted=true`, runtime schema available, synthetic metadata CRUD verified, two-workspace isolation verified, object artifact metadata pairing verified, `cleanup_completed=true`, and `db_blob_artifact_rows_written=0`. The verifier did not touch R2/object storage and did not print DB URL, hostname, username, password, connection string, provider value, object key, private path, tenant data, generated quote contents, or artifact bytes. |
| `python scripts/check_production_readiness.py` | Expected nonzero exit 1. Reported `local_uat_supported=true`, `internal_alpha_ready=false`, `production_ready=false`, live object-provider evidence `not_run_by_checker`, and blockers including `local_runtime_storage`, `local_artifact_storage`, `object_storage_missing`, `session_business_hardening_incomplete`, `production_deployment_operations_evidence_missing`, `backup_restore_unverified`, `hosted_logging_monitoring_missing`, and `hosted_smoke_evidence_missing`. |
| `python scripts/check_production_readiness.py --with-production-database-evidence` with `SQAG_DATABASE_URL` and `SQAG_LIVE_DATABASE_EVIDENCE=1` supplied by the operator environment | Expected nonzero exit 2. Reported `production_ready=false`, `production_database_evidence.status=passed`, database family `postgres_compatible`, and no DB URL values. In default local storage mode, local runtime/artifact blockers remained, so this command did not credit production readiness. |
| `SQAG_STORAGE_MODE=database SQAG_ARTIFACT_STORAGE_MODE=object python scripts/check_production_readiness.py --with-production-database-evidence` with `SQAG_DATABASE_URL` and `SQAG_LIVE_DATABASE_EVIDENCE=1` supplied by the operator environment | Expected nonzero exit 2. Reported `production_ready=false`, `production_database_evidence.status=passed`, `production_database_evidence_supported=true`, workspace-scoped database app records, object artifact mode, and remaining production blockers including live object provider evidence, object lifecycle evidence, live retention/delete, live DB+object backup/restore, session/business hardening, production deployment operations, backup/restore, hosted logging/monitoring, and hosted smoke. |
| `SQAG_STORAGE_MODE=database SQAG_ARTIFACT_STORAGE_MODE=object SQAG_DATABASE_URL=<synthetic-sqlite-url> python scripts/check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence --with-object-storage-evidence --with-object-artifact-lifecycle-evidence --with-live-object-storage-provider-evidence --backup-restore-work-dir _tmp\validation\s3-sdk-pr-backup --hosted-observability-work-dir _tmp\validation\s3-sdk-pr-observability --hosted-smoke-work-dir _tmp\validation\s3-sdk-pr-smoke --object-storage-work-dir _tmp\validation\s3-sdk-pr-object-contract --object-artifact-lifecycle-work-dir _tmp\validation\s3-sdk-pr-lifecycle` | Expected nonzero exit 2. Reported `internal_alpha_ready=false`, `production_ready=false`, live object-provider evidence `failed/supported=false`, and remaining blockers `sqlite_not_final_production`, `object_storage_provider_unavailable`, `object_retention_delete_live_evidence_missing`, `db_object_backup_restore_live_evidence_missing`, `session_business_hardening_incomplete`, and `production_deployment_operations_evidence_missing`. |
| `SQAG_STORAGE_MODE=database SQAG_ARTIFACT_STORAGE_MODE=object SQAG_DATABASE_URL=<synthetic-sqlite-url> python scripts/check_production_readiness.py --with-object-storage-evidence --with-object-artifact-lifecycle-evidence --with-live-object-storage-provider-evidence` with canonical `SQAG_*` live provider env supplied by the operator environment | Expected nonzero exit 2. Reported `production_ready=false`, live provider evidence `passed/supported=true`, synthetic object contract `passed/supported=true`, synthetic object lifecycle `passed/supported=true`, and remaining blockers `sqlite_not_final_production`, `object_retention_delete_live_evidence_missing`, `db_object_backup_restore_live_evidence_missing`, `session_business_hardening_incomplete`, `production_deployment_operations_evidence_missing`, `backup_restore_unverified`, `hosted_logging_monitoring_missing`, and `hosted_smoke_evidence_missing`. |
| `python -m unittest tests.test_production_readiness tests.test_internal_alpha_hosted_validation_verifier tests.test_database_backup_restore_verifier tests.test_hosted_smoke_verifier tests.test_platform_integration_contract_docs` | Passed: 38 tests OK, covering empty new DB workspace, workspace-owned profile/pricing isolation, wrong-workspace denial, missing workspace profile/pricing/layout fail-closed generation, and DB/BLOB artifact mode readiness denial. |
| `python -m py_compile webapp/server.py scripts/check_production_readiness.py scripts/verify_internal_alpha_hosted_validation.py scripts/verify_database_backup_restore.py scripts/verify_hosted_smoke.py` | Passed. |
| `python scripts/scan_sensitive_fixtures.py` | Passed: 0 blocking, 0 review findings. |
| `python scripts/validate_dynamic_pricing_reference_rules.py` | Passed. |
| `python scripts/validate_local_pdf_dependency_usage.py` | Passed. |
| `python -m pip check` | Passed: `No broken requirements found.` |
| `npm audit --audit-level=high` | Passed: `found 0 vulnerabilities`. |
| `python scripts/check_production_readiness.py` | Expected nonzero exit 1. Reported `local_uat_supported=true`, `internal_alpha_ready=false`, `production_ready=false`, all evidence statuses `not_run_by_checker`, and eight remaining blockers in local mode: `local_runtime_storage`, `local_artifact_storage`, `object_storage_missing`, `session_business_hardening_incomplete`, `production_deployment_operations_evidence_missing`, `backup_restore_unverified`, `hosted_logging_monitoring_missing`, and `hosted_smoke_evidence_missing`. |
| `SQAG_STORAGE_MODE=database SQAG_ARTIFACT_STORAGE_MODE=database SQAG_DATABASE_URL=<synthetic-sqlite-url> python scripts/check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence --backup-restore-work-dir _tmp\validation\readiness-backup-evidence-db --hosted-observability-work-dir _tmp\validation\readiness-observability-evidence --hosted-smoke-work-dir _tmp\validation\readiness-hosted-smoke-evidence` | Expected nonzero exit 2. Reported backup evidence `passed`, hosted observability evidence `passed`, hosted smoke evidence `passed`, object-storage evidence `not_run_by_checker`, `internal_alpha_ready=false`, `production_ready=false`, and `database_blob_artifact_storage_not_launch_ready`; DB/BLOB artifact mode was not credited as hosted/protected/deploy readiness. |
| `SQAG_STORAGE_MODE=database SQAG_ARTIFACT_STORAGE_MODE=object SQAG_DATABASE_URL=<synthetic-sqlite-url> python scripts/check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence --with-object-storage-evidence --with-object-artifact-lifecycle-evidence --backup-restore-work-dir _tmp\validation\readiness-backup-evidence-object --hosted-observability-work-dir _tmp\validation\readiness-observability-object --hosted-smoke-work-dir _tmp\validation\readiness-hosted-smoke-object --object-storage-work-dir _tmp\validation\readiness-object-contract --object-artifact-lifecycle-work-dir _tmp\validation\readiness-object-lifecycle` | Expected nonzero exit 2. Reported workspace-scoped DB app records, object artifact mode, synthetic backup/observability/smoke/object contract/lifecycle evidence `passed`, `internal_alpha_ready=false`, `production_ready=false`, and remaining production blockers focused on non-final SQLite evidence, missing real object-storage provider, live object retention/delete, live DB+object backup/restore, session/business hardening, and production deployment/operations. |
| `python scripts/verify_internal_alpha_hosted_validation.py --work-dir _tmp\validation\hosted-blocked` | Passed. Reported synthetic backup/restore, hosted observability, and hosted smoke evidence `passed`, `target_posture.launch_ready=false`, `internal_alpha_ready=false`, `production_ready=false`, and `database_blob_artifact_storage_not_launch_ready`. |
| `python -m unittest discover -s tests` | Initial sandbox run failed with Windows temp-directory `PermissionError`; escalated rerun passed: 584 tests OK. |

Readiness command note: the nonzero result from `python scripts/check_production_readiness.py` is expected and desired in this audit. The legacy `internal_alpha_ready` field must stay `false`; DB/BLOB artifact mode must also keep `production_ready=false` even when all synthetic evidence flags pass.

Namespace cleanup note: after the SQAG-side env/table/object-metadata/app-key
rename, earlier live evidence rows in this audit are historical/pre-namespace
evidence. They remain useful provenance, but they must not be treated as
post-rename proof. Post-rename live production database evidence, live DB+object
backup/restore evidence, live retention/delete evidence, and live
Platform-to-SQAG hosted smoke remain required. The Swooshz Platform
`appKey=sqag` migration has landed in Platform PR #79; hosted smoke evidence is
still pending. `production_ready=false` remains.

## What Was Not Verified

- Live deployed Swooshz Platform behavior, platform token service, hosted SQAG handoff, or platform workspace membership enforcement; this audit checked source-contract surfaces only, and `scripts/verify_hosted_smoke.py` uses only synthetic platform/workspace context.
- Live OIDC provider behavior.
- Live AI provider privacy posture, data retention, or rate limits.
- Real private Koncept pricing/profile/layout data import.
- Generated customer quote contents.
- Production-grade object-storage readiness beyond historical/pre-namespace live-provider and live DB+object backup/restore verifier passes. Those runs prove synthetic generated XLSX/PDF object behavior and paired DB+object restore behavior against operator-supplied targets before the SQAG namespace cleanup, but post-rename reruns remain required and they do not prove live retention/delete policy, uploaded/reference/profile asset wiring, hosted operations, alert delivery, or final production audit.
- Production-grade live Neon/Postgres-compatible database evidence beyond the historical/pre-namespace opt-in metadata verifier and paired DB+object drill. The DB verifier remains fail-closed by default, connects only under explicit `SQAG_LIVE_DATABASE_EVIDENCE` operator opt-in with DB values supplied outside Git/chat, uses synthetic namespaced metadata rows only, checks schema plus profile/pricing/session/object metadata CRUD, two-workspace isolation, object artifact metadata pairing, and cleanup, does not store generated artifact bytes in DB, does not touch R2/object storage, and reports only sanitized booleans/counts/schema version. Passing post-rename evidence gates can remove only their matching evidence blockers; production readiness still needs live retention/delete, hosted logging/monitoring, hosted smoke, production deployment operations, live Platform-to-SQAG launch smoke, session/business hardening, and final production audit.
- Production rollback/runbook evidence for a real hosted incident remains unproven; the operator-run live DB+object drill proves synthetic backup/restore pairing and isolated restore target behavior, but it is not hosted operations, alert delivery, retention/delete, or final production audit evidence.
- Hosted backup/restore evidence against a real host; PR #95 verifies only synthetic SQLite database/database-artifact drills.
- External hosted observability vendor/export wiring and alert delivery; PR #96 verifies only synthetic structured log and health metadata properties.
- Production deployment/operations evidence and live hosted smoke checks; PR #97 verifies only a synthetic `127.0.0.1` hosted-like path.
- Exhaustive hostile upload corpus beyond existing unit tests and static review.
- Every possible race/deletion edge case in session/artifact lifecycle.

## Self-Review Checklist

- This audit does not claim production readiness.
- Critical/High findings are listed directly in the findings table and release gates.
- Scanner/audit output is metadata-only and privacy-safe.
- No private Koncept data, secrets, generated quote contents, staff emails, or private paths are committed by this audit.
- No product-visible sample/demo/fake flow is reintroduced.
- Remaining implementation blockers have an explicit PR sequence.
