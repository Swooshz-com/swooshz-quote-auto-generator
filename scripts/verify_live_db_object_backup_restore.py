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
from typing import Any, Callable, Mapping, NamedTuple

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


LIVE_DB_OBJECT_BACKUP_RESTORE_ENV_NAME = "SQAG_LIVE_DB_OBJECT_BACKUP_RESTORE_EVIDENCE"
RESTORE_DATABASE_URL_ENV_NAME = "SQAG_RESTORE_DATABASE_URL"
RESTORE_MIGRATOR_DATABASE_URL_ENV_NAME = "SQAG_RESTORE_MIGRATOR_DATABASE_URL"
RESTORE_MAINTENANCE_DATABASE_URL_ENV_NAME = "SQAG_RESTORE_MAINTENANCE_DATABASE_URL"
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
    webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME,
    webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME,
    *ACTIVE_OBJECT_ENV_NAMES,
    RESTORE_DATABASE_URL_ENV_NAME,
    RESTORE_MIGRATOR_DATABASE_URL_ENV_NAME,
    RESTORE_MAINTENANCE_DATABASE_URL_ENV_NAME,
    *RESTORE_OBJECT_ENV_NAMES,
    BACKUP_RESTORE_DECISION_ID_ENV_NAME,
    BACKUP_RESTORE_WINDOW_ID_ENV_NAME,
]
TRUE_VALUES = {"1", "true", "yes", "on", "run", "enabled"}
SYNTHETIC_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SYNTHETIC_FILENAME = webapp.QUOTE_SESSION_EXPORT_KINDS["xlsx"]
SYNTHETIC_PROBE_OWNER_TYPE = "generated_quote"
SYNTHETIC_ACTIVE_PROBE_KIND = "isolation_probe_active"
SYNTHETIC_RESTORE_PROBE_KIND = "isolation_probe_restore"
SYNTHETIC_PROBE_FILENAME = "sqag-isolation-probe.xlsx"
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


JOURNAL_NOT_REACHED = "not-reached"
JOURNAL_ATTEMPTED = "attempted"
JOURNAL_TOUCHED = "touched"
JOURNAL_UNKNOWN = "unknown"
JOURNAL_CLEANUP_PENDING = "cleanup-pending"
JOURNAL_CLEANED = "cleaned"
JOURNAL_ABSENCE_VERIFIED = "absence-verified"
JOURNAL_CLEANUP_FAILED = "cleanup-failed"
JOURNAL_STATES = (
    JOURNAL_NOT_REACHED,
    JOURNAL_ATTEMPTED,
    JOURNAL_TOUCHED,
    JOURNAL_UNKNOWN,
    JOURNAL_CLEANUP_PENDING,
    JOURNAL_CLEANED,
    JOURNAL_ABSENCE_VERIFIED,
    JOURNAL_CLEANUP_FAILED,
)
JOURNAL_TRANSITIONS = {
    JOURNAL_NOT_REACHED: {
        JOURNAL_ATTEMPTED,
        JOURNAL_ABSENCE_VERIFIED,
        JOURNAL_CLEANUP_FAILED,
    },
    JOURNAL_ATTEMPTED: {JOURNAL_TOUCHED, JOURNAL_UNKNOWN},
    JOURNAL_UNKNOWN: {
        JOURNAL_TOUCHED,
        JOURNAL_ABSENCE_VERIFIED,
        JOURNAL_CLEANUP_FAILED,
    },
    JOURNAL_TOUCHED: {JOURNAL_CLEANUP_PENDING, JOURNAL_CLEANUP_FAILED},
    JOURNAL_CLEANUP_PENDING: {JOURNAL_CLEANED, JOURNAL_CLEANUP_FAILED},
    JOURNAL_CLEANED: set(),
    JOURNAL_ABSENCE_VERIFIED: set(),
    JOURNAL_CLEANUP_FAILED: set(),
}


class ResourceSpec(NamedTuple):
    key: str
    operation: str
    workspace_label: str
    resource_kind: str
    identifier: str


class _JournalEntry:
    def __init__(self, spec: ResourceSpec):
        self.spec = spec
        self._state = JOURNAL_NOT_REACHED
        self.transitions: list[dict[str, str]] = []
        self.touch_receipts: list[str] = []
        self.attempted = False
        self.unknowned = False
        self.destructive_cleanup_attempted = False
        self.destructive_cleanup_calls = 0

    @property
    def state(self) -> str:
        return self._state


class ResourceJournal:
    """Verifier-owned per-run resource state machine.

    The entry state is intentionally private to this bounded API. Callers
    record causal attempts, receipts, resolutions, and cleanup outcomes here;
    cleanup eligibility is never inferred from storage construction.
    """

    def __init__(self, specs: tuple[ResourceSpec, ...]):
        if len(specs) != 16 or len({spec.key for spec in specs}) != len(specs):
            raise ValueError("The live backup/restore drill requires exactly 16 unique resources.")
        self._order = tuple(spec.key for spec in specs)
        self._entries = {spec.key: _JournalEntry(spec=spec) for spec in specs}

    @property
    def keys(self) -> tuple[str, ...]:
        return self._order

    def entry(self, key: str) -> _JournalEntry:
        try:
            return self._entries[key]
        except KeyError as exc:
            raise KeyError(f"Unknown verifier journal resource: {key}") from exc

    def state(self, key: str) -> str:
        return self.entry(key).state

    def transition(self, key: str, target: str, reason: str) -> None:
        if target not in JOURNAL_STATES:
            raise ValueError(f"Unknown verifier journal state: {target}")
        entry = self.entry(key)
        current = entry.state
        if target not in JOURNAL_TRANSITIONS[current]:
            raise ValueError(f"Invalid verifier journal transition: {current} -> {target}")
        entry._state = target
        entry.transitions.append({"from": current, "to": target, "reason": reason})
        if target == JOURNAL_ATTEMPTED:
            entry.attempted = True
        if target == JOURNAL_UNKNOWN:
            entry.unknowned = True

    def mark_attempted(self, key: str, reason: str) -> None:
        self.transition(key, JOURNAL_ATTEMPTED, reason)

    def mark_touched(self, key: str, reason: str) -> None:
        self.transition(key, JOURNAL_TOUCHED, reason)

    def mark_unknown(self, key: str, reason: str) -> None:
        self.transition(key, JOURNAL_UNKNOWN, reason)

    def mark_absence_verified(self, key: str, reason: str) -> None:
        self.transition(key, JOURNAL_ABSENCE_VERIFIED, reason)

    def mark_cleanup_pending(self, key: str, reason: str) -> None:
        self.transition(key, JOURNAL_CLEANUP_PENDING, reason)

    def mark_cleaned(self, key: str, reason: str) -> None:
        self.transition(key, JOURNAL_CLEANED, reason)

    def mark_cleanup_failed(self, key: str, reason: str) -> None:
        self.transition(key, JOURNAL_CLEANUP_FAILED, reason)

    def record_receipt(self, key: str, receipt: str) -> None:
        entry = self.entry(key)
        if receipt not in entry.touch_receipts:
            entry.touch_receipts.append(receipt)

    def mark_destructive_cleanup_attempt(self, key: str, operation: str) -> None:
        entry = self.entry(key)
        if entry.state != JOURNAL_CLEANUP_PENDING:
            raise ValueError("Destructive cleanup requires cleanup-pending journal state.")
        entry.destructive_cleanup_attempted = True
        entry.destructive_cleanup_calls += 1
        self.record_receipt(key, f"destructive-cleanup:{operation}")

    def keys_in_state(self, state: str) -> tuple[str, ...]:
        return tuple(key for key in self._order if self.state(key) == state)

    def report(self) -> dict[str, object]:
        entries: list[dict[str, object]] = []
        state_counts = {state: 0 for state in JOURNAL_STATES}
        attempted_and_resolved = 0
        unknown_and_resolved = 0
        destructive_cleanup_count = 0
        not_written_by_this_run = 0
        for key in self._order:
            entry = self.entry(key)
            state_counts[entry.state] += 1
            if entry.attempted and entry.state != JOURNAL_ATTEMPTED:
                attempted_and_resolved += 1
            if entry.unknowned and entry.state != JOURNAL_UNKNOWN:
                unknown_and_resolved += 1
            if entry.destructive_cleanup_attempted:
                destructive_cleanup_count += 1
            not_written = not entry.attempted
            if not_written:
                not_written_by_this_run += 1
            entries.append(
                {
                    "resource_key": entry.spec.key,
                    "operation": entry.spec.operation,
                    "workspace": entry.spec.workspace_label,
                    "resource_kind": entry.spec.resource_kind,
                    "state": entry.state,
                    "transition_history": [dict(item) for item in entry.transitions],
                    "touch_receipts": list(entry.touch_receipts),
                    "destructive_cleanup_attempted": entry.destructive_cleanup_attempted,
                    "destructive_cleanup_calls": entry.destructive_cleanup_calls,
                    "not_written_by_this_run": not_written,
                }
            )
        return {
            "planned_resource_count": len(self._order),
            "states": state_counts,
            "attempted_and_resolved": attempted_and_resolved,
            "unknown_and_resolved": unknown_and_resolved,
            "destructive_cleanup_attempted": destructive_cleanup_count,
            "not_written_by_this_run": not_written_by_this_run,
            "entries": entries,
        }


