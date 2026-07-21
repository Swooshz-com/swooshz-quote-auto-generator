# SQAG Platform Integration Contract Audit

This audit records the current contract SQAG expects from Swooshz Platform for
launch, auth, workspace, role, and app-gating context. It is documentation and
evidence only. It does not change runtime behavior, does not prove a live
Platform deployment, and does not claim production readiness. Production remains blocked.

## Evidence Sources

SQAG source reviewed:

- `webapp/server.py`: `configured_platform_launch_mode()`,
  `configured_platform_base_url()`, `consume_platform_launch_token()`,
  `safe_platform_launch_context()`, `user_from_platform_launch_context()`,
  `safe_platform_session_context()`,
  `platform_workspace_id_from_auth_session()`,
  `app_storage_for_auth_session()`, and
  `artifact_storage_for_auth_session()`.
- `docs/platform-launch-mode.md`
- `docs/platform-scoped-storage-mode.md`
- `docs/platform-uat-smoke-runbook.md`
- Existing regression tests in `tests/test_webapp.py` covering platform launch,
  workspace-scoped database storage, database artifacts, protected local-storage
  blocking, tenant-pack no-default behavior, and raw launch-token omission.

Swooshz Platform source previously reviewed before the SQAG namespace cleanup:

- GitHub/local `origin/main` at
  `5bce4d52e4273762375d97149b1d77e5716189b2`.
- `docs/sqag-integration-contract.md`
- `docs/app-access-contract.md`
- `docs/auth-session-security-contract.md`
- `src/http/route-contracts.ts`
- `src/platform/app-launch-token-consume-service.ts`
- `src/http/handlers.ts`
- `tests/app-launch-token-consume-service.test.mjs`
- `tests/app-access-contract.test.mjs`
- `tests/platform-shell.test.mjs`

## Ownership Boundary

Swooshz Platform owns login, users, browser sessions, workspace membership,
roles, app whitelist records, app entitlements, app access decisions, short-lived
launch-token issue and consume, and future billing/credits. SQAG must not
implement those platform responsibilities.

SQAG owns quote-specific workflow state: profile/pricing imports, selected
workspace-owned profile/pricing references, quote basis review, pricing review,
quote-session persistence, generated quote artifacts, and SQAG-side runtime
sessions after a valid Platform launch consume.

Koncept Images profile, pricing, and layout packs are tenant/workspace-imported
SQAG data. They are not app defaults, bundled defaults, global seeds, or
fallback packs. A new workspace starts without a real Koncept pack.

## Required SQAG Configuration Names

SQAG lists environment variable names only. Values must be supplied through the
host secret manager or local UAT process environment and must not be committed.

- `SQAG_PLATFORM_LAUNCH_MODE`
- `SQAG_PLATFORM_BASE_URL`
- `SQAG_PUBLIC_BASE_URL`
- `SQAG_PLATFORM_SERVICE_SECRET`
- `SQAG_PLATFORM_REQUEST_TIMEOUT_SECONDS` (optional, non-secret, bounded)
- `SESSION_SECRET`
- `SQAG_STORAGE_MODE`
- `SQAG_ARTIFACT_STORAGE_MODE`
- `SQAG_DATABASE_URL`

For hosted/protected/deploy readiness, SQAG expects workspace-owned database
app records plus object storage for generated artifact bytes:

- `SQAG_STORAGE_MODE=database`
- `SQAG_ARTIFACT_STORAGE_MODE=object`

Database artifact/BLOB mode is local-UAT/synthetic evidence only and must not
satisfy hosted/protected/deploy or production readiness. Object artifact mode
remains production-blocked until live provider, backup/restore,
retention/delete, deployment, and operations evidence are complete.

## Launch And Consume Contract

SQAG now expects the canonical Platform app key `sqag`. A consumed Platform
payload whose `app.appKey` is anything else is rejected by SQAG and does not
satisfy launch/session validation.

Platform-to-SQAG handoff:

```http
POST <sqag-base-url>/api/platform/launch
X-App-Launch-Token: <one-time-platform-launch-token>
```

SQAG-to-Platform consume:

```http
POST <platform-base-url>/api/platform/apps/launch/consume?appKey=sqag
X-App-Launch-Token: <one-time-platform-launch-token>
```

