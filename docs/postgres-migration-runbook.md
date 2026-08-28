# SQAG PostgreSQL Migration Runbook

SQAG PostgreSQL migrations are explicit operator actions. Application startup,
ordinary pull-request CI, health checks, and readiness probes do not apply
them. The URL values must be supplied outside source control, chat, logs,
screenshots, and command history.

The operator roles are deliberately separate:

- `SQAG_MIGRATOR_DATABASE_URL` is the only PostgreSQL migration and ledger
  authority. It must authenticate as `sqag_migrator`.
- `SQAG_DATABASE_URL` is the runtime projection. The migration CLI uses it
  only when it is explicitly a local SQLite URL; it is never a PostgreSQL
  migration target or fallback.
- `SQAG_MAINTENANCE_DATABASE_URL` is the maintenance projection used by the
  post-apply readiness check. It is never a migration target or fallback.

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

## Two-Phase Read-only Preflight

The phase is mandatory. There is no default:

```powershell
python scripts/preflight_sqag_migrations.py --phase pre-apply
python scripts/preflight_sqag_migrations.py --phase post-apply
```

`pre-apply` is the admission check before forward migration. It connects only
through `SQAG_MIGRATOR_DATABASE_URL`, binds the server-authoritative
`session_user` and `current_user` to `sqag_migrator`, starts a read-only
transaction, and reads the canonical per-migration manifest/object model and
ledger. It verifies that the ledger is an exact contiguous applied prefix,
that every already-applied table, column mutation, index, trigger, and routine
has the expected schema, definition, identity, ownership, and migration-defined
ACL facts, and that pending-only objects are absent. A safe result may
therefore contain a non-empty pending set, but that set must be the exact
untouched suffix after the applied prefix. Missing-ledger state is safe only
when no reserved `public.sqag_*` object exists; an empty ledger is safe only
when no managed or pending object exists. The check is read-only and never
normalizes ACLs or repairs schema.

`post-apply` is the final readiness check. It requires all three dedicated
URLs, uses the migrator URL for the read-only ledger inspection, requires zero
pending migrations and the unchanged strict final-state migration validator,
then verifies the complete v4 runtime and maintenance projections. That
verification includes the canonical callable routine body, ownership, ACLs,
runtime direct denial of protected legal-hold table access, and the required
PostgreSQL-17 contract.

Both phases roll back their read-only transactions and print deterministic,
privacy-safe JSON containing the actual phase, ledger state, expected and
applied heads, applied IDs, pending IDs, and blockers. `post-apply` also
contains the runtime and maintenance contract summaries. No URL value is
printed.

## Future Approved Application Sequence

No database or provider operation is authorized by this runbook. After an
operator receives separate approval naming an isolated empty Neon branch or a
new empty PostgreSQL database, follow this exact sequence:

1. Confirm the approved provider rollback/recovery point and recovery plan.
2. Confirm the canonical repository migration source and ordered manifest.
3. Supply only `SQAG_MIGRATOR_DATABASE_URL` through the approved secret or
   configuration path and run `pre-apply`.
4. Have the controller record and approve the exact pending migration suffix
   returned by `pre-apply`; stop on any blocker, prefix drift, or unexpected
   object state.
5. Run `python scripts/migrate_sqag_storage.py` exactly once for the approved
   forward application. For PostgreSQL,
   this command uses only `SQAG_MIGRATOR_DATABASE_URL`. If the configured
   runtime URL is explicitly a local SQLite URL, it preserves the separate
   local SQLite branch and does not use PostgreSQL at all.
6. Supply the dedicated runtime and maintenance URLs, run `post-apply`, and
   require zero pending IDs, the exact final heads, no blockers, and verified
   v4 runtime and maintenance contracts.
7. Run the existing readiness checks and proceed to deployment only through a
   separately approved deployment authority.

Do not run a down migration, delete ledger rows, edit checksums, issue manual
SQL, create missing objects by hand, invoke startup as a migration mechanism,
or skip either phase. A failed forward transaction rolls back its own SQL and
ledger rows; an approved provider rollback/recovery point remains the recovery
authority for an already committed application.

The migration command reports migration IDs only. It does not print any target
connection value.

## Evidence Verifier Boundary

Live retention/delete verification is not a migration action. Before that
evidence can be separately authorized, run `post-apply` and require a present
exact ordered ledger, zero pending migrations, and ready tables, indexes,
triggers, routines, callable body, and ACLs. If migrations are pending, stop
and obtain separate authorization for migration application, then repeat the
two-phase sequence.

Verification commands never grant themselves migration authority. They do not
create or populate the ledger, run DDL, execute migration files, or silently
repair schema state.

## Existing Unledgered Schema

The production schema described before this change has no trusted ledger. Do
not run the migration command against it. Adoption requires a separate
proposal and approval for all of the following: an isolated clone or branch,
exact schema comparison against repository expectations, checksum and object
inventory, review of existing data, an explicit baseline operation, and a
rollback point. This repository intentionally contains no automatic `mark
current`, repair, or baseline shortcut. Canonical migration bytes remove
cross-platform EOL drift; they do not prove equivalence for an existing
unledgered schema and do not authorize production baselining.

## CI Evidence

Pull-request CI starts an isolated disposable PostgreSQL-17 service with no
production connectivity or provider credentials. The integration tests prove:

- fresh first application and complete expected tables;
- exact ledger IDs and checksums;
- a second no-op run;
- fail-closed checksum drift and unexpected ledger entries;
- advisory-lock serialization;
- a mutation-free read-only preflight;
- transaction rollback without a false success ledger row;
- refusal to adopt an existing unledgered schema;
- applied-prefix index, trigger, and routine drift controls;
- no-ledger reserved-namespace and exact-empty-state controls;
- actual-CLI wrong-authority and assumed-role zero-mutation controls; and
- the runtime-only callable hold-decision authority with direct legal-hold
  table access still denied; and
- the causal operator transition from canonical 001-007, through an exact
  pending 008 preflight, to one real migrator-bound 008 application, complete
  post-apply v4 verification, and a ledger-preserving second no-op.

## Rollback

Before a future live application, create the separately approved provider-level
rollback point. A failed migration transaction rolls back its SQL and ledger
rows automatically. After a successful forward migration, do not delete ledger
rows, edit checksums, or run ad hoc down migrations. Roll application code back
only when its previous release is verified compatible with the applied schema;
otherwise restore or switch to the approved pre-migration database rollback
point. Re-run the two-phase read-only preflight after any rollback decision.
