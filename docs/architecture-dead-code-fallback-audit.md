# KQAG Architecture, Dead-Code, And Fallback Audit

Audit date: 2026-07-02

This audit extends `docs/production-readiness-audit.md` with a whole-repo
review of architecture boundaries, dead-code risk, local traces, fallback
paths, sample/demo behavior, and documentation cleanup. It is intentionally an
audit PR: it adds safe tooling and tests, but does not convert storage
architecture, migrate private data, remove current smoke-test dependencies, or
change quote math.

## Executive Summary

SAQG/KQAG is moving from local UAT toward workspace-owned tenant data and
sellable production readiness. Product-visible local/demo/sample shortcuts
should not be preserved as normal app behavior.

Core direction:

- Koncept Images Pte Ltd can exist only as an explicitly imported tenant
  workspace, never as a bundled/default app fallback.
- Dev and test should move toward the real product shape: platform-scoped
  sessions, workspace-scoped profile/pricing/session/artifact storage, and
  private Koncept data imported outside Git.
- Local/test fixtures may remain only as isolated test harnesses. They must not
  appear as normal product UI, routes, or operator instructions.
- Load Sample is not part of the sellable product path and should be removed
  completely from product code in the first follow-up implementation PR.
- No broad fallback should exist in database/platform/deploy mode unless
  explicitly justified, mode-gated, tested, and scheduled for removal.

Current conclusion:

KQAG remains local-UAT capable, but it is not ready for hosted, protected,
deploy, or production use. The biggest product-shape blockers are Load Sample
product surfaces, local/bundled profile and pricing fallbacks, local artifact
download paths, broad exception fallback behavior, and docs that still describe
local or demo data as the operator happy path.

## Search Strategy

The repo was searched with targeted whole-repo `rg` queries and the new safe
static scanner in `scripts/audit_architecture_fallbacks.py`.

Required search families covered:

- fallback terms: `fallback`, `fallback_to`, `default`, `try:`,
  `except Exception`, `catch`, `pass`
- local storage terms: `local`, `local mode`, `local pack`,
  `QUOTE_DATA_ROOT`, `QUOTE_OUTPUT_ROOT`, `QUOTE_TMP_ROOT`,
  `KQAG_LOCAL_PRICING_REFERENCES_ROOT`, `KQAG_STORAGE_MODE`,
  `KQAG_ARTIFACT_STORAGE_MODE`
- profile and pricing terms: `profile pack`, `pricing reference`,
  `load_profile_pack`, `list_local_pricing_references`,
  `list_bundled_pricing_references`, `pricing_reference_pack_detail`
- artifact terms: `send_download`, `/api/jobs`
- dead-code terms: `compatibility`, `legacy`, `deprecated`, `TODO`, `FIXME`,
  `TODO remove`, `unused`, `orphan`, `hardcoded`
- sample/demo terms: `Load Sample`, `load sample`, `loadSample`,
  `sample quote`, `sample`, `fixture`, `synthetic`, `bundled`, `Kent`, `mock`,
  `demo`, `fake`, `example`, `seed`

The scanner reports metadata only: relative paths, line numbers, pattern names,
categories, counts, and static definition heuristics. It does not echo source
lines, absolute roots, environment values, tokens, private runtime paths,
generated quote contents, private pricing/profile contents, or fixture text.

Representative scanner snapshot from this PR run. The scanner skips this audit
report file to avoid counting its own inventory text as product evidence:

| Category | Hits |
| --- | ---: |
| `sample_or_bundled_data` | 2548 |
| `exception_or_default_boundary` | 1379 |
| `local_storage_dependency` | 832 |
| `fallback_path` | 278 |
| `pricing_reference_boundary` | 259 |
| `legacy_or_compatibility` | 80 |
| `artifact_download_boundary` | 46 |
| `storage_mode_boundary` | 48 |
| `profile_boundary` | 21 |
| `hardcoded_marker` | 18 |
| `broad_exception_boundary` | 17 |
| `load_sample_surface` | 14 |
| `dead_code_marker` | 2 |

