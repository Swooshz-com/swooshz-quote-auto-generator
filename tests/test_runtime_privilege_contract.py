"""Runtime privilege contract tests.

Deterministic discovery receipt for this amendment:
  discovered methods: 260
  static and validator methods: 164
  PostgreSQL methods: 92
  requirement-map and documentation parity methods: 4
  hosted executions: 260
  hosted skips: 0
  unique locked requirement IDs: 38 (R01-R38)

The locked proof points are requirement identifiers, not a test count. The
static section also contains ten independent adversarial manifest fixtures
(A01-A10), and the PostgreSQL section uses disposable databases, actual
migrated objects, and non-superuser SET ROLE sessions.
"""

from __future__ import annotations

import copy
import hashlib
import io
import inspect
import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from typing import Any, Iterator, cast
from unittest import mock

import scripts.validate_runtime_privilege_contract as contract_validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_runtime_privilege_contract import (  # noqa: E402
    LOCKED_PRIVILEGE_MATRIX,
    SQLLexError,
    lex_sql,
    validate_manifest_strictly,
)
from webapp.postgres_migrations import (  # noqa: E402
    EXPECTED_ROUTINES,
    MIGRATION_FILE_NAMES,
    MIGRATION_TABLES,
    apply_postgres_migrations,
    migration_manifest,
)
from webapp.server import DatabaseSqagStorage, PostgresConnectionAdapter  # noqa: E402


MANIFEST_PATH = ROOT / "docs" / "runtime-privilege-contract.json"
IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
RUNTIME_TABLES = frozenset(load_name for load_name in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["tables"]["runtime_accessible"])
FORBIDDEN_TABLES = frozenset(json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["tables"]["runtime_forbidden"])
ALL_TABLES = RUNTIME_TABLES | FORBIDDEN_TABLES
TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
    "MAINTAIN",
)
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
DATABASE_PRIVILEGES = ("CONNECT", "CREATE", "TEMPORARY")
SCHEMA_PRIVILEGES = ("USAGE", "CREATE")
DEFAULT_ACL_OBJECT_TYPES = ("r", "S", "f", "n", "T")
EXPLICIT_COLUMN_PRIVILEGES = {
    "sqag_quote_publication_artifacts": {
        "UPDATE": ("checksum_sha256",),
    },
}

# Independent query-shape authority.  These expectations are deliberately
# repository-owned test data, not generated from candidate manifest values.
CANONICAL_QUERY_KEYS = (
    "database_acl",
    "schema_acl",
    "table_acl",
    "routine_acl",
    "default_acl",
    "role_attributes",
    "role_memberships",
    "sequence_acl",
    "effective_runtime_database_privileges",
    "effective_runtime_table_privileges",
    "effective_runtime_column_privileges",
    "effective_runtime_schema_privileges",
    "effective_runtime_routine_privileges",
    "effective_runtime_parameter_privileges",
    "view_acl",
    "system_relation_acl",
)
CANONICAL_QUERY_COLUMNS = {
    "database_acl": ["database_name", "database_owner", "datallowconn", "datconnlimit", "datacl", "acl_entries"],
    "schema_acl": ["schema_name", "schema_owner", "database_owner", "acl_entries"],
    "table_acl": [
        "schema_name",
        "relname",
        "relacl",
        "table_columns",
        "table_constraints",
        "index_contracts",
        "trigger_bindings",
        "rule_bindings",
    ],
    "routine_acl": [
        "schema_name",
        "routine_name",
        "identity_arguments",
        "routine_kind",
        "security_definer",
        "owner",
        "language",
        "routine_definition",
        "routine_configuration",
        "acl_entries",
        "has_trigger_dependency",
        "trigger_bindings",
    ],
    "default_acl": ["owner", "namespace", "object_type", "grantee", "privilege_type", "is_grantable"],
    "role_attributes": [
        "rolname",
        "rolsuper",
        "rolinherit",
        "rolcreaterole",
        "rolcreatedb",
        "rolcanlogin",
        "rolreplication",
        "rolbypassrls",
        "rolconnlimit",
        "password_is_null",
    ],
    "role_memberships": [
        "role",
        "member",
        "grantor",
        "admin_option",
        "inherit_option",
        "set_option",
    ],
    "sequence_acl": [
        "schema_name",
        "sequence_name",
        "sequence_acl",
        "privilege_type",
        "effective",
        "is_grantable",
    ],
    "effective_runtime_database_privileges": ["privilege_type", "effective", "is_grantable"],
    "effective_runtime_table_privileges": [
        "schema_name",
        "table_name",
        "relation_kind",
        "relation_persistence",
        "acl_entries",
        "owner",
        "owner_select",
        "visible_column_count",
        "column_contract",
        "row_security_enabled",
        "row_security_forced",
        "has_inheritance_descendants",
        "has_inheritance_parents",
        "is_partition",
        "partition_bound",
        "privilege_type",
        "effective",
        "is_grantable",
    ],
    "effective_runtime_column_privileges": [
        "schema_name",
        "table_name",
        "column_name",
        "acl_entries",
        "privilege_type",
        "effective",
        "is_grantable",
    ],
    "effective_runtime_schema_privileges": [
        "schema_name",
        "privilege_type",
        "effective",
        "is_grantable",
    ],
    "effective_runtime_routine_privileges": [
        "schema_name",
        "routine_name",
        "identity_arguments",
        "routine_kind",
        "privilege_type",
        "direct_runtime_execute",
        "public_execute",
        "effective",
        "is_grantable",
    ],
    "effective_runtime_parameter_privileges": [
        "parameter_name",
        "acl_entries",
        "startup_defaults",
        "effective_set",
        "effective_alter_system",
        "set_grantable",
        "alter_system_grantable",
    ],
    "view_acl": [
        "schema_name",
        "relation_name",
        "relation_kind",
        "owner",
        "relation_acl",
        "acl_entries",
        "column_acl_entries",
        "view_definition",
        "view_dependencies",
        "view_columns",
        "relation_options",
        "view_security",
        "runtime_privileges",
        "runtime_select",
        "runtime_select_grantable",
    ],
    "system_relation_acl": [
        "schema_name",
        "relation_name",
        "relation_kind",
        "current_acl_entries",
        "initial_acl_entries",
        "initial_privilege_types",
    ],
}
DEFAULT_ACL_SNAPSHOT_SQL = """
select owner_role.rolname as owner_name,
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
where d.defaclobjtype in ('r', 'S', 'f', 'n', 'T')
order by owner_name, namespace, object_type, grantee, expanded.privilege_type, expanded.is_grantable
"""

PRODUCTION_PROVIDER_CONTROL_EDGE = {
    "parent_role": "sqag_runtime",
    "member_role": "neondb_owner",
    "grantor": "cloud_admin",
    "admin_option": True,
    "inherit_option": False,
    "set_option": False,
    "classification": "postgresql17_creator_admin_control",
    "security_rationale": (
        "PostgreSQL 17 system-generated creator-admin control for the provider administrator; "
        "it grants no privilege, inheritance, or SET-role path to sqag_runtime."
    ),
}
PRODUCTION_PROVIDER_CONTROL_ROW = {
    "role": "sqag_runtime",
    "member": "neondb_owner",
    "grantor": "cloud_admin",
    "admin_option": True,
    "inherit_option": False,
    "set_option": False,
}


def postgres_test_conninfo(database_name: str = "postgres") -> str | None:
    host = os.getenv("SQAG_TEST_POSTGRES_HOST", "").strip()
    port = os.getenv("SQAG_TEST_POSTGRES_PORT", "").strip()
    user = os.getenv("SQAG_TEST_POSTGRES_USER", "").strip()
    if not host or not port or not user:
        return None
    return f"host={host} port={port} user={user} dbname={database_name}"


def _row_dict(row: object, key: str) -> object:

    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, TypeError, IndexError):
        return None

FILE_FDW_UNAVAILABLE_REASON = (
    "PostgreSQL file_fdw is not available in the isolated PostgreSQL test service"
)


def _materialize_file_fdw_fixture(available: bool, factory: Any) -> Any:
    if not available:
        raise unittest.SkipTest(FILE_FDW_UNAVAILABLE_REASON)
    return factory()


def load_manifest() -> dict[str, Any]:
    with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _complete_classified_authority_evidence(
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the complete valid H16-H21 classified table/column snapshot."""

    tables = manifest["tables"]
    accessible_tables = tables["runtime_accessible"]
    forbidden_tables = tables["runtime_forbidden"]
    column_manifest = manifest.get("column_privileges", {})
    column_contracts = contract_validator.classified_table_column_contract()
    table_rows: list[dict[str, Any]] = []
    column_rows: list[dict[str, Any]] = []

    classified_entries = [
        *accessible_tables.items(),
        *((table_name, {}) for table_name in forbidden_tables),
    ]
    for table_name, table_entry in classified_entries:
        table_privileges = (
            table_entry.get("privileges", {})
            if isinstance(table_entry, dict)
            else {}
        )
        table_acl = [
            {
                "grantee": "sqag_migrator",
                "grantor": "sqag_migrator",
                "privilege_type": privilege,
                "is_grantable": False,
            }
            for privilege in TABLE_PRIVILEGES
        ]
        table_acl.extend(
            {
                "grantee": "sqag_runtime",
                "grantor": "sqag_migrator",
                "privilege_type": privilege,
                "is_grantable": False,
            }
            for privilege in TABLE_PRIVILEGES
            if table_privileges.get(privilege.lower()) is True
        )

        explicit_columns: dict[str, set[str]] = {}
        explicit_table = column_manifest.get(table_name, {})
        if isinstance(explicit_table, dict):
            for privilege, columns in explicit_table.items():
                if type(privilege) is not str or type(columns) is not list:
                    continue
                for column_name in columns:
                    if type(column_name) is str:
                        explicit_columns.setdefault(column_name, set()).add(privilege.upper())

        expected_contract = copy.deepcopy(column_contracts[table_name])
        for privilege in TABLE_PRIVILEGES:
            table_rows.append(
                {
                    "schema_name": "public",
                    "table_name": table_name,
                    "relation_kind": "r",
                    "relation_persistence": "p",
                    "acl_entries": copy.deepcopy(table_acl),
                    "owner": "sqag_migrator",
                    "owner_select": True,
                    "visible_column_count": len(expected_contract),
                    "column_contract": copy.deepcopy(expected_contract),
                    "row_security_enabled": False,
                    "row_security_forced": False,
                    "has_inheritance_descendants": False,
                    "has_inheritance_parents": False,
                    "is_partition": False,
                    "partition_bound": None,
                    "privilege_type": privilege,
                    "effective": table_privileges.get(privilege.lower()) is True,
                    "is_grantable": False,
                }
            )

        for column in expected_contract:
            column_name = str(column["name"])
            explicit_privileges = explicit_columns.get(column_name, set())
            column_acl = [
                {
                    "grantee": "sqag_runtime",
                    "grantor": "sqag_migrator",
                    "privilege_type": privilege,
                    "is_grantable": False,
                }
                for privilege in sorted(explicit_privileges)
            ]
            for privilege in COLUMN_PRIVILEGES:
                column_rows.append(
                    {
                        "schema_name": "public",
                        "table_name": table_name,
                        "column_name": column_name,
                        "acl_entries": copy.deepcopy(column_acl),
                        "privilege_type": privilege,
                        "effective": (
                            table_privileges.get(privilege.lower()) is True
                            or privilege in explicit_privileges
                        ),
                        "is_grantable": False,
                    }
                )

    return table_rows, column_rows


def _quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"unsafe test identifier: {identifier!r}")
    return f'"{identifier}"'


def _fixture_json(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=True, sort_keys=True)


REQUIREMENT_IDS = tuple(f"R{index:02d}" for index in range(1, 39))
REQUIREMENT_EVIDENCE: dict[str, dict[str, str]] = {
    "R01": {"requirement": "Schema version is locked to v1.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_schema_version_is_1"},
    "R02": {"requirement": "Manifest binds to the canonical repository revision and tree.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_repository_binding"},
    "R03": {"requirement": "Runtime role attributes are dormant and restricted.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_runtime_role_attributes"},
    "R04": {"requirement": "The runtime has no privilege-bearing membership, inherited role, SET-role path or runtime-as-member edge; exactly one PostgreSQL-17 provider creator-admin control edge is permitted with ADMIN true, INHERIT false and SET false.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_runtime_role_membership_contract_is_exact"},
    "R05": {"requirement": "Migrator cannot create roles.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_migrator_cannot_create_roles"},
    "R06": {"requirement": "Forbidden maintenance role is classified.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_sqag_maintenance_is_forbidden"},
    "R07": {"requirement": "Production migrations, digests, and table bindings match repository authority.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_production_migrations_match_repository"},
    "R08": {"requirement": "The complete table inventory is 16 objects.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_all_tables_union_is_16"},
    "R09": {"requirement": "The runtime-accessible table set is exact.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_runtime_accessible_table_set_is_exact"},
    "R10": {"requirement": "The forbidden table set is exact.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_forbidden_table_set_is_exact"},
    "R11": {"requirement": "Every runtime table privilege tuple matches the locked matrix.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_runtime_accessible_table_privileges_are_exact"},
    "R12": {"requirement": "No user-defined public sequence has runtime privilege.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_sequence_count_is_0"},
    "R13": {"requirement": "Routine inventory contains the two SQAG routines and one bounded provider exception.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_routine_inventory_is_two_sqag_plus_one_provider"},
    "R14": {"requirement": "SQAG routines are trigger-only invoker routines with no direct runtime grant.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_sqag_trigger_routines_are_trigger_only"},
    "R15": {"requirement": "Database and schema ACL targets are exact.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_database_and_schema_acl_targets"},
    "R16": {"requirement": "Default-privilege targets are grantee-aware and provider defaults are unchanged.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_default_privileges_are_grantee_aware"},
    "R17": {"requirement": "All canonical verification-query keys are present.", "evidence_type": "static", "evidence": "ManifestStructureTest.test_verification_queries_are_complete"},
    "R18": {"requirement": "The unmodified manifest passes strict validation.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_valid_manifest_passes"},
    "R19": {"requirement": "Duplicate JSON keys fail closed.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_duplicate_json_key_fixture_fails"},
    "R20": {"requirement": "Recursive unknown-key violations fail closed.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_nested_unknown_key_fixture_fails"},
    "R21": {"requirement": "Provider routine exception set cannot be broadened.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_extra_provider_exception_fixture_fails"},
    "R22": {"requirement": "Missing migration table bindings fail closed.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_missing_migration_table_binding_fixture_fails"},
    "R23": {"requirement": "Contradictory PUBLIC database ACL values fail closed.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_incorrect_public_temporary_fixture_fails"},
    "R24": {"requirement": "Contradictory runtime role attributes fail closed.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_incorrect_runtime_connection_limit_fixture_fails"},
    "R25": {"requirement": "Canonical verification queries have bounded lexical shape.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_canonical_verification_queries_pass_lexical_shape"},
    "R26": {"requirement": "Comment, literal, and dollar-quote no-op tokens cannot satisfy query validation.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_query_lexer_rejects_comment_literal_and_dollar_noops"},
    "R27": {"requirement": "Verification queries are one executable read-only SELECT statement.", "evidence_type": "static", "evidence": "ValidatorStaticTest.test_query_lexer_rejects_multiple_and_write_statements"},
    "R28": {"requirement": "Canonical query result projections are exact.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_canonical_query_result_shapes_and_non_empty_fixture_rows"},
    "R29": {"requirement": "PostgreSQL routine inventory covers the complete public routine boundary.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_actual_routine_inventory_has_no_stock_provider_exception"},
    "R30": {"requirement": "Provider routine exception is bounded and PUBLIC-only.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_provider_show_db_tree_is_only_bounded_exception"},
    "R31": {"requirement": "Migrated trigger dependencies match routine classification.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_trigger_dependencies_match_migrated_routine_classification"},
    "R32": {"requirement": "Runtime table operations still enforce trigger invariants after PUBLIC EXECUTE revoke.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_trigger_enforcement_runs_under_runtime_authority_after_public_revoke"},
    "R33": {"requirement": "Direct runtime calls to both trigger functions fail with 42501.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_direct_runtime_calls_to_both_trigger_functions_are_denied_42501"},
    "R34": {"requirement": "The positive effective privilege matrix matches the manifest exactly.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_effective_runtime_table_privileges_match_manifest_exactly"},
    "R35": {"requirement": "Every required matrix mismatch class is isolated and rejected.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_effective_runtime_matrix_missing_privilege_is_rejected"},
    "R36": {"requirement": "Effective database and schema privilege boundaries are exact.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_public_connect_and_database_schema_acl_posture_is_exact"},
    "R37": {"requirement": "Default ACL adversarial grants and real grant options are detected.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_default_acl_adversarial_fixtures_detect_runtime_public_and_grant_option"},
    "R38": {"requirement": "Cleanup restores PUBLIC ACL baselines and surfaces early failures.", "evidence_type": "postgresql", "evidence": "PostgreSQLContractIntegrationTest.test_early_failure_cleanup_restores_public_acl_baseline"},
}


def _discover_test_method_names() -> set[str]:
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    names: set[str] = set()

    def visit(item: unittest.TestSuite | unittest.TestCase) -> None:
        if isinstance(item, unittest.TestSuite):
            for child in item:
                visit(child)
        else:
            names.add(f"{item.__class__.__name__}.{item._testMethodName}")

    visit(suite)
    return names


class ManifestStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest()

    def test_schema_version_is_1(self) -> None:
        self.assertEqual(self.manifest["schema_version"], 1)

    def test_repository_binding(self) -> None:
        self.assertEqual(self.manifest["repository"], "Swooshz-com/swooshz-quote-auto-generator")
        self.assertEqual(self.manifest["canonical_source_revision"], "cc53c685ff617aaa5bf1eb24e8a62c1273570779")
        self.assertEqual(self.manifest["canonical_source_tree"], "68d67a9a08c4c3d9e86460e24060f31fdc0eaa27")

    def test_runtime_role_attributes(self) -> None:
        attrs = self.manifest["roles"]["runtime"]["attributes"]
        self.assertIs(attrs["login"], False)
        self.assertIsNone(attrs["password"])
        self.assertIs(attrs["superuser"], False)
        self.assertIs(attrs["createdb"], False)
        self.assertIs(attrs["createrole"], False)
        self.assertIs(attrs["replication"], False)
        self.assertIs(attrs["bypassrls"], False)
        self.assertIs(attrs["inherit"], True)
        self.assertEqual(attrs["connection_limit"], -1)

    def test_runtime_role_membership_contract_is_exact(self) -> None:
        runtime = self.manifest["roles"]["runtime"]
        self.assertEqual(runtime["memberships_as_member"], [])
        self.assertEqual(runtime["inherited_roles"], [])
        self.assertEqual(runtime["set_assumable_roles"], [])
        self.assertEqual(runtime["membership_derived_privileges"], [])
        self.assertEqual(runtime["provider_control_edges"], [PRODUCTION_PROVIDER_CONTROL_EDGE])
        self.assertEqual(runtime["ownership"], [])
        self.assertEqual(runtime["grant_options"], [])

    def test_retained_legacy_role_posture_is_explicit(self) -> None:
        self.assertEqual(
            self.manifest["roles"]["legacy"],
            {
                "name": "sqag_app",
                "description": "Legacy active rollback role. Retained until separately gated retirement after #160 switch and observation window.",
                "status": "retained_until_retirement",
                "retained_attributes": {
                    "login": True,
                    "superuser": False,
                    "createdb": False,
                    "createrole": False,
                    "replication": False,
                    "bypassrls": False,
                    "inherit": True,
                    "connection_limit": -1,
                },
            },
        )

    def test_migrator_cannot_create_roles(self) -> None:
        self.assertIs(self.manifest["roles"]["migrator"]["can_create_roles"], False)

    def test_sqag_maintenance_is_forbidden(self) -> None:
        self.assertEqual(self.manifest["roles"]["forbidden"], ["sqag_maintenance"])

    def test_production_migrations_match_repository(self) -> None:
        manifest_migrations = self.manifest["production_migrations"]
        repo_manifest = migration_manifest(ROOT / "migrations")
        self.assertEqual(len(manifest_migrations), len(repo_manifest))
        self.assertEqual(len(manifest_migrations), len(MIGRATION_FILE_NAMES))
        for entry, migration in zip(manifest_migrations, repo_manifest):
            self.assertEqual(entry["path"], f"migrations/{migration.migration_id}")
            self.assertEqual(entry["sha256"], migration.checksum_sha256)
            self.assertEqual(set(entry["tables"]), set(MIGRATION_TABLES[migration.migration_id]))

    def test_table_total_is_16(self) -> None:
        self.assertEqual(self.manifest["tables"]["total_count"], 16)

    def test_runtime_accessible_count_is_11(self) -> None:
        self.assertEqual(self.manifest["tables"]["rw_count"], 11)

    def test_forbidden_table_count_is_5(self) -> None:
        self.assertEqual(self.manifest["tables"]["forbidden_count"], 5)

    def test_runtime_accessible_table_set_is_exact(self) -> None:
        self.assertEqual(set(self.manifest["tables"]["runtime_accessible"]), RUNTIME_TABLES)

    def test_forbidden_table_set_is_exact(self) -> None:
        self.assertEqual(set(self.manifest["tables"]["runtime_forbidden"]), FORBIDDEN_TABLES)

    def test_all_tables_union_is_16(self) -> None:
        actual = set(self.manifest["tables"]["runtime_accessible"]) | set(self.manifest["tables"]["runtime_forbidden"])
        self.assertEqual(actual, ALL_TABLES)
        self.assertEqual(len(actual), 16)
        self.assertFalse(set(self.manifest["tables"]["runtime_accessible"]) & set(self.manifest["tables"]["runtime_forbidden"]))

    def test_runtime_accessible_table_privileges_are_exact(self) -> None:
        actual = {
            table_name: dict(entry["privileges"])
            for table_name, entry in self.manifest["tables"]["runtime_accessible"].items()
        }
        self.assertEqual(actual, LOCKED_PRIVILEGE_MATRIX)

    def test_runtime_direct_grant_total_is_exactly_38(self) -> None:
        table_grants = sum(
            privilege is True
            for table in self.manifest["tables"]["runtime_accessible"].values()
            for privilege in table["privileges"].values()
        )
        column_grants = sum(
            len(privileges)
            for table in self.manifest["column_privileges"].values()
            for privileges in table.values()
        )
        database_grants = sum(
            privilege is True
            for privilege in self.manifest["database_acl"]["sqag_runtime"].values()
        )
        schema_grants = sum(
            privilege is True
            for privilege in self.manifest["schema_acl"]["sqag_runtime"].values()
        )
        view_grants = sum(
            privilege is True
            for view in self.manifest["views"]["runtime_accessible"].values()
            for privilege in view["privileges"].values()
        )
        self.assertEqual(
            (table_grants, column_grants, database_grants, schema_grants, view_grants),
            (34, 1, 1, 1, 1),
        )
        self.assertEqual(table_grants + column_grants + database_grants + schema_grants + view_grants, 38)

    def test_runtime_direct_grant_total_is_conditional_on_legacy_view_presence(self) -> None:
        self.assertEqual(
            self.manifest["views"]["direct_runtime_grants"],
            {'legacy_absent': 37, 'legacy_present': 38},
        )

    def test_legacy_view_inventory_is_exact(self) -> None:
        views = self.manifest["views"]
        self.assertEqual(views["count"], 1)
        self.assertIs(views["legacy_optional"], True)
        self.assertIn("No public materialized view is classified", views["materialized_view_rule"])
        self.assertIn("fails closed", views["materialized_view_rule"])
        self.assertEqual(set(views["runtime_accessible"]), {"sqag_quote_artifacts"})
        entry = views["runtime_accessible"]["sqag_quote_artifacts"]
        self.assertEqual(entry["schema"], "public")
        self.assertEqual(entry["class"], "legacy_publication_backfill")
        self.assertEqual(entry["privileges"], {"select": True})
        self.assertIs(entry["bound"], True)
        self.assertEqual(
            entry["production_source"],
            "webapp.server.DatabaseSqagStorage.publish_quote_session_forensic_transaction",
        )

    def test_publication_backfill_reads_legacy_view_under_runtime_identity(self) -> None:
        source = inspect.getsource(DatabaseSqagStorage.publish_quote_session_forensic_transaction)
        self.assertIn(
            "from sqag_quote_artifacts where workspace_id = ? and session_id = ?",
            source,
        )
        self.assertIn('configured_artifact_storage_mode() == "database"', source)

    def test_boundary_b_authority_model_is_exact(self) -> None:
        boundary = self.manifest["boundary_b"]
        self.assertTrue(boundary["requires_postgresql17"])
        self.assertEqual(boundary["runtime_role"], "sqag_runtime")
        self.assertEqual(boundary["object_owner"], "sqag_migrator")
        self.assertEqual(boundary["database_owner_authority"], "database_owner")
        self.assertEqual(boundary["authority_input_model"], "variable_reference_only")
        self.assertIs(boundary["fail_closed"], True)
        self.assertIs(boundary["idempotent_rerun"], True)
        operations = boundary["operations"]
        self.assertEqual(operations["database_acl_grant"], "database_owner_authority")
        self.assertEqual(operations["schema_acl_grant"], "database_owner_authority")
        self.assertEqual(operations["public_temporary_revoke"], "database_owner_authority")
        self.assertEqual(operations["object_privilege_grants"], "object_owner")
        self.assertEqual(operations["public_trigger_execute_revoke"], "object_owner")

    def test_publication_artifact_column_update_is_exact(self) -> None:
        self.assertEqual(
            self.manifest["column_privileges"],
            {"sqag_quote_publication_artifacts": {"update": ["checksum_sha256"]}},
        )
        self.assertIs(
            self.manifest["tables"]["runtime_accessible"]["sqag_quote_publication_artifacts"]["privileges"]["update"],
            False,
        )

    def test_publication_backfill_application_path_updates_only_checksum(self) -> None:
        source = inspect.getsource(DatabaseSqagStorage.publish_quote_session_forensic_transaction)
        self.assertEqual(source.count("update sqag_quote_publication_artifacts"), 1)
        self.assertEqual(source.count("set checksum_sha256 = ?"), 1)
        self.assertNotIn("set checksum_sha256 = ?,", source)
        self.assertIn(
            "where workspace_id = ? and run_id = ? and artifact_kind = ?",
            source,
        )

    def test_no_runtime_table_has_grant_option(self) -> None:
        for entry in self.manifest["tables"]["runtime_accessible"].values():
            self.assertNotIn("grant_option", entry)

    def test_sequence_count_is_0(self) -> None:
        self.assertEqual(self.manifest["sequences"]["user_defined_public_count"], 0)
        self.assertEqual(self.manifest["sequences"]["runtime_privileges"], "none")

    def test_routine_inventory_is_two_sqag_plus_one_provider(self) -> None:
        routines = self.manifest["routines"]
        self.assertEqual(routines["total_count"], 3)
        self.assertEqual(routines["sqag_owned_count"], 2)
        self.assertEqual(set(routines["sqag_owned_triggers"]), set(EXPECTED_ROUTINES))
        self.assertEqual(set(routines["provider_owned_exceptions"]), {"show_db_tree"})

    def test_sqag_trigger_routines_are_trigger_only(self) -> None:
        for entry in self.manifest["routines"]["sqag_owned_triggers"].values():
            self.assertEqual(entry["owner"], "sqag_migrator")
            self.assertEqual(entry["security_mode"], "invoker")
            self.assertIs(entry["has_trigger_dependency"], True)
            self.assertEqual(entry["class"], "trigger_only")
            self.assertIs(entry["direct_runtime_execute"], False)
            self.assertIs(entry["public_execute_after_boundary_b"], False)

    def test_database_and_schema_acl_targets(self) -> None:
        database = self.manifest["database_acl"]
        self.assertEqual(set(database), {"operability", "public", "sqag_migrator", "sqag_app", "sqag_runtime"})
        self.assertIs(database["operability"]["datallowconn"], True)
        self.assertEqual(database["operability"]["datconnlimit"], -1)
        self.assertIs(database["public"]["connect"], True)
        self.assertIs(database["public"]["create"], False)
        self.assertEqual(database["public"]["temporary"], "forbidden_after_boundary_b")
        self.assertIs(database["sqag_runtime"]["connect"], True)
        self.assertIs(database["sqag_runtime"]["create"], False)
        self.assertIs(database["sqag_runtime"]["temporary"], False)
        schema = self.manifest["schema_acl"]
        self.assertEqual(schema["schema_name"], "public")
        self.assertEqual(schema["schema_owner"], "pg_database_owner")
        self.assertEqual(schema["authorized_grantor"], "database_owner_authority")
        self.assertIs(schema["public"]["usage"], True)
        self.assertIs(schema["sqag_runtime"]["usage"], True)
        self.assertIs(schema["sqag_runtime"]["create"], False)

    def test_parameter_privilege_contract_is_exactly_zero_classified_runtime_authority(self) -> None:
        self.assertEqual(
            self.manifest["parameter_privileges"],
            {
                "runtime_role": "sqag_runtime",
                "classified_runtime_privileges": [],
                "required_parameters": ["session_replication_role"],
                "startup_default_policies": {
                    "default_transaction_read_only": {
                        "posture": "allowed_values",
                        "allowed_values": ["off"],
                    },
                    "session_replication_role": {
                        "posture": "allowed_values",
                        "allowed_values": ["local", "origin"],
                    },
                },
                "rule": "No effective SET or ALTER SYSTEM authority, or corresponding grant option, is classified for sqag_runtime; every PostgreSQL parameter is enumerated and every applicable startup default is semantically classified; any unsafe, ambiguous, unknown, or grantable authority fails closed.",
            },
        )

    def test_default_privileges_are_grantee_aware(self) -> None:
        defaults = self.manifest["default_privileges"]
        self.assertEqual(defaults["object_classes"], list(DEFAULT_ACL_OBJECT_TYPES))
        self.assertEqual(defaults["sqag_runtime"], {"tables": "none", "sequences": "none", "routines": "none"})
        self.assertIn("aclexplode", defaults["verification_rule"])
        self.assertIn("grantee", defaults["verification_rule"])

    def test_verification_queries_are_complete(self) -> None:
        self.assertEqual(tuple(self.manifest["verification_queries"]), CANONICAL_QUERY_KEYS)
        self.assertEqual(set(self.manifest["verification_queries"]), set(CANONICAL_QUERY_COLUMNS))
        for query in self.manifest["verification_queries"].values():
            self.assertIsInstance(query, str)
            self.assertTrue(query.strip())


class ValidatorStaticTest(unittest.TestCase):
    def test_positive_file_fdw_availability_does_not_swallow_fixture_failure(
        self,
    ) -> None:
        fixture_factory = mock.Mock(
            side_effect=RuntimeError("synthetic foreign-table fixture failure")
        )
        with self.assertRaisesRegex(
            RuntimeError, "synthetic foreign-table fixture failure"
        ):
            _materialize_file_fdw_fixture(True, fixture_factory)
        fixture_factory.assert_called_once_with()

        unavailable_factory = mock.Mock()
        with self.assertRaisesRegex(unittest.SkipTest, "file_fdw is not available"):
            _materialize_file_fdw_fixture(False, unavailable_factory)
        unavailable_factory.assert_not_called()

    def _assert_fixture_rejected(self, payload: str, expected_error: str) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
            fh.write(payload)
            path = fh.name
        try:
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = validate_manifest_strictly(path)
            self.assertEqual(result, 2)
            self.assertIn(expected_error, stderr.getvalue())
        finally:
            Path(path).unlink(missing_ok=True)

    def _assert_fixture_accepted(self, manifest: dict[str, Any]) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(manifest, fh)
            path = fh.name
        try:
            self.assertEqual(validate_manifest_strictly(path), 0)
        finally:
            Path(path).unlink(missing_ok=True)

    def _assert_exact_query_rejected(self, query_key: str, query: str) -> None:
        self._assert_query_fixture_rejected(
            query_key,
            query,
            f"verification_query_{query_key}_executable_structure_mismatch",
        )

    def _mutated_fixture(self, mutate: Any) -> dict[str, Any]:
        manifest = copy.deepcopy(load_manifest())
        mutate(manifest)
        return manifest

    def test_valid_manifest_passes(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
            json.dump(load_manifest(), fh)
            path = fh.name
        try:
            self.assertEqual(validate_manifest_strictly(path), 0)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_duplicate_json_key_fixture_fails(self) -> None:
        raw = _fixture_json(load_manifest())
        self._assert_fixture_rejected(raw[:-1] + ',"schema_version":1}', "manifest_duplicate_json_key")

    def test_nested_unknown_key_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["roles"]["migrator"].update({"unexpected": "x"}))
        self._assert_fixture_rejected(json.dumps(manifest), "migrator_role_unknown_keys")

    def test_extra_provider_exception_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["routines"]["provider_owned_exceptions"].update({"extra": {}}))
        self._assert_fixture_rejected(json.dumps(manifest), "provider_exception_set_must_be_exactly_show_db_tree")

    def test_public_routine_authority_classification_is_exact(self) -> None:
        manifest = load_manifest()

        def routine(
            schema_name: str,
            routine_name: str,
            identity_arguments: str,
            routine_kind: str,
            direct_runtime_execute: bool,
            public_execute: bool,
            effective: bool,
            is_grantable: bool,
        ) -> dict[str, Any]:
            return {
                "schema_name": schema_name,
                "routine_name": routine_name,
                "identity_arguments": identity_arguments,
                "routine_kind": routine_kind,
                "privilege_type": "EXECUTE",
                "direct_runtime_execute": direct_runtime_execute,
                "public_execute": public_execute,
                "effective": effective,
                "is_grantable": is_grantable,
            }

        def routine_acl(
            schema_name: str,
            routine_name: str,
            identity_arguments: str,
            routine_kind: str,
            owner: str,
            security_definer: bool,
            has_trigger_dependency: bool,
            acl_entries: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return {
                "schema_name": schema_name,
                "routine_name": routine_name,
                "identity_arguments": identity_arguments,
                "routine_kind": routine_kind,
                "security_definer": security_definer,
                "owner": owner,
                "language": "plpgsql",
                "routine_definition": "",
                "routine_configuration": [],
                "acl_entries": acl_entries,
                "has_trigger_dependency": has_trigger_dependency,
                "trigger_bindings": [],
            }

        rows = [
            routine("public", name, "", "f", False, False, False, False)
            for name in EXPECTED_ROUTINES
        ]
        rows.extend(
            [
                routine("public", "show_db_tree", "", "f", False, True, True, False),
                routine("public", "unrelated_public", "", "f", False, False, False, False),
            ]
        )
        routine_acl_rows = [
            routine_acl(
                "public",
                name,
                "",
                "f",
                "sqag_migrator",
                False,
                True,
                [],
            )
            for name in sorted(EXPECTED_ROUTINES)
        ]
        routine_acl_rows.extend(
            [
                routine_acl(
                    "public",
                    "show_db_tree",
                    "",
                    "f",
                    "neondb_owner",
                    False,
                    False,
                    [
                        {
                            "grantee": "PUBLIC",
                            "grantor": "neondb_owner",
                            "privilege_type": "EXECUTE",
                            "is_grantable": False,
                        }
                    ],
                ),
                routine_acl(
                    "public",
                    "unrelated_public",
                    "",
                    "f",
                    "sqag_migrator",
                    False,
                    False,
                    [],
                ),
            ]
        )
        routine_acl_rows.sort(key=lambda row: (row["schema_name"], row["routine_name"], row["identity_arguments"], row["routine_kind"]))
        self.assertEqual(
            contract_validator.evaluate_schema_wide_runtime_authority(
                manifest,
                None,
                None,
                rows,
                routine_acl_rows=routine_acl_rows,
            ),
            (),
        )

        missing_acl_errors = contract_validator.evaluate_schema_wide_runtime_authority(manifest, None, None, rows)
        self.assertIn("routine_acl_evidence_required", missing_acl_errors)

        controls = (
            (0, "direct_runtime_execute", True, "runtime_public_trigger_direct_execute_forbidden_"),
            (0, "public_execute", True, "runtime_public_trigger_public_execute_forbidden_"),
            (0, "effective", True, "runtime_public_trigger_effective_execute_forbidden_"),
            (0, "is_grantable", True, "runtime_public_trigger_grant_option_forbidden_"),
        )
        for index, key, value, expected in controls:
            mutated = copy.deepcopy(rows)
            mutated[index][key] = value
            errors = contract_validator.evaluate_schema_wide_runtime_authority(
                manifest,
                None,
                None,
                mutated,
                routine_acl_rows=routine_acl_rows,
            )
            self.assertTrue(any(expected in error for error in errors), errors)

        mutated = copy.deepcopy(rows)
        mutated[-1]["effective"] = True
        mutated[-1]["public_execute"] = True
        errors = contract_validator.evaluate_schema_wide_runtime_authority(manifest, None, None, mutated, routine_acl_rows=routine_acl_rows)
        self.assertTrue(any("runtime_public_unclassified_authority_public.unrelated_public" in error for error in errors), errors)

        mutated = copy.deepcopy(rows)
        mutated[-2]["identity_arguments"] = "integer"
        errors = contract_validator.evaluate_schema_wide_runtime_authority(manifest, None, None, mutated, routine_acl_rows=routine_acl_rows)
        self.assertTrue(any("runtime_public_unclassified_authority_public.show_db_tree" in error for error in errors), errors)

        mutated = copy.deepcopy(rows)
        mutated[-2]["is_grantable"] = True
        errors = contract_validator.evaluate_schema_wide_runtime_authority(manifest, None, None, mutated, routine_acl_rows=routine_acl_rows)
        self.assertIn("runtime_provider_exception_grant_option_forbidden_show_db_tree", errors)

        posture_controls = (
            ("owner", "wrong_owner", "runtime_provider_exception_acl_owner_mismatch_show_db_tree"),
            ("security_definer", True, "runtime_provider_exception_acl_security_mismatch_show_db_tree"),
            ("has_trigger_dependency", True, "runtime_provider_exception_acl_dependency_mismatch_show_db_tree"),
        )
        for key, value, expected in posture_controls:
            acl_mutation = copy.deepcopy(routine_acl_rows)
            provider_acl = next(row for row in acl_mutation if row["routine_name"] == "show_db_tree")
            provider_acl[key] = value
            errors = contract_validator.evaluate_schema_wide_runtime_authority(
                manifest,
                None,
                None,
                rows,
                routine_acl_rows=acl_mutation,
            )
            self.assertIn(expected, errors)

        acl_mutation = copy.deepcopy(routine_acl_rows)
        provider_acl = next(row for row in acl_mutation if row["routine_name"] == "show_db_tree")
        provider_acl["acl_entries"].append(
            {
                "grantee": "sqag_runtime",
                "grantor": "neondb_owner",
                "privilege_type": "EXECUTE",
                "is_grantable": False,
            }
        )
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            None,
            None,
            rows,
            routine_acl_rows=acl_mutation,
        )
        self.assertIn("runtime_public_routine_acl_direct_evidence_mismatch_show_db_tree", errors)

        acl_mutation = copy.deepcopy(routine_acl_rows)
        provider_acl = next(row for row in acl_mutation if row["routine_name"] == "show_db_tree")
        provider_acl["acl_entries"] = []
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            None,
            None,
            rows,
            routine_acl_rows=acl_mutation,
        )
        self.assertIn("runtime_public_routine_acl_public_evidence_mismatch_show_db_tree", errors)

        acl_mutation = copy.deepcopy(routine_acl_rows)
        provider_acl = next(row for row in acl_mutation if row["routine_name"] == "show_db_tree")
        provider_acl["acl_entries"][0]["is_grantable"] = True
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            None,
            None,
            rows,
            routine_acl_rows=acl_mutation,
        )
        self.assertIn("runtime_public_routine_acl_grant_option_evidence_mismatch_show_db_tree", errors)

        acl_mutation = [
            row for row in copy.deepcopy(routine_acl_rows) if row["routine_name"] != "show_db_tree"
        ]
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            None,
            None,
            rows,
            routine_acl_rows=acl_mutation,
        )
        self.assertTrue(
            any("runtime_public_routine_acl_evidence_missing_show_db_tree" in error for error in errors),
            errors,
        )

        acl_mutation = copy.deepcopy(routine_acl_rows)
        acl_mutation.append(copy.deepcopy(next(row for row in acl_mutation if row["routine_name"] == "show_db_tree")))
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            None,
            None,
            rows,
            routine_acl_rows=acl_mutation,
        )
        self.assertTrue(any("duplicate" in error for error in errors), errors)

    def test_schema_direct_grant_provenance_is_independent_from_effective_authority(self) -> None:
        manifest = load_manifest()

        def schema_privilege(privilege_type: str, effective: bool, is_grantable: bool = False) -> dict[str, Any]:
            return {
                "schema_name": "public",
                "privilege_type": privilege_type,
                "effective": effective,
                "is_grantable": is_grantable,
            }

        def schema_evidence(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    "schema_name": "public",
                    "schema_owner": "pg_database_owner",
                    "database_owner": "db_owner",
                    "acl_entries": entries,
                }
            ]

        public_usage = {
            "grantee": "PUBLIC",
            "grantor": "pg_database_owner",
            "privilege_type": "USAGE",
            "is_grantable": False,
        }
        owner_create = {
            "grantee": "pg_database_owner",
            "grantor": "pg_database_owner",
            "privilege_type": "CREATE",
            "is_grantable": False,
        }
        owner_usage = {
            "grantee": "pg_database_owner",
            "grantor": "pg_database_owner",
            "privilege_type": "USAGE",
            "is_grantable": False,
        }
        direct_runtime_usage = {
            "grantee": "sqag_runtime",
            "grantor": "pg_database_owner",
            "privilege_type": "USAGE",
            "is_grantable": False,
        }
        effective_rows = [schema_privilege("USAGE", True), schema_privilege("CREATE", False)]
        clean_entries = [public_usage, owner_create, owner_usage, direct_runtime_usage]
        self.assertEqual(
            contract_validator.evaluate_schema_wide_runtime_authority(
                manifest,
                effective_rows,
                None,
                None,
                schema_acl_rows=schema_evidence(copy.deepcopy(clean_entries)),
            ),
            (),
        )

        missing_acl_errors = contract_validator.evaluate_schema_wide_runtime_authority(manifest, effective_rows, None, None)
        self.assertIn("schema_acl_evidence_required", missing_acl_errors)

        revoked_entries = [entry for entry in clean_entries if entry is not direct_runtime_usage]
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            effective_rows,
            None,
            None,
            schema_acl_rows=schema_evidence(copy.deepcopy(revoked_entries)),
        )
        self.assertIn("runtime_schema_direct_usage_evidence_missing_or_duplicate", errors)

        wrong_grantor = copy.deepcopy(clean_entries)
        wrong_grantor[-1]["grantor"] = "db_owner"
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            effective_rows,
            None,
            None,
            schema_acl_rows=schema_evidence(wrong_grantor),
        )
        self.assertIn("runtime_schema_direct_usage_grantor_invalid_expected_pg_database_owner_got_db_owner", errors)

        grant_option = copy.deepcopy(clean_entries)
        grant_option[-1]["is_grantable"] = True
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            effective_rows,
            None,
            None,
            schema_acl_rows=schema_evidence(grant_option),
        )
        self.assertIn("runtime_schema_direct_usage_grant_option_forbidden", errors)

        runtime_create = copy.deepcopy(clean_entries)
        runtime_create.append(
            {
                "grantee": "sqag_runtime",
                "grantor": "db_owner",
                "privilege_type": "CREATE",
                "is_grantable": False,
            }
        )
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            effective_rows,
            None,
            None,
            schema_acl_rows=schema_evidence(runtime_create),
        )
        self.assertIn("runtime_schema_direct_privilege_forbidden_CREATE", errors)

        public_usage_removed = [entry for entry in clean_entries if entry is not public_usage]
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            effective_rows,
            None,
            None,
            schema_acl_rows=schema_evidence(copy.deepcopy(public_usage_removed)),
        )
        self.assertIn("runtime_schema_public_usage_acl_evidence_missing", errors)

        duplicate_direct = copy.deepcopy(clean_entries)
        duplicate_direct.append(copy.deepcopy(direct_runtime_usage))
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            effective_rows,
            None,
            None,
            schema_acl_rows=schema_evidence(duplicate_direct),
        )
        self.assertIn("runtime_schema_direct_usage_evidence_missing_or_duplicate", errors)

        runtime_create_effective = [schema_privilege("USAGE", True), schema_privilege("CREATE", True)]
        errors = contract_validator.evaluate_schema_wide_runtime_authority(
            manifest,
            runtime_create_effective,
            None,
            None,
            schema_acl_rows=schema_evidence(copy.deepcopy(clean_entries)),
        )
        self.assertTrue(any("runtime_schema_public_CREATE_mismatch" in error for error in errors), errors)

    def test_parameter_authority_evaluator_is_zero_and_fail_closed(self) -> None:
        manifest = load_manifest()

        def parameter_row(
            parameter_name: str,
            *,
            acl_entries: list[dict[str, Any]] | None = None,
            effective_set: bool = False,
            effective_alter_system: bool = False,
            set_grantable: bool = False,
            alter_system_grantable: bool = False,
        ) -> dict[str, Any]:
            return {
                "parameter_name": parameter_name,
                "acl_entries": [] if acl_entries is None else acl_entries,
                "effective_set": effective_set,
                "effective_alter_system": effective_alter_system,
                "set_grantable": set_grantable,
                "alter_system_grantable": alter_system_grantable,
                "startup_defaults": [],
            }

        baseline = [parameter_row("application_name"), parameter_row("session_replication_role")]
        self.assertEqual(contract_validator.evaluate_parameter_authority(manifest, baseline), ())
        missing_required = [parameter_row("application_name")]
        missing_required_errors = contract_validator.evaluate_parameter_authority(manifest, missing_required)
        self.assertTrue(any("runtime_parameter_required_evidence_missing" in error for error in missing_required_errors), missing_required_errors)

        for key, expected in (
            ("effective_set", "runtime_parameter_effective_set_forbidden_session_replication_role"),
            ("effective_alter_system", "runtime_parameter_effective_alter_system_forbidden_session_replication_role"),
            ("set_grantable", "runtime_parameter_set_grant_option_forbidden_session_replication_role"),
            ("alter_system_grantable", "runtime_parameter_alter_system_grant_option_forbidden_session_replication_role"),
        ):
            mutated = copy.deepcopy(baseline)
            mutated[-1][key] = True
            errors = contract_validator.evaluate_parameter_authority(manifest, mutated)
            self.assertIn(expected, errors)

        direct_set_acl = [
            {
                "grantee": "sqag_runtime",
                "grantor": "db_owner",
                "privilege_type": "SET",
                "is_grantable": False,
            }
        ]
        mutated = copy.deepcopy(baseline)
        mutated[-1]["acl_entries"] = direct_set_acl
        errors = contract_validator.evaluate_parameter_authority(manifest, mutated)
        self.assertIn("runtime_parameter_row_1_direct_set_effective_mismatch", errors)

        duplicate = copy.deepcopy(baseline)
        duplicate.append(copy.deepcopy(duplicate[-1]))
        errors = contract_validator.evaluate_parameter_authority(manifest, duplicate)
        self.assertTrue(any("duplicate" in error or "ordering_or_duplicate" in error for error in errors), errors)

        malformed = copy.deepcopy(baseline)
        malformed[-1]["acl_entries"] = [{"grantee": "PUBLIC"}]
        errors = contract_validator.evaluate_parameter_authority(manifest, malformed)
        self.assertTrue(any("acl_entries" in error for error in errors), errors)

    def test_retained_rollback_role_evidence_is_required_and_bounded(self) -> None:
        manifest = load_manifest()

        def role_row(
            name: str,
            *,
            login: bool,
            superuser: bool = False,
            createdb: bool = False,
            createrole: bool = False,
            replication: bool = False,
            bypassrls: bool = False,
        ) -> dict[str, Any]:
            return {
                "rolname": name,
                "rolsuper": superuser,
                "rolinherit": True,
                "rolcreaterole": createrole,
                "rolcreatedb": createdb,
                "rolcanlogin": login,
                "rolreplication": replication,
                "rolbypassrls": bypassrls,
                "rolconnlimit": -1,
                "password_is_null": True,
            }

        baseline = [
            role_row("sqag_app", login=True),
            role_row("sqag_migrator", login=False),
            role_row("sqag_runtime", login=False),
        ]
        errors: list[str] = []
        contract_validator._validate_role_attribute_evidence(baseline, manifest, errors)
        self.assertEqual(errors, [])

        omitted = [row for row in baseline if row["rolname"] != "sqag_app"]
        errors = []
        contract_validator._validate_role_attribute_evidence(omitted, manifest, errors)
        self.assertTrue(any("role_attribute_required_evidence_missing" in error for error in errors), errors)

        no_login = copy.deepcopy(baseline)
        no_login[0]["rolcanlogin"] = False
        errors = []
        contract_validator._validate_role_attribute_evidence(no_login, manifest, errors)
        self.assertIn("role_attribute_row_0_rolcanlogin_mismatch", errors)

        elevated = copy.deepcopy(baseline)
        elevated[0]["rolsuper"] = True
        errors = []
        contract_validator._validate_role_attribute_evidence(elevated, manifest, errors)
        self.assertIn("role_attribute_row_0_rolsuper_mismatch", errors)
        self.assertIn("role_attribute_row_0_rolsuper_privileged_forbidden", errors)

        retired_without_contract = copy.deepcopy(manifest)
        retired_without_contract["roles"]["legacy"]["status"] = "retired"
        errors = []
        contract_validator._validate_role_attribute_evidence(omitted, retired_without_contract, errors)
        self.assertIn("legacy_role_status_not_explicitly_retained", errors)
        self.assertTrue(any("role_attribute_required_evidence_missing" in error for error in errors), errors)
    def test_final_authority_requires_each_canonical_query_collection(self) -> None:
        manifest = load_manifest()
        evidence = {key: [] for key in CANONICAL_QUERY_KEYS}
        for required_key in (
            "database_acl",
            "effective_runtime_database_privileges",
            "schema_acl",
            "sequence_acl",
            "effective_runtime_routine_privileges",
            "effective_runtime_parameter_privileges",
        ):
            candidate = copy.deepcopy(evidence)
            del candidate[required_key]
            errors = contract_validator.evaluate_final_runtime_authority(manifest, candidate)
            self.assertTrue(
                any(
                    f"final_runtime_evidence_missing_queries" in error
                    and required_key in error
                    for error in errors
                ),
                (required_key, errors),
            )

    def test_parameter_startup_default_precedence_rejects_replica(self) -> None:
        manifest = load_manifest()
        def parameter_row(parameter_name: str, startup_defaults: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "parameter_name": parameter_name,
                "acl_entries": [],
                "startup_defaults": startup_defaults,
                "effective_set": False,
                "effective_alter_system": False,
                "set_grantable": False,
                "alter_system_grantable": False,
            }

        baseline = [
            parameter_row("application_name", []),
            parameter_row(
                "session_replication_role",
                [
                    {"scope": "database_global", "precedence": 1, "setting": "session_replication_role=origin"},
                ],
            ),
        ]
        self.assertEqual(contract_validator.evaluate_parameter_authority(manifest, baseline), ())
        unsafe = copy.deepcopy(baseline)
        unsafe[-1]["startup_defaults"].append(
            {"scope": "role_global", "precedence": 2, "setting": "session_replication_role=replica"}
        )
        unsafe[-1]["startup_defaults"].sort(key=lambda entry: (entry["precedence"], entry["setting"]))
        errors = contract_validator.evaluate_parameter_authority(manifest, unsafe)
        self.assertTrue(
            any("runtime_parameter_unsafe_startup_default_session_replication_role" in error for error in errors),
            errors,
        )
        restored = copy.deepcopy(unsafe)
        restored[-1]["startup_defaults"][-1] = {
            "scope": "role_database",
            "precedence": 3,
            "setting": "session_replication_role=origin",
        }
        self.assertEqual(contract_validator.evaluate_parameter_authority(manifest, restored), ())

    def test_startup_default_policy_is_closed_world_and_precedence_aware(self) -> None:
        manifest = load_manifest()

        def parameter_row(parameter_name: str, startup_defaults: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "parameter_name": parameter_name,
                "acl_entries": [],
                "startup_defaults": startup_defaults,
                "effective_set": False,
                "effective_alter_system": False,
                "set_grantable": False,
                "alter_system_grantable": False,
            }

        baseline = [
            parameter_row("application_name", []),
            parameter_row("default_transaction_read_only", []),
            parameter_row("session_replication_role", []),
        ]
        self.assertEqual(contract_validator.evaluate_parameter_authority(manifest, baseline), ())

        safe = copy.deepcopy(baseline)
        safe[2]["startup_defaults"] = [
            {"scope": "role_global", "precedence": 2, "setting": "session_replication_role=local"}
        ]
        safe[1]["startup_defaults"] = [
            {"scope": "database_global", "precedence": 1, "setting": "default_transaction_read_only=off"}
        ]
        self.assertEqual(contract_validator.evaluate_parameter_authority(manifest, safe), ())

        unsafe_read_only = copy.deepcopy(baseline)
        unsafe_read_only[1]["startup_defaults"] = [
            {"scope": "role_global", "precedence": 2, "setting": "default_transaction_read_only=on"}
        ]
        errors = contract_validator.evaluate_parameter_authority(manifest, unsafe_read_only)
        self.assertIn(
            "runtime_parameter_unsafe_startup_default_default_transaction_read_only_on",
            errors,
        )

        precedence = copy.deepcopy(baseline)
        precedence[1]["startup_defaults"] = [
            {"scope": "database_global", "precedence": 1, "setting": "default_transaction_read_only=off"},
            {"scope": "role_global", "precedence": 2, "setting": "default_transaction_read_only=on"},
        ]
        errors = contract_validator.evaluate_parameter_authority(manifest, precedence)
        self.assertIn(
            "runtime_parameter_unsafe_startup_default_default_transaction_read_only_on",
            errors,
        )

        ambiguous = copy.deepcopy(baseline)
        ambiguous[1]["startup_defaults"] = [
            {"scope": "role_global", "precedence": 2, "setting": "default_transaction_read_only=off"},
            {"scope": "role_global", "precedence": 2, "setting": "default_transaction_read_only=on"},
        ]
        errors = contract_validator.evaluate_parameter_authority(manifest, ambiguous)
        self.assertIn("runtime_parameter_row_1_startup_default_ambiguous", errors)

        unknown = copy.deepcopy(baseline)
        unknown.append(
            parameter_row(
                "sqag_h50_unknown.setting",
                [{"scope": "role_global", "precedence": 2, "setting": "sqag_h50_unknown.setting=on"}],
            )
        )
        errors = contract_validator.evaluate_parameter_authority(manifest, unknown)
        self.assertIn(
            "runtime_parameter_startup_default_unclassified_sqag_h50_unknown.setting",
            errors,
        )

        unsafe_session = copy.deepcopy(baseline)
        unsafe_session[2]["startup_defaults"] = [
            {"scope": "role_global", "precedence": 2, "setting": "session_replication_role=replica"}
        ]
        errors = contract_validator.evaluate_parameter_authority(manifest, unsafe_session)
        self.assertTrue(
            any("runtime_parameter_unsafe_startup_default_session_replication_role" in error for error in errors),
            errors,
        )
    def test_migration_derived_structural_contract_rejects_constraint_and_index_drift(self) -> None:
        structural = contract_validator.classified_table_structure_contract()

        def evidence_rows() -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for table_name, table in structural["tables"].items():
                constraints = []
                for index, constraint in enumerate(copy.deepcopy(table["constraints"])):
                    if constraint["constraint_name"] is None:
                        constraint["constraint_name"] = f"fixture_{table_name}_{index}"
                    constraints.append(constraint)
                indexes = [
                    copy.deepcopy(index_contract)
                    for index_contract in structural["indexes"].values()
                    if index_contract["target_table"] == table_name
                ]
                triggers = [
                    copy.deepcopy(trigger)
                    for trigger in structural["triggers"].values()
                    if trigger["target_relation"] == table_name
                ]
                rows.append(
                    {
                        "schema_name": "public",
                        "relname": table_name,
                        "relacl": [],
                        "table_columns": copy.deepcopy(table["columns"]),
                        "table_constraints": constraints,
                        "trigger_bindings": triggers,
                        "index_contracts": indexes,
                        "rule_bindings": copy.deepcopy(structural["rules"].get(table_name, [])),
                    }
                )
            rows.append(
                {
                    "schema_name": "public",
                    "relname": "legacy_quote_artifacts_source",
                    "relacl": [],
                    "table_columns": [],
                    "table_constraints": [],
                    "trigger_bindings": [],
                    "index_contracts": [],
                    "rule_bindings": [],
                }
            )
            return rows

        baseline = evidence_rows()
        errors: list[str] = []
        contract_validator._validate_table_structure_evidence(baseline, errors)
        self.assertEqual(errors, [])
        self.assertEqual(structural["rules"], {})
        rule_row = next(row for row in baseline if row["relname"] == "sqag_profiles")
        rule_row["rule_bindings"] = [
            {
                "rule_name": "sqag_h46_unexpected_rule",
                "target_schema": "public",
                "target_relation": "sqag_profiles",
                "event": "INSERT",
                "is_instead": True,
                "enabled": "O",
                "definition": "create rule sqag_h46_unexpected_rule as on insert to public.sqag_profiles do instead nothing",
            }
        ]
        errors = []
        contract_validator._validate_table_structure_evidence(baseline, errors)
        self.assertIn(
            "table_structural_rule_binding_contract_mismatch_sqag_profiles",
            errors,
        )
        baseline = evidence_rows()
        composite_fk = next(
            constraint
            for row in baseline
            for constraint in row["table_constraints"]
            if constraint["constraint_type"] == "f"
            and len(constraint["columns"]) > 1
        )
        self.assertEqual(composite_fk["match_type"], "SIMPLE")
        composite_fk["match_type"] = "FULL"
        errors = []
        contract_validator._validate_table_structure_evidence(baseline, errors)
        self.assertTrue(
            any("table_structural_constraint_contract_mismatch_" in error for error in errors),
            errors,
        )
        baseline = evidence_rows()
        publication_artifacts = next(
            row for row in baseline if row["relname"] == "sqag_quote_publication_artifacts"
        )
        checksum_check = next(
            constraint
            for constraint in publication_artifacts["table_constraints"]
            if constraint["constraint_type"] == "c"
            and "checksum_sha256" in str(constraint["check_expression"])
        )
        clean_expression = checksum_check["check_expression"]
        for cast_expression in (
            clean_expression.replace("checksum_sha256", "checksum_sha256::name"),
            clean_expression.replace("checksum_sha256", "CAST(checksum_sha256 AS name)"),
            clean_expression.replace("checksum_sha256", "checksum_sha256::char(64)"),
        ):
            mutated = evidence_rows()
            mutated_check = next(
                constraint
                for row in mutated
                if row["relname"] == "sqag_quote_publication_artifacts"
                for constraint in row["table_constraints"]
                if constraint["constraint_type"] == "c"
                and "checksum_sha256" in str(constraint["check_expression"])
            )
            mutated_check["check_expression"] = cast_expression
            errors = []
            contract_validator._validate_table_structure_evidence(mutated, errors)
            self.assertIn(
                "table_structural_constraint_contract_mismatch_sqag_quote_publication_artifacts",
                errors,
            )
        harmless_formatting = evidence_rows()
        harmless_check = next(
            constraint
            for row in harmless_formatting
            if row["relname"] == "sqag_quote_publication_artifacts"
            for constraint in row["table_constraints"]
            if constraint["constraint_type"] == "c"
            and "checksum_sha256" in str(constraint["check_expression"])
        )
        harmless_check["check_expression"] = " length ( checksum_sha256 ) = 64 "
        errors = []
        contract_validator._validate_table_structure_evidence(harmless_formatting, errors)
        self.assertEqual(errors, [])
        baseline = evidence_rows()
        publication = next(
            row for row in baseline if row["relname"] == "sqag_quote_publication_versions"
        )
        publication["table_constraints"] = [
            constraint
            for constraint in publication["table_constraints"]
            if constraint["constraint_type"] != "p"
        ]
        errors = []
        contract_validator._validate_table_structure_evidence(baseline, errors)
        self.assertTrue(
            any("table_structural_constraint_contract_mismatch_sqag_quote_publication_versions" in error for error in errors),
            errors,
        )
        baseline = evidence_rows()
        index_row = next(
            row
            for row in baseline
            if any(index["index_name"] == "sqag_feedback_publication_idx" for index in row["index_contracts"])
        )
        next(
            index
            for index in index_row["index_contracts"]
            if index["index_name"] == "sqag_feedback_publication_idx"
        )["target_table"] = "sqag_feedback_status_history"
        errors = []
        contract_validator._validate_table_structure_evidence(baseline, errors)
        self.assertTrue(
            any("table_structural_index_sqag_feedback_publication_idx_target_table_mismatch" in error for error in errors),
            errors,
        )

    def test_migration_derived_routine_contract_rejects_body_and_trigger_drift(self) -> None:
        structural = contract_validator.classified_table_structure_contract()
        evidence = {
            identity: {
                **copy.deepcopy(routine),
                "security_definer": False,
                "owner": "sqag_migrator",
                "has_trigger_dependency": bool(
                    any(
                        trigger["function_name"] == identity[1]
                        and trigger["function_identity_arguments"] == identity[2]
                        for trigger in structural["triggers"].values()
                    )
                ),
                "acl_entries": [],
                "trigger_bindings": [
                    copy.deepcopy(trigger)
                    for trigger in structural["triggers"].values()
                    if trigger["function_name"] == identity[1]
                    and trigger["function_identity_arguments"] == identity[2]
                ],
            }
            for identity, routine in structural["routines"].items()
        }
        errors: list[str] = []
        contract_validator._validate_routine_structural_evidence(evidence, errors)
        self.assertEqual(errors, [])
        mutated = copy.deepcopy(evidence)
        mutated[("public", "sqag_reject_immutable_change", "", "f")]["language"] = "sql"
        errors = []
        contract_validator._validate_routine_structural_evidence(mutated, errors)
        self.assertIn("routine_structural_language_mismatch_sqag_reject_immutable_change", errors)
        mutated = copy.deepcopy(evidence)
        mutated[("public", "sqag_reject_immutable_change", "", "f")]["trigger_bindings"][0]["target_relation"] = "sqag_feedback"
        errors = []
        contract_validator._validate_routine_structural_evidence(mutated, errors)
        self.assertIn("routine_structural_trigger_binding_mismatch_sqag_reject_immutable_change", errors)
        mutated = copy.deepcopy(evidence)
        mutated[("public", "sqag_reject_immutable_change", "", "f")]["trigger_bindings"][0]["enabled"] = "D"
        errors = []
        contract_validator._validate_routine_structural_evidence(mutated, errors)
        self.assertIn("routine_structural_trigger_binding_mismatch_sqag_reject_immutable_change", errors)

    def test_database_acl_exact_state_rejects_public_create(self) -> None:
        row = {
            "database_name": "fixture",
            "database_owner": "database_owner",
            "datallowconn": True,
            "datconnlimit": -1,
            "datacl": [],
            "acl_entries": [
                {
                    "grantee": "sqag_runtime",
                    "grantor": "database_owner",
                    "privilege_type": "CONNECT",
                    "is_grantable": False,
                },
                {
                    "grantee": "PUBLIC",
                    "grantor": "database_owner",
                    "privilege_type": "CONNECT",
                    "is_grantable": False,
                },
                {
                    "grantee": "sqag_migrator",
                    "grantor": "database_owner",
                    "privilege_type": "CONNECT",
                    "is_grantable": False,
                },
                {
                    "grantee": "sqag_migrator",
                    "grantor": "database_owner",
                    "privilege_type": "CREATE",
                    "is_grantable": False,
                },
                {
                    "grantee": "sqag_migrator",
                    "grantor": "database_owner",
                    "privilege_type": "TEMPORARY",
                    "is_grantable": False,
                },
            ],
        }
        errors: list[str] = []
        contract_validator._validate_database_acl_evidence([row], errors, runtime_role="sqag_runtime")
        self.assertEqual(errors, [])
        mutated = copy.deepcopy(row)
        mutated["acl_entries"].append(
            {
                "grantee": "PUBLIC",
                "grantor": "database_owner",
                "privilege_type": "CREATE",
                "is_grantable": False,
            }
        )
        errors = []
        contract_validator._validate_database_acl_evidence([mutated], errors, runtime_role="sqag_runtime")
        self.assertIn("database_acl_public_create_forbidden", errors)

    def test_database_acl_requires_migrator_direct_provenance(self) -> None:
        row = {
            "database_name": "fixture",
            "database_owner": "database_owner",
            "datallowconn": True,
            "datconnlimit": -1,
            "datacl": [],
            "acl_entries": [
                {"grantee": "PUBLIC", "grantor": "database_owner", "privilege_type": "CONNECT", "is_grantable": False},
                {"grantee": "sqag_runtime", "grantor": "database_owner", "privilege_type": "CONNECT", "is_grantable": False},
                {"grantee": "sqag_migrator", "grantor": "database_owner", "privilege_type": "CONNECT", "is_grantable": False},
                {"grantee": "sqag_migrator", "grantor": "database_owner", "privilege_type": "CREATE", "is_grantable": False},
                {"grantee": "sqag_migrator", "grantor": "database_owner", "privilege_type": "TEMPORARY", "is_grantable": False},
            ],
        }
        self.assertEqual(
            contract_validator._validate_database_acl_evidence(
                [row], [], runtime_role="sqag_runtime"
            )[1],
            tuple(
                (entry["grantee"], entry["grantor"], entry["privilege_type"], entry["is_grantable"])
                for entry in row["acl_entries"]
            ),
        )
        for privilege in ("CONNECT", "CREATE", "TEMPORARY"):
            mutated = copy.deepcopy(row)
            mutated["acl_entries"] = [
                entry
                for entry in mutated["acl_entries"]
                if not (entry["grantee"] == "sqag_migrator" and entry["privilege_type"] == privilege)
            ]
            errors: list[str] = []
            contract_validator._validate_database_acl_evidence([mutated], errors, runtime_role="sqag_runtime")
            self.assertIn(
                f"database_acl_migrator_{privilege.lower()}_direct_evidence_missing_or_duplicate",
                errors,
            )
        wrong_grantee = copy.deepcopy(row)
        next(
            entry
            for entry in wrong_grantee["acl_entries"]
            if entry["grantee"] == "sqag_migrator" and entry["privilege_type"] == "CREATE"
        )["grantee"] = "sqag_wrong_grantee"
        errors = []
        contract_validator._validate_database_acl_evidence([wrong_grantee], errors, runtime_role="sqag_runtime")
        self.assertIn("database_acl_migrator_create_direct_evidence_missing_or_duplicate", errors)
        wrong_grantor = copy.deepcopy(row)
        next(
            entry
            for entry in wrong_grantor["acl_entries"]
            if entry["grantee"] == "sqag_migrator" and entry["privilege_type"] == "CREATE"
        )["grantor"] = "PUBLIC"
        errors = []
        contract_validator._validate_database_acl_evidence([wrong_grantor], errors, runtime_role="sqag_runtime")
        self.assertIn("database_acl_migrator_create_grantor_invalid_expected_database_owner_got_PUBLIC", errors)
        grantable = copy.deepcopy(row)
        next(
            entry
            for entry in grantable["acl_entries"]
            if entry["grantee"] == "sqag_migrator" and entry["privilege_type"] == "TEMPORARY"
        )["is_grantable"] = True
        errors = []
        contract_validator._validate_database_acl_evidence([grantable], errors, runtime_role="sqag_runtime")
        self.assertIn("database_acl_migrator_temporary_grant_option_forbidden", errors)

    def test_system_relation_acl_provenance_rejects_exceptional_runtime_and_public_grants(self) -> None:
        baseline_entry = {
            "grantee": "postgres",
            "grantor": "postgres",
            "privilege_type": "SELECT",
            "is_grantable": False,
        }
        baseline = [
            {
                "schema_name": "pg_catalog",
                "relation_name": "fixture_catalog",
                "relation_kind": "r",
                "current_acl_entries": [copy.deepcopy(baseline_entry)],
                "initial_acl_entries": [copy.deepcopy(baseline_entry)],
                "initial_privilege_types": ["i"],
            }
        ]
        errors: list[str] = []
        contract_validator._validate_system_relation_acl_evidence(
            baseline, errors, runtime_role="sqag_runtime"
        )
        self.assertEqual(errors, [])
        for grantee in ("sqag_runtime", "PUBLIC"):
            mutated = copy.deepcopy(baseline)
            mutated[0]["current_acl_entries"].append(
                {
                    "grantee": grantee,
                    "grantor": "postgres",
                    "privilege_type": "SELECT",
                    "is_grantable": False,
                }
            )
            errors = []
            contract_validator._validate_system_relation_acl_evidence(
                mutated, errors, runtime_role="sqag_runtime"
            )
            self.assertTrue(
                any(f"system_relation_acl_exceptional_{grantee}_select_pg_catalog.fixture_catalog" in error for error in errors),
                errors,
            )
        grantable = copy.deepcopy(baseline)
        grantable[0]["current_acl_entries"].append(
            {
                "grantee": "PUBLIC",
                "grantor": "postgres",
                "privilege_type": "SELECT",
                "is_grantable": True,
            }
        )
        errors = []
        contract_validator._validate_system_relation_acl_evidence(
            grantable, errors, runtime_role="sqag_runtime"
        )
        self.assertIn(
            "system_relation_acl_grant_option_forbidden_PUBLIC_select_pg_catalog.fixture_catalog",
            errors,
        )

    def test_schema_scoped_explicit_column_exception_rejects_shadow_schema(self) -> None:
        manifest = copy.deepcopy(load_manifest())
        manifest["tables"]["runtime_accessible"] = {}
        manifest["tables"]["runtime_forbidden"] = {}

        def column(schema_name: str, effective: bool) -> dict[str, Any]:
            return {
                "schema_name": schema_name,
                "table_name": "sqag_quote_publication_artifacts",
                "column_name": "checksum_sha256",
                "acl_entries": [],
                "privilege_type": "UPDATE",
                "effective": effective,
                "is_grantable": False,
            }

        shadow_errors = contract_validator.evaluate_public_table_like_authority(
            manifest,
            [],
            [column("application_h27_synthetic", True)],
        )
        self.assertTrue(
            any("runtime_privilege_mismatch_UPDATE_expected_False_got_True" in error for error in shadow_errors),
            shadow_errors,
        )
        public_errors = contract_validator.evaluate_public_table_like_authority(
            manifest,
            [],
            [column("public", True)],
        )
        self.assertFalse(
            any("runtime_privilege_mismatch_UPDATE_expected_False_got_True" in error for error in public_errors),
            public_errors,
        )

    def test_missing_public_database_acl_fixture_fails(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            del manifest["database_acl"]["public"]
        self._assert_fixture_rejected(json.dumps(self._mutated_fixture(mutate)), "database_acl_missing_keys")

    def test_incorrect_public_temporary_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["database_acl"]["public"].update({"temporary": True}))
        self._assert_fixture_rejected(json.dumps(manifest), "database_acl_public_temporary_invalid")

    def test_incorrect_runtime_connection_limit_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["roles"]["runtime"]["attributes"].update({"connection_limit": 0}))
        self._assert_fixture_rejected(json.dumps(manifest), "runtime_attribute_connection_limit_invalid")

    def test_incorrect_provider_default_rule_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["default_privileges"].update({"provider_controlled": "changed"}))
        self._assert_fixture_rejected(json.dumps(manifest), "provider_default_privileges_invalid")

    def test_empty_default_acl_verification_query_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["verification_queries"].update({"default_acl": ""}))
        self._assert_fixture_rejected(json.dumps(manifest), "verification_query_default_acl_must_be_non_empty_string")

    def test_missing_migration_table_binding_fixture_fails(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["production_migrations"][0]["tables"].pop()
        self._assert_fixture_rejected(json.dumps(self._mutated_fixture(mutate)), "migration_0_table_binding_mismatch")

    def test_empty_forbidden_table_reason_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["tables"]["runtime_forbidden"]["sqag_legal_holds"].update({"reason": ""}))
        self._assert_fixture_rejected(json.dumps(manifest), "forbidden_table_sqag_legal_holds_reason_must_be_non_empty_string")

    def test_wrong_runtime_privilege_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(
            lambda m: m["tables"]["runtime_accessible"]["sqag_generation_evidence"]["privileges"].update({"delete": True})
        )
        self._assert_fixture_rejected(json.dumps(manifest), "accessible_table_sqag_generation_evidence_delete_invalid")

    def test_publication_column_grant_fixture_fails_closed(self) -> None:
        manifest = self._mutated_fixture(
            lambda m: m["column_privileges"]["sqag_quote_publication_artifacts"].update(
                {"update": ["content_blob"]}
            )
        )
        self._assert_fixture_rejected(
            json.dumps(manifest),
            "column_privileges_sqag_quote_publication_artifacts_update_invalid",
        )

    def test_default_acl_object_class_fixture_fails_closed(self) -> None:
        manifest = self._mutated_fixture(
            lambda m: m["default_privileges"].update({"object_classes": ["r", "S", "f"]})
        )
        self._assert_fixture_rejected(
            json.dumps(manifest),
            "default_privilege_object_classes_invalid",
        )

    def test_wrong_digest_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["production_migrations"][0].update({"sha256": "0" * 64}))
        self._assert_fixture_rejected(json.dumps(manifest), "migration_0_sha256_invalid")

    def test_extra_forbidden_table_fixture_fails(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["tables"]["runtime_forbidden"]["extra_table"] = {
                "class": "migration_only", "schema": "public", "reason": "test"
            }
        self._assert_fixture_rejected(json.dumps(self._mutated_fixture(mutate)), "forbidden_table_set_mismatch")

    def test_wrong_runtime_connect_type_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["database_acl"]["sqag_runtime"].update({"connect": "true"}))
        self._assert_fixture_rejected(json.dumps(manifest), "database_acl_sqag_runtime_connect_invalid")

    def test_extra_schema_acl_actor_fixture_fails(self) -> None:
        manifest = self._mutated_fixture(lambda m: m["schema_acl"].update({"unexpected": {}}))
        self._assert_fixture_rejected(json.dumps(manifest), "schema_acl_unknown_keys")

    def test_prefix_filtered_routine_query_fixture_fails(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            canonical = manifest["verification_queries"]["routine_acl"]
            manifest["verification_queries"]["routine_acl"] = canonical.replace(
                "and p.prokind in ('f', 'p', 'a', 'w')",
                "and p.prokind in ('f', 'p', 'a', 'w') and p.proname like 'sqag_' || chr(37)",
            )

        self._assert_fixture_rejected(
            json.dumps(self._mutated_fixture(mutate)),
            "verification_query_routine_acl_must_not_prefix_filter_routines",
        )

    def test_default_acl_query_missing_schema_and_type_classes_fails(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"].replace(
            ", 'n', 'T'", "", 1
        )
        self._assert_query_fixture_rejected(
            "default_acl",
            query,
            "verification_query_default_acl_must_cover_r_s_f_n_T_object_types",
        )

    def test_role_attributes_masked_catalog_query_fails(self) -> None:
        query = (
            "select rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, "
            "rolcanlogin, rolreplication, rolbypassrls, rolconnlimit, rolpassword "
            "from pg_catalog.pg_roles where rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner') "
            "order by rolname"
        )
        self._assert_query_fixture_rejected(
            "role_attributes",
            query,
            "verification_query_role_attributes_must_read_pg_authid",
        )

    def test_role_attributes_raw_password_projection_fails(self) -> None:
        query = load_manifest()["verification_queries"]["role_attributes"].replace(
            "a.rolpassword is null as password_is_null",
            "a.rolpassword as password_is_null",
            1,
        )
        self._assert_query_fixture_rejected(
            "role_attributes",
            query,
            "verification_query_role_attributes_password_state_must_be_boolean_null_assertion",
        )

    def test_missing_legacy_view_fails_closed(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["views"]["runtime_accessible"].pop("sqag_quote_artifacts")

        self._assert_fixture_rejected(json.dumps(self._mutated_fixture(mutate)), "view_set_mismatch")

    def test_extra_legacy_view_fails_closed(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["views"]["runtime_accessible"]["sqag_file_artifacts"] = {
                "schema": "public",
                "class": "legacy_publication_backfill",
                "privileges": {"select": True},
                "production_source": "webapp.server.DatabaseSqagStorage.publish_quote_session_forensic_transaction",
                "bound": True,
            }

        self._assert_fixture_rejected(json.dumps(self._mutated_fixture(mutate)), "view_set_mismatch")

    def test_view_write_authority_fails_closed(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["views"]["runtime_accessible"]["sqag_quote_artifacts"]["privileges"].update({"insert": True})

        self._assert_fixture_rejected(
            json.dumps(self._mutated_fixture(mutate)),
            "accessible_view_sqag_quote_artifacts_privileges_unknown_keys",
        )

    def test_view_grant_option_fails_closed(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["views"]["runtime_accessible"]["sqag_quote_artifacts"].update({"grant_option": True})

        self._assert_fixture_rejected(
            json.dumps(self._mutated_fixture(mutate)),
            "accessible_view_sqag_quote_artifacts_unknown_keys",
        )

    def test_missing_legacy_optional_marker_fails_closed(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            del manifest["views"]["legacy_optional"]

        self._assert_fixture_rejected(
            json.dumps(self._mutated_fixture(mutate)),
            "views_missing_keys",
        )

    def test_materialized_view_rule_mutation_fails_closed(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["views"]["materialized_view_rule"] = "materialized views are permitted"

        self._assert_fixture_rejected(
            json.dumps(self._mutated_fixture(mutate)),
            "views_materialized_view_rule_invalid",
        )

    def test_view_acl_query_must_enumerate_all_non_system_views(self) -> None:
        canonical = load_manifest()["verification_queries"]["view_acl"]
        for mutation in (
            canonical.replace("('v', 'm')", "('v')", 1),
            canonical.replace("('v', 'm')", "('r', 'm')", 1),
            canonical.replace(
                "n.nspname !~ '^pg_temp_[0-9]+$'",
                "n.nspname !~ '^pg_%'",
                1,
            ),
        ):
            self._assert_query_fixture_rejected(
                "view_acl",
                mutation,
                "verification_query_view_acl_executable_structure_mismatch",
            )

        for schema_name in (
            "pg_catalog",
            "information_schema",
            "pg_toast",
            "pg_temp_7",
            "pg_toast_temp_42",
        ):
            self.assertTrue(contract_validator._is_postgresql_system_schema(schema_name))
        for schema_name in ("public", "application", "pg_application_data", "pg_temp_backup"):
            self.assertFalse(contract_validator._is_postgresql_system_schema(schema_name))

    def test_view_acl_query_must_expose_runtime_select_and_grant_option(self) -> None:
        canonical = load_manifest()["verification_queries"]["view_acl"]
        for replaced, replacement in (
            ("runtime_select", "runtime_missing"),
            ("runtime_select_grantable", "grantable_missing"),
            ("acl_entries", "acl_entries_missing"),
            ("runtime_privileges", "runtime_privileges_missing"),
        ):
            self._assert_query_fixture_rejected(
                "view_acl",
                canonical.replace(replaced, replacement, 1),
                "verification_query_view_acl_executable_structure_mismatch",
            )

    def test_view_acl_query_must_expose_relation_kind_and_owner(self) -> None:
        canonical = load_manifest()["verification_queries"]["view_acl"]
        for replaced, replacement in (
            ("relation_kind", "kind_missing"),
            ("owner", "owner_missing"),
        ):
            self._assert_query_fixture_rejected(
                "view_acl",
                canonical.replace(replaced, replacement, 1),
                "verification_query_view_acl_executable_structure_mismatch",
            )

    def test_boundary_b_wrong_authority_fails_closed(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["boundary_b"]["operations"].update({"database_acl_grant": "object_owner"})

        self._assert_fixture_rejected(json.dumps(self._mutated_fixture(mutate)), "boundary_b_database_acl_grant_invalid")

    def test_boundary_b_missing_authority_model_fails_closed(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            del manifest["boundary_b"]["authority_input_model"]

        self._assert_fixture_rejected(json.dumps(self._mutated_fixture(mutate)), "boundary_b_missing_keys")

    def _assert_query_fixture_rejected(self, query_key: str, query: str, expected_error: str) -> None:
        manifest = self._mutated_fixture(lambda m: m["verification_queries"].update({query_key: query}))
        self._assert_fixture_rejected(json.dumps(manifest), expected_error)

    def test_canonical_verification_queries_pass_lexical_shape(self) -> None:
        manifest = load_manifest()
        self.assertEqual(validate_manifest_strictly(str(MANIFEST_PATH)), 0)
        for key in manifest["verification_queries"]:
            self.assertTrue(lex_sql(manifest["verification_queries"][key]))

    def test_exact_query_contract_accepts_formatting_and_comments(self) -> None:
        manifest = load_manifest()
        for key, canonical in manifest["verification_queries"].items():
            variants = (
                canonical.replace("\n", " ").replace("\t", " ").strip(),
                canonical.replace(" ", " /* presentation */\n ", 1),
                canonical.replace("select", "SELECT", 1),
            )
            for variant in variants:
                candidate = copy.deepcopy(manifest)
                candidate["verification_queries"][key] = variant
                self._assert_fixture_accepted(candidate)

    def test_exact_query_contract_accepts_authorised_final_semicolon(self) -> None:
        manifest = load_manifest()
        for key, canonical in manifest["verification_queries"].items():
            candidate = copy.deepcopy(manifest)
            candidate["verification_queries"][key] = canonical + ";\n/* trailing presentation comment */"
            self._assert_fixture_accepted(candidate)

    def test_exact_query_contract_opaque_regions_cannot_supply_structure(self) -> None:
        fixtures = {
            "default_acl": (
                "select 'from pg_catalog.pg_default_acl defaclrole defaclnamespace defaclobjtype '"
                "'defaclacl privilege_type is_grantable grantee owner namespace order by'",
                'select "from pg_catalog.pg_default_acl defaclrole defaclnamespace defaclobjtype '
                'defaclacl privilege_type is_grantable grantee owner namespace order by"',
                "select $$from pg_catalog.pg_default_acl defaclrole defaclnamespace defaclobjtype "
                "defaclacl privilege_type is_grantable grantee owner namespace order by$$",
            ),
            "routine_acl": (
                "select 'from pg_catalog.pg_proc pg_catalog.pg_namespace pg_catalog.pg_roles '"
                "'pg_catalog.pg_trigger order by'",
                'select "from pg_catalog.pg_proc pg_catalog.pg_namespace pg_catalog.pg_roles '
                'pg_catalog.pg_trigger order by"',
                "select $$from pg_catalog.pg_proc pg_catalog.pg_namespace pg_catalog.pg_roles "
                "pg_catalog.pg_trigger order by$$",
            ),
        }
        for query_key, query_variants in fixtures.items():
            for query in query_variants:
                self._assert_exact_query_rejected(query_key, query)

    def test_exact_query_contract_rejects_unqualified_sequence_mutation(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"]
        self._assert_exact_query_rejected(
            "default_acl",
            query.replace(" order by ", " and nextval('sqag_probe') is null order by ", 1),
        )

    def test_exact_query_contract_rejects_schema_qualified_sequence_mutation(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"]
        self._assert_exact_query_rejected(
            "default_acl",
            query.replace(" order by ", " and pg_catalog.nextval('sqag_probe') is null order by ", 1),
        )

    def test_exact_query_contract_rejects_unqualified_advisory_lock_mutation(self) -> None:
        query = load_manifest()["verification_queries"]["routine_acl"]
        self._assert_exact_query_rejected(
            "routine_acl",
            query.replace(" order by ", " and pg_advisory_lock(1) is null order by ", 1),
        )

    def test_exact_query_contract_rejects_schema_qualified_advisory_lock_mutation(self) -> None:
        query = load_manifest()["verification_queries"]["routine_acl"]
        self._assert_exact_query_rejected(
            "routine_acl",
            query.replace(" order by ", " and pg_catalog.pg_advisory_lock(1) is null order by ", 1),
        )

    def test_exact_query_contract_rejects_transaction_advisory_lock_mutation(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"]
        self._assert_exact_query_rejected(
            "default_acl",
            query.replace(" order by ", " and pg_advisory_xact_lock(1) is null order by ", 1),
        )

    def test_exact_query_contract_rejects_schema_qualified_transaction_advisory_lock_mutation(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"]
        self._assert_exact_query_rejected(
            "default_acl",
            query.replace(" order by ", " and pg_catalog.pg_advisory_xact_lock(1) is null order by ", 1),
        )

    def test_exact_query_contract_rejects_unqualified_procedural_delay_mutation(self) -> None:
        query = load_manifest()["verification_queries"]["routine_acl"]
        self._assert_exact_query_rejected(
            "routine_acl",
            query.replace(" order by ", " and pg_sleep(0) is null order by ", 1),
        )

    def test_exact_query_contract_rejects_schema_qualified_procedural_delay_mutation(self) -> None:
        query = load_manifest()["verification_queries"]["routine_acl"]
        self._assert_exact_query_rejected(
            "routine_acl",
            query.replace(" order by ", " and pg_catalog.pg_sleep(0) is null order by ", 1),
        )

    def test_exact_query_contract_rejects_user_defined_callable_substitution(self) -> None:
        query = load_manifest()["verification_queries"]["routine_acl"]
        self._assert_exact_query_rejected(
            "routine_acl",
            query.replace(
                "p.proname as routine_name,\n               pg_catalog.pg_get_function_identity_arguments",
                "sqag_user_defined(p.proname) as routine_name,\n       pg_catalog.pg_get_function_identity_arguments",
                1,
            ),
        )

    def test_exact_query_contract_rejects_side_effecting_extra_projection(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"]
        self._assert_exact_query_rejected(
            "default_acl",
            query.replace(
                "where d.defaclobjtype in ('r', 'S', 'f', 'n', 'T')",
                "where d.defaclobjtype in ('r', 'S', 'f', 'n', 'T') and exists (select 1, nextval('sqag_probe') "
                "from pg_catalog.pg_roles probe)",
                1,
            ),
        )

    def test_exact_query_contract_rejects_side_effecting_predicate(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"]
        self._assert_exact_query_rejected(
            "default_acl",
            query.replace(" order by ", " and pg_sleep(0) is null order by ", 1),
        )

    def test_exact_query_contract_rejects_side_effecting_ordering_expression(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"]
        self._assert_exact_query_rejected(
            "default_acl",
            query.replace(
                "order by owner, namespace, object_type, grantee, expanded.privilege_type, expanded.is_grantable",
                "order by owner, namespace, object_type, grantee, expanded.privilege_type, "
                "expanded.is_grantable, pg_sleep(0)",
                1,
            ),
        )

    def test_exact_query_contract_rejects_projection_structure_mutations(self) -> None:
        query = load_manifest()["verification_queries"]["default_acl"]
        mutations = (
            query.replace(", expanded.is_grantable", "", 1),
            query.replace("select owner.rolname as owner,", "select owner.rolname as owner, 1 as extra,", 1),
            query.replace(
                "owner.rolname as owner,\n               coalesce(ns.nspname, '<global>') as namespace",
                "coalesce(ns.nspname, '<global>') as namespace,\n               owner.rolname as owner",
                1,
            ),
        )
        for mutation in mutations:
            self._assert_exact_query_rejected("default_acl", mutation)

    def test_exact_query_contract_rejects_relation_predicate_literal_operator_order_and_function_mutations(self) -> None:
        manifest = load_manifest()
        routine_query = manifest["verification_queries"]["routine_acl"]
        default_query = manifest["verification_queries"]["default_acl"]
        mutations = (
            ("routine_acl", routine_query.replace("pg_catalog.pg_proc", "pg_catalog.pg_class", 1)),
            ("default_acl", default_query.replace("defaclobjtype in", "defaclobjtype not in", 1)),
            ("routine_acl", routine_query.replace("n.nspname = 'public'", "n.nspname = 'private'", 1)),
            ("default_acl", default_query.replace("expanded.grantee = 0", "expanded.grantee <> 0", 1)),
            (
                "routine_acl",
                routine_query.replace(
                    "order by n.nspname, p.proname, identity_arguments, p.prokind",
                    "order by n.nspname, identity_arguments, p.proname, p.prokind",
                    1,
                ),
            ),
            ("default_acl", default_query.replace("pg_catalog.aclexplode", "pg_catalog.jsonb_array_elements", 1)),
        )
        for query_key, mutation in mutations:
            self._assert_exact_query_rejected(query_key, mutation)

    def test_query_lexer_rejects_comment_literal_and_dollar_noops(self) -> None:
        fixtures = {
            "default_acl": [
                "select 1 /* pg_catalog.pg_default_acl defaclrole defaclnamespace defaclobjtype defaclacl privilege_type is_grantable grantee owner namespace order by cross join lateral aclexplode( case when expanded.grantee = 0 then 'PUBLIC' left join pg_catalog.pg_roles defaclobjtype in ('r', 'S', 'f') */",
                "select 1 -- pg_catalog.pg_default_acl defaclrole defaclnamespace defaclobjtype defaclacl privilege_type is_grantable grantee owner namespace order by cross join lateral aclexplode( case when expanded.grantee = 0 then 'PUBLIC' left join pg_catalog.pg_roles defaclobjtype in ('r', 'S', 'f')\n",
                "select 'pg_catalog.pg_default_acl defaclrole defaclnamespace defaclobjtype defaclacl privilege_type is_grantable grantee owner namespace order by cross join lateral aclexplode( case when expanded.grantee = 0 then PUBLIC left join pg_catalog.pg_roles defaclobjtype in (r, S, f)'",
                'select "pg_catalog.pg_default_acl defaclrole defaclnamespace defaclobjtype defaclacl privilege_type is_grantable grantee owner namespace order by cross join lateral aclexplode case when expanded.grantee left join pg_catalog.pg_roles"',
                "select $$pg_catalog.pg_default_acl defaclrole defaclnamespace defaclobjtype defaclacl privilege_type is_grantable grantee owner namespace order by cross join lateral aclexplode( case when expanded.grantee = 0 then PUBLIC left join pg_catalog.pg_roles defaclobjtype in (r, S, f)$$",
            ],
            "routine_acl": [
                "select 1 /* pg_catalog.pg_proc pg_catalog.pg_namespace pg_catalog.pg_roles pg_catalog.pg_trigger pg_get_function_identity_arguments proname proacl proowner prosecdef prokind tgfoid tgisinternal order by nspname = 'public' p.prokind in ('f', 'p', 'a', 'w') */",
                "select 1 -- pg_catalog.pg_proc pg_catalog.pg_namespace pg_catalog.pg_roles pg_catalog.pg_trigger pg_get_function_identity_arguments proname proacl proowner prosecdef prokind tgfoid tgisinternal order by nspname = 'public' p.prokind in ('f', 'p', 'a', 'w')\n",
                "select 'pg_catalog.pg_proc pg_catalog.pg_namespace pg_catalog.pg_roles pg_catalog.pg_trigger pg_get_function_identity_arguments proname proacl proowner prosecdef prokind tgfoid tgisinternal order by nspname = public p.prokind in (f, p, a, w)'",
                'select "pg_catalog.pg_proc pg_catalog.pg_namespace pg_catalog.pg_roles pg_catalog.pg_trigger pg_get_function_identity_arguments proname proacl proowner prosecdef prokind tgfoid tgisinternal order by nspname p.prokind"',
                "select $$pg_catalog.pg_proc pg_catalog.pg_namespace pg_catalog.pg_roles pg_catalog.pg_trigger pg_get_function_identity_arguments proname proacl proowner prosecdef prokind tgfoid tgisinternal order by nspname = public p.prokind in (f, p, a, w)$$",
            ],
        }
        for query_key, query_variants in fixtures.items():
            for query in query_variants:
                manifest = self._mutated_fixture(lambda m, key=query_key, value=query: m["verification_queries"].update({key: value}))
                stderr = io.StringIO()
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as fh:
                    json.dump(manifest, fh)
                    path = fh.name
                try:
                    with redirect_stderr(stderr):
                        result = validate_manifest_strictly(path)
                    self.assertEqual(result, 2, f"{query_key} no-op unexpectedly passed")
                finally:
                    Path(path).unlink(missing_ok=True)

        for query_key, required_feature in (
            ("database_acl", "pg_catalog.pg_database"),
            ("schema_acl", "pg_catalog.pg_namespace"),
            ("table_acl", "pg_catalog.pg_class"),
            ("role_attributes", "pg_catalog.pg_roles"),
            ("role_memberships", "pg_catalog.pg_auth_members"),
            ("sequence_acl", "pg_catalog.pg_class"),
            ("effective_runtime_database_privileges", "has_database_privilege"),
            ("effective_runtime_schema_privileges", "has_schema_privilege"),
            ("effective_runtime_routine_privileges", "has_function_privilege"),
            ("effective_runtime_table_privileges", "has_table_privilege"),
            ("effective_runtime_column_privileges", "has_column_privilege"),
        ):
            self._assert_query_fixture_rejected(
                query_key,
                f"select '{required_feature} datacl relacl public execute has_table_privilege'",
                f"verification_query_{query_key}_missing_semantic_feature_{required_feature}",
            )

    def test_query_lexer_rejects_multiple_and_write_statements(self) -> None:
        for query_key, canonical in load_manifest()["verification_queries"].items():
            self._assert_query_fixture_rejected(
                query_key,
                canonical + "; select 1",
                f"verification_query_{query_key}_must_be_single_executable_statement",
            )
            self._assert_query_fixture_rejected(
                query_key,
                canonical.replace("select ", "update ", 1),
                f"verification_query_{query_key}_must_be_single_read_only_select",
            )

    def test_query_lexer_rejects_unterminated_and_wrong_projection_fixtures(self) -> None:
        manifest = load_manifest()
        default_query = manifest["verification_queries"]["default_acl"]
        routine_query = manifest["verification_queries"]["routine_acl"]
        self._assert_query_fixture_rejected(
            "default_acl",
            default_query + " /* unterminated",
            "verification_query_default_acl_lexical_error_unterminated_block_comment",
        )
        self._assert_query_fixture_rejected(
            "routine_acl",
            routine_query + " $$unterminated",
            "verification_query_routine_acl_lexical_error_unterminated_dollar_quote",
        )
        with self.assertRaises(SQLLexError):
            lex_sql("select $$unterminated")
        self._assert_query_fixture_rejected(
            "default_acl",
            default_query.replace("select owner.rolname as owner", "select 1 as owner", 1),
            "verification_query_default_acl_projection_0_missing_expected_expression",
        )
        self._assert_query_fixture_rejected(
            "default_acl",
            default_query.replace("pg_default_acl", "pg_class", 1),
            "verification_query_default_acl_must_read_pg_default_acl",
        )
        self._assert_query_fixture_rejected(
            "default_acl",
            default_query.replace("cross join lateral pg_catalog.aclexplode", "join pg_catalog.aclexplode", 1),
            "verification_query_default_acl_requires_exactly_one_cross_join_lateral_aclexplode",
        )
        self._assert_query_fixture_rejected(
            "default_acl",
            default_query.replace("expanded.is_grantable", "expanded.grantable_missing"),
            "verification_query_default_acl_missing_semantic_feature_is_grantable",
        )
        self._assert_query_fixture_rejected(
            "default_acl",
            default_query.replace("when expanded.grantee = 0 then 'PUBLIC'", "when expanded.grantee = 0 then 'PRIVATE'", 1),
            "verification_query_default_acl_projection_grantee_missing_public_mapping",
        )

    def test_query_key_contract_rejects_missing_unknown_and_swapped_bindings(self) -> None:
        manifest = load_manifest()
        missing = copy.deepcopy(manifest)
        missing["verification_queries"].pop("effective_runtime_column_privileges")
        self._assert_fixture_rejected(json.dumps(missing), "verification_queries_missing_keys")

        unknown = copy.deepcopy(manifest)
        unknown["verification_queries"]["unexpected_query"] = manifest["verification_queries"]["database_acl"]
        self._assert_fixture_rejected(json.dumps(unknown), "verification_queries_unknown_keys")

        swapped = copy.deepcopy(manifest)
        swapped["verification_queries"]["effective_runtime_database_privileges"], swapped["verification_queries"]["effective_runtime_schema_privileges"] = (
            swapped["verification_queries"]["effective_runtime_schema_privileges"],
            swapped["verification_queries"]["effective_runtime_database_privileges"],
        )
        self._assert_fixture_rejected(
            json.dumps(swapped),
            "verification_query_effective_runtime_database_privileges_executable_structure_mismatch",
        )

    def test_role_membership_query_alias_and_projection_mutations_fail(self) -> None:
        query = load_manifest()["verification_queries"]["role_memberships"]
        mutations = (
            query.replace("am.admin_option", "a.admin_option", 1),
            query.replace("am.admin_option", "am.grant_option", 1),
            query.replace("am.admin_option,\n", "", 1),
            query.replace("grantor.rolname as grantor,", "", 1),
            query.replace("am.inherit_option,", "", 1),
            query.replace("am.set_option", "false as set_option", 1),
        )
        for mutation in mutations:
            self._assert_exact_query_rejected("role_memberships", mutation)

    def test_view_acl_query_key_is_mandatory_and_cannot_be_skipped(self) -> None:
        def mutate(manifest: dict[str, Any]) -> None:
            manifest["verification_queries"].pop("view_acl")

        self._assert_fixture_rejected(
            json.dumps(self._mutated_fixture(mutate)),
            "verification_queries_missing_keys",
        )

    def test_verification_query_malformed_value_fails_closed(self) -> None:
        manifest = self._mutated_fixture(
            lambda m: m["verification_queries"].update({"view_acl": ["not", "a", "query"]})
        )
        self._assert_fixture_rejected(
            json.dumps(manifest),
            "verification_query_view_acl_must_be_non_empty_string",
        )

    def test_effective_privilege_query_contracts_bind_every_privilege_and_grant_option(self) -> None:
        manifest = load_manifest()
        table_query = manifest["verification_queries"]["effective_runtime_table_privileges"]
        for privilege in TABLE_PRIVILEGES:
            self._assert_exact_query_rejected(
                "effective_runtime_table_privileges",
                table_query.replace(f"('{privilege}')", "('REPLACED')", 1),
            )
        column_query = manifest["verification_queries"]["effective_runtime_column_privileges"]
        for privilege in COLUMN_PRIVILEGES:
            self._assert_exact_query_rejected(
                "effective_runtime_column_privileges",
                column_query.replace(f"('{privilege}')", "('REPLACED')", 1),
            )
        mutations = (
            ("effective_runtime_table_privileges", table_query.replace("has_table_privilege", "has_schema_privilege", 1)),
            (
                "effective_runtime_table_privileges",
                table_query.replace("c.relkind in ('r', 'p', 'f')", "c.relkind in ('r', 'p')", 1),
            ),
            ("effective_runtime_column_privileges", column_query.replace("a.attname", "c.relname", 1)),
            ("effective_runtime_column_privileges", column_query.replace("has_column_privilege", "has_table_privilege", 1)),
            (
                "effective_runtime_database_privileges",
                manifest["verification_queries"]["effective_runtime_database_privileges"].replace(
                    " WITH GRANT OPTION", "", 1
                ),
            ),
            (
                "effective_runtime_schema_privileges",
                manifest["verification_queries"]["effective_runtime_schema_privileges"].replace(
                    "has_schema_privilege", "has_database_privilege", 1
                ),
            ),
        )
        for query_key, mutation in mutations:
            self._assert_exact_query_rejected(query_key, mutation)

        source_identity_mutations = (
            ("table_acl", manifest["verification_queries"]["table_acl"]),
            (
                "effective_runtime_table_privileges",
                table_query.replace(
                    "c.relkind in ('r', 'p', 'f')",
                    "c.relkind in ('r', 'p')",
                    1,
                ),
            ),
            (
                "effective_runtime_table_privileges",
                table_query.replace(
                    "has_table_privilege('sqag_migrator', c.oid, 'SELECT') as owner_select,",
                    "",
                    1,
                ),
            ),
            (
                "effective_runtime_table_privileges",
                table_query.replace(
                    "has_table_privilege('sqag_migrator', c.oid, 'SELECT') as owner_select",
                    "has_table_privilege('sqag_runtime', c.oid, 'SELECT') as owner_select",
                    1,
                ),
            ),
            (
                "effective_runtime_table_privileges",
                table_query.replace(
                    "c.relrowsecurity as row_security_enabled,",
                    "",
                    1,
                ),
            ),
            (
                "effective_runtime_table_privileges",
                table_query.replace(
                    "c.relforcerowsecurity as row_security_forced,",
                    "",
                    1,
                ),
            ),
            (
                "effective_runtime_table_privileges",
                table_query.replace(
                    ") as has_inheritance_descendants,",
                    "",
                    1,
                ),
            ),
            (
                "effective_runtime_column_privileges",
                column_query.replace(
                    "c.relkind in ('r', 'p', 'f')",
                    "c.relkind in ('r', 'p')",
                    1,
                ),
            ),
        )
        for query_key, query in source_identity_mutations:
            if query_key == "table_acl":
                query = query.replace(
                    "c.relname like 'sqag_' || chr(37)",
                    "c.relname like 'wrong_source'",
                    1,
                )
            self._assert_exact_query_rejected(query_key, query)


    def test_h54_database_operability_controls_fail_closed(self) -> None:
        manifest = load_manifest()
        base = {
            "database_name": "fixture",
            "database_owner": "database_owner",
            "datallowconn": True,
            "datconnlimit": -1,
            "datacl": [],
            "acl_entries": [
                {"grantee": "PUBLIC", "grantor": "database_owner", "privilege_type": "CONNECT", "is_grantable": False},
                {"grantee": "sqag_runtime", "grantor": "database_owner", "privilege_type": "CONNECT", "is_grantable": False},
                {"grantee": "sqag_migrator", "grantor": "database_owner", "privilege_type": "CONNECT", "is_grantable": False},
                {"grantee": "sqag_migrator", "grantor": "database_owner", "privilege_type": "CREATE", "is_grantable": False},
                {"grantee": "sqag_migrator", "grantor": "database_owner", "privilege_type": "TEMPORARY", "is_grantable": False},
            ],
        }

        def errors_for(candidate: dict[str, Any], candidate_manifest: dict[str, Any] = manifest) -> list[str]:
            errors: list[str] = []
            contract_validator._validate_database_acl_evidence(
                [candidate],
                errors,
                runtime_role="sqag_runtime",
                manifest=candidate_manifest,
            )
            return errors

        self.assertEqual(errors_for(base), [])
        disallowed_connections = copy.deepcopy(base)
        disallowed_connections["datallowconn"] = False
        self.assertIn("database_acl_datallowconn_forbidden", errors_for(disallowed_connections))
        unlimited_block = copy.deepcopy(base)
        unlimited_block["datconnlimit"] = 0
        self.assertTrue(
            any("datconnlimit_policy_mismatch" in error for error in errors_for(unlimited_block)),
            errors_for(unlimited_block),
        )
        outside_policy = copy.deepcopy(base)
        outside_policy["datconnlimit"] = 1
        self.assertTrue(
            any("datconnlimit_policy_mismatch" in error for error in errors_for(outside_policy)),
            errors_for(outside_policy),
        )
        missing = copy.deepcopy(base)
        missing.pop("datallowconn")
        self.assertTrue(any("missing_keys: datallowconn" in error for error in errors_for(missing)), errors_for(missing))
        malformed = copy.deepcopy(base)
        malformed["datconnlimit"] = "unlimited"
        self.assertIn("database_acl_evidence_datconnlimit_must_be_int", errors_for(malformed))
        contradictory_manifest = copy.deepcopy(manifest)
        contradictory_manifest["database_acl"]["operability"]["datconnlimit"] = 1
        self.assertTrue(
            any("database_acl_operability_datconnlimit_invalid" in error for error in errors_for(base, contradictory_manifest)),
            errors_for(base, contradictory_manifest),
        )
        self.assertEqual(errors_for(base), [])


class RuntimeMembershipEdgeEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest()

    def _errors(
        self,
        rows: list[dict[str, Any]],
        manifest: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        return contract_validator.validate_runtime_membership_edges(
            manifest or self.manifest,
            rows,
        )

    def _assert_rejected(
        self,
        rows: list[dict[str, Any]],
        expected_fragment: str,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        errors = self._errors(rows, manifest)
        self.assertTrue(
            any(expected_fragment in error for error in errors),
            f"missing {expected_fragment!r} in {errors!r}",
        )

    @staticmethod
    def _membership_row(
        role: str,
        member: str,
        *,
        grantor: str = "unrelated_grantor",
        admin_option: bool = False,
        inherit_option: bool = False,
        set_option: bool = False,
    ) -> dict[str, Any]:
        return {
            "role": role,
            "member": member,
            "grantor": grantor,
            "admin_option": admin_option,
            "inherit_option": inherit_option,
            "set_option": set_option,
        }

    def test_exact_postgresql17_creator_admin_edge_is_accepted(self) -> None:
        self.assertEqual(self._errors([copy.deepcopy(PRODUCTION_PROVIDER_CONTROL_ROW)]), ())

    def test_runtime_as_member_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "role": "sqag_parent", "member": "sqag_runtime"}
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "runtime_as_member")

    def test_runtime_inherited_role_is_rejected(self) -> None:
        row = {
            **PRODUCTION_PROVIDER_CONTROL_ROW,
            "role": "sqag_parent",
            "member": "sqag_runtime",
            "inherit_option": True,
        }
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "runtime_inherit_path")

    def test_runtime_set_authority_is_rejected(self) -> None:
        row = {
            **PRODUCTION_PROVIDER_CONTROL_ROW,
            "role": "sqag_parent",
            "member": "sqag_runtime",
            "set_option": True,
        }
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "runtime_set_path")

    def test_second_runtime_as_parent_edge_is_rejected(self) -> None:
        second = {**PRODUCTION_PROVIDER_CONTROL_ROW, "member": "sqag_other_provider"}
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, second], "runtime_edge_count")

    def test_wrong_provider_member_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "member": "unknown_provider"}
        self._assert_rejected([row], "provider_control_edge_tuple")

    def test_wrong_grantor_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "grantor": "postgres"}
        self._assert_rejected([row], "provider_control_edge_tuple")

    def test_admin_option_false_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "admin_option": False}
        self._assert_rejected([row], "provider_control_edge_tuple")

    def test_inherit_option_true_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "inherit_option": True}
        self._assert_rejected([row], "provider_control_edge_tuple")

    def test_set_option_true_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "set_option": True}
        self._assert_rejected([row], "provider_control_edge_tuple")

    def test_provider_edge_involving_sqag_app_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "member": "sqag_app"}
        self._assert_rejected([row], "forbidden_provider_control_role_sqag_app")

    def test_provider_edge_involving_sqag_migrator_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "member": "sqag_migrator"}
        self._assert_rejected([row], "forbidden_provider_control_role_sqag_migrator")

    def test_provider_edge_involving_neon_superuser_is_rejected(self) -> None:
        row = {**PRODUCTION_PROVIDER_CONTROL_ROW, "member": "neon_superuser"}
        self._assert_rejected([row], "forbidden_provider_control_role_neon_superuser")

    def test_recursive_runtime_membership_path_is_rejected(self) -> None:
        reverse = {
            **PRODUCTION_PROVIDER_CONTROL_ROW,
            "role": "neondb_owner",
            "member": "sqag_runtime",
            "inherit_option": True,
            "set_option": True,
        }
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, reverse], "recursive_runtime_membership")

    def test_effective_runtime_privilege_introduced_through_membership_is_rejected(self) -> None:
        row = {
            **PRODUCTION_PROVIDER_CONTROL_ROW,
            "role": "privileged_parent",
            "member": "sqag_runtime",
            "inherit_option": True,
        }
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "runtime_privilege_membership_path")

    def test_unknown_provider_control_classification_is_rejected(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["roles"]["runtime"]["provider_control_edges"][0]["classification"] = "unknown"
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW], "classification", manifest)

    def test_missing_required_provider_control_field_is_rejected(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["roles"]["runtime"]["provider_control_edges"][0].pop("grantor")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW], "missing_keys", manifest)

    def test_duplicate_provider_control_edges_are_rejected(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        manifest["roles"]["runtime"]["provider_control_edges"].append(
            copy.deepcopy(manifest["roles"]["runtime"]["provider_control_edges"][0])
        )
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW], "provider_control_edges_count", manifest)

    def test_unrelated_parent_to_sqag_migrator_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "sqag_migrator")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_unrelated_parent_to_sqag_app_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "sqag_app")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_unrelated_parent_to_neon_superuser_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "neon_superuser")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_sqag_migrator_to_unrelated_member_is_rejected(self) -> None:
        row = self._membership_row("sqag_migrator", "unrelated_member")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_sqag_app_to_unrelated_member_is_rejected(self) -> None:
        row = self._membership_row("sqag_app", "unrelated_member")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_neon_superuser_to_unrelated_member_is_rejected(self) -> None:
        row = self._membership_row("neon_superuser", "unrelated_member")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_role_edge_forbidden")

    def test_protected_role_used_as_grantor_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "unrelated_member", grantor="sqag_migrator")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_grantor_forbidden")

    def test_inherit_true_on_unrelated_parent_protected_member_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "sqag_migrator", inherit_option=True)
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_inherit_option_forbidden")

    def test_set_true_on_unrelated_parent_protected_member_is_rejected(self) -> None:
        row = self._membership_row("unrelated_parent", "sqag_app", set_option=True)
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_set_option_forbidden")

    def test_admin_true_on_unauthorised_protected_role_row_is_rejected(self) -> None:
        row = self._membership_row("neon_superuser", "unrelated_member", admin_option=True)
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "protected_admin_option_forbidden")

    def test_multiple_protected_role_rows_alongside_exact_edge_are_rejected(self) -> None:
        rows = [
            PRODUCTION_PROVIDER_CONTROL_ROW,
            self._membership_row("unrelated_parent", "sqag_migrator"),
            self._membership_row("sqag_app", "unrelated_member"),
        ]
        self._assert_rejected(rows, "protected_role_row_count_invalid")

    def test_recursive_protected_role_path_not_beginning_with_runtime_is_rejected(self) -> None:
        rows = [
            PRODUCTION_PROVIDER_CONTROL_ROW,
            self._membership_row("sqag_migrator", "unrelated_bridge"),
            self._membership_row("unrelated_bridge", "sqag_migrator"),
        ]
        self._assert_rejected(rows, "recursive_protected_role_membership_path")

    def test_duplicate_unrelated_membership_rows_are_rejected(self) -> None:
        unrelated = self._membership_row("unrelated_parent", "unrelated_member")
        self._assert_rejected(
            [PRODUCTION_PROVIDER_CONTROL_ROW, unrelated, copy.deepcopy(unrelated)],
            "duplicate_role_membership_row",
        )

    def test_unknown_participant_connected_to_protected_role_is_rejected(self) -> None:
        row = self._membership_row("unknown_parent", "neondb_owner")
        self._assert_rejected([PRODUCTION_PROVIDER_CONTROL_ROW, row], "unknown_protected_edge_participant")

    def test_truly_unrelated_membership_row_is_outside_contract(self) -> None:
        unrelated = self._membership_row("unrelated_parent", "unrelated_member")
        self.assertEqual(self._errors([PRODUCTION_PROVIDER_CONTROL_ROW, unrelated]), ())

    def test_malformed_membership_rows_fail_closed(self) -> None:
        def evaluate(rows: Any) -> tuple[str, ...]:
            return contract_validator.validate_runtime_membership_edges(
                self.manifest,
                cast(list[dict[str, Any]], rows),
            )

        def expect_category(label: str, rows: Any, category: str) -> None:
            with self.subTest(case=label):
                errors = evaluate(rows)
                self.assertIn(category, errors)

        for label, rows in (
            ("rows_none", None),
            ("rows_tuple", (PRODUCTION_PROVIDER_CONTROL_ROW,)),
            ("rows_dictionary", {"row": PRODUCTION_PROVIDER_CONTROL_ROW}),
            ("rows_string", "synthetic_rows"),
        ):
            expect_category(label, rows, "role_membership_rows_must_be_list")

        for value_label, value in (
            ("none", None),
            ("string", "synthetic"),
            ("integer", 7),
            ("list", []),
            ("boolean", False),
        ):
            expect_category(
                f"non_object_{value_label}",
                [value],
                "role_membership_row_0_must_be_object",
            )

        for key in ("role", "member", "grantor", "admin_option", "inherit_option", "set_option"):
            row = copy.deepcopy(PRODUCTION_PROVIDER_CONTROL_ROW)
            row.pop(key)
            expect_category(
                f"missing_{key}",
                [row],
                f"role_membership_row_0_missing_keys: {key}",
            )

        for key in ("unexpected_scalar", "unexpected_second"):
            row = copy.deepcopy(PRODUCTION_PROVIDER_CONTROL_ROW)
            row[key] = "synthetic" if key == "unexpected_scalar" else False
            expect_category(
                f"unexpected_{key}",
                [row],
                f"role_membership_row_0_unknown_keys: {key}",
            )

        for field in ("role", "member", "grantor"):
            for value_label, value in (
                ("none", None),
                ("integer", 7),
                ("boolean", False),
                ("list", []),
                ("dictionary", {}),
                ("empty", ""),
                ("whitespace", "   "),
            ):
                row = copy.deepcopy(PRODUCTION_PROVIDER_CONTROL_ROW)
                row[field] = value
                expect_category(
                    f"invalid_{field}_{value_label}",
                    [row],
                    f"role_membership_row_0_{field}_must_be_non_empty_string",
                )

        for field in ("admin_option", "inherit_option", "set_option"):
            for value_label, value in (
                ("zero", 0),
                ("one", 1),
                ("false_string", "false"),
                ("true_string", "true"),
                ("none", None),
                ("empty_list", []),
            ):
                row = copy.deepcopy(PRODUCTION_PROVIDER_CONTROL_ROW)
                row[field] = value
                expect_category(
                    f"invalid_{field}_{value_label}",
                    [row],
                    f"role_membership_row_0_{field}_must_be_bool",
                )

        malformed_unrelated = self._membership_row("unrelated_parent", "unrelated_member")
        malformed_unrelated.pop("grantor")
        expect_category(
            "malformed_unrelated_row_is_not_ignored",
            [PRODUCTION_PROVIDER_CONTROL_ROW, malformed_unrelated],
            "role_membership_row_1_missing_keys: grantor",
        )

        expect_category(
            "malformed_row_after_exact_edge_fails_closed",
            [PRODUCTION_PROVIDER_CONTROL_ROW, "synthetic_row"],
            "role_membership_row_1_must_be_object",
        )

        mixed = copy.deepcopy(PRODUCTION_PROVIDER_CONTROL_ROW)
        mixed["role"] = None
        mixed["admin_option"] = 0
        mixed_errors = evaluate([mixed])
        self.assertIn("role_membership_row_0_role_must_be_non_empty_string", mixed_errors)
        self.assertIn("role_membership_row_0_admin_option_must_be_bool", mixed_errors)

class ClassifiedTableColumnContractEvaluatorTest(unittest.TestCase):
    """Migration-bound exact column identity regressions for all classified tables."""

    def setUp(self) -> None:
        self.manifest = load_manifest()
        self.table_rows, self.column_rows = _complete_classified_authority_evidence(
            self.manifest
        )

    def _errors_after_contract_mutation(
        self, mutation
    ) -> tuple[str, ...]:
        table_rows = copy.deepcopy(self.table_rows)
        target_rows = [
            row for row in table_rows if row["table_name"] == "sqag_profiles"
        ]
        contract = copy.deepcopy(target_rows[0]["column_contract"])
        mutation(contract)
        for row in target_rows:
            row["column_contract"] = copy.deepcopy(contract)
            row["visible_column_count"] = sum(
                not column["is_dropped"] for column in contract
            )
        return contract_validator.evaluate_public_table_like_authority(
            self.manifest, table_rows, copy.deepcopy(self.column_rows)
        )

    def test_migration_authority_derives_all_sixteen_exact_table_contracts(self) -> None:
        contracts = contract_validator.classified_table_column_contract()
        self.assertEqual(set(contracts), ALL_TABLES)
        self.assertEqual(len(contracts), 16)
        self.assertEqual(sum(len(columns) for columns in contracts.values()), 195)
        self.assertTrue(
            all(
                column["ordinal"] == ordinal
                and column["type_schema"] == "pg_catalog"
                and column["is_dropped"] is False
                for columns in contracts.values()
                for ordinal, column in enumerate(columns, start=1)
            )
        )
        self.assertEqual(
            [column["name"] for column in contracts["sqag_profiles"]],
            ["workspace_id", "profile_id", "payload_json", "created_at", "updated_at"],
        )

    def test_classified_column_rename_with_same_visible_count_is_rejected(self) -> None:
        errors = self._errors_after_contract_mutation(
            lambda columns: columns[2].update({"name": "payload_json_renamed"})
        )
        self.assertTrue(
            any("column_name_mismatch_ordinal_3" in error for error in errors),
            errors,
        )

    def test_classified_column_same_name_wrong_type_is_rejected(self) -> None:
        errors = self._errors_after_contract_mutation(
            lambda columns: columns[2].update(
                {
                    "type_oid": 1043,
                    "type_name": "varchar",
                    "type_modifier": 68,
                }
            )
        )
        self.assertTrue(
            any("column_type_mismatch_payload_json" in error for error in errors),
            errors,
        )

    def test_classified_column_missing_expected_identity_is_rejected(self) -> None:
        errors = self._errors_after_contract_mutation(
            lambda columns: columns.pop(2)
        )
        self.assertTrue(
            any("column_missing_payload_json" in error for error in errors),
            errors,
        )

    def test_classified_column_dropped_replacement_masking_visible_count_is_rejected(self) -> None:
        def replace(columns: list[dict[str, Any]]) -> None:
            dropped = columns[2]
            dropped.update(
                {
                    "name": "........pg.dropped.3........",
                    "type_oid": 0,
                    "type_schema": None,
                    "type_name": None,
                    "type_modifier": -1,
                    "collation": "none",
                    "is_dropped": True,
                }
            )
            columns.append(
                {
                    "ordinal": len(columns) + 1,
                    "name": "replacement_payload_json",
                    "type_oid": 25,
                    "type_schema": "pg_catalog",
                    "type_name": "text",
                    "type_modifier": -1,
                    "collation": "database_default",
                    "is_dropped": False,
                }
            )

        errors = self._errors_after_contract_mutation(replace)
        self.assertTrue(any("column_dropped_slot_3" in error for error in errors), errors)
        self.assertTrue(any("column_missing_payload_json" in error for error in errors), errors)
        self.assertTrue(
            any("column_unexpected_replacement_payload_json" in error for error in errors),
            errors,
        )


    def test_collation_identity_is_explicit_and_fixture_stable(self) -> None:
        contracts = contract_validator.classified_table_column_contract()
        collatable = [
            column
            for columns in contracts.values()
            for column in columns
            if column["type_name"] in {"text", "varchar", "bpchar", "name"}
        ]
        non_collatable = [
            column
            for columns in contracts.values()
            for column in columns
            if column["type_name"] not in {"text", "varchar", "bpchar", "name"}
        ]
        self.assertTrue(collatable)
        self.assertTrue(non_collatable)
        self.assertTrue(all(column["collation"] == "database_default" for column in collatable))
        self.assertTrue(all(column["collation"] == "none" for column in non_collatable))
        self.assertEqual(
            contracts,
            contract_validator.classified_table_column_contract(),
        )

    def test_behaviour_changing_collation_identity_is_rejected(self) -> None:
        errors = self._errors_after_contract_mutation(
            lambda columns: columns[2].update({"collation": "pg_catalog.C"})
        )
        self.assertTrue(
            any("column_collation_mismatch_payload_json" in error for error in errors),
            errors,
        )

    def test_non_collatable_column_cannot_claim_collation(self) -> None:
        table_rows = copy.deepcopy(self.table_rows)
        target_rows = [
            row for row in table_rows if row["table_name"] == "sqag_quote_publication_artifacts"
        ]
        contract = copy.deepcopy(target_rows[0]["column_contract"])
        content_blob = next(column for column in contract if column["name"] == "content_blob")
        content_blob["collation"] = "database_default"
        for row in target_rows:
            row["column_contract"] = copy.deepcopy(contract)
        errors = contract_validator.evaluate_public_table_like_authority(
            self.manifest, table_rows, copy.deepcopy(self.column_rows)
        )
        self.assertTrue(
            any("column_collation_mismatch_content_blob" in error for error in errors),
            errors,
        )

    def test_missing_or_malformed_collation_identity_fails_closed(self) -> None:
        missing_errors = self._errors_after_contract_mutation(
            lambda columns: columns[0].pop("collation")
        )
        self.assertTrue(any("missing_keys: collation" in error for error in missing_errors), missing_errors)
        malformed_errors = self._errors_after_contract_mutation(
            lambda columns: columns[0].update({"collation": None})
        )
        self.assertTrue(any("collation_must_be_non_empty_string" in error for error in malformed_errors), malformed_errors)


class ViewAuthorityEvaluatorTest(unittest.TestCase):
    """Evaluator regressions for the closed ordinary/materialized view contract.

    The canonical relation/view query must enumerate every `public` ordinary
    view (`relkind='v'`) and materialized view (`relkind='m'`). The evaluator
    must fail closed on materialized-view runtime authority, unclassified
    ordinary-view runtime authority, runtime ownership, grant options, and any
    posture outside the locked contract. The classified legacy view is
    optional: absent on a fresh canonical production-migration database, and
    when present it must be an ordinary view with exactly bounded SELECT.
    """

    def setUp(self) -> None:
        self.manifest = load_manifest()

    @staticmethod
    def _view_row(
        name: str,
        *,
        schema_name: str = "public",
        kind: str = "v",
        owner: str = "sqag_migrator",
        relation_acl: str | None = None,
        acl_entries: list[dict[str, Any]] | None = None,
        runtime_privileges: list[dict[str, Any]] | None = None,
        runtime_select: bool = False,
        runtime_select_grantable: bool = False,
        include_evidence: bool = True,
    ) -> dict[str, Any]:
        if acl_entries is None:
            acl_entries = [
                {
                    "grantee": owner,
                    "grantor": owner,
                    "privilege_type": privilege,
                    "is_grantable": False,
                }
                for privilege in TABLE_PRIVILEGES
            ]
            if runtime_select:
                acl_entries.append(
                    {
                        "grantee": "sqag_runtime",
                        "grantor": owner,
                        "privilege_type": "SELECT",
                        "is_grantable": runtime_select_grantable,
                    }
                )
        if runtime_privileges is None:
            runtime_privileges = [
                {
                    "privilege_type": privilege,
                    "effective": runtime_select and privilege == "SELECT",
                    "is_grantable": runtime_select_grantable and privilege == "SELECT",
                }
                for privilege in TABLE_PRIVILEGES
            ]
        row = {
            "schema_name": schema_name,
            "relation_name": name,
            "relation_kind": kind,
            "owner": owner,
            "relation_acl": relation_acl,
            "acl_entries": copy.deepcopy(acl_entries),
            "runtime_privileges": copy.deepcopy(runtime_privileges),
            "runtime_select": runtime_select,
            "runtime_select_grantable": runtime_select_grantable,
        }
        if include_evidence:
            return ViewAuthorityEvaluatorTest._extended_view_row(
                name,
                kind=kind,
                owner=owner,
                runtime_select=runtime_select,
                runtime_select_grantable=runtime_select_grantable,
                base_row=row,
            )
        return row

    @classmethod
    def _extended_view_row(
        cls,
        name: str,
        *,
        kind: str = 'v',
        owner: str = 'sqag_migrator',
        runtime_select: bool = False,
        runtime_select_grantable: bool = False,
        base_row: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        row = copy.deepcopy(base_row) if base_row is not None else cls._view_row(
            name,
            kind=kind,
            owner=owner,
            runtime_select=runtime_select,
            runtime_select_grantable=runtime_select_grantable,
            include_evidence=False,
        )
        if name == 'sqag_quote_artifacts':
            columns = [
                {'ordinal': 1, 'name': 'workspace_id', 'type_oid': 25, 'type_schema': 'pg_catalog', 'type_name': 'text', 'type_modifier': -1, 'type_sql': 'text'},
                {'ordinal': 2, 'name': 'session_id', 'type_oid': 25, 'type_schema': 'pg_catalog', 'type_name': 'text', 'type_modifier': -1, 'type_sql': 'text'},
                {'ordinal': 3, 'name': 'artifact_kind', 'type_oid': 25, 'type_schema': 'pg_catalog', 'type_name': 'text', 'type_modifier': -1, 'type_sql': 'text'},
                {'ordinal': 4, 'name': 'filename', 'type_oid': 25, 'type_schema': 'pg_catalog', 'type_name': 'text', 'type_modifier': -1, 'type_sql': 'text'},
                {'ordinal': 5, 'name': 'content_type', 'type_oid': 25, 'type_schema': 'pg_catalog', 'type_name': 'text', 'type_modifier': -1, 'type_sql': 'text'},
                {'ordinal': 6, 'name': 'size_bytes', 'type_oid': 20, 'type_schema': 'pg_catalog', 'type_name': 'int8', 'type_modifier': -1, 'type_sql': 'bigint'},
                {'ordinal': 7, 'name': 'content_blob', 'type_oid': 17, 'type_schema': 'pg_catalog', 'type_name': 'bytea', 'type_modifier': -1, 'type_sql': 'bytea'},
                {'ordinal': 8, 'name': 'created_at', 'type_oid': 25, 'type_schema': 'pg_catalog', 'type_name': 'text', 'type_modifier': -1, 'type_sql': 'text'},
                {'ordinal': 9, 'name': 'updated_at', 'type_oid': 25, 'type_schema': 'pg_catalog', 'type_name': 'text', 'type_modifier': -1, 'type_sql': 'text'},
            ]
            definition = 'select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, content_blob, created_at, updated_at from legacy_quote_artifacts_source'
            dependencies = [{'schema': 'public', 'relation_name': 'legacy_quote_artifacts_source', 'relation_kind': 'r', 'dependency_type': 'n'}]
        else:
            columns = [{'ordinal': 1, 'name': 'marker', 'type_oid': 25, 'type_schema': 'pg_catalog', 'type_name': 'text', 'type_modifier': -1, 'type_sql': 'text'}]
            definition = 'select marker'
            dependencies = []
        row.update({
            'column_acl_entries': [
                {
                    'relation_name': name,
                    'relation_kind': kind,
                    'column_number': column['ordinal'],
                    'column_name': column['name'],
                    'acl_entries': [],
                    'runtime_privileges': [
                        {'privilege_type': privilege, 'effective': runtime_select and privilege == 'SELECT', 'is_grantable': runtime_select_grantable and privilege == 'SELECT'}
                        for privilege in COLUMN_PRIVILEGES
                    ],
                }
                for column in columns
            ],
            'view_definition': definition,
            'view_dependencies': dependencies,
            'view_columns': columns,
            'relation_options': {},
            'view_security': {'security_barrier': False, 'security_invoker': False, 'check_option': None},
        })
        return row

    @staticmethod
    def _bound_source_evidence() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        table_rows = [
            {
                "schema_name": "public",
                "table_name": "legacy_quote_artifacts_source",
                "relation_persistence": "p",
                "acl_entries": [],
                "relation_kind": "r",
                "owner": "sqag_migrator",
                "owner_select": True,
                "visible_column_count": len(contract_validator.LEGACY_VIEW_DEFINITION["columns"]),
                "column_contract": [
                    {
                        "ordinal": column["ordinal"],
                        "name": column["name"],
                        "type_oid": column["type_oid"],
                        "type_schema": column["type_schema"],
                        "type_name": column["type_name"],
                        "type_modifier": column["type_modifier"],
                        "collation": (
                            "database_default"
                            if column["type_name"] in {"text", "varchar", "bpchar", "name"}
                            else "none"
                        ),
                        "is_dropped": False,
                    }
                    for column in contract_validator.LEGACY_VIEW_DEFINITION["columns"]
                ],
                "row_security_enabled": False,
                "row_security_forced": False,
                "has_inheritance_descendants": False,
                "has_inheritance_parents": False,
                "is_partition": False,
                "partition_bound": None,
                "privilege_type": privilege,
                "effective": False,
                "is_grantable": False,
            }
            for privilege in TABLE_PRIVILEGES
        ]
        column_rows = [
            {
                "schema_name": "public",
                "table_name": "legacy_quote_artifacts_source",
                "acl_entries": [],
                "column_name": column["name"],
                "privilege_type": privilege,
                "effective": False,
                "is_grantable": False,
            }
            for column in contract_validator.LEGACY_VIEW_DEFINITION["columns"]
            for privilege in COLUMN_PRIVILEGES
        ]
        return table_rows, column_rows

    def _with_classified_authority_evidence(
        self,
        table_rows: list[dict[str, Any]],
        column_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        complete_table_rows, complete_column_rows = _complete_classified_authority_evidence(
            self.manifest
        )
        return (
            complete_table_rows + copy.deepcopy(table_rows),
            complete_column_rows + copy.deepcopy(column_rows),
        )

    def _errors(self, rows: list[dict[str, Any]]) -> tuple[str, ...]:
        return contract_validator.evaluate_view_authority(self.manifest, rows)

    def _assert_rejected(self, rows: list[dict[str, Any]], expected_fragment: str) -> None:
        errors = self._errors(rows)
        self.assertTrue(
            any(expected_fragment in error for error in errors),
            f"missing {expected_fragment!r} in {errors!r}",
        )

    def _assert_source_evidence_rejected(
        self,
        view_rows: list[dict[str, Any]],
        table_rows: list[dict[str, Any]],
        column_rows: list[dict[str, Any]],
        expected_fragment: str,
    ) -> None:
        complete_table_rows, complete_column_rows = self._with_classified_authority_evidence(
            table_rows, column_rows
        )
        errors = contract_validator.evaluate_runtime_authority(
            self.manifest, view_rows, complete_table_rows, complete_column_rows
        )
        self.assertTrue(
            any(expected_fragment in error for error in errors),
            f"missing {expected_fragment!r} in {errors!r}",
        )

    def test_fresh_migration_database_without_legacy_view_is_valid(self) -> None:
        self.assertEqual(self._errors([]), ())

    def test_legacy_ordinary_view_with_bounded_select_is_accepted(self) -> None:
        self.assertEqual(
            self._errors(
                [
                    self._view_row(
                        "sqag_quote_artifacts",
                        kind="v",
                        runtime_select=True,
                    )
                ]
            ),
            (),
        )

    def test_owner_acl_completeness_rejects_each_missing_owner_privilege(self) -> None:
        base = self._view_row('sqag_quote_artifacts', runtime_select=True)
        for privilege in TABLE_PRIVILEGES:
            with self.subTest(privilege=privilege):
                row = copy.deepcopy(base)
                row['acl_entries'] = [
                    entry
                    for entry in row['acl_entries']
                    if not (
                        entry['grantee'] == 'sqag_migrator'
                        and entry['privilege_type'] == privilege
                    )
                ]
                self._assert_rejected([row], 'owner_acl_completeness')

    def test_unclassified_ordinary_view_direct_runtime_column_select_is_rejected(self) -> None:
        row = self._extended_view_row('sqag_file_artifacts')
        row['column_acl_entries'][0]['acl_entries'].append(
            {'grantee': 'sqag_runtime', 'grantor': 'sqag_migrator', 'privilege_type': 'SELECT', 'is_grantable': False}
        )
        self._assert_rejected([row], 'column_acl_runtime_authority_forbidden')

    def test_unclassified_ordinary_view_direct_runtime_column_grant_option_is_rejected(self) -> None:
        row = self._extended_view_row('sqag_file_artifacts')
        row['column_acl_entries'][0]['acl_entries'].append(
            {'grantee': 'sqag_runtime', 'grantor': 'sqag_migrator', 'privilege_type': 'SELECT', 'is_grantable': True}
        )
        self._assert_rejected([row], 'column_acl_runtime_grant_option_forbidden')

    def test_public_column_authority_is_rejected(self) -> None:
        row = self._extended_view_row('sqag_file_artifacts')
        row['column_acl_entries'][0]['acl_entries'].append(
            {'grantee': 'PUBLIC', 'grantor': 'sqag_migrator', 'privilege_type': 'SELECT', 'is_grantable': False}
        )
        self._assert_rejected([row], 'column_acl_public_authority_forbidden')

    def test_malformed_and_duplicate_column_evidence_is_rejected(self) -> None:
        malformed = self._extended_view_row('sqag_file_artifacts')
        malformed['column_acl_entries'][0].pop('column_name')
        self._assert_rejected([malformed], 'column_acl_row_0_missing_keys: column_name')

        duplicate = self._extended_view_row('sqag_file_artifacts')
        duplicate['column_acl_entries'][0]['runtime_privileges'].append(
            copy.deepcopy(duplicate['column_acl_entries'][0]['runtime_privileges'][0])
        )
        self._assert_rejected([duplicate], 'column_runtime_privilege_4_duplicate')

    def test_classified_view_additional_direct_column_acl_is_rejected(self) -> None:
        row = self._extended_view_row('sqag_quote_artifacts', runtime_select=True)
        row['column_acl_entries'][0]['acl_entries'].append(
            {'grantee': 'sqag_runtime', 'grantor': 'sqag_migrator', 'privilege_type': 'SELECT', 'is_grantable': False}
        )
        self._assert_rejected([row], 'column_acl_runtime_authority_forbidden')

    def test_materialized_view_column_authority_is_rejected(self) -> None:
        row = self._extended_view_row('sqag_mat_view', kind='m')
        row['column_acl_entries'][0]['runtime_privileges'][0]['effective'] = True
        self._assert_rejected([row], 'materialized_view_column_authority_forbidden')

    def test_classified_view_definition_binding_rejects_drift(self) -> None:
        base = self._extended_view_row('sqag_quote_artifacts', runtime_select=True)
        definition = copy.deepcopy(base)
        definition['view_definition'] = 'select session_id from legacy_quote_artifacts_source'
        self._assert_rejected([definition], 'classified_view_definition_mismatch')

        qualified = copy.deepcopy(base)
        qualified['view_definition'] = (
            'select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, '
            'content_blob, created_at, updated_at from public.legacy_quote_artifacts_source'
        )
        self.assertEqual(self._errors([qualified]), ())

        wrong_schema = copy.deepcopy(qualified)
        wrong_schema['view_definition'] = wrong_schema['view_definition'].replace(
            'public.legacy_quote_artifacts_source', 'other_schema.legacy_quote_artifacts_source'
        )
        self._assert_rejected([wrong_schema], 'classified_view_definition_mismatch')

        wrong_source = copy.deepcopy(qualified)
        wrong_source['view_definition'] = wrong_source['view_definition'].replace(
            'public.legacy_quote_artifacts_source', 'public.some_other_source'
        )
        self._assert_rejected([wrong_source], 'classified_view_definition_mismatch')

        reordered = copy.deepcopy(base)
        reordered['view_definition'] = (
            'select session_id, workspace_id, artifact_kind, filename, content_type, size_bytes, '
            'content_blob, created_at, updated_at from legacy_quote_artifacts_source'
        )
        self._assert_rejected([reordered], 'classified_view_definition_mismatch')

        def evaluate_with_complete_evidence(
            view_rows: list[dict[str, Any]],
            source_table_rows: list[dict[str, Any]],
            source_column_rows: list[dict[str, Any]],
        ) -> tuple[str, ...]:
            complete_table_rows, complete_column_rows = self._with_classified_authority_evidence(
                source_table_rows, source_column_rows
            )
            return contract_validator.evaluate_runtime_authority(
                self.manifest,
                view_rows,
                complete_table_rows,
                complete_column_rows,
            )

        source_table_rows, source_column_rows = self._bound_source_evidence()
        missing_owner_select = copy.deepcopy(source_table_rows)
        unlogged_source = copy.deepcopy(source_table_rows)
        for row in unlogged_source:
            row["relation_persistence"] = "u"
        self._assert_source_evidence_rejected(
            [base],
            unlogged_source,
            source_column_rows,
            "classified_view_source_relation_persistence_invalid",
        )

        malformed_persistence = copy.deepcopy(source_table_rows)
        for row in malformed_persistence:
            row["relation_persistence"] = "x"
        self._assert_source_evidence_rejected(
            [base],
            malformed_persistence,
            source_column_rows,
            "unknown_relation_persistence_x",
        )

        surviving_non_permanent = copy.deepcopy(source_table_rows)
        for row in surviving_non_permanent:
            row["relation_persistence"] = "u"
        self.assertEqual(
            evaluate_with_complete_evidence(
                [],
                surviving_non_permanent,
                source_column_rows,
            ),
            (),
        )
        missing_owner_select[0]["owner_select"] = False
        self._assert_source_evidence_rejected(
            [base],
            missing_owner_select,
            source_column_rows,
            "classified_view_owner_source_select_required",
        )
        other_source_owner = copy.deepcopy(source_table_rows)
        for row in other_source_owner:
            row["owner"] = "sqag_app"
        self.assertEqual(
            evaluate_with_complete_evidence(
                [base],
                other_source_owner,
                source_column_rows,
            ),
            (),
        )
        self.assertEqual(
            evaluate_with_complete_evidence(
                [base],
                source_table_rows,
                source_column_rows,
            ),
            (),
        )
        self.assertEqual(
            evaluate_with_complete_evidence(
                [],
                source_table_rows,
                source_column_rows,
            ),
            (),
        )
        surviving_table_authority = copy.deepcopy(source_table_rows)
        surviving_table_authority[0]["effective"] = True
        self._assert_source_evidence_rejected(
            [],
            surviving_table_authority,
            source_column_rows,
            "runtime_privilege_forbidden",
        )
        surviving_column_authority = copy.deepcopy(source_column_rows)
        surviving_column_authority[0]["effective"] = True
        self._assert_source_evidence_rejected(
            [],
            source_table_rows,
            surviving_column_authority,
            "runtime_privilege_forbidden",
        )
        surviving_grant_option = copy.deepcopy(source_table_rows)
        surviving_grant_option[0]["is_grantable"] = True
        self._assert_source_evidence_rejected(
            [],
            surviving_grant_option,
            source_column_rows,
            "runtime_grant_option_forbidden",
        )
        surviving_runtime_owner = copy.deepcopy(source_table_rows)
        surviving_runtime_owner[0]["owner"] = "sqag_runtime"
        self._assert_source_evidence_rejected(
            [],
            surviving_runtime_owner,
            source_column_rows,
            "runtime_owner_forbidden",
        )
        surviving_without_owner_select = copy.deepcopy(source_table_rows)
        for row in surviving_without_owner_select:
            row["owner"] = "sqag_app"
            row["owner_select"] = False
        self.assertEqual(
            evaluate_with_complete_evidence(
                [],
                surviving_without_owner_select,
                source_column_rows,
            ),
            (),
        )
        surviving_rls = copy.deepcopy(source_table_rows)
        for row in surviving_rls:
            row["row_security_enabled"] = True
            row["row_security_forced"] = True
        self.assertEqual(
            evaluate_with_complete_evidence(
                [],
                surviving_rls,
                source_column_rows,
            ),
            (),
        )
        malformed_rls = copy.deepcopy(source_table_rows)
        malformed_rls[0]["row_security_enabled"] = "false"
        self.assertTrue(
            any(
                "row_security_enabled_must_be_bool" in error
                for error in contract_validator.evaluate_runtime_authority(
                    self.manifest,
                    [],
                    malformed_rls,
                    source_column_rows,
                )
            )
        )
        self.assertEqual(
            evaluate_with_complete_evidence([], [], []),
            (),
        )
        self.assertTrue(
            any(
                "bound_source_column_evidence_missing" in error
                for error in contract_validator.evaluate_runtime_authority(
                    self.manifest,
                    [],
                    source_table_rows,
                    [],
                )
            )
        )
        self.assertTrue(
            any(
                "bound_source_table_evidence_missing" in error
                for error in contract_validator.evaluate_runtime_authority(
                    self.manifest,
                    [],
                    [],
                    source_column_rows,
                )
            )
        )
        missing_source = contract_validator.evaluate_runtime_authority(
            self.manifest, [base], [], source_column_rows
        )
        self.assertTrue(any('bound_source_table_evidence_missing' in error for error in missing_source), missing_source)

        wrong_source_schema = copy.deepcopy(source_table_rows)
        wrong_source_schema[0]['schema_name'] = 'other_schema'
        self._assert_source_evidence_rejected(
            [base],
            wrong_source_schema,
            source_column_rows,
            'bound_source_table_evidence_cardinality',
        )
        wrong_source_relation = copy.deepcopy(source_table_rows)
        wrong_source_relation[0]['table_name'] = 'some_other_source'
        self._assert_source_evidence_rejected(
            [base],
            wrong_source_relation,
            source_column_rows,
            'bound_source_table_evidence_cardinality',
        )
        for privilege in TABLE_PRIVILEGES:
            with self.subTest(source_table_privilege=privilege):
                table_case = copy.deepcopy(source_table_rows)
                next(row for row in table_case if row['privilege_type'] == privilege)['effective'] = True
                errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [base], table_case, source_column_rows
                )
                self.assertTrue(any('bound_source_table_row_' in error and 'runtime_privilege_forbidden' in error for error in errors), errors)
                absent_errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [], table_case, source_column_rows
                )
                self.assertTrue(any('bound_source_table_row_' in error and 'runtime_privilege_forbidden' in error for error in absent_errors), absent_errors)

                grant_case = copy.deepcopy(source_table_rows)
                next(row for row in grant_case if row['privilege_type'] == privilege)['is_grantable'] = True
                errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [base], grant_case, source_column_rows
                )
                self.assertTrue(any('bound_source_table_row_' in error and 'runtime_grant_option_forbidden' in error for error in errors), errors)
                absent_errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [], grant_case, source_column_rows
                )
                self.assertTrue(any('bound_source_table_row_' in error and 'runtime_grant_option_forbidden' in error for error in absent_errors), absent_errors)
        owner_case = copy.deepcopy(source_table_rows)
        owner_case[0]['owner'] = 'sqag_runtime'
        self._assert_source_evidence_rejected([base], owner_case, source_column_rows, 'runtime_owner_forbidden')
        kind_case = copy.deepcopy(source_table_rows)
        kind_case[0]['relation_kind'] = 'v'
        self._assert_source_evidence_rejected([base], kind_case, source_column_rows, 'relation_kind_invalid')
        for field, fragment in (
            ("row_security_enabled", "row_security_enabled_forbidden"),
            ("row_security_forced", "row_security_forced_forbidden"),
        ):
            with self.subTest(source_table_posture=field):
                rls_case = copy.deepcopy(source_table_rows)
                rls_case[0][field] = True
                self._assert_source_evidence_rejected([base], rls_case, source_column_rows, fragment)
        inherited_source = copy.deepcopy(source_table_rows)
        for row in inherited_source:
            row["has_inheritance_descendants"] = True
        self._assert_source_evidence_rejected(
            [base], inherited_source, source_column_rows, "inheritance_descendants_forbidden"
        )
        self.assertEqual(
            evaluate_with_complete_evidence([], inherited_source, source_column_rows),
            (),
        )
        for privilege in COLUMN_PRIVILEGES:
            with self.subTest(source_column_privilege=privilege):
                column_case = copy.deepcopy(source_column_rows)
                next(row for row in column_case if row['privilege_type'] == privilege)['effective'] = True
                self._assert_source_evidence_rejected([base], source_table_rows, column_case, 'runtime_privilege_forbidden')
                absent_errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [], source_table_rows, column_case
                )
                self.assertTrue(any('bound_source_column_row_' in error and 'runtime_privilege_forbidden' in error for error in absent_errors), absent_errors)

                grant_case = copy.deepcopy(source_column_rows)
                next(row for row in grant_case if row['privilege_type'] == privilege)['is_grantable'] = True
                self._assert_source_evidence_rejected([base], source_table_rows, grant_case, 'runtime_grant_option_forbidden')
                absent_errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [], source_table_rows, grant_case
                )
                self.assertTrue(any('bound_source_column_row_' in error and 'runtime_grant_option_forbidden' in error for error in absent_errors), absent_errors)

        dependency = copy.deepcopy(base)
        dependency['view_dependencies'] = []
        self._assert_rejected([dependency], 'classified_view_dependencies_mismatch')

        shape = copy.deepcopy(base)
        shape['view_columns'][0]['name'] = 'session_id'
        self._assert_rejected([shape], 'classified_view_columns_mismatch')

        options = copy.deepcopy(base)
        options['relation_options'] = {'security_barrier': 'true'}
        self._assert_rejected([options], 'classified_view_options_mismatch')

        security = copy.deepcopy(base)
        security['view_security']['security_invoker'] = True
        self._assert_rejected([security], 'classified_view_security_mismatch')
        table_rows, column_rows = _complete_classified_authority_evidence(self.manifest)
        table_rows.extend(
            {
                'schema_name': 'public',
                'table_name': 'unrelated_partitioned',
                'relation_kind': 'p',
                'relation_persistence': 'u',
                'acl_entries': [],
                'owner': 'sqag_app',
                'owner_select': False,
                'visible_column_count': 0,
                'column_contract': [],
                'row_security_enabled': True,
                'row_security_forced': True,
                'has_inheritance_descendants': True,
                'has_inheritance_parents': False,
                'is_partition': False,
                'partition_bound': None,
                'privilege_type': privilege,
                'effective': False,
                'is_grantable': False,
            }
            for privilege in TABLE_PRIVILEGES
        )
        self.assertEqual(
            contract_validator.evaluate_public_table_like_authority(
                self.manifest, table_rows, column_rows
            ),
            (),
        )

        def assert_table_case(
            candidate_tables: list[dict[str, Any]],
            candidate_columns: list[dict[str, Any]],
            fragment: str,
        ) -> None:
            errors = contract_validator.evaluate_public_table_like_authority(
                self.manifest, candidate_tables, candidate_columns
            )
            self.assertTrue(any(fragment in error for error in errors), errors)

        for missing_name in ('sqag_profiles', 'sqag_schema_migrations'):
            with self.subTest(classified_table_missing=missing_name):
                candidate = [row for row in table_rows if row['table_name'] != missing_name]
                assert_table_case(candidate, column_rows, f'public_table_classified_relation_missing_{missing_name}')

        incomplete_full_errors = contract_validator.evaluate_runtime_authority(
            self.manifest,
            [],
            [row for row in table_rows if row['table_name'] != 'sqag_profiles'],
            column_rows,
        )
        self.assertTrue(
            any(
                'public_table_classified_relation_missing_sqag_profiles' in error
                for error in incomplete_full_errors
            ),
            incomplete_full_errors,
        )

        for field, fragment, value in (
            ('row_security_enabled', 'row_security_enabled_forbidden', True),
            ('row_security_forced', 'row_security_forced_forbidden', True),
            ('relation_persistence', 'relation_persistence_invalid', 'u'),
            ('relation_persistence', 'relation_persistence_invalid', 'x'),
            ('owner', 'owner_invalid', 'sqag_app'),
            ('has_inheritance_descendants', 'inheritance_descendants_forbidden', True),
            ('has_inheritance_parents', 'inheritance_parents_forbidden', True),
            ('is_partition', 'partition_forbidden', True),
            ('partition_bound', 'partition_bound_forbidden', 'FOR VALUES FROM (MINVALUE) TO (MAXVALUE)'),
        ):
            with self.subTest(classified_table_state=field, value=value):
                candidate = copy.deepcopy(table_rows)
                next(row for row in candidate if row['table_name'] == 'sqag_profiles')[field] = value
                assert_table_case(candidate, column_rows, f'public.sqag_profiles_{fragment}')

        public_substitution = copy.deepcopy(table_rows)
        for row in public_substitution:
            if row['table_name'] == 'sqag_profiles':
                row['acl_entries'] = [
                    entry for entry in row['acl_entries']
                    if not (entry['grantee'] == 'sqag_runtime' and entry['privilege_type'] == 'SELECT')
                ] + [
                    {
                        'grantee': 'PUBLIC',
                        'grantor': 'sqag_migrator',
                        'privilege_type': 'SELECT',
                        'is_grantable': False,
                    }
                ]
        assert_table_case(public_substitution, column_rows, 'public.sqag_profiles_acl_public_authority_forbidden')

        wrong_grantor = copy.deepcopy(table_rows)
        for row in wrong_grantor:
            if row['table_name'] == 'sqag_profiles':
                for entry in row['acl_entries']:
                    if entry['grantee'] == 'sqag_runtime' and entry['privilege_type'] == 'SELECT':
                        entry['grantor'] = 'sqag_app'
        assert_table_case(wrong_grantor, column_rows, 'public.sqag_profiles_acl_runtime_grantor_invalid')

        table_grant_option = copy.deepcopy(table_rows)
        for row in table_grant_option:
            if row['table_name'] == 'sqag_profiles':
                for entry in row['acl_entries']:
                    if entry['grantee'] == 'sqag_runtime' and entry['privilege_type'] == 'SELECT':
                        entry['is_grantable'] = True
        assert_table_case(table_grant_option, column_rows, 'public.sqag_profiles_acl_runtime_grant_option_forbidden')

        extra_direct = copy.deepcopy(table_rows)
        for row in extra_direct:
            if row['table_name'] == 'sqag_generation_evidence':
                row['acl_entries'].append(
                    {
                        'grantee': 'sqag_runtime',
                        'grantor': 'sqag_migrator',
                        'privilege_type': 'UPDATE',
                        'is_grantable': False,
                    }
                )
        assert_table_case(extra_direct, column_rows, 'public.sqag_generation_evidence_acl_provenance_mismatch')

        column_public_substitution = copy.deepcopy(column_rows)
        for row in column_public_substitution:
            row['acl_entries'] = [
                {
                    'grantee': 'PUBLIC',
                    'grantor': 'sqag_migrator',
                    'privilege_type': 'UPDATE',
                    'is_grantable': False,
                }
            ]
        assert_table_case(table_rows, column_public_substitution, 'column_acl_public_authority_forbidden')

        column_wrong_grantor = copy.deepcopy(column_rows)
        for row in column_wrong_grantor:
            if row['acl_entries']:
                row['acl_entries'][0]['grantor'] = 'sqag_app'

        assert_table_case(table_rows, column_wrong_grantor, 'column_acl_runtime_grantor_invalid')

        column_grant_option = copy.deepcopy(column_rows)
        for row in column_grant_option:
            if row['acl_entries']:
                row['acl_entries'][0]['is_grantable'] = True

        assert_table_case(table_rows, column_grant_option, 'column_acl_runtime_grant_option_forbidden')

        column_missing = copy.deepcopy(column_rows)
        for row in column_missing:
            row['acl_entries'] = []
        assert_table_case(table_rows, column_missing, 'column_acl_provenance_mismatch_checksum_sha256')

    def test_materialized_view_effective_select_is_rejected(self) -> None:
        self._assert_rejected(
            [self._view_row("sqag_mat_view", kind="m", runtime_select=True)],
            "materialized_view",
        )

    def test_materialized_view_runtime_ownership_is_rejected(self) -> None:
        self._assert_rejected(
            [self._view_row("sqag_mat_view", kind="m", owner="sqag_runtime")],
            "materialized_view",
        )

    def test_materialized_view_runtime_grant_option_is_rejected(self) -> None:
        self._assert_rejected(
            [self._view_row("sqag_mat_view", kind="m", runtime_select_grantable=True)],
            "materialized_view",
        )

    def test_legacy_relation_as_materialized_view_is_rejected(self) -> None:
        self._assert_rejected(
            [
                self._view_row(
                    "sqag_quote_artifacts",
                    kind="m",
                    runtime_select=True,
                )
            ],
            "materialized_view",
        )

    def test_legacy_view_grant_option_drift_is_rejected(self) -> None:
        self._assert_rejected(
            [
                self._view_row(
                    "sqag_quote_artifacts",
                    kind="v",
                    runtime_select=True,
                    runtime_select_grantable=True,
                )
            ],
            "grant_option",
        )

    def test_legacy_view_ownership_drift_is_rejected(self) -> None:
        self._assert_rejected(
            [
                self._view_row(
                    "sqag_quote_artifacts",
                    kind="v",
                    owner="sqag_runtime",
                    runtime_select=True,
                )
            ],
            "runtime_relation_ownership",
        )

    def test_unclassified_ordinary_view_authority_is_rejected(self) -> None:
        self._assert_rejected(
            [self._view_row("sqag_file_artifacts", kind="v", runtime_select=True)],
            "unclassified_ordinary_view",
        )

    def test_unrelated_ordinary_view_without_runtime_authority_is_outside_contract(self) -> None:
        self.assertEqual(
            self._errors([self._view_row("sqag_file_artifacts", kind="v")]),
            (),
        )

    def test_unknown_relation_kind_fails_closed(self) -> None:
        self._assert_rejected([self._view_row("sqag_strange", kind="r")], "unknown_relation_kind")

    def test_malformed_rows_fail_closed(self) -> None:
        def expect_category(rows: Any, category: str) -> None:
            with self.subTest(category=category):
                errors = contract_validator.evaluate_view_authority(
                    self.manifest,
                    cast(list[dict[str, Any]], rows),
                )
                self.assertIn(category, errors)

        expect_category(None, "relation_view_rows_must_be_list")
        expect_category("synthetic", "relation_view_rows_must_be_list")
        expect_category({"row": self._view_row("sqag_quote_artifacts")}, "relation_view_rows_must_be_list")
        expect_category(["synthetic"], "relation_view_row_0_must_be_object")
        row = self._view_row("sqag_quote_artifacts")
        for key in (
            "relation_name",
            "relation_kind",
            "owner",
            "relation_acl",
            "acl_entries",
            "runtime_privileges",
            "runtime_select",
            "runtime_select_grantable",
        ):
            missing = copy.deepcopy(row)
            missing.pop(key)
            expect_category([missing], f"relation_view_row_0_missing_keys: {key}")
        unexpected = copy.deepcopy(row)
        unexpected["extra"] = True
        expect_category([unexpected], "relation_view_row_0_unknown_keys: extra")

    def test_structured_acl_and_effective_privilege_evidence_fail_closed(self) -> None:
        base = self._view_row("sqag_quote_artifacts", runtime_select=True)

        def add_acl(row: dict[str, Any], grantee: str, privilege: str, *, grantable: Any = False) -> None:
            row["acl_entries"].append(
                {
                    "grantee": grantee,
                    "grantor": "sqag_migrator",
                    "privilege_type": privilege,
                    "is_grantable": grantable,
                }
            )

        def set_effective(row: dict[str, Any], privilege: str, *, effective: Any = True, grantable: Any = False) -> None:
            entry = next(item for item in row["runtime_privileges"] if item["privilege_type"] == privilege)
            entry["effective"] = effective
            entry["is_grantable"] = grantable

        for privilege in ("INSERT", "UPDATE", "DELETE"):
            row = copy.deepcopy(base)
            add_acl(row, "sqag_runtime", privilege)
            set_effective(row, privilege)
            self._assert_rejected([row], f"runtime_acl_privilege_forbidden_{privilege}")

        public_select = copy.deepcopy(base)
        add_acl(public_select, "PUBLIC", "SELECT")
        self._assert_rejected([public_select], "public_acl_authority_forbidden")

        public_write = copy.deepcopy(base)
        add_acl(public_write, "PUBLIC", "INSERT")
        set_effective(public_write, "INSERT")
        self._assert_rejected([public_write], "public_acl_authority_forbidden")

        grantable = self._view_row(
            "sqag_quote_artifacts",
            runtime_select=True,
            runtime_select_grantable=True,
        )
        self._assert_rejected([grantable], "runtime_acl_grant_option_forbidden")

        unexpected = copy.deepcopy(base)
        add_acl(unexpected, "sqag_app", "SELECT")
        self._assert_rejected([unexpected], "unexpected_acl_grantee_sqag_app")

        classified_wrong_grantor = copy.deepcopy(base)
        next(
            entry
            for entry in classified_wrong_grantor["acl_entries"]
            if entry["grantee"] == "sqag_migrator" and entry["privilege_type"] == "SELECT"
        )["grantor"] = "sqag_app"
        self._assert_rejected([classified_wrong_grantor], "owner_acl_grantor_invalid")

        classified_grantable = copy.deepcopy(base)
        next(
            entry
            for entry in classified_grantable["acl_entries"]
            if entry["grantee"] == "sqag_migrator" and entry["privilege_type"] == "SELECT"
        )["is_grantable"] = True
        self._assert_rejected([classified_grantable], "owner_acl_grant_option_forbidden")

        partial_unclassified = self._extended_view_row("sqag_file_artifacts")
        partial_unclassified["acl_entries"] = [
            entry
            for entry in partial_unclassified["acl_entries"]
            if not (
                entry["grantee"] == "sqag_migrator"
                and entry["privilege_type"] == "SELECT"
            )
        ]
        self.assertEqual(self._errors([partial_unclassified]), ())

        partial_with_unrelated = copy.deepcopy(partial_unclassified)
        partial_with_unrelated["acl_entries"].append(
            {
                "grantee": "unrelated_role",
                "grantor": "unrelated_role",
                "privilege_type": "SELECT",
                "is_grantable": False,
            }
        )
        self.assertEqual(self._errors([partial_with_unrelated]), ())

        partial_materialized = self._extended_view_row("sqag_mat_view", kind="m")
        partial_materialized["acl_entries"] = [
            entry
            for entry in partial_materialized["acl_entries"]
            if not (
                entry["grantee"] == "sqag_migrator"
                and entry["privilege_type"] == "SELECT"
            )
        ]
        materialized_errors = self._errors([partial_materialized])
        self.assertTrue(any("materialized_view_unclassified" in error for error in materialized_errors))
        self.assertFalse(any("owner_acl_completeness" in error for error in materialized_errors))

        unrelated_relation = self._extended_view_row("sqag_file_artifacts")
        unrelated_relation["acl_entries"].append(
            {"grantee": "unrelated_role", "grantor": "unrelated_role", "privilege_type": "SELECT", "is_grantable": False}
        )
        self.assertEqual(self._errors([unrelated_relation]), ())

        public_relation = self._extended_view_row("sqag_file_artifacts")
        public_relation["acl_entries"].append(
            {"grantee": "PUBLIC", "grantor": "unrelated_role", "privilege_type": "SELECT", "is_grantable": False}
        )
        self._assert_rejected([public_relation], "public_acl_authority_forbidden")

        unrelated_column = self._extended_view_row("sqag_file_artifacts")
        unrelated_column["column_acl_entries"][0]["acl_entries"].append(
            {"grantee": "unrelated_role", "grantor": "unrelated_role", "privilege_type": "SELECT", "is_grantable": True}
        )
        self.assertEqual(self._errors([unrelated_column]), ())

        effective_membership = self._extended_view_row("sqag_file_artifacts")
        effective_membership["runtime_select"] = True
        effective_membership["runtime_privileges"][0]["effective"] = True
        self._assert_rejected([effective_membership], "unclassified_ordinary_view_runtime_authority")

        classified_table_rows, classified_column_rows = _complete_classified_authority_evidence(
            self.manifest
        )
        unrelated_base_table_rows, unrelated_base_column_rows = self._bound_source_evidence()

        def unrelated_evidence(
            relation_name: str, relation_kind: str
        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            table_rows = copy.deepcopy(classified_table_rows)
            column_rows = copy.deepcopy(classified_column_rows)
            unrelated_table_rows = copy.deepcopy(unrelated_base_table_rows)
            unrelated_column_rows = copy.deepcopy(unrelated_base_column_rows)
            for row in unrelated_table_rows:
                row.update(
                    {
                        "table_name": relation_name,
                        "relation_kind": relation_kind,
                        "owner": "sqag_app",
                    }
                )
            for row in unrelated_column_rows:
                row["table_name"] = relation_name
            table_rows.extend(unrelated_table_rows)
            column_rows.extend(unrelated_column_rows)
            return table_rows, column_rows

        for relation_kind in ("r", "p", "f"):
            relation_name = f"unrelated_{relation_kind}_relation"
            table_rows, column_rows = unrelated_evidence(relation_name, relation_kind)
            with self.subTest(public_relation_kind=relation_kind):
                self.assertEqual(
                    contract_validator.evaluate_runtime_authority(
                        self.manifest, [], table_rows, column_rows
                    ),
                    (),
                )
                runtime_authority = copy.deepcopy(table_rows)
                next(
                    row
                    for row in runtime_authority
                    if row["table_name"] == relation_name and row["privilege_type"] == "SELECT"
                )["effective"] = True
                runtime_errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [], runtime_authority, column_rows
                )
                self.assertTrue(any("runtime_privilege_mismatch" in error for error in runtime_errors), runtime_errors)
                column_authority = copy.deepcopy(column_rows)
                next(
                    row
                    for row in column_authority
                    if row["table_name"] == relation_name and row["privilege_type"] == "SELECT"
                )["effective"] = True
                column_errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [], table_rows, column_authority
                )
                self.assertTrue(any("runtime_privilege_mismatch" in error for error in column_errors), column_errors)
                column_grantable = copy.deepcopy(column_rows)
                next(
                    row
                    for row in column_grantable
                    if row["table_name"] == relation_name and row["privilege_type"] == "SELECT"
                )["is_grantable"] = True
                grantable_errors = contract_validator.evaluate_runtime_authority(
                    self.manifest, [], table_rows, column_grantable
                )
                self.assertTrue(any("runtime_grant_option_forbidden" in error for error in grantable_errors), grantable_errors)

        for relation_kind in ("r", "p", "f"):
            relation_name = f"unrelated_{relation_kind}_owner"
            owner_rows, owner_columns = unrelated_evidence(relation_name, relation_kind)
            next(
                row
                for row in owner_rows
                if row["table_name"] == relation_name and row["privilege_type"] == "SELECT"
            )["owner"] = "sqag_runtime"
            owner_errors = contract_validator.evaluate_runtime_authority(
                self.manifest, [], owner_rows, owner_columns
            )
            self.assertTrue(any("runtime_owner_forbidden" in error for error in owner_errors), owner_errors)

        foreign_grantable, foreign_columns = unrelated_evidence("unrelated_f_grantable", "f")
        next(
            row
            for row in foreign_grantable
            if row["table_name"] == "unrelated_f_grantable" and row["privilege_type"] == "SELECT"
        )["is_grantable"] = True
        foreign_errors = contract_validator.evaluate_runtime_authority(
            self.manifest, [], foreign_grantable, foreign_columns
        )
        self.assertTrue(
            any("runtime_grant_option_forbidden" in error for error in foreign_errors),
            foreign_errors,
        )

        malformed_acl = copy.deepcopy(base)
        malformed_acl["acl_entries"] = "not-a-list"
        self._assert_rejected([malformed_acl], "acl_entries_must_be_list")

        missing_acl_field = copy.deepcopy(base)
        missing_acl_field["acl_entries"][0].pop("grantor")
        self._assert_rejected([missing_acl_field], "acl_entry_0_missing_keys: grantor")

        unknown_acl_field = copy.deepcopy(base)
        unknown_acl_field["acl_entries"][0]["extra"] = True
        self._assert_rejected([unknown_acl_field], "acl_entry_0_unknown_keys: extra")

        invalid_privilege = copy.deepcopy(base)
        invalid_privilege["acl_entries"][0]["privilege_type"] = "DROP"
        self._assert_rejected([invalid_privilege], "acl_entry_0_invalid_privilege_type_DROP")

        invalid_grantable = copy.deepcopy(base)
        invalid_grantable["acl_entries"][0]["is_grantable"] = "false"
        self._assert_rejected([invalid_grantable], "acl_entry_0_is_grantable_must_be_bool")

        malformed_runtime = copy.deepcopy(base)
        malformed_runtime["runtime_privileges"] = [{"privilege_type": "SELECT", "effective": True}]
        self._assert_rejected([malformed_runtime], "runtime_privilege_0_missing_keys: is_grantable")

        unknown_runtime = copy.deepcopy(base)
        unknown_runtime["runtime_privileges"][0]["extra"] = True
        self._assert_rejected([unknown_runtime], "runtime_privilege_0_unknown_keys: extra")

        invalid_runtime_type = copy.deepcopy(base)
        invalid_runtime_type["runtime_privileges"][0]["is_grantable"] = "false"
        self._assert_rejected([invalid_runtime_type], "runtime_privilege_0_is_grantable_must_be_bool")

    def test_duplicate_relation_view_rows_fail_closed(self) -> None:
        row = self._view_row("sqag_quote_artifacts", runtime_select=True)
        self._assert_rejected([row, copy.deepcopy(row)], "duplicate_relation_view_row_same_relation_identity")

        conflicting_first = self._view_row("sqag_unclassified", owner="sqag_migrator")
        conflicting_second = self._view_row("sqag_unclassified", owner="sqag_app")
        self._assert_rejected(
            [conflicting_first, conflicting_second],
            "duplicate_relation_view_row_same_relation_identity",
        )


class RequirementEvidenceMapTest(unittest.TestCase):
    def test_requirement_evidence_map_is_exact_and_discoverable(self) -> None:
        self.assertEqual(tuple(REQUIREMENT_EVIDENCE), REQUIREMENT_IDS)
        self.assertEqual(len(REQUIREMENT_EVIDENCE), 38)
        discovered = _discover_test_method_names()
        for requirement_id, entry in REQUIREMENT_EVIDENCE.items():
            self.assertEqual(set(entry), {"requirement", "evidence_type", "evidence"}, requirement_id)
            self.assertTrue(entry["requirement"].strip(), requirement_id)
            self.assertIn(entry["evidence_type"], {"static", "postgresql", "documentation"}, requirement_id)
            self.assertIn(entry["evidence"], discovered, requirement_id)

    def test_documentation_requirement_ids_match_canonical_map(self) -> None:
        documentation = (ROOT / "docs" / "runtime-privilege-contract.md").read_text(encoding="utf-8")
        documented_rows = re.findall(
            r"^\|\s*(R\d{2})\s*\|\s*(.*?)\s*\|\s*`([^`]+)`\s*\|$",
            documentation,
            flags=re.MULTILINE,
        )
        self.assertEqual([row[0] for row in documented_rows], list(REQUIREMENT_IDS))
        for requirement_id, requirement, evidence in documented_rows:
            self.assertEqual(requirement, REQUIREMENT_EVIDENCE[requirement_id]["requirement"])
            self.assertEqual(evidence, REQUIREMENT_EVIDENCE[requirement_id]["evidence"])

    def test_ci_status_document_matches_runtime_contract_workflow_gate(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        documentation = (ROOT / "docs" / "current-cicd-status.md").read_text(encoding="utf-8")
        self.assertIn("- name: Validate runtime privilege contract", workflow)
        self.assertIn("run: python scripts/validate_runtime_privilege_contract.py", workflow)
        self.assertIn("Runtime privilege-contract static validation", documentation)
        self.assertIn("Disposable PostgreSQL 17 runtime privilege-contract tests exercise the sixteen canonical query keys", documentation)
        self.assertIn("creator-admin control edge with ADMIN true, INHERIT false, and SET false", documentation)
        self.assertIn("Boundary A remains repository-only", documentation)
        self.assertIn("Green CI does not authorise Boundary B or #160", documentation)
        self.assertNotIn("green CI authorises Boundary B", documentation.lower())

    def test_membership_query_narrative_has_exact_six_field_unfiltered_contract(self) -> None:
        documentation = (ROOT / "docs" / "runtime-privilege-contract.md").read_text(encoding="utf-8")
        section_match = re.search(
            r"### Membership-query narrative contract\s+(.*?)(?=\n### |\n## |\Z)",
            documentation,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(section_match, "membership-query narrative section is missing")
        paragraph = " ".join(section_match.group(1).split())
        required_phrases = (
            "exact aliases `role`, `member`, `grantor`, `admin_option`, `inherit_option`, and `set_option`",
            "complete unfiltered membership result",
            "validates the `grantor`",
            "distinguishes ADMIN authority from INHERIT and SET authority",
            "No column may be omitted",
            "no value may be supplied by a substituted default",
            "no unexpected row may be filtered away",
        )
        for phrase in required_phrases:
            self.assertIn(phrase, paragraph)

        mutations = {
            "grantor_omitted": paragraph.replace("`grantor`, ", "", 1),
            "inherit_omitted": paragraph.replace("`inherit_option`, ", "", 1),
            "set_omitted": paragraph.replace(", and `set_option`", "", 1),
            "only_three_fields": re.sub(
                r"exact aliases `role`.*?`set_option`",
                "exact aliases `role`, `member`, and `admin_option`",
                paragraph,
                count=1,
            ),
            "incorrect_alias": paragraph.replace("`grantor`", "`grantor_name`", 1),
            "filtering_permitted": paragraph.replace(
                "no unexpected row may be filtered away",
                "unexpected rows may be filtered away",
                1,
            ),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                self.assertTrue(
                    any(phrase not in mutation for phrase in required_phrases),
                    f"narrative mutation {label} was not detected",
                )

        expected_view_projection = ", ".join(CANONICAL_QUERY_COLUMNS["view_acl"])
        self.assertIn(
            f"The view query must project exactly `{expected_view_projection}`",
            " ".join(documentation.split()),
        )


    def test_h55_hierarchy_identity_is_closed_world(self) -> None:
        manifest = load_manifest()
        table_rows, column_rows = _complete_classified_authority_evidence(manifest)
        self.assertEqual(
            contract_validator.evaluate_public_table_like_authority(
                manifest, table_rows, column_rows
            ),
            (),
        )
        for field, value, fragment in (
            ("has_inheritance_parents", True, "inheritance_parents_forbidden"),
            ("is_partition", True, "partition_forbidden"),
            ("partition_bound", "FOR VALUES FROM (MINVALUE) TO (MAXVALUE)", "partition_bound_forbidden"),
        ):
            candidate = copy.deepcopy(table_rows)
            for row in candidate:
                if row["table_name"] == "sqag_profiles":
                    row[field] = value
            errors = contract_validator.evaluate_public_table_like_authority(
                manifest, candidate, copy.deepcopy(column_rows)
            )
            self.assertTrue(
                any(f"public.sqag_profiles_{fragment}" in error for error in errors),
                errors,
            )
        unrelated = copy.deepcopy(table_rows)
        unrelated.extend(
            {
                "schema_name": "public",
                "table_name": "h55_unrelated_parent",
                "relation_kind": "p",
                "relation_persistence": "p",
                "acl_entries": [],
                "owner": "sqag_app",
                "owner_select": False,
                "visible_column_count": 0,
                "column_contract": [],
                "row_security_enabled": False,
                "row_security_forced": False,
                "has_inheritance_descendants": True,
                "has_inheritance_parents": False,
                "is_partition": False,
                "partition_bound": None,
                "privilege_type": privilege,
                "effective": False,
                "is_grantable": False,
            }
            for privilege in TABLE_PRIVILEGES
        )
        self.assertEqual(
            contract_validator.evaluate_public_table_like_authority(
                manifest, unrelated, copy.deepcopy(column_rows)
            ),
            (),
        )
        self.assertEqual(
            contract_validator.evaluate_public_table_like_authority(
                manifest, table_rows, column_rows
            ),
            (),
        )


@unittest.skipUnless(postgres_test_conninfo(), "isolated PostgreSQL test service is not configured")
class PostgreSQLContractIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import psycopg
        from psycopg.rows import dict_row

        cls.psycopg = psycopg
        cls.dict_row = staticmethod(dict_row)
        cls.contract = load_manifest()
        cls._db_counter = 0
        cls._seen_roles: set[str] = set()
        cls._seen_databases: set[str] = set()

    @classmethod
    def tearDownClass(cls) -> None:
        errors: list[str] = []
        try:
            with cls.psycopg.connect(postgres_test_conninfo(), row_factory=cls.dict_row, autocommit=True) as connection:
                leftover_databases = [
                    str(_row_dict(row, "datname"))
                    for row in connection.execute(
                        "select datname from pg_catalog.pg_database where datname like %s order by datname",
                        ("sqag_rpc_db_%",),
                    ).fetchall()
                ]
                leftover_roles = [
                    str(_row_dict(row, "rolname"))
                    for row in connection.execute(
                        "select rolname from pg_catalog.pg_roles "
                        "where rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner') or rolname like %s "
                        "order by rolname",
                        ("sqag_rpc_role_%",),
                    ).fetchall()
                ]
                leftover_memberships = connection.execute(
                    "select parent.rolname as role_name, member.rolname as member_name "
                    "from pg_catalog.pg_auth_members am "
                    "join pg_catalog.pg_roles parent on parent.oid = am.roleid "
                    "join pg_catalog.pg_roles member on member.oid = am.member "
                    "where parent.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner') "
                    "or member.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner') "
                    "or parent.rolname like %s or member.rolname like %s",
                    ("sqag_rpc_role_%", "sqag_rpc_role_%"),
                ).fetchall()
                if leftover_databases:
                    errors.append(f"leftover_test_databases:{leftover_databases}")
                if leftover_roles:
                    errors.append(f"leftover_test_roles:{leftover_roles}")
                if leftover_memberships:
                    errors.append(f"leftover_test_memberships:{leftover_memberships}")
                for database_name in leftover_databases:
                    try:
                        with cls.psycopg.connect(
                            postgres_test_conninfo(database_name), row_factory=cls.dict_row, autocommit=True
                        ) as database_connection:
                            rows = database_connection.execute(
                                "select owner_role.rolname as owner_name, grantee_role.rolname as grantee_name "
                                "from pg_catalog.pg_default_acl d "
                                "join pg_catalog.pg_roles owner_role on owner_role.oid = d.defaclrole "
                                "cross join lateral pg_catalog.aclexplode(d.defaclacl) expanded "
                                "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = expanded.grantee "
                                "where owner_role.rolname in ('sqag_migrator', 'neondb_owner') "
                                "or owner_role.rolname = 'sqag_runtime' "
                                "or owner_role.rolname like %s "
                                "or grantee_role.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner') "
                                "or grantee_role.rolname like %s",
                                ("sqag_rpc_role_%", "sqag_rpc_role_%"),
                            ).fetchall()
                            if rows:
                                errors.append(f"leftover_test_default_acl:{database_name}:{rows}")
                    except Exception as exc:
                        errors.append(f"default_acl_audit_failed:{database_name}:{exc}")
        except Exception as exc:
            errors.append(f"post_suite_audit_failed:{exc}")
        finally:
            super().tearDownClass()
        if errors:
            raise AssertionError("; ".join(errors))

    def setUp(self) -> None:
        self.database_name = self._create_database()
        self.contract = copy.deepcopy(type(self).contract)
        self._local_bootstrap_role = self._current_session_user()
        self.contract["roles"]["runtime"]["provider_control_edges"][0]["grantor"] = (
            self._local_bootstrap_role
        )
        self._membership_baseline = self._membership_snapshot()
        self._public_database_baseline = {
            privilege: self._has_database_privilege("public", privilege)
            for privilege in ("CONNECT", "CREATE", "TEMPORARY")
        }
        self._public_function_baselines: dict[str, bool] = {}
        self._public_function_restoration_receipts: dict[str, bool] = {}
        self.addCleanup(self._audit_and_drop_database)
        self.addCleanup(self._assert_membership_baseline_restored)
        self._create_role("sqag_migrator")
        self._create_role("sqag_app", login=True)
        provider_name = self._create_role("neondb_owner")
        self._execute_admin_sql(f"alter role {_quote_identifier(provider_name)} createrole")
        self._create_role("sqag_runtime", creator_role=provider_name)
        self._grant_database_privilege("sqag_migrator", "CONNECT")
        self._grant_database_privilege("sqag_migrator", "CREATE")
        self._grant_database_privilege("sqag_migrator", "TEMPORARY")
        self._grant_schema_privilege("sqag_migrator", "CREATE")

    def _create_database(self) -> str:
        type(self)._db_counter += 1
        database_name = f"sqag_rpc_db_{type(self)._db_counter}_{uuid.uuid4().hex[:8]}"
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            connection.execute(f"create database {_quote_identifier(database_name)}")
        type(self)._seen_databases.add(database_name)
        return database_name

    def connect(self, database_name: str | None = None) -> PostgresConnectionAdapter:
        raw = self.psycopg.connect(
            postgres_test_conninfo(database_name or self.database_name),
            row_factory=self.dict_row,
        )
        return PostgresConnectionAdapter(raw)

    def _current_session_user(self) -> str:
        connection = self.connect()
        try:
            row = connection.execute("select session_user as session_user").fetchone()
            value = _row_dict(row, "session_user")
            self.assertIsInstance(value, str)
            return str(value)
        finally:
            connection.rollback()
            connection.close()

    @staticmethod
    def _membership_tuples(
        rows: list[dict[str, Any]],
    ) -> tuple[tuple[str, str, str, bool, bool, bool], ...]:
        return tuple(
            sorted(
                (
                    str(row["role"]),
                    str(row["member"]),
                    str(row["grantor"]),
                    bool(row["admin_option"]),
                    bool(row["inherit_option"]),
                    bool(row["set_option"]),
                )
                for row in rows
            )
        )

    def _membership_snapshot(self) -> tuple[tuple[str, str, str, bool, bool, bool], ...]:
        columns, rows = self._execute_contract_query("role_memberships")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["role_memberships"])
        return self._membership_tuples(rows)

    @staticmethod
    def _runtime_like_memberships(
        memberships: tuple[tuple[str, str, str, bool, bool, bool], ...],
    ) -> tuple[tuple[str, str, str, bool, bool, bool], ...]:
        runtime_like_roles = {"sqag_runtime", "sqag_migrator"}
        return tuple(
            row
            for row in memberships
            if row[0] in runtime_like_roles or row[1] in runtime_like_roles
        )

    def _assert_membership_baseline_restored(self) -> None:
        self.assertEqual(self._membership_snapshot(), self._membership_baseline)

    def _expected_provider_edge(self) -> tuple[str, str, str, bool, bool, bool]:
        return (
            "sqag_runtime",
            "neondb_owner",
            self._local_bootstrap_role,
            True,
            False,
            False,
        )

    def _cleanup_steps(self, steps: list[tuple[str, str]], database_name: str | None = None) -> None:
        errors: list[str] = []
        for label, sql in steps:
            connection: PostgresConnectionAdapter | None = None
            try:
                connection = self.connect(database_name)
                connection.execute(sql)
                connection.commit()
            except Exception as exc:
                errors.append(f"{label}:{exc}")
                if connection is not None:
                    connection.rollback()
            finally:
                if connection is not None:
                    connection.close()
        if errors:
            raise AssertionError("cleanup failed: " + "; ".join(errors))

    def _create_role(
        self,
        role_name: str,
        *,
        creator_role: str | None = None,
        login: bool = False,
    ) -> str:
        _quote_identifier(role_name)
        if creator_role is not None:
            _quote_identifier(creator_role)
        connection = self.connect()
        try:
            if creator_role is not None:
                connection.execute(f"set session authorization {_quote_identifier(creator_role)}")
            login_clause = "LOGIN" if login else "NOLOGIN"
            connection.execute(
                f"create role {_quote_identifier(role_name)} {login_clause} NOSUPERUSER NOCREATEDB "
                f"NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT CONNECTION LIMIT -1"
            )
            connection.commit()
        finally:
            connection.close()
        type(self)._seen_roles.add(role_name)
        self.addCleanup(self._drop_role, role_name)
        return role_name

    def _new_role(self, purpose: str) -> str:
        role_name = f"sqag_rpc_role_{purpose}_{uuid.uuid4().hex[:8]}"
        return self._create_role(role_name)

    def _drop_role(self, role_name: str) -> None:
        ident = _quote_identifier(role_name)
        database_ident = _quote_identifier(self.database_name)
        self._revoke_role_memberships(role_name)
        self._cleanup_steps(
            [
                ("revoke_schema", f"revoke all privileges on schema public from {ident}"),
                ("revoke_tables", f"revoke all privileges on all tables in schema public from {ident}"),
                ("revoke_sequences", f"revoke all privileges on all sequences in schema public from {ident}"),
                ("revoke_functions", f"revoke all privileges on all functions in schema public from {ident}"),
                ("revoke_database", f"revoke all privileges on database {database_ident} from {ident}"),
                ("drop_owned", f"drop owned by {ident}"),
                ("drop_role", f"drop role {ident}"),
            ]
        )

    def _revoke_role_memberships(self, role_name: str) -> None:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select parent.rolname as parent_name, member.rolname as member_name "
                "from pg_catalog.pg_auth_members am "
                "join pg_catalog.pg_roles parent on parent.oid = am.roleid "
                "join pg_catalog.pg_roles member on member.oid = am.member "
                "where parent.rolname = %s or member.rolname = %s "
                "order by parent_name, member_name",
                (role_name, role_name),
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()
        steps = [
            (
                f"revoke_membership_{parent}_{member}",
                f"revoke {_quote_identifier(str(parent))} from {_quote_identifier(str(member))}",
            )
            for parent, member in (
                (_row_dict(row, "parent_name"), _row_dict(row, "member_name")) for row in rows
            )
        ]
        if steps:
            self._cleanup_steps(steps)

    def _audit_and_drop_database(self) -> None:
        errors: list[str] = []
        connection: PostgresConnectionAdapter | None = None
        try:
            self._audit_public_acl_baseline()
        except Exception as exc:
            errors.append(f"public_acl_baseline_audit_failed:{exc}")

        try:
            connection = self.connect()
            leftover_roles = connection.execute(
                "select rolname from pg_catalog.pg_roles "
                "where rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner') or rolname like %s "
                "order by rolname",
                ("sqag_rpc_role_%",),
            ).fetchall()
            if leftover_roles:
                errors.append(f"leftover_roles:{leftover_roles}")
            leftover_acl = connection.execute(
                "select owner_role.rolname as owner_name, grantee_role.rolname as grantee_name "
                "from pg_catalog.pg_default_acl d "
                "join pg_catalog.pg_roles owner_role on owner_role.oid = d.defaclrole "
                "cross join lateral pg_catalog.aclexplode(d.defaclacl) expanded "
                "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = expanded.grantee "
                "where owner_role.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner') "
                "or owner_role.rolname like %s "
                "or grantee_role.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_app', 'neondb_owner') "
                "or grantee_role.rolname like %s",
                ("sqag_rpc_role_%", "sqag_rpc_role_%"),
            ).fetchall()
            if leftover_acl:
                errors.append(f"leftover_default_acl:{leftover_acl}")
        except Exception as exc:
            errors.append(f"database_audit_failed:{exc}")
        finally:
            if connection is not None:
                connection.close()

        try:
            with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
                connection.execute(
                    "select pg_catalog.pg_terminate_backend(pid) from pg_catalog.pg_stat_activity "
                    "where datname = %s and pid <> pg_catalog.pg_backend_pid()",
                    (self.database_name,),
                )
                connection.execute(f"drop database { _quote_identifier(self.database_name) }")
            type(self)._seen_databases.discard(self.database_name)
        except Exception as exc:
            errors.append(f"drop_database_failed:{exc}")
        if errors:
            raise AssertionError("; ".join(errors))

    def _grant_database_privilege(self, role_name: str, privilege: str) -> None:
        if privilege not in {"CONNECT", "CREATE", "TEMPORARY"}:
            raise ValueError(privilege)
        connection = self.connect()
        try:
            connection.execute(
                f"grant {privilege} on database {_quote_identifier(self.database_name)} to {_quote_identifier(role_name)}"
            )
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(self._revoke_database_privilege, role_name, privilege)

    def _revoke_database_privilege(self, role_name: str, privilege: str) -> None:
        self._cleanup_steps(
            [
                (
                    f"revoke_database_{privilege}",
                    f"revoke {privilege} on database {_quote_identifier(self.database_name)} from {_quote_identifier(role_name)}",
                )
            ]
        )

    def _grant_schema_privilege(self, role_name: str, privilege: str) -> None:
        if privilege not in {"USAGE", "CREATE"}:
            raise ValueError(privilege)
        connection = self.connect()
        try:
            connection.execute(f"grant {privilege} on schema public to {_quote_identifier(role_name)}")
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(self._revoke_schema_privilege, role_name, privilege)

    def _revoke_schema_privilege(self, role_name: str, privilege: str) -> None:
        self._cleanup_steps(
            [
                (
                    f"revoke_schema_{privilege}",
                    f"revoke {privilege} on schema public from {_quote_identifier(role_name)}",
                )
            ]
        )

    def _grant_table_privilege(
        self,
        role_name: str,
        table_name: str,
        privilege: str,
        *,
        grantor_role: str | None = None,
    ) -> None:
        if table_name not in ALL_TABLES or privilege not in set(TABLE_PRIVILEGES):
            raise ValueError(f"invalid table grant: {table_name}:{privilege}")
        connection = self.connect()
        try:
            if grantor_role is not None:
                connection.execute(f"set role {_quote_identifier(grantor_role)}")
            connection.execute(
                f"grant {privilege} on table {_quote_identifier(table_name)} to {_quote_identifier(role_name)}"
            )
            if grantor_role is not None:
                connection.execute("reset role")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self.addCleanup(self._revoke_table_privilege, role_name, table_name, privilege)

    def _grant_column_privilege(
        self,
        role_name: str,
        table_name: str,
        column_name: str,
        privilege: str,
        *,
        grantee: str | None = None,
        with_grant_option: bool = False,
        grantor_role: str | None = None,
    ) -> None:
        if table_name not in ALL_TABLES or privilege not in set(COLUMN_PRIVILEGES):
            raise ValueError(f"invalid column grant: {table_name}:{column_name}:{privilege}")
        target = grantee or role_name
        target_sql = "public" if target == "PUBLIC" else _quote_identifier(target)
        option = " with grant option" if with_grant_option else ""
        connection = self.connect()
        try:
            if grantor_role is not None:
                connection.execute(f"set role {_quote_identifier(grantor_role)}")
            connection.execute(
                f"grant {privilege} ({_quote_identifier(column_name)}) on table "
                f"{_quote_identifier(table_name)} to {target_sql}{option}"
            )
            if grantor_role is not None:
                connection.execute("reset role")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        self.addCleanup(
            self._revoke_column_privilege,
            table_name,
            column_name,
            privilege,
            target,
        )

    def _revoke_column_privilege(
        self, table_name: str, column_name: str, privilege: str, grantee: str
    ) -> None:
        target_sql = "public" if grantee == "PUBLIC" else _quote_identifier(grantee)
        self._cleanup_steps(
            [
                (
                    f"revoke_column_{table_name}_{column_name}_{privilege}_{grantee}",
                    f"revoke {privilege} ({_quote_identifier(column_name)}) on table "
                    f"{_quote_identifier(table_name)} from {target_sql}",
                )
            ]
        )

    def _grant_role_membership(
        self, parent_role: str, member_role: str, *, admin_option: bool = False
    ) -> None:
        option = " with admin option" if admin_option else ""
        self.addCleanup(
            self._cleanup_steps,
            [
                (
                    f"revoke_membership_{parent_role}_{member_role}",
                    f"revoke {_quote_identifier(parent_role)} from {_quote_identifier(member_role)}",
                )
            ],
        )
        self._execute_admin_sql(
            f"grant {_quote_identifier(parent_role)} to {_quote_identifier(member_role)}{option}"
        )

    def _revoke_table_privilege(self, role_name: str, table_name: str, privilege: str) -> None:
        self._cleanup_steps(
            [
                (
                    f"revoke_table_{table_name}_{privilege}",
                    f"revoke {privilege} on table {_quote_identifier(table_name)} from {_quote_identifier(role_name)}",
                )
            ]
        )

    def _has_database_privilege(self, grantee: str, privilege: str) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                "select has_database_privilege(%s, %s, %s) as allowed",
                (grantee, self.database_name, privilege),
            ).fetchone()
            return bool(_row_dict(row, "allowed"))
        finally:
            connection.rollback()
            connection.close()

    def _has_schema_privilege(self, grantee: str, privilege: str) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                "select has_schema_privilege(%s, 'public', %s) as allowed",
                (grantee, privilege),
            ).fetchone()
            return bool(_row_dict(row, "allowed"))
        finally:
            connection.rollback()
            connection.close()

    def _has_table_privilege(self, grantee: str, table_name: str, privilege: str) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                "select has_table_privilege(%s, %s, %s) as allowed",
                (grantee, f"public.{table_name}", privilege),
            ).fetchone()
            return bool(_row_dict(row, "allowed"))
        finally:
            connection.rollback()
            connection.close()

    def _has_column_privilege(
        self, grantee: str, table_name: str, column_name: str, privilege: str
    ) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                "select has_column_privilege(%s, %s, %s, %s) as allowed",
                (grantee, f"public.{table_name}", column_name, privilege),
            ).fetchone()
            return bool(_row_dict(row, "allowed"))
        finally:
            connection.rollback()
            connection.close()

    def _public_database_acl_snapshot(self) -> dict[str, bool]:
        return {
            privilege: self._has_database_privilege("public", privilege)
            for privilege in ("CONNECT", "CREATE", "TEMPORARY")
        }

    def _function_exists(self, function_name: str) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                "select exists (select 1 from pg_catalog.pg_proc p "
                "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
                "where n.nspname = 'public' and p.proname = %s "
                "and pg_get_function_identity_arguments(p.oid) = '') as present",
                (function_name,),
            ).fetchone()
            return bool(_row_dict(row, "present"))
        finally:
            connection.rollback()
            connection.close()

    def _public_function_acl_snapshot(self, function_names: set[str] | None = None) -> dict[str, bool]:
        names = function_names or set(self._public_function_baselines)
        return {
            name: self._public_function_execute(name)
            for name in sorted(names)
            if self._function_exists(name)
        }

    def _assert_public_acl_baseline_values(
        self,
        expected_database: dict[str, bool],
        actual_database: dict[str, bool],
        expected_functions: dict[str, bool],
        actual_functions: dict[str, bool],
    ) -> None:
        self.assertEqual(actual_database, expected_database, "PUBLIC database ACL baseline drifted")
        self.assertEqual(actual_functions, expected_functions, "PUBLIC routine EXECUTE baseline drifted")

    def _audit_public_acl_baseline(self) -> None:
        expected_functions: dict[str, bool] = {}
        for name, expected in self._public_function_baselines.items():
            if self._function_exists(name):
                expected_functions[name] = expected
            elif not self._public_function_restoration_receipts.get(name, False):
                raise AssertionError(f"PUBLIC routine baseline disappeared without restoration receipt: {name}")
        actual_functions = self._public_function_acl_snapshot(set(expected_functions))
        self._assert_public_acl_baseline_values(
            self._public_database_baseline,
            self._public_database_acl_snapshot(),
            expected_functions,
            actual_functions,
        )

    def _alter_public_database_privilege(self, privilege: str, grant: bool) -> None:
        before = self._public_database_baseline[privilege]
        connection = self.connect()
        try:
            verb = "grant" if grant else "revoke"
            connection.execute(f"{verb} {privilege} on database {_quote_identifier(self.database_name)} from public" if not grant else f"{verb} {privilege} on database {_quote_identifier(self.database_name)} to public")
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(self._restore_public_database_privilege, privilege, before)

    def _restore_public_database_privilege(self, privilege: str, expected: bool) -> None:
        connection = self.connect()
        try:
            verb = "grant" if expected else "revoke"
            target = "to public" if expected else "from public"
            connection.execute(f"{verb} {privilege} on database {_quote_identifier(self.database_name)} {target}")
            connection.commit()
        finally:
            connection.close()

    def _public_function_execute(self, function_name: str) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                "select has_function_privilege('public', %s, 'EXECUTE') as allowed",
                (f"public.{function_name}()",),
            ).fetchone()
            return bool(_row_dict(row, "allowed"))
        finally:
            connection.rollback()
            connection.close()

    def _has_function_privilege(self, grantee: str, function_name: str) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                "select has_function_privilege(%s, %s, 'EXECUTE') as allowed",
                (grantee, f"public.{function_name}()"),
            ).fetchone()
            return bool(_row_dict(row, "allowed"))
        finally:
            connection.rollback()
            connection.close()

    def _call_function_as_role(self, role_name: str, function_name: str) -> object:
        with self.as_role(role_name) as connection:
            row = connection.execute(
                f"select public.{_quote_identifier(function_name)}() as result"
            ).fetchone()
            return _row_dict(row, "result")

    def _revoke_public_execute(self, function_name: str, *, register_cleanup: bool = True) -> None:
        before = self._public_function_execute(function_name)
        self._public_function_baselines.setdefault(function_name, before)
        connection = self.connect()
        try:
            connection.execute(f"revoke execute on function public.{_quote_identifier(function_name)}() from public")
            connection.commit()
        finally:
            connection.close()
        if register_cleanup:
            self.addCleanup(self._restore_public_execute, function_name, before)

    def _restore_public_execute(self, function_name: str, expected: bool) -> None:
        connection = self.connect()
        try:
            verb = "grant" if expected else "revoke"
            target = "to public" if expected else "from public"
            connection.execute(f"{verb} execute on function public.{_quote_identifier(function_name)}() {target}")
            connection.commit()
        finally:
            connection.close()
        if function_name in self._public_function_baselines:
            self._public_function_restoration_receipts[function_name] = True

    def _restore_and_drop_public_function(self, function_name: str, expected: bool) -> None:
        self._restore_public_execute(function_name, expected)
        self.assertEqual(self._public_function_execute(function_name), expected)
        self._public_function_restoration_receipts[function_name] = True
        self._cleanup_steps(
            [(f"drop_{function_name}", f"drop function public.{_quote_identifier(function_name)}()")]
        )

    def apply_migrations(self) -> None:
        migrations = migration_manifest(ROOT / "migrations")
        connection = self.connect()
        try:
            connection.execute(f"set role {_quote_identifier('sqag_migrator')}")
            apply_postgres_migrations(connection, migrations)
            connection.execute("reset role")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def as_role(self, role_name: str) -> Iterator[PostgresConnectionAdapter]:
        connection = self.connect()
        try:
            connection.execute(f"set role {_quote_identifier(role_name)}")
            yield connection
        finally:
            try:
                connection.rollback()
                connection.execute("reset role")
            finally:
                connection.rollback()
                connection.close()

    def _default_acl_snapshot(self, owner_name: str | None = None) -> set[tuple[Any, ...]]:
        connection = self.connect()
        try:
            query = DEFAULT_ACL_SNAPSHOT_SQL
            params: tuple[Any, ...] = ()
            if owner_name is not None:
                query = query.replace(
                    "where d.defaclobjtype in ('r', 'S', 'f', 'n', 'T')",
                    "where d.defaclobjtype in ('r', 'S', 'f', 'n', 'T') and owner_role.rolname = %s",
                )
                params = (owner_name,)
            rows = connection.execute(query, params).fetchall()
            return {
                (
                    _row_dict(row, "owner_name"),
                    _row_dict(row, "namespace"),
                    _row_dict(row, "object_type"),
                    _row_dict(row, "grantee"),
                    _row_dict(row, "privilege_type"),
                    bool(_row_dict(row, "is_grantable")),
                )
                for row in rows
            }
        finally:
            connection.rollback()
            connection.close()

    def _assert_expected_provider_default_tuples(
        self, snapshot: set[tuple[Any, ...]], expected: set[tuple[Any, ...]]
    ) -> None:
        missing = expected - snapshot
        self.assertEqual(missing, set(), f"provider default baseline missing intended tuples: {missing}")

    def _alter_default_privilege(
        self,
        owner_name: str,
        grantee: str,
        privilege: str,
        object_keyword: str,
        with_grant_option: bool = False,
    ) -> None:
        allowed_privileges = {
            "TABLES": {"SELECT", "INSERT", "UPDATE", "DELETE"},
            "SEQUENCES": {"SELECT", "USAGE", "UPDATE"},
            "FUNCTIONS": {"EXECUTE"},
            "SCHEMAS": {"USAGE", "CREATE"},
            "TYPES": {"USAGE"},
        }
        if object_keyword not in allowed_privileges or privilege not in allowed_privileges[object_keyword]:
            raise ValueError(f"invalid default privilege: {privilege}:{object_keyword}")
        target = "public" if grantee == "PUBLIC" else _quote_identifier(grantee)
        option = " with grant option" if with_grant_option else ""
        scope = "" if object_keyword == "SCHEMAS" else " in schema public"
        self.addCleanup(
            self._revoke_default_privilege,
            owner_name,
            grantee,
            privilege,
            object_keyword,
        )
        connection = self.connect()
        try:
            connection.execute(
                f"alter default privileges for role {_quote_identifier(owner_name)}{scope} "
                f"grant {privilege} on {object_keyword} to {target}{option}"
            )
            connection.commit()
        finally:
            connection.close()

    def _revoke_default_privilege(self, owner_name: str, grantee: str, privilege: str, object_keyword: str) -> None:
        target = "public" if grantee == "PUBLIC" else _quote_identifier(grantee)
        scope = "" if object_keyword == "SCHEMAS" else " in schema public"
        self._cleanup_steps(
            [
                (
                    f"revoke_default_{owner_name}_{grantee}_{privilege}_{object_keyword}",
                    f"alter default privileges for role {_quote_identifier(owner_name)}{scope} "
                    f"revoke {privilege} on {object_keyword} from {target}",
                )
            ]
        )

    def _register_default_acl_audit(self, role_names: set[str]) -> None:
        self.addCleanup(self._assert_no_default_acl_for_roles, tuple(sorted(role_names)))

    def _assert_no_default_acl_for_roles(self, role_names: tuple[str, ...]) -> None:
        rows = self._default_acl_snapshot()
        leftovers = [row for row in rows if row[0] in role_names or row[3] in role_names]
        self.assertEqual(leftovers, [], f"default ACL cleanup left rows: {leftovers}")

    def _execute_contract_query_on(
        self, connection, query_key: str
    ) -> tuple[list[str], list[dict[str, Any]]]:
        cursor = connection.execute(self.contract["verification_queries"][query_key])
        columns = [
            column.name if hasattr(column, "name") else column[0]
            for column in cursor.description
        ]
        return columns, [dict(row) for row in cursor.fetchall()]

    def _execute_contract_query(self, query_key: str) -> tuple[list[str], list[dict[str, Any]]]:
        connection = self.connect()
        try:
            return self._execute_contract_query_on(connection, query_key)
        finally:
            connection.rollback()
            connection.close()

    def _routine_inventory(self) -> list[dict[str, Any]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select p.oid, p.proname, pg_get_function_identity_arguments(p.oid) as identity_arguments, "
                "p.prokind, p.prosecdef, p.proacl, r.rolname as owner, "
                "has_function_privilege('public', p.oid, 'EXECUTE') as public_execute, "
                "exists (select 1 from pg_catalog.pg_trigger t where t.tgfoid = p.oid and not t.tgisinternal) "
                "as has_trigger_dependency "
                "from pg_catalog.pg_proc p "
                "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
                "join pg_catalog.pg_roles r on r.oid = p.proowner "
                "where n.nspname = 'public' and p.prokind in ('f', 'p', 'a', 'w') "
                "order by p.proname, identity_arguments"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            connection.rollback()
            connection.close()

    def _assert_routine_inventory(self, provider_name: str | None = None, runtime_name: str | None = None) -> list[dict[str, Any]]:
        rows = self._routine_inventory()
        names = {str(row["proname"]) for row in rows}
        expected_names = set(EXPECTED_ROUTINES)
        if provider_name is None:
            self.assertEqual(names, expected_names)
            self.assertNotIn("show_db_tree", names, "stock PostgreSQL must not provide show_db_tree")
        else:
            self.assertEqual(names, expected_names | {"show_db_tree"})
        for routine_name in EXPECTED_ROUTINES:
            matches = [row for row in rows if row["proname"] == routine_name]
            self.assertEqual(len(matches), 1, routine_name)
            row = matches[0]
            self.assertEqual(row["owner"], "sqag_migrator")
            self.assertEqual(row["prokind"], "f")
            self.assertIs(row["prosecdef"], False)
            self.assertIs(row["has_trigger_dependency"], True)
            self.assertIs(row["public_execute"], True)
            if runtime_name is not None:
                connection = self.connect()
                try:
                    direct_row = connection.execute(
                        "select exists (select 1 from pg_catalog.aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) expanded "
                        "where p.oid = %s and expanded.grantee = (select oid from pg_catalog.pg_roles where rolname = %s)) "
                        "as direct_runtime_grant from pg_catalog.pg_proc p where p.oid = %s",
                        (row["oid"], runtime_name, row["oid"]),
                    ).fetchone()
                    self.assertIs(_row_dict(direct_row, "direct_runtime_grant"), False)
                finally:
                    connection.rollback()
                    connection.close()
        if provider_name is not None:
            matches = [row for row in rows if row["proname"] == "show_db_tree"]
            self.assertEqual(len(matches), 1)
            provider_row = matches[0]
            self.assertEqual(provider_row["owner"], "neondb_owner")
            self.assertIs(provider_row["prosecdef"], False)
            self.assertIs(provider_row["has_trigger_dependency"], False)
            if runtime_name is not None:
                connection = self.connect()
                try:
                    row = connection.execute(
                        "select exists (select 1 from pg_catalog.aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) expanded "
                        "where p.oid = %s and expanded.grantee = (select oid from pg_catalog.pg_roles where rolname = %s)) "
                        "as direct_runtime_grant from pg_catalog.pg_proc p where p.oid = %s",
                        (provider_row["oid"], runtime_name, provider_row["oid"]),
                    ).fetchone()
                    self.assertIs(_row_dict(row, "direct_runtime_grant"), False)
                finally:
                    connection.rollback()
                    connection.close()
        return rows

    def _trigger_dependencies(self) -> set[tuple[str, str, str]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select t.tgname as trigger_name, c.relname as table_name, p.proname as routine_name "
                "from pg_catalog.pg_trigger t "
                "join pg_catalog.pg_class c on c.oid = t.tgrelid "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "join pg_catalog.pg_proc p on p.oid = t.tgfoid "
                "where n.nspname = 'public' and not t.tgisinternal "
                "order by trigger_name"
            ).fetchall()
            return {
                (str(row["trigger_name"]), str(row["table_name"]), str(row["routine_name"]))
                for row in rows
            }
        finally:
            connection.rollback()
            connection.close()

    def _effective_table_grants(self, role_name: str) -> set[tuple[str, str, str, bool]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select n.nspname as schema_name, c.relname as table_name, p.privilege_type, "
                "has_table_privilege(%s, c.oid, p.privilege_type) as effective, "
                "has_table_privilege(%s, c.oid, p.privilege_type || ' WITH GRANT OPTION') as is_grantable "
                "from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE'), ('TRUNCATE'), "
                "('REFERENCES'), ('TRIGGER'), ('MAINTAIN')) p(privilege_type) "
                "where n.nspname = 'public' and c.relkind in ('r', 'p', 'f') "
                "order by n.nspname, c.relname, p.privilege_type",
                (role_name, role_name),
            ).fetchall()
            return {
                (
                    str(row["schema_name"]),
                    str(row["table_name"]),
                    str(row["privilege_type"]),
                    bool(row["is_grantable"]),
                )
                for row in rows
                if bool(row["effective"])
            }
        finally:
            connection.rollback()
            connection.close()

    def _user_columns(self) -> dict[str, tuple[str, ...]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select c.relname as table_name, a.attname as column_name "
                "from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "join pg_catalog.pg_attribute a on a.attrelid = c.oid "
                "where n.nspname = 'public' and c.relkind in ('r', 'p', 'f') "
                "and a.attnum > 0 and not a.attisdropped "
                "order by c.relname, a.attnum"
            ).fetchall()
            columns: dict[str, list[str]] = {}
            for row in rows:
                columns.setdefault(str(row["table_name"]), []).append(str(row["column_name"]))
            return {table_name: tuple(names) for table_name, names in columns.items()}
        finally:
            connection.rollback()
            connection.close()

    def _effective_column_grants(
        self, role_name: str
    ) -> set[tuple[str, str, str, str, bool]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select n.nspname as schema_name, c.relname as table_name, a.attname as column_name, "
                "p.privilege_type, has_column_privilege(%s, c.oid, a.attname, p.privilege_type) as effective, "
                "has_column_privilege(%s, c.oid, a.attname, p.privilege_type || ' WITH GRANT OPTION') as is_grantable "
                "from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "join pg_catalog.pg_attribute a on a.attrelid = c.oid "
                "cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('REFERENCES')) p(privilege_type) "
                "where n.nspname = 'public' and c.relkind in ('r', 'p', 'f') "
                "and a.attnum > 0 and not a.attisdropped "
                "order by n.nspname, c.relname, a.attname, p.privilege_type",
                (role_name, role_name),
            ).fetchall()
            return {
                (
                    str(row["schema_name"]),
                    str(row["table_name"]),
                    str(row["column_name"]),
                    str(row["privilege_type"]),
                    bool(row["is_grantable"]),
                )
                for row in rows
                if bool(row["effective"])
            }
        finally:
            connection.rollback()
            connection.close()

    def _expected_runtime_column_grants(self) -> set[tuple[str, str, str, str, bool]]:
        columns = self._user_columns()
        expected: set[tuple[str, str, str, str, bool]] = set()
        for table_name, entry in self.contract["tables"]["runtime_accessible"].items():
            for column_name in columns.get(table_name, ()):
                for privilege in COLUMN_PRIVILEGES:
                    if entry["privileges"].get(privilege.lower(), False):
                        expected.add((str(entry["schema"]), table_name, column_name, privilege, False))
        for table_name, grants in EXPLICIT_COLUMN_PRIVILEGES.items():
            schema = str(self.contract["tables"]["runtime_accessible"][table_name]["schema"])
            for privilege, column_names in grants.items():
                for column_name in column_names:
                    self.assertIn(column_name, columns.get(table_name, ()))
                    expected.add((schema, table_name, column_name, privilege, False))
        return expected

    def _assert_exact_runtime_column_matrix(self, role_name: str) -> None:
        actual = self._effective_column_grants(role_name)
        self.assertEqual(actual, self._expected_runtime_column_grants())
        self.assertFalse(any(row[4] for row in actual), f"column grant options found: {actual}")
        self.assertFalse({row[1] for row in actual} & FORBIDDEN_TABLES)

    def _effective_database_privileges(self, role_name: str) -> set[tuple[str, bool, bool]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select p.privilege_type, "
                "has_database_privilege(%s, current_database(), p.privilege_type) as effective, "
                "has_database_privilege(%s, current_database(), p.privilege_type || ' WITH GRANT OPTION') as is_grantable "
                "from (values ('CONNECT'), ('CREATE'), ('TEMPORARY')) p(privilege_type) "
                "order by p.privilege_type",
                (role_name, role_name),
            ).fetchall()
            return {
                (str(row["privilege_type"]), bool(row["effective"]), bool(row["is_grantable"]))
                for row in rows
            }
        finally:
            connection.rollback()
            connection.close()

    def _expected_runtime_database_privileges(self) -> set[tuple[str, bool, bool]]:
        return {("CONNECT", True, False), ("CREATE", False, False), ("TEMPORARY", False, False)}

    def _assert_exact_runtime_database_privileges(self, role_name: str) -> None:
        self.assertEqual(
            self._effective_database_privileges(role_name),
            self._expected_runtime_database_privileges(),
        )

    def _effective_schema_privileges(self, role_name: str) -> set[tuple[str, bool, bool]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select p.privilege_type, "
                "has_schema_privilege(%s, 'public', p.privilege_type) as effective, "
                "has_schema_privilege(%s, 'public', p.privilege_type || ' WITH GRANT OPTION') as is_grantable "
                "from (values ('USAGE'), ('CREATE')) p(privilege_type) "
                "order by p.privilege_type",
                (role_name, role_name),
            ).fetchall()
            return {
                (str(row["privilege_type"]), bool(row["effective"]), bool(row["is_grantable"]))
                for row in rows
            }
        finally:
            connection.rollback()
            connection.close()

    def _expected_runtime_schema_privileges(self) -> set[tuple[str, bool, bool]]:
        return {("USAGE", True, False), ("CREATE", False, False)}

    def _assert_exact_runtime_schema_privileges(self, role_name: str) -> None:
        self.assertEqual(
            self._effective_schema_privileges(role_name),
            self._expected_runtime_schema_privileges(),
        )

    def _expected_runtime_grants(self) -> set[tuple[str, str, str, bool]]:
        expected: set[tuple[str, str, str, bool]] = set()
        for table_name, entry in self.contract["tables"]["runtime_accessible"].items():
            for privilege, allowed in entry["privileges"].items():
                if allowed:
                    expected.add((str(entry["schema"]), table_name, privilege.upper(), False))
        return expected

    def _assert_exact_runtime_matrix(self, role_name: str) -> None:
        actual = self._effective_table_grants(role_name)
        self.assertEqual(actual, self._expected_runtime_grants())
        self.assertEqual({row[1] for row in actual}, set(self.contract["tables"]["runtime_accessible"]))
        self.assertFalse({row[1] for row in actual} & set(self.contract["tables"]["runtime_forbidden"]))
        self.assertFalse(any(row[3] for row in actual), f"grant options found: {actual}")

    def _execute_admin_sql(self, sql: str) -> None:
        connection = self.psycopg.connect(
            postgres_test_conninfo(self.database_name),
            row_factory=self.dict_row,
            options="-c default_transaction_read_only=off",
        )
        try:
            connection.execute(sql)
            connection.commit()
        finally:
            connection.close()

    def _new_exact_matrix_role(self, purpose: str) -> str:
        self.apply_migrations()
        role_name = self._new_role(purpose)
        self._grant_database_privilege(role_name, "CONNECT")
        self._grant_schema_privilege(role_name, "USAGE")
        for table_name, entry in self.contract["tables"]["runtime_accessible"].items():
            for privilege, allowed in entry["privileges"].items():
                if allowed:
                    self._grant_table_privilege(role_name, table_name, privilege.upper())
        for table_name, grants in EXPLICIT_COLUMN_PRIVILEGES.items():
            for privilege, column_names in grants.items():
                for column_name in column_names:
                    self._grant_column_privilege(role_name, table_name, column_name, privilege)
        self._assert_exact_runtime_matrix(role_name)
        return role_name

    def _prepare_fixed_runtime_contract_fixture(self) -> tuple[str, str]:
        self.apply_migrations()
        self._grant_database_privilege("sqag_runtime", "CONNECT")
        self._grant_schema_privilege("sqag_runtime", "USAGE")
        for table_name, entry in self.contract["tables"]["runtime_accessible"].items():
            for privilege, allowed in entry["privileges"].items():
                if allowed:
                    self._grant_table_privilege("sqag_runtime", table_name, privilege.upper(), grantor_role="sqag_migrator")
        for table_name, grants in EXPLICIT_COLUMN_PRIVILEGES.items():
            for privilege, column_names in grants.items():
                for column_name in column_names:
                    self._grant_column_privilege("sqag_runtime", table_name, column_name, privilege, grantor_role="sqag_migrator")
        self._alter_public_database_privilege("TEMPORARY", False)
        self._revoke_public_execute("sqag_reject_immutable_change")
        self._revoke_public_execute("sqag_require_retention_delete_authorization")
        owner_name = self._new_role("query_owner")
        grantee_name = self._new_role("query_grantee")
        self._alter_default_privilege(owner_name, grantee_name, "SELECT", "TABLES")
        return owner_name, grantee_name

    def _assert_isolated_matrix_mismatch(
        self,
        role_name: str,
        expected_symmetric_diff: set[tuple[str, str, str, bool]],
    ) -> None:
        expected = self._expected_runtime_grants()
        actual = self._effective_table_grants(role_name)
        self.assertEqual(actual ^ expected, expected_symmetric_diff)
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_matrix(role_name)

    def _assert_isolated_column_mismatch(
        self,
        role_name: str,
        expected_symmetric_diff: set[tuple[str, str, str, str, bool]],
    ) -> None:
        expected = self._expected_runtime_column_grants()
        actual = self._effective_column_grants(role_name)
        self.assertEqual(actual ^ expected, expected_symmetric_diff)
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_column_matrix(role_name)

    def _session_user(self) -> str:
        connection = self.connect()
        try:
            row = connection.execute("select session_user as name").fetchone()
            return str(_row_dict(row, "name"))
        finally:
            connection.rollback()
            connection.close()

    def _current_user(self, role_name: str) -> str:
        with self.as_role(role_name) as connection:
            row = connection.execute("select current_user as name").fetchone()
            return str(_row_dict(row, "name"))

    def _role_attribute_row(self, role_name: str) -> dict[str, Any]:
        connection = self.connect()
        try:
            row = connection.execute(
                "select r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb, "
                "r.rolcanlogin, r.rolreplication, r.rolbypassrls, r.rolconnlimit "
                "from pg_catalog.pg_roles r where r.rolname = %s",
                (role_name,),
            ).fetchone()
            return dict(row) if row is not None else {}
        finally:
            connection.rollback()
            connection.close()

    def _database_owner(self, database_name: str | None = None) -> str:
        connection = self.psycopg.connect(
            postgres_test_conninfo(database_name or self.database_name),
            row_factory=self.dict_row,
        )
        try:
            row = connection.execute(
                "select r.rolname as owner from pg_catalog.pg_database d "
                "join pg_catalog.pg_roles r on r.oid = d.datdba where d.datname = current_database()"
            ).fetchone()
            return str(_row_dict(row, "owner"))
        finally:
            connection.rollback()
            connection.close()

    def _schema_owner(self) -> str:
        connection = self.connect()
        try:
            row = connection.execute(
                "select r.rolname as owner from pg_catalog.pg_namespace n "
                "join pg_catalog.pg_roles r on r.oid = n.nspowner where n.nspname = 'public'"
            ).fetchone()
            return str(_row_dict(row, "owner"))
        finally:
            connection.rollback()
            connection.close()

    def _table_owner(self, table_name: str) -> str:
        connection = self.connect()
        try:
            row = connection.execute(
                "select r.rolname as owner from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "join pg_catalog.pg_roles r on r.oid = c.relowner "
                "where n.nspname = 'public' and c.relname = %s",
                (table_name,),
            ).fetchone()
            return str(_row_dict(row, "owner"))
        finally:
            connection.rollback()
            connection.close()

    def _is_role_member(self, member: str, parent: str) -> bool:
        connection = self.connect()
        try:
            row = connection.execute(
                "select exists (select 1 from pg_catalog.pg_auth_members am "
                "join pg_catalog.pg_roles m on m.oid = am.member "
                "join pg_catalog.pg_roles p on p.oid = am.roleid "
                "where m.rolname = %s and p.rolname = %s) as present",
                (member, parent),
            ).fetchone()
            return bool(_row_dict(row, "present"))
        finally:
            connection.rollback()
            connection.close()

    def _raw_database_acl_entry(
        self, grantee: str, privilege: str, database_name: str | None = None
    ) -> bool | None:
        connection = self.psycopg.connect(
            postgres_test_conninfo(database_name or self.database_name),
            row_factory=self.dict_row,
        )
        try:
            row = connection.execute(
                "select expanded.is_grantable from pg_catalog.pg_database d "
                "cross join lateral pg_catalog.aclexplode("
                "coalesce(d.datacl, pg_catalog.acldefault('d', d.datdba))) expanded "
                "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = expanded.grantee "
                "where d.datname = current_database() "
                "and case when expanded.grantee = 0 then 'PUBLIC' "
                "else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text) end = %s "
                "and expanded.privilege_type = %s",
                (grantee, privilege),
            ).fetchone()
            return bool(_row_dict(row, "is_grantable")) if row is not None else None
        finally:
            connection.rollback()
            connection.close()

    def _raw_schema_acl_entry(self, grantee: str, privilege: str) -> bool | None:
        connection = self.connect()
        try:
            row = connection.execute(
                "select expanded.is_grantable from pg_catalog.pg_namespace n "
                "cross join lateral pg_catalog.aclexplode("
                "coalesce(n.nspacl, pg_catalog.acldefault('n', n.nspowner))) expanded "
                "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = expanded.grantee "
                "where n.nspname = 'public' "
                "and case when expanded.grantee = 0 then 'PUBLIC' "
                "else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text) end = %s "
                "and expanded.privilege_type = %s",
                (grantee, privilege),
            ).fetchone()
            return bool(_row_dict(row, "is_grantable")) if row is not None else None
        finally:
            connection.rollback()
            connection.close()

    def _drop_extra_database(self, database_name: str) -> None:
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            connection.execute(
                "select pg_catalog.pg_terminate_backend(pid) from pg_catalog.pg_stat_activity "
                "where datname = %s and pid <> pg_catalog.pg_backend_pid()",
                (database_name,),
            )
            connection.execute(f"drop database if exists {_quote_identifier(database_name)}")
        type(self)._seen_databases.discard(database_name)

    def _grant_view_privilege(
        self,
        role_name: str,
        view_name: str,
        privilege: str,
        *,
        with_grant_option: bool = False,
    ) -> None:
        option = " with grant option" if with_grant_option else ""
        connection = self.connect()
        try:
            connection.execute(
                f"grant {privilege} on table {_quote_identifier(view_name)} "
                f"to {_quote_identifier(role_name)}{option}"
            )
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(self._revoke_view_privilege, role_name, view_name, privilege)

    def _revoke_view_privilege(self, role_name: str, view_name: str, privilege: str) -> None:
        self._cleanup_steps(
            [
                (
                    f"revoke_view_{view_name}_{privilege}",
                    f"revoke {privilege} on table {_quote_identifier(view_name)} from {_quote_identifier(role_name)}",
                )
            ]
        )

    def _grant_manifest_view_authority(self, role_name: str) -> None:
        for view_name, entry in self.contract["views"]["runtime_accessible"].items():
            for privilege, allowed in entry["privileges"].items():
                if allowed:
                    self._grant_view_privilege(role_name, view_name, privilege.upper())

    def _create_legacy_quote_artifacts_view(self) -> str:
        table_name = "legacy_quote_artifacts_source"
        self.addCleanup(
            self._cleanup_steps,
            [
                ("drop_run73_legacy_quote_view", "drop view if exists public.sqag_quote_artifacts"),
                ("drop_run73_legacy_quote_table", f"drop table if exists public.{_quote_identifier(table_name)}"),
            ],
        )
        connection = self.connect()
        try:
            connection.execute("set role \"sqag_migrator\"")
            connection.execute(
                f"create table public.{_quote_identifier(table_name)} ("
                "workspace_id text not null, session_id text not null, artifact_kind text not null, "
                "filename text not null, content_type text not null, size_bytes bigint not null, "
                "content_blob bytea not null, created_at text not null, updated_at text not null)"
            )
            connection.execute(
                "create view public.sqag_quote_artifacts as "
                f"select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, "
                f"content_blob, created_at, updated_at from public.{_quote_identifier(table_name)}"
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        return table_name

    def _public_view_names(self) -> set[str]:
        return self._public_relation_names(("v", "m"))

    def _public_relation_names(self, kinds: tuple[str, ...]) -> set[str]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select c.relname as relation_name from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relkind = any(%s) order by c.relname",
                (list(kinds),),
            ).fetchall()
            return {str(_row_dict(row, "relation_name")) for row in rows}
        finally:
            connection.rollback()
            connection.close()

    def _relation_kind(self, relation_name: str) -> str:
        connection = self.connect()
        try:
            row = connection.execute(
                "select c.relkind as kind from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relname = %s",
                (relation_name,),
            ).fetchone()
            return str(_row_dict(row, "kind")) if row is not None else ""
        finally:
            connection.rollback()
            connection.close()

    def _create_materialized_view(self, view_name: str) -> None:
        connection = self.connect()
        try:
            connection.execute("set role \"sqag_migrator\"")
            connection.execute(
                f"create materialized view public.{_quote_identifier(view_name)} as "
                "select 1 as marker"
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(
            self._cleanup_steps,
            [
                (
                    f"drop_mat_view_{view_name}",
                    f"drop materialized view if exists public.{_quote_identifier(view_name)}",
                )
            ],
        )

    def _evaluate_view_authority_rows(self) -> tuple[str, ...]:
        columns, rows = self._execute_contract_query("view_acl")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["view_acl"])
        return contract_validator.evaluate_view_authority(
            self.contract,
            [dict(row) for row in rows],
        )

    def _evaluate_runtime_authority_on(self, connection) -> tuple[str, ...]:
        evidence: dict[str, list[dict[str, Any]]] = {}
        for query_key in CANONICAL_QUERY_KEYS:
            columns, rows = self._execute_contract_query_on(connection, query_key)
            self.assertEqual(columns, CANONICAL_QUERY_COLUMNS[query_key])
            evidence[query_key] = rows
        return contract_validator.evaluate_final_runtime_authority(
            self.contract,
            evidence,
            enforce_production_identity=False,
        )
    def _evaluate_runtime_authority_rows(self) -> tuple[str, ...]:
        connection = self.connect()
        try:
            return self._evaluate_runtime_authority_on(connection)
        finally:
            connection.rollback()
            connection.close()

    def _view_owner(self, view_name: str) -> str:
        connection = self.connect()
        try:
            row = connection.execute(
                "select r.rolname as owner from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "join pg_catalog.pg_roles r on r.oid = c.relowner "
                "where n.nspname = 'public' and c.relkind in ('v', 'm') and c.relname = %s",
                (view_name,),
            ).fetchone()
            return str(_row_dict(row, "owner"))
        finally:
            connection.rollback()
            connection.close()

    def _exact_view_acl_entries(self, view_name: str) -> set[tuple[str, str, bool]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "select case when expanded.grantee = 0 then 'PUBLIC' "
                "else coalesce(grantee_role.rolname, 'OID:' || expanded.grantee::text) end as grantee, "
                "expanded.privilege_type, expanded.is_grantable "
                "from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "cross join lateral pg_catalog.aclexplode("
                "coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))) expanded "
                "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = expanded.grantee "
                "where n.nspname = 'public' and c.relkind in ('v', 'm') and c.relname = %s "
                "order by grantee, expanded.privilege_type",
                (view_name,),
            ).fetchall()
            return {
                (str(row["grantee"]), str(row["privilege_type"]), bool(row["is_grantable"]))
                for row in rows
            }
        finally:
            connection.rollback()
            connection.close()

    def _expected_exact_view_acl(self, view_owner: str, role_name: str) -> set[tuple[str, str, bool]]:
        expected: set[tuple[str, str, bool]] = set()
        for privilege in TABLE_PRIVILEGES:
            expected.add((view_owner, privilege, False))
        for view_name, entry in self.contract["views"]["runtime_accessible"].items():
            for privilege, allowed in entry["privileges"].items():
                if allowed:
                    expected.add((role_name, privilege.upper(), False))
        return expected

    def _assert_exact_runtime_view_grants(self, role_name: str) -> None:
        view_name = "sqag_quote_artifacts"
        self.assertEqual(
            self._public_view_names(),
            set(self.contract["views"]["runtime_accessible"]),
            "public view/materialized-view inventory must remain closed",
        )
        self.assertEqual(self._relation_kind(view_name), "v", "legacy relation must be an ordinary view")
        view_owner = self._view_owner(view_name)
        actual = self._exact_view_acl_entries(view_name)
        expected = self._expected_exact_view_acl(view_owner, role_name)
        self.assertEqual(actual, expected, f"exact view ACL provenance mismatch for {view_name}")
        self.assertFalse(any(entry[2] for entry in actual), f"view grant options found: {actual}")
        connection = self.connect()
        try:
            rows = connection.execute(
                "select c.relname as relation_name from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "cross join lateral pg_catalog.aclexplode(c.relacl) expanded "
                "where n.nspname = 'public' and c.relkind in ('v', 'm') "
                "and expanded.grantee = (select oid from pg_catalog.pg_roles where rolname = %s)",
                (role_name,),
            ).fetchall()
            granted_views = {str(_row_dict(row, "relation_name")) for row in rows}
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(granted_views, {view_name}, f"unrelated view authority detected: {granted_views}")

    def test_actual_table_inventory_equals_manifest(self) -> None:
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select tablename from pg_catalog.pg_tables where schemaname = 'public' order by tablename"
            ).fetchall()
            actual = {str(_row_dict(row, "tablename")) for row in rows}
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(actual, ALL_TABLES)

    def test_actual_sequence_inventory_is_zero(self) -> None:
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select c.relname from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relkind = 'S' order by c.relname"
            ).fetchall()
            actual = {str(_row_dict(row, "relname")) for row in rows}
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(actual, set())

    def test_canonical_query_result_shapes_and_non_empty_fixture_rows(self) -> None:
        owner_name, grantee_name = self._prepare_fixed_runtime_contract_fixture()
        expected_cardinality = {
            "database_acl": 1,
            "schema_acl": 1,
            "table_acl": 16,
            "routine_acl": 2,
            "default_acl": 1,
            "role_attributes": 4,
            "role_memberships": len(self._membership_baseline) + 1,
            "sequence_acl": 0,
            "effective_runtime_database_privileges": 3,
            "effective_runtime_table_privileges": len(ALL_TABLES) * len(TABLE_PRIVILEGES),
            "effective_runtime_column_privileges": sum(len(columns) for columns in self._user_columns().values()) * len(COLUMN_PRIVILEGES),
            "effective_runtime_schema_privileges": 2,
            "effective_runtime_routine_privileges": 2,
            "view_acl": 0,
        }
        executed: list[str] = []
        results: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        for query_key in CANONICAL_QUERY_KEYS:
            self.assertNotIn(query_key, executed)
            executed.append(query_key)
            results[query_key] = self._execute_contract_query(query_key)

        self.assertEqual(executed, list(CANONICAL_QUERY_KEYS))
        self.assertEqual(len(executed), 16)
        self.assertEqual(set(executed), set(self.contract["verification_queries"]))
        for query_key in CANONICAL_QUERY_KEYS:
            columns, rows = results[query_key]
            self.assertEqual(columns, CANONICAL_QUERY_COLUMNS[query_key], query_key)
            if query_key in {"effective_runtime_parameter_privileges", "system_relation_acl"}:
                self.assertGreater(len(rows), 0, query_key)
            else:
                self.assertEqual(len(rows), expected_cardinality[query_key], query_key)

        default_rows = results["default_acl"][1]
        self.assertIn(
            {
                "owner": owner_name,
                "namespace": "public",
                "object_type": "r",
                "grantee": grantee_name,
                "privilege_type": "SELECT",
                "is_grantable": False,
            },
            default_rows,
        )
        role_rows = results["role_attributes"][1]
        self.assertEqual(
            {str(row["rolname"]) for row in role_rows},
            {"neondb_owner", "sqag_app", "sqag_migrator", "sqag_runtime"},
        )
        self.assertTrue(all(isinstance(row["password_is_null"], bool) for row in role_rows))
        self.assertTrue(all(row["password_is_null"] for row in role_rows))
        self.assertTrue(all("rolpassword" not in row for row in role_rows))
        membership_rows = results["role_memberships"][1]
        membership_tuples = self._membership_tuples(membership_rows)
        expected_provider_edge = (
            "sqag_runtime",
            "neondb_owner",
            self._local_bootstrap_role,
            True,
            False,
            False,
        )
        self.assertEqual(
            membership_tuples,
            tuple(sorted((*self._membership_baseline, expected_provider_edge))),
        )
        self.assertEqual(self._runtime_like_memberships(membership_tuples), (expected_provider_edge,))
        database_rows = results["effective_runtime_database_privileges"][1]
        self.assertEqual(
            {
                (str(row["privilege_type"]), bool(row["effective"]), bool(row["is_grantable"]))
                for row in database_rows
            },
            self._expected_runtime_database_privileges(),
        )
        database_acl_rows = results["database_acl"][1]
        self.assertEqual(len(database_acl_rows), 1)
        database_acl_row = database_acl_rows[0]
        self.assertEqual(database_acl_row["database_name"], self.database_name)
        self.assertTrue(str(database_acl_row["database_owner"]))
        self.assertIsInstance(database_acl_row["acl_entries"], list)
        self.assertIn(
            {
                "grantee": "sqag_runtime",
                "grantor": database_acl_row["database_owner"],
                "privilege_type": "CONNECT",
                "is_grantable": False,
            },
            database_acl_row["acl_entries"],
        )
        self.assertFalse(
            any(
                entry["grantee"] in {"PUBLIC", "sqag_runtime"}
                and entry["privilege_type"] in {"CREATE", "TEMPORARY"}
                for entry in database_acl_row["acl_entries"]
            )
        )
        raw_table_rows = results["table_acl"][1]
        structural_errors: list[str] = []
        contract_validator._validate_table_structure_evidence(raw_table_rows, structural_errors)
        self.assertEqual(structural_errors, [])
        schema_rows = results["effective_runtime_schema_privileges"][1]
        self.assertEqual(
            {
                (str(row["privilege_type"]), bool(row["effective"]), bool(row["is_grantable"]))
                for row in schema_rows
                if str(row["schema_name"]) == "public"
            },
            self._expected_runtime_schema_privileges(),
        )
        schema_acl_columns, schema_acl_rows = results["schema_acl"]
        self.assertEqual(schema_acl_columns, CANONICAL_QUERY_COLUMNS["schema_acl"])
        self.assertEqual(len(schema_acl_rows), 1)
        schema_acl_row = schema_acl_rows[0]
        self.assertEqual(schema_acl_row["schema_name"], "public")
        self.assertEqual(schema_acl_row["schema_owner"], "pg_database_owner")
        database_owner = str(schema_acl_row["database_owner"])
        self.assertTrue(database_owner)
        self.assertNotEqual(database_owner, schema_acl_row["schema_owner"])
        self.assertIn(
            {
                "grantee": "PUBLIC",
                "grantor": "pg_database_owner",
                "privilege_type": "USAGE",
                "is_grantable": False,
            },
            schema_acl_row["acl_entries"],
        )
        self.assertEqual(
            [
                (entry["grantor"], entry["is_grantable"])
                for entry in schema_acl_row["acl_entries"]
                if entry["grantee"] == "sqag_runtime" and entry["privilege_type"] == "USAGE"
            ],
            [("pg_database_owner", False)],
        )
        self.assertFalse(
            any(
                entry["grantee"] == "sqag_runtime" and entry["privilege_type"] == "CREATE"
                for entry in schema_acl_row["acl_entries"]
            )
        )
        table_rows = results["effective_runtime_table_privileges"][1]
        self.assertEqual(
            {
                (str(row["schema_name"]), str(row["table_name"]), str(row["privilege_type"]), bool(row["is_grantable"]))
                for row in table_rows
                if bool(row["effective"])
            },
            self._expected_runtime_grants(),
        )
        observed_column_contracts = {
            str(row["table_name"]): row["column_contract"]
            for row in table_rows
            if str(row["schema_name"]) == "public"
            and str(row["privilege_type"]) == "SELECT"
        }
        self.assertEqual(
            observed_column_contracts,
            contract_validator.classified_table_column_contract(),
        )
        column_rows = results["effective_runtime_column_privileges"][1]
        self.assertEqual(
            {
                (
                    str(row["schema_name"]),
                    str(row["table_name"]),
                    str(row["column_name"]),
                    str(row["privilege_type"]),
                    bool(row["is_grantable"]),
                )
                for row in column_rows
                if bool(row["effective"])
            },
            self._expected_runtime_column_grants(),
        )
        routine_rows = results["effective_runtime_routine_privileges"][1]
        self.assertEqual({str(row["routine_name"]) for row in routine_rows}, set(EXPECTED_ROUTINES))
        self.assertEqual(
            {
                (
                    str(row["routine_name"]),
                    str(row["identity_arguments"]),
                    str(row["routine_kind"]),
                    bool(row["direct_runtime_execute"]),
                    bool(row["public_execute"]),
                    bool(row["effective"]),
                    bool(row["is_grantable"]),
                )
                for row in routine_rows
            },
            {
                (routine_name, "", "f", False, False, False, False)
                for routine_name in EXPECTED_ROUTINES
            },
        )
        routine_acl_columns, routine_acl_rows = results["routine_acl"]
        self.assertEqual(routine_acl_columns, CANONICAL_QUERY_COLUMNS["routine_acl"])
        routine_acl_by_name = {str(row["routine_name"]): row for row in routine_acl_rows}
        self.assertEqual(set(routine_acl_by_name), set(EXPECTED_ROUTINES))
        for routine_name in EXPECTED_ROUTINES:
            row = routine_acl_by_name[routine_name]
            self.assertEqual(
                (
                    row["schema_name"],
                    row["identity_arguments"],
                    row["routine_kind"],
                    row["owner"],
                    row["security_definer"],
                    row["language"],
                    row["routine_configuration"],
                    row["has_trigger_dependency"],
                ),
                ("public", "", "f", "sqag_migrator", False, "plpgsql", [], True),
            )
            self.assertFalse(
                any(
                    entry["grantee"] in {"PUBLIC", "sqag_runtime"}
                    and entry["privilege_type"] == "EXECUTE"
                    for entry in row["acl_entries"]
                )
            )
        structural = contract_validator.classified_table_structure_contract()
        for routine_name, expected_routine in structural["routines"].items():
            routine_row = routine_acl_by_name[routine_name[1]]
            self.assertEqual(routine_row["language"], expected_routine["language"])
            self.assertEqual(
                contract_validator._normalise_routine_definition(routine_row["routine_definition"]),
                contract_validator._normalise_routine_definition(expected_routine["routine_definition"]),
            )
            self.assertEqual(routine_row["routine_configuration"], expected_routine["routine_configuration"])
            expected_bindings = sorted(
                contract_validator._trigger_binding_signature(trigger)
                for trigger in structural["triggers"].values()
                if (
                    trigger["function_schema"],
                    trigger["function_name"],
                    trigger["function_identity_arguments"],
                    "f",
                ) == routine_name
            )
            observed_bindings = sorted(
                contract_validator._trigger_binding_signature(binding)
                for binding in routine_row["trigger_bindings"]
            )
            self.assertEqual(observed_bindings, expected_bindings)
        parameter_columns, parameter_rows = results["effective_runtime_parameter_privileges"]
        self.assertEqual(
            parameter_columns,
            CANONICAL_QUERY_COLUMNS["effective_runtime_parameter_privileges"],
        )
        session_replication_rows = [
            row for row in parameter_rows if row["parameter_name"] == "session_replication_role"
        ]
        self.assertEqual(len(session_replication_rows), 1)
        self.assertTrue(all(isinstance(row["startup_defaults"], list) for row in parameter_rows))
        self.assertEqual(session_replication_rows[0]["startup_defaults"], [])
        self.assertEqual(
            {
                (
                    bool(row["effective_set"]),
                    bool(row["effective_alter_system"]),
                    bool(row["set_grantable"]),
                    bool(row["alter_system_grantable"]),
                )
                for row in session_replication_rows
            },
            {(False, False, False, False)},
        )
        self.assertEqual(
            contract_validator.evaluate_parameter_authority(self.contract, [dict(row) for row in parameter_rows]),
            (),
        )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        table_acl = next(
            row['acl_entries']
            for row in table_rows
            if row['table_name'] == 'sqag_profiles' and row['privilege_type'] == 'SELECT'
        )
        self.assertIn(
            {
                'grantee': 'sqag_runtime',
                'grantor': 'sqag_migrator',
                'privilege_type': 'SELECT',
                'is_grantable': False,
            },
            table_acl,
        )
        column_acl = next(
            row['acl_entries']
            for row in column_rows
            if row['table_name'] == 'sqag_quote_publication_artifacts'
            and row['column_name'] == 'checksum_sha256'
        )
        self.assertIn(
            {
                'grantee': 'sqag_runtime',
                'grantor': 'sqag_migrator',
                'privilege_type': 'UPDATE',
                'is_grantable': False,
            },
            column_acl,
        )

        self._execute_admin_sql('alter table public."sqag_profiles" enable row level security')
        try:
            _, rls_rows = self._execute_contract_query('effective_runtime_table_privileges')
            self.assertTrue(
                all(row['row_security_enabled'] for row in rls_rows if row['table_name'] == 'sqag_profiles')
            )
            rls_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('public.sqag_profiles_row_security_enabled_forbidden' in error for error in rls_errors), rls_errors)
        finally:
            self._execute_admin_sql('alter table public."sqag_profiles" disable row level security')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql('alter table public."sqag_profiles" enable row level security')
        self._execute_admin_sql('alter table public."sqag_profiles" force row level security')
        try:
            _, forced_rows = self._execute_contract_query('effective_runtime_table_privileges')
            self.assertTrue(
                all(row['row_security_forced'] for row in forced_rows if row['table_name'] == 'sqag_profiles')
            )
            forced_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('public.sqag_profiles_row_security_forced_forbidden' in error for error in forced_errors), forced_errors)
        finally:
            self._execute_admin_sql('alter table public."sqag_profiles" no force row level security')
            self._execute_admin_sql('alter table public."sqag_profiles" disable row level security')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql('alter table public."sqag_profiles" set unlogged')
        try:
            _, unlogged_rows = self._execute_contract_query('effective_runtime_table_privileges')
            self.assertEqual(
                {row['relation_persistence'] for row in unlogged_rows if row['table_name'] == 'sqag_profiles'},
                {'u'},
            )
            unlogged_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('public.sqag_profiles_relation_persistence_invalid' in error for error in unlogged_errors), unlogged_errors)
        finally:
            self._execute_admin_sql('alter table public."sqag_profiles" set logged')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        wrong_owner = self._new_role('classified_table_wrong_owner')
        self._execute_admin_sql(
            f'alter table public."sqag_profiles" owner to {_quote_identifier(wrong_owner)}'
        )
        try:
            _, wrong_owner_rows = self._execute_contract_query('effective_runtime_table_privileges')
            self.assertEqual(
                {row['owner'] for row in wrong_owner_rows if row['table_name'] == 'sqag_profiles'},
                {wrong_owner},
            )
            wrong_owner_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('public.sqag_profiles_owner_invalid' in error for error in wrong_owner_errors), wrong_owner_errors)
        finally:
            self._execute_admin_sql('alter table public."sqag_profiles" owner to "sqag_migrator"')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        child_name = f'sqag_rpc_classified_child_{uuid.uuid4().hex[:8]}'
        with self.as_role('sqag_migrator') as connection:
            connection.execute(
                f'create table public.{_quote_identifier(child_name)} () inherits (public."sqag_profiles")'
            )
            connection.commit()
        try:
            _, inherited_rows = self._execute_contract_query('effective_runtime_table_privileges')
            self.assertTrue(
                all(row['has_inheritance_descendants'] for row in inherited_rows if row['table_name'] == 'sqag_profiles')
            )
            inherited_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('public.sqag_profiles_inheritance_descendants_forbidden' in error for error in inherited_errors), inherited_errors)
        finally:
            self._execute_admin_sql(f'drop table if exists public.{_quote_identifier(child_name)}')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql('revoke select on table public."sqag_profiles" from "sqag_runtime"')
        self._execute_admin_sql('grant select on table public."sqag_profiles" to public')
        try:
            self.assertTrue(self._has_table_privilege('sqag_runtime', 'sqag_profiles', 'SELECT'))
            public_table_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('public.sqag_profiles_acl_public_authority_forbidden' in error for error in public_table_errors), public_table_errors)
        finally:
            self._execute_admin_sql('revoke select on table public."sqag_profiles" from public')
            self._grant_table_privilege('sqag_runtime', 'sqag_profiles', 'SELECT', grantor_role='sqag_migrator')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql('revoke select on table public."sqag_profiles" from "sqag_runtime"')
        with self.as_role('sqag_migrator') as connection:
            connection.execute('grant select on table public."sqag_profiles" to "sqag_runtime" with grant option')
            connection.commit()
        try:
            self.assertTrue(self._has_table_privilege('sqag_runtime', 'sqag_profiles', 'SELECT'))
            grant_option_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('public.sqag_profiles_acl_runtime_grant_option_forbidden' in error for error in grant_option_errors), grant_option_errors)
        finally:
            self._execute_admin_sql('revoke select on table public."sqag_profiles" from "sqag_runtime"')
            self._grant_table_privilege('sqag_runtime', 'sqag_profiles', 'SELECT', grantor_role='sqag_migrator')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        table_grantor = self._new_role('table_grantor')
        table_direct_grant_may_be_missing = False
        table_upstream_grant_may_exist = False
        table_downstream_grant_may_exist = False
        table_cleanup_errors: list[str] = []

        def run_table_cleanup(label: str, operation) -> None:
            try:
                operation()
            except Exception as exc:
                table_cleanup_errors.append(f'{label}:{exc}')

        def revoke_table_downstream() -> None:
            with self.as_role(table_grantor) as connection:
                connection.execute('revoke select on table public."sqag_profiles" from "sqag_runtime"')
                connection.commit()

        def revoke_table_upstream() -> None:
            with self.as_role('sqag_migrator') as connection:
                connection.execute(
                    f'revoke select on table public."sqag_profiles" from {_quote_identifier(table_grantor)}'
                )
                connection.commit()

        try:
            table_direct_grant_may_be_missing = True
            self._execute_admin_sql('revoke select on table public."sqag_profiles" from "sqag_runtime"')
            table_upstream_grant_may_exist = True
            with self.as_role('sqag_migrator') as connection:
                connection.execute(
                    f'grant select on table public."sqag_profiles" to {_quote_identifier(table_grantor)} with grant option'
                )
                connection.commit()
            table_downstream_grant_may_exist = True
            with self.as_role(table_grantor) as connection:
                connection.execute('grant select on table public."sqag_profiles" to "sqag_runtime"')
                connection.commit()

            table_columns, table_rows = self._execute_contract_query('effective_runtime_table_privileges')
            self.assertEqual(table_columns, CANONICAL_QUERY_COLUMNS['effective_runtime_table_privileges'])
            table_row = next(
                row
                for row in table_rows
                if row['table_name'] == 'sqag_profiles' and row['privilege_type'] == 'SELECT'
            )
            self.assertTrue(self._has_table_privilege('sqag_runtime', 'sqag_profiles', 'SELECT'))
            self.assertTrue(bool(table_row['effective']))
            self.assertFalse(bool(table_row['is_grantable']))
            self.assertEqual(table_row['owner'], 'sqag_migrator')
            self.assertTrue(bool(table_row['owner_select']))
            self.assertFalse(self._has_table_privilege('sqag_runtime', 'sqag_profiles', 'SELECT WITH GRANT OPTION'))
            table_runtime_acl = [
                dict(entry)
                for entry in table_row['acl_entries']
                if entry['grantee'] == 'sqag_runtime' and entry['privilege_type'] == 'SELECT'
            ]
            self.assertEqual(
                table_runtime_acl,
                [
                    {
                        'grantee': 'sqag_runtime',
                        'grantor': table_grantor,
                        'privilege_type': 'SELECT',
                        'is_grantable': False,
                    }
                ],
            )
            wrong_grantor_errors = self._evaluate_runtime_authority_rows()
            expected_table_error = (
                f'public_table_classified_public.sqag_profiles_acl_runtime_grantor_invalid_SELECT_{table_grantor}'
            )
            self.assertIn(expected_table_error, wrong_grantor_errors)
        finally:
            table_original_failure = sys.exc_info()[1]
            if table_downstream_grant_may_exist:
                run_table_cleanup('revoke_table_downstream_as_intermediary', revoke_table_downstream)
            if table_upstream_grant_may_exist:
                run_table_cleanup('revoke_table_upstream_as_owner', revoke_table_upstream)
            if table_direct_grant_may_be_missing:
                run_table_cleanup(
                    'restore_table_direct_as_owner',
                    lambda: self._grant_table_privilege(
                        'sqag_runtime', 'sqag_profiles', 'SELECT', grantor_role='sqag_migrator'
                    ),
                )
            if table_cleanup_errors:
                message = 'table provenance cleanup failed: ' + '; '.join(table_cleanup_errors)
                if table_original_failure is None:
                    raise AssertionError(message)
                add_note = getattr(table_original_failure, 'add_note', None)
                if callable(add_note):
                    add_note(message)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        _, restored_table_rows = self._execute_contract_query('effective_runtime_table_privileges')
        restored_table_row = next(
            row
            for row in restored_table_rows
            if row['table_name'] == 'sqag_profiles' and row['privilege_type'] == 'SELECT'
        )
        self.assertEqual(
            [
                dict(entry)
                for entry in restored_table_row['acl_entries']
                if entry['grantee'] == 'sqag_runtime' and entry['privilege_type'] == 'SELECT'
            ],
            [
                {
                    'grantee': 'sqag_runtime',
                    'grantor': 'sqag_migrator',
                    'privilege_type': 'SELECT',
                    'is_grantable': False,
                }
            ],
        )
        self.assertFalse(
            any(
                entry['grantee'] == table_grantor
                for entry in restored_table_row['acl_entries']
            )
        )

        with self.as_role('sqag_migrator') as connection:
            connection.execute('grant update on table public."sqag_generation_evidence" to "sqag_runtime"')
            connection.commit()
        try:
            extra_direct_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('sqag_generation_evidence_acl_provenance_mismatch' in error for error in extra_direct_errors), extra_direct_errors)
        finally:
            self._execute_admin_sql('revoke update on table public."sqag_generation_evidence" from "sqag_runtime"')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql('revoke update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" from "sqag_runtime"')
        self._execute_admin_sql('grant update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" to public')
        try:
            self.assertTrue(self._has_column_privilege('sqag_runtime', 'sqag_quote_publication_artifacts', 'checksum_sha256', 'UPDATE'))
            public_column_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('column_acl_public_authority_forbidden' in error for error in public_column_errors), public_column_errors)
        finally:
            self._execute_admin_sql('revoke update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" from public')
            self._grant_column_privilege('sqag_runtime', 'sqag_quote_publication_artifacts', 'checksum_sha256', 'UPDATE', grantor_role='sqag_migrator')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql('revoke update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" from "sqag_runtime"')
        with self.as_role('sqag_migrator') as connection:
            connection.execute('grant update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" to "sqag_runtime" with grant option')
            connection.commit()
        try:
            column_option_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any('column_acl_runtime_grant_option_forbidden' in error for error in column_option_errors), column_option_errors)
        finally:
            self._execute_admin_sql('revoke update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" from "sqag_runtime"')
            self._grant_column_privilege('sqag_runtime', 'sqag_quote_publication_artifacts', 'checksum_sha256', 'UPDATE', grantor_role='sqag_migrator')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        column_grantor = self._new_role('column_grantor')
        column_direct_grant_may_be_missing = False
        column_upstream_grant_may_exist = False
        column_downstream_grant_may_exist = False
        column_cleanup_errors: list[str] = []

        def run_column_cleanup(label: str, operation) -> None:
            try:
                operation()
            except Exception as exc:
                column_cleanup_errors.append(f'{label}:{exc}')

        def revoke_column_downstream() -> None:
            with self.as_role(column_grantor) as connection:
                connection.execute(
                    'revoke update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" '
                    'from "sqag_runtime"'
                )
                connection.commit()

        def revoke_column_upstream() -> None:
            with self.as_role('sqag_migrator') as connection:
                connection.execute(
                    f'revoke update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" '
                    f'from {_quote_identifier(column_grantor)}'
                )
                connection.commit()

        try:
            column_direct_grant_may_be_missing = True
            self._execute_admin_sql('revoke update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" from "sqag_runtime"')
            column_upstream_grant_may_exist = True
            with self.as_role('sqag_migrator') as connection:
                connection.execute(
                    f'grant update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" '
                    f'to {_quote_identifier(column_grantor)} with grant option'
                )
                connection.commit()
            column_downstream_grant_may_exist = True
            with self.as_role(column_grantor) as connection:
                connection.execute(
                    'grant update ("checksum_sha256") on table public."sqag_quote_publication_artifacts" '
                    'to "sqag_runtime"'
                )
                connection.commit()

            column_columns, column_rows = self._execute_contract_query('effective_runtime_column_privileges')
            self.assertEqual(column_columns, CANONICAL_QUERY_COLUMNS['effective_runtime_column_privileges'])
            table_columns, target_table_rows = self._execute_contract_query('effective_runtime_table_privileges')
            self.assertEqual(table_columns, CANONICAL_QUERY_COLUMNS['effective_runtime_table_privileges'])
            self.assertEqual(
                {
                    row['owner']
                    for row in target_table_rows
                    if row['table_name'] == 'sqag_quote_publication_artifacts'
                },
                {'sqag_migrator'},
            )
            column_row = next(
                row
                for row in column_rows
                if row['table_name'] == 'sqag_quote_publication_artifacts'
                and row['column_name'] == 'checksum_sha256'
                and row['privilege_type'] == 'UPDATE'
            )
            self.assertTrue(self._has_column_privilege('sqag_runtime', 'sqag_quote_publication_artifacts', 'checksum_sha256', 'UPDATE'))
            self.assertTrue(bool(column_row['effective']))
            self.assertFalse(bool(column_row['is_grantable']))
            self.assertFalse(
                self._has_column_privilege(
                    'sqag_runtime',
                    'sqag_quote_publication_artifacts',
                    'checksum_sha256',
                    'UPDATE WITH GRANT OPTION',
                )
            )
            column_runtime_acl = [
                dict(entry)
                for entry in column_row['acl_entries']
                if entry['grantee'] == 'sqag_runtime' and entry['privilege_type'] == 'UPDATE'
            ]
            self.assertEqual(
                column_runtime_acl,
                [
                    {
                        'grantee': 'sqag_runtime',
                        'grantor': column_grantor,
                        'privilege_type': 'UPDATE',
                        'is_grantable': False,
                    }
                ],
            )
            column_grantor_errors = self._evaluate_runtime_authority_rows()
            expected_column_error = (
                'public_table_classified_public.sqag_quote_publication_artifacts_'
                f'column_acl_runtime_grantor_invalid_checksum_sha256_UPDATE_{column_grantor}'
            )
            self.assertIn(expected_column_error, column_grantor_errors)
        finally:
            column_original_failure = sys.exc_info()[1]
            if column_downstream_grant_may_exist:
                run_column_cleanup('revoke_column_downstream_as_intermediary', revoke_column_downstream)
            if column_upstream_grant_may_exist:
                run_column_cleanup('revoke_column_upstream_as_owner', revoke_column_upstream)
            if column_direct_grant_may_be_missing:
                run_column_cleanup(
                    'restore_column_direct_as_owner',
                    lambda: self._grant_column_privilege(
                        'sqag_runtime',
                        'sqag_quote_publication_artifacts',
                        'checksum_sha256',
                        'UPDATE',
                        grantor_role='sqag_migrator',
                    ),
                )
            if column_cleanup_errors:
                message = 'column provenance cleanup failed: ' + '; '.join(column_cleanup_errors)
                if column_original_failure is None:
                    raise AssertionError(message)
                add_note = getattr(column_original_failure, 'add_note', None)
                if callable(add_note):
                    add_note(message)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        _, restored_column_rows = self._execute_contract_query('effective_runtime_column_privileges')
        restored_column_row = next(
            row
            for row in restored_column_rows
            if row['table_name'] == 'sqag_quote_publication_artifacts'
            and row['column_name'] == 'checksum_sha256'
            and row['privilege_type'] == 'UPDATE'
        )
        self.assertEqual(
            [
                dict(entry)
                for entry in restored_column_row['acl_entries']
                if entry['grantee'] == 'sqag_runtime' and entry['privilege_type'] == 'UPDATE'
            ],
            [
                {
                    'grantee': 'sqag_runtime',
                    'grantor': 'sqag_migrator',
                    'privilege_type': 'UPDATE',
                    'is_grantable': False,
                }
            ],
        )
        self.assertFalse(
            any(
                entry['grantee'] == column_grantor
                for entry in restored_column_row['acl_entries']
            )
        )

        # The migration ledger has no dependents. Drop it only at the end of this
        # disposable test so database teardown is the guaranteed restoration.
        self._execute_admin_sql('drop table public."sqag_schema_migrations"')
        _, missing_table_rows = self._execute_contract_query('effective_runtime_table_privileges')
        self.assertFalse(any(row['table_name'] == 'sqag_schema_migrations' for row in missing_table_rows))
        missing_manifest_errors = self._evaluate_runtime_authority_rows()
        self.assertTrue(any('public_table_classified_relation_missing_sqag_schema_migrations' in error for error in missing_manifest_errors), missing_manifest_errors)

    def _assert_transactional_classified_column_drift(
        self, ddl: tuple[str, ...], expected_fragment: str
    ) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        connection = self.connect()
        try:
            for statement in ddl:
                connection.execute(statement)
            errors = self._evaluate_runtime_authority_on(connection)
            self.assertTrue(
                any(expected_fragment in error for error in errors),
                errors,
            )
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_classified_column_rename_same_count_fails_closed_postgres(self) -> None:
        self._assert_transactional_classified_column_drift(
            (
                'alter table public."sqag_profiles" '
                'rename column "payload_json" to "payload_json_renamed"',
            ),
            "column_name_mismatch_ordinal_3",
        )

    def test_classified_column_same_name_wrong_type_fails_closed_postgres(self) -> None:
        self._assert_transactional_classified_column_drift(
            (
                'alter table public."sqag_profiles" '
                'alter column "payload_json" type varchar(64)',
            ),
            "column_type_mismatch_payload_json",
        )

    def test_classified_column_missing_expected_fails_closed_postgres(self) -> None:
        self._assert_transactional_classified_column_drift(
            ('alter table public."sqag_profiles" drop column "payload_json"',),
            "column_missing_payload_json",
        )

    def test_classified_column_replacement_masks_count_fails_closed_postgres(self) -> None:
        self._assert_transactional_classified_column_drift(
            (
                'alter table public."sqag_profiles" drop column "payload_json"',
                'alter table public."sqag_profiles" '
                'add column "replacement_payload_json" text',
            ),
            "column_unexpected_replacement_payload_json",
        )

    def test_non_system_schema_authority_all_object_kinds_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        schema_name = f"application_h23_{uuid.uuid4().hex[:8]}"
        schema_ident = _quote_identifier(schema_name)
        table_ident = f'{schema_ident}."authority_table"'
        sequence_ident = f'{schema_ident}."authority_sequence"'
        routine_ident = f'{schema_ident}."authority_function"()'
        view_ident = f'{schema_ident}."authority_view"'
        materialized_ident = f'{schema_ident}."authority_materialized"'

        connection = self.connect()
        try:
            connection.execute(
                f'create schema {schema_ident} authorization "sqag_migrator"'
            )
            connection.execute('set role "sqag_migrator"')
            connection.execute(
                f"create table {table_ident} (id integer not null, note text)"
            )
            connection.execute(f"create sequence {sequence_ident}")
            connection.execute(
                f"create function {routine_ident} returns integer "
                "language sql as 'select 1'"
            )
            connection.execute(f"revoke execute on function {routine_ident} from public")
            connection.execute(
                f"create view {view_ident} as select id, note from {table_ident}"
            )
            connection.execute(
                f"create materialized view {materialized_ident} "
                f"as select id from {table_ident}"
            )
            connection.execute("reset role")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        cleanup_steps = [
            (
                "drop_h23_materialized",
                f"drop materialized view if exists {materialized_ident}",
            ),
            ("drop_h23_view", f"drop view if exists {view_ident}"),
            ("drop_h23_routine", f"drop function if exists {routine_ident}"),
            ("drop_h23_sequence", f"drop sequence if exists {sequence_ident}"),
            ("drop_h23_table", f"drop table if exists {table_ident}"),
            ("drop_h23_schema", f"drop schema if exists {schema_ident}"),
        ]
        try:
            self.assertFalse(contract_validator._is_postgresql_system_schema(schema_name))
            self.assertEqual(self._evaluate_runtime_authority_rows(), ())
            controls = (
                (
                    "schema",
                    f'grant usage on schema {schema_ident} to "sqag_runtime" with grant option',
                    f'revoke usage on schema {schema_ident} from "sqag_runtime"',
                    "runtime_schema_non_public_authority",
                ),
                (
                    "schema_create",
                    f'grant create on schema {schema_ident} to "sqag_runtime" with grant option',
                    f'revoke create on schema {schema_ident} from "sqag_runtime"',
                    "runtime_schema_non_public_authority",
                ),
                (
                    "table",
                    f'grant select on table {table_ident} to "sqag_runtime" with grant option',
                    f'revoke select on table {table_ident} from "sqag_runtime"',
                    "runtime_privilege_mismatch_SELECT_expected_False_got_True",
                ),
                (
                    "column",
                    f'grant update (note) on table {table_ident} to "sqag_runtime" with grant option',
                    f'revoke update (note) on table {table_ident} from "sqag_runtime"',
                    "runtime_privilege_mismatch_UPDATE_expected_False_got_True",
                ),
                (
                    "sequence",
                    f'grant usage on sequence {sequence_ident} to "sqag_runtime" with grant option',
                    f'revoke usage on sequence {sequence_ident} from "sqag_runtime"',
                    "runtime_sequence_non_public_authority",
                ),
                (
                    "routine",
                    f'grant execute on function {routine_ident} to "sqag_runtime" with grant option',
                    f'revoke execute on function {routine_ident} from "sqag_runtime"',
                    "runtime_routine_non_public_authority",
                ),
                (
                    "view",
                    f'grant select on table {view_ident} to "sqag_runtime" with grant option',
                    f'revoke select on table {view_ident} from "sqag_runtime"',
                    "unclassified_ordinary_view_runtime_authority",
                ),
                (
                    "materialized_view",
                    f'grant select on table {materialized_ident} to "sqag_runtime" with grant option',
                    f'revoke select on table {materialized_ident} from "sqag_runtime"',
                    "materialized_view_runtime_select_forbidden",
                ),
            )
            for label, grant_sql, revoke_sql, expected_fragment in controls:
                with self.subTest(h23_control=label):
                    self._execute_admin_sql(grant_sql)
                    try:
                        errors = self._evaluate_runtime_authority_rows()
                        self.assertTrue(
                            any(expected_fragment in error for error in errors),
                            errors,
                        )
                    finally:
                        self._execute_admin_sql(revoke_sql)
                    self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        finally:
            primary_failure = sys.exc_info()[1]
            try:
                self._cleanup_steps(cleanup_steps)
            except Exception as cleanup_error:
                message = f"H23 cleanup failed: {cleanup_error}"
                if primary_failure is None:
                    raise
                add_note = getattr(primary_failure, "add_note", None)
                if callable(add_note):
                    add_note(message)

        connection = self.connect()
        try:
            row = connection.execute(
                "select exists (select 1 from pg_catalog.pg_namespace where nspname = %s) "
                "as present",
                (schema_name,),
            ).fetchone()
            self.assertFalse(bool(_row_dict(row, "present")))
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_explicit_checksum_column_exception_is_schema_scoped_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        schema_name = f"application_h27_{uuid.uuid4().hex[:8]}"
        schema_ident = _quote_identifier(schema_name)
        shadow_table_ident = f'{schema_ident}."sqag_quote_publication_artifacts"'
        wrong_table_name = f"h27_wrong_table_{uuid.uuid4().hex[:8]}"
        wrong_table_ident = f'public.{_quote_identifier(wrong_table_name)}'
        connection = self.connect()
        try:
            connection.execute(f'create schema {schema_ident} authorization "sqag_migrator"')
            connection.execute('set role "sqag_migrator"')
            connection.execute(
                f'create table {shadow_table_ident} '
                '(checksum_sha256 text, note text)'
            )
            connection.execute(
                f'create table {wrong_table_ident} '
                '(checksum_sha256 text, note text)'
            )
            connection.execute("reset role")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

        cleanup_steps = [
            ("drop_h27_wrong_table", f"drop table if exists {wrong_table_ident}"),
            ("drop_h27_shadow_table", f"drop table if exists {shadow_table_ident}"),
            ("drop_h27_shadow_schema", f"drop schema if exists {schema_ident}"),
        ]
        try:
            self.assertEqual(self._evaluate_runtime_authority_rows(), ())
            controls = (
                (
                    "shadow_checksum",
                    f'grant usage on schema {schema_ident} to "sqag_runtime"',
                    f'grant update ("checksum_sha256") on table {shadow_table_ident} to "sqag_runtime"',
                    f'revoke update ("checksum_sha256") on table {shadow_table_ident} from "sqag_runtime"',
                    f'revoke usage on schema {schema_ident} from "sqag_runtime"',
                    "runtime_privilege_mismatch_UPDATE_expected_False_got_True",
                ),
                (
                    "shadow_wrong_column",
                    f'grant usage on schema {schema_ident} to "sqag_runtime"',
                    f'grant update ("note") on table {shadow_table_ident} to "sqag_runtime"',
                    f'revoke update ("note") on table {shadow_table_ident} from "sqag_runtime"',
                    f'revoke usage on schema {schema_ident} from "sqag_runtime"',
                    "runtime_privilege_mismatch_UPDATE_expected_False_got_True",
                ),
                (
                    "shadow_checksum_grant_option",
                    f'grant usage on schema {schema_ident} to "sqag_runtime"',
                    f'grant update ("checksum_sha256") on table {shadow_table_ident} to "sqag_runtime" with grant option',
                    f'revoke update ("checksum_sha256") on table {shadow_table_ident} from "sqag_runtime"',
                    f'revoke usage on schema {schema_ident} from "sqag_runtime"',
                    "runtime_grant_option_forbidden_UPDATE",
                ),
                (
                    "public_wrong_table",
                    "",
                    "grant update (\"checksum_sha256\") on table " + wrong_table_ident + ' to "sqag_runtime"',
                    "revoke update (\"checksum_sha256\") on table " + wrong_table_ident + ' from "sqag_runtime"',
                    "",
                    "runtime_privilege_mismatch_UPDATE_expected_False_got_True",
                ),
            )
            for label, pre_sql, grant_sql, revoke_sql, post_sql, expected_fragment in controls:
                with self.subTest(h27_control=label):
                    if pre_sql:
                        self._execute_admin_sql(pre_sql)
                    self._execute_admin_sql(grant_sql)
                    try:
                        errors = self._evaluate_runtime_authority_rows()
                        self.assertTrue(
                            any(expected_fragment in error for error in errors),
                            errors,
                        )
                    finally:
                        self._execute_admin_sql(revoke_sql)
                        if post_sql:
                            self._execute_admin_sql(post_sql)
                    self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        finally:
            primary_failure = sys.exc_info()[1]
            try:
                self._cleanup_steps(cleanup_steps)
            except Exception as cleanup_error:
                message = f"H27 cleanup failed: {cleanup_error}"
                if primary_failure is None:
                    raise
                add_note = getattr(primary_failure, "add_note", None)
                if callable(add_note):
                    add_note(message)

        connection = self.connect()
        try:
            namespace_row = connection.execute(
                "select exists (select 1 from pg_catalog.pg_namespace where nspname = %s) as present",
                (schema_name,),
            ).fetchone()
            wrong_table_row = connection.execute(
                "select exists (select 1 from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relname = %s) as present",
                (wrong_table_name,),
            ).fetchone()
            self.assertFalse(bool(_row_dict(namespace_row, "present")))
            self.assertFalse(bool(_row_dict(wrong_table_row, "present")))
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_role_membership_query_executes_and_invalid_alias_fails(self) -> None:
        invalid_query = self.contract["verification_queries"]["role_memberships"].replace(
            "am.admin_option", "a.admin_option", 1
        )
        connection = self.connect()
        try:
            with self.assertRaises(Exception) as failure:
                connection.execute(invalid_query).fetchall()
            connection.rollback()
            self.assertEqual(getattr(failure.exception, "sqlstate", None), "42P01")
        finally:
            connection.close()

        columns, rows = self._execute_contract_query("role_memberships")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["role_memberships"])
        expected_provider_edge = self._expected_provider_edge()
        expected_baseline = tuple(sorted((*self._membership_baseline, expected_provider_edge)))
        self.assertEqual(self._membership_tuples(rows), expected_baseline)
        self.assertEqual(self._runtime_like_memberships(self._membership_tuples(rows)), (expected_provider_edge,))

        parent_name = self._new_role("membership_parent")
        self._grant_role_membership(parent_name, "sqag_runtime", admin_option=False)
        admin_parent_name = self._new_role("membership_admin_parent")
        self._grant_role_membership(admin_parent_name, "sqag_runtime", admin_option=True)
        columns, rows = self._execute_contract_query("role_memberships")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["role_memberships"])
        actual = self._membership_tuples(rows)
        added = tuple(row for row in actual if row not in expected_baseline)
        self.assertEqual({row[0] for row in added}, {parent_name, admin_parent_name})
        self.assertEqual({row[1] for row in added}, {"sqag_runtime"})
        self.assertEqual({row[3] for row in added}, {False, True})
        self.assertTrue(all(type(option) is bool for row in added for option in row[3:]))
        self.assertEqual(self._runtime_like_memberships(actual), tuple(sorted((expected_provider_edge, *added))))
        membership_errors = contract_validator.validate_runtime_membership_edges(
            self.contract,
            rows,
        )
        self.assertTrue(any("runtime_as_member" in error for error in membership_errors))

    def _create_postgresql17_creator_admin_fixture(
        self,
    ) -> tuple[str, str, str, list[dict[str, Any]], dict[str, Any]]:
        with self.psycopg.connect(
            postgres_test_conninfo(self.database_name),
            row_factory=self.dict_row,
        ) as bootstrap_connection:
            identity = bootstrap_connection.execute(
                "select session_user as bootstrap_user, current_setting('server_version_num')::int as version_num"
            ).fetchone()
            bootstrap_user = str(_row_dict(identity, "bootstrap_user"))
            self.assertGreaterEqual(int(_row_dict(identity, "version_num")), 170000)
            self.assertLess(int(_row_dict(identity, "version_num")), 180000)

        creator_name = self._new_role("pg17_creator")
        self._execute_admin_sql(f"alter role {_quote_identifier(creator_name)} createrole")
        runtime_name = f"sqag_rpc_role_pg17_runtime_{uuid.uuid4().hex[:8]}"
        _quote_identifier(runtime_name)
        connection = self.connect()
        try:
            connection.execute(f"set session authorization {_quote_identifier(creator_name)}")
            connection.execute(
                f"create role {_quote_identifier(runtime_name)} NOLOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT CONNECTION LIMIT -1"
            )
            connection.commit()
        finally:
            connection.close()
        type(self)._seen_roles.add(runtime_name)
        self.addCleanup(self._drop_role, runtime_name)

        columns, rows = self._execute_contract_query("role_memberships")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["role_memberships"])
        actual_edge = next(
            row
            for row in rows
            if row["role"] == runtime_name and row["member"] == creator_name
        )
        return runtime_name, creator_name, bootstrap_user, rows, actual_edge

    def _scope_postgresql17_creator_admin_fixture_participants(
        self,
        rows: list[dict[str, Any]],
        actual_edge: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        fixture_grantor = self._new_role("pg17_fixture_grantor")
        fixture_edge = {**actual_edge, "grantor": fixture_grantor}
        fixture_rows = [
            fixture_edge if row == actual_edge else copy.deepcopy(row) for row in rows
        ]
        self.assertEqual(len(fixture_rows), len(rows))
        self.assertEqual(sum(row == fixture_edge for row in fixture_rows), 1)
        return fixture_grantor, fixture_rows, fixture_edge

    def test_run35_creator_admin_fixture_classifies_only_exact_edge_as_protected(self) -> None:
        runtime_name, creator_name, _, rows, actual_edge = (
            self._create_postgresql17_creator_admin_fixture()
        )
        grantor_name, fixture_rows, fixture_actual_edge = (
            self._scope_postgresql17_creator_admin_fixture_participants(rows, actual_edge)
        )
        fixture_manifest = copy.deepcopy(self.contract)
        fixture_edge = fixture_manifest["roles"]["runtime"]["provider_control_edges"][0]
        fixture_edge["parent_role"] = runtime_name
        fixture_edge["member_role"] = creator_name
        fixture_edge["grantor"] = grantor_name
        self.assertEqual(fixture_edge["classification"], "postgresql17_creator_admin_control")

        unrelated_rows = [row for row in fixture_rows if row != fixture_actual_edge]
        protected_participants = {runtime_name, creator_name, grantor_name}
        self.assertTrue(unrelated_rows, "the PostgreSQL fixture must retain unrelated graph rows")
        for row in unrelated_rows:
            self.assertTrue(
                {str(row["role"]), str(row["member"]), str(row["grantor"])}.isdisjoint(
                    protected_participants
                ),
                row,
            )
        self.assertEqual(
            contract_validator.validate_runtime_membership_edges(
                fixture_manifest,
                fixture_rows,
                enforce_production_identity=False,
            ),
            (),
        )

        material_row = copy.deepcopy(unrelated_rows[0])
        protected_variants = {
            "parent": ({**material_row, "role": creator_name}, "protected_role_edge_forbidden"),
            "member": ({**material_row, "member": creator_name}, "protected_role_edge_forbidden"),
            "grantor": ({**material_row, "grantor": grantor_name}, "protected_grantor_forbidden"),
        }
        for label, (variant, expected_fragment) in protected_variants.items():
            with self.subTest(protected_participant=label):
                errors = contract_validator.validate_runtime_membership_edges(
                    fixture_manifest,
                    [*fixture_rows, variant],
                    enforce_production_identity=False,
                )
                self.assertTrue(any(expected_fragment in error for error in errors), errors)

        duplicate_errors = contract_validator.validate_runtime_membership_edges(
            fixture_manifest,
            [*fixture_rows, copy.deepcopy(fixture_actual_edge)],
            enforce_production_identity=False,
        )
        self.assertTrue(
            any("duplicate_role_membership_row" in error for error in duplicate_errors),
            duplicate_errors,
        )

        unsafe_variants = {
            "admin": (
                {**fixture_actual_edge, "admin_option": False},
                "provider_control_edge_tuple_mismatch",
            ),
            "inherit": (
                {**fixture_actual_edge, "inherit_option": True},
                "protected_inherit_option_forbidden",
            ),
            "set": (
                {**fixture_actual_edge, "set_option": True},
                "protected_set_option_forbidden",
            ),
        }
        unrelated_only = [row for row in fixture_rows if row != fixture_actual_edge]
        for label, (variant, expected_fragment) in unsafe_variants.items():
            with self.subTest(unsafe_option=label):
                errors = contract_validator.validate_runtime_membership_edges(
                    fixture_manifest,
                    [*unrelated_only, variant],
                    enforce_production_identity=False,
                )
                self.assertTrue(any(expected_fragment in error for error in errors), errors)

        recursive_rows = [
            {
                **material_row,
                "role": creator_name,
                "member": "run35_recursive_bridge",
            },
            {
                **material_row,
                "role": "run35_recursive_bridge",
                "member": creator_name,
            },
        ]
        recursive_errors = contract_validator.validate_runtime_membership_edges(
            fixture_manifest,
            [*fixture_rows, *recursive_rows],
            enforce_production_identity=False,
        )
        self.assertTrue(
            any("recursive_protected_role_membership_path" in error for error in recursive_errors),
            recursive_errors,
        )

    def test_postgresql17_creator_admin_edge_is_system_generated_dormant_and_non_removable(self) -> None:
        runtime_name, creator_name, bootstrap_user, rows, actual_edge = (
            self._create_postgresql17_creator_admin_fixture()
        )
        self.assertEqual(
            actual_edge,
            {
                "role": runtime_name,
                "member": creator_name,
                "grantor": bootstrap_user,
                "admin_option": True,
                "inherit_option": False,
                "set_option": False,
            },
        )

        fixture_grantor, fixture_rows, fixture_actual_edge = (
            self._scope_postgresql17_creator_admin_fixture_participants(rows, actual_edge)
        )
        fixture_manifest = copy.deepcopy(self.contract)
        fixture_edge = fixture_manifest["roles"]["runtime"]["provider_control_edges"][0]
        fixture_edge["parent_role"] = runtime_name
        fixture_edge["member_role"] = creator_name
        fixture_edge["grantor"] = fixture_grantor
        self.assertEqual(
            contract_validator.validate_runtime_membership_edges(
                fixture_manifest,
                fixture_rows,
                enforce_production_identity=False,
            ),
            (),
        )

        connection = self.connect()
        try:
            connection.execute(f"set session authorization {_quote_identifier(creator_name)}")
            with self.assertRaises(Exception) as set_failure:
                connection.execute(f"set role {_quote_identifier(runtime_name)}")
            self.assertEqual(getattr(set_failure.exception, "sqlstate", None), "42501")
            connection.rollback()
        finally:
            connection.close()

        revoke_result = "completed"
        connection = self.connect()
        try:
            connection.execute(f"set session authorization {_quote_identifier(creator_name)}")
            try:
                connection.execute(
                    f"revoke {_quote_identifier(runtime_name)} from {_quote_identifier(creator_name)}"
                )
                connection.commit()
            except Exception as exc:
                revoke_result = f"rejected:{getattr(exc, 'sqlstate', 'unknown')}"
                connection.rollback()
        finally:
            connection.close()
        self.assertTrue(revoke_result == "completed" or revoke_result.startswith("rejected:"))

        _, rows_after_revoke = self._execute_contract_query("role_memberships")
        edge_after_revoke = next(
            row
            for row in rows_after_revoke
            if row["role"] == runtime_name and row["member"] == creator_name
        )
        self.assertEqual(edge_after_revoke, actual_edge)

        self._grant_database_privilege(creator_name, "CREATE")
        self.assertTrue(self._has_database_privilege(creator_name, "CREATE"))
        self.assertFalse(self._has_database_privilege(runtime_name, "CREATE"))

        mutations = {
            "parent": {**fixture_actual_edge, "role": "unknown_parent"},
            "member": {**fixture_actual_edge, "member": "unknown_member"},
            "grantor": {**fixture_actual_edge, "grantor": "unknown_grantor"},
            "admin": {**fixture_actual_edge, "admin_option": False},
            "inherit": {**fixture_actual_edge, "inherit_option": True},
            "set": {**fixture_actual_edge, "set_option": True},
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                self.assertTrue(
                    contract_validator.validate_runtime_membership_edges(
                        fixture_manifest,
                        [mutation],
                        enforce_production_identity=False,
                    )
                )
        additional = {**fixture_actual_edge, "member": "unknown_second_member"}
        additional_errors = contract_validator.validate_runtime_membership_edges(
            fixture_manifest,
            [fixture_actual_edge, additional],
            enforce_production_identity=False,
        )
        self.assertTrue(any("runtime_edge_count" in error for error in additional_errors))

    def test_effective_runtime_privilege_introduced_through_membership_fails_edge_contract(self) -> None:
        self.apply_migrations()
        privileged_parent = self._new_role("membership_privileged_parent")
        forbidden_table = sorted(FORBIDDEN_TABLES)[0]
        self._grant_table_privilege(privileged_parent, forbidden_table, "SELECT")
        self._grant_role_membership(privileged_parent, "sqag_runtime")
        self.assertTrue(self._has_table_privilege("sqag_runtime", forbidden_table, "SELECT"))
        _, rows = self._execute_contract_query("role_memberships")
        membership_errors = contract_validator.validate_runtime_membership_edges(
            self.contract,
            rows,
        )
        self.assertTrue(
            any("runtime_privilege_membership_path" in error for error in membership_errors),
            membership_errors,
        )
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_matrix("sqag_runtime")

    def test_canonical_query_shape_contract_is_independent_and_complete(self) -> None:
        self.assertEqual(tuple(CANONICAL_QUERY_COLUMNS), CANONICAL_QUERY_KEYS)
        manifest_keys = tuple(self.contract["verification_queries"])
        self.assertEqual(manifest_keys, CANONICAL_QUERY_KEYS)
        self.assertEqual(set(CANONICAL_QUERY_KEYS), set(self.contract["verification_queries"]))
        self.assertEqual(len(CANONICAL_QUERY_KEYS), 16)
        self.assertEqual(len(set(CANONICAL_QUERY_KEYS)), 16)
        self.assertEqual(len(self.contract["verification_queries"]), 16)
        self.assertEqual(len(set(self.contract["verification_queries"])), 16)
        self.assertIn("view_acl", CANONICAL_QUERY_KEYS)
        for query_key in CANONICAL_QUERY_KEYS:
            self.assertIn(query_key, CANONICAL_QUERY_COLUMNS)
            query = self.contract["verification_queries"][query_key]
            self.assertIsInstance(query, str)
            self.assertTrue(query.strip())
        for index in range(1, len(CANONICAL_QUERY_KEYS)):
            reordered = CANONICAL_QUERY_KEYS[index:] + CANONICAL_QUERY_KEYS[:index]
            self.assertEqual(set(reordered), set(CANONICAL_QUERY_KEYS))
            self.assertNotEqual(tuple(reordered), CANONICAL_QUERY_KEYS)

    def test_actual_routine_inventory_has_no_stock_provider_exception(self) -> None:
        runtime_name = self._new_role("routine_inventory")
        self.apply_migrations()
        self._assert_routine_inventory(runtime_name=runtime_name)

    def test_extra_public_routine_is_rejected_by_inventory_proof(self) -> None:
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute(
                "set role \"sqag_migrator\""
            )
            connection.execute(
                "create function public.sqag_rpc_unexpected() returns integer language sql as 'select 1'"
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(
            self._cleanup_steps,
            [("drop_unexpected_function", "drop function public.sqag_rpc_unexpected()")],
        )
        rows = self._routine_inventory()
        self.assertIn("sqag_rpc_unexpected", {str(row["proname"]) for row in rows})
        with self.assertRaises(AssertionError):
            self._assert_routine_inventory()

    def test_provider_show_db_tree_is_only_bounded_exception(self) -> None:
        runtime_name = self._new_role("provider_runtime")
        provider_name = "neondb_owner"
        self._grant_database_privilege(runtime_name, "CONNECT")
        self._grant_schema_privilege(runtime_name, "USAGE")
        self._grant_schema_privilege(provider_name, "CREATE")
        connection = self.connect()
        try:
            connection.execute(f"set role {_quote_identifier(provider_name)}")
            connection.execute("create function public.show_db_tree() returns jsonb language sql as $$ select '{}'::jsonb $$")
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        before_public_execute = self._public_function_execute("show_db_tree")
        self._public_function_baselines["show_db_tree"] = before_public_execute
        self.addCleanup(
            self._restore_and_drop_public_function,
            "show_db_tree",
            before_public_execute,
        )
        self.assertTrue(before_public_execute)
        self.assertTrue(self._has_function_privilege(runtime_name, "show_db_tree"))
        self.assertEqual(self._call_function_as_role(runtime_name, "show_db_tree"), {})
        self._revoke_public_execute("show_db_tree", register_cleanup=False)
        self.assertFalse(self._has_function_privilege(runtime_name, "show_db_tree"))
        with self.assertRaises(Exception) as denied:
            self._call_function_as_role(runtime_name, "show_db_tree")
        self.assertEqual(getattr(denied.exception, "sqlstate", None), "42501")
        self._restore_public_execute("show_db_tree", before_public_execute)
        self.assertTrue(self._has_function_privilege(runtime_name, "show_db_tree"))
        self.assertEqual(self._call_function_as_role(runtime_name, "show_db_tree"), {})
        self.apply_migrations()
        rows = self._assert_routine_inventory(provider_name=provider_name, runtime_name=runtime_name)
        provider_row = next(row for row in rows if row["proname"] == "show_db_tree")
        self.assertEqual(provider_row["owner"], "neondb_owner")
        self.assertTrue(provider_row["public_execute"])
        self.assertEqual(self._public_function_execute("show_db_tree"), before_public_execute)

    def test_provider_exception_posture_substitutions_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        provider_name = "neondb_owner"
        self._grant_schema_privilege(provider_name, "CREATE")
        connection = self.connect()
        try:
            connection.execute(f"set role {_quote_identifier(provider_name)}")
            connection.execute(
                "create function public.show_db_tree() returns jsonb "
                "language sql as $$ select '{}'::jsonb $$"
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        provider_public_before = self._public_function_execute("show_db_tree")
        self.assertTrue(provider_public_before)
        self.addCleanup(
            self._restore_and_drop_public_function,
            "show_db_tree",
            provider_public_before,
        )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql("alter function public.show_db_tree() security definer")
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_provider_exception_acl_security_mismatch_show_db_tree",
                errors,
            )
        finally:
            self._execute_admin_sql("alter function public.show_db_tree() security invoker")
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(
            'alter function public.show_db_tree() owner to "sqag_migrator"'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_provider_exception_acl_owner_mismatch_show_db_tree",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'alter function public.show_db_tree() owner to {_quote_identifier(provider_name)}'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_public_routine_authority_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        provider_name = "neondb_owner"
        self._grant_schema_privilege(provider_name, "CREATE")
        connection = self.connect()
        try:
            connection.execute(f"set role {_quote_identifier(provider_name)}")
            connection.execute(
                "create function public.show_db_tree() returns jsonb "
                "language sql as $$ select '{}'::jsonb $$"
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        provider_public_before = self._public_function_execute("show_db_tree")
        self.assertTrue(provider_public_before)
        self.addCleanup(
            self._restore_and_drop_public_function,
            "show_db_tree",
            provider_public_before,
        )

        unrelated_name = "h26_unrelated_public"
        connection = self.connect()
        try:
            connection.execute('set role "sqag_migrator"')
            connection.execute(
                f"create function public.{_quote_identifier(unrelated_name)}() returns integer "
                "language sql as 'select 1'"
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        unrelated_public_before = self._public_function_execute(unrelated_name)
        self.assertTrue(unrelated_public_before)
        self.addCleanup(
            self._restore_and_drop_public_function,
            unrelated_name,
            unrelated_public_before,
        )
        self._revoke_public_execute(unrelated_name, register_cleanup=False)

        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        controls = (
            (
                'grant execute on function public."sqag_reject_immutable_change"() to "sqag_runtime"',
                'revoke execute on function public."sqag_reject_immutable_change"() from "sqag_runtime"',
                "runtime_public_trigger_direct_execute_forbidden_sqag_reject_immutable_change",
            ),
            (
                'grant execute on function public."sqag_reject_immutable_change"() to public',
                'revoke execute on function public."sqag_reject_immutable_change"() from public',
                "runtime_public_trigger_public_execute_forbidden_sqag_reject_immutable_change",
            ),
            (
                f'grant execute on function public."{unrelated_name}"() to "sqag_runtime"',
                f'revoke execute on function public."{unrelated_name}"() from "sqag_runtime"',
                "runtime_public_unclassified_authority_public.h26_unrelated_public",
            ),
            (
                f'grant execute on function public."{unrelated_name}"() to "sqag_runtime" with grant option',
                f'revoke execute on function public."{unrelated_name}"() from "sqag_runtime"',
                "runtime_public_unclassified_authority_public.h26_unrelated_public",
            ),
            (
                'grant execute on function public."show_db_tree"() to "sqag_runtime"',
                'revoke execute on function public."show_db_tree"() from "sqag_runtime"',
                "runtime_provider_exception_direct_execute_forbidden_show_db_tree",
            ),
            (
                'grant execute on function public."show_db_tree"() to "sqag_runtime" with grant option',
                'revoke execute on function public."show_db_tree"() from "sqag_runtime"',
                "runtime_provider_exception_grant_option_forbidden_show_db_tree",
            ),
        )
        for grant_sql, revoke_sql, expected_fragment in controls:
            with self.subTest(h26_control=expected_fragment):
                self._execute_admin_sql(grant_sql)
                try:
                    errors = self._evaluate_runtime_authority_rows()
                    self.assertTrue(any(expected_fragment in error for error in errors), errors)
                finally:
                    self._execute_admin_sql(revoke_sql)
                self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        _, routine_rows = self._execute_contract_query("effective_runtime_routine_privileges")
        substituted_rows = [dict(row) for row in routine_rows]
        provider_row = next(row for row in substituted_rows if row["routine_name"] == "show_db_tree")
        provider_row["identity_arguments"] = "integer"
        substitution_errors = contract_validator.evaluate_schema_wide_runtime_authority(
            self.contract,
            None,
            None,
            substituted_rows,
        )
        self.assertTrue(
            any("runtime_public_unclassified_authority_public.show_db_tree" in error for error in substitution_errors),
            substitution_errors,
        )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_trigger_dependencies_match_migrated_routine_classification(self) -> None:
        self.apply_migrations()
        self.assertEqual(
            self._trigger_dependencies(),
            {
                ("sqag_generation_evidence_no_update", "sqag_generation_evidence", "sqag_reject_immutable_change"),
                ("sqag_audit_events_no_update", "sqag_audit_events", "sqag_reject_immutable_change"),
                ("sqag_feedback_linkage_no_update", "sqag_feedback", "sqag_reject_immutable_change"),
                ("sqag_generation_evidence_guard_delete", "sqag_generation_evidence", "sqag_require_retention_delete_authorization"),
                ("sqag_audit_events_guard_delete", "sqag_audit_events", "sqag_require_retention_delete_authorization"),
            },
        )

    def test_routine_trigger_binding_structural_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        trigger_name = "sqag_generation_evidence_no_update"
        source_table = "sqag_generation_evidence"
        rebound_table = "sqag_feedback"
        self._execute_admin_sql(
            f'drop trigger "{trigger_name}" on public."{source_table}"'
        )
        self._execute_admin_sql(
            f'create trigger "{trigger_name}" before update on public."{rebound_table}" '
            'for each row execute function public.sqag_reject_immutable_change()'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(
                any(
                    "routine_structural_trigger_binding_mismatch_sqag_reject_immutable_change"
                    in error
                    for error in errors
                ),
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'drop trigger "{trigger_name}" on public."{rebound_table}"'
            )
            self._execute_admin_sql(
                f'create trigger "{trigger_name}" before update on public."{source_table}" '
                'for each row execute function public.sqag_reject_immutable_change()'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(
            f'alter table public."{source_table}" disable trigger "{trigger_name}"'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(
                any(
                    "routine_structural_trigger_binding_mismatch_sqag_reject_immutable_change"
                    in error
                    for error in errors
                ),
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'alter table public."{source_table}" enable trigger "{trigger_name}"'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_migration_derived_table_structure_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        connection = self.connect()
        try:
            row = connection.execute(
                "select conname from pg_catalog.pg_constraint "
                "where conrelid = 'public.sqag_quote_publication_versions'::regclass "
                "and contype = 'c' "
                "and pg_catalog.pg_get_constraintdef(oid, true) ilike '%%state%%' "
                "order by conname limit 1"
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()
        if row is None:
            self.fail('publication state check constraint was not found')
        constraint_name = str(_row_dict(row, 'conname'))
        self._execute_admin_sql(
            f'alter table public."sqag_quote_publication_versions" drop constraint {_quote_identifier(constraint_name)}'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(
                any(
                    'table_structural_constraint_contract_mismatch_sqag_quote_publication_versions'
                    in error
                    for error in errors
                ),
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'alter table public."sqag_quote_publication_versions" add constraint '
                f'{_quote_identifier(constraint_name)} check '
                "(state in ('staged','published','superseded','failed'))"
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        index_name = 'sqag_generation_runs_workspace_job_uidx'
        self._execute_admin_sql(f'drop index public.{_quote_identifier(index_name)}')
        self._execute_admin_sql(
            f'create index {_quote_identifier(index_name)} on public."sqag_generation_runs" '
            '(workspace_id, job_id) where job_id is not null'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                f'table_structural_index_{index_name}_is_unique_mismatch',
                errors,
            )
        finally:
            self._execute_admin_sql(f'drop index public.{_quote_identifier(index_name)}')
            self._execute_admin_sql(
                f'create unique index {_quote_identifier(index_name)} on public."sqag_generation_runs" '
                '(workspace_id, job_id) where job_id is not null'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())


        rule_name = f"sqag_h46_unexpected_{uuid.uuid4().hex[:8]}"
        self._execute_admin_sql(
            f'create rule {_quote_identifier(rule_name)} as on insert '
            'to public."sqag_profiles" do instead nothing'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "table_structural_rule_binding_contract_mismatch_sqag_profiles",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'drop rule if exists {_quote_identifier(rule_name)} '
                'on public."sqag_profiles"'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        connection = self.connect()
        try:
            row = connection.execute(
                "select c.conname, relation.relname as table_name, "
                "pg_catalog.pg_get_constraintdef(c.oid, true) as definition, "
                "c.convalidated, c.condeferrable, c.condeferred "
                "from pg_catalog.pg_constraint c "
                "join pg_catalog.pg_class relation on relation.oid = c.conrelid "
                "join pg_catalog.pg_namespace namespace on namespace.oid = relation.relnamespace "
                "where c.contype = 'f' and array_length(c.conkey, 1) = 2 "
                "and namespace.nspname = 'public' and relation.relname like 'sqag_' || chr(37) "
                "order by relation.relname, c.conname limit 1"
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()
        if row is None:
            self.fail("composite migration-defined foreign key was not found")
        constraint_name = str(_row_dict(row, "conname"))
        table_name = str(_row_dict(row, "table_name"))
        baseline_definition = str(_row_dict(row, "definition"))
        self.assertTrue(bool(_row_dict(row, "convalidated")))
        table_columns, table_rows = self._execute_contract_query("table_acl")
        self.assertEqual(table_columns, CANONICAL_QUERY_COLUMNS["table_acl"])
        table_row = next(item for item in table_rows if item["relname"] == table_name)
        constraint_row = next(
            item for item in table_row["table_constraints"]
            if item["constraint_name"] == constraint_name
        )
        self.assertEqual(constraint_row["match_type"], "SIMPLE")
        full_definition = re.sub(
            r"\s+(DEFERRABLE|NOT DEFERRABLE)\b",
            r" MATCH FULL\1",
            baseline_definition,
            count=1,
            flags=re.IGNORECASE,
        )
        if full_definition == baseline_definition:
            full_definition = f"{baseline_definition} MATCH FULL"
        table_ident = _quote_identifier(table_name)
        constraint_ident = _quote_identifier(constraint_name)
        self._execute_admin_sql(
            f'alter table public.{table_ident} drop constraint {constraint_ident}'
        )
        try:
            self._execute_admin_sql(
                f'alter table public.{table_ident} add constraint {constraint_ident} '
                f'{full_definition}'
            )
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                f"table_structural_constraint_contract_mismatch_{table_name}",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'alter table public.{table_ident} drop constraint if exists {constraint_ident}'
            )
            self._execute_admin_sql(
                f'alter table public.{table_ident} add constraint {constraint_ident} '
                f'{baseline_definition}'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_h53_collation_identity_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        columns, rows = self._execute_contract_query("effective_runtime_table_privileges")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["effective_runtime_table_privileges"])
        baseline_row = next(
            row
            for row in rows
            if row["schema_name"] == "public"
            and row["table_name"] == "sqag_feedback"
            and row["privilege_type"] == "SELECT"
        )
        baseline_columns = {entry["name"]: entry for entry in baseline_row["column_contract"]}
        self.assertEqual(baseline_columns["support_reference"]["collation"], "database_default")
        self.assertEqual(baseline_columns["legal_hold"]["collation"], "none")

        table_ident = 'public."sqag_feedback"'
        default_collation = 'pg_catalog."default"'

        def assert_collation_rejected(collation: str) -> None:
            self._execute_admin_sql(
                f'alter table {table_ident} alter column "support_reference" '
                f"type text collate {collation}"
            )
            try:
                mutated_columns, mutated_rows = self._execute_contract_query(
                    "effective_runtime_table_privileges"
                )
                self.assertEqual(
                    mutated_columns,
                    CANONICAL_QUERY_COLUMNS["effective_runtime_table_privileges"],
                )
                errors = contract_validator.evaluate_public_table_like_authority(
                    self.contract,
                    mutated_rows,
                    self._execute_contract_query("effective_runtime_column_privileges")[1],
                )
                self.assertTrue(
                    any(
                        "public_table_classified_public.sqag_feedback_column_collation_mismatch_support_reference"
                        in error
                        for error in errors
                    ),
                    errors,
                )
            finally:
                self._execute_admin_sql(
                    f'alter table {table_ident} alter column "support_reference" '
                    f"type text collate {default_collation}"
                )

        # A distinct deterministic collation proves the H53-C3 identity boundary.
        assert_collation_rejected('pg_catalog."C"')

        capability_connection = self.connect()
        try:
            capability_row = capability_connection.execute(
                "select exists ("
                "select 1 from pg_catalog.pg_collation where collprovider = 'i'"
                ") as icu_available"
            ).fetchone()
            icu_available = bool(_row_dict(capability_row, "icu_available"))
        finally:
            capability_connection.rollback()
            capability_connection.close()

        custom_collation_name = f"sqag_h53_nondeterministic_{uuid.uuid4().hex[:8]}"
        custom_collation = f"public.{_quote_identifier(custom_collation_name)}"
        custom_collation_created = False
        if icu_available:
            self._execute_admin_sql(
                f"create collation {custom_collation} "
                "(provider = icu, locale = 'und-u-ks-level2', deterministic = false)"
            )
            custom_collation_created = True
        else:
            # A PostgreSQL build without ICU uses the executable deterministic
            # identity mutation above rather than skipping H53-C2.
            custom_collation = 'pg_catalog."C"'
        try:
            assert_collation_rejected(custom_collation)
        finally:
            if custom_collation_created:
                self._execute_admin_sql(f"drop collation if exists {custom_collation}")
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_h54_database_operability_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        database_ident = _quote_identifier(self.database_name)

        def target_database_sql(sql: str) -> None:
            connection = self.psycopg.connect(
                postgres_test_conninfo(),
                row_factory=self.dict_row,
                options="-c default_transaction_read_only=off",
            )
            try:
                connection.execute(sql)
                connection.commit()
            finally:
                connection.close()

        def database_errors(connection) -> tuple[str, ...]:
            columns, rows = self._execute_contract_query_on(connection, "database_acl")
            self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["database_acl"])
            errors: list[str] = []
            contract_validator._validate_database_acl_evidence(
                rows,
                errors,
                runtime_role="sqag_runtime",
                manifest=self.contract,
            )
            return tuple(errors)

        connection = self.connect()
        try:
            columns, rows = self._execute_contract_query_on(connection, "database_acl")
            self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["database_acl"])
            self.assertEqual(len(rows), 1)
            self.assertIs(rows[0]["datallowconn"], True)
            self.assertEqual(rows[0]["datconnlimit"], -1)

            target_database_sql(f"alter database {database_ident} with allow_connections false")
            try:
                self.assertIn("database_acl_datallowconn_forbidden", database_errors(connection))
            finally:
                target_database_sql(f"alter database {database_ident} with allow_connections true")
            self.assertEqual(self._evaluate_runtime_authority_rows(), ())

            for limit in (0, 1):
                target_database_sql(f"alter database {database_ident} with connection limit {limit}")
                try:
                    errors = database_errors(connection)
                    self.assertTrue(
                        any("database_acl_datconnlimit_policy_mismatch" in error for error in errors),
                        errors,
                    )
                finally:
                    target_database_sql(f"alter database {database_ident} with connection limit -1")
                self.assertEqual(self._evaluate_runtime_authority_rows(), ())

            _, restored_rows = self._execute_contract_query_on(connection, "database_acl")
            self.assertIs(restored_rows[0]["datallowconn"], True)
            self.assertEqual(restored_rows[0]["datconnlimit"], -1)
        finally:
            target_database_sql(f"alter database {database_ident} with allow_connections true connection limit -1")
            connection.rollback()
            connection.close()

        columns, restored_rows = self._execute_contract_query("database_acl")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["database_acl"])
        restored = copy.deepcopy(restored_rows[0])
        missing = copy.deepcopy(restored)
        missing.pop("datallowconn")
        malformed = copy.deepcopy(restored)
        malformed["datconnlimit"] = "unlimited"
        for candidate in (missing, malformed):
            errors: list[str] = []
            contract_validator._validate_database_acl_evidence(
                [candidate],
                errors,
                runtime_role="sqag_runtime",
                manifest=self.contract,
            )
            self.assertTrue(errors, candidate)

    def test_h55_relation_hierarchy_identity_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        suffix = uuid.uuid4().hex[:8]
        inheritance_child = f"sqag_h55_inheritance_child_{suffix}"
        partition_parent = f"sqag_h55_partition_parent_{suffix}"
        unrelated_parent = f"h55_unrelated_parent_{suffix}"
        unrelated_child = f"h55_unrelated_child_{suffix}"

        self._execute_admin_sql(
            f'create table public.{_quote_identifier(inheritance_child)} () '
            'inherits (public."sqag_profiles")'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "public_table_classified_public.sqag_profiles_inheritance_descendants_forbidden",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'drop table if exists public.{_quote_identifier(inheritance_child)}'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(
            f'create table public.{_quote_identifier(partition_parent)} '
            '(workspace_id text not null, profile_id text not null, payload_json text not null, '
            'created_at text not null, updated_at text not null) '
            'partition by range (workspace_id)'
        )
        try:
            self._execute_admin_sql(
                f'alter table public.{_quote_identifier(partition_parent)} attach partition '
                'public."sqag_profiles" for values from (minvalue) to (maxvalue)'
            )
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "public_table_classified_public.sqag_profiles_inheritance_parents_forbidden",
                errors,
            )
            self.assertIn(
                "public_table_classified_public.sqag_profiles_partition_forbidden",
                errors,
            )
            self.assertIn(
                "public_table_classified_public.sqag_profiles_partition_bound_forbidden",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'alter table public.{_quote_identifier(partition_parent)} detach partition '
                'public."sqag_profiles"'
            )
            self._execute_admin_sql(
                f'drop table if exists public.{_quote_identifier(partition_parent)}'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(
            f'create table public.{_quote_identifier(unrelated_parent)} (id integer) '
            'partition by range (id)'
        )
        self._execute_admin_sql(
            f'create table public.{_quote_identifier(unrelated_child)} partition of '
            f'public.{_quote_identifier(unrelated_parent)} for values from (0) to (100)'
        )
        try:
            self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        finally:
            self._execute_admin_sql(
                f'drop table if exists public.{_quote_identifier(unrelated_child)}'
            )
            self._execute_admin_sql(
                f'drop table if exists public.{_quote_identifier(unrelated_parent)}'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_target_relation_trigger_inventory_rejects_external_function_schema_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        schema_name = f"sqag_h39_{uuid.uuid4().hex[:8]}"
        function_name = f"guard_{uuid.uuid4().hex[:8]}"
        trigger_name = f"sqag_h39_trigger_{uuid.uuid4().hex[:8]}"
        self._execute_admin_sql(f"create schema {_quote_identifier(schema_name)}")
        self._execute_admin_sql(
            f"create function {_quote_identifier(schema_name)}.{_quote_identifier(function_name)}() "
            "returns trigger language plpgsql as $$ begin return new; end $$"
        )
        self._execute_admin_sql(
            f"create trigger {_quote_identifier(trigger_name)} before update "
            f"on public.{_quote_identifier('sqag_profiles')} for each row "
            f"execute function {_quote_identifier(schema_name)}.{_quote_identifier(function_name)}()"
        )
        self.addCleanup(
            self._cleanup_steps,
            [
                (
                    "drop_h39_schema",
                    f"drop schema {_quote_identifier(schema_name)} cascade",
                )
            ],
        )
        errors = self._evaluate_runtime_authority_rows()
        self.assertIn(
            "table_structural_trigger_binding_contract_mismatch_sqag_profiles",
            errors,
        )

    def test_unexpected_standalone_unique_index_is_rejected_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        index_name = f"sqag_h40_unique_{uuid.uuid4().hex[:8]}"
        self._execute_admin_sql(
            f"create unique index {_quote_identifier(index_name)} on public.{_quote_identifier('sqag_profiles')} "
            "(workspace_id)"
        )
        self.addCleanup(
            self._cleanup_steps,
            [
                (
                    "drop_h40_index",
                    f"drop index public.{_quote_identifier(index_name)}",
                )
            ],
        )
        errors = self._evaluate_runtime_authority_rows()
        self.assertIn(f"table_structural_unexpected_unique_index_{index_name}", errors)

    def test_not_valid_constraint_is_rejected_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        constraint_name = f"sqag_h41_not_valid_{uuid.uuid4().hex[:8]}"
        self._execute_admin_sql(
            f"alter table public.{_quote_identifier('sqag_profiles')} add constraint "
            f"{_quote_identifier(constraint_name)} check (workspace_id is not null) not valid"
        )
        self.addCleanup(
            self._cleanup_steps,
            [
                (
                    "drop_h41_constraint",
                    f"alter table public.{_quote_identifier('sqag_profiles')} drop constraint "
                    f"{_quote_identifier(constraint_name)}",
                )
            ],
        )
        errors = self._evaluate_runtime_authority_rows()
        self.assertIn(
            f"table_structural_constraint_not_valid_sqag_profiles_{constraint_name}",
            errors,
        )

    def test_check_constraint_cast_semantics_are_preserved_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        table_name = "sqag_quote_publication_artifacts"
        connection = self.connect()
        try:
            row = connection.execute(
                "select conname from pg_catalog.pg_constraint "
                "where conrelid = 'public.sqag_quote_publication_artifacts'::regclass "
                "and contype = 'c' and conname like '%%checksum%%' "
                "order by conname"
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()
        constraint_name = str(_row_dict(row, "conname")) if row is not None else ""
        self.assertTrue(constraint_name)
        constraint_ident = _quote_identifier(constraint_name)
        table_ident = _quote_identifier(table_name)
        baseline_expression = "length(checksum_sha256) = 64"

        def replace_check(expression: str) -> None:
            self._execute_admin_sql(
                f"alter table public.{table_ident} drop constraint if exists {constraint_ident}"
            )
            self._execute_admin_sql(
                f"alter table public.{table_ident} add constraint {constraint_ident} check ({expression})"
            )

        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        for expression in (
            "length(checksum_sha256::name) = 64",
            "length(CAST(checksum_sha256 AS name)) = 64",
            "length(checksum_sha256::char(64)) = 64",
        ):
            with self.subTest(h51_cast_expression=expression):
                replace_check(expression)
                try:
                    errors = self._evaluate_runtime_authority_rows()
                    self.assertIn(
                        f"table_structural_constraint_contract_mismatch_{table_name}",
                        errors,
                    )
                finally:
                    replace_check(baseline_expression)
                self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        replace_check("length ( checksum_sha256 ) = 64")
        try:
            self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        finally:
            replace_check(baseline_expression)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
    def test_retained_rollback_role_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        def role_errors(rows: list[dict[str, Any]] | None = None) -> tuple[str, ...]:
            if rows is None:
                columns, rows = self._execute_contract_query("role_attributes")
                self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["role_attributes"])
            errors: list[str] = []
            contract_validator._validate_role_attribute_evidence(rows, self.contract, errors)
            return tuple(errors)

        self.assertEqual(role_errors(), ())
        _, baseline_rows = self._execute_contract_query("role_attributes")
        omitted = [row for row in baseline_rows if row["rolname"] != "sqag_app"]
        omitted_errors = role_errors(omitted)
        self.assertTrue(any("role_attribute_required_evidence_missing" in error for error in omitted_errors), omitted_errors)

        self._execute_admin_sql('drop role "sqag_app"')
        try:
            _, dropped_rows = self._execute_contract_query("role_attributes")
            self.assertFalse(any(row["rolname"] == "sqag_app" for row in dropped_rows))
            dropped_errors = role_errors(dropped_rows)
            self.assertTrue(any("role_attribute_required_evidence_missing" in error for error in dropped_errors), dropped_errors)
        finally:
            self._execute_admin_sql(
                'create role "sqag_app" LOGIN NOSUPERUSER NOCREATEDB '
                'NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT CONNECTION LIMIT -1'
            )
        self.assertEqual(role_errors(), ())

        self._execute_admin_sql('alter role "sqag_app" nologin')
        try:
            errors = role_errors()
            self.assertTrue(any("rolcanlogin_mismatch" in error for error in errors), errors)
        finally:
            self._execute_admin_sql('alter role "sqag_app" login')
        self.assertEqual(role_errors(), ())

        elevated_controls = (
            ("superuser", "nosuperuser", "rolsuper"),
            ("createdb", "nocreatedb", "rolcreatedb"),
            ("createrole", "nocreaterole", "rolcreaterole"),
            ("replication", "noreplication", "rolreplication"),
            ("bypassrls", "nobypassrls", "rolbypassrls"),
        )
        for enable_clause, restore_clause, field in elevated_controls:
            with self.subTest(h52_elevated_attribute=enable_clause):
                self._execute_admin_sql(f'alter role "sqag_app" {enable_clause}')
                try:
                    errors = role_errors()
                    self.assertTrue(any(field + "_mismatch" in error for error in errors), errors)
                    self.assertTrue(any(field + "_privileged_forbidden" in error for error in errors), errors)
                finally:
                    self._execute_admin_sql(f'alter role "sqag_app" {restore_clause}')
                self.assertEqual(role_errors(), ())

        future_retirement = copy.deepcopy(self.contract)
        future_retirement["roles"]["legacy"]["status"] = "retired"
        errors: list[str] = []
        contract_validator._validate_role_attribute_evidence(omitted, future_retirement, errors)
        self.assertIn("legacy_role_status_not_explicitly_retained", errors)
        self.assertTrue(any("role_attribute_required_evidence_missing" in error for error in errors), errors)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
    def test_public_default_privilege_is_rejected_even_without_grant_option_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        owner_name = self._new_role("h42_default_owner")
        self._register_default_acl_audit({owner_name})
        self._alter_default_privilege(owner_name, "PUBLIC", "SELECT", "TABLES")
        errors = self._evaluate_runtime_authority_rows()
        self.assertIn(
            "default_acl_evidence_row_0_public_default_privilege_forbidden_r_SELECT",
            errors,
        )

    def test_routine_body_cast_mutation_is_rejected_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        mutated_sql = (
            'create or replace function public."sqag_reject_immutable_change"() '
            "returns trigger language plpgsql as $$ begin perform current_timestamp::text; "
            "raise exception 'SQAG immutable record cannot be changed'; end $$"
        )
        restore_sql = (
            'create or replace function public."sqag_reject_immutable_change"() '
            "returns trigger language plpgsql as $$ begin raise exception "
            "'SQAG immutable record cannot be changed'; end $$"
        )
        self._execute_admin_sql(mutated_sql)
        self.addCleanup(self._execute_admin_sql, restore_sql)
        errors = self._evaluate_runtime_authority_rows()
        self.assertIn(
            "routine_structural_definition_mismatch_sqag_reject_immutable_change",
            errors,
        )

    def test_public_connect_acl_is_required_in_addition_to_direct_runtime_connect_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self._alter_public_database_privilege("CONNECT", False)
        errors = self._evaluate_runtime_authority_rows()
        self.assertIn("database_acl_public_connect_evidence_missing_or_duplicate", errors)
    def test_database_migrator_and_system_relation_acl_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        database_ident = _quote_identifier(self.database_name)

        for privilege in ("CONNECT", "CREATE", "TEMPORARY"):
            with self.subTest(h48_missing_privilege=privilege):
                self._execute_admin_sql(
                    f'revoke {privilege} on database {database_ident} from "sqag_migrator"'
                )
                try:
                    errors = self._evaluate_runtime_authority_rows()
                    self.assertIn(
                        f"database_acl_migrator_{privilege.lower()}_direct_evidence_missing_or_duplicate",
                        errors,
                    )
                finally:
                    self._execute_admin_sql(
                        f'grant {privilege} on database {database_ident} to "sqag_migrator"'
                    )
                self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        wrong_grantee = self._new_role("h48_wrong_grantee")
        self._execute_admin_sql(
            f'revoke CREATE on database {database_ident} from "sqag_migrator"'
        )
        self._execute_admin_sql(
            f'grant CREATE on database {database_ident} to {_quote_identifier(wrong_grantee)}'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "database_acl_migrator_create_direct_evidence_missing_or_duplicate",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'revoke CREATE on database {database_ident} from {_quote_identifier(wrong_grantee)}'
            )
            self._execute_admin_sql(
                f'grant CREATE on database {database_ident} to "sqag_migrator"'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        wrong_grantor = self._new_role("h48_wrong_grantor")
        self._execute_admin_sql(
            f'grant CONNECT on database {database_ident} to '
            f'{_quote_identifier(wrong_grantor)} with grant option'
        )
        with self.as_role(wrong_grantor) as connection:
            connection.execute(
                f'grant CONNECT on database {database_ident} to "sqag_migrator"'
            )
            connection.commit()
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(
                any(
                    error.startswith("database_acl_migrator_connect_grantor_invalid_expected_")
                    or error == "database_acl_migrator_connect_direct_evidence_missing_or_duplicate"
                    for error in errors
                ),
                errors,
            )
        finally:
            with self.as_role(wrong_grantor) as connection:
                connection.execute(
                    f'revoke CONNECT on database {database_ident} from "sqag_migrator"'
                )
                connection.commit()
            self._execute_admin_sql(
                f'revoke CONNECT on database {database_ident} from {_quote_identifier(wrong_grantor)}'
            )
            self._execute_admin_sql(
                f'grant CONNECT on database {database_ident} to "sqag_migrator"'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(
            f'grant TEMPORARY on database {database_ident} to "sqag_migrator" with grant option'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "database_acl_migrator_temporary_grant_option_forbidden",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'revoke TEMPORARY on database {database_ident} from "sqag_migrator"'
            )
            self._execute_admin_sql(
                f'grant TEMPORARY on database {database_ident} to "sqag_migrator"'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        columns, system_rows = self._execute_contract_query("system_relation_acl")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["system_relation_acl"])
        candidate = next(
            (
                row
                for row in system_rows
                if row["schema_name"] == "pg_catalog"
                and row["relation_kind"] in {"r", "p"}
                and row["relation_name"] not in {"pg_authid", "pg_shadow", "pg_roles", "pg_user"}
                and not any(
                    entry["grantee"] in {"PUBLIC", "sqag_runtime"}
                    and entry["privilege_type"] == "SELECT"
                    for entry in row["initial_acl_entries"]
                )
                and not any(
                    entry["grantee"] in {"PUBLIC", "sqag_runtime"}
                    and entry["privilege_type"] == "SELECT"
                    for entry in row["current_acl_entries"]
                )
            ),
            None,
        )
        self.assertIsNotNone(candidate, "no suitable PostgreSQL 17 system relation baseline fixture")
        relation_name = str(candidate["relation_name"])
        relation_ident = f'pg_catalog.{_quote_identifier(relation_name)}'

        self._execute_admin_sql(
            f'grant SELECT on table {relation_ident} to "sqag_runtime"'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                f"system_relation_acl_exceptional_sqag_runtime_select_pg_catalog.{relation_name}",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'revoke SELECT on table {relation_ident} from "sqag_runtime"'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(f"grant SELECT on table {relation_ident} to public")
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                f"system_relation_acl_exceptional_PUBLIC_select_pg_catalog.{relation_name}",
                errors,
            )
        finally:
            self._execute_admin_sql(f"revoke SELECT on table {relation_ident} from public")
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        grant_option_applied = True
        try:
            self._execute_admin_sql(
                f"grant SELECT on table {relation_ident} to public with grant option"
            )
        except self.psycopg.errors.InvalidGrantOperation:
            grant_option_applied = False
        if grant_option_applied:
            try:
                errors = self._evaluate_runtime_authority_rows()
                self.assertIn(
                    f"system_relation_acl_grant_option_forbidden_PUBLIC_select_pg_catalog.{relation_name}",
                    errors,
                )
            finally:
                self._execute_admin_sql(f"revoke SELECT on table {relation_ident} from public")
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

    def test_runtime_role_attributes_memberships_and_ownership_are_exact(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("attributes")
        connection = self.connect()
        try:
            row = connection.execute(
                "select rolcanlogin, rolpassword is null as password_is_null, rolsuper, rolcreatedb, "
                "rolcreaterole, rolreplication, rolbypassrls, rolinherit, rolconnlimit "
                "from pg_catalog.pg_authid where rolname = %s",
                (role_name,),
            ).fetchone()
            memberships = connection.execute(
                "select count(*) as count from pg_catalog.pg_auth_members am "
                "join pg_catalog.pg_roles r on r.oid = am.member where r.rolname = %s",
                (role_name,),
            ).fetchone()
            ownership = connection.execute(
                "select count(*) as count from pg_catalog.pg_class c "
                "join pg_catalog.pg_roles r on r.oid = c.relowner where r.rolname = %s",
                (role_name,),
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()
        self.assertIs(_row_dict(row, "rolcanlogin"), False)
        self.assertIs(_row_dict(row, "password_is_null"), True)
        self.assertIs(_row_dict(row, "rolsuper"), False)
        self.assertIs(_row_dict(row, "rolcreatedb"), False)
        self.assertIs(_row_dict(row, "rolcreaterole"), False)
        self.assertIs(_row_dict(row, "rolreplication"), False)
        self.assertIs(_row_dict(row, "rolbypassrls"), False)
        self.assertIs(_row_dict(row, "rolinherit"), True)
        self.assertEqual(_row_dict(row, "rolconnlimit"), -1)
        self.assertEqual(_row_dict(memberships, "count"), 0)
        self.assertEqual(_row_dict(ownership, "count"), 0)

    def _assert_canonical_runtime_password_is_null(self) -> None:
        columns, rows = self._execute_contract_query("role_attributes")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["role_attributes"])
        runtime_rows = [row for row in rows if row["rolname"] == "sqag_runtime"]
        self.assertEqual(len(runtime_rows), 1)
        self.assertIs(runtime_rows[0]["password_is_null"], True)
        self.assertNotIn("rolpassword", runtime_rows[0])

    def test_canonical_role_attribute_password_state_round_trip(self) -> None:
        self.apply_migrations()
        self._assert_canonical_runtime_password_is_null()
        synthetic_password = "sqag-run22-" + uuid.uuid4().hex
        try:
            self._execute_admin_sql(
                f"alter role {_quote_identifier('sqag_runtime')} password '{synthetic_password}'"
            )
            columns, rows = self._execute_contract_query("role_attributes")
            self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["role_attributes"])
            runtime_row = next(row for row in rows if row["rolname"] == "sqag_runtime")
            self.assertIs(runtime_row["password_is_null"], False)
            with self.assertRaises(AssertionError):
                self._assert_canonical_runtime_password_is_null()
        finally:
            self._execute_admin_sql(f"alter role {_quote_identifier('sqag_runtime')} password null")
        self._assert_canonical_runtime_password_is_null()

    def test_runtime_like_role_cannot_read_pg_authid_password_catalog(self) -> None:
        self.apply_migrations()
        with self.as_role("sqag_runtime") as runtime_connection:
            with self.assertRaises(Exception) as failure:
                runtime_connection.execute(
                    "select a.rolpassword from pg_catalog.pg_authid a "
                    "where a.rolname = 'sqag_runtime'"
                ).fetchall()
            runtime_connection.rollback()
            self.assertEqual(getattr(failure.exception, "sqlstate", None), "42501")

    def test_trigger_enforcement_runs_under_runtime_authority_after_public_revoke(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("trigger")
        self._grant_database_privilege(role_name, "CONNECT")
        self._grant_schema_privilege(role_name, "USAGE")
        self._grant_table_privilege(role_name, "sqag_feedback", "SELECT")
        self._grant_table_privilege(role_name, "sqag_feedback", "UPDATE")
        self._revoke_public_execute("sqag_reject_immutable_change")
        self._revoke_public_execute("sqag_require_retention_delete_authorization")
        connection = self.connect()
        try:
            connection.execute(
                "insert into sqag_feedback "
                "(feedback_id, support_reference, workspace_id, reporter_tracking_id, reporter_key_version, "
                "category, title, message, link_choice, manual_reference_status, diagnostic_metadata_json, "
                "status, created_at, updated_at, retention_expires_at, original_retention_expires_at, "
                "submission_retention_expires_at, retention_policy_version) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    "feedback-runtime-trigger", "support-runtime-trigger", "workspace-runtime-trigger",
                    "reporter", "v1", "bug", "title", "message", "none", "not_applicable", "{}",
                    "received", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
                    "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z", "v1",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        with self.as_role(role_name) as runtime_connection:
            try:
                runtime_connection.execute(
                    "update sqag_feedback set run_id = %s where feedback_id = %s",
                    ("changed-by-runtime", "feedback-runtime-trigger"),
                )
                runtime_connection.commit()
                self.fail("installed immutable trigger did not reject the runtime update")
            except Exception as exc:
                runtime_connection.rollback()
                self.assertEqual(getattr(exc, "sqlstate", None), "P0001")
                self.assertIn("SQAG immutable record cannot be changed", str(exc))
        connection = self.connect()
        try:
            row = connection.execute(
                "select run_id from sqag_feedback where feedback_id = %s",
                ("feedback-runtime-trigger",),
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()
        self.assertIsNone(_row_dict(row, "run_id"))

    def test_direct_runtime_calls_to_both_trigger_functions_are_denied_42501(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("direct")
        self._grant_database_privilege(role_name, "CONNECT")
        self._grant_schema_privilege(role_name, "USAGE")
        self._revoke_public_execute("sqag_reject_immutable_change")
        self._revoke_public_execute("sqag_require_retention_delete_authorization")
        for function_name in ("sqag_reject_immutable_change", "sqag_require_retention_delete_authorization"):
            with self.as_role(role_name) as runtime_connection:
                try:
                    runtime_connection.execute(f"select public.{_quote_identifier(function_name)}()")
                    runtime_connection.rollback()
                    self.fail(f"runtime direct call unexpectedly succeeded: {function_name}")
                except Exception as exc:
                    runtime_connection.rollback()
                    self.assertEqual(getattr(exc, "sqlstate", None), "42501")

    def test_effective_runtime_table_privileges_match_manifest_exactly(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("matrix")
        self._grant_database_privilege(role_name, "CONNECT")
        self._grant_schema_privilege(role_name, "USAGE")
        for table_name, entry in self.contract["tables"]["runtime_accessible"].items():
            for privilege, allowed in entry["privileges"].items():
                if allowed:
                    self._grant_table_privilege(role_name, table_name, privilege.upper())
        for table_name, grants in EXPLICIT_COLUMN_PRIVILEGES.items():
            for privilege, column_names in grants.items():
                for column_name in column_names:
                    self._grant_column_privilege(role_name, table_name, column_name, privilege)
        self._alter_public_database_privilege("TEMPORARY", False)
        self._assert_exact_runtime_matrix(role_name)
        self._assert_exact_runtime_column_matrix(role_name)
        self._assert_exact_runtime_database_privileges(role_name)
        self._assert_exact_runtime_schema_privileges(role_name)
        for table_name in FORBIDDEN_TABLES:
            self.assertFalse(("public", table_name, "SELECT", False) in self._effective_table_grants(role_name))

    def test_publication_backfill_actual_application_path_uses_only_checksum_update(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        workspace_id = "workspace-run22-publication"
        session_id = "quote-run22-session"
        old_run_id = "run-run22-old"
        new_run_id = "run-run22-new"
        old_job_id = "job-run22-old"
        new_job_id = "job-run22-new"
        timestamp = "2026-07-31T00:00:00Z"
        legacy_content = b"run22 legacy quotation content"
        staged_content = b"run22 staged quotation content"
        legacy_digest = hashlib.sha256(legacy_content).hexdigest()
        staged_digest = hashlib.sha256(staged_content).hexdigest()
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        legacy_table = "run22_legacy_quote_artifacts"
        compatibility_schema = "run22_app_compat"
        target_table = "sqag_quote_publication_artifacts"

        current_metadata = {
            "schema_version": 1,
            "session_id": session_id,
            "created_at": timestamp,
            "updated_at": timestamp,
            "exports": {
                "xlsx": {
                    "filename": "quotation.xlsx",
                    "created_at": timestamp,
                    "sha256": staged_digest,
                    "size_bytes": len(staged_content),
                    "stale": False,
                },
                "pdf": {},
            },
            "status": {"quote_generated": False, "xlsx_exported": True, "pdf_exported": False},
            "publication": {
                "state": "staged",
                "run_id": old_run_id,
                "job_id": old_job_id,
                "pending_run_id": new_run_id,
                "pending_job_id": new_job_id,
            },
        }
        staged_metadata = copy.deepcopy(current_metadata)
        staged_metadata["publication"] = {
            "state": "staged",
            "run_id": new_run_id,
            "job_id": new_job_id,
        }
        current_metadata_json = json.dumps(current_metadata, ensure_ascii=True, sort_keys=True)
        staged_metadata_json = json.dumps(staged_metadata, ensure_ascii=True, sort_keys=True)

        self.addCleanup(
            self._cleanup_steps,
            [
                (
                    "drop_run22_compatibility_schema",
                    f"drop schema if exists {_quote_identifier(compatibility_schema)} cascade",
                ),
                (
                    "drop_run22_legacy_quote_view",
                    "drop view if exists public.sqag_quote_artifacts",
                ),
                (
                    "drop_run22_legacy_quote_table",
                    f"drop table if exists public.{_quote_identifier(legacy_table)}",
                ),
            ],
        )
        connection = self.connect()
        try:
            connection.execute("set role \"sqag_migrator\"")
            connection.execute(
                f"create table public.{_quote_identifier(legacy_table)} ("
                "workspace_id text not null, session_id text not null, artifact_kind text not null, "
                "filename text not null, content_type text not null, size_bytes bigint not null, "
                "content_blob bytea not null, created_at text not null, updated_at text not null)"
            )
            connection.execute(
                "create view public.sqag_quote_artifacts as "
                f"select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, "
                f"content_blob, created_at, updated_at from public.{_quote_identifier(legacy_table)}"
            )
            connection.execute(
                f"create schema {_quote_identifier(compatibility_schema)}"
            )
            connection.execute(
                f"create function {_quote_identifier(compatibility_schema)}.randomblob(integer) "
                "returns bytea language sql immutable as $$ select decode(repeat('00', $1), 'hex') $$"
            )
            connection.execute(
                f"create function {_quote_identifier(compatibility_schema)}.hex(bytea) "
                "returns text language sql immutable as $$ select encode($1, 'hex') $$"
            )
            connection.execute(
                f"grant usage on schema {_quote_identifier(compatibility_schema)} to \"sqag_runtime\""
            )
            connection.execute(
                f"insert into public.{_quote_identifier(legacy_table)} "
                "(workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, content_blob, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id, session_id, "xlsx", "quotation.xlsx", content_type,
                    len(legacy_content), legacy_content, timestamp, timestamp,
                ),
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()

        self._grant_manifest_view_authority("sqag_runtime")

        connection = self.connect()
        try:
            connection.execute(
                "insert into sqag_quote_sessions "
                "(workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (workspace_id, session_id, current_metadata_json, "[]", timestamp, timestamp),
            )
            connection.execute(
                "insert into sqag_quote_publication_versions "
                "(workspace_id, session_id, run_id, job_id, state, artifact_storage_mode, artifact_source, "
                "metadata_json, created_at, updated_at, promoted_at, retention_expires_at, "
                "original_retention_expires_at, legal_hold, deletion_state) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id, session_id, new_run_id, new_job_id, "staged", "database", "version",
                    staged_metadata_json, timestamp, timestamp, None, "2099-01-01T00:00:00Z",
                    "2099-01-01T00:00:00Z", 0, "active",
                ),
            )
            connection.execute(
                "insert into sqag_quote_publication_artifacts "
                "(workspace_id, session_id, run_id, artifact_kind, filename, content_type, size_bytes, "
                "checksum_sha256, content_blob, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    workspace_id, session_id, new_run_id, "xlsx", "quotation.xlsx", content_type,
                    len(staged_content), staged_digest, staged_content, timestamp, timestamp,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        storage = DatabaseSqagStorage.__new__(DatabaseSqagStorage)
        storage.workspace_id = workspace_id
        expected_files = [{"name": "quotation.xlsx", "bytes": len(staged_content), "sha256": staged_digest}]
        with self.as_role("sqag_runtime") as runtime_connection:
            runtime_connection.execute(
                f"set local search_path = {_quote_identifier(compatibility_schema)}, public"
            )
            with mock.patch("webapp.server.configured_artifact_storage_mode", return_value="database"):
                storage.publish_quote_session_forensic_transaction(
                    runtime_connection, session_id, new_run_id, expected_files
                )
            runtime_connection.commit()

        connection = self.connect()
        try:
            row = connection.execute(
                "select workspace_id, session_id, run_id, artifact_kind, filename, content_type, size_bytes, "
                "checksum_sha256, content_blob, created_at, updated_at "
                "from sqag_quote_publication_artifacts where workspace_id = ? and run_id = ?",
                (workspace_id, old_run_id),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["checksum_sha256"], legacy_digest)
            self.assertEqual(bytes(row["content_blob"]), legacy_content)
            self.assertEqual(row["workspace_id"], workspace_id)
            self.assertEqual(row["session_id"], session_id)
            self.assertEqual(row["run_id"], old_run_id)
            self.assertEqual(row["artifact_kind"], "xlsx")
            self.assertEqual(row["filename"], "quotation.xlsx")
            self.assertEqual(row["content_type"], content_type)
            self.assertEqual(int(row["size_bytes"]), len(legacy_content))
            self.assertEqual(row["created_at"], timestamp)
            self.assertEqual(row["updated_at"], timestamp)
            authority = connection.execute(
                "select has_table_privilege('sqag_runtime', 'public.sqag_quote_publication_artifacts', 'UPDATE') as table_update, "
                "has_table_privilege('sqag_runtime', 'public.sqag_quote_publication_artifacts', 'UPDATE WITH GRANT OPTION') as table_grantable, "
                "exists (select 1 from pg_catalog.aclexplode(coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))) expanded "
                "where expanded.grantee = (select oid from pg_catalog.pg_roles where rolname = 'sqag_runtime') "
                "and expanded.privilege_type = 'UPDATE') as direct_table_update "
                "from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "where n.nspname = 'public' and c.relname = 'sqag_quote_publication_artifacts'"
            ).fetchone()
            self.assertIs(_row_dict(authority, "table_update"), False)
            self.assertIs(_row_dict(authority, "table_grantable"), False)
            self.assertIs(_row_dict(authority, "direct_table_update"), False)
        finally:
            connection.rollback()
            connection.close()

        publication_columns = self._user_columns()[target_table]
        self.assertIn("checksum_sha256", publication_columns)
        self.assertTrue(
            self._has_column_privilege(
                "sqag_runtime", target_table, "checksum_sha256", "UPDATE"
            )
        )
        self.assertFalse(
            self._has_column_privilege(
                "sqag_runtime", target_table, "checksum_sha256", "UPDATE WITH GRANT OPTION"
            )
        )
        self.assertFalse(
            self._has_column_privilege("public", target_table, "checksum_sha256", "UPDATE")
        )
        for column_name in publication_columns:
            if column_name == "checksum_sha256":
                continue
            self.assertFalse(
                self._has_column_privilege("sqag_runtime", target_table, column_name, "UPDATE"),
                column_name,
            )
            with self.as_role("sqag_runtime") as runtime_connection:
                try:
                    runtime_connection.execute(
                        f"update {_quote_identifier(target_table)} set {_quote_identifier(column_name)} = "
                        f"{_quote_identifier(column_name)} where false"
                    )
                    runtime_connection.commit()
                except Exception as exc:
                    runtime_connection.rollback()
                    self.assertEqual(getattr(exc, "sqlstate", None), "42501", column_name)
                else:
                    self.fail(f"non-checksum UPDATE unexpectedly succeeded: {column_name}")

        columns, rows = self._execute_contract_query("effective_runtime_column_privileges")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["effective_runtime_column_privileges"])
        checksum_rows = [
            row for row in rows
            if row["table_name"] == target_table
            and row["column_name"] == "checksum_sha256"
            and row["privilege_type"] == "UPDATE"
        ]
        self.assertEqual(len(checksum_rows), 1)
        self.assertIs(checksum_rows[0]["effective"], True)
        self.assertIs(checksum_rows[0]["is_grantable"], False)

        self._execute_admin_sql(
            f"grant UPDATE on table {_quote_identifier(target_table)} to public"
        )
        try:
            self.assertTrue(self._has_table_privilege("sqag_runtime", target_table, "UPDATE"))
            with self.assertRaises(AssertionError):
                self._assert_exact_runtime_matrix("sqag_runtime")
        finally:
            self._execute_admin_sql(
                f"revoke UPDATE on table {_quote_identifier(target_table)} from public"
            )

        parent_name = self._new_role("publication_update_parent")
        self._grant_role_membership(parent_name, "sqag_runtime")
        self._grant_table_privilege(parent_name, target_table, "UPDATE")
        self.assertTrue(self._has_table_privilege("sqag_runtime", target_table, "UPDATE"))
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_matrix("sqag_runtime")
        self._assert_exact_runtime_view_grants("sqag_runtime")

    def test_owner_acl_completeness_rejects_each_missing_privilege_postgres(self) -> None:
        self.apply_migrations()
        self._create_legacy_quote_artifacts_view()
        self._grant_manifest_view_authority('sqag_runtime')
        for privilege in TABLE_PRIVILEGES:
            self._execute_admin_sql(
                f'revoke {privilege} on table public.sqag_quote_artifacts from {_quote_identifier("sqag_migrator")}'
            )
            try:
                errors = self._evaluate_view_authority_rows()
                self.assertTrue(any('owner_acl_completeness' in error for error in errors), (privilege, errors))
            finally:
                self._execute_admin_sql(
                    f'grant {privilege} on table public.sqag_quote_artifacts to {_quote_identifier("sqag_migrator")}'
                )
        self.assertEqual(self._evaluate_view_authority_rows(), ())

    def test_column_acl_authority_and_grant_options_fail_closed_postgres(self) -> None:
        self.apply_migrations()
        self._create_legacy_quote_artifacts_view()
        self._grant_manifest_view_authority('sqag_runtime')
        column = 'workspace_id'
        cases = (
            ('sqag_runtime', False, 'column_acl_runtime_authority_forbidden'),
            ('sqag_runtime', True, 'column_acl_runtime_grant_option_forbidden'),
            ('public', False, 'column_acl_public_authority_forbidden'),
        )
        for grantee, with_grant_option, expected in cases:
            target = _quote_identifier(grantee) if grantee != 'public' else 'public'
            grant = f'grant SELECT ({_quote_identifier(column)}) on table public.sqag_quote_artifacts to {target}'
            if with_grant_option:
                grant += ' with grant option'
            self._execute_admin_sql(grant)
            try:
                errors = self._evaluate_view_authority_rows()
                self.assertTrue(any(expected in error for error in errors), (grantee, with_grant_option, errors))
            finally:
                self._execute_admin_sql(
                    f'revoke SELECT ({_quote_identifier(column)}) on table public.sqag_quote_artifacts from {target}'
                )
        self.assertEqual(self._evaluate_view_authority_rows(), ())

    def test_materialized_view_column_authority_fails_closed_postgres(self) -> None:
        self.apply_migrations()
        view_name = 'sqag_quote_matview_columns'
        self._create_materialized_view(view_name)
        target = _quote_identifier('sqag_runtime')
        self._execute_admin_sql(
            f'grant SELECT (marker) on table public.{_quote_identifier(view_name)} to {target}'
        )
        try:
            errors = self._evaluate_view_authority_rows()
            self.assertTrue(any('materialized_view_column_authority_forbidden' in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                f'revoke SELECT (marker) on table public.{_quote_identifier(view_name)} from {target}'
            )

    def test_classified_view_definition_dependency_and_option_binding_fail_closed_postgres(self) -> None:
        self.apply_migrations()
        self._create_legacy_quote_artifacts_view()
        self._grant_manifest_view_authority('sqag_runtime')
        self._execute_admin_sql(
            'create or replace view public.sqag_quote_artifacts as '
            'select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, '
            'content_blob, created_at, updated_at from public.legacy_quote_artifacts_source '
            'where workspace_id is not null'
        )
        try:
            errors = self._evaluate_view_authority_rows()
            self.assertTrue(any('classified_view_definition_mismatch' in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                'create or replace view public.sqag_quote_artifacts as '
                'select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, '
                'content_blob, created_at, updated_at from public.legacy_quote_artifacts_source'
            )

        self._execute_admin_sql(
            'create table public.legacy_quote_artifacts_substitute '
            '(workspace_id text not null, session_id text not null, artifact_kind text not null, '
            'filename text not null, content_type text not null, size_bytes bigint not null, '
            'content_blob bytea not null, created_at text not null, updated_at text not null)'
        )
        self.addCleanup(
            self._cleanup_steps,
            [('drop_run73_legacy_substitute', 'drop table if exists public.legacy_quote_artifacts_substitute')],
        )
        self._execute_admin_sql(
            'create or replace view public.sqag_quote_artifacts as '
            'select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, '
            'content_blob, created_at, updated_at from public.legacy_quote_artifacts_substitute'
        )
        try:
            errors = self._evaluate_view_authority_rows()
            self.assertTrue(any('classified_view_dependencies_mismatch' in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                'create or replace view public.sqag_quote_artifacts as '
                'select workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, '
                'content_blob, created_at, updated_at from public.legacy_quote_artifacts_source'
            )

        self._execute_admin_sql(
            'alter view public.sqag_quote_artifacts set (security_barrier=true)'
        )
        try:
            errors = self._evaluate_view_authority_rows()
            self.assertTrue(any('classified_view_options_mismatch' in error for error in errors), errors)
            self.assertTrue(any('classified_view_security_mismatch' in error for error in errors), errors)
        finally:
            self._execute_admin_sql('alter view public.sqag_quote_artifacts reset (security_barrier)')
        self.assertEqual(self._evaluate_view_authority_rows(), ())

    def test_legacy_view_runtime_read_is_prescribed_and_verified(self) -> None:
        self.apply_migrations()
        self._create_legacy_quote_artifacts_view()
        self._grant_manifest_view_authority("sqag_runtime")
        self._assert_exact_runtime_view_grants("sqag_runtime")
        self.assertTrue(self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT"))
        self.assertFalse(
            self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT WITH GRANT OPTION")
        )
        with self.as_role("sqag_runtime") as runtime_connection:
            runtime_connection.execute("select * from public.sqag_quote_artifacts limit 0")
            runtime_connection.commit()
        columns, rows = self._execute_contract_query("view_acl")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["view_acl"])
        self.assertEqual(
            {str(row["relation_name"]) for row in rows},
            {"sqag_quote_artifacts"},
        )
        self.assertEqual({str(row["relation_kind"]) for row in rows}, {"v"})
        self.assertTrue(all(bool(row["runtime_select"]) for row in rows))
        self.assertFalse(any(bool(row["runtime_select_grantable"]) for row in rows))
        self.assertEqual(
            contract_validator.evaluate_view_authority(self.contract, [dict(row) for row in rows]),
            (),
        )
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            self.assertFalse(self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", privilege))
        self._execute_admin_sql("grant select on table public.sqag_quote_artifacts to public")
        try:
            self.assertTrue(self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT"))
            with self.assertRaises(AssertionError):
                self._assert_exact_runtime_view_grants("sqag_runtime")
        finally:
            self._execute_admin_sql("revoke select on table public.sqag_quote_artifacts from public")
        self._assert_exact_runtime_view_grants("sqag_runtime")

        membership_parent = self._new_role("legacy_view_parent")
        self._grant_role_membership(membership_parent, "sqag_runtime")
        self._grant_view_privilege(membership_parent, "sqag_quote_artifacts", "SELECT")
        self.assertTrue(self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT"))
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_view_grants("sqag_runtime")

    def test_legacy_view_revocation_restores_expected_failure(self) -> None:
        self.apply_migrations()
        self._create_legacy_quote_artifacts_view()
        self._grant_manifest_view_authority("sqag_runtime")
        with self.as_role("sqag_runtime") as runtime_connection:
            runtime_connection.execute("select * from public.sqag_quote_artifacts limit 0")
            runtime_connection.commit()
        self._execute_admin_sql("revoke select on table public.sqag_quote_artifacts from \"sqag_runtime\"")
        with self.as_role("sqag_runtime") as runtime_connection:
            try:
                runtime_connection.execute("select * from public.sqag_quote_artifacts limit 0")
                runtime_connection.commit()
            except Exception as exc:
                runtime_connection.rollback()
                self.assertEqual(getattr(exc, "sqlstate", None), "42501")
            else:
                self.fail("view read unexpectedly succeeded after revoke")

    def test_unrelated_legacy_view_authority_is_denied(self) -> None:
        self.apply_migrations()
        self._create_legacy_quote_artifacts_view()
        unrelated = "sqag_file_artifacts"
        self.addCleanup(
            self._cleanup_steps,
            [("drop_run55_unrelated_view", f"drop view if exists public.{_quote_identifier(unrelated)}")],
        )
        connection = self.connect()
        try:
            connection.execute("set role \"sqag_migrator\"")
            connection.execute(f"create view public.{_quote_identifier(unrelated)} as select 1 as marker")
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        self.assertFalse(self._has_table_privilege("sqag_runtime", unrelated, "SELECT"))
        self._grant_view_privilege("sqag_runtime", unrelated, "SELECT")
        self.assertTrue(self._has_table_privilege("sqag_runtime", unrelated, "SELECT"))
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_view_grants("sqag_runtime")

    def test_legacy_view_write_authority_is_rejected(self) -> None:
        self.apply_migrations()
        self._create_legacy_quote_artifacts_view()
        self._grant_manifest_view_authority("sqag_runtime")
        self._assert_exact_runtime_view_grants("sqag_runtime")
        self._grant_view_privilege("sqag_runtime", "sqag_quote_artifacts", "INSERT")
        self.assertTrue(self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", "INSERT"))
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_view_grants("sqag_runtime")
        self._revoke_view_privilege("sqag_runtime", "sqag_quote_artifacts", "INSERT")
        self._grant_view_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT", with_grant_option=True)
        self.assertTrue(
            self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT WITH GRANT OPTION")
        )
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_view_grants("sqag_runtime")

    def test_fresh_migration_database_without_legacy_view_is_valid(self) -> None:
        self.apply_migrations()
        self.assertEqual(self._relation_kind("sqag_quote_artifacts"), "")
        columns, rows = self._execute_contract_query("view_acl")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["view_acl"])
        self.assertEqual(rows, [])
        self.assertEqual(
            contract_validator.evaluate_view_authority(self.contract, [dict(row) for row in rows]),
            (),
        )

    def test_materialized_view_runtime_select_escapes_old_relation_proof(self) -> None:
        self.apply_migrations()
        self._create_materialized_view("sqag_quote_matview")
        self._grant_view_privilege("sqag_runtime", "sqag_quote_matview", "SELECT")
        self.assertTrue(self._has_table_privilege("sqag_runtime", "sqag_quote_matview", "SELECT"))
        self.assertNotEqual(
            self._evaluate_view_authority_rows(),
            (),
            "materialized-view runtime SELECT must be rejected",
        )

    def test_materialized_view_runtime_ownership_is_rejected(self) -> None:
        self.apply_migrations()
        self._create_materialized_view("sqag_quote_owned_matview")
        self._execute_admin_sql("alter materialized view public.sqag_quote_owned_matview owner to \"sqag_runtime\"")
        self.assertEqual(self._relation_kind("sqag_quote_owned_matview"), "m")
        self.assertNotEqual(
            self._evaluate_view_authority_rows(),
            (),
            "materialized-view runtime ownership must be rejected",
        )

    def test_materialized_view_runtime_grant_option_is_rejected(self) -> None:
        self.apply_migrations()
        self._create_materialized_view("sqag_quote_grantable_matview")
        self._grant_view_privilege("sqag_runtime", "sqag_quote_grantable_matview", "SELECT", with_grant_option=True)
        self.assertTrue(
            self._has_table_privilege(
                "sqag_runtime", "sqag_quote_grantable_matview", "SELECT WITH GRANT OPTION"
            )
        )
        self.assertNotEqual(
            self._evaluate_view_authority_rows(),
            (),
            "materialized-view runtime grant option must be rejected",
        )

    def test_legacy_relation_as_materialized_view_is_rejected(self) -> None:
        self.apply_migrations()
        self._create_materialized_view("sqag_quote_artifacts")
        self._grant_view_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT")
        self.assertTrue(self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT"))
        self.assertNotEqual(
            self._evaluate_view_authority_rows(),
            (),
            "legacy relation as materialized view must be rejected",
        )

    def test_legacy_view_grant_option_and_ownership_drift_are_rejected(self) -> None:
        self.apply_migrations()
        self._create_legacy_quote_artifacts_view()
        self._grant_view_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT", with_grant_option=True)
        self.assertTrue(
            self._has_table_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT WITH GRANT OPTION")
        )
        self.assertNotEqual(
            self._evaluate_view_authority_rows(),
            (),
            "legacy-view runtime grant option must be rejected",
        )
        self._revoke_view_privilege("sqag_runtime", "sqag_quote_artifacts", "SELECT")
        self._execute_admin_sql("alter view public.sqag_quote_artifacts owner to \"sqag_runtime\"")
        self.assertNotEqual(
            self._evaluate_view_authority_rows(),
            (),
            "legacy-view runtime ownership must be rejected",
        )

    def test_legacy_ordinary_view_with_bounded_select_is_accepted(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        source_table = self._create_legacy_quote_artifacts_view()
        self._grant_manifest_view_authority("sqag_runtime")
        self._assert_exact_runtime_view_grants("sqag_runtime")
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        child_table = "legacy_quote_artifacts_source_child"
        missing_table = "sqag_profiles"
        missing_table_ident = _quote_identifier(missing_table)
        runtime_ident = _quote_identifier("sqag_runtime")
        self._execute_admin_sql(
            f"revoke SELECT on table public.{missing_table_ident} from {runtime_ident}"
        )
        try:
            missing_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(
                any(
                    "public_table_row_" in error
                    and "runtime_privilege_mismatch_SELECT_expected_True_got_False" in error
                    for error in missing_errors
                ),
                missing_errors,
            )
        finally:
            self._execute_admin_sql(
                f"grant SELECT on table public.{missing_table_ident} to {runtime_ident}"
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        persistence_source_ident = f"public.{_quote_identifier(source_table)}"
        self._execute_admin_sql(f"alter table {persistence_source_ident} set unlogged")
        try:
            persistence_columns, persistence_rows = self._execute_contract_query(
                "effective_runtime_table_privileges"
            )
            self.assertEqual(
                persistence_columns, CANONICAL_QUERY_COLUMNS["effective_runtime_table_privileges"]
            )
            persistence_values = {
                str(row["relation_persistence"])
                for row in persistence_rows
                if str(row["table_name"]) == source_table
            }
            self.assertEqual(persistence_values, {"u"})
            unlogged_errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(
                any(
                    "classified_view_source_relation_persistence_invalid" in error
                    for error in unlogged_errors
                ),
                unlogged_errors,
            )
        finally:
            self._execute_admin_sql(f"alter table {persistence_source_ident} set logged")

        restored_columns, restored_rows = self._execute_contract_query(
            "effective_runtime_table_privileges"
        )
        self.assertEqual(
            restored_columns, CANONICAL_QUERY_COLUMNS["effective_runtime_table_privileges"]
        )
        restored_values = {
            str(row["relation_persistence"])
            for row in restored_rows
            if str(row["table_name"]) == source_table
        }
        self.assertEqual(restored_values, {"p"})
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        grandchild_table = "legacy_quote_artifacts_source_grandchild"
        child_ident = _quote_identifier(child_table)
        grandchild_ident = _quote_identifier(grandchild_table)
        source_ident = f"public.{_quote_identifier(source_table)}"
        self.addCleanup(
            self._cleanup_steps,
            [
                ("drop_legacy_source_grandchild", f"drop table if exists public.{grandchild_ident}"),
                ("drop_legacy_source_child", f"drop table if exists public.{child_ident}"),
            ],
        )
        connection = self.connect()
        try:
            connection.execute("set role \"sqag_migrator\"")
            connection.execute(
                f"create table public.{child_ident} () inherits ({source_ident})"
            )
            connection.execute(
                f"create table public.{grandchild_ident} () inherits (public.{child_ident})"
            )
            connection.execute(
                f"insert into {source_ident} values "
                "('workspace', 'session', 'kind', 'file', 'text/plain', 1, decode('00', 'hex'), 'created', 'updated')"
            )
            connection.execute(
                f"insert into public.{child_ident} values "
                "('workspace', 'session-child', 'kind', 'file-child', 'text/plain', 1, decode('00', 'hex'), 'created', 'updated')"
            )
            connection.execute(
                f"insert into public.{grandchild_ident} values "
                "('workspace', 'session-grandchild', 'kind', 'file-grandchild', 'text/plain', 1, decode('00', 'hex'), 'created', 'updated')"
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()

        with self.as_role("sqag_runtime") as runtime_connection:
            row = runtime_connection.execute(
                "select count(*) as row_count from public.sqag_quote_artifacts"
            ).fetchone()
            runtime_connection.commit()
        self.assertEqual(int(_row_dict(row, "row_count")), 3)
        descendant_columns, descendant_rows = self._execute_contract_query(
            "effective_runtime_table_privileges"
        )
        self.assertEqual(
            descendant_columns, CANONICAL_QUERY_COLUMNS["effective_runtime_table_privileges"]
        )
        source_rows = [
            row for row in descendant_rows if str(row["table_name"]) == source_table
        ]
        self.assertTrue(source_rows)
        self.assertTrue(all(bool(row["has_inheritance_descendants"]) for row in source_rows))
        descendant_errors = self._evaluate_runtime_authority_rows()
        self.assertTrue(
            any("classified_view_source_inheritance_descendants_forbidden" in error for error in descendant_errors),
            descendant_errors,
        )
        self._execute_admin_sql(f"drop table public.{grandchild_ident}")
        self._execute_admin_sql(f"drop table public.{child_ident}")
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        view_columns, view_rows = self._execute_contract_query("view_acl")
        raw_table_columns, raw_table_rows = self._execute_contract_query("table_acl")
        table_columns, table_rows = self._execute_contract_query("effective_runtime_table_privileges")
        column_columns, column_rows = self._execute_contract_query("effective_runtime_column_privileges")
        self.assertEqual(view_columns, CANONICAL_QUERY_COLUMNS["view_acl"])
        self.assertEqual(raw_table_columns, CANONICAL_QUERY_COLUMNS["table_acl"])
        self.assertIn(
            source_table,
            {str(row["relname"]) for row in raw_table_rows},
        )
        self.assertEqual(table_columns, CANONICAL_QUERY_COLUMNS["effective_runtime_table_privileges"])
        self.assertEqual(column_columns, CANONICAL_QUERY_COLUMNS["effective_runtime_column_privileges"])
        view_evidence = [dict(row) for row in view_rows]
        table_evidence = [dict(row) for row in table_rows]
        column_evidence = [dict(row) for row in column_rows]
        source_table_evidence = [row for row in table_evidence if row["table_name"] == source_table]
        source_column_evidence = [row for row in column_evidence if row["table_name"] == source_table]
        self.assertEqual(len(source_table_evidence), len(TABLE_PRIVILEGES))
        self.assertTrue(source_table_evidence)
        self.assertTrue(all(row["schema_name"] == "public" for row in source_table_evidence))
        self.assertTrue(all(row["relation_kind"] == "r" for row in source_table_evidence))
        self.assertTrue(all(row["owner"] != "sqag_runtime" for row in source_table_evidence))
        self.assertTrue(all(row["relation_persistence"] == "p" for row in source_table_evidence))
        self.assertTrue(all(row["owner_select"] for row in source_table_evidence))
        self.assertTrue(all(not row["row_security_enabled"] for row in source_table_evidence))
        self.assertTrue(all(not row["row_security_forced"] for row in source_table_evidence))
        self.assertTrue(source_column_evidence)
        self.assertTrue(all(not row["effective"] and not row["is_grantable"] for row in source_table_evidence))
        self.assertTrue(all(not row["effective"] and not row["is_grantable"] for row in source_column_evidence))
        missing_source = contract_validator.evaluate_runtime_authority(
            self.contract,
            view_evidence,
            [row for row in table_evidence if row["table_name"] != source_table],
            column_evidence,
        )
        self.assertTrue(any("bound_source_table_evidence_missing" in error for error in missing_source), missing_source)

        source_ident = f"public.{_quote_identifier(source_table)}"
        source_owner = self._new_role("legacy_source_owner")
        self._execute_admin_sql(
            f"alter table {source_ident} owner to {_quote_identifier(source_owner)}"
        )
        self._execute_admin_sql(
            f"revoke SELECT on table {source_ident} from {_quote_identifier('sqag_migrator')}"
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(
                any("classified_view_owner_source_select_required" in error for error in errors),
                errors,
            )
            self._execute_admin_sql(
                f"grant SELECT on table {source_ident} to {_quote_identifier('sqag_migrator')}"
            )
            try:
                self.assertEqual(self._evaluate_runtime_authority_rows(), ())
            finally:
                self._execute_admin_sql(
                    f"revoke SELECT on table {source_ident} from {_quote_identifier('sqag_migrator')}"
                )
        finally:
            self._execute_admin_sql(
                f"alter table {source_ident} owner to {_quote_identifier('sqag_migrator')}"
            )

        for privilege in TABLE_PRIVILEGES:
            self._execute_admin_sql(
                f'grant {privilege} on table {source_ident} to {_quote_identifier("sqag_runtime")}'
            )
            try:
                errors = self._evaluate_runtime_authority_rows()
                self.assertTrue(any("bound_source_table_row_" in error and "runtime_privilege_forbidden" in error for error in errors), (privilege, errors))
            finally:
                self._execute_admin_sql(
                    f'revoke {privilege} on table {source_ident} from {_quote_identifier("sqag_runtime")}'
                )

        self._execute_admin_sql(
            f'grant SELECT on table {source_ident} to {_quote_identifier("sqag_runtime")} with grant option'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any("bound_source_table_row_" in error and "runtime_grant_option_forbidden" in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                f'revoke SELECT on table {source_ident} from {_quote_identifier("sqag_runtime")}'
            )

        self._execute_admin_sql(
            f'alter table {source_ident} owner to {_quote_identifier("sqag_runtime")}'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any("runtime_owner_forbidden" in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                f'alter table {source_ident} owner to {_quote_identifier("sqag_migrator")}'
            )

        self._execute_admin_sql(
            f'alter table {source_ident} owner to {_quote_identifier(source_owner)}'
        )
        try:
            self._execute_admin_sql(
                f'grant SELECT on table {source_ident} to {_quote_identifier("sqag_migrator")}'
            )
            self._execute_admin_sql(f'alter table {source_ident} enable row level security')
            try:
                enabled_errors = self._evaluate_runtime_authority_rows()
                self.assertTrue(
                    any("row_security_enabled_forbidden" in error for error in enabled_errors),
                    enabled_errors,
                )
                self._execute_admin_sql(f'alter table {source_ident} force row level security')
                forced_errors = self._evaluate_runtime_authority_rows()
                self.assertTrue(
                    any("row_security_forced_forbidden" in error for error in forced_errors),
                    forced_errors,
                )
            finally:
                self._execute_admin_sql(f'alter table {source_ident} no force row level security')
                self._execute_admin_sql(f'alter table {source_ident} disable row level security')
        finally:
            self._execute_admin_sql(
                f'revoke SELECT on table {source_ident} from {_quote_identifier("sqag_migrator")}'
            )
            self._execute_admin_sql(
                f'alter table {source_ident} owner to {_quote_identifier("sqag_migrator")}'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(f"grant SELECT on table {source_ident} to public")
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any("runtime_privilege_forbidden_SELECT" in error for error in errors), errors)
        finally:
            self._execute_admin_sql(f"revoke SELECT on table {source_ident} from public")

        table_parent = self._new_role("legacy_source_table_parent")
        self._grant_role_membership(table_parent, "sqag_runtime")
        self._execute_admin_sql(
            f'grant SELECT on table {source_ident} to {_quote_identifier(table_parent)}'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any("runtime_privilege_forbidden_SELECT" in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                f'revoke SELECT on table {source_ident} from {_quote_identifier(table_parent)}'
            )
            self._revoke_role_memberships(table_parent)

        source_column = "workspace_id"
        for privilege in COLUMN_PRIVILEGES:
            self._execute_admin_sql(
                f'grant {privilege} ({_quote_identifier(source_column)}) on table {source_ident} to {_quote_identifier("sqag_runtime")}'
            )
            try:
                errors = self._evaluate_runtime_authority_rows()
                self.assertTrue(any("bound_source_column_row_" in error and "runtime_privilege_forbidden" in error for error in errors), (privilege, errors))
            finally:
                self._execute_admin_sql(
                    f'revoke {privilege} ({_quote_identifier(source_column)}) on table {source_ident} from {_quote_identifier("sqag_runtime")}'
                )

        self._execute_admin_sql(
            f'grant SELECT ({_quote_identifier(source_column)}) on table {source_ident} to {_quote_identifier("sqag_runtime")} with grant option'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any("bound_source_column_row_" in error and "runtime_grant_option_forbidden" in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                f'revoke SELECT ({_quote_identifier(source_column)}) on table {source_ident} from {_quote_identifier("sqag_runtime")}'
            )

        self._execute_admin_sql(
            f'grant SELECT ({_quote_identifier(source_column)}) on table {source_ident} to public'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any("runtime_privilege_forbidden_SELECT" in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                f'revoke SELECT ({_quote_identifier(source_column)}) on table {source_ident} from public'
            )

        column_parent = self._new_role("legacy_source_column_parent")
        self._grant_role_membership(column_parent, "sqag_runtime")
        self._execute_admin_sql(
            f'grant SELECT ({_quote_identifier(source_column)}) on table {source_ident} to {_quote_identifier(column_parent)}'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any("runtime_privilege_forbidden_SELECT" in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                f'revoke SELECT ({_quote_identifier(source_column)}) on table {source_ident} from {_quote_identifier(column_parent)}'
            )
            self._revoke_role_memberships(column_parent)

        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        columns, rows = self._execute_contract_query("view_acl")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["view_acl"])
        self.assertEqual(
            {str(row["relation_name"]) for row in rows},
            {"sqag_quote_artifacts"},
        )
        self.assertEqual({str(row["relation_kind"]) for row in rows}, {"v"})
        self.assertTrue(all(bool(row["runtime_select"]) for row in rows))
        self.assertFalse(any(bool(row["runtime_select_grantable"]) for row in rows))
        self.assertEqual(
            contract_validator.evaluate_view_authority(self.contract, [dict(row) for row in rows]),
            (),
        )

    def test_boundary_b_owner_authority_is_required_and_exact(self) -> None:
        self.apply_migrations()
        runtime_name = "sqag_runtime"
        migrator_name = "sqag_migrator"
        bootstrap_identity = self._session_user()
        owner_name = self._new_role("boundary_b_owner")
        unrelated_name = self._new_role("boundary_b_unrelated")
        self.addCleanup(
            self._cleanup_steps,
            [
                (
                    "restore_database_owner",
                    f"alter database {_quote_identifier(self.database_name)} owner to {_quote_identifier(bootstrap_identity)}",
                )
            ],
        )

        # 1. Session/bootstrap identity and the active current_user under every SET ROLE boundary.
        self.assertNotIn(bootstrap_identity, {runtime_name, migrator_name, owner_name, unrelated_name})
        for role_name in (migrator_name, runtime_name, owner_name, unrelated_name):
            self.assertEqual(self._current_user(role_name), role_name, role_name)

        # 2. Exact role attributes for every boundary identity; none is superuser.
        for role_name in (migrator_name, runtime_name, owner_name, unrelated_name):
            attrs = self._role_attribute_row(role_name)
            self.assertEqual(attrs["rolname"], role_name)
            self.assertIs(attrs["rolsuper"], False)
            self.assertIs(attrs["rolcreatedb"], False)
            self.assertIs(attrs["rolcreaterole"], False)
            self.assertIs(attrs["rolreplication"], False)
            self.assertIs(attrs["rolbypassrls"], False)
            self.assertIs(attrs["rolcanlogin"], False)
            self.assertIs(attrs["rolinherit"], True)
            self.assertEqual(attrs["rolconnlimit"], -1)

        # 3. The database-owner authority owns the exact disposable database.
        self.assertEqual(self._database_owner(), bootstrap_identity)
        self._execute_admin_sql(
            f"alter database {_quote_identifier(self.database_name)} owner to {_quote_identifier(owner_name)}"
        )
        self.assertEqual(self._database_owner(), owner_name)

        # 4. The public schema is owned by pg_database_owner, which resolves to the exact database owner.
        self.assertEqual(self._schema_owner(), "pg_database_owner")
        self.assertEqual(self._database_owner(), owner_name)

        # 5. The migrator has no database/schema ownership, no membership in the
        #    database-owner authority, and no hidden membership source.
        self.assertNotEqual(self._database_owner(), migrator_name)
        self.assertNotEqual(self._schema_owner(), migrator_name)
        self.assertFalse(self._is_role_member(migrator_name, owner_name))
        self.assertFalse(self._is_role_member(migrator_name, "pg_database_owner"))
        self.assertFalse(self._is_role_member(runtime_name, owner_name))
        self.assertFalse(self._is_role_member(unrelated_name, owner_name))
        self.assertEqual(self._runtime_like_memberships(self._membership_snapshot()), (self._expected_provider_edge(),))

        # 6. The migrator and the unrelated role hold no direct grant option on the
        #    database or schema that could satisfy the owner-only operations.
        for role_name in (migrator_name, unrelated_name):
            self.assertFalse(self._has_database_privilege(role_name, "CONNECT WITH GRANT OPTION"))
            self.assertFalse(self._has_database_privilege(role_name, "CREATE WITH GRANT OPTION"))
            self.assertFalse(self._has_database_privilege(role_name, "TEMPORARY WITH GRANT OPTION"))
            self.assertFalse(self._has_schema_privilege(role_name, "USAGE WITH GRANT OPTION"))
            self.assertFalse(self._has_schema_privilege(role_name, "CREATE WITH GRANT OPTION"))

        # 7. sqag_runtime starts with no direct database CONNECT or schema USAGE ACL entry.
        self.assertIsNone(self._raw_database_acl_entry(runtime_name, "CONNECT"))
        self.assertIsNone(self._raw_schema_acl_entry(runtime_name, "USAGE"))

        owner_only_sql = (
            f"grant connect on database {_quote_identifier(self.database_name)} to \"sqag_runtime\"",
            "grant usage on schema public to \"sqag_runtime\"",
            f"revoke temporary on database {_quote_identifier(self.database_name)} from public",
        )

        # 8. The migrator cannot perform the owner-only operations. PostgreSQL 17
        #    applies the documented no-op-with-warning GRANT/REVOKE denial for
        #    database/schema ACL statements from a non-owner without a grant option,
        #    so the denial is proven by the exact absence of effect on the raw ACL
        #    rather than by a SQLSTATE that PostgreSQL does not raise for these
        #    object classes.
        for sql in owner_only_sql:
            with self.as_role(migrator_name) as migrator_connection:
                migrator_connection.execute(sql)
                migrator_connection.commit()
            self.assertIsNone(self._raw_database_acl_entry(runtime_name, "CONNECT"), sql)
            self.assertIsNone(self._raw_schema_acl_entry(runtime_name, "USAGE"), sql)
            self.assertTrue(self._has_database_privilege("public", "TEMPORARY"), sql)

        # 9. An unrelated negative role cannot perform them either.
        for sql in owner_only_sql:
            with self.as_role(unrelated_name) as unrelated_connection:
                unrelated_connection.execute(sql)
                unrelated_connection.commit()
            self.assertIsNone(self._raw_database_acl_entry(runtime_name, "CONNECT"), sql)
            self.assertIsNone(self._raw_schema_acl_entry(runtime_name, "USAGE"), sql)
            self.assertTrue(self._has_database_privilege("public", "TEMPORARY"), sql)

        # 10. The exact database-owner authority performs the owner-only operations
        #     and they take effect without any grant option.
        with self.as_role(owner_name) as owner_connection:
            owner_connection.execute(
                f"grant connect on database {_quote_identifier(self.database_name)} to \"sqag_runtime\""
            )
            owner_connection.execute("grant usage on schema public to \"sqag_runtime\"")
            owner_connection.execute(
                f"revoke temporary on database {_quote_identifier(self.database_name)} from public"
            )
            owner_connection.commit()
        self.addCleanup(
            self._restore_public_database_privilege,
            "TEMPORARY",
            self._public_database_baseline["TEMPORARY"],
        )
        self.assertIs(self._raw_database_acl_entry(runtime_name, "CONNECT"), False)
        self.assertIs(self._raw_schema_acl_entry(runtime_name, "USAGE"), False)
        self.assertTrue(self._has_database_privilege(runtime_name, "CONNECT"))
        self.assertTrue(self._has_schema_privilege(runtime_name, "USAGE"))
        self.assertFalse(self._has_database_privilege("public", "TEMPORARY"))
        self.assertFalse(self._has_database_privilege(runtime_name, "TEMPORARY"))

        # 11. An unrelated role with database CONNECT but no owner authority still cannot grant.
        wrong_owner = self._new_role("boundary_b_wrong_owner")
        self._grant_database_privilege(wrong_owner, "CONNECT")
        with self.as_role(wrong_owner) as wrong_connection:
            wrong_connection.execute(
                f"grant connect on database {_quote_identifier(self.database_name)} to \"sqag_runtime\""
            )
            wrong_connection.commit()
        self.assertIs(self._raw_database_acl_entry(runtime_name, "CONNECT"), False)

        # 12. The same database owner cannot grant authority on a different database it does not own.
        other_database = self._create_database()
        self.addCleanup(self._drop_extra_database, other_database)
        with self.as_role(owner_name) as owner_connection:
            owner_connection.execute(
                f"grant connect on database {_quote_identifier(other_database)} to \"sqag_runtime\""
            )
            owner_connection.commit()
        self.assertIsNone(self._raw_database_acl_entry(runtime_name, "CONNECT", other_database))

        # 13. Object-level grants remain the correct object owner's (sqag_migrator) work, and an
        #     unrelated role with no table privilege is denied with SQLSTATE 42501.
        self.assertEqual(self._table_owner("sqag_profiles"), migrator_name)
        with self.as_role(migrator_name) as migrator_connection:
            migrator_connection.execute("grant select on table public.sqag_profiles to \"sqag_runtime\"")
            migrator_connection.commit()
        self.assertTrue(self._has_table_privilege(runtime_name, "sqag_profiles", "SELECT"))
        with self.as_role(unrelated_name) as unrelated_connection:
            try:
                unrelated_connection.execute("grant select on table public.sqag_profiles to \"sqag_runtime\"")
                unrelated_connection.commit()
            except Exception as exc:
                unrelated_connection.rollback()
                self.assertEqual(getattr(exc, "sqlstate", None), "42501")
            else:
                self.fail("unrelated role unexpectedly granted an object-level table privilege")

        # 14. Idempotent rerun succeeds and leaves the exact state unchanged.
        with self.as_role(owner_name) as owner_connection:
            owner_connection.execute(
                f"grant connect on database {_quote_identifier(self.database_name)} to \"sqag_runtime\""
            )
            owner_connection.execute(
                f"revoke temporary on database {_quote_identifier(self.database_name)} from public"
            )
            owner_connection.commit()
        self.assertTrue(self._has_database_privilege(runtime_name, "CONNECT"))
        self.assertFalse(self._has_database_privilege("public", "TEMPORARY"))
        self.assertIs(self._raw_database_acl_entry(runtime_name, "CONNECT"), False)

        # 15. Exact final runtime state: no grant options, no memberships, no default ACL drift.
        self.assertEqual(
            self._effective_database_privileges(runtime_name),
            {("CONNECT", True, False), ("CREATE", False, False), ("TEMPORARY", False, False)},
        )
        self.assertEqual(
            self._effective_schema_privileges(runtime_name),
            {("USAGE", True, False), ("CREATE", False, False)},
        )
        self.assertEqual(self._runtime_like_memberships(self._membership_snapshot()), (self._expected_provider_edge(),))
        self.assertEqual(self._default_acl_snapshot(), set())

        # 16. Cleanup restores the exact captured PUBLIC baseline.
        self._restore_public_database_privilege(
            "TEMPORARY", self._public_database_baseline["TEMPORARY"]
        )
        self.assertIs(
            self._has_database_privilege("public", "TEMPORARY"),
            self._public_database_baseline["TEMPORARY"],
        )

    def test_effective_runtime_matrix_missing_privilege_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_missing")
        table_name = "sqag_profiles"
        privilege = "DELETE"
        self._execute_admin_sql(
            f"revoke {privilege} on table {_quote_identifier(table_name)} from {_quote_identifier(role_name)}"
        )
        self.addCleanup(
            self._cleanup_steps,
            [("restore_missing_privilege", f"grant {privilege} on table {_quote_identifier(table_name)} to {_quote_identifier(role_name)}")],
        )
        self._assert_isolated_matrix_mismatch(role_name, {("public", table_name, privilege, False)})

    def test_effective_runtime_matrix_extra_privilege_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_extra")
        table_name = "sqag_generation_runs"
        privilege = "DELETE"
        self._grant_table_privilege(role_name, table_name, privilege)
        self._assert_isolated_matrix_mismatch(role_name, {("public", table_name, privilege, False)})

    def test_effective_runtime_matrix_moved_privilege_table_to_table_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_moved")
        source_table = "sqag_object_artifacts"
        target_table = "sqag_quote_publication_artifacts"
        privilege = "UPDATE"
        self._execute_admin_sql(
            f"revoke {privilege} on table {_quote_identifier(source_table)} from {_quote_identifier(role_name)}"
        )
        self.addCleanup(
            self._cleanup_steps,
            [("restore_moved_source", f"grant {privilege} on table {_quote_identifier(source_table)} to {_quote_identifier(role_name)}")],
        )
        self._grant_table_privilege(role_name, target_table, privilege)
        self._assert_isolated_matrix_mismatch(
            role_name,
            {
                ("public", source_table, privilege, False),
                ("public", target_table, privilege, False),
            },
        )

    def test_effective_runtime_matrix_missing_privilege_offset_by_unrelated_extra_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_offset")
        missing_table = "sqag_audit_events"
        missing_privilege = "INSERT"
        extra_table = "sqag_feedback_status_history"
        extra_privilege = "UPDATE"
        self._execute_admin_sql(
            f"revoke {missing_privilege} on table {_quote_identifier(missing_table)} from {_quote_identifier(role_name)}"
        )
        self.addCleanup(
            self._cleanup_steps,
            [("restore_offset_missing", f"grant {missing_privilege} on table {_quote_identifier(missing_table)} to {_quote_identifier(role_name)}")],
        )
        self._grant_table_privilege(role_name, extra_table, extra_privilege)
        self._assert_isolated_matrix_mismatch(
            role_name,
            {
                ("public", missing_table, missing_privilege, False),
                ("public", extra_table, extra_privilege, False),
            },
        )

    def test_effective_runtime_matrix_forbidden_table_privilege_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_forbidden")
        table_name = "sqag_legal_holds"
        privilege = "SELECT"
        self._grant_table_privilege(role_name, table_name, privilege)
        self._assert_isolated_matrix_mismatch(role_name, {("public", table_name, privilege, False)})

    def test_effective_runtime_matrix_real_grant_option_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_grant_option")
        table_name = "sqag_profiles"
        privilege = "SELECT"
        table_ident = _quote_identifier(table_name)
        role_ident = _quote_identifier(role_name)
        self._execute_admin_sql(f"grant {privilege} on table {table_ident} to {role_ident} with grant option")
        self.addCleanup(
            self._cleanup_steps,
            [
                ("revoke_grant_option", f"revoke {privilege} on table {table_ident} from {role_ident}"),
                ("restore_without_grant_option", f"grant {privilege} on table {table_ident} to {role_ident}"),
            ],
        )
        self._assert_isolated_matrix_mismatch(
            role_name,
            {
                ("public", table_name, privilege, False),
                ("public", table_name, privilege, True),
            },
        )

    def test_table_privilege_matrix_direct_mismatches_cover_all_postgresql17_privileges(self) -> None:
        role_name = self._new_exact_matrix_role("table_direct_all")
        table_name = "sqag_legal_holds"
        for privilege in TABLE_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on table {_quote_identifier(table_name)} to {_quote_identifier(role_name)}"
                )
                try:
                    self._assert_isolated_matrix_mismatch(
                        role_name,
                        {("public", table_name, privilege, False)},
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on table {_quote_identifier(table_name)} from {_quote_identifier(role_name)}"
                    )

    def test_table_privilege_matrix_direct_grant_options_cover_all_postgresql17_privileges(self) -> None:
        role_name = self._new_exact_matrix_role("table_grant_option_all")
        table_name = "sqag_legal_holds"
        for privilege in TABLE_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on table {_quote_identifier(table_name)} "
                    f"to {_quote_identifier(role_name)} with grant option"
                )
                try:
                    self._assert_isolated_matrix_mismatch(
                        role_name,
                        {("public", table_name, privilege, True)},
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on table {_quote_identifier(table_name)} from {_quote_identifier(role_name)}"
                    )

    def test_table_privilege_matrix_public_grants_cover_all_postgresql17_privileges(self) -> None:
        role_name = self._new_exact_matrix_role("table_public_all")
        table_name = "sqag_legal_holds"
        for privilege in TABLE_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on table {_quote_identifier(table_name)} to public"
                )
                try:
                    self._assert_isolated_matrix_mismatch(
                        role_name,
                        {("public", table_name, privilege, False)},
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on table {_quote_identifier(table_name)} from public"
                    )

    def test_table_privilege_matrix_membership_grants_cover_all_postgresql17_privileges(self) -> None:
        role_name = self._new_exact_matrix_role("table_membership_all")
        parent_name = self._new_role("table_membership_parent")
        self._grant_role_membership(parent_name, role_name)
        table_name = "sqag_legal_holds"
        for privilege in TABLE_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on table {_quote_identifier(table_name)} to {_quote_identifier(parent_name)}"
                )
                try:
                    self._assert_isolated_matrix_mismatch(
                        role_name,
                        {("public", table_name, privilege, False)},
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on table {_quote_identifier(table_name)} from {_quote_identifier(parent_name)}"
                    )

    def test_column_privilege_matrix_direct_grants_cover_all_postgresql17_column_privileges(self) -> None:
        role_name = self._new_exact_matrix_role("column_direct_all")
        table_name = "sqag_legal_holds"
        column_name = self._user_columns()[table_name][0]
        for privilege in COLUMN_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} ({_quote_identifier(column_name)}) on table "
                    f"{_quote_identifier(table_name)} to {_quote_identifier(role_name)}"
                )
                try:
                    self._assert_isolated_column_mismatch(
                        role_name,
                        {("public", table_name, column_name, privilege, False)},
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} ({_quote_identifier(column_name)}) on table "
                        f"{_quote_identifier(table_name)} from {_quote_identifier(role_name)}"
                    )

    def test_column_privilege_matrix_grant_options_cover_all_postgresql17_column_privileges(self) -> None:
        role_name = self._new_exact_matrix_role("column_grant_option_all")
        table_name = "sqag_legal_holds"
        column_name = self._user_columns()[table_name][0]
        for privilege in COLUMN_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} ({_quote_identifier(column_name)}) on table "
                    f"{_quote_identifier(table_name)} to {_quote_identifier(role_name)} with grant option"
                )
                try:
                    self._assert_isolated_column_mismatch(
                        role_name,
                        {("public", table_name, column_name, privilege, True)},
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} ({_quote_identifier(column_name)}) on table "
                        f"{_quote_identifier(table_name)} from {_quote_identifier(role_name)}"
                    )

    def test_column_privilege_matrix_public_grants_cover_all_postgresql17_column_privileges(self) -> None:
        role_name = self._new_exact_matrix_role("column_public_all")
        table_name = "sqag_legal_holds"
        column_name = self._user_columns()[table_name][0]
        for privilege in COLUMN_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} ({_quote_identifier(column_name)}) on table "
                    f"{_quote_identifier(table_name)} to public"
                )
                try:
                    self._assert_isolated_column_mismatch(
                        role_name,
                        {("public", table_name, column_name, privilege, False)},
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} ({_quote_identifier(column_name)}) on table "
                        f"{_quote_identifier(table_name)} from public"
                    )

    def test_column_privilege_matrix_membership_grants_cover_all_postgresql17_column_privileges(self) -> None:
        role_name = self._new_exact_matrix_role("column_membership_all")
        parent_name = self._new_role("column_membership_parent")
        self._grant_role_membership(parent_name, role_name)
        table_name = "sqag_legal_holds"
        column_name = self._user_columns()[table_name][0]
        for privilege in COLUMN_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} ({_quote_identifier(column_name)}) on table "
                    f"{_quote_identifier(table_name)} to {_quote_identifier(parent_name)}"
                )
                try:
                    self._assert_isolated_column_mismatch(
                        role_name,
                        {("public", table_name, column_name, privilege, False)},
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} ({_quote_identifier(column_name)}) on table "
                        f"{_quote_identifier(table_name)} from {_quote_identifier(parent_name)}"
                    )

    def test_column_grants_are_exact_per_column_and_not_hidden_by_table_authority(self) -> None:
        role_name = self._new_exact_matrix_role("column_exact_distribution")
        table_name = "sqag_generation_evidence"
        columns = self._user_columns()[table_name]
        self.assertGreaterEqual(len(columns), 2)
        first_column, second_column = columns[:2]
        privilege = "UPDATE"
        self._execute_admin_sql(
            f"grant {privilege} ({_quote_identifier(first_column)}) on table "
            f"{_quote_identifier(table_name)} to {_quote_identifier(role_name)}"
        )
        try:
            self._assert_isolated_column_mismatch(
                role_name,
                {("public", table_name, first_column, privilege, False)},
            )
            expected = self._expected_runtime_column_grants()
            actual = self._effective_column_grants(role_name)
            intended_delta = {("public", table_name, first_column, privilege, False)}
            self.assertEqual(actual ^ expected, intended_delta)
            self.assertEqual(actual - expected, intended_delta)
            self.assertEqual(expected - actual, set())
            self.assertNotIn(("public", table_name, second_column, privilege, False), actual)
            self.assertFalse(
                any(row[1] != table_name and row[3] == privilege for row in actual - expected)
            )
        finally:
            self._execute_admin_sql(
                f"revoke {privilege} ({_quote_identifier(first_column)}) on table "
                f"{_quote_identifier(table_name)} from {_quote_identifier(role_name)}"
            )

    def test_effective_runtime_matrix_unexpected_table_privilege_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_unexpected_table")
        ordinary_name = "sqag_rpc_unexpected_table"
        partitioned_name = "sqag_rpc_unexpected_partitioned"
        partition_name = "sqag_rpc_unexpected_partition"
        ordinary_ident = _quote_identifier(ordinary_name)
        partitioned_ident = _quote_identifier(partitioned_name)
        partition_ident = _quote_identifier(partition_name)

        connection = self.connect()
        try:
            connection.execute("set role \"sqag_migrator\"")
            connection.execute(f"create table {ordinary_ident} (id integer not null)")
            connection.execute(
                f"create table {partitioned_ident} (id integer not null) partition by range (id)"
            )
            connection.execute(
                f"create table {partition_ident} partition of {partitioned_ident} for values from (0) to (100)"
            )
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()

        cleanup_steps = [
            ("revoke_unexpected_table", f"revoke SELECT on table {ordinary_ident} from {_quote_identifier(role_name)}"),
            ("revoke_unexpected_partitioned", f"revoke SELECT on table {partitioned_ident} from {_quote_identifier(role_name)}"),
            ("drop_unexpected_partition", f"drop table if exists {partition_ident}"),
            ("drop_unexpected_partitioned", f"drop table if exists {partitioned_ident}"),
            ("drop_unexpected_table", f"drop table if exists {ordinary_ident}"),
        ]
        self.addCleanup(self._cleanup_steps, cleanup_steps)

        self._execute_admin_sql(
            f"grant SELECT on table {ordinary_ident} to {_quote_identifier(role_name)}"
        )
        self._execute_admin_sql(
            f"grant SELECT on table {partitioned_ident} to {_quote_identifier(role_name)}"
        )

        fixture_names = {ordinary_name, partitioned_name, partition_name}
        actual = self._effective_table_grants(role_name)
        unexpected = {row for row in actual if row[1] in fixture_names}
        self.assertIn(("public", ordinary_name, "SELECT", False), unexpected)
        self.assertIn(("public", partitioned_name, "SELECT", False), unexpected)
        self._assert_isolated_matrix_mismatch(role_name, unexpected)

        relation_identifiers = [(ordinary_name, ordinary_ident), (partitioned_name, partitioned_ident)]
        for provenance in ("public", "membership"):
            parent_name = None
            if provenance == "membership":
                parent_name = self._new_role("matrix_unexpected_parent")
                self._grant_role_membership(parent_name, role_name)
            target = "public" if provenance == "public" else _quote_identifier(parent_name)
            for relation_name, relation_ident in relation_identifiers:
                self._execute_admin_sql(
                    f"grant SELECT on table {relation_ident} to {target}"
                )
                try:
                    actual = self._effective_table_grants(role_name)
                    self.assertIn(
                        ("public", relation_name, "SELECT", False),
                        actual,
                        f"{provenance} authority was not effective for {relation_name}",
                    )
                    self._assert_isolated_matrix_mismatch(
                        role_name,
                        {
                            row
                            for row in actual
                            if row[1] in fixture_names
                        },
                    )
                finally:
                    self._execute_admin_sql(
                        f"revoke SELECT on table {relation_ident} from {target}"
                    )

        table_columns, table_rows = self._execute_contract_query("effective_runtime_table_privileges")
        self.assertEqual(table_columns, CANONICAL_QUERY_COLUMNS["effective_runtime_table_privileges"])
        relation_kinds = {
            str(row["table_name"]): str(row["relation_kind"])
            for row in table_rows
            if str(row["table_name"]) in fixture_names
        }
        self.assertEqual(relation_kinds.get(ordinary_name), "r")
        self.assertEqual(relation_kinds.get(partitioned_name), "p")

    def test_effective_runtime_matrix_unexpected_foreign_table_privilege_is_rejected(
        self,
    ) -> None:
        availability_connection = self.connect()
        try:
            available = bool(
                _row_dict(
                    availability_connection.execute(
                        "select exists (select 1 from pg_catalog.pg_available_extensions "
                        "where name = 'file_fdw') as available"
                    ).fetchone(),
                    "available",
                )
            )
        finally:
            availability_connection.rollback()
            availability_connection.close()

        role_name = ""
        foreign_name = f"sqag_rpc_unexpected_foreign_{uuid.uuid4().hex[:8]}"
        foreign_server = f"sqag_rpc_file_server_{uuid.uuid4().hex[:8]}"
        foreign_ident = _quote_identifier(foreign_name)
        foreign_server_ident = _quote_identifier(foreign_server)
        extension_created = False
        server_created = False
        foreign_created = False
        grant_created = False

        def create_fixture() -> None:
            nonlocal extension_created, server_created, foreign_created, grant_created
            connection = self.connect()
            try:
                extension_present = bool(
                    _row_dict(
                        connection.execute(
                            "select exists (select 1 from pg_catalog.pg_extension "
                            "where extname = 'file_fdw') as present"
                        ).fetchone(),
                        "present",
                    )
                )
                if not extension_present:
                    connection.execute("create extension file_fdw")
                    extension_created = True
                connection.execute(
                    f"create server {foreign_server_ident} foreign data wrapper file_fdw"
                )
                server_created = True
                connection.execute(
                    f"create foreign table {foreign_ident} (id integer) "
                    f"server {foreign_server_ident} options "
                    f"(filename '/tmp/{foreign_name}.csv', format 'csv')"
                )
                foreign_created = True
                connection.execute(
                    f"grant SELECT on table {foreign_ident} "
                    f"to {_quote_identifier(role_name)}"
                )
                grant_created = True
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

        primary_failure: BaseException | None = None
        try:
            _materialize_file_fdw_fixture(available, lambda: None)
            role_name = self._new_exact_matrix_role("matrix_unexpected_foreign")
            _materialize_file_fdw_fixture(available, create_fixture)

            actual = self._effective_table_grants(role_name)
            unexpected = {
                ("public", foreign_name, "SELECT", False),
            }
            self.assertIn(("public", foreign_name, "SELECT", False), actual)
            self._assert_isolated_matrix_mismatch(role_name, unexpected)

            columns, rows = self._execute_contract_query(
                "effective_runtime_table_privileges"
            )
            self.assertEqual(
                columns,
                CANONICAL_QUERY_COLUMNS["effective_runtime_table_privileges"],
            )
            foreign_rows = [
                row for row in rows if str(row["table_name"]) == foreign_name
            ]
            self.assertEqual(len(foreign_rows), 8)
            self.assertEqual(
                {str(row["relation_kind"]) for row in foreign_rows},
                {"f"},
            )
        except BaseException as exc:
            primary_failure = exc
            raise
        finally:
            cleanup_steps: list[tuple[str, str]] = []
            if grant_created:
                cleanup_steps.append(
                    (
                        "revoke_unexpected_foreign",
                        f"revoke SELECT on table {foreign_ident} "
                        f"from {_quote_identifier(role_name)}",
                    )
                )
            if foreign_created:
                cleanup_steps.append(
                    (
                        "drop_unexpected_foreign",
                        f"drop foreign table if exists {foreign_ident}",
                    )
                )
            if server_created:
                cleanup_steps.append(
                    (
                        "drop_unexpected_foreign_server",
                        f"drop server if exists {foreign_server_ident}",
                    )
                )
            if extension_created:
                cleanup_steps.append(
                    (
                        "drop_test_created_file_fdw",
                        "drop extension if exists file_fdw",
                    )
                )
            try:
                self._cleanup_steps(cleanup_steps)
            except Exception as cleanup_error:
                if primary_failure is None:
                    raise
                primary_failure.add_note(f"H25 cleanup failed: {cleanup_error}")

    def test_effective_runtime_matrix_missing_accessible_table_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_missing_table")
        table_name = "sqag_feedback"
        expected_missing = {
            ("public", table_name, privilege, False)
            for privilege in ("SELECT", "INSERT", "UPDATE")
        }
        self._execute_admin_sql(
            f"revoke all privileges on table {_quote_identifier(table_name)} from {_quote_identifier(role_name)}"
        )
        self.addCleanup(
            self._cleanup_steps,
            [
                (
                    f"restore_{table_name}_{privilege}",
                    f"grant {privilege} on table {_quote_identifier(table_name)} to {_quote_identifier(role_name)}",
                )
                for privilege in ("SELECT", "INSERT", "UPDATE")
            ],
        )
        self._assert_isolated_matrix_mismatch(role_name, expected_missing)

    def test_effective_runtime_matrix_wrong_privilege_type_on_correct_table_is_rejected(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_wrong_type")
        table_name = "sqag_generation_evidence"
        missing_privilege = "INSERT"
        extra_privilege = "UPDATE"
        self._execute_admin_sql(
            f"revoke {missing_privilege} on table {_quote_identifier(table_name)} from {_quote_identifier(role_name)}"
        )
        self.addCleanup(
            self._cleanup_steps,
            [("restore_wrong_type_missing", f"grant {missing_privilege} on table {_quote_identifier(table_name)} to {_quote_identifier(role_name)}")],
        )
        self._grant_table_privilege(role_name, table_name, extra_privilege)
        self._assert_isolated_matrix_mismatch(
            role_name,
            {
                ("public", table_name, missing_privilege, False),
                ("public", table_name, extra_privilege, False),
            },
        )

    def test_effective_runtime_matrix_aggregate_counts_do_not_accept_wrong_distribution(self) -> None:
        role_name = self._new_exact_matrix_role("matrix_counts")
        missing_table = "sqag_profiles"
        moved_table = "sqag_generation_runs"
        privilege = "DELETE"
        self._execute_admin_sql(
            f"revoke {privilege} on table {_quote_identifier(missing_table)} from {_quote_identifier(role_name)}"
        )
        self.addCleanup(
            self._cleanup_steps,
            [("restore_count_distribution", f"grant {privilege} on table {_quote_identifier(missing_table)} to {_quote_identifier(role_name)}")],
        )
        self._grant_table_privilege(role_name, moved_table, privilege)
        actual = self._effective_table_grants(role_name)
        self.assertEqual(len(actual), len(self._expected_runtime_grants()))
        self._assert_isolated_matrix_mismatch(
            role_name,
            {
                ("public", missing_table, privilege, False),
                ("public", moved_table, privilege, False),
            },
        )

    def test_forbidden_tables_have_no_effective_runtime_privilege(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("forbidden")
        self._grant_database_privilege(role_name, "CONNECT")
        self._grant_schema_privilege(role_name, "USAGE")
        actual = self._effective_table_grants(role_name)
        self.assertFalse({row[1] for row in actual} & FORBIDDEN_TABLES)

    def test_public_connect_and_database_schema_acl_posture_is_exact(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("acl")
        self._grant_database_privilege(role_name, "CONNECT")
        self._grant_schema_privilege(role_name, "USAGE")
        self._alter_public_database_privilege("TEMPORARY", False)
        self.assertTrue(self._has_database_privilege("public", "CONNECT"))
        self.assertFalse(self._has_database_privilege("public", "CREATE"))
        self._assert_exact_runtime_database_privileges(role_name)
        self._assert_exact_runtime_schema_privileges(role_name)

    def test_schema_direct_grant_provenance_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        columns, rows = self._execute_contract_query("schema_acl")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["schema_acl"])
        schema_row = rows[0]
        self.assertEqual(schema_row["schema_owner"], "pg_database_owner")
        database_owner = str(schema_row["database_owner"])
        self.assertTrue(database_owner)
        self.assertNotEqual(database_owner, schema_row["schema_owner"])
        direct_usage = [
            entry
            for entry in schema_row["acl_entries"]
            if entry["grantee"] == "sqag_runtime" and entry["privilege_type"] == "USAGE"
        ]
        self.assertEqual(
            [(entry["grantor"], entry["is_grantable"]) for entry in direct_usage],
            [("pg_database_owner", False)],
        )

        self._execute_admin_sql('revoke usage on schema public from "sqag_runtime"')
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn("runtime_schema_direct_usage_evidence_missing_or_duplicate", errors)
        finally:
            self._execute_admin_sql('grant usage on schema public to "sqag_runtime"')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        parent_role = self._new_role("schema_provenance_parent")
        self._grant_role_membership(parent_role, "sqag_runtime")
        self._execute_admin_sql(
            f'grant usage on schema public to {_quote_identifier(parent_role)}'
        )
        self._execute_admin_sql('revoke usage on schema public from "sqag_runtime"')
        try:
            self.assertTrue(self._has_schema_privilege("sqag_runtime", "USAGE"))
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn("runtime_schema_direct_usage_evidence_missing_or_duplicate", errors)
        finally:
            self._execute_admin_sql('revoke usage on schema public from "sqag_runtime"')
            self._execute_admin_sql(
                f'revoke all privileges on schema public from {_quote_identifier(parent_role)}'
            )
            self._revoke_role_memberships(parent_role)
            self._execute_admin_sql('grant usage on schema public to "sqag_runtime"')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql('grant usage on schema public to "sqag_runtime" with grant option')
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn("runtime_schema_direct_usage_grant_option_forbidden", errors)
        finally:
            self._execute_admin_sql('revoke usage on schema public from "sqag_runtime"')
            self._execute_admin_sql('grant usage on schema public to "sqag_runtime"')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql('grant create on schema public to "sqag_runtime"')
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn("runtime_schema_direct_privilege_forbidden_CREATE", errors)
        finally:
            self._execute_admin_sql('revoke create on schema public from "sqag_runtime"')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        wrong_grantor = self._new_role("schema_wrong_grantor")
        self._execute_admin_sql(
            f'grant usage on schema public to {_quote_identifier(wrong_grantor)} with grant option'
        )
        self._execute_admin_sql('revoke usage on schema public from "sqag_runtime"')
        try:
            with self.as_role(wrong_grantor) as wrong_connection:
                wrong_connection.execute('grant usage on schema public to "sqag_runtime"')
                wrong_connection.commit()
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(
                any(
                    "runtime_schema_direct_usage_grantor_invalid_expected_" in error
                    for error in errors
                ),
                errors,
            )
        finally:
            with self.as_role(wrong_grantor) as wrong_connection:
                wrong_connection.execute('revoke usage on schema public from "sqag_runtime"')
                wrong_connection.commit()
            self._execute_admin_sql(
                f'revoke all privileges on schema public from {_quote_identifier(wrong_grantor)}'
            )
            self._execute_admin_sql('grant usage on schema public to "sqag_runtime"')
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
    def test_parameter_privilege_authority_controls_fail_closed_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        direct_set = 'grant set on parameter session_replication_role to "sqag_runtime"'
        direct_set_revoke = 'revoke set on parameter session_replication_role from "sqag_runtime"'
        self._execute_admin_sql(direct_set)
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_parameter_effective_set_forbidden_session_replication_role",
                errors,
            )
        finally:
            self._execute_admin_sql(direct_set_revoke)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql("grant set on parameter session_replication_role to public")
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_parameter_effective_set_forbidden_session_replication_role",
                errors,
            )
        finally:
            self._execute_admin_sql("revoke set on parameter session_replication_role from public")
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        parent_role = self._new_role("parameter_parent")
        self._grant_role_membership(parent_role, "sqag_runtime")
        self._execute_admin_sql(
            f'grant set on parameter session_replication_role to {_quote_identifier(parent_role)}'
        )
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_parameter_effective_set_forbidden_session_replication_role",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'revoke set on parameter session_replication_role from {_quote_identifier(parent_role)}'
            )
        self._revoke_role_memberships(parent_role)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        direct_alter_system = 'grant alter system on parameter session_replication_role to "sqag_runtime"'
        direct_alter_system_revoke = 'revoke alter system on parameter session_replication_role from "sqag_runtime"'
        self._execute_admin_sql(direct_alter_system)
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_parameter_effective_alter_system_forbidden_session_replication_role",
                errors,
            )
        finally:
            self._execute_admin_sql(direct_alter_system_revoke)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        set_grant_option = 'grant set on parameter session_replication_role to "sqag_runtime" with grant option'
        self._execute_admin_sql(set_grant_option)
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_parameter_effective_set_forbidden_session_replication_role",
                errors,
            )
            self.assertIn(
                "runtime_parameter_set_grant_option_forbidden_session_replication_role",
                errors,
            )
        finally:
            self._execute_admin_sql(direct_set_revoke)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        alter_system_grant_option = 'grant alter system on parameter session_replication_role to "sqag_runtime" with grant option'
        self._execute_admin_sql(alter_system_grant_option)
        try:
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_parameter_effective_alter_system_forbidden_session_replication_role",
                errors,
            )
            self.assertIn(
                "runtime_parameter_alter_system_grant_option_forbidden_session_replication_role",
                errors,
            )
        finally:
            self._execute_admin_sql(direct_alter_system_revoke)
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        columns, rows = self._execute_contract_query("effective_runtime_parameter_privileges")
        self.assertEqual(columns, CANONICAL_QUERY_COLUMNS["effective_runtime_parameter_privileges"])
        session_row = next(row for row in rows if row["parameter_name"] == "session_replication_role")
        self.assertFalse(session_row["effective_set"])
        self.assertFalse(session_row["effective_alter_system"])
        self.assertFalse(session_row["set_grantable"])
        self.assertFalse(session_row["alter_system_grantable"])
        self.assertFalse(
            any(
                entry["grantee"] in {"PUBLIC", "sqag_runtime"}
                and entry["privilege_type"] in {"SET", "ALTER SYSTEM"}
                for entry in session_row["acl_entries"]
            )
        )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
    def test_parameter_startup_defaults_are_closed_world_postgres(self) -> None:
        self._prepare_fixed_runtime_contract_fixture()
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
        database_ident = _quote_identifier(self.database_name)

        controls = (
            (
                "role_global_session_replication",
                'alter role "sqag_runtime" set session_replication_role = replica',
                'alter role "sqag_runtime" reset session_replication_role',
                "runtime_parameter_unsafe_startup_default_session_replication_role",
            ),
            (
                "database_global_session_replication",
                f"alter database {database_ident} set session_replication_role = replica",
                f"alter database {database_ident} reset session_replication_role",
                "runtime_parameter_unsafe_startup_default_session_replication_role",
            ),
            (
                "role_database_session_replication",
                f"alter role \"sqag_runtime\" in database {database_ident} set session_replication_role = replica",
                f"alter role \"sqag_runtime\" in database {database_ident} reset session_replication_role",
                "runtime_parameter_unsafe_startup_default_session_replication_role",
            ),
            (
                "role_global_read_only",
                'alter role "sqag_runtime" set default_transaction_read_only = on',
                'alter role "sqag_runtime" reset default_transaction_read_only',
                "runtime_parameter_unsafe_startup_default_default_transaction_read_only_on",
            ),
            (
                "database_global_read_only",
                f"alter database {database_ident} set default_transaction_read_only = on",
                f"alter database {database_ident} reset default_transaction_read_only",
                "runtime_parameter_unsafe_startup_default_default_transaction_read_only_on",
            ),
            (
                "role_database_read_only",
                f"alter role \"sqag_runtime\" in database {database_ident} set default_transaction_read_only = on",
                f"alter role \"sqag_runtime\" in database {database_ident} reset default_transaction_read_only",
                "runtime_parameter_unsafe_startup_default_default_transaction_read_only_on",
            ),
        )
        for scope, apply_sql, restore_sql, expected_error in controls:
            with self.subTest(h50_scope=scope):
                self._execute_admin_sql(apply_sql)
                try:
                    errors = self._evaluate_runtime_authority_rows()
                    self.assertTrue(any(expected_error in error for error in errors), (scope, errors))
                finally:
                    self._execute_admin_sql(restore_sql)
                self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(
            f"alter database {database_ident} set default_transaction_read_only = off"
        )
        self._execute_admin_sql(
            'alter role "sqag_runtime" set default_transaction_read_only = on'
        )
        try:
            _, parameter_rows = self._execute_contract_query("effective_runtime_parameter_privileges")
            read_only_row = next(
                row for row in parameter_rows if row["parameter_name"] == "default_transaction_read_only"
            )
            observed_settings = {entry["setting"] for entry in read_only_row["startup_defaults"]}
            self.assertEqual(
                observed_settings,
                {"default_transaction_read_only=off", "default_transaction_read_only=on"},
            )
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                "runtime_parameter_unsafe_startup_default_default_transaction_read_only_on",
                errors,
            )
        finally:
            self._execute_admin_sql(
                'alter role "sqag_runtime" reset default_transaction_read_only'
            )
            self._execute_admin_sql(
                f"alter database {database_ident} reset default_transaction_read_only"
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self._execute_admin_sql(
            'alter role "sqag_runtime" set default_transaction_read_only = on'
        )
        try:
            self._execute_admin_sql(
                "update pg_catalog.pg_db_role_setting setting_row "
                "set setconfig = array['default_transaction_read_only=off', "
                "'default_transaction_read_only=on']::text[] "
                "where setting_row.setrole = (select oid from pg_catalog.pg_roles "
                "where rolname = 'sqag_runtime') and setting_row.setdatabase = 0"
            )
            _, parameter_rows = self._execute_contract_query("effective_runtime_parameter_privileges")
            read_only_row = next(
                row for row in parameter_rows if row["parameter_name"] == "default_transaction_read_only"
            )
            self.assertEqual(len(read_only_row["startup_defaults"]), 2)
            errors = self._evaluate_runtime_authority_rows()
            self.assertTrue(any("startup_default_ambiguous" in error for error in errors), errors)
        finally:
            self._execute_admin_sql(
                'alter role "sqag_runtime" reset default_transaction_read_only'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        unknown_parameter = f"sqag_h50_unknown_{uuid.uuid4().hex[:8]}.setting"
        self._execute_admin_sql(
            f'alter role "sqag_runtime" set {unknown_parameter} = \'on\''
        )
        try:
            _, parameter_rows = self._execute_contract_query("effective_runtime_parameter_privileges")
            unknown_row = next(
                row for row in parameter_rows if row["parameter_name"] == unknown_parameter
            )
            self.assertEqual(
                unknown_row["startup_defaults"],
                [{"scope": "role_global", "precedence": 2, "setting": f"{unknown_parameter}=on"}],
            )
            errors = self._evaluate_runtime_authority_rows()
            self.assertIn(
                f"runtime_parameter_startup_default_unclassified_{unknown_parameter}",
                errors,
            )
        finally:
            self._execute_admin_sql(
                f'alter role "sqag_runtime" reset {unknown_parameter}'
            )
        self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        for safe_value in ("origin", "local"):
            with self.subTest(h50_safe_session_replication=safe_value):
                self._execute_admin_sql(
                    f'alter role "sqag_runtime" set session_replication_role = {safe_value}'
                )
                try:
                    self.assertEqual(self._evaluate_runtime_authority_rows(), ())
                finally:
                    self._execute_admin_sql(
                        'alter role "sqag_runtime" reset session_replication_role'
                    )
                self.assertEqual(self._evaluate_runtime_authority_rows(), ())

        self.assertEqual(self._evaluate_runtime_authority_rows(), ())
    def test_database_privilege_matrix_direct_public_membership_and_cross_substitution(self) -> None:
        self._alter_public_database_privilege("CONNECT", False)
        self._alter_public_database_privilege("TEMPORARY", False)
        direct_role = self._new_role("database_direct_all")
        for privilege in DATABASE_PRIVILEGES:
            with self.subTest(source="direct", privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on database {_quote_identifier(self.database_name)} "
                    f"to {_quote_identifier(direct_role)}"
                )
                try:
                    if privilege == "CONNECT":
                        self._assert_exact_runtime_database_privileges(direct_role)
                    else:
                        self.assertNotEqual(
                            self._effective_database_privileges(direct_role),
                            self._expected_runtime_database_privileges(),
                        )
                        self.assertTrue(
                            any(row[0] == privilege and row[1] for row in self._effective_database_privileges(direct_role))
                        )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on database {_quote_identifier(self.database_name)} "
                        f"from {_quote_identifier(direct_role)}"
                    )

        public_role = self._new_role("database_public_all")
        for privilege in DATABASE_PRIVILEGES:
            with self.subTest(source="public", privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on database {_quote_identifier(self.database_name)} to public"
                )
                try:
                    if privilege == "CONNECT":
                        self._assert_exact_runtime_database_privileges(public_role)
                    else:
                        self.assertNotEqual(
                            self._effective_database_privileges(public_role),
                            self._expected_runtime_database_privileges(),
                        )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on database {_quote_identifier(self.database_name)} from public"
                    )

        membership_role = self._new_role("database_membership_all")
        parent_role = self._new_role("database_membership_parent")
        self._grant_role_membership(parent_role, membership_role)
        for privilege in DATABASE_PRIVILEGES:
            with self.subTest(source="membership", privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on database {_quote_identifier(self.database_name)} "
                    f"to {_quote_identifier(parent_role)}"
                )
                try:
                    if privilege == "CONNECT":
                        self._assert_exact_runtime_database_privileges(membership_role)
                    else:
                        self.assertNotEqual(
                            self._effective_database_privileges(membership_role),
                            self._expected_runtime_database_privileges(),
                        )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on database {_quote_identifier(self.database_name)} "
                        f"from {_quote_identifier(parent_role)}"
                    )

        cross_role = self._new_role("database_cross")
        self._execute_admin_sql(
            f"grant CREATE on database {_quote_identifier(self.database_name)} to {_quote_identifier(cross_role)}"
        )
        self.assertFalse(
            next(row for row in self._effective_database_privileges(cross_role) if row[0] == "CONNECT")[1]
        )
        self.assertTrue(
            next(row for row in self._effective_database_privileges(cross_role) if row[0] == "CREATE")[1]
        )

    def test_database_privilege_matrix_grant_options_cover_all_privileges(self) -> None:
        self._alter_public_database_privilege("CONNECT", False)
        self._alter_public_database_privilege("TEMPORARY", False)
        role_name = self._new_role("database_grant_options")
        for privilege in DATABASE_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on database {_quote_identifier(self.database_name)} "
                    f"to {_quote_identifier(role_name)} with grant option"
                )
                try:
                    actual = self._effective_database_privileges(role_name)
                    row = next(item for item in actual if item[0] == privilege)
                    self.assertTrue(row[1])
                    self.assertTrue(row[2])
                    self.assertNotEqual(actual, self._expected_runtime_database_privileges())
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on database {_quote_identifier(self.database_name)} "
                        f"from {_quote_identifier(role_name)}"
                    )

    def test_public_temporary_revocation_blocks_runtime_and_restores(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("temporary")
        self._grant_database_privilege(role_name, "CONNECT")
        self._alter_public_database_privilege("TEMPORARY", False)
        self.assertFalse(self._has_database_privilege(role_name, "TEMPORARY"))
        self.assertFalse(self._has_database_privilege("public", "TEMPORARY"))

    def test_schema_privilege_matrix_direct_public_membership_and_cross_substitution(self) -> None:
        self._alter_public_database_privilege("TEMPORARY", False)
        self._execute_admin_sql("revoke USAGE on schema public from public")
        self.addCleanup(self._execute_admin_sql, "grant USAGE on schema public to public")

        direct_role = self._new_role("schema_direct_all")
        for privilege in SCHEMA_PRIVILEGES:
            with self.subTest(source="direct", privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on schema public to {_quote_identifier(direct_role)}"
                )
                try:
                    if privilege == "USAGE":
                        self._assert_exact_runtime_schema_privileges(direct_role)
                    else:
                        self.assertNotEqual(
                            self._effective_schema_privileges(direct_role),
                            self._expected_runtime_schema_privileges(),
                        )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on schema public from {_quote_identifier(direct_role)}"
                    )

        public_role = self._new_role("schema_public_all")
        for privilege in SCHEMA_PRIVILEGES:
            with self.subTest(source="public", privilege=privilege):
                self._execute_admin_sql(f"grant {privilege} on schema public to public")
                try:
                    if privilege == "USAGE":
                        self._assert_exact_runtime_schema_privileges(public_role)
                    else:
                        self.assertNotEqual(
                            self._effective_schema_privileges(public_role),
                            self._expected_runtime_schema_privileges(),
                        )
                finally:
                    self._execute_admin_sql(f"revoke {privilege} on schema public from public")

        membership_role = self._new_role("schema_membership_all")
        parent_role = self._new_role("schema_membership_parent")
        self._grant_role_membership(parent_role, membership_role)
        for privilege in SCHEMA_PRIVILEGES:
            with self.subTest(source="membership", privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on schema public to {_quote_identifier(parent_role)}"
                )
                try:
                    if privilege == "USAGE":
                        self._assert_exact_runtime_schema_privileges(membership_role)
                    else:
                        self.assertNotEqual(
                            self._effective_schema_privileges(membership_role),
                            self._expected_runtime_schema_privileges(),
                        )
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on schema public from {_quote_identifier(parent_role)}"
                    )

        cross_role = self._new_role("schema_cross")
        self._execute_admin_sql(f"grant CREATE on schema public to {_quote_identifier(cross_role)}")
        self.assertFalse(
            next(row for row in self._effective_schema_privileges(cross_role) if row[0] == "USAGE")[1]
        )
        self.assertTrue(
            next(row for row in self._effective_schema_privileges(cross_role) if row[0] == "CREATE")[1]
        )

    def test_schema_privilege_matrix_grant_options_cover_all_privileges(self) -> None:
        self._alter_public_database_privilege("TEMPORARY", False)
        self._execute_admin_sql("revoke USAGE on schema public from public")
        self.addCleanup(self._execute_admin_sql, "grant USAGE on schema public to public")
        role_name = self._new_role("schema_grant_options")
        for privilege in SCHEMA_PRIVILEGES:
            with self.subTest(privilege=privilege):
                self._execute_admin_sql(
                    f"grant {privilege} on schema public to {_quote_identifier(role_name)} with grant option"
                )
                try:
                    actual = self._effective_schema_privileges(role_name)
                    row = next(item for item in actual if item[0] == privilege)
                    self.assertTrue(row[1])
                    self.assertTrue(row[2])
                    self.assertNotEqual(actual, self._expected_runtime_schema_privileges())
                finally:
                    self._execute_admin_sql(
                        f"revoke {privilege} on schema public from {_quote_identifier(role_name)}"
                    )

    def test_public_acl_baseline_audit_detects_omitted_restoration(self) -> None:
        privilege = "TEMPORARY"
        expected = self._public_database_baseline[privilege]
        self._execute_admin_sql(
            f"grant {privilege} on database {_quote_identifier(self.database_name)} to public"
            if not expected
            else f"revoke {privilege} on database {_quote_identifier(self.database_name)} from public"
        )
        try:
            with self.assertRaisesRegex(AssertionError, "PUBLIC database ACL baseline drifted"):
                self._audit_public_acl_baseline()
        finally:
            self._restore_public_database_privilege(privilege, expected)

    def test_early_failure_cleanup_restores_public_acl_baseline(self) -> None:
        privilege = "TEMPORARY"
        expected = self._public_database_baseline[privilege]
        self._alter_public_database_privilege(privilege, not expected)
        with self.assertRaisesRegex(RuntimeError, "synthetic early failure"):
            try:
                raise RuntimeError("synthetic early failure")
            finally:
                self._restore_public_database_privilege(privilege, expected)
        self._assert_public_acl_baseline_values(
            self._public_database_baseline,
            self._public_database_acl_snapshot(),
            {},
            {},
        )

    def test_runtime_grants_have_no_grant_options(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("grant_option")
        self._grant_database_privilege(role_name, "CONNECT")
        self._grant_schema_privilege(role_name, "USAGE")
        self._grant_table_privilege(role_name, "sqag_profiles", "SELECT")
        actual = self._effective_table_grants(role_name)
        self.assertFalse(any(row[3] for row in actual))

    def test_no_default_acl_grants_target_runtime_like_role(self) -> None:
        self.apply_migrations()
        runtime_name = self._new_role("default_clean")
        rows = self._default_acl_snapshot()
        self.assertFalse(any(row[3] == runtime_name for row in rows), rows)

    def test_default_acl_adversarial_fixtures_detect_runtime_public_and_grant_option(self) -> None:
        self.apply_migrations()
        owner_name = self._new_role("default_owner")
        runtime_name = self._new_role("default_runtime")
        self._register_default_acl_audit({owner_name, runtime_name})
        self._alter_default_privilege(owner_name, runtime_name, "SELECT", "TABLES")
        self._alter_default_privilege(owner_name, runtime_name, "USAGE", "SEQUENCES")
        self._alter_default_privilege(owner_name, runtime_name, "EXECUTE", "FUNCTIONS")
        self._alter_default_privilege(owner_name, runtime_name, "USAGE", "SCHEMAS")
        self._alter_default_privilege(owner_name, runtime_name, "USAGE", "TYPES")
        self._alter_default_privilege(owner_name, "PUBLIC", "SELECT", "TABLES")
        self._alter_default_privilege(owner_name, runtime_name, "UPDATE", "TABLES", with_grant_option=True)
        rows = self._default_acl_snapshot(owner_name)
        self.assertIn((owner_name, "public", "r", runtime_name, "SELECT", False), rows)
        self.assertIn((owner_name, "public", "S", runtime_name, "USAGE", False), rows)
        self.assertIn((owner_name, "public", "f", runtime_name, "EXECUTE", False), rows)
        self.assertIn((owner_name, "<global>", "n", runtime_name, "USAGE", False), rows)
        self.assertIn((owner_name, "public", "T", runtime_name, "USAGE", False), rows)
        self.assertIn((owner_name, "public", "r", "PUBLIC", "SELECT", False), rows)
        self.assertIn((owner_name, "public", "r", runtime_name, "UPDATE", True), rows)

    def test_default_acl_schema_type_and_tuple_mutations_fail_exact_contract(self) -> None:
        self.apply_migrations()

        def assert_mismatch(
            owner_name: str,
            expected: set[tuple[Any, ...]],
            mutate: Any,
            restore: Any,
        ) -> None:
            before = self._default_acl_snapshot(owner_name)
            mutate()
            actual = self._default_acl_snapshot(owner_name)
            self.assertNotEqual(actual, expected)
            with self.assertRaises(AssertionError):
                self.assertEqual(actual, expected)
            restore()
            self.assertEqual(self._default_acl_snapshot(owner_name), before)

        schema_owner = self._new_role("default_schema_owner")
        schema_runtime = self._new_role("default_schema_runtime")
        self._register_default_acl_audit({schema_owner, schema_runtime})
        assert_mismatch(
            schema_owner,
            set(),
            lambda: self._alter_default_privilege(schema_owner, schema_runtime, "USAGE", "SCHEMAS"),
            lambda: self._revoke_default_privilege(schema_owner, schema_runtime, "USAGE", "SCHEMAS"),
        )

        type_owner = self._new_role("default_type_owner")
        type_runtime = self._new_role("default_type_runtime")
        self._register_default_acl_audit({type_owner, type_runtime})
        expected_type_tuple = {
            (type_owner, "public", "T", type_runtime, "USAGE", False),
        }
        before_type = self._default_acl_snapshot(type_owner)
        self._alter_default_privilege(type_owner, type_runtime, "USAGE", "TYPES")
        actual_type = self._default_acl_snapshot(type_owner)
        self.assertEqual(actual_type, expected_type_tuple)
        with self.assertRaises(AssertionError):
            self.assertEqual(actual_type, set())
        self._revoke_default_privilege(type_owner, type_runtime, "USAGE", "TYPES")
        self.assertEqual(self._default_acl_snapshot(type_owner), before_type)

        public_owner = self._new_role("default_public_owner")
        public_runtime = self._new_role("default_public_runtime")
        self._register_default_acl_audit({public_owner, public_runtime})
        assert_mismatch(
            public_owner,
            {(public_owner, "public", "T", public_runtime, "USAGE", False)},
            lambda: self._alter_default_privilege(public_owner, "PUBLIC", "USAGE", "TYPES"),
            lambda: self._revoke_default_privilege(public_owner, "PUBLIC", "USAGE", "TYPES"),
        )

        option_owner = self._new_role("default_option_owner")
        option_runtime = self._new_role("default_option_runtime")
        self._register_default_acl_audit({option_owner, option_runtime})
        assert_mismatch(
            option_owner,
            {(option_owner, "public", "T", option_runtime, "USAGE", False)},
            lambda: self._alter_default_privilege(
                option_owner, option_runtime, "USAGE", "TYPES", with_grant_option=True
            ),
            lambda: self._revoke_default_privilege(option_owner, option_runtime, "USAGE", "TYPES"),
        )

        extra_owner = self._new_role("default_extra_owner")
        extra_runtime = self._new_role("default_extra_runtime")
        self._register_default_acl_audit({extra_owner, extra_runtime})
        expected_extra = {(extra_owner, "public", "r", extra_runtime, "SELECT", False)}
        self._alter_default_privilege(extra_owner, extra_runtime, "SELECT", "TABLES")
        self._alter_default_privilege(extra_owner, extra_runtime, "INSERT", "TABLES")
        actual_extra = self._default_acl_snapshot(extra_owner)
        self.assertNotEqual(actual_extra, expected_extra)
        with self.assertRaises(AssertionError):
            self.assertEqual(actual_extra, expected_extra)
        self._revoke_default_privilege(extra_owner, extra_runtime, "INSERT", "TABLES")
        self._revoke_default_privilege(extra_owner, extra_runtime, "SELECT", "TABLES")

        missing_owner = self._new_role("default_missing_owner")
        missing_runtime = self._new_role("default_missing_runtime")
        self._register_default_acl_audit({missing_owner, missing_runtime})
        expected_missing = {(missing_owner, "public", "r", missing_runtime, "SELECT", False)}
        self._alter_default_privilege(missing_owner, missing_runtime, "SELECT", "TABLES")
        self.assertEqual(self._default_acl_snapshot(missing_owner), expected_missing)
        self._revoke_default_privilege(missing_owner, missing_runtime, "SELECT", "TABLES")
        actual_missing = self._default_acl_snapshot(missing_owner)
        self.assertNotEqual(actual_missing, expected_missing)
        with self.assertRaises(AssertionError):
            self.assertEqual(actual_missing, expected_missing)

        wrong_grantee_owner = self._new_role("default_wrong_grantee_owner")
        expected_runtime = self._new_role("default_expected_runtime")
        wrong_grantee = self._new_role("default_wrong_grantee")
        self._register_default_acl_audit({wrong_grantee_owner, expected_runtime, wrong_grantee})
        expected_grantee = {
            (wrong_grantee_owner, "public", "r", expected_runtime, "SELECT", False),
        }
        assert_mismatch(
            wrong_grantee_owner,
            expected_grantee,
            lambda: self._alter_default_privilege(wrong_grantee_owner, wrong_grantee, "SELECT", "TABLES"),
            lambda: self._revoke_default_privilege(wrong_grantee_owner, wrong_grantee, "SELECT", "TABLES"),
        )

        expected_owner = self._new_role("default_expected_owner")
        wrong_owner = self._new_role("default_wrong_owner")
        owner_runtime = self._new_role("default_owner_runtime")
        self._register_default_acl_audit({expected_owner, wrong_owner, owner_runtime})
        expected_owner_tuple = {
            (expected_owner, "public", "r", owner_runtime, "SELECT", False),
        }
        before_all = self._default_acl_snapshot()
        self._alter_default_privilege(wrong_owner, owner_runtime, "SELECT", "TABLES")
        actual_wrong_owner = self._default_acl_snapshot()
        self.assertNotEqual(actual_wrong_owner & expected_owner_tuple, expected_owner_tuple)
        with self.assertRaises(AssertionError):
            self.assertEqual(actual_wrong_owner, expected_owner_tuple)
        self._revoke_default_privilege(wrong_owner, owner_runtime, "SELECT", "TABLES")
        self.assertEqual(self._default_acl_snapshot(), before_all)

        wrong_type_owner = self._new_role("default_wrong_type_owner")
        wrong_type_runtime = self._new_role("default_wrong_type_runtime")
        self._register_default_acl_audit({wrong_type_owner, wrong_type_runtime})
        expected_table_tuple = {
            (wrong_type_owner, "public", "r", wrong_type_runtime, "SELECT", False),
        }
        assert_mismatch(
            wrong_type_owner,
            expected_table_tuple,
            lambda: self._alter_default_privilege(wrong_type_owner, wrong_type_runtime, "USAGE", "SEQUENCES"),
            lambda: self._revoke_default_privilege(wrong_type_owner, wrong_type_runtime, "USAGE", "SEQUENCES"),
        )

        distribution_owner = self._new_role("default_distribution_owner")
        distribution_runtime = self._new_role("default_distribution_runtime")
        self._register_default_acl_audit({distribution_owner, distribution_runtime})
        expected_distribution = {
            (distribution_owner, "public", "r", distribution_runtime, "SELECT", False),
        }
        assert_mismatch(
            distribution_owner,
            expected_distribution,
            lambda: self._alter_default_privilege(distribution_owner, "PUBLIC", "SELECT", "TABLES"),
            lambda: self._revoke_default_privilege(distribution_owner, "PUBLIC", "SELECT", "TABLES"),
        )

        membership_owner = self._new_role("default_membership_owner")
        membership_parent = self._new_role("default_membership_parent")
        membership_runtime = self._new_role("default_membership_runtime")
        self._register_default_acl_audit({membership_owner, membership_parent, membership_runtime})
        membership_table = "run22_default_membership"
        self.addCleanup(
            self._cleanup_steps,
            [("drop_default_membership_table", f"drop table if exists public.{_quote_identifier(membership_table)}")],
        )
        self._grant_schema_privilege(membership_owner, "CREATE")
        self._grant_role_membership(membership_parent, membership_runtime)
        self._alter_default_privilege(membership_owner, membership_parent, "SELECT", "TABLES")
        connection = self.connect()
        try:
            connection.execute(f"set role {_quote_identifier(membership_owner)}")
            connection.execute(f"create table public.{_quote_identifier(membership_table)} (id integer)")
            connection.execute("reset role")
            connection.commit()
        finally:
            connection.close()
        self.assertTrue(
            self._has_table_privilege(membership_runtime, membership_table, "SELECT")
        )
        actual_membership = self._default_acl_snapshot(membership_owner)
        self.assertNotEqual(actual_membership, set())
        with self.assertRaises(AssertionError):
            self.assertEqual(actual_membership, set())

    def test_provider_default_acl_state_is_identical_before_and_after_migrations(self) -> None:
        provider_name = "neondb_owner"
        grantee_name = self._new_role("provider_default_grantee")
        self._register_default_acl_audit({provider_name, grantee_name})
        self._alter_default_privilege(provider_name, grantee_name, "SELECT", "TABLES")
        self._alter_default_privilege(provider_name, grantee_name, "USAGE", "SEQUENCES")
        self._alter_default_privilege(provider_name, grantee_name, "EXECUTE", "FUNCTIONS")
        self._alter_default_privilege(provider_name, grantee_name, "USAGE", "SCHEMAS")
        self._alter_default_privilege(provider_name, grantee_name, "USAGE", "TYPES")
        before = self._default_acl_snapshot(provider_name)
        expected = {
            (provider_name, "public", "r", grantee_name, "SELECT", False),
            (provider_name, "public", "S", grantee_name, "USAGE", False),
            (provider_name, "public", "f", grantee_name, "EXECUTE", False),
            (provider_name, "<global>", "n", grantee_name, "USAGE", False),
            (provider_name, "public", "T", grantee_name, "USAGE", False),
        }
        self._assert_expected_provider_default_tuples(before, expected)
        self.apply_migrations()
        after = self._default_acl_snapshot(provider_name)
        self.assertEqual(after, before)

    def test_provider_default_baseline_absence_is_rejected(self) -> None:
        expected = {("neondb_owner", "public", "r", "provider_grantee", "SELECT", False)}
        with self.assertRaises(AssertionError):
            self._assert_expected_provider_default_tuples(set(), expected)


if __name__ == "__main__":
    unittest.main()
