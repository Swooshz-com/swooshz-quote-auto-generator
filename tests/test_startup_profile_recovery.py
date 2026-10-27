"""Unavailable pricing references must never overwrite saved quote recovery."""
import json
import subprocess
import unittest

from tests.test_webapp import ROOT, require_node


class StartupProfileRecoveryTest(unittest.TestCase):
    def test_startup_prerequisite_and_persistence_fence(self):
        completed = subprocess.run(
            [require_node(self), "tests/startup_profile_recovery_harness.cjs"],
            cwd=ROOT, text=True, capture_output=True, timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), {"status": "PASS", "cases": 5})