High-signal pattern totals from that snapshot:

| Pattern | Hits |
| --- | ---: |
| `KQAG_STORAGE_MODE` | 26 |
| `KQAG_ARTIFACT_STORAGE_MODE` | 22 |
| `KQAG_LOCAL_PRICING_REFERENCES_ROOT` | 3 |
| `QUOTE_DATA_ROOT` | 37 |
| `QUOTE_OUTPUT_ROOT` | 19 |
| `QUOTE_TMP_ROOT` | 17 |
| `fallback` | 272 |
| `fallback_to` | 3 |
| `except Exception` | 17 |
| `legacy` | 76 |
| `/api/jobs` | 40 |
| `send_download` | 6 |
| `Load Sample` | 7 |
| `load sample` | 7 |
| `Kent` | 61 |
| `sample` | 230 |
| `synthetic` | 1225 |
| `fixture` | 244 |
| `bundled` | 113 |
| `mock` | 446 |
| `demo` | 56 |
| `fake` | 49 |

Static possible-unused output is a follow-up aid only. It found many
definitions with low token counts, including parser callbacks, test methods,
and CLI helpers. None should be deleted from this PR without targeted runtime
evidence and tests.

## Codebase Map

| Surface | Responsibilities | Current posture | Classification |
| --- | --- | --- | --- |
| `webapp/server.py` routes | Serves app shell, APIs, storage mode selection, profile/pricing/session/artifact handlers, sample routes, quote generation, readiness checks. | Large mixed boundary. Some routes are workspace-aware; PR #91 disables legacy job downloads in protected modes, while other storage/fallback blockers remain. | Live app code with production blockers. |
| `webapp/static/app.js` | Main product UI flow, intake state, uploads, profile/pricing selection, quote basis, generation, sessions, dashboard, sample loading. | Live app UI includes Load Sample and demo fixture copy. | Live app code with product-visible sample blockers. |
| `webapp/static/index.html` | App shell and control placement. | Live app shell includes Load Sample button and sample fixture wording. | Live app code with product-visible sample blockers. |
| `scripts/generate_quote.py` | XLSX generation, layout handling, optional PDF generation, spreadsheet hardening. | Core quote output path. It has layout/PDF fallback paths that are useful locally but must not hide missing profile assets in production. | Live core code; fallback policy needed. |
| Storage and migration scripts | Create/update local and database storage schemas, migrate local runtime data into DB mode, validate readiness. | Important for the platform-scoped transition. Some modes still preserve local fallback compatibility. | Live tooling; hosted/protected/deploy blockers remain. |
| Tests | Unit, integration, readiness, sensitive fixture, Playwright smoke/stress coverage. | Many tests rely on product-visible Load Sample or synthetic/sample fixtures. That is acceptable only until follow-up replaces it with seeded test setup. | Test harness needed, but product-path reliance must change. |
| Docs | UAT, platform launch, storage mode, production readiness, CI, import behavior, privacy, playbooks. | Several docs still frame local/sample/demo paths as normal operator flow. | Rewrite/delete inventory below. |
| Runtime directories | Local data, output, temp, logs, private imports, generated artifacts. | Local UAT only. Must not be treated as production persistence. | Local-UAT-only; production blocker if hosted. |
| Fixtures/sample/bundled data | Repo sample PDF/JSON, synthetic test values, bundled references. | Useful for tests, but product-visible sample and bundled-private-like fallback paths are not sellable-product behavior. | Move to test-only or historical/audit-only. |
| Platform launch/auth/session handling | Platform session consume adapter, approved-tester login, CSRF/same-origin, host guard, workspace context. | Directionally correct, but still mixed with local storage and sample shortcuts. | Live, not production-complete. |
| Quote session/dashboard handling | Save, list, duplicate, modify, delete, export artifacts. | DB storage is closer to target; local mode still exists, and PR #91 owner-binds hosted job status/result reads. | Live; production blockers remain. |
| Artifact generation/download handling | Writes XLSX/PDF artifacts and serves downloads. | Quote-session downloads are closer to workspace-aware behavior. PR #91 disables legacy `/api/jobs` direct files in hosted/database/platform/deploy paths. | Live; object-storage production blocker remains. |
| Pricing reference import/list/detail/use | Import AI/manual catalogs, save/list details, generate with selected reference. | DB mode currently merges DB references with local/bundled packs and detail can fall through to local pack resolver. | Production blocker. |
| Profile import/list/detail/use | Manage customer profile data and layout assets. | DB rows/artifacts exist, but generation-time profile pack resolution still reads local layout/default assets. | Production blocker. |
| AI analysis flow | Draft quote basis from images and notes; local fallback when model is unavailable or fails. | Useful in local UAT, but fake success must not mask AI/storage failure in hosted, protected, deploy, or production modes. | Must be mode-gated or converted to explicit error. |