class CleanupContext(NamedTuple):
    """Immutable operation/workspace binding used by destructive cleanup."""

    operation: str
    workspace_identity: str
    workspace_label: str
    database_family: str
    artifact_storage_mode: str
    storage: object | None
    maintenance_storage: object | None
    backend: ObjectStorageBackend | None
    backend_origin: str
    backend_state: str
    resource_keys: tuple[str, ...]
    captured_artifacts: tuple[ObjectArtifactMetadata, ...]
    capture_complete: bool
    destructive_cleanup_eligible: bool

    def report(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "workspace": self.workspace_label,
            "database_family": self.database_family,
            "artifact_storage_mode": self.artifact_storage_mode,
            "backend_origin": self.backend_origin,
            "backend_state": self.backend_state,
            "backend_bound": self.backend is not None,
            "ambient_route_allowed": False,
            "resource_keys": list(self.resource_keys),
            "captured_artifact_count": len(self.captured_artifacts),
            "capture_complete": self.capture_complete,
            "destructive_cleanup_eligible": self.destructive_cleanup_eligible,
        }


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
        "active_object_cannot_read_restore_synthetic_object": False,
        "bidirectional_backend_isolation_verified": False,
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
    journal: ResourceJournal | None = None,
    cleanup_contexts: Mapping[str, Mapping[str, object]] | None = None,
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
        "resource_journal": (
            journal.report()
            if journal is not None
            else {
                "planned_resource_count": 0,
                "states": {state: 0 for state in JOURNAL_STATES},
                "attempted_and_resolved": 0,
                "unknown_and_resolved": 0,
                "destructive_cleanup_attempted": 0,
                "not_written_by_this_run": 0,
                "entries": [],
            }
        ),
        "cleanup_contexts": [
            dict(value)
            for value in (cleanup_contexts or {}).values()
        ],
        "notes": [
            "This verifier uses synthetic namespaced rows and one tiny synthetic generated artifact payload only.",
            "It fails closed unless explicit live evidence, isolated restore targets, and operator decision markers are present.",
            "It never restores over active runtime targets and never reports private target values or object keys.",
            "A test-injected backend exercises verifier logic only and is not live production evidence.",
        ],
    }


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
        "active_probe_id": f"{prefix}-active-probe",
        "restore_probe_id": f"{prefix}-restore-probe",
    }


def _synthetic_payload(ids: Mapping[str, str]) -> bytes:
    seed = f"sqag-db-object-restore:{ids['workspace_a']}:{ids['session_a']}".encode("ascii")
    return hashlib.sha256(seed).digest()[:24]


def _synthetic_probe_payload(ids: Mapping[str, str], purpose: str) -> bytes:
    seed = f"sqag-backend-isolation-probe:{purpose}:{ids['workspace_a']}".encode("ascii")
    return hashlib.sha256(seed).digest()[:16]


def _planned_resource_specs(ids: Mapping[str, str]) -> tuple[ResourceSpec, ...]:
    specs: list[ResourceSpec] = []
    for operation in ("active", "restore"):
        for workspace_label in ("workspace_a", "workspace_b"):
            for resource_kind, id_key in (
                ("profile", "profile_a" if workspace_label == "workspace_a" else "profile_b"),
                ("pricing_reference", "pricing_a" if workspace_label == "workspace_a" else "pricing_b"),
                ("quote_session", "session_a" if workspace_label == "workspace_a" else "session_b"),
            ):
                specs.append(
                    ResourceSpec(
                        key=f"{operation}/{workspace_label}/{resource_kind}",
                        operation=operation,
                        workspace_label=workspace_label,
                        resource_kind=resource_kind,
                        identifier=ids[id_key],
                    )
                )
    specs.extend(
        (
            ResourceSpec(
                key="active/workspace_a/generated_xlsx",
                operation="active",
                workspace_label="workspace_a",
                resource_kind="generated_xlsx",
                identifier=ids["session_a"],
            ),
            ResourceSpec(
                key="restore/workspace_a/generated_xlsx",
                operation="restore",
                workspace_label="workspace_a",
                resource_kind="generated_xlsx",
                identifier=ids["session_a"],
            ),
            ResourceSpec(
                key="active/workspace_a/isolation_probe",
                operation="active",
                workspace_label="workspace_a",
                resource_kind="isolation_probe",
                identifier=ids["active_probe_id"],
            ),
            ResourceSpec(
                key="restore/workspace_a/isolation_probe",
                operation="restore",
                workspace_label="workspace_a",
                resource_kind="isolation_probe",
                identifier=ids["restore_probe_id"],
            ),
        )
    )
    return tuple(specs)


def _verifier_artifact_storage_mode(env: Mapping[str, str]) -> str:
    configured = _clean(env.get(webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME)).lower()
    return configured if configured in {"database", "object"} else "object"


def _expected_artifact_descriptor(
    *,
    workspace_id: str,
    owner_type: str,
    owner_id: str,
    artifact_kind: str,
    filename: str,
    content_type: str,
    content: bytes,
) -> dict[str, object]:
    checksum = artifact_checksum(content)
    metadata = ObjectArtifactMetadata(
        workspace_id=workspace_id,
        owner_type=owner_type,
        owner_id=owner_id,
        artifact_kind=artifact_kind,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        checksum_sha256=checksum,
        storage_key=webapp.object_artifact_key(
            workspace_id=workspace_id,
            owner_type=owner_type,
            owner_id=owner_id,
            artifact_kind=artifact_kind,
            filename=filename,
            checksum_sha256=checksum,
        ),
        created_at="1970-01-01T00:00:00Z",
        updated_at="1970-01-01T00:00:00Z",
    )
    return {"metadata": metadata, "content": bytes(content)}


def _expected_artifact_descriptors(
    ids: Mapping[str, str],
    active_payload: bytes,
) -> dict[str, dict[str, object]]:
    return {
        "active/workspace_a/generated_xlsx": _expected_artifact_descriptor(
            workspace_id=ids["workspace_a"],
            owner_type="generated_quote",
            owner_id=ids["session_a"],
            artifact_kind="xlsx",
            filename=SYNTHETIC_FILENAME,
            content_type=SYNTHETIC_CONTENT_TYPE,
            content=active_payload,
        ),
        "restore/workspace_a/generated_xlsx": _expected_artifact_descriptor(
            workspace_id=ids["workspace_a"],
            owner_type="generated_quote",
            owner_id=ids["session_a"],
            artifact_kind="xlsx",
            filename=SYNTHETIC_FILENAME,
            content_type=SYNTHETIC_CONTENT_TYPE,
            content=active_payload,
        ),
        "active/workspace_a/isolation_probe": _expected_artifact_descriptor(
            workspace_id=ids["workspace_a"],
            owner_type=SYNTHETIC_PROBE_OWNER_TYPE,
            owner_id=ids["active_probe_id"],
            artifact_kind=SYNTHETIC_ACTIVE_PROBE_KIND,
            filename=SYNTHETIC_PROBE_FILENAME,
            content_type=SYNTHETIC_CONTENT_TYPE,
            content=_synthetic_probe_payload(ids, "active"),
        ),
        "restore/workspace_a/isolation_probe": _expected_artifact_descriptor(
            workspace_id=ids["workspace_a"],
            owner_type=SYNTHETIC_PROBE_OWNER_TYPE,
            owner_id=ids["restore_probe_id"],
            artifact_kind=SYNTHETIC_RESTORE_PROBE_KIND,
            filename=SYNTHETIC_PROBE_FILENAME,
            content_type=SYNTHETIC_CONTENT_TYPE,
            content=_synthetic_probe_payload(ids, "restore"),
        ),
    }


def _artifact_receipt_matches(
    receipt: object,
    expected: ObjectArtifactMetadata,
) -> bool:
    if not isinstance(receipt, ObjectArtifactMetadata):
        return False
    return all(
        (
            receipt.workspace_id == expected.workspace_id,
            receipt.owner_type == expected.owner_type,
            receipt.owner_id == expected.owner_id,
            receipt.artifact_kind == expected.artifact_kind,
            receipt.filename == expected.filename,
            receipt.content_type == expected.content_type,
            receipt.size_bytes == expected.size_bytes,
            receipt.checksum_sha256 == expected.checksum_sha256,
            receipt.storage_key == expected.storage_key,
        )
    )


