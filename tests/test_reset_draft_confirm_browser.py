"""Reset Draft asks first on every step and an exchange rate that is not above 0 is refused, in the real UI (#442).
Synthetic data, no paid AI."""
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class ResetDraftConfirmBrowserTest(unittest.TestCase):
    def test_every_reset_asks_first_and_a_bad_rate_is_refused(self):
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-reset-draft-confirm.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=600,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[:4000])
        self.assertIn('"status": "PASS"', result.stdout)


if __name__ == '__main__':
    unittest.main()
