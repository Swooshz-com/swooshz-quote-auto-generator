"""Build script tests: syntax, dry run, archive and a full build against a fake docker."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_internal_image.sh"
BASH = shutil.which("bash")
GIT = shutil.which("git")

FAKE_DOCKER = r"""#!/usr/bin/env bash
# Fake docker for tests. Records calls and answers like the real image would.
echo "$*" >> "$FAKE_DOCKER_LOG"
if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
  case "$4" in
    *Config.Env*) printf 'PATH=/root/.nix-profile/bin\nNIXPACKS_PATH=/app\n' ;;
    *) echo "sha256:$(printf 'a%.0s' $(seq 1 64)) 2026-11-18T02:45:52Z 7538179097" ;;
  esac
  exit 0
fi
if [ "$1" = "run" ]; then
  for arg in "$@"; do
    if [ "$arg" = "nixpacks" ]; then echo "Successfully Built!"; exit 0; fi
  done
  cmd="${@: -1}"
  case "$cmd" in
    "python --version") echo "Python ${FAKE_PYTHON:-3.12.13}" ;;
    *"import server"*) echo "import-ok" ;;
    "command -v soffice") echo "/root/.nix-profile/bin/soffice" ;;
    "soffice --version"*) echo "LibreOffice 25.2.6.2" ;;
    "command -v fc-match") echo "/root/.nix-profile/bin/fc-match" ;;
    *"'Calibri:style=Bold'"*) echo "${FAKE_BOLD:-Carlito / Bold}" ;;
    *"'Calibri'"*) echo "Carlito / Regular" ;;
    *"'Arial:style=Bold'"*) echo "Liberation Sans / Bold" ;;
    *"'Arial'"*) echo "Liberation Sans / Regular" ;;
    *"tr -cd"*) echo "0" ;;
    *) echo "unexpected command" >&2; exit 9 ;;
  esac
  exit 0
fi
exit 9
"""


@unittest.skipUnless(BASH and GIT, "bash and git are required")
class BuildInternalImageTests(unittest.TestCase):
    def run_script(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [BASH, str(SCRIPT), *args], cwd=ROOT, env=env or os.environ.copy(), capture_output=True, text=True, timeout=120
        )

    def head(self) -> str:
        return subprocess.run([GIT, "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()

    def test_syntax(self) -> None:
        result = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_has_lf_line_endings(self) -> None:
        self.assertNotIn(b"\r", SCRIPT.read_bytes())

    def test_dry_run_prints_plan_without_docker(self) -> None:
        env = os.environ.copy()
        env["PATH"] = "/nonexistent-bin:" + env.get("PATH", "")
        result = self.run_script(
            "build", "--dry-run", "--commit", "a" * 40, "--tar", "/root/x.tar", "--tar-sha256", "b" * 64, env=env
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--entrypoint nixpacks docker.io/coollabsio/coolify-helper:1.0.19 build", result.stdout)
        self.assertIn("--name sqag-internal:" + "a" * 40, result.stdout)
        self.assertIn("--network none", result.stdout)

    def test_bad_arguments_are_refused(self) -> None:
        for args in (
            ("build", "--dry-run", "--commit", "abc1234", "--tar", "x", "--tar-sha256", "b" * 64),
            ("build", "--dry-run", "--commit", "a" * 40, "--tar", "x", "--tar-sha256", "nothex"),
            ("build", "--dry-run", "--commit", "a" * 40, "--tar", "x", "--tar-sha256", "b" * 64, "--workdir", "/root"),
            ("nonsense",),
        ):
            self.assertNotEqual(self.run_script(*args).returncode, 0, args)

    def make_archive(self, folder: Path) -> tuple[str, Path, str]:
        commit = self.head()
        result = self.run_script("archive", "--commit", commit, "--out", str(folder))
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout.strip().splitlines()[-1])
        tar = Path(record["tar"])
        self.assertEqual(record["tar_sha256"], hashlib.sha256(tar.read_bytes()).hexdigest())
        self.assertEqual(record["source_commit"], commit)
        self.assertGreater(record["tar_file_count"], 50)
        return commit, tar, record["tar_sha256"]

    def fake_env(self, folder: Path, **extra: str) -> dict[str, str]:
        bin_dir = folder / "bin"
        bin_dir.mkdir(exist_ok=True)
        docker = bin_dir / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["FAKE_DOCKER_LOG"] = str(folder / "docker.log")
        env.update(extra)
        return env

    def test_archive_then_build_with_fake_docker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            commit, tar, digest = self.make_archive(folder / "out")
            env = self.fake_env(folder)
            result = self.run_script(
                "build", "--commit", commit, "--tar", str(tar), "--tar-sha256", digest,
                "--workdir", str(folder / "work"), env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(record["result"], "pass")
            self.assertEqual(record["failures"], [])
            self.assertEqual(record["image_tag"], f"sqag-internal:{commit}")
            self.assertTrue(record["image_id"].startswith("sha256:"))
            self.assertEqual(record["image_env_names"], ["PATH", "NIXPACKS_PATH"])
            self.assertEqual(len(record["fonts"]), 4)
            calls = (folder / "docker.log").read_text(encoding="utf-8")
            self.assertIn("--network none", calls)

    def test_font_or_python_mismatch_fails_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            commit, tar, digest = self.make_archive(folder / "out")
            env = self.fake_env(folder, FAKE_BOLD="DejaVu Sans / Book", FAKE_PYTHON="3.11.0")
            result = self.run_script(
                "build", "--commit", commit, "--tar", str(tar), "--tar-sha256", digest,
                "--workdir", str(folder / "work"), env=env,
            )
            self.assertNotEqual(result.returncode, 0)
            record = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertEqual(record["result"], "fail")
            self.assertTrue(any("Calibri:style=Bold" in item for item in record["failures"]))
            self.assertTrue(any("python version" in item for item in record["failures"]))

    def test_wrong_tar_digest_or_commit_is_refused_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            commit, tar, digest = self.make_archive(folder / "out")
            env = self.fake_env(folder)
            wrong_digest = self.run_script(
                "build", "--commit", commit, "--tar", str(tar), "--tar-sha256", "0" * 64,
                "--workdir", str(folder / "w1"), env=env,
            )
            self.assertNotEqual(wrong_digest.returncode, 0)
            wrong_commit = self.run_script(
                "build", "--commit", "f" * 40, "--tar", str(tar), "--tar-sha256", digest,
                "--workdir", str(folder / "w2"), env=env,
            )
            self.assertNotEqual(wrong_commit.returncode, 0)
            self.assertIn("header commit", wrong_commit.stderr)
            self.assertFalse((folder / "docker.log").exists())
            self.assertFalse((folder / "w1").exists())


if __name__ == "__main__":
    unittest.main()
