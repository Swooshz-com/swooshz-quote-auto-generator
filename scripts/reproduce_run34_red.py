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
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "retrospective" / "run34"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
MANIFEST_SHA256 = "0b9cb4740334fe15c93b645c22d3230dae03586f49783bd5f180225c9c6d78e1"
MAX_OUTPUT_BYTES = 64 * 1024
MAX_FIXTURE_FILE_BYTES = 2 * 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024
TEST_TIMEOUT_SECONDS = 30.0
EXPECTED_INTERPRETER = "cpython"
EXPECTED_PYTHON_VERSION = (3, 12, 13)
RETROSPECTIVE_TIMING_MAX_SECONDS = 3600
RETROSPECTIVE_TIMING_MAX_MICROS = RETROSPECTIVE_TIMING_MAX_SECONDS * 1_000_000

EXPECTED_ATTRIBUTION = {
    "provider": "OpenAI",
    "model": "GPT-5.6 Sol",
    "reasoning": "High",
}
MAX_ATTRIBUTION_FIELD_LENGTH = 64

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
    "child_exit_status",
    "timeout",
    "signal_termination",
    "output_overflow",
    "cleanup_verified",
    "historical_git_lookup",
    "current_dependencies_used",
    "child_output_emitted",
    "live_system_use",
    "secret_bearing_output",
)

RECEIPT_ERROR_CODES = frozenset(
    {
        "child_start_failed",
        "child_stream_mismatch",
        "child_output_decode_failed",
        "child_reap_failed",
        "cleanup_failed",
        "cleanup_remnant",
        "dependency_definition_digest_mismatch",
        "dependency_definition_format_mismatch",
        "dependency_definition_not_bound",
        "dependency_definition_schema_mismatch",
        "dependency_snapshot_schema_mismatch",
        "dependency_snapshot_value_mismatch",
        "duplicate_json_key",
        "duplicate_payload_digest",
        "duplicate_payload_path",
        "expected_failure_category_missing",
        "expected_result_mismatch",
        "execution_timeout",
        "failure_summary_mismatch",
        "fixture_entry_forbidden",
        "fixture_enumeration_failed",
        "fixture_execution_failed",
        "fixture_file_changed",
        "fixture_file_digest_mismatch",
        "fixture_file_missing",
        "fixture_file_set_mismatch",
        "fixture_file_size_limit",
        "fixture_json_must_be_object",
        "fixture_materialisation_failed",
        "fixture_root_forbidden",
        "fixture_version_mismatch",
        "historical_change_decoded_digest_mismatch",
        "historical_change_digest_mismatch",
        "historical_change_encoding_invalid",
        "historical_change_not_bound",
        "historical_change_scope_mismatch",
        "historical_change_schema_mismatch",
        "historical_change_target_mismatch",
        "import_or_collection_error",
        "invalid_fixture_digest",
        "invalid_fixture_json",
        "invalid_fixture_path",
        "interpreter_mismatch",
        "manifest_digest_mismatch",
        "manifest_missing",
        "manifest_schema_mismatch",
        "non_assertion_result_present",
        "output_overflow",
        "payload_entry_schema_mismatch",
        "payload_schema_mismatch",
        "payload_size_mismatch",
        "provenance_schema_mismatch",
        "provenance_value_mismatch",
        "reader_shutdown_failed",
        "receipt_internal_validation_failed",
        "receipt_invalid",
        "receipt_schema_mismatch",
        "signal_terminated",
        "test_count_mismatch",
        "test_selection_contract_mismatch",
        "test_selection_execution_mismatch",
        "test_selection_mismatch",
        "test_selection_schema_mismatch",
        "temporary_directory_create_failed",
        "unexpected_exit_status",
        "unexpected_test_status",
        "validated_fixture_required",
    }
)

_REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_FORBIDDEN_RAW_PATH_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)
_PAYLOAD_ROLES = frozenset(
    {
        "dependency-definition",
        "explanation",
        "historical-source-fragment",
        "historical-test-selection",
        "historical-documentation-fragment",
        "historical-test-change",
    }
)


class _ValidatedManifest(dict[str, Any]):
    """Manifest mapping plus the immutable bytes verified for its payload."""

    def __init__(self, values: dict[str, Any], payload_bytes: dict[str, bytes]) -> None:
        super().__init__(values)
        self.payload_bytes = MappingProxyType(dict(payload_bytes))


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


