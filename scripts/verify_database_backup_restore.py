#!/usr/bin/env python3
"""Synthetic KQAG SQLite backup/restore/rollback verifier.

The verifier creates only synthetic rows and emits only metadata. It is a
temporary internal-alpha evidence path for SQLite database + database-artifact
mode, not final production object-storage readiness.
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


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


RETENTION_POLICY_PATH = ROOT / "docs" / "internal-alpha-retention-policy.json"
KQAG_TABLES = (
    "kqag_profiles",
    "kqag_pricing_references",
    "kqag_quote_sessions",
    "kqag_quote_artifacts",
    "kqag_file_artifacts",
)
REQUIRED_RETENTION_CLASSES = {
    "quote_sessions",
    "generated_artifacts",
    "uploaded_references",
    "profile_layout_assets",
    "pricing_visual_assets",
    "logs",
    "backups",
}


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def table_row_count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"select count(*) from {table}").fetchone()[0])


def table_digest(connection: sqlite3.Connection, table: str) -> str:
    rows = connection.execute(f"select * from {table} order by 1, 2, 3").fetchall()
    digest = hashlib.sha256()
    for row in rows:
        for value in row:
            if isinstance(value, bytes):
                digest.update(sha256_hex(value).encode("ascii"))
            else:
                digest.update(str(value).encode("utf-8"))
            digest.update(b"\0")
        digest.update(b"\n")
    return digest.hexdigest()


def artifact_digest(connection: sqlite3.Connection, table: str) -> str:
    if table == "kqag_quote_artifacts":
        rows = connection.execute(
            "select workspace_id, session_id, artifact_kind, size_bytes, content_blob "
            "from kqag_quote_artifacts order by workspace_id, session_id, artifact_kind"
        ).fetchall()
    else:
        rows = connection.execute(
            "select workspace_id, owner_type, owner_id, artifact_kind, size_bytes, content_blob "
            "from kqag_file_artifacts order by workspace_id, owner_type, owner_id, artifact_kind"
        ).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        values = tuple(row)
        content = bytes(values[-1] or b"")
        for value in values[:-1]:
            digest.update(str(value).encode("utf-8"))
            digest.update(b"\0")
        digest.update(sha256_hex(content).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def snapshot_database(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        row_counts = {table: table_row_count(connection, table) for table in KQAG_TABLES}
        table_checksums = {table: table_digest(connection, table) for table in KQAG_TABLES}
        artifact_checksums = {
            "kqag_quote_artifacts": artifact_digest(connection, "kqag_quote_artifacts"),
            "kqag_file_artifacts": artifact_digest(connection, "kqag_file_artifacts"),
        }
        workspaces = [
            row["workspace_id"]
            for row in connection.execute(
                "select distinct workspace_id from kqag_quote_sessions order by workspace_id"
            ).fetchall()
        ]
        owners = [
            json.loads(row["metadata_json"]).get("owner", {}).get("user_id", "")
            for row in connection.execute(
                "select metadata_json from kqag_quote_sessions order by workspace_id, session_id"
            ).fetchall()
        ]
    finally:
        connection.close()
    return {
        "row_counts": row_counts,
        "table_checksums": table_checksums,
        "artifact_checksums": artifact_checksums,
        "workspace_count": len(workspaces),
        "owner_count": len([owner for owner in owners if owner]),
    }


def backup_sqlite_database(source: Path, backup: Path) -> None:
    source_connection = sqlite3.connect(source)
    backup_connection = sqlite3.connect(backup)
    try:
        source_connection.backup(backup_connection)
    finally:
        backup_connection.close()
        source_connection.close()


def seed_synthetic_database(database_path: Path) -> None:
    webapp.apply_kqag_storage_migrations(sqlite_url(database_path))
    now = "2026-07-03T00:00:00Z"
    quote_blob = b"synthetic-private-artifact-bytes"
    file_blob = b"synthetic-private-file-artifact-bytes"
    metadata = {
        "schema": "swooshz.kqag.quote-session.v1",
        "session_id": "quote-synthetic-alpha",
        "created_at": now,
        "updated_at": now,
        "owner": {"user_id": "synthetic-user-alpha"},
        "customer_summary": {"name": "Synthetic Private Customer"},
        "status": {"quote_generated": True},
        "exports": {"xlsx": {"filename": "quotation.xlsx", "created_at": now, "size_bytes": len(quote_blob), "stale": False}},
    }
    draft_files: list[dict[str, Any]] = []
    profile_payload = {"id": "profile-alpha", "label": "Synthetic profile", "defaults": {"project": "Private profile layout contents"}}
    pricing_payload = {"id": "pricing-alpha", "label": "Synthetic pricing", "items": [{"description": "Private pricing catalog contents"}]}
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "insert into kqag_profiles (workspace_id, profile_id, payload_json, created_at, updated_at) values (?, ?, ?, ?, ?)",
            ("workspace-alpha", "profile-alpha", json.dumps(profile_payload, sort_keys=True), now, now),
        )
        connection.execute(
            "insert into kqag_pricing_references (workspace_id, reference_id, payload_json, created_at, updated_at) values (?, ?, ?, ?, ?)",
            ("workspace-alpha", "pricing-alpha", json.dumps(pricing_payload, sort_keys=True), now, now),
        )
        connection.execute(
            "insert into kqag_quote_sessions (workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            ("workspace-alpha", "quote-synthetic-alpha", json.dumps(metadata, sort_keys=True), json.dumps(draft_files), now, now),
        )
        connection.execute(
            "insert into kqag_quote_artifacts (workspace_id, session_id, artifact_kind, filename, content_type, size_bytes, content_blob, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("workspace-alpha", "quote-synthetic-alpha", "xlsx", "quotation.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", len(quote_blob), sqlite3.Binary(quote_blob), now, now),
        )
        connection.execute(
            "insert into kqag_file_artifacts (workspace_id, owner_type, owner_id, artifact_kind, filename, content_type, size_bytes, content_blob, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("workspace-alpha", "profile", "profile-alpha", "quotation_layout", "quotation-layout.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", len(file_blob), sqlite3.Binary(file_blob), now, now),
        )
        connection.execute(
            "insert into kqag_quote_sessions (workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
            (
                "workspace-beta",
                "quote-synthetic-beta",
                json.dumps({**metadata, "session_id": "quote-synthetic-beta", "owner": {"user_id": "synthetic-user-beta"}}, sort_keys=True),
                json.dumps(draft_files),
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def mutate_database_after_known_good_backup(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("delete from kqag_quote_artifacts where workspace_id = ?", ("workspace-alpha",))
        connection.execute(
            "insert into kqag_profiles (workspace_id, profile_id, payload_json, created_at, updated_at) values (?, ?, ?, ?, ?)",
            ("workspace-alpha", "profile-mutated", json.dumps({"id": "profile-mutated"}, sort_keys=True), "2026-07-03T00:05:00Z", "2026-07-03T00:05:00Z"),
        )
        connection.commit()
    finally:
        connection.close()


def load_retention_policy(path: Path = RETENTION_POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    data_classes = policy.get("data_classes") if isinstance(policy.get("data_classes"), list) else []
    covered = {item.get("data_class") for item in data_classes if isinstance(item, dict)}
    missing = sorted(REQUIRED_RETENTION_CLASSES - covered)
    if policy.get("schema") != "swooshz.kqag.internal-alpha-retention-policy.v1" or missing:
        raise ValueError("Internal-alpha retention policy is incomplete.")
    if not policy.get("non_destructive_verifier_only"):
        raise ValueError("Internal-alpha retention policy must be non-destructive for this verifier.")
    return policy


def retention_summary(policy: dict[str, Any]) -> dict[str, Any]:
    data_classes = policy.get("data_classes") if isinstance(policy.get("data_classes"), list) else []
    return {
        "schema": policy.get("schema"),
        "non_destructive": bool(policy.get("non_destructive_verifier_only")),
        "covered_data_classes": sorted(
            item.get("data_class") for item in data_classes if isinstance(item, dict) and item.get("data_class")
        ),
    }


def run_verification(*, work_dir: Path | None = None) -> dict[str, Any]:
    parent = work_dir or (ROOT / "_tmp" / "database-backup-restore")
    run_root = parent / f"run-{time.time_ns()}"
    run_root.mkdir(parents=True, exist_ok=True)
    source = run_root / "source.sqlite3"
    backup = run_root / "backup.sqlite3"
    restored = run_root / "restored.sqlite3"
    rollback_source_backup = run_root / "rollback-known-good.sqlite3"
    rollback_restored = run_root / "rollback-restored.sqlite3"

    seed_synthetic_database(source)
    before = snapshot_database(source)
    backup_sqlite_database(source, backup)
    backup_sqlite_database(backup, restored)
    after = snapshot_database(restored)

    backup_sqlite_database(source, rollback_source_backup)
    known_good = snapshot_database(source)
    mutate_database_after_known_good_backup(source)
    mutated = snapshot_database(source)
    backup_sqlite_database(rollback_source_backup, rollback_restored)
    rolled_back = snapshot_database(rollback_restored)

    policy = load_retention_policy()
    row_counts_match = before["row_counts"] == after["row_counts"]
    table_checksums_match = before["table_checksums"] == after["table_checksums"]
    artifact_checksums_match = before["artifact_checksums"] == after["artifact_checksums"]
    ownership_preserved = after["workspace_count"] == 2 and after["owner_count"] == 2
    rollback_ok = known_good["table_checksums"] == rolled_back["table_checksums"] and mutated["table_checksums"] != rolled_back["table_checksums"]
    status = "passed" if all((row_counts_match, table_checksums_match, artifact_checksums_match, ownership_preserved, rollback_ok)) else "failed"

    return {
        "schema": "swooshz.kqag.database-backup-restore-verification.v1",
        "status": status,
        "storage_modes": ["sqlite-database", "sqlite-database-artifacts"],
        "synthetic_only": True,
        "backup_restore": {
            "row_counts_match": row_counts_match,
            "table_checksums_match": table_checksums_match,
            "artifact_checksums_match": artifact_checksums_match,
            "workspace_ownership_preserved": ownership_preserved,
            "tables_verified": list(KQAG_TABLES),
        },
        "rollback": {
            "restored_prior_known_good_state": rollback_ok,
            "mutation_detected_before_rollback": mutated["table_checksums"] != known_good["table_checksums"],
        },
        "retention_policy": retention_summary(policy),
        "privacy": {
            "output": "metadata-only",
            "paths": "omitted",
            "database_urls": "omitted",
            "artifact_bytes": "omitted",
            "payloads": "omitted",
        },
        "production_ready": False,
        "internal_alpha_ready": False,
        "notes": [
            "This verifies a synthetic SQLite database plus BLOB artifact backup/restore/rollback path only.",
            "It does not add object storage, hosted logging/monitoring, hosted smoke evidence, or production readiness.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify synthetic KQAG SQLite backup/restore/rollback evidence.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Synthetic drill workspace. The path is never printed.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_verification(work_dir=args.work_dir)
    except Exception:
        report = {
            "schema": "swooshz.kqag.database-backup-restore-verification.v1",
            "status": "failed",
            "synthetic_only": True,
            "privacy": {
                "output": "metadata-only",
                "paths": "omitted",
                "database_urls": "omitted",
                "artifact_bytes": "omitted",
                "payloads": "omitted",
            },
            "notes": [
                "Synthetic verification failed before producing evidence.",
                "Failure details are omitted to avoid printing private paths or payloads.",
            ],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
