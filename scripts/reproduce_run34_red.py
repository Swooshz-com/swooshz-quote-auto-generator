"""Run the repository-contained Run-34 retrospective fixture.

The fixture is retrospective evidence, not original RED chronology. It is
validated and materialised from a closed manifest. No historical repository
object, source branch, tag, current requirements file, network service, or
secret is required.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "retrospective" / "run34"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
MANIFEST_SHA256 = "88e01577be6aa83e43a4c6e2ebd9655b453943ae624f3ba67ce7610271662a0c"
MAX_OUTPUT_BYTES = 64 * 1024
TEST_TIMEOUT_SECONDS = 30.0

EXPECTED_TEST_SELECTION = (
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_unrelated_parent_to_sqag_migrator_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_unrelated_parent_to_sqag_app_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_unrelated_parent_to_neon_superuser_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_sqag_migrator_to_unrelated_member_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_sqag_app_to_unrelated_member_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_neon_superuser_to_unrelated_member_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_protected_role_used_as_grantor_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_inherit_true_on_unrelated_parent_protected_member_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_set_true_on_unrelated_parent_protected_member_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_admin_true_on_unauthorised_protected_role_row_is_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_multiple_protected_role_rows_alongside_exact_edge_are_rejected",
    "tests.test_runtime_privilege_contract.RuntimeMembershipEdgeEvaluatorTest.test_recursive_protected_role_path_not_beginning_with_runtime_is_rejected",
    "tests.test_runtime_privilege_contract.RequirementEvidenceMapTest.test_membership_query_narrative_has_exact_six_field_unfiltered_contract",
)
EXPECTED_RESULT = {
    "tests": 13,
    "assertion_failures": 13,
    "errors": 0,
    "unexpected_passes": 0,
    "skipped": 0,
    "exit_status": 1,
}
RECEIPT_KEYS = (
    "schema",
    "status",
    "error_code",
    "fixture_version",
    "test_count",
    "assertion_failures",
    "errors",
    "unexpected_passes",
    "skipped",
    "cleanup_verified",
    "historical_git_lookup",
    "current_dependencies_used",
    "child_output_emitted",
    "live_system_use",
    "secret_bearing_output",
)


class FixtureError(RuntimeError):
    """A public-safe, deterministic fixture failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureError("duplicate_json_key")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeError, json.JSONDecodeError, FixtureError):
        raise FixtureError("invalid_fixture_json") from None
    if type(value) is not dict:
        raise FixtureError("fixture_json_must_be_object")
    return value


