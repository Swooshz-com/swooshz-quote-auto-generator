"""Step messages show in the bottom-right popup: errors stay until closed, warnings fade (#250).
Synthetic data, no paid AI."""
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class StepMessagesBrowserTest(unittest.TestCase):
    def test_errors_stay_in_the_popup_until_closed_and_warnings_fade(self):
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-step-messages.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=600,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[:4000])
        self.assertIn('"status": "PASS"', result.stdout)


if __name__ == '__main__':
    unittest.main()
