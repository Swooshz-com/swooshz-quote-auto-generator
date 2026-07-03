import json
from io import BytesIO
import unittest
from unittest import mock


from webapp import object_storage


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def put_object(self, **kwargs):
        key = (kwargs["Bucket"], kwargs["Key"])
        self.objects[key] = {
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


class ObjectStorageProviderConfigTest(unittest.TestCase):
    def test_unset_provider_reports_disabled_without_secret_values(self):
        status = object_storage.object_storage_provider_status({})
        text = json.dumps(status, sort_keys=True)

        self.assertEqual(status["provider"], "disabled")
        self.assertFalse(status["configured"])
        self.assertFalse(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])
        self.assertEqual(status["missing_fields"], [])
        self.assertNotIn("http", text)

    def test_s3_compatible_provider_lists_missing_field_names_only(self):
        env = {
            "KQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "KQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test/path",
            "KQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
            "KQAG_OBJECT_STORAGE_REGION": "example-region-1",
            "KQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
        }

        status = object_storage.object_storage_provider_status(env)
        text = json.dumps(status, sort_keys=True)

        self.assertEqual(status["provider"], "s3_compatible")
        self.assertFalse(status["configured"])
        self.assertEqual(status["missing_fields"], ["KQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY"])
        self.assertFalse(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])
        for name, value in env.items():
            if name == "KQAG_OBJECT_STORAGE_PROVIDER":
                continue
            self.assertNotIn(value, text)

    def test_s3_compatible_provider_config_is_metadata_only_and_unwired(self):
        env = {
            "KQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "KQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test",
            "KQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
            "KQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "KQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
            "KQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "example-secret-key",
        }

        with mock.patch.object(object_storage, "_optional_s3_sdk_available", return_value=False):
            status = object_storage.object_storage_provider_status(env)
        text = json.dumps(status, sort_keys=True)

        self.assertEqual(status["provider"], "s3_compatible")
        self.assertTrue(status["configured"])
        self.assertEqual(status["missing_fields"], [])
        self.assertEqual(status["adapter"], "s3_compatible")
        self.assertFalse(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])
        self.assertIn("optional_s3_sdk_missing", status["blockers"])
        for name, value in env.items():
            if name == "KQAG_OBJECT_STORAGE_PROVIDER":
                continue
            self.assertNotIn(value, text)

    def test_s3_compatible_provider_with_sdk_is_runtime_available_but_not_production_ready(self):
        env = {
            "KQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "KQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test",
            "KQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
            "KQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "KQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
            "KQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "example-secret-key",
        }

        with mock.patch.object(object_storage, "_optional_s3_sdk_available", return_value=True):
            status = object_storage.object_storage_provider_status(env)
        text = json.dumps(status, sort_keys=True)

        self.assertEqual(status["adapter"], "s3_compatible")
        self.assertTrue(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])
        self.assertIn("live_provider_evidence_missing", status["blockers"])
        self.assertIn("db_object_backup_restore_unverified", status["blockers"])
        self.assertIn("retention_delete_evidence_missing", status["blockers"])
        for name, value in env.items():
            if name == "KQAG_OBJECT_STORAGE_PROVIDER":
                continue
            self.assertNotIn(value, text)

    def test_synthetic_provider_is_test_only_and_not_production_ready(self):
        status = object_storage.object_storage_provider_status({"KQAG_OBJECT_STORAGE_PROVIDER": "synthetic"})

        self.assertEqual(status["provider"], "synthetic")
        self.assertTrue(status["configured"])
        self.assertTrue(status["synthetic_only"])
        self.assertFalse(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])
        self.assertIn("synthetic_provider_test_only", status["blockers"])

    def test_s3_compatible_adapter_stores_retrieves_and_deletes_with_checksum(self):
        client = FakeS3Client()
        backend = object_storage.S3CompatibleObjectStorageBackend(
            bucket="example-artifact-bucket",
            client=client,
        )

        metadata = backend.store_artifact(
            workspace_id="workspace-a",
            owner_type="generated_quote",
            owner_id="quote-session-a",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=b"synthetic-xlsx-content",
        )

        self.assertEqual(metadata.workspace_id, "workspace-a")
        self.assertEqual(metadata.owner_type, "generated_quote")
        self.assertEqual(metadata.owner_id, "quote-session-a")
        self.assertEqual(metadata.size_bytes, len(b"synthetic-xlsx-content"))
        self.assertEqual(len(metadata.checksum_sha256), 64)
        self.assertNotIn("storage_key", metadata.public_metadata())
        self.assertEqual(
            backend.retrieve_artifact(metadata, workspace_id="workspace-a"),
            b"synthetic-xlsx-content",
        )
        self.assertTrue(backend.verify_metadata(metadata, workspace_id="workspace-a"))
        self.assertTrue(backend.delete_artifact(metadata, workspace_id="workspace-a"))
        self.assertFalse(backend.verify_metadata(metadata, workspace_id="workspace-a"))

    def test_s3_compatible_adapter_denies_wrong_workspace_without_client_read(self):
        client = FakeS3Client()
        backend = object_storage.S3CompatibleObjectStorageBackend(
            bucket="example-artifact-bucket",
            client=client,
        )
        metadata = backend.store_artifact(
            workspace_id="workspace-a",
            owner_type="generated_quote",
            owner_id="quote-session-a",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=b"synthetic-xlsx-content",
        )

        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.retrieve_artifact(metadata, workspace_id="workspace-b")
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.delete_artifact(metadata, workspace_id="workspace-b")
        self.assertEqual(client.deleted, [])
        self.assertTrue(backend.verify_metadata(metadata, workspace_id="workspace-a"))

    def test_s3_compatible_adapter_delete_fails_closed_on_corrupt_remote_metadata(self):
        client = FakeS3Client()
        backend = object_storage.S3CompatibleObjectStorageBackend(
            bucket="example-artifact-bucket",
            client=client,
        )
        metadata = backend.store_artifact(
            workspace_id="workspace-a",
            owner_type="generated_quote",
            owner_id="quote-session-a",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=b"synthetic-xlsx-content",
        )
        stored = client.objects[("example-artifact-bucket", metadata.storage_key)]
        stored["metadata"]["kqag-workspace-id"] = "workspace-b"

        self.assertFalse(backend.verify_metadata(metadata, workspace_id="workspace-a"))
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.delete_artifact(metadata, workspace_id="workspace-a")
        self.assertEqual(client.deleted, [])


if __name__ == "__main__":
    unittest.main()
