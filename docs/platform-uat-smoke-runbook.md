# KQAG Platform UAT Smoke Runbook

This runbook proves the first internal Swooshz Platform to KQAG handoff path:
Platform owns login, session, workspace, membership, role, entitlement, and app
access decisions; KQAG consumes the Platform launch context, creates its own
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
- Platform KQAG app access and browser launch handoff.
- Platform server-side handoff to KQAG using `X-App-Launch-Token`.
- KQAG launch consume through `POST /api/platform/launch`.
- Raw launch token forwarded from KQAG to Platform only in the
  `X-App-Launch-Token` header.
- KQAG signed runtime session with safe Platform context only.
- Platform-scoped KQAG database rows for quote sessions.
- Platform-scoped generated XLSX artifact storage and download through the
  quote-session route used by the past-session dashboard.

Out of scope:

- Production deployment, DNS, TLS, or reverse proxy setup.
- Public signup, billing, invitations, member management, or admin dashboard.
- KQAG-owned accounts, login, membership, billing, or app registry.
- Object storage and multi-instance durable job execution.
- Private Koncept profile or pricing files committed to the repo.

## Automated Local Coverage

The unit test
`test_platform_uat_smoke_launch_generate_list_and_download_database_artifact`
uses a fake Platform consume response, a synthetic generator subprocess, and a
temporary SQLite database. It proves that KQAG can:

- Accept a launch token only through `X-App-Launch-Token`.
- Call Platform consume with the raw token only in the header.
- Create a KQAG session from safe Platform context.
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

KQAG Platform launch mode is explicit and disabled by default:

For Windows local UAT, the recommended Windows local path is the helper script
`scripts/local-uat-kqag-start.ps1`. It detects `py -3`, then `python`, then
`python3`; creates a disposable runtime root under
`$env:TEMP\kqag-platform-uat`; applies KQAG storage migrations unless
`-SkipMigrations` is passed; and starts KQAG in Platform launch mode without
printing session secrets or database URLs.

```powershell
.\scripts\local-uat-kqag-start.ps1 -PlatformBaseUrl "http://127.0.0.1:4317"
```

Optional overrides:

```powershell
.\scripts\local-uat-kqag-start.ps1 `
  -PlatformBaseUrl "http://127.0.0.1:4317" `
  -KqagDatabaseUrl "<disposable-sqlite-url>" `
  -UatRoot "<disposable-uat-root>" `
  -KqagPort 8765 `
  -SkipMigrations
```

After the helper starts, copy only the printed `PLATFORM_KQAG_APP_BASE_URL`
line into the Platform shell. Keep Platform and KQAG on the same browser cookie
host, such as `127.0.0.1`.

### Manual fallback

If you need to run the commands manually, set the same local process
environment:

```powershell
$env:APP_MODE="deploy"
$env:AUTH_REQUIRED="true"
$env:SESSION_SECRET="<kqag-session-secret>"
$env:KQAG_PLATFORM_LAUNCH_MODE="platform"
$env:KQAG_PLATFORM_BASE_URL="<platform-base-url>"
$env:KQAG_STORAGE_MODE="database"
$env:KQAG_ARTIFACT_STORAGE_MODE="database"
$env:SQAG_DATABASE_URL="<kqag-local-database-url>"
```

Apply the reviewed KQAG storage migrations only against a disposable local
database:

```powershell
@'
from webapp import server
server.apply_kqag_storage_migrations("<kqag-local-database-url>")
'@ | python -
```

Start KQAG locally after the environment is set:

```powershell
python -m webapp.server
```

## Manual Platform Smoke

1. In the Platform repo, complete the reviewed local pre-smoke commands and
   migrations against a disposable local database.
2. Configure Platform for same-host local KQAG handoff:

```powershell
$env:PLATFORM_KQAG_LAUNCH_MODE="server_handoff"
$env:PLATFORM_KQAG_APP_BASE_URL="<kqag-local-base-url>"
```

3. Start Platform with `npm run platform:start`.
4. Open `<platform-base-url>/` in the browser.
5. Complete Google sign-in.
6. Confirm the callback lands on `/app`.
7. Seed the logged-in provider-backed user for KQAG access using the Platform
   seed command with the email used for sign-in. Do not paste the real email
   into chat or PR text.
8. Refresh `/app` and confirm session context, workspace, and KQAG app access
   appear.
9. Click the KQAG launch button in the Platform shell.
10. Confirm the browser reaches `<kqag-local-base-url>/` without a launch token
    in the URL.
11. Confirm the browser-scoped KQAG session context loads by checking the
    browser request to `<kqag-base-url>/api/session`. Do not copy the real
    session cookie into chat, PR text, screenshots, or docs.

12. For generated quote storage, session listing, and XLSX download coverage
    without private files or live services, run the automated local smoke test
    above. Use safe test data only for any additional manual generation.
13. Confirm no raw launch token, provider token, auth code, OIDC state, nonce,
    provider payload, callback query, database URL, staff email, production
    domain, private local path, or generated customer quote appears in logs,
    browser storage, screenshots, docs, or PR text.

## Browser Launch Shape

The Platform internal shell now uses the browser-safe handoff route:
`POST /api/platform/apps/launch/open?workspaceId=<platform-workspace-id>&appKey=kqag`.
The browser calls Platform only. Platform creates the one-time launch token
server-side, sends it to KQAG only in the `X-App-Launch-Token` header on
`POST <kqag-local-base-url>/api/platform/launch`, relays KQAG's session cookie
for the same browser cookie host, and returns only the safe KQAG launch URL to
the browser.

For local UAT, Platform and KQAG must be visited through the same browser cookie
host, for example `127.0.0.1` on different ports. Mixing `localhost` and
`127.0.0.1` is intentionally rejected by Platform. Cross-host or production
routing remains out of scope for this runbook.

## Pass Criteria

- Platform session context loads after Google sign-in.
- Platform workspace appears.
- KQAG app access appears.
- Platform KQAG launch button reaches KQAG through the server-side handoff.
- KQAG launch consume succeeds.
- KQAG browser-scoped session context loads with Platform workspace context.
- Automated local smoke proves a generated quote session is stored under the
  Platform workspace ID.
- Automated local smoke proves a generated XLSX artifact downloads from the
  quote-session route.
- Raw launch token is not stored after consume and is not present in reports,
  screenshots, logs, URLs, browser storage, database rows, or KQAG responses.
