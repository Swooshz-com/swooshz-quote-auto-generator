# KQAG Internal-Alpha VPS/Coolify Scaffold

## Purpose

This runbook is the KQAG app-specific scaffold for an already-prepared
VPS/Coolify-style host. It is metadata-only guidance for internal-alpha
validation. It does not deploy anything, configure infrastructure, add secrets,
prove live hosting, or claim production readiness.

KQAG is an internal Koncept Images Pte Ltd quote generator module, not
ecommerce or public SaaS. Koncept profile, pricing, and layout packs are
workspace-imported tenant data only. A new workspace starts with no real
Koncept pack until an authorized import or seed action targets that workspace.

## Target Posture

Use this posture only for the temporary internal-alpha/simple-hosting path:

- `APP_MODE=deploy`.
- `KQAG_STORAGE_MODE=database`.
- `KQAG_ARTIFACT_STORAGE_MODE=database`.
- `KQAG_DATABASE_URL` is configured only through the host secret manager.
- Platform/workspace launch context is required for protected hosted use.
- Object mode remains blocked for production until live provider evidence,
  DB+object backup/restore, retention/delete evidence, and operations evidence
  are complete.

The database artifact mode is a temporary internal-alpha exception. It is not
final production object storage.

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

Required names for this internal-alpha posture:

| Area | Names |
| --- | --- |
| App/auth | `APP_MODE`, `AUTH_REQUIRED`, `SESSION_SECRET` |
| Storage | `KQAG_STORAGE_MODE`, `KQAG_ARTIFACT_STORAGE_MODE`, `KQAG_DATABASE_URL` |
| Platform launch | `KQAG_PLATFORM_LAUNCH_MODE`, `KQAG_PLATFORM_BASE_URL` |
| Optional OIDC fallback/checklist | `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, `OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL`, `OIDC_USERINFO_URL`, `OIDC_LOGOUT_URL` |
| Tester policy | `AUTH_ALLOWED_EMAILS`, `AUTH_ALLOWED_DOMAINS`, `AUTH_ALLOW_ANY_AUTHENTICATED_USER`, `AUTH_APPROVED_TESTER_ROLE` |
| Runtime housekeeping | `QUOTE_DATA_ROOT`, `QUOTE_OUTPUT_ROOT`, `QUOTE_TMP_ROOT`, `QUOTE_LOG_ROOT`, `PORT` |

`QUOTE_DATA_ROOT` and `QUOTE_OUTPUT_ROOT` are not durable product-visible
storage in this posture. Quote sessions and generated artifacts must persist
through workspace-owned database rows and database artifact records.

## Validation Commands

Run the placeholder template check locally before copying names into the host:

```powershell
python scripts\verify_internal_uat_deploy_template.py
```

Run the metadata-only hosted validation bundle with synthetic data:

```powershell
python scripts\verify_internal_alpha_hosted_validation.py --work-dir _tmp\validation\internal-alpha-hosted
```

The bundle composes the existing synthetic evidence:

```powershell
python scripts\verify_database_backup_restore.py --work-dir _tmp\validation\backup-restore
python scripts\verify_hosted_observability.py --work-dir _tmp\validation\hosted-observability
python scripts\verify_hosted_smoke.py --work-dir _tmp\validation\hosted-smoke
```

Run the readiness checker for the same DB + DB-artifact posture:

```powershell
python scripts\check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence
```

The readiness checker is expected to keep `production_ready=false`. It may
report conditional internal-alpha readiness only when database storage,
database artifact storage, and the evidence flags all pass.

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
- Quote generation persists through database quote sessions.
- XLSX/PDF artifacts download only through authorized quote-session routes.
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
