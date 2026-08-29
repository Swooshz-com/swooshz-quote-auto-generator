#!/usr/bin/env python3
"""Apply the reviewed SQAG platform-scoped storage migrations."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


def main() -> int:
    runtime_url = webapp.configured_database_url()
    if not runtime_url:
        print("SQAG_DATABASE_URL is required for the storage migration.", file=sys.stderr)
        return 2
    family = webapp.database_family_from_url(runtime_url)
    if family == "sqlite":
        migration_url = runtime_url
    elif family == "postgres_compatible":
        migration_url = webapp.configured_migrator_database_url()
        if not migration_url:
            print(
                "SQAG_MIGRATOR_DATABASE_URL is required for PostgreSQL storage migrations.",
                file=sys.stderr,
            )
            return 2
        if not webapp.postgres_database_url_is_supported(migration_url):
            print(
                "SQAG_MIGRATOR_DATABASE_URL must use a supported PostgreSQL URL.",
                file=sys.stderr,
            )
            return 2
    else:
        print("SQAG_DATABASE_URL uses an unsupported storage backend.", file=sys.stderr)
        return 2
    try:
        result = webapp.apply_sqag_storage_migrations(migration_url)
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
