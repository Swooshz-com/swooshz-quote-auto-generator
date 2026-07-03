# SAQG/KQAG Release Security Audit

Audit date: 2026-07-02

Branch: `codex/release-security-audit`

Base evidence ref: `origin/main` at `29de611ee1723ae1e9d1755c32b013efdbc4511e`

## Executive Verdict

SAQG/KQAG remains suitable for local UAT by default. The readiness checker can
now report a narrow synthetic internal-alpha/simple-hosting posture only when
database storage, database artifact storage, backup/restore evidence, hosted
observability evidence, and hosted smoke evidence are all explicitly enabled and
verified.

| Gate | Verdict | Reason |
| --- | --- | --- |
| `local_uat_supported` | Yes | Local localhost mode, local runtime storage, seeded test setup, and current CI remain supported. |
| `internal_alpha_ready` | Conditional | Default local mode remains blocked. Under `KQAG_STORAGE_MODE=database`, `KQAG_ARTIFACT_STORAGE_MODE=database`, and explicit passing backup/restore, hosted observability, and hosted smoke evidence flags, the checker may report `internal_alpha_ready=true` for the temporary SQLite/DB-artifact simple-hosting exception only. |
| `production_ready` | No | The object-storage contract now has synthetic evidence, but real provider wiring, DB+object backup/restore, retention/delete evidence, and production deployment/operations evidence are not complete. |

This PR does not claim production readiness. It adds a provider-neutral
object-storage artifact contract and synthetic evidence path, keeps runtime
object mode fail-closed until a real backend is configured, and keeps
production blocked.

Highest-priority remaining blockers:

- Medium: database artifact storage remains only a temporary internal-alpha/simple-hosting exception when synthetic backup/restore, hosted observability, and hosted smoke verifiers pass; production still requires real object-storage provider wiring and production operations evidence.
- Medium: docs and runbooks still include local/deploy helper paths that must be rewritten before operators treat them as the real happy path.

Load Sample status: product-visible Load Sample UI/API/JS paths are gone after PR #86. No Load Sample button, product API, or Playwright smoke dependency is part of the sellable path. Remaining sample/Kent references are test-only or historical audit references.

PR #88 pricing-reference isolation update: database/platform pricing-reference
list/detail/export/generation now resolve only workspace-owned database rows.
Local and bundled pricing packs remain local-UAT-only behavior, not a hosted or
internal-alpha fallback.

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

Local artifact storage policy update: protected generate paths now block before
creating or returning local `QUOTE_OUTPUT_ROOT` quote artifacts when database
artifact storage is not enabled. Protected profile layout uploads and pricing
visual uploads also block before local filesystem artifact writes. Database
artifact mode remains a documented temporary internal-alpha/simple-hosting
exception only after backup/restore, hosted observability, and hosted smoke
evidence; it is not final production object storage.

Database backup/restore evidence update: `scripts/verify_database_backup_restore.py`
now performs a synthetic SQLite drill for the temporary database +
database-artifact internal-alpha option. It applies the reviewed migrations,
seeds synthetic workspace/profile/pricing/session/artifact rows, backs up and
restores database rows plus BLOB artifacts together, compares row-count and
checksum metadata, verifies workspace/session ownership metadata survives, and
proves rollback to a prior known-good synthetic state. The output is
metadata-only and omits DB URLs, local paths, artifact bytes, generated quote
contents, customer details, pricing/profile payloads, staff emails, OAuth
values, cookies, tokens, and API keys. This is not production object storage,
external hosted logging wiring, or hosted smoke evidence.

Hosted observability evidence update: `scripts/verify_hosted_observability.py`
now performs a synthetic structured-logging and health/readiness drill. It
checks event allowlisting, metadata-only log records, support-traceable error
references, sensitive-value omission, the machine-readable policy in
`docs/hosted-observability-policy.json`, and path-free health metadata. The
output is metadata-only and omits DB URLs, local paths, artifact bytes,
generated quote contents, customer details, pricing/profile payloads, staff
emails, OAuth values, cookies, tokens, API keys, and raw provider responses.
This is internal-alpha evidence only; external vendor wiring, alert delivery,
hosted smoke checks, object storage, and production readiness remain separate.

Hosted smoke evidence update: `scripts/verify_hosted_smoke.py` now performs a
synthetic hosted-like internal-alpha smoke drill on `127.0.0.1` only. It runs
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
storage, or claim production readiness.

