import json
import unittest
from unittest import mock

from webapp import server as webapp


class ProductionReadinessStatusTest(unittest.TestCase):
    def readiness_json(self, env):
        with mock.patch.dict(webapp.os.environ, env, clear=True):
            status = webapp.production_readiness_status(security_scan_status="standard_scan_started")
        return json.dumps(status, sort_keys=True)

    def test_readiness_status_redacts_private_values_and_lists_storage_surfaces(self):
        private_root = "C:/Users/Private/Koncept Runtime"
        database_url = "sqlite:///C:/Users/Private/kqag-storage.sqlite3?token=secret"
        text = self.readiness_json(
            {
                "QUOTE_DATA_ROOT": private_root,
                "QUOTE_OUTPUT_ROOT": private_root + "/output",
                "QUOTE_TMP_ROOT": private_root + "/tmp",
                "QUOTE_LOG_ROOT": private_root + "/logs",
                "KQAG_DATABASE_URL": database_url,
                "OIDC_CLIENT_SECRET": "super-secret",
            }
        )

        self.assertNotIn(private_root, text)
        self.assertNotIn(database_url, text)
        self.assertNotIn("super-secret", text)
        for surface in (
            "profiles_storage",
            "pricing_references_storage",
            "quote_sessions_storage",
            "generated_artifacts_storage",
        ):
            self.assertIn(surface, text)

    def test_local_storage_mode_is_not_production_ready_but_keeps_local_uat_supported(self):
        with mock.patch.dict(webapp.os.environ, {}, clear=True):
            status = webapp.production_readiness_status()

        self.assertEqual(status["kqag_storage_mode"], "local")
        self.assertEqual(status["kqag_artifact_storage_mode"], "local")
        self.assertTrue(status["local_uat_supported"])
        self.assertFalse(status["internal_alpha_ready"])
        self.assertFalse(status["production_ready"])
        self.assertIn("local_runtime_storage", {item["id"] for item in status["blockers"]})

    def test_database_and_artifact_modes_are_evaluated_separately(self):
        database_url = "sqlite:///tmp/kqag-storage.sqlite3"
        with mock.patch.dict(
            webapp.os.environ,
            {"KQAG_STORAGE_MODE": "database", "KQAG_ARTIFACT_STORAGE_MODE": "local", "KQAG_DATABASE_URL": database_url},
            clear=True,
        ):
            mixed_status = webapp.production_readiness_status()

        self.assertEqual(mixed_status["profiles_storage"]["mode"], "database")
        self.assertEqual(mixed_status["pricing_references_storage"]["mode"], "database")
        self.assertEqual(mixed_status["quote_sessions_storage"]["mode"], "database")
        self.assertEqual(mixed_status["generated_artifacts_storage"]["mode"], "local")
        self.assertFalse(mixed_status["workspace_scoped"])
        self.assertIn("local_artifact_storage", {item["id"] for item in mixed_status["blockers"]})

        with mock.patch.dict(
            webapp.os.environ,
            {"KQAG_STORAGE_MODE": "database", "KQAG_ARTIFACT_STORAGE_MODE": "database", "KQAG_DATABASE_URL": database_url},
            clear=True,
        ):
            database_status = webapp.production_readiness_status()

        self.assertEqual(database_status["generated_artifacts_storage"]["mode"], "database")
        self.assertTrue(database_status["workspace_scoped"])
        self.assertIn("object_storage_missing", {item["id"] for item in database_status["blockers"]})
        self.assertIn("profile_runtime_layout_dependency", {item["id"] for item in database_status["blockers"]})


if __name__ == "__main__":
    unittest.main()
