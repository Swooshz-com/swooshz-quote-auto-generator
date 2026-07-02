# SAQG/KQAG Release Security Audit

Audit date: 2026-07-02

Branch: `codex/release-security-audit`

Base evidence ref: `origin/main` at `29de611ee1723ae1e9d1755c32b013efdbc4511e`

## Executive Verdict

SAQG/KQAG remains suitable for local UAT only:

| Gate | Verdict | Reason |
| --- | --- | --- |
| `local_uat_supported` | Yes | Local localhost mode, local runtime storage, seeded test setup, and current CI remain supported. |
| `internal_alpha_ready` | No | Workspace-scoped auth/session work exists, but profile/pricing/artifact behavior can still fall back to local or globally shared resources. |
| `production_ready` | No | Object storage, backup/restore evidence, workspace-owned profile assets, strict pricing isolation, and legacy artifact download authorization are not complete. |

This PR does not claim production readiness. It is a release-grade security audit and release-gate review for the implementation PRs that must follow.

Highest-priority blockers:

- High: database/platform pricing-reference list/detail/generation can still use local or bundled references outside the workspace boundary.
- High: generation-time profile/layout resolution still uses local profile pack fallback instead of workspace-owned DB/object artifacts.
- High: legacy `/api/jobs/{job}/files/{filename}` downloads are not bound to workspace, session, or job owner.
- High: local AI draft fallback can create product-visible "drafted" success when remote AI is missing or failed; this must be disallowed in internal-alpha/production mode.
- Medium: local quote-session storage and job status are local-UAT-only and not an internal-alpha ownership model.
- Medium: docs and runbooks still include local/deploy helper paths that must be rewritten before operators treat them as the real happy path.

