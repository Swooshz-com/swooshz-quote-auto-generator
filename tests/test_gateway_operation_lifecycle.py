"""All-route correlation ownership across pre-send, terminal and nested exits."""
import copy
import io
import json
import os
import socket
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from webapp import ai_gateway, server
from tests.gateway_test_support import synthetic_configuration, json_response, FixtureHttpResponse, responses_envelope, install_mock_responses_transport
from tests import test_webapp as existing
from tests.test_openai_draft_request_contract import synthetic_image
from tests.test_provider_response_admission import CATALOG, draft
from tests.test_pricing_import_telemetry import imported_rows

FEATURES={"draft":"draft_quote_basis","draft_high_quality":"draft_quote_basis","basis_line":"basis_chat","basis_answer":"basis_chat","pricing_import":"pricing_reference_import","pricing_metadata":"pricing_reference_metadata_enrichment"}


class GatewayOperationLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.values=synthetic_configuration(credential="synthetic-key")
        self.enterContext(mock.patch.dict(os.environ,{"APP_MODE":"local","SQAG_DISABLE_DOTENV":"1"},clear=True))
        install_mock_responses_transport(self)
        self.enterContext(mock.patch.object(server,"read_dotenv_value",side_effect=self.values.get))
        self.enterContext(mock.patch.object(socket.socket,"connect",side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(socket,"create_connection",side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(server,"profiles_root",return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT / "profiles"))
        self.enterContext(mock.patch.object(server,"pricing_references_root",return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT / "pricing-references"))
        self.detail={"id":"synthetic-lifecycle-reference","source":"local","currency":"SGD","tax":{"label":"GST","rate":0},"items":copy.deepcopy(CATALOG)}
        self.detail["digest_sha256"]=server.pricing_reference_catalog_digest(self.detail)
        self.snapshot=server.PricingReferenceSnapshot(source="local",reference_id=self.detail["id"],digest=self.detail["digest_sha256"],currency="SGD",detail_json=json.dumps(self.detail))
        self.logs=self.enterContext(mock.patch.object(server,"write_local_log",return_value=True))
        self.send=self.enterContext(mock.patch.object(server.urllib.request,"urlopen"))

    def model(self,operation):
        return self.values["OPENROUTER_"+ai_gateway.ROUTE_KEYS[operation]+"_MODEL"]

    def payload(self):
        return {"images":[{"name":"synthetic.png","data_url":synthetic_image()}],
                "profile_id":"synthetic-exhibition-fixture-template",
                "pricing_reference_id":self.detail["id"],"pricing_reference_source":"local",
                "pricing_reference":copy.deepcopy(self.detail),"_pricing_reference_snapshot":self.snapshot,
                "project":{"title":"Synthetic booth","booth_width":4,"booth_depth":3}}

    def source_rows(self):
        return [{"id":"synthetic-row","section":"Synthetic structures","description":"Synthetic panel",
                 "unit_hint":"sqm","internal_cost":50,"markup_multiplier":1.7,"sale_unit_price":85,
                 "remarks":[],"aliases":[]}]

    def response_payload(self,operation):
        if operation in {"draft","draft_high_quality"}:
            return draft()
        if operation=="basis_line":
            return {"intent":"proposal","proposal":{"message":"Change the platform height?",
                    "replacement_line":{"tag":"Confirm","text":"150mm raised platform with needle punch carpet.","confidence_pct":90}}}
        if operation=="basis_answer":
            return {"intent":"answer","answer":"Synthetic answer"}
        if operation=="pricing_import":
            return imported_rows()
        return {"items":[{"id":row["id"],"match_terms":[row["description"].lower()],"object_families":["synthetic_panel"]} for row in self.source_rows()]}

    def success_response(self,operation):
        return json_response(self.model(operation),self.response_payload(operation))

    def invoke(self,operation,gateway=None,*,missing_media=False):
        payload=self.payload()
        if operation in {"draft","draft_high_quality"}:
            mode="high_quality" if operation=="draft_high_quality" else "standard"
            payload["analysis_mode"]=mode
            if missing_media:payload["images"]=[]
            config=None if gateway is None else server.OpenAIDraftConfiguration(mode,self.model(operation),"high",gateway)
            result=server.request_openai_quote_basis(payload,"synthetic-key",draft_configuration=config)
            self.assertTrue(result["line_items"])
            return result
        if operation in {"basis_line","basis_answer"}:
            payload["quote_basis_sections"]=[{"id":"platform","title":"Platform","lines":[{"tag":"Confirm","text":"100mm raised platform with needle punch carpet."}]}]
            payload["quote_basis"]=server.quote_basis_from_sections(payload["quote_basis_sections"])
            payload["basis_chat"]={"question":"change 100mm to 150mm" if operation=="basis_line" else "What is included?",
                "scope":"line" if operation=="basis_line" else "quote","field":"platform","line_index":0,
                "line":"Confirm: 100mm raised platform with needle punch carpet." if operation=="basis_line" else ""}
            result=server.request_configured_basis_chat(payload)
            self.assertEqual(result["type"],"proposal" if operation=="basis_line" else "answer")
            if operation=="basis_answer":self.assertEqual(result["answer"],"Synthetic answer")
            return result
        rows=self.source_rows()
        if operation=="pricing_import":
            parsed=server.request_openai_pricing_catalog_import("synthetic.csv",{"rows":rows},{"label":"GST","rate":0},gateway=gateway)
            self.assertTrue(parsed["items"])
            result=server.validate_pricing_reference_rows(parsed["items"],list(server.PRICING_REFERENCE_TEMPLATE_COLUMNS),"synthetic.csv")
            self.assertEqual(result["rowCount"],1)
            self.assertTrue(result["canSave"])
            return result
        original=copy.deepcopy(rows)
        parsed=server.request_openai_pricing_catalog_metadata("synthetic.csv",rows,gateway=gateway)
        self.assertTrue(parsed["items"])
        enriched,errors=server.merge_pricing_reference_ai_metadata(rows,parsed["items"])
        self.assertEqual(errors,[])
        self.assertEqual(server.pricing_reference_metadata_quality_errors(enriched),[])
        self.assertEqual(rows,original)
        for source,row in zip(original,enriched):
            for key in ("id","description","unit_hint","internal_cost","markup_multiplier","sale_unit_price"):
                self.assertEqual(row[key],source[key])
        return parsed

    def attempts(self):
        return [call.args[1] for call in self.logs.call_args_list if call.args[0]=="ai_call_attempt"]

    def parent(self,operation):
        owner=server.AiTransportOperationScope(server.AI_LOG_TRACKING_CONTEXT,FEATURES[operation])
        owner.__enter__()
        server.begin_ai_transport_operation(FEATURES[operation],"openrouter",self.model(operation))
        owner.operation.update(attempt_number=7,usage={"input_tokens":101,"output_tokens":202,"total_tokens":303})
        return owner

    def test_all_six_routes_restore_identical_same_feature_parent_on_every_exit(self):
        cases=("missing_route","missing_key","malformed_key","envelope","success","http","refusal","shape","model_json")
        for operation in ai_gateway.ROUTE_KEYS:
            route_cases=cases+("missing_media",) if operation in {"draft","draft_high_quality"} else cases
            for case in route_cases:
                with self.subTest(operation=operation,case=case):
                    saved_values=dict(self.values);self.logs.reset_mock();self.send.reset_mock(side_effect=True)
                    parent=self.parent(operation);before=copy.deepcopy(parent.operation)
                    self.send.return_value=self.success_response(operation)
                    target="OPENROUTER_"+ai_gateway.ROUTE_KEYS[operation]+"_MODEL"
                    try:
                        if case=="missing_route":self.values.pop(target)
                        elif case=="missing_key":self.values.pop("OPENROUTER_API_KEY")
                        elif case=="malformed_key":self.values["OPENROUTER_API_KEY"]="synthetic invalid credential"
                        elif case=="http":self.send.side_effect=urllib.error.HTTPError(ai_gateway.OPENROUTER_RESPONSES_URL,400,"PRIVATE_REASON",{},io.BytesIO(b'{"error":{"type":"invalid_request_error"}}'))
                        elif case=="refusal":self.send.return_value=FixtureHttpResponse(responses_envelope(self.model(operation),"PRIVATE_BODY",refusal="PRIVATE_REFUSAL"))
                        elif case=="shape":self.send.return_value=FixtureHttpResponse({"model":self.model(operation),"status":"completed","output":7})
                        elif case=="model_json":self.send.return_value=FixtureHttpResponse(responses_envelope(self.model(operation),"PRIVATE_NOT_JSON"))
                        real_request=ai_gateway.AiGateway.request
                        def malformed(subject,chosen,body):return real_request(subject,chosen,{**body,"unexpected":True})
                        with mock.patch.object(ai_gateway.AiGateway,"request",malformed if case=="envelope" else real_request):
                            if case=="success":self.invoke(operation)
                            else:
                                with self.assertRaises(server.OpenAIAnalysisError):self.invoke(operation,missing_media=case=="missing_media")
                        self.assertIs(server.AI_LOG_TRACKING_CONTEXT.transport_operation,parent.operation)
                        self.assertIs(server.current_ai_transport_owner(),parent)
                        self.assertEqual(parent.operation,before)
                        attempts=self.attempts();self.assertEqual(len(attempts),1)
                        zero=case in {"missing_route","missing_key","malformed_key","envelope","missing_media"}
                        self.assertEqual(self.send.call_count,0 if zero else 1)
                        self.assertEqual(attempts[0]["attempt_number"],0 if zero else 1)
                        self.assertNotEqual(attempts[0]["retry_lineage_id"],parent.operation["retry_lineage_id"])
                        if zero:
                            self.assertEqual(attempts[0]["status"],"failed")
                            self.assertEqual(attempts[0]["failure_boundary"],"request_validation")
                            self.assertEqual(attempts[0]["model"],"")
                            for key in ("input_tokens","output_tokens","total_tokens"):self.assertNotIn(key,attempts[0])
                        self.assertNotIn("PRIVATE",json.dumps(attempts))
                    finally:
                        self.values.clear();self.values.update(saved_values)
                        parent.__exit__(None,None,None)

    def test_all_routes_restore_parent_when_local_or_telemetry_logger_raises(self):
        for operation in ai_gateway.ROUTE_KEYS:
            for failing in ("local","telemetry"):
                with self.subTest(operation=operation,failing=failing):
                    self.logs.reset_mock();self.send.reset_mock(side_effect=True)
                    self.send.return_value=self.success_response(operation)
                    parent=self.parent(operation);before=copy.deepcopy(parent.operation)
                    auth={"auth_mode":server.INTERNAL_AUTH_MODE,"user":{"subject":"synthetic-actor","account":"synthetic-workspace","internal_role":"admin"}}
                    try:
                        marker="SYNTHETIC_LOCAL_LOG_FAILURE" if failing=="local" else "SYNTHETIC_TELEMETRY_FAILURE"
                        def local_log(event,record,**kwargs):
                            if failing=="local" and event=="ai_call_attempt" and record.get("attempt_number")==1:
                                self.assertEqual(self.send.call_count,1)
                                raise RuntimeError(marker)
                            return True
                        def append_telemetry(session,record):
                            if failing=="telemetry" and record.get("attempt_number")==1:
                                self.assertEqual(self.send.call_count,1)
                                raise RuntimeError(marker)
                            return {}
                        with mock.patch.object(server.AI_LOG_TRACKING_CONTEXT,"auth_session",auth,create=True),mock.patch.object(server,"append_ai_attempt_telemetry",side_effect=append_telemetry) as telemetry:
                            self.logs.side_effect=local_log
                            with self.assertRaisesRegex(RuntimeError,"^"+marker+"$"):self.invoke(operation)
                            self.assertEqual(self.send.call_count,1)
                            self.assertEqual(len(self.attempts()),1)
                            self.assertEqual(self.attempts()[0]["attempt_number"],1)
                            self.assertEqual(telemetry.call_count,1 if failing=="telemetry" else 0)
                        self.assertIs(server.AI_LOG_TRACKING_CONTEXT.transport_operation,parent.operation)
                        self.assertIs(server.current_ai_transport_owner(),parent)
                        self.assertEqual(parent.operation,before)
                    finally:
                        self.logs.side_effect=None
                        parent.__exit__(None,None,None)

    def test_explicit_full_and_metadata_owners_keep_admission_window_and_close_blocked_result(self):
        for operation in ("draft","draft_high_quality","pricing_metadata"):
            with self.subTest(operation=operation):
                self.logs.reset_mock();self.send.reset_mock(side_effect=True)
                self.send.return_value=self.success_response(operation)
                parent=self.parent(operation);before=copy.deepcopy(parent.operation)
                try:
                    with server.AiTransportOperationScope(server.AI_LOG_TRACKING_CONTEXT,FEATURES[operation]) as child:
                        gateway=server.configured_ai_gateway(FEATURES[operation],operation_owner=child)
                        self.invoke(operation,gateway=gateway)
                        self.assertIs(server.AI_LOG_TRACKING_CONTEXT.transport_operation,child.operation)
                        self.assertIsNotNone(child.pending)
                        self.assertEqual(self.attempts(),[])
                        child.accept_result({"status":"blocked","error_reference":"ERR-1234ABCD"})
                    self.assertEqual(len(self.attempts()),1)
                    self.assertEqual(self.attempts()[0]["status"],"failed")
                    self.assertEqual(self.attempts()[0]["attempt_number"],1)
                    self.assertIs(server.AI_LOG_TRACKING_CONTEXT.transport_operation,parent.operation)
                    self.assertEqual(parent.operation,before)
                finally:parent.__exit__(None,None,None)

    def test_tracking_scope_restores_parent_owner_operation_and_metadata_identity(self):
        parent=self.parent("basis_answer");before=copy.deepcopy(parent.operation)
        metadata={"module":"sqag","purpose":"synthetic_parent"}
        try:
            with mock.patch.object(server.AI_LOG_TRACKING_CONTEXT,"metadata",metadata,create=True):
                with server.ai_log_tracking_scope({"purpose":"synthetic_child"}):
                    self.assertIsNone(server.current_ai_transport_owner())
                    with mock.patch.dict(self.values,{"OPENROUTER_API_KEY":""}):
                        with self.assertRaises(server.AIConfigurationError):self.invoke("basis_answer")
                self.assertIs(server.AI_LOG_TRACKING_CONTEXT.metadata,metadata)
                self.assertIs(server.AI_LOG_TRACKING_CONTEXT.transport_operation,parent.operation)
                self.assertIs(server.current_ai_transport_owner(),parent)
                self.assertEqual(parent.operation,before)
        finally:parent.__exit__(None,None,None)

    def test_all_six_routes_have_independent_parent_and_lineage_in_parallel_threads(self):
        operation_by_model={self.model(operation):operation for operation in ai_gateway.ROUTE_KEYS}
        def respond(request,**kwargs):
            model=json.loads(request.data)["model"]
            return self.success_response(operation_by_model[model])
        self.send.side_effect=respond
        def run(operation):
            parent=self.parent(operation);before=copy.deepcopy(parent.operation)
            try:
                self.invoke(operation)
                return server.AI_LOG_TRACKING_CONTEXT.transport_operation is parent.operation and parent.operation==before
            finally:parent.__exit__(None,None,None)
        with ThreadPoolExecutor(max_workers=6) as pool:self.assertEqual(list(pool.map(run,ai_gateway.ROUTE_KEYS)),[True]*6)
        attempts=self.attempts();self.assertEqual(len(attempts),6)
        self.assertEqual(len({row["retry_lineage_id"] for row in attempts}),6)
        self.assertEqual({row["attempt_number"] for row in attempts},{1})

    def test_metadata_batches_retain_one_configuration_and_distinct_terminal_lineages(self):
        operation="pricing_metadata";model=self.model(operation)
        parent=self.parent(operation);before=copy.deepcopy(parent.operation)
        items=[{**self.source_rows()[0],"id":"synthetic-"+suffix,"description":"Synthetic panel "+suffix.upper()} for suffix in ("a","b")]
        original=copy.deepcopy(items)
        sent=[]
        def respond(request,**kwargs):
            sent.append(json.loads(request.data)["model"])
            item=items[len(sent)-1]
            self.values["OPENROUTER_PRICING_METADATA_MODEL"]="PRIVATE_INVALID_MODEL"
            return json_response(model,{"items":[{"id":item["id"],"match_terms":[item["description"].lower()],"object_families":["synthetic_panel"]}]})
        try:
            self.send.side_effect=respond
            with mock.patch.object(server,"MAX_PRICING_METADATA_BATCH_ITEMS",1):
                enriched,errors=server.ai_pricing_reference_metadata_enrichment("synthetic",items)
            self.assertEqual(errors,[]);self.assertEqual([row["id"] for row in enriched],[row["id"] for row in items])
            self.assertEqual(server.pricing_reference_metadata_quality_errors(enriched),[])
            self.assertEqual(items,original)
            for source,row in zip(original,enriched):
                for key in ("description","unit_hint","internal_cost","markup_multiplier","sale_unit_price"):
                    self.assertEqual(row[key],source[key])
            self.assertEqual(sent,[model,model])
            actual=self.attempts();self.assertEqual(len(actual),2)
            self.assertEqual([row["attempt_number"] for row in actual],[1,1])
            self.assertEqual(len({row["retry_lineage_id"] for row in actual}),2)
            self.assertIs(server.AI_LOG_TRACKING_CONTEXT.transport_operation,parent.operation)
            self.assertEqual(parent.operation,before)
            self.logs.reset_mock();self.send.reset_mock(side_effect=True)
            enriched,errors=server.ai_pricing_reference_metadata_enrichment("synthetic",items)
            self.assertEqual(enriched,[]);self.assertTrue(errors)
            self.send.assert_not_called();self.assertEqual(len(self.attempts()),1)
            self.assertEqual(self.attempts()[0]["attempt_number"],0)
            self.assertIs(server.AI_LOG_TRACKING_CONTEXT.transport_operation,parent.operation)
            self.assertEqual(parent.operation,before)
        finally:
            self.values["OPENROUTER_PRICING_METADATA_MODEL"]=model
            parent.__exit__(None,None,None)
