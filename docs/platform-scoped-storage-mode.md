# KQAG Platform-Scoped Storage Mode

This runbook covers the first KQAG-owned app-data storage boundary for platform
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
$env:KQAG_ARTIFACT_STORAGE_MODE="database"
$env:KQAG_DATABASE_URL="sqlite:///C:/path/to/local/kqag-storage.sqlite3"
```

The first implementation supports the reviewed SQLite migration path. Unsupported
database URL schemes fail closed with a generic app-facing storage error and
privacy-safe logs. `KQAG_ARTIFACT_STORAGE_MODE=database` stores generated quote
exports and file assets in workspace-scoped SQLite BLOB rows for internal UAT.
Object storage such as S3 or R2 is intentionally out of scope for this layer.

## Migration

Review the migrations, then apply them explicitly:

- `migrations/001_platform_scoped_storage.sql` for workspace-scoped app data
- `migrations/002_platform_scoped_artifacts.sql` for workspace-scoped file and quote artifacts

```powershell
$env:KQAG_DATABASE_URL="sqlite:///C:/path/to/local/kqag-storage.sqlite3"
python scripts/migrate_kqag_storage.py
```

The app does not auto-run migrations on startup.

## Backup, Restore, Retention, And Rollback Evidence

Database artifact mode is a temporary internal-alpha/simple-hosting exception,
not final production storage. Before using it for internal alpha evidence, run
the synthetic verifier:

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
or production object storage. Production still requires object storage plus DB
metadata, retention state, and backup/restore evidence for DB rows and objects
together.

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

Local profile/pricing/session/artifact behavior remains the default and continues
to use existing runtime storage. Database artifact mode enforces allowed generated
quote artifact names, workspace keys, and size limits. It does not store arbitrary
uploaded reference files as permanent artifacts.

## Out Of Scope

This does not add a platform admin dashboard, invites or member management,
KQAG-owned login/auth, fake login, billing, Stripe, deployment, DNS/TLS, public
signup, object storage, private profile/pricing files, arbitrary permanent uploads, or
generated customer quotes in Git.
