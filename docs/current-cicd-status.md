# Current CI/CD Status

Last updated: 2026-07-31

Source of truth: `.github/workflows/ci.yml`

## Active Workflow

- Workflow name: `CI`
- Triggers: pull requests, pushes to `main`, and manual `workflow_dispatch`
- Permissions: read-only repository contents
- No deployment job is configured
- This phase-planning PR adds no deployment job
- No production environment mutation is performed by CI
- CI does not require OpenAI, DeepSeek, Gemini, Google OIDC, deployment, or production secrets

## Active Jobs

- `Secret scan`: checks the repository with Gitleaks before validation work proceeds.
- `Dependency audit`: installs Node dependencies with `npm ci` and runs `npm audit --audit-level=high`.
- `Validate app`: runs after the security gates pass.

## Validate App Checks

- Installs Python 3.12 and Node 22.
- Installs pinned Python dependencies with `python -m pip install --only-binary=:all: -r requirements.txt`.
- Uses pinned PyJWT and cryptography packages for synthetic RS256/JWK and OIDC
  claim-validation compatibility tests; CI never contacts Google.
- Installs `pip-audit` and runs `python -m pip_audit -r requirements.txt --strict` against pinned Python dependencies.
- Installs Playwright Chromium.
- Starts an isolated disposable PostgreSQL 17 service for migration-ledger
  integration tests. The service has no production connectivity or provider
  credentials. Before the main test suite CI asserts the running PostgreSQL
  major version via `scripts/assert_postgres17.py`; an unexpected major version
  fails the job. This assertion does not prove production Neon, Coolify or
  hosted deployment state.
- Checks JavaScript syntax for `webapp/static/app.js`, `scripts/playwright-smoke.mjs`, and `scripts/playwright-ai-basis-chat-stress.mjs`.
- Checks Python syntax for `webapp/server.py`, quote/pricing scripts, and validation guard scripts.
- Runs `python scripts/validate_local_pdf_dependency_usage.py` to keep `pypdfium2` and `Pillow` usage on the local PDF rendering path only.
- Runs `python scripts/validate_dynamic_pricing_reference_rules.py` to keep pricing-reference matching data-driven and block source-code semantic family/synonym packs.
- Runs `python scripts/scan_sensitive_fixtures.py --fail-on-review` so review-level sensitive fixture findings fail CI.
- Runtime privilege-contract static validation runs `python scripts/validate_runtime_privilege_contract.py`; the canonical manifest, thirteen-key bounded verification-query set, independent executable-token contracts, and repository requirement binding fail closed.
- Disposable PostgreSQL 17 runtime privilege-contract tests exercise the thirteen canonical query keys, exact result shapes, complete table/column privilege matrices, and database/schema grant-option semantics with disposable service databases and roles only. The hosted exact-head evidence must report zero skips.
- Boundary A remains repository-only. It performs no live database, provider, credential, Coolify, deployment, or activation mutation.
- Green CI does not authorise Boundary B or #160; those scopes require their own exact-head authority and verification.
- Runs `python -m unittest discover -s tests`.
- Runs `npm run playwright:ai-stress`.
- Runs `npm run playwright:smoke`.

## Security And Secrets

- `.env.example` must contain placeholders only.
- Local `.env` files stay ignored and must not be committed.
- CI must stay free of production/customer secrets unless a future deployment design explicitly documents the new boundary and approval path.
- CI never applies migrations against a configured production database. The
  real PostgreSQL migration tests create and remove only disposable databases
  inside the job-scoped service.

## Deploy Runtime Gate

- CI still performs no deployment.
- `APP_MODE=deploy` requires an explicit `SQAG_AUTH_MODE` of `platform` or
  `internal_google`, plus `AUTH_REQUIRED=true`; missing, unknown, local, mixed,
  or incomplete configurations fail closed.
- `platform` preserves the Swooshz Platform launch, workspace, entitlement, and
  per-request validation/revocation boundary.
- `internal_google` is a temporary single-instance private-alpha lane with
  one bounded server-only subject/email/role identity map, verified Google OIDC
  code flow, per-request mapping revalidation, and server-side session
  revocation. Legacy email-list configuration is rejected. It can never
  satisfy public production readiness.
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
- Deploy preflight requires `SQAG_PLATFORM_SERVICE_SECRET` to contain at least
  32 characters. `POST /api/platform/launch` requires exactly one
  `X-SQAG-Service-Authorization` value and validates it before launch-token
  consume or any finalization/session side effect.
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
- Object-backed profile, pricing-reference, and generated-quote saves and
  deletes now acquire one database-backed lifecycle boundary keyed by the
  canonical workspace ID, owner type, and validated owner ID before reading
  snapshots or mutating provider objects. Postgres uses a transaction-scoped,
  non-blocking advisory lock derived from that canonical identity; contention
  fails closed with the generic storage HTTP 503 before provider mutation, and
  commit or rollback releases the lock. The signed 64-bit digest has a
  theoretical collision boundary that can only serialize unrelated owners or
  fail them closed, not merge their workspace-scoped SQL predicates. SQLite
  uses `BEGIN IMMEDIATE`, which intentionally serializes local/test writers
  more broadly. The fresh zero-active assertion and owner delete occur in the
  same transaction as artifact tombstones, so a save cannot commit active
  metadata after deletion has passed a stale check. Per-artifact savepoints
  preserve confirmed partial deletion and retry semantics; savepoint rollback
  keeps provider compensation inside the lifecycle boundary. This design adds
  no schema or external lock service. CI proves the SQL order and deterministic
  SQLite interleavings with synthetic adapters only; live Postgres concurrency
  remains hosted evidence.
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
