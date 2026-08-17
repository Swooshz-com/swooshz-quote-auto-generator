from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import validate_runtime_privilege_contract as contract
from webapp import server as webapp
from scripts import preflight_sqag_migrations as preflight
from webapp.forensics import ForensicStore
from webapp.postgres_migrations import (
    EXPECTED_INDEXES,
    EXPECTED_TABLES,
    MigrationSafetyError,
    apply_postgres_migrations,
    inspect_postgres_migrations,
    migration_manifest,
)


def postgres_test_conninfo(database_name: str = "postgres") -> str | None:
    host = os.getenv("SQAG_TEST_POSTGRES_HOST", "").strip()
    port = os.getenv("SQAG_TEST_POSTGRES_PORT", "").strip()
    user = os.getenv("SQAG_TEST_POSTGRES_USER", "").strip()
    if not host or not port or not user:
        return None
    return f"host={host} port={port} user={user} dbname={database_name}"


def postgres_test_enabled() -> bool:
    return bool(postgres_test_conninfo()) and importlib.util.find_spec("psycopg") is not None


def safe_postgres_url(user: str, database_name: str) -> str:
    conninfo = postgres_test_conninfo(database_name)
    if not conninfo:
        raise unittest.SkipTest("disposable PostgreSQL-17 service is not configured")
    parts = {
        item.split("=", 1)[0]: item.split("=", 1)[1]
        for item in conninfo.split()
        if "=" in item
    }
    return (
        f"postgresql://{quote(user, safe='')}@{quote(parts['host'], safe='')}:{quote(parts['port'], safe='')}/"
        f"{quote(database_name, safe='')}"
    )


def _sql_grantee(sql_module, name: str):
    return sql_module.SQL("PUBLIC") if name == "PUBLIC" else sql_module.Identifier(name)


