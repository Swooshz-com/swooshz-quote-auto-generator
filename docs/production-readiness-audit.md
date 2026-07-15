# SQAG Production-Readiness Audit
## 2026-07-15 Generation Forensics, Feedback, And Retention Audit

Audit branch: `codex/sqag-forensics-feedback-retention`

Base SHA: `92956c56181b4e7377303b205787b6dbffec7a5f`

App type: full-stack Python HTTP service with a static JavaScript frontend,
SQLite local/test storage, Postgres-compatible metadata storage, and
S3-compatible object-artifact storage.

Audit scope: full repository, focused on generation lifecycle integrity,
protected forensic evidence, privacy-minimised audit/operational telemetry,
workspace-scoped feedback, retention/deletion, legal surfaces, readiness gates,
and their existing storage/auth/object boundaries. No live system, deployment,
external service, credential, payment, customer-data, or private-data action is
authorised or used.

### RED-Before-GREEN Findings

| Severity | Finding | Current evidence | Required remediation |
| --- | --- | --- | --- |
| P0 | Generation attempts are not durably tracked before validation and do not have an immutable canonical evidence record or append-only audit lifecycle. | `create_job()` validates before assigning a `job_id`, keeps lifecycle state only in the process-global `JOBS` dictionary, and `run_quote_job()` can return completion after quote-session/artifact persistence without a mandatory generation-run or completion-audit commit. | Assign a durable `generation_run_id` before validation, persist workspace/actor-scoped run state and canonical evidence, append terminal audit events, and fail closed in deploy mode when mandatory persistence disagrees or is unavailable. |
| P1 | Deploy metadata schemas do not contain generation runs, audit events, canonical evidence metadata, feedback reports, feedback status history, legal holds, or deletion replay records. | Migrations `001`-`003` cover profiles, pricing references, quote sessions, DB-BLOB artifacts, and object-artifact metadata only. | Add additive SQLite/Postgres-compatible metadata migration and make deploy schema preflight require it. |
| P1 | Users have no in-product bug-report or feedback workflow and no safe automatic/manual diagnostic linking. | No feedback/report endpoint, storage model, support reference, app-shell action, or support-triage boundary exists. | Add authenticated workspace-scoped submission, safe context suggestions, manual-reference resolution, support/admin access, and audited status/forensic access. |
| P1 | Current retention policy conflicts with the fixed product policy and has no calendar-aware automatic deletion/legal-hold implementation. | The synthetic internal-alpha policy uses 90/180-day app-data periods and 30-day logs; existing object tombstones are lifecycle groundwork, not a three-calendar-year generation/feedback policy or exact 90-day production-log worker. | Add calendar-aware expiry, bounded idempotent retention processing, legal holds that preserve original expiry, partial-delete retry state, and backup deletion-replay evidence. |
| P1 | Operational identity fields are privacy-minimised incompletely. | AI log tracking currently records raw `user_id` and `account_id`; sanitisation omits many raw-content keys but there is no versioned keyed pseudonymisation boundary. | Add versioned HMAC pseudonyms for actor/workspace references, fail closed for deploy audit persistence without a key, and add adversarial redaction tests. |
| P1 | Product-facing legal disclosure does not describe forensic evidence, feedback linking, three-year retention, exact 90-day production logs, legal holds, or residual backups; Terms are absent. | `/privacy` is generic, `/terms` does not exist, and the app shell links only Privacy Notice. | Amend the engineering baseline and Privacy Notice, add counsel/owner-reviewable SQAG Terms or a documented Platform dependency, link legal/support surfaces, and retain legal approval blockers. |

### Existing Controls Preserved

- Platform-authenticated workspace identity and role mapping fail closed in
  deploy/database/object modes.
- CSRF/same-origin checks, mutable-route rate limiting, safe input bounds,
  generic user errors, and support-safe error references exist.
- Generated object artifacts use workspace-owned metadata, checksums, guarded
  replacement/deletion, tombstones, compensation, and retry-safe lifecycle
  boundaries.
- Quote sessions preserve a privacy-minimised generation snapshot, but it is
  not the exact immutable evidence required for run reconstruction.
