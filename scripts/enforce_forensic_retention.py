#!/usr/bin/env python3
"""Workspace-scoped SQAG forensic retention worker with an explicit apply gate."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.forensics import ForensicStore
from webapp import server as webapp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete expired SQAG forensic records for one workspace.")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-url", help="Explicit SQLite or Postgres database URL. Values are never printed.")
    parser.add_argument("--use-configured-database", action="store_true", help="Use SQAG_DATABASE_URL from the configured environment.")
    parser.add_argument("--apply", action="store_true", help="Required to perform deletion and create receipts.")
    parser.add_argument("--now", help="Optional UTC ISO timestamp for deterministic operator testing.")
    return parser.parse_args()


def parsed_now(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--now must include a timezone.")
    return parsed.astimezone(dt.timezone.utc)


def blocked(reason: str) -> dict[str, object]:
    return {
        "schema": "swooshz.sqag.forensic-retention-worker.v1",
        "status": "blocked",
        "reason": reason,
        "production_ready": False,
    }


def main() -> int:
    args = parse_args()
    if not args.apply:
        print(json.dumps(blocked("apply_confirmation_required"), indent=2, sort_keys=True))
        return 2
    if args.database_url and args.use_configured_database:
        print(json.dumps(blocked("choose_one_database_source"), indent=2, sort_keys=True))
        return 2
    database_url = args.database_url or (webapp.configured_database_url() if args.use_configured_database else "")
    now = parsed_now(args.now)

    if database_url:
        storage = webapp.DatabaseSqagStorage(database_url, args.workspace_id, role="admin", user_id="retention-worker")
        storage.ensure_ready()
        with storage.connection() as connection:
            result = ForensicStore(connection, args.workspace_id, "retention-worker").enforce_retention(now=now)
    else:
        path = webapp.configured_data_root() / "forensics.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript((ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql").read_text(encoding="utf-8"))
            result = ForensicStore(connection, args.workspace_id, "retention-worker").enforce_retention(now=now)
        finally:
            connection.close()

    report = {
        "schema": "swooshz.sqag.forensic-retention-worker.v1",
        "status": "completed",
        "workspace_scoped": True,
        "examined": result.examined,
        "deleted": result.deleted,
        "held": result.held,
        "deletion_receipts_created": result.deleted,
        "production_ready": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
