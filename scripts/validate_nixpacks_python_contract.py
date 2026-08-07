#!/usr/bin/env python3
"""Validate the Nixpacks Python-only production contract with locked Nixpkgs archive.

Checks that the repository enforces:
- nixpacks.toml exists with providers = ["python"] only.
- [phases.setup] section present with an exact 40-char lowercase hex nixpkgsArchive.
- The archive matches the locked immutable NixOS/nixpkgs commit for python312==3.12.13.
- Start command is exactly python webapp/server.py.
- .python-version contains exactly 3.12.13.
- No Node provider, Dockerfile, Procfile or alternate runtime is configured.
- requirements.txt is the production dependency source.
- package.json is preserved for CI/local tooling only.

The nixpacks.toml surface is parsed completely with Python 3.12 `tomllib` and
then validated against an exact closed schema. Malformed TOML, duplicate
keys/tables, unknown top-level or nested keys, wrong value types, wrong
provider lists, missing keys, wrong immutable archive, branch/tag/floating
archive references, malformed SHAs, wrong start commands, misplaced archive
configuration, shadowing/alternate configuration, and any additional content
that would expand the locked production build contract fail closed.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = frozenset(
    {
        "nixpacks.toml",
        ".python-version",
        "requirements.txt",
    }
)

FORBIDDEN_FILES = frozenset(
    {
        "Dockerfile",
        "Dockerfile.production",
        "Procfile",
        ".nvmrc",
        "nvmrc",
        ".node-version",
        "runtime.txt",
    }
)

LOCKED_PYTHON_VERSION = "3.12.13"
LOCKED_START_CMD = "python webapp/server.py"
LOCKED_PROVIDERS = ("python",)

# Immutable NixOS/nixpkgs commit providing python312 == 3.12.13 on x86_64-linux.
# Commit message: "python312: 3.12.12 -> 3.12.13"
# Verified via: https://github.com/NixOS/nixpkgs/commit/5c994fe2b1e540ff83aa59ba370918ad5aae4776
LOCKED_NIXPKGS_ARCHIVE = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"

HEX_ARCHIVE_RE = re.compile(r"^[0-9a-f]{40}$")

EXIT_CODE_OK = 0
EXIT_CODE_SINGLE_ISSUE = 1
EXIT_CODE_MULTI_ISSUE = 2

TOMLDecodeError = tomllib.TOMLDecodeError  # type: ignore[attr-defined]

# The exact closed Nixpacks build-contract document tree.  Only this structure
# may appear; anything else expands the locked production build contract.
CLOSED_NIXPACKS_SCHEMA: dict[str, Any] = {
    "providers": ["python"],
    "phases": {"setup": {"nixpkgsArchive": LOCKED_NIXPKGS_ARCHIVE}},
    "start": {"cmd": LOCKED_START_CMD},
}


def _fail(issues: list[str]) -> int:
    for msg in issues:
        print(f"FAIL: {msg}", file=sys.stderr)
    if len(issues) == 1:
        return EXIT_CODE_SINGLE_ISSUE
    return EXIT_CODE_MULTI_ISSUE


def parse_nixpacks_toml(path: Path) -> dict[str, Any]:
    """Parse the complete nixpacks.toml with tomllib.

    Raises tomllib.TOMLDecodeError for malformed TOML, duplicate keys, and
    duplicate/shadowing table definitions. Callers fail closed on the error.
    """
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _require_exact_string(value: Any, expected: str, label: str, issues: list[str]) -> None:
    if type(value) is not str:
        issues.append(f"nixpacks.toml: {label} must be a string")
    elif value != expected:
        issues.append(
            f"nixpacks.toml: {label} is not exactly the locked value {expected!r}"
        )


def _validate_closed_schema(document: dict[str, Any], issues: list[str]) -> None:
    """Require the parsed document to equal the exact closed build contract."""
    if type(document) is not dict:
        issues.append("nixpacks.toml: parsed document must be a TOML table")
        return

    unknown_top = sorted(set(document) - set(CLOSED_NIXPACKS_SCHEMA))
    if unknown_top:
        issues.append(f"nixpacks.toml: unknown top-level keys {unknown_top}")
    missing_top = sorted(set(CLOSED_NIXPACKS_SCHEMA) - set(document))
    if missing_top:
        issues.append(f"nixpacks.toml: missing top-level keys {missing_top}")
    if unknown_top or missing_top:
        return

    providers = document["providers"]
    if type(providers) is not list:
        issues.append("nixpacks.toml: providers must be an array")
    elif providers != list(LOCKED_PROVIDERS):
        issues.append(
            f"nixpacks.toml: providers must be exactly {list(LOCKED_PROVIDERS)}, got {providers}"
        )

    phases = document["phases"]
    if type(phases) is not dict:
        issues.append("nixpacks.toml: phases must be a table")
    else:
        unknown_phases = sorted(set(phases) - set(CLOSED_NIXPACKS_SCHEMA["phases"]))
        if unknown_phases:
            issues.append(f"nixpacks.toml: unknown phases keys {unknown_phases}")
        missing_phases = sorted(set(CLOSED_NIXPACKS_SCHEMA["phases"]) - set(phases))
        if missing_phases:
            issues.append(f"nixpacks.toml: missing phases keys {missing_phases}")
        if not unknown_phases and not missing_phases:
            setup = phases["setup"]
            if type(setup) is not dict:
                issues.append("nixpacks.toml: phases.setup must be a table")
            else:
                unknown_setup = sorted(set(setup) - set(CLOSED_NIXPACKS_SCHEMA["phases"]["setup"]))
                if unknown_setup:
                    issues.append(f"nixpacks.toml: unknown phases.setup keys {unknown_setup}")
                missing_setup = sorted(
                    set(CLOSED_NIXPACKS_SCHEMA["phases"]["setup"]) - set(setup)
                )
                if missing_setup:
                    issues.append(
                        f"nixpacks.toml: missing phases.setup keys {missing_setup}"
                    )
                if not unknown_setup and not missing_setup:
                    archive = setup["nixpkgsArchive"]
                    if type(archive) is not str:
                        issues.append(
                            "nixpacks.toml: phases.setup.nixpkgsArchive must be a string"
                        )
                    elif not HEX_ARCHIVE_RE.fullmatch(archive):
                        issues.append(
                            "nixpacks.toml: nixpkgsArchive is not exactly 40 lowercase hex chars"
                        )
                    elif archive != LOCKED_NIXPKGS_ARCHIVE:
                        issues.append(
                            f"nixpacks.toml: nixpkgsArchive does not match locked archive "
                            f"{LOCKED_NIXPKGS_ARCHIVE}"
                        )

    start = document["start"]
    if type(start) is not dict:
        issues.append("nixpacks.toml: start must be a table")
    else:
        unknown_start = sorted(set(start) - set(CLOSED_NIXPACKS_SCHEMA["start"]))
        if unknown_start:
            issues.append(f"nixpacks.toml: unknown start keys {unknown_start}")
        missing_start = sorted(set(CLOSED_NIXPACKS_SCHEMA["start"]) - set(start))
        if missing_start:
            issues.append(f"nixpacks.toml: missing start keys {missing_start}")
        if not unknown_start and not missing_start:
            _require_exact_string(
                start["cmd"], LOCKED_START_CMD, "start.cmd", issues
            )


def validate() -> int:
    """Return an OS-style exit code."""
    issues: list[str] = []

    # -- 1. Required files exist ---------------------------------------------------
    for name in sorted(REQUIRED_FILES):
        p = ROOT / name
        if not p.is_file():
            issues.append(f"missing required file: {name}")
        elif p.stat().st_size == 0:
            issues.append(f"required file is empty: {name}")

    # -- 2. Forbidden files must be absent -----------------------------------------
    for name in sorted(FORBIDDEN_FILES):
        if (ROOT / name).is_file():
            issues.append(f"forbidden production file present: {name}")

    # -- 3. nixpacks.toml parsed completely and validated against the closed schema
    nixpacks_path = ROOT / "nixpacks.toml"
    if nixpacks_path.is_file() and nixpacks_path.stat().st_size > 0:
        try:
            document = parse_nixpacks_toml(nixpacks_path)
        except TOMLDecodeError as exc:
            issues.append(f"nixpacks.toml: invalid TOML rejected: {exc}")
            document = None
        except OSError as exc:
            issues.append(f"nixpacks.toml: unreadable: {exc}")
            document = None
        if document is not None:
            _validate_closed_schema(document, issues)
    else:
        issues.append("nixpacks.toml: missing or empty")

    # -- 4. .python-version --------------------------------------------------------
    pyver_path = ROOT / ".python-version"
    if pyver_path.is_file() and pyver_path.stat().st_size > 0:
        content = pyver_path.read_text(encoding="utf-8").strip()
        if content != LOCKED_PYTHON_VERSION:
            issues.append(
                f".python-version: expected {LOCKED_PYTHON_VERSION}, got {content}"
            )
    else:
        issues.append(".python-version: missing or empty")

    # -- 5. requirements.txt bound --------------------------------------------------
    req_path = ROOT / "requirements.txt"
    if req_path.is_file() and req_path.stat().st_size > 0:
        req_text = req_path.read_text(encoding="utf-8").strip()
        if not req_text:
            issues.append("requirements.txt: empty")
    else:
        issues.append("requirements.txt: missing or empty")

    # -- 6. package.json preserved --------------------------------------------------
    pkg_path = ROOT / "package.json"
    if not pkg_path.is_file():
        issues.append("package.json: missing (allowed CI/local tooling file)")

    if issues:
        return _fail(issues)

    print("PASS: Nixpacks Python-only production contract verified")
    return EXIT_CODE_OK


if __name__ == "__main__":
    sys.exit(validate())
