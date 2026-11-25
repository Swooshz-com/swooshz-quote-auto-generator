# Current CI/CD Status

Last updated: 2026-11-19

Sources of truth: `.github/workflows/ci.yml`, `.github/workflows/plain-text.yml`,
and the two Toolkit Programme Sweep workflows
(`toolkit-programme-sweep-gate.yml` and `toolkit-programme-sweep.yml`).

The cloud programme sweep runs on pushes to `main` or by dispatch. The hourly
gate also starts it on `main` after eligible repository activity or CI.
When no sweep is running, most activity waits an hour after the latest sweep
run. Closing an issue can start a sweep immediately unless one is already queued
or the GitHub App rate limit still applies.
The hourly check measures activity from the last run that did work, so a later run that
finished green while waiting for the sweep lease cannot hide pending changes.
A pause at the GitHub App rate limit still holds retries until the limit resets.
The gate can read check annotations with the built-in token. Hourly and
manual gate runs flag three consecutive held sweeps, sweeps skipped for the
same reason, or hourly gates skipped because governance is off.
The sweep checks the selected ref and programme binding before minting
separate GitHub App tokens for the quote repository and its private evidence
store. Both use the existing App variable and secret. An off-main run or
invalid binding leaves both token steps skipped.

The sweep reads large state documents by Git blob ID when GitHub truncates
them in the bulk response, within the sweep's request budget. After writing
nested state, it verifies the saved blob IDs against the bytes it wrote.
After taking its lease, the cloud sweep checks the private evidence store for
queued controller orders before refreshing the board. Orders must name the
current programme parent, holder and holder epoch, specify allowed operations,
and still be within their expiry time. A rehearsal makes no programme writes.
The worker checks expiry before starting each operation. If the deadline
passes during an operation, the worker keeps that operation and its receipt,
stops before the next operation and files the order as stopped. Retries skip
operations already recorded as complete. The worker removes a pending file
only after it saves and reads back the result. If removal is interrupted, a
later pass preserves the verified result for that queued file and retries
only removal, even after expiry. The workflow summary reports counts.
Order details and receipts stay in the private store.

Footer cleanup requires both App tokens and the refresh signal from the same
commit. The worker withholds the signal when it waits for the lease, hits the
App rate limit, cannot verify state read-back, encounters a hold with no saved
state, or finds a board rendered by a newer Toolkit. A clean run with no
changes or a partial run with a verified save can still emit the signal.

The programme board lists unblocked assigned or active work first in
Waiting on and In progress. If an owner test is still owed on an earlier
pull request, the board lists it after that gate.

Both managed workflows pin Toolkit 3.145.0 (`d667ad5`). When an unfinished task
records no pull request or branch and exactly one open pull request from a
branch of this repository claims it, the sweep records that pull request, its
branch and head, and moves the task from planned to active. Several claimants,
a fork, bot or generated branch, or a finished task stay report-only as
`PR_UNTRACKED`. For an unfinished task,
the sweep can follow a single newer open pull request that explicitly claims
the task after its tracked pull request is closed or merged. If the tracked
pull request closed unmerged and there is no eligible open replacement, the
sweep can follow the newest eligible merged one. Ambiguous open claims leave
the tracked pull request in place. Finished tasks can replace a closed unmerged
pull request only with a newer merged claimant. Retired tasks keep their
tracked pull request.

