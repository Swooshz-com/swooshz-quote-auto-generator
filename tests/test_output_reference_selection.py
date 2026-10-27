"""Explicit Output reference selection must persist authority before export."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.test_webapp import ROOT, require_node


class OutputReferenceSelectionTest(unittest.TestCase):
    def test_selection_closure_matrix(self):
        completed = subprocess.run(
            [require_node(self), "tests/output_reference_selection_harness.cjs"],
            cwd=ROOT, input="{}", text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"status": "PASS", "cases": 58, "bindingCases": 12})

    def test_rendered_duplicate_output_selection_reopen_and_protected_xlsx(self):
        if not (ROOT / "node_modules/playwright/package.json").exists():
            if os.environ.get("CI"):
                self.fail("Playwright is required for Output selection regression.")
            self.skipTest("Playwright is unavailable.")
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [require_node(self), "scripts/playwright-owner-uat-basis-repair.mjs",
                 "--lines=0", "--latency-ms=0", "--fresh-analysis",
                 "--currency=SGD", "--output-reference-selection"],
                cwd=ROOT, env={**os.environ, "SQAG_OWNER_UAT_EVIDENCE_ROOT": tmp},
                text=True, encoding="utf-8", capture_output=True, timeout=180,
            )
            self.assertEqual(completed.returncode, 0, (completed.stderr or completed.stdout)[-5000:])
            [run] = [path for path in Path(tmp).iterdir() if path.is_dir()]
            result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "PASS")
        proof = result["outputReferenceSelection"]
        self.assertEqual(proof["status"], "PASS")
        self.assertEqual(proof["rows"], 2)
        self.assertEqual(proof["conditionalSelections"], 1)
        for key in ("targetGrantPersisted", "saveReopen", "manualFxAndResetRetained", "originalUnchanged"):
            self.assertIs(proof[key], True)
        self.assertGreater(proof["protectedXlsxBytes"], 1000)
