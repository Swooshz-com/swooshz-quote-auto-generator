#!/usr/bin/env python3
"""Inspect or resume bounded durable object-artifact cleanup journal work."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


OWNER_TYPES = (
    "profile",
    "pricing_reference",
    "uploaded_reference",
    "generated_quote",
    "generated_quote_version",
)
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


class _PrivacySafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("Reconciliation arguments are invalid.")


def build_parser() -> argparse.ArgumentParser:
    parser = _PrivacySafeArgumentParser(
        description="Inspect or resume bounded SQAG object-artifact cleanup work."
    )
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--owner-type", required=True, choices=OWNER_TYPES)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--apply-cleanup",
        action="store_true",
        help="Resume eligible published cleanup targets for this owner.",
    )
    return parser


def _valid_identifier(value: str) -> bool:
    return isinstance(value, str) and bool(IDENTIFIER_RE.fullmatch(value))


def run(args: argparse.Namespace) -> dict[str, object]:
    if (
        not _valid_identifier(args.workspace_id)
        or not _valid_identifier(args.owner_id)
        or not isinstance(args.limit, int)
        or isinstance(args.limit, bool)
        or not 1 <= args.limit <= 100
    ):
        raise ValueError("Reconciliation arguments are invalid.")
    database_url = webapp.configured_maintenance_database_url()
    if not database_url:
        raise RuntimeError("Maintenance database storage is unavailable.")
    if not webapp.postgres_database_url_is_supported(database_url):
        raise RuntimeError("Maintenance database storage is unavailable.")
    storage = webapp.DatabaseSqagStorage(
        database_url,
        args.workspace_id,
        role="maintenance",
        user_id="artifact-lifecycle-reconciler",
        expected_session_role=webapp.SQAG_MAINTENANCE_DATABASE_ROLE,
    )
    counts = storage.reconcile_object_artifact_lifecycle(
        args.owner_type,
        args.owner_id,
        limit=args.limit,
        apply_cleanup=args.apply_cleanup,
    )
    return {
        "schema": "swooshz.sqag.object-artifact-reconciliation.v1",
        "status": "inspected",
        "cleanup_requested": bool(args.apply_cleanup),
        "counts": counts,
        "privacy": {
            "identifiers": "omitted",
            "database_url": "omitted",
            "object_keys": "omitted",
            "credentials": "omitted",
            "payloads": "omitted",
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    supplied_args = list(argv if argv is not None else sys.argv[1:])
    cleanup_requested = "--apply-cleanup" in supplied_args
    try:
        args = build_parser().parse_args(supplied_args)
        report = run(args)
    except Exception:
        report = {
            "schema": "swooshz.sqag.object-artifact-reconciliation.v1",
            "status": "unavailable",
            "cleanup_requested": cleanup_requested,
            "privacy": {
                "identifiers": "omitted",
                "database_url": "omitted",
                "object_keys": "omitted",
                "credentials": "omitted",
                "payloads": "omitted",
            },
        }
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if report["status"] == "inspected" else 1


if __name__ == "__main__":
    raise SystemExit(main())