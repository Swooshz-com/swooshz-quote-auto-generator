"""Re on one line in the real UI: Apply reaches the quote row (and a Re detail on a catalogue line prints after its name), so Confirm, the saved quote, a reopen, a second
Confirm and the workbook all read the new wording; a typed price and Included stay, a catalogue line turned into
another item asks for a price, and every other row is unchanged. The price rule's full table runs on the page's
own functions and on the server's check. Synthetic data, mocked AI reply, no paid AI."""
import json
import os
import subprocess
import unittest

from tests.rendered_browser_support import owned_browser_evidence_directory
from tests import test_webapp as fixtures


class ReviseApplyBrowserTest(unittest.TestCase):
    def test_rendered_revise_apply_reaches_output_saved_quote_and_workbook(self):
        # unittest discovery is a required CI gate after Chromium installation; a missing
        # Node fails in CI (require_node), like the other rendered suites.
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-revise-line.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=600,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-4000:])
        evidence = json.loads(result.stdout[result.stdout.index('{'):])
        self.assertEqual(evidence['status'], 'PASS')
        confirmed = evidence['confirmed']
        self.assertEqual(confirmed['rv-wall']['description'], 'Feature wall 4m W x 3m H with veneer finish')
        self.assertEqual((confirmed['rv-wall']['variant'], confirmed['rv-wall']['price'], confirmed['rv-wall']['quantity']), ('manual', 55.5, 15))
        self.assertEqual((confirmed['rv-table']['description'], confirmed['rv-table']['price']), ('bar table', 20))
        self.assertEqual((confirmed['rv-carpet']['description'], confirmed['rv-carpet']['variant']), ('red rug', 'none'))
        self.assertEqual(confirmed['standard-waste-disposal']['mode'], 'Included')
        # A Re detail on a catalogue line prints after the name with the price it held (#430 print rule);
        # the AI's own draft detail does not print.
        self.assertEqual((confirmed['rv-banner']['description'], confirmed['rv-banner']['variant'], confirmed['rv-banner']['price']),
                         ('sqm synthetic hanging banner - black colour', 'catalog', 21.6))
        self.assertEqual((confirmed['rv-vinyl']['description'], confirmed['rv-vinyl']['price']), ('sqm synthetic vinyl floor finish - matt finish', 9.25))
        self.assertEqual(confirmed['rv-deck']['description'], 'sqm synthetic raised deck panel')
        self.assertIn(['sqm synthetic hanging banner - black colour'], [[cell for cell in row if 'black colour' in cell and 'banner' in cell] for row in evidence['workbook'].values()])
        self.assertNotIn('rv-cover', confirmed)
        self.assertEqual(evidence['reinclude']['re'], evidence['reinclude']['tick'])
        self.assertIn('This is no longer the same catalogue item', evidence['cards']['carpet'])


if __name__ == '__main__':
    unittest.main()
