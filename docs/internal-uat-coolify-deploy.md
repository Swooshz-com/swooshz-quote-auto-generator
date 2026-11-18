# SQAG Hosted Validation Notes

## Temporary internal-alpha authentication

The committed template describes server-only staff password access with an exact
semicolon allowlist, stable identity bindings and one fixed workspace. Configure
actual values only through a separately approved host secret-manager operation.
The single-instance contract is in [staff password access](internal-password-auth-mode.md)
and inference configuration is in [the gateway contract](ai-gateway.md).

## Purpose

This note preserves the SQAG app-specific hosted validation checklist shape for
an already-prepared host. It is metadata-only local guidance only. It does not
deploy anything, configure infrastructure, add secrets, prove live hosting, or
claim hosted/protected/deploy/production readiness.

SQAG is an internal Koncept Images Pte Ltd quote generator module, not
ecommerce or public SaaS. Koncept profile, pricing, and layout packs are
workspace-imported tenant data only. A new workspace starts with no real
Koncept pack until an authorized import or seed action targets that workspace.

## Internal redeployment flow

Every release is rehearsed on a throwaway copy of the app before live gets the
same image. The one-time setup is mapped in the repository, so each run is
create, test, delete, with no new decisions per run.

- **Map:** `deploy/rehearsal/resources.json` lists every rehearsal resource,
  who creates it, where each secret comes from and how it is deleted. It holds
  names and sources only, never a secret value.
- **Rehearsal app:** `deploy/rehearsal/compose.yaml` has the same shape as the
  live internal compose file: one pinned local image, never pulled. It runs no
  storage or other third-party container. Its files go to a rehearsal-only
  Cloudflare R2 bucket that each run empties.
- **Release settings:** `deploy/internal/release-settings.json` records the
  non-secret settings both apps share (modes and AI model, effort and provider
  settings) and every secret by name only. The app never reads this file.
- **Scripts:** `scripts/build_internal_image.sh` (archive and image build),
  `scripts/check_rehearsal_map.py` and `scripts/check_release_settings.py`
  (offline checks), `scripts/clear_rehearsal_bucket.py` (empties the
  rehearsal bucket), `scripts/rehearsal-check.mjs` (checks against the
  rehearsal or live URL) and `scripts/verify_release_identity.py` (identity).
  Until `scripts/rehearsal-check.mjs` is added, run each of its steps below by
  hand in a browser against the rehearsal URL and record the results.

### One-time rehearsal setup

Only these outlive a run. Everything else in the map is created and deleted
by each run.

1. Live's OpenRouter key in the laptop credential store under the name the map
   lists. The owner chose to reuse it rather than keep a separate test key, so
   rehearsal AI calls count against live's key. It sits on the rehearsal app
   for one run only, and `--no-ai` keeps reruns free.
2. The R2 bucket `sqag-rehearsal`, used by nothing else, and an R2 token with
   object read and write on that bucket only. The token and the account's R2
   S3 endpoint sit in the laptop credential store under the entry names the
   map lists.
3. The laptop's Neon access to the `swooshz-sqag` project.
4. The laptop's SSH host alias `swooshz-vps` (Windows OpenSSH) and Coolify
   access to the server and project that host live.
5. The release settings file with no pending values. A null value is filled
   from the owner's approved record of the live value, never by an agent
   reading live.

The owner's standing scope confirmation for this flow lets each run create and
delete the resources the map names as rehearsal, and copy live's migration
ledger rows into the rehearsal branch, without asking again. It never covers
live resources, live env changes, live migrations, new keys or image pruning.
Those keep their own approvals.

### Per-release order

Run the PowerShell blocks on the laptop from the repository folder. Replace
`<candidate commit>` with the full 40-character commit.

1. **Preflight.**
   - CI is green on the exact candidate commit.
   - Record the image id live runs now (the pin on the `deployment/internal`
     branch) and the previous image id. Keep both images on the server.
   - Record that Coolify auto-deploy and preview deployments are off for live.
   - The offline checks pass on the candidate:

     ```powershell
     python scripts\check_rehearsal_map.py
     python scripts\check_release_settings.py --require-complete
     ```

