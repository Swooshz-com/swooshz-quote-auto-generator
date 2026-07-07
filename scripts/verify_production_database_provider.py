#!/usr/bin/env python3
"""Metadata-only SQAG production database readiness boundary.

This verifier intentionally does not connect to or mutate a live database while
the app runtime remains SQLite-only. It reports the Postgres/Neon production DB
gap without printing database URL values, hostnames, usernames, or passwords.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
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
REQUIRED_METADATA_TABLES = {
    "kqag_profiles": {"workspace_id", "profile_id", "payload_json"},
    "kqag_pricing_references": {"workspace_id", "reference_id", "payload_json"},
    "kqag_quote_sessions": {"workspace_id", "session_id", "metadata_json", "draft_files_json"},
    "kqag_object_artifacts": {
        "artifact_id",
        "workspace_id",
        "owner_type",
        "owner_id",
        "session_id",
        "artifact_kind",
        "filename",
        "content_type",
        "size_bytes",
        "checksum_sha256",
        "object_provider_type",
        "object_key_ref",
        "status",
        "retention_status",
    },
}


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
        table_present = bool(re.search(rf"\bcreate\s+table\s+if\s+not\s+exists\s+{re.escape(table)}\b", sql))
        missing_columns = sorted(column for column in required_columns if not re.search(rf"\b{re.escape(column)}\b", sql))
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


def postgres_schema_status(database_url: str) -> dict[str, object]:
    required = REQUIRED_METADATA_TABLES
    tables = set(required)
    placeholders = ", ".join("?" for _ in tables)
    with webapp.postgres_storage_connection(database_url) as connection:
        rows = connection.execute(
            f"select table_name, column_name from information_schema.columns where table_schema = current_schema() and table_name in ({placeholders})",
            tuple(sorted(tables)),
        ).fetchall()
    present: dict[str, set[str]] = {}
    for row in rows:
        table_name = _clean(row["table_name"])
        column_name = _clean(row["column_name"])
        if table_name and column_name:
            present.setdefault(table_name, set()).add(column_name)
    missing_tables = sorted(table for table in required if table not in present)
    missing_columns = {
        table: sorted(columns - present.get(table, set()))
        for table, columns in required.items()
        if columns - present.get(table, set())
    }
    return {
        "schema_available": not missing_tables and not missing_columns,
        "required_tables": {
            table: {"present": table in present, "missing_columns": missing_columns.get(table, [])}
            for table in sorted(required)
        },
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


def run_verification(
    *,
    env: Mapping[str, str] | None = None,
    migration_paths: tuple[Path, ...] = PRODUCTION_METADATA_MIGRATION_PATHS,
    driver_available: bool | None = None,
    schema_validator=None,
) -> dict[str, object]:
    effective_env = env or os.environ
    database_url = effective_env.get(webapp.KQAG_DATABASE_URL_ENV_NAME, "")
    family = database_family(database_url)
    migration_status = metadata_migration_status(migration_paths)
    postgres_driver = postgres_driver_available() if driver_available is None else bool(driver_available)
    live_evidence_enabled = _enabled(effective_env.get(webapp.SQAG_LIVE_DATABASE_EVIDENCE_ENV_NAME, ""))
    runtime_supported = webapp.postgres_metadata_storage_adapter_supported()
    connection_attempted = False
    runtime_schema_status: dict[str, object] | None = None

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

    passed = family == "postgres_compatible" and live_evidence_enabled and not blockers

    return {
        "schema": "swooshz.sqag.production-database-provider-verification.v1",
        "status": "passed" if passed else "failed",
        "database_family": family,
        "intended_production_family": "postgres_neon_compatible",
        "required_env_names": [
            webapp.KQAG_DATABASE_URL_ENV_NAME,
            webapp.SQAG_LIVE_DATABASE_EVIDENCE_ENV_NAME,
        ],
        "live_database_evidence_enabled": live_evidence_enabled,
        "postgres_driver_available": postgres_driver,
        "app_runtime_postgres_supported": runtime_supported,
        "connection_attempted": connection_attempted,
        "production_database_evidence_supported": passed,
        "metadata_migrations": migration_status,
        "runtime_schema": runtime_schema_status
        or {
            "schema_available": False,
            "required_tables": {},
            "missing_tables": [],
            "missing_columns": {},
        },
        "workspace_isolation_check": "runtime_adapter_available" if runtime_supported else "not_run_runtime_adapter_missing",
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