## Load Sample Removal Inventory

Decision: the first implementation PR after this audit must be:

> Remove Load Sample completely from product UI/routes/JS/docs and replace test
> reliance with test-only seeded setup.

Post-audit implementation status: this follow-up removes the product Load
Sample UI, JS handler, HTTP routes, product-facing sample helpers, product
sample fixture folder, and Playwright product-control dependency. The inventory
below is retained as historical audit evidence for the removed path and for
verifying that remaining fixtures are test-only.

Required sellable-product posture:

- No Load Sample button in hosted/protected/deploy or production.
- No Load Sample product route/API.
- No Playwright smoke depending on product-visible Load Sample.
- No docs telling real operators/users to use sample/demo data.
- Remaining fixtures must be test-only or historical/audit-only.

| Surface | Evidence | Current behavior | Classification | Follow-up action |
| --- | --- | --- | --- | --- |
| UI copy | `webapp/static/index.html:274` | Historical app shell told operators they could upload reference files or load the sample fixture. | Removed from product code. | Done in this follow-up; current copy points to real workspace reference files. |
| UI button | `webapp/static/index.html:277` | Historical product UI rendered `Load Sample`. | Removed from product code. | Done in this follow-up; no hosted/protected/deploy or production button remains. |
| JS default | `webapp/static/app.js:4` | Historical `DEFAULT_SAMPLE_ID` pointed at the sample fixture. | Removed from product code. | Done in this follow-up; seeded IDs live only in test tooling. |
| JS intake copy | `webapp/static/app.js:73` | Historical intake subtitle mentioned loading the demo fixture. | Removed from product code. | Done in this follow-up; no demo fixture happy path remains. |
| JS element wiring | `webapp/static/app.js:341` | Historical product state referenced `sampleDetailsButton`. | Removed from product code. | Done in this follow-up. |
| JS handler | `webapp/static/app.js:6207` to `webapp/static/app.js:6235` | Historical `setSampleDetails()` fetched sample API, applied sample details, profile, pricing, and image files. | Removed from product code. | Done in this follow-up; tests seed setup without product sample API. |
| JS control state | `webapp/static/app.js:11378` to `webapp/static/app.js:11379` | Historical sample button visibility and disabled state were managed like a normal app control. | Removed from product code. | Done in this follow-up. |
| JS click binding | `webapp/static/app.js:11696` | Historical product click listener called the sample handler. | Removed from product code. | Done in this follow-up. |
| Server constants | `webapp/server.py` sample root constants | Historical server knew about repo sample fixture root. | Removed from product code. | Done in this follow-up. |
| Server helper | `webapp/server.py:13052` to `webapp/server.py:13123` | Historical `sample_dir`, `list_samples`, and `load_sample` loaded fixture files and resolved profile/pricing IDs. | Removed from product code. | Done in this follow-up; route absence is covered by tests. |
| Server routes | `webapp/server.py:13671` to `webapp/server.py:13683` | Historical product API exposed `/api/samples` and `/api/samples/{id}`. | Removed from product code. | Done in this follow-up; product sample route/API is absent. |
| Repo fixture files | `fixtures/samples/kent-group/sample.json`, `fixtures/samples/kent-group/kent-group.pdf` | Historical bundled sample data and image were reachable through product route. | Kept only under tests with no product path. | Done in this follow-up; moved under `tests/fixtures/quote-generator/samples/`. |
| Sensitive fixture scanner | `scripts/scan_sensitive_fixtures.py` allowlist entries | Scanner permits known test-only fixture markers. | Keep only if fixture remains test-only. | Done in this follow-up; allowlist points at test fixture paths only. |
| Unit tests | `tests/test_webapp.py` sample-route, Load Sample DOM, default sample ID, Kent fixture assertions. | Historical tests protected product-visible sample behavior. | Replaced with test-only fixture helper or absence assertions. | Done in this follow-up; tests assert UI/API/helper absence. |
| Playwright smoke | `scripts/playwright-smoke.mjs` sample button clicks and sample restore expectations. | Historical smoke depended on product-visible Load Sample. | Replaced with test-only seeded fixture helper. | Done in this follow-up; no product-visible Load Sample dependency. |
| Playwright stress | `scripts/playwright-ai-basis-chat-stress.mjs` sample button click. | Historical stress setup used product sample path. | Replaced with test-only seeded fixture helper. | Done in this follow-up; no product sample CTA/API. |
| Live seeded AI check | `scripts/playwright-live-seeded-ai-check.mjs` seeded reference setup. | Historical live smoke depended on sample path. | Rewritten as seeded test tooling; private-data import smoke remains a separate future path. | Done in this follow-up for bundled sample dependence; private-data import smoke remains follow-up work outside Git. |
| Docs | `docs/internal-uat*.md`, `docs/platform-uat-smoke-runbook.md`, `docs/kqag-current-status.md`, and related indexes | Some docs presented sample/local/demo flow as normal UAT or smoke path. | Outdated docs to delete/rewrite. | Current follow-up removes known product-visible sample guidance; broader platform/storage doc alignment remains in the cleanup inventory. |