2. **Build.** Make the archive from a clean worktree, never the primary
   checkout, then build on the server. The archive keeps LF line endings and
   records the commit in its header, which the build checks.

   ```powershell
   $sha = "<candidate commit>"
   $short = $sha.Substring(0, 7)
   git fetch origin main
   git worktree add --detach "_tmp\deploy-$short" $sha
   & "$env:ProgramFiles\Git\bin\bash.exe" -c "cd _tmp/deploy-$short && bash scripts/build_internal_image.sh archive --commit $sha --out ../deploy-$short-out"
   scp.exe -o BatchMode=yes "_tmp\deploy-$short-out\sqag-$short.tar" "swooshz-vps:/root/sqag-build-$short.tar"
   scp.exe -o BatchMode=yes "_tmp\deploy-$short\scripts\build_internal_image.sh" "swooshz-vps:/root/sqag-build-$short.sh"
   $tarSha = "<tar_sha256 from the archive record>"
   ssh.exe swooshz-vps "nohup bash /root/sqag-build-$short.sh build --commit $sha --tar /root/sqag-build-$short.tar --tar-sha256 $tarSha > /root/sqag-build-$short.json 2> /root/sqag-build-$short.log &"
   ```

   The build takes about five minutes. Then read the record and require
   `"result":"pass"`. It holds the image id, the four font checks, the Python
   version, the server import and the image's env names.

   ```powershell
   ssh.exe swooshz-vps "cat /root/sqag-build-$short.json" | Set-Content -Encoding utf8 "_tmp\deploy-$short-out\build-record.json"
   $build = Get-Content "_tmp\deploy-$short-out\build-record.json" -Raw | ConvertFrom-Json
   $build.result
   $build.image_env_names | Set-Content -Encoding ascii "_tmp\deploy-$short-out\image-env-names.txt"
   ```

3. **Create the rehearsal from the map.**
   - Create the Neon schema-only branch `rehearsal-<short>` of live's default
     branch in `swooshz-sqag`. A schema-only branch copies tables but no rows,
     so its migration ledger is empty and the migration preflight would refuse
     it. Right after creating it, read live's
     `public.sqag_schema_migrations` rows (`sequence_no`, `migration_id`,
     `checksum_sha256`, `applied_at`; nothing else) and insert the same rows
     into the branch's table. Then reset the branch roles' passwords so no
     live password is used.
   - Run the read-only preflight on the branch with the branch's migrator URL
     (see [the migration runbook](postgres-migration-runbook.md)). It must
     report ready, with live's applied IDs and only the candidate's new
     migrations pending.
   - In Coolify, create the Docker Compose service `quote-rehearsal` on the
     same server and project as live and paste `deploy/rehearsal/compose.yaml`.
     Set each variable from the source the map gives it: shared settings from
     `python scripts\check_release_settings.py --print-shared-env`, generated
     secrets, the branch's runtime URL, the `sqag-rehearsal` key and the run
     values. Set `REHEARSAL_IMAGE_ID` to the image live runs now. Give it the
     host `quote-rehearsal.<server-ip>.sslip.io` with Traefik basic auth.
   - Start the app and wait for health. Then check the bucket is empty from
     inside the rehearsal container with the map's `clear_command`, using
     `--mode check`. If a failed run left files, run it with `--mode clear`
     first. The script refuses any bucket or workspace that is not the
     rehearsal's, so it cannot touch live.

4. **Seed on the current image.** Run `rehearsal-check --profile seed`. It
   creates the fixed synthetic set and saves the seed ids for later steps.

5. **Migrate the rehearsal database** if the candidate has pending migrations:
   pre-apply check, one apply, post-apply check, all on the branch with its
   migrator URL. Then run `rehearsal-check --profile reopen` on the current
   image to prove it still serves the seed on the new schema.

6. **Upgrade.** Set `REHEARSAL_IMAGE_ID` to the candidate image id, redeploy
   and run `rehearsal-check --profile verify`. It signs in, reopens every seed
   item, makes a new quote from test images with image analysis, downloads and
   validates the XLSX and PDF, checks the PDF fonts and captures the served
   `app.js` hash. `--no-ai` skips the paid calls on a rerun.

7. **Rollback drill.** Set `REHEARSAL_IMAGE_ID` back to the current image,
   redeploy and run `rehearsal-check --profile reopen`. Then switch forward to
   the candidate again and run `--profile reopen` once more. Any failed check
   holds promotion.

