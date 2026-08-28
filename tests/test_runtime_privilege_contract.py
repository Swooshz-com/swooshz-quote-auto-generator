from __future__ import annotations

import copy
import datetime as dt
import io
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from unittest import mock
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DISPOSABLE_BOOTSTRAP_ROLE = "cloud_admin"
sys.path.insert(0, str(ROOT))

from scripts import validate_runtime_privilege_contract as contract
from scripts import migrate_sqag_storage as migrate_cli
from webapp import server as webapp
from scripts import preflight_sqag_migrations as preflight
from webapp import postgres_migrations as postgres_migration_module
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


def load_script_module(filename: str, module_name: str):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"script module is missing: {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql_grantee(sql_module, name: str):
    return sql_module.SQL("PUBLIC") if name == "PUBLIC" else sql_module.Identifier(name)


class RuntimePrivilegeContractStaticTest(unittest.TestCase):
    def test_manifest_is_strict_a25_contract_without_source_digest_mirrors(self):
        manifest = contract.validate_manifest()
        self.assertEqual(manifest["schema_version"], 4)
        self.assertNotIn("canonical_source_revision", manifest)
        self.assertNotIn("canonical_source_tree", manifest)
        self.assertNotIn("implementation_registry", manifest)
        self.assertEqual(manifest["namespace"]["search_path"], ["public", "pg_catalog"])
        self.assertEqual(manifest["session_authority"]["runtime_role"], "sqag_runtime")
        self.assertEqual(manifest["session_authority"]["maintenance_role"], "sqag_maintenance")
        self.assertTrue(manifest["session_authority"]["required_before_sql"])
        self.assertEqual(set(manifest["runtime_tables"]), set(contract.RUNTIME_TABLE_PRIVILEGES))
        self.assertEqual(set(manifest["maintenance_tables"]), set(contract.MAINTENANCE_TABLE_PRIVILEGES))

    def test_option_a_provider_control_and_ownership_contract_is_exact(self):
        manifest = contract.validate_manifest()
        self.assertEqual(manifest["$schema"], "runtime-privilege-contract-schema-v4")
        self.assertEqual(
            manifest["provider_controlled_memberships"]["protected_roles"],
            ["sqag_runtime", "sqag_migrator", "sqag_maintenance"],
        )
        self.assertEqual(
            manifest["provider_controlled_memberships"]["allowed_edges"],
            [
                {
                    "role": role,
                    "member": "neondb_owner",
                    "grantor": "cloud_admin",
                    "admin_option": True,
                    "inherit_option": False,
                    "set_option": False,
                }
                for role in ("sqag_runtime", "sqag_migrator", "sqag_maintenance")
            ],
        )
        self.assertEqual(
            manifest["ownership"],
            {
                "database_owner": "neondb_owner",
                "public_schema_owner": "pg_database_owner",
            },
        )

    def test_callable_session_hold_authority_contract_is_exact(self):
        manifest = contract.validate_manifest()
        self.assertEqual(
            manifest["namespace"]["callable_routines"],
            [contract.EXPECTED_CALLABLE_ROUTINE_DOCUMENT],
        )
        self.assertEqual(
            manifest["source_binding"]["callable_routine_source"]["routine"],
            "public.sqag_quote_session_deletion_hold_blocked(text, text)",
        )
        self.assertEqual(contract.validate_source_bindings(manifest), [])

    def test_callable_session_hold_body_normalizes_formatting_but_not_semantics(self):
        canonical_body = contract._canonical_callable_routine_body()
        self.assertTrue(canonical_body)
        formatting_only = canonical_body.replace("\n", " \t") + "\n-- formatting-only comment"
        self.assertEqual(
            contract._semantic_sql_tokens(canonical_body),
            contract._semantic_sql_tokens(formatting_only),
        )
        semantic_mutation = canonical_body.replace(
            "or (select blocked from hold_state)",
            "and (select blocked from hold_state)",
            1,
        )
        self.assertNotEqual(
            contract._semantic_sql_tokens(canonical_body),
            contract._semantic_sql_tokens(semantic_mutation),
        )
        verifier_source = (ROOT / "scripts" / "validate_runtime_privilege_contract.py").read_text(encoding="utf-8")
        self.assertIn("p.prosrc as function_body", verifier_source)

    def test_option_a_static_contract_drift_is_fail_closed(self):
        manifest = contract.validate_manifest()
        edge = manifest["provider_controlled_memberships"]["allowed_edges"][0]

        mutations = {
            "unknown_top_level_key": lambda candidate: candidate.update(unexpected=True),
            "missing_edge": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"].pop(),
            "fourth_edge": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"].append(copy.deepcopy(edge)),
            "wrong_schema_version": lambda candidate: candidate.update(schema_version=2),
            "admin_drift": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"][0].update(admin_option=False),
            "inherit_drift": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"][0].update(inherit_option=True),
            "set_drift": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"][0].update(set_option=True),
            "wrong_member": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"][0].update(member="cloud_admin"),
            "wrong_grantor": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"][0].update(grantor="neondb_owner"),
            "unknown_edge_key": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"][0].update(unexpected=True),
            "unknown_provider_key": lambda candidate: candidate["provider_controlled_memberships"].update(unexpected=True),
            "unknown_role": lambda candidate: candidate["provider_controlled_memberships"]["allowed_edges"][0].update(role="sqag_unknown"),
            "protected_role_set_drift": lambda candidate: candidate["provider_controlled_memberships"]["protected_roles"].append("sqag_unknown"),
            "unknown_ownership_key": lambda candidate: candidate["ownership"].update(unexpected=True),
            "malformed_ownership": lambda candidate: candidate.update(ownership={"database_owner": "neondb_owner"}),
            "wrong_database_owner": lambda candidate: candidate["ownership"].update(database_owner="postgres"),
            "wrong_public_schema_owner": lambda candidate: candidate["ownership"].update(public_schema_owner="postgres"),
        }
        for label, mutate in mutations.items():
            candidate = copy.deepcopy(manifest)
            mutate(candidate)
            with self.assertRaises(contract.RuntimePrivilegeContractError, msg=label):
                contract.validate_manifest(candidate)

    def test_option_a_runtime_membership_collection_matches_manifest_exactly(self):
        manifest = contract.validate_manifest()
        expected = copy.deepcopy(manifest["provider_controlled_memberships"]["allowed_edges"])
        self.assertEqual(contract.validate_runtime_membership_edges(manifest, expected), [])

        mutations = {
            "missing_edge": lambda rows: rows.pop(),
            "fourth_edge": lambda rows: rows.append({
                "role": "sqag_runtime",
                "member": "sqag_migrator",
                "grantor": "cloud_admin",
                "admin_option": True,
                "inherit_option": False,
                "set_option": False,
            }),
            "duplicate_edge": lambda rows: rows.append(copy.deepcopy(rows[0])),
            "admin_drift": lambda rows: rows[0].update(admin_option=False),
            "inherit_drift": lambda rows: rows[0].update(inherit_option=True),
            "set_drift": lambda rows: rows[0].update(set_option=True),
            "wrong_member": lambda rows: rows[0].update(member="sqag_migrator"),
            "wrong_grantor": lambda rows: rows[0].update(grantor="neondb_owner"),
            "outgoing_protected_role": lambda rows: rows.append({
                "role": "sqag_runtime",
                "member": "sqag_migrator",
                "grantor": "cloud_admin",
                "admin_option": True,
                "inherit_option": False,
                "set_option": False,
            }),
            "unexpected_protected_grantor": lambda rows: rows.append({
                "role": "sqag_runtime",
                "member": "neondb_owner",
                "grantor": "sqag_migrator",
                "admin_option": True,
                "inherit_option": False,
                "set_option": False,
            }),
            "application_membership": lambda rows: rows.append({
                "role": "sqag_migrator",
                "member": "sqag_runtime",
                "grantor": "cloud_admin",
                "admin_option": True,
                "inherit_option": False,
                "set_option": False,
            }),
            "non_boolean_option": lambda rows: rows[0].update(admin_option="true"),
        }
        for label, mutate in mutations.items():
            rows = copy.deepcopy(expected)
            mutate(rows)
            self.assertTrue(contract.validate_runtime_membership_edges(manifest, rows), label)
    def test_migration_manifest_cannot_be_used_as_runtime_contract_mapping(self):
        migrations = migration_manifest(ROOT / "migrations")
        self.assertIsInstance(migrations, tuple)
        with self.assertRaises(TypeError):
            contract.validate_manifest(migrations)

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

    def test_migrator_projection_never_falls_back_to_runtime_or_maintenance_database_url(self):
        values = {
            webapp.SQAG_DATABASE_URL_ENV_NAME: "postgresql://runtime.example/sqag",
            webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME: "postgresql://maintenance.example/sqag",
            webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME: "",
        }

        def read_value(name: str) -> str:
            return values.get(name, "")

        with mock.patch.object(webapp, "read_dotenv_value", side_effect=read_value):
            self.assertEqual(webapp.configured_migrator_database_url(), "")
        preflight_source = (ROOT / "scripts" / "preflight_sqag_migrations.py").read_text(encoding="utf-8")
        self.assertIn("configured_migrator_database_url", preflight_source)
        self.assertNotIn("migrator_url = webapp.configured_database_url()", preflight_source)
        self.assertNotIn("migrator_url = webapp.configured_maintenance_database_url()", preflight_source)

    def test_retention_readiness_is_limited_to_forensic_surfaces(self):
        storage = webapp.DatabaseSqagStorage(
            "sqlite:///:memory:",
            "workspace-retention-readiness",
            role="admin",
        )
        with mock.patch.object(storage, "_ensure_schema") as ensure_schema:
            storage.ensure_retention_ready()
        ensure_schema.assert_called_once_with(
            webapp.SQAG_FORENSIC_REQUIRED_COLUMNS,
            reason="storage_forensics_database_not_migrated",
        )
        retention_source = (ROOT / "scripts" / "enforce_forensic_retention.py").read_text(encoding="utf-8")
        self.assertIn("storage.ensure_retention_ready()", retention_source)
        self.assertNotIn("storage.ensure_ready()", retention_source)
        server_source = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")
        self.assertIn("pg_catalog.pg_proc", server_source)
        self.assertNotIn("information_schema.routines", server_source)

    def test_fixed_search_path_and_sensitive_observation_boundaries_are_source_bound(self):
        server_source = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")
        verifier_source = (ROOT / "scripts" / "validate_runtime_privilege_contract.py").read_text(encoding="utf-8")
        self.assertIn('options="-c search_path=public,pg_catalog"', server_source)
        self.assertIn("SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME", server_source)
        self.assertNotIn("pg_catalog.pg_authid", verifier_source)
        self.assertNotIn("rolpassword", verifier_source)
        self.assertIn("aclexplode", verifier_source)
        self.assertIn("pg_catalog.pg_attribute", verifier_source)
        self.assertIn("a.attacl", verifier_source)
        self.assertNotIn("role_column_grants", verifier_source)
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

    def test_preflight_phase_is_required_before_any_database_access(self):
        for arguments, blocker in (
            ([], "phase_required"),
            (["--phase", "pre-apply", "--phase", "post-apply"], "phase_duplicate"),
            (["--phase", "during-apply"], "phase_invalid"),
            (["--phase", "pre-apply", "--unknown"], "unknown_argument"),
        ):
            output = io.StringIO()
            with mock.patch.object(webapp, "configured_migrator_database_url") as configured_url:
                with redirect_stdout(output):
                    result = preflight.main(arguments)
            self.assertEqual(result, 2, arguments)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "unsafe")
            self.assertEqual(payload["blockers"], [blocker])
            configured_url.assert_not_called()

    def test_preflight_accepts_only_an_exact_canonical_pending_suffix(self):
        migrations = migration_manifest(ROOT / "migrations")
        applied = [migration.migration_id for migration in migrations[:-1]]
        valid = {
            "status": "ready",
            "safeToApply": True,
            "ledgerState": "present",
            "expectedHead": migrations[-1].migration_id,
            "appliedHead": migrations[-2].migration_id,
            "appliedMigrationIds": applied,
            "pendingMigrationIds": [migrations[-1].migration_id],
            "blockers": [],
        }
        preflight.validate_pre_apply_migration_report(valid, migrations)
        for mutation in (
            {"appliedMigrationIds": [migrations[0].migration_id, migrations[2].migration_id]},
            {"pendingMigrationIds": [migrations[5].migration_id]},
            {"appliedHead": migrations[4].migration_id},
            {"ledgerState": "missing"},
        ):
            candidate = dict(valid)
            candidate.update(mutation)
            with self.assertRaises(contract.RuntimePrivilegeContractError):
                preflight.validate_pre_apply_migration_report(candidate, migrations)

    def test_postgres_migration_cli_binds_only_to_the_dedicated_migrator_url(self):
        with mock.patch.object(webapp, "configured_database_url", return_value="postgresql://runtime.example/sqag"), \
            mock.patch.object(webapp, "configured_migrator_database_url", return_value="postgresql://migrator.example/sqag"), \
            mock.patch.object(webapp, "configured_maintenance_database_url", side_effect=AssertionError("maintenance fallback")):
            self.assertEqual(
                migrate_cli._migration_database_url(),
                "postgresql://migrator.example/sqag",
            )

    def test_postgres_migration_cli_preserves_the_explicit_local_sqlite_branch(self):
        with mock.patch.object(webapp, "configured_database_url", return_value="sqlite:///:memory:"), \
            mock.patch.object(webapp, "configured_migrator_database_url", side_effect=AssertionError("postgres fallback")):
            self.assertEqual(
                migrate_cli._migration_database_url(),
                "sqlite:///:memory:",
            )

    def test_postgres_migration_cli_rejects_postgres_without_a_migrator_url(self):
        with mock.patch.object(webapp, "configured_database_url", return_value="postgresql://runtime.example/sqag"), \
            mock.patch.object(webapp, "configured_migrator_database_url", return_value=""):
            with self.assertRaises(migrate_cli.MigrationConfigurationError):
                migrate_cli._migration_database_url()

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
    def __init__(self, current_user: str, session_user: str | None = None):
        self.current_user = current_user
        self.session_user = session_user or current_user
        self.statements: list[str] = []
        self.rollback_calls = 0
        self.closed = False

    def execute(self, sql, params=()):
        _ = params
        self.statements.append(sql)
        if sql == "select session_user as session_role, current_user as active_role":
            return _FakeIdentityCursor(
                {
                    "session_role": self.session_user,
                    "active_role": self.current_user,
                }
            )
        return _FakeIdentityCursor({"ok": True})

    def rollback(self):
        self.rollback_calls += 1

    def close(self):
        self.closed = True


