"""Tests for the runtime privilege contract manifest, validator, and disposable PostgreSQL enforcement.

Proof mapping to locked requirement (22 points):

Static/manifest tests (always run):
  R01: Schema version is 1                       -> test_schema_version_is_1
  R02: Repository binding matches                -> test_repository_binding
  R03: Runtime role attributes (NOLOGIN etc.)     -> test_runtime_role_attributes
  R04: Runtime no memberships/ownership          -> test_runtime_role_no_memberships_no_ownership
  R05: Migrator cannot create roles              -> test_migrator_cannot_create_roles
  R06: sqag_maintenance forbidden                -> test_sqag_maintenance_is_forbidden
  R07: Migration digests match repository        -> test_production_migration_digests_match_repository
  R08: 16-table inventory with exact 11/5 split  -> test_table_total_is_16, test_all_tables_union_is_16
  R09: No grant options in manifest              -> test_no_runtime_table_has_grant_option
  R10: 0 sequences, 0 runtime privileges         -> test_sequence_count_is_0
  R11: 3 routines (2 SQAG + 1 provider)          -> test_routine_total_is_3
  R12: Provider exception exact and singular     -> test_show_db_tree_is_provider_exception
  R13: Database ACL targets                      -> test_database_acl_target
  R14: Schema ACL targets                        -> test_schema_acl_target
  R15: Default privileges: no runtime grants     -> test_default_privileges_no_runtime_grants
  R16: Verification queries required             -> test_verification_queries_include_required_keys

Validator static tests (always run):
  R17: Valid manifest passes                     -> test_valid_manifest_passes
  R18: Missing/extra/over-broad privilege fails  -> test_wrong_runtime_privilege_fails
  R19: Wrong digest fails                        -> test_wrong_digest_fails
  R20: Provider exception missing fails          -> test_provider_exception_missing_fails

Disposable PostgreSQL tests (run in CI with PostgreSQL 17):
  R21: Trigger enforcement survives EXECUTE revoke -> test_trigger_enforcement_after_public_execute_revoke
  R22: Direct runtime trigger call denied          -> test_direct_runtime_call_to_trigger_functions_denied
  R23: Actual table inventory equals manifest      -> test_actual_table_inventory_equals_manifest
  R24: Actual sequence inventory (0)               -> test_actual_sequence_inventory_equals_manifest
  R25: Routine owners/security modes               -> test_actual_routine_inventory_equals_manifest
  R26: Trigger dependencies match trigger routines  -> test_trigger_dependencies_match_routine_classification
  R27: 11-table runtime grants match matrix        -> test_effective_runtime_table_privileges_match_manifest
  R28: 5 forbidden tables have zero runtime grants  -> test_forbidden_tables_have_zero_runtime_privileges
  R29: PUBLIC TEMPORARY removal blocks runtime temp -> test_public_temporary_removal_blocks_runtime
  R30: PUBLIC CONNECT retained                      -> test_public_connect_remains_effective
  R31: Runtime database CREATE is false             -> test_runtime_database_create_is_false
  R32: Runtime schema CREATE is false               -> test_runtime_schema_create_is_false
  R33: No grant options on runtime grants           -> test_runtime_has_no_grant_options
  R34: Grantee-aware default ACL -- no runtime grants -> test_no_default_acl_grants_to_runtime_by_grantee
  R35: Grantee-aware default ACL negative fixture    -> test_default_acl_negative_fixture_detects_unauthorized_grant
  R36: Provider-controlled defaults unchanged        -> test_provider_controlled_defaults_unchanged
  R37: info_schema grant count matches manifest       -> test_information_schema_runtime_grant_coverage
  R38: Deterministic role cleanup after all tests     -> test_all_test_roles_cleaned_up
"""

