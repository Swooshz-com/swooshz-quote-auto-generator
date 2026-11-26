"""Every route to the last decision on a line X'd, saved and ticked back in, in the real UI: what staff last
pressed is what is saved, reopened, priced at Confirm and exported. Synthetic data, no paid AI."""
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class ReinclusionDecisionsBrowserTest(unittest.TestCase):
    def test_rendered_last_decision_is_kept_on_every_route(self):
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-reinclusion-decisions.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=600,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[:4000])
        self.assertIn('"status": "PASS"', result.stdout)


if __name__ == '__main__':
    unittest.main()
