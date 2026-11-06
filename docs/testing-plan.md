# Testing Plan

## Purpose

Every code change must test the features it can affect. A small edit can use focused checks, but the checks must cover the real affected surfaces instead of only proving a demo fixture still works.

## User-owned company profiles

Saved profile access is enforced by `webapp/profile_access.py` and the database
storage adapter. New HTTP saves and imports create personal copies with opaque
IDs; explicit updates and deletes carry the listed revision. Members can read
shared profiles and export or copy them. Only administrators can mutate shared
profiles or change ownership. Pricing Reference permissions remain separate.
Unmapped legacy rows retain workspace reads and administrator writes pending an
explicit ID-to-owner mapping. Invalid metadata is unavailable, including to admins.
Internal-password sessions require database storage for saved company profiles.
With local JSON storage, saved profile lists are empty and save, delete and export
are refused; templates, pricing references and quote sessions retain local behaviour.
Saved default/layout readers also omit local company profiles in this mode.

Run the focused offline suites before the existing profile, auth and publication
suites. Set `SQAG_DISABLE_DOTENV=1`, `PYTHONDONTWRITEBYTECODE=1` and direct all
temporary files to the approved scratch directory. No provider or live DB is used.

```powershell
python -m unittest tests.test_profile_permissions tests.test_profile_access_storage tests.test_profile_access_backfill tests.test_profile_publication_continuity tests.test_profile_ui_permissions
python -m unittest tests.test_profile_roundtrip tests.test_profile_layout_safety tests.test_startup_profile_recovery tests.test_internal_password_auth tests.test_internal_password_auth_webapp tests.test_internal_password_identity_continuity tests.test_quote_publication_projection tests.test_publication_storage_adapters tests.test_publication_proof_reasons tests.test_publication_owner_subjects tests.test_postgres_metadata_storage tests.test_runtime_privilege_contract tests.test_quote_flow_reliability tests.test_pricing_spatial_context tests.test_pricing_import_telemetry
python scripts/validate_dynamic_pricing_reference_rules.py
python scripts/scan_sensitive_fixtures.py --fail-on-review
node .github/scripts/plain-text-check.cjs
node --check webapp/static/app.js
git diff --check
```

The new suites cover role/state decisions, filtered lists and assets, same-label
copies, exports/imports, denied effects, stale writes, shared mutation, metadata
hash exclusion, run-evidence linkage, dry-run purity and reversible offline maps.
The JS harness checks existing control states and invisible request fields only.
The profile suites also check local internal-login boot over HTTP and refusal
without saved-profile reads or writes, plus member share/unshare/transfer denial
over HTTP and both database artifact adapters, with unchanged persisted access.
Backup/restore cleanup reads each synthetic profile's current revision from the
public company-profile list before deletion. Run
`python -m unittest tests.test_live_db_object_backup_restore_verifier` for SQLite
BLOB/in-memory object cleanup, changed revisions, lookup failure and stale-read
refusal. Lifecycle fault tests must supply the public revision to reach their
intended remote-delete/rollback boundary. PostgreSQL fakes include the profile
payload pre-read; real PostgreSQL privilege/backup coverage still requires CI's
disposable PostgreSQL service.
Qualify full saved-quote reopen/download after delete, transfer and unsharing on
the final installed candidate. Verify changed pricing authority, evidence tampering,
wrong quote owner/workspace and stale commercial revisions still deny delivery.
Synthetic unit tests alone do not qualify that full historical journey, real
PostgreSQL/object storage, installed/browser behaviour or owner acceptance.

The offline mapper accepts an inventory with `workspace_id` and `profiles`, and a
manifest with the same workspace plus `assignments` containing `profile_id` and
`access` objects. Access contains `version: 1`, `owner_user_id` and `visibility`
(`user` or `shared`). Unresolved, malformed or deleted targets block mapping.
`python scripts/backfill_profile_access.py --inventory inventory.json --manifest mapping.json`
lists IDs and visibility reductions without writing. Add `--apply --output mapped.json
--receipt rollback.json` to create separate offline files. Use `--inventory mapped.json
--rollback rollback.json --apply --output restored.json` to restore access only;
intervening changes block restoration. Treat inventories and rollback receipts as
private operational material. The script cannot read or write a database; any live
inventory, mapping, cutover or rollback requires separate exact owner approval.

## Required Impact Pass

Before finishing a change:

- Identify the affected user-facing features, APIs, scripts, data files, generated outputs, and security boundaries.
- Run or add tests for every affected feature path.
- Prefer targeted tests first, then broaden when shared behavior, prompts, parsing, pricing, persistence, export, auth, or CI behavior changes.
- If an affected feature cannot be tested locally, document the reason and the manual or CI check that must cover it.
- Do not replace a missing general test with a demo-only fixture check.

## Baseline Checks

Use the smallest relevant subset for the change, then broaden as risk increases:

```powershell
git diff --check
node --check webapp\static\app.js
python -m py_compile webapp\server.py webapp\postgres_migrations.py scripts\migrate_sqag_storage.py scripts\preflight_sqag_migrations.py scripts\generate_quote.py scripts\live_ai_basis_chat_smoke.py
python scripts\validate_local_pdf_dependency_usage.py
python -m unittest tests.test_internal_password_auth tests.test_internal_password_auth_webapp
python -m unittest discover -s tests
npm run playwright:ai-stress
npm run playwright:smoke
```

## Button sizing and placement checks

- Run `python -m unittest tests.test_button_standard tests.test_quote_flow_reliability`
  for the shared button CSS contract and PDF/Excel ready-dialog order and actions,
  including repeated Excel/PDF/Excel switches with separating whitespace nodes.
- In a synthetic local browser session, check Quote Basis row check/X/Re controls,
  Company Profile Load/Save New/More Actions, and PDF ready Close/Open PDF at
  1280 px and 390 px. Capture each group before and after; check 32 px review
  squares, 40 px standard controls, 8 px gaps, 14 px standard text, 16 px padding,
  and 96 px minimum standard widths. Profile action SVGs retain their 15 px width;
  the desktop action column is 168 px and mobile Load/Save New use 4 px inline
  padding to keep icons and text inside their buttons. At 640 px and below all
  targets are 44 px high and review controls are 44 px wide. Check 640 px and
  641 px boundaries.
- Use keyboard Tab to verify visible 2 px green focus outlines with 2 px offset,
  no clipped rings or horizontal overflow, and Close before Open PDF in DOM and
  Tab order. Verify PDF open/close, Excel order restoration, row include/exclude
  and revision, and profile load/save/menu actions. Other buttons and menu items
  retain their existing styles. Browser geometry complements the text contract.

## Coverage Matrix and CI Scope

The tables below describe coverage selection, not passing test receipts. The
read-only contract `python -m unittest tests.test_ci_coverage_matrix` compares
them with `package.json`, `.github/workflows/ci.yml` and scope-based unittest
skips. It runs through the existing unittest discovery command. No workflow or
branch-protection setting is changed by this contract.

