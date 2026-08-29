"""Auditable, fail-closed PostgreSQL migrations for SQAG.

This module deliberately has no application-startup hook. Operators invoke it
through the dedicated migration and preflight scripts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
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
    "008_quote_session_deletion_hold_authority_postgres.sql",
)
MANAGED_NAMESPACE_PREFIX = "sqag_"

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
    {
        "sqag_reject_immutable_change",
        "sqag_require_retention_delete_authorization",
        "sqag_quote_session_deletion_hold_blocked",
    }
)
EXPECTED_TRIGGER_ROUTINE_KEYS = frozenset(
    {
        ("sqag_reject_immutable_change", ""),
        ("sqag_require_retention_delete_authorization", ""),
    }
)
EXPECTED_CALLABLE_ROUTINE_KEYS = frozenset(
    {("sqag_quote_session_deletion_hold_blocked", "text, text")}
)
EXPECTED_ROUTINE_KEYS = EXPECTED_TRIGGER_ROUTINE_KEYS | EXPECTED_CALLABLE_ROUTINE_KEYS
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


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type_name: str
    nullable: bool
    default_sql: str | None = None
    identity: str = ""
    generated: str = ""


@dataclass(frozen=True)
class ConstraintSpec:
    kind: str
    columns: tuple[str, ...] = ()
    referenced_table: str | None = None
    referenced_columns: tuple[str, ...] = ()
    match_type: str = ""
    on_delete: str = ""
    on_update: str = ""
    expression: str | None = None
    validated: bool = True
    deferrable: bool = False
    initially_deferred: bool = False


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    constraints: tuple[ConstraintSpec, ...]


@dataclass(frozen=True)
class IndexSpec:
    name: str
    table_name: str
    unique: bool
    columns: tuple[str, ...]
    predicate: str | None = None


@dataclass(frozen=True)
class TriggerSpec:
    name: str
    table_name: str
    timing: str
    event: str
    columns: tuple[str, ...]
    row_level: bool
    function_name: str
    function_identity_arguments: str = ""
    enabled: str = "O"


@dataclass(frozen=True)
class RoutineSpec:
    name: str
    identity_arguments: str
    return_type: str
    language: str
    volatility: str
    parallel: str
    security_definer: bool
    config: tuple[str, ...]
    owner: str
    source_file: str
    require_runtime_execute: bool = False
    leakproof: bool = False


@dataclass(frozen=True)
class TableMutationSpec:
    table_name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class MigrationObjectSpec:
    migration_id: str
    object_kind: str
    object_name: str
    table_name: str | None = None
    table_spec: TableSpec | None = None
    index_spec: IndexSpec | None = None
    trigger_spec: TriggerSpec | None = None
    routine_spec: RoutineSpec | None = None
    mutation_spec: TableMutationSpec | None = None


def _col(
    name: str,
    type_name: str = "text",
    *,
    nullable: bool = False,
    default_sql: str | None = None,
) -> ColumnSpec:
    return ColumnSpec(name, type_name, nullable, default_sql)


def _pk(*columns: str) -> ConstraintSpec:
    return ConstraintSpec("p", columns=tuple(columns))


def _unique(*columns: str) -> ConstraintSpec:
    return ConstraintSpec("u", columns=tuple(columns))


def _fk(
    columns: tuple[str, ...],
    referenced_table: str,
    referenced_columns: tuple[str, ...],
    *,
    on_delete: str = "no_action",
    on_update: str = "no_action",
) -> ConstraintSpec:
    return ConstraintSpec(
        "f",
        columns=columns,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
        match_type="simple",
        on_delete=on_delete,
        on_update=on_update,
    )


def _check(expression: str) -> ConstraintSpec:
    return ConstraintSpec("c", expression=expression)


def _table(
    name: str,
    columns: tuple[ColumnSpec, ...],
    constraints: tuple[ConstraintSpec, ...],
) -> TableSpec:
    return TableSpec(name, columns, constraints)


def _index(
    name: str,
    table_name: str,
    columns: tuple[str, ...],
    *,
    unique: bool = False,
    predicate: str | None = None,
) -> IndexSpec:
    return IndexSpec(name, table_name, unique, columns, predicate)


def _trigger(
    name: str,
    table_name: str,
    event: str,
    function_name: str,
    *,
    columns: tuple[str, ...] = (),
) -> TriggerSpec:
    return TriggerSpec(name, table_name, "BEFORE", event, columns, True, function_name)


def _routine(
    name: str,
    identity_arguments: str,
    return_type: str,
    language: str,
    volatility: str,
    parallel: str,
    security_definer: bool,
    config: tuple[str, ...],
    source_file: str,
    *,
    require_runtime_execute: bool = False,
) -> RoutineSpec:
    return RoutineSpec(
        name,
        identity_arguments,
        return_type,
        language,
        volatility,
        parallel,
        security_definer,
        config,
        "sqag_migrator",
        source_file,
        require_runtime_execute,
    )


_TABLE_001_PROFILES = _table(
    "sqag_profiles",
    (
        _col("workspace_id"),
        _col("profile_id"),
        _col("payload_json"),
        _col("created_at"),
        _col("updated_at"),
    ),
    (_pk("workspace_id", "profile_id"),),
)
_TABLE_001_PRICING = _table(
    "sqag_pricing_references",
    (
        _col("workspace_id"),
        _col("reference_id"),
        _col("payload_json"),
        _col("created_at"),
        _col("updated_at"),
    ),
    (_pk("workspace_id", "reference_id"),),
)
_TABLE_001_SESSIONS = _table(
    "sqag_quote_sessions",
    (
        _col("workspace_id"),
        _col("session_id"),
        _col("metadata_json"),
        _col("draft_files_json", default_sql="'[]'"),
        _col("created_at"),
        _col("updated_at"),
    ),
    (_pk("workspace_id", "session_id"),),
)

_TABLE_003_ARTIFACTS = _table(
    "sqag_object_artifacts",
    (
        _col("artifact_id"),
        _col("workspace_id"),
        _col("owner_type"),
        _col("owner_id"),
        _col("platform_user_id", nullable=True),
        _col("session_id", nullable=True),
        _col("job_id", nullable=True),
        _col("artifact_kind"),
        _col("filename"),
        _col("content_type"),
        _col("size_bytes", "integer"),
        _col("checksum_sha256"),
        _col("object_provider_type"),
        _col("object_key_ref"),
        _col("status", default_sql="'active'"),
        _col("retention_status", default_sql="'active'"),
        _col("created_at"),
        _col("updated_at"),
        _col("deleted_at", nullable=True),
    ),
    (
        _pk("artifact_id"),
        _unique("workspace_id", "owner_type", "owner_id", "artifact_kind"),
    ),
)

_TABLE_004_RUNS = _table(
    "sqag_generation_runs",
    (
        _col("run_id"),
        _col("workspace_id"),
        _col("actor_tracking_id"),
        _col("actor_key_version"),
        _col("job_id", nullable=True),
        _col("idempotency_key", nullable=True),
        _col("parent_run_id", nullable=True),
        _col("attempt_number", "integer", default_sql="1"),
        _col("job_type"),
        _col("status"),
        _col("error_category", nullable=True),
        _col("quote_session_id", nullable=True),
        _col("started_at"),
        _col("completed_at", nullable=True),
        _col("app_revision", nullable=True),
        _col("evidence_schema_version"),
        _col("retention_expires_at"),
        _col("original_retention_expires_at"),
        _col("legal_hold", "integer", default_sql="0"),
        _col("deletion_state", default_sql="'active'"),
        _col("deletion_error_code", nullable=True),
        _col("deletion_claimed_at", nullable=True),
    ),
    (
        _pk("run_id"),
        _unique("run_id", "workspace_id"),
        _fk(("parent_run_id",), "sqag_generation_runs", ("run_id",)),
        _check("attempt_number >= 1"),
        _check(
            "status in ('received','queued','running','blocked','completed',"
            "'needs_confirmation','needs_review','completed_with_review_required',"
            "'degraded','failed','cancelled','timed_out','abandoned','superseded')"
        ),
    ),
)
_TABLE_004_EVIDENCE = _table(
    "sqag_generation_evidence",
    (
        _col("evidence_id"),
        _col("run_id"),
        _col("workspace_id"),
        _col("evidence_type"),
        _col("evidence_schema_version"),
        _col("evidence_json"),
        _col("evidence_sha256"),
        _col("created_at"),
        _col("retention_expires_at"),
        _col("original_retention_expires_at"),
        _col("legal_hold", "integer", default_sql="0"),
    ),
    (
        _pk("evidence_id"),
        _fk(
            ("run_id", "workspace_id"),
            "sqag_generation_runs",
            ("run_id", "workspace_id"),
        ),
        _check("length(evidence_sha256) = 64"),
    ),
)
_TABLE_004_AUDIT = _table(
    "sqag_audit_events",
    (
        _col("event_id"),
        _col("run_id", nullable=True),
        _col("feedback_id", nullable=True),
        _col("session_id", nullable=True),
        _col("workspace_id"),
        _col("actor_tracking_id"),
        _col("actor_key_version"),
        _col("event_type"),
        _col("event_json"),
        _col("event_sha256"),
        _col("created_at"),
        _col("retention_expires_at"),
        _col("original_retention_expires_at"),
        _col("legal_hold", "integer", default_sql="0"),
    ),
    (
        _pk("event_id"),
        _fk(
            ("run_id", "workspace_id"),
            "sqag_generation_runs",
            ("run_id", "workspace_id"),
        ),
        _check("length(event_sha256) = 64"),
    ),
)
_TABLE_004_FEEDBACK = _table(
    "sqag_feedback",
    (
        _col("feedback_id"),
        _col("support_reference"),
        _col("workspace_id"),
        _col("reporter_tracking_id"),
        _col("reporter_key_version"),
        _col("run_id", nullable=True),
        _col("session_id", nullable=True),
        _col("category"),
        _col("title"),
        _col("message"),
        _col("expected_result", nullable=True),
        _col("actual_result", nullable=True),
        _col("reproduction_steps", nullable=True),
        _col("impact", nullable=True),
        _col("link_choice"),
        _col("manual_reference_text", nullable=True),
        _col("manual_reference_status"),
        _col("resolved_reference_type", nullable=True),
        _col("resolved_reference_id", nullable=True),
        _col("publication_version_id", nullable=True),
        _col("link_resolution_source", nullable=True),
        _col("link_resolved_at", nullable=True),
        _col("diagnostic_metadata_json"),
        _col("status"),
        _col("created_at"),
        _col("updated_at"),
        _col("closed_at", nullable=True),
        _col("retention_expires_at"),
        _col("original_retention_expires_at"),
        _col("submission_retention_expires_at"),
        _col("retention_policy_version"),
        _col("legal_hold", "integer", default_sql="0"),
        _col("deletion_state", default_sql="'active'"),
        _col("deletion_error_code", nullable=True),
        _col("deletion_claimed_at", nullable=True),
    ),
    (
        _pk("feedback_id"),
        _unique("support_reference"),
        _unique("feedback_id", "workspace_id"),
        _fk(
            ("run_id", "workspace_id"),
            "sqag_generation_runs",
            ("run_id", "workspace_id"),
        ),
    ),
)
_TABLE_004_HISTORY = _table(
    "sqag_feedback_status_history",
    (
        _col("history_id"),
        _col("feedback_id"),
        _col("workspace_id"),
        _col("from_status", nullable=True),
        _col("to_status"),
        _col("actor_tracking_id"),
        _col("actor_key_version"),
        _col("resolution_note", nullable=True),
        _col("created_at"),
        _col("retention_expires_at"),
        _col("original_retention_expires_at"),
        _col("legal_hold", "integer", default_sql="0"),
    ),
    (
        _pk("history_id"),
        _fk(
            ("feedback_id", "workspace_id"),
            "sqag_feedback",
            ("feedback_id", "workspace_id"),
        ),
    ),
)
_TABLE_004_HOLDS = _table(
    "sqag_legal_holds",
    (
        _col("hold_id"),
        _col("workspace_id"),
        _col("target_type"),
        _col("target_id"),
        _col("enabled", "integer", default_sql="1"),
        _col("reason_code"),
        _col("case_reference", nullable=True),
        _col("actor_tracking_id"),
        _col("actor_key_version"),
        _col("created_at"),
        _col("released_by_tracking_id", nullable=True),
        _col("released_by_key_version", nullable=True),
        _col("released_at", nullable=True),
    ),
    (_pk("hold_id"),),
)
_TABLE_004_AUTH = _table(
    "sqag_retention_delete_authorizations",
    (
        _col("authorization_id"),
        _col("workspace_id"),
        _col("record_type"),
        _col("record_id"),
        _col("created_at"),
    ),
    (
        _pk("authorization_id"),
        _unique("workspace_id", "record_type", "record_id"),
    ),
)
_TABLE_004_RECEIPTS = _table(
    "sqag_deletion_receipts",
    (
        _col("receipt_id"),
        _col("workspace_id"),
        _col("record_type"),
        _col("record_id"),
        _col("reason"),
        _col("deleted_at"),
        _col("original_retention_expires_at"),
        _col("created_at"),
        _col("retention_expires_at"),
    ),
    (
        _pk("receipt_id"),
        _unique("workspace_id", "record_type", "record_id"),
    ),
)
_TABLE_004_CURSORS = _table(
    "sqag_retention_scan_cursors",
    (
        _col("workspace_id"),
        _col("candidate_type"),
        _col("last_retention_expires_at"),
        _col("last_record_id"),
        _col("updated_at"),
    ),
    (_pk("workspace_id", "candidate_type"),),
)

_TABLE_006_VERSIONS = _table(
    "sqag_quote_publication_versions",
    (
        _col("workspace_id"),
        _col("session_id"),
        _col("run_id"),
        _col("job_id", nullable=True),
        _col("state"),
        _col("artifact_storage_mode"),
        _col("artifact_source", default_sql="'version'"),
        _col("metadata_json"),
        _col("error_code", nullable=True),
        _col("created_at"),
        _col("updated_at"),
        _col("promoted_at", nullable=True),
        _col("failed_at", nullable=True),
        _col("retention_expires_at"),
        _col("original_retention_expires_at"),
        _col("legal_hold", "integer", default_sql="0"),
        _col("deletion_state", default_sql="'active'"),
        _col("deletion_error_code", nullable=True),
        _col("deletion_claimed_at", nullable=True),
    ),
    (
        _pk("workspace_id", "run_id"),
        _unique("workspace_id", "session_id", "run_id"),
        _check("state in ('staged','published','superseded','failed')"),
        _check("artifact_storage_mode in ('database','object')"),
        _check("artifact_source in ('version','legacy_current')"),
    ),
)
_TABLE_006_ARTIFACTS = _table(
    "sqag_quote_publication_artifacts",
    (
        _col("workspace_id"),
        _col("session_id"),
        _col("run_id"),
        _col("artifact_kind"),
        _col("filename"),
        _col("content_type"),
        _col("size_bytes", "bigint"),
        _col("checksum_sha256"),
        _col("content_blob", "bytea"),
        _col("created_at"),
        _col("updated_at"),
    ),
    (
        _pk("workspace_id", "run_id", "artifact_kind"),
        _fk(
            ("workspace_id", "run_id"),
            "sqag_quote_publication_versions",
            ("workspace_id", "run_id"),
            on_delete="cascade",
        ),
        _check("length(checksum_sha256) = 64"),
    ),
)

_INDEX_SPECS = (
    _index(
        "sqag_generation_runs_workspace_job_uidx",
        "sqag_generation_runs",
        ("workspace_id", "job_id"),
        unique=True,
        predicate="job_id is not null",
    ),
    _index(
        "sqag_generation_runs_workspace_idempotency_uidx",
        "sqag_generation_runs",
        ("workspace_id", "idempotency_key"),
        unique=True,
        predicate="idempotency_key is not null",
    ),
    _index(
        "sqag_legal_holds_active_target_uidx",
        "sqag_legal_holds",
        ("workspace_id", "target_type", "target_id"),
        unique=True,
        predicate="enabled = 1",
    ),
    _index(
        "sqag_generation_runs_workspace_started_idx",
        "sqag_generation_runs",
        ("workspace_id", "started_at"),
    ),
    _index(
        "sqag_generation_runs_retention_idx",
        "sqag_generation_runs",
        ("workspace_id", "deletion_state", "retention_expires_at", "run_id"),
    ),
    _index(
        "sqag_generation_runs_actor_idx",
        "sqag_generation_runs",
        ("workspace_id", "actor_tracking_id", "started_at"),
    ),
    _index(
        "sqag_generation_evidence_run_idx",
        "sqag_generation_evidence",
        ("workspace_id", "run_id", "created_at"),
    ),
    _index(
        "sqag_generation_evidence_retention_idx",
        "sqag_generation_evidence",
        ("workspace_id", "retention_expires_at"),
    ),
    _index(
        "sqag_audit_events_run_idx",
        "sqag_audit_events",
        ("workspace_id", "run_id", "created_at"),
    ),
    _index(
        "sqag_audit_events_actor_idx",
        "sqag_audit_events",
        ("workspace_id", "actor_tracking_id", "created_at"),
    ),
    _index(
        "sqag_audit_events_feedback_idx",
        "sqag_audit_events",
        ("workspace_id", "feedback_id", "created_at"),
    ),
    _index(
        "sqag_audit_events_retention_idx",
        "sqag_audit_events",
        ("workspace_id", "retention_expires_at", "event_id"),
    ),
    _index(
        "sqag_feedback_workspace_status_idx",
        "sqag_feedback",
        ("workspace_id", "status", "created_at"),
    ),
    _index(
        "sqag_feedback_support_idx",
        "sqag_feedback",
        ("workspace_id", "support_reference"),
    ),
    _index(
        "sqag_feedback_retention_idx",
        "sqag_feedback",
        ("workspace_id", "deletion_state", "retention_expires_at", "feedback_id"),
    ),
    _index(
        "sqag_feedback_history_parent_idx",
        "sqag_feedback_status_history",
        ("workspace_id", "feedback_id", "created_at"),
    ),
    _index(
        "sqag_legal_holds_state_idx",
        "sqag_legal_holds",
        ("workspace_id", "enabled", "target_type", "target_id"),
    ),
    _index(
        "sqag_deletion_receipts_retention_idx",
        "sqag_deletion_receipts",
        ("workspace_id", "retention_expires_at"),
    ),
    _index(
        "sqag_quote_publication_versions_session_idx",
        "sqag_quote_publication_versions",
        ("workspace_id", "session_id", "state", "updated_at", "run_id"),
    ),
    _index(
        "sqag_quote_publication_versions_retention_idx",
        "sqag_quote_publication_versions",
        ("workspace_id", "deletion_state", "retention_expires_at", "run_id"),
    ),
    _index(
        "sqag_quote_publication_artifacts_session_idx",
        "sqag_quote_publication_artifacts",
        ("workspace_id", "session_id", "run_id", "artifact_kind"),
    ),
    _index(
        "sqag_feedback_publication_idx",
        "sqag_feedback",
        ("workspace_id", "publication_version_id", "run_id"),
    ),
)

_TRIGGER_SPECS = (
    _trigger(
        "sqag_generation_evidence_no_update",
        "sqag_generation_evidence",
        "UPDATE",
        "sqag_reject_immutable_change",
    ),
    _trigger(
        "sqag_audit_events_no_update",
        "sqag_audit_events",
        "UPDATE",
        "sqag_reject_immutable_change",
    ),
    _trigger(
        "sqag_generation_evidence_guard_delete",
        "sqag_generation_evidence",
        "DELETE",
        "sqag_require_retention_delete_authorization",
    ),
    _trigger(
        "sqag_audit_events_guard_delete",
        "sqag_audit_events",
        "DELETE",
        "sqag_require_retention_delete_authorization",
    ),
    _trigger(
        "sqag_feedback_linkage_no_update",
        "sqag_feedback",
        "UPDATE",
        "sqag_reject_immutable_change",
        columns=(
            "run_id",
            "session_id",
            "publication_version_id",
            "link_resolution_source",
            "link_resolved_at",
        ),
    ),
)

_ROUTINE_SPECS = (
    _routine(
        "sqag_reject_immutable_change",
        "",
        "trigger",
        "plpgsql",
        "volatile",
        "unsafe",
        False,
        (),
        "004_generation_forensics_feedback_retention_postgres.sql",
    ),
    _routine(
        "sqag_require_retention_delete_authorization",
        "",
        "trigger",
        "plpgsql",
        "volatile",
        "unsafe",
        False,
        (),
        "005_forensic_postgres_delete_guards.sql",
    ),
    _routine(
        "sqag_quote_session_deletion_hold_blocked",
        "text, text",
        "boolean",
        "sql",
        "stable",
        "unsafe",
        True,
        ("search_path=pg_catalog, public",),
        "008_quote_session_deletion_hold_authority_postgres.sql",
        require_runtime_execute=True,
    ),
)

_TABLE_MUTATIONS = (
    TableMutationSpec(
        "sqag_feedback",
        ("publication_version_id", "link_resolution_source", "link_resolved_at"),
    ),
)

_TABLE_OBJECTS = (
    ("001_platform_scoped_storage.sql", _TABLE_001_PROFILES),
    ("001_platform_scoped_storage.sql", _TABLE_001_PRICING),
    ("001_platform_scoped_storage.sql", _TABLE_001_SESSIONS),
    ("003_object_artifact_metadata.sql", _TABLE_003_ARTIFACTS),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_RUNS),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_EVIDENCE),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_AUDIT),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_FEEDBACK),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_HISTORY),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_HOLDS),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_AUTH),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_RECEIPTS),
    ("004_generation_forensics_feedback_retention_postgres.sql", _TABLE_004_CURSORS),
    ("006_quote_publication_versions_postgres.sql", _TABLE_006_VERSIONS),
    ("006_quote_publication_versions_postgres.sql", _TABLE_006_ARTIFACTS),
)

_OBJECTS: list[MigrationObjectSpec] = []
for _migration_id, _table_spec in _TABLE_OBJECTS:
    _OBJECTS.append(
        MigrationObjectSpec(
            _migration_id,
            "table",
            _table_spec.name,
            table_name=_table_spec.name,
            table_spec=_table_spec,
        )
    )
for _index_spec in _INDEX_SPECS:
    _OBJECTS.append(
        MigrationObjectSpec(
            (
                "004_generation_forensics_feedback_retention_postgres.sql"
                if _index_spec.name
                not in {
                    "sqag_quote_publication_versions_session_idx",
                    "sqag_quote_publication_versions_retention_idx",
                    "sqag_quote_publication_artifacts_session_idx",
                    "sqag_feedback_publication_idx",
                }
                else (
                    "006_quote_publication_versions_postgres.sql"
                    if _index_spec.name
                    != "sqag_feedback_publication_idx"
                    else "007_feedback_publication_binding_postgres.sql"
                )
            ),
            "index",
            _index_spec.name,
            table_name=_index_spec.table_name,
            index_spec=_index_spec,
        )
    )
for _trigger_spec in _TRIGGER_SPECS:
    _OBJECTS.append(
        MigrationObjectSpec(
            (
                "007_feedback_publication_binding_postgres.sql"
                if _trigger_spec.name == "sqag_feedback_linkage_no_update"
                else (
                    "005_forensic_postgres_delete_guards.sql"
                    if "guard_delete" in _trigger_spec.name
                    else "004_generation_forensics_feedback_retention_postgres.sql"
                )
            ),
            "trigger",
            _trigger_spec.name,
            table_name=_trigger_spec.table_name,
            trigger_spec=_trigger_spec,
        )
    )
for _routine_spec in _ROUTINE_SPECS:
    _OBJECTS.append(
        MigrationObjectSpec(
            _routine_spec.source_file,
            "routine",
            _routine_spec.name,
            routine_spec=_routine_spec,
        )
    )
for _mutation_spec in _TABLE_MUTATIONS:
    _OBJECTS.append(
        MigrationObjectSpec(
            "007_feedback_publication_binding_postgres.sql",
            "table_mutation",
            f"{_mutation_spec.table_name}:{','.join(_mutation_spec.columns)}",
            table_name=_mutation_spec.table_name,
            mutation_spec=_mutation_spec,
        )
    )

_OBJECT_LOOKUP = {
    (item.migration_id, item.object_kind, item.object_name): item
    for item in _OBJECTS
}
_PROVENANCE_ORDER = (
    ("001_platform_scoped_storage.sql", "table", "sqag_profiles"),
    ("001_platform_scoped_storage.sql", "table", "sqag_pricing_references"),
    ("001_platform_scoped_storage.sql", "table", "sqag_quote_sessions"),
    ("003_object_artifact_metadata.sql", "table", "sqag_object_artifacts"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_generation_runs"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_generation_runs_workspace_job_uidx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_generation_runs_workspace_idempotency_uidx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_generation_evidence"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_audit_events"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_feedback"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_feedback_status_history"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_legal_holds"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_legal_holds_active_target_uidx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_retention_delete_authorizations"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_deletion_receipts"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "table", "sqag_retention_scan_cursors"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_generation_runs_workspace_started_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_generation_runs_retention_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_generation_runs_actor_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_generation_evidence_run_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_generation_evidence_retention_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_audit_events_run_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_audit_events_actor_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_audit_events_feedback_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_audit_events_retention_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_feedback_workspace_status_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_feedback_support_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_feedback_retention_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_feedback_history_parent_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_legal_holds_state_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "index", "sqag_deletion_receipts_retention_idx"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "routine", "sqag_reject_immutable_change"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "trigger", "sqag_generation_evidence_no_update"),
    ("004_generation_forensics_feedback_retention_postgres.sql", "trigger", "sqag_audit_events_no_update"),
    ("005_forensic_postgres_delete_guards.sql", "routine", "sqag_require_retention_delete_authorization"),
    ("005_forensic_postgres_delete_guards.sql", "trigger", "sqag_generation_evidence_guard_delete"),
    ("005_forensic_postgres_delete_guards.sql", "trigger", "sqag_audit_events_guard_delete"),
    ("006_quote_publication_versions_postgres.sql", "table", "sqag_quote_publication_versions"),
    ("006_quote_publication_versions_postgres.sql", "index", "sqag_quote_publication_versions_session_idx"),
    ("006_quote_publication_versions_postgres.sql", "index", "sqag_quote_publication_versions_retention_idx"),
    ("006_quote_publication_versions_postgres.sql", "table", "sqag_quote_publication_artifacts"),
    ("006_quote_publication_versions_postgres.sql", "index", "sqag_quote_publication_artifacts_session_idx"),
    ("007_feedback_publication_binding_postgres.sql", "table_mutation", "sqag_feedback:publication_version_id,link_resolution_source,link_resolved_at"),
    ("007_feedback_publication_binding_postgres.sql", "index", "sqag_feedback_publication_idx"),
    ("007_feedback_publication_binding_postgres.sql", "trigger", "sqag_feedback_linkage_no_update"),
    ("008_quote_session_deletion_hold_authority_postgres.sql", "routine", "sqag_quote_session_deletion_hold_blocked"),
)
if set(_PROVENANCE_ORDER) != set(_OBJECT_LOOKUP):
    raise RuntimeError("SQAG PostgreSQL migration object provenance is incomplete.")
MIGRATION_OBJECTS = tuple(_OBJECT_LOOKUP[key] for key in _PROVENANCE_ORDER)
MIGRATION_OBJECT_PROVENANCE = tuple(
    (item.migration_id, item.object_kind, item.object_name)
    for item in MIGRATION_OBJECTS
)
MIGRATION_TABLES = {
    migration_id: frozenset(
        item.object_name
        for item in MIGRATION_OBJECTS
        if item.migration_id == migration_id and item.object_kind == "table"
    )
    for migration_id in MIGRATION_FILE_NAMES
}
MIGRATION_TABLE_SPECS = {
    item.object_name: item.table_spec
    for item in MIGRATION_OBJECTS
    if item.object_kind == "table" and item.table_spec is not None
}
MIGRATION_INDEX_SPECS = {
    item.object_name: item.index_spec
    for item in MIGRATION_OBJECTS
    if item.object_kind == "index" and item.index_spec is not None
}
MIGRATION_TRIGGER_SPECS = {
    item.object_name: item.trigger_spec
    for item in MIGRATION_OBJECTS
    if item.object_kind == "trigger" and item.trigger_spec is not None
}
MIGRATION_ROUTINE_SPECS = {
    (item.routine_spec.name, item.routine_spec.identity_arguments): item.routine_spec
    for item in MIGRATION_OBJECTS
    if item.object_kind == "routine" and item.routine_spec is not None
}
MIGRATION_OBJECT_RANK = {
    (item.object_kind, item.object_name): position
    for position, item in enumerate(MIGRATION_OBJECTS)
}

if tuple(MIGRATION_TABLES) != MIGRATION_FILE_NAMES:
    raise RuntimeError("SQAG PostgreSQL migration table map must match the ordered manifest.")
if set().union(*MIGRATION_TABLES.values()) != EXPECTED_TABLES:
    raise RuntimeError("SQAG PostgreSQL expected table inventory must match the migration manifest.")
if set(MIGRATION_INDEX_SPECS) != EXPECTED_INDEXES:
    raise RuntimeError("SQAG PostgreSQL expected index inventory must match the migration manifest.")
if set(MIGRATION_TRIGGER_SPECS) != EXPECTED_TRIGGERS:
    raise RuntimeError("SQAG PostgreSQL expected trigger inventory must match the migration manifest.")
if set(name for name, _args in MIGRATION_ROUTINE_SPECS) != EXPECTED_ROUTINES:
    raise RuntimeError("SQAG PostgreSQL expected routine inventory must match the migration manifest.")
if len(MIGRATION_OBJECT_PROVENANCE) != len(set(MIGRATION_OBJECT_PROVENANCE)):
    raise RuntimeError("SQAG PostgreSQL migration object provenance must be unique.")

_LEDGER_SPEC = _table(
    LEDGER_TABLE,
    (
        _col("sequence_no", "integer"),
        _col("migration_id"),
        _col("checksum_sha256", "char(64)"),
        _col("applied_at", "timestamptz", default_sql="current_timestamp"),
    ),
    (
        _pk("migration_id"),
        _unique("sequence_no"),
        _check("sequence_no > 0"),
        _check("checksum_sha256::text ~ '^[0-9a-f]{64}$'"),
    ),
)


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
    except (KeyError, TypeError, IndexError):
        return row[index]


def _int_array(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (tuple, list)):
        return tuple(int(item) for item in value)
    text = str(value).strip().strip("{}")
    if not text:
        return ()
    return tuple(int(item) for item in text.split(",") if item)


def _remove_sql_comments(sql: str) -> str:
    """Remove comments without interpreting comment markers inside strings."""

    result: list[str] = []
    position = 0
    quote: str | None = None
    while position < len(sql):
        char = sql[position]
        if quote:
            result.append(char)
            if char == quote:
                if position + 1 < len(sql) and sql[position + 1] == quote:
                    result.append(sql[position + 1])
                    position += 2
                    continue
                quote = None
            elif char == "\\" and quote == "'" and position + 1 < len(sql):
                result.append(sql[position + 1])
                position += 2
                continue
            position += 1
            continue
        if char in ("'", '"'):
            quote = char
            result.append(char)
            position += 1
            continue
        if sql.startswith("--", position):
            newline = sql.find("\n", position + 2)
            if newline < 0:
                break
            result.append("\n")
            position = newline + 1
            continue
        if sql.startswith("/*", position):
            end = sql.find("*/", position + 2)
            if end < 0:
                break
            result.append(" ")
            position = end + 2
            continue
        result.append(char)
        position += 1
    return "".join(result)


_LITERAL_TEXT_CAST_RE = re.compile(
    r"((?:E)?'(?:''|\\.|[^'])*')\s*::\s*text\b",
    flags=re.IGNORECASE,
)
_ANY_ARRAY_RE = re.compile(
    r"=\s*any\s*\(\s*array\s*\[(.*?)\]\s*(?:::"
    r"[A-Za-z_][A-Za-z0-9_]*(?:\s*\[\])?)?\s*\)",
    flags=re.IGNORECASE | re.DOTALL,
)
_OUTER_PAREN_RE = re.compile(r"^\(\s*(.*)\s*\)$", flags=re.DOTALL)
_ATOM_PAREN_RE = re.compile(
    r"(?<![A-Za-z0-9_$])\(\s*"
    r"((?:E)?'(?:''|\\.|[^'])*'|[A-Za-z_][A-Za-z0-9_]*)\s*\)"
)
_CAST_PAREN_RE = re.compile(
    r"\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*::"
)


def _balanced_outer_parentheses(sql: str) -> bool:
    depth = 0
    quote: str | None = None
    for position, char in enumerate(sql):
        if quote:
            if char == quote:
                if position + 1 < len(sql) and sql[position + 1] == quote:
                    continue
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and position != len(sql) - 1:
                return False
            if depth < 0:
                return False
    return depth == 0


def _normalize_sql_text(sql: str | None) -> str:
    if sql is None:
        return ""
    normalized = _remove_sql_comments(str(sql)).strip()
    normalized = _LITERAL_TEXT_CAST_RE.sub(r"\1", normalized)
    normalized = _ANY_ARRAY_RE.sub(
        lambda match: " IN (" + match.group(1) + ")",
        normalized,
    )
    normalized = _CAST_PAREN_RE.sub(r"\1::", normalized)
    normalized = _ATOM_PAREN_RE.sub(r"\1", normalized)
    normalized = re.sub(r"\bcurrent_timestamp\s*\(\s*\)", "CURRENT_TIMESTAMP", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bnow\s*\(\s*\)", "CURRENT_TIMESTAMP", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    while _balanced_outer_parentheses(normalized):
        match = _OUTER_PAREN_RE.match(normalized)
        if not match:
            break
        normalized = match.group(1).strip()
    return normalized


_SQL_TOKEN_RE = re.compile(
    r"(?:E)?'(?:''|\\.|[^'])*'|"
    r'"(?:\"\"|[^"])*"|'
    r"\$\d+|"
    r"[A-Za-z_][A-Za-z0-9_$]*|"
    r"[0-9]+(?:\.[0-9]+)?|"
    r"<>|<=|>=|:=|=>|::|"
    r"[(),.\[\]]|"
    r"[+\-*/<>=~!@#%^&|?:;]"
)


def _semantic_sql_tokens(sql: str | None) -> tuple[str, ...]:
    normalized = _normalize_sql_text(sql)
    tokens = _SQL_TOKEN_RE.findall(normalized)
    return tuple(
        token if token.startswith(("'", "E'", '"')) else token.lower()
        for token in tokens
    )


def _normalize_type(type_name: Any) -> str:
    value = re.sub(r"\s+", " ", str(type_name or "").strip().lower())
    value = re.sub(r"^character varying\b", "varchar", value)
    value = re.sub(r"^timestamp with time zone\b", "timestamptz", value)
    value = re.sub(r"^timestamp without time zone\b", "timestamp", value)
    value = re.sub(r"^character\(", "char(", value)
    if value == "character":
        value = "char"
    return value


def _normalize_default(default_sql: Any) -> tuple[str, ...]:
    return _semantic_sql_tokens(default_sql)


def _normalize_config(config: Any) -> tuple[str, ...]:
    if config is None:
        return ()
    values = config if isinstance(config, (tuple, list)) else (config,)
    return tuple(sorted(re.sub(r"\s+", " ", str(value).strip().lower()) for value in values))


def _extract_routine_source_body(spec: RoutineSpec) -> str:
    source_path = Path(__file__).resolve().parents[1] / "migrations" / spec.source_file
    try:
        source = canonical_migration_payload(source_path).decode("utf-8")
    except MigrationSafetyError:
        return ""
    pattern = re.compile(
        rf"create\s+(?:or\s+replace\s+)?function\s+(?:public\.)?"
        rf"{re.escape(spec.name)}\s*\([^)]*\).*?\bas\s+"
        rf"(\$[A-Za-z0-9_]*\$)(.*?)\1",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(source)
    return match.group(2) if match else ""


def _expected_routine_body_tokens(spec: RoutineSpec) -> tuple[str, ...]:
    return _semantic_sql_tokens(_extract_routine_source_body(spec))


def _fetch_public_relations(connection: Any) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
select c.oid, c.relname, c.relkind, c.relpersistence, c.relispartition,
       pg_catalog.pg_get_userbyid(c.relowner) as owner
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
order by c.relname
"""
    ).fetchall()
    relations: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(_row_value(row, "relname", 1))
        if not name.startswith(MANAGED_NAMESPACE_PREFIX):
            continue
        relations[name] = {
            "oid": int(_row_value(row, "oid", 0)),
            "name": name,
            "relkind": str(_row_value(row, "relkind", 2) or ""),
            "relpersistence": str(_row_value(row, "relpersistence", 3) or ""),
            "relispartition": bool(_row_value(row, "relispartition", 4)),
            "owner": str(_row_value(row, "owner", 5) or ""),
        }
    return relations


