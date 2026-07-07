import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_live_db_object_backup_restore.py"


def load_verifier():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("verify_live_db_object_backup_restore", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Live DB+object backup/restore verifier script is missing.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def complete_env() -> dict[str, str]:
    return {
        "SQAG_LIVE_DB_OBJECT_BACKUP_RESTORE_EVIDENCE": "1",
        "KQAG_DATABASE_URL": "ACTIVE_DB_TARGET_MARKER_A",
        "SQAG_OBJECT_STORAGE_PROVIDER": "ACTIVE_OBJECT_PROVIDER_MARKER_A",
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "ACTIVE_OBJECT_ENDPOINT_MARKER_A",
        "SQAG_OBJECT_STORAGE_BUCKET": "ACTIVE_OBJECT_BUCKET_MARKER_A",
        "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "ACTIVE_ACCESS_MARKER_A",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "ACTIVE_CREDENTIAL_MARKER_A",
        "SQAG_RESTORE_DATABASE_URL": "RESTORE_DB_TARGET_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_PROVIDER": "RESTORE_OBJECT_PROVIDER_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_ENDPOINT_URL": "RESTORE_OBJECT_ENDPOINT_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_BUCKET": "RESTORE_OBJECT_BUCKET_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_REGION": "ap-southeast-1",
        "SQAG_RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID": "RESTORE_ACCESS_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY": "RESTORE_CREDENTIAL_MARKER_B",
        "SQAG_BACKUP_RESTORE_DECISION_ID": "operator-approved-window",
        "SQAG_BACKUP_RESTORE_WINDOW_ID": "isolated-restore-window",
    }


class LiveDbObjectBackupRestoreVerifierTest(unittest.TestCase):
    def test_missing_env_reports_blocked_preflight_without_values(self):
        verifier = load_verifier()
        report = verifier.run_verification(env={})
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])
        self.assertIn("KQAG_DATABASE_URL", report["missing_env_names"])
        self.assertIn("SQAG_RESTORE_DATABASE_URL", report["missing_env_names"])
        self.assertIn("blocked_isolated_restore_target_missing", report["blockers"])
        self.assertIn("blocked_backup_restore_decision_missing", report["blockers"])
        self.assertFalse(report["checks"]["isolated_restore_target_available"])
        self.assertFalse(report["checks"]["backup_ownership_decision_present"])
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertNotIn("ACTIVE_DB_TARGET_MARKER_A", text)
        self.assertNotIn("ACTIVE_OBJECT_BUCKET_MARKER_A", text)

    def test_restore_targets_matching_active_targets_are_blocked(self):
        verifier = load_verifier()
        env = complete_env()
        env["SQAG_RESTORE_DATABASE_URL"] = env["KQAG_DATABASE_URL"]
        env["SQAG_RESTORE_OBJECT_STORAGE_ENDPOINT_URL"] = env["SQAG_OBJECT_STORAGE_ENDPOINT_URL"]
        env["SQAG_RESTORE_OBJECT_STORAGE_BUCKET"] = env["SQAG_OBJECT_STORAGE_BUCKET"]

        report = verifier.run_verification(env=env)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("blocked_isolated_restore_target_missing", report["blockers"])
        self.assertFalse(report["checks"]["isolated_restore_target_available"])
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])
        self.assertNotIn(env["KQAG_DATABASE_URL"], text)
        self.assertNotIn(env["SQAG_OBJECT_STORAGE_BUCKET"], text)
        self.assertNotIn(env["SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY"], text)

    def test_complete_preflight_still_does_not_claim_live_evidence(self):
        verifier = load_verifier()
        report = verifier.run_verification(env=complete_env())

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["missing_env_names"], [])
        self.assertTrue(report["checks"]["backup_ownership_decision_present"])
        self.assertTrue(report["checks"]["restore_window_decision_present"])
        self.assertTrue(report["checks"]["isolated_restore_target_available"])
        self.assertTrue(report["checks"]["destructive_restore_prevented"])
        self.assertIn("live_db_object_backup_restore_execution_not_implemented", report["blockers"])
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])
        self.assertFalse(report["production_ready"])

    def test_cli_output_is_metadata_only_and_nonzero(self):
        verifier = load_verifier()
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = verifier.main([])

        output = stdout.getvalue()
        report = json.loads(output)
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertNotIn("ACTIVE_DB_TARGET_MARKER_A", output)
        self.assertNotIn("ACTIVE_OBJECT_BUCKET_MARKER_A", output)
        self.assertNotIn("RESTORE_DB_TARGET_MARKER_B", output)


if __name__ == "__main__":
    unittest.main()
