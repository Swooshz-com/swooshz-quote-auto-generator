import contextlib
import io
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import verify_hosted_observability as verifier


def test_temp_root() -> Path:
    root = ROOT / "_tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


class HostedObservabilityVerifierTest(unittest.TestCase):
    def test_synthetic_observability_report_is_metadata_only(self):
        private_values = {
            "C:/Users/Private/Koncept Runtime",
            "sqlite:///C:/Users/Private/kqag-storage.sqlite3?token=secret",
            "Acme Private Customer",
            "Generated quote private line item",
            "Private pricing catalog contents",
            "Private profile layout contents",
            "staff.member@example.test",
            "oauth-client-secret-value",
            "swooshz_private_session_cookie",
            "sk-proj-private-api-key",
            "synthetic-private-artifact-bytes",
            "raw provider response text",
        }
        work_dir = test_temp_root() / f"hosted-observability-{time.time_ns()}"

        report = verifier.run_verification(work_dir=work_dir)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["synthetic_only"])
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertTrue(report["structured_logs"]["sensitive_values_omitted"])
        self.assertTrue(report["structured_logs"]["allowed_events_enforced"])
        self.assertTrue(report["support_traceability"]["error_reference_present"])
        self.assertTrue(report["health_readiness"]["safe_metadata_only"])
        self.assertNotIn(str(work_dir), text)
        for value in private_values:
            self.assertNotIn(value, text)

    def test_cli_output_is_json_metadata_only(self):
        work_dir = test_temp_root() / f"hosted-observability-cli-{time.time_ns()}"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = verifier.main(["--work-dir", str(work_dir)])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertNotIn(str(work_dir), output)
        self.assertNotIn("sqlite:///", output)
        self.assertNotIn("staff.member@example.test", output)
        self.assertNotIn("sk-proj-private-api-key", output)

    def test_cli_failure_output_remains_metadata_only(self):
        private_path = "C:/Users/Private/Koncept Runtime/observability.jsonl"
        stdout = io.StringIO()
        with mock.patch.object(verifier, "run_verification", side_effect=RuntimeError(private_path)):
            with contextlib.redirect_stdout(stdout):
                exit_code = verifier.main([])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertNotIn(private_path, output)

    def test_policy_is_machine_readable_and_secret_safe(self):
        policy = verifier.load_policy()
        text = json.dumps(policy, sort_keys=True)

        self.assertEqual(policy["schema"], "swooshz.kqag.hosted-observability-policy.v1")
        self.assertTrue(policy["synthetic_verifier_only"])
        self.assertIn("allowed_event_categories", policy)
        self.assertIn("forbidden_content", policy)
        self.assertIn("minimum_metadata", policy)
        self.assertIn("support_traceability", policy)
        self.assertIn("health_readiness", policy)
        self.assertIn("retention", policy)
        for value in (
            "sqlite:///",
            "C:/Users/",
            "staff.member@example.test",
            "sk-proj",
            "oauth-client-secret-value",
            "swooshz_private_session_cookie",
        ):
            self.assertNotIn(value, text)


if __name__ == "__main__":
    unittest.main()
