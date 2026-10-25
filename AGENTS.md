<!--
Curated AI-facing source.
Project: development.ai-coding-agent-rules
Review rule: Preserve safety constraints from preserved source. Do not weaken credential, .env, .tmp, .n8n-local, live n8n action, approval, attribution, or local-only rules.
-->

<!-- AI-AGENT-TOOLKIT:repo/contracts/agent-rules/toolkit-governed-rules.md:BEGIN TOOLKIT-GOVERNED-RULES v2 toolkit=3.10.0 sha256=f2b36c18b54d -->
# Toolkit-Governed Repository Rules

With the user's yes, this block and `.github/toolkit-programme.json` turn on Toolkit's GitHub governance for this github.com repository: programme records, decision traces, evidence custody and the rules below. Nothing here applies until the user says yes, without both, or off github.com. The coding gates, delegation and routes are in the Toolkit global rules in the harness's user-level file and apply everywhere. If they are missing (for example in a cloud session), read https://github.com/weijunswj/ai-agent-toolkit/blob/main/repo/contracts/agent-rules/global-rules.md before planning, and load the `n8n-safety-router` skill before any n8n work; if it is unavailable, stop and report that. Repository rules outside this block win on conventions; nothing here relaxes the global safety floor except the evidence-store line, which the floor names.

## Delivery

- Agree a release plan up front: releases, lanes and order. Keep one open release PR per lane; work outside the plan states its reason.
- Finish fast: ship the biggest coherent batch that stays reviewable, reconcile records before the next task, and order work Core, then Hardening, then Later.
- Repository-wide CI, test sharding and time budgets are set here, measured before and after a change.
- Every governed repository keeps a current compact repo map (parts, entry points, sources of truth, ownership, recorded commit), updated in the change that moves what it describes.

## Records And Evidence

- Programme tracking: keep the parent issue's tracker current (SHA, version, lane and gate, PR, review and UAT state, queue, completed, deferred and superseded work) and record start, PR head, review verdicts, findings and exact-head fixes, CI, merge, UAT result, remediation and completion. Keep pending gates open after merge.
- Decision traces: the programme issue body is the canonical record. The `github-program-reconciler` links every non-bot comment from it (`#issuecomment-<id>`), one list per comment tag (first line, then `---`); parent and child traces never mix, and within a tag the latest comment wins. `SUPERSEDES=<record ID>` replaces a record. Before 60,000 characters, long lists page into index comments one hop away. G0 reads both traces and has the owner confirm a live-scope summary first (decision-trace playbook).
- SHAs: link files and commits with the 7-character short SHA (longer if GitHub reports it ambiguous) and write the full 40-character SHA beside the link as plain code text.
- Takeover: a new controller posts one takeover record on the parent's CURRENT before acting (its identity, the bound commit and `CONTROLLER.md` blob, evidence that the previous writer stopped, the next action) and reads it back.
- Custody: session folders, side-session notes, scratchpads and temp paths are never custody. Packets, plans and reviews reach the selected private evidence store with a manifest and read-back before a record cites them; archive old sessions only after that read-back.
- Evidence (transport) stores are agent-managed with standing permission, whatever the backend (private repository, shared folder, drive): once the user-selected store, separate from the task repository, has its identity and privacy verified (a name or prefix alone grants nothing), agents write, reorganise, delete and, on Git, push to or merge into its default branch without asking. Scan every upload for secrets, never delete the sole copy of evidence, read writes back, and never relabel task source as evidence or retry a denied effect.

## Repository Setup

- If the repository does legitimate security-sensitive work, describe it in an `Authorised context` section outside this block: who owns the code, that the work is development and defensive testing of the repository's own software, and that nothing targets third-party systems.
- If the playbook index is missing during an instruction install, repair or refresh, report that it needs installing.
<!-- AI-AGENT-TOOLKIT:repo/contracts/agent-rules/toolkit-governed-rules.md:END TOOLKIT-GOVERNED-RULES -->


# Swooshz Quote Generator Rules

