# SQAG Runtime Privilege Contract

## Overview

This document describes the canonical runtime privilege contract for the SQAG database. The contract defines exactly which privileges the restricted application runtime role `sqag_runtime` may hold, and it enforces those boundaries through machine-readable manifest validation, CI integration, and disposable-PostgreSQL contract tests.

The contract is implemented across three surfaces:

- `docs/runtime-privilege-contract.json` -- canonical machine-readable manifest.
- `scripts/validate_runtime_privilege_contract.py` -- strict static validator.
- `tests/test_runtime_privilege_contract.py` -- static and disposable-PostgreSQL contract tests.

The remaining role, grant, and ACL mutations are reserved for **Boundary B** (staged live alignment) and **#160** (internal-alpha activation). This repository-only PR performs no live database or provider mutation.

## Why the manifest is canonical

The JSON manifest is the single authoritative source of truth for runtime privilege boundaries. It is:

- **Machine-readable**: consumed by the validator, tests, and CI without interpretation.
- **Versioned**: schema version `1` with strict key validation; unknown or duplicate keys cause the validator to fail closed.
- **Fail-closed**: any missing, extra, or over-broad entry is rejected.
- **Migration-bound**: every production PostgreSQL migration path, SHA-256 digest, and exact table binding from `MIGRATION_TABLES` is locked; adding, removing, renaming, or reordering a migration requires a reviewed contract refresh.

Code-level expectations elsewhere in the repository, such as `webapp/postgres_migrations.py`, are secondary sources. The manifest takes precedence, and the validator reconciles them.

## Migration digest refresh

When you add, remove, rename, reorder, or modify a production PostgreSQL migration file, the contract manifest must be updated:

1. Determine the ordered production migration set. This is defined in `webapp/postgres_migrations.py` as `MIGRATION_FILE_NAMES`.
2. For each migration, compute its canonical SHA-256 digest:
   ```
   python -c "from webapp.postgres_migrations import migration_manifest; from pathlib import Path; m = migration_manifest(Path('migrations')); [print(item.migration_id, item.checksum_sha256) for item in m]"
   ```
3. Update every `production_migrations` entry in the manifest:
   - `path` must match `migrations/<filename>`.
   - `sequence_no` must be its 1-indexed position.
   - `sha256` must be the canonical hex digest.
   - `tables` must exactly match the tables introduced or governed by that migration in `webapp.postgres_migrations.MIGRATION_TABLES`.
4. If the table inventory changes, update the `tables` section as described below.
5. Run the validator:
   ```
   python scripts/validate_runtime_privilege_contract.py
   ```
6. Run the test suite to confirm the contract holds in a disposable database.

CI enforces digest freshness automatically. A digest drift against the committed migrations fails the job.

## Table classification

Every `sqag_` table must be classified in the manifest under exactly one of two sections:

- `tables.runtime_accessible`: exactly 11 tables. Each entry records the exact `SELECT`, `INSERT`, `UPDATE`, `DELETE` boolean privileges.
- `tables.runtime_forbidden`: exactly 5 tables. Each entry records only its class and reason.

### How to classify a new table

1. Add the new table through a reviewed production migration.
2. Refresh migration digests.
3. Decide whether the runtime role needs direct access:
   - If yes, add it to `runtime_accessible` with the correct privilege booleans. Increment `rw_count` and `total_count`.
   - If no, add it to `runtime_forbidden` with a non-empty `class` and `reason`. Increment `forbidden_count` and `total_count`.
4. Run the validator and tests. Both must pass before merging.

The validator rejects:
- A table not present in either section.
- A table present in both sections.
- A count mismatch.
- A privilege not listed as a boolean for a runtime-accessible table.
- A forbidden table with an empty class or reason.
- Duplicate JSON object keys, nested unknown keys, missing nested keys, wrong JSON types, and contradictory locked values.
- A migration binding that omits or adds a table relative to the migration table map.

## Unknown objects fail closed

The validator and tests are designed so that any future object not explicitly classified fails the contract. This applies to:
- Tables.
- Sequences.
- Routines (functions and procedures).
- Default ACLs.

There is no silent adoption path. Every new production object requires a contract refresh with explicit classification.

## Exact table split: 11 runtime + 5 forbidden

### Runtime-accessible (11 tables)

| Table | Class | Privileges |
|---|---|---|
| `sqag_profiles` | mutable | SELECT, INSERT, UPDATE, DELETE |
| `sqag_pricing_references` | mutable | SELECT, INSERT, UPDATE, DELETE |
| `sqag_quote_sessions` | mutable | SELECT, INSERT, UPDATE, DELETE |
| `sqag_generation_runs` | append_and_status | SELECT, INSERT, UPDATE |
| `sqag_generation_evidence` | immutable_evidence | SELECT, INSERT |
| `sqag_audit_events` | immutable_audit | SELECT, INSERT |
| `sqag_feedback` | mutable_feedback | SELECT, INSERT, UPDATE |
| `sqag_feedback_status_history` | append_only_history | SELECT, INSERT |
| `sqag_object_artifacts` | mutable_metadata | SELECT, INSERT, UPDATE |
| `sqag_quote_publication_versions` | mutable | SELECT, INSERT, UPDATE, DELETE |
| `sqag_quote_publication_artifacts` | mutable | SELECT, INSERT, DELETE |