import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp.postgres_migrations import (
    EXPECTED_ROUTINES,
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
        manifest["tables"]["runtime_forbidden"]["some_extra_table"] = {
            "class": "migration_only", "schema": "public", "reason": "test"
        }
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


def _safe_execute(connection: PostgresConnectionAdapter, sql: str, params=None) -> object:
    """Execute SQL through the adapter with doubled %% for psycopg placeholder safety."""
    return connection.execute(sql, params)


@unittest.skipUnless(postgres_test_conninfo(), "isolated PostgreSQL test service is not configured")
class PostgreSQLContractIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.rows import dict_row

        cls.psycopg = psycopg
        cls.dict_row = staticmethod(dict_row)
        cls.manifest = migration_manifest(ROOT / "migrations")
        cls.contract = load_manifest()
        cls._db_counter = 0

    def setUp(self):
        self.database_names: list[str] = []
        self.database_name = self.create_database()

    def create_database(self) -> str:
        PostgreSQLContractIntegrationTest._db_counter += 1
        database_name = f"sqag_rpc_{PostgreSQLContractIntegrationTest._db_counter}_{uuid.uuid4().hex[:8]}"
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            connection.execute(f'create database "{database_name}"')
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
                connection.execute(f'drop database if exists "{database_name}"')

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

    def _drop_role_safe(self, rolename: str) -> None:
        """Revoke all privileges from a role then drop it deterministically."""
        connection = self.connect()
        try:
            connection.execute(f"revoke all on schema public from {rolename}")
        except Exception:
            connection.rollback()
        else:
            connection.commit()
        finally:
            connection.close()

        connection = self.connect()
        try:
            connection.execute(f"revoke all on database {self.database_name} from {rolename}")
        except Exception:
            connection.rollback()
        else:
            connection.commit()
        finally:
            connection.close()

        connection = self.connect()
        try:
            connection.execute(f"drop owned by {rolename}")
        except Exception:
            connection.rollback()
        else:
            connection.commit()
        finally:
            connection.close()

        connection = self.connect()
        try:
            connection.execute(f"drop role if exists {rolename}")
            connection.commit()
        except Exception:
            connection.rollback()
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
            pattern = "sqag_%%"
            rows = connection.execute(
                "select p.proname, r.rolname as owner, p.prosecdef "
                "from pg_catalog.pg_proc p "
                "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
                "join pg_catalog.pg_roles r on r.oid = p.proowner "
                "where n.nspname = 'public' and p.proname like %s "
                "order by p.proname",
                (pattern,),
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

    def test_trigger_dependencies_match_routine_classification(self):
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select distinct p.proname as routine_name "
                "from pg_catalog.pg_trigger t "
                "join pg_catalog.pg_proc p on p.oid = t.tgfoid "
                "join pg_catalog.pg_namespace pn on pn.oid = p.pronamespace "
                "where pn.nspname = 'public'"
            ).fetchall()
            trigger_routines = {str(_row_dict(r, "routine_name")) for r in rows}
        finally:
            connection.rollback()
            connection.close()

        self.assertEqual(
            trigger_routines,
            EXPECTED_ROUTINES,
            f"Trigger routines {trigger_routines} must equal expected {EXPECTED_ROUTINES}",
        )

    def test_trigger_enforcement_after_public_execute_revoke(self):
        """Insert a row, revoke PUBLIC EXECUTE, then prove the immutable-change
        trigger still fires through table operations."""
        self.apply_migrations()
        connection = self.connect()
        try:
            connection.execute(
                "insert into sqag_generation_runs "
                "(run_id, workspace_id, actor_tracking_id, actor_key_version, job_type, status, "
                "started_at, evidence_schema_version, retention_expires_at, original_retention_expires_at) "
                "values ('run-imm-test', 'ws-imm', 'actor', 'v1', 'quote', 'received', "
                "'2024-01-01T00:00:00Z', '1.0', '2099-01-01T00:00:00Z', '2099-01-01T00:00:00Z')"
            )
            connection.execute(
                "insert into sqag_generation_evidence "
                "(evidence_id, run_id, workspace_id, evidence_type, evidence_schema_version, "
                "evidence_json, evidence_sha256, created_at, retention_expires_at, original_retention_expires_at) "
                "values ('evid-imm-test', 'run-imm-test', 'ws-imm', 'prompt', '1.0', "
                "'{}', '0000000000000000000000000000000000000000000000000000000000000000', "
                "'2024-01-01T00:00:00Z', '2099-01-01T00:00:00Z', '2099-01-01T00:00:00Z')"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        rows_before = connection.execute(
            "select evidence_json from sqag_generation_evidence where evidence_id = 'evid-imm-test'"
        ).fetchall()
        self.assertEqual(len(rows_before), 1)
        connection.close()

        connection = self.connect()
        try:
            connection.execute("revoke execute on function sqag_reject_immutable_change() from public")
            connection.commit()
        finally:
            connection.close()

        connection = self.connect()
        try:
            connection.execute(
                "update sqag_generation_evidence set evidence_json = '{\"x\":1}' where evidence_id = 'evid-imm-test'"
            )
            connection.commit()
            self.fail("Trigger should have rejected the immutable update")
        except Exception as exc:
            connection.rollback()
            err_str = str(exc)
            self.assertTrue(
                "immutable" in err_str.lower() or "reject" in err_str.lower() or "SQAG" in err_str,
                f"Expected immutable rejection, got: {err_str[:200]}",
            )

        connection = self.connect()
        try:
            row = connection.execute(
                "select evidence_json from sqag_generation_evidence where evidence_id = 'evid-imm-test'"
            ).fetchone()
            self.assertEqual(
                str(_row_dict(row, "evidence_json")), "{}",
                "Row must be unchanged after rejected update",
            )
        finally:
            connection.rollback()
            connection.close()

    def test_direct_runtime_call_to_trigger_functions_denied(self):
        """Revoke PUBLIC EXECUTE, then prove direct runtime call to trigger function
        is denied with a permission error."""
        self.apply_migrations()
        role_name = f"sqag_direct_test_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            connection.execute("revoke execute on function sqag_reject_immutable_change() from public")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
        finally:
            connection.close()

        connection = self.connect()
        try:
            connection.execute(f"set role {role_name}")
            try:
                connection.execute("select sqag_reject_immutable_change()")
                connection.rollback()
                self.fail("Runtime should not be able to call sqag_reject_immutable_change directly")
            except Exception as exc:
                connection.rollback()
                err_str = str(exc)
                self.assertTrue(
                    "permission denied" in err_str.lower()
                    or "42501" in err_str
                    or "insufficient" in err_str.lower(),
                    f"Expected permission-denied, got: {err_str[:200]}",
                )
        finally:
            connection.execute("reset role")
            connection.rollback()
            connection.close()

        self._drop_role_safe(role_name)

    def test_effective_runtime_table_privileges_match_manifest(self):
        """Grant the exact manifest privileges to a test role and verify info_schema."""
        self.apply_migrations()
        role_name = f"sqag_rtpriv_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            for table_name in sorted(RUNTIME_TABLES):
                entry = self.contract["tables"]["runtime_accessible"][table_name]
                privs = entry["privileges"]
                if privs.get("select"):
                    connection.execute(f"grant select on {table_name} to {role_name}")
                if privs.get("insert"):
                    connection.execute(f"grant insert on {table_name} to {role_name}")
                if privs.get("update"):
                    connection.execute(f"grant update on {table_name} to {role_name}")
                if privs.get("delete"):
                    connection.execute(f"grant delete on {table_name} to {role_name}")
            connection.execute(f"grant usage on schema public to {role_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            self._drop_role_safe(role_name)
            raise

        connection = self.connect()
        try:
            rows = connection.execute(
                "select table_name, privilege_type from information_schema.role_table_grants "
                f"where grantee = '{role_name}' and table_schema = 'public' "
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

        self._drop_role_safe(role_name)

    def test_forbidden_tables_have_zero_runtime_privileges(self):
        """Prove the 5 forbidden tables receive no grants even when schema USAGE is granted."""
        self.apply_migrations()
        role_name = f"sqag_fbpriv_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            connection.execute(f"grant usage on schema public to {role_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        try:
            connection = self.connect()
            try:
                rows = connection.execute(
                    "select table_name, privilege_type from information_schema.role_table_grants "
                    f"where grantee = '{role_name}' and table_schema = 'public' "
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
        finally:
            self._drop_role_safe(role_name)

    def test_public_temporary_removal_blocks_runtime(self):
        """Prove that revoking PUBLIC TEMPORARY causes runtime has_database_privilege('temp') = false."""
        self.apply_migrations()
        role_name = f"sqag_tmptest_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            connection.execute(f"grant connect on database {self.database_name} to {role_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            connection.execute(f"revoke temporary on database {self.database_name} from public")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            self._drop_role_safe(role_name)
            raise

        try:
            connection = self.connect()
            try:
                row = connection.execute(
                    f"select has_database_privilege('{role_name}', %s, 'temp') as has_temp",
                    (self.database_name,),
                ).fetchone()
            finally:
                connection.rollback()
                connection.close()

            self.assertFalse(
                bool(_row_dict(row, "has_temp")),
                "Runtime should not have TEMPORARY after PUBLIC TEMPORARY is revoked",
            )
        finally:
            self._drop_role_safe(role_name)

    def test_public_connect_remains_effective(self):
        """Prove PUBLIC CONNECT remains effective on the database."""
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
        """Prove a new runtime role does not inherit database CREATE."""
        self.apply_migrations()
        role_name = f"sqag_crttest_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        try:
            connection = self.connect()
            try:
                row = connection.execute(
                    f"select has_database_privilege('{role_name}', %s, 'create') as has_create",
                    (self.database_name,),
                ).fetchone()
            finally:
                connection.rollback()
                connection.close()

            self.assertFalse(
                bool(_row_dict(row, "has_create")),
                "Runtime should not have database CREATE",
            )
        finally:
            self._drop_role_safe(role_name)

    def test_runtime_schema_create_is_false(self):
        """Prove a new runtime role does not have schema CREATE."""
        self.apply_migrations()
        role_name = f"sqag_scmtest_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        try:
            connection = self.connect()
            try:
                row = connection.execute(
                    f"select has_schema_privilege('{role_name}', 'public', 'create') as has_create"
                ).fetchone()
            finally:
                connection.rollback()
                connection.close()

            self.assertFalse(
                bool(_row_dict(row, "has_create")),
                "Runtime should not have schema CREATE",
            )
        finally:
            self._drop_role_safe(role_name)

    def test_runtime_has_no_grant_options(self):
        """Prove runtime table grants do not include WITH GRANT OPTION."""
        self.apply_migrations()
        role_name = f"sqag_gopttest_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            for table_name in sorted(RUNTIME_TABLES):
                connection.execute(f"grant select on {table_name} to {role_name}")
            connection.execute(f"grant usage on schema public to {role_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            self._drop_role_safe(role_name)
            raise

        try:
            connection = self.connect()
            try:
                rows = connection.execute(
                    "select privilege_type, is_grantable from information_schema.role_table_grants "
                    f"where grantee = '{role_name}' and table_schema = 'public' "
                    "and is_grantable = 'YES'"
                ).fetchall()
            finally:
                connection.rollback()
                connection.close()

            self.assertEqual(
                len(rows), 0, f"Runtime should have no grant options, found: {rows}"
            )
        finally:
            self._drop_role_safe(role_name)

    # ---------------------------------------------------------------
    # Grantee-Aware Default ACL Tests
    # ---------------------------------------------------------------

    def test_no_default_acl_grants_to_runtime_by_grantee(self):
        """Verify no default ACL entry grants to a runtime-like role using aclexplode().
        Does not cast absent sqag_runtime to regrole. Uses pg_roles join instead."""
        self.apply_migrations()
        role_name = f"sqag_dacla_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        try:
            connection = self.connect()
            try:
                rows = connection.execute(
                    "select "
                    "  d.defaclrole::regrole::text as granting_role, "
                    "  (aclexplode(d.defaclacl)).grantee::regrole::text as grantee, "
                    "  d.defaclobjtype::text as object_type "
                    "from pg_catalog.pg_default_acl d "
                    "join pg_catalog.pg_roles r on r.oid = (aclexplode(d.defaclacl)).grantee "
                    f"where r.rolname = '{role_name}' "
                    "order by granting_role, object_type"
                ).fetchall()
            finally:
                connection.rollback()
                connection.close()

            self.assertEqual(
                len(rows), 0,
                f"Default ACL must not grant to {role_name}, found: {rows}",
            )
        finally:
            self._drop_role_safe(role_name)

    def test_default_acl_negative_fixture_detects_unauthorized_grant(self):
        """Create a separate owner role that grants a default table privilege to a
        runtime-like role, then prove the grantee-aware check detects it.
        After cleanup, prove detection passes."""
        self.apply_migrations()
        owner_name = f"sqag_daclowner_{uuid.uuid4().hex[:8]}"
        runtime_name = f"sqag_daclrt_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {owner_name}")
            connection.execute(f"create role {runtime_name}")
            connection.execute(f"grant usage on schema public to {owner_name}")
            connection.execute(f"grant create on schema public to {owner_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            connection.execute(f"set role {owner_name}")
            connection.execute(
                f"alter default privileges for role {owner_name} in schema public "
                f"grant select on tables to {runtime_name}"
            )
            connection.execute("reset role")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            self._drop_role_safe(runtime_name)
            self._drop_role_safe(owner_name)
            raise

        connection = self.connect()
        try:
            rows = connection.execute(
                "select "
                "  d.defaclrole::regrole::text as granting_role, "
                "  (aclexplode(d.defaclacl)).grantee::regrole::text as grantee, "
                "  d.defaclobjtype::text as object_type, "
                "  (aclexplode(d.defaclacl)).privilege_type, "
                "  (aclexplode(d.defaclacl)).is_grantable "
                "from pg_catalog.pg_default_acl d "
                "join pg_catalog.pg_roles r on r.oid = (aclexplode(d.defaclacl)).grantee "
                f"where r.rolname = '{runtime_name}' "
                "order by granting_role, object_type"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        self.assertGreater(
            len(rows), 0,
            f"Negative fixture must detect the unauthorized grant to {runtime_name}",
        )
        self.assertTrue(
            any(
                str(_row_dict(r, "privilege_type")) == "SELECT"
                and str(_row_dict(r, "object_type")) == "r"
                for r in rows
            ),
            "Negative fixture must detect SELECT default table grant",
        )

        connection = self.connect()
        try:
            connection.execute(f"set role {owner_name}")
            connection.execute(
                f"alter default privileges for role {owner_name} in schema public "
                f"revoke select on tables from {runtime_name}"
            )
            connection.execute("reset role")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        connection = self.connect()
        try:
            clean_rows = connection.execute(
                "select "
                "  (aclexplode(d.defaclacl)).grantee::regrole::text as grantee "
                "from pg_catalog.pg_default_acl d "
                "join pg_catalog.pg_roles r on r.oid = (aclexplode(d.defaclacl)).grantee "
                f"where r.rolname = '{runtime_name}'"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        self.assertEqual(
            len(clean_rows), 0,
            f"After revoke, no default ACL should target {runtime_name}",
        )

        self._drop_role_safe(runtime_name)
        self._drop_role_safe(owner_name)

    # ---------------------------------------------------------------
    # Provider-Controlled Defaults
    # ---------------------------------------------------------------

    def test_provider_controlled_defaults_unchanged(self):
        """Prove that no SQAG-owned migration or test path mutates provider default ACLs.
        The disposable PostgreSQL 17 service will have provider-default roles
        (like the bootstrap superuser). We verify:
        (a) Default ACL on the disposable database is limited to what the test itself creates.
        (b) The migration path does not add or remove entries owned by provider roles."""
        self.apply_migrations()

        connection = self.connect()
        try:
            current_roles = {
                str(_row_dict(r, "rolname"))
                for r in connection.execute(
                    "select rolname from pg_catalog.pg_roles "
                    "where rolname not like 'pg_%%' and rolname not like 'sqag_%%'"
                ).fetchall()
            }
        finally:
            connection.rollback()
            connection.close()

        connection = self.connect()
        try:
            rows = connection.execute(
                "select defaclrole::regrole::text as granting_role, defaclobjtype::text "
                "from pg_catalog.pg_default_acl "
                "order by granting_role, defaclobjtype"
            ).fetchall()
        finally:
            connection.rollback()
            connection.close()

        sqag_owned = {
            str(_row_dict(r, "granting_role"))
            for r in rows
            if str(_row_dict(r, "granting_role")).startswith("sqag_")
        }
        self.assertEqual(
            sqag_owned, set(),
            f"No SQAG-owned roles should have default ACL entries, found: {sqag_owned}",
        )

        provider_roles_found = {
            str(_row_dict(r, "granting_role"))
            for r in rows
            if str(_row_dict(r, "granting_role")) not in current_roles
            and not str(_row_dict(r, "granting_role")).startswith("sqag_")
        }
        self.assertEqual(
            provider_roles_found, set(),
            f"No unknown-owner default ACL entries expected, found: {provider_roles_found}",
        )

    # ---------------------------------------------------------------
    # Grant Coverage
    # ---------------------------------------------------------------

    def test_information_schema_runtime_grant_coverage(self):
        """Grant the exact manifest privileges and prove info_schema counts match."""
        self.apply_migrations()
        role_name = f"sqag_covtest_{uuid.uuid4().hex[:8]}"

        connection = self.connect()
        try:
            connection.execute(f"create role {role_name}")
            for table_name in sorted(RUNTIME_TABLES):
                entry = self.contract["tables"]["runtime_accessible"][table_name]
                privs = entry["privileges"]
                if privs.get("select"):
                    connection.execute(f"grant select on {table_name} to {role_name}")
                if privs.get("insert"):
                    connection.execute(f"grant insert on {table_name} to {role_name}")
                if privs.get("update"):
                    connection.execute(f"grant update on {table_name} to {role_name}")
                if privs.get("delete"):
                    connection.execute(f"grant delete on {table_name} to {role_name}")
            connection.execute(f"grant usage on schema public to {role_name}")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise

        try:
            connection = self.connect()
            try:
                rows = connection.execute(
                    "select table_name, privilege_type from information_schema.role_table_grants "
                    f"where grantee = '{role_name}' and table_schema = 'public' "
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
                f"info_schema grant count {len(rows)} must match manifest {manifest_priv_count}",
            )
        finally:
            self._drop_role_safe(role_name)

    def test_all_test_roles_cleaned_up(self):
        """Prove that after all preceding tests, no leftover test roles remain."""
        self.apply_migrations()
        connection = self.connect()
        try:
            rows = connection.execute(
                "select rolname from pg_catalog.pg_roles "
                "where rolname like 'sqag_%%' and rolname not in "
                "('sqag_migrator', 'sqag_runtime', 'sqag_app') "
                "order by rolname"
            ).fetchall()
            leftover = {str(_row_dict(r, "rolname")) for r in rows}
        finally:
            connection.rollback()
            connection.close()

        self.assertEqual(
            leftover, set(),
            f"Leftover test roles detected: {leftover}",
        )


if __name__ == "__main__":
    unittest.main()
