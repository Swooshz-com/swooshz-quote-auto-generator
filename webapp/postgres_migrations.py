"""Auditable, fail-closed PostgreSQL migrations for SQAG.

The application never runs these migrations implicitly.  The operator CLI
binds to the dedicated migration role and this module owns the immutable
manifest, migration-object provenance, and read-only catalogue evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from types import MappingProxyType
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
    """Raised when the ledger or schema state makes migration unsafe."""

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
    nullable: bool = False
    default_sql: str | None = None
    identity: str = ""
    generated: str = ""


@dataclass(frozen=True)
class ConstraintSpec:
    kind: str
    columns: tuple[str, ...]
    referenced_table: str | None = None
    referenced_columns: tuple[str, ...] = ()
    on_delete: str = "a"
    on_update: str = "a"
    expression: str | None = None
    validated: bool = True
    deferrable: bool = False
    deferred: bool = False


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    constraints: tuple[ConstraintSpec, ...] = ()
    owner: str = "sqag_migrator"


@dataclass(frozen=True)
class IndexSpec:
    name: str
    table_name: str
    unique: bool
    key_semantics: tuple[str, ...]
    predicate: str | None = None
    owner: str = "sqag_migrator"


@dataclass(frozen=True)
class TriggerSpec:
    name: str
    table_name: str
    timing: str
    events: tuple[str, ...]
    columns: tuple[str, ...]
    level: str
    enabled: str
    routine_key: tuple[str, str, str]


@dataclass(frozen=True)
class RoutineSpec:
    schema_name: str
    name: str
    identity_arguments: str
    result_type: str
    language: str
    owner: str
    security_definer: bool
    volatility: str
    parallel: str
    leakproof: bool
    proconfig: tuple[str, ...]
    source_migration_id: str
    referenced_relations: tuple[str, ...] = ()
    direct_acl: tuple[tuple[str, str, bool], ...] = ()

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.schema_name, self.name, self.identity_arguments)


@dataclass(frozen=True)
class TableMutationSpec:
    table_name: str
    added_columns: tuple[str, ...]


@dataclass(frozen=True)
class MigrationObjectSpec:
    migration_id: str
    tables: tuple[TableSpec, ...] = ()
    indexes: tuple[IndexSpec, ...] = ()
    triggers: tuple[TriggerSpec, ...] = ()
    routines: tuple[RoutineSpec, ...] = ()
    table_mutations: tuple[TableMutationSpec, ...] = ()


def _column(
    name: str,
    type_name: str,
    *,
    nullable: bool = False,
    default_sql: str | None = None,
    identity: str = "",
    generated: str = "",
) -> ColumnSpec:
    return ColumnSpec(name, type_name, nullable, default_sql, identity, generated)


def _constraint(
    kind: str,
    columns: tuple[str, ...],
    *,
    referenced_table: str | None = None,
    referenced_columns: tuple[str, ...] = (),
    on_delete: str = "a",
    on_update: str = "a",
    expression: str | None = None,
) -> ConstraintSpec:
    return ConstraintSpec(
        kind=kind,
        columns=columns,
        referenced_table=referenced_table,
        referenced_columns=referenced_columns,
        on_delete=on_delete,
        on_update=on_update,
        expression=expression,
    )


def _index(
    name: str,
    table_name: str,
    key_semantics: tuple[str, ...],
    *,
    unique: bool = False,
    predicate: str | None = None,
) -> IndexSpec:
    return IndexSpec(name, table_name, unique, key_semantics, predicate)


def _trigger(
    name: str,
    table_name: str,
    events: tuple[str, ...],
    *,
    columns: tuple[str, ...] = (),
    routine_name: str,
) -> TriggerSpec:
    return TriggerSpec(
        name=name,
        table_name=table_name,
        timing="before",
        events=events,
        columns=columns,
        level="row",
        enabled="O",
        routine_key=("public", routine_name, ""),
    )


def _trigger_routine(name: str, migration_id: str) -> RoutineSpec:
    return RoutineSpec(
        schema_name="public",
        name=name,
        identity_arguments="",
        result_type="trigger",
        language="plpgsql",
        owner="sqag_migrator",
        security_definer=False,
        volatility="v",
        parallel="u",
        leakproof=False,
        proconfig=(),
        source_migration_id=migration_id,
    )


_STATUS_VALUES = (
    "'received', 'queued', 'running', 'blocked', 'completed', 'needs_confirmation', "
    "'needs_review', 'completed_with_review_required', 'degraded', 'failed', 'cancelled', "
    "'timed_out', 'abandoned', 'superseded'"
)


# Canonical table contracts.  The fields reasserted by 007 are modelled as a
# prefix mutation as well as in the final table shape because the migration is
# additive for older databases even though the current 004 source is
# idempotently compatible with those fields.
TABLE_SPECS = (
    TableSpec(
        "sqag_profiles",
        (
            _column("workspace_id", "text"),
            _column("profile_id", "text"),
            _column("payload_json", "text"),
            _column("created_at", "text"),
            _column("updated_at", "text"),
        ),
        (_constraint("p", ("workspace_id", "profile_id")),),
    ),
    TableSpec(
        "sqag_pricing_references",
        (
            _column("workspace_id", "text"),
            _column("reference_id", "text"),
            _column("payload_json", "text"),
            _column("created_at", "text"),
            _column("updated_at", "text"),
        ),
        (_constraint("p", ("workspace_id", "reference_id")),),
    ),
    TableSpec(
        "sqag_quote_sessions",
        (
            _column("workspace_id", "text"),
            _column("session_id", "text"),
            _column("metadata_json", "text"),
            _column("draft_files_json", "text", default_sql="'[]'"),
            _column("created_at", "text"),
            _column("updated_at", "text"),
        ),
        (_constraint("p", ("workspace_id", "session_id")),),
    ),
    TableSpec(
        "sqag_object_artifacts",
        (
            _column("artifact_id", "text"),
            _column("workspace_id", "text"),
            _column("owner_type", "text"),
            _column("owner_id", "text"),
            _column("platform_user_id", "text", nullable=True),
            _column("session_id", "text", nullable=True),
            _column("job_id", "text", nullable=True),
            _column("artifact_kind", "text"),
            _column("filename", "text"),
            _column("content_type", "text"),
            _column("size_bytes", "integer"),
            _column("checksum_sha256", "text"),
            _column("object_provider_type", "text"),
            _column("object_key_ref", "text"),
            _column("status", "text", default_sql="'active'"),
            _column("retention_status", "text", default_sql="'active'"),
            _column("created_at", "text"),
            _column("updated_at", "text"),
            _column("deleted_at", "text", nullable=True),
        ),
        (
            _constraint("p", ("artifact_id",)),
            _constraint("u", ("workspace_id", "owner_type", "owner_id", "artifact_kind")),
        ),
    ),
    TableSpec(
        "sqag_generation_runs",
        (
            _column("run_id", "text"),
            _column("workspace_id", "text"),
            _column("actor_tracking_id", "text"),
            _column("actor_key_version", "text"),
            _column("job_id", "text", nullable=True),
            _column("idempotency_key", "text", nullable=True),
            _column("parent_run_id", "text", nullable=True),
            _column("attempt_number", "integer", default_sql="1"),
            _column("job_type", "text"),
            _column("status", "text"),
            _column("error_category", "text", nullable=True),
            _column("quote_session_id", "text", nullable=True),
            _column("started_at", "text"),
            _column("completed_at", "text", nullable=True),
            _column("app_revision", "text", nullable=True),
            _column("evidence_schema_version", "text"),
            _column("retention_expires_at", "text"),
            _column("original_retention_expires_at", "text"),
            _column("legal_hold", "integer", default_sql="0"),
            _column("deletion_state", "text", default_sql="'active'"),
            _column("deletion_error_code", "text", nullable=True),
            _column("deletion_claimed_at", "text", nullable=True),
        ),
        (
            _constraint("p", ("run_id",)),
            _constraint("u", ("run_id", "workspace_id")),
            _constraint(
                "f",
                ("parent_run_id",),
                referenced_table="sqag_generation_runs",
                referenced_columns=("run_id",),
            ),
            _constraint("c", (), expression="attempt_number >= 1"),
            _constraint("c", (), expression=f"status in ({_STATUS_VALUES})"),
        ),
    ),
    TableSpec(
        "sqag_generation_evidence",
        (
            _column("evidence_id", "text"),
            _column("run_id", "text"),
            _column("workspace_id", "text"),
            _column("evidence_type", "text"),
            _column("evidence_schema_version", "text"),
            _column("evidence_json", "text"),
            _column("evidence_sha256", "text"),
            _column("created_at", "text"),
            _column("retention_expires_at", "text"),
            _column("original_retention_expires_at", "text"),
            _column("legal_hold", "integer", default_sql="0"),
        ),
        (
            _constraint("p", ("evidence_id",)),
            _constraint(
                "f",
                ("run_id", "workspace_id"),
                referenced_table="sqag_generation_runs",
                referenced_columns=("run_id", "workspace_id"),
            ),
            _constraint("c", (), expression="length(evidence_sha256) = 64"),
        ),
    ),
    TableSpec(
        "sqag_audit_events",
        (
            _column("event_id", "text"),
            _column("run_id", "text", nullable=True),
            _column("feedback_id", "text", nullable=True),
            _column("session_id", "text", nullable=True),
            _column("workspace_id", "text"),
            _column("actor_tracking_id", "text"),
            _column("actor_key_version", "text"),
            _column("event_type", "text"),
            _column("event_json", "text"),
            _column("event_sha256", "text"),
            _column("created_at", "text"),
            _column("retention_expires_at", "text"),
            _column("original_retention_expires_at", "text"),
            _column("legal_hold", "integer", default_sql="0"),
        ),
        (
            _constraint("p", ("event_id",)),
            _constraint(
                "f",
                ("run_id", "workspace_id"),
                referenced_table="sqag_generation_runs",
                referenced_columns=("run_id", "workspace_id"),
            ),
        ),
    ),
    TableSpec(
        "sqag_feedback",
        (
            _column("feedback_id", "text"),
            _column("support_reference", "text"),
            _column("workspace_id", "text"),
            _column("reporter_tracking_id", "text"),
            _column("reporter_key_version", "text"),
            _column("run_id", "text", nullable=True),
            _column("session_id", "text", nullable=True),
            _column("category", "text"),
            _column("title", "text"),
            _column("message", "text"),
            _column("expected_result", "text", nullable=True),
            _column("actual_result", "text", nullable=True),
            _column("reproduction_steps", "text", nullable=True),
            _column("impact", "text", nullable=True),
            _column("link_choice", "text"),
            _column("manual_reference_text", "text", nullable=True),
            _column("manual_reference_status", "text"),
            _column("resolved_reference_type", "text", nullable=True),
            _column("resolved_reference_id", "text", nullable=True),
            _column("publication_version_id", "text", nullable=True),
            _column("link_resolution_source", "text", nullable=True),
            _column("link_resolved_at", "text", nullable=True),
            _column("diagnostic_metadata_json", "text"),
            _column("status", "text"),
            _column("created_at", "text"),
            _column("updated_at", "text"),
            _column("closed_at", "text", nullable=True),
            _column("retention_expires_at", "text"),
            _column("original_retention_expires_at", "text"),
            _column("submission_retention_expires_at", "text"),
            _column("retention_policy_version", "text"),
            _column("legal_hold", "integer", default_sql="0"),
            _column("deletion_state", "text", default_sql="'active'"),
            _column("deletion_error_code", "text", nullable=True),
            _column("deletion_claimed_at", "text", nullable=True),
        ),
        (
            _constraint("p", ("feedback_id",)),
            _constraint("u", ("support_reference",)),
            _constraint("u", ("feedback_id", "workspace_id")),
            _constraint(
                "f",
                ("run_id", "workspace_id"),
                referenced_table="sqag_generation_runs",
                referenced_columns=("run_id", "workspace_id"),
            ),
        ),
    ),
    TableSpec(
        "sqag_feedback_status_history",
        (
            _column("history_id", "text"),
            _column("feedback_id", "text"),
            _column("workspace_id", "text"),
            _column("from_status", "text", nullable=True),
            _column("to_status", "text"),
            _column("actor_tracking_id", "text"),
            _column("actor_key_version", "text"),
            _column("resolution_note", "text", nullable=True),
            _column("created_at", "text"),
            _column("retention_expires_at", "text"),
            _column("original_retention_expires_at", "text"),
            _column("legal_hold", "integer", default_sql="0"),
        ),
        (
            _constraint("p", ("history_id",)),
            _constraint(
                "f",
                ("feedback_id", "workspace_id"),
                referenced_table="sqag_feedback",
                referenced_columns=("feedback_id", "workspace_id"),
            ),
        ),
    ),
    TableSpec(
        "sqag_legal_holds",
        (
            _column("hold_id", "text"),
            _column("workspace_id", "text"),
            _column("target_type", "text"),
            _column("target_id", "text"),
            _column("enabled", "integer", default_sql="1"),
            _column("reason_code", "text"),
            _column("case_reference", "text", nullable=True),
            _column("actor_tracking_id", "text"),
            _column("actor_key_version", "text"),
            _column("created_at", "text"),
            _column("released_by_tracking_id", "text", nullable=True),
            _column("released_by_key_version", "text", nullable=True),
            _column("released_at", "text", nullable=True),
        ),
        (_constraint("p", ("hold_id",)),),
    ),
    TableSpec(
        "sqag_retention_delete_authorizations",
        (
            _column("authorization_id", "text"),
            _column("workspace_id", "text"),
            _column("record_type", "text"),
            _column("record_id", "text"),
            _column("created_at", "text"),
        ),
        (
            _constraint("p", ("authorization_id",)),
            _constraint("u", ("workspace_id", "record_type", "record_id")),
        ),
    ),
    TableSpec(
        "sqag_deletion_receipts",
        (
            _column("receipt_id", "text"),
            _column("workspace_id", "text"),
            _column("record_type", "text"),
            _column("record_id", "text"),
            _column("reason", "text"),
            _column("deleted_at", "text"),
            _column("original_retention_expires_at", "text"),
            _column("created_at", "text"),
            _column("retention_expires_at", "text"),
        ),
        (
            _constraint("p", ("receipt_id",)),
            _constraint("u", ("workspace_id", "record_type", "record_id")),
        ),
    ),
    TableSpec(
        "sqag_retention_scan_cursors",
        (
            _column("workspace_id", "text"),
            _column("candidate_type", "text"),
            _column("last_retention_expires_at", "text"),
            _column("last_record_id", "text"),
            _column("updated_at", "text"),
        ),
        (_constraint("p", ("workspace_id", "candidate_type")),),
    ),
    TableSpec(
        "sqag_quote_publication_versions",
        (
            _column("workspace_id", "text"),
            _column("session_id", "text"),
            _column("run_id", "text"),
            _column("job_id", "text", nullable=True),
            _column("state", "text"),
            _column("artifact_storage_mode", "text"),
            _column("artifact_source", "text", default_sql="'version'"),
            _column("metadata_json", "text"),
            _column("error_code", "text", nullable=True),
            _column("created_at", "text"),
            _column("updated_at", "text"),
            _column("promoted_at", "text", nullable=True),
            _column("failed_at", "text", nullable=True),
            _column("retention_expires_at", "text"),
            _column("original_retention_expires_at", "text"),
            _column("legal_hold", "integer", default_sql="0"),
            _column("deletion_state", "text", default_sql="'active'"),
            _column("deletion_error_code", "text", nullable=True),
            _column("deletion_claimed_at", "text", nullable=True),
        ),
        (
            _constraint("p", ("workspace_id", "run_id")),
            _constraint("u", ("workspace_id", "session_id", "run_id")),
            _constraint(
                "c", (), expression="state in ('staged', 'published', 'superseded', 'failed')"
            ),
            _constraint("c", (), expression="artifact_storage_mode in ('database', 'object')"),
            _constraint("c", (), expression="artifact_source in ('version', 'legacy_current')"),
        ),
    ),
    TableSpec(
        "sqag_quote_publication_artifacts",
        (
            _column("workspace_id", "text"),
            _column("session_id", "text"),
            _column("run_id", "text"),
            _column("artifact_kind", "text"),
            _column("filename", "text"),
            _column("content_type", "text"),
            _column("size_bytes", "bigint"),
            _column("checksum_sha256", "text"),
            _column("content_blob", "bytea"),
            _column("created_at", "text"),
            _column("updated_at", "text"),
        ),
        (
            _constraint("p", ("workspace_id", "run_id", "artifact_kind")),
            _constraint(
                "f",
                ("workspace_id", "run_id"),
                referenced_table="sqag_quote_publication_versions",
                referenced_columns=("workspace_id", "run_id"),
                on_delete="c",
            ),
            _constraint("c", (), expression="length(checksum_sha256) = 64"),
        ),
    ),
)
TABLE_SPECS_BY_NAME = MappingProxyType({item.name: item for item in TABLE_SPECS})

_LEDGER_TABLE_SPEC = TableSpec(
    LEDGER_TABLE,
    (
        _column("sequence_no", "integer"),
        _column("migration_id", "text"),
        _column("checksum_sha256", "character(64)"),
        _column("applied_at", "timestamp with time zone", default_sql="CURRENT_TIMESTAMP"),
    ),
    (
        _constraint("p", ("migration_id",)),
        _constraint("u", ("sequence_no",)),
        _constraint("c", (), expression="sequence_no > 0"),
        _constraint("c", (), expression="checksum_sha256 ~ '^[0-9a-f]{64}$'"),
    ),
)

INDEX_SPECS = (
    _index("sqag_generation_runs_workspace_job_uidx", "sqag_generation_runs", ("workspace_id", "job_id"), unique=True, predicate="job_id is not null"),
    _index("sqag_generation_runs_workspace_idempotency_uidx", "sqag_generation_runs", ("workspace_id", "idempotency_key"), unique=True, predicate="idempotency_key is not null"),
    _index("sqag_legal_holds_active_target_uidx", "sqag_legal_holds", ("workspace_id", "target_type", "target_id"), unique=True, predicate="enabled = 1"),
    _index("sqag_generation_runs_workspace_started_idx", "sqag_generation_runs", ("workspace_id", "started_at")),
    _index("sqag_generation_runs_retention_idx", "sqag_generation_runs", ("workspace_id", "deletion_state", "retention_expires_at", "run_id")),
    _index("sqag_generation_runs_actor_idx", "sqag_generation_runs", ("workspace_id", "actor_tracking_id", "started_at")),
    _index("sqag_generation_evidence_run_idx", "sqag_generation_evidence", ("workspace_id", "run_id", "created_at")),
    _index("sqag_generation_evidence_retention_idx", "sqag_generation_evidence", ("workspace_id", "retention_expires_at")),
    _index("sqag_audit_events_run_idx", "sqag_audit_events", ("workspace_id", "run_id", "created_at")),
    _index("sqag_audit_events_actor_idx", "sqag_audit_events", ("workspace_id", "actor_tracking_id", "created_at")),
    _index("sqag_audit_events_feedback_idx", "sqag_audit_events", ("workspace_id", "feedback_id", "created_at")),
    _index("sqag_audit_events_retention_idx", "sqag_audit_events", ("workspace_id", "retention_expires_at", "event_id")),
    _index("sqag_feedback_workspace_status_idx", "sqag_feedback", ("workspace_id", "status", "created_at")),
    _index("sqag_feedback_support_idx", "sqag_feedback", ("workspace_id", "support_reference")),
    _index("sqag_feedback_retention_idx", "sqag_feedback", ("workspace_id", "deletion_state", "retention_expires_at", "feedback_id")),
    _index("sqag_feedback_history_parent_idx", "sqag_feedback_status_history", ("workspace_id", "feedback_id", "created_at")),
    _index("sqag_legal_holds_state_idx", "sqag_legal_holds", ("workspace_id", "enabled", "target_type", "target_id")),
    _index("sqag_deletion_receipts_retention_idx", "sqag_deletion_receipts", ("workspace_id", "retention_expires_at")),
    _index("sqag_quote_publication_versions_session_idx", "sqag_quote_publication_versions", ("workspace_id", "session_id", "state", "updated_at", "run_id")),
    _index("sqag_quote_publication_versions_retention_idx", "sqag_quote_publication_versions", ("workspace_id", "deletion_state", "retention_expires_at", "run_id")),
    _index("sqag_quote_publication_artifacts_session_idx", "sqag_quote_publication_artifacts", ("workspace_id", "session_id", "run_id", "artifact_kind")),
    _index("sqag_feedback_publication_idx", "sqag_feedback", ("workspace_id", "publication_version_id", "run_id")),
)
INDEX_SPECS_BY_NAME = MappingProxyType({item.name: item for item in INDEX_SPECS})

TRIGGER_SPECS = (
    _trigger("sqag_generation_evidence_no_update", "sqag_generation_evidence", ("update",), routine_name="sqag_reject_immutable_change"),
    _trigger("sqag_audit_events_no_update", "sqag_audit_events", ("update",), routine_name="sqag_reject_immutable_change"),
    _trigger("sqag_generation_evidence_guard_delete", "sqag_generation_evidence", ("delete",), routine_name="sqag_require_retention_delete_authorization"),
    _trigger("sqag_audit_events_guard_delete", "sqag_audit_events", ("delete",), routine_name="sqag_require_retention_delete_authorization"),
    _trigger(
        "sqag_feedback_linkage_no_update",
        "sqag_feedback",
        ("update",),
        columns=("run_id", "session_id", "publication_version_id", "link_resolution_source", "link_resolved_at"),
        routine_name="sqag_reject_immutable_change",
    ),
)
TRIGGER_SPECS_BY_NAME = MappingProxyType({item.name: item for item in TRIGGER_SPECS})

ROUTINE_SPECS = (
    _trigger_routine("sqag_reject_immutable_change", "004_generation_forensics_feedback_retention_postgres.sql"),
    _trigger_routine("sqag_require_retention_delete_authorization", "005_forensic_postgres_delete_guards.sql"),
    RoutineSpec(
        schema_name="public",
        name="sqag_quote_session_deletion_hold_blocked",
        identity_arguments="text, text",
        result_type="boolean",
        language="sql",
        owner="sqag_migrator",
        security_definer=True,
        volatility="s",
        parallel="u",
        leakproof=False,
        proconfig=("search_path=pg_catalog, public",),
        source_migration_id="008_quote_session_deletion_hold_authority_postgres.sql",
        referenced_relations=(
            "sqag_audit_events",
            "sqag_feedback",
            "sqag_feedback_status_history",
            "sqag_generation_evidence",
            "sqag_generation_runs",
            "sqag_legal_holds",
            "sqag_quote_publication_versions",
            "sqag_quote_sessions",
        ),
        direct_acl=(("sqag_migrator", "EXECUTE", False), ("sqag_runtime", "EXECUTE", False)),
    ),
)
ROUTINE_SPECS_BY_KEY = MappingProxyType({item.key: item for item in ROUTINE_SPECS})

MIGRATION_OBJECTS = (
    MigrationObjectSpec(
        MIGRATION_FILE_NAMES[0],
        tables=tuple(TABLE_SPECS_BY_NAME[name] for name in ("sqag_profiles", "sqag_pricing_references", "sqag_quote_sessions")),
    ),
    MigrationObjectSpec(MIGRATION_FILE_NAMES[1], tables=(TABLE_SPECS_BY_NAME["sqag_object_artifacts"],)),
    MigrationObjectSpec(
        MIGRATION_FILE_NAMES[2],
        tables=tuple(TABLE_SPECS_BY_NAME[name] for name in ("sqag_generation_runs", "sqag_generation_evidence", "sqag_audit_events", "sqag_feedback", "sqag_feedback_status_history", "sqag_legal_holds", "sqag_retention_delete_authorizations", "sqag_deletion_receipts", "sqag_retention_scan_cursors")),
        indexes=INDEX_SPECS[:18],
        triggers=TRIGGER_SPECS[:2],
        routines=(ROUTINE_SPECS[0],),
    ),
    MigrationObjectSpec(MIGRATION_FILE_NAMES[3], triggers=TRIGGER_SPECS[2:4], routines=(ROUTINE_SPECS[1],)),
    MigrationObjectSpec(
        MIGRATION_FILE_NAMES[4],
        tables=tuple(TABLE_SPECS_BY_NAME[name] for name in ("sqag_quote_publication_versions", "sqag_quote_publication_artifacts")),
        indexes=INDEX_SPECS[18:21],
    ),
    MigrationObjectSpec(
        MIGRATION_FILE_NAMES[5],
        indexes=(INDEX_SPECS[21],),
        triggers=(TRIGGER_SPECS[4],),
        table_mutations=(TableMutationSpec("sqag_feedback", ("publication_version_id", "link_resolution_source", "link_resolved_at")),),
    ),
    MigrationObjectSpec(MIGRATION_FILE_NAMES[6], routines=(ROUTINE_SPECS[2],)),
)

if tuple(item.migration_id for item in MIGRATION_OBJECTS) != MIGRATION_FILE_NAMES:
    raise RuntimeError("SQAG PostgreSQL migration object map must match the ordered manifest.")
if {item.name for item in TABLE_SPECS} != EXPECTED_TABLES:
    raise RuntimeError("SQAG PostgreSQL table specifications must match the canonical inventory.")
if {item.name for item in INDEX_SPECS} != EXPECTED_INDEXES:
    raise RuntimeError("SQAG PostgreSQL index specifications must match the canonical inventory.")
if {item.name for item in TRIGGER_SPECS} != EXPECTED_TRIGGERS:
    raise RuntimeError("SQAG PostgreSQL trigger specifications must match the canonical inventory.")
if {item.name for item in ROUTINE_SPECS} != EXPECTED_ROUTINES:
    raise RuntimeError("SQAG PostgreSQL routine specifications must match the canonical inventory.")
if {item.key[1:] for item in ROUTINE_SPECS} != EXPECTED_ROUTINE_KEYS:
    raise RuntimeError("SQAG PostgreSQL routine identity specifications must match the canonical inventory.")
_derived_trigger_links: dict[str, set[tuple[str, str]]] = {}
for _trigger_spec in TRIGGER_SPECS:
    _derived_trigger_links.setdefault(_trigger_spec.routine_key[1], set()).add((_trigger_spec.name, _trigger_spec.table_name))
if {routine: frozenset(links) for routine, links in _derived_trigger_links.items()} != EXPECTED_TRIGGER_ROUTINE_LINKS:
    raise RuntimeError("SQAG PostgreSQL trigger-routine links must match the canonical inventory.")

MIGRATION_TABLES = MappingProxyType({item.migration_id: frozenset(table.name for table in item.tables) for item in MIGRATION_OBJECTS})
if tuple(MIGRATION_TABLES) != MIGRATION_FILE_NAMES:
    raise RuntimeError("SQAG PostgreSQL migration table map must match the ordered manifest.")
if set().union(*MIGRATION_TABLES.values()) != EXPECTED_TABLES:
    raise RuntimeError("SQAG PostgreSQL expected table inventory must match the migration manifest.")
for _migration_object in MIGRATION_OBJECTS:
    for _mutation in _migration_object.table_mutations:
        _mutation_table = TABLE_SPECS_BY_NAME.get(_mutation.table_name)
        if (
            _mutation_table is None
            or not _mutation.added_columns
            or len(set(_mutation.added_columns)) != len(_mutation.added_columns)
            or not set(_mutation.added_columns).issubset(
                {column.name for column in _mutation_table.columns}
            )
        ):
            raise RuntimeError("SQAG PostgreSQL table mutation map is not prefix-safe.")

MIGRATION_OBJECT_PROVENANCE = MappingProxyType(
    {
        item.migration_id: MappingProxyType(
            {
                "tables": tuple(table.name for table in item.tables),
                "indexes": tuple(index.name for index in item.indexes),
                "triggers": tuple(trigger.name for trigger in item.triggers),
                "routines": tuple(routine.key for routine in item.routines),
                "table_mutations": tuple((mutation.table_name, mutation.added_columns) for mutation in item.table_mutations),
            }
        )
        for item in MIGRATION_OBJECTS
    }
)
_OBJECT_PROVENANCE_RANK: dict[tuple[str, str], int] = {}
for _migration_index, _migration_object in enumerate(MIGRATION_OBJECTS):
    for _table in _migration_object.tables:
        _object_key = ("table", f"public.{_table.name}")
        if _object_key in _OBJECT_PROVENANCE_RANK:
            raise RuntimeError("SQAG table provenance must be unique.")
        _OBJECT_PROVENANCE_RANK[_object_key] = _migration_index
    for _index in _migration_object.indexes:
        _object_key = ("index", f"public.{_index.name}")
        if _object_key in _OBJECT_PROVENANCE_RANK:
            raise RuntimeError("SQAG index provenance must be unique.")
        _OBJECT_PROVENANCE_RANK[_object_key] = _migration_index
    for _trigger in _migration_object.triggers:
        _object_key = ("trigger", f"public.{_trigger.table_name}.{_trigger.name}")
        if _object_key in _OBJECT_PROVENANCE_RANK:
            raise RuntimeError("SQAG trigger provenance must be unique.")
        _OBJECT_PROVENANCE_RANK[_object_key] = _migration_index
    for _routine in _migration_object.routines:
        _object_key = ("routine", f"public.{_routine.name}({_routine.identity_arguments})")
        if _object_key in _OBJECT_PROVENANCE_RANK:
            raise RuntimeError("SQAG routine provenance must be unique.")
        _OBJECT_PROVENANCE_RANK[_object_key] = _migration_index
    for _mutation in _migration_object.table_mutations:
        for _column_name in _mutation.added_columns:
            _object_key = ("column", f"public.{_mutation.table_name}.{_column_name}")
            if _object_key in _OBJECT_PROVENANCE_RANK:
                raise RuntimeError("SQAG column provenance must be unique.")
            _OBJECT_PROVENANCE_RANK[_object_key] = _migration_index


def _effective_table_spec(table_name: str, applied_count: int) -> TableSpec:
    spec = TABLE_SPECS_BY_NAME[table_name]
    prefix_columns = {
        column.name
        for migration_object in MIGRATION_OBJECTS[:applied_count]
        for table in migration_object.tables
        if table.name == table_name
        for column in table.columns
    }
    pending_columns = {
        column_name
        for migration_object in MIGRATION_OBJECTS[applied_count:]
        for mutation in migration_object.table_mutations
        if mutation.table_name == table_name
        for column_name in mutation.added_columns
        if column_name not in prefix_columns
    }
    if not pending_columns:
        return spec
    return TableSpec(spec.name, tuple(column for column in spec.columns if column.name not in pending_columns), spec.constraints, spec.owner)


def canonical_migration_payload(path: Path) -> bytes:
    try:
        raw_payload = path.read_bytes()
    except OSError as exc:
        raise MigrationSafetyError(f"migration_source_missing:{path.name}") from exc
    try:
        source = raw_payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MigrationSafetyError(f"migration_source_invalid_utf8:{path.name}") from exc
    return source.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def migration_manifest(migrations_dir: Path) -> tuple[Migration, ...]:
    return tuple(
        Migration(
            sequence_no=sequence_no,
            migration_id=file_name,
            path=migrations_dir / file_name,
            checksum_sha256=sha256(canonical_migration_payload(migrations_dir / file_name)).hexdigest(),
        )
        for sequence_no, file_name in enumerate(MIGRATION_FILE_NAMES, start=1)
    )


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            content = stripped[1:-1]
            return tuple(item.strip('"') for item in content.split(",")) if content else ()
        if not stripped:
            return ()
    return (value,)


_SQL_TOKEN_RE = re.compile(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z0-9_$]*|\d+(?:\.\d+)?|<>|<=|>=|:=|::|[^\s]", re.DOTALL)
_SQL_CAST_RE = re.compile(r"::\s*(?:(?:pg_catalog)\s*\.\s*)?[a-z_][a-z0-9_]*(?:\s*\[\s*\])?", re.IGNORECASE)


def _strip_outer_sql_parentheses(tokens: list[str]) -> list[str]:
    while len(tokens) >= 2 and tokens[0] == "(" and tokens[-1] == ")":
        depth = 0
        encloses_all = True
        for index, token in enumerate(tokens):
            if token == "(":
                depth += 1
            elif token == ")":
                depth -= 1
                if depth == 0 and index != len(tokens) - 1:
                    encloses_all = False
                    break
        if encloses_all and depth == 0:
            tokens = tokens[1:-1]
        else:
            break
    return tokens


def _semantic_sql_tokens(value: str | None, *, expression: bool = False) -> tuple[str, ...]:
    normalized = str(value or "")
    normalized = re.sub(r"/\*.*?\*/", " ", normalized, flags=re.DOTALL)
    normalized = re.sub(r"--[^\r\n]*", " ", normalized)
    normalized = _SQL_CAST_RE.sub("", normalized)
    normalized = re.sub(r"=\s*any\s*\(\s*array\s*\[", " in (", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\]\s*\)", ")", normalized)
    tokens = _SQL_TOKEN_RE.findall(normalized)
    if expression:
        tokens = _strip_outer_sql_parentheses(tokens)
        changed = True
        while changed:
            changed = False
            collapsed: list[str] = []
            index = 0
            while index < len(tokens):
                if index + 2 < len(tokens) and tokens[index] == "(" and tokens[index + 2] == ")" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", tokens[index + 1]):
                    collapsed.append(tokens[index + 1])
                    index += 3
                    changed = True
                else:
                    collapsed.append(tokens[index])
                    index += 1
            tokens = collapsed
        if tokens == ["now", "(", ")"]:
            tokens = ["current_timestamp"]
    return tuple(token.lower() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", token) else token for token in tokens)


_FUNCTION_BODY_RE = re.compile(
    r"create\s+(?:or\s+replace\s+)?function\s+(?:(?:public)\s*\.\s*)?{name}\s*\([^)]*\).*?\bas\s+(?P<delimiter>\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$)",
    re.IGNORECASE | re.DOTALL,
)


def _canonical_routine_body(routine: RoutineSpec, migrations: Sequence[Migration]) -> str:
    migration = next((item for item in migrations if item.migration_id == routine.source_migration_id), None)
    if migration is None:
        raise MigrationSafetyError(f"migration_source_missing:{routine.source_migration_id}")
    source = canonical_migration_payload(migration.path).decode("utf-8")
    matches = list(re.finditer(_FUNCTION_BODY_RE.format(name=re.escape(routine.name)), source))
    if len(matches) != 1:
        raise MigrationSafetyError(f"routine_source_invalid:{routine.name}")
    match = matches[0]
    body_end = source.find(match.group("delimiter"), match.end())
    if body_end < 0:
        raise MigrationSafetyError(f"routine_source_invalid:{routine.name}")
    body = source[match.end() : body_end]
    if not _semantic_sql_tokens(body):
        raise MigrationSafetyError(f"routine_source_invalid:{routine.name}")
    return body


def _fetch_public_relations(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
select c.oid as relation_oid, c.relname as relation_name, c.relkind,
       c.relpersistence, c.relispartition, owner.rolname as owner
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_roles owner on owner.oid = c.relowner
where n.nspname = 'public' and c.relname like 'sqag_' || chr(37)
order by c.relname, c.oid
"""
    ).fetchall()
    return [
        {
            "oid": _row_value(row, "relation_oid"),
            "name": str(_row_value(row, "relation_name")),
            "relkind": str(_row_value(row, "relkind") or ""),
            "relpersistence": str(_row_value(row, "relpersistence") or ""),
            "relispartition": bool(_row_value(row, "relispartition")),
            "owner": _row_value(row, "owner"),
        }
        for row in rows
    ]


