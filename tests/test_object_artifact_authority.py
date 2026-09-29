from __future__ import annotations

import contextlib
import datetime as dt
import dataclasses
import json
import os
import pickle
import sqlite3
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

from webapp import server as webapp


ARTIFACT_FIELDS = (
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
)


class PersistentFileObjectStorageBackend(webapp.InMemoryObjectStorageBackend):
    """Synthetic provider state that survives a real interpreter restart."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        if self.path.exists():
            with self.path.open("rb") as source:
                state = pickle.load(source)
            self._objects = state["objects"]
            self._metadata = state["metadata"]

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("wb") as target:
            pickle.dump(
                {"objects": self._objects, "metadata": self._metadata},
                target,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, self.path)

    def store_artifact(self, **kwargs):
        metadata = super().store_artifact(**kwargs)
        self._persist()
        return metadata

    def delete_artifact(self, metadata, *, workspace_id):
        deleted = super().delete_artifact(metadata, workspace_id=workspace_id)
        if deleted:
            self._persist()
        return deleted


class ArtifactAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {"APP_MODE": "local"}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.storage = webapp.DatabaseSqagStorage(
            "sqlite:///:memory:",
            "workspace-authority",
            role="maintenance",
            user_id="synthetic-authority-test",
        )

    def artifact_row(self) -> dict[str, object]:
        return {
            "artifact_id": "obj-v2-" + "a" * 64,
            "workspace_id": "workspace-authority",
            "owner_type": "profile",
            "owner_id": "profile-one",
            "platform_user_id": None,
            "session_id": None,
            "job_id": None,
            "artifact_kind": "quotation_layout",
            "filename": "layout.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "size_bytes": 4,
            "checksum_sha256": "b" * 64,
            "object_provider_type": "s3_compatible",
            "object_key_ref": "workspaces/workspace-authority/profile/profile-one/quotation_layout/v2/inc-v2-" + "c" * 64 + "/" + "b" * 64 + "-layout.xlsx",
            "status": "active",
            "retention_status": "active",
            "created_at": "2026-09-29T00:00:00Z",
            "updated_at": "2026-09-29T00:00:00Z",
            "deleted_at": None,
        }

    def test_snapshot_has_exact_frozen_nineteen_scalar_fields(self):
        self.assertEqual(
            tuple(field.name for field in dataclasses.fields(webapp.ArtifactRowSnapshot)),
            ARTIFACT_FIELDS,
        )
        snapshot = self.storage._artifact_row_snapshot(self.artifact_row())
        with self.assertRaises(FrozenInstanceError):
            snapshot.filename = "changed.xlsx"

        for name in ARTIFACT_FIELDS:
            with self.subTest(field=name):
                changed = self.artifact_row()
                original = changed[name]
                if name == "size_bytes":
                    changed[name] = original + 1
                elif original is None:
                    changed[name] = ""
                else:
                    changed[name] = original + " "
                self.assertNotEqual(
                    snapshot,
                    self.storage._artifact_row_snapshot(changed),
                )

    def test_snapshot_preserves_null_empty_and_whitespace_distinctions(self):
        null_value = self.artifact_row()
        empty_value = self.artifact_row()
        empty_value["platform_user_id"] = ""
        self.assertNotEqual(
            self.storage._artifact_row_snapshot(null_value),
            self.storage._artifact_row_snapshot(empty_value),
        )

        exact_filename = self.artifact_row()
        whitespace_filename = self.artifact_row()
        whitespace_filename["filename"] = " layout.xlsx"
        self.assertNotEqual(
            self.storage._artifact_row_snapshot(exact_filename),
            self.storage._artifact_row_snapshot(whitespace_filename),
        )

    def test_snapshot_rejects_incomplete_rows_and_invalid_size_types(self):
        missing = self.artifact_row()
        del missing["created_at"]
        with self.assertRaises(webapp.ObjectStorageContractError):
            self.storage._artifact_row_snapshot(missing)

        for invalid in (0, -1, True, 1.0, "4", None):
            with self.subTest(size_bytes=invalid):
                row = self.artifact_row()
                row["size_bytes"] = invalid
                with self.assertRaises(webapp.ObjectStorageContractError):
                    self.storage._artifact_row_snapshot(row)

        wrong_scalar = self.artifact_row()
        wrong_scalar["filename"] = 4
        with self.assertRaises(webapp.ObjectStorageContractError):
            self.storage._artifact_row_snapshot(wrong_scalar)

    def test_profile_snapshot_binds_all_five_exact_fields(self):
        row = {
            "workspace_id": "workspace-authority",
            "profile_id": "profile-one",
            "payload_json": '{"id":"profile-one"}',
            "created_at": "2026-09-29T00:00:00Z",
            "updated_at": "2026-09-29T00:00:00Z",
        }
        snapshot = self.storage._profile_row_snapshot(row)
        self.assertEqual(
            tuple(dataclasses.asdict(snapshot)),
            ("workspace_id", "profile_id", "payload_json", "created_at", "updated_at"),
        )
        for name in row:
            with self.subTest(field=name):
                changed = dict(row)
                changed[name] += " "
                self.assertNotEqual(snapshot, self.storage._profile_row_snapshot(changed))

        malformed = dict(row)
        malformed["payload_json"] = None
        with self.assertRaises(webapp.ObjectStorageContractError):
            self.storage._profile_row_snapshot(malformed)

    def test_snapshot_retrieval_helper_uses_snapshot_without_database_read(self):
        content = b"exact-snapshot-bytes"
        row = self.artifact_row()
        row["size_bytes"] = len(content)
        row["checksum_sha256"] = webapp.artifact_checksum(content)
        incarnation = "inc-v2-" + "c" * 64
        row["object_key_ref"] = webapp.object_artifact_key(
            workspace_id=row["workspace_id"],
            owner_type=row["owner_type"],
            owner_id=row["owner_id"],
            artifact_kind=row["artifact_kind"],
            filename=row["filename"],
            checksum_sha256=row["checksum_sha256"],
            artifact_incarnation=incarnation,
        )
        snapshot = self.storage._artifact_row_snapshot(row)
        metadata = self.storage._object_metadata_from_snapshot(snapshot)
        backend = webapp.InMemoryObjectStorageBackend()
        stored = backend.store_artifact(
            workspace_id=metadata.workspace_id,
            owner_type=metadata.owner_type,
            owner_id=metadata.owner_id,
            artifact_kind=metadata.artifact_kind,
            filename=metadata.filename,
            content_type=metadata.content_type,
            content=content,
            artifact_incarnation=metadata.artifact_incarnation,
            artifact_id=metadata.artifact_id,
            platform_user_id=metadata.platform_user_id,
            session_id=metadata.session_id,
            job_id=metadata.job_id,
            binding_sha256=metadata.binding_sha256,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
        )
        self.assertEqual(stored, metadata)

        with (
            mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
            mock.patch.object(self.storage, "connection", side_effect=AssertionError("snapshot retrieval queried the database")),
        ):
            retrieved = self.storage._retrieve_object_artifact_snapshot(snapshot)

        self.assertEqual(retrieved["content"], content)
        self.assertEqual(retrieved["filename"], snapshot.filename)
        self.assertEqual(retrieved["size_bytes"], snapshot.size_bytes)

    def test_owner_row_digest_uses_the_exact_authority_payload(self):
        payload = {"id": "profile-one", "details": "exact  "}

        class Result:
            def fetchone(self):
                return {"payload_json": json.dumps(payload, ensure_ascii=True, sort_keys=True)}

        class Connection:
            def execute(self, query, params):
                self_query = " ".join(query.split())
                if "from sqag_profiles" not in self_query:
                    raise AssertionError("Unexpected owner table.")
                if params != ("workspace-authority", "profile-one"):
                    raise AssertionError("Owner identity changed.")
                return Result()

        self.assertEqual(
            self.storage._object_owner_row_digest(Connection(), "profile", "profile-one"),
            self.storage._canonical_json_digest(payload),
        )


class PreparedAbortLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="sqag-prepared-abort-")
        self.addCleanup(self.temp.cleanup)
        self.database_url = f"sqlite:///{(Path(self.temp.name) / 'sqag.sqlite3').as_posix()}"
        env = mock.patch.dict(
            os.environ,
            {
                "APP_MODE": "local",
                "SQAG_STORAGE_MODE": "database",
                "SQAG_ARTIFACT_STORAGE_MODE": "object",
                "SQAG_DATABASE_URL": self.database_url,
            },
            clear=True,
        )
        env.start()
        self.addCleanup(env.stop)
        webapp.apply_sqag_storage_migrations(self.database_url)
        self.backend = webapp.InMemoryObjectStorageBackend()
        backend_patch = mock.patch.object(
            webapp,
            "configured_object_storage_backend",
            return_value=self.backend,
        )
        backend_patch.start()
        self.addCleanup(backend_patch.stop)
        self.storage = self.new_storage()

    def new_storage(self):
        return webapp.DatabaseSqagStorage(
            self.database_url,
            "workspace-abort-test",
            role="maintenance",
            user_id="synthetic-abort-test",
        )

    def lifecycle_callbacks(self, content: bytes, label: str, storage=None):
        target_storage = storage or self.storage
        context = {"id": "profile-abort-test", "label": label}
        item = webapp.ArtifactBatchItem(
            artifact_kind="quotation_layout",
            filename="layout.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=content,
        )

        def prepare(connection, lock_identities):
            plan = target_storage._prepare_object_artifact_batch(
                "profile",
                "profile-abort-test",
                [item],
                {"quotation_layout"},
                {"quotation_layout"},
                connection=connection,
                quote_session=False,
                request_context=context,
                lifecycle_lock_identities=lock_identities,
            )
            return plan, plan

        def persist(connection, plan):
            target_storage._execute_object_artifact_batch_metadata(
                connection,
                plan,
                quote_session=False,
            )
            target_storage._execute_upsert_payload(
                connection,
                "sqag_profiles",
                "profile_id",
                "profile-abort-test",
                context,
            )
            return context

        return prepare, persist

    def commit_fault_patch(self, storage, *, target_commit: int, apply_before_raise: bool):
        state = {"commits": 0, "injected": False}
        original_connection = storage.connection

        class CommitFaultProxy:
            def __init__(self, connection):
                self.connection = connection

            def commit(self):
                state["commits"] += 1
                if state["commits"] == target_commit and not state["injected"]:
                    state["injected"] = True
                    if apply_before_raise:
                        self.connection.commit()
                    raise RuntimeError("synthetic publication commit acknowledgement lost")
                return self.connection.commit()

            def rollback(self):
                return self.connection.rollback()

            def __getattr__(self, name):
                return getattr(self.connection, name)

        @contextlib.contextmanager
        def wrapped_connection():
            with original_connection() as connection:
                yield CommitFaultProxy(connection)

        return mock.patch.object(storage, "connection", new=wrapped_connection), state

    def run_save(self, storage, content: bytes, label: str):
        prepare, persist = self.lifecycle_callbacks(content, label, storage=storage)
        result, plan = storage._run_object_lifecycle_save(
            "profile", "profile-abort-test", prepare, persist
        )
        return result, plan

    def test_rehydrated_version_fallback_rejects_session_job_and_state_drift(self):
        run_id = "run-receipt-version-1"
        session_id = "quote-receipt-version"
        job_id = "job-receipt-version-1"
        now = webapp.utc_timestamp()
        metadata = {
            "session_id": session_id,
            "publication": {"run_id": run_id, "job_id": job_id},
        }
        encoded_metadata = json.dumps(metadata, ensure_ascii=True, sort_keys=True)
        with self.storage.connection() as connection:
            connection.execute(
                "insert into sqag_quote_publication_versions "
                "(workspace_id, session_id, run_id, job_id, state, artifact_storage_mode, "
                "artifact_source, metadata_json, error_code, created_at, updated_at, "
                "retention_expires_at, original_retention_expires_at, legal_hold, deletion_state) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "workspace-abort-test", session_id, run_id, job_id, "staged",
                    "object", "version", encoded_metadata, None, now, now,
                    "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z", 0, "active",
                ),
            )
            connection.commit()

        def run_version_save(content):
            item = webapp.ArtifactBatchItem(
                artifact_kind="xlsx",
                filename="quotation.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                content=content,
            )

            def prepare(connection, lock_identities):
                plan = self.storage._prepare_object_artifact_batch(
                    "generated_quote_version",
                    run_id,
                    [item],
                    {"xlsx"},
                    {"xlsx"},
                    connection=connection,
                    quote_session=True,
                    request_context={"metadata": metadata},
                    session_id=session_id,
                    job_id=job_id,
                    lifecycle_lock_identities=lock_identities,
                )
                return plan, plan

            def persist(connection, plan):
                self.storage._execute_object_artifact_batch_metadata(
                    connection, plan, quote_session=True
                )
                return plan

            _result, plan = self.storage._run_object_lifecycle_save(
                "generated_quote_version", run_id, prepare, persist
            )
            return plan

        original_plan = run_version_save(b"version-one")
        run_version_save(b"version-two")
        self.assertTrue(self.storage._verify_published_object_artifact_batch(original_plan))

        for column, changed in (
            ("session_id", "session-drift"),
            ("job_id", "job-drift"),
            ("state", "superseded"),
        ):
            with self.subTest(column=column):
                with self.storage.connection() as connection:
                    original = connection.execute(
                        f"select {column} from sqag_quote_publication_versions "
                        "where workspace_id = ? and run_id = ?",
                        ("workspace-abort-test", run_id),
                    ).fetchone()[column]
                    connection.execute(
                        f"update sqag_quote_publication_versions set {column} = ? "
                        "where workspace_id = ? and run_id = ?",
                        (changed, "workspace-abort-test", run_id),
                    )
                    connection.commit()
                self.assertFalse(
                    self.storage._verify_published_object_artifact_batch(original_plan)
                )
                with self.storage.connection() as connection:
                    connection.execute(
                        f"update sqag_quote_publication_versions set {column} = ? "
                        "where workspace_id = ? and run_id = ?",
                        (original, "workspace-abort-test", run_id),
                    )
                    connection.commit()
                self.assertTrue(
                    self.storage._verify_published_object_artifact_batch(original_plan)
                )

    def test_process_restart_recovers_staged_successor_from_database_and_provider(self):
        database_path = Path(self.temp.name) / "fresh-process.sqlite3"
        database_url = f"sqlite:///{database_path.as_posix()}"
        backend_path = Path(self.temp.name) / "fresh-process-objects.pickle"
        workspace_id = "workspace-process-recovery"
        owner_id = "profile-process-recovery"
        webapp.apply_sqag_storage_migrations(database_url)
        backend = PersistentFileObjectStorageBackend(backend_path)
        storage = webapp.DatabaseSqagStorage(
            database_url, workspace_id, role="maintenance", user_id="synthetic-process-test"
        )
        env = {
            "APP_MODE": "local",
            "SQAG_STORAGE_MODE": "database",
            "SQAG_ARTIFACT_STORAGE_MODE": "object",
            "SQAG_DATABASE_URL": database_url,
        }

        def save(content, label):
            context = {"id": owner_id, "label": label}
            item = webapp.ArtifactBatchItem(
                artifact_kind="quotation_layout",
                filename="layout.xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                content=content,
            )

            def prepare(connection, lock_identities):
                plan = storage._prepare_object_artifact_batch(
                    "profile", owner_id, [item], {"quotation_layout"},
                    {"quotation_layout"}, connection=connection, quote_session=False,
                    request_context=context, lifecycle_lock_identities=lock_identities,
                )
                return plan, plan

            def persist(connection, plan):
                storage._execute_object_artifact_batch_metadata(
                    connection, plan, quote_session=False
                )
                storage._execute_upsert_payload(
                    connection, "sqag_profiles", "profile_id", owner_id, context
                )
                return context

            return storage._run_object_lifecycle_save("profile", owner_id, prepare, persist)[1]

        child_script = """
