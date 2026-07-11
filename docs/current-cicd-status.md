# Current CI/CD Status

Last updated: 2026-07-11

Source of truth: `.github/workflows/ci.yml`

## Active Workflow

- Workflow name: `CI`
- Triggers: pull requests, pushes to `main`, and manual `workflow_dispatch`
- Permissions: read-only repository contents
- No deployment job is configured
- This phase-planning PR adds no deployment job
- No production environment mutation is performed by CI
- CI does not require OpenAI, DeepSeek, Gemini, OIDC, deployment, or production secrets

## Active Jobs

- `Secret scan`: checks the repository with Gitleaks before validation work proceeds.
- `Dependency audit`: installs Node dependencies with `npm ci` and runs `npm audit --audit-level=high`.
- `Validate app`: runs after the security gates pass.

## Validate App Checks

- Installs Python 3.12 and Node 22.
- Installs pinned Python dependencies with `python -m pip install --only-binary=:all: -r requirements.txt`.
- Installs `pip-audit` and runs `python -m pip_audit -r requirements.txt --strict` against pinned Python dependencies.
- Installs Playwright Chromium.
- Checks JavaScript syntax for `webapp/static/app.js`, `scripts/playwright-smoke.mjs`, and `scripts/playwright-ai-basis-chat-stress.mjs`.
- Checks Python syntax for `webapp/server.py`, quote/pricing scripts, and validation guard scripts.
- Runs `python scripts/validate_local_pdf_dependency_usage.py` to keep `pypdfium2` and `Pillow` usage on the local PDF rendering path only.
- Runs `python scripts/validate_dynamic_pricing_reference_rules.py` to keep pricing-reference matching data-driven and block source-code semantic family/synonym packs.
- Runs `python scripts/scan_sensitive_fixtures.py --fail-on-review` so review-level sensitive fixture findings fail CI.
- Runs `python -m unittest discover -s tests`.
- Runs `npm run playwright:ai-stress`.
- Runs `npm run playwright:smoke`.

## Security And Secrets

- `.env.example` must contain placeholders only.
- Local `.env` files stay ignored and must not be committed.
- CI must stay free of production/customer secrets unless a future deployment design explicitly documents the new boundary and approval path.

## Deploy Runtime Gate

- CI still performs no deployment.
- `APP_MODE=deploy` now requires Swooshz Platform launch configuration; a
  standalone OIDC session cannot satisfy the workspace identity boundary.
- Deploy requests accept only signed sessions containing the consumed Platform
  user, workspace, app, and supported membership-role context. Pre-existing
  OIDC-only cookies are treated as unauthenticated and cannot inherit a tester
  role or reach AI/import routes.
- Deploy preflight and startup require a valid, nonempty
  `SQAG_TRUSTED_PROXY_CIDRS` boundary. SQAG accepts bounded, valid
  `X-Forwarded-For` chains only from a configured direct proxy peer, resolves
  the first untrusted hop from right to left, and otherwise rate-limits by the
  socket peer. Platform-launch and normal mutable routes use the same client
  resolver, so direct spoofing cannot split buckets and distinct proxied
  clients do not share Traefik socket identity.
- Each process retains at most 4,096 ordinary client/normalized-route
  rate-limit buckets. When that map is full, previously unseen identities share
  one fail-closed overflow bucket per normalized configured route; overflow
  cardinality is therefore capped at the 14 configured rate-limited routes.
  Traffic-triggered global pruning runs at most once every 15 seconds under the
  rate-limit lock and removes fully expired ordinary and overflow buckets.
  Active ordinary buckets are not evicted to admit rotating attacker identities.
- Before binding the deploy server, SQAG performs read-only checks for the
  required database schema, object-artifact metadata schema, and configured
  object-storage bucket. It never applies migrations from startup.
- Object-backed profile, pricing-reference, and quote-session deletion removes
  provider objects before deleting owner records. Provider deletion failure
  returns a generic HTTP 503 and preserves the owner plus failed artifact for a
  retry. Before each provider deletion SQAG retains verified prior bytes;
  confirmed deletion is counted only after the exact active metadata row is
  tombstoned and committed. Tombstone execution or commit failure rolls back
  the row and restores the provider object with identity, key, checksum, and
  size verification. Earlier committed artifacts in a multi-artifact deletion
  remain tombstoned, the failing artifact remains active, and retry processes
  only active rows. Owner deletion verifies that no active object metadata
  remains. Superseded profile,
  pricing, and generated-quote objects, including artifact kinds omitted from
  a replacement payload, use the same fail-closed replacement guard. Pricing
  visual and generated-quote export replacements are batch-scoped: all new
  provider objects are staged, every old object that may be removed is backed
  up, and provider changes are compensated if a later provider or database
  step fails. Artifact metadata and the pricing-owner or quote-session payload
  then commit in one database transaction; database/BLOB mode performs its
  omitted-kind deletes, artifact upserts, and owner/session update in that same
  transaction. Quote staging files are removed only after the batch commits.
  CI injects later-step object-store and database failures and requires the
  complete prior owner payload, artifact rows, provider bytes, availability,
  and stale state to survive unchanged. Profile layout replacement uses the
  same prepared provider batch: layout metadata and the profile payload commit
  in one database transaction, while database/BLOB mode writes layout bytes
  and profile payload in one transaction. Metadata-only profile updates retain
  the active layout without contacting object storage. Quote
  reconciliation requires a persisted XLSX, so confirmation-only outcomes
  retain prior bytes as stale instead of deleting them. Object-mode Postgres
  deletes do not query the unsupported database/BLOB artifact tables. CI
  covers these paths with synthetic in-memory objects, SQLite, and a Postgres
  query adapter only.
- `/api/health` returns HTTP 200 only while required dependencies are ready and
  returns metadata-only HTTP 503 otherwise. A short cache bounds repeated
  unauthenticated health probes.
- CI exercises these boundaries with synthetic/mocked dependencies only and
  requires no database, object-storage, Platform, OIDC, or deployment secrets.

## Not Configured

- No deployment workflow is enabled.
- CodeQL is not enabled.
- Branch protection requirements are not documented as complete in this repo yet.

## Maintenance Rule

Update this file whenever CI/CD jobs, triggers, required checks, branch protection, deployment behavior, secret requirements, or security gates change.
