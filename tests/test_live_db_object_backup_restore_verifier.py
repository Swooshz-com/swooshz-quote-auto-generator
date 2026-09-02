import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "verify_live_db_object_backup_restore.py"


def load_verifier():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("verify_live_db_object_backup_restore", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Live DB+object backup/restore verifier script is missing.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def complete_env() -> dict[str, str]:
    return {
        "SQAG_LIVE_DB_OBJECT_BACKUP_RESTORE_EVIDENCE": "1",
        "SQAG_DATABASE_URL": "ACTIVE_DB_TARGET_MARKER_A",
        "SQAG_MIGRATOR_DATABASE_URL": "ACTIVE_MIGRATOR_DB_TARGET_MARKER_A",
        "SQAG_MAINTENANCE_DATABASE_URL": "ACTIVE_MAINTENANCE_DB_TARGET_MARKER_A",
        "SQAG_OBJECT_STORAGE_PROVIDER": "ACTIVE_OBJECT_PROVIDER_MARKER_A",
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "ACTIVE_OBJECT_ENDPOINT_MARKER_A",
        "SQAG_OBJECT_STORAGE_BUCKET": "ACTIVE_OBJECT_BUCKET_MARKER_A",
        "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "ACTIVE_ACCESS_MARKER_A",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "ACTIVE_CREDENTIAL_MARKER_A",
        "SQAG_RESTORE_DATABASE_URL": "RESTORE_DB_TARGET_MARKER_B",
        "SQAG_RESTORE_MIGRATOR_DATABASE_URL": "RESTORE_MIGRATOR_DB_TARGET_MARKER_B",
        "SQAG_RESTORE_MAINTENANCE_DATABASE_URL": "RESTORE_MAINTENANCE_DB_TARGET_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_PROVIDER": "RESTORE_OBJECT_PROVIDER_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_ENDPOINT_URL": "RESTORE_OBJECT_ENDPOINT_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_BUCKET": "RESTORE_OBJECT_BUCKET_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_REGION": "ap-southeast-1",
        "SQAG_RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID": "RESTORE_ACCESS_MARKER_B",
        "SQAG_RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY": "RESTORE_CREDENTIAL_MARKER_B",
        "SQAG_BACKUP_RESTORE_DECISION_ID": "operator-approved-window",
        "SQAG_BACKUP_RESTORE_WINDOW_ID": "isolated-restore-window",
    }


class FakeStorage:
    def __init__(
        self,
        *,
        label: str,
        workspace_id: str,
        fail_on: str = "",
        pairing_mismatch: bool = False,
        state: dict[str, dict[object, dict[str, object]]] | None = None,
        runtime_filename_gate: bool = False,
    ):
        self.label = label
        self.workspace_id = workspace_id
        self.fail_on = fail_on
        self.pairing_mismatch = pairing_mismatch
        self.runtime_filename_gate = runtime_filename_gate
        self._state = state or {
            "profiles": {},
            "pricing": {},
            "sessions": {},
            "object_artifacts": {},
        }
        self.profiles = self._state["profiles"]
        self.pricing = self._state["pricing"]
        self.sessions = self._state["sessions"]
        self.object_artifacts = self._state["object_artifacts"]

    def _maybe_fail(self, step: str) -> None:
        if self.fail_on == step or self.fail_on == f"{self.label}_{step}":
            raise RuntimeError(f"{self.label} private failure detail")

    def ensure_ready(self) -> None:
        self._maybe_fail("ensure_ready")

    def ensure_object_artifact_ready(self) -> None:
        self._maybe_fail("ensure_object_artifact_ready")

    def save_profile(self, profile: dict[str, object]) -> dict[str, object]:
        self._maybe_fail("write")
        self.profiles[str(profile["id"])] = dict(profile)
        return dict(profile)

    def save_pricing_reference(self, reference: dict[str, object]) -> dict[str, object]:
        self._maybe_fail("write")
        self.pricing[str(reference["id"])] = dict(reference)
        return dict(reference)

    def create_or_update_quote_session(self, payload: dict[str, object], **_kwargs) -> dict[str, object]:
        self._maybe_fail("write")
        session_id = str(payload["session_id"])
        self.sessions[session_id] = dict(payload)
        return dict(payload)

    def _upsert_object_quote_artifact(
        self,
        session_id: str,
        kind: str,
        filename: str,
        content_type: str,
        metadata,
    ) -> None:
        self._maybe_fail("write")
        checksum = metadata.checksum_sha256
        if self.pairing_mismatch:
            checksum = "0" * 64
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
        }

    def list_company_profiles(self) -> list[dict[str, object]]:
        return list(self.profiles.values())

    def list_pricing_references(self) -> list[dict[str, object]]:
        return list(self.pricing.values())

    def list_quote_sessions(self) -> list[dict[str, object]]:
        return list(self.sessions.values())

    def profile_detail(self, profile_id: str, **_kwargs):
        return self.profiles.get(profile_id)

    def pricing_reference_detail(self, reference_id: str, **_kwargs):
        return self.pricing.get(reference_id)

    def get_quote_session(self, session_id: str, **_kwargs):
        return self.sessions.get(session_id)

    def object_artifact_row(self, session_id: str, kind: str):
        row = self.object_artifacts.get((session_id, kind))
        if (
            self.runtime_filename_gate
            and row
            and kind == "xlsx"
            and row.get("filename") != "quotation.xlsx"
        ):
            return None
        return row

    def delete_profile(self, profile_id: str) -> bool:
        self._maybe_fail("cleanup")
        return self.profiles.pop(profile_id, None) is not None

    def delete_pricing_reference(self, reference_id: str, **_kwargs) -> bool:
        self._maybe_fail("cleanup")
        return self.pricing.pop(reference_id, None) is not None

    def delete_quote_session(self, session_id: str) -> bool:
        self._maybe_fail("cleanup")
        self.object_artifacts = {
            key: value for key, value in self.object_artifacts.items() if key[0] != session_id
        }
        return self.sessions.pop(session_id, None) is not None


