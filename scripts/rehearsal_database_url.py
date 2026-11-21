#!/usr/bin/env python3
"""Use a rehearsal database URL only when it is the branch the production-branch guard passed.

The guard (scripts/neon_branch_guard.py) checks a branch's URL once, before anything writes
to it. This script is the only way the rehearsal steps then use a database URL, so the app
and the migrations reach exactly the guarded branch, never live's database:

set-service   Writes SQAG_DATABASE_URL of one rehearsal Coolify service (a coolify_service in
              deploy/rehearsal/resources.json: quote-rehearsal or quote-rehearsal-copy)
              through Coolify's API, from an environment variable. It reads the service's
              name only, never its environment values, and prints no response body. Its
              record (schema sqag-db-url-set/v1) is the --db-guard file that every check
              in scripts/rehearsal-check.mjs on that service needs.
run-migrator  Runs one migration step on the branch: check (the guard only, before the role
              password reset), pre-apply, apply or post-apply. It guards
              SQAG_MIGRATOR_DATABASE_URL and, when set, SQAG_DATABASE_URL and
              SQAG_MAINTENANCE_DATABASE_URL (post-apply needs both) before the step runs.

Both commands need the guard's passing result for the branch (--guard-result, at most 12
hours old) and run the guard again on every URL they use: a URL on live's endpoint, on
another endpoint than the guarded one, or that the guard cannot place is refused before
any call. URLs are never printed, logged or stored; records hold hosts, ids and statuses.

    node <toolkit>/repo/scripts/toolkit-env.cjs run --names NEON_API_KEY,COOLIFY_SWOOSHZ_API_URL,COOLIFY_SWOOSHZ_API_TOKEN -- \\
      python scripts/rehearsal_database_url.py set-service --guard-result <db-guard.json> \\
        --database-url-env <NAME> --service-uuid <uuid> --result <db-url-set.json>
    python scripts/rehearsal_database_url.py run-migrator --guard-result <db-guard.json> \\
        --step check|pre-apply|apply|post-apply --result <migrator-step.json>

Exit codes: 0 pass; 2 refused before any call; 1 Coolify or unexpected error; a failed
migration step exits with the step's own code. The record is written in every case.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import live_rollback  # noqa: E402 (needs the path setup above)
from scripts import neon_branch_guard as guard  # noqa: E402

SET_SCHEMA = "sqag-db-url-set/v1"
MIGRATOR_SCHEMA = "sqag-db-migrator-step/v1"
GUARD_MAX_AGE = timedelta(hours=12)
SERVICE_KEY = "SQAG_DATABASE_URL"
SERVICE_UUID_RE = re.compile(r"[A-Za-z0-9]{8,64}")
MIGRATOR_URL = "SQAG_MIGRATOR_DATABASE_URL"
RUNTIME_URL = "SQAG_DATABASE_URL"
MAINTENANCE_URL = "SQAG_MAINTENANCE_DATABASE_URL"
PREFLIGHT = ROOT / "scripts" / "preflight_sqag_migrations.py"
MIGRATE = ROOT / "scripts" / "migrate_sqag_storage.py"
STEPS: dict[str, list[str]] = {
    "check": [],
    "pre-apply": [str(PREFLIGHT), "--phase", "pre-apply"],
    "apply": [str(MIGRATE)],
    "post-apply": [str(PREFLIGHT), "--phase", "post-apply"],
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_guard_result(path: Path, now: datetime) -> dict[str, Any]:
    """The guard's passing result for the branch, at most 12 hours old."""
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise guard.GuardRefusal("--guard-result is unreadable") from None
    if not isinstance(result, dict) or result.get("schema") != guard.RESULT_SCHEMA:
        raise guard.GuardRefusal("--guard-result is not a production-branch guard result")
    if result.get("status") != "pass":
        raise guard.GuardRefusal("the production-branch guard did not pass for this branch")
    endpoint_id = result.get("endpoint_id")
    if not isinstance(endpoint_id, str) or guard.ENDPOINT_ID_RE.fullmatch(endpoint_id) is None:
        raise guard.GuardRefusal("--guard-result names no endpoint")
    try:
        checked = datetime.strptime(str(result.get("checked_at")), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise guard.GuardRefusal("--guard-result is undated") from None
    if checked > now + timedelta(minutes=1) or now - checked > GUARD_MAX_AGE:
        raise guard.GuardRefusal("--guard-result is older than 12 hours or dated in the future: run the guard again")
    return result


def guarded_url(env: Mapping[str, str], name: str, guarded: Mapping[str, Any], rehearsal_map: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """(URL, this guard run's result) for the variable `name`, refused unless it is the guarded branch."""
    if not guard.ENV_NAME_RE.fullmatch(name):
        raise guard.GuardRefusal("the URL variable must be an environment variable name (letters, digits, underscore)")
    value = str(env.get(name) or "").strip()
    if not value:
        raise guard.GuardRefusal(f"{name} is unset or empty")
    result = guard.check_database_target(value, rehearsal_map, guard.neon_client_from_env(env))
    if result["endpoint_id"] != guarded["endpoint_id"]:
        raise guard.GuardRefusal(f"{name} is not on the guarded branch's endpoint")
    return value, result


def rehearsal_service_names(rehearsal_map: Mapping[str, Any]) -> set[str]:
    resources = rehearsal_map.get("resources")
    return {
        str(item.get("name"))
        for item in resources if isinstance(item, Mapping) and item.get("kind") == "coolify_service"
    } if isinstance(resources, list) else set()


def set_service(args: argparse.Namespace, env: Mapping[str, str], coolify_factory: Callable, record: dict) -> int:
    rehearsal_map = guard.load_map(args.map)
    allowed = rehearsal_service_names(rehearsal_map)
    if not allowed:
        raise guard.GuardRefusal("the rehearsal map lists no coolify_service")
    if SERVICE_UUID_RE.fullmatch(args.service_uuid) is None:
        raise guard.GuardRefusal("--service-uuid must be a Coolify uuid (letters and digits)")
    guarded = load_guard_result(args.guard_result, _now())
    record["guard_checked_at"] = guarded["checked_at"]
    value, checked = guarded_url(env, args.database_url_env, guarded, rehearsal_map)
    record.update(host=checked["host"], endpoint_id=checked["endpoint_id"], branch_id=checked["branch_id"],
                  branch_name=checked["branch_name"], neon_api=checked["neon_api"], checked_at=checked["checked_at"])
    coolify = coolify_factory(env)
    name = coolify.call("GET", f"/services/{args.service_uuid}")
    name = str(name.get("name") or "") if isinstance(name, dict) else ""
    if name not in allowed:
        raise guard.GuardRefusal("the Coolify service is not a rehearsal service in the map")
    record["service_name"] = name
    body = {"key": SERVICE_KEY, "value": value}
    try:
        coolify.call("PATCH", f"/services/{args.service_uuid}/envs", body)
    except live_rollback.CoolifyHttpError as exc:
        if exc.status != 404:
            raise
        coolify.call("POST", f"/services/{args.service_uuid}/envs", body)
    record.update(status="pass", reason=None, set_at=_stamp(_now()))
    print(f"set {SERVICE_KEY} on {name}: host {checked['host']}", file=sys.stderr)
    return 0


def run_migrator(args: argparse.Namespace, env: Mapping[str, str], runner: Callable, record: dict) -> int:
    rehearsal_map = guard.load_map(args.map)
    guarded = load_guard_result(args.guard_result, _now())
    record["guard_checked_at"] = guarded["checked_at"]
    names = [MIGRATOR_URL] + [name for name in (RUNTIME_URL, MAINTENANCE_URL) if args.step == "post-apply" or name in env]
    for name in names:
        guarded_url(env, name, guarded, rehearsal_map)
    record.update(endpoint_id=guarded["endpoint_id"], checked_variables=sorted(names))
    command = STEPS[args.step]
    code = runner([sys.executable, *command], dict(env)) if command else 0
    record.update(exit_code=code, status="pass" if code == 0 else "step_failed", reason=None if code == 0 else "the migration step failed")
    return code


def _run_step(command: list[str], env: dict[str, str]) -> int:
    return subprocess.run(command, env=env, cwd=ROOT, check=False).returncode


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Use a rehearsal database URL only when the guard passed its branch.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("set-service", "run-migrator"):
        sub = commands.add_parser(name)
        sub.add_argument("--guard-result", type=Path, required=True)
        sub.add_argument("--result", type=Path, required=True)
        sub.add_argument("--map", type=Path, default=guard.DEFAULT_MAP)
        if name == "set-service":
            sub.add_argument("--database-url-env", required=True)
            sub.add_argument("--service-uuid", required=True)
        else:
            sub.add_argument("--step", choices=sorted(STEPS), required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None,
         coolify_factory: Callable = live_rollback.Coolify, runner: Callable = _run_step) -> int:
    args = parse_args(argv)
    env = os.environ if env is None else env
    record: dict[str, Any] = {"schema": SET_SCHEMA if args.command == "set-service" else MIGRATOR_SCHEMA,
                              "status": "refused", "reason": None}
    if args.command == "set-service":
        record.update(service_uuid=args.service_uuid, key=SERVICE_KEY)
    else:
        record["step"] = args.step
    try:
        if args.command == "set-service":
            code = set_service(args, env, coolify_factory, record)
        else:
            code = run_migrator(args, env, runner, record)
    except guard.GuardRefusal as exc:
        record.update(status="refused", reason=str(exc))
        print(f"refused: {exc}", file=sys.stderr)
        code = 2
    except live_rollback.Refusal as exc:
        record.update(status="refused", reason=str(exc))
        print(f"refused: {exc}", file=sys.stderr)
        code = 2
    except live_rollback.CoolifyHttpError as exc:
        record.update(status="error", reason=str(exc))
        print(str(exc), file=sys.stderr)
        code = 1
    except Exception as exc:  # any other failure: type name only, never the message
        record.update(status="error", reason=f"error: {type(exc).__name__}")
        print(record["reason"], file=sys.stderr)
        code = 1
    try:
        args.result.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        print("result file could not be written", file=sys.stderr)
        return code or 1
    return code


if __name__ == "__main__":
    sys.exit(main())