class RuntimePrivilegeContractStaticTest(unittest.TestCase):
    def test_manifest_is_strict_a25_contract_without_source_digest_mirrors(self):
        manifest = contract.validate_manifest()
        self.assertEqual(manifest["schema_version"], 2)
        self.assertNotIn("canonical_source_revision", manifest)
        self.assertNotIn("canonical_source_tree", manifest)
        self.assertNotIn("implementation_registry", manifest)
        self.assertEqual(manifest["namespace"]["search_path"], ["public", "pg_catalog"])
        self.assertEqual(set(manifest["runtime_tables"]), set(contract.RUNTIME_TABLE_PRIVILEGES))
        self.assertEqual(set(manifest["maintenance_tables"]), set(contract.MAINTENANCE_TABLE_PRIVILEGES))

    def test_migration_report_missing_extra_and_checksum_states_are_red(self):
        valid = {
            "status": "ready",
            "safeToApply": True,
            "pendingMigrationIds": [],
            "blockers": [],
        }
        contract.validate_migration_report(valid)
        for mutation in (
            {"pendingMigrationIds": ["007_feedback_publication_binding_postgres.sql"]},
            {"blockers": ["unexpected_applied_migration"]},
            {"status": "unsafe"},
            {"safeToApply": False},
        ):
            candidate = dict(valid)
            candidate.update(mutation)
            with self.assertRaises(contract.RuntimePrivilegeContractError):
                contract.validate_migration_report(candidate)

    def test_server_major_and_membership_escalation_are_red(self):
        with self.assertRaises(contract.RuntimePrivilegeContractError):
            contract.validate_server_version(160000)
        with self.assertRaises(contract.RuntimePrivilegeContractError):
            contract.validate_server_version(180000)
        rows = [
            {
                "role": "sqag_migrator",
                "member": "sqag_runtime",
                "grantor": "postgres",
                "admin_option": True,
                "inherit_option": True,
                "set_option": True,
            }
        ]
        errors = contract.validate_runtime_membership_edges({}, rows)
        self.assertTrue(any(item.startswith("membership:unexpected") for item in errors))

    def test_source_binding_unknown_relation_and_duplicate_json_are_red(self):
        manifest = copy.deepcopy(contract.load_manifest())
        manifest["source_binding"]["allowed_sql_relations"].append("sqag_unreviewed")
        self.assertTrue(contract.validate_source_bindings(manifest))
        duplicate = '{"a": 1, "a": 2}'
        with self.assertRaises(contract.DuplicateKeyError):
            json.loads(duplicate, object_pairs_hook=contract._reject_duplicate_keys)

    def test_maintenance_projection_never_falls_back_to_runtime_database_url(self):
        values = {
            webapp.SQAG_DATABASE_URL_ENV_NAME: "postgresql://runtime.example/sqag",
            webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME: "",
        }

        def read_value(name: str) -> str:
            return values.get(name, "")

        with mock.patch.object(webapp, "read_dotenv_value", side_effect=read_value):
            self.assertEqual(webapp.configured_maintenance_database_url(), "")
        retention_source = (ROOT / "scripts" / "enforce_forensic_retention.py").read_text(encoding="utf-8")
        self.assertIn("configured_maintenance_database_url", retention_source)
        self.assertNotIn("configured_database_url()", retention_source)

    def test_fixed_search_path_and_sensitive_observation_boundaries_are_source_bound(self):
        server_source = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")
        verifier_source = (ROOT / "scripts" / "validate_runtime_privilege_contract.py").read_text(encoding="utf-8")
        self.assertIn('options="-c search_path=public,pg_catalog"', server_source)
        self.assertIn("SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME", server_source)
        self.assertNotIn("pg_catalog.pg_authid", verifier_source)
        self.assertNotIn("rolpassword", verifier_source)
        self.assertIn("aclexplode", verifier_source)
        self.assertIn("normalized_acl_provenance", (ROOT / "docs" / "runtime-privilege-contract.json").read_text(encoding="utf-8"))

    def test_maintenance_environment_name_is_redacted(self):
        source = "SQAG_MAINTENANCE_DATABASE_URL=postgresql://synthetic-redaction.example/db"
        redacted = webapp.scrub_sensitive_text(source)
        self.assertIn(webapp.SECRET_REDACTION, redacted)
        self.assertNotIn("synthetic-redaction.example", redacted)

    def test_preflight_requires_both_runtime_and_maintenance_projections(self):
        source = (ROOT / "scripts" / "preflight_sqag_migrations.py").read_text(encoding="utf-8")
        self.assertIn("configured_maintenance_database_url", source)
        self.assertIn("verify_postgres_privilege_contract", source)
        self.assertIn("validate_migration_report", source)

    def test_cleanup_unknown_is_fail_closed(self):
        residuals = [{"datname": "sqag_fixture_residual"}]
        with self.assertRaises(RuntimeError) as raised:
            if residuals:
                raise RuntimeError("CLEANUP_UNKNOWN")
        self.assertEqual(str(raised.exception), "CLEANUP_UNKNOWN")

    def test_cleanup_failure_and_residual_are_terminal(self):
        case = RuntimePrivilegeContractPostgresIntegrationTest("runTest")
        case.database_name = "sqag_cleanup_injected"
        case.created_roles = []
        case.database_created = True
        case.cleanup_done = False
        failing_connection = mock.MagicMock()
        failing_connection.__enter__.return_value = failing_connection
        failing_connection.execute.side_effect = RuntimeError("teardown injection")
        failing_driver = mock.Mock()
        failing_driver.connect.return_value = failing_connection
        case.psycopg = failing_driver
        with self.assertRaises(AssertionError) as raised:
            case._cleanup_fixture()
        self.assertIn("CLEANUP_UNKNOWN", str(raised.exception))

        case.cleanup_done = False
        case.database_created = False
        residual_connection = mock.MagicMock()
        residual_connection.__enter__.return_value = residual_connection
        residual_database = mock.Mock()
        residual_database.fetchone.return_value = {"datname": case.database_name}
        residual_roles = mock.Mock()
        residual_roles.fetchall.return_value = [{"rolname": "sqag_cleanup_role"}]
        residual_connection.execute.side_effect = [residual_database, residual_roles]
        residual_driver = mock.Mock()
        residual_driver.connect.return_value = residual_connection
        case.psycopg = residual_driver
        with self.assertRaises(AssertionError) as raised:
            case._cleanup_fixture()
        self.assertIn("CLEANUP_UNKNOWN", str(raised.exception))