Load Sample status: product-visible Load Sample UI/API/JS paths are gone after PR #86. No Load Sample button, product API, or Playwright smoke dependency is part of the sellable path. Remaining sample/Kent references are test-only or historical audit references.

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
| `/api/health` | GET | Public | N/A | N/A | None | N/A | Low, exposes generator path string | Acceptable for local/readiness, but should avoid local path detail in hosted production. |
| `/api/session` | GET | Required in deploy | Platform session returned when present | Current session | Any authenticated user | N/A | Auth context, CSRF token | Acceptable for app bootstrap; keep payload privacy-minimized. |
| `/login` | GET | Public entry | Platform mode blocks local OIDC login | N/A | Approved tester through OIDC or platform | N/A | Auth boundary | Acceptable fail-closed direction. |
| `/callback` | GET | Public callback | No workspace unless OIDC mode | State cookie | OIDC allowlist | N/A | OIDC code/state | State check exists; logs redact callback query values. |
| `/logout`, `/signed-out` | GET | Session optional | N/A | Current cookies | Any | N/A | Session termination | Acceptable. |
| `/api/platform/launch` | POST | Platform token | Required in launch context | Platform user and workspace become signed session | Platform membership role mapped to local role | Exempt from CSRF by design, rate-limited | Launch token/session boundary | Acceptable shape, but not verified against live Platform repo. |
| `/api/profiles` | GET | Required in deploy | Uses current storage workspace | N/A | Any authenticated user | N/A | Profile and pricing summaries | Risk: DB pricing list can include local/bundled shared references. |
| `/api/settings` | GET | Required in deploy | Uses current storage workspace | N/A | `canManageSettings` | N/A | Settings, profiles, pricing refs | Role-gated; inherits pricing/profile fallback blockers. |
| `/api/settings/pricing-references` | GET | Required in deploy | DB rows keyed by workspace, but list also merges local/bundled | N/A | `canManagePricingReferences` | N/A | Pricing metadata | High blocker: shared local/bundled fallback in DB mode. |
| `/api/settings/pricing-references/{id}` | GET | Required in deploy | DB lookup first, then local/bundled fallback | N/A | `canManagePricingReferences` | N/A | Pricing rows/catalog | High blocker: detail falls through to `pricing_reference_pack_detail()`. |
| `/api/settings/pricing-references/{id}/export.xlsx` | GET | Required in deploy | Same as detail | N/A | `canManagePricingReferences` | N/A | Pricing export workbook | High blocker: export can be built from shared fallback detail. |
| `/api/pricing-reference/template.xlsx` | GET | Required in deploy | N/A | N/A | Any authenticated user | N/A | Blank template | Acceptable. |
| `/api/pricing-reference/validate` | POST | Required in deploy | N/A | N/A | Any authenticated user | Yes | Uploaded pricing file | Medium: validation exists, but import surface should be retested with hostile workbooks before production. |
| `/api/settings/pricing-references/import-preview` | POST | Required in deploy | N/A until save | N/A | `canImportPricingReferences` | Yes | Uploaded pricing source and AI normalization | Medium: parser limits exist; AI import must remain privacy-minimized. |
| `/api/settings/pricing-references` | POST | Required in deploy | Saves DB rows by workspace in DB mode | N/A | `canManagePricingReferences` | Yes | Pricing rows/catalog | Save path is workspace-scoped, but existing checks use fallback detail and local pack helper for unchanged case. |
| `/api/settings/pricing-references/{id}` | DELETE | Required in deploy | Deletes DB row by workspace in DB mode | N/A | `canManagePricingReferences` | Yes | Pricing reference deletion | DB delete is workspace-scoped; local mode remains local-UAT only. |
| `/api/settings/profiles` | GET | Required in deploy | DB profiles keyed by workspace, but default profile is bundled | N/A | `canManageProfiles` | N/A | Profile metadata | High blocker: default/local profile pack remains mixed into DB list. |
| `/api/settings/profiles/{id}/export.json` | GET | Required in deploy | DB row export in DB mode; local helper can export pack assets in local mode | N/A | `canManageProfiles` | N/A | Profile defaults/layout assets | Medium/high depending mode: profile asset export needs object-storage ownership before internal alpha. |
| `/api/settings/profiles` | POST | Required in deploy | Saves DB row by workspace in DB mode | N/A | `canManageProfiles` | Yes | Profile defaults/layout assets | Save is scoped; generation does not yet consume DB artifacts as source of truth. |
| `/api/settings/profiles/{id}` | DELETE | Required in deploy | Deletes DB row by workspace in DB mode | N/A | `canManageProfiles` | Yes | Profile deletion | DB delete is workspace-scoped; confirm artifact cleanup in follow-up. |
| `/api/quote-sessions` | GET | Required in deploy | DB rows keyed by workspace | Owner visibility in DB mode | Any authenticated user | N/A | Quote session summaries | DB visibility exists; local mode is not multi-user. |
| `/api/quote-sessions/{id}` | GET | Required in deploy | DB row keyed by workspace | Owner/admin visibility in DB mode | Any authenticated user | N/A | Draft state, filenames, session metadata | DB visibility exists; local mode is local-UAT only. |
| `/api/quote-sessions/{id}` | DELETE | Required in deploy | DB row keyed by workspace | Owner editable in DB mode | `canGenerateQuote` | Yes | Session deletion | DB editability exists; artifact cleanup/retention needs follow-up. |
| `/api/quote-sessions/{id}/download/{kind}` | GET | Required in deploy | DB artifact keyed by workspace/session in DB artifact mode | Owner/admin visibility through metadata lookup | Any authenticated user | N/A | Generated XLSX/PDF | Better path. Production still needs object storage and retention. |
| `/api/jobs` | POST | Required in deploy | Auth session passed to worker | No job owner stored in `JOBS` | Any authenticated user; generation checks later | Yes | Async draft/generate jobs | Medium: job status/result is random-ID gated, not owner-bound. |
| `/api/jobs/{job}` | GET | Required in deploy | None | None | Any authenticated user | N/A | Job status/result/files | Medium: in-memory job result is not session/workspace-owned. |
| `/api/jobs/{job}/files/{filename}` | GET | Required in deploy | None | None | Any authenticated user | N/A | Generated XLSX/PDF direct file | High blocker: legacy download is not workspace/session/job-owner bound. |
| `/api/line-items/normalize` | POST | Required in deploy | Uses selected pricing path in payload | N/A | `canGenerateQuote` | Yes | Quote basis/line item normalization | Inherits pricing fallback risk. |
| `/api/draft` | POST | Required in deploy | Payload/state only | N/A | Any authenticated user today | Yes | Uploaded images/PDFs, quote details, AI draft | High for hosted mode: local fallback can return success-like draft on missing/failed AI. |
| `/api/generate` | POST | Required in deploy | Payload/session storage may be DB-scoped | Session update through storage if supplied | Any authenticated user today | Yes | Quote generation, temp/output files | High: profile/pricing generation still resolves local fallback assets. |
| `/api/log` | POST | Required in deploy | Current log context only | N/A | Any authenticated user | Yes | Client diagnostics | Logs are sanitized, but hosted logging/retention is not productionized. |
| `OPTIONS *` | OPTIONS | Host guard | N/A | N/A | None | N/A | CORS preflight | Blocks CORS preflight. |