After an approved merge, verify the first eligible App-backed sweep on
`main` using the [programme sweep checks](testing-plan.md#programme-sweep-and-gate).
Read back an actual graph advance from the saved state and match its revision and
hash to the published board. To verify replacement, record an eligible claimant
at the time of the sweep and confirm the saved child follows it. Verify the
installed worker with a run at the new pin. A queued or held run stays pending
until the save and redraw are verified.

Python discovery includes `tests.test_quote_api_auth` and
`tests.test_quote_api_tenancy` for the disabled admission boundary.
`tests.test_quote_api_hardening` adds normalised v1 log canaries, registry ID
and failure-code guards, duplicate staff cookies, a fixed verifier vector,
disabled Platform launch mode, role allow-list and missing Origin/Referer checks.
These run through existing discovery with synthetic data and loopback HTTP.
The switches
`SQAG_QUOTE_API_SERVICE_ENABLED` and
`SQAG_QUOTE_API_STAFF_TRANSPORT_ENABLED` default off; only exact `true` enables
their independent admission paths. No CI settings or secrets enable them. Tests
use synthetic in-memory registry injection and loopback HTTP only. The runtime
has no default registry composition or v1 work engine; valid admission returns 503,
never accepted work. No job, trigger, required check, secret requirement
or deployment behaviour changes. Migration 012 is source only: two registry tables,
column-only runtime grants and a bounded lookup routine. Full Python discovery
includes its synthetic SQLite controls; the existing disposable PostgreSQL 17
job includes migration, ACL and two-worker/CAS integration controls. Real PG
tests skip on an offline host and must run without required skips in CI. No
startup apply, seeded key, default tenant, live apply or flag enablement is supplied. Independent security review and installed-host
qualification are still required before later enablement.

The disposable PostgreSQL 17 privilege-contract suite also qualifies current
startup, uncached health and request checks with only migrations 001-011 applied,
then repeats after explicit 012 application and with the old forensic inventory.
Dedicated admission-function body, dependency and EXECUTE drift must fail.
No additional job, service, secret or startup migration is introduced.

Full Python discovery includes `tests.test_libreoffice_workbook_values`: actual
LibreOffice recalculates a disposable cache-cleared workbook, then each priced
and total amount, and each Included row's Included text, is checked in its own row. A missing converter fails in
CI and explicitly skips locally. Native conversion and browser receipts still
require an equipped host; no workflow, trigger, required check, secret requirement
or deployment behaviour changes accompany this test.

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
- CI does not require OpenAI, DeepSeek, Gemini, internal password login, deployment, or production secrets

## Plain-Text Check

- Workflow: `.github/workflows/plain-text.yml`; runs on pull requests and pushes to every branch.
- Uses read-only repository contents, pinned checkout and Node actions, no persisted checkout credentials, and a ten-minute timeout.
- Checks the exact pull-request head or pushed commit with Node 22 and no additional dependencies or secrets.
- Runs `.github/scripts/plain-text-check.cjs` using `.github/plain-text.json` with the default reference rules and no custom banned terms.
- Existing documentation references are recorded once in `.github/plain-text-baseline.json`; new references fail and the baseline can only shrink.
- Rewrite failures in plain English, then run `node .github/scripts/plain-text-check.cjs`; use `--prune-baseline` after removing old references.
- The existing application workflow and its security gates are unchanged. No deployment job or required branch-protection check is added.

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
- `Quote Basis click budget`: runs after the retrospective fixture and security
  gates pass, on `ubuntu-24.04` with a 20-minute timeout and no secrets. It
  installs the locked Node and Python dependencies and Playwright Chromium, then
  runs `npm run playwright:click-budget` against the local synthetic server (probes and count checks
  in `scripts/lib/basis-click-probe.mjs`, shared with Pass A of the live-data test). It
  fails when a Quote Basis tick or X writes the browser copy more than once,
  rewrites unchanged reference files, re-renders or rebuilds the list, saves the
  quote more than once per burst or later than 1.5 s after the click, sends files the server already has, or goes
  over its byte, long-frame or input-to-paint budget. It does the same on that
  quote reopened at the largest real quote's size, where the byte budgets come
  from the quote's own browser copy. It is a required check on
  `main` (see Required Checks below).

## Validate App Checks

- Pins the `Validate app` runner to `ubuntu-24.04`, which supports the locked
  Playwright 1.64.1 browser dependencies. Other jobs retain their existing
  runners; checks, triggers, permissions and dependencies are unchanged.
- Installs Python 3.12.13 (the exact patch shared with the retrospective job) and Node 22.
- Installs pinned Python dependencies with `python -m pip install --only-binary=:all: -r requirements.txt`.
  This current application dependency set is used only by `Validate app`; the
  retrospective job uses its own closed fixture dependency snapshot and does
  not read or install from `requirements.txt`.
- The retired internal Google verifier no longer adds PyJWT or cryptography
  to `requirements.txt`. The local OIDC provider adapter is retired. Platform
  contracts retain their standard-library HTTP and HMAC session helpers; those
  helpers do not require the removed packages. Tests use synthetic dependencies.
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
- Runs `python -m unittest discover -s tests` without validation-scope overrides,
  including fresh and saved Modify/restoration browser pricing checks.
- Runs the actual generated-workbook -> LibreOffice -> PDF -> PDFium raster and
  geometry regression, including canonical workbook styles, dimensions, bold
  headers, merges/header placement, logo aspect/no-overlap, print area,
  scaling, margins, page count, and deterministic rendered output.
- Runs `npm run playwright:ai-stress`.
- Runs `npm run playwright:internal-auth`.
- Runs `npm run playwright:quote-templates`: the Settings > Quote Templates editor on a synthetic SQLite
  database server, admin and operator, including a real PDF preview through the job's LibreOffice.
- Runs `npm run playwright:smoke` and `npm run playwright:alpha-smoke`, plus `npm run playwright:smoke -- --recovery-only` in a separate
  process with fresh quote state. The isolated `npm run playwright:smoke -- --terminal-only` step checks live
  needs-review, blocked, failed and interrupted polling without reload. `npm run playwright:rehearsal-check` runs
  `scripts/rehearsal-check.mjs` (seed, verify without AI, reopen, live-readonly and the live-data walk) against a loopback
  synthetic server behind a logging proxy, checks at the server that live-readonly sends no write, and checks
  its argument, base-URL and read-only refusals. It runs every profile twice: once on a file-storage server,
  and once on a database-storage server in live's modes (`SQAG_STORAGE_MODE=database`,
  `SQAG_ARTIFACT_STORAGE_MODE=object`) built by `tests/disposable_database_mode.py`: a uniquely named
  database on the job's disposable PostgreSQL 17 service, migrations through 011 as on live, the runtime
  contract's grants, and an in-memory object store. The harness drops the database (and any roles it
  created) when it stops. Under `CI=true` a missing PostgreSQL service fails this run instead of skipping it. All eight browser steps remain gated by the successful
  `runtime_prerequisites` privilege-contract validation, including after a
  Python test failure.
