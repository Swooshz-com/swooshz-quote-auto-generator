"""Lines ticked back in on a saved quote, in the real UI: the quote keeps saving, the tick survives Dashboard,
leaving, tab close and another browser, and only Confirm restores the price. Synthetic data, no paid AI."""
import json
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class ReincludedLinesBrowserTest(unittest.TestCase):
    def test_rendered_reincluded_lines_save_reopen_confirm_and_refusal(self):
        # unittest discovery is a required CI gate after Chromium installation; a missing
        # Node fails in CI (require_node), like the other rendered suites.
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-reincluded-lines.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=300,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-4000:])
        evidence = json.loads(result.stdout[result.stdout.index('{'):])
        self.assertEqual(evidence['status'], 'PASS')
        for step in ('dashboard', 'exportHeld', 'leaving', 'tabClose'):
            self.assertEqual(evidence[step], 'PASS', step)
        self.assertEqual({row_id: row['price'] for row_id, row in evidence['confirmed'].items()},
                         {'re-carpet': 14.4, 're-vinyl': 10.4, 're-graphic': 21, 're-banner': 21.6})
        self.assertEqual(evidence['confirmed']['re-graphic']['quantity'], 14)
        self.assertIn('The old price could not be restored', evidence['refusal'])
        self.assertTrue(evidence['refusedRowBlocksExport'])
        self.assertEqual(evidence['unsavedDialog']['title'], 'Quote not saved')
        self.assertEqual(len(evidence['staleNext']), 2)


if __name__ == '__main__':
    unittest.main()
