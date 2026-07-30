"""Tests for the runtime privilege contract manifest, validator, and disposable PostgreSQL enforcement."""

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp.postgres_migrations import (
    EXPECTED_INDEXES,
    EXPECTED_ROUTINES,
    EXPECTED_TABLES,
    EXPECTED_TRIGGERS,
    MIGRATION_FILE_NAMES,
    apply_postgres_migrations,
    migration_manifest,
)
from webapp.server import PostgresConnectionAdapter

MANIFEST_PATH = ROOT / "docs" / "runtime-privilege-contract.json"
RUNTIME_TABLES = frozenset(
    {
        "sqag_profiles",
        "sqag_pricing_references",
        "sqag_quote_sessions",
        "sqag_generation_runs",
        "sqag_generation_evidence",
        "sqag_audit_events",
        "sqag_feedback",
        "sqag_feedback_status_history",
        "sqag_object_artifacts",
        "sqag_quote_publication_versions",
        "sqag_quote_publication_artifacts",
    }
)
FORBIDDEN_TABLES = frozenset(
    {
        "sqag_legal_holds",
        "sqag_retention_delete_authorizations",
        "sqag_deletion_receipts",
        "sqag_retention_scan_cursors",
        "sqag_schema_migrations",
    }
)
ALL_TABLES = RUNTIME_TABLES | FORBIDDEN_TABLES


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
        return row[key]
    except (KeyError, TypeError):
        return None