The raw launch token must stay header-only. It must not appear in URLs, query
parameters, fragments, browser storage, cookies, logs, docs, screenshots,
committed files, telemetry, or public API responses.

SQAG accepts only the safe Platform consume response shape:

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

SQAG rejects missing tokens, consume transport failures, oversized or invalid
JSON responses, non-`consumed` outcomes, app keys other than `sqag`, missing
platform user IDs, missing workspace IDs, unsupported membership roles,
missing/malformed/naive/expired/inconsistently distant launch-token timestamps,
and missing validation grant IDs.

After launch consume, SQAG creates a cryptographically random handle and
registers only `handleHashSha256`, its short mandatory expiry, the grant ID, and
the intended SQAG origin at Platform's service-authenticated finalization
register route. SQAG returns the raw handle only in
`X-SQAG-Finalization-Handle`. The exact configured Platform origin may send it
to `POST /api/auth/platform/finalize`; SQAG consumes it with Platform and only
then sets its host-only cookie. The handle never enters URLs, bodies, DOM,
storage, cookies, logs, or telemetry.

Each authenticated SQAG API request sends the non-secret grant ID and
`{workspaceId, appKey: "sqag"}` to Platform's validation route under
`X-SQAG-Service-Authorization`. Timeout, transport, non-2xx, malformed, invalid,
expired, revoked, disabled-user, membership, entitlement, app, user, or
workspace mismatch all fail closed. Current Platform role replaces the cookie
role for that request, so downgrades are immediate. Logout clears the local
cookie even if best-effort Platform revoke is unavailable.

Production uses only `https://swooshz.com` as the Platform/CORS origin and
`https://quote.swooshz.com` as SQAG's canonical public origin. Deploy Host
validation and finalization binding use `SQAG_PUBLIC_BASE_URL`; the `www`
hostname redirects permanently to the apex and is not accepted as CORS.

## Role And Capability Expectations

Platform is authoritative for app access. After the separate Platform migration,
the Platform contract must allow SQAG launch for `owner`, `admin`, and `member`
when app status and workspace entitlement allow it. Platform should continue to
block `viewer` launch because SQAG has no approved read-only launch mode.

SQAG defensively maps consumed roles as follows:

| Platform membership role | SQAG local role |
| --- | --- |
| `owner` | `admin` |
| `admin` | `admin` |
| `member` | `operator` |
| `operator` | `operator` |
| `viewer` | `viewer` |

Unsupported roles fail closed. If a `viewer` context ever reaches SQAG, SQAG
keeps it as a non-generating viewer role instead of upgrading permissions.

## Workspace Isolation Expectations

SQAG treats `workspace.workspaceId` from the consumed Platform context as the
tenant boundary for database-backed app data. Database/profile/pricing/session
and artifact rows are read through workspace-owned storage helpers.

In database or artifact protected modes:

- Missing Platform workspace context blocks database storage access.
- Workspace A cannot read Workspace B profile, pricing, quote-session, or
  artifact rows.
- Missing workspace-owned profile/pricing/layout data does not fall back to
  local, bundled, or synthetic fixture packs.
- Legacy direct job-file downloads remain disabled in protected
  deploy/database/platform paths.
- Quote-session and artifact routes must use workspace-owned durable storage or
  fail closed with generic errors.

## Existing SQAG Coverage

Existing SQAG regression coverage already exercises the contract without live
Platform credentials:

