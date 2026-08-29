# SQAG PostgreSQL Migration Runbook

SQAG PostgreSQL migrations are explicit operator actions. Application startup,
ordinary pull-request CI, health checks, and readiness probes do not apply
them. PostgreSQL migration and preflight commands use the dedicated
`SQAG_MIGRATOR_DATABASE_URL`. `SQAG_DATABASE_URL` is the runtime URL and
`SQAG_MAINTENANCE_DATABASE_URL` is the maintenance URL; neither is a fallback
for the migrator URL. Secret values are supplied outside source control,
chat, logs, screenshots, and command history.

## Ledger Contract

`webapp/postgres_migrations.py` defines this immutable ordered manifest:

1. `001_platform_scoped_storage.sql`
2. `003_object_artifact_metadata.sql`
3. `004_generation_forensics_feedback_retention_postgres.sql`
4. `005_forensic_postgres_delete_guards.sql`
5. `006_quote_publication_versions_postgres.sql`
6. `007_feedback_publication_binding_postgres.sql`
7. `008_quote_session_deletion_hold_authority_postgres.sql`

Successful applications are recorded in `public.sqag_schema_migrations` with
the sequence number, migration ID, SHA-256 source checksum, and database
applied timestamp. Checksums use strict UTF-8 migration bytes after CRLF and
bare CR line endings are normalized to LF. The same canonical bytes are
decoded and executed. A transaction-scoped PostgreSQL advisory lock
serializes migration processes, and the ledger row and its migration SQL
commit in the same transaction.

The runner accepts only an exact ordered prefix of the repository manifest.
It fails closed for checksum drift, unknown or out-of-order rows, a complete
ledger whose required objects are missing or altered, kind-specific constraint
drift, duplicate object multiplicity, invalid catalog projections, or any
existing public SQAG schema without the trusted ledger. The catalog comparison
keeps `pg_catalog.pg_get_expr(conbin, conrelid)` as the observed CHECK source.
CHECK comparison ignores `conkey` and permits only the documented deterministic
deparse/type-aware equivalences: comments, whitespace, redundant parentheses,
`now()`/`current_timestamp`, restricted `ANY`/`IN`, redundant literal
casts, and proven binary-compatible character relabels for the allowed regex
operators. Semantic or type-changing casts, typmods, operators, constants,
identifiers, functions, collations, validation, deferrability, and deferred
state remain significant.

## Read-only Preflight

Run the pre-apply inspection with only the dedicated migrator URL:

    python scripts/preflight_sqag_migrations.py --phase pre-apply

This opens an exact `sqag_migrator` session, starts a read-only transaction,
reads the catalog and ledger, rolls the transaction back, and prints
privacy-safe metadata. A ready pre-apply report may have a missing or present
ledger and may have pending IDs, but applied IDs must be the exact manifest
prefix and pending IDs the exact manifest suffix. A truly empty public schema
reports all seven IDs pending and is safe for a separately approved first
application. Existing public SQAG objects without a trusted ledger are
unsafe.

The command fails closed when `SQAG_MIGRATOR_DATABASE_URL` is absent,
non-PostgreSQL, or cannot establish the true `session_user` and
`current_user` identity `sqag_migrator`. It never inspects the ledger through
the runtime or maintenance URL.

## Future Approved First Application

No database or provider operation is authorized by this runbook. After a
separate approval naming an isolated disposable or otherwise approved empty
PostgreSQL database:

1. Supply `SQAG_MIGRATOR_DATABASE_URL` through the approved secret/configuration
   path.
2. Run the pre-apply command and retain its sanitized metadata output.
3. Require `safeToApply=true`, no blockers, an empty or missing trusted ledger,
   and the expected pending suffix.
4. Run `python scripts/migrate_sqag_storage.py` once.
5. Supply the runtime and maintenance URLs only for post-apply verification,
   then run:

       python scripts/preflight_sqag_migrations.py --phase post-apply

6. Require exact expected and applied heads, no pending IDs, no blockers, and
   verified runtime and maintenance privilege projections.

The migration CLI reports migration IDs only. It does not print connection
values. It revalidates each source checksum, uses the same transaction as
the DDL and ledger writes, rolls back on failure, and performs a strict
post-apply/no-op inspection.

## Privilege and Catalog Evidence

The disposable PostgreSQL-17 matrix must include the exact manifest and
ledger, CHECK deparse/type-aware equivalence and drift cases, validation and
deferrability drift, PK/UQ/FK metadata, duplicate multiplicity, standalone
and constraint-backed indexes, exact index alias/projection mismatch, empty
target grants, known and unknown managed objects, premature suffix objects,
unrelated provider objects, malformed/out-of-order/checksum rows, rollback,
advisory-lock serialization, applied-prefix drift, post-apply and no-op
behavior, and the actual CLI under wrong runtime, maintenance, bootstrap,
provider-like, and assumed-role authorities. Wrong-authority runs must fail
before mutation.

The causal transition is one disposable target: apply `001` through `007`,
observe preflight pending `008`, apply `008` with the actual CLI through the
dedicated migrator URL, verify callable identity/body/owner/security/search
path/ACL, observe post-apply ready state, then verify that the runtime role is
denied direct legal-hold SELECT. Emit
`RUN313_PG17_CAUSAL_TRANSITION_EXECUTED` only after that final denial check.

Live retention/delete verification is not a migration action. Verification
commands never grant themselves migration authority, create or populate the
ledger, execute migration files, or silently repair schema state.

## Existing Unledgered Schema

Do not run the migration command against an existing unledgered schema.
Adoption requires a separate isolated comparison, data review, explicit
baseline proposal, and rollback point. This repository contains no automatic
baseline or repair shortcut.

## CI Evidence

Pull-request CI uses a disposable PostgreSQL-17 service with no production
connectivity or provider credentials. It runs the focused ledger and privilege
matrix, static runtime-contract checks, the causal transition, and the full
repository test gate. CI must be checked at the exact PR head before
acceptance.

## Rollback

A failed migration transaction rolls back its SQL and ledger rows
automatically. Do not delete ledger rows, edit checksums, or run ad hoc down
migrations. Any future live rollback uses a separately approved
provider-level rollback point, followed by read-only preflight.