def _fetch_table_catalog(
    connection: Any,
    relations: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
select c.oid as table_oid, c.relname, c.relkind, c.relpersistence,
       c.relispartition, pg_catalog.pg_get_userbyid(c.relowner) as owner,
       a.attnum, a.attname,
       pg_catalog.format_type(a.atttypid, a.atttypmod) as type_name,
       a.attnotnull, a.attidentity, a.attgenerated,
       pg_catalog.pg_get_expr(d.adbin, d.adrelid) as default_sql
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_attribute a
  on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
left join pg_catalog.pg_attrdef d on d.adrelid = c.oid and d.adnum = a.attnum
where n.nspname = 'public' and c.relkind in ('r', 'p')
order by c.relname, a.attnum
"""
    ).fetchall()
    tables: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(_row_value(row, "relname", 1))
        if not name.startswith(MANAGED_NAMESPACE_PREFIX):
            continue
        table = tables.setdefault(
            name,
            {
                "oid": int(_row_value(row, "table_oid", 0)),
                "name": name,
                "relkind": str(_row_value(row, "relkind", 2) or ""),
                "relpersistence": str(_row_value(row, "relpersistence", 3) or ""),
                "relispartition": bool(_row_value(row, "relispartition", 4)),
                "owner": str(_row_value(row, "owner", 5) or ""),
                "columns": [],
                "constraints": [],
            },
        )
        table["columns"].append(
            {
                "attnum": int(_row_value(row, "attnum", 6)),
                "name": str(_row_value(row, "attname", 7)),
                "type_name": _normalize_type(_row_value(row, "type_name", 8)),
                "nullable": not bool(_row_value(row, "attnotnull", 9)),
                "identity": str(_row_value(row, "attidentity", 10) or ""),
                "generated": str(_row_value(row, "attgenerated", 11) or ""),
                "default_sql": _normalize_sql_text(_row_value(row, "default_sql", 12)),
            }
        )
    table_oids = [table["oid"] for table in tables.values()]
    if not table_oids:
        return tables
    constraints = connection.execute(
        """
select con.conrelid, con.contype, con.conkey, con.confrelid,
       rn.nspname as referenced_schema, rc.relname as referenced_table,
       con.confkey, con.confmatchtype, con.confdeltype, con.confupdtype,
       con.convalidated, con.condeferrable, con.condeferred,
       pg_catalog.pg_get_expr(con.conbin, con.conrelid) as expression
from pg_catalog.pg_constraint con
left join pg_catalog.pg_class rc on rc.oid = con.confrelid
left join pg_catalog.pg_namespace rn on rn.oid = rc.relnamespace
where con.conrelid = any(?)
order by con.conrelid, con.oid
""",
        (table_oids,),
    ).fetchall()
    by_oid = {table["oid"]: table for table in tables.values()}
    for row in constraints:
        table = by_oid.get(int(_row_value(row, "conrelid", 0)))
        if table is None:
            continue
        attnames = {item["attnum"]: item["name"] for item in table["columns"]}
        kind = str(_row_value(row, "contype", 1) or "")
        columns = tuple(
            attnames.get(attnum, f"#attnum:{attnum}")
            for attnum in _int_array(_row_value(row, "conkey", 2))
        )
        referenced_columns: tuple[str, ...] = ()
        referenced_oid = _row_value(row, "confrelid", 3)
        referenced_table = _row_value(row, "referenced_table", 5)
        if referenced_oid and referenced_table:
            referenced_columns = tuple(
                str(attnum)
                for attnum in _int_array(_row_value(row, "confkey", 6))
            )
        table["constraints"].append(
            {
                "kind": kind,
                "columns": columns,
                "referenced_schema": str(_row_value(row, "referenced_schema", 4) or ""),
                "referenced_table": str(referenced_table or ""),
                "referenced_oid": int(referenced_oid or 0),
                "referenced_columns_attnums": referenced_columns,
                "match_type": str(_row_value(row, "confmatchtype", 7) or ""),
                "on_delete": str(_row_value(row, "confdeltype", 8) or ""),
                "on_update": str(_row_value(row, "confupdtype", 9) or ""),
                "expression": _normalize_sql_text(_row_value(row, "expression", 13)),
                "validated": bool(_row_value(row, "convalidated", 10)),
                "deferrable": bool(_row_value(row, "condeferrable", 11)),
                "initially_deferred": bool(_row_value(row, "condeferred", 12)),
            }
        )
    for table in tables.values():
        for constraint in table["constraints"]:
            if constraint["kind"] == "f" and constraint["referenced_columns_attnums"]:
                referenced_table = by_oid.get(
                    int(constraint.get("referenced_oid") or 0)
                )
                referenced_attnames = (
                    {
                        item["attnum"]: item["name"]
                        for item in referenced_table["columns"]
                    }
                    if referenced_table is not None
                    else {}
                )
                constraint["referenced_columns"] = tuple(
                    referenced_attnames.get(int(item), f"#attnum:{item}")
                    for item in constraint["referenced_columns_attnums"]
                )
            else:
                constraint["referenced_columns"] = ()
            constraint.pop("referenced_columns_attnums", None)
            constraint.pop("referenced_oid", None)
    return tables


def _fetch_index_catalog(connection: Any) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
select i.indexrelid, idx.relname as index_name, tbl.relname as table_name,
       i.indisunique, i.indisvalid, i.indisready, i.indconstraint,
       pg_catalog.pg_get_userbyid(idx.relowner) as owner,
       pg_catalog.pg_get_expr(i.indpred, i.indrelid) as predicate,
       coalesce(array_agg(pg_catalog.pg_get_indexdef(i.indexrelid, keys.n, true)
                          order by keys.n), '{}') as key_definitions
from pg_catalog.pg_index i
join pg_catalog.pg_class idx on idx.oid = i.indexrelid
join pg_catalog.pg_class tbl on tbl.oid = i.indrelid
join pg_catalog.pg_namespace n on n.oid = idx.relnamespace
left join lateral pg_catalog.generate_series(1, i.indnkeyatts) keys(n) on true
where n.nspname = 'public'
group by i.indexrelid, idx.relname, tbl.relname, i.indisunique,
         i.indisvalid, i.indisready, i.indconstraint, idx.relowner,
         i.indpred, i.indrelid
order by idx.relname
"""
    ).fetchall()
    indexes: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(_row_value(row, "index_name", 1))
        if not name.startswith(MANAGED_NAMESPACE_PREFIX):
            continue
        definitions = _row_value(row, "key_definitions", 9) or ()
        indexes[name] = {
            "oid": int(_row_value(row, "indexrelid", 0)),
            "name": name,
            "table_name": str(_row_value(row, "table_name", 2)),
            "unique": bool(_row_value(row, "indisunique", 3)),
            "valid": bool(_row_value(row, "indisvalid", 4)),
            "ready": bool(_row_value(row, "indisready", 5)),
            "constraint_oid": int(_row_value(row, "indconstraint", 6) or 0),
            "owner": str(_row_value(row, "owner", 7) or ""),
            "predicate": _normalize_sql_text(_row_value(row, "predicate", 8)),
            "columns": tuple(_normalize_sql_text(item) for item in definitions),
        }
    return indexes


