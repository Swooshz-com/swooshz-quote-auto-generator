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
PRODUCTION_LOG_RETENTION_DAYS = 90
LOCAL_UAT_LOG_RETENTION_DAYS = 30
EVIDENCE_SCHEMA_VERSION = "swooshz.sqag.generation-evidence.v2"
FEEDBACK_RETENTION_POLICY_VERSION = "sqag.feedback-retention.v2"
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
}
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


@dataclass(frozen=True)
class RetentionResult:
    examined: int
    deleted: int
    held: int
    parents_processed: int = 0
    failed: int = 0
    review_required: int = 0


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

    def _retention(self, now: dt.datetime | None = None) -> tuple[str, str]:
        expiry = iso_timestamp(add_calendar_years(now or utc_now()))
        return expiry, expiry

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

    def _latest_owned_run(self) -> dict[str, Any]:
        return row_dict(self.connection.execute(
            "select run_id, job_id, quote_session_id, status, started_at from sqag_generation_runs where workspace_id = ? and actor_tracking_id = ? order by started_at desc, run_id desc limit 1",
            (self.workspace_id, self.actor_tracking_id),
        ).fetchone())

    def feedback_context(self, *, run_id: Any = "", validated_session_id: Any = "", session_status: Any = "", session_created_at: Any = "") -> dict[str, str]:
        run = self._owned_run(run_id)
        if run:
            safe_run = str(run["run_id"])
            return {"link_type": "generation_run", "run_id": safe_run, "session_id": safe_reference(run.get("quote_session_id")), "short_reference": safe_run[-12:], "label": f"Generation run ...{safe_run[-8:]}", "status": safe_feedback_text(run.get("status"), 40), "created_at": safe_feedback_text(run.get("started_at"), 40), "source": "current_generation"}
        safe_session = safe_reference(validated_session_id)
        if safe_session:
            return {"link_type": "quote_session", "run_id": "", "session_id": safe_session, "short_reference": safe_session[-12:], "label": f"Quote session ...{safe_session[-8:]}", "status": safe_feedback_text(session_status, 40), "created_at": safe_feedback_text(session_created_at, 40), "source": "current_session"}
        run = self._latest_owned_run()
        if run:
            safe_run = str(run["run_id"])
            return {"link_type": "generation_run", "run_id": safe_run, "session_id": safe_reference(run.get("quote_session_id")), "short_reference": safe_run[-12:], "label": f"Recent generation ...{safe_run[-8:]}", "status": safe_feedback_text(run.get("status"), 40), "created_at": safe_feedback_text(run.get("started_at"), 40), "source": "recent_generation"}
        return {"link_type": "none", "run_id": "", "session_id": "", "short_reference": "", "label": "No quote context will be linked", "status": "", "created_at": "", "source": "none"}

    def record_run_started(
        self,
        job_type: str,
        summary: dict[str, Any],
        *,
        run_id: str = "",
        job_id: str = "",
        idempotency_key: str = "",
        parent_run_id: str = "",
        attempt_number: int = 1,
        app_revision: str = "",
        now: dt.datetime | None = None,
    ) -> str:
        safe_job_id = safe_reference(job_id, "job-") if job_id else ""
        if job_id and not safe_job_id:
            raise ValueError("Generation job identity is invalid.")
        if safe_job_id:
            existing = self.run_for_job(safe_job_id)
            if existing:
                if safe_feedback_text(existing.get("job_type"), 40) not in {"", safe_feedback_text(job_type, 40)}:
                    raise ValueError("Generation job identity is already in use.")
                return str(existing["run_id"])
        now = now or utc_now()
        run_id = safe_reference(run_id, "run-") or self.new_id("run")
        expiry, original = self._retention(now)
        try:
            self.connection.execute(
                "insert into sqag_generation_runs (run_id, workspace_id, actor_tracking_id, actor_key_version, job_id, idempotency_key, parent_run_id, attempt_number, job_type, status, started_at, app_revision, evidence_schema_version, retention_expires_at, original_retention_expires_at, legal_hold, deletion_state) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, self.workspace_id, self.actor_tracking_id, self.actor_key_version, safe_job_id or None, safe_reference(idempotency_key) or safe_job_id or None, safe_reference(parent_run_id, "run-") or None, max(1, int(attempt_number or 1)), safe_feedback_text(job_type, 40) or "unknown", "received", iso_timestamp(now), safe_feedback_text(app_revision, 80) or None, EVIDENCE_SCHEMA_VERSION, expiry, original, 0, "active"),
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
        self.connection.commit()
        return run_id

    def set_run_state(self, run_id: str, status: str) -> None:
        if status not in NON_TERMINAL_RUN_STATES:
            raise ValueError("Generation run state is invalid.")
        cursor = self.connection.execute(
            "update sqag_generation_runs set status = ? where workspace_id = ? and run_id = ? and completed_at is null",
            (status, self.workspace_id, safe_reference(run_id, "run-")),
        )
        if getattr(cursor, "rowcount", 0) != 1:
            self.connection.rollback()
            raise ValueError("Generation run could not be updated.")
        self.append_audit(f"generation_{status}", {}, run_id=run_id, commit=False)
        self.connection.commit()

    def finish_run(self, run_id: str, status: str, *, error_category: str = "", quote_session_id: str = "", result_summary: dict[str, Any] | None = None, canonical_manifest: dict[str, Any] | None = None, now: dt.datetime | None = None) -> None:
        if status not in TERMINAL_RUN_STATES:
            status = "failed"
        now = now or utc_now()
        cursor = self.connection.execute(
            "update sqag_generation_runs set status = ?, error_category = ?, quote_session_id = ?, completed_at = ? where workspace_id = ? and run_id = ? and completed_at is null",
            (status, safe_feedback_text(error_category, 80), safe_reference(quote_session_id), iso_timestamp(now), self.workspace_id, safe_reference(run_id, "run-")),
        )
        if getattr(cursor, "rowcount", 0) != 1:
            existing = row_dict(self.connection.execute("select status from sqag_generation_runs where workspace_id = ? and run_id = ?", (self.workspace_id, safe_reference(run_id, "run-"))).fetchone())
            self.connection.rollback()
            if existing.get("status") == status:
                return
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
        self.connection.commit()

    def compensate_run_start(self, run_id: str, error_category: str = "job_creation_failed") -> None:
        self.finish_run(run_id, "cancelled", error_category=error_category, result_summary={"schema": EVIDENCE_SCHEMA_VERSION, "status": "cancelled", "reason": error_category})

    def append_evidence(self, run_id: str, evidence_type: str, evidence: dict[str, Any], *, now: dt.datetime | None = None, commit: bool = True) -> str:
        now = now or utc_now()
        evidence_id = self.new_id("evidence")
        body, digest = digest_json(evidence)
        expiry, original = self._retention(now)
        self.connection.execute(
            "insert into sqag_generation_evidence (evidence_id, run_id, workspace_id, evidence_type, evidence_schema_version, evidence_json, evidence_sha256, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (evidence_id, safe_reference(run_id, "run-"), self.workspace_id, safe_feedback_text(evidence_type, 80), safe_feedback_text(evidence.get("schema"), 120) or EVIDENCE_SCHEMA_VERSION, body, digest, iso_timestamp(now), expiry, original, 0),
        )
        if commit:
            self.connection.commit()
        return evidence_id

    def append_audit(self, event_type: str, details: dict[str, Any], *, run_id: str = "", now: dt.datetime | None = None, commit: bool = True) -> str:
        now = now or utc_now()
        event_id = self.new_id("audit")
        body, digest = digest_json(details)
        expiry, original = self._retention(now)
        self.connection.execute(
            "insert into sqag_audit_events (event_id, run_id, workspace_id, actor_tracking_id, actor_key_version, event_type, event_json, event_sha256, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, safe_reference(run_id, "run-") or None, self.workspace_id, self.actor_tracking_id, self.actor_key_version, safe_feedback_text(event_type, 80), body, digest, iso_timestamp(now), expiry, original, 0),
        )
        if commit:
            self.connection.commit()
        return event_id

    def verify_run_evidence(self, run_id: str, *, reason_code: str, privileged: bool, artifact_verifier: Callable[[dict[str, Any]], bool] | None = None) -> dict[str, Any]:
        if not privileged:
            raise PermissionError("Forensic evidence is not available.")
        reason = safe_reference(reason_code)
        if not reason:
            raise ValueError("A forensic access reason is required.")
        safe_run_id = safe_reference(run_id, "run-")
        run = row_dict(self.connection.execute("select run_id from sqag_generation_runs where workspace_id = ? and run_id = ?", (self.workspace_id, safe_run_id)).fetchone())
        def failed(reason_code: str) -> dict[str, Any]:
            self.append_audit("forensic_evidence_verification_failed", {"run_id": safe_run_id, "reason_code": reason, "failure": reason_code}, run_id=safe_run_id)
            return {"integrity_ok": False, "run_id": safe_run_id, "reason": reason_code}

        if not run:
            raise LookupError("Forensic evidence is not available.")
        rows = self.connection.execute("select evidence_type, evidence_json, evidence_sha256 from sqag_generation_evidence where workspace_id = ? and run_id = ? order by created_at, evidence_id", (self.workspace_id, safe_run_id)).fetchall()
        manifest: dict[str, Any] = {}
        for row in rows:
            item = row_dict(row)
            raw = str(item.get("evidence_json") or "")
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != item.get("evidence_sha256"):
                return failed("evidence_digest_mismatch")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                return failed("evidence_json_invalid")
            if item.get("evidence_type") == "generation_manifest" and isinstance(parsed, dict):
                manifest = parsed
        if not manifest or manifest.get("schema") != EVIDENCE_SCHEMA_VERSION:
            return failed("manifest_missing")
        for artifact in manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []:
            if not isinstance(artifact, dict) or not SHA256_RE.fullmatch(str(artifact.get("sha256") or "")):
                return failed("artifact_digest_missing")
            if artifact_verifier is not None and not artifact_verifier(artifact):
                return failed("artifact_digest_mismatch")
        self.append_audit("forensic_evidence_accessed", {"run_id": safe_run_id, "reason_code": reason}, run_id=safe_run_id)
        return {"integrity_ok": True, "run_id": safe_run_id, "manifest": manifest}

    def submit_feedback(self, payload: dict[str, Any]) -> dict[str, str]:
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
                session_id = session_id or safe_reference(manual_run.get("quote_session_id"))
            else:
                validated = safe_reference(payload.get("validated_manual_session_id"))
                if validated:
                    resolved_type, resolved_id, manual_status = "quote_session", validated, "resolved"
                    session_id = session_id or validated
        source = payload.get("diagnostic_metadata") if isinstance(payload.get("diagnostic_metadata"), dict) else {}
        diagnostics = {key: safe_feedback_text(source.get(key), limit) for key, limit in {"app_revision": 80, "browser_family_major": 80, "current_route": 120, "job_state": 40, "product_area": 80, "viewport_bucket": 40}.items()}
        now = utc_now()
        feedback_id = self.new_id("feedback")
        support_reference = self.new_support_reference()
        expiry, original = self._retention(now)
        values = (
            feedback_id, support_reference, self.workspace_id, self.actor_tracking_id, self.actor_key_version, run_id or None, session_id or None, category, title, message,
            safe_feedback_text(payload.get("expected_result"), 2000) or None, safe_feedback_text(payload.get("actual_result"), 2000) or None, safe_feedback_text(payload.get("reproduction_steps"), 3000) or None,
            impact, link_choice if include_link else "none", manual_reference or None, manual_status, resolved_type or None, resolved_id or None,
            canonical_json({key: value for key, value in diagnostics.items() if value}), "open", iso_timestamp(now), iso_timestamp(now), expiry, original, expiry, FEEDBACK_RETENTION_POLICY_VERSION, 0, "active",
        )
        self.connection.execute(
            "insert into sqag_feedback (feedback_id, support_reference, workspace_id, reporter_tracking_id, reporter_key_version, run_id, session_id, category, title, message, expected_result, actual_result, reproduction_steps, impact, link_choice, manual_reference_text, manual_reference_status, resolved_reference_type, resolved_reference_id, diagnostic_metadata_json, status, created_at, updated_at, retention_expires_at, original_retention_expires_at, submission_retention_expires_at, retention_policy_version, legal_hold, deletion_state) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        self.connection.execute(
            "insert into sqag_feedback_status_history (history_id, feedback_id, workspace_id, from_status, to_status, actor_tracking_id, actor_key_version, resolution_note, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.new_id("feedback-history"), feedback_id, self.workspace_id, "", "open", self.actor_tracking_id, self.actor_key_version, None, iso_timestamp(now), expiry, original, 0),
        )
        self.append_audit("feedback_submitted", {"feedback_id": feedback_id, "support_reference": support_reference, "category": category, "linked_run": bool(run_id), "linked_session": bool(session_id), "manual_reference_status": manual_status}, run_id=run_id, now=now, commit=False)
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
            self.append_audit("feedback_report_accessed", {"feedback_id": report["feedback_id"]})
        return {"report": report, "status_history": history}

    def update_feedback_status(self, reference: Any, status: str, *, resolution_note: str = "", now: dt.datetime | None = None) -> dict[str, Any]:
        report = self._feedback(reference)
        if not report:
            raise LookupError("Feedback report is not available.")
        current = str(report.get("status") or "")
        status = safe_feedback_text(status, 40)
        if status not in FEEDBACK_STATUSES or status not in FEEDBACK_TRANSITIONS.get(current, set()):
            raise ValueError("Feedback status transition is not allowed.")
        note = safe_feedback_text(resolution_note, 2000)
        now = now or utc_now()
        now_text = iso_timestamp(now)
        closed_at = report.get("closed_at")
        expiry = str(report.get("retention_expires_at") or "")
        if status in FEEDBACK_CLOSED_STATES and not closed_at:
            closed_at = now_text
            expiry = iso_timestamp(add_calendar_years(now))
        self.connection.execute("update sqag_feedback set status = ?, updated_at = ?, closed_at = ?, retention_expires_at = ? where workspace_id = ? and feedback_id = ?", (status, now_text, closed_at, expiry, self.workspace_id, report["feedback_id"]))
        self.connection.execute("insert into sqag_feedback_status_history (history_id, feedback_id, workspace_id, from_status, to_status, actor_tracking_id, actor_key_version, resolution_note, created_at, retention_expires_at, original_retention_expires_at, legal_hold) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (self.new_id("feedback-history"), report["feedback_id"], self.workspace_id, current, status, self.actor_tracking_id, self.actor_key_version, note or None, now_text, expiry, report["original_retention_expires_at"], 0))
        self.append_audit("feedback_status_changed", {"feedback_id": report["feedback_id"], "from_status": current, "to_status": status, "has_resolution_note": bool(note)}, run_id=safe_reference(report.get("run_id"), "run-"), now=now, commit=False)
        self.connection.commit()
        return self._feedback(report["feedback_id"])

    def _target_exists(self, table: str, id_column: str, record_id: str) -> bool:
        return bool(self.connection.execute(f"select 1 from {table} where workspace_id = ? and {id_column} = ? limit 1", (self.workspace_id, record_id)).fetchone())

    def set_legal_hold(self, table: str, record_id_column: str, record_id: str, enabled: bool, *, reason_code: str = "legal_process", case_reference: str = "", now: dt.datetime | None = None) -> bool:
        expected = LEGAL_HOLD_TARGETS.get(table)
        if not expected or expected[0] != record_id_column:
            raise ValueError("Unsupported legal-hold target.")
        safe_id = safe_reference(record_id)
        if not safe_id or not self._target_exists(table, record_id_column, safe_id):
            return False
        target_type = expected[1]
        active = row_dict(self.connection.execute("select * from sqag_legal_holds where workspace_id = ? and target_type = ? and target_id = ? and enabled = 1", (self.workspace_id, target_type, safe_id)).fetchone())
        now = now or utc_now()
        if enabled and active:
            return True
        if not enabled and not active:
            return True
        reason = safe_reference(reason_code)
        if enabled and not reason:
            raise ValueError("Legal-hold reason is required.")
        case = safe_manual_reference(case_reference)
        if enabled:
            self.connection.execute("insert into sqag_legal_holds (hold_id, workspace_id, target_type, target_id, enabled, reason_code, case_reference, actor_tracking_id, actor_key_version, created_at) values (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)", (self.new_id("hold"), self.workspace_id, target_type, safe_id, reason, case or None, self.actor_tracking_id, self.actor_key_version, iso_timestamp(now)))
            event = "legal_hold_applied"
        else:
            self.connection.execute("update sqag_legal_holds set enabled = 0, released_by_tracking_id = ?, released_by_key_version = ?, released_at = ? where hold_id = ? and enabled = 1", (self.actor_tracking_id, self.actor_key_version, iso_timestamp(now), active["hold_id"]))
            event = "legal_hold_released"
        self.append_audit(event, {"target_type": target_type, "target_id": safe_id, "reason_code": reason or active.get("reason_code"), "case_reference": case or active.get("case_reference") or ""}, now=now, commit=False)
        self.append_audit("legal_hold_changed", {"target_type": target_type, "target_id": safe_id, "enabled": enabled}, now=now, commit=False)
        self.connection.commit()
        return True

    def _active_hold(self, target_type: str, target_id: str) -> bool:
        return bool(self.connection.execute("select 1 from sqag_legal_holds where workspace_id = ? and target_type = ? and target_id = ? and enabled = 1 limit 1", (self.workspace_id, target_type, target_id)).fetchone())

    def _run_graph_held(self, run_id: str) -> bool:
        if self._active_hold("generation_run", run_id):
            return True
        queries = (("sqag_generation_evidence", "evidence_id", "generation_evidence"), ("sqag_audit_events", "event_id", "audit_event"))
        for table, column, target in queries:
            row = self.connection.execute(f"select 1 from {table} child join sqag_legal_holds hold on hold.workspace_id = child.workspace_id and hold.target_type = ? and hold.target_id = child.{column} and hold.enabled = 1 where child.workspace_id = ? and child.run_id = ? limit 1", (target, self.workspace_id, run_id)).fetchone()
            if row:
                return True
        return False

    def _feedback_graph_held(self, feedback_id: str) -> bool:
        if self._active_hold("feedback", feedback_id):
            return True
        return bool(self.connection.execute("select 1 from sqag_feedback_status_history child join sqag_legal_holds hold on hold.workspace_id = child.workspace_id and hold.target_type = 'feedback_status_history' and hold.target_id = child.history_id and hold.enabled = 1 where child.workspace_id = ? and child.feedback_id = ? limit 1", (self.workspace_id, feedback_id)).fetchone())

    def _authorize_delete(self, record_type: str, record_id: str) -> None:
        self.connection.execute("insert into sqag_retention_delete_authorizations (authorization_id, workspace_id, record_type, record_id, created_at) values (?, ?, ?, ?, ?)", (self.new_id("retention-auth"), self.workspace_id, record_type, record_id, iso_timestamp(utc_now())))

    def _receipt(self, record_type: str, record_id: str, original_expiry: str, now_text: str) -> None:
        expiry = iso_timestamp(add_calendar_years(parse_timestamp(now_text) or utc_now()))
        self.connection.execute("insert into sqag_deletion_receipts (receipt_id, workspace_id, record_type, record_id, reason, deleted_at, original_retention_expires_at, created_at, retention_expires_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?) on conflict(workspace_id, record_type, record_id) do nothing", (self.new_id("delete"), self.workspace_id, record_type, record_id, "retention_expired", now_text, original_expiry, now_text, expiry))

    def _retained_run_child(self, run_id: str, now_text: str) -> bool:
        if self.connection.execute("select 1 from sqag_feedback where workspace_id = ? and run_id = ? limit 1", (self.workspace_id, run_id)).fetchone():
            return True
        for table in ("sqag_generation_evidence", "sqag_audit_events"):
            if self.connection.execute(f"select 1 from {table} where workspace_id = ? and run_id = ? and retention_expires_at > ? limit 1", (self.workspace_id, run_id, now_text)).fetchone():
                return True
        return False

    def enforce_retention(self, *, now: dt.datetime | None = None, batch_size: int = 100, apply: bool = True, artifact_delete: Callable[[dict[str, Any]], bool] | None = None) -> RetentionResult:
        now_text = iso_timestamp(now or utc_now())
        batch_size = max(1, min(int(batch_size or 100), 500))
        runs = [dict(row_dict(row), _kind="generation_run") for row in self.connection.execute("select * from sqag_generation_runs where workspace_id = ? and retention_expires_at <= ? order by retention_expires_at, run_id limit ?", (self.workspace_id, now_text, batch_size)).fetchall()]
        feedback = [dict(row_dict(row), _kind="feedback") for row in self.connection.execute("select * from sqag_feedback where workspace_id = ? and retention_expires_at <= ? order by retention_expires_at, feedback_id limit ?", (self.workspace_id, now_text, batch_size)).fetchall()]
        candidates = sorted(runs + feedback, key=lambda row: (str(row.get("retention_expires_at")), row["_kind"], str(row.get("run_id") or row.get("feedback_id"))))[:batch_size]
        examined = deleted = held = failed = review_required = parents = 0
        for item in candidates:
            examined += 1
            kind = item.pop("_kind")
            record_id = str(item["run_id"] if kind == "generation_run" else item["feedback_id"])
            if kind == "feedback" and item.get("status") not in FEEDBACK_CLOSED_STATES:
                review_required += 1
                if apply:
                    self.connection.execute("update sqag_feedback set deletion_state = 'review_required' where workspace_id = ? and feedback_id = ?", (self.workspace_id, record_id))
                    self.connection.commit()
                continue
            graph_held = self._run_graph_held(record_id) if kind == "generation_run" else self._feedback_graph_held(record_id)
            retained_child = self._retained_run_child(record_id, now_text) if kind == "generation_run" else False
            if graph_held or retained_child:
                held += 1
                continue
            if not apply:
                continue
            try:
                if kind == "generation_run" and artifact_delete is not None and not artifact_delete(item):
                    raise RuntimeError("artifact_delete_incomplete")
                if (self._run_graph_held(record_id) if kind == "generation_run" else self._feedback_graph_held(record_id)):
                    held += 1
                    self.connection.rollback()
                    continue
                removed: list[tuple[str, str, str]] = []
                if kind == "generation_run":
                    for table, column, record_type in (("sqag_generation_evidence", "evidence_id", "sqag_generation_evidence"), ("sqag_audit_events", "event_id", "sqag_audit_events")):
                        rows = self.connection.execute(f"select {column}, original_retention_expires_at from {table} where workspace_id = ? and run_id = ?", (self.workspace_id, record_id)).fetchall()
                        for row in rows:
                            child = row_dict(row)
                            self._authorize_delete(record_type, str(child[column]))
                            self.connection.execute(f"delete from {table} where workspace_id = ? and {column} = ?", (self.workspace_id, child[column]))
                            removed.append((record_type, str(child[column]), str(child["original_retention_expires_at"])))
                    self.connection.execute("delete from sqag_generation_runs where workspace_id = ? and run_id = ?", (self.workspace_id, record_id))
                else:
                    rows = self.connection.execute("select history_id, original_retention_expires_at from sqag_feedback_status_history where workspace_id = ? and feedback_id = ?", (self.workspace_id, record_id)).fetchall()
                    for row in rows:
                        child = row_dict(row)
                        self.connection.execute("delete from sqag_feedback_status_history where workspace_id = ? and history_id = ?", (self.workspace_id, child["history_id"]))
                        removed.append(("sqag_feedback_status_history", str(child["history_id"]), str(child["original_retention_expires_at"])))
                    self.connection.execute("delete from sqag_feedback where workspace_id = ? and feedback_id = ?", (self.workspace_id, record_id))
                removed.append(("sqag_generation_runs" if kind == "generation_run" else "sqag_feedback", record_id, str(item["original_retention_expires_at"])))
                for record_type, removed_id, original in removed:
                    self._receipt(record_type, removed_id, original, now_text)
                self.connection.commit()
                parents += 1
                deleted += len(removed)
            except Exception:
                self.connection.rollback()
                failed += 1
                self.connection.execute(f"update {'sqag_generation_runs' if kind == 'generation_run' else 'sqag_feedback'} set deletion_state = 'delete_failed', deletion_error_code = 'retention_partial_failure' where workspace_id = ? and {'run_id' if kind == 'generation_run' else 'feedback_id'} = ?", (self.workspace_id, record_id))
                self.connection.commit()
        return RetentionResult(examined, deleted, held, parents, failed, review_required)

    def reconcile_non_terminal_runs(self, *, active_job_ids: Iterable[str] = (), now: dt.datetime | None = None, stale_after_seconds: int = 900, batch_size: int = 100) -> int:
        now = now or utc_now()
        cutoff = iso_timestamp(now - dt.timedelta(seconds=max(60, int(stale_after_seconds))))
        active = {safe_reference(item, "job-") for item in active_job_ids}
        rows = self.connection.execute("select run_id, job_id from sqag_generation_runs where workspace_id = ? and status in ('received','queued','running') and started_at <= ? order by started_at, run_id limit ?", (self.workspace_id, cutoff, max(1, min(int(batch_size), 500)))).fetchall()
        reconciled = 0
        for row in rows:
            item = row_dict(row)
            if safe_reference(item.get("job_id"), "job-") in active:
                continue
            now_text = iso_timestamp(now)
            self.connection.execute("update sqag_generation_runs set status = 'abandoned', error_category = 'interrupted_run_reconciliation', completed_at = ? where workspace_id = ? and run_id = ? and status in ('received','queued','running')", (now_text, self.workspace_id, item["run_id"]))
            self.append_audit("generation_abandoned", {"reason": "interrupted_run_reconciliation"}, run_id=item["run_id"], now=now, commit=False)
            reconciled += 1
        self.connection.commit()
        return reconciled