def _fetch_constraint_index_oids(connection: Any) -> set[Any]:
    rows = connection.execute(
        """
select constraint_row.conindid as index_oid
from pg_catalog.pg_constraint constraint_row
join pg_catalog.pg_class table_class on table_class.oid = constraint_row.conrelid
join pg_catalog.pg_namespace table_namespace on table_namespace.oid = table_class.relnamespace
where table_namespace.nspname = 'public' and constraint_row.conindid <> 0
"""
    ).fetchall()
    return {_row_value(row, "index_oid") for row in rows}


def _fetch_public_tables(connection: Any) -> dict[str, dict[str, Any]]:
    relations = _fetch_public_relations(connection)
    tables: dict[str, dict[str, Any]] = {
        relation["name"]: {"relation": relation, "columns": [], "constraints": []}
        for relation in relations
        if relation["relkind"] in {"r", "p"}
    }
    rows = connection.execute(
        """
select c.oid as relation_oid, a.attnum, a.attname,
       format_type(a.atttypid, a.atttypmod) as type_name,
       a.attnotnull, pg_get_expr(ad.adbin, ad.adrelid) as column_default,
       a.attidentity, a.attgenerated
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_attribute a on a.attrelid = c.oid
left join pg_catalog.pg_attrdef ad on ad.adrelid = a.attrelid and ad.adnum = a.attnum
where n.nspname = 'public' and c.relname like 'sqag_' || chr(37)
  and c.relkind in ('r', 'p') and a.attnum > 0 and not a.attisdropped
order by c.relname, a.attnum
"""
    ).fetchall()
    by_oid = {table["relation"]["oid"]: table for table in tables.values()}
    for row in rows:
        table = by_oid.get(_row_value(row, "relation_oid"))
        if table is not None:
            table["columns"].append(
                (
                    str(_row_value(row, "attname")),
                    str(_row_value(row, "type_name")),
                    not bool(_row_value(row, "attnotnull")),
                    _row_value(row, "column_default"),
                    str(_row_value(row, "attidentity") or ""),
                    str(_row_value(row, "attgenerated") or ""),
                )
            )
    constraint_rows = connection.execute(
        """
select c.conrelid as relation_oid, c.contype,
       array(select local_attribute.attname
             from unnest(c.conkey) with ordinality as local_key(attnum, ordinal)
             join pg_catalog.pg_attribute local_attribute
               on local_attribute.attrelid = c.conrelid and local_attribute.attnum = local_key.attnum
             order by local_key.ordinal) as columns,
       array(select foreign_attribute.attname
             from unnest(c.confkey) with ordinality as foreign_key(attnum, ordinal)
             join pg_catalog.pg_attribute foreign_attribute
               on foreign_attribute.attrelid = c.confrelid and foreign_attribute.attnum = foreign_key.attnum
             order by foreign_key.ordinal) as referenced_columns,
       referenced_table.relname as referenced_table,
       c.confdeltype as on_delete, c.confupdtype as on_update,
       pg_get_expr(c.conbin, c.conrelid) as expression,
       c.convalidated, c.condeferrable, c.condeferred
from pg_catalog.pg_constraint c
join pg_catalog.pg_class table_class on table_class.oid = c.conrelid
join pg_catalog.pg_namespace table_namespace on table_namespace.oid = table_class.relnamespace
left join pg_catalog.pg_class referenced_table on referenced_table.oid = c.confrelid
where table_namespace.nspname = 'public' and table_class.relname like 'sqag_' || chr(37)
  and c.contype in ('p', 'u', 'f', 'c')
order by table_class.relname, c.oid
"""
    ).fetchall()
    for row in constraint_rows:
        table = by_oid.get(_row_value(row, "relation_oid"))
        if table is None:
            continue
        kind = str(_row_value(row, "contype") or "")
        table["constraints"].append(
            (
                kind,
                tuple(str(item) for item in _as_tuple(_row_value(row, "columns"))),
                _row_value(row, "referenced_table"),
                tuple(str(item) for item in _as_tuple(_row_value(row, "referenced_columns"))),
                str(_row_value(row, "on_delete") or "a"),
                str(_row_value(row, "on_update") or "a"),
                _semantic_sql_tokens(str(_row_value(row, "expression") or ""), expression=True) if kind == "c" else (),
                bool(_row_value(row, "convalidated")),
                bool(_row_value(row, "condeferrable")),
                bool(_row_value(row, "condeferred")),
            )
        )
    return tables


