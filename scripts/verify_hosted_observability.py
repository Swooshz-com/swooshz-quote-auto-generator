#!/usr/bin/env python3
"""Synthetic hosted observability readiness verifier.

The verifier exercises SQAG's structured log sanitizer, event allowlist,
support error references, and health/readiness metadata with synthetic values
only. It emits metadata-only JSON and does not configure an external vendor.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


POLICY_PATH = ROOT / "docs" / "hosted-observability-policy.json"
REQUIRED_POLICY_KEYS = {
    "allowed_event_categories",
    "forbidden_content",
    "minimum_metadata",
    "support_traceability",
    "health_readiness",
    "retention",
}
SENSITIVE_SYNTHETIC_VALUES = (
    "C:/Users/Private/Koncept Runtime",
    "sqlite:///C:/Users/Private/sqag-storage.sqlite3?token=secret",
    "Acme Private Customer",
    "Generated quote private line item",
    "Private pricing catalog contents",
    "Private profile layout contents",
    "staff.member@example.test",
    "oauth-client-secret-value",
    "swooshz_private_session_cookie",
    "sk-proj-private-api-key",
    "synthetic-private-artifact-bytes",
    "raw provider response text",
)


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_POLICY_KEYS - set(policy))
    if policy.get("schema") != "swooshz.sqag.hosted-observability-policy.v1" or missing:
        raise ValueError("Hosted observability policy is incomplete.")
    if not policy.get("synthetic_verifier_only"):
        raise ValueError("Hosted observability policy must be synthetic-verifier-only.")
    return policy


def jsonl_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def contains_sensitive_value(text: str) -> bool:
    return any(value in text for value in SENSITIVE_SYNTHETIC_VALUES)


def category_names(root: Path) -> list[str]:
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def run_verification(*, work_dir: Path | None = None) -> dict[str, Any]:
    policy = load_policy()
    parent = work_dir or (ROOT / "_tmp" / "hosted-observability")
    run_root = parent / f"run-{time.time_ns()}"
    log_root = run_root / "logs"
    log_root.mkdir(parents=True, exist_ok=True)

    ignored = webapp.write_local_log(
        "chat_message",
        {
            "content": "Generated quote private line item",
            "api_key": "sk-proj-private-api-key",
            "customer_name": "Acme Private Customer",
        },
        log_root=log_root,
    )
    error_reference = webapp.new_error_reference()
    client_logged = webapp.write_local_log(
        "client_error",
        {
            "status": "failed",
            "url": "/callback?code=private-code&state=private-state",
            "error_reference": error_reference,
            "customer_name": "Acme Private Customer",
            "staff_email": "staff.member@example.test",
            "db_url": "sqlite:///C:/Users/Private/sqag-storage.sqlite3?token=secret",
            "private_path": "C:/Users/Private/Koncept Runtime",
            "generated_quote": "Generated quote private line item",
            "pricing_payload": "Private pricing catalog contents",
            "profile_payload": "Private profile layout contents",
            "artifact_bytes": "synthetic-private-artifact-bytes",
            "cookie": "swooshz_private_session_cookie",
            "oauth_value": "oauth-client-secret-value",
            "provider_response": "raw provider response text",
            "api_key": "sk-proj-private-api-key",
        },
        log_root=log_root,
    )
    ai_logged = webapp.log_ai_call_attempt(
        feature="draft_quote_basis",
        provider=webapp.AI_PROVIDER_OPENAI,
        model="gpt-test",
        status="failed",
        duration_ms=123,
        image_count=1,
        pdf_count=0,
        error_reference=error_reference,
        details={
            "prompt": "Private prompt for Acme Private Customer",
            "payload": {"quote_contents": "Generated quote private line item"},
            "provider_response": "raw provider response text",
            "staff_email": "staff.member@example.test",
            "authorization": "Bearer sk-proj-private-api-key",
        },
        log_root=log_root,
    )
    security_logged = webapp.write_local_log(
        "security_event",
        {"reason": "invalid_csrf", "error_reference": error_reference, "token": "swooshz_private_session_cookie"},
        log_root=log_root,
    )

    records = jsonl_records(log_root)
    serialized_records = json.dumps(records, sort_keys=True)
    allowed_categories = set(policy["allowed_event_categories"])
    observed_categories = set(category_names(log_root))
    all_records_have_minimum_metadata = all(
        record.get("timestamp") and record.get("event") and isinstance(record.get("details"), dict)
        for record in records
    )
    error_reference_present = any(
        record.get("details", {}).get("error_reference") == error_reference for record in records
    )
    health_status = webapp.health_status()
    health_text = json.dumps(health_status, sort_keys=True)
    health_safe = (
        health_status.get("status") == "ok"
        and isinstance(health_status.get("generator_available"), bool)
        and "generator" not in health_status
        and not contains_sensitive_value(health_text)
        and "scripts/generate_quote.py" not in health_text
    )

    sensitive_values_omitted = not contains_sensitive_value(serialized_records)
    allowed_events_enforced = (
        ignored is False
        and client_logged
        and ai_logged
        and security_logged
        and observed_categories <= allowed_categories
    )
    status = "passed" if all(
        (
            records,
            all_records_have_minimum_metadata,
            error_reference_present,
            sensitive_values_omitted,
            allowed_events_enforced,
            health_safe,
        )
    ) else "failed"

    return {
        "schema": "swooshz.sqag.hosted-observability-verification.v1",
        "status": status,
        "synthetic_only": True,
        "policy": {
            "schema": policy.get("schema"),
            "allowed_event_categories_count": len(policy.get("allowed_event_categories", [])),
            "forbidden_content_count": len(policy.get("forbidden_content", [])),
            "retention_days": policy.get("retention", {}).get("internal_alpha_log_retention_days"),
        },
        "structured_logs": {
            "records_checked": len(records),
            "categories_checked": sorted(observed_categories),
            "allowed_events_enforced": bool(allowed_events_enforced),
            "minimum_metadata_present": bool(all_records_have_minimum_metadata),
            "sensitive_values_omitted": bool(sensitive_values_omitted),
        },
        "support_traceability": {
            "error_reference_present": bool(error_reference_present),
            "generic_error_reference_shape": bool(webapp.generic_referenced_errors(error_reference)),
        },
        "health_readiness": {
            "safe_metadata_only": bool(health_safe),
            "generator_available_boolean": isinstance(health_status.get("generator_available"), bool),
        },
        "privacy": {
            "output": "metadata-only",
            "paths": "omitted",
            "database_urls": "omitted",
            "artifact_bytes": "omitted",
            "payloads": "omitted",
            "provider_responses": "omitted",
            "staff_emails": "omitted",
            "tokens": "omitted",
        },
        "internal_alpha_ready": False,
        "production_ready": False,
        "notes": [
            "This verifies synthetic structured logging, health metadata, and support traceability only.",
            "It does not configure an external logging vendor, hosted smoke checks, object storage, or production readiness.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify synthetic hosted observability readiness evidence.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Synthetic verifier workspace. The path is never printed.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_verification(work_dir=args.work_dir)
    except Exception:
        report = {
            "schema": "swooshz.sqag.hosted-observability-verification.v1",
            "status": "failed",
            "synthetic_only": True,
            "privacy": {
                "output": "metadata-only",
                "paths": "omitted",
                "database_urls": "omitted",
                "artifact_bytes": "omitted",
                "payloads": "omitted",
                "provider_responses": "omitted",
                "staff_emails": "omitted",
                "tokens": "omitted",
            },
            "notes": [
                "Synthetic hosted observability verification failed before producing evidence.",
                "Failure details are omitted to avoid printing private paths or payloads.",
            ],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