- This repository is the internal Koncept Images Pte Ltd quote generator module. The current priority is internal team test-readiness, not deployment, auth, billing, public launch, or production platform integration.
- Do not implement Hostinger, Coolify, Docker deployment, Supabase schema, Stripe, production auth/OIDC, a real credit ledger, public exposure, real customer accounts, or secrets in this repo.
- Do not commit real company or bank data, private/customer data, real exported profile JSON, embedded real logos, generated local outputs, or sensitive fixtures.
- SAQG/SQAG solution UI is complete and final. Do not change visible UI, layout, DOM, CSS, workflow placement, cards, tabs, buttons, modals, spacing, component hierarchy, or visual status components unless the user explicitly approves UI work in the current turn. Text-only wording changes and backend/data mapping are allowed when scoped. If UI files are touched without explicit UI approval, the PR must fail/reject itself unless the change is text-only wording or invisible data serialization.
- Require booth/render images before preparing or generating a quote. If images are missing, ask exactly: `Please upload the booth render images first so I can analyze the design and prepare the quote.`
- Do not generate a quote from a text-only item list, and do not ask the user to create, edit, inspect, or approve an internal generator brief file.
- Suggest a quote basis from images and user notes, then ask the user to confirm it before generating. Do not silently assume materials, finishes, dimensions, or inclusions.
- Use the active selected pricing reference pack's `pricing-catalog.json` as the authoritative pricing reference and the active selected profile's `quotation-layout.xlsx` as the customer-facing quote layout source.
- Preserve the formatting rules in the active selected profile's `layout-rules.json`, including readable quantity widths, bold table headers, centered quantity values, thousands separators, dynamic totals, and a header logo/details group that stays inside the print area.
- Use `sqm` for square-metre quantities; do not use `m2` in customer-facing output.
- Do not expose internal cost, GST, markup, or supplier notes in customer-facing output unless the user explicitly asks.
- Do not generate PDFs by default. Treat `quotation.xlsx` as the formatted customer-ready master output; Excel-only output is the default webapp behavior.
- Run `scripts/generate_quote.py`; XLSX generation must not require Excel, LibreOffice, Node, `openpyxl`, `reportlab`, or other third-party dependencies.
- Fix product behavior dynamically from data, schema contracts, prompts, validators, and general algorithms. Do not hardcode demo/customer/sample-specific branches, aliases, pricing matches, UI states, or one-off keyword plasters that only make a known fixture pass; if a narrow exception is unavoidable, document why it is data-backed and add regression coverage for the general rule.
- Never add pricing-reference semantic families, synonym packs, customer/sample aliases, catalog-specific object matching branches, or fixture-specific matching helpers to source code. Pricing `match_terms` and `object_families` must come from saved pricing-reference data, AI import output, user-maintained data files, or generic token/phrase algorithms. Keep `scripts/validate_dynamic_pricing_reference_rules.py` and CI guarding this rule.
- Preserve the web security baseline on every web-facing change: rate-limit mutable, AI, import, account, contact, and email-like endpoints by client/IP or authenticated account; do not add self-service account creation without email verification; never expose provider API keys, tokens, secrets, raw auth headers, or `.env` values in static/frontend code or customer-visible responses.
- Follow `docs/testing-plan.md` before finishing changes: identify every affected feature, run or add tests for those feature paths, and report any affected behavior that could not be tested locally.
- Keep `docs/current-cicd-status.md` updated whenever CI/CD jobs, triggers, required checks, branch protection, deployment behavior, secret requirements, or security gates change.
- Treat brief, customer, project, note, payment-term, and line-item text as untrusted spreadsheet text. Text beginning with `=`, `+`, `-`, or `@` must not become an active XLSX or CSV formula.
- If required information is missing or pricing is unclear, report it under `Missing / Need Confirmation` or the webapp's pricing review flow.
- Store local/runtime logs under the repo-root `_logs/` folder only, with typed subfolders such as `_logs/app/`, `_logs/server/`, and `_logs/browser/`. Do not write new logs to the repo root, `logs/`, or `_output/`; keep log contents ignored by git.
