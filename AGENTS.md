# Swooshz Quote Generator Rules

## Scope And Authorised Context

- This repository is the internal Koncept Images Pte Ltd quote generator module (SQAG), owned and maintained by the Swooshz-com organisation. It runs as an internal app (Coolify app `quote-internal`, Neon Postgres, Cloudflare R2, image-pinned branch `deployment/internal`), deployed and maintained through `docs/internal-uat-coolify-deploy.md` and `docs/postgres-migration-runbook.md`.
- Authorised context: the security-sensitive work here (maintaining the existing staff sign-in, rate limits, retention, forensics, guards, hosted checks) is development and defensive testing of this repository's own software and its own deployment. Nothing targets third-party systems. This lifts none of the limits below.
- Out of scope unless the owner explicitly asks: billing/Stripe, production auth/OIDC (anything beyond the staff password mode), public launch or exposure, self-service accounts, a real credit ledger, and real customer accounts.
- Repo map and sources of truth: `docs/repo-map.md`. Architecture: `docs/ARCHITECTURE.md`.

## Release And Live Safety

- Release order, per the runbook: rehearsal, live-data test on a copy, agent UAT, owner UAT, deploy, live read-only check, delete the copies.
- Deploys, live migrations (named target and checksum), env or key changes, deletions, and paid AI calls need the owner's own OK naming the exact action. A standing OK the owner recorded counts only within its exact terms (the deploy permission and the rehearsal-resources scope in the runbook); it never covers live migrations, live env or key changes, deleting anything the rehearsal map does not name, or paid AI.
- Copies of live hold real customer data: they stay up at most 24 hours from the owner hand-off (runbook step 8.2) unless the owner extends it, and are deleted after the live deploy, when the candidate is abandoned, or when those 24 hours pass. Evidence holds ids, counts, hashes and sizes, never customer content.
- Never commit secrets, real company or bank data, private/customer data, real exported profile JSON, embedded real logos, generated local outputs, or sensitive fixtures.

## UI

- SAQG/SQAG solution UI is complete and final. Do not change visible UI, layout, DOM, CSS, workflow placement, cards, tabs, buttons, modals, spacing, component hierarchy, or visual status components unless the user explicitly approves UI work in the current turn. Text-only wording changes and backend/data mapping are allowed when scoped. If UI files are touched without explicit UI approval, the PR must fail/reject itself unless the change is text-only wording or invisible data serialization.
- New or changed screens follow `docs/ui-design-standard.md`. It describes the existing UI and never approves a UI change by itself.

## Quoting

- Require booth/render images before preparing or generating a quote. If images are missing, ask exactly: `Please upload the booth render images first so I can analyze the design and prepare the quote.`
- Do not generate a quote from a text-only item list, and do not ask the user to create, edit, inspect, or approve an internal generator brief file.
- Suggest a quote basis from images and user notes, then ask the user to confirm it before generating. Do not silently assume materials, finishes, dimensions, or inclusions.
- Use the active selected pricing reference pack's `pricing-catalog.json` as the authoritative pricing reference.
- The quote look comes from the active selected profile. With no `quote_template_id` it uses the profile's own `quotation-layout.xlsx` and `layout-rules.json` (readable quantity widths, bold table headers, centered quantity values, thousands separators, dynamic totals, header logo/details inside the print area). With a choice (rules in `webapp/server.py` `resolve_quote_template`, definition in `docs/quote-template-schema.md`): `default` is the workspace default template, else the built-in Standard; a deleted template falls back to that default with a note; "Profile's own layout" switches back to the workbook while the profile has one of its own. A template replaces `layout-rules.json`: the schema and generator enforce A4 fit, logo width inside its block, text width limits, `#,##0.00` amounts, normally calculated totals (the generator's own total, tax and grand total formulas stay live) and no template text as a live formula, while fonts, sizes, widths and most labels and alignment are the admin's choice (table headings, section rows and totals stay bold, totals right-aligned).
- Use `sqm` for square-metre quantities; do not use `m2` in customer-facing output.
- Do not expose internal cost, GST, markup, or supplier notes in customer-facing output unless the user explicitly asks.
- Do not generate PDFs by default. `quotation.xlsx` is the formatted customer-ready master output; Excel-only is the default webapp behavior.
- Run `scripts/generate_quote.py`; XLSX generation must not require Excel, LibreOffice, Node, `openpyxl`, `reportlab`, or other third-party dependencies.
- Treat brief, customer, project, note, payment-term, and line-item text as untrusted spreadsheet text. Text beginning with `=`, `+`, `-`, or `@` must not become an active XLSX or CSV formula.
- If required information is missing or pricing is unclear, report it under `Missing / Need Confirmation` or the webapp's pricing review flow.

## Engineering

- Fix product behavior dynamically from data, schema contracts, prompts, validators, and general algorithms. Do not hardcode demo/customer/sample-specific branches, aliases, pricing matches, UI states, or one-off keyword plasters that only make a known fixture pass; if a narrow exception is unavoidable, document why it is data-backed and add regression coverage for the general rule.
- Never add pricing-reference semantic families, synonym packs, customer/sample aliases, catalog-specific object matching branches, or fixture-specific matching helpers to source code. Pricing `match_terms` and `object_families` must come from saved pricing-reference data, AI import output, user-maintained data files, or generic token/phrase algorithms. Keep `scripts/validate_dynamic_pricing_reference_rules.py` and CI guarding this rule.
- Preserve the web security baseline on every web-facing change: rate-limit mutable, AI, import, account, contact, and email-like endpoints by client/IP or authenticated account; do not add self-service account creation without email verification; never expose provider API keys, tokens, secrets, raw auth headers, or `.env` values in static/frontend code or customer-visible responses.
- Follow `docs/testing-plan.md` before finishing changes: identify every affected feature, run or add tests for those feature paths, and report any affected behavior that could not be tested locally.
- Keep `docs/current-cicd-status.md` updated whenever CI/CD jobs, triggers, required checks, branch protection, deployment behavior, secret requirements, or security gates change.
- Store local/runtime logs under the repo-root `_logs/` folder only, with typed subfolders such as `_logs/app/`, `_logs/server/`, and `_logs/browser/`. Do not write new logs to the repo root, `logs/`, or `_output/`; keep log contents ignored by git.