def _fetch_trigger_catalog(connection: Any) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
select t.oid, t.tgname, c.relname as table_name, t.tgenabled,
       (t.tgtype & 2) <> 0 as is_before,
       (t.tgtype & 64) <> 0 as is_instead,
       (t.tgtype & 1) <> 0 as row_level,
       (t.tgtype & 4) <> 0 as is_insert,
       (t.tgtype & 8) <> 0 as is_delete,
       (t.tgtype & 16) <> 0 as is_update,
       array(
         select a.attname
         from pg_catalog.pg_attribute a
         where a.attrelid = t.tgrelid
           and a.attnum = any(t.tgattr::smallint[])
         order by pg_catalog.array_position(t.tgattr::smallint[], a.attnum)
       ) as update_columns,
       p.proname as function_name,
       pg_catalog.pg_get_function_identity_arguments(p.oid) as function_identity_arguments
from pg_catalog.pg_trigger t
join pg_catalog.pg_class c on c.oid = t.tgrelid
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_proc p on p.oid = t.tgfoid
where not t.tgisinternal and n.nspname = 'public'
order by t.tgname
"""
    ).fetchall()
    triggers: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(_row_value(row, "tgname", 1))
        if not name.startswith(MANAGED_NAMESPACE_PREFIX):
            continue
        event = (
            "INSERT"
            if _row_value(row, "is_insert", 7)
            else "DELETE"
            if _row_value(row, "is_delete", 8)
            else "UPDATE"
            if _row_value(row, "is_update", 9)
            else "UNKNOWN"
        )
        timing = (
            "INSTEAD"
            if _row_value(row, "is_instead", 5)
            else "BEFORE"
            if _row_value(row, "is_before", 4)
            else "AFTER"
        )
        columns = _row_value(row, "update_columns", 10) or ()
        triggers[name] = {
            "oid": int(_row_value(row, "oid", 0)),
            "name": name,
            "table_name": str(_row_value(row, "table_name", 2)),
            "timing": timing,
            "event": event,
            "columns": tuple(str(item) for item in columns),
            "row_level": bool(_row_value(row, "row_level", 6)),
            "function_name": str(_row_value(row, "function_name", 11)),
            "function_identity_arguments": str(
                _row_value(row, "function_identity_arguments", 12) or ""
            ),
            "enabled": str(_row_value(row, "tgenabled", 3) or ""),
        }
    return triggers


def _fetch_routine_catalog(connection: Any) -> dict[tuple[str, str], dict[str, Any]]:
    rows = connection.execute(
        """
