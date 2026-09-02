"""Auditable, fail-closed PostgreSQL migrations for SQAG.

This module has no application-startup hook. Operators invoke it through the
dedicated migration and preflight scripts.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence


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
    "009_telemetry_events_postgres.sql",
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
        "sqag_telemetry_source_state",
        "sqag_telemetry_events",
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
        "sqag_telemetry_source_state_workspace_idx",
        "sqag_telemetry_events_feed_idx",
        "sqag_telemetry_events_source_sequence_uidx",
        "sqag_telemetry_events_retention_idx",
        "sqag_telemetry_events_actor_idx",
        "sqag_telemetry_events_retry_uidx",
    }
)
EXPECTED_TRIGGERS = frozenset(
    {
        "sqag_generation_evidence_no_update",
        "sqag_audit_events_no_update",
        "sqag_generation_evidence_guard_delete",
        "sqag_audit_events_guard_delete",
        "sqag_feedback_linkage_no_update",
        "sqag_telemetry_source_state_no_delete",
        "sqag_telemetry_events_no_update",
        "sqag_telemetry_events_guard_delete",
    }
)
EXPECTED_TRIGGER_KEYS = frozenset(
    {
        ("public", "sqag_generation_evidence", "sqag_generation_evidence_no_update"),
        ("public", "sqag_audit_events", "sqag_audit_events_no_update"),
        ("public", "sqag_generation_evidence", "sqag_generation_evidence_guard_delete"),
        ("public", "sqag_audit_events", "sqag_audit_events_guard_delete"),
        ("public", "sqag_feedback", "sqag_feedback_linkage_no_update"),
        ("public", "sqag_telemetry_source_state", "sqag_telemetry_source_state_no_delete"),
        ("public", "sqag_telemetry_events", "sqag_telemetry_events_no_update"),
        ("public", "sqag_telemetry_events", "sqag_telemetry_events_guard_delete"),
    }
)
EXPECTED_ROUTINES = frozenset(
    {
        "sqag_reject_immutable_change",
        "sqag_require_retention_delete_authorization",
        "sqag_quote_session_deletion_hold_blocked",
        "sqag_quote_session_deletion_hold_blocked_v2",
    }
)
EXPECTED_TRIGGER_ROUTINE_KEYS = frozenset(
    {
        ("sqag_reject_immutable_change", ""),
        ("sqag_require_retention_delete_authorization", ""),
    }
)
EXPECTED_CALLABLE_ROUTINE_KEYS = frozenset(
    {
        ("sqag_quote_session_deletion_hold_blocked", "text, text"),
        ("sqag_quote_session_deletion_hold_blocked_v2", "text, text"),
    }
)
EXPECTED_ROUTINE_KEYS = EXPECTED_TRIGGER_ROUTINE_KEYS | EXPECTED_CALLABLE_ROUTINE_KEYS
EXPECTED_TRIGGER_ROUTINE_LINKS = {
    "sqag_reject_immutable_change": frozenset(
        {
            ("sqag_generation_evidence_no_update", "sqag_generation_evidence"),
            ("sqag_audit_events_no_update", "sqag_audit_events"),
            ("sqag_feedback_linkage_no_update", "sqag_feedback"),
            ("sqag_telemetry_source_state_no_delete", "sqag_telemetry_source_state"),
            ("sqag_telemetry_events_no_update", "sqag_telemetry_events"),
        }
    ),
    "sqag_require_retention_delete_authorization": frozenset(
        {
            ("sqag_generation_evidence_guard_delete", "sqag_generation_evidence"),
            ("sqag_audit_events_guard_delete", "sqag_audit_events"),
            ("sqag_telemetry_events_guard_delete", "sqag_telemetry_events"),
        }
    ),
}


class MigrationSafetyError(RuntimeError):
    """Raised when ledger or schema state makes migration unsafe."""

    def __init__(self, blocker: str) -> None:
        super().__init__(f"SQAG PostgreSQL migration blocked: {blocker}")
        self.blocker = blocker


class CatalogProjectionError(MigrationSafetyError):
    """Raised when a catalog query does not return its locked projection."""


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
    referenced_schema: str | None = None
    match_type: str = "s"


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


def _column(name: str, type_name: str, *, nullable: bool = False, default_sql: str | None = None) -> ColumnSpec:
    return ColumnSpec(name, type_name, nullable, default_sql)


def _cols(*definitions: str) -> tuple[ColumnSpec, ...]:
    result: list[ColumnSpec] = []
    for definition in definitions:
        name, type_part = definition.split(":", 1)
        default_sql = None
        if "=" in type_part:
            type_part, default_sql = type_part.split("=", 1)
        nullable = type_part.endswith("?")
        if nullable:
            type_part = type_part[:-1]
        result.append(_column(name, type_part, nullable=nullable, default_sql=default_sql))
    return tuple(result)


def _c(
    kind: str,
    columns: str | tuple[str, ...] = (),
    *,
    referenced_table: str | None = None,
    referenced_schema: str | None = None,
    referenced_columns: str | tuple[str, ...] = (),
    match_type: str = "s",
    on_delete: str = "a",
    on_update: str = "a",
    expression: str | None = None,
) -> ConstraintSpec:
    local = tuple(columns.split(",")) if isinstance(columns, str) and columns else tuple(columns)
    foreign = (
        tuple(referenced_columns.split(","))
        if isinstance(referenced_columns, str) and referenced_columns
        else tuple(referenced_columns)
    )
    if kind == "f" and referenced_schema is None:
        referenced_schema = "public"
    return ConstraintSpec(
        kind=kind,
        columns=local,
        referenced_table=referenced_table,
        referenced_columns=foreign,
        on_delete=on_delete,
        on_update=on_update,
        expression=expression,
        referenced_schema=referenced_schema,
        match_type=match_type,
    )


def _index(
    name: str,
    table_name: str,
    key_semantics: str | tuple[str, ...],
    *,
    unique: bool = False,
    predicate: str | None = None,
) -> IndexSpec:
    keys = tuple(key_semantics.split(",")) if isinstance(key_semantics, str) else tuple(key_semantics)
    return IndexSpec(name, table_name, unique, keys, predicate)


def _trigger(
    name: str,
    table_name: str,
    events: tuple[str, ...],
    *,
    columns: tuple[str, ...] = (),
    routine_name: str,
) -> TriggerSpec:
    return TriggerSpec(name, table_name, "before", events, columns, "row", "O", ("public", routine_name, ""))


def _trigger_routine(name: str, migration_id: str) -> RoutineSpec:
    return RoutineSpec(
        "public", name, "", "trigger", "plpgsql", "sqag_migrator",
        False, "v", "u", False, (), migration_id,
    )


_STATUS_VALUES = (
    "'received', 'queued', 'running', 'blocked', 'completed', "
    "'needs_confirmation', 'needs_review', 'completed_with_review_required', "
    "'degraded', 'failed', 'cancelled', 'timed_out', 'abandoned', 'superseded'"
)

TABLE_SPECS = (
    TableSpec(
        "sqag_profiles",
        _cols("workspace_id:text", "profile_id:text", "payload_json:text", "created_at:text", "updated_at:text"),
        (_c("p", "workspace_id,profile_id"),),
    ),
    TableSpec(
        "sqag_pricing_references",
        _cols("workspace_id:text", "reference_id:text", "payload_json:text", "created_at:text", "updated_at:text"),
        (_c("p", "workspace_id,reference_id"),),
    ),
    TableSpec(
        "sqag_quote_sessions",
        _cols("workspace_id:text", "session_id:text", "metadata_json:text", "draft_files_json:text='[]'", "created_at:text", "updated_at:text"),
        (_c("p", "workspace_id,session_id"),),
    ),
    TableSpec(
        "sqag_object_artifacts",
        _cols(
            "artifact_id:text", "workspace_id:text", "owner_type:text", "owner_id:text",
            "platform_user_id:text?", "session_id:text?", "job_id:text?", "artifact_kind:text",
            "filename:text", "content_type:text", "size_bytes:integer", "checksum_sha256:text",
            "object_provider_type:text", "object_key_ref:text", "status:text='active'",
            "retention_status:text='active'", "created_at:text", "updated_at:text", "deleted_at:text?",
        ),
        (_c("p", "artifact_id"), _c("u", "workspace_id,owner_type,owner_id,artifact_kind")),
    ),
    TableSpec(
        "sqag_generation_runs",
        _cols(
            "run_id:text", "workspace_id:text", "actor_tracking_id:text", "actor_key_version:text",
            "job_id:text?", "idempotency_key:text?", "parent_run_id:text?", "attempt_number:integer=1",
            "job_type:text", "status:text", "error_category:text?", "quote_session_id:text?",
            "started_at:text", "completed_at:text?", "app_revision:text?",
            "evidence_schema_version:text", "retention_expires_at:text",
            "original_retention_expires_at:text", "legal_hold:integer=0", "deletion_state:text='active'",
            "deletion_error_code:text?", "deletion_claimed_at:text?",
        ),
        (
            _c("p", "run_id"),
            _c("u", "run_id,workspace_id"),
            _c("f", "parent_run_id", referenced_table="sqag_generation_runs", referenced_columns="run_id"),
            _c("c", expression="attempt_number >= 1"),
            _c("c", expression=f"status in ({_STATUS_VALUES})"),
        ),
    ),
    TableSpec(
        "sqag_generation_evidence",
        _cols(
            "evidence_id:text", "run_id:text", "workspace_id:text", "evidence_type:text",
            "evidence_schema_version:text", "evidence_json:text", "evidence_sha256:text",
            "created_at:text", "retention_expires_at:text", "original_retention_expires_at:text",
            "legal_hold:integer=0",
        ),
        (
            _c("p", "evidence_id"),
            _c("f", "run_id,workspace_id", referenced_table="sqag_generation_runs", referenced_columns="run_id,workspace_id"),
            _c("c", expression="length(evidence_sha256) = 64"),
        ),
    ),
    TableSpec(
        "sqag_audit_events",
        _cols(
            "event_id:text", "run_id:text?", "feedback_id:text?", "session_id:text?", "workspace_id:text",
            "actor_tracking_id:text", "actor_key_version:text", "event_type:text", "event_json:text",
            "event_sha256:text", "created_at:text", "retention_expires_at:text",
            "original_retention_expires_at:text", "legal_hold:integer=0",
        ),
        (
            _c("p", "event_id"),
            _c("f", "run_id,workspace_id", referenced_table="sqag_generation_runs", referenced_columns="run_id,workspace_id"),
            _c("c", expression="length(event_sha256) = 64"),
        ),
    ),
    TableSpec(
        "sqag_feedback",
        _cols(
            "feedback_id:text", "support_reference:text", "workspace_id:text",
            "reporter_tracking_id:text", "reporter_key_version:text", "run_id:text?", "session_id:text?",
            "category:text", "title:text", "message:text", "expected_result:text?", "actual_result:text?",
            "reproduction_steps:text?", "impact:text?", "link_choice:text", "manual_reference_text:text?",
            "manual_reference_status:text", "resolved_reference_type:text?", "resolved_reference_id:text?",
            "publication_version_id:text?", "link_resolution_source:text?", "link_resolved_at:text?",
            "diagnostic_metadata_json:text", "status:text", "created_at:text", "updated_at:text",
            "closed_at:text?", "retention_expires_at:text", "original_retention_expires_at:text",
            "submission_retention_expires_at:text", "retention_policy_version:text", "legal_hold:integer=0",
            "deletion_state:text='active'", "deletion_error_code:text?", "deletion_claimed_at:text?",
        ),
        (
            _c("p", "feedback_id"),
            _c("u", "support_reference"),
            _c("u", "feedback_id,workspace_id"),
            _c("f", "run_id,workspace_id", referenced_table="sqag_generation_runs", referenced_columns="run_id,workspace_id"),
        ),
    ),
    TableSpec(
        "sqag_feedback_status_history",
        _cols(
            "history_id:text", "feedback_id:text", "workspace_id:text", "from_status:text?", "to_status:text",
            "actor_tracking_id:text", "actor_key_version:text", "resolution_note:text?", "created_at:text",
            "retention_expires_at:text", "original_retention_expires_at:text", "legal_hold:integer=0",
        ),
        (
            _c("p", "history_id"),
            _c("f", "feedback_id,workspace_id", referenced_table="sqag_feedback", referenced_columns="feedback_id,workspace_id"),
        ),
    ),
    TableSpec(
        "sqag_legal_holds",
        _cols(
            "hold_id:text", "workspace_id:text", "target_type:text", "target_id:text", "enabled:integer=1",
            "reason_code:text", "case_reference:text?", "actor_tracking_id:text", "actor_key_version:text",
            "created_at:text", "released_by_tracking_id:text?", "released_by_key_version:text?", "released_at:text?",
        ),
        (_c("p", "hold_id"),),
    ),
    TableSpec(
        "sqag_retention_delete_authorizations",
        _cols("authorization_id:text", "workspace_id:text", "record_type:text", "record_id:text", "created_at:text"),
        (_c("p", "authorization_id"), _c("u", "workspace_id,record_type,record_id")),
    ),
    TableSpec(
        "sqag_deletion_receipts",
        _cols(
            "receipt_id:text", "workspace_id:text", "record_type:text", "record_id:text",
            "reason:text", "deleted_at:text", "original_retention_expires_at:text",
            "created_at:text", "retention_expires_at:text",
        ),
        (_c("p", "receipt_id"), _c("u", "workspace_id,record_type,record_id")),
    ),
    TableSpec(
        "sqag_retention_scan_cursors",
        _cols(
            "workspace_id:text", "candidate_type:text", "last_retention_expires_at:text",
            "last_record_id:text", "updated_at:text",
        ),
        (_c("p", "workspace_id,candidate_type"),),
    ),
    TableSpec(
        "sqag_quote_publication_versions",
        _cols(
            "workspace_id:text", "session_id:text", "run_id:text", "job_id:text?", "state:text",
            "artifact_storage_mode:text", "artifact_source:text='version'", "metadata_json:text",
            "error_code:text?", "created_at:text", "updated_at:text", "promoted_at:text?", "failed_at:text?",
            "retention_expires_at:text", "original_retention_expires_at:text", "legal_hold:integer=0",
            "deletion_state:text='active'", "deletion_error_code:text?", "deletion_claimed_at:text?",
        ),
        (
            _c("p", "workspace_id,run_id"),
            _c("u", "workspace_id,session_id,run_id"),
            _c("c", expression="state in ('staged', 'published', 'superseded', 'failed')"),
            _c("c", expression="artifact_storage_mode in ('database', 'object')"),
            _c("c", expression="artifact_source in ('version', 'legacy_current')"),
        ),
    ),
    TableSpec(
        "sqag_quote_publication_artifacts",
        _cols(
            "workspace_id:text", "session_id:text", "run_id:text", "artifact_kind:text",
            "filename:text", "content_type:text", "size_bytes:bigint", "checksum_sha256:text",
            "content_blob:bytea", "created_at:text", "updated_at:text",
        ),
        (
            _c("p", "workspace_id,run_id,artifact_kind"),
            _c(
                "f",
                "workspace_id,run_id",
                referenced_table="sqag_quote_publication_versions",
                referenced_columns="workspace_id,run_id",
                on_delete="c",
            ),
            _c("c", expression="length(checksum_sha256) = 64"),
        ),
    ),
    TableSpec(
        "sqag_telemetry_source_state",
        _cols(
            "workspace_id:text", "source_product:text", "next_source_sequence:integer=1",
            "high_watermark:integer=0", "reconciliation_state:text='healthy'",
            "last_reconciled_at:text?", "reconciliation_reference:text?",
            "created_at:text", "updated_at:text",
        ),
        (
            _c("p", "workspace_id,source_product"),
            _c("c", expression="source_product = 'sqag'"),
            _c("c", expression="next_source_sequence >= 1"),
            _c("c", expression="(high_watermark >= 0) and (high_watermark < next_source_sequence)"),
            _c("c", expression="reconciliation_state in ('healthy', 'reconciling', 'inconsistent')"),
        ),
    ),
    TableSpec(
        "sqag_telemetry_events",
        _cols(
            "workspace_id:text", "event_id:text", "source_product:text",
            "source_sequence:integer", "event_type:text", "event_status:text",
            "actor_tracking_id:text", "actor_key_version:text", "action_reference:text?",
            "run_reference:text?", "session_reference:text?", "support_reference:text?",
            "retry_lineage_id:text?", "attempt_number:integer?", "provider:text?",
            "model:text?", "reasoning_level:text?", "operation_route:text?", "purpose:text?",
            "failure_class:text?", "duration_ms:integer?", "usage_available:integer?",
            "input_tokens:integer?", "output_tokens:integer?", "total_tokens:integer?",
            "cache_read_tokens:integer?", "cache_write_tokens:integer?", "cost_available:integer?",
            "estimated_cost:numeric?", "actual_cost:numeric?", "currency:text?", "cost_version:text?",
            "quota_decision:text?", "rate_limit_decision:text?", "abuse_decision:text?",
            "deployment_revision:text?", "occurred_at:text", "immutable_metadata_digest:text",
            "retention_expires_at:text", "original_retention_expires_at:text",
            "legal_hold:integer=0", "deletion_state:text='active'", "deletion_error_code:text?",
            "deletion_claimed_at:text?",
        ),
        (
            _c("p", "workspace_id,event_id"),
            _c("f", "workspace_id,source_product", referenced_table="sqag_telemetry_source_state", referenced_columns="workspace_id,source_product"),
            _c("c", expression="source_product = 'sqag'"),
            _c("c", expression="source_sequence >= 1"),
            _c("c", expression="event_type in ('generation', 'validation', 'ai_provider_attempt', 'pricing_change', 'profile_change', 'publication', 'download', 'feedback', 'security', 'rate_limit', 'abuse', 'cancellation', 'timeout', 'abandonment', 'supersession', 'storage_staging', 'storage_finalization', 'storage_compensation', 'configuration', 'operator_action', 'reconciliation', 'retention', 'legal_hold', 'deletion', 'backup', 'restore')"),
            _c("c", expression="event_status in ('started', 'queued', 'running', 'success', 'failed', 'blocked', 'denied', 'completed', 'needs_confirmation', 'needs_review', 'completed_with_review_required', 'degraded', 'cancelled', 'timed_out', 'abandoned', 'superseded', 'available', 'unavailable', 'held', 'deleted', 'reconciled', 'staged', 'finalized', 'compensated', 'requested', 'updated', 'restored', 'rate_limited')"),
            _c("c", expression="(attempt_number is null) or (attempt_number >= 1)"),
            _c("c", expression="(failure_class is null) or (failure_class in ('missing_api_key', 'timeout', 'rate_limited', 'upstream_unavailable', 'http_error', 'network_error', 'invalid_json', 'schema_validation_failed', 'model_output_invalid', 'provider_error', 'generator_error', 'configuration', 'storage', 'authorization', 'unknown'))"),
            _c("c", expression="(duration_ms is null) or (duration_ms >= 0)"),
            _c("c", expression="(usage_available is null) or (usage_available in (0, 1))"),
            _c("c", expression="(input_tokens is null) or (input_tokens >= 0)"),
            _c("c", expression="(output_tokens is null) or (output_tokens >= 0)"),
            _c("c", expression="(total_tokens is null) or (total_tokens >= 0)"),
            _c("c", expression="(cache_read_tokens is null) or (cache_read_tokens >= 0)"),
            _c("c", expression="(cache_write_tokens is null) or (cache_write_tokens >= 0)"),
            _c("c", expression="(cost_available is null) or (cost_available in (0, 1))"),
            _c("c", expression="(estimated_cost is null) or (estimated_cost >= (0)::numeric)"),
            _c("c", expression="(actual_cost is null) or (actual_cost >= (0)::numeric)"),
            _c("c", expression="(provider is null) or (provider in ('openai', 'deepseek'))"),
            _c("c", expression="(reasoning_level is null) or (reasoning_level in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra', 'standard'))"),
            _c("c", expression="(quota_decision is null) or (quota_decision in ('allowed', 'denied', 'not_evaluated'))"),
            _c("c", expression="(rate_limit_decision is null) or (rate_limit_decision in ('allowed', 'denied', 'not_evaluated'))"),
            _c("c", expression="(abuse_decision is null) or (abuse_decision in ('allowed', 'denied', 'not_evaluated'))"),
            _c("c", expression="length(immutable_metadata_digest) = 64"),
            _c("c", expression="legal_hold in (0, 1)"),
            _c("c", expression="deletion_state in ('active', 'review_required', 'deleting', 'delete_failed')"),
        ),
    ),
)
TABLE_SPECS_BY_NAME = MappingProxyType({item.name: item for item in TABLE_SPECS})

_LEDGER_TABLE_SPEC = TableSpec(
    LEDGER_TABLE,
    _cols("sequence_no:integer", "migration_id:text", "checksum_sha256:char(64)", "applied_at:timestamptz=CURRENT_TIMESTAMP"),
    (
        _c("p", "migration_id"),
        _c("u", "sequence_no"),
        _c("c", expression="sequence_no > 0"),
        _c("c", expression="checksum_sha256 ~ '^[0-9a-f]{64}$'"),
    ),
)

INDEX_SPECS = (
    _index("sqag_generation_runs_workspace_job_uidx", "sqag_generation_runs", "workspace_id,job_id", unique=True, predicate="job_id is not null"),
    _index("sqag_generation_runs_workspace_idempotency_uidx", "sqag_generation_runs", "workspace_id,idempotency_key", unique=True, predicate="idempotency_key is not null"),
    _index("sqag_legal_holds_active_target_uidx", "sqag_legal_holds", "workspace_id,target_type,target_id", unique=True, predicate="enabled = 1"),
    _index("sqag_generation_runs_workspace_started_idx", "sqag_generation_runs", "workspace_id,started_at"),
    _index("sqag_generation_runs_retention_idx", "sqag_generation_runs", "workspace_id,deletion_state,retention_expires_at,run_id"),
    _index("sqag_generation_runs_actor_idx", "sqag_generation_runs", "workspace_id,actor_tracking_id,started_at"),
    _index("sqag_generation_evidence_run_idx", "sqag_generation_evidence", "workspace_id,run_id,created_at"),
    _index("sqag_generation_evidence_retention_idx", "sqag_generation_evidence", "workspace_id,retention_expires_at"),
    _index("sqag_audit_events_run_idx", "sqag_audit_events", "workspace_id,run_id,created_at"),
    _index("sqag_audit_events_actor_idx", "sqag_audit_events", "workspace_id,actor_tracking_id,created_at"),
    _index("sqag_audit_events_feedback_idx", "sqag_audit_events", "workspace_id,feedback_id,created_at"),
    _index("sqag_audit_events_retention_idx", "sqag_audit_events", "workspace_id,retention_expires_at,event_id"),
    _index("sqag_feedback_workspace_status_idx", "sqag_feedback", "workspace_id,status,created_at"),
    _index("sqag_feedback_support_idx", "sqag_feedback", "workspace_id,support_reference"),
    _index("sqag_feedback_retention_idx", "sqag_feedback", "workspace_id,deletion_state,retention_expires_at,feedback_id"),
    _index("sqag_feedback_history_parent_idx", "sqag_feedback_status_history", "workspace_id,feedback_id,created_at"),
    _index("sqag_legal_holds_state_idx", "sqag_legal_holds", "workspace_id,enabled,target_type,target_id"),
    _index("sqag_deletion_receipts_retention_idx", "sqag_deletion_receipts", "workspace_id,retention_expires_at"),
    _index("sqag_quote_publication_versions_session_idx", "sqag_quote_publication_versions", "workspace_id,session_id,state,updated_at,run_id"),
    _index("sqag_quote_publication_versions_retention_idx", "sqag_quote_publication_versions", "workspace_id,deletion_state,retention_expires_at,run_id"),
    _index("sqag_quote_publication_artifacts_session_idx", "sqag_quote_publication_artifacts", "workspace_id,session_id,run_id,artifact_kind"),
    _index("sqag_feedback_publication_idx", "sqag_feedback", "workspace_id,publication_version_id,run_id"),
    _index("sqag_telemetry_source_state_workspace_idx", "sqag_telemetry_source_state", "workspace_id,source_product,high_watermark"),
    _index("sqag_telemetry_events_feed_idx", "sqag_telemetry_events", "workspace_id,source_sequence,event_id"),
    _index("sqag_telemetry_events_source_sequence_uidx", "sqag_telemetry_events", "workspace_id,source_sequence", unique=True),
    _index("sqag_telemetry_events_retention_idx", "sqag_telemetry_events", "workspace_id,deletion_state,retention_expires_at,event_id"),
    _index("sqag_telemetry_events_actor_idx", "sqag_telemetry_events", "workspace_id,actor_tracking_id,occurred_at"),
    _index("sqag_telemetry_events_retry_uidx", "sqag_telemetry_events", "workspace_id,retry_lineage_id,attempt_number", unique=True, predicate="(retry_lineage_id is not null) and (attempt_number is not null)"),
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
    _trigger(
        "sqag_telemetry_source_state_no_delete",
        "sqag_telemetry_source_state",
        ("delete",),
        routine_name="sqag_reject_immutable_change",
    ),
    _trigger(
        "sqag_telemetry_events_no_update",
        "sqag_telemetry_events",
        ("update",),
        columns=(
            "workspace_id", "event_id", "source_product", "source_sequence",
            "event_type", "event_status", "actor_tracking_id", "actor_key_version",
            "action_reference", "run_reference", "session_reference", "support_reference",
            "retry_lineage_id", "attempt_number", "provider", "model", "reasoning_level",
            "operation_route", "purpose", "failure_class", "duration_ms", "usage_available",
            "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
            "cache_write_tokens", "cost_available", "estimated_cost", "actual_cost",
            "currency", "cost_version", "quota_decision", "rate_limit_decision",
            "abuse_decision", "deployment_revision", "occurred_at",
            "immutable_metadata_digest", "retention_expires_at", "original_retention_expires_at",
        ),
        routine_name="sqag_reject_immutable_change",
    ),
    _trigger("sqag_telemetry_events_guard_delete", "sqag_telemetry_events", ("delete",), routine_name="sqag_require_retention_delete_authorization"),
)
TRIGGER_SPECS_BY_KEY = MappingProxyType(
    {("public", item.table_name, item.name): item for item in TRIGGER_SPECS}
)

ROUTINE_SPECS = (
    _trigger_routine("sqag_reject_immutable_change", "004_generation_forensics_feedback_retention_postgres.sql"),
    _trigger_routine("sqag_require_retention_delete_authorization", "005_forensic_postgres_delete_guards.sql"),
    RoutineSpec(
        "public",
        "sqag_quote_session_deletion_hold_blocked",
        "text, text",
        "boolean",
        "sql",
        "sqag_migrator",
        True,
        "s",
        "u",
        False,
        ("search_path=pg_catalog, public",),
        "008_quote_session_deletion_hold_authority_postgres.sql",
        (
            "sqag_audit_events",
            "sqag_feedback",
            "sqag_feedback_status_history",
            "sqag_generation_evidence",
            "sqag_generation_runs",
            "sqag_legal_holds",
            "sqag_quote_publication_versions",
            "sqag_quote_sessions",
        ),
        (("sqag_runtime", "EXECUTE", False),),
    ),
    RoutineSpec(
        "public",
        "sqag_quote_session_deletion_hold_blocked_v2",
        "text, text",
        "boolean",
        "sql",
        "sqag_migrator",
        True,
        "s",
        "u",
        False,
        ("search_path=pg_catalog, public",),
        "009_telemetry_events_postgres.sql",
        (
            "sqag_audit_events",
            "sqag_feedback",
            "sqag_feedback_status_history",
            "sqag_generation_evidence",
            "sqag_generation_runs",
            "sqag_legal_holds",
            "sqag_quote_publication_versions",
            "sqag_quote_sessions",
            "sqag_telemetry_events",
        ),
        (("sqag_runtime", "EXECUTE", False),),
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
        tables=tuple(
            TABLE_SPECS_BY_NAME[name]
            for name in (
                "sqag_generation_runs", "sqag_generation_evidence", "sqag_audit_events",
                "sqag_feedback", "sqag_feedback_status_history", "sqag_legal_holds",
                "sqag_retention_delete_authorizations", "sqag_deletion_receipts", "sqag_retention_scan_cursors",
            )
        ),
        indexes=tuple(INDEX_SPECS[:18]),
        triggers=tuple(TRIGGER_SPECS[:2]),
        routines=(ROUTINE_SPECS[0],),
    ),
    MigrationObjectSpec(
        MIGRATION_FILE_NAMES[3],
        triggers=tuple(TRIGGER_SPECS[2:4]),
        routines=(ROUTINE_SPECS[1],),
    ),
    MigrationObjectSpec(
        MIGRATION_FILE_NAMES[4],
        tables=tuple(TABLE_SPECS_BY_NAME[name] for name in ("sqag_quote_publication_versions", "sqag_quote_publication_artifacts")),
        indexes=tuple(INDEX_SPECS[18:21]),
    ),
    MigrationObjectSpec(
        MIGRATION_FILE_NAMES[5],
        indexes=(INDEX_SPECS[21],),
        triggers=(TRIGGER_SPECS[4],),
        table_mutations=(TableMutationSpec("sqag_feedback", ("publication_version_id", "link_resolution_source", "link_resolved_at")),),
    ),
    MigrationObjectSpec(MIGRATION_FILE_NAMES[6], routines=(ROUTINE_SPECS[2],)),
    MigrationObjectSpec(
        MIGRATION_FILE_NAMES[7],
        tables=tuple(TABLE_SPECS_BY_NAME[name] for name in ("sqag_telemetry_source_state", "sqag_telemetry_events")),
        indexes=tuple(INDEX_SPECS[22:28]),
        triggers=tuple(TRIGGER_SPECS[5:8]),
        routines=(ROUTINE_SPECS[3],),
    ),
)

if tuple(item.migration_id for item in MIGRATION_OBJECTS) != MIGRATION_FILE_NAMES:
    raise RuntimeError("SQAG migration object map is not ordered.")
if {item.name for item in TABLE_SPECS} != EXPECTED_TABLES:
    raise RuntimeError("SQAG table provenance does not match the canonical inventory.")
if {item.name for item in INDEX_SPECS} != EXPECTED_INDEXES:
    raise RuntimeError("SQAG index provenance does not match the canonical inventory.")
if {item.name for item in TRIGGER_SPECS} != EXPECTED_TRIGGERS:
    raise RuntimeError("SQAG trigger provenance does not match the canonical inventory.")
if frozenset(TRIGGER_SPECS_BY_KEY) != EXPECTED_TRIGGER_KEYS:
    raise RuntimeError("SQAG table-qualified trigger identities do not match the canonical inventory.")
if {item.name for item in ROUTINE_SPECS} != EXPECTED_ROUTINES:
    raise RuntimeError("SQAG routine provenance does not match the canonical inventory.")
if {item.key[1:] for item in ROUTINE_SPECS} != EXPECTED_ROUTINE_KEYS:
    raise RuntimeError("SQAG routine identities do not match the canonical inventory.")

_derived_trigger_links: dict[str, set[tuple[str, str]]] = {}
for _trigger_spec in TRIGGER_SPECS:
    _derived_trigger_links.setdefault(_trigger_spec.routine_key[1], set()).add((_trigger_spec.name, _trigger_spec.table_name))
if {name: frozenset(value) for name, value in _derived_trigger_links.items()} != EXPECTED_TRIGGER_ROUTINE_LINKS:
    raise RuntimeError("SQAG trigger-routine provenance does not match.")

for _item in MIGRATION_OBJECTS:
    for _mutation in _item.table_mutations:
        _table = TABLE_SPECS_BY_NAME.get(_mutation.table_name)
        if _table is None or not set(_mutation.added_columns).issubset({column.name for column in _table.columns}):
            raise RuntimeError("SQAG column mutation provenance is not prefix-safe.")

MIGRATION_TABLES = MappingProxyType(
    {item.migration_id: frozenset(table.name for table in item.tables) for item in MIGRATION_OBJECTS}
)
if tuple(MIGRATION_TABLES) != MIGRATION_FILE_NAMES:
    raise RuntimeError("SQAG migration table map is not ordered.")
if set().union(*MIGRATION_TABLES.values()) != EXPECTED_TABLES:
    raise RuntimeError("SQAG expected table inventory is incomplete.")

MIGRATION_OBJECT_PROVENANCE = MappingProxyType(
    {
        item.migration_id: MappingProxyType(
            {
                "tables": tuple(table.name for table in item.tables),
                "indexes": tuple(index.name for index in item.indexes),
                "triggers": tuple(("public", trigger.table_name, trigger.name) for trigger in item.triggers),
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
        _OBJECT_PROVENANCE_RANK[("table", f"public.{_table.name}")] = _migration_index
    for _index_spec in _migration_object.indexes:
        _OBJECT_PROVENANCE_RANK[("index", f"public.{_index_spec.name}")] = _migration_index
    for _trigger_spec in _migration_object.triggers:
        _OBJECT_PROVENANCE_RANK[("trigger", f"public.{_trigger_spec.table_name}.{_trigger_spec.name}")] = _migration_index
    for _routine_spec in _migration_object.routines:
        _OBJECT_PROVENANCE_RANK[("routine", f"public.{_routine_spec.name}({_routine_spec.identity_arguments})")] = _migration_index
    for _mutation in _migration_object.table_mutations:
        for _column_name in _mutation.added_columns:
            _OBJECT_PROVENANCE_RANK[("column", f"public.{_mutation.table_name}.{_column_name}")] = _migration_index


def _effective_table_spec(table_name: str, applied_count: int) -> TableSpec:
    spec = TABLE_SPECS_BY_NAME[table_name]
    pending_columns = {
        column_name
        for item in MIGRATION_OBJECTS[applied_count:]
        for mutation in item.table_mutations
        if mutation.table_name == table_name
        for column_name in mutation.added_columns
    }
    if not pending_columns:
        return spec
    return TableSpec(spec.name, tuple(column for column in spec.columns if column.name not in pending_columns), spec.constraints, spec.owner)


def canonical_migration_payload(path: Path) -> bytes:
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
    result: list[Migration] = []
    for sequence_no, file_name in enumerate(MIGRATION_FILE_NAMES, 1):
        path = migrations_dir / file_name
        payload = canonical_migration_payload(path)
        result.append(Migration(sequence_no, file_name, path, sha256(payload).hexdigest()))
    return tuple(result)


def _row_value(row: Any, key: str, index: int = 0) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return row[index]


def _require_projection(row: Any, fields: Sequence[str], *, exact: bool = False) -> None:
    if isinstance(row, Mapping):
        actual = set(row)
        expected = set(fields)
        valid = actual == expected if exact else expected.issubset(actual)
        if not valid:
            raise CatalogProjectionError("catalog_projection_invalid")
        return
    try:
        if not isinstance(row, (list, tuple)):
            raise CatalogProjectionError("catalog_projection_invalid")
        invalid_length = len(row) != len(fields) if exact else len(row) < len(fields)
        if invalid_length:
            raise CatalogProjectionError("catalog_projection_invalid")
    except TypeError as exc:
        raise CatalogProjectionError("catalog_projection_invalid") from exc


def _projection_value(row: Any, key: str, index: int) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    if not isinstance(row, (list, tuple)):
        raise CatalogProjectionError("catalog_projection_invalid")
    try:
        return row[index]
    except (IndexError, KeyError, TypeError) as exc:
        raise CatalogProjectionError("catalog_projection_invalid") from exc


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return tuple(item for item in stripped[1:-1].split(",") if item)
        if not stripped:
            return ()
    return (value,)


def _sql_tokens(value: str | None) -> list[str]:
    text = str(value or "")
    tokens: list[str] = []
    index = 0
    parentheses = 0
    operators = (
        "->>", "!~*", "!~", "~*", "::", "<=", ">=", "<>", "!=", ":=", "||", "&&",
    )
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("--", index):
            newline = text.find("\n", index + 2)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                return []
            index = end + 2
            continue
        if char in {"'", '"'}:
            quote = char
            end = index + 1
            while end < len(text):
                if text[end] == quote:
                    if end + 1 < len(text) and text[end + 1] == quote:
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
            if end > len(text) or not text[index:end].endswith(quote):
                return []
            tokens.append(text[index:end])
            index = end
            continue
        matched = next((operator for operator in operators if text.startswith(operator, index)), None)
        if matched:
            tokens.append(matched)
            index += len(matched)
            continue
        if char.isalpha() or char in {"_", "$"}:
            end = index + 1
            while end < len(text) and (text[end].isalnum() or text[end] in {"_", "$"}):
                end += 1
            tokens.append(text[index:end])
            index = end
            continue
        if char.isdigit() or (char == "." and index + 1 < len(text) and text[index + 1].isdigit()):
            end = index + 1
            while end < len(text) and (text[end].isdigit() or text[end] in {".", "_"}):
                end += 1
            tokens.append(text[index:end])
            index = end
            continue
        if char == "(":
            parentheses += 1
        elif char == ")":
            parentheses -= 1
            if parentheses < 0:
                return []
        tokens.append(char)
        index += 1
    return tokens if parentheses == 0 else []


def _lower_identifier(token: str) -> str:
    return token.lower() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", token) else token


def _strip_outer_sql_parentheses(tokens: list[str]) -> list[str]:
    while len(tokens) >= 2 and tokens[0] == "(" and tokens[-1] == ")":
        depth = 0
        encloses_all = True
        for position, token in enumerate(tokens):
            if token == "(":
                depth += 1
            elif token == ")":
                depth -= 1
                if depth == 0 and position != len(tokens) - 1:
                    encloses_all = False
                    break
                if depth < 0:
                    encloses_all = False
                    break
        if encloses_all and depth == 0:
            tokens = tokens[1:-1]
        else:
            break
    return tokens


def _collapse_atom_parentheses(tokens: list[str]) -> list[str]:
    while len(tokens) >= 3 and tokens[0] == "(" and tokens[2] == ")" and tokens[1] not in {"(", ")", ","}:
        tokens = [tokens[1], *tokens[3:]]
    return tokens


def _rewrite_common_sql(tokens: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(tokens):
        if index + 2 < len(tokens) and tokens[index].lower() == "now" and tokens[index + 1] == "(" and tokens[index + 2] == ")":
            result.append("current_timestamp")
            index += 3
            continue
        if (
            index + 4 < len(tokens)
            and tokens[index] == "="
            and tokens[index + 1].lower() == "any"
            and tokens[index + 2] == "("
            and tokens[index + 3].lower() == "array"
            and tokens[index + 4] == "["
        ):
            depth = 1
            end = index + 5
            while end < len(tokens) and depth:
                if tokens[end] == "[":
                    depth += 1
                elif tokens[end] == "]":
                    depth -= 1
                end += 1
            array_items = tokens[index + 5 : end - 1] if depth == 0 else []
            item: list[str] = []
            valid_items = bool(array_items)
            normalized_items: list[str] = []
            for array_token in [*array_items, ","]:
                if array_token == ",":
                    normalized = _restricted_array_item(item)
                    if normalized is None:
                        valid_items = False
                    else:
                        normalized_items.extend(normalized)
                        normalized_items.append(",")
                    item = []
                else:
                    item.append(array_token)
            tail = end
            if (
                tail + 3 < len(tokens)
                and tokens[tail] == "::"
                and tokens[tail + 1].lower() == "text"
                and tokens[tail + 2] == "["
                and tokens[tail + 3] == "]"
            ):
                tail += 4
            if depth == 0 and tail < len(tokens) and tokens[tail] == ")" and valid_items:
                result.extend(["in", "("])
                result.extend(normalized_items[:-1])
                result.append(")")
                index = tail + 1
                continue
        result.append(tokens[index])
        index += 1
    return result


def _normal_type(type_name: str | None) -> str:
    text = re.sub(r"\s+", " ", str(type_name or "").strip().lower()).replace("pg_catalog.", "")
    if text == "character varying":
        return "varchar"
    if text == "character":
        return "char"
    match = re.fullmatch(r"character\((\d+)\)", text)
    if match:
        return f"char({match.group(1)})"
    return {
        "timestamp with time zone": "timestamptz",
        "timestamp without time zone": "timestamp",
        "double precision": "float8",
        "real": "float4",
        "int2": "smallint",
        "int4": "integer",
        "int8": "bigint",
        "bool": "boolean",
    }.get(text, text)


def _column_type_map(table: Any) -> dict[str, str]:
    if isinstance(table, TableSpec):
        return {column.name: _normal_type(column.type_name) for column in table.columns}
    if isinstance(table, Mapping):
        return {str(name): _normal_type(value) for name, value in table.items()}
    if isinstance(table, (list, tuple)):
        return {
            str(item[0]): _normal_type(str(item[1]))
            for item in table
            if isinstance(item, (list, tuple)) and len(item) >= 2
        }
    return {}


def _cast_target(tokens: list[str], start: int) -> tuple[str | None, int]:
    if start >= len(tokens):
        return None, start
    index = start
    parts: list[str] = []
    if index + 2 < len(tokens) and tokens[index].lower() == "pg_catalog" and tokens[index + 1] == ".":
        index += 2
    if index >= len(tokens) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", tokens[index]):
        return None, start
    parts.append(tokens[index])
    index += 1
    if index + 1 < len(tokens) and tokens[index].lower() == "varying":
        parts.append(tokens[index])
        index += 1
    if index < len(tokens) and tokens[index] == "(":
        depth = 0
        while index < len(tokens):
            parts.append(tokens[index])
            if tokens[index] == "(":
                depth += 1
            elif tokens[index] == ")":
                depth -= 1
                if depth == 0:
                    index += 1
                    break
            index += 1
    if index < len(tokens) and tokens[index] == "[":
        if index + 1 >= len(tokens) or tokens[index + 1] != "]":
            return None, start
        parts.extend(["[", "]"])
        index += 2
    return _normal_type(" ".join(parts)), index


def _is_literal_token(token: str) -> bool:
    return token.startswith("'") or bool(re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", token))


def _restricted_array_item(tokens: list[str]) -> list[str] | None:
    if len(tokens) == 1 and (_is_literal_token(tokens[0]) or tokens[0].lower() == "null"):
        return list(tokens)
    if (
        len(tokens) == 3
        and tokens[0].startswith("'")
        and tokens[1] == "::"
        and tokens[2].lower() == "text"
    ):
        return [tokens[0]]
    return None


def _relabel_is_proven(source_type: str, target_type: str, next_token: str | None) -> bool:
    source = _normal_type(source_type)
    target = _normal_type(target_type)
    if source == target:
        return True
    if target == "text" and source == "varchar" and next_token in {"~", "!~", "~*", "!~*"}:
        return True
    if target == "text" and source.startswith("char(") and next_token in {"~", "!~", "~*", "!~*"}:
        return True
    return False


def canonicalize_check_expression(expression: str | None, table: Any = None) -> tuple[str, ...]:
    """Apply only proven type-aware CHECK deparse no-ops."""

    tokens = _sql_tokens(expression)
    if expression and not tokens:
        return ("<invalid_sql>",)
    tokens = _rewrite_common_sql(tokens)
    types = _column_type_map(table)
    result: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "::" and result:
            target, end = _cast_target(tokens, index + 1)
            if target is not None:
                source = result[-1]
                next_token = tokens[end] if end < len(tokens) else None
                source_type = types.get(source) if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", source) else None
                if source.startswith("'") and _normal_type(target) == "text":
                    index = end
                    continue
                if source_type and _relabel_is_proven(source_type, target, next_token):
                    index = end
                    continue
        result.append(_lower_identifier(token))
        index += 1
    return tuple(_lower_identifier(item) for item in _collapse_atom_parentheses(_strip_outer_sql_parentheses(result)))


_canonicalize_check_expression = canonicalize_check_expression


def _semantic_sql_tokens(value: str | None, *, expression: bool = False) -> tuple[str, ...]:
    tokens = _rewrite_common_sql(_sql_tokens(value))
    if expression:
        tokens = _collapse_atom_parentheses(_strip_outer_sql_parentheses(tokens))
    return tuple(_lower_identifier(token) for token in tokens)


def _canonical_default(value: str | None, type_name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    tokens = _sql_tokens(value)
    if len(tokens) >= 3 and _is_literal_token(tokens[0]) and tokens[1] == "::":
        target, end = _cast_target(tokens, 2)
        if target is not None and end == len(tokens) and _normal_type(target) == _normal_type(type_name):
            tokens = [tokens[0]]
    return tuple(_lower_identifier(item) for item in tokens)


def _canonical_proconfig(value: Sequence[str] | None) -> tuple[str, ...]:
    result: list[str] = []
    for item in value or ():
        text = str(item)
        if text.lower().startswith("search_path="):
            path = ", ".join(part.strip() for part in text.split("=", 1)[1].split(","))
            result.append("search_path=" + path)
        else:
            result.append(text)
    return tuple(result)


def _fetch_public_relations(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
select c.oid as relation_oid, c.relname as relation_name, c.relkind,
       c.relpersistence, c.relispartition, owner.rolname as owner
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_roles owner on owner.oid = c.relowner
where n.nspname = 'public'
order by c.relname, c.oid
"""
    ).fetchall()
    fields = ("relation_oid", "relation_name", "relkind", "relpersistence", "relispartition", "owner")
    result: list[dict[str, Any]] = []
    for row in rows:
        _require_projection(row, fields)
        if (
            type(_row_value(row, "relation_oid")) is not int
            or not isinstance(_row_value(row, "relation_name"), str)
            or not isinstance(_row_value(row, "relkind"), str)
            or not isinstance(_row_value(row, "relpersistence"), str)
            or type(_row_value(row, "relispartition")) is not bool
            or not isinstance(_row_value(row, "owner"), str)
        ):
            raise CatalogProjectionError("catalog_projection_invalid")
        result.append(
            {
                "oid": _row_value(row, "relation_oid"),
                "name": str(_row_value(row, "relation_name")),
                "relkind": _row_value(row, "relkind"),
                "relpersistence": _row_value(row, "relpersistence"),
                "relispartition": bool(_row_value(row, "relispartition")),
                "owner": _row_value(row, "owner"),
            }
        )
    return result


