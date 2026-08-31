#!/usr/bin/env python3
"""Apply the reviewed SQAG platform-scoped storage migrations."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


def _migration_database_url() -> str | None:
    configured = webapp.configured_database_url()
    if configured and webapp.database_family_from_url(configured) == "sqlite":
        return configured
    migrator_url = webapp.configured_migrator_database_url()
    if migrator_url and webapp.postgres_database_url_is_supported(migrator_url):
        return migrator_url
    return None


def main() -> int:
    database_url = _migration_database_url()
    if not database_url:
        print(
            "SQAG dedicated migrator database URL is required for PostgreSQL storage migrations.",
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
