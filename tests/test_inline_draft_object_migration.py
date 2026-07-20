import base64
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

from webapp import server as webapp

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "migrate_inline_draft_files_to_object_storage.py"


@contextlib.contextmanager
def writable_test_root():
    root = ROOT / "_tmp" / f"inline-draft-migration-{webapp.secrets.token_hex(6)}"
    root.mkdir(parents=True, exist_ok=False)
    try:
        yield root
    finally:
        shutil.rmtree(root)


def load_migrator():
    spec = importlib.util.spec_from_file_location("migrate_inline_draft_files_to_object_storage", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Inline draft migration script is missing.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InlineDraftObjectMigrationTest(unittest.TestCase):
    def object_env(self, database_url):
        return {
            "APP_MODE": "deploy",
            "SQAG_STORAGE_MODE": "database",
            "SQAG_ARTIFACT_STORAGE_MODE": "object",
            "SQAG_DATABASE_URL": database_url,
            "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
            "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test",
            "SQAG_OBJECT_STORAGE_BUCKET": "synthetic-bucket",
            "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
            "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "synthetic-access-key",
            "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "synthetic-secret-key",
        }

    def database_env(self, database_url):
        return {
            "APP_MODE": "deploy",
            "SQAG_STORAGE_MODE": "database",
            "SQAG_ARTIFACT_STORAGE_MODE": "database",
            "SQAG_DATABASE_URL": database_url,
        }

    def auth_session(self, workspace_id, user_id="migration-user", membership_role="owner"):
        context = webapp.safe_platform_launch_context(
            {
                "outcome": "consumed",
                "user": {
                    "userId": user_id,
                    "email": f"{user_id}@example.test",
                    "displayName": "Synthetic User",
                    "status": "active",
                },
                "workspace": {
                    "workspaceId": workspace_id,
                    "workspaceSlug": workspace_id,
                    "workspaceName": "Synthetic Workspace",
                },
                "app": {"appKey": "sqag", "appName": "SQAG"},
                "membershipRole": membership_role,
                "launchTokenExpiresAt": "2999-01-01T00:00:00.000Z",
            }
        )
        return {
            "user": webapp.user_from_platform_launch_context(context),
            "platform": context,
        }

    def inline_record(self, key="legacy-reference"):
        content = b"synthetic-reference"
        return {
            "session_file_key": key,
            "name": "reference.png",
            "type": "image/png",
            "size": len(content),
            "data_url": "data:image/png;base64," + base64.b64encode(content).decode("ascii"),
        }

    def insert_legacy(self, path, workspace_id, session_id, records, user_id="migration-user"):
        now = webapp.utc_timestamp()
        metadata = webapp.blank_quote_session_metadata(session_id, now)
        metadata["owner"] = {"user_id": user_id}
        with contextlib.closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "insert into sqag_quote_sessions "
                "(workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (
                    workspace_id,
                    session_id,
                    json.dumps(metadata),
                    json.dumps(records),
                    now,
                    now,
                ),
            )
            connection.commit()

    def admin_storage(self, database_url, workspace_id):
        return webapp.DatabaseSqagStorage(
            database_url,
            workspace_id,
            role="admin",
            user_id="inline-draft-recovery",
        )

    def test_object_versioned_combined_mutation_rejects_before_any_mutation(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "quotation.xlsx").write_bytes(b"synthetic-xlsx")
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                owner = webapp.app_storage_for_auth_session(self.auth_session("workspace-combined"))
                session_id = "quote-combined-object"
                owner.create_or_update_quote_session(
                    {
                        "quote_session": {
                            "session_id": session_id,
                            "draft_files": [self.inline_record("old-reference")],
                        }
                    }
                )
                owner.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    {"status": "completed"},
                    output_dir,
                    generation_run_id="run-existing-123",
                    generation_job_id="job-existing-123",
                )
                prior_session = owner.get_quote_session(
                    session_id,
                    include_draft_state=True,
                )
                prior_objects = dict(backend._objects)
                (output_dir / "quotation.xlsx").write_bytes(b"replacement-xlsx")
                payload = {
                    "quote_session": {
                        "session_id": session_id,
                        "draft_files": [self.inline_record("replacement-reference")],
                    }
                }
                with self.assertRaises(webapp.SqagStorageAccessError) as raised:
                    owner.create_or_update_quote_session(
                        payload,
                        {"status": "completed"},
                        output_dir,
                        generation_run_id="run-combined-123",
                        generation_job_id="job-combined-123",
                    )
                self.assertEqual(raised.exception.status, 409)
                self.assertEqual(backend._objects, prior_objects)
                self.assertEqual(
                    owner.get_quote_session(session_id, include_draft_state=True),
                    prior_session,
                )
                with contextlib.closing(sqlite3.connect(root / "sqag.sqlite3")) as connection:
                    self.assertEqual(
                        connection.execute("select count(*) from sqag_quote_sessions").fetchone()[0],
                        1,
                    )
                    self.assertEqual(
                        connection.execute(
                            "select count(*) from sqag_quote_publication_versions"
                        ).fetchone()[0],
                        1,
                    )

    def test_database_versioned_generation_keeps_existing_saved_upload(self):
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "quotation.xlsx").write_bytes(b"synthetic-xlsx")
            upload = self.inline_record()
            with mock.patch.dict(os.environ, self.database_env(database_url)):
                webapp.apply_sqag_storage_migrations(database_url)
                owner = webapp.app_storage_for_auth_session(self.auth_session("workspace-database"))
                owner.create_or_update_quote_session(
                    {
                        "quote_session": {
                            "session_id": "quote-database-version",
                            "draft_files": [upload],
                        }
                    }
                )
                owner.create_or_update_quote_session(
                    {
                        "quote_session": {
                            "session_id": "quote-database-version",
                            "draft_files": [],
                        }
                    },
                    {"status": "completed"},
                    output_dir,
                    generation_run_id="run-database-123",
                    generation_job_id="job-database-123",
                )
                restored = owner.get_quote_session(
                    "quote-database-version",
                    include_draft_state=True,
                )
                self.assertEqual(restored["draft_files"], [upload])

    def test_workspace_scoped_recovery_counts_blank_as_failure_and_is_idempotent(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            db_path = root / "sqag.sqlite3"
            good = self.inline_record()
            blank = {**self.inline_record("blank-reference"), "data_url": ""}
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                self.insert_legacy(db_path, "workspace-recovery", "quote-legacy-good", [good])
                self.insert_legacy(db_path, "workspace-recovery", "quote-legacy-blank", [blank])
                self.insert_legacy(
                    db_path,
                    "workspace-other",
                    "quote-legacy-other",
                    [self.inline_record("other-reference")],
                )
                admin = self.admin_storage(database_url, "workspace-recovery")
                other = self.admin_storage(database_url, "workspace-other")
                self.assertEqual(admin.count_workspace_inline_draft_files_for_object_migration(), 2)
                result = admin.migrate_workspace_inline_draft_files_to_object_storage(limit=10)
                self.assertEqual(
                    (
                        result["candidates"],
                        result["processed"],
                        result["migrated"],
                        result["failed"],
                        result["remaining"],
                    ),
                    (2, 2, 1, 1, 1),
                )
                self.assertEqual(other.count_workspace_inline_draft_files_for_object_migration(), 1)
                retry = admin.migrate_workspace_inline_draft_files_to_object_storage(limit=10)
                self.assertEqual(
                    (retry["migrated"], retry["failed"], retry["remaining"]),
                    (0, 1, 1),
                )
                owner = webapp.app_storage_for_auth_session(self.auth_session("workspace-recovery"))
                restored = owner.get_quote_session(
                    "quote-legacy-good",
                    include_draft_state=True,
                )
                self.assertEqual(restored["draft_files"][0]["data_url"], good["data_url"])

    def test_visible_session_lazy_recovery_is_admin_gated_for_batch_and_compensates(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            db_path = root / "sqag.sqlite3"
            good = self.inline_record()
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                self.insert_legacy(db_path, "workspace-lazy", "quote-lazy-visible", [good])
                owner = webapp.app_storage_for_auth_session(
                    self.auth_session("workspace-lazy", membership_role="member")
                )
                with self.assertRaises(webapp.SqagStorageAccessError) as raised:
                    owner.count_workspace_inline_draft_files_for_object_migration()
                self.assertEqual(raised.exception.status, 403)
                restored = owner.get_quote_session(
                    "quote-lazy-visible",
                    include_draft_state=True,
                )
                self.assertEqual(restored["draft_files"][0]["data_url"], good["data_url"])
                self.insert_legacy(
                    db_path,
                    "workspace-lazy",
                    "quote-compensate",
                    [self.inline_record("compensate-reference")],
                )
                before = dict(backend._objects)
                with mock.patch.object(
                    owner,
                    "_execute_object_artifact_batch_metadata",
                    side_effect=RuntimeError("synthetic metadata failure"),
                ):
                    with self.assertRaises(RuntimeError):
                        owner._migrate_inline_draft_files_to_object_storage(
                            "quote-compensate",
                            [self.inline_record("compensate-reference")],
                        )
                self.assertEqual(backend._objects, before)
                with contextlib.closing(sqlite3.connect(db_path)) as connection:
                    stored = json.loads(
                        connection.execute(
                            "select draft_files_json from sqag_quote_sessions "
                            "where workspace_id = ? and session_id = ?",
                            ("workspace-lazy", "quote-compensate"),
                        ).fetchone()[0]
                    )
                self.assertEqual(stored, [self.inline_record("compensate-reference")])

    def test_cli_defaults_to_count_only_then_applies_a_bounded_batch(self):
        backend = webapp.InMemoryObjectStorageBackend()
        migrator = load_migrator()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            db_path = root / "sqag.sqlite3"
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                self.insert_legacy(
                    db_path,
                    "workspace-cli",
                    "quote-cli-legacy",
                    [self.inline_record()],
                )
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "migrate_inline_draft_files_to_object_storage.py",
                            "--workspace-id",
                            "workspace-cli",
                        ],
                    ),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(migrator.main(), 0)
                self.assertEqual(json.loads(output.getvalue())["status"], "dry_run")
                self.assertEqual(backend._objects, {})
                with (
                    mock.patch.object(
                        sys,
                        "argv",
                        [
                            "migrate_inline_draft_files_to_object_storage.py",
                            "--workspace-id",
                            "workspace-cli",
                            "--limit",
                            "1",
                            "--apply",
                        ],
                    ),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    self.assertEqual(migrator.main(), 0)
                report = json.loads(output.getvalue())
                self.assertEqual(
                    (
                        report["status"],
                        report["limit"],
                        report["migrated"],
                        report["remaining"],
                    ),
                    ("ok", 1, 1, 0),
                )

    def test_empty_upload_is_rejected_before_backend(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            empty = {
                **self.inline_record(),
                "data_url": "data:image/png;base64,",
                "size": 0,
            }
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                owner = webapp.app_storage_for_auth_session(self.auth_session("workspace-empty"))
                with self.assertRaisesRegex(ValueError, "must not be empty"):
                    owner.create_or_update_quote_session(
                        {
                            "quote_session": {
                                "session_id": "quote-empty-upload",
                                "draft_files": [empty],
                            }
                        }
                    )
                self.assertEqual(backend._objects, {})


if __name__ == "__main__":
    unittest.main()
