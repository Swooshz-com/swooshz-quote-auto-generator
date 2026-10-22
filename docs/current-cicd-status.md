# Current CI/CD Status

Last updated: 2026-10-22 05:17 UTC

Source of truth: `.github/workflows/ci.yml`

## Hosted Alpha Trigger State

Alpha 1 remains available on accepted software
`292eb58769085d43831b278715f35b3749101290` on `deployment/alpha1`, serving the
same container and image. Automatic Git deployments and PR previews remain
disabled. At the 05:14 UTC readback, it remained healthy with restart count
zero. Authenticated manual/API deployment remains possible under separate
operation authority; a push to `main` is not an Alpha 1 promotion. The owner
accepts shared resources and intends to retire Alpha 1 only after Alpha 2 is
properly working. Retirement has not occurred and is not authorised now.

The `quote-internal` app now has normal Git-backed Compose configuration from
`deployment/alpha2` at
`f5545bc9949fb77842a4d13ede734c7cfbfbf04f`, using
`deploy/alpha2/compose.yaml`. It is manual-only, with automatic deployments
and PR previews disabled. The single supported stopped-app PATCH and official
Reload completed. One native private deployment job finished and created a
container from the accepted image. At the 05:14 UTC readback, it was stopped;
its last reported health was unhealthy, restart count was zero, and no OOM
was reported. An independent 05:17 UTC readback verified the approved phase 1
private-stop drill: healthy/running samples at 05:12:24, 05:12:26, and
05:12:28 UTC were followed by stopped readbacks at 05:13:01, 05:13:03,
05:13:05, and 05:13:07 UTC. The official stop exited 0. The container remains
stopped; Alpha 1 and peers remain preserved, and no Alpha 2 public route exists.

Compose uses accepted software
`292eb58769085d43831b278715f35b3749101290` from cached image
`sha256:c652072d3741c893b008e4898942f702ddfc83e7cfa193bbd1bd339815a67871`,
for `linux/amd64`, with 1 CPU, 2 GiB RAM, a 2 GiB combined RAM/swap limit,
a 1 GiB reservation, and `pull_policy: never`. It has no source build, host
ports, or mounts. The P1 registry proposal was superseded by the approved
P2 cached-image Compose route; no registry token or resource was created.
No image build/publish or automatic deployment workflow is enabled.

The VPS, Google, R2, account, bucket, and runtime-credential failure boundaries
remain shared, with no provider-isolation claim. The existing TEST database
and workspace are retained. The shared Google client has an additive Alpha 2
callback; existing callbacks were retained. Owner-saved DNS is verified, but
this does not establish an Alpha 2 public route or HTTPS readiness.

The source Git manifest is 711 bytes with SHA-256
`a156be91ec608be7d76cab4f1bcb3a741d0df17050da1391873e25ca7894eba0`. The
installed official SSH return is 710 bytes with SHA-256
`55b01b0311eaed98b09ceac8468c8369a9a7fcae30fcc25b380f0a51211161d5`, exactly
the source with its final LF removed. The reviewed v5 guard verifies both
identities and their direct byte relation; the original strict-predicate HOLD
remains. No generic normalisation was used.

The v4 detached-survival check, fresh 300-second admission, and two isolated
cached-helper probes passed against Compose 5.5.0 with full service-policy,
image, health, and resource-cap checks. The v3 native probe remains HOLD
because of a checker string/integer mismatch; its native output was not
retained. The v4 observer ended normally.
The v5 helper-configuration qualification passed against the native 710-byte
payload. The approved phase 1 private-stop drill passed, with official stop
exit 0 and independent verification of three healthy/running samples followed
by four stopped readbacks. A later observer reached terminal
`STOPPED_VERIFIED` through `controller_observation_loss`, with reason
`already_terminal_readback`, after the stop completed. Synchronous stop and
readbacks exceeded the observer's 35-second window. This observer status does not negate
phase 1 evidence. The later-phase custody finding remains a hold; no
hot edit or replay was performed.

