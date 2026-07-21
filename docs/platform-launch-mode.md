# SQAG Platform Launch Mode

This runbook covers the first SQAG-side adapter boundary for Swooshz Platform
launch handoff. It is disabled by default and does not change local/internal
SQAG mode.

## Boundary

Swooshz Platform owns login, platform sessions, users, workspaces, membership
roles, app entitlements, app access decisions, finalization state, and
validation-grant lifecycle. SQAG keeps only its signed host-only runtime cookie
with safe Platform context and the non-secret validation grant ID.

SQAG must not store provider tokens, raw provider claims, auth codes, OIDC
state, nonce, platform session cookies, raw launch tokens, or platform database
details.

## Deploy Invariant

`APP_MODE=deploy` requires a complete Platform launch configuration. A
standalone OIDC session has no Platform workspace, membership role, or app
entitlement and therefore cannot start the hosted SQAG process. OIDC helpers
remain available only for localhost/local component testing; SQAG must not
manufacture a workspace from OIDC tenant or account claims.

Every authenticated deploy request also requires complete signed Platform
session provenance: consumed outcome, Platform user id, workspace id, SQAG app
key, and a supported membership role. Legacy OIDC-only cookies remain
cryptographically valid only until their original expiry, but deploy mode
treats them as unauthenticated and grants no permissions. Platform mode rejects
the OIDC callback before provider calls; logout still clears the rejected
cookie and returns to the validated Platform base URL.

Before binding a deploy server, SQAG also performs read-only checks for the
required database schema, object-artifact metadata schema, and configured
object-storage bucket. `/api/health` returns HTTP 503 with metadata-only check
results while any required dependency is unavailable.

## Enable Platform Launch Mode

Use placeholders for local smoke setup:

```powershell
$env:APP_MODE="deploy"
$env:AUTH_REQUIRED="true"
$env:SESSION_SECRET="<sqag-session-secret>"
$env:SQAG_PLATFORM_LAUNCH_MODE="platform"
$env:SQAG_PLATFORM_BASE_URL="https://swooshz.com"
$env:SQAG_PUBLIC_BASE_URL="https://quote.swooshz.com"
$env:SQAG_PLATFORM_SERVICE_SECRET="<host-managed-shared-secret>"
$env:SQAG_PLATFORM_REQUEST_TIMEOUT_SECONDS="10"
```

`SQAG_PLATFORM_LAUNCH_MODE=disabled` is the default and keeps the existing
local/internal flow unchanged.

## Launch Consume Endpoint

SQAG accepts a platform launch token only through:

```http
POST /api/platform/launch HTTP/1.1
X-App-Launch-Token: <one-time-platform-launch-token>
```

The adapter then calls:

```http
POST <platform-base-url>/api/platform/apps/launch/consume?appKey=sqag
X-App-Launch-Token: <one-time-platform-launch-token>
```

The raw token must not be placed in query parameters, browser storage, logs,
files, screenshots, docs, or telemetry. Consume must return a mandatory,
timezone-aware, future, bounded `launchTokenExpiresAt` plus a non-secret
`validationGrantId`. SQAG then generates a random short-lived finalization
handle, registers only its SHA-256 hash with Platform, and returns the raw
handle only in `X-SQAG-Finalization-Handle`.

The browser sends that header from the exact configured Platform origin to
`POST /api/auth/platform/finalize`. SQAG consumes the handle server-to-server,
then sets only its host-only `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`
cookie. It never sets a Platform cookie or a parent-domain cookie. Every later
authenticated SQAG API request validates the non-secret grant ID with Platform
under `X-SQAG-Service-Authorization`; validation failures and authority changes
take effect on that request. Logout always clears the SQAG cookie and attempts
to revoke the Platform grant.

Production routing is exact: Platform is `https://swooshz.com`,
`https://www.swooshz.com` permanently redirects to the apex, and SQAG is
`https://quote.swooshz.com`. `SQAG_PUBLIC_BASE_URL` binds deploy Host checks and
finalization state to the exact SQAG origin; wrong hosts or ports are rejected.
`app.swooshz.com` is not a production Platform origin.

## Accepted Consume Context

SQAG stores only these fields:

- `outcome`
- `user.userId`
- `user.email`
- `user.displayName`
- `user.status`
- `workspace.workspaceId`
- `workspace.workspaceSlug`
- `workspace.workspaceName`
- `app.appKey`
- `app.appName`
- `membershipRole`
- `launchTokenExpiresAt`
- `validationGrantId`

SQAG rejects missing tokens, consume failures, non-`consumed` outcomes, wrong
app keys, missing platform user IDs, missing workspace IDs, and stale expiry
values.

## Deferred Work

This adapter does not add production deployment, object storage, billing,
public signup, member management, or SQAG-owned accounts. Platform-scoped
database storage for app data and generated artifacts is available behind its
own explicit storage flags; multi-instance durable jobs and object storage are
future work.

For the operator smoke that ties launch consume, Platform workspace context,
database quote-session rows, generated XLSX artifacts, and dashboard download
together, see `docs/platform-uat-smoke-runbook.md`.
