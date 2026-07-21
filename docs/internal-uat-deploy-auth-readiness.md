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
  unset still defaults to auth required. Setting it to `false` is treated as an
  incomplete auth boundary and must not be used for hosted UAT.
- `SESSION_SECRET`: required for signed session and OIDC state cookies when
  deploy auth is enabled. It must be at least 32 characters. Never print or
  commit the value.
- `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`,
  `OIDC_REDIRECT_URI`, `OIDC_AUTHORIZE_URL`, `OIDC_TOKEN_URL`,
  `OIDC_USERINFO_URL`, and `OIDC_LOGOUT_URL`: retained OIDC component-test
  settings for localhost/local mode only. They do not establish a trusted
  Platform workspace and cannot satisfy deploy startup. `OIDC_AUTHORIZE_URL`,
  `OIDC_TOKEN_URL`, and
  `OIDC_USERINFO_URL` are explicit provider endpoints; the app does not guess
  the authorize endpoint from the issuer. `OIDC_LOGOUT_URL` is optional; the
  other OIDC fields plus `SESSION_SECRET` are required for a complete auth
  boundary. OIDC URL fields must use HTTPS, except loopback HTTP is allowed for
  local smoke-only endpoints.
- `AUTH_ALLOWED_EMAILS`: comma-separated exact tester email allowlist.
- `AUTH_ALLOWED_DOMAINS`: comma-separated tester email-domain allowlist.
- `AUTH_ALLOW_ANY_AUTHENTICATED_USER`: local OIDC component-test escape hatch
  only. It does not grant deploy or Platform workspace access.
- `AUTH_APPROVED_TESTER_ROLE`: local OIDC component-test role:
  `admin`, `management`, `operator`, or `viewer`.
- `SQAG_STORAGE_MODE`: storage mode. Hosted/protected/deploy validation must
  use workspace-owned database rows for SQAG app records.
- `SQAG_ARTIFACT_STORAGE_MODE`: generated artifact storage mode. Database
  artifact/BLOB mode is local-UAT/synthetic evidence only and cannot satisfy
  hosted/protected/deploy readiness; generated XLSX/PDF bytes require object
  storage.
- `SQAG_DATABASE_URL`: database connection configured through the host secret
  manager only.
- `SQAG_PLATFORM_LAUNCH_MODE`, `SQAG_PLATFORM_BASE_URL`,
  `SQAG_PUBLIC_BASE_URL`, and `SQAG_PLATFORM_SERVICE_SECRET`: mandatory platform/workspace launch,
  finalization, validation, and revoke boundary for protected hosted/deploy
  use. The shared service secret must be configured separately in both runtimes
  and contain at least 32 characters; it must not appear in repositories, logs,
  screenshots, reports, or chat. `SQAG_PLATFORM_REQUEST_TIMEOUT_SECONDS` is an optional bounded,
  non-secret timeout. The
  deploy mode fixes these origins to `https://swooshz.com` and
  `https://quote.swooshz.com`; loopback flexibility is local-mode only.
- `SQAG_TRUSTED_PROXY_CIDRS`: mandatory deploy-mode comma-separated CIDRs for
  only the reverse-proxy peers that connect directly to SQAG. SQAG accepts a
  bounded, valid `X-Forwarded-For` chain only from those peers, walks the chain
  right-to-left past trusted proxies, and uses the first untrusted address for
  Platform-launch and mutable-route rate limits. Missing, malformed,
  oversized, overlong, or untrusted forwarding data falls back to the socket
  peer. Never configure a trust-all network.
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
- Deploy mode also refuses to start without a complete Swooshz Platform launch
  boundary because database and object-storage records require the consumed
  Platform workspace identity. Standalone OIDC claims are never converted into
  a workspace, membership role, or entitlement.
- Deploy preflight, startup, Platform launch, and protected request handling
  fail closed when the trusted-proxy CIDR boundary is missing or malformed.
  Forwarded headers are not logged and cannot be used by a direct untrusted
  client to select a rate-limit bucket.
- Deploy request authentication accepts only a signed session containing the
  consumed Platform user, workspace, SQAG app key, supported membership role,
  and non-secret validation grant ID. Every authenticated API request rechecks
  that grant and replaces the role with Platform's current result. A still-valid pre-upgrade OIDC-only cookie is rejected as
  unauthenticated and receives no deploy permissions.