select p.oid, p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid)
       as identity_arguments,
       pg_catalog.pg_get_function_result(p.oid) as return_type,
       l.lanname as language, p.provolatile, p.proparallel, p.prosecdef,
       p.proconfig, p.prosrc, pg_catalog.pg_get_userbyid(p.proowner) as owner,
       p.prokind, p.proleakproof
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
join pg_catalog.pg_language l on l.oid = p.prolang
where n.nspname = 'public'
order by p.proname, identity_arguments
"""
    ).fetchall()
    routines: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        name = str(_row_value(row, "proname", 1))
        if not name.startswith(MANAGED_NAMESPACE_PREFIX):
            continue
        identity_arguments = str(_row_value(row, "identity_arguments", 2) or "")
        routines[(name, identity_arguments)] = {
            "oid": int(_row_value(row, "oid", 0)),
            "name": name,
            "identity_arguments": identity_arguments,
            "return_type": _normalize_type(_row_value(row, "return_type", 3)),
            "language": str(_row_value(row, "language", 4) or "").lower(),
            "volatility": {
                "i": "immutable",
                "s": "stable",
                "v": "volatile",
            }.get(str(_row_value(row, "provolatile", 5) or ""), ""),
            "parallel": {
                "s": "safe",
                "r": "restricted",
                "u": "unsafe",
            }.get(str(_row_value(row, "proparallel", 6) or ""), ""),
            "security_definer": bool(_row_value(row, "prosecdef", 7)),
            "config": _normalize_config(_row_value(row, "proconfig", 8)),
            "body": str(_row_value(row, "prosrc", 9) or ""),
            "owner": str(_row_value(row, "owner", 10) or ""),
            "prokind": str(_row_value(row, "prokind", 11) or ""),
            "leakproof": bool(_row_value(row, "proleakproof", 12)),
            "acl": [],
        }
    if not routines:
        return routines
    routine_oids = [item["oid"] for item in routines.values()]
    acl_rows = connection.execute(
        """
