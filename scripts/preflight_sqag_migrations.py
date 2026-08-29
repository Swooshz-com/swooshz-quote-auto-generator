#!/usr/bin/env python3
"""Read-only SQAG PostgreSQL migration and A25 admission preflight."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
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
        "ledgerState": "unknown",
        "expectedHead": None,
        "appliedHead": None,
        "appliedMigrationIds": None,
        "pendingMigrationIds": None,
        "blockers": [blocker],
    }, sort_keys=True))
    return 2


def validate_pre_apply_migration_report(
    report: Mapping[str, object],
    migrations=None,
) -> None:
    """Validate the exact safe migration-prefix report before any apply."""

    if migrations is None:
        migrations = migration_manifest(ROOT / "migrations")
    expected_ids = [migration.migration_id for migration in migrations]
    expected_keys = {
        "status",
        "safeToApply",
        "ledgerState",
        "expectedHead",
        "appliedHead",
        "appliedMigrationIds",
        "pendingMigrationIds",
        "blockers",
    }
    if not isinstance(report, Mapping) or set(report) != expected_keys:
        raise RuntimePrivilegeContractError("migration_report_shape_invalid")
    if report["status"] != "ready" or report["safeToApply"] is not True:
        raise RuntimePrivilegeContractError("migration_state_not_ready")
    if report["ledgerState"] not in {"missing", "present"}:
        raise RuntimePrivilegeContractError("migration_ledger_state_invalid")
    if report["expectedHead"] != (expected_ids[-1] if expected_ids else None):
        raise RuntimePrivilegeContractError("migration_expected_head_invalid")
    applied = report["appliedMigrationIds"]
    pending = report["pendingMigrationIds"]
    blockers = report["blockers"]
    if (
        not isinstance(applied, list)
        or not all(isinstance(item, str) for item in applied)
        or len(set(applied)) != len(applied)
        or not isinstance(pending, list)
        or not all(isinstance(item, str) for item in pending)
        or len(set(pending)) != len(pending)
        or blockers != []
    ):
        raise RuntimePrivilegeContractError("migration_report_values_invalid")
    if applied != expected_ids[: len(applied)] or pending != expected_ids[len(applied) :]:
        raise RuntimePrivilegeContractError("migration_prefix_invalid")
    if report["appliedHead"] != (applied[-1] if applied else None):
        raise RuntimePrivilegeContractError("migration_applied_head_invalid")
    if report["ledgerState"] == "missing" and applied:
        raise RuntimePrivilegeContractError("missing_ledger_has_applied_rows")


def _inspect_migration_ledger(
    database_url: str,
    migrations,
    *,
    require_final: bool = False,
):
    """Inspect the migrator-owned ledger through an exact migrator session."""
    with webapp.postgres_storage_connection(
        database_url,
        expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
    ) as connection:
        connection.execute("set transaction read only")
        try:
            report = inspect_postgres_migrations(connection, migrations)
            if report.get("safeToApply") is True:
                validate_pre_apply_migration_report(report, migrations)
                if require_final:
                    validate_migration_report(report)
            return report
        finally:
            connection.rollback()


def _inspect_privilege_projection(database_url: str, manifest, expected_role: str):
    """Inspect only one role's capability projection; never inspect the ledger."""
    with webapp.postgres_storage_connection(
        database_url,
        expected_role=expected_role,
    ) as connection:
        connection.execute("set transaction read only")
        try:
            return verify_postgres_privilege_contract(connection, manifest)
        finally:
            connection.rollback()


def _inspect(database_url: str, migrations, manifest, *, require_maintenance_role: bool = False):
    """Compatibility wrapper for a role projection without ledger access."""
    _ = migrations
    expected_role = (
        webapp.SQAG_MAINTENANCE_DATABASE_ROLE
        if require_maintenance_role
        else webapp.SQAG_RUNTIME_DATABASE_ROLE
    )
    return None, _inspect_privilege_projection(database_url, manifest, expected_role)


def _phase_from_argv(argv: list[str]) -> str | None:
    if len(argv) != 2 or argv[0] != "--phase" or argv[1] not in {
        "pre-apply",
        "post-apply",
    }:
        return None
    return argv[1]


def main() -> int:
    phase = _phase_from_argv(sys.argv[1:])
    if phase is None:
        return _failure("phase_argument_invalid")
    try:
        manifest = validate_manifest()
    except RuntimePrivilegeContractError:
        return _failure("runtime_contract_static_invalid")
    migrator_url = webapp.configured_migrator_database_url()
    if not migrator_url:
        return _failure("migrator_database_url_absent")
    if not webapp.postgres_database_url_is_supported(migrator_url):
        return _failure("migrator_database_url_requires_postgres")
    try:
        migrations = migration_manifest(ROOT / "migrations")
        migration_report = _inspect_migration_ledger(
            migrator_url,
            migrations,
            require_final=phase == "post-apply",
        )
        if migration_report.get("safeToApply") is not True:
            print(json.dumps(migration_report, sort_keys=True, default=str))
            return 2
        if phase == "pre-apply":
            print(json.dumps(migration_report, sort_keys=True, default=str))
            return 0
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
        runtime_report = _inspect_privilege_projection(
            database_url,
            manifest,
            webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        maintenance_contract_report = _inspect_privilege_projection(
            maintenance_url,
            manifest,
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
        )
    except Exception:
        return _failure("preflight_failed")
    print(json.dumps({
        **migration_report,
        "runtimeContract": runtime_report,
        "maintenanceContract": maintenance_contract_report,
    }, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
