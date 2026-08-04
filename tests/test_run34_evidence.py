"""Focused integrity and result-contract tests for the Run-34 fixture."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts import reproduce_run34_red as red


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = red.FIXTURE_ROOT


def _copy_fixture(parent: Path) -> Path:
    target = parent / "candidate" / "tests" / "fixtures" / "retrospective" / "run34"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE_ROOT, target)
    return target


def _completed(
    output: str | bytes,
    *,
    returncode: int = 1,
    stdout: bytes = b"",
    stderr: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    encoded = output.encode("utf-8") if isinstance(output, str) else output
    return subprocess.CompletedProcess(
        ["fixture-test"],
        returncode,
        stdout,
        encoded if stderr is None else stderr,
    )


def _directory_factory(path: Path):
    def create(**_: object) -> str:
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    return create


def _rewrite_manifest(package: Path, mutate: object) -> str:
    path = package / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    mutate(manifest)
    data = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _capture_historical_child_result() -> subprocess.CompletedProcess[bytes]:
    manifest = red._validate_fixture()
    red._validate_interpreter(manifest)
    with tempfile.TemporaryDirectory(prefix="sqag-run34-captured-") as temporary:
        execution_root = Path(temporary)
        red._materialise_fixture(manifest, red.FIXTURE_ROOT, execution_root)
        result = red._execute_selected_tests(execution_root, red.EXPECTED_TEST_SELECTION)
        return subprocess.CompletedProcess(
            list(result.args),
            result.returncode,
            bytes(result.stdout),
            bytes(result.stderr),
        )


_HISTORICAL_CAPTURE: subprocess.CompletedProcess[bytes] | None = None


def _cached_historical_result() -> subprocess.CompletedProcess[bytes]:
    global _HISTORICAL_CAPTURE
    if _HISTORICAL_CAPTURE is None:
        _HISTORICAL_CAPTURE = _capture_historical_child_result()
    return _HISTORICAL_CAPTURE


def _child_command(source: str) -> list[str]:
    return [sys.executable, "-c", source]


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

    def test_general_validate_job_is_pinned_to_exact_python_patch(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        validate_section = workflow.split("  validate:", 1)[1]
        self.assertIn('python-version: "3.12.13"', validate_section)
        self.assertNotIn('python-version: "3.12"\n', validate_section)

    def test_non_matching_general_interpreter_does_not_crash_unrelated_checks(self) -> None:
        manifest = red._validate_fixture()
        with mock.patch.object(red.sys, "version_info", (3, 12, 12)):
            self.assertEqual(red._validate_fixture()["fixture_version"], "1.0.0")
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_interpreter(manifest)
            self.assertEqual(failure.exception.code, "interpreter_mismatch")

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
        cls.historical_result = _capture_historical_child_result()
        cls.historical_stderr = red._normalise_child_channel(cls.historical_result.stderr)

    def _valid_output(self, *, statuses: dict[str, str] | None = None, count: int = 13, summary: str = "failures=13") -> str:
        statuses = statuses or {name: "FAIL" for name in red.EXPECTED_TEST_SELECTION}
        lines = self.historical_stderr.splitlines()
        for index, line in enumerate(lines):
            match = re.fullmatch(r"(test_[A-Za-z0-9_]+) \(([^)]+)\) \.\.\. (.+)", line)
            if match and match.group(2) in statuses:
                lines[index] = f"{match.group(1)} ({match.group(2)}) ... {statuses[match.group(2)]}"
        count_index = next(index for index, line in enumerate(lines) if line.startswith("Ran "))
        summary_index = next(index for index, line in enumerate(lines) if line.startswith("FAILED "))
        lines[count_index] = f"Ran {count} tests in 0.001s"
        lines[summary_index] = f"FAILED ({summary})"
        return "\n".join(lines) + ("\n" if self.historical_stderr.endswith("\n") else "")

    def _result_from_lines(self, lines: list[str], *, stdout: bytes = b"") -> subprocess.CompletedProcess[bytes]:
        text = "\n".join(lines) + ("\n" if self.historical_stderr.endswith("\n") else "")
        return _completed(text, stdout=stdout)

    def _assert_rejected(self, result: subprocess.CompletedProcess[bytes], code: str) -> None:
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_test_result(result, self.manifest)
        self.assertEqual(failure.exception.code, code)

    def test_exactly_13_assertion_failures_and_zero_errors_are_accepted(self) -> None:
        red._validate_test_result(self.historical_result, self.manifest)

    def test_crlf_normalization_is_accepted(self) -> None:
        result = _completed(self.historical_stderr.replace("\n", "\r\n"))
        red._validate_test_result(result, self.manifest)

    def test_lone_cr_normalization_is_accepted(self) -> None:
        result = _completed(self.historical_stderr.replace("\n", "\r"))
        red._validate_test_result(result, self.manifest)

    def test_permitted_timing_variation_is_accepted(self) -> None:
        result = _completed(self.historical_stderr.replace(" in 0.", " in 12.345"))
        red._validate_test_result(result, self.manifest)

    def test_permitted_terminal_newline_is_optional(self) -> None:
        result = _completed(self.historical_stderr.rstrip("\n"))
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
        self.assertEqual(failure.exception.code, "unexpected_test_status")

    def test_output_overflow_is_rejected(self) -> None:
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_output_size(b"x" * (red.MAX_OUTPUT_BYTES + 1), b"")
        self.assertEqual(failure.exception.code, "output_overflow")

    def test_decode_failure_is_rejected(self) -> None:
        result = subprocess.CompletedProcess(["fixture-test"], 1, b"", b"\xff")
        self._assert_rejected(result, "child_output_decode_failed")

    def test_unexpected_exit_status_is_rejected(self) -> None:
        result = _completed(self.historical_stderr, returncode=0)
        self._assert_rejected(result, "unexpected_exit_status")

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


    def test_duplicate_count_marker_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("Ran "))
        lines.insert(index + 1, lines[index])
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_conflicting_count_marker_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("Ran "))
        lines.insert(index + 1, "Ran 12 tests in 0.001s")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_missing_count_marker_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("Ran "))
        lines.pop(index)
        self._assert_rejected(self._result_from_lines(lines), "test_count_mismatch")

    def test_duplicate_terminal_summary_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("FAILED "))
        lines.insert(index + 1, lines[index])
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_conflicting_terminal_summary_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("FAILED "))
        lines.insert(index + 1, "FAILED (failures=12)")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_missing_terminal_summary_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        index = next(index for index, line in enumerate(lines) if line.startswith("FAILED "))
        lines.pop(index)
        self._assert_rejected(self._result_from_lines(lines), "failure_summary_mismatch")

    def test_reordered_markers_are_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        count_index = next(index for index, line in enumerate(lines) if line.startswith("Ran "))
        summary_index = next(index for index, line in enumerate(lines) if line.startswith("FAILED "))
        lines[count_index], lines[summary_index] = lines[summary_index], lines[count_index]
        self._assert_rejected(self._result_from_lines(lines), "test_count_mismatch")

    def test_duplicate_selected_test_record_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.insert(1, lines[0])
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_extra_selected_test_record_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.insert(0, "test_extra (tests.extra.ExtraTest.test_extra) ... FAIL")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_missing_selected_test_record_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.pop(0)
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_reordered_selected_test_records_are_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_unmatched_prefix_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.insert(0, "unmatched-prefix")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_unmatched_interstitial_material_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.insert(len(red.EXPECTED_TEST_SELECTION), "unmatched-interstitial")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_trailing_material_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.append("unmatched-suffix")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_valid_stream_followed_by_second_result_structure_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.extend(["Ran 13 tests in 0.001s", "", "FAILED (failures=13)"])
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_required_failure_category_in_wrong_test_block_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        starts = [index for index, line in enumerate(lines) if line.startswith("FAIL: ")]
        categories = tuple(item["category"] for item in self.manifest["test_selection"])
        first_end = starts[1]
        other_end = starts[7]
        for index in range(starts[0], first_end):
            lines[index] = lines[index].replace(categories[0], "__temporary_category__").replace(categories[6], categories[0])
            lines[index] = lines[index].replace("__temporary_category__", categories[6])
        for index in range(starts[6], other_end):
            lines[index] = lines[index].replace(categories[6], "__temporary_category__").replace(categories[0], categories[6])
            lines[index] = lines[index].replace("__temporary_category__", categories[0])
        self._assert_rejected(self._result_from_lines(lines), "expected_failure_category_missing")

    def test_error_indicator_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.insert(len(red.EXPECTED_TEST_SELECTION), "ERROR")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_skip_indicator_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.insert(len(red.EXPECTED_TEST_SELECTION), "SKIP")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_unexpected_success_indicator_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.insert(len(red.EXPECTED_TEST_SELECTION), "unexpected success")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_contradictory_success_structure_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        lines.insert(len(red.EXPECTED_TEST_SELECTION), "OK")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_improper_cross_channel_split_is_rejected(self) -> None:
        encoded = self.historical_stderr.encode("utf-8")
        split = encoded.index(b"Ran ")
        result = _completed(b"", stdout=encoded[:split], stderr=encoded[split:])
        self._assert_rejected(result, "child_stream_mismatch")

    def test_conflicting_valid_structures_across_channels_are_rejected(self) -> None:
        result = _completed(
            b"",
            stdout=b"Ran 13 tests in 0.001s\n\nFAILED (failures=13)\n",
            stderr=self.historical_stderr.encode("utf-8"),
        )
        self._assert_rejected(result, "child_stream_mismatch")


class RetrospectiveFieldBoundContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = red._validate_fixture()
        cls.historical_result = _cached_historical_result()
        cls.historical_stderr = red._normalise_child_channel(cls.historical_result.stderr)
        cls.source_path = next(
            entry["path"] for entry in cls.manifest["payload"] if entry.get("role") == "historical-test-selection"
        )
        cls.source_text = cls.manifest.payload_bytes[cls.source_path].decode("utf-8")
        cls.source_all_lines = cls.source_text.splitlines()
        cls.source_line_count = len(cls.source_all_lines)

    def _result_from_lines(self, lines: list[str]) -> subprocess.CompletedProcess[bytes]:
        return _completed("\n".join(lines) + ("\n" if self.historical_stderr.endswith("\n") else ""))

    def _assert_rejected(self, result: subprocess.CompletedProcess[bytes], code: str) -> None:
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_test_result(result, self.manifest)
        self.assertEqual(failure.exception.code, code)

    def _frame_and_source(self, frame_line: str) -> tuple[int, int]:
        lines = self.historical_stderr.splitlines()
        frame_index = next(
            index for index, line in enumerate(lines) if line.startswith('  File "') and frame_line in line
        )
        return frame_index, frame_index + 1

    def _mutate_second_frame(self, new_number: str, new_source: str | None = None) -> subprocess.CompletedProcess[bytes]:
        lines = self.historical_stderr.splitlines()
        frame_index, source_index = self._frame_and_source(", line 46, in _assert_rejected")
        lines[frame_index] = lines[frame_index].replace(
            ", line 46, in ", ", line %s, in " % new_number
        )
        if new_source is not None:
            lines[source_index] = "    " + new_source
        return self._result_from_lines(lines)

    def test_minimum_valid_source_line_is_accepted(self) -> None:
        red._validate_test_result(
            self._mutate_second_frame("1", self.source_all_lines[0].lstrip(" ")),
            self.manifest,
        )

    def test_maximum_valid_source_line_is_accepted(self) -> None:
        red._validate_test_result(
            self._mutate_second_frame(str(self.source_line_count), self.source_all_lines[-1].lstrip(" ")),
            self.manifest,
        )

    def test_maximum_valid_source_line_with_correct_source_text_is_accepted(self) -> None:
        red._validate_test_result(
            self._mutate_second_frame(str(self.source_line_count), self.source_all_lines[-1].lstrip(" ")),
            self.manifest,
        )

    def test_line_number_invalid_matrix_rejects(self) -> None:
        invalid_numbers = (
            "0",
            "-5",
            "+5",
            " 5",
            "5 ",
            "046",
            str(self.source_line_count + 1),
            "999",
            "1000",
            "9" * 400,
            "9" * 2000,
            "5.0",
            "5e3",
            "\u0665",
        )
        for value in invalid_numbers:
            with self.subTest(line_number=value):
                self._assert_rejected(self._mutate_second_frame(value), "child_stream_mismatch")

    def test_correct_line_number_with_wrong_source_text_is_rejected(self) -> None:
        wrong_source = self.source_all_lines[71].lstrip(" ")
        self.assertNotEqual(wrong_source, self.source_all_lines[45].lstrip(" "))
        self._assert_rejected(self._mutate_second_frame("46", wrong_source), "child_stream_mismatch")

    def test_correct_source_text_attached_to_wrong_line_number_is_rejected(self) -> None:
        correct_text = self.source_all_lines[45].lstrip(" ")
        self._assert_rejected(self._mutate_second_frame("72", correct_text), "child_stream_mismatch")

    def test_timing_valid_matrix_is_accepted(self) -> None:
        valid_timings = (
            "0",
            "0.001",
            "0.123456",
            "3600.000",
            "3600",
        )
        for timing in valid_timings:
            with self.subTest(timing=timing):
                red._validate_test_result(self._timing_result(timing), self.manifest)

    def test_timing_invalid_matrix_rejects(self) -> None:
        invalid_timings = (
            "3600.000001",
            "10000.000",
            "0.1234567",
            "9" * 200 + ".000",
            "0." + "9" * 200,
            "01.500",
            ".500",
            "5.",
            "0.",
            "5.1.2",
            "+5.000",
            "-5.000",
            " 5.000",
            "5.000 ",
            "5e3",
            "NaN",
            "inf",
            "-inf",
            "\u0665.\u0665",
        )
        for timing in invalid_timings:
            with self.subTest(timing=timing):
                self._assert_rejected(self._timing_result(timing), "child_stream_mismatch")

    def test_extra_timing_marker_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        count_index = next(index for index, line in enumerate(lines) if line.startswith("Ran "))
        lines.insert(count_index + 1, "Ran 13 tests in 0.001s")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def test_conflicting_timing_marker_is_rejected(self) -> None:
        lines = self.historical_stderr.splitlines()
        count_index = next(index for index, line in enumerate(lines) if line.startswith("Ran "))
        lines.insert(count_index + 1, "Ran 13 tests in 0.002s")
        self._assert_rejected(self._result_from_lines(lines), "child_stream_mismatch")

    def _timing_result(self, timing: str) -> subprocess.CompletedProcess[bytes]:
        lines = self.historical_stderr.splitlines()
        count_index = next(index for index, line in enumerate(lines) if line.startswith("Ran "))
        lines[count_index] = "Ran 13 tests in %ss" % timing
        return self._result_from_lines(lines)


class RetrospectiveCleanupNegativeContractTest(unittest.TestCase):
    def test_cleanup_failure_returns_non_success_receipt_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run51-cleanup-fail-") as temporary:
            target = Path(temporary) / "work"
            created: list = []
            real_popen = red.subprocess.Popen

            def recording_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                created.append(process)
                return process

            def failing_rmtree(path, *args, **kwargs):
                raise OSError("forced cleanup failure")

            with mock.patch.object(red.subprocess, "Popen", side_effect=recording_popen):
                with mock.patch.object(red.shutil, "rmtree", side_effect=failing_rmtree):
                    with self.assertRaises(red.FixtureError) as failure:
                        red.run_reproduction(temp_directory_factory=_directory_factory(target))
            self.assertEqual(failure.exception.code, "cleanup_failed")
            self.assertTrue(all(process.poll() is not None for process in created))
            receipt = red._failure_receipt("cleanup_failed")
            serialised = red._serialise_receipt(receipt)
            self.assertEqual(red._parse_receipt(serialised), receipt)
            self.assertEqual(receipt["status"], "failed")
            self.assertFalse(receipt["cleanup_verified"])
            self.assertFalse(receipt["child_output_emitted"])
            self.assertNotIn(str(target), serialised)
            self.assertNotIn("AssertionError", serialised)
            self.assertTrue(target.exists())
            shutil.rmtree(target, ignore_errors=True)
            self.assertFalse(target.exists())

    def test_cleanup_remnant_returns_non_success_receipt_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run51-remnant-") as temporary:
            target = Path(temporary) / "work"
            created: list = []
            real_popen = red.subprocess.Popen
            real_rmtree = red.shutil.rmtree

            def recording_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                created.append(process)
                return process

            def rmtree_leaving_remnant(path, *args, **kwargs):
                real_rmtree(path, *args, **kwargs)
                remnant = Path(path) / "synthetic-remnant.txt"
                remnant.parent.mkdir(parents=True, exist_ok=True)
                remnant.write_text("synthetic remnant", encoding="utf-8")

            with mock.patch.object(red.subprocess, "Popen", side_effect=recording_popen):
                with mock.patch.object(red.shutil, "rmtree", side_effect=rmtree_leaving_remnant):
                    with self.assertRaises(red.FixtureError) as failure:
                        red.run_reproduction(temp_directory_factory=_directory_factory(target))
            self.assertEqual(failure.exception.code, "cleanup_remnant")
            self.assertTrue(all(process.poll() is not None for process in created))
            receipt = red._failure_receipt("cleanup_remnant")
            serialised = red._serialise_receipt(receipt)
            self.assertEqual(red._parse_receipt(serialised), receipt)
            self.assertEqual(receipt["status"], "failed")
            self.assertFalse(receipt["cleanup_verified"])
            self.assertFalse(receipt["child_output_emitted"])
            self.assertNotIn(str(target), serialised)
            self.assertNotIn("AssertionError", serialised)
            self.assertTrue(target.exists())
            shutil.rmtree(target, ignore_errors=True)
            self.assertFalse(target.exists())

    def test_cleanup_failure_and_remnant_receipts_are_distinct_and_bounded(self) -> None:
        failure_receipt = red._failure_receipt("cleanup_failed")
        remnant_receipt = red._failure_receipt("cleanup_remnant")
        self.assertEqual(tuple(failure_receipt), red.RECEIPT_KEYS)
        self.assertEqual(tuple(remnant_receipt), red.RECEIPT_KEYS)
        self.assertNotEqual(failure_receipt["error_code"], remnant_receipt["error_code"])
        self.assertIn(failure_receipt["error_code"], red.RECEIPT_ERROR_CODES)
        self.assertIn(remnant_receipt["error_code"], red.RECEIPT_ERROR_CODES)
        for receipt in (failure_receipt, remnant_receipt):
            self.assertEqual(len(red._serialise_receipt(receipt).encode("utf-8")) <= red.MAX_RECEIPT_BYTES, True)
            self.assertEqual(red._parse_receipt(red._serialise_receipt(receipt)), receipt)
            self.assertFalse(receipt["child_output_emitted"])
            self.assertIsNone(receipt["child_exit_status"])


class RetrospectiveFailClosedIntegrityTest(unittest.TestCase):
    def test_raw_path_examples_and_windows_forms_are_rejected(self) -> None:
        rejected = (
            "",
            "/absolute",
            "a/",
            "a//b",
            "./a",
            "a/./b",
            "../a",
            "a/../b",
            "a\\b",
            "\\\\server\\share",
            "C:/absolute",
            "C:relative",
            "name:stream",
            "a\x00b",
            "a\nb",
            "a./b.",
            "a/part ",
            "aux",
            "CON.txt",
            "prn.log",
            "Com1.data",
            "lPt9.out",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(red.FixtureError) as failure:
                    red._validate_relative_path(value)
                self.assertEqual(failure.exception.code, "invalid_fixture_path")

    def test_control_format_and_surrogate_categories_are_rejected(self) -> None:
        values = (
            "safe" + chr(0x0009) + "name",
            "safe" + chr(0x202E) + "name",
            "safe" + chr(0xD800) + "name",
        )
        for value in values:
            with self.subTest(category=unicodedata.category(value[4])):
                with self.assertRaises(red.FixtureError) as failure:
                    red._validate_relative_path(value)
                self.assertEqual(failure.exception.code, "invalid_fixture_path")

    def test_reserved_device_canonicalisation_rejects_adjacent_forms_and_keeps_near_misses(self) -> None:
        for stem in sorted(red._WINDOWS_RESERVED_NAMES):
            for value in (stem.lower() + ".data", stem + " .data", stem + "...data", stem + " . .data"):
                with self.subTest(kind="reserved", stem=stem):
                    with self.assertRaises(red.FixtureError) as failure:
                        red._validate_relative_path(value)
                    self.assertEqual(failure.exception.code, "invalid_fixture_path")
        for value in ("CONX.data", "COM10.data", "LPT10.data", "AUXILIARY.data"):
            with self.subTest(kind="near_miss"):
                self.assertEqual(red._validate_relative_path(value), value)

    def test_case_and_normalisation_collisions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-collision-") as temporary:
            package = _copy_fixture(Path(temporary))
            digest = _rewrite_manifest(package, lambda manifest: manifest["payload"][1].update({"path": "DEPENDENCIES.JSON"}))
            with mock.patch.object(red, "MANIFEST_SHA256", digest):
                with self.assertRaises(red.FixtureError) as failure:
                    red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "duplicate_payload_path")

    def test_duplicate_payload_digest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-duplicate-digest-") as temporary:
            package = _copy_fixture(Path(temporary))
            digest = _rewrite_manifest(
                package,
                lambda manifest: manifest["payload"][1].update({"sha256": manifest["payload"][0]["sha256"]}),
            )
            with mock.patch.object(red, "MANIFEST_SHA256", digest):
                with self.assertRaises(red.FixtureError) as failure:
                    red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "duplicate_payload_digest")

    def test_closed_manifest_types_reject_unknown_role_and_boolean_counts(self) -> None:
        mutations = (
            (lambda manifest: manifest["payload"][0].update({"role": "unknown"}), "payload_entry_schema_mismatch"),
            (lambda manifest: manifest["expected_result"].update({"tests": True}), "expected_result_mismatch"),
            (lambda manifest: manifest["historical_test_change"].update({"implementation_files_applied": False}), "historical_change_scope_mismatch"),
        )
        for index, (mutate, expected_code) in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory(prefix="sqag-run44-closed-schema-") as temporary:
                package = _copy_fixture(Path(temporary))
                digest = _rewrite_manifest(package, mutate)
                with mock.patch.object(red, "MANIFEST_SHA256", digest):
                    with self.assertRaises(red.FixtureError) as failure:
                        red._validate_fixture(package)
                self.assertEqual(failure.exception.code, expected_code)

    def test_symlink_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-symlink-") as temporary:
            package = _copy_fixture(Path(temporary))
            target = package / "fixture-explanation.md"
            outside = Path(temporary) / "outside.md"
            outside.write_text("outside\n", encoding="utf-8")
            target.unlink()
            try:
                os.symlink(outside, target)
            except OSError:
                self.skipTest("host does not permit symlink creation")
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "fixture_entry_forbidden")

    def test_symlink_intermediate_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-intermediate-link-") as temporary:
            root = Path(temporary)
            package = _copy_fixture(root)
            outside = root / "outside-tests"
            shutil.copytree(package / "tests", outside)
            shutil.rmtree(package / "tests")
            try:
                os.symlink(outside, package / "tests", target_is_directory=True)
            except OSError:
                self.skipTest("host does not permit directory symlink creation")
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "fixture_entry_forbidden")

    def test_link_inserted_after_enumeration_is_rejected_before_payload_read(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-link-race-") as temporary:
            root = Path(temporary)
            package = _copy_fixture(root)
            outside = root / "outside-tests"
            shutil.copytree(package / "tests", outside)
            original_payload_files = red._payload_files

            def enumerate_then_replace(path: Path) -> set[str]:
                result = original_payload_files(path)
                shutil.rmtree(path / "tests")
                try:
                    os.symlink(outside, path / "tests", target_is_directory=True)
                except OSError:
                    raise unittest.SkipTest("host does not permit directory symlink creation")
                return result

            with mock.patch.object(red, "_payload_files", side_effect=enumerate_then_replace):
                with self.assertRaises(red.FixtureError) as failure:
                    red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "fixture_entry_forbidden")

    def test_hard_link_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-hardlink-") as temporary:
            package = _copy_fixture(Path(temporary))
            target = package / "fixture-explanation.md"
            outside = Path(temporary) / "hardlink-target.md"
            try:
                os.link(target, outside)
            except OSError:
                self.skipTest("host does not permit hard-link creation")
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "fixture_entry_forbidden")

    def test_non_regular_fifo_payload_is_rejected_on_posix(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX FIFO capability is unavailable on Windows")
        with tempfile.TemporaryDirectory(prefix="sqag-run44-fifo-") as temporary:
            package = _copy_fixture(Path(temporary))
            fifo = package / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "fixture_entry_forbidden")

    def test_windows_junction_is_rejected_when_supported(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows reparse-point capability is unavailable on this host")
        with tempfile.TemporaryDirectory(prefix="sqag-run44-junction-") as temporary:
            package = _copy_fixture(Path(temporary))
            target = package / "docs"
            link = package / "linked-docs"
            result = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(link), str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if result.returncode != 0:
                self.skipTest("host does not permit junction creation")
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "fixture_entry_forbidden")

    def test_materialisation_uses_original_verified_bytes_after_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-toctou-") as temporary:
            root = Path(temporary)
            package = _copy_fixture(root)
            manifest = red._validate_fixture(package)
            original = manifest.payload_bytes["fixture-explanation.md"]
            with self.assertRaises(TypeError):
                manifest.payload_bytes["fixture-explanation.md"] = b"forged"  # type: ignore[index]
            source = package / "fixture-explanation.md"
            source.write_bytes(b"changed after validation with the same path")
            execution = root / "execution"
            execution.mkdir()
            red._materialise_fixture(manifest, package, execution)
            self.assertEqual((execution / "fixture-explanation.md").read_bytes(), original)

    def test_materialisation_survives_source_replacement_with_link(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-toctou-link-") as temporary:
            root = Path(temporary)
            package = _copy_fixture(root)
            manifest = red._validate_fixture(package)
            original = manifest.payload_bytes["fixture-explanation.md"]
            source = package / "fixture-explanation.md"
            outside = root / "outside.md"
            outside.write_bytes(b"replacement")
            source.unlink()
            try:
                os.symlink(outside, source)
            except OSError:
                self.skipTest("host does not permit symlink creation")
            execution = root / "execution"
            execution.mkdir()
            red._materialise_fixture(manifest, package, execution)
            self.assertEqual((execution / "fixture-explanation.md").read_bytes(), original)

    def test_materialisation_survives_source_replacement_with_hard_link(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-toctou-hardlink-") as temporary:
            root = Path(temporary)
            package = _copy_fixture(root)
            manifest = red._validate_fixture(package)
            original = manifest.payload_bytes["fixture-explanation.md"]
            source = package / "fixture-explanation.md"
            outside = root / "outside-hardlink.md"
            outside.write_bytes(b"replacement")
            source.unlink()
            try:
                os.link(outside, source)
            except OSError:
                self.skipTest("host does not permit hard-link creation")
            execution = root / "execution"
            execution.mkdir()
            red._materialise_fixture(manifest, package, execution)
            self.assertEqual((execution / "fixture-explanation.md").read_bytes(), original)

    def test_materialisation_rejects_linked_destination_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run44-destination-link-") as temporary:
            root = Path(temporary)
            package = _copy_fixture(root)
            manifest = red._validate_fixture(package)
            execution = root / "execution"
            execution.mkdir()
            outside = root / "outside"
            outside.mkdir()
            linked_parent = execution / "tests"
            try:
                os.symlink(outside, linked_parent, target_is_directory=True)
            except OSError:
                self.skipTest("host does not permit directory symlink creation")
            with self.assertRaises(red.FixtureError) as failure:
                red._materialise_fixture(manifest, package, execution)
            self.assertEqual(failure.exception.code, "fixture_materialisation_failed")
            self.assertFalse((outside / "test_runtime_privilege_contract.py").exists())
            shutil.rmtree(execution, ignore_errors=True)

    def test_interpreter_identity_and_version_mismatches_reject(self) -> None:
        manifest = red._validate_fixture()
        with mock.patch.object(red.sys, "version_info", (3, 12, 12)):
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_interpreter(manifest)
        self.assertEqual(failure.exception.code, "interpreter_mismatch")
        with mock.patch.object(red.sys, "version_info", (3, 13, 0)):
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_interpreter(manifest)
        self.assertEqual(failure.exception.code, "interpreter_mismatch")
        with mock.patch.object(red.sys, "implementation", SimpleNamespace(name="pypy")):
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_interpreter(manifest)
        self.assertEqual(failure.exception.code, "interpreter_mismatch")


class StrictReceiptParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = red._validate_fixture()
        cls.receipt = red._success_receipt(cls.manifest)
        cls.line = red._serialise_receipt(cls.receipt)

    def assert_rejected(self, value: bytes | str) -> None:
        with self.assertRaises(red.FixtureError) as failure:
            red._parse_receipt(value)
        self.assertEqual(failure.exception.code, "receipt_invalid")

    def test_valid_receipt_is_independently_parsed(self) -> None:
        parsed = red._parse_receipt(self.line.encode("utf-8"))
        self.assertEqual(parsed, self.receipt)

    def test_receipt_negative_matrix(self) -> None:
        cases: list[bytes | str] = [
            b"",
            b"not-json",
            b"\xff",
            self.line + "\n" + self.line,
            " " + self.line,
            self.line + " ",
            self.line + "\n",
            '{"schema":"sqag-retrospective-receipt-v1","schema":"sqag-retrospective-receipt-v1"}',
            (self.line[:-1] + ",").encode("utf-8"),
            ("x" * (red.MAX_RECEIPT_BYTES + 1)),
        ]
        conflicting = dict(self.receipt)
        conflicting["test_count"] = 12
        cases.append(self.line + "\n" + red._serialise_receipt(conflicting))
        missing = dict(self.receipt)
        missing.pop("schema")
        cases.append(red._serialise_receipt(missing))
        unknown = dict(self.receipt)
        unknown["unknown"] = False
        cases.append(red._serialise_receipt(unknown))
        cases.append(red._serialise_receipt(dict(reversed(tuple(self.receipt.items())))))
        wrong_schema = dict(self.receipt)
        wrong_schema["schema"] = "other"
        cases.append(red._serialise_receipt(wrong_schema))
        wrong_status = dict(self.receipt)
        wrong_status["status"] = "other"
        cases.append(red._serialise_receipt(wrong_status))
        for field in ("test_count", "assertion_failures", "errors", "unexpected_passes", "skipped"):
            candidate = dict(self.receipt)
            candidate[field] = True
            cases.append(red._serialise_receipt(candidate))
        for field in ("timeout", "signal_termination", "output_overflow", "cleanup_verified"):
            candidate = dict(self.receipt)
            candidate[field] = 1
            cases.append(red._serialise_receipt(candidate))
        for field in (
            "historical_git_lookup",
            "current_dependencies_used",
            "child_output_emitted",
            "live_system_use",
            "secret_bearing_output",
        ):
            candidate = dict(self.receipt)
            candidate[field] = 1
            cases.append(red._serialise_receipt(candidate))
        for field in ("schema", "status", "fixture_version"):
            candidate = dict(self.receipt)
            candidate[field] = 1
            cases.append(red._serialise_receipt(candidate))
        wrong_error_type = dict(self.receipt)
        wrong_error_type["error_code"] = 1
        cases.append(red._serialise_receipt(wrong_error_type))
        wrong_child_status = dict(self.receipt)
        wrong_child_status["child_exit_status"] = "1"
        cases.append(red._serialise_receipt(wrong_child_status))
        negative = dict(self.receipt)
        negative["test_count"] = -1
        cases.append(red._serialise_receipt(negative))
        inconsistent = dict(self.receipt)
        inconsistent["assertion_failures"] = 12
        cases.append(red._serialise_receipt(inconsistent))
        success_error = dict(self.receipt)
        success_error["error_code"] = "interpreter_mismatch"
        cases.append(red._serialise_receipt(success_error))
        cleanup_false = dict(self.receipt)
        cleanup_false["cleanup_verified"] = False
        cases.append(red._serialise_receipt(cleanup_false))
        for field in ("historical_git_lookup", "current_dependencies_used", "child_output_emitted", "live_system_use", "secret_bearing_output"):
            candidate = dict(self.receipt)
            candidate[field] = True
            cases.append(red._serialise_receipt(candidate))
        uri_error = dict(self.receipt)
        uri_error["status"] = "failed"
        uri_error["error_code"] = "https://invalid.example/secret"
        uri_error["fixture_version"] = None
        uri_error["child_exit_status"] = None
        uri_error.update({field: 0 for field in ("test_count", "assertion_failures", "errors", "unexpected_passes", "skipped")})
        uri_error.update({field: False for field in ("cleanup_verified", "timeout", "signal_termination", "output_overflow", "historical_git_lookup", "current_dependencies_used", "child_output_emitted", "live_system_use", "secret_bearing_output")})
        cases.append(red._serialise_receipt(uri_error))
        unknown_error = dict(uri_error)
        unknown_error["error_code"] = "unknown_error_category"
        cases.append(red._serialise_receipt(unknown_error))
        missing_error = dict(uri_error)
        missing_error["error_code"] = None
        cases.append(red._serialise_receipt(missing_error))
        free_form_error = dict(uri_error)
        free_form_error["error_code"] = "Traceback: secret-shaped detail"
        cases.append(red._serialise_receipt(free_form_error))
        historical_git = dict(self.receipt)
        historical_git["historical_git_lookup"] = True
        cases.append(red._serialise_receipt(historical_git))
        for index, case in enumerate(cases):
            with self.subTest(index=index):
                self.assert_rejected(case)

    def test_failure_posture_is_consistent(self) -> None:
        timeout = red._failure_receipt("execution_timeout")
        self.assertTrue(red._parse_receipt(red._serialise_receipt(timeout))["timeout"])
        bad = dict(timeout)
        bad["timeout"] = False
        self.assert_rejected(red._serialise_receipt(bad))
        stream_failure = red._failure_receipt("child_stream_mismatch")
        self.assertEqual(red._parse_receipt(red._serialise_receipt(stream_failure)), stream_failure)


class RealSubprocessLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.execution_root = Path(tempfile.mkdtemp(prefix="sqag-run44-process-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.execution_root, ignore_errors=True)

    def test_real_timeout_terminates_child_and_returns_fixed_category(self) -> None:
        with self.assertRaises(red.FixtureError) as failure:
            red._execute_child(
                _child_command("import time; time.sleep(5)"),
                self.execution_root,
                timeout_seconds=0.1,
            )
        self.assertEqual(failure.exception.code, "execution_timeout")

    def test_real_stdout_overflow_is_bounded_and_terminated(self) -> None:
        with self.assertRaises(red.FixtureError) as failure:
            red._execute_child(
                _child_command("import sys; sys.stdout.write('x' * 70000); sys.stdout.flush()"),
                self.execution_root,
                timeout_seconds=5,
            )
        self.assertEqual(failure.exception.code, "output_overflow")

    def test_real_stderr_overflow_is_bounded_and_terminated(self) -> None:
        with self.assertRaises(red.FixtureError) as failure:
            red._execute_child(
                _child_command("import sys; sys.stderr.write('x' * 70000); sys.stderr.flush()"),
                self.execution_root,
                timeout_seconds=5,
            )
        self.assertEqual(failure.exception.code, "output_overflow")

    def test_signal_result_is_rejected_on_posix(self) -> None:
        if os.name == "nt":
            self.skipTest("signal termination semantics are unavailable on Windows")
        result = red._execute_child(
            _child_command("import os, signal; os.kill(os.getpid(), signal.SIGTERM)"),
            self.execution_root,
            timeout_seconds=5,
        )
        self.assertLess(result.returncode, 0)
        with self.assertRaises(red.FixtureError) as failure:
            red._validate_test_result(result, red._validate_fixture())
        self.assertEqual(failure.exception.code, "signal_terminated")

    def test_both_reader_streams_close_without_deadlock(self) -> None:
        result = red._execute_child(
            _child_command("import sys; sys.stdout.write('out'); sys.stderr.write('err')"),
            self.execution_root,
            timeout_seconds=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"out")
        self.assertEqual(result.stderr, b"err")


HISTORICAL_ATTRIBUTION = {
    "provider": "OpenAI",
    "model": "GPT-5.6 Sol",
    "reasoning": "High",
}


class _EmptyReaderStream:
    def read(self, _size: int) -> bytes:
        return b""

    def close(self) -> None:
        pass


class _UnreapableProcess:
    def __init__(self, stream: object | None = None) -> None:
        self.stdout = stream if stream is not None else _EmptyReaderStream()
        self.stderr = self.stdout
        self.returncode: int | None = None
        self.wait_calls: list[object] = []

    def poll(self) -> None:
        return None

    def wait(self, timeout: object | None = None) -> None:
        self.wait_calls.append(timeout)
        raise subprocess.TimeoutExpired(["synthetic"], timeout)

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


class RetrospectiveAttributionContractTest(unittest.TestCase):
    def _assert_attribution_rejected(self, attribution: object, expected_code: str) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run55-attribution-") as temporary:
            package = _copy_fixture(Path(temporary))

            def mutate(manifest: dict[str, Any]) -> None:
                manifest["provenance"]["attribution"] = attribution

            digest = _rewrite_manifest(package, mutate)
            with mock.patch.object(red, "MANIFEST_SHA256", digest):
                with self.assertRaises(red.FixtureError) as failure:
                    red._validate_fixture(package)
            self.assertEqual(failure.exception.code, expected_code)

    def test_historical_attribution_is_preserved_in_manifest(self) -> None:
        manifest = red._validate_fixture()
        self.assertEqual(manifest["provenance"]["attribution"], HISTORICAL_ATTRIBUTION)

    def test_missing_blank_wrong_type_extra_and_oversized_attribution_rejects(self) -> None:
        cases: list[tuple[object, str]] = [
            (None, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": "GPT-5.6 Sol"}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "reasoning": "High"}, "provenance_schema_mismatch"),
            ({"model": "GPT-5.6 Sol", "reasoning": "High"}, "provenance_schema_mismatch"),
            ({"provider": "", "model": "GPT-5.6 Sol", "reasoning": "High"}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": "   ", "reasoning": "High"}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": "GPT-5.6 Sol", "reasoning": ""}, "provenance_schema_mismatch"),
            ({"provider": 7, "model": "GPT-5.6 Sol", "reasoning": "High"}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": None, "reasoning": "High"}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": "GPT-5.6 Sol", "reasoning": ["High"]}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": "GPT-5.6 Sol", "reasoning": "High", "run_id": "x"}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": "GPT-5.6 Sol", "reasoning": "High", "model2": "x"}, "provenance_schema_mismatch"),
            ({"provider": "O" * 65, "model": "GPT-5.6 Sol", "reasoning": "High"}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": "M" * 65, "reasoning": "High"}, "provenance_schema_mismatch"),
            ({"provider": "OpenAI", "model": "GPT-5.6 Sol", "reasoning": "R" * 65}, "provenance_schema_mismatch"),
        ]
        for index, (attribution, expected_code) in enumerate(cases):
            with self.subTest(index=index):
                self._assert_attribution_rejected(attribution, expected_code)

    def test_changed_attribution_invalidates_manifest_digest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run55-attribution-digest-") as temporary:
            package = _copy_fixture(Path(temporary))

            def mutate(manifest: dict[str, Any]) -> None:
                manifest["provenance"]["attribution"] = {
                    "provider": "DeepSeek",
                    "model": "DeepSeek V4 Flash",
                    "reasoning": "High",
                }

            _rewrite_manifest(package, mutate)
            with self.assertRaises(red.FixtureError) as failure:
                red._validate_fixture(package)
            self.assertEqual(failure.exception.code, "manifest_digest_mismatch")

    def test_current_executor_model_cannot_alter_historical_attribution(self) -> None:
        source = Path(red.__file__).read_text(encoding="utf-8")
        for token in ("deepseek", "DeepSeek V4 Flash", "DeepSeek V4"):
            self.assertNotIn(token, source, token)
        manifest = red._validate_fixture()
        self.assertEqual(manifest["provenance"]["attribution"], HISTORICAL_ATTRIBUTION)

    def test_reproduction_receipt_requires_valid_attribution_contract(self) -> None:
        receipt = red.run_reproduction()
        self.assertEqual(receipt["status"], "passed")
        self.assertEqual(tuple(receipt), red.RECEIPT_KEYS)


class BoundedChildReapContractTest(unittest.TestCase):
    def test_child_reap_failed_is_a_fixed_bounded_receipt_category(self) -> None:
        self.assertIn("child_reap_failed", red.RECEIPT_ERROR_CODES)
        receipt = red._failure_receipt("child_reap_failed")
        self.assertEqual(red._parse_receipt(red._serialise_receipt(receipt)), receipt)
        self.assertLessEqual(len(red._serialise_receipt(receipt).encode("utf-8")), red.MAX_RECEIPT_BYTES)
        self.assertFalse(receipt["child_output_emitted"])
        self.assertFalse(receipt["cleanup_verified"])

    def test_unreapable_child_returns_fixed_lifecycle_error_with_bounded_waits(self) -> None:
        process = _UnreapableProcess()
        with self.assertRaises(red.FixtureError) as failure:
            red._kill_process(process)
        self.assertEqual(failure.exception.code, "child_reap_failed")
        self.assertGreaterEqual(len(process.wait_calls), 3)
        self.assertTrue(all(timeout is not None for timeout in process.wait_calls))

    def test_no_final_wait_is_invoked_without_a_timeout(self) -> None:
        source = Path(red.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        bounded_waits = True
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "wait":
                continue
            keywords = {keyword.arg for keyword in node.keywords}
            if "timeout" not in keywords:
                bounded_waits = False
                break
        self.assertTrue(bounded_waits)

    def test_child_ignoring_terminate_is_reaped_after_kill(self) -> None:
        if os.name == "nt":
            self.skipTest("SIGTERM ignore semantics are unavailable on Windows")
        child = red.subprocess.Popen(
            _child_command("import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            red._kill_process(child)
            self.assertIsNotNone(child.poll())
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)

    def test_child_reap_failure_propagates_from_execute_child_without_masking(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sqag-run55-reap-") as temporary:
            execution_root = Path(temporary)
            process = _UnreapableProcess()
            with mock.patch.object(red.subprocess, "Popen", return_value=process):
                with self.assertRaises(red.FixtureError) as failure:
                    red._execute_child(["synthetic"], execution_root, timeout_seconds=0.05)
            self.assertEqual(failure.exception.code, "child_reap_failed")
            self.assertTrue(all(timeout is not None for timeout in process.wait_calls))


if __name__ == "__main__":
    unittest.main()
