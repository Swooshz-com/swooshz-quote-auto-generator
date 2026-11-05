"""Pricing import operation context through the real bounded gateway; no network."""
import io
import json
import os
import socket
import unittest
import urllib.error
from unittest import mock
from webapp import server
from tests import test_webapp as existing
from tests.gateway_test_support import synthetic_configuration, responses_envelope, json_response, FixtureHttpResponse
from webapp.ai_gateway import OPENROUTER_RESPONSES_URL


def imported_rows():
    return {"currency":"SGD", "items":[{"section":"Synthetic structures", "description":"Synthetic panel PRIVATE_IMPORTED_DESCRIPTION", "unit_hint":"sqm", "internal_cost":50, "markup_multiplier":1.7, "remarks":[], "aliases":[]}]}


class PricingImportTelemetryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = existing.WebappServerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.enterContext(mock.patch.dict(os.environ,existing.isolated_env(OPENROUTER_API_KEY="synthetic-key",QUOTE_DATA_ROOT=str(self.fixture._test_data_root)),clear=True))
        self.enterContext(mock.patch.object(socket.socket,"connect",side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(socket,"create_connection",side_effect=AssertionError("NETWORK_DENIED")))

    def invoke(self, values, outcome):
        with mock.patch.object(server,"read_dotenv_value",side_effect=values.get),mock.patch.object(server.urllib.request,"urlopen",side_effect=[outcome] if outcome is not None else AssertionError("SEND_FORBIDDEN")) as send,mock.patch.object(server,"write_local_log",return_value=True) as logs:
            result=server.ai_pricing_reference_import_preview("PRIVATE_UPLOAD.xlsx",{"rows":[{"Item":"PRIVATE_SOURCE_CELL"}]},{"label":"GST","rate":0.09})
        attempts=[call.args[1] for call in logs.call_args_list if call.args[0]=="ai_call_attempt"]
        rollups=[call.args[1] for call in logs.call_args_list if call.args[0]=="ai_pricing_reference_import_timing"]
        self.assertEqual(len(rollups),1)
        rollup=rollups[0]
        self.assertRegex(rollup["ai_run_id"],r"^ai_[a-f0-9]{16}$")
        self.assertEqual(rollup["operator_stage"],"import_cleanup")
        self.assertEqual(rollup["source_file_extension"],"xlsx")
        for key in ("configuration","provider_request","validate_rows","total"):
            self.assertIsInstance(rollup["timings_ms"][key],int)
            self.assertGreaterEqual(rollup["timings_ms"][key],0)
        for entry in attempts:
            self.assertEqual(entry["ai_run_id"],rollup["ai_run_id"])
            self.assertEqual(entry["operator_stage"],"import_cleanup")
            self.assertEqual((entry["attempt_index"],entry["attempt_count"]),(1,1))
        private=json.dumps([call.args for call in logs.call_args_list],default=str)
        for canary in ("PRIVATE_UPLOAD","PRIVATE_SOURCE_CELL","PRIVATE_IMPORTED_DESCRIPTION","PRIVATE_PROVIDER_BODY","PRIVATE_TRANSPORT","PRIVATE_MODEL_OUTPUT","PRIVATE_ROUTING_MODEL","synthetic-key"):
            self.assertNotIn(canary,private)
        self.assertIsNone(getattr(server.AI_LOG_TRACKING_CONTEXT,"transport_operation",None))
        return result,send,attempts,rollup

    def test_success_rollup_shares_actual_send_context_and_truthful_usage_once(self):
        values=synthetic_configuration(credential="synthetic-key")
        outcome=json_response(values["OPENROUTER_PRICING_IMPORT_MODEL"],imported_rows(),usage={"input_tokens":2,"output_tokens":3,"total_tokens":5})
        result,send,attempts,rollup=self.invoke(values,outcome)
        send.assert_called_once()
        self.assertEqual(result["layout"],"ai-normalized-pricing-reference")
        self.assertEqual(result["sourceName"],"PRIVATE_UPLOAD.xlsx")
        self.assertEqual((rollup["selected_provider"],rollup["completed_provider"]),("openrouter","openrouter"))
        self.assertEqual((rollup["status"],rollup["raw_item_count"],rollup["row_count"]),("success",1,1))
        self.assertEqual(len(attempts),1)
        self.assertEqual((attempts[0]["status"],attempts[0]["attempt_number"]),("success",1))
        self.assertTrue(attempts[0]["usage_available"])
        self.assertEqual((attempts[0]["input_tokens"],attempts[0]["output_tokens"],attempts[0]["total_tokens"]),(2,3,5))
        self.assertEqual(len(rollup["provider_attempts"]),1)
        self.assertEqual(rollup["provider_attempts"][0]["total_tokens"],5)

    def test_failed_imports_have_one_actual_terminal_or_zero_send_validation(self):
        for case in ("configuration","credential","http","connection","refusal","provider_json","model_json"):
            values=synthetic_configuration(credential="synthetic-key")
            model=values["OPENROUTER_PRICING_IMPORT_MODEL"]
            if case=="configuration":
                values.pop("OPENROUTER_PRICING_IMPORT_MODEL")
                outcome=None
            elif case=="credential":
                values["OPENROUTER_API_KEY"]=""
                outcome=None
            elif case=="http":
                outcome=urllib.error.HTTPError(OPENROUTER_RESPONSES_URL,400,"PRIVATE_PROVIDER_BODY",{},io.BytesIO(b'{"error":{"type":"invalid_request_error","code":"unsupported_value","message":"PRIVATE_PROVIDER_BODY"}}'))
            elif case=="connection":
                outcome=urllib.error.URLError("PRIVATE_TRANSPORT")
            elif case=="refusal":
                outcome=FixtureHttpResponse(responses_envelope(model,"PRIVATE_PROVIDER_BODY",refusal="PRIVATE_PROVIDER_BODY"))
            elif case=="provider_json":
                outcome=FixtureHttpResponse.from_bytes(b"PRIVATE_PROVIDER_BODY")
            else:
                values["OPENROUTER_PRICING_IMPORT_MODEL"]="synthetic/PRIVATE_ROUTING_MODEL"
                outcome=FixtureHttpResponse(responses_envelope(values["OPENROUTER_PRICING_IMPORT_MODEL"],"PRIVATE_MODEL_OUTPUT"))
            with self.subTest(case=case):
                result,send,attempts,rollup=self.invoke(values,outcome)
                configuration_required=case in {"configuration","credential"}
                self.assertEqual(result["layout"],"ai-normalization-required" if configuration_required else "ai-normalization-failed")
                self.assertEqual(result["sourceName"],"PRIVATE_UPLOAD.xlsx")
                expected_errors=([server.AI_PRICING_IMPORT_NOT_CONFIGURED] if configuration_required else [])+server.generic_referenced_errors(result["error_reference"])
                self.assertEqual(result["errors"],expected_errors)
                self.assertFalse(result["canSave"])
                self.assertEqual(result["rowCount"],0)
                self.assertEqual(rollup["status"],"failed")
                self.assertEqual(rollup["completed_provider"],"")
                self.assertEqual(rollup["error_reference"],result["error_reference"])
                actual=[row for row in attempts if row["attempt_number"]>0]
                if configuration_required:
                    send.assert_not_called()
                    self.assertEqual(len(actual),0)
                    self.assertEqual(len(attempts),1)
                    self.assertEqual(attempts[0]["attempt_number"],0)
                    self.assertEqual(attempts[0]["failure_boundary"],"request_validation")
                    self.assertEqual(rollup["provider_attempts"],[])
                else:
                    send.assert_called_once()
                    self.assertEqual(len(actual),1)
                    self.assertEqual(actual[0]["attempt_number"],1)
                    self.assertEqual(len(rollup["provider_attempts"]),1)
                    # A completed Responses send can precede failed inner JSON admission.
                    self.assertEqual(actual[0]["status"],"success" if case=="model_json" else "failed")
