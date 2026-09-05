"""Nixpacks Python-only production contract tests.

Focused RED/GREEN contract tests for the Nixpacks Python provider binding
with locked Nixpkgs archive pinning.
"""

from __future__ import annotations

from contextlib import redirect_stderr
import io
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
            'nixPkgs = ["...", "libreoffice"]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            '[start]\n'
            'cmd = "python webapp/server.py"\n',
        )
        self._write(".python-version", "3.12.13\n")
        self._write("requirements.txt", "pyjwt==2.13.0\n")
        self._write("package.json", '{"name":"test"}\n')

    def _replace_setup_package_binding(self, replacement: str) -> None:
        self._replace_nixpacks_text(
            'nixPkgs = ["...", "libreoffice"]\n',
            replacement,
        )

    def _replace_nixpacks_text(self, old: str, replacement: str) -> None:
        path = self.tmp_root / "nixpacks.toml"
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content.count(old), 1)
        self._write(
            "nixpacks.toml",
            content.replace(old, replacement),
        )

    def _assert_failure_contains(self, *diagnostics: str) -> None:
        self._redirect_root()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = validator.validate()
        self.assertNotEqual(result, 0)
        output = stderr.getvalue()
        for diagnostic in diagnostics:
            self.assertIn(diagnostic, output)

    def test_pass_when_all_correct(self):
        self._write_valid_files()
        self._redirect_root()
        self.assertEqual(validator.validate(), 0)

    # -- Workbook-PDF converter fail-closed coverage -----------------------------

    def test_pass_when_workbook_pdf_converter_is_bound_in_setup(self):
        self._write_valid_files()
        self._redirect_root()
        self.assertEqual(validator.validate(), 0)

    def test_fail_missing_workbook_pdf_converter(self):
        self._write_valid_files()
        self._replace_setup_package_binding("")
        self._assert_failure_contains(
            "[phases.setup].nixPkgs must bind the required"
        )

    def test_fail_wrong_workbook_pdf_package(self):
        self._write_valid_files()
        self._replace_setup_package_binding('nixPkgs = ["...", "libreoffice-fresh"]\n')
        self._assert_failure_contains(
            "[phases.setup].nixPkgs must equal exactly ['...', 'libreoffice']"
        )

    def test_fail_alternate_nix_package_field(self):
        self._write_valid_files()
        self._replace_setup_package_binding(
            'nixPackages = ["...", "libreoffice"]\n'
        )
        self._assert_failure_contains(
            "alternate Nix package field phases.setup.nixPackages"
        )

    def test_fail_malformed_workbook_pdf_package_binding(self):
        self._write_valid_files()
        self._replace_setup_package_binding('nixPkgs = "libreoffice"\n')
        self._assert_failure_contains(
            "phases.setup.nixPkgs must be an array of package names"
        )

    def test_fail_non_string_workbook_pdf_package(self):
        self._write_valid_files()
        self._replace_setup_package_binding('nixPkgs = ["...", 42]\n')
        self._assert_failure_contains(
            "phases.setup.nixPkgs must contain only strings"
        )

    def test_fail_malformed_alternate_nix_package_field(self):
        self._write_valid_files()
        self._replace_setup_package_binding('nixPackages = "libreoffice"\n')
        self._assert_failure_contains(
            "phases.setup.nixPackages must be an array of package names"
        )

    def test_fail_malformed_apt_package_field(self):
        self._write_valid_files()
        self._replace_setup_package_binding(
            'nixPkgs = ["...", "libreoffice"]\n'
            'aptPkgs = "libreoffice"\n'
        )
        self._assert_failure_contains(
            "phases.setup.aptPkgs must be an array of package names"
        )

    def test_fail_non_string_apt_package_field(self):
        self._write_valid_files()
        self._replace_setup_package_binding(
            'nixPkgs = ["...", "libreoffice"]\n'
            "aptPkgs = [42]\n"
        )
        self._assert_failure_contains(
            "phases.setup.aptPkgs must contain only strings"
        )

    def test_fail_duplicate_workbook_pdf_package(self):
        self._write_valid_files()
        self._replace_setup_package_binding(
            'nixPkgs = ["...", "libreoffice", "libreoffice"]\n'
        )
        self._assert_failure_contains(
            "[phases.setup].nixPkgs must equal exactly ['...', 'libreoffice']"
        )

    def test_fail_reversed_workbook_pdf_package_order(self):
        self._write_valid_files()
        self._replace_setup_package_binding('nixPkgs = ["libreoffice", "..."]\n')
        self._assert_failure_contains(
            "[phases.setup].nixPkgs must equal exactly ['...', 'libreoffice']"
        )

    def test_fail_missing_workbook_pdf_package_hole(self):
        self._write_valid_files()
        self._replace_setup_package_binding('nixPkgs = ["libreoffice"]\n')
        self._assert_failure_contains(
            "[phases.setup].nixPkgs must equal exactly ['...', 'libreoffice']"
        )

    def test_fail_extra_workbook_pdf_package(self):
        self._write_valid_files()
        self._replace_setup_package_binding(
            'nixPkgs = ["...", "libreoffice", "curl"]\n'
        )
        self._assert_failure_contains(
            "[phases.setup].nixPkgs must equal exactly ['...', 'libreoffice']"
        )

    def test_fail_ambiguous_duplicate_workbook_pdf_binding(self):
        self._write_valid_files()
        self._replace_setup_package_binding(
            'nixPkgs = ["...", "libreoffice"]\n'
            'nixPackages = ["...", "libreoffice"]\n'
        )
        self._assert_failure_contains(
            "duplicate Nix package bindings are not allowed",
            "alternate Nix package field phases.setup.nixPackages",
        )

    def test_fail_misplaced_workbook_pdf_package(self):
        self._write_valid_files()
        self._write(
            "nixpacks.toml",
            (self.tmp_root / "nixpacks.toml").read_text(encoding="utf-8")
            + "[phases.build]\n"
            + 'nixPkgs = ["...", "libreoffice"]\n',
        )
        self._assert_failure_contains(
            "workbook PDF nixPkgs must be declared only at"
        )

    def test_fail_alternate_apt_workbook_pdf_package(self):
        self._write_valid_files()
        self._replace_setup_package_binding(
            'nixPkgs = ["...", "libreoffice"]\n'
            'aptPkgs = ["libreoffice"]\n'
        )
        self._assert_failure_contains(
            "alternate phases.setup.aptPkgs"
        )

    def test_fail_missing_nixpacks_toml(self):
        self._write_valid_files()
        (self.tmp_root / "nixpacks.toml").unlink()
        self._assert_failure_contains("missing required file: nixpacks.toml")

    def test_fail_empty_nixpacks_toml(self):
        self._write_valid_files()
        (self.tmp_root / "nixpacks.toml").write_text("", encoding="utf-8")
        self._assert_failure_contains("required file is empty: nixpacks.toml")

    def test_fail_provider_absent(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'providers = ["python"]\n',
            "",
        )
        self._assert_failure_contains("nixpacks.toml: providers absent")

    def test_fail_not_exactly_python_only(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'providers = ["python"]\n',
            'providers = ["node", "python"]\n',
        )
        self._assert_failure_contains(
            "providers must equal exactly ['python']",
            "Node provider present (forbidden)",
        )

    def test_fail_node_provider_present(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'providers = ["python"]\n',
            'providers = ["node"]\n',
        )
        self._assert_failure_contains(
            "providers must equal exactly ['python']",
            "Node provider present (forbidden)",
        )

    def test_fail_provider_case_variant(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'providers = ["python"]\n',
            'providers = ["Python"]\n',
        )
        self._assert_failure_contains("providers must equal exactly ['python']")

    def test_fail_wrong_python_version(self):
        self._write_valid_files()
        self._write(".python-version", "3.13.1\n")
        self._assert_failure_contains(".python-version: expected 3.12.13")

    def test_fail_missing_python_version_file(self):
        self._write_valid_files()
        (self.tmp_root / ".python-version").unlink()
        self._assert_failure_contains(".python-version: missing or empty")

    def test_fail_empty_python_version_file(self):
        self._write_valid_files()
        (self.tmp_root / ".python-version").write_text("", encoding="utf-8")
        self._assert_failure_contains(".python-version: missing or empty")

    def test_fail_wrong_start_command(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'cmd = "python webapp/server.py"\n',
            'cmd = "node server.js"\n',
        )
        self._assert_failure_contains("start command \"node server.js\"")

    def test_fail_missing_requirements_txt(self):
        self._write_valid_files()
        (self.tmp_root / "requirements.txt").unlink()
        self._assert_failure_contains("requirements.txt: missing or empty")

    def test_fail_empty_requirements_txt(self):
        self._write_valid_files()
        (self.tmp_root / "requirements.txt").write_text("", encoding="utf-8")
        self._assert_failure_contains("requirements.txt: missing or empty")

    def test_fail_dockerfile_present(self):
        self._write_valid_files()
        self._write("Dockerfile", "FROM python:3.12\n")
        self._assert_failure_contains("forbidden production file present: Dockerfile")

    def test_fail_procfile_present(self):
        self._write_valid_files()
        self._write("Procfile", "web: python app.py\n")
        self._assert_failure_contains("forbidden production file present: Procfile")

    def test_fail_wrong_provider_multi(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'providers = ["python"]\n',
            'providers = ["python", "node", "rust"]\n',
        )
        self._assert_failure_contains(
            "providers must equal exactly ['python']",
            "Node provider present (forbidden)",
        )

    # -- New archive-pinning fail-closed tests -----------------------------------

    def test_fail_missing_setup_phase(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            '[phases.setup]\n'
            'nixPkgs = ["...", "libreoffice"]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n',
            "",
        )
        self._assert_failure_contains(
            "nixpacks.toml: [phases.setup].nixpkgsArchive absent"
        )

    def test_fail_missing_nixpkgs_archive(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n',
            "",
        )
        self._assert_failure_contains(
            "nixpacks.toml: [phases.setup].nixpkgsArchive absent"
        )

    def test_fail_archive_not_40_hex(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n',
            'nixpkgsArchive = "short"\n',
        )
        self._assert_failure_contains(
            "nixpkgsArchive is not exactly 40 lowercase hex chars"
        )

    def test_fail_archive_not_hex(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n',
            'nixpkgsArchive = "gggggggggggggggggggggggggggggggggggggggg"\n',
        )
        self._assert_failure_contains(
            "nixpkgsArchive is not exactly 40 lowercase hex chars"
        )

    def test_fail_archive_branch_reference(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n',
            'nixpkgsArchive = "nixos-unstable"\n',
        )
        self._assert_failure_contains(
            "nixpkgsArchive is not exactly 40 lowercase hex chars"
        )

    def test_fail_wrong_archive_commit(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n',
            'nixpkgsArchive = "1111111111111111111111111111111111111111"\n',
        )
        self._assert_failure_contains("does not match locked archive")

    def test_fail_archive_has_uppercase(self):
        self._write_valid_files()
        self._replace_nixpacks_text(
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n',
            'nixpkgsArchive = "5C994FE2B1E540FF83AA59BA370918AD5AAE4776"\n',
        )
        self._assert_failure_contains(
            "nixpkgsArchive is not exactly 40 lowercase hex chars"
        )


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

    def test_locked_workbook_pdf_package_identity(self):
        self.assertEqual(
            validator.REQUIRED_WORKBOOK_PDF_NIXPKG,
            "libreoffice",
        )
        self.assertEqual(
            validator.EXPECTED_SETUP_NIXPKGS,
            ("...", "libreoffice"),
        )


def _toml_text(content: str) -> Path:
    """Write content to a temp file and return the path."""
    path = Path(tempfile.mktemp(suffix=".toml"))
    path.write_text(content, encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
