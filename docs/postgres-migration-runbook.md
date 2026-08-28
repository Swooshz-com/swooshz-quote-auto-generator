# SQAG PostgreSQL Migration Runbook

SQAG PostgreSQL migrations are explicit operator actions. Application startup,
ordinary pull-request CI, health checks, and readiness probes do not apply them.
The only required environment-variable name is `SQAG_DATABASE_URL`; its value
must be supplied outside source control, chat, logs, screenshots, and command
history.

## Ledger Contract

`webapp/postgres_migrations.py` defines the ordered immutable migration IDs:

1. `001_platform_scoped_storage.sql`
2. `003_object_artifact_metadata.sql`
3. `004_generation_forensics_feedback_retention_postgres.sql`
4. `005_forensic_postgres_delete_guards.sql`
5. `006_quote_publication_versions_postgres.sql`
6. `007_feedback_publication_binding_postgres.sql`
7. `008_quote_session_deletion_hold_authority_postgres.sql`

Successful applications are recorded in `public.sqag_schema_migrations` with
the sequence number, migration ID, SHA-256 source checksum, and database-applied
timestamp. The checksum is calculated over strict UTF-8 migration bytes after
CRLF and bare CR line endings are normalized to LF. Working-tree EOL conversion
therefore does not change ledger identity. The same canonical UTF-8/LF bytes are
decoded and executed; hashing and execution do not use separate representations.
A transaction-scoped PostgreSQL advisory lock serializes migration processes.
The ledger row and its migration SQL commit in the same transaction; a failed
statement cannot record false success.

The runner accepts only an exact ordered prefix of the repository manifest. It
fails closed for checksum drift, an unknown or out-of-order ledger row, a
complete ledger whose required tables are missing, or any existing public
schema without the trusted ledger. It never silently baselines an existing
schema.

## Read-only Preflight

Run the preflight before proposing any database mutation:

```powershell
python scripts/preflight_sqag_migrations.py
```

The command starts a read-only transaction, reads catalog and ledger metadata,
rolls the transaction back, and prints only non-secret migration metadata:
expected head, applied head, applied IDs, pending IDs, ledger state, and safe
blocker identifiers. It exits non-zero when migration would be unsafe.

A missing ledger on a truly empty public schema is reported as `ledgerState` =
`missing` with all IDs pending and is safe for a separately approved first
application. A missing ledger with any existing public table is unsafe and is
reported as `existing_schema_without_trusted_ledger`.

## Future Approved First Application

No database or provider operation is authorized by this runbook. After an
operator receives separate approval naming an isolated empty Neon branch or a
new empty PostgreSQL database:

1. Supply `SQAG_DATABASE_URL` through the approved secret/configuration path.
2. Run the read-only preflight and retain its sanitized metadata output.
3. Require `safeToApply=true`, a missing or empty trusted ledger, and the
   expected pending IDs.
4. Run `python scripts/migrate_sqag_storage.py` once.
5. Run the read-only preflight again and require no pending IDs, exact expected
   and applied heads, no blockers, and `safeToApply=true`.
6. Run the existing read-only schema/readiness checks before deploying app code.

The migration command reports migration IDs only. It does not print the target
connection value.

## Evidence Verifier Boundary

Live retention/delete verification is not a migration action. Before that
evidence can be separately authorized, run the read-only migration preflight
and require a present exact ordered ledger, zero pending migrations, and ready
tables, indexes, triggers, and routines. If migrations are pending, stop and
obtain separate authorization for migration application, then repeat the
read-only preflight.

Verification commands never grant themselves migration authority. They do not
create or populate the ledger, run DDL, execute migration files, or silently
repair schema state.

## Existing Unledgered Schema

The production schema described before this change has no trusted ledger. Do
not run the migration command against it. Adoption requires a separate proposal
and approval for all of the following: an isolated clone or branch, exact schema
comparison against repository expectations, checksum and object inventory,
review of existing data, an explicit baseline operation, and a rollback point.
This repository intentionally contains no automatic `mark current`, repair, or
baseline shortcut. Canonical migration bytes remove cross-platform EOL drift;
they do not prove equivalence for an existing unledgered schema and do not
authorize production baselining.

## CI Evidence

Pull-request CI starts an isolated disposable PostgreSQL service with no
production connectivity or provider credentials. The integration tests prove:

- fresh first application and complete expected tables;
- exact ledger IDs and checksums;
- a second no-op run;
- fail-closed checksum drift and unexpected ledger entries;
- advisory-lock serialization;
- a mutation-free read-only preflight;
- transaction rollback without a false success ledger row;
- refusal to adopt an existing unledgered schema; and
- the runtime-only callable hold-decision authority with direct legal-hold
  table access still denied.

## Rollback

Before a future live application, create the separately approved provider-level
rollback point. A failed migration transaction rolls back its SQL and ledger
rows automatically. After a successful forward migration, do not delete ledger
rows, edit checksums, or run ad hoc down migrations. Roll application code back
only when its previous release is verified compatible with the applied schema;
otherwise restore or switch to the approved pre-migration database rollback
point. Re-run read-only preflight after any rollback decision.
