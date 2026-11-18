#!/usr/bin/env python3
"""Check the internal release settings record and compare an app's env names with it.

The settings file (deploy/internal/release-settings.json) is a deployment record:
non-secret shared values plus every secret by name only. The app never reads it.

This checker never reads env values. `--env-names` takes a file with one env
name per line (for example from `docker inspect` piped through `cut -d= -f1` on
the server). A line containing `=` is refused without echoing it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = ROOT / "deploy" / "internal" / "release-settings.json"
SCHEMA = "sqag-release-settings/v1"
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
PLACEHOLDER_RE = re.compile(r"^<[^<>]+>$")
SECRET_LIKE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|://[^/\s]*@|BEGIN [A-Z ]*PRIVATE KEY|[A-Za-z0-9+/=_-]{40,})"
)
LIST_FIELDS = (
    "per_app",
    "never_in_git",
    "optional",
    "must_be_unset",
    "must_be_unset_prefixes",
    "platform_names",
    "platform_prefixes",
)


class SettingsError(Exception):
    pass


def parse_template(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip()
    return values


def load_settings(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"settings file unreadable: {type(exc).__name__}") from None
    if not isinstance(data, dict):
        raise SettingsError("settings file must hold a JSON object")
    return data


def settings_names(data: dict[str, Any]) -> set[str]:
    shared = data.get("shared") if isinstance(data.get("shared"), dict) else {}
    return set(shared) | set(data.get("per_app") or []) | set(data.get("never_in_git") or [])


def validate_settings(data: dict[str, Any], template: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if data.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    shared = data.get("shared")
    if not isinstance(shared, dict):
        errors.append("shared must be an object of NAME: value-or-null")
        shared = {}
    for field in LIST_FIELDS:
        value = data.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            errors.append(f"{field} must be a list of strings")
            data[field] = []
        elif len(set(value)) != len(value):
            errors.append(f"{field} has duplicate entries")

    groups = {"shared": set(shared), "per_app": set(data["per_app"]), "never_in_git": set(data["never_in_git"])}
    every_name = set().union(*groups.values()) | set(data["must_be_unset"]) | set(data["platform_names"])
    for name in sorted(every_name):
        if not NAME_RE.match(name):
            errors.append(f"invalid env name in settings: {name[:40]!r}")
    for prefix in data["must_be_unset_prefixes"] + data["platform_prefixes"]:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*_", prefix):
            errors.append(f"invalid prefix in settings: {prefix[:40]!r}")
    seen: dict[str, str] = {}
    for group, names in groups.items():
        for name in sorted(names):
            if name in seen:
                errors.append(f"{name} is listed in both {seen[name]} and {group}")
            seen.setdefault(name, group)

    union = settings_names(data)
    for name in sorted(set(data["optional"]) - union):
        errors.append(f"optional name {name} is not in shared, per_app or never_in_git")
    for name in sorted(union & set(data["must_be_unset"])):
        errors.append(f"{name} is both a release setting and must_be_unset")
    for name in sorted(set(template) - union):
        errors.append(f"template name {name} is missing from the settings file")
    for name in sorted(union - set(template)):
        errors.append(f"settings name {name} is not in the env template")

    for name, value in shared.items():
        if value is None:
            continue
        if not isinstance(value, str):
            errors.append(f"shared value for {name} must be a string or null")
            continue
        if any(ord(ch) < 32 for ch in value) or SECRET_LIKE_RE.search(value):
            errors.append(f"shared value for {name} looks like a secret or is malformed (value not shown)")
            continue
        expected = template.get(name, "")
        if expected and not PLACEHOLDER_RE.match(expected) and expected != value:
            errors.append(f"shared value for {name} differs from the env template's fixed value")
        if value == "" and name not in data["optional"]:
            errors.append(f"shared value for {name} is empty but the name is not optional")
    return errors


def pending_names(data: dict[str, Any]) -> list[str]:
    shared = data.get("shared") if isinstance(data.get("shared"), dict) else {}
    return sorted(name for name, value in shared.items() if value is None)


def read_names_file(path: Path, label: str) -> tuple[set[str], list[str]]:
    errors: list[str] = []
    names: set[str] = set()
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return names, [f"{label} file unreadable: {type(exc).__name__}"]
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            errors.append(f"{label} line {number} contains '='; pass names only (line not shown)")
            continue
        if not NAME_RE.match(line):
            errors.append(f"{label} line {number} is not an env name (line not shown)")
            continue
        names.add(line)
    return names, errors


def compare_env_names(data: dict[str, Any], present: set[str], ignored: set[str]) -> dict[str, list[str]]:
    union = settings_names(data)
    required = union - set(data["optional"])
    platform = set(data["platform_names"]) | ignored
    platform_prefixes = tuple(data["platform_prefixes"])
    unset_names = set(data["must_be_unset"])
    unset_prefixes = tuple(data["must_be_unset_prefixes"])
    must_be_unset_present = sorted(
        name for name in present if name in unset_names or name.startswith(unset_prefixes)
    )
    unexpected = sorted(
        name
        for name in present - union
        if name not in platform
        and not name.startswith(platform_prefixes)
        and name not in must_be_unset_present
    )
    return {
        "missing": sorted(required - present),
        "unexpected": unexpected,
        "must_be_unset_present": must_be_unset_present,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--template", type=Path, default=None, help="Defaults to the settings file's template path.")
    parser.add_argument("--require-complete", action="store_true", help="Fail while any shared value is still null.")
    parser.add_argument("--env-names", type=Path, help="File with one env name per line (names only).")
    parser.add_argument(
        "--ignore-names",
        type=Path,
        help="File with env names baked into the image (names only); they are not reported as unexpected.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report: dict[str, Any] = {"status": "ok", "errors": [], "pending": []}
    try:
        data = load_settings(args.settings)
        template_path = args.template or ROOT / str(data.get("template") or "")
        if not template_path.is_file():
            raise SettingsError("env template not found")
        template = parse_template(template_path)
    except SettingsError as exc:
        report.update(status="fail", errors=[str(exc)])
        print(json.dumps(report, sort_keys=True))
        return 1

    report["errors"] = validate_settings(data, template)
    report["pending"] = pending_names(data)
    if args.require_complete and report["pending"]:
        report["errors"].append("shared values still pending: " + ", ".join(report["pending"]))

    if args.env_names:
        present, name_errors = read_names_file(args.env_names, "env-names")
        ignored: set[str] = set()
        if args.ignore_names:
            ignored, ignore_errors = read_names_file(args.ignore_names, "ignore-names")
            name_errors += ignore_errors
        report["errors"] += name_errors
        if not name_errors:
            comparison = compare_env_names(data, present, ignored)
            report.update(comparison)
            if comparison["missing"]:
                report["errors"].append("missing env names: " + ", ".join(comparison["missing"]))
            if comparison["unexpected"]:
                report["errors"].append("unexpected env names: " + ", ".join(comparison["unexpected"]))
            if comparison["must_be_unset_present"]:
                report["errors"].append(
                    "env names that must stay unset: " + ", ".join(comparison["must_be_unset_present"])
                )

    if report["errors"]:
        report["status"] = "fail"
    print(json.dumps(report, sort_keys=True))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