def _fetch_public_indexes(connection: Any) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
select index_class.oid as index_oid, index_class.relname as index_name,
       table_class.relname as table_name, index_owner.rolname as owner,
       index_info.indisunique, index_info.indisvalid, index_info.indisready,
       index_info.indnkeyatts, index_info.indnatts,
       array(select pg_get_indexdef(index_info.indexrelid, key_no, true)
             from generate_series(1, index_info.indnkeyatts) as key_no order by key_no) as key_semantics,
       pg_get_expr(index_info.indpred, index_info.indrelid) as predicate,
       exists(select 1 from pg_catalog.pg_constraint constraint_row
              where constraint_row.conindid = index_info.indexrelid) as constraint_index
from pg_catalog.pg_index index_info
join pg_catalog.pg_class index_class on index_class.oid = index_info.indexrelid
join pg_catalog.pg_namespace index_namespace on index_namespace.oid = index_class.relnamespace
join pg_catalog.pg_class table_class on table_class.oid = index_info.indrelid
join pg_catalog.pg_roles index_owner on index_owner.oid = index_class.relowner
where index_namespace.nspname = 'public'
order by index_class.relname, index_class.oid
"""
    ).fetchall()
    return {
        str(_row_value(row, "index_name")): {
            "oid": _row_value(row, "index_oid"),
            "name": str(_row_value(row, "index_name")),
            "table_name": str(_row_value(row, "table_name")),
            "owner": _row_value(row, "owner"),
            "unique": bool(_row_value(row, "indisunique")),
            "valid": bool(_row_value(row, "indisvalid")),
            "ready": bool(_row_value(row, "indisready")),
            "key_semantics": tuple(str(item) for item in _as_tuple(_row_value(row, "key_semantics"))),
            "key_count": int(_row_value(row, "indnkeyatts") or 0),
            "attribute_count": int(_row_value(row, "indnatts") or 0),
            "predicate": _row_value(row, "predicate"),
            "constraint_index": bool(_row_value(row, "constraint_index")),
        }
        for row in rows
    }


def _fetch_public_triggers(connection: Any) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
select t.oid as trigger_oid, t.tgname as trigger_name,
       table_namespace.nspname as table_schema, table_class.relname as table_name,
       t.tgtype, t.tgenabled,
       array(select trigger_attribute.attname
             from unnest(t.tgattr) with ordinality as trigger_key(attnum, ordinal)
             join pg_catalog.pg_attribute trigger_attribute
               on trigger_attribute.attrelid = t.tgrelid and trigger_attribute.attnum = trigger_key.attnum
             order by trigger_key.ordinal) as columns,
       function_namespace.nspname as function_schema, function_row.proname as function_name,
       pg_get_function_identity_arguments(function_row.oid) as identity_arguments
from pg_catalog.pg_trigger t
join pg_catalog.pg_class table_class on table_class.oid = t.tgrelid
join pg_catalog.pg_namespace table_namespace on table_namespace.oid = table_class.relnamespace
join pg_catalog.pg_proc function_row on function_row.oid = t.tgfoid
join pg_catalog.pg_namespace function_namespace on function_namespace.oid = function_row.pronamespace
where table_namespace.nspname = 'public' and not t.tgisinternal
order by t.tgname, table_class.relname, t.oid
"""
    ).fetchall()
    return {
        str(_row_value(row, "trigger_name")): {
            "oid": _row_value(row, "trigger_oid"),
            "name": str(_row_value(row, "trigger_name")),
            "table_schema": str(_row_value(row, "table_schema")),
            "table_name": str(_row_value(row, "table_name")),
            "tgtype": int(_row_value(row, "tgtype") or 0),
            "enabled": str(_row_value(row, "tgenabled") or ""),
            "columns": tuple(str(item) for item in _as_tuple(_row_value(row, "columns"))),
            "routine_key": (
                str(_row_value(row, "function_schema")),
                str(_row_value(row, "function_name")),
                str(_row_value(row, "identity_arguments") or ""),
            ),
        }
        for row in rows
    }


