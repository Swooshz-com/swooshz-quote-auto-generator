from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp import server as webapp  # noqa: E402


RUNTIME_FORENSIC_TABLES = {
    "sqag_generation_runs",
    "sqag_generation_evidence",
    "sqag_audit_events",
    "sqag_feedback",
    "sqag_feedback_status_history",
}
MAINTENANCE_ONLY_FORENSIC_TABLES = {
    "sqag_legal_holds",
    "sqag_retention_delete_authorizations",
    "sqag_deletion_receipts",
    "sqag_retention_scan_cursors",
}
HEALTH_CHECK_NAMES = (
    "quote_generator_script",
    "database_metadata_schema",
    "forensic_schema",
    "forensic_tracking_configuration",
    "forensic_local_fallback_disabled",
    "object_artifact_metadata_schema",
    "object_storage_bucket",
)


@contextlib.contextmanager
def disposable_privilege_fixture():
    from tests.test_runtime_privilege_contract import (
        RuntimePrivilegeContractPostgresIntegrationTest,
    )

    RuntimePrivilegeContractPostgresIntegrationTest.setUpClass()
    fixture = RuntimePrivilegeContractPostgresIntegrationTest("runTest")
    try:
        fixture.setUp()
    except BaseException:
        fixture._cleanup_fixture()
        raise
    try:
        yield fixture
    finally:
        fixture._cleanup_fixture()


def postgres_test_enabled() -> bool:
    from tests.test_runtime_privilege_contract import (
        postgres_test_enabled as enabled,
    )

    return enabled()


def safe_postgres_url(user: str, database_name: str) -> str:
    from tests.test_runtime_privilege_contract import (
        safe_postgres_url as build_url,
    )

    return build_url(user, database_name)


class RuntimeHealthProjectionStaticTest(unittest.TestCase):
    def test_runtime_forensic_projection_matches_contract_and_excludes_maintenance_tables(self):
        projection = getattr(webapp, "SQAG_RUNTIME_FORENSIC_REQUIRED_COLUMNS", None)
        self.assertIsInstance(projection, dict)
        self.assertEqual(set(projection), RUNTIME_FORENSIC_TABLES)
        self.assertTrue(MAINTENANCE_ONLY_FORENSIC_TABLES.isdisjoint(projection))

        contract = json.loads(
            (ROOT / "docs" / "runtime-privilege-contract.json").read_text(
                encoding="utf-8"
            )
        )
        runtime_forensic_contract_tables = set(contract["runtime_tables"]) & set(
            webapp.SQAG_FORENSIC_REQUIRED_COLUMNS
        )
        self.assertEqual(set(projection), runtime_forensic_contract_tables)
        for table, columns in projection.items():
            self.assertEqual(
                set(columns),
                set(webapp.SQAG_FORENSIC_REQUIRED_COLUMNS[table]),
            )

    def test_retention_readiness_keeps_the_complete_forensic_contract(self):
        self.assertEqual(
            set(webapp.SQAG_FORENSIC_REQUIRED_COLUMNS),
            RUNTIME_FORENSIC_TABLES | MAINTENANCE_ONLY_FORENSIC_TABLES,
        )


