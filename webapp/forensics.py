"""Workspace-scoped generation evidence, support feedback, holds, and retention."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Callable, Iterable


EVIDENCE_RETENTION_YEARS = 3
MAX_GENERATION_MANIFEST_BYTES = 1024 * 1024
PRODUCTION_LOG_RETENTION_DAYS = 90
LOCAL_UAT_LOG_RETENTION_DAYS = 30
EVIDENCE_SCHEMA_VERSION = "swooshz.sqag.generation-evidence.v2"
FEEDBACK_RETENTION_POLICY_VERSION = "sqag.feedback-retention.v3"
FEEDBACK_STATUSES = {"open", "triaged", "in_progress", "resolved", "closed", "rejected", "duplicate"}
FEEDBACK_CATEGORIES = {"bug", "incorrect_output", "failed_process", "usability", "general"}
FEEDBACK_IMPACTS = {"low", "medium", "high", "blocking"}
FEEDBACK_LINK_CHOICES = {"automatic", "current", "manual", "none"}
NON_TERMINAL_RUN_STATES = {"received", "queued", "running"}
TERMINAL_RUN_STATES = {
    "blocked",
    "completed",
    "needs_confirmation",
    "needs_review",
    "completed_with_review_required",
    "degraded",
    "failed",
    "cancelled",
    "timed_out",
    "abandoned",
    "superseded",
}
RUN_STATES = NON_TERMINAL_RUN_STATES | TERMINAL_RUN_STATES
WORKSPACE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
OPAQUE_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
LEGAL_HOLD_TARGETS = {
    "sqag_generation_runs": ("run_id", "generation_run"),
    "sqag_generation_evidence": ("evidence_id", "generation_evidence"),
    "sqag_audit_events": ("event_id", "audit_event"),
    "sqag_feedback": ("feedback_id", "feedback"),
    "sqag_feedback_status_history": ("history_id", "feedback_status_history"),
    "sqag_telemetry_events": ("event_id", "telemetry_event"),
}
TELEMETRY_SOURCE_PRODUCT = "sqag"
TELEMETRY_EVENT_TYPES = frozenset({
    "generation", "validation", "ai_provider_attempt", "pricing_change",
    "profile_change", "publication", "download", "feedback", "security",
    "rate_limit", "abuse", "cancellation", "timeout", "abandonment",
    "supersession", "storage_staging", "storage_finalization",
    "storage_compensation", "configuration", "operator_action",
    "reconciliation", "retention", "legal_hold", "deletion", "backup",
    "restore",
})
TELEMETRY_EVENT_STATUSES = frozenset({
    "started", "queued", "running", "success", "failed", "blocked", "denied",
    "completed", "needs_confirmation", "needs_review",
    "completed_with_review_required", "degraded", "cancelled", "timed_out",
    "abandoned", "superseded", "available", "unavailable", "held", "deleted",
    "reconciled", "staged", "finalized", "compensated", "requested", "updated",
    "restored", "rate_limited",
})
TELEMETRY_FAILURE_CLASSES = frozenset({
    "missing_api_key", "timeout", "rate_limited", "upstream_unavailable",
    "http_error", "network_error", "invalid_json", "schema_validation_failed",
    "model_output_invalid", "provider_error", "generator_error", "configuration",
    "storage", "authorization", "unknown",
})
TELEMETRY_DECISIONS = frozenset({"allowed", "denied", "not_evaluated"})
TELEMETRY_PROVIDERS = frozenset({"openai", "deepseek"})
TELEMETRY_REASONING_LEVELS = frozenset({
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "standard",
})
TELEMETRY_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
TELEMETRY_IMMUTABLE_FIELDS = (
    "workspace_id", "event_id", "source_product", "source_sequence", "event_type",
    "event_status", "actor_tracking_id", "actor_key_version", "action_reference",
    "run_reference", "session_reference", "support_reference", "retry_lineage_id",
    "attempt_number", "provider", "model", "reasoning_level", "operation_route",
    "purpose", "failure_class", "duration_ms", "usage_available", "input_tokens",
    "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens",
    "cost_available", "estimated_cost", "actual_cost", "currency", "cost_version",
    "quota_decision", "rate_limit_decision", "abuse_decision", "deployment_revision",
    "occurred_at", "immutable_metadata_digest", "retention_expires_at",
    "original_retention_expires_at",
)
FEEDBACK_TRANSITIONS = {
    "open": {"triaged", "in_progress", "resolved", "closed", "rejected", "duplicate"},
    "triaged": {"in_progress", "resolved", "closed", "rejected", "duplicate"},
    "in_progress": {"triaged", "resolved", "closed", "rejected", "duplicate"},
    "resolved": {"in_progress", "closed"},
    "closed": {"in_progress"},
    "rejected": {"in_progress", "closed"},
    "duplicate": {"in_progress", "closed"},
}
FEEDBACK_CLOSED_STATES = {"resolved", "closed", "rejected", "duplicate"}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def add_calendar_years(value: dt.datetime, years: int = EVIDENCE_RETENTION_YEARS) -> dt.datetime:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, month=2, day=28)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def digest_json(value: Any) -> tuple[str, str]:
    body = canonical_json(value)
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def bounded_digest_json(
    value: Any, *, max_bytes: int = MAX_GENERATION_MANIFEST_BYTES
) -> tuple[str, str]:
    """Serialize a bounded canonical record without first building an oversized body."""
    chunks: list[str] = []
    size_bytes = 0
    encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    for chunk in encoder.iterencode(value):
        size_bytes += len(chunk.encode("utf-8"))
        if size_bytes > max_bytes:
            raise ValueError("Generation evidence exceeds the canonical size limit.")
        chunks.append(chunk)
    body = "".join(chunks)
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def safe_reference(value: Any, prefix: str = "") -> str:
    raw = str(value or "").strip()
    if not raw or not OPAQUE_REFERENCE_RE.fullmatch(raw):
        return ""
    return raw if not prefix or raw.startswith(prefix) else ""


def trusted_workspace_id(value: Any, *, allow_local: bool = False) -> str:
    raw = str(value or "")
    if raw != raw.strip() or not WORKSPACE_ID_RE.fullmatch(raw):
        raise ValueError("Trusted workspace identity is missing or invalid.")
    if raw in {".", ".."} or ".." in raw or raw == "local-workspace" and not allow_local:
        raise ValueError("Trusted workspace identity is missing or invalid.")
    return raw


def trusted_actor_tracking_id(value: Any, *, allow_local: bool = False) -> str:
    raw = safe_reference(value)
    if not raw or raw == "local-user" and not allow_local:
        raise ValueError("Trusted actor identity is missing or invalid.")
    return raw


def actor_key_version(value: str, fallback: str = "unknown") -> str:
    match = re.fullmatch(r"pid-([A-Za-z0-9._-]{1,32})-[a-f0-9]{24}", value)
    return match.group(1) if match else fallback


def safe_feedback_text(value: Any, limit: int = 4000) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value or ""))
    return re.sub(r"[ \t]+", " ", text).strip()[:limit]


def safe_manual_reference(value: Any) -> str:
    text = safe_feedback_text(value, 128)
    if text and not re.fullmatch(r"[A-Za-z0-9 ._:/#-]{1,128}", text):
        raise ValueError("Manual reference contains unsupported characters.")
    return text


def row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def safe_telemetry_label(value: Any, *, lowercase: bool = False) -> str:
    text = safe_feedback_text(value, 128)
    if lowercase:
        text = text.lower()
    return text if text and TELEMETRY_LABEL_RE.fullmatch(text) else ""


def safe_telemetry_route(value: Any) -> str:
    text = safe_feedback_text(value, 128)
    if not text:
        return ""
    if text.startswith("/"):
        return (
            text
            if re.fullmatch(r"/[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", text)
            else ""
        )
    return safe_telemetry_label(text)


def telemetry_integer(value: Any, *, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"Telemetry {field} is invalid.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Telemetry {field} is invalid.") from exc
    if parsed < 0:
        raise ValueError(f"Telemetry {field} is invalid.")
    return parsed


def telemetry_number(value: Any, *, field: str) -> int | float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"Telemetry {field} is invalid.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Telemetry {field} is invalid.") from exc
    if parsed < 0 or parsed != parsed or parsed in {float("inf"), float("-inf")}:
        raise ValueError(f"Telemetry {field} is invalid.")
    return parsed


@dataclass(frozen=True)
class RetentionResult:
    examined: int
    deleted: int
    held: int
    parents_processed: int = 0
    failed: int = 0
    review_required: int = 0
    scan_limit: int = 0
    scan_exhausted: bool = False
    standalone_examined: int = 0
    standalone_deleted: int = 0
    standalone_held: int = 0
    standalone_failed: int = 0
    receipt_examined: int = 0
    receipt_deleted: int = 0
    receipt_failed: int = 0
    publication_retained: int = 0
    telemetry_examined: int = 0
    telemetry_deleted: int = 0
    telemetry_held: int = 0
    telemetry_failed: int = 0


class RetentionGraphHeld(RuntimeError):
    """Signal that a retention graph became protected before commit."""


class RetentionPublicationDependency(RetentionGraphHeld):
    """Signal that a current publication still requires its generation graph."""


class TelemetryConflictError(ValueError):
    """Raised when an event identity is replayed with different immutable data."""


class TelemetryUnavailableError(RuntimeError):
    """Raised when source state cannot prove a safe telemetry read/write."""


class ForensicStore:
    """Database-neutral store; SQL adapters must support qmark-style parameters."""

    def __init__(
        self,
        connection: Any,
        workspace_id: str,
        actor_tracking_id: str,
        *,
        local_mode: bool = False,
        actor_key_version_value: str = "",
    ) -> None:
        self.connection = connection
        self.workspace_id = trusted_workspace_id(workspace_id, allow_local=local_mode)
        self.actor_tracking_id = trusted_actor_tracking_id(actor_tracking_id, allow_local=local_mode)
        self.actor_key_version = safe_reference(actor_key_version_value) or actor_key_version(actor_tracking_id)

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(12)}"

    @staticmethod
    def new_support_reference() -> str:
        return f"SQAG-FB-{secrets.token_hex(5).upper()}"

    @staticmethod
    def telemetry_event_id(prefix: str, *parts: Any) -> str:
        safe_prefix = safe_telemetry_label(prefix)[:32] or "telemetry"
        raw = "-".join(
            [safe_prefix]
            + [safe_feedback_text(part, 256) for part in parts if part not in (None, "")]
        )
        if safe_reference(raw):
            return raw
        return f"{safe_prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:48]}"

    def _retention(self, now: dt.datetime | None = None) -> tuple[str, str]:
        expiry = iso_timestamp(add_calendar_years(now or utc_now()))
        return expiry, expiry

    def _telemetry_retention(
        self,
        current: dt.datetime,
        *,
        run_reference: str = "",
        support_reference: str = "",
    ) -> tuple[str, str]:
        expiry, original = self._retention(current)
        linked_expiries: list[dt.datetime] = []
        for table, identifier, column in (
            ("sqag_generation_runs", run_reference, "run_id"),
            ("sqag_feedback", support_reference, "feedback_id"),
        ):
            if not identifier:
                continue
            row = row_dict(
                self.connection.execute(
                    f"select retention_expires_at, original_retention_expires_at from {table} "
                    f"where workspace_id = ? and {column} = ? limit 1",
                    (self.workspace_id, identifier),
                ).fetchone()
            )
            for field in ("retention_expires_at", "original_retention_expires_at"):
                parsed = parse_timestamp(row.get(field))
                if parsed is not None:
                    linked_expiries.append(parsed)
        if linked_expiries:
            inherited = iso_timestamp(max(linked_expiries))
            return inherited, inherited
        return expiry, original

    def _telemetry_source_state(self) -> dict[str, Any]:
        state = row_dict(
            self.connection.execute(
                "select * from sqag_telemetry_source_state where workspace_id = ? "
                "and source_product = ? limit 1",
                (self.workspace_id, TELEMETRY_SOURCE_PRODUCT),
            ).fetchone()
        )
        if not state:
            raise TelemetryUnavailableError("telemetry_source_state_missing")
        try:
            next_sequence = int(state.get("next_source_sequence"))
            high_watermark = int(state.get("high_watermark"))
        except (TypeError, ValueError) as exc:
            raise TelemetryUnavailableError("telemetry_source_state_invalid") from exc
        if (
            state.get("source_product") != TELEMETRY_SOURCE_PRODUCT
            or next_sequence < 1
            or high_watermark < 0
            or next_sequence != high_watermark + 1
            or safe_feedback_text(state.get("reconciliation_state"), 40) != "healthy"
        ):
            raise TelemetryUnavailableError("telemetry_source_state_inconsistent")
        maximum = self.connection.execute(
            "select max(source_sequence) as maximum_source_sequence "
            "from sqag_telemetry_events where workspace_id = ? and source_product = ?",
            (self.workspace_id, TELEMETRY_SOURCE_PRODUCT),
        ).fetchone()
        maximum_value = row_dict(maximum).get("maximum_source_sequence")
        if maximum_value is not None:
            try:
                if int(maximum_value) > high_watermark:
                    raise TelemetryUnavailableError("telemetry_high_watermark_behind")
            except (TypeError, ValueError) as exc:
                raise TelemetryUnavailableError("telemetry_source_sequence_invalid") from exc
        state["next_source_sequence"] = next_sequence
        state["high_watermark"] = high_watermark
        return state

    @staticmethod
    def _telemetry_value_matches(actual: Any, expected: Any, field: str) -> bool:
        if field in {"estimated_cost", "actual_cost"} and actual is not None and expected is not None:
            try:
                return float(actual) == float(expected)
            except (TypeError, ValueError):
                return False
        if actual is None or expected is None:
            return actual is None and expected is None
        return str(actual) == str(expected)

    def _telemetry_row_matches(
        self, existing: dict[str, Any], expected: dict[str, Any]
    ) -> bool:
        return all(
            self._telemetry_value_matches(existing.get(field), expected.get(field), field)
            for field in TELEMETRY_IMMUTABLE_FIELDS
            if field != "source_sequence"
        )

    def _public_telemetry_event(self, row: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "workspace_id", "event_id", "source_product", "source_sequence",
            "event_type", "event_status", "actor_tracking_id", "actor_key_version",
            "action_reference", "run_reference", "session_reference", "support_reference",
            "retry_lineage_id", "attempt_number", "provider", "model", "reasoning_level",
            "operation_route", "purpose", "failure_class", "duration_ms", "usage_available",
            "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
            "cache_write_tokens", "cost_available", "estimated_cost", "actual_cost",
            "currency", "cost_version", "quota_decision", "rate_limit_decision",
            "abuse_decision", "deployment_revision", "occurred_at",
            "immutable_metadata_digest", "legal_hold", "deletion_state",
        )
        return {field: row.get(field) for field in fields}

    def append_telemetry_event(
        self,
        event_type: str,
        event_status: str,
        *,
        event_id: str = "",
        action_reference: str = "",
        run_reference: str = "",
        session_reference: str = "",
        support_reference: str = "",
        retry_lineage_id: str = "",
        attempt_number: int | None = None,
        provider: str = "",
        model: str = "",
        reasoning_level: str = "",
        operation_route: str = "",
        purpose: str = "",
        failure_class: str = "",
        duration_ms: int | None = None,
        usage_available: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        cost_available: int | None = None,
        estimated_cost: int | float | None = None,
        actual_cost: int | float | None = None,
        currency: str = "",
        cost_version: str = "",
        quota_decision: str = "",
        rate_limit_decision: str = "",
        abuse_decision: str = "",
        deployment_revision: str = "",
        occurred_at: dt.datetime | str | None = None,
        immutable_metadata_digest: str = "",
        now: dt.datetime | None = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        event_type = safe_feedback_text(event_type, 40).lower()
        event_status = safe_feedback_text(event_status, 60).lower()
        if event_type not in TELEMETRY_EVENT_TYPES or event_status not in TELEMETRY_EVENT_STATUSES:
            raise ValueError("Telemetry event classification is invalid.")
        if event_id:
            safe_event_id = safe_reference(event_id)
            if not safe_event_id:
                raise ValueError("Telemetry event identity is invalid.")
        else:
            safe_event_id = self.new_id("telemetry")
        def label(value: Any, field: str, *, lowercase: bool = False) -> str | None:
            if value in (None, ""):
                return None
            result = safe_telemetry_label(value, lowercase=lowercase)
            if not result:
                raise ValueError(f"Telemetry {field} is invalid.")
            return result

        action = label(action_reference, "action_reference")
        run_ref = safe_reference(run_reference, "run-") if run_reference else ""
        if run_reference and not run_ref:
            raise ValueError("Telemetry run reference is invalid.")
        session_ref = label(session_reference, "session_reference")
        support_ref = label(support_reference, "support_reference")
        retry_ref = label(retry_lineage_id, "retry_lineage_id")
        attempt = telemetry_integer(attempt_number, field="attempt_number")
        if attempt is not None and attempt < 1:
            raise ValueError("Telemetry attempt_number is invalid.")
        provider_value = label(provider, "provider", lowercase=True)
        if provider_value and provider_value not in TELEMETRY_PROVIDERS:
            raise ValueError("Telemetry provider is invalid.")
        model_value = label(model, "model")
        reasoning_value = label(reasoning_level, "reasoning_level", lowercase=True)
        if reasoning_value and reasoning_value not in TELEMETRY_REASONING_LEVELS:
            raise ValueError("Telemetry reasoning level is invalid.")
        route_value = (
            safe_telemetry_route(operation_route)
            if operation_route not in (None, "")
            else None
        )
        if operation_route not in (None, "") and not route_value:
            raise ValueError("Telemetry operation_route is invalid.")
        purpose_value = label(purpose, "purpose")
        failure_value = safe_feedback_text(failure_class, 40).lower() if failure_class else ""
        if failure_value and failure_value not in TELEMETRY_FAILURE_CLASSES:
            raise ValueError("Telemetry failure class is invalid.")
        duration = telemetry_integer(duration_ms, field="duration_ms")
        usage = telemetry_integer(usage_available, field="usage_available")
        if usage is not None and usage not in (0, 1):
            raise ValueError("Telemetry usage availability is invalid.")
        token_values = {
            "input_tokens": telemetry_integer(input_tokens, field="input_tokens"),
            "output_tokens": telemetry_integer(output_tokens, field="output_tokens"),
            "total_tokens": telemetry_integer(total_tokens, field="total_tokens"),
            "cache_read_tokens": telemetry_integer(cache_read_tokens, field="cache_read_tokens"),
            "cache_write_tokens": telemetry_integer(cache_write_tokens, field="cache_write_tokens"),
        }
        if any(value is not None for value in token_values.values()):
            if usage == 0:
                raise ValueError("Telemetry usage availability conflicts with usage values.")
            usage = 1
        cost_available_value = telemetry_integer(cost_available, field="cost_available")
        if cost_available_value is not None and cost_available_value not in (0, 1):
            raise ValueError("Telemetry cost availability is invalid.")
        estimated = telemetry_number(estimated_cost, field="estimated_cost")
        actual = telemetry_number(actual_cost, field="actual_cost")
        if estimated is not None or actual is not None:
            if cost_available_value == 0:
                raise ValueError("Telemetry cost availability conflicts with cost values.")
            cost_available_value = 1
        currency_value = label(currency, "currency", lowercase=True)
        cost_version_value = label(cost_version, "cost_version")
        def decision(value: Any, field: str) -> str | None:
            if value in (None, ""):
                return None
            result = safe_feedback_text(value, 20).lower()
            if result not in TELEMETRY_DECISIONS:
                raise ValueError(f"Telemetry {field} is invalid.")
            return result
        quota = decision(quota_decision, "quota_decision")
        rate_limit = decision(rate_limit_decision, "rate_limit_decision")
        abuse = decision(abuse_decision, "abuse_decision")
        revision = label(deployment_revision, "deployment_revision")
        current = now or utc_now()
        if occurred_at is None:
            occurred_text = iso_timestamp(current)
        elif isinstance(occurred_at, dt.datetime):
            occurred_text = iso_timestamp(occurred_at)
        else:
            parsed_occurred = parse_timestamp(occurred_at)
            if parsed_occurred is None:
                raise ValueError("Telemetry occurred_at is invalid.")
            occurred_text = iso_timestamp(parsed_occurred)
        expiry, original = self._telemetry_retention(
            current,
            run_reference=run_ref,
            support_reference=support_ref,
        )
        values: dict[str, Any] = {
            "workspace_id": self.workspace_id,
            "event_id": safe_event_id,
            "source_product": TELEMETRY_SOURCE_PRODUCT,
            "event_type": event_type,
            "event_status": event_status,
            "actor_tracking_id": self.actor_tracking_id,
            "actor_key_version": self.actor_key_version,
            "action_reference": action,
            "run_reference": run_ref or None,
            "session_reference": session_ref,
            "support_reference": support_ref,
            "retry_lineage_id": retry_ref,
            "attempt_number": attempt,
            "provider": provider_value,
            "model": model_value,
            "reasoning_level": reasoning_value,
            "operation_route": route_value,
            "purpose": purpose_value,
            "failure_class": failure_value or None,
            "duration_ms": duration,
            "usage_available": usage,
            **token_values,
            "cost_available": cost_available_value,
            "estimated_cost": estimated,
            "actual_cost": actual,
            "currency": currency_value,
            "cost_version": cost_version_value,
            "quota_decision": quota,
            "rate_limit_decision": rate_limit,
            "abuse_decision": abuse,
            "deployment_revision": revision,
            "occurred_at": occurred_text,
            "retention_expires_at": expiry,
            "original_retention_expires_at": original,
        }
        digest_material = {
            field: values.get(field)
            for field in TELEMETRY_IMMUTABLE_FIELDS
            if field not in {"source_sequence", "immutable_metadata_digest"}
        }
        calculated_digest = hashlib.sha256(canonical_json(digest_material).encode("utf-8")).hexdigest()
        supplied_digest = safe_feedback_text(immutable_metadata_digest, 64).lower()
        if supplied_digest and (not SHA256_RE.fullmatch(supplied_digest) or supplied_digest != calculated_digest):
            raise TelemetryConflictError("Telemetry metadata digest does not match immutable fields.")
        values["immutable_metadata_digest"] = calculated_digest
        self._acquire_transaction_locks(("telemetry_source", self.workspace_id))
        existing = row_dict(
            self.connection.execute(
                "select * from sqag_telemetry_events where workspace_id = ? and event_id = ? limit 1",
                (self.workspace_id, safe_event_id),
            ).fetchone()
        )
        if existing:
            if not self._telemetry_row_matches(existing, values):
                raise TelemetryConflictError("Telemetry event identity conflicts with immutable data.")
            result = self._public_telemetry_event(existing)
            result["idempotent_replay"] = True
            if commit:
                self.connection.commit()
            return result
        state = self._telemetry_source_state() if self.connection.execute(
            "select 1 from sqag_telemetry_source_state where workspace_id = ? and source_product = ? limit 1",
            (self.workspace_id, TELEMETRY_SOURCE_PRODUCT),
        ).fetchone() else None
        if state is None:
            now_text = iso_timestamp(current)
            self.connection.execute(
                "insert into sqag_telemetry_source_state "
                "(workspace_id, source_product, next_source_sequence, high_watermark, "
                "reconciliation_state, created_at, updated_at) values (?, ?, 1, 0, 'healthy', ?, ?) "
                "on conflict(workspace_id, source_product) do nothing",
                (self.workspace_id, TELEMETRY_SOURCE_PRODUCT, now_text, now_text),
            )
            state = self._telemetry_source_state()
        source_sequence = int(state["next_source_sequence"])
        values["source_sequence"] = source_sequence
        self.connection.execute(
            "update sqag_telemetry_source_state set next_source_sequence = ?, "
            "high_watermark = ?, updated_at = ? where workspace_id = ? and source_product = ?",
            (source_sequence + 1, source_sequence, iso_timestamp(current), self.workspace_id, TELEMETRY_SOURCE_PRODUCT),
        )
        self.connection.execute(
            "insert into sqag_telemetry_events (workspace_id, event_id, source_product, source_sequence, "
            "event_type, event_status, actor_tracking_id, actor_key_version, action_reference, run_reference, "
            "session_reference, support_reference, retry_lineage_id, attempt_number, provider, model, "
            "reasoning_level, operation_route, purpose, failure_class, duration_ms, usage_available, "
            "input_tokens, output_tokens, total_tokens, cache_read_tokens, cache_write_tokens, cost_available, "
            "estimated_cost, actual_cost, currency, cost_version, quota_decision, rate_limit_decision, "
            "abuse_decision, deployment_revision, occurred_at, immutable_metadata_digest, retention_expires_at, "
            "original_retention_expires_at, legal_hold, deletion_state) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'active')",
            (
                values["workspace_id"], values["event_id"], values["source_product"], values["source_sequence"],
                values["event_type"], values["event_status"], values["actor_tracking_id"], values["actor_key_version"],
                values["action_reference"], values["run_reference"], values["session_reference"], values["support_reference"],
                values["retry_lineage_id"], values["attempt_number"], values["provider"], values["model"],
                values["reasoning_level"], values["operation_route"], values["purpose"], values["failure_class"],
                values["duration_ms"], values["usage_available"], values["input_tokens"], values["output_tokens"],
                values["total_tokens"], values["cache_read_tokens"], values["cache_write_tokens"], values["cost_available"],
                values["estimated_cost"], values["actual_cost"], values["currency"], values["cost_version"],
                values["quota_decision"], values["rate_limit_decision"], values["abuse_decision"], values["deployment_revision"],
                values["occurred_at"], values["immutable_metadata_digest"], values["retention_expires_at"],
                values["original_retention_expires_at"],
            ),
        )
        result = self._public_telemetry_event(values | {"legal_hold": 0, "deletion_state": "active"})
        result["idempotent_replay"] = False
        if commit:
            self.connection.commit()
        return result

    def feed_telemetry_events(
        self,
        cursor: tuple[int, str] | None = None,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("Telemetry feed limit is invalid.")
        cursor_sequence: int | None = None
        cursor_event_id = ""
        if cursor is not None:
            if (
                not isinstance(cursor, tuple)
                or len(cursor) != 2
                or isinstance(cursor[0], bool)
                or not isinstance(cursor[0], int)
                or cursor[0] < 0
                or not safe_reference(cursor[1])
            ):
                raise ValueError("Telemetry feed cursor is invalid.")
            cursor_sequence = cursor[0]
            cursor_event_id = safe_reference(cursor[1])
        state = self._telemetry_source_state()
        high_watermark = int(state["high_watermark"])
        parameters: list[Any] = [self.workspace_id, TELEMETRY_SOURCE_PRODUCT, high_watermark]
        continuation = ""
        if cursor_sequence is not None:
            continuation = "and (source_sequence > ? or (source_sequence = ? and event_id > ?)) "
            parameters.extend((cursor_sequence, cursor_sequence, cursor_event_id))
        parameters.append(limit)
        rows = self.connection.execute(
            "select * from sqag_telemetry_events where workspace_id = ? and source_product = ? "
            "and source_sequence <= ? " + continuation +
            "order by source_sequence, event_id limit ?",
            tuple(parameters),
        ).fetchall()
        events = [self._public_telemetry_event(row_dict(row)) for row in rows]
        next_cursor = None
        if events:
            next_cursor = (int(events[-1]["source_sequence"]), str(events[-1]["event_id"]))
        return {
            "events": events,
            "high_watermark": high_watermark,
            "next_cursor": next_cursor,
        }

    def reconcile_telemetry_source_state(
        self, *, now: dt.datetime | None = None, reference: str = ""
    ) -> dict[str, Any]:
        current = now or utc_now()
        self._acquire_transaction_locks(("telemetry_source", self.workspace_id))
        state = row_dict(
            self.connection.execute(
                "select * from sqag_telemetry_source_state where workspace_id = ? and source_product = ?",
                (self.workspace_id, TELEMETRY_SOURCE_PRODUCT),
            ).fetchone()
        )
        if not state:
            raise TelemetryUnavailableError("telemetry_source_state_missing")
        maximum = self.connection.execute(
            "select max(source_sequence) as maximum_source_sequence "
            "from sqag_telemetry_events where workspace_id = ? and source_product = ?",
            (self.workspace_id, TELEMETRY_SOURCE_PRODUCT),
        ).fetchone()
        maximum_value = row_dict(maximum).get("maximum_source_sequence")
        try:
            next_sequence = int(state.get("next_source_sequence"))
            high_watermark = int(state.get("high_watermark"))
            maximum_sequence = int(maximum_value or 0)
        except (TypeError, ValueError) as exc:
            next_sequence = high_watermark = maximum_sequence = -1
            _ = exc
        valid = (
            state.get("source_product") == TELEMETRY_SOURCE_PRODUCT
            and next_sequence == high_watermark + 1
            and high_watermark >= 0
            and maximum_sequence <= high_watermark
        )
        reconciliation_state = "healthy" if valid else "inconsistent"
        safe_reference_value = safe_telemetry_label(reference, lowercase=False) if reference else None
        self.connection.execute(
            "update sqag_telemetry_source_state set reconciliation_state = ?, "
            "last_reconciled_at = ?, reconciliation_reference = ?, updated_at = ? "
            "where workspace_id = ? and source_product = ?",
            (reconciliation_state, iso_timestamp(current), safe_reference_value, iso_timestamp(current), self.workspace_id, TELEMETRY_SOURCE_PRODUCT),
        )
        self.connection.commit()
        return {
            "reconciliation_state": reconciliation_state,
            "next_source_sequence": next_sequence,
            "high_watermark": high_watermark,
            "maximum_source_sequence": maximum_sequence,
        }

    def _owned_run(self, run_id: Any) -> dict[str, Any]:
        run_id = safe_reference(run_id, "run-")
        if not run_id:
            return {}
        return row_dict(self.connection.execute(
            "select run_id, job_id, quote_session_id, status, started_at from sqag_generation_runs where workspace_id = ? and actor_tracking_id = ? and run_id = ?",
            (self.workspace_id, self.actor_tracking_id, run_id),
        ).fetchone())

    def run_for_job(self, job_id: Any) -> dict[str, Any]:
        job_id = safe_reference(job_id, "job-")
        if not job_id:
            return {}
        return row_dict(self.connection.execute(
            "select * from sqag_generation_runs where workspace_id = ? and actor_tracking_id = ? and job_id = ?",
            (self.workspace_id, self.actor_tracking_id, job_id),
        ).fetchone())

    def feedback_context(self, *, run_id: Any = "", validated_session_id: Any = "", session_status: Any = "", session_created_at: Any = "") -> dict[str, str]:
        run = self._owned_run(run_id)
        safe_session = safe_reference(validated_session_id)
        if run and safe_session and safe_reference(run.get("quote_session_id")) != safe_session:
            run = {}
        if run:
            safe_run = str(run["run_id"])
            return {"link_type": "generation_run", "run_id": safe_run, "session_id": safe_reference(run.get("quote_session_id")), "short_reference": safe_run[-12:], "label": f"Generation run ...{safe_run[-8:]}", "status": safe_feedback_text(run.get("status"), 40), "created_at": safe_feedback_text(run.get("started_at"), 40), "source": "current_generation"}
        if safe_session:
            return {"link_type": "quote_session", "run_id": "", "session_id": safe_session, "short_reference": safe_session[-12:], "label": f"Quote session ...{safe_session[-8:]}", "status": safe_feedback_text(session_status, 40), "created_at": safe_feedback_text(session_created_at, 40), "source": "current_session"}
        return {"link_type": "none", "run_id": "", "session_id": "", "short_reference": "", "label": "No quote context will be linked", "status": "", "created_at": "", "source": "none"}

    def record_run_started(
        self,
        job_type: str,
        summary: dict[str, Any],
        *,
        run_id: str = "",
        job_id: str = "",
        idempotency_key: str = "",
        quote_session_id: str = "",
        parent_run_id: str = "",
        attempt_number: int = 1,
        app_revision: str = "",
        now: dt.datetime | None = None,
    ) -> str:
        safe_job_id = safe_reference(job_id, "job-") if job_id else ""
        if job_id and not safe_job_id:
            raise ValueError("Generation job identity is invalid.")
        safe_session_id = safe_reference(quote_session_id)
        if quote_session_id and not safe_session_id:
            raise ValueError("Quote session identity is invalid.")
        if safe_job_id:
            existing = self.run_for_job(safe_job_id)
            if existing:
                if safe_feedback_text(existing.get("job_type"), 40) not in {"", safe_feedback_text(job_type, 40)}:
                    raise ValueError("Generation job identity is already in use.")
                return str(existing["run_id"])
        now = now or utc_now()
        run_id = safe_reference(run_id, "run-") or self.new_id("run")
        lock_identities = [("generation_run", run_id)]
        if safe_session_id:
            lock_identities.append(("quote_session", safe_session_id))
        self._acquire_transaction_locks(*lock_identities)
        expiry, original = self._retention(now)
        try:
            self.connection.execute(
                "insert into sqag_generation_runs (run_id, workspace_id, actor_tracking_id, actor_key_version, job_id, idempotency_key, parent_run_id, attempt_number, job_type, status, quote_session_id, started_at, app_revision, evidence_schema_version, retention_expires_at, original_retention_expires_at, legal_hold, deletion_state) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, self.workspace_id, self.actor_tracking_id, self.actor_key_version, safe_job_id or None, safe_reference(idempotency_key) or safe_job_id or None, safe_reference(parent_run_id, "run-") or None, max(1, int(attempt_number or 1)), safe_feedback_text(job_type, 40) or "unknown", "received", safe_session_id or None, iso_timestamp(now), safe_feedback_text(app_revision, 80) or None, EVIDENCE_SCHEMA_VERSION, expiry, original, 0, "active"),
            )
        except Exception:
            self.connection.rollback()
            existing = self.run_for_job(safe_job_id) if safe_job_id else {}
            if existing and safe_feedback_text(existing.get("job_type"), 40) == safe_feedback_text(job_type, 40):
                return str(existing["run_id"])
            raise
        request = dict(summary)
        request.setdefault("schema", EVIDENCE_SCHEMA_VERSION)
        request.setdefault("job_id", safe_job_id)
        request.setdefault("attempt_number", max(1, int(attempt_number or 1)))
        self.append_evidence(run_id, "request_manifest", request, now=now, commit=False)
        self.append_audit("generation_received", {"job_type": safe_feedback_text(job_type, 40) or "unknown", "job_id": safe_job_id}, run_id=run_id, now=now, commit=False)
        self.append_telemetry_event("generation", "started", event_id=self.telemetry_event_id("telemetry-generation", run_id, "received"), action_reference=safe_job_id or run_id, run_reference=run_id, session_reference=safe_session_id, purpose=safe_feedback_text(job_type, 40), deployment_revision=app_revision, now=now, commit=False)
        self.connection.commit()
        return run_id

    def set_run_state(self, run_id: str, status: str, *, expected_status: str = "") -> bool:
        if status not in NON_TERMINAL_RUN_STATES:
            raise ValueError("Generation run state is invalid.")
        expected = safe_feedback_text(expected_status, 40)
        expected_clause = " and status = ?" if expected else ""
        parameters = [status, self.workspace_id, self.actor_tracking_id, safe_reference(run_id, "run-")]
        if expected:
            parameters.append(expected)
        cursor = self.connection.execute(
            "update sqag_generation_runs set status = ? where workspace_id = ? and actor_tracking_id = ? and run_id = ? and completed_at is null" + expected_clause,
            tuple(parameters),
        )
        if getattr(cursor, "rowcount", 0) != 1:
            self.connection.rollback()
            return False
        self.append_audit(f"generation_{status}", {}, run_id=run_id, commit=False)
        self.append_telemetry_event(
            "generation",
            "started" if status == "received" else status,
            event_id=self.telemetry_event_id("telemetry-generation", safe_reference(run_id, "run-"), status),
            action_reference=safe_reference(run_id, "run-"),
            run_reference=safe_reference(run_id, "run-"),
            now=utc_now(),
            commit=False,
        )
        self.connection.commit()
        return True

    def finish_run(self, run_id: str, status: str, *, error_category: str = "", quote_session_id: str = "", result_summary: dict[str, Any] | None = None, canonical_manifest: dict[str, Any] | None = None, now: dt.datetime | None = None, commit: bool = True) -> bool:
        if status not in TERMINAL_RUN_STATES:
            status = "failed"
        now = now or utc_now()
        safe_run_id = safe_reference(run_id, "run-")
        if not safe_run_id:
            raise ValueError("Generation run identity is invalid.")
        requested_session_id = safe_reference(quote_session_id)
        if quote_session_id and not requested_session_id:
            raise ValueError("Quote session identity is invalid.")
        lock_identities = [("generation_run", safe_run_id)]
        if requested_session_id:
            lock_identities.append(("quote_session", requested_session_id))
        self._acquire_transaction_locks(*lock_identities)
        existing = row_dict(
            self.connection.execute(
                "select status, quote_session_id, completed_at from sqag_generation_runs where workspace_id = ? and actor_tracking_id = ? and run_id = ?",
                (self.workspace_id, self.actor_tracking_id, safe_run_id),
            ).fetchone()
        )
        if not existing:
            self.connection.rollback()
            raise ValueError("Generation run could not be finalized.")
        stored_session_id = safe_reference(existing.get("quote_session_id"))
        if stored_session_id and requested_session_id and stored_session_id != requested_session_id:
            self.connection.rollback()
            raise ValueError("Generation run session identity changed.")
        effective_session_id = stored_session_id or requested_session_id
        cursor = self.connection.execute(
            "update sqag_generation_runs set status = ?, error_category = ?, quote_session_id = ?, completed_at = ? where workspace_id = ? and actor_tracking_id = ? and run_id = ? and completed_at is null",
            (status, safe_feedback_text(error_category, 80), effective_session_id or None, iso_timestamp(now), self.workspace_id, self.actor_tracking_id, safe_run_id),
        )
        if getattr(cursor, "rowcount", 0) != 1:
            existing = row_dict(self.connection.execute("select status from sqag_generation_runs where workspace_id = ? and actor_tracking_id = ? and run_id = ?", (self.workspace_id, self.actor_tracking_id, safe_reference(run_id, "run-"))).fetchone())
            if existing.get("status") == status:
                if commit:
                    self.connection.commit()
                return False
            self.connection.rollback()
            raise ValueError("Generation run could not be finalized.")
        if result_summary is not None:
            self.append_evidence(run_id, "result_summary", result_summary, now=now, commit=False)
        if canonical_manifest is not None:
            manifest = dict(canonical_manifest)
            manifest["schema"] = EVIDENCE_SCHEMA_VERSION
            manifest["generation_run_id"] = run_id
            manifest["workspace_id"] = self.workspace_id
            manifest["actor_tracking_id"] = self.actor_tracking_id
            manifest["actor_key_version"] = self.actor_key_version
            manifest["terminal_state"] = status
            manifest["terminal_at"] = iso_timestamp(now)
            self.append_evidence(run_id, "generation_manifest", manifest, now=now, commit=False)
        self.append_audit(f"generation_{status}", {"error_category": safe_feedback_text(error_category, 80)}, run_id=run_id, now=now, commit=False)
        event_type = {
            "cancelled": "cancellation",
            "timed_out": "timeout",
            "abandoned": "abandonment",
            "superseded": "supersession",
        }.get(status, "generation")
        self.append_telemetry_event(
            event_type,
            status,
            event_id=self.telemetry_event_id("telemetry", event_type, safe_run_id, status),
            action_reference=safe_run_id,
            run_reference=safe_run_id,
            session_reference=effective_session_id,
            failure_class="timeout" if status == "timed_out" else "",
            now=now,
            commit=False,
        )
        if commit:
            self.connection.commit()
        return True

    def compensate_run_start(self, run_id: str, error_category: str = "job_creation_failed") -> None:
        self.finish_run(run_id, "cancelled", error_category=error_category, result_summary={"schema": EVIDENCE_SCHEMA_VERSION, "status": "cancelled", "reason": error_category})

    def append_evidence(self, run_id: str, evidence_type: str, evidence: dict[str, Any], *, now: dt.datetime | None = None, commit: bool = True) -> str:
        now = now or utc_now()
        evidence_id = self.new_id("evidence")
        body, digest = (
            bounded_digest_json(evidence)
            if evidence_type == "generation_manifest"
            else digest_json(evidence)
        )
        expiry, original = self._retention(now)
        self.connection.execute(
            "insert into sqag_generation_evidence (evidence_id, run_id, workspace_id, evidence_type, evidence_schema_version, evidence_json, evidence_sha256, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (evidence_id, safe_reference(run_id, "run-"), self.workspace_id, safe_feedback_text(evidence_type, 80), safe_feedback_text(evidence.get("schema"), 120) or EVIDENCE_SCHEMA_VERSION, body, digest, iso_timestamp(now), expiry, original, 0),
        )
        if commit:
            self.connection.commit()
        return evidence_id

    def append_audit(
        self,
        event_type: str,
        details: dict[str, Any],
        *,
        run_id: str = "",
        feedback_id: str = "",
        session_id: str = "",
        now: dt.datetime | None = None,
        commit: bool = True,
    ) -> str:
        now = now or utc_now()
        event_id = self.new_id("audit")
        body, digest = digest_json(details)
        expiry, original = self._retention(now)
        self.connection.execute(
            "insert into sqag_audit_events "
            "(event_id, run_id, feedback_id, session_id, workspace_id, "
            "actor_tracking_id, actor_key_version, event_type, event_json, "
            "event_sha256, created_at, retention_expires_at, "
            "original_retention_expires_at, legal_hold) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                safe_reference(run_id, "run-") or None,
                safe_reference(feedback_id, "feedback-") or None,
                safe_reference(session_id) or None,
                self.workspace_id,
                self.actor_tracking_id,
                self.actor_key_version,
                safe_feedback_text(event_type, 80),
                body,
                digest,
                iso_timestamp(now),
                expiry,
                original,
                0,
            ),
        )
        if commit:
            self.connection.commit()
        return event_id

    def verify_run_evidence(
        self,
        run_id: str,
        *,
        reason_code: str,
        privileged: bool,
        artifact_verifier: Callable[[dict[str, Any]], bool] | None = None,
        artifact_verifier_factory: Callable[[], Callable[[dict[str, Any]], bool]] | None = None,
    ) -> dict[str, Any]:
        if not privileged:
            raise PermissionError("Forensic evidence is not available.")
        reason = safe_reference(reason_code)
        if not reason:
            raise ValueError("A forensic access reason is required.")
        safe_run_id = safe_reference(run_id, "run-")
        run = row_dict(
            self.connection.execute(
                "select run_id from sqag_generation_runs where workspace_id = ? and run_id = ?",
                (self.workspace_id, safe_run_id),
            ).fetchone()
        )

        def failed(failure_code: str) -> dict[str, Any]:
            self.append_audit(
                "forensic_evidence_verification_failed",
                {
                    "run_id": safe_run_id,
                    "reason_code": reason,
                    "failure": failure_code,
                },
                run_id=safe_run_id,
            )
            return {
                "integrity_ok": False,
                "run_id": safe_run_id,
                "reason": failure_code,
            }

        if not run:
            raise LookupError("Forensic evidence is not available.")
        rows = self.connection.execute(
            "select evidence_type, evidence_json, evidence_sha256 "
            "from sqag_generation_evidence where workspace_id = ? and run_id = ? "
            "order by created_at, evidence_id",
            (self.workspace_id, safe_run_id),
        ).fetchall()
        manifest: dict[str, Any] = {}
        for row in rows:
            item = row_dict(row)
            raw = str(item.get("evidence_json") or "")
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != item.get(
                "evidence_sha256"
            ):
                return failed("evidence_digest_mismatch")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return failed("evidence_json_invalid")
            if item.get("evidence_type") == "generation_manifest" and isinstance(
                parsed, dict
            ):
                manifest = parsed
        if not manifest or manifest.get("schema") != EVIDENCE_SCHEMA_VERSION:
            return failed("manifest_missing")
        if (
            safe_reference(manifest.get("generation_run_id"), "run-") != safe_run_id
            or manifest.get("workspace_id") != self.workspace_id
        ):
            return failed("manifest_linkage_mismatch")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list):
            return failed("manifest_artifacts_invalid")
        verifier = artifact_verifier
        if artifacts and verifier is None:
            if artifact_verifier_factory is None:
                return failed("artifact_verifier_required")
            try:
                verifier = artifact_verifier_factory()
            except Exception:
                self.append_audit(
                    "forensic_evidence_verification_failed",
                    {
                        "run_id": safe_run_id,
                        "reason_code": reason,
                        "failure": "artifact_storage_unavailable",
                    },
                    run_id=safe_run_id,
                )
                raise
            if not callable(verifier):
                return failed("artifact_verifier_required")
        for artifact in artifacts:
            if not isinstance(artifact, dict) or not SHA256_RE.fullmatch(
                str(artifact.get("sha256") or "")
            ):
                return failed("artifact_digest_missing")
            if verifier is None:
                return failed("artifact_verifier_required")
            try:
                verified = verifier(artifact)
            except Exception:
                self.append_audit(
                    "forensic_evidence_verification_failed",
                    {
                        "run_id": safe_run_id,
                        "reason_code": reason,
                        "failure": "artifact_storage_unavailable",
                    },
                    run_id=safe_run_id,
                )
                raise
            if not verified:
                return failed("artifact_digest_mismatch")
        self.append_audit(
            "forensic_evidence_accessed",
            {"run_id": safe_run_id, "reason_code": reason},
            run_id=safe_run_id,
        )
        return {
            "integrity_ok": True,
            "run_id": safe_run_id,
            "manifest": manifest,
        }

    def _feedback_submission_session_link(
        self,
        session_id: str,
        publication_context_factory: Callable[[str], dict[str, str]] | None,
    ) -> tuple[str, str, str]:
        safe_session_id = safe_reference(session_id)
        if not safe_session_id:
            return "", "", "none"
        self._acquire_transaction_locks(("quote_session", safe_session_id))
        if publication_context_factory is not None:
            publication = publication_context_factory(safe_session_id)
            if isinstance(publication, dict) and safe_feedback_text(
                publication.get("state"), 20
            ).lower() == "published":
                published_run_id = safe_reference(
                    publication.get("run_id"), "run-"
                )
                version_id = safe_reference(
                    publication.get("version_id"), "run-"
                ) or published_run_id
                if not published_run_id or version_id != published_run_id:
                    return "", "", "session_without_run"
                linked = row_dict(
                    self.connection.execute(
                        "select run_id, quote_session_id from sqag_generation_runs "
                        "where workspace_id = ? and run_id = ? limit 1",
                        (self.workspace_id, published_run_id),
                    ).fetchone()
                )
                if (
                    linked
                    and safe_reference(linked.get("quote_session_id"))
                    == safe_session_id
                ):
                    return published_run_id, version_id, "current_published_run"
        blocked_rows = self.connection.execute(
            "select run_id from sqag_generation_runs where workspace_id = ? "
            "and quote_session_id = ? and status in "
            "('blocked','failed','needs_confirmation','needs_review',"
            "'completed_with_review_required','degraded','cancelled','timed_out') "
            "order by completed_at desc, run_id desc limit 2",
            (self.workspace_id, safe_session_id),
        ).fetchall()
        if len(blocked_rows) == 1:
            blocked_run_id = safe_reference(
                row_dict(blocked_rows[0]).get("run_id"), "run-"
            )
            if blocked_run_id:
                return blocked_run_id, "", "current_blocked_run"
        return "", "", "session_without_run"
    def submit_feedback(
        self,
        payload: dict[str, Any],
        *,
        publication_context_factory: Callable[[str], dict[str, str]] | None = None,
        now: dt.datetime | None = None,
    ) -> dict[str, str]:
        category = safe_feedback_text(payload.get("category"), 40)
        if category not in FEEDBACK_CATEGORIES:
            raise ValueError("Select a valid feedback category.")
        title = safe_feedback_text(payload.get("title"), 120)
        message = safe_feedback_text(payload.get("message"))
        if not title or not message:
            raise ValueError("Feedback title and description are required.")
        impact = safe_feedback_text(payload.get("impact"), 20)
        if impact not in FEEDBACK_IMPACTS:
            impact = "medium"
        manual_reference = safe_manual_reference(payload.get("manual_reference"))
        include_link = payload.get("include_link") is not False
        link_choice = safe_feedback_text(payload.get("link_choice"), 20)
        if link_choice not in FEEDBACK_LINK_CHOICES:
            link_choice = "automatic" if include_link else "none"
        run = self._owned_run(payload.get("run_id")) if include_link else {}
        run_id = safe_reference(run.get("run_id"), "run-")
        session_id = safe_reference(payload.get("validated_session_id")) if include_link else ""
        if run and session_id and safe_reference(run.get("quote_session_id")) != session_id:
            run, run_id = {}, ""
        if run and not session_id:
            session_id = safe_reference(run.get("quote_session_id"))
        resolved_type = resolved_id = ""
        manual_status = "none"
        if manual_reference:
            manual_status = "unresolved"
            manual_run = self._owned_run(manual_reference)
            if manual_run:
                resolved_type, resolved_id, manual_status = "generation_run", str(manual_run["run_id"]), "resolved"
                run_id = run_id or resolved_id
                run = manual_run
                session_id = session_id or safe_reference(manual_run.get("quote_session_id"))
            else:
                validated = safe_reference(payload.get("validated_manual_session_id"))
                if validated:
                    resolved_type, resolved_id, manual_status = "quote_session", validated, "resolved"
                    session_id = session_id or validated
        now = now or utc_now()
        publication_version_id = ""
        link_resolution_source = "none"
        if run_id:
            if manual_status == "resolved" and resolved_type == "generation_run":
                link_resolution_source = "manual_run"
            elif safe_feedback_text(run.get("status"), 40) in {
                "blocked", "failed", "needs_confirmation", "needs_review",
                "completed_with_review_required", "degraded", "cancelled", "timed_out",
            }:
                link_resolution_source = "current_blocked_run"
            else:
                link_resolution_source = "current_run"
        elif session_id:
            (
                run_id,
                publication_version_id,
                link_resolution_source,
            ) = self._feedback_submission_session_link(
                session_id,
                publication_context_factory,
            )
        if run_id and not resolved_type:
            resolved_type, resolved_id = "generation_run", run_id
        link_resolved_at = iso_timestamp(now) if run_id else None
        source = payload.get("diagnostic_metadata") if isinstance(payload.get("diagnostic_metadata"), dict) else {}
        diagnostics = {key: safe_feedback_text(source.get(key), limit) for key, limit in {"app_revision": 80, "browser_family_major": 80, "current_route": 120, "job_state": 40, "product_area": 80, "viewport_bucket": 40}.items()}
        feedback_id = self.new_id("feedback")
        support_reference = self.new_support_reference()
        expiry, original = self._retention(now)
        values = (
            feedback_id, support_reference, self.workspace_id, self.actor_tracking_id, self.actor_key_version, run_id or None, session_id or None, category, title, message,
            safe_feedback_text(payload.get("expected_result"), 2000) or None, safe_feedback_text(payload.get("actual_result"), 2000) or None, safe_feedback_text(payload.get("reproduction_steps"), 3000) or None,
            impact, link_choice if include_link else "none", manual_reference or None, manual_status, resolved_type or None, resolved_id or None, publication_version_id or None, link_resolution_source, link_resolved_at,
            canonical_json({key: value for key, value in diagnostics.items() if value}), "open", iso_timestamp(now), iso_timestamp(now), expiry, original, expiry, FEEDBACK_RETENTION_POLICY_VERSION, 0, "active",
        )
        self.connection.execute(
            "insert into sqag_feedback (feedback_id, support_reference, workspace_id, reporter_tracking_id, reporter_key_version, run_id, session_id, category, title, message, expected_result, actual_result, reproduction_steps, impact, link_choice, manual_reference_text, manual_reference_status, resolved_reference_type, resolved_reference_id, publication_version_id, link_resolution_source, link_resolved_at, diagnostic_metadata_json, status, created_at, updated_at, retention_expires_at, original_retention_expires_at, submission_retention_expires_at, retention_policy_version, legal_hold, deletion_state) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        self.connection.execute(
            "insert into sqag_feedback_status_history (history_id, feedback_id, workspace_id, from_status, to_status, actor_tracking_id, actor_key_version, resolution_note, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.new_id("feedback-history"), feedback_id, self.workspace_id, "", "open", self.actor_tracking_id, self.actor_key_version, None, iso_timestamp(now), expiry, original, 0),
        )
        self.append_audit("feedback_submitted", {"feedback_id": feedback_id, "support_reference": support_reference, "category": category, "linked_run": bool(run_id), "linked_session": bool(session_id), "manual_reference_status": manual_status, "link_resolution_source": link_resolution_source}, run_id=run_id, feedback_id=feedback_id, session_id=session_id, now=now, commit=False)
        self.append_telemetry_event(
            "feedback",
            "completed",
            event_id=self.telemetry_event_id("telemetry-feedback", feedback_id, "submitted"),
            action_reference=feedback_id,
            run_reference=run_id,
            session_reference=session_id,
            support_reference=feedback_id,
            purpose=category,
            now=now,
            commit=False,
        )
        self.connection.commit()
        return {"feedback_id": feedback_id, "feedback_report_id": feedback_id, "support_reference": support_reference, "manual_reference_status": manual_status, "status": "open"}

    def _feedback(self, reference: Any) -> dict[str, Any]:
        ref = safe_reference(reference)
        if not ref:
            return {}
        return row_dict(self.connection.execute("select * from sqag_feedback where workspace_id = ? and (feedback_id = ? or support_reference = ?)", (self.workspace_id, ref, ref)).fetchone())

    def list_feedback(self, *, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 100))
        query = safe_reference(query)
        if query:
            rows = self.connection.execute("select * from sqag_feedback where workspace_id = ? and (feedback_id = ? or support_reference = ? or run_id = ? or session_id = ?) order by created_at desc limit ?", (self.workspace_id, query, query, query, query, limit)).fetchall()
        else:
            rows = self.connection.execute("select * from sqag_feedback where workspace_id = ? order by created_at desc limit ?", (self.workspace_id, limit)).fetchall()
        return [row_dict(row) for row in rows]

    def get_feedback(self, reference: Any, *, audit_access: bool = True) -> dict[str, Any]:
        report = self._feedback(reference)
        if not report:
            raise LookupError("Feedback report is not available.")
        history = [row_dict(row) for row in self.connection.execute("select * from sqag_feedback_status_history where workspace_id = ? and feedback_id = ? order by created_at, history_id", (self.workspace_id, report["feedback_id"])).fetchall()]
        if audit_access:
            self.append_audit(
                "feedback_report_accessed",
                {"feedback_id": report["feedback_id"]},
                feedback_id=report["feedback_id"],
                session_id=safe_reference(report.get("session_id")),
            )
        return {"report": report, "status_history": history}

    def resolve_feedback_evidence_run(
        self,
        report: dict[str, Any],
        *,
        publication_context_factory: Callable[[], dict[str, str]] | None = None,
    ) -> str:
        """Resolve one authorised report to one same-workspace evidence run."""
        if not isinstance(report, dict):
            raise LookupError("Forensic evidence is not available.")
        direct_run_id = safe_reference(report.get("run_id"), "run-")
        publication_version_id = safe_reference(
            report.get("publication_version_id"), "run-"
        )
        if publication_version_id and publication_version_id != direct_run_id:
            raise LookupError("Forensic evidence is not available.")
        session_id = safe_reference(report.get("session_id"))
        if direct_run_id:
            direct = row_dict(
                self.connection.execute(
                    "select run_id, quote_session_id from sqag_generation_runs "
                    "where workspace_id = ? and run_id = ? limit 1",
                    (self.workspace_id, direct_run_id),
                ).fetchone()
            )
            if not direct:
                raise LookupError("Forensic evidence is not available.")
            linked_session = safe_reference(direct.get("quote_session_id"))
            if session_id and linked_session and session_id != linked_session:
                raise LookupError("Forensic evidence is not available.")
            return direct_run_id
        _ = publication_context_factory, session_id
        raise LookupError("Forensic evidence is not available.")

    def update_feedback_status(
        self,
        reference: Any,
        status: str,
        *,
        resolution_note: str = "",
        now: dt.datetime | None = None,
    ) -> dict[str, Any]:
        status = safe_feedback_text(status, 40)
        if status not in FEEDBACK_STATUSES:
            raise ValueError("Feedback status transition is not allowed.")
        ref = safe_reference(reference)
        identity = row_dict(
            self.connection.execute(
                "select feedback_id from sqag_feedback where workspace_id = ? "
                "and (feedback_id = ? or support_reference = ?)",
                (self.workspace_id, ref, ref),
            ).fetchone()
        )
        feedback_id = safe_reference(identity.get("feedback_id"), "feedback-")
        if not feedback_id:
            raise LookupError("Feedback report is not available.")
        self._acquire_transaction_locks(("feedback", feedback_id))
        report = self._feedback(feedback_id)
        if not report:
            self.connection.rollback()
            raise LookupError("Feedback report is not available.")
        current = str(report.get("status") or "")
        if status not in FEEDBACK_TRANSITIONS.get(current, set()):
            self.connection.rollback()
            raise ValueError("Feedback status transition is not allowed.")
        note = safe_feedback_text(resolution_note, 2000)
        reopening = current in FEEDBACK_CLOSED_STATES and status not in FEEDBACK_CLOSED_STATES
        closing = current not in FEEDBACK_CLOSED_STATES and status in FEEDBACK_CLOSED_STATES
        if reopening and not note:
            self.connection.rollback()
            raise ValueError("A reopen reason is required.")
        now = now or utc_now()
        now_text = iso_timestamp(now)
        closed_at = report.get("closed_at")
        expiry = str(report.get("retention_expires_at") or "")
        if reopening:
            closed_at = None
            retained_expiries = [
                parsed
                for parsed in (
                    parse_timestamp(report.get("retention_expires_at")),
                    parse_timestamp(report.get("submission_retention_expires_at")),
                )
                if parsed is not None
            ]
            if retained_expiries:
                expiry = iso_timestamp(max(retained_expiries))
        elif closing:
            closed_at = now_text
            expiry = iso_timestamp(add_calendar_years(now))
        prior_reopens = int(
            self.connection.execute(
                "select count(*) from sqag_feedback_status_history "
                "where workspace_id = ? and feedback_id = ? "
                "and from_status in ('resolved','closed','rejected','duplicate') "
                "and to_status not in ('resolved','closed','rejected','duplicate')",
                (self.workspace_id, report["feedback_id"]),
            ).fetchone()[0]
        )
        reopen_count = prior_reopens + (1 if reopening else 0)
        self.connection.execute(
            "update sqag_feedback set status = ?, updated_at = ?, closed_at = ?, "
            "retention_expires_at = ?, retention_policy_version = ?, "
            "deletion_state = 'active', deletion_error_code = null, "
            "deletion_claimed_at = null where workspace_id = ? and feedback_id = ?",
            (
                status,
                now_text,
                closed_at,
                expiry,
                FEEDBACK_RETENTION_POLICY_VERSION,
                self.workspace_id,
                report["feedback_id"],
            ),
        )
        history_id = self.new_id("feedback-history")
        self.connection.execute(
            "insert into sqag_feedback_status_history "
            "(history_id, feedback_id, workspace_id, from_status, to_status, "
            "actor_tracking_id, actor_key_version, resolution_note, created_at, "
            "retention_expires_at, original_retention_expires_at, legal_hold) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                history_id,
                report["feedback_id"],
                self.workspace_id,
                current,
                status,
                self.actor_tracking_id,
                self.actor_key_version,
                note or None,
                now_text,
                expiry,
                report["original_retention_expires_at"],
                0,
            ),
        )
        self.append_audit(
            "feedback_status_changed",
            {
                "feedback_id": report["feedback_id"],
                "from_status": current,
                "to_status": status,
                "has_resolution_note": bool(note),
                "reopen_count": reopen_count,
                "unusual_reopen_activity": reopen_count >= 3,
                "retention_policy_version": FEEDBACK_RETENTION_POLICY_VERSION,
            },
            run_id=safe_reference(report.get("run_id"), "run-"),
            feedback_id=report["feedback_id"],
            session_id=safe_reference(report.get("session_id")),
            now=now,
            commit=False,
        )
        self.append_telemetry_event(
            "feedback",
            "updated",
            event_id=self.telemetry_event_id("telemetry-feedback", history_id),
            action_reference=report["feedback_id"],
            run_reference=safe_reference(report.get("run_id"), "run-"),
            session_reference=safe_reference(report.get("session_id")),
            support_reference=report["feedback_id"],
            purpose=status,
            now=now,
            commit=False,
        )
        self.connection.commit()
        return self._feedback(report["feedback_id"])

    def _target_exists(self, table: str, id_column: str, record_id: str) -> bool:
        return bool(self.connection.execute(f"select 1 from {table} where workspace_id = ? and {id_column} = ? limit 1", (self.workspace_id, record_id)).fetchone())

    def _acquire_transaction_locks(self, *identities: tuple[str, str]) -> None:
        normalized = sorted({
            (safe_feedback_text(scope, 40), safe_reference(identifier))
            for scope, identifier in identities
            if safe_feedback_text(scope, 40) and safe_reference(identifier)
        })
        if not normalized:
            return
        if self.connection.__class__.__module__.startswith("sqlite3"):
            if not bool(getattr(self.connection, "in_transaction", False)):
                self.connection.execute("begin immediate")
            return
        for scope, identifier in normalized:
            digest = hashlib.sha256(
                f"{self.workspace_id}:{scope}:{identifier}".encode("utf-8")
            ).digest()
            lock_key = int.from_bytes(digest[:8], "big", signed=True)
            self.connection.execute(
                "select pg_advisory_xact_lock(?)",
                (lock_key,),
            )

    def _legal_hold_graph_identity(
        self,
        table: str,
        id_column: str,
        record_id: str,
    ) -> tuple[str, str] | None:
        if table == "sqag_generation_runs":
            return ("generation_run", record_id) if self._target_exists(table, id_column, record_id) else None
        if table == "sqag_generation_evidence":
            row = row_dict(self.connection.execute(
                f"select run_id from {table} where workspace_id = ? and {id_column} = ? limit 1",
                (self.workspace_id, record_id),
            ).fetchone())
            run_id = safe_reference(row.get("run_id"), "run-")
            if run_id:
                return ("generation_run", run_id)
            return (LEGAL_HOLD_TARGETS[table][1], record_id) if row else None
        if table == "sqag_audit_events":
            row = row_dict(self.connection.execute(
                "select run_id, feedback_id from sqag_audit_events "
                "where workspace_id = ? and event_id = ? limit 1",
                (self.workspace_id, record_id),
            ).fetchone())
            feedback_id = safe_reference(row.get("feedback_id"), "feedback-")
            if feedback_id and self._target_exists("sqag_feedback", "feedback_id", feedback_id):
                return ("feedback", feedback_id)
            run_id = safe_reference(row.get("run_id"), "run-")
            if run_id:
                return ("generation_run", run_id)
            return (LEGAL_HOLD_TARGETS[table][1], record_id) if row else None
        if table == "sqag_feedback":
            return ("feedback", record_id) if self._target_exists(table, id_column, record_id) else None
        if table == "sqag_feedback_status_history":
            row = row_dict(self.connection.execute(
                "select feedback_id from sqag_feedback_status_history where workspace_id = ? and history_id = ? limit 1",
                (self.workspace_id, record_id),
            ).fetchone())
            feedback_id = safe_reference(row.get("feedback_id"))
            return ("feedback", feedback_id) if feedback_id else None
        if table == "sqag_telemetry_events":
            return ("telemetry_event", record_id) if self._target_exists(table, id_column, record_id) else None
        return None

    def set_legal_hold(self, table: str, record_id_column: str, record_id: str, enabled: bool, *, reason_code: str = "legal_process", case_reference: str = "", now: dt.datetime | None = None) -> bool:
        expected = LEGAL_HOLD_TARGETS.get(table)
        if not expected or expected[0] != record_id_column:
            raise ValueError("Unsupported legal-hold target.")
        safe_id = safe_reference(record_id)
        reason = safe_reference(reason_code)
        if enabled and not reason:
            raise ValueError("Legal-hold reason is required.")
        case = safe_manual_reference(case_reference)
        graph_identity = self._legal_hold_graph_identity(
            table,
            record_id_column,
            safe_id,
        ) if safe_id else None
        if graph_identity is None:
            return False
        self._acquire_transaction_locks(graph_identity)
        if not self._target_exists(table, record_id_column, safe_id):
            self.connection.rollback()
            return False
        target_type = expected[1]
        active = row_dict(self.connection.execute("select * from sqag_legal_holds where workspace_id = ? and target_type = ? and target_id = ? and enabled = 1", (self.workspace_id, target_type, safe_id)).fetchone())
        now = now or utc_now()
        if enabled and active:
            self.connection.commit()
            return True
        if not enabled and not active:
            self.connection.commit()
            return True
        hold_id = ""
        if enabled:
            hold_id = self.new_id("hold")
            self.connection.execute("insert into sqag_legal_holds (hold_id, workspace_id, target_type, target_id, enabled, reason_code, case_reference, actor_tracking_id, actor_key_version, created_at) values (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)", (hold_id, self.workspace_id, target_type, safe_id, reason, case or None, self.actor_tracking_id, self.actor_key_version, iso_timestamp(now)))
            event = "legal_hold_applied"
        else:
            self.connection.execute("update sqag_legal_holds set enabled = 0, released_by_tracking_id = ?, released_by_key_version = ?, released_at = ? where hold_id = ? and enabled = 1", (self.actor_tracking_id, self.actor_key_version, iso_timestamp(now), active["hold_id"]))
            event = "legal_hold_released"
        if table == "sqag_telemetry_events":
            hold_cursor = self.connection.execute(
                "update sqag_telemetry_events set legal_hold = ? "
                "where workspace_id = ? and event_id = ?",
                (1 if enabled else 0, self.workspace_id, safe_id),
            )
            if getattr(hold_cursor, "rowcount", 0) != 1:
                self.connection.rollback()
                return False
        self.append_audit(event, {"target_type": target_type, "target_id": safe_id, "reason_code": reason or active.get("reason_code"), "case_reference": case or active.get("case_reference") or ""}, now=now, commit=False)
        self.append_audit("legal_hold_changed", {"target_type": target_type, "target_id": safe_id, "enabled": enabled}, now=now, commit=False)
        self.append_telemetry_event(
            "legal_hold",
            "held" if enabled else "updated",
            event_id=self.telemetry_event_id(
                "telemetry-legal-hold",
                hold_id or safe_reference(active.get("hold_id")),
                "applied" if enabled else "released",
            ),
            action_reference=safe_id,
            purpose=target_type,
            now=now,
            commit=False,
        )
        self.connection.commit()
        return True

    def _active_hold(self, target_type: str, target_id: str) -> bool:
        return bool(self.connection.execute("select 1 from sqag_legal_holds where workspace_id = ? and target_type = ? and target_id = ? and enabled = 1 limit 1", (self.workspace_id, target_type, target_id)).fetchone())

    def _run_graph_held(self, run_id: str) -> bool:
        direct = row_dict(
            self.connection.execute(
                "select legal_hold from sqag_generation_runs "
                "where workspace_id = ? and run_id = ? limit 1",
                (self.workspace_id, run_id),
            ).fetchone()
        )
        if bool(direct.get("legal_hold")) or self._active_hold("generation_run", run_id):
            return True
        queries = (("sqag_generation_evidence", "evidence_id", "generation_evidence"), ("sqag_audit_events", "event_id", "audit_event"))
        for table, column, target in queries:
            row = self.connection.execute(
                f"select 1 from {table} child where child.workspace_id = ? "
                "and child.run_id = ? and (child.legal_hold = 1 or exists ("
                "select 1 from sqag_legal_holds hold where hold.workspace_id = child.workspace_id "
                f"and hold.target_type = ? and hold.target_id = child.{column} "
                "and hold.enabled = 1)) limit 1",
                (self.workspace_id, run_id, target),
            ).fetchone()
            if row:
                return True
        telemetry = self.connection.execute(
            "select 1 from sqag_telemetry_events child where child.workspace_id = ? "
            "and child.run_reference = ? and (child.legal_hold = 1 or exists ("
            "select 1 from sqag_legal_holds hold where hold.workspace_id = child.workspace_id "
            "and hold.target_type = 'telemetry_event' and hold.target_id = child.event_id "
            "and hold.enabled = 1)) limit 1",
            (self.workspace_id, run_id),
        ).fetchone()
        if telemetry:
            return True
        return False

    def _feedback_graph_held(self, feedback_id: str) -> bool:
        if self._active_hold("feedback", feedback_id):
            return True
        child_held = self.connection.execute(
            "select 1 from sqag_feedback_status_history child "
            "where child.workspace_id = ? and child.feedback_id = ? "
            "and (child.legal_hold = 1 or exists (select 1 from sqag_legal_holds hold "
            "where hold.workspace_id = child.workspace_id "
            "and hold.target_type = 'feedback_status_history' "
            "and hold.target_id = child.history_id and hold.enabled = 1)) limit 1",
            (self.workspace_id, feedback_id),
        ).fetchone()
        if child_held:
            return True
        linked_audit_held = self.connection.execute(
            "select 1 from sqag_audit_events child "
            "where child.workspace_id = ? and child.feedback_id = ? "
            "and (child.legal_hold = 1 or exists (select 1 from sqag_legal_holds hold "
            "where hold.workspace_id = child.workspace_id "
            "and hold.target_type = 'audit_event' "
            "and hold.target_id = child.event_id and hold.enabled = 1)) limit 1",
            (self.workspace_id, feedback_id),
        ).fetchone()
        if linked_audit_held:
            return True
        linked_telemetry_held = self.connection.execute(
            "select 1 from sqag_telemetry_events child "
            "where child.workspace_id = ? and child.support_reference = ? "
            "and (child.legal_hold = 1 or exists (select 1 from sqag_legal_holds hold "
            "where hold.workspace_id = child.workspace_id "
            "and hold.target_type = 'telemetry_event' "
            "and hold.target_id = child.event_id and hold.enabled = 1)) limit 1",
            (self.workspace_id, feedback_id),
        ).fetchone()
        if linked_telemetry_held:
            return True
        report = row_dict(
            self.connection.execute(
                "select run_id, session_id, legal_hold from sqag_feedback "
                "where workspace_id = ? and feedback_id = ? limit 1",
                (self.workspace_id, feedback_id),
            ).fetchone()
        )
        if bool(report.get("legal_hold")):
            return True
        linked_run_id = safe_reference(report.get("run_id"), "run-")
        if linked_run_id and self._run_graph_held(linked_run_id):
            return True
        session_id = safe_reference(report.get("session_id"))
        if not session_id:
            return False
        linked_runs = self.connection.execute(
            "select run_id from sqag_generation_runs where workspace_id = ? "
            "and quote_session_id = ? order by run_id limit 501",
            (self.workspace_id, session_id),
        ).fetchall()
        if len(linked_runs) > 500:
            return True
        return any(
            self._run_graph_held(safe_reference(row_dict(row).get("run_id"), "run-"))
            for row in linked_runs
        )
    def _authorize_delete(self, record_type: str, record_id: str) -> None:
        self.connection.execute("insert into sqag_retention_delete_authorizations (authorization_id, workspace_id, record_type, record_id, created_at) values (?, ?, ?, ?, ?)", (self.new_id("retention-auth"), self.workspace_id, record_type, record_id, iso_timestamp(utc_now())))

    def _receipt(self, record_type: str, record_id: str, original_expiry: str, now_text: str) -> None:
        expiry = iso_timestamp(add_calendar_years(parse_timestamp(now_text) or utc_now()))
        self.connection.execute("insert into sqag_deletion_receipts (receipt_id, workspace_id, record_type, record_id, reason, deleted_at, original_retention_expires_at, created_at, retention_expires_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?) on conflict(workspace_id, record_type, record_id) do nothing", (self.new_id("delete"), self.workspace_id, record_type, record_id, "retention_expired", now_text, original_expiry, now_text, expiry))

    def _retained_run_child(self, run_id: str, now_text: str) -> bool:
        if self.connection.execute("select 1 from sqag_feedback where workspace_id = ? and run_id = ? limit 1", (self.workspace_id, run_id)).fetchone():
            return True
        session = self.connection.execute(
            "select quote_session_id from sqag_generation_runs where workspace_id = ? and run_id = ?",
            (self.workspace_id, run_id),
        ).fetchone()
        session_id = safe_reference(row_dict(session).get("quote_session_id"))
        if session_id and self.connection.execute(
            "select 1 from sqag_feedback where workspace_id = ? and session_id = ? limit 1",
            (self.workspace_id, session_id),
        ).fetchone():
            return True
        for table in ("sqag_generation_evidence", "sqag_audit_events"):
            if self.connection.execute(f"select 1 from {table} where workspace_id = ? and run_id = ? and retention_expires_at > ? limit 1", (self.workspace_id, run_id, now_text)).fetchone():
                return True
        if self.connection.execute(
            "select 1 from sqag_telemetry_events where workspace_id = ? "
            "and run_reference = ? and retention_expires_at > ? limit 1",
            (self.workspace_id, run_id, now_text),
        ).fetchone():
            return True
        return False

    def _telemetry_event_graph_held(self, item: dict[str, Any]) -> bool:
        run_id = safe_reference(item.get("run_reference"), "run-")
        if run_id and self._run_graph_held(run_id):
            return True
        feedback_id = safe_reference(item.get("support_reference"), "feedback-")
        return bool(feedback_id and self._feedback_graph_held(feedback_id))

    def _record_retention_telemetry(
        self,
        event_type: str,
        event_status: str,
        record_id: str,
        *,
        purpose: str,
        now_text: str,
    ) -> None:
        self.append_telemetry_event(
            event_type,
            event_status,
            action_reference=record_id,
            operation_route="retention_worker",
            purpose=purpose,
            now=parse_timestamp(now_text) or utc_now(),
            commit=False,
        )

    def _delete_retention_graph(
        self,
        kind: str,
        item: dict[str, Any],
        record_id: str,
        now_text: str,
        *,
        require_session_exclusive: bool = False,
    ) -> list[tuple[str, str, str]]:
        lock_identities = [
            ("generation_run" if kind == "generation_run" else "feedback", record_id)
        ]
        session_id = safe_reference(
            item.get("quote_session_id")
            if kind == "generation_run"
            else item.get("session_id")
        )
        if kind == "generation_run" and session_id and require_session_exclusive:
            lock_identities.append(("quote_session", session_id))
        if kind == "feedback":
            linked_run_id = safe_reference(item.get("run_id"), "run-")
            if linked_run_id:
                lock_identities.append(("generation_run", linked_run_id))
            if session_id:
                lock_identities.append(("quote_session", session_id))
        self._acquire_transaction_locks(*lock_identities)
        table = "sqag_generation_runs" if kind == "generation_run" else "sqag_feedback"
        id_column = "run_id" if kind == "generation_run" else "feedback_id"
        current_item = row_dict(
            self.connection.execute(
                f"select * from {table} where workspace_id = ? and {id_column} = ?",
                (self.workspace_id, record_id),
            ).fetchone()
        )
        current_expiry = parse_timestamp(current_item.get("retention_expires_at"))
        retention_now = parse_timestamp(now_text)
        if (
            not current_item
            or bool(current_item.get("legal_hold"))
            or current_expiry is None
            or retention_now is None
            or current_expiry > retention_now
            or (
                kind == "feedback"
                and current_item.get("status") not in FEEDBACK_CLOSED_STATES
            )
        ):
            raise RetentionGraphHeld("retention_parent_became_protected")
        item = current_item
        if (
            kind == "generation_run"
            and session_id
            and require_session_exclusive
            and self.connection.execute(
                "select 1 from sqag_generation_runs where workspace_id = ? and quote_session_id = ? and run_id <> ? limit 1",
                (self.workspace_id, session_id, record_id),
            ).fetchone()
        ):
            raise RetentionGraphHeld("retention_session_became_shared")
        graph_held = self._run_graph_held(record_id) if kind == "generation_run" else self._feedback_graph_held(record_id)
        retained_child = self._retained_run_child(record_id, now_text) if kind == "generation_run" else False
        if graph_held or retained_child:
            raise RetentionGraphHeld("retention_graph_became_protected")
        removed: list[tuple[str, str, str]] = []
        if kind == "generation_run":
            for table, column, record_type in (("sqag_generation_evidence", "evidence_id", "sqag_generation_evidence"), ("sqag_audit_events", "event_id", "sqag_audit_events")):
                rows = self.connection.execute(f"select {column}, original_retention_expires_at from {table} where workspace_id = ? and run_id = ?", (self.workspace_id, record_id)).fetchall()
                for row in rows:
                    child = row_dict(row)
                    self._authorize_delete(record_type, str(child[column]))
                    self.connection.execute(f"delete from {table} where workspace_id = ? and {column} = ?", (self.workspace_id, child[column]))
                    removed.append((record_type, str(child[column]), str(child["original_retention_expires_at"])))
            telemetry_rows = self.connection.execute(
                "select event_id, original_retention_expires_at from sqag_telemetry_events "
                "where workspace_id = ? and run_reference = ?",
                (self.workspace_id, record_id),
            ).fetchall()
            for row in telemetry_rows:
                child = row_dict(row)
                self._authorize_delete("sqag_telemetry_events", str(child["event_id"]))
                self.connection.execute(
                    "delete from sqag_telemetry_events where workspace_id = ? and event_id = ?",
                    (self.workspace_id, child["event_id"]),
                )
                removed.append(("sqag_telemetry_events", str(child["event_id"]), str(child["original_retention_expires_at"])))
            cursor = self.connection.execute("delete from sqag_generation_runs where workspace_id = ? and run_id = ?", (self.workspace_id, record_id))
        else:
            audit_rows = self.connection.execute(
                "select event_id, original_retention_expires_at "
                "from sqag_audit_events where workspace_id = ? "
                "and feedback_id = ?",
                (self.workspace_id, record_id),
            ).fetchall()
            for row in audit_rows:
                child = row_dict(row)
                self._authorize_delete("sqag_audit_events", str(child["event_id"]))
                self.connection.execute(
                    "delete from sqag_audit_events where workspace_id = ? and event_id = ?",
                    (self.workspace_id, child["event_id"]),
                )
                removed.append(("sqag_audit_events", str(child["event_id"]), str(child["original_retention_expires_at"])))
            rows = self.connection.execute("select history_id, original_retention_expires_at from sqag_feedback_status_history where workspace_id = ? and feedback_id = ?", (self.workspace_id, record_id)).fetchall()
            for row in rows:
                child = row_dict(row)
                self.connection.execute("delete from sqag_feedback_status_history where workspace_id = ? and history_id = ?", (self.workspace_id, child["history_id"]))
                removed.append(("sqag_feedback_status_history", str(child["history_id"]), str(child["original_retention_expires_at"])))
            telemetry_rows = self.connection.execute(
                "select event_id, original_retention_expires_at from sqag_telemetry_events "
                "where workspace_id = ? and support_reference = ?",
                (self.workspace_id, record_id),
            ).fetchall()
            for row in telemetry_rows:
                child = row_dict(row)
                self._authorize_delete("sqag_telemetry_events", str(child["event_id"]))
                self.connection.execute(
                    "delete from sqag_telemetry_events where workspace_id = ? and event_id = ?",
                    (self.workspace_id, child["event_id"]),
                )
                removed.append(("sqag_telemetry_events", str(child["event_id"]), str(child["original_retention_expires_at"])))
            cursor = self.connection.execute("delete from sqag_feedback where workspace_id = ? and feedback_id = ?", (self.workspace_id, record_id))
        if getattr(cursor, "rowcount", 0) != 1:
            raise RuntimeError("retention_parent_delete_incomplete")
        removed.append(("sqag_generation_runs" if kind == "generation_run" else "sqag_feedback", record_id, str(item["original_retention_expires_at"])))
        for record_type, removed_id, original in removed:
            self._receipt(record_type, removed_id, original, now_text)
        return removed

    def enforce_retention(
        self,
        *,
        now: dt.datetime | None = None,
        batch_size: int = 100,
        apply: bool = True,
        artifact_delete: Callable[
            [dict[str, Any], Callable[..., list[tuple[str, str, str]]]], bool
        ]
        | None = None,
    ) -> RetentionResult:
        now_text = iso_timestamp(now or utc_now())
        batch_size = max(1, min(int(batch_size or 100), 500))
        scan_limit = min(5000, max(batch_size, batch_size * 16))
        query_limit = scan_limit + 1
        runs = [
            dict(row_dict(row), _kind="generation_run")
            for row in self.connection.execute(
                "select * from sqag_generation_runs "
                "where workspace_id = ? and retention_expires_at <= ? "
                "order by case when deletion_claimed_at is null then 0 else 1 end, "
                "deletion_claimed_at, retention_expires_at, run_id limit ?",
                (self.workspace_id, now_text, query_limit),
            ).fetchall()
        ]
        feedback = [
            dict(row_dict(row), _kind="feedback")
            for row in self.connection.execute(
                "select * from sqag_feedback "
                "where workspace_id = ? and retention_expires_at <= ? "
                "order by case when deletion_claimed_at is null then 0 else 1 end, "
                "deletion_claimed_at, retention_expires_at, feedback_id limit ?",
                (self.workspace_id, now_text, query_limit),
            ).fetchall()
        ]
        telemetry = [
            dict(row_dict(row), _kind="telemetry_event")
            for row in self.connection.execute(
                "select * from sqag_telemetry_events "
                "where workspace_id = ? and retention_expires_at <= ? "
                "order by case when deletion_claimed_at is null then 0 else 1 end, "
                "deletion_claimed_at, retention_expires_at, source_sequence, event_id limit ?",
                (self.workspace_id, now_text, query_limit),
            ).fetchall()
        ]
        cursor = row_dict(self.connection.execute(
            "select last_retention_expires_at, last_record_id "
            "from sqag_retention_scan_cursors "
            "where workspace_id = ? and candidate_type = 'standalone_audit'",
            (self.workspace_id,),
        ).fetchone()) if apply else {}

        def standalone_candidates(after: dict[str, Any]) -> list[dict[str, Any]]:
            cursor_clause = ""
            parameters: list[Any] = [self.workspace_id, now_text]
            if after:
                cursor_clause = (
                    "and (retention_expires_at > ? or "
                    "(retention_expires_at = ? and event_id > ?)) "
                )
                cursor_expiry = str(after.get("last_retention_expires_at") or "")
                parameters.extend((cursor_expiry, cursor_expiry, str(after.get("last_record_id") or "")))
            parameters.append(query_limit)
            return [
                dict(row_dict(row), _kind="standalone_audit")
                for row in self.connection.execute(
                    "select * from sqag_audit_events where workspace_id = ? "
                    "and run_id is null and feedback_id is null "
                    "and retention_expires_at <= ? " + cursor_clause +
                    "order by retention_expires_at, event_id limit ?",
                    tuple(parameters),
                ).fetchall()
            ]

        standalone_audits = standalone_candidates(cursor)
        if apply and cursor and not standalone_audits:
            self.connection.execute(
                "delete from sqag_retention_scan_cursors "
                "where workspace_id = ? and candidate_type = 'standalone_audit'",
                (self.workspace_id,),
            )
            self.connection.commit()
            standalone_audits = standalone_candidates({})
        receipts = [
            dict(row_dict(row), _kind="deletion_receipt")
            for row in self.connection.execute(
                "select * from sqag_deletion_receipts where workspace_id = ? "
                "and retention_expires_at <= ? "
                "order by retention_expires_at, receipt_id limit ?",
                (self.workspace_id, now_text, query_limit),
            ).fetchall()
        ]

        def candidate_key(row: dict[str, Any]) -> tuple[Any, ...]:
            kind = str(row.get("_kind") or "")
            record_id = str(
                row.get("feedback_id")
                if kind == "feedback"
                else row.get("run_id")
                if kind == "generation_run"
                else row.get("receipt_id")
                if kind == "deletion_receipt"
                else row.get("event_id")
                or ""
            )
            return (
                0 if not row.get("deletion_claimed_at") else 1,
                str(row.get("deletion_claimed_at") or ""),
                str(row.get("retention_expires_at") or ""),
                record_id,
            )

        groups = tuple(
            sorted(group, key=candidate_key)
            for group in (feedback, runs, standalone_audits, receipts)
        )
        merged: list[dict[str, Any]] = []
        for offset in range(max((len(group) for group in groups), default=0)):
            for group in groups:
                if offset < len(group):
                    merged.append(group[offset])
        merged.extend(sorted(telemetry, key=candidate_key))
        has_more = bool(
            len(runs) > scan_limit
            or len(feedback) > scan_limit
            or len(telemetry) > scan_limit
            or len(standalone_audits) > scan_limit
            or len(receipts) > scan_limit
            or len(merged) > scan_limit
        )
        candidates = merged[:scan_limit]
        examined = deleted = held = failed = review_required = parents = actions = 0
        standalone_examined = standalone_deleted = standalone_held = standalone_failed = 0
        receipt_examined = receipt_deleted = receipt_failed = 0
        telemetry_examined = telemetry_deleted = telemetry_held = telemetry_failed = 0
        publication_retained = 0

        def mark_examined(kind: str, record_id: str, *, state: str = "") -> None:
            if not apply or kind in {"standalone_audit", "deletion_receipt"}:
                return
            if kind == "telemetry_event":
                self.connection.execute(
                    "update sqag_telemetry_events set deletion_claimed_at = ?, deletion_state = ? "
                    "where workspace_id = ? and event_id = ?",
                    (now_text, state or "active", self.workspace_id, record_id),
                )
                self.connection.commit()
                return
            table = "sqag_generation_runs" if kind == "generation_run" else "sqag_feedback"
            id_column = "run_id" if kind == "generation_run" else "feedback_id"
            if state:
                self.connection.execute(
                    f"update {table} set deletion_claimed_at = ?, deletion_state = ? "
                    f"where workspace_id = ? and {id_column} = ?",
                    (now_text, state, self.workspace_id, record_id),
                )
            else:
                self.connection.execute(
                    f"update {table} set deletion_claimed_at = ? "
                    f"where workspace_id = ? and {id_column} = ?",
                    (now_text, self.workspace_id, record_id),
                )
            self.connection.commit()

        def advance_standalone_cursor(item: dict[str, Any], record_id: str) -> None:
            if not apply:
                return
            self._acquire_transaction_locks(
                ("retention_cursor", "standalone_audit")
            )
            self.connection.execute(
                "insert into sqag_retention_scan_cursors "
                "(workspace_id, candidate_type, last_retention_expires_at, last_record_id, updated_at) "
                "values (?, 'standalone_audit', ?, ?, ?) "
                "on conflict(workspace_id, candidate_type) do update set "
                "last_retention_expires_at = excluded.last_retention_expires_at, "
                "last_record_id = excluded.last_record_id, updated_at = excluded.updated_at "
                "where excluded.last_retention_expires_at > sqag_retention_scan_cursors.last_retention_expires_at "
                "or (excluded.last_retention_expires_at = sqag_retention_scan_cursors.last_retention_expires_at "
                "and excluded.last_record_id > sqag_retention_scan_cursors.last_record_id)",
                (self.workspace_id, str(item.get("retention_expires_at") or ""), record_id, now_text),
            )
            self.connection.commit()

        for candidate in candidates:
            if actions >= batch_size:
                break
            examined += 1
            item = dict(candidate)
            kind = str(item.pop("_kind"))
            record_id = str(
                item.get("feedback_id")
                if kind == "feedback"
                else item.get("run_id")
                if kind == "generation_run"
                else item.get("receipt_id")
                if kind == "deletion_receipt"
                else item.get("event_id")
                or ""
            )
            if kind == "standalone_audit":
                standalone_examined += 1
                if bool(item.get("legal_hold")) or self._active_hold(
                    "audit_event", record_id
                ):
                    held += 1
                    standalone_held += 1
                    advance_standalone_cursor(item, record_id)
                    continue
                actions += 1
                if not apply:
                    continue
                try:
                    self._acquire_transaction_locks(("audit_event", record_id))
                    current = row_dict(
                        self.connection.execute(
                            "select * from sqag_audit_events where workspace_id = ? "
                            "and event_id = ? and run_id is null and feedback_id is null",
                            (self.workspace_id, record_id),
                        ).fetchone()
                    )
                    current_expiry = parse_timestamp(current.get("retention_expires_at"))
                    retention_now = parse_timestamp(now_text)
                    if (
                        not current
                        or bool(current.get("legal_hold"))
                        or self._active_hold("audit_event", record_id)
                        or current_expiry is None
                        or retention_now is None
                        or current_expiry > retention_now
                    ):
                        raise RetentionGraphHeld("standalone_audit_became_protected")
                    self._authorize_delete("sqag_audit_events", record_id)
                    delete_cursor = self.connection.execute(
                        "delete from sqag_audit_events where workspace_id = ? and event_id = ?",
                        (self.workspace_id, record_id),
                    )
                    if getattr(delete_cursor, "rowcount", 0) != 1:
                        raise RuntimeError("standalone_audit_delete_incomplete")
                    self._receipt(
                        "sqag_audit_events",
                        record_id,
                        str(current["original_retention_expires_at"]),
                        now_text,
                    )
                    self.connection.commit()
                    deleted += 1
                    standalone_deleted += 1
                    advance_standalone_cursor(item, record_id)
                except RetentionGraphHeld:
                    self.connection.rollback()
                    actions -= 1
                    held += 1
                    standalone_held += 1
                    advance_standalone_cursor(item, record_id)
                except Exception:
                    self.connection.rollback()
                    failed += 1
                    standalone_failed += 1
                    advance_standalone_cursor(item, record_id)
                continue
            if kind == "deletion_receipt":
                receipt_examined += 1
                actions += 1
                if not apply:
                    continue
                try:
                    self._acquire_transaction_locks(("deletion_receipt", record_id))
                    current = row_dict(
                        self.connection.execute(
                            "select * from sqag_deletion_receipts "
                            "where workspace_id = ? and receipt_id = ?",
                            (self.workspace_id, record_id),
                        ).fetchone()
                    )
                    current_expiry = parse_timestamp(current.get("retention_expires_at"))
                    retention_now = parse_timestamp(now_text)
                    if (
                        not current
                        or current_expiry is None
                        or retention_now is None
                        or current_expiry > retention_now
                    ):
                        raise RetentionGraphHeld("deletion_receipt_became_ineligible")
                    delete_cursor = self.connection.execute(
                        "delete from sqag_deletion_receipts "
                        "where workspace_id = ? and receipt_id = ?",
                        (self.workspace_id, record_id),
                    )
                    if getattr(delete_cursor, "rowcount", 0) != 1:
                        raise RuntimeError("deletion_receipt_delete_incomplete")
                    self.connection.commit()
                    deleted += 1
                    receipt_deleted += 1
                except RetentionGraphHeld:
                    self.connection.rollback()
                    actions -= 1
                    held += 1
                except Exception:
                    self.connection.rollback()
                    failed += 1
                    receipt_failed += 1
                continue
            if kind == "telemetry_event":
                telemetry_examined += 1
                if (
                    bool(item.get("legal_hold"))
                    or self._active_hold("telemetry_event", record_id)
                    or self._telemetry_event_graph_held(item)
                ):
                    if apply:
                        self._record_retention_telemetry(
                            "retention",
                            "held",
                            record_id,
                            purpose="candidate_held",
                            now_text=now_text,
                        )
                    telemetry_held += 1
                    mark_examined(kind, record_id, state="review_required")
                    continue
                if not apply:
                    continue
                try:
                    self._acquire_transaction_locks(("telemetry_event", record_id))
                    current = row_dict(
                        self.connection.execute(
                            "select * from sqag_telemetry_events where workspace_id = ? and event_id = ?",
                            (self.workspace_id, record_id),
                        ).fetchone()
                    )
                    current_expiry = parse_timestamp(current.get("retention_expires_at"))
                    retention_now = parse_timestamp(now_text)
                    if (
                        not current
                        or bool(current.get("legal_hold"))
                        or self._active_hold("telemetry_event", record_id)
                        or self._telemetry_event_graph_held(current)
                        or current_expiry is None
                        or retention_now is None
                        or current_expiry > retention_now
                    ):
                        raise RetentionGraphHeld("telemetry_event_became_protected")
                    self._record_retention_telemetry(
                        "retention",
                        "requested",
                        record_id,
                        purpose="candidate_delete",
                        now_text=now_text,
                    )
                    self._authorize_delete("sqag_telemetry_events", record_id)
                    delete_cursor = self.connection.execute(
                        "delete from sqag_telemetry_events where workspace_id = ? and event_id = ?",
                        (self.workspace_id, record_id),
                    )
                    if getattr(delete_cursor, "rowcount", 0) != 1:
                        raise RuntimeError("telemetry_event_delete_incomplete")
                    self._receipt(
                        "sqag_telemetry_events",
                        record_id,
                        str(current["original_retention_expires_at"]),
                        now_text,
                    )
                    self._record_retention_telemetry(
                        "deletion",
                        "deleted",
                        record_id,
                        purpose="expired_record_deleted",
                        now_text=now_text,
                    )
                    self.connection.commit()
                    telemetry_deleted += 1
                except RetentionGraphHeld:
                    self.connection.rollback()
                    telemetry_held += 1
                    mark_examined(kind, record_id, state="review_required")
                except Exception:
                    self.connection.rollback()
                    telemetry_failed += 1
                    mark_examined(kind, record_id, state="delete_failed")
                continue
            if kind == "feedback" and item.get("status") not in FEEDBACK_CLOSED_STATES:
                review_required += 1
                mark_examined(kind, record_id, state="review_required")
                continue
            graph_held = (
                self._run_graph_held(record_id)
                if kind == "generation_run"
                else self._feedback_graph_held(record_id)
            )
            retained_child = (
                self._retained_run_child(record_id, now_text)
                if kind == "generation_run"
                else False
            )
            if graph_held or retained_child:
                if apply:
                    self._record_retention_telemetry(
                        "retention",
                        "held",
                        record_id,
                        purpose="candidate_held",
                        now_text=now_text,
                    )
                held += 1
                mark_examined(kind, record_id)
                continue
            parents += 1
            actions += 1
            if not apply:
                continue
            try:
                self._record_retention_telemetry(
                    "retention",
                    "requested",
                    record_id,
                    purpose="candidate_delete",
                    now_text=now_text,
                )
                removed: list[tuple[str, str, str]] = []
                if kind == "generation_run" and artifact_delete is not None:
                    # The existing artifact adapter owns a separate storage
                    # transaction. Persist the retention request before
                    # crossing that boundary so SQLite does not retain a
                    # write lock while the adapter classifies the version.
                    self.connection.commit()

                    def finalize_graph(
                        connection: Any,
                        *,
                        require_session_exclusive: bool = False,
                    ) -> list[tuple[str, str, str]]:
                        target = (
                            self
                            if connection is self.connection
                            else ForensicStore(
                                connection,
                                self.workspace_id,
                                self.actor_tracking_id,
                                actor_key_version_value=self.actor_key_version,
                            )
                        )
                        graph_removed = target._delete_retention_graph(
                            kind,
                            item,
                            record_id,
                            now_text,
                            require_session_exclusive=require_session_exclusive,
                        )
                        removed.extend(graph_removed)
                        return graph_removed

                    if not artifact_delete(item, finalize_graph):
                        raise RuntimeError("artifact_delete_incomplete")
                    if not removed:
                        raise RuntimeError("retention_graph_delete_incomplete")
                else:
                    removed = self._delete_retention_graph(
                        kind, item, record_id, now_text
                    )
                self._record_retention_telemetry(
                    "deletion",
                    "deleted",
                    record_id,
                    purpose="expired_record_deleted",
                    now_text=now_text,
                )
                self.connection.commit()
                deleted += len(removed)
            except RetentionPublicationDependency:
                self.connection.rollback()
                parents -= 1
                actions -= 1
                held += 1
                publication_retained += 1
                mark_examined(kind, record_id)
            except RetentionGraphHeld:
                self.connection.rollback()
                parents -= 1
                actions -= 1
                held += 1
                mark_examined(kind, record_id)
            except Exception:
                self.connection.rollback()
                failed += 1
                table = "sqag_generation_runs" if kind == "generation_run" else "sqag_feedback"
                id_column = "run_id" if kind == "generation_run" else "feedback_id"
                self.connection.execute(
                    f"update {table} set deletion_state = 'delete_failed', "
                    "deletion_error_code = 'retention_partial_failure', "
                    f"deletion_claimed_at = ? where workspace_id = ? and {id_column} = ?",
                    (now_text, self.workspace_id, record_id),
                )
                self.connection.commit()
        scan_exhausted = bool(
            actions < batch_size and has_more and examined >= scan_limit
        )
        return RetentionResult(
            examined,
            deleted,
            held,
            parents,
            failed,
            review_required,
            scan_limit,
            scan_exhausted,
            standalone_examined,
            standalone_deleted,
            standalone_held,
            standalone_failed,
            receipt_examined,
            receipt_deleted,
            receipt_failed,
            publication_retained,
            telemetry_examined,
            telemetry_deleted,
            telemetry_held,
            telemetry_failed,
        )

    def reconcile_non_terminal_runs(
        self,
        *,
        active_job_ids: Iterable[str] = (),
        now: dt.datetime | None = None,
        stale_after_seconds: int = 900,
        batch_size: int = 100,
    ) -> int:
        now = now or utc_now()
        now_text = iso_timestamp(now)
        cutoff = iso_timestamp(
            now - dt.timedelta(seconds=max(60, int(stale_after_seconds)))
        )
        active = {
            safe_reference(item, "job-")
            for item in active_job_ids
            if safe_reference(item, "job-")
        }
        rows = self.connection.execute(
            "select run_id, job_id from sqag_generation_runs "
            "where workspace_id = ? and status in ('received','queued','running') "
            "and started_at <= ? order by started_at, run_id limit ?",
            (self.workspace_id, cutoff, max(1, min(int(batch_size), 500))),
        ).fetchall()
        reconciled = 0
        for candidate in rows:
            candidate_item = row_dict(candidate)
            run_id = safe_reference(candidate_item.get("run_id"), "run-")
            if not run_id or safe_reference(candidate_item.get("job_id"), "job-") in active:
                continue
            try:
                self._acquire_transaction_locks(("generation_run", run_id))
                run = row_dict(
                    self.connection.execute(
                        "select run_id, job_id, job_type, status, started_at, "
                        "actor_tracking_id, actor_key_version "
                        "from sqag_generation_runs where workspace_id = ? and run_id = ?",
                        (self.workspace_id, run_id),
                    ).fetchone()
                )
                if (
                    run.get("status") not in NON_TERMINAL_RUN_STATES
                    or safe_feedback_text(run.get("started_at"), 40) > cutoff
                    or safe_reference(run.get("job_id"), "job-") in active
                ):
                    self.connection.commit()
                    continue
                request_row = row_dict(
                    self.connection.execute(
                        "select evidence_schema_version, evidence_json, evidence_sha256 "
                        "from sqag_generation_evidence where workspace_id = ? and run_id = ? "
                        "and evidence_type = 'request_manifest' "
                        "order by created_at, evidence_id limit 1",
                        (self.workspace_id, run_id),
                    ).fetchone()
                )
                request_reference: dict[str, Any] = {
                    "evidence_type": "request_manifest",
                    "sha256": safe_feedback_text(
                        request_row.get("evidence_sha256"), 64
                    ),
                    "schema": safe_feedback_text(
                        request_row.get("evidence_schema_version"), 120
                    ),
                }
                raw_request = str(request_row.get("evidence_json") or "")
                if (
                    raw_request
                    and hashlib.sha256(raw_request.encode("utf-8")).hexdigest()
                    == request_reference["sha256"]
                ):
                    try:
                        parsed_request = json.loads(raw_request)
                    except json.JSONDecodeError:
                        parsed_request = {}
                    if isinstance(parsed_request, dict):
                        for key in (
                            "job_id",
                            "image_count",
                            "has_quote_session",
                            "profile_id",
                            "pricing_reference_id",
                            "payload_shape_sha256",
                            "attempt_number",
                        ):
                            value = parsed_request.get(key)
                            if isinstance(value, (str, int, bool)):
                                request_reference[key] = value
                manifest = {
                    "schema": EVIDENCE_SCHEMA_VERSION,
                    "generation_schema_version": 1,
                    "generation_run_id": run_id,
                    "workspace_id": self.workspace_id,
                    "actor_tracking_id": safe_feedback_text(
                        run.get("actor_tracking_id"), 160
                    ),
                    "actor_key_version": safe_feedback_text(
                        run.get("actor_key_version"), 80
                    ),
                    "job_id": safe_reference(run.get("job_id"), "job-"),
                    "job_type": safe_feedback_text(run.get("job_type"), 40),
                    "lifecycle_stage": "startup_reconciliation",
                    "terminal_status": "abandoned",
                    "terminal_state": "abandoned",
                    "terminal_at": now_text,
                    "error_category": "interrupted_run_reconciliation",
                    "reconciliation": {"reconciled_at": now_text},
                    "request_evidence": request_reference,
                    "artifacts": [],
                }
                cursor = self.connection.execute(
                    "update sqag_generation_runs set status = 'abandoned', "
                    "error_category = 'interrupted_run_reconciliation', completed_at = ? "
                    "where workspace_id = ? and run_id = ? and completed_at is null "
                    "and status in ('received','queued','running')",
                    (now_text, self.workspace_id, run_id),
                )
                if getattr(cursor, "rowcount", 0) != 1:
                    self.connection.rollback()
                    continue
                self.append_evidence(
                    run_id, "generation_manifest", manifest, now=now, commit=False
                )
                self.append_audit(
                    "generation_abandoned",
                    {"reason": "interrupted_run_reconciliation"},
                    run_id=run_id,
                    now=now,
                    commit=False,
                )
                self.append_telemetry_event(
                    "abandonment",
                    "abandoned",
                    event_id=self.telemetry_event_id("telemetry-abandonment", run_id),
                    action_reference=run_id,
                    run_reference=run_id,
                    purpose="interrupted_run_reconciliation",
                    failure_class="generator_error",
                    now=now,
                    commit=False,
                )
                self.append_telemetry_event(
                    "reconciliation",
                    "reconciled",
                    event_id=self.telemetry_event_id("telemetry-reconciliation", run_id),
                    action_reference=run_id,
                    run_reference=run_id,
                    purpose="startup_reconciliation",
                    now=now,
                    commit=False,
                )
                self.connection.commit()
                reconciled += 1
            except Exception:
                self.connection.rollback()
                raise
        return reconciled
