<!--
Curated AI-facing source.
Project: development.ai-coding-agent-rules
Review rule: Preserve safety constraints from preserved source. Do not weaken credential, .env, .tmp, .n8n-local, live n8n action, approval, attribution, or local-only rules.
-->

<!-- AI-AGENT-TOOLKIT:repo/contracts/agent-rules/toolkit-governed-rules.md:BEGIN TOOLKIT-GOVERNED-RULES v2 toolkit=3.15.15 sha256=132af9446394 -->
# Toolkit-Governed Repository Rules

With the user's yes, this block and `.github/toolkit-programme.json` turn on Toolkit's GitHub governance for this github.com repository: programme records, decision traces, evidence custody and the rules below. Nothing here applies until the user says yes, without both, or off github.com. The coding gates and delegation are in the Toolkit global rules in the harness's user-level file, with gate mechanics in the gate rules, and apply everywhere. If they are missing (for example in a cloud session), read https://github.com/weijunswj/ai-agent-toolkit/blob/main/repo/contracts/agent-rules/global-rules.md before planning, and load the `n8n-safety-router` skill before any n8n work; if it is unavailable, stop and report that. Repository rules outside this block win on conventions; nothing here relaxes the global safety floor except the evidence-store line, which the floor names.

## Delivery

- Agree a release plan up front: releases, lanes and order. Keep one open release PR per lane; work outside the plan states its reason.
- Finish fast: ship the biggest coherent batch that stays reviewable, reconcile records before the next task, and order work Now, then Later by priority.
- Repository-wide CI, test sharding and time budgets are set here, measured before and after a change.
- Every governed repository keeps a current compact repo map (parts, entry points, sources of truth, ownership, recorded commit), updated in the change that moves what it describes.

## Records And Evidence

- Programme tracking: the parent issue's tracker shows SHA, version, lane and gate, PR, review and UAT state, queue, and completed, deferred and superseded work. Update it when one of those changes; a comment that changes none is its own record. Keep pending gates open after merge.
- Decision traces: the programme issue body is the canonical record. Record each owner decision that changes scope, authority or the plan as one tagged comment (tag on the first line, then `---`); other comments need no index entry. The `github-program-reconciler` lists them by tag; parent and child traces never mix, the latest comment wins within a tag, `SUPERSEDES=<record ID>` replaces a record and long lists page into index comments. G0 reads both traces and has the owner confirm a live-scope summary first (decision-trace playbook).
- SHAs: link files and commits with the 7-character short SHA (longer if GitHub reports it ambiguous). Write the full 40-character SHA only in review verdicts, UAT results and merge summaries.
- Issues: to add a task, search the parent's sub-issues and open or recently closed issues; extend or reopen an owner, else create a child and attach it as a native sub-issue. Use `Refs` for staged, UAT-pending, blocked or follow-up PRs; `Closes`/`Fixes`, and closing, only once the merge completes every criterion and any owner UAT passed, with any remainder moved to a linked follow-up first.
- Custody: session folders, side-session notes, scratchpads and temp paths are never custody. Plans and reviews reach the selected private evidence store before a record cites them or sessions are archived; in a Git store the commit is the manifest and a push plus one fetch the read-back.
- Evidence (transport) stores are agent-managed with standing permission, whatever the backend (private repository, shared folder, drive): once the user-selected store, separate from the task repository, has its identity and privacy verified (a name or prefix alone grants nothing), agents write, reorganise, delete and, on Git, push to or merge into its default branch without asking. Scan every upload for secrets, never delete the sole copy of evidence, never relabel task source as evidence or retry a denied effect.

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
