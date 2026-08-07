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


class NixpacksTomlClosedSchemaParseTests(unittest.TestCase):
    """Unit tests for the full tomllib parse plus exact closed-schema validation."""

    def test_parses_exact_closed_contract(self):
        result = validator.parse_nixpacks_toml(
            _toml_text(
                'providers = ["python"]\n'
                '[phases.setup]\nnixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
                '[start]\ncmd = "python webapp/server.py"'
            )
        )
        self.assertEqual(
            result,
            {
                "providers": ["python"],
                "phases": {"setup": {"nixpkgsArchive": "5c994fe2b1e540ff83aa59ba370918ad5aae4776"}},
                "start": {"cmd": "python webapp/server.py"},
            },
        )
        issues: list[str] = []
        validator._validate_closed_schema(result, issues)
        self.assertEqual(issues, [])

    def test_parse_rejects_invalid_toml(self):
        with self.assertRaises(validator.TOMLDecodeError):
            validator.parse_nixpacks_toml(
                _toml_text(
                    'providers = ["python"]\nthis is not toml\n'
                    '[phases.setup]\nnixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
                    '[start]\ncmd = "python webapp/server.py"'
                )
            )

    def test_parse_rejects_duplicate_key(self):
        with self.assertRaises(validator.TOMLDecodeError):
            validator.parse_nixpacks_toml(
                _toml_text('providers = ["python"]\nproviders = ["python"]')
            )

    def test_parse_rejects_duplicate_table(self):
        with self.assertRaises(validator.TOMLDecodeError):
            validator.parse_nixpacks_toml(
                _toml_text(
                    '[phases.setup]\nnixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
                    "[phases.setup]"
                )
            )

    def test_parse_dotted_shadowing_is_rejected_by_closed_schema(self):
        result = validator.parse_nixpacks_toml(
            _toml_text(
                'providers = ["python"]\n'
                '[phases.setup]\nnixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
                'phases.setup.nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
                '[start]\ncmd = "python webapp/server.py"'
            )
        )
        issues: list[str] = []
        validator._validate_closed_schema(result, issues)
        self.assertTrue(
            any("unknown phases.setup keys" in issue for issue in issues),
            f"dotted shadowing not detected: {issues!r}",
        )

    def test_closed_schema_rejects_unknown_top_level_key(self):
        issues: list[str] = []
        validator._validate_closed_schema(
            {
                "providers": ["python"],
                "phases": {"setup": {"nixpkgsArchive": validator.LOCKED_NIXPKGS_ARCHIVE}},
                "start": {"cmd": "python webapp/server.py"},
                "processes": {"web": "python webapp/server.py"},
            },
            issues,
        )
        self.assertTrue(any("unknown top-level keys" in issue for issue in issues))

    def test_closed_schema_rejects_unknown_nested_key(self):
        issues: list[str] = []
        validator._validate_closed_schema(
            {
                "providers": ["python"],
                "phases": {
                    "setup": {
                        "nixpkgsArchive": validator.LOCKED_NIXPKGS_ARCHIVE,
                        "install": "apt-get install nodejs",
                    }
                },
                "start": {"cmd": "python webapp/server.py"},
            },
            issues,
        )
        self.assertTrue(any("unknown phases.setup keys" in issue for issue in issues))

    def test_closed_schema_rejects_alternate_phase(self):
        issues: list[str] = []
        validator._validate_closed_schema(
            {
                "providers": ["python"],
                "phases": {
                    "setup": {"nixpkgsArchive": validator.LOCKED_NIXPKGS_ARCHIVE},
                    "build": {"nixpkgsArchive": "1" * 40},
                },
                "start": {"cmd": "python webapp/server.py"},
            },
            issues,
        )
        self.assertTrue(any("unknown phases keys" in issue for issue in issues))

    def test_closed_schema_rejects_missing_top_level_key(self):
        issues: list[str] = []
        validator._validate_closed_schema(
            {
                "providers": ["python"],
                "phases": {"setup": {"nixpkgsArchive": validator.LOCKED_NIXPKGS_ARCHIVE}},
            },
            issues,
        )
        self.assertTrue(any("missing top-level keys" in issue for issue in issues))

    def test_closed_schema_rejects_misplaced_archive(self):
        issues: list[str] = []
        validator._validate_closed_schema(
            {
                "providers": ["python"],
                "nixpkgsArchive": validator.LOCKED_NIXPKGS_ARCHIVE,
                "phases": {"setup": {}},
                "start": {"cmd": "python webapp/server.py"},
            },
            issues,
        )
        self.assertTrue(any("unknown top-level keys" in issue for issue in issues))

    def test_closed_schema_rejects_wrong_value_types(self):
        for label, document in (
            ("providers_string", {"providers": "python", "phases": {"setup": {"nixpkgsArchive": validator.LOCKED_NIXPKGS_ARCHIVE}}, "start": {"cmd": "python webapp/server.py"}}),
            ("archive_integer", {"providers": ["python"], "phases": {"setup": {"nixpkgsArchive": 42}}, "start": {"cmd": "python webapp/server.py"}}),
            ("start_cmd_array", {"providers": ["python"], "phases": {"setup": {"nixpkgsArchive": validator.LOCKED_NIXPKGS_ARCHIVE}}, "start": {"cmd": ["python", "webapp/server.py"]}}),
            ("archive_uppercase", {"providers": ["python"], "phases": {"setup": {"nixpkgsArchive": "5C994FE2B1E540FF83AA59BA370918AD5AAE4776"}}, "start": {"cmd": "python webapp/server.py"}}),
            ("archive_branch", {"providers": ["python"], "phases": {"setup": {"nixpkgsArchive": "nixos-unstable"}}, "start": {"cmd": "python webapp/server.py"}}),
            ("archive_short", {"providers": ["python"], "phases": {"setup": {"nixpkgsArchive": "short"}}, "start": {"cmd": "python webapp/server.py"}}),
            ("wrong_archive", {"providers": ["python"], "phases": {"setup": {"nixpkgsArchive": "1" * 40}}, "start": {"cmd": "python webapp/server.py"}}),
        ):
            with self.subTest(label=label):
                issues: list[str] = []
                validator._validate_closed_schema(document, issues)
                self.assertTrue(issues, f"{label} unexpectedly accepted")


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