## Fallback Inventory

| File/function/route | Trigger | Falls back from | Falls back to | Local/private file risk | Cross-workspace risk | Quote/pricing/profile/session/artifact impact | Mode gating today | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `DatabaseKqagStorage.list_pricing_references` | DB pricing list requested. | Workspace DB pricing rows. | Local saved packs and bundled packs. | Yes, if host has local private packs. | Yes, list/detail can expose non-workspace packs in DB/platform mode. | Pricing reference list and generated quote basis. | Not strict enough. | Must be removed before hosted/protected/deploy use. |
| `DatabaseKqagStorage.pricing_reference_detail` | DB detail lookup misses or falls through. | Workspace DB reference detail. | `pricing_reference_pack_detail()` local/bundled resolver. | Yes. | Yes. | Pricing detail and quote generation. | Not strict enough. | Must be removed before hosted/protected/deploy use. |
| `load_profile_pack` and `ProfilePack.resolve` | Profile ID/default path requested during generation. | Workspace profile row/artifact expectation. | Local profile pack, bundled/default layout template. | Yes, layout/profile assets can be local. | Possible if IDs/defaults are shared outside workspace storage. | Profile defaults, layout rules, XLSX generation. | Not strict enough. | Must be removed before hosted/protected/deploy use. |
| `PricingReferencePack.resolve` | Pricing reference ID requested outside DB rows. | Selected workspace pricing reference. | Local pack then bundled default. | Yes. | Possible in hosted mode. | Pricing catalog used for quote basis and line items. | Not strict enough. | Must be removed before hosted/protected/deploy use in database/platform/deploy mode. |
| `/api/samples`, `/api/samples/{id}` | Historical product API request. | Real workspace data/imports. | Repo sample fixture. | No private-file read by default, but trained product toward fake data. | Shared across all sessions. | Details, images, profile, pricing setup. | Removed from product code in this follow-up. | No product route/API in hosted/protected/deploy or production. |
| `setSampleDetails()` | Historical product UI button click. | User-uploaded booth/render images and real workspace data. | Repo sample details, files, profile, pricing. | No private-file read by default, but created product-visible fake success. | Shared sample behavior. | Quote basis and generation setup. | Removed from product code in this follow-up. | No product CTA/handler in hosted/protected/deploy or production. |
| AI draft local fallback | AI unavailable, not configured, or call fails. | Model-backed image analysis. | Local starter draft / local source result. | No direct private-file read, but can mask AI failure. | No direct cross-workspace data leak found. | Quote basis. | Needs stricter mode gate. | Allowed only as explicit local/test failure mode; disallow fake success in hosted/protected/deploy or production modes. |
| `send_download` on `/api/jobs/{job}/files/{filename}` | Direct legacy job-file URL. | Session-owned artifact lookup. | Output-root file by job id and filename. | Local-UAT only after PR #91. | Disabled in hosted/database/platform/deploy paths after PR #91. | Generated artifacts. | Protected paths now return generic not found. | Keep disabled in hosted modes; use quote-session downloads. |
| Quote-session download route | Session artifact download. | Direct file path. | Storage-backed artifact lookup. | Lower risk when storage mode is DB/artifact aware. | Storage visibility check exists. | Generated artifacts. | Better boundary. | Keep and strengthen with object storage. |
| `scripts/generate_quote.py` layout fallback | Layout template missing. | Active profile layout workbook. | Minimal generated workbook. | No private-file leak, but hides missing profile assets. | No direct cross-workspace risk. | Customer-ready XLSX formatting. | Not product-mode strict. | Local/dev only; production should fail clearly if workspace layout is missing. |
| `scripts/generate_quote.py` PDF fallback | Optional PDF mode requested and styled PDF path unavailable/fails. | Styled PDF renderer. | Text/simple PDF path. | No private-file leak. | No direct cross-workspace risk. | Optional PDF artifact only. | PDF is not default webapp output. | Keep local/tooling only; do not make PDF fallback part of sellable product path without tests. |
| Broad `except Exception` handlers | Unexpected storage/API/generation failures. | Explicit failure propagation. | Generic fallback, warning, or alternate path. | Varies. | Varies. | Can affect quote, storage, artifact, AI, and import flows. | Mixed. | Keep only when error is logged, user gets safe failure, and no fake success is returned. |
| Local runtime roots | Missing DB/object storage or local mode selected. | Workspace DB/object storage. | `QUOTE_DATA_ROOT`, `QUOTE_OUTPUT_ROOT`, `QUOTE_TMP_ROOT`, local logs. | Yes. | Yes in hosted/shared mode if mounted broadly. | Profiles, pricing, sessions, artifacts. | Local mode supported. | Local-UAT/dev only; blocker in database/platform/deploy readiness. |

