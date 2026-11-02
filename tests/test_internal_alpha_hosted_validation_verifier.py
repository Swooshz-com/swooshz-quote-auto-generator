import contextlib
import io
import json
import shutil
import sys
import tempfile
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
        work_dir = Path(tempfile.mkdtemp(prefix="iav-", dir=test_temp_root()))
        self.addCleanup(lambda: shutil.rmtree(work_dir, ignore_errors=True))

        report = verifier.run_verification(work_dir=work_dir)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["synthetic_only"])
        self.assertFalse(report["live_deployment_evidence"])
        self.assertEqual(report["hosted_smoke_scope"]["evidence_scope"], "synthetic_local_hosted_contract")
        self.assertFalse(report["hosted_smoke_scope"]["public_hosted_url_tested"])
        self.assertFalse(report["hosted_smoke_scope"]["live_neon_r2_evidence"])
        self.assertFalse(report["hosted_smoke_scope"]["platform_handoff_evidence"])
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
        self.assertIn("SQAG_TRACKING_HMAC_KEY", report["host_secret_manager_only_env_names"])
        self.assertIn("SQAG_TRACKING_HMAC_KEY_VERSION", report["required_env_names"])
        self.assertNotIn("SQAG_TRACKING_HMAC_KEY_VERSION", report["host_secret_manager_only_env_names"])
        self.assertIn("SQAG_STORAGE_MODE", report["required_env_names"])
        self.assertIn('SQAG_TRUSTED_PROXY_CIDRS', report['required_env_names'])
        self.assertEqual(report["health"]["path"], "/api/health")
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertNotIn(str(work_dir), text)
        for value in private_values:
            self.assertNotIn(value, text)

    def test_cli_output_is_json_metadata_only(self):
        work_dir = Path(tempfile.mkdtemp(prefix="iav-", dir=test_temp_root()))
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

    def projected_fixture(self):
        with contextlib.ExitStack() as stack:
            for dependency in (verifier.verify_database_backup_restore, verifier.verify_hosted_observability, verifier.verify_hosted_smoke):
                stack.enter_context(mock.patch.object(dependency, "run_verification", return_value={"status": "passed"}))
            stack.enter_context(mock.patch.object(verifier.webapp, "production_readiness_status", return_value={
                "internal_alpha_ready": False, "production_ready": False,
                "blockers": [{"id": "database_blob_artifact_storage_not_launch_ready"}],
                "production_blockers": [{"id": "object_storage_missing"}],
            }))
            return verifier.run_verification(work_dir=ROOT / "_logs" / "server" / "synthetic-projection")

    def test_closed_projection_retains_only_typed_public_metadata(self):
        report = self.projected_fixture()
        self.assertEqual(verifier.public_report_metadata(report), report)
        self.assertEqual(report["status"], "passed")
        self.assertIn("SESSION_SECRET", report["host_secret_manager_only_env_names"])
        for stage in ("exception", "privacy_guard", "metadata_projection"):
            expected = verifier.safe_failure_report(stage)
            self.assertEqual(verifier.public_report_metadata(expected), expected)

    def test_cli_projection_refuses_canaries_at_every_string_leaf(self):
        import copy
        canary = "synthetic-private-projection-canary"
        def leaves(value, path=()):
            if isinstance(value, dict):
                for key, child in value.items():
                    yield from leaves(child, path + (key,))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    yield from leaves(child, path + (index,))
            elif isinstance(value, str):
                yield path
        for report in (self.projected_fixture(), verifier.safe_failure_report("exception")):
            for path in leaves(report):
                candidate = copy.deepcopy(report)
                target = candidate
                for key in path[:-1]: target = target[key]
                target[path[-1]] = canary
                stdout = io.StringIO()
                with self.subTest(path=path), mock.patch.object(verifier, "run_verification", return_value=candidate), contextlib.redirect_stdout(stdout):
                    self.assertEqual(verifier.main([]), 1)
                output = stdout.getvalue()
                self.assertNotIn(canary, output)
                self.assertEqual(json.loads(output)["failed_stage"], "metadata_projection")

    def test_projection_rejects_extra_keys_wrong_types_and_missing_fields(self):
        import copy
        report = self.projected_fixture()
        cases = [None, [], report | {"unexpected": "synthetic-canary"},
                 report | {"synthetic_only": 1}, report | {"status": "PASSED"},
                 report | {"required_env_names": ["UNREVIEWED_CONFIGURATION_VALUE"]},
                 report | {"evidence": report["evidence"] | {"extra": "synthetic-canary"}}]
        missing = copy.deepcopy(report); del missing["privacy"]; cases.append(missing)
        for candidate in cases:
            with self.subTest(kind=type(candidate).__name__), self.assertRaisesRegex(ValueError, "^Unsafe verification metadata[.]$"):
                verifier.public_report_metadata(candidate)

    def test_existing_prohibited_value_guard_remains_before_projection(self):
        with contextlib.ExitStack() as stack:
            for dependency in (verifier.verify_database_backup_restore, verifier.verify_hosted_observability, verifier.verify_hosted_smoke):
                stack.enter_context(mock.patch.object(dependency, "run_verification", return_value={"status": "passed"}))
            stack.enter_context(mock.patch.object(verifier.webapp, "production_readiness_status", return_value={
                "blockers": [{"id": "private-code"}], "production_blockers": []}))
            report = verifier.run_verification(work_dir=ROOT / "_logs" / "server" / "synthetic-projection")
        self.assertEqual(report, verifier.safe_failure_report("privacy_guard"))
        self.assertEqual(verifier.public_report_metadata(report), report)

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
