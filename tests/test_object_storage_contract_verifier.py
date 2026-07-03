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

import verify_object_storage_contract as verifier


def test_temp_root() -> Path:
    root = ROOT / "_tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


class ObjectStorageContractVerifierTest(unittest.TestCase):
    def test_synthetic_object_storage_report_is_metadata_only(self):
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
            "workspaces/workspace-object-contract",
        }
        work_dir = test_temp_root() / f"object-contract-{time.time_ns()}"

        report = verifier.run_verification(work_dir=work_dir)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["synthetic_only"])
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertEqual(report["contract"]["backend"], "synthetic-in-memory")
        self.assertEqual(
            report["artifact_classes"]["covered"],
            [
                "generated_quote_artifacts",
                "uploaded_references",
                "profile_layout_assets",
                "pricing_visual_assets",
            ],
        )
        for check_name in (
            "store_retrieve_delete",
            "checksum_verified",
            "workspace_metadata_enforced",
            "wrong_workspace_retrieval_blocked",
            "wrong_workspace_delete_blocked",
            "metadata_without_object_keys",
        ):
            self.assertTrue(report["checks"][check_name], check_name)
        self.assertNotIn(str(work_dir), text)
        for value in private_values:
            self.assertNotIn(value, text)

    def test_cli_output_is_json_metadata_only(self):
        work_dir = test_temp_root() / f"object-contract-cli-{time.time_ns()}"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = verifier.main(["--work-dir", str(work_dir)])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertNotIn(str(work_dir), output)
        self.assertNotIn("sqlite:///", output)
        self.assertNotIn("workspaces/workspace-object-contract", output)
        self.assertNotIn("synthetic-private-artifact-bytes", output)

    def test_cli_failure_output_remains_metadata_only(self):
        private_path = "C:/Users/Private/Koncept Runtime/object-storage"
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
