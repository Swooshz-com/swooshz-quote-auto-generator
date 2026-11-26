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
- **Live-data check:** step 8 tests the candidate on a copy of live data
  before live switches (Pass A: `deploy/rehearsal/compose-live-copy.yaml`,
  `scripts/copy_live_objects_to_rehearsal.py`), then walks live read-only
  after the switch (Pass B). Both walk the quotes in
  `deploy/rehearsal/live-cases.json` (opaque quote ids the owner approves)
  plus the operator's newest quotes, reopen each one, check its files are
  unchanged and check every block, the booth box and the quantity boxes.
  Pass A also runs the Quote Basis click probe on the largest of those quotes.
  Between the two, the agent and then the owner test the candidate on the copy
  (UAT), and live waits for the owner's UAT pass. If Pass B fails,
  `scripts/live_rollback.py` pins and deploys the previous image. Paid AI on a
  run is capped at US$1 (`scripts/lib/ai-budget.mjs`) and never starts without
  `--ai-ok`.
- **Production-branch guard:** `scripts/neon_branch_guard.py` checks a database
  URL before anything writes to a rehearsal database. It refuses live's
  default-branch endpoint listed in the map's `production_guard` and its
  per-compute hosts (`ep-<id>-<suffix>`, with or without `-pooler`), a branch
  Neon marks as default or one whose name does not start with `rehearsal-`,
  and any host it cannot place. It prints the host and branch name only.
  It asks Neon's API with the key `NEONDB_SWOOSHZ_SQAG_API_KEY` from the
  credential store; that key is scoped to the one project, so the guard reads
  the project by the map's `neon_project_id`. After it passes,
  `scripts/rehearsal_database_url.py` is the only way a rehearsal step uses a
  database URL, except that `scripts/copy_live_objects_to_rehearsal.py` runs the
  same guard, then uses the copy branch's URL itself. `set-service` writes a
  rehearsal service's `SQAG_DATABASE_URL` through Coolify's API and `run-migrator` runs each migration step. Both run
  the guard again on the exact URL they use and refuse one that is not on the
  guarded branch's endpoint. They, and the live object copy, refuse a guard
  result that Neon's API did not confirm (`neon_api: not_checked`, the key
  not set). Writing checks on
  a rehearsal host, and every check on the copy of live, refuse to start
  without `set-service`'s record for that service (`--db-guard`).

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
6. The laptop's tools for `scripts/rehearsal-check.mjs`: `npm ci`,
   `npx playwright install chromium` and the Python packages in
   `requirements.txt` (it validates the downloaded PDF with them).
7. The operator's own live sign-in in the laptop credential store under
   `SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL` and
   `SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD`. The owner adds them once with
   `toolkit env edit`. Only the live read-only check reads them, through
   `toolkit env run`, so the values never pass through a chat or a command
   line, and the rehearsal's synthetic logins never reach live.
8. An R2 token with Object Read only on live's bucket, in the laptop
   credential store under the `live-r2-readonly` entry names in the map. Only
   the copy script reads it. The owner creates it.
9. The quote manifest `deploy/rehearsal/live-cases.json` approved by the
   owner: its `approval` names the date and the issue comment or commit that
   approved the listed quote ids. A walk refuses an unapproved manifest.
