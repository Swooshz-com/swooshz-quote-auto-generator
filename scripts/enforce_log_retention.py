#!/usr/bin/env python3
"""Delete expired privacy-minimized SQAG log files with an explicit apply gate."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webapp import server as webapp


RETENTION_DAYS = {"production": 90, "local_uat": 30}
ALLOWED_SUFFIXES = {".jsonl", ".md"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the exact SQAG routine-log retention window.")
    parser.add_argument("--mode", choices=sorted(RETENTION_DAYS), required=True)
    parser.add_argument("--log-root", type=Path)
    parser.add_argument("--expected-log-root", type=Path, help="Required exact confirmation when --log-root overrides the configured root.")
    parser.add_argument("--legal-hold-manifest", type=Path, help="Optional JSON object with a held_relative_paths string list.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--now", help="Optional timezone-aware ISO timestamp for deterministic testing.")
    return parser.parse_args()


def load_holds(path: Path | None) -> set[str]:
    if path is None:
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("held_relative_paths") if isinstance(payload, dict) else None
    if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
        raise ValueError("Legal-hold manifest must contain held_relative_paths strings.")
    return {Path(item).as_posix() for item in values if item and not Path(item).is_absolute() and ".." not in Path(item).parts}


def main() -> int:
    args = parse_args()
    retention_days = RETENTION_DAYS[args.mode]
    if not args.apply:
        print(json.dumps({"status": "blocked", "reason": "apply_confirmation_required", "retention_days": retention_days, "production_ready": False}, sort_keys=True))
        return 2
    now = dt.datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("--now must include a timezone.")
    cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(days=retention_days)
    configured_root = webapp.configured_log_root().resolve()
    root = (args.log_root or configured_root).resolve()
    if args.log_root is not None:
        expected_root = args.expected_log_root.resolve() if args.expected_log_root else None
        if expected_root != root:
            print(json.dumps({
                "status": "blocked",
                "reason": "custom_log_root_confirmation_required",
                "retention_days": retention_days,
                "production_ready": False,
            }, sort_keys=True))
            return 2
    root.mkdir(parents=True, exist_ok=True)
    holds = load_holds(args.legal_hold_manifest)
    examined = deleted = held = skipped = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        examined += 1
        if path.is_symlink():
            skipped += 1
            continue
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            skipped += 1
            continue
        if relative in holds:
            held += 1
            continue
        modified = dt.datetime.fromtimestamp(resolved.stat().st_mtime, tz=dt.timezone.utc)
        if modified < cutoff:
            resolved.unlink()
            deleted += 1
    print(json.dumps({
        "schema": "swooshz.sqag.log-retention-worker.v1",
        "status": "completed",
        "mode": args.mode,
        "retention_days": retention_days,
        "examined": examined,
        "deleted": deleted,
        "held": held,
        "skipped": skipped,
        "production_ready": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
