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
    database_url = webapp.configured_database_url()
    if not database_url:
        print("SQAG_DATABASE_URL is required for the storage migration.", file=sys.stderr)
        return 2
    webapp.apply_sqag_storage_migrations(database_url)
    print("SQAG storage migrations applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
