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
    ObjectStorageNotFoundError,
    S3CompatibleObjectStorageBackend,
    artifact_checksum,
    object_storage_provider_status,
)
from webapp.postgres_migrations import (
    MIGRATION_FILE_NAMES,
    inspect_postgres_migrations,
    migration_manifest,
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
RUNTIME_MODE_ENV_NAMES = [
    webapp.SQAG_STORAGE_MODE_ENV_NAME,
    webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME,
]
REQUIRED_ENV_NAMES = [
    LIVE_RETENTION_DELETE_ENV_NAME,
    webapp.SQAG_DATABASE_URL_ENV_NAME,
    webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME,
    webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME,
    *RUNTIME_MODE_ENV_NAMES,
    *ACTIVE_OBJECT_ENV_NAMES,
]
TRUE_VALUES = {"1", "true", "yes", "on", "run", "enabled"}
SYNTHETIC_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SYNTHETIC_FILENAME = webapp.QUOTE_SESSION_EXPORT_KINDS["xlsx"]
RUNTIME_DOWNLOAD_FAILURE_STAGES = {
    "session_missing_or_not_visible",
    "session_not_published",
    "export_metadata_missing_or_noncanonical",
    "export_stale",
    "active_object_metadata_missing",
    "object_not_found",
    "object_contract_error",
    "runtime_returned_none",
    "runtime_response_mismatch",
    "unexpected_runtime_exception",
}
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
MigrationInspector = Callable[[str], Mapping[str, Any]]


def _clean(value: object) -> str:
    return str(value or "").strip()


def _enabled(env: Mapping[str, str]) -> bool:
    return _clean(env.get(LIVE_RETENTION_DELETE_ENV_NAME)).lower() in TRUE_VALUES


def _present(env: Mapping[str, str], name: str) -> bool:
    return bool(_clean(env.get(name)))


def _missing_env_names(env: Mapping[str, str]) -> list[str]:
    return [name for name in REQUIRED_ENV_NAMES if not _present(env, name)]


def _runtime_database_mode_enabled(env: Mapping[str, str]) -> bool:
    return _clean(env.get(webapp.SQAG_STORAGE_MODE_ENV_NAME)).lower() == "database"


def _runtime_object_artifact_mode_enabled(env: Mapping[str, str]) -> bool:
    return _clean(env.get(webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME)).lower() == "object"


def _default_checks(
    *,
    live_opt_in_enabled: bool,
    database_present: bool,
    object_present: bool,
    runtime_database_mode_enabled: bool,
    runtime_object_artifact_mode_enabled: bool,
) -> dict[str, bool]:
    return {
        "live_evidence_opt_in_enabled": live_opt_in_enabled,
        "active_database_target_present": database_present,
        "active_object_target_present": object_present,
        "runtime_database_mode_enabled": runtime_database_mode_enabled,
        "runtime_object_artifact_mode_enabled": runtime_object_artifact_mode_enabled,
        "migration_preflight_attempted": False,
        "trusted_migration_ledger": False,
        "zero_pending_migrations": False,
        "migration_schema_ready": False,
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
    runtime_download_failure_stage: str = "",
) -> dict[str, object]:
    supported = status == "passed" and not test_injected_backend
    safe_runtime_download_failure_stage = (
        runtime_download_failure_stage
        if runtime_download_failure_stage in RUNTIME_DOWNLOAD_FAILURE_STAGES
        else ""
    )
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
        "migration_payload_statements_executed": 0,
        "runtime_download_failure_stage": safe_runtime_download_failure_stage,
        "privacy": _privacy_report(),
        "production_ready": False,
        "blockers": list(blockers),
        "notes": [
            "This verifier uses synthetic namespaced rows and one tiny synthetic generated artifact payload only.",
            "It fails closed unless explicit live retention/delete evidence env names are present.",
            "It never reports private target values, object keys, artifact bytes, tenant data, generated quote contents, or secrets.",
            "A test-injected backend exercises verifier logic only and is not live production evidence.",
            "This verifier has no migration-application authority and requires a read-only migrator preflight with a trusted zero-pending ledger.",
        ],
    }


def _inspect_migration_readiness(database_url: str) -> Mapping[str, Any]:
    migrations = migration_manifest(ROOT / "migrations")
    with webapp.postgres_storage_connection(database_url, expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE) as connection:
        try:
            connection.execute("set transaction read only")
            return inspect_postgres_migrations(connection, migrations)
        finally:
            connection.rollback()