| Feature | Test or suite | Execution and remaining limit |
| --- | --- | --- |
| First Customer entry, selected reference, currency and tax | `tests.test_webapp.WebappServerTest.test_static_next_to_customer_initializes_untouched_commercial_defaults`; `tests.test_webapp.WebappServerTest.test_static_authority_profile_race_discards_late_request_completely`; pricing-startup browser mode | Python discovery and full app smoke; focused visible-only startup mode is manual. State assertions alone do not prove visible labels. |
| Save, reload, Modify, interrupted duplicate and restored pricing authority | Full app smoke; full alpha smoke; saved Modify/restoration Python test | CI selects the maintained full app and alpha commands plus an isolated recovery command. Discovery includes the saved Modify/restoration test; browser selection is not a passing runtime receipt. |
| Upload, basis review and generated XLSX | `tests.test_generate_quote`; `tests.test_quote_flow_reliability`; app smoke | Python discovery and full app smoke use synthetic inputs. No paid inference or customer files. |
| Durable publication, protected XLSX/PDF and rollback | `tests.test_quote_publication_projection`; `tests.test_object_pdf_completion`; `tests.test_postgres_metadata_storage` | Python discovery; publication tests use local databases and synthetic object/query adapters. Disposable PostgreSQL migration coverage does not qualify the full publication journey or live Neon/R2. |
| PostgreSQL migration and runtime privileges | `tests.test_postgres_migration_ledger`; `tests.test_runtime_privilege_contract` | Discovery with the disposable PostgreSQL 17 service; missing local service is a dependency skip, not hosted readiness. |
| Workbook layout and actual rendered PDF | `tests.test_generate_quote` native LibreOffice cases | Discovery after CI converter/font readiness checks. Local renderer skips leave fidelity unverified; XLSX remains the default master. |
| Staff access, CSRF and protected routes | `tests.test_internal_password_auth`; `tests.test_internal_password_auth_webapp`; internal-auth browser suite | Discovery and direct synthetic browser command; no real credentials or identity-provider calls. |
| AI request routing, privacy and errors | `tests.test_openrouter_gateway`; `tests.test_gateway_operation_lifecycle`; ai-stress browser suite | Discovery and mocked browser transport; request shape does not establish live provider eligibility. |
| Installed source/image/asset identity | Operator comparison against the accepted candidate | Not established by event-head CI. Installation checks require their own read-only evidence; no deployment is performed here. |
| Coverage selection and omissions | `tests.test_ci_coverage_matrix` | Discovery rejects command drift, undocumented package browser scripts and unexplained scope-based skips. It does not execute browser journeys or enforce GitHub required checks. |

### Declared CI test commands

These are the exact test steps in the `validate` job. `prerequisites` means
`always() && steps.runtime_prerequisites.outcome == 'success'`; `none` means no
step-level condition or scope override. Command, condition and scope changes
require review of this table, including any intentional narrowing. Extra shell
wrappers or failure suppression in these steps are rejected by the contract.

<!-- coverage-ci:start -->
| Step | Command | Scope | Condition |
| --- | --- | --- | --- |
| Run Python tests | `python -m unittest discover -s tests` | none | none |
| Run AI basis chat stress smoke | `npm run playwright:ai-stress` | none | prerequisites |
| Run synthetic internal password auth smoke | `npm run playwright:internal-auth` | none | prerequisites |
| Run app smoke test | `npm run playwright:smoke` | none | prerequisites |
| Run isolated app recovery smoke | `npm run playwright:smoke -- --recovery-only` | none | prerequisites |
| Run isolated live generation terminal smoke | `npm run playwright:smoke -- --terminal-only` | none | prerequisites |
| Run minimum internal alpha browser smoke | `npm run playwright:alpha-smoke` | none | prerequisites |
<!-- coverage-ci:end -->

### Browser suite inventory

Every `playwright:` package script is declared, including setup and manual tools.
An `absent` command is deliberately outside direct CI selection and needs a
reason. Indirect rendered checks reached through Python are not counted as a
direct full-suite run. The maintained full suites and isolated recovery mode are
selected by CI. Selection alone does not establish a passing browser receipt.
Do not remove old failing fixtures or narrow their assertions to pass.

<!-- coverage-browser:start -->
| Suite | Package script | Script definition | CI command | Reason or limit |
| --- | --- | --- | --- | --- |
| browser-install | playwright:install | `playwright install chromium` | absent | Setup tool; CI installs Chromium with dependencies through npx. |
| download-confirm | playwright:download-confirm | `node scripts/playwright-download-excel-confirm-regression.mjs` | absent | Focused manual download regression; not a direct CI command. |
| app-full | playwright:smoke | `node scripts/playwright-smoke.mjs` | `npm run playwright:smoke` | Broad lifecycle plus isolated G3 pricing/current XLSX. Expired-job and Customer Next commit checks run in the separate recovery step. |
| app-recovery | playwright:smoke | `node scripts/playwright-smoke.mjs` | `npm run playwright:smoke -- --recovery-only` | Separate process/server/state for interrupted saves, terminal and expired jobs, and pricing-reference commit on Customer Next; avoids broad-run stale-draft contamination. |
| app-terminal | playwright:smoke | `node scripts/playwright-smoke.mjs` | `npm run playwright:smoke -- --terminal-only` | Fresh quote per case; live needs-review, blocked, failed and interrupted polling without reload, separate from broad/recovery state. |
| pricing-startup | playwright:smoke | `node scripts/playwright-smoke.mjs` | absent | Manual focused command: npm run playwright:smoke -- --pricing-startup. Visible first-entry check, not a full lifecycle. |
| customer-summary | playwright:customer-summary | `node scripts/playwright-customer-summary.mjs` | absent | Synthetic Customer summary matrix: first-entry visible reference, currency, tax, repeated entry, reload recovery, pending selection and held profile responses; manual command, not yet wired into CI; wiring is tracked separately. |
| ai-stress | playwright:ai-stress | `node scripts/playwright-ai-basis-chat-stress.mjs` | `npm run playwright:ai-stress` | Mocked basis chat and interruption checks; no live provider readiness. |
| internal-auth | playwright:internal-auth | `node scripts/playwright-internal-password-auth.mjs` | `npm run playwright:internal-auth` | Synthetic local staff admission and denial. |
| screenshots | playwright:screenshot | `node scripts/playwright-smoke.mjs --screenshots` | absent | Manual broad-run visual tool using Playwright Chromium; skips the isolated G3 tail. Default full-smoke CI still runs G3. |
| alpha-full | playwright:alpha-smoke | `node scripts/playwright-alpha-smoke.mjs` | `npm run playwright:alpha-smoke` | Native saved edits, reopen, workbook/PDF and protected download checks; renderer dependencies required. |
| owner-uat | playwright:owner-uat-basis | `node scripts/playwright-owner-uat-basis-repair.mjs --lines=24 --latency-ms=800` | absent | Operator tool; bounded rendered variants also run indirectly in focused Python tests. |
<!-- coverage-browser:end -->

### Intentional scope skips and receipts

No validation-scope exclusions are declared. This empty table is checked against
all test sources for scope-based decorators.
The exact predicate and reason are checked without importing or running the
affected tests. Host/dependency skips (Node, Playwright, PostgreSQL, renderer,
fonts, WebP, symlink and filesystem support) are separate runtime limits; they
must appear in the run receipt and cannot silently become scope exclusions.

<!-- coverage-skips:start -->
| Test | Predicate | Reason |
| --- | --- | --- |
<!-- coverage-skips:end -->

For each candidate qualification, retain the exact command and candidate identity,
test count, failures/errors, skip count and named skip reasons. Report zero
unexpected skips only after comparing the actual runner output with the scope
table and available dependencies. Required PostgreSQL/native-rendering checks
need zero skips on their provisioned CI host. This static contract is not a
runtime skip-receipt collector. Full recovery, installed identity, live provider
eligibility and hosted storage remain unverified until their own evidence exists.
When workflow selection changes, update this matrix and
`docs/current-cicd-status.md` together. Required-check/ruleset changes belong to
the owner-approved CI lane.

