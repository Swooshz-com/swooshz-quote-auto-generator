#!/usr/bin/env python3
"""Read-only SQAG PostgreSQL migration admission preflight."""

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


PHASES = frozenset({"pre-apply", "post-apply"})
REPORT_LIST_FIELDS = frozenset({"appliedMigrationIds", "pendingMigrationIds", "blockers"})
REPORT_FIELDS = (
    "status",
    "safeToApply",
    "ledgerState",
    "expectedHead",
    "appliedHead",
    "appliedMigrationIds",
    "pendingMigrationIds",
    "blockers",
)


def _report_fields(report: Mapping[str, object] | None) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key in REPORT_FIELDS:
        value = report.get(key) if isinstance(report, Mapping) else None
        if key in REPORT_LIST_FIELDS and value is not None:
            value = list(value) if isinstance(value, (list, tuple)) else value
        fields[key] = value
    return fields


def _failure(
    blocker: str,
    *,
    phase: str | None = None,
    report: Mapping[str, object] | None = None,
) -> int:
    payload = _report_fields(report)
    payload.update(
        {
            "phase": phase,
            "status": "unsafe",
            "safeToApply": False,
        }
    )
    blockers = payload.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    if blocker not in blockers:
        blockers.append(blocker)
    payload["blockers"] = blockers
    print(json.dumps(payload, sort_keys=True, default=str))
    return 2


def _success(
    phase: str,
    migration_report: Mapping[str, object],
    *,
    runtime_report: Mapping[str, object] | None = None,
    maintenance_report: Mapping[str, object] | None = None,
) -> int:
    payload = _report_fields(migration_report)
    payload["phase"] = phase
    if phase == "post-apply":
        payload["runtimeContract"] = runtime_report
        payload["maintenanceContract"] = maintenance_report
    print(json.dumps(payload, sort_keys=True, default=str))
    return 0


def _parse_phase(argv: list[str] | None = None) -> str | None:
    """Parse the exact phase-only CLI before any source or database access."""

    args = list(sys.argv[1:] if argv is None else argv)
    phase_positions = [index for index, argument in enumerate(args) if argument == "--phase"]
    if not phase_positions:
        _failure("phase_required")
        return None
    if len(phase_positions) > 1:
        _failure("phase_duplicate")
        return None
    phase_position = phase_positions[0]
    if phase_position != 0:
        _failure("unknown_argument")
        return None
    if len(args) <= phase_position + 1:
        _failure("phase_required")
        return None
    phase = args[phase_position + 1]
    if phase not in PHASES:
        _failure("phase_invalid")
        return None
    if len(args) != 2:
        _failure("unknown_argument")
        return None
    return phase


def validate_pre_apply_migration_report(report: Mapping[str, object], migrations) -> None:
    """Require a safe report whose ledger is an exact canonical prefix."""

    expected_ids = [migration.migration_id for migration in migrations]
    applied_ids = report.get("appliedMigrationIds")
    pending_ids = report.get("pendingMigrationIds")
    if (
        report.get("status") != "ready"
        or report.get("safeToApply") is not True
        or report.get("blockers")
        or not isinstance(applied_ids, list)
        or not isinstance(pending_ids, list)
    ):
        raise RuntimePrivilegeContractError("migration_pre_apply_state_not_safe")
    if applied_ids != expected_ids[: len(applied_ids)]:
        raise RuntimePrivilegeContractError("migration_ledger_not_canonical_prefix")
    if pending_ids != expected_ids[len(applied_ids) :]:
        raise RuntimePrivilegeContractError("migration_pending_set_not_canonical_suffix")
    if report.get("expectedHead") != (expected_ids[-1] if expected_ids else None):
        raise RuntimePrivilegeContractError("migration_expected_head_mismatch")
    if report.get("appliedHead") != (applied_ids[-1] if applied_ids else None):
        raise RuntimePrivilegeContractError("migration_applied_head_mismatch")
    ledger_state = report.get("ledgerState")
    if ledger_state not in {"missing", "present"}:
        raise RuntimePrivilegeContractError("migration_ledger_state_unknown")
    if applied_ids and ledger_state != "present":
        raise RuntimePrivilegeContractError("migration_applied_prefix_without_ledger")