- The default app smoke covers reload, saved reopen and generation recovery,
  then runs the isolated mocked-provider pricing/current-XLSX checks. The separate
  recovery step also checks expired jobs do not resume and pricing-reference
  selection commits on Customer Next; those two checks are absent from the
  default broad run. Both app paths drain the existing 60-second write window
  before generation recovery and require resumed final saves to return HTTP 200.
  Any observed HTTP 429 on quote-session endpoints fails the app smoke, since
  throttled saves cannot qualify persisted state. Server limits are unchanged.
  Default and focused G3 runs use installed Playwright Chromium; screenshot mode
  keeps the broad visual run and skips the isolated G3 tail. The native
  alpha smoke exercises saved edits, real workbook/PDF generation and access
  denial. Its workbook checks bind description, quantity and amount to the same
  row, verify totals/formulas, formatting, original uploaded logo and print area,
  and join accepted-job bytes, independently persisted publication metadata,
  the comparison-bound protected URL, owning-server HTTP status and native
  browser download size/SHA256. Currency and tax expectations come from the
  selected reference config; customer/project/company text comes from entered
  fixture inputs. Header and logo anchors must lie within print-area rows and columns and match the selected template geometry,
  and quantity/estimate headers retain their required alignment. Protected
  readbacks also check original render/logo bytes, the Customer checkpoint digest
  and persisted Included authority/zero amount. Exact complete-draft equality
  and saved-profile binding through generation are not claimed; the owner UAT
  carrier separately checks saved-profile selection.
  No live AI provider is used.
- Owner basis UAT uses `tests/owner_uat_mock_harness.py` with a caller-written
  synthetic scenario and network denial. Its lifecycle tests cover normal and
  exceptional shutdown.
- Last CI check-up: 2026-11-05, local read-only check against e83883b; hosted
  job times and required-check settings were not accessible from this sandbox.
  The changed-workflow check reports ten existing action references without
  full commit pins. Action pinning remains separate from browser scope selection.

## Security And Secrets

- `.env.example` must contain placeholders only.
- Local `.env` files stay ignored and must not be committed.
- CI must stay free of production/customer secrets unless a future deployment design explicitly documents the new boundary and approval path.
- CI never applies migrations against a configured production database. The
  real PostgreSQL migration tests create and remove only disposable databases
  inside the job-scoped service.
- GitHub repository settings (free on a public repository): Dependabot security
  updates, secret scanning with push protection, CodeQL default setup and private
  vulnerability reporting are on.
- Dependabot opens weekly version updates for pip, npm and GitHub Actions, with
  patch and minor updates grouped and a short cooldown (seven days for GitHub
  Actions, three for the rest). Nothing merges on its own: every update PR waits
  for review.

## Deploy Runtime Gate

- The deploy-template verifier permits the optional
  `OPENROUTER_DRAFT_BATCH_REASONING_EFFORT` key to be absent, empty or the reviewed
  placeholder. Runtime capture validates a nonempty value against the existing
  effort set before any send. The four-send page-batch pool retains the 18-send
  ceiling and zero retries; no CI job, trigger or required check changes.

- CI still performs no deployment.
- The internal redeployment flow is a manual, rehearsed operator procedure
  (`docs/internal-uat-coolify-deploy.md`). Its offline checkers
  (`scripts/check_release_settings.py`, `scripts/check_rehearsal_map.py`), the
  bucket clear script and the build script's dry run and fake-docker build are
  covered by the existing `python -m unittest discover -s tests` step. Its
  remote check, `scripts/rehearsal-check.mjs`, is tested by the browser job's
  `npm run playwright:rehearsal-check` step against loopback synthetic servers only (file
  storage and database storage); CI never reaches the rehearsal or live app. No trigger, secret or required
  check changes. The live-data end-to-end test (Pass A on a copy of live before the switch, Pass B
  read-only on live after it, `scripts/copy_live_objects_to_rehearsal.py`, `scripts/live_rollback.py`) is
  part of that manual procedure, run from the laptop; CI runs only its offline and loopback tests
  (`tests/test_live_e2e.mjs` in the same npm step, the Python tests in the unittest step). In database
  mode the loopback run fails if Pass B changes any `sqag_` table's row count, telemetry included. The
  production-branch guard (`scripts/neon_branch_guard.py`) is tested offline with a fake production
  host; CI never calls Neon.