def _backend_retrieval_proves_absence(
    backend: ObjectStorageBackend,
    metadata: ObjectArtifactMetadata,
    *,
    workspace_id: str,
) -> bool:
    try:
        backend.retrieve_artifact(metadata, workspace_id=workspace_id)
    except (ObjectStorageNotFoundError, KeyError):
        return True
    except Exception:
        return False
    return False


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


def _owner_write_payload(
    resource_kind: str,
    identifier: str,
    workspace_label: str,
) -> tuple[str, dict[str, object], dict[str, object]]:
    if resource_kind == "profile":
        return (
            "save_profile",
            {"id": identifier, "label": f"SQAG synthetic restore drill profile {workspace_label[-1].upper()}"},
            {"id": identifier},
        )
    if resource_kind == "pricing_reference":
        return (
            "save_pricing_reference",
            {"id": identifier, "label": f"SQAG synthetic restore drill pricing {workspace_label[-1].upper()}", "items": []},
            {"id": identifier},
        )
    if resource_kind == "quote_session":
        return (
            "create_or_update_quote_session",
            {"session_id": identifier, "customer_summary": {"name": f"Synthetic Restore Drill {workspace_label[-1].upper()}"}},
            {"session_id": identifier},
        )
    raise ValueError(f"Unsupported verifier owner resource: {resource_kind}")


def _owner_receipt_is_valid(
    receipt: object,
    expected: Mapping[str, object],
) -> bool:
    return isinstance(receipt, Mapping) and all(
        _clean(receipt.get(key)) == _clean(value)
        for key, value in expected.items()
    )


def _write_owner_resource(
    *,
    storage: object,
    backend: ObjectStorageBackend | None,
    journal: ResourceJournal,
    resource_key: str,
    resource_kind: str,
    identifier: str,
    workspace_label: str,
    artifact_storage_mode: str = "object",
) -> object:
    method_name, payload, expected_receipt = _owner_write_payload(
        resource_kind,
        identifier,
        workspace_label,
    )
    journal.mark_attempted(resource_key, f"dispatch:{method_name}")
    try:
        method = getattr(storage, method_name)
        def invoke() -> object:
            if resource_kind == "quote_session":
                return method(payload, session_id=identifier)
            return method(payload)

        if backend is None:
            if artifact_storage_mode == "object":
                raise ObjectStorageConfigurationError(
                    "Operation-specific object backend is required for this operation."
                )
            receipt = invoke()
        elif resource_kind == "quote_session":
            receipt = _with_configured_backend(
                backend,
                lambda: method(payload, session_id=identifier),
            )
        else:
            receipt = _with_configured_backend(backend, lambda: method(payload))
    except Exception:
        journal.mark_unknown(resource_key, f"outcome-uncertain:{method_name}")
        raise
    if not _owner_receipt_is_valid(receipt, expected_receipt):
        journal.mark_unknown(resource_key, f"receipt-invalid:{method_name}")
        raise ObjectStorageContractError("Synthetic owner write receipt is invalid.")
    journal.record_receipt(resource_key, f"validated:{method_name}")
    journal.mark_touched(resource_key, f"receipt:{method_name}")
    return receipt


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


def _active_artifact_metadata(
    storage: object,
    owner_type: str,
    owner_id: str,
    artifact_kind: str = "",
) -> list[ObjectArtifactMetadata] | None:
    reader = getattr(storage, "_active_object_artifact_rows", None)
    converter = getattr(storage, "_object_metadata_from_row", None)
    if not callable(reader) or not callable(converter):
        return []
    try:
        rows = reader(owner_type, owner_id, artifact_kind=artifact_kind)
    except Exception:
        return None
    metadata: list[ObjectArtifactMetadata] = []
    for row in rows:
        try:
            metadata.append(converter(row))
        except Exception:
            return None
    return metadata


def _database_rows(
    storage: object,
    query: str,
    params: tuple[object, ...],
) -> list[object] | None | bool:
    connection_factory = getattr(storage, "connection", None)
    if not callable(connection_factory):
        return None
    try:
        with connection_factory() as connection:
            return list(connection.execute(query, params).fetchall())
    except Exception:
        return False


def _backend_artifact_absent(
    backend: ObjectStorageBackend,
    metadata: ObjectArtifactMetadata,
    *,
    workspace_id: str,
) -> bool:
    try:
        backend.retrieve_artifact(metadata, workspace_id=workspace_id)
    except (ObjectStorageNotFoundError, KeyError):
        return True
    except Exception:
        return False
    return False


def _delete_backend_artifact_and_verify(
    backend: ObjectStorageBackend,
    metadata: ObjectArtifactMetadata,
    *,
    workspace_id: str,
) -> bool:
    try:
        delete_result = backend.delete_artifact(metadata, workspace_id=workspace_id)
    except Exception:
        return False
    absent = _backend_artifact_absent(
        backend,
        metadata,
        workspace_id=workspace_id,
    )
    if delete_result is False and not absent:
        return False
    return absent


def _object_rows_are_deleted(rows: list[object]) -> bool:
    for row in rows:
        if not (
            _clean(_row_value(row, "status")) == "deleted"
            and _clean(_row_value(row, "retention_status")) == "deleted"
            and _clean(_row_value(row, "deleted_at"))
        ):
            return False
    return True


def _cleanup_storage(
    storage: object,
    *,
    profile_id: str,
    pricing_id: str,
    session_id: str,
    backend: ObjectStorageBackend | None,
    captured_artifacts: tuple[ObjectArtifactMetadata, ...] = (),
) -> bool:
    operation_results: dict[str, bool] = {}
    operation_failed = False
    for operation_name, operation in (
        ("quote_session", lambda: storage.delete_quote_session(session_id)),
        ("pricing_reference", lambda: storage.delete_pricing_reference(pricing_id)),
        ("profile", lambda: storage.delete_profile(profile_id)),
    ):
        try:
            operation_results[operation_name] = bool(operation())
        except Exception:
            operation_results[operation_name] = False
            operation_failed = True

    postconditions_ok = not operation_failed
    try:
        if storage.profile_detail(profile_id) is not None:
            postconditions_ok = False
        if storage.pricing_reference_detail(pricing_id) is not None:
            postconditions_ok = False
        if storage.get_quote_session(session_id) is not None:
            postconditions_ok = False
    except Exception:
        postconditions_ok = False

    workspace_id = _clean(getattr(storage, "workspace_id", ""))
    profile_rows = _database_rows(
        storage,
        "select payload_json from sqag_profiles where workspace_id = ? and profile_id = ?",
        (workspace_id, profile_id),
    )
    if profile_rows is False:
        postconditions_ok = False
    elif profile_rows:
        if len(profile_rows) != 1:
            postconditions_ok = False
        else:
            try:
                payload = json.loads(_row_value(profile_rows[0], "payload_json"))
            except (TypeError, json.JSONDecodeError):
                payload = None
            if not isinstance(payload, Mapping) or payload.get(webapp.DELETED_PROFILE_MARKER_KEY) is not True:
                postconditions_ok = False

    for table, id_column, item_id in (
        ("sqag_pricing_references", "reference_id", pricing_id),
        ("sqag_quote_sessions", "session_id", session_id),
    ):
        rows = _database_rows(
            storage,
            f"select 1 from {table} where workspace_id = ? and {id_column} = ?",
            (workspace_id, item_id),
        )
        if rows is False or rows:
            postconditions_ok = False

    # DatabaseSqagStorage exposes the canonical family signal. Hosted
    # PostgreSQL has no local DB-BLOB relations, while SQLite/local paths do.
    # Keep this capability decision explicit rather than inferring it from a
    # missing-table error after issuing an unsupported query.
    if _clean(getattr(storage, "database_family", "")) != "postgres_compatible":
        file_rows = _database_rows(
            storage,
            "select 1 from sqag_file_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
            (workspace_id, "profile", profile_id, "quotation_layout"),
        )
        quote_rows = _database_rows(
            storage,
            "select 1 from sqag_quote_artifacts where workspace_id = ? and session_id = ? and artifact_kind = ?",
            (workspace_id, session_id, "xlsx"),
        )
        if file_rows is False or file_rows or quote_rows is False or quote_rows:
            postconditions_ok = False

    object_rows = _database_rows(
        storage,
        "select * from sqag_object_artifacts where workspace_id = ? and ((owner_type = ? and owner_id = ?) or (owner_type = ? and owner_id = ?) or (owner_type = ? and owner_id = ?))",
        (
            workspace_id,
            "profile",
            profile_id,
            "pricing_reference",
            pricing_id,
            "generated_quote",
            session_id,
        ),
    )
    if object_rows is False or (object_rows is not None and not _object_rows_are_deleted(object_rows)):
        postconditions_ok = False

    if backend is not None:
        for metadata in captured_artifacts:
            if not _backend_artifact_absent(
                backend,
                metadata,
                workspace_id=workspace_id,
            ):
                postconditions_ok = False
    false_returns = any(not result for result in operation_results.values())
    if false_returns and not postconditions_ok:
        return False
    return not operation_failed and postconditions_ok


