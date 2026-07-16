import hashlib
import json
from io import BytesIO
import unittest
from unittest import mock


from webapp import object_storage


class FakeS3Client:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.head_bucket_calls = []

    def head_bucket(self, **kwargs):
        self.head_bucket_calls.append(kwargs["Bucket"])
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

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
    def assert_transformed_identity_segment(self, canonical, segment):
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertTrue(segment.startswith("~"))
        self.assertTrue(segment.endswith(f"-{digest}"))
        self.assertLessEqual(
            len(segment),
            object_storage.SAFE_SEGMENT_MAX_LENGTH,
        )
        self.assertNotIn("/", segment)
        self.assertNotIn("\\", segment)

    def assert_owner_delete_isolation(self, first_owner, second_owner):
        backend = object_storage.InMemoryObjectStorageBackend()
        shared = {
            "workspace_id": "workspace-isolation",
            "owner_type": "profile",
            "artifact_kind": "layout",
            "filename": "layout.json",
            "content_type": "application/json",
            "content": b"identical-synthetic-layout",
        }
        first = backend.store_artifact(owner_id=first_owner, **shared)
        second = backend.store_artifact(owner_id=second_owner, **shared)

        self.assertEqual(first.owner_id, first_owner)
        self.assertEqual(second.owner_id, second_owner)
        self.assertNotEqual(first.storage_key, second.storage_key)
        self.assertEqual(
            backend.retrieve_artifact(first, workspace_id="workspace-isolation"),
            shared["content"],
        )
        self.assertEqual(
            backend.retrieve_artifact(second, workspace_id="workspace-isolation"),
            shared["content"],
        )
        self.assertTrue(
            backend.delete_artifact(first, workspace_id="workspace-isolation")
        )
        self.assertFalse(
            backend.verify_metadata(first, workspace_id="workspace-isolation")
        )
        self.assertTrue(
            backend.verify_metadata(second, workspace_id="workspace-isolation")
        )
        self.assertEqual(
            backend.retrieve_artifact(second, workspace_id="workspace-isolation"),
            shared["content"],
        )

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
            "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test/path",
            "SQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
            "SQAG_OBJECT_STORAGE_REGION": "example-region-1",
            "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
        }

        status = object_storage.object_storage_provider_status(env)
        text = json.dumps(status, sort_keys=True)

        self.assertEqual(status["provider"], "s3_compatible")
        self.assertFalse(status["configured"])
        self.assertEqual(status["missing_fields"], ["SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY"])
        self.assertFalse(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])
        for name, value in env.items():
            if name == "SQAG_OBJECT_STORAGE_PROVIDER":
                continue
            self.assertNotIn(value, text)

    def test_s3_compatible_provider_config_is_metadata_only_and_unwired(self):
        env = {
            "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test",
            "SQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
            "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
            "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "example-secret-key",
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
            if name == "SQAG_OBJECT_STORAGE_PROVIDER":
                continue
            self.assertNotIn(value, text)

    def test_s3_compatible_provider_with_sdk_is_runtime_available_but_not_production_ready(self):
        env = {
            "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test",
            "SQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
            "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
            "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "example-secret-key",
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
            if name == "SQAG_OBJECT_STORAGE_PROVIDER":
                continue
            self.assertNotIn(value, text)

    def test_s3_compatible_provider_accepts_loopback_http_endpoint_for_local_smoke(self):
        env = {
            "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "http://127.0.0.1:9000",
            "SQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
            "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
            "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "example-secret-key",
        }

        with mock.patch.object(object_storage, "_optional_s3_sdk_available", return_value=True):
            status = object_storage.object_storage_provider_status(env)

        self.assertTrue(status["configured"])
        self.assertTrue(status["runtime_backend_available"])
        self.assertNotIn("insecure_endpoint_url", status["blockers"])

    def test_s3_compatible_provider_rejects_remote_plain_http_endpoint(self):
        env = {
            "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "http://object-store.example.test",
            "SQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
            "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
            "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "example-secret-key",
        }

        with mock.patch.object(object_storage, "_optional_s3_sdk_available", return_value=True):
            status = object_storage.object_storage_provider_status(env)
        text = json.dumps(status, sort_keys=True)

        self.assertFalse(status["configured"])
        self.assertFalse(status["runtime_backend_available"])
        self.assertIn("insecure_endpoint_url", status["blockers"])
        self.assertNotIn(env["SQAG_OBJECT_STORAGE_ENDPOINT_URL"], text)

    def test_legacy_sqag_provider_env_is_ignored(self):
        legacy_prefix = "K" + "QAG"
        status = object_storage.object_storage_provider_status(
            {
                f"{legacy_prefix}_OBJECT_STORAGE_PROVIDER": "s3_compatible",
                f"{legacy_prefix}_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test",
                f"{legacy_prefix}_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
                f"{legacy_prefix}_OBJECT_STORAGE_REGION": "ap-southeast-1",
                f"{legacy_prefix}_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
                f"{legacy_prefix}_OBJECT_STORAGE_SECRET_ACCESS_KEY": "example-secret-key",
            }
        )

        self.assertEqual(status["provider"], "disabled")
        self.assertFalse(status["configured"])
        self.assertEqual(status["required_fields"], [])
        self.assertEqual(status["missing_fields"], [])
        self.assertFalse(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])

    def test_synthetic_provider_is_test_only_and_not_production_ready(self):
        status = object_storage.object_storage_provider_status({"SQAG_OBJECT_STORAGE_PROVIDER": "synthetic"})

        self.assertEqual(status["provider"], "synthetic")
        self.assertTrue(status["configured"])
        self.assertTrue(status["synthetic_only"])
        self.assertFalse(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])
        self.assertIn("synthetic_provider_test_only", status["blockers"])

    def test_s3_compatible_readiness_probe_is_read_only_and_fails_closed(self):
        client = FakeS3Client()
        backend = object_storage.S3CompatibleObjectStorageBackend(
            bucket="example-artifact-bucket",
            client=client,
        )

        self.assertTrue(backend.readiness_probe())
        self.assertEqual(client.head_bucket_calls, ["example-artifact-bucket"])
        self.assertEqual(client.objects, {})

        client.head_bucket = mock.Mock(side_effect=RuntimeError("private-provider-response"))
        self.assertFalse(backend.readiness_probe())

    def test_s3_adapter_distinguishes_authoritative_missing_from_provider_outage(self):
        client = FakeS3Client()
        backend = object_storage.S3CompatibleObjectStorageBackend(bucket="synthetic", client=client)
        metadata = backend.store_artifact(
            workspace_id="workspace-missing",
            owner_type="generated_quote",
            owner_id="quote-missing",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type="application/octet-stream",
            content=b"synthetic-object",
        )

        missing = RuntimeError("private provider detail")
        missing.response = {
            "Error": {"Code": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }
        client.get_object = mock.Mock(side_effect=missing)
        with self.assertRaises(object_storage.ObjectStorageNotFoundError):
            backend.retrieve_artifact(metadata, workspace_id="workspace-missing")

        client.get_object = mock.Mock(side_effect=TimeoutError("private provider timeout"))
        with self.assertRaises(object_storage.ObjectStorageContractError) as captured:
            backend.retrieve_artifact(metadata, workspace_id="workspace-missing")
        self.assertNotIsInstance(captured.exception, object_storage.ObjectStorageNotFoundError)

    def test_object_artifact_key_preserves_existing_short_safe_shape(self):
        key = object_storage.object_artifact_key(
            workspace_id="workspace-a",
            owner_type="profile",
            owner_id="profile-a",
            artifact_kind="layout",
            filename="layout.json",
            checksum_sha256="a" * 64,
        )

        self.assertEqual(
            key,
            "workspaces/workspace-a/profile/profile-a/layout/"
            "aaaaaaaaaaaaaaaa-layout.json",
        )

    def test_identity_segment_separates_unchanged_and_transformed_owner_domains(self):
        long_owner = "b" * 121
        derived_segment = object_storage.identity_segment(long_owner, "")
        adversarial_owner = derived_segment
        adversarial_segment = object_storage.identity_segment(
            adversarial_owner,
            "",
        )

        self.assertNotEqual(derived_segment, adversarial_segment)
        self.assert_transformed_identity_segment(long_owner, derived_segment)
        self.assert_transformed_identity_segment(
            adversarial_owner,
            adversarial_segment,
        )
        self.assertEqual(
            derived_segment,
            object_storage.identity_segment(long_owner, ""),
        )
        self.assertEqual(
            adversarial_segment,
            object_storage.identity_segment(adversarial_owner, ""),
        )

    def test_object_artifact_key_separates_cross_domain_owners(self):
        long_owner = "b" * 121
        adversarial_owner = object_storage.identity_segment(long_owner, "")
        shared = {
            "workspace_id": "workspace-a",
            "owner_type": "profile",
            "artifact_kind": "layout",
            "filename": "layout.json",
            "checksum_sha256": "a" * 64,
        }

        self.assertNotEqual(
            object_storage.object_artifact_key(owner_id=long_owner, **shared),
            object_storage.object_artifact_key(
                owner_id=adversarial_owner,
                **shared,
            ),
        )

    def test_in_memory_backend_keeps_cross_domain_owners_isolated_when_deleting_first(self):
        long_owner = "b" * 121
        adversarial_owner = object_storage.identity_segment(long_owner, "")

        self.assert_owner_delete_isolation(long_owner, adversarial_owner)

    def test_in_memory_backend_keeps_cross_domain_owners_isolated_when_deleting_second(self):
        long_owner = "b" * 121
        adversarial_owner = object_storage.identity_segment(long_owner, "")

        self.assert_owner_delete_isolation(adversarial_owner, long_owner)

    def test_cross_domain_workspace_keys_and_access_remain_isolated(self):
        long_workspace = "w" * 121
        adversarial_workspace = object_storage.identity_segment(
            long_workspace,
            "",
        )
        backend = object_storage.InMemoryObjectStorageBackend()
        shared = {
            "owner_type": "profile",
            "owner_id": "profile-a",
            "artifact_kind": "layout",
            "filename": "layout.json",
            "content_type": "application/json",
            "content": b"identical-synthetic-layout",
        }

        first = backend.store_artifact(workspace_id=long_workspace, **shared)
        second = backend.store_artifact(
            workspace_id=adversarial_workspace,
            **shared,
        )

        self.assert_transformed_identity_segment(
            long_workspace,
            object_storage.identity_segment(long_workspace, ""),
        )
        self.assert_transformed_identity_segment(
            adversarial_workspace,
            object_storage.identity_segment(adversarial_workspace, ""),
        )
        self.assertEqual(first.workspace_id, long_workspace)
        self.assertEqual(second.workspace_id, adversarial_workspace)
        self.assertNotEqual(first.storage_key, second.storage_key)
        self.assertEqual(
            backend.retrieve_artifact(first, workspace_id=long_workspace),
            shared["content"],
        )
        self.assertEqual(
            backend.retrieve_artifact(
                second,
                workspace_id=adversarial_workspace,
            ),
            shared["content"],
        )
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.retrieve_artifact(first, workspace_id=adversarial_workspace)
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.retrieve_artifact(second, workspace_id=long_workspace)
        self.assertFalse(
            backend.verify_metadata(first, workspace_id=adversarial_workspace)
        )
        self.assertFalse(
            backend.verify_metadata(second, workspace_id=long_workspace)
        )
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.delete_artifact(first, workspace_id=adversarial_workspace)
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.delete_artifact(second, workspace_id=long_workspace)
        self.assertTrue(
            backend.verify_metadata(first, workspace_id=long_workspace)
        )
        self.assertTrue(
            backend.verify_metadata(
                second,
                workspace_id=adversarial_workspace,
            )
        )
        self.assertTrue(
            backend.delete_artifact(first, workspace_id=long_workspace)
        )
        self.assertTrue(
            backend.verify_metadata(
                second,
                workspace_id=adversarial_workspace,
            )
        )

    def test_sanitized_identity_and_its_transformed_segment_do_not_collide(self):
        canonical = "profile/unsupported?identity"
        transformed = object_storage.identity_segment(canonical, "")
        transformed_again = object_storage.identity_segment(transformed, "")

        self.assertNotEqual(transformed, transformed_again)
        self.assert_transformed_identity_segment(canonical, transformed)
        self.assert_transformed_identity_segment(transformed, transformed_again)

    def test_marker_containing_canonical_identity_is_transformed_again(self):
        canonical = "~already-transformed-looking-identity"
        encoded = object_storage.identity_segment(canonical, "")

        self.assertNotEqual(encoded, canonical)
        self.assertEqual(
            encoded,
            object_storage.identity_segment(canonical, ""),
        )
        self.assert_transformed_identity_segment(canonical, encoded)

    def test_identity_segment_boundaries_and_fallbacks(self):
        cases = (
            ("a" * 120, "fallback", "a" * 120, False),
            ("a" * 121, "fallback", None, True),
            ("long-" + ("z" * 4096), "fallback", None, True),
            ("changed/only/by/sanitization", "fallback", None, True),
            ("  whitespace-safe  ", "fallback", "whitespace-safe", False),
            ("unicode-?", "fallback", None, True),
            ("unsupported!?punctuation", "fallback", None, True),
            ("", "fallback", "fallback", False),
            ("///", "fallback", None, True),
        )

        for canonical_input, fallback, expected, transformed in cases:
            with self.subTest(canonical_input=canonical_input):
                canonical = object_storage.canonical_identity(canonical_input)
                encoded = object_storage.identity_segment(
                    canonical_input,
                    fallback,
                )
                self.assertEqual(
                    encoded,
                    object_storage.identity_segment(
                        canonical_input,
                        fallback,
                    ),
                )
                self.assertLessEqual(
                    len(encoded),
                    object_storage.SAFE_SEGMENT_MAX_LENGTH,
                )
                self.assertNotIn("/", encoded)
                self.assertNotIn("\\", encoded)
                if transformed:
                    self.assert_transformed_identity_segment(canonical, encoded)
                else:
                    self.assertEqual(encoded, expected)

    def test_legacy_safe_identity_segments_remain_byte_for_byte_unchanged(self):
        for canonical in (
            "letters",
            "LettersAZ",
            "digits0123456789",
            "hyphen-safe",
            "underscore_safe",
            "period.safe",
            "mixed-AZ_09.safe",
        ):
            with self.subTest(canonical=canonical):
                self.assertEqual(
                    object_storage.identity_segment(canonical, ""),
                    canonical,
                )

    def test_existing_transformed_collision_classes_remain_separated(self):
        long_prefix = "p" * 120
        pairs = (
            (long_prefix + "x", long_prefix + "y"),
            ("profile/a", "profile?a"),
            (
                ("shared-" + ("q" * 120)) + "x",
                ("shared-" + ("q" * 120)) + "y",
            ),
        )

        for first, second in pairs:
            with self.subTest(first=first, second=second):
                first_segment = object_storage.identity_segment(first, "")
                second_segment = object_storage.identity_segment(second, "")
                self.assertNotEqual(first_segment, second_segment)
                self.assert_transformed_identity_segment(first, first_segment)
                self.assert_transformed_identity_segment(second, second_segment)

    def test_object_artifact_key_hashes_sanitization_collisions(self):
        shared = {
            "workspace_id": "workspace-a",
            "owner_type": "profile",
            "artifact_kind": "layout",
            "filename": "layout.json",
            "checksum_sha256": "a" * 64,
        }

        slash_key = object_storage.object_artifact_key(
            owner_id="profile/a",
            **shared,
        )
        question_key = object_storage.object_artifact_key(
            owner_id="profile?a",
            **shared,
        )

        self.assertNotEqual(slash_key, question_key)
        self.assertIn("profile-a-", slash_key)
        self.assertIn("profile-a-", question_key)

    def test_in_memory_backend_keeps_long_common_prefix_owners_isolated(self):
        backend = object_storage.InMemoryObjectStorageBackend()
        common_prefix = "profile-" + ("a" * 112)
        owner_a = common_prefix + "x"
        owner_b = common_prefix + "y"
        shared = {
            "workspace_id": "workspace-a",
            "owner_type": "profile",
            "artifact_kind": "layout",
            "filename": "layout.json",
            "content_type": "application/json",
            "content": b"synthetic-layout-content",
        }

        first = backend.store_artifact(owner_id=owner_a, **shared)
        second = backend.store_artifact(owner_id=owner_b, **shared)

        self.assertEqual(first.owner_id, owner_a)
        self.assertEqual(second.owner_id, owner_b)
        self.assertNotEqual(first.storage_key, second.storage_key)
        self.assertTrue(
            backend.delete_artifact(second, workspace_id="workspace-a")
        )
        self.assertEqual(
            backend.retrieve_artifact(first, workspace_id="workspace-a"),
            b"synthetic-layout-content",
        )
        self.assertFalse(
            backend.verify_metadata(second, workspace_id="workspace-a")
        )

    def test_s3_adapter_preserves_canonical_identities_in_provider_metadata(self):
        client = FakeS3Client()
        backend = object_storage.S3CompatibleObjectStorageBackend(
            bucket="example-artifact-bucket",
            client=client,
        )
        workspace_id = "workspace/alpha"
        owner_id = ("profile-" + ("a" * 112)) + "x"

        metadata = backend.store_artifact(
            workspace_id=workspace_id,
            owner_type="profile",
            owner_id=owner_id,
            artifact_kind="layout",
            filename="layout.json",
            content_type="application/json",
            content=b"synthetic-layout-content",
        )

        key_parts = metadata.storage_key.split("/")
        self.assertEqual(key_parts[1], object_storage.identity_segment(workspace_id, ""))
        self.assertEqual(key_parts[3], object_storage.identity_segment(owner_id, ""))

        stored = client.objects[
            ("example-artifact-bucket", metadata.storage_key)
        ]
        self.assertEqual(metadata.workspace_id, workspace_id)
        self.assertEqual(metadata.owner_id, owner_id)
        self.assertEqual(
            stored["metadata"]["sqag-workspace-id"],
            workspace_id,
        )
        self.assertEqual(
            stored["metadata"]["sqag-owner-id"],
            owner_id,
        )
        self.assertEqual(
            backend.retrieve_artifact(metadata, workspace_id=workspace_id),
            b"synthetic-layout-content",
        )
        self.assertFalse(
            backend.verify_metadata(metadata, workspace_id="different-workspace")
        )
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.retrieve_artifact(metadata, workspace_id="different-workspace")
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.delete_artifact(metadata, workspace_id="different-workspace")
        self.assertEqual(client.deleted, [])
        self.assertTrue(
            backend.delete_artifact(metadata, workspace_id=workspace_id)
        )
        self.assertFalse(
            backend.verify_metadata(metadata, workspace_id=workspace_id)
        )


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
        stored["metadata"]["sqag-workspace-id"] = "workspace-b"

        self.assertFalse(backend.verify_metadata(metadata, workspace_id="workspace-a"))
        with self.assertRaises(object_storage.ObjectStorageContractError):
            backend.delete_artifact(metadata, workspace_id="workspace-a")
        self.assertEqual(client.deleted, [])


if __name__ == "__main__":
    unittest.main()
