import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import verify_production_database_provider as verifier

POSTGRES_URL = "postgres" + "ql://redacted-db-url"


class FakePostgresSchemaConnection:
    def __init__(self, columns_by_table):
        self.columns_by_table = {table: set(columns) for table, columns in columns_by_table.items()}

    def execute(self, _sql, params=None):
        requested_tables = set(params or ())
        rows = []
        for table in sorted(requested_tables):
            for column in sorted(self.columns_by_table.get(table, set())):
                rows.append({"table_name": table, "column_name": column})
        return FakePostgresSchemaCursor(rows)


class FakePostgresSchemaCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return list(self._rows)


class FakePostgresSchemaContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, _exc_type, _exc, _tb):
        return False


def runtime_required_metadata_tables():
    required = {}
    for table_map in (
        verifier.webapp.KQAG_APP_METADATA_REQUIRED_COLUMNS,
        verifier.webapp.KQAG_OBJECT_ARTIFACT_METADATA_REQUIRED_COLUMNS,
    ):
        for table, columns in table_map.items():
            required.setdefault(table, set()).update(columns)
    return {table: set(columns) for table, columns in required.items()}


def schema_status_for_runtime_columns(missing_columns=None):
    columns_by_table = {
        table: set(columns)
        for table, columns in runtime_required_metadata_tables().items()
    }
    for table, columns in (missing_columns or {}).items():
        columns_by_table[table] -= set(columns)
    connection = FakePostgresSchemaConnection(columns_by_table)
    with mock.patch(
        "verify_production_database_provider.webapp.postgres_storage_connection",
        return_value=FakePostgresSchemaContext(connection),
    ):
        return verifier.postgres_schema_status(POSTGRES_URL)


def migration_status_for_sql(sql: str):
    path = ROOT / "_tmp" / "tests" / f"production-db-migration-{time.time_ns()}.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sql, encoding="utf-8")
    try:
        return verifier.metadata_migration_status((path,))
    finally:
        path.unlink(missing_ok=True)


