#!/usr/bin/env python3
"""Refuse a database target that is Neon's default (production) branch.

Pass A's copy step (scripts/copy_live_objects_to_rehearsal.py) runs check_database_target
before any database or bucket call. The database URL comes from an environment variable.
It is never printed, logged or stored: the result holds only the host, the endpoint id and
branch facts.

Every rule fails closed:
- the rehearsal map must record the production endpoints (production_guard);
- the URL must name exactly one Neon endpoint host (ep-<id>, optionally ep-<id>-pooler,
  under .neon.tech), with no second host and no host override in its query (host,
  hostaddr, service, or options and sslsni, which can name another endpoint);
- that endpoint id must not be a recorded production endpoint;
- when NEON_API_KEY is set (even blank, which then fails), the Neon API must answer: the
  endpoint's branch must not be the default, must not be a recorded production branch,
  and must start with the rehearsal prefix; the result then records the branch id for
  the role password reset. Without the key only the endpoint rule applies and the result
  says neon_api not_checked.

scripts/rehearsal_database_url.py re-runs this check on every URL it writes to a rehearsal
service or hands to a migration step, and refuses one that is not on the guarded endpoint.

Before the verdict one line goes to stderr: "target host <host> branch <name or not checked>".
Exit codes (CLI): 0 pass, 2 refused, 1 unexpected error. The result file is written in every case.

CLI: python scripts/neon_branch_guard.py --database-url-env NAME --result FILE [--map PATH]
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / "deploy" / "rehearsal" / "resources.json"
RESULT_SCHEMA = "sqag-db-guard/v1"
NEON_API = "https://console.neon.tech/api/v2"
NEON_TIMEOUT_S = 20
NEON_KEY_ENV = "NEON_API_KEY"
NEON_UNREACHABLE = "Neon API unreachable while a key is set"
UNKNOWN_HOST = "unknown host"
NEON_SUFFIX = ".neon.tech"
ENDPOINT_PREFIX = "ep-"
POOLER_SUFFIX = "-pooler"
HOST_PORT_RE = re.compile(r"([a-z0-9.-]+)(?::[0-9]{1,5})?")
ENDPOINT_ID_RE = re.compile(r"ep-[a-z0-9]+(?:-[a-z0-9]+)*")
ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# libpq keys that can send the connection somewhere other than the URL's host: Neon reads
# its endpoint from `options` (endpoint=...) when SNI is off (`sslsni=0`).
HOST_OVERRIDE_KEYS = frozenset({"host", "hostaddr", "service", "options", "sslsni"})
UNEXPECTED_NEON_RESPONSE = "unexpected Neon API response"


class GuardRefusal(Exception):
    """The target is refused. The message names no values; result is the verdict record."""

    def __init__(self, reason: str, result: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.result = result


class NeonApiError(Exception):
    """A Neon API call failed or answered in an unexpected shape. The message names no values."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would resend the key to another address, so it becomes an error instead."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _seg(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _field(data: Any, *keys: str) -> Any:
    value = data
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise NeonApiError(UNEXPECTED_NEON_RESPONSE)
        value = value[key]
    return value


def _text_field(data: Any, *keys: str) -> str:
    value = _field(data, *keys)
    if not isinstance(value, str) or not value:
        raise NeonApiError(UNEXPECTED_NEON_RESPONSE)
    return value


class NeonClient:
    """Read-only Neon API calls for the guard. The key is sent only in the Authorization header."""

    def __init__(self, api_key: str, *, timeout: float = NEON_TIMEOUT_S) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def _get(self, path: str, query: Mapping[str, str] | None = None) -> Any:
        url = NEON_API + path + ("?" + urllib.parse.urlencode(query) if query else "")
        try:
            request = urllib.request.Request(
                url,
                method="GET",
                headers={"Accept": "application/json", "Authorization": f"Bearer {self._api_key}"},
            )
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, http.client.HTTPException, ValueError):
            raise NeonApiError("Neon API call failed") from None

    def project_ids_named(self, name: str) -> list[str]:
        """Ids of the projects whose name is exactly `name`; the search itself is fuzzy."""
        projects = _field(self._get("/projects", {"search": name}), "projects")
        if not isinstance(projects, list):
            raise NeonApiError(UNEXPECTED_NEON_RESPONSE)
        return [
            item["id"]
            for item in projects
            if isinstance(item, dict) and item.get("name") == name and isinstance(item.get("id"), str)
        ]

    def endpoint_branch_id(self, project_id: str, endpoint_id: str) -> str:
        path = f"/projects/{_seg(project_id)}/endpoints/{_seg(endpoint_id)}"
        return _text_field(self._get(path), "endpoint", "branch_id")

    def branch(self, project_id: str, branch_id: str) -> tuple[str, bool]:
        """(name, default flag) of one branch."""
        data = self._get(f"/projects/{_seg(project_id)}/branches/{_seg(branch_id)}")
        name = _field(data, "branch", "name")
        default = _field(data, "branch", "default")
        if not isinstance(name, str) or not name or not isinstance(default, bool):
            raise NeonApiError(UNEXPECTED_NEON_RESPONSE)
        return name, default