class FakeBackend:
    def __init__(
        self,
        metadata_cls,
        *,
        label: str,
        fail_on: str = "",
        tamper_retrieve: bool = False,
        cleanup_fails: bool = False,
        state: dict[str, dict[str, object]] | None = None,
    ):
        self.metadata_cls = metadata_cls
        self.label = label
        self.fail_on = fail_on
        self.tamper_retrieve = tamper_retrieve
        self.cleanup_fails = cleanup_fails
        self._state = state or {"objects": {}, "metadata": {}}
        self.objects = self._state["objects"]
        self.metadata = self._state["metadata"]

    def _maybe_fail(self, step: str) -> None:
        if self.fail_on == step or self.fail_on == f"{self.label}_{step}":
            raise RuntimeError(f"{self.label} private failure detail")

    def store_artifact(self, *, workspace_id, owner_type, owner_id, artifact_kind, filename, content_type, content):
        self._maybe_fail("write")
        key = f"OPAQUE-STORAGE-REF-{self.label}-{workspace_id}-{owner_id}-{artifact_kind}"
        checksum = __import__("hashlib").sha256(bytes(content)).hexdigest()
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
            raise RuntimeError("private workspace mismatch detail")
        content = self.objects[metadata.storage_key]
        if self.tamper_retrieve:
            return content[:-1] + bytes([(content[-1] + 1) % 256])
        return content

    def delete_artifact(self, metadata, *, workspace_id):
        if self.cleanup_fails:
            raise RuntimeError("private cleanup detail")
        if metadata.workspace_id != workspace_id:
            raise RuntimeError("private workspace mismatch detail")
        self.objects.pop(metadata.storage_key, None)
        self.metadata.pop(metadata.storage_key, None)
        return True


