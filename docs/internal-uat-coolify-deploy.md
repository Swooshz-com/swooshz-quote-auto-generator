# SQAG Hosted Validation Notes

## Temporary internal-alpha authentication

The committed env template now describes `SQAG_AUTH_MODE=internal_google`, not
an enabled Platform launch. Its subject-bound identity JSON, fixed workspace,
Google OIDC endpoints, client identifier, and client secret must be populated
only in a separately authorised host secret/configuration manager. The mode is
single-instance and private-alpha only; it cannot satisfy public production
readiness. See `docs/internal-google-auth-mode.md`.

## Purpose

This note preserves the SQAG app-specific hosted validation checklist shape for
an already-prepared host. It is metadata-only local guidance only. It does not
deploy anything, configure infrastructure, add secrets, prove live hosting, or
claim hosted/protected/deploy/production readiness.

SQAG is an internal Koncept Images Pte Ltd quote generator module, not
ecommerce or public SaaS. Koncept profile, pricing, and layout packs are
workspace-imported tenant data only. A new workspace starts with no real
Koncept pack until an authorized import or seed action targets that workspace.

## Hosted Object Artifact Posture

The hosted internal-alpha deploy scaffold uses database metadata plus object
storage for generated artifact bytes:

- `APP_MODE=deploy`.
- `SQAG_STORAGE_MODE=database`.
- `SQAG_ARTIFACT_STORAGE_MODE=object`.
- `SQAG_DATABASE_URL` is configured only through the host secret manager.
- The canonical `SQAG_OBJECT_STORAGE_*` names are configured only through the
  host secret manager.
- Platform/workspace launch context is required for protected hosted use.
- `SQAG_TRUSTED_PROXY_CIDRS` is configured in the host environment manager with
  only the exact Coolify/Traefik proxy network CIDRs that connect directly to
  SQAG. A trust-all network is not permitted.
- The object-storage credential must permit the read-only bucket probe used by
  startup and `/api/health`, plus runtime read, write, and delete operations.
  A provider delete failure returns HTTP 503 and keeps the profile, pricing
  reference, or quote session available for a later retry instead of reporting
  false deletion.
- Database rows store metadata and workspace-owned app records only.

Database/BLOB artifact mode is local-UAT/synthetic evidence only. It must not
satisfy hosted, protected, deploy, or production readiness.

## Nixpacks Production Build Contract

The `nixpacks.toml` file enforces an exact Python-only production build contract:

- `providers = ["python"]` — Node is never a production provider.
- Start command: `python webapp/server.py` (locked in `[start].cmd`).
- Python version: `3.12.13` bound in `.python-version` and pinned through
  `[phases.setup].nixpkgsArchive` to the immutable Nixpkgs commit
  `5c994fe2b1e540ff83aa59ba370918ad5aae4776` (python312: 3.12.12 -> 3.12.13).
- Dependencies: `requirements.txt`.
- The root `package.json` is preserved for local Playwright scripts, CI
  smokes and `npm audit`; it must not cause Nixpacks to select Node.

Validate the contract before deployment:

```powershell
python scripts\validate_nixpacks_python_contract.py
python -m unittest tests.test_nixpacks_python_contract
```

## Host Boundary

This repo owns only the app-specific shape:

- Build provider contract: `nixpacks.toml` and `.python-version`.
- Start command: `python webapp/server.py`.
- Health/readiness path: `/api/health`. It returns HTTP 200 only after the
  generator, required database schemas, and read-only object bucket probe pass;
  required dependency failure returns metadata-only HTTP 503.
- Deploy-mode environment variable names.
- Metadata-only validation commands.
- SQAG private-data and tenant-import guardrails.

Infrastructure runbooks outside this repo own VPS purchase, Coolify
installation, SSH access, DNS, TLS, firewall, reverse proxy, backups on the
host, and server maintenance. They also own discovering the actual
Coolify/Traefik proxy CIDRs. SQAG owns the application-side rule that only a
configured direct proxy peer may supply forwarding metadata.

## Environment Names