## Feature Coverage Guide

- Quotation API contract proposal: Run
  `python -m unittest tests.test_quote_api_contract`. The standard-library-only
  module reads the contract document/spec and uses in-file fake gateway, clock
  and storage objects. It checks example schemas, recursive closed fields,
  forged authority/query rejection, estimate wording, decimal money, unresolved
  nulls and labelled subtotals, optional admin-set tax, safe error shapes,
  idempotency replay/conflict/expiry, owner isolation, confirmation, closed state
  transitions, endpoint agreement, JSON/XLSX/PDF contracts and sandbox limits.
  Exact service request fields, required confirmation revisions, per-operation
  authentication, staff-session/CSRF reservations and authenticated staff-row
  pricing on the shared endpoint are pinned against the actual schemas. Direct
  boundary tests cover money/rates, units, notes and polling independently of
  arithmetic. Migration wording, priced revision visibility and response evolution
  are checked too. Run `python -m unittest tests.test_sensitive_fixture_scan` for
  the path/category-specific documentation exceptions and secret-marker controls.
  It imports no app runtime and proves no real HTTP admission, authentication,
  durable queue/recovery, media limits, artifact privacy/fidelity or retention
  execution. Those require later implementation tests and independent review.
  No CI command or required-check setting changes; existing discovery includes
  this module. Browser and owner UAT are not required for this docs/test slice.

- Pricing-reference startup and first Customer entry: Run
  `python -m unittest tests.test_webapp.WebappServerTest.test_static_next_to_customer_initializes_untouched_commercial_defaults tests.test_webapp.WebappServerTest.test_static_authority_profile_race_discards_late_request_completely`
  and `node scripts/playwright-smoke.mjs --pricing-startup`. The bounded browser
  mode uses isolated synthetic local data and checks the existing selected name,
  source badge, reference currency and tax before Settings is opened. Retain
  non-default currency/tax, repeated entry, saved/recovered/pending ownership,
  first-load rendering and stale-response negative controls. No live provider
  or hosted service is used.

- Synthetic hosted-validation verifier: Run
  `python -m unittest tests.test_internal_alpha_hosted_validation_verifier`.
  Keep each fixture in a unique short `iav-` temporary directory under the
  existing owned test root, so nested verifier run directories do not exhaust
  Windows path capacity. Retain the metadata-only output assertions, blocked
  readiness checks and registered cleanup. Configuration metadata must include
  the current server auth variable names and classify staff aliases, identity
  bindings, the login password and the signing secret as host-secret-manager-only;
  no values are emitted. These checks use synthetic local
  services and do not establish live hosted readiness.

- PDF references for quote analysis: run the focused draft request and gateway
  tests, then run the internal-password browser harness to check file admission.
  The app accepts up to 48 MiB per PDF, 128 MiB across reference files and 96 PDF
  pages per analysis. Draft save and analysis carry base64 JSON, so test both
  requests through the deployed ingress with a synthetic PDF over 16 MiB before
  rollout. Check that a multi-batch analysis includes every source page, a failed
  batch returns a support reference without a draft, and usage distinguishes
  measured tokens and OpenRouter-reported cost from sends with unavailable
  usage or cost. Keep this check separate
  from customer documents and the live AI provider.

- Quote basis, `Re`, `Ask For Changes`, AI parsing, or basis UI state: follow `docs/ai-basis-chat-test-playbook.md`, run the mocked tests, and run `npm run playwright:ai-stress` when practical.
- PDF batch measurement retention: run `python -m unittest tests.test_basis_quantity_retention`.
  The offline transport probe covers single-call and observation/synthesis paths with
  explicit object quantities and units, lossy observations, and unmeasured descriptive
  rows. Prompt checks require object/location associations, separate dimensions,
  coherent linked measurements and no invented defaults. Compact notes keep quantities
  and units with their object/location within a per-page budget derived from the batch
  response limit, reserving 20% for JSON overhead and escaping. Check full and partial
  batches and a changed limit. Synthesis permits floor area derived from stated booth
  dimensions and services genuinely priced per lot in the catalogue. Fake responses establish
  request and normalisation behaviour, not live model takeoff accuracy.
- Default booth-dimension reminders: run `python -m unittest tests.test_basis_dimension_contract`. Preserve structured row IDs, measurements, catalogue authority, notes, order and confidence through full Analyse, finalisation and review. Repeated reminder insertion must be stable; distinct physical rows sharing a catalogue item remain separate, and unmeasured rows must not display an invented `1 lot`.
- Provider measurement provenance: run `python -m unittest tests.test_basis_source_provenance`. Missing raw row IDs may bind only to one existing basis identity with exact full-detail text and coherent explicit raw measurements. Preserve the raw source unit before catalogue projection; ambiguous, missing or conflicting measurements must not offer an unactionable candidate. The rendered carrier `node scripts/playwright-owner-uat-basis-repair.mjs --source-provenance --fresh-analysis --lines=0 --latency-ms=0 --currency=SGD` covers explicit Possible Match admission, distinct physical rows sharing a catalogue, Output, saved reopen and protected XLSX. A saved blank seal/ledger is never upgraded from the current displayed unit; fresh analysis is required to capture newly recoverable provenance.
- Pricing reference import, catalog matching, currency/tax metadata, or template parsing: add/update parser and normalization tests, then run the relevant `tests.test_webapp` and `tests.test_generate_quote` cases.
- Pricing-pack reads and numeric admission: Run `tests.test_codeql_boundaries`
  and the exact-pack, quote-basis and primary-order cases in `tests.test_webapp`.
  Preserve valid nested catalogues and reject escaped manifests/catalogues before
  reading them. Linux CI covers real links; Windows controlled resolution tests
  retain the existing native-link qualification limit. Generator brief writes
  must use the private atomic writer, with mode 0600 checked on POSIX.
  Generic resolution, listing, public summary/detail and asset access must share
  the same fence. Unsafe entries cannot disclose their manifest or substitute a
  default catalogue; safe listing peers remain available, unrelated errors stay
  visible and valid Windows directory aliases retain their public spelling.
  Canonical string-prefix admission must include the directory separator, retain
  Unicode path spelling and keep the resolved-path checks. Exact-parent and nested
  paths must pass the same unconditional prefix admission. Cover both directory
  and asset siblings sharing a prefix before Path resolution, nested Unicode
  catalogue filenames and resolution failures.
- Diagnostic JSON privacy: Run `tests.test_codeql_diagnostic_privacy` with mocked
  providers and configuration. Live-smoke results expose fixed outcome metadata
  while keeping model and provider exception text internal. Readiness output may
  name required environment keys but must not echo their values. These tests make
  no paid provider calls or live network probes.
- Reusable company-profile import/export: Run `tests.test_profile_roundtrip` and
  the existing profile import, overwrite and export endpoint cases. Applying an
  imported profile must preserve quotation analysis, reviewed rows and workflow,
  while invalidating existing downloads even when only the layout changes.
  Database and object-storage exports must carry the exact owning profile's
  workbook, survive reimport, respect workspace isolation and refuse an unavailable
  required layout. A normal profile save must retain its existing behaviour.