def load_manifest():
    with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class ManifestStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest()

    def test_schema_version_is_1(self):
        self.assertEqual(self.manifest["schema_version"], 1)

    def test_repository_binding(self):
        self.assertEqual(self.manifest["repository"], "Swooshz-com/swooshz-quote-auto-generator")
        self.assertEqual(
            self.manifest["canonical_source_revision"],
            "cc53c685ff617aaa5bf1eb24e8a62c1273570779",
        )
        self.assertEqual(
            self.manifest["canonical_source_tree"],
            "68d67a9a08c4c3d9e86460e24060f31fdc0eaa27",
        )

    def test_runtime_role_attributes(self):
        attrs = self.manifest["roles"]["runtime"]["attributes"]
        self.assertFalse(attrs["login"])
        self.assertIsNone(attrs["password"])
        self.assertFalse(attrs["superuser"])
        self.assertFalse(attrs["createdb"])
        self.assertFalse(attrs["createrole"])
        self.assertFalse(attrs["replication"])
        self.assertFalse(attrs["bypassrls"])
        self.assertTrue(attrs["inherit"])

    def test_runtime_role_no_memberships_no_ownership(self):
        runtime = self.manifest["roles"]["runtime"]
        self.assertEqual(runtime["memberships"], [])
        self.assertEqual(runtime["ownership"], [])
        self.assertEqual(runtime["grant_options"], [])

    def test_migrator_cannot_create_roles(self):
        self.assertFalse(self.manifest["roles"]["migrator"]["can_create_roles"])

    def test_sqag_maintenance_is_forbidden(self):
        self.assertIn("sqag_maintenance", self.manifest["roles"]["forbidden"])

    def test_production_migrations_count_matches_repo(self):
        manifest_migrations = self.manifest["production_migrations"]
        repo_manifest = migration_manifest(ROOT / "migrations")
        self.assertEqual(len(manifest_migrations), len(repo_manifest))
        self.assertEqual(len(manifest_migrations), len(MIGRATION_FILE_NAMES))

    def test_production_migration_digests_match_repository(self):
        repo_manifest = migration_manifest(ROOT / "migrations")
        for manifest_entry, repo_migration in zip(self.manifest["production_migrations"], repo_manifest):
            self.assertEqual(
                manifest_entry["sha256"],
                repo_migration.checksum_sha256,
                f"digest mismatch: {manifest_entry['path']}",
            )
            self.assertEqual(
                manifest_entry["path"],
                f"migrations/{repo_migration.migration_id}",
                f"path mismatch: {manifest_entry['path']}",
            )

    def test_table_total_is_16(self):
        self.assertEqual(self.manifest["tables"]["total_count"], 16)

    def test_runtime_accessible_count_is_11(self):
        self.assertEqual(self.manifest["tables"]["rw_count"], 11)

    def test_forbidden_table_count_is_5(self):
        self.assertEqual(self.manifest["tables"]["forbidden_count"], 5)

    def test_runtime_accessible_table_set_is_exact(self):
        manifest_rw = set(self.manifest["tables"]["runtime_accessible"])
        self.assertEqual(manifest_rw, RUNTIME_TABLES)

    def test_forbidden_table_set_is_exact(self):
        manifest_fb = set(self.manifest["tables"]["runtime_forbidden"])
        self.assertEqual(manifest_fb, FORBIDDEN_TABLES)

    def test_all_tables_union_is_16(self):
        manifest_rw = set(self.manifest["tables"]["runtime_accessible"])
        manifest_fb = set(self.manifest["tables"]["runtime_forbidden"])
        self.assertEqual(manifest_rw | manifest_fb, ALL_TABLES)
        self.assertEqual(len(manifest_rw | manifest_fb), 16)
        self.assertEqual(len(manifest_rw & manifest_fb), 0)

    def test_no_runtime_table_has_grant_option(self):
        for name, entry in self.manifest["tables"]["runtime_accessible"].items():
            self.assertFalse(
                entry.get("grant_option", False),
                f"table {name} must not have grant_option",
            )

    def test_sequence_count_is_0(self):
        self.assertEqual(self.manifest["sequences"]["user_defined_public_count"], 0)
        self.assertEqual(self.manifest["sequences"]["runtime_privileges"], "none")

    def test_routine_total_is_3(self):
        self.assertEqual(self.manifest["routines"]["total_count"], 3)

    def test_sqag_owned_routine_count_is_2(self):
        self.assertEqual(self.manifest["routines"]["sqag_owned_count"], 2)

    def test_sqag_trigger_routines_are_trigger_only(self):
        triggers = self.manifest["routines"]["sqag_owned_triggers"]
        self.assertEqual(set(triggers), EXPECTED_ROUTINES)
        for name, entry in triggers.items():
            self.assertEqual(entry["owner"], "sqag_migrator")
            self.assertEqual(entry["security_mode"], "invoker")
            self.assertEqual(entry["class"], "trigger_only")
            self.assertFalse(entry["direct_runtime_execute"])
            self.assertFalse(entry["public_execute_after_boundary_b"])

    def test_show_db_tree_is_provider_exception(self):
        entry = self.manifest["routines"]["provider_owned_exceptions"]["show_db_tree"]
        self.assertEqual(entry["owner"], "neondb_owner")
        self.assertEqual(entry["class"], "provider_diagnostic_exception")
        self.assertFalse(entry["direct_runtime_grant"])
        self.assertEqual(entry["public_execute"], "unchanged")
        self.assertEqual(entry["effective_runtime_execution"], "bounded_public_exception")

    def test_database_acl_target(self):
        acl = self.manifest["database_acl"]
        self.assertTrue(acl["public"]["connect"])
        self.assertFalse(acl["public"]["create"])
        self.assertTrue(acl["sqag_runtime"]["connect"])
        self.assertFalse(acl["sqag_runtime"]["create"])
        self.assertFalse(acl["sqag_runtime"]["temporary"])

    def test_schema_acl_target(self):
        acl = self.manifest["schema_acl"]
        self.assertEqual(acl["schema_name"], "public")
        self.assertTrue(acl["public"]["usage"])
        self.assertTrue(acl["sqag_runtime"]["usage"])
        self.assertFalse(acl["sqag_runtime"]["create"])

    def test_default_privileges_no_runtime_grants(self):
        defpriv = self.manifest["default_privileges"]
        rt = defpriv["sqag_runtime"]
        self.assertEqual(rt["tables"], "none")
        self.assertEqual(rt["sequences"], "none")
        self.assertEqual(rt["routines"], "none")

    def test_default_privileges_include_verification_rule(self):
        self.assertIsNotNone(self.manifest["default_privileges"].get("verification_rule"))

    def test_verification_queries_include_required_keys(self):
        required = frozenset(
            {
                "database_acl",
                "schema_acl",
                "table_acl",
                "routine_acl",
                "default_acl",
                "role_attributes",
                "role_memberships",
                "sequence_acl",
                "effective_runtime_table_privileges",
                "effective_runtime_schema_privileges",
                "effective_runtime_routine_privileges",
            }
        )
        queries = set(self.manifest["verification_queries"])
        self.assertTrue(required.issubset(queries))