def _load_json_bytes(data: bytes, error_code: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError, FixtureError):
        raise FixtureError(error_code) from None
    if type(value) is not dict:
        raise FixtureError("fixture_json_must_be_object")
    return value


def _safe_lstat(path: Path, error_code: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError:
        raise FixtureError(error_code) from None
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if stat.S_ISLNK(metadata.st_mode) or attributes & _REPARSE_POINT_ATTRIBUTE:
        raise FixtureError(error_code)
    return metadata


def _validate_ancestor_chain(path: Path, error_code: str) -> None:
    absolute = Path(os.path.abspath(path))
    current = absolute
    ancestors: list[Path] = []
    while True:
        ancestors.append(current)
        if current == current.parent:
            break
        current = current.parent
    for ancestor in reversed(ancestors):
        metadata = _safe_lstat(ancestor, error_code)
        if not stat.S_ISDIR(metadata.st_mode):
            raise FixtureError(error_code)


def _read_verified_bytes(
    path: Path,
    *,
    expected_size: int | None,
    expected_digest: str | None,
    missing_code: str,
) -> bytes:
    _validate_ancestor_chain(path.parent, "fixture_entry_forbidden")
    before = _safe_lstat(path, missing_code)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise FixtureError("fixture_entry_forbidden")
    if before.st_size > MAX_FIXTURE_FILE_BYTES:
        raise FixtureError("fixture_file_size_limit")
    flags = (
        os.O_RDONLY
        | int(getattr(os, "O_BINARY", 0))
        | int(getattr(os, "O_NOFOLLOW", 0))
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise FixtureError(missing_code) from None
    try:
        opened = os.fstat(descriptor)
        opened_attributes = int(getattr(opened, "st_file_attributes", 0))
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened_attributes & _REPARSE_POINT_ATTRIBUTE
            or opened.st_size > MAX_FIXTURE_FILE_BYTES
            or opened.st_size != before.st_size
            or (before.st_ino and opened.st_ino != before.st_ino)
            or (before.st_dev and opened.st_dev != before.st_dev)
        ):
            raise FixtureError("fixture_file_changed")
        _validate_ancestor_chain(path.parent, "fixture_file_changed")
        visible = _safe_lstat(path, "fixture_file_changed")
        if (
            not stat.S_ISREG(visible.st_mode)
            or visible.st_nlink != 1
            or (opened.st_ino and visible.st_ino != opened.st_ino)
            or (opened.st_dev and visible.st_dev != opened.st_dev)
        ):
            raise FixtureError("fixture_file_changed")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FIXTURE_FILE_BYTES:
                raise FixtureError("fixture_file_size_limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except FixtureError:
        raise
    except OSError:
        raise FixtureError("fixture_file_changed") from None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    data = b"".join(chunks)
    _validate_ancestor_chain(path.parent, "fixture_file_changed")
    visible = _safe_lstat(path, "fixture_file_changed")
    after_attributes = int(getattr(after, "st_file_attributes", 0))
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_nlink != 1
        or after_attributes & _REPARSE_POINT_ATTRIBUTE
        or after.st_size != total
        or after.st_size != before.st_size
        or (before.st_ino and after.st_ino != before.st_ino)
        or (before.st_dev and after.st_dev != before.st_dev)
        or not stat.S_ISREG(visible.st_mode)
        or visible.st_nlink != 1
        or (after.st_ino and visible.st_ino != after.st_ino)
        or (after.st_dev and visible.st_dev != after.st_dev)
    ):
        raise FixtureError("fixture_file_changed")
    if expected_size is not None and len(data) != expected_size:
        raise FixtureError("fixture_file_digest_mismatch")
    if expected_digest is not None and hashlib.sha256(data).hexdigest() != expected_digest:
        raise FixtureError("fixture_file_digest_mismatch")
    return data


def _validate_relative_path(value: Any) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise FixtureError("invalid_fixture_path")
    if (
        value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or "\\" in value
        or ":" in value
        or unicodedata.normalize("NFC", value) != value
        or any(unicodedata.category(character) in _FORBIDDEN_RAW_PATH_CATEGORIES for character in value)
    ):
        raise FixtureError("invalid_fixture_path")
    components = value.split("/")
    if any(
        not component
        or component in {".", ".."}
        or component.endswith((".", " "))
        or component.rstrip(" .") != component
        for component in components
    ):
        raise FixtureError("invalid_fixture_path")
    for component in components:
        device_name = component.split(".", 1)[0].rstrip(" .").casefold().upper()
        if device_name in _WINDOWS_RESERVED_NAMES:
            raise FixtureError("invalid_fixture_path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise FixtureError("invalid_fixture_path")
    return value


def _validate_sha(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise FixtureError("invalid_fixture_digest")
    return value


def _payload_files(package_root: Path) -> set[str]:
    actual: set[str] = set()
    def visit(directory: Path, prefix: str) -> None:
        metadata = _safe_lstat(directory, "fixture_entry_forbidden")
        if not stat.S_ISDIR(metadata.st_mode):
            raise FixtureError("fixture_entry_forbidden")
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError:
            raise FixtureError("fixture_enumeration_failed") from None
        for entry in entries:
            candidate = Path(entry.path)
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            metadata = _safe_lstat(candidate, "fixture_entry_forbidden")
            if stat.S_ISDIR(metadata.st_mode):
                visit(candidate, relative)
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise FixtureError("fixture_entry_forbidden")
            if relative != "manifest.json":
                actual.add(relative)

    visit(package_root, "")
    return actual


def _validate_fixture(package_root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    package_root = Path(os.path.abspath(package_root))
    _validate_ancestor_chain(package_root, "fixture_root_forbidden")
    manifest_path = package_root / "manifest.json"
    manifest_bytes = _read_verified_bytes(
        manifest_path,
        expected_size=None,
        expected_digest=None,
        missing_code="manifest_missing",
    )
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA256:
        raise FixtureError("manifest_digest_mismatch")
    manifest = _load_json_bytes(manifest_bytes, "invalid_fixture_json")

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
        "attribution",
        "retrospective",
        "original_red_chronology",
        "original_development_sequence",
    }:
        raise FixtureError("provenance_schema_mismatch")
    attribution = provenance["attribution"]
    if type(attribution) is not dict or set(attribution) != set(EXPECTED_ATTRIBUTION):
        raise FixtureError("provenance_schema_mismatch")
    if any(
        type(attribution[key]) is not str
        or not attribution[key].strip()
        or len(attribution[key]) > MAX_ATTRIBUTION_FIELD_LENGTH
        for key in EXPECTED_ATTRIBUTION
    ):
        raise FixtureError("provenance_schema_mismatch")
    if attribution != EXPECTED_ATTRIBUTION:
        raise FixtureError("provenance_value_mismatch")
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
    canonical_paths: set[str] = set()
    digest_paths: dict[str, tuple[str, int]] = {}
    for entry in payload:
        if type(entry) is not dict or set(entry) != {"path", "role", "sha256", "size"}:
            raise FixtureError("payload_entry_schema_mismatch")
        if type(entry["role"]) is not str or entry["role"] not in _PAYLOAD_ROLES:
            raise FixtureError("payload_entry_schema_mismatch")
        path = _validate_relative_path(entry["path"])
        digest = _validate_sha(entry["sha256"])
        if type(entry["size"]) is not int or entry["size"] < 0:
            raise FixtureError("payload_size_mismatch")
        if path in expected_payload:
            raise FixtureError("duplicate_payload_path")
        canonical_path = unicodedata.normalize("NFC", path).casefold()
        if canonical_path in canonical_paths:
            raise FixtureError("duplicate_payload_path")
        canonical_paths.add(canonical_path)
        if digest in digest_paths:
            raise FixtureError("duplicate_payload_digest")
        digest_paths[digest] = (path, entry["size"])
        expected_payload[path] = {"role": entry["role"], "sha256": digest, "size": entry["size"]}
    if set(expected_payload) != _payload_files(package_root):
        raise FixtureError("fixture_file_set_mismatch")

    payload_bytes: dict[str, bytes] = {}
    for relative, entry in expected_payload.items():
        payload_bytes[relative] = _read_verified_bytes(
            package_root / Path(*relative.split("/")),
            expected_size=entry["size"],
            expected_digest=entry["sha256"],
            missing_code="fixture_file_missing",
        )

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
    dependency_data = _load_json_bytes(payload_bytes[dependency_path], "invalid_fixture_json")
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
    if type(historical_change["changed_files"]) is not list or any(
        type(path) is not str for path in historical_change["changed_files"]
    ):
        raise FixtureError("historical_change_schema_mismatch")
    patch_path = _validate_relative_path(historical_change["path"])
    patch_entry = expected_payload.get(patch_path)
    if patch_entry is None or patch_entry["role"] != "historical-test-change":
        raise FixtureError("historical_change_not_bound")
    if _validate_sha(historical_change["sha256"]) != patch_entry["sha256"]:
        raise FixtureError("historical_change_digest_mismatch")
    try:
        patch_bytes = base64.b64decode(
            payload_bytes[patch_path].strip(),
            validate=True,
        )
    except (KeyError, ValueError):
        raise FixtureError("historical_change_encoding_invalid") from None
    if hashlib.sha256(patch_bytes).hexdigest() != _validate_sha(historical_change["decoded_sha256"]):
        raise FixtureError("historical_change_decoded_digest_mismatch")
    try:
        patch_text = patch_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise FixtureError("historical_change_encoding_invalid") from None
    patch_targets = tuple(
        line.removeprefix("+++ b/")
        for line in patch_text.splitlines()
        if line.startswith("+++ b/")
    )
    if tuple(historical_change["changed_files"]) != patch_targets or patch_targets != (
        "tests/test_runtime_privilege_contract.py",
    ):
        raise FixtureError("historical_change_target_mismatch")
    if type(historical_change["implementation_files_applied"]) is not int or historical_change["implementation_files_applied"] != 0:
        raise FixtureError("historical_change_scope_mismatch")

    selection = manifest["test_selection"]
    if type(selection) is not list or any(type(item) is not dict for item in selection):
        raise FixtureError("test_selection_schema_mismatch")
    if tuple(item.get("name") for item in selection) != EXPECTED_TEST_SELECTION:
        raise FixtureError("test_selection_mismatch")
    if any(set(item) != {"name", "expected", "category"} for item in selection):
        raise FixtureError("test_selection_schema_mismatch")
    if any(item["expected"] != "failure" or type(item["category"]) is not str for item in selection):
        raise FixtureError("test_selection_contract_mismatch")
    expected_result = manifest["expected_result"]
    if (
        type(expected_result) is not dict
        or set(expected_result) != set(EXPECTED_RESULT)
        or any(type(value) is not int or value < 0 for value in expected_result.values())
        or expected_result != EXPECTED_RESULT
    ):
        raise FixtureError("expected_result_mismatch")

    receipt_schema = manifest["receipt_schema"]
    if (
        type(receipt_schema) is not dict
        or set(receipt_schema) != {"name", "keys"}
        or type(receipt_schema["keys"]) is not list
        or any(type(key) is not str for key in receipt_schema["keys"])
    ):
        raise FixtureError("receipt_schema_mismatch")
    if receipt_schema["name"] != "sqag-retrospective-receipt-v1" or tuple(receipt_schema["keys"]) != RECEIPT_KEYS:
        raise FixtureError("receipt_schema_mismatch")
    return _ValidatedManifest(manifest, payload_bytes)


def _materialise_fixture(manifest: dict[str, Any], package_root: Path, execution_root: Path) -> None:
    del package_root
    if not isinstance(manifest, _ValidatedManifest):
        raise FixtureError("validated_fixture_required")
    execution_root = Path(os.path.abspath(execution_root))
    _validate_ancestor_chain(execution_root, "fixture_materialisation_failed")
    root_resolved = execution_root.resolve()
    for relative, data in manifest.payload_bytes.items():
        destination = execution_root.joinpath(*relative.split("/"))
        descriptor: int | None = None
        created = False
        materialised = False
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _validate_ancestor_chain(destination.parent, "fixture_materialisation_failed")
            destination.parent.resolve().relative_to(root_resolved)
            descriptor = os.open(
                destination,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | int(getattr(os, "O_BINARY", 0)),
                0o600,
            )
            created = True
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset:])
                if written <= 0:
                    raise FixtureError("fixture_materialisation_failed")
                offset += written
            metadata = os.fstat(descriptor)
            attributes = int(getattr(metadata, "st_file_attributes", 0))
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or attributes & _REPARSE_POINT_ATTRIBUTE
                or metadata.st_size != len(data)
            ):
                raise FixtureError("fixture_materialisation_failed")
            materialised = True
        except FixtureError:
            raise
        except (OSError, ValueError):
            raise FixtureError("fixture_materialisation_failed") from None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if created and not materialised:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass


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