def _with_configured_backend(
    binding: CleanupContext | ObjectStorageBackend,
    callback: Callable[[], Any],
) -> Any:
    context = binding if isinstance(binding, CleanupContext) else None
    backend = context.backend if context is not None else binding
    if backend is None and context is not None and context.artifact_storage_mode != "object":
        return callback()
    if backend is None:
        raise ObjectStorageConfigurationError(
            "Operation-specific object backend is required for this operation."
        )
    missing = object()
    original_backend_factory = getattr(webapp, "configured_object_storage_backend", missing)
    try:
        webapp.configured_object_storage_backend = lambda: backend  # type: ignore[assignment]
        return callback()
    finally:
        if original_backend_factory is missing:
            delattr(webapp, "configured_object_storage_backend")
        else:
            webapp.configured_object_storage_backend = original_backend_factory  # type: ignore[assignment]


def _store_object_resource(
    *,
    backend: ObjectStorageBackend | None,
    journal: ResourceJournal,
    resource_key: str,
    descriptor: Mapping[str, object],
) -> ObjectArtifactMetadata:
    journal.mark_attempted(resource_key, "dispatch:object-store")
    expected = descriptor.get("metadata")
    content = descriptor.get("content")
    if not isinstance(expected, ObjectArtifactMetadata) or not isinstance(content, bytes) or backend is None:
        journal.mark_unknown(resource_key, "outcome-uncertain:object-backend-missing")
        raise ObjectStorageConfigurationError("Operation-specific object backend is required for this operation.")
    try:
        receipt = backend.store_artifact(
            workspace_id=expected.workspace_id,
            owner_type=expected.owner_type,
            owner_id=expected.owner_id,
            artifact_kind=expected.artifact_kind,
            filename=expected.filename,
            content_type=expected.content_type,
            content=content,
        )
    except Exception:
        journal.mark_unknown(resource_key, "outcome-uncertain:object-store")
        raise
    if not _artifact_receipt_matches(receipt, expected):
        journal.mark_unknown(resource_key, "receipt-invalid:object-store")
        raise ObjectStorageContractError("Synthetic object store receipt is invalid.")
    journal.record_receipt(resource_key, "validated:object-store")
    journal.mark_touched(resource_key, "receipt:object-store")
    return receipt


def _context_resource_keys(
    journal: ResourceJournal,
    operation: str,
    workspace_label: str,
) -> tuple[str, ...]:
    return tuple(
        key
        for key in journal.keys
        if journal.entry(key).spec.operation == operation
        and journal.entry(key).spec.workspace_label == workspace_label
    )


def _capture_context_artifacts(
    storage: object | None,
    *,
    ids: Mapping[str, str],
    workspace_label: str,
    known_artifacts: tuple[ObjectArtifactMetadata, ...],
) -> tuple[tuple[ObjectArtifactMetadata, ...], bool]:
    if storage is None:
        return (), False
    captured: list[ObjectArtifactMetadata] = []
    complete = True
    suffix = "a" if workspace_label == "workspace_a" else "b"
    for owner_type, owner_id in (
        ("profile", ids[f"profile_{suffix}"]),
        ("pricing_reference", ids[f"pricing_{suffix}"]),
        ("generated_quote", ids[f"session_{suffix}"]),
    ):
        artifacts = _active_artifact_metadata(storage, owner_type, owner_id)
        if artifacts is None:
            complete = False
            continue
        captured.extend(artifacts)
    for metadata in known_artifacts:
        if not any(item.storage_key == metadata.storage_key for item in captured):
            captured.append(metadata)
    return tuple(captured), complete


def _build_cleanup_contexts(
    *,
    active_storage_a: object | None,
    active_storage_b: object | None,
    restore_storage_a: object | None,
    restore_storage_b: object | None,
    active_maintenance_storage: object | None,
    restore_maintenance_storage: object | None,
    active_backend: ObjectStorageBackend | None,
    restore_backend: ObjectStorageBackend | None,
    active_backend_origin: str,
    restore_backend_origin: str,
    ids: Mapping[str, str],
    journal: ResourceJournal,
    expected_artifacts: Mapping[str, Mapping[str, object]],
    artifact_metadata: Mapping[str, ObjectArtifactMetadata],
    artifact_storage_mode: str,
    evidence: dict[str, Mapping[str, object]],
) -> tuple[CleanupContext, ...]:
    bindings = (
        (
            "active",
            "workspace_a",
            ids["workspace_a"],
            active_storage_a,
            active_maintenance_storage,
            active_backend,
            active_backend_origin,
        ),
        (
            "active",
            "workspace_b",
            ids["workspace_b"],
            active_storage_b,
            None,
            active_backend,
            active_backend_origin,
        ),
        (
            "restore",
            "workspace_a",
            ids["workspace_a"],
            restore_storage_a,
            restore_maintenance_storage,
            restore_backend,
            restore_backend_origin,
        ),
        (
            "restore",
            "workspace_b",
            ids["workspace_b"],
            restore_storage_b,
            None,
            restore_backend,
            restore_backend_origin,
        ),
    )
    contexts: list[CleanupContext] = []
    for (
        operation,
        workspace_label,
        workspace_identity,
        storage,
        maintenance_storage,
        backend,
        backend_origin,
    ) in bindings:
        resource_keys = _context_resource_keys(journal, operation, workspace_label)
        known: list[ObjectArtifactMetadata] = []
        for key in resource_keys:
            if journal.state(key) not in {JOURNAL_TOUCHED, JOURNAL_CLEANUP_PENDING}:
                continue
            metadata = artifact_metadata.get(key)
            if metadata is None:
                descriptor = expected_artifacts.get(key)
                candidate = descriptor.get("metadata") if descriptor else None
                if isinstance(candidate, ObjectArtifactMetadata):
                    metadata = candidate
            if metadata is not None:
                known.append(metadata)
        captured, capture_complete = _capture_context_artifacts(
            storage,
            ids=ids,
            workspace_label=workspace_label,
            known_artifacts=tuple(known),
        )
        touched = any(journal.state(key) == JOURNAL_TOUCHED for key in resource_keys)
        eligible = bool(
            touched
            and storage is not None
            and (
                artifact_storage_mode != "object"
                or (backend is not None and capture_complete)
            )
        )
        context = CleanupContext(
            operation=operation,
            workspace_identity=workspace_identity,
            workspace_label=workspace_label,
            database_family=_clean(getattr(storage, "database_family", "")) or "unknown",
            artifact_storage_mode=artifact_storage_mode,
            storage=storage,
            maintenance_storage=maintenance_storage,
            backend=backend,
            backend_origin=backend_origin,
            backend_state="available" if backend is not None else "missing",
            resource_keys=resource_keys,
            captured_artifacts=captured,
            capture_complete=capture_complete,
            destructive_cleanup_eligible=eligible,
        )
        contexts.append(context)
        evidence[f"{operation}/{workspace_label}"] = context.report()
    return tuple(contexts)


def _database_unknown_evidence(
    storage: object | None,
    *,
    resource_kind: str,
    identifier: str,
    artifact_storage_mode: str,
) -> str:
    if storage is None:
        return "failed"
    detail_method_name = {
        "profile": "profile_detail",
        "pricing_reference": "pricing_reference_detail",
        "quote_session": "get_quote_session",
    }.get(resource_kind)
    if detail_method_name is None:
        return "failed"
    try:
        detail = getattr(storage, detail_method_name)(identifier)
    except Exception:
        return "failed"
    if detail is not None:
        return "present"
    workspace_id = _clean(getattr(storage, "workspace_id", ""))
    if resource_kind == "profile":
        query = "select payload_json from sqag_profiles where workspace_id = ? and profile_id = ?"
    elif resource_kind == "pricing_reference":
        query = "select 1 from sqag_pricing_references where workspace_id = ? and reference_id = ?"
    else:
        query = "select 1 from sqag_quote_sessions where workspace_id = ? and session_id = ?"
    rows = _database_rows(storage, query, (workspace_id, identifier))
    if rows is None or rows is False:
        return "failed"
    if rows:
        return "present"
    if artifact_storage_mode == "object":
        owner_type = {
            "profile": "profile",
            "pricing_reference": "pricing_reference",
            "quote_session": "generated_quote",
        }[resource_kind]
        artifacts = _active_artifact_metadata(
            storage,
            owner_type,
            identifier,
        )
        if artifacts is None:
            return "failed"
        if artifacts:
            return "present"
    return "absent"


