import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import verify_production_database_provider as verifier


class ProductionDatabaseProviderVerifierTest(unittest.TestCase):
    def test_missing_database_url_fails_closed(self):
        report = verifier.run_verification(env={}, driver_available=False)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["database_family"], "missing")
        self.assertIn("database_url_missing", report["blockers"])
        self.assertFalse(report["production_database_evidence_supported"])
        self.assertFalse(report["connection_attempted"])

    def test_sqlite_database_url_is_local_uat_only(self):
        report = verifier.run_verification(
            env={"KQAG_DATABASE_URL": "sqlite:///tmp/kqag-storage.sqlite3"},
            driver_available=False,
        )

        self.assertEqual(report["database_family"], "sqlite")
        self.assertIn("sqlite_not_final_production", report["blockers"])
        self.assertFalse(report["production_database_evidence_supported"])

    def test_unsupported_database_scheme_fails_closed(self):
        unsupported_url = "my" + "sql://redacted-db-url"
        report = verifier.run_verification(
            env={"KQAG_DATABASE_URL": unsupported_url},
            driver_available=True,
        )

        text = json.dumps(report, sort_keys=True)
        self.assertEqual(report["database_family"], "unsupported")
        self.assertIn("database_url_scheme_unsupported", report["blockers"])
        self.assertNotIn(unsupported_url, text)

    def test_postgres_compatible_url_reports_adapter_available_without_live_evidence(self):
        private_url = "postgres" + "ql://redacted-db-url"
        report = verifier.run_verification(
            env={
                "KQAG_DATABASE_URL": private_url,
            },
            driver_available=True,
        )

        text = json.dumps(report, sort_keys=True)
        self.assertEqual(report["database_family"], "postgres_compatible")
        self.assertFalse(report["live_database_evidence_enabled"])
        self.assertTrue(report["metadata_migrations"]["metadata_tables_declared"])
        self.assertTrue(report["app_runtime_postgres_supported"])
        self.assertNotIn("postgres_runtime_adapter_missing", report["blockers"])
        self.assertIn("live_database_evidence_not_enabled", report["blockers"])
        self.assertFalse(report["production_database_evidence_supported"])
        self.assertFalse(report["connection_attempted"])
        self.assertNotIn(private_url, text)

    def test_live_opt_in_can_validate_postgres_schema_with_fake_connection(self):
        private_url = "postgres" + "ql://redacted-db-url"
        report = verifier.run_verification(
            env={
                "KQAG_DATABASE_URL": private_url,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            driver_available=True,
            schema_validator=lambda _database_url: {
                "schema_available": True,
                "required_tables": {},
                "missing_tables": [],
                "missing_columns": {},
            },
        )

        text = json.dumps(report, sort_keys=True)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["connection_attempted"])
        self.assertTrue(report["production_database_evidence_supported"])
        self.assertEqual(report["blockers"], [])
        self.assertNotIn(private_url, text)

    def test_live_opt_in_connection_failure_is_sanitized(self):
        private_url = "postgres" + "ql://redacted-db-url"

        def fail_schema(_database_url):
            raise RuntimeError("private connection details must not leak")

        report = verifier.run_verification(
            env={
                "KQAG_DATABASE_URL": private_url,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            driver_available=True,
            schema_validator=fail_schema,
        )

        text = json.dumps(report, sort_keys=True)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["connection_attempted"])
        self.assertIn("postgres_connection_failed", report["blockers"])
        self.assertFalse(report["production_database_evidence_supported"])
        self.assertNotIn(private_url, text)
        self.assertNotIn("private connection details", text)

    def test_missing_metadata_migration_is_reported_by_file_name_only(self):
        missing_path = ROOT / "_tmp" / "tests" / "missing-production-db-migration.sql"
        private_url = "postgres" + "://redacted-db-url"
        report = verifier.run_verification(
            env={
                "KQAG_DATABASE_URL": private_url,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            migration_paths=(missing_path,),
            driver_available=True,
        )

        text = json.dumps(report, sort_keys=True)
        self.assertIn("postgres_metadata_migrations_missing", report["blockers"])
        self.assertEqual(report["metadata_migrations"]["missing_source_files"], [missing_path.name])
        self.assertNotIn(str(missing_path.parent), text)
        self.assertNotIn(private_url, text)


if __name__ == "__main__":
    unittest.main()