Object-storage contract evidence update: PR #98 added `webapp/object_storage.py`,
a provider-neutral artifact backend contract for generated quote XLSX/PDF
artifacts, uploaded references, profile layout assets, and pricing visual
assets. The contract requires workspace-scoped owner metadata, content type,
byte size, SHA-256 checksum, timestamps, retrieve/delete operations, and
workspace authorization checks. `scripts/verify_object_storage_contract.py`
exercises this contract with a synthetic in-memory backend only; it does not
configure AWS, GCP, Azure, R2, MinIO, S3-compatible endpoints, or credentials.
When `KQAG_ARTIFACT_STORAGE_MODE=object` is selected at runtime, KQAG fails
closed with the generic artifact-storage-unavailable message until a usable
provider backend is available.

Object-storage provider configuration update: this PR adds strict
metadata-only configuration validation for the production object-storage
provider scaffold. The recognized object provider setting is
`KQAG_OBJECT_STORAGE_PROVIDER`; unset, `disabled`, `none`, `off`, `false`, or
`0` mean disabled. `s3_compatible` is the scaffolded production-provider
family and requires these environment names to be present:
`KQAG_OBJECT_STORAGE_ENDPOINT_URL`, `KQAG_OBJECT_STORAGE_BUCKET`,
`KQAG_OBJECT_STORAGE_REGION`, `KQAG_OBJECT_STORAGE_ACCESS_KEY_ID`, and
`KQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY`. The checker reports only provider
type, required field names, missing field names, adapter/scaffold status, and
runtime availability booleans; it never prints endpoint URLs, bucket values,
access keys, secret keys, object keys, DB URLs, paths, artifact bytes, quote
contents, or customer data. A `synthetic` provider is accepted only as
test/verifier metadata and is never production-credited. The S3-compatible
adapter remains a fail-closed scaffold in this PR, so production still requires
a credentialed runtime implementation, DB object metadata integration,
DB+object backup/restore, retention/delete evidence, and deployment/operations
evidence before readiness can be claimed.

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
| `/api/settings/profiles/{id}/export.json` | GET | Required in deploy | DB row export in DB mode; local helper can export pack assets in local mode | N/A | `canManageProfiles` | N/A | Profile defaults/layout assets | Medium/high depending mode: profile asset export needs object-storage ownership before internal alpha. |
| `/api/settings/profiles` | POST | Required in deploy | Saves DB row by workspace in DB mode | N/A | `canManageProfiles` | Yes | Profile defaults/layout assets | Save is scoped; DB generation consumes stored profile layout artifacts after PR #89. |
| `/api/settings/profiles/{id}` | DELETE | Required in deploy | Deletes DB row by workspace in DB mode | N/A | `canManageProfiles` | Yes | Profile deletion | DB delete is workspace-scoped; confirm artifact cleanup in follow-up. |
| `/api/quote-sessions` | GET | Required in deploy | DB rows keyed by workspace | Owner visibility in DB mode | Any authenticated user | N/A | Quote session summaries | Protected modes block local runtime quote-session listing when database storage is unavailable; local mode remains local-UAT only. |
| `/api/quote-sessions/{id}` | GET | Required in deploy | DB row keyed by workspace | Owner/admin visibility in DB mode | Any authenticated user | N/A | Draft state, filenames, session metadata | Protected modes block local runtime quote-session detail reads when database storage is unavailable; local mode remains local-UAT only. |
| `/api/quote-sessions/{id}` | DELETE | Required in deploy | DB row keyed by workspace | Owner editable in DB mode | `canGenerateQuote` | Yes | Session deletion | Protected modes block local runtime quote-session deletion when database storage is unavailable; artifact cleanup/retention still needs follow-up. |
| `/api/quote-sessions/{id}/download/{kind}` | GET | Required in deploy | DB artifact keyed by workspace/session in DB artifact mode | Owner/admin visibility through metadata lookup | Any authenticated user | N/A | Generated XLSX/PDF | Protected modes block local runtime quote-session downloads when database storage is unavailable. Production still needs object storage and retention. |
| `/api/jobs` | POST | Required in deploy | Auth session passed to worker | Job owner context stored for protected modes | Any authenticated user; generation checks later | Yes | Async draft/generate jobs | PR #91 stores privacy-safe owner/workspace context for hosted job visibility. |
| `/api/jobs/{job}` | GET | Required in deploy | Owner workspace in protected modes | Creating platform user/workspace in protected modes | Any authenticated user | N/A | Job status/result/files | PR #91 blocks cross-user/cross-workspace job status/result reads in hosted/database/platform/deploy mode. |
| `/api/jobs/{job}/files/{filename}` | GET | Required in deploy | Disabled in protected modes | Disabled in protected modes | Any authenticated user | N/A | Generated XLSX/PDF direct file | PR #91 disables legacy output-root downloads in deploy/database/platform/database-artifact mode; local-UAT local mode remains supported. |
| `/api/line-items/normalize` | POST | Required in deploy | Uses selected pricing path in payload; DB mode attaches workspace-owned pricing detail | N/A | `canGenerateQuote` | Yes | Quote basis/line item normalization | Pricing fallback is blocked in DB mode after PR #88. |
| `/api/draft` | POST | Required in deploy | Payload/state only | N/A | Any authenticated user today | Yes | Uploaded images/PDFs, quote details, AI draft | AI draft fallback now fails closed in protected modes; role expectations still need internal-alpha review. |
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
| DB profiles | `kqag_profiles` primary key includes `(workspace_id, profile_id)` and DB profile reads use the current workspace. PR #89 makes DB profile lists workspace-row-only and generation consume workspace DB profile defaults/layout artifacts. | Object storage is still missing for final production profile asset storage. | Medium | Keep regression coverage; production object-storage blocker remains. |
| DB pricing references | `kqag_pricing_references` primary key includes `(workspace_id, reference_id)`; PR #88 list/detail/export/generation read only current-workspace DB rows. | Uploaded/reference assets still need final object-storage productionization. | Medium | Keep regression tests; production object-storage blocker remains. |
| DB quote sessions | `kqag_quote_sessions` key includes `(workspace_id, session_id)` (`webapp/server.py:6823`), reads include workspace predicate (`webapp/server.py:7437`). | Protected modes block local runtime quote sessions when DB storage is unavailable. Admin can view other-user DB sessions only under visibility conditions (`webapp/server.py:7405`). | Medium | Keep protected-mode local-runtime block and DB owner tests green; add hosted policy before internal alpha. |
| DB artifacts | `kqag_quote_artifacts` and `kqag_file_artifacts` keys include `workspace_id`. PR #89 uses stored profile layout artifacts for DB-mode generation. | This is SQLite/BLOB mode, not final object storage. | Medium/High | Internal alpha temporary exception only when `scripts/verify_database_backup_restore.py` passes and hosted smoke/logging gates are satisfied; production still requires real object-storage provider wiring. |
| Object-storage contract | `webapp/object_storage.py` defines workspace-scoped object metadata, checksums, retrieve/delete, and wrong-workspace denial. | Synthetic in-memory evidence only; no live cloud/object provider is wired. Runtime `object` mode fails closed. | Medium | Use `scripts/verify_object_storage_contract.py` as provider-neutral evidence; add a real provider adapter, DB metadata integration, DB+object backup/restore, and retention/delete evidence before production. |
| Local profile/pricing/session roots | Local roots are shared by process and company/default identifiers. | Not tenant-isolated. | High in hosted mode | Allowed only for local UAT/test harness. |
| Platform session | `safe_platform_session_context()` requires consumed outcome, user id, workspace id, app key, and supported role (`webapp/server.py:6944`). | Live platform contract not verified in this repo; Swooshz Platform repo out of scope. | Medium | Verify in platform integration PR. |