## Fallback Policy

Allowed:

- UI empty states that do not create fake data.
- Clear capability failure with a safe user message and support-traceable log
  reference, not fake success.
- Test-only seeded fixtures that are unavailable from product UI/routes.
- Explicit migration compatibility paths with tests, owner/workspace guards,
  and a documented removal or review condition.
- Public bundled references only if explicitly allowlisted, non-private, not
  private-like, and not used as an implicit substitute for missing workspace
  data in database/platform/deploy/production mode.

Disallowed in database/platform/deploy/production mode:

- Load Sample product CTA.
- Load Sample product route/API.
- Synthetic, Kent, sample, demo, fake, or fixture defaults as normal product
  data.
- Bundled private-like pricing/profile fallback.
- Local profile/pricing pack fallback.
- Hosted/deploy fallback to local runtime folders.
- Quote generation silently using bundled/synthetic/sample pricing or profile
  data when workspace data is missing.
- Fake success from sample data when real workspace data is missing.
- Catch-all exception handlers that hide failed storage, auth, artifact,
  profile, pricing, or AI operations.
- Artifact download routes that bypass workspace/session authorization.
- "Try everything until one works" behavior for production data.

Local dev/test direction:

- Use real workspace-shaped setup, preferably a seeded test workspace.
- Use Koncept Images Pte Ltd as the first real workspace for internal testing.
- Import private Koncept profile/pricing/layout/image data outside Git.
- Keep fixtures under tests or dedicated harnesses only; do not expose them as
  product controls or product APIs.

