# SQAG Platform Launch Mode

This runbook covers the first SQAG-side adapter boundary for Swooshz Platform
launch handoff. It is disabled by default and does not change local/internal
SQAG mode.

## Boundary

Swooshz Platform owns login, platform sessions, users, workspaces, membership
roles, app entitlements, and app access decisions. SQAG consumes only the
platform launch context needed to create its own runtime session.

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
$env:SQAG_PLATFORM_BASE_URL="https://platform.example.test"
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
files, screenshots, docs, or telemetry. After consume, SQAG stores only the safe
platform context returned by the consume response in its signed SQAG session.

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
