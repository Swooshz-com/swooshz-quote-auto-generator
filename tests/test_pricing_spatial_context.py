"""Independent primary-object wording survives spatial catalogue context and ledger checks."""
import copy
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock
from tests.rendered_browser_support import owned_browser_evidence_directory
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
            receipt = re.compile(r'"(?:deferred_pricing|unmatched_pricing_offer|mac)"\s*:\s*"[a-f0-9]{64}"')
            self.assertFalse(bool(receipt.search(result.stdout + result.stderr)), 'Raw pricing receipt in browser output')
            for evidence_file in Path(evidence_root).rglob('*'):
                if evidence_file.suffix in {'.json', '.log'} and evidence_file.is_file():
                    self.assertFalse(bool(receipt.search(evidence_file.read_text(encoding='utf-8', errors='replace'))),
                                     f'Raw pricing receipt in {evidence_file.name}')
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        evidence = json.loads(result.stdout)
        self.assertEqual(evidence['status'], 'PASS')
        self.assertEqual(evidence['selectionStatuses'], [200, 409, 200, 200])
        self.assertEqual(evidence['spatialBeforeConfirm']['price'], None)
        self.assertEqual(evidence['saveReopen'], 'PASS')
        self.assertEqual(evidence['excludedReopen'], 'PASS')
        self.assertEqual(evidence['reincludedPricing'], 'PASS')
        self.assertEqual(evidence['staleRecovery'], 'PASS')
        self.assertEqual(evidence['confirmFollowupConflict'], 'PASS')
        self.assertEqual(evidence['preVersionRecovery'], 'PASS')
        self.assertEqual(evidence['spatialPartialRefusal']['mixedStatus'], 200)
        self.assertEqual(evidence['spatialPartialRefusal']['resolvedVideo']['quoted'], '12.00')
        self.assertEqual(evidence['tickOnlyDeferred'], 'PASS')
        self.assertTrue(evidence['deferredReopened'])
        self.assertEqual(evidence['deferredEditorDismissal'], 'PASS')
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

    def test_versioned_ordinary_selection_rejects_unrelated_draft_changes(self):
        with mock.patch.dict(os.environ, self.fixture._ledger_env()), fixtures.LocalRunnerServer() as runner:
            headers = self.fixture._ledger_runner_headers(runner)
            post = lambda path, body: self.fixture._ledger_post(runner, headers, path, body)
            for unit, expected_status in (("nos", "selected"), ("sqm", "refused")):
                with self.subTest(outcome=expected_status):
                    payload, item = self.selection_payload("Synthetic cafe chairs at the synthetic round tables", unit=unit)
                    session_id = f"quote-ordinary-version-{expected_status}"
                    payload["quote_session"] = {"session_id": session_id}
                    self.fixture._ledger_sync(payload)
                    initial = post("/api/quote-sessions", payload)["body"]["quote_session"]
                    request = {**payload, "expected_draft_version": initial["draft_version"], "pricing_selections": [{
                        "source_basis_line_id": "selected-row", "catalog_item_id": item["id"],
                        "expected_revision": initial["pricing_ledger"]["rows"]["selected-row"],
                    }]}
                    newer = copy.deepcopy(payload)
                    newer["quote_session"]["customer_summary"] = {"project_name": "Synthetic newer tab"}
                    newer["expected_draft_version"] = initial["draft_version"]
                    saved = post("/api/quote-sessions", newer)
                    self.assertEqual(saved["status"], 200)
                    persisted = webapp.read_quote_session_metadata(session_id)
                    stale = post("/api/quote-sessions/pricing-selections", request)
                    self.assertEqual(stale["status"], 409)
                    self.assertEqual(webapp.read_quote_session_metadata(session_id), persisted)
                    current_request = {**request, "quote_session": newer["quote_session"],
                                       "expected_draft_version": saved["body"]["quote_session"]["draft_version"]}
                    current = post("/api/quote-sessions/pricing-selections", current_request)
                    self.assertEqual(current["status"], 200, current["text"][-600:])
                    self.assertEqual(current["body"]["selections"][0]["status"], expected_status)
                    receipt = current["body"]["quote_session"]
                    detail = self.fixture.http_json(runner, "GET", f"/api/quote-sessions/{session_id}", headers=headers)
                    self.assertEqual(receipt["draft_version"], detail["body"]["quote_session"]["draft_version"])

    def test_conditional_draft_commit_preserves_newer_metadata_ledger_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            database_url = f"sqlite:///{(Path(tmp) / 'draft-cas.sqlite3').as_posix()}"
            for artifact_mode in ("database", "object"):
                backend = webapp.InMemoryObjectStorageBackend()
                backend_context = (mock.patch.object(webapp, "configured_object_storage_backend", return_value=backend)
                                   if artifact_mode == "object" else contextlib.nullcontext())
                with self.subTest(artifact_mode=artifact_mode), backend_context, mock.patch.dict(os.environ, {
                    "APP_MODE": "local", webapp.SQAG_ARTIFACT_STORAGE_MODE_ENV_NAME: artifact_mode,
                }):
                    webapp.apply_sqag_storage_migrations(database_url)
                    storage = webapp.DatabaseSqagStorage(database_url, "workspace-draft-cas", "operator", "synthetic-owner")
                    session_id = f"quote-draft-cas-{artifact_mode}"

                    def payload(title, file_key):
                        return {"session_id": session_id, "customer_summary": {"project_name": title},
                                "draft_state": {"workflowStage": "basis_review"},
                                "draft_files": [{"session_file_key": file_key, "name": file_key + ".png",
                                                 "type": "image/png", "data_url": fixtures.SANITIZED_LOGO_DATA_URL}]}

                    create = {**payload("Synthetic initial", "initial"), "expected_draft_version": ""}
                    initial = webapp.save_quote_session_from_browser(create, storage)
                    original = storage._read_quote_session_metadata_for_workspace(session_id)
                    with self.assertRaises(webapp.PricingSelectionConflict):
                        webapp.save_quote_session_from_browser({
                            **payload("Synthetic duplicate creation", "other"), "expected_draft_version": "",
                        }, storage)
                    self.assertEqual(storage._read_quote_session_metadata_for_workspace(session_id), original)
                    newer = {**payload("Synthetic newer", "newer"), "expected_draft_version": initial["draft_version"]}
                    saved = webapp.save_quote_session_from_browser(newer, storage)
                    before = storage._read_quote_session_metadata_for_workspace(session_id)
                    self.assertEqual(before[1][0]["session_file_key"], "newer")
                    self.assertEqual(saved["draft_version"], storage.get_quote_session(session_id, include_draft_state=True)["draft_version"])
                    with self.assertRaises(webapp.PricingSelectionConflict):
                        webapp.save_quote_session_from_browser({**newer, "draft_files": []}, storage)
                    self.assertEqual(storage._read_quote_session_metadata_for_workspace(session_id), before)
                    without_files = {key: value for key, value in newer.items() if key != "draft_files"}
                    saved = webapp.save_quote_session_from_browser({
                        **without_files, "expected_draft_version": saved["draft_version"],
                        "draft_state": {"workflowStage": "details_review"},
                    }, storage)
                    after = storage._read_quote_session_metadata_for_workspace(session_id)
                    self.assertEqual(after[0]["draft_state"]["workflowStage"], "details_review")
                    self.assertEqual(after[1], before[1])
                    before = after
                    if artifact_mode == "object":
                        continue

                    commit = webapp.QuoteDraftPrecondition.persist_database
                    competing = []

                    def interleave_after_read(precondition, connection, workspace, metadata, draft_files):
                        def execute(sql, params):
                            cursor = connection.execute(sql, params)
                            if sql.startswith("select metadata_json, draft_files_json"):
                                previous = cursor.fetchone()
                                with storage.connection() as other:
                                    replacement = copy.deepcopy(before[0])
                                    replacement["customer_summary"]["project_name"] = "Synthetic competing tab"
                                    other.execute(
                                        "update sqag_quote_sessions set metadata_json = ? where workspace_id = ? and session_id = ?",
                                        (json.dumps(replacement), workspace, session_id),
                                    )
                                    other.commit()
                                competing.append(storage._read_quote_session_metadata_for_workspace(session_id))
                                return mock.Mock(fetchone=lambda: previous)
                            return cursor

                        return commit(precondition, mock.Mock(execute=execute), workspace, metadata, draft_files)

                    with mock.patch.object(webapp.QuoteDraftPrecondition, "persist_database", autospec=True,
                                           side_effect=interleave_after_read):
                        with self.assertRaises(webapp.PricingSelectionConflict):
                            webapp.save_quote_session_from_browser({
                                **payload("Synthetic stale follow-up", "stale"), "expected_draft_version": saved["draft_version"],
                            }, storage)
                    self.assertEqual(len(competing), 1)
                    self.assertEqual(storage._read_quote_session_metadata_for_workspace(session_id), competing[0])
                    self.assertEqual(competing[0][0]["pricing_ledger"], before[0]["pricing_ledger"])
                    self.assertEqual(competing[0][1], before[1])

                    raced_id = "quote-draft-create-race"
                    first = {**payload("Synthetic first creator", "first"), "session_id": raced_id,
                             "expected_draft_version": ""}
                    winner = {**payload("Synthetic winning creator", "winner"), "session_id": raced_id}
                    winning_rows = []

                    def insert_before_first(precondition, connection, workspace, metadata, draft_files):
                        storage.create_or_update_quote_session(winner)
                        winning_rows.append(storage._read_quote_session_metadata_for_workspace(raced_id))
                        return commit(precondition, connection, workspace, metadata, draft_files)

                    with mock.patch.object(webapp.QuoteDraftPrecondition, "persist_database", autospec=True,
                                           side_effect=insert_before_first):
                        with self.assertRaises(webapp.PricingSelectionConflict):
                            webapp.save_quote_session_from_browser(first, storage)
                    self.assertEqual(len(winning_rows), 1)
                    self.assertEqual(storage._read_quote_session_metadata_for_workspace(raced_id), winning_rows[0])

    def test_reinclude_requires_current_revision_unchanged_row_and_a_prior_grant(self):
        storage = webapp.LocalSqagStorage()
        for case in ('current', 'stale', 'changed', 'missing', 'missing-version', 'wrong-version', 'reference', 'other-tab-text',
                     'other-tab-section', 'other-tab-identity', 'other-tab-quantity', 'other-tab-unit',
                     'other-tab-item', 'other-tab-commercial', 'other-tab-unrelated', 'other-tab-draft',
                     'duplicate', 'mixed', 'mixed-ordinary-refused', 'legacy', 'legacy-no-ledger', 'legacy-forged',
                     'legacy-missing-seal', 'legacy-stale-seal',
                     'legacy-digest', 'legacy-reference-id', 'legacy-source', 'legacy-unit',
                     'legacy-item', 'legacy-quantity', 'legacy-duplicate', 'legacy-alias-row', 'legacy-wrong-scope',
                     'legacy-incoming-reference-id', 'legacy-incoming-digest', 'legacy-top-level-digest'):
            with self.subTest(case=case):
                payload, item = self.selection_payload('Synthetic cafe chairs at the synthetic round tables')
                row_id = payload['line_items'][0]['source_basis_line_id']
                session_id = f'quote-reinclude-{case}'
                if case == 'missing':
                    payload['quote_session'] = {'session_id': session_id}
                    original_revision = 0
                else:
                    payload, selected = self.fixture._owner_server_selection(payload, item, session_id)
                    original_revision = selected['quote_session']['pricing_ledger']['rows'][row_id]
                    payload['line_items'] = selected['line_items']
                self.fixture._ledger_line(payload, row_id)['tag'] = 'Exclude'
                if case == 'legacy-alias-row':
                    self.fixture._ledger_line(payload, row_id)['source_line_item_id'] = 'synthetic-alternate-row'
                self.fixture._ledger_sync(payload)
                recovered = copy.deepcopy(payload['line_items'])
                if case.startswith('legacy'):
                    snapshot = payload['quote_session']['draft_state']['originalAnalysisSnapshot']
                    snapshot.update(quote_basis_sections=copy.deepcopy(payload['quote_basis_sections']), line_items=copy.deepcopy(recovered))
                    snapshot['pricing_analysis_seal'] = webapp.issue_pricing_analysis_seal(
                        payload, recovered, source_units={row_id: recovered[0]['unit']},
                    )
                    if case == 'legacy-forged':
                        snapshot['pricing_analysis_seal']['mac'] = '0' * 64
                    if case == 'legacy-missing-seal':
                        snapshot.pop('pricing_analysis_seal')
                    if case == 'legacy-wrong-scope':
                        seal = snapshot['pricing_analysis_seal']
                        seal['mac'] = webapp.pricing_analysis_seal_mac(
                            webapp.pricing_analysis_seal_key(), 'other-synthetic-scope',
                            {key: value for key, value in seal.items() if key != 'mac'},
                        )
                    original = snapshot['line_items'][0]
                    if case in ('legacy-digest', 'legacy-reference-id', 'legacy-source', 'legacy-unit', 'legacy-item', 'legacy-quantity'):
                        for row in (original, recovered[0]):
                            if case == 'legacy-digest':
                                row['pricing_authority']['catalog_digest'] = 'sha256:' + '0' * 64
                            elif case == 'legacy-reference-id':
                                row['pricing_reference_id'] = 'synthetic-wrong-reference'
                            elif case == 'legacy-source':
                                row['pricing_authority']['catalog_source'] = 'database'
                            elif case == 'legacy-unit':
                                row['pricing_authority']['catalog_unit'] = 'lot'
                            elif case == 'legacy-item':
                                row['pricing_authority']['catalog_item_id'] = 'synthetic-wrong-item'
                            else:
                                row['quantity'] += 1
                    if case == 'legacy-duplicate':
                        snapshot['line_items'].append(copy.deepcopy(original))
                    if case == 'legacy-incoming-reference-id':
                        recovered[0]['pricing_reference_id'] = 'synthetic-wrong-reference'
                    if case == 'legacy-incoming-digest':
                        recovered[0]['pricing_authority']['catalog_digest'] = 'sha256:' + '0' * 64
                    payload['line_items'] = []
                    if case == 'legacy-alias-row':
                        payload['line_items'] = [{**recovered[0], 'source_basis_line_id': 'synthetic-alternate-row'}]
                    self.fixture._ledger_sync(payload)
                acknowledged = storage.create_or_update_quote_session(payload)
                saved = webapp.read_quote_session_metadata(session_id)
                if case == 'legacy-stale-seal':
                    seal = saved['draft_state']['originalAnalysisSnapshot']['pricing_analysis_seal']
                    saved['pricing_ledger']['analysis'] = {'issued_at': seal['issued_at'] + 1}
                    saved = webapp.write_quote_session_metadata(saved)
                    acknowledged = storage.get_quote_session(session_id, include_draft_state=True)
                if case == 'legacy-no-ledger':
                    saved.pop('pricing_ledger')
                    webapp.write_quote_session_metadata(saved)
                    acknowledged = storage.get_quote_session(session_id, include_draft_state=True)
                    # Recovery authority comes from saved evidence, even if the browser omits it.
                    payload['quote_session']['draft_state']['originalAnalysisSnapshot'].pop('pricing_analysis_seal')
                if case == 'reference':
                    ledger = copy.deepcopy(saved['pricing_ledger'])
                    ledger['rows'][row_id]['grant']['catalog_digest'] = 'sha256:' + '0' * 64
                    webapp.write_quote_session_pricing_ledger(storage, session_id, saved, ledger)
                    saved = webapp.read_quote_session_metadata(session_id)
                payload['line_items'] = copy.deepcopy(saved['draft_state']['lineItems'])
                payload['quote_basis_sections'] = copy.deepcopy(saved['draft_state']['quoteBasisSections'])
                if case.startswith('legacy'):
                    payload['line_items'] = recovered
                if case.startswith('other-tab'):
                    newer = copy.deepcopy(payload)
                    newer_line = self.fixture._ledger_line(newer, row_id)
                    if case == 'other-tab-commercial':
                        newer['quote_session']['customer_summary'] = {'project_name': 'Synthetic newer project'}
                    elif case == 'other-tab-unrelated':
                        newer['quote_basis_sections'].append({'id': 'synthetic-other', 'title': 'Synthetic other',
                            'lines': [{'id': 'synthetic-other-row', 'tag': 'Exclude', 'text': 'Synthetic later line'}]})
                    elif case == 'other-tab-section':
                        newer['quote_basis_sections'][0]['id'] += '-moved'
                    elif case == 'other-tab-text':
                        newer_line['text'] += ' for another position'
                    elif case != 'other-tab-draft':
                        key, value = {'identity': ('id', 'other-id'), 'quantity': ('quantity', 20),
                                      'unit': ('unit', 'sets'), 'item': ('pricing_keyword', 'other-item')}[case[10:]]
                        newer_line[key] = value
                    self.fixture._ledger_sync(newer)
                    if case == 'other-tab-draft':
                        newer['quote_session']['draft_state']['workflowStage'] = 'basis_review'
                    with mock.patch.object(webapp, 'utc_timestamp', return_value=saved['updated_at']):
                        updated = storage.create_or_update_quote_session(newer)
                    if case in ('other-tab-commercial', 'other-tab-unrelated', 'other-tab-draft'):
                        self.assertEqual(updated['pricing_ledger']['rows'][row_id], acknowledged['pricing_ledger']['rows'][row_id])
                        self.assertNotEqual(updated['draft_version'], acknowledged['draft_version'])
                line = self.fixture._ledger_line(payload, row_id)
                line['tag'] = 'Include'
                if case in ('changed', 'mixed'):
                    line['text'] += ' for another position'
                if case == 'current':
                    payload['line_items'][0]['catalog_unit_price'] = 999
                if case in ('current', 'legacy', 'legacy-no-ledger'):
                    payload['line_items'][0]['reincluded_catalog_selection'] = 'synthetic-browser-intent'
                if case == 'duplicate':
                    payload['line_items'].append(copy.deepcopy(payload['line_items'][0]))
                self.fixture._ledger_sync(payload)
                if case != 'missing-version':
                    payload['expected_draft_version'] = '0' * 64 if case == 'wrong-version' else acknowledged.get('draft_version')
                if case == 'legacy-top-level-digest':
                    payload['line_items'][0]['pricing_authority']['catalog_digest'] = 'sha256:' + '0' * 64
                request = {'source_basis_line_id': row_id, 'catalog_item_id': item['id'], 'reinclude': True,
                           'expected_revision': original_revision if case == 'stale' else
                           webapp.public_pricing_ledger(saved)['rows'].get(row_id, 0)}
                selections = [request]
                if case in ('mixed', 'mixed-ordinary-refused'):
                    independent = 'independent-row'
                    payload['line_items'].append({**payload['line_items'][0], 'source_basis_line_id': independent,
                                                  'pricing_authority': {'variant': 'none'}})
                    if case == 'mixed-ordinary-refused':
                        payload['line_items'][-1]['unit'] = 'lot'
                    payload['quote_basis_sections'][0]['lines'].append({**line, 'id': independent})
                    self.fixture._ledger_sync(payload)
                    selections.insert(0, {'source_basis_line_id': independent,
                                          'catalog_item_id': item['id'],
                                          'expected_revision': 0})
                self.assertIsNone(self.fixture._ledger_price(webapp.normalize_line_items_for_quote_basis_review(payload), row_id))
                persisted = webapp.read_quote_session_metadata(session_id)
                if case in ('current', 'legacy', 'legacy-no-ledger', 'mixed-ordinary-refused'):
                    with mock.patch.object(webapp, 'write_quote_session_metadata', wraps=webapp.write_quote_session_metadata) as writes:
                        result = webapp.apply_pricing_selections(payload, selections, storage)
                        self.assertEqual(writes.call_count, 1)
                    expected = [{'source_basis_line_id': row_id, 'status': 'selected'}]
                    if case == 'mixed-ordinary-refused':
                        expected.insert(0, {'source_basis_line_id': independent, 'status': 'refused', 'reason': 'unit_review_required'})
                        self.assertIsNone(self.fixture._ledger_price(result['line_items'], independent))
                    self.assertEqual(result['selections'], expected)
                    self.assertEqual(self.fixture._ledger_price(result['line_items'], row_id), webapp.pricing_reference_sale_unit_price(item))
                    committed = webapp.read_quote_session_metadata(session_id)
                    self.assertNotIn('reincluded_catalog_selection', committed['draft_state']['lineItems'][0])
                else:
                    for _ in range(2):
                        with self.assertRaises(webapp.PricingSelectionConflict):
                            webapp.apply_pricing_selections(payload, selections, storage)
                        self.assertTrue(webapp.read_quote_session_metadata(session_id) == persisted,
                                        'refused re-inclusion changed the saved quote')

    def test_reinclude_request_limits_preserve_all_supported_rows(self):
        def entries(count, ordinary=0):
            return [{'source_basis_line_id': f'synthetic-row-{i}', 'catalog_item_id': 'synthetic-item',
                     'expected_revision': 0, 'reinclude': i >= ordinary} for i in range(count)]

        for count, ordinary, accepted in ((100, 0, True), (101, 0, True), (200, 0, True),
                                          (200, 100, True), (100, 100, True), (101, 101, False),
                                          (201, 0, False), (200, 101, False)):
            with self.subTest(count=count, ordinary=ordinary):
                requests = webapp.pricing_selection_requests(entries(count, ordinary))
                self.assertEqual(len(requests) if requests else None, count if accepted else None)
        duplicate = entries(2)
        duplicate[1]['source_basis_line_id'] = duplicate[0]['source_basis_line_id']
        self.assertIsNone(webapp.pricing_selection_requests(duplicate))
        for key, value in (('reinclude', 1), ('expected_revision', True), ('catalog_item_id', ''),
                           ('catalog_item_id', ['synthetic-item'])):
            [entry] = entries(1)
            self.assertIsNone(webapp.pricing_selection_requests([{**entry, key: value}]))

    def test_browser_sends_whole_batches_and_adopts_only_a_complete_acknowledgement(self):
        harness = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const app = fs.readFileSync("webapp/static/app.js", "utf8");