## Dead-Code And Local-Trace Findings

| Item | Evidence | Classification | Action |
| --- | --- | --- | --- |
| Load Sample product path | Historical UI, JS handler, server helpers/routes, fixtures, tests, Playwright scripts. | Removed product blocker in this follow-up. | Product UI/API/JS/docs now reject this path; tests use seeded setup. |
| Legacy job-file route | `/api/jobs` route and `send_download` output-root lookup. | Production blocker. | Disable in deploy/database mode or make workspace/session-aware. |
| Local pricing-reference fallback in DB storage | DB list/detail merging local/bundled packs. | Production blocker. | Remove before hosted/protected/deploy use; add isolation tests. |
| Local profile-pack generation dependency | Generation-time `load_profile_pack` and layout path resolution. | Production blocker. | Resolve from workspace DB/object-storage assets. |
| AI local draft fallback | Local starter draft on AI unavailable/failure. | Local-UAT-only unless made explicit failure. | Mode-gate or convert to safe error in hosted/protected/deploy or production modes. |
| Minimal layout fallback | Missing layout creates minimal workbook. | Local/tooling-only unless explicitly approved. | Production should fail clearly when workspace layout is missing. |
| Optional PDF fallback | Styled PDF can fall back to text/simple PDF. | Local/tooling-only. | Keep out of default product path unless production PDF is specified and tested. |
| Possible-unused definitions | Static scanner low-reference heuristics. | Follow-up verification needed. | Do not delete from this PR; investigate with targeted tests before removal. |
| Local UAT docs and smoke paths | Several docs/scripts describe sample/local demo flows. | Outdated docs to rewrite. | See docs cleanup inventory. |
| Sensitive fixture allowlist for sample data | Sensitive fixture scanner allows known sample markers. | Test-only after sample removal. | Tighten once sample route/UI are gone. |

This follow-up safely deletes the product sample surfaces after replacing test
and smoke reliance with seeded test setup. Remaining fallback and storage
architecture blockers are tracked below and should not be hidden by sample data.

## Local Storage Architecture Audit

| Dependency | Current role | Local UAT | Internal alpha | Production | Required change |
| --- | --- | --- | --- | --- | --- |
| `QUOTE_DATA_ROOT` | Local profiles, quote sessions, runtime app data. | Acceptable. | Blocked for hosted/protected/deploy readiness. | Not acceptable. | Move workspace data to DB/object storage. |
| `QUOTE_OUTPUT_ROOT` | Local generated artifacts by job id. | Acceptable. | Blocked unless DB/object-backed and authorized. | Not acceptable. | Store generated XLSX/PDF in object storage with DB metadata. |
| `QUOTE_TMP_ROOT` | Temporary uploads/intermediates. | Acceptable for ephemeral work. | Acceptable only as ephemeral temp, not durable. | Acceptable only as ephemeral temp. | Ensure durable artifacts are copied to owned storage before download. |
| `KQAG_LOCAL_PRICING_REFERENCES_ROOT` | Local mutable pricing packs. | Acceptable for local UAT imports outside Git. | Not acceptable as product fallback. | Not acceptable. | Import into workspace-scoped DB/object storage. |
| Bundled profile/pricing assets | Defaults and sample-like references. | Test/local only if explicitly selected. | Not acceptable as hidden fallback. | Not acceptable as hidden fallback. | Replace with workspace-owned imported assets or explicit public allowlist. |
| Local logs under `_logs/` | Local diagnostics. | Acceptable. | Needs hosted privacy-minimized logging. | Needs hosted privacy-minimized logging. | Add logging backend/runbook before production. |

