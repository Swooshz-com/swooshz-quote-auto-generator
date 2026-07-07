# SQAG Platform-Scoped Storage Mode

This runbook covers the first SQAG-owned app-data storage boundary for platform
team mode. It is disabled by default. Existing local/internal mode continues to
use local runtime storage under `QUOTE_DATA_ROOT` and related local roots. The
artifact layer is separately gated so app-data database mode can be tested before
database-backed file artifacts are enabled.

## Boundary

Swooshz Platform owns users, login, platform sessions, workspaces, membership
roles, app access, invites, and billing. KQAG stores only quote-generator app
data scoped to the platform workspace ID present in the signed KQAG platform
session.

KQAG storage must not store raw platform launch tokens, provider tokens, raw
provider claims, auth codes, OIDC state, nonce, platform cookies, platform
session secrets, database passwords in logs, private local paths, or private
profile/pricing files in Git.

## Modes

Local mode is the default:

```powershell
$env:KQAG_STORAGE_MODE="local"
$env:KQAG_ARTIFACT_STORAGE_MODE="local"
```

Database mode requires a valid platform-launched KQAG session and a configured
KQAG database URL:

```powershell
$env:KQAG_PLATFORM_LAUNCH_MODE="platform"
$env:KQAG_PLATFORM_BASE_URL="https://platform.example.test"
$env:KQAG_STORAGE_MODE="database"
$env:KQAG_ARTIFACT_STORAGE_MODE="object"
$env:KQAG_DATABASE_URL="sqlite:///C:/path/to/local/kqag-storage.sqlite3"
```

The first implementation supports the reviewed SQLite migration path. Unsupported
database URL schemes fail closed with a generic app-facing storage error and
privacy-safe logs. `KQAG_ARTIFACT_STORAGE_MODE=database` stores generated quote
exports and file assets in workspace-scoped SQLite BLOB rows for local-UAT and
synthetic verifier coverage only; it is not a hosted/protected/deploy readiness
or production artifact path.
`KQAG_ARTIFACT_STORAGE_MODE=object` requires database storage plus a configured
S3-compatible object backend. Object mode stores generated artifact bytes in the
object backend and safe workspace-owned metadata in the database; it must not
fall back to local artifacts or database BLOB artifacts when the provider is
missing, incomplete, unauthorized, or failing.

## Migration

Review the migrations, then apply them explicitly:

- `migrations/001_platform_scoped_storage.sql` for workspace-scoped app data
- `migrations/002_platform_scoped_artifacts.sql` for workspace-scoped file and quote artifacts
- `migrations/003_object_artifact_metadata.sql` for generated object-artifact metadata

```powershell
$env:KQAG_DATABASE_URL="sqlite:///C:/path/to/local/kqag-storage.sqlite3"
python scripts/migrate_kqag_storage.py
```

The app does not auto-run migrations on startup.

## Backup, Restore, Retention, And Rollback Evidence

Database artifact mode is not a hosted/protected/deploy readiness path and is
not final production storage. The synthetic verifier remains useful as a
metadata-only local-UAT guard, but it cannot make DB/BLOB artifact storage
launch-ready:

```powershell
python scripts/verify_database_backup_restore.py
python scripts/check_production_readiness.py --with-backup-restore-evidence
```

The verifier creates synthetic SQLite rows and BLOB artifacts only. It backs up
and restores the database and database-artifact rows together, compares
metadata checksums and row counts, verifies workspace/session ownership metadata
survives restore, validates the machine-readable retention policy in
`docs/internal-alpha-retention-policy.json`, and verifies rollback to a prior
known-good synthetic state.

Verifier output is metadata-only. It must not include DB URLs, absolute private
paths, artifact bytes, generated quote contents, customer data, pricing/profile
payloads, staff emails, OAuth values, cookies, tokens, or API keys.

This evidence does not replace hosted smoke testing, hosted logging/monitoring,
or production object storage. Hosted/protected/deploy and production readiness
require object storage plus DB metadata, retention state, and backup/restore
evidence for DB rows and objects together.

## Object Artifact Mode

SQAG is the canonical operator-facing prefix for object-storage provider and
live-provider evidence environment variables. The older `KQAG_*` object-storage
provider names are not compatibility aliases and do not satisfy live-provider
or production-readiness evidence. Existing non-object storage mode names such as
`KQAG_STORAGE_MODE` remain out of scope for this compatibility-preserving PR.

Object artifact mode is production groundwork, not production readiness. The
required environment variable names are:

- `SQAG_OBJECT_STORAGE_PROVIDER`
- `SQAG_OBJECT_STORAGE_ENDPOINT_URL`
- `SQAG_OBJECT_STORAGE_BUCKET`
- `SQAG_OBJECT_STORAGE_REGION`
- `SQAG_OBJECT_STORAGE_ACCESS_KEY_ID`
- `SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY`

