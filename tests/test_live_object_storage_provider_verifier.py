import contextlib
import io
import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import verify_live_object_storage_provider as verifier


class FakeLiveS3Client:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = {
            "body": bytes(kwargs["Body"]),
            "content_type": kwargs.get("ContentType"),
            "metadata": dict(kwargs.get("Metadata") or {}),
        }
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, **kwargs):
        item = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {
            "Body": BytesIO(item["body"]),
            "ContentLength": len(item["body"]),
            "ContentType": item["content_type"],
            "Metadata": dict(item["metadata"]),
        }

    def head_object(self, **kwargs):
        item = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        return {
            "ContentLength": len(item["body"]),
            "ContentType": item["content_type"],
            "Metadata": dict(item["metadata"]),
        }

    def delete_object(self, **kwargs):
        self.deleted.append((kwargs["Bucket"], kwargs["Key"]))
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}


def complete_env() -> dict[str, str]:
    return {
        "SQAG_LIVE_OBJECT_STORAGE_EVIDENCE": "1",
        "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "<redacted-endpoint-url>",
        "SQAG_OBJECT_STORAGE_BUCKET": "<redacted-bucket-name>",
        "SQAG_OBJECT_STORAGE_REGION": "<redacted-region>",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "<redacted-access-key-id>",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "<redacted-secret-access-key>",
    }


def legacy_sqag_env() -> dict[str, str]:
    legacy_prefix = "K" + "QAG"
    return {
        f"{legacy_prefix}_LIVE_OBJECT_STORAGE_EVIDENCE": "1",
        f"{legacy_prefix}_OBJECT_STORAGE_PROVIDER": "s3_compatible",
        f"{legacy_prefix}_OBJECT_STORAGE_ENDPOINT_URL": "<redacted-endpoint-url>",
        f"{legacy_prefix}_OBJECT_STORAGE_BUCKET": "<redacted-bucket-name>",
        f"{legacy_prefix}_OBJECT_STORAGE_REGION": "<redacted-region>",
        f"{legacy_prefix}_OBJECT_STORAGE_ACCESS_KEY_ID": "<redacted-access-key-id>",
        f"{legacy_prefix}_OBJECT_STORAGE_SECRET_ACCESS_KEY": "<redacted-secret-access-key>",
    }


class LiveObjectStorageProviderVerifierTest(unittest.TestCase):
    def test_no_env_refuses_to_run_and_reports_metadata_only(self):
        report = verifier.run_verification(env={})
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["live_provider_evidence_supported"])
        self.assertEqual(report["provider"]["family"], "disabled")
        self.assertIn("SQAG_LIVE_OBJECT_STORAGE_EVIDENCE", report["missing_env_names"])
        self.assertIn("SQAG_OBJECT_STORAGE_PROVIDER", report["required_env_names"])
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertNotIn("https://", text)
        self.assertNotIn("bucket", text.lower().replace("SQAG_OBJECT_STORAGE_BUCKET".lower(), ""))
        self.assertNotIn("<redacted-", text)

    def test_incomplete_env_lists_missing_names_only(self):
        env = complete_env()
        secret_value = env.pop("SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY")
        endpoint_value = env["SQAG_OBJECT_STORAGE_ENDPOINT_URL"]
        bucket_value = env["SQAG_OBJECT_STORAGE_BUCKET"]

        report = verifier.run_verification(env=env)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["live_provider_evidence_supported"])
        self.assertEqual(report["provider"]["family"], "s3_compatible")
        self.assertIn("SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY", report["missing_env_names"])
        self.assertNotIn(secret_value, text)
        self.assertNotIn(endpoint_value, text)
        self.assertNotIn(bucket_value, text)

    def test_legacy_sqag_env_does_not_satisfy_live_provider_evidence(self):
        report = verifier.run_verification(env=legacy_sqag_env())

        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["live_provider_evidence_supported"])
        self.assertEqual(report["provider"]["family"], "disabled")
        self.assertIn("SQAG_LIVE_OBJECT_STORAGE_EVIDENCE", report["missing_env_names"])
        self.assertIn("SQAG_OBJECT_STORAGE_PROVIDER", report["missing_env_names"])
        legacy_prefix = "K" + "QAG"
        self.assertNotIn(f"{legacy_prefix}_LIVE_OBJECT_STORAGE_EVIDENCE", report["required_env_names"])
        self.assertNotIn(f"{legacy_prefix}_OBJECT_STORAGE_PROVIDER", report["required_env_names"])

    def test_injected_backend_exercises_checks_without_claiming_live_evidence(self):
        fake_client = FakeLiveS3Client()

        def backend_factory(_env):
            return verifier.build_s3_backend_for_test(bucket="<redacted-bucket-name>", client=fake_client)

        report = verifier.run_verification(env=complete_env(), backend_factory=backend_factory)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["test_injected_backend"])
        self.assertFalse(report["live_provider_evidence_supported"])
        for check_name in (
            "store",
            "retrieve",
            "checksum",
            "content_type",
            "byte_size",
            "wrong_workspace_denied",
            "delete",
            "tombstone",
            "missing_object_failed_closed",
        ):
            self.assertTrue(report["checks"][check_name], check_name)
        self.assertNotIn("<redacted-bucket-name>", text)
        self.assertNotIn("workspaces/", text)
        self.assertNotIn("synthetic-live-provider-xlsx-bytes", text)

    def test_simulated_real_provider_pass_removes_only_live_evidence_missing_blocker(self):
        fake_client = FakeLiveS3Client()
        provider_status = {
            "provider": "s3_compatible",
            "configured": True,
            "runtime_backend_available": True,
            "synthetic_only": False,
            "missing_fields": [],
            "blockers": [
                "live_provider_evidence_missing",
                "db_object_backup_restore_unverified",
                "retention_delete_evidence_missing",
            ],
        }

        with mock.patch.object(verifier, "object_storage_provider_status", return_value=provider_status):
            with mock.patch.object(
                verifier,
                "_build_live_s3_backend",
                return_value=verifier.build_s3_backend_for_test(bucket="<redacted-bucket-name>", client=fake_client),
            ):
                report = verifier.run_verification(env=complete_env())

        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["test_injected_backend"])
        self.assertTrue(report["live_provider_evidence_supported"])
        self.assertNotIn("live_provider_evidence_missing", report["provider"]["blockers"])
        self.assertIn("db_object_backup_restore_unverified", report["provider"]["blockers"])
        self.assertIn("retention_delete_evidence_missing", report["provider"]["blockers"])

    def test_cli_output_is_json_metadata_only_when_env_missing(self):
        stdout = io.StringIO()
        with mock.patch.dict(verifier.os.environ, {}, clear=True), contextlib.redirect_stdout(stdout):
            exit_code = verifier.main([])
        output = stdout.getvalue()
        report = json.loads(output)

        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["live_provider_evidence_supported"])
        self.assertNotIn("https://", output)
        self.assertNotIn("<redacted-", output)


if __name__ == "__main__":
    unittest.main()