def _resolve_unknown_resource(
    *,
    key: str,
    journal: ResourceJournal,
    storage_by_context: Mapping[str, object | None],
    backend_by_operation: Mapping[str, ObjectStorageBackend | None],
    expected_artifacts: Mapping[str, Mapping[str, object]],
    artifact_metadata: dict[str, ObjectArtifactMetadata],
    artifact_storage_mode: str,
) -> None:
    entry = journal.entry(key)
    spec = entry.spec
    context_key = f"{spec.operation}/{spec.workspace_label}"
    storage = storage_by_context.get(context_key)
    if spec.resource_kind in {"profile", "pricing_reference", "quote_session"}:
        evidence = _database_unknown_evidence(
            storage,
            resource_kind=spec.resource_kind,
            identifier=spec.identifier,
            artifact_storage_mode=artifact_storage_mode,
        )
        if evidence == "present":
            journal.record_receipt(key, "unknown-resolution:database-present")
            journal.mark_touched(key, "unknown-resolution:database-present")
        elif evidence == "absent":
            journal.record_receipt(key, "unknown-resolution:database-absent")
            journal.mark_absence_verified(key, "unknown-resolution:database-absent")
        else:
            journal.mark_cleanup_failed(key, "unknown-resolution:database-read-failed")
        return

    descriptor = expected_artifacts.get(key)
    expected = descriptor.get("metadata") if descriptor else None
    backend = backend_by_operation.get(spec.operation)
    if not isinstance(expected, ObjectArtifactMetadata) or backend is None:
        journal.mark_cleanup_failed(key, "unknown-resolution:operation-backend-missing")
        return
    try:
        backend.retrieve_artifact(expected, workspace_id=expected.workspace_id)
    except (ObjectStorageNotFoundError, KeyError):
        residue = _active_artifact_metadata(
            storage,
            expected.owner_type,
            expected.owner_id,
            artifact_kind=expected.artifact_kind,
        )
        if residue is None:
            journal.mark_cleanup_failed(key, "unknown-resolution:metadata-read-failed")
        elif residue:
            artifact_metadata[key] = residue[0]
            journal.record_receipt(key, "unknown-resolution:metadata-present")
            journal.mark_touched(key, "unknown-resolution:metadata-present")
        else:
            journal.record_receipt(key, "unknown-resolution:object-and-metadata-absent")
            journal.mark_absence_verified(key, "unknown-resolution:object-and-metadata-absent")
    except Exception:
        journal.mark_cleanup_failed(key, "unknown-resolution:object-read-failed")
    else:
        artifact_metadata.setdefault(key, expected)
        journal.record_receipt(key, "unknown-resolution:object-present")
        journal.mark_touched(key, "unknown-resolution:object-present")


def _normalize_and_resolve_journal(
    *,
    journal: ResourceJournal,
    storage_by_context: Mapping[str, object | None],
    backend_by_operation: Mapping[str, ObjectStorageBackend | None],
    expected_artifacts: Mapping[str, Mapping[str, object]],
    artifact_metadata: dict[str, ObjectArtifactMetadata],
    artifact_storage_mode: str,
) -> None:
    for key in journal.keys_in_state(JOURNAL_ATTEMPTED):
        journal.mark_unknown(key, "cleanup-normalization:attempted-outcome-uncertain")
    for key in journal.keys_in_state(JOURNAL_UNKNOWN):
        _resolve_unknown_resource(
            key=key,
            journal=journal,
            storage_by_context=storage_by_context,
            backend_by_operation=backend_by_operation,
            expected_artifacts=expected_artifacts,
            artifact_metadata=artifact_metadata,
            artifact_storage_mode=artifact_storage_mode,
        )


def _captured_for_resource(
    context: CleanupContext,
    *,
    resource_kind: str,
    identifier: str,
) -> tuple[ObjectArtifactMetadata, ...]:
    owner_type = {
        "profile": "profile",
        "pricing_reference": "pricing_reference",
        "quote_session": "generated_quote",
    }.get(resource_kind, "")
    if not owner_type:
        return ()
    return tuple(
        metadata
        for metadata in context.captured_artifacts
        if metadata.owner_type == owner_type and metadata.owner_id == identifier
    )


def _owner_postconditions(
    context: CleanupContext,
    *,
    resource_kind: str,
    identifier: str,
) -> bool:
    storage = context.storage
    if storage is None:
        return False
    try:
        detail_method = {
            "profile": "profile_detail",
            "pricing_reference": "pricing_reference_detail",
            "quote_session": "get_quote_session",
        }[resource_kind]
        if getattr(storage, detail_method)(identifier) is not None:
            return False
        workspace_id = _clean(getattr(storage, "workspace_id", ""))
        if resource_kind == "profile":
            profile_rows = _database_rows(
                storage,
                "select payload_json from sqag_profiles where workspace_id = ? and profile_id = ?",
                (workspace_id, identifier),
            )
            if profile_rows is None or profile_rows is False:
                return False
            if profile_rows:
                if len(profile_rows) != 1:
                    return False
                try:
                    payload = json.loads(_row_value(profile_rows[0], "payload_json"))
                except (TypeError, json.JSONDecodeError):
                    payload = None
                if not isinstance(payload, Mapping) or payload.get(webapp.DELETED_PROFILE_MARKER_KEY) is not True:
                    return False
        else:
            table, column = (
                ("sqag_pricing_references", "reference_id")
                if resource_kind == "pricing_reference"
                else ("sqag_quote_sessions", "session_id")
            )
            rows = _database_rows(
                storage,
                f"select 1 from {table} where workspace_id = ? and {column} = ?",
                (workspace_id, identifier),
            )
            if rows is None or rows is False or rows:
                return False

        if context.database_family != "postgres_compatible":
            if resource_kind == "profile":
                file_rows = _database_rows(
                    storage,
                    "select 1 from sqag_file_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
                    (workspace_id, "profile", identifier, "quotation_layout"),
                )
                if file_rows is None or file_rows is False or file_rows:
                    return False
            if resource_kind == "quote_session":
                quote_rows = _database_rows(
                    storage,
                    "select 1 from sqag_quote_artifacts where workspace_id = ? and session_id = ? and artifact_kind = ?",
                    (workspace_id, identifier, "xlsx"),
                )
                if quote_rows is None or quote_rows is False or quote_rows:
                    return False

        object_rows = _database_rows(
            storage,
            "select * from sqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ?",
            (workspace_id, {
                "profile": "profile",
                "pricing_reference": "pricing_reference",
                "quote_session": "generated_quote",
            }[resource_kind], identifier),
        )
        if object_rows is None or object_rows is False or not _object_rows_are_deleted(object_rows):
            return False
        for metadata in _captured_for_resource(
            context,
            resource_kind=resource_kind,
            identifier=identifier,
        ):
            if context.backend is None or not _backend_artifact_absent(
                context.backend,
                metadata,
                workspace_id=workspace_id,
            ):
                return False
    except Exception:
        return False
    return True


def _object_postconditions(
    context: CleanupContext,
    metadata: ObjectArtifactMetadata,
) -> bool:
    if context.backend is None or not _backend_artifact_absent(
        context.backend,
        metadata,
        workspace_id=metadata.workspace_id,
    ):
        return False
    if metadata.artifact_kind != "xlsx":
        return True
    storage = context.storage
    if storage is None:
        return False
    rows = _database_rows(
        storage,
        "select * from sqag_object_artifacts where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
        (
            metadata.workspace_id,
            metadata.owner_type,
            metadata.owner_id,
            metadata.artifact_kind,
        ),
    )
    if rows is None or rows is False:
        return False
    for row in rows:
        if not all(
            (
                _clean(_row_value(row, "workspace_id")) == metadata.workspace_id,
                _clean(_row_value(row, "owner_type")) == metadata.owner_type,
                _clean(_row_value(row, "owner_id")) == metadata.owner_id,
                _clean(_row_value(row, "artifact_kind")) == metadata.artifact_kind,
                _clean(_row_value(row, "filename")) == metadata.filename,
                _clean(_row_value(row, "checksum_sha256")) == metadata.checksum_sha256,
                _clean(_row_value(row, "object_key_ref")) == metadata.storage_key,
            )
        ):
            return False
    return _object_rows_are_deleted(rows)


