"""Privacy-minimized generation evidence, feedback, and retention primitives."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any


EVIDENCE_RETENTION_YEARS = 3
PRODUCTION_LOG_RETENTION_DAYS = 90
LOCAL_UAT_LOG_RETENTION_DAYS = 30
FEEDBACK_STATUSES = {"open", "triaged", "in_progress", "resolved", "closed"}
FEEDBACK_CATEGORIES = {"bug", "incorrect_output", "failed_process", "usability", "general"}
FEEDBACK_IMPACTS = {"low", "medium", "high", "blocking"}
FEEDBACK_LINK_CHOICES = {"automatic", "current", "manual", "none"}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


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


def safe_reference(value: Any, prefix: str = "") -> str:
    raw = str(value or "").strip()
    if not raw or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", raw):
        return ""
    return raw if not prefix or raw.startswith(prefix) else ""


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


@dataclass(frozen=True)
class RetentionResult:
    examined: int
    deleted: int
    held: int


class ForensicStore:
    """Database-neutral store; callers provide a SQLite or Postgres adapter."""

    def __init__(self, connection: Any, workspace_id: str, actor_tracking_id: str) -> None:
        self.connection = connection
        self.workspace_id = safe_reference(workspace_id) or "local-workspace"
        self.actor_tracking_id = safe_reference(actor_tracking_id) or "local-user"

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}-{secrets.token_hex(12)}"

    @staticmethod
    def new_support_reference() -> str:
        return f"SQAG-FB-{secrets.token_hex(5).upper()}"

    def _retention(self, now: dt.datetime | None = None) -> tuple[str, str]:
        expiry = iso_timestamp(add_calendar_years(now or utc_now()))
        return expiry, expiry

    def _owned_run(self, run_id: Any) -> dict[str, Any]:
        safe_run_id = safe_reference(run_id, "run-")
        if not safe_run_id:
            return {}
        row = self.connection.execute(
            "select run_id, quote_session_id, status, started_at from sqag_generation_runs where workspace_id = ? and actor_tracking_id = ? and run_id = ?",
            (self.workspace_id, self.actor_tracking_id, safe_run_id),
        ).fetchone()
        return row_dict(row)

    def _latest_owned_run(self) -> dict[str, Any]:
        row = self.connection.execute(
            "select run_id, quote_session_id, status, started_at from sqag_generation_runs where workspace_id = ? and actor_tracking_id = ? order by started_at desc limit 1",
            (self.workspace_id, self.actor_tracking_id),
        ).fetchone()
        return row_dict(row)

    def feedback_context(
        self,
        *,
        run_id: Any = "",
        validated_session_id: Any = "",
        session_status: Any = "",
        session_created_at: Any = "",
    ) -> dict[str, str]:
        run = self._owned_run(run_id)
        if run:
            safe_run = str(run["run_id"])
            return {
                "link_type": "generation_run",
                "run_id": safe_run,
                "session_id": safe_reference(run.get("quote_session_id")),
                "short_reference": safe_run[-12:],
                "label": f"Generation run ...{safe_run[-8:]}",
                "status": safe_feedback_text(run.get("status"), 40),
                "created_at": safe_feedback_text(run.get("started_at"), 40),
                "source": "current_generation",
            }
        safe_session = safe_reference(validated_session_id)
        if safe_session:
            return {
                "link_type": "quote_session",
                "run_id": "",
                "session_id": safe_session,
                "short_reference": safe_session[-12:],
                "label": f"Quote session ...{safe_session[-8:]}",
                "status": safe_feedback_text(session_status, 40),
                "created_at": safe_feedback_text(session_created_at, 40),
                "source": "current_session",
            }
        run = self._latest_owned_run()
        if run:
            safe_run = str(run["run_id"])
            return {
                "link_type": "generation_run",
                "run_id": safe_run,
                "session_id": safe_reference(run.get("quote_session_id")),
                "short_reference": safe_run[-12:],
                "label": f"Recent generation ...{safe_run[-8:]}",
                "status": safe_feedback_text(run.get("status"), 40),
                "created_at": safe_feedback_text(run.get("started_at"), 40),
                "source": "recent_generation",
            }
        return {
            "link_type": "none",
            "run_id": "",
            "session_id": "",
            "short_reference": "",
            "label": "No quote context will be linked",
            "status": "",
            "created_at": "",
            "source": "none",
        }

    def record_run_started(self, job_type: str, summary: dict[str, Any], *, run_id: str = "") -> str:
        now = utc_now()
        run_id = safe_reference(run_id, "run-") or self.new_id("run")
        expiry, original = self._retention(now)
        self.connection.execute(
            "insert into sqag_generation_runs (run_id, workspace_id, actor_tracking_id, job_type, status, started_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, self.workspace_id, self.actor_tracking_id, safe_feedback_text(job_type, 40) or "unknown", "received", iso_timestamp(now), expiry, original, 0),
        )
        self.append_evidence(run_id, "request_summary", summary, now=now, commit=False)
        self.append_audit("generation_received", {"job_type": safe_feedback_text(job_type, 40) or "unknown"}, run_id=run_id, now=now, commit=False)
        self.connection.commit()
        return run_id

    def finish_run(self, run_id: str, status: str, *, error_category: str = "", quote_session_id: str = "", result_summary: dict[str, Any] | None = None) -> None:
        safe_status = status if status in {"blocked", "completed", "failed", "needs_confirmation"} else "failed"
        now = utc_now()
        cursor = self.connection.execute(
            "update sqag_generation_runs set status = ?, error_category = ?, quote_session_id = ?, completed_at = ? where workspace_id = ? and run_id = ?",
            (safe_status, safe_feedback_text(error_category, 80), safe_reference(quote_session_id), iso_timestamp(now), self.workspace_id, safe_reference(run_id, "run-")),
        )
        if getattr(cursor, "rowcount", 0) != 1:
            self.connection.rollback()
            raise ValueError("Generation run could not be finalized.")
        if result_summary is not None:
            self.append_evidence(run_id, "result_summary", result_summary, now=now, commit=False)
        self.append_audit(f"generation_{safe_status}", {"error_category": safe_feedback_text(error_category, 80)}, run_id=run_id, now=now, commit=False)
        self.connection.commit()

    def append_evidence(self, run_id: str, evidence_type: str, evidence: dict[str, Any], *, now: dt.datetime | None = None, commit: bool = True) -> str:
        now = now or utc_now()
        evidence_id = self.new_id("evidence")
        body, digest = digest_json(evidence)
        expiry, original = self._retention(now)
        self.connection.execute(
            "insert into sqag_generation_evidence (evidence_id, run_id, workspace_id, evidence_type, evidence_json, evidence_sha256, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (evidence_id, safe_reference(run_id, "run-"), self.workspace_id, safe_feedback_text(evidence_type, 80), body, digest, iso_timestamp(now), expiry, original, 0),
        )
        if commit:
            self.connection.commit()
        return evidence_id

    def append_audit(self, event_type: str, details: dict[str, Any], *, run_id: str = "", now: dt.datetime | None = None, commit: bool = True) -> str:
        now = now or utc_now()
        event_id = self.new_id("audit")
        body, _digest = digest_json(details)
        expiry, original = self._retention(now)
        self.connection.execute(
            "insert into sqag_audit_events (event_id, run_id, workspace_id, event_type, event_json, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, safe_reference(run_id, "run-"), self.workspace_id, safe_feedback_text(event_type, 80), body, iso_timestamp(now), expiry, original, 0),
        )
        if commit:
            self.connection.commit()
        return event_id

    def submit_feedback(self, payload: dict[str, Any]) -> dict[str, str]:
        category = safe_feedback_text(payload.get("category"), 40)
        if category not in FEEDBACK_CATEGORIES:
            raise ValueError("Select a valid feedback category.")
        title = safe_feedback_text(payload.get("title"), 120)
        message = safe_feedback_text(payload.get("message"))
        if not title:
            raise ValueError("Feedback title is required.")
        if not message:
            raise ValueError("Feedback description is required.")
        impact = safe_feedback_text(payload.get("impact"), 20)
        if impact not in FEEDBACK_IMPACTS:
            impact = "medium"
        expected_result = safe_feedback_text(payload.get("expected_result"), 2000)
        actual_result = safe_feedback_text(payload.get("actual_result"), 2000)
        reproduction_steps = safe_feedback_text(payload.get("reproduction_steps"), 3000)
        manual_reference = safe_manual_reference(payload.get("manual_reference"))
        include_link = payload.get("include_link") is not False
        link_choice = safe_feedback_text(payload.get("link_choice"), 20)
        if link_choice not in FEEDBACK_LINK_CHOICES:
            link_choice = "automatic" if include_link else "none"

        run = self._owned_run(payload.get("run_id")) if include_link else {}
        run_id = safe_reference(run.get("run_id"), "run-")
        session_id = safe_reference(payload.get("validated_session_id")) if include_link else ""
        if run and not session_id:
            session_id = safe_reference(run.get("quote_session_id"))

        resolved_type = ""
        resolved_id = ""
        manual_status = "none"
        if manual_reference:
            manual_status = "unresolved"
            manual_run = self._owned_run(manual_reference)
            if manual_run:
                resolved_type = "generation_run"
                resolved_id = str(manual_run["run_id"])
                run_id = run_id or resolved_id
                session_id = session_id or safe_reference(manual_run.get("quote_session_id"))
                manual_status = "resolved"
            else:
                validated_manual_session = safe_reference(payload.get("validated_manual_session_id"))
                if validated_manual_session:
                    resolved_type = "quote_session"
                    resolved_id = validated_manual_session
                    session_id = session_id or validated_manual_session
                    manual_status = "resolved"

        diagnostics_source = payload.get("diagnostic_metadata") if isinstance(payload.get("diagnostic_metadata"), dict) else {}
        diagnostics = {
            "app_revision": safe_feedback_text(diagnostics_source.get("app_revision"), 80),
            "browser_family_major": safe_feedback_text(diagnostics_source.get("browser_family_major"), 80),
            "current_route": safe_feedback_text(diagnostics_source.get("current_route"), 120),
            "job_state": safe_feedback_text(diagnostics_source.get("job_state"), 40),
            "product_area": safe_feedback_text(diagnostics_source.get("product_area"), 80),
            "viewport_bucket": safe_feedback_text(diagnostics_source.get("viewport_bucket"), 40),
        }
        diagnostics_json = canonical_json({key: value for key, value in diagnostics.items() if value})

        now = utc_now()
        feedback_id = self.new_id("feedback")
        support_reference = self.new_support_reference()
        expiry, original = self._retention(now)
        self.connection.execute(
            "insert into sqag_feedback (feedback_id, support_reference, workspace_id, reporter_tracking_id, run_id, session_id, category, title, message, expected_result, actual_result, reproduction_steps, impact, link_choice, manual_reference_text, manual_reference_status, resolved_reference_type, resolved_reference_id, diagnostic_metadata_json, status, created_at, updated_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (feedback_id, support_reference, self.workspace_id, self.actor_tracking_id, run_id or None, session_id or None, category, title, message, expected_result or None, actual_result or None, reproduction_steps or None, impact, link_choice if include_link else "none", manual_reference or None, manual_status, resolved_type or None, resolved_id or None, diagnostics_json, "open", iso_timestamp(now), iso_timestamp(now), expiry, original, 0),
        )
        self.connection.execute(
            "insert into sqag_feedback_status_history (history_id, feedback_id, workspace_id, from_status, to_status, actor_tracking_id, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.new_id("feedback-history"), feedback_id, self.workspace_id, "", "open", self.actor_tracking_id, iso_timestamp(now), expiry, original, 0),
        )
        self.append_audit(
            "feedback_submitted",
            {
                "feedback_id": feedback_id,
                "support_reference": support_reference,
                "category": category,
                "linked_run": bool(run_id),
                "linked_session": bool(session_id),
                "manual_reference_status": manual_status,
            },
            run_id=run_id,
            now=now,
            commit=False,
        )
        self.connection.commit()
        return {
            "feedback_id": feedback_id,
            "feedback_report_id": feedback_id,
            "support_reference": support_reference,
            "manual_reference_status": manual_status,
            "status": "open",
        }

    def set_legal_hold(self, table: str, record_id_column: str, record_id: str, enabled: bool) -> bool:
        allowed = {
            "sqag_generation_runs": "run_id",
            "sqag_generation_evidence": "evidence_id",
            "sqag_audit_events": "event_id",
            "sqag_feedback": "feedback_id",
            "sqag_feedback_status_history": "history_id",
        }
        if allowed.get(table) != record_id_column:
            raise ValueError("Unsupported legal-hold target.")
        safe_id = safe_reference(record_id)
        cursor = self.connection.execute(
            f"update {table} set legal_hold = ? where workspace_id = ? and {record_id_column} = ?",
            (1 if enabled else 0, self.workspace_id, safe_id),
        )
        changed = bool(getattr(cursor, "rowcount", 0))
        if changed:
            self.append_audit(
                "legal_hold_changed",
                {"record_type": table, "record_id": safe_id, "enabled": bool(enabled)},
                commit=False,
            )
        self.connection.commit()
        return changed

    def _parent_has_retained_children(self, table: str, record_id: str, now_text: str) -> bool:
        if table == "sqag_generation_runs":
            for child in ("sqag_generation_evidence", "sqag_audit_events"):
                row = self.connection.execute(
                    f"select 1 from {child} where workspace_id = ? and run_id = ? and (legal_hold = 1 or retention_expires_at > ?) limit 1",
                    (self.workspace_id, record_id, now_text),
                ).fetchone()
                if row:
                    return True
        if table == "sqag_feedback":
            row = self.connection.execute(
                "select 1 from sqag_feedback_status_history where workspace_id = ? and feedback_id = ? and (legal_hold = 1 or retention_expires_at > ?) limit 1",
                (self.workspace_id, record_id, now_text),
            ).fetchone()
            return bool(row)
        return False

    def enforce_retention(self, *, now: dt.datetime | None = None) -> RetentionResult:
        now_text = iso_timestamp(now or utc_now())
        tables = (
            ("sqag_feedback_status_history", "history_id"),
            ("sqag_feedback", "feedback_id"),
            ("sqag_generation_evidence", "evidence_id"),
            ("sqag_audit_events", "event_id"),
            ("sqag_generation_runs", "run_id"),
        )
        examined = deleted = held = 0
        for table, id_column in tables:
            rows = self.connection.execute(
                f"select {id_column}, legal_hold, original_retention_expires_at from {table} where workspace_id = ? and retention_expires_at <= ?",
                (self.workspace_id, now_text),
            ).fetchall()
            for row in rows:
                examined += 1
                item = row_dict(row)
                record_id = str(item[id_column])
                if bool(item.get("legal_hold")) or self._parent_has_retained_children(table, record_id, now_text):
                    held += 1
                    continue
                self.connection.execute(
                    "insert into sqag_deletion_receipts (receipt_id, workspace_id, record_type, record_id, reason, deleted_at, original_retention_expires_at, created_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.new_id("delete"), self.workspace_id, table, record_id, "retention_expired", now_text, str(item["original_retention_expires_at"]), now_text),
                )
                self.connection.execute(f"delete from {table} where workspace_id = ? and {id_column} = ?", (self.workspace_id, record_id))
                deleted += 1
        self.connection.commit()
        return RetentionResult(examined=examined, deleted=deleted, held=held)