def _migration_preflight_blockers(report: Mapping[str, Any]) -> list[str]:
    expected_ids = list(MIGRATION_FILE_NAMES)
    ledger_state = _clean(report.get("ledgerState"))
    pending_ids = report.get("pendingMigrationIds")
    applied_ids = report.get("appliedMigrationIds")
    inspection_blockers = report.get("blockers")

    if ledger_state == "missing":
        return ["migration_ledger_missing"]
    if ledger_state != "present":
        return ["migration_ledger_untrusted"]
    if not isinstance(pending_ids, list) or not isinstance(applied_ids, list) or not isinstance(inspection_blockers, list):
        return ["migration_preflight_invalid"]
    if pending_ids:
        return ["pending_migrations"]
    if applied_ids != expected_ids:
        return ["migration_ledger_untrusted"]
    if inspection_blockers:
        if any(str(item).startswith("checksum_drift:") for item in inspection_blockers):
            return ["migration_checksum_drift"]
        if any(item in {"unexpected_applied_migration", "unknown_or_out_of_order_migration"} for item in inspection_blockers):
            return ["migration_ledger_untrusted"]
        return ["migration_schema_not_ready"]
    if (
        report.get("status") != "ready"
        or report.get("safeToApply") is not True
        or report.get("expectedHead") != expected_ids[-1]
        or report.get("appliedHead") != expected_ids[-1]
    ):
        return ["migration_schema_not_ready"]
    return []


def _build_runtime_storage(database_url: str, workspace_id: str) -> Any:
    return webapp.DatabaseSqagStorage(
        database_url,
        workspace_id,
        role="admin",
        user_id=f"{workspace_id}-synthetic-user",
        expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
    )


def _build_maintenance_storage(database_url: str, workspace_id: str) -> Any:
    return webapp.DatabaseSqagStorage(
        database_url,
        workspace_id,
        role="admin",
        user_id=f"{workspace_id}-synthetic-user",
        expected_session_role=webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
    )


def _build_default_storage(database_url: str, workspace_id: str) -> Any:
    """Retain the test helper name while making its default authority runtime."""
    return _build_runtime_storage(database_url, workspace_id)


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
                "from sqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and session_id = ?",
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
                    "sha256": metadata.checksum_sha256,
                    "stale": False,
                }
            },
            "publication": {
                "state": "published",
                "run_id": "",
                "job_id": "",
                "error_code": "",
            },
        },
        session_id=ids["session_a"],
    )
    if hasattr(storage, "connection"):
        _persist_synthetic_published_session(storage, ids["session_a"], metadata)
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


