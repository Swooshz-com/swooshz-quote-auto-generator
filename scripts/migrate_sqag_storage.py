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
    migrator_url = webapp.configured_migrator_database_url()
    runtime_family = webapp.database_family_from_url(runtime_url)

    # SQLite is the explicit local-only exception.  Every PostgreSQL
    # mutation must use the dedicated migration projection; the runtime and
    # maintenance projections are never fallback authorities.
    if runtime_family == "sqlite":
        database_url = runtime_url
    elif migrator_url and webapp.postgres_database_url_is_supported(migrator_url):
        database_url = migrator_url
    else:
        print("SQAG storage migrations require the dedicated PostgreSQL migration URL.", file=sys.stderr)
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
