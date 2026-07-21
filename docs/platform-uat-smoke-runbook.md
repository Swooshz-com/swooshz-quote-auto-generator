# SQAG Platform UAT Smoke Runbook

This runbook proves the first internal Swooshz Platform to SQAG handoff path:
Platform owns login, session, workspace, membership, role, entitlement, and app
access decisions; SQAG consumes the Platform launch context, creates its own
runtime session, and stores quote sessions and generated artifacts under the
Platform workspace context.

Use placeholders only in notes and reports. Do not paste real launch tokens,
provider tokens, auth codes, OIDC state, nonce values, provider responses,
callback URLs with query parameters, database URLs, staff emails, production
domains, private profile paths, or generated customer quotes into chat, docs,
issues, logs, screenshots, or PR text.

## Scope

In scope:

- Platform Google OIDC sign-in and Platform session creation.
- Platform SQAG app access and browser launch handoff.
- Platform server-side handoff to SQAG using `X-App-Launch-Token`.
- SQAG launch consume through `POST /api/platform/launch`.
- Raw launch token forwarded from SQAG to Platform only in the
  `X-App-Launch-Token` header.
- Cross-subdomain finalization with a header-only one-time handle and an SQAG
  host-only signed runtime cookie containing safe context plus a non-secret
  validation grant ID.
- Fail-closed Platform validation on every authenticated SQAG API request.
- Platform-scoped SQAG database rows for quote sessions.
- Platform-scoped generated XLSX artifact storage and download through the
  quote-session route used by the past-session dashboard.

Out of scope:

- Production deployment, DNS, TLS, or reverse proxy setup.
- Public signup, billing, invitations, member management, or admin dashboard.
- SQAG-owned accounts, login, membership, billing, or app registry.
- Object storage and multi-instance durable job execution.
- Private Koncept profile or pricing files committed to the repo.

## Automated Local Coverage

The unit test
`test_platform_uat_smoke_launch_generate_list_and_download_database_artifact`
uses a fake Platform consume response, a synthetic generator subprocess, and a
temporary SQLite database. It proves that SQAG can:

- Accept a launch token only through `X-App-Launch-Token`.
- Call Platform consume with the raw token only in the header.
- Create a SQAG session from safe Platform context.
- Generate a synthetic XLSX through the normal `/api/generate` HTTP path.
- Persist quote session and artifact rows under the Platform workspace ID.
- List the generated session through `/api/quote-sessions`.
- Download the stored XLSX through the quote-session download route.
- Avoid storing or returning the raw launch token.

Run the focused smoke locally with:

```powershell
python -m unittest tests.test_webapp.WebappServerTest.test_platform_uat_smoke_launch_generate_list_and_download_database_artifact
```

## Local Environment

SQAG Platform launch mode is explicit and disabled by default:

For Windows local UAT, the recommended Windows local path is the helper script
`scripts/local-uat-sqag-start.ps1`. It detects `py -3`, then `python`, then
`python3`; creates a disposable, gitignored runtime root under
`<repo>\_tmp\sqag-platform-uat`; applies SQAG storage migrations unless
`-SkipMigrations` is passed; and starts SQAG in Platform launch mode without
printing session secrets or database URLs.

```powershell
.\scripts\local-uat-sqag-start.ps1 -PlatformBaseUrl "http://127.0.0.1:4317"
```

Optional overrides:

```powershell
.\scripts\local-uat-sqag-start.ps1 `
  -PlatformBaseUrl "http://127.0.0.1:4317" `
  -SqagDatabaseUrl "<disposable-sqlite-url>" `
  -UatRoot "<disposable-uat-root>" `
  -SqagPort 8765 `
  -SkipMigrations