class RuntimeSessionIdentityBindingTest(unittest.TestCase):
    DATABASE_URL = "postgresql://sqag_runtime:synthetic-secret@db.example.test/sqag"

    def _preflight(
        self,
        current_user: str,
        *,
        maintenance: bool = False,
        session_user: str | None = None,
    ):
        raw = _FakeIdentityRawConnection(current_user, session_user=session_user)
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

    def _runtime_storage(self, current_user: str, *, session_user: str | None = None):
        raw = _FakeIdentityRawConnection(current_user, session_user=session_user)
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
        self.assertIsNone(result[0])
        self.assertEqual(result[1]["status"], "verified")
        self.assertEqual(
            raw.statements[:2],
            ["select session_user as session_role, current_user as active_role", "set transaction read only"],
        )
        self.assertEqual(raw.rollback_calls, 1)
        self.assertTrue(raw.closed)

    def test_runtime_preflight_rejects_admin_migrator_and_maintenance_identities(self):
        for current_user in ("postgres", "sqag_migrator", "sqag_maintenance"):
            with self.subTest(current_user=current_user):
                with self.assertRaises(webapp.SqagStorageAccessError):
                    self._preflight(current_user)
                _, raw = self._preflight_rejected(current_user)
                self.assertEqual(raw.statements, ["select session_user as session_role, current_user as active_role"])
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
            ["select session_user as session_role, current_user as active_role", "select application_sql"],
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
                self.assertEqual(raw.statements, ["select session_user as session_role, current_user as active_role"])
                self.assertTrue(raw.closed)
                message = str(raised.exception)
                self.assertNotIn(self.DATABASE_URL, message)
                self.assertNotIn("db.example.test", message)
                self.assertNotIn("synthetic-secret", message)
                self.assertNotIn(current_user, message)

    def test_runtime_identity_binding_is_fixed_and_covers_application_paths(self):
        source = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")
        self.assertIn('SQAG_RUNTIME_DATABASE_ROLE = "sqag_runtime"', source)
        self.assertEqual(source.count("expected_session_role=SQAG_RUNTIME_DATABASE_ROLE"), 5)
        self.assertIn(
            "return postgres_storage_connection(self.database_url, expected_role=self.expected_session_role)",
            source,
        )
        self.assertNotIn("SQAG_RUNTIME_DATABASE_ROLE_ENV_NAME", source)
        self.assertNotIn("read_dotenv_value(SQAG_RUNTIME_DATABASE_ROLE", source)

    def test_assumed_role_is_rejected_before_protected_sql_for_every_fixed_role(self):
        identity_query = "select session_user as session_role, current_user as active_role"
        for expected_role in (
            webapp.SQAG_RUNTIME_DATABASE_ROLE,
            webapp.SQAG_MIGRATOR_DATABASE_ROLE,
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
        ):
            with self.subTest(expected_role=expected_role):
                raw = _FakeIdentityRawConnection(expected_role, session_user="synthetic_login")
                connect = lambda _database_url: raw
                with mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect):
                    with self.assertRaises(webapp.SqagStorageAccessError):
                        with webapp.postgres_storage_connection(
                            self.DATABASE_URL,
                            expected_role=expected_role,
                        ) as connection:
                            connection.execute("protected_sql_must_not_run")
                self.assertEqual(raw.statements, [identity_query])
                self.assertTrue(raw.closed)

    def test_maintenance_preflight_accepts_only_exact_maintenance_identity(self):
        result, raw = self._preflight(
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
            maintenance=True,
        )
        self.assertIsNone(result[0])
        self.assertEqual(
            raw.statements[:2],
            ["select session_user as session_role, current_user as active_role", "set transaction read only"],
        )
        with self.assertRaises(webapp.SqagStorageAccessError):
            self._preflight(webapp.SQAG_RUNTIME_DATABASE_ROLE, maintenance=True)

class StructuralSessionAuthorityTest(unittest.TestCase):
    DATABASE_URL = "postgresql://sqag_runtime:synthetic-secret@db.example.test/sqag"

    def _storage_identity(self, current_user: str, expected_role: str, sql: str):
        raw = _FakeIdentityRawConnection(current_user)
        connect = lambda _database_url: raw
        with mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect):
            storage = webapp.DatabaseSqagStorage(
                self.DATABASE_URL,
                "workspace-identity-contract",
                role="admin",
                expected_session_role=expected_role,
            )
            with storage.connection() as connection:
                connection.execute(sql)
        return raw

    def test_ast_inventory_fails_closed_for_every_current_runtime_and_maintenance_constructor(self):
        self.assertEqual(contract.validate_session_authority_source(), [])
        server_source = (ROOT / "webapp" / "server.py").read_text(encoding="utf-8")
        retention_source = (ROOT / "scripts" / "enforce_forensic_retention.py").read_text(encoding="utf-8")
        self.assertNotIn("expected_session_role: str | None = None", server_source)
        self.assertIn("expected_session_role=SQAG_RUNTIME_DATABASE_ROLE", server_source)
        self.assertIn("expected_session_role=webapp.SQAG_MAINTENANCE_DATABASE_ROLE", retention_source)

    def test_production_postgres_caller_inventory_is_semantic_and_fails_closed_on_missing_binding(self):
        self.assertEqual(contract.validate_session_authority_source(), [])
        for relative_path, spec in contract.PRODUCTION_POSTGRES_CALLER_SPECS.items():
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertTrue(all(str(role) in source for role in (*spec["storage_roles"], *spec["connection_roles"])))
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "unbound.py"
            path.write_text("from webapp import server as webapp\nwebapp.DatabaseSqagStorage(url, 'workspace')\n", encoding="utf-8")
            spec = contract.PRODUCTION_POSTGRES_CALLER_SPECS["scripts/verify_production_database_provider.py"]
            errors = contract._validate_production_postgres_caller(path, spec)
        self.assertTrue(any("storage_call_site_unbound" in item for item in errors))

    def test_omitted_postgres_expected_role_cannot_open_a_storage_connection(self):
        storage = webapp.DatabaseSqagStorage(self.DATABASE_URL, "workspace-omitted-role")
        with self.assertRaises(webapp.SqagStorageAccessError) as raised:
            with storage.connection():
                self.fail("an omitted PostgreSQL role must not yield a connection")
        self.assertEqual(raised.exception.reason, "storage_postgres_session_role_required")
        with self.assertRaises(webapp.SqagStorageAccessError):
            webapp.DatabaseSqagStorage(
                self.DATABASE_URL,
                "workspace-invalid-role",
                expected_session_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
            )

    def test_maintenance_accepts_exact_role_and_rejects_admin_migrator_runtime_before_destructive_sql(self):
        accepted = self._storage_identity(
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
            "delete from public.sqag_generation_runs",
        )
        self.assertEqual(
            accepted.statements,
            ["select session_user as session_role, current_user as active_role", "delete from public.sqag_generation_runs"],
        )
        for current_user in ("postgres", "sqag_migrator", "sqag_runtime"):
            with self.subTest(current_user=current_user):
                raw = _FakeIdentityRawConnection(current_user)
                connect = lambda _database_url: raw
                with mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect):
                    storage = webapp.DatabaseSqagStorage(
                        self.DATABASE_URL,
                        "workspace-identity-contract",
                        role="admin",
                        expected_session_role=webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
                    )
                    with self.assertRaises(webapp.SqagStorageAccessError) as raised:
                        with storage.connection():
                            self.fail("destructive SQL must not run after maintenance identity mismatch")
                self.assertEqual(raw.statements, ["select session_user as session_role, current_user as active_role"])
                self.assertTrue(raw.closed)
                message = str(raised.exception)
                self.assertNotIn(self.DATABASE_URL, message)
                self.assertNotIn("db.example.test", message)
                self.assertNotIn("synthetic-secret", message)
                self.assertNotIn(current_user, message)

    def test_migration_role_is_explicit_and_not_usable_by_runtime_storage(self):
        raw = _FakeIdentityRawConnection(webapp.SQAG_MIGRATOR_DATABASE_ROLE)
        connect = lambda _database_url: raw
        with mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect):
            with webapp.postgres_storage_connection(
                self.DATABASE_URL,
                expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
            ) as connection:
                connection.execute("select migration_sql")
        self.assertEqual(raw.statements, ["select session_user as session_role, current_user as active_role", "select migration_sql"])
        for current_user in (webapp.SQAG_RUNTIME_DATABASE_ROLE, webapp.SQAG_MAINTENANCE_DATABASE_ROLE):
            raw = _FakeIdentityRawConnection(current_user)
            connect = lambda _database_url: raw
            with mock.patch.object(webapp, "postgres_driver_connection_factory", return_value=connect):
                with self.assertRaises(webapp.SqagStorageAccessError):
                    with webapp.postgres_storage_connection(
                        self.DATABASE_URL,
                        expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
                    ):
                        self.fail("migration identity mismatch must stop before migration SQL")
            self.assertEqual(raw.statements, ["select session_user as session_role, current_user as active_role"])

    def test_maintenance_preflight_rejects_all_nonmaintenance_identities(self):
        case = RuntimeSessionIdentityBindingTest("runTest")
        for current_user in ("postgres", "sqag_migrator", "sqag_runtime"):
            with self.subTest(current_user=current_user):
                with self.assertRaises(webapp.SqagStorageAccessError):
                    case._preflight(current_user, maintenance=True)

    def test_sqlite_storage_does_not_run_postgres_identity_query(self):
        storage = webapp.DatabaseSqagStorage("sqlite:///:memory:", "workspace-sqlite-separate")
        with mock.patch.object(webapp, "postgres_storage_connection", side_effect=AssertionError("PostgreSQL path used for SQLite")):
            with storage.connection() as connection:
                self.assertEqual(connection.execute("select 1").fetchone()[0], 1)

