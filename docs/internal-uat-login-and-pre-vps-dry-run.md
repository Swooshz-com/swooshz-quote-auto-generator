# Internal UAT Login And Pre-VPS Dry Run

## Purpose

Use this guide before buying or touching a VPS. It covers the parts of the
bounded internal UAT login and deploy-auth path that can be verified locally
with synthetic values only.

This is not an account system, public SaaS launch, customer portal, billing
flow, database-backed user model, or production deployment plan. KQAG uses the
existing deploy-auth OIDC gate only for approved internal testers.

## Approved Tester Login Flow

In deploy mode, unauthenticated browser requests redirect to `/login`, and
`/login` redirects to the configured `OIDC_AUTHORIZE_URL`.

Approved testers should expect:

- A normal provider sign-in page from the configured internal OIDC provider.
- Return to KQAG after a successful provider callback.
- A privacy-safe dashboard state such as `Signed in as approved tester`.
- A logout action that clears the KQAG session and temporary OIDC state cookie.

The app must not show raw OIDC claims, auth codes, access tokens, ID tokens,
refresh tokens, provider responses, session secrets, or OIDC client secrets.
Denied users see a generic approved-tester access message.

## What Can Be Tested Before VPS

Run these local checks with synthetic env values and temporary runtime roots:

```powershell
python scripts\verify_internal_uat_deploy_template.py
```

```powershell
python webapp\server.py --check-deploy-uat-env
```

```powershell
python scripts\verify_internal_alpha_hosted_validation.py --work-dir _tmp\validation\hosted-blocked
```

```powershell
python -m unittest tests.test_webapp.WebappServerTest.test_deploy_auth_routes_block_unauthenticated_access_and_redirect_login
python -m unittest tests.test_webapp.WebappServerTest.test_deploy_oidc_callback_exchanges_code_fetches_userinfo_and_sets_session_cookie
python -m unittest tests.test_webapp.WebappServerTest.test_internal_uat_coolify_env_template_is_offline_verifiable
```

These checks verify:

- The Coolify env template has the required deploy-auth keys.
- Template secret/provider-specific fields remain placeholders.
- `AUTH_ALLOW_ANY_AUTHENTICATED_USER=false`.
- Runtime roots remain placeholders in committed templates and must be
  host-managed housekeeping paths, not durable quote-session or artifact
  storage for hosted use.
- The hosted validation bundle proves database storage plus database
  artifact/BLOB mode remains blocked as launch readiness, even with synthetic
  evidence.
- Deploy preflight can reach `ready` with synthetic env and temporary runtime
  roots outside the repo.
- Missing deploy-auth config blocks without printing secret values.
- `/api/health` stays reachable.
- Unauthenticated browser requests redirect to login.
- Unauthenticated API requests return `auth_required`.
- `/login` redirects to the configured fake authorize URL.
- Mocked callback success sets a signed session cookie.
- Missing state, provider error, missing `sub`, and unapproved testers are
  blocked without leaking private values.

## What Still Requires Real VPS/OIDC

These items cannot be completed before live infrastructure exists:

- DNS, TLS, firewall, VPS, Coolify host, reverse proxy, and public network
  reachability.
- Real OIDC application registration and redirect URI validation.
- Real provider login, logout, and tester allowlist confirmation.
- Coolify secret entry, volume mounting, app start, and healthcheck evidence on
  the prepared host.
- Authenticated quote workflow smoke testing through the real deployed URL.

## Safe Temporary Env Shape

Use only placeholders or synthetic values in local tests. Runtime roots should
be temporary folders outside the repository and should not be reported.

```powershell
$env:APP_MODE="<deploy>"
$env:AUTH_REQUIRED="<true>"
$env:SESSION_SECRET="<synthetic-session-secret>"
$env:KQAG_STORAGE_MODE="<database>"
$env:KQAG_ARTIFACT_STORAGE_MODE="<object-for-readiness-or-database-for-negative-test>"
$env:KQAG_DATABASE_URL="<synthetic-database-url>"
$env:KQAG_PLATFORM_LAUNCH_MODE="<platform>"
$env:KQAG_PLATFORM_BASE_URL="<synthetic-platform-base-url>"
$env:AUTH_ALLOWED_EMAILS="<synthetic-allowlist>"
$env:AUTH_ALLOWED_DOMAINS="<synthetic-allowlist-domain>"
$env:AUTH_ALLOW_ANY_AUTHENTICATED_USER="<false>"
$env:AUTH_APPROVED_TESTER_ROLE="<role>"
```

Do not commit populated env files, real provider values, private local paths,
runtime folders, real profile JSON, files containing `logo_data_url`, embedded
Base64 logos, pricing files, generated quote exports, or customer/company/bank
data.

## Pass/Fail Interpretation

Pass means the repo artifacts, deploy-auth env shape, and mocked OIDC route
behavior are ready for a later real UAT host.

Fail means fix the reported key/category before buying or touching a VPS. Report
only key names, check names, status, and generic messages. Do not paste secret
values, provider responses, auth codes, tokens, or screenshots with private
data into GitHub.