Cross-workspace leak paths:

- Pricing references: PR #88 blocks DB/platform list, detail, export, and generation fallback to local/bundled pricing packs.
- Profile layout/defaults: PR #89 blocks database-mode generation unless the selected workspace DB profile row and DB layout artifact are present.
- Legacy job files: PR #91 disables direct output-root file downloads in hosted/database/platform/deploy paths. Local-UAT local storage keeps the route as a localhost workflow convenience.

## Artifact Lifecycle And Security Matrix

| Artifact path | Storage | Download route | Controls present | Gap | Severity |
| --- | --- | --- | --- | --- | --- |
| Async job output | `QUOTE_OUTPUT_ROOT/{job_id}` | `/api/jobs/{job}/files/{filename}` | Safe filename allowlist and output-root containment; PR #91 disables the route in hosted/database/platform/deploy paths. PR #94 blocks protected generate paths before local output artifacts are created or returned when artifact mode is local; object artifact mode now also fails closed until a real provider adapter exists. | Local-UAT local mode still uses this convenience route; hosted/internal-alpha paths should use database artifact mode until object provider wiring exists. | Low in local-UAT only |
| Quote-session XLSX/PDF in local mode | `QUOTE_DATA_ROOT/quote-sessions/{session}/exports` | `/api/quote-sessions/{id}/download/{kind}` | Safe session id, expected filename, stale checks (`webapp/server.py:12963`), and PR #93 blocks protected local quote-session routes when database storage is unavailable. | Local mode has no tenant boundary and remains local-UAT only. | Medium |
| Quote-session XLSX/PDF in DB artifact mode | `kqag_quote_artifacts` | `/api/quote-sessions/{id}/download/{kind}` | Workspace/session/artifact-kind query and owner visibility through session metadata (`webapp/server.py:7355`). Synthetic SQLite backup/restore/rollback verification now covers DB rows and BLOB artifacts together. | SQLite/BLOB is not final object storage; hosted smoke and production object-storage backup/restore are not proven. | Medium/High |
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
- `/api/draft` and `/api/generate` do not currently require an explicit generate permission at every path. `/api/line-items/normalize` and quote-session save do, but draft/generate should be reviewed for internal alpha role expectations.
- Local-UAT job status/download behavior remains local-only; PR #91 owner-binds job status and disables legacy file downloads in hosted/database/platform/deploy modes.
- Platform-owned auth/workspace verification against the Platform repo was not performed because the Swooshz Platform repo is out of scope.

