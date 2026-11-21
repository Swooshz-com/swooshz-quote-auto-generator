#!/usr/bin/env python3
"""Copy the object files of a few live quotes into the rehearsal bucket (laptop only).

Run on the operator's laptop so the rehearsal app on the Neon data branch can reopen
those quotes. SQAG_LIVE_COPY_DATABASE_URL is the data branch's per-run URL, set in the
session only (never stored). The bucket tokens, and NEONDB_SWOOSHZ_SQAG_API_KEY for the
guard's branch check, come from the credential store through toolkit env run (names only):

    node "<toolkit>/repo/scripts/toolkit-env.cjs" run --names NEONDB_SWOOSHZ_SQAG_API_KEY,SWOOSHZ_SQAG_LIVE_R2_READONLY_ENDPOINT_URL,SWOOSHZ_SQAG_LIVE_R2_READONLY_ACCESS_KEY_ID,SWOOSHZ_SQAG_LIVE_R2_READONLY_SECRET_ACCESS_KEY,SWOOSHZ_SQAG_REHEARSAL_R2_ENDPOINT_URL,SWOOSHZ_SQAG_REHEARSAL_R2_ACCESS_KEY_ID,SWOOSHZ_SQAG_REHEARSAL_R2_SECRET_ACCESS_KEY -- python scripts/copy_live_objects_to_rehearsal.py --targets <targets.json> --source-bucket <live bucket> --expect-dest-bucket sqag-rehearsal --mode plan --confirm-live-read

--targets is JSON: {"schema": "sqag-live-walk-targets/v1", "quote_ids": ["quote-<24 hex>"]}.
Run --mode plan first (reads rows and source heads, writes nothing), then --mode copy;
copy is safe to repeat. Output is counts only. Exit: 0 ok, 1 mismatch or storage error,
2 refused before any call.

Before any database or bucket call, the run checks SQAG_LIVE_COPY_DATABASE_URL with
scripts/neon_branch_guard.py and refuses (exit 2) a host that is Neon's default (production)
branch, or a branch Neon's API did not confirm (the key unset: neon_api not_checked). The
guard's one line goes to stderr; stdout stays the JSON report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, NamedTuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import neon_branch_guard  # noqa: E402 (needs the path setup above)

DEFAULT_MAP = ROOT / "deploy" / "rehearsal" / "resources.json"
TARGETS_SCHEMA = "sqag-live-walk-targets/v1"
QUOTE_ID = re.compile(r"quote-[0-9a-f]{24}")
MAX_QUOTES = 50
MAX_OBJECTS = 2000
MAX_BYTES = 1024**3
DB_URL_ENV = "SQAG_LIVE_COPY_DATABASE_URL"
SRC_ENDPOINT = "SWOOSHZ_SQAG_LIVE_R2_READONLY_ENDPOINT_URL"
SRC_KEY_ID = "SWOOSHZ_SQAG_LIVE_R2_READONLY_ACCESS_KEY_ID"
SRC_SECRET = "SWOOSHZ_SQAG_LIVE_R2_READONLY_SECRET_ACCESS_KEY"
DST_ENDPOINT = "SWOOSHZ_SQAG_REHEARSAL_R2_ENDPOINT_URL"
DST_KEY_ID = "SWOOSHZ_SQAG_REHEARSAL_R2_ACCESS_KEY_ID"
DST_SECRET = "SWOOSHZ_SQAG_REHEARSAL_R2_SECRET_ACCESS_KEY"
SOURCE_NAMES = (SRC_ENDPOINT, SRC_KEY_ID, SRC_SECRET)
DEST_NAMES = (DST_ENDPOINT, DST_KEY_ID, DST_SECRET)
REQUIRED_ENV = (DB_URL_ENV, *SOURCE_NAMES, *DEST_NAMES)
READ_METHODS = frozenset({"head_object", "get_object"})
NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
ROWS_SQL = (
    "select workspace_id, owner_type, session_id, artifact_kind, size_bytes, checksum_sha256, object_key_ref "
    "from sqag_object_artifacts where session_id = any(%s) "
    "and status = 'active' and object_provider_type = 's3_compatible'"
)
# Same retry policy as webapp.object_storage.S3_CLIENT_RETRIES (botocore 1.44 changed
# the SDK default). Kept inline because this script runs from a laptop checkout; a
# test keeps the two equal.
S3_CLIENT_RETRIES: Mapping[str, str] = {"mode": "legacy"}
R2_REGION = "auto"


class Refusal(Exception):
    """Stops the run before any database or storage call. The message names no values."""


class ReadOnlySourceViolation(Exception):
    """A write method was called on the live source client. It never reached the client."""


class StoredObject(NamedTuple):
    key: str
    size: int
    checksum: str


def load_targets(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refusal("targets file is unreadable or not JSON") from None
    ids = data.get("quote_ids") if isinstance(data, dict) and data.get("schema") == TARGETS_SCHEMA else None
    if not isinstance(ids, list):
        raise Refusal("targets file needs schema sqag-live-walk-targets/v1 and a quote_ids list")
    if not 1 <= len(ids) <= MAX_QUOTES:
        raise Refusal("quote_ids must hold 1 to 50 ids")
    if not all(isinstance(item, str) and QUOTE_ID.fullmatch(item) for item in ids):
        raise Refusal("a quote id does not match quote-[0-9a-f]{24}")
    if len(set(ids)) != len(ids):
        raise Refusal("quote_ids repeat an id")
    return ids


def check_env(env: Mapping[str, str]) -> None:
    missing = [name for name in REQUIRED_ENV if not str(env.get(name) or "").strip()]
    if missing:
        raise Refusal("settings missing: " + ", ".join(missing))
    if not env[DB_URL_ENV].strip().startswith(("postgresql://", "postgres://")):
        raise Refusal("database URL must start with postgresql:// or postgres://")
    if not all(env[name].strip().startswith("https://") for name in (SRC_ENDPOINT, DST_ENDPOINT)):
        raise Refusal("storage endpoints must be https")
    if env[SRC_KEY_ID].strip() == env[DST_KEY_ID].strip():
        raise Refusal("source and destination use the same access key id")


def check_buckets(source_bucket: str, dest_bucket: str, map_path: Path) -> None:
    if not source_bucket or "rehearsal" in source_bucket.lower():
        raise Refusal("source bucket is missing or its name says rehearsal")
    if source_bucket == dest_bucket:
        raise Refusal("source and destination bucket are the same")
    try:
        mapped = json.loads(map_path.read_text(encoding="utf-8"))["storage"]["bucket"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refusal("rehearsal map is unreadable") from None
    if dest_bucket != mapped or "rehearsal" not in dest_bucket:
        raise Refusal("destination bucket is not the rehearsal map's bucket")


def check_production_guard(env: Mapping[str, str], map_path: Path) -> None:
    """Refuse a database URL that names Neon's default (production) branch, or one Neon's API did
    not confirm. Runs before any call."""
    try:
        rehearsal_map = neon_branch_guard.load_map(map_path)
        result = neon_branch_guard.check_database_target(
            env[DB_URL_ENV].strip(), rehearsal_map, neon_branch_guard.neon_client_from_env(env)
        )
        neon_branch_guard.require_neon_checked(result)
    except neon_branch_guard.GuardRefusal as exc:
        raise Refusal(f"production database guard refused: {exc}") from None


def make_db(env: Mapping[str, str]) -> Any:
    import psycopg  # lazy: tests run without the driver or a database

    return psycopg.connect(env[DB_URL_ENV].strip(), autocommit=True)


def read_rows(conn: Any, quote_ids: list[str]) -> list[tuple]:
    # Session-level read-only guard; with autocommit, SET TRANSACTION would do nothing.
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return list(conn.execute(ROWS_SQL, (quote_ids,)).fetchall())


def plan_objects(rows: list[tuple]) -> list[StoredObject]:
    if not rows:
        raise Refusal("no active object rows for the listed quotes")
    if len({row[0] for row in rows}) != 1:
        raise Refusal("rows span more than one workspace")
    by_key: dict[str, StoredObject] = {}
    for _workspace, _owner_type, _session, _kind, size, checksum, key in rows:
        if not isinstance(key, str) or not key.startswith("workspaces/") or ".." in key:
            raise Refusal("an object key is outside workspaces/ or unsafe")
        if not isinstance(size, int) or size < 0 or not isinstance(checksum, str):
            raise Refusal("an object row has a bad size or checksum")
        found = StoredObject(key, size, checksum)
        if by_key.setdefault(key, found) != found:
            raise Refusal("two rows give one object key different content")
    objects = list(by_key.values())
    if len(objects) > MAX_OBJECTS:
        raise Refusal("more than 2000 objects")
    if sum(obj.size for obj in objects) > MAX_BYTES:
        raise Refusal("objects total more than 1 GiB")
    return objects


def keys_digest(objects: list[StoredObject]) -> str:
    return hashlib.sha256("\n".join(sorted(obj.key for obj in objects)).encode("utf-8")).hexdigest()


class ReadOnlySource:
    """Live source client. Only head_object and get_object reach it; any other call is refused and counted."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.write_attempts_refused = 0

    def __getattr__(self, name: str) -> Any:
        if name in READ_METHODS:
            return getattr(self._client, name)

        def refuse(*_args: Any, **_kwargs: Any) -> None:
            self.write_attempts_refused += 1
            raise ReadOnlySourceViolation(name)

        return refuse


