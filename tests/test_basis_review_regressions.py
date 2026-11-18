"""Quote Basis group blocks: scoped section controls, Detected booth, click latency and blank quantities."""
import json
import os
import re
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class BasisReviewRegressionsTest(unittest.TestCase):
    def test_rendered_group_blocks_booth_latency_and_blank_quantity_catalogue_lines(self):
        # unittest discovery is a required CI gate after Chromium installation; a missing
        # Node fails in CI (require_node), exactly like the other rendered basis suites.
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-basis-review-regressions.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=300,
            )
        receipt = re.compile(r'"(?:deferred_pricing|unmatched_pricing_offer|mac)"\s*:\s*"[a-f0-9]{64}"')
        self.assertFalse(bool(receipt.search(result.stdout + result.stderr)), 'Raw pricing receipt in browser output')
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-4000:])
        evidence = json.loads(result.stdout[result.stdout.index('{'):])
        self.assertEqual(evidence['status'], 'PASS')
        self.assertEqual(evidence['blockScopedActions'], 'PASS')
        lighting = [block for block in evidence['sectionBlocks'] if block.endswith(':Lighting and Electrical')]
        self.assertEqual(lighting, ['Matched:Lighting and Electrical', 'Review:Lighting and Electrical',
                                    'Unmatched:Lighting and Electrical'])
        self.assertIn('Booth size 9m x 10.5m (94.5 sqm)', evidence['detectedBooth']['structured'])
        self.assertEqual(evidence['detectedBooth']['unknownDimensions'], [])
        self.assertGreaterEqual(evidence['latency']['basisLines'], 60)
        reused, probes = evidence['latency']['reusedProbeNodes'].split('/')
        self.assertEqual(reused, probes)
        self.assertEqual(evidence['blankQuantityPriced']['variant'], 'catalog')
        self.assertEqual(evidence['blankQuantityPriced']['unit'], 'sqm')


if __name__ == '__main__':
    unittest.main()