The 47 offline guardian tests passed independently. The native phase 1 stop
proof does not establish shared storage, authentication, public routing, or
HTTPS readiness. TEST canonical migration 010
is already applied, with POST and named-role ACL checks passed and a preboot
workspace count of zero. The bounded accepted-image and schema/ledger recovery
is proved, but full TEST business-data restoration is not established.
Previously, accepted-image custody and isolated local load identity were
proved without running the app. The disposable schema/ledger-only recovery,
full ACL projections, and failed-transaction rollback drill passed.
The local fixture was removed and the local engine's original stopped state
restored. No migration or recovery was repeated for this configuration
update. The runtime URI exists only in app configuration; migrator and
maintenance components are absent. No paid model calls or new cloud resources
were used.

Source CI remains source-only and is not hosted product verification, owner
UAT, or G2. Actual native PDF visual checks for fonts, logo, and pagination;
hosted reopen/Next UAT; full acceptance; and representative-corpus,
entitlement, provider, and large-input assurance remain open. Product image
promotion was excluded from P2 and is not constructed or deployed. OpenRouter
migration and DeepSeek removal remain queued after Alpha 2. A product-branch
merge does not establish runtime qualification, owner acceptance, or
independent review. Alpha 2 readiness remains conditional.

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
- `Nixpacks Python-only contract`: runs `python scripts/validate_nixpacks_python_contract.py`
  and `python -m unittest tests.test_nixpacks_python_contract`. It requires
  the locked Python-only provider/archive/start contract and exact
  `[phases.setup].nixPkgs = ["...", "libreoffice", "fontconfig", "carlito",
  "liberation_ttf"]`; missing entries, wrong order, malformed arrays, duplicate,
  misplaced, alternate or Apt-substituted converter/font bindings fail closed.
- Before the readiness check, `Validate app` provisions the hosted Ubuntu
  runtime with `libreoffice-calc`, `fontconfig`, `fonts-liberation`, and
  `fonts-crosextra-carlito` using the runner's package manager.
- Deployed Nixpacks uses the already-pinned Nixpkgs archive and Nix package
  attributes, rather than the Ubuntu Apt names used by CI:

  | Purpose | CI Ubuntu package | Pinned Nix package |
  |---|---|---|
  | Workbook PDF conversion | `libreoffice-calc` | `libreoffice` |
  | Font discovery | `fontconfig` | `fontconfig` |
  | Calibri-compatible regular/bold faces | `fonts-crosextra-carlito` | `carlito` |
  | Arial-compatible regular/bold faces | `fonts-liberation` | `liberation_ttf` |

  These are equivalent runtime requirements, not interchangeable package-manager
  names. The pinned `carlito` package includes the Calibri fontconfig alias;
  `liberation_ttf` resolves to `liberation_ttf_v2` in that archive. No proprietary
  Microsoft fonts or font binaries are added to the repository.
- Nix package installation alone does not create a default fontconfig file in
  the Nixpacks image. Locked source-controlled setup commands register the
  Nix profile font directory, include the pinned fontconfig package's standard
  substitution rules and Carlito's `30-calibri.conf`, map Arial to Liberation
  Sans and build the cache before the image is published. The rule directory
  must resolve to exactly one packaged path; an ambiguous or missing path fails
  the build. Generic families retain the packaged sans/serif/monospace rules. Missing or altered registration fails the contract validator.
- `Validate app` also proves the hosted LibreOffice executable/version and
  fontconfig resolution for the canonical Arial/Calibri presentation families
  (including the pinned Linux metric-compatible Liberation Sans/Carlito
  providers) before the real rendered-PDF regression runs.
- `Validate app`: runs after retrospective fixture integrity/result and security gates pass.

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
- Verifies hosted `soffice --version` and expected font readiness before PDF
  fidelity validation; missing converter or unsupported font resolution fails
  the job rather than being treated as a passing skip.
- Runs `python scripts/validate_local_pdf_dependency_usage.py` to keep `pypdfium2` and `Pillow` usage on the local PDF rendering path only.
- The local PDF dependency guard is separate from hosted workbook conversion: it
  does not prove live PDF success. The hosted readiness step and the real
  workbook-to-PDF/render regression provide the converter/runtime proof; the
  Nixpacks contract job separately verifies the locked `libreoffice` binding.
