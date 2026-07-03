#!/usr/bin/env python3
"""Report KQAG production-readiness posture without printing private values."""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp
import verify_database_backup_restore
import verify_hosted_observability
import verify_hosted_smoke
import verify_object_storage_contract


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report KQAG production-readiness posture without printing private values.")
    parser.add_argument(
        "--with-backup-restore-evidence",
        action="store_true",
        help="Run the synthetic SQLite database/database-artifact backup/restore verifier and include only its pass/fail status.",
    )
    parser.add_argument(
        "--backup-restore-work-dir",
        type=Path,
        default=None,
        help="Synthetic verifier workspace. The path is never printed.",
    )
    parser.add_argument(
        "--with-hosted-observability-evidence",
        action="store_true",
        help="Run the synthetic hosted observability verifier and include only its pass/fail status.",
    )
    parser.add_argument(
        "--hosted-observability-work-dir",
        type=Path,
        default=None,
        help="Synthetic hosted observability verifier workspace. The path is never printed.",
    )
    parser.add_argument(
        "--with-hosted-smoke-evidence",
        action="store_true",
        help="Run the synthetic hosted smoke verifier and include only its pass/fail status.",
    )
    parser.add_argument(
        "--hosted-smoke-work-dir",
        type=Path,
        default=None,
        help="Synthetic hosted smoke verifier workspace. The path is never printed.",
    )
    parser.add_argument(
        "--with-object-storage-evidence",
        action="store_true",
        help="Run the synthetic object-storage contract verifier and include only its pass/fail status.",
    )
    parser.add_argument(
        "--object-storage-work-dir",
        type=Path,
        default=None,
        help="Synthetic object-storage contract verifier workspace. The path is never printed.",
    )
    return parser


def backup_restore_evidence_status(*, enabled: bool, work_dir: Path | None) -> str:
    if not enabled:
        return "not_run_by_checker"
    try:
        report = verify_database_backup_restore.run_verification(work_dir=work_dir)
    except Exception:
        return "failed"
    return "passed" if report.get("status") == "passed" else "failed"


def hosted_observability_evidence_status(*, enabled: bool, work_dir: Path | None) -> str:
    if not enabled:
        return "not_run_by_checker"
    try:
        report = verify_hosted_observability.run_verification(work_dir=work_dir)
    except Exception:
        return "failed"
    return "passed" if report.get("status") == "passed" else "failed"


def hosted_smoke_evidence_status(*, enabled: bool, work_dir: Path | None) -> str:
    if not enabled:
        return "not_run_by_checker"
    try:
        report = verify_hosted_smoke.run_verification(work_dir=work_dir)
    except Exception:
        return "failed"
    return "passed" if report.get("status") == "passed" else "failed"


def object_storage_evidence_status(*, enabled: bool, work_dir: Path | None) -> str:
    if not enabled:
        return "not_run_by_checker"
    try:
        report = verify_object_storage_contract.run_verification(work_dir=work_dir)
    except Exception:
        return "failed"
    return "passed" if report.get("status") == "passed" else "failed"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    status = webapp.production_readiness_status(
        backup_restore_evidence_status=backup_restore_evidence_status(
            enabled=args.with_backup_restore_evidence,
            work_dir=args.backup_restore_work_dir,
        ),
        hosted_observability_evidence_status=hosted_observability_evidence_status(
            enabled=args.with_hosted_observability_evidence,
            work_dir=args.hosted_observability_work_dir,
        ),
        hosted_smoke_evidence_status=hosted_smoke_evidence_status(
            enabled=args.with_hosted_smoke_evidence,
            work_dir=args.hosted_smoke_work_dir,
        ),
        object_storage_evidence_status=object_storage_evidence_status(
            enabled=args.with_object_storage_evidence,
            work_dir=args.object_storage_work_dir,
        ),
    )
    print(json.dumps(status, indent=2, ensure_ascii=True))
    return 0 if status["production_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
