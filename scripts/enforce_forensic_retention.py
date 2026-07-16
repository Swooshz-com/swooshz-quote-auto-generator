#!/usr/bin/env python3
"""Workspace-scoped SQAG forensic retention worker with explicit safe modes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp.forensics import (
    ForensicStore,
    RetentionGraphHeld,
    RetentionPublicationDependency,
)
from webapp import server as webapp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process a bounded batch of expired SQAG forensic records for one workspace.")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--database-url", help="Explicit local SQLite URL only. Use --use-configured-database for Postgres.")
    parser.add_argument("--use-configured-database", action="store_true", help="Use SQAG_DATABASE_URL from the configured environment.")
    parser.add_argument("--apply", action="store_true", help="Perform deletion and create receipts.")
    parser.add_argument("--dry-run", action="store_true", help="Report candidates without deletion.")
    parser.add_argument("--batch-size", type=int, default=100, help="Maximum parent graphs to inspect (1-500).")
    parser.add_argument("--now", help="Optional UTC ISO timestamp for deterministic operator testing.")
    return parser.parse_args()


def parsed_now(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--now must include a timezone.")
    return parsed.astimezone(dt.timezone.utc)


def argv_database_url_allowed(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(
        parsed.scheme.casefold() == "sqlite"
        and not parsed.username
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def session_has_retained_forensic_links(
    connection,
    workspace_id: str,
    session_id: str,
    deleting_run_id: str,
) -> bool:
    retained_feedback = connection.execute(
        "select 1 from sqag_feedback where workspace_id = ? and session_id = ? limit 1",
        (workspace_id, session_id),
    ).fetchone()
    if retained_feedback:
        return True
    sibling = connection.execute(
        "select 1 from sqag_generation_runs where workspace_id = ? and quote_session_id = ? and run_id <> ? limit 1",
        (workspace_id, session_id, deleting_run_id),
    ).fetchone()
    return bool(sibling)


def blocked(reason: str) -> dict[str, object]:
    return {
        "schema": "swooshz.sqag.forensic-retention-worker.v1",
        "status": "blocked",
        "reason": reason,
        "production_ready": False,
    }


def _main() -> int:
    args = parse_args()
    if args.apply == args.dry_run:
        print(json.dumps(blocked("choose_exactly_one_of_apply_or_dry_run"), indent=2, sort_keys=True))
        return 2
    if args.database_url and args.use_configured_database:
        print(json.dumps(blocked("choose_one_database_source"), indent=2, sort_keys=True))
        return 2
    if args.database_url and not argv_database_url_allowed(args.database_url):
        print(json.dumps(blocked("database_url_argv_requires_sqlite"), indent=2, sort_keys=True))
        return 2
    database_url = args.database_url or (webapp.configured_database_url() if args.use_configured_database else "")
    now = parsed_now(args.now)
    batch_size = max(1, min(args.batch_size, 500))

    if database_url:
        storage = webapp.DatabaseSqagStorage(database_url, args.workspace_id, role="admin", user_id="retention-worker")
        storage.ensure_ready()
        storage._ensure_schema(webapp.SQAG_FORENSIC_REQUIRED_COLUMNS, reason="storage_forensics_database_not_migrated")
        with storage.connection() as connection:
            def delete_artifacts(item: dict[str, object], finalize_graph) -> bool:
                run_id = str(item.get("run_id") or "")
                session_id = webapp.safe_quote_session_id(item.get("quote_session_id"), "")
                version_result = storage.delete_quote_publication_version_for_retention(
                    run_id,
                    finalize_graph=finalize_graph,
                )
                if version_result == webapp.PUBLICATION_RETENTION_DELETED:
                    return True
                if version_result == webapp.PUBLICATION_RETENTION_HELD:
                    raise RetentionGraphHeld("publication_version_held")
                if version_result == webapp.PUBLICATION_RETENTION_FAILED:
                    return False
                if version_result == webapp.PUBLICATION_RETENTION_REFERENCED:
                    raise RetentionPublicationDependency(
                        "feedback_requires_publication_version"
                    )
                if version_result == webapp.PUBLICATION_RETENTION_CURRENT:
                    if not session_id or session_has_retained_forensic_links(
                        connection, args.workspace_id, session_id, run_id
                    ):
                        raise RetentionPublicationDependency(
                            "current_publication_requires_generation_graph"
                        )
                    return storage.delete_quote_session_for_retention(
                        session_id,
                        finalize_graph=finalize_graph,
                    )

                if not session_id:
                    finalize_graph(connection)
                    return True
                if session_has_retained_forensic_links(
                    connection, args.workspace_id, session_id, run_id
                ):
                    finalize_graph(connection)
                    return True
                return storage.delete_quote_session_for_retention(
                    session_id,
                    finalize_graph=finalize_graph,
                )

            result = ForensicStore(connection, args.workspace_id, "retention-worker").enforce_retention(
                now=now,
                batch_size=batch_size,
                apply=args.apply,
                artifact_delete=delete_artifacts if args.apply else None,
            )
    else:
        path = webapp.configured_data_root() / "forensics.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        database_url = f"sqlite:///{path.as_posix()}"
        with webapp.sqlite_storage_connection(database_url) as connection:
            webapp.upgrade_legacy_local_forensic_schema(connection)
            connection.executescript((ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql").read_text(encoding="utf-8"))
            connection.commit()
            result = ForensicStore(connection, args.workspace_id, "retention-worker", local_mode=webapp.configured_app_mode() == "local").enforce_retention(now=now, batch_size=batch_size, apply=args.apply)

    report = {
        "schema": "swooshz.sqag.forensic-retention-worker.v1",
        "status": "partial_failure" if result.failed else ("completed" if args.apply else "dry_run"),
        "workspace_scoped": True,
        "examined": result.examined,
        "rows_examined": result.examined,
        "deleted": result.deleted,
        "held": result.held,
        "publication_retained": result.publication_retained,
        "deletion_receipts_created": max(0, result.deleted - result.receipt_deleted),
        "parents_processed": result.parents_processed,
        "standalone_audits_examined": result.standalone_examined,
        "standalone_audits_deleted": result.standalone_deleted,
        "failed": result.failed,
        "standalone_audits_held": result.standalone_held,
        "standalone_audits_failed": result.standalone_failed,
        "deletion_receipts_examined": result.receipt_examined,
        "deletion_receipts_deleted": result.receipt_deleted,
        "deletion_receipts_failed": result.receipt_failed,
        "scan_cursor_persisted": bool(args.apply),
        "scan_cursor_dry_run_unchanged": bool(args.dry_run),
        "review_required": result.review_required,
        "batch_size": batch_size,
        "scan_limit": result.scan_limit,
        "scan_exhausted": result.scan_exhausted,
        "production_ready": False,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if result.failed else 0


def main() -> int:
    try:
        return _main()
    except Exception:
        print(json.dumps(blocked("retention_storage_unavailable"), indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