Only the names should appear in docs, diagnostics, tests, and readiness output.
Do not print or commit provider values, bucket values, endpoints, access keys,
secret keys, object keys, DB URLs, generated artifacts, uploaded content, or
private paths.

The current S3-compatible adapter supports mocked/stubbed store, retrieve,
delete, workspace metadata checks, and checksum validation. This evidence does
not prove live provider credentials, provider IAM policy, network reachability,
DB+object backup/restore, retention/delete jobs, alert delivery, or production
deployment operations.

The live-provider verifier is explicit opt-in. In addition to the provider env
names above, it requires `SQAG_LIVE_OBJECT_STORAGE_EVIDENCE` to be enabled by an
operator in the execution environment. The repo-controlled Python dependencies
in `requirements.txt` include pinned `boto3`/`botocore` plus compatible pinned
transitive packages so the S3-compatible provider path is reproducible from repo
dependencies. Real provider values still belong only in the host secret manager
or operator environment, never in Git. The verifier stores, retrieves, checks,
and deletes synthetic XLSX/PDF bytes only, and its output is metadata-only. It
must not print or commit provider values, object keys, generated artifact bytes,
DB URLs, private paths, customer data, uploaded content, or secrets.

```powershell
python -m pip install --only-binary=:all: -r requirements.txt
python scripts/verify_live_object_storage_provider.py
python scripts/check_production_readiness.py --with-live-object-storage-provider-evidence
```

Without complete provider configuration and the live-evidence opt-in, the
verifier fails closed and production readiness remains false. A test-injected or
synthetic backend can exercise verifier logic, but it is not live-provider
evidence and cannot satisfy hosted/protected/deploy readiness.

Sanitized live-provider evidence was run on 2026-07-07 with canonical `SQAG_*`
env names supplied by the operator environment and no provider values committed
or printed. The live verifier reported `status=passed`,
`test_injected_backend=false`, `live_provider_evidence_supported=true`, and all
store, retrieve, checksum, content type, byte size, wrong-workspace, delete,
tombstone, and missing-object checks true. This proves only the metadata-only
live S3-compatible provider path for synthetic generated XLSX/PDF bytes.
Production readiness still requires live DB+object backup/restore, live
retention/delete, operations evidence, hosted observability and smoke evidence,
Platform smoke, and final audit.

Object artifact lifecycle evidence is synthetic/stubbed only:

```powershell
python scripts/verify_object_artifact_lifecycle.py
python scripts/check_production_readiness.py --with-object-storage-evidence --with-object-artifact-lifecycle-evidence --with-live-object-storage-provider-evidence
```

The lifecycle verifier uses synthetic SQLite metadata and the in-memory object
backend to check metadata backup/restore, restored object retrieval, missing
object detection, checksum mismatch detection, tombstoned artifact denial,
wrong-workspace denial, and local staging cleanup. It does not prove live
provider retention/delete, live DB+object backup/restore, production alerts, or
host operations. Object mode must not fall back to local artifacts or database
BLOB artifacts when object storage is unavailable, stale, deleted, corrupt,
unauthorized, or failing.

## Workspace Scope

Database rows are keyed by the platform workspace ID from the KQAG platform
session. Profiles, pricing references, and quote sessions saved by workspace A
must not list, read, export, or delete from workspace B.

## Included App Data

The boundary covers:

- quote-company profile list, save, delete, and export payload resolution
- profile pack layout asset persistence when artifact database mode is enabled
- pricing-reference list, detail, save, delete, and export payload resolution
- pricing-reference visual asset persistence when artifact database mode is enabled
- quote-session list, read, save, delete, and download metadata resolution
- generated `quotation.xlsx` and optional `quotation.pdf` artifact persistence
  when artifact database mode is enabled
- generated `quotation.xlsx` and optional `quotation.pdf` object metadata when
  artifact object mode is enabled with a usable object backend
- object-mode generated artifact tombstone status, retention status, and
  synthetic lifecycle evidence for generated quote artifacts

Local profile/pricing/session/artifact behavior remains the default and continues
to use existing runtime storage. Database artifact mode enforces allowed generated
quote artifact names, workspace keys, and size limits. It does not store arbitrary
uploaded reference files as permanent artifacts.

## Out Of Scope

This does not add a platform admin dashboard, invites or member management,
KQAG-owned login/auth, fake login, billing, Stripe, deployment, DNS/TLS, public
signup, live object-storage credentials or provider accounts, private
profile/pricing files, arbitrary permanent uploads, or generated customer quotes
in Git.
