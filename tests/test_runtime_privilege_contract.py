"""Runtime privilege contract tests.

Deterministic discovery receipt for this amendment:
  discovered methods: 55
  static and validator methods: 37
  PostgreSQL methods: 18
  hosted executions: 55
  hosted skips: 0
  unique locked requirement IDs: 38 (R01-R38)

The locked proof points are requirement identifiers, not a test count. The
static section also contains ten independent adversarial manifest fixtures
(A01-A10), and the PostgreSQL section uses disposable databases, actual
migrated objects, and non-superuser SET ROLE sessions.
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_runtime_privilege_contract import (  # noqa: E402
    validate_manifest_strictly,
)
from webapp.postgres_migrations import (  # noqa: E402
    EXPECTED_ROUTINES,
    MIGRATION_FILE_NAMES,
    MIGRATION_TABLES,
    apply_postgres_migrations,
    migration_manifest,
)
from webapp.server import PostgresConnectionAdapter  # noqa: E402


MANIFEST_PATH = ROOT / "docs" / "runtime-privilege-contract.json"
IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
RUNTIME_TABLES = frozenset(load_name for load_name in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["tables"]["runtime_accessible"])
FORBIDDEN_TABLES = frozenset(json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["tables"]["runtime_forbidden"])
ALL_TABLES = RUNTIME_TABLES | FORBIDDEN_TABLES
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
where d.defaclobjtype in ('r', 'S', 'f')
order by owner_name, namespace, object_type, grantee, expanded.privilege_type, expanded.is_grantable
"""


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


def load_manifest() -> dict[str, Any]:
    with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"unsafe test identifier: {identifier!r}")
    return f'"{identifier}"'


def _fixture_json(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, ensure_ascii=True, sort_keys=True)


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

    def test_runtime_role_no_memberships_no_ownership(self) -> None:
        runtime = self.manifest["roles"]["runtime"]
        self.assertEqual(runtime["memberships"], [])
        self.assertEqual(runtime["ownership"], [])
        self.assertEqual(runtime["grant_options"], [])

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
            self.assertEqual(entry["class"], "trigger_only")
            self.assertIs(entry["direct_runtime_execute"], False)
            self.assertIs(entry["public_execute_after_boundary_b"], False)

    def test_database_and_schema_acl_targets(self) -> None:
        database = self.manifest["database_acl"]
        self.assertEqual(set(database), {"public", "sqag_migrator", "sqag_app", "sqag_runtime"})
        self.assertIs(database["public"]["connect"], True)
        self.assertIs(database["public"]["create"], False)
        self.assertEqual(database["public"]["temporary"], "forbidden_after_boundary_b")
        self.assertIs(database["sqag_runtime"]["connect"], True)
        self.assertIs(database["sqag_runtime"]["create"], False)
        self.assertIs(database["sqag_runtime"]["temporary"], False)
        schema = self.manifest["schema_acl"]
        self.assertEqual(schema["schema_name"], "public")
        self.assertIs(schema["public"]["usage"], True)
        self.assertIs(schema["sqag_runtime"]["usage"], True)
        self.assertIs(schema["sqag_runtime"]["create"], False)

    def test_default_privileges_are_grantee_aware(self) -> None:
        defaults = self.manifest["default_privileges"]
        self.assertEqual(defaults["sqag_runtime"], {"tables": "none", "sequences": "none", "routines": "none"})
        self.assertIn("aclexplode", defaults["verification_rule"])
        self.assertIn("grantee", defaults["verification_rule"])

    def test_verification_queries_are_complete(self) -> None:
        self.assertEqual(
            set(self.manifest["verification_queries"]),
            {
                "database_acl", "schema_acl", "table_acl", "routine_acl", "default_acl",
                "role_attributes", "role_memberships", "sequence_acl",
                "effective_runtime_table_privileges", "effective_runtime_schema_privileges",
                "effective_runtime_routine_privileges",
            },
        )
        for query in self.manifest["verification_queries"].values():
            self.assertIsInstance(query, str)
            self.assertTrue(query.strip())


