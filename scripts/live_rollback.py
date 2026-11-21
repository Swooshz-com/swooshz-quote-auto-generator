#!/usr/bin/env python3
"""Roll live back to the recorded previous image when the live-data walk (Pass B) fails.

Step 8 of docs/internal-uat-coolify-deploy.md runs this right after Pass B,
on the laptop, from a clean checkout of the deployment/internal branch:

    node <toolkit>/repo/scripts/toolkit-env.cjs run --names COOLIFY_SWOOSHZ_API_URL,COOLIFY_SWOOSHZ_API_TOKEN -- \
      python scripts/live_rollback.py --pass-b-result <live-walk.json> \
        --live-url https://<live host> --switched-at <UTC time the live deploy finished> \
        --candidate-image sha256:<id> --candidate-source <40-char commit> \
        --previous-image sha256:<id> --previous-source <40-char commit> \
        --deploy-checkout <deployment/internal checkout> --live-app-uuid <uuid> \
        --record <rollback.json> --execute

Only an explicit pass of the full walk keeps the candidate; a missing,
unreadable or partial result rolls back, and so does a result that did not
walk live's host (--live-url, compared by hash), started before the switch
(--switched-at), downloaded files or did not compare them with Pass A. Before any effect it checks that the
checkout is the clean deployment/internal branch pinned to the candidate and
that the Coolify app is live's (named quote-internal). Then: rewrite the pin
to the previous image, commit, push (never forced), deploy the app with
Coolify's deploy API and wait for the deployment to finish. Without --execute
it only prints the plan. The record holds ids, statuses and timings only.

Exit codes: 0 walk passed, nothing done; 10 rolled back; 3 rollback needed but
not executed (no --execute); 2 refused before any effect; 1 rollback failed
(live may still run the candidate: act at once).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")
REOPEN_STEP_RE = re.compile(r"^quote [0-9]+ reopens read-only$")
BRANCH = "deployment/internal"
COMPOSE = Path("deploy") / "internal" / "compose.yaml"
LIVE_APP_NAME = "quote-internal"
SOURCE_LINE = "# Application image source: {}\n"
IMAGE_LINE = '    image: "{}"\n'
DEPLOY_DONE = {"finished"}
DEPLOY_FAILED = {"failed", "cancelled", "cancelled-by-user", "error"}
DEPLOY_TIMEOUT_S = 15 * 60


class Refusal(Exception):
    pass


def origin_sha256(url: str) -> str:
    """Hash of the URL's origin as the walk records it (JavaScript's URL.origin)."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise Refusal("--live-url must be live's https origin with no path")
    port = parts.port
    origin = f"https://{host}" + (f":{port}" if port and port != 443 else "")
    return hashlib.sha256(origin.encode("utf-8")).hexdigest()


def utc_time(text: str, label: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        raise Refusal(f"{label} is not an ISO time") from None
    if value.tzinfo is None:
        raise Refusal(f"{label} must say its time zone (UTC)")
    return value.astimezone(timezone.utc)


def walk_passed(result_path: Path, live_origin_sha256: str, switched_at: datetime) -> tuple[bool, str]:
    """True only for a complete, passing live-data walk of live, run after the switch."""
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False, "the Pass B result is missing or unreadable"
    if not isinstance(result, dict) or result.get("schema") != "sqag-rehearsal-check/v1" or result.get("profile") != "live-readonly":
        return False, "the Pass B result is not a live-readonly result"
    if result.get("result") != "pass":
        return False, "the Pass B walk failed"
    if result.get("walked_origin_sha256") != live_origin_sha256:
        return False, "the Pass B result did not walk live's host"
    try:
        started = utc_time(result.get("started_at"), "the Pass B start time")
    except Refusal:
        return False, "the Pass B result has no start time"
    if started < switched_at:
        return False, "the Pass B walk started before live switched to the candidate"
    if result.get("downloaded_files") is not False or result.get("compared_with_pass_a") is not True:
        return False, "the Pass B walk downloaded files or did not compare them with Pass A"
    steps = {step.get("name"): step.get("status") for step in result.get("steps") or [] if isinstance(step, dict)}
    for needed in ("walk targets", "served app.js", "no write attempted"):
        if steps.get(needed) != "pass":
            return False, f"the Pass B walk has no passing '{needed}' step"
    if not any(REOPEN_STEP_RE.match(name) and status == "pass" for name, status in steps.items() if isinstance(name, str)):
        return False, "the Pass B walk reopened no quote"
    return True, "the Pass B walk passed"


class Git:
    def __init__(self, checkout: Path) -> None:
        self.checkout = checkout

    def run(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.checkout), *args], capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed (exit {done.returncode})")
        return done.stdout.strip()

    def branch(self) -> str:
        return self.run("rev-parse", "--abbrev-ref", "HEAD")

    def clean(self) -> bool:
        return self.run("status", "--porcelain") == ""

    def commit_all(self, message: str) -> str:
        self.run("add", "--", str(COMPOSE))
        self.run("commit", "-m", message)
        return self.run("rev-parse", "HEAD")

    def push(self) -> None:
        self.run("push", "origin", f"HEAD:refs/heads/{BRANCH}")