def _persist_synthetic_published_session(
    storage: object,
    session_id: str,
    metadata: ObjectArtifactMetadata,
) -> None:
    """Persist verifier-owned publication metadata rejected by normal caller APIs."""
    with storage.connection() as connection:
        row = connection.execute(
            "select metadata_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
            (storage.workspace_id, session_id),
        ).fetchone()
        if not row:
            raise ObjectStorageContractError("Synthetic quote session metadata is unavailable.")
        try:
            current = json.loads(row["metadata_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ObjectStorageContractError("Synthetic quote session metadata is unavailable.") from exc
        published = webapp.normalized_quote_session_metadata(current if isinstance(current, dict) else {})
        if not published:
            raise ObjectStorageContractError("Synthetic quote session metadata is unavailable.")
        now = webapp.utc_timestamp()
        published["updated_at"] = now
        published["publication"] = {
            "state": "published",
            "run_id": "",
            "job_id": "",
            "error_code": "",
        }
        published["status"]["quote_generated"] = True
        published["status"]["xlsx_exported"] = True
        published["status"]["pdf_exported"] = False
        published["exports"]["xlsx"] = {
            "filename": SYNTHETIC_FILENAME,
            "created_at": metadata.created_at,
            "size_bytes": metadata.size_bytes,
            "sha256": metadata.checksum_sha256,
            "stale": False,
        }
        connection.execute(
            "update sqag_quote_sessions set metadata_json = ?, updated_at = ? where workspace_id = ? and session_id = ?",
            (
                json.dumps(published, ensure_ascii=True, sort_keys=True),
                now,
                storage.workspace_id,
                session_id,
            ),
        )
        connection.commit()


def _runtime_env_names(database_url_name: str = webapp.SQAG_DATABASE_URL_ENV_NAME) -> list[str]:
    return [
        database_url_name,
        webapp.SQAG_STORAGE_MODE_ENV_NAME,
        webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME,
        *ACTIVE_OBJECT_ENV_NAMES,
    ]


def _with_runtime_env(
    env: Mapping[str, str],
    callback: Callable[[], Any],
    *,
    database_url_name: str = webapp.SQAG_DATABASE_URL_ENV_NAME,
) -> Any:
    names = _runtime_env_names(database_url_name)
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            value = env.get(name)
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = str(value)
        return callback()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _runtime_download(storage: object, session_id: str, backend: ObjectStorageBackend, env: Mapping[str, str]) -> dict[str, object] | None:
    if not hasattr(storage, "quote_session_export_artifact"):
        return None
    return _with_runtime_env(
        env,
        lambda: _with_configured_backend(backend, lambda: storage.quote_session_export_artifact(session_id, "xlsx")),
    )


def _runtime_download_verified(
    *,
    storage: object,
    session_id: str,
    backend: ObjectStorageBackend,
    env: Mapping[str, str],
    metadata: ObjectArtifactMetadata,
    payload: bytes,
    diagnostics: dict[str, str] | None = None,
) -> bool:
    if diagnostics is not None:
        diagnostics["failure_stage"] = ""
    try:
        artifact = _runtime_download(storage, session_id, backend, env)
    except Exception as exc:
        if isinstance(exc, ObjectStorageNotFoundError):
            stage = "object_not_found"
        elif isinstance(exc, ObjectStorageContractError):
            stage = "object_contract_error"
        else:
            stage = "unexpected_runtime_exception"
        if diagnostics is not None:
            diagnostics["failure_stage"] = stage
        return False
    if artifact is None:
        if diagnostics is not None:
            diagnostics["failure_stage"] = _runtime_download_precondition_failure_stage(storage, session_id)
        return False
    content = artifact.get("content") if isinstance(artifact, Mapping) else None
    verified = bool(
        isinstance(artifact, Mapping)
        and bytes(content or b"") == payload
        and _clean(artifact.get("filename")) == SYNTHETIC_FILENAME
        and _clean(artifact.get("content_type")) == SYNTHETIC_CONTENT_TYPE
        and int(artifact.get("size_bytes") or 0) == metadata.size_bytes
        and artifact_checksum(bytes(content or b"")) == metadata.checksum_sha256
        and _metadata_object_pairing_ok(storage, session_id, metadata)
    )
    if not verified and diagnostics is not None:
        diagnostics["failure_stage"] = "runtime_response_mismatch"
    return verified


def _runtime_download_precondition_failure_stage(storage: object, session_id: str) -> str:
    try:
        if hasattr(storage, "_read_quote_session_metadata"):
            metadata, _draft_files = storage._read_quote_session_metadata(session_id)
            if not metadata:
                return "session_missing_or_not_visible"
            if not webapp.quote_session_is_published(metadata):
                return "session_not_published"
            export = metadata.get("exports", {}).get("xlsx") if isinstance(metadata.get("exports"), dict) else None
            if not isinstance(export, dict) or _clean(export.get("filename")) != SYNTHETIC_FILENAME:
                return "export_metadata_missing_or_noncanonical"
            if webapp.quote_session_export_is_stale(metadata, export):
                return "export_stale"
        if _object_artifact_row(storage, session_id, "xlsx") is None:
            return "active_object_metadata_missing"
    except Exception:
        return "runtime_returned_none"
    return "runtime_returned_none"



def _runtime_download_denied(storage: object, session_id: str, backend: ObjectStorageBackend, env: Mapping[str, str]) -> bool:
    if not hasattr(storage, "quote_session_export_artifact"):
        return _object_artifact_row(storage, session_id, "xlsx") is None
    try:
        return _runtime_download(storage, session_id, backend, env) is None
    except Exception as exc:
        return _is_confirmed_missing_error(exc)



def _is_confirmed_missing_error(exc: Exception) -> bool:
    return isinstance(exc, ObjectStorageNotFoundError)


def _delete_object_or_confirm_missing(backend: ObjectStorageBackend, metadata: ObjectArtifactMetadata) -> tuple[bool, bool]:
    try:
        deleted = bool(backend.delete_artifact(metadata, workspace_id=metadata.workspace_id))
    except Exception:
        deleted = False
    try:
        backend.retrieve_artifact(metadata, workspace_id=metadata.workspace_id)
    except Exception as exc:
        confirmed_missing = _is_confirmed_missing_error(exc)
        return bool(deleted or confirmed_missing), confirmed_missing
    return False, False


def _repeated_delete_safe(backend: ObjectStorageBackend, metadata: ObjectArtifactMetadata) -> bool:
    try:
        deleted = bool(backend.delete_artifact(metadata, workspace_id=metadata.workspace_id))
    except Exception:
        deleted = False
    if deleted:
        return True
    try:
        backend.retrieve_artifact(metadata, workspace_id=metadata.workspace_id)
    except Exception as exc:
        return _is_confirmed_missing_error(exc)
    return False


def _cleanup_storage(storage: object, session_id: str) -> bool:
    if hasattr(storage, "connection"):
        with storage.connection() as connection:
            if getattr(storage, "database_family", "") != "postgres_compatible":
                connection.execute(
                    "delete from sqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
                    (storage.workspace_id, "generated_quote", session_id, "xlsx"),
                )
            connection.execute(
                "delete from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                (storage.workspace_id, session_id),
            )
            connection.commit()
        return True
    if hasattr(storage, "delete_quote_session"):
        return bool(storage.delete_quote_session(session_id))
    return True


def _cleanup(
    *,
    storage: object | None,
    backend: ObjectStorageBackend | None,
    metadata: ObjectArtifactMetadata | None,
    ids: Mapping[str, str],
    maintenance_storage: object | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    ok = True
    object_absence_confirmed = True
    if backend is not None and metadata is not None:
        try:
            if maintenance_storage is not None and hasattr(maintenance_storage, "tombstone_object_quote_artifacts"):
                tombstoned = int(
                    _with_runtime_env(
                        env or {},
                        lambda: _with_configured_backend(
                            backend,
                            lambda: maintenance_storage.tombstone_object_quote_artifacts(ids["session_a"]),
                        ),
                        database_url_name=webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME,
                    )
                    or 0
                )
                if tombstoned == 0 and _object_artifact_row(maintenance_storage, ids["session_a"], "xlsx") is not None:
                    raise ObjectStorageContractError("Synthetic artifact tombstone was not applied.")
            removed, confirmed_missing = _delete_object_or_confirm_missing(backend, metadata)
            object_absence_confirmed = bool(removed and confirmed_missing)
            ok = object_absence_confirmed
        except Exception:
            object_absence_confirmed = False
            ok = False
    if storage is not None and object_absence_confirmed:
        try:
            ok = bool(_cleanup_storage(storage, ids["session_a"])) and ok
        except Exception:
            ok = False
    elif storage is not None:
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
    runtime_storage_factory: StorageFactory,
    maintenance_storage_factory: StorageFactory,
    backend_factory: BackendFactory,
    runtime_download_diagnostics: dict[str, str],
) -> tuple[dict[str, bool], list[str], int, int, int]:
    ids = _synthetic_ids()
    runtime_database_url = _clean(env.get(webapp.SQAG_DATABASE_URL_ENV_NAME))
    maintenance_database_url = _clean(env.get(webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME))
    runtime_storage = None
    maintenance_storage = None
    backend = None
    metadata = None
    active_db_rows = 0
    active_object_count = 0
    active_object_deleted_count = 0
    payload = _synthetic_payload(ids)

    try:
        checks["connection_attempted"] = True
        try:
            runtime_storage = runtime_storage_factory(runtime_database_url, ids["workspace_a"])
            if runtime_storage_factory is maintenance_storage_factory:
                maintenance_storage = runtime_storage
            else:
                maintenance_storage = maintenance_storage_factory(maintenance_database_url, ids["workspace_a"])
            runtime_storage.ensure_ready()
            runtime_storage.ensure_object_artifact_ready()
            maintenance_ready = getattr(maintenance_storage, "ensure_retention_ready", None)
            if callable(maintenance_ready):
                maintenance_ready()
            else:
                maintenance_storage.ensure_ready()
            maintenance_storage.ensure_object_artifact_ready()
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
            active_db_rows = _write_synthetic_metadata(runtime_storage, ids, metadata)
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
        checks["metadata_object_pairing_verified"] = _metadata_object_pairing_ok(runtime_storage, ids["session_a"], metadata)
        checks["db_metadata_active_verified"] = checks["metadata_object_pairing_verified"] and runtime_storage.get_quote_session(ids["session_a"]) is not None
        checks["active_runtime_download_verified"] = _runtime_download_verified(
            storage=runtime_storage,
            session_id=ids["session_a"],
            backend=backend,
            env=env,
            metadata=metadata,
            payload=payload,
            diagnostics=runtime_download_diagnostics,
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
            tombstoned = int(
                _with_runtime_env(
                    env,
                    lambda: _with_configured_backend(
                        backend,
                        lambda: maintenance_storage.tombstone_object_quote_artifacts(ids["session_a"]),
                    ),
                    database_url_name=webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME,
                )
                or 0
            )
            checks["tombstone_metadata_verified"] = tombstoned > 0 and _tombstone_verified(maintenance_storage, ids["session_a"])
        except Exception:
            blockers.append("tombstone_metadata_failed")
            return checks, blockers, active_db_rows, active_object_count, active_object_deleted_count

        checks["deleted_metadata_download_denied"] = bool(
            checks["active_runtime_download_verified"]
            and _runtime_download_denied(runtime_storage, ids["session_a"], backend, env)
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
        cleanup_completed = _cleanup(storage=runtime_storage, maintenance_storage=maintenance_storage, backend=backend, metadata=metadata, ids=ids, env=env)
        checks["cleanup_completed"] = cleanup_completed
        if not cleanup_completed and "cleanup_failed" not in blockers:
            blockers.append("cleanup_failed")


def run_verification(
    *,
    env: Mapping[str, str] | None = None,
    storage_factory: StorageFactory | None = None,
    maintenance_storage_factory: StorageFactory | None = None,
    backend_factory: BackendFactory | None = None,
    migration_inspector: MigrationInspector | None = None,
    execute_live_drill: bool = True,
    test_injected_backend: bool = False,
) -> dict[str, object]:
    dependency_injected = any(
        dependency is not None
        for dependency in (
            storage_factory,
            maintenance_storage_factory,
            backend_factory,
            migration_inspector,
        )
    )
    effective_test_injected = bool(test_injected_backend or dependency_injected)
    effective_env = dict(os.environ if env is None else env)
    missing = _missing_env_names(effective_env)
    live_opt_in_enabled = _enabled(effective_env)
    runtime_database_mode_enabled = _runtime_database_mode_enabled(effective_env)
    runtime_object_artifact_mode_enabled = _runtime_object_artifact_mode_enabled(effective_env)
    checks = _default_checks(
        live_opt_in_enabled=live_opt_in_enabled,
        database_present=all(
            _present(effective_env, name)
            for name in (
                webapp.SQAG_DATABASE_URL_ENV_NAME,
                webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME,
                webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME,
            )
        ),
        object_present=all(_present(effective_env, name) for name in ACTIVE_OBJECT_ENV_NAMES),
        runtime_database_mode_enabled=runtime_database_mode_enabled,
        runtime_object_artifact_mode_enabled=runtime_object_artifact_mode_enabled,
    )
    runtime_download_diagnostics = {"failure_stage": ""}

    blockers: list[str] = []
    if missing or not live_opt_in_enabled:
        blockers.append("live_retention_delete_evidence_not_enabled_or_incomplete")
    if not runtime_database_mode_enabled:
        blockers.append("runtime_database_mode_not_enabled")
    if not runtime_object_artifact_mode_enabled:
        blockers.append("runtime_object_artifact_mode_not_enabled")
    if not execute_live_drill and not blockers:
        blockers.append("live_retention_delete_execution_not_enabled")
    if blockers:
        return _report(
            status="blocked",
            checks=checks,
            missing_env_names=missing,
            blockers=blockers,
            test_injected_backend=effective_test_injected,
        )

    checks["migration_preflight_attempted"] = True
    try:
        migration_report = (migration_inspector or _inspect_migration_readiness)(
            _clean(effective_env.get(webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME))
        )
        migration_blockers = _migration_preflight_blockers(migration_report)
    except Exception:
        migration_blockers = ["migration_preflight_failed"]
    if migration_blockers:
        return _report(
            status="blocked",
            checks=checks,
            missing_env_names=missing,
            blockers=migration_blockers,
            test_injected_backend=effective_test_injected,
        )
    checks["trusted_migration_ledger"] = True
    checks["zero_pending_migrations"] = True
    checks["migration_schema_ready"] = True

    checks, blockers, active_db_rows, active_object_count, active_object_deleted_count = _run_drill(
        env=effective_env,
        checks=checks,
        blockers=blockers,
        runtime_storage_factory=storage_factory or _build_default_storage,
        maintenance_storage_factory=(
            maintenance_storage_factory
            or (_build_maintenance_storage if storage_factory is None else storage_factory)
        ),
        backend_factory=backend_factory or _build_s3_backend,
        runtime_download_diagnostics=runtime_download_diagnostics,
    )
    status = "passed" if not blockers else "failed"
    return _report(
        status=status,
        checks=checks,
        missing_env_names=missing,
        blockers=blockers,
        test_injected_backend=effective_test_injected,
        active_db_rows=active_db_rows,
        active_object_count=active_object_count,
        active_object_deleted_count=active_object_deleted_count,
        runtime_download_failure_stage=runtime_download_diagnostics["failure_stage"],
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