- Ordinary log writes use an allowlisted event boundary and omit recognised
  raw-content/secret fields; this is useful groundwork, not full adversarial
  sanitisation or keyed pseudonymisation.

### Approved Verification Scope

- Codex Security: fresh standard repository scan `18f229ec-ee43-4085-a96e-8d40cf554784` completed with one Medium/P2 finding, `SQAG-SEC-001`, remediated in this working tree.
- Browser verification: Playwright app smoke, AI-stress smoke, and feedback/Terms/Privacy flow passed. Feedback/legal screenshots were written under `_logs/browser/forensics-feedback-retention/`.
- Live/deploy/external/provider evidence: not required for this task and remains an explicit readiness blocker. Production readiness stays false until hosted/live owner-approved evidence is rerun and reviewed.

### Remediation Batches

1. Add schema, storage primitives, calendar-aware retention, HMAC identity,
   immutable generation evidence, and append-only audit events.
2. Integrate the generation lifecycle and reconcile non-terminal runs without
   creating false completion states.
3. Add feedback persistence, context linking, support access, status history,
   and audited forensic retrieval.
4. Add Privacy/Terms/support surfaces and update readiness/observability policy.
5. Run focused RED/GREEN tests, full repository validation, standard security
   scan, and isolated Playwright verification; record exact results below.

`production_ready=false`

### 2026-07-15 Validation Record

- `git diff --check`: passed.
- JavaScript syntax checks: `webapp/static/app.js`, `scripts/playwright-smoke.mjs`, `scripts/playwright-ai-basis-chat-stress.mjs`, `scripts/playwright-download-excel-confirm-regression.mjs`, and `scripts/playwright-forensics-feedback.mjs` passed.
- Python syntax checks: server, generator, forensics, retention, observability, production DB, and live retention verifier modules passed.
- `python -m unittest discover -s tests`: 791 tests passed.
- `python -m pip check`: passed.
- `python -m pip_audit -r requirements.txt --strict`: passed, no known vulnerabilities found.
- `npm ci`: passed, installed/audited 3 packages.
- `npm audit --audit-level=high`: passed, 0 vulnerabilities.
- `python scripts/validate_local_pdf_dependency_usage.py`: passed.
- `python scripts/validate_dynamic_pricing_reference_rules.py`: passed.
- `python scripts/scan_sensitive_fixtures.py --fail-on-review`: passed, 0 blocking and 0 review findings.
- Synthetic verifiers passed: forensics/feedback/retention, hosted observability, database backup/restore, hosted smoke, object-storage contract, and object-artifact lifecycle.
- Expected fail-closed verifiers: live retention/delete, production database provider, live DB+object backup/restore, and production readiness remained blocked/failed without required owner-supplied live evidence or deployment operations evidence.
- `npm run playwright:ai-stress`: passed with no console problems.
- `npm run playwright:smoke`: passed with no console or network problems.
- `node scripts/playwright-forensics-feedback.mjs`: passed on a fresh local server with health verified and `production_ready=false`.
- `npm run playwright:download-confirm`: not part of the documented baseline and remains a stale dashboard-first regression harness; it times out waiting for Quote Basis to be visible immediately after load.


Audit date: 2026-07-02

Verdict: Swooshz Quote Auto Generator (SQAG) is still local-UAT ready, but it is not ready for hosted,
protected, deploy, or production hosting yet. PR #84 does not make SQAG
production-ready; it documents the current blockers and adds a safe readiness
check.

Follow-up note: `docs/architecture-dead-code-fallback-audit.md` extends this
audit with the stricter product direction that Load Sample is not part of the
sellable product path. The first implementation PR after that audit should
remove Load Sample from product UI/routes/JS/docs and replace test reliance
with test-only seeded setup.

This audit did not inspect private runtime data, generated customer files,
local `.env` values, secrets, or private pricing/profile contents. It reviewed
source, migrations, existing runbooks, the storage implementation, and a Codex
Security standard scan focused on production-readiness boundaries.

## Executive Summary

Local UAT can continue under the existing repo rules: single operator, local
runtime data, ignored generated outputs, and no public deployment. Production
or broad internal rollout needs follow-up work first.

Readiness verdicts are intentionally separate:

- `local_uat_supported`: yes. Existing local UAT can continue.
- `internal_alpha_ready`: no. The readiness checker no longer recognizes a
  DB/BLOB artifact exception for hosted/protected/deploy readiness.
- `production_ready`: no. Generated XLSX/PDF bytes require object storage;
  database rows store metadata and quote-domain records only.

Primary blockers:

- Runtime data can still depend on local filesystem storage.
- Generated quote artifacts can still depend on local filesystem storage.
- Earlier live object-provider, database, and DB+object backup/restore evidence
  is now historical/pre-namespace evidence after the SQAG env/table/object
  metadata/app-key cleanup. Post-rename reruns are required before those live
  gates can count for the current SQAG namespace.
- Live retention/delete has only an opt-in verifier path and has not recorded
  passing post-rename operator evidence.
- Swooshz Platform app-key migration landed in Platform PR #79; hosted
  Platform-to-SQAG smoke still has not been rerun against deployed environments.

PR #88 update: database/platform pricing-reference list/detail/export/generation
paths now resolve only workspace-owned database rows. Local and bundled pricing
packs remain available only in local-UAT storage mode.

PR #89 update: database/platform quote generation now resolves the selected
profile defaults and quotation layout from workspace-owned DB profile rows and
stored DB layout artifacts. Missing/deleted workspace profiles or missing
layout artifacts block generation instead of falling back to bundled/default or
local profile packs.

PR #91 update: hosted/database/platform/deploy mode disables legacy direct
`/api/jobs/{job}/files/{filename}` output-root downloads, and job
status/result reads are owner/workspace-bound in protected modes. Local-UAT
local storage mode keeps the legacy direct download route for localhost
workflows.

## Safe Readiness Command

Run this check locally before any hosted rollout review:

```powershell
python scripts\check_production_readiness.py
```

The command prints JSON only. It intentionally does not print database URLs,
absolute private local paths, OAuth values, token values, cookie values,
callback URLs with query params, customer data, generated quote contents,
private pricing/profile contents, or staff emails. It exits nonzero until the
known production blockers are fixed.

Equivalent server entry point:

```powershell
python -m webapp.server --check-production-readiness
```

Object-mode reviews can include synthetic contract/lifecycle evidence and the
opt-in live-provider verifier. The live verifier fails closed unless
`SQAG_LIVE_OBJECT_STORAGE_EVIDENCE` and the required S3-compatible provider env
names are present in the operator environment. The S3-compatible SDK path is
repo-controlled through pinned dependencies in `requirements.txt`; real
provider values still belong only in the host secret manager or operator
environment:

```powershell
python -m pip install --only-binary=:all: -r requirements.txt
python scripts\check_production_readiness.py --with-object-storage-evidence --with-object-artifact-lifecycle-evidence --with-live-object-storage-provider-evidence
```

Sanitized operator evidence from 2026-07-07 shows the live verifier could pass
against a real S3-compatible provider when env names were supplied outside Git:
`status=passed`, `test_injected_backend=false`,
`live_provider_evidence_supported=true`, and store/retrieve/checksum/content
type/byte size/wrong-workspace/delete/tombstone/missing-object checks all true.
No provider values, bucket names, object keys, artifact bytes, DB URLs, private
paths, uploaded content, tenant data, or secrets were committed or printed.
After the SQAG namespace cleanup, this run is historical/pre-namespace evidence
and must not be treated as post-rename proof. Production readiness still remains
false. The separate live DB+object backup/restore gate also has only
historical/pre-namespace sanitized passing evidence; post-rename live database,
DB+object backup/restore, live retention/delete, and live Platform-to-SQAG smoke
reruns remain required.

