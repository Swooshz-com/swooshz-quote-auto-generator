import contextlib
import importlib.util
import io
import json
import os
import sys
import time
import unittest
from unittest import mock
from pathlib import Path

from webapp import server as webapp


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_live_retention_delete.py"


def load_verifier():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("verify_live_retention_delete", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Live retention/delete verifier script is missing.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def local_test_directory():
    root = ROOT / "_tmp" / "tests" / "live-retention-delete"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"case-{time.time_ns()}"
    path.mkdir()
    yield path


def complete_env() -> dict[str, str]:
    return {
        "SQAG_LIVE_RETENTION_DELETE_EVIDENCE": "1",
        "SQAG_DATABASE_URL": "REDACTED_DB_TARGET_MARKER",
        "SQAG_OBJECT_STORAGE_PROVIDER": "REDACTED_PROVIDER_MARKER",
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "REDACTED_ENDPOINT_MARKER",
        "SQAG_OBJECT_STORAGE_BUCKET": "REDACTED_BUCKET_MARKER",
        "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "REDACTED_ACCESS_MARKER",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "REDACTED_SECRET_MARKER",
        "SQAG_STORAGE_MODE": "database",
        "SQAG_ARTIFACT_STORAGE_MODE": "object",
    }


def trusted_migration_report(verifier, **updates):
    migration_ids = list(verifier.MIGRATION_FILE_NAMES)
    report = {
        "status": "ready",
        "safeToApply": True,
        "ledgerState": "present",
        "expectedHead": migration_ids[-1],
        "appliedHead": migration_ids[-1],
        "appliedMigrationIds": migration_ids,
        "pendingMigrationIds": [],
        "blockers": [],
    }
    report.update(updates)
    return report


class FakeStorage:
    def __init__(
        self,
        *,
        workspace_id: str,
        fail_on: str = "",
        pairing_mismatch: bool = False,
        tombstone_returns_zero: bool = False,
        cleanup_fails: bool = False,
        runtime_export_available: bool = True,
        require_runtime_env: bool = False,
    ):
        self.workspace_id = workspace_id
        self.fail_on = fail_on
        self.pairing_mismatch = pairing_mismatch
        self.tombstone_returns_zero = tombstone_returns_zero
        self.cleanup_fails = cleanup_fails
        self.runtime_export_available = runtime_export_available
        self.require_runtime_env = require_runtime_env
        self.sessions = {}
        self.object_artifacts = {}

    def _maybe_fail(self, step: str) -> None:
        if self.fail_on == step:
            raise RuntimeError("private failure detail")

    def ensure_ready(self) -> None:
        self._maybe_fail("ensure_ready")

    def ensure_object_artifact_ready(self) -> None:
        self._maybe_fail("ensure_object_artifact_ready")

    def create_or_update_quote_session(self, payload: dict[str, object], **_kwargs):
        self._maybe_fail("write")
        session_id = str(payload["session_id"])
        metadata = dict(payload)
        self.sessions[session_id] = {"session_id": session_id, "metadata": metadata, **payload}
        return dict(self.sessions[session_id])

    def get_quote_session(self, session_id: str, **_kwargs):
        return self.sessions.get(session_id)

    def _upsert_object_quote_artifact(self, session_id, kind, filename, content_type, metadata) -> None:
        self._maybe_fail("write")
        checksum = "0" * 64 if self.pairing_mismatch else metadata.checksum_sha256
        self.object_artifacts[(session_id, kind)] = {
            "workspace_id": self.workspace_id,
            "owner_type": "generated_quote",
            "owner_id": session_id,
            "session_id": session_id,
            "artifact_kind": kind,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": metadata.size_bytes,
            "checksum_sha256": checksum,
            "object_key_ref": metadata.storage_key,
            "status": "active",
            "retention_status": "active",
            "deleted_at": None,
            "_metadata": metadata,
        }

    def object_artifact_row(self, session_id: str, kind: str):
        row = self.object_artifacts.get((session_id, kind))
        if row and row.get("status") == "active" and row.get("retention_status") == "active" and not row.get("deleted_at"):
            return row
        return None

    def object_artifact_rows_for_session(self, session_id: str):
        return [dict(row) for (owner_id, _kind), row in self.object_artifacts.items() if owner_id == session_id]

    def tombstone_object_quote_artifacts(self, session_id: str) -> int:
        self._maybe_fail("tombstone")
        if self.tombstone_returns_zero:
            return 0
        count = 0
        for (owner_id, _kind), row in list(self.object_artifacts.items()):
            if owner_id == session_id and not row.get("deleted_at"):
                row["status"] = "deleted"
                row["retention_status"] = "deleted"
                row["deleted_at"] = "2026-07-07T00:00:00Z"
                count += 1
        return count

    def quote_session_export_artifact(self, session_id: str, kind: str):
        session = self.sessions.get(session_id) or {}
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        export = metadata.get("exports", {}).get(kind) if isinstance(metadata, dict) else None
        status = metadata.get("status", {}) if isinstance(metadata, dict) else {}
        if (
            self.runtime_export_available
            and (
                not self.require_runtime_env
                or (
                    os.environ.get("SQAG_STORAGE_MODE") == "database"
                    and os.environ.get("SQAG_ARTIFACT_STORAGE_MODE") == "object"
                )
            )
            and self.object_artifact_row(session_id, kind)
            and isinstance(export, dict)
            and export.get("filename") == "quotation.xlsx"
            and not export.get("stale")
            and status.get(f"{kind}_exported") is True
        ):
            row = self.object_artifacts[(session_id, kind)]
            content = webapp.configured_object_storage_backend().retrieve_artifact(row["_metadata"], workspace_id=self.workspace_id)
            return {
                "filename": "quotation.xlsx",
                "content_type": row["content_type"],
                "size_bytes": row["size_bytes"],
                "content": content,
            }
        return None

    def delete_quote_session(self, session_id: str) -> bool:
        if self.cleanup_fails:
            raise RuntimeError("private cleanup detail")
        self.sessions.pop(session_id, None)
        self.object_artifacts = {key: value for key, value in self.object_artifacts.items() if key[0] != session_id}
        return True


class FakeBackend:
    def __init__(
        self,
        metadata_cls,
        *,
        fail_on: str = "",
        tamper_retrieve: bool = False,
        delete_fails: bool = False,
        repeated_delete_unsafe: bool = False,
        confirmed_missing_after_delete: bool = False,
        cleanup_fails: bool = False,
    ):
        self.metadata_cls = metadata_cls
        self.fail_on = fail_on
        self.tamper_retrieve = tamper_retrieve
        self.delete_fails = delete_fails
        self.repeated_delete_unsafe = repeated_delete_unsafe
        self.confirmed_missing_after_delete = confirmed_missing_after_delete
        self.cleanup_fails = cleanup_fails
        self.objects = {}
        self.metadata = {}
        self.delete_calls = 0

    def _maybe_fail(self, step: str) -> None:
        if self.fail_on == step:
            raise RuntimeError("private backend detail")

    def store_artifact(self, *, workspace_id, owner_type, owner_id, artifact_kind, filename, content_type, content):
        self._maybe_fail("write")
        checksum = __import__("hashlib").sha256(bytes(content)).hexdigest()
        key = f"OPAQUE-STORAGE-REF-{workspace_id}-{owner_id}-{artifact_kind}"
        metadata = self.metadata_cls(
            workspace_id=workspace_id,
            owner_type=owner_type,
            owner_id=owner_id,
            artifact_kind=artifact_kind,
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            checksum_sha256=checksum,
            storage_key=key,
            created_at="2026-07-07T00:00:00Z",
            updated_at="2026-07-07T00:00:00Z",
        )
        self.objects[key] = bytes(content)
        self.metadata[key] = metadata
        return metadata

    def retrieve_artifact(self, metadata, *, workspace_id):
        self._maybe_fail("read")
        if metadata.workspace_id != workspace_id:
            raise webapp.ObjectStorageContractError("Artifact is not available for this workspace.")
        try:
            content = self.objects[metadata.storage_key]
        except KeyError as exc:
            if self.repeated_delete_unsafe:
                raise RuntimeError("private repeated-delete uncertainty") from exc
            raise webapp.ObjectStorageNotFoundError("Artifact is not available.") from exc
        if self.tamper_retrieve:
            return content + b"x"
        return content

    def delete_artifact(self, metadata, *, workspace_id):
        self.delete_calls += 1
        if self.cleanup_fails or self.delete_fails:
            raise RuntimeError("private delete detail")
        if metadata.workspace_id != workspace_id:
            raise RuntimeError("private workspace detail")
        existed = metadata.storage_key in self.objects
        if not existed and self.repeated_delete_unsafe:
            return False
        if not existed and self.confirmed_missing_after_delete:
            raise webapp.ObjectStorageContractError("Artifact metadata verification failed.")
        self.objects.pop(metadata.storage_key, None)
        self.metadata.pop(metadata.storage_key, None)
        return True


def run_injected_drill(verifier, *, fail_storage="", fail_backend="", backend_factory_fails=False, pairing_mismatch=False, tombstone_returns_zero=False, tamper_retrieve=False, delete_fails=False, repeated_delete_unsafe=False, confirmed_missing_after_delete=False, cleanup_fails=False, runtime_export_available=True, require_runtime_env=False):
    storages = {}
    backend_holder = {}

    def storage_factory(_database_url, workspace_id, **_kwargs):
        storage = FakeStorage(
            workspace_id=workspace_id,
            fail_on=fail_storage,
            pairing_mismatch=pairing_mismatch,
            tombstone_returns_zero=tombstone_returns_zero,
            cleanup_fails=cleanup_fails,
            runtime_export_available=runtime_export_available,
            require_runtime_env=require_runtime_env,
        )
        storages[workspace_id] = storage
        return storage

    def backend_factory(_env):
        if backend_factory_fails:
            raise RuntimeError("private backend config detail")
        backend = FakeBackend(
            verifier.ObjectArtifactMetadata,
            fail_on=fail_backend,
            tamper_retrieve=tamper_retrieve,
            delete_fails=delete_fails,
            repeated_delete_unsafe=repeated_delete_unsafe,
            confirmed_missing_after_delete=confirmed_missing_after_delete,
            cleanup_fails=cleanup_fails,
        )
        backend_holder["backend"] = backend
        return backend

    report = verifier.run_verification(
        env=complete_env(),
        storage_factory=storage_factory,
        backend_factory=backend_factory,
        migration_inspector=lambda _database_url: trusted_migration_report(verifier),
        test_injected_backend=True,
    )
    return report, storages, backend_holder


class LiveRetentionDeleteVerifierTest(unittest.TestCase):
    def run_preflight_case(self, verifier, migration_report=None, migration_error=None):
        called = {"storage": False, "backend": False}

        def storage_factory(*_args, **_kwargs):
            called["storage"] = True
            raise AssertionError("storage factory must not run before a trusted zero-pending preflight")

        def backend_factory(*_args, **_kwargs):
            called["backend"] = True
            raise AssertionError("backend factory must not run before a trusted zero-pending preflight")

        def migration_inspector(_database_url):
            if migration_error is not None:
                raise migration_error
            return migration_report

        report = verifier.run_verification(
            env=complete_env(),
            storage_factory=storage_factory,
            backend_factory=backend_factory,
            migration_inspector=migration_inspector,
            test_injected_backend=True,
        )
        return report, called

    def run_dependency_classification_case(
        self,
        verifier,
        *,
        inject_storage=False,
        inject_backend=False,
        inject_migration=False,
        explicit_test_flag=None,
        backend_failure=False,
        private_marker="PRIVATE-INJECTED-DEPENDENCY-DETAIL",
    ):
        def storage_factory(_database_url, workspace_id, **_kwargs):
            return FakeStorage(workspace_id=workspace_id)

        def backend_factory(_env):
            if backend_failure:
                raise RuntimeError(private_marker)
            return FakeBackend(verifier.ObjectArtifactMetadata)

        def migration_inspector(_database_url):
            return trusted_migration_report(verifier)

        kwargs = {}
        if inject_storage:
            kwargs["storage_factory"] = storage_factory
        if inject_backend:
            kwargs["backend_factory"] = backend_factory
        if inject_migration:
            kwargs["migration_inspector"] = migration_inspector
        if explicit_test_flag is not None:
            kwargs["test_injected_backend"] = explicit_test_flag

        with (
            mock.patch.object(verifier, "_build_default_storage", new=storage_factory),
            mock.patch.object(verifier, "_build_s3_backend", new=backend_factory),
            mock.patch.object(verifier, "_inspect_migration_readiness", new=migration_inspector),
        ):
            return verifier.run_verification(env=complete_env(), **kwargs)

    def test_missing_env_reports_blocked_without_values(self):
        verifier = load_verifier()
        report = verifier.run_verification(env={})
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("SQAG_LIVE_RETENTION_DELETE_EVIDENCE", report["missing_env_names"])
        self.assertIn("SQAG_DATABASE_URL", report["missing_env_names"])
        self.assertFalse(report["live_retention_delete_evidence_supported"])
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertNotIn("REDACTED_DB_TARGET_MARKER", text)

    def test_missing_opt_in_fails_closed(self):
        verifier = load_verifier()
        env = complete_env()
        env.pop("SQAG_LIVE_RETENTION_DELETE_EVIDENCE")

        report = verifier.run_verification(env=env)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("live_retention_delete_evidence_not_enabled_or_incomplete", report["blockers"])
        self.assertFalse(report["checks"]["live_evidence_opt_in_enabled"])

    def test_missing_database_runtime_mode_blocks_before_writes(self):
        verifier = load_verifier()
        env = complete_env()
        env.pop("SQAG_STORAGE_MODE")
        called = {"storage": False, "backend": False}

        def storage_factory(_database_url, _workspace_id, **_kwargs):
            called["storage"] = True
            raise AssertionError("storage factory should not be called")

        def backend_factory(_env):
            called["backend"] = True
            raise AssertionError("backend factory should not be called")

        report = verifier.run_verification(
            env=env,
            storage_factory=storage_factory,
            backend_factory=backend_factory,
            migration_inspector=lambda _database_url: trusted_migration_report(verifier),
            test_injected_backend=True,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("SQAG_STORAGE_MODE", report["missing_env_names"])
        self.assertIn("runtime_database_mode_not_enabled", report["blockers"])
        self.assertFalse(report["checks"]["write_attempted"])
        self.assertEqual(report["active_db_synthetic_rows_written"], 0)
        self.assertEqual(report["active_object_synthetic_objects_written"], 0)
        self.assertEqual(report["active_object_synthetic_objects_deleted"], 0)
        self.assertEqual(called, {"storage": False, "backend": False})

    def test_local_database_runtime_mode_blocks_before_writes(self):
        verifier = load_verifier()
        env = complete_env()
        env["SQAG_STORAGE_MODE"] = "local"

        report = verifier.run_verification(
            env=env,
            storage_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("storage factory should not be called")),
            backend_factory=lambda _env: (_ for _ in ()).throw(AssertionError("backend factory should not be called")),
            migration_inspector=lambda _database_url: trusted_migration_report(verifier),
            test_injected_backend=True,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertNotIn("SQAG_STORAGE_MODE", report["missing_env_names"])
        self.assertIn("runtime_database_mode_not_enabled", report["blockers"])
        self.assertFalse(report["checks"]["write_attempted"])
        self.assertEqual(report["active_db_synthetic_rows_written"], 0)
        self.assertEqual(report["active_object_synthetic_objects_written"], 0)

    def test_missing_object_artifact_runtime_mode_blocks_before_writes(self):
        verifier = load_verifier()
        env = complete_env()
        env.pop("SQAG_ARTIFACT_STORAGE_MODE")

        report = verifier.run_verification(
            env=env,
            storage_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("storage factory should not be called")),
            backend_factory=lambda _env: (_ for _ in ()).throw(AssertionError("backend factory should not be called")),
            migration_inspector=lambda _database_url: trusted_migration_report(verifier),
            test_injected_backend=True,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("SQAG_ARTIFACT_STORAGE_MODE", report["missing_env_names"])
        self.assertIn("runtime_object_artifact_mode_not_enabled", report["blockers"])
        self.assertFalse(report["checks"]["write_attempted"])
        self.assertEqual(report["active_db_synthetic_rows_written"], 0)
        self.assertEqual(report["active_object_synthetic_objects_written"], 0)

    def test_non_object_artifact_runtime_mode_blocks_before_writes(self):
        verifier = load_verifier()
        for mode in ("local", "database"):
            env = complete_env()
            env["SQAG_ARTIFACT_STORAGE_MODE"] = mode

            report = verifier.run_verification(
                env=env,
                storage_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("storage factory should not be called")),
                backend_factory=lambda _env: (_ for _ in ()).throw(AssertionError("backend factory should not be called")),
                migration_inspector=lambda _database_url: trusted_migration_report(verifier),
                test_injected_backend=True,
            )

            self.assertEqual(report["status"], "blocked")
            self.assertIn("runtime_object_artifact_mode_not_enabled", report["blockers"])
            self.assertFalse(report["checks"]["write_attempted"])
            self.assertEqual(report["active_db_synthetic_rows_written"], 0)
            self.assertEqual(report["active_object_synthetic_objects_written"], 0)

    def test_legacy_database_url_alone_does_not_satisfy_database_requirement(self):
        verifier = load_verifier()
        env = complete_env()
        env.pop("SQAG_DATABASE_URL")
        legacy_database_url = "K" + "QAG_DATABASE_URL"
        env[legacy_database_url] = "LEGACY_DB_TARGET_MARKER"

        report = verifier.run_verification(env=env)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("SQAG_DATABASE_URL", report["missing_env_names"])
        self.assertFalse(report["checks"]["active_database_target_present"])
        self.assertEqual(report["active_db_synthetic_rows_written"], 0)
        self.assertEqual(report["active_object_synthetic_objects_written"], 0)
        self.assertNotIn("LEGACY_DB_TARGET_MARKER", text)

    def test_legacy_runtime_modes_alone_do_not_satisfy_runtime_mode_requirement(self):
        verifier = load_verifier()
        env = complete_env()
        env.pop("SQAG_STORAGE_MODE")
        env.pop("SQAG_ARTIFACT_STORAGE_MODE")
        legacy_storage_mode = "K" + "QAG_STORAGE_MODE"
        legacy_artifact_mode = "K" + "QAG_ARTIFACT_STORAGE_MODE"
        env[legacy_storage_mode] = "database"
        env[legacy_artifact_mode] = "object"

        report = verifier.run_verification(
            env=env,
            storage_factory=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("storage factory should not be called")),
            backend_factory=lambda _env: (_ for _ in ()).throw(AssertionError("backend factory should not be called")),
            migration_inspector=lambda _database_url: trusted_migration_report(verifier),
            test_injected_backend=True,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("SQAG_STORAGE_MODE", report["missing_env_names"])
        self.assertIn("SQAG_ARTIFACT_STORAGE_MODE", report["missing_env_names"])
        self.assertNotIn(legacy_storage_mode, report["required_env_names"])
        self.assertNotIn(legacy_artifact_mode, report["required_env_names"])
        self.assertIn("runtime_database_mode_not_enabled", report["blockers"])
        self.assertIn("runtime_object_artifact_mode_not_enabled", report["blockers"])
        self.assertEqual(report["active_db_synthetic_rows_written"], 0)
        self.assertEqual(report["active_object_synthetic_objects_written"], 0)

    def test_runtime_download_uses_effective_env_instead_of_ambient_env(self):
        verifier = load_verifier()

        with mock.patch.dict(
            os.environ,
            {"SQAG_STORAGE_MODE": "local", "SQAG_ARTIFACT_STORAGE_MODE": "local"},
            clear=True,
        ):
            report, _storages, _backend_holder = run_injected_drill(
                verifier,
                require_runtime_env=True,
            )

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["checks"]["active_runtime_download_verified"])
        self.assertTrue(report["checks"]["deleted_metadata_download_denied"])

    def test_mocked_successful_live_drill_passes_with_sanitized_counts(self):
        verifier = load_verifier()

        report, _storages, backend_holder = run_injected_drill(verifier)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["live_retention_delete_evidence_supported"])
        self.assertTrue(report["test_injected_backend"])
        self.assertTrue(report["checks"]["db_metadata_active_verified"])
        self.assertTrue(report["checks"]["object_write_read_verified"])
        self.assertTrue(report["checks"]["active_runtime_download_verified"])
        self.assertTrue(report["checks"]["checksum_match"])
        self.assertTrue(report["checks"]["metadata_object_pairing_verified"])
        self.assertTrue(report["checks"]["tombstone_metadata_verified"])
        self.assertTrue(report["checks"]["object_delete_verified"])
        self.assertTrue(report["checks"]["deleted_metadata_download_denied"])
        self.assertTrue(report["checks"]["missing_object_fail_closed"])
        self.assertTrue(report["checks"]["wrong_workspace_denied"])
        self.assertTrue(report["checks"]["repeated_delete_safe"])
        self.assertTrue(report["checks"]["cleanup_completed"])
        self.assertEqual(report["active_db_synthetic_rows_written"], 2)
        self.assertEqual(report["active_object_synthetic_objects_written"], 1)
        self.assertEqual(report["active_object_synthetic_objects_deleted"], 1)
        self.assertEqual(report["db_blob_artifact_rows_written"], 0)
        self.assertFalse(report["production_ready"])
        self.assertEqual(report["blockers"], [])
        self.assertFalse(backend_holder["backend"].objects)
        for value in complete_env().values():
            if value in {"1", "database", "object", "ap-southeast-1"}:
                continue
            self.assertNotIn(value, text)
        self.assertNotIn("OPAQUE-STORAGE-REF", text)
        self.assertNotIn("private-payload", text)

    def test_dependency_injection_is_automatically_non_live_without_manual_flag(self):
        verifier = load_verifier()
        report = self.run_dependency_classification_case(
            verifier,
            inject_storage=True,
            inject_backend=True,
            inject_migration=True,
        )

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["test_injected_backend"])
        self.assertFalse(report["live_retention_delete_evidence_supported"])

    def test_each_injected_dependency_automatically_forces_non_live_classification(self):
        verifier = load_verifier()

        for dependency_name in ("storage", "backend", "migration"):
            with self.subTest(dependency=dependency_name):
                report = self.run_dependency_classification_case(
                    verifier,
                    inject_storage=dependency_name == "storage",
                    inject_backend=dependency_name == "backend",
                    inject_migration=dependency_name == "migration",
                )

                self.assertEqual(report["status"], "passed")
                self.assertTrue(report["test_injected_backend"])
                self.assertFalse(report["live_retention_delete_evidence_supported"])

    def test_explicit_false_cannot_override_automatic_injected_classification(self):
        verifier = load_verifier()
        report = self.run_dependency_classification_case(
            verifier,
            inject_storage=True,
            inject_backend=True,
            inject_migration=True,
            explicit_test_flag=False,
        )

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["test_injected_backend"])
        self.assertFalse(report["live_retention_delete_evidence_supported"])

    def test_explicit_true_remains_non_live_without_dependency_injection(self):
        verifier = load_verifier()
        report = self.run_dependency_classification_case(
            verifier,
            explicit_test_flag=True,
        )

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["test_injected_backend"])
        self.assertFalse(report["live_retention_delete_evidence_supported"])

    def test_failed_injected_verification_uses_the_same_non_live_classification(self):
        verifier = load_verifier()
        report = self.run_dependency_classification_case(
            verifier,
            inject_backend=True,
            backend_failure=True,
        )

        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["test_injected_backend"])
        self.assertFalse(report["live_retention_delete_evidence_supported"])

    def test_no_argument_dependency_path_is_not_automatically_classified_as_injected(self):
        verifier = load_verifier()
        report = self.run_dependency_classification_case(verifier)

        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["test_injected_backend"])
        self.assertTrue(report["live_retention_delete_evidence_supported"])

    def test_injected_dependency_details_are_not_serialized(self):
        verifier = load_verifier()
        private_marker = "PRIVATE-INJECTED-DEPENDENCY-DETAIL"
        report = self.run_dependency_classification_case(
            verifier,
            inject_backend=True,
            backend_failure=True,
            private_marker=private_marker,
        )

        self.assertTrue(report["test_injected_backend"])
        self.assertNotIn(private_marker, json.dumps(report, sort_keys=True))

    def test_verifier_never_calls_migration_appliers(self):
        verifier = load_verifier()
        import webapp.postgres_migrations as postgres_migrations

        with (
            mock.patch.object(webapp, "apply_sqag_storage_migrations", side_effect=AssertionError("must not apply")) as legacy_apply,
            mock.patch.object(postgres_migrations, "apply_postgres_migrations", side_effect=AssertionError("must not apply")) as postgres_apply,
        ):
            report, _storages, _backend = run_injected_drill(verifier)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["checks"]["migration_preflight_attempted"])
        self.assertTrue(report["checks"]["trusted_migration_ledger"])
        self.assertTrue(report["checks"]["zero_pending_migrations"])
        self.assertTrue(report["checks"]["migration_schema_ready"])
        self.assertEqual(report["migration_payload_statements_executed"], 0)
        legacy_apply.assert_not_called()
        postgres_apply.assert_not_called()

    def test_default_migration_inspector_executes_read_only_preflight_only(self):
        verifier = load_verifier()
        statements = []

        class Connection:
            def execute(self, statement):
                statements.append(statement)

            def rollback(self):
                statements.append("rollback")

        class ConnectionContext:
            def __enter__(self):
                return Connection()

            def __exit__(self, *_args):
                return False

        expected = trusted_migration_report(verifier)
        with (
            mock.patch.object(verifier.webapp, "postgres_storage_connection", return_value=ConnectionContext()),
            mock.patch.object(verifier, "migration_manifest", return_value=(object(),)),
            mock.patch.object(verifier, "inspect_postgres_migrations", return_value=expected) as inspect,
        ):
            report = verifier._inspect_migration_readiness("PRIVATE-CONNECTION-DETAIL")

        self.assertEqual(report, expected)
        self.assertEqual(statements, ["set transaction read only", "rollback"])
        inspect.assert_called_once()

    def test_pending_migration_blocks_before_evidence_writes(self):
        verifier = load_verifier()
        migration_ids = list(verifier.MIGRATION_FILE_NAMES)
        report, called = self.run_preflight_case(
            verifier,
            trusted_migration_report(
                verifier,
                appliedHead=migration_ids[-2],
                appliedMigrationIds=migration_ids[:-1],
                pendingMigrationIds=migration_ids[-1:],
            ),
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["blockers"], ["pending_migrations"])
        self.assertEqual(called, {"storage": False, "backend": False})
        self.assertFalse(report["checks"]["write_attempted"])

    def test_checksum_drift_blocks_before_evidence_writes(self):
        verifier = load_verifier()
        report, called = self.run_preflight_case(
            verifier,
            trusted_migration_report(verifier, status="unsafe", safeToApply=False, blockers=["checksum_drift:001_platform_scoped_storage.sql"]),
        )

        self.assertEqual(report["blockers"], ["migration_checksum_drift"])
        self.assertEqual(called, {"storage": False, "backend": False})

    def test_unknown_or_out_of_order_ledger_blocks_before_evidence_writes(self):
        verifier = load_verifier()
        report, called = self.run_preflight_case(
            verifier,
            trusted_migration_report(
                verifier,
                status="unsafe",
                safeToApply=False,
                appliedMigrationIds=["999_unknown.sql"],
                appliedHead="999_unknown.sql",
                blockers=["unknown_or_out_of_order_migration"],
            ),
        )

        self.assertEqual(report["blockers"], ["migration_ledger_untrusted"])
        self.assertEqual(called, {"storage": False, "backend": False})

    def test_missing_schema_trigger_or_routine_readiness_blocks_before_evidence_writes(self):
        verifier = load_verifier()
        report, called = self.run_preflight_case(
            verifier,
            trusted_migration_report(
                verifier,
                status="unsafe",
                safeToApply=False,
                blockers=["schema_ledger_inconsistent_missing_triggers:sqag_quote_sessions_delete_guard"],
            ),
        )

        self.assertEqual(report["blockers"], ["migration_schema_not_ready"])
        self.assertEqual(called, {"storage": False, "backend": False})

    def test_missing_ledger_blocks_before_evidence_writes(self):
        verifier = load_verifier()
        report, called = self.run_preflight_case(
            verifier,
            trusted_migration_report(
                verifier,
                ledgerState="missing",
                appliedHead=None,
                appliedMigrationIds=[],
                pendingMigrationIds=list(verifier.MIGRATION_FILE_NAMES),
            ),
        )

        self.assertEqual(report["blockers"], ["migration_ledger_missing"])
        self.assertEqual(called, {"storage": False, "backend": False})

    def test_migration_preflight_failure_is_sanitized_and_blocks_writes(self):
        verifier = load_verifier()
        private_detail = "postgresql://private-user:private-password@private-host/private-db"
        report, called = self.run_preflight_case(verifier, migration_error=RuntimeError(private_detail))
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["blockers"], ["migration_preflight_failed"])
        self.assertEqual(called, {"storage": False, "backend": False})
        self.assertNotIn(private_detail, text)

    def test_missing_active_runtime_download_fails_closed_before_tombstone(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, runtime_export_available=False)

        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["checks"]["active_runtime_download_verified"])
        self.assertFalse(report["checks"]["tombstone_attempted"])
        self.assertFalse(report["checks"]["deleted_metadata_download_denied"])
        self.assertIn("active_runtime_download_not_verified", report["blockers"])
        self.assertEqual(report["runtime_download_failure_stage"], "runtime_returned_none")

    def test_real_database_storage_fixture_completes_runtime_download_and_delete_lifecycle(self):
        verifier = load_verifier()
        with local_test_directory() as temp_dir:
            database_url = f"sqlite:///{(Path(temp_dir) / 'sqag.sqlite3').as_posix()}"
            env = complete_env()
            env[webapp.SQAG_DATABASE_URL_ENV_NAME] = database_url
            backend = webapp.InMemoryObjectStorageBackend()
            with mock.patch.dict(os.environ, env, clear=True):
                webapp.apply_sqag_storage_migrations(database_url)
                report = verifier.run_verification(
                    env=env,
                    storage_factory=verifier._build_default_storage,
                    backend_factory=lambda _env: backend,
                    migration_inspector=lambda _database_url: trusted_migration_report(verifier),
                    test_injected_backend=True,
                )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["runtime_download_failure_stage"], "")
        for check in (
            "db_metadata_active_verified",
            "object_write_read_verified",
            "active_runtime_download_verified",
            "checksum_match",
            "metadata_object_pairing_verified",
            "tombstone_metadata_verified",
            "object_delete_verified",
            "deleted_metadata_download_denied",
            "missing_object_fail_closed",
            "wrong_workspace_denied",
            "repeated_delete_safe",
            "cleanup_completed",
        ):
            self.assertTrue(report["checks"][check], check)

    def test_real_database_storage_unpublished_session_is_not_runtime_download_verified(self):
        verifier = load_verifier()
        with local_test_directory() as temp_dir:
            database_url = f"sqlite:///{(Path(temp_dir) / 'sqag.sqlite3').as_posix()}"
            env = complete_env()
            env[webapp.SQAG_DATABASE_URL_ENV_NAME] = database_url
            backend = webapp.InMemoryObjectStorageBackend()
            with mock.patch.dict(os.environ, env, clear=True):
                webapp.apply_sqag_storage_migrations(database_url)
                storage = verifier._build_default_storage(database_url, "workspace-unpublished")
                session_id = "quote-unpublished123"
                payload = b"synthetic-unpublished-runtime-download"
                metadata = backend.store_artifact(
                    workspace_id=storage.workspace_id,
                    owner_type="generated_quote",
                    owner_id=session_id,
                    artifact_kind="xlsx",
                    filename=verifier.SYNTHETIC_FILENAME,
                    content_type=verifier.SYNTHETIC_CONTENT_TYPE,
                    content=payload,
                )
                storage.create_or_update_quote_session(
                    {
                        "session_id": session_id,
                        "status": {"quote_generated": True, "xlsx_exported": True},
                        "exports": {
                            "xlsx": {
                                "filename": verifier.SYNTHETIC_FILENAME,
                                "created_at": metadata.created_at,
                                "size_bytes": metadata.size_bytes,
                                "sha256": metadata.checksum_sha256,
                                "stale": False,
                            }
                        },
                    },
                    session_id=session_id,
                )
                storage._upsert_object_quote_artifact(
                    session_id,
                    "xlsx",
                    verifier.SYNTHETIC_FILENAME,
                    verifier.SYNTHETIC_CONTENT_TYPE,
                    metadata,
                )
                diagnostics = {}
                verified = verifier._runtime_download_verified(
                    storage=storage,
                    session_id=session_id,
                    backend=backend,
                    env=env,
                    metadata=metadata,
                    payload=payload,
                    diagnostics=diagnostics,
                )

        self.assertFalse(verified)
        self.assertEqual(diagnostics["failure_stage"], "session_not_published")

    def test_runtime_download_report_drops_unallowlisted_failure_detail(self):
        verifier = load_verifier()
        private_detail = "private runtime target detail"
        report = verifier._report(
            status="failed",
            checks={},
            missing_env_names=[],
            blockers=["active_runtime_download_not_verified"],
            test_injected_backend=True,
            runtime_download_failure_stage=private_detail,
        )

        self.assertEqual(report["runtime_download_failure_stage"], "")
        self.assertNotIn(private_detail, json.dumps(report, sort_keys=True))

    def test_db_schema_failure_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, fail_storage="ensure_ready")

        self.assertEqual(report["status"], "failed")
        self.assertIn("database_connection_or_schema_failed", report["blockers"])

    def test_object_write_failure_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, fail_backend="write")

        self.assertEqual(report["status"], "failed")
        self.assertIn("object_write_failed", report["blockers"])

    def test_object_backend_config_failure_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, backend_factory_fails=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("object_backend_unavailable", report["blockers"])

    def test_object_read_failure_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, fail_backend="read")

        self.assertEqual(report["status"], "failed")
        self.assertIn("object_read_failed", report["blockers"])

    def test_metadata_pairing_mismatch_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, pairing_mismatch=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("metadata_object_pairing_mismatch", report["blockers"])

    def test_tombstone_failure_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, tombstone_returns_zero=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("tombstone_metadata_failed", report["blockers"])

    def test_object_delete_failure_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, delete_fails=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("object_delete_failed", report["blockers"])

    def test_checksum_mismatch_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, tamper_retrieve=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("checksum_mismatch", report["blockers"])

    def test_repeated_delete_unsafe_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, repeated_delete_unsafe=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("repeated_delete_not_safe", report["blockers"])

    def test_repeated_delete_accepts_authoritatively_confirmed_missing_object(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(
            verifier,
            confirmed_missing_after_delete=True,
        )

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["checks"]["repeated_delete_safe"])

    def test_generic_provider_outage_is_not_confirmed_missing(self):
        verifier = load_verifier()
        exc = webapp.ObjectStorageContractError("Artifact is not available.")
        self.assertFalse(verifier._is_confirmed_missing_error(exc))

    def test_cleanup_preserves_metadata_when_object_absence_is_unconfirmed(self):
        verifier = load_verifier()
        backend = FakeBackend(verifier.ObjectArtifactMetadata, cleanup_fails=True)
        metadata = backend.store_artifact(
            workspace_id="workspace-cleanup",
            owner_type="generated_quote",
            owner_id="quote-cleanup",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=b"synthetic-cleanup",
        )

        cleaned = verifier._cleanup(
            storage=None,
            backend=backend,
            metadata=metadata,
            ids={"session_a": "quote-cleanup"},
        )

        self.assertFalse(cleaned)
        self.assertIn(metadata.storage_key, backend.objects)

    def test_cleanup_failure_fails_closed(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, cleanup_fails=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("cleanup_failed", report["blockers"])
        self.assertFalse(report["checks"]["cleanup_completed"])

    def test_cli_output_is_metadata_only_and_nonzero(self):
        verifier = load_verifier()
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = verifier.main([])

        output = stdout.getvalue()
        report = json.loads(output)
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertNotIn("REDACTED_DB_TARGET_MARKER", output)
        self.assertNotIn("REDACTED_BUCKET_MARKER", output)


if __name__ == "__main__":
    unittest.main()
