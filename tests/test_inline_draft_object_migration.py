import base64
import contextlib
import http.client
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from webapp import server as webapp
from tests.test_webapp import LocalRunnerServer

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
        launch_expiry = (
            webapp.dt.datetime.now(webapp.dt.timezone.utc) + webapp.dt.timedelta(minutes=5)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
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
                "launchTokenExpiresAt": launch_expiry,
                "validationGrantId": f"synthetic-grant-{workspace_id}-{user_id}",
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
                prior_objects = dict(backend._objects)
                with contextlib.closing(sqlite3.connect(root / "sqag.sqlite3")) as connection:
                    prior_upload_rows = connection.execute(
                        "select artifact_kind, object_key_ref, checksum_sha256 from sqag_object_artifacts "
                        "where workspace_id = ? and owner_type = ? and status = ? order by artifact_kind",
                        ("workspace-combined", "uploaded_reference", "active"),
                    ).fetchall()
                (output_dir / "quotation.xlsx").write_bytes(b"synthetic-xlsx")
                unchanged = owner.create_or_update_quote_session(
                    {
                        "quote_session": {
                            "session_id": session_id,
                            "draft_files": [self.inline_record("old-reference")],
                        }
                    },
                    {"status": "completed"},
                    output_dir,
                    generation_run_id="run-unchanged-123",
                    generation_job_id="job-unchanged-123",
                )
                self.assertEqual(unchanged["session_id"], session_id)
                with contextlib.closing(sqlite3.connect(root / "sqag.sqlite3")) as connection:
                    self.assertEqual(
                        connection.execute(
                            "select artifact_kind, object_key_ref, checksum_sha256 from sqag_object_artifacts "
                            "where workspace_id = ? and owner_type = ? and status = ? order by artifact_kind",
                            ("workspace-combined", "uploaded_reference", "active"),
                        ).fetchall(),
                        prior_upload_rows,
                    )
                    self.assertGreater(
                        connection.execute(
                            "select count(*) from sqag_object_artifacts where workspace_id = ? and owner_type = ? and status = ?",
                            ("workspace-combined", "generated_quote_version", "active"),
                        ).fetchone()[0],
                        0,
                    )
                for object_key, content in prior_objects.items():
                    self.assertEqual(backend._objects[object_key], content)
                object_snapshot_after_generation = dict(backend._objects)
                session_snapshot_after_generation = owner.get_quote_session(
                    session_id, include_draft_state=True,
                )
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
                self.assertEqual(backend._objects, object_snapshot_after_generation)
                self.assertEqual(
                    owner.get_quote_session(session_id, include_draft_state=True),
                    session_snapshot_after_generation,
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
                        2,
                    )

                tampered = self.inline_record("other-session-reference")
                with self.assertRaises(webapp.SqagStorageAccessError) as tampered_error:
                    owner.create_or_update_quote_session(
                        {"quote_session": {"session_id": session_id, "draft_files": [tampered]}},
                        {"status": "completed"},
                        output_dir,
                        generation_run_id="run-tampered-123",
                        generation_job_id="job-tampered-123",
                    )
                self.assertEqual(tampered_error.exception.status, 409)
                self.assertEqual(backend._objects, object_snapshot_after_generation)

                (output_dir / "quotation.xlsx").write_bytes(b"race-xlsx")
                with mock.patch.object(
                    owner,
                    "_object_draft_files_match_persisted",
                    side_effect=[True, False],
                ):
                    with self.assertRaises(webapp.SqagStorageAccessError) as race_error:
                        owner.create_or_update_quote_session(
                            {
                                "quote_session": {
                                    "session_id": session_id,
                                    "draft_files": [self.inline_record("old-reference")],
                                }
                            },
                            {"status": "completed"},
                            output_dir,
                            generation_run_id="run-race-123",
                            generation_job_id="job-race-123",
                        )
                self.assertEqual(race_error.exception.status, 409)
                self.assertEqual(backend._objects, object_snapshot_after_generation)

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

    def test_object_nonversioned_generation_accepts_unchanged_saved_upload(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            output_dir = root / "output"
            output_dir.mkdir()
            (output_dir / "quotation.xlsx").write_bytes(b"synthetic-nonversioned-xlsx")
            upload = self.inline_record("nonversioned-reference")
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                owner = webapp.app_storage_for_auth_session(self.auth_session("workspace-nonversioned"))
                owner.create_or_update_quote_session(
                    {"quote_session": {"session_id": "quote-nonversioned", "draft_files": [upload]}}
                )
                with contextlib.closing(sqlite3.connect(root / "sqag.sqlite3")) as connection:
                    upload_row_before = connection.execute(
                        "select artifact_kind, object_key_ref, checksum_sha256 from sqag_object_artifacts "
                        "where workspace_id = ? and owner_type = ? and owner_id = ? and status = ?",
                        ("workspace-nonversioned", "uploaded_reference", "quote-nonversioned", "active"),
                    ).fetchone()
                upload_object_before = backend._objects[upload_row_before[1]]
                generated = owner.create_or_update_quote_session(
                    {"quote_session": {"session_id": "quote-nonversioned", "draft_files": [upload]}},
                    {"status": "completed"},
                    output_dir,
                )
                self.assertEqual(generated["session_id"], "quote-nonversioned")
                with contextlib.closing(sqlite3.connect(root / "sqag.sqlite3")) as connection:
                    upload_rows = connection.execute(
                        "select artifact_kind, object_key_ref, checksum_sha256 from sqag_object_artifacts "
                        "where workspace_id = ? and owner_type = ? and owner_id = ? and status = ?",
                        ("workspace-nonversioned", "uploaded_reference", "quote-nonversioned", "active"),
                    ).fetchall()
                    generated_rows = connection.execute(
                        "select count(*) from sqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and status = ?",
                        ("workspace-nonversioned", "generated_quote", "quote-nonversioned", "active"),
                    ).fetchone()[0]
                self.assertEqual(upload_rows, [upload_row_before])
                self.assertEqual(backend._objects[upload_row_before[1]], upload_object_before)
                self.assertGreater(generated_rows, 0)

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

    def test_legacy_get_is_read_only_and_admin_recovery_compensates(self):
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
                before = dict(backend._objects)
                with contextlib.closing(sqlite3.connect(db_path)) as connection:
                    before_json = connection.execute(
                        "select draft_files_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                        ("workspace-lazy", "quote-lazy-visible"),
                    ).fetchone()[0]
                with self.assertRaises(webapp.SqagStorageAccessError) as recovery_required:
                    owner.get_quote_session("quote-lazy-visible", include_draft_state=True)
                self.assertEqual(recovery_required.exception.reason, "object_draft_recovery_required")
                self.assertEqual(backend._objects, before)
                with contextlib.closing(sqlite3.connect(db_path)) as connection:
                    self.assertEqual(
                        connection.execute(
                            "select draft_files_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                            ("workspace-lazy", "quote-lazy-visible"),
                        ).fetchone()[0],
                        before_json,
                    )
                admin = self.admin_storage(database_url, "workspace-lazy")
                migrated = admin.migrate_workspace_inline_draft_files_to_object_storage(limit=1)
                self.assertEqual((migrated["migrated"], migrated["failed"]), (1, 0))
                restored = owner.get_quote_session("quote-lazy-visible", include_draft_state=True)
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

    def test_migration_keyset_cursor_advances_past_permanent_failures(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            db_path = root / "sqag.sqlite3"
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                invalid = {**self.inline_record("invalid"), "data_url": ""}
                for index in range(3):
                    self.insert_legacy(db_path, "workspace-cursor", f"quote-00{index}", [invalid])
                for index in range(3):
                    self.insert_legacy(
                        db_path,
                        "workspace-cursor",
                        f"quote-10{index}",
                        [self.inline_record(f"valid-{index}")],
                    )
                self.insert_legacy(
                    db_path, "workspace-other", "quote-999", [self.inline_record("other")]
                )
                admin = self.admin_storage(database_url, "workspace-cursor")
                first = admin.migrate_workspace_inline_draft_files_to_object_storage(limit=3)
                self.assertEqual((first["processed"], first["failed"], first["migrated"]), (3, 3, 0))
                self.assertEqual(first["next_cursor"], "quote-002")
                second = admin.migrate_workspace_inline_draft_files_to_object_storage(
                    limit=3, after_session_id=first["next_cursor"],
                )
                self.assertEqual((second["processed"], second["failed"], second["migrated"]), (3, 0, 3))
                object_snapshot = dict(backend._objects)
                retry = admin.migrate_workspace_inline_draft_files_to_object_storage(
                    limit=3, after_session_id=first["next_cursor"],
                )
                self.assertEqual((retry["processed"], retry["migrated"]), (0, 0))
                self.assertEqual(backend._objects, object_snapshot)
                self.assertEqual(admin.count_workspace_inline_draft_files_for_object_migration(), 3)
                self.assertEqual(
                    self.admin_storage(database_url, "workspace-other").count_workspace_inline_draft_files_for_object_migration(),
                    1,
                )
                with contextlib.closing(sqlite3.connect(db_path)) as connection:
                    failed_rows = connection.execute(
                        "select session_id from sqag_quote_sessions where workspace_id = ? and draft_files_json like ? order by session_id",
                        ("workspace-cursor", '%"data_url"%'),
                    ).fetchall()
                self.assertEqual([row[0] for row in failed_rows], ["quote-000", "quote-001", "quote-002"])

    def test_display_filename_is_separate_from_storage_filename(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            db_path = root / "sqag.sqlite3"
            names = [
                "Booth Render Final.png",
                "展台设计.png",
                "x" * 220 + ".png",
                "../../unsafe/path/render.png",
                "é.png",
                "è.png",
            ]
            records = [
                {**self.inline_record(f"display-{index}"), "name": name}
                for index, name in enumerate(names)
            ]
            with (
                mock.patch.dict(os.environ, self.object_env(database_url)),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                owner = webapp.app_storage_for_auth_session(self.auth_session("workspace-filenames"))
                owner.create_or_update_quote_session(
                    {"quote_session": {"session_id": "quote-filenames", "draft_files": records}}
                )
                restored = owner.get_quote_session("quote-filenames", include_draft_state=True)
                expected_names = [names[0], names[1], names[2][:180], "render.png", names[4], names[5]]
                self.assertEqual([item["name"] for item in restored["draft_files"]], expected_names)
                with contextlib.closing(sqlite3.connect(db_path)) as connection:
                    storage_names = [
                        row[0] for row in connection.execute(
                            "select filename from sqag_object_artifacts where workspace_id = ? and owner_type = ? order by artifact_kind",
                            ("workspace-filenames", "uploaded_reference"),
                        ).fetchall()
                    ]
                self.assertTrue(all(name == webapp.safe_segment(name, "reference-file") for name in storage_names))
                self.assertEqual(len(backend._objects), len(records))
                owner.create_or_update_quote_session(
                    {"quote_session": {"session_id": "quote-filenames", "draft_files": [records[0]]}}
                )
                replaced = owner.get_quote_session("quote-filenames", include_draft_state=True)
                self.assertEqual([item["name"] for item in replaced["draft_files"]], [names[0]])
                self.assertEqual(len(backend._objects), 1)

                legacy = {**self.inline_record("legacy-display"), "name": "../旧版 图片.png"}
                self.insert_legacy(db_path, "workspace-filenames", "quote-legacy-filename", [legacy])
                admin = self.admin_storage(database_url, "workspace-filenames")
                migrated = admin.migrate_workspace_inline_draft_files_to_object_storage(limit=10)
                self.assertEqual(migrated["migrated"], 1)
                reloaded = owner.get_quote_session("quote-legacy-filename", include_draft_state=True)
                self.assertEqual(reloaded["draft_files"][0]["name"], "旧版 图片.png")

    def test_quote_session_get_storage_failures_are_safe_503_and_server_recovers(self):
        backend = webapp.InMemoryObjectStorageBackend()
        with writable_test_root() as root:
            database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
            db_path = root / "sqag.sqlite3"
            auth_session = self.auth_session("workspace-http")
            env = {
                **self.object_env(database_url),
                "APP_MODE": "local",
                "AUTH_REQUIRED": "true",
                "SESSION_SECRET": "test-session-secret-with-enough-entropy",
            }
            with (
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            ):
                webapp.apply_sqag_storage_migrations(database_url)
                owner = webapp.app_storage_for_auth_session(auth_session)
                cookie = f"{webapp.SESSION_COOKIE_NAME}={webapp.signed_cookie_value({'user': auth_session['user']})}"

                def request(runner, path, request_cookie=cookie):
                    parsed = urllib.parse.urlparse(runner.base_url)
                    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
                    connection.request("GET", path, headers={"Cookie": request_cookie})
                    response = connection.getresponse()
                    body = json.loads(response.read().decode("utf-8"))
                    connection.close()
                    return response.status, body

                cases = ("missing", "checksum", "size", "provider")
                with LocalRunnerServer() as runner:
                    legacy_session_id = "quote-http-legacy"
                    self.insert_legacy(
                        db_path, "workspace-http", legacy_session_id, [self.inline_record("legacy-http")]
                    )
                    with contextlib.closing(sqlite3.connect(db_path)) as connection:
                        legacy_before = connection.execute(
                            "select draft_files_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                            ("workspace-http", legacy_session_id),
                        ).fetchone()[0]
                    objects_before_legacy_get = dict(backend._objects)
                    status, body = request(runner, f"/api/quote-sessions/{legacy_session_id}")
                    self.assertEqual(status, 503)
                    self.assertTrue(body["recovery_required"])
                    self.assertEqual(backend._objects, objects_before_legacy_get)
                    with contextlib.closing(sqlite3.connect(db_path)) as connection:
                        self.assertEqual(
                            connection.execute(
                                "select draft_files_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                                ("workspace-http", legacy_session_id),
                            ).fetchone()[0],
                            legacy_before,
                        )
                    other_auth = self.auth_session("workspace-other-http", user_id="other-http-user")
                    other_cookie = (
                        f"{webapp.SESSION_COOKIE_NAME}="
                        f"{webapp.signed_cookie_value({'user': other_auth['user']})}"
                    )
                    cross_status, _cross_body = request(
                        runner, f"/api/quote-sessions/{legacy_session_id}", other_cookie,
                    )
                    self.assertEqual(cross_status, 404)
                    self.assertEqual(backend._objects, objects_before_legacy_get)
                    for case in cases:
                        session_id = f"quote-http-{case}"
                        owner.create_or_update_quote_session(
                            {"quote_session": {"session_id": session_id, "draft_files": [self.inline_record(case)]}}
                        )
                        object_snapshot = dict(backend._objects)
                        patcher = contextlib.nullcontext()
                        if case == "missing":
                            backend._objects.clear()
                        elif case in {"checksum", "size"}:
                            column = "checksum_sha256" if case == "checksum" else "size_bytes"
                            value = "0" * 64 if case == "checksum" else 999999
                            with contextlib.closing(sqlite3.connect(db_path)) as connection:
                                connection.execute(
                                    f"update sqag_object_artifacts set {column} = ? where workspace_id = ? and owner_type = ? and owner_id = ?",
                                    (value, "workspace-http", "uploaded_reference", session_id),
                                )
                                connection.commit()
                        else:
                            patcher = mock.patch.object(
                                backend, "retrieve_artifact", side_effect=RuntimeError("private provider detail")
                            )
                        with patcher:
                            status, body = request(runner, f"/api/quote-sessions/{session_id}")
                        self.assertEqual(status, 503, (case, body))
                        self.assertEqual(body["status"], "failed")
                        self.assertTrue(body.get("error_reference"))
                        serialized = json.dumps(body)
                        self.assertNotIn("synthetic-bucket", serialized)
                        self.assertNotIn("private provider detail", serialized)
                        self.assertNotIn("object_key", serialized)
                        backend._objects.clear()
                        backend._objects.update(object_snapshot)
                    status, body = request(runner, "/api/session")
                    self.assertEqual(status, 200)
                    self.assertTrue(body["authenticated"])

    def test_quote_session_post_storage_failures_are_structured_and_compensated(self):
        class StoreFailingBackend(webapp.InMemoryObjectStorageBackend):
            def store_artifact(self, **_kwargs):
                raise webapp.ObjectStorageContractError("private provider write detail")

        for failure_kind in ("provider", "metadata"):
            with self.subTest(failure_kind=failure_kind), writable_test_root() as root:
                database_url = f"sqlite:///{(root / 'sqag.sqlite3').as_posix()}"
                db_path = root / "sqag.sqlite3"
                backend = StoreFailingBackend() if failure_kind == "provider" else webapp.InMemoryObjectStorageBackend()
                auth_session = self.auth_session(
                    f"workspace-post-{failure_kind}",
                    user_id=f"post-{failure_kind}-user",
                )
                env = {
                    **self.object_env(database_url),
                    "APP_MODE": "local",
                    "AUTH_REQUIRED": "true",
                    "SESSION_SECRET": "test-session-secret-with-enough-entropy",
                }
                def request_json(runner, method, path, *, body=None, headers=None):
                    parsed = urllib.parse.urlparse(runner.base_url)
                    request_headers = {"Accept": "application/json", "Cookie": cookie}
                    if body is not None:
                        request_headers["Content-Type"] = "application/json"
                    request_headers.update(headers or {})
                    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
                    try:
                        connection.request(
                            method,
                            path,
                            body=json.dumps(body).encode("utf-8") if body is not None else None,
                            headers=request_headers,
                        )
                        response = connection.getresponse()
                        response_body = json.loads(response.read().decode("utf-8"))
                        return response.status, response_body
                    finally:
                        connection.close()

                with (
                    mock.patch.dict(os.environ, env, clear=True),
                    mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
                ):
                    cookie = (
                        f"{webapp.SESSION_COOKIE_NAME}="
                        f"{webapp.signed_cookie_value({'user': auth_session['user']})}"
                    )
                    webapp.apply_sqag_storage_migrations(database_url)
                    metadata_failure = (
                        mock.patch.object(
                            webapp.DatabaseSqagStorage,
                            "_execute_object_artifact_batch_metadata",
                            side_effect=sqlite3.OperationalError("private database detail"),
                        )
                        if failure_kind == "metadata"
                        else contextlib.nullcontext()
                    )
                    with LocalRunnerServer() as runner:
                        session_status, session_body = request_json(runner, "GET", "/api/session")
                        self.assertEqual(session_status, 200)
                        csrf_headers = {
                            "Origin": runner.base_url,
                            session_body["csrf_header"]: session_body["csrf_token"],
                        }
                        with metadata_failure:
                            status, body = request_json(
                                runner,
                                "POST",
                                "/api/quote-sessions",
                                body={
                                    "quote_session": {
                                        "session_id": f"quote-post-{failure_kind}",
                                        "draft_state": {"activeSidePanel": "customer"},
                                        "draft_files": [self.inline_record(f"post-{failure_kind}")],
                                    }
                                },
                                headers=csrf_headers,
                            )
                        self.assertEqual(status, 503)
                        self.assertEqual(body["status"], "failed")
                        self.assertTrue(body.get("error_reference"))
                        response_text = json.dumps(body, sort_keys=True)
                        self.assertNotIn("synthetic-bucket", response_text)
                        self.assertNotIn("private provider write detail", response_text)
                        self.assertNotIn("private database detail", response_text)
                        self.assertNotIn("object_key", response_text)
                        subsequent_status, subsequent_body = request_json(
                            runner, "GET", "/api/session",
                        )
                        self.assertEqual(subsequent_status, 200)
                        self.assertTrue(subsequent_body["authenticated"])

                    with contextlib.closing(sqlite3.connect(db_path)) as connection:
                        self.assertEqual(
                            connection.execute("select count(*) from sqag_quote_sessions").fetchone()[0],
                            0,
                        )
                        self.assertEqual(
                            connection.execute("select count(*) from sqag_object_artifacts").fetchone()[0],
                            0,
                        )
                    self.assertEqual(backend._objects, {})

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
