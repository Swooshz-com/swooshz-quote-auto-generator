#!/usr/bin/env python3
"""Apply the reviewed SQAG platform-scoped storage migrations."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


class MigrationConfigurationError(RuntimeError):
    """Raised when the operator path cannot bind an authorized database URL."""


def _migration_database_url() -> str:
    """Select SQLite locally or the dedicated migrator URL for PostgreSQL."""

    configured_url = webapp.configured_database_url()
    configured_family = webapp.database_family_from_url(configured_url)
    if configured_family == "sqlite":
        return configured_url
    if configured_family == "unsupported":
        raise MigrationConfigurationError("runtime_database_url_unsupported")

    migrator_url = webapp.configured_migrator_database_url()
    if not migrator_url:
        raise MigrationConfigurationError("migrator_database_url_absent")
    if not webapp.postgres_database_url_is_supported(migrator_url):
        raise MigrationConfigurationError("migrator_database_url_requires_postgres")
    return migrator_url


def main() -> int:
    try:
        database_url = _migration_database_url()
    except MigrationConfigurationError:
        print(
            "SQAG migration configuration is invalid; the operator path failed closed.",
            file=sys.stderr,
        )
        return 2
    try:
        result = webapp.apply_sqag_storage_migrations(database_url)
    except Exception:
        print("SQAG storage migrations failed closed; inspect privacy-safe operator logs.", file=sys.stderr)
        return 2
    if isinstance(result, dict):
        applied = result.get("appliedNow", [])
        print(f"SQAG PostgreSQL migration head: {result.get('expectedHead') or 'none'}")
        print("Applied migration IDs: " + (", ".join(applied) if applied else "none"))
    else:
        print("SQAG local storage migrations applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