- Output pricing review, manual price detection, subtotal, Excel download, or quote generation: run generator unit tests plus an app smoke test that reaches output.
  Run QuantityLayoutTest for quantity/unit output: decimal and integer tokens,
  longer units, complete centred text at the selected font size, unchanged amounts,
  shared quantity/description row budgets and page-boundary admission. Reject a
  numeric token or complete line-item block that cannot fit the selected layout.
  Its native LibreOffice regression checks rendered digit ink, since complete PDF
  text extraction can conceal a clipped leading digit. Keep the short quantities,
  selected profile column widths, print area and existing fonts unchanged.
  Acceptance and signature text must preserve every plain or rich paragraph and its
  emphasis. Test visible left/right template widths, narrow and wide unbroken text,
  combining marks, literal formula prefixes, larger fonts, dynamic signature
  offsets, exact fitting/one-row-over admission and oversized continuation. Keep
  the existing short-text positions, optional-text capacity, final labels, logos,
  print-area and font controls. Run AcceptanceSignatureLayoutTest; its native
  LibreOffice test must run in CI and checks actual glyph-box paint plus signature
  clearance. Text extraction alone cannot detect the retained clipping failure.
  Local renderer skips remain explicit gaps until native rendered proof exists.
  Exercise the actual web payload mapper into the workbook, using nested and flat
  plain acceptance/signature fields, CRLF/internal blank lines, literal HTML-like
  text and formula prefixes. Selected acceptance/signature style fonts must remain
  explicit in the master and conversion copy, with emphasis and master bytes intact.
  Keep base/Unicode-mark sequences together across rich-run boundaries, including
  nonspacing/enclosing marks with combining class zero and spacing marks. Native
  rendering must also check a larger selected font's actual PDF size and paint.
  PDF conversion must preserve the XLSX master bytes. Resolve DrawingML Latin
  theme-font references from that workbook's internal theme in a temporary
  conversion copy; retain explicit fonts, run sizes/emphasis and every other
  workbook part. Test custom major/minor fonts, explicit-font/no-theme inputs,
  unresolved/external/ambiguous themes, converter input and temporary cleanup.
  The real LibreOffice fidelity regression must measure company-header fonts
  as well as geometry, so a monospace substitute cannot pass as equivalent.
  A Calibri-compatible Linux font still does not prove exact Excel typography
  or pagination; native Excel and installed PDF comparisons remain required
  before claiming that fidelity.
  Partially formatted SpreadsheetML text runs must also materialise their
  workbook-default font and size in the conversion copy. Cover inline and
  shared strings, explicit run overrides, theme schemes and missing defaults;
  preserve plain cell inheritance and the XLSX master. The real renderer check
  must reject a monospace font for the client text, independently of the header.
  A review result with an explicit empty or diagnostic-only file list must save
  as review without entering atomic workbook publication. Test this with the real
  generator, migrated database and synthetic object backend, then resolve pricing
  explicitly and verify a real durable XLSX after owned scratch cleanup. Completed
  results with missing artifacts must still fail storage validation; partial review
  results declaring XLSX retain artifact validation. Legacy results without file
  declarations retain guarded output-directory discovery only until the saved-result
  contract is migrated; the publication owner must preserve that consumer meanwhile.
  Export diagnostics carry a server-created row token and the original source row
  ID. Test exact correspondence against the captured final brief, including
  reordered, missing, extra, duplicate and formula-prefixed identities. CSV prices
  cannot create authority. Structured results preserve manual/quote-currency FX
  and demote rejected catalogue claims before direct/resumed failure, review or
  completion adoption. Current malformed results must keep existing rows and
  refuse new artifact adoption. A direct current completion/review missing its
  structured contract must fail. Only saved-job resume may retain usable current
  rows for a legacy-format result; an empty legacy resume must not invent readiness.
  Current hard-generator/pre-generator failures may legitimately omit diagnostics:
  preserve the original failure/reference/return code and rows, with no new files.
  Missing diagnostics alone never identify an old runtime. New generation jobs
  persist their output-row contract marker before dispatch; a restored starting
  operation upgrades before its dispatch, while a present invalid marker rejects.
  Only an already-running saved job with no marker uses legacy row retention.
  Cover missing/valid/invalid marker across direct, starting and running/resumed
  operations, completion/review/failure, and present/absent result contracts.
  Run `tests.test_webapp.GenerationOutputRowsRegression`,
  `tests.test_webapp.GenerationDiagnosticReadFailureTest`,
  `tests.test_generate_quote.GeneratorDiagnosticIdentityTest`,
  `tests.test_quote_flow_reliability` and `tests.test_owner_uat_contract`.
  The generation-output harness builds immutable production source once per
  invocation and evaluates it in a fresh VM for each case. Preserve all nine
  tests, their assertions and matrices, and the 30-second subprocess limit.
  Duplicate must preserve current Output edits/subsets independently of its Reset
  snapshot, strip source-bound seals/catalogue claims, and obtain target grants
  only through explicit selection/Confirm. Cover save/reopen, manual FX,
  exclusions, wrong scope/quote/digest/unit, stale revisions, asynchronous
  operation/auth-scope changes, and genuine guarded XLSX bytes. Retain the
  original quote unchanged. The rendered resume check is
  `node scripts/playwright-owner-uat-basis-repair.mjs --resume-export --fresh-analysis --lines=0 --latency-ms=0 --currency=SGD`.
  It holds the accepted browser poll, lets the real worker commit, reloads into
  ordinary resume, and verifies automatic save plus fresh protected bytes without
  another generation, publication or provider call.
  Local synthetic tests do not establish the cause or
  job/artifact/counter outcome of a historical installed failure.
  Output's Use reference price action must synchronise the draft, verify any
  existing server grant, and request a new grant only through conditional pricing
  selection. Test missing/copied catalogue keywords against uniquely included
  basis identities, current reference checks, refusal and stale/authentication
  responses, including context changes inside the production HTTP helper's CSRF
  refresh and valid advancing selected-row ledger acknowledgements. Keep the
  edited Output subset, manual FX prices and Reset snapshot;
  never rebuild all basis rows as a side effect of selecting one price. Run
  `python -m unittest tests.test_output_reference_selection` for the closure
  matrix and a rendered duplicate, target grant, reopen and protected XLSX check.
  Before each reference-outage baseline, the driver finishes the old document's
  pending draft-save owner. Queued debounce work is synchronised through the
  existing save operation; in-flight acknowledgements are awaited before either
  browser recovery string or durable metadata is captured. Failed saves or a
  changed authenticated target refuse the checkpoint. No quiet delay, timestamp
  normalisation or weakened byte comparison is used. The deterministic startup
  pack retains its prerequisite/authority matrix and proves the former prefix
  captures too early, while the completed boundary preserves both strings.
  Startup must finish pricing-reference hydration before admitting saved quote
  rows or releasing the boot persistence fence. An unavailable, malformed or
  throwing reference load must preserve the exact browser recovery payload and
  durable quote, retain the loading hold, and dispatch no saved operation.
  Run `tests.test_startup_profile_recovery` for the prerequisite matrix. The
  rendered Output-selection regression injects a local reference-list outage,
  then reloads successfully and verifies retained catalogue/manual authority,
  FX, edited subset and Reset snapshot before protected XLSX generation.
  Include successful responses with malformed reference entries, duplicate
  identities and missing saved references. Hold current-scope priced recovery
  unchanged, while allowing a genuinely empty new workspace to open Settings.
  Matching reference headers also need the catalogue content used by saved
  claims. Exercise missing, malformed, duplicate and inconsistent items under
  an unchanged reference basis across saved rows and Reset. Keep legitimate
  changed-basis commercial review and supported nested snapshot restoration.