## Fallback/Fail-Open Audit

Confirmed Load Sample posture:

- Product code no longer exposes `/api/samples`, `DEFAULT_SAMPLE_ID`, `setSampleDetails`, or the Load Sample button.
- Remaining Load Sample/sample/Kent references are in tests, scanner terms, or historical audit docs.

Disallowed in database/platform/deploy/internal-alpha/production mode:

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
| Local quote artifact storage | Resolved in PR #94 for protected generate and artifact-upload paths | Protected modes now return a generic failed response before creating or returning local `QUOTE_OUTPUT_ROOT` quote artifacts, profile layout uploads, or pricing visual uploads when database artifact storage is unavailable. Database artifact mode remains a temporary internal-alpha/simple-hosting exception only, not production object storage. | Resolved High for protected local artifact success path |
| Job/session summary local pack fallback | `webapp/server.py:12474`, `12491` | Display names can resolve through local pack loaders. Lower data impact, but still wrong product shape. | Medium |
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

| OWASP-style category | KQAG evidence | Release verdict |
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
| Deleted sessions/artifacts restored/exported/downloaded | DB session delete removes metadata but artifact cleanup/retention is not fully verified; legacy job files may remain under output root but are not downloadable through the legacy route in protected modes after PR #91. | Medium | Add artifact lifecycle cleanup/authorization tests and object-storage metadata. |
| Edited past session leaks current/default settings | Session summary fallback can recalculate display names/defaults from current local packs; generated artifact stale logic exists but not a full historical snapshot model. | Medium | Store immutable profile/pricing snapshot metadata with session/artifacts. |
| Cross-user dashboard visibility | DB mode owner visibility exists; ownerless sessions remain visible. | Medium | Define migration/owner policy before internal alpha. |
| Race/deletion/export edge cases | Not deeply exercised in this audit. | Medium | Add focused tests for delete-while-download, stale artifacts, and deleted references. |

## Findings Table