def _fetch_public_routines(connection: Any) -> tuple[dict[tuple[str, str, str], list[dict[str, Any]]], dict[tuple[str, str, str], list[dict[str, Any]]]]:
    rows = connection.execute(
        """
select p.oid as function_oid, n.nspname as schema_name, p.proname,
       p.prokind, pg_get_function_identity_arguments(p.oid) as identity_arguments,
       pg_get_function_result(p.oid) as result_type, p.prosecdef,
       p.provolatile, p.proparallel, p.proleakproof, p.proconfig,
       p.prosrc as function_body, pg_get_functiondef(p.oid) as function_definition,
       language_row.lanname as language, owner.rolname as owner
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
join pg_catalog.pg_language language_row on language_row.oid = p.prolang
join pg_catalog.pg_roles owner on owner.oid = p.proowner
where n.nspname = 'public' and p.proname like 'sqag_' || chr(37)
order by p.proname, identity_arguments, p.oid
"""
    ).fetchall()
    routines: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(_row_value(row, "schema_name")), str(_row_value(row, "proname")), str(_row_value(row, "identity_arguments") or ""))
        routines.setdefault(key, []).append(
            {
                "oid": _row_value(row, "function_oid"),
                "schema_name": key[0],
                "name": key[1],
                "identity_arguments": key[2],
                "prokind": str(_row_value(row, "prokind") or ""),
                "result_type": str(_row_value(row, "result_type") or ""),
                "security_definer": bool(_row_value(row, "prosecdef")),
                "volatility": str(_row_value(row, "provolatile") or ""),
                "parallel": str(_row_value(row, "proparallel") or ""),
                "leakproof": bool(_row_value(row, "proleakproof")),
                "proconfig": tuple(str(item) for item in _as_tuple(_row_value(row, "proconfig"))),
                "function_body": str(_row_value(row, "function_body") or ""),
                "function_definition": str(_row_value(row, "function_definition") or ""),
                "language": str(_row_value(row, "language") or ""),
                "owner": str(_row_value(row, "owner") or ""),
            }
        )
    acl_rows = connection.execute(
        """
select n.nspname as schema_name, p.proname,
       pg_get_function_identity_arguments(p.oid) as identity_arguments,
       case when acl.grantee = 0 then 'PUBLIC' else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee,
       acl.privilege_type, acl.is_grantable
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
left join lateral aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) acl on true
left join pg_catalog.pg_roles grantee_role on grantee_role.oid = acl.grantee and acl.grantee <> 0
where n.nspname = 'public' and p.proname like 'sqag_' || chr(37)
order by p.proname, identity_arguments, grantee, acl.privilege_type
"""
    ).fetchall()
    acls: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in acl_rows:
        key = (str(_row_value(row, "schema_name")), str(_row_value(row, "proname")), str(_row_value(row, "identity_arguments") or ""))
        grantee = _row_value(row, "grantee")
        if grantee is not None:
            acls.setdefault(key, []).append(
                {
                    "grantee": str(grantee),
                    "privilege": str(_row_value(row, "privilege_type")),
                    "grantable": bool(_row_value(row, "is_grantable")),
                }
            )
    return routines, acls