@unittest.skipUnless(
    postgres_test_enabled(),
    "real disposable PostgreSQL-17 service is not configured",
)
class RuntimeDependencyHealthPostgresIntegrationTest(unittest.TestCase):
    def health_env(self, fixture) -> dict[str, str]:
        return {
            "APP_MODE": "deploy",
            "SQAG_STORAGE_MODE": "database",
            "SQAG_ARTIFACT_STORAGE_MODE": "object",
            "SQAG_DATABASE_URL": safe_postgres_url(
                "sqag_runtime", fixture.database_name
            ),
            "SQAG_TRACKING_HMAC_KEY": "synthetic-health-key",
            "SQAG_TRACKING_HMAC_KEY_VERSION": "run240-v1",
        }

    def health_status(self, fixture) -> dict:
        backend = webapp.InMemoryObjectStorageBackend()
        with (
            mock.patch.dict(os.environ, self.health_env(fixture), clear=True),
            mock.patch.object(
                webapp,
                "configured_object_storage_backend",
                return_value=backend,
            ),
        ):
            return webapp.health_status(force_dependency_probe=True)

    def assert_health_matrix(self, status: dict, *, overall: str = "ok") -> dict[str, bool]:
        self.assertEqual(status["status"], overall)
        checks = {check["name"]: bool(check["ok"]) for check in status["checks"]}
        self.assertEqual(tuple(checks), HEALTH_CHECK_NAMES)
        return checks

    def assert_runtime_can_read_only_runtime_forensics(self, fixture) -> None:
        with fixture._role_connection("sqag_runtime") as connection:
            for table in sorted(RUNTIME_FORENSIC_TABLES):
                connection.execute(f"select 1 from public.{table} limit 0")
            for table in sorted(MAINTENANCE_ONLY_FORENSIC_TABLES):
                with self.assertRaises(Exception):
                    connection.execute(
                        f"select 1 from public.{table} limit 0"
                    ).fetchone()
                connection.rollback()

    def test_pg17_visibility_green_after_projection_fix(self):
        with disposable_privilege_fixture() as fixture:
            self.assert_runtime_can_read_only_runtime_forensics(fixture)
            status = self.health_status(fixture)

        checks = self.assert_health_matrix(status, overall="ok")
        self.assertTrue(checks["quote_generator_script"])
        self.assertTrue(checks["database_metadata_schema"])
        self.assertTrue(checks["forensic_schema"])
        self.assertTrue(checks["forensic_tracking_configuration"])
        self.assertTrue(checks["forensic_local_fallback_disabled"])
        self.assertTrue(checks["object_artifact_metadata_schema"])
        self.assertTrue(checks["object_storage_bucket"])

    def test_forensic_failure_does_not_cascade_into_object_artifact_readiness(self):
        with disposable_privilege_fixture() as fixture:
            original = webapp.DatabaseSqagStorage.ensure_object_artifact_ready
            calls = []

            def record_object_readiness(storage):
                calls.append(storage)
                return original(storage)

            with mock.patch.object(
                webapp.DatabaseSqagStorage,
                "ensure_object_artifact_ready",
                record_object_readiness,
            ):
                status = self.health_status(fixture)

        checks = self.assert_health_matrix(status, overall="ok")
        self.assertEqual(len(calls), 1)
        self.assertTrue(checks["object_artifact_metadata_schema"])

    def test_missing_application_metadata_fails_only_application_check(self):
        with disposable_privilege_fixture() as fixture:
            fixture._execute_admin("drop table public.sqag_profiles cascade")
            status = self.health_status(fixture)

        checks = self.assert_health_matrix(status, overall="blocked")
        self.assertFalse(checks["database_metadata_schema"])
        self.assertTrue(checks["forensic_schema"])
        self.assertTrue(checks["object_artifact_metadata_schema"])
        self.assertTrue(checks["object_storage_bucket"])

    def test_missing_runtime_forensic_relation_fails_only_forensic_check(self):
        with disposable_privilege_fixture() as fixture:
            fixture._execute_admin(
                "drop table public.sqag_feedback_status_history cascade"
            )
            status = self.health_status(fixture)

        checks = self.assert_health_matrix(status, overall="blocked")
        self.assertTrue(checks["database_metadata_schema"])
        self.assertFalse(checks["forensic_schema"])
        self.assertTrue(checks["object_artifact_metadata_schema"])
        self.assertTrue(checks["object_storage_bucket"])

    def test_missing_object_artifact_metadata_fails_only_object_artifact_check(self):
        with disposable_privilege_fixture() as fixture:
            fixture._execute_admin("drop table public.sqag_object_artifacts cascade")
            status = self.health_status(fixture)

        checks = self.assert_health_matrix(status, overall="blocked")
        self.assertTrue(checks["database_metadata_schema"])
        self.assertTrue(checks["forensic_schema"])
        self.assertFalse(checks["object_artifact_metadata_schema"])
        self.assertTrue(checks["object_storage_bucket"])

    def test_maintenance_readiness_still_requires_missing_maintenance_relation(self):
        with disposable_privilege_fixture() as fixture:
            maintenance = webapp.DatabaseSqagStorage(
                safe_postgres_url("sqag_maintenance", fixture.database_name),
                "workspace-retention-readiness",
                role="admin",
                expected_session_role=webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
            )
            maintenance.ensure_retention_ready()
            fixture._execute_admin("drop table public.sqag_legal_holds cascade")
            with self.assertRaises(webapp.SqagStorageAccessError):
                maintenance.ensure_retention_ready()

    def test_all_required_runtime_checks_are_green_on_least_privilege_fixture(self):
        with disposable_privilege_fixture() as fixture:
            status = self.health_status(fixture)

        checks = self.assert_health_matrix(status, overall="ok")
        self.assertTrue(all(checks.values()))

    def test_genuine_runtime_reconciliation_and_forensic_store_use_runtime_projection(self):
        with disposable_privilege_fixture() as fixture:
            environment = self.health_env(fixture)
            self.assert_runtime_can_read_only_runtime_forensics(fixture)
            with mock.patch.dict(os.environ, environment, clear=True):
                self.assertEqual(webapp.reconcile_forensic_runs_on_startup(), 0)
                auth_session = {
                    "user": {
                        "platform": {
                            "workspace": {"workspaceId": "workspace-runtime"},
                            "user": {"userId": "user-runtime"},
                        }
                    }
                }
                with webapp.forensic_store_for_auth_session(auth_session) as store:
                    self.assertEqual(store.workspace_id, "workspace-runtime")

    def test_genuine_deploy_startup_reaches_listener_after_real_health_and_reconciliation(self):
        with disposable_privilege_fixture() as fixture:
            environment = self.health_env(fixture)
            with tempfile.TemporaryDirectory() as temporary:
                output_root = Path(temporary) / "output"
                tmp_root = Path(temporary) / "tmp"
                server = mock.Mock()
                server.serve_forever.side_effect = KeyboardInterrupt
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.dict(os.environ, environment, clear=True))
                    stack.enter_context(
                        mock.patch.object(
                            webapp,
                            "configured_object_storage_backend",
                            return_value=webapp.InMemoryObjectStorageBackend(),
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(webapp, "deploy_requires_auth_guard", return_value=False)
                    )
                    stack.enter_context(
                        mock.patch.object(
                            webapp,
                            "deploy_requires_platform_workspace_guard",
                            return_value=False,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(
                            webapp,
                            "deploy_requires_trusted_proxy_guard",
                            return_value=False,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(webapp, "deploy_requires_storage_guard", return_value=False)
                    )
                    stack.enter_context(
                        mock.patch.object(webapp, "configured_output_root", return_value=output_root)
                    )
                    stack.enter_context(
                        mock.patch.object(webapp, "configured_tmp_root", return_value=tmp_root)
                    )
                    stack.enter_context(
                        mock.patch.object(
                            sys,
                            "argv",
                            ["server.py", "--host", "127.0.0.1", "--port", "0"],
                        )
                    )
                    server_factory = stack.enter_context(
                        mock.patch.object(webapp, "ThreadingHTTPServer", return_value=server)
                    )
                    result = webapp.main()

                self.assertEqual(result, 0)
                server_factory.assert_called_once()
                server.serve_forever.assert_called_once_with()
                server.server_close.assert_called_once_with()


class RuntimeDependencyHealthDeployGateTest(unittest.TestCase):
    def common_deploy_patches(self, health):
        return (
            mock.patch.dict(os.environ, {"APP_MODE": "deploy"}, clear=True),
            mock.patch.object(
                sys, "argv", ["server.py", "--host", "127.0.0.1", "--port", "0"]
            ),
            mock.patch.object(
                webapp, "deploy_requires_auth_guard", return_value=False
            ),
            mock.patch.object(
                webapp, "deploy_requires_platform_workspace_guard", return_value=False
            ),
            mock.patch.object(
                webapp, "deploy_requires_trusted_proxy_guard", return_value=False
            ),
            mock.patch.object(
                webapp, "deploy_requires_storage_guard", return_value=False
            ),
            mock.patch.object(webapp, "health_status", return_value=health),
        )

    def test_deploy_startup_still_refuses_blocked_dependency_health(self):
        blocked = {
            "status": "blocked",
            "generator_available": True,
            "checks": [{"name": "forensic_schema", "ok": False}],
        }
        patches = self.common_deploy_patches(blocked)
        with contextlib.ExitStack() as stack:
            for patcher in patches:
                stack.enter_context(patcher)
            server = stack.enter_context(
                mock.patch.object(
                    webapp.ThreadingHTTPServer,
                    "__init__",
                    side_effect=AssertionError("server should not start"),
                )
            )
            self.assertEqual(webapp.main(), 2)
            self.assertEqual(server.call_count, 0)

    def test_deploy_startup_proceeds_past_dependency_health_when_all_checks_are_ok(self):
        all_ok = {
            "status": "ok",
            "generator_available": True,
            "checks": [{"name": name, "ok": True} for name in HEALTH_CHECK_NAMES],
        }
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary) / "output"
            tmp_root = Path(temporary) / "tmp"
            patches = self.common_deploy_patches(all_ok)
            server = mock.Mock()
            server.serve_forever.side_effect = KeyboardInterrupt
            with contextlib.ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)
                health = stack.enter_context(
                    mock.patch.object(webapp, "health_status", return_value=all_ok)
                )
                stack.enter_context(
                    mock.patch.object(
                        webapp, "configured_output_root", return_value=output_root
                    )
                )
                stack.enter_context(
                    mock.patch.object(webapp, "configured_tmp_root", return_value=tmp_root)
                )
                stack.enter_context(
                    mock.patch.object(
                        webapp, "reconcile_forensic_runs_on_startup", return_value=0
                    )
                )
                server_factory = stack.enter_context(
                    mock.patch.object(webapp, "ThreadingHTTPServer", return_value=server)
                )
                self.assertEqual(webapp.main(), 0)

            health.assert_called_once_with(force_dependency_probe=True)
            server_factory.assert_called_once()
            server.serve_forever.assert_called_once_with()
            server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
