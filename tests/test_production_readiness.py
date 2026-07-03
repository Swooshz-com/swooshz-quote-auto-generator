import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from webapp import server as webapp

ROOT = Path(__file__).resolve().parents[1]


class ProductionReadinessStatusTest(unittest.TestCase):
    def readiness_json(self, env):
        with mock.patch.dict(webapp.os.environ, env, clear=True):
            status = webapp.production_readiness_status(security_scan_status="standard_scan_started")
        return json.dumps(status, sort_keys=True)

    def test_readiness_status_redacts_private_values_and_lists_storage_surfaces(self):
        private_root = "C:/Users/Private/Koncept Runtime"
        database_url = "sqlite:///C:/Users/Private/kqag-storage.sqlite3?token=secret"
        text = self.readiness_json(
            {
                "QUOTE_DATA_ROOT": private_root,
                "QUOTE_OUTPUT_ROOT": private_root + "/output",
                "QUOTE_TMP_ROOT": private_root + "/tmp",
                "QUOTE_LOG_ROOT": private_root + "/logs",
                "KQAG_DATABASE_URL": database_url,
                "OIDC_CLIENT_SECRET": "super-secret",
            }
        )

        self.assertNotIn(private_root, text)
        self.assertNotIn(database_url, text)
        self.assertNotIn("super-secret", text)
        for surface in (
            "profiles_storage",
            "pricing_references_storage",
            "quote_sessions_storage",
            "generated_artifacts_storage",
        ):
            self.assertIn(surface, text)

    def test_local_storage_mode_is_not_production_ready_but_keeps_local_uat_supported(self):
        with mock.patch.dict(webapp.os.environ, {}, clear=True):
            status = webapp.production_readiness_status()

        self.assertEqual(status["kqag_storage_mode"], "local")
        self.assertEqual(status["kqag_artifact_storage_mode"], "local")
        self.assertTrue(status["local_uat_supported"])
        self.assertFalse(status["internal_alpha_ready"])
        self.assertFalse(status["production_ready"])
        self.assertTrue(status["internal_alpha_future_exception"]["possible"])
        blocker_ids = {item["id"] for item in status["blockers"]}
        self.assertIn("local_runtime_storage", blocker_ids)
        self.assertIn("hosted_logging_monitoring_missing", blocker_ids)
        self.assertNotIn("pricing_reference_local_pack_isolation", blocker_ids)
        self.assertNotIn("legacy_job_artifact_download_authorization", blocker_ids)

    def test_readiness_command_redacts_sensitive_runtime_values(self):
        private_values = {
            "C:/Users/Private/Koncept Runtime",
            "sqlite:///C:/Users/Private/kqag-storage.sqlite3?token=secret",
            "oauth-client-secret-value",
            "https://auth.example.test/callback?code=private-code&state=private-state",
            "swooshz_private_session_cookie",
            "staff.member@example.test",
            "Acme Private Customer",
            "Generated quote private line item",
            "Private pricing catalog contents",
            "Private profile layout contents",
        }
        command_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(ROOT),
            "KQAG_STORAGE_MODE": "database",
            "KQAG_ARTIFACT_STORAGE_MODE": "database",
            "QUOTE_DATA_ROOT": "C:/Users/Private/Koncept Runtime",
            "QUOTE_OUTPUT_ROOT": "C:/Users/Private/Koncept Runtime/output",
            "QUOTE_TMP_ROOT": "C:/Users/Private/Koncept Runtime/tmp",
            "QUOTE_LOG_ROOT": "C:/Users/Private/Koncept Runtime/logs",
            "KQAG_DATABASE_URL": "sqlite:///C:/Users/Private/kqag-storage.sqlite3?token=secret",
            "OIDC_CLIENT_SECRET": "oauth-client-secret-value",
            "OIDC_REDIRECT_URI": "https://auth.example.test/callback?code=private-code&state=private-state",
            "SESSION_SECRET": "swooshz_private_session_cookie",
            "AUTH_ALLOWED_EMAILS": "staff.member@example.test",
            "PRIVATE_CUSTOMER_FIXTURE": "Acme Private Customer",
            "PRIVATE_QUOTE_FIXTURE": "Generated quote private line item",
            "PRIVATE_PRICING_FIXTURE": "Private pricing catalog contents",
            "PRIVATE_PROFILE_FIXTURE": "Private profile layout contents",
        }
        for name in ("COMSPEC", "SystemRoot", "TEMP", "TMP"):
            if os.environ.get(name):
                command_env[name] = os.environ[name]

        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_production_readiness.py")],
            cwd=ROOT,
            env=command_env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        status = json.loads(completed.stdout)
        self.assertFalse(status["production_ready"])
        text = completed.stdout + completed.stderr
        for value in private_values:
            self.assertNotIn(value, text)

    def test_database_and_artifact_modes_are_evaluated_separately(self):
        database_url = "sqlite:///tmp/kqag-storage.sqlite3"
        with mock.patch.dict(
            webapp.os.environ,
            {"KQAG_STORAGE_MODE": "database", "KQAG_ARTIFACT_STORAGE_MODE": "local", "KQAG_DATABASE_URL": database_url},
            clear=True,
        ):
            mixed_status = webapp.production_readiness_status()

        self.assertEqual(mixed_status["profiles_storage"]["mode"], "database")
        self.assertEqual(mixed_status["pricing_references_storage"]["mode"], "database")
        self.assertEqual(mixed_status["quote_sessions_storage"]["mode"], "database")
        self.assertEqual(mixed_status["generated_artifacts_storage"]["mode"], "local")
        self.assertFalse(mixed_status["workspace_scoped"])
        self.assertIn("local_artifact_storage", {item["id"] for item in mixed_status["blockers"]})

        with mock.patch.dict(
            webapp.os.environ,
            {"KQAG_STORAGE_MODE": "database", "KQAG_ARTIFACT_STORAGE_MODE": "database", "KQAG_DATABASE_URL": database_url},
            clear=True,
        ):
            database_status = webapp.production_readiness_status()

        self.assertEqual(database_status["generated_artifacts_storage"]["mode"], "database")
        self.assertTrue(database_status["workspace_scoped"])
        self.assertIn("object_storage_missing", {item["id"] for item in database_status["blockers"]})
        self.assertIn("hosted_logging_monitoring_missing", {item["id"] for item in database_status["blockers"]})
        self.assertNotIn("profile_runtime_layout_dependency", {item["id"] for item in database_status["blockers"]})
        self.assertNotIn("pricing_reference_local_pack_isolation", {item["id"] for item in database_status["blockers"]})
        self.assertNotIn("legacy_job_artifact_download_authorization", {item["id"] for item in database_status["blockers"]})


if __name__ == "__main__":
    unittest.main()
