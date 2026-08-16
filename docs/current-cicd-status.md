# Current CI/CD Status

Last updated: 2026-08-16

Source of truth: `.github/workflows/ci.yml`

## Active Workflow

- Workflow name: `CI`
- Triggers: pull requests, pushes to `main`, and manual `workflow_dispatch`
- Permissions: read-only repository contents
- Every checkout binds to the pull request's exact `pull_request.head.sha`, or
  to `github.sha` for push/manual runs; hosted results therefore identify the
  tested source head instead of relying on a synthetic pull-request merge ref.
- No deployment job is configured
- This remediation adds no deployment job
- No production environment mutation is performed by CI
- CI does not require OpenAI, DeepSeek, Gemini, Google OIDC, deployment, or production secrets

## Active Jobs

- `Retrospective fixture integrity and RED reproduction`: checks out the exact
  workflow head, validates a repository-contained closed fixture manifest and
  every preserved-input digest. Raw manifest paths are rejected before path
  normalisation when they contain traversal, separator, drive-relative,
  colon/ADS, reserved-device, trailing-dot/space, control-character, or
  Unicode-normalisation hazards. Fixture enumeration rejects symbolic links,
  reparse points/junctions, hard links, and non-regular entries. Each payload
  is read once through a checked descriptor; materialisation writes those exact
  verified bytes and never rereads a mutable source path. The runner requires
  the executing interpreter to be CPython 3.12.13 and keeps the explicit empty
  third-party package snapshot. It materialises only that fixture in one
  bounded temporary directory and runs the exact thirteen-test selection. It
  requires exactly thirteen assertion failures, zero errors, zero unexpected
  passes, and zero skipped required tests. An independent strict receipt
  parser enforces the closed schema, exact types, fixed values, process
  posture, and safety flags. Real timeout, stdout/stderr overflow, signal,
  reader, and cleanup tests cover the bounded child lifecycle. The runner
  removes the temporary directory and emits only one bounded public-safe
  receipt. It performs no historical Git lookup and is retrospective evidence,
  not the original RED chronology or original development sequence.
- `Secret scan`: checks the repository with Gitleaks before validation work proceeds.
- `Dependency audit`: installs Node dependencies with `npm ci` and runs `npm audit --audit-level=high`.
- `Validate app`: runs after retrospective fixture integrity/result and security gates pass.
- `A23 real PostgreSQL-17 A/B/C/P finality`: a mandatory exact-head job that installs the pinned Python dependencies, runs the focused finality regressions, then creates four fresh disposable PostgreSQL 17 containers and volumes. It replays the canonical migration manifest, derives the whole live `pg_catalog` field universe, records hashed `pg_toast` authority boundaries, proves semantic/reference/authority convergence across A/B/C, performs P's bounded insert/`VACUUM (ANALYZE)`/delete restoration witness, and verifies cleanup. It never contacts a provider, production database, credential, deployment, or external service.

## A23 PostgreSQL-17 Finality Job

- The `postgresql17-finality` job is a separate required gate and is not allowed to pass by skipping the real path.
- It runs `python scripts/validate_postgresql17_finality.py --real-references --json` on PostgreSQL 17 only. Synthetic contract output is retained for unit coverage but cannot satisfy the live gate.
- The live receipt must report labels A/B/C/P, distinct container/volume/cluster identities, nonzero executed fields and values, exact canonical migration replay, public-safe authority boundaries, P maintenance variance, restored semantic equality, and verified cleanup.
- This job is local/disposable proof only; a green result does not authorize provider, production, deployment, or issue/tracker mutations.

## Validate App Checks

- Installs Python 3.12.13 (the exact patch shared with the retrospective job) and Node 22.
- Installs pinned Python dependencies with `python -m pip install --only-binary=:all: -r requirements.txt`.
  This current application dependency set is used only by `Validate app`; the
  retrospective job uses its own closed fixture dependency snapshot and does
  not read or install from `requirements.txt`.
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
- Runtime privilege-contract static validation runs `python scripts/validate_runtime_privilege_contract.py`; the canonical manifest, complete unfiltered protected-role membership evaluation across parent/member/grantor positions, closed runtime-as-member/provider-control schema, exact six-column membership tuple, fourteen-key bounded verification-query set, independent executable-token contracts, exact publication-artifact column authority, the legacy `sqag_quote_artifacts` view read, the Boundary B owner-authority model, complete `r`/`S`/`f`/`n`/`T` default-ACL object-class binding, and repository requirement binding fail closed.
- Disposable PostgreSQL 17 runtime privilege-contract tests exercise the fourteen canonical query keys, exact result shapes, the automatic creator-admin control edge with ADMIN true, INHERIT false, and SET false, creator REVOKE non-removability, absence of inherited/SET/effective runtime authority, the real publication checksum-backfill path and its prescribed legacy `sqag_quote_artifacts` view read, the exact Boundary B owner authority split for database/schema ACL and PUBLIC TEMPORARY operations, complete table/column privilege matrices, complete default-ACL object classes, and database/schema grant-option semantics with disposable service databases and roles only. The hosted exact-head evidence must report zero skips.
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