8. **Promote after the owner approves the tested image id.**
   - Live migrations first, if any, in the runbook order and with their own
     approval.
   - Compare live's env names with the settings file without reading values.
     On the server, list the names only and drop the values before they leave
     the server, then check them with the image's names (from the build
     record) ignored:

     ```powershell
     ssh.exe swooshz-vps "docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' <live container> | cut -d= -f1" | Set-Content -Encoding ascii "_tmp\deploy-$short-out\live-env-names.txt"
     python scripts\check_release_settings.py --env-names "_tmp\deploy-$short-out\live-env-names.txt" --ignore-names "_tmp\deploy-$short-out\image-env-names.txt"
     ```

     Fix a missing or unexpected name only with the owner's approval for that
     env change.
   - Commit the one-line image pin and its source comment on the
     `deployment/internal` branch and deploy in Coolify.
   - Run `rehearsal-check --profile live-readonly` against live. It signs in,
     loads the quote list and captures the served `app.js`, and creates
     nothing. Then run `scripts/verify_release_identity.py` with the expected
     source commit, image id and the hash the rehearsal captured.
   - If any live check fails, pin the previous image id and deploy again.

9. **Delete and record.**
   - Empty the rehearsal bucket with the map's `clear_command` (`--mode
     clear`, which reports zero remaining), then delete the `quote-rehearsal`
     service in Coolify and the `rehearsal-<short>` Neon branch. The bucket
     itself stays. Never delete anything whose name does not say rehearsal.
   - Drop the run's generated secrets.
   - Store the build record, the preflight outputs and every check result in
     the evidence store, then remove the worktree with `git worktree remove`.
   - Image retention: keep the current, previous and candidate images. Removing
     any other image or old container is a separate owner-approved server
     action.

## Hosted Object Artifact Posture

The hosted internal-alpha deploy scaffold uses database metadata plus object
storage for generated artifact bytes:

- `APP_MODE=deploy`.
- `SQAG_STORAGE_MODE=database`.
- `SQAG_ARTIFACT_STORAGE_MODE=object`.
- `SQAG_DATABASE_URL` is configured only through the host secret manager.
- The canonical `SQAG_OBJECT_STORAGE_*` names are configured only through the
  host secret manager.
- Platform/workspace launch context is required for protected hosted use in
  Platform mode; internal password access uses its fixed configured workspace instead.
- `SQAG_TRUSTED_PROXY_CIDRS` is configured in the host environment manager with
  only the exact Coolify/Traefik proxy network CIDRs that connect directly to
  SQAG. A trust-all network is not permitted.
- The object-storage credential must permit the read-only bucket probe used by
  deploy startup readiness, plus runtime read, write, and delete operations.
  Public `/api/health` is process-only liveness and does not call object
  storage.
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
- Exact ordered workbook-PDF/font packages in `[phases.setup].nixPkgs`:
  `["...", "libreoffice", "fontconfig", "carlito", "liberation_ttf"]`.
  `libreoffice` provides the existing workbook-mode `soffice` converter;
  `fontconfig` provides runtime discovery including `fc-match`; `carlito`
  supplies Calibri-compatible regular/bold faces and `30-calibri.conf`;
  `liberation_ttf` resolves to the pinned `liberation_ttf_v2` Arial-compatible
  regular/bold family. The `...` entry preserves Python provider packages.
  Missing entries, wrong order, duplicates, malformed arrays and alternate,
  misplaced or Apt-substituted converter/font bindings fail closed.
- Locked `[phases.setup].cmds` preserves the provider setup hole and creates
  `/etc/fonts/fonts.conf` during the image build. It registers the packaged
  `/root/.nix-profile/share/fonts` directory, includes the pinned fontconfig
  package's standard substitution rules and Carlito's `30-calibri.conf`, maps
  Arial to Liberation Sans and builds the cache. The packaged rule directory
  must resolve to exactly one path; an absent or ambiguous path fails the
  build. These rules preserve the generic sans/serif/monospace substitutions.
  Nixpacks otherwise has no default fontconfig file and can fall back to
  DejaVu despite those fonts being installed. Registration is part of the
  source build; it requires no runtime environment override or host changes.
- Internal-alpha PDF is a required second export path. An explicit PDF request
  regenerates a fresh XLSX from the current reviewed quote state, converts that
  workbook with the installed LibreOffice/soffice path, persists and
  session-associates the PDF, then permits the authorized PDF download. XLSX
  remains the customer-ready master/default export; repository checks do not
  prove live hosted PDF success.
- The root `package.json` is preserved for local Playwright scripts, CI
  smokes and `npm audit`; it must not cause Nixpacks to select Node.

Validate the contract before deployment:

```powershell
python scripts\validate_nixpacks_python_contract.py
python -m unittest tests.test_nixpacks_python_contract
```

CI uses Ubuntu Apt packages `libreoffice-calc`, `fontconfig`,
`fonts-crosextra-carlito` and `fonts-liberation`. The deployed pinned Nix
attributes are respectively `libreoffice`, `fontconfig`, `carlito` and
`liberation_ttf`; the package-manager names are not interchangeable.

Before accepting a font-runtime packaging change, build a fresh disposable
image through the normal Nixpacks build with this archive. Do not mount host
fonts, install packages after build or modify the built image manually. Inside
that same image, require `soffice` and `fc-match`, then verify family/style:

| Request | Required compatible family/style |
|---|---|
| `fc-match "Calibri"` | Carlito / Regular |
| `fc-match "Calibri:style=Bold"` | Carlito / Bold |
| `fc-match "Arial"` | Liberation Sans / Regular |
| `fc-match "Arial:style=Bold"` | Liberation Sans / Bold |

The actual generated-workbook PDF must also embed the Carlito-resolved regular
and bold faces; font discovery alone is insufficient. Keep the XLSX's Calibri
font requests, render the synthetic multi-page workbook twice, and verify bold
Quantity/Service/Estimate headings on page 1 and a continuation page, complete
logos, page-1-only right header text, final signature clearance, continuation
content, expected page count and no trailing blank page. Equivalent rendered
layout/font behavior is required; metadata-dependent PDF byte identity is not.
Do not accept DejaVu Sans fallback or a regular/book face for bold text.

This source-controlled packaging contract does not authorize a live host font
installation, Coolify configuration/environment change or deployment. Hosted
Alpha acceptance remains separate from disposable build/render evidence.

## Host Boundary

This repo owns only the app-specific shape:

- Build provider contract: `nixpacks.toml` and `.python-version`.
- Start command: `python webapp/server.py`.
- Health path: `/api/health` is process-only liveness and returns HTTP 200 with
  `{"status":"ok"}` without probing dependencies. Deploy startup performs
  one internal forced dependency-readiness probe before constructing the
  listener and refuses to start when that probe is blocked; readiness is not a
  public endpoint.
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
| Temporary staff password access | `SQAG_AUTH_MODE=internal_password`, `SQAG_PLATFORM_LAUNCH_MODE=disabled`, `SQAG_PUBLIC_BASE_URL`, `SQAG_INTERNAL_WORKSPACE_ID`, operator and admin settings documented in [Internal staff password access](internal-password-auth-mode.md) |
| Reverse proxy | `SQAG_TRUSTED_PROXY_CIDRS` |
| Runtime housekeeping | `QUOTE_DATA_ROOT`, `QUOTE_OUTPUT_ROOT`, `QUOTE_TMP_ROOT`, `QUOTE_LOG_ROOT`, `PORT` |

Password mode ignores retired Google settings and rejects mixed OIDC or Platform
configuration. Keep the workspace and existing user IDs for continuity. Set
staff roles in the active password-mode email lists. Actual credentials and identity-continuity
qualification remain separate host operations, not repository validation. See
[internal staff password access](internal-password-auth-mode.md) for the exact
admission and session contract. The local OIDC adapter is retired. The independent
Platform adapter retains its own contract; it is not a password-mode requirement
or fallback authentication.

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
- `/api/health` returns process-only `{"status":"ok"}` JSON and HTTP 200.
- Deploy startup's internal readiness probe confirms the database schema,
  object-artifact metadata schema, and object bucket before the listener is
  constructed.
- After accepted Platform launch or finalization admission, the app may start
  one short-lived advisory runtime-database warm attempt using only the
  configured runtime database role and `SELECT 1`; it cannot satisfy readiness
  or change auth/session outcomes.
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
- Internal-alpha hosted PDF validation must exercise the explicit PDF request
  flow: regenerate the current reviewed quote state to a fresh
  `quotation.xlsx`, convert that workbook with the installed
  `libreoffice`/`soffice` dependency, persist and session-associate the PDF, and
  verify authorized PDF download. XLSX remains the master/default export;
  repository CI does not establish live PDF success.
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