class NixpacksClosedSchemaREDTests(unittest.TestCase):
    """RED regressions for the closed Nixpacks TOML contract.

    These cases demonstrate that a permissive line scanner accepts content that
    a real TOML consumer rejects or that expands the locked production build
    contract. Each must fail closed after the full tomllib parse plus
    closed-schema validation is implemented.
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp_root = Path(self._td.name)
        self._orig_root = validator.ROOT

    def tearDown(self):
        validator.ROOT = self._orig_root

    def _write(self, rel: str, content: str) -> None:
        p = self.tmp_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _write_support_files(self):
        self._write(".python-version", "3.12.13\n")
        self._write("requirements.txt", "pyjwt==2.13.0\n")
        self._write("package.json", '{"name":"test"}\n')

    def _write_nixpacks(self, content: str) -> None:
        self._write_support_files()
        self._write("nixpacks.toml", content)
        validator.ROOT = self.tmp_root

    def _assert_validator_fails(self, content: str, label: str) -> None:
        self._write_nixpacks(content)
        result = validator.validate()
        self.assertNotEqual(result, 0, f"{label} unexpectedly passed")

    def test_invalid_toml_that_scanner_accepts_fails_closed(self):
        self._assert_validator_fails(
            'providers = ["python"]\n'
            "this is not toml\n"
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            '[start]\n'
            'cmd = "python webapp/server.py"\n',
            "invalid_toml_syntax",
        )

    def test_unknown_top_level_toml_content_fails_closed(self):
        self._assert_validator_fails(
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            "[processes]\n"
            'web = "python webapp/server.py"\n'
            "[start]\n"
            'cmd = "python webapp/server.py"\n',
            "unknown_top_level_table",
        )

    def test_unknown_nested_toml_key_expands_build_contract(self):
        self._assert_validator_fails(
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            'install = "apt-get install nodejs"\n'
            "[start]\n"
            'cmd = "python webapp/server.py"\n',
            "unknown_nested_key",
        )

    def test_duplicate_toml_key_fails_closed(self):
        self._assert_validator_fails(
            'providers = ["python"]\n'
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            "[start]\n"
            'cmd = "python webapp/server.py"\n',
            "duplicate_key",
        )

    def test_duplicate_toml_table_fails_closed(self):
        self._assert_validator_fails(
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            "[phases.setup]\n"
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            "[start]\n"
            'cmd = "python webapp/server.py"\n',
            "duplicate_table",
        )

    def test_misplaced_archive_configuration_fails_closed(self):
        self._assert_validator_fails(
            'providers = ["python"]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            "[start]\n"
            'cmd = "python webapp/server.py"\n',
            "misplaced_archive",
        )

    def test_alternate_shadowing_build_phase_fails_closed(self):
        self._assert_validator_fails(
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            "[phases.build]\n"
            'nixpkgsArchive = "1111111111111111111111111111111111111111"\n'
            "[start]\n"
            'cmd = "python webapp/server.py"\n',
            "alternate_phase",
        )

    def test_dotted_key_shadowing_of_section_fails_closed(self):
        self._assert_validator_fails(
            'providers = ["python"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            'phases.setup.nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            "[start]\n"
            'cmd = "python webapp/server.py"\n',
            "dotted_shadowing",
        )

    def test_extra_provider_fails_closed(self):
        self._assert_validator_fails(
            'providers = ["python", "node"]\n'
            '[phases.setup]\n'
            'nixpkgsArchive = "5c994fe2b1e540ff83aa59ba370918ad5aae4776"\n'
            "[start]\n"
            'cmd = "python webapp/server.py"\n',
            "extra_provider",
        )


class NixpacksCIGateTests(unittest.TestCase):
    """RED regression: Validate app must explicitly gate on the Nixpacks job."""

    def test_validate_app_gate_requires_nixpacks_contract_success(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        validate_section = workflow.split("name: Validate app", 1)[1]
        self.assertIn(
            "needs.nixpacks-contract.result == 'success'",
            validate_section.split("steps:", 1)[0],
        )
        self.assertIn("always()", validate_section.split("steps:", 1)[0])
        self.assertIn(
            "needs.retrospective_exact_starting_head_red.result == 'success'",
            validate_section.split("steps:", 1)[0],
        )


def _toml_text(content: str) -> Path:
    """Write content to a temp file and return the path."""
    path = Path(tempfile.mktemp(suffix=".toml"))
    path.write_text(content, encoding="utf-8")
    return path


if __name__ == "__main__":
    unittest.main()
