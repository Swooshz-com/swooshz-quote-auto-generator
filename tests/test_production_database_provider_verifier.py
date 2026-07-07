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

    def test_postgres_compatible_url_reports_runtime_gap_without_private_values(self):
        private_url = "postgres" + "ql://redacted-db-url"
        report = verifier.run_verification(
            env={
                "KQAG_DATABASE_URL": private_url,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            driver_available=True,
        )

        text = json.dumps(report, sort_keys=True)
        self.assertEqual(report["database_family"], "postgres_compatible")
        self.assertTrue(report["live_database_evidence_enabled"])
        self.assertTrue(report["metadata_migrations"]["metadata_tables_declared"])
        self.assertIn("postgres_runtime_adapter_missing", report["blockers"])
        self.assertFalse(report["production_database_evidence_supported"])
        self.assertFalse(report["connection_attempted"])
        self.assertNotIn(private_url, text)

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