- Platform mode does not process the legacy OIDC callback. Logout remains
  available to clear both session and OIDC-state cookies and returns to the
  validated Platform base URL rather than a stale OIDC logout setting.
- OIDC configuration and route behavior remain testable in local/component
  checks, but OIDC-only configuration cannot start `APP_MODE=deploy`.
- In local OIDC component mode, `/login` redirects to `OIDC_AUTHORIZE_URL` with the configured client,
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
- `SESSION_SECRET` is at least 32 characters.
- `SQAG_STORAGE_MODE=database`.
- `SQAG_ARTIFACT_STORAGE_MODE=object` for hosted/protected/deploy readiness
  evidence; `database` is allowed only for local-UAT/synthetic negative tests.
- `SQAG_DATABASE_URL` set through the host secret manager only.
- Platform/workspace launch context for every protected hosted/deploy use.
- Exact direct Coolify/Traefik proxy CIDRs set through the host environment
  manager only; no trust-all proxy range.
- No standalone OIDC fallback in the hosted SQAG process. Platform owns login,
  workspace membership, role, entitlement, and launch authorization.
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
- `SESSION_SECRET` is at least 32 characters.
- Database storage is selected, and database artifact/BLOB mode is not treated
  as hosted/protected/deploy readiness evidence.
- Required Platform launch settings are present. OIDC-only settings may appear
  as local component-test diagnostics but never make deploy preflight ready.
- `SQAG_TRUSTED_PROXY_CIDRS` contains a valid, nonempty trusted direct-proxy
  boundary.
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
      `SQAG_PLATFORM_LAUNCH_MODE`, `SQAG_PLATFORM_BASE_URL`,
      `SQAG_PUBLIC_BASE_URL`, `SQAG_PLATFORM_SERVICE_SECRET`,
      `SQAG_TRUSTED_PROXY_CIDRS`, and runtime housekeeping root envs.
- [ ] Run `python webapp\server.py --check-deploy-uat-env`.
- [ ] Run `python scripts\verify_internal_alpha_hosted_validation.py --work-dir _tmp\validation\hosted-blocked`.
- [ ] Confirm `APP_MODE=deploy`.
- [ ] Confirm `AUTH_REQUIRED=true`.
- [ ] Confirm runtime roots are outside the repository.
- [ ] Confirm quote-domain records use workspace-owned database storage.
- [ ] Confirm generated artifact bytes require object storage for
      hosted/protected/deploy readiness and are not credited from DB/BLOB mode.
- [ ] Confirm the app refuses unsafe or incomplete deploy-auth configuration.
- [ ] Confirm `/api/health` returns HTTP 200 only after the generator, required
      database schema, object-artifact metadata schema, and object-storage
      bucket probe pass; dependency failure must return metadata-only HTTP 503.
- [ ] Confirm unauthenticated users are blocked or redirected.
- [ ] Confirm launch consume registers a hashed short-lived finalization handle,
      exact-origin browser finalization sets only the host-only SQAG cookie, and
      handle replay fails without returning the launch token or raw handle in a body/URL.
- [ ] Confirm validation runs on every authenticated SQAG API request and that
      revoke, expiry, role downgrade, user/membership/entitlement disable, and
      Platform unavailability deny or reduce authority on the next request.
- [ ] Confirm distinct forwarded clients behind the configured trusted proxy do
      not share one Platform-launch or mutable-route rate-limit bucket, while
      repeated requests from the same effective client still receive HTTP 429.
- [ ] Confirm direct clients cannot spoof `X-Forwarded-For`, and attacker-added
      leftmost addresses do not override the first untrusted hop nearest the
      trusted proxy side of the chain.
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
- Whether required Platform launch env names are set, without values.
- Whether the trusted-proxy CIDR env name is set and valid, without values.
- Runtime root category, such as `outside repo`, without private paths.
- Health endpoint result.
- Auth redirect/block result.
- Platform launch consume and workspace-session result.
- Quote workflow smoke result if authenticated dashboard access is available.
- Confirmation that no private runtime/output files appear in `git status`.
