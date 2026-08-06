#!/usr/bin/env python3
"""Validate the Nixpacks Python-only production contract.

Checks that the repository enforces:
- nixpacks.toml exists with providers = ["python"] only.
- Start command is exactly python webapp/server.py.
- .python-version contains exactly 3.12.13.
- No Node provider, Dockerfile, Procfile or alternate runtime is configured.
- requirements.txt is the production dependency source.
- package.json is preserved for CI/local tooling only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

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

EXIT_CODE_OK = 0
EXIT_CODE_SINGLE_ISSUE = 1
EXIT_CODE_MULTI_ISSUE = 2


def _fail(issues: list[str]) -> int:
    for msg in issues:
        print(f"FAIL: {msg}", file=sys.stderr)
    if len(issues) == 1:
        return EXIT_CODE_SINGLE_ISSUE
    return EXIT_CODE_MULTI_ISSUE


def _parse_nixpacks_toml(path: Path) -> tuple[list[str] | None, str | None]:
    """Return (providers, start_cmd) or raise on parse failure."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None, None

    providers = None
    start_cmd = None

    in_start_section = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("["):
            in_start_section = line == "[start]"
            continue

        if in_start_section:
            m = re.match(r'^cmd\s*=\s*"(.+)"$', line)
            if m:
                start_cmd = m.group(1)
            continue

        m = re.match(r"^providers\s*=\s*\[(.+)\]$", line)
        if m:
            providers = [p.strip().strip('"').strip("'") for p in m.group(1).split(",")]
            continue

    return providers, start_cmd


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

    # -- 3. nixpacks.toml parsed ---------------------------------------------------
    nixpacks_path = ROOT / "nixpacks.toml"
    if nixpacks_path.is_file() and nixpacks_path.stat().st_size > 0:
        try:
            providers, start_cmd = _parse_nixpacks_toml(nixpacks_path)
        except Exception as exc:
            providers, start_cmd = None, None
            issues.append(f"nixpacks.toml unparseable: {exc}")
    else:
        providers, start_cmd = None, None

    # -- 4. Providers --------------------------------------------------------------
    if providers is None:
        issues.append("nixpacks.toml: providers absent")
    else:
        if len(providers) != 1:
            issues.append(
                f"nixpacks.toml: expected exactly 1 provider, got {len(providers)}: {providers}"
            )
        elif providers[0].lower() != "python":
            issues.append(f"nixpacks.toml: provider must be python, got {providers[0]}")
        if "node" in [p.lower() for p in providers]:
            issues.append("nixpacks.toml: Node provider present (forbidden)")

    # -- 5. Start command ----------------------------------------------------------
    if start_cmd is None:
        issues.append("nixpacks.toml: start command absent")
    elif start_cmd != LOCKED_START_CMD:
        issues.append(
            f'nixpacks.toml: start command "{start_cmd}" does not match '
            f'locked "{LOCKED_START_CMD}"'
        )

    # -- 6. .python-version --------------------------------------------------------
    pyver_path = ROOT / ".python-version"
    if pyver_path.is_file() and pyver_path.stat().st_size > 0:
        content = pyver_path.read_text(encoding="utf-8").strip()
        if content != LOCKED_PYTHON_VERSION:
            issues.append(
                f".python-version: expected {LOCKED_PYTHON_VERSION}, got {content}"
            )
    else:
        issues.append(".python-version: missing or empty")

    # -- 7. requirements.txt bound --------------------------------------------------
    req_path = ROOT / "requirements.txt"
    if req_path.is_file() and req_path.stat().st_size > 0:
        req_text = req_path.read_text(encoding="utf-8").strip()
        if not req_text:
            issues.append("requirements.txt: empty")
    else:
        issues.append("requirements.txt: missing or empty")

    # -- 8. package.json preserved --------------------------------------------------
    pkg_path = ROOT / "package.json"
    if not pkg_path.is_file():
        issues.append("package.json: missing (allowed CI/local tooling file)")

    if issues:
        return _fail(issues)

    print("PASS: Nixpacks Python-only production contract verified")
    return EXIT_CODE_OK


if __name__ == "__main__":
    sys.exit(validate())