| Coverage area | Existing tests |
| --- | --- |
| Header-only launch, hashed finalization registration, and host-only session creation | `test_platform_launch_registers_header_only_finalization_then_sets_safe_host_cookie` |
| Missing, wrong-app, failed, expired, and unsupported launch contexts | `test_platform_launch_rejects_*` platform launch tests |
| Role mapping and unsupported-role fail closed behavior | `test_platform_launch_supported_roles_map_to_permissions`, `test_platform_session_with_unsupported_role_does_not_fallback_to_local_permissions` |
| Deploy guard satisfied by complete Platform config | `test_platform_launch_mode_satisfies_deploy_guard_without_oidc` |
| Workspace-scoped profile, pricing, and quote-session DB rows | `test_database_storage_scopes_profiles_pricing_and_sessions_by_platform_workspace` |
| New workspace has no Koncept/synthetic default pack | `test_database_storage_new_workspace_has_no_koncept_or_synthetic_defaults` |
| Profile/pricing/layout workspace-only reads | `test_database_storage_profiles_are_workspace_db_only`, `test_database_storage_pricing_references_are_workspace_db_only`, `test_database_artifact_profile_layout_is_workspace_db_only` |
| Database artifact workspace scoping and raw token omission | `test_database_artifact_storage_saves_workspace_scoped_generated_exports`, `test_database_artifact_storage_does_not_persist_raw_platform_launch_token` |
| Local quote-session and artifact fallback blocked by Platform context | `test_platform_session_context_blocks_local_quote_session_runtime_storage_in_local_app_mode`, `test_platform_session_context_blocks_local_artifact_storage_in_local_app_mode` |
| Synthetic hosted negative launch/generate/session/download/delete smoke | `test_platform_uat_smoke_launch_generate_list_and_download_database_artifact` and `scripts/verify_hosted_smoke.py`; readiness remains blocked until object storage evidence is live |

This PR adds no duplicate runtime behavior tests because the existing tests
already cover the SQAG-side fail-closed and isolation behavior. The new docs
regression test keeps this audit document tied to the expected source evidence
and privacy posture.

## Platform app-key migration complete

This SQAG-side runtime contract accepts `appKey=sqag` only. Platform PR #79
landed the Platform-owned app registry, entitlement, launch/open, consume,
seed, tests, docs, and admin-surface migration to the canonical SQAG app key.
The current Platform `origin/main` contract matches SQAG's adapter assumptions
for `appKey=sqag`.

Hosted Platform-to-SQAG smoke remains pending until an operator runs it against
the deployed Platform and SQAG environments. SQAG source tests cover the
fail-closed behavior for non-`sqag` app keys, and Platform source tests cover
the migrated app-key surfaces; neither is live hosted Platform-to-SQAG
evidence.

The expected post-migration Platform contract remains:

- Platform launch intent and browser-safe open routes require an active browser
  session plus Origin/Referer and CSRF checks.
- Platform keeps raw launch tokens one-time and stores only token hashes plus
  lifecycle metadata.
- Platform sends the raw launch token to SQAG only in the server-side
  `x-app-launch-token` header.
- Platform consume accepts the raw token only in the `x-app-launch-token`
  header, hashes it before lookup, rejects invalid/expired/consumed/revoked or
  app-mismatched tokens safely, re-checks app access, consumes once, and returns
  only safe user/workspace/app/membership context for `appKey=sqag`.
- Platform viewer access for SQAG is blocked because SQAG has no approved
  read-only launch mode.
- Platform stores finalization handles only as hashes, consumes them atomically,
  and keeps their single-use state durable across SQAG restarts/replacements.
- Platform owns grant validation/revocation and rechecks user, membership,
  entitlement, app, workspace, and current role on every SQAG API validation.

## Hosted Readiness Checklist

Before treating a hosted SQAG environment as launch-ready, operators should:

1. Configure SQAG with Platform launch mode, a host-managed session secret,
   database storage, object artifact storage, and a host-managed database URL.
2. Configure Platform SQAG browser handoff with `appKey=sqag` and the SQAG app
   base URL through Platform's own secret/config surface.
3. Verify Platform workspace membership, app entitlement, and role access are
   present for the intended internal workspace.
4. Run SQAG's metadata-only readiness checker and confirm DB/BLOB artifact mode
   remains blocked by `database_blob_artifact_storage_not_launch_ready`.
5. Run a live Platform-to-SQAG smoke using safe synthetic tenant data only.
6. Confirm no raw launch token, OAuth value, cookie, DB URL, callback query,
   private path, generated quote content, or artifact bytes appear in logs,
   screenshots, reports, issues, docs, or PR text.

## Production Gaps

This audit does not prove live Swooshz Platform integration. Production remains
blocked until at least:

- Live Platform-to-SQAG end-to-end evidence is captured against the deployed
  Platform contract.
- Production object storage is fully wired for generated artifacts and uploaded
  reference/profile/pricing assets.
- Real DB+object backup/restore, retention/delete, and rollback evidence exists.
- Production deployment/operations evidence is complete.
- Production observability export and alert delivery are wired and tested.
- Supply-chain and branch-protection hardening are complete.
- Final security and production-readiness audit is complete.
