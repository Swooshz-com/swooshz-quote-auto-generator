#!/usr/bin/env python3
"""Migrate legacy inline quote-session drafts for one workspace into object storage.

The default mode is count-only. Apply mode is bounded and never runs during
application startup or deploy. After recovery-capable code is deployed, repeat
apply mode per workspace with each reported ``next_cursor`` so malformed early
rows cannot starve later valid rows. A final pass from the beginning retries
remaining failures; no failed inline record is discarded automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Count or migrate legacy inline draft files for exactly one SQAG "
            "workspace."
        ),
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--after-session-id",
        default="",
        help="Continue after the prior batch's next_cursor value.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write a bounded batch to configured object storage and metadata storage.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database_url = webapp.configured_database_url()
    if not database_url:
        print("SQAG_DATABASE_URL is required.", file=sys.stderr)
        return 2
    try:
        storage = webapp.DatabaseSqagStorage(
            database_url,
            args.workspace_id,
            role="admin",
            user_id="inline-draft-recovery",
        )
        if args.apply:
            result = storage.migrate_workspace_inline_draft_files_to_object_storage(
                limit=args.limit,
                after_session_id=args.after_session_id,
            )
            status = "ok" if not result["failed"] and not result["remaining"] else "incomplete"
        else:
            count = storage.count_workspace_inline_draft_files_for_object_migration()
            result = {
                "workspace_id": storage.workspace_id,
                "candidates": count,
            }
            status = "dry_run"
    except (ValueError, webapp.SqagStorageAccessError) as exc:
        reason = webapp.safe_resource_id(
            getattr(exc, "reason", "inline_draft_migration_failed"),
            "inline_draft_migration_failed",
        )
        print(json.dumps({"status": "blocked", "reason": reason}, sort_keys=True))
        return 1
    print(json.dumps({"status": status, **result}, sort_keys=True))
    if args.apply and status != "ok":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