def run_injected_drill(
    verifier,
    *,
    fail_storage: str = "",
    fail_backend: str = "",
    tamper_restore_retrieve: bool = False,
    pairing_mismatch: bool = False,
    cleanup_fails: bool = False,
    shared_storage_state: bool = False,
    shared_backend_state: bool = False,
    runtime_filename_gate: bool = False,
):
    storages: dict[tuple[str, str], FakeStorage] = {}
    backends: dict[str, FakeBackend] = {}
    storage_states: dict[str, dict[str, dict[object, dict[str, object]]]] = {}
    backend_state: dict[str, dict[str, object]] | None = {"objects": {}, "metadata": {}} if shared_backend_state else None

    def storage_factory(label: str):
        def factory(_database_url: str, workspace_id: str, **_kwargs):
            state = None
            if shared_storage_state:
                state = storage_states.setdefault(
                    workspace_id,
                    {
                        "profiles": {},
                        "pricing": {},
                        "sessions": {},
                        "object_artifacts": {},
                    },
                )
            storage = FakeStorage(
                label=label,
                workspace_id=workspace_id,
                fail_on=fail_storage,
                pairing_mismatch=pairing_mismatch and label == "restore",
                state=state,
                runtime_filename_gate=runtime_filename_gate,
            )
            storages[(label, workspace_id)] = storage
            return storage

        return factory

    def backend_factory(label: str):
        def factory(_env):
            backend = FakeBackend(
                verifier.ObjectArtifactMetadata,
                label=label,
                fail_on=fail_backend,
                tamper_retrieve=tamper_restore_retrieve and label == "restore",
                cleanup_fails=cleanup_fails and label == "restore",
                state=backend_state,
            )
            backends[label] = backend
            return backend

        return factory

    report = verifier.run_verification(
        env=complete_env(),
        active_storage_factory=storage_factory("active"),
        restore_storage_factory=storage_factory("restore"),
        active_backend_factory=backend_factory("active"),
        restore_backend_factory=backend_factory("restore"),
        migration_applier=lambda _database_url: None,
        test_injected_backend=True,
    )
    return report, storages, backends


