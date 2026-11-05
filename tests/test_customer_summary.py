"""Render the real Customer summary with synthetic, offline API responses."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CustomerSummaryTest(unittest.TestCase):
    def test_visible_customer_summary_authority_matrix(self):
        node = shutil.which("node")
        if not node or not (ROOT / "node_modules/playwright/package.json").is_file():
            if os.environ.get("CI"):
                self.fail("Node and Playwright are required for Customer summary regression.")
            self.skipTest("Node or Playwright is unavailable.")
        completed = subprocess.run(
            [node, "scripts/playwright-customer-summary.mjs"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["passed"], [
            "fresh-local", "fresh-workspace", "repeated-entry",
            "saved-authority", "recovered-authority", "pending-selection",
            "out-of-order-profiles",
            "stale-visible-negative-controls",
        ])
        self.assertEqual(result["failed"], [])


if __name__ == "__main__":
    unittest.main()
