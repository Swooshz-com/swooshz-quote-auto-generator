import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webapp import server as webapp


POSTGRES_URL = "postgres" + "ql://redacted-db-url"


def platform_session(workspace_id: str = "workspace-alpha", user_id: str = "user-alpha") -> dict:
    return {
        "user": {
            "subject": user_id,
            "platform": {
                "outcome": "consumed",
                "user": {"userId": user_id},
                "workspace": {"workspaceId": workspace_id},
                "app": {"appKey": "kqag"},
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
        "kqag_profiles",
        "kqag_pricing_references",
        "kqag_quote_sessions",
        "kqag_object_artifacts",
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
        if "information_schema.columns" in normalized:
            rows = []
            column_map = {
                "kqag_profiles": {"workspace_id", "profile_id", "payload_json", "created_at", "updated_at"},
                "kqag_pricing_references": {"workspace_id", "reference_id", "payload_json", "created_at", "updated_at"},
                "kqag_quote_sessions": {"workspace_id", "session_id", "metadata_json", "draft_files_json", "created_at", "updated_at"},
                "kqag_object_artifacts": {
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
            }
            for table in sorted(self.required_tables - self.missing_tables):
                if table not in params:
                    continue
                rows.extend({"table_name": table, "column_name": column} for column in sorted(column_map[table]))
            return FakePostgresCursor(rows)
        if normalized.startswith("insert into kqag_profiles"):
            workspace_id, profile_id, payload_json, created_at, updated_at = params
            self.profiles[(workspace_id, profile_id)] = {
                "payload_json": payload_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakePostgresCursor(rowcount=1)
        if normalized.startswith("select payload_json from kqag_profiles"):
            workspace_id = params[0]
            rows = [
                {"payload_json": value["payload_json"]}
                for (stored_workspace, _profile_id), value in sorted(self.profiles.items())
                if stored_workspace == workspace_id
            ]
            return FakePostgresCursor(rows)
        if normalized.startswith("insert into kqag_pricing_references"):
            workspace_id, reference_id, payload_json, created_at, updated_at = params
            self.pricing_references[(workspace_id, reference_id)] = {
                "payload_json": payload_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakePostgresCursor(rowcount=1)
        if normalized.startswith("insert into kqag_quote_sessions"):
            workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at = params
            self.quote_sessions[(workspace_id, session_id)] = {
                "metadata_json": metadata_json,
                "draft_files_json": draft_files_json,
                "created_at": created_at,
                "updated_at": updated_at,
            }
            return FakePostgresCursor(rowcount=1)
        if normalized.startswith("select metadata_json from kqag_quote_sessions where workspace_id = %s"):
            workspace_id = params[0]
            rows = [
                {"metadata_json": value["metadata_json"]}
                for (stored_workspace, _session_id), value in sorted(self.quote_sessions.items())
                if stored_workspace == workspace_id
            ]
            return FakePostgresCursor(rows)
        if "from kqag_quote_sessions where workspace_id = %s and session_id = %s" in normalized:
            workspace_id, session_id = params
            row = self.quote_sessions.get((workspace_id, session_id))
            return FakePostgresCursor([row] if row else [])
        if normalized.startswith("delete from kqag_quote_sessions"):
            workspace_id, session_id = params
            existed = self.quote_sessions.pop((workspace_id, session_id), None) is not None
            return FakePostgresCursor(rowcount=1 if existed else 0)
        if normalized.startswith("insert into kqag_object_artifacts"):
            return FakePostgresCursor(rowcount=1)
        if "from kqag_object_artifacts where workspace_id = %s" in normalized:
            return FakePostgresCursor()
        return FakePostgresCursor()

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class PostgresMetadataStorageTest(unittest.TestCase):
    def postgres_env(self) -> dict[str, str]:
        return {
            "KQAG_STORAGE_MODE": "database",
            "KQAG_ARTIFACT_STORAGE_MODE": "object",
            "KQAG_DATABASE_URL": POSTGRES_URL,
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
        self.assertIn("insert into kqag_pricing_references", executed_sql)
        self.assertIn("insert into kqag_quote_sessions", executed_sql)
        self.assertIn("insert into kqag_object_artifacts", executed_sql)
        self.assertNotIn("content_blob", executed_sql)

    def test_postgres_storage_blocks_missing_driver_without_sqlite_fallback(self):
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            side_effect=webapp.KqagStorageAccessError(
                "KQAG Postgres database driver is not available.",
                status=503,
                reason="storage_postgres_driver_unavailable",
            ),
        ), mock.patch("webapp.server.sqlite_storage_connection", side_effect=AssertionError("sqlite fallback used")):
            with self.assertRaises(webapp.KqagStorageAccessError) as blocked:
                webapp.app_storage_for_auth_session(platform_session())

        self.assertEqual(blocked.exception.reason, "storage_postgres_driver_unavailable")

    def test_postgres_storage_blocks_connection_failure_without_private_values(self):
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: (_ for _ in ()).throw(RuntimeError("private host value")),
        ):
            with self.assertRaises(webapp.KqagStorageAccessError) as blocked:
                webapp.app_storage_for_auth_session(platform_session())

        text = json.dumps({"message": str(blocked.exception), "reason": blocked.exception.reason})
        self.assertEqual(blocked.exception.reason, "storage_postgres_connection_failed")
        self.assertNotIn(POSTGRES_URL, text)
        self.assertNotIn("private host value", text)

    def test_postgres_storage_blocks_missing_required_schema(self):
        connection = FakePostgresConnection(missing_tables={"kqag_quote_sessions"})
        with mock.patch.dict(os.environ, self.postgres_env(), clear=True), mock.patch(
            "webapp.server.postgres_driver_connection_factory",
            return_value=lambda _database_url: connection,
        ):
            with self.assertRaises(webapp.KqagStorageAccessError) as blocked:
                webapp.app_storage_for_auth_session(platform_session())

        self.assertEqual(blocked.exception.reason, "storage_database_not_migrated")


if __name__ == "__main__":
    unittest.main()
