import contextlib
import importlib.util
import io
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_object_artifact_lifecycle.py"


def test_temp_root() -> Path:
    root = ROOT / "_tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


def load_verifier():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("verify_object_artifact_lifecycle", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Object artifact lifecycle verifier script is missing.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ObjectArtifactLifecycleVerifierTest(unittest.TestCase):
    def test_synthetic_db_object_lifecycle_report_is_metadata_only(self):
        self.assertTrue(SCRIPT_PATH.is_file(), "Object artifact lifecycle verifier script is missing.")
        verifier = load_verifier()
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
            "workspaces/workspace-object-lifecycle",
        }
        work_dir = test_temp_root() / f"object-lifecycle-{time.time_ns()}"

        report = verifier.run_verification(work_dir=work_dir)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["synthetic_only"])
        self.assertEqual(report["storage_modes"], ["sqlite-database", "stubbed-object-artifacts"])
        for check_name in (
            "db_metadata_backup_restore_preserved",
            "restored_metadata_retrieves_object",
            "missing_object_detected",
            "checksum_mismatch_detected",
            "tombstoned_artifact_inaccessible_after_restore",
            "wrong_workspace_restore_access_denied",
            "local_staging_files_cleaned",
        ):
            self.assertTrue(report["checks"][check_name], check_name)
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertFalse(report["privacy"]["object_keys_printed"])
        self.assertFalse(report["privacy"]["artifact_bytes_printed"])
        self.assertNotIn(str(work_dir), text)
        for value in private_values:
            self.assertNotIn(value, text)

    def test_cli_output_is_json_metadata_only(self):
        self.assertTrue(SCRIPT_PATH.is_file(), "Object artifact lifecycle verifier script is missing.")
        verifier = load_verifier()
        work_dir = test_temp_root() / f"object-lifecycle-cli-{time.time_ns()}"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = verifier.main(["--work-dir", str(work_dir)])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertNotIn(str(work_dir), output)
        self.assertNotIn("sqlite:///", output)
        self.assertNotIn("workspaces/workspace-object-lifecycle", output)
        self.assertNotIn("synthetic-private-artifact-bytes", output)

    def test_cli_failure_output_remains_metadata_only(self):
        self.assertTrue(SCRIPT_PATH.is_file(), "Object artifact lifecycle verifier script is missing.")
        verifier = load_verifier()
        private_path = "C:/Users/Private/Koncept Runtime/object-lifecycle"
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