Use `deploy/internal-uat/coolify/sqag.uat.env.example` as the placeholder
checklist. Copy names into the host secret/environment manager and replace
placeholders there. Do not commit populated values.

Required names for any future hosted validation environment:

| Area | Names |
| --- | --- |
| App/auth | `APP_MODE`, `AUTH_REQUIRED`, `SQAG_AUTH_MODE`, `SESSION_SECRET` |
| Security/tracking | secret `SQAG_TRACKING_HMAC_KEY`; non-secret configuration `SQAG_TRACKING_HMAC_KEY_VERSION` |
| Storage | `SQAG_STORAGE_MODE`, `SQAG_ARTIFACT_STORAGE_MODE`, `SQAG_DATABASE_URL`, `SQAG_OBJECT_STORAGE_PROVIDER`, `SQAG_OBJECT_STORAGE_ENDPOINT_URL`, `SQAG_OBJECT_STORAGE_BUCKET`, `SQAG_OBJECT_STORAGE_REGION`, `SQAG_OBJECT_STORAGE_ACCESS_KEY_ID`, `SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY` |
| Temporary internal Google | `SQAG_PLATFORM_LAUNCH_MODE=disabled`, `SQAG_PUBLIC_BASE_URL`, `SQAG_INTERNAL_WORKSPACE_ID`, server-only `SQAG_INTERNAL_GOOGLE_IDENTITIES_JSON`, `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, `OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL` |

The former three email-list variables are rejected in this mode. Real subject
enrolment is a separate provider operation and is not performed by repository
validation.
| Reverse proxy | `SQAG_TRUSTED_PROXY_CIDRS` |
| Runtime housekeeping | `QUOTE_DATA_ROOT`, `QUOTE_OUTPUT_ROOT`, `QUOTE_TMP_ROOT`, `QUOTE_LOG_ROOT`, `PORT` |

`SQAG_TRUSTED_PROXY_CIDRS` is mandatory in deploy mode. Set it to the exact
CIDR or comma-separated CIDRs for direct Coolify/Traefik peers, not public
client ranges and never a catch-all network. SQAG ignores `X-Forwarded-For`
from an untrusted socket peer. For a trusted peer, it accepts only a bounded
chain of valid IP addresses, walks right-to-left past trusted proxy hops, and
uses the first untrusted address for both Platform-launch and normal mutable
route rate limits. Missing, malformed, oversized, or overlong forwarding data
falls back to the socket peer. Missing or malformed trusted-proxy
configuration blocks deploy preflight and startup.

`SQAG_TRACKING_HMAC_KEY` is a dedicated secret and must be supplied only by
the host secret manager. `SQAG_TRACKING_HMAC_KEY_VERSION` is non-secret
configuration and must contain 1-24 ASCII letters, digits, dots, underscores,
or hyphens, matching deploy runtime preflight. Neither value may be printed.

Rate-limit state is also bounded per SQAG process. The ordinary map holds at
most 4,096 client/normalized-route buckets. At capacity, unseen identities
share one overflow bucket per normalized configured route instead of evicting
an active ordinary client; the overflow map is capped at the 14 configured
rate-limited routes and fails closed if that finite state is unavailable.
Traffic triggers global stale-bucket pruning at most once every 15 seconds.
This is a per-process availability guard, not a cross-replica global throttle.

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

`verify_hosted_smoke.py` is a synthetic/local hosted-contract smoke verifier.
It binds only to `127.0.0.1`; it does not test a public hosted URL and is not
live Neon/R2 evidence or Platform handoff evidence.

Run the readiness checker with database metadata plus object artifact mode:

```powershell
python scripts\check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence --with-object-storage-evidence --with-object-artifact-lifecycle-evidence
```

The readiness checker is expected to keep both `internal_alpha_ready=false` and
`production_ready=false` until live object provider, live retention/delete,
DB+object backup/restore, hosted operations, and hosted smoke evidence are
actually available.

Before any separately authorized live retention/delete evidence run:

1. Run `python scripts\preflight_sqag_migrations.py`; it is read-only.
2. Require a present, exact ordered trusted ledger, zero pending migrations,
   and ready tables, indexes, triggers, and routines.
3. If migration application is needed, obtain separate authorization and use
   the migration runbook; verification does not grant that authority.
4. Re-run the read-only preflight and require zero pending migrations.
5. Only then may the live retention/delete evidence drill be separately
   authorized.

A verification command never grants itself migration authority, creates or
repairs the ledger, executes migration payloads, or runs DDL.

## Hosted Smoke Checklist

After a real host exists, record only metadata and never paste secrets, DB URLs,
cookies, platform tokens, generated quote contents, customer data, artifact
bytes, host IPs, or private paths into issue/PR output.

- App build completes.
- App starts with the documented start command.
- `/api/health` returns metadata-only JSON and HTTP 200 only while the database
  schema, object-artifact metadata schema, and object bucket are usable.
- Unauthenticated protected routes block or redirect.
- Platform/workspace launch reaches the app.
- Cross-subdomain finalization permits only the exact Platform origin, sets
  only the host-only SQAG cookie, and rejects replay.
- Every authenticated SQAG API request is revalidated with Platform; current
  role/authority changes and Platform unavailability fail closed immediately.
- Distinct clients behind the trusted proxy receive independent Platform
  launch and mutable-route rate-limit buckets, while repeated requests from
  the same effective client still receive HTTP 429.
- A direct client cannot select its rate-limit identity by supplying
  `X-Forwarded-For`, and an attacker-prepended address cannot override the
  first untrusted hop nearest the trusted proxy side of the chain.
- Intended workspace starts without a Koncept pack until import.
- Workspace-owned profile pack import/save/use works.
- Workspace-owned pricing reference import/save/use works.
- Quote generation persists metadata through database quote sessions.
- XLSX/PDF artifacts require object storage before hosted/protected/deploy or
  production readiness can be claimed.
- Delete makes the session and artifacts inaccessible.
- Concurrent save/delete probes for the same profile, pricing reference, and
  quote session resolve to a consistent save-wins, delete-wins, or generic-503
  outcome; no active object metadata remains without its workspace owner.
- Logout/sign-out behaves safely.
- Legacy direct job file downloads remain disabled in deploy/database/platform
  paths.
- Logs remain metadata-only and omit secrets, provider responses, private data,
  generated quote contents, and artifact bytes.

## Production Routing And Reputation Evidence

The only approved production routing shape is:

- `https://swooshz.com`: Platform and the only allowed browser finalization
  Origin.
