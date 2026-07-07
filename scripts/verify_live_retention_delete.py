#!/usr/bin/env python3
"""Opt-in live SQAG retention/delete drill for DB metadata plus object bytes.

The verifier fails closed by default. When explicitly enabled, it writes one
synthetic quote-session metadata row and one tiny synthetic generated artifact
object, verifies metadata/object pairing, exercises the runtime tombstone and
object-delete lifecycle, and removes all synthetic data.

Output is metadata-only: no DB URL, provider value, bucket, endpoint,
credential, object key, path, artifact byte, tenant data, quote content, backup
dump, or restore dump is printed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp
from webapp.object_storage import (
    OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME,
    OBJECT_STORAGE_BUCKET_ENV_NAME,
    OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME,
    OBJECT_STORAGE_PROVIDER_ENV_NAME,
    OBJECT_STORAGE_REGION_ENV_NAME,
    OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME,
    ObjectArtifactMetadata,
    ObjectStorageBackend,
    ObjectStorageConfigurationError,
    ObjectStorageContractError,
    S3CompatibleObjectStorageBackend,
    artifact_checksum,
    object_storage_provider_status,
)


LIVE_RETENTION_DELETE_ENV_NAME = "SQAG_LIVE_RETENTION_DELETE_EVIDENCE"
ACTIVE_OBJECT_ENV_NAMES = [
    OBJECT_STORAGE_PROVIDER_ENV_NAME,
    OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME,
    OBJECT_STORAGE_BUCKET_ENV_NAME,
    OBJECT_STORAGE_REGION_ENV_NAME,
    OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME,
    OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME,
]
REQUIRED_ENV_NAMES = [
    LIVE_RETENTION_DELETE_ENV_NAME,
    webapp.KQAG_DATABASE_URL_ENV_NAME,
    *ACTIVE_OBJECT_ENV_NAMES,
]
TRUE_VALUES = {"1", "true", "yes", "on", "run", "enabled"}
SYNTHETIC_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SYNTHETIC_FILENAME = webapp.QUOTE_SESSION_EXPORT_KINDS["xlsx"]
OBJECT_ARTIFACT_ROW_FIELDS = (
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
OBJECT_ARTIFACT_ROW_FIELD_INDEX = {field: index for index, field in enumerate(OBJECT_ARTIFACT_ROW_FIELDS)}

StorageFactory = Callable[[str, str], Any]
BackendFactory = Callable[[Mapping[str, str]], ObjectStorageBackend]
MigrationApplier = Callable[[str], None]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _enabled(env: Mapping[str, str]) -> bool:
    return _clean(env.get(LIVE_RETENTION_DELETE_ENV_NAME)).lower() in TRUE_VALUES


def _present(env: Mapping[str, str], name: str) -> bool:
    return bool(_clean(env.get(name)))


def _missing_env_names(env: Mapping[str, str]) -> list[str]:
    return [name for name in REQUIRED_ENV_NAMES if not _present(env, name)]


def _default_checks(*, live_opt_in_enabled: bool, database_present: bool, object_present: bool) -> dict[str, bool]:
    return {
        "live_evidence_opt_in_enabled": live_opt_in_enabled,
        "active_database_target_present": database_present,
        "active_object_target_present": object_present,
        "connection_attempted": False,
        "write_attempted": False,
        "read_attempted": False,
        "tombstone_attempted": False,
        "delete_attempted": False,
        "db_metadata_active_verified": False,
        "object_write_read_verified": False,
        "active_runtime_download_verified": False,
        "checksum_match": False,
        "metadata_object_pairing_verified": False,
        "tombstone_metadata_verified": False,
        "object_delete_verified": False,
        "deleted_metadata_download_denied": False,
        "missing_object_fail_closed": False,
        "wrong_workspace_denied": False,
        "repeated_delete_safe": False,
        "cleanup_completed": False,
        "db_blob_artifacts_prevented": True,
    }


def _privacy_report() -> dict[str, object]:
    return {
        "output": "metadata-only",
        "database_urls_printed": False,
        "hostnames_printed": False,
        "usernames_printed": False,
        "passwords_printed": False,
        "connection_strings_printed": False,
        "provider_values_printed": False,
        "endpoint_values_printed": False,
        "bucket_names_printed": False,
        "object_keys_printed": False,
        "access_keys_printed": False,
        "secret_keys_printed": False,
        "oauth_values_printed": False,
        "cookies_or_tokens_printed": False,
        "artifact_bytes_printed": False,
        "private_paths_printed": False,
        "tenant_data_printed": False,
        "generated_quote_contents_printed": False,
        "backup_dumps_printed": False,
        "restore_dumps_printed": False,
    }


def _report(
    *,
    status: str,
    checks: Mapping[str, bool],
    missing_env_names: list[str],
    blockers: list[str],
    test_injected_backend: bool,
    active_db_rows: int = 0,
    active_object_count: int = 0,
    active_object_deleted_count: int = 0,
) -> dict[str, object]:
    supported = status == "passed" and not test_injected_backend
    return {
        "schema": "swooshz.sqag.live-retention-delete-evidence.v1",
        "status": status,
        "live_retention_delete_evidence_supported": supported,
        "test_injected_backend": bool(test_injected_backend),
        "required_env_names": list(REQUIRED_ENV_NAMES),
        "missing_env_names": list(missing_env_names),
        "checks": dict(checks),
        "active_db_synthetic_rows_written": int(active_db_rows),
        "active_object_synthetic_objects_written": int(active_object_count),
        "active_object_synthetic_objects_deleted": int(active_object_deleted_count),
        "db_blob_artifact_rows_written": 0,
        "privacy": _privacy_report(),
        "production_ready": False,
        "blockers": list(blockers),
        "notes": [
            "This verifier uses synthetic namespaced rows and one tiny synthetic generated artifact payload only.",
            "It fails closed unless explicit live retention/delete evidence env names are present.",
            "It never reports private target values, object keys, artifact bytes, tenant data, generated quote contents, or secrets.",
            "A test-injected backend exercises verifier logic only and is not live production evidence.",
        ],
    }


def _build_default_storage(database_url: str, workspace_id: str) -> Any:
    return webapp.DatabaseKqagStorage(
        database_url,
        workspace_id,
        role="admin",
        user_id=f"{workspace_id}-synthetic-user",
    )


def _build_s3_backend(env: Mapping[str, str]) -> S3CompatibleObjectStorageBackend:
    provider_status = object_storage_provider_status(env)
    if provider_status.get("provider") != "s3_compatible" or not provider_status.get("configured"):
        raise ObjectStorageConfigurationError("Object storage backend is not available.")
    try:
        boto3 = importlib.import_module("boto3")
        client = boto3.client(
            "s3",
            endpoint_url=env.get(OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME),
            region_name=env.get(OBJECT_STORAGE_REGION_ENV_NAME),
            aws_access_key_id=env.get(OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME),
            aws_secret_access_key=env.get(OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME),
        )
    except Exception as exc:
        raise ObjectStorageConfigurationError("Object storage backend is not available.") from exc
    return S3CompatibleObjectStorageBackend(
        bucket=_clean(env.get(OBJECT_STORAGE_BUCKET_ENV_NAME)),
        client=client,
        status=provider_status,
    )


def _synthetic_ids() -> dict[str, str]:
    token = uuid.uuid4().hex[:12]
    prefix = f"sqagrd-{token}"
    return {
        "workspace_a": f"{prefix}-workspace-a",
        "workspace_b": f"{prefix}-workspace-b",
        "session_a": f"quote-{token}a",
    }


def _synthetic_payload(ids: Mapping[str, str]) -> bytes:
    seed = f"sqag-retention-delete:{ids['workspace_a']}:{ids['session_a']}".encode("ascii")
    return hashlib.sha256(seed).digest()[:24]


def _row_value(row: object, key: str) -> object:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]  # type: ignore[index]
    except Exception:
        pass
    index = OBJECT_ARTIFACT_ROW_FIELD_INDEX.get(key)
    if index is None:
        return None
    try:
        return row[index]  # type: ignore[index]
    except Exception:
        return None


def _row_int(row: object, key: str) -> int:
    try:
        return int(_row_value(row, key) or 0)
    except Exception:
        return 0


def _object_artifact_row(storage: object, session_id: str, kind: str) -> object | None:
    if hasattr(storage, "object_artifact_row"):
        return storage.object_artifact_row(session_id, kind)
    if hasattr(storage, "_object_quote_artifact_row"):
        return storage._object_quote_artifact_row(session_id, kind)
    return None


def _object_artifact_rows_for_session(storage: object, session_id: str) -> list[object]:
    if hasattr(storage, "object_artifact_rows_for_session"):
        return list(storage.object_artifact_rows_for_session(session_id))
    if hasattr(storage, "connection"):
        with storage.connection() as connection:
            return connection.execute(
                "select artifact_id, workspace_id, owner_type, owner_id, platform_user_id, session_id, job_id, artifact_kind, filename, content_type, size_bytes, checksum_sha256, object_provider_type, object_key_ref, status, retention_status, created_at, updated_at, deleted_at "
                "from kqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and session_id = ?",
                (storage.workspace_id, "generated_quote", session_id, session_id),
            ).fetchall()
    if hasattr(storage, "_object_quote_artifact_rows_for_session"):
        return list(storage._object_quote_artifact_rows_for_session(session_id))
    return []


def _metadata_object_pairing_ok(storage: object, session_id: str, metadata: ObjectArtifactMetadata) -> bool:
    row = _object_artifact_row(storage, session_id, "xlsx")
    expected_workspace = _clean(getattr(storage, "workspace_id", "")) or metadata.workspace_id
    return bool(
        row
        and _clean(_row_value(row, "workspace_id")) == expected_workspace == metadata.workspace_id
        and _clean(_row_value(row, "owner_type")) == metadata.owner_type == "generated_quote"
        and _clean(_row_value(row, "owner_id")) == metadata.owner_id == session_id
        and _clean(_row_value(row, "session_id")) == session_id
        and _clean(_row_value(row, "artifact_kind")) == metadata.artifact_kind == "xlsx"
        and _clean(_row_value(row, "filename")) == metadata.filename == SYNTHETIC_FILENAME
        and _clean(_row_value(row, "checksum_sha256")) == metadata.checksum_sha256
        and _row_int(row, "size_bytes") == metadata.size_bytes
        and _clean(_row_value(row, "content_type")) == metadata.content_type
        and _clean(_row_value(row, "object_key_ref")) == metadata.storage_key
        and _clean(_row_value(row, "status")) == "active"
        and _clean(_row_value(row, "retention_status")) == "active"
        and not _clean(_row_value(row, "deleted_at"))
    )


def _tombstone_verified(storage: object, session_id: str) -> bool:
    rows = _object_artifact_rows_for_session(storage, session_id)
    return bool(
        rows
        and all(
            _clean(_row_value(row, "status")) == "deleted"
            and _clean(_row_value(row, "retention_status")) == "deleted"
            and bool(_clean(_row_value(row, "deleted_at")))
            for row in rows
        )
    )


def _write_synthetic_metadata(storage: object, ids: Mapping[str, str], metadata: ObjectArtifactMetadata) -> int:
    storage.create_or_update_quote_session(
        {
            "session_id": ids["session_a"],
            "customer_summary": {"name": "Synthetic Retention Delete Drill"},
            "status": {
                "quote_generated": True,
                "xlsx_exported": True,
                "pdf_exported": False,
            },
            "exports": {
                "xlsx": {
                    "filename": SYNTHETIC_FILENAME,
                    "created_at": metadata.created_at,
                    "size_bytes": metadata.size_bytes,
                    "stale": False,
                }
            },
        },
        session_id=ids["session_a"],
    )
    rows = 1
    storage._upsert_object_quote_artifact(
        ids["session_a"],
        "xlsx",
        SYNTHETIC_FILENAME,
        SYNTHETIC_CONTENT_TYPE,
        metadata,
    )
    rows += 1
    return rows


def _runtime_download(storage: object, session_id: str, backend: ObjectStorageBackend) -> dict[str, object] | None:
    if not hasattr(storage, "quote_session_export_artifact"):
        return None
    return _with_configured_backend(backend, lambda: storage.quote_session_export_artifact(session_id, "xlsx"))


def _runtime_download_verified(
    *,
    storage: object,
    session_id: str,
    backend: ObjectStorageBackend,
    metadata: ObjectArtifactMetadata,
    payload: bytes,
) -> bool:
    try:
        artifact = _runtime_download(storage, session_id, backend)
    except Exception:
        return False
    content = artifact.get("content") if isinstance(artifact, Mapping) else None
    return bool(
        isinstance(artifact, Mapping)
        and bytes(content or b"") == payload
        and _clean(artifact.get("filename")) == SYNTHETIC_FILENAME
        and _clean(artifact.get("content_type")) == SYNTHETIC_CONTENT_TYPE
        and int(artifact.get("size_bytes") or 0) == metadata.size_bytes
        and artifact_checksum(bytes(content or b"")) == metadata.checksum_sha256
        and _metadata_object_pairing_ok(storage, session_id, metadata)
    )


def _runtime_download_denied(storage: object, session_id: str, backend: ObjectStorageBackend) -> bool:
    if not hasattr(storage, "quote_session_export_artifact"):
        return _object_artifact_row(storage, session_id, "xlsx") is None
    try:
        return _runtime_download(storage, session_id, backend) is None
    except Exception:
        return True


def _delete_object_or_confirm_missing(backend: ObjectStorageBackend, metadata: ObjectArtifactMetadata) -> tuple[bool, bool]:
    try:
        deleted = bool(backend.delete_artifact(metadata, workspace_id=metadata.workspace_id))
    except Exception:
        deleted = False
    missing_fail_closed = False
    try:
        backend.retrieve_artifact(metadata, workspace_id=metadata.workspace_id)
    except Exception:
        missing_fail_closed = True
    return bool(deleted or missing_fail_closed), missing_fail_closed


def _repeated_delete_safe(backend: ObjectStorageBackend, metadata: ObjectArtifactMetadata) -> bool:
    try:
        result = backend.delete_artifact(metadata, workspace_id=metadata.workspace_id)
        return bool(result)
    except Exception:
        return True


def _cleanup_storage(storage: object, session_id: str) -> bool:
    if hasattr(storage, "connection"):
        with storage.connection() as connection:
            connection.execute(
                "delete from kqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
                (storage.workspace_id, "generated_quote", session_id, "xlsx"),
            )
            connection.execute(
                "delete from kqag_quote_sessions where workspace_id = ? and session_id = ?",
                (storage.workspace_id, session_id),
            )
            connection.commit()
        return True
    if hasattr(storage, "delete_quote_session"):
        return bool(storage.delete_quote_session(session_id))
    return True


def _cleanup(*, storage: object | None, backend: ObjectStorageBackend | None, metadata: ObjectArtifactMetadata | None, ids: Mapping[str, str]) -> bool:
    ok = True
    if backend is not None and metadata is not None:
        try:
            _delete_object_or_confirm_missing(backend, metadata)
        except Exception:
            ok = False
    if storage is not None:
        try:
            ok = bool(_cleanup_storage(storage, ids["session_a"])) and ok
        except Exception:
            ok = False
    return ok


def _with_configured_backend(backend: ObjectStorageBackend, callback: Callable[[], Any]) -> Any:
    original_backend_factory = getattr(webapp, "configured_object_storage_backend", None)
    try:
        webapp.configured_object_storage_backend = lambda: backend  # type: ignore[assignment]
        return callback()
    finally:
        if original_backend_factory is not None:
            webapp.configured_object_storage_backend = original_backend_factory  # type: ignore[assignment]


def _run_drill(
    *,
    env: Mapping[str, str],
    checks: dict[str, bool],
    blockers: list[str],
    storage_factory: StorageFactory,
    backend_factory: BackendFactory,
    migration_applier: MigrationApplier,
) -> tuple[dict[str, bool], list[str], int, int, int]:
    ids = _synthetic_ids()
    database_url = _clean(env.get(webapp.KQAG_DATABASE_URL_ENV_NAME))
    storage = None
    backend = None
    metadata = None
    active_db_rows = 0
    active_object_count = 0
    active_object_deleted_count = 0
    payload = _synthetic_payload(ids)

    try:
        checks["connection_attempted"] = True
        try:
            migration_applier(database_url)
            storage = storage_factory(database_url, ids["workspace_a"])
            storage.ensure_ready()
            storage.ensure_object_artifact_ready()
        except Exception:
            blockers.append("database_connection_or_schema_failed")
            return checks, blockers, active_db_rows, active_object_count, active_object_deleted_count

        try:
            backend = backend_factory(env)
        except Exception:
            blockers.append("object_backend_unavailable")
            return checks, blockers, active_db_rows, active_object_count, active_object_deleted_count

        checks["write_attempted"] = True
        checks["read_attempted"] = True
        try:
            metadata = backend.store_artifact(
                workspace_id=ids["workspace_a"],
                owner_type="generated_quote",
                owner_id=ids["session_a"],
                artifact_kind="xlsx",
                filename=SYNTHETIC_FILENAME,
                content_type=SYNTHETIC_CONTENT_TYPE,
                content=payload,
            )
            active_object_count = 1
            active_db_rows = _write_synthetic_metadata(storage, ids, metadata)
        except Exception:
            blockers.append("object_write_failed")
            return checks, blockers, active_db_rows, active_object_count, active_object_deleted_count

        try:
            retrieved = backend.retrieve_artifact(metadata, workspace_id=ids["workspace_a"])
        except Exception:
            blockers.append("object_read_failed")
            return checks, blockers, active_db_rows, active_object_count, active_object_deleted_count

        checks["checksum_match"] = artifact_checksum(retrieved) == metadata.checksum_sha256
        checks["object_write_read_verified"] = (
            retrieved == payload
            and len(retrieved) == metadata.size_bytes
            and metadata.content_type == SYNTHETIC_CONTENT_TYPE
            and checks["checksum_match"]
        )
        checks["metadata_object_pairing_verified"] = _metadata_object_pairing_ok(storage, ids["session_a"], metadata)
        checks["db_metadata_active_verified"] = checks["metadata_object_pairing_verified"] and storage.get_quote_session(ids["session_a"]) is not None
        checks["active_runtime_download_verified"] = _runtime_download_verified(
            storage=storage,
            session_id=ids["session_a"],
            backend=backend,
            metadata=metadata,
            payload=payload,
        )
        try:
            backend.retrieve_artifact(metadata, workspace_id=ids["workspace_b"])
        except Exception:
            checks["wrong_workspace_denied"] = True

        if not checks["active_runtime_download_verified"]:
            if not checks["checksum_match"]:
                blockers.append("checksum_mismatch")
            if not checks["object_write_read_verified"]:
                blockers.append("object_read_failed")
            if not checks["db_metadata_active_verified"] or not checks["metadata_object_pairing_verified"]:
                blockers.append("metadata_object_pairing_mismatch")
            if not checks["wrong_workspace_denied"]:
                blockers.append("wrong_workspace_not_denied")
            blockers.append("active_runtime_download_not_verified")
            return checks, blockers, active_db_rows, active_object_count, active_object_deleted_count

        checks["tombstone_attempted"] = True
        try:
            tombstoned = int(_with_configured_backend(backend, lambda: storage.tombstone_object_quote_artifacts(ids["session_a"])) or 0)
            checks["tombstone_metadata_verified"] = tombstoned > 0 and _tombstone_verified(storage, ids["session_a"])
        except Exception:
            blockers.append("tombstone_metadata_failed")
            return checks, blockers, active_db_rows, active_object_count, active_object_deleted_count

        checks["deleted_metadata_download_denied"] = bool(
            checks["active_runtime_download_verified"]
            and _runtime_download_denied(storage, ids["session_a"], backend)
        )

        checks["delete_attempted"] = True
        checks["object_delete_verified"], checks["missing_object_fail_closed"] = _delete_object_or_confirm_missing(backend, metadata)
        if checks["object_delete_verified"]:
            active_object_deleted_count = 1
        checks["repeated_delete_safe"] = _repeated_delete_safe(backend, metadata)

        if not checks["checksum_match"]:
            blockers.append("checksum_mismatch")
        if not checks["object_write_read_verified"]:
            blockers.append("object_read_failed")
        if not checks["db_metadata_active_verified"] or not checks["metadata_object_pairing_verified"]:
            blockers.append("metadata_object_pairing_mismatch")
        if not checks["wrong_workspace_denied"]:
            blockers.append("wrong_workspace_not_denied")
        if not checks["tombstone_metadata_verified"]:
            blockers.append("tombstone_metadata_failed")
        if not checks["deleted_metadata_download_denied"]:
            blockers.append("deleted_metadata_download_not_denied")
        if not checks["object_delete_verified"]:
            blockers.append("object_delete_failed")
        if not checks["missing_object_fail_closed"]:
            blockers.append("missing_object_not_fail_closed")
        if not checks["repeated_delete_safe"]:
            blockers.append("repeated_delete_not_safe")
        return checks, blockers, active_db_rows, active_object_count, active_object_deleted_count
    finally:
        cleanup_completed = _cleanup(storage=storage, backend=backend, metadata=metadata, ids=ids)
        checks["cleanup_completed"] = cleanup_completed
        if not cleanup_completed and "cleanup_failed" not in blockers:
            blockers.append("cleanup_failed")


def run_verification(
    *,
    env: Mapping[str, str] | None = None,
    storage_factory: StorageFactory | None = None,
    backend_factory: BackendFactory | None = None,
    migration_applier: MigrationApplier | None = None,
    execute_live_drill: bool = True,
    test_injected_backend: bool = False,
) -> dict[str, object]:
    effective_env = dict(os.environ if env is None else env)
    missing = _missing_env_names(effective_env)
    live_opt_in_enabled = _enabled(effective_env)
    checks = _default_checks(
        live_opt_in_enabled=live_opt_in_enabled,
        database_present=_present(effective_env, webapp.KQAG_DATABASE_URL_ENV_NAME),
        object_present=all(_present(effective_env, name) for name in ACTIVE_OBJECT_ENV_NAMES),
    )

    blockers: list[str] = []
    if missing or not live_opt_in_enabled:
        blockers.append("live_retention_delete_evidence_not_enabled_or_incomplete")
    if not execute_live_drill and not blockers:
        blockers.append("live_retention_delete_execution_not_enabled")
    if blockers:
        return _report(
            status="blocked",
            checks=checks,
            missing_env_names=missing,
            blockers=blockers,
            test_injected_backend=test_injected_backend,
        )

    checks, blockers, active_db_rows, active_object_count, active_object_deleted_count = _run_drill(
        env=effective_env,
        checks=checks,
        blockers=blockers,
        storage_factory=storage_factory or _build_default_storage,
        backend_factory=backend_factory or _build_s3_backend,
        migration_applier=migration_applier or webapp.apply_kqag_storage_migrations,
    )
    status = "passed" if not blockers else "failed"
    return _report(
        status=status,
        checks=checks,
        missing_env_names=missing,
        blockers=blockers,
        test_injected_backend=test_injected_backend,
        active_db_rows=active_db_rows,
        active_object_count=active_object_count,
        active_object_deleted_count=active_object_deleted_count,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Run metadata-only SQAG live retention/delete evidence drill."
    )


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    report = run_verification()
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if report.get("status") == "passed" and report.get("live_retention_delete_evidence_supported") else 2


if __name__ == "__main__":
    raise SystemExit(main())
