import json
import unittest


from webapp import object_storage


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
            "KQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://private-object-store.example.test/private-path",
            "KQAG_OBJECT_STORAGE_BUCKET": "private-koncept-bucket",
            "KQAG_OBJECT_STORAGE_REGION": "ap-southeast-private",
            "KQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "AKIA_PRIVATE_TEST_KEY",
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
            "KQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://private-object-store.example.test",
            "KQAG_OBJECT_STORAGE_BUCKET": "private-koncept-bucket",
            "KQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "KQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "AKIA_PRIVATE_TEST_KEY",
            "KQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "private-secret-access-key",
        }

        status = object_storage.object_storage_provider_status(env)
        text = json.dumps(status, sort_keys=True)

        self.assertEqual(status["provider"], "s3_compatible")
        self.assertTrue(status["configured"])
        self.assertEqual(status["missing_fields"], [])
        self.assertEqual(status["adapter"], "s3_compatible_scaffold")
        self.assertFalse(status["runtime_backend_available"])
        self.assertFalse(status["production_provider_ready"])
        self.assertIn("provider_adapter_unwired", status["blockers"])
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


if __name__ == "__main__":
    unittest.main()
