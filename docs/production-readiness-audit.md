# KQAG Production-Readiness Audit

Audit date: 2026-07-02

Verdict: KQAG/SAQG is still local-UAT ready, but it is not ready for hosted,
protected, deploy, or production hosting yet. PR #84 does not make KQAG
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
- No operator-run live object-storage provider evidence has been recorded for
  generated XLSX/PDF bytes, and uploaded/reference/profile assets still need
  final object-storage wiring.
- Backup, restore, retention, and rollback have not been implemented or proven.

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
`KQAG_LIVE_OBJECT_STORAGE_EVIDENCE` and the required S3-compatible provider env
names are present in the operator environment:

```powershell
python scripts\check_production_readiness.py --with-object-storage-evidence --with-object-artifact-lifecycle-evidence --with-live-object-storage-provider-evidence
```

Current expected posture in local mode:

- `local_uat_supported`: `true`
- `internal_alpha_ready`: `false`
- `production_ready`: `false`
- Expected blockers include `local_runtime_storage`,
  `local_artifact_storage`, `object_storage_missing`, and
  `backup_restore_unverified`.

## Storage Surface Audit

| Surface | Current local mode path/source | Database/platform support | Workspace-scoped today | Restart-persistent today | Redeploy-persistent today | Hosted/protected/deploy suitability | Production suitability | Blocker or follow-up PR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Profiles | `QUOTE_DATA_ROOT/{company_id}/profiles.json` and `profile-packs/{profile_id}` | `kqag_profiles` rows plus profile file artifacts exist; PR #89 uses DB rows/artifacts for DB-mode generation | Yes in DB row/artifact mode; no in local mode | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | No until the remaining non-profile blockers are resolved | No | Keep profile defaults/layouts workspace-owned; move uploaded profile assets to object storage for production. |
| Pricing references | `KQAG_LOCAL_PRICING_REFERENCES_ROOT` or `_pricing-references/{reference_id}` plus bundled references | `kqag_pricing_references` rows with runtime catalog JSON | Yes in DB mode; local/bundled packs are local-UAT only | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | No until the remaining non-pricing blockers are resolved | No | Keep pricing references imported or seeded as workspace-owned database rows; move uploaded/reference assets to object storage for production. |
| Quote sessions | `QUOTE_DATA_ROOT/quote-sessions/{session_id}` | `kqag_quote_sessions` rows keyed by `workspace_id` | Yes in DB mode; no in local mode | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | Blocked until backup/retention is proven | No | Add backup/restore, retention, owner isolation tests, and hosted smoke evidence. |
| Generated artifacts | `QUOTE_OUTPUT_ROOT/{job_id}` and quote-session `exports` folders | `kqag_quote_artifacts` and `kqag_file_artifacts` DB BLOBs exist for local-UAT/synthetic coverage; object mode stores generated bytes in a configured object backend with `kqag_object_artifacts` metadata | Workspace-scoped only when storage is database-backed and artifact metadata is database/object-backed | Local mode requires mounted output root; DB artifact mode can survive restart but is not launch posture; object mode requires provider evidence | DB artifact mode survives, but cannot satisfy hosted/protected/deploy readiness; object mode remains blocked without live provider, retention/delete, and DB+object restore evidence | Blocked | No | Prove live object-storage provider evidence for generated XLSX/PDF bytes, then complete uploaded/reference/profile object wiring and DB+object backup/restore. |
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

- `DatabaseKqagStorage.list_profiles()` now returns only current-workspace DB
  profile rows.
- DB/platform generation now blocks when the selected profile row or stored
  quotation-layout artifact is missing.
- Local-UAT storage mode still uses local profile packs.

Production requirement: profile defaults, layout rules, and layout workbook
assets resolve from the authenticated workspace's database records in DB mode
after PR #89. Final production still requires object storage, backup/restore,
retention, and hosted monitoring evidence.

## Pricing References Audit

Database pricing references are stored in `kqag_pricing_references`. PR #88
removes the database-mode fallback that previously merged workspace rows with
local and bundled pricing references. Detail/export/generation now fail safely
when the selected pricing reference is missing, deleted, or not owned by the
current workspace.

Evidence:

- `DatabaseKqagStorage.list_pricing_references()` returns only public summaries
  for `kqag_pricing_references` rows in the current workspace.
- `DatabaseKqagStorage.pricing_reference_detail()` returns `None` for local or
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

`scripts/verify_live_object_storage_provider.py` is the opt-in live-provider
evidence path. It uses synthetic XLSX/PDF bytes and reports only provider family,
status booleans, required/missing env names, and check results. It must not print
provider values, object keys, artifact bytes, DB URLs, private paths, customer
data, uploaded content, or secrets. Without a successful operator-run live
provider verifier, `production_ready` remains false.

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

PR #84 does not make KQAG production-ready. KQAG should not move beyond local
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
redesign, customer data migration, or any claim that PR #84 alone makes KQAG
production-ready.