## Security Scan Status

PR #84 completed a focused Codex Security standard scan and this audit carried
forward two production-readiness findings: database pricing references could
include local packs across workspaces, and legacy direct job artifact downloads
were not bound to workspace/session ownership. PR #88 resolves the database
pricing-reference finding for DB/platform mode; PR #91 resolves the legacy
direct job artifact download finding for hosted/database/platform/deploy mode.

For this PR, the Codex Security plugin setup workspace was opened in standard
whole-repo mode, but no new scan result was produced because the app-side Start
Scan step timed out in this turn. Treat this audit as a manual/static
architecture review plus the PR #84 scan findings, not as a fresh completed
Codex Security scan. Before hosted/protected/deploy use, rerun the plugin scan and attach
the completed scan result to the implementation PR that removes or gates the
production-blocking fallback paths.

## Docs Cleanup Inventory

| Doc | Current issue | Classification | Required cleanup |
| --- | --- | --- | --- |
| `docs/README.md` | Index still frames KQAG as quote-specific local workflow and points operators to local/runtime storage. | Rewrite after architecture follow-ups. | Add this audit, then update ownership language as platform/workspace storage lands. |
| `docs/kqag-current-status.md` | References sample fixtures and local RC posture. | Outdated sections to rewrite. | Remove sample/demo happy path and align with Koncept Images workspace/private import path. |
| `docs/testing-plan.md` | Broadly valid, but should not allow demo-only smoke as product validation. | Keep, minor follow-up. | Add rule that product smoke cannot depend on product-visible sample/demo controls. |
| `docs/internal-uat.md` | Local runtime and sample-oriented UAT flow can be read as operator happy path. | Outdated doc to rewrite. | Replace with real workspace/private import UAT instructions. |
| `docs/internal-uat-deploy-auth-readiness.md` | Single-instance local runtime/OIDC scaffold does not match final platform-owned auth/workspace direction. | Outdated doc to rewrite. | Reframe as historical/local-UAT only or replace with platform-scoped launch checks. |
| `docs/internal-uat-login-and-pre-vps-dry-run.md` | Pre-VPS/fake OIDC dry-run docs are not sellable-product operator flow. | Outdated doc to rewrite. | Move fake/OIDC checks to isolated test harness docs only. |
| `docs/internal-uat-coolify-deploy.md` | Coolify/local roots conflict with DB/object-storage production direction if treated as current target. | Outdated doc to rewrite/delete. | Keep only as historical/internal-UAT adapter or remove once platform deployment docs exist. |
| `docs/platform-uat-smoke-runbook.md` | Local disposable smoke and fake/local paths conflict with no sample/fake product behavior. | Outdated doc to rewrite. | Convert to seeded workspace/private import smoke. |
| `docs/platform-launch-mode.md` | Directionally useful but still defers cloud storage and lives beside local fallbacks. | Keep, update after storage PRs. | Clarify no sample/local fallback in platform launch mode. |
| `docs/platform-scoped-storage-mode.md` | Useful DB boundary doc, but must converge with object storage and no local fallback. | Keep, update after storage PRs. | Add final object-storage and no local-pack fallback posture. |
| `docs/production-readiness-audit.md` | Valid PR #84 baseline, but Load Sample direction is stricter now. | Keep, superseded in part by this audit. | Link or fold in this audit before hosted/protected/deploy review. |
| `docs/current-cicd-status.md` | Current CI status doc remains valid; this PR does not change CI triggers/required checks. | Keep. | Update only if CI/CD gates change. |
| `docs/ai-basis-chat-test-playbook.md` | AI tests may rely on sample setup indirectly through smoke scripts. | Keep, update after sample removal. | Use seeded test workspace instead of product sample UI/API. |
| `docs/pricing-catalog-import.md` | Import behavior remains useful, but local/bundled fallback direction must be tightened. | Keep, update after pricing isolation PR. | Document workspace-scoped pricing import as normal path. |
| `docs/privacy-pdpa-gdpr-baseline.md` | Still valid baseline. | Keep. | Revisit before production legal launch. |
| `docs/pr-checks/quote-generator-pr-checklist.md` | Does not yet require no product-visible sample/demo path. | Keep, update follow-up. | Add Load Sample/local fallback checklist item. |
| `docs/agent-playbooks/*` | Agent workflow docs, not product docs. | Keep. | No product cleanup needed. |