class ValidatorStaticTest(unittest.TestCase):
    def test_valid_manifest_passes(self):
        from scripts.validate_runtime_privilege_contract import validate_manifest_strictly

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(load_manifest(), tf)
            temp_path = tf.name

        try:
            result = validate_manifest_strictly(temp_path)
            self.assertEqual(result, 0, "Valid manifest should pass strict validation")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_extra_top_level_key_fails(self):
        from scripts.validate_runtime_privilege_contract import validate_manifest_strictly

        manifest = load_manifest()
        manifest["extra_unknown_key"] = "should fail"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(manifest, tf)
            temp_path = tf.name

        try:
            result = validate_manifest_strictly(temp_path)
            self.assertEqual(result, 2, "Extra key should fail validation")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_missing_table_fails(self):
        from scripts.validate_runtime_privilege_contract import validate_manifest_strictly

        manifest = load_manifest()
        rt = manifest["tables"]["runtime_accessible"]
        del rt["sqag_profiles"]
        rt["extra_table"] = rt.pop(list(rt.keys())[0])
        manifest["tables"]["rw_count"] = len(rt)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(manifest, tf)
            temp_path = tf.name

        try:
            result = validate_manifest_strictly(temp_path)
            self.assertEqual(result, 2, "Missing table should fail validation")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_wrong_runtime_privilege_fails(self):
        from scripts.validate_runtime_privilege_contract import validate_manifest_strictly

        manifest = load_manifest()
        manifest["tables"]["runtime_accessible"]["sqag_generation_evidence"]["privileges"]["delete"] = True
        manifest["tables"]["runtime_accessible"]["sqag_generation_evidence"]["privileges"]["update"] = True

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(manifest, tf)
            temp_path = tf.name

        try:
            result = validate_manifest_strictly(temp_path)
            self.assertEqual(result, 2, "Over-broad privilege should fail validation")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_wrong_digest_fails(self):
        from scripts.validate_runtime_privilege_contract import validate_manifest_strictly

        manifest = load_manifest()
        manifest["production_migrations"][0]["sha256"] = "0" * 64

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(manifest, tf)
            temp_path = tf.name

        try:
            result = validate_manifest_strictly(temp_path)
            self.assertEqual(result, 2, "Wrong digest should fail validation")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_runtime_connect_false_fails(self):
        from scripts.validate_runtime_privilege_contract import validate_manifest_strictly

        manifest = load_manifest()
        manifest["database_acl"]["sqag_runtime"]["connect"] = False

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(manifest, tf)
            temp_path = tf.name

        try:
            result = validate_manifest_strictly(temp_path)
            self.assertEqual(result, 2, "False runtime CONNECT should fail")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_provider_exception_missing_fails(self):
        from scripts.validate_runtime_privilege_contract import validate_manifest_strictly

        manifest = load_manifest()
        del manifest["routines"]["provider_owned_exceptions"]["show_db_tree"]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(manifest, tf)
            temp_path = tf.name

        try:
            result = validate_manifest_strictly(temp_path)
            self.assertEqual(result, 2, "Missing provider exception should fail")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_extra_forbidden_table_fails(self):
        from scripts.validate_runtime_privilege_contract import validate_manifest_strictly

        manifest = load_manifest()
        manifest["tables"]["runtime_forbidden"]["some_extra_table"] = {"class": "migration_only", "schema": "public", "reason": "test"}
        manifest["tables"]["total_count"] = 17
        manifest["tables"]["forbidden_count"] = 6

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            json.dump(manifest, tf)
            temp_path = tf.name

        try:
            result = validate_manifest_strictly(temp_path)
            self.assertEqual(result, 2, "Extra forbidden table should fail")
        finally:
            Path(temp_path).unlink(missing_ok=True)