Fail-open and client-trusting routes:

- `/api/jobs/{job}` and `/api/jobs/{job}/files/{filename}` trust the random job id rather than workspace/session ownership.
- `/api/generate` accepts payload-selected `profile_id`, `pricing_reference_id`, and pricing reference source; validation checks existence, but existence can be satisfied by local/bundled fallback.
- `/api/draft` can produce local fallback draft data when remote AI is missing or failed.

## Workspace And Tenant Isolation Matrix

| Surface | Workspace-bound proof | Gap | Severity | Release gate |
| --- | --- | --- | --- | --- |
| DB profiles | `kqag_profiles` primary key includes `(workspace_id, profile_id)` (`webapp/server.py:6807`), reads use `where workspace_id = ?` (`webapp/server.py:7112`). | `DatabaseKqagStorage.list_profiles()` always prepends a bundled default profile (`webapp/server.py:7140`), and generation uses `load_profile_pack()` (`webapp/server.py:13287`). | High | Internal alpha blocker. |
| DB pricing references | `kqag_pricing_references` primary key includes `(workspace_id, reference_id)` (`webapp/server.py:6815`). | DB list merges workspace rows with `list_local_pricing_references()` and `list_bundled_pricing_references()` (`webapp/server.py:7180`), and detail falls through to `pricing_reference_pack_detail()` (`webapp/server.py:7223`). | High | Internal alpha blocker. |
| DB quote sessions | `kqag_quote_sessions` key includes `(workspace_id, session_id)` (`webapp/server.py:6823`), reads include workspace predicate (`webapp/server.py:7437`). | Ownerless legacy/local sessions remain local-UAT-only. Admin can view other-user sessions only under visibility conditions (`webapp/server.py:7405`). | Medium | Add tests and hosted policy before internal alpha. |
| DB artifacts | `kqag_quote_artifacts` and `kqag_file_artifacts` keys include `workspace_id` (`webapp/server.py:6833`). | This is SQLite/BLOB mode, not final object storage. Profile file artifacts are stored but not used by generation as source of truth. | High | Internal alpha temporary exception only after backup/restore; production blocker. |
| Local profile/pricing/session roots | Local roots are shared by process and company/default identifiers. | Not tenant-isolated. | High in hosted mode | Allowed only for local UAT/test harness. |
| Platform session | `safe_platform_session_context()` requires consumed outcome, user id, workspace id, app key, and supported role (`webapp/server.py:6944`). | Live platform contract not verified in this repo; Swooshz Platform repo out of scope. | Medium | Verify in platform integration PR. |

Cross-workspace leak paths:

- Pricing references: one workspace can list, inspect, export, or generate with local/bundled pricing references present on the host when DB/platform mode falls through to global local pack resolution.
- Profile layout/defaults: generation can use bundled/default/local profile layout fallback instead of workspace-owned profile assets.
- Legacy job files: any authenticated user with a valid job id and filename can request output-root files without workspace/session ownership lookup.