select p.oid,
       case when acl.grantee = 0 then 'PUBLIC'
            else pg_catalog.pg_get_userbyid(acl.grantee) end as grantee,
       pg_catalog.pg_get_userbyid(acl.grantor) as grantor,
       acl.privilege_type, acl.is_grantable
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
cross join lateral pg_catalog.aclexplode(
  coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
) acl
where n.nspname = 'public' and p.oid = any(?)
order by p.oid, grantee, acl.privilege_type
""",
        (routine_oids,),
    ).fetchall()
    by_oid = {item["oid"]: item for item in routines.values()}
    for row in acl_rows:
        routine = by_oid.get(int(_row_value(row, "oid", 0)))
        if routine is not None:
            routine["acl"].append(
                {
                    "grantee": str(_row_value(row, "grantee", 1) or ""),
                    "grantor": str(_row_value(row, "grantor", 2) or ""),
                    "privilege_type": str(_row_value(row, "privilege_type", 3) or ""),
                    "is_grantable": bool(_row_value(row, "is_grantable", 4)),
                }
            )
    return routines


def _column_types_for(table: TableSpec | dict[str, Any]) -> dict[str, str]:
    if isinstance(table, TableSpec):
        return {column.name: _normalize_type(column.type_name) for column in table.columns}
    return {
        str(column.get("name")): _normalize_type(column.get("type_name"))
        for column in table.get("columns", ())
        if isinstance(column, dict)
    }


def _normalize_constraint_expression(
    expression: str | None,
    table: TableSpec | dict[str, Any] | None = None,
) -> str:
    normalized = _normalize_sql_text(expression)
    if table is not None:
        text_columns = {
            name for name, type_name in _column_types_for(table).items() if type_name == "text"
        }
        for column_name in sorted(text_columns, key=len, reverse=True):
            normalized = re.sub(
                rf"\b{re.escape(column_name)}\b\s*::\s*text\b",
                column_name,
                normalized,
                flags=re.IGNORECASE,
            )
    return normalized


_CONSTRAINT_MATCH_TYPES = {
    "f": "full",
    "p": "partial",
    "s": "simple",
}
_CONSTRAINT_ACTIONS = {
    "a": "no_action",
    "c": "cascade",
    "d": "set_default",
    "n": "set_null",
    "r": "restrict",
}


def _constraint_fingerprint(
    constraint: ConstraintSpec,
    table: TableSpec | dict[str, Any] | None = None,
) -> tuple[Any, ...]:
    """Return only the semantic identity required for this constraint kind."""

    common = (
        constraint.kind,
        bool(constraint.validated),
        bool(constraint.deferrable),
        bool(constraint.initially_deferred),
    )
    if constraint.kind in {"p", "u"}:
        return (
            constraint.kind,
            tuple(constraint.columns),
            *common[1:],
        )
    if constraint.kind == "f":
        return (
            constraint.kind,
            tuple(constraint.columns),
            constraint.referenced_table or "",
            tuple(constraint.referenced_columns),
            constraint.match_type or "simple",
            constraint.on_delete or "no_action",
            constraint.on_update or "no_action",
            *common[1:],
        )
    if constraint.kind == "c":
        return (
            constraint.kind,
            _semantic_sql_tokens(_normalize_constraint_expression(constraint.expression, table)),
            *common[1:],
        )
    return (constraint.kind, *common[1:])


def _observed_constraint_fingerprint(
    constraint: dict[str, Any],
    table: dict[str, Any] | None = None,
) -> tuple[Any, ...]:
    kind = str(constraint.get("kind") or "")
    common = (
        bool(constraint.get("validated")),
        bool(constraint.get("deferrable")),
        bool(constraint.get("initially_deferred")),
    )
    if kind in {"p", "u"}:
        return (kind, tuple(constraint.get("columns") or ()), *common)
    if kind == "f":
        schema = str(constraint.get("referenced_schema") or "")
        referenced_name = str(constraint.get("referenced_table") or "")
        if schema and schema != "public":
            referenced_name = f"{schema}.{referenced_name}"
        return (
            kind,
            tuple(constraint.get("columns") or ()),
            referenced_name,
            tuple(constraint.get("referenced_columns") or ()),
            _CONSTRAINT_MATCH_TYPES.get(
                str(constraint.get("match_type") or ""),
                str(constraint.get("match_type") or ""),
            ),
            _CONSTRAINT_ACTIONS.get(
                str(constraint.get("on_delete") or ""),
                str(constraint.get("on_delete") or ""),
            ),
            _CONSTRAINT_ACTIONS.get(
                str(constraint.get("on_update") or ""),
                str(constraint.get("on_update") or ""),
            ),
            *common,
        )
    if kind == "c":
        return (
            kind,
            _semantic_sql_tokens(
                _normalize_constraint_expression(constraint.get("expression"), table)
            ),
            *common,
        )
    return (kind, *common)


def _column_fingerprint(column: ColumnSpec) -> tuple[Any, ...]:
    return (
        column.name,
        _normalize_type(column.type_name),
        bool(column.nullable),
        _normalize_default(column.default_sql),
        column.identity,
        column.generated,
    )


def _observed_column_fingerprint(column: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(column.get("name") or ""),
        _normalize_type(column.get("type_name")),
        bool(column.get("nullable")),
        _normalize_default(column.get("default_sql")),
        str(column.get("identity") or ""),
        str(column.get("generated") or ""),
    )


def _table_matches(table: dict[str, Any] | None, expected: TableSpec) -> bool:
    if not table:
        return False
    if (
        str(table.get("name") or "") != expected.name
        or str(table.get("relkind") or "") != "r"
        or str(table.get("relpersistence") or "") != "p"
        or bool(table.get("relispartition"))
        or str(table.get("owner") or "") != "sqag_migrator"
    ):
        return False
    observed_columns = tuple(
        _observed_column_fingerprint(column)
        for column in table.get("columns", ())
        if isinstance(column, dict)
    )
    expected_columns = tuple(_column_fingerprint(column) for column in expected.columns)
    if observed_columns != expected_columns:
        return False
    expected_constraints = Counter(
        _constraint_fingerprint(constraint, expected)
        for constraint in expected.constraints
    )
    observed_constraints = Counter(
        _observed_constraint_fingerprint(constraint, table)
        for constraint in table.get("constraints", ())
        if isinstance(constraint, dict)
    )
    return observed_constraints == expected_constraints


def _index_matches(index: dict[str, Any] | None, expected: IndexSpec) -> bool:
    if not index:
        return False
    return (
        str(index.get("name") or "") == expected.name
        and str(index.get("table_name") or "") == expected.table_name
        and bool(index.get("unique")) == expected.unique
        and bool(index.get("valid"))
        and bool(index.get("ready"))
        and str(index.get("owner") or "") == "sqag_migrator"
        and tuple(
            _semantic_sql_tokens(item)
            for item in index.get("columns", ())
        )
        == tuple(_semantic_sql_tokens(item) for item in expected.columns)
        and _semantic_sql_tokens(index.get("predicate"))
        == _semantic_sql_tokens(expected.predicate)
    )


def _trigger_matches(trigger: dict[str, Any] | None, expected: TriggerSpec) -> bool:
    if not trigger:
        return False
    return (
        str(trigger.get("name") or "") == expected.name
        and str(trigger.get("table_name") or "") == expected.table_name
        and str(trigger.get("timing") or "") == expected.timing
        and str(trigger.get("event") or "") == expected.event
        and tuple(trigger.get("columns") or ()) == expected.columns
        and bool(trigger.get("row_level")) == expected.row_level
        and str(trigger.get("function_name") or "") == expected.function_name
        and str(trigger.get("function_identity_arguments") or "")
        == expected.function_identity_arguments
        and str(trigger.get("enabled") or "") == expected.enabled
    )


def _routine_acl_matches(routine: dict[str, Any], expected: RoutineSpec) -> bool:
    if not expected.require_runtime_execute:
        return True
    allowed: list[tuple[str, str, str, bool]] = []
    for row in routine.get("acl", ()):
        allowed.append(
            (
                str(row.get("grantee") or ""),
                str(row.get("grantor") or ""),
                str(row.get("privilege_type") or "").upper(),
                bool(row.get("is_grantable")),
            )
        )
    if not allowed:
        return False
    owner_rows = 0
    runtime_rows = 0
    for grantee, grantor, privilege, grantable in allowed:
        if grantee == expected.owner:
            if privilege != "EXECUTE":
                return False
            owner_rows += 1
            continue
        if (
            grantee != "sqag_runtime"
            or privilege != "EXECUTE"
            or grantable
        ):
            return False
        runtime_rows += 1
    return owner_rows == 1 and runtime_rows == 1


def _routine_matches(
    routine: dict[str, Any] | None,
    expected: RoutineSpec,
) -> bool:
    if not routine:
        return False
    return (
        str(routine.get("name") or "") == expected.name
        and str(routine.get("identity_arguments") or "") == expected.identity_arguments
        and _normalize_type(routine.get("return_type")) == _normalize_type(expected.return_type)
        and str(routine.get("language") or "") == expected.language
        and str(routine.get("volatility") or "") == expected.volatility
        and str(routine.get("parallel") or "") == expected.parallel
        and bool(routine.get("security_definer")) == expected.security_definer
        and _normalize_config(routine.get("config")) == _normalize_config(expected.config)
        and str(routine.get("owner") or "") == expected.owner
        and str(routine.get("prokind") or "") == "f"
        and bool(routine.get("leakproof")) == expected.leakproof
        and _semantic_sql_tokens(routine.get("body"))
        == _expected_routine_body_tokens(expected)
        and _routine_acl_matches(routine, expected)
    )


def _fetch_public_tables(connection: Any) -> set[str]:
    relations = _fetch_public_relations(connection)
    return {
        name
        for name, relation in relations.items()
        if name != LEDGER_TABLE and relation["relkind"] in {"r", "p"}
    }


def _fetch_public_indexes(connection: Any) -> set[str]:
    return {
        name
        for name, index in _fetch_index_catalog(connection).items()
        if not index.get("constraint_oid")
    }


def _fetch_public_triggers(connection: Any) -> set[str]:
    return set(_fetch_trigger_catalog(connection))


def _fetch_public_routines(connection: Any) -> set[str]:
    return {name for name, _identity in _fetch_routine_catalog(connection)}


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
            "sequence_no": _row_value(row, "sequence_no", 0),
            "migration_id": _row_value(row, "migration_id", 1),
            "checksum_sha256": _row_value(row, "checksum_sha256", 2),
            "applied_at": _row_value(row, "applied_at", 3),
        }
        for row in rows
    ]


def _object_blocker_sort_key(blocker: str) -> tuple[Any, ...]:
    parts = blocker.split(":")
    family = parts[0]
    family_rank = {
        "ledger_schema_invalid": 0,
        "checksum_drift": 1,
        "unknown_or_out_of_order_migration": 2,
        "unexpected_applied_migration": 3,
        "existing_schema_without_trusted_ledger": 4,
        "managed_namespace_extra": 5,
        "applied_prefix_missing": 6,
        "applied_prefix_drift": 7,
        "pending_suffix_present": 8,
    }.get(family, 99)
    return (family_rank, *parts)


def _dedupe_sorted_blockers(blockers: Sequence[str]) -> list[str]:
    return sorted(set(str(item) for item in blockers), key=_object_blocker_sort_key)


def _object_specs_for_migration_ids(
    migration_ids: set[str],
    *,
    object_kinds: set[str] | None = None,
) -> tuple[MigrationObjectSpec, ...]:
    return tuple(
        item
        for item in MIGRATION_OBJECTS
        if item.migration_id in migration_ids
        and (object_kinds is None or item.object_kind in object_kinds)
    )


def _managed_namespace_has_objects(
    relations: dict[str, dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    indexes: dict[str, dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
    routines: dict[tuple[str, str], dict[str, Any]],
) -> bool:
    return bool(
        any(name != LEDGER_TABLE for name in relations)
        or any(name != LEDGER_TABLE for name in tables)
        or any(
            item.get("constraint_oid") == 0
            for item in indexes.values()
        )
        or triggers
        or routines
    )


def _schema_object_blockers(
    migrations: Sequence[Migration],
    applied_ids: Sequence[str],
    relations: dict[str, dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    indexes: dict[str, dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
    routines: dict[tuple[str, str], dict[str, Any]],
) -> list[str]:
    """Compare the applied prefix and pending suffix against catalog semantics."""

    expected_ids = {migration.migration_id for migration in migrations}
    applied_id_set = set(applied_ids)
    pending_id_set = expected_ids - applied_id_set
    blockers: list[str] = []

    expected_table_names = set(MIGRATION_TABLE_SPECS)
    for name, relation in relations.items():
        if name == LEDGER_TABLE or relation.get("relkind") in {"i", "I"}:
            continue
        if name not in expected_table_names:
            blockers.append(f"managed_namespace_extra:relation:{name}")

    for name, index in indexes.items():
        if index.get("constraint_oid"):
            continue
        if name not in MIGRATION_INDEX_SPECS:
            blockers.append(f"managed_namespace_extra:index:{name}")

    for name in triggers:
        if name not in MIGRATION_TRIGGER_SPECS:
            blockers.append(f"managed_namespace_extra:trigger:{name}")

    expected_routine_keys = set(MIGRATION_ROUTINE_SPECS)
    for key in routines:
        if key not in expected_routine_keys:
            blockers.append(
                f"managed_namespace_extra:routine:{key[0]}({key[1]})"
            )

    applied_specs = _object_specs_for_migration_ids(applied_id_set)
    pending_specs = _object_specs_for_migration_ids(pending_id_set)
    for item in applied_specs:
        if item.object_kind == "table" and item.table_spec is not None:
            actual = tables.get(item.object_name)
            if actual is None:
                blockers.append(
                    f"applied_prefix_missing:table:{item.object_name}"
                )
            elif not _table_matches(actual, item.table_spec):
                blockers.append(
                    f"applied_prefix_drift:table:{item.object_name}"
                )
        elif item.object_kind == "index" and item.index_spec is not None:
            actual = indexes.get(item.object_name)
            if actual is None or actual.get("constraint_oid"):
                blockers.append(
                    f"applied_prefix_missing:index:{item.object_name}"
                )
            elif not _index_matches(actual, item.index_spec):
                blockers.append(
                    f"applied_prefix_drift:index:{item.object_name}"
                )
        elif item.object_kind == "trigger" and item.trigger_spec is not None:
            actual = triggers.get(item.object_name)
            if actual is None:
                blockers.append(
                    f"applied_prefix_missing:trigger:{item.object_name}"
                )
            elif not _trigger_matches(actual, item.trigger_spec):
                blockers.append(
                    f"applied_prefix_drift:trigger:{item.object_name}"
                )
        elif item.object_kind == "routine" and item.routine_spec is not None:
            key = (item.routine_spec.name, item.routine_spec.identity_arguments)
            actual = routines.get(key)
            if actual is None:
                blockers.append(
                    f"applied_prefix_missing:routine:{item.object_name}"
                )
            elif not _routine_matches(actual, item.routine_spec):
                blockers.append(
                    f"applied_prefix_drift:routine:{item.object_name}"
                )
        elif item.object_kind == "table_mutation" and item.mutation_spec is not None:
            table = tables.get(item.mutation_spec.table_name)
            present_columns = {
                str(column.get("name"))
                for column in (table or {}).get("columns", ())
            }
            for column in item.mutation_spec.columns:
                if column not in present_columns:
                    blockers.append(
                        f"applied_prefix_drift:table_mutation:"
                        f"{item.mutation_spec.table_name}:{column}"
                    )

    for item in pending_specs:
        if item.object_kind == "table" and item.object_name in relations:
            blockers.append(
                f"pending_suffix_present:table:{item.object_name}"
            )
        elif item.object_kind == "index":
            actual = indexes.get(item.object_name)
            if actual is not None and not actual.get("constraint_oid"):
                blockers.append(
                    f"pending_suffix_present:index:{item.object_name}"
                )
        elif item.object_kind == "trigger" and item.object_name in triggers:
            blockers.append(
                f"pending_suffix_present:trigger:{item.object_name}"
            )
        elif item.object_kind == "routine" and item.routine_spec is not None:
            key = (item.routine_spec.name, item.routine_spec.identity_arguments)
            if key in routines:
                blockers.append(
                    f"pending_suffix_present:routine:{item.object_name}"
                )

    return _dedupe_sorted_blockers(blockers)


def _valid_ledger_table(table: dict[str, Any] | None) -> bool:
    return _table_matches(table, _LEDGER_SPEC)


def _ledger_entry_blockers(
    rows: Sequence[dict[str, Any]],
    migrations: Sequence[Migration],
) -> list[str]:
    if len(rows) > len(migrations):
        return ["unexpected_applied_migration"]
    blockers: list[str] = []
    for position, row in enumerate(rows):
        expected = migrations[position]
        if (
            row.get("sequence_no") != expected.sequence_no
            or str(row.get("migration_id") or "") != expected.migration_id
        ):
            blockers.append("unknown_or_out_of_order_migration")
            break
        if str(row.get("checksum_sha256") or "") != expected.checksum_sha256:
            blockers.append(f"checksum_drift:{expected.migration_id}")
    return _dedupe_sorted_blockers(blockers)


def inspect_postgres_migrations(
    connection: Any,
    migrations: Sequence[Migration],
) -> dict[str, Any]:
    """Inspect the ledger and every managed object without making mutations."""

    expected_ids = [migration.migration_id for migration in migrations]
    expected_head = expected_ids[-1] if expected_ids else None
    relations = _fetch_public_relations(connection)
    tables = _fetch_table_catalog(connection, relations)
    indexes = _fetch_index_catalog(connection)
    triggers = _fetch_trigger_catalog(connection)
    routines = _fetch_routine_catalog(connection)
    ledger_relation = relations.get(LEDGER_TABLE)
    ledger_state = "missing" if ledger_relation is None else "present"

    if ledger_relation is not None and not _valid_ledger_table(tables.get(LEDGER_TABLE)):
        return {
            "status": "unsafe",
            "safeToApply": False,
            "ledgerState": "invalid",
            "expectedHead": None,
            "appliedHead": None,
            "appliedMigrationIds": None,
            "pendingMigrationIds": None,
            "blockers": ["ledger_schema_invalid"],
        }

    if ledger_relation is None:
        if _managed_namespace_has_objects(relations, tables, indexes, triggers, routines):
            blockers = ["existing_schema_without_trusted_ledger"]
            return {
                "status": "unsafe",
                "safeToApply": False,
                "ledgerState": ledger_state,
                "expectedHead": expected_head,
                "appliedHead": None,
                "appliedMigrationIds": [],
                "pendingMigrationIds": [],
                "blockers": blockers,
            }
        return {
            "status": "ready",
            "safeToApply": True,
            "ledgerState": "missing",
            "expectedHead": expected_head,
            "appliedHead": None,
            "appliedMigrationIds": [],
            "pendingMigrationIds": expected_ids,
            "blockers": [],
        }

    try:
        rows = _ledger_rows(connection)
    except Exception:
        return {
            "status": "unsafe",
            "safeToApply": False,
            "ledgerState": "invalid",
            "expectedHead": None,
            "appliedHead": None,
            "appliedMigrationIds": None,
            "pendingMigrationIds": None,
            "blockers": ["ledger_schema_invalid"],
        }
    ledger_blockers = _ledger_entry_blockers(rows, migrations)
    if ledger_blockers:
        return {
            "status": "unsafe",
            "safeToApply": False,
            "ledgerState": "present",
            "expectedHead": None,
            "appliedHead": None,
            "appliedMigrationIds": None,
            "pendingMigrationIds": None,
            "blockers": ledger_blockers,
        }

    applied_ids = [str(row["migration_id"]) for row in rows]
    blockers = _schema_object_blockers(
        migrations,
        applied_ids,
        relations,
        tables,
        indexes,
        triggers,
        routines,
    )
    return {
        "status": "unsafe" if blockers else "ready",
        "safeToApply": not blockers,
        "ledgerState": "present",
        "expectedHead": expected_head,
        "appliedHead": applied_ids[-1] if applied_ids else None,
        "appliedMigrationIds": applied_ids,
        "pendingMigrationIds": expected_ids[len(applied_ids):],
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
    connection.execute(
        "select pg_catalog.pg_advisory_xact_lock(?)",
        (MIGRATION_LOCK_KEY,),
    )
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
            raise MigrationSafetyError(
                f"migration_source_changed_during_run:{migration.migration_id}"
            )
        execute_migration_sql(connection, payload.decode("utf-8"))
        connection.execute(
            "insert into public.sqag_schema_migrations "
            "(sequence_no, migration_id, checksum_sha256) values (?, ?, ?)",
            (
                migration.sequence_no,
                migration.migration_id,
                migration.checksum_sha256,
            ),
        )
        applied_now.append(migration.migration_id)

    after = inspect_postgres_migrations(connection, migrations)
    if not after["safeToApply"] or after["pendingMigrationIds"]:
        blocker = (
            after["blockers"][0]
            if after["blockers"]
            else "migration_head_not_reached"
        )
        raise MigrationSafetyError(str(blocker))

    return {
        "expectedHead": migrations[-1].migration_id if migrations else None,
        "appliedNow": applied_now,
        "alreadyApplied": [
            migration.migration_id for migration in migrations[:applied_count]
        ],
    }