```

After the helper starts, copy only the printed `PLATFORM_SQAG_APP_BASE_URL`
line into the Platform shell. Keep Platform and SQAG on the same browser cookie
host, such as `127.0.0.1`.

### Manual fallback

If you need to run the commands manually, set the same local process
environment:

```powershell
$env:APP_MODE="local"
$env:AUTH_REQUIRED="true"
$env:SESSION_SECRET="<sqag-session-secret>"
$env:SQAG_PLATFORM_LAUNCH_MODE="platform"
$env:SQAG_PLATFORM_BASE_URL="<platform-base-url>"
$env:SQAG_PUBLIC_BASE_URL="<sqag-base-url>"
$env:SQAG_PLATFORM_SERVICE_SECRET="<synthetic-shared-service-secret>"
$env:SQAG_PLATFORM_REQUEST_TIMEOUT_SECONDS="10"
$env:SQAG_STORAGE_MODE="database"
$env:SQAG_ARTIFACT_STORAGE_MODE="database"
$env:SQAG_DATABASE_URL="<sqag-local-database-url>"
```

Local mode is required for this same-machine fallback because deploy mode
accepts only the fixed production origins. Production evidence must use the
canonical origins listed below.

Apply the reviewed SQAG storage migrations only against a disposable local
database:

```powershell
@'
from webapp import server
server.apply_sqag_storage_migrations("<sqag-local-database-url>")
'@ | python -
```

Start SQAG locally after the environment is set:

```powershell
python -m webapp.server
```

## Manual Platform Smoke

1. In the Platform repo, complete the reviewed local pre-smoke commands and
   migrations against a disposable local database.
2. Configure Platform for same-host local SQAG handoff:

```powershell
$env:PLATFORM_SQAG_LAUNCH_MODE="server_handoff"
$env:PLATFORM_SQAG_APP_BASE_URL="<sqag-local-base-url>"
```

3. Start Platform with `npm run platform:start`.
4. Open `<platform-base-url>/` in the browser.
5. Complete Google sign-in.
6. Confirm the callback lands on `/app`.
7. Seed the logged-in provider-backed user for SQAG access using the Platform
   seed command with the email used for sign-in. Do not paste the real email
   into chat or PR text.
8. Refresh `/app` and confirm session context, workspace, and SQAG app access
   appear.
9. Click the SQAG launch button in the Platform shell.
10. Confirm the browser reaches `<sqag-local-base-url>/` without a launch token
    in the URL.
11. Confirm the browser-scoped SQAG session context loads by checking the
    browser request to `<sqag-base-url>/api/session`. Do not copy the real
    session cookie into chat, PR text, screenshots, or docs.

12. For generated quote storage, session listing, and XLSX download coverage
    without private files or live services, run the automated local smoke test
    above. Use safe test data only for any additional manual generation.
13. Confirm no raw launch token, provider token, auth code, OIDC state, nonce,
    provider payload, callback query, database URL, staff email, production
    domain, private local path, or generated customer quote appears in logs,
    browser storage, screenshots, docs, or PR text.

## Browser Launch Shape

The Platform internal shell uses the browser-safe handoff route:
`POST /api/platform/apps/launch/open?workspaceId=<platform-workspace-id>&appKey=sqag`.
The browser calls Platform only. Platform creates the one-time launch token
server-side, sends it to SQAG only in the `X-App-Launch-Token` header on
`POST <sqag-local-base-url>/api/platform/launch`. SQAG consumes the token,
registers only a random handle hash with Platform, and returns the raw handle
only in `X-SQAG-Finalization-Handle`. Platform returns that header to the
browser with clean finalization/launch URLs. The browser credentialed-POSTs the
header to SQAG's `/api/auth/platform/finalize` from the exact Platform origin.
Only SQAG sets the final host-only cookie; Platform never relays or sets it.

Platform and SQAG may use separate allowed origins/subdomains. Confirm the
finalization preflight permits only the exact configured Platform origin and
the finalization-handle header, and that no parent-domain cookie is emitted.
For production evidence, use only `https://swooshz.com` for Platform and
`https://quote.swooshz.com` for SQAG; `www` must permanently redirect to the
apex and must not pass CORS.

## Pass Criteria

- Platform session context loads after Google sign-in.
- Platform workspace appears.
- SQAG app access appears.
- Platform SQAG launch button reaches SQAG through the server-side handoff.
- SQAG launch consume succeeds.
- Header-only finalization succeeds once and replay fails.
- SQAG browser-scoped session context loads with Platform workspace context.
- Role downgrade, revoke, expiry, disabled user/membership/entitlement, and
  Platform validation unavailability deny the next authenticated SQAG API
  request.
- Automated local smoke proves a generated quote session is stored under the
  Platform workspace ID.
- Automated local smoke proves a generated XLSX artifact downloads from the
  quote-session route.
- Raw launch token is not stored after consume and is not present in reports,
  screenshots, logs, URLs, browser storage, database rows, or SQAG responses.