class CoolifyHttpError(RuntimeError):
    """A Coolify call answered with an HTTP error. Only the method and status are kept, never the body."""

    def __init__(self, method: str, status: int) -> None:
        super().__init__(f"Coolify {method} returned HTTP {status}")
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the token to another address, so it becomes an error instead."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class Coolify:
    def __init__(self, env: dict[str, str]) -> None:
        base = str(env.get("COOLIFY_SWOOSHZ_API_URL") or "").rstrip("/")
        token = str(env.get("COOLIFY_SWOOSHZ_API_TOKEN") or "")
        if not base.startswith("https://") or not token:
            raise Refusal("set COOLIFY_SWOOSHZ_API_URL (https) and COOLIFY_SWOOSHZ_API_TOKEN through toolkit env run")
        self.base = base if base.endswith("/api/v1") else base + "/api/v1"
        self.token = token

    def call(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=60) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise CoolifyHttpError(method, exc.code) from None

    def app_name(self, uuid: str) -> str:
        return str(self.call("GET", f"/applications/{uuid}").get("name") or "")

    def deploy(self, uuid: str) -> str:
        body = self.call("POST", f"/deploy?uuid={uuid}")
        deployments = body.get("deployments") if isinstance(body, dict) else None
        deployment = deployments[0].get("deployment_uuid") if deployments and isinstance(deployments[0], dict) else None
        if not deployment:
            raise RuntimeError("Coolify did not return a deployment id")
        return str(deployment)

    def deployment_status(self, deployment: str) -> str:
        return str(self.call("GET", f"/deployments/{deployment}").get("status") or "")


