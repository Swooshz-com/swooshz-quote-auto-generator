"""Bounded integrity checks for the Run-34 retrospective RED evidence."""

from __future__ import annotations

import base64
import hashlib
import json
import unittest
from pathlib import Path

import scripts.reproduce_run34_red as red_reproduction


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "tests" / "evidence" / "run34-protected-role-edge-evidence.json"
PATCH_PATH = ROOT / "tests" / "evidence" / "run34-exact-starting-head-tests.patch.b64"


class Run34EvidenceIntegrityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))

    def test_retrospective_label_and_immutable_starting_head_are_exact(self) -> None:
        self.assertEqual(
            self.evidence["label"],
            "retrospective exact-starting-head RED reproduction",
        )
        self.assertEqual(self.evidence["starting_head"], red_reproduction.STARTING_HEAD)
        chronology = self.evidence["chronology_classification"]
        self.assertIs(chronology["retrospective"], True)
        self.assertIs(chronology["original_red_chronology"], False)
        self.assertIs(chronology["original_development_sequence"], False)
        self.assertIs(chronology["pre_existing_historical_evidence"], False)

    def test_test_only_patch_digest_and_scope_are_exact(self) -> None:
        patch_bytes = base64.b64decode(PATCH_PATH.read_bytes().strip(), validate=True)
        digest = hashlib.sha256(patch_bytes).hexdigest()
        self.assertEqual(digest, red_reproduction.PATCH_SHA256)
        self.assertEqual(digest, self.evidence["test_only_patch"]["sha256"])
        self.assertEqual(
            self.evidence["test_only_patch"]["changed_files"],
            ["tests/test_runtime_privilege_contract.py"],
        )
        self.assertEqual(self.evidence["test_only_patch"]["implementation_files_applied"], 0)
        patch_text = patch_bytes.decode("utf-8")
        self.assertEqual(
            [line for line in patch_text.splitlines() if line.startswith("+++ b/")],
            ["+++ b/tests/test_runtime_privilege_contract.py"],
        )

    def test_red_test_names_failures_and_observed_result_are_bound(self) -> None:
        expected_short_names = [test_name.split(f"{red_reproduction.TEST_MODULE}.", 1)[1] for test_name in red_reproduction.TESTS]
        self.assertEqual(self.evidence["focused_red"]["tests"], expected_short_names)
        self.assertEqual(
            self.evidence["focused_red"]["expected_failure_categories"],
            list(red_reproduction.EXPECTED_FAILURE_CATEGORIES),
        )
        self.assertEqual(self.evidence["focused_red"]["observed_test_count"], 13)
        self.assertEqual(self.evidence["focused_red"]["observed_assertion_failures"], 13)
        self.assertEqual(self.evidence["focused_red"]["observed_errors"], 0)
        self.assertEqual(self.evidence["focused_red"]["observed_exit_status"], 1)
        self.assertIs(self.evidence["focused_red"]["temporary_worktree_removed"], True)
        self.assertIs(self.evidence["focused_red"]["temporary_worktree_remnant"], False)

    def test_hosted_ci_is_bound_to_exact_pr_head_and_reproduction_script(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        exact_ref = "ref: ${{ github.event.pull_request.head.sha || github.sha }}"
        self.assertEqual(workflow.count(exact_ref), 4)
        self.assertIn("name: Retrospective exact-starting-head RED reproduction", workflow)
        self.assertIn("run: python scripts/reproduce_run34_red.py", workflow)
        self.assertEqual(self.evidence["hosted_ci"]["final_head_binding"], "pull_request.head.sha")

    def test_evidence_declares_public_safe_local_only_boundary(self) -> None:
        self.assertEqual(
            self.evidence["safety"],
            {
                "secret_bearing_output": False,
                "live_system_use": False,
                "provider_api_use": False,
                "deployment_use": False,
                "credential_mutation": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
