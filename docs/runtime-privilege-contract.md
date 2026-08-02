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

- `tables.runtime_accessible`: exactly 11 tables. Each entry records the exact currently authorized table-level `SELECT`, `INSERT`, `UPDATE`, `DELETE` boolean privileges. The PostgreSQL 17 effective proof also enumerates `TRUNCATE`, `REFERENCES`, `TRIGGER`, and `MAINTAIN` and requires them to remain false unless a future Design Lock authorizes them.
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
| `sqag_quote_publication_artifacts` | mutable | SELECT, INSERT, DELETE; column-only UPDATE on `checksum_sha256` |

The publication-artifact table has no table-level `UPDATE`. The only update
authority is the manifest's exact column grant on `checksum_sha256`; all other
columns remain denied, no grant option is allowed, and PUBLIC or membership
authority must not supply table-level UPDATE.

The direct runtime grant total remains exactly 37: 34 table grants, one column
grant, one database grant, and one schema grant.

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

## PostgreSQL 17 effective privilege proofs

### Runtime membership and provider creator-admin control

The runtime has zero `memberships_as_member`, zero `inherited_roles`, zero
`set_assumable_roles`, and zero `membership_derived_privileges`. It therefore
has no privilege-bearing membership, inherited authority, SET-role path, or
database, schema, table, column, sequence, or routine authority derived from a
role membership.

PostgreSQL 17 automatically records one creator-admin edge when a
non-superuser `CREATEROLE` provider administrator creates `sqag_runtime`. The
manifest represents that administrative control relationship separately from
runtime membership as one closed-schema `provider_control_edges` entry:

```json
{
  "parent_role": "sqag_runtime",
  "member_role": "neondb_owner",
  "grantor": "cloud_admin",
  "admin_option": true,
  "inherit_option": false,
  "set_option": false,
  "classification": "postgresql17_creator_admin_control",
  "security_rationale": "PostgreSQL 17 system-generated creator-admin control for the provider administrator; it grants no privilege, inheritance, or SET-role path to sqag_runtime."
}
```

This edge gives the already-privileged provider administrator administrative
control over the dormant runtime role. It gives `sqag_runtime` no privilege,
inheritance, or SET-role path and must never be described as runtime privilege
or runtime membership inheritance. Any second runtime edge, runtime-as-member
edge, different identity or grantor, mutated option, recursive path, protected
SQAG/provider role, unknown classification, missing field, duplicate edge, or
membership-derived effective privilege fails closed.

The manifest and validator bind exactly thirteen canonical verification-query
keys. Each key has an independent repository-owned executable-token contract;
candidate manifest text cannot redefine its expected query. The PostgreSQL
integration contract executes every key once, checks the exact returned column
names and order, and applies the row-cardinality rule for its disposable
fixture. The generic shape fixture has no runtime edge; the dedicated
non-superuser `CREATEROLE` fixture produces exactly one automatic edge and is
the acceptance proof for the complete tuple.

### Membership-query narrative contract

The membership query projects the exact aliases `role`, `member`, `grantor`,
`admin_option`, `inherit_option`, and `set_option`. Its evaluator consumes the
complete unfiltered membership result, validates the `grantor`, and
distinguishes ADMIN authority from INHERIT and SET authority. No column may be
omitted, no value may be supplied by a substituted default, and no unexpected
row may be filtered away.

Every well-formed row is classified before validation. The only authorised
protected-role row is the exact PostgreSQL 17 creator-admin control tuple shown
above. Any other row containing `sqag_runtime`, `sqag_migrator`, `sqag_app`,
`neondb_owner`, `neon_superuser`, or `cloud_admin` in the parent, member, or
grantor position fails closed, including unknown participants, duplicate rows,
unexpected ADMIN, INHERIT, or SET authority, and recursive protected-role
paths. A membership row whose parent, member, and grantor contain no protected
participant and which creates no recursive protected-role authority is outside
this contract, but it remains in the complete result and is classified rather
than silently discarded by the protected-role branch.

The disposable creator-admin fixture preserves every queried membership row
and its graph position. For validator evaluation only, it copies the observed
automatic edge to a dedicated existing fixture-grantor identity; all unrelated
rows remain unchanged and non-protected. The regression separately proves that
using that fixture grantor on an otherwise unrelated row is rejected.

