"""Duplicate Quote lands the copy where its source ended, priced for the copy, in the real UI (#445).
Synthetic data, no paid AI."""
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class DuplicateQuoteBrowserTest(unittest.TestCase):
    def test_copy_of_a_finished_quote_opens_at_output_priced(self):
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-duplicate-quote.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=600,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[:4000])
        self.assertIn('"status": "PASS"', result.stdout)


if __name__ == '__main__':
    unittest.main()
