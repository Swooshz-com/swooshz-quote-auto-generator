"""Older saved quotes reopen where staff left them (#385), in the real UI with a synthetic AI reply.

A quote saved before the Known details box existed has no known_details in its saved Customer step.
Modify quote opens it at Output with its pricing rows; a real Customer change still reopens at Customer
details. No live provider, live data or paid AI."""
import json
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class OlderQuoteReopenBrowserTest(unittest.TestCase):
    def test_rendered_older_quote_reopens_at_output(self):
        # unittest discovery is a required CI gate after Chromium installation; a missing
        # Node fails in CI (require_node), like the other rendered suites.
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-older-quote-reopen.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=420,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-4000:])
        evidence = json.loads(result.stdout[result.stdout.index('{'):])
        self.assertEqual(evidence['status'], 'PASS')
        self.assertGreaterEqual(len(evidence['generatedRows']), 2)
        for stage in ('currentShape', 'olderShape', 'clearedKnownDetails'):
            self.assertEqual(evidence[stage]['panel'], 'output', stage)
        self.assertEqual(evidence['olderShape']['rows'], evidence['generatedRows'])
        for stage in ('changedCustomer', 'typedKnownDetails'):
            self.assertEqual(evidence[stage]['panel'], 'customer', stage)
            self.assertEqual(evidence[stage]['rows'], [], stage)


if __name__ == '__main__':
    unittest.main()
