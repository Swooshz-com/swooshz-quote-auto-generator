# SQAG Documentation Index

This index describes the active documentation set for the internal SQAG/SAQG
quote-generator module. SQAG owns the quote-specific local workflow. Future
platform concerns belong in `Swooshz-com/swooshz-platform`.

## Source Map

See [the repo map](repo-map.md) for parts, entry points, sources of truth and ownership.

## Current Docs

- [Quotation API contract proposal](quotation-api-v1.md) and
  [machine-readable spec](quotation-api-v1.openapi.json): proposed server-to-server
  estimates, confirmation, safe projections and offline fake-backed contract tests;
  no API runtime is implemented by these documents.
- `docs/ui-design-standard.md`: how new or changed screens look and are
  checked, taken from the existing UI (type, colour, spacing, buttons, forms,
  containers, status, review checklist).
- `docs/testing-plan.md`: validation expectations for product, frontend,
  import/export, security, CI, and smoke-test changes.
- `docs/internal-uat.md`: release UAT checklist that every agent UAT runs on
  the copy of live before a deploy; the owner checks only its gaps.
- `docs/internal-uat-checkpoint.md`: documentation-only local UAT passed
  checkpoint for the Platform launch and SQAG quote/dashboard baseline through
  PR #82.
- `docs/internal-uat-deploy-auth-readiness.md`: existing gated single-instance
  internal UAT deploy/auth readiness notes, boundaries, smoke checks, and
  non-production limitations.
- `docs/internal-password-auth-mode.md`: canonical temporary staff password mode, admission, session revocation, and readiness contract.
- `docs/internal-uat-coolify-deploy.md`: release runbook for the internal app
  (rehearsal, live-data test, deploy, rollback); the DB/BLOB artifact path is
  documented as blocked for hosted, protected, deploy and production readiness.
- `docs/internal-uat-login-and-pre-vps-dry-run.md`: approved-tester login
  expectations and local/offline deploy-auth checks to run before buying or
  touching a VPS.
- `docs/production-readiness-audit.md`: production-readiness verdict, storage
  surface audit, Codex Security findings, safe readiness command, and follow-up
  PR sequence before any hosted/protected/deploy or production launch.
- `docs/architecture-dead-code-fallback-audit.md`: whole-codebase architecture,
  dead-code, local-trace, removed sample-path, fallback, and docs-cleanup audit
  that defines the stricter no-product-visible-sample direction.
- `docs/platform-launch-mode.md`: first SQAG-side Swooshz Platform launch
  consume adapter boundary, env flags, safe consume shape, and cloud-storage
  deferral.
- `docs/platform-integration-contract.md`: SQAG-side audit of the expected
  Swooshz Platform launch/auth/workspace contract, canonical `appKey=sqag`,
  completed Platform app-key migration, fail-closed behavior, and live hosted
  integration gaps.
- `docs/platform-scoped-storage-mode.md`: platform-workspace-scoped SQAG app
  data storage boundary, migration command, and local-vs-database mode notes.
- `docs/postgres-migration-runbook.md`: immutable PostgreSQL migration manifest,
  checksum ledger, read-only preflight, future operator sequence, and rollback
  constraints.
- `docs/pricing-catalog-import.md`: current pricing-reference import behavior,
  AI normalization/enrichment contracts, save behavior, ordering, and deferred
  import items.
- `docs/ai-basis-chat-test-playbook.md`: AI basis chat test scope, mocked checks,
  live smoke guidance, and prompt/response expectations.
- `docs/privacy-pdpa-gdpr-baseline.md`: privacy and legal engineering baseline.
  Treat production and account-related entries there as future launch/platform
  blockers, not internal RC implementation work.
- `docs/current-cicd-status.md`: active GitHub Actions workflow, CI checks, and
  maintenance rule for CI/CD changes.
- `docs/pr-checks/quote-generator-pr-checklist.md`: PR review checklist and
  SQAG module boundary reminder.

## Historical Or Archive Docs

No historical/archive docs remain in this cleanup pass. Completed phase plans and
old handoff notes were consolidated instead of archived.

## Removed Or Consolidated Docs

- `docs/internal-team-test-handoff.md` and `docs/sqag-current-status.md` (a
  September 2026 release-candidate audit that no longer matched how the app
  runs) were removed. `AGENTS.md`, `docs/repo-map.md` and the runbooks hold the
  current scope and release order.

## Scope

Current scope and what stays out of scope without the owner's explicit request
are in `AGENTS.md`. Platform-shell, app-registry and cross-app concerns belong
in `Swooshz-com/swooshz-platform`.

## Runtime And Private Asset Reminder

Internal SQAG testing should use clean local/runtime storage:

```powershell
QUOTE_DATA_ROOT=<local-runtime-data-root>
```

Private profile and pricing files stay outside the repository:

- `<private-profile-json-outside-repo>`
- `<private-pricing-xlsx-outside-repo>`

Do not commit runtime stores, private uploads, generated workbooks, generated
PDFs, real logos, workbook rows/cells, bank/payment data, customer data, or
private local paths.
