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
from typing import Mapping
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


PRODUCTION_METADATA_MIGRATION_PATHS = (
    ROOT / "migrations" / "001_platform_scoped_storage.sql",
    ROOT / "migrations" / "003_object_artifact_metadata.sql",
)


def runtime_required_metadata_tables() -> dict[str, set[str]]:
    required: dict[str, set[str]] = {}
    for table_map in (
        webapp.KQAG_APP_METADATA_REQUIRED_COLUMNS,
        webapp.KQAG_OBJECT_ARTIFACT_METADATA_REQUIRED_COLUMNS,
    ):
        for table, columns in table_map.items():
            required.setdefault(table, set()).update(columns)
    return {table: set(columns) for table, columns in required.items()}


REQUIRED_METADATA_TABLES = runtime_required_metadata_tables()


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
        rf"\bcreate\s+table\s+if\s+not\s+exists\s+{re.escape(table.lower())}\s*\((?P<body>.*?)\)\s*;",
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
    with webapp.postgres_storage_connection(database_url) as connection:
        rows = connection.execute(
            f"select table_name, column_name from information_schema.columns where table_schema = current_schema() and table_name in ({placeholders})",
            tuple(sorted(tables)),
        ).fetchall()
    return schema_status_from_information_schema_rows(rows)


def _empty_live_metadata_operations(*, cleanup_completed: bool = False) -> dict[str, object]:
    return {
        "workspace_count": 0,
        "insert_count": 0,
        "read_count": 0,
        "update_count": 0,
        "delete_count": 0,
        "workspace_isolation": False,
        "crud_verified": False,
        "object_artifact_metadata_pairing": False,
        "cleanup_completed": cleanup_completed,
        "db_blob_artifact_rows_written": 0,
    }


def _synthetic_ids() -> dict[str, str]:
    token = uuid.uuid4().hex[:12]
    prefix = f"sqagldb-{token}"
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


def _contains_id(items: list[dict[str, object]], item_id: str, id_key: str = "id") -> bool:
    return any(_clean(item.get(id_key)) == item_id for item in items if isinstance(item, dict))


def _delete_synthetic_workspace_metadata(
    storage: object,
    *,
    profile_id: str,
    pricing_id: str,
    session_id: str,
) -> int:
    """Delete only verifier-created synthetic metadata rows through DB SQL."""
    deleted = 0
    with storage.connection() as connection:
        for sql, params in (
            (
                "delete from kqag_object_artifacts "
                "where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ?",
                (storage.workspace_id, "generated_quote", session_id, "xlsx"),
            ),
            (
                "delete from kqag_quote_sessions where workspace_id = ? and session_id = ?",
                (storage.workspace_id, session_id),
            ),
            (
                "delete from kqag_pricing_references where workspace_id = ? and reference_id = ?",
                (storage.workspace_id, pricing_id),
            ),
            (
                "delete from kqag_profiles where workspace_id = ? and profile_id = ?",
                (storage.workspace_id, profile_id),
            ),
        ):
            cursor = connection.execute(sql, params)
            deleted += int(getattr(cursor, "rowcount", 0) or 0)
        connection.commit()
    return deleted


def _synthetic_object_artifact_row(storage: object, session_id: str, artifact_kind: str) -> object | None:
    with storage.connection() as connection:
        cursor = connection.execute(
            "select artifact_id from kqag_object_artifacts "
            "where workspace_id = ? and owner_type = ? and owner_id = ? and artifact_kind = ? "
            "and status = ? and retention_status = ? and deleted_at is null",
            (storage.workspace_id, "generated_quote", session_id, artifact_kind, "active", "active"),
        )
        return cursor.fetchone()


def _cleanup_synthetic_metadata(database_url: str, ids: dict[str, str]) -> bool:
    cleanup_ok = True
    for workspace_key, profile_key, pricing_key, session_key in (
        ("workspace_a", "profile_a", "pricing_a", "session_a"),
        ("workspace_b", "profile_b", "pricing_b", "session_b"),
    ):
        storage = webapp.DatabaseKqagStorage(database_url, ids[workspace_key], role="admin", user_id=f"{ids[workspace_key]}-user")
        try:
            _delete_synthetic_workspace_metadata(
                storage,
                profile_id=ids[profile_key],
                pricing_id=ids[pricing_key],
                session_id=ids[session_key],
            )
        except Exception:
            cleanup_ok = False
    return cleanup_ok


