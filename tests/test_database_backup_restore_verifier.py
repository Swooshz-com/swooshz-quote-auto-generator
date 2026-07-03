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

import verify_database_backup_restore as verifier


def test_temp_root() -> Path:
    root = ROOT / "_tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


class DatabaseBackupRestoreVerifierTest(unittest.TestCase):
    def test_synthetic_backup_restore_and_rollback_report_metadata_only(self):
        private_root = "C:/Users/Private/Koncept Runtime"
        private_db_url = "sqlite:///C:/Users/Private/kqag-storage.sqlite3?token=secret"
        private_values = {
            private_root,
            private_db_url,
            "synthetic-private-artifact-bytes",
            "Synthetic Private Customer",
            "Generated quote private line item",
            "Private pricing catalog contents",
            "Private profile layout contents",
            "staff.member@example.test",
            "oauth-client-secret-value",
            "swooshz_private_session_cookie",
            "sk-proj-private-api-key",
        }
        work_dir = test_temp_root() / f"backup-restore-verifier-{time.time_ns()}"
        self.addCleanup(lambda: shutil.rmtree(work_dir, ignore_errors=True))

        report = verifier.run_verification(work_dir=work_dir)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["storage_modes"], ["sqlite-database", "sqlite-database-artifacts"])
        self.assertTrue(report["backup_restore"]["row_counts_match"])
        self.assertTrue(report["backup_restore"]["artifact_checksums_match"])
        self.assertTrue(report["backup_restore"]["workspace_ownership_preserved"])
        self.assertTrue(report["rollback"]["restored_prior_known_good_state"])
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertNotIn(str(work_dir), text)
        for value in private_values:
            self.assertNotIn(value, text)

    def test_cli_output_is_json_metadata_only(self):
        work_dir = test_temp_root() / f"backup-restore-cli-{time.time_ns()}"
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
        self.assertNotIn("content_blob", output)

    def test_verifier_can_rerun_in_same_synthetic_work_dir(self):
        work_dir = test_temp_root() / f"backup-restore-rerun-{time.time_ns()}"
        self.addCleanup(lambda: shutil.rmtree(work_dir, ignore_errors=True))

        first = verifier.run_verification(work_dir=work_dir)
        second = verifier.run_verification(work_dir=work_dir)

        self.assertEqual(first["status"], "passed")
        self.assertEqual(second["status"], "passed")

    def test_cli_accepts_relative_synthetic_work_dir(self):
        work_dir = Path("_tmp") / "tests" / f"backup-restore-relative-{time.time_ns()}"
        self.addCleanup(lambda: shutil.rmtree(ROOT / work_dir, ignore_errors=True))

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = verifier.main(["--work-dir", str(work_dir)])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "passed")
        self.assertNotIn(str(work_dir), output)

    def test_cli_failure_output_remains_metadata_only(self):
        private_path = "C:/Users/Private/Koncept Runtime/kqag-storage.sqlite3"
        stdout = io.StringIO()
        with mock.patch.object(verifier, "run_verification", side_effect=RuntimeError(private_path)):
            with contextlib.redirect_stdout(stdout):
                exit_code = verifier.main([])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertNotIn(private_path, output)

    def test_retention_policy_is_machine_readable_and_non_destructive(self):
        policy = verifier.load_retention_policy()
        covered = {item["data_class"] for item in policy["data_classes"]}

        self.assertEqual(policy["schema"], "swooshz.kqag.internal-alpha-retention-policy.v1")
        self.assertTrue(policy["non_destructive_verifier_only"])
        self.assertGreaterEqual(
            covered,
            {
                "quote_sessions",
                "generated_artifacts",
                "uploaded_references",
                "profile_layout_assets",
                "pricing_visual_assets",
                "logs",
                "backups",
            },
        )
        for item in policy["data_classes"]:
            self.assertIn("retention_days", item)
            self.assertIn("rollback_note", item)
            self.assertNotIn("delete_real_data", json.dumps(item, sort_keys=True).lower())


if __name__ == "__main__":
    unittest.main()
