#!/usr/bin/env python3
"""Run metadata-only blocked hosted-validation evidence.

This verifier composes existing synthetic SQAG evidence checks and verifies
that database/BLOB artifact mode remains blocked for hosted, protected, deploy,
and production readiness. It does not deploy, contact a live host, read
committed secrets, or prove hosted or production readiness.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp
import verify_database_backup_restore
import verify_hosted_observability
import verify_hosted_smoke


REQUIRED_ENV_NAMES = [
    "APP_MODE",
    "AUTH_REQUIRED",
    "SQAG_AUTH_MODE",
    "SESSION_SECRET",
    "SQAG_TRACKING_HMAC_KEY",
    "SQAG_TRACKING_HMAC_KEY_VERSION",
    "SQAG_STORAGE_MODE",
    "SQAG_ARTIFACT_STORAGE_MODE",
    "SQAG_DATABASE_URL",
    "SQAG_PLATFORM_LAUNCH_MODE",
    "SQAG_PUBLIC_BASE_URL",
    "SQAG_INTERNAL_WORKSPACE_ID",
    "SQAG_INTERNAL_GOOGLE_IDENTITIES_JSON",
    "OIDC_ISSUER_URL",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OIDC_REDIRECT_URI",
    "OIDC_AUTHORIZE_URL",
    "OIDC_TOKEN_URL",
    'SQAG_TRUSTED_PROXY_CIDRS',
    "QUOTE_DATA_ROOT",
    "QUOTE_OUTPUT_ROOT",
    "QUOTE_TMP_ROOT",
    "QUOTE_LOG_ROOT",
    "PORT",
]

HOST_SECRET_MANAGER_ONLY_ENV_NAMES = [
    "SESSION_SECRET",
    "SQAG_TRACKING_HMAC_KEY",
    "SQAG_DATABASE_URL",
    "SQAG_INTERNAL_GOOGLE_IDENTITIES_JSON",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
]

PROHIBITED_OUTPUT_MARKERS = (
    "sqlite:///",
    "C:/Users/Private",
    "/var/lib/sqag",
    "/var/log/sqag",
    "Generated quote private line item",
    "Private pricing catalog contents",
    "Private profile layout contents",
    "Synthetic Private Customer",
    "staff.member@example.test",
    "oauth-client-secret-value",
    "swooshz_private_session_cookie",
    "sk-proj-private-api-key",
    "synthetic-private-artifact-bytes",
    "raw provider response text",
    "private-code",
    "private-state",
)


@contextlib.contextmanager
def temporary_env(values: dict[str, str]):
    original = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def evidence_status(report: dict[str, Any]) -> str:
    return "passed" if report.get("status") == "passed" else "failed"


def safe_failure_report(stage: str) -> dict[str, Any]:
    return {
        "schema": "swooshz.sqag.internal-alpha-hosted-validation.v1",
        "status": "failed",
        "failed_stage": stage,
        "synthetic_only": True,
        "live_deployment_evidence": False,
        "hosted_smoke_scope": {
            "evidence_scope": "synthetic_local_hosted_contract",
            "public_hosted_url_tested": False,
            "live_neon_r2_evidence": False,
            "platform_handoff_evidence": False,
        },
        "production_ready": False,
        "privacy": privacy_summary(),
        "notes": [
            "Hosted validation failed before producing complete blocked-readiness evidence.",
            "Failure details are omitted to avoid printing private paths, URLs, secrets, or payloads.",
        ],
    }


def privacy_summary() -> dict[str, str]:
    return {
        "output": "metadata-only",
        "paths": "omitted",
        "database_urls": "omitted",
        "hostnames": "omitted",
        "cookies": "omitted",
        "tokens": "omitted",
        "oauth_values": "omitted",
        "artifact_bytes": "omitted",
        "quote_contents": "omitted",
        "pricing_profile_payloads": "omitted",
        "object_keys": "omitted",
    }


def run_verification(*, work_dir: Path | None = None) -> dict[str, Any]:
    parent = work_dir or (ROOT / "_tmp" / "internal-alpha-hosted-validation")
    run_root = parent / f"run-{time.time_ns()}"
    backup_restore_report = verify_database_backup_restore.run_verification(work_dir=run_root / "backup-restore")
    observability_report = verify_hosted_observability.run_verification(work_dir=run_root / "hosted-observability")
    hosted_smoke_report = verify_hosted_smoke.run_verification(work_dir=run_root / "hosted-smoke")

    readiness_env = {
        "APP_MODE": "deploy",
        "AUTH_REQUIRED": "true",
        "SQAG_AUTH_MODE": "internal_google",
        "SESSION_SECRET": "synthetic-session-secret-with-enough-entropy",
        "SQAG_TRACKING_HMAC_KEY": "synthetic-tracking-key",
        "SQAG_TRACKING_HMAC_KEY_VERSION": "synthetic-v1",
        "SQAG_TRUSTED_PROXY_CIDRS": "127.0.0.1/32",
        "SQAG_PLATFORM_LAUNCH_MODE": "disabled",
        "SQAG_PUBLIC_BASE_URL": "https://quote.swooshz.com",
        "SQAG_INTERNAL_WORKSPACE_ID": "workspace-synthetic-alpha",
        "SQAG_INTERNAL_GOOGLE_IDENTITIES_JSON": json.dumps(
            [
                {
                    "sub": "synthetic-hosted-subject",
                    "email": "synthetic.tester@example.test",
                    "role": "admin",
                }
            ],
            separators=(",", ":"),
        ),
        "OIDC_ISSUER_URL": "https://accounts.google.com",
        "OIDC_CLIENT_ID": "synthetic-client-id",
        "OIDC_CLIENT_SECRET": "synthetic-client-secret",
        "OIDC_REDIRECT_URI": "https://quote.swooshz.com/callback",
        "OIDC_AUTHORIZE_URL": "https://accounts.google.com/o/oauth2/v2/auth",
        "OIDC_TOKEN_URL": "https://oauth2.googleapis.com/token",
        "SQAG_STORAGE_MODE": "database",
        "SQAG_ARTIFACT_STORAGE_MODE": "database",
        "SQAG_DATABASE_URL": f"sqlite:///{(run_root / 'readiness.sqlite3').as_posix()}",
    }
    with temporary_env(readiness_env):
        readiness = webapp.production_readiness_status(
            backup_restore_evidence_status=evidence_status(backup_restore_report),
            hosted_observability_evidence_status=evidence_status(observability_report),
            hosted_smoke_evidence_status=evidence_status(hosted_smoke_report),
        )

    evidence = {
        "backup_restore": evidence_status(backup_restore_report),
        "hosted_observability": evidence_status(observability_report),
        "hosted_smoke": evidence_status(hosted_smoke_report),
    }
    blocker_ids = [item.get("id", "") for item in readiness.get("blockers", []) if item.get("id")]
    production_blocker_ids = [item.get("id", "") for item in readiness.get("production_blockers", []) if item.get("id")]
    blocked_database_blob_posture = "database_blob_artifact_storage_not_launch_ready" in blocker_ids
    status = "passed" if (
        all(value == "passed" for value in evidence.values())
        and blocked_database_blob_posture
        and not readiness.get("internal_alpha_ready")
        and not readiness.get("production_ready")
    ) else "failed"

    report = {
        "schema": "swooshz.sqag.internal-alpha-hosted-validation.v1",
        "status": status,
        "synthetic_only": True,
        "live_deployment_evidence": False,
        "hosted_smoke_scope": {
            "evidence_scope": "synthetic_local_hosted_contract",
            "public_hosted_url_tested": False,
            "live_neon_r2_evidence": False,
            "platform_handoff_evidence": False,
        },
        "target_posture": {
            "environment": "blocked-deploy-database-blob-artifact-validation",
            "app_mode": "deploy",
            "storage_mode": "database",
            "artifact_storage_mode": "database",
            "database_url_source": "host_secret_manager_only",
            "fixed_internal_workspace_required": True,
            "platform_handoff_enabled": False,
            "launch_ready": False,
            "object_mode_final_production_storage": False,
        },
        "required_env_names": list(REQUIRED_ENV_NAMES),
        "host_secret_manager_only_env_names": list(HOST_SECRET_MANAGER_ONLY_ENV_NAMES),
        "health": {
            "path": "/api/health",
            "expected_status": 200,
            "metadata_only": True,
        },
        "evidence": evidence,
        "readiness": {
            "internal_alpha_ready": bool(readiness.get("internal_alpha_ready")),
            "production_ready": bool(readiness.get("production_ready")),
            "blocker_ids": blocker_ids,
            "production_blocker_ids": production_blocker_ids,
        },
        "validation_commands": [
            "python scripts/verify_database_backup_restore.py --work-dir <synthetic-work-dir>",
            "python scripts/verify_hosted_observability.py --work-dir <synthetic-work-dir>",
            "python scripts/verify_hosted_smoke.py --work-dir <synthetic-work-dir>",
            "python scripts/check_production_readiness.py --with-backup-restore-evidence --with-hosted-observability-evidence --with-hosted-smoke-evidence",
        ],
        "proves": [
            "synthetic SQLite database/database-artifact backup, restore, rollback, and retention-policy evidence ran",
            "synthetic privacy-minimized hosted observability schema and health metadata evidence",
            "synthetic/local deploy/database/database-artifact hosted-contract smoke evidence on 127.0.0.1",
            "readiness checker blocks DB/BLOB artifact mode from launch, hosted, protected, deploy, and production readiness",
        ],
        "does_not_prove": [
            "live VPS, Coolify, DNS, TLS, firewall, reverse proxy, or host health evidence",
            "real OIDC provider login/logout or live Swooshz Platform integration",
            "real object-storage provider, DB+object backup/restore, or live retention/delete evidence",
            "a public hosted URL test, live Neon/R2 evidence, or Platform handoff evidence",
            "hosted launch readiness, production deployment operations, alert delivery, supply-chain hardening, or production readiness",
        ],
        "privacy": privacy_summary(),
        "notes": [
            "Use this as metadata-only local/synthetic evidence that the old DB/BLOB launch posture remains blocked.",
            "Do not paste environment values, DB URLs, hostnames, tokens, cookies, generated quotes, or private tenant data into reports.",
        ],
    }
    text = json.dumps(report, sort_keys=True)
    if any(marker in text for marker in PROHIBITED_OUTPUT_MARKERS):
        return safe_failure_report("privacy_guard")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify metadata-only SQAG blocked synthetic/local hosted-contract evidence.")
    parser.add_argument("--work-dir", type=Path, default=None, help="Synthetic verifier workspace. The path is never printed.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_verification(work_dir=args.work_dir)
    except Exception:
        report = safe_failure_report("exception")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
