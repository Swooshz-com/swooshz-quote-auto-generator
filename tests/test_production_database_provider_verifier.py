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
        verifier.webapp.SQAG_APP_METADATA_REQUIRED_COLUMNS,
        verifier.webapp.SQAG_OBJECT_ARTIFACT_METADATA_REQUIRED_COLUMNS,
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

class FakeLivePostgresCursor:
    def __init__(self, rows=None, rowcount: int = 0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeLivePostgresConnection:
    def __init__(
        self,
        *,
        leak_workspace_reads: bool = False,
        fail_object_cleanup: bool = False,
        fail_tombstone_commit: bool = False,
        fail_terminal_read: bool = False,
        tombstone_rowcount: int | None = None,
    ):
        self.leak_workspace_reads = leak_workspace_reads
        self.fail_object_cleanup = fail_object_cleanup
        self.fail_tombstone_commit = fail_tombstone_commit
        self.fail_terminal_read = fail_terminal_read
        self.tombstone_rowcount = tombstone_rowcount
        self.queries = []
        self.commits = 0
        self.profiles = {}
        self.pricing_references = {}
        self.quote_sessions = {}
        self.object_artifacts = {}
        self.object_artifact_insert_snapshots = []
        self._tombstone_pending = False
        self._tombstone_committed = False
        self._tombstone_before = None

    def _rows_for_workspace(self, collection, workspace_id):
        return [
            value
            for (stored_workspace, _), value in sorted(collection.items())
            if self.leak_workspace_reads or stored_workspace == workspace_id
        ]

    def _select_object_rows(self, normalized, params):
        if "where workspace_id = ? and artifact_id = ?" in normalized:
            if self.fail_terminal_read and self._tombstone_committed:
                raise RuntimeError("private post-commit read canary")
            workspace_id, artifact_id = params[:2]
            rows = [
                row
                for row in self.object_artifacts.values()
                if row["workspace_id"] == workspace_id and row["artifact_id"] == artifact_id
            ]
            return rows
        if (
            "where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?"
            in normalized
        ):
            workspace_id, owner_type, owner_id, artifact_kind = params[:4]
            rows = [
                row
                for row in self.object_artifacts.values()
                if row["workspace_id"] == workspace_id
                and row["owner_type"] == owner_type
                and row["owner_id"] == owner_id
                and row["artifact_kind"] == artifact_kind
            ]
            if "status = ?" in normalized:
                status, retention_status = params[4:6]
                rows = [
                    row
                    for row in rows
                    if row["status"] == status
                    and row["retention_status"] == retention_status
                    and row["deleted_at"] is None
                ]
            return rows
        workspace_id = params[0]
        rows = [
            row
            for row in self.object_artifacts.values()
            if self.leak_workspace_reads or row["workspace_id"] == workspace_id
        ]
        if "status = ?" in normalized:
            status, retention_status = params[1:3]
            rows = [
                row
                for row in rows
                if row["status"] == status
                and row["retention_status"] == retention_status
                and row["deleted_at"] is None
            ]
        return rows

    def execute(self, sql, params=None):
        params = tuple(params or ())
        self.queries.append((sql, params))
        normalized = " ".join(sql.lower().split())
        if "pg_try_advisory_xact_lock" in normalized:
            return FakeLivePostgresCursor([{"lock_acquired": True}])
        if "information_schema.columns" in normalized:
            column_map = runtime_required_metadata_tables()
            rows = []
            for table in sorted(set(params)):
                rows.extend(
                    {"table_name": table, "column_name": column}
                    for column in sorted(column_map.get(table, set()))
                )
            return FakeLivePostgresCursor(rows)
        if normalized.startswith("insert into sqag_profiles"):
            workspace_id, profile_id, payload_json, created_at, updated_at = params
            key = (workspace_id, profile_id)
            if key in self.profiles:
                raise RuntimeError("synthetic profile collision")
            self.profiles[key] = {
                "payload_json": payload_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakeLivePostgresCursor(rowcount=1)
        if normalized.startswith("select payload_json from sqag_profiles"):
            workspace_id = params[0]
            if "profile_id = ?" in normalized:
                row = self.profiles.get((workspace_id, params[1]))
                return FakeLivePostgresCursor([row] if row else [])
            return FakeLivePostgresCursor(self._rows_for_workspace(self.profiles, workspace_id))
        if normalized.startswith("select 1 from sqag_profiles"):
            row = self.profiles.get((params[0], params[1]))
            return FakeLivePostgresCursor([{"present": 1}] if row else [])
        if normalized.startswith("update sqag_profiles"):
            payload_json, updated_at, workspace_id, profile_id = params
            row = self.profiles.get((workspace_id, profile_id))
            if not row:
                return FakeLivePostgresCursor(rowcount=0)
            row.update({"payload_json": payload_json, "updated_at": updated_at})
            return FakeLivePostgresCursor(rowcount=1)
        if normalized.startswith("delete from sqag_profiles"):
            workspace_id, profile_id = params[:2]
            existed = self.profiles.pop((workspace_id, profile_id), None) is not None
            return FakeLivePostgresCursor(rowcount=1 if existed else 0)
        if normalized.startswith("insert into sqag_pricing_references"):
            workspace_id, reference_id, payload_json, created_at, updated_at = params
            key = (workspace_id, reference_id)
            if key in self.pricing_references:
                raise RuntimeError("synthetic pricing collision")
            self.pricing_references[key] = {
                "payload_json": payload_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakeLivePostgresCursor(rowcount=1)
        if normalized.startswith("select payload_json from sqag_pricing_references"):
            workspace_id = params[0]
            if "reference_id = ?" in normalized:
                row = self.pricing_references.get((workspace_id, params[1]))
                return FakeLivePostgresCursor([row] if row else [])
            return FakeLivePostgresCursor(
                self._rows_for_workspace(self.pricing_references, workspace_id)
            )
        if normalized.startswith("select 1 from sqag_pricing_references"):
            row = self.pricing_references.get((params[0], params[1]))
            return FakeLivePostgresCursor([{"present": 1}] if row else [])
        if normalized.startswith("update sqag_pricing_references"):
            payload_json, updated_at, workspace_id, reference_id = params
            row = self.pricing_references.get((workspace_id, reference_id))
            if not row:
                return FakeLivePostgresCursor(rowcount=0)
            row.update({"payload_json": payload_json, "updated_at": updated_at})
            return FakeLivePostgresCursor(rowcount=1)
        if normalized.startswith("delete from sqag_pricing_references"):
            workspace_id, reference_id = params[:2]
            existed = self.pricing_references.pop((workspace_id, reference_id), None) is not None
            return FakeLivePostgresCursor(rowcount=1 if existed else 0)
        if normalized.startswith("insert into sqag_quote_sessions"):
            workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at = params
            key = (workspace_id, session_id)
            if key in self.quote_sessions:
                raise RuntimeError("synthetic session collision")
            self.quote_sessions[key] = {
                "metadata_json": metadata_json,
                "draft_files_json": draft_files_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakeLivePostgresCursor(rowcount=1)
        if normalized.startswith("select metadata_json, draft_files_json from sqag_quote_sessions"):
            workspace_id, session_id = params[:2]
            row = self.quote_sessions.get((workspace_id, session_id))
            return FakeLivePostgresCursor([row] if row else [])
        if normalized.startswith("select metadata_json from sqag_quote_sessions"):
            workspace_id = params[0]
            if "session_id = ?" in normalized:
                row = self.quote_sessions.get((workspace_id, params[1]))
                return FakeLivePostgresCursor([row] if row else [])
            return FakeLivePostgresCursor(
                self._rows_for_workspace(self.quote_sessions, workspace_id)
            )
        if normalized.startswith("select 1 from sqag_quote_sessions"):
            row = self.quote_sessions.get((params[0], params[1]))
            return FakeLivePostgresCursor([{"present": 1}] if row else [])
        if normalized.startswith("select session_id from sqag_quote_sessions"):
            workspace_id, session_id = params[:2]
            row = self.quote_sessions.get((workspace_id, session_id))
            return FakeLivePostgresCursor([{"session_id": session_id}] if row else [])
        if normalized.startswith("update sqag_quote_sessions"):
            metadata_json, updated_at, workspace_id, session_id = params
            row = self.quote_sessions.get((workspace_id, session_id))
            if not row:
                return FakeLivePostgresCursor(rowcount=0)
            row.update({"metadata_json": metadata_json, "updated_at": updated_at})
            return FakeLivePostgresCursor(rowcount=1)
        if normalized.startswith("delete from sqag_quote_sessions"):
            workspace_id, session_id = params[:2]
            existed = self.quote_sessions.pop((workspace_id, session_id), None) is not None
            return FakeLivePostgresCursor(rowcount=1 if existed else 0)
        if normalized.startswith("insert into sqag_object_artifacts"):
            fields = tuple(verifier.OBJECT_ARTIFACT_COLUMNS)
            row = dict(zip(fields, params))
            key = (row["workspace_id"], row["owner_type"], row["owner_id"], row["artifact_kind"])
            if key in self.object_artifacts:
                raise RuntimeError("synthetic object collision")
            self.object_artifacts[key] = row
            self.object_artifact_insert_snapshots.append(dict(row))
            return FakeLivePostgresCursor(rowcount=1)
        if normalized.startswith("select ") and "from sqag_object_artifacts" in normalized:
            return FakeLivePostgresCursor(self._select_object_rows(normalized, params))
        if normalized.startswith("update sqag_object_artifacts"):
            if self.fail_object_cleanup:
                raise RuntimeError("synthetic cleanup failed")
            artifact_id = params[5]
            workspace_id = params[4]
            row = next(
                (
                    value
                    for value in self.object_artifacts.values()
                    if value["workspace_id"] == workspace_id
                    and value["artifact_id"] == artifact_id
                ),
                None,
            )
            if not row:
                return FakeLivePostgresCursor(rowcount=0)
            expected_values = {
                "owner_type": params[8],
                "owner_id": params[9],
                "artifact_kind": params[10],
                "filename": params[11],
                "content_type": params[12],
                "object_provider_type": params[13],
                "platform_user_id": params[14],
                "session_id": params[15],
                "job_id": params[16],
                "object_key_ref": params[17],
                "checksum_sha256": params[18],
                "size_bytes": params[19],
                "created_at": params[20],
                "updated_at": params[21],
            }
            if any(row[field] != value for field, value in expected_values.items()):
                return FakeLivePostgresCursor(rowcount=0)
            if row["status"] != "active" or row["retention_status"] != "active" or row["deleted_at"] is not None:
                return FakeLivePostgresCursor(rowcount=0)
            if self.tombstone_rowcount is not None:
                return FakeLivePostgresCursor(rowcount=self.tombstone_rowcount)
            self._tombstone_before = dict(row)
            row.update(
                {
                    "status": params[0],
                    "retention_status": params[1],
                    "updated_at": params[2],
                    "deleted_at": params[3],
                }
            )
            self._tombstone_pending = True
            return FakeLivePostgresCursor(rowcount=1)
        if normalized.startswith("delete from sqag_object_artifacts"):
            raise AssertionError("forbidden object-artifact DELETE")
        raise AssertionError(f"unknown SQL operation: {normalized}")

    def commit(self):
        if self._tombstone_pending and self.fail_tombstone_commit:
            raise RuntimeError("private tombstone commit canary")
        self.commits += 1
        if self._tombstone_pending:
            self._tombstone_pending = False
            self._tombstone_committed = True
            self._tombstone_before = None

    def rollback(self):
        if self._tombstone_pending and self._tombstone_before is not None:
            artifact_id = self._tombstone_before["artifact_id"]
            for key, row in self.object_artifacts.items():
                if row["artifact_id"] == artifact_id:
                    self.object_artifacts[key] = dict(self._tombstone_before)
                    break
        self._tombstone_pending = False
        self._tombstone_before = None


class FakeLivePostgresContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, _exc_type, _exc, _tb):
        return False


def run_live_database_report(connection: FakeLivePostgresConnection):
    with mock.patch(
        "verify_production_database_provider.webapp.postgres_storage_connection",
        return_value=FakeLivePostgresContext(connection),
    ):
        return verifier.run_verification(
            env={
                "SQAG_DATABASE_URL": POSTGRES_URL,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            driver_available=True,
        )

class ProductionDatabaseProviderVerifierTest(unittest.TestCase):
    def live_schema_report(self, missing_columns=None):
        runtime_schema = schema_status_for_runtime_columns(missing_columns)
        return verifier.run_verification(
            env={
                "SQAG_DATABASE_URL": POSTGRES_URL,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            driver_available=True,
            schema_validator=lambda _database_url: runtime_schema,
            live_operations_validator=lambda _database_url: {
                "workspace_count": 2,
                "insert_count": 8,
                "read_count": 16,
                "update_count": 3,
                "delete_count": 3,
                "workspace_isolation": True,
                "crud_verified": True,
                "object_artifact_metadata_pairing": True,
                "cleanup_completed": True,
                "db_blob_artifact_rows_written": 0,
            },
        )

    def _database_storage(self, connection, workspace_id):
        return verifier.webapp.DatabaseSqagStorage(
            POSTGRES_URL,
            workspace_id,
            role="admin",
            user_id=workspace_id + "-user",
            expected_session_role=verifier.webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )

    def _seed_object_row(self, connection, row):
        key = (
            row["workspace_id"],
            row["owner_type"],
            row["owner_id"],
            row["artifact_kind"],
        )
        connection.object_artifacts[key] = dict(row)

    def _tombstone_row(self, connection, expected):
        storage = self._database_storage(connection, expected["workspace_id"])
        patcher = mock.patch.object(
            verifier.webapp,
            "postgres_storage_connection",
            return_value=FakeLivePostgresContext(connection),
        )
        patcher.start()
        try:
            return verifier._tombstone_synthetic_object_artifact(storage, expected)
        finally:
            patcher.stop()

    def test_runtime_acl_and_fake_sql_reject_object_artifact_delete(self):
        contract = json.loads(
            (ROOT / "docs" / "runtime-privilege-contract.json").read_text(
                encoding="utf-8"
            )
        )
        privileges = set(
            contract["runtime_tables"]["sqag_object_artifacts"]["privileges"]
        )
        self.assertEqual(privileges, {"SELECT", "INSERT", "UPDATE"})
        connection = FakeLivePostgresConnection()
        with self.assertRaises(AssertionError):
            connection.execute("delete from sqag_object_artifacts where workspace_id = ?", ("w",))
        with self.assertRaises(AssertionError):
            connection.execute("vacuum sqag_object_artifacts")

    def test_test_injected_results_cannot_masquerade_as_live_evidence(self):
        report = verifier.run_verification(
            env={
                "SQAG_DATABASE_URL": POSTGRES_URL,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            driver_available=True,
            schema_validator=lambda _database_url: {"schema_available": True},
            live_operations_validator=lambda _database_url: {
                "workspace_isolation": True,
                "crud_verified": True,
                "object_artifact_metadata_pairing": True,
                "cleanup_completed": True,
                "test_injected_backend": True,
            },
        )
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["test_injected_backend"])
        self.assertIn("test_injected_backend_not_live_evidence", report["blockers"])
        self.assertFalse(report["live_database_evidence_supported"])

    def test_unobserved_and_error_states_use_not_verified_blockers(self):
        report = verifier.run_verification(
            env={
                "SQAG_DATABASE_URL": POSTGRES_URL,
                "SQAG_LIVE_DATABASE_EVIDENCE": "1",
            },
            driver_available=True,
            schema_validator=lambda _database_url: {"schema_available": True},
            live_operations_validator=lambda _database_url: {
                "observation_states": {
                    "workspace_isolation": "not_observed",
                    "metadata_crud": "error",
                    "object_artifact_metadata_pairing": "passed",
                    "cleanup": "error",
                },
                "operational_exception": True,
            },
        )
        self.assertIn("postgres_workspace_isolation_not_verified", report["blockers"])
        self.assertIn("postgres_metadata_crud_not_verified", report["blockers"])
        self.assertNotIn("postgres_object_artifact_metadata_not_verified", report["blockers"])
        self.assertIn("postgres_cleanup_failed", report["blockers"])
        self.assertIn("postgres_live_metadata_operations_failed", report["blockers"])

    def test_tombstone_transition_is_terminal_snapshot_guarded_and_idempotent(self):
        connection = FakeLivePostgresConnection()
        ids = verifier._synthetic_ids()
        expected = verifier._synthetic_object_artifact_spec(ids, "a")
        self._seed_object_row(connection, expected)

        self.assertEqual(self._tombstone_row(connection, expected), ("passed", 1))
        row = next(iter(connection.object_artifacts.values()))
        self.assertEqual(row["status"], "deleted")
        self.assertEqual(row["retention_status"], "deleted")
        self.assertTrue(row["deleted_at"])
        self.assertEqual(row["updated_at"], row["deleted_at"])
        for field in verifier.OBJECT_ARTIFACT_IMMUTABLE_COLUMNS:
            self.assertEqual(row[field], expected[field])

        self.assertEqual(self._tombstone_row(connection, expected), ("passed", 0))
        self.assertEqual(connection.commits, 1)

    def test_tombstone_rejects_collisions_malformed_state_and_snapshot_mismatch(self):
        ids = verifier._synthetic_ids()
        expected = verifier._synthetic_object_artifact_spec(ids, "a")
        mutations = (
            ("wrong token prefix", {"artifact_id": f"{ids['prefix']}-other-run-artifact"}),
            ("wrong side", {"owner_id": ids["session_b"]}),
            ("misleading prefix", {"artifact_id": "sqagldb-misleading-artifact"}),
            ("immutable change", {"checksum_sha256": "b" * 64}),
            ("snapshot mismatch", {"updated_at": "2026-01-02T00:00:00Z"}),
            ("malformed lifecycle", {"retention_status": "pending_delete"}),
        )
        for name, changes in mutations:
            with self.subTest(name=name):
                connection = FakeLivePostgresConnection()
                row = dict(expected)
                row.update(changes)
                self._seed_object_row(connection, row)
                state, count = self._tombstone_row(connection, expected)
                self.assertEqual(state, "failed")
                self.assertEqual(count, 0)
                self.assertEqual(next(iter(connection.object_artifacts.values()))["status"], row["status"])

    def test_tombstone_rowcount_commit_and_post_commit_read_fail_closed(self):
        ids = verifier._synthetic_ids()
        expected = verifier._synthetic_object_artifact_spec(ids, "a")

        wrong_count_connection = FakeLivePostgresConnection(tombstone_rowcount=2)
        self._seed_object_row(wrong_count_connection, expected)
        self.assertEqual(
            self._tombstone_row(wrong_count_connection, expected),
            ("error", 0),
        )
        self.assertEqual(
            next(iter(wrong_count_connection.object_artifacts.values()))["status"],
            "active",
        )

        commit_connection = FakeLivePostgresConnection(fail_tombstone_commit=True)
        self._seed_object_row(commit_connection, expected)
        self.assertEqual(self._tombstone_row(commit_connection, expected), ("error", 0))
        self.assertEqual(
            next(iter(commit_connection.object_artifacts.values()))["status"],
            "active",
        )

        read_connection = FakeLivePostgresConnection(fail_terminal_read=True)
        self._seed_object_row(read_connection, expected)
        self.assertEqual(self._tombstone_row(read_connection, expected), ("error", 0))
        self.assertEqual(
            next(iter(read_connection.object_artifacts.values()))["status"],
            "deleted",
        )

    def test_constructor_failure_is_sanitized_and_unobserved(self):
        connection = FakeLivePostgresConnection()
        with mock.patch.object(
            verifier.webapp.DatabaseSqagStorage,
            "__init__",
            side_effect=RuntimeError("private constructor canary"),
        ):
            report = run_live_database_report(connection)
        text = json.dumps(report, sort_keys=True)
        self.assertIn("postgres_live_metadata_operations_failed", report["blockers"])
        self.assertIn("postgres_workspace_isolation_not_verified", report["blockers"])
        self.assertIn("postgres_metadata_crud_not_verified", report["blockers"])
        self.assertIn("postgres_object_artifact_metadata_not_verified", report["blockers"])
        self.assertIn("postgres_cleanup_failed", report["blockers"])
        self.assertNotIn("private constructor canary", text)

    def test_missing_database_url_fails_closed(self):
        report = verifier.run_verification(env={}, driver_available=False)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["database_family"], "missing")
        self.assertIn("database_url_missing", report["blockers"])
        self.assertFalse(report["production_database_evidence_supported"])
        self.assertFalse(report["connection_attempted"])

    def test_sqlite_database_url_is_local_uat_only(self):
        report = verifier.run_verification(
            env={"SQAG_DATABASE_URL": "sqlite:///tmp/sqag-storage.sqlite3"},
            driver_available=False,
        )

        self.assertEqual(report["database_family"], "sqlite")
        self.assertIn("sqlite_not_final_production", report["blockers"])
        self.assertFalse(report["production_database_evidence_supported"])

    def test_unsupported_database_scheme_fails_closed(self):
        unsupported_url = "my" + "sql://redacted-db-url"
        report = verifier.run_verification(
            env={"SQAG_DATABASE_URL": unsupported_url},
            driver_available=True,
        )

        text = json.dumps(report, sort_keys=True)
        self.assertEqual(report["database_family"], "unsupported")
        self.assertIn("database_url_scheme_unsupported", report["blockers"])
        self.assertNotIn(unsupported_url, text)

    def test_postgres_compatible_url_reports_adapter_available_without_live_evidence(self):
        report = verifier.run_verification(
            env={
                "SQAG_DATABASE_URL": POSTGRES_URL,
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
        for db_blob_table in verifier.webapp.SQAG_DATABASE_ARTIFACT_REQUIRED_COLUMNS:
            if db_blob_table == "sqag_quote_publication_versions":
                self.assertIn(db_blob_table, verifier.REQUIRED_METADATA_TABLES)
            else:
                self.assertNotIn(db_blob_table, verifier.REQUIRED_METADATA_TABLES)

    def test_metadata_migration_status_uses_runtime_required_columns(self):
        status = verifier.metadata_migration_status()

        self.assertTrue(status["metadata_tables_declared"])
        self.assertEqual(status["missing_tables"], [])
        self.assertEqual(status["missing_columns"], {})
        self.assertFalse(status["db_blob_tables_required_for_production"])
        self.assertIn("created_at", verifier.REQUIRED_METADATA_TABLES["sqag_profiles"])
        self.assertIn("updated_at", verifier.REQUIRED_METADATA_TABLES["sqag_quote_sessions"])
        self.assertIn("platform_user_id", verifier.REQUIRED_METADATA_TABLES["sqag_object_artifacts"])
        self.assertIn("deleted_at", verifier.REQUIRED_METADATA_TABLES["sqag_object_artifacts"])

    def test_live_schema_check_passes_with_all_runtime_required_metadata_columns(self):
        report = schema_status_for_runtime_columns()

        self.assertTrue(report["schema_available"])
        self.assertEqual(report["missing_tables"], [])
        self.assertEqual(report["missing_columns"], {})
        self.assertNotIn("sqag_quote_artifacts", report["required_tables"])

    def test_live_opt_in_validates_all_runtime_required_metadata_columns(self):
        report = self.live_schema_report()

        text = json.dumps(report, sort_keys=True)
        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["connection_attempted"])
        self.assertTrue(report["production_database_evidence_supported"])
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["runtime_schema"]["missing_columns"], {})
        self.assertIn("sqag_profiles", report["runtime_schema"]["required_tables"])
        self.assertIn("sqag_object_artifacts", report["runtime_schema"]["required_tables"])
        self.assertNotIn("sqag_quote_artifacts", report["runtime_schema"]["required_tables"])
        self.assertNotIn(POSTGRES_URL, text)

    def test_live_schema_check_fails_when_runtime_required_columns_are_missing(self):
        cases = (
            ("sqag_profiles", "created_at"),
            ("sqag_quote_sessions", "updated_at"),
            ("sqag_object_artifacts", "platform_user_id"),
            ("sqag_object_artifacts", "deleted_at"),
        )
        for table, column in cases:
            with self.subTest(table=table, column=column):
                report = schema_status_for_runtime_columns({table: {column}})

                self.assertFalse(report["schema_available"])
                self.assertIn(column, report["missing_columns"][table])

    def test_live_opt_in_fails_when_runtime_required_metadata_columns_are_missing(self):
        cases = (
            ("sqag_profiles", "created_at"),
            ("sqag_quote_sessions", "updated_at"),
            ("sqag_object_artifacts", "platform_user_id"),
            ("sqag_object_artifacts", "deleted_at"),
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
        create table if not exists sqag_profiles (
          workspace_id text not null,
          profile_id text not null,
          payload_json text not null,
          updated_at text not null
        );
        create table if not exists sqag_pricing_references (
          workspace_id text not null,
          reference_id text not null,
          payload_json text not null,
          created_at text not null,
          updated_at text not null
        );
        create table if not exists sqag_quote_sessions (
          workspace_id text not null,
          session_id text not null,
          metadata_json text not null,
          draft_files_json text not null,
          created_at text not null,
          updated_at text not null
        );
        create table if not exists sqag_object_artifacts (
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
        create table if not exists sqag_quote_publication_versions (
          workspace_id text not null,
          session_id text not null,
          run_id text not null,
          job_id text,
          state text not null,
          artifact_storage_mode text not null,
          artifact_source text not null,
          metadata_json text not null,
          error_code text,
          created_at text not null,
          updated_at text not null,
          promoted_at text,
          failed_at text,
          retention_expires_at text not null,
          original_retention_expires_at text not null,
          legal_hold integer not null,
          deletion_state text not null,
          deletion_error_code text,
          deletion_claimed_at text
        );

        """
        status = migration_status_for_sql(sql)

        self.assertFalse(status["metadata_tables_declared"])
        self.assertEqual(status["missing_columns"], {"sqag_profiles": ["created_at"]})

    def test_live_opt_in_runs_synthetic_metadata_crud_isolation_and_cleanup(self):
        connection = FakeLivePostgresConnection()
        report = run_live_database_report(connection)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["connection_attempted"])
        self.assertFalse(report["test_injected_backend"])
        self.assertTrue(report["live_database_evidence_supported"])
        self.assertTrue(report["production_database_evidence_supported"])
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["live_metadata_operations"]["workspace_count"], 2)
        self.assertTrue(report["live_metadata_operations"]["workspace_isolation"])
        self.assertTrue(report["live_metadata_operations"]["crud_verified"])
        self.assertTrue(report["live_metadata_operations"]["object_artifact_metadata_pairing"])
        self.assertTrue(report["live_metadata_operations"]["cleanup_completed"])
        self.assertEqual(report["live_metadata_operations"]["observation_states"], {
            "workspace_isolation": "passed",
            "metadata_crud": "passed",
            "object_artifact_metadata_pairing": "passed",
            "cleanup": "passed",
        })
        self.assertEqual(report["live_metadata_operations"]["delete_count"], 6)
        self.assertEqual(report["live_metadata_operations"]["ordinary_delete_count"], 6)
        self.assertEqual(report["live_metadata_operations"]["object_artifact_tombstone_count"], 2)
        self.assertEqual(report["live_metadata_operations"]["db_blob_artifact_rows_written"], 0)
        self.assertFalse(connection.profiles)
        self.assertFalse(connection.pricing_references)
        self.assertFalse(connection.quote_sessions)
        self.assertEqual(len(connection.object_artifacts), 2)
        original_rows = {
            row["artifact_id"]: row
            for row in connection.object_artifact_insert_snapshots
        }
        for row in connection.object_artifacts.values():
            self.assertEqual(row["status"], "deleted")
            self.assertEqual(row["retention_status"], "deleted")
            self.assertTrue(row["deleted_at"])
            self.assertEqual(row["updated_at"], row["deleted_at"])
            self.assertEqual(
                {
                    field: row[field]
                    for field in verifier.OBJECT_ARTIFACT_IMMUTABLE_COLUMNS
                },
                {
                    field: original_rows[row["artifact_id"]][field]
                    for field in verifier.OBJECT_ARTIFACT_IMMUTABLE_COLUMNS
                },
            )
        self.assertNotIn(POSTGRES_URL, text)
        self.assertNotIn("redacted-object-key-ref", text)
        self.assertNotIn("content_blob", "\n".join(query.lower() for query, _params in connection.queries))

    def test_live_opt_in_does_not_touch_object_storage_when_object_mode_configured(self):
        connection = FakeLivePostgresConnection()
        backend = mock.Mock()
        backend.delete_artifact.side_effect = AssertionError("object backend delete must not be called")

        with mock.patch.dict(verifier.os.environ, {"SQAG_ARTIFACT_STORAGE_MODE": "object"}, clear=False), \
            mock.patch.object(verifier.webapp, "configured_object_storage_backend", side_effect=AssertionError("object backend factory must not be called")) as configured_backend, \
            mock.patch.object(verifier.webapp.DatabaseSqagStorage, "tombstone_object_quote_artifacts", side_effect=AssertionError("object tombstone must not be called")) as tombstone, \
            mock.patch.object(verifier.webapp.DatabaseSqagStorage, "delete_quote_session", side_effect=AssertionError("runtime quote-session delete must not be called")) as delete_session:
            report = run_live_database_report(connection)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["live_database_evidence_supported"])
        configured_backend.assert_not_called()
        tombstone.assert_not_called()
        delete_session.assert_not_called()
        backend.delete_artifact.assert_not_called()

    def test_live_opt_in_workspace_isolation_failure_fails_closed(self):
        report = run_live_database_report(FakeLivePostgresConnection(leak_workspace_reads=True))

        self.assertEqual(report["status"], "failed")
        self.assertIn("postgres_workspace_isolation_failed", report["blockers"])
        self.assertFalse(report["live_database_evidence_supported"])
        self.assertFalse(report["production_database_evidence_supported"])

    def test_live_opt_in_cleanup_failure_fails_closed_without_private_values(self):
        connection = FakeLivePostgresConnection(fail_object_cleanup=True)
        report = run_live_database_report(connection)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("postgres_cleanup_failed", report["blockers"])
        self.assertFalse(report["live_database_evidence_supported"])
        self.assertFalse(report["production_database_evidence_supported"])
        self.assertFalse(report["live_metadata_operations"]["cleanup_completed"])
        self.assertEqual(
            report["live_metadata_operations"]["observation_states"],
            {
                "workspace_isolation": "passed",
                "metadata_crud": "passed",
                "object_artifact_metadata_pairing": "passed",
                "cleanup": "error",
            },
        )
        self.assertIn("postgres_live_metadata_operations_failed", report["blockers"])
        self.assertNotIn(POSTGRES_URL, text)
        self.assertNotIn("synthetic cleanup failed", text)

    def test_live_opt_in_connection_failure_is_sanitized(self):
        def fail_schema(_database_url):
            raise RuntimeError("private connection details must not leak")

        report = verifier.run_verification(
            env={
                "SQAG_DATABASE_URL": POSTGRES_URL,
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
                "SQAG_DATABASE_URL": private_url,
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