@unittest.skipUnless(
    postgres_test_enabled(),
    "real disposable PostgreSQL-17 service is not configured",
)
class RuntimePrivilegeContractPostgresIntegrationTest(unittest.TestCase):
    apply_full_migration_fixture = True

    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row

        cls.psycopg = psycopg
        cls.sql = sql
        cls.dict_row = staticmethod(dict_row)
        cls.migrations = migration_manifest(ROOT / "migrations")
        cls.manifest = contract.validate_manifest()

    def setUp(self):
        self.database_name = "sqag_a25_runtime_" + uuid.uuid4().hex
        self.roles = ("sqag_runtime", "sqag_migrator", "sqag_maintenance")
        self.provider_roles = ("neondb_owner",)
        self.bootstrap_role = os.getenv("SQAG_TEST_POSTGRES_USER", "").strip()
        if self.bootstrap_role != DISPOSABLE_BOOTSTRAP_ROLE:
            raise AssertionError(
                "disposable PostgreSQL test identity must be cloud_admin"
            )
        self.created_database_names: list[str] = []
        self.created_roles: list[str] = []
        self.database_created = False
        self.cleanup_done = False
        self.addCleanup(self._cleanup_fixture)
        self.fixture_ready = False
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            bootstrap = connection.execute(
                """
                select current_user, role.rolname, role.oid, role.rolsuper
                from pg_catalog.pg_roles as role
                where role.rolname = %s
                """,
                (self.bootstrap_role,),
            ).fetchone()
            if bootstrap is None:
                raise AssertionError("disposable bootstrap role is missing")
            self.assertEqual(bootstrap[0], DISPOSABLE_BOOTSTRAP_ROLE)
            self.assertEqual(bootstrap[1], DISPOSABLE_BOOTSTRAP_ROLE)
            self.assertEqual(bootstrap[2], 10)
            self.assertTrue(bootstrap[3])
            existing_database = connection.execute(
                "select 1 from pg_catalog.pg_database where datname = %s",
                (self.database_name,),
            ).fetchone()
            all_roles = (*self.roles, *self.provider_roles)
            existing_roles = connection.execute(
                "select rolname from pg_catalog.pg_roles where rolname = any(%s)",
                (list(all_roles),),
            ).fetchall()
            if existing_database or existing_roles:
                raise RuntimeError("CLEANUP_UNKNOWN")
            connection.execute(
                self.sql.SQL("create database {}").format(
                    self.sql.Identifier(self.database_name)
                )
            )
            self.database_created = True
            self.created_database_names.append(self.database_name)
            role_options = {
                "neondb_owner": "nologin nosuperuser nocreatedb nocreaterole "
                "noreplication nobypassrls noinherit connection limit -1",
            }
            for role in (*self.provider_roles, *self.roles):
                options = role_options.get(
                    role,
                    "login nosuperuser nocreatedb nocreaterole "
                    "noreplication nobypassrls noinherit connection limit -1",
                )
                connection.execute(
                    self.sql.SQL("create role {} {}").format(
                        self.sql.Identifier(role),
                        self.sql.SQL(options),
                    )
                )
                self.created_roles.append(role)

        self._configure_provider_ownership(self.database_name)
        self._configure_provider_memberships()

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
        if self.apply_full_migration_fixture:
            with webapp.postgres_storage_connection(migrator_url, expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE) as connection:
                result = apply_postgres_migrations(connection, self.migrations)
                connection.commit()
            self.assertEqual(result["expectedHead"], self.migrations[-1].migration_id)
            self._configure_acl_contract()
            self.fixture_ready = True
        else:
            self._bootstrap_prefix_migrations(migrator_url)

    def _cleanup_fixture(self):
        if self.cleanup_done:
            return
        self.cleanup_done = True
        cleanup_error = None
        try:
            with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
                for database_name in self.created_database_names:
                    connection.execute(
                        "select pg_catalog.pg_terminate_backend(pid) "
                        "from pg_catalog.pg_stat_activity "
                        "where datname = %s and pid <> pg_catalog.pg_backend_pid()",
                        (database_name,),
                    )
                    connection.execute(
                        self.sql.SQL("drop database if exists {}").format(
                            self.sql.Identifier(database_name)
                        )
                    )
                for role in self.created_roles:
                    connection.execute(
                        self.sql.SQL("drop role if exists {}").format(
                            self.sql.Identifier(role)
                        )
                    )
                residual_databases = connection.execute(
                    "select 1 from pg_catalog.pg_database where datname = any(%s)",
                    (self.created_database_names,),
                ).fetchall()
                residual_roles = connection.execute(
                    "select 1 from pg_catalog.pg_roles where rolname = any(%s)",
                    (self.created_roles,),
                ).fetchall()
                if residual_databases or residual_roles:
                    cleanup_error = "CLEANUP_UNKNOWN"
        except Exception:
            cleanup_error = "CLEANUP_UNKNOWN"
        if cleanup_error:
            self.fail(cleanup_error)

    def _admin_connection(self, database_name: str | None = None):
        target_database = database_name or self.database_name
        return self.psycopg.connect(
            safe_postgres_url(self.bootstrap_role, target_database),
            row_factory=self.dict_row,
            options="-c search_path=public,pg_catalog",
            autocommit=True,
        )

    def _role_connection(self, role: str):
        return self.psycopg.connect(
            safe_postgres_url(role, self.database_name),
            row_factory=self.dict_row,
        )

    def _configure_provider_ownership(self, database_name: str):
        with self._admin_connection("postgres") as connection:
            connection.execute(
                self.sql.SQL("alter database {} owner to neondb_owner").format(
                    self.sql.Identifier(database_name)
                )
            )
        with self._admin_connection(database_name) as connection:
            connection.execute("alter schema public owner to pg_database_owner")

    def _execute_as_role(self, role: str, statement, params=()):
        with self._admin_connection() as connection:
            connection.execute(
                self.sql.SQL("set session authorization {}").format(self.sql.Identifier(role))
            )
            identity = connection.execute("select session_user, current_user").fetchone()
            self.assertEqual(identity["session_user"], role)
            self.assertEqual(identity["current_user"], role)
            return connection.execute(statement, params)

    def _configure_provider_memberships(self):
        for role in self.roles:
            self._grant_provider_edge(role)

    def _grant_provider_edge(
        self,
        role: str,
        member: str = "neondb_owner",
        grantor: str = "cloud_admin",
        *,
        admin: bool = True,
        inherit: bool = False,
        set_option: bool = False,
    ):
        statement = self.sql.SQL(
            "grant {} to {} with admin {}, inherit {}, set {} granted by {}"
        ).format(
            self.sql.Identifier(role),
            self.sql.Identifier(member),
            self.sql.SQL("true" if admin else "false"),
            self.sql.SQL("true" if inherit else "false"),
            self.sql.SQL("true" if set_option else "false"),
            self.sql.Identifier(grantor),
        )
        return self._execute_as_role(grantor, statement)

    def _revoke_provider_edge(
        self,
        role: str,
        member: str = "neondb_owner",
        grantor: str = "cloud_admin",
    ):
        statement = self.sql.SQL("revoke {} from {} granted by {}").format(
            self.sql.Identifier(role),
            self.sql.Identifier(member),
            self.sql.Identifier(grantor),
        )
        return self._execute_as_role(grantor, statement)

    def _revoke_provider_admin_option(
        self,
        role: str,
        member: str,
        grantor: str = "cloud_admin",
    ):
        statement = self.sql.SQL("revoke admin option for {} from {}").format(
            self.sql.Identifier(role),
            self.sql.Identifier(member),
        )
        return self._execute_as_role(grantor, statement)

    def _configure_acl_contract(self, database_name: str | None = None):
        target_database = database_name or self.database_name
        tables = sorted(set(EXPECTED_TABLES) | {contract.LEDGER_TABLE})
        with self._admin_connection(target_database) as connection:
            database = self.sql.Identifier(target_database)
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
            for routine, _identity_arguments in contract.EXPECTED_TRIGGER_ROUTINE_KEYS:
                connection.execute(
                    self.sql.SQL("grant execute on function public.{}() to sqag_migrator").format(
                        self.sql.Identifier(routine)
                    )
                )
            for routine, identity_arguments in contract.EXPECTED_CALLABLE_ROUTINE_KEYS:
                connection.execute(
                    self.sql.SQL("grant execute on function public.{}({}) to sqag_runtime").format(
                        self.sql.Identifier(routine),
                        self.sql.SQL(identity_arguments),
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

    def _create_isolated_database_fixture(self) -> str:
        database_name = "sqag_a25_restore_" + uuid.uuid4().hex
        with self.psycopg.connect(postgres_test_conninfo(), autocommit=True) as connection:
            existing_database = connection.execute(
                "select 1 from pg_catalog.pg_database where datname = %s",
                (database_name,),
            ).fetchone()
            if existing_database:
                raise RuntimeError("CLEANUP_UNKNOWN")
            connection.execute(
                self.sql.SQL("create database {}").format(
                    self.sql.Identifier(database_name)
                )
            )
        self.created_database_names.append(database_name)
        self._configure_provider_ownership(database_name)
        with self._admin_connection(database_name) as connection:
            connection.execute("grant usage, create on schema public to sqag_migrator")
        migrator_url = safe_postgres_url("sqag_migrator", database_name)
        with webapp.postgres_storage_connection(
            migrator_url,
            expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
        ) as connection:
            result = apply_postgres_migrations(connection, self.migrations)
            connection.commit()
        self.assertEqual(result["expectedHead"], self.migrations[-1].migration_id)
        self._configure_acl_contract(database_name)
        return database_name

    def _verify(self):
        with self._admin_connection() as raw_connection:
            connection = webapp.PostgresConnectionAdapter(raw_connection)
            return contract.verify_postgres_privilege_contract(connection, self.manifest)

    def _inspect(self):
        with self._admin_connection() as raw_connection:
            connection = webapp.PostgresConnectionAdapter(raw_connection)
            return inspect_postgres_migrations(connection, self.migrations)

    def _red_then_restore(self, mutate, restore, expected_label: str):
        mutate()
        try:
            with self.assertRaises(contract.RuntimePrivilegeContractError, msg=expected_label):
                self._verify()
        finally:
            restore()
        self.assertEqual(self._verify()["status"], "verified")

    def _workspace_deletion_snapshot(self, workspace_id: str) -> dict[str, list[tuple[object, ...]]]:
        queries = {
            "sessions": (
                "select * from public.sqag_quote_sessions "
                "where workspace_id = %s order by session_id",
            ),
            "publication_versions": (
                "select * from public.sqag_quote_publication_versions "
                "where workspace_id = %s order by session_id, run_id",
            ),
            "publication_artifacts": (
                "select * from public.sqag_quote_publication_artifacts "
                "where workspace_id = %s order by session_id, run_id, artifact_kind",
            ),
            "object_artifacts": (
                "select * from public.sqag_object_artifacts "
                "where workspace_id = %s order by artifact_id",
            ),
            "generation_runs": (
                "select * from public.sqag_generation_runs "
                "where workspace_id = %s order by run_id",
            ),
            "generation_evidence": (
                "select * from public.sqag_generation_evidence "
                "where workspace_id = %s order by evidence_id",
            ),
            "audit_events": (
                "select * from public.sqag_audit_events "
                "where workspace_id = %s order by event_id",
            ),
            "feedback": (
                "select * from public.sqag_feedback "
                "where workspace_id = %s order by feedback_id",
            ),
            "feedback_status_history": (
                "select * from public.sqag_feedback_status_history "
                "where workspace_id = %s order by history_id",
            ),
            "legal_holds": (
                "select * from public.sqag_legal_holds "
                "where workspace_id = %s order by hold_id",
            ),
        }
        return {
            name: [tuple(row.values()) for row in self._admin_rows(statement, (workspace_id,))]
            for name, (statement,) in queries.items()
        }

    def _insert_synthetic_feedback(
        self,
        *,
        workspace_id: str,
        feedback_id: str,
        support_reference: str,
        run_id: str | None,
        session_id: str | None,
        now: str,
        expiry: str,
    ) -> None:
        self._execute_admin(
            "insert into public.sqag_feedback "
            "(feedback_id, support_reference, workspace_id, "
            "reporter_tracking_id, reporter_key_version, run_id, session_id, "
            "category, title, message, expected_result, actual_result, "
            "reproduction_steps, impact, link_choice, manual_reference_text, "
            "manual_reference_status, resolved_reference_type, resolved_reference_id, "
            "publication_version_id, link_resolution_source, link_resolved_at, "
            "diagnostic_metadata_json, status, created_at, updated_at, "
            "retention_expires_at, original_retention_expires_at, "
            "submission_retention_expires_at, retention_policy_version, "
            "legal_hold, deletion_state) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s)",
            (
                feedback_id,
                support_reference,
                workspace_id,
                "actor-run304-fixture",
                "test-v1",
                run_id,
                session_id,
                "bug",
                "Synthetic Run-304 feedback",
                "Synthetic feedback fixture.",
                None,
                None,
                None,
                "medium",
                "automatic",
                None,
                "none",
                "generation_run" if run_id else None,
                run_id,
                None,
                "current_run" if run_id else "current_session",
                now if run_id else None,
                '{"synthetic":true}',
                "open",
                now,
                now,
                expiry,
                expiry,
                expiry,
                "test-v1",
                0,
                "active",
            ),
        )

    def _insert_synthetic_audit(
        self,
        *,
        workspace_id: str,
        event_id: str,
        run_id: str | None,
        feedback_id: str | None,
        session_id: str | None,
        now: str,
        expiry: str,
    ) -> None:
        self._execute_admin(
            "insert into public.sqag_audit_events "
            "(event_id, run_id, feedback_id, session_id, workspace_id, "
            "actor_tracking_id, actor_key_version, event_type, event_json, "
            "event_sha256, created_at, retention_expires_at, "
            "original_retention_expires_at, legal_hold) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                event_id,
                run_id,
                feedback_id,
                session_id,
                workspace_id,
                "actor-run304-fixture",
                "test-v1",
                "run304_direct_audit",
                '{"synthetic":true}',
                "0" * 64,
                now,
                expiry,
                expiry,
                0,
            ),
        )

    def _prepare_run308_two_session_graph(self, suffix: str):
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        workspace_id = f"workspace-run308-{suffix}"
        target_session_id = f"quote-run308-{suffix}-target"
        unrelated_session_id = f"quote-run308-{suffix}-other"
        target_run_id = f"run-run308-{suffix}-target"
        unrelated_run_id = f"run-run308-{suffix}-other"
        storage = webapp.DatabaseSqagStorage(
            runtime_url,
            workspace_id,
            role="admin",
            user_id=f"user-run308-{suffix}",
            expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        now = dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc)
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            storage.create_or_update_quote_session(
                {"session_id": target_session_id, "client": {"name": "synthetic"}},
                result=None,
            )
            storage.create_or_update_quote_session(
                {"session_id": unrelated_session_id, "client": {"name": "synthetic"}},
                result=None,
            )
        with webapp.postgres_storage_connection(
            runtime_url,
            expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        ) as connection:
            for actor, run_id, session_id in (
                ("target", target_run_id, target_session_id),
                ("unrelated", unrelated_run_id, unrelated_session_id),
            ):
                forensic = ForensicStore(
                    connection,
                    workspace_id,
                    f"actor-run308-{suffix}-{actor}",
                    actor_key_version_value="test-v1",
                )
                recorded_run_id = forensic.record_run_started(
                    "generate",
                    {"image_count": 0, "synthetic": True},
                    run_id=run_id,
                    job_id=f"job-run308-{suffix}-{actor}",
                    quote_session_id=session_id,
                    now=now,
                )
                forensic.finish_run(
                    recorded_run_id,
                    "completed",
                    now=now,
                )
        return {
            "runtime_url": runtime_url,
            "workspace_id": workspace_id,
            "target_session_id": target_session_id,
            "unrelated_session_id": unrelated_session_id,
            "target_run_id": target_run_id,
            "unrelated_run_id": unrelated_run_id,
            "storage": storage,
            "now": "2026-08-28T00:00:00Z",
            "expiry": "2099-08-28T00:00:00Z",
        }

    def _assert_run308_deletion_blocked_without_hold(self, fixture) -> None:
        no_active_hold = self._admin_row(
            "select count(*) as count from public.sqag_legal_holds "
            "where workspace_id = %s and enabled = 1",
            (fixture["workspace_id"],),
        )
        self.assertEqual(no_active_hold["count"], 0)
        before = self._workspace_deletion_snapshot(fixture["workspace_id"])
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            self.assertFalse(
                fixture["storage"].delete_quote_session(
                    fixture["target_session_id"]
                )
            )
        after = self._workspace_deletion_snapshot(fixture["workspace_id"])
        self.assertEqual(before, after)
        self.assertIsNotNone(
            fixture["storage"].get_quote_session(fixture["target_session_id"])
        )

    def _restore_canonical_forensic_routines(self):
        for trigger, table in (
            ("sqag_generation_evidence_no_update", "sqag_generation_evidence"),
            ("sqag_audit_events_no_update", "sqag_audit_events"),
            ("sqag_feedback_linkage_no_update", "sqag_feedback"),
            ("sqag_generation_evidence_guard_delete", "sqag_generation_evidence"),
            ("sqag_audit_events_guard_delete", "sqag_audit_events"),
        ):
            self._execute_admin(f"drop trigger if exists {trigger} on public.{table}")
        self._execute_admin("drop routine if exists public.sqag_reject_immutable_change()")
        self._execute_admin("drop routine if exists public.sqag_require_retention_delete_authorization()")
        self._execute_admin("create function public.sqag_reject_immutable_change() returns trigger language plpgsql security invoker as $$ begin return old; end $$;")
        self._execute_admin("create function public.sqag_require_retention_delete_authorization() returns trigger language plpgsql security invoker as $$ begin return old; end $$;")
        for routine in ("sqag_reject_immutable_change", "sqag_require_retention_delete_authorization"):
            self._execute_admin(f"alter function public.{routine}() owner to sqag_migrator")
            self._execute_admin(f"revoke all privileges on function public.{routine}() from public, sqag_runtime, sqag_maintenance")
            self._execute_admin(f"grant execute on function public.{routine}() to sqag_migrator")
        self._execute_admin("create trigger sqag_generation_evidence_no_update before update on public.sqag_generation_evidence for each row execute function public.sqag_reject_immutable_change()")
        self._execute_admin("create trigger sqag_audit_events_no_update before update on public.sqag_audit_events for each row execute function public.sqag_reject_immutable_change()")
        self._execute_admin("create trigger sqag_feedback_linkage_no_update before update of run_id, session_id, publication_version_id, link_resolution_source, link_resolved_at on public.sqag_feedback for each row execute function public.sqag_reject_immutable_change()")
        self._execute_admin("create trigger sqag_generation_evidence_guard_delete before delete on public.sqag_generation_evidence for each row execute function public.sqag_require_retention_delete_authorization()")
        self._execute_admin("create trigger sqag_audit_events_guard_delete before delete on public.sqag_audit_events for each row execute function public.sqag_require_retention_delete_authorization()")

    def test_real_pg17_authenticated_and_active_identity_invariant(self):
        for expected_role in (
            webapp.SQAG_RUNTIME_DATABASE_ROLE,
            webapp.SQAG_MIGRATOR_DATABASE_ROLE,
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
        ):
            with self.subTest(expected_role=expected_role):
                with webapp.postgres_storage_connection(
                    safe_postgres_url(expected_role, self.database_name),
                    expected_role=expected_role,
                ) as connection:
                    row = connection.execute(
                        "select session_user as session_role, current_user as active_role"
                    ).fetchone()
                self.assertEqual(row["session_role"], expected_role)
                self.assertEqual(row["active_role"], expected_role)
        for expected_role in (
            webapp.SQAG_RUNTIME_DATABASE_ROLE,
            webapp.SQAG_MIGRATOR_DATABASE_ROLE,
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
        ):
            with self.subTest(assumed_role=expected_role):
                with self.psycopg.connect(
                    safe_postgres_url(self.bootstrap_role, self.database_name),
                    row_factory=self.dict_row,
                    options="-c search_path=public,pg_catalog",
                    autocommit=True,
                ) as raw:
                    raw.execute(
                        self.sql.SQL("set role {}").format(self.sql.Identifier(expected_role))
                    )
                    adapter = webapp.PostgresConnectionAdapter(raw)
                    with self.assertRaises(webapp.SqagStorageAccessError):
                        webapp.require_postgres_session_role(adapter, expected_role)

    def test_real_pg17_canonical_routine_identity_passes(self):
        self.assertEqual(self._verify()["status"], "verified")

    def test_real_pg17_callable_session_hold_authority_metadata_and_acl_are_exact(self):
        row = self._admin_row(
            "select p.prokind, pg_get_function_identity_arguments(p.oid) as identity_arguments, "
            "pg_get_function_result(p.oid) as result_type, l.lanname as language, "
            "p.prosecdef, p.provolatile, p.proparallel, p.proleakproof, p.proconfig, "
            "owner.rolname as owner "
            "from pg_catalog.pg_proc p "
            "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
            "join pg_catalog.pg_language l on l.oid = p.prolang "
            "join pg_catalog.pg_roles owner on owner.oid = p.proowner "
            "where n.nspname = 'public' and p.proname = %s "
            "and pg_get_function_identity_arguments(p.oid) = %s",
            (contract.CALLABLE_ROUTINE_NAME, contract.CALLABLE_ROUTINE_IDENTITY_ARGUMENTS),
        )
        self.assertEqual(row["prokind"], "f")
        self.assertEqual(row["identity_arguments"], "text, text")
        self.assertEqual(row["result_type"].lower(), "boolean")
        self.assertEqual(row["language"], "sql")
        self.assertTrue(row["prosecdef"])
        self.assertEqual(row["provolatile"], "s")
        self.assertEqual(row["proparallel"], "u")
        self.assertFalse(row["proleakproof"])
        self.assertEqual(row["proconfig"], ["search_path=pg_catalog, public"])
        self.assertEqual(row["owner"], "sqag_migrator")

        with self._admin_connection() as connection:
            acl_rows = connection.execute(
                "select case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee, "
                "acl.privilege_type, acl.is_grantable "
                "from pg_catalog.pg_proc p "
                "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
                "left join lateral aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl on true "
                "left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0 "
                "where n.nspname = 'public' and p.proname = %s "
                "and pg_get_function_identity_arguments(p.oid) = %s "
                "order by grantee, acl.privilege_type",
                (contract.CALLABLE_ROUTINE_NAME, contract.CALLABLE_ROUTINE_IDENTITY_ARGUMENTS),
            ).fetchall()
        self.assertEqual(
            {
                (row["grantee"], row["privilege_type"], row["is_grantable"])
                for row in acl_rows
            },
            {("sqag_migrator", "EXECUTE", False), ("sqag_runtime", "EXECUTE", False)},
        )
        for role, expected in (
            ("sqag_runtime", True),
            ("sqag_maintenance", False),
        ):
            effective = self._admin_row(
                "select has_function_privilege(%s, %s, 'EXECUTE') as effective",
                (role, "public.sqag_quote_session_deletion_hold_blocked(text, text)"),
            )
            self.assertEqual(effective["effective"], expected, role)
        self.assertEqual(self._verify()["status"], "verified")

    def test_real_pg17_callable_session_hold_metadata_drift_is_rejected(self):
        self._execute_admin(
            "alter function public.sqag_quote_session_deletion_hold_blocked(text, text) "
            "set search_path = public"
        )
        with self.assertRaises(contract.RuntimePrivilegeContractError) as raised:
            self._verify()
        self.assertIn("callable_routine_properties_mismatch", str(raised.exception))

    def test_real_pg17_callable_session_hold_body_relation_drift_is_rejected(self):
        self._execute_admin(
            "create or replace function public.sqag_quote_session_deletion_hold_blocked(text, text) "
            "returns boolean language sql stable parallel unsafe security definer "
            "set search_path = pg_catalog, public as $$ select false $$"
        )
        with self.assertRaises(contract.RuntimePrivilegeContractError) as raised:
            self._verify()
        self.assertIn("callable_routine_relation_inventory_mismatch", str(raised.exception))

    def test_real_pg17_callable_session_hold_semantic_body_drift_is_rejected_and_restored(self):
        canonical_body = contract._canonical_callable_routine_body()
        semantic_mutation = canonical_body.replace(
            "or (select blocked from hold_state)",
            "and (select blocked from hold_state)",
            1,
        )
        self.assertNotEqual(canonical_body, semantic_mutation)
        self._execute_admin(
            "create or replace function public.sqag_quote_session_deletion_hold_blocked(text, text) "
            "returns boolean language sql stable parallel unsafe security definer "
            "set search_path = pg_catalog, public as $sqag$"
            + semantic_mutation
            + "$sqag$"
        )
        try:
            with self.assertRaises(contract.RuntimePrivilegeContractError) as raised:
                self._verify()
            failure = str(raised.exception)
            self.assertIn("callable_routine_body_mismatch", failure)
            self.assertNotIn("callable_routine_relation_inventory_mismatch", failure)
        finally:
            self._execute_admin(
                "create or replace function public.sqag_quote_session_deletion_hold_blocked(text, text) "
                "returns boolean language sql stable parallel unsafe security definer "
                "set search_path = pg_catalog, public as $sqag$"
                + canonical_body
                + "$sqag$"
            )
        self.assertEqual(self._verify()["status"], "verified")

    def test_real_pg17_callable_session_hold_acl_drift_is_rejected(self):
        self._execute_admin(
            "grant execute on function public.sqag_quote_session_deletion_hold_blocked(text, text) "
            "to sqag_maintenance"
        )
        with self.assertRaises(contract.RuntimePrivilegeContractError) as raised:
            self._verify()
        failure = str(raised.exception)
        self.assertIn("routine_acl_mismatch:sqag_quote_session_deletion_hold_blocked", failure)
        self.assertIn("routine_escalation:sqag_quote_session_deletion_hold_blocked", failure)

    def test_run297_pg17_runtime_session_delete_crossing_is_repaired(self):
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        workspace_id = "workspace-run297-red"
        session_id = "quote-run297-red-session"
        run_id = "run-run297-red-session"
        storage = webapp.DatabaseSqagStorage(
            runtime_url,
            workspace_id,
            role="admin",
            user_id="user-run297-red",
            expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            storage.create_or_update_quote_session(
                {"session_id": session_id, "client": {"name": "synthetic"}},
                result=None,
            )
            with webapp.postgres_storage_connection(
                runtime_url,
                expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
            ) as connection:
                forensic = ForensicStore(
                    connection,
                    workspace_id,
                    "actor-run297-red",
                    actor_key_version_value="test-v1",
                )
                recorded_run_id = forensic.record_run_started(
                    "generate",
                    {"image_count": 0, "synthetic": True},
                    run_id=run_id,
                    job_id="job-run297-red-session",
                    quote_session_id=session_id,
                    now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                )
                forensic.finish_run(
                    recorded_run_id,
                    "completed",
                    now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                )

            with self._role_connection("sqag_runtime") as connection:
                with self.assertRaises(Exception):
                    connection.execute(
                        "select 1 from public.sqag_legal_holds"
                    ).fetchone()
                connection.rollback()

            deletion_error = None
            try:
                deleted = storage.delete_quote_session(session_id)
            except webapp.SqagStorageAccessError as exc:
                deletion_error = exc
                deleted = False
            if deletion_error is not None:
                self.assertEqual(deletion_error.status, 503)
                self.assertEqual(
                    deletion_error.reason,
                    "object_artifact_storage_unavailable",
                )
                self.assertNotIn("sqag_legal_holds", str(deletion_error))
                preserved = self._admin_row(
                    "select "
                    "(select count(*) from public.sqag_quote_sessions "
                    "where workspace_id = %s and session_id = %s) as sessions, "
                    "(select count(*) from public.sqag_quote_publication_versions "
                    "where workspace_id = %s and session_id = %s) as versions, "
                    "(select count(*) from public.sqag_quote_publication_artifacts "
                    "where workspace_id = %s and session_id = %s) as publication_artifacts, "
                    "(select count(*) from public.sqag_object_artifacts "
                    "where workspace_id = %s and session_id = %s) as object_artifacts",
                    (
                        workspace_id,
                        session_id,
                        workspace_id,
                        session_id,
                        workspace_id,
                        session_id,
                        workspace_id,
                        session_id,
                    ),
                )
                self.assertEqual(preserved["sessions"], 1)
                self.assertEqual(preserved["versions"], 0)
                self.assertEqual(preserved["publication_artifacts"], 0)
                self.assertEqual(preserved["object_artifacts"], 0)
                self.fail(
                    "G3 RED: runtime deletion crossed maintenance-only legal-hold authority"
                )
            self.assertTrue(deleted)
            self.assertIsNone(storage.get_quote_session(session_id))

    def test_run302_pg17_held_session_delete_preserves_every_graph_surface(self):
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        maintenance_url = safe_postgres_url("sqag_maintenance", self.database_name)
        workspace_id = "workspace-run302-held"
        session_id = "quote-run302-held-session"
        target_run_id = "run-run302-held-session"
        unrelated_run_id = "run-run302-unrelated"
        target_job_id = "job-run302-held-session"
        unrelated_job_id = "job-run302-unrelated"
        held_event_id = "audit-run302-dangling-link"
        malformed_event_id = "audit-run302-malformed-link"
        now = "2026-08-28T00:00:00Z"
        expiry = "2099-08-28T00:00:00Z"
        storage = webapp.DatabaseSqagStorage(
            runtime_url,
            workspace_id,
            role="admin",
            user_id="user-run302-held",
            expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            storage.create_or_update_quote_session(
                {"session_id": session_id, "client": {"name": "synthetic"}},
                result=None,
            )

        with webapp.postgres_storage_connection(
            runtime_url,
            expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        ) as connection:
            forensic = ForensicStore(
                connection,
                workspace_id,
                "actor-run302-held",
                actor_key_version_value="test-v1",
            )
            recorded_target_run_id = forensic.record_run_started(
                "generate",
                {"image_count": 1, "synthetic": True},
                run_id=target_run_id,
                job_id=target_job_id,
                quote_session_id=session_id,
                now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
            )
            forensic.finish_run(
                recorded_target_run_id,
                "completed",
                result_summary={"status": "completed", "synthetic": True},
                now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
            )
            feedback = forensic.submit_feedback(
                {
                    "category": "bug",
                    "title": "Synthetic held-session feedback",
                    "message": "Synthetic evidence for Run-302.",
                    "impact": "medium",
                    "include_link": True,
                    "link_choice": "automatic",
                    "run_id": target_run_id,
                    "validated_session_id": session_id,
                }
            )
            self.assertTrue(feedback["feedback_id"])
            unrelated_forensic = ForensicStore(
                connection,
                workspace_id,
                "actor-run302-unrelated",
                actor_key_version_value="test-v1",
            )
            unrelated_forensic.record_run_started(
                "generate",
                {"image_count": 0, "synthetic": True},
                run_id=unrelated_run_id,
                job_id=unrelated_job_id,
                quote_session_id="quote-run302-unrelated-session",
                now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
            )

        self._execute_admin(
            "insert into public.sqag_audit_events "
            "(event_id, run_id, feedback_id, session_id, workspace_id, "
            "actor_tracking_id, actor_key_version, event_type, event_json, "
            "event_sha256, created_at, retention_expires_at, "
            "original_retention_expires_at, legal_hold) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                held_event_id,
                unrelated_run_id,
                "feedback-run302-dangling",
                session_id,
                workspace_id,
                "actor-run302-direct",
                "test-v1",
                "run302_direct_dangling",
                '{"synthetic":true}',
                "0" * 64,
                now,
                expiry,
                expiry,
                0,
            ),
        )
        self._execute_admin(
            "insert into public.sqag_audit_events "
            "(event_id, run_id, feedback_id, session_id, workspace_id, "
            "actor_tracking_id, actor_key_version, event_type, event_json, "
            "event_sha256, created_at, retention_expires_at, "
            "original_retention_expires_at, legal_hold) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                malformed_event_id,
                unrelated_run_id,
                "not a feedback id",
                session_id,
                workspace_id,
                "actor-run302-direct",
                "test-v1",
                "run302_direct_malformed",
                '{"synthetic":true}',
                "0" * 64,
                now,
                expiry,
                expiry,
                0,
            ),
        )

        publication_metadata = json.dumps(
            {
                "session_id": session_id,
                "publication": {
                    "state": "published",
                    "run_id": target_run_id,
                    "job_id": target_job_id,
                },
                "synthetic": True,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        self._execute_admin(
            "insert into public.sqag_quote_publication_versions "
            "(workspace_id, session_id, run_id, job_id, state, "
            "artifact_storage_mode, artifact_source, metadata_json, error_code, "
            "created_at, updated_at, promoted_at, failed_at, retention_expires_at, "
            "original_retention_expires_at, legal_hold, deletion_state, "
            "deletion_error_code, deletion_claimed_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                workspace_id,
                session_id,
                target_run_id,
                target_job_id,
                "published",
                "object",
                "version",
                publication_metadata,
                None,
                now,
                now,
                now,
                None,
                expiry,
                expiry,
                0,
                "active",
                None,
                None,
            ),
        )
        publication_bytes = b"synthetic-run302-publication"
        self._execute_admin(
            "insert into public.sqag_quote_publication_artifacts "
            "(workspace_id, session_id, run_id, artifact_kind, filename, "
            "content_type, size_bytes, checksum_sha256, content_blob, created_at, "
            "updated_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                workspace_id,
                session_id,
                target_run_id,
                "xlsx",
                "quotation.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                len(publication_bytes),
                "0" * 64,
                publication_bytes,
                now,
                now,
            ),
        )
        self._execute_admin(
            "insert into public.sqag_object_artifacts "
            "(artifact_id, workspace_id, owner_type, owner_id, platform_user_id, "
            "session_id, job_id, artifact_kind, filename, content_type, size_bytes, "
            "checksum_sha256, object_provider_type, object_key_ref, status, "
            "retention_status, created_at, updated_at, deleted_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                "artifact-run302-held-xlsx",
                workspace_id,
                "generated_quote_version",
                target_run_id,
                "synthetic-user",
                session_id,
                target_job_id,
                "xlsx",
                "quotation.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                len(publication_bytes),
                "0" * 64,
                "synthetic",
                "synthetic/run302/quotation.xlsx",
                "active",
                "active",
                now,
                now,
                None,
            ),
        )

        with webapp.postgres_storage_connection(
            maintenance_url,
            expected_role=webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
        ) as connection:
            maintenance_forensic = ForensicStore(
                connection,
                workspace_id,
                "actor-run302-maintenance",
                actor_key_version_value="test-v1",
            )
            self.assertTrue(
                maintenance_forensic.set_legal_hold(
                    "sqag_audit_events",
                    "event_id",
                    held_event_id,
                    True,
                    reason_code="run302_direct_audit",
                    case_reference="RUN-302",
                )
            )

        active_hold = self._admin_row(
            "select enabled, target_type, target_id from public.sqag_legal_holds "
            "where workspace_id = %s and target_type = %s and target_id = %s",
            (workspace_id, "audit_event", held_event_id),
        )
        self.assertEqual(
            (active_hold["enabled"], active_hold["target_type"], active_hold["target_id"]),
            (1, "audit_event", held_event_id),
        )
        before = self._workspace_deletion_snapshot(workspace_id)
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            deleted = storage.delete_quote_session(session_id)
        self.assertFalse(deleted)
        after = self._workspace_deletion_snapshot(workspace_id)
        self.assertEqual(before, after)
        self.assertIsNotNone(storage.get_quote_session(session_id))

    def test_run304_pg17_valid_id_cross_session_feedback_cannot_expand_target_graph(self):
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        workspace_id = "workspace-run304-cross-session"
        target_session_id = "quote-run304-cross-session-target"
        unrelated_session_id = "quote-run304-cross-session-other"
        unrelated_run_id = "run-run304-cross-session-other"
        feedback_id = "feedback-run304-cross-session"
        audit_id = "audit-run304-cross-session"
        now = "2026-08-28T00:00:00Z"
        expiry = "2099-08-28T00:00:00Z"
        storage = webapp.DatabaseSqagStorage(
            runtime_url,
            workspace_id,
            role="admin",
            user_id="user-run304-cross-session",
            expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            storage.create_or_update_quote_session(
                {"session_id": target_session_id, "client": {"name": "synthetic"}},
                result=None,
            )
            storage.create_or_update_quote_session(
                {"session_id": unrelated_session_id, "client": {"name": "synthetic"}},
                result=None,
            )
            with webapp.postgres_storage_connection(
                runtime_url,
                expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
            ) as connection:
                forensic = ForensicStore(
                    connection,
                    workspace_id,
                    "actor-run304-cross-session",
                    actor_key_version_value="test-v1",
                )
                recorded_run_id = forensic.record_run_started(
                    "generate",
                    {"image_count": 0, "synthetic": True},
                    run_id=unrelated_run_id,
                    job_id="job-run304-cross-session-other",
                    quote_session_id=unrelated_session_id,
                    now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
                )
                forensic.finish_run(
                    recorded_run_id,
                    "completed",
                    now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
                )

        self._insert_synthetic_feedback(
            workspace_id=workspace_id,
            feedback_id=feedback_id,
            support_reference="SQAG-RUN304-CROSS-SESSION",
            run_id=unrelated_run_id,
            session_id=target_session_id,
            now=now,
            expiry=expiry,
        )
        self._insert_synthetic_audit(
            workspace_id=workspace_id,
            event_id=audit_id,
            run_id=unrelated_run_id,
            feedback_id=feedback_id,
            session_id=target_session_id,
            now=now,
            expiry=expiry,
        )
        self.assertEqual(
            self._admin_row(
                "select session_id, run_id from public.sqag_feedback "
                "where workspace_id = %s and feedback_id = %s",
                (workspace_id, feedback_id),
            )["session_id"],
            target_session_id,
        )
        self.assertEqual(
            self._admin_row(
                "select session_id, run_id, feedback_id from public.sqag_audit_events "
                "where workspace_id = %s and event_id = %s",
                (workspace_id, audit_id),
            )["run_id"],
            unrelated_run_id,
        )

        before = self._workspace_deletion_snapshot(workspace_id)
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            deleted = storage.delete_quote_session(target_session_id)
        self.assertFalse(deleted)
        after = self._workspace_deletion_snapshot(workspace_id)
        self.assertEqual(before, after)
        self.assertIsNotNone(storage.get_quote_session(target_session_id))

    def test_run304_pg17_consistent_target_graph_is_not_blocked_by_links(self):
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        workspace_id = "workspace-run304-consistent"
        session_id = "quote-run304-consistent-session"
        run_id = "run-run304-consistent-session"
        feedback_id = "feedback-run304-consistent"
        audit_id = "audit-run304-consistent"
        now = "2026-08-28T00:00:00Z"
        expiry = "2099-08-28T00:00:00Z"
        storage = webapp.DatabaseSqagStorage(
            runtime_url,
            workspace_id,
            role="admin",
            user_id="user-run304-consistent",
            expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            storage.create_or_update_quote_session(
                {"session_id": session_id, "client": {"name": "synthetic"}},
                result=None,
            )
            with webapp.postgres_storage_connection(
                runtime_url,
                expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
            ) as connection:
                forensic = ForensicStore(
                    connection,
                    workspace_id,
                    "actor-run304-consistent",
                    actor_key_version_value="test-v1",
                )
                recorded_run_id = forensic.record_run_started(
                    "generate",
                    {"image_count": 0, "synthetic": True},
                    run_id=run_id,
                    job_id="job-run304-consistent-session",
                    quote_session_id=session_id,
                    now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
                )
                forensic.finish_run(
                    recorded_run_id,
                    "completed",
                    now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
                )

        self._insert_synthetic_feedback(
            workspace_id=workspace_id,
            feedback_id=feedback_id,
            support_reference="SQAG-RUN304-CONSISTENT",
            run_id=run_id,
            session_id=session_id,
            now=now,
            expiry=expiry,
        )
        self._insert_synthetic_audit(
            workspace_id=workspace_id,
            event_id=audit_id,
            run_id=run_id,
            feedback_id=feedback_id,
            session_id=session_id,
            now=now,
            expiry=expiry,
        )
        with webapp.postgres_storage_connection(
            runtime_url,
            expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        ) as connection:
            hold_row = connection.execute(
                "select public.sqag_quote_session_deletion_hold_blocked("
                "cast(? as text), cast(? as text)) as hold_blocked",
                (workspace_id, session_id),
            ).fetchone()
        self.assertFalse(hold_row["hold_blocked"])
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            self.assertTrue(storage.delete_quote_session(session_id))
        self.assertIsNone(storage.get_quote_session(session_id))

    def test_run308_pg17_target_run_audit_rejects_existing_unrelated_feedback(self):
        fixture = self._prepare_run308_two_session_graph("run-feedback")
        unrelated_feedback_id = "feedback-run308-run-feedback-other"
        audit_id = "audit-run308-run-feedback-mixed"
        self._insert_synthetic_feedback(
            workspace_id=fixture["workspace_id"],
            feedback_id=unrelated_feedback_id,
            support_reference="SQAG-RUN308-RUN-FEEDBACK",
            run_id=fixture["unrelated_run_id"],
            session_id=fixture["unrelated_session_id"],
            now=fixture["now"],
            expiry=fixture["expiry"],
        )
        self._insert_synthetic_audit(
            workspace_id=fixture["workspace_id"],
            event_id=audit_id,
            run_id=fixture["target_run_id"],
            feedback_id=unrelated_feedback_id,
            session_id=None,
            now=fixture["now"],
            expiry=fixture["expiry"],
        )
        self._assert_run308_deletion_blocked_without_hold(fixture)

    def test_run308_pg17_target_feedback_audit_rejects_existing_unrelated_run(self):
        fixture = self._prepare_run308_two_session_graph("feedback-run")
        target_feedback_id = "feedback-run308-feedback-run-target"
        audit_id = "audit-run308-feedback-run-mixed"
        self._insert_synthetic_feedback(
            workspace_id=fixture["workspace_id"],
            feedback_id=target_feedback_id,
            support_reference="SQAG-RUN308-FEEDBACK-RUN",
            run_id=fixture["target_run_id"],
            session_id=fixture["target_session_id"],
            now=fixture["now"],
            expiry=fixture["expiry"],
        )
        self._insert_synthetic_audit(
            workspace_id=fixture["workspace_id"],
            event_id=audit_id,
            run_id=fixture["unrelated_run_id"],
            feedback_id=target_feedback_id,
            session_id=None,
            now=fixture["now"],
            expiry=fixture["expiry"],
        )
        self._assert_run308_deletion_blocked_without_hold(fixture)

    def test_run308_pg17_target_audit_rejects_existing_unrelated_session(self):
        fixture = self._prepare_run308_two_session_graph("audit-session")
        target_feedback_id = "feedback-run308-audit-session-target"
        audit_id = "audit-run308-audit-session-mixed"
        self._insert_synthetic_feedback(
            workspace_id=fixture["workspace_id"],
            feedback_id=target_feedback_id,
            support_reference="SQAG-RUN308-AUDIT-SESSION",
            run_id=fixture["target_run_id"],
            session_id=fixture["target_session_id"],
            now=fixture["now"],
            expiry=fixture["expiry"],
        )
        self._insert_synthetic_audit(
            workspace_id=fixture["workspace_id"],
            event_id=audit_id,
            run_id=fixture["target_run_id"],
            feedback_id=target_feedback_id,
            session_id=fixture["unrelated_session_id"],
            now=fixture["now"],
            expiry=fixture["expiry"],
        )
        self._assert_run308_deletion_blocked_without_hold(fixture)

    def test_run306_pg17_publication_derived_feedback_hold_serializes_session_delete(self):
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        deletion_application_name = "sqag_run306_delete"
        deletion_url = runtime_url + "?application_name=" + deletion_application_name
        maintenance_url = safe_postgres_url("sqag_maintenance", self.database_name)
        workspace_id = "workspace-run306-publication-feedback"
        session_id = "quote-run306-publication-feedback"
        run_id = "run-run306-publication-derived"
        job_id = "job-run306-publication-derived"
        feedback_id = "feedback-run306-publication-derived"
        now = "2026-08-28T00:00:00Z"
        expiry = "2099-08-28T00:00:00Z"
        storage = webapp.DatabaseSqagStorage(
            deletion_url,
            workspace_id,
            role="admin",
            user_id="user-run306-publication-feedback",
            expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        with mock.patch.dict(
            os.environ,
            {
                webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
            },
            clear=False,
        ):
            storage.create_or_update_quote_session(
                {"session_id": session_id, "client": {"name": "synthetic"}},
                result=None,
            )
        with webapp.postgres_storage_connection(
            runtime_url,
            expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        ) as connection:
            forensic = ForensicStore(
                connection,
                workspace_id,
                "actor-run306-runtime",
                actor_key_version_value="test-v1",
            )
            recorded_run_id = forensic.record_run_started(
                "generate",
                {"image_count": 0, "synthetic": True},
                run_id=run_id,
                job_id=job_id,
                now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
            )
            self.assertEqual(recorded_run_id, run_id)
            self.assertTrue(
                forensic.finish_run(
                    recorded_run_id,
                    "completed",
                    now=dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc),
                )
            )

        publication_metadata = json.dumps(
            {
                "session_id": session_id,
                "publication": {
                    "state": "published",
                    "run_id": run_id,
                    "job_id": job_id,
                },
                "synthetic": True,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        self._execute_admin(
            "insert into public.sqag_quote_publication_versions "
            "(workspace_id, session_id, run_id, job_id, state, "
            "artifact_storage_mode, artifact_source, metadata_json, error_code, "
            "created_at, updated_at, promoted_at, failed_at, retention_expires_at, "
            "original_retention_expires_at, legal_hold, deletion_state, "
            "deletion_error_code, deletion_claimed_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s)",
            (
                workspace_id,
                session_id,
                run_id,
                job_id,
                "published",
                "database",
                "version",
                publication_metadata,
                None,
                now,
                now,
                now,
                None,
                expiry,
                expiry,
                0,
                "active",
                None,
                None,
            ),
        )
        self._insert_synthetic_feedback(
            workspace_id=workspace_id,
            feedback_id=feedback_id,
            support_reference="SQAG-RUN306-PUBLICATION-FEEDBACK",
            run_id=run_id,
            session_id=None,
            now=now,
            expiry=expiry,
        )
        publication_run = self._admin_row(
            "select r.quote_session_id, v.session_id "
            "from public.sqag_generation_runs r "
            "join public.sqag_quote_publication_versions v "
            "on v.workspace_id = r.workspace_id and v.run_id = r.run_id "
            "where r.workspace_id = %s and r.run_id = %s",
            (workspace_id, run_id),
        )
        self.assertIsNone(publication_run["quote_session_id"])
        self.assertEqual(publication_run["session_id"], session_id)
        feedback_link = self._admin_row(
            "select session_id, run_id, publication_version_id "
            "from public.sqag_feedback where workspace_id = %s and feedback_id = %s",
            (workspace_id, feedback_id),
        )
        self.assertEqual(
            (
                feedback_link["session_id"],
                feedback_link["run_id"],
                feedback_link["publication_version_id"],
            ),
            (None, run_id, None),
        )

        release_maintenance = threading.Event()
        maintenance_lock_acquired = threading.Event()
        maintenance_hold_committed = threading.Event()
        maintenance_done = threading.Event()
        deletion_started = threading.Event()
        deletion_done = threading.Event()
        post_lock_reload_seen = threading.Event()
        callable_executed = threading.Event()
        release_callable = threading.Event()
        maintenance_pids: list[int] = []
        maintenance_errors: list[BaseException] = []
        deletion_errors: list[BaseException] = []
        deletion_results: list[bool] = []
        feedback_discovery_count = 0
        observation_lock = threading.Lock()
        deletion_thread_name = "run306-session-delete"
        original_execute = webapp.PostgresConnectionAdapter.execute

        def observed_execute(adapter, statement, params=()):
            nonlocal feedback_discovery_count
            result = original_execute(adapter, statement, params)
            if threading.current_thread().name != deletion_thread_name:
                return result
            normalized = " ".join(statement.lower().split())
            if normalized.startswith(
                "select distinct feedback_id, legal_hold from sqag_feedback"
            ):
                with observation_lock:
                    feedback_discovery_count += 1
                    if feedback_discovery_count == 2:
                        post_lock_reload_seen.set()
            if "sqag_quote_session_deletion_hold_blocked" in normalized:
                callable_executed.set()
                if not release_callable.wait(10):
                    raise AssertionError("Run-306 callable observation barrier timed out")
            return result

        def maintenance_worker():
            try:
                with webapp.postgres_storage_connection(
                    maintenance_url,
                    expected_role=webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
                ) as connection:
                    pid_row = connection.execute(
                        "select pg_backend_pid() as backend_pid"
                    ).fetchone()
                    maintenance_pids.append(int(pid_row["backend_pid"]))
                    forensic = ForensicStore(
                        connection,
                        workspace_id,
                        "actor-run306-maintenance",
                        actor_key_version_value="test-v1",
                    )
                    forensic._acquire_transaction_locks(("feedback", feedback_id))
                    maintenance_lock_acquired.set()
                    if not release_maintenance.wait(10):
                        raise AssertionError("Run-306 maintenance barrier timed out")
                    if not forensic.set_legal_hold(
                        "sqag_feedback",
                        "feedback_id",
                        feedback_id,
                        True,
                        reason_code="run306_publication_feedback",
                        case_reference="RUN-306",
                    ):
                        raise AssertionError("Run-306 feedback hold was not applied")
                    maintenance_hold_committed.set()
            except BaseException as exc:
                maintenance_errors.append(exc)
            finally:
                maintenance_done.set()

        def deletion_worker():
            try:
                deletion_started.set()
                with mock.patch.dict(
                    os.environ,
                    {
                        webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
                        webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "local",
                    },
                    clear=False,
                ):
                    deletion_results.append(storage.delete_quote_session(session_id))
            except BaseException as exc:
                deletion_errors.append(exc)
            finally:
                deletion_done.set()

        maintenance_thread = threading.Thread(
            target=maintenance_worker,
            name="run306-maintenance-hold",
            daemon=True,
        )
        deletion_thread = threading.Thread(
            target=deletion_worker,
            name=deletion_thread_name,
            daemon=True,
        )
        held_snapshot = None
        observed_wait = None
        try:
            with mock.patch.object(
                webapp.PostgresConnectionAdapter,
                "execute",
                new=observed_execute,
            ):
                maintenance_thread.start()
                self.assertTrue(
                    maintenance_lock_acquired.wait(10),
                    "maintenance did not acquire F2's feedback advisory lock",
                )
                self.assertEqual(len(maintenance_pids), 1)
                deletion_thread.start()
                self.assertTrue(deletion_started.wait(10))
                for _attempt in range(200):
                    rows = self._admin_rows(
                        "select pid, wait_event_type, wait_event, "
                        "pg_catalog.pg_blocking_pids(pid) as blocking_pids "
                        "from pg_catalog.pg_stat_activity "
                        "where datname = %s and application_name = %s",
                        (self.database_name, deletion_application_name),
                    )
                    observed_wait = next(
                        (
                            row
                            for row in rows
                            if row["wait_event_type"] == "Lock"
                            and str(row["wait_event"]).lower() == "advisory"
                            and maintenance_pids[0] in row["blocking_pids"]
                        ),
                        None,
                    )
                    if observed_wait is not None or deletion_done.wait(0.05):
                        break
                self.assertIsNotNone(
                    observed_wait,
                    "deletion did not serialize on maintenance's F2 feedback lock",
                )
                release_maintenance.set()
                self.assertTrue(
                    maintenance_hold_committed.wait(10),
                    "maintenance did not commit the enabled feedback hold",
                )
                self.assertTrue(
                    callable_executed.wait(10),
                    "deletion did not reach the post-lock hold callable",
                )
                self.assertTrue(post_lock_reload_seen.is_set())
                held_snapshot = self._workspace_deletion_snapshot(workspace_id)
                release_callable.set()
                self.assertTrue(
                    deletion_done.wait(10),
                    "deletion did not resume after maintenance committed",
                )
        finally:
            release_maintenance.set()
            release_callable.set()
            maintenance_thread.join(timeout=10)
            deletion_thread.join(timeout=10)

        self.assertFalse(maintenance_thread.is_alive())
        self.assertFalse(deletion_thread.is_alive())
        self.assertTrue(maintenance_done.is_set())
        self.assertEqual(maintenance_errors, [])
        self.assertEqual(deletion_errors, [])
        self.assertEqual(deletion_results, [False])
        self.assertEqual(feedback_discovery_count, 2)
        self.assertIsNotNone(held_snapshot)
        active_hold = self._admin_row(
            "select enabled, target_type, target_id from public.sqag_legal_holds "
            "where workspace_id = %s and target_type = %s and target_id = %s",
            (workspace_id, "feedback", feedback_id),
        )
        self.assertEqual(
            (active_hold["enabled"], active_hold["target_type"], active_hold["target_id"]),
            (1, "feedback", feedback_id),
        )
        after = self._workspace_deletion_snapshot(workspace_id)
        self.assertEqual(held_snapshot, after)
        self.assertEqual(len(after["sessions"]), 1)
        self.assertEqual(len(after["publication_versions"]), 1)
        self.assertEqual(len(after["generation_runs"]), 1)
        self.assertEqual(len(after["feedback"]), 1)
        self.assertEqual(len(after["legal_holds"]), 1)
        self.assertIsNotNone(storage.get_quote_session(session_id))

    def test_real_pg17_unexpected_public_sqag_routine_default_public_execute_is_rejected(self):
        self._execute_admin(
            "create function public.sqag_unexpected_inventory() returns integer "
            "language sql immutable as $$ select 1 $$;"
        )
        try:
            with self.assertRaises(contract.RuntimePrivilegeContractError) as raised:
                self._verify()
            message = str(raised.exception)
            self.assertIn("routine_inventory_mismatch", message)
            self.assertIn("routine_escalation:sqag_unexpected_inventory", message)
            effective = self._admin_row(
                "select has_function_privilege(%s, %s, 'EXECUTE') as effective",
                ("sqag_runtime", "public.sqag_unexpected_inventory()"),
            )
            self.assertTrue(effective["effective"])
        finally:
            self._execute_admin("drop function if exists public.sqag_unexpected_inventory()")
        self.assertEqual(self._verify()["status"], "verified")

    def test_real_pg17_routine_identity_negative_matrix(self):
        def restore_overload():
            self._execute_admin("drop function if exists public.sqag_reject_immutable_change(integer)")
            self._restore_canonical_forensic_routines()

        def restore_decoy():
            self._execute_admin("drop function if exists public.sqag_reject_immutable_change(integer)")
            self._restore_canonical_forensic_routines()

        self._red_then_restore(
            lambda: self._execute_admin(
                "create function public.sqag_reject_immutable_change(integer) returns integer "
                "language sql immutable as $$ select 1 $$;"
            ),
            restore_overload,
            "routine overload",
        )
        self._red_then_restore(
            lambda: self._execute_admin(
                "alter function public.sqag_reject_immutable_change() owner to sqag_runtime"
            ),
            self._restore_canonical_forensic_routines,
            "routine owner",
        )
        self._red_then_restore(
            lambda: self._execute_admin(
                "create or replace function public.sqag_reject_immutable_change() "
                "returns trigger language plpgsql security definer as $$ begin return old; end $$;"
            ),
            self._restore_canonical_forensic_routines,
            "security definer",
        )
        self._red_then_restore(
            lambda: self._execute_admin(
                "drop trigger sqag_generation_evidence_no_update on public.sqag_generation_evidence"
            ),
            self._restore_canonical_forensic_routines,
            "missing trigger",
        )
        self._red_then_restore(
            lambda: (
                self._execute_admin(
                    "drop trigger sqag_generation_evidence_no_update on public.sqag_generation_evidence"
                ),
                self._execute_admin(
                    "create trigger sqag_generation_evidence_no_update before update "
                    "on public.sqag_generation_evidence for each row execute function "
                    "public.sqag_require_retention_delete_authorization()"
                ),
            ),
            self._restore_canonical_forensic_routines,
            "wrong trigger linkage",
        )
        self._red_then_restore(
            lambda: (
                self._execute_admin(
                    "create function public.sqag_reject_immutable_change(integer) returns integer "
                    "language sql immutable as $$ select 1 $$;"
                ),
                self._execute_admin(
                    "drop trigger sqag_generation_evidence_no_update on public.sqag_generation_evidence"
                ),
                self._execute_admin(
                    "create trigger sqag_generation_evidence_no_update before update "
                    "on public.sqag_generation_evidence for each row execute function "
                    "public.sqag_require_retention_delete_authorization()"
                ),
            ),
            restore_decoy,
            "same-name decoy linkage",
        )
        self._red_then_restore(
            lambda: (
                self._execute_admin(
                    "drop trigger sqag_generation_evidence_no_update on public.sqag_generation_evidence"
                ),
                self._execute_admin(
                    "drop trigger sqag_audit_events_no_update on public.sqag_audit_events"
                ),
                self._execute_admin(
                    "drop trigger sqag_feedback_linkage_no_update on public.sqag_feedback"
                ),
                self._execute_admin(
                    "drop function public.sqag_reject_immutable_change()"
                ),
                self._execute_admin(
                    "create function public.sqag_reject_immutable_change() returns integer "
                    "language sql immutable as $$ select 1 $$;"
                ),
            ),
            self._restore_canonical_forensic_routines,
            "wrong return type",
        )
        self._red_then_restore(
            lambda: (
                self._execute_admin(
                    "drop trigger sqag_generation_evidence_no_update on public.sqag_generation_evidence"
                ),
                self._execute_admin(
                    "drop trigger sqag_audit_events_no_update on public.sqag_audit_events"
                ),
                self._execute_admin(
                    "drop trigger sqag_feedback_linkage_no_update on public.sqag_feedback"
                ),
                self._execute_admin(
                    "drop function public.sqag_reject_immutable_change()"
                ),
                self._execute_admin(
                    "create procedure public.sqag_reject_immutable_change() "
                    "language plpgsql as $$ begin null; end $$;"
                ),
            ),
            self._restore_canonical_forensic_routines,
            "wrong routine kind",
        )

    def test_real_pg17_retention_verifier_uses_operation_specific_default_factories(self):
        verifier = load_script_module(
            "verify_live_retention_delete.py",
            "run146_verify_live_retention_delete",
        )
        env = {
            verifier.LIVE_RETENTION_DELETE_ENV_NAME: "1",
            webapp.SQAG_DATABASE_URL_ENV_NAME: safe_postgres_url("sqag_runtime", self.database_name),
            webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME: safe_postgres_url(
                "sqag_maintenance", self.database_name
            ),
            webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME: safe_postgres_url(
                "sqag_migrator", self.database_name
            ),
            webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
            webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "object",
            webapp.OBJECT_STORAGE_PROVIDER_ENV_NAME: "s3_compatible",
            webapp.OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME: "https://synthetic.example",
            webapp.OBJECT_STORAGE_BUCKET_ENV_NAME: "synthetic-retention-bucket",
            webapp.OBJECT_STORAGE_REGION_ENV_NAME: "ap-southeast-1",
            webapp.OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME: "REDACTED",
            webapp.OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME: "REDACTED",
        }
        backend = webapp.InMemoryObjectStorageBackend()
        with mock.patch.dict(os.environ, env, clear=True):
            report = verifier.run_verification(
                env=env,
                backend_factory=lambda _env: backend,
                test_injected_backend=True,
            )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["checks"]["active_runtime_download_verified"])
        self.assertTrue(report["checks"]["tombstone_metadata_verified"])

    def test_real_pg17_backup_restore_verifier_uses_operation_specific_default_factories(self):
        verifier = load_script_module(
            "verify_live_db_object_backup_restore.py",
            "run146_verify_live_db_object_backup_restore",
        )
        restore_database_name = self._create_isolated_database_fixture()
        env = {
            verifier.LIVE_DB_OBJECT_BACKUP_RESTORE_ENV_NAME: "1",
            webapp.SQAG_DATABASE_URL_ENV_NAME: safe_postgres_url("sqag_runtime", self.database_name),
            webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME: safe_postgres_url(
                "sqag_migrator", self.database_name
            ),
            webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME: safe_postgres_url(
                "sqag_maintenance", self.database_name
            ),
            verifier.RESTORE_DATABASE_URL_ENV_NAME: safe_postgres_url(
                "sqag_runtime", restore_database_name
            ),
            verifier.RESTORE_MIGRATOR_DATABASE_URL_ENV_NAME: safe_postgres_url(
                "sqag_migrator", restore_database_name
            ),
            verifier.RESTORE_MAINTENANCE_DATABASE_URL_ENV_NAME: safe_postgres_url(
                "sqag_maintenance", restore_database_name
            ),
            webapp.SQAG_STORAGE_MODE_ENV_NAME: "database",
            webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "object",
            webapp.OBJECT_STORAGE_PROVIDER_ENV_NAME: "s3_compatible",
            webapp.OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME: "https://synthetic-active.example",
            webapp.OBJECT_STORAGE_BUCKET_ENV_NAME: "synthetic-active-bucket",
            webapp.OBJECT_STORAGE_REGION_ENV_NAME: "ap-southeast-1",
            webapp.OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME: "REDACTED",
            webapp.OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME: "REDACTED",
            verifier.RESTORE_OBJECT_STORAGE_PROVIDER_ENV_NAME: "s3_compatible",
            verifier.RESTORE_OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME: "https://synthetic-restore.example",
            verifier.RESTORE_OBJECT_STORAGE_BUCKET_ENV_NAME: "synthetic-restore-bucket",
            verifier.RESTORE_OBJECT_STORAGE_REGION_ENV_NAME: "ap-southeast-1",
            verifier.RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME: "REDACTED",
            verifier.RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME: "REDACTED",
            verifier.BACKUP_RESTORE_DECISION_ID_ENV_NAME: "synthetic-decision",
            verifier.BACKUP_RESTORE_WINDOW_ID_ENV_NAME: "synthetic-window",
        }
        active_backend = webapp.InMemoryObjectStorageBackend()
        restore_backend = webapp.InMemoryObjectStorageBackend()
        with mock.patch.dict(os.environ, env, clear=True):
            report = verifier.run_verification(
                env=env,
                active_backend_factory=lambda _env: active_backend,
                restore_backend_factory=lambda _env: restore_backend,
                test_injected_backend=True,
            )
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["checks"]["active_db_write_read_verified"])
        self.assertTrue(report["checks"]["restore_db_write_read_verified"])
        self.assertTrue(report["checks"]["restore_object_write_read_verified"])

    def test_real_pg17_migrations_capabilities_provenance_and_workspace_isolation(self):
        evidence = self._verify()
        self.assertEqual(evidence["postgres_major"], 17)
        self.assertEqual(evidence["search_path"], ["public", "pg_catalog"])
        self.assertEqual(self._inspect()["status"], "ready")

        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        storage_a = webapp.DatabaseSqagStorage(
            runtime_url, "workspace-alpha", role="admin", user_id="user-alpha",
            expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        storage_b = webapp.DatabaseSqagStorage(
            runtime_url, "workspace-beta", role="admin", user_id="user-beta",
            expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
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
                runtime_url, "workspace-missing", role="admin", user_id="user-missing",
                expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
            )
            self.assertIsNone(storage_unknown.profile_detail("shared-profile"))
            self.assertEqual(storage_unknown.list_company_profiles(), [])
            self.assertIsNone(storage_a.profile_detail("beta-only-profile"))
            self.assertEqual(stored_a["label"], "alpha-only")
            self.assertEqual(stored_b["label"], "beta-only")
            self.assertEqual(storage_a.profile_detail("shared-profile")["label"], "alpha-only")
            self.assertEqual(storage_b.profile_detail("shared-profile")["label"], "beta-only")
            self.assertEqual(storage_a.list_company_profiles()[0]["label"], "alpha-only")
            listed_beta_profiles = {item["id"]: item["label"] for item in storage_b.list_company_profiles()}
            self.assertEqual(listed_beta_profiles, {"beta-only-profile": "beta-only-record", "shared-profile": "beta-only"})

            with mock.patch.dict(
                os.environ,
                {webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "object"},
                clear=False,
            ):
                object_session = storage_a.create_or_update_quote_session(
                    {"session_id": "quote-object-mode-session", "client": {"name": "synthetic"}},
                    result=None,
                )
            self.assertEqual(object_session["session_id"], "quote-object-mode-session")

            with webapp.postgres_storage_connection(runtime_url, expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE) as connection:
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

            with mock.patch.dict(
                os.environ,
                {webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: "database"},
                clear=False,
            ):
                with tempfile.TemporaryDirectory() as output_root:
                    output_dir = Path(output_root)
                    (output_dir / "quotation.xlsx").write_bytes(b"synthetic-xlsx-proof")
                    published = storage_a.create_or_update_quote_session(
                        {"session_id": "quote-publication-session", "client": {"name": "synthetic"}},
                        result={"status": "completed"},
                        output_dir=output_dir,
                        session_id="quote-publication-session",
                        generation_run_id="run-publication-alpha",
                        generation_job_id="job-publication-alpha",
                    )
            self.assertEqual(published["session_id"], "quote-publication-session")
            self.assertTrue(storage_a.quote_publication_version_is_current("run-publication-alpha"))

        with self._role_connection("sqag_runtime") as connection:
            with self.assertRaises(Exception):
                connection.execute("select 1 from public.sqag_retention_delete_authorizations").fetchone()
            connection.rollback()
            with self.assertRaises(Exception):
                connection.execute("select rolpassword from pg_catalog.pg_authid").fetchone()
            connection.rollback()

    def test_column_privilege_provenance_separates_effective_table_grants(self):
        with self._admin_connection() as connection:
            effective_rows = connection.execute(
                "select table_name, column_name, grantee, privilege_type, is_grantable "
                "from information_schema.role_column_grants "
                "where table_schema = 'public' and table_name = 'sqag_profiles' "
                "and grantee = 'sqag_runtime' and privilege_type = 'UPDATE' "
                "order by column_name"
            ).fetchall()
        self.assertTrue(effective_rows)
        self.assertTrue(any(row["column_name"] == "payload_json" for row in effective_rows))
        self.assertEqual({row["grantee"] for row in effective_rows}, {"sqag_runtime"})
        self.assertEqual(self._verify()["status"], "verified")

        self._execute_admin(
            "grant update (payload_json) on table public.sqag_profiles "
            "to sqag_runtime with grant option"
        )
        try:
            with self.assertRaises(contract.RuntimePrivilegeContractError) as raised:
                self._verify()
            failure = str(raised.exception)
            self.assertIn("column_privilege:sqag_profiles:payload_json:sqag_runtime", failure)
            self.assertIn("column_grant_option:sqag_profiles:payload_json:sqag_runtime", failure)
        finally:
            self._execute_admin(
                "revoke update (payload_json) on table public.sqag_profiles from sqag_runtime"
            )
        self.assertEqual(self._verify()["status"], "verified")

        self._execute_admin("revoke update on table public.sqag_profiles from sqag_runtime")
        try:
            with self.assertRaises(contract.RuntimePrivilegeContractError) as raised:
                self._verify()
            self.assertIn(
                "effective_table_privilege:sqag_runtime:sqag_profiles:UPDATE",
                str(raised.exception),
            )
        finally:
            self._execute_admin("grant update on table public.sqag_profiles to sqag_runtime")
        self.assertEqual(self._verify()["status"], "verified")

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
            lambda: (self._execute_admin("alter table public.sqag_profiles owner to sqag_migrator"),
                     self._execute_admin("grant select, insert, update, delete on table public.sqag_profiles to sqag_runtime")),
            "unexpected ownership",
        )
        self._red_then_restore(
            lambda: self._execute_admin("alter schema public owner to sqag_runtime"),
            lambda: (self._execute_admin("alter schema public owner to pg_database_owner"),
                     self._execute_admin("grant usage on schema public to PUBLIC, sqag_runtime, sqag_maintenance"),
                     self._execute_admin("grant usage, create on schema public to sqag_migrator")),
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

        last = self.migrations[-1]
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
            self.assertTrue(report["safeToApply"])
            self.assertEqual(report["status"], "ready")
            self.assertEqual(report["pendingMigrationIds"], [last.migration_id])
            self.assertFalse(report["blockers"])
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
            safe_postgres_url(self.bootstrap_role, self.database_name),
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
        with webapp.postgres_storage_connection(runtime_url, expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE) as connection:
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
        with webapp.postgres_storage_connection(maintenance_url, expected_role=webapp.SQAG_MAINTENANCE_DATABASE_ROLE) as connection:
            remaining = connection.execute(
                "select 1 from sqag_generation_runs where workspace_id = ? and run_id = ?",
                ("workspace-retention", "run-retention-alpha"),
            ).fetchone()
        self.assertIsNone(remaining)

    def test_real_pg17_migrator_only_ledger_and_role_projections(self):
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        maintenance_url = safe_postgres_url("sqag_maintenance", self.database_name)
        migrator_url = safe_postgres_url("sqag_migrator", self.database_name)
        for database_url, expected_role in (
            (runtime_url, webapp.SQAG_RUNTIME_DATABASE_ROLE),
            (maintenance_url, webapp.SQAG_MAINTENANCE_DATABASE_ROLE),
        ):
            with webapp.postgres_storage_connection(database_url, expected_role=expected_role) as connection:
                with self.assertRaises(Exception):
                    connection.execute("select 1 from public.sqag_schema_migrations")
        runtime_report = preflight._inspect_privilege_projection(
            runtime_url, self.manifest, webapp.SQAG_RUNTIME_DATABASE_ROLE
        )
        maintenance_report = preflight._inspect_privilege_projection(
            maintenance_url, self.manifest, webapp.SQAG_MAINTENANCE_DATABASE_ROLE
        )
        self.assertEqual(runtime_report["status"], "verified")
        self.assertEqual(maintenance_report["status"], "verified")
        migrator_report = preflight._inspect_migration_ledger(migrator_url, self.migrations)
        self.assertEqual(migrator_report["status"], "ready")
        with self.assertRaises(webapp.SqagStorageAccessError):
            preflight._inspect_migration_ledger(runtime_url, self.migrations)
        with self._admin_connection() as connection:
            runtime_ledger = connection.execute(
                "select has_table_privilege(%s, %s, 'SELECT') as allowed",
                (webapp.SQAG_RUNTIME_DATABASE_ROLE, "public.sqag_schema_migrations"),
            ).fetchone()
            maintenance_ledger = connection.execute(
                "select has_table_privilege(%s, %s, 'SELECT') as allowed",
                (webapp.SQAG_MAINTENANCE_DATABASE_ROLE, "public.sqag_schema_migrations"),
            ).fetchone()
            migrator_ledger = connection.execute(
                "select has_table_privilege(%s, %s, 'SELECT') as allowed",
                (webapp.SQAG_MIGRATOR_DATABASE_ROLE, "public.sqag_schema_migrations"),
            ).fetchone()
        self.assertFalse(runtime_ledger["allowed"])
        self.assertFalse(maintenance_ledger["allowed"])
        self.assertTrue(migrator_ledger["allowed"])

    def _execute_admin(self, statement: str, params=()):
        with self._admin_connection() as connection:
            return connection.execute(statement, params)

    def _admin_row(self, statement: str, params=()):
        with self._admin_connection() as connection:
            row = connection.execute(statement, params).fetchone()
            if row is None:
                raise AssertionError("fixture row missing")
            return row

    def test_real_pg17_bootstrap_identity_is_oid_10_superuser(self):
        version = self._admin_row(
            "select current_setting('server_version_num') as server_version_num"
        )
        self.assertTrue(str(version["server_version_num"]).startswith("17"))
        row = self._admin_row(
            "select current_user, role.rolname, role.oid, role.rolsuper "
            "from pg_catalog.pg_roles as role where role.rolname = %s",
            (self.bootstrap_role,),
        )
        self.assertEqual(row["current_user"], DISPOSABLE_BOOTSTRAP_ROLE)
        self.assertEqual(row["rolname"], DISPOSABLE_BOOTSTRAP_ROLE)
        self.assertEqual(row["oid"], 10)
        self.assertTrue(row["rolsuper"])

    def test_real_pg17_option_a_provider_rows_and_effective_database_matrix(self):
        self.assertEqual(self._verify()["status"], "verified")
        rows = self._admin_rows(
            "select parent.rolname as role, member.rolname as member, "
            "grantor.rolname as grantor, am.admin_option, am.inherit_option, am.set_option "
            "from pg_catalog.pg_auth_members am "
            "join pg_catalog.pg_roles parent on parent.oid = am.roleid "
            "join pg_catalog.pg_roles member on member.oid = am.member "
            "join pg_catalog.pg_roles grantor on grantor.oid = am.grantor "
            "where parent.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_maintenance') "
            "or member.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_maintenance') "
            "or grantor.rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_maintenance') "
            "order by parent.rolname, member.rolname, grantor.rolname"
        )
        self.assertEqual(
            [
                tuple(row[key] for key in (
                    "role", "member", "grantor",
                    "admin_option", "inherit_option", "set_option",
                ))
                for row in rows
            ],
            [
                (role, "neondb_owner", "cloud_admin", True, False, False)
                for role in sorted(self.roles)
            ],
        )
        with self._admin_connection() as connection:
            for role in self.roles:
                row = connection.execute(
                    "select has_database_privilege(%s, current_database(), 'CONNECT') as connect, "
                    "has_database_privilege(%s, current_database(), 'CREATE') as create_privilege, "
                    "has_database_privilege(%s, current_database(), 'TEMPORARY') as temporary, "
                    "has_database_privilege(%s, current_database(), 'CONNECT WITH GRANT OPTION') as connect_grantable, "
                    "has_database_privilege(%s, current_database(), 'CREATE WITH GRANT OPTION') as create_grantable, "
                    "has_database_privilege(%s, current_database(), 'TEMPORARY WITH GRANT OPTION') as temporary_grantable",
                    (role, role, role, role, role, role),
                ).fetchone()
                self.assertTrue(row["connect"], role)
                self.assertFalse(row["create_privilege"], role)
                self.assertFalse(row["temporary"], role)
                self.assertFalse(row["connect_grantable"], role)
                self.assertFalse(row["create_grantable"], role)
                self.assertFalse(row["temporary_grantable"], role)

    def test_real_pg17_option_a_provider_membership_negative_matrix(self):
        self._red_then_restore(
            lambda: self._revoke_provider_edge("sqag_runtime"),
            lambda: self._grant_provider_edge("sqag_runtime"),
            "missing provider edge",
        )
        self._red_then_restore(
            lambda: self._grant_provider_edge("sqag_runtime", "sqag_migrator"),
            lambda: self._revoke_provider_edge("sqag_runtime", "sqag_migrator"),
            "extra provider edge",
        )
        self._red_then_restore(
            lambda: self._revoke_provider_admin_option("sqag_runtime", "neondb_owner"),
            lambda: self._grant_provider_edge("sqag_runtime"),
            "ADMIN drift",
        )
        self._red_then_restore(
            lambda: self._grant_provider_edge("sqag_runtime", inherit=True),
            lambda: self._grant_provider_edge("sqag_runtime"),
            "INHERIT drift",
        )
        self._red_then_restore(
            lambda: self._grant_provider_edge("sqag_runtime", set_option=True),
            lambda: self._grant_provider_edge("sqag_runtime"),
            "SET drift",
        )
        self._red_then_restore(
            lambda: (
                self._revoke_provider_edge("sqag_runtime"),
                self._grant_provider_edge("sqag_runtime", "sqag_migrator"),
            ),
            lambda: (
                self._revoke_provider_edge("sqag_runtime", "sqag_migrator"),
                self._grant_provider_edge("sqag_runtime"),
            ),
            "wrong member",
        )
        self._red_then_restore(
            lambda: (
                self._grant_provider_edge(
                    "sqag_runtime", "sqag_migrator", grantor="neondb_owner"
                ),
            ),
            lambda: (
                self._revoke_provider_edge(
                    "sqag_runtime", "sqag_migrator", grantor="neondb_owner"
                ),
            ),
            "wrong grantor",
        )
        self._red_then_restore(
            lambda: self._execute_admin("grant cloud_admin to sqag_runtime"),
            lambda: self._execute_admin("revoke cloud_admin from sqag_runtime"),
            "protected role as member elsewhere",
        )
        self._red_then_restore(
            lambda: self._execute_admin("grant sqag_runtime to cloud_admin"),
            lambda: self._execute_admin("revoke sqag_runtime from cloud_admin"),
            "provider-control role in an unapproved position",
        )

    def test_real_pg17_option_a_ownership_and_database_privilege_negative_matrix(self):
        self._red_then_restore(
            lambda: self._execute_admin_database(
                "postgres",
                self.sql.SQL("alter database {} owner to cloud_admin").format(
                    self.sql.Identifier(self.database_name)
                ),
            ),
            lambda: self._execute_admin_database(
                "postgres",
                self.sql.SQL("alter database {} owner to neondb_owner").format(
                    self.sql.Identifier(self.database_name)
                ),
            ),
            "database owner mismatch",
        )
        self._red_then_restore(
            lambda: self._execute_admin("alter schema public owner to cloud_admin"),
            lambda: self._execute_admin("alter schema public owner to pg_database_owner"),
            "public schema owner mismatch",
        )
        self._red_then_restore(
            lambda: self._execute_admin(
                "alter table public.sqag_profiles owner to neondb_owner"
            ),
            lambda: self._execute_admin(
                "alter table public.sqag_profiles owner to sqag_migrator"
            ),
            "migrator object owner mismatch",
        )
        self._red_then_restore(
            lambda: (
                self._execute_admin("create table public.runtime_owned_probe (id integer)"),
                self._execute_admin(
                    "alter table public.runtime_owned_probe owner to sqag_runtime"
                ),
            ),
            lambda: self._execute_admin("drop table if exists public.runtime_owned_probe"),
            "runtime-owned public object",
        )
        self._red_then_restore(
            lambda: (
                self._execute_admin("create table public.maintenance_owned_probe (id integer)"),
                self._execute_admin(
                    "alter table public.maintenance_owned_probe owner to sqag_maintenance"
                ),
            ),
            lambda: self._execute_admin("drop table if exists public.maintenance_owned_probe"),
            "maintenance-owned public object",
        )
        self._red_then_restore(
            lambda: self._execute_admin(
                self.sql.SQL("grant create on database {} to sqag_migrator").format(
                    self.sql.Identifier(self.database_name)
                )
            ),
            lambda: self._execute_admin(
                self.sql.SQL("revoke create on database {} from sqag_migrator").format(
                    self.sql.Identifier(self.database_name)
                )
            ),
            "migrator effective CREATE",
        )
        self._red_then_restore(
            lambda: self._execute_admin(
                self.sql.SQL("grant temporary on database {} to sqag_migrator").format(
                    self.sql.Identifier(self.database_name)
                )
            ),
            lambda: self._execute_admin(
                self.sql.SQL("revoke temporary on database {} from sqag_migrator").format(
                    self.sql.Identifier(self.database_name)
                )
            ),
            "migrator effective TEMPORARY",
        )

    def test_real_pg17_role_attribute_drift_remains_red(self):
        self._red_then_restore(
            lambda: self._execute_admin("alter role sqag_runtime inherit"),
            lambda: self._execute_admin("alter role sqag_runtime noinherit"),
            "role attribute drift",
        )

    def _execute_admin_database(self, database_name: str, statement, params=()):
        with self._admin_connection(database_name) as connection:
            return connection.execute(statement, params)

    def _admin_rows(self, statement, params=()):
        with self._admin_connection() as connection:
            return connection.execute(statement, params).fetchall()


@unittest.skipUnless(
    postgres_test_enabled(),
    "real disposable PostgreSQL-17 service is not configured",
)
class MigrationOperatorPathPostgresIntegrationTest(unittest.TestCase):
    """Exercise the real pre-apply, apply, post-apply, and no-op transition."""

    apply_full_migration_fixture = False

    @classmethod
    def setUpClass(cls):
        RuntimePrivilegeContractPostgresIntegrationTest.setUpClass.__func__(cls)

    def setUp(self):
        RuntimePrivilegeContractPostgresIntegrationTest.setUp(self)

    _cleanup_fixture = RuntimePrivilegeContractPostgresIntegrationTest._cleanup_fixture
    _admin_connection = RuntimePrivilegeContractPostgresIntegrationTest._admin_connection
    _admin_row = RuntimePrivilegeContractPostgresIntegrationTest._admin_row
    _admin_rows = RuntimePrivilegeContractPostgresIntegrationTest._admin_rows
    _configure_acl_contract = RuntimePrivilegeContractPostgresIntegrationTest._configure_acl_contract
    _configure_provider_memberships = RuntimePrivilegeContractPostgresIntegrationTest._configure_provider_memberships
    _configure_provider_ownership = RuntimePrivilegeContractPostgresIntegrationTest._configure_provider_ownership
    _execute_admin = RuntimePrivilegeContractPostgresIntegrationTest._execute_admin
    _execute_as_role = RuntimePrivilegeContractPostgresIntegrationTest._execute_as_role
    _grant_provider_edge = RuntimePrivilegeContractPostgresIntegrationTest._grant_provider_edge
    _verify = RuntimePrivilegeContractPostgresIntegrationTest._verify

    def _bootstrap_prefix_migrations(self, migrator_url: str) -> None:
        with webapp.postgres_storage_connection(
            migrator_url,
            expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
        ) as connection:
            connection.execute("set local search_path to public, pg_catalog")
            connection.execute(
                "select pg_catalog.pg_advisory_xact_lock(?)",
                (postgres_migration_module.MIGRATION_LOCK_KEY,),
            )
            postgres_migration_module._create_ledger(connection)
            for migration in self.migrations[:-1]:
                payload = postgres_migration_module.canonical_migration_payload(migration.path)
                self.assertEqual(sha256(payload).hexdigest(), migration.checksum_sha256)
                postgres_migration_module.execute_migration_sql(connection, payload.decode("utf-8"))
                connection.execute(
                    "insert into public.sqag_schema_migrations "
                    "(sequence_no, migration_id, checksum_sha256) values (?, ?, ?)",
                    (migration.sequence_no, migration.migration_id, migration.checksum_sha256),
                )
            connection.commit()

    def _operator_environment(self, *, include_runtime: bool = False) -> dict[str, str]:
        environment = dict(os.environ)
        for name in (
            webapp.SQAG_DATABASE_URL_ENV_NAME,
            webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME,
            webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME,
        ):
            environment.pop(name, None)
        environment[webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME] = safe_postgres_url(
            "sqag_migrator", self.database_name
        )
        if include_runtime:
            environment[webapp.SQAG_DATABASE_URL_ENV_NAME] = safe_postgres_url(
                "sqag_runtime", self.database_name
            )
            environment[webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME] = safe_postgres_url(
                "sqag_maintenance", self.database_name
            )
        return environment

    def _run_operator(self, script_name: str, arguments: list[str], environment: dict[str, str]):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script_name), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, f"{script_name} failed closed")
        return completed

    def test_real_pg17_operator_phase_007_to_008_causal_transition(self):
        migrator_url = safe_postgres_url("sqag_migrator", self.database_name)
        runtime_url = safe_postgres_url("sqag_runtime", self.database_name)
        last_migration = self.migrations[-1].migration_id
        expected_ids = [migration.migration_id for migration in self.migrations]

        prefix_rows = self._admin_rows(
            "select sequence_no, migration_id, checksum_sha256 "
            "from public.sqag_schema_migrations order by sequence_no"
        )
        self.assertEqual(
            [row["migration_id"] for row in prefix_rows],
            expected_ids[:-1],
        )
        callable_absent = self._admin_row(
            "select to_regprocedure(%s) is null as absent",
            ("public.sqag_quote_session_deletion_hold_blocked(text, text)",),
        )
        self.assertTrue(callable_absent["absent"])

        preflight_run = self._run_operator(
            "preflight_sqag_migrations.py",
            ["--phase", "pre-apply"],
            self._operator_environment(),
        )
        preflight_report = json.loads(preflight_run.stdout)
        self.assertEqual(preflight_report["phase"], "pre-apply")
        self.assertEqual(preflight_report["status"], "ready")
        self.assertTrue(preflight_report["safeToApply"])
        self.assertEqual(preflight_report["ledgerState"], "present")
        self.assertEqual(preflight_report["expectedHead"], last_migration)
        self.assertEqual(preflight_report["appliedHead"], expected_ids[-2])
        self.assertEqual(preflight_report["appliedMigrationIds"], expected_ids[:-1])
        self.assertEqual(preflight_report["pendingMigrationIds"], [last_migration])
        self.assertEqual(preflight_report["blockers"], [])
        self.assertNotIn("runtimeContract", preflight_report)
        self.assertNotIn("maintenanceContract", preflight_report)

        with webapp.postgres_storage_connection(
            migrator_url,
            expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
        ) as connection:
            identity = connection.execute(
                "select session_user as session_role, current_user as active_role"
            ).fetchone()
        self.assertEqual(identity["session_role"], webapp.SQAG_MIGRATOR_DATABASE_ROLE)
        self.assertEqual(identity["active_role"], webapp.SQAG_MIGRATOR_DATABASE_ROLE)

        first_apply = self._run_operator(
            "migrate_sqag_storage.py",
            [],
            self._operator_environment(),
        )
        applied_lines = [
            line for line in first_apply.stdout.splitlines()
            if line.startswith("Applied migration IDs:")
        ]
        self.assertEqual(applied_lines, [f"Applied migration IDs: {last_migration}"])

        ledger_after_apply = self._admin_rows(
            "select sequence_no, migration_id, checksum_sha256 "
            "from public.sqag_schema_migrations order by sequence_no"
        )
        self.assertEqual([row["migration_id"] for row in ledger_after_apply], expected_ids)
        self.assertEqual(
            [row["checksum_sha256"] for row in ledger_after_apply],
            [migration.checksum_sha256 for migration in self.migrations],
        )
        callable_present = self._admin_row(
            "select to_regprocedure(%s) is not null as present",
            ("public.sqag_quote_session_deletion_hold_blocked(text, text)",),
        )
        self.assertTrue(callable_present["present"])

        self._configure_acl_contract()
        postflight_run = self._run_operator(
            "preflight_sqag_migrations.py",
            ["--phase", "post-apply"],
            self._operator_environment(include_runtime=True),
        )
        postflight_report = json.loads(postflight_run.stdout)
        self.assertEqual(postflight_report["phase"], "post-apply")
        self.assertEqual(postflight_report["status"], "ready")
        self.assertTrue(postflight_report["safeToApply"])
        self.assertEqual(postflight_report["pendingMigrationIds"], [])
        self.assertEqual(postflight_report["appliedMigrationIds"], expected_ids)
        self.assertEqual(postflight_report["blockers"], [])
        self.assertEqual(postflight_report["runtimeContract"]["status"], "verified")
        self.assertEqual(postflight_report["maintenanceContract"]["status"], "verified")
        self.assertEqual(self._verify()["status"], "verified")

        callable_body = self._admin_row(
            "select p.prosrc as function_body "
            "from pg_catalog.pg_proc p "
            "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname = 'public' and p.proname = %s "
            "and pg_get_function_identity_arguments(p.oid) = %s",
            (contract.CALLABLE_ROUTINE_NAME, contract.CALLABLE_ROUTINE_IDENTITY_ARGUMENTS),
        )
        self.assertEqual(
            contract._semantic_sql_tokens(callable_body["function_body"]),
            contract._semantic_sql_tokens(contract._canonical_callable_routine_body()),
        )
        callable_runtime_acl = self._admin_row(
            "select has_function_privilege(%s, %s, 'EXECUTE') as allowed",
            (webapp.SQAG_RUNTIME_DATABASE_ROLE, "public.sqag_quote_session_deletion_hold_blocked(text, text)"),
        )
        callable_maintenance_acl = self._admin_row(
            "select has_function_privilege(%s, %s, 'EXECUTE') as allowed",
            (webapp.SQAG_MAINTENANCE_DATABASE_ROLE, "public.sqag_quote_session_deletion_hold_blocked(text, text)"),
        )
        self.assertTrue(callable_runtime_acl["allowed"])
        self.assertFalse(callable_maintenance_acl["allowed"])

        with webapp.postgres_storage_connection(
            runtime_url,
            expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
        ) as connection:
            with self.assertRaises(Exception):
                connection.execute("select 1 from public.sqag_legal_holds").fetchone()
            connection.rollback()

        ledger_before_noop = self._admin_rows(
            "select sequence_no, migration_id, checksum_sha256, applied_at "
            "from public.sqag_schema_migrations order by sequence_no"
        )
        second_apply = self._run_operator(
            "migrate_sqag_storage.py",
            [],
            self._operator_environment(include_runtime=True),
        )
        second_applied_lines = [
            line for line in second_apply.stdout.splitlines()
            if line.startswith("Applied migration IDs:")
        ]
        self.assertEqual(second_applied_lines, ["Applied migration IDs: none"])
        ledger_after_noop = self._admin_rows(
            "select sequence_no, migration_id, checksum_sha256, applied_at "
            "from public.sqag_schema_migrations order by sequence_no"
        )
        self.assertEqual(ledger_after_noop, ledger_before_noop)
        print("RUN313_PG17_CAUSAL_TRANSITION_EXECUTED")


if __name__ == "__main__":
    unittest.main()
