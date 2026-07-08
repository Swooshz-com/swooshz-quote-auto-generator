# Quote Generator PR Checklist

Copy this checklist into quote-generator PRs and complete it before requesting
review.

## Required PR Fields

- Objective:
- Out of scope:
- User-facing change:
- UI files touched (`webapp/static/index.html`, `webapp/static/styles.css`,
  visible layout/DOM/workflow portions of `webapp/static/app.js`, Playwright
  screenshot/smoke expectations): Yes/No
- Explicit current-turn UI approval if UI files were touched:
- Data, storage, auth, or permission impact:
- Live/external service, deployment, Docker, or production action involved:
  Yes/No
- Secrets or env values touched: Yes/No
- Templates, pricing references, profiles, fixtures, or generated outputs
  touched: Yes/No
- Import/export behavior touched: Yes/No
- Runtime/company import behavior touched: Yes/No
- Validation commands and results:
- Manual smoke test evidence when relevant:
- Rollback notes when relevant:
- Remaining risks:

## Required Review Notes

- SAQG/SQAG solution UI is complete and final. Do not change visible UI, layout,
  DOM, CSS, workflow placement, cards, tabs, buttons, modals, spacing, component
  hierarchy, or visual status components unless the user explicitly approves UI
  work in the current turn. Text-only wording changes and backend/data mapping
  are allowed when scoped.
- If UI files are touched without explicit current-turn UI approval, the PR must
  fail/reject itself unless the change is text-only wording or invisible data
  serialization.
- If live/external service, deployment, Docker, DNS, firewall, env var, secret,
  credential, or production action is involved, state the explicit approval and
  target operation.
- If secrets or env values are touched, confirm no secret values are committed,
  logged, screenshotted, or exposed to frontend/static code.
- If templates, pricing references, profiles, fixtures, or generated outputs
  are touched, state whether they are source files, generated files, or test
  fixtures.
- If import/export behavior is touched, state the export source, import target,
  validation path, and whether private data remains in ignored runtime storage.
- If deleting repo-bundled assets, confirm tests are migrated and no active
  guardrail or SQAG module doc is removed.

## Module Boundary

SQAG owns quote-specific workflow/settings, pricing references, quote-company
profiles, pricing review, and XLSX output. The main Swooshz platform should own
login/accounts, billing, credits, app access, company membership, product
navigation, and cross-app registry concerns in a future platform repository.