## Artifact Lifecycle And Security Matrix

| Artifact path | Storage | Download route | Controls present | Gap | Severity |
| --- | --- | --- | --- | --- | --- |
| Async job output | `QUOTE_OUTPUT_ROOT/{job_id}` | `/api/jobs/{job}/files/{filename}` | Safe filename allowlist and output-root containment (`webapp/server.py:14213`). | No workspace, session, or job-owner check. Guessing is hard but leaked URLs are enough. | High |
| Quote-session XLSX/PDF in local mode | `QUOTE_DATA_ROOT/quote-sessions/{session}/exports` | `/api/quote-sessions/{id}/download/{kind}` | Safe session id, expected filename, stale checks (`webapp/server.py:12963`). | Local mode has no tenant boundary. | Medium |
| Quote-session XLSX/PDF in DB artifact mode | `kqag_quote_artifacts` | `/api/quote-sessions/{id}/download/{kind}` | Workspace/session/artifact-kind query and owner visibility through session metadata (`webapp/server.py:7355`). | SQLite/BLOB is not final object storage; backup/restore and retention not proven. | Medium/High |
| Uploaded booth/render images | Request payload and draft session files | Stored in draft files and temp job directory | MIME from data URL/name, base64 decode, size limits (`webapp/server.py:8388`, `8422`, `12320`). | MIME sniff is partial; persistent object ownership is not final. | Medium |
| Pricing visual assets | Local pricing pack or DB file artifacts | Export/reference rendering | Filename sanitization, image data URL parse, byte limits (`webapp/server.py:7287`). | DB artifacts are not final object storage; local pack path is shared. | Medium/High |
| Profile layout workbook | Local profile pack or DB file artifacts | Profile export/generation | XLSX zip validation (`webapp/server.py:4728`), safe filenames (`webapp/server.py:4719`). | Generation still reads local profile pack layout path (`webapp/server.py:13287`). | High |

Path traversal review:

- Static files enforce containment under `STATIC_DIR`.
- Legacy job downloads enforce filename allowlist and output-root containment, so this audit did not confirm arbitrary file read.
- Quote-session local path helpers validate generated safe session IDs and expected filenames.
- The main artifact release blocker is authorization/ownership, not raw traversal.

## Import/Upload Risk Matrix

| Surface | Accepted input | Controls | Gap | Severity |
| --- | --- | --- | --- | --- |
| Pricing template validation | `.xlsx`, `.csv` | Data URL base64 decode max 10 MB; extension check; ZIP entry and total uncompressed limits; row and column limits; formula text sanitization on save. | MIME/content-type is driven by data URL and filename. Needs hostile workbook corpus before production. | Medium |
| Pricing import preview | `.xlsx`, `.csv`, `.md` | Same decode limits; parser exceptions become sanitized validation errors; AI timing logs are metadata-only. | AI normalization can see source rows; hosted privacy posture depends on provider config and data handling policy. | Medium |
| Profile save/import | JSON payload with optional embedded layout workbook/rules | Profile id/filename sanitization, layout XLSX validation, rules JSON limit, formula-neutralized defaults. | Profile layout assets stored in DB artifact mode but not consumed from workspace artifacts during generation. | High |
| Booth/render images | Data URL images/PDFs | Max files, max bytes, base64 decode, basic PDF and image checks. | MIME sniffing is partial; temp/local roots not production storage. | Medium |
| Client log events | JSON event/details | Event allowlist/category and sanitize log values. | Hosted logging backend, retention, monitoring, and support traceability are not productionized. | Medium |

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
- `/api/jobs` status and legacy file routes are not owner-bound.
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
- Legacy direct artifact download without workspace/session authorization.
- Broad exception handling that hides storage/auth/artifact/AI failure and returns success.

Current fallback blockers:

| Fallback | Evidence | Risk | Severity |
| --- | --- | --- | --- |
| DB pricing list/detail local/bundled fallback | `webapp/server.py:7180`, `7223` | Workspace users can see/use shared host pricing packs. | High |
| Profile/layout generation local fallback | `webapp/server.py:7711`, `7762`, `13287` | Missing workspace profile assets can silently use bundled/default layout. | High |
| AI draft local fallback | `webapp/server.py:12152` to `12285` | Missing/failed remote AI can return `status: drafted` with local starter basis. | High in hosted/internal alpha |
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

- Hosted logging backend, alerting, retention, and support-safe trace correlation are not productionized.
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
| Broken Access Control | Workspace DB rows exist, but pricing/profile fallbacks and legacy job downloads bypass object ownership. | High blockers remain. |
| Cryptographic Failures / Sensitive Data Exposure | Signed cookies, secure deploy cookies, redaction helpers, and formula hardening exist. Local logs/storage remain UAT-only. | Medium gaps remain. |
| Injection | JSON parsing, safe IDs, safe filenames, formula neutralization, XLSX XML limits, no SQL string interpolation for user IDs in core queries. | No confirmed critical injection, but hostile workbook testing should expand. |
| Insecure Design | Local UAT architecture remains mixed with hosted/platform goals. Local/bundled fallbacks are product-shape blockers. | High blockers remain. |
| Security Misconfiguration | Deploy fail-closed guard exists; CI has read-only permissions. CodeQL absent; Gitleaks image not pinned. | Medium gaps remain. |
| Vulnerable/Outdated Components | `npm audit` is part of CI; local result is tracked below. Python audit not present. | Evidence incomplete. |
| Identification/Auth Failures | OIDC state, allowlist, platform launch validation, and signed cookies exist. Live platform contract not verified. | Medium gap. |
| Software/Data Integrity Failures | CI validates syntax/tests and security gates, but external actions/images are not fully pinned. | Medium gap. |
| Logging/Monitoring Failures | Privacy-safe local logging exists. Hosted monitoring, alerting, retention, and audit trail are not productionized. | Medium gap. |
| SSRF / External Fetch Risk | OIDC and platform launch outbound URLs come from env config; AI provider requests go to configured providers. No arbitrary user-supplied URL fetch was confirmed. | Low/Medium, keep config allowlists strict. |
| Business Logic Vulnerabilities | Mixed workspace/local profile/pricing state can influence quote generation; disabled/deleted artifact cases need tests. | High blockers remain. |

## Business Logic Security Findings

| Scenario | Result | Severity | Required follow-up |
| --- | --- | --- | --- |
| Generate quote with mixed workspace profile/pricing/session state | Possible because generation applies workspace defaults from local company store and then loads profile/pricing packs through fallback resolvers. | High | Generate only from workspace-owned profile/pricing artifacts in DB/platform mode. |
| Disabled/deleted pricing reference still usable | DB delete removes workspace row, but bundled/local fallback by same id can still satisfy lookup. | High | Remove fallback in DB/platform mode and add deleted-reference regression tests. |
| Deleted sessions/artifacts restored/exported/downloaded | DB session delete removes metadata but artifact cleanup/retention is not fully verified; legacy job files remain under output root. | Medium/High | Add artifact lifecycle cleanup/authorization tests and object-storage metadata. |
| Edited past session leaks current/default settings | Session summary fallback can recalculate display names/defaults from current local packs; generated artifact stale logic exists but not a full historical snapshot model. | Medium | Store immutable profile/pricing snapshot metadata with session/artifacts. |
| Cross-user dashboard visibility | DB mode owner visibility exists; ownerless sessions remain visible. | Medium | Define migration/owner policy before internal alpha. |
| Race/deletion/export edge cases | Not deeply exercised in this audit. | Medium | Add focused tests for delete-while-download, stale artifacts, and deleted references. |

## Findings Table

