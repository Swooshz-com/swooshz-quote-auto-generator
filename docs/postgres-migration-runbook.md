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
8. `009_telemetry_events_postgres.sql`

Successful applications are recorded in `public.sqag_schema_migrations` with
the sequence number, migration ID, SHA-256 source checksum, and database
applied timestamp. Checksums use strict UTF-8 migration bytes after CRLF and
bare CR line endings are normalized to LF. The same canonical bytes are
decoded and executed. A transaction-scoped PostgreSQL advisory lock
serializes migration processes, and the ledger row and its migration SQL
commit in the same transaction.

Migration 009 creates `sqag_telemetry_source_state` and
`sqag_telemetry_events`, their feed/retention/retry indexes, and the existing
append-only/delete-authorization trigger bindings. It stores only bounded
metadata, preserves workspace-scoped source ordering, and keeps source state
after event retention or deletion. Its historical v1 quote-session hold
authority remains bound to migration 008; the independent telemetry-aware v2
authority is bound to migration 009 and its callable relation inventory does
not include `sqag_telemetry_source_state`. The migration is source- and
checksum-locked like the earlier migrations; this run authorizes no live
migration.

The runner accepts only an exact ordered prefix of the repository manifest.
It fails closed for checksum drift, unknown or out-of-order rows, a complete
ledger whose required objects are missing or altered, kind-specific constraint
drift, duplicate object multiplicity, invalid catalog projections, or any
known, premature, or unknown object in the reserved public `sqag_` namespace
without the trusted ledger. Unrelated provider/public objects outside that
reserved namespace do not block a first application and are not adopted. The
catalog comparison keeps `pg_catalog.pg_get_expr(conbin, conrelid)` as the
observed CHECK source. Foreign-key identity includes the exact referenced
schema, referenced table, ordered local and referenced columns, match type,
actions, validation, and deferrability state. Index identity uses an exact
ten-field catalog projection with positional tuple decoding and explicit
boolean `constraint_backed` semantics.
Trigger observations are an ordered, lossless catalog sequence. Their semantic
identity is `(schema_name, table_name, trigger_name)`; comparison also binds
timing, canonical event order, ordered update columns, row/statement level,
enabled state, and the linked routine schema/name/identity arguments. Trigger
OID is projection evidence and a deterministic query tie-breaker only. A
second expected-named trigger on another public table is a managed extra, not
a replacement for the canonical trigger, and duplicate observations cannot be
collapsed by name.
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
prefix and pending IDs the exact manifest suffix. A target with no reserved
public `sqag_` objects reports all eight IDs pending and is safe for a
separately approved first application, even when unrelated provider/public
tables, indexes, routines, or triggers are present. Known premature SQAG
objects and unknown `sqag_` objects remain fail-closed blockers; existing
public SQAG objects without a trusted ledger are not adopted.

The command fails closed when `SQAG_MIGRATOR_DATABASE_URL` is absent,
non-PostgreSQL, or cannot establish the true `session_user` and
`current_user` identity `sqag_migrator`. It never inspects the ledger through
the runtime or maintenance URL.

### CLI Grammar and Output

The preflight command accepts exactly one of these two argument sequences
after the program name:

    --phase pre-apply
    --phase post-apply

Every other form fails before manifest validation, configuration or URL
lookups, driver or connection setup, and PostgreSQL access. This includes a
missing or invalid phase, repeated phase options, unknown or short options,
abbreviated or case/Unicode-variant options or values, positional or extra
tokens, a standalone `--`, and `--phase=...`. Grammar failure exits `2` and
prints exactly one deterministic JSON object with no usage text, traceback,
argument echo, URL, credential, SQL, or driver detail:

    {"appliedHead":null,"appliedMigrationIds":null,"blockers":["invalid_cli_grammar"],"expectedHead":null,"ledgerState":null,"pendingMigrationIds":null,"phase":null,"safeToApply":false,"status":"unsafe"}

Once the exact phase grammar succeeds, every failure preserves that phase.
Migration fields contain only observations established before the failure;
unavailable fields are JSON `null`, never fabricated `unknown` or empty
projections. PRE failures do not contain runtime or maintenance contract
keys. POST failures contain both keys, each either its completed trustworthy
verification summary or JSON `null`. Blockers use stable privacy-safe
identifiers only.

Successful PRE output contains the actual migration report, the explicit
`"phase":"pre-apply"` field, and no runtime or maintenance contract keys.
Successful POST output contains the actual final migration report, the
explicit `"phase":"post-apply"` field, zero pending IDs and blockers, and
the verified runtime and maintenance contract summaries.

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

The retained operator order is binding: rollback or recovery point, exact
reviewed source, PRE, separate Web approval, one forward migration, POST,
then readiness/deploy. PRE and POST are read-only evidence and do not replace
the separate migration approval or authorize live/provider operations.

The migration CLI reports migration IDs only. It does not print connection
values. It revalidates each source checksum, uses the same transaction as
the DDL and ledger writes, rolls back on failure, and performs a strict
post-apply/no-op inspection.

## Privilege and Catalog Evidence