let state, response, calls, recoveryCopies, quoteSessionDraftSaveTimer;
const quoteSessionDraftSavePromise = null;
const browserRecoveryEpoch = 0;
const sessionId = "quote-synthetic";
const baseline = "a".repeat(64);
const acknowledged = "b".repeat(64);
const safeQuoteSessionId = (id) => id;
const isPlainObject = (value) => value && typeof value === "object" && !Array.isArray(value);
const quoteSessionDraftStateCanSave = () => true;
const normalizeBasisTag = (tag) => tag;
const reincludedCatalogueSelectionKey = (_line, _section, row) => row.source_basis_line_id;
const buildLineItemNormalizePayload = () => ({});
const normalizeLineItem = (row) => row;
const pricingAuthorityPrice = (authority) => authority?.price ?? null;
const retainExcludedBasisPricingRows = (rows) => rows;
const clearQuoteSessionDraftSaveTimer = () => ({});
const saveSessionState = () => recoveryCopies.push(structuredClone(state));
const queueQuoteSessionDraftStateSave = () => assert.fail("unsafe autosave");
const postJson = async (path, payload) => {
  assert.equal(path, "/api/quote-sessions/pricing-selections");
  calls.push(payload);
  return response;
};
function extract(start, end) {
  const first = app.indexOf(start);
  const last = app.indexOf(end, first);
  assert(first >= 0 && last > first);
  return app.slice(first, last);
}
eval(extract("function validQuoteDraftVersion", "function captureOriginalAnalysisSnapshot")
  + extract("function pendingPricingSelections", "function openBlockingClarifications"));