### Runtime-forbidden (5 tables)

| Table | Class |
|---|---|
| `sqag_legal_holds` | operator_only |
| `sqag_retention_delete_authorizations` | retention_only |
| `sqag_deletion_receipts` | retention_only |
| `sqag_retention_scan_cursors` | retention_only |
| `sqag_schema_migrations` | migration_only |

## Provider-owned routine exception

The function `public.show_db_tree()` is owned by the Neon provider role `neondb_owner`. It is not created or managed by SQAG migrations and does not appear in `EXPECTED_ROUTINES`.

The manifest records it as the single `provider_owned_exceptions` entry with these properties:
- **Owner**: `neondb_owner`.
- **Class**: `provider_diagnostic_exception`.
- **Direct runtime grant**: none.
- **PUBLIC EXECUTE**: unchanged (Boundary A and Boundary B must not mutate it).
- **Effective runtime execution**: a bounded PUBLIC exception. The runtime role can invoke it through inherited PUBLIC EXECUTE, and this is the only routine for which this is accepted.

The validator fails when:
- The exception is omitted from the manifest.
- The exception is broadened to another routine.
- The exception is represented as having no effective runtime execution.

Routine inventory is defined by the user-created/public-schema boundary: every
`pg_proc` row whose namespace is `public` and whose `prokind` is a routine kind
(`f`, `p`, `a`, or `w`) is included. The proof checks owner, invoker security
mode, trigger dependency, ACL posture, and deterministic identity-argument
ordering. Stock PostgreSQL must contain no `show_db_tree()` routine. A
provider-compatible fixture may add exactly that one routine and no other
public routine.

## PUBLIC TEMPORARY

Database-level TEMPORARY is currently granted to PUBLIC. This allows any connecting role, including `sqag_runtime` when activated, to create temporary tables.

Boundary A records the target state: PUBLIC TEMPORARY must be revoked after Boundary B. A role-specific REVOKE TEMPORARY on `sqag_runtime` is insufficient because a PUBLIC grant overrides a role-specific revoke. The correct fix is:

```sql
revoke temporary on database <dbname> from public;
```

The contract manifest records this as:
```json
"public": {
  "temporary": "forbidden_after_boundary_b"
}
```

The disposable PostgreSQL tests prove that after the revoke, a runtime role has `has_database_privilege('temp') = false`.

## Grantee-aware default-privilege verification

PostgreSQL's `ALTER DEFAULT PRIVILEGES` stores grants in `pg_catalog.pg_default_acl`. The `defaclrole` column records which role granted the defaults, not which role receives them. The actual grantee is embedded in the `defaclacl` ACL array.

The canonical verification query uses one grantee-expanded `CROSS JOIN
LATERAL aclexplode(...)` and resolves named grantees through a nullable role
join. It does not cast an absent role name to `regrole`:

```sql
select owner_role.rolname as owner,
       coalesce(ns.nspname, '<global>') as namespace,
       d.defaclobjtype as object_type,
       case when expanded.grantee = 0 then 'PUBLIC'
            else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text)
       end as grantee,
       expanded.privilege_type,
       expanded.is_grantable
from pg_catalog.pg_default_acl d
join pg_catalog.pg_roles owner_role on owner_role.oid = d.defaclrole
left join pg_catalog.pg_namespace ns on ns.oid = d.defaclnamespace
cross join lateral pg_catalog.aclexplode(d.defaclacl) expanded
left join pg_catalog.pg_roles grantee_role
  on grantee_role.oid = expanded.grantee and expanded.grantee <> 0
where d.defaclobjtype in ('r', 'S', 'f')
order by owner, namespace, object_type, grantee, expanded.privilege_type, expanded.is_grantable;
```

Checking only `defaclrole = 'sqag_runtime'` is insufficient and must not be used as the sole acceptance proof. The validator and tests must distinguish:
- Default-ACL owner (`defaclrole`).
- Actual grantee (from exploded ACL).
- Object type (`defaclobjtype`).
- Schema (`defaclnamespace`).
- Privilege.
- Grant option.

The disposable PostgreSQL suite creates five negative fixtures: a table,
sequence, and routine default grant to a runtime-like role; a default grant to
`PUBLIC`; and a `WITH GRANT OPTION` default grant. It snapshots the complete
provider-owned default state before migrations, applies the canonical
migrations, snapshots again, and compares owner, namespace, object type,
grantee, privilege, and grant-option tuples exactly.

## Runtime trigger and effective-privilege proof

The trigger tests revoke PUBLIC EXECUTE on both SQAG trigger functions, then
use a non-superuser runtime-like role with only database CONNECT, schema USAGE,
and the exact table privileges needed by the fixture. A permitted
`sqag_feedback` UPDATE executes the installed migrated trigger and must return
SQLSTATE `P0001` with the exact immutable-record message while leaving the row
unchanged. Direct calls to both trigger functions run under `SET ROLE` and
must fail specifically with SQLSTATE `42501`.