def neon_client_from_env(env: Mapping[str, str]) -> NeonClient | None:
    """A client when NEON_API_KEY is set. A blank key still counts as set, so the API check fails closed."""
    return NeonClient(env[NEON_KEY_ENV]) if NEON_KEY_ENV in env else None


def load_map(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GuardRefusal("rehearsal map is unreadable") from None
    if not isinstance(data, dict):
        raise GuardRefusal("rehearsal map must be a JSON object")
    return data


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _production_record(rehearsal_map: Mapping[str, Any]) -> tuple[str, str, list[dict[str, str]]]:
    """(Neon project name, rehearsal branch prefix, recorded production endpoints and branches)."""
    section = rehearsal_map.get("production_guard")
    endpoints = section.get("forbidden_endpoints") if isinstance(section, Mapping) else None
    if not isinstance(endpoints, list) or not endpoints:
        raise GuardRefusal("no production endpoint recorded")
    records: list[dict[str, str]] = []
    for entry in endpoints:
        endpoint_id = _text(entry.get("endpoint_id")) if isinstance(entry, Mapping) else ""
        branch_id = _text(entry.get("branch_id")) if isinstance(entry, Mapping) else ""
        if not endpoint_id or not branch_id:
            raise GuardRefusal("production endpoint record is malformed")
        records.append({"endpoint_id": endpoint_id.lower(), "branch_id": branch_id.lower()})
    project = _text(section.get("neon_project"))
    prefix = _text(section.get("branch_prefix"))
    if not project or not prefix:
        raise GuardRefusal("production_guard needs neon_project and branch_prefix")
    return project, prefix, records


def parse_target(database_url: str) -> tuple[str, str]:
    """(host, endpoint id) of one Neon endpoint URL. Anything else is refused without echoing it.

    The whole host part of the netloc is checked, not only .hostname: urlsplit().hostname
    drops a second host listed after a port, and the query can override the host.
    """
    try:
        parts = urllib.parse.urlsplit(database_url)
        query_keys = {key.lower() for key, _ in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)}
    except ValueError:
        raise GuardRefusal(UNKNOWN_HOST) from None
    if parts.netloc.count("@") > 1:
        raise GuardRefusal(UNKNOWN_HOST)
    match = HOST_PORT_RE.fullmatch(parts.netloc.rpartition("@")[2].lower())
    if match is None:
        raise GuardRefusal(UNKNOWN_HOST)
    host = match.group(1)
    label = host.partition(".")[0]
    if not host.endswith(NEON_SUFFIX) or not label.startswith(ENDPOINT_PREFIX):
        raise GuardRefusal(UNKNOWN_HOST)
    endpoint_id = label[: -len(POOLER_SUFFIX)] if label.endswith(POOLER_SUFFIX) else label
    if ENDPOINT_ID_RE.fullmatch(endpoint_id) is None:
        raise GuardRefusal(UNKNOWN_HOST)
    if query_keys & HOST_OVERRIDE_KEYS:
        raise GuardRefusal(f"{UNKNOWN_HOST}: the URL sets a host override")
    return host, endpoint_id


