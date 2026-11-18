#!/usr/bin/env python3
"""Check or clear the rehearsal-only object storage bucket.

Run inside the rehearsal app container, where the storage settings are already
in the environment, so no credential is typed or passed on the command line:

    python scripts/clear_rehearsal_bucket.py --expect-bucket sqag-rehearsal --mode check
    python scripts/clear_rehearsal_bucket.py --expect-bucket sqag-rehearsal --mode clear

It refuses unless the configured bucket equals --expect-bucket, the bucket
name says "rehearsal", it matches the rehearsal map, and the container's
workspace id is the map's rehearsal workspace. So it cannot empty live's
bucket from live's container. Output is counts only: no keys, endpoint or
credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / "deploy" / "rehearsal" / "resources.json"
ENV_NAMES = (
    "SQAG_OBJECT_STORAGE_ENDPOINT_URL",
    "SQAG_OBJECT_STORAGE_BUCKET",
    "SQAG_OBJECT_STORAGE_REGION",
    "SQAG_OBJECT_STORAGE_ACCESS_KEY_ID",
    "SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY",
)
MAX_PAGES = 1000


class Refusal(Exception):
    pass


def guard(env: Mapping[str, str], expect_bucket: str, map_path: Path) -> None:
    missing = [name for name in ENV_NAMES if not str(env.get(name) or "").strip()]
    if missing:
        raise Refusal("storage settings missing: " + ", ".join(missing))
    try:
        rehearsal_map = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise Refusal("rehearsal map unreadable") from None
    mapped_bucket = str((rehearsal_map.get("storage") or {}).get("bucket") or "")
    bucket = str(env.get("SQAG_OBJECT_STORAGE_BUCKET") or "").strip()
    if not expect_bucket or bucket != expect_bucket:
        raise Refusal("configured bucket does not equal --expect-bucket")
    if "rehearsal" not in bucket:
        raise Refusal("configured bucket name does not say rehearsal")
    if bucket != mapped_bucket:
        raise Refusal("configured bucket is not the rehearsal map's bucket")
    workspace = str(env.get("SQAG_INTERNAL_WORKSPACE_ID") or "").strip()
    if not workspace or workspace != rehearsal_map.get("workspace_id"):
        raise Refusal("this is not the rehearsal app (workspace id differs from the map)")
    if not str(env.get("SQAG_OBJECT_STORAGE_ENDPOINT_URL") or "").startswith("https://"):
        raise Refusal("storage endpoint must be https")


def make_client(env: Mapping[str, str]) -> Any:
    import boto3  # imported here so tests can run without network or credentials

    return boto3.client(
        "s3",
        endpoint_url=env["SQAG_OBJECT_STORAGE_ENDPOINT_URL"],
        region_name=env["SQAG_OBJECT_STORAGE_REGION"],
        aws_access_key_id=env["SQAG_OBJECT_STORAGE_ACCESS_KEY_ID"],
        aws_secret_access_key=env["SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY"],
    )


def list_keys(client: Any, bucket: str) -> list[str]:
    keys: list[str] = []
    token = None
    for _ in range(MAX_PAGES):
        kwargs = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        keys.extend(item["Key"] for item in page.get("Contents") or [])
        if not page.get("IsTruncated"):
            return keys
        token = page.get("NextContinuationToken")
    raise Refusal("bucket listing did not finish within the page limit")


def clear(client: Any, bucket: str) -> tuple[int, int]:
    keys = list_keys(client, bucket)
    deleted = 0
    errors = 0
    for start in range(0, len(keys), 1000):
        batch = keys[start : start + 1000]
        result = client.delete_objects(
            Bucket=bucket,
            Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
        )
        failed = len(result.get("Errors") or [])
        errors += failed
        deleted += len(batch) - failed
    return deleted, errors


def run(argv: list[str] | None, env: Mapping[str, str], client_factory=make_client) -> tuple[int, dict[str, Any]]:
    parser = argparse.ArgumentParser(description="Check or clear the rehearsal-only bucket.")
    parser.add_argument("--expect-bucket", required=True)
    parser.add_argument("--mode", choices=("check", "clear"), required=True)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    args = parser.parse_args(argv)
    report: dict[str, Any] = {"mode": args.mode, "status": "fail"}
    try:
        guard(env, args.expect_bucket, args.map)
        client = client_factory(env)
        bucket = env["SQAG_OBJECT_STORAGE_BUCKET"].strip()
        if args.mode == "check":
            count = len(list_keys(client, bucket))
            report.update(object_count=count, status="empty" if count == 0 else "not_empty")
            return (0 if count == 0 else 3), report
        deleted, errors = clear(client, bucket)
        remaining = len(list_keys(client, bucket))
        report.update(deleted=deleted, delete_errors=errors, remaining=remaining)
        report["status"] = "cleared" if remaining == 0 and errors == 0 else "fail"
        return (0 if report["status"] == "cleared" else 1), report
    except Refusal as exc:
        report["error"] = str(exc)
        return 2, report
    except Exception as exc:  # provider errors: name only, never the message (may carry endpoint details)
        report["error"] = f"storage error: {type(exc).__name__}"
        return 1, report


def main(argv: list[str] | None = None) -> int:
    code, report = run(argv, os.environ)
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
