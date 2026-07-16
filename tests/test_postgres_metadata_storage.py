import json
import os
import sqlite3
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp import server as webapp


POSTGRES_URL = "postgres" + "ql://redacted-db-url"
LEGACY_PREFIX = "k" + "qag"
SQAG_TABLES = {
    "sqag_profiles",
    "sqag_pricing_references",
    "sqag_quote_sessions",
    "sqag_quote_artifacts",
    "sqag_quote_publication_versions",
    "sqag_file_artifacts",
    "sqag_object_artifacts",
}


def platform_session(workspace_id: str = "workspace-alpha", user_id: str = "user-alpha") -> dict:
    return {
        "user": {
            "subject": user_id,
            "platform": {
                "outcome": "consumed",
                "user": {"userId": user_id},
                "workspace": {"workspaceId": workspace_id},
                "app": {"appKey": "sqag"},
                "membershipRole": "owner",
            },
        }
    }


class FakePostgresCursor:
    def __init__(self, rows=None, rowcount: int = 0):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakePostgresConnection:
    required_tables = {
        "sqag_profiles",
        "sqag_pricing_references",
        "sqag_quote_publication_versions",
        "sqag_quote_sessions",
        "sqag_object_artifacts",
    }

    def __init__(self, *, missing_tables=None):
        self.closed = False
        self.commits = 0
        self.queries = []
        self.missing_tables = set(missing_tables or [])
        self.profiles = {}
        self.pricing_references = {}
        self.quote_sessions = {}
        self.object_artifacts = {}

    def execute(self, sql, params=None):
        params = tuple(params or ())
        self.queries.append((sql, params))
        normalized = " ".join(sql.lower().split())
        if "pg_try_advisory_xact_lock" in normalized:
            return FakePostgresCursor([{"lock_acquired": True}])
        if "information_schema.columns" in normalized:
            rows = []
            column_map = {
                "sqag_profiles": {"workspace_id", "profile_id", "payload_json", "created_at", "updated_at"},
                "sqag_pricing_references": {"workspace_id", "reference_id", "payload_json", "created_at", "updated_at"},
                "sqag_quote_sessions": {"workspace_id", "session_id", "metadata_json", "draft_files_json", "created_at", "updated_at"},
                "sqag_object_artifacts": {
                    "artifact_id",
                    "workspace_id",
                    "owner_type",
                    "owner_id",
                    "platform_user_id",
                    "session_id",
                    "job_id",
                    "artifact_kind",
                    "filename",
                    "content_type",
                    "size_bytes",
                    "checksum_sha256",
                    "object_provider_type",
                    "object_key_ref",
                    "status",
                    "retention_status",
                    "created_at",
                    "updated_at",
                    "deleted_at",
                },
                "sqag_quote_publication_versions": webapp.SQAG_PUBLICATION_VERSION_REQUIRED_COLUMNS,
            }
            for table in sorted(self.required_tables - self.missing_tables):
                if table not in params:
                    continue
                rows.extend({"table_name": table, "column_name": column} for column in sorted(column_map[table]))
            return FakePostgresCursor(rows)
        if normalized.startswith("insert into sqag_profiles"):
            workspace_id, profile_id, payload_json, created_at, updated_at = params
            self.profiles[(workspace_id, profile_id)] = {
                "payload_json": payload_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakePostgresCursor(rowcount=1)
        if normalized.startswith("select payload_json from sqag_profiles"):
            workspace_id = params[0]
            rows = [
                {"payload_json": value["payload_json"]}
                for (stored_workspace, _profile_id), value in sorted(self.profiles.items())
                if stored_workspace == workspace_id
            ]
            return FakePostgresCursor(rows)
        if normalized.startswith("insert into sqag_pricing_references"):
            workspace_id, reference_id, payload_json, created_at, updated_at = params
            self.pricing_references[(workspace_id, reference_id)] = {
                "payload_json": payload_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakePostgresCursor(rowcount=1)
        if normalized.startswith("insert into sqag_quote_sessions"):
            workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at = params
            self.quote_sessions[(workspace_id, session_id)] = {
                "metadata_json": metadata_json,
                "draft_files_json": draft_files_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakePostgresCursor(rowcount=1)
        if normalized.startswith("select metadata_json from sqag_quote_sessions where workspace_id = %s"):
            workspace_id = params[0]
            rows = [
                {"metadata_json": value["metadata_json"]}
                for (stored_workspace, _session_id), value in sorted(self.quote_sessions.items())
                if stored_workspace == workspace_id
            ]
            return FakePostgresCursor(rows)
        if "from sqag_quote_sessions where workspace_id = %s and session_id = %s" in normalized:
            workspace_id, session_id = params
            row = self.quote_sessions.get((workspace_id, session_id))
            return FakePostgresCursor([row] if row else [])
        if normalized.startswith("delete from sqag_quote_sessions"):
            workspace_id, session_id = params
            existed = self.quote_sessions.pop((workspace_id, session_id), None) is not None
            return FakePostgresCursor(rowcount=1 if existed else 0)
        if normalized.startswith("insert into sqag_object_artifacts"):
            (
                artifact_id,
                workspace_id,
                owner_type,
                owner_id,
                platform_user_id,
                session_id,
                job_id,
                artifact_kind,
                filename,
                content_type,
                size_bytes,
                checksum_sha256,
                object_provider_type,
                object_key_ref,
                status,
                retention_status,
                created_at,
                updated_at,
                deleted_at,
            ) = params
            self.object_artifacts[(workspace_id, owner_type, owner_id, artifact_kind)] = {
                "artifact_id": artifact_id,
                "workspace_id": workspace_id,
                "owner_type": owner_type,
                "owner_id": owner_id,
                "platform_user_id": platform_user_id,
                "session_id": session_id,
                "job_id": job_id,
                "artifact_kind": artifact_kind,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "checksum_sha256": checksum_sha256,
                "object_provider_type": object_provider_type,
                "object_key_ref": object_key_ref,
                "status": status,
                "retention_status": retention_status,
                "created_at": created_at,
                "updated_at": updated_at,
                "deleted_at": deleted_at,
            }
            return FakePostgresCursor(rowcount=1)
        if "from sqag_object_artifacts where workspace_id = %s" in normalized:
            workspace_id, owner_type, owner_id, artifact_kind = params[:4]
            status = params[4] if len(params) > 4 else None
            retention_status = params[5] if len(params) > 5 else None
            row = self.object_artifacts.get((workspace_id, owner_type, owner_id, artifact_kind))
            if (
                not row
                or row["deleted_at"] is not None
                or (status is not None and row["status"] != status)
                or (retention_status is not None and row["retention_status"] != retention_status)
            ):
                return FakePostgresCursor()
            return FakePostgresCursor([row])
        return FakePostgresCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None

    def close(self):
        self.closed = True


class RecordingConnection:
    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, tuple(params or ())))
        return FakePostgresCursor()


class SqliteSqagMigrationTest(unittest.TestCase):
    def temp_path(self) -> Path:
        root = ROOT / "_tmp" / "test-postgres-metadata-storage"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"case-{uuid.uuid4().hex}"
        path.mkdir()
        return path

    def sqlite_url(self, path: Path) -> str:
        return f"sqlite:///{path.as_posix()}"

    def table_names(self, database_path: Path) -> set[str]:
        with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                "select name from sqlite_master where type = 'table'"
            ).fetchall()
        return {row[0] for row in rows}

    def test_sqag_storage_migration_creates_sqag_tables(self):
        database_path = self.temp_path() / "sqag-storage.sqlite3"

        webapp.apply_sqag_storage_migrations(self.sqlite_url(database_path))

        tables = self.table_names(database_path)
        self.assertTrue(SQAG_TABLES.issubset(tables))
        self.assertFalse(any(table.startswith(f"{LEGACY_PREFIX}_") for table in tables))

    def test_sqag_storage_migration_renames_existing_legacy_tables(self):
        database_path = self.temp_path() / "sqag-storage.sqlite3"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                f"create table {LEGACY_PREFIX}_profiles (workspace_id text not null, profile_id text not null, payload_json text not null, created_at text not null, updated_at text not null, primary key (workspace_id, profile_id))"
            )
            connection.execute(
                f"insert into {LEGACY_PREFIX}_profiles values (?, ?, ?, ?, ?)",
                ("workspace-a", "profile-a", '{"id":"profile-a"}', "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.commit()

        webapp.apply_sqag_storage_migrations(self.sqlite_url(database_path))

        tables = self.table_names(database_path)
        self.assertIn("sqag_profiles", tables)
        self.assertNotIn(f"{LEGACY_PREFIX}_profiles", tables)
        with sqlite3.connect(database_path) as connection:
            row = connection.execute(
                "select payload_json from sqag_profiles where workspace_id = ? and profile_id = ?",
                ("workspace-a", "profile-a"),
            ).fetchone()
        self.assertEqual(row[0], '{"id":"profile-a"}')

    def test_sqag_storage_migration_merges_legacy_and_new_tables(self):
        database_path = self.temp_path() / "sqag-storage.sqlite3"
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "create table sqag_profiles (workspace_id text not null, profile_id text not null, payload_json text not null, created_at text not null, updated_at text not null, primary key (workspace_id, profile_id))"
            )
            connection.execute(
                f"create table {LEGACY_PREFIX}_profiles (workspace_id text not null, profile_id text not null, payload_json text not null, created_at text not null, updated_at text not null, primary key (workspace_id, profile_id))"
            )
            connection.execute(
                "insert into sqag_profiles values (?, ?, ?, ?, ?)",
                ("workspace-a", "new-profile", '{"id":"new-profile"}', "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.execute(
                f"insert into {LEGACY_PREFIX}_profiles values (?, ?, ?, ?, ?)",
                ("workspace-a", "legacy-profile", '{"id":"legacy-profile"}', "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            connection.commit()

        webapp.apply_sqag_storage_migrations(self.sqlite_url(database_path))

        tables = self.table_names(database_path)
        self.assertIn("sqag_profiles", tables)
        self.assertNotIn(f"{LEGACY_PREFIX}_profiles", tables)
        with sqlite3.connect(database_path) as connection:
            rows = connection.execute(
                "select profile_id from sqag_profiles where workspace_id = ? order by profile_id",
                ("workspace-a",),
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["legacy-profile", "new-profile"])

    def test_sqag_storage_migration_is_idempotent(self):
        database_path = self.temp_path() / "sqag-storage.sqlite3"

        webapp.apply_sqag_storage_migrations(self.sqlite_url(database_path))
        webapp.apply_sqag_storage_migrations(self.sqlite_url(database_path))

        tables = self.table_names(database_path)
        self.assertTrue(SQAG_TABLES.issubset(tables))
        self.assertFalse(any(table.startswith(f"{LEGACY_PREFIX}_") for table in tables))

    def test_postgres_legacy_table_migration_sql_uses_guarded_sqag_rename_merge(self):
        connection = RecordingConnection()

        webapp.migrate_legacy_sqag_tables_postgres(connection)

        executed_sql = "\n".join(query for query, _params in connection.queries)
        self.assertIn("public.sqag_profiles", executed_sql)
        self.assertIn(f"public.{LEGACY_PREFIX}_profiles", executed_sql)
        self.assertIn("on conflict do nothing", executed_sql.lower())
        self.assertIn("drop table", executed_sql.lower())


class PostgresMetadataStorageTest(unittest.TestCase):
    def postgres_env(self) -> dict[str, str]:
        return {
            "SQAG_STORAGE_MODE": "database",
            "SQAG_ARTIFACT_STORAGE_MODE": "object",
            "SQAG_DATABASE_URL": POSTGRES_URL,
        }

    def test_postgres_storage_uses_workspace_scoped_metadata_queries(self):
        connection = FakePostgresConnection()
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: connection,
        ):
            storage = webapp.app_storage_for_auth_session(platform_session("workspace-alpha"))
            saved = storage.save_profile({"id": "profile-a", "label": "Profile A"})
            listed = storage.list_company_profiles()

        self.assertEqual(saved["id"], "profile-a")
        self.assertEqual([item["id"] for item in listed], ["profile-a"])
        self.assertTrue(connection.closed)
        self.assertTrue(
            any("workspace_id = %s" in query and params[0] == "workspace-alpha" for query, params in connection.queries),
            "Postgres metadata queries must bind workspace_id.",
        )
        self.assertFalse(any("sqlite_master" in query.lower() for query, _params in connection.queries))

    def test_postgres_storage_handles_pricing_sessions_and_object_metadata_without_blob_fallback(self):
        connection = FakePostgresConnection()
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: connection,
        ):
            storage = webapp.app_storage_for_auth_session(platform_session("workspace-alpha"))
            storage.save_pricing_reference({"id": "reference-a", "label": "Reference A", "items": []})
            session = storage.create_or_update_quote_session(
                {
                    "session_id": "quote-alpha123",
                    "pricing_reference": {"id": "reference-a", "source": "company"},
                    "quote_company_profile": {"id": "profile-a", "source": "company"},
                },
                session_id="quote-alpha123",
            )
            storage._upsert_object_quote_artifact(
                "quote-alpha123",
                "xlsx",
                "quotation.xlsx",
                webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                webapp.ObjectArtifactMetadata(
                    workspace_id="workspace-alpha",
                    owner_type="generated_quote",
                    owner_id="quote-alpha123",
                    artifact_kind="xlsx",
                    filename="quotation.xlsx",
                    content_type=webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                    size_bytes=12,
                    checksum_sha256="a" * 64,
                    storage_key="redacted-object-key-ref",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
            )

        self.assertEqual(session["session_id"], "quote-alpha123")
        executed_sql = "\n".join(query.lower() for query, _params in connection.queries)
        self.assertIn("insert into sqag_pricing_references", executed_sql)
        self.assertIn("insert into sqag_quote_sessions", executed_sql)
        self.assertIn("insert into sqag_object_artifacts", executed_sql)
        self.assertNotIn("content_blob", executed_sql)

    def test_postgres_object_artifact_upsert_requires_workspace_owner(self):
        connection = FakePostgresConnection()
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: connection,
        ):
            storage = webapp.app_storage_for_auth_session(
                platform_session("workspace-alpha")
            )
            with self.assertRaises(webapp.ObjectStorageContractError):
                storage._upsert_object_quote_artifact(
                    "quote-ownerless123",
                    "xlsx",
                    webapp.QUOTE_SESSION_EXPORT_KINDS["xlsx"],
                    webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                    webapp.ObjectArtifactMetadata(
                        workspace_id="workspace-alpha",
                        owner_type="generated_quote",
                        owner_id="quote-ownerless123",
                        artifact_kind="xlsx",
                        filename=webapp.QUOTE_SESSION_EXPORT_KINDS["xlsx"],
                        content_type=webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                        size_bytes=12,
                        checksum_sha256="a" * 64,
                        storage_key="redacted-object-key-ref",
                        created_at="2026-01-01T00:00:00Z",
                        updated_at="2026-01-01T00:00:00Z",
                    ),
                )

        self.assertFalse(
            any(
                query.lower().startswith("insert into sqag_object_artifacts")
                for query, _params in connection.queries
            )
        )

    def test_postgres_object_artifact_row_matches_runtime_mapping_contract(self):
        connection = FakePostgresConnection()
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: connection,
        ):
            storage = webapp.app_storage_for_auth_session(platform_session("workspace-alpha"))
            storage.create_or_update_quote_session(
                {"session_id": "quote-alpha123"},
                session_id="quote-alpha123",
            )
            storage._upsert_object_quote_artifact(
                "quote-alpha123",
                "xlsx",
                webapp.QUOTE_SESSION_EXPORT_KINDS["xlsx"],
                webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                webapp.ObjectArtifactMetadata(
                    workspace_id="workspace-alpha",
                    owner_type="generated_quote",
                    owner_id="quote-alpha123",
                    artifact_kind="xlsx",
                    filename=webapp.QUOTE_SESSION_EXPORT_KINDS["xlsx"],
                    content_type=webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                    size_bytes=12,
                    checksum_sha256="a" * 64,
                    storage_key="redacted-object-key-ref",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
            )
            row = storage._object_quote_artifact_row("quote-alpha123", "xlsx")

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["workspace_id"], "workspace-alpha")
        self.assertEqual(row["owner_type"], "generated_quote")
        self.assertEqual(row["owner_id"], "quote-alpha123")
        self.assertEqual(row["session_id"], "quote-alpha123")
        self.assertEqual(row["artifact_kind"], "xlsx")
        self.assertEqual(row["filename"], webapp.QUOTE_SESSION_EXPORT_KINDS["xlsx"])
        self.assertEqual(row["content_type"], webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"])
        self.assertEqual(row["size_bytes"], 12)
        self.assertEqual(row["checksum_sha256"], "a" * 64)
        self.assertTrue(row["object_key_ref"])
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["retention_status"], "active")
        self.assertIsNone(row["deleted_at"])

    def test_postgres_object_artifact_row_rejects_noncanonical_runtime_filename(self):
        connection = FakePostgresConnection()
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: connection,
        ):
            storage = webapp.app_storage_for_auth_session(platform_session("workspace-alpha"))
            storage.create_or_update_quote_session(
                {"session_id": "quote-alpha123"},
                session_id="quote-alpha123",
            )
            storage._upsert_object_quote_artifact(
                "quote-alpha123",
                "xlsx",
                "synthetic-live-db-object-restore.xlsx",
                webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                webapp.ObjectArtifactMetadata(
                    workspace_id="workspace-alpha",
                    owner_type="generated_quote",
                    owner_id="quote-alpha123",
                    artifact_kind="xlsx",
                    filename="synthetic-live-db-object-restore.xlsx",
                    content_type=webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                    size_bytes=12,
                    checksum_sha256="a" * 64,
                    storage_key="redacted-object-key-ref",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
            )
            row = storage._object_quote_artifact_row("quote-alpha123", "xlsx")

        self.assertIsNone(row)

    def test_postgres_storage_blocks_missing_driver_without_sqlite_fallback(self):
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            side_effect=webapp.SqagStorageAccessError(
                "SQAG Postgres database driver is not available.",
                status=503,
                reason="storage_postgres_driver_unavailable",
            ),
        ), mock.patch("webapp.server.sqlite_storage_connection", side_effect=AssertionError("sqlite fallback used")):
            with self.assertRaises(webapp.SqagStorageAccessError) as blocked:
                webapp.app_storage_for_auth_session(platform_session())

        self.assertEqual(blocked.exception.reason, "storage_postgres_driver_unavailable")

    def test_postgres_storage_blocks_connection_failure_without_private_values(self):
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: (_ for _ in ()).throw(RuntimeError("private host value")),
        ):
            with self.assertRaises(webapp.SqagStorageAccessError) as blocked:
                webapp.app_storage_for_auth_session(platform_session())

        text = json.dumps({"message": str(blocked.exception), "reason": blocked.exception.reason})
        self.assertEqual(blocked.exception.reason, "storage_postgres_connection_failed")
        self.assertNotIn(POSTGRES_URL, text)
        self.assertNotIn("private host value", text)

    def test_postgres_storage_blocks_missing_required_schema(self):
        connection = FakePostgresConnection(missing_tables={"sqag_quote_sessions"})
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: connection,
        ):
            with self.assertRaises(webapp.SqagStorageAccessError) as blocked:
                webapp.app_storage_for_auth_session(platform_session())

        self.assertEqual(blocked.exception.reason, "storage_database_not_migrated")


if __name__ == "__main__":
    unittest.main()