def _read_migration_ledger(database_url: str, migrations):
    """Read the ledger through a true migrator session and a read-only transaction."""

    with webapp.postgres_storage_connection(
        database_url,
        expected_role=webapp.SQAG_MIGRATOR_DATABASE_ROLE,
    ) as connection:
        connection.execute("set transaction read only")
        try:
            return inspect_postgres_migrations(connection, migrations)
        finally:
            connection.rollback()


def _inspect_migration_ledger(database_url: str, migrations):
    """Inspect the final ledger state through an exact migrator session."""

    report = _read_migration_ledger(database_url, migrations)
    validate_migration_report(report)
    return report


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


def _storage_blocker(exc: BaseException, fallback: str) -> str:
    reason = getattr(exc, "reason", None)
    return reason if isinstance(reason, str) and reason else fallback


def main(argv: list[str] | None = None) -> int:
    phase = _parse_phase(argv)
    if phase is None:
        return 2
    try:
        manifest = validate_manifest()
    except RuntimePrivilegeContractError:
        return _failure("runtime_contract_static_invalid", phase=phase)
    try:
        migrations = migration_manifest(ROOT / "migrations")
    except Exception:
        return _failure("migration_manifest_invalid", phase=phase)

    if phase == "pre-apply":
        migrator_url = webapp.configured_migrator_database_url()
        if not migrator_url:
            return _failure("migrator_database_url_absent", phase=phase)
        if not webapp.postgres_database_url_is_supported(migrator_url):
            return _failure("migrator_database_url_requires_postgres", phase=phase)
        migration_report = None
        try:
            migration_report = _read_migration_ledger(migrator_url, migrations)
            validate_pre_apply_migration_report(migration_report, migrations)
        except RuntimePrivilegeContractError:
            return _failure(
                "pre_apply_migration_state_invalid",
                phase=phase,
                report=migration_report,
            )
        except webapp.SqagStorageAccessError as exc:
            return _failure(
                _storage_blocker(exc, "pre_apply_storage_access_failed"),
                phase=phase,
                report=migration_report,
            )
        except Exception:
            return _failure("pre_apply_failed", phase=phase, report=migration_report)
        return _success(phase, migration_report)

    database_url = webapp.configured_database_url()
    maintenance_url = webapp.configured_maintenance_database_url()
    migrator_url = webapp.configured_migrator_database_url()
    if not database_url:
        return _failure("database_url_absent", phase=phase)
    if not webapp.postgres_database_url_is_supported(database_url):
        return _failure("postgres_database_required", phase=phase)
    if not maintenance_url:
        return _failure("maintenance_database_url_absent", phase=phase)
    if not webapp.postgres_database_url_is_supported(maintenance_url):
        return _failure("maintenance_database_url_requires_postgres", phase=phase)
    if not migrator_url:
        return _failure("migrator_database_url_absent", phase=phase)
    if not webapp.postgres_database_url_is_supported(migrator_url):
        return _failure("migrator_database_url_requires_postgres", phase=phase)

    migration_report = None
    try:
        migration_report = _read_migration_ledger(migrator_url, migrations)
        validate_pre_apply_migration_report(migration_report, migrations)
        validate_migration_report(migration_report)
        runtime_report = _inspect_privilege_projection(
            database_url,
            manifest,
            webapp.SQAG_RUNTIME_DATABASE_ROLE,
        )
        maintenance_report = _inspect_privilege_projection(
            maintenance_url,
            manifest,
            webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
        )
    except RuntimePrivilegeContractError:
        return _failure(
            "post_apply_contract_invalid",
            phase=phase,
            report=migration_report,
        )
    except webapp.SqagStorageAccessError as exc:
        return _failure(
            _storage_blocker(exc, "post_apply_storage_access_failed"),
            phase=phase,
            report=migration_report,
        )
    except Exception:
        return _failure("post_apply_failed", phase=phase, report=migration_report)
    return _success(
        phase,
        migration_report,
        runtime_report=runtime_report,
        maintenance_report=maintenance_report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