def live_metadata_operations_status(database_url: str) -> dict[str, object]:
    ids = _synthetic_ids()
    operations = _empty_live_metadata_operations()
    inserted = 0
    reads = 0
    updates = 0
    deletes = 0
    try:
        storage_a = webapp.DatabaseKqagStorage(database_url, ids["workspace_a"], role="admin", user_id=f"{ids['workspace_a']}-user")
        storage_b = webapp.DatabaseKqagStorage(database_url, ids["workspace_b"], role="admin", user_id=f"{ids['workspace_b']}-user")
        storage_a.ensure_ready()
        storage_a.ensure_object_artifact_ready()
        storage_b.ensure_ready()
        storage_b.ensure_object_artifact_ready()

        storage_a.save_profile({"id": ids["profile_a"], "label": "SQAG live DB evidence profile A"})
        storage_b.save_profile({"id": ids["profile_b"], "label": "SQAG live DB evidence profile B"})
        inserted += 2
        storage_a.save_pricing_reference({"id": ids["pricing_a"], "label": "SQAG live DB evidence pricing A", "items": []})
        storage_b.save_pricing_reference({"id": ids["pricing_b"], "label": "SQAG live DB evidence pricing B", "items": []})
        inserted += 2
        storage_a.create_or_update_quote_session({"session_id": ids["session_a"], "customer_summary": {"name": "Synthetic DB Evidence A"}}, session_id=ids["session_a"])
        storage_b.create_or_update_quote_session({"session_id": ids["session_b"], "customer_summary": {"name": "Synthetic DB Evidence B"}}, session_id=ids["session_b"])
        inserted += 2

        checksum = "a" * 64
        for storage, workspace_key, session_key in (
            (storage_a, "workspace_a", "session_a"),
            (storage_b, "workspace_b", "session_b"),
        ):
            storage._upsert_object_quote_artifact(
                ids[session_key],
                "xlsx",
                "quotation.xlsx",
                webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                webapp.ObjectArtifactMetadata(
                    workspace_id=ids[workspace_key],
                    owner_type="generated_quote",
                    owner_id=ids[session_key],
                    artifact_kind="xlsx",
                    filename="quotation.xlsx",
                    content_type=webapp.QUOTE_SESSION_EXPORT_CONTENT_TYPES["xlsx"],
                    size_bytes=12,
                    checksum_sha256=checksum,
                    storage_key="redacted-object-key-ref",
                    created_at="2026-01-01T00:00:00Z",
                    updated_at="2026-01-01T00:00:00Z",
                ),
            )
            inserted += 1

        profiles_a = storage_a.list_company_profiles()
        profiles_b = storage_b.list_company_profiles()
        pricing_a = storage_a.list_pricing_references()
        pricing_b = storage_b.list_pricing_references()
        sessions_a = storage_a.list_quote_sessions()
        sessions_b = storage_b.list_quote_sessions()
        reads += 6
        workspace_isolation = all(
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

        storage_a.save_profile({"id": ids["profile_a"], "label": "SQAG live DB evidence profile A updated"})
        storage_a.save_pricing_reference({"id": ids["pricing_a"], "label": "SQAG live DB evidence pricing A updated", "items": []})
        storage_a.create_or_update_quote_session({"session_id": ids["session_a"], "customer_summary": {"name": "Synthetic DB Evidence A Updated"}}, session_id=ids["session_a"])
        updates += 3
        profile_updated = _clean((storage_a.profile_detail(ids["profile_a"]) or {}).get("label")) == "SQAG live DB evidence profile A updated"
        pricing_updated = _clean((storage_a.pricing_reference_detail(ids["pricing_a"]) or {}).get("label")) == "SQAG live DB evidence pricing A updated"
        session_updated = bool(storage_a.get_quote_session(ids["session_a"]))
        reads += 3

        object_a = _synthetic_object_artifact_row(storage_a, ids["session_a"], "xlsx")
        object_b = _synthetic_object_artifact_row(storage_b, ids["session_b"], "xlsx")
        object_cross_a = _synthetic_object_artifact_row(storage_a, ids["session_b"], "xlsx")
        object_cross_b = _synthetic_object_artifact_row(storage_b, ids["session_a"], "xlsx")
        reads += 4
        object_pairing = bool(object_a and object_b and not object_cross_a and not object_cross_b)

        deleted_count = _delete_synthetic_workspace_metadata(
            storage_a,
            profile_id=ids["profile_a"],
            pricing_id=ids["pricing_a"],
            session_id=ids["session_a"],
        )
        deletes += deleted_count
        deleted_rows_hidden = all(
            (
                storage_a.profile_detail(ids["profile_a"]) is None,
                storage_a.pricing_reference_detail(ids["pricing_a"]) is None,
                storage_a.get_quote_session(ids["session_a"]) is None,
                _synthetic_object_artifact_row(storage_a, ids["session_a"], "xlsx") is None,
            )
        )
        reads += 3
        crud_verified = all((profile_updated, pricing_updated, session_updated, deleted_count >= 3, deleted_rows_hidden))

        operations = {
            "workspace_count": 2,
            "insert_count": inserted,
            "read_count": reads,
            "update_count": updates,
            "delete_count": deletes,
            "workspace_isolation": workspace_isolation,
            "crud_verified": crud_verified,
            "object_artifact_metadata_pairing": object_pairing,
            "cleanup_completed": False,
            "db_blob_artifact_rows_written": 0,
        }
        return operations
    except Exception:
        operations["workspace_count"] = 2
        return operations
    finally:
        cleanup_completed = _cleanup_synthetic_metadata(database_url, ids)
        operations["cleanup_completed"] = cleanup_completed

def run_verification(
    *,
    env: Mapping[str, str] | None = None,
    migration_paths: tuple[Path, ...] = PRODUCTION_METADATA_MIGRATION_PATHS,
    driver_available: bool | None = None,
    schema_validator=None,
    live_operations_validator=None,
    test_injected_backend: bool = False,
) -> dict[str, object]:
    effective_env = env or os.environ
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
                    except Exception:
                        blockers.append("postgres_live_metadata_operations_failed")
                        live_operations_status = _empty_live_metadata_operations()
                    else:
                        if not live_operations_status.get("workspace_isolation"):
                            blockers.append("postgres_workspace_isolation_failed")
                        if not live_operations_status.get("crud_verified"):
                            blockers.append("postgres_metadata_crud_failed")
                        if not live_operations_status.get("object_artifact_metadata_pairing"):
                            blockers.append("postgres_object_artifact_metadata_failed")
                        if not live_operations_status.get("cleanup_completed"):
                            blockers.append("postgres_cleanup_failed")

    passed = family == "postgres_compatible" and live_evidence_enabled and not blockers

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
        "test_injected_backend": bool(test_injected_backend),
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
        "workspace_isolation_check": "validated" if live_operations_status.get("workspace_isolation") else ("runtime_adapter_available" if runtime_supported else "not_run_runtime_adapter_missing"),
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