Production database readiness is now a separate explicit gate. SQLite remains
local-UAT/synthetic evidence only, while Neon/Postgres-compatible metadata
storage is the intended production DB direction. `scripts/verify_production_database_provider.py`
is a metadata-only checker that recognizes Postgres-compatible URL schemes
without printing DB URL values, confirms the repo-declared runtime-required
metadata tables, and can run explicit opt-in live Postgres/Neon evidence through
the metadata adapter. It still fails closed by default and does not credit
production readiness without operator-run live DB evidence. On 2026-07-07,
the existing guarded SQAG metadata migrations were applied through
`scripts/migrate_sqag_storage.py` after an initial sanitized verifier pass
reported the runtime schema missing. The verifier rerun then passed with
`status=passed`, `database_family=postgres_compatible`,
`live_database_evidence_enabled=true`, `test_injected_backend=false`,
`live_database_evidence_supported=true`,
`production_database_evidence_supported=true`, `connection_attempted=true`,
runtime schema available, synthetic metadata CRUD verified, two-workspace
isolation verified, object artifact metadata pairing verified,
`cleanup_completed=true`, and `db_blob_artifact_rows_written=0`. With
`SQAG_LIVE_DATABASE_EVIDENCE=1`, an operator supplies DB values outside Git/chat
and the verifier uses synthetic namespaced metadata rows only to check schema,
profile/pricing/session/object metadata CRUD, two-workspace isolation, object
artifact metadata pairing, and cleanup. It does not require DB-BLOB artifact
tables, does not store generated XLSX/PDF bytes in the DB, does not touch
R2/object storage, and reports only sanitized booleans/counts/schema version.
No DB URL, hostname, username, password, connection string, provider value,
object key, private path, tenant data, generated quote contents, or artifact
bytes were committed or printed. Passing this DB evidence removes only the DB
evidence blocker for the pre-namespace runtime only. The later operator-run live
DB+object backup/restore drill also passed with sanitized pre-namespace
evidence. After the SQAG namespace cleanup, post-rename live production database
evidence and post-rename live DB+object backup/restore evidence must be rerun.
`production_ready=false` remains until live retention/delete, hosted
logging/monitoring, hosted smoke, production deployment operations, live
Platform-to-SQAG launch smoke, session/business hardening, and final production
audit are complete.

Current expected posture in local mode:

- `local_uat_supported`: `true`
- `internal_alpha_ready`: `false`
- `production_ready`: `false`
- Expected blockers include `local_runtime_storage`,
  `local_artifact_storage`, `object_storage_missing`, and
  `backup_restore_unverified`. Production blockers also include hosted
  Platform-to-SQAG smoke until a deployed smoke passes.

## Storage Surface Audit

| Surface | Current local mode path/source | Database/platform support | Workspace-scoped today | Restart-persistent today | Redeploy-persistent today | Hosted/protected/deploy suitability | Production suitability | Blocker or follow-up PR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Profiles | `QUOTE_DATA_ROOT/{company_id}/profiles.json` and `profile-packs/{profile_id}` | `sqag_profiles` rows plus profile file artifacts exist; PR #89 uses DB rows/artifacts for DB-mode generation | Yes in DB row/artifact mode; no in local mode | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | No until the remaining non-profile blockers are resolved | No | Keep profile defaults/layouts workspace-owned; move uploaded profile assets to object storage for production. |
| Pricing references | `SQAG_LOCAL_PRICING_REFERENCES_ROOT` or `_pricing-references/{reference_id}` plus bundled references | `sqag_pricing_references` rows with runtime catalog JSON | Yes in DB mode; local/bundled packs are local-UAT only | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | No until the remaining non-pricing blockers are resolved | No | Keep pricing references imported or seeded as workspace-owned database rows; move uploaded/reference assets to object storage for production. |
| Quote sessions | `QUOTE_DATA_ROOT/quote-sessions/{session_id}` | `sqag_quote_sessions` rows keyed by `workspace_id` | Yes in DB mode; no in local mode | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | Backup/restore live evidence has passed for synthetic namespaced DB rows plus one generated artifact object; retention, hosted smoke, and operations gates remain | No | Complete live retention/delete, hosted smoke, operations, and final audit evidence. |
| Generated artifacts | `QUOTE_OUTPUT_ROOT/{job_id}` and quote-session `exports` folders | `sqag_quote_artifacts` and `sqag_file_artifacts` DB BLOBs exist for local-UAT/synthetic coverage; object mode stores generated bytes in a configured object backend with `sqag_object_artifacts` metadata | Workspace-scoped only when storage is database-backed and artifact metadata is database/object-backed | Local mode requires mounted output root; DB artifact mode can survive restart but is not launch posture; object mode requires post-rename provider evidence | DB artifact mode survives, but cannot satisfy hosted/protected/deploy readiness; object mode has only historical/pre-namespace live provider and live DB+object backup/restore evidence and remains blocked without post-rename live reruns, live retention/delete, hosted smoke, and operations evidence | Blocked | No | Complete post-rename live evidence reruns, live retention/delete, uploaded/reference/profile object wiring as needed, hosted smoke, operations, and final audit evidence. |
| Temp uploads/intermediates | `QUOTE_TMP_ROOT/{job_id}` | No durable production storage expected | No | No | No | Local/single-job only | No | Keep temp data ephemeral, but ensure generated durable artifacts are copied to owned artifact storage before download. |
| Runtime logs | `_logs/app/` or configured log root | External log backend not implemented | N/A | Only if configured | Depends on host | Local UAT only | No | Add privacy-minimized hosted logging/monitoring before production. |

