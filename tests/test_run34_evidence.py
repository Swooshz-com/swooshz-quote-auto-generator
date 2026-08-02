"""Focused integrity and result-contract tests for the Run-34 fixture."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import reproduce_run34_red as red


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = red.FIXTURE_ROOT


def _copy_fixture(parent: Path) -> Path:
    target = parent / "candidate" / "tests" / "fixtures" / "retrospective" / "run34"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE_ROOT, target)
    return target


def _completed(output: str, *, returncode: int = 1) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        ["fixture-test"],
        returncode,
        output.encode("utf-8"),
        b"",
    )


def _directory_factory(path: Path):
    def create(**_: object) -> str:
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    return create


class RetrospectiveFixtureIntegrityTest(unittest.TestCase):
    def test_manifest_binds_provenance_selection_and_exact_result(self) -> None:
        manifest = red._validate_fixture()
        self.assertEqual(manifest["fixture_version"], "1.0.0")
        self.assertTrue(manifest["provenance"]["retrospective"])
        self.assertFalse(manifest["provenance"]["original_red_chronology"])
        self.assertEqual(len(manifest["test_selection"]), 13)
        self.assertEqual(manifest["expected_result"], red.EXPECTED_RESULT)
        self.assertEqual(manifest["dependency_definition"]["path"], "dependencies.json")

    def test_every_payload_digest_and_dependency_digest_are_bound(self) -> None:
        manifest = red._validate_fixture()
        dependency_path = manifest["dependency_definition"]["path"]
        dependency_entry = next(item for item in manifest["payload"] if item["path"] == dependency_path)
        self.assertEqual(dependency_entry["sha256"], manifest["dependency_definition"]["sha256"])
        for entry in manifest["payload"]:
            path = FIXTURE_ROOT / Path(*entry["path"].split("/"))
            self.assertEqual(path.stat().st_size, entry["size"])

    def test_mutating_any_preserved_input_breaks_its_digest(self) -> None:
        manifest = red._validate_fixture()
        for entry in manifest["payload"]:
            with self.subTest(path=entry["path"]), tempfile.TemporaryDirectory(prefix="sqag-run34-digest-") as temporary:
                package = _copy_fixture(Path(temporary))
                path = package / Path(*entry["path"].split("/"))
                path.write_bytes(path.read_bytes() + b"mutation")
                with self.assertRaises(red.FixtureError) as failure:
                    red._validate_fixture(package)
                self.assertEqual(failure.exception.code, "fixture_file_digest_mismatch")

    def test_exact_historical_patch_bytes_and_targets_are_preserved(self) -> None:
        manifest = red._validate_fixture()
        patch = manifest["historical_test_change"]
        path = FIXTURE_ROOT / Path(*patch["path"].split("/"))
        encoded = path.read_bytes().strip()
        decoded = base64.b64decode(encoded, validate=True)
        self.assertEqual(hashlib.sha256(decoded).hexdigest(), patch["decoded_sha256"])
        self.assertEqual(patch["changed_files"], ["tests/test_runtime_privilege_contract.py"])
        self.assertEqual(patch["implementation_files_applied"], 0)

    def test_no_historical_git_lookup_or_current_dependency_install_is_possible(self) -> None:
        source = Path(red.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        subprocess_calls: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"Popen", "run", "check_call", "check_output"}:
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    subprocess_calls.append(argument.value)
        self.assertFalse(any(value.lower() in {"git", "cat-file", "worktree"} for value in subprocess_calls))
        self.assertNotIn("requirements.txt", source)
        self.assertNotIn("pip install", source)
        self.assertNotIn("source_branch", red._validate_fixture()["provenance"])
        self.assertNotIn("source_tag", red._validate_fixture()["provenance"])

    def test_fixture_runs_without_git_history_or_source_branch_and_ignores_current_requirements(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-fixture-test-") as temporary:
            parent = Path(temporary)
            package = _copy_fixture(parent)
            candidate_root = package.parents[3]
            (candidate_root / "requirements.txt").write_text("not-a-real-package==999.0.0\n", encoding="utf-8")
            (candidate_root / ".git").mkdir()
            (candidate_root / ".git" / "shallow").write_text("candidate-only\n", encoding="utf-8")
            receipt = red.run_reproduction(package)
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["assertion_failures"], 13)
        self.assertFalse(receipt["current_dependencies_used"])
        self.assertTrue(receipt["cleanup_verified"])

    def test_fixture_runs_after_source_branch_deletion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-source-deletion-") as temporary:
            parent = Path(temporary)
            package = _copy_fixture(parent)
            candidate_root = package.parents[3]
            (candidate_root / ".git" / "refs" / "heads").mkdir(parents=True)
            receipt = red.run_reproduction(package)
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(receipt["test_count"], 13)

    def test_missing_fixture_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-missing-") as temporary:
            package = _copy_fixture(Path(temporary))
            (package / "fixture-explanation.md").unlink()
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
        self.assertIn(failure.exception.code, {"fixture_file_set_mismatch", "fixture_file_missing"})

    def test_extra_fixture_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-extra-") as temporary:
            package = _copy_fixture(Path(temporary))
            (package / "unexpected.txt").write_text("extra\n", encoding="utf-8")
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
        self.assertEqual(failure.exception.code, "fixture_file_set_mismatch")

    def test_mutated_fixture_content_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-mutated-") as temporary:
            package = _copy_fixture(Path(temporary))
            path = package / "fixture-explanation.md"
            path.write_bytes(path.read_bytes() + b"mutation")
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
        self.assertEqual(failure.exception.code, "fixture_file_digest_mismatch")

    def test_mutated_dependency_version_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-dependency-") as temporary:
            package = _copy_fixture(Path(temporary))
            dependency = json.loads((package / "dependencies.json").read_text(encoding="utf-8"))
            dependency["snapshot_version"] = "1.0.1"
            (package / "dependencies.json").write_text(
                json.dumps(dependency, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
        self.assertEqual(failure.exception.code, "fixture_file_digest_mismatch")

    def test_mutated_manifest_digest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-manifest-") as temporary:
            package = _copy_fixture(Path(temporary))
            (package / "manifest.json").write_bytes((package / "manifest.json").read_bytes() + b"\n")
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
        self.assertEqual(failure.exception.code, "manifest_digest_mismatch")

    def test_success_receipt_is_bounded_and_contains_no_child_output(self) -> None:
        receipt = red.run_reproduction()
        self.assertEqual(tuple(receipt), red.RECEIPT_KEYS)
        self.assertFalse(receipt["child_output_emitted"])
        self.assertNotIn("AssertionError", json.dumps(receipt, sort_keys=True))
        self.assertNotIn("protected_role_edge_forbidden", json.dumps(receipt, sort_keys=True))

    def test_workflow_uses_exact_head_fixture_gate_and_read_only_triggers(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        retrospective = workflow.split("  secret-scan:", 1)[0]
        self.assertIn("pull_request:", workflow)
        self.assertIn("branches:\n      - main", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertIn("ref: ${{ github.event.pull_request.head.sha || github.sha }}", retrospective)
        self.assertIn("fetch-depth: 1", retrospective)
        self.assertIn("python-version: \"3.12.13\"", retrospective)
        self.assertNotIn("fa03eca2", retrospective)
        self.assertNotIn("requirements.txt", retrospective)
        self.assertIn("needs:\n      - retrospective_exact_starting_head_red", workflow)
        self.assertIn("needs.retrospective_exact_starting_head_red.result == 'success'", workflow)

    def test_cleanup_occurs_on_success_and_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-cleanup-") as temporary:
            success_path = Path(temporary) / "success"
            receipt = red.run_reproduction(
                temp_directory_factory=_directory_factory(success_path),
            )
            self.assertTrue(receipt["cleanup_verified"])
            self.assertFalse(success_path.exists())

            failure_path = Path(temporary) / "failure"
            with mock.patch.object(red, "_execute_selected_tests", side_effect=red.FixtureError("synthetic_failure")):
                with self.assertRaises(red.FixtureError):
                    red.run_reproduction(temp_directory_factory=_directory_factory(failure_path))
            self.assertFalse(failure_path.exists())


class RetrospectiveResultContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = red._validate_fixture()

    def _valid_output(self, *, statuses: dict[str, str] | None = None, count: int = 13, summary: str = "failures=13") -> str:
        statuses = statuses or {name: "FAIL" for name in red.EXPECTED_TEST_SELECTION}
        lines = []
        for name, status in statuses.items():
            short = name.rsplit(".", 1)[1]
            lines.append(f"{short} ({name}) ... {status}")
        lines.append("")
        lines.append(f"Ran {count} tests in 0.001s")
        lines.append(f"FAILED ({summary})")
        lines.extend(item["category"] for item in self.manifest["test_selection"])
        return "\n".join(lines) + "\n"

    def test_exactly_13_assertion_failures_and_zero_errors_are_accepted(self) -> None:
        result = _completed(self._valid_output())
        red._validate_test_result(result, self.manifest)

    def test_12_or_14_failures_are_rejected(self) -> None:
        twelve = dict(list((name, "FAIL") for name in red.EXPECTED_TEST_SELECTION)[:12])
        with self.assertRaises(red.FixtureError):
            red._validate_test_result(_completed(self._valid_output(statuses=twelve, count=12, summary="failures=12")), self.manifest)
        fourteen = {name: "FAIL" for name in red.EXPECTED_TEST_SELECTION}
        fourteen["tests.extra.unexpected"] = "FAIL"
        with self.assertRaises(red.FixtureError):
            red._validate_test_result(_completed(self._valid_output(statuses=fourteen, count=14, summary="failures=14")), self.manifest)

    def test_import_or_collection_error_is_rejected(self) -> None:
        output = "tests (unittest.loader._FailedTest.tests) ... ERROR\nImportError: collection failed\nRan 13 tests in 0.001s\nFAILED (errors=1)\n"
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_test_result(_completed(output), self.manifest)
        self.assertEqual(failure.exception.code, "import_or_collection_error")

    def test_unexpected_pass_is_rejected(self) -> None:
        first = red.EXPECTED_TEST_SELECTION[0]
        output = self._valid_output(statuses={**{name: "FAIL" for name in red.EXPECTED_TEST_SELECTION}, first: "ok"})
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_test_result(_completed(output), self.manifest)
        self.assertEqual(failure.exception.code, "unexpected_test_status")

    def test_skip_is_rejected(self) -> None:
        first = red.EXPECTED_TEST_SELECTION[0]
        output = self._valid_output(
            statuses={**{name: "FAIL" for name in red.EXPECTED_TEST_SELECTION}, first: "skipped 'not required'"},
            summary="failures=12, skipped=1",
        )
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_test_result(_completed(output), self.manifest)
        self.assertEqual(failure.exception.code, "failure_summary_mismatch")

    def test_output_overflow_is_rejected(self) -> None:
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_output_size(b"x" * (red.MAX_OUTPUT_BYTES + 1), b"")
        self.assertEqual(failure.exception.code, "output_overflow")

    def test_timeout_and_signal_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run34-timeout-") as temporary:
            timeout_path = Path(temporary) / "timeout"
            with mock.patch.object(red, "_execute_selected_tests", side_effect=red.FixtureError("execution_timeout")):
                with self.assertRaises(red.FixtureError) as failure:
                    red.run_reproduction(temp_directory_factory=_directory_factory(timeout_path))
            self.assertEqual(failure.exception.code, "execution_timeout")
            self.assertFalse(timeout_path.exists())
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_test_result(
                subprocess.CompletedProcess(["fixture-test"], -9, b"", b""),
                self.manifest,
            )
        self.assertEqual(failure.exception.code, "signal_terminated")


if __name__ == "__main__":
    unittest.main()
