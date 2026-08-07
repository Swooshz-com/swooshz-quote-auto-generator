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

# Immutable NixOS/nixpkgs commit providing python312 == 3.12.13 on x86_64-linux.
# Commit message: "python312: 3.12.12 -> 3.12.13"
# Verified via: https://github.com/NixOS/nixpkgs/commit/5c994fe2b1e540ff83aa59ba370918ad5aae4776
LOCKED_NIXPKGS_ARCHIVE = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"

HEX_ARCHIVE_RE = re.compile(r"^[0-9a-f]{40}$")

EXIT_CODE_OK = 0
EXIT_CODE_SINGLE_ISSUE = 1
EXIT_CODE_MULTI_ISSUE = 2


def _fail(issues: list[str]) -> int:
    for msg in issues:
        print(f"FAIL: {msg}", file=sys.stderr)
    if len(issues) == 1:
        return EXIT_CODE_SINGLE_ISSUE
    return EXIT_CODE_MULTI_ISSUE


def _parse_nixpacks_toml(path: Path) -> tuple[list[str] | None, str | None, str | None]:
    """Return (providers, start_cmd, nixpkgs_archive) or raise on parse failure."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None, None, None

    providers = None
    start_cmd = None
    nixpkgs_archive = None

    current_section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        sec_match = re.match(r"^\[(.+)\]$", line)
        if sec_match:
            current_section = sec_match.group(1)
            continue

        if current_section == "phases.setup":
            m = re.match(r"^nixpkgsArchive\s*=\s*\"(.+)\"$", line)
            if m:
                nixpkgs_archive = m.group(1)
            continue

        if current_section == "start":
            m = re.match(r'^cmd\s*=\s*"(.+)"$', line)
            if m:
                start_cmd = m.group(1)
            continue

        m = re.match(r"^providers\s*=\s*\[(.+)\]$", line)
        if m:
            providers = [p.strip().strip('"').strip("'") for p in m.group(1).split(",")]
            continue

    return providers, start_cmd, nixpkgs_archive


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
            providers, start_cmd, nixpkgs_archive = _parse_nixpacks_toml(nixpacks_path)
        except Exception as exc:
            providers, start_cmd, nixpkgs_archive = None, None, None
            issues.append(f"nixpacks.toml unparseable: {exc}")
    else:
        providers, start_cmd, nixpkgs_archive = None, None, None

    # -- 4. [phases.setup] section ---------------------------------------------------
    if nixpkgs_archive is None:
        issues.append("nixpacks.toml: [phases.setup].nixpkgsArchive absent")
    else:
        if not HEX_ARCHIVE_RE.fullmatch(nixpkgs_archive):
            issues.append(
                "nixpacks.toml: nixpkgsArchive is not exactly 40 lowercase hex chars"
            )
        elif nixpkgs_archive != LOCKED_NIXPKGS_ARCHIVE:
            issues.append(
                f"nixpacks.toml: nixpkgsArchive does not match locked archive "
                f"{LOCKED_NIXPKGS_ARCHIVE}"
            )

    # -- 5. Providers --------------------------------------------------------------
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

    # -- 6. Start command ----------------------------------------------------------
    if start_cmd is None:
        issues.append("nixpacks.toml: start command absent")
    elif start_cmd != LOCKED_START_CMD:
        issues.append(
            f'nixpacks.toml: start command "{start_cmd}" does not match '
            f'locked "{LOCKED_START_CMD}"'
        )

    # -- 7. .python-version --------------------------------------------------------
    pyver_path = ROOT / ".python-version"
    if pyver_path.is_file() and pyver_path.stat().st_size > 0:
        content = pyver_path.read_text(encoding="utf-8").strip()
        if content != LOCKED_PYTHON_VERSION:
            issues.append(
                f".python-version: expected {LOCKED_PYTHON_VERSION}, got {content}"
            )
    else:
        issues.append(".python-version: missing or empty")

    # -- 8. requirements.txt bound --------------------------------------------------
    req_path = ROOT / "requirements.txt"
    if req_path.is_file() and req_path.stat().st_size > 0:
        req_text = req_path.read_text(encoding="utf-8").strip()
        if not req_text:
            issues.append("requirements.txt: empty")
    else:
        issues.append("requirements.txt: missing or empty")

    # -- 9. package.json preserved --------------------------------------------------
    pkg_path = ROOT / "package.json"
    if not pkg_path.is_file():
        issues.append("package.json: missing (allowed CI/local tooling file)")

    if issues:
        return _fail(issues)

    print("PASS: Nixpacks Python-only production contract verified")
    return EXIT_CODE_OK


if __name__ == "__main__":
    sys.exit(validate())