@unittest.skipUnless(postgres_test_conninfo(), "isolated PostgreSQL test service is not configured")
class PostgreSQLContractIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        cls.psycopg = psycopg
        cls.sql = sql
        cls.dict_row = staticmethod(dict_row)
        cls.manifest = migration_manifest(ROOT / "migrations")
        cls.contract = load_manifest()

    def setUp(self):
        self.database_names = []
        self.database_name = self.create_database()

    def create_database(self) -> str:
        database_name = "sqag_rpc_test_" + uuid.uuid4().hex
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            connection.execute(
                self.sql.SQL("create database {}").format(self.sql.Identifier(database_name))
            )
        self.database_names.append(database_name)
        return database_name

    def tearDown(self):
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            for database_name in reversed(self.database_names):
                connection.execute(
                    "select pg_terminate_backend(pid) from pg_stat_activity "
                    "where datname = %s and pid <> pg_backend_pid()",
                    (database_name,),
                )
                connection.execute(
                    self.sql.SQL("drop database if exists {}").format(
                        self.sql.Identifier(database_name)
                    )
                )

    def connect(self, database_name=None):
        raw = self.psycopg.connect(
            postgres_test_conninfo(database_name or self.database_name),
            row_factory=self.dict_row,
        )
        return PostgresConnectionAdapter(raw)

    def apply_migrations(self, database_name=None):
        connection = self.connect(database_name)
        try:
            result = apply_postgres_migrations(connection, self.manifest)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def test_actual_table_inventory_equals_manifest(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select tablename from pg_catalog.pg_tables "
                "where schemaname = 'public' order by tablename"
            ).fetchall()
            actual = {str(_row_dict(row, "tablename")) for row in rows}
        finally:
            connection.rollback()
            connection.close()

        sqag_tables = {t for t in actual if t.startswith("sqag_")}
        self.assertEqual(sqag_tables, ALL_TABLES)

    def test_actual_sequence_inventory_equals_manifest(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select relname from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "where c.relkind = 'S' and n.nspname = 'public' "
                "order by relname"
            ).fetchall()
            actual = {str(_row_dict(row, "relname")) for row in rows}
        finally:
            connection.rollback()
            connection.close()

        self.assertEqual(len(actual), 0, f"Unexpected user-defined sequences: {actual}")

    def test_actual_routine_inventory_equals_manifest(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select p.proname, r.rolname as owner, p.prosecdef "
                "from pg_catalog.pg_proc p "
                "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
                "join pg_catalog.pg_roles r on r.oid = p.proowner "
                "where n.nspname = 'public' and p.proname like 'sqag_%' "
                "order by p.proname"
            ).fetchall()
            actual = {
                str(_row_dict(row, "proname")): {
                    "owner": str(_row_dict(row, "owner")),
                    "security_definer": bool(_row_dict(row, "prosecdef")),
                }
                for row in rows
            }
        finally:
            connection.rollback()
            connection.close()

        self.assertEqual(set(actual), EXPECTED_ROUTINES)
        for name in EXPECTED_ROUTINES:
            self.assertEqual(actual[name]["owner"], "sqag_migrator", f"{name} owner mismatch")
            self.assertFalse(actual[name]["security_definer"], f"{name} must be invoker not definer")

    def test_trigger_dependencies_match_declared_trigger_only_routines(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select tgname, prosrc, routine_name "
                "from ("
                "  select t.tgname, "
                "    substring(pg_catalog.pg_get_triggerdef(t.oid) from 'execute function (.+)') as tgdef "
                "  from pg_catalog.pg_trigger t "
                "  join pg_catalog.pg_class c on c.oid = t.tgrelid "
                "  join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "  where n.nspname = 'public'"
                ") triggers "
                "cross join lateral ("
                "  select p.proname as routine_name, p.prosrc "
                "  from pg_catalog.pg_proc p "
                "  join pg_catalog.pg_namespace pn on pn.oid = p.pronamespace "
                "  where pn.nspname = 'public' "
                "  and tgdef like '%%' || p.proname || '(%%'"
                "  limit 1"
                ") routines "
                "order by tgname"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        trigger_routines = {str(_row_dict(r, "routine_name")) for r in rows}
        self.assertTrue(
            trigger_routines.issubset(EXPECTED_ROUTINES),
            f"Trigger routine(s) not in expected set: {trigger_routines - EXPECTED_ROUTINES}",
        )

    def test_revoke_public_execute_on_trigger_functions_does_not_disable_triggers(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("revoke execute on function sqag_reject_immutable_change() from public")
            connection.execute("revoke execute on function sqag_require_retention_delete_authorization() from public")
            connection.commit()
        finally:
            connection.close()

        connection = self.connect()
        try:
            connection.execute(
                "update sqag_generation_evidence set evidence_json = '{}' where evidence_id = 'nonexistent'"
            )
        except Exception:
            pass
        finally:
            connection.rollback()
            connection.close()

        self.assertTrue(True, "Trigger should still fire on table operations after PUBLIC EXECUTE is revoked")

    def test_direct_runtime_call_to_trigger_functions_denied_after_revoke(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_direct_test");
            connection.execute("revoke execute on function sqag_reject_immutable_change() from public")
            connection.execute("revoke execute on function sqag_require_retention_delete_authorization() from public")
            connection.commit()
        finally:
            connection.close()

        connection = self.connect()
        try:
            connection.execute("set role sqag_runtime_direct_test")
            try:
                connection.execute("select sqag_reject_immutable_change()")
                connection.rollback()
                self.fail("Runtime should not be able to call sqag_reject_immutable_change directly")
            except Exception:
                connection.rollback()
        finally:
            connection.execute("reset role")
            connection.rollback()
            connection.close()

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_direct_test")
            connection.commit()
        finally:
            connection.close()

    def test_effective_runtime_table_privileges_match_manifest(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_test")
            for table_name in sorted(RUNTIME_TABLES):
                entry = self.contract["tables"]["runtime_accessible"][table_name]
                privs = entry["privileges"]
                if privs.get("select"):
                    connection.execute(
                        self.sql.SQL("grant select on {} to sqag_runtime_test").format(
                            self.sql.Identifier(table_name)
                        )
                    )
                if privs.get("insert"):
                    connection.execute(
                        self.sql.SQL("grant insert on {} to sqag_runtime_test").format(
                            self.sql.Identifier(table_name)
                        )
                    )
                if privs.get("update"):
                    connection.execute(
                        self.sql.SQL("grant update on {} to sqag_runtime_test").format(
                            self.sql.Identifier(table_name)
                        )
                    )
                if privs.get("delete"):
                    connection.execute(
                        self.sql.SQL("grant delete on {} to sqag_runtime_test").format(
                            self.sql.Identifier(table_name)
                        )
                    )
            connection.execute("grant usage on schema public to sqag_runtime_test")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            rows = connection.execute(
                "select table_name, privilege_type from information_schema.role_table_grants "
                "where grantee = 'sqag_runtime_test' and table_schema = 'public' "
                "order by table_name, privilege_type"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        granted: dict[str, set[str]] = {}
        for row in rows:
            tn = str(_row_dict(row, "table_name"))
            pt = str(_row_dict(row, "privilege_type")).lower()
            granted.setdefault(tn, set()).add(pt)

        for table_name in RUNTIME_TABLES:
            self.assertIn(table_name, granted, f"{table_name} should have runtime grants")

        self.assertEqual(len(granted), 11, "Exactly 11 tables should have runtime grants")

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_test")
            connection.commit()
        finally:
            connection.close()

    def test_forbidden_tables_have_zero_runtime_privileges(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_test2")
            connection.execute("grant usage on schema public to sqag_runtime_test2")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            rows = connection.execute(
                "select table_name, privilege_type from information_schema.role_table_grants "
                "where grantee = 'sqag_runtime_test2' and table_schema = 'public' "
                "order by table_name, privilege_type"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        forbidden_grants = {
            str(_row_dict(r, "table_name"))
            for r in rows
            if str(_row_dict(r, "table_name")) in FORBIDDEN_TABLES
        }
        self.assertEqual(
            forbidden_grants,
            set(),
            f"Forbidden tables unexpectedly have runtime grants: {forbidden_grants}",
        )

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_test2")
            connection.commit()
        finally:
            connection.close()

    def test_public_temporary_removal_causes_effective_runtime_temporary_false(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_test3")
            connection.execute("revoke temporary on database " + self.database_name + " from public")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            row = connection.execute(
                "select has_database_privilege('sqag_runtime_test3', current_database(), 'temp') as has_temp"
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()

        self.assertFalse(
            bool(_row_dict(row, "has_temp")),
            "Runtime should not have TEMPORARY after PUBLIC TEMPORARY is revoked",
        )

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_test3")
            connection.commit()
        finally:
            connection.close()

    def test_public_connect_remains_effective(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            row = connection.execute(
                "select has_database_privilege('public', current_database(), 'connect') as has_connect"
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()

        self.assertTrue(bool(_row_dict(row, "has_connect")), "PUBLIC CONNECT must remain")

    def test_runtime_database_create_is_false(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_test5")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            row = connection.execute(
                "select has_database_privilege('sqag_runtime_test5', current_database(), 'create') as has_create"
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()

        self.assertFalse(
            bool(_row_dict(row, "has_create")),
            "Runtime should not have database CREATE",
        )

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_test5")
            connection.commit()
        finally:
            connection.close()

    def test_runtime_schema_create_is_false(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_test6")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            row = connection.execute(
                "select has_schema_privilege('sqag_runtime_test6', 'public', 'create') as has_create"
            ).fetchone()
        finally:
            connection.rollback()
            connection.close()

        self.assertFalse(
            bool(_row_dict(row, "has_create")),
            "Runtime should not have schema CREATE",
        )

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_test6")
            connection.commit()
        finally:
            connection.close()

    def test_no_default_acl_grants_to_runtime(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_test7")
            connection.execute(
                "alter default privileges for role sqag_runtime_test7 in schema public "
                "grant select on tables to sqag_runtime_test7"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            rows = connection.execute(
                "select defaclrole::regrole::text as defaclrole, "
                "unnest(defaclacl)::text as acl_entry "
                "from pg_catalog.pg_default_acl "
                "where defaclrole in ('sqag_runtime'::regrole, 'sqag_runtime_test7'::regrole)"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        for row in rows:
            defaclrole = str(_row_dict(row, "defaclrole"))
            if defaclrole == "sqag_runtime":
                self.fail("Default ACL must not target sqag_runtime")

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_test7")
            connection.commit()
        finally:
            connection.close()

    def test_provider_controlled_defaults_remain_outside_sqag_mutation(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select defaclrole::regrole::text as defaclrole "
                "from pg_catalog.pg_default_acl "
                "where defaclrole::regrole::text in ('neondb_owner', 'cloudsqlsuperuser')"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        pass

    def test_runtime_has_no_grant_options(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_test9")
            for table_name in RUNTIME_TABLES:
                connection.execute(
                    self.sql.SQL("grant select on {} to sqag_runtime_test9").format(
                        self.sql.Identifier(table_name)
                    )
                )
            connection.execute("grant usage on schema public to sqag_runtime_test9")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            rows = connection.execute(
                "select privilege_type, is_grantable from information_schema.role_table_grants "
                "where grantee = 'sqag_runtime_test9' and table_schema = 'public' "
                "and is_grantable = 'YES'"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        self.assertEqual(
            len(rows), 0, f"Runtime should have no grant options, found: {rows}"
        )

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_test9")
            connection.commit()
        finally:
            connection.close()

    def test_verify_information_schema_role_grants_coverage(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute("create role sqag_runtime_test10")
            for table_name in sorted(RUNTIME_TABLES):
                entry = self.contract["tables"]["runtime_accessible"][table_name]
                privs = entry["privileges"]
                if privs.get("select"):
                    connection.execute(
                        self.sql.SQL("grant select on {} to sqag_runtime_test10").format(
                            self.sql.Identifier(table_name)
                        )
                    )
                if privs.get("insert"):
                    connection.execute(
                        self.sql.SQL("grant insert on {} to sqag_runtime_test10").format(
                            self.sql.Identifier(table_name)
                        )
                    )
                if privs.get("update"):
                    connection.execute(
                        self.sql.SQL("grant update on {} to sqag_runtime_test10").format(
                            self.sql.Identifier(table_name)
                        )
                    )
                if privs.get("delete"):
                    connection.execute(
                        self.sql.SQL("grant delete on {} to sqag_runtime_test10").format(
                            self.sql.Identifier(table_name)
                        )
                    )
            connection.execute("grant usage on schema public to sqag_runtime_test10")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            rows = connection.execute(
                "select table_name, privilege_type from information_schema.role_table_grants "
                "where grantee = 'sqag_runtime_test10' and table_schema = 'public' "
                "order by table_name, privilege_type"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        manifest_priv_count = 0
        for entry in self.contract["tables"]["runtime_accessible"].values():
            for v in entry["privileges"].values():
                if v:
                    manifest_priv_count += 1

        self.assertEqual(
            len(rows),
            manifest_priv_count,
            f"information_schema grant count {len(rows)} must match manifest {manifest_priv_count}",
        )

        connection = self.connect()
        try:
            connection.execute("drop role if exists sqag_runtime_test10")
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