## Profiles Audit

Local mode reads and writes mutable profile config through `CompanyConfigStore`
and local profile packs. Database mode stores profile rows and can store profile
file artifacts. PR #89 makes DB/platform quote generation resolve the selected
profile row, apply its defaults, and extract the stored DB layout artifact for
the generator instead of calling local/bundled profile-pack loaders as fallback
source of truth.

Evidence:

- `DatabaseSqagStorage.list_profiles()` now returns only current-workspace DB
  profile rows.
- DB/platform generation now blocks when the selected profile row or stored
  quotation-layout artifact is missing.
- Local-UAT storage mode still uses local profile packs.

Production requirement: profile defaults, layout rules, and layout workbook
assets resolve from the authenticated workspace's database records in DB mode
after PR #89. Final production still requires object storage, backup/restore,
retention, and hosted monitoring evidence.

## Pricing References Audit

Database pricing references are stored in `sqag_pricing_references`. PR #88
removes the database-mode fallback that previously merged workspace rows with
local and bundled pricing references. Detail/export/generation now fail safely
when the selected pricing reference is missing, deleted, or not owned by the
current workspace.

Evidence:

- `DatabaseSqagStorage.list_pricing_references()` returns only public summaries
  for `sqag_pricing_references` rows in the current workspace.
- `DatabaseSqagStorage.pricing_reference_detail()` returns `None` for local or
  bundled sources in database mode.
- Generation validation and runtime catalog creation use the authenticated
  workspace storage in database mode.

Production requirement: in database/platform mode, mutable pricing references
must remain workspace-scoped. Public bundled references can be added later only
through an explicit workspace-owned import or allowlist design; they are not a
silent fallback.

## Sessions And Artifact Download Audit

Quote-session downloads are the intended owned artifact path because they use
current storage and database visibility checks. PR #91 disables the legacy
direct job-file download route in hosted/database/platform/deploy mode because
that route treats the job id as the object selector instead of resolving
workspace/session artifact ownership.

Evidence:

- `webapp/server.py` routes `/api/jobs/.../files/...` to `send_download()`,
  which now returns a generic not-found response in deploy, database storage,
  platform launch/session, or database artifact mode.
- `webapp/server.py` records privacy-safe job owner/workspace context for
  asynchronous jobs and filters `/api/jobs/{job}` status/result reads in
  protected modes.
- `webapp/server.py` shows the safer quote-session download path using
  storage-backed artifact lookup.

Production requirement: keep the legacy job-file route disabled in hosted
modes. Prefer quote-session artifact URLs backed by database metadata and,
before production, object storage.

## Production Storage Recommendation

Recommended final architecture:

- PostgreSQL or equivalent managed relational storage for workspace-scoped
  profiles, pricing-reference metadata/catalogs, quote sessions, artifact
  metadata, ownership, audit metadata, and retention state.
- S3/R2-compatible object storage for generated XLSX/PDF files, uploaded
  booth/render images, imported reference assets, and profile layout workbooks.
- Database rows should store object keys, content type, byte size, checksums,
  workspace id, owner id, artifact type, lifecycle/retention status, and
  created/updated timestamps.
- Download URLs must be authorized per workspace/session before bytes are
  streamed or signed.
