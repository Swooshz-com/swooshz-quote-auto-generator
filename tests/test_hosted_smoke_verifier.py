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

import verify_hosted_smoke as verifier


def test_temp_root() -> Path:
    root = ROOT / "_tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


class HostedSmokeVerifierTest(unittest.TestCase):
    def test_synthetic_hosted_smoke_report_is_metadata_only(self):
        private_values = {
            "sqlite:///",
            "C:/Users/Private",
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
            "synthetic-launch-token-reference",
        }
        work_dir = test_temp_root() / f"hosted-smoke-{time.time_ns()}"
        self.addCleanup(lambda: shutil.rmtree(work_dir, ignore_errors=True))

        report = verifier.run_verification(work_dir=work_dir)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["synthetic_only"])
        self.assertEqual(report["network"]["host"], "127.0.0.1")
        self.assertNotIn("localhost", text.lower())
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertTrue(report["storage"]["database_mode"])
        self.assertTrue(report["storage"]["database_artifact_mode"])
        self.assertFalse(report["storage"]["local_quote_session_success_path_used"])
        self.assertFalse(report["storage"]["local_artifact_success_path_used"])
        self.assertEqual(report["authorized_artifact_downloads"]["kinds"], ["pdf", "xlsx"])
        for check_name in (
            "health_metadata",
            "unauthenticated_routes_blocked",
            "synthetic_platform_launch",
            "workspace_profile_saved_and_used",
            "workspace_pricing_saved_and_used",
            "quote_generation",
            "quote_session_persisted",
            "authorized_artifact_download",
            "quote_session_delete",
            "logout",
            "legacy_job_file_lockdown",
        ):
            self.assertTrue(report["checks"][check_name], check_name)
        self.assertNotIn(str(work_dir), text)
        for value in private_values:
            self.assertNotIn(value, text)

    def test_cli_output_is_json_metadata_only(self):
        work_dir = test_temp_root() / f"hosted-smoke-cli-{time.time_ns()}"
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
        self.assertNotIn("localhost", output.lower())
        self.assertNotIn("synthetic-launch-token-reference", output)
        self.assertNotIn("Set-Cookie", output)

    def test_cli_failure_output_remains_metadata_only(self):
        private_path = "C:/Users/Private/Koncept Runtime/hosted-smoke.sqlite3"
        stdout = io.StringIO()
        with mock.patch.object(verifier, "run_verification", side_effect=RuntimeError(private_path)):
            with contextlib.redirect_stdout(stdout):
                exit_code = verifier.main([])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertNotIn(private_path, output)


if __name__ == "__main__":
    unittest.main()
