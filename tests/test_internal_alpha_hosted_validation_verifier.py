import contextlib
import io
import json
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import verify_internal_alpha_hosted_validation as verifier


def test_temp_root() -> Path:
    root = ROOT / "_tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


class InternalAlphaHostedValidationVerifierTest(unittest.TestCase):
    def test_report_is_metadata_only_and_keeps_launch_and_production_blocked(self):
        private_values = {
            "sqlite:///",
            "C:/Users/Private",
            "/var/lib/sqag",
            "/var/log/sqag",
            "Synthetic Private Customer",
            "Generated quote private line item",
            "Private pricing catalog contents",
            "Private profile layout contents",
            "staff.member@example.test",
            "oauth-client-secret-value",
            "swooshz_private_session_cookie",
            "sk-proj-private-api-key",
            "synthetic-private-artifact-bytes",
            "raw provider response text",
            "private-code",
            "private-state",
        }
        work_dir = test_temp_root() / f"internal-alpha-hosted-validation-{time.time_ns()}"
        self.addCleanup(lambda: shutil.rmtree(work_dir, ignore_errors=True))

        report = verifier.run_verification(work_dir=work_dir)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["synthetic_only"])
        self.assertFalse(report["live_deployment_evidence"])
        self.assertEqual(report["target_posture"]["storage_mode"], "database")
        self.assertEqual(report["target_posture"]["artifact_storage_mode"], "database")
        self.assertEqual(report["target_posture"]["database_url_source"], "host_secret_manager_only")
        self.assertFalse(report["target_posture"]["launch_ready"])
        self.assertFalse(report["readiness"]["internal_alpha_ready"])
        self.assertFalse(report["readiness"]["production_ready"])
        self.assertIn("database_blob_artifact_storage_not_launch_ready", report["readiness"]["blocker_ids"])
        self.assertIn("object_storage_missing", report["readiness"]["production_blocker_ids"])
        self.assertEqual(report["evidence"]["backup_restore"], "passed")
        self.assertEqual(report["evidence"]["hosted_observability"], "passed")
        self.assertEqual(report["evidence"]["hosted_smoke"], "passed")
        self.assertIn("SQAG_DATABASE_URL", report["host_secret_manager_only_env_names"])
        self.assertIn("SQAG_STORAGE_MODE", report["required_env_names"])
        self.assertIn('SQAG_TRUSTED_PROXY_CIDRS', report['required_env_names'])
        self.assertEqual(report["health"]["path"], "/api/health")
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertNotIn(str(work_dir), text)
        for value in private_values:
            self.assertNotIn(value, text)

    def test_cli_output_is_json_metadata_only(self):
        work_dir = test_temp_root() / f"internal-alpha-hosted-validation-cli-{time.time_ns()}"
        self.addCleanup(lambda: shutil.rmtree(work_dir, ignore_errors=True))

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = verifier.main(["--work-dir", str(work_dir)])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertNotIn(str(work_dir), output)
        self.assertNotIn("sqlite:///", output)
        self.assertNotIn("/var/lib/sqag", output)
        self.assertNotIn("Set-Cookie", output)

    def test_cli_failure_output_remains_metadata_only(self):
        private_path = "C:/Users/Private/Koncept Runtime/sqag-storage.sqlite3"
        stdout = io.StringIO()
        with mock.patch.object(verifier, "run_verification", side_effect=RuntimeError(private_path)):
            with contextlib.redirect_stdout(stdout):
                exit_code = verifier.main([])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertNotIn(private_path, output)
        self.assertNotIn("sqlite:///", output)


if __name__ == "__main__":
    unittest.main()