- Backups must cover both DB and object storage. Restore drills must prove that
  profile assets, pricing assets, sessions, and generated XLSX outputs come
  back together.

SQLite/database BLOB artifact mode is acceptable only for local UAT and
synthetic verifier coverage. It is not a hosted/protected/deploy launch posture
and must not be treated as production-ready. Production generated XLSX/PDF bytes
require object storage; database rows should store metadata, ownership,
checksums, retention state, and audit data only.

Neon/Postgres-compatible metadata storage is the production database direction.
The runtime adapter boundary now exists for workspace-scoped profiles, pricing
references, quote sessions, and object-artifact metadata. A sanitized
operator-run verifier on 2026-07-07 proved the app can use a
Postgres-compatible database with the required workspace-scoped tables,
object-artifact metadata, isolation behavior, synthetic metadata CRUD, object
metadata pairing, cleanup, and sanitized output. The database evidence credits
only `production_database_evidence=passed`; DB+object backup/restore evidence is
a separate live gate and is documented below.

`scripts/verify_live_object_storage_provider.py` is the opt-in live-provider
evidence path. It uses the pinned S3-compatible SDK dependency set from
`requirements.txt`, synthetic XLSX/PDF bytes, and reports only provider family,
status booleans, required/missing env names, and check results. It must not
print provider values, object keys, artifact bytes, DB URLs, private paths,
customer data, uploaded content, or secrets. Without a successful operator-run
live provider verifier and the remaining production gates, `production_ready`
remains false.

`scripts/verify_live_db_object_backup_restore.py` is the opt-in live DB+object
backup/restore drill path. It stays blocked unless all required env names are
present, `SQAG_LIVE_DB_OBJECT_BACKUP_RESTORE_EVIDENCE` is enabled, active and
restore DB/object targets are distinct, and backup ownership plus restore-window
decision markers are present. Missing or non-isolated restore targets report
`blocked_isolated_restore_target_missing`; missing decision markers report
`blocked_backup_restore_decision_missing`.

When the operator-run drill is enabled, it uses only synthetic namespaced
profile, pricing, quote-session, and object-artifact metadata rows plus one tiny
synthetic generated artifact payload. It applies the existing guarded SQAG
metadata migrations where needed, writes active DB/object data, and then proves
isolation with live synthetic visibility checks before restore writes. The
restore DB must not read the active synthetic profile, pricing, quote-session,
or object-artifact metadata rows, and the restore object backend must not read
the active synthetic object. If either restore target can see active synthetic
data, the drill fails closed before restore writes. Only after those checks pass
does it restore equivalent synthetic rows and object bytes into isolated restore
targets, verify checksum/content type/byte size, verify DB+object metadata
pairing, preserve workspace isolation, and clean up synthetic rows and objects
from both active and restore targets. Any missing env, non-isolated target,
DB/object write or read failure, restore mismatch, metadata/object pairing
mismatch, or cleanup failure fails closed. Reports are sanitized booleans,
counts, and blocker IDs only, with no DB URLs, provider values, bucket names,
object keys, artifact bytes, tenant data, generated quote contents, private
paths, backup dumps, or restore dumps. A passing non-test-injected run can
remove only the live DB+object backup/restore blocker; `production_ready=false`
remains until the unrelated production blockers are complete.

Sanitized operator-run live DB+object backup/restore evidence passed on
2026-07-07 after the verifier/runtime metadata pairing fix. The drill reported
`status=passed`, `test_injected_backend=false`,
`live_db_object_backup_restore_evidence_supported=true`, active DB write-read
verified, active object write-read verified, restore DB write-read verified,
restore object write-read verified, restore DB could not read active synthetic
rows before restore, restore object target could not read the active synthetic
object before restore, `checksum_match=true`, DB+object metadata pairing
verified, content type and byte size matched, workspace isolation preserved,
`cleanup_completed=true`,
`active_db_synthetic_rows_written=7`,
`active_object_synthetic_objects_written=1`,
`restore_db_synthetic_rows_written=7`,
`restore_object_synthetic_objects_written=1`, and
`db_blob_artifact_rows_written=0`. The run used synthetic namespaced DB rows
and one tiny synthetic generated artifact object only. Restore targets were
isolated from active targets, no destructive restore over active live targets
occurred, and cleanup completed.

