"""Diagnostic output retains safe outcome metadata without model or exception text."""
import json
import contextlib
import io
import socket
import sys
import unittest
from unittest import mock

from tests import test_webapp as support
import live_ai_basis_chat_smoke as smoke
import check_production_readiness as readiness


class DiagnosticPrivacyTest(unittest.TestCase):
    def test_live_smoke_missing_provider_reports_key_names_only(self):
        output = io.StringIO()
        with mock.patch.object(smoke, 'configured_provider', return_value=''), \
             mock.patch.object(smoke.webapp, 'answer_basis_chat') as provider, \
             mock.patch.object(sys, 'argv', ['live_ai_basis_chat_smoke.py']), \
             contextlib.redirect_stdout(output):
            self.assertEqual(smoke.main(), 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['error'], 'Configure DEEPSEEK_API_KEY or OPENAI_API_KEY in the local environment.')
        provider.assert_not_called()

    def test_readiness_prints_configuration_names_without_values(self):
        marker = 'SYNTHETIC_PRIVATE_CONFIGURATION_MATERIAL'
        for mode in ('local', 'production'):
            settings = {'APP_MODE': mode, 'SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY': marker,
                        'OIDC_CLIENT_SECRET': marker, 'SQAG_INTERNAL_SESSION_SECRET': marker}
            output = io.StringIO()
            with self.subTest(mode=mode), mock.patch.dict(support.webapp.os.environ, {}, clear=True), \
                 mock.patch.object(support.webapp, 'read_dotenv_value', side_effect=lambda key: settings.get(key, '')), \
                 mock.patch.object(socket, 'create_connection', side_effect=AssertionError('Network access forbidden')), \
                 contextlib.redirect_stdout(output):
                readiness.main([])
            report = output.getvalue()
            json.loads(report)
            self.assertNotIn(marker, report)
            self.assertIn('SQAG_OBJECT_STORAGE_SECRET_ACCESS_KEY', report)

    def test_live_smoke_keeps_model_text_internal(self):
        marker = 'Synthetic confidential quotation commentary'
        case = {'name': 'synthetic-case', 'basis_chat': {}, 'expect_type': 'answer'}
        with mock.patch.object(smoke, 'base_payload', return_value={}), \
             mock.patch.object(smoke.webapp, 'answer_basis_chat', return_value={
                 'type': 'answer', 'answer': marker, 'source': 'openai'}):
            result = smoke.run_case(case)
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn(marker, json.dumps(result))
        self.assertNotIn('summary', result)

    def test_live_smoke_keeps_provider_exception_text_internal(self):
        marker = 'Synthetic confidential provider request detail'
        case = {'name': 'synthetic-case', 'basis_chat': {}, 'expect_type': 'answer'}
        with mock.patch.object(smoke, 'base_payload', return_value={}), \
             mock.patch.object(smoke.webapp, 'answer_basis_chat', side_effect=support.webapp.OpenAIAnalysisError(marker)):
            result = smoke.run_case(case)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn(marker, json.dumps(result))
        self.assertEqual(result['error_code'], 'provider_request_failed')

    def test_live_smoke_still_rejects_forbidden_model_output(self):
        case = {'name': 'synthetic-case', 'basis_chat': {}, 'expect_type': 'answer'}
        with mock.patch.object(smoke, 'base_payload', return_value={}), \
             mock.patch.object(smoke.webapp, 'answer_basis_chat', return_value={
                 'type': 'answer', 'answer': 'Traceback: synthetic provider internals', 'source': 'openai'}):
            result = smoke.run_case(case)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['forbidden_output'])

    def test_live_smoke_emits_fixed_metadata_for_unexpected_types(self):
        marker = 'Synthetic private model metadata'
        case = {'name': 'synthetic-case', 'basis_chat': {}, 'expect_type': 'answer'}
        for source, actual in [(marker, marker), ({'value': marker}, 'answer')]:
            with self.subTest(source_type=type(source).__name__), \
                 mock.patch.object(smoke, 'base_payload', return_value={}), \
                 mock.patch.object(smoke.webapp, 'answer_basis_chat', return_value={
                     'type': actual, 'answer': marker, 'source': source}):
                result = smoke.run_case(case)
            self.assertNotIn(marker, json.dumps(result))
            self.assertEqual(result['source'], 'unknown')
