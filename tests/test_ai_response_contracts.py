"""Independent fresh-wire, routing and fail-closed controls; fake gateway only."""
import copy
import dataclasses
import io
import json
import socket
import tempfile
import unittest
import urllib.error
from unittest import mock

from tests.gateway_test_support import synthetic_configuration, responses_envelope
from tests.test_basis_quantity_retention import draft
from tests.test_openrouter_gateway import RecordingTransport, envelope
from tests import test_synthesis_contract_repair as repair_support
from tests import test_provider_response_admission as admission_support
from webapp import ai_gateway, ai_response_contracts as contracts, server
SynthesisTransport = repair_support.SynthesisTransport
CANARY = repair_support.CANARY
PATH = 'quote_basis_sections[0].lines[0].confidence_pct'


def canonical_draft():
    value = draft()
    value['analysis_findings'] = []
    for section in value['quote_basis_sections']:
        for line in section['lines']:
            line.setdefault('quantity', None)
            line.setdefault('unit', None)
            line['pricing_keyword'] = None
    for item in value['line_items']:
        item.update(price_mode=None, display_price=None)
    return value


class ContractDefinitionTest(unittest.TestCase):
    def test_independent_canonical_fields_and_recursive_closed_objects(self):
        schema = contracts.DRAFT_SYNTHESIS.text_format()
        self.assertEqual(set(schema), {'format'})
        fmt = schema['format']
        self.assertEqual(set(fmt), {'type', 'name', 'strict', 'schema'})
        self.assertEqual(fmt['type'], 'json_schema')
        self.assertEqual(fmt['name'], 'draft_synthesis_v1')
        self.assertIs(fmt.get('strict'), True)
        root = fmt['schema']
        self.assertEqual(set(root['required']), {'project', 'quote_basis_sections', 'line_items', 'analysis_findings'})
        line = root['properties']['quote_basis_sections']['items']['properties']['lines']['items']
        self.assertEqual(set(line['required']), {'id', 'tag', 'text', 'confidence_pct', 'quantity', 'unit', 'pricing_keyword'})
        self.assertEqual(line['properties']['confidence_pct']['type'], 'integer')
        self.assertEqual(line['properties']['quantity']['type'], ['number', 'null'])
        self.assertEqual(set(line['properties']['tag']['enum']), {'Include', 'Confirm', 'Custom', 'Exclude'})

        def walk(node):
            self.assertLessEqual(set(node), {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum'})
            if node['type'] == 'object':
                self.assertIs(node.get('additionalProperties'), False)
                self.assertEqual(set(node['required']), set(node['properties']))
                for child in node['properties'].values():
                    walk(child)
            if node['type'] == 'array':
                walk(node['items'])
        walk(root)
        self.assertIn('draft.quote_basis_sections[].lines[].confidence_pct: integer', contracts.DRAFT_SYNTHESIS.field_instructions())
        self.assertNotIn('confidence (or', contracts.DRAFT_SYNTHESIS.field_instructions())

    def test_contract_tree_generates_schema_admission_and_instructions_together(self):
        # An independent tiny future contract proves extensibility without freezing a matcher shape.
        contract = contracts.ResponseContract('future_probe', contracts.obj(probe=contracts.Field('string')))
        self.assertEqual(contract.text_format()['format']['schema']['required'], ['probe'])
        self.assertIn('requires probe', contract.field_instructions())
        self.assertEqual(contract.parse('{"probe":"ok"}'), {'probe': 'ok'})
        with self.assertRaises(contracts.ContractViolation):
            contract.parse('{}')

    def test_strict_local_structure_and_exact_json_before_semantic_consumers(self):
        value = canonical_draft()
        admitted = contracts.DRAFT_SYNTHESIS.parse(json.dumps(value))
        note = admitted['quote_basis_sections'][0]['lines'][-1]
        self.assertNotIn('quantity', note)
        self.assertNotIn('unit', note)
        self.assertNotIn('price_mode', admitted['line_items'][0])
        self.assertEqual(value, canonical_draft())
        bad = []
        for mutate in (
                lambda v: v.update(untrusted=CANARY),
                lambda v: v['project'].update(untrusted=CANARY),
                lambda v: v['quote_basis_sections'][0]['lines'][0].update(confidence=90),
                lambda v: v['quote_basis_sections'][0]['lines'][0].update(confidence_pct=True),
                lambda v: v['quote_basis_sections'][0]['lines'][0].update(confidence_pct=101),
                lambda v: v['line_items'][0].update(quantity='3'),
                lambda v: v['line_items'][0].update(quantity=10**1000),
                lambda v: v['line_items'][0].update(unit_price_override=2),
                lambda v: v.update(analysis_findings=[{'text': CANARY}]),
                lambda v: v.update(line_items=None)):
            row = canonical_draft(); mutate(row); bad.append(json.dumps(row))
        text = json.dumps(value)
        bad += ['```json\n'+text+'\n```', 'prefix '+text, text+' trailing', text+text,
                '['+text+']', '{"project":{},"project":{}}', '{"x":NaN}', '{"x":1e9999}']
        for text in bad:
            with self.subTest(text=text[:20]), self.assertRaises(contracts.ContractViolation) as caught:
                contracts.DRAFT_SYNTHESIS.parse(text)
            self.assertNotIn(CANARY, str(caught.exception))
        # Existing history aliases retain their own compatible admission.
        historical = draft(); historical['quote_basis_sections'][0]['lines'][0]['confidence'] = 90
        server.admit_provider_quote_draft(historical, require_confidence=True)

    def test_real_prompt_uses_generated_strict_fields_without_changing_legacy_wording(self):
        existing = admission_support.existing
        with mock.patch.dict(server.os.environ, existing.isolated_env(OPENROUTER_API_KEY='synthetic'), clear=True), \
             mock.patch.object(server, 'read_dotenv_value', side_effect=existing.env_only_dotenv_value), \
             mock.patch.object(server, 'profiles_root', return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT/'profiles'), \
             mock.patch.object(server, 'pricing_references_root', return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT/'pricing-references'):
            prompt = server.build_quote_draft_prompt(existing.valid_payload(), response_contract=contracts.DRAFT_SYNTHESIS)
        self.assertIn(contracts.DRAFT_SYNTHESIS.field_instructions(), prompt)
        self.assertNotIn('Omit analysis_findings', prompt)
        self.assertNotIn('Pure informational notes may omit', prompt)
        self.assertNotIn('or internal notes', prompt)


class GatewayContractTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_DENIED')))

    def config(self, enabled='true'):
        return ai_gateway.GatewayConfiguration.capture(synthetic_configuration(
            overrides={'OPENROUTER_DRAFT_SCHEMA_ENABLED': enabled}).get)

    def test_default_off_explicit_strict_setting_and_frozen_snapshot(self):
        self.assertFalse(ai_gateway.GatewayConfiguration.capture(synthetic_configuration().get).draft_schema_enabled)
        for setting in (None, '', 'false', 'true'):
            self.assertIs(self.config(setting).draft_schema_enabled, setting == 'true')
        for setting in ('TRUE', ' true', '1', True, 1):
            with self.subTest(setting=setting), self.assertRaises(ai_gateway.GatewayError):
                self.config(setting)
        values = synthetic_configuration(overrides={'OPENROUTER_DRAFT_SCHEMA_ENABLED': 'true'})
        config = ai_gateway.GatewayConfiguration.capture(values.get)
        values['OPENROUTER_DRAFT_SCHEMA_ENABLED'] = 'false'
        self.assertTrue(config.draft_schema_enabled)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.draft_schema_enabled = False

    def test_request_exact_schema_strict_policy_and_stage_isolation(self):
        config = self.config()
        for operation in ('draft', 'draft_high_quality'):
            route = config.route(operation)
            body = envelope(route); body['text'] = contracts.DRAFT_SYNTHESIS.text_format()
            transport = RecordingTransport([responses_envelope(route.model, json.dumps(canonical_draft()))])
            subject = ai_gateway.AiGateway(config, credential_reader=lambda: 'synthetic', transport=transport)
            subject.request(operation, body, response_contract='draft_synthesis_v1')
            sent = transport.calls[0][0]
            self.assertEqual(set(sent), {'model', 'input', 'reasoning', 'text', 'provider', 'store'})
            self.assertIs(sent['text']['format']['strict'], True)
            self.assertIs(sent['provider']['require_parameters'], True)
            self.assertIs(sent['provider']['allow_fallbacks'], False)
            self.assertEqual(sent['provider']['only'], [route.provider])
            self.assertEqual(sent['provider']['order'], [route.provider])
            self.assertEqual(sent['provider']['data_collection'], 'deny')
            self.assertIs(sent['provider']['zdr'], True)
            self.assertIs(sent['store'], False)
        for operation in ai_gateway.ROUTE_KEYS:
            for batch in (False, True):
                body = envelope(config.route(operation)); body['text'] = contracts.DRAFT_SYNTHESIS.text_format()
                transport = RecordingTransport([]); credential = mock.Mock(return_value='synthetic')
                subject = ai_gateway.AiGateway(config, credential_reader=credential, transport=transport)
                if operation in ('draft', 'draft_high_quality') and not batch:
                    continue
                with self.subTest(operation=operation, batch=batch), self.assertRaises(ai_gateway.GatewayError):
                    subject.request(operation, body, draft_batch=batch, response_contract='draft_synthesis_v1')
                credential.assert_not_called(); self.assertFalse(transport.calls)

    def test_missing_or_altered_contract_refused_before_credentials_and_send(self):
        config = self.config()
        original = envelope(config.route('draft')); original['text'] = contracts.DRAFT_SYNTHESIS.text_format()
        changes = [lambda b: b.pop('text'), lambda b: b['text'].pop('format'),
                   lambda b: b['text']['format'].pop('strict'),
                   lambda b: b['text']['format'].update(strict=1),
                   lambda b: b['text']['format']['schema']['properties']['project'].pop('additionalProperties'),
                   lambda b: b['text']['format']['schema']['properties']['quote_basis_sections']['items']['properties']['lines']['items']['required'].remove('confidence_pct')]
        for change in changes:
            body = copy.deepcopy(original); change(body)
            credential = mock.Mock(return_value='synthetic'); transport = RecordingTransport([])
            gateway = ai_gateway.AiGateway(config, credential_reader=credential, transport=transport)
            with self.subTest(change=change), self.assertRaises(ai_gateway.GatewayError):
                gateway.request('draft', body, response_contract='draft_synthesis_v1')
            credential.assert_not_called(); self.assertFalse(transport.calls)
        with self.assertRaises(ai_gateway.GatewayError):
            gateway.request('draft', original)  # trusted identity is separate from operation/body

    def test_schema_http_rejection_closed_reason_settlement_and_no_prose(self):
        config = self.config(); route = config.route('draft')
        body = envelope(route); body['text'] = contracts.DRAFT_SYNTHESIS.text_format()
        for status, error in ((400, {'code': 'invalid_json_schema'}), (404, {}),
                              (422, {}), (503, {'code': 'unsupported_parameter', 'param': 'text.format'})):
            raw = json.dumps({'error': {**error, 'message': CANARY}, 'model': route.model,
                              'usage': {'cost': 0.125}}).encode()
            http_error = urllib.error.HTTPError(ai_gateway.OPENROUTER_RESPONSES_URL, status, CANARY, {}, io.BytesIO(raw))
            opener = mock.Mock(); opener.open.side_effect = http_error
            spend = mock.Mock(); spend.tracks.return_value = True; spend.reserve.return_value = 1
            telemetry = mock.Mock(); sleep = mock.Mock()
            gateway = ai_gateway.AiGateway(config, credential_reader=lambda: 'synthetic',
                transport=ai_gateway.BoundedHttpsTransport(opener=opener), quote_spend=spend, telemetry=telemetry, sleep=sleep)
            with self.subTest(status=status), self.assertRaises(ai_gateway.GatewayError) as caught:
                gateway.request('draft', body, response_contract='draft_synthesis_v1')
            self.assertEqual(caught.exception.reason, 'response_schema_rejected')
            self.assertEqual(caught.exception.diagnostics['failure_boundary'], 'provider_response_schema')
            self.assertEqual(caught.exception.diagnostics['attempt_number'], 1)
            self.assertNotIn(CANARY, str(caught.exception)+json.dumps(caught.exception.diagnostics))
            opener.open.assert_called_once(); sleep.assert_not_called()
            spend.settle.assert_called_once_with(1, 0.125)
            self.assertEqual(telemetry.call_count, 1)
        self.assertEqual(ai_gateway.safe_draft_error_param('text.format.schema.'+CANARY), '')

    def test_complete_schema_size_preflight_and_shape_fingerprint(self):
        gateway = ai_gateway.AiGateway(self.config(), credential_reader=mock.Mock(), transport=RecordingTransport([]))
        route = gateway.configuration.route('draft')
        config = server.OpenAIDraftConfiguration('standard', route.model, route.effort, gateway)
        content = [{'type': 'input_text', 'text': 'synthetic'}]
        body = server.draft_request_body(config, content, final_draft=True)
        self.assertIn('text', body)
        encoded, shape = server.validate_draft_responses_envelope(body, 'standard', draft_configuration=config,
            synthesis=True, response_contract=contracts.DRAFT_SYNTHESIS)
        for length, accepts in ((len(encoded)-1, False), (len(encoded), True), (len(encoded)+1, True)):
            with mock.patch.object(server, 'MAX_DRAFT_RESPONSES_BYTES', length):
                if accepts:
                    server.draft_request_fingerprint(body, config, synthesis=True, final_draft=True)
                else:
                    with self.assertRaises(server.OpenAIAnalysisError):
                        server.draft_request_fingerprint(body, config, synthesis=True, final_draft=True)
        changed = copy.deepcopy(body); changed['input'][0]['content'][0]['text'] = CANARY
        self.assertEqual(shape, server.draft_request_fingerprint(changed, config, synthesis=True, final_draft=True))
        plain = server.draft_request_body(config, content)
        self.assertNotEqual(shape, server.draft_request_fingerprint(plain, config, synthesis=True))
        for mutate in (lambda b: b.pop('text'), lambda b: b['text']['format'].update(strict=False)):
            changed = copy.deepcopy(body); mutate(changed)
            with self.assertRaises(server.OpenAIAnalysisError):
                server.draft_request_fingerprint(changed, config, synthesis=True, final_draft=True)

    def test_failed_success_envelope_schema_machine_errors_and_unknown_usage(self):
        config = self.config(); route = config.route('draft')
        body = envelope(route); body['text'] = contracts.DRAFT_SYNTHESIS.text_format()
        for error in ({'error': {'code': 'unsupported_schema', 'message': CANARY}},
                      {'error_type': 'unsupported_schema'}, {'error': {'type': 'invalid_json_schema'}}):
            data = {'status': 'failed', 'model': route.model, **error}
            transport = RecordingTransport([data]); spend = mock.Mock(); spend.tracks.return_value = True
            gateway = ai_gateway.AiGateway(config, credential_reader=lambda: 'synthetic', transport=transport, quote_spend=spend)
            with self.subTest(error=error), self.assertRaises(ai_gateway.GatewayError) as caught:
                gateway.request('draft', body, response_contract='draft_synthesis_v1')
            self.assertEqual(caught.exception.reason, 'response_schema_rejected')
            self.assertFalse(caught.exception.retryable)
            self.assertNotIn(CANARY, str(caught.exception)+json.dumps(caught.exception.diagnostics))
            spend.settle.assert_called_once_with(spend.reserve.return_value, None)
            self.assertEqual(len(transport.calls), 1)

    def test_gateway_schema_size_limit_before_credential_access(self):
        config = self.config(); route = config.route('draft')
        body = envelope(route); body['text'] = contracts.DRAFT_SYNTHESIS.text_format()
        full = {**body, 'provider': config.provider_policy('draft'), 'store': False}
        size = len(json.dumps(full).encode())
        for limit, allowed in ((size-1, False), (size, True), (size+1, True)):
            transport = RecordingTransport([responses_envelope(route.model, json.dumps(canonical_draft()))])
            credential = mock.Mock(return_value='synthetic')
            gateway = ai_gateway.AiGateway(config, credential_reader=credential, transport=transport)
            with mock.patch.object(ai_gateway, 'MAX_REQUEST_BYTES', limit):
                if allowed:
                    gateway.request('draft', body, response_contract='draft_synthesis_v1')
                    credential.assert_called_once()
                else:
                    with self.assertRaises(ai_gateway.GatewayError):
                        gateway.request('draft', body, response_contract='draft_synthesis_v1')
                    credential.assert_not_called(); self.assertFalse(transport.calls)


class StrictSynthesisTest(unittest.TestCase):
    # Reuse only the fake-send/real-admission harness, not its legacy test cases.
    log = repair_support.SynthesisContractRepairTest.log
    usage = repair_support.SynthesisContractRepairTest.usage
    assert_sends = repair_support.SynthesisContractRepairTest.assert_sends

    def setUp(self):
        repair_support.SynthesisContractRepairTest.setUp(self)
        self.values['OPENROUTER_DRAFT_SCHEMA_ENABLED'] = 'true'

    def analyse(self, transport, *, batches=3, terminal=False, single=False, mode='standard'):
        media = {'type': 'input_image', 'image_url': self.image, 'detail': 'high'}
        prepared = server.PreparedDraftMedia([media] if single else [], [] if single else [
            [(f'Reference 1, PDF page {index*6+page}', media) for page in range(1, 7)] for index in range(batches)])
        with mock.patch.object(ai_gateway, 'BoundedHttpsTransport', return_value=transport):
            gateway = server.configured_ai_gateway()
        route = gateway.configuration.route('draft_high_quality' if mode == 'high_quality' else 'draft')
        config = server.OpenAIDraftConfiguration(mode, route.model, route.effort, gateway)
        payload = {'images': [{'name': 'synthetic.png', 'data_url': self.image}], 'analysis_mode': mode}
        with mock.patch.object(server, 'prepare_draft_media', return_value=prepared):
            if not terminal:
                return server.request_openai_quote_basis(payload, 'synthetic', draft_configuration=config)
            with mock.patch.object(server, 'capture_openai_draft_configuration', return_value=config), \
                 mock.patch.object(server, 'pricing_reference_snapshot_for_payload', return_value=object()), \
                 mock.patch.object(server, 'pricing_reference_authority_error', return_value=''), \
                 mock.patch.object(server, 'new_error_reference', return_value='ERR-01234567'):
                return server.draft_quote_basis(payload)

    def test_single_and_batched_standard_and_high_quality_use_final_contract(self):
        for mode in ('standard', 'high_quality'):
            for single in (True, False):
                self.logs.clear(); transport = SynthesisTransport([canonical_draft()])
                result = self.analyse(transport, mode=mode, single=single)
                self.assertTrue(result['quote_basis_sections'])
                self.assert_sends(transport, 0 if single else 3, 1)
                self.assertEqual(transport.calls[-1]['text'], contracts.DRAFT_SYNTHESIS.text_format())
                self.assertEqual(transport.calls[-1]['model'], 'synthetic/'+('draft_high_quality' if mode == 'high_quality' else 'draft'))

    def test_batched_schema_preflight_rejects_before_any_observation_send(self):
        transport = SynthesisTransport([canonical_draft()])
        with mock.patch.object(server, 'MAX_DRAFT_RESPONSES_BYTES', 1000), self.assertRaises(server.OpenAIAnalysisError):
            self.analyse(transport)
        self.assertFalse(transport.calls)

    def test_strict_synthesis_repair_once_same_schema_route_and_no_page_replay(self):
        missing = canonical_draft(); missing['quote_basis_sections'][0]['lines'][0].pop('confidence_pct')
        missing['quote_basis_sections'][0]['lines'][0]['text'] = 'rejected-output-only-canary'
        transport = SynthesisTransport([missing, canonical_draft()])
        result = self.analyse(transport, batches=3)
        self.assertTrue(result['quote_basis_sections'])
        self.assert_sends(transport, 3, 2)
        for body in transport.calls[:3]:
            self.assertNotIn('text', body)
        first, repair = transport.calls[-2:]
        self.assertEqual(first['text'], repair['text'])
        self.assertEqual(first['text'], contracts.DRAFT_SYNTHESIS.text_format())
        prompt = first['input'][0]['content'][0]['text']
        self.assertIn('Use null quantity and unit for genuinely unmeasured or purely informational lines.', prompt)
        self.assertIn('Keep unmeasured physical and service scope in Quote Basis only, with unknown fields null.', prompt)
        self.assertIn('Do not emit line_items for informational or clarification-only notes.', prompt)
        self.assertIn('Derive area only from coherent dimensions and confirmed coverage of the same scope.', prompt)
        for key in ('model', 'reasoning', 'provider', 'store'):
            self.assertEqual(first[key], repair[key])
        self.assertIn(contracts.DRAFT_SYNTHESIS.field_instructions(), repair['input'][0]['content'][0]['text'])
        note = repair['input'][0]['content'][0]['text'][len(first['input'][0]['content'][0]['text']):]
        self.assertNotIn(CANARY, note)
        self.assertNotIn('rejected-output-only-canary', repair['input'][0]['content'][0]['text'])
        self.assertEqual(self.usage()['synthesis_repair_count'], 1)
        self.assertEqual(self.usage()['response_contract_missing_paths'], [PATH])

    def test_strict_state_matrix_no_repair_or_normalisation_for_invalid_output(self):
        missing = canonical_draft(); missing['quote_basis_sections'][0]['lines'][0].pop('confidence_pct')
        invalid = canonical_draft(); invalid['quote_basis_sections'][0]['lines'][0]['confidence_pct'] = 101
        extra = canonical_draft(); extra['project']['untrusted'] = CANARY
        mixed = copy.deepcopy(missing); mixed['line_items'][0]['quantity'] = True
        conflict = copy.deepcopy(missing); conflict['line_items'][0]['quantity'] += 1
        collision = copy.deepcopy(missing); collision['quote_basis_sections'][0]['lines'][1]['id'] = 'floor'
        foreign = copy.deepcopy(missing); foreign['line_items'][0]['source_basis_line_id'] = 'foreign'
        for name, output in (('missing-single', missing), ('invalid', invalid), ('extra', extra),
                             ('mixed', mixed), ('conflict', conflict), ('collision', collision), ('foreign', foreign),
                             ('json', 'prefix '+json.dumps(canonical_draft())), ('refusal', 'refusal')):
            self.logs.clear(); transport = SynthesisTransport([output])
            with self.subTest(state=name), mock.patch.object(server, 'normalize_ai_draft') as normalize, \
                 mock.patch.object(server, 'issue_pricing_analysis_seal') as seal, \
                 self.assertRaises(server.OpenAIAnalysisError):
                self.analyse(transport, batches=3, single=(name == 'missing-single'))
            normalize.assert_not_called(); seal.assert_not_called()
            self.assert_sends(transport, 0 if name == 'missing-single' else 3, 1)

    def test_each_eligible_canonical_absent_field_repairs_but_clarifications_do_not(self):
        for change in (lambda value: value['quote_basis_sections'][0].pop('lines'),
                       lambda value: value['quote_basis_sections'][0]['lines'][0].pop('text'),
                       lambda value: value['line_items'][0].pop('description'),
                       lambda value: value.update(analysis_findings=[{'id': None, 'confidence_pct': None}])):
            self.logs.clear(); value = canonical_draft(); change(value)
            transport = SynthesisTransport([value, canonical_draft()])
            result = self.analyse(transport)
            self.assertTrue(result['quote_basis_sections'])
            self.assert_sends(transport, 3, 2)
        value = canonical_draft(); value['blocking_clarification_questions'] = [{'id': 'probe'}]
        transport = SynthesisTransport([value])
        with self.assertRaises(server.AIModelOutputError):
            self.analyse(transport)
        self.assertEqual(len(transport.calls), 4)

    def test_strict_missing_field_backstop_disabled_exhausted_and_second_failure(self):
        missing = canonical_draft(); missing['quote_basis_sections'][0]['lines'][0].pop('confidence_pct')
        for batches, enabled, count, outcome in ((3, False, 1, 'not_attempted'),
                                               (17, True, 1, 'budget_exhausted'), (3, True, 2, 'failed')):
            self.logs.clear(); transport = SynthesisTransport([missing, missing])
            with self.subTest(batches=batches, enabled=enabled), mock.patch.object(server, 'ENABLE_SYNTHESIS_CONTRACT_REPAIR', enabled), \
                 self.assertRaises(server.AIModelOutputError):
                self.analyse(transport, batches=batches)
            self.assert_sends(transport, batches, count)
            self.assertEqual(self.usage()['synthesis_repair_count'], count-1)
            if enabled:
                self.assertEqual(self.usage()['synthesis_repair_outcome'], outcome)

    def test_rejection_blocks_local_mode_before_normalisation_sealing_and_finalisation(self):
        class Rejected(SynthesisTransport):
            def send(self, encoded, **kwargs):
                body = json.loads(encoded)
                if body['input'][0]['content'][0]['text'].startswith('Inspect each attached visual'):
                    return super().send(encoded, **kwargs)
                self.calls.append(body)
                error = ai_gateway.GatewayError('http_error', boundary='provider_http', http_status=503, retryable=True)
                error.diagnostics.update(provider_error_code='unsupported_parameter', provider_error_param='text.format')
                raise error
        for single in (True, False):
            self.logs.clear(); transport = Rejected([])
            with self.subTest(single=single), \
                 mock.patch.object(server, 'protected_ai_draft_mode_enabled', return_value=False), \
                 mock.patch.object(server, 'normalize_ai_draft') as normalize, \
                 mock.patch.object(server, 'issue_pricing_analysis_seal') as seal, \
                 mock.patch.object(server, 'finalized_remote_draft_result') as finalise, \
                 mock.patch.object(server, 'default_quote_basis') as fallback, \
                 mock.patch.object(server, 'completed_draft_with_pricing_reference_snapshot') as saveable:
                result = self.analyse(transport, batches=3, single=single, terminal=True)
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['error_reference'], 'ERR-01234567')
            self.assertNotIn('line_items', result)
            for guard in (normalize, seal, finalise, fallback, saveable):
                guard.assert_not_called()
            self.assertEqual(len(transport.calls), 1 if single else 4)
            failure = [row for event, row in self.logs if event == 'openai_draft_failed'][-1]
            self.assertEqual(failure['response_contract_reason'], 'response_schema_rejected')
            self.assertEqual(failure['error_reference'], result['error_reference'])
            self.assertNotIn(CANARY, json.dumps(self.logs)+json.dumps(result))


class StrictDraftHTTPTest(unittest.TestCase):
    def setUp(self):
        self.fixture = admission_support.existing.WebappServerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        # The runner also denies external DNS/connect; retain a suite-local guard.
        connect = socket.create_connection
        def loopback(address, *args, **kwargs):
            if address[0] not in ('127.0.0.1', '::1', 'localhost'):
                raise AssertionError('NETWORK_DENIED')
            return connect(address, *args, **kwargs)
        self.enterContext(mock.patch.object(socket, 'create_connection', side_effect=loopback))

    def test_schema_rejection_http_503_preserves_saved_draft_and_ledger(self):
        existing = admission_support.existing
        with tempfile.TemporaryDirectory(dir=existing.test_temp_root()) as temp:
            reference_id = 'strict-response-reference'
            existing.write_test_pricing_reference(existing.Path(temp), reference_id, admission_support.CATALOG)
            payload = existing.valid_payload()
            payload.update(images=[{'name': 'synthetic.png', 'data_url': repair_support.synthetic_image()}],
                pricing_reference_id=reference_id, pricing_reference_source='local',
                pricing_reference={'id': reference_id, 'source': 'local', 'currency': 'SGD'},
                quote_session={'session_id': 'quote-strict-response'})
            env = existing.isolated_env(OPENROUTER_API_KEY='synthetic-mocked-only', AUTH_REQUIRED='false',
                OPENROUTER_DRAFT_SCHEMA_ENABLED='true', QUOTE_DATA_ROOT=str(self.fixture._test_data_root))
            raw = json.dumps({'error': {'code': 'invalid_json_schema', 'message': CANARY}}).encode()
            error = urllib.error.HTTPError(ai_gateway.OPENROUTER_RESPONSES_URL, 400, CANARY, {}, io.BytesIO(raw))
            with mock.patch.dict(server.os.environ, env, clear=True), \
                 mock.patch.object(server, 'read_dotenv_value', side_effect=existing.env_only_dotenv_value), \
                 mock.patch.object(server, 'pricing_references_root', return_value=existing.Path(temp)), \
                 mock.patch.object(server, 'protected_ai_draft_mode_enabled', return_value=False), \
                 mock.patch.object(server, 'write_local_log') as logs, existing.LocalRunnerServer() as runner:
                headers = self.fixture._ledger_runner_headers(runner)
                saved = self.fixture._ledger_post(runner, headers, '/api/quote-sessions', {'quote_session': payload['quote_session']})
                self.assertEqual(saved['status'], 200)
                before = self.fixture.http_json(runner, 'GET', '/api/quote-sessions/quote-strict-response')['body']['quote_session']
                with mock.patch.object(server.urllib.request, 'urlopen', side_effect=error) as send, \
                     mock.patch.object(server, 'normalize_ai_draft') as normalize, \
                     mock.patch.object(server, 'issue_pricing_analysis_seal') as seal, \
                     mock.patch.object(server, 'default_quote_basis') as fallback:
                    drafted = self.fixture._ledger_post(runner, headers, '/api/draft', payload)
                self.assertEqual(drafted['status'], 503)
                self.assertEqual(drafted['body']['status'], 'blocked')
                self.assertNotIn('line_items', drafted['body'])
                for guard in (normalize, seal, fallback):
                    guard.assert_not_called()
                send.assert_called_once()
                after = self.fixture.http_json(runner, 'GET', '/api/quote-sessions/quote-strict-response')['body']['quote_session']
                self.assertIsInstance(before['draft_state'], dict)
                self.assertEqual(before['draft_state'], after['draft_state'])
                self.assertEqual(before.get('pricing_ledger'), after.get('pricing_ledger'))
                failure = next(call.args[1] for call in logs.call_args_list if call.args[0] == 'openai_draft_failed')
                self.assertEqual(failure['error_reference'], drafted['body']['error_reference'])
                self.assertEqual(failure['response_contract_reason'], 'response_schema_rejected')
                self.assertNotIn(CANARY, json.dumps([call.args for call in logs.call_args_list])+json.dumps(drafted['body']))


if __name__ == '__main__':
    unittest.main()
