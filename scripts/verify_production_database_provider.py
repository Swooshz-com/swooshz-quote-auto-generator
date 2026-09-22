#!/usr/bin/env python3
"""Metadata-only SQAG production database readiness boundary.

This verifier is metadata-only by default. Live Postgres-compatible schema and
synthetic metadata CRUD/isolation checks run only when SQAG_LIVE_DATABASE_EVIDENCE
is explicitly enabled, and reports must not print database URL values, hostnames,
usernames, passwords, provider values, object keys, artifact bytes, or tenant data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


PRODUCTION_METADATA_MIGRATION_PATHS = (
    ROOT / "migrations" / "006_quote_publication_versions_postgres.sql",
    ROOT / "migrations" / "001_platform_scoped_storage.sql",
    ROOT / "migrations" / "003_object_artifact_metadata.sql",
)


def runtime_required_metadata_tables() -> dict[str, set[str]]:
    required: dict[str, set[str]] = {}
    for table_map in (
        webapp.SQAG_APP_METADATA_REQUIRED_COLUMNS,
        webapp.SQAG_OBJECT_ARTIFACT_METADATA_REQUIRED_COLUMNS,
    ):
        for table, columns in table_map.items():
            required.setdefault(table, set()).update(columns)
    return {table: set(columns) for table, columns in required.items()}


REQUIRED_METADATA_TABLES = runtime_required_metadata_tables()

OBJECT_ARTIFACT_COLUMNS = (
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
OBJECT_ARTIFACT_IMMUTABLE_COLUMNS = (
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
    "created_at",
)
OBSERVATION_STATES = frozenset({"passed", "failed", "error", "not_observed"})


def _clean(value: object) -> str:
    return str(value or "").strip()


def _enabled(value: object) -> bool:
    return _clean(value).lower() in {"1", "true", "yes", "on"}


def database_family(raw_url: str) -> str:
    if not _clean(raw_url):
        return "missing"
    scheme = _clean(urlparse(raw_url).scheme).lower()
    if scheme in webapp.SQLITE_DATABASE_SCHEMES:
        return "sqlite"
    if scheme in webapp.POSTGRES_COMPATIBLE_DATABASE_SCHEMES:
        return "postgres_compatible"
    return "unsupported"


def postgres_driver_available() -> bool:
    return webapp.postgres_driver_available()


def _metadata_table_definition(sql: str, table: str) -> str | None:
    match = re.search(
        rf"\bcreate\s+table\s+if\s+not\s+exists\s+{re.escape(table.lower())}\s*\((?P<body>.*?)\)\s*(?:;|--\s*sqag_statement_boundary)",
        sql,
        flags=re.S,
    )
    if not match:
        return None
    return match.group("body")


def metadata_migration_status(paths: tuple[Path, ...] = PRODUCTION_METADATA_MIGRATION_PATHS) -> dict[str, object]:
    sql_parts: list[str] = []
    source_files: list[str] = []
    missing_files: list[str] = []
    for path in paths:
        try:
            sql_parts.append(path.read_text(encoding="utf-8"))
            source_files.append(path.name)
        except OSError:
            missing_files.append(path.name)

    sql = "\n".join(sql_parts).lower()
    table_status: dict[str, dict[str, object]] = {}
    for table, required_columns in REQUIRED_METADATA_TABLES.items():
        table_definition = _metadata_table_definition(sql, table)
        table_present = table_definition is not None
        missing_columns = sorted(
            column
            for column in required_columns
            if not table_definition or not re.search(rf"\b{re.escape(column)}\b", table_definition)
        )
        table_status[table] = {
            "present": table_present,
            "missing_columns": missing_columns,
        }

    missing_tables = sorted(table for table, status in table_status.items() if not status["present"])
    missing_columns = {
        table: status["missing_columns"]
        for table, status in table_status.items()
        if status["missing_columns"]
    }
    return {
        "source_files": sorted(source_files),
        "missing_source_files": sorted(missing_files),
        "required_tables": table_status,
        "metadata_tables_declared": not missing_files and not missing_tables and not missing_columns,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "db_blob_tables_required_for_production": False,
    }


def schema_status_from_information_schema_rows(
    rows: object,
    required: Mapping[str, set[str]] | None = None,
) -> dict[str, object]:
    required_tables = {table: set(columns) for table, columns in (required or REQUIRED_METADATA_TABLES).items()}
    present: dict[str, set[str]] = {}
    for row in rows:
        table_name = _clean(row["table_name"])
        column_name = _clean(row["column_name"])
        if table_name and column_name:
            present.setdefault(table_name, set()).add(column_name)
    missing_tables = sorted(table for table in required_tables if table not in present)
    missing_columns = {
        table: sorted(columns - present.get(table, set()))
        for table, columns in required_tables.items()
        if columns - present.get(table, set())
    }
    return {
        "schema_available": not missing_tables and not missing_columns,
        "required_tables": {
            table: {"present": table in present, "missing_columns": missing_columns.get(table, [])}
            for table in sorted(required_tables)
        },
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


def postgres_schema_status(database_url: str) -> dict[str, object]:
    tables = set(REQUIRED_METADATA_TABLES)
    placeholders = ", ".join("?" for _ in tables)
    with webapp.postgres_storage_connection(database_url, expected_role=webapp.SQAG_RUNTIME_DATABASE_ROLE) as connection:
        rows = connection.execute(
            f"select table_name, column_name from information_schema.columns where table_schema = current_schema() and table_name in ({placeholders})",
            tuple(sorted(tables)),
        ).fetchall()
    return schema_status_from_information_schema_rows(rows)


def _empty_live_metadata_operations(*, cleanup_completed: bool = False) -> dict[str, object]:
    cleanup_state = "passed" if cleanup_completed else "not_observed"
    operations: dict[str, object] = {
        "workspace_count": 0,
        "insert_count": 0,
        "read_count": 0,
        "update_count": 0,
        "delete_count": 0,
        "ordinary_delete_count": 0,
        "object_artifact_tombstone_count": 0,
        "workspace_isolation": False,
        "crud_verified": False,
        "object_artifact_metadata_pairing": False,
        "cleanup_completed": cleanup_completed,
        "db_blob_artifact_rows_written": 0,
        "observation_states": {
            "workspace_isolation": "not_observed",
            "metadata_crud": "not_observed",
            "object_artifact_metadata_pairing": "not_observed",
            "cleanup": cleanup_state,
        },
        "workspace_isolation_state": "not_observed",
        "metadata_crud_state": "not_observed",
        "object_artifact_metadata_pairing_state": "not_observed",
        "cleanup_state": cleanup_state,
        "operational_exception": False,
    }
    if cleanup_completed:
        _set_observation_state(operations, "cleanup", "passed")
    return operations


def _set_observation_state(
    operations: dict[str, object],
    key: str,
    state: str,
) -> None:
    if state not in OBSERVATION_STATES:
        state = "error"
    states = operations.setdefault("observation_states", {})
    if not isinstance(states, dict):
        states = {}
        operations["observation_states"] = states
    states[key] = state
    operations[f"{key}_state"] = state
    if key == "workspace_isolation":
        operations["workspace_isolation"] = state == "passed"
    elif key == "metadata_crud":
        operations["crud_verified"] = state == "passed"
    elif key == "object_artifact_metadata_pairing":
        operations["object_artifact_metadata_pairing"] = state == "passed"
    elif key == "cleanup":
        operations["cleanup_completed"] = state == "passed"


def _operation_observation_state(
    operations: Mapping[str, object],
    key: str,
) -> str:
    states = operations.get("observation_states")
    if isinstance(states, Mapping):
        state = _clean(states.get(key))
        if state in OBSERVATION_STATES:
            return state
    bool_key = {
        "workspace_isolation": "workspace_isolation",
        "metadata_crud": "crud_verified",
        "object_artifact_metadata_pairing": "object_artifact_metadata_pairing",
        "cleanup": "cleanup_completed",
    }[key]
    if bool_key in operations:
        return "passed" if bool(operations.get(bool_key)) else "failed"
    return "not_observed"


def _append_live_metadata_blockers(
    blockers: list[str],
    operations: Mapping[str, object],
) -> None:
    def add(blocker: str) -> None:
        if blocker not in blockers:
            blockers.append(blocker)

    feature_blockers = (
        (
            "workspace_isolation",
            "postgres_workspace_isolation_failed",
            "postgres_workspace_isolation_not_verified",
        ),
        (
            "metadata_crud",
            "postgres_metadata_crud_failed",
            "postgres_metadata_crud_not_verified",
        ),
        (
            "object_artifact_metadata_pairing",
            "postgres_object_artifact_metadata_failed",
            "postgres_object_artifact_metadata_not_verified",
        ),
    )
    for key, failed_blocker, not_verified_blocker in feature_blockers:
        state = _operation_observation_state(operations, key)
        if state == "failed":
            add(failed_blocker)
        elif state in {"error", "not_observed"}:
            add(not_verified_blocker)
    if _operation_observation_state(operations, "cleanup") != "passed":
        add("postgres_cleanup_failed")
    if operations.get("operational_exception"):
        add("postgres_live_metadata_operations_failed")


def _object_artifact_select_sql(suffix: str = "") -> str:
    return (
        "select "
        + ", ".join(OBJECT_ARTIFACT_COLUMNS)
        + " from sqag_object_artifacts "
        + suffix
    ).strip()


def _synthetic_object_artifact_spec(
    ids: Mapping[str, str],
    side: str,
) -> dict[str, object]:
    if side not in {"a", "b"}:
        raise ValueError("Synthetic workspace side is invalid.")
    prefix = ids["prefix"]
    workspace_id = ids[f"workspace_{side}"]
    session_id = ids[f"session_{side}"]
    return {
        "artifact_id": f"{prefix}-artifact-{side}",
        "workspace_id": workspace_id,
        "owner_type": "generated_quote",
        "owner_id": session_id,
        "platform_user_id": f"{workspace_id}-user",
        "session_id": session_id,
        "job_id": "",
        "artifact_kind": "xlsx",
        "filename": "quotation.xlsx",
        "content_type": webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
        "size_bytes": 12,
        "checksum_sha256": "a" * 64,
        "object_provider_type": "s3_compatible",
        "object_key_ref": f"synthetic/{ids['token']}/{side}/quotation.xlsx",
        "status": "active",
        "retention_status": "active",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "deleted_at": None,
    }


def _object_artifact_row_matches_expected(
    row: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    state: str | None = None,
) -> bool:
    try:
        if any(row[field] != expected[field] for field in OBJECT_ARTIFACT_IMMUTABLE_COLUMNS):
            return False
        if int(row["size_bytes"] or 0) != int(expected["size_bytes"] or 0):
            return False
        if state == "active":
            return (
                row["status"] == "active"
                and row["retention_status"] == "active"
                and row["updated_at"] == expected["updated_at"]
                and row["deleted_at"] is None
            )
        if state == "deleted":
            deleted_at = _clean(row["deleted_at"])
            return (
                row["status"] == "deleted"
                and row["retention_status"] == "deleted"
                and deleted_at
                and row["updated_at"] == row["deleted_at"]
            )
        return True
    except (KeyError, TypeError, ValueError):
        return False


def _synthetic_ids() -> dict[str, str]:
    token = uuid.uuid4().hex
    prefix = f"sqagldb-{token}"
    return {
        "token": token,
        "prefix": prefix,
        "workspace_a": f"{prefix}-workspace-a",
        "workspace_b": f"{prefix}-workspace-b",
        "profile_a": f"{prefix}-profile-a",
        "profile_b": f"{prefix}-profile-b",
        "pricing_a": f"{prefix}-pricing-a",
        "pricing_b": f"{prefix}-pricing-b",
        "session_a": f"quote-{token}a",
        "session_b": f"quote-{token}b",
    }


def _contains_id(items: list[dict[str, object]], item_id: str, id_key: str = "id") -> bool:
    return any(_clean(item.get(id_key)) == item_id for item in items if isinstance(item, dict))


def _insert_synthetic_metadata_rows(
    storage: object,
    ids: Mapping[str, str],
    side: str,
) -> int:
    now = webapp.utc_timestamp()
    profile_id = ids[f"profile_{side}"]
    pricing_id = ids[f"pricing_{side}"]
    session_id = ids[f"session_{side}"]
    workspace_id = ids[f"workspace_{side}"]
    statements = (
        (
            "insert into sqag_profiles (workspace_id, profile_id, payload_json, created_at, updated_at) "
            "values (?, ?, ?, ?, ?)",
            (
                workspace_id,
                profile_id,
                json.dumps({"id": profile_id, "label": f"Synthetic profile {side}"}, sort_keys=True),
                now,
                now,
            ),
        ),
        (
            "insert into sqag_pricing_references (workspace_id, reference_id, payload_json, created_at, updated_at) "
            "values (?, ?, ?, ?, ?)",
            (
                workspace_id,
                pricing_id,
                json.dumps(
                    {"id": pricing_id, "label": f"Synthetic pricing {side}", "items": []},
                    sort_keys=True,
                ),
                now,
                now,
            ),
        ),
        (
            "insert into sqag_quote_sessions (workspace_id, session_id, metadata_json, draft_files_json, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?)",
            (
                workspace_id,
                session_id,
                json.dumps(
                    {
                        "session_id": session_id,
                        "customer_summary": {"name": f"Synthetic customer {side}"},
                        "owner": {"user_id": f"{workspace_id}-user"},
                    },
                    sort_keys=True,
                ),
                "[]",
                now,
                now,
            ),
        ),
    )
    with storage.connection() as connection:
        for sql, params in statements:
            cursor = connection.execute(sql, params)
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise RuntimeError("Synthetic metadata insert was not singular.")
        connection.commit()
    return len(statements)


def _insert_synthetic_object_artifact(
    storage: object,
    expected: Mapping[str, object],
) -> bool:
    owner_params = (
        expected["workspace_id"],
        expected["owner_type"],
        expected["owner_id"],
        expected["artifact_kind"],
    )
    with storage.connection() as connection:
        existing_rows = connection.execute(
            _object_artifact_select_sql(
                "where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?"
            ),
            owner_params,
        ).fetchall()
        if existing_rows:
            return len(existing_rows) == 1 and (
                _object_artifact_row_matches_expected(existing_rows[0], expected, state="active")
                or _object_artifact_row_matches_expected(existing_rows[0], expected, state="deleted")
            )
        cursor = connection.execute(
            "insert into sqag_object_artifacts ("
            + ", ".join(OBJECT_ARTIFACT_COLUMNS)
            + ") values ("
            + ", ".join("?" for _ in OBJECT_ARTIFACT_COLUMNS)
            + ")",
            tuple(expected[field] for field in OBJECT_ARTIFACT_COLUMNS),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            connection.rollback()
            return False
        connection.commit()
    with storage.connection() as connection:
        row = connection.execute(
            _object_artifact_select_sql("where workspace_id = ? and artifact_id = ?"),
            (expected["workspace_id"], expected["artifact_id"]),
        ).fetchone()
    return bool(row) and _object_artifact_row_matches_expected(row, expected, state="active")


def _synthetic_object_artifact_row(
    storage: object,
    session_id: str,
    artifact_kind: str,
) -> object | None:
    with storage.connection() as connection:
        return connection.execute(
            _object_artifact_select_sql(
                "where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ? "
                "and status = ? and retention_status = ? and deleted_at is null"
            ),
            (
                storage.workspace_id,
                "generated_quote",
                session_id,
                artifact_kind,
                "active",
                "active",
            ),
        ).fetchone()


def _active_object_artifact_rows_for_workspace(storage: object) -> list[object]:
    with storage.connection() as connection:
        return connection.execute(
            _object_artifact_select_sql(
                "where workspace_id = ? and status = ? and retention_status = ? and deleted_at is null"
            ),
            (storage.workspace_id, "active", "active"),
        ).fetchall()


def _workspace_isolation_observed(
    storage_a: object,
    storage_b: object,
    ids: Mapping[str, str],
) -> bool:
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


def _update_synthetic_metadata(
    storage: object,
    ids: Mapping[str, str],
    side: str,
) -> tuple[bool, bool, bool]:
    now = webapp.utc_timestamp()
    profile_id = ids[f"profile_{side}"]
    pricing_id = ids[f"pricing_{side}"]
    session_id = ids[f"session_{side}"]
    workspace_id = ids[f"workspace_{side}"]
    session_probe = f"{ids['prefix']}-{side}-updated"
    statements = (
        (
            "update sqag_profiles set payload_json = ?, updated_at = ? "
            "where workspace_id = ? and profile_id = ?",
            (
                json.dumps({"id": profile_id, "label": f"Synthetic profile {side} updated"}, sort_keys=True),
                now,
                workspace_id,
                profile_id,
            ),
        ),
        (
            "update sqag_pricing_references set payload_json = ?, updated_at = ? "
            "where workspace_id = ? and reference_id = ?",
            (
                json.dumps({"id": pricing_id, "label": f"Synthetic pricing {side} updated", "items": []}, sort_keys=True),
                now,
                workspace_id,
                pricing_id,
            ),
        ),
        (
            "update sqag_quote_sessions set metadata_json = ?, updated_at = ? "
            "where workspace_id = ? and session_id = ?",
            (
                json.dumps(
                    {
                        "session_id": session_id,
                        "customer_summary": {"name": f"Synthetic customer {side} updated"},
                        "update_probe": session_probe,
                    },
                    sort_keys=True,
                ),
                now,
                workspace_id,
                session_id,
            ),
        ),
    )
    with storage.connection() as connection:
        for sql, params in statements:
            cursor = connection.execute(sql, params)
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                raise RuntimeError("Synthetic metadata update was not singular.")
        connection.commit()
    with storage.connection() as connection:
        profile_row = connection.execute(
            "select payload_json from sqag_profiles where workspace_id = ? and profile_id = ?",
            (workspace_id, profile_id),
        ).fetchone()
        pricing_row = connection.execute(
            "select payload_json from sqag_pricing_references where workspace_id = ? and reference_id = ?",
            (workspace_id, pricing_id),
        ).fetchone()
        session_row = connection.execute(
            "select metadata_json from sqag_quote_sessions where workspace_id = ? and session_id = ?",
            (workspace_id, session_id),
        ).fetchone()
    try:
        profile_payload = json.loads(profile_row["payload_json"]) if profile_row else {}
        pricing_payload = json.loads(pricing_row["payload_json"]) if pricing_row else {}
        session_payload = json.loads(session_row["metadata_json"]) if session_row else {}
    except (KeyError, TypeError, json.JSONDecodeError):
        return False, False, False
    return (
        _clean(profile_payload.get("label")) == f"Synthetic profile {side} updated",
        _clean(pricing_payload.get("label")) == f"Synthetic pricing {side} updated",
        _clean(session_payload.get("update_probe")) == session_probe,
    )


def _delete_one_synthetic_profile(
    storage: object,
    profile_id: str,
) -> bool:
    with storage.connection() as connection:
        cursor = connection.execute(
            "delete from sqag_profiles where workspace_id = ? and profile_id = ?",
            (storage.workspace_id, profile_id),
        )
        if int(getattr(cursor, "rowcount", 0) or 0) != 1:
            connection.rollback()
            return False
        connection.commit()
    with storage.connection() as connection:
        return (
            connection.execute(
                "select 1 from sqag_profiles where workspace_id = ? and profile_id = ?",
                (storage.workspace_id, profile_id),
            ).fetchone()
            is None
        )


def _delete_synthetic_ordinary_rows(
    storage: object,
    ids: Mapping[str, str],
    side: str,
) -> int:
    workspace_id = ids[f"workspace_{side}"]
    statements = (
        (
            "delete from sqag_profiles where workspace_id = ? and profile_id = ?",
            (workspace_id, ids[f"profile_{side}"]),
        ),
        (
            "delete from sqag_pricing_references where workspace_id = ? and reference_id = ?",
            (workspace_id, ids[f"pricing_{side}"]),
        ),
        (
            "delete from sqag_quote_sessions where workspace_id = ? and session_id = ?",
            (workspace_id, ids[f"session_{side}"]),
        ),
    )
    deleted = 0
    with storage.connection() as connection:
        for sql, params in statements:
            cursor = connection.execute(sql, params)
            rowcount = int(getattr(cursor, "rowcount", 0) or 0)
            if rowcount not in {0, 1}:
                raise RuntimeError("Synthetic ordinary cleanup was not singular.")
            deleted += rowcount
        connection.commit()
    absence_queries = (
        (
            "select 1 from sqag_profiles where workspace_id = ? and profile_id = ?",
            (workspace_id, ids[f"profile_{side}"]),
        ),
        (
            "select 1 from sqag_pricing_references where workspace_id = ? and reference_id = ?",
            (workspace_id, ids[f"pricing_{side}"]),
        ),
        (
            "select 1 from sqag_quote_sessions where workspace_id = ? and session_id = ?",
            (workspace_id, ids[f"session_{side}"]),
        ),
    )
    with storage.connection() as connection:
        if any(connection.execute(sql, params).fetchone() is not None for sql, params in absence_queries):
            raise RuntimeError("Synthetic ordinary cleanup did not verify absence.")
    return deleted


def _tombstone_synthetic_object_artifact(
    storage: object,
    expected: Mapping[str, object],
) -> tuple[str, int]:
    active_connection: Any | None = None
    try:
        owner_params = (
            expected["workspace_id"],
            expected["owner_type"],
            expected["owner_id"],
            expected["artifact_kind"],
        )
        with storage.connection() as connection:
            active_connection = connection
            rows = connection.execute(
                _object_artifact_select_sql(
                    "where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?"
                ),
                owner_params,
            ).fetchall()
            if not rows:
                artifact_rows = connection.execute(
                    _object_artifact_select_sql("where artifact_id = ?"),
                    (expected["artifact_id"],),
                ).fetchall()
                if not artifact_rows:
                    return "passed", 0
                return "failed", 0
            if len(rows) != 1:
                return "failed", 0
            current = rows[0]
            if _object_artifact_row_matches_expected(current, expected, state="deleted"):
                return "passed", 0
            if not _object_artifact_row_matches_expected(current, expected, state="active"):
                return "failed", 0
            storage._execute_mark_object_artifact_deleted(
                connection,
                str(expected["artifact_id"]),
                current,
            )
            try:
                connection.commit()
            except Exception:
                try:
                    connection.rollback()
                except Exception:
                    pass
                return "error", 0
        with storage.connection() as connection:
            terminal = connection.execute(
                _object_artifact_select_sql("where workspace_id = ? and artifact_id = ?"),
                (expected["workspace_id"], expected["artifact_id"]),
            ).fetchone()
        if not terminal or not _object_artifact_row_matches_expected(
            terminal,
            expected,
            state="deleted",
        ):
            return "failed", 0
        return "passed", 1
    except Exception:
        if active_connection is not None:
            try:
                active_connection.rollback()
            except Exception:
                pass
        return "error", 0


def _cleanup_synthetic_metadata(
    database_url: str,
    ids: Mapping[str, str],
    *,
    artifact_specs: list[tuple[str, Mapping[str, object]]] | None = None,
    storages: Mapping[str, object] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "state": "passed",
        "ordinary_delete_count": 0,
        "object_artifact_tombstone_count": 0,
    }
    active_storages: dict[str, object] = dict(storages or {})
    specs = artifact_specs
    if specs is None:
        specs = [
            ("a", _synthetic_object_artifact_spec(ids, "a")),
            ("b", _synthetic_object_artifact_spec(ids, "b")),
        ]
    deterministic_failure = False
    operational_error = False

    for side in ("a", "b"):
        try:
            storage = active_storages.get(side)
            if storage is None:
                workspace_id = ids[f"workspace_{side}"]
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    workspace_id,
                    role="admin",
                    user_id=f"{workspace_id}-user",
                    expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
                )
                active_storages[side] = storage
            result["ordinary_delete_count"] = int(result["ordinary_delete_count"]) + _delete_synthetic_ordinary_rows(
                storage,
                ids,
                side,
            )
        except Exception:
            operational_error = True

    for side, expected in specs:
        try:
            storage = active_storages.get(side)
            if storage is None:
                workspace_id = ids[f"workspace_{side}"]
                storage = webapp.DatabaseSqagStorage(
                    database_url,
                    workspace_id,
                    role="admin",
                    user_id=f"{workspace_id}-user",
                    expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
                )
                active_storages[side] = storage
            state, tombstones = _tombstone_synthetic_object_artifact(storage, expected)
            result["object_artifact_tombstone_count"] = int(result["object_artifact_tombstone_count"]) + tombstones
            if state == "error":
                operational_error = True
            elif state != "passed":
                deterministic_failure = True
        except Exception:
            operational_error = True

    if operational_error:
        result["state"] = "error"
    elif deterministic_failure:
        result["state"] = "failed"
    return result


def live_metadata_operations_status(database_url: str) -> dict[str, object]:
    ids = _synthetic_ids()
    operations = _empty_live_metadata_operations()
    operations["workspace_count"] = 2
    storages: dict[str, object] = {}
    created_artifact_specs: list[tuple[str, Mapping[str, object]]] = []
    initialized = False
    try:
        for side in ("a", "b"):
            workspace_id = ids[f"workspace_{side}"]
            storage = webapp.DatabaseSqagStorage(
                database_url,
                workspace_id,
                role="admin",
                user_id=f"{workspace_id}-user",
                expected_session_role=webapp.SQAG_RUNTIME_DATABASE_ROLE,
            )
            storages[side] = storage
            storage.ensure_ready()
            storage.ensure_object_artifact_ready()
        for side in ("a", "b"):
            operations["insert_count"] = int(operations["insert_count"]) + _insert_synthetic_metadata_rows(
                storages[side],
                ids,
                side,
            )
        for side in ("a", "b"):
            expected = _synthetic_object_artifact_spec(ids, side)
            # The insert commits before its read-back. Track the candidate before
            # attempting that read so an uncertain insert can be reconciled by
            # strict fingerprint during cleanup.
            created_artifact_specs.append((side, expected))
            if not _insert_synthetic_object_artifact(storages[side], expected):
                raise RuntimeError("Synthetic object metadata ownership collision.")
            operations["insert_count"] = int(operations["insert_count"]) + 1
        initialized = True
    except Exception:
        operations["operational_exception"] = True

    if initialized:
        try:
            operations["read_count"] = int(operations["read_count"]) + 6
            _set_observation_state(
                operations,
                "workspace_isolation",
                "passed"
                if _workspace_isolation_observed(storages["a"], storages["b"], ids)
                else "failed",
            )
        except Exception:
            operations["operational_exception"] = True
            _set_observation_state(operations, "workspace_isolation", "error")

        try:
            profile_updated, pricing_updated, session_updated = _update_synthetic_metadata(
                storages["a"],
                ids,
                "a",
            )
            operations["update_count"] = 3
            profile_deleted = _delete_one_synthetic_profile(
                storages["a"],
                ids["profile_a"],
            )
            operations["delete_count"] = 1 if profile_deleted else 0
            operations["ordinary_delete_count"] = operations["delete_count"]
            operations["read_count"] = int(operations["read_count"]) + 3
            _set_observation_state(
                operations,
                "metadata_crud",
                "passed"
                if all((profile_updated, pricing_updated, session_updated, profile_deleted))
                else "failed",
            )
        except Exception:
            operations["operational_exception"] = True
            _set_observation_state(operations, "metadata_crud", "error")

        try:
            rows_a = _active_object_artifact_rows_for_workspace(storages["a"])
            rows_b = _active_object_artifact_rows_for_workspace(storages["b"])
            expected_a = _synthetic_object_artifact_spec(ids, "a")
            expected_b = _synthetic_object_artifact_spec(ids, "b")
            pairing_ok = (
                len(rows_a) == 1
                and len(rows_b) == 1
                and _object_artifact_row_matches_expected(rows_a[0], expected_a, state="active")
                and _object_artifact_row_matches_expected(rows_b[0], expected_b, state="active")
            )
            operations["read_count"] = int(operations["read_count"]) + 2
            _set_observation_state(
                operations,
                "object_artifact_metadata_pairing",
                "passed" if pairing_ok else "failed",
            )
        except Exception:
            operations["operational_exception"] = True
            _set_observation_state(
                operations,
                "object_artifact_metadata_pairing",
                "error",
            )

    try:
        cleanup = _cleanup_synthetic_metadata(
            database_url,
            ids,
            artifact_specs=created_artifact_specs,
            storages=storages,
        )
    except Exception:
        cleanup = {
            "state": "error",
            "ordinary_delete_count": 0,
            "object_artifact_tombstone_count": 0,
        }
    operations["delete_count"] = int(operations["delete_count"]) + int(
        cleanup["ordinary_delete_count"]
    )
    operations["ordinary_delete_count"] = operations["delete_count"]
    operations["object_artifact_tombstone_count"] = int(
        cleanup["object_artifact_tombstone_count"]
    )
    cleanup_state = _clean(cleanup.get("state")) or "error"
    _set_observation_state(operations, "cleanup", cleanup_state)
    if cleanup_state == "error":
        operations["operational_exception"] = True
    return operations

def run_verification(
    *,
    env: Mapping[str, str] | None = None,
    migration_paths: tuple[Path, ...] = PRODUCTION_METADATA_MIGRATION_PATHS,
    driver_available: bool | None = None,
    schema_validator=None,
    live_operations_validator=None,
    test_injected_backend: bool = False,
) -> dict[str, object]:
    effective_env = os.environ if env is None else env
    database_url = effective_env.get(webapp.SQAG_DATABASE_URL_ENV_NAME, "")
    family = database_family(database_url)
    migration_status = metadata_migration_status(migration_paths)
    postgres_driver = postgres_driver_available() if driver_available is None else bool(driver_available)
    live_evidence_enabled = _enabled(effective_env.get(webapp.SQAG_LIVE_DATABASE_EVIDENCE_ENV_NAME, ""))
    runtime_supported = webapp.postgres_metadata_storage_adapter_supported()
    connection_attempted = False
    runtime_schema_status: dict[str, object] | None = None
    live_operations_status = _empty_live_metadata_operations()

    blockers: list[str] = []
    if family == "missing":
        blockers.append("database_url_missing")
    elif family == "sqlite":
        blockers.append("sqlite_not_final_production")
    elif family == "unsupported":
        blockers.append("database_url_scheme_unsupported")
    elif family == "postgres_compatible":
        if not live_evidence_enabled:
            blockers.append("live_database_evidence_not_enabled")
        if not migration_status["metadata_tables_declared"]:
            blockers.append("postgres_metadata_migrations_missing")
        if not postgres_driver:
            blockers.append("postgres_driver_unavailable")
        if not runtime_supported:
            blockers.append("postgres_runtime_adapter_missing")
        if live_evidence_enabled and migration_status["metadata_tables_declared"] and postgres_driver and runtime_supported:
            connection_attempted = True
            try:
                validator = schema_validator or postgres_schema_status
                runtime_schema_status = validator(database_url)
            except Exception:
                blockers.append("postgres_connection_failed")
                runtime_schema_status = {
                    "schema_available": False,
                    "required_tables": {},
                    "missing_tables": [],
                    "missing_columns": {},
                }
            else:
                if not runtime_schema_status.get("schema_available"):
                    blockers.append("postgres_schema_missing")
                else:
                    try:
                        operation_validator = live_operations_validator or live_metadata_operations_status
                        live_operations_status = operation_validator(database_url)
                        if not isinstance(live_operations_status, Mapping):
                            raise TypeError("Live metadata operation result is invalid.")
                    except Exception:
                        blockers.append("postgres_live_metadata_operations_failed")
                        live_operations_status = _empty_live_metadata_operations()
                        live_operations_status["operational_exception"] = True
                    else:
                        if (
                            test_injected_backend
                            or bool(live_operations_status.get("test_injected_backend"))
                        ):
                            blockers.append("test_injected_backend_not_live_evidence")
                    _append_live_metadata_blockers(blockers, live_operations_status)

    live_operations_complete = all(
        _operation_observation_state(live_operations_status, key) == "passed"
        for key in (
            "workspace_isolation",
            "metadata_crud",
            "object_artifact_metadata_pairing",
            "cleanup",
        )
    )
    passed = (
        family == "postgres_compatible"
        and live_evidence_enabled
        and live_operations_complete
        and not blockers
    )
    workspace_state = _operation_observation_state(
        live_operations_status,
        "workspace_isolation",
    )
    workspace_check = {
        "passed": "validated",
        "failed": "failed",
        "error": "error",
        "not_observed": "not_verified",
    }[workspace_state]
    if workspace_state == "not_observed" and not runtime_supported:
        workspace_check = "not_run_runtime_adapter_missing"

    return {
        "schema": "swooshz.sqag.production-database-provider-verification.v1",
        "status": "passed" if passed else "failed",
        "database_family": family,
        "intended_production_family": "postgres_neon_compatible",
        "required_env_names": [
            webapp.SQAG_DATABASE_URL_ENV_NAME,
            webapp.SQAG_LIVE_DATABASE_EVIDENCE_ENV_NAME,
        ],
        "live_database_evidence_enabled": live_evidence_enabled,
        "postgres_driver_available": postgres_driver,
        "app_runtime_postgres_supported": runtime_supported,
        "connection_attempted": connection_attempted,
        "test_injected_backend": bool(
            test_injected_backend or live_operations_status.get("test_injected_backend")
        ),
        "live_database_evidence_supported": passed,
        "production_database_evidence_supported": passed,
        "metadata_migrations": migration_status,
        "runtime_schema": runtime_schema_status
        or {
            "schema_available": False,
            "required_tables": {},
            "missing_tables": [],
            "missing_columns": {},
        },
        "workspace_isolation_check": workspace_check,
        "live_metadata_operations": live_operations_status,
        "object_artifact_metadata_check": "validated" if passed else ("declared" if migration_status["metadata_tables_declared"] else "missing"),
        "db_object_pairing": {
            "database_stores": "rows_and_metadata_only",
            "generated_artifact_bytes": "object_storage_only",
            "object_storage_live_provider_evidence_required_separately": True,
        },
        "privacy": {
            "database_urls": "omitted",
            "hostnames": "omitted",
            "usernames": "omitted",
            "passwords": "omitted",
            "tenant_data": "omitted",
            "artifact_bytes": "omitted",
        },
        "blockers": blockers,
        "notes": [
            "This verifier fails closed unless SQAG live database evidence is explicitly enabled by the operator.",
            "No DB URL value, hostname, username, password, tenant data, object key, or artifact bytes are printed.",
            "SQLite remains local-UAT/synthetic evidence only.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Report SQAG production database readiness without printing private database values."
    )


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    report = run_verification()
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