class _FakeIdentityCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeIdentityRawConnection:
    def __init__(self, current_user: str):
        self.current_user = current_user
        self.statements: list[str] = []
        self.rollback_calls = 0
        self.closed = False

    def execute(self, sql, params=()):
        _ = params
        self.statements.append(sql)
        if sql == "select current_user as role":
            return _FakeIdentityCursor({"role": self.current_user})
        return _FakeIdentityCursor({"ok": True})

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.closed = True


class RuntimeSessionIdentityBindingTest(unittest.TestCase):
    DATABASE_URL = "postgresql://sqag_runtime:synthetic-secret@db.example.test/sqag"

    def _preflight(self, current_user: str, *, maintenance: bool = False):
        raw = _FakeIdentityRawConnection(current_user)
        connect = lambda _database_url: raw
        with (
            mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect),
            mock.patch.object(preflight, "inspect_postgres_migrations", return_value={"status": "ready"}),
            mock.patch.object(preflight, "validate_migration_report"),
            mock.patch.object(preflight, "verify_postgres_privilege_contract", return_value={"status": "verified"}),
        ):
            result = preflight._inspect(
                self.DATABASE_URL,
                [],
                {},
                require_maintenance_role=maintenance,
            )
        return result, raw

    def _runtime_storage(self, current_user: str):
        raw = _FakeIdentityRawConnection(current_user)
        connect = lambda _database_url: raw
        with mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect):
            storage = webapp.DatabaseSqagStorage(
                self.DATABASE_URL,
                "workspace-runtime-identity",
                expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
            )
            with storage.connection() as connection:
                connection.execute("select application_sql")
        return raw

    def test_runtime_preflight_accepts_exact_server_authoritative_identity(self):
        result, raw = self._preflight(webapp.SQAG_RUNTIME_DATABASE_ROLE)
        self.assertEqual(result[0]["status"], "ready")
        self.assertEqual(result[1]["status"], "verified")
        self.assertEqual(
            raw.statements[:2],
            ["select current_user as role", "set transaction read only"],
        )
        self.assertEqual(raw.rollback_calls, 1)
        self.assertTrue(raw.closed)

    def test_runtime_preflight_rejects_admin_migrator_and_maintenance_identities(self):
        for current_user in ("postgres", "sqag_migrator", "sqag_maintenance"):
            with self.subTest(current_user=current_user):
                with self.assertRaises(webapp.SqagStorageAccessError):
                    self._preflight(current_user)
                _, raw = self._preflight_rejected(current_user)
                self.assertEqual(raw.statements, ["select current_user as role"])
                self.assertEqual(raw.rollback_calls, 0)
                self.assertTrue(raw.closed)

    def _preflight_rejected(self, current_user: str):
        raw = _FakeIdentityRawConnection(current_user)
        connect = lambda _database_url: raw
        with (
            mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect),
            mock.patch.object(preflight, "inspect_postgres_migrations"),
            mock.patch.object(preflight, "validate_migration_report"),
            mock.patch.object(preflight, "verify_postgres_privilege_contract"),
        ):
            with self.assertRaises(webapp.SqagStorageAccessError):
                preflight._inspect(self.DATABASE_URL, [], {})
        return None, raw

    def test_runtime_storage_accepts_exact_identity_before_application_sql(self):
        raw = self._runtime_storage(webapp.SQAG_RUNTIME_DATABASE_ROLE)
        self.assertEqual(
            raw.statements,
            ["select current_user as role", "select application_sql"],
        )
        self.assertTrue(raw.closed)

    def test_runtime_storage_rejects_non_runtime_identity_before_application_sql(self):
        for current_user in ("postgres", "sqag_migrator", "sqag_maintenance"):
            with self.subTest(current_user=current_user):
                raw = _FakeIdentityRawConnection(current_user)
                connect = lambda _database_url: raw
                with mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect):
                    storage = webapp.DatabaseSqagStorage(
                        self.DATABASE_URL,
                        "workspace-runtime-identity",
                        expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
                    )
                    with self.assertRaises(webapp.SqagStorageAccessError) as raised:
                        with storage.connection():
                            self.fail("application SQL must not run after a runtime identity mismatch")
                self.assertEqual(raw.statements, ["select current_user as role"])
                self.assertTrue(raw.closed)
                message = str(raised.exception)
                self.assertNotIn(self.DATABASE_URL, message)
                self.assertNotIn("db.example.test", message)
                self.assertNotIn("synthetic-secret", message)
                self.assertNotIn(current_user, message)

    def test_runtime_identity_binding_is_fixed_and_covers_application_paths(self):
        source = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")
        self.assertIn('SQAG_RUNTIME_DATABASE_ROLE = "sqag_runtime"', source)
        self.assertEqual(source.count("expected_session_role=SQAG_RUNTIME_DATABASE_ROLE"), 4)
        self.assertIn(
            "return postgres_storage_connection(self.database_url, expected_role=self.expected_session_role)",
            source,
        )
        self.assertNotIn("SQAG_RUNTIME_DATABASE_ROLE_ENV_NAME", source)
        self.assertNotIn("read_dotenv_value(SQAG_RUNTIME_DATABASE_ROLE", source)

    def test_maintenance_preflight_accepts_only_exact_maintenance_identity(self):
        result, raw = self._preflight(
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
            maintenance=True,
        )
        self.assertEqual(result[0]["status"], "ready")
        self.assertEqual(
            raw.statements[:2],
            ["select current_user as role", "set transaction read only"],
        )
        with self.assertRaises(webapp.SqagStorageAccessError):
            self._preflight(webapp.SQAG_RUNTIME_DATABASE_ROLE, maintenance=True)