## Internal Alpha Blockers

- Product-visible Load Sample UI and sample fixture wording.
- Product `/api/samples` routes and JS handler that create fake setup success.
- Playwright smoke/stress paths depending on the Load Sample product control.
- Database/platform pricing reference mode listing or resolving local packs.
- Generation-time profile layout/default resolution from local profile packs.
- AI/local starter draft fallback returning fake success in hosted modes.
- Missing object-storage path for generated artifacts and uploaded assets.
- Missing backup/restore/rollback evidence for DB plus artifact storage.
- Docs that tell operators to use sample/demo/local data as the happy path.

## Production Blockers

- All hosted/protected/deploy blockers above.
- SQLite/database BLOB artifact mode is not the final production storage model.
- No production object storage for profile layout workbooks, imported assets,
  uploaded images, generated XLSX files, or optional PDFs.
- No final hosted logging/monitoring and retention runbook.
- No proven restore drill covering DB metadata and object assets together.
- No final platform-owned auth/workspace/session implementation acceptance.

## Safe Deletion Candidates

Nothing should be deleted in this audit PR.

After seeded test setup exists, these become good deletion candidates:

- Load Sample button and sample fixture wording in product HTML.
- `DEFAULT_SAMPLE_ID`, `setSampleDetails()`, sample DOM wiring, and click
  listener in product JS.
- `/api/samples` and `/api/samples/{id}` product routes.
- Product-facing `sample_dir`, `list_samples`, and `load_sample` helpers.
- Playwright sample-click setup and sample restore assertions.
- Docs that instruct real operators to use sample/demo data.
- Sensitive fixture allowlist entries that exist only for product-reachable
  sample data.

## Recommended Implementation PR Sequence

1. Remove Load Sample completely from product UI/routes/JS/docs and replace
   test reliance with test-only seeded setup.
2. Remove or strictly gate local and bundled pricing-reference fallback in
   database/platform/deploy mode; add workspace isolation tests.
3. Resolve profile layout/defaults from workspace-scoped DB/object-storage
   assets instead of local profile packs.
4. Legacy direct job artifact downloads:
   completed in PR #91 by disabling them in protected hosted/database/platform/deploy modes.
5. Convert AI/local starter draft fallback into explicit local/test-only
   behavior; production should fail safely instead of returning fake success.
6. Delete proven dead compatibility/sample/demo code after tests no longer
   depend on product-visible paths.
7. Add object storage for generated artifacts, uploaded assets, imported
   references, and profile layout workbooks.
8. Add backup/restore/rollback operations readiness and drill evidence.
9. Rewrite local/demo-heavy docs into platform/workspace/private-import docs.
10. Run platform-side production readiness and security review.

## Validation Expectations For Follow-Ups

Any implementation PR that removes or gates fallbacks should run the smallest
targeted local validation plus the affected product paths from
`docs/testing-plan.md`. At minimum:

- Unit tests for the changed route/storage/helper behavior.
- Tests proving database/platform mode cannot read local pricing/profile packs.
- Tests proving product UI/API no longer exposes Load Sample.
- Playwright smoke that seeds a workspace through test-only setup rather than
  a product-visible sample control.
- `python scripts/check_production_readiness.py` to confirm readiness blockers
  decrease only when the actual blocker is fixed.
- `python scripts/scan_sensitive_fixtures.py` to keep private data out of Git.
