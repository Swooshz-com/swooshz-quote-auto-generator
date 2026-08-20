# SQAG PostgreSQL runtime privilege contract

## Purpose

This contract is the small repository boundary for the SQAG PostgreSQL runtime.
It proves the reviewed application namespace, the canonical migration head, the
capabilities actually used by the application, and the bounded provenance of
those capabilities. Exact Git commit/tree admission remains a CI and deployment
preflight concern. The contract deliberately contains no source revision pin,
source digest mirror, provider role exception, or self-referential package hash.

The machine-readable authority is
[`docs/runtime-privilege-contract.json`](runtime-privilege-contract.json).
The executable authority is
[`scripts/validate_runtime_privilege_contract.py`](../scripts/validate_runtime_privilege_contract.py).
The read-only admission path is
[`scripts/preflight_sqag_migrations.py`](../scripts/preflight_sqag_migrations.py).

## Trust boundary

The declared set is deliberately bounded to `public.sqag_*` application objects:

- six canonical PostgreSQL migrations and their canonical migration-ledger checksums;
- the 15 application tables plus `sqag_schema_migrations`;
- the 22 canonical indexes;
- the two migrator-owned invoker trigger routines;
- the fixed connection search path `public, pg_catalog`;
- the reviewed runtime and maintenance capability matrices.

Any missing, extra, malformed, differently owned, or otherwise unclassified
`public.sqag_*` relation or routine is RED. Provider-created administration and
system role topology is not silently adopted by this repository contract; it is
admitted separately at deployment.

## Fixed connection namespace

Every PostgreSQL connection created by the canonical storage adapter passes the
libpq option:

```text
-c search_path=public,pg_catalog
```

`$user` is not present. The setting is applied before application SQL without a
preparatory SQL statement, so a caller can still begin a read-only evidence
transaction before collecting observations.

## Mandatory session authority

Every PostgreSQL application, migration, or maintenance connection must admit the server-authoritative pair `session_user` and `current_user` before yielding a connection to protected SQL. The expected role is explicit and fixed at the call site: `sqag_runtime` for application-runtime metadata, `sqag_migrator` for migration-ledger/schema inspection or application, and `sqag_maintenance` for destructive retention. The invariant is `session_user == current_user == expected_role`; an omitted, assumed, or unexpected role fails closed without exposing the DSN, host, credential, or observed role.

Migration tooling is a separate explicit `sqag_migrator` path and is not accepted by `DatabaseSqagStorage`. The migration-ledger preflight requires the separately configured `SQAG_MIGRATOR_DATABASE_URL`; it never falls back to `SQAG_DATABASE_URL` or `SQAG_MAINTENANCE_DATABASE_URL`. The DSN username, environment, and `SET ROLE` cannot redefine application, migration, or maintenance authority. SQLite remains a separate local connection path and does not execute the PostgreSQL session-identity check.

## Roles and capabilities

The three declared roles are `sqag_runtime`, `sqag_migrator`, and
`sqag_maintenance`. All are non-superuser, non-CREATEDB, non-CREATEROLE,
non-replication, non-bypass-RLS, `NOINHERIT`, and have no declared memberships.
The migrator owns the declared namespace. Runtime and maintenance own no
application objects and have no grant options.

The runtime role has only `CONNECT`, public-schema `USAGE`, and the operations
listed under `runtime_tables` in the JSON contract. It has no CREATE or
TEMPORARY database privilege, no schema CREATE, no sequence, routine, view,
materialized-view, column, migration-ledger, or retention-control authority.

The maintenance role is a separate projection. PostgreSQL retention must use
`SQAG_MAINTENANCE_DATABASE_URL`; it must never fall back to
`SQAG_DATABASE_URL`. Its matrix is limited to the reads, claims, deletes,
legal-hold mutations, delete authorizations, receipts, cursor updates, and
publication/object metadata operations required by the canonical retention
paths. Normal runtime connections are denied every maintenance-only table.

The local SQLite retention path is unchanged when an explicit SQLite URL or the
local default is selected. `--use-configured-database` is PostgreSQL-only and
requires the maintenance projection.

## Provenance checks

Effective `has_*_privilege` observations are necessary but not sufficient. The
verifier also observes, only for the declared namespace and roles:

- normalized database, schema, table, and routine ACL entries produced through
  `aclexplode`, never raw ACL arrays;
- explicit column ACL provenance from `pg_attribute.attacl`, expanded without
  a default ACL;
- grant-option flags;
- ownership of every declared table, index, and routine, plus exact `pg_proc` identity and `pg_trigger.tgfoid` linkage for every forensic trigger routine;
- nonsecret role attributes;
- direct membership rows involving declared roles, including ADMIN, INHERIT,
  and SET options;
- relevant default ACL entries;
- the absence of runtime/maintenance explicit column grants and public
  application grants.

The information-schema column-grant view is effective authority, so table-level
grants may appear there as per-column rows. It is not used as proof of explicit
column ACL provenance. Effective table authority remains checked independently,
including grant-option state.


It does not select passwords, authentication material, database URLs, customer
or business rows, query text, sensitive statistics, raw role graphs, function
source, object bytes, arbitrary OIDs, or provider-private values. Receipts and
validator output contain only safe statuses, counts, and fixed identifiers.

## Migration and source binding

`production_migrations` is mechanically compared with
`webapp.postgres_migrations.migration_manifest`. A migration must be at the
canonical head with no checksum drift, missing table, unexpected pending table,
missing index, missing trigger, ambiguous/wrong routine identity, or wrong trigger linkage before the capability proof
can pass.

The verifier performs a small source binding over the actual SQL relation names
in the canonical application files. Supported PostgreSQL relations must be in
the declared migration universe. The legacy SQLite/database-artifact names are
listed only as explicitly unsupported source branches; they are not PostgreSQL
authority. Dynamic SQL relation variables must be named in the bounded source
binding and remain constrained by the canonical allowlist. No source digest is
generated, stored, refreshed, or compared.

## Failure behavior

Unknown PostgreSQL major, wrong database identity, unsafe search path, migration
drift, unexpected namespace object, ownership change, missing capability,
unexpected capability, PUBLIC drift, grant option, membership, INHERIT/SET/Admin
path, default-ACL drift, routine authority, or sensitive-observation boundary
failure is fail-closed. The preflight emits `safeToApply: false` and a generic
blocker; it never prints a connection value.

## Evidence and teardown

Repository CI must run the static validator and the real PostgreSQL 17 contract
tests with actual canonical migrations and storage/forensic/retention paths.
The disposable fixture uses separate migrator, runtime, and maintenance roles,
two synthetic workspaces, positive operations, cross-workspace negatives, and
anti-false privilege mutations. Its final receipt is emitted only after all
connections are terminated, the database and roles are removed, and negative
residual inspection proves that no fixture resource remains. Cleanup ambiguity
is `CLEANUP_UNKNOWN`, never PASS, and the uncertain fixture is never reused.

## Review rule

Adding a migration, application relation, routine, privilege, search-path entry,
role edge, or source SQL relation requires an explicit reviewed contract change
and corresponding real-runtime evidence. Provider/system role edges and live
deployment credentials remain outside repository finality and require a later
deployment admission boundary.
