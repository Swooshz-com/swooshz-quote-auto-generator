#!/usr/bin/env python3
"""Opt-in live SQAG DB+object backup/restore drill.

The verifier fails closed by default. When all live evidence env names are
present, targets are isolated, and operator decision markers exist, it writes
synthetic namespaced metadata rows and one tiny synthetic object into the active
targets, restores equivalent synthetic rows and object bytes into isolated
restore targets, verifies DB+object pairing, and removes all synthetic data.

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


LIVE_DB_OBJECT_BACKUP_RESTORE_ENV_NAME = "SQAG_LIVE_DB_OBJECT_BACKUP_RESTORE_EVIDENCE"
RESTORE_DATABASE_URL_ENV_NAME = "SQAG_RESTORE_DATABASE_URL"
RESTORE_OBJECT_STORAGE_PROVIDER_ENV_NAME = "SQAG_RESTORE_OBJECT_STORAGE_PROVIDER"
RESTORE_OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME = "SQAG_RESTORE_OBJECT_STORAGE_ENDPOINT_URL"
RESTORE_OBJECT_STORAGE_BUCKET_ENV_NAME = "SQAG_RESTORE_OBJECT_STORAGE_BUCKET"
RESTORE_OBJECT_STORAGE_REGION_ENV_NAME = "SQAG_RESTORE_OBJECT_STORAGE_REGION"
RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME = "SQAG_RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID"
RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME = "SQAG_RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY"
BACKUP_RESTORE_DECISION_ID_ENV_NAME = "SQAG_BACKUP_RESTORE_DECISION_ID"
BACKUP_RESTORE_WINDOW_ID_ENV_NAME = "SQAG_BACKUP_RESTORE_WINDOW_ID"

ACTIVE_OBJECT_ENV_NAMES = [
    OBJECT_STORAGE_PROVIDER_ENV_NAME,
    OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME,
    OBJECT_STORAGE_BUCKET_ENV_NAME,
    OBJECT_STORAGE_REGION_ENV_NAME,
    OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME,
    OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME,
]
RESTORE_OBJECT_ENV_NAMES = [
    RESTORE_OBJECT_STORAGE_PROVIDER_ENV_NAME,
    RESTORE_OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME,
    RESTORE_OBJECT_STORAGE_BUCKET_ENV_NAME,
    RESTORE_OBJECT_STORAGE_REGION_ENV_NAME,
    RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME,
    RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME,
]
REQUIRED_ENV_NAMES = [
    LIVE_DB_OBJECT_BACKUP_RESTORE_ENV_NAME,
    webapp.SQAG_DATABASE_URL_ENV_NAME,
    *ACTIVE_OBJECT_ENV_NAMES,
    RESTORE_DATABASE_URL_ENV_NAME,
    *RESTORE_OBJECT_ENV_NAMES,
    BACKUP_RESTORE_DECISION_ID_ENV_NAME,
    BACKUP_RESTORE_WINDOW_ID_ENV_NAME,
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
    return _clean(env.get(LIVE_DB_OBJECT_BACKUP_RESTORE_ENV_NAME)).lower() in TRUE_VALUES


def _present(env: Mapping[str, str], name: str) -> bool:
    return bool(_clean(env.get(name)))


def _missing_env_names(env: Mapping[str, str]) -> list[str]:
    return [name for name in REQUIRED_ENV_NAMES if not _present(env, name)]


def _active_object_target(env: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        _clean(env.get(OBJECT_STORAGE_PROVIDER_ENV_NAME)).lower(),
        _clean(env.get(OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME)).lower(),
        _clean(env.get(OBJECT_STORAGE_BUCKET_ENV_NAME)).lower(),
    )


def _restore_object_target(env: Mapping[str, str]) -> tuple[str, str, str]:
    return (
        _clean(env.get(RESTORE_OBJECT_STORAGE_PROVIDER_ENV_NAME)).lower(),
        _clean(env.get(RESTORE_OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME)).lower(),
        _clean(env.get(RESTORE_OBJECT_STORAGE_BUCKET_ENV_NAME)).lower(),
    )


def _database_targets_distinct(env: Mapping[str, str]) -> bool:
    active = _clean(env.get(webapp.SQAG_DATABASE_URL_ENV_NAME))
    restore = _clean(env.get(RESTORE_DATABASE_URL_ENV_NAME))
    return bool(active and restore and active != restore)


def _object_targets_distinct(env: Mapping[str, str]) -> bool:
    active = _active_object_target(env)
    restore = _restore_object_target(env)
    return bool(all(active) and all(restore) and active != restore)


def _restore_env_as_object_env(env: Mapping[str, str]) -> dict[str, str]:
    return {
        OBJECT_STORAGE_PROVIDER_ENV_NAME: _clean(env.get(RESTORE_OBJECT_STORAGE_PROVIDER_ENV_NAME)),
        OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME: _clean(env.get(RESTORE_OBJECT_STORAGE_ENDPOINT_URL_ENV_NAME)),
        OBJECT_STORAGE_BUCKET_ENV_NAME: _clean(env.get(RESTORE_OBJECT_STORAGE_BUCKET_ENV_NAME)),
        OBJECT_STORAGE_REGION_ENV_NAME: _clean(env.get(RESTORE_OBJECT_STORAGE_REGION_ENV_NAME)),
        OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME: _clean(env.get(RESTORE_OBJECT_STORAGE_ACCESS_KEY_ID_ENV_NAME)),
        OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME: _clean(env.get(RESTORE_OBJECT_STORAGE_SECRET_ACCESS_KEY_ENV_NAME)),
    }


def _default_checks(
    *,
    live_opt_in_enabled: bool,
    decision_present: bool,
    window_present: bool,
    active_database_present: bool,
    restore_database_present: bool,
    database_targets_distinct: bool,
    active_object_present: bool,
    restore_object_present: bool,
    object_targets_distinct: bool,
    isolated_restore_target: bool,
) -> dict[str, bool]:
    return {
        "live_evidence_opt_in_enabled": live_opt_in_enabled,
        "backup_ownership_decision_present": decision_present,
        "restore_window_decision_present": window_present,
        "active_database_target_present": active_database_present,
        "restore_database_target_present": restore_database_present,
        "database_targets_distinct": database_targets_distinct,
        "active_object_target_present": active_object_present,
        "restore_object_target_present": restore_object_present,
        "object_targets_distinct": object_targets_distinct,
        "isolated_restore_target_available": isolated_restore_target,
        "destructive_restore_prevented": True,
        "connection_attempted": False,
        "write_attempted": False,
        "read_attempted": False,
        "restore_attempted": False,
        "active_db_write_read_verified": False,
        "active_object_write_read_verified": False,
        "restore_db_write_read_verified": False,
        "restore_object_write_read_verified": False,
        "restore_database_cannot_read_active_synthetic_rows": False,
        "restore_object_cannot_read_active_synthetic_object": False,
        "checksum_match": False,
        "metadata_object_pairing_verified": False,
        "workspace_isolation_preserved": False,
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
        "provider_values_printed": False,
        "bucket_names_printed": False,
        "object_keys_printed": False,
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
    restore_db_rows: int = 0,
    restore_object_count: int = 0,
) -> dict[str, object]:
    supported = status == "passed" and not test_injected_backend
    return {
        "schema": "swooshz.sqag.live-db-object-backup-restore-drill.v1",
        "status": status,
        "live_db_object_backup_restore_evidence_supported": supported,
        "test_injected_backend": bool(test_injected_backend),
        "required_env_names": list(REQUIRED_ENV_NAMES),
        "missing_env_names": list(missing_env_names),
        "checks": dict(checks),
        "active_db_synthetic_rows_written": int(active_db_rows),
        "active_object_synthetic_objects_written": int(active_object_count),
        "restore_db_synthetic_rows_written": int(restore_db_rows),
        "restore_object_synthetic_objects_written": int(restore_object_count),
        "db_blob_artifact_rows_written": 0,
        "privacy": _privacy_report(),
        "production_ready": False,
        "blockers": list(blockers),
        "notes": [
            "This verifier uses synthetic namespaced rows and one tiny synthetic generated artifact payload only.",
            "It fails closed unless explicit live evidence, isolated restore targets, and operator decision markers are present.",
            "It never restores over active runtime targets and never reports private target values or object keys.",
            "A test-injected backend exercises verifier logic only and is not live production evidence.",
        ],
    }


def _build_default_storage(database_url: str, workspace_id: str) -> Any:
    return webapp.DatabaseSqagStorage(
        database_url,
        workspace_id,
        role="admin",
        user_id=f"{workspace_id}-synthetic-user",
        expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
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


def _active_backend_factory(env: Mapping[str, str]) -> ObjectStorageBackend:
    return _build_s3_backend(env)


def _restore_backend_factory(env: Mapping[str, str]) -> ObjectStorageBackend:
    return _build_s3_backend(_restore_env_as_object_env(env))


def _synthetic_ids() -> dict[str, str]:
    token = uuid.uuid4().hex[:12]
    prefix = f"sqagbr-{token}"
    return {
        "workspace_a": f"{prefix}-workspace-a",
        "workspace_b": f"{prefix}-workspace-b",
        "profile_a": f"{prefix}-profile-a",
        "profile_b": f"{prefix}-profile-b",
        "pricing_a": f"{prefix}-pricing-a",
        "pricing_b": f"{prefix}-pricing-b",
        "session_a": f"quote-{token}a",
        "session_b": f"quote-{token}b",
    }


def _synthetic_payload(ids: Mapping[str, str]) -> bytes:
    seed = f"sqag-db-object-restore:{ids['workspace_a']}:{ids['session_a']}".encode("ascii")
    return hashlib.sha256(seed).digest()[:24]


def _contains_id(items: list[dict[str, object]], item_id: str, id_key: str = "id") -> bool:
    return any(_clean(item.get(id_key)) == item_id for item in items if isinstance(item, dict))


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


def _write_metadata_rows(
    *,
    storage_a: object,
    storage_b: object,
    ids: Mapping[str, str],
    artifact_metadata: ObjectArtifactMetadata | None,
) -> int:
    rows = 0
    storage_a.save_profile({"id": ids["profile_a"], "label": "SQAG synthetic restore drill profile A"})
    storage_b.save_profile({"id": ids["profile_b"], "label": "SQAG synthetic restore drill profile B"})
    rows += 2
    storage_a.save_pricing_reference({"id": ids["pricing_a"], "label": "SQAG synthetic restore drill pricing A", "items": []})
    storage_b.save_pricing_reference({"id": ids["pricing_b"], "label": "SQAG synthetic restore drill pricing B", "items": []})
    rows += 2
    storage_a.create_or_update_quote_session(
        {"session_id": ids["session_a"], "customer_summary": {"name": "Synthetic Restore Drill A"}},
        session_id=ids["session_a"],
    )
    storage_b.create_or_update_quote_session(
        {"session_id": ids["session_b"], "customer_summary": {"name": "Synthetic Restore Drill B"}},
        session_id=ids["session_b"],
    )
    rows += 2
    if artifact_metadata is not None:
        storage_a._upsert_object_quote_artifact(
            ids["session_a"],
            "xlsx",
            SYNTHETIC_FILENAME,
            SYNTHETIC_CONTENT_TYPE,
            artifact_metadata,
        )
        rows += 1
    return rows


def _verify_db_rows(storage_a: object, storage_b: object, ids: Mapping[str, str]) -> bool:
    profiles_a = storage_a.list_company_profiles()
    profiles_b = storage_b.list_company_profiles()
    pricing_a = storage_a.list_pricing_references()
    pricing_b = storage_b.list_pricing_references()
    sessions_a = storage_a.list_quote_sessions()
    sessions_b = storage_b.list_quote_sessions()
    return all(
        (
            _contains_id(profiles_a, ids["profile_a"]),
            not _contains_id(profiles_a, ids["profile_b"]),
            _contains_id(profiles_b, ids["profile_b"]),
            not _contains_id(profiles_b, ids["profile_a"]),
            _contains_id(pricing_a, ids["pricing_a"]),
            not _contains_id(pricing_a, ids["pricing_b"]),
            _contains_id(pricing_b, ids["pricing_b"]),
            not _contains_id(pricing_b, ids["pricing_a"]),
            _contains_id(sessions_a, ids["session_a"], "session_id"),
            not _contains_id(sessions_a, ids["session_b"], "session_id"),
            _contains_id(sessions_b, ids["session_b"], "session_id"),
            not _contains_id(sessions_b, ids["session_a"], "session_id"),
        )
    )


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


def _restore_database_cannot_read_active_synthetic_rows(
    *,
    restore_storage_a: object,
    restore_storage_b: object,
    ids: Mapping[str, str],
    active_metadata: ObjectArtifactMetadata,
) -> bool:
    visible = any(
        (
            restore_storage_a.profile_detail(ids["profile_a"]) is not None,
            restore_storage_b.profile_detail(ids["profile_b"]) is not None,
            restore_storage_a.pricing_reference_detail(ids["pricing_a"]) is not None,
            restore_storage_b.pricing_reference_detail(ids["pricing_b"]) is not None,
            restore_storage_a.get_quote_session(ids["session_a"]) is not None,
            restore_storage_b.get_quote_session(ids["session_b"]) is not None,
            _metadata_object_pairing_ok(restore_storage_a, ids["session_a"], active_metadata),
        )
    )
    return not visible


def _cleanup_storage(storage: object, *, profile_id: str, pricing_id: str, session_id: str) -> bool:
    if hasattr(storage, "connection"):
        with storage.connection() as connection:
            connection.execute(
                "delete from sqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
                (storage.workspace_id, "generated_quote", session_id, "xlsx"),
            )
            connection.execute(
                "delete from sqag_quote_sessions where workspace_id = ? and session_id = ?",
                (storage.workspace_id, session_id),
            )
            connection.execute(
                "delete from sqag_pricing_references where workspace_id = ? and reference_id = ?",
                (storage.workspace_id, pricing_id),
            )
            connection.execute(
                "delete from sqag_profiles where workspace_id = ? and profile_id = ?",
                (storage.workspace_id, profile_id),
            )
            connection.commit()
        return True
    ok = True
    ok = bool(storage.delete_quote_session(session_id)) or ok
    ok = bool(storage.delete_pricing_reference(pricing_id)) or ok
    ok = bool(storage.delete_profile(profile_id)) or ok
    return ok


def _cleanup(
    *,
    active_storage_a: object | None,
    active_storage_b: object | None,
    restore_storage_a: object | None,
    restore_storage_b: object | None,
    active_backend: ObjectStorageBackend | None,
    restore_backend: ObjectStorageBackend | None,
    active_metadata: ObjectArtifactMetadata | None,
    restore_metadata: ObjectArtifactMetadata | None,
    ids: Mapping[str, str],
) -> bool:
    ok = True
    for backend, metadata, workspace_key in (
        (active_backend, active_metadata, "workspace_a"),
        (restore_backend, restore_metadata, "workspace_a"),
    ):
        if backend is None or metadata is None:
            continue
        try:
            backend.delete_artifact(metadata, workspace_id=ids[workspace_key])
        except Exception:
            ok = False
    for storage, profile_key, pricing_key, session_key in (
        (active_storage_a, "profile_a", "pricing_a", "session_a"),
        (active_storage_b, "profile_b", "pricing_b", "session_b"),
        (restore_storage_a, "profile_a", "pricing_a", "session_a"),
        (restore_storage_b, "profile_b", "pricing_b", "session_b"),
    ):
        if storage is None:
            continue
        try:
            _cleanup_storage(
                storage,
                profile_id=ids[profile_key],
                pricing_id=ids[pricing_key],
                session_id=ids[session_key],
            )
        except Exception:
            ok = False
    return ok


def _run_drill(
    *,
    env: Mapping[str, str],
    checks: dict[str, bool],
    blockers: list[str],
    active_storage_factory: StorageFactory,
    restore_storage_factory: StorageFactory,
    active_backend_factory: BackendFactory,
    restore_backend_factory: BackendFactory,
    migration_applier: MigrationApplier,
) -> tuple[dict[str, bool], list[str], int, int, int, int]:
    ids = _synthetic_ids()
    active_db_url = _clean(env.get(webapp.SQAG_DATABASE_URL_ENV_NAME))
    restore_db_url = _clean(env.get(RESTORE_DATABASE_URL_ENV_NAME))
    active_storage_a = active_storage_b = restore_storage_a = restore_storage_b = None
    active_backend = restore_backend = None
    active_metadata = restore_metadata = None
    active_db_rows = active_object_count = restore_db_rows = restore_object_count = 0
    active_payload = _synthetic_payload(ids)

    try:
        checks["connection_attempted"] = True
        try:
            migration_applier(active_db_url)
            migration_applier(restore_db_url)
            active_storage_a = active_storage_factory(active_db_url, ids["workspace_a"])
            active_storage_b = active_storage_factory(active_db_url, ids["workspace_b"])
            restore_storage_a = restore_storage_factory(restore_db_url, ids["workspace_a"])
            restore_storage_b = restore_storage_factory(restore_db_url, ids["workspace_b"])
            for storage in (active_storage_a, active_storage_b, restore_storage_a, restore_storage_b):
                storage.ensure_ready()
                storage.ensure_object_artifact_ready()
        except Exception:
            blockers.append("database_connection_or_schema_failed")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count

        checks["write_attempted"] = True
        checks["read_attempted"] = True
        try:
            active_db_rows += _write_metadata_rows(
                storage_a=active_storage_a,
                storage_b=active_storage_b,
                ids=ids,
                artifact_metadata=None,
            )
            checks["active_db_write_read_verified"] = _verify_db_rows(active_storage_a, active_storage_b, ids)
            checks["workspace_isolation_preserved"] = checks["active_db_write_read_verified"]
        except Exception:
            blockers.append("active_db_write_failed")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count

        try:
            active_backend = active_backend_factory(env)
            active_metadata = active_backend.store_artifact(
                workspace_id=ids["workspace_a"],
                owner_type="generated_quote",
                owner_id=ids["session_a"],
                artifact_kind="xlsx",
                filename=SYNTHETIC_FILENAME,
                content_type=SYNTHETIC_CONTENT_TYPE,
                content=active_payload,
            )
            active_object_count = 1
            active_db_rows += _write_metadata_rows(
                storage_a=active_storage_a,
                storage_b=active_storage_b,
                ids=ids,
                artifact_metadata=active_metadata,
            ) - 6
            active_content = active_backend.retrieve_artifact(active_metadata, workspace_id=ids["workspace_a"])
            checks["active_object_write_read_verified"] = (
                active_content == active_payload
                and artifact_checksum(active_content) == active_metadata.checksum_sha256
                and _metadata_object_pairing_ok(active_storage_a, ids["session_a"], active_metadata)
            )
        except Exception:
            blockers.append("active_object_write_failed")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count

        try:
            checks["restore_database_cannot_read_active_synthetic_rows"] = _restore_database_cannot_read_active_synthetic_rows(
                restore_storage_a=restore_storage_a,
                restore_storage_b=restore_storage_b,
                ids=ids,
                active_metadata=active_metadata,
            )
        except Exception:
            blockers.append("isolated_restore_target_live_check_failed")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count
        if not checks["restore_database_cannot_read_active_synthetic_rows"]:
            blockers.append("restore_database_can_read_active_synthetic_rows")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count

        try:
            restore_backend = restore_backend_factory(env)
        except Exception:
            blockers.append("restore_object_write_failed")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count
        try:
            restore_backend.retrieve_artifact(active_metadata, workspace_id=ids["workspace_a"])
        except Exception:
            checks["restore_object_cannot_read_active_synthetic_object"] = True
        else:
            blockers.append("restore_object_can_read_active_synthetic_object")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count

        checks["restore_attempted"] = True
        try:
            restore_db_rows += _write_metadata_rows(
                storage_a=restore_storage_a,
                storage_b=restore_storage_b,
                ids=ids,
                artifact_metadata=None,
            )
            checks["restore_db_write_read_verified"] = _verify_db_rows(restore_storage_a, restore_storage_b, ids)
            checks["workspace_isolation_preserved"] = (
                checks["workspace_isolation_preserved"] and checks["restore_db_write_read_verified"]
            )
        except Exception:
            blockers.append("restore_db_write_failed")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count

        try:
            restore_metadata = restore_backend.store_artifact(
                workspace_id=ids["workspace_a"],
                owner_type="generated_quote",
                owner_id=ids["session_a"],
                artifact_kind="xlsx",
                filename=SYNTHETIC_FILENAME,
                content_type=SYNTHETIC_CONTENT_TYPE,
                content=active_payload,
            )
            restore_object_count = 1
            restore_db_rows += _write_metadata_rows(
                storage_a=restore_storage_a,
                storage_b=restore_storage_b,
                ids=ids,
                artifact_metadata=restore_metadata,
            ) - 6
            restored_content = restore_backend.retrieve_artifact(restore_metadata, workspace_id=ids["workspace_a"])
            restored_checksum = hashlib.sha256(restored_content).hexdigest()
            checks["checksum_match"] = restored_checksum == active_metadata.checksum_sha256
            checks["metadata_object_pairing_verified"] = _metadata_object_pairing_ok(
                restore_storage_a,
                ids["session_a"],
                restore_metadata,
            )
            checks["restore_object_write_read_verified"] = (
                bool(restored_content)
                and restored_checksum == restore_metadata.checksum_sha256
                and len(restored_content) == restore_metadata.size_bytes
            )
        except Exception:
            blockers.append("restore_object_write_failed")
            return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count

        if not checks["checksum_match"]:
            blockers.append("checksum_mismatch")
        if not checks["metadata_object_pairing_verified"]:
            blockers.append("metadata_object_pairing_mismatch")
        if not checks["workspace_isolation_preserved"]:
            blockers.append("workspace_isolation_failed")
        if not checks["active_db_write_read_verified"]:
            blockers.append("active_db_read_failed")
        if not checks["active_object_write_read_verified"]:
            blockers.append("active_object_read_failed")
        if not checks["restore_db_write_read_verified"]:
            blockers.append("restore_db_read_failed")
        if not checks["restore_object_write_read_verified"]:
            blockers.append("restore_object_read_failed")
        return checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count
    finally:
        cleanup_completed = _cleanup(
            active_storage_a=active_storage_a,
            active_storage_b=active_storage_b,
            restore_storage_a=restore_storage_a,
            restore_storage_b=restore_storage_b,
            active_backend=active_backend,
            restore_backend=restore_backend,
            active_metadata=active_metadata,
            restore_metadata=restore_metadata,
            ids=ids,
        )
        checks["cleanup_completed"] = cleanup_completed
        if not cleanup_completed and "cleanup_failed" not in blockers:
            blockers.append("cleanup_failed")


def run_verification(
    *,
    env: Mapping[str, str] | None = None,
    active_storage_factory: StorageFactory | None = None,
    restore_storage_factory: StorageFactory | None = None,
    active_backend_factory: BackendFactory | None = None,
    restore_backend_factory: BackendFactory | None = None,
    migration_applier: MigrationApplier | None = None,
    execute_live_drill: bool = True,
    test_injected_backend: bool = False,
) -> dict[str, object]:
    effective_env = dict(os.environ if env is None else env)
    missing = _missing_env_names(effective_env)
    live_opt_in_enabled = _enabled(effective_env)
    decision_present = _present(effective_env, BACKUP_RESTORE_DECISION_ID_ENV_NAME)
    window_present = _present(effective_env, BACKUP_RESTORE_WINDOW_ID_ENV_NAME)
    database_targets_distinct = _database_targets_distinct(effective_env)
    object_targets_distinct = _object_targets_distinct(effective_env)
    isolated_restore_target = database_targets_distinct and object_targets_distinct
    checks = _default_checks(
        live_opt_in_enabled=live_opt_in_enabled,
        decision_present=decision_present,
        window_present=window_present,
        active_database_present=_present(effective_env, webapp.SQAG_DATABASE_URL_ENV_NAME),
        restore_database_present=_present(effective_env, RESTORE_DATABASE_URL_ENV_NAME),
        database_targets_distinct=database_targets_distinct,
        active_object_present=all(_present(effective_env, name) for name in ACTIVE_OBJECT_ENV_NAMES),
        restore_object_present=all(_present(effective_env, name) for name in RESTORE_OBJECT_ENV_NAMES),
        object_targets_distinct=object_targets_distinct,
        isolated_restore_target=isolated_restore_target,
    )

    blockers: list[str] = []
    if missing or not live_opt_in_enabled:
        blockers.append("live_db_object_backup_restore_evidence_not_enabled_or_incomplete")
    if not isolated_restore_target:
        blockers.append("blocked_isolated_restore_target_missing")
    if not (decision_present and window_present):
        blockers.append("blocked_backup_restore_decision_missing")
    if not execute_live_drill and not blockers:
        blockers.append("live_db_object_backup_restore_execution_not_enabled")
    if blockers:
        return _report(
            status="blocked",
            checks=checks,
            missing_env_names=missing,
            blockers=blockers,
            test_injected_backend=test_injected_backend,
        )

    checks, blockers, active_db_rows, active_object_count, restore_db_rows, restore_object_count = _run_drill(
        env=effective_env,
        checks=checks,
        blockers=blockers,
        active_storage_factory=active_storage_factory or _build_default_storage,
        restore_storage_factory=restore_storage_factory or _build_default_storage,
        active_backend_factory=active_backend_factory or _active_backend_factory,
        restore_backend_factory=restore_backend_factory or _restore_backend_factory,
        migration_applier=migration_applier or webapp.apply_sqag_storage_migrations,
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
        restore_db_rows=restore_db_rows,
        restore_object_count=restore_object_count,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Run metadata-only SQAG live DB+object backup/restore drill."
    )


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    report = run_verification()
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if report.get("status") == "passed" and report.get("live_db_object_backup_restore_evidence_supported") else 2


if __name__ == "__main__":
    raise SystemExit(main())
