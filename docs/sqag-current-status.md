# SQAG Current Status And RC Audit

Last updated: 2026-06-21 Singapore local time

## Namespace Cleanup Update

SQAG is now the canonical runtime/repo/product namespace. Active runtime
configuration uses `SQAG_STORAGE_MODE`, `SQAG_ARTIFACT_STORAGE_MODE`,
`SQAG_DATABASE_URL`, `SQAG_PLATFORM_LAUNCH_MODE`, `SQAG_PLATFORM_BASE_URL`, and
canonical `SQAG_OBJECT_STORAGE_*` names. SQAG expects Platform launches with
`appKey=sqag` only; the separate Swooshz Platform app-key migration remains
pending and is tracked by `platform_app_key_migration_pending`.

Earlier live database and DB+object evidence is historical/pre-namespace
evidence after the SQAG table/object-metadata/app-key cleanup. Post-rename live
production database evidence, live DB+object backup/restore evidence, live
retention/delete evidence, and live Platform-to-SQAG smoke after the Platform
app-key migration remain required. `production_ready=false` remains correct.

## Executive Verdict

Verdict: RC-ready with minor follow-ups.

SQAG can proceed to owner-run private real-data smoke testing using private
profile and pricing files kept outside this repository. The audit did not find
a product-code blocker for internal RC testing. Hosting, public exposure, login,
database-backed history, billing, credits, and platform-shell work should wait
until after the private smoke test and should be owned by the future
`Swooshz-com/swooshz-platform` repo.

Minor follow-ups before wider internal rollout:

- Run the private real-data smoke checklist with files outside the repo.
- Confirm the exported `quotation.xlsx` visually in Excel with the real private
  profile and pricing reference.
- Confirm optional workbook PDF viewing only if the current local machine has a
  supported Excel export path.

## Current Product Scope

SQAG owns the quote-generator module only:

- booth/render image intake
- quote-company profile import, export, selection, and runtime reuse
- pricing-reference import, review, save, edit, list, selection, and export
- quote basis review and pricing review
- `quotation.xlsx` generation as the customer-ready master output
- optional view-PDF behavior when explicitly requested and supported locally
- local Settings/runtime configuration for the quote workflow

## Current Non-Goals

These are intentionally out of scope for SQAG RC:

- public launch or hosted staging
- login, accounts, users, roles, company membership, or app access
- Stripe, billing, credits, subscriptions, or ledgers
- Supabase, hosted databases, DB-backed quote history, or durable multi-user
  storage
- Hostinger, Coolify, Docker deployment, DNS, or production infrastructure
- Swooshz platform shell, navigation, app registry, or cross-app architecture
- user dashboard/history or past-session management

## Module Boundary

Important module surfaces inspected for this audit:

- `webapp/server.py`: local HTTP API, runtime storage, import/export endpoints,
  job orchestration, pricing-reference resolution, and privacy-safe errors.
- `webapp/static/app.js`: browser workflow state, pricing selection, output row
  review, export controls, and optional PDF view trigger.
- `webapp/static/index.html` and `webapp/static/styles.css`: existing final UI
  shell. This audit made no UI changes.
- `scripts/generate_quote.py`: XLSX master generation and optional workbook PDF
  export mode.
- `scripts/scan_sensitive_fixtures.py`: committed fixture and private-data scan.
- `scripts/validate_dynamic_pricing_reference_rules.py`: guard that pricing
  matching stays data-driven.
- `templates/profile/default/` and `templates/pricing-reference/`: generic local
  templates, not private company data.
- `tests/fixtures/quote-generator/`: synthetic and test-only seeded fixtures
  used for automated coverage only.

## Runtime Data Model

The intended internal RC path starts from a clean runtime data root:

```powershell
QUOTE_DATA_ROOT=<local-runtime-data-root>
```

Current storage behavior:

- quote-company profiles imported through Settings are stored in the company
  runtime store under `QUOTE_DATA_ROOT/{company_id}/profiles.json`
- runtime profile pack assets are stored under the same local data root
- company pricing references imported through Settings are stored under
  `QUOTE_DATA_ROOT/{company_id}/pricing-references.json`
- job-specific runtime catalog files are materialized under the job temp folder,
  not committed source paths
- `_pricing-references/`, `_output/`, `_tmp/`, and `_logs/` are ignored local
  runtime/output paths
- the clean runtime API can expose no pricing references, leaving the quote
  company step blocked until a valid reference is selected

The repo still contains generic templates and synthetic test-only fixtures.
Those are not the private-company RC data path and should not be treated as
normal working pricing data for team testing.

## Private Asset Rules

Use placeholders only in reports, issues, and PRs:

- `<private-profile-json-outside-repo>`
- `<private-pricing-xlsx-outside-repo>`
- `QUOTE_DATA_ROOT=<local-runtime-data-root>`

Do not commit, paste, screenshot, or include in GitHub:

- real profile JSON
- real pricing XLSX/CSV/Markdown uploads
- real logo data or logo Base64
- real company, bank, payment, customer, project, workbook row, or workbook cell
  details
- private local usernames or machine-specific private paths
- generated customer quotes, exported workbooks, exported PDFs, runtime logs, or
  runtime stores

## Internal RC Flow

Owner-run private smoke flow:

1. Start from a clean runtime data root using `QUOTE_DATA_ROOT=<local-runtime-data-root>`.
2. Open the local app.
3. Confirm the fresh workspace has no pricing references.
4. Confirm the missing-pricing empty state appears.
5. Confirm `Next: Quote Company` is disabled until a valid pricing reference is
   selected.