_TERMINATE_WAIT_SECONDS = 1.0
_KILL_WAIT_SECONDS = 5.0
_FINAL_WAIT_SECONDS = 1.0


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=_TERMINATE_WAIT_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=_KILL_WAIT_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=_FINAL_WAIT_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        raise FixtureError("child_reap_failed") from None


def _child_environment() -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR")
        if key in os.environ
    }
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    return environment


def _join_reader_threads(threads: tuple[threading.Thread, threading.Thread]) -> None:
    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        raise FixtureError("reader_shutdown_failed")


def _execute_child(
    command: list[str],
    execution_root: Path,
    *,
    timeout_seconds: float = TEST_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    try:
        process = subprocess.Popen(
            command,
            cwd=execution_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_child_environment(),
        )
    except OSError:
        raise FixtureError("child_start_failed") from None
    stdout = _OutputCollector()
    stderr = _OutputCollector()
    stdout_thread = threading.Thread(target=_drain, args=(process.stdout, stdout), daemon=True)
    stderr_thread = threading.Thread(target=_drain, args=(process.stderr, stderr), daemon=True)
    reader_threads = (stdout_thread, stderr_thread)
    stdout_thread.start()
    stderr_thread.start()
    deadline = time.monotonic() + timeout_seconds
    try:
        while process.poll() is None:
            if stdout.overflow or stderr.overflow:
                _kill_process(process)
                raise FixtureError("output_overflow")
            if time.monotonic() >= deadline:
                _kill_process(process)
                raise FixtureError("execution_timeout")
            time.sleep(0.02)
        _join_reader_threads(reader_threads)
        if stdout.overflow or stderr.overflow:
            raise FixtureError("output_overflow")
        return subprocess.CompletedProcess(command, process.returncode, bytes(stdout.data), bytes(stderr.data))
    finally:
        if process.poll() is None:
            try:
                _kill_process(process)
            except FixtureError:
                pass
        _join_reader_threads(reader_threads)


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
    return _execute_child(command, execution_root)


def _validate_output_size(stdout: bytes, stderr: bytes) -> None:
    if len(stdout) + len(stderr) > MAX_OUTPUT_BYTES:
        raise FixtureError("output_overflow")


def _validate_interpreter(manifest: dict[str, Any]) -> None:
    dependency = manifest["dependency_definition"]
    dependency_data = _load_json_bytes(manifest.payload_bytes[dependency["path"]], "invalid_fixture_json")
    runtime = dependency_data["runtime"]
    implementation = getattr(getattr(sys, "implementation", None), "name", None)
    try:
        version = tuple(sys.version_info[:3])
    except (AttributeError, TypeError):
        version = ()
    if (
        implementation != EXPECTED_INTERPRETER
        or version != EXPECTED_PYTHON_VERSION
        or runtime.get("implementation") != "CPython"
        or runtime.get("version") != "3.12.13"
    ):
        raise FixtureError("interpreter_mismatch")


def _receipt_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    try:
        return _strict_object(pairs)
    except FixtureError:
        raise FixtureError("receipt_invalid") from None


def _parse_receipt(value: bytes | str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise FixtureError("receipt_invalid") from None
    elif isinstance(value, bytes):
        encoded = value
    else:
        raise FixtureError("receipt_invalid")
    if not encoded or len(encoded) > MAX_RECEIPT_BYTES:
        raise FixtureError("receipt_invalid")
    try:
        line = encoded.decode("utf-8")
    except UnicodeDecodeError:
        raise FixtureError("receipt_invalid") from None
    if line != line.strip() or "\n" in line or "\r" in line:
        raise FixtureError("receipt_invalid")
    try:
        parsed = json.loads(line, object_pairs_hook=_receipt_object)
    except (json.JSONDecodeError, FixtureError):
        raise FixtureError("receipt_invalid") from None
    if type(parsed) is not dict or tuple(parsed) != RECEIPT_KEYS:
        raise FixtureError("receipt_invalid")
    integer_fields = (
        "test_count",
        "assertion_failures",
        "errors",
        "unexpected_passes",
        "skipped",
    )
    boolean_fields = (
        "timeout",
        "signal_termination",
        "output_overflow",
        "cleanup_verified",
        "historical_git_lookup",
        "current_dependencies_used",
        "child_output_emitted",
        "live_system_use",
        "secret_bearing_output",
    )
    if any(type(parsed[field]) is not int or parsed[field] < 0 for field in integer_fields):
        raise FixtureError("receipt_invalid")
    if any(type(parsed[field]) is not bool for field in boolean_fields):
        raise FixtureError("receipt_invalid")
    if parsed["schema"] != "sqag-retrospective-receipt-v1" or type(parsed["schema"]) is not str:
        raise FixtureError("receipt_invalid")
    if type(parsed["status"]) is not str or parsed["status"] not in {"passed", "failed"}:
        raise FixtureError("receipt_invalid")
    if parsed["child_exit_status"] is not None and type(parsed["child_exit_status"]) is not int:
        raise FixtureError("receipt_invalid")
    if parsed["status"] == "passed":
        if (
            parsed["error_code"] is not None
            or parsed["fixture_version"] != "1.0.0"
            or parsed["child_exit_status"] != EXPECTED_RESULT["exit_status"]
            or parsed["test_count"] != EXPECTED_RESULT["tests"]
            or parsed["assertion_failures"] != EXPECTED_RESULT["assertion_failures"]
            or parsed["errors"] != EXPECTED_RESULT["errors"]
            or parsed["unexpected_passes"] != EXPECTED_RESULT["unexpected_passes"]
            or parsed["skipped"] != EXPECTED_RESULT["skipped"]
            or parsed["cleanup_verified"] is not True
            or any(parsed[field] for field in boolean_fields if field != "cleanup_verified")
        ):
            raise FixtureError("receipt_invalid")
    else:
        if (
            type(parsed["error_code"]) is not str
            or parsed["error_code"] not in RECEIPT_ERROR_CODES
            or parsed["fixture_version"] is not None
            or parsed["child_exit_status"] is not None
            or any(parsed[field] != 0 for field in integer_fields)
            or parsed["cleanup_verified"] is not False
            or parsed["historical_git_lookup"]
            or parsed["current_dependencies_used"]
            or parsed["child_output_emitted"]
            or parsed["live_system_use"]
            or parsed["secret_bearing_output"]
        ):
            raise FixtureError("receipt_invalid")
        expected_posture = {
            "timeout": parsed["error_code"] == "execution_timeout",
            "signal_termination": parsed["error_code"] == "signal_terminated",
            "output_overflow": parsed["error_code"] == "output_overflow",
        }
        if any(parsed[field] != expected_posture[field] for field in expected_posture):
            raise FixtureError("receipt_invalid")
    return parsed


def _serialise_receipt(receipt: dict[str, Any]) -> str:
    return json.dumps(receipt, ensure_ascii=True, separators=(",", ":"))


def _normalise_child_channel(value: bytes) -> str:
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        raise FixtureError("child_output_decode_failed") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_normalised_output(text: str) -> list[str]:
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def _bounded_ascii_line(line: str, maximum: int) -> bool:
    return bool(line) and len(line) <= maximum and all(0x20 <= ord(character) <= 0x7E for character in line)


_TIMING_INTEGER_WIDTH = len(str(RETROSPECTIVE_TIMING_MAX_SECONDS))
_TIMING_INTEGER_PATTERN = r"[1-9][0-9]{0,%d}" % (_TIMING_INTEGER_WIDTH - 1)
_TIMING_FULL_PATTERN = re.compile(r"^(?:0|%s)(?:\.[0-9]{1,6})?$" % _TIMING_INTEGER_PATTERN)


def _parse_bounded_timing_micros(value: str) -> int | None:
    """Parse a bounded fixed-point timing value into microseconds.

    Returns the microsecond value when the input is a canonical unsigned
    bounded fixed-point decimal, or None when the input is malformed or above
    the retrospective timing maximum.
    """
    if _TIMING_FULL_PATTERN.fullmatch(value) is None:
        return None
    integer_part, separator, fraction_part = value.partition(".")
    integer = int(integer_part)
    if separator:
        fraction_micros = int(fraction_part.ljust(6, "0"))
    else:
        fraction_micros = 0
    micros = integer * 1_000_000 + fraction_micros
    if micros > RETROSPECTIVE_TIMING_MAX_MICROS:
        return None
    return micros


def _validate_complete_child_stream(stdout: str, stderr: str, manifest: dict[str, Any]) -> None:
    if stdout:
        raise FixtureError("child_stream_mismatch")

    lines = _split_normalised_output(stderr)
    selection = tuple(item["name"] for item in manifest["test_selection"])
    categories = tuple(item["category"] for item in manifest["test_selection"])
    expected = manifest["expected_result"]
    record_pattern = re.compile(r"^(test_[A-Za-z0-9_]+) \(([^()\r\n]+)\) \.\.\. (.+)$")
    header_pattern = re.compile(r"^FAIL: (test_[A-Za-z0-9_]+) \(([^()\r\n]+)\)$")
    cursor = 0
    test_source_paths = tuple(entry["path"] for entry in manifest["payload"] if entry.get("role") == "historical-test-selection")
    if len(test_source_paths) != 1:
        raise FixtureError("test_selection_schema_mismatch")
    try:
        source_text = manifest.payload_bytes[test_source_paths[0]].decode("utf-8")
    except (AttributeError, KeyError, UnicodeDecodeError):
        raise FixtureError("test_selection_schema_mismatch") from None
    source_all_lines = source_text.splitlines()
    source_line_count = len(source_all_lines)
    source_lines = frozenset(
        line.lstrip(" ")
        for line in source_all_lines
        if line.strip()
    )
    source_lines_by_number = {
        number: line.lstrip(" ")
        for number, line in enumerate(source_all_lines, start=1)
        if line.strip()
    }
    frame_line_width = len(str(source_line_count))
    frame_pattern = re.compile(
        r'^  File "([^"\r\n]+)", line ([1-9][0-9]{0,%d}), in (?:[A-Za-z_][A-Za-z0-9_]*|<[^>\r\n]+>)$'
        % (frame_line_width - 1)
    )
    for name in selection:
        if cursor >= len(lines):
            raise FixtureError("child_stream_mismatch")
        match = record_pattern.fullmatch(lines[cursor])
        if match is None:
            raise FixtureError("child_stream_mismatch")
        if match.group(2) != name or match.group(1) != name.rsplit(".", 1)[1]:
            raise FixtureError("child_stream_mismatch")
        if match.group(3) != "FAIL":
            raise FixtureError("unexpected_test_status")
        cursor += 1

    if cursor >= len(lines) or lines[cursor] != "":
        raise FixtureError("child_stream_mismatch")
    cursor += 1

    for index, name in enumerate(selection):
        if cursor >= len(lines) or lines[cursor] != "=" * 70:
            raise FixtureError("child_stream_mismatch")
        cursor += 1
        if cursor >= len(lines):
            raise FixtureError("child_stream_mismatch")
        header = header_pattern.fullmatch(lines[cursor])
        if header is None or header.group(2) != name or header.group(1) != name.rsplit(".", 1)[1]:
            raise FixtureError("child_stream_mismatch")
        cursor += 1
        if cursor >= len(lines) or lines[cursor] != "-" * 70:
            raise FixtureError("child_stream_mismatch")
        cursor += 1
        if cursor >= len(lines) or lines[cursor] != "Traceback (most recent call last):":
            raise FixtureError("child_stream_mismatch")
        cursor += 1

        frame_count = 1 if index == len(selection) - 1 else 2
        frame_suffixes = [test_source_paths[0]] * frame_count
        expected_category = categories[index]
        for frame_index, suffix in enumerate(frame_suffixes):
            if cursor >= len(lines):
                raise FixtureError("child_stream_mismatch")
            frame = frame_pattern.fullmatch(lines[cursor])
            if frame is None or not frame.group(1).replace("\\", "/").endswith(suffix):
                raise FixtureError("child_stream_mismatch")
            line_number = int(frame.group(2))
            if line_number < 1 or line_number > source_line_count:
                raise FixtureError("child_stream_mismatch")
            cursor += 1
            if cursor >= len(lines):
                raise FixtureError("child_stream_mismatch")
            source = lines[cursor]
            if not source.startswith("    ") or not _bounded_ascii_line(source, 256):
                raise FixtureError("child_stream_mismatch")
            if frame_index == 0 and (
                expected_category not in source
                or any(category != expected_category and category in source for category in categories)
            ):
                raise FixtureError("expected_failure_category_missing")
            if source.lstrip(" ") not in source_lines:
                raise FixtureError("child_stream_mismatch")
            if source_lines_by_number.get(line_number) != source.lstrip(" "):
                raise FixtureError("child_stream_mismatch")
            cursor += 1

        if cursor >= len(lines):
            raise FixtureError("child_stream_mismatch")
        assertion = lines[cursor]
        if not assertion.startswith("AssertionError: ") or not _bounded_ascii_line(assertion, 512):
            raise FixtureError("child_stream_mismatch")
        if expected_category not in assertion or any(
            category != expected_category and category in assertion for category in categories
        ):
            raise FixtureError("expected_failure_category_missing")
        cursor += 1
        if cursor >= len(lines) or lines[cursor] != "":
            raise FixtureError("child_stream_mismatch")
        cursor += 1

    if cursor >= len(lines) or lines[cursor] != "-" * 70:
        raise FixtureError("child_stream_mismatch")
    cursor += 1
    if cursor >= len(lines):
        raise FixtureError("test_count_mismatch")
    count_marker = re.fullmatch(r"Ran ([0-9]+) tests? in (.+)s", lines[cursor])
    if count_marker is None or int(count_marker.group(1)) != expected["tests"]:
        raise FixtureError("test_count_mismatch")
    if _parse_bounded_timing_micros(count_marker.group(2)) is None:
        raise FixtureError("child_stream_mismatch")
    cursor += 1
    if cursor >= len(lines) or lines[cursor] != "":
        raise FixtureError("child_stream_mismatch")
    cursor += 1
    summary = f"FAILED (failures={expected['assertion_failures']})"
    if cursor >= len(lines) or lines[cursor] != summary:
        raise FixtureError("failure_summary_mismatch")
    cursor += 1
    if cursor != len(lines):
        raise FixtureError("child_stream_mismatch")


def _validate_test_result(result: subprocess.CompletedProcess[bytes], manifest: dict[str, Any]) -> None:
    if result.returncode < 0:
        raise FixtureError("signal_terminated")
    _validate_output_size(result.stdout, result.stderr)
    stdout = _normalise_child_channel(result.stdout)
    stderr = _normalise_child_channel(result.stderr)

    if result.returncode != manifest["expected_result"]["exit_status"]:
        raise FixtureError("unexpected_exit_status")
    if stdout:
        raise FixtureError("child_stream_mismatch")
    if "unittest.loader._FailedTest" in stderr or "ImportError:" in stderr or "ModuleNotFoundError:" in stderr:
        raise FixtureError("import_or_collection_error")
    _validate_complete_child_stream(stdout, stderr, manifest)


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
        "child_exit_status": expected["exit_status"],
        "timeout": False,
        "signal_termination": False,
        "output_overflow": False,
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
    _validate_interpreter(manifest)
    temporary_path: Path | None = None
    try:
        temporary_path = Path(temp_directory_factory(prefix="sqag-run34-fixture-")).resolve()
        if not temporary_path.is_dir():
            raise FixtureError("temporary_directory_create_failed")
        _materialise_fixture(manifest, package_root, temporary_path)
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
    posture = {
        "timeout": error_code == "execution_timeout",
        "signal_termination": error_code == "signal_terminated",
        "output_overflow": error_code == "output_overflow",
    }
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
        "child_exit_status": None,
        "timeout": posture["timeout"],
        "signal_termination": posture["signal_termination"],
        "output_overflow": posture["output_overflow"],
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
        pass
    except Exception:
        receipt = _failure_receipt("fixture_execution_failed")
    try:
        parsed = _parse_receipt(_serialise_receipt(receipt))
    except FixtureError:
        parsed = _failure_receipt("receipt_internal_validation_failed")
    print(_serialise_receipt(parsed))
    return 0 if parsed["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