The live drill output and this audit include sanitized booleans, counts,
schema/status fields, and blocker categories only. No secrets, private values,
provider values, DB URLs, hostnames, usernames, passwords, connection strings,
endpoints, bucket names, object keys, access keys, secret keys, OAuth values,
cookies/tokens, private paths, tenant/customer/staff/profile/pricing data,
generated quote contents, artifact bytes, backup dumps, or restore dumps were
printed or committed. This evidence may remove only
`db_object_backup_restore_live_evidence_missing`; no unrelated blocker is
removed. Remaining production blockers still include live retention/delete
evidence, hosted logging/monitoring and alert delivery, hosted smoke evidence,
production deployment operations evidence, live Platform-to-SQAG launch smoke,
session/business hardening, and final production audit. `production_ready=false`
remains.

`scripts/verify_live_retention_delete.py` is the opt-in live retention/delete
drill path. It stays blocked unless `SQAG_LIVE_RETENTION_DELETE_EVIDENCE=1`,
`SQAG_DATABASE_URL`, `SQAG_STORAGE_MODE=database`,
`SQAG_ARTIFACT_STORAGE_MODE=object`, and the canonical
`SQAG_OBJECT_STORAGE_*` env names are present in the operator environment. It
validates those runtime modes before writing synthetic rows or objects. When
enabled, it uses synthetic namespaced DB metadata rows and one tiny synthetic
generated artifact object only. The drill verifies active DB metadata, active
object write/read, checksum/content type/byte size,
DB+object metadata pairing, and active runtime export download through
`quote_session_export_artifact()` before tombstone/delete. It then verifies
runtime tombstone/delete behavior, denied deleted downloads, missing object
fail-closed behavior, wrong-workspace denial, repeated delete safety, and
cleanup. It fails closed on missing env, wrong runtime mode, DB/schema failure,
object write/read failure, active runtime download failure,
metadata/object mismatch, tombstone/delete mismatch, unsafe wrong-workspace
behavior, missing-object handling failure, repeated delete safety failure, or
cleanup failure.

The live retention/delete verifier reports sanitized booleans, counts, status
fields, and blocker IDs only. It must not print DB URLs, hostnames, usernames,
passwords, connection strings, endpoints, bucket names, provider values, object
keys, access keys, secret keys, OAuth values, cookies/tokens, private paths,
tenant/customer/staff/profile/pricing data, generated quote contents, artifact
bytes, backup dumps, restore dumps, or secrets. Passing it may remove only
`object_retention_delete_live_evidence_missing`; no unrelated blocker is
removed. Until a non-test-injected operator run passes, live retention/delete
evidence remains a production blocker and `production_ready=false` remains.


### PR #140 blocked-head remediation addendum (2026-07-15)

The review-blocked head `ef9767429211f8d84731d7d660b63c6b73d07de8` was
retested with synthetic fixtures before repair. Deterministic RED evidence
confirmed workspace-ID collapse, parent-hold child deletion, duplicate resume
runs, hosted hash loss, non-reconstructive request evidence, terminal-state
collapse, submission-only feedback expiry, mutable evidence/audit rows,
unbounded retention, and repeated-delete false failure.

The repaired design keeps `production_ready=false` and adds exact trusted
workspace identities with deploy fail-closed behavior, dedicated versioned
HMAC pseudonyms, one job/idempotency identity per run, bounded abandoned-run
reconciliation, normalized legal-hold records, graph-aware retention,
closure-based feedback retention, immutable evidence content, controlled
retention deletes, privileged audited support access, and authoritative durable
artifact hashes. Privileged integrity retrieval re-reads the linked durable
XLSX/PDF bytes and compares their authoritative backend checksum and size with
the immutable manifest. The canonical generation manifest is written only after
final artifact persistence and contains normalized brief/Basis/Output values in
order, immutable profile/pricing snapshots and checksums, template checksum,
input checksums, material generation configuration, and final artifact hashes.
The bounded retention worker preserves sessions shared by retained runs,
deletes database/object session artifacts before forensic rows, resumes safely
after partial success, and creates deletion receipts only after the remaining
record graph commits successfully.