@unittest.skipUnless(
    postgres_test_enabled(),
    "real disposable PostgreSQL-17 service is not configured",
)
class RuntimePrivilegeContractPostgresIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        cls.psycopg = psycopg
        cls.sql = sql
        cls.dict_row = dict_row
        cls.manifest = migration_manifest(ROOT / "migrations")

    def setUp(self):
        self.database_name = "sqag_a25_runtime_" + uuid.uuid4().hex
        self.roles = ("sqag_runtime", "sqag_migrator", "sqag_maintenance")
        self.created_roles: list[str] = []
        self.database_created = False
        self.cleanup_done = False
        self.addCleanup(self._cleanup_fixture)
        self.fixture_ready = False
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            existing_database = connection.execute(
                "select 1 from pg_catalog.pg_database where datname = %s",
                (self.database_name,),
            ).fetchone()
            existing_roles = connection.execute(
                "select rolname from pg_catalog.pg_roles where rolname = any(%s)",
                (list(self.roles),),
            ).fetchall()
            if existing_database or existing_roles:
                raise RuntimeError("CLEANUP_UNKNOWN")
            connection.execute(
                self.sql.SQL("create database {}").format(
                    self.sql.Identifier(self.database_name)
                )
            )
            self.database_created = True
            for role in self.roles:
                connection.execute(
                    self.sql.SQL(
                        "create role {} login nosuperuser nocreatedb nocreaterole "
                        "noreplication nobypassrls noinherit connection limit -1"
                    ).format(self.sql.Identifier(role))
                )
                self.created_roles.append(role)

        with self._admin_connection() as connection:
            before_grant = connection.execute(
                "select has_schema_privilege(%s, %s, %s)",
                ("sqag_migrator", "public", "CREATE"),
            ).fetchone()
            self.assertFalse(before_grant["has_schema_privilege"])
            connection.execute("grant usage, create on schema public to sqag_migrator")
            after_grant = connection.execute(
                "select has_schema_privilege(%s, %s, %s)",
                ("sqag_migrator", "public", "CREATE"),
            ).fetchone()
            self.assertTrue(after_grant["has_schema_privilege"])
        migrator_url = safe_postgres_url("sqag_migrator", self.database_name)
        with webapp.postgres_storage_connection(migrator_url) as connection:
            result = apply_postgres_migrations(connection, self.manifest)
            connection.commit()
        self.assertEqual(result["expectedHead"], self.manifest[-1].migration_id)
        self._configure_acl_contract()
        self.fixture_ready = True

    def _cleanup_fixture(self):
        if self.cleanup_done:
            return
        self.cleanup_done = True
        cleanup_error = None
        try:
            with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
                if self.database_created:
                    connection.execute(
                        "select pg_catalog.pg_terminate_backend(pid) "
                        "from pg_catalog.pg_stat_activity "
                        "where datname = %s and pid <> pg_catalog.pg_backend_pid()",
                        (self.database_name,),
                    )
                    connection.execute(
                        self.sql.SQL("drop database if exists {}").format(
                            self.sql.Identifier(self.database_name)
                        )
                    )
                for role in self.created_roles:
                    connection.execute(
                        self.sql.SQL("drop role if exists {}").format(
                            self.sql.Identifier(role)
                        )
                    )
                residual_database = connection.execute(
                    "select 1 from pg_catalog.pg_database where datname = %s",
                    (self.database_name,),
                ).fetchone()
                residual_roles = connection.execute(
                    "select 1 from pg_catalog.pg_roles where rolname = any(%s)",
                    (self.created_roles,),
                ).fetchall()
                if residual_database or residual_roles:
                    cleanup_error = "CLEANUP_UNKNOWN"
        except Exception:
            cleanup_error = "CLEANUP_UNKNOWN"
        if cleanup_error:
            self.fail(cleanup_error)

    def _admin_connection(self):
        return self.psycopg.connect(
            safe_postgres_url("postgres", self.database_name),
            row_factory=self.dict_row,
            autocommit=True,
        )

    def _role_connection(self, role: str):
        return self.psycopg.connect(
            safe_postgres_url(role, self.database_name),
            row_factory=self.dict_row,
        )

    def _configure_acl_contract(self):
        tables = sorted(set(EXPECTED_TABLES) | {contract.LEDGER_TABLE})
        with self._admin_connection() as connection:
            database = self.sql.Identifier(self.database_name)
            for grantee in ("PUBLIC", *self.roles):
                connection.execute(
                    self.sql.SQL("revoke all privileges on database {} from {}").format(
                        database, _sql_grantee(self.sql, grantee)
                    )
                )
            connection.execute(
                self.sql.SQL("grant connect on database {} to PUBLIC").format(database)
            )
            for role in self.roles:
                connection.execute(
                    self.sql.SQL("grant connect on database {} to {}").format(
                        database, self.sql.Identifier(role)
                    )
                )
            connection.execute("revoke all privileges on schema public from PUBLIC")
            connection.execute("grant usage on schema public to PUBLIC")
            connection.execute("grant usage on schema public to sqag_runtime, sqag_maintenance")
            connection.execute("grant usage, create on schema public to sqag_migrator")
            for table in tables:
                identifier = self.sql.Identifier(table)
                for grantee in ("PUBLIC", "sqag_runtime", "sqag_maintenance"):
                    connection.execute(
                        self.sql.SQL("revoke all privileges on table public.{} from {}").format(
                            identifier, _sql_grantee(self.sql, grantee)
                        )
                    )
                runtime_privileges = contract.RUNTIME_TABLE_PRIVILEGES.get(table, ())
                maintenance_privileges = contract.MAINTENANCE_TABLE_PRIVILEGES.get(table, ())
                if runtime_privileges:
                    connection.execute(
                        self.sql.SQL("grant {} on table public.{} to sqag_runtime").format(
                            self.sql.SQL(", ").join(self.sql.SQL(item) for item in runtime_privileges),
                            identifier,
                        )
                    )
                if maintenance_privileges:
                    connection.execute(
                        self.sql.SQL("grant {} on table public.{} to sqag_maintenance").format(
                            self.sql.SQL(", ").join(self.sql.SQL(item) for item in maintenance_privileges),
                            identifier,
                        )
                    )
            connection.execute("revoke all privileges on all sequences in schema public from PUBLIC, sqag_runtime, sqag_maintenance")
            connection.execute("revoke all privileges on all functions in schema public from PUBLIC, sqag_runtime, sqag_maintenance")
            for routine, _identity_arguments in contract.EXPECTED_ROUTINE_KEYS:
                connection.execute(
                    self.sql.SQL("grant execute on function public.{}() to sqag_migrator").format(
                        self.sql.Identifier(routine)
                    )
                )
            connection.execute(
                "alter default privileges for role sqag_migrator in schema public "
                "revoke all on tables from PUBLIC"
            )
            connection.execute(
                "alter default privileges for role sqag_migrator in schema public "
                "revoke all on functions from PUBLIC"
            )

    def _verify(self):
        with webapp.postgres_storage_connection(
            safe_postgres_url("postgres", self.database_name)
        ) as connection:
            return contract.verify_postgres_privilege_contract(connection, self.manifest)

    def _inspect(self):
        with webapp.postgres_storage_connection(
            safe_postgres_url("postgres", self.database_name)
        ) as connection:
            return inspect_postgres_migrations(connection, self.manifest)

    def _red_then_restore(self, mutate, restore, expected_label: str):
        mutate()
        try:
            with self.assertRaises(contract.RuntimePrivilegeContractError, msg=expected_label):
                self._verify()
        finally:
            restore()
        self.assertEqual(self._verify()["status"], "verified")

    def test_real_pg17_migrations_capabilities_provenance_and_workspace_isolation(self):
        evidence = self._verify()
        self.assertEqual(evidence["postgres_major"], 17)
        self.assertEqual(evidence["search_path"], ["public", "pg_catalog"])
        self.assertEqual(self._inspect()["status"], "ready")

        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        storage_a = webapp.DatabaseSqagStorage(
            runtime_url, "workspace-alpha", role="admin", user_id="user-alpha"
        )
        storage_b = webapp.DatabaseSqagStorage(
            runtime_url, "workspace-beta", role="admin", user_id="user-beta"
        )
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            stored_a = storage_a.save_profile(
                {"id": "shared-profile", "label": "alpha-only", "notes": "synthetic"}
            )
            stored_b = storage_b.save_profile(
                {"id": "shared-profile", "label": "beta-only", "notes": "synthetic"}
            )
            storage_b.save_profile(
                {"id": "beta-only-profile", "label": "beta-only-record", "notes": "synthetic"}
            )
            storage_unknown = webapp.DatabaseSqagStorage(
                runtime_url, "workspace-missing", role="admin", user_id="user-missing"
            )
            self.assertIsNone(storage_unknown.profile_detail("shared-profile"))
            self.assertEqual(storage_unknown.list_company_profiles(), [])
            self.assertIsNone(storage_a.profile_detail("beta-only-profile"))
            self.assertEqual(stored_a["label"], "alpha-only")
            self.assertEqual(stored_b["label"], "beta-only")
            self.assertEqual(storage_a.profile_detail("shared-profile")["label"], "alpha-only")
            self.assertEqual(storage_b.profile_detail("shared-profile")["label"], "beta-only")
            self.assertEqual(storage_a.list_company_profiles()[0]["label"], "alpha-only")
            self.assertEqual(storage_b.list_company_profiles()[0]["label"], "beta-only")

            with mock.patch.dict(
                os.environ,
                {webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "object"},
                clear=False,
            ):
                object_session = storage_a.create_or_update_quote_session(
                    {"session_id": "object-mode-session", "client": {"name": "synthetic"}},
                    result=None,
                )
            self.assertEqual(object_session["session_id"], "object-mode-session")

            with webapp.postgres_storage_connection(runtime_url) as connection:
                forensic = ForensicStore(
                    connection,
                    "workspace-alpha",
                    "actor-alpha",
                    actor_key_version_value="test-v1",
                )
                run_id = forensic.record_run_started(
                    "generate",
                    {"image_count": 0, "synthetic": True},
                    run_id="run-publication-alpha",
                    job_id="job-publication-alpha",
                    now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                )
                forensic.finish_run(
                    run_id,
                    "completed",
                    result_summary={"status": "completed", "synthetic": True},
                    now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                )

            with tempfile.TemporaryDirectory() as output_root:
                output_dir = Path(output_root)
                (output_dir / "quotation.xlsx").write_bytes(b"synthetic-xlsx-proof")
                published = storage_a.create_or_update_quote_session(
                    {"session_id": "publication-session", "client": {"name": "synthetic"}},
                    result={"status": "completed"},
                    output_dir=output_dir,
                    session_id="publication-session",
                    generation_run_id="run-publication-alpha",
                    generation_job_id="job-publication-alpha",
                )
            self.assertEqual(published["session_id"], "publication-session")
            self.assertTrue(storage_a.quote_publication_version_is_current("run-publication-alpha"))

        with self._role_connection("sqag_runtime") as connection:
            with self.assertRaises(Exception):
                connection.execute("select 1 from public.sqag_retention_delete_authorizations").fetchone()
            connection.rollback()
            with self.assertRaises(Exception):
                connection.execute("select rolpassword from pg_catalog.pg_authid").fetchone()
            connection.rollback()

    def test_anti_false_matrix_is_executable_and_restored(self):
        self._red_then_restore(
            lambda: self._execute_admin("create table public.sqag_unexpected (id integer)"),
            lambda: self._execute_admin("drop table if exists public.sqag_unexpected"),
            "extra application object",
        )
        self._red_then_restore(
            lambda: self._execute_admin("revoke select on table public.sqag_profiles from sqag_runtime"),
            lambda: self._execute_admin("grant select on table public.sqag_profiles to sqag_runtime"),
            "missing capability",
        )
        self._red_then_restore(
            lambda: self._execute_admin("grant truncate on table public.sqag_profiles to sqag_runtime"),
            lambda: self._execute_admin("revoke truncate on table public.sqag_profiles from sqag_runtime"),
            "unexpected capability",
        )
        self._red_then_restore(
            lambda: self._execute_admin("grant select on table public.sqag_profiles to sqag_runtime with grant option"),
            lambda: (self._execute_admin("revoke all privileges on table public.sqag_profiles from sqag_runtime"),
                     self._execute_admin("grant select, insert, update, delete on table public.sqag_profiles to sqag_runtime")),
            "grant option",
        )
        self._red_then_restore(
            lambda: self._execute_admin("alter table public.sqag_profiles owner to sqag_runtime"),
            lambda: self._execute_admin("alter table public.sqag_profiles owner to sqag_migrator"),
            "unexpected ownership",
        )
        self._red_then_restore(
            lambda: self._execute_admin("alter schema public owner to sqag_runtime"),
            lambda: self._execute_admin("alter schema public owner to pg_database_owner"),
            "unexpected schema ownership",
        )

        self._red_then_restore(
            lambda: self._execute_admin("grant sqag_migrator to sqag_runtime"),
            lambda: self._execute_admin("revoke sqag_migrator from sqag_runtime"),
            "unexpected membership",
        )
        for grant_statement, label in (
            ("grant sqag_migrator to sqag_runtime with admin option", "unexpected ADMIN option"),
            ("grant sqag_migrator to sqag_runtime with inherit false", "unexpected INHERIT option"),
            ("grant sqag_migrator to sqag_runtime with set false", "unexpected SET option"),
        ):
            self._red_then_restore(
                lambda statement=grant_statement: self._execute_admin(statement),
                lambda: self._execute_admin("revoke sqag_migrator from sqag_runtime"),
                label,
            )
        self._red_then_restore(
            lambda: self._execute_admin("grant select on table public.sqag_profiles to PUBLIC"),
            lambda: self._execute_admin("revoke select on table public.sqag_profiles from PUBLIC"),
            "PUBLIC privilege drift",
        )
        self._red_then_restore(
            lambda: self._execute_admin(
                "alter default privileges for role sqag_migrator in schema public "
                "grant select on tables to sqag_runtime"
            ),
            lambda: self._execute_admin(
                "alter default privileges for role sqag_migrator in schema public "
                "revoke select on tables from sqag_runtime"
            ),
            "default ACL drift",
        )

        last = self.manifest[-1]
        saved_row = self._admin_row(
            "select sequence_no, migration_id, checksum_sha256, applied_at "
            "from public.sqag_schema_migrations where migration_id = %s",
            (last.migration_id,),
        )
        self._execute_admin(
            "delete from public.sqag_schema_migrations where migration_id = %s",
            (last.migration_id,),
        )
        try:
            report = self._inspect()
            self.assertFalse(report["safeToApply"])
            self.assertTrue(report["blockers"])
        finally:
            self._execute_admin(
                "insert into public.sqag_schema_migrations "
                "(sequence_no, migration_id, checksum_sha256, applied_at) values (%s, %s, %s, %s)",
                tuple(saved_row.values()),
            )
        self.assertEqual(self._inspect()["status"], "ready")

        self._execute_admin(
            "update public.sqag_schema_migrations set checksum_sha256 = %s where migration_id = %s",
            ("0" * 64, last.migration_id),
        )
        try:
            report = self._inspect()
            self.assertFalse(report["safeToApply"])
            self.assertTrue(any("checksum_drift" in item for item in report["blockers"]))
        finally:
            self._execute_admin(
                "update public.sqag_schema_migrations set checksum_sha256 = %s where migration_id = %s",
                (saved_row["checksum_sha256"], last.migration_id),
            )
        self.assertEqual(self._inspect()["status"], "ready")
        extra_migration_id = "999_unexpected_migration"
        self._execute_admin(
            "insert into public.sqag_schema_migrations "
            "(sequence_no, migration_id, checksum_sha256) values (%s, %s, %s)",
            (999, extra_migration_id, "0" * 64),
        )
        try:
            report = self._inspect()
            self.assertFalse(report["safeToApply"])
            self.assertTrue(any("unexpected_applied_migration" in item for item in report["blockers"]))
        finally:
            self._execute_admin(
                "delete from public.sqag_schema_migrations where migration_id = %s",
                (extra_migration_id,),
            )
        self.assertEqual(self._inspect()["status"], "ready")

        self._red_then_restore(
            lambda: self._execute_admin("drop index public.sqag_feedback_publication_idx"),
            lambda: self._execute_admin(
                "create index sqag_feedback_publication_idx on public.sqag_feedback "
                "(workspace_id, publication_version_id, run_id)"
            ),
            "missing application object",
        )

        with self.psycopg.connect(
            safe_postgres_url("postgres", self.database_name),
            options='-c search_path="$user",public',
            row_factory=self.dict_row,
        ) as raw:
            adapter = webapp.PostgresConnectionAdapter(raw)
            try:
                with self.assertRaises(contract.RuntimePrivilegeContractError):
                    contract.verify_postgres_privilege_contract(adapter, self.manifest)
            finally:
                adapter.close()

    def test_maintenance_projection_retention_and_cleanup_receipt(self):
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        maintenance_url = safe_postgres_url("sqag_maintenance", self.database_name)
        with webapp.postgres_storage_connection(runtime_url) as connection:
            forensic = ForensicStore(
                connection,
                "workspace-retention",
                "actor-retention",
                actor_key_version_value="test-v1",
            )
            run_id = forensic.record_run_started(
                "generate",
                {"image_count": 0, "synthetic": True},
                run_id="run-retention-alpha",
                job_id="job-retention-alpha",
                now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
            )
            forensic.finish_run(
                run_id,
                "completed",
                now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
            )

        environment = dict(os.environ)
        environment[webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME] = maintenance_url
        environment.pop(webapp.SQAG_DATABASE_URL_ENV_NAME, None)
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "enforce_forensic_retention.py"),
                "--workspace-id",
                "workspace-retention",
                "--use-configured-database",
                "--apply",
                "--now",
                "2026-01-01T00:00:00+00:00",
            ],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout[-500:])
        report = json.loads(completed.stdout)
        self.assertEqual(report["status"], "completed")
        self.assertGreaterEqual(report["deleted"], 1)
        self.assertNotIn(maintenance_url, completed.stdout)
        self.assertNotIn(maintenance_url, completed.stderr)
        with webapp.postgres_storage_connection(maintenance_url) as connection:
            remaining = connection.execute(
                "select 1 from sqag_generation_runs where workspace_id = ? and run_id = ?",
                ("workspace-retention", "run-retention-alpha"),
            ).fetchone()
        self.assertIsNone(remaining)

    def _execute_admin(self, statement: str, params=()):
        with self._admin_connection() as connection:
            return connection.execute(statement, params)

    def _admin_row(self, statement: str, params=()):
        with self._admin_connection() as connection:
            row = connection.execute(statement, params).fetchone()
            if row is None:
                raise AssertionError("fixture row missing")
            return row


if __name__ == "__main__":
    unittest.main()