The disposable PostgreSQL-17 matrix must include the exact manifest and
ledger, CHECK deparse/type-aware equivalence and drift cases, validation and
deferrability drift, PK/UQ/FK metadata, exact referenced-schema and match-type
drift, local/referenced column order and action/state drift, duplicate
multiplicity, standalone and constraint-backed indexes, exact index
alias/projection mismatch, empty-target grants, known and unknown managed
objects, premature suffix objects, unrelated provider objects, malformed/
out-of-order/checksum rows, rollback, advisory-lock serialization,
applied-prefix drift, post-apply and no-op behavior, and the actual CLI under
wrong runtime, maintenance, bootstrap, provider-like, and assumed-role
authorities. The real PostgreSQL-17 applied-prefix matrix holds `001` through
`008` applied with `009` pending and proves read-only RED behavior for a
missing and drifted required telemetry index and trigger, while retaining the
historical v1 routine checks from migration 008 and the exact telemetry-aware
v2 routine checks from migration 009. It also proves
lexically-before and lexically-after same-name trigger collisions, the
canonical-missing plus wrong-table-extra case, missing-ledger known and unknown
SQAG routines, an unrelated-provider-routine GREEN control, a managed-empty
GREEN control, and a valid present empty ledger with premature `009` telemetry
objects. Every applicable inspection has an identical mutation-relevant
BEFORE/AFTER snapshot.

The assumed-role case creates one generated LOGIN role with no superuser,
database, role-creation, inheritance, replication, or row-security bypass
authority. It receives only target-database `CONNECT`, `SET TRUE` membership
in `sqag_migrator` with `ADMIN FALSE` and `INHERIT FALSE`, and a
database-local connection-time `role=sqag_migrator` setting. A direct
connection first proves the authenticated `session_user` is the generated
login while `current_user=sqag_migrator`. The test then invokes the unchanged
production migration script in a child interpreter with only the migrator
database URL among SQAG database selectors and with conflicting libpq
`PG*` selectors removed. No URL `options=`, in-process CLI call, or patched
connection factory is used. The CLI must exit 2 with only the generic
privacy-safe refusal before inspector, lock, DDL, or ledger mutation.

Every wrong-authority actual-CLI run must capture deterministic
read-only BEFORE/AFTER snapshots covering ledger rows and timestamps, applied/
pending/head state, all managed relations/tables/columns/constraints, index
semantics, qualified trigger multiplicity and linkage, routine
identity/body/security/config, and object/schema/database ACLs;
the snapshots must be identical and the CLI must fail before mutation.
After the assumed-role refusal, all generated-role sessions are closed, the
database-local role setting is reset, membership and `CONNECT` are revoked,
and the role is dropped. The test proves no role, membership, database ACL,
`pg_db_role_setting`, shared dependency, or owned-object residue before
rechecking the clean `001`-`008` / pending-`009` preflight.

The causal transition is one disposable target and has no post-`009` helper
repair: complete the ACL/default-ACL fixture first, apply `001` through `008`,
take a mutation-relevant BEFORE snapshot, and execute the production preflight
script in a bounded child interpreter with a scrubbed minimal environment.
Require the actual PRE JSON to show exactly `001` through `008` applied and
only `009` pending, with an identical BEFORE/AFTER snapshot. Exercise every
wrong-authority actual-CLI negative, including the assumed-role
`session_user != current_user` case, with immutable snapshots; tear down that
role narrowly and prove no role, membership, database ACL, database-local
setting, owned-object, or related residue. Execute the actual PRE child process
again on the same clean target and require the same `001`-`008` / pending-`009`
report before applying `009`.

Apply `009` with the actual CLI through the dedicated migrator URL, immediately
prove the telemetry tables, indexes, trigger bindings, and migration-ledger
row directly from the catalog, then take a pre-POST snapshot and execute the
actual POST preflight child process with the dedicated migrator, runtime, and
maintenance URLs. Require exact final heads, zero pending IDs and blockers,
verified runtime and maintenance summaries, no URL/secret leakage, and an
identical read-only BEFORE/AFTER snapshot. Prove the v1 catalog/source/ACL
snapshot is unchanged and the v2 catalog/source/ACL/relation contract is
exact. Prove the runtime role is denied direct legal-hold SELECT. The causal
behavior must additionally prove linked held telemetry, enabled linked
`telemetry_event` holds, invalid telemetry fail-closed behavior, valid
unheld deletion, and same-workspace unrelated and wrong-workspace isolation.
Run the actual CLI a second time as an unchanged no-op with no ledger/object/
ACL mutation. A second POST is not required.

Live retention/delete verification is not a migration action. Verification
commands never grant themselves migration authority, create or populate the
ledger, execute migration files, or silently repair schema state.

## Existing Unledgered Schema

Do not run the migration command against a target containing any public
`sqag_` object without a trusted ledger. The reserved namespace is the
admission boundary: known, premature, and unknown managed objects require a
separate isolated comparison, data review, explicit baseline proposal, and
rollback point. Unrelated provider/public objects outside the reserved
namespace are allowed and are neither adopted nor changed. This repository
contains no automatic baseline or repair shortcut.

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