- Upload intake, PDF/image handling, sample loading, browser refresh persistence, or local file state: run server tests for request validation and a rendered Playwright flow for the affected upload/persistence path.
- Frontend layout, controls, settings, privacy page, or responsive behavior: run syntax checks and a browser/Playwright visual interaction loop on the changed screen.
- Auth, permissions, retired OIDC refusal, cookies, CSRF, file download boundaries, or secret
  handling: run `tests.test_internal_password_auth`,
  `tests.test_internal_password_auth_webapp`,
  `tests.test_internal_password_identity_continuity`, focused Platform/session tests, and
  then the full unit and Playwright suites. Internal identity changes must cover
  strict bounded JSON parsing, bidirectional subject/email substitution,
  role/workspace changes, policy fingerprinting, restart, route denial without
  a cookie, and browser positive/negative flows. Retired local login/callback
  tests must prove safe refusal and no provider calls even with former settings.
  Retain old-cookie rejection, logout state-cookie cleanup and exact historical
  owner IDs. Independent Platform behavior must use local synthetic adapters only.
- CI/CD, package scripts, dependency setup, or workflow files: validate the YAML/script syntax when possible, run the nearest local command, and update `docs/current-cicd-status.md`.
  Run `python -m unittest tests.test_ci_coverage_matrix tests.test_ci_browser_scope tests.test_smoke_wiring` for command selection, trace coverage and executable wiring controls.
  All six browser steps have runtime-prerequisite gates. The full app smoke
  includes isolated mocked-provider pricing/current XLSX after reload/recovery.
  Separate recovery and live-terminal steps own fresh state; each live terminal
  case starts its own server, browser and write budget. Python discovery
  runs fresh and saved Modify/restoration pricing cases without a scope override.
  The owner contract invokes `--g3-pricing`.
- PostgreSQL migration manifest, ledger, preflight, or operator command: run
  `tests.test_postgres_migration_ledger` against the isolated CI PostgreSQL
  service and require fresh apply, complete schema, exact checksums, no-op
  replay, drift refusal, concurrency serialization, read-only preflight, and
  failed-transaction rollback evidence. For the telemetry attempt evolution,
  also prove the 009-to-010 prefix transition, renamed historical CHECK
  discovery, successor constraint name and validation, row preservation, and
  refusal of missing, duplicate, unvalidated, drifted, premature, and stale
  attempt constraints on disposable PostgreSQL 17. Never point this CI test
  at a provider or production database.

- Browser harness startup and cleanup: run `python -m unittest tests.test_smoke_harness_lifecycle tests.test_owner_harness_lifecycle`. Preserve non-zero startup failures, handle already-exited children, and register exit listeners before signalling. Synthetic browser login must use its actual loopback origin; generated carriers must resolve shared helper imports from the maintained source.
- Missing AI pricing import configuration: run the Markdown missing-configuration test in `tests.test_webapp` and `tests.test_pricing_import_telemetry`. Assert zero sends, unsaved/non-saveable state, a configuration-required review layout, and matching support references. Keep transport, refusal and invalid-output negative controls distinct.

- AI operation lifetime: run `tests.test_gateway_operation_lifecycle` with the gateway, telemetry and import suites. Cover all six routes, missing/malformed configuration and credentials, success, refusal, response shape and JSON failures, logger exceptions, nested same-feature parents, concurrent threads, and immutable metadata batches. Keep exactly one terminal record per actual send and canonical zero-send validation. The G3 metadata fixture must admit source-backed synthetic metadata through real gateway admission while rejecting other operations and external network access.

- AI telemetry attempt numbering, pre-transport validation, provider fallback,
  or model privacy: run `tests.test_telemetry_producer`,
  `tests.test_openai_draft_request_contract`, and
  `tests.test_ai_basis_chat_stress`. Assert attempts count actual sends,
  request-validation events persist only the canonical zero-send tuple, and
  invalid model labels are projected before logs, summaries, fingerprints,
  and telemetry.
- Draft pricing authority binding, run-to-session forensic linkage, or live
  Settings selector refresh after a same-id pricing-reference save/delete:
  run the focused pricing-draft regressions in `tests.test_webapp`,
  `tests.test_forensics_feedback_retention`, and
  `tests.test_openai_draft_request_contract`, plus
  `node scripts/playwright-smoke.mjs --g3-pricing`. The browser mode starts a
  temporary local server and deterministic synthetic provider; it checks exact
  catalogue rebinding, unresolved custom rows, persisted quote state, safe
  forensic counts, feedback lookup, and immediate selector refresh without a
  live provider call. Also run `tests.test_generate_quote` to retain the XLSX
  boundary checks.
  Cover mixed identified/unidentified rows, duplicate IDs, and repeated text
  with differing quantities or units. Basis ownership must not transfer a
  selector to another row; alignment must recalculate trusted amounts and
  repeated normalization must preserve the result.
- Possible Match selection, AI Confirm ticking, quote-basis click responsiveness
  or Confirm Quotation Basis: run the `test_owner_uat_*` and `test_ledger_*`
  cases in `tests.test_webapp` and `npm run playwright:owner-uat-basis`. The
  rendered script uses the real server, UI, normalisation, persistence and XLSX
  path with a deterministic synthetic draft, a saved (`EXISTING`) quote, similar
  catalogue entries, a non-reference quote currency and injected request
  latency. It checks trusted catalogue authority, FX-correct quoted price and
  amount, review-only rows staying `???`, Output edits, reopen and protected
  XLSX amounts bound to their own workbook rows plus the totals block.
  Include a fresh Custom proposal with equivalent `pcs`/`nos` units and a
  saved catalogue `reference_section` different from its canonical section.
  Confirm must retain the server-admitted row and its authority by stable
  source ID through Output, save/reopen and XLSX; unidentified or unrelated
  rows still require section-compatible heuristic matching. The in-flight
  save regression uses `--fresh-analysis` to cover this without an initial reopen.