The runtime table proof constructs the expected set directly from the manifest
as `(schema_name, table_name, privilege_type, is_grantable)` and compares it
with the complete effective set. It separately checks database CONNECT,
database CREATE, database TEMPORARY after PUBLIC revocation, schema USAGE,
schema CREATE, all five forbidden tables, unexpected tables, missing grants,
swapped grants, and grant options. It does not use aggregate row counts as
acceptance evidence.

## Deterministic cleanup and discovery receipt

Every temporary role, object/schema/database grant, PUBLIC ACL change, routine
ACL change, and default-ACL fixture registers cleanup immediately after the
change succeeds. Cleanup explicitly revokes default privileges, memberships,
database/schema/object privileges, runs `DROP OWNED`, and drops the role; any
failure is raised as a test failure. Role resets occur in `finally` blocks.
Each disposable database is audited after its test cleanups, and the class
teardown performs the final cluster audit for leftover roles, memberships,
default ACL rows, and test databases.

The deterministic discovery receipt for this amendment is:

| Receipt item | Count |
|---|---:|
| Discovered test methods | 55 |
| Static and validator methods | 37 |
| PostgreSQL methods | 18 |
| Hosted executions | 55 |
| Hosted skips | 0 |
| Unique locked requirement IDs | 38 (`R01`-`R38`) |

The ten adversarial manifest fixtures are regression cases, not additional
requirement IDs. Recalculate the receipt with the repository's unittest
discovery before updating this section or the PR body:

```powershell
python -c "import unittest; s=unittest.defaultTestLoader.loadTestsFromName('tests.test_runtime_privilege_contract'); print(s.countTestCases())"
```

## Boundary A versus Boundary B versus #160

| Boundary | Scope | Status |
|---|---|---|
| **Boundary A** | Repository privilege-contract manifest, validator, CI integration, and disposable-PostgreSQL tests. | This PR. |
| **Boundary B** | Staged live database alignment: create dormant `sqag_runtime`, apply exact grants and ACL changes, revoke PUBLIC TEMPORARY, revoke PUBLIC EXECUTE on SQAG trigger functions, verify, and stop with NOLOGIN. | Blocked pending exact-head acceptance of Boundary A. |
| **#160** | Internal-alpha activation: runtime password generation, LOGIN activation, Coolify configuration, deployment, Google login, and hosted smoke. | Blocked pending independent verification of Boundary B. |

Boundary A performs no live database, provider, Coolify, credential, or deployment mutation.

## Staged multi-authority Boundary B sequence

Boundary B is not one atomic cross-authority operation because role creation and object grants use different authorities:

1. **Read-only preflight** and baseline snapshot.
2. **Provider authority** creates `sqag_runtime` as dormant `NOLOGIN`, password null.
3. **Object owner** (`sqag_migrator`) grants exact database, schema, and 11-table privileges.
4. **Object owner** revokes `PUBLIC TEMPORARY`.
5. **Object owner** revokes `PUBLIC EXECUTE` on the two SQAG-owned trigger functions.
6. **Metadata and effective-privilege verification** runs.
7. **Independent exact-state review** accepts or rejects the result.
8. Stop with `sqag_runtime` still `NOLOGIN`, no password, and no credential.

On failure, compensate in reverse order.

## Rollback limitations

A full rollback of Boundary B is limited in one critical respect: after a `DROP ROLE sqag_runtime` followed by a `CREATE ROLE sqag_runtime`, the recreated role has a different OID. Any object grants, column ACLs, or default privileges recorded against the original OID are not restored by recreating the same role name.

The staged compensation order (revoke grants in reverse before dropping the role) mitigates this for grants, but:
- Column-level ACL entries referencing the old OID are lost.
- Any default privilege that was adjusted and then reverted would reference the new OID.

For this reason, Boundary B does not drop the runtime role on failure unless absolutely necessary. It compensates by revoking grants in reverse order while preserving the original role OID.

## Running local validation

```bash
# Validate the manifest statically
python scripts/validate_runtime_privilege_contract.py

# Run all contract tests (static tests always run; PostgreSQL tests skip when no test service is configured)
python -m unittest tests.test_runtime_privilege_contract -v

# Run the full Python test suite
python -m unittest discover -s tests -v

# Run the migration integration tests (requires SQAG_TEST_POSTGRES_HOST/Port/User)
python -m unittest tests.test_postgres_migration_ledger -v
```

## CI enforcement

The `Validate app` job in `.github/workflows/ci.yml` runs:
- Manifest/schema validation via `python scripts/validate_runtime_privilege_contract.py`.
- All Python tests via `python -m unittest discover -s tests`, which includes the contract tests.
- The disposable PostgreSQL integration tests (both migration-ledger and privilege-contract).

A contract failure fails the CI job. No live database or provider mutations occur in CI.
