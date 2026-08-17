#!/usr/bin/env python3
"""Read-only SQAG PostgreSQL migration and A25 admission preflight."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp
from webapp.postgres_migrations import inspect_postgres_migrations, migration_manifest
from scripts.validate_runtime_privilege_contract import (
    RuntimePrivilegeContractError,
    validate_manifest,
    validate_migration_report,
    verify_postgres_privilege_contract,
)


def _failure(blocker: str) -> int:
    print(json.dumps({
        "status": "unsafe",
        "safeToApply": False,
        "expectedHead": None,
        "appliedHead": None,
        "pendingMigrationIds": [],
        "blockers": [blocker],
    }, sort_keys=True))
    return 2


def _inspect(database_url: str, migrations, manifest, *, require_maintenance_role: bool = False):
    with webapp.postgres_storage_connection(database_url) as connection:
        connection.execute("set transaction read only")
        try:
            if require_maintenance_role:
                row = connection.execute("select current_user as role").fetchone()
                if not row or str(row["role"] if isinstance(row, dict) else row[0]) != "sqag_maintenance":
                    raise RuntimePrivilegeContractError("maintenance_role_mismatch")
            report = inspect_postgres_migrations(connection, migrations)
            validate_migration_report(report)
            contract_report = verify_postgres_privilege_contract(connection, manifest)
            return report, contract_report
        finally:
            connection.rollback()


def main() -> int:
    try:
        manifest = validate_manifest()
    except RuntimePrivilegeContractError:
        return _failure("runtime_contract_static_invalid")
    database_url = webapp.configured_database_url()
    maintenance_url = webapp.configured_maintenance_database_url()
    if not database_url:
        return _failure("database_url_absent")
    if not webapp.postgres_database_url_is_supported(database_url):
        return _failure("postgres_database_required")
    if not maintenance_url:
        return _failure("maintenance_database_url_absent")
    if not webapp.postgres_database_url_is_supported(maintenance_url):
        return _failure("maintenance_database_url_requires_postgres")
    try:
        migrations = migration_manifest(ROOT / "migrations")
        migration_report, runtime_report = _inspect(database_url, migrations, manifest)
        _maintenance_report, maintenance_contract_report = _inspect(
            maintenance_url, migrations, manifest, require_maintenance_role=True
        )
    except Exception:
        return _failure("preflight_failed")
    print(json.dumps({
        "status": "ready",
        "safeToApply": True,
        "expectedHead": migration_report.get("expectedHead"),
        "appliedHead": migration_report.get("appliedHead"),
        "pendingMigrationIds": [],
        "blockers": [],
        "runtimeContract": runtime_report,
        "maintenanceContract": maintenance_contract_report,
    }, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