def _validate_relative_path(value: Any) -> str:
    if type(value) is not str or not value or "\\" in value:
        raise FixtureError("invalid_fixture_path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FixtureError("invalid_fixture_path")
    return path.as_posix()


def _validate_sha(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise FixtureError("invalid_fixture_digest")
    return value


def _payload_files(package_root: Path) -> set[str]:
    actual: set[str] = set()
    try:
        candidates = tuple(package_root.rglob("*"))
    except OSError:
        raise FixtureError("fixture_enumeration_failed") from None
    for candidate in candidates:
        if candidate.is_symlink():
            raise FixtureError("fixture_symlink_forbidden")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(package_root).as_posix()
        if relative == "manifest.json":
            continue
        actual.add(relative)
    return actual


def _validate_fixture(package_root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    package_root = package_root.resolve()
    manifest_path = package_root / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError:
        raise FixtureError("manifest_missing") from None
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        raise FixtureError("manifest_digest_mismatch")
    manifest = _load_json(manifest_path)

    expected_top_level = {
        "$schema",
        "fixture_version",
        "provenance",
        "payload",
        "dependency_definition",
        "historical_test_change",
        "test_selection",
        "expected_result",
        "receipt_schema",
    }
    if set(manifest) != expected_top_level or manifest["$schema"] != "sqag-retrospective-fixture-manifest-v1":
        raise FixtureError("manifest_schema_mismatch")
    if manifest["fixture_version"] != "1.0.0":
        raise FixtureError("fixture_version_mismatch")

    provenance = manifest["provenance"]
    if type(provenance) is not dict or set(provenance) != {
        "historical_source_revision",
        "canonical_parent",
        "canonical_replacement_head",
        "retrospective",
        "original_red_chronology",
        "original_development_sequence",
    }:
        raise FixtureError("provenance_schema_mismatch")
    if (
        type(provenance["historical_source_revision"]) is not str
        or type(provenance["canonical_parent"]) is not str
        or type(provenance["canonical_replacement_head"]) is not str
        or provenance["retrospective"] is not True
        or provenance["original_red_chronology"] is not False
        or provenance["original_development_sequence"] is not False
    ):
        raise FixtureError("provenance_value_mismatch")

    payload = manifest["payload"]
    if type(payload) is not list or not payload:
        raise FixtureError("payload_schema_mismatch")
    expected_payload: dict[str, dict[str, Any]] = {}
    for entry in payload:
        if type(entry) is not dict or set(entry) != {"path", "role", "sha256", "size"}:
            raise FixtureError("payload_entry_schema_mismatch")
        path = _validate_relative_path(entry["path"])
        digest = _validate_sha(entry["sha256"])
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise FixtureError("payload_size_mismatch")
        if path in expected_payload:
            raise FixtureError("duplicate_payload_path")
        expected_payload[path] = {"role": entry["role"], "sha256": digest, "size": entry["size"]}
    if set(expected_payload) != _payload_files(package_root):
        raise FixtureError("fixture_file_set_mismatch")

    for relative, entry in expected_payload.items():
        try:
            data = (package_root / Path(*PurePosixPath(relative).parts)).read_bytes()
        except OSError:
            raise FixtureError("fixture_file_missing") from None
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise FixtureError("fixture_file_digest_mismatch")

    dependency = manifest["dependency_definition"]
    if type(dependency) is not dict or set(dependency) != {"path", "sha256", "format"}:
        raise FixtureError("dependency_definition_schema_mismatch")
    dependency_path = _validate_relative_path(dependency["path"])
    dependency_digest = _validate_sha(dependency["sha256"])
    if dependency["format"] != "json-closed-v1" or dependency_path != "dependencies.json":
        raise FixtureError("dependency_definition_format_mismatch")
    dependency_entry = expected_payload.get(dependency_path)
    if dependency_entry is None or dependency_entry["role"] != "dependency-definition":
        raise FixtureError("dependency_definition_not_bound")
    if dependency_entry["sha256"] != dependency_digest:
        raise FixtureError("dependency_definition_digest_mismatch")
    dependency_data = _load_json(package_root / Path(*PurePosixPath(dependency_path).parts))
    if set(dependency_data) != {"schema", "snapshot_version", "runtime", "packages"}:
        raise FixtureError("dependency_snapshot_schema_mismatch")
    runtime = dependency_data["runtime"]
    if (
        dependency_data["schema"] != "sqag-retrospective-dependency-snapshot-v1"
        or dependency_data["snapshot_version"] != "1.0.0"
        or type(runtime) is not dict
        or set(runtime) != {"implementation", "version"}
        or runtime["implementation"] != "CPython"
        or runtime["version"] != "3.12.13"
        or dependency_data["packages"] != []
    ):
        raise FixtureError("dependency_snapshot_value_mismatch")

    historical_change = manifest["historical_test_change"]
    if type(historical_change) is not dict or set(historical_change) != {
        "path",
        "sha256",
        "decoded_sha256",
        "changed_files",
        "implementation_files_applied",
    }:
        raise FixtureError("historical_change_schema_mismatch")
    patch_path = _validate_relative_path(historical_change["path"])
    patch_entry = expected_payload.get(patch_path)
    if patch_entry is None or patch_entry["role"] != "historical-test-change":
        raise FixtureError("historical_change_not_bound")
    if _validate_sha(historical_change["sha256"]) != patch_entry["sha256"]:
        raise FixtureError("historical_change_digest_mismatch")
    try:
        patch_bytes = base64.b64decode(
            (package_root / Path(*PurePosixPath(patch_path).parts)).read_bytes().strip(),
            validate=True,
        )
    except (OSError, ValueError):
        raise FixtureError("historical_change_encoding_invalid") from None
    if hashlib.sha256(patch_bytes).hexdigest() != _validate_sha(historical_change["decoded_sha256"]):
        raise FixtureError("historical_change_decoded_digest_mismatch")
    patch_targets = tuple(
        line.removeprefix("+++ b/")
        for line in patch_bytes.decode("utf-8").splitlines()
        if line.startswith("+++ b/")
    )
    if tuple(historical_change["changed_files"]) != patch_targets or patch_targets != (
        "tests/test_runtime_privilege_contract.py",
    ):
        raise FixtureError("historical_change_target_mismatch")
    if historical_change["implementation_files_applied"] != 0:
        raise FixtureError("historical_change_scope_mismatch")

    selection = manifest["test_selection"]
    if type(selection) is not list or tuple(item.get("name") for item in selection) != EXPECTED_TEST_SELECTION:
        raise FixtureError("test_selection_mismatch")
    if any(type(item) is not dict or set(item) != {"name", "expected", "category"} for item in selection):
        raise FixtureError("test_selection_schema_mismatch")
    if any(item["expected"] != "failure" or type(item["category"]) is not str for item in selection):
        raise FixtureError("test_selection_contract_mismatch")
    if manifest["expected_result"] != EXPECTED_RESULT:
        raise FixtureError("expected_result_mismatch")

    receipt_schema = manifest["receipt_schema"]
    if type(receipt_schema) is not dict or set(receipt_schema) != {"name", "keys"}:
        raise FixtureError("receipt_schema_mismatch")
    if receipt_schema["name"] != "sqag-retrospective-receipt-v1" or tuple(receipt_schema["keys"]) != RECEIPT_KEYS:
        raise FixtureError("receipt_schema_mismatch")
    return manifest


def _materialise_fixture(manifest: dict[str, Any], package_root: Path, execution_root: Path) -> None:
    for entry in manifest["payload"]:
        relative = PurePosixPath(entry["path"])
        source = package_root / Path(*relative.parts)
        destination = execution_root / Path(*relative.parts)
        try:
            destination.resolve().relative_to(execution_root.resolve())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        except (OSError, ValueError):
            raise FixtureError("fixture_materialisation_failed") from None


class _OutputCollector:
    def __init__(self) -> None:
        self.data = bytearray()
        self.total = 0
        self.overflow = False
        self.lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        with self.lock:
            self.total += len(chunk)
            if self.total > MAX_OUTPUT_BYTES:
                self.overflow = True
                return
            self.data.extend(chunk)


def _drain(stream: Any, collector: _OutputCollector) -> None:
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            collector.append(chunk)
    finally:
        stream.close()


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _execute_selected_tests(execution_root: Path, selection: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    launcher = (
        "import os,sys,unittest;"
        "sys.path.insert(0,os.getcwd());"
        f"names={selection!r};"
        "suite=unittest.TestSuite(unittest.TestLoader().loadTestsFromName(name) for name in names);"
        "result=unittest.TextTestRunner(verbosity=2).run(suite);"
        "raise SystemExit(0 if result.wasSuccessful() else 1)"
    )
    command = [sys.executable, "-I", "-S", "-c", launcher]
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR")
        if key in os.environ
    }
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    try:
        process = subprocess.Popen(
            command,
            cwd=execution_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except OSError:
        raise FixtureError("child_start_failed") from None
    stdout = _OutputCollector()
    stderr = _OutputCollector()
    stdout_thread = threading.Thread(target=_drain, args=(process.stdout, stdout), daemon=True)
    stderr_thread = threading.Thread(target=_drain, args=(process.stderr, stderr), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + TEST_TIMEOUT_SECONDS
    try:
        while process.poll() is None:
            if stdout.overflow or stderr.overflow:
                _kill_process(process)
                raise FixtureError("output_overflow")
            if time.monotonic() >= deadline:
                _kill_process(process)
                raise FixtureError("execution_timeout")
            time.sleep(0.02)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        if stdout.overflow or stderr.overflow:
            raise FixtureError("output_overflow")
        return subprocess.CompletedProcess(command, process.returncode, bytes(stdout.data), bytes(stderr.data))
    finally:
        if process.poll() is None:
            _kill_process(process)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)


def _validate_output_size(stdout: bytes, stderr: bytes) -> None:
    if len(stdout) + len(stderr) > MAX_OUTPUT_BYTES:
        raise FixtureError("output_overflow")


def _validate_test_result(result: subprocess.CompletedProcess[bytes], manifest: dict[str, Any]) -> None:
    if result.returncode < 0:
        raise FixtureError("signal_terminated")
    _validate_output_size(result.stdout, result.stderr)
    try:
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    except Exception:
        raise FixtureError("child_output_decode_failed") from None
    output = output.replace("\r\n", "\n").replace("\r", "\n")

    if result.returncode != EXPECTED_RESULT["exit_status"]:
        raise FixtureError("unexpected_exit_status")
    if "unittest.loader._FailedTest" in output or "ImportError:" in output or "ModuleNotFoundError:" in output:
        raise FixtureError("import_or_collection_error")
    ran_match = re.search(r"^Ran (\d+) tests? in [0-9.]+s$", output, flags=re.MULTILINE)
    failure_match = re.search(r"^FAILED \(([^)]*)\)$", output, flags=re.MULTILINE)
    if ran_match is None or int(ran_match.group(1)) != EXPECTED_RESULT["tests"]:
        raise FixtureError("test_count_mismatch")
    if failure_match is None or failure_match.group(1) != "failures=13":
        raise FixtureError("failure_summary_mismatch")
    if "errors=" in output or "skipped=" in output or "unexpected successes" in output:
        raise FixtureError("non_assertion_result_present")

    records: dict[str, str] = {}
    record_count = 0
    status_pattern = re.compile(r"^(test_[A-Za-z0-9_]+) \(([^)]+)\) \.\.\. (.+)$", flags=re.MULTILINE)
    for match in status_pattern.finditer(output):
        record_count += 1
        records[match.group(2)] = match.group(3).strip()
    selection = tuple(item["name"] for item in manifest["test_selection"])
    if record_count != len(selection) or set(records) != set(selection) or len(records) != len(selection):
        raise FixtureError("test_selection_execution_mismatch")
    if any(records[name] != "FAIL" for name in selection):
        raise FixtureError("unexpected_test_status")
    for item in manifest["test_selection"]:
        if item["category"] not in output:
            raise FixtureError("expected_failure_category_missing")


def _success_receipt(manifest: dict[str, Any]) -> dict[str, Any]:
    expected = manifest["expected_result"]
    return {
        "schema": manifest["receipt_schema"]["name"],
        "status": "passed",
        "error_code": None,
        "fixture_version": manifest["fixture_version"],
        "test_count": expected["tests"],
        "assertion_failures": expected["assertion_failures"],
        "errors": expected["errors"],
        "unexpected_passes": expected["unexpected_passes"],
        "skipped": expected["skipped"],
        "cleanup_verified": True,
        "historical_git_lookup": False,
        "current_dependencies_used": False,
        "child_output_emitted": False,
        "live_system_use": False,
        "secret_bearing_output": False,
    }


def run_reproduction(
    package_root: Path = FIXTURE_ROOT,
    *,
    temp_directory_factory: Callable[..., str] = tempfile.mkdtemp,
) -> dict[str, Any]:
    """Validate, materialise, run, and clean one closed fixture directory."""

    manifest = _validate_fixture(package_root)
    temporary_path: Path | None = None
    try:
        temporary_path = Path(temp_directory_factory(prefix="sqag-run34-fixture-")).resolve()
        if not temporary_path.is_dir():
            raise FixtureError("temporary_directory_create_failed")
        _materialise_fixture(manifest, Path(package_root).resolve(), temporary_path)
        result = _execute_selected_tests(temporary_path, EXPECTED_TEST_SELECTION)
        _validate_test_result(result, manifest)
        return _success_receipt(manifest)
    finally:
        if temporary_path is not None:
            try:
                shutil.rmtree(temporary_path)
            except OSError:
                raise FixtureError("cleanup_failed") from None
            if temporary_path.exists():
                raise FixtureError("cleanup_remnant")


def _failure_receipt(error_code: str) -> dict[str, Any]:
    return {
        "schema": "sqag-retrospective-receipt-v1",
        "status": "failed",
        "error_code": error_code,
        "fixture_version": None,
        "test_count": 0,
        "assertion_failures": 0,
        "errors": 0,
        "unexpected_passes": 0,
        "skipped": 0,
        "cleanup_verified": False,
        "historical_git_lookup": False,
        "current_dependencies_used": False,
        "child_output_emitted": False,
        "live_system_use": False,
        "secret_bearing_output": False,
    }


def main() -> int:
    try:
        receipt = run_reproduction()
    except FixtureError as exc:
        receipt = _failure_receipt(exc.code)
        print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
        return 2
    except Exception:
        print(json.dumps(_failure_receipt("fixture_execution_failed"), sort_keys=True, separators=(",", ":")))
        return 2
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