| Severity | Finding | Evidence | Impact | Required fix |
| --- | --- | --- | --- | --- |
| Critical | None confirmed in the audited source and metadata-only scans. | N/A | N/A | Keep Critical gate open for any confirmed cross-workspace private data leak, unauthorized artifact byte access, production auth bypass, committed secret, or arbitrary file read/write. |
| High, resolved in PR #88 | DB/platform pricing references could include shared local/bundled packs. | Regression coverage in `tests/test_webapp.py` | Workspace users can no longer list/detail/export/generate from non-owned local/bundled references in DB mode. | Keep DB pricing isolation tests in the release gate. |
| High, resolved in PR #89 | Profile/layout generation still resolved local/default profile packs. | Regression coverage in `tests/test_webapp.py` | Missing workspace profile assets now block DB/platform generation. | Keep DB profile/layout isolation tests in the release gate; object storage remains separate. |
| High, resolved in PR #91 | Legacy job-file downloads were not workspace/session-owner bound. | Regression coverage in `tests/test_webapp.py` | Hosted/database/platform/deploy mode now returns a generic not-found response instead of legacy output-root bytes; local-UAT local mode remains supported. | Prefer quote-session artifact downloads and add object storage before production. |
| High, resolved in PR #92 | Local AI draft fallback returned product-visible drafted result when remote AI was missing/failed. | Regression coverage in `tests/test_webapp.py` | Protected-mode users now receive a blocked draft result with a generic message, and no local starter draft/result is returned. | Keep protected-mode fail-closed draft tests in the release gate; local-UAT fallback remains local only. |
| High, resolved in PR #93 | Local quote-session runtime storage could be product-visible in protected modes when database storage was unavailable. | Regression coverage in `tests/test_webapp.py` | Protected-mode quote-session routes and generate-session persistence now return a generic storage-unavailable response instead of using local runtime session files. | Keep protected-mode quote-session storage tests in the release gate; local-UAT fallback remains local only. |
| High, resolved in PR #94 | Local artifact storage could be product-visible in protected generate and artifact-upload paths when database artifact storage was unavailable. | Regression coverage in `tests/test_webapp.py` | Protected-mode generate paths, profile layout uploads, and pricing visual uploads now fail with a generic artifact-storage-unavailable response before local output or upload artifact files are created or returned. | Keep protected-mode artifact storage tests green; database artifact mode is still only a temporary exception, and production object storage remains required. |
| Medium, evidence path added in PR #95 | DB/DB-artifact backup, restore, retention, and rollback had no safe verifier. | `scripts/verify_database_backup_restore.py`, `docs/internal-alpha-retention-policy.json`, `tests/test_database_backup_restore_verifier.py` | Synthetic SQLite rows and BLOB artifacts can now be backed up, restored, checksum-verified, and rolled back together without private data. | This is temporary internal-alpha/simple-hosting evidence only; production still requires object storage and hosted operations evidence. |
| Medium, evidence path added in PR #96 | Hosted logging/monitoring evidence had no safe verifier. | `scripts/verify_hosted_observability.py`, `docs/hosted-observability-policy.json`, `tests/test_hosted_observability_verifier.py` | Synthetic structured logs, event categories, support error references, and health metadata can now be checked without private data or an external vendor dependency. | This is internal-alpha evidence only; alert delivery, vendor/export wiring, and production object storage remain separate. |
| Medium, evidence path added in PR #97 | Hosted smoke evidence had no safe verifier. | `scripts/verify_hosted_smoke.py`, `tests/test_hosted_smoke_verifier.py` | Synthetic deploy/database/database-artifact smoke coverage now verifies platform launch, auth gate, workspace profile/pricing use, quote generation, session persistence, XLSX/PDF artifact download, delete, logout, and legacy direct job-file lockdown without private data or live Platform dependency. | This is internal-alpha/simple-hosting evidence only; live Swooshz Platform integration, object storage, and production deployment/operations evidence remain separate. |
| Medium, evidence path added in PR #98 | Object-storage artifact contract was missing. | `webapp/object_storage.py`, `scripts/verify_object_storage_contract.py`, `tests/test_object_storage_contract_verifier.py` | Synthetic in-memory contract evidence now covers store/retrieve/delete, checksum verification, workspace metadata enforcement, wrong-workspace denial, and metadata-only output for generated quote artifacts, uploaded references, profile layouts, and pricing visuals. Runtime object mode fails closed because no real provider adapter is wired. | This is provider-neutral contract evidence only; production still requires real object-storage provider wiring, DB+object backup/restore, retention/delete evidence, and deployment/operations evidence. |
| Medium, scaffold added in this PR | Real object-storage provider configuration was not validated. | `webapp/object_storage.py`, `webapp/server.py`, `tests/test_object_storage_provider_config.py`, `tests/test_production_readiness.py`, `tests/test_webapp.py` | Object mode now reports disabled, S3-compatible, synthetic, and unsupported provider status as metadata only. Missing S3-compatible config is listed by environment variable name only, and the scaffold remains fail-closed until a real credentialed backend is implemented. | This is provider-config validation and adapter scaffold only; production still requires runtime provider implementation, DB object-key/checksum metadata, DB+object backup/restore, retention/delete evidence, and production operations evidence. |
| Medium, resolved in PR #91 | Async job status/result was random-ID gated, not owner-bound. | Regression coverage in `tests/test_webapp.py` | Hosted/database/platform/deploy job status/result reads require the creating platform user/workspace. | Keep job owner visibility tests in the release gate. |
| Medium | Import/upload validation is good but hostile-corpus evidence is incomplete. | `webapp/server.py:3713`, `4728`, `6322`, `8422` | Malformed XLSX/PDF/image edge cases could cause parser failure or resource pressure. | Add synthetic hostile upload fixtures and regression tests. |
| Medium | Hosted alert delivery and production observability wiring are not productionized. | `webapp/server.py:1182`, docs | Synthetic evidence proves local schema/privacy properties, but not a host/vended log pipeline. | Add host-specific export/alert wiring before treating this as production observability. |
| Medium | Supply-chain evidence is incomplete. | `.github/workflows/ci.yml`, `package.json` | CI has useful gates but no CodeQL/Python audit and unpinned Gitleaks image. | Add CodeQL/dependency review or documented equivalent before production. |
| Low | Historical docs still mention Load Sample/sample paths as audit evidence. | `docs/architecture-dead-code-fallback-audit.md`, `docs/production-readiness-audit.md` | Could confuse future readers if not superseded by this doc. | Link this audit as the current release gate and trim historical wording later. |

## Internal-Alpha Release Gate

Do not start internal alpha until all are true:

- No product-visible Load Sample/sample/demo/fake seeded path exists.
- Database/platform mode cannot list, detail, export, delete, or generate from local/bundled private-like pricing references. PR #88 satisfies this pricing-reference gate; keep it covered by regression tests.
- Generation resolves profile defaults and layout workbook from workspace-owned profile assets. PR #89 satisfies this gate for DB/platform mode; keep it covered by regression tests.
- New workspaces have no real Koncept Images profile/pricing/layout pack by default; Koncept packs become available only after explicit import/seed into the intended workspace.
- Legacy `/api/jobs/{job}/files/{filename}` is disabled in hosted modes or authorized by workspace/session ownership. PR #91 satisfies this by disabling the route in deploy/database/platform/database-artifact modes.
- `/api/draft` does not return local fallback success in internal-alpha/protected modes; PR #92 satisfies that gate with protected-mode regression coverage.
- Quote-session routes do not use local runtime storage in internal-alpha/protected modes; PR #93 satisfies that local-runtime fail-closed gate.
- Protected generate paths and artifact-bearing settings uploads do not create or return local quote/profile/pricing artifacts when database artifact storage is unavailable; PR #94 satisfies that protected local-artifact fail-closed gate.
- Quote sessions and artifacts must use the chosen workspace-owned durable storage before internal alpha.
- Backup/restore/rollback is documented and tested for the temporary SQLite DB/DB-artifact internal-alpha option by `scripts/verify_database_backup_restore.py`; run it for each internal-alpha evidence bundle and keep the output metadata-only.
- Hosted smoke covers platform launch, workspace profile save/use, pricing save/use, quote generation, session persistence, authorized XLSX/PDF artifact download, delete, logout, and legacy direct job-file lockdown; `scripts/verify_hosted_smoke.py` satisfies this synthetic evidence gate.
- Logs remain privacy-minimized and support-traceable without raw prompts, uploads, provider responses, secrets, or generated quote contents; `scripts/verify_hosted_observability.py` satisfies this synthetic evidence gate.
- Object-storage contract/provider evidence is not required for the temporary DB/DB-artifact internal-alpha exception; if `KQAG_ARTIFACT_STORAGE_MODE=object` is selected, runtime paths fail closed until a real provider backend is implemented and production evidence is complete.
- Codex Security standard scan is complete or any incomplete status is explicitly disclosed.

## Production Release Gate

Do not claim production readiness until all internal-alpha gates plus these are true:

- A real object-storage provider is wired for generated XLSX/PDF, uploaded images/PDFs, profile layout assets, and pricing visual assets.
- Object provider configuration is present, validated, non-secret in diagnostics, and backed by a usable runtime adapter rather than the current fail-closed scaffold.
- DB rows store object keys, checksums, byte sizes, content types, owner/workspace/session metadata, retention state, and audit metadata.
- Downloads stream or sign objects only after workspace/session/owner authorization.
- Backup and restore drills prove DB and object storage recover together.
- Retention/deletion policies are implemented and tested.
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
7. DB/DB-artifact backup evidence: completed in PR #95 for the temporary SQLite internal-alpha exception with synthetic backup, restore, checksum, retention-policy, and rollback verification.
8. Hosted observability evidence: completed in PR #96 for synthetic privacy-minimized structured logs, support references, event categories, and health metadata.
9. Hosted smoke evidence: completed in PR #97 for synthetic deploy/database/database-artifact smoke coverage on `127.0.0.1`; live Platform verification remains separate.
10. Artifact object-storage contract: completed in PR #98 as provider-neutral contract and synthetic in-memory evidence only; runtime object mode fails closed without a real provider adapter.
11. Object-storage provider configuration validation: completed in this PR as S3-compatible provider env-name validation and a fail-closed adapter scaffold; no credentials or live provider wiring are added.
12. Real object-storage provider integration: wire generated outputs and uploaded/reference/profile assets to a credentialed object store with DB metadata and checksums.
13. Session and business-logic hardening: immutable profile/pricing snapshots, stale/deleted artifact tests, delete/export race tests.
14. Hosted production operations: host-specific logging export, alert delivery, DB+object backup/restore/rollback runbooks, production deployment evidence, and live host smoke evidence.
15. Supply-chain hardening: CodeQL/equivalent, Python dependency audit, pinned security scanner image, branch protection docs.
16. Platform integration audit: verify launch/auth/workspace claims against the Swooshz Platform repo in a separate PR.

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
| `python -m py_compile webapp/server.py webapp/object_storage.py scripts/generate_quote.py scripts/audit_architecture_fallbacks.py scripts/check_production_readiness.py scripts/verify_database_backup_restore.py scripts/verify_hosted_observability.py scripts/verify_hosted_smoke.py scripts/verify_object_storage_contract.py` | Passed. |
| `python scripts/verify_database_backup_restore.py --work-dir _tmp\validation\backup-restore-evidence` | Passed. Reported `status=passed`, `synthetic_only=true`, row counts/checksums matched, workspace ownership was preserved, rollback restored a prior known-good state, retention policy covered required data classes, and output omitted paths, DB URLs, artifact bytes, and payloads. |
| `python scripts/verify_hosted_observability.py --work-dir _tmp\validation\hosted-observability-evidence` | Passed. Reported `status=passed`, `synthetic_only=true`, structured log records checked, allowed events enforced, sensitive values omitted, support error reference present, health metadata path-free, and output omitted paths, DB URLs, artifact bytes, payloads, provider responses, staff emails, and tokens. |
| `python scripts/verify_hosted_smoke.py --work-dir _tmp\validation\hosted-smoke-evidence` | Passed. Reported `status=passed`, `synthetic_only=true`, `network.host=127.0.0.1`, database/database-artifact mode, health/auth/platform/profile/pricing/generate/session/download/delete/logout/legacy-lockdown checks true, XLSX/PDF authorized downloads, and no local quote-session or local artifact success path. |
| `python scripts/verify_object_storage_contract.py --work-dir _tmp\validation\object-storage-contract` | Passed. Reported `status=passed`, `synthetic_only=true`, backend `synthetic-in-memory`, generated quote/uploaded reference/profile layout/pricing visual artifact classes covered, store/retrieve/delete, checksum, workspace metadata, wrong-workspace denial, and metadata-only output without object keys or artifact bytes. |
| `python -m unittest tests.test_architecture_fallback_audit` | Passed: 3 tests OK. |
| `python -m unittest tests.test_production_readiness` | Passed: 18 tests OK. |
| `python -m unittest tests.test_database_backup_restore_verifier` | Passed: 6 tests OK. |
| `python -m unittest tests.test_hosted_observability_verifier` | Passed: 4 tests OK. |
| `python -m unittest tests.test_hosted_smoke_verifier` | Passed: 3 tests OK. |
| `python -m unittest tests.test_object_storage_contract_verifier` | Passed: 3 tests OK. |
| `python -m unittest tests.test_object_storage_provider_config` | Passed: 4 tests OK. |
| `python -m unittest -k database_storage tests.test_webapp.WebappServerTest` | Passed on escalated rerun: 7 tests OK, including new-workspace no-default-Koncept evidence and workspace-scoped profile layout artifact isolation. |
| `python -m unittest -k database_pricing tests.test_webapp.WebappServerTest` | Passed on escalated rerun: 2 tests OK. |
| `python -m unittest -k database_profile tests.test_webapp.WebappServerTest` | Passed on escalated rerun: 1 test OK. |
| `python -m unittest -k legacy_job tests.test_webapp.WebappServerTest` | Passed on escalated rerun: 2 tests OK. |
| `python -m unittest -k protected_draft tests.test_webapp.WebappServerTest` | Passed on escalated rerun: 4 tests OK. |
| `python -m unittest -k quote_session tests.test_webapp.WebappServerTest` | Passed on escalated rerun: 11 tests OK. |
| `python -m unittest -k local_artifact tests.test_webapp.WebappServerTest` | Passed on escalated rerun: 4 tests OK. |
| `python -m unittest -k database_artifact tests.test_webapp.WebappServerTest` | Passed on escalated rerun: 7 tests OK. |
| `python -m unittest discover -s tests` | Passed on escalated rerun: 542 tests OK. |
| `python scripts/audit_architecture_fallbacks.py --max-hits-per-pattern 1 --max-possible-unused 5` | Passed; metadata-only scanner output recorded above. |
| `python scripts/check_production_readiness.py` | Expected nonzero exit 2. Reported `local_uat_supported=true`, `internal_alpha_ready=false`, `production_ready=false`, all evidence statuses `not_run_by_checker`, and seven remaining blockers in local mode: `local_runtime_storage`, `local_artifact_storage`, `object_storage_missing`, `production_deployment_operations_evidence_missing`, `backup_restore_unverified`, `hosted_logging_monitoring_missing`, and `hosted_smoke_evidence_missing`. |
| `KQAG_STORAGE_MODE=database KQAG_ARTIFACT_STORAGE_MODE=database KQAG_DATABASE_URL=<synthetic-sqlite-url> python scripts/check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence --backup-restore-work-dir _tmp\validation\readiness-backup-evidence-db --hosted-observability-work-dir _tmp\validation\readiness-observability-evidence --hosted-smoke-work-dir _tmp\validation\readiness-hosted-smoke-evidence` | Expected nonzero exit 2. Reported backup evidence `passed`, hosted observability evidence `passed`, hosted smoke evidence `passed`, object-storage evidence `not_run_by_checker`, `internal_alpha_ready=true` for the synthetic DB/DB-artifact internal-alpha/simple-hosting posture, and `production_ready=false` with SQLite-not-final, object storage, and production deployment/operations evidence still blocking production. |
| `KQAG_STORAGE_MODE=database KQAG_ARTIFACT_STORAGE_MODE=database KQAG_DATABASE_URL=<synthetic-sqlite-url> python scripts/check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence --with-object-storage-evidence --backup-restore-work-dir _tmp\validation\readiness-backup-evidence-db-object-flag --hosted-observability-work-dir _tmp\validation\readiness-observability-evidence-object-flag --hosted-smoke-work-dir _tmp\validation\readiness-hosted-smoke-evidence-object-flag --object-storage-work-dir _tmp\validation\readiness-object-storage-contract-db-mode` | Expected nonzero exit 2. Reported backup/observability/smoke/object evidence `passed`, `internal_alpha_ready=true` for the prior DB/DB-artifact posture, and `production_ready=false`; object contract support was not credited as production object storage because artifact mode was `database`, so SQLite-not-final, object storage, and production deployment/operations remained production blockers. |
| `KQAG_STORAGE_MODE=database KQAG_ARTIFACT_STORAGE_MODE=object KQAG_DATABASE_URL=<synthetic-sqlite-url> python scripts/check_production_readiness.py --with-object-storage-evidence --object-storage-work-dir _tmp\validation\readiness-object-provider-contract` | Expected nonzero exit 2. Reported object-storage contract evidence `passed`, `object_storage_provider.provider=disabled`, `object_storage_provider.production_provider_ready=false`, `object_storage_provider_unavailable`, and `production_ready=false`. |
| `KQAG_STORAGE_MODE=database KQAG_ARTIFACT_STORAGE_MODE=object KQAG_DATABASE_URL=<synthetic-sqlite-url> KQAG_OBJECT_STORAGE_PROVIDER=s3_compatible KQAG_OBJECT_STORAGE_ENDPOINT_URL=<redacted> KQAG_OBJECT_STORAGE_BUCKET=<redacted> KQAG_OBJECT_STORAGE_REGION=<redacted> KQAG_OBJECT_STORAGE_ACCESS_KEY_ID=<redacted> KQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY=<redacted> python scripts/check_production_readiness.py --with-object-storage-evidence --object-storage-work-dir _tmp\validation\readiness-object-provider-s3-config` | Expected nonzero exit 2. Reported provider `s3_compatible`, required field names present, no missing fields, adapter `s3_compatible_scaffold`, `runtime_backend_available=false`, `production_provider_ready=false`, `object_storage_provider_unavailable`, and `production_ready=false`; output omitted provider values/secrets. |
| `python scripts/scan_sensitive_fixtures.py` | Passed: 0 blocking, 0 review findings. |
| `python scripts/validate_local_pdf_dependency_usage.py` | Passed. |
| `python scripts/validate_dynamic_pricing_reference_rules.py` | Passed. |
| `node --check webapp/static/app.js` | Passed. |
| `node --check scripts/playwright-smoke.mjs` | Passed. |
| `node --check scripts/playwright-ai-basis-chat-stress.mjs` | Passed. |
| `Invoke-WebRequest http://127.0.0.1:8765/api/health` | Passed after starting the local webapp server with `scripts/start-webapp.ps1`: HTTP 200. |
| `npm run playwright:smoke -- --port 8766` | Passed with `status=ok` on isolated local test server. The plain `npm run playwright:smoke` reused the already-running health-check server on port 8765 and hit a stale dashboard/localStorage assertion, so the isolated-port run is the recorded smoke evidence. |
| `npm run playwright:ai-stress -- --port 8767` | Passed with `status=ok` on isolated local test server. |
| `npm audit` | Passed: 0 vulnerabilities. |
| `git diff --check` | Passed with line-ending warnings only. |

Readiness command note: the nonzero result from the default `python scripts/check_production_readiness.py` is expected and desired in this audit. The all-evidence command may report conditional internal-alpha readiness for the synthetic DB/DB-artifact simple-hosting posture, while production remains blocked.

## What Was Not Verified

- Live Swooshz Platform repo behavior, platform token service, or platform workspace membership enforcement; `scripts/verify_hosted_smoke.py` uses only synthetic platform/workspace context.
- Live OIDC provider behavior.
- Live AI provider privacy posture, data retention, or rate limits.
- Real private Koncept pricing/profile/layout data import.
- Generated customer quote contents.
- Real object-storage provider runtime wiring, because this PR adds only S3-compatible configuration validation and a fail-closed adapter scaffold.
- Production backup/restore/rollback for DB+object storage, because no real provider is wired.
- Hosted backup/restore evidence against a real internal-alpha host; PR #95 verifies only synthetic SQLite database/database-artifact drills.
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