The evaluator boundary also has direct malformed-row coverage. One bounded
test method exercises non-list row containers, non-object rows, each missing
required key, unexpected keys, invalid `role`/`member`/`grantor` values, invalid
`admin_option`/`inherit_option`/`set_option` values, and mixed malformed rows.
Each case asserts the existing row-indexed fail-closed error category. This is
test evidence against the unchanged validator, not a production correction or
live-system evidence.

| Key | Exact result columns | Fixture cardinality |
|---|---|---:|
| `database_acl` | `datacl` | 1 |
| `schema_acl` | `nspacl` | 1 |
| `table_acl` | `relname`, `relacl` | 16 |
| `routine_acl` | `proname`, `identity_arguments`, `prokind`, `prosecdef`, `proacl`, `proowner`, `owner`, `has_trigger_dependency` | 2 |
| `default_acl` | `owner`, `namespace`, `object_type`, `grantee`, `privilege_type`, `is_grantable` | 1 |
| `role_attributes` | `rolname`, `rolsuper`, `rolinherit`, `rolcreaterole`, `rolcreatedb`, `rolcanlogin`, `rolreplication`, `rolbypassrls`, `rolconnlimit`, `password_is_null` | 2 |
| `role_memberships` | `role`, `member`, `grantor`, `admin_option`, `inherit_option`, `set_option` | 0 in the generic shape fixture; 1 exact runtime edge in the creator fixture |
| `sequence_acl` | `relname`, `relacl` | 0 |
| `effective_runtime_database_privileges` | `privilege_type`, `effective`, `is_grantable` | 3 |
| `effective_runtime_table_privileges` | `schema_name`, `table_name`, `privilege_type`, `effective`, `is_grantable` | 128 |
| `effective_runtime_column_privileges` | `schema_name`, `table_name`, `column_name`, `privilege_type`, `effective`, `is_grantable` | fixture columns x 4 |
| `effective_runtime_schema_privileges` | `privilege_type`, `effective`, `is_grantable` | 2 |
| `effective_runtime_routine_privileges` | `routine_name`, `effective` | 2 |

The effective database proof covers `CONNECT`, `CREATE`, and `TEMPORARY`; the
locked runtime result is `true/false/false`, with all three grantable flags
false. The effective schema proof covers `USAGE` and `CREATE` with the locked
result `true/false`, again with both grantable flags false. Both queries use
PostgreSQL's `has_*_privilege(... 'WITH GRANT OPTION')` evaluation, so direct,
PUBLIC, membership-derived, owner-derived, and grant-option authority is
evaluated for the exact privilege rather than inferred from ACL text.

The table proof evaluates every `public.sqag_*` table against the ordered
PostgreSQL 17 set `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`,
`REFERENCES`, `TRIGGER`, `MAINTAIN`. The column proof evaluates every
non-dropped user column against `SELECT`, `INSERT`, `UPDATE`, and `REFERENCES`.
Expected effective column rows are exactly those implied by the locked
table-level matrix plus the one explicit `UPDATE(checksum_sha256)` tuple on
`sqag_quote_publication_artifacts`. Column grants cannot add authority to a
forbidden table, an unauthorized table-level privilege, another
publication-artifact column, or a grant option.

## Grantee-aware default-privilege verification

PostgreSQL's `ALTER DEFAULT PRIVILEGES` stores grants in `pg_catalog.pg_default_acl`. The `defaclrole` column records which role granted the defaults, not which role receives them. The actual grantee is embedded in the `defaclacl` ACL array.

The canonical verification query uses one grantee-expanded `CROSS JOIN
LATERAL aclexplode(...)` and resolves named grantees through a nullable role
join. It does not cast an absent role name to `regrole`:

```sql
select owner.rolname as owner,
       coalesce(ns.nspname, '<global>') as namespace,
       d.defaclobjtype as object_type,
       case when expanded.grantee = 0 then 'PUBLIC'
            else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text)
       end as grantee,
       expanded.privilege_type,
       expanded.is_grantable
from pg_catalog.pg_default_acl d
join pg_catalog.pg_roles owner on owner.oid = d.defaclrole
left join pg_catalog.pg_namespace ns on ns.oid = d.defaclnamespace
cross join lateral pg_catalog.aclexplode(d.defaclacl) expanded
left join pg_catalog.pg_roles grantee_role
  on grantee_role.oid = expanded.grantee and expanded.grantee <> 0
where d.defaclobjtype in ('r', 'S', 'f', 'n', 'T')
order by owner, namespace, object_type, grantee, expanded.privilege_type, expanded.is_grantable;
```

Checking only `defaclrole = 'sqag_runtime'` is insufficient and must not be used as the sole acceptance proof. The validator and tests must distinguish:
- Default-ACL owner (`defaclrole`).
- Actual grantee (from exploded ACL).
- Object type (`defaclobjtype`).
- Schema (`defaclnamespace`).
- Privilege.
- Grant option.

The canonical object-class boundary is complete: `r` tables, `S` sequences,
`f` functions, `n` schemas, and `T` types. The disposable PostgreSQL suite
creates adversarial direct, PUBLIC, membership-derived, grant-option,
additional, missing, wrong-grantee, wrong-owner, wrong-object-type, and
equal-count/wrong-tuple fixtures across that boundary. It snapshots complete
provider/default-ACL state before migrations, registers cleanup before each
mutation, applies the canonical migrations, restores the state, and compares
owner, namespace, object type, grantee, privilege, and grant-option tuples
exactly. A separate provider fixture creates non-vacuous baseline tuples for
all five object classes owned by `neondb_owner`, requires them to be present,
and rejects an empty baseline as evidence.

## Bounded verification-query validation

The validator does not accept feature words found by raw substring search. It
uses a bounded SQL lexer that skips line comments, supports nested block
comments, and treats single-quoted strings, double-quoted identifiers, and
dollar-quoted bodies as opaque tokens. Unterminated quoted or comment regions
fail closed. After lexing, each verification query must be exactly one
executable read-only `SELECT` statement, with at most one terminal semicolon;
additional statements and write, DDL, transaction, session, or procedural
keywords are rejected.

The default-ACL query must have the exact six-column projection
`owner, namespace, object_type, grantee, privilege_type, is_grantable`, one
`CROSS JOIN LATERAL aclexplode(...)`, the `r`/`S`/`f`/`n`/`T` object-type
boundary, the PUBLIC case mapping, and deterministic ordering. The
role-attribute query must join `pg_roles` to the authoritative `pg_authid`
catalog and project only the boolean `password_is_null` assertion; it never
returns the password field. The routine query must have
the exact eight-column projection
`proname, identity_arguments, prokind, prosecdef, proacl, proowner, owner,
has_trigger_dependency`, the complete public-schema routine boundary, and
deterministic identity-argument ordering. The PostgreSQL query-shape evidence
executes all thirteen canonical queries, asserts the exact returned column
names, and applies the documented cardinality rules.

The remaining effective queries have the same exact-token binding. In
particular, the membership query must preserve the complete six-field contract
and aliases defined above; the table query must retain all eight privilege
literals; the column query must retain the table, column, and privilege
predicates; and database/schema grantability must remain a real
privilege-specific `WITH GRANT OPTION` check. Invalid SQL is still rejected by
PostgreSQL execution even when a candidate happens to contain expected words.

Adversarial static fixtures cover comment-token, string-literal, and
dollar-quote no-op attempts; multiple statements; write statements;
unterminated lexical regions; wrong projections; wrong catalog relations;
missing lateral expansion; missing grant-option output; and missing PUBLIC
mapping. These fixtures prove that validation tokens must be executable query
structure.

## Runtime trigger and effective-privilege proof

The trigger tests revoke PUBLIC EXECUTE on both SQAG trigger functions, then
use a non-superuser runtime-like role with only database CONNECT, schema USAGE,
and the exact table privileges needed by the fixture. A permitted
`sqag_feedback` UPDATE executes the installed migrated trigger and must return
SQLSTATE `P0001` with the exact immutable-record message while leaving the row
unchanged. Direct calls to both trigger functions run under `SET ROLE` and
must fail specifically with SQLSTATE `42501`.