def make_client(env: Mapping[str, str], names: tuple[str, str, str]) -> Any:
    import boto3  # lazy: tests need neither boto3 nor a network
    from botocore.config import Config

    endpoint, key_id, secret = (env[name].strip() for name in names)
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=R2_REGION,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(retries=dict(S3_CLIENT_RETRIES)),
    )


def make_source_client(env: Mapping[str, str]) -> Any:
    return make_client(env, SOURCE_NAMES)


def make_dest_client(env: Mapping[str, str]) -> Any:
    return make_client(env, DEST_NAMES)


def remote_metadata(response: Mapping[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in dict(response.get("Metadata") or {}).items()}


def is_not_found(exc: Exception) -> bool:
    error = (getattr(exc, "response", None) or {}).get("Error") or {}
    return str(error.get("Code", "")) in NOT_FOUND_CODES


def matches(head: Mapping[str, Any], obj: StoredObject, metadata: dict[str, str]) -> bool:
    # The app's artifact check reads the sqag-* object metadata on read, so it must survive the copy.
    return head.get("ContentLength") == obj.size and remote_metadata(head) == metadata


def check_source(source: ReadOnlySource, bucket: str, obj: StoredObject, report: dict[str, Any]) -> None:
    if source.head_object(Bucket=bucket, Key=obj.key).get("ContentLength") != obj.size:
        report["mismatches"] += 1
        report.setdefault("error", "source object size differs from its database row")


def copy_object(
    source: ReadOnlySource, dest: Any, src_bucket: str, dst_bucket: str, obj: StoredObject, report: dict[str, Any]
) -> None:
    got = source.get_object(Bucket=src_bucket, Key=obj.key)
    data = got["Body"].read()
    if len(data) != obj.size or hashlib.sha256(data).hexdigest() != obj.checksum:
        report["mismatches"] += 1
        report.setdefault("error", "source object does not match its database row")
        return
    metadata = remote_metadata(got)
    try:
        existing = dest.head_object(Bucket=dst_bucket, Key=obj.key)
    except Exception as exc:
        if not is_not_found(exc):
            raise
        existing = None
    if existing is not None:
        if matches(existing, obj, metadata):
            report["already_present"] += 1
        else:
            report["mismatches"] += 1  # never overwrite: a different object sits at this key
            report.setdefault("error", "destination holds a different object at a key")
        return
    content_type = got.get("ContentType") or "application/octet-stream"
    dest.put_object(Bucket=dst_bucket, Key=obj.key, Body=data, ContentType=content_type, Metadata=metadata)
    if matches(dest.head_object(Bucket=dst_bucket, Key=obj.key), obj, metadata):
        report["copied"] += 1
    else:
        report["errors"] += 1
        report.setdefault("error", "destination check failed after the copy")


def attempt(report: dict[str, Any], step: Any, *args: Any) -> None:
    """Run one object. A provider error is counted and named by its type only, never by its message."""
    try:
        step(*args, report)
    except Exception as exc:
        report["errors"] += 1
        report.setdefault("error", f"storage error: {type(exc).__name__}")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy live quote objects into the rehearsal bucket.")
    parser.add_argument("--targets", type=Path, required=True)
    parser.add_argument("--source-bucket", required=True)
    parser.add_argument("--expect-dest-bucket", required=True)
    parser.add_argument("--mode", choices=("plan", "copy"), required=True)
    parser.add_argument("--confirm-live-read", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    return parser.parse_args(argv)


def run(
    argv: list[str] | None,
    env: Mapping[str, str],
    *,
    db_factory: Any = None,
    source_factory: Any = None,
    dest_factory: Any = None,
) -> tuple[int, dict[str, Any]]:
    args = parse_args(argv)
    report: dict[str, Any] = {
        "mode": args.mode, "status": "fail", "objects": 0, "bytes": 0, "copied": 0, "already_present": 0,
        "mismatches": 0, "errors": 0, "write_attempts_refused": 0, "keys_sha256": "", "quote_ids": 0,
    }
    code = 1
    source: ReadOnlySource | None = None
    conn: Any = None
    try:
        quote_ids = load_targets(args.targets)
        report["quote_ids"] = len(quote_ids)
        if not args.confirm_live_read:
            raise Refusal("--confirm-live-read is required: this reads live's bucket")
        check_env(env)
        source_bucket = args.source_bucket.strip()
        dest_bucket = args.expect_dest_bucket.strip()
        check_buckets(source_bucket, dest_bucket, args.map)
        check_production_guard(env, args.map)
        conn = (db_factory or make_db)(env)
        objects = plan_objects(read_rows(conn, quote_ids))
        report.update(objects=len(objects), bytes=sum(o.size for o in objects), keys_sha256=keys_digest(objects))
        source = ReadOnlySource((source_factory or make_source_client)(env))
        if args.mode == "plan":
            for obj in objects:
                attempt(report, check_source, source, source_bucket, obj)
        else:
            dest = (dest_factory or make_dest_client)(env)
            for obj in objects:
                attempt(report, copy_object, source, dest, source_bucket, dest_bucket, obj)
        ok = report["mismatches"] == 0 and report["errors"] == 0
        report["status"] = ("planned" if args.mode == "plan" else "copied") if ok else "fail"
        code = 0 if ok else 1
    except Refusal as exc:
        report.update(status="refused", error=str(exc))
        code = 2
    except Exception as exc:  # any other failure: type name only, never the message
        report.update(status="fail", error=f"run error: {type(exc).__name__}")
    finally:
        if source is not None:
            report["write_attempts_refused"] = source.write_attempts_refused
        if conn is not None:
            conn.close()
    if args.report is not None:
        try:
            args.report.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        except OSError:
            report.update(status="fail", error="report file could not be written")
            code = 1
    return code, report


def main(argv: list[str] | None = None) -> int:
    code, report = run(argv, os.environ)
    print(json.dumps(report, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