- `https://www.swooshz.com`: permanent redirect to the apex, preserving only
  safe intended paths/query behavior.
- `https://quote.swooshz.com`: SQAG and the exact `SQAG_PUBLIC_BASE_URL`.
- `app.swooshz.com`: not a production origin or redirect target.

Before recording hosted evidence, verify trusted certificates and SAN coverage
for each served hostname. No route may expose a Traefik default 404/503,
self-signed certificate, wrong-host response, or wrong-port origin. Test the
apex/www/quote behavior from desktop and mobile user agents and Googlebot.
Capture sanitized evidence for redirects, certificate trust/SAN, screenshots,
forms, iframes, external resources, API requests, and every redirect/CORS
origin. Verify Google Safe Browsing status separately for each hostname. If the
operator already has Search Console access, record sanitized URL Inspection
results; this runbook does not authorize requesting indexing, review, or
reconsideration.

## Not Proven

This scaffold does not prove:

- Live VPS/Coolify host health.
- DNS, TLS, firewall, reverse proxy, or server operations.
- The live Coolify/Traefik network CIDRs or forwarded-header behavior; those
  remain hosted evidence and must not be copied into repository or PR output.
- Real OIDC provider behavior.
- Live Swooshz Platform integration.
- Real object-storage provider wiring.
- DB+object backup/restore or live object retention/delete evidence.
- Live Postgres advisory-lock behavior under concurrent SQAG replicas; local
  tests cover deterministic SQLite interleavings and synthetic Postgres SQL
  ordering only.
- Production observability export or alert delivery.
- Production readiness.