The runtime table proof constructs the expected effective set directly from
the manifest as `(schema_name, table_name, privilege_type, is_grantable)` and
compares it with the complete effective set. The table and column adversarial
matrices independently exercise direct, grant-option, PUBLIC, and
membership-derived authority for every PostgreSQL 17 table privilege and each
column privilege. They also reject one-column/one-table substitutions, equal
aggregate counts with wrong distribution, and column grants hidden by table
authority. The disposable-database tests start from valid matrices and reject
these named mismatch classes:

1. a missing privilege tuple;
2. an extra privilege tuple;
3. a privilege moved from one table to another;
4. a missing tuple offset by an unrelated extra tuple;
5. a privilege on a forbidden table;
6. a real `WITH GRANT OPTION` ACL;
7. a privilege on an unexpected table;
8. a completely missing accessible table;
9. the wrong privilege type on an otherwise correct table; and
10. equal aggregate row counts with the wrong tuple distribution.

The proof also checks database `CONNECT`, `CREATE`, and `TEMPORARY`, schema
`USAGE` and `CREATE`, their grant options, direct/PUBLIC/membership-derived
authority, cross-privilege substitutions, and all five forbidden tables. It
asserts exact tuples for each negative case, so aggregate counts are never
acceptance evidence.

## PUBLIC ACL baseline and cleanup proof

Before any disposable test mutates a PUBLIC database privilege or routine
EXECUTE ACL, the suite captures the exact baseline values. Cleanup restores
the captured value, asserts the restored state, and records a restoration
receipt before dropping a fixture routine. The final database cleanup audit
compares every remaining PUBLIC database and routine ACL to its captured
baseline and fails if a routine disappeared without an explicit restoration
receipt. The early-failure regression raises after a deliberate ACL mutation
and proves its `finally` restoration runs; the omitted-restoration regression
proves the final audit itself detects a drift. This catches both a missing
cleanup registration and an omitted restoration step.

## Requirement-to-evidence map

Each locked requirement has exactly one named evidence method. The map is
explicit and independently checked for exact `R01`-`R38` ordering, discoverable
method references, and parity with this documentation table.

