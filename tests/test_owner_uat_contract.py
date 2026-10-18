"""Owner UAT regressions across UI actions and trusted pricing/export boundaries."""
import copy
import json
import subprocess
import unittest
import zipfile
import xml.etree.ElementTree as ET
from unittest import mock
from tests import test_webapp as fixtures
ROOT = fixtures.ROOT
require_node = fixtures.require_node
webapp = fixtures.webapp
import generate_quote as quote


class OwnerUatContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebappServerTest(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def _owner_selected_suggestion_payload(self, owned=False):
        return self.fixture._owner_selected_suggestion_payload(owned)

    def browser_contract(self, data):
        result = subprocess.run([require_node(self), 'tests/owner_uat_contract_harness.cjs'],
            input=json.dumps(data), text=True, capture_output=True, cwd=ROOT, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_fxui_r01_r02_r06_r08_reference_and_explicit_quote_round_trip(self):
        for fx in (1, 2, 3, .5, 1.37, 10000):
            with self.subTest(fx=fx):
                self.browser_contract(dict(mode='fx', fx=fx))

    def test_fxui_r01_r03_r06_large_price_reopen_is_unchanged(self):
        self.browser_contract(dict(mode='reopen'))

    def test_fxui_r05_r06_r10_job_dispatch_readmits_after_synchronization(self):
        self.browser_contract(dict(mode='dispatch'))

    def test_fxui_r05_r10_deferred_save_temporal_contract(self):
        self.browser_contract(dict(mode='temporal'))

    def test_selected_suggestion_after_output_reaches_real_server_catalogue_admission(self):
        for owned in (False, True):
            with self.subTest(owned=owned):
                payload, catalog = self._owner_selected_suggestion_payload(owned)
                row = copy.deepcopy(payload['line_items'][0])
                result = self.browser_contract(dict(mode='selection', row=row,
                    line=payload['quote_basis_sections'][0]['lines'][0],
                    match=dict(pricing_keyword=catalog['id'], description=catalog['description'],
                               unit='sqm', catalog_unit_price=999999)))
                payload.update(result)
                payload['quote_basis'] = webapp.quote_basis_from_sections(payload['quote_basis_sections'])
                admitted = webapp.normalize_line_items_for_quote_basis_review(payload)
                self.assertEqual(admitted[0]['pricing_authority']['variant'], 'catalog')
                self.assertEqual(admitted[0]['pricing_authority']['price'], webapp.pricing_reference_sale_unit_price(catalog))
                self.assertNotEqual(admitted[0]['pricing_authority']['price'], 999999)

    def test_rendered_selection_fx_reload_and_current_xlsx(self):
        result = subprocess.run([require_node(self), 'scripts/playwright-smoke.mjs', '--g3-pricing', '--alpha1'],
            text=True, capture_output=True, cwd=ROOT, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        evidence = json.loads(result.stdout)['analysis']['ownerUat']
        self.assertEqual(evidence['status'], 'FXUI_G3_RENDERED_SERVER_CURRENT_XLSX_PASS')
        self.assertEqual(evidence['providerCalls'], 0)
        workbook = ROOT / '_logs/browser/playwright-smoke/owner-uat-c17-current.xlsx'
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(workbook) as archive:
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            shared = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared = [''.join(node.itertext()) for node in ET.fromstring(archive.read('xl/sharedStrings.xml'))]

        def cell_value(cell):
            value = cell.find('s:v', ns)
            if cell.get('t') == 's':
                return shared[int(value.text)]
            return ''.join(cell.itertext()) if cell.get('t') == 'inlineStr' else value.text if value is not None else ''

        amounts = {}
        for row in sheet.findall('.//s:sheetData/s:row', ns):
            cells = {cell.get('r').rstrip('0123456789'): cell_value(cell) for cell in row}
            for description in ('nos synthetic wall light', 'nos synthetic ceiling light'):
                if description in cells.get('C', ''):
                    amounts[description] = float(cells['E'])
        self.assertEqual(amounts, {'nos synthetic wall light': 2.0, 'nos synthetic ceiling light': 300.0})

    def test_fxui_r08_real_xlsx_line_first_runtime_rounding(self):
        import hashlib
        from tests.test_generate_quote import KONCEPT_LAYOUT, cell_value, find_cell_ref, commercial_test_brief
        payload, _ = self._owner_selected_suggestion_payload(True)
        row = payload['line_items'][0]
        row.update(quantity=3)
        row['pricing_authority'] = webapp.build_pricing_authority('manual', row, price=1.01,
            reference_authority=webapp.exact_pricing_reference_authority(payload))
        payload['quote_exchange_rate'] = 1.37
        payload['quote_session']['commercials']['exchange_rate'] = 1.37
        payload['quote_session']['draft_state']['quoteDetails']['exchange_rate'] = 1.37
        admitted = webapp.normalize_line_items(payload)
        self.assertEqual(admitted[0]['approved_quote_amount'], 4.15)
        identity = payload['quote_session']['draft_state']['quoteDetails']['commercial_snapshot']['pricing_basis']
        brief = commercial_test_brief({'currency': 'SGD', 'exchange_rate': 1.37, 'line_items': admitted, 'tax': {'label': 'GST', 'rate': 0}})
        lines = quote.prepare_lines(brief, [], allow_ambiguous=False, authority_required=True, trusted_pricing_reference=identity)
        self.assertEqual(quote.line_amount_value(lines[0], 1.37), 4.15)
        target = ROOT / '_logs/browser/fxui-line-first.xlsx'
        target.parent.mkdir(parents=True, exist_ok=True)
        quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, target, brief, lines)
        with zipfile.ZipFile(target) as archive:
            sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
            workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        total_row = quote.parse_cell_ref(find_cell_ref(sheet, 'Total'))[0]
        self.assertEqual(float(cell_value(sheet, f'E{total_row}')), 4.15)
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        self.assertEqual([n.text for n in workbook.findall('.//s:definedName', ns)
                          if n.get('name') == '_xlnm.Print_Area'], ['Quotation!$A$1:$I$47'])
        (target.parent / 'fxui-line-first-proof.json').write_text(json.dumps({
            'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'referencePrice': 1.01, 'quantity': 3, 'fx': 1.37, 'approvedAmount': 4.15}), encoding='utf-8')

    def test_restored_dashboard_search_is_visible(self):
        self.browser_contract(dict(mode='dashboard'))

    def test_manual_quote_price_survives_server_and_generator_at_nonunit_fx(self):
        for fx in (2, 3, .5, 1.37, 10000):
            with self.subTest(fx=fx):
                payload, _ = self._owner_selected_suggestion_payload(True)
                payload['quote_session']['commercials'].update(currency='AUD', exchange_rate=fx)
                payload['quote_session']['draft_state']['quoteDetails'].update(currency='AUD', exchange_rate=fx)
                payload['quote_exchange_rate'] = fx
                row=payload['line_items'][0]
                row['pricing_authority']=webapp.build_pricing_authority('manual',row,price=1,
                    reference_authority=webapp.exact_pricing_reference_authority(payload))
                row['pricing_authority']['price_currency']='AUD'
                admitted=webapp.normalize_line_items(payload)
                self.assertEqual(admitted[0]['pricing_authority']['variant'],'manual')
                self.assertEqual(admitted[0]['pricing_authority']['price_currency'],'AUD')
                self.assertEqual(admitted[0]['approved_quote_amount'],2)
                repeated=webapp.normalize_line_items({**payload,'line_items':admitted})
                self.assertEqual(repeated,admitted)
                identity=payload['quote_session']['draft_state']['quoteDetails']['commercial_snapshot']['pricing_basis']
                brief={'currency':'AUD','exchange_rate':fx,'line_items':admitted}
                lines=quote.prepare_lines(brief,[],allow_ambiguous=False,authority_required=True,trusted_pricing_reference=identity)
                self.assertEqual(lines[0].match_status,'manual-price')
                self.assertEqual(quote.line_amount_value(lines[0],fx),2)

    def test_manual_quote_currency_cannot_be_forged_or_added_to_catalogue(self):
        payload,catalog=self._owner_selected_suggestion_payload(True)
        row=payload['line_items'][0]
        authority=webapp.exact_pricing_reference_authority(payload)
        manual=webapp.build_pricing_authority('manual',row,price=1,reference_authority=authority)
        manual['price_currency']='USD'
        payload['line_items']=[{**row,'pricing_authority':manual}]
        self.assertNotEqual(webapp.normalize_line_items(payload)[0]['pricing_authority']['variant'],'manual')
        selected=webapp.normalize_line_items_for_quote_basis_review(self._owner_selected_suggestion_payload(True)[0])[0]
        selected['pricing_authority']['price_currency']='SGD'
        self.assertNotEqual(webapp.normalize_line_items({**payload,'line_items':[selected]})[0]['pricing_authority']['variant'],'catalog')

    def test_fxui_r04_immutable_c17_predecessor_readback(self):
        # Produced by exact C17 eda4dd0 tree 095a5869, not the candidate.
        import hashlib
        raw = (ROOT / 'tests/fixtures/fxui-c17-predecessor.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), '1db57ff326ad072ecef74f146c5c5c1965fe5d55c1028de0ff30dcf0ea5a0d99')
        payload = json.loads(raw)
        admitted = webapp.normalize_line_items(payload)
        self.assertEqual(admitted, payload['line_items'])
        self.assertNotIn('price_currency', admitted[0]['pricing_authority'])
        self.assertEqual(admitted[0]['approved_quote_amount'], 4)

    def test_legacy_manual_price_keeps_reference_currency_fx(self):
        payload,_=self._owner_selected_suggestion_payload(True)
        payload['quote_session']['commercials']['exchange_rate']=2
        payload['quote_session']['draft_state']['quoteDetails']['exchange_rate']=2
        payload['quote_exchange_rate']=2
        row=payload['line_items'][0]
        row['pricing_authority']=webapp.build_pricing_authority('manual',row,price=1,
            reference_authority=webapp.exact_pricing_reference_authority(payload))
        self.assertEqual(webapp.normalize_line_items(payload)[0]['approved_quote_amount'],4)


if __name__ == '__main__':
    unittest.main()