6. Import `<private-profile-json-outside-repo>`.
7. Import `<private-pricing-xlsx-outside-repo>`.
8. Select the imported valid pricing reference.
9. Confirm `Next: Quote Company` enables.
10. Complete customer, quote-company, and quote-basis flow with booth/render
    images.
11. Generate and review the output table.
12. Export `quotation.xlsx`.
13. Use View PDF only if currently supported on the local machine.
14. Confirm `git status --short` shows no committed or untracked private
    runtime/export files.

## Audit Findings

Confirmed pass items:

- The retired seed-workspace directory is absent.
- A targeted scan for the retired seed-workspace spellings returned no matches.
- Synthetic quote-generator data is confined to clear fixture/sample paths and
  test scripts.
- A clean runtime can expose no pricing references; missing-pricing UI text and
  quote-company navigation blocking are covered by tests.
- Runtime/company imported profile storage is covered, including embedded logo
  data preserved through save, reload, export, and XLSX generation.
- Runtime/company pricing references are listed, selected, saved, edited,
  detailed, deleted, and passed to generation without relying on a repo pricing
  pack.
- Output table rows are the reviewed export source: the browser converts
  reviewed output rows back into final line items before generation.
- XLSX export remains the default master output; PDF generation is not the
  default and uses explicit workbook PDF mode only.
- Stale output/PDF behavior is guarded: output edits clear stale download files,
  reset restores the reviewed snapshot, and default generation removes stale PDF
  output.
- Formula-like profile and line-item text is sanitized before spreadsheet output
  paths.
- No DB, Supabase, Stripe, billing, credit ledger, deployment, Hostinger,
  Coolify, or Swooshz platform implementation was added by this audit.
- The tracked-file check found private/profile/pricing/generated-output matches
  only under synthetic fixture paths.

Risks and known limitations:

- Real private profile/pricing files were not available to this task runner, so
  the private smoke test remains owner-run.
- Optional PDF viewing depends on the local workbook export path. Excel-only
  output remains the default and should be treated as the RC master output.
- Existing local auth/deploy scaffolding is outside the internal RC path and was
  not expanded. Hosting should wait for a separate platform/security decision.
- Test-only seeded fixtures remain available for automated coverage, but team
  RC should start from clean runtime data and imported private files.

Required fixes before internal team testing:

- None found in product code during this audit.

Optional follow-ups after RC:

- quote dashboard or past sessions
- hosted private staging
- Swooshz platform login integration
- DB-backed quote history
- platform-owned billing, credits, app access, and account membership

Recommended next PR:

Run the owner private real-data smoke checklist and file a follow-up PR only for
findings that are reproduced with sanitized evidence. User dashboard/history
should be treated as the next product feature after this RC audit, not as a
blocker for the private smoke test.

## Validation Commands Run

These commands were required for this audit branch:

```powershell
python scripts/scan_sensitive_fixtures.py
python scripts/scan_sensitive_fixtures.py --fail-on-review
python -m unittest discover -s tests
python -m py_compile webapp/server.py scripts/generate_quote.py scripts/scan_sensitive_fixtures.py
node --check webapp/static/app.js
python scripts/validate_dynamic_pricing_reference_rules.py
git diff --check
git status --short
```

Results:

- `python scripts/scan_sensitive_fixtures.py`: initial plain `python` launcher
  failed before execution in this shell; rerun with the bundled workspace Python
  passed with `0 blocking, 0 review findings`.
- `python scripts/scan_sensitive_fixtures.py --fail-on-review`: initial plain
  `python` launcher failed before execution in this shell; rerun with the
  bundled workspace Python passed with `0 blocking, 0 review findings`.
- `python -m unittest discover -s tests`: initial plain `python` launcher failed
  before execution in this shell; rerun with the bundled workspace Python passed
  with 393 tests.
- `python -m py_compile webapp/server.py scripts/generate_quote.py
  scripts/scan_sensitive_fixtures.py`: initial plain `python` launcher failed
  before execution in this shell; rerun with the bundled workspace Python
  passed.
- `node --check webapp/static/app.js`: passed.
- `python scripts/validate_dynamic_pricing_reference_rules.py`: initial plain
  `python` launcher failed before execution in this shell; rerun with the
  bundled workspace Python passed.
- `git diff --check`: passed.
- `git status --short`: final status is recorded in the PR/final report after
  staging and commit.

## Documentation Cleanup Summary

Docs kept current:

- `docs/sqag-current-status.md`
- `docs/README.md`
- `docs/testing-plan.md`
- `docs/pricing-catalog-import.md`
- `docs/ai-basis-chat-test-playbook.md`
- `docs/privacy-pdpa-gdpr-baseline.md`
- `docs/current-cicd-status.md`
- `docs/pr-checks/quote-generator-pr-checklist.md`
- `docs/agent-playbooks/`

Docs consolidated:

- `docs/internal-team-test-handoff.md` was consolidated into this current status
  and audit handoff doc.

Docs archived:

- None.

Docs deleted:

- `docs/internal-team-test-handoff.md`

Platform transfer candidates:

- login/accounts/users/roles
- billing, credits, Stripe, ledgers, and entitlement
- app registry/app whitelist
- SEOzilla integration
- platform shell/navigation
- hosted deployment and cross-app production architecture
