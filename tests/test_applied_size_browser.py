"""Known details Apply with a floor area, in the real UI through Confirm to the workbook: no quote line changes what it
reads as, and the booth profile states the applied size. Synthetic data, no paid AI."""
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class AppliedSizeBrowserTest(unittest.TestCase):
    def test_rendered_apply_keeps_quantities_through_to_the_workbook(self):
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-applied-size.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=240,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[:4000])
        self.assertIn('"status": "PASS"', result.stdout)


if __name__ == '__main__':
    unittest.main()