| Severity | Finding | Evidence | Impact | Required fix |
| --- | --- | --- | --- | --- |
| Critical | None confirmed in the audited source and metadata-only scans. | N/A | N/A | Keep Critical gate open for any confirmed cross-workspace private data leak, unauthorized artifact byte access, production auth bypass, committed secret, or arbitrary file read/write. |
| High | DB/platform pricing references can include shared local/bundled packs. | `webapp/server.py:7180`, `7223` | Workspace users can see/use references not owned by the workspace. | Remove local/bundled fallback in database/platform/deploy mode and test workspace isolation. |
| High | Profile/layout generation still resolves local/default profile packs. | `webapp/server.py:7711`, `7762`, `13287` | Missing workspace profile assets can silently use shared local/default assets. | Resolve layout/defaults from workspace DB/object artifacts only. |
| High | Legacy job-file downloads are not workspace/session-owner bound. | `webapp/server.py:13595`, `14213` | Any authenticated user with a leaked job URL can fetch generated artifacts. | Disable route in hosted modes or authorize through workspace/session artifact metadata. |
| High | Local AI draft fallback returns product-visible drafted result when remote AI is missing/failed. | `webapp/server.py:12232`, `12274` | Hosted/internal-alpha users can receive fake success from local starter data. | Disallow fallback in internal-alpha/production; return safe failure requiring real AI/workspace data. |
| Medium | Async job status/result is random-ID gated, not owner-bound. | `webapp/server.py:13226`, `13257`, `13598` | Leaked job IDs can expose job status/result metadata. | Store job owner/workspace or remove legacy job polling for hosted mode. |
| Medium | Import/upload validation is good but hostile-corpus evidence is incomplete. | `webapp/server.py:3713`, `4728`, `6322`, `8422` | Malformed XLSX/PDF/image edge cases could cause parser failure or resource pressure. | Add synthetic hostile upload fixtures and regression tests. |
| Medium | Hosted logging/monitoring/retention is not productionized. | `webapp/server.py:1182`, docs | Local logs are not a production support/audit trail. | Add hosted privacy-minimized logging and retention design. |
| Medium | Supply-chain evidence is incomplete. | `.github/workflows/ci.yml`, `package.json` | CI has useful gates but no CodeQL/Python audit and unpinned Gitleaks image. | Add CodeQL/dependency review or documented equivalent before production. |
| Low | Historical docs still mention Load Sample/sample paths as audit evidence. | `docs/architecture-dead-code-fallback-audit.md`, `docs/production-readiness-audit.md` | Could confuse future readers if not superseded by this doc. | Link this audit as the current release gate and trim historical wording later. |

## Internal-Alpha Release Gate

Do not start internal alpha until all are true:

- No product-visible Load Sample/sample/demo/fake seeded path exists.
- Database/platform mode cannot list, detail, export, delete, or generate from local/bundled private-like pricing references.
- Generation resolves profile defaults and layout workbook from workspace-owned profile assets.
- Legacy `/api/jobs/{job}/files/{filename}` is disabled in hosted modes or authorized by workspace/session ownership.
- `/api/draft` does not return local fallback success in internal-alpha mode.
- Quote sessions and artifacts are workspace-owned, owner-aware, and restart persistent.
- Backup/restore/rollback is documented and tested for the chosen internal-alpha storage mode.
- Hosted smoke covers platform launch, workspace profile import/use, pricing import/use, quote generation, session persistence, artifact download, delete, and logout.
- Logs remain privacy-minimized and support-traceable without raw prompts, uploads, provider responses, secrets, or generated quote contents.
- Codex Security standard scan is complete or any incomplete status is explicitly disclosed.

## Production Release Gate

Do not claim production readiness until all internal-alpha gates plus these are true:

- Object storage exists for generated XLSX/PDF, uploaded images/PDFs, profile layout assets, and pricing visual assets.
- DB rows store object keys, checksums, byte sizes, content types, owner/workspace/session metadata, retention state, and audit metadata.
- Downloads stream or sign objects only after workspace/session/owner authorization.
- Backup and restore drills prove DB and object storage recover together.
- Retention/deletion policies are implemented and tested.
- CodeQL or equivalent static analysis is enabled.
- Python dependency audit/advisory check is documented.
- CI branch protection and required checks are documented as complete.
- No unresolved Critical/High findings remain, unless a written risk acceptance exists for a narrow internal-only exception. No such acceptance exists in this audit.

