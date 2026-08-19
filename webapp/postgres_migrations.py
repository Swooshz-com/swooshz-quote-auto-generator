"""Auditable, fail-closed PostgreSQL migrations for SQAG.

This module deliberately has no application-startup hook. Operators invoke it
through the dedicated migration and preflight scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence


LEDGER_TABLE = "sqag_schema_migrations"
MIGRATION_LOCK_KEY = 728_802_465_115_198_728
MIGRATION_FILE_NAMES = (
    "001_platform_scoped_storage.sql",
    "003_object_artifact_metadata.sql",
    "004_generation_forensics_feedback_retention_postgres.sql",
    "005_forensic_postgres_delete_guards.sql",
    "006_quote_publication_versions_postgres.sql",
    "007_feedback_publication_binding_postgres.sql",
)
EXPECTED_TABLES = frozenset(
    {
        "sqag_profiles",
        "sqag_pricing_references",
        "sqag_quote_sessions",
        "sqag_object_artifacts",
        "sqag_generation_runs",
        "sqag_generation_evidence",
        "sqag_audit_events",
        "sqag_feedback",
        "sqag_feedback_status_history",
        "sqag_legal_holds",
        "sqag_retention_delete_authorizations",
        "sqag_deletion_receipts",
        "sqag_retention_scan_cursors",
        "sqag_quote_publication_versions",
        "sqag_quote_publication_artifacts",
    }
)
EXPECTED_INDEXES = frozenset(
    {
        "sqag_generation_runs_workspace_job_uidx",
        "sqag_generation_runs_workspace_idempotency_uidx",
        "sqag_legal_holds_active_target_uidx",
        "sqag_generation_runs_workspace_started_idx",
        "sqag_generation_runs_retention_idx",
        "sqag_generation_runs_actor_idx",
        "sqag_generation_evidence_run_idx",
        "sqag_generation_evidence_retention_idx",
        "sqag_audit_events_run_idx",
        "sqag_audit_events_actor_idx",
        "sqag_audit_events_feedback_idx",
        "sqag_audit_events_retention_idx",
        "sqag_feedback_workspace_status_idx",
        "sqag_feedback_support_idx",
        "sqag_feedback_retention_idx",
        "sqag_feedback_history_parent_idx",
        "sqag_legal_holds_state_idx",
        "sqag_deletion_receipts_retention_idx",
        "sqag_quote_publication_versions_session_idx",
        "sqag_quote_publication_versions_retention_idx",
        "sqag_quote_publication_artifacts_session_idx",
        "sqag_feedback_publication_idx",
    }
)
EXPECTED_TRIGGERS = frozenset(
    {
        "sqag_generation_evidence_no_update",
        "sqag_audit_events_no_update",
        "sqag_generation_evidence_guard_delete",
        "sqag_audit_events_guard_delete",
        "sqag_feedback_linkage_no_update",
    }
)
EXPECTED_ROUTINES = frozenset(
    {"sqag_reject_immutable_change", "sqag_require_retention_delete_authorization"}
)
EXPECTED_TRIGGER_ROUTINE_LINKS = {
    "sqag_reject_immutable_change": frozenset(
        {
            ("sqag_generation_evidence_no_update", "sqag_generation_evidence"),
            ("sqag_audit_events_no_update", "sqag_audit_events"),
            ("sqag_feedback_linkage_no_update", "sqag_feedback"),
        }
    ),
    "sqag_require_retention_delete_authorization": frozenset(
        {
            ("sqag_generation_evidence_guard_delete", "sqag_generation_evidence"),
            ("sqag_audit_events_guard_delete", "sqag_audit_events"),
        }
    ),
}
MIGRATION_TABLES = {
    "001_platform_scoped_storage.sql": frozenset(
        {"sqag_profiles", "sqag_pricing_references", "sqag_quote_sessions"}
    ),
    "003_object_artifact_metadata.sql": frozenset({"sqag_object_artifacts"}),
    "004_generation_forensics_feedback_retention_postgres.sql": frozenset(
        {
            "sqag_generation_runs",
            "sqag_generation_evidence",
            "sqag_audit_events",
            "sqag_feedback",
            "sqag_feedback_status_history",
            "sqag_legal_holds",
            "sqag_retention_delete_authorizations",
            "sqag_deletion_receipts",
            "sqag_retention_scan_cursors",
        }
    ),
    "005_forensic_postgres_delete_guards.sql": frozenset(),
    "006_quote_publication_versions_postgres.sql": frozenset(
        {"sqag_quote_publication_versions", "sqag_quote_publication_artifacts"}
    ),
    "007_feedback_publication_binding_postgres.sql": frozenset(),
}
if tuple(MIGRATION_TABLES) != MIGRATION_FILE_NAMES:
    raise RuntimeError("SQAG PostgreSQL migration table map must match the ordered manifest.")
if set().union(*MIGRATION_TABLES.values()) != EXPECTED_TABLES:
    raise RuntimeError("SQAG PostgreSQL expected table inventory must match the migration manifest.")


class MigrationSafetyError(RuntimeError):
    """Raised when ledger or schema state makes migration unsafe."""

    def __init__(self, blocker: str) -> None:
        super().__init__(f"SQAG PostgreSQL migration blocked: {blocker}")
        self.blocker = blocker


@dataclass(frozen=True)
class Migration:
    sequence_no: int
    migration_id: str
    path: Path
    checksum_sha256: str


def canonical_migration_payload(path: Path) -> bytes:
    """Read migration source as strict UTF-8 with canonical LF line endings."""

    migration_id = path.name
    try:
        raw_payload = path.read_bytes()
    except OSError as exc:
        raise MigrationSafetyError(f"migration_source_missing:{migration_id}") from exc
    try:
        source = raw_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationSafetyError(f"migration_source_invalid_utf8:{migration_id}") from exc
    return source.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def migration_manifest(migrations_dir: Path) -> tuple[Migration, ...]:
    migrations: list[Migration] = []
    for sequence_no, file_name in enumerate(MIGRATION_FILE_NAMES, start=1):
        path = migrations_dir / file_name
        payload = canonical_migration_payload(path)

        migrations.append(
            Migration(
                sequence_no=sequence_no,
                migration_id=file_name,
                path=path,
                checksum_sha256=sha256(payload).hexdigest(),
            )
        )
    return tuple(migrations)


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError):
        return row[index]


def _fetch_public_tables(connection: Any) -> set[str]:
    rows = connection.execute(
        "select tablename from pg_catalog.pg_tables "
        "where schemaname = 'public' order by tablename"
    ).fetchall()
    return {str(_row_value(row, "tablename")) for row in rows}


def _fetch_public_indexes(connection: Any) -> set[str]:
    rows = connection.execute(
        "select indexname from pg_catalog.pg_indexes "
        "where schemaname = 'public' order by indexname"
    ).fetchall()
    return {str(_row_value(row, "indexname")) for row in rows}


def _fetch_public_triggers(connection: Any) -> set[str]:
    rows = connection.execute(
        "select trigger_name from information_schema.triggers "
        "where trigger_schema = 'public' order by trigger_name"
    ).fetchall()
    return {str(_row_value(row, "trigger_name")) for row in rows}


def _fetch_public_routines(connection: Any) -> set[str]:
    rows = connection.execute(
        "select routine_name from information_schema.routines "
        "where routine_schema = 'public' order by routine_name"
    ).fetchall()
    return {str(_row_value(row, "routine_name")) for row in rows}


def _ledger_exists(connection: Any) -> bool:
    row = connection.execute(
        "select to_regclass('public.sqag_schema_migrations') as ledger_table"
    ).fetchone()
    return bool(row and _row_value(row, "ledger_table"))


def _ledger_rows(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        "select sequence_no, migration_id, checksum_sha256, applied_at "
        "from public.sqag_schema_migrations order by sequence_no"
    ).fetchall()
    return [
        {
            "sequence_no": int(_row_value(row, "sequence_no", 0)),
            "migration_id": str(_row_value(row, "migration_id", 1)),
            "checksum_sha256": str(_row_value(row, "checksum_sha256", 2)),
            "applied_at": _row_value(row, "applied_at", 3),
        }
        for row in rows
    ]


def inspect_postgres_migrations(
    connection: Any,
    migrations: Sequence[Migration],
) -> dict[str, Any]:
    """Inspect ledger and schema without making any database mutation."""

    expected_ids = [migration.migration_id for migration in migrations]
    expected_head = expected_ids[-1] if expected_ids else None
    public_tables = _fetch_public_tables(connection)
    ledger_exists = _ledger_exists(connection)
    blockers: list[str] = []
    applied_rows: list[dict[str, Any]] = []

    if ledger_exists:
        applied_rows = _ledger_rows(connection)
        if len(applied_rows) > len(migrations):
            blockers.append("unexpected_applied_migration")
        for position, row in enumerate(applied_rows):
            if position >= len(migrations):
                break
            expected = migrations[position]
            if row["sequence_no"] != expected.sequence_no or row["migration_id"] != expected.migration_id:
                blockers.append("unknown_or_out_of_order_migration")
                break
            if row["checksum_sha256"] != expected.checksum_sha256:
                blockers.append(f"checksum_drift:{expected.migration_id}")
        applied_count = min(len(applied_rows), len(migrations))
        if not blockers:
            applied_tables = set().union(
                *(MIGRATION_TABLES.get(item.migration_id, frozenset()) for item in migrations[:applied_count])
            )
            pending_tables = set().union(
                *(MIGRATION_TABLES.get(item.migration_id, frozenset()) for item in migrations[applied_count:])
            )
            missing_tables = sorted(applied_tables - public_tables)
            unexpected_pending_tables = sorted(pending_tables & public_tables)
            if missing_tables:
                blockers.append("schema_ledger_inconsistent_missing_tables:" + ",".join(missing_tables))
            if unexpected_pending_tables:
                blockers.append(
                    "schema_ledger_inconsistent_unapplied_tables:" + ",".join(unexpected_pending_tables)
                )
            if applied_count == len(migrations):
                missing_indexes = sorted(EXPECTED_INDEXES - _fetch_public_indexes(connection))
                missing_triggers = sorted(EXPECTED_TRIGGERS - _fetch_public_triggers(connection))
                missing_routines = sorted(EXPECTED_ROUTINES - _fetch_public_routines(connection))
                if missing_indexes:
                    blockers.append("schema_ledger_inconsistent_missing_indexes:" + ",".join(missing_indexes))
                if missing_triggers:
                    blockers.append("schema_ledger_inconsistent_missing_triggers:" + ",".join(missing_triggers))
                if missing_routines:
                    blockers.append("schema_ledger_inconsistent_missing_routines:" + ",".join(missing_routines))
    else:
        applied_count = 0
        if public_tables:
            blockers.append("existing_schema_without_trusted_ledger")

    applied_ids = [row["migration_id"] for row in applied_rows]
    pending_ids = expected_ids[applied_count:] if not blockers else []
    return {
        "status": "unsafe" if blockers else "ready",
        "safeToApply": not blockers,
        "ledgerState": "present" if ledger_exists else "missing",
        "expectedHead": expected_head,
        "appliedHead": applied_ids[-1] if applied_ids else None,
        "appliedMigrationIds": applied_ids,
        "pendingMigrationIds": pending_ids,
        "blockers": blockers,
    }


def _create_ledger(connection: Any) -> None:
    connection.execute(
        """
