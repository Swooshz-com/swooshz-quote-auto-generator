import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from webapp import server as webapp

ROOT = Path(__file__).resolve().parents[1]


def test_temp_root() -> Path:
    root = ROOT / "_tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


class ProductionReadinessStatusTest(unittest.TestCase):
    def readiness_json(self, env):
        with mock.patch.dict(webapp.os.environ, env, clear=True):
            status = webapp.production_readiness_status(security_scan_status="standard_scan_started")
        return json.dumps(status, sort_keys=True)

    def readiness_status(self, env, **kwargs):
        with mock.patch.dict(webapp.os.environ, env, clear=True):
            return webapp.production_readiness_status(security_scan_status="standard_scan_started", **kwargs)

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
        self.assertIn("hosted_smoke_evidence_missing", blocker_ids)
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

    def test_readiness_command_can_include_synthetic_backup_restore_evidence(self):
        work_dir = test_temp_root() / f"readiness-backup-evidence-{time.time_ns()}"
        command_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(ROOT),
            "KQAG_STORAGE_MODE": "database",
            "KQAG_ARTIFACT_STORAGE_MODE": "database",
            "KQAG_DATABASE_URL": "sqlite:///tmp/kqag-storage.sqlite3",
        }
        for name in ("COMSPEC", "SystemRoot", "TEMP", "TMP"):
            if os.environ.get(name):
                command_env[name] = os.environ[name]

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "check_production_readiness.py"),
                "--with-backup-restore-evidence",
                "--backup-restore-work-dir",
                str(work_dir),
            ],
            cwd=ROOT,
            env=command_env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        status = json.loads(completed.stdout)
        blocker_ids = {item["id"] for item in status["blockers"]}
        self.assertEqual(status["backup_restore_evidence"]["status"], "passed")
        self.assertNotIn("backup_restore_unverified", blocker_ids)
        self.assertIn("object_storage_missing", blocker_ids)
        self.assertIn("hosted_logging_monitoring_missing", blocker_ids)
        self.assertIn("hosted_smoke_evidence_missing", blocker_ids)
        self.assertFalse(status["internal_alpha_ready"])
        self.assertFalse(status["production_ready"])
        self.assertNotIn(str(work_dir), completed.stdout + completed.stderr)

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

    def test_database_backup_restore_evidence_can_be_reported_without_readiness_overclaim(self):
        database_url = "sqlite:///tmp/kqag-storage.sqlite3"
        status = self.readiness_status(
            {
                "KQAG_STORAGE_MODE": "database",
                "KQAG_ARTIFACT_STORAGE_MODE": "database",
                "KQAG_DATABASE_URL": database_url,
            },
            backup_restore_evidence_status="passed",
            hosted_observability_evidence_status="passed",
        )

        blocker_ids = {item["id"] for item in status["blockers"]}
        internal_alpha_blocker_ids = {item["id"] for item in status["internal_alpha_blockers"]}
        production_blocker_ids = {item["id"] for item in status["production_blockers"]}
        self.assertEqual(status["backup_restore_evidence"]["status"], "passed")
        self.assertTrue(status["backup_restore_evidence"]["database_artifact_temporary_exception_supported"])
        self.assertNotIn("backup_restore_unverified", blocker_ids)
        self.assertIn("object_storage_missing", blocker_ids)
        self.assertNotIn("object_storage_missing", internal_alpha_blocker_ids)
        self.assertIn("object_storage_missing", production_blocker_ids)
        self.assertNotIn("sqlite_not_final_production", internal_alpha_blocker_ids)
        self.assertIn("sqlite_not_final_production", production_blocker_ids)
        self.assertEqual(status["hosted_observability_evidence"]["status"], "passed")
        self.assertTrue(status["hosted_observability_evidence"]["internal_alpha_observability_supported"])
        self.assertNotIn("hosted_logging_monitoring_missing", blocker_ids)
        self.assertIn("hosted_smoke_evidence_missing", blocker_ids)
        self.assertNotIn("hosted_logging_monitoring_missing", internal_alpha_blocker_ids)
        self.assertIn("hosted_smoke_evidence_missing", internal_alpha_blocker_ids)
        self.assertFalse(status["internal_alpha_ready"])
        self.assertFalse(status["production_ready"])

    def test_hosted_observability_evidence_is_not_assumed_when_not_run(self):
        status = self.readiness_status({})

        blocker_ids = {item["id"] for item in status["blockers"]}
        self.assertEqual(status["hosted_observability_evidence"]["status"], "not_run_by_checker")
        self.assertFalse(status["hosted_observability_evidence"]["internal_alpha_observability_supported"])
        self.assertIn("hosted_logging_monitoring_missing", blocker_ids)

    def test_hosted_observability_evidence_drops_only_logging_blocker(self):
        database_url = "sqlite:///tmp/kqag-storage.sqlite3"
        status = self.readiness_status(
            {
                "KQAG_STORAGE_MODE": "database",
                "KQAG_ARTIFACT_STORAGE_MODE": "database",
                "KQAG_DATABASE_URL": database_url,
            },
            backup_restore_evidence_status="passed",
            hosted_observability_evidence_status="passed",
        )

        blocker_ids = {item["id"] for item in status["blockers"]}
        internal_alpha_blocker_ids = {item["id"] for item in status["internal_alpha_blockers"]}
        production_blocker_ids = {item["id"] for item in status["production_blockers"]}
        self.assertNotIn("hosted_logging_monitoring_missing", blocker_ids)
        self.assertIn("hosted_smoke_evidence_missing", blocker_ids)
        self.assertIn("hosted_smoke_evidence_missing", internal_alpha_blocker_ids)
        self.assertIn("object_storage_missing", production_blocker_ids)
        self.assertFalse(status["internal_alpha_ready"])
        self.assertFalse(status["production_ready"])

    def test_readiness_command_can_include_synthetic_hosted_observability_evidence(self):
        work_dir = test_temp_root() / f"readiness-observability-evidence-{time.time_ns()}"
        command_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONPATH": str(ROOT),
            "KQAG_STORAGE_MODE": "database",
            "KQAG_ARTIFACT_STORAGE_MODE": "database",
            "KQAG_DATABASE_URL": "sqlite:///tmp/kqag-storage.sqlite3",
        }
        for name in ("COMSPEC", "SystemRoot", "TEMP", "TMP"):
            if os.environ.get(name):
                command_env[name] = os.environ[name]

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "check_production_readiness.py"),
                "--with-backup-restore-evidence",
                "--with-hosted-observability-evidence",
                "--hosted-observability-work-dir",
                str(work_dir),
            ],
            cwd=ROOT,
            env=command_env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        status = json.loads(completed.stdout)
        blocker_ids = {item["id"] for item in status["blockers"]}
        self.assertEqual(status["backup_restore_evidence"]["status"], "passed")
        self.assertEqual(status["hosted_observability_evidence"]["status"], "passed")
        self.assertNotIn("backup_restore_unverified", blocker_ids)
        self.assertNotIn("hosted_logging_monitoring_missing", blocker_ids)
        self.assertIn("hosted_smoke_evidence_missing", blocker_ids)
        self.assertIn("object_storage_missing", blocker_ids)
        self.assertFalse(status["internal_alpha_ready"])
        self.assertFalse(status["production_ready"])
        self.assertNotIn(str(work_dir), completed.stdout + completed.stderr)

    def test_database_backup_restore_evidence_is_not_assumed_when_not_run(self):
        database_url = "sqlite:///tmp/kqag-storage.sqlite3"
        status = self.readiness_status(
            {
                "KQAG_STORAGE_MODE": "database",
                "KQAG_ARTIFACT_STORAGE_MODE": "database",
                "KQAG_DATABASE_URL": database_url,
            }
        )

        blocker_ids = {item["id"] for item in status["blockers"]}
        self.assertEqual(status["backup_restore_evidence"]["status"], "not_run_by_checker")
        self.assertFalse(status["backup_restore_evidence"]["database_artifact_temporary_exception_supported"])
        self.assertIn("backup_restore_unverified", blocker_ids)


if __name__ == "__main__":
    unittest.main()
