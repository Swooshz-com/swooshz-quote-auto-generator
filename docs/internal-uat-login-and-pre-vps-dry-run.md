# Internal UAT Login And Pre-VPS Dry Run

## Purpose

Use this guide before buying or touching a VPS. It covers the parts of the
bounded internal UAT login and deploy-auth path that can be verified locally
with synthetic values only.

This is not an account system, public SaaS launch, customer portal, billing
flow, database-backed user model, or production deployment plan. Hosted SQAG
uses only the Swooshz Platform launch boundary so the authenticated session
includes the trusted workspace, membership role, and entitlement.

## Approved Tester Launch Flow

In deploy mode, unauthenticated browser requests redirect to `/login`, which
shows a Platform-launch-required page linking to the configured Swooshz
Platform base URL. The Platform owns provider login, workspace selection,
membership, entitlement, and launch-token issuance. SQAG consumes the one-time
launch token server-side and creates only its own signed session.

Approved testers should expect:

- A normal sign-in and workspace-selection flow owned by Swooshz Platform.
- Return to SQAG after a successful Platform launch consume.
- A privacy-safe dashboard state such as `Signed in as approved tester`.
- A logout action that clears the SQAG session and returns to Platform.

The app must not show raw launch tokens, Platform responses, OIDC claims, auth
codes, access tokens, ID tokens, refresh tokens, provider responses, or session
secrets. Denied users see a generic approved-tester access message.

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
python -m unittest tests.test_webapp.WebappServerTest.test_platform_launch_mode_consumes_header_token_and_sets_safe_session
python -m unittest tests.test_webapp.WebappServerTest.test_deploy_logout_clears_session_and_state_cookies
python -m unittest tests.test_webapp.WebappServerTest.test_deploy_rejects_oidc_identity_without_platform_workspace_context
python -m unittest tests.test_webapp.WebappServerTest.test_internal_uat_coolify_env_template_is_offline_verifiable
```

These checks verify:

- The Coolify env template has the required Platform launch keys.
- Template secret/provider-specific fields remain placeholders.
- Runtime roots remain placeholders in committed templates and must be
  host-managed housekeeping paths, not durable quote-session or artifact
  storage for hosted use.
- The hosted validation bundle proves database storage plus database
  artifact/BLOB mode remains blocked as launch readiness, even with synthetic
  evidence.
- Deploy preflight can reach `ready` with synthetic env and temporary runtime
  roots outside the repo.
- Missing deploy-auth config blocks without printing secret values.
- `/api/health` returns HTTP 200 only after the required generator, database
  schemas, and object-storage bucket probe pass; dependency failure returns
  metadata-only HTTP 503.
- Unauthenticated browser requests redirect to login.
- Unauthenticated API requests return `auth_required`.
- `/login` redirects to the configured synthetic Platform URL.
- Mocked Platform consume success sets a signed session containing only trusted
  workspace, membership, entitlement, and privacy-minimized user metadata.
- Missing, replayed, malformed, or denied launch tokens are blocked without
  leaking private values.

Standalone OIDC helpers remain covered as localhost/local component tests only.
They do not establish Platform workspace authority and cannot start
`APP_MODE=deploy`.

## What Still Requires Real VPS/Platform

These items cannot be completed before live infrastructure exists:

- DNS, TLS, firewall, VPS, Coolify host, reverse proxy, and public network
  reachability.
- Real Platform login, workspace membership, entitlement, launch consume,
  replay denial, and logout confirmation.
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
$env:SQAG_STORAGE_MODE="<database>"
$env:SQAG_ARTIFACT_STORAGE_MODE="<object-for-readiness-or-database-for-negative-test>"
$env:SQAG_DATABASE_URL="<synthetic-database-url>"
$env:SQAG_PLATFORM_LAUNCH_MODE="<platform>"
$env:SQAG_PLATFORM_BASE_URL="<synthetic-platform-base-url>"
```

Do not commit populated env files, real provider values, private local paths,
runtime folders, real profile JSON, files containing `logo_data_url`, embedded
Base64 logos, pricing files, generated quote exports, or customer/company/bank
data.

## Pass/Fail Interpretation

Pass means the repo artifacts, deploy-auth env shape, and mocked Platform
launch behavior are ready for a later real UAT host.

Fail means fix the reported key/category before buying or touching a VPS. Report
only key names, check names, status, and generic messages. Do not paste secret
values, provider responses, auth codes, tokens, or screenshots with private
data into GitHub.
