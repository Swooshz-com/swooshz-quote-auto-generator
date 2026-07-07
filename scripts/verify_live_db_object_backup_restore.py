#!/usr/bin/env python3
"""Fail-closed preflight for live SQAG DB+object backup/restore evidence.

This script does not perform a live backup or restore. It checks whether the
operator has supplied the minimum isolated-target and decision markers needed
before a future live restore drill can safely run. Output is metadata-only:
no DB URL, provider value, bucket, endpoint, credential, object key, path,
artifact byte, tenant data, or quote contents are printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Mapping

from pathlib import Path

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
)


LIVE_DB_OBJECT_BACKUP_RESTORE_ENV_NAME = "SQAG_LIVE_DB_OBJECT_BACKUP_RESTORE_EVIDENCE"
RESTORE_DATABASE_URL_ENV_NAME = "SQAG_RESTORE_DATABASE_URL"
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
    webapp.KQAG_DATABASE_URL_ENV_NAME,
    *ACTIVE_OBJECT_ENV_NAMES,
    RESTORE_DATABASE_URL_ENV_NAME,
    *RESTORE_OBJECT_ENV_NAMES,
    BACKUP_RESTORE_DECISION_ID_ENV_NAME,
    BACKUP_RESTORE_WINDOW_ID_ENV_NAME,
]
TRUE_VALUES = {"1", "true", "yes", "on", "run", "enabled"}


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
    active = _clean(env.get(webapp.KQAG_DATABASE_URL_ENV_NAME))
    restore = _clean(env.get(RESTORE_DATABASE_URL_ENV_NAME))
    return bool(active and restore and active != restore)


def _object_targets_distinct(env: Mapping[str, str]) -> bool:
    active = _active_object_target(env)
    restore = _restore_object_target(env)
    return bool(all(active) and all(restore) and active != restore)


def run_verification(*, env: Mapping[str, str] | None = None) -> dict[str, object]:
    effective_env = dict(os.environ if env is None else env)
    missing = _missing_env_names(effective_env)
    live_opt_in_enabled = _enabled(effective_env)
    decision_present = _present(effective_env, BACKUP_RESTORE_DECISION_ID_ENV_NAME)
    window_present = _present(effective_env, BACKUP_RESTORE_WINDOW_ID_ENV_NAME)
    database_targets_distinct = _database_targets_distinct(effective_env)
    object_targets_distinct = _object_targets_distinct(effective_env)
    isolated_restore_target = database_targets_distinct and object_targets_distinct

    blockers: list[str] = []
    if missing or not live_opt_in_enabled:
        blockers.append("live_db_object_backup_restore_evidence_not_enabled_or_incomplete")
    if not isolated_restore_target:
        blockers.append("blocked_isolated_restore_target_missing")
    if not (decision_present and window_present):
        blockers.append("blocked_backup_restore_decision_missing")
    if not blockers:
        blockers.append("live_db_object_backup_restore_execution_not_implemented")

    return {
        "schema": "swooshz.sqag.live-db-object-backup-restore-preflight.v1",
        "status": "blocked",
        "live_db_object_backup_restore_evidence_supported": False,
        "required_env_names": list(REQUIRED_ENV_NAMES),
        "missing_env_names": missing,
        "checks": {
            "live_evidence_opt_in_enabled": live_opt_in_enabled,
            "backup_ownership_decision_present": decision_present,
            "restore_window_decision_present": window_present,
            "active_database_target_present": _present(effective_env, webapp.KQAG_DATABASE_URL_ENV_NAME),
            "restore_database_target_present": _present(effective_env, RESTORE_DATABASE_URL_ENV_NAME),
            "database_targets_distinct": database_targets_distinct,
            "active_object_target_present": all(_present(effective_env, name) for name in ACTIVE_OBJECT_ENV_NAMES),
            "restore_object_target_present": all(_present(effective_env, name) for name in RESTORE_OBJECT_ENV_NAMES),
            "object_targets_distinct": object_targets_distinct,
            "isolated_restore_target_available": isolated_restore_target,
            "destructive_restore_prevented": True,
        },
        "privacy": {
            "output": "metadata-only",
            "database_urls_printed": False,
            "provider_values_printed": False,
            "object_keys_printed": False,
            "artifact_bytes_printed": False,
            "private_paths_printed": False,
            "tenant_data_printed": False,
            "generated_quote_contents_printed": False,
        },
        "production_ready": False,
        "blockers": blockers,
        "notes": [
            "This preflight does not run backup, restore, database writes, object writes, or destructive operations.",
            "Live DB+object backup/restore evidence requires an isolated restore database and isolated restore object target.",
            "If the active and restore targets are identical, the preflight stays blocked.",
            "Operator backup ownership and restore-window decisions must be recorded outside Git before any future live restore drill.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Run metadata-only preflight for live SQAG DB+object backup/restore evidence."
    )


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    report = run_verification()
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
