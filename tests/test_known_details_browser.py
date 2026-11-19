"""Known details box (#385) in the real UI with a synthetic AI reply: request, Detected booth, resize note,
save/reload restore and re-send. No live provider, live data or paid AI."""
import json
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class KnownDetailsBrowserTest(unittest.TestCase):
    def test_rendered_known_details_box_round_trip(self):
        # unittest discovery is a required CI gate after Chromium installation; a missing
        # Node fails in CI (require_node), like the other rendered basis suites.
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-known-details.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=300,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-4000:])
        evidence = json.loads(result.stdout[result.stdout.index('{'):])
        self.assertEqual(evidence['status'], 'PASS')
        self.assertEqual(evidence['boxLabel'], 'Known details (optional)')
        self.assertIn('Booth size 93 sqm (from Known details)', evidence['detectedBooth'])
        self.assertIn('but the AI analysis used 36 sqm, so 1 full-floor line was resized to match', evidence['resizeNote'])
        self.assertEqual(evidence['carpetQuantity'], 93)
        self.assertEqual(evidence['restoredText'], 'Booth is 93 sqm, 2 open sides\nMax height 4 m')
        self.assertEqual(evidence['restoredBooth']['dimension_source'], 'staff')


if __name__ == '__main__':
    unittest.main()