| ID | Requirement | Evidence |
|---|---|---|
| R01 | Schema version is locked to v1. | `ManifestStructureTest.test_schema_version_is_1` |
| R02 | Manifest binds to the canonical repository revision and tree. | `ManifestStructureTest.test_repository_binding` |
| R03 | Runtime role attributes are dormant and restricted. | `ManifestStructureTest.test_runtime_role_attributes` |
| R04 | The runtime has no privilege-bearing membership, inherited role, SET-role path or runtime-as-member edge; exactly one PostgreSQL-17 provider creator-admin control edge is permitted with ADMIN true, INHERIT false and SET false. | `ManifestStructureTest.test_runtime_role_membership_contract_is_exact` |
| R05 | Migrator cannot create roles. | `ManifestStructureTest.test_migrator_cannot_create_roles` |
| R06 | Forbidden maintenance role is classified. | `ManifestStructureTest.test_sqag_maintenance_is_forbidden` |
| R07 | Production migrations, digests, and table bindings match repository authority. | `ManifestStructureTest.test_production_migrations_match_repository` |
| R08 | The complete table inventory is 16 objects. | `ManifestStructureTest.test_all_tables_union_is_16` |
| R09 | The runtime-accessible table set is exact. | `ManifestStructureTest.test_runtime_accessible_table_set_is_exact` |
| R10 | The forbidden table set is exact. | `ManifestStructureTest.test_forbidden_table_set_is_exact` |
| R11 | Every runtime table privilege tuple matches the locked matrix. | `ManifestStructureTest.test_runtime_accessible_table_privileges_are_exact` |
| R12 | No user-defined public sequence has runtime privilege. | `ManifestStructureTest.test_sequence_count_is_0` |
| R13 | Routine inventory contains the two SQAG routines and one bounded provider exception. | `ManifestStructureTest.test_routine_inventory_is_two_sqag_plus_one_provider` |
| R14 | SQAG routines are trigger-only invoker routines with no direct runtime grant. | `ManifestStructureTest.test_sqag_trigger_routines_are_trigger_only` |
| R15 | Database and schema ACL targets are exact. | `ManifestStructureTest.test_database_and_schema_acl_targets` |
| R16 | Default-privilege targets are grantee-aware and provider defaults are unchanged. | `ManifestStructureTest.test_default_privileges_are_grantee_aware` |
| R17 | All canonical verification-query keys are present. | `ManifestStructureTest.test_verification_queries_are_complete` |
| R18 | The unmodified manifest passes strict validation. | `ValidatorStaticTest.test_valid_manifest_passes` |
| R19 | Duplicate JSON keys fail closed. | `ValidatorStaticTest.test_duplicate_json_key_fixture_fails` |
| R20 | Recursive unknown-key violations fail closed. | `ValidatorStaticTest.test_nested_unknown_key_fixture_fails` |
| R21 | Provider routine exception set cannot be broadened. | `ValidatorStaticTest.test_extra_provider_exception_fixture_fails` |
| R22 | Missing migration table bindings fail closed. | `ValidatorStaticTest.test_missing_migration_table_binding_fixture_fails` |
| R23 | Contradictory PUBLIC database ACL values fail closed. | `ValidatorStaticTest.test_incorrect_public_temporary_fixture_fails` |
| R24 | Contradictory runtime role attributes fail closed. | `ValidatorStaticTest.test_incorrect_runtime_connection_limit_fixture_fails` |
| R25 | Canonical verification queries have bounded lexical shape. | `ValidatorStaticTest.test_canonical_verification_queries_pass_lexical_shape` |
| R26 | Comment, literal, and dollar-quote no-op tokens cannot satisfy query validation. | `ValidatorStaticTest.test_query_lexer_rejects_comment_literal_and_dollar_noops` |
| R27 | Verification queries are one executable read-only SELECT statement. | `ValidatorStaticTest.test_query_lexer_rejects_multiple_and_write_statements` |
| R28 | Canonical query result projections are exact. | `PostgreSQLContractIntegrationTest.test_canonical_query_result_shapes_and_non_empty_fixture_rows` |
| R29 | PostgreSQL routine inventory covers the complete public routine boundary. | `PostgreSQLContractIntegrationTest.test_actual_routine_inventory_has_no_stock_provider_exception` |
| R30 | Provider routine exception is bounded and PUBLIC-only. | `PostgreSQLContractIntegrationTest.test_provider_show_db_tree_is_only_bounded_exception` |
| R31 | Migrated trigger dependencies match routine classification. | `PostgreSQLContractIntegrationTest.test_trigger_dependencies_match_migrated_routine_classification` |
| R32 | Runtime table operations still enforce trigger invariants after PUBLIC EXECUTE revoke. | `PostgreSQLContractIntegrationTest.test_trigger_enforcement_runs_under_runtime_authority_after_public_revoke` |
| R33 | Direct runtime calls to both trigger functions fail with 42501. | `PostgreSQLContractIntegrationTest.test_direct_runtime_calls_to_both_trigger_functions_are_denied_42501` |
| R34 | The positive effective privilege matrix matches the manifest exactly. | `PostgreSQLContractIntegrationTest.test_effective_runtime_table_privileges_match_manifest_exactly` |
| R35 | Every required matrix mismatch class is isolated and rejected. | `PostgreSQLContractIntegrationTest.test_effective_runtime_matrix_missing_privilege_is_rejected` |
| R36 | Effective database and schema privilege boundaries are exact. | `PostgreSQLContractIntegrationTest.test_public_connect_and_database_schema_acl_posture_is_exact` |
| R37 | Default ACL adversarial grants and real grant options are detected. | `PostgreSQLContractIntegrationTest.test_default_acl_adversarial_fixtures_detect_runtime_public_and_grant_option` |
| R38 | Cleanup restores PUBLIC ACL baselines and surfaces early failures. | `PostgreSQLContractIntegrationTest.test_early_failure_cleanup_restores_public_acl_baseline` |

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
| Discovered test methods | 161 |
| Static and validator methods | 104 |
| PostgreSQL methods | 53 |
| Requirement-map and documentation parity methods | 4 |
| Hosted executions | 161 |
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