class LiveRollback:
    """Owns the rollback's checks and the order of its effects."""

    def __init__(self, args: argparse.Namespace, git: Git, coolify_factory: Callable[[], Coolify],
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> None:
        self.args = args
        self.git = git
        self.coolify_factory = coolify_factory
        self.sleep = sleep
        self.clock = clock
        self.record: dict[str, Any] = {
            "schema": "sqag-live-rollback/v1", "candidate_image": args.candidate_image,
            "previous_image": args.previous_image, "status": "running", "steps": [],
        }

    def step(self, name: str, status: str = "pass", **detail: Any) -> None:
        self.record["steps"].append({"name": name, "status": status, **detail})

    def check_inputs(self) -> None:
        a = self.args
        if not (IMAGE_RE.match(a.candidate_image) and IMAGE_RE.match(a.previous_image)):
            raise Refusal("images must be sha256:<64 hex> ids")
        if not (COMMIT_RE.match(a.candidate_source) and COMMIT_RE.match(a.previous_source)):
            raise Refusal("sources must be full 40-character commits")
        if a.candidate_image == a.previous_image:
            raise Refusal("the previous image equals the candidate")
        if not UUID_RE.match(a.live_app_uuid):
            raise Refusal("--live-app-uuid is not a Coolify uuid")

    def rolled_back_text(self) -> str:
        compose = (self.args.deploy_checkout / COMPOSE).read_text(encoding="utf-8").replace("\r\n", "\n")
        a = self.args
        expected = (SOURCE_LINE.format(a.candidate_source), IMAGE_LINE.format(a.candidate_image))
        if any(compose.count(line) != 1 for line in expected) or not compose.startswith(expected[0]):
            raise Refusal("the deployment checkout is not pinned to the candidate, so the switch was not made from it")
        return compose.replace(expected[0], SOURCE_LINE.format(a.previous_source)).replace(expected[1], IMAGE_LINE.format(a.previous_image))

    def check_checkout(self) -> str:
        if self.git.branch() != BRANCH:
            raise Refusal(f"the deployment checkout is not on {BRANCH}")
        if not self.git.clean():
            raise Refusal("the deployment checkout has uncommitted changes")
        return self.rolled_back_text()

    def run(self) -> int:
        passed, reason = walk_passed(self.args.pass_b_result, origin_sha256(self.args.live_url),
                                     utc_time(self.args.switched_at, "--switched-at"))
        self.record["decision"] = "keep" if passed else "rollback"
        self.record["reason"] = reason
        if passed:
            self.record["status"] = "not_needed"
            return 0
        self.check_inputs()
        text = self.check_checkout()
        self.step("checkout pinned to the candidate")
        if not self.args.execute:
            self.record["status"] = "planned"
            return 3
        coolify = self.coolify_factory()
        if coolify.app_name(self.args.live_app_uuid) != LIVE_APP_NAME:
            raise Refusal(f"the Coolify app is not {LIVE_APP_NAME}")
        self.step("Coolify app is live's")
        (self.args.deploy_checkout / COMPOSE).write_bytes(text.encode("utf-8"))
        commit = self.git.commit_all(f"Roll back internal image to {self.args.previous_source[:7]} after the live check failed")
        self.step("pin commit", commit=commit[:7])
        self.git.push()
        self.step("pin pushed")
        deployment = coolify.deploy(self.args.live_app_uuid)
        self.step("deploy started")
        started = self.clock()
        while True:
            status = coolify.deployment_status(deployment)
            if status in DEPLOY_DONE:
                break
            if status in DEPLOY_FAILED or self.clock() - started > DEPLOY_TIMEOUT_S:
                raise RuntimeError(f"the rollback deployment ended {status or 'without a status'}")
            self.sleep(10)
        self.step("deploy finished", seconds=round(self.clock() - started))
        self.record["status"] = "rolled_back"
        return 10


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Roll live back to the previous image when Pass B fails.")
    parser.add_argument("--pass-b-result", type=Path, required=True)
    parser.add_argument("--live-url", required=True, help="live's https origin, as Pass B walked it")
    parser.add_argument("--switched-at", required=True, help="UTC time the candidate's live deploy finished")
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--candidate-source", required=True)
    parser.add_argument("--previous-image", required=True)
    parser.add_argument("--previous-source", required=True)
    parser.add_argument("--deploy-checkout", type=Path, required=True)
    parser.add_argument("--live-app-uuid", required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def run(argv: list[str] | None, env: dict[str, str], git_factory=Git, coolify_factory=Coolify, **timing: Any) -> tuple[int, dict[str, Any]]:
    args = parse_args(argv)
    rollback = LiveRollback(args, git_factory(args.deploy_checkout), lambda: coolify_factory(env), **timing)
    try:
        code = rollback.run()
    except Refusal as exc:
        rollback.record.update(status="refused", error=str(exc))
        code = 2
    except Exception as exc:  # names only: messages may carry URLs or paths
        rollback.record.update(status="failed", error=f"{type(exc).__name__}: {exc}" if isinstance(exc, RuntimeError) else type(exc).__name__)
        code = 1
    args.record.write_text(json.dumps(rollback.record, indent=2) + "\n", encoding="utf-8")
    return code, rollback.record


def main(argv: list[str] | None = None) -> int:
    code, record = run(argv, dict(os.environ))
    print(json.dumps({"decision": record.get("decision"), "status": record["status"], "error": record.get("error")}))
    return code


if __name__ == "__main__":
    sys.exit(main())