- All six maintained inference operations require `OPENROUTER_API_KEY` and
  explicit `OPENROUTER_<SUFFIX>_MODEL`,
  `OPENROUTER_<SUFFIX>_REASONING_EFFORT` and
  `OPENROUTER_<SUFFIX>_PROVIDER_ONLY` settings in the deployed process environment.
  The suffixes, timeout and required privacy/routing settings are defined in
  [the gateway contract](ai-gateway.md). Each operation captures one immutable
  configuration for payload validation, transport and telemetry. Missing, blank
  or invalid settings fail before transport; source defaults and local `.env`
  values cannot fill deployed gaps. There is no provider/model fallback or
  silent effort downgrade. Arbitrary configured model labels are redacted from logs,
  telemetry and evidence. Local development retains explicit `.env` support.
  These source checks do not verify installed configuration, account access or
  exact Responses image/PDF/effort/privacy compatibility, and do not change
  process-only liveness or perform configuration mutation or deployment.
- `APP_MODE=deploy` requires an explicit `SQAG_AUTH_MODE` of `platform` or
  `internal_password`, plus `AUTH_REQUIRED=true`; missing, unknown, local, mixed,
  or incomplete configurations fail closed.
- `platform` preserves the Swooshz Platform launch, workspace, entitlement, and
  per-request validation/revocation boundary.
- `internal_password` is a temporary single-instance private-alpha lane with
  separate semicolon-delimited operator/admin alias lists and distinct shared
  passwords for each configured role. Optional server-only continuity bindings
  preserve stable IDs; the role lists grant access, with admin precedence on
  overlap. It retains per-request policy revalidation and server-side revocation.
  See [internal staff password access](internal-password-auth-mode.md) for the
  exact settings and limits. Mixed legacy configuration is rejected. It can
  never satisfy public production readiness.
- The offline internal UAT template check permits an omitted or empty
  `SQAG_INTERNAL_IDENTITY_BINDINGS_JSON` or its approved placeholder, while
  retaining generic secret scans and rejecting raw JSON and other markers.
  Runtime preflight and startup permit omission, empty or `[]`; supplied bindings
  retain strict validation. Synthetic hosted metadata excludes this optional key
  from required names and keeps it secret-manager-only if supplied. These checks
  do not inspect the live host. CI jobs, triggers and required checks are unchanged.
- Platform deploy requests accept only signed sessions containing the consumed Platform
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

## Required Checks

The `protect-main` repository ruleset on the default branch requires changes to
arrive through a pull request, blocks deleting the branch, and requires one
status check from GitHub Actions to pass before a merge:

- `Quote Basis click budget` (owner approval 2026-11-19). Branches do not need
  to be up to date with `main` before merging.

No other check is required by GitHub; the remaining CI jobs are checked green on
the exact pull request head before every merge but are not enforced by the
ruleset.

## Not Configured

- No deployment workflow is enabled.

## Maintenance Rule

Review the `Validate app` Ubuntu 24.04 pin when the locked Playwright version supports a newer runner and the full rendered gate passes. The `ubuntu-latest` move to Ubuntu 26.04 currently fails browser installation before tests; remove the pin only after that compatibility is verified.

Update this file whenever CI/CD jobs, triggers, required checks, branch protection, deployment behavior, secret requirements, or security gates change.

## Login and inference source contract

CI compiles `webapp/internal_password_auth.py`, `webapp/ai_gateway.py` and
`tests/internal_password_browser_harness.py`. The existing security, unit,
retrospective, native-output and browser gates remain required. CI makes no real
provider calls and requires no login or OpenRouter secret.

Runtime configuration replaces the maintained Google identity map with
the [role-specific lists and passwords](internal-password-auth-mode.md), with
server-only continuity bindings. Existing workspace, stable user IDs and signing
and recovery secrets must remain unchanged when a separately approved cutover is
performed. Shared-password holders can select any configured alias and its role.

All maintained inference uses `OPENROUTER_API_KEY` and explicit model, reasoning
and downstream provider settings for each operation. See
[the gateway contract](ai-gateway.md). Live route preservation, identity continuity,
privacy/account compatibility, installed agent checks and owner acceptance remain
unqualified by mocked CI. This source change adds no deployment job or authority.