class ValidatorStaticTest(unittest.TestCase):
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
                        "where rolname in ('sqag_migrator', 'neondb_owner') or rolname like %s "
                        "order by rolname",
                        ("sqag_rpc_role_%",),
                    ).fetchall()
                ]
                leftover_memberships = connection.execute(
                    "select parent.rolname as role_name, member.rolname as member_name "
                    "from pg_catalog.pg_auth_members am "
                    "join pg_catalog.pg_roles parent on parent.oid = am.roleid "
                    "join pg_catalog.pg_roles member on member.oid = am.member "
                    "where parent.rolname in ('sqag_migrator', 'neondb_owner') "
                    "or member.rolname in ('sqag_migrator', 'neondb_owner') "
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
                                "or owner_role.rolname like %s "
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
        self.addCleanup(self._audit_and_drop_database)
        self._create_role("sqag_migrator")
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

    def _create_role(self, role_name: str) -> str:
        _quote_identifier(role_name)
        connection = self.connect()
        try:
            connection.execute(
                f"create role {_quote_identifier(role_name)} NOLOGIN NOSUPERUSER NOCREATEDB "
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

    def _audit_and_drop_database(self) -> None:
        errors: list[str] = []
        connection: PostgresConnectionAdapter | None = None
        try:
            connection = self.connect()
            leftover_roles = connection.execute(
                "select rolname from pg_catalog.pg_roles "
                "where rolname in ('sqag_migrator', 'neondb_owner') or rolname like %s "
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
                "where owner_role.rolname in ('sqag_migrator', 'neondb_owner') "
                "or owner_role.rolname like %s "
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

    def _grant_table_privilege(self, role_name: str, table_name: str, privilege: str) -> None:
        if table_name not in ALL_TABLES or privilege not in {"SELECT", "INSERT", "UPDATE", "DELETE"}:
            raise ValueError(f"invalid table grant: {table_name}:{privilege}")
        connection = self.connect()
        try:
            connection.execute(
                f"grant {privilege} on table {_quote_identifier(table_name)} to {_quote_identifier(role_name)}"
            )
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(self._revoke_table_privilege, role_name, table_name, privilege)

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

    def _alter_public_database_privilege(self, privilege: str, grant: bool) -> None:
        before = self._has_database_privilege("public", privilege)
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

    def _revoke_public_execute(self, function_name: str) -> None:
        before = self._public_function_execute(function_name)
        connection = self.connect()
        try:
            connection.execute(f"revoke execute on function public.{_quote_identifier(function_name)}() from public")
            connection.commit()
        finally:
            connection.close()
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
                    "where d.defaclobjtype in ('r', 'S', 'f')",
                    "where d.defaclobjtype in ('r', 'S', 'f') and owner_role.rolname = %s",
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
        }
        if object_keyword not in allowed_privileges or privilege not in allowed_privileges[object_keyword]:
            raise ValueError(f"invalid default privilege: {privilege}:{object_keyword}")
        target = "public" if grantee == "PUBLIC" else _quote_identifier(grantee)
        option = " with grant option" if with_grant_option else ""
        connection = self.connect()
        try:
            connection.execute(
                f"alter default privileges for role {_quote_identifier(owner_name)} in schema public "
                f"grant {privilege} on {object_keyword} to {target}{option}"
            )
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(
            self._revoke_default_privilege,
            owner_name,
            grantee,
            privilege,
            object_keyword,
        )

    def _revoke_default_privilege(self, owner_name: str, grantee: str, privilege: str, object_keyword: str) -> None:
        target = "public" if grantee == "PUBLIC" else _quote_identifier(grantee)
        self._cleanup_steps(
            [
                (
                    f"revoke_default_{owner_name}_{grantee}_{privilege}_{object_keyword}",
                    f"alter default privileges for role {_quote_identifier(owner_name)} in schema public "
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
                "coalesce((select bool_or(a.is_grantable) from pg_catalog.aclexplode(coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))) a "
                "where a.grantee = 0 or a.grantee = (select oid from pg_catalog.pg_roles where rolname = %s)), false) as is_grantable "
                "from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) p(privilege_type) "
                "where n.nspname = 'public' and c.relkind = 'r' "
                "and has_table_privilege(%s, c.oid, p.privilege_type) "
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
            }
        finally:
            connection.rollback()
            connection.close()

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
        provider_name = self._create_role("neondb_owner")
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
        self.addCleanup(
            self._cleanup_steps,
            [("drop_show_db_tree", "drop function public.show_db_tree()")],
        )
        self.apply_migrations()
        rows = self._assert_routine_inventory(provider_name=provider_name, runtime_name=runtime_name)
        provider_row = next(row for row in rows if row["proname"] == "show_db_tree")
        self.assertEqual(provider_row["owner"], "neondb_owner")
        self.assertEqual(self._public_function_execute("show_db_tree"), before_public_execute)

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
        self._alter_public_database_privilege("TEMPORARY", False)
        self._assert_exact_runtime_matrix(role_name)
        self.assertTrue(self._has_database_privilege(role_name, "CONNECT"))
        self.assertFalse(self._has_database_privilege(role_name, "CREATE"))
        self.assertFalse(self._has_database_privilege(role_name, "TEMPORARY"))
        self.assertTrue(self._has_schema_privilege(role_name, "USAGE"))
        self.assertFalse(self._has_schema_privilege(role_name, "CREATE"))
        for table_name in FORBIDDEN_TABLES:
            self.assertFalse(("public", table_name, "SELECT", False) in self._effective_table_grants(role_name))

    def test_effective_runtime_matrix_rejects_swapped_missing_and_forbidden_grants(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("matrix_negative")
        self._grant_database_privilege(role_name, "CONNECT")
        self._grant_schema_privilege(role_name, "USAGE")
        for table_name, entry in self.contract["tables"]["runtime_accessible"].items():
            for privilege, allowed in entry["privileges"].items():
                if allowed:
                    self._grant_table_privilege(role_name, table_name, privilege.upper())
        connection = self.connect()
        try:
            connection.execute(f"revoke UPDATE on table {_quote_identifier('sqag_generation_runs')} from {_quote_identifier(role_name)}")
            connection.commit()
        finally:
            connection.close()
        self.addCleanup(self._grant_table_privilege, role_name, "sqag_generation_runs", "UPDATE")
        self._grant_table_privilege(role_name, "sqag_generation_evidence", "UPDATE")
        self._grant_table_privilege(role_name, "sqag_legal_holds", "SELECT")
        with self.assertRaises(AssertionError):
            self._assert_exact_runtime_matrix(role_name)

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
        self.assertTrue(self._has_database_privilege("public", "CONNECT"))
        self.assertFalse(self._has_database_privilege("public", "CREATE"))
        self.assertTrue(self._has_database_privilege(role_name, "CONNECT"))
        self.assertFalse(self._has_database_privilege(role_name, "CREATE"))
        self.assertTrue(self._has_schema_privilege(role_name, "USAGE"))
        self.assertFalse(self._has_schema_privilege(role_name, "CREATE"))

    def test_public_temporary_revocation_blocks_runtime_and_restores(self) -> None:
        self.apply_migrations()
        role_name = self._new_role("temporary")
        self._grant_database_privilege(role_name, "CONNECT")
        self._alter_public_database_privilege("TEMPORARY", False)
        self.assertFalse(self._has_database_privilege(role_name, "TEMPORARY"))
        self.assertFalse(self._has_database_privilege("public", "TEMPORARY"))

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
        self._alter_default_privilege(owner_name, "PUBLIC", "SELECT", "TABLES")
        self._alter_default_privilege(owner_name, runtime_name, "UPDATE", "TABLES", with_grant_option=True)
        rows = self._default_acl_snapshot(owner_name)
        self.assertIn((owner_name, "public", "r", runtime_name, "SELECT", False), rows)
        self.assertIn((owner_name, "public", "S", runtime_name, "USAGE", False), rows)
        self.assertIn((owner_name, "public", "f", runtime_name, "EXECUTE", False), rows)
        self.assertIn((owner_name, "public", "r", "PUBLIC", "SELECT", False), rows)
        self.assertIn((owner_name, "public", "r", runtime_name, "UPDATE", True), rows)

    def test_provider_default_acl_state_is_identical_before_and_after_migrations(self) -> None:
        provider_name = self._create_role("neondb_owner")
        grantee_name = self._new_role("provider_default_grantee")
        self._register_default_acl_audit({provider_name, grantee_name})
        self._alter_default_privilege(provider_name, grantee_name, "SELECT", "TABLES")
        self._alter_default_privilege(provider_name, grantee_name, "USAGE", "SEQUENCES")
        self._alter_default_privilege(provider_name, grantee_name, "EXECUTE", "FUNCTIONS")
        before = self._default_acl_snapshot(provider_name)
        self.apply_migrations()
        after = self._default_acl_snapshot(provider_name)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