def _endpoint_branch(neon: Any, project_name: str, endpoint_id: str) -> tuple[str, str, bool] | None:
    """(branch id, branch name, default flag) read from the Neon API; None when the name is not unique.

    Any call failure raises the fixed unreachable refusal, so no provider text reaches the result.
    """
    try:
        project_ids = neon.project_ids_named(project_name)
        if len(project_ids) != 1:
            return None
        branch_id = neon.endpoint_branch_id(project_ids[0], endpoint_id)
        branch_name, branch_default = neon.branch(project_ids[0], branch_id)
    except Exception:
        raise GuardRefusal(NEON_UNREACHABLE) from None
    return branch_id, branch_name, branch_default


def _check_neon(
    neon: Any, project_name: str, prefix: str, production: list[dict[str, str]], endpoint_id: str, result: dict
) -> None:
    facts = _endpoint_branch(neon, project_name, endpoint_id)
    if facts is None:
        raise GuardRefusal("the map's Neon project name does not match exactly one project")
    branch_id, branch_name, branch_default = facts
    result.update(neon_api="checked", branch_id=branch_id, branch_name=branch_name, branch_default=branch_default)
    if branch_default:
        raise GuardRefusal("target branch is Neon's default (production) branch")
    if branch_id.lower() in {record["branch_id"] for record in production}:
        raise GuardRefusal("target branch is a recorded production branch")
    if not branch_name.startswith(prefix):
        raise GuardRefusal("target branch name does not start with the rehearsal prefix")


def _decide(database_url: str, rehearsal_map: Mapping[str, Any], neon: Any, result: dict) -> None:
    project_name, prefix, production = _production_record(rehearsal_map)
    host, endpoint_id = parse_target(database_url)
    result.update(host=host, endpoint_id=endpoint_id)
    if endpoint_id in {record["endpoint_id"] for record in production}:
        raise GuardRefusal("target is a recorded production endpoint")
    if neon is not None:
        _check_neon(neon, project_name, prefix, production, endpoint_id, result)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_result() -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA,
        "status": "refused",
        "host": None,
        "endpoint_id": None,
        "branch_id": None,
        "branch_name": None,
        "branch_default": None,
        "neon_api": "not_checked",
        "checked_at": None,
        "reason": None,
    }


def _finish(result: dict[str, Any]) -> None:
    result["checked_at"] = _now()
    host = result["host"] or "unknown"
    branch = result["branch_name"] or "not checked"
    print(f"target host {host} branch {branch}", file=sys.stderr)


def check_database_target(database_url: str, rehearsal_map: Mapping[str, Any], neon: Any) -> dict[str, Any]:
    """Return the pass result, or raise GuardRefusal (its .result holds the refusal record)."""
    result = _new_result()
    try:
        _decide(database_url.strip(), rehearsal_map, neon, result)
    except GuardRefusal as exc:
        result.update(status="refused", reason=str(exc))
        _finish(result)
        raise GuardRefusal(str(exc), result) from None
    result.update(status="pass", reason=None)
    _finish(result)
    return result


def _refused_result(reason: str) -> dict[str, Any]:
    result = _new_result()
    result.update(reason=reason, checked_at=_now())
    return result


def check_from_env(name: str, map_path: Path, env: Mapping[str, str]) -> dict[str, Any]:
    if not ENV_NAME_RE.fullmatch(name):
        raise GuardRefusal("--database-url-env must name an environment variable (letters, digits, underscore)")
    url = str(env.get(name) or "").strip()
    if not url:
        raise GuardRefusal(f"environment variable {name} is unset or empty")
    return check_database_target(url, load_map(map_path), neon_client_from_env(env))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refuse a database URL that is Neon's default (production) branch.")
    parser.add_argument("--database-url-env", required=True, help="name of the environment variable holding the URL")
    parser.add_argument("--result", type=Path, required=True, help="file to write the JSON result to")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = check_from_env(args.database_url_env, args.map, os.environ if env is None else env)
        code = 0
    except GuardRefusal as exc:
        result = exc.result or _refused_result(str(exc))
        code = 2
        print(f"refused: {exc}", file=sys.stderr)
    except Exception as exc:  # any other failure: type name only, never the message
        result = _refused_result(f"guard error: {type(exc).__name__}")
        code = 1
        print(result["reason"], file=sys.stderr)
    try:
        args.result.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        print("result file could not be written", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