- Runs `python scripts/validate_dynamic_pricing_reference_rules.py` to keep pricing-reference matching data-driven and block source-code semantic family/synonym packs.
- Runs `python scripts/scan_sensitive_fixtures.py --fail-on-review` so review-level sensitive fixture findings fail CI.
- Runtime privilege-contract static validation runs `python scripts/validate_runtime_privilege_contract.py`; the canonical manifest, complete unfiltered protected-role membership evaluation across parent/member/grantor positions, closed runtime-as-member/provider-control schema, exact six-column membership tuple, fourteen-key bounded verification-query set, independent executable-token contracts, exact publication-artifact column authority, the legacy `sqag_quote_artifacts` view read, the Boundary B owner-authority model, complete `r`/`S`/`f`/`n`/`T` default-ACL object-class binding, and repository requirement binding fail closed.
- Disposable PostgreSQL 17 runtime privilege-contract tests exercise the fourteen canonical query keys, exact result shapes, the automatic creator-admin control edge with ADMIN true, INHERIT false, and SET false, creator REVOKE non-removability, absence of inherited/SET/effective runtime authority, the real publication checksum-backfill path and its prescribed legacy `sqag_quote_artifacts` view read, the exact Boundary B owner authority split for database/schema ACL and PUBLIC TEMPORARY operations, complete table/column privilege matrices, complete default-ACL object classes, and database/schema grant-option semantics with disposable service databases and roles only. The hosted exact-head evidence must report zero skips.
- Boundary A remains repository-only. It performs no live database, provider, credential, Coolify, deployment, or activation mutation.
- Green CI does not authorise Boundary B or #160; those scopes require their own exact-head authority and verification.
- Runs `python -m unittest discover -s tests`.
- Runs the actual generated-workbook -> LibreOffice -> PDF -> PDFium raster and
  geometry regression, including canonical workbook styles, dimensions, bold
  headers, merges/header placement, logo aspect/no-overlap, print area,
  scaling, margins, page count, and deterministic rendered output.
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
- Normal and High Quality full Analyse each require explicit model and reasoning
  settings in the deployed process environment (Coolify): `OPENAI_DRAFT_MODEL`,
  `OPENAI_DRAFT_REASONING_EFFORT`, `OPENAI_DRAFT_HIGH_QUALITY_MODEL`, and
  `OPENAI_DRAFT_HIGH_QUALITY_REASONING_EFFORT`. Each operation captures its
  selected mode's pair once for payload validation and telemetry. Missing, blank
  or malformed settings fail before provider transport; source defaults
  and local `.env` values cannot fill deployed gaps. The requested runtime pairs
  are `gpt-6.1-sol/high` and `gpt-6-astra/high`. Bounded model and effort
  identifiers pass through without a source allowlist. The provider owns model
  access, reasoning compatibility and image/PDF capability; an incompatible pair
  fails truthfully after one send without a model/effort downgrade. Arbitrary
  configured model labels are redacted from logs, telemetry and evidence. Local
  development retains `.env` support. These request checks do not change
  process-only liveness or perform
  configuration mutation, deployment or provider verification.
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
- `/api/health` is process-only liveness: it returns HTTP 200 with
  `{"status":"ok"}` and performs no dependency, provider, or warm-up call.
  Deploy startup keeps one forced internal dependency-readiness probe before
  listener construction; readiness is not exposed as a public endpoint.
- After valid Platform launch or finalization admission, one short-lived
  advisory runtime-database warm attempt may run with the runtime role and
  exactly `SELECT 1`; it cannot change auth, readiness, or response status.
- CI exercises these boundaries with synthetic/mocked dependencies only and
  requires no database, object-storage, Platform, OIDC, or deployment secrets.
- Internal-alpha requires a second PDF export path: an explicit PDF request
  regenerates a fresh `quotation.xlsx` from the current reviewed state, converts it
  through the installed workbook-mode `libreoffice`/`soffice` dependency, then
  persists and session-associates the PDF for authorized download. XLSX remains the
  master/default export; CI validates the contract but not live PDF success.

## Not Configured

- No deployment workflow is enabled.
- CodeQL is not enabled.
- Branch protection requirements are not documented as complete in this repo yet.

## Maintenance Rule

Update this file whenever CI/CD jobs, triggers, required checks, branch protection, deployment behavior, secret requirements, or security gates change.
