import contextlib
import importlib.util
import io
import json
import sys
import unittest
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


def complete_env() -> dict[str, str]:
    return {
        "SQAG_LIVE_RETENTION_DELETE_EVIDENCE": "1",
        "KQAG_DATABASE_URL": "REDACTED_DB_TARGET_MARKER",
        "SQAG_OBJECT_STORAGE_PROVIDER": "REDACTED_PROVIDER_MARKER",
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "REDACTED_ENDPOINT_MARKER",
        "SQAG_OBJECT_STORAGE_BUCKET": "REDACTED_BUCKET_MARKER",
        "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "REDACTED_ACCESS_MARKER",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "REDACTED_SECRET_MARKER",
        "KQAG_STORAGE_MODE": "database",
        "KQAG_ARTIFACT_STORAGE_MODE": "object",
    }


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
    ):
        self.workspace_id = workspace_id
        self.fail_on = fail_on
        self.pairing_mismatch = pairing_mismatch
        self.tombstone_returns_zero = tombstone_returns_zero
        self.cleanup_fails = cleanup_fails
        self.runtime_export_available = runtime_export_available
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
        cleanup_fails: bool = False,
    ):
        self.metadata_cls = metadata_cls
        self.fail_on = fail_on
        self.tamper_retrieve = tamper_retrieve
        self.delete_fails = delete_fails
        self.repeated_delete_unsafe = repeated_delete_unsafe
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
            raise RuntimeError("private workspace detail")
        content = self.objects[metadata.storage_key]
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
        self.objects.pop(metadata.storage_key, None)
        self.metadata.pop(metadata.storage_key, None)
        return True


def run_injected_drill(verifier, *, fail_storage="", fail_backend="", backend_factory_fails=False, pairing_mismatch=False, tombstone_returns_zero=False, tamper_retrieve=False, delete_fails=False, repeated_delete_unsafe=False, cleanup_fails=False, runtime_export_available=True):
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
            cleanup_fails=cleanup_fails,
        )
        backend_holder["backend"] = backend
        return backend

    report = verifier.run_verification(
        env=complete_env(),
        storage_factory=storage_factory,
        backend_factory=backend_factory,
        migration_applier=lambda _database_url: None,
        test_injected_backend=True,
    )
    return report, storages, backend_holder


class LiveRetentionDeleteVerifierTest(unittest.TestCase):
    def test_missing_env_reports_blocked_without_values(self):
        verifier = load_verifier()
        report = verifier.run_verification(env={})
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("SQAG_LIVE_RETENTION_DELETE_EVIDENCE", report["missing_env_names"])
        self.assertIn("KQAG_DATABASE_URL", report["missing_env_names"])
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

    def test_missing_active_runtime_download_fails_closed_before_tombstone(self):
        verifier = load_verifier()
        report, _storages, _backend = run_injected_drill(verifier, runtime_export_available=False)

        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["checks"]["active_runtime_download_verified"])
        self.assertFalse(report["checks"]["tombstone_attempted"])
        self.assertFalse(report["checks"]["deleted_metadata_download_denied"])
        self.assertIn("active_runtime_download_not_verified", report["blockers"])

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
