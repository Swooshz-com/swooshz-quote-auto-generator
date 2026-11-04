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

    def test_output_alignment_preserves_stable_row_identity_across_reference_sections(self):
        self.assertEqual(self.browser_contract(dict(mode='alignment'))['status'], 'STABLE_ROW_SECTION_ALIGNMENT_PASS')

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
                # The browser click alone never prices; the server records the selection, then prices it.
                self.assertNotEqual(webapp.normalize_line_items_for_quote_basis_review(payload)[0]['pricing_authority']['variant'], 'catalog')
                _request, outcome = self.fixture._owner_server_selection(payload, catalog, f'quote-owner-contract-{int(owned)}')
                self.assertEqual(outcome['selections'], [{'source_basis_line_id': 'selected-row', 'status': 'selected'}])
                admitted = outcome['line_items']
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

        cells_by_ref = {cell.get('r'): cell for cell in sheet.findall('.//s:sheetData/s:row/s:c', ns)}
        values = {ref: cell_value(cell) for ref, cell in cells_by_ref.items()}
        self.assertEqual(values['E21'], 'AUD')
        for row_number, label, amount in ((29, 'Total', 302), (30, 'GST 0%', 0), (31, 'Total including GST', 302)):
            self.assertEqual(values[f'D{row_number}'], label)
            self.assertEqual(float(values[f'E{row_number}']), amount)
            self.assertEqual(values[f'F{row_number}'], 'AUD')
            self.assertIsNotNone(cells_by_ref[f'E{row_number}'].find('s:f', ns))
        self.assertEqual(evidence['ui'], 'OWNER_ACCEPTED_S3')
        self.assertEqual(len(evidence['catalogueProof']['outcomes']), 24)
        self.assertEqual(len(evidence['catalogueProof']['fullTableOutcomes']), 5)
        self.assertEqual(evidence['catalogueProof']['effects'], [])
        self.assertEqual(len(evidence['keyboardProof']['outcomes']), 12)
        self.assertEqual(evidence['keyboardProof']['effects'], [])
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


    def test_duplicate_target_ledger_reopen_explicit_confirmation_and_guarded_real_xlsx(self):
        import contextlib
        import hashlib
        import io
        import os
        import socket
        import sys
        import tempfile
        from pathlib import Path
        runtime_root = ROOT / '_tmp' / 'xr'
        runtime_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='g', dir=runtime_root) as tmp:
            runtime = Path(tmp)
            env = {name: os.environ[name] for name in ('SystemRoot', 'WINDIR') if name in os.environ}
            env.update(APP_MODE='local', USER_TYPE='operator', SQAG_STORAGE_MODE='local', SQAG_ARTIFACT_STORAGE_MODE='local',
                       QUOTE_DATA_ROOT=str(runtime / 'd'), QUOTE_OUTPUT_ROOT=str(runtime / 'o'), QUOTE_TMP_ROOT=str(runtime / 't'),
                       QUOTE_LOG_ROOT=str(ROOT / '_logs' / 'server' / 'alpha2-export-recovery' / 'regression'))
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.dict(os.environ, env, clear=True))
                stack.enter_context(mock.patch.object(webapp, 'read_dotenv_value', side_effect=lambda name, *a, **k: os.environ.get(name, '')))
                network = stack.enter_context(mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('No network in this synthetic contract')))
                storage = webapp.LocalSqagStorage()
                f = self.fixture
                original, _ = f._ledger_analysis([
                    ('cat-row', 'Floors', 'Synthetic carpet tile flooring', 2, 'sqm', f.LEDGER_CARPET),
                    ('manual-row', 'General', 'Synthetic operator handling', 3, 'nos', None),
                    ('unpriced-row', 'General', 'Synthetic unidentified item', 1, 'lot', None),
                ], session_id='quote-regression-original')
                reference = webapp.exact_pricing_reference_authority(original)
                manual = next(row for row in original['line_items'] if row['source_basis_line_id'] == 'manual-row')
                manual.update(pricing_keyword='', price_mode='Priced', unit_price_override=1.01)
                manual['pricing_authority'] = webapp.build_pricing_authority('manual', manual, price=1.01,
                    reference_authority=reference)
                manual['pricing_authority']['price_currency'] = 'AUD'
                for section in original['quote_basis_sections']:
                    for line in section['lines']:
                        line.update(tag='Include' if line['id']=='cat-row' else 'Custom', custom_pricing=line['id']!='cat-row')

                def sync(payload):
                    f._ledger_sync(payload)
                    draft = payload['quote_session']['draft_state']
                    details = copy.deepcopy(fixtures.recovered_convergence_payload(include_included_row=False)['quote_session']['draft_state']['quoteDetails'])
                    details.update(currency='AUD', exchange_rate=1.37)
                    details['company']['logo_data_url'] = fixtures.SANITIZED_LOGO_DATA_URL
                    details['commercial_snapshot']['presence'] = {key:'captured' if webapp.quote_commercial_value_is_present(value) else 'intentional_empty'
                        for key,value in webapp.quote_commercial_snapshot_raw_values(details).items()}
                    draft.update(quoteDetails=details, pricingReferenceId=payload['pricing_reference_id'],
                        pricingReferenceSource=payload['pricing_reference_source'], outputRevision=0,
                        outputRows=copy.deepcopy(payload['line_items']))
                    payload.update(quote_exchange_rate=1.37)
                    payload['quote_session']['commercials'].update(currency='AUD', exchange_rate=1.37)
                    return payload

                sync(original)
                storage.create_or_update_quote_session(original)
                source_id = original['quote_session']['session_id']
                source_before = json.dumps(webapp.read_quote_session_metadata(source_id), sort_keys=True)
                target = copy.deepcopy(original)
                target['quote_session']['session_id'] = 'quote-regression-target'
                target.pop('pricing_analysis_seal', None)
                target['quote_session']['draft_state']['originalAnalysisSnapshot']['pricing_analysis_seal'] = None
                self.assertIsNone(webapp.verified_pricing_analysis_seal(original['pricing_analysis_seal'],
                    scope=webapp.pricing_ledger_scope_for_storage(storage),session_id='quote-regression-target'))
                self.assertIsNone(webapp.verified_pricing_analysis_seal(original['pricing_analysis_seal'],scope='workspace:wrong.test',session_id=source_id))
                normalized = webapp.normalize_line_items_for_quote_basis_review(target)
                self.assertEqual(len(normalized),3)
                self.assertEqual({row['source_basis_line_id'] for row in normalized}, {'cat-row','manual-row','unpriced-row'})
                self.assertNotEqual(next(row for row in normalized if row['source_basis_line_id']=='cat-row')['pricing_authority']['variant'],'catalog')
                manual_normalized = next(row for row in normalized if row['source_basis_line_id']=='manual-row')
                self.assertEqual(manual_normalized['pricing_authority']['price_currency'],'AUD')
                self.assertEqual(manual_normalized['approved_quote_amount'],3.03)
                target['line_items'] = normalized
                sync(target)
                storage.create_or_update_quote_session(target)
                reopened = storage.get_quote_session('quote-regression-target', include_draft_state=True)
                self.assertEqual(len(reopened['draft_state']['lineItems']),3)
                self.assertIsNone(webapp.pricing_ledger_view_for_payload(target).grant('cat-row'))
                self.assertFalse(any(row['pricing_authority']['variant']=='catalog' for row in webapp.normalize_line_items_for_final_brief(target)))
                # An unpriced row remains a genuine review blocker; the approved priced subset is explicit.
                with self.assertRaises(webapp.QuoteCommercialStateError): webapp.payload_to_brief(target)
                target['line_items'] = [row for row in target['line_items'] if row['source_basis_line_id']!='unpriced-row']
                sync(target)
                storage.create_or_update_quote_session(target)
                revision = webapp.public_pricing_ledger(webapp.read_quote_session_metadata('quote-regression-target'))['rows']['cat-row']
                selection = {'source_basis_line_id':'cat-row','catalog_item_id':f.LEDGER_CARPET,'expected_revision':revision}
                selected = webapp.apply_pricing_selections(target,[selection],storage)
                self.assertEqual(selected['selections'],[{'source_basis_line_id':'cat-row','status':'selected'}])
                with self.assertRaises(webapp.PricingSelectionConflict): webapp.apply_pricing_selections(target,[selection],storage)
                target['line_items'] = selected['line_items']
                sync(target)
                storage.create_or_update_quote_session(target)
                for changed, value in (('catalog_digest','sha256:'+'0'*64),('catalog_source','company'),('price',999999),('catalog_unit','lot')):
                    forged = copy.deepcopy(target)
                    row = next(row for row in forged['line_items'] if row['source_basis_line_id']=='cat-row')
                    row['pricing_authority'][changed] = value
                    self.assertFalse(any(row['pricing_authority']['variant']=='catalog' for row in webapp.normalize_line_items_for_final_brief(forged)),changed)

                def local_generator(command, **kwargs):
                    output = io.StringIO()
                    with mock.patch.object(sys, 'argv', ['generate_quote.py', *command[2:]]), contextlib.redirect_stdout(output):
                        code = quote.main()
                    return subprocess.CompletedProcess(command, code, output.getvalue(), '')

                with mock.patch.object(webapp, 'begin_generation_forensics', return_value=''), \
                     mock.patch.object(webapp, 'append_runtime_telemetry'), \
                     mock.patch.object(webapp.subprocess, 'run', side_effect=local_generator), \
                     mock.patch.object(webapp, 'quote_session_storage_for_auth_session', return_value=storage):
                    # The real generator completes and writes real bytes, then a
                    # diagnostic read failure must truthfully finish without publishing them.
                    with mock.patch.object(webapp, 'read_pricing_matches', side_effect=OSError('synthetic diagnostic read refused')), \
                         mock.patch.object(webapp, 'finish_generation_forensics', wraps=webapp.finish_generation_forensics) as finish:
                        refused = webapp._run_quote_job(copy.deepcopy(target), output_root=runtime/'o',tmp_root=runtime/'t',job_id='job-read-refused')
                    self.assertEqual((refused['status'],refused['return_code']),('failed',0))
                    self.assertFalse(refused.get('files'))
                    manifest = finish.call_args.kwargs['canonical_manifest']
                    self.assertIs(manifest['generator_executed'],True)
                    self.assertEqual(manifest['return_code'],0)
                    self.assertFalse(manifest['artifacts'])
                    self.assertIsNone(storage.quote_session_export_artifact('quote-regression-target','xlsx'))
                    self.assertTrue((runtime/'o'/'job-read-refused'/'quotation.xlsx').is_file())
                    result = webapp._run_quote_job(copy.deepcopy(target), output_root=runtime/'o',tmp_root=runtime/'t',job_id='job-target-positive')
                self.assertEqual((result['status'],result['return_code']),('completed',0),result)
                self.assertEqual(len(result['output_rows']['rows']),2)
                self.assertEqual({row['source_basis_line_id'] for row in result['output_rows']['rows']},{'cat-row','manual-row'})
                artifact = storage.quote_session_export_artifact('quote-regression-target','xlsx')
                self.assertIsNotNone(artifact)
                self.assertEqual(hashlib.sha256(artifact['content']).hexdigest(),artifact['sha256'])
                with zipfile.ZipFile(io.BytesIO(artifact['content'])) as workbook: self.assertIsNone(workbook.testzip())
                excluded = copy.deepcopy(target)
                for section in excluded['quote_basis_sections']:
                    for line in section['lines']:
                        if line['id']=='cat-row': line['tag']='Exclude'
                sync(excluded)
                storage.create_or_update_quote_session(excluded)
                self.assertFalse(any(row['pricing_authority']['variant']=='catalog' for row in webapp.normalize_line_items_for_final_brief(target)))
                self.assertEqual(json.dumps(webapp.read_quote_session_metadata(source_id), sort_keys=True),source_before)
                network.assert_not_called()


if __name__ == '__main__':
    unittest.main()
