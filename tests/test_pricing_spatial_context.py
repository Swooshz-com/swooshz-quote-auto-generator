"""Independent primary-object wording survives spatial catalogue context and ledger checks."""
import copy
import json
import subprocess
import unittest
from tests import test_webapp as fixtures

webapp = fixtures.webapp


class PricingSpatialContextTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WebappServerTest('test_ledger_units_share_quantity_basis_only_for_one_to_one_aliases')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_rendered_confirm_partial_refusal_manual_review_and_current_xlsx(self):
        # unittest discovery is a required CI gate after Chromium installation.
        # Match the existing owner-UAT wrapper: missing Playwright is a failure,
        # and require_node fails in CI (only a missing local Node may skip).
        result = subprocess.run(
            [fixtures.require_node(self), 'scripts/playwright-pricing-spatial-context.mjs'],
            text=True, capture_output=True, cwd=fixtures.ROOT, timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence['status'], 'PASS')
        self.assertEqual(evidence['selectionStatuses'], [200])
        self.assertEqual(evidence['spatialBeforeConfirm']['price'], None)
        self.assertEqual(evidence['saveReopen'], 'PASS')
        self.assertEqual(evidence['xlsx']['status'], 'PASS')
        self.assertEqual(json.loads(evidence['xlsx']['validator'])['bad'], [])

    @staticmethod
    def catalogue():
        return [
            {'id': 'object-a', 'section': 'Objects', 'description': 'nos wooden task seat',
             'unit_hint': 'nos', 'sale_unit_price': 7, 'object_families': ['task_seat'],
             'match_terms': ['task seat']},
            {'id': 'object-b', 'section': 'Objects', 'description': 'nos work desk',
             'unit_hint': 'nos', 'sale_unit_price': 11, 'object_families': ['work_desk'],
             'match_terms': ['work desk']},
        ]

    def test_independent_catalogue_primary_context_matrix(self):
        selected, other = self.catalogue()
        for detail in ('wooden task seats at work desks', 'wooden task seats around the work desks',
                       'wooden task seats beside work desks', 'nos wooden task seat at work desks',
                       'nos. wooden task seat at work desks', 'high-top wooden task seats at work desks'):
            for bracketed in (False, True):
                text = '[ nos wooden task seat ] - ' + detail if bracketed else detail
                with self.subTest(text=text):
                    self.assertFalse(webapp.catalog_line_contradicts_item(text, selected, catalog_items=[selected, other]))
        for detail in ('work desks with wooden task seats', 'work desks without wooden task seats',
                       'wooden task seats and work desks', 'No wooden task seats at work desks',
                       'work desks at wooden task seats', 'work desks for visitor seating',
                       'at work desks', 'wooden task seats, at work desks',
                       'wooden task seats at work desks and cabinets',
                       "wooden task seats aren't at work desks", 'wooden task seats nowhere near work desks',
                       'wooden task seats removed at work desks',
                       'wooden task seats at work desks. Work desks included',
                       'wooden task seats at work desks - work desks supplied',
                       'metal task seats at work desks'):
            text = '[ nos wooden task seat ] - ' + detail
            with self.subTest(text=text):
                self.assertTrue(webapp.catalog_line_contradicts_item(text, selected, catalog_items=[selected, other]))
        self.assertTrue(webapp.catalog_line_contradicts_item(
            '[ nos work desk ] - wooden task seats at work desks', selected, catalog_items=[selected, other]))

    def test_single_terminal_full_stop_preserves_spatial_primary_scope(self):
        selected, other = self.catalogue()
        for detail in ('wooden task seats at work desks.',
                       'nos wooden task seat at work desks.',
                       'nos. wooden task seat at work desks.',
                       'high-top wooden task seats at work desks.'):
            with self.subTest(detail=detail):
                self.assertEqual(webapp.catalog_spatial_context_parts(detail)[1], 'work desks.')
            for bracketed in (False, True):
                text = '[ nos wooden task seat ] - ' + detail if bracketed else detail
                with self.subTest(text=text):
                    self.assertFalse(webapp.catalog_line_contradicts_item(
                        text, selected, catalog_items=[selected, other]))
        for detail in ('wooden task seats at work desks. Work desks supplied',
                       'wooden task seats at work desks. Work desks supplied.',
                       'wooden task seats at work desks..',
                       'wooden task seats at work desks...',
                       'wooden task seats at work desks ...',
                       'wooden task seats at work desks; work desks supplied.',
                       'wooden task seats at work desks - work desks supplied.',
                       'wooden task seats, at work desks.',
                       'wooden task seats at work desks and cabinets.',
                       'No wooden task seats at work desks.'):
            with self.subTest(detail=detail):
                self.assertIsNone(webapp.catalog_spatial_context_parts(detail))
                self.assertTrue(webapp.catalog_line_contradicts_item(
                    '[ nos wooden task seat ] - ' + detail, selected, catalog_items=[selected, other]))
        for bracketed in (False, True):
            detail = 'metal task seats at work desks.'
            text = '[ nos wooden task seat ] - ' + detail if bracketed else detail
            with self.subTest(text=text):
                self.assertTrue(webapp.catalog_line_contradicts_item(
                    text, selected, catalog_items=[selected, other]))

    def test_context_material_cannot_mask_primary_attribute_conflict(self):
        selected, other = self.catalogue()
        other['description'] = 'nos wooden work desk'
        other['match_terms'] = ['wooden work desk']
        for detail, contradiction in (
            ('wooden task seats at wooden work desks', False),
            ('metal task seats at work desks', True),
            ('metal task seats at wooden work desks', True),
        ):
            for bracketed in (False, True):
                text = '[ nos wooden task seat ] - ' + detail if bracketed else detail
                with self.subTest(text=text):
                    self.assertEqual(webapp.catalog_line_contradicts_item(
                        text, selected, catalog_items=[selected, other]), contradiction)

    def selection_payload(self, detail, unit='nos'):
        payload, _ = self.fixture._owner_possible_match_payload(False, detail)
        lookup = webapp.pricing_catalog_runtime_lookup_for_payload(payload, payload['profile_id'])
        item = lookup['synthetic-rentals-synthetic-cafe-chair']
        row = payload['line_items'][0]
        row.update(section=item['section'], description=detail, quantity=3, unit=unit)
        section = payload['quote_basis_sections'][0]
        section['title'] = item['section']
        section['lines'][0].update(text='[ ' + item['description'] + ' ] - ' + detail,
                                   quantity=3, unit=item['unit_hint'], pricing_keyword=item['id'])
        payload['quote_basis'] = webapp.quote_basis_from_sections(payload['quote_basis_sections'])
        return payload, item

    def test_real_selection_normalisation_save_reopen_and_final_brief(self):
        for index, detail in enumerate(('Synthetic cafe chairs at the synthetic round tables',
                                        'nos. Synthetic cafe chair at the synthetic round tables')):
            with self.subTest(detail=detail):
                payload, item = self.selection_payload(detail)
                request, selected = self.fixture._owner_server_selection(payload, item, f'quote-spatial-context-{index}')
                self.assertEqual(selected['selections'][0]['status'], 'selected')
                price = webapp.pricing_reference_sale_unit_price(item)
                self.assertEqual(self.fixture._ledger_price(selected['line_items'], 'selected-row'), price)
                request['line_items'] = selected['line_items']
                for _ in range(3):
                    self.fixture._ledger_sync(request)
                    request['line_items'] = webapp.normalize_line_items_for_quote_basis_review(request)
                    self.assertEqual(self.fixture._ledger_price(request['line_items'], 'selected-row'), price)
                self.fixture._ledger_sync(request)
                storage = webapp.LocalSqagStorage()
                storage.create_or_update_quote_session(request)
                reopened = webapp.read_quote_session_metadata(request['quote_session']['session_id'])
                request['line_items'] = json.loads(json.dumps(reopened['draft_state']['lineItems']))
                request['quote_basis_sections'] = reopened['draft_state']['quoteBasisSections']
                self.fixture._ledger_sync(request)
                rows = webapp.normalize_line_items_for_final_brief(request)
                self.assertEqual(self.fixture._ledger_price(rows, 'selected-row'), price)
                self.assertEqual(rows[0]['pricing_basis_amount'], 3 * price)
                # An admitted row cannot use its old grant to hide later contradictory wording.
                bad = copy.deepcopy(request)
                bad['line_items'][0]['description'] = 'Synthetic round tables with synthetic cafe chairs'
                bad['quote_basis_sections'][0]['lines'][0]['text'] = '[ ' + item['description'] + ' ] - Synthetic round tables with synthetic cafe chairs'
                self.fixture._ledger_sync(bad)
                self.assertIsNone(self.fixture._ledger_price(webapp.normalize_line_items_for_final_brief(bad), 'selected-row'))

    def test_bracket_label_and_client_authority_cannot_supply_primary_evidence(self):
        for index, (detail, unit, reason) in enumerate((
            ('at the synthetic round tables', 'nos', 'contradiction'),
            ('Synthetic round tables with synthetic cafe chairs', 'nos', 'contradiction'),
            ('Synthetic cafe chairs and synthetic round tables', 'nos', 'contradiction'),
            ('No synthetic cafe chairs at the synthetic round tables', 'nos', 'contradiction'),
            ('Synthetic cafe chairs at the synthetic round tables', 'lot', 'unit_review_required'),
        )):
            with self.subTest(detail=detail, unit=unit):
                payload, item = self.selection_payload(detail, unit)
                row = payload['line_items'][0]
                row['pricing_authority'] = webapp.build_pricing_authority('catalog', row, price=999999,
                    reference_authority=webapp.exact_pricing_reference_authority(payload), catalog_item=item)
                _, result = self.fixture._owner_server_selection(payload, item, f'quote-spatial-negative-{index}')
                self.assertEqual(result['selections'][0]['reason'], reason)
                self.assertIsNone(self.fixture._ledger_price(result['line_items'], 'selected-row'))


if __name__ == '__main__':
    unittest.main()