def _fetch_public_tables(
    connection: Any,
    relations: Sequence[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    relations = list(relations) if relations is not None else _fetch_public_relations(connection)
    tables = {
        relation["name"]: {"relation": relation, "columns": [], "constraints": []}
        for relation in relations
        if relation["relkind"] in {"r", "p"}
    }
    relation_by_oid = {table["relation"]["oid"]: table for table in tables.values()}
    rows = connection.execute(
        """
select c.oid as relation_oid, a.attnum, a.attname,
       pg_catalog.format_type(a.atttypid, a.atttypmod) as type_name,
       a.attnotnull, pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) as column_default,
       a.attidentity, a.attgenerated
from pg_catalog.pg_class c
join pg_catalog.pg_namespace n on n.oid = c.relnamespace
join pg_catalog.pg_attribute a on a.attrelid = c.oid
left join pg_catalog.pg_attrdef ad
  on ad.adrelid = a.attrelid and ad.adnum = a.attnum
where n.nspname = 'public'
  and pg_catalog.left(c.relname, 5) = 'sqag_'
  and c.relkind in ('r', 'p')
  and a.attnum > 0
  and not a.attisdropped
order by c.relname, a.attnum
"""
    ).fetchall()
    column_fields = (
        "relation_oid", "attnum", "attname", "type_name", "attnotnull",
        "column_default", "attidentity", "attgenerated",
    )
    for row in rows:
        _require_projection(row, column_fields)
        if (
            type(_row_value(row, "relation_oid")) is not int
            or type(_row_value(row, "attnum")) is not int
            or not isinstance(_row_value(row, "attname"), str)
            or not isinstance(_row_value(row, "type_name"), str)
            or type(_row_value(row, "attnotnull")) is not bool
            or (_row_value(row, "column_default") is not None and not isinstance(_row_value(row, "column_default"), str))
            or not isinstance(_row_value(row, "attidentity"), str)
            or not isinstance(_row_value(row, "attgenerated"), str)
        ):
            raise CatalogProjectionError("catalog_projection_invalid")
        table = relation_by_oid.get(_row_value(row, "relation_oid"))
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

    rows = connection.execute(
        """
select c.conrelid as relation_oid, c.contype,
       array(
         select local_attribute.attname
          from pg_catalog.unnest(c.conkey) with ordinality as local_key(attnum, ordinal)
         join pg_catalog.pg_attribute local_attribute
           on local_attribute.attrelid = c.conrelid
          and local_attribute.attnum = local_key.attnum
         order by local_key.ordinal
       ) as columns,
       array(
         select foreign_attribute.attname
          from pg_catalog.unnest(c.confkey) with ordinality as foreign_key(attnum, ordinal)
         join pg_catalog.pg_attribute foreign_attribute
           on foreign_attribute.attrelid = c.confrelid
          and foreign_attribute.attnum = foreign_key.attnum
         order by foreign_key.ordinal
       ) as referenced_columns,
       referenced_table.relname as referenced_table,
       referenced_namespace.nspname as referenced_schema,
       c.confmatchtype as match_type,
       c.confdeltype as on_delete, c.confupdtype as on_update,
       pg_catalog.pg_get_expr(c.conbin, c.conrelid) as expression,
       c.convalidated, c.condeferrable, c.condeferred
from pg_catalog.pg_constraint c
join pg_catalog.pg_class table_class on table_class.oid = c.conrelid
join pg_catalog.pg_namespace table_namespace on table_namespace.oid = table_class.relnamespace
left join pg_catalog.pg_class referenced_table on referenced_table.oid = c.confrelid
left join pg_catalog.pg_namespace referenced_namespace on referenced_namespace.oid = referenced_table.relnamespace
where table_namespace.nspname = 'public'
  and table_class.relname like 'sqag_' || chr(37)
order by table_class.relname, c.oid
"""
    ).fetchall()
    constraint_fields = (
        "relation_oid", "contype", "columns", "referenced_columns", "referenced_table",
        "referenced_schema", "match_type",
        "on_delete", "on_update", "expression", "convalidated", "condeferrable", "condeferred",
    )
    for row in rows:
        _require_projection(row, constraint_fields)
        if (
            type(_row_value(row, "relation_oid")) is not int
            or not isinstance(_row_value(row, "contype"), str)
            or (_row_value(row, "columns") is not None and not isinstance(_row_value(row, "columns"), (list, tuple)))
            or (_row_value(row, "referenced_columns") is not None and not isinstance(_row_value(row, "referenced_columns"), (list, tuple)))
            or (_row_value(row, "referenced_table") is not None and not isinstance(_row_value(row, "referenced_table"), str))
            or (_row_value(row, "referenced_schema") is not None and not isinstance(_row_value(row, "referenced_schema"), str))
            or not isinstance(_row_value(row, "match_type"), str)
            or not isinstance(_row_value(row, "on_delete"), str)
            or not isinstance(_row_value(row, "on_update"), str)
            or (_row_value(row, "expression") is not None and not isinstance(_row_value(row, "expression"), str))
            or type(_row_value(row, "convalidated")) is not bool
            or type(_row_value(row, "condeferrable")) is not bool
            or type(_row_value(row, "condeferred")) is not bool
        ):
            raise CatalogProjectionError("catalog_projection_invalid")
        table = relation_by_oid.get(_row_value(row, "relation_oid"))
        if table is not None:
            constraint_kind = str(_row_value(row, "contype"))
            raw_on_delete = _row_value(row, "on_delete")
            raw_on_update = _row_value(row, "on_update")
            on_delete = (
                "a"
                if constraint_kind != "f" or raw_on_delete in (None, "", "\x00", " ")
                else str(raw_on_delete)
            )
            on_update = (
                "a"
                if constraint_kind != "f" or raw_on_update in (None, "", "\x00", " ")
                else str(raw_on_update)
            )
            table["constraints"].append(
                {
                    "kind": constraint_kind,
                    "columns": tuple(str(item) for item in _as_tuple(_row_value(row, "columns"))),
                    "referenced_table": (
                        str(_row_value(row, "referenced_table"))
                        if _row_value(row, "referenced_table") is not None
                        else None
                    ),
                    "referenced_schema": (
                        str(_row_value(row, "referenced_schema"))
                        if constraint_kind == "f" and _row_value(row, "referenced_schema") is not None
                        else None
                    ),
                    "referenced_columns": tuple(str(item) for item in _as_tuple(_row_value(row, "referenced_columns"))),
                    "match_type": (
                        str(_row_value(row, "match_type"))
                        if constraint_kind == "f"
                        else "s"
                    ),
                    "on_delete": on_delete,
                    "on_update": on_update,
                    "expression": _row_value(row, "expression"),
                    "validated": bool(_row_value(row, "convalidated")),
                    "deferrable": bool(_row_value(row, "condeferrable")),
                    "deferred": bool(_row_value(row, "condeferred")),
                }
            )
    return tables


_INDEX_PROJECTION_FIELDS = (
    "indexrelid", "index_name", "table_name", "indisunique", "indisvalid",
    "indisready", "constraint_backed", "owner", "predicate", "key_definitions",
)


def _fetch_public_indexes(connection: Any) -> dict[str, dict[str, Any]]:
    """Fetch the exact locked projection for every public index."""

    rows = connection.execute(
        """
select index_info.indexrelid as indexrelid,
       index_class.relname as index_name,
       table_class.relname as table_name,
       index_info.indisunique as indisunique,
       index_info.indisvalid as indisvalid,
       index_info.indisready as indisready,
       exists(
         select 1 from pg_catalog.pg_constraint constraint_row
         where constraint_row.conindid = index_info.indexrelid
       ) as constraint_backed,
       index_owner.rolname as owner,
       pg_catalog.pg_get_expr(index_info.indpred, index_info.indrelid) as predicate,
       array(
         select pg_catalog.pg_get_indexdef(index_info.indexrelid, key_no, true)
         from pg_catalog.generate_series(1, index_info.indnatts) as key_no
         order by key_no
       ) as key_definitions
from pg_catalog.pg_index index_info
join pg_catalog.pg_class index_class on index_class.oid = index_info.indexrelid
join pg_catalog.pg_namespace index_namespace on index_namespace.oid = index_class.relnamespace
join pg_catalog.pg_class table_class on table_class.oid = index_info.indrelid
join pg_catalog.pg_roles index_owner on index_owner.oid = index_class.relowner
where index_namespace.nspname = 'public'
order by index_class.relname, index_class.oid
"""
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        _require_projection(row, _INDEX_PROJECTION_FIELDS, exact=True)
        values = {
            field: _projection_value(row, field, position)
            for position, field in enumerate(_INDEX_PROJECTION_FIELDS)
        }
        for field in ("indisunique", "indisvalid", "indisready", "constraint_backed"):
            if type(values[field]) is not bool:
                raise CatalogProjectionError("catalog_projection_invalid")
        if (
            type(values["indexrelid"]) is not int
            or not isinstance(values["key_definitions"], (list, tuple))
            or not all(isinstance(item, str) for item in values["key_definitions"])
        ):
            raise CatalogProjectionError("catalog_projection_invalid")
        if not isinstance(values["index_name"], str) or not isinstance(values["table_name"], str):
            raise CatalogProjectionError("catalog_projection_invalid")
        if not isinstance(values["owner"], str):
            raise CatalogProjectionError("catalog_projection_invalid")
        predicate = values["predicate"]
        if predicate is not None and not isinstance(predicate, str):
            raise CatalogProjectionError("catalog_projection_invalid")
        name = values["index_name"]
        result[name] = {
            "oid": values["indexrelid"],
            "name": name,
            "table_name": values["table_name"],
            "unique": values["indisunique"],
            "valid": values["indisvalid"],
            "ready": values["indisready"],
            "constraint_backed": values["constraint_backed"],
            "owner": values["owner"],
            "predicate": predicate,
            "key_definitions": tuple(values["key_definitions"]),
        }
    return result


def _fetch_public_triggers(connection: Any) -> tuple[dict[str, Any], ...]:
    rows = connection.execute(
        """
select t.oid as trigger_oid, t.tgname as trigger_name,
       table_namespace.nspname as table_schema, table_class.relname as table_name,
       t.tgtype, t.tgenabled,
       array(
         select trigger_attribute.attname
          from pg_catalog.unnest(t.tgattr) with ordinality as trigger_key(attnum, ordinal)
         join pg_catalog.pg_attribute trigger_attribute
           on trigger_attribute.attrelid = t.tgrelid
          and trigger_attribute.attnum = trigger_key.attnum
         order by trigger_key.ordinal
       ) as columns,
       function_namespace.nspname as function_schema,
       function_row.proname as function_name,
       pg_catalog.pg_get_function_identity_arguments(function_row.oid) as identity_arguments
from pg_catalog.pg_trigger t
join pg_catalog.pg_class table_class on table_class.oid = t.tgrelid
join pg_catalog.pg_namespace table_namespace on table_namespace.oid = table_class.relnamespace
join pg_catalog.pg_proc function_row on function_row.oid = t.tgfoid
join pg_catalog.pg_namespace function_namespace on function_namespace.oid = function_row.pronamespace
where table_namespace.nspname = 'public' and not t.tgisinternal
order by table_namespace.nspname, table_class.relname, t.tgname, t.oid
"""
    ).fetchall()
    fields = (
        "trigger_oid", "trigger_name", "table_schema", "table_name", "tgtype",
        "tgenabled", "columns", "function_schema", "function_name", "identity_arguments",
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        _require_projection(row, fields)
        if (
            type(_row_value(row, "trigger_oid")) is not int
            or not isinstance(_row_value(row, "trigger_name"), str)
            or not isinstance(_row_value(row, "table_schema"), str)
            or not isinstance(_row_value(row, "table_name"), str)
            or type(_row_value(row, "tgtype")) is not int
            or not isinstance(_row_value(row, "tgenabled"), str)
            or not isinstance(_row_value(row, "columns"), (list, tuple))
            or not isinstance(_row_value(row, "function_schema"), str)
            or not isinstance(_row_value(row, "function_name"), str)
            or not isinstance(_row_value(row, "identity_arguments"), str)
            or not all(isinstance(item, str) for item in _row_value(row, "columns"))
        ):
            raise CatalogProjectionError("catalog_projection_invalid")
        name = str(_row_value(row, "trigger_name"))
        result.append({
            "oid": _row_value(row, "trigger_oid"),
            "name": name,
            "table_schema": str(_row_value(row, "table_schema")),
            "table_name": str(_row_value(row, "table_name")),
            "tgtype": _row_value(row, "tgtype"),
            "enabled": str(_row_value(row, "tgenabled") or ""),
            "columns": tuple(str(item) for item in _as_tuple(_row_value(row, "columns"))),
            "routine_key": (
                str(_row_value(row, "function_schema")),
                str(_row_value(row, "function_name")),
                str(_row_value(row, "identity_arguments") or ""),
            ),
        })
    return tuple(result)


def _fetch_public_routines(
    connection: Any,
) -> tuple[dict[tuple[str, str, str], list[dict[str, Any]]], dict[tuple[str, str, str], list[dict[str, Any]]]]:
    rows = connection.execute(
        """
select p.oid as function_oid, n.nspname as schema_name, p.proname,
       p.prokind, pg_catalog.pg_get_function_identity_arguments(p.oid) as identity_arguments,
       pg_catalog.pg_get_function_result(p.oid) as result_type, p.prosecdef,
       p.provolatile, p.proparallel, p.proleakproof, p.proconfig,
       p.prosrc as function_body, pg_catalog.pg_get_functiondef(p.oid) as function_definition,
       language_row.lanname as language, owner.rolname as owner
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
join pg_catalog.pg_language language_row on language_row.oid = p.prolang
join pg_catalog.pg_roles owner on owner.oid = p.proowner
where n.nspname = 'public' and pg_catalog.left(p.proname, 5) = 'sqag_'
order by p.proname, identity_arguments, p.oid
"""
    ).fetchall()
    fields = (
        "function_oid", "schema_name", "proname", "prokind", "identity_arguments",
        "result_type", "prosecdef", "provolatile", "proparallel", "proleakproof",
        "proconfig", "function_body", "function_definition", "language", "owner",
    )
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        _require_projection(row, fields)
        if (
            type(_row_value(row, "function_oid")) is not int
            or not isinstance(_row_value(row, "schema_name"), str)
            or not isinstance(_row_value(row, "proname"), str)
            or not isinstance(_row_value(row, "prokind"), str)
            or not isinstance(_row_value(row, "identity_arguments"), str)
            or not isinstance(_row_value(row, "result_type"), str)
            or type(_row_value(row, "prosecdef")) is not bool
            or not isinstance(_row_value(row, "provolatile"), str)
            or not isinstance(_row_value(row, "proparallel"), str)
            or type(_row_value(row, "proleakproof")) is not bool
            or (_row_value(row, "proconfig") is not None and not isinstance(_row_value(row, "proconfig"), (list, tuple)))
            or not isinstance(_row_value(row, "function_body"), str)
            or not isinstance(_row_value(row, "function_definition"), str)
            or not isinstance(_row_value(row, "language"), str)
            or not isinstance(_row_value(row, "owner"), str)
        ):
            raise CatalogProjectionError("catalog_projection_invalid")
        key = (
            str(_row_value(row, "schema_name")),
            str(_row_value(row, "proname")),
            str(_row_value(row, "identity_arguments") or ""),
        )
        grouped.setdefault(key, []).append(
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
                "proconfig": _canonical_proconfig(_as_tuple(_row_value(row, "proconfig"))),
                "function_body": str(_row_value(row, "function_body") or ""),
                "function_definition": str(_row_value(row, "function_definition") or ""),
                "language": str(_row_value(row, "language") or ""),
                "owner": str(_row_value(row, "owner") or ""),
            }
        )

    acl_rows = connection.execute(
        """
select n.nspname as schema_name, p.proname,
       pg_catalog.pg_get_function_identity_arguments(p.oid) as identity_arguments,
       case when acl.grantee = 0 then 'PUBLIC'
            else coalesce(grantee_role.rolname, 'UNKNOWN') end as grantee,
       acl.privilege_type, acl.is_grantable
from pg_catalog.pg_proc p
join pg_catalog.pg_namespace n on n.oid = p.pronamespace
left join lateral pg_catalog.aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) acl on true
left join pg_catalog.pg_roles grantee_role
  on grantee_role.oid = acl.grantee and acl.grantee <> 0
where n.nspname = 'public' and pg_catalog.left(p.proname, 5) = 'sqag_'
order by p.proname, identity_arguments, grantee, acl.privilege_type
"""
    ).fetchall()
    acl_fields = ("schema_name", "proname", "identity_arguments", "grantee", "privilege_type", "is_grantable")
    acl_grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in acl_rows:
        _require_projection(row, acl_fields)
        grantee = _row_value(row, "grantee")
        if grantee is None:
            continue
        if (
            not isinstance(grantee, str)
            or not isinstance(_row_value(row, "schema_name"), str)
            or not isinstance(_row_value(row, "proname"), str)
            or not isinstance(_row_value(row, "identity_arguments"), str)
            or not isinstance(_row_value(row, "privilege_type"), str)
            or type(_row_value(row, "is_grantable")) is not bool
        ):
            raise CatalogProjectionError("catalog_projection_invalid")
        key = (
            str(_row_value(row, "schema_name")),
            str(_row_value(row, "proname")),
            str(_row_value(row, "identity_arguments") or ""),
        )
        acl_grouped.setdefault(key, []).append(
            {
                "grantee": str(grantee),
                "privilege": str(_row_value(row, "privilege_type")),
                "grantable": bool(_row_value(row, "is_grantable")),
            }
        )
    return grouped, acl_grouped


def _ledger_rows(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        "select sequence_no, migration_id, checksum_sha256, applied_at "
        "from public.sqag_schema_migrations order by sequence_no"
    ).fetchall()
    fields = ("sequence_no", "migration_id", "checksum_sha256", "applied_at")
    result: list[dict[str, Any]] = []
    for row in rows:
        _require_projection(row, fields)
        result.append(
            {
                "sequence_no": _row_value(row, "sequence_no", 0),
                "migration_id": _row_value(row, "migration_id", 1),
                "checksum_sha256": _row_value(row, "checksum_sha256", 2),
                "applied_at": _row_value(row, "applied_at", 3),
            }
        )
    return result


def _column_fingerprint(spec: ColumnSpec) -> tuple[Any, ...]:
    return (
        spec.name,
        _normal_type(spec.type_name),
        spec.nullable,
        _canonical_default(spec.default_sql, spec.type_name),
        spec.identity,
        spec.generated,
    )


def _constraint_fingerprint(spec: ConstraintSpec, table: Any = None) -> tuple[Any, ...]:
    """Use kind-specific identity; CHECK conkey is intentionally ignored."""

    common = (spec.validated, spec.deferrable, spec.deferred)
    if spec.kind == "c":
        return ("c", canonicalize_check_expression(spec.expression, table), *common)
    if spec.kind == "f":
        return (
            "f", spec.columns, spec.referenced_schema, spec.referenced_table,
            spec.referenced_columns, spec.match_type, spec.on_delete,
            spec.on_update, *common,
        )
    return (spec.kind, spec.columns, *common)


def _observed_constraint_fingerprint(observed: Mapping[str, Any], table: Any = None) -> tuple[Any, ...]:
    return _constraint_fingerprint(
        ConstraintSpec(
            kind=str(observed.get("kind") or ""),
            columns=tuple(str(item) for item in observed.get("columns") or ()),
            referenced_schema=observed.get("referenced_schema"),
            referenced_table=observed.get("referenced_table"),
            referenced_columns=tuple(str(item) for item in observed.get("referenced_columns") or ()),
            match_type=str(observed.get("match_type") or ""),
            on_delete=str(observed.get("on_delete") or "a"),
            on_update=str(observed.get("on_update") or "a"),
            expression=observed.get("expression"),
            validated=bool(observed.get("validated")),
            deferrable=bool(observed.get("deferrable")),
            deferred=bool(observed.get("deferred")),
        ),
        table,
    )


def _table_matches(table: dict[str, Any], spec: TableSpec) -> bool:
    relation = table["relation"]
    if (
        relation["name"] != spec.name
        or relation["relkind"] != "r"
        or relation["relpersistence"] != "p"
        or relation["relispartition"]
        or relation["owner"] != spec.owner
    ):
        return False
    actual_columns = tuple(
        (
            name,
            _normal_type(type_name),
            nullable,
            _canonical_default(default_sql, type_name),
            identity,
            generated,
        )
        for name, type_name, nullable, default_sql, identity, generated in table["columns"]
    )
    if actual_columns != tuple(_column_fingerprint(column) for column in spec.columns):
        return False
    expected = Counter(_constraint_fingerprint(item, spec) for item in spec.constraints)
    actual = Counter(_observed_constraint_fingerprint(item, spec) for item in table["constraints"])
    return actual == expected


def _index_matches(observed: Mapping[str, Any], spec: IndexSpec) -> bool:
    actual_keys = tuple(_semantic_sql_tokens(item, expression=True) for item in observed["key_definitions"])
    expected_keys = tuple(_semantic_sql_tokens(item, expression=True) for item in spec.key_semantics)
    actual_predicate = (
        _semantic_sql_tokens(observed["predicate"], expression=True)
        if observed["predicate"] is not None
        else ()
    )
    expected_predicate = (
        _semantic_sql_tokens(spec.predicate, expression=True)
        if spec.predicate is not None
        else ()
    )
    return (
        observed["name"] == spec.name
        and observed["table_name"] == spec.table_name
        and observed["owner"] == spec.owner
        and observed["unique"] is spec.unique
        and observed["constraint_backed"] is False
        and observed["valid"] is True
        and observed["ready"] is True
        and len(actual_keys) == len(expected_keys)
        and actual_keys == expected_keys
        and actual_predicate == expected_predicate
    )


def _trigger_events(tgtype: int) -> tuple[str, ...]:
    result: list[str] = []
    if tgtype & 4:
        result.append("insert")
    if tgtype & 8:
        result.append("delete")
    if tgtype & 16:
        result.append("update")
    if tgtype & 32:
        result.append("truncate")
    return tuple(result)


def _trigger_matches(observed: Mapping[str, Any], spec: TriggerSpec) -> bool:
    tgtype = observed["tgtype"]
    timing = "before" if tgtype & 2 else "instead" if tgtype & 64 else "after"
    level = "row" if tgtype & 1 else "statement"
    return (
        observed["name"] == spec.name
        and observed["table_schema"] == "public"
        and observed["table_name"] == spec.table_name
        and timing == spec.timing
        and _trigger_events(tgtype) == spec.events
        and observed["columns"] == spec.columns
        and level == spec.level
        and observed["enabled"] == spec.enabled
        and observed["routine_key"] == spec.routine_key
    )


def _trigger_identity(observed: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(observed["table_schema"]),
        str(observed["table_name"]),
        str(observed["name"]),
    )


_FUNCTION_BODY_RE_TEMPLATE = (
    r"create\s+(?:or\s+replace\s+)?function\s+"
    r"(?:(?:public)\s*\.\s*)?{name}\s*\([^)]*\).*?\bas\s+"
    r"(?P<delimiter>\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$)"
)


def _canonical_routine_body(routine: RoutineSpec, migrations: Sequence[Migration]) -> str:
    migration = next((item for item in migrations if item.migration_id == routine.source_migration_id), None)
    if migration is None:
        raise MigrationSafetyError(f"migration_source_missing:{routine.source_migration_id}")
    source = canonical_migration_payload(migration.path).decode("utf-8")
    matches = list(
        re.finditer(
            _FUNCTION_BODY_RE_TEMPLATE.format(name=re.escape(routine.name)),
            source,
            flags=re.IGNORECASE | re.DOTALL,
        )
    )
    if len(matches) != 1:
        raise MigrationSafetyError(f"routine_source_invalid:{routine.name}")
    match = matches[0]
    delimiter = match.group("delimiter")
    body_end = source.find(delimiter, match.end())
    if body_end < 0:
        raise MigrationSafetyError(f"routine_source_invalid:{routine.name}")
    body = source[match.end() : body_end]
    if not _semantic_sql_tokens(body):
        raise MigrationSafetyError(f"routine_source_invalid:{routine.name}")
    return body


_SQL_RELATION_RE = re.compile(
    r"\b(?:delete\s+from|from|join|into|update)\s+"
    r"(?:public\s*\.\s*)?([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)
_UNQUALIFIED_SQL_RELATION_RE = re.compile(
    r"\b(?:delete\s+from|from|join|into|update)\s+"
    r"(?!public\s*\.)((?:sqag_)[a-z0-9_]*)",
    re.IGNORECASE,
)


def _routine_matches(
    observed: Mapping[str, Any],
    spec: RoutineSpec,
    acl_rows: Sequence[Mapping[str, Any]],
    migrations: Sequence[Migration],
) -> bool:
    if (
        observed["schema_name"] != spec.schema_name
        or observed["name"] != spec.name
        or observed["identity_arguments"] != spec.identity_arguments
        or observed["prokind"] != "f"
        or observed["result_type"].lower() != spec.result_type.lower()
        or observed["language"] != spec.language
        or observed["owner"] != spec.owner
        or observed["security_definer"] is not spec.security_definer
        or observed["volatility"] != spec.volatility
        or observed["parallel"] != spec.parallel
        or observed["leakproof"] is not spec.leakproof
        or _canonical_proconfig(observed["proconfig"]) != _canonical_proconfig(spec.proconfig)
    ):
        return False
    try:
        expected_body = _canonical_routine_body(spec, migrations)
    except (MigrationSafetyError, UnicodeDecodeError):
        return False
    if _semantic_sql_tokens(observed["function_body"]) != _semantic_sql_tokens(expected_body):
        return False
    if spec.referenced_relations:
        relations = {
            match.group(1).lower()
            for match in _SQL_RELATION_RE.finditer(observed["function_definition"])
            if match.group(1).lower().startswith("sqag_")
        }
        if relations != set(spec.referenced_relations) or _UNQUALIFIED_SQL_RELATION_RE.search(observed["function_definition"]):
            return False
    if spec.direct_acl:
        expected_acl = set(spec.direct_acl)
        actual_acl = {
            (item["grantee"], item["privilege"], item["grantable"])
            for item in acl_rows
        }
        if not expected_acl.issubset(actual_acl):
            return False
        if any(item[0] not in {"sqag_migrator"} and item not in expected_acl for item in actual_acl):
            return False
        if any(item[0] == "sqag_migrator" and item[1:] != ("EXECUTE", False) for item in actual_acl):
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
    ledger_exists: bool,
    relations: Sequence[Mapping[str, Any]],
    tables: Mapping[str, dict[str, Any]],
    indexes: Mapping[str, dict[str, Any]],
    triggers: Sequence[Mapping[str, Any]],
    routines: Mapping[tuple[str, str, str], list[dict[str, Any]]],
    routine_acls: Mapping[tuple[str, str, str], list[dict[str, Any]]],
) -> list[str]:
    applied_specs = MIGRATION_OBJECTS[:applied_count]
    pending_specs = MIGRATION_OBJECTS[applied_count:]
    applied_tables = {table.name for item in applied_specs for table in item.tables}
    pending_tables = {table.name for item in pending_specs for table in item.tables}
    pending_indexes = {index.name for item in pending_specs for index in item.indexes}
    pending_triggers = {
        ("public", trigger.table_name, trigger.name): trigger
        for item in pending_specs
        for trigger in item.triggers
    }
    pending_routines = {routine.key for item in pending_specs for routine in item.routines}

    relation_by_name = {str(relation["name"]): relation for relation in relations}
    index_by_oid = {value["oid"]: value for value in indexes.values()}
    triggers_by_identity: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for observed_trigger in triggers:
        triggers_by_identity.setdefault(_trigger_identity(observed_trigger), []).append(observed_trigger)
    blockers: list[str] = []
    known_relations = EXPECTED_TABLES | EXPECTED_INDEXES | {LEDGER_TABLE}
    for relation in relations:
        name = str(relation["name"])
        if not name.startswith("sqag_"):
            continue
        if name in known_relations:
            continue
        index = index_by_oid.get(relation["oid"], {})
        if (
            relation["relkind"] in {"i", "I"}
            and index.get("constraint_backed") is True
            and index.get("table_name") in EXPECTED_TABLES | {LEDGER_TABLE}
        ):
            continue
        blockers.append(f"managed_namespace_extra:relation:public.{name}")

    for table_name in sorted(applied_tables):
        spec = _effective_table_spec(table_name, applied_count)
        relation = relation_by_name.get(table_name)
        observed = tables.get(table_name)
        if relation is None:
            blockers.append(f"applied_prefix_missing:table:{_object_key('table', spec)}")
        elif observed is None or not _table_matches(observed, spec):
            blockers.append(f"applied_prefix_drift:table:{_object_key('table', spec)}")

    pending_table_names = sorted(pending_tables & set(relation_by_name))
    if ledger_exists and applied_count == 0:
        if pending_table_names:
            blockers.append(
                "schema_ledger_inconsistent_unapplied_tables:" + ",".join(pending_table_names)
            )
    else:
        for table_name in pending_table_names:
            blockers.append(f"pending_suffix_present:table:{_object_key('table', TABLE_SPECS_BY_NAME[table_name])}")

    for item in pending_specs:
        for mutation in item.table_mutations:
            table = tables.get(mutation.table_name)
            if table is None:
                continue
            present = {column[0] for column in table["columns"]}
            for column_name in mutation.added_columns:
                if column_name in present:
                    blockers.append(f"pending_suffix_present:column:public.{mutation.table_name}.{column_name}")

    for item in applied_specs:
        for spec in item.indexes:
            observed = indexes.get(spec.name)
            if observed is None:
                category = "applied_prefix_drift" if spec.name in relation_by_name else "applied_prefix_missing"
                blockers.append(f"{category}:index:{_object_key('index', spec)}")
            elif not _index_matches(observed, spec):
                blockers.append(f"applied_prefix_drift:index:{_object_key('index', spec)}")
    for index_name in sorted(pending_indexes):
        if index_name in relation_by_name:
            blockers.append(f"pending_suffix_present:index:{_object_key('index', INDEX_SPECS_BY_NAME[index_name])}")

    for item in applied_specs:
        for spec in item.triggers:
            trigger_key = ("public", spec.table_name, spec.name)
            candidates = triggers_by_identity.get(trigger_key, [])
            if not candidates:
                blockers.append(f"applied_prefix_missing:trigger:{_object_key('trigger', spec)}")
            elif len(candidates) != 1 or not _trigger_matches(candidates[0], spec):
                blockers.append(f"applied_prefix_drift:trigger:{_object_key('trigger', spec)}")
    for trigger_key, spec in sorted(pending_triggers.items()):
        if trigger_key in triggers_by_identity:
            blockers.append(f"pending_suffix_present:trigger:{_object_key('trigger', spec)}")

    for item in applied_specs:
        for spec in item.routines:
            candidates = routines.get(spec.key, [])
            if not candidates:
                blockers.append(f"applied_prefix_missing:routine:{_object_key('routine', spec)}")
            elif len(candidates) != 1 or not _routine_matches(candidates[0], spec, routine_acls.get(spec.key, []), migrations):
                blockers.append(f"applied_prefix_drift:routine:{_object_key('routine', spec)}")
    for routine_key in sorted(pending_routines):
        if routine_key in routines:
            blockers.append(f"pending_suffix_present:routine:{_object_key('routine', ROUTINE_SPECS_BY_KEY[routine_key])}")

    for observed in triggers:
        trigger_key = _trigger_identity(observed)
        if observed["name"].startswith("sqag_") and trigger_key not in EXPECTED_TRIGGER_KEYS:
            blockers.append(
                f"managed_namespace_extra:trigger:{trigger_key[0]}.{trigger_key[1]}.{trigger_key[2]}"
            )
    for routine_key in routines:
        if routine_key[1].startswith("sqag_") and routine_key[1:] not in EXPECTED_ROUTINE_KEYS:
            blockers.append(f"managed_namespace_extra:routine:{routine_key[0]}.{routine_key[1]}({routine_key[2]})")
    return blockers


def _sort_blockers(blockers: Sequence[str]) -> list[str]:
    ledger_priority = {
        "existing_schema_without_trusted_ledger": 10,
        "ledger_schema_invalid": 20,
        "unknown_or_out_of_order_migration": 30,
        "unexpected_applied_migration": 30,
    }
    object_priority = {"table": 10, "column": 20, "index": 30, "trigger": 40, "routine": 50, "relation": 60}
    category_priority = {"managed_namespace_extra": 10, "applied_prefix_missing": 20, "applied_prefix_drift": 30, "pending_suffix_present": 40}

    def sort_key(blocker: str) -> tuple[Any, ...]:
        parts = blocker.split(":", 2)
        if blocker == "catalog_projection_invalid":
            return (0, 5, 0, 0, blocker)
        if parts[0] in ledger_priority:
            return (0, ledger_priority[parts[0]], 0, 0, blocker)
        if parts[0] == "checksum_drift":
            rank = next(
                (position for position, item in enumerate(MIGRATION_OBJECTS) if len(parts) == 2 and item.migration_id == parts[1]),
                len(MIGRATION_OBJECTS),
            )
            return (0, 40, rank, 0, blocker)
        if len(parts) == 3 and parts[0] in category_priority:
            kind, key = parts[1], parts[2]
            return (
                1,
                _OBJECT_PROVENANCE_RANK.get((kind, key), len(MIGRATION_OBJECTS)),
                object_priority.get(kind, 99),
                category_priority[parts[0]],
                key,
            )
        return (2, 0, 0, 0, blocker)

    return sorted(set(blockers), key=sort_key)


def _valid_ledger_table(tables: Mapping[str, dict[str, Any]]) -> bool:
    table = tables.get(LEDGER_TABLE)
    return table is not None and _table_matches(table, _LEDGER_TABLE_SPEC)


def inspect_postgres_migrations(connection: Any, migrations: Sequence[Migration]) -> dict[str, Any]:
    expected_ids = [migration.migration_id for migration in migrations]
    expected_head = expected_ids[-1] if expected_ids else None
    try:
        relations = _fetch_public_relations(connection)
        tables = _fetch_public_tables(connection, relations)
        indexes = _fetch_public_indexes(connection)
        triggers = _fetch_public_triggers(connection)
        routines, routine_acls = _fetch_public_routines(connection)
    except CatalogProjectionError:
        return {
            "status": "unsafe",
            "safeToApply": False,
            "ledgerState": "unknown",
            "expectedHead": expected_head,
            "appliedHead": None,
            "appliedMigrationIds": None,
            "pendingMigrationIds": None,
            "blockers": ["catalog_projection_invalid"],
        }

    ledger_relation = next((relation for relation in relations if relation["name"] == LEDGER_TABLE), None)
    ledger_exists = ledger_relation is not None
    blockers: list[str] = []
    applied_rows: list[dict[str, Any]] = []
    applied_count: int | None = None

    if ledger_exists:
        if not _valid_ledger_table(tables):
            try:
                empty_ledger = not _ledger_rows(connection)
            except Exception:
                empty_ledger = False
            expected_tables_present = sorted(
                EXPECTED_TABLES & {str(relation["name"]) for relation in relations}
            )
            if empty_ledger and expected_tables_present:
                blockers.append(
                    "schema_ledger_inconsistent_unapplied_tables:" + ",".join(expected_tables_present)
                )
            else:
                blockers.append("ledger_schema_invalid")
        else:
            try:
                applied_rows = _ledger_rows(connection)
            except Exception:
                blockers.append("ledger_schema_invalid")
            if not blockers:
                if len(applied_rows) > len(migrations):
                    blockers.append("unexpected_applied_migration")
                else:
                    for position, row in enumerate(applied_rows):
                        expected = migrations[position]
                        if (
                            type(row["sequence_no"]) is not int
                            or not isinstance(row["migration_id"], str)
                            or row["sequence_no"] != expected.sequence_no
                            or row["migration_id"] != expected.migration_id
                            or not isinstance(row["checksum_sha256"], str)
                            or row["checksum_sha256"] != expected.checksum_sha256
                            or row["applied_at"] is None
                        ):
                            if (
                                isinstance(row["checksum_sha256"], str)
                                and row["sequence_no"] == expected.sequence_no
                                and row["migration_id"] == expected.migration_id
                                and row["checksum_sha256"] != expected.checksum_sha256
                            ):
                                blockers.append(f"checksum_drift:{expected.migration_id}")
                            else:
                                blockers.append("unknown_or_out_of_order_migration")
                            break
                if not blockers:
                    applied_count = len(applied_rows)
    else:
        # A missing ledger reserves only the SQAG-managed public namespace.
        # Unrelated provider/public objects do not authorise SQAG adoption and
        # are intentionally ignored; _schema_object_blockers still rejects
        # every known, premature, or unknown managed object.
        applied_count = 0

    if applied_count is not None and not blockers:
        blockers.extend(
            _schema_object_blockers(
                migrations,
                applied_count,
                ledger_exists=ledger_exists,
                relations=relations,
                tables=tables,
                indexes=indexes,
                triggers=triggers,
                routines=routines,
                routine_acls=routine_acls,
            )
        )

    if applied_count is None:
        applied_ids = None
        pending_ids = None
        applied_head = None
    else:
        applied_ids = [str(row["migration_id"]) for row in applied_rows]
        pending_ids = expected_ids[applied_count:]
        applied_head = applied_ids[-1] if applied_ids else None

    ordered = _sort_blockers(blockers)
    return {
        "status": "unsafe" if ordered else "ready",
        "safeToApply": not ordered,
        "ledgerState": "present" if ledger_exists else "missing",
        "expectedHead": expected_head,
        "appliedHead": applied_head,
        "appliedMigrationIds": applied_ids,
        "pendingMigrationIds": pending_ids,
        "blockers": ordered,
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


def _split_sql_statements(sql: str) -> tuple[str, ...]:
    """Split migration SQL without treating quoted-body semicolons as boundaries."""
    statements: list[str] = []
    start = 0
    index = 0
    quote: str | None = None
    dollar_quote: str | None = None
    line_comment = False
    block_comment = False
    while index < len(sql):
        if line_comment:
            if sql[index] == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if sql.startswith("*/", index):
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if dollar_quote is not None:
            if sql.startswith(dollar_quote, index):
                index += len(dollar_quote)
                dollar_quote = None
            else:
                index += 1
            continue
        if quote is not None:
            if sql[index] == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                else:
                    quote = None
                    index += 1
            else:
                index += 1
            continue
        if sql.startswith("--", index):
            line_comment = True
            index += 2
            continue
        if sql.startswith("/*", index):
            block_comment = True
            index += 2
            continue
        if sql[index] in {"'", '"'}:
            quote = sql[index]
            index += 1
            continue
        if sql[index] == "$":
            match = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[index:])
            if match:
                dollar_quote = match.group(0)
                index += len(dollar_quote)
                continue
        if sql[index] == ";":
            statement = sql[start:index].strip()
            if statement:
                statements.append(statement)
            start = index + 1
        index += 1
    statement = sql[start:].strip()
    if statement:
        statements.append(statement)
    return tuple(statements)


def execute_migration_sql(connection: Any, sql: str) -> None:
    marker = "-- SQAG_STATEMENT_BOUNDARY"
    for part in sql.split(marker):
        for statement in _split_sql_statements(part):
            connection.execute(statement)


def apply_postgres_migrations(connection: Any, migrations: Sequence[Migration]) -> dict[str, Any]:
    connection.execute("set local search_path to public, pg_catalog")
    connection.execute("select pg_catalog.pg_advisory_xact_lock(?)", (MIGRATION_LOCK_KEY,))
    before = inspect_postgres_migrations(connection, migrations)
    if not before["safeToApply"]:
        raise MigrationSafetyError(str(before["blockers"][0]))
    applied_count = len(before["appliedMigrationIds"] or ())
    if before["ledgerState"] == "missing":
        _create_ledger(connection)

    applied_now: list[str] = []
    for migration in migrations[applied_count:]:
        payload = canonical_migration_payload(migration.path)
        if sha256(payload).hexdigest() != migration.checksum_sha256:
            raise MigrationSafetyError(f"migration_source_changed_during_run:{migration.migration_id}")
        execute_migration_sql(connection, payload.decode("utf-8"))
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
