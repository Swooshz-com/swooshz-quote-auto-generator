"""Standard service rows and quote exclusions in the real page, through Output and the generated workbook (#363)."""
import json
import os
import re
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class StandardServiceRowsBrowserTest(unittest.TestCase):
    def test_rows_offered_once_reset_to_confirm_kept_through_reanalyse_reopen_and_ticked_row_reaches_the_workbook(self):
        # Reset Draft, also after the Dashboard and Modify, returns both rows to Confirm (INT-SQAG-202611200003).
        # unittest discovery is a required CI gate after Chromium installation; a missing
        # Node fails in CI (require_node), exactly like the other rendered basis suites.
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-standard-service-rows.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=300,
            )
        receipt = re.compile(r'"(?:deferred_pricing|unmatched_pricing_offer|mac)"\s*:\s*"[a-f0-9]{64}"')
        self.assertFalse(bool(receipt.search(result.stdout + result.stderr)), 'Raw pricing receipt in browser output')
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-4000:])
        evidence = json.loads(result.stdout[result.stdout.index('{'):])
        self.assertEqual(evidence['status'], 'PASS')
        self.assertEqual(evidence['standardServiceRows'],
                         {'newQuote': 'PASS', 'tickAndX': 'PASS', 'dashboardModifyReset': 'PASS', 'reset': 'PASS', 'reAnalyse': 'PASS'})
        # Undecided rows say Confirm wherever they show; ticked say Included and X'd say Exclude.
        badges = {}
        for where, (text, _colour, _mark) in evidence['standardServiceBadges'].items():
            badges.setdefault(where.rsplit(':', 1)[0], []).append(text)
        self.assertEqual(badges['new quote'], ['Confirm', 'Confirm'])
        self.assertEqual(badges['reopened undecided'], ['Confirm', 'Confirm'])
        self.assertEqual(badges['reopened decided'], ['Included', 'Exclude'])
        self.assertEqual(badges['Re-Analyse'], ['Included', 'Exclude'])
        self.assertEqual(evidence['standardServiceRowsReopen'], {
            'decisions': [['standard-project-management-fee', 'Include'], ['standard-waste-disposal', 'Exclude']],
            'output': 'included',
        })
        # The ticked row prints once as Included (adding nothing); the crossed row and missing exclusions fail
        # the workbook check.
        self.assertIn({'id': 'standard-project-management-fee', 'description': 'Lump sum for project management',
                       'quantity': '1 nos', 'amount': 'Included'}, evidence['xlsx']['expectedRows'])
        self.assertEqual(json.loads(evidence['xlsx']['validator'])['bad'], [])


if __name__ == '__main__':
    unittest.main()
