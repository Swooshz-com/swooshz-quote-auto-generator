#!/usr/bin/env python3
"""Check the rehearsal resource map and compose file offline.

Fails when:
- the map holds anything that looks like a secret value;
- a per-run resource has no delete step, or its name does not say "rehearsal";
- a rehearsal name equals a live name, or the compose file mentions a live name;
- an app env name in the compose file has no source in the map, or the
  compose names differ from the release settings file;
- a secret is written into the compose file, or a shared setting is not taken
  from the settings file;
- the compose file holds anything but the one app service (no storage or
  other third-party container), publishes a host port, mounts a volume or
  host path, or loads an env file;
- storage is not the map's rehearsal-only bucket, or the bucket has no
  clear step.

It reads only repository files. It never reads env values.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / "deploy" / "rehearsal" / "resources.json"
SCHEMA = "sqag-rehearsal-map/v1"
INTERPOLATION_RE = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)(?::\?[^}]*)?\}$")
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|://[^/\s<>]*:[^/\s<>]*@|postgres(?:ql)?://|BEGIN [A-Z ]*PRIVATE KEY)"
)
LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
KEBAB_NAME_RE = re.compile(r"^[a-z0-9]{1,12}(?:-[a-z0-9]{1,12})+$")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
SECRET_KEY_NAMES = {"value", "values", "password", "secret", "secret_value", "token", "api_key", "apikey", "key_value"}
ENV_SOURCE_KINDS = (
    "settings",
    "compose",
    "compose_synthetic",
    "run_value",
    "generated",
    "neon_branch",
    "credential_store",
    "not_used",
)
LITERAL_SOURCES = {"compose", "compose_synthetic"}
SECRET_SOURCES = {"generated", "neon_branch", "credential_store"}
KEPT_KINDS = {"credential_store_entry", "r2_bucket"}
SYNTHETIC_EMAIL_RE = re.compile(r"^[a-z0-9._-]+@example\.com$")


class MiniYamlError(Exception):
    pass


def _strip_comment(line: str) -> str:
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == "\\" and quote == '"':
                continue
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index].rstrip()
    return line.rstrip()


def _scalar(text: str) -> Any:
    text = text.strip()
    if text.startswith('"'):
        if not text.endswith('"') or len(text) < 2:
            raise MiniYamlError("unterminated double-quoted string")
        return json.loads(text)
    if text.startswith("'"):
        if not text.endswith("'") or len(text) < 2:
            raise MiniYamlError("unterminated single-quoted string")
        return text[1:-1].replace("''", "'")
    if text in {"", "~", "null"}:
        return None
    if text[0] in "{[&*|>!%@`":
        raise MiniYamlError(f"unsupported YAML value: {text[:30]!r}")
    return text


def parse_mini_yaml(text: str) -> Any:
    """Parse the block-style YAML subset the rehearsal compose file uses.

    Supports nested mappings, lists of scalars, quoted and plain scalars and
    comments. Anything else (flow collections, anchors, multi-line strings)
    is refused so the checker never guesses.
    """

    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise MiniYamlError("tab indentation is not supported")
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        content = stripped.strip()
        if content[0] in "{[&*|>!" or content.startswith("- {") or content.startswith("- ["):
            raise MiniYamlError(f"unsupported YAML construct: {content[:30]!r}")
        lines.append((len(stripped) - len(stripped.lstrip(" ")), content))

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(lines):
            return None, index
        is_list = lines[index][1].startswith("- ") or lines[index][1] == "-"
        result: Any = [] if is_list else {}
        while index < len(lines):
            line_indent, content = lines[index]
            if line_indent < indent:
                break
            if line_indent > indent:
                raise MiniYamlError(f"unexpected indentation at {content[:30]!r}")
            if is_list:
                if not content.startswith("- "):
                    raise MiniYamlError(f"expected a list item at {content[:30]!r}")
                item = content[2:].strip()
                if re.match(r"^[A-Za-z0-9_.-]+:(\s|$)", item) and not item.startswith(("'", '"')):
                    raise MiniYamlError("lists of mappings are not supported")
                result.append(_scalar(item))
                index += 1
                continue
            match = re.match(r'^("(?:[^"\\]|\\.)*"|[A-Za-z0-9_.-]+):(?:\s+(.*))?$', content)
            if not match:
                raise MiniYamlError(f"expected 'key: value' at {content[:30]!r}")
            key = _scalar(match.group(1))
            if key in result:
                raise MiniYamlError(f"duplicate key {key!r}")
            rest = match.group(2)
            index += 1
            if rest is None or rest == "":
                if index < len(lines) and lines[index][0] > line_indent:
                    result[key], index = parse_block(index, lines[index][0])
                elif index < len(lines) and lines[index][0] == line_indent and lines[index][1].startswith("- "):
                    result[key], index = parse_block(index, line_indent)
                else:
                    result[key] = None
            else:
                result[key] = _scalar(rest)
        return result, index

    if not lines:
        return {}
    parsed, end = parse_block(0, lines[0][0])
    if end != len(lines):
        raise MiniYamlError("could not parse the whole file")
    return parsed


def _walk_strings(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_strings(child, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_strings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _walk_keys(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, str(key)
            yield from _walk_keys(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{path}[{index}]")


def looks_like_secret(value: str) -> bool:
    """True for strings shaped like credentials: known prefixes, URLs with
    userinfo, database URLs, keys, or long random tokens mixing character
    classes. Image digests and plain env names are not secrets."""

    text = DIGEST_RE.sub("", value)
    if SECRET_VALUE_RE.search(text):
        return True
    for token in LONG_TOKEN_RE.findall(text):
        if KEBAB_NAME_RE.match(token) or ENV_NAME_RE.match(token):
            continue
        classes = sum(
            bool(re.search(pattern, token)) for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]")
        )
        if classes >= 2:
            return True
    return False


def check_map_values(data: dict[str, Any]) -> list[str]:
    errors = []
    for path, value in _walk_strings(data):
        if looks_like_secret(value):
            errors.append(f"map field {path} looks like it holds a secret value (value not shown)")
    for path, key in _walk_keys(data):
        if key.lower() in SECRET_KEY_NAMES:
            errors.append(f"map field {path} is named like a secret value; the map holds names and sources only")
    return errors


def check_resources(data: dict[str, Any]) -> list[str]:
    errors = []
    live_names = {str(name).lower() for name in data.get("live_names") or []}
    if not live_names:
        errors.append("live_names must list the live resource names the rehearsal must never touch")
    resources = data.get("resources")
    if not isinstance(resources, list) or not resources:
        return errors + ["resources must be a non-empty list"]
    ids = set()
    for resource in resources:
        if not isinstance(resource, dict):
            errors.append("each resource must be an object")
            continue
        rid = str(resource.get("id") or "")
        if not rid or rid in ids:
            errors.append(f"resource id missing or duplicated: {rid!r}")
        ids.add(rid)
        name = str(resource.get("name") or "")
        lifetime = resource.get("lifetime")
        for field in ("kind", "name", "created_by", "delete"):
            if not str(resource.get(field) or "").strip():
                errors.append(f"resource {rid} has no {field}")
        if name.lower() in live_names:
            errors.append(f"resource {rid} uses a live name")
        if lifetime == "per_run":
            if "rehearsal" not in name.lower():
                errors.append(f"per-run resource {rid} must have 'rehearsal' in its name")
        elif lifetime == "kept":
            if resource.get("kind") not in KEPT_KINDS:
                errors.append(f"resource {rid} is kept but only {sorted(KEPT_KINDS)} may outlive a run")
            elif resource.get("kind") == "r2_bucket" and "rehearsal" not in name.lower():
                errors.append(f"kept bucket {rid} must have 'rehearsal' in its name")
        else:
            errors.append(f"resource {rid} lifetime must be per_run or kept")
    for name in (data.get("app_service"),):
        if not name or "rehearsal" not in str(name) or str(name).lower() in live_names:
            errors.append(f"service name {name!r} must say rehearsal and differ from live names")
    errors += check_create_steps(data)
    return errors


BRANCH_CREATE_STEPS = (
    ("public.sqag_schema_migrations", "copy live's migration ledger rows into the branch"),
    ("GRANT sqag_migrator TO neondb_owner", "grant sqag_migrator to neondb_owner on the branch"),
    ("OWNER TO sqag_migrator", "reassign each sqag_ table and function to sqag_migrator"),
    ("REVOKE sqag_migrator FROM neondb_owner", "revoke the grant afterwards"),
    ("rehearsal-<run id> branch", "reset passwords on the rehearsal branch only"),
    ("Never reset a password on the default branch", "never reset a password on the default branch"),
)


def check_create_steps(data: dict[str, Any]) -> list[str]:
    """Create steps a schema-only branch and a Coolify service network need."""
    errors = []
    branches = [
        r for r in data.get("resources") or [] if isinstance(r, dict) and r.get("kind") == "neon_branch"
    ]
    for branch in branches:
        created = str(branch.get("created_by") or "")
        for needle, step in BRANCH_CREATE_STEPS:
            if needle not in created:
                errors.append(f"resource {branch.get('id')} created_by must {step}")
    for service in (r for r in data.get("resources") or [] if isinstance(r, dict) and r.get("kind") == "coolify_service"):
        if "POST /api/v1/deploy?uuid=" not in str(service.get("created_by") or ""):
            errors.append(f"resource {service.get('id')} created_by must apply changes with POST /api/v1/deploy?uuid=, not a restart")
    run_values = data.get("run_values") if isinstance(data.get("run_values"), dict) else {}
    proxy = str(run_values.get("SQAG_TRUSTED_PROXY_CIDRS") or "")
    if (
        "rehearsal service's own network" not in proxy
        or "never a fixed value" not in proxy
        or re.search(r"\d+\.\d+\.\d+\.\d+", proxy)
    ):
        errors.append(
            "run_values.SQAG_TRUSTED_PROXY_CIDRS must be derived from the rehearsal service's own network "
            "at create time, never a fixed value or CIDR"
        )
    return errors


def check_env_sources(data: dict[str, Any], settings: dict[str, Any], app_env: dict[str, Any]) -> list[str]:
    errors = []
    sources = data.get("env_sources")
    if not isinstance(sources, dict):
        return ["env_sources must be an object"]
    unknown_kinds = set(sources) - set(ENV_SOURCE_KINDS)
    if unknown_kinds:
        errors.append(f"unknown env source kinds: {sorted(unknown_kinds)}")
    source_of: dict[str, str] = {}
    for kind in ENV_SOURCE_KINDS:
        names = sources.get(kind) or ([] if kind != "credential_store" else {})
        listed = list(names) if isinstance(names, (list, dict)) else []
        for name in listed:
            if name in source_of:
                errors.append(f"{name} has two sources: {source_of[name]} and {kind}")
            source_of[name] = kind
    for name, store_name in (sources.get("credential_store") or {}).items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,63}", str(store_name)):
            errors.append(f"credential store name for {name} is not a plain store name")
        store_entries = set()
        for r in data.get("resources") or []:
            if isinstance(r, dict) and r.get("kind") == "credential_store_entry":
                store_entries.add(r.get("name"))
                store_entries.update(r.get("entries") or [])
        if store_name not in store_entries:
            errors.append(f"credential store name for {name} is not a kept resource in the map")

    shared = set((settings.get("shared") or {}).keys())
    host_only = set(settings.get("never_in_git") or [])
    every = shared | set(settings.get("per_app") or []) | host_only
    optional = set(settings.get("optional") or [])

    for name in sorted(every - set(source_of)):
        errors.append(f"settings name {name} has no source in the map")
    for name in sorted(set(source_of) - every):
        errors.append(f"map source name {name} is not in the settings file")
    for name in sorted(shared):
        if source_of.get(name) not in {"settings", "not_used"}:
            errors.append(f"shared setting {name} must come from the settings file")
    for name in sorted(host_only):
        kind = source_of.get(name)
        if kind in {"compose", "settings", "run_value"}:
            errors.append(f"secret {name} must not have source {kind}")
    for name in sorted(set(sources.get("not_used") or []) - optional):
        errors.append(f"{name} is marked not_used but is not optional")

    for name in sorted(set(app_env) - every):
        errors.append(f"compose app env {name} is not in the settings file")
    for name, kind in sorted(source_of.items()):
        if kind == "not_used":
            if name in app_env:
                errors.append(f"{name} is marked not_used but the compose file sets it")
            continue
        if name not in app_env:
            errors.append(f"compose app env is missing {name}")
            continue
        value = app_env[name]
        interpolation = INTERPOLATION_RE.match(str(value or ""))
        if kind in LITERAL_SOURCES:
            if interpolation or value in (None, ""):
                errors.append(f"{name} must be a fixed rehearsal value in the compose file")
            elif looks_like_secret(str(value)):
                errors.append(f"compose value for {name} looks like a secret (value not shown)")
            elif kind == "compose_synthetic" and not all(
                SYNTHETIC_EMAIL_RE.match(part) for part in str(value).split(";")
            ):
                errors.append(f"{name} must hold synthetic example.com aliases only")
        elif not interpolation or interpolation.group(1) != name:
            errors.append(f"{name} must be set as ${{{name}}} in the compose file, not written into it")
    return errors


def check_compose(data: dict[str, Any], compose: dict[str, Any], compose_text: str) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    services = compose.get("services") if isinstance(compose, dict) else None
    if not isinstance(services, dict):
        return ["compose file has no services"], {}
    app_name = data.get("app_service")
    if set(services) != {app_name}:
        errors.append(f"compose must hold exactly one service, {app_name!r}; no storage or other third-party container")
    if compose.get("volumes"):
        errors.append("compose must not define volumes; the rehearsal keeps no data on the server")
    lowered = compose_text.lower()
    for name in data.get("live_names") or []:
        if str(name).lower() in lowered:
            errors.append(f"compose file refers to live name {name}")
    app = services.get(app_name) if isinstance(services.get(app_name), dict) else {}
    for forbidden in (
        "ports", "env_file", "privileged", "cap_add", "devices", "pid", "ipc", "network_mode", "networks", "volumes", "build",
    ):
        if forbidden in app:
            errors.append(f"service {app_name} must not set {forbidden}")
    image = str(app.get("image") or "")
    match = INTERPOLATION_RE.match(image)
    if not match or match.group(1) != "REHEARSAL_IMAGE_ID":
        errors.append("app image must be ${REHEARSAL_IMAGE_ID} so each run passes the image under test")
    if app.get("pull_policy") != "never":
        errors.append("app pull_policy must be never")

    app_env = app.get("environment") if isinstance(app.get("environment"), dict) else {}
    storage = data.get("storage") if isinstance(data.get("storage"), dict) else {}
    bucket = str(storage.get("bucket") or "")
    if "rehearsal" not in bucket:
        errors.append("storage.bucket must be a rehearsal-only bucket name")
    if app_env.get("SQAG_OBJECT_STORAGE_BUCKET") != bucket:
        errors.append("app bucket must equal the map's storage.bucket")
    if app_env.get("SQAG_OBJECT_STORAGE_REGION") != storage.get("region"):
        errors.append("app region must equal the map's storage.region")
    if app_env.get("SQAG_OBJECT_STORAGE_PROVIDER") != "s3_compatible":
        errors.append("app storage provider must be s3_compatible")
    buckets = [r for r in data.get("resources") or [] if isinstance(r, dict) and r.get("kind") == "r2_bucket"]
    if [r.get("name") for r in buckets] != [bucket]:
        errors.append("the map must list exactly one r2_bucket resource named like storage.bucket")
    clear = str(storage.get("clear_command") or "")
    setup = str(storage.get("clear_setup") or "")
    tools = str(storage.get("tools_dir") or "")
    if f"--expect-bucket {bucket}" not in clear or "clear_rehearsal_bucket.py" not in clear:
        errors.append("storage.clear_command must run clear_rehearsal_bucket.py with --expect-bucket for the mapped bucket")
    if not tools.startswith("/tmp/") or f"{tools}/scripts/clear_rehearsal_bucket.py" not in clear or "/app" in clear:
        errors.append("storage.clear_command must run the copied script under storage.tools_dir, never the image's /app copy")
    for needed in (
        f"docker cp /root/sqag-build-<run id>/scripts/clear_rehearsal_bucket.py <rehearsal app container>:{tools}/scripts/",
        f"docker cp /root/sqag-build-<run id>/deploy/rehearsal/resources.json <rehearsal app container>:{tools}/deploy/rehearsal/",
    ):
        if needed not in setup:
            errors.append("storage.clear_setup must copy the reviewed script and map from the candidate build folder into storage.tools_dir")
            break
    if app_env.get("SQAG_INTERNAL_WORKSPACE_ID") != data.get("workspace_id"):
        errors.append("app workspace id must equal the map's workspace_id")
    if "rehearsal" not in str(data.get("workspace_id") or ""):
        errors.append("workspace_id must say rehearsal")
    return errors, app_env


def run_checks(map_path: Path) -> list[str]:
    try:
        data = json.loads(map_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"map unreadable: {type(exc).__name__}"]
    if not isinstance(data, dict):
        return ["map must be a JSON object"]
    errors: list[str] = []
    if data.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    errors += check_map_values(data)
    errors += check_resources(data)
    base = map_path.resolve().parents[2] if len(map_path.resolve().parents) > 2 else ROOT
    compose_path = base / str(data.get("compose") or "")
    settings_path = base / str(data.get("settings") or "")
    try:
        compose_text = compose_path.read_text(encoding="utf-8")
        compose = parse_mini_yaml(compose_text)
    except (OSError, UnicodeDecodeError, MiniYamlError, json.JSONDecodeError) as exc:
        return errors + [f"compose file unreadable: {type(exc).__name__}: {exc}"]
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return errors + [f"settings file unreadable: {type(exc).__name__}"]
    compose_errors, app_env = check_compose(data, compose, compose_text)
    errors += compose_errors
    errors += check_env_sources(data, settings, app_env)
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the rehearsal resource map and compose file offline.")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    args = parser.parse_args(argv)
    errors = run_checks(args.map)
    print(json.dumps({"status": "fail" if errors else "ok", "errors": errors}, indent=None))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
