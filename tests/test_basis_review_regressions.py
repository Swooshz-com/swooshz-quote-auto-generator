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
        # Owner-approved display (INT-SQAG-202611200005): every Detected booth variant, at desktop and phone
        # widths, has a heading the size and weight of "Matched" and 15px regular lines, in the same card.
        looks = evidence['detectedBooth']['looks']
        self.assertEqual(sorted(looks), sorted(f'{width}:{variant}' for width in ('desktop', 'phone')
                                              for variant in ('detected', 'knownDetails', 'defaultNote')))
        for key, look in looks.items():
            with self.subTest(key):
                self.assertEqual((look['heading']['fontSize'], look['heading']['fontWeight']),
                                 (look['matchedHeading']['fontSize'], look['matchedHeading']['fontWeight']))
                self.assertTrue(all(line['fontSize'] == 15 and line['fontWeight'] < 700 for line in look['lines']))
                self.assertEqual(look['card'], {'background': 'rgb(247, 251, 255)', 'border': 'rgb(213, 227, 238)',
                                                'padding': '14px 18px', 'radius': '8px'})
                self.assertTrue(look['fits'])
        # "Est." matches its quantity's size and colour (INT-SQAG-202611200004).
        self.assertEqual(evidence['estTag']['est'], evidence['estTag']['quantity'])
        self.assertGreaterEqual(evidence['latency']['basisLines'], 60)
        reused, probes = evidence['latency']['reusedProbeNodes'].split('/')
        self.assertEqual(reused, probes)
        self.assertEqual(evidence['blankQuantityPriced']['variant'], 'catalog')
        self.assertEqual(evidence['blankQuantityPriced']['unit'], 'sqm')
        # Quantity is mandatory: a line with no catalogue match takes a typed quantity and unit.
        self.assertEqual(evidence['freeQuantityLine'], {'quantity': 6, 'unit': 'sqm', 'needsQuantity': False})
        self.assertEqual((evidence['keywordQuantityOutput']['quantity'], evidence['keywordQuantityOutput']['unit']), (5, 'sqm'))
        self.assertEqual((evidence['freeQuantityOutput']['quantity'], evidence['freeQuantityOutput']['unit']), (6, 'sqm'))


if __name__ == '__main__':
    unittest.main()
