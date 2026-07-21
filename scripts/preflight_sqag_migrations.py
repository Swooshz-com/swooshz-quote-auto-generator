#!/usr/bin/env python3
"""Read-only SQAG PostgreSQL migration safety preflight."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp
from webapp.postgres_migrations import inspect_postgres_migrations, migration_manifest


def _failure(blocker: str) -> int:
    print(
        json.dumps(
            {
                "status": "unsafe",
                "safeToApply": False,
                "expectedHead": None,
                "appliedHead": None,
                "pendingMigrationIds": [],
                "blockers": [blocker],
            },
            sort_keys=True,
        )
    )
    return 2


def main() -> int:
    database_url = webapp.configured_database_url()
    if not database_url:
        return _failure("database_url_absent")
    if not webapp.postgres_database_url_is_supported(database_url):
        return _failure("postgres_database_required")

    try:
        migrations = migration_manifest(ROOT / "migrations")
        with webapp.postgres_storage_connection(database_url) as connection:
            try:
                connection.execute("set transaction read only")
                report = inspect_postgres_migrations(connection, migrations)
            finally:
                connection.rollback()
    except Exception:
        return _failure("preflight_failed")

    print(json.dumps(report, sort_keys=True, default=str))
    return 0 if report["safeToApply"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
