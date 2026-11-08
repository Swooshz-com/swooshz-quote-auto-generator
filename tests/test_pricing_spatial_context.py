"""Independent primary-object wording survives spatial catalogue context and ledger checks."""
import copy
import hashlib
import json
import os
from tests.rendered_browser_support import owned_browser_evidence_directory
import subprocess
import unittest
from unittest import mock
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
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-pricing-spatial-context.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=180,
            )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence['status'], 'PASS')
        self.assertEqual(evidence['selectionStatuses'], [200, 200])
        self.assertEqual(evidence['spatialBeforeConfirm']['price'], None)
        self.assertEqual(evidence['saveReopen'], 'PASS')
        self.assertEqual(evidence['excludedReopen'], 'PASS')
        self.assertTrue(evidence['deferredReopened'])
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

    def test_deferred_pricing_binds_scope_measurements_session_and_current_catalogue(self):
        payload, _ = self.fixture._owner_possible_match_payload(False, 'Synthetic kinetic sculpture')
        payload['quote_session'] = {'session_id': 'quote-deferred-synthetic'}
        row = payload['line_items'][0]
        row.update(section='Special Effects', quantity=2, unit='nos')
        row['pricing_authority'] = webapp.build_pricing_authority('none', row)
        line = {'id': row['source_basis_line_id'], 'tag': 'Custom', 'text': row['description'],
                'custom_pricing': True, 'quantity': 2, 'unit': 'nos'}
        payload['quote_basis_sections'] = [{'id': 'special-effects', 'title': 'Special Effects', 'lines': [line]}]
        payload['quote_basis'] = webapp.quote_basis_from_sections(payload['quote_basis_sections'])
        [offered] = webapp.project_deferred_pricing(
            payload, [row], offer=True, source_measurements={row['source_basis_line_id']: [2, 'nos']},
        )
        self.assertNotIn('unmatched_pricing_offer', webapp.project_deferred_pricing(
            payload, [row], offer=True, source_measurements={row['source_basis_line_id']: [2, 'sets']},
        )[0])
        token = offered['unmatched_pricing_offer']
        self.assertNotIn('deferred_pricing', webapp.normalize_line_items_for_final_brief(payload)[0])
        line['custom_confirmed'] = True
        row['deferred_pricing'] = token
        payload['quote_basis'] = webapp.quote_basis_from_sections(payload['quote_basis_sections'])
        for normalizer in (webapp.normalize_line_items_for_quote_basis_review, webapp.normalize_line_items_for_final_brief):
            [normalised] = normalizer(payload)
            self.assertEqual(normalised['deferred_pricing'], token)
            self.assertEqual(normalised['pricing_authority']['variant'], 'none')
            self.assertNotIn('approved_quote_amount', normalised)
        diagnostic_row = {**normalised, 'diagnostic_row_id': 'row-0'}
        diagnostic = {'source_basis_line_id': row['source_basis_line_id'], 'diagnostic_row_id': 'row-0',
                      'status': 'deferred-pricing'}
        self.assertEqual(webapp.generation_output_rows([diagnostic_row], [diagnostic])['rows'][0]['deferred_pricing'], token)
        with self.assertRaisesRegex(ValueError, 'contradicts the admitted pricing deferral'):
            webapp.generation_output_rows([diagnostic_row], [{**diagnostic, 'status': 'unmatched'}])

        reference = webapp.exact_pricing_reference_authority(payload)
        old_binding = [payload['quote_session']['session_id'], reference['source'], reference['id'],
                       webapp.pricing_reference_catalog_digest(reference['detail']), webapp.pricing_authority_context(row), 2.0]
        old_digest = hashlib.sha256(json.dumps(old_binding, sort_keys=True, ensure_ascii=True).encode('utf-8')).hexdigest()
        for field, value in (('deferred_pricing', None), ('deferred_pricing', '0' * 64),
                             ('deferred_pricing', old_digest), ('quantity', 3),
                             ('description', 'Synthetic kinetic mobile'), ('unit', 'sets'),
                             ('source_basis_line_id', 'edited-synthetic'), ('section', 'Structures'),
                             ('unit_price_override', 0), ('pricing_authority', 'none')):
            bad = copy.deepcopy(payload)
            bad['line_items'][0][field] = value
            edited_row = bad['line_items'][0]
            edited_line = bad['quote_basis_sections'][0]['lines'][0]
            if field in ('quantity', 'description', 'unit', 'source_basis_line_id', 'section'):
                edited_row['pricing_authority'] = webapp.build_pricing_authority('none', edited_row)
                if field == 'section':
                    bad['quote_basis_sections'][0]['title'] = value
                else:
                    edited_line[{'description': 'text', 'source_basis_line_id': 'id'}.get(field, field)] = value
                bad['quote_basis'] = webapp.quote_basis_from_sections(bad['quote_basis_sections'])
            with self.subTest(field=field, value=value):
                [rejected] = webapp.normalize_line_items_for_final_brief(bad)
                self.assertNotIn('deferred_pricing', rejected)
                if field not in ('unit_price_override', 'pricing_authority'):
                    self.assertEqual(rejected['pricing_authority']['variant'], 'none')
        copied = copy.deepcopy(payload)
        copied['quote_session']['session_id'] = 'quote-other-synthetic'
        self.assertNotIn('deferred_pricing', webapp.normalize_line_items_for_final_brief(copied)[0])
        aliased = copy.deepcopy(payload)
        alias = aliased['quote_basis_sections'][0]['lines'][0]
        alias.update(id='basis-synthetic', source_line_item_id=row['source_basis_line_id'])
        [alias_offer] = webapp.project_deferred_pricing(aliased, aliased['line_items'], offer=True)
        aliased['line_items'][0]['deferred_pricing'] = alias_offer['unmatched_pricing_offer']
        self.assertIn('deferred_pricing', webapp.project_deferred_pricing(aliased, aliased['line_items'])[0])
        alias['id'] = 'changed-basis-synthetic'
        self.assertNotIn('deferred_pricing', webapp.project_deferred_pricing(aliased, aliased['line_items'])[0])
        excluded = copy.deepcopy(payload)
        excluded['quote_basis_sections'][0]['lines'][0]['tag'] = 'Exclude'
        excluded['quote_basis'] = webapp.quote_basis_from_sections(excluded['quote_basis_sections'])
        self.assertNotIn('deferred_pricing', webapp.normalize_line_items_for_final_brief(excluded)[0])
        selection = webapp.explicit_pricing_reference_selection(payload)
        late_catalogue = {
            'id': selection['id'], 'source': selection['source'], 'currency': 'SGD',
            'items': [{'id': f'test-{index}', 'section': 'Structures', 'description': f'Fixture beam {index}',
                       'unit_hint': 'nos', 'sale_unit_price': 10} for index in range(499)]
            + [{'id': 'late-sculpture', 'section': row['section'], 'description': row['description'],
                'unit_hint': 'nos', 'sale_unit_price': 80}],
        }
        self.assertFalse(any(entry['id'] == 'late-sculpture' for entry in webapp.pricing_catalog_prompt_rows_from_reference(late_catalogue)))
        reader = lambda *_args, **_kwargs: late_catalogue
        self.assertNotIn('unmatched_pricing_offer', webapp.project_deferred_pricing(payload, [row], offer=True, reference_reader=reader)[0])
        self.assertNotIn('deferred_pricing', webapp.project_deferred_pricing(payload, [row], reference_reader=reader)[0])
        changed_catalogue = copy.deepcopy(reference['detail'])
        changed_catalogue['currency'] = 'USD'
        self.assertNotEqual(webapp.pricing_reference_catalog_digest(changed_catalogue), old_binding[3])
        changed_reader = lambda *_args, **_kwargs: changed_catalogue
        self.assertNotIn('deferred_pricing', webapp.project_deferred_pricing(payload, [row], reference_reader=changed_reader)[0])
        self.assertIn('unmatched_pricing_offer', webapp.project_deferred_pricing(
            payload, [row], offer=True, reference_reader=changed_reader,
        )[0])
        other_reference = copy.deepcopy(payload)
        other_reference['pricing_reference_id'] = 'other-synthetic-reference'
        other_reference['pricing_reference']['id'] = 'other-synthetic-reference'
        same_catalogue = {**reference['detail'], 'id': 'other-synthetic-reference'}
        same_catalogue_reader = lambda *_args, **_kwargs: same_catalogue
        self.assertIn('unmatched_pricing_offer', webapp.project_deferred_pricing(
            other_reference, [row], offer=True, reference_reader=same_catalogue_reader,
        )[0])
        self.assertNotIn('deferred_pricing', webapp.project_deferred_pricing(
            other_reference, [row], reference_reader=same_catalogue_reader,
        )[0])

        self.fixture._ledger_sync(payload)
        storage = webapp.LocalSqagStorage()
        storage.create_or_update_quote_session(payload)
        reopened = webapp.read_quote_session_metadata(payload['quote_session']['session_id'])
        payload['quote_session']['draft_state'] = reopened['draft_state']
        self.assertEqual(webapp.normalize_line_items_for_final_brief(payload)[0]['deferred_pricing'], token)

        priced = {**row, 'source_basis_line_id': 'manual-synthetic', 'description': 'Manual item',
                  'quantity': 1, 'unit_price_override': 50}
        priced.pop('deferred_pricing')
        priced['pricing_authority'] = webapp.build_pricing_authority(
            'manual', priced, price=50, reference_authority=webapp.exact_pricing_reference_authority(payload))
        draft_state = payload['quote_session']['draft_state']
        draft_state['outputRows'] = [row, priced]
        self.assertEqual(webapp.quote_session_commercials(payload, {})['subtotal'], 50)
        self.assertIn('deferred_pricing', webapp.payload_to_brief(payload)['line_items'][0])
        storage.create_or_update_quote_session(payload)
        self.assertEqual(webapp.read_quote_session_metadata(payload['quote_session']['session_id'])['commercials']['grand_total'], 54.5)
        for invalid in (None, '0' * 64, old_digest):
            draft_state['outputRows'][0] = {**row, 'deferred_pricing': invalid}
            storage.create_or_update_quote_session(payload)
            saved = webapp.read_quote_session_metadata(payload['quote_session']['session_id'])['commercials']
            self.assertEqual([saved[key] for key in ('subtotal', 'tax_amount', 'grand_total')], [None, None, None])
            with self.assertRaises(webapp.QuoteCommercialStateError):
                webapp.payload_to_brief(payload)

        def actor(workspace, subject):
            return {'auth_mode': webapp.INTERNAL_AUTH_MODE, 'user': {'account': workspace, 'subject': subject}}

        owner = actor('workspace-synthetic', 'user-synthetic')
        reference_reader = lambda *_args, **_kwargs: reference['detail']
        with mock.patch.object(webapp, 'configured_storage_mode', return_value='database'):
            [scoped] = webapp.project_deferred_pricing(payload, [row], owner, offer=True, reference_reader=reference_reader)
            self.assertNotEqual(scoped['unmatched_pricing_offer'], token)
            scoped_row = {**row, 'deferred_pricing': scoped['unmatched_pricing_offer']}
            payload['line_items'] = [scoped_row]
            draft_state['outputRows'][0] = scoped_row
            for auth, allowed in ((owner, True), (actor('other-workspace', 'user-synthetic'), False),
                                  (actor('workspace-synthetic', 'other-user'), False)):
                with self.subTest(auth=auth):
                    [validated] = webapp.project_deferred_pricing(payload, [scoped_row], auth, reference_reader=reference_reader)
                    self.assertEqual('deferred_pricing' in validated, allowed)
                    saved = webapp.quote_session_commercials(
                        payload, {}, reference_reader=reference_reader, storage_scope=webapp.pricing_ledger_scope_for_auth_session(auth),
                    )
                    self.assertEqual(saved['subtotal'], 50 if allowed else None)
            with mock.patch.object(webapp, 'pricing_analysis_seal_key', return_value=None):
                self.assertNotIn('deferred_pricing', webapp.project_deferred_pricing(
                    payload, [scoped_row], owner, reference_reader=reference_reader,
                )[0])
                self.assertNotIn('unmatched_pricing_offer', webapp.project_deferred_pricing(
                    payload, [row], owner, offer=True, reference_reader=reference_reader,
                )[0])


if __name__ == '__main__':
    unittest.main()
