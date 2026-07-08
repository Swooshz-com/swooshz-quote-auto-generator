# Internal UAT Deploy/Auth Readiness

## Purpose

This document explains the existing single-instance gated internal UAT path for
SQAG/SAQG deploy/auth scaffolding. It is a repo-specific readiness guide for
Koncept/Swooshz internal testing only.

This document does not approve a production launch, public SaaS access,
customer portal access, ecommerce, billing, DB-backed multi-user mode, or full
Swooshz platform integration. It also does not move platform-owned account,
membership, legal, billing, or app-whitelist work into SQAG.

## Current Repo-Supported Knobs

The deploy/auth surface is already represented in `.env.example` and
`webapp/server.py`.

- `APP_MODE=local`: localhost-first desktop/dev mode. The server defaults to
  loopback binding, local host allowlisting, and `AUTH_REQUIRED=false` unless
  explicitly overridden.
- `APP_MODE=deploy`: gated deploy mode. The server defaults to a deploy bind
  host and `AUTH_REQUIRED=true`.
- `AUTH_REQUIRED`: explicit auth gate toggle. In deploy mode, leaving this
  unset still defaults to auth required.
- `SESSION_SECRET`: required for signed session and OIDC state cookies when
  deploy auth is enabled. Never print or commit the value.
- `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
  `OIDC_REDIRECT_URI`, `OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL`,
  `OIDC_USERINFO_URL`, and `OIDC_LOGOUT_URL`: OIDC settings used by the deploy
  auth scaffold. `OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL`, and
  `OIDC_USERINFO_URL` are explicit provider endpoints; the app does not guess
  the authorize endpoint from the issuer. `OIDC_LOGOUT_URL` is optional; the
  other OIDC fields plus `SESSION_SECRET` are required for a complete auth
  boundary.
- `AUTH_ALLOWED_EMAILS`: comma-separated exact tester email allowlist.
- `AUTH_ALLOWED_DOMAINS`: comma-separated tester email-domain allowlist.
- `AUTH_ALLOW_ANY_AUTHENTICATED_USER`: internal UAT escape hatch only. Keep it
  `false` unless the UAT owner has explicitly accepted any authenticated
  identity from the configured OIDC provider.
- `AUTH_APPROVED_TESTER_ROLE`: shared role for approved internal testers:
  `admin`, `management`, `operator`, or `viewer`.
- `SQAG_STORAGE_MODE`: storage mode. Hosted/protected/deploy validation must
  use workspace-owned database rows for SQAG app records.
- `SQAG_ARTIFACT_STORAGE_MODE`: generated artifact storage mode. Database
  artifact/BLOB mode is local-UAT/synthetic evidence only and cannot satisfy
  hosted/protected/deploy readiness; generated XLSX/PDF bytes require object
  storage.
- `SQAG_DATABASE_URL`: database connection configured through the host secret
  manager only.
- `SQAG_PLATFORM_LAUNCH_MODE` and `SQAG_PLATFORM_BASE_URL`: platform/workspace
  launch context for protected hosted use.
- `QUOTE_DATA_ROOT`: runtime housekeeping root; not the hosted source of truth
  for profile/pricing/session data.
- `QUOTE_OUTPUT_ROOT`: output staging root; not durable hosted artifact storage.
- `QUOTE_TMP_ROOT`: temporary job/work root.
- `QUOTE_LOG_ROOT`: runtime log root.
- `USER_TYPE`: local role simulation for desktop/internal testing. Deploy mode
  must rely on authenticated session claims rather than local role simulation.

The server also changes cookie behavior in deploy mode: signed session and OIDC
state cookies are emitted with `Secure`, `HttpOnly`, and `SameSite=Lax`.

## Current Implementation Notes

- Deploy mode is intended to require authentication by default.
- Deploy mode refuses to start when auth is required and the auth boundary is
  incomplete.
- OIDC configuration completeness is checked before serving authenticated
  deploy traffic.
- `/login` redirects to `OIDC_AUTHORIZE_URL` with the configured client,
  redirect URI, response type, scope, and signed state when the auth boundary
  is complete.
- `/callback` validates state, handles provider errors generically, requires an
  authorization code, exchanges it at `OIDC_TOKEN_URL`, fetches user claims from
  `OIDC_USERINFO_URL`, requires a stable `sub`, enforces the internal allowlist,
  sets the signed session cookie, clears the temporary OIDC state cookie, and
  redirects to `/`.
- The callback uses token endpoint plus userinfo endpoint. It does not perform
  custom JWT signature verification.
- Provider tokens, raw provider responses, authorization codes,
  `OIDC_CLIENT_SECRET`, and `SESSION_SECRET` must not be printed or returned.
- In-memory jobs are acceptable for local mode and a first single-instance UAT
  deploy only. Multi-instance deployment requires durable job, upload, download,
  log, pricing-reference, and quote-session storage partitioned by authenticated
  user/account.

## Recommended Internal UAT Shape

Use this shape only for local/offline validation of the gated UAT boundary:

- Single UAT host.
- Single app instance.
- `APP_MODE=deploy`.
- `AUTH_REQUIRED=true`.
- `SQAG_STORAGE_MODE=database`.
- `SQAG_ARTIFACT_STORAGE_MODE=object` for hosted/protected/deploy readiness
  evidence; `database` is allowed only for local-UAT/synthetic negative tests.
- `SQAG_DATABASE_URL` set through the host secret manager only.
- Platform/workspace launch context for protected hosted use.
- OIDC configuration only when using the OIDC deploy-auth fallback path.
- `AUTH_ALLOWED_EMAILS` and/or `AUTH_ALLOWED_DOMAINS` set when using OIDC.
- Runtime housekeeping roots outside the repository.
- Approved tester access only.
- No public/customer access.
- No multi-instance scaling.
- No committed runtime data.
- No real secrets in repository files.

For quote workflow coverage that does not depend on deploy auth, keep using the
local internal UAT checklist in `docs/internal-uat.md`.

For an already-prepared Coolify host, use the SQAG-specific adapter in
`docs/internal-uat-coolify-deploy.md`. Generic Hostinger/VPS/Coolify setup,
SSH, firewall, DNS, TLS, and server maintenance guidance belongs to the
toolkit/infrastructure workflow, not this repo.

Before a VPS or live OIDC provider exists, use
`docs/internal-uat-login-and-pre-vps-dry-run.md` for the approved-tester login
expectations, offline Coolify env template check, mocked OIDC route coverage,
and safe synthetic deploy preflight.

## Explicitly Not Ready

The current SQAG repo is not ready for:

- Production launch.
- Public SaaS.
- Customer portal access.
- Multi-instance deployment.
- DB-backed session/history.
- Durable per-user storage partitioning.
- Billing, credits, checkout, orders, or ecommerce.
- User, account, team, membership, or customer-management models.
- Platform-owned app whitelist/account membership.
- Public-use auth hardening, account lifecycle, account membership, and
  platform-owned authorization.
- Legal/customer-facing production launch without privacy, terms, retention,
  sub-processor, and counsel review.

## Safe Deploy-Auth Preflight

Run the preflight before starting a deploy-mode UAT app. It reports only
presence/shape and does not print secret values:

```powershell
python webapp\server.py --check-deploy-uat-env
```

Pass means:

- `APP_MODE=deploy`.
- `AUTH_REQUIRED=true`.
- `SESSION_SECRET` is present.
- Database storage is selected, and database artifact/BLOB mode is not treated
  as hosted/protected/deploy readiness evidence.
- Required platform launch settings are present, or required OIDC
  endpoint/client settings are present for the OIDC fallback path.
- `OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL`, and `OIDC_USERINFO_URL` are provided
  explicitly when OIDC is used.
- An internal allowlist or explicit internal escape hatch is configured.
- `AUTH_APPROVED_TESTER_ROLE` is valid.
- Runtime housekeeping roots are set and outside the repository.

Fail means fix the env shape before starting the UAT app. Do not paste secret
values into bug reports; report only which check name failed.

For the Coolify template itself, run this local offline verifier before entering
values into a live host:

```powershell
python scripts\verify_internal_uat_deploy_template.py
```

## UAT Deploy Smoke Checklist

Use this checklist for the gated internal UAT deploy path. Do not print secrets
while checking env values.

- [ ] Confirm required env names are present without printing values:
      `APP_MODE`, `AUTH_REQUIRED`, `SESSION_SECRET`, `SQAG_STORAGE_MODE`,
      `SQAG_ARTIFACT_STORAGE_MODE`, `SQAG_DATABASE_URL`,
      `SQAG_PLATFORM_LAUNCH_MODE`, `SQAG_PLATFORM_BASE_URL`, optional OIDC
      names if used, allowlist settings, tester role, and runtime
      housekeeping root envs.
- [ ] Run `python webapp\server.py --check-deploy-uat-env`.
- [ ] Run `python scripts\verify_internal_alpha_hosted_validation.py --work-dir _tmp\validation\hosted-blocked`.
- [ ] Confirm `APP_MODE=deploy`.
- [ ] Confirm `AUTH_REQUIRED=true`.
- [ ] Confirm runtime roots are outside the repository.
- [ ] Confirm quote-domain records use workspace-owned database storage.
- [ ] Confirm generated artifact bytes require object storage for
      hosted/protected/deploy readiness and are not credited from DB/BLOB mode.
- [ ] Confirm the app refuses unsafe or incomplete deploy-auth configuration.
- [ ] Confirm the health endpoint responds.
- [ ] Confirm unauthenticated users are blocked or redirected.
- [ ] Confirm OIDC login redirects to the exact configured
      `OIDC_AUTHORIZE_URL`.
- [ ] Confirm OIDC callback completes for an approved tester and redirects to
      `/`.
- [ ] Confirm OIDC callback blocks a non-allowlisted tester with a generic
      forbidden response.
- [ ] Confirm an authenticated approved tester can reach the dashboard.
- [ ] Confirm New Quote works.
- [ ] Confirm profile/pricing import works with authorised private local files.
- [ ] Confirm quote generation works.
- [ ] Confirm XLSX/PDF export works.
- [ ] Confirm dashboard modify/download/delete works.
- [ ] Confirm direct runtime/output files are not publicly browsable.
- [ ] Confirm `git status --short` has no private runtime/output files.

## Private Data And Secret Rules

Do not commit, paste into GitHub, print in logs, or include in screenshots:

- `.env` secrets.
- OIDC client secret.
- Session secret.
- Authorization code.
- Access token.
- Refresh token.
- ID token.
- Raw OIDC provider response.
- Tunnel/provider tokens.
- Real profile JSON.
- Files containing `logo_data_url`.
- Embedded Base64 logos.
- Real pricing files.
- Generated quote exports.
- Runtime session folders.
- Customer, company, or bank data.
- Private filesystem paths.

Use placeholders in docs, issues, PRs, and bug reports, and redact screenshots
unless they use clearly synthetic/test-only data.

## Validation Evidence To Keep With UAT Notes

For each internal deploy-auth UAT run, record only non-secret evidence:

- Date/time and app version or commit.
- `APP_MODE` value.
- Whether `AUTH_REQUIRED` is enabled.
- Whether required OIDC env names are set, without values.
- Runtime root category, such as `outside repo`, without private paths.
- Health endpoint result.
- Auth redirect/block result.
- OIDC callback result.
- Quote workflow smoke result if authenticated dashboard access is available.
- Confirmation that no private runtime/output files appear in `git status`.