function setup(count, ordinary = 0, version = baseline) {
  const ids = Array.from({length: count}, (_, index) => "synthetic-row-" + index);
  const pricing_ledger = {revision: 8, rows: Object.fromEntries(ids.map((id) => [id, 4]))};
  const selections = ids.map((id) => ({source_basis_line_id: id, status: "selected"}));
  const line_items = ids.map((id) => ({source_basis_line_id: id,
    pricing_authority: {variant: "catalog", catalog_item_id: "synthetic-item", price: 12}}));
  response = {ok: true, data: {status: "normalized", line_items, selections,
    quote_session: {session_id: sessionId, draft_version: acknowledged, pricing_ledger}}};
  state = {quoteSessionId: sessionId, pricingDraftVersion: version, pricingLedgerRevisions: {[sessionId]: {}},
    lineItems: line_items.map((row, index) => index < ordinary
      ? {...row, pricing_authority: {variant: "none"}} : {...row, reincluded_catalog_selection: row.source_basis_line_id}),
    quoteBasisSections: [{lines: ids.map((id) => ({id, tag: "Include", pricing_keyword: "synthetic-item"}))}]};
  calls = [];
  recoveryCopies = [];
  quoteSessionDraftSaveTimer = null;
}
(async () => {
  for (const [count, ordinary] of [[100, 0], [101, 0], [200, 0], [200, 100]]) {
    setup(count, ordinary);
    assert((await refreshLineItemsFromServer()).ok);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].expected_draft_version, baseline);
    assert.equal(calls[0].pricing_selections.length, count);
    assert.equal(calls[0].pricing_selections.filter((entry) => entry.reinclude).length, count - ordinary);
    assert.equal(state.pricingDraftVersion, acknowledged);
    assert.equal(recoveryCopies[0].pricingDraftVersion, false);
  }
  setup(200, 101);
  const capped = pendingPricingSelections();
  assert.equal(capped.filter((entry) => !entry.reinclude).length, 100);
  assert.deepEqual(capped.filter((entry) => entry.reinclude).map((entry) => entry.source_basis_line_id),
    state.lineItems.slice(101).map((row) => row.source_basis_line_id));
  setup(101, 101);
  assert.equal(pendingPricingSelections().length, 100);
  setup(2, 1);
  Object.assign(response.data.selections[0], {status: "refused", reason: "unit_review_required"});
  response.data.line_items[0].pricing_authority = {variant: "none"};
  assert((await refreshLineItemsFromServer()).ok);
  assert.equal(state.pricingDraftVersion, acknowledged);
  assert.deepEqual(state.pricingIssues, [{source_basis_line_id: "synthetic-row-0", reason: "unit_review_required"}]);
  assert.equal(state.lineItems[0].pricing_authority.variant, "none");
  for (const failure of ["stale", "unknown-reason", "refused-reinclude", "wrong-id", "stale-revision",
    "malformed-revision", "missing-data", "network", "no-baseline", "invalid-batch", "missing-row",
    "ordinary-authority", "ordinary-item", "ordinary-duplicate", "ordinary-refusal-conflicts"]) {
    setup(failure === "invalid-batch" ? 201 : 2, 1, failure === "no-baseline" ? "" : baseline);
    const ledger = response.data.quote_session.pricing_ledger;
    if (failure === "unknown-reason") Object.assign(response.data.selections[0], {status: "refused", reason: "stale"});
    if (failure === "refused-reinclude") Object.assign(response.data.selections[1], {status: "refused", reason: "unit_review_required"});
    if (failure === "wrong-id") response.data.selections[0].source_basis_line_id = "synthetic-row-1";
    if (failure === "stale-revision") ledger.rows["synthetic-row-1"] = 0;
    if (failure === "malformed-revision") ledger.rows["synthetic-row-0"] = "4";
    if (failure === "missing-data") delete response.data;
    if (failure === "missing-row") response.data.line_items.pop();
    if (failure === "ordinary-authority") delete response.data.line_items[0].pricing_authority;
    if (failure === "ordinary-item") response.data.line_items[0].pricing_authority.catalog_item_id = "synthetic-other";
    if (failure === "ordinary-duplicate") response.data.line_items.push(structuredClone(response.data.line_items[0]));
    if (failure === "ordinary-refusal-conflicts") Object.assign(response.data.selections[0], {status: "refused", reason: "unit_review_required"});
    if (failure === "stale") response = {ok: false, status: 409, data: {...response.data, status: "conflict"}};
    if (failure === "network") response = {ok: false, data: {fetch_failed: true}};
    if (failure === "invalid-batch") response = {ok: false, status: 400, data: {status: "blocked"}};
    const before = structuredClone(state.lineItems);
    assert(!(await refreshLineItemsFromServer()).ok);
    assert.equal(state.pricingDraftVersion, false);
    assert.deepEqual(state.pricingLedgerRevisions[sessionId], {});
    assert.deepEqual(state.lineItems, before);
    assert(!(await refreshLineItemsFromServer()).ok);
    assert.equal(calls.length, failure === "no-baseline" ? 0 : 1);
    if (failure === "invalid-batch") assert.equal(calls[0].pricing_selections.length, 201);
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run([fixtures.require_node(self), '-'], input=harness, text=True, capture_output=True,
                                cwd=fixtures.ROOT, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_full_reinclude_batch_commits_all_rows_in_one_request(self):
        with mock.patch.dict(os.environ, self.fixture._ledger_env()), fixtures.LocalRunnerServer() as runner:
            headers = self.fixture._ledger_runner_headers(runner)
            for count, ordinary in ((100, 0), (101, 0), (200, 0), (200, 100)):
                with self.subTest(count=count, ordinary=ordinary):
                    payload, item = self.selection_payload('Synthetic cafe chairs at the synthetic round tables')
                    session_id = f'quote-bulk-{count}-{ordinary}'
                    payload['quote_session'] = {'session_id': session_id}
                    row = payload['line_items'][0]
                    [section] = payload['quote_basis_sections']
                    [line] = section['lines']
                    reference = webapp.exact_pricing_reference_authority(payload)
                    row_ids = [f'synthetic-row-{index}' for index in range(count)]
                    payload['line_items'] = [
                        {**row, 'source_basis_line_id': row_id, 'pricing_keyword': item['id']} for row_id in row_ids
                    ]
                    for index, row in enumerate(payload['line_items']):
                        row['pricing_authority'] = webapp.build_pricing_authority(
                            'none' if index < ordinary else 'catalog', row,
                            price=webapp.pricing_reference_sale_unit_price(item),
                            reference_authority=reference, catalog_item=item,
                        )
                    section['lines'] = [{**line, 'id': row_id, 'tag': 'Confirm'} for row_id in row_ids]
                    payload['pricing_analysis_seal'] = webapp.issue_pricing_analysis_seal(
                        payload, payload['line_items'], source_units={row_id: 'nos' for row_id in row_ids},
                    )
                    storage = webapp.LocalSqagStorage()
                    storage.create_or_update_quote_session(self.fixture._ledger_sync(payload))
                    for line in section['lines'][ordinary:]:
                        line['tag'] = 'Exclude'
                    saved = storage.create_or_update_quote_session(self.fixture._ledger_sync(payload))
                    for line in section['lines']:
                        line['tag'] = 'Include'
                    self.fixture._ledger_sync(payload)
                    payload['expected_draft_version'] = saved['draft_version']
                    payload['pricing_selections'] = [
                        {'source_basis_line_id': row_id, 'catalog_item_id': item['id'],
                         'expected_revision': saved['pricing_ledger']['rows'][row_id], 'reinclude': index >= ordinary}
                        for index, row_id in enumerate(row_ids)
                    ]
                    route = '/api/quote-sessions/pricing-selections'
                    if count == 200 and ordinary == 100:
                        before = webapp.read_quote_session_metadata(session_id)
                        selections = payload['pricing_selections']
                        invalid_batches = [
                            [*selections, {**selections[-1], 'source_basis_line_id': 'synthetic-extra'}],
                            [*selections[:100], {**selections[100], 'reinclude': False}, *selections[101:]],
                            [*selections[:-1], selections[0]],
                            [*selections[:-1], {**selections[-1], 'expected_revision': '1'}],
                        ]
                        for invalid in invalid_batches:
                            response = self.fixture._ledger_post(runner, headers, route, {**payload, 'pricing_selections': invalid})
                            self.assertEqual(response['status'], 400)
                            self.assertEqual(webapp.read_quote_session_metadata(session_id), before)
                    with mock.patch.object(webapp, 'write_quote_session_metadata', wraps=webapp.write_quote_session_metadata) as writes:
                        self.fixture.reset_rate_limit_state()
                        response = self.fixture.http_json(runner, 'POST', route, body=payload, headers=headers, timeout=40)
                        self.assertEqual(response['status'], 200, response['text'][-500:])
                        self.assertEqual(writes.call_count, 1)
                    body = response['body']
                    self.assertEqual(body['selections'], [
                        {'source_basis_line_id': row_id, 'status': 'selected'} for row_id in row_ids
                    ])
                    reopened = storage.get_quote_session(session_id, include_draft_state=True)
                    self.assertEqual(reopened['draft_version'], body['quote_session']['draft_version'])
                    self.assertEqual(len(reopened['draft_state']['lineItems']), count)
                    price = webapp.pricing_reference_sale_unit_price(item)
                    expected_amounts = dict.fromkeys(row_ids, 3 * price)
                    self.assertEqual({row['source_basis_line_id']: row['pricing_basis_amount'] for row in body['line_items']},
                                     expected_amounts)
                    if count == 200 and not ordinary:
                        final_rows = webapp.normalize_line_items_for_final_brief(payload)
                        self.assertEqual({row['source_basis_line_id']: row['pricing_basis_amount'] for row in final_rows},
                                         expected_amounts)

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