def _resource_cleanup_is_eligible(
    context: CleanupContext,
    resource_kind: str,
) -> bool:
    if context.storage is None:
        return False
    if resource_kind in {"generated_xlsx", "isolation_probe"}:
        return context.backend is not None
    if context.artifact_storage_mode == "object":
        return context.backend is not None and context.capture_complete
    return True


def _cleanup_owner_resource(
    *,
    context: CleanupContext,
    journal: ResourceJournal,
    key: str,
    resource_kind: str,
    identifier: str,
) -> bool:
    if journal.state(key) != JOURNAL_TOUCHED:
        return True
    if not _resource_cleanup_is_eligible(context, resource_kind):
        journal.mark_cleanup_failed(key, "destructive-cleanup:ineligible-context")
        return False
    journal.mark_cleanup_pending(key, "destructive-cleanup:owner-pending")
    journal.mark_destructive_cleanup_attempt(key, f"delete-{resource_kind}")
    method_name = {
        "profile": "delete_profile",
        "pricing_reference": "delete_pricing_reference",
        "quote_session": "delete_quote_session",
    }[resource_kind]
    try:
        result = _with_configured_backend(
            context,
            lambda: getattr(context.storage, method_name)(identifier),
        )
    except Exception:
        journal.mark_cleanup_failed(key, f"destructive-cleanup:{resource_kind}-exception")
        return False
    if not _owner_postconditions(
        context,
        resource_kind=resource_kind,
        identifier=identifier,
    ):
        journal.mark_cleanup_failed(key, f"destructive-cleanup:{resource_kind}-residue")
        return False
    journal.record_receipt(
        key,
        f"cleanup-receipt:{resource_kind}-deleted" if result else f"cleanup-receipt:{resource_kind}-already-absent",
    )
    journal.mark_cleaned(key, f"destructive-cleanup:{resource_kind}-postconditions-verified")
    return True


def _cleanup_object_resource(
    *,
    context: CleanupContext,
    journal: ResourceJournal,
    key: str,
    metadata: ObjectArtifactMetadata | None,
    maintenance: bool,
) -> bool:
    if journal.state(key) != JOURNAL_TOUCHED:
        return True
    if metadata is None or not _resource_cleanup_is_eligible(context, journal.entry(key).spec.resource_kind):
        journal.mark_cleanup_failed(key, "destructive-cleanup:object-ineligible-context")
        return False
    journal.mark_cleanup_pending(key, "destructive-cleanup:object-pending")
    ok = True
    if maintenance and context.maintenance_storage is not None:
        tombstone = getattr(context.maintenance_storage, "tombstone_object_quote_artifacts", None)
        if callable(tombstone):
            journal.mark_destructive_cleanup_attempt(key, "quote-maintenance-tombstone")
            try:
                _with_configured_backend(
                    context,
                    lambda: tombstone(metadata.owner_id),
                )
                journal.record_receipt(key, "cleanup-receipt:quote-maintenance-tombstone")
            except Exception:
                ok = False
                journal.record_receipt(key, "cleanup-receipt:quote-maintenance-tombstone-failed")
    journal.mark_destructive_cleanup_attempt(key, "object-delete")
    try:
        deleted = _delete_backend_artifact_and_verify(
            context.backend,  # type: ignore[arg-type]
            metadata,
            workspace_id=metadata.workspace_id,
        )
    except Exception:
        deleted = False
    if not deleted or not _object_postconditions(context, metadata):
        journal.mark_cleanup_failed(key, "destructive-cleanup:object-residue")
        return False
    if not ok:
        journal.record_receipt(key, "cleanup-receipt:maintenance-failed")
        journal.mark_cleanup_failed(key, "destructive-cleanup:maintenance-failed")
        return False
    journal.record_receipt(
        key,
        "cleanup-receipt:object-deleted",
    )
    journal.mark_cleaned(key, "destructive-cleanup:object-postconditions-verified")
    return ok


def _verify_final_context_postconditions(
    *,
    context: CleanupContext,
    journal: ResourceJournal,
    expected_artifacts: Mapping[str, Mapping[str, object]],
    artifact_metadata: Mapping[str, ObjectArtifactMetadata],
) -> bool:
    ok = True
    for key in context.resource_keys:
        if journal.state(key) != JOURNAL_CLEANED:
            continue
        spec = journal.entry(key).spec
        if spec.resource_kind in {"profile", "pricing_reference", "quote_session"}:
            verified = _owner_postconditions(
                context,
                resource_kind=spec.resource_kind,
                identifier=spec.identifier,
            )
        else:
            metadata = artifact_metadata.get(key)
            if metadata is None:
                descriptor = expected_artifacts.get(key)
                candidate = descriptor.get("metadata") if descriptor else None
                metadata = candidate if isinstance(candidate, ObjectArtifactMetadata) else None
            verified = metadata is not None and _object_postconditions(context, metadata)
        if not verified:
            journal.record_receipt(key, "postconditions:failed")
            ok = False
        else:
            journal.record_receipt(key, "postconditions:verified")
    return ok


def _cleanup(
    *,
    active_storage_a: object | None,
    active_storage_b: object | None,
    restore_storage_a: object | None,
    restore_storage_b: object | None,
    active_maintenance_storage: object | None,
    restore_maintenance_storage: object | None,
    active_backend: ObjectStorageBackend | None,
    restore_backend: ObjectStorageBackend | None,
    active_metadata: ObjectArtifactMetadata | None,
    restore_metadata: ObjectArtifactMetadata | None,
    ids: Mapping[str, str],
    journal: ResourceJournal,
    expected_artifacts: Mapping[str, Mapping[str, object]],
    artifact_metadata: dict[str, ObjectArtifactMetadata],
    artifact_storage_mode: str,
    active_backend_origin: str,
    restore_backend_origin: str,
    context_evidence: dict[str, Mapping[str, object]],
    active_probe_metadata: ObjectArtifactMetadata | None = None,
    restore_probe_metadata: ObjectArtifactMetadata | None = None,
) -> bool:
    if active_metadata is not None:
        artifact_metadata["active/workspace_a/generated_xlsx"] = active_metadata
    if restore_metadata is not None:
        artifact_metadata["restore/workspace_a/generated_xlsx"] = restore_metadata
    if active_probe_metadata is not None:
        artifact_metadata["active/workspace_a/isolation_probe"] = active_probe_metadata
    if restore_probe_metadata is not None:
        artifact_metadata["restore/workspace_a/isolation_probe"] = restore_probe_metadata

    storage_by_context = {
        "active/workspace_a": active_storage_a,
        "active/workspace_b": active_storage_b,
        "restore/workspace_a": restore_storage_a,
        "restore/workspace_b": restore_storage_b,
    }
    backend_by_operation = {
        "active": active_backend,
        "restore": restore_backend,
    }
    _normalize_and_resolve_journal(
        journal=journal,
        storage_by_context=storage_by_context,
        backend_by_operation=backend_by_operation,
        expected_artifacts=expected_artifacts,
        artifact_metadata=artifact_metadata,
        artifact_storage_mode=artifact_storage_mode,
    )

    contexts = _build_cleanup_contexts(
        active_storage_a=active_storage_a,
        active_storage_b=active_storage_b,
        restore_storage_a=restore_storage_a,
        restore_storage_b=restore_storage_b,
        active_maintenance_storage=active_maintenance_storage,
        restore_maintenance_storage=restore_maintenance_storage,
        active_backend=active_backend,
        restore_backend=restore_backend,
        active_backend_origin=active_backend_origin,
        restore_backend_origin=restore_backend_origin,
        ids=ids,
        journal=journal,
        expected_artifacts=expected_artifacts,
        artifact_metadata=artifact_metadata,
        artifact_storage_mode=artifact_storage_mode,
        evidence=context_evidence,
    )

    ok = True
    for context in contexts:
        for key in context.resource_keys:
            if journal.state(key) != JOURNAL_TOUCHED:
                continue
            if not _resource_cleanup_is_eligible(
                context,
                journal.entry(key).spec.resource_kind,
            ):
                journal.mark_cleanup_failed(key, "destructive-cleanup:ineligible-context")
                ok = False

    for context in contexts:
        probe_key = f"{context.operation}/{context.workspace_label}/isolation_probe"
        if probe_key not in journal.keys:
            continue
        if journal.state(probe_key) == JOURNAL_TOUCHED:
            metadata = artifact_metadata.get(probe_key)
            if metadata is None:
                descriptor = expected_artifacts.get(probe_key)
                candidate = descriptor.get("metadata") if descriptor else None
                metadata = candidate if isinstance(candidate, ObjectArtifactMetadata) else None
            if not _cleanup_object_resource(
                context=context,
                journal=journal,
                key=probe_key,
                metadata=metadata,
                maintenance=False,
            ):
                ok = False

    for context in contexts:
        object_key = f"{context.operation}/{context.workspace_label}/generated_xlsx"
        if object_key not in journal.keys:
            continue
        if journal.state(object_key) == JOURNAL_TOUCHED:
            metadata = artifact_metadata.get(object_key)
            if metadata is None:
                descriptor = expected_artifacts.get(object_key)
                candidate = descriptor.get("metadata") if descriptor else None
                metadata = candidate if isinstance(candidate, ObjectArtifactMetadata) else None
            if not _cleanup_object_resource(
                context=context,
                journal=journal,
                key=object_key,
                metadata=metadata,
                maintenance=context.workspace_label == "workspace_a",
            ):
                ok = False

    for resource_kind in ("quote_session", "pricing_reference", "profile"):
        for context in contexts:
            for key in context.resource_keys:
                spec = journal.entry(key).spec
                if spec.resource_kind != resource_kind:
                    continue
                if not _cleanup_owner_resource(
                    context=context,
                    journal=journal,
                    key=key,
                    resource_kind=resource_kind,
                    identifier=spec.identifier,
                ):
                    ok = False

    for context in contexts:
        if not _verify_final_context_postconditions(
            context=context,
            journal=journal,
            expected_artifacts=expected_artifacts,
            artifact_metadata=artifact_metadata,
        ):
            ok = False

    for left, right, left_ids, right_ids in (
        (
            active_storage_a,
            active_storage_b,
            ("profile_a", "pricing_a", "session_a"),
            ("profile_b", "pricing_b", "session_b"),
        ),
        (
            restore_storage_a,
            restore_storage_b,
            ("profile_a", "pricing_a", "session_a"),
            ("profile_b", "pricing_b", "session_b"),
        ),
    ):
        if left is None or right is None:
            continue
        try:
            if any(
                (
                    left.profile_detail(ids[right_ids[0]]) is not None,
                    left.pricing_reference_detail(ids[right_ids[1]]) is not None,
                    left.get_quote_session(ids[right_ids[2]]) is not None,
                    right.profile_detail(ids[left_ids[0]]) is not None,
                    right.pricing_reference_detail(ids[left_ids[1]]) is not None,
                    right.get_quote_session(ids[left_ids[2]]) is not None,
                )
            ):
                ok = False
        except Exception:
            ok = False
    if any(
        journal.state(key)
        in {JOURNAL_ATTEMPTED, JOURNAL_UNKNOWN, JOURNAL_CLEANUP_PENDING, JOURNAL_CLEANUP_FAILED}
        for key in journal.keys
    ):
        ok = False
    return ok