import json, os, pickle, sys
from pathlib import Path
from webapp import server as webapp
database_url, workspace_id, owner_id, backend_path, mode = sys.argv[1:6]
class PersistentFileObjectStorageBackend(webapp.InMemoryObjectStorageBackend):
    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            with self.path.open('rb') as source:
                state = pickle.load(source)
            self._objects = state['objects']
            self._metadata = state['metadata']
    def _persist(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.tmp')
        with temporary.open('wb') as target:
            pickle.dump({'objects': self._objects, 'metadata': self._metadata}, target, protocol=pickle.HIGHEST_PROTOCOL)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, self.path)
    def store_artifact(self, **kwargs):
        metadata = super().store_artifact(**kwargs)
        self._persist()
        return metadata
    def delete_artifact(self, metadata, *, workspace_id):
        deleted = super().delete_artifact(metadata, workspace_id=workspace_id)
        if deleted:
            self._persist()
        return deleted
backend = PersistentFileObjectStorageBackend(backend_path)
webapp.configured_object_storage_backend = lambda: backend
storage = webapp.DatabaseSqagStorage(database_url, workspace_id, role='maintenance', user_id='synthetic-process-test')
context = {'id': owner_id, 'label': 'second'}
item = webapp.ArtifactBatchItem(artifact_kind='quotation_layout', filename='layout.xlsx', content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', content=b'process-successor')
def prepare(connection, lock_identities):
    plan = storage._prepare_object_artifact_batch('profile', owner_id, [item], {'quotation_layout'}, {'quotation_layout'}, connection=connection, quote_session=False, request_context=context, lifecycle_lock_identities=lock_identities)
    return plan, plan
def persist(connection, plan):
    storage._execute_object_artifact_batch_metadata(connection, plan, quote_session=False)
    storage._execute_upsert_payload(connection, 'sqag_profiles', 'profile_id', owner_id, context)
    return context
if mode == 'crash':
    original = storage._stage_object_artifact_successors
    def stage_then_exit(plan):
        original(plan)
        os._exit(73)
    storage._stage_object_artifact_successors = stage_then_exit
result, plan = storage._run_object_lifecycle_save('profile', owner_id, prepare, persist)
print(json.dumps({'pid': os.getpid(), 'operation_id': plan.operation_id, 'state': plan.state}))
"""
        child_env = dict(env)
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
        ):
            save(b"process-predecessor", "first")
            child_args = [
                sys.executable, "-c", child_script, database_url, workspace_id,
                owner_id, str(backend_path), "crash",
            ]
            crashed = subprocess.run(
                child_args, cwd=Path(__file__).resolve().parents[1], env=child_env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(crashed.returncode, 73, crashed.stdout + crashed.stderr)

            with storage.connection() as connection:
                prepared = connection.execute(
                    "select operation_id, state from sqag_object_artifact_operations "
                    "where workspace_id = ? and owner_type = ? and owner_id = ? "
                    "order by operation_seq desc limit 1",
                    (workspace_id, "profile", owner_id),
                ).fetchone()
                payload = json.loads(connection.execute(
                    "select payload_json from sqag_profiles where workspace_id = ? and profile_id = ?",
                    (workspace_id, owner_id),
                ).fetchone()["payload_json"])
            self.assertEqual(prepared["state"], "prepared")
            self.assertEqual(payload["label"], "first")

            child_args[7] = "resume"
            resumed = subprocess.run(
                child_args, cwd=Path(__file__).resolve().parents[1], env=child_env,
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            child_result = json.loads(resumed.stdout.strip())
            self.assertNotEqual(child_result["pid"], os.getpid())
            self.assertEqual(child_result["operation_id"], prepared["operation_id"])
            self.assertEqual(child_result["state"], "published")

            with storage.connection() as connection:
                payload = json.loads(connection.execute(
                    "select payload_json from sqag_profiles where workspace_id = ? and profile_id = ?",
                    (workspace_id, owner_id),
                ).fetchone()["payload_json"])
                journal = connection.execute(
                    "select * from sqag_object_artifact_operations where workspace_id = ? "
                    "and owner_type = ? and owner_id = ? order by operation_seq desc limit 1",
                    (workspace_id, "profile", owner_id),
                ).fetchone()
            self.assertEqual(payload["label"], "second")
            self.assertEqual(journal["state"], "published")
            fresh_backend = PersistentFileObjectStorageBackend(backend_path)
            with mock.patch.object(
                webapp, "configured_object_storage_backend", return_value=fresh_backend
            ):
                recovered_plan = storage._load_object_artifact_plan(
                    journal, backend=fresh_backend, items=[], request_context=None
                )
                self.assertTrue(
                    storage._verify_published_object_artifact_batch(recovered_plan)
                )

    def test_cancellation_between_lock_phases_leaves_prepared_operation_recoverable(self):
        prepare, persist = self.lifecycle_callbacks(b"cancelled-layout", "first")
        original_begin = self.storage._begin_object_lifecycle_transactions
        calls = 0

        def cancel_second_phase(connection, identities):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt("synthetic cancellation between phases")
            return original_begin(connection, identities)

        with mock.patch.object(
            self.storage,
            "_begin_object_lifecycle_transactions",
            side_effect=cancel_second_phase,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.storage._run_object_lifecycle_save(
                    "profile", "profile-abort-test", prepare, persist
                )

        self.assertEqual(calls, 2)
        with contextlib.closing(sqlite3.connect(Path(self.temp.name) / "sqag.sqlite3")) as connection:
            row = connection.execute(
                "select state, operation_id, plan_json from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ?",
                ("workspace-abort-test", "profile", "profile-abort-test"),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "prepared")
        self.assertEqual(self.backend._objects, {})

        restarted = self.new_storage()
        _result, retried = self.run_save(
            restarted, b"cancelled-layout", "first"
        )
        self.assertEqual(retried.operation_id, row[1])
        current = self.active_profile_snapshot(restarted)
        self.assertEqual(
            restarted._retrieve_object_artifact_snapshot(current)["content"],
            b"cancelled-layout",
        )

    def test_sqlite_journal_identity_state_and_history_are_database_guarded(self):
        _result, plan = self.run_save(self.storage, b"guarded-layout", "first")
        connection = sqlite3.connect(Path(self.temp.name) / "sqag.sqlite3")
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "update sqag_object_artifact_operations set plan_json = ? "
                    "where workspace_id = ? and owner_type = ? and owner_id = ? and operation_seq = ?",
                    ("{}", "workspace-abort-test", "profile", "profile-abort-test", plan.operation_seq),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "update sqag_object_artifact_operations set state = ? "
                    "where workspace_id = ? and owner_type = ? and owner_id = ? and operation_seq = ?",
                    ("aborted", "workspace-abort-test", "profile", "profile-abort-test", plan.operation_seq),
                )
            connection.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "delete from sqag_object_artifact_operations "
                    "where workspace_id = ? and owner_type = ? and owner_id = ? and operation_seq = ?",
                    ("workspace-abort-test", "profile", "profile-abort-test", plan.operation_seq),
                )
            connection.rollback()
        finally:
            connection.close()

    def test_object_retention_deletes_unheld_old_version_but_preserves_held_and_current(self):
        admin = webapp.DatabaseSqagStorage(
            self.database_url,
            "workspace-abort-test",
            role="admin",
            user_id="synthetic-abort-test",
        )
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        def seed_version(run_id, session_id, *, state="staged", legal_hold=0, current=False):
            content = ("synthetic-retention-artifact:" + run_id).encode("utf-8")
            digest = webapp.artifact_checksum(content)
            now = webapp.utc_timestamp()
            filename = "quotation.xlsx"
            stored = self.backend.store_artifact(
                workspace_id="workspace-abort-test",
                owner_type="generated_quote_version",
                owner_id=run_id,
                artifact_kind="xlsx",
                filename=filename,
                content_type=content_type,
                content=content,
                artifact_id="obj-retention-" + run_id,
                platform_user_id=admin.user_id,
                session_id=session_id,
                job_id="job-" + run_id,
                created_at=now,
                updated_at=now,
            )
            quote_metadata = {
                "session_id": session_id,
                "created_at": now,
                "updated_at": now,
                "publication": (
                    {"state": "published", "run_id": run_id}
                    if current else {"state": "staged", "run_id": ""}
                ),
            }
            version_metadata = {
                "exports": {
                    "xlsx": {
                        "filename": filename,
                        "sha256": digest,
                        "size_bytes": len(content),
                    }
                }
            }
            with admin.connection() as connection:
                connection.execute(
                    "insert into sqag_quote_sessions "
                    "(workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) "
                    "values (?, ?, ?, ?, ?, ?)",
                    (
                        "workspace-abort-test", session_id,
                        json.dumps(quote_metadata, sort_keys=True), "[]", now, now,
                    ),
                )
                connection.execute(
                    "insert into sqag_quote_publication_versions "
                    "(workspace_id, session_id, run_id, job_id, state, artifact_storage_mode, "
                    "artifact_source, metadata_json, created_at, updated_at, retention_expires_at, "
                    "original_retention_expires_at, legal_hold, deletion_state) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "workspace-abort-test", session_id, run_id, "job-" + run_id,
                        state, "object", "version", json.dumps(version_metadata, sort_keys=True),
                        now, now, "2030-01-01T00:00:00Z", "2030-01-01T00:00:00Z",
                        legal_hold, "active",
                    ),
                )
                admin._execute_upsert_object_quote_artifact(
                    connection, session_id, "xlsx", filename, content_type, stored,
                    owner_type="generated_quote_version", owner_id=run_id,
                )
                connection.commit()
            return stored

        held_metadata = seed_version(
            "run-retention-held", "quote-retention-held", legal_hold=1
        )
        held_outcome = admin.delete_quote_publication_version_for_retention(
            "run-retention-held", finalize_graph=lambda _connection: None
        )
        self.assertEqual(held_outcome, webapp.PUBLICATION_RETENTION_HELD)
        self.assertEqual(
            self.backend.retrieve_artifact(
                held_metadata, workspace_id="workspace-abort-test"
            ),
            b"synthetic-retention-artifact:run-retention-held",
        )

        current_metadata = seed_version(
            "run-retention-current", "quote-retention-current",
            state="published", current=True,
        )
        current_outcome = admin.delete_quote_publication_version_for_retention(
            "run-retention-current", finalize_graph=lambda _connection: None
        )
        self.assertEqual(current_outcome, webapp.PUBLICATION_RETENTION_CURRENT)
        self.assertEqual(
            self.backend.retrieve_artifact(
                current_metadata, workspace_id="workspace-abort-test"
            ),
            b"synthetic-retention-artifact:run-retention-current",
        )

        old_metadata = seed_version(
            "run-retention-old", "quote-retention-old", state="superseded"
        )
        old_outcome = admin.delete_quote_publication_version_for_retention(
            "run-retention-old", finalize_graph=lambda _connection: None
        )
        self.assertEqual(old_outcome, webapp.PUBLICATION_RETENTION_DELETED)
        with self.assertRaises(webapp.ObjectStorageNotFoundError):
            self.backend.retrieve_artifact(
                old_metadata, workspace_id="workspace-abort-test"
            )

    def test_deleted_quote_session_between_read_and_lock_is_not_recreated(self):
        session_id = "quote-race-existing"
        now = webapp.utc_timestamp()
        metadata = webapp.blank_quote_session_metadata(session_id, now)
        metadata["owner"] = {"user_id": self.storage.user_id}
        metadata = webapp.normalized_quote_session_metadata(metadata)
        with self.storage.connection() as connection:
            connection.execute(
                "insert into sqag_quote_sessions "
                "(workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (
                    "workspace-abort-test", session_id,
                    json.dumps(metadata, sort_keys=True), "[]", now, now,
                ),
            )
            connection.commit()

        original = self.storage._run_object_lifecycle_save

        def delete_after_outer_read(owner_type, owner_id, prepare, persist, **kwargs):
            with self.storage.connection() as connection:
                connection.execute(
                    "delete from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                    ("workspace-abort-test", session_id),
                )
                connection.commit()
            return original(owner_type, owner_id, prepare, persist, **kwargs)

        with mock.patch.object(
            self.storage,
            "_run_object_lifecycle_save",
            side_effect=delete_after_outer_read,
        ):
            with self.assertRaises(webapp.SqagStorageAccessError):
                self.storage.create_or_update_quote_session(
                    {"quote_session": {"session_id": session_id}},
                    session_id=session_id,
                )

        with self.storage.connection() as connection:
            row = connection.execute(
                "select 1 from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                ("workspace-abort-test", session_id),
            ).fetchone()
        self.assertIsNone(row)

    def test_publication_run_id_cannot_be_rebound_to_another_session(self):
        run_id = "run-cross-session-binding"
        now = webapp.utc_timestamp()
        metadata = {
            "session_id": "quote-original-session",
            "publication": {"state": "staged", "run_id": run_id, "job_id": "job-original"},
        }
        with self.storage.connection() as connection:
            connection.execute(
                "insert into sqag_quote_publication_versions "
                "(workspace_id, session_id, run_id, job_id, state, artifact_storage_mode, "
                "artifact_source, metadata_json, created_at, updated_at, retention_expires_at, "
                "original_retention_expires_at, legal_hold, deletion_state) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "workspace-abort-test", "quote-original-session", run_id,
                    "job-original", "staged", "object", "version",
                    json.dumps(metadata, sort_keys=True), now, now,
                    "2030-01-01T00:00:00Z", "2030-01-01T00:00:00Z", 0, "active",
                ),
            )
            connection.commit()
            for bound_session, bound_job in (
                ("quote-other-session", "job-original"),
                ("quote-original-session", "job-other"),
            ):
                with self.subTest(session_id=bound_session, job_id=bound_job):
                    with self.assertRaisesRegex(
                        webapp.ObjectStorageContractError,
                        "Quote publication version binding changed",
                    ):
                        self.storage._assert_quote_publication_version_binding(
                            connection, run_id, bound_session, bound_job
                        )
            self.storage._assert_quote_publication_version_binding(
                connection, run_id, "quote-original-session", "job-original"
            )

    def test_published_version_verifier_rejects_session_job_or_state_drift(self):
        run_id = "run-verifier-bound"
        session_id = "quote-verifier-bound"
        job_id = "job-verifier-bound"
        now = webapp.utc_timestamp()
        metadata = {
            "session_id": session_id,
            "publication": {"state": "staged", "run_id": run_id, "job_id": job_id},
        }
        plan = webapp.ObjectArtifactBatchPlan(
            backend=self.backend,
            owner_type="generated_quote_version",
            owner_id=run_id,
            items=[],
            request_context={"metadata": metadata},
        )
        with self.storage.connection() as connection:
            connection.execute(
                "insert into sqag_quote_publication_versions "
                "(workspace_id, session_id, run_id, job_id, state, artifact_storage_mode, "
                "artifact_source, metadata_json, created_at, updated_at, retention_expires_at, "
                "original_retention_expires_at, legal_hold, deletion_state) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "workspace-abort-test", session_id, run_id, job_id,
                    "staged", "object", "version", json.dumps(metadata, sort_keys=True),
                    now, now, "2030-01-01T00:00:00Z", "2030-01-01T00:00:00Z", 0, "active",
                ),
            )
            connection.commit()
            self.assertTrue(self.storage._object_owner_request_matches(connection, plan))
            for column, value in (
                ("session_id", "quote-substituted-session"),
                ("job_id", "job-substituted"),
                ("state", "failed"),
            ):
                with self.subTest(column=column):
                    connection.execute(
                        f"update sqag_quote_publication_versions set {column} = ? "
                        "where workspace_id = ? and run_id = ?",
                        (value, "workspace-abort-test", run_id),
                    )
                    self.assertFalse(self.storage._object_owner_request_matches(connection, plan))
                    connection.execute(
                        f"update sqag_quote_publication_versions set {column} = ? "
                        "where workspace_id = ? and run_id = ?",
                        ({"session_id": session_id, "job_id": job_id, "state": "staged"}[column],
                         "workspace-abort-test", run_id),
                    )
                    self.assertTrue(self.storage._object_owner_request_matches(connection, plan))

    def test_profile_cleanup_guard_is_exact_and_expired_claim_stays_blocked(self):
        self.run_save(self.storage, b"old-layout", "first")
        _result, plan = self.run_save(self.storage, b"new-layout", "replacement")
        self.assertTrue(plan.cleanup)
        snapshot = plan.cleanup[0]["snapshot"]
        cleanup = [
            dict(item, state="delete_started", lease_expires_at="2999-01-01T00:00:00Z")
            for item in plan.cleanup
        ]
        connection = sqlite3.connect(Path(self.temp.name) / "sqag.sqlite3")
        try:
            encoded = json.dumps(cleanup, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "update sqag_object_artifact_operations set cleanup_json = ? "
                "where workspace_id = ? and owner_type = ? and owner_id = ? and operation_seq = ?",
                (encoded, "workspace-abort-test", "profile", "profile-abort-test", plan.operation_seq),
            )
            connection.commit()

            def insert_version(run_id, artifact_kind, filename, checksum):
                metadata = {"exports": {artifact_kind: {
                    "filename": filename, "sha256": checksum,
                }}}
                connection.execute(
                    "insert into sqag_quote_publication_versions "
                    "(workspace_id, session_id, run_id, job_id, state, artifact_storage_mode, "
                    "artifact_source, metadata_json, created_at, updated_at, retention_expires_at, "
                    "original_retention_expires_at, legal_hold, deletion_state) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "workspace-abort-test", "quote-guard-session", run_id,
                        "job-guard-session", "staged", "object", "version",
                        json.dumps(metadata, sort_keys=True), "2026-09-29T00:00:00Z",
                        "2026-09-29T00:00:00Z", "2030-01-01T00:00:00Z",
                        "2030-01-01T00:00:00Z", 0, "active",
                    ),
                )

            with self.assertRaisesRegex(sqlite3.IntegrityError, "cleanup policy is busy"):
                insert_version(
                    "run-guard-match", snapshot["artifact_kind"],
                    snapshot["filename"], snapshot["checksum_sha256"],
                )
            connection.rollback()
            insert_version("run-guard-unrelated", "xlsx", "quotation.xlsx", "a" * 64)
            connection.execute(
                "insert into sqag_legal_holds "
                "(hold_id, workspace_id, target_type, target_id, enabled, reason_code, "
                "actor_tracking_id, actor_key_version, created_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "hold-unrelated", "workspace-abort-test", "generation_run",
                    "run-guard-unrelated", 1, "synthetic_test", "actor-synthetic",
                    "test-v1", "2026-09-29T00:00:00Z",
                ),
            )
            connection.commit()

            expired = [
                dict(item, state="delete_started", lease_expires_at="2000-01-01T00:00:00Z")
                for item in plan.cleanup
            ]
            connection.execute(
                "update sqag_object_artifact_operations set cleanup_json = ? "
                "where workspace_id = ? and owner_type = ? and owner_id = ? and operation_seq = ?",
                (
                    json.dumps(expired, sort_keys=True, separators=(",", ":")),
                    "workspace-abort-test", "profile", "profile-abort-test", plan.operation_seq,
                ),
            )
            connection.commit()
            with self.assertRaisesRegex(sqlite3.IntegrityError, "cleanup policy is busy"):
                insert_version(
                    "run-guard-expired", snapshot["artifact_kind"],
                    snapshot["filename"], snapshot["checksum_sha256"],
                )
            connection.rollback()
        finally:
            connection.close()

    def active_profile_snapshot(self, storage):
        with storage.connection() as connection:
            rows = storage._object_artifact_rows_for_kinds(
                connection,
                "profile",
                "profile-abort-test",
                {"quotation_layout"},
            )
        return rows.get("quotation_layout")

    def test_store_effect_then_lost_ack_retries_reserved_incarnation_after_restart(self):
        self.run_save(self.storage, b"old-layout", "first")
        old = self.active_profile_snapshot(self.storage)
        original_store = self.backend.store_artifact
        store_calls = 0
        metadata_after_effect = {}

        def apply_then_lose_ack(**kwargs):
            nonlocal store_calls
            store_calls += 1
            metadata = original_store(**kwargs)
            if store_calls == 1:
                metadata_after_effect["value"] = metadata
                raise webapp.ObjectStorageContractError("synthetic lost store acknowledgement")
            return metadata

        with mock.patch.object(self.backend, "store_artifact", side_effect=apply_then_lose_ack):
            with self.assertRaises(webapp.ObjectStorageContractError):
                self.run_save(self.storage, b"new-layout", "replacement")

        with self.storage.connection() as connection:
            operations = connection.execute(
                "select state, plan_json from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? "
                "order by operation_seq",
                ("workspace-abort-test", "profile", "profile-abort-test"),
            ).fetchall()
        self.assertEqual([row["state"] for row in operations], ["published", "prepared"])
        self.assertNotIn("old-layout", operations[1]["plan_json"])
        self.assertEqual(self.active_profile_snapshot(self.storage), old)
        reserved = metadata_after_effect["value"]
        self.assertEqual(
            self.backend.retrieve_artifact(reserved, workspace_id="workspace-abort-test"),
            b"new-layout",
        )

        restarted = self.new_storage()
        _result, retry_plan = self.run_save(restarted, b"new-layout", "replacement")
        restarted._finalize_object_artifact_batch(retry_plan)
        current = self.active_profile_snapshot(restarted)
        self.assertNotEqual(current.object_key_ref, old.object_key_ref)
        self.assertEqual(current.object_key_ref, reserved.storage_key)
        self.assertEqual(
            restarted._retrieve_object_artifact_snapshot(current)["content"],
            b"new-layout",
        )
        with restarted.connection() as connection:
            states = connection.execute(
                "select state from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? "
                "order by operation_seq",
                ("workspace-abort-test", "profile", "profile-abort-test"),
            ).fetchall()
        self.assertEqual([row["state"] for row in states], ["published", "published"])

    def test_publication_commit_failure_keeps_predecessor_and_retries_same_successor(self):
        self.run_save(self.storage, b"old-layout", "first")
        old = self.active_profile_snapshot(self.storage)
        patcher, state = self.commit_fault_patch(
            self.storage, target_commit=2, apply_before_raise=False
        )
        with patcher:
            with self.assertRaises(webapp.ObjectStorageContractError):
                self.run_save(self.storage, b"new-layout", "replacement")
        self.assertTrue(state["injected"])
        self.assertEqual(self.active_profile_snapshot(self.storage), old)
        self.assertEqual(len(self.backend._objects), 2)
        with self.storage.connection() as connection:
            operation = connection.execute(
                "select state, plan_json from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? "
                "order by operation_seq desc limit 1",
                ("workspace-abort-test", "profile", "profile-abort-test"),
            ).fetchone()
        self.assertEqual(operation["state"], "prepared")
        reserved_key = json.loads(operation["plan_json"])["successors"]["quotation_layout"]["object_key_ref"]
        self.assertIn(reserved_key, {metadata.storage_key for metadata in self.backend._metadata.values()})

        restarted = self.new_storage()
        _result, retry_plan = self.run_save(restarted, b"new-layout", "replacement")
        restarted._finalize_object_artifact_batch(retry_plan)
        current = self.active_profile_snapshot(restarted)
        self.assertEqual(current.object_key_ref, reserved_key)
        self.assertEqual(
            restarted._retrieve_object_artifact_snapshot(current)["content"],
            b"new-layout",
        )
        with restarted.connection() as connection:
            states = connection.execute(
                "select state from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? "
                "order by operation_seq",
                ("workspace-abort-test", "profile", "profile-abort-test"),
            ).fetchall()
        self.assertEqual([row["state"] for row in states], ["published", "published"])

    def test_unknown_commit_preserves_both_incarnations_until_fresh_retry(self):
        self.run_save(self.storage, b"old-layout", "first")
        old = self.active_profile_snapshot(self.storage)
        patcher, state = self.commit_fault_patch(
            self.storage, target_commit=2, apply_before_raise=True
        )
        with (
            patcher,
            mock.patch.object(
                self.storage,
                "_object_artifact_operation_row_after_uncertain_commit",
                return_value=False,
            ),
        ):
            with self.assertRaises(webapp.ObjectStorageContractError):
                self.run_save(self.storage, b"new-layout", "replacement")
        self.assertTrue(state["injected"])
        current = self.active_profile_snapshot(self.storage)
        self.assertNotEqual(current.object_key_ref, old.object_key_ref)
        self.assertEqual(len(self.backend._objects), 2)
        self.assertEqual(
            self.backend.retrieve_artifact(
                self.backend._metadata[old.object_key_ref],
                workspace_id="workspace-abort-test",
            ),
            b"old-layout",
        )
        self.assertEqual(
            self.backend.retrieve_artifact(
                self.backend._metadata[current.object_key_ref],
                workspace_id="workspace-abort-test",
            ),
            b"new-layout",
        )

        restarted = self.new_storage()
        _result, retry_plan = self.run_save(restarted, b"new-layout", "replacement")
        restarted._finalize_object_artifact_batch(retry_plan)
        final = self.active_profile_snapshot(restarted)
        self.assertEqual(final, current)
        self.assertEqual(len(self.backend._objects), 1)
        self.assertEqual(
            restarted._retrieve_object_artifact_snapshot(final)["content"],
            b"new-layout",
        )

    def test_delayed_delete_past_lease_expiry_keeps_matching_publication_blocked(self):
        self.run_save(self.storage, b"old-layout", "first")
        old = self.active_profile_snapshot(self.storage)
        _result, replacement_plan = self.run_save(
            self.storage, b"middle-layout", "replacement"
        )
        entered = threading.Event()
        release = threading.Event()
        original_delete = self.backend.delete_artifact
        delete_calls = 0
        lease_expiry = (
            dt.datetime.now(dt.UTC) + dt.timedelta(milliseconds=50)
        ).isoformat().replace("+00:00", "Z")

        def delayed_delete(metadata, *, workspace_id):
            nonlocal delete_calls
            if metadata.storage_key == old.object_key_ref:
                delete_calls += 1
                if delete_calls == 1:
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("Timed out waiting to release delayed predecessor delete.")
            return original_delete(metadata, workspace_id=workspace_id)

        thread_errors = []
        def finalize_replacement():
            try:
                self.storage._finalize_object_artifact_batch(replacement_plan)
            except Exception as exc:
                thread_errors.append(exc)

        with mock.patch.object(
            self.storage, "_object_artifact_cleanup_lease_expiry", return_value=lease_expiry
        ), mock.patch.object(self.backend, "delete_artifact", side_effect=delayed_delete):
            cleanup_thread = threading.Thread(target=finalize_replacement, daemon=True)
            cleanup_thread.start()
            self.assertTrue(entered.wait(5), "Predecessor cleanup did not reach the provider.")
            self.assertFalse(release.wait(0.08), "Provider delay ended before the claim expired.")
            self.assertGreater(
                dt.datetime.now(dt.UTC), dt.datetime.fromisoformat(lease_expiry[:-1] + "+00:00")
            )

            snapshot = replacement_plan.cleanup[0]["snapshot"]
            metadata = {"exports": {snapshot["artifact_kind"]: {
                "filename": snapshot["filename"],
                "sha256": snapshot["checksum_sha256"],
            }}}
            blocked_writer = sqlite3.connect(
                Path(self.temp.name) / "sqag.sqlite3", timeout=0.05
            )
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    blocked_writer.execute(
                        "insert into sqag_quote_publication_versions "
                        "(workspace_id, session_id, run_id, job_id, state, artifact_storage_mode, "
                        "artifact_source, metadata_json, created_at, updated_at, retention_expires_at, "
                        "original_retention_expires_at, legal_hold, deletion_state) "
                        "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            "workspace-abort-test", "quote-expiry-guard", "run-expiry-guard",
                            "job-expiry-guard", "staged", "object", "version",
                            json.dumps(metadata, sort_keys=True), "2026-09-29T00:00:00Z",
                            "2026-09-29T00:00:00Z", "2030-01-01T00:00:00Z",
                            "2030-01-01T00:00:00Z", 0, "active",
                        ),
                    )
                blocked_writer.rollback()
            finally:
                blocked_writer.close()

            restarted = self.new_storage()
            reconcile_results = []
            reconcile_errors = []
            reconcile_thread = threading.Thread(
                target=lambda: self._capture_thread_result(
                    lambda: reconcile_results.append(
                        restarted.reconcile_object_artifact_lifecycle(
                            "profile", "profile-abort-test", apply_cleanup=True
                        )
                    ),
                    reconcile_errors,
                ),
                daemon=True,
            )
            reconcile_thread.start()

            save_results = []
            save_errors = []
            save_thread = threading.Thread(
                target=lambda: self._capture_thread_result(
                    lambda: save_results.append(
                        self.run_save(restarted, b"old-layout", "same-bytes-again")
                    ),
                    save_errors,
                ),
                daemon=True,
            )
            save_thread.start()
            self.assertFalse(release.wait(0.05), "Provider delay ended before queued work was checked.")
            release.set()
            cleanup_thread.join(timeout=5)
            reconcile_thread.join(timeout=5)
            save_thread.join(timeout=5)
            self.assertFalse(cleanup_thread.is_alive())
            self.assertFalse(reconcile_thread.is_alive())
            self.assertFalse(save_thread.is_alive())
            self.assertEqual(thread_errors, [])
            self.assertEqual(reconcile_errors, [])
            self.assertEqual(save_errors, [])
            self.assertEqual(delete_calls, 1)
            self.assertTrue(reconcile_results)
            self.assertTrue(save_results)
            _result, next_plan = save_results[0]
            restarted._finalize_object_artifact_batch(next_plan)

        current = self.active_profile_snapshot(restarted)
        self.assertNotEqual(current.object_key_ref, old.object_key_ref)
        self.assertEqual(
            restarted._retrieve_object_artifact_snapshot(current)["content"],
            b"old-layout",
        )
        with restarted.connection() as connection:
            operation = connection.execute(
                "select cleanup_json from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? and operation_seq = ?",
                ("workspace-abort-test", "profile", "profile-abort-test", replacement_plan.operation_seq),
            ).fetchone()
        self.assertEqual(json.loads(operation["cleanup_json"])[0]["state"], "deleted")

    @staticmethod
    def _capture_thread_result(action, errors):
        try:
            action()
        except Exception as exc:
            errors.append(exc)

    def test_delayed_store_after_abort_is_never_authoritative_and_uses_new_incarnation(self):
        delayed: dict[str, object] = {}
        original_store = self.backend.store_artifact
        first_prepare, first_persist = self.lifecycle_callbacks(b"old-layout", "first")

        def lose_first_store(**kwargs):
            delayed.update(kwargs)
            raise webapp.ObjectStorageContractError("synthetic lost store acknowledgement")

        with mock.patch.object(self.backend, "store_artifact", side_effect=lose_first_store):
            with self.assertRaises(webapp.ObjectStorageContractError):
                self.storage._run_object_lifecycle_save(
                    "profile",
                    "profile-abort-test",
                    first_prepare,
                    first_persist,
                )

        with self.storage.connection() as connection:
            prepared = connection.execute(
                "select state from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? and operation_seq = ?",
                ("workspace-abort-test", "profile", "profile-abort-test", 1),
            ).fetchone()
        self.assertEqual(prepared["state"], "prepared")
        self.assertEqual(self.backend._objects, {})

        replacement_prepare, replacement_persist = self.lifecycle_callbacks(
            b"new-layout", "replacement"
        )
        self.storage._run_object_lifecycle_save(
            "profile",
            "profile-abort-test",
            replacement_prepare,
            replacement_persist,
        )

        with self.storage.connection() as connection:
            rows = connection.execute(
                "select operation_seq, operation_id, state, plan_json, cleanup_json "
                "from sqag_object_artifact_operations where workspace_id = ? "
                "and owner_type = ? and owner_id = ? order by operation_seq",
                ("workspace-abort-test", "profile", "profile-abort-test"),
            ).fetchall()
            active = self.storage._object_artifact_rows_for_kinds(
                connection,
                "profile",
                "profile-abort-test",
                {"quotation_layout"},
            )["quotation_layout"]
        self.assertEqual([row["state"] for row in rows], ["aborted", "published"])
        self.assertNotEqual(rows[0]["operation_id"], rows[1]["operation_id"])
        delayed_metadata = original_store(**delayed)
        self.assertNotEqual(active.object_key_ref, delayed_metadata.storage_key)

        fresh_storage = self.new_storage()
        with fresh_storage.connection() as connection:
            fresh_active = fresh_storage._object_artifact_rows_for_kinds(
                connection,
                "profile",
                "profile-abort-test",
                {"quotation_layout"},
            )["quotation_layout"]
        self.assertEqual(
            fresh_storage._retrieve_object_artifact_snapshot(fresh_active),
            {
                "filename": "layout.xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "size_bytes": len(b"new-layout"),
                "content": b"new-layout",
            },
        )
        self.assertEqual(
            self.backend.retrieve_artifact(delayed_metadata, workspace_id="workspace-abort-test"),
            b"old-layout",
        )
        fresh_storage.reconcile_object_artifact_lifecycle(
            "profile", "profile-abort-test", apply_cleanup=True
        )
        with self.assertRaises(webapp.ObjectStorageNotFoundError):
            self.backend.retrieve_artifact(
                delayed_metadata,
                workspace_id="workspace-abort-test",
            )



    def test_predecessor_delete_failure_keeps_published_successor_and_recovers_from_journal(self):
        self.run_save(self.storage, b"old-layout", "first")
        old = self.active_profile_snapshot(self.storage)
        old_metadata = self.backend._metadata[old.object_key_ref]
        _result, replacement_plan = self.run_save(self.storage, b"new-layout", "replacement")
        original_delete = self.backend.delete_artifact
        attempted = False

        def fail_before_old_delete(metadata, *, workspace_id):
            nonlocal attempted
            if metadata.storage_key == old.object_key_ref and not attempted:
                attempted = True
                return False
            return original_delete(metadata, workspace_id=workspace_id)

        with mock.patch.object(self.backend, "delete_artifact", side_effect=fail_before_old_delete):
            self.storage._finalize_object_artifact_batch(replacement_plan)

        self.assertTrue(attempted)
        published = self.active_profile_snapshot(self.storage)
        self.assertNotEqual(published.object_key_ref, old.object_key_ref)
        self.assertEqual(self.storage._retrieve_object_artifact_snapshot(published)["content"], b"new-layout")
        self.assertEqual(self.backend.retrieve_artifact(old_metadata, workspace_id="workspace-abort-test"), b"old-layout")
        with self.storage.connection() as connection:
            row = connection.execute(
                "select state, cleanup_json from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? and operation_seq = ?",
                ("workspace-abort-test", "profile", "profile-abort-test", replacement_plan.operation_seq),
            ).fetchone()
        self.assertEqual(row["state"], "published")
        self.assertEqual(json.loads(row["cleanup_json"])[0]["state"], "pending")

        restarted = self.new_storage()
        restarted.reconcile_object_artifact_lifecycle("profile", "profile-abort-test", apply_cleanup=True)
        self.assertEqual(self.active_profile_snapshot(restarted), published)
        with self.assertRaises(webapp.ObjectStorageNotFoundError):
            self.backend.retrieve_artifact(old_metadata, workspace_id="workspace-abort-test")
        self.assertEqual(restarted._retrieve_object_artifact_snapshot(published)["content"], b"new-layout")

    def test_acknowledged_delete_still_present_keeps_reference_guard_until_delayed_effect(self):
        self.run_save(self.storage, b"old-layout", "first")
        old = self.active_profile_snapshot(self.storage)
        old_metadata = self.backend._metadata[old.object_key_ref]
        original_delete = self.backend.delete_artifact
        dispatches = []

        def acknowledge_before_effect(metadata, *, workspace_id):
            if metadata.storage_key == old.object_key_ref:
                dispatches.append(metadata)
                return True
            return original_delete(metadata, workspace_id=workspace_id)

        with mock.patch.object(
            self.backend, "delete_artifact", side_effect=acknowledge_before_effect
        ):
            _result, replacement_plan = self.run_save(
                self.storage, b"new-layout", "replacement"
            )
            self.storage._finalize_object_artifact_batch(replacement_plan)

        self.assertEqual(len(dispatches), 1)
        with self.storage.connection() as connection:
            row = connection.execute(
                "select cleanup_json from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? "
                "and operation_seq = ?",
                (
                    "workspace-abort-test", "profile", "profile-abort-test",
                    replacement_plan.operation_seq,
                ),
            ).fetchone()
        cleanup = json.loads(row["cleanup_json"])
        self.assertEqual(cleanup[0]["state"], "uncertain")
        self.assertEqual(
            self.backend.retrieve_artifact(old_metadata, workspace_id="workspace-abort-test"),
            b"old-layout",
        )

        matching_metadata = json.dumps({
            "exports": {
                old.artifact_kind: {
                    "sha256": old.checksum_sha256,
                    "filename": old.filename,
                }
            }
        }, ensure_ascii=True, sort_keys=True)
        now = webapp.utc_timestamp()
        with self.storage.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "insert into sqag_quote_publication_versions "
                    "(workspace_id, session_id, run_id, job_id, state, artifact_storage_mode, "
                    "artifact_source, metadata_json, error_code, created_at, updated_at, "
                    "retention_expires_at, original_retention_expires_at, legal_hold, deletion_state) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "workspace-abort-test", "matching-session", "run-matching-reference",
                        "job-matching-reference", "staged", "object", "version",
                        matching_metadata, None, now, now,
                        "2099-01-01T00:00:00Z", "2099-01-01T00:00:00Z", 0, "active",
                    ),
                )
            connection.rollback()

        original_delete(old_metadata, workspace_id="workspace-abort-test")
        self.storage.reconcile_object_artifact_lifecycle(
            "profile", "profile-abort-test", apply_cleanup=True
        )
        self.assertEqual(self.active_profile_snapshot(self.storage).object_key_ref,
                         replacement_plan.successor_rows["quotation_layout"].object_key_ref)
        with self.assertRaises(webapp.ObjectStorageNotFoundError):
            self.backend.retrieve_artifact(old_metadata, workspace_id="workspace-abort-test")
        with self.storage.connection() as connection:
            final_row = connection.execute(
                "select cleanup_json from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? "
                "and operation_seq = ?",
                (
                    "workspace-abort-test", "profile", "profile-abort-test",
                    replacement_plan.operation_seq,
                ),
            ).fetchone()
        self.assertEqual(json.loads(final_row["cleanup_json"])[0]["state"], "deleted")

    def test_predecessor_delete_effect_then_lost_ack_keeps_published_successor(self):
        self.run_save(self.storage, b"old-layout", "first")
        old = self.active_profile_snapshot(self.storage)
        old_metadata = self.backend._metadata[old.object_key_ref]
        _result, replacement_plan = self.run_save(self.storage, b"new-layout", "replacement")
        original_delete = self.backend.delete_artifact
        attempted = False

        def delete_then_lose_ack(metadata, *, workspace_id):
            nonlocal attempted
            result = original_delete(metadata, workspace_id=workspace_id)
            if metadata.storage_key == old.object_key_ref and not attempted:
                attempted = True
                raise webapp.ObjectStorageContractError("synthetic lost delete acknowledgement")
            return result

        with mock.patch.object(self.backend, "delete_artifact", side_effect=delete_then_lose_ack):
            self.storage._finalize_object_artifact_batch(replacement_plan)

        self.assertTrue(attempted)
        published = self.active_profile_snapshot(self.storage)
        self.assertNotEqual(published.object_key_ref, old.object_key_ref)
        self.assertEqual(self.storage._retrieve_object_artifact_snapshot(published)["content"], b"new-layout")
        with self.assertRaises(webapp.ObjectStorageNotFoundError):
            self.backend.retrieve_artifact(old_metadata, workspace_id="workspace-abort-test")
        with self.storage.connection() as connection:
            row = connection.execute(
                "select state, cleanup_json from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? "
                "order by operation_seq desc limit 1",
                ("workspace-abort-test", "profile", "profile-abort-test"),
            ).fetchone()
        self.assertEqual(row["state"], "published")
        self.assertEqual(json.loads(row["cleanup_json"])[0]["state"], "deleted")
        restarted = self.new_storage()
        restarted.reconcile_object_artifact_lifecycle(
            "profile", "profile-abort-test", apply_cleanup=True
        )
        self.assertEqual(self.active_profile_snapshot(restarted), published)
        self.assertEqual(restarted._retrieve_object_artifact_snapshot(published)["content"], b"new-layout")

    def test_journal_backup_restore_retries_prepared_store_with_fresh_storage(self):
        self.run_save(self.storage, b"old-layout", "first")
        original_store = self.backend.store_artifact
        staged = {}

        def apply_new_then_lose_ack(**kwargs):
            metadata = original_store(**kwargs)
            if kwargs["content"] == b"new-layout":
                staged["metadata"] = metadata
                raise webapp.ObjectStorageContractError("synthetic store acknowledgement lost before publication")
            return metadata

        with mock.patch.object(self.backend, "store_artifact", side_effect=apply_new_then_lose_ack):
            with self.assertRaises(webapp.ObjectStorageContractError):
                self.run_save(self.storage, b"new-layout", "replacement")

        backup_path = Path(self.temp.name) / "sqag-restored.sqlite3"
        with contextlib.closing(sqlite3.connect(Path(self.temp.name) / "sqag.sqlite3")) as source:
            with contextlib.closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
        restored = webapp.DatabaseSqagStorage(
            f"sqlite:///{backup_path.as_posix()}",
            "workspace-abort-test",
            role="maintenance",
            user_id="synthetic-backup-restore-test",
        )
        with restored.connection() as connection:
            operation = connection.execute(
                "select state from sqag_object_artifact_operations "
                "where workspace_id = ? and owner_type = ? and owner_id = ? "
                "order by operation_seq desc limit 1",
                ("workspace-abort-test", "profile", "profile-abort-test"),
            ).fetchone()
        self.assertEqual(operation["state"], "prepared")

        _result, retry_plan = self.run_save(restored, b"new-layout", "replacement")
        restored._finalize_object_artifact_batch(retry_plan)
        active = self.active_profile_snapshot(restored)
        self.assertEqual(active.object_key_ref, staged["metadata"].storage_key)
        self.assertEqual(restored._retrieve_object_artifact_snapshot(active)["content"], b"new-layout")

    def test_legacy_active_row_remains_readable_and_replacement_publishes_v2(self):
        self.run_save(self.storage, b"legacy-layout", "legacy")
        original = self.active_profile_snapshot(self.storage)
        original_metadata = self.storage._object_metadata_from_snapshot(original)
        legacy_key = webapp.object_artifact_key(
            workspace_id=original.workspace_id,
            owner_type=original.owner_type,
            owner_id=original.owner_id,
            artifact_kind=original.artifact_kind,
            filename=original.filename,
            checksum_sha256=original.checksum_sha256,
        )
        legacy = dataclasses.replace(original, artifact_id="legacy-artifact-authority-test", object_key_ref=legacy_key)
        legacy_metadata = self.storage._object_metadata_from_snapshot(legacy)
        self.backend.store_artifact(
            workspace_id=legacy_metadata.workspace_id,
            owner_type=legacy_metadata.owner_type,
            owner_id=legacy_metadata.owner_id,
            artifact_kind=legacy_metadata.artifact_kind,
            filename=legacy_metadata.filename,
            content_type=legacy_metadata.content_type,
            content=b"legacy-layout",
            artifact_id=legacy_metadata.artifact_id,
            platform_user_id=legacy_metadata.platform_user_id,
            session_id=legacy_metadata.session_id,
            job_id=legacy_metadata.job_id,
            binding_sha256=legacy_metadata.binding_sha256,
            created_at=legacy_metadata.created_at,
            updated_at=legacy_metadata.updated_at,
        )
        with self.storage.connection() as connection:
            connection.execute(
                "update sqag_object_artifacts set artifact_id = ?, object_key_ref = ? "
                "where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
                (legacy.artifact_id, legacy.object_key_ref, legacy.workspace_id, legacy.owner_type, legacy.owner_id, legacy.artifact_kind),
            )
            connection.commit()
        self.backend.delete_artifact(original_metadata, workspace_id="workspace-abort-test")
        self.assertEqual(self.storage._retrieve_object_artifact_snapshot(legacy)["content"], b"legacy-layout")

        _result, replacement_plan = self.run_save(self.storage, b"replacement-layout", "replace-legacy")
        self.storage._finalize_object_artifact_batch(replacement_plan)
        current = self.active_profile_snapshot(self.storage)
        self.assertIn("/v2/", current.object_key_ref)
        self.assertNotEqual(current.object_key_ref, legacy.object_key_ref)
        self.assertEqual(self.storage._retrieve_object_artifact_snapshot(current)["content"], b"replacement-layout")
        with self.assertRaises(webapp.ObjectStorageNotFoundError):
            self.backend.retrieve_artifact(legacy_metadata, workspace_id="workspace-abort-test")

if __name__ == "__main__":
    unittest.main()