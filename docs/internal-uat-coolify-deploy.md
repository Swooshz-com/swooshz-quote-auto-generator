# KQAG Hosted Validation Notes

## Purpose

This note preserves the KQAG app-specific hosted validation checklist shape for
an already-prepared host. It is metadata-only local guidance only. It does not
deploy anything, configure infrastructure, add secrets, prove live hosting, or
claim hosted/protected/deploy/production readiness.

KQAG is an internal Koncept Images Pte Ltd quote generator module, not
ecommerce or public SaaS. Koncept profile, pricing, and layout packs are
workspace-imported tenant data only. A new workspace starts with no real
Koncept pack until an authorized import or seed action targets that workspace.

## Blocked DB/BLOB Posture

The former DB/BLOB artifact posture is no longer a launch target:

- `APP_MODE=deploy`.
- `KQAG_STORAGE_MODE=database`.
- `KQAG_ARTIFACT_STORAGE_MODE=database`.
- `KQAG_DATABASE_URL` is configured only through the host secret manager.
- Platform/workspace launch context is required for protected hosted use.
- Readiness remains blocked because generated XLSX/PDF bytes require object
  storage. Database rows store metadata and workspace-owned app records only.

Database/BLOB artifact mode is local-UAT/synthetic evidence only. It must not
satisfy hosted, protected, deploy, or production readiness.

## Host Boundary

This repo owns only the app-specific shape:

- Start command: `python webapp/server.py`.
- Health path: `/api/health`.
- Deploy-mode environment variable names.
- Metadata-only validation commands.
- KQAG private-data and tenant-import guardrails.

Infrastructure runbooks outside this repo own VPS purchase, Coolify
installation, SSH access, DNS, TLS, firewall, reverse proxy, backups on the
host, and server maintenance.

## Environment Names

Use `deploy/internal-uat/coolify/kqag.uat.env.example` as the placeholder
checklist. Copy names into the host secret/environment manager and replace
placeholders there. Do not commit populated values.

Required names for any future hosted validation environment:

| Area | Names |
| --- | --- |
| App/auth | `APP_MODE`, `AUTH_REQUIRED`, `SESSION_SECRET` |
| Storage | `KQAG_STORAGE_MODE`, `KQAG_ARTIFACT_STORAGE_MODE`, `KQAG_DATABASE_URL` |
| Platform launch | `KQAG_PLATFORM_LAUNCH_MODE`, `KQAG_PLATFORM_BASE_URL` |
| Optional OIDC fallback/checklist | `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, `OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL`, `OIDC_USERINFO_URL`, `OIDC_LOGOUT_URL` |
| Tester policy | `AUTH_ALLOWED_EMAILS`, `AUTH_ALLOWED_DOMAINS`, `AUTH_ALLOW_ANY_AUTHENTICATED_USER`, `AUTH_APPROVED_TESTER_ROLE` |
| Runtime housekeeping | `QUOTE_DATA_ROOT`, `QUOTE_OUTPUT_ROOT`, `QUOTE_TMP_ROOT`, `QUOTE_LOG_ROOT`, `PORT` |

`QUOTE_DATA_ROOT` and `QUOTE_OUTPUT_ROOT` are not durable product-visible
storage in this posture. Quote-domain records must use workspace-owned database
rows. Production generated artifact bytes must use object storage, not database
BLOB rows or local runtime roots.

## Validation Commands

Run the placeholder template check locally before copying names into the host:

```powershell
python scripts\verify_internal_uat_deploy_template.py
```

Run the metadata-only blocked hosted validation bundle with synthetic data:

```powershell
python scripts\verify_internal_alpha_hosted_validation.py --work-dir _tmp\validation\hosted-blocked
```

The bundle composes the existing synthetic evidence:

```powershell
python scripts\verify_database_backup_restore.py --work-dir _tmp\validation\backup-restore
python scripts\verify_hosted_observability.py --work-dir _tmp\validation\hosted-observability
python scripts\verify_hosted_smoke.py --work-dir _tmp\validation\hosted-smoke
```

Run the readiness checker for the same DB + DB-artifact negative posture:

```powershell
python scripts\check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence
```

The readiness checker is expected to keep both `internal_alpha_ready=false` and
`production_ready=false`. It should report
`database_blob_artifact_storage_not_launch_ready` when DB/BLOB artifact mode is
selected.

## Hosted Smoke Checklist

After a real host exists, record only metadata and never paste secrets, DB URLs,
cookies, platform tokens, generated quote contents, customer data, artifact
bytes, host IPs, or private paths into issue/PR output.

- App build completes.
- App starts with the documented start command.
- `/api/health` returns metadata-only JSON.
- Unauthenticated protected routes block or redirect.
- Platform/workspace launch reaches the app.
- Intended workspace starts without a Koncept pack until import.
- Workspace-owned profile pack import/save/use works.
- Workspace-owned pricing reference import/save/use works.
- Quote generation persists metadata through database quote sessions.
- XLSX/PDF artifacts require object storage before hosted/protected/deploy or
  production readiness can be claimed.
- Delete makes the session and artifacts inaccessible.
- Logout/sign-out behaves safely.
- Legacy direct job file downloads remain disabled in deploy/database/platform
  paths.
- Logs remain metadata-only and omit secrets, provider responses, private data,
  generated quote contents, and artifact bytes.

## Not Proven

This scaffold does not prove:

- Live VPS/Coolify host health.
- DNS, TLS, firewall, reverse proxy, or server operations.
- Real OIDC provider behavior.
- Live Swooshz Platform integration.
- Real object-storage provider wiring.
- DB+object backup/restore or live object retention/delete evidence.
- Production observability export or alert delivery.
- Production readiness.
