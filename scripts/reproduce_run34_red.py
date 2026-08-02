"""Reproduce the Run-34 retrospective exact-starting-head RED state.

This verifier creates a detached temporary worktree at the immutable Run-34
starting commit, applies only the recorded test patch, and succeeds only when
the focused tests fail with the accepted assertion-failure categories. It does
not recreate or claim original development chronology.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


STARTING_HEAD = "fa03eca2b0406b864618453f30292c0303f34744"
PATCH_RELATIVE_PATH = Path("tests/evidence/run34-exact-starting-head-tests.patch.b64")
PATCH_SHA256 = "db9e0c9d6df26d8b3de84e9381276b138fbdefcd8be0a37386b200a4c82bb02d"
TEST_MODULE = "tests.test_runtime_privilege_contract"
TESTS = (
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_unrelated_parent_to_sqag_migrator_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_unrelated_parent_to_sqag_app_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_unrelated_parent_to_neon_superuser_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_sqag_migrator_to_unrelated_member_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_sqag_app_to_unrelated_member_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_neon_superuser_to_unrelated_member_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_protected_role_used_as_grantor_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_inherit_true_on_unrelated_parent_protected_member_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_set_true_on_unrelated_parent_protected_member_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_admin_true_on_unauthorised_protected_role_row_is_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_multiple_protected_role_rows_alongside_exact_edge_are_rejected",
    f"{TEST_MODULE}.RuntimeMembershipEdgeEvaluatorTest.test_recursive_protected_role_path_not_beginning_with_runtime_is_rejected",
    f"{TEST_MODULE}.RequirementEvidenceMapTest.test_membership_query_narrative_has_exact_six_field_unfiltered_contract",
)
EXPECTED_FAILURE_CATEGORIES = (
    "protected_role_edge_forbidden",
    "protected_grantor_forbidden",
    "protected_inherit_option_forbidden",
    "protected_set_option_forbidden",
    "protected_admin_option_forbidden",
    "protected_role_row_count_invalid",
    "recursive_protected_role_membership_path",
    "membership-query narrative section is missing",
)


def _run(
    *args: str,
    cwd: Path,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input_text,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(args)}")
    return result


def _patch_targets(patch_text: str) -> tuple[str, ...]:
    return tuple(
        line.removeprefix("+++ b/")
        for line in patch_text.splitlines()
        if line.startswith("+++ b/")
    )


def main() -> int:
    repo_root = Path(
        _run("git", "rev-parse", "--show-toplevel", cwd=Path.cwd()).stdout.strip()
    ).resolve()
    patch_path = repo_root / PATCH_RELATIVE_PATH
    patch_bytes = base64.b64decode(patch_path.read_bytes().strip(), validate=True)
    patch_text = patch_bytes.decode("utf-8")
    patch_digest = hashlib.sha256(patch_bytes).hexdigest()
    if patch_digest != PATCH_SHA256:
        raise RuntimeError(f"test-only patch digest mismatch: {patch_digest}")
    patch_targets = _patch_targets(patch_text)
    if patch_targets != ("tests/test_runtime_privilege_contract.py",):
        raise RuntimeError(f"test-only patch target mismatch: {patch_targets!r}")
    _run("git", "cat-file", "-e", f"{STARTING_HEAD}^{{commit}}", cwd=repo_root)

    worktree_path = Path(tempfile.mkdtemp(prefix="sqag-run34-red-")).resolve()
    worktree_path.rmdir()
    worktree_added = False
    try:
        _run(
            "git",
            "worktree",
            "add",
            "--detach",
            str(worktree_path),
            STARTING_HEAD,
            cwd=repo_root,
        )
        worktree_added = True
        _run("git", "apply", "-", cwd=worktree_path, input_text=patch_text)
        actual_head = _run("git", "rev-parse", "HEAD", cwd=worktree_path).stdout.strip()
        if actual_head != STARTING_HEAD:
            raise RuntimeError(f"detached worktree head mismatch: {actual_head}")
        changed_files = tuple(
            line
            for line in _run("git", "diff", "--name-only", cwd=worktree_path).stdout.splitlines()
            if line
        )
        if changed_files != ("tests/test_runtime_privilege_contract.py",):
            raise RuntimeError(f"implementation files were applied: {changed_files!r}")

        test_result = _run(
            sys.executable,
            "-m",
            "unittest",
            *TESTS,
            cwd=worktree_path,
            check=False,
        )
        output = test_result.stdout + test_result.stderr
        if test_result.returncode != 1:
            raise RuntimeError(f"expected RED exit status 1, got {test_result.returncode}")
        if "ERROR:" in output or "FAILED (failures=13)" not in output or "Ran 13 tests" not in output:
            raise RuntimeError("RED result was not thirteen assertion failures")
        for test_name in TESTS:
            if test_name.rsplit(".", 1)[-1] not in output:
                raise RuntimeError(f"missing expected RED test result: {test_name}")
        for category in EXPECTED_FAILURE_CATEGORIES:
            if category not in output:
                raise RuntimeError(f"missing expected RED failure category: {category}")

        print(
            json.dumps(
                {
                    "label": "retrospective exact-starting-head RED reproduction",
                    "starting_head": STARTING_HEAD,
                    "test_only_patch_sha256": patch_digest,
                    "test_count": len(TESTS),
                    "expected_failure_categories": list(EXPECTED_FAILURE_CATEGORIES),
                    "observed_exit_status": test_result.returncode,
                    "implementation_files_applied": 0,
                    "changed_files": list(changed_files),
                    "secret_bearing_output": False,
                    "live_system_use": False,
                },
                sort_keys=True,
            )
        )
    finally:
        if worktree_added:
            _run("git", "worktree", "remove", "--force", str(worktree_path), cwd=repo_root)
        elif worktree_path.exists():
            shutil.rmtree(worktree_path)
        if worktree_path.exists():
            raise RuntimeError(f"temporary worktree remnant remains: {worktree_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
