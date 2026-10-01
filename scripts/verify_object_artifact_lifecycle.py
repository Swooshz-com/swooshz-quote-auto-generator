#!/usr/bin/env python3
"""Verify synthetic DB+object artifact lifecycle evidence.

This verifier uses synthetic SQLite databases and the in-memory object backend
only. It does not configure a live cloud object provider or print private
paths, database URLs, object keys, artifact bytes, quote contents, or payloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp
from webapp.object_storage import InMemoryObjectStorageBackend, ObjectStorageContractError


WORKSPACE_ID = "workspace-object-lifecycle"
OTHER_WORKSPACE_ID = "workspace-object-lifecycle-other"
SESSION_ID = "quote-object-lifecycle"
USER_ID = "synthetic-user-object-lifecycle"
ARTIFACT_CONTENT = b"synthetic-private-artifact-bytes"
CONTENT_TYPE = webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"]


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def backup_sqlite_database(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def object_metadata_digest(database_path: Path) -> str:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "select workspace_id, owner_type, owner_id, session_id, artifact_kind, filename, content_type, size_bytes, checksum_sha256, object_provider_type, status, retention_status, deleted_at "
            "from sqag_object_artifacts order by workspace_id, owner_type, owner_id, artifact_kind"
        ).fetchall()
    finally:
        connection.close()
    digest = hashlib.sha256()
    for row in rows:
        for value in row:
            digest.update(str(value).encode("utf-8"))
            digest.update(b"\0")
        digest.update(b"\n")
    return digest.hexdigest()


def journal_digest(database_path: Path) -> str:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "select workspace_id, owner_type, owner_id, operation_seq, operation_id, "
            "request_sha256, plan_json, state, cleanup_json, created_at, updated_at "
            "from sqag_object_artifact_operations "
            "order by workspace_id, owner_type, owner_id, operation_seq"
        ).fetchall()
    finally:
        connection.close()
    digest = hashlib.sha256()
    for row in rows:
        for value in row:
            digest.update(str(value).encode("utf-8"))
            digest.update(b"\0")
        digest.update(b"\n")
    return digest.hexdigest()

def row_count(database_path: Path, table: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        return int(connection.execute(f"select count(*) from {table}").fetchone()[0])
    finally:
        connection.close()


def seed_matching_backend(database_path: Path) -> InMemoryObjectStorageBackend:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "select * from sqag_object_artifacts where workspace_id = ? "
            "and owner_type = ? and owner_id = ? and artifact_kind = ?",
            (WORKSPACE_ID, "generated_quote", SESSION_ID, "xlsx"),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("Synthetic object artifact metadata is unavailable.")
    metadata = storage_for(database_path)._object_metadata_from_row(row)
    backend = InMemoryObjectStorageBackend()
    backend.store_artifact(
        workspace_id=metadata.workspace_id,
        owner_type=metadata.owner_type,
        owner_id=metadata.owner_id,
        artifact_kind=metadata.artifact_kind,
        filename=metadata.filename,
        content_type=metadata.content_type,
        content=ARTIFACT_CONTENT,
        artifact_incarnation=metadata.artifact_incarnation,
        artifact_id=metadata.artifact_id,
        platform_user_id=metadata.platform_user_id,
        session_id=metadata.session_id,
        job_id=metadata.job_id,
        binding_sha256=metadata.binding_sha256,
        created_at=metadata.created_at,
        updated_at=metadata.updated_at,
    )
    return backend


def storage_for(database_path: Path, workspace_id: str = WORKSPACE_ID) -> webapp.DatabaseSqagStorage:
    return webapp.DatabaseSqagStorage(sqlite_url(database_path), workspace_id, "admin", USER_ID)


def object_mode_env(database_path: Path) -> dict[str, str]:
    return {
        "SQAG_STORAGE_MODE": "database",
        "SQAG_ARTIFACT_STORAGE_MODE": "object",
        "SQAG_DATABASE_URL": sqlite_url(database_path),
        "SQAG_OBJECT_STORAGE_PROVIDER": "s3_compatible",
        "SQAG_OBJECT_STORAGE_ENDPOINT_URL": "https://object-store.example.test",
        "SQAG_OBJECT_STORAGE_BUCKET": "example-artifact-bucket",
        "SQAG_OBJECT_STORAGE_REGION": "ap-southeast-1",
        "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID": "EXAMPLE_ACCESS_KEY_ID",
        "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY": "example-secret-key",
    }


def seed_source_database(database_path: Path, staging_root: Path) -> dict[str, bool]:
    backend = InMemoryObjectStorageBackend()
    webapp.apply_sqag_storage_migrations(sqlite_url(database_path))
    output_dir = staging_root / "job-object-lifecycle"
    output_dir.mkdir(parents=True, exist_ok=True)
    staging_file = output_dir / "quotation.xlsx"
    staging_file.write_bytes(ARTIFACT_CONTENT)
    payload = {
        "quote_session": {"session_id": SESSION_ID},
        "customer": {"name": "Synthetic Object Lifecycle"},
    }
    result = {"status": "completed", "files": [{"name": "quotation.xlsx", "url": "/api/jobs/job-object-lifecycle/files/quotation.xlsx"}]}
    with (
        mock.patch.dict(webapp.os.environ, object_mode_env(database_path), clear=True),
        mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
    ):
        storage = storage_for(database_path)
        storage.create_or_update_quote_session(payload, result=result, output_dir=output_dir)
        artifact = storage.quote_session_export_artifact(SESSION_ID, "xlsx")
    return {
        "local_staging_files_cleaned": not staging_file.exists(),
        "stored_artifact_retrieved": bool(artifact and artifact.get("content") == ARTIFACT_CONTENT),
    }


def restored_artifact(database_path: Path, backend: InMemoryObjectStorageBackend, workspace_id: str = WORKSPACE_ID) -> dict[str, Any] | None:
    with (
        mock.patch.dict(webapp.os.environ, object_mode_env(database_path), clear=True),
        mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend),
    ):
        return storage_for(database_path, workspace_id).quote_session_export_artifact(SESSION_ID, "xlsx")


def mark_tombstoned(database_path: Path) -> None:
    now = webapp.utc_timestamp()
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "update sqag_object_artifacts set status = ?, retention_status = ?, updated_at = ?, deleted_at = ? where workspace_id = ? and session_id = ?",
            ("deleted", "deleted", now, now, WORKSPACE_ID, SESSION_ID),
        )
        connection.commit()
    finally:
        connection.close()


def corrupt_checksum(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "update sqag_object_artifacts set checksum_sha256 = ? where workspace_id = ? and session_id = ?",
            ("0" * 64, WORKSPACE_ID, SESSION_ID),
        )
        connection.commit()
    finally:
        connection.close()


def run_verification(*, work_dir: Path | None = None) -> dict[str, Any]:
    parent = work_dir or (ROOT / "_tmp" / "object-artifact-lifecycle")
    run_root = parent / f"run-{time.time_ns()}"
    run_root.mkdir(parents=True, exist_ok=True)
    source = run_root / "source.sqlite3"
    backup = run_root / "backup.sqlite3"
    restored = run_root / "restored.sqlite3"
    restored_missing = run_root / "restored-missing.sqlite3"
    restored_checksum = run_root / "restored-checksum.sqlite3"
    restored_tombstone = run_root / "restored-tombstone.sqlite3"
    staging_root = run_root / "staging"

    seed_checks = seed_source_database(source, staging_root)
    before_digest = object_metadata_digest(source)
    before_journal_digest = journal_digest(source)
    backup_sqlite_database(source, backup)
    backup_sqlite_database(backup, restored)
    backup_sqlite_database(backup, restored_missing)
    backup_sqlite_database(backup, restored_checksum)
    backup_sqlite_database(backup, restored_tombstone)
    after_digest = object_metadata_digest(restored)
    after_journal_digest = journal_digest(restored)

    restored_content = restored_artifact(restored, seed_matching_backend(restored))
    missing_object = restored_artifact(restored_missing, InMemoryObjectStorageBackend())
    corrupt_checksum(restored_checksum)
    checksum_mismatch = restored_artifact(restored_checksum, seed_matching_backend(restored))
    mark_tombstoned(restored_tombstone)
    tombstoned = restored_artifact(restored_tombstone, seed_matching_backend(restored))
    wrong_workspace = restored_artifact(restored, seed_matching_backend(restored), workspace_id=OTHER_WORKSPACE_ID)

    checks = {
        "db_metadata_backup_restore_preserved": before_digest == after_digest and row_count(restored, "sqag_object_artifacts") == 1,
        "journal_backup_restore_preserved": before_journal_digest == after_journal_digest and row_count(restored, "sqag_object_artifact_operations") == 1,
        "restored_metadata_retrieves_object": bool(restored_content and restored_content.get("content") == ARTIFACT_CONTENT),
        "missing_object_detected": missing_object is None,
        "checksum_mismatch_detected": checksum_mismatch is None,
        "tombstoned_artifact_inaccessible_after_restore": tombstoned is None,
        "wrong_workspace_restore_access_denied": wrong_workspace is None,
        "local_staging_files_cleaned": bool(seed_checks["local_staging_files_cleaned"]),
    }
    status = "passed" if all(checks.values()) and seed_checks["stored_artifact_retrieved"] else "failed"
    return {
        "schema": "swooshz.sqag.object-artifact-lifecycle-verification.v1",
        "status": status,
        "storage_modes": ["sqlite-database", "stubbed-object-artifacts"],
        "synthetic_only": True,
        "checks": checks,
        "row_counts": {
            "object_artifacts": row_count(restored, "sqag_object_artifacts"),
            "object_artifact_operations": row_count(restored, "sqag_object_artifact_operations"),
            "quote_artifacts_blob_rows": row_count(restored, "sqag_quote_artifacts"),
        },
        "privacy": {
            "output": "metadata-only",
            "paths": "omitted",
            "database_urls": "omitted",
            "object_keys_printed": False,
            "artifact_bytes_printed": False,
            "payloads": "omitted",
        },
        "production_ready": False,
        "notes": [
            "This verifies only synthetic SQLite metadata and stubbed object-storage lifecycle behavior.",
            "It does not prove live object provider retention/delete, DB+object backup/restore, deployment operations, or production readiness.",
        ],
    }


def failed_report() -> dict[str, Any]:
    return {
        "schema": "swooshz.sqag.object-artifact-lifecycle-verification.v1",
        "status": "failed",
        "synthetic_only": True,
        "checks": {},
        "privacy": {
            "output": "metadata-only",
            "paths": "omitted",
            "database_urls": "omitted",
            "object_keys_printed": False,
            "artifact_bytes_printed": False,
            "payloads": "omitted",
        },
        "production_ready": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify synthetic DB+object artifact lifecycle evidence without private data.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Synthetic verifier workspace. The path is never printed.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_verification(work_dir=args.work_dir)
    except Exception:
        report = failed_report()
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
