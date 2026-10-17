import copy
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from webapp import server


ROOT = Path(__file__).resolve().parents[1]


class CustomerCheckpointTests(unittest.TestCase):
    def test_production_navigation_save_and_restore_controls_without_provider(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for the production navigation controls")
        result = subprocess.run(
            [node, "tests/customer_checkpoint_controls.cjs"],
            cwd=ROOT, text=True, capture_output=True, timeout=60, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        proof = json.loads(result.stdout)
        self.assertEqual(proof["provider_requests"], 0)
        self.assertGreaterEqual(len(proof["controls"]), 16)
        print(result.stdout, end="")

    def test_checkpoint_survives_production_draft_sanitization(self):
        checkpoint = {
            "version": 1, "sessionId": "quote-synthetic-checkpoint",
            "recoveryScope": "synthetic-scope",
            "pricingReferenceId": "synthetic-reference", "pricingReferenceSource": "company",
            "pricingReferenceDigest": "sha256:" + "a" * 64,
            "customer": {"client": {"name": "Synthetic Customer", "address": "A" * 4000},
                         "project": {"title": "Synthetic Exhibition", "show_name": "Synthetic Show"},
                         "quote_date": "2026-10-13", "project_number": "Synthetic-01"},
        }
        draft = {"customerStepCheckpoint": copy.deepcopy(checkpoint)}
        saved = server.quote_session_draft_state_value(draft)
        restored = json.loads(json.dumps(saved, sort_keys=True))
        self.assertEqual(restored["customerStepCheckpoint"], checkpoint)
        self.assertEqual(draft["customerStepCheckpoint"], checkpoint)
