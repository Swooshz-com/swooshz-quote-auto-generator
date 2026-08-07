"""Nixpacks Python-only production contract tests.

Focused RED/GREEN contract tests for the Nixpacks Python provider binding
with locked Nixpkgs archive pinning.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.validate_nixpacks_python_contract as validator  # noqa: E402


class NixpacksTomlParsingTests(unittest.TestCase):
    """Unit tests for the nixpacks.toml inline parser."""

    def test_parses_python_only_provider(self):
        result = validator._parse_nixpacks_toml(
            _toml_text(
                'providers = ["python"]\n'
                '[phases.setup]\nnixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
                '[start]\ncmd = "python webapp/server.py"'
            )
        )
        self.assertEqual(
            result,
            (["python"], "python webapp/server.py", "5c994fe2b1e540ff83aa59ba370918ad5aae4776"),
        )

    def test_parses_no_start_section(self):
        result = validator._parse_nixpacks_toml(
            _toml_text('providers = ["python"]')
        )
        self.assertEqual(result, (["python"], None, None))

    def test_parses_no_providers(self):
        result = validator._parse_nixpacks_toml(
            _toml_text('[start]\ncmd = "python webapp/server.py"')
        )
        self.assertEqual(result, (None, "python webapp/server.py", None))

    def test_parses_empty(self):
        result = validator._parse_nixpacks_toml(_toml_text(""))
        self.assertEqual(result, (None, None, None))

    def test_parses_comment_lines(self):
        result = validator._parse_nixpacks_toml(
            _toml_text(
                '# comment\n'
                'providers = ["python"]\n'
                '# another\n'
                '[phases.setup]\n'
                'nixpkgsArchive = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
                '[start]\ncmd = "echo hi"'
            )
        )
        self.assertEqual(
            result,
            (["python"], "echo hi", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        )

    def test_parses_provider_with_spaces(self):
        result = validator._parse_nixpacks_toml(
            _toml_text(
                'providers = [ "python" ]\n'
                '[start]\ncmd = "python webapp/server.py"'
            )
        )
        self.assertEqual(result, (["python"], "python webapp/server.py", None))

    def test_detects_node_provider(self):
        result = validator._parse_nixpacks_toml(
            _toml_text(
                'providers = ["node", "python"]\n'
                '[start]\ncmd = "python webapp/server.py"'
            )
        )
        self.assertEqual(result, (["node", "python"], "python webapp/server.py", None))

    def test_detects_node_only(self):
        result = validator._parse_nixpacks_toml(
            _toml_text('providers = ["node"]')
        )
        self.assertEqual(result, (["node"], None, None))


class NixpacksPythonContractREDTests(unittest.TestCase):
    """Tests that the validator fails closed for each defect category."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp_root = Path(self._td.name)
        self._orig_root = validator.ROOT

    def tearDown(self):
        validator.ROOT = self._orig_root

    def _redirect_root(self):
        validator.ROOT = self.tmp_root

    def _write(self, rel: str, content: str) -> None:
        p = self.tmp_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _write_valid_files(self):
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            '[start]\n'
            'cmd = "python webapp/server.py"\n',
        )
        self._write(".python-version", "3.12.13\n")
        self._write("requirements.txt", "pyjwt==2.13.0\n")
        self._write("package.json", '{"name":"test"}\n')

    def test_pass_when_all_correct(self):
        self._write_valid_files()
        self._redirect_root()
        self.assertEqual(validator.validate(), 0)

    def test_fail_missing_nixpacks_toml(self):
        self._write_valid_files()
        (self.tmp_root / "nixpacks.toml").unlink()
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_empty_nixpacks_toml(self):
        self._write_valid_files()
        (self.tmp_root / "nixpacks.toml").write_text("", encoding="utf-8")
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_provider_absent(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_not_exactly_python_only(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["node", "python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_node_provider_present(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["node"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_wrong_python_version(self):
        self._write_valid_files()
        self._write(".python-version", "3.13.1\n")
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_missing_python_version_file(self):
        self._write_valid_files()
        (self.tmp_root / ".python-version").unlink()
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_empty_python_version_file(self):
        self._write_valid_files()
        (self.tmp_root / ".python-version").write_text("", encoding="utf-8")
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_wrong_start_command(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            '[start]\ncmd = "node server.js"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_missing_requirements_txt(self):
        self._write_valid_files()
        (self.tmp_root / "requirements.txt").unlink()
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_empty_requirements_txt(self):
        self._write_valid_files()
        (self.tmp_root / "requirements.txt").write_text("", encoding="utf-8")
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_dockerfile_present(self):
        self._write_valid_files()
        self._write("Dockerfile", "FROM python:3.12\n")
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_procfile_present(self):
        self._write_valid_files()
        self._write("Procfile", "web: python app.py\n")
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_wrong_provider_multi(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python", "node", "rust"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    # -- New archive-pinning fail-closed tests -----------------------------------

    def test_fail_missing_setup_phase(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_missing_nixpkgs_archive(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[phases.setup]\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_archive_not_40_hex(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "short"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_archive_not_hex(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "gggggggggggggggggggggggggggggggggggggggg"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_archive_branch_reference(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "nixos-unstable"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_wrong_archive_commit(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "1111111111111111111111111111111111111111"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)

    def test_fail_archive_has_uppercase(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5C994FE2B1E540FF83AA59BA370918AD5AAE4776"\n'
            '[start]\ncmd = "python webapp/server.py"\n',
        )
        self._redirect_root()
        self.assertNotEqual(validator.validate(), 0)


class NixpacksArchiveProofTests(unittest.TestCase):
    """Tests that the locked archive is a well-formed, immutable Nixpkgs commit."""

    def test_locked_archive_is_40_hex(self):
        self.assertIsNotNone(
            validator.HEX_ARCHIVE_RE.fullmatch(validator.LOCKED_NIXPKGS_ARCHIVE)
        )

    def test_locked_archive_matches_validator_constant(self):
        self.assertEqual(
            validator.LOCKED_NIXPKGS_ARCHIVE,
            "5c994fe2b1e540ff83aa59ba370918ad5aae4776",
        )


def _toml_text(content: str) -> Path:
    """Write content to a temp file and return the path."""
    path = Path(tempfile.mktemp(suffix=".toml"))
    path.write_text(content, encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