- Pricing trust is server state (the quote's `pricing_ledger`), never request
  JSON. A row keeps a catalogue price only while the server grants that exact
  row, item, reference digest and catalogue unit: an analysis match sealed by
  the server (`pricing_analysis_seal`, ingested into the ledger on the next
  save) or an operator selection recorded by `POST
  /api/quote-sessions/pricing-selections` against the row's current revision.
  Saves never grant: they bump the row revision on any change and permanently
  supersede a grant after a saved Exclude, Custom, removed row, other catalogue
  item, other pricing reference or incompatible unit. Manual and Included stay
  operator overrides; Use Reference Price restores only a live grant. The
  selected catalogue label is reference evidence, not additional independent
  row detail. Run `test_selected_reference_label_is_not_independent_detail`
  and `test_ledger_reference_only_label_selects_saves_and_reopens` in
  `tests.test_webapp` for canonical and leading-unit labels whose words also
  name another catalogue family, real server selection, save/reopen and final
  brief pricing. Additional conflicting detail, ambiguous identities, quantity
  divergence, incompatible units and non-Include basis lines stay refused.
  The `test_ledger_*` cases cover forged authority (variant flip, deleted authority,
  self-built catalogue authority), replay after a saved exclusion or
  replacement, stale revisions and concurrent tabs, an interrupted commit,
  legacy quotes (persisted catalogue prices kept, `HISTORICAL` rows never
  promoted), cross-user, cross-workspace and cross-quote isolation in sqlite
  database mode, and units: a quantity is reused only when both units count the
  same thing (`pcs`/`nos`, `m`/`m length`/`m run`, `sqm`, `sets`, `lot`); `10 pcs`
  is never `10 sets`, and count, lot or length is never area. Such rows stay
  unpriced for manual review; no conversion factor is inferred. The rendered
  script expects the `lot` video row to stay `???` and exactly one
  pricing-selections request at Confirm. Fresh Possible Match candidates use the same
  measured-unit and detail compatibility checks as Confirm; incompatible candidates
  are omitted without inferring a quantity conversion. The rendered script injects
  one synthetic pre-upgrade cached candidate and checks its explicit unit refusal.
  Compatible aliases retain their saved basis quantity and use the server catalogue
  unit and price. Quantity divergence refuses before any grant; original provider
  units survive draft finalisation and saved-ledger review. For timing evidence add
  `--measure-only --cpu-throttle=4 --catalog-extra=300` (optionally
  `--cpu-profile`); it reports Event Timing input-to-paint per click and the
  time until `Preparing Output` paints.
- The analysis seal attests only rows derived from the provider's result, in
  the unit the provider measured each row in (carried internally from the first
  normalisation, before rows re-unit to their catalogue items). Request or
  profile-default fallback rows are never sealed as analysis matches. Cover this
  through `POST /api/draft` with a deterministic provider stub, not only
  `normalize_ai_draft`.
- Database/object publication promotion publishes the staged outputs but keeps
  the draft and pricing ledger of any save that landed after staging, so a
  later saved Exclude or replacement is never undone by promotion. The
  rendered script's `--inflight-save` option starts a delayed draft save that
  is still in flight at Confirm and expects the single pricing-selections
  request to succeed (no false "changed in another tab" refusal).
## Manual price and restored search contract

Output defaults to editable reference prices, read-only FX and derived quoted unit prices.
The explicit Quote-currency override action stages an empty transient replacement;
only a valid cent-exact commit changes authority. Escape cancels. Catalogue no-op
and cancellation preserve authority and revision. Invalid editors block saves,
navigation and generation before effects. Resolve tagged overrides through Reference
price before switching quote currency. Preserve line-first FX rounding (1.01 x 3
at FX1.37 gives 4.15, while displayed unit is 1.38) and runtime multiply ties.
A new manual authority records `price_currency` only when it matches that quote
currency. Its value is not multiplied by FX again. Catalog prices and legacy
manual authorities without that field retain reference-currency conversion.
The server and XLSX generator reject mismatched tags and extra catalog fields.

Run `python -m unittest tests.test_owner_uat_contract` for the cross-boundary
regressions. Its rendered G3 fixture uses the native temporary directory to keep
Windows publication paths bounded; evidence stays under `_logs/browser/`.
It checks suggestion selection after Output, a distinct catalog positive
control, forged suggestion prices, FX editing and reopening, persisted visible
Dashboard search, protected download identity, and actual XLSX row amounts.
No live provider is used. Retain existing authority, generator and commercial
regressions and require complete exact-head CI, including native output.

FXUI-R01 through R11 are bound by the owner contract suite: explicit reference and
quote denominations, catalogue no-op/cancel, immutable C17 predecessor readback,
transient staging, exact precision, previous-currency admission, unchanged runtime
line-first rounding, rendered event/sort/mobile behavior, deferred save ordering,
and authority/current-publication protection. The immutable synthetic predecessor
in `tests/fixtures/fxui-c17-predecessor.json` was produced by C17 main
`eda4dd086b8e570632321a0b8e9f595ec6903a5b`, SHA256
`1db57ff326ad072ecef74f146c5c5c1965fe5d55c1028de0ff30dcf0ea5a0d99`.
Candidate tests consume those bytes unchanged in browser and server checks.
The rendered carrier also checks invalid editors before save/job/window effects
and consumes the protected generated XLSX, its persisted authority and SHA256.

Delayed generation synchronization must refuse keyboard editor opening while busy.
A transient invalid editor appearing across the await must block actual job
dispatch; cancel and retry must dispatch exactly one job. The shared dispatch
admission also covers saved-job retries and XLSX/PDF consumers. Verify desktop
heading text fits inside cells at 1440px and 1100px, retaining mobile readability.
Accepted large decimal prices must reopen as plain decimal text in both
denominations. Enter/Tab without changes must preserve exact authority, revision,
draft key, save count and artifact identity; precision-changing input still rejects.

## Regression Standard

### Fresh provider response admission

Run `python -m unittest tests.test_provider_response_admission` together with
`tests.test_basis_dimension_contract`, `tests.test_basis_source_provenance`,
`tests.test_openai_draft_request_contract`, `tests.test_ai_basis_chat_stress`, and
`tests.test_quote_flow_reliability`. Fixtures use synthetic data, denied outbound
sockets and explicitly mocked provider responses. Full Analyse must reject
inconsistent measurements or malformed scope before pricing and sealing, with a
support-safe error reference rather than a substitute local draft.

Cover canonical positive quantities, decimal-comma/percent/bool/nonfinite and
float underflow rejection, coherent prefix-only measurements, conflicting text,
structured and linked measurements, and unrepresentable derived prices. Keep
same-text physical rows with distinct IDs through review, actual server selection,
Output, save/reopen and protected XLSX. Coherent ID aliases and generic equivalent
units (including Unicode square metres) must preserve original provenance;
ambiguous/unbound identities reject the response without partial success. Blank
original measurement units never gain a catalogue grant from a later display unit.

Supplied arrays, objects and rows must have supported shapes, with no silent
material row removal. JSON admission accepts one bounded object and rejects
duplicate keys, array wrappers, extra objects and nested nonfinite numbers. Keep
the demonstrated fenced/trailing-prose chat consumer until its response contract
is explicitly changed. All six maintained gateway callers share shape/completion/refusal checks.
The retained legacy envelope parser tests cover historical consumers only.
Single-send analysis and pricing calls send once; text retries stay on the captured
route and never switch model, provider or response parser.
Confidence supplied by a fresh response is an integer from 0 to 100, including
0 and 1; fractions, percent strings and conflicting aliases reject. Saved/user
confidence and commercial-number normalisers remain compatible with their existing
consumers, and saved blank seals/ledgers are never upgraded. Identified items-only
and legacy basis shapes retain optional-field support; supplied structured basis
must own every supplied material item exactly once.

Existing genuinely empty-provider-row finalisation retains request rows only for
review; its analysis seal grants no catalogue price to those fallback rows. The
quote finaliser owns this recovery path until the recovery contract is explicitly
replaced. Cover its unpriced save/selection boundary separately from malformed
material responses, which must reject before any partial success.

### Full-draft Responses contract

Run `python -m unittest tests.test_openai_draft_request_contract` for full-draft
request changes. This suite uses synthetic in-memory JPEG/PNG/WEBP/PDF media,
mocked transport, and a socket-level outbound-network denial backstop. No live
provider call is part of this validation.

Invalid supplied references reject the entire draft before filtering, and the
assembled Responses envelope is checked immediately before sending. Original
PDF attachments, rendered-page order/budget, catalog visuals, configured model
and reasoning, and prompt semantics remain unchanged for valid inputs. Full
drafts have one provider-send budget; unrelated routes retain their retry rules.
Draft PDF rendering does not retain local debug images.

Failure diagnostics permit only enumerated provider classifications and bounded
structural `error.param` paths supplied by the provider. Paths are never inferred
from messages. `request_shape_sha256` hashes a versioned structural projection:
field/type markers, ordered content discriminators, detail levels, and MIME
classes. Prompt text, filenames, media/base64, private identifiers/URLs, model
configuration values, credentials, headers, and raw provider bodies are excluded.
The fingerprint is not evidence of historical failure causation.

The final assembled Responses envelope enforces the application's combined
`input_file` ceiling of 50,000,000 decoded bytes, alongside per-file and
whole-envelope bounds. Retain the five-PDF control and below/exact/above aggregate
boundaries. Original PDFs, rendered pages and catalogue visuals keep their order,
detail levels and independent budgets; draft rendering keeps no debug images.

All six operation routes require explicit namespaced model, reasoning effort and
single downstream endpoint settings described in [the gateway contract](ai-gateway.md).
There are no model defaults or cross-route substitutions. Reasoning must match
one of the seven supported values exactly, without trimming or case changes.
Test each accepted effort and shape-valid unknown values on every route. Missing,
empty, malformed or unsupported settings and assembled/configured mismatches
reject with `failure_boundary=request_validation`, `attempt_number=0` and zero
sends. Missing-key tests remove their own setting; fixtures must not refill it.

Deploy configuration comes from process settings only. Poison the dotenv reader
to prove that missing Coolify settings cannot fall through to a local file.
Local development keeps its explicit dotenv source. Complete synthetic fixtures
provide all six routes, even when a test exercises just one. Every operation
captures an immutable configuration for request, retries, metadata batches and
telemetry. Change settings mid-prompt and between operations to prove snapshot
stability and later refresh. Client model/effort overrides have no effect.

Use explicit completed, incomplete, refused, malformed and mismatched-model
Responses fixtures. Do not repair envelopes or insert missing model/status fields
at test runtime. Mock only the actual bounded transport's opener, retaining the
socket denial backstop. A six-page synthetic PDF covers High Quality through real
local rendering and mocked transport. Gateway admission is followed by the same
JSON, confidence, quantity, row identity and pricing-authority validators.

Single-send analysis and pricing failures have a one-send budget. Text calls may retry
only the existing bounded transient failures on the same captured route; partial
output, refusal and auth/payment/permission failures never replay. Preserve safe
HTTP parameter/class/code controls, privacy canaries, usage availability, retry
lineage and exactly one terminal record per actual send. Provider contract,
response shape, JSON, HTTP and transport failures remain separately identifiable.
Unknown configured model labels stay out of logs and forensic derivations;
closed historical public telemetry labels retain their existing positive tests.

Pricing-import telemetry checks in `tests.test_pricing_import_telemetry` use the
actual bounded opener for success, configuration rejection, HTTP/connection,
refusal and malformed JSON paths. Preserve one send-terminal record per actual
send, distinct zero-send validation, matching import run/stage identifiers,
truthful usage and phase timings, and separate uploaded-filename response checks
from filename/provider-content exclusion in logs.

`tests.test_owner_harness_lifecycle` exercises the real harness main function in
an isolated child with a fake server. Normal return closes once and exits zero;
a serving exception closes once and retains its exception and nonzero exit.

The loaded-browser fixture uses `tests/gateway_browser_harness.py` to inject a
loopback-only opener into the actual bounded transport. It accepts only its
literal synthetic credential and fixed local fixture endpoint. Production retains
the fixed HTTPS gateway URL. Preserve the three real server-backed selected-line
proposals, pricing authority and current XLSX assertions in that browser flow.

Login source checks include `GET /login` issuance limits before nonce allocation,
a separate POST password-attempt budget, trusted client resolution and no
allocation on rate denial. Login access logs contain only the canonical `/login`
route for query keys/values, fragments, absolute-form userinfo/authority and initial
double-slash request targets. Exercise both pure projection and the actual local
HTTP handler, while retaining unrelated route logging behaviour.

Password verification controls retain identical-configuration fingerprints, secret
and identity-context rotation, UTF-8 byte bounds, and one PBKDF2 derivation plus
one fixed-length comparison for every valid, incorrect, unknown-alias or invalid
login input. Run the password policy, actual-HTTP and identity-continuity modules;
measure representative login and session-check cost without changing timeouts.
The browser authority controls in `tests.test_browser_smoke_authority` compare
public opaque recovery scope and authenticated workspace before/after quote
creation, rejecting changed authority or missing permissions without exposing a
user record. The browser final-authority test also executes the maintained smoke's
actual schema/decimal/Unicode/whitespace assertion with each negative control.

The hosted-validation verifier projects successful and failed reports through a
closed, typed public metadata vocabulary before serialization. Run
`tests.test_internal_alpha_hosted_validation_verifier` for success, exceptions,
prohibited markers, unknown keys/types/values and canaries at every string leaf.
Only reviewed environment names and readiness IDs may leave; values and arbitrary
exceptions must not appear on stdout.

Contract coverage includes exact Responses field sets, invalid envelopes, whole
request rejection, one-send failures, privacy canaries, and N-1/N/N+1 boundaries
for reference/catalog counts, decoded media/derived-image bytes, dimensions,
pixels, page budgets, and inbound/outbound JSON. Byte/pixel/JSON boundary tests
use scaled ceilings to exercise exact equality without oversized fixtures.
Hosted acceptance remains a separate gate requiring explicit live-operation
authority; deterministic local validation does not establish live acceptance.

Run `python -m unittest tests.test_synthesis_contract_repair` for synthesis
contract changes. Cover safe nested missing-field paths, the final required-field
prompt restatement, one same-input synthesis repair and strict second-failure
rejection with the shared support reference. Verify exact send/token/cost totals,
one terminal record per send, no repeated page batches, exhausted-budget refusal,
the repair switch, and no repair for single-send analysis, invalid supplied values,
JSON failures or refusals. Equivalent absent text/description/lines fields qualify
only with a structural missing-path diagnostic. Use fake provider responses and
socket-level network denial; never default confidence or accept a partial basis.

A fix is not complete until the failing behavior has a regression test at the right layer:

- Unit tests for deterministic parsing, normalization, pricing, persistence serialization, and XLSX generation.
- Mocked AI tests for prompt/response contracts and malformed provider responses.
- Playwright tests for rendered controls, navigation, refresh behavior, upload flows, and visible error handling.
- CI checks for repository-wide syntax, unit, and smoke coverage.

When production code changes without a matching automated test, the final report must say which affected feature remains manually tested or untested and why.

## Quote reopening and Next acknowledgement

For dashboard Modify/Duplicate detail loading or Next loading changes, run
`python -m unittest tests.test_quote_flow_reliability` and
`node scripts/playwright-owner-uat-basis-repair.mjs --lines=6 --latency-ms=300 --alpha2-reliability`.
The synthetic rendered mode covers a failed Customer checkpoint, visible failure
with stopped spinner, dismissal and retry, duplicate activation suppression,
failed Modify/Duplicate reads preserving saved/current drafts without a save,
and a successful Modify retry. It then covers Duplicate plus a cancelled and
confirmed pricing-reference replacement, fresh analysis and one Confirm reaching
Output with the replacement catalogue price. Dependency and analysis choices
must remain pauses; neither starts a loading request before confirmation.
Existing protected XLSX amount, pricing-ledger and reopen checks remain active.
The unit contract also covers saved Customer checkpoint address equivalence
(NFC and whitespace) while preserving display text and invalidating substantive
address changes, and fresh protected PDF reuse without a new generation job.
Run the existing G3 restored-session browser test for the first Modify path.
The native Alpha smoke additionally verifies a saved/current PDF reopens through
the protected inline endpoint without another generation job. Real LibreOffice
conversion, native PDF viewer/rendering and hosted persistence qualification
remain separate; the synthetic mode does not substitute for native PDF smoke or
owner UAT.


## Current quotation links and completed jobs

Atomic text writes use short temporary basenames in the destination directory.
Run the two `test_atomic_text_*` cases in `tests.test_quote_publication_projection`
for real-filesystem old-helper versus revised-helper path growth and replacement
failure preservation. The regression accepts an old-helper success on hosts
that support its longer path, and always checks revised-helper success and
bounded temporary names. Path-length qualification on the affected host requires
actual old-helper failure and revised-helper success; a contrary result blocks
that qualification. Retain the existing local publication staging, rollback and transaction
checks, and the rendered source-provenance XLSX download regression. Temporary
creation permissions, UTF8 bytes, flush/fsync, replacement and cleanup remain part
of the atomic-write contract.

Local unpublished publication directories use an independent short random name
ending in `.staging`. The publication-projection regressions cover bounded
same-parent staging of both exports and nonempty draft files, unchanged final
publication identity, exclusive collisions and copy-failure rollback preserving
prior metadata and bytes. Retain the controlled long-parent filesystem comparison
and the original rendered quote save/reload/XLSX check for the admitted Windows
environment. Temporary names keep their existing permissions and transaction
tracking; final publication paths, metadata and links retain their existing format.

Run `python -m unittest tests.test_quote_publication_projection tests.test_object_pdf_completion`
for current publication eligibility, saved/list/job/resume projections and protected
XLSX/PDF download plus PDF inline delivery. Historical completed status and evidence
stay unchanged when an edit, replacement, missing/corrupt artifact or unknown read
makes ordinary links unavailable. A failed attempt cannot present the independently
valid prior saved pair as its own output. Committed projection warnings retain their
bound recovery receipt; both ordinary PDF URLs remain unavailable until recovery.

New eligible links carry an optional `publication` comparison. Retained bound links
deny after replacement; old unbound URLs intentionally remain CURRENT aliases.
Empty, duplicate, malformed, wrong-kind and mismatched comparisons deny before
artifact bytes are read. This comparison grants no access: existing owner, workspace,
role and authentication checks remain the separate gate. Direct `/api/generate`
responses and asynchronous jobs never expose private publication identities or proof.

Publication fault tests first establish an eligible revision-bearing draft with
real saved profile/reference authority, then inject deletion, staleness or storage
failure. Keep staged invisibility, raw committed completion, forensic idempotence,
exact object/metadata rollback and prior byte/hash checks. Recorded but unverified
exports expose unknown `exists`/`missing`, no ordinary URL, and `unverified`
availability; preserved historical bytes are checked separately from current delivery.
Bound URL expectations join known session/kind/owner identities to independently
read persisted publication metadata, including the canonical comparison digest.
The maintained `npm run playwright:alpha-smoke` joins the accepted job's actual
generator workbook bytes to that metadata and checks the original owning-server
HTTP receipt and native browser workbook filename, URL, size and SHA256. The
authenticated protected download must equal the native browser bytes before PDF
generation. `scripts/verify_current_workbook.py` independently binds each accepted
priced/Included row's description, quantity, unit and numeric amount, then checks
cached totals, dynamic formulas/currency, bold headers, centred quantities, money
formatting, hidden internal columns, original uploaded logo, header placement and
the selected quotation print area. It runs before and after native PDF generation.
Currency/tax expectations come from the selected reference config; customer,
project, company name and header details come from the fixture inputs entered in
the smoke, not the generation request echo. Both header/logo anchors must remain
inside print-area rows and columns and match the selected template geometry. B20 quantity and E20 estimate headers must be centred
and right-aligned respectively, as the active quotation layout rules require.
Protected same-session reads check original render/logo bytes, Customer checkpoint
session/digest and Included authority with numeric zero amount. The maintained
alpha smoke does not claim exact complete-draft equality or saved-profile binding
through generation; the owner UAT carrier separately checks saved-profile selection.
These checks use the existing quotation layout and synthetic catalogue inputs;
they introduce no pricing aliases or product UI changes.

Run `python -m unittest tests.test_alpha_workbook_request tests.test_current_workbook_receipt tests.test_workbook_http_receipt`
for browser-request mapping, real-generator workbook proof and negative controls: swapped row amounts,
incorrect quantities, formula/currency corruption, wrong logo/print area, exposed
internal columns, stale or mismatched publication/owner/revision, wrong accepted
job bytes, forged URLs, wrong download bytes and failed owning-server HTTP status.
Negative controls also reject wrong reference currency/tax, company name/header
details shared by request and workbook, out-of-area header/logo anchors and changed
B20/E20 alignment. Run `python -m unittest tests.test_ci_smoke_reliability` for
the default-path rate-window drain, unconditional final-save status check, fatal
quote-session throttling, screenshot mode and protected readback guards.
Both broad and isolated recovery app modes wait out the unchanged 60-second write
window before generation recovery. Resumed final saves must return HTTP 200;
any HTTP 429 observed on quote-session endpoints fails the run. The separate
`npm run playwright:smoke -- --recovery-only` CI step retains expired-job and
Customer Next selection-commit checks on fresh state; the broad default run
does not invoke those two checks. No server rate limit is loosened.
Run both full browser commands in an environment with Playwright Chromium and,
for the native alpha smoke, LibreOffice and the declared Python PDF dependencies.
Missing local dependencies are a test gap, not a passing browser result.
Run `python -m unittest tests.test_workbook_anchor_bounds tests.test_smoke_wiring`
for oversized drawing rows, jointly shifted anchors, selected-template geometry,
field-specific failures, recovery invocation sites, protected readback calls,
fatal verifier exits and response observation across tabs/popups. Both modules
run in CI's complete Python discovery step. The live-terminal browser step checks
each visible terminal label, exactly one generation, polling, zero navigation and
retained interruption identity on its own server and browser.
The smoke uses Playwright Chromium locally as well as in CI. Screenshot mode
runs the broad visual journey without the isolated G3 tail; default CI retains it.
On Windows, native Excel COM PDF export may rewrite the workbook tax formula's
expression while preserving its value. The post-PDF exact formula assertion
still rejects such a rewrite; record it as a local converter qualification gap.
CI's LibreOffice path and the verifier's formula requirements remain unchanged.

Each eligible saved/job/resume read verifies at most one actual PDF body; a list of N
eligible rows verifies at most N PDF bodies. Invalid auth, proof or expected identity
reads no PDF body. XLSX keeps its metadata/path projection; download independently
verifies its bytes. Local XLSX projection uses path/size metadata without reading its
body. Atomic completion uses already admitted committed files and adds no artifact
read. Configuration lookup, storage and file work run outside `JOBS_LOCK`. The existing
second-admission race retains its forensic admission sequencing; this correction does
not redesign job reservation or replay policy.

After a separately authorised installation, eligible PDF projections can make one
additional existing object GET per view, or N per list, plus existing ancillary
publication-authority reads. There is no new HEAD, SDK call, cache, retry or provider
request. Synthetic local, SQLite BLOB and in-memory object tests do not qualify live
S3 transport, installed performance, real PDF rendering, native viewer acceptance or
owner UAT. Direct post-commit warning coverage uses the reachable local publication
path; database/object generation finalises atomically and has no equivalent later
projection-read warning branch.