class TupleArtifactRow:
    FIELDS = (
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

    def __init__(self, **values):
        self.values = tuple(values.get(field) for field in self.FIELDS)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self.values[key]
        raise TypeError("tuple row indexes by integer only")


class LiveDbObjectBackupRestoreVerifierTest(unittest.TestCase):
    def test_missing_env_reports_blocked_preflight_without_values(self):
        verifier = load_verifier()
        report = verifier.run_verification(env={})
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])
        self.assertIn("SQAG_DATABASE_URL", report["missing_env_names"])
        self.assertIn("SQAG_RESTORE_DATABASE_URL", report["missing_env_names"])
        self.assertIn("blocked_isolated_restore_target_missing", report["blockers"])
        self.assertIn("blocked_backup_restore_decision_missing", report["blockers"])
        self.assertFalse(report["checks"]["isolated_restore_target_available"])
        self.assertFalse(report["checks"]["backup_ownership_decision_present"])
        self.assertEqual(report["privacy"]["output"], "metadata-only")
        self.assertNotIn("ACTIVE_DB_TARGET_MARKER_A", text)
        self.assertNotIn("ACTIVE_OBJECT_BUCKET_MARKER_A", text)

    def test_restore_targets_matching_active_targets_are_blocked(self):
        verifier = load_verifier()
        env = complete_env()
        env["SQAG_RESTORE_DATABASE_URL"] = env["SQAG_DATABASE_URL"]
        env["SQAG_RESTORE_OBJECT_STORAGE_ENDPOINT_URL"] = env["SQAG_OBJECT_STORAGE_ENDPOINT_URL"]
        env["SQAG_RESTORE_OBJECT_STORAGE_BUCKET"] = env["SQAG_OBJECT_STORAGE_BUCKET"]

        report = verifier.run_verification(env=env)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "blocked")
        self.assertIn("blocked_isolated_restore_target_missing", report["blockers"])
        self.assertFalse(report["checks"]["isolated_restore_target_available"])
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])
        self.assertNotIn(env["SQAG_DATABASE_URL"], text)
        self.assertNotIn(env["SQAG_OBJECT_STORAGE_BUCKET"], text)
        self.assertNotIn(env["SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY"], text)

    def test_restore_database_target_matching_active_target_is_blocked(self):
        verifier = load_verifier()
        env = complete_env()
        env["SQAG_RESTORE_DATABASE_URL"] = env["SQAG_DATABASE_URL"]

        report = verifier.run_verification(env=env)

        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["checks"]["database_targets_distinct"])
        self.assertIn("blocked_isolated_restore_target_missing", report["blockers"])

    def test_restore_object_target_matching_active_target_is_blocked(self):
        verifier = load_verifier()
        env = complete_env()
        env["SQAG_RESTORE_OBJECT_STORAGE_PROVIDER"] = env["SQAG_OBJECT_STORAGE_PROVIDER"]
        env["SQAG_RESTORE_OBJECT_STORAGE_ENDPOINT_URL"] = env["SQAG_OBJECT_STORAGE_ENDPOINT_URL"]
        env["SQAG_RESTORE_OBJECT_STORAGE_BUCKET"] = env["SQAG_OBJECT_STORAGE_BUCKET"]

        report = verifier.run_verification(env=env)

        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["checks"]["object_targets_distinct"])
        self.assertIn("blocked_isolated_restore_target_missing", report["blockers"])

    def test_missing_operator_decision_or_window_marker_is_blocked(self):
        verifier = load_verifier()
        for missing_name in ("SQAG_BACKUP_RESTORE_DECISION_ID", "SQAG_BACKUP_RESTORE_WINDOW_ID"):
            with self.subTest(missing_name=missing_name):
                env = complete_env()
                env.pop(missing_name)

                report = verifier.run_verification(env=env)

                self.assertEqual(report["status"], "blocked")
                self.assertIn("blocked_backup_restore_decision_missing", report["blockers"])
                self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])

    def test_complete_preflight_still_does_not_claim_live_evidence(self):
        verifier = load_verifier()
        report = verifier.run_verification(env=complete_env(), execute_live_drill=False)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["missing_env_names"], [])
        self.assertTrue(report["checks"]["backup_ownership_decision_present"])
        self.assertTrue(report["checks"]["restore_window_decision_present"])
        self.assertTrue(report["checks"]["isolated_restore_target_available"])
        self.assertTrue(report["checks"]["destructive_restore_prevented"])
        self.assertIn("live_db_object_backup_restore_execution_not_enabled", report["blockers"])
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])
        self.assertFalse(report["production_ready"])

    def test_mocked_successful_live_drill_passes_with_synthetic_counts_only(self):
        verifier = load_verifier()

        report, storages, backends = run_injected_drill(verifier)
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])
        self.assertTrue(report["test_injected_backend"])
        self.assertTrue(report["checks"]["active_db_write_read_verified"])
        self.assertTrue(report["checks"]["active_object_write_read_verified"])
        self.assertTrue(report["checks"]["restore_db_write_read_verified"])
        self.assertTrue(report["checks"]["restore_object_write_read_verified"])
        self.assertTrue(report["checks"]["checksum_match"])
        self.assertTrue(report["checks"]["metadata_object_pairing_verified"])
        self.assertTrue(report["checks"]["workspace_isolation_preserved"])
        self.assertTrue(report["checks"]["restore_database_cannot_read_active_synthetic_rows"])
        self.assertTrue(report["checks"]["restore_object_cannot_read_active_synthetic_object"])
        self.assertTrue(report["checks"]["cleanup_completed"])
        self.assertEqual(report["active_db_synthetic_rows_written"], 7)
        self.assertEqual(report["active_object_synthetic_objects_written"], 1)
        self.assertEqual(report["restore_db_synthetic_rows_written"], 7)
        self.assertEqual(report["restore_object_synthetic_objects_written"], 1)
        self.assertEqual(report["db_blob_artifact_rows_written"], 0)
        self.assertFalse(report["production_ready"])
        self.assertEqual(report["blockers"], [])
        self.assertTrue(storages)
        self.assertFalse(backends["active"].objects)
        self.assertFalse(backends["restore"].objects)
        for value in complete_env().values():
            if value in {"1", "ap-southeast-1", "operator-approved-window", "isolated-restore-window"}:
                continue
            self.assertNotIn(value, text)
        self.assertNotIn("OPAQUE-STORAGE-REF", text)

    def test_runtime_filename_gate_does_not_break_active_object_pairing(self):
        verifier = load_verifier()

        report, _storages, _backends = run_injected_drill(verifier, runtime_filename_gate=True)

        self.assertEqual(report["status"], "passed")
        self.assertTrue(report["checks"]["active_object_write_read_verified"])
        self.assertTrue(report["checks"]["metadata_object_pairing_verified"])

    def test_metadata_pairing_requires_runtime_owner_session_kind_and_workspace(self):
        verifier = load_verifier()
        metadata = verifier.ObjectArtifactMetadata(
            workspace_id="workspace-a",
            owner_type="generated_quote",
            owner_id="quote-session-a",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type=verifier.SYNTHETIC_CONTENT_TYPE,
            size_bytes=24,
            checksum_sha256="a" * 64,
            storage_key="opaque-ref",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        base_row = {
            "workspace_id": "workspace-a",
            "owner_type": "generated_quote",
            "owner_id": "quote-session-a",
            "session_id": "quote-session-a",
            "artifact_kind": "xlsx",
            "filename": "quotation.xlsx",
            "content_type": verifier.SYNTHETIC_CONTENT_TYPE,
            "size_bytes": 24,
            "checksum_sha256": "a" * 64,
            "object_key_ref": "opaque-ref",
            "status": "active",
            "retention_status": "active",
            "deleted_at": None,
        }

        class Storage:
            workspace_id = "workspace-a"

            def __init__(self, row):
                self.row = row

            def object_artifact_row(self, _session_id, _kind):
                return self.row

        self.assertTrue(verifier._metadata_object_pairing_ok(Storage(base_row), "quote-session-a", metadata))
        for field, value in (
            ("workspace_id", "workspace-b"),
            ("owner_type", "uploaded_reference"),
            ("owner_id", "quote-session-b"),
            ("session_id", "quote-session-b"),
            ("artifact_kind", "pdf"),
            ("filename", "not-quotation.xlsx"),
            ("content_type", "application/octet-stream"),
            ("size_bytes", 25),
            ("checksum_sha256", "b" * 64),
            ("object_key_ref", ""),
            ("status", "deleted"),
            ("retention_status", "pending_delete"),
            ("deleted_at", "2026-01-01T00:00:00Z"),
        ):
            with self.subTest(field=field):
                row = dict(base_row)
                row[field] = value
                self.assertFalse(verifier._metadata_object_pairing_ok(Storage(row), "quote-session-a", metadata))

    def test_metadata_pairing_accepts_tuple_row_with_runtime_select_order(self):
        verifier = load_verifier()
        metadata = verifier.ObjectArtifactMetadata(
            workspace_id="workspace-a",
            owner_type="generated_quote",
            owner_id="quote-session-a",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type=verifier.SYNTHETIC_CONTENT_TYPE,
            size_bytes=24,
            checksum_sha256="a" * 64,
            storage_key="opaque-ref",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        row = TupleArtifactRow(
            artifact_id="obj-redacted",
            workspace_id="workspace-a",
            owner_type="generated_quote",
            owner_id="quote-session-a",
            platform_user_id="",
            session_id="quote-session-a",
            job_id="",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type=verifier.SYNTHETIC_CONTENT_TYPE,
            size_bytes=24,
            checksum_sha256="a" * 64,
            object_provider_type="s3_compatible",
            object_key_ref="opaque-ref",
            status="active",
            retention_status="active",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            deleted_at=None,
        )

        class Storage:
            workspace_id = "workspace-a"

            def object_artifact_row(self, _session_id, _kind):
                return row

        self.assertTrue(verifier._metadata_object_pairing_ok(Storage(), "quote-session-a", metadata))

    def test_same_underlying_restore_database_fails_before_restore_write(self):
        verifier = load_verifier()

        report, _storages, backends = run_injected_drill(verifier, shared_storage_state=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("restore_database_can_read_active_synthetic_rows", report["blockers"])
        self.assertFalse(report["checks"]["restore_database_cannot_read_active_synthetic_rows"])
        self.assertFalse(report["checks"]["restore_db_write_read_verified"])
        self.assertEqual(report["restore_db_synthetic_rows_written"], 0)
        self.assertNotIn("restore", backends)
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])

    def test_same_underlying_restore_object_target_fails_before_restore_object_write(self):
        verifier = load_verifier()

        report, _storages, backends = run_injected_drill(verifier, shared_backend_state=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("restore_object_can_read_active_synthetic_object", report["blockers"])
        self.assertTrue(report["checks"]["restore_database_cannot_read_active_synthetic_rows"])
        self.assertFalse(report["checks"]["restore_object_cannot_read_active_synthetic_object"])
        self.assertFalse(report["checks"]["restore_object_write_read_verified"])
        self.assertEqual(report["restore_object_synthetic_objects_written"], 0)
        self.assertIn("restore", backends)
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])

    def test_active_db_write_failure_fails_closed(self):
        verifier = load_verifier()

        report, _storages, _backends = run_injected_drill(verifier, fail_storage="active_write")

        self.assertEqual(report["status"], "failed")
        self.assertIn("active_db_write_failed", report["blockers"])
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])

    def test_active_object_write_failure_fails_closed(self):
        verifier = load_verifier()

        report, _storages, _backends = run_injected_drill(verifier, fail_backend="active_write")

        self.assertEqual(report["status"], "failed")
        self.assertIn("active_object_write_failed", report["blockers"])
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])

    def test_restore_db_write_failure_fails_closed(self):
        verifier = load_verifier()

        report, _storages, _backends = run_injected_drill(verifier, fail_storage="restore_write")

        self.assertEqual(report["status"], "failed")
        self.assertIn("restore_db_write_failed", report["blockers"])
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])

    def test_restore_object_write_failure_fails_closed(self):
        verifier = load_verifier()

        report, _storages, _backends = run_injected_drill(verifier, fail_backend="restore_write")

        self.assertEqual(report["status"], "failed")
        self.assertIn("restore_object_write_failed", report["blockers"])
        self.assertFalse(report["live_db_object_backup_restore_evidence_supported"])

    def test_checksum_mismatch_fails_closed(self):
        verifier = load_verifier()

        report, _storages, _backends = run_injected_drill(verifier, tamper_restore_retrieve=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("checksum_mismatch", report["blockers"])
        self.assertFalse(report["checks"]["checksum_match"])

    def test_metadata_object_pairing_mismatch_fails_closed(self):
        verifier = load_verifier()

        report, _storages, _backends = run_injected_drill(verifier, pairing_mismatch=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("metadata_object_pairing_mismatch", report["blockers"])
        self.assertFalse(report["checks"]["metadata_object_pairing_verified"])

    def test_cleanup_failure_fails_closed(self):
        verifier = load_verifier()

        report, _storages, _backends = run_injected_drill(verifier, cleanup_fails=True)

        self.assertEqual(report["status"], "failed")
        self.assertIn("cleanup_failed", report["blockers"])
        self.assertFalse(report["checks"]["cleanup_completed"])

    def test_profile_delete_false_with_residue_fails_cleanup(self):
        verifier = load_verifier()

        class FalseProfileDeleteStorage(FakeStorage):
            def delete_profile(self, _profile_id):
                return False

        storage = FalseProfileDeleteStorage(label="active", workspace_id="workspace-a")
        storage.profiles["profile-a"] = {"id": "profile-a", "label": "residue"}

        self.assertFalse(
            verifier._cleanup_storage(
                storage,
                profile_id="profile-a",
                pricing_id="pricing-a",
                session_id="quote-a",
                backend=None,
            )
        )
        self.assertIsNotNone(storage.profile_detail("profile-a"))

    def test_backend_delete_false_with_bytes_remaining_fails_cleanup(self):
        verifier = load_verifier()

        class FalseDeleteBackend(FakeBackend):
            def delete_artifact(self, metadata, *, workspace_id):
                if metadata.workspace_id != workspace_id:
                    raise RuntimeError("workspace mismatch")
                return False

        backend = FalseDeleteBackend(verifier.ObjectArtifactMetadata, label="active")
        metadata = backend.store_artifact(
            workspace_id="workspace-a",
            owner_type="generated_quote",
            owner_id="quote-a",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type=verifier.SYNTHETIC_CONTENT_TYPE,
            content=b"residual-bytes",
        )

        self.assertFalse(
            verifier._delete_backend_artifact_and_verify(
                backend,
                metadata,
                workspace_id="workspace-a",
            )
        )
        self.assertIn(metadata.storage_key, backend.objects)

    def test_false_delete_for_already_absent_backend_artifact_is_benign(self):
        verifier = load_verifier()

        class AlreadyAbsentBackend(FakeBackend):
            def delete_artifact(self, _metadata, *, workspace_id):
                _ = workspace_id
                return False

        backend = AlreadyAbsentBackend(verifier.ObjectArtifactMetadata, label="active")
        metadata = verifier.ObjectArtifactMetadata(
            workspace_id="workspace-a",
            owner_type="generated_quote",
            owner_id="quote-a",
            artifact_kind="xlsx",
            filename="quotation.xlsx",
            content_type=verifier.SYNTHETIC_CONTENT_TYPE,
            size_bytes=1,
            checksum_sha256="a" * 64,
            storage_key="already-absent",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )

        self.assertTrue(
            verifier._delete_backend_artifact_and_verify(
                backend,
                metadata,
                workspace_id="workspace-a",
            )
        )

    def test_one_cleanup_failure_does_not_skip_other_targets(self):
        verifier = load_verifier()

        report, storages, _backends = run_injected_drill(
            verifier,
            fail_storage="active_cleanup",
        )

        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["checks"]["cleanup_completed"])
        self.assertIn("cleanup_failed", report["blockers"])
        restore_storages = [storage for (label, _workspace), storage in storages.items() if label == "restore"]
        self.assertTrue(restore_storages)
        for storage in restore_storages:
            self.assertEqual(storage.profiles, {})
            self.assertEqual(storage.pricing, {})
            self.assertEqual(storage.sessions, {})

    def test_partial_restore_write_still_runs_outer_cleanup(self):
        verifier = load_verifier()

        report, _storages, backends = run_injected_drill(
            verifier,
            fail_storage="restore_write",
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("restore_db_write_failed", report["blockers"])
        self.assertTrue(report["checks"]["cleanup_completed"])
        self.assertEqual(backends["active"].objects, {})
        self.assertEqual(backends["restore"].objects, {})

    def test_early_return_after_active_probe_failure_still_cleans_everything(self):
        verifier = load_verifier()

        report, _storages, backends = run_injected_drill(
            verifier,
            fail_backend="active_write",
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("active_object_write_failed", report["blockers"])
        self.assertTrue(report["checks"]["cleanup_completed"])
        self.assertEqual(backends["active"].objects, {})

    def test_sanitized_output_omits_private_live_values_and_payloads(self):
        verifier = load_verifier()
        env = complete_env()
        env.update(
            {
                "SQAG_DATABASE_URL": "PRIVATE_ACTIVE_DB_URL_MARKER",
                "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "PRIVATE_ACTIVE_ENDPOINT_MARKER",
                "SQAG_OBJECT_STORAGE_BUCKET": "PRIVATE_ACTIVE_BUCKET_MARKER",
                "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "PRIVATE_ACTIVE_ACCESS_MARKER",
                "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "PRIVATE_ACTIVE_SECRET_MARKER",
                "SQAG_RESTORE_DATABASE_URL": "PRIVATE_RESTORE_DB_URL_MARKER",
                "SQAG_RESTORE_OBJECT_STORAGE_ENDPOINT_URL": "PRIVATE_RESTORE_ENDPOINT_MARKER",
                "SQAG_RESTORE_OBJECT_STORAGE_BUCKET": "PRIVATE_RESTORE_BUCKET_MARKER",
                "SQAG_RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID": "PRIVATE_RESTORE_ACCESS_MARKER",
                "SQAG_RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY": "PRIVATE_RESTORE_SECRET_MARKER",
            }
        )

        report = verifier.run_verification(
            env=env,
            active_storage_factory=lambda _database_url, workspace_id: FakeStorage(label="active", workspace_id=workspace_id),
            restore_storage_factory=lambda _database_url, workspace_id: FakeStorage(label="restore", workspace_id=workspace_id),
            active_backend_factory=lambda _env: FakeBackend(verifier.ObjectArtifactMetadata, label="active"),
            restore_backend_factory=lambda _env: FakeBackend(verifier.ObjectArtifactMetadata, label="restore"),
            migration_applier=lambda _database_url: None,
            test_injected_backend=True,
        )
        text = json.dumps(report, sort_keys=True)

        self.assertEqual(report["privacy"]["output"], "metadata-only")
        for value in env.values():
            if value in {"1", "ap-southeast-1", "operator-approved-window", "isolated-restore-window"}:
                continue
            self.assertNotIn(value, text)
        for private_value in (
            "OPAQUE-STORAGE-REF",
            "backup dump",
            "restore dump",
            "tenant data",
            "generated quote contents",
        ):
            self.assertNotIn(private_value, text)

    def test_cli_output_is_metadata_only_and_nonzero(self):
        verifier = load_verifier()
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = verifier.main([])

        output = stdout.getvalue()
        report = json.loads(output)
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertNotIn("ACTIVE_DB_TARGET_MARKER_A", output)
        self.assertNotIn("ACTIVE_OBJECT_BUCKET_MARKER_A", output)
        self.assertNotIn("RESTORE_DB_TARGET_MARKER_B", output)


if __name__ == "__main__":
    unittest.main()
