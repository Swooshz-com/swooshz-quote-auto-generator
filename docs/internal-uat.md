# Internal UAT Checklist

## Purpose

This guide is for internal Koncept/Swooshz testing of the local SQAG/SAQG quote
generator. It helps testers exercise the local quote workflow, imported company
profiles, imported pricing references, generated quote output, exports, and
dashboard session restore/delete behavior.

SQAG/SAQG is not public SaaS, production deployment, ecommerce, a customer
portal, or DB-backed multi-user mode. Do not use this checklist as deployment,
auth, billing, account, or hosted-platform readiness.

## Tester Prerequisites

- The local app can start successfully.
- Runtime storage may contain local workspace data from earlier tests.
- The tester has access to safe test images and PDFs.
- The tester has a real company profile or pricing reference only if they are
  authorised to use it locally.
- Private files must remain local and must not be committed.

## Private Data Guardrails

Do not commit, paste into GitHub, or include in PR evidence:

- Real customer data.
- Real company or bank details.
- Real quote exports.
- Real profile exports.
- Files containing `logo_data_url`.
- Embedded Base64 logo data.
- Runtime quote-session folders.
- Local pricing or profile imports if they contain private data.
- Any generated XLSX or PDF output from real quotes.

Synthetic and test fixtures are allowed only when they are clearly
synthetic/test-only and do not contain real customer, company, bank, logo, quote,
or private pricing/profile data.

## Recommended Post-Merge Smoke Commands

These commands already exist in the repo docs, package scripts, or CI workflow.
Run the smallest useful set for the smoke being performed. CI runs the broader
gate on pull requests.

```powershell
git diff --check
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-webapp.ps1
```

```powershell
node --check webapp\static\app.js
node --check scripts\playwright-smoke.mjs
node --check scripts\playwright-ai-basis-chat-stress.mjs
```

```powershell
python -m py_compile webapp\server.py scripts\generate_quote.py scripts\live_ai_basis_chat_smoke.py scripts\build_pricing_catalog.py scripts\pricing_reference_cleanup.py scripts\pricing_reference_enrichment.py scripts\validate_dynamic_pricing_reference_rules.py scripts\validate_local_pdf_dependency_usage.py
```

```powershell
python scripts\validate_local_pdf_dependency_usage.py
python scripts\validate_dynamic_pricing_reference_rules.py
python -m unittest discover -s tests
```

```powershell
npm run playwright:ai-stress
npm run playwright:smoke
```

If Playwright browsers are missing locally, install the repo's configured
Chromium browser with:

```powershell
npm run playwright:install
```

## Manual UAT Checklist

### Dashboard / Startup

- [ ] Start the app.
- [ ] Confirm the dashboard is the first useful screen.
- [ ] Confirm New Quote starts a clean quote.
- [ ] Confirm the Privacy Notice link is reachable.
- [ ] Confirm the Pricing Reference action is reachable if applicable.

### Profile / Pricing Setup

- [ ] Import quote company profile.
- [ ] Confirm the imported company profile appears for quote company selection.
- [ ] Import pricing reference.
- [ ] Confirm the imported pricing reference appears for quote basis or pricing
      selection.
- [ ] Confirm no unexpected bundled real/private data appears.
- [ ] Confirm missing pricing/profile states are understandable.

### New Quote Flow

- [ ] Start New Quote from the dashboard.
- [ ] Upload safe reference images/PDFs or use an approved sample path if
      available.
- [ ] Fill customer details.
- [ ] Select quote company/profile.
- [ ] Select pricing reference.
- [ ] Run Quote Basis / AI analysis.
- [ ] Generate the output table.
- [ ] Review totals and line items.
- [ ] Export XLSX.
- [ ] Export PDF.

### Dashboard Session Flow

- [ ] Return to the dashboard.
- [ ] Confirm the new quote appears in past sessions.
- [ ] Select a session.
- [ ] Confirm the detail panel shows useful metadata only.
- [ ] Download XLSX/PDF where available.
- [ ] Confirm stale or missing exports are not downloadable.
- [ ] Use Modify quote.
- [ ] Confirm the saved draft restores.
- [ ] Confirm reference files restore without company logo files being treated
      as booth/reference images.
- [ ] Delete a single session using the custom delete modal.
- [ ] Create/select multiple sessions if practical.
- [ ] Bulk delete sessions.
- [ ] Confirm deleted sessions disappear and do not remain downloadable.

## Known Limits

- Storage is local/runtime storage only.
- There is no auth, users, accounts, or teams.
- There are no multi-user DB-backed sessions.
- There are no deployment assumptions.
- There is no billing or credits flow.
- There is no customer portal.
- Runtime data can be deleted locally.
- Missing export files may show an unavailable or stale state.
- Internal testers should report unclear copy, restore issues, export issues,
  and pricing/profile import issues.

## Bug Report Format

Use this format for internal UAT bugs. Remove or redact sensitive values before
sharing.

```text
Date/time:
Browser:
App start command:
Test data type used: synthetic / authorised real local data
Step where issue happened:
Expected result:
Actual result:
Screenshot if safe:
Private data visible in screenshot: yes / no
Console/server error if available, with sensitive values removed:
```

## Pass / Fail Criteria

UAT passes when:

- The tester can import profile/pricing or use safe existing local runtime data.
- The tester can create a quote.
- The tester can generate output.
- The tester can export XLSX/PDF.
- The tester can find the quote on the dashboard.
- The tester can restore/modify the quote.
- The tester can delete single and bulk sessions.
- No private data is committed.