def _ledger_exists(connection: Any) -> bool:
    row = connection.execute("select to_regclass('public.sqag_schema_migrations') as ledger_table").fetchone()
    return bool(row and _row_value(row, "ledger_table"))


def _ledger_rows(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        "select sequence_no, migration_id, checksum_sha256, applied_at from public.sqag_schema_migrations order by sequence_no"
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


def _column_fingerprint(spec: ColumnSpec) -> tuple[Any, ...]:
    return (spec.name, spec.type_name.lower(), spec.nullable, _semantic_sql_tokens(spec.default_sql, expression=True) if spec.default_sql is not None else None, spec.identity, spec.generated)


def _constraint_fingerprint(spec: ConstraintSpec) -> tuple[Any, ...]:
    return (spec.kind, spec.columns, spec.referenced_table, spec.referenced_columns, spec.on_delete, spec.on_update, _semantic_sql_tokens(spec.expression, expression=True) if spec.expression is not None else (), spec.validated, spec.deferrable, spec.deferred)


def _table_matches(table: dict[str, Any], spec: TableSpec) -> bool:
    relation = table["relation"]
    if relation["name"] != spec.name or relation["relkind"] != "r" or relation["relpersistence"] != "p" or relation["relispartition"] or relation["owner"] != spec.owner:
        return False
    actual_columns = tuple(
        (name, str(type_name).lower(), nullable, _semantic_sql_tokens(default_sql, expression=True) if default_sql is not None else None, identity, generated)
        for name, type_name, nullable, default_sql, identity, generated in table["columns"]
    )
    if actual_columns != tuple(_column_fingerprint(column) for column in spec.columns):
        return False
    return set(table["constraints"]) == {_constraint_fingerprint(constraint) for constraint in spec.constraints}


def _index_matches(observed: dict[str, Any], spec: IndexSpec) -> bool:
    return (
        observed["name"] == spec.name
        and observed["table_name"] == spec.table_name
        and observed["owner"] == spec.owner
        and observed["unique"] is spec.unique
        and observed["valid"]
        and observed["ready"]
        and observed["key_count"] == len(spec.key_semantics)
        and observed["attribute_count"] == len(spec.key_semantics)
        and tuple(_semantic_sql_tokens(value, expression=True) for value in observed["key_semantics"]) == tuple(_semantic_sql_tokens(value, expression=True) for value in spec.key_semantics)
        and (_semantic_sql_tokens(observed["predicate"], expression=True) if observed["predicate"] is not None else ()) == (_semantic_sql_tokens(spec.predicate, expression=True) if spec.predicate is not None else ())
    )


def _trigger_events(tgtype: int) -> tuple[str, ...]:
    return tuple(event for bit, event in ((4, "insert"), (8, "delete"), (16, "update"), (32, "truncate")) if tgtype & bit)


def _trigger_matches(observed: dict[str, Any], spec: TriggerSpec) -> bool:
    tgtype = observed["tgtype"]
    timing = "before" if tgtype & 2 else "instead" if tgtype & 64 else "after"
    level = "row" if tgtype & 1 else "statement"
    return observed["name"] == spec.name and observed["table_schema"] == "public" and observed["table_name"] == spec.table_name and timing == spec.timing and _trigger_events(tgtype) == spec.events and observed["columns"] == spec.columns and level == spec.level and observed["enabled"] == spec.enabled and observed["routine_key"] == spec.routine_key


_SQL_RELATION_RE = re.compile(r"\b(?:delete\s+from|from|join|into|update)\s+(?:public\s*\.\s*)?([a-z_][a-z0-9_]*)", re.IGNORECASE)
_UNQUALIFIED_SQL_RELATION_RE = re.compile(r"\b(?:delete\s+from|from|join|into|update)\s+(?!public\s*\.\s*)(sqag_[a-z0-9_]*)", re.IGNORECASE)


def _routine_matches(observed: dict[str, Any], spec: RoutineSpec, acl_rows: list[dict[str, Any]], migrations: Sequence[Migration]) -> bool:
    if observed["schema_name"] != spec.schema_name or observed["name"] != spec.name or observed["identity_arguments"] != spec.identity_arguments or observed["prokind"] != "f" or observed["result_type"].lower() != spec.result_type.lower() or observed["language"] != spec.language or observed["owner"] != spec.owner or observed["security_definer"] is not spec.security_definer or observed["volatility"] != spec.volatility or observed["parallel"] != spec.parallel or observed["leakproof"] is not spec.leakproof or observed["proconfig"] != spec.proconfig:
        return False
    try:
        expected_body = _canonical_routine_body(spec, migrations)
    except MigrationSafetyError:
        return False
    if _semantic_sql_tokens(observed["function_body"]) != _semantic_sql_tokens(expected_body):
        return False
    if spec.referenced_relations:
        relations = {match.group(1).lower() for match in _SQL_RELATION_RE.finditer(observed["function_definition"]) if match.group(1).lower().startswith("sqag_")}
        if relations != set(spec.referenced_relations) or _UNQUALIFIED_SQL_RELATION_RE.search(observed["function_definition"]):
            return False
    if spec.direct_acl:
        actual_acl = {(item["grantee"], item["privilege"], item["grantable"]) for item in acl_rows}
        if actual_acl != set(spec.direct_acl):
            return False
    return True


def _object_key(kind: str, value: Any) -> str:
    if kind in {"table", "index"}:
        return f"public.{value.name}"
    if kind == "trigger":
        return f"public.{value.table_name}.{value.name}"
    if kind == "routine":
        return f"public.{value.name}({value.identity_arguments})"
    return str(value)


def _schema_object_blockers(
    migrations: Sequence[Migration],
    applied_count: int,
    *,
    relations: list[dict[str, Any]],
    tables: dict[str, dict[str, Any]],
    indexes: dict[str, dict[str, Any]],
    triggers: dict[str, dict[str, Any]],
    routines: dict[tuple[str, str, str], list[dict[str, Any]]],
    routine_acls: dict[tuple[str, str, str], list[dict[str, Any]]],
    constraint_index_oids: set[Any],
) -> list[str]:
    applied_specs = MIGRATION_OBJECTS[:applied_count]
    pending_specs = MIGRATION_OBJECTS[applied_count:]
    applied_tables = {table.name for item in applied_specs for table in item.tables}
    pending_tables = {table.name for item in pending_specs for table in item.tables}
    pending_indexes = {index.name for item in pending_specs for index in item.indexes}
    pending_triggers = {trigger.name for item in pending_specs for trigger in item.triggers}
    blockers: list[str] = []

    known_relations = EXPECTED_TABLES | EXPECTED_INDEXES | {LEDGER_TABLE}
    for relation in relations:
        if relation["name"] in known_relations:
            continue
        if relation["relkind"] in {"i", "I"} and relation["oid"] in constraint_index_oids:
            continue
        blockers.append(f"managed_namespace_extra:relation:public.{relation['name']}")

    relation_by_name = {relation["name"]: relation for relation in relations}
    for table_name in sorted(applied_tables):
        spec = _effective_table_spec(table_name, applied_count)
        relation = relation_by_name.get(table_name)
        table = tables.get(table_name)
        if relation is None:
            blockers.append(f"applied_prefix_missing:table:{_object_key('table', spec)}")
        elif table is None or not _table_matches(table, spec):
            blockers.append(f"applied_prefix_drift:table:{_object_key('table', spec)}")
    for table_name in sorted(pending_tables):
        if table_name in relation_by_name:
            blockers.append(f"pending_suffix_present:table:{_object_key('table', TABLE_SPECS_BY_NAME[table_name])}")
    for item in pending_specs:
        for mutation in item.table_mutations:
            table = tables.get(mutation.table_name)
            if table is None:
                continue
            prefix_columns = {
                column.name
                for prefix_item in applied_specs
                for prefix_table in prefix_item.tables
                if prefix_table.name == mutation.table_name
                for column in prefix_table.columns
            }
            present_columns = {column[0] for column in table["columns"]}
            for column_name in mutation.added_columns:
                # 007 is an idempotent upgrade for older 004 installations;
                # the canonical 004 creator already contains these columns.
                if column_name not in prefix_columns and column_name in present_columns:
                    blockers.append(f"pending_suffix_present:column:public.{mutation.table_name}.{column_name}")

    for item in applied_specs:
        for spec in item.indexes:
            observed = indexes.get(spec.name)
            if observed is None:
                category = "drift" if spec.name in relation_by_name else "missing"
                blockers.append(f"applied_prefix_{category}:index:{_object_key('index', spec)}")
            elif not _index_matches(observed, spec):
                blockers.append(f"applied_prefix_drift:index:{_object_key('index', spec)}")
    for index_name in sorted(pending_indexes):
        if index_name in relation_by_name:
            blockers.append(f"pending_suffix_present:index:{_object_key('index', INDEX_SPECS_BY_NAME[index_name])}")

    for item in applied_specs:
        for spec in item.triggers:
            observed = triggers.get(spec.name)
            if observed is None:
                blockers.append(f"applied_prefix_missing:trigger:{_object_key('trigger', spec)}")
            elif not _trigger_matches(observed, spec):
                blockers.append(f"applied_prefix_drift:trigger:{_object_key('trigger', spec)}")
    for trigger_name in sorted(pending_triggers):
        if trigger_name in triggers:
            blockers.append(f"pending_suffix_present:trigger:{_object_key('trigger', TRIGGER_SPECS_BY_NAME[trigger_name])}")

    for item in applied_specs:
        for spec in item.routines:
            candidates = routines.get(spec.key, [])
            if not candidates:
                blockers.append(f"applied_prefix_missing:routine:{_object_key('routine', spec)}")
            elif len(candidates) != 1 or not _routine_matches(candidates[0], spec, routine_acls.get(spec.key, []), migrations):
                blockers.append(f"applied_prefix_drift:routine:{_object_key('routine', spec)}")
    pending_routines = {routine.key for item in pending_specs for routine in item.routines}
    for routine_key in sorted(pending_routines):
        if routine_key in routines:
            blockers.append(f"pending_suffix_present:routine:{_object_key('routine', ROUTINE_SPECS_BY_KEY[routine_key])}")

    for trigger_name, observed in triggers.items():
        if trigger_name.startswith("sqag_") and trigger_name not in EXPECTED_TRIGGERS:
            blockers.append(f"managed_namespace_extra:trigger:public.{observed['table_name']}.{trigger_name}")
    for routine_key in routines:
        if routine_key[1].startswith("sqag_") and routine_key not in EXPECTED_ROUTINE_KEYS:
            blockers.append(f"managed_namespace_extra:routine:{routine_key[0]}.{routine_key[1]}({routine_key[2]})")
    return blockers


def _sort_blockers(blockers: Sequence[str]) -> list[str]:
    ledger_priority = {"existing_schema_without_trusted_ledger": 10, "ledger_schema_invalid": 20, "unknown_or_out_of_order_migration": 30, "unexpected_applied_migration": 30}
    kind_priority = {"table": 10, "column": 20, "index": 30, "trigger": 40, "routine": 50, "relation": 60}
    category_priority = {"managed_namespace_extra": 10, "applied_prefix_missing": 20, "applied_prefix_drift": 30, "pending_suffix_present": 40}

    def sort_key(blocker: str) -> tuple[Any, ...]:
        parts = blocker.split(":", 2)
        category = parts[0]
        if category in ledger_priority:
            return (0, ledger_priority[category], 0, 0, blocker)
        if category == "checksum_drift":
            index = next((index for index, item in enumerate(MIGRATION_OBJECTS) if len(parts) == 2 and item.migration_id == parts[1]), len(MIGRATION_OBJECTS))
            return (0, 40, index, 0, blocker)
        if len(parts) == 3 and category in category_priority:
            kind, object_key = parts[1], parts[2]
            return (1, _OBJECT_PROVENANCE_RANK.get((kind, object_key), len(MIGRATION_OBJECTS)), kind_priority.get(kind, 99), category_priority[category], object_key)
        return (2, 0, 0, 0, blocker)

    return sorted(set(blockers), key=sort_key)


def _valid_ledger_table(tables: dict[str, dict[str, Any]]) -> bool:
    table = tables.get(LEDGER_TABLE)
    return table is not None and _table_matches(table, _LEDGER_TABLE_SPEC)


def inspect_postgres_migrations(connection: Any, migrations: Sequence[Migration]) -> dict[str, Any]:
    """Inspect the trusted ledger prefix and exact PostgreSQL objects."""

    expected_ids = [migration.migration_id for migration in migrations]
    expected_head = expected_ids[-1] if expected_ids else None
    relations = _fetch_public_relations(connection)
    tables = _fetch_public_tables(connection)
    indexes = _fetch_public_indexes(connection)
    triggers = _fetch_public_triggers(connection)
    routines, routine_acls = _fetch_public_routines(connection)
    constraint_index_oids = _fetch_constraint_index_oids(connection)
    ledger_relation = next((relation for relation in relations if relation["name"] == LEDGER_TABLE), None)
    ledger_exists = ledger_relation is not None
    blockers: list[str] = []
    applied_rows: list[dict[str, Any]] = []
    applied_count: int | None = None

    if ledger_exists:
        if not _valid_ledger_table(tables):
            blockers.append("ledger_schema_invalid")
        else:
            try:
                applied_rows = _ledger_rows(connection)
            except Exception:
                blockers.append("ledger_schema_invalid")
            else:
                if len(applied_rows) > len(migrations):
                    blockers.append("unexpected_applied_migration")
                if not blockers:
                    for position, row in enumerate(applied_rows):
                        expected = migrations[position]
                        if not isinstance(row["sequence_no"], int) or row["sequence_no"] != expected.sequence_no or row["migration_id"] != expected.migration_id:
                            blockers.append("unknown_or_out_of_order_migration")
                            break
                        if row["checksum_sha256"] != expected.checksum_sha256:
                            blockers.append(f"checksum_drift:{expected.migration_id}")
                            break
                        if row["applied_at"] is None:
                            blockers.append("unknown_or_out_of_order_migration")
                            break
                if not blockers:
                    applied_count = len(applied_rows)
    else:
        if any(relation["name"] != LEDGER_TABLE for relation in relations) or any(name.startswith("sqag_") for name in indexes) or any(name.startswith("sqag_") for name in triggers) or any(key[1].startswith("sqag_") for key in routines):
            blockers.append("existing_schema_without_trusted_ledger")
        else:
            applied_count = 0

    if applied_count is not None and not blockers:
        blockers.extend(_schema_object_blockers(migrations, applied_count, relations=relations, tables=tables, indexes=indexes, triggers=triggers, routines=routines, routine_acls=routine_acls, constraint_index_oids=constraint_index_oids))

    if applied_count is None:
        applied_ids = None
        pending_ids = None
        applied_head = None
    else:
        applied_ids = [row["migration_id"] for row in applied_rows]
        pending_ids = expected_ids[applied_count:]
        applied_head = applied_ids[-1] if applied_ids else None
    ordered_blockers = _sort_blockers(blockers)
    return {
        "status": "unsafe" if ordered_blockers else "ready",
        "safeToApply": not ordered_blockers,
        "ledgerState": "present" if ledger_exists else "missing",
        "expectedHead": expected_head,
        "appliedHead": applied_head,
        "appliedMigrationIds": applied_ids,
        "pendingMigrationIds": pending_ids,
        "blockers": ordered_blockers,
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
        for part in parts[1:]:
            statement = part.strip()
            if statement:
                connection.execute(statement)
        return
    for statement in (part.strip() for part in sql.split(";")):
        if statement:
            connection.execute(statement)


def apply_postgres_migrations(connection: Any, migrations: Sequence[Migration]) -> dict[str, Any]:
    """Apply the next immutable suffix in the caller-owned transaction."""

    connection.execute("set local search_path to public, pg_catalog")
    connection.execute("select pg_catalog.pg_advisory_xact_lock(?)", (MIGRATION_LOCK_KEY,))
    before = inspect_postgres_migrations(connection, migrations)
    if not before["safeToApply"]:
        raise MigrationSafetyError(str(before["blockers"][0]))
    if before["ledgerState"] == "missing":
        _create_ledger(connection)
    applied_now: list[str] = []
    applied_count = len(before.get("appliedMigrationIds") or [])
    for migration in migrations[applied_count:]:
        payload = canonical_migration_payload(migration.path)
        if sha256(payload).hexdigest() != migration.checksum_sha256:
            raise MigrationSafetyError(f"migration_source_changed_during_run:{migration.migration_id}")
        execute_migration_sql(connection, payload.decode("utf-8"))
        connection.execute(
            "insert into public.sqag_schema_migrations (sequence_no, migration_id, checksum_sha256) values (?, ?, ?)",
            (migration.sequence_no, migration.migration_id, migration.checksum_sha256),
        )
        applied_now.append(migration.migration_id)
    after = inspect_postgres_migrations(connection, migrations)
    if not after["safeToApply"] or after.get("pendingMigrationIds"):
        blocker = after["blockers"][0] if after["blockers"] else "migration_head_not_reached"
        raise MigrationSafetyError(str(blocker))
    return {
        "expectedHead": migrations[-1].migration_id if migrations else None,
        "appliedNow": applied_now,
        "alreadyApplied": [migration.migration_id for migration in migrations[:applied_count]],
    }
