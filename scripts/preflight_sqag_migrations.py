#!/usr/bin/env python3
"""Read-only SQAG PostgreSQL migration and privilege preflight."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp
from webapp.postgres_migrations import Migration, inspect_postgres_migrations, migration_manifest
from scripts.validate_runtime_privilege_contract import (
    RuntimePrivilegeContractError,
    validate_manifest,
    validate_migration_report,
    verify_postgres_privilege_contract,
)


_MIGRATION_PROJECTION_FIELDS = (
    "ledgerState",
    "expectedHead",
    "appliedHead",
    "appliedMigrationIds",
    "pendingMigrationIds",
)


def _failure(
    blocker: str,
    *,
    phase: str | None = None,
    migration_report: Mapping[str, Any] | None = None,
    runtime_contract: Mapping[str, Any] | None = None,
    maintenance_contract: Mapping[str, Any] | None = None,
) -> int:
    report = migration_report if isinstance(migration_report, Mapping) else {}
    result: dict[str, Any] = {
        "status": "unsafe",
        "safeToApply": False,
        "ledgerState": None if report.get("ledgerState") == "unknown" else report.get("ledgerState"),
        "expectedHead": report.get("expectedHead"),
        "appliedHead": report.get("appliedHead"),
        "appliedMigrationIds": report.get("appliedMigrationIds"),
        "pendingMigrationIds": report.get("pendingMigrationIds"),
        "blockers": [blocker],
        "phase": phase,
    }
    for field in _MIGRATION_PROJECTION_FIELDS:
        if field not in report:
            result[field] = None
    if phase == "post-apply":
        result["runtimeContract"] = runtime_contract
        result["maintenanceContract"] = maintenance_contract
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 2


def _parse_phase(argv: Sequence[str] | None) -> str | None:
    """Accept only the two-token operator grammar, without parser heuristics."""
    tokens = tuple(sys.argv[1:] if argv is None else argv)
    if tokens == ("--phase", "pre-apply"):
        return "pre-apply"
    if tokens == ("--phase", "post-apply"):
        return "post-apply"
    return None


def _validate_pre_apply_report(
    report: dict[str, Any],
    migrations: Sequence[Migration],
) -> None:
    expected_ids = [migration.migration_id for migration in migrations]
    if report.get("status") != "ready" or report.get("safeToApply") is not True:
        raise RuntimePrivilegeContractError("migration_state_not_pre_apply_safe")
    if report.get("blockers") != []:
        raise RuntimePrivilegeContractError("migration_state_not_pre_apply_safe")
    if report.get("expectedHead") != (expected_ids[-1] if expected_ids else None):
        raise RuntimePrivilegeContractError("migration_manifest_head_mismatch")
    if report.get("ledgerState") not in {"missing", "present"}:
        raise RuntimePrivilegeContractError("migration_ledger_state_invalid")

    applied = report.get("appliedMigrationIds")
    pending = report.get("pendingMigrationIds")
    if not isinstance(applied, list) or not isinstance(pending, list):
        raise RuntimePrivilegeContractError("migration_state_projection_invalid")
    if applied != expected_ids[:len(applied)] or pending != expected_ids[len(applied):]:
        raise RuntimePrivilegeContractError("migration_state_prefix_invalid")
    if report.get("appliedHead") != (applied[-1] if applied else None):
        raise RuntimePrivilegeContractError("migration_applied_head_mismatch")
    if report.get("ledgerState") == "missing" and applied:
        raise RuntimePrivilegeContractError("migration_missing_ledger_rows")


def _inspect_migration_ledger(
    database_url: str,
    migrations: Sequence[Migration],
    *,
    phase: str = "post-apply",
    validate: bool = True,
) -> dict[str, Any]:
    """Inspect the migrator-owned ledger through an exact migrator session."""
    with webapp.postgres_storage_connection(
        database_url,
        expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
    ) as connection:
        connection.execute("set transaction read only")
        try:
            report = inspect_postgres_migrations(connection, migrations)
            if validate:
                _validate_pre_apply_report(report, migrations)
                if phase == "post-apply":
                    validate_migration_report(report)
            return report
        finally:
            connection.rollback()


def _inspect_privilege_projection(
    database_url: str,
    manifest: dict[str, Any],
    expected_role: str,
) -> dict[str, Any]:
    """Inspect one role's capability projection without reading the ledger."""
    with webapp.postgres_storage_connection(
        database_url,
        expected_role=expected_role,
    ) as connection:
        connection.execute("set transaction read only")
        try:
            return verify_postgres_privilege_contract(connection, manifest)
        finally:
            connection.rollback()


def _inspect(
    database_url: str,
    migrations: Sequence[Migration],
    manifest: dict[str, Any],
    *,
    require_maintenance_role: bool = False,
):
    """Compatibility wrapper for a role projection without ledger access."""
    _ = migrations
    expected_role = (
        webapp.SQAG_MAINTENANCE_DATABASE_ROLE
        if require_maintenance_role
        else webapp.SQAG_RUNTIME_DATABASE_ROLE
    )
    return None, _inspect_privilege_projection(database_url, manifest, expected_role)


def main(argv: Sequence[str] | None = None) -> int:
    phase = _parse_phase(argv)
    if phase is None:
        return _failure("invalid_cli_grammar")

    migration_report: dict[str, Any] | None = None
    runtime_contract: dict[str, Any] | None = None
    maintenance_contract: dict[str, Any] | None = None
    try:
        try:
            manifest = validate_manifest()
        except RuntimePrivilegeContractError:
            return _failure("runtime_contract_static_invalid", phase=phase)
        except Exception:
            return _failure("runtime_contract_static_invalid", phase=phase)

        migrator_url = webapp.configured_migrator_database_url()
        if not migrator_url:
            return _failure("migrator_database_url_absent", phase=phase)
        if not webapp.postgres_database_url_is_supported(migrator_url):
            return _failure("migrator_database_url_requires_postgres", phase=phase)

        database_url = ""
        maintenance_url = ""
        if phase == "post-apply":
            database_url = webapp.configured_database_url()
            maintenance_url = webapp.configured_maintenance_database_url()
            if not database_url:
                return _failure("database_url_absent", phase=phase)
            if not webapp.postgres_database_url_is_supported(database_url):
                return _failure("postgres_database_required", phase=phase)
            if not maintenance_url:
                return _failure("maintenance_database_url_absent", phase=phase)
            if not webapp.postgres_database_url_is_supported(maintenance_url):
                return _failure("maintenance_database_url_requires_postgres", phase=phase)

        migrations = migration_manifest(ROOT / "migrations")
        migration_report = _inspect_migration_ledger(
            migrator_url,
            migrations,
            phase=phase,
            validate=False,
        )
        _validate_pre_apply_report(migration_report, migrations)
        if phase == "post-apply":
            validate_migration_report(migration_report)
            runtime_contract = _inspect_privilege_projection(
                database_url,
                manifest,
                webapp.SQAG_RUNTIME_DATABASE_ROLE,
            )
            maintenance_contract = _inspect_privilege_projection(
                maintenance_url,
                manifest,
                webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
            )
    except Exception:
        return _failure(
            "preflight_failed",
            phase=phase,
            migration_report=migration_report,
            runtime_contract=runtime_contract,
            maintenance_contract=maintenance_contract,
        )

    result: dict[str, Any] = dict(migration_report)
    result["phase"] = phase
    if phase == "post-apply":
        result["runtimeContract"] = runtime_contract
        result["maintenanceContract"] = maintenance_contract
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