def _run_drill(
    *,
    env: Mapping[str, str],
    checks: dict[str, bool],
    blockers: list[str],
    active_storage_factory: StorageFactory,
    restore_storage_factory: StorageFactory,
    active_maintenance_storage_factory: StorageFactory,
    restore_maintenance_storage_factory: StorageFactory,
    active_backend_factory: BackendFactory,
    restore_backend_factory: BackendFactory,
    migration_applier: MigrationApplier,
) -> tuple[
    dict[str, bool],
    list[str],
    int,
    int,
    int,
    int,
    ResourceJournal,
    dict[str, Mapping[str, object]],
]:
    ids = _synthetic_ids()
    active_payload = _synthetic_payload(ids)
    expected_artifacts = _expected_artifact_descriptors(ids, active_payload)
    journal = ResourceJournal(_planned_resource_specs(ids))
    context_evidence: dict[str, Mapping[str, object]] = {}
    artifact_metadata: dict[str, ObjectArtifactMetadata] = {}
    artifact_storage_mode = _verifier_artifact_storage_mode(env)

    active_db_url = _clean(env.get(webapp.SQAG_DATABASE_URL_ENV_NAME))
    active_migrator_db_url = _clean(env.get(webapp.SQAG_MIGRATOR_DATABASE_URL_ENV_NAME))
    active_maintenance_db_url = _clean(env.get(webapp.SQAG_MAINTENANCE_DATABASE_URL_ENV_NAME))
    restore_db_url = _clean(env.get(RESTORE_DATABASE_URL_ENV_NAME))
    restore_migrator_db_url = _clean(env.get(RESTORE_MIGRATOR_DATABASE_URL_ENV_NAME))
    restore_maintenance_db_url = _clean(env.get(RESTORE_MAINTENANCE_DATABASE_URL_ENV_NAME))
    active_storage_a = active_storage_b = restore_storage_a = restore_storage_b = None
    active_maintenance_storage = restore_maintenance_storage = None
    active_backend = restore_backend = None
    active_metadata = restore_metadata = None
    active_probe_metadata = restore_probe_metadata = None
    active_db_rows = active_object_count = restore_db_rows = restore_object_count = 0
    active_backend_origin = "active-backend-factory"
    restore_backend_origin = "restore-backend-factory"

    def result():
        return (
            checks,
            blockers,
            active_db_rows,
            active_object_count,
            restore_db_rows,
            restore_object_count,
            journal,
            context_evidence,
        )

    try:
        checks["connection_attempted"] = True
        try:
            migration_applier(active_migrator_db_url)
            migration_applier(restore_migrator_db_url)
            active_storage_a = active_storage_factory(active_db_url, ids["workspace_a"])
            active_storage_b = active_storage_factory(active_db_url, ids["workspace_b"])
            restore_storage_a = restore_storage_factory(restore_db_url, ids["workspace_a"])
            restore_storage_b = restore_storage_factory(restore_db_url, ids["workspace_b"])
            active_maintenance_storage = active_maintenance_storage_factory(active_maintenance_db_url, ids["workspace_a"])
            restore_maintenance_storage = restore_maintenance_storage_factory(restore_maintenance_db_url, ids["workspace_a"])
            for storage in (active_storage_a, active_storage_b, restore_storage_a, restore_storage_b):
                storage.ensure_ready()
                storage.ensure_object_artifact_ready()
            for storage in (active_maintenance_storage, restore_maintenance_storage):
                storage.ensure_object_artifact_ready()
        except Exception:
            blockers.append("database_connection_or_schema_failed")
            return result()

        checks["write_attempted"] = True
        checks["read_attempted"] = True
        try:
            active_backend = active_backend_factory(env)
        except Exception:
            blockers.append("active_object_write_failed")
            return result()

        try:
            active_probe_metadata = _store_object_resource(
                backend=active_backend,
                journal=journal,
                resource_key="active/workspace_a/isolation_probe",
                descriptor=expected_artifacts["active/workspace_a/isolation_probe"],
            )
            artifact_metadata["active/workspace_a/isolation_probe"] = active_probe_metadata
        except Exception:
            blockers.append("active_object_write_failed")
            return result()

        active_owner_writes = (
            ("active/workspace_a/profile", active_storage_a, "profile", ids["profile_a"], "workspace_a"),
            ("active/workspace_b/profile", active_storage_b, "profile", ids["profile_b"], "workspace_b"),
            ("active/workspace_a/pricing_reference", active_storage_a, "pricing_reference", ids["pricing_a"], "workspace_a"),
            ("active/workspace_b/pricing_reference", active_storage_b, "pricing_reference", ids["pricing_b"], "workspace_b"),
            ("active/workspace_a/quote_session", active_storage_a, "quote_session", ids["session_a"], "workspace_a"),
            ("active/workspace_b/quote_session", active_storage_b, "quote_session", ids["session_b"], "workspace_b"),
        )
        try:
            for resource_key, storage, resource_kind, identifier, workspace_label in active_owner_writes:
                _write_owner_resource(
                    storage=storage,
                    backend=active_backend,
                    journal=journal,
                    resource_key=resource_key,
                    resource_kind=resource_kind,
                    identifier=identifier,
                    workspace_label=workspace_label,
                    artifact_storage_mode=artifact_storage_mode,
                )
                active_db_rows += 1
            checks["active_db_write_read_verified"] = _verify_db_rows(active_storage_a, active_storage_b, ids)
            checks["workspace_isolation_preserved"] = checks["active_db_write_read_verified"]
        except Exception:
            blockers.append("active_db_write_failed")
            return result()

        try:
            active_metadata = _store_object_resource(
                backend=active_backend,
                journal=journal,
                resource_key="active/workspace_a/generated_xlsx",
                descriptor=expected_artifacts["active/workspace_a/generated_xlsx"],
            )
            artifact_metadata["active/workspace_a/generated_xlsx"] = active_metadata
            active_object_count = 1
            _with_configured_backend(
                active_backend,
                lambda: active_storage_a._upsert_object_quote_artifact(
                    ids["session_a"],
                    "xlsx",
                    SYNTHETIC_FILENAME,
                    SYNTHETIC_CONTENT_TYPE,
                    active_metadata,
                ),
            )
            if not _metadata_object_pairing_ok(active_storage_a, ids["session_a"], active_metadata):
                journal.record_receipt("active/workspace_a/generated_xlsx", "metadata-upsert:invalid")
                checks["metadata_object_pairing_verified"] = False
                blockers.append("active_object_metadata_write_failed")
                return result()
            journal.record_receipt("active/workspace_a/generated_xlsx", "validated:metadata-upsert")
            active_db_rows += 1
            active_content = active_backend.retrieve_artifact(active_metadata, workspace_id=ids["workspace_a"])
            checks["active_object_write_read_verified"] = (
                active_content == active_payload
                and artifact_checksum(active_content) == active_metadata.checksum_sha256
                and _metadata_object_pairing_ok(active_storage_a, ids["session_a"], active_metadata)
            )
        except Exception:
            journal.record_receipt("active/workspace_a/generated_xlsx", "metadata-upsert:outcome-uncertain")
            blockers.append("active_object_write_failed")
            return result()

        try:
            checks["restore_database_cannot_read_active_synthetic_rows"] = _restore_database_cannot_read_active_synthetic_rows(
                restore_storage_a=restore_storage_a,
                restore_storage_b=restore_storage_b,
                ids=ids,
                active_metadata=active_metadata,
            )
        except Exception:
            blockers.append("isolated_restore_target_live_check_failed")
            return result()
        if not checks["restore_database_cannot_read_active_synthetic_rows"]:
            blockers.append("restore_database_can_read_active_synthetic_rows")
            return result()

        try:
            restore_backend = restore_backend_factory(env)
        except Exception:
            blockers.append("restore_object_write_failed")
            return result()
        if restore_backend is None:
            blockers.append("restore_object_write_failed")
            return result()

        if not _backend_retrieval_proves_absence(
            active_backend,
            expected_artifacts["restore/workspace_a/isolation_probe"]["metadata"],
            workspace_id=ids["workspace_a"],
        ):
            blockers.append("active_object_can_read_restore_synthetic_object")
            return result()
        checks["active_object_cannot_read_restore_synthetic_object"] = True
        if not _backend_retrieval_proves_absence(
            restore_backend,
            expected_artifacts["active/workspace_a/isolation_probe"]["metadata"],
            workspace_id=ids["workspace_a"],
        ):
            blockers.append("restore_object_can_read_active_synthetic_object")
            return result()
        checks["restore_object_cannot_read_active_synthetic_object"] = True
        checks["bidirectional_backend_isolation_verified"] = True

        if active_metadata is not None and not _backend_retrieval_proves_absence(
            restore_backend,
            active_metadata,
            workspace_id=ids["workspace_a"],
        ):
            blockers.append("restore_object_can_read_active_synthetic_object")
            return result()

        checks["restore_attempted"] = True
        try:
            restore_probe_metadata = _store_object_resource(
                backend=restore_backend,
                journal=journal,
                resource_key="restore/workspace_a/isolation_probe",
                descriptor=expected_artifacts["restore/workspace_a/isolation_probe"],
            )
            artifact_metadata["restore/workspace_a/isolation_probe"] = restore_probe_metadata
        except Exception:
            blockers.append("restore_object_write_failed")
            return result()

        restore_owner_writes = (
            ("restore/workspace_a/profile", restore_storage_a, "profile", ids["profile_a"], "workspace_a"),
            ("restore/workspace_b/profile", restore_storage_b, "profile", ids["profile_b"], "workspace_b"),
            ("restore/workspace_a/pricing_reference", restore_storage_a, "pricing_reference", ids["pricing_a"], "workspace_a"),
            ("restore/workspace_b/pricing_reference", restore_storage_b, "pricing_reference", ids["pricing_b"], "workspace_b"),
            ("restore/workspace_a/quote_session", restore_storage_a, "quote_session", ids["session_a"], "workspace_a"),
            ("restore/workspace_b/quote_session", restore_storage_b, "quote_session", ids["session_b"], "workspace_b"),
        )
        try:
            for resource_key, storage, resource_kind, identifier, workspace_label in restore_owner_writes:
                _write_owner_resource(
                    storage=storage,
                    backend=restore_backend,
                    journal=journal,
                    resource_key=resource_key,
                    resource_kind=resource_kind,
                    identifier=identifier,
                    workspace_label=workspace_label,
                    artifact_storage_mode=artifact_storage_mode,
                )
                restore_db_rows += 1
            checks["restore_db_write_read_verified"] = _verify_db_rows(restore_storage_a, restore_storage_b, ids)
            checks["workspace_isolation_preserved"] = (
                checks["workspace_isolation_preserved"] and checks["restore_db_write_read_verified"]
            )
        except Exception:
            blockers.append("restore_db_write_failed")
            return result()

        try:
            restore_metadata = _store_object_resource(
                backend=restore_backend,
                journal=journal,
                resource_key="restore/workspace_a/generated_xlsx",
                descriptor=expected_artifacts["restore/workspace_a/generated_xlsx"],
            )
            artifact_metadata["restore/workspace_a/generated_xlsx"] = restore_metadata
            restore_object_count = 1
            _with_configured_backend(
                restore_backend,
                lambda: restore_storage_a._upsert_object_quote_artifact(
                    ids["session_a"],
                    "xlsx",
                    SYNTHETIC_FILENAME,
                    SYNTHETIC_CONTENT_TYPE,
                    restore_metadata,
                ),
            )
            if not _metadata_object_pairing_ok(restore_storage_a, ids["session_a"], restore_metadata):
                journal.record_receipt("restore/workspace_a/generated_xlsx", "metadata-upsert:invalid")
                checks["metadata_object_pairing_verified"] = False
                blockers.append("metadata_object_pairing_mismatch")
                blockers.append("restore_object_metadata_write_failed")
                return result()
            journal.record_receipt("restore/workspace_a/generated_xlsx", "validated:metadata-upsert")
            restore_db_rows += 1
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
            journal.record_receipt("restore/workspace_a/generated_xlsx", "metadata-upsert:outcome-uncertain")
            blockers.append("restore_object_write_failed")
            return result()

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
        return result()
    finally:
        try:
            cleanup_completed = _cleanup(
                active_storage_a=active_storage_a,
                active_storage_b=active_storage_b,
                restore_storage_a=restore_storage_a,
                restore_storage_b=restore_storage_b,
                active_maintenance_storage=active_maintenance_storage,
                restore_maintenance_storage=restore_maintenance_storage,
                active_backend=active_backend,
                restore_backend=restore_backend,
                active_metadata=active_metadata,
                restore_metadata=restore_metadata,
                ids=ids,
                journal=journal,
                expected_artifacts=expected_artifacts,
                artifact_metadata=artifact_metadata,
                artifact_storage_mode=artifact_storage_mode,
                active_backend_origin=active_backend_origin,
                restore_backend_origin=restore_backend_origin,
                context_evidence=context_evidence,
                active_probe_metadata=active_probe_metadata,
                restore_probe_metadata=restore_probe_metadata,
            )
        except Exception:
            cleanup_completed = False
        checks["cleanup_completed"] = cleanup_completed
        if not cleanup_completed and "cleanup_failed" not in blockers:
            blockers.append("cleanup_failed")


def run_verification(
    *,
    env: Mapping[str, str] | None = None,
    active_storage_factory: StorageFactory | None = None,
    restore_storage_factory: StorageFactory | None = None,
    active_maintenance_storage_factory: StorageFactory | None = None,
    restore_maintenance_storage_factory: StorageFactory | None = None,
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

    (
        checks,
        blockers,
        active_db_rows,
        active_object_count,
        restore_db_rows,
        restore_object_count,
        journal,
        cleanup_contexts,
    ) = _run_drill(
        env=effective_env,
        checks=checks,
        blockers=blockers,
        active_storage_factory=active_storage_factory or _build_default_storage,
        restore_storage_factory=restore_storage_factory or _build_default_storage,
        active_maintenance_storage_factory=(
            active_maintenance_storage_factory
            or (_build_maintenance_storage if active_storage_factory is None else active_storage_factory)
        ),
        restore_maintenance_storage_factory=(
            restore_maintenance_storage_factory
            or (_build_maintenance_storage if restore_storage_factory is None else restore_storage_factory)
        ),
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
        journal=journal,
        cleanup_contexts=cleanup_contexts,
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