class ProductionDatabaseProviderVerifierTest(unittest.TestCase):
    def live_schema_report(self, missing_columns=None):
        runtime_schema = schema_status_for_runtime_columns(missing_columns)
        return verifier.run_verification(
            env={
                "KQAG_DATABASE_URL": POSTGRES_URL,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            driver_available=True,
            schema_validator=lambda _database_url: runtime_schema,
        )

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
        report = verifier.run_verification(
            env={
                "KQAG_DATABASE_URL": POSTGRES_URL,
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
        self.assertNotIn(POSTGRES_URL, text)

    def test_required_metadata_tables_match_runtime_metadata_schema_only(self):
        self.assertEqual(verifier.REQUIRED_METADATA_TABLES, runtime_required_metadata_tables())
        for db_blob_table in verifier.webapp.KQAG_DATABASE_ARTIFACT_REQUIRED_COLUMNS:
            self.assertNotIn(db_blob_table, verifier.REQUIRED_METADATA_TABLES)

    def test_metadata_migration_status_uses_runtime_required_columns(self):
        status = verifier.metadata_migration_status()

        self.assertTrue(status["metadata_tables_declared"])
        self.assertEqual(status["missing_tables"], [])
        self.assertEqual(status["missing_columns"], {})
        self.assertFalse(status["db_blob_tables_required_for_production"])
        self.assertIn("created_at", verifier.REQUIRED_METADATA_TABLES["kqag_profiles"])
        self.assertIn("updated_at", verifier.REQUIRED_METADATA_TABLES["kqag_quote_sessions"])
        self.assertIn("platform_user_id", verifier.REQUIRED_METADATA_TABLES["kqag_object_artifacts"])
        self.assertIn("deleted_at", verifier.REQUIRED_METADATA_TABLES["kqag_object_artifacts"])

    def test_live_schema_check_passes_with_all_runtime_required_metadata_columns(self):
        report = schema_status_for_runtime_columns()

        self.assertTrue(report["schema_available"])
        self.assertEqual(report["missing_tables"], [])
        self.assertEqual(report["missing_columns"], {})
        self.assertNotIn("kqag_quote_artifacts", report["required_tables"])

    def test_live_opt_in_validates_all_runtime_required_metadata_columns(self):
        report = self.live_schema_report()

        text = json.dumps(report, sort_keys=True)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["connection_attempted"])
        self.assertTrue(report["production_database_evidence_supported"])
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["runtime_schema"]["missing_columns"], {})
        self.assertIn("kqag_profiles", report["runtime_schema"]["required_tables"])
        self.assertIn("kqag_object_artifacts", report["runtime_schema"]["required_tables"])
        self.assertNotIn("kqag_quote_artifacts", report["runtime_schema"]["required_tables"])
        self.assertNotIn(POSTGRES_URL, text)

    def test_live_schema_check_fails_when_runtime_required_columns_are_missing(self):
        cases = (
            ("kqag_profiles", "created_at"),
            ("kqag_quote_sessions", "updated_at"),
            ("kqag_object_artifacts", "platform_user_id"),
            ("kqag_object_artifacts", "deleted_at"),
        )
        for table, column in cases:
            with self.subTest(table=table, column=column):
                report = schema_status_for_runtime_columns({table: {column}})

                self.assertFalse(report["schema_available"])
                self.assertIn(column, report["missing_columns"][table])

    def test_live_opt_in_fails_when_runtime_required_metadata_columns_are_missing(self):
        cases = (
            ("kqag_profiles", "created_at"),
            ("kqag_quote_sessions", "updated_at"),
            ("kqag_object_artifacts", "platform_user_id"),
            ("kqag_object_artifacts", "deleted_at"),
        )
        for table, column in cases:
            with self.subTest(table=table, column=column):
                report = self.live_schema_report({table: {column}})

                self.assertEqual(report["status"], "failed")
                self.assertIn("postgres_schema_missing", report["blockers"])
                self.assertFalse(report["production_database_evidence_supported"])
                self.assertEqual(report["runtime_schema"]["missing_columns"], {table: [column]})

    def test_metadata_migration_check_fails_when_runtime_required_column_is_missing(self):
        sql = """
        create table if not exists kqag_profiles (
          workspace_id text not null,
          profile_id text not null,
          payload_json text not null,
          updated_at text not null
        );
        create table if not exists kqag_pricing_references (
          workspace_id text not null,
          reference_id text not null,
          payload_json text not null,
          created_at text not null,
          updated_at text not null
        );
        create table if not exists kqag_quote_sessions (
          workspace_id text not null,
          session_id text not null,
          metadata_json text not null,
          draft_files_json text not null,
          created_at text not null,
          updated_at text not null
        );
        create table if not exists kqag_object_artifacts (
          artifact_id text not null primary key,
          workspace_id text not null,
          owner_type text not null,
          owner_id text not null,
          platform_user_id text,
          session_id text,
          job_id text,
          artifact_kind text not null,
          filename text not null,
          content_type text not null,
          size_bytes integer not null,
          checksum_sha256 text not null,
          object_provider_type text not null,
          object_key_ref text not null,
          status text not null,
          retention_status text not null,
          created_at text not null,
          updated_at text not null,
          deleted_at text
        );
        """
        status = migration_status_for_sql(sql)

        self.assertFalse(status["metadata_tables_declared"])
        self.assertEqual(status["missing_columns"], {"kqag_profiles": ["created_at"]})

    def test_live_opt_in_connection_failure_is_sanitized(self):
        def fail_schema(_database_url):
            raise RuntimeError("private connection details must not leak")

        report = verifier.run_verification(
            env={
                "KQAG_DATABASE_URL": POSTGRES_URL,
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
        self.assertNotIn(POSTGRES_URL, text)
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