10. The owner's recorded OK for paid AI (`--ai-ok`) naming its release, runs
    and cap; one ledger file shared by every check caps each run at US$1.

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
   ssh.exe swooshz-vps "rm -f /root/sqag-build-$short.exit"
   ssh.exe swooshz-vps "nohup bash -c 'bash /root/sqag-build-$short.sh build --commit $sha --tar /root/sqag-build-$short.tar --tar-sha256 $tarSha > /root/sqag-build-$short.json 2> /root/sqag-build-$short.log; echo `$? > /root/sqag-build-$short.exit' < /dev/null > /dev/null 2>&1 &"
   ssh.exe swooshz-vps "until [ -e /root/sqag-build-$short.exit ]; do sleep 15; done; cat /root/sqag-build-$short.exit"
   ```

   The build takes about five minutes. The wait prints the build's exit code
   once the record is complete; require `0`. Then read the record and require
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
     it. Right after creating it, run the production-branch guard below on the
     branch's URL. Then read live's
     `public.sqag_schema_migrations` rows (`sequence_no`, `migration_id`,
     `checksum_sha256`, `applied_at`; nothing else) and insert the same rows
     into the branch's table.
   - A schema-only branch also leaves every `sqag_` table and function owned
     by `neondb_owner` instead of `sqag_migrator`, and readiness refuses that.
     On the branch only, run `GRANT sqag_migrator TO neondb_owner`, then
     `ALTER TABLE ... OWNER TO sqag_migrator` for each `sqag_` table and
     `ALTER FUNCTION ... OWNER TO sqag_migrator` for each `sqag_` function,
     then `REVOKE sqag_migrator FROM neondb_owner`. Other grants already match
     live.
   - **Production-branch guard**, before the first write to the branch. Hold
     the branch's URL in this session only, never in a file or the chat. The
     guard checks the branch with Neon's API when `NEONDB_SWOOSHZ_SQAG_API_KEY`
     is set (through `toolkit env run`, under that credential-store name);
     without it, it checks the endpoint only and records
     `neon_api: not_checked`, which every writing step below refuses. Hold
     the branch's plain endpoint host, `ep-<id>` or `ep-<id>-pooler` as Neon's
     console shows it, never a per-compute host (`ep-<id>-<suffix>`): Neon's
     API does not know those, so the guard refuses them as unreachable.
     Require exit 0 and the printed branch name `rehearsal-<short>`:

     ```powershell
     $out = "_tmp\deploy-$short-out"
     $env:SQAG_GUARD_DATABASE_URL = <the rehearsal-<short> branch's URL>
     node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names NEONDB_SWOOSHZ_SQAG_API_KEY -- python scripts\neon_branch_guard.py --database-url-env SQAG_GUARD_DATABASE_URL --result "$out\db-guard.json"
     Remove-Item Env:SQAG_GUARD_DATABASE_URL
     ```

     The result (under 12 hours old) is the `--guard-result` of every
     `scripts/rehearsal_database_url.py` step on this branch. Run the guard
     again if the branch's URL changes.
   - Before the role password reset, check that the migrator URL you hold is
     the guarded branch's, with the guard only (`--step check`). Then reset
     the roles' passwords on the branch the result names: its `branch_id`,
     recorded when the guard checked Neon's API (`--step check` refuses a
     result without it). Never reset a
     password on the default branch. Then set the migrator URL with the new
     password and run the read-only preflight through the same script (see
     [the migration runbook](postgres-migration-runbook.md)). It must report
     ready, with live's applied IDs and only the candidate's new migrations
     pending:

     ```powershell
     $env:SQAG_MIGRATOR_DATABASE_URL = <the branch's migrator URL>
     node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names NEONDB_SWOOSHZ_SQAG_API_KEY -- python scripts\rehearsal_database_url.py run-migrator --guard-result "$out\db-guard.json" --step check --result "$out\migrator-check.json"
     # reset the roles' passwords on the result's branch_id, then hold the migrator URL with its new password
     node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names NEONDB_SWOOSHZ_SQAG_API_KEY -- python scripts\rehearsal_database_url.py run-migrator --guard-result "$out\db-guard.json" --step pre-apply --result "$out\migrator-pre-apply.json"
     ```

     Each step exits 2 and runs nothing when a URL it holds is not on the
     guarded branch's endpoint.
   - In Coolify, create the Docker Compose service `quote-rehearsal` on the
     same server and project as live and paste `deploy/rehearsal/compose.yaml`.
     Set each variable from the source the map gives it: shared settings from
     the `shared` values in `deploy/internal/release-settings.json`, generated
     secrets, the credential-store entries the map names (the R2 token, the R2
     endpoint and the AI key) and the run values. Set `REHEARSAL_IMAGE_ID` to
     the image live runs now. Give it the host
     `quote-rehearsal.<server-ip>.sslip.io`. Coolify services have no basic
     auth, so access control is the app's own sign-in with the run's test-only
     logins.
   - Never paste the database URL into Coolify. `set-service` writes the
     service's `SQAG_DATABASE_URL` through Coolify's API from the runtime URL
     you hold in this session, after it runs the guard again on that exact
     value. It reads only the service's name (it must be a `coolify_service`
     in the map), never its environment values, and its record is the
     `--db-guard` file of every check below. Run it again whenever the URL
     changes:

     ```powershell
     $env:SQAG_REHEARSAL_RUNTIME_DATABASE_URL = <the branch's runtime URL, after the password reset>
     node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names NEONDB_SWOOSHZ_SQAG_API_KEY,COOLIFY_SWOOSHZ_API_URL,COOLIFY_SWOOSHZ_API_TOKEN -- python scripts\rehearsal_database_url.py set-service --guard-result "$out\db-guard.json" --database-url-env SQAG_REHEARSAL_RUNTIME_DATABASE_URL --service-uuid <quote-rehearsal service uuid> --result "$out\db-url-set.json"
     Remove-Item Env:SQAG_REHEARSAL_RUNTIME_DATABASE_URL
     ```
   - Apply every compose or variable change, and every image swap, with
     Coolify's deploy API for the service: `POST /api/v1/deploy?uuid=<service
     uuid>`. A service restart does not re-render the compose, so it keeps the
     old image and values. "Redeploy" below always means this call.
   - Derive `SQAG_TRUSTED_PROXY_CIDRS` from the rehearsal service's own
     network, never a fixed value. The proxy joins that network, so a
     request reaches the app from there and not from the shared `coolify`
     network. Set every other value first, then deploy once so the network
     exists: with the value empty the app exits at start ("auth boundary",
     exit 2) and restarts in a loop, which is expected here and never a pass.
     List the container's networks, read the subnet of the service's own
     network, set it as the value and redeploy at once. It must be one exact
     subnet, never a trust-all range.

     ```powershell
     ssh.exe swooshz-vps 'docker inspect --format ''{{range $k, $v := .NetworkSettings.Networks}}{{println $k}}{{end}}'' <rehearsal app container>'
     ssh.exe swooshz-vps 'docker network inspect --format ''{{range .IPAM.Config}}{{println .Subnet}}{{end}}'' <rehearsal service network>'
     ```

   - Wait for health. Then check the bucket is empty from inside the
     rehearsal container. The image under test may predate the clear script,
     so first run the map's `clear_setup` on the server: it copies the
     reviewed script and map from the candidate's build folder into the
     container's `/tmp/rehearsal-tools`. Then run the map's `clear_command`
     with `--mode check`. If a failed run left files, run it with
     `--mode clear` first. A redeploy replaces the container, so run
     `clear_setup` again before any later check or clear. The script refuses
     any bucket or workspace that is not the rehearsal's, so it cannot touch
     live.

4. **Seed on the current image.** `scripts/rehearsal-check.mjs` reads its
   credentials from the environment only and refuses them as arguments. For
   the rehearsal, sign in as the compose file's synthetic admin alias with the
   run's generated admin password. Set them in the same PowerShell session from the run's generated values, never by
   typing them into the command. Writing profiles refuse any host that does
   not say rehearsal and any host in `SQAG_REHEARSAL_LIVE_HOSTS`.

   ```powershell
   $env:SQAG_REHEARSAL_LOGIN_EMAIL = "rehearsal-admin@example.com"
   $env:SQAG_REHEARSAL_LOGIN_PASSWORD = <the run's generated admin password>
   $env:SQAG_REHEARSAL_LIVE_HOSTS = "<live host>"
   $rehearsal = "https://quote-rehearsal.<server-ip>.sslip.io"
   $out = "_tmp\deploy-$short-out"
   $ledger = "$out\ai-ledger.json"
   $guard = "$out\db-url-set.json"
   node scripts\rehearsal-check.mjs --profile seed --base-url $rehearsal --result "$out\seed.json" --ai-ok --ai-ledger $ledger --db-guard $guard
   ```

   It saves the synthetic pricing reference (waiting up to 120 seconds for
   the save) and a synthetic company profile, makes a quote to XLSX and PDF
   and a saved draft, and records their ids and file hashes in `seed.json`.
   A rerun reuses the saved company profile. Saving the pricing reference
   makes one small AI call when the app has an AI key; the ledger counts it
   as US$0.10 because the app does not report its cost. A damaged pricing reference has no app route, so its fail-closed
   message stays covered by the unit tests only. Each later profile writes its
   own result file; a failed step holds promotion.

5. **Migrate the rehearsal database** if the candidate has pending migrations:
   pre-apply check, one apply, post-apply check, all on the branch and each
   through `scripts/rehearsal_database_url.py run-migrator`. Hold the
   branch's migrator, runtime and maintenance URLs in this session only; each
   step guards every one it holds (post-apply needs all three) and runs
   nothing if one is not on the guarded branch's endpoint:

   ```powershell
   foreach ($step in "pre-apply", "apply", "post-apply") {
     node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names NEONDB_SWOOSHZ_SQAG_API_KEY -- python scripts\rehearsal_database_url.py run-migrator --guard-result "$out\db-guard.json" --step $step --result "$out\migrator-$step.json"
     if ($LASTEXITCODE -ne 0) { break }
   }
   ```

   Then run the reopen profile on the current image to prove it still serves
   the seed on the new schema:
   `node scripts\rehearsal-check.mjs --profile reopen --base-url $rehearsal --seed-file "$out\seed.json" --result "$out\reopen-migrated.json" --db-guard $guard`.

6. **Upgrade.** Set `REHEARSAL_IMAGE_ID` to the candidate image id, redeploy
   and run
   `node scripts\rehearsal-check.mjs --profile verify --base-url $rehearsal --seed-file "$out\seed.json" --result "$out\verify.json" --asset-out "$out\rehearsal-app.js" --ai-ok --ai-ledger $ledger --db-guard $guard`.
   It signs in, reopens every seed item and checks their files are unchanged,
   makes a new quote to XLSX and PDF, validates both, checks the PDF uses only
   the release fonts (Carlito and Liberation Sans), runs AI image analysis on
   a test image and captures the served `app.js`. It refuses to start the
   analysis if the run could pass the cap, and an analysis whose cost the app
   does not report stops every later paid call. `--no-ai` (without the AI
   flags) skips the paid call on a rerun.

7. **Rollback drill.** Set `REHEARSAL_IMAGE_ID` back to the current image,
   redeploy and run the reopen profile with `--db-guard $guard` (result
   `reopen-rollback.json`). Then
   switch forward to the candidate again and run it once more (result
   `reopen-forward.json`). Any failed check holds promotion.

8. **Promote after the owner approves the tested image id.** Five parts, in
   order. Live changes only in part 3, and only after the owner's UAT pass.

   **8.1 Pass A: the candidate on a copy of live data**, before anything on
   live changes. Every write goes to the copy.
   - Create the Neon branch `rehearsal-copy-<short>` of live's default branch
     with data, as the map's `neon-data-branch` says, and record the time.
     Before anything writes to it, run the production-branch guard (step 3)
     on its URL, with the branch's plain endpoint host as in step 3, and the
     result `"$out\db-guard-copy.json"`. Require exit 0 and the branch name
     `rehearsal-copy-<short>`. Run the guard again if the URL changes.
   - As in step 3, with `--guard-result "$out\db-guard-copy.json"` and
     result files named `copy-migrator-<step>.json`: run `run-migrator --step
     check` on the migrator URL you hold, reset the roles' passwords on the
     result's `branch_id` only, then apply the candidate's pending migrations
     with `run-migrator` as in step 5.
   - On the branch, read the one `workspace_id` in
     `public.sqag_quote_sessions` (an opaque id; read nothing else).
   - Generate this run's copy password straight into the laptop credential
     store as `SWOOSHZ_SQAG_REHEARSAL_UAT_LOGIN_PASSWORD` with `toolkit env`.
     It is the copy's sign-in for the checks and for the UAT in 8.2, never
     the owner's live password, and never pasted into the chat.
   - In Coolify, create the service `quote-rehearsal-copy` from
     `deploy/rehearsal/compose-live-copy.yaml` with the host
     `quote-rehearsal-copy.<server-ip>.sslip.io`. Set its values as for
     `quote-rehearsal` in step 3, with `REHEARSAL_IMAGE_ID` the candidate,
     `SWOOSHZ_SQAG_REHEARSAL_UAT_LOGIN_PASSWORD` as the admin password and the
     map's `live_copy` run values: the workspace id, the owner's own sign-in
     email (twice) and `[]` for the identity bindings, or the one row the
     owner supplies. Set its `SQAG_DATABASE_URL` only with `set-service` as in
     step 3, from the copy branch's runtime URL, with
     `--guard-result "$out\db-guard-copy.json"`, the copy service's uuid and
     `--result "$out\db-url-set-copy.json"`. Derive the proxy subnet as in
     step 3 (the first deploy restarts in a loop until it is set) and wait
     for health.
   - Then six parts, in this order: list the copy ids, plan the copy, copy,
     list the candidates, the owner approves the manifest, walk (Pass A).
     Opening a quote loads its uploaded files, and on the fresh copy those
     are only in live's bucket until the copy runs, so the candidates come
     after the copy.
   - **List the copy ids.** On a host that says rehearsal the walk signs in
     with the rehearsal names, so live's password never reaches the copy:

     ```powershell
     $copy = "https://quote-rehearsal-copy.<server-ip>.sslip.io"
     $copyGuard = "$out\db-url-set-copy.json"
     $env:SQAG_REHEARSAL_LOGIN_EMAIL = "<the owner's own sign-in email>"
     $env:SQAG_REHEARSAL_LOGIN_PASSWORD = <SWOOSHZ_SQAG_REHEARSAL_UAT_LOGIN_PASSWORD from the credential store>
     node scripts\rehearsal-check.mjs --profile live-readonly --base-url $copy --manifest deploy\rehearsal\live-cases.json --targets-out "$out\walk-targets.json" --targets-only --result "$out\copy-ids.json" --db-guard $copyGuard
     ```

     From the quote list alone, before it opens any quote, it writes
     `walk-targets.json`: the `quote_ids` of the manifest quotes and every
     other quote the login owns, newest first, at most 50 (the step `copy ids
     file` counts any owned quotes past that). Then it opens the listed
     quotes for their candidate reasons, which fails on this first run
     (`Quote readback returned HTTP 503`: the files are not copied yet). The
     result says so and the ids file stays: that is this part's expected
     outcome. A failure of any other kind, or in a step before `targets file`,
     holds the run.
   - **Plan the copy, then copy** those quotes' files from live's bucket into
     the rehearsal bucket. The copy branch's URL is a per-run value set in
     this session only; the bucket tokens come from the credential store.
     The script runs the production-branch guard on that URL before any
     call, reads live with the read-only token, writes only to
     `sqag-rehearsal` and reports counts only. It copies what the walk reads
     for each listed quote: its uploaded files, its published quote files and
     the layout of the company profile it names (`rows_by_owner_type` counts
     each kind); pricing reference files are not needed:

     ```powershell
     $env:SQAG_LIVE_COPY_DATABASE_URL = <the copy branch's runtime URL>
     $r2 = "NEONDB_SWOOSHZ_SQAG_API_KEY,SWOOSHZ_SQAG_LIVE_R2_READONLY_ENDPOINT_URL,SWOOSHZ_SQAG_LIVE_R2_READONLY_ACCESS_KEY_ID,SWOOSHZ_SQAG_LIVE_R2_READONLY_SECRET_ACCESS_KEY,SWOOSHZ_SQAG_REHEARSAL_R2_ENDPOINT_URL,SWOOSHZ_SQAG_REHEARSAL_R2_ACCESS_KEY_ID,SWOOSHZ_SQAG_REHEARSAL_R2_SECRET_ACCESS_KEY"
     node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names $r2 -- python scripts\copy_live_objects_to_rehearsal.py --targets "$out\walk-targets.json" --source-bucket <live bucket> --expect-dest-bucket sqag-rehearsal --mode plan --confirm-live-read --report "$out\copy-plan.json"
     node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names $r2 -- python scripts\copy_live_objects_to_rehearsal.py --targets "$out\walk-targets.json" --source-bucket <live bucket> --expect-dest-bucket sqag-rehearsal --mode copy --confirm-live-read --report "$out\copy.json"
     Remove-Item Env:SQAG_LIVE_COPY_DATABASE_URL
     ```

   - **List the candidates.** Run the same listing command again, with the
     result `"$out\copy-targets.json"`. It must pass. It writes the same
     `quote_ids` and now the `candidates` list (reason codes, section and
     line counts of at most 30 listed quotes), which is what the owner picks
     manifest cases from. `walk-targets.json` holds quote ids and counts only.
   - **The owner approves the manifest**: the cases they pick from the
     candidates go into `deploy/rehearsal/live-cases.json` with its
     `approval` (prerequisite 9). Every manifest quote must be in
     `walk-targets.json`'s `quote_ids`, so its files were copied; if one is
     not, list the copy ids again and copy again before the walk.
   - **Walk the copy (Pass A)** before anything writes to it, then run the
     writing checks on it under the same ledger:

     ```powershell
     node scripts\rehearsal-check.mjs --profile live-readonly --base-url $copy --manifest deploy\rehearsal\live-cases.json --targets-out "$out\pass-a-targets.json" --download-files --click-budget --result "$out\pass-a.json" --asset-out "$out\copy-app.js" --db-guard $copyGuard
     node scripts\rehearsal-check.mjs --profile seed --base-url $copy --result "$out\copy-seed.json" --ai-ok --ai-ledger $ledger --db-guard $copyGuard
     node scripts\rehearsal-check.mjs --profile verify --base-url $copy --seed-file "$out\copy-seed.json" --result "$out\copy-verify.json" --ai-ok --ai-ledger $ledger --db-guard $copyGuard
     ```

     The walk reopens every manifest quote and the newest quotes the login
     owns. It downloads each quote's current XLSX and PDF and checks them
     against their recorded hashes; a quote with no current PDF (Excel-only,
     the default) is recorded, not failed. It checks that every block has its
     own tick and X, the booth box is filled and every line has a quantity
     box, and fails if anything tried to write. `--click-budget` then runs
     the Quote Basis click probe (the CI click-budget job's) on the two
     largest manifest quotes the login can edit, in a separate browser that
     answers every save itself and counts it, so nothing reaches the copy. A
     quote made by another staff member opens view only for the login: the
     walk reads and downloads it, records `view_only` in its step and does not
     probe it (step "click budget leaves view-only quotes"). The copy runs with
     its own generated `SESSION_SECRET`, so the browser recovery scope saved
     in every quote's Customer checkpoint differs there and Quote Basis opens
     locked ("Click Next in Customer..."), as it would for a user of the
     copy. The probe then does what that user does: it presses Next in
     Customer (that save is answered in the probe browser too) and records
     the lock in the step's `basis_lock`: which checkpoint fields differed
     (names only; `copy_only` is true when only `recoveryScope` did) and how
     it was unlocked, with `next_saves`, the saves the Next press alone
     made. Any other lock fails the probe with those facts, without pressing
     Next: another checkpoint field that also differs, a missing checkpoint,
     a pending pricing-reference change, or a Next that would ask to confirm
     changed inputs. A lock that Next does not clear fails it after the press,
     with the facts after it (`after_next`). Such a lock would show on
     live too, so that failure goes to the controller as a hold. Its exact counts
     (browser storage writes, IndexedDB puts, renders, rebuilt rows, bytes
     copied, saves per burst) fail the walk; its timings are recorded only,
     because the laptop's speed varies. The byte budgets come from the sizes
     the step records: a click may serialise the quote's browser copy once
     (on a reopened quote that copy also holds the restore key, so it is
     about twice the draft) and a save the draft six times and the copy once,
     so a second copy fails. Those sizes have limits of their own: the draft at
     most 1.75 times the quote as the server holds it (`saved_quote_bytes`),
     the copy and the restore key that draft once each, the workspace view 4
     KiB. A quote save must be sent within 1.5 s of the last click; only its
     answer may be later. A click on a line already in that state is
     recorded in `clicks_with_nothing_to_change`; one that changes nothing
     although a line was not in that state fails. Both quotes are probed even
     when the first fails, and a failed step keeps all its facts in its
     `detail` (sizes, budgets, lock, samples, every failure in `failures`);
     its `error` is cut. Pass A passes only if all three
     checks pass, `pass-a-targets.json` lists the same ids as
     `walk-targets.json`, in any order, and `copy-app.js` has the same hash as
     `rehearsal-app.js`. Any failure holds promotion. Keep the copy up for
     8.2.

   **8.2 UAT on the copy, before live: the agent first, then the owner.**
   The copy stays up on the candidate with the copy of live data and files.
   It holds real customer data on a public host behind the app's sign-in, so
   it stays up for at most 24 hours from the hand-off unless the owner
   extends it. AI calls made during UAT use live's key and cost the same as
   on live. Changes made during UAT stay on the copy and are deleted with it.
   - **Agent UAT.** The agent signs in to the copy in a browser as the owner
     would (the owner's email and `SWOOSHZ_SQAG_REHEARSAL_UAT_LOGIN_PASSWORD`)
     and runs every line of the [release UAT checklist](internal-uat.md) on
     the copy, plus this release's own UAT steps. Every line and step must
     pass, or be recorded NOT RUN with its reason, before the owner is asked;
     a blocking failure goes back to the builder. A failure an open task's
     plan already covers is recorded as known and planned with that task and
     slice; only a failure no plan covers that is new in this release or
     breaks a safety or pricing rule blocks the release, and any other goes
     to its area's open task for the owner to place before live or later. The owner's UAT list is only the
     checklist's human-eye lines, the lines the agent could not run, and any
     release step the agent could not run. Expect every quote of your own saved on live to reopen with Quote Basis
     locked until Next in Customer is pressed: the copy's `SESSION_SECRET`
     differs from live's (see the Pass A walk above). A quote made by another
     staff member opens view only instead, with no lock: every step with
     content opens, its files download, and nothing can be changed or
     saved. Only that lock is expected,
     the one Pass A records as `copy_only` in the `basis_lock` of each walked
     quote; a quote locked there without `copy_only`, a quote Pass A failed
     with any other lock, or a lock that stays after Next in Customer, is a
     failure.
   - **Hand-off message**, from the acting thread to the owner:

     > The test app is ready for your UAT: https://quote-rehearsal-copy.<server-ip>.sslip.io.
     > Sign in with your own email and the password in the credential store
     > under SWOOSHZ_SQAG_REHEARSAL_UAT_LOGIN_PASSWORD (not your live
     > password). It runs candidate <short> on a copy of live data taken at
     > <recorded time>; what you change there stays on the copy. On this copy
     > only, a quote of yours that you reopen keeps Quote Basis locked until
     > you press Next in Customer; live does not do this. A quote another
     > staff member made opens view only, on the copy and on live. The agent's
     > UAT passed: <evidence link>. Please check only these: <the gaps list>. It
     > stays up until <hand-off time + 24 hours> unless you extend it. Reply
     > "UAT pass <short>" to deploy it to live, or tell me what failed.

   - **UAT pass line.** Live waits for the owner's typed `UAT pass <short>`
     in the acting thread. Nothing else counts as the pass.
   - **UAT fail.** The fix goes back to the builder. The next candidate
     replaces the image on the same copy: set its `REHEARSAL_IMAGE_ID`,
     redeploy and run 8.1's walk and checks again, then this step again.
   - **Cap and cleanup.** Without a pass or an extension 24 hours after the
     hand-off, or when the owner abandons the candidate, live is not touched
     and step 9 runs.

   **8.3 Switch live**, after the owner's UAT pass.
   - Live migrations first, if any, in the runbook order and with their own
     approval.
   - Compare live's env names with the settings file without reading values.
     On the server, list the names only and drop the values before they leave
     the server, then check them with the image's names (from the build
     record) ignored:

     ```powershell
     ssh.exe swooshz-vps 'docker inspect --format ''{{range .Config.Env}}{{index (split . `=`) 0}}{{println}}{{end}}'' <live container>' | Set-Content -Encoding ascii "_tmp\deploy-$short-out\live-env-names.txt"
     python scripts\check_release_settings.py --env-names "_tmp\deploy-$short-out\live-env-names.txt" --ignore-names "_tmp\deploy-$short-out\image-env-names.txt"
     ```

     Fix a missing or unexpected name only with the owner's approval for that
     env change.
   - Commit the one-line image pin and its source comment on the
     `deployment/internal` branch and deploy in Coolify. Record the UTC time
     the deployment finished as `$switchedAt` (for example
     `2026-11-21T03:00:00Z`).

   **8.4 Pass B: the live read-only walk.** Run the live read-only check with
   the same manifest and Pass A's result. It reads the live sign-in from
   `SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL` and
   `SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD` only, never the rehearsal
   names, and `toolkit env run` feeds them from the credential store to this
   one command. Clear the rehearsal values from the session first:

   ```powershell
   Remove-Item Env:SQAG_REHEARSAL_LOGIN_EMAIL, Env:SQAG_REHEARSAL_LOGIN_PASSWORD -ErrorAction SilentlyContinue
   node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL,SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD -- node scripts\rehearsal-check.mjs --profile live-readonly --base-url https://<live host> --manifest deploy\rehearsal\live-cases.json --expect-files "$out\pass-a.json" --result "$out\live-readonly.json" --asset-out "$out\live-app.js"
   ```

   It signs in, loads the quote list, walks the same quotes as Pass A and
   captures the served `app.js`. It makes no AI call and writes nothing to
   live, telemetry included: it downloads no file (a download writes a
   telemetry row) and builds no candidate list. Instead it compares each
   quote's recorded XLSX and PDF hashes and sizes on live with what Pass A
   downloaded and checked on the copy, unless the quote was saved again after
   the copy was taken. Every channel it opens is read-only: the browser and
   its own API requests send only reads and the sign-in form, refuse
   anything else before it is sent, and list both in the result. It never
   runs the click probe: a click on live would save. Then run
   `scripts/verify_release_identity.py` with the expected source commit and
   image id and `--served-asset "$out\live-app.js"`; its hash must equal the
   rehearsal's `rehearsal-app.js`.

   **8.5 Roll back unless Pass B fully passed.** Precondition: the owner has
   confirmed that their deploy approval also covers this automatic rollback
   (an open owner item for the live-data test). Until they have, run it
   without `--execute`: it only prints the plan, and the owner approves the
   rollback first. From a clean checkout of `deployment/internal` (still
   pinned to the candidate), run:

   ```powershell
   node "<home folder>/.ai-agent-toolkit/source/ai-agent-toolkit/repo/scripts/toolkit-env.cjs" run --names COOLIFY_SWOOSHZ_API_URL,COOLIFY_SWOOSHZ_API_TOKEN -- python scripts\live_rollback.py --pass-b-result "$out\live-readonly.json" --live-url https://<live host> --switched-at $switchedAt --candidate-image <candidate image id> --candidate-source <candidate commit> --previous-image <current image id from step 1> --previous-source <its source commit> --deploy-checkout <deployment/internal checkout> --live-app-uuid <live app uuid> --record "$out\rollback.json" --execute
   ```

   It keeps the candidate only for a passing Pass B result that walked live's
   host (compared by hash), started after `$switchedAt`, downloaded nothing
   and compared the files with Pass A. Exit 0 keeps the candidate. Exit 10
   means it pinned the previous image, pushed the pin and Coolify finished
   deploying it: run Pass B again on the previous image and record the
   release as failed. Exit 2 refused before any effect and exit 1 failed
   part way: live may still run the candidate, so pin the previous image by
   hand and deploy at once. A failed identity check also means pinning the
   previous image and deploying again.
   - When a live migration is applied, move `LIVE_MIGRATION_HEAD` in
     `tests/disposable_database_mode.py` and the expected head in
     `tests/test_disposable_database_mode.py` in the next change, so CI keeps
     testing live's schema.

9. **Delete and record.** Runs after the live deploy (8.4 passed or 8.5
   finished), or when the owner abandons the candidate or its 24 hours run
   out (8.2).
   - Empty the rehearsal bucket: run the map's `clear_setup`, then its
     `clear_command` (`--mode clear`, which reports zero remaining). Then
     delete the `quote-rehearsal` and `quote-rehearsal-copy` services in
     Coolify and the `rehearsal-<short>` and `rehearsal-copy-<short>` Neon
     branches. The bucket itself stays. Never delete anything whose name does
     not say rehearsal.
   - Drop the run's generated secrets and remove
     `SWOOSHZ_SQAG_REHEARSAL_UAT_LOGIN_PASSWORD` from the credential store
     with `toolkit env`.
   - Store the build record, the preflight outputs, the guard results, every
     check result, the copy reports, the AI ledger, the UAT results and any
     rollback record in the evidence store, then remove the worktree with
     `git worktree remove`.
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
