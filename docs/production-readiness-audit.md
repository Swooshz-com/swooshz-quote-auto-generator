# KQAG Production-Readiness Audit

Audit date: 2026-07-02

Verdict: KQAG/SAQG is still local-UAT ready, but it is not ready for internal
alpha or production hosting yet. PR #84 does not make KQAG production-ready;
it documents the current blockers and adds a safe readiness check.

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
- `internal_alpha_ready`: no today. A temporary small-team exception could
  become acceptable after database storage, database artifacts, and documented
  backup/restore/rollback are in place, but that would still be an explicit
  internal-alpha exception rather than final production clearance.
- `production_ready`: no. SQLite/database BLOB mode is not final
  production-ready storage.

Primary blockers:

- Runtime data can still depend on local filesystem storage.
- Generated quote artifacts can still depend on local filesystem storage.
- Database mode exists, but generated profile layout/default resolution still
  depends on local profile-pack loading.
- Database pricing-reference mode can still expose local saved pricing packs
  alongside workspace rows.
- Legacy job artifact downloads are not bound to workspace/session ownership.
- No object-storage layer exists for XLSX/PDF outputs and uploaded assets.
- Backup, restore, retention, and rollback have not been implemented or proven.

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

Current expected posture in local mode:

- `local_uat_supported`: `true`
- `internal_alpha_ready`: `false`
- `production_ready`: `false`
- Expected blockers include `local_runtime_storage`,
  `local_artifact_storage`, `pricing_reference_local_pack_isolation`,
  `legacy_job_artifact_download_authorization`, `object_storage_missing`, and
  `backup_restore_unverified`.

## Storage Surface Audit

| Surface | Current local mode path/source | Database/platform support | Workspace-scoped today | Restart-persistent today | Redeploy-persistent today | Internal alpha suitability | Production suitability | Blocker or follow-up PR |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Profiles | `QUOTE_DATA_ROOT/{company_id}/profiles.json` and `profile-packs/{profile_id}` | `kqag_profiles` rows plus profile file artifacts exist | Yes in DB row storage; no in local mode | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | Blocked | No | Resolve generation-time profile layout/defaults from workspace DB artifacts instead of local profile-pack files. |
| Pricing references | `KQAG_LOCAL_PRICING_REFERENCES_ROOT` or `_pricing-references/{reference_id}` plus bundled references | `kqag_pricing_references` rows with runtime catalog JSON | Partially; DB rows are scoped, but DB list/detail still falls back to local packs | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | Blocked | No | Remove local private pricing-reference fallback from database/platform mode and add isolation tests. |
| Quote sessions | `QUOTE_DATA_ROOT/quote-sessions/{session_id}` | `kqag_quote_sessions` rows keyed by `workspace_id` | Yes in DB mode; no in local mode | Only if mounted local roots or DB mode are configured | DB rows survive; local mode requires mounted volume | Blocked until backup/retention is proven | No | Add backup/restore, retention, owner isolation tests, and hosted smoke evidence. |
| Generated artifacts | `QUOTE_OUTPUT_ROOT/{job_id}` and quote-session `exports` folders | `kqag_quote_artifacts` and `kqag_file_artifacts` DB BLOBs exist | Only when both storage and artifact mode are database-backed | Local mode requires mounted output root; DB artifact mode survives restart | DB artifact mode survives, but is not final production storage | Blocked | No | Move generated XLSX/PDF and uploaded assets to object storage with DB metadata. |
| Temp uploads/intermediates | `QUOTE_TMP_ROOT/{job_id}` | No durable production storage expected | No | No | No | Local/single-job only | No | Keep temp data ephemeral, but ensure generated durable artifacts are copied to owned artifact storage before download. |
| Runtime logs | `_logs/app/` or configured log root | External log backend not implemented | N/A | Only if configured | Depends on host | Local UAT only | No | Add privacy-minimized hosted logging/monitoring before production. |

## Profiles Audit

Local mode reads and writes mutable profile config through `CompanyConfigStore`
and local profile packs. Database mode stores profile rows and can store profile
file artifacts, but quote generation still calls `load_profile_pack()` from the
payload path. That path resolves local company/bundled profile packs and uses
`profile.quotation_layout_path`.