Migration 004 is amended in place because it remains unmerged and has never
entered canonical main history. SQLite and Postgres now use intentionally
separate migration files; Postgres also has a follow-on controlled-delete guard
file. This avoids unsupported mixed-dialect DDL while keeping equivalent table,
index, immutability, and retention authorization semantics.
The canonical object-storage provider and live-evidence env names use the
`SQAG_` prefix. Legacy `SQAG_*` object-storage provider names are not aliases
and do not silently satisfy live-provider evidence or readiness checks. Existing
database table names and non-object storage compatibility env names remain
unchanged by this cleanup PR.

## Security Audit

Codex Security standard scan status: complete.

Scan ID: `79f90132-20c3-4d47-a634-704ac903172a`

Reportable findings:

| Severity | Finding | Production impact | Required follow-up |
| --- | --- | --- | --- |
| High, resolved in PR #88 | Database pricing references could include local packs across workspaces | Hosted database mode could cross workspace boundaries if private local pricing packs existed on the host. | Keep regression tests proving DB mode lists/details/exports/generates only workspace-owned references. |
| Medium, resolved in PR #91 | Legacy job artifact downloads were not bound to workspace/session ownership | Hosted/database/platform/deploy mode now disables the legacy direct download route and owner-binds job status/result reads. | Keep regression tests proving leaked job IDs cannot download legacy bytes in protected modes; use quote-session artifact downloads. |

Positive controls reviewed:

- Deploy auth guard and platform/OIDC session handling exist.
- Unsafe requests use CSRF/same-origin checks.
- Host-header guard and security headers exist.
- Upload size limits and safe filename/path containment checks exist.
- Spreadsheet formula-injection hardening exists.
- Diagnostics include redaction helpers and generic user-facing errors.

Scan limitation: this was a focused production-readiness security scan, not an
exhaustive line-by-line security review of every source-like file.

## Final Readiness Checklist

PR #84 does not make SQAG production-ready. SQAG should not move beyond local
UAT until these are true:

- `python scripts\check_production_readiness.py` reports no P1 blockers.
- Profile generation resolves workspace-scoped DB/object-storage profile assets.
- Pricing-reference list/detail/generation paths do not expose local private
  packs in database/platform mode. PR #88 adds this guard; keep it in the
  release gate.
- Generated XLSX/PDF and uploaded assets are stored in object storage with
  database metadata; DB/BLOB artifact mode is not an equivalent launch posture.
- Artifact downloads are bound to workspace/session ownership.
- The medium security finding for legacy direct job artifact downloads remains
  fixed: PR #91 disables the legacy route in deploy/database/platform modes.
- Backup and restore are documented, automated where possible, and drill-tested.
- Rollback procedure is documented for app version, DB migration, and object
  storage compatibility.
- Hosted smoke covers login/launch, profile save/load, pricing reference
  import/use, quote generation, session persistence, and artifact download
  after restart.
- Monitoring/logging is privacy-minimized and support-traceable.
- CI status is green on the PR that introduces any storage/security change.

## Recommended Follow-Up PR Sequence

1. Load Sample product-path removal:
   remove Load Sample completely from product UI/routes/JS/docs and replace
   Playwright/unit-test reliance with test-only seeded setup.
2. Pricing-reference isolation:
   completed in PR #88 for database/platform list/detail/export/generation
   fallback removal and same-id delete regression coverage.
3. Profile artifact/layout resolution:
   completed in PR #89 for DB/platform mode; generation now uses
   workspace-scoped profile defaults/layout artifacts and fails clearly when
   the selected workspace profile or layout is missing.
4. Artifact authorization / legacy job download lockdown:
   completed in PR #91 by disabling legacy job-file downloads in protected
   modes and adding cross-user artifact/status tests.
5. Storage productionization / object storage:
   introduce object-storage metadata, object-key authorization, checksum
   recording, and retention hooks.
6. Backup/restore/rollback/monitoring operations readiness:
   add backup/restore/rollback runbooks, hosted smoke checklist, and monitoring
   requirements.

Do not combine these with public deployment, billing, Stripe, production auth
redesign, customer data migration, or any claim that PR #84 alone makes SQAG
production-ready.