create table public.sqag_schema_migrations (
  sequence_no integer not null unique check (sequence_no > 0),
  migration_id text primary key,
  checksum_sha256 char(64) not null check (checksum_sha256 ~ '^[0-9a-f]{64}$'),
  applied_at timestamptz not null default current_timestamp
)
"""
    )


def execute_migration_sql(connection: Any, sql: str) -> None:
    marker = "-- SQAG_STATEMENT_BOUNDARY"
    if marker in sql:
        parts = sql.split(marker)
        for statement in (part.strip() for part in parts[0].split(";")):
            if statement:
                connection.execute(statement)
        for statement in (part.strip() for part in parts[1:]):
            if statement:
                connection.execute(statement)
        return
    for statement in (part.strip() for part in sql.split(";")):
        if statement:
            connection.execute(statement)


def apply_postgres_migrations(
    connection: Any,
    migrations: Sequence[Migration],
) -> dict[str, Any]:
    """Apply the pending immutable prefix in the caller-owned transaction."""

    connection.execute("set local search_path to public, pg_catalog")
    connection.execute("select pg_catalog.pg_advisory_xact_lock(?)", (MIGRATION_LOCK_KEY,))
    before = inspect_postgres_migrations(connection, migrations)
    if not before["safeToApply"]:
        raise MigrationSafetyError(str(before["blockers"][0]))

    if before["ledgerState"] == "missing":
        _create_ledger(connection)

    applied_now: list[str] = []
    applied_count = len(before["appliedMigrationIds"])
    for migration in migrations[applied_count:]:
        payload = canonical_migration_payload(migration.path)

        actual_checksum = sha256(payload).hexdigest()
        if actual_checksum != migration.checksum_sha256:
            raise MigrationSafetyError(f"migration_source_changed_during_run:{migration.migration_id}")
        sql = payload.decode("utf-8")
        execute_migration_sql(connection, sql)
        connection.execute(
            "insert into public.sqag_schema_migrations "
            "(sequence_no, migration_id, checksum_sha256) values (?, ?, ?)",
            (migration.sequence_no, migration.migration_id, migration.checksum_sha256),
        )
        applied_now.append(migration.migration_id)

    after = inspect_postgres_migrations(connection, migrations)
    if not after["safeToApply"] or after["pendingMigrationIds"]:
        blocker = after["blockers"][0] if after["blockers"] else "migration_head_not_reached"
        raise MigrationSafetyError(str(blocker))

    return {
        "expectedHead": migrations[-1].migration_id if migrations else None,
        "appliedNow": applied_now,
        "alreadyApplied": [migration.migration_id for migration in migrations[:applied_count]],
    }