## Recommended Implementation PR Sequence

1. Pricing-reference isolation: remove local/bundled fallback from database/platform/deploy mode, block deleted-reference fallback, and add cross-workspace tests.
2. Workspace profile/layout resolution: generate quotes from workspace-scoped profile defaults and layout artifacts; fail clearly if missing.
3. Legacy job route lockdown: disable `/api/jobs/{job}/files/{filename}` in hosted modes or route it through session-owned artifact metadata.
4. AI fallback policy: disallow local starter draft success in internal-alpha/production; keep only test/local-UAT harness behavior.
5. Artifact object-storage design: move generated outputs and uploaded/reference/profile assets to object storage with DB metadata and checksums.
6. Session and business-logic hardening: owner-bound jobs, immutable profile/pricing snapshots, stale/deleted artifact tests, delete/export race tests.
7. Hosted logging and operations: privacy-minimized logging, monitoring, backup/restore/rollback runbooks, and hosted smoke evidence.
8. Supply-chain hardening: CodeQL/equivalent, Python dependency audit, pinned security scanner image, branch protection docs.
9. Platform integration audit: verify launch/auth/workspace claims against the Swooshz Platform repo in a separate PR.

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
| `python -m py_compile webapp/server.py scripts/generate_quote.py scripts/audit_architecture_fallbacks.py` | Passed. |
| `python -m unittest tests.test_webapp` | Passed on escalated rerun: 400 tests OK. Initial sandbox run failed on Windows temp-directory permissions, not code assertions. |
| `python -m unittest tests.test_architecture_fallback_audit` | Passed: 3 tests OK. |
| `python -m unittest tests.test_generate_quote` | Passed on escalated rerun: 63 tests OK. Initial sandbox run failed on Windows temp-directory permissions, not code assertions. |
| `python -m unittest tests.test_production_readiness` | Passed: 4 tests OK. |
| `python scripts/audit_architecture_fallbacks.py --max-hits-per-pattern 1 --max-possible-unused 5` | Passed; metadata-only scanner output recorded above. |
| `python scripts/check_production_readiness.py` | Expected nonzero exit. Reported `local_uat_supported=true`, `internal_alpha_ready=false`, `production_ready=false`, and 6 blockers. |
| `python scripts/scan_sensitive_fixtures.py` | Passed: 0 blocking, 0 review findings. |
| `python scripts/validate_local_pdf_dependency_usage.py` | Passed. |
| `python scripts/validate_dynamic_pricing_reference_rules.py` | Passed. |
| `node --check webapp/static/app.js` | Passed. |
| `node --check scripts/playwright-smoke.mjs` | Passed. |
| `npm audit` | Passed: 0 vulnerabilities. |
| `git diff --check` | Passed before final doc status update; rerun required after final patch. |

Readiness command note: the nonzero result from `python scripts/check_production_readiness.py` is expected and desired in this audit. It proves this PR does not claim internal-alpha or production readiness while release blockers remain.

## What Was Not Verified

- Live Swooshz Platform repo behavior, platform token service, or platform workspace membership enforcement.
- Live OIDC provider behavior.
- Live AI provider privacy posture, data retention, or rate limits.
- Real private Koncept pricing/profile/layout data import.
- Generated customer quote contents.
- Object storage, because it is not implemented in this repo.
- Production backup/restore/rollback, because production storage is not implemented.
- Browser click-through during this docs-only audit, unless a later validation run adds it.
- Exhaustive hostile upload corpus beyond existing unit tests and static review.
- Every possible race/deletion edge case in session/artifact lifecycle.

## Self-Review Checklist

- This audit does not claim production readiness.
- Critical/High findings are listed directly in the findings table and release gates.
- Scanner/audit output is metadata-only and privacy-safe.
- No private Koncept data, secrets, generated quote contents, staff emails, or private paths are committed by this audit.
- No product-visible sample/demo/fake flow is reintroduced.
- Remaining implementation blockers have an explicit PR sequence.