Evidence:

- `webapp/server.py:7562` selects `profile_id` from the request payload.
- `webapp/server.py:7937` resolves profile packs through local pack loading.
- `webapp/server.py:13348` loads the generation profile and
  `webapp/server.py:13350` uses the local layout template path.

Production requirement: profile defaults, layout rules, and layout workbook
assets must resolve from the authenticated workspace's database/object-storage
records before internal alpha.

## Pricing References Audit

Database pricing references are stored in `kqag_pricing_references`, but the
database storage implementation currently merges workspace rows with local and
bundled pricing references. Detail lookup can also fall through to the shared
local/bundled pack resolver.

Evidence:

- `webapp/server.py:7166` merges company database references with
  `list_local_pricing_references()` and `list_bundled_pricing_references()`.
- `webapp/server.py:7191` starts database detail lookup, and
  `webapp/server.py:7206` falls through to `pricing_reference_pack_detail()`.

Production requirement: in database/platform mode, mutable pricing references
must be workspace-scoped. Public bundled references can remain only through an
explicit allowlist. Local private reference packs must not be listed, fetched,
or used by other workspaces.

## Sessions And Artifact Download Audit

Quote-session downloads are closer to the desired production path because they
use current storage and database visibility checks. The legacy direct job-file
download route is not production-safe because it treats the job id as the object
selector and does not bind the artifact to the current workspace or session.

Evidence:

- `webapp/server.py:13667` routes `/api/jobs/.../files/...` directly to
  `send_download()`.
- `webapp/server.py:14285` reads from the configured output root by URL job id
  and filename after path/filename checks, but without workspace/session owner
  lookup.
- `webapp/server.py:14317` shows the safer quote-session download path using
  storage-backed artifact lookup.

Production requirement: disable the legacy job-file route in deploy/database
mode or make it workspace-aware. Prefer quote-session artifact URLs backed by
database metadata and object storage.

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

SQLite/database BLOB artifact mode is acceptable only for local UAT or, as a
future temporary small-team exception, an explicitly backed-up internal-alpha
setup with database-backed profiles, pricing references, quote sessions, and
artifacts plus documented restore and rollback procedures. It is not the
recommended final production storage model and must not be treated as
production-ready.

## Security Audit

Codex Security standard scan status: complete.

Scan ID: `79f90132-20c3-4d47-a634-704ac903172a`

Reportable findings:

| Severity | Finding | Production impact | Required follow-up |
| --- | --- | --- | --- |
| High | Database pricing references can include local packs across workspaces | Hosted database mode can cross workspace boundaries if private local pricing packs exist on the host. | Remove local private fallback from DB/platform mode and add isolation tests. |
| Medium | Legacy job artifact downloads are not bound to workspace/session ownership | Any leaked valid job URL can bypass object-level ownership checks in a hosted multi-user deployment. | Route downloads through workspace/session-aware artifact storage or disable the legacy route in deploy mode. |

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
  packs in database/platform mode.
- The high security finding for database pricing-reference local pack leakage
  is fixed or explicitly risk-accepted only for a narrow internal-alpha
  exception.
- Generated XLSX/PDF and uploaded assets are stored in object storage or a
  documented, backed-up internal-alpha equivalent.
- Artifact downloads are bound to workspace/session ownership.
- The medium security finding for legacy direct job artifact downloads is fixed
  or the legacy route is disabled in deploy/database mode.
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
   remove local private reference fallback in database/platform mode and add
   cross-workspace tests.
3. Profile artifact/layout resolution:
   generate quotes from workspace-scoped profile layout/default assets instead
   of local profile-pack paths.
4. Artifact authorization / legacy job download lockdown:
   disable or replace legacy job-file downloads in deploy/database mode and add
   cross-user artifact tests.
5. Storage productionization / object storage:
   introduce object-storage metadata, object-key authorization, checksum
   recording, and retention hooks.
6. Backup/restore/rollback/monitoring operations readiness:
   add backup/restore/rollback runbooks, hosted smoke checklist, and monitoring
   requirements.

Do not combine these with public deployment, billing, Stripe, production auth
redesign, customer data migration, or any claim that PR #84 alone makes KQAG
production-ready.
