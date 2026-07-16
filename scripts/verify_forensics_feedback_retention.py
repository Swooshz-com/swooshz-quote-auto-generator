#!/usr/bin/env python3
"""Run synthetic-only checks for SQAG forensics, feedback, and retention."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import inspect
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import generate_quote
from webapp.forensics import ForensicStore, add_calendar_years, iso_timestamp
from webapp import server as webapp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify SQAG forensic and retention contracts with synthetic SQLite data.")
    parser.add_argument("--work-dir", type=Path)
    return parser.parse_args()


def run_verification(work_dir: Path | None = None) -> dict[str, object]:
    with tempfile.TemporaryDirectory(dir=str(work_dir) if work_dir else None) as raw_dir:
        database_path = Path(raw_dir) / "forensics.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.executescript((ROOT / "migrations" / "004_generation_forensics_feedback_retention.sql").read_text(encoding="utf-8"))
        store = ForensicStore(connection, "workspace-synthetic", "pid-v1-synthetic")

        run_id = store.record_run_started("generate", {"image_count": 2, "payload_shape_sha256": "0" * 64})
        store.finish_run(
            run_id,
            "blocked",
            error_category="images_missing",
            quote_session_id="quote-synthetic",
            result_summary={"artifact_count": 0},
            canonical_manifest={"generator_executed": False, "artifacts": []},
        )
        run_row = dict(connection.execute("select * from sqag_generation_runs where run_id = ?", (run_id,)).fetchone())
        evidence_rows = [dict(row) for row in connection.execute("select * from sqag_generation_evidence where run_id = ?", (run_id,)).fetchall()]
        audit_types = [row[0] for row in connection.execute("select event_type from sqag_audit_events where run_id = ? order by created_at, event_id", (run_id,)).fetchall()]

        feedback = store.submit_feedback({"category": "incorrect_output", "title": "Synthetic mismatch", "message": "Synthetic pricing mismatch", "run_id": run_id, "validated_session_id": "quote-synthetic", "include_link": True, "impact": "medium", "diagnostic_metadata": {"current_route": "/synthetic"}})
        feedback_row = dict(connection.execute("select * from sqag_feedback where feedback_id = ?", (feedback["feedback_id"],)).fetchone())
        history_count = connection.execute("select count(*) from sqag_feedback_status_history where feedback_id = ?", (feedback["feedback_id"],)).fetchone()[0]

        past = iso_timestamp(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc))
        connection.execute("update sqag_generation_runs set retention_expires_at = ?, original_retention_expires_at = ? where run_id = ?", (past, past, run_id))
        connection.execute("update sqag_generation_evidence set retention_expires_at = ?, original_retention_expires_at = ? where run_id = ?", (past, past, run_id))
        connection.execute("update sqag_audit_events set retention_expires_at = ?, original_retention_expires_at = ? where run_id = ?", (past, past, run_id))
        store.update_feedback_status(feedback["support_reference"], "resolved", resolution_note="Synthetic verifier closure")
        connection.execute("update sqag_audit_events set retention_expires_at = ?, original_retention_expires_at = ? where run_id = ?", (past, past, run_id))
        connection.execute("update sqag_feedback set retention_expires_at = ? where feedback_id = ?", (past, feedback["feedback_id"]))
        connection.execute("update sqag_feedback_status_history set retention_expires_at = ? where feedback_id = ?", (past, feedback["feedback_id"]))
        connection.commit()
        hold_set = store.set_legal_hold("sqag_generation_runs", "run_id", run_id, True)
        held_result = store.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
        held_exists = connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0] == 1
        hold_released = store.set_legal_hold("sqag_generation_runs", "run_id", run_id, False)
        deletion_result = store.enforce_retention(now=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc))
        deleted = connection.execute("select count(*) from sqag_generation_runs where run_id = ?", (run_id,)).fetchone()[0] == 0
        receipt_exists = connection.execute("select count(*) from sqag_deletion_receipts where record_id = ?", (run_id,)).fetchone()[0] == 1
        sqlite_authorizations_consumed = connection.execute(
            "select count(*) from sqag_retention_delete_authorizations"
        ).fetchone()[0] == 0
        postgres_guard_sql = (
            ROOT / "migrations" / "005_forensic_postgres_delete_guards.sql"
        ).read_text(encoding="utf-8").lower()
        postgres_authorization_is_consumed = all(
            token in postgres_guard_sql
            for token in ("delete from sqag_retention_delete_authorizations", "returning authorization_id", "workspace_id = old.workspace_id", "record_type = expected_type", "record_id = expected_id")
        ) and "if not exists (" not in postgres_guard_sql
        lock_source = inspect.getsource(ForensicStore._acquire_transaction_locks)
        postgres_retention_lock_contract = all(
            token in lock_source
            for token in (
                "pg_advisory_xact_lock",
                "begin immediate",
                "self.workspace_id",
            )
        )

        leap_start = dt.datetime(2024, 2, 29, tzinfo=dt.timezone.utc)
        calendar_expiry = add_calendar_years(leap_start)
        ownerless = {"session_id": "quote-ownerless", "owner": {}}
        editor = webapp.DatabaseSqagStorage("sqlite:///:memory:", "workspace-synthetic", role="editor", user_id="user-editor")
        with mock.patch.dict(os.environ, {webapp.TRACKING_HMAC_KEY_ENV_NAME: "synthetic-tracking-key-with-enough-entropy", webapp.TRACKING_HMAC_KEY_VERSION_ENV_NAME: "synthetic-v1"}):
            tracking_identifier = webapp.privacy_safe_audit_tracking_id("user-editor")
        schema_storage = object.__new__(webapp.DatabaseSqagStorage)
        schema_storage.database_family = "sqlite"
        schema_storage.connection = lambda: contextlib.nullcontext(connection)
        sqlite_schema_ready = True
        try:
            schema_storage._ensure_schema(
                webapp.SQAG_FORENSIC_REQUIRED_COLUMNS,
                reason="storage_forensics_database_not_migrated",
            )
        except webapp.SqagStorageAccessError:
            sqlite_schema_ready = False
        postgres_sql = "\n".join(
            path.read_text(encoding="utf-8").lower()
            for path in (
                ROOT / "migrations" / "004_generation_forensics_feedback_retention_postgres.sql",
                ROOT / "migrations" / "005_forensic_postgres_delete_guards.sql",
            )
        )
        postgres_schema_contract_ready = all(
            name.lower() in postgres_sql
            for name in (
                set(webapp.SQAG_FORENSIC_REQUIRED_INDEXES)
                | set(webapp.SQAG_FORENSIC_REQUIRED_TRIGGERS)
                | set(webapp.SQAG_FORENSIC_POSTGRES_REQUIRED_ROUTINES)
            )
        ) and all(
            table.lower() in postgres_sql
            and all(column.lower() in postgres_sql for column in columns)
            for table, columns in webapp.SQAG_FORENSIC_REQUIRED_COLUMNS.items()
        )
        standalone_id = store.append_audit(
            "synthetic_standalone_retention",
            {"synthetic": True},
            now=dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
        )
        standalone_result = store.enforce_retention(
            now=dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc)
        )
        standalone_deleted = connection.execute(
            "select count(*) from sqag_audit_events where event_id = ?",
            (standalone_id,),
        ).fetchone()[0] == 0
        checks = {
            "blocked_attempt_is_durable": run_row["status"] == "blocked" and run_row["error_category"] == "images_missing",
            "canonical_evidence_is_hashed": {
                row["evidence_type"] for row in evidence_rows
            } == {"request_manifest", "result_summary", "generation_manifest"}
            and all(len(row["evidence_sha256"]) == 64 for row in evidence_rows),
            "append_only_audit_covers_lifecycle": {"generation_received", "generation_blocked"}.issubset(set(audit_types)),
            "feedback_links_without_evidence_copy": feedback_row["run_id"] == run_id and feedback_row["session_id"] == "quote-synthetic" and feedback_row["support_reference"].startswith("SQAG-FB-") and "pricing mismatch" not in feedback_row["diagnostic_metadata_json"] and history_count == 1,
            "three_calendar_year_rule_handles_leap_day": calendar_expiry == dt.datetime(2027, 2, 28, tzinfo=dt.timezone.utc),
            "legal_hold_preserves_expired_record": hold_set and held_result.held >= 1 and held_exists,
            "release_makes_expired_record_eligible": hold_released and deletion_result.deleted >= 1 and deleted and receipt_exists,
            "sqlite_delete_authorizations_are_consumed": sqlite_authorizations_consumed,
            "postgres_delete_authorization_contract_is_single_use": postgres_authorization_is_consumed,
            "retention_graph_lock_contract_is_transaction_scoped": postgres_retention_lock_contract,
            "complete_sqlite_forensic_schema_is_ready": sqlite_schema_ready,
            "complete_postgres_forensic_contract_is_declared": postgres_schema_contract_ready,
            "standalone_audit_retention_is_enforced": standalone_result.standalone_deleted == 1 and standalone_deleted,
            "ownerless_sessions_fail_closed": not editor._quote_session_visible_to_current_user(ownerless) and not editor._quote_session_editable_by_current_user(ownerless),
            "tracking_identifier_is_keyed_and_versioned": tracking_identifier.startswith("pid-synthetic-v1-") and tracking_identifier != "user-editor",
            "xlsx_column_bound_is_enforced": False,
        }
        try:
            generate_quote.col_to_index("ZZZZZZ1")
        except ValueError:
            checks["xlsx_column_bound_is_enforced"] = True
        connection.close()

    passed = all(checks.values())
    return {
        "schema": "swooshz.sqag.forensics-feedback-retention-evidence.v1",
        "status": "passed" if passed else "failed",
        "synthetic_only": True,
        "checks": checks,
        "production_ready": False,
    }


def main() -> int:
    args = parse_args()
    result = run_verification(args.work_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
