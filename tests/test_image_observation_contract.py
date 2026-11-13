"""Offline image observation wire, admission and server handoff tests."""

import copy
import http.client
import io
import json
import socket
from itertools import product
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from tests import test_synthesis_contract_repair as synthesis
from tests.gateway_test_support import responses_envelope, synthetic_configuration
from tests.test_ai_response_contracts import canonical_draft
from tests.test_basis_quantity_retention import MEASURED_NOTE, draft
from tests.test_openai_draft_request_contract import synthetic_image
from webapp import ai_gateway, server
from webapp.ai_response_contracts import DRAFT_SYNTHESIS
from webapp.image_observation_contract import IMAGE_OBSERVATIONS, MAX_IMAGES, MAX_RESULT_CHARS


IMAGE_OPTION = "OPENROUTER_DRAFT_IMAGE_SCHEMA_ENABLED"
SYNTHESIS_OPTION = "OPENROUTER_DRAFT_SCHEMA_ENABLED"
CANARY = "synthetic-observation-must-stay-out-of-diagnostics"
USAGE = {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3, "cost": 0.125}


def batch_body(route, count=1):
    prompt = {"type": "input_text", "text": "Synthetic image inspection"}
    image = {"type": "input_image", "detail": "high", "image_url": synthetic_image()}
    return {"model": route.model, "reasoning": {"effort": route.effort},
            "input": [{"role": "user", "content": [prompt, *[dict(image) for _ in range(count)]]}]}


def wire_http_response(status, payload, *, truncated, chunked=True, interruption=None):
    class Response(http.client.HTTPResponse):
        def geturl(self):
            return ai_gateway.OPENROUTER_RESPONSES_URL

    head = f"HTTP/1.1 {status} Synthetic fixture\r\nContent-Type: application/json\r\n".encode()
    if chunked:
        head += b"Transfer-Encoding: chunked\r\n\r\n"
        chunk = f"{len(payload):x}\r\n".encode() + payload + b"\r\n"
        body = chunk + (b"10\r\nshort" if truncated else b"0\r\n\r\n")
    else:
        declared = len(payload) + int(truncated)
        head += f"Content-Length: {declared}\r\n\r\n".encode()
        body = payload
    class WireFile(io.BytesIO):
        def read(self, size=-1):
            # The next chunk's data read begins after the first full chunk.
            if interruption and self.tell() >= len(head + chunk) + len(b"10\r\n"):
                raise interruption(CANARY)
            return super().read(size)

    response = Response(SimpleNamespace(makefile=lambda *_args: WireFile(head + body)))
    response.begin()
    return response


def wire_opener(status, payload, *, truncated, chunked=True, interruption=None):
    response = wire_http_response(status, payload, truncated=truncated, chunked=chunked, interruption=interruption)
    opener = mock.Mock()
    if status == 200:
        opener.open.return_value = response
    else:
        opener.open.side_effect = urllib.error.HTTPError(
            response.geturl(), status, "Synthetic fixture", response.headers, response,
        )
    return opener, response


class ImageObservationGatewayTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Offline only")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("Offline only")))

    def gateway(self, *, images=None, final=None, batch_effort=None):
        values = synthetic_configuration(overrides={
            IMAGE_OPTION: images, SYNTHESIS_OPTION: final,
            "OPENROUTER_DRAFT_BATCH_REASONING_EFFORT": batch_effort,
        })
        configuration = ai_gateway.GatewayConfiguration.capture(values.get)
        transport = mock.Mock(spec=["send"])
        credential = mock.Mock(return_value="synthetic")
        telemetry = mock.Mock()
        spend = mock.Mock()
        gateway = ai_gateway.AiGateway(
            configuration, credential_reader=credential, transport=transport, telemetry=telemetry,
            quote_spend=spend, sleep=mock.Mock(side_effect=AssertionError("Unexpected retry")),
        )
        return gateway, transport, credential, telemetry, spend

    def test_image_and_synthesis_flags_are_independent_in_both_modes(self):
        text = ' \n{ "observations" : ["  synthetic note  "] }\n '
        for images in ("false", "true"):
            for final in ("false", "true"):
                for operation in ("draft", "draft_high_quality"):
                    for count in (1, MAX_IMAGES):
                        with self.subTest(images=images, final=final, operation=operation, count=count):
                            gateway, transport, _, _, _ = self.gateway(images=images, final=final, batch_effort="low")
                            route = gateway.configuration.route(operation)
                            body = batch_body(route, count)
                            body["reasoning"]["effort"] = "low"
                            before = copy.deepcopy(body)
                            transport.send.return_value = responses_envelope(route.model, text)
                            result = gateway.request(operation, body, draft_batch=True)
                            self.assertEqual(result["output"][0]["content"][0]["text"], text)
                            self.assertEqual(body, before)
                            wire = json.loads(transport.send.call_args.args[0])
                            self.assertEqual(wire["input"], body["input"])
                            self.assertEqual(wire["model"], route.model)
                            self.assertEqual(wire["reasoning"], {"effort": "low"})
                            self.assertEqual(wire["provider"], {
                                "only": [route.provider], "order": [route.provider], "allow_fallbacks": False,
                                "require_parameters": True, "data_collection": "deny", "zdr": True,
                            })
                            self.assertIs(wire["store"], False)
                            self.assertEqual(wire.get("text"), IMAGE_OBSERVATIONS.text_format() if images == "true" else None)

                            final_body = batch_body(route)
                            final_body["input"][0]["content"].pop()
                            options = {}
                            if final == "true":
                                final_body["text"] = DRAFT_SYNTHESIS.text_format()
                                options["response_contract"] = DRAFT_SYNTHESIS.name
                            gateway.request(operation, final_body, **options)
                            wire = json.loads(transport.send.call_args.args[0])
                            self.assertEqual(wire.get("text"), final_body.get("text"))
                            self.assertEqual(wire["reasoning"], {"effort": route.effort})

    def test_exact_schema_and_limits_match_server(self):
        self.assertEqual(MAX_IMAGES, server.MAX_DRAFT_BATCH_MEDIA)
        self.assertEqual(MAX_RESULT_CHARS, server.MAX_DRAFT_BATCH_RESULT_CHARS)
        self.assertEqual(IMAGE_OBSERVATIONS.text_format(), {"format": {
            "type": "json_schema", "name": "draft_image_observations_v1", "strict": True,
            "schema": {"type": "object", "properties": {
                "observations": {"type": "array", "items": {"type": "string"}},
            }, "required": ["observations"], "additionalProperties": False},
        }})
        gateway, transport, _, telemetry, _ = self.gateway(images="true")
        route = gateway.configuration.route("draft")
        prefix, suffix = '{"observations":["', '"]}'
        text = prefix + "x" * (MAX_RESULT_CHARS - len(prefix) - len(suffix)) + suffix
        transport.send.return_value = responses_envelope(route.model, text)
        gateway.request("draft", batch_body(route), draft_batch=True)
        self.assertEqual(telemetry.call_args.args[2], "success")

    def test_default_off_preserves_existing_wire_and_admission(self):
        for raw in (None, "", "false"):
            with self.subTest(raw=raw):
                gateway, transport, _, _, _ = self.gateway(images=raw)
                route = gateway.configuration.route("draft")
                transport.send.return_value = responses_envelope(route.model, "non-JSON still reaches the server")
                gateway.request("draft", batch_body(route), draft_batch=True)
                self.assertNotIn("text", json.loads(transport.send.call_args.args[0]))
        for raw in ("True", "1", "on", True, [], {}):
            with self.subTest(raw=raw), self.assertRaises(ai_gateway.GatewayError) as caught:
                self.gateway(images=raw)
            self.assertEqual(caught.exception.reason, "response_schema_configuration")

    def test_invalid_stage_shape_and_caller_schema_stop_before_credential_or_send(self):
        gateway, transport, credential, telemetry, spend = self.gateway(images="true", final="true")
        operation = "draft"
        route = gateway.configuration.route(operation)
        body = batch_body(route)
        invalid_role = copy.deepcopy(body)
        invalid_role["input"][0]["role"] = "assistant"
        extra_message = {**body, "input": body["input"] * 2}
        parts = body["input"][0]["content"]
        low_detail = {"role": "user", "content": [parts[0], {**parts[1], "detail": "low"}]}
        extra_text = {"role": "user", "content": [*parts, parts[0]]}
        remote_image = {"role": "user", "content": [parts[0], {**parts[1], "image_url": "https://example.test/image.png"}]}
        wrong_effort = {**body, "reasoning": {"effort": "none"}}
        wrong_model = {**body, "model": "synthetic/other"}
        with_schema = {**body, "text": IMAGE_OBSERVATIONS.text_format()}
        cases = [
            ("caller schema", operation, with_schema, {"draft_batch": True}),
            ("caller marker", operation, body, {"draft_batch": True, "response_contract": IMAGE_OBSERVATIONS.name}),
            ("final marker on batch", operation, body, {"draft_batch": True, "response_contract": DRAFT_SYNTHESIS.name}),
            ("batch marker on final", operation, with_schema, {"response_contract": IMAGE_OBSERVATIONS.name}),
            ("other route", "basis_line", batch_body(gateway.configuration.route("basis_line")), {"draft_batch": True}),
            ("truthy stage", operation, body, {"draft_batch": 1}),
            ("wrong model", operation, wrong_model, {"draft_batch": True}),
            ("wrong effort", operation, wrong_effort, {"draft_batch": True}),
            ("zero images", operation, batch_body(route, 0), {"draft_batch": True}),
            ("too many images", operation, batch_body(route, MAX_IMAGES + 1), {"draft_batch": True}),
            ("wrong role", operation, invalid_role, {"draft_batch": True}),
            ("extra message", operation, extra_message, {"draft_batch": True}),
            ("low detail", operation, {**body, "input": [low_detail]}, {"draft_batch": True}),
            ("extra text", operation, {**body, "input": [extra_text]}, {"draft_batch": True}),
            ("remote image", operation, {**body, "input": [remote_image]}, {"draft_batch": True}),
        ]
        budget = mock.Mock()
        for label, route_name, envelope, options in cases:
            with self.subTest(case=label), self.assertRaises(ai_gateway.GatewayError) as caught:
                gateway.request(route_name, envelope, send_budget=budget, **options)
            self.assertEqual(caught.exception.reason, "envelope_invalid")
            self.assertEqual(caught.exception.diagnostics["attempt_number"], 0)
        budget.claim.assert_not_called()
        credential.assert_not_called()
        transport.send.assert_not_called()
        spend.reserve.assert_not_called()
        telemetry.assert_not_called()

    def test_schema_bytes_count_toward_limit_before_credential_and_reservation(self):
        gateway, transport, credential, telemetry, spend = self.gateway(images="true")
        route = gateway.configuration.route("draft")
        body = batch_body(route)
        without_schema = {**body, "provider": gateway.configuration.provider_policy("draft"), "store": False}
        budget = mock.Mock()
        with (mock.patch.object(ai_gateway, "MAX_REQUEST_BYTES", len(json.dumps(without_schema).encode())),
              self.assertRaises(ai_gateway.GatewayError) as caught):
            gateway.request("draft", body, draft_batch=True, send_budget=budget)
        self.assertEqual(caught.exception.reason, "request_size")
        budget.claim.assert_not_called()
        credential.assert_not_called()
        spend.reserve.assert_not_called()
        transport.send.assert_not_called()
        telemetry.assert_not_called()

    def test_chunked_read_closure_and_spend_in_both_quality_modes(self):
        for operation in ("draft", "draft_high_quality"):
            for status in (200, 400, 503):
                for truncated in (False, True):
                    with self.subTest(operation=operation, status=status, truncated=truncated):
                        gateway, _, _, telemetry, spend = self.gateway(images="true")
                        route = gateway.configuration.route(operation)
                        if status == 200:
                            reply = responses_envelope(
                                route.model, json.dumps({"observations": [CANARY]}), usage=USAGE,
                            )
                        else:
                            reply = {"model": route.model, "usage": USAGE,
                                     "error": {"code": "unsupported_schema", "message": CANARY}}
                        opener, response = wire_opener(status, json.dumps(reply).encode(), truncated=truncated)
                        gateway._transport = ai_gateway.BoundedHttpsTransport(opener=opener)
                        budget = mock.Mock()
                        args = {"send_budget": budget, "draft_batch": True}
                        if truncated or status != 200:
                            with self.assertRaises(ai_gateway.GatewayError) as caught:
                                gateway.request(operation, batch_body(route), **args)
                            error = caught.exception
                            reason = "response_partial" if truncated else "response_schema_rejected"
                            self.assertEqual(error.reason, reason)
                            self.assertFalse(error.retryable)
                            self.assertEqual(error.diagnostics["response_contract_reason"], reason)
                            self.assertEqual(error.diagnostics["response_contract_name"], IMAGE_OBSERVATIONS.name)
                            self.assertEqual(error.diagnostics["batch_failure_reason"], "batch_provider_failure")
                            self.assertEqual(error.diagnostics.get("http_status"), status if status != 200 else None)
                            if truncated:
                                self.assertNotIn("provider_error_code", error.diagnostics)
                            self.assertNotIn(CANARY, str(error))
                        else:
                            result = gateway.request(operation, batch_body(route), **args)
                            self.assertEqual(result["output"], reply["output"])
                        self.assertTrue(response.closed)
                        opener.open.assert_called_once()
                        spend.reserve.assert_called_once_with()
                        cost = None if truncated else 0.125
                        spend.settle.assert_called_once_with(spend.reserve.return_value, cost)
                        budget.claim.assert_called_once_with(operation, route.model)
                        tokens = {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
                        budget.finish.assert_called_once_with(tokens if status == 200 and not truncated else None, cost_usd=cost)
                        telemetry.assert_called_once()
                        self.assertEqual(telemetry.call_args.args[2], "success" if status == 200 and not truncated else "failed")
                        self.assertFalse(telemetry.call_args.args[4]["retry_pending"])
                        self.assertNotIn(CANARY, json.dumps(telemetry.call_args.args))

    def test_interrupted_chunk_discards_data_before_status_and_schema_classification(self):
        for operation in ("draft", "draft_high_quality"):
            for status in (200, 400, 503):
                for interruption in (ConnectionResetError, TimeoutError):
                    with self.subTest(operation=operation, status=status, interruption=interruption):
                        gateway, _, _, telemetry, spend = self.gateway(images="true")
                        route = gateway.configuration.route(operation)
                        first = {"model": route.model, "usage": USAGE,
                                 "error": {"code": "unsupported_schema", "message": CANARY}}
                        opener, response = wire_opener(
                            status, json.dumps(first).encode(), truncated=True, interruption=interruption,
                        )
                        gateway._transport = ai_gateway.BoundedHttpsTransport(opener=opener)
                        budget = mock.Mock()
                        with self.assertRaises(ai_gateway.GatewayError) as caught:
                            gateway.request(operation, batch_body(route), draft_batch=True, send_budget=budget)
                        error = caught.exception
                        self.assertEqual(error.reason, "response_partial")
                        self.assertEqual(error.diagnostics["failure_boundary"], "provider_response_shape")
                        self.assertEqual(error.diagnostics["response_contract_reason"], "response_partial")
                        self.assertEqual(error.diagnostics["response_contract_name"], IMAGE_OBSERVATIONS.name)
                        self.assertEqual(error.diagnostics["batch_failure_reason"], "batch_provider_failure")
                        self.assertEqual(error.diagnostics.get("http_status"), status if status != 200 else None)
                        self.assertNotIn("provider_error_code", error.diagnostics)
                        self.assertFalse(error.retryable)
                        self.assertTrue(response.closed)
                        opener.open.assert_called_once()
                        spend.reserve.assert_called_once_with()
                        spend.settle.assert_called_once_with(spend.reserve.return_value, None)
                        budget.claim.assert_called_once_with(operation, route.model)
                        budget.finish.assert_called_once_with(None, cost_usd=None)
                        telemetry.assert_called_once()
                        self.assertEqual(telemetry.call_args.args[2], "failed")
                        self.assertFalse(telemetry.call_args.args[4]["retry_pending"])
                        self.assertNotIn(CANARY, json.dumps(telemetry.call_args.args))

    def test_complete_image_http_errors_require_explicit_schema_evidence(self):
        cases = (
            (400, {"code": "invalid_image", "param": "input[0].content[1].image_url"}, "http_error"),
            (400, {"code": "invalid_request_error", "message": "unsupported_schema " + CANARY}, "http_error"),
            (404, {}, "http_error"),
            (422, {"code": "unsupported_schema"}, "response_schema_rejected"),
            (400, {"type": "unsupported_schema"}, "response_schema_rejected"),
            (400, {"code": "invalid_value", "param": "text.format.schema"}, "response_schema_rejected"),
        )
        for operation in ("draft", "draft_high_quality"):
            for status, fields, reason in cases:
                with self.subTest(operation=operation, status=status, fields=fields):
                    gateway, _, _, telemetry, spend = self.gateway(images="true", final="true")
                    route = gateway.configuration.route(operation)
                    payload = {"model": route.model, "usage": USAGE, "error": fields}
                    opener, response = wire_opener(status, json.dumps(payload).encode(), truncated=False)
                    gateway._transport = ai_gateway.BoundedHttpsTransport(opener=opener)
                    with self.assertRaises(ai_gateway.GatewayError) as caught:
                        gateway.request(operation, batch_body(route), draft_batch=True)
                    error = caught.exception
                    self.assertEqual(error.reason, reason)
                    self.assertEqual(error.diagnostics["response_contract_reason"], reason)
                    self.assertEqual(error.diagnostics["response_contract_name"], IMAGE_OBSERVATIONS.name)
                    self.assertEqual(error.diagnostics["batch_failure_reason"], "batch_provider_failure")
                    self.assertEqual(error.diagnostics["http_status"], status)
                    boundary = "provider_http" if reason == "http_error" else "provider_response_schema"
                    self.assertEqual(error.diagnostics["failure_boundary"], boundary)
                    if fields.get("code") == "invalid_image":
                        self.assertEqual(error.diagnostics["provider_error_code"], "invalid_image")
                    self.assertTrue(response.closed)
                    opener.open.assert_called_once()
                    spend.settle.assert_called_once_with(spend.reserve.return_value, 0.125)
                    telemetry.assert_called_once()
                    self.assertNotIn(CANARY, json.dumps(telemetry.call_args.args))

    def test_partial_bodies_never_retry_even_on_retry_eligible_routes(self):
        for operation, status, interruption in product(
            ("basis_line", "basis_answer"), (200, 503),
            ("fixed_length", None, ConnectionResetError, TimeoutError),
        ):
            with self.subTest(operation=operation, status=status, interruption=interruption):
                gateway, _, _, telemetry, spend = self.gateway()
                route = gateway.configuration.route(operation)
                reply = {"model": route.model, "usage": USAGE, "error": {"message": CANARY}}
                chunked = interruption != "fixed_length"
                opener, response = wire_opener(
                    status, json.dumps(reply).encode(), truncated=True, chunked=chunked,
                    interruption=interruption if chunked else None,
                )
                gateway._transport = ai_gateway.BoundedHttpsTransport(opener=opener)
                with self.assertRaises(ai_gateway.GatewayError) as caught:
                    gateway.request(operation, batch_body(route))
                error = caught.exception
                self.assertEqual(error.reason, "response_partial")
                self.assertEqual(error.diagnostics["failure_boundary"], "provider_response_shape")
                self.assertEqual(error.diagnostics.get("http_status"), 503 if status == 503 else None)
                self.assertFalse(error.retryable)
                self.assertNotIn("response_contract_name", error.diagnostics)
                self.assertNotIn(CANARY, json.dumps(error.diagnostics))
                self.assertTrue(response.closed)
                opener.open.assert_called_once()
                spend.settle.assert_called_once_with(spend.reserve.return_value, None)
                telemetry.assert_called_once()
                self.assertFalse(telemetry.call_args.args[4]["retry_pending"])

    def test_basis_retries_pre_response_failures_and_complete_transient_http_errors(self):
        for operation, failure in product(
            ("basis_line", "basis_answer"), (ConnectionResetError, TimeoutError, urllib.error.HTTPError),
        ):
            with self.subTest(operation=operation, failure=failure):
                gateway, _, _, telemetry, spend = self.gateway()
                route = gateway.configuration.route(operation)
                if failure is urllib.error.HTTPError:
                    opener, failed_response = wire_opener(503, b'{"error":{"code":"server_error"}}', truncated=False)
                    first_error = opener.open.side_effect
                else:
                    opener, failed_response = mock.Mock(), None
                    first_error = failure(CANARY)
                reply = responses_envelope(route.model, '{"message":"complete"}', usage=USAGE)
                success = wire_http_response(200, json.dumps(reply).encode(), truncated=False)
                opener.open.side_effect = [first_error, success]
                sleep = mock.Mock()
                gateway._sleep = sleep
                gateway._transport = ai_gateway.BoundedHttpsTransport(opener=opener)
                self.assertEqual(gateway.request(operation, batch_body(route))["output"], reply["output"])
                if failed_response is not None:
                    self.assertTrue(failed_response.closed)
                self.assertEqual(opener.open.call_count, 2)
                self.assertEqual(spend.reserve.call_count, 2)
                self.assertEqual([call.args[1] for call in spend.settle.call_args_list], [None, 0.125])
                sleep.assert_called_once_with(2.0)
                self.assertEqual([call.args[2] for call in telemetry.call_args_list], ["failed", "success"])
                first_details = telemetry.call_args_list[0].args[4]
                self.assertTrue(first_details["retry_pending"])
                self.assertNotIn(CANARY, json.dumps(first_details))
                if failure is not urllib.error.HTTPError:
                    self.assertEqual(first_details["failure_boundary"], "provider_transport")
                    self.assertEqual(first_details["transport_timeout"], failure is TimeoutError)

    def test_response_failure_matrix_settles_once_with_safe_diagnostics(self):
        valid = json.dumps({"observations": [CANARY]})
        cases = (
            ("malformed", '{"observations": [', {}, "schema_json", "batch_provider_failure"),
            ("fenced", "```json\n" + valid + "\n```", {}, "schema_json", "batch_provider_failure"),
            ("prose", valid + " " + CANARY, {}, "schema_json", "batch_provider_failure"),
            ("duplicate", '{"observations": [], ' + valid[1:], {}, "schema_json", "batch_provider_failure"),
            ("missing", '{}', {}, "schema_shape", "batch_output_coverage"),
            ("extra", json.dumps({"observations": ["note"], CANARY: 1}), {}, "schema_shape", "batch_output_coverage"),
            ("null root", "null", {}, "schema_shape", "batch_output_coverage"),
            ("null array", '{"observations": null}', {}, "schema_shape", "batch_output_coverage"),
            ("blank", '{"observations": [" \\t"]}', {}, "schema_shape", "batch_output_coverage"),
            ("nonstring", '{"observations": [3]}', {}, "schema_shape", "batch_output_coverage"),
            ("oversize", valid + " " * MAX_RESULT_CHARS, {}, "response_too_large", "batch_output_limit"),
            ("refusal", valid, {"output": [{"type": "refusal", "refusal": CANARY}]}, "response_refused", "batch_provider_failure"),
            ("incomplete", valid, {"status": "incomplete"}, "response_incomplete", "batch_provider_failure"),
            ("two texts", valid, {"output_text": CANARY}, "response_mixed", "batch_provider_failure"),
            ("unsupported", valid, {"status": "failed", "error": {
                "code": "unsupported_schema", "param": "text.format.schema", "message": CANARY,
            }}, "response_schema_rejected", "batch_provider_failure"),
        )
        for index, (label, text, fields, contract_reason, batch_reason) in enumerate(cases):
            with self.subTest(case=label):
                gateway, transport, _, telemetry, spend = self.gateway(images="true")
                operation = "draft_high_quality" if index % 2 else "draft"
                route = gateway.configuration.route(operation)
                usage = USAGE if index % 2 else None
                transport.send.return_value = responses_envelope(route.model, text, usage=usage, **fields)
                budget = mock.Mock()
                with self.assertRaises(ai_gateway.GatewayError) as caught:
                    gateway.request(operation, batch_body(route), draft_batch=True, send_budget=budget)
                diagnostic = caught.exception.diagnostics
                self.assertEqual(diagnostic["response_contract_name"], IMAGE_OBSERVATIONS.name)
                self.assertEqual(diagnostic["response_contract_reason"], contract_reason)
                self.assertEqual(diagnostic["batch_failure_reason"], batch_reason)
                self.assertEqual(diagnostic["attempt_number"], 1)
                transport.send.assert_called_once()
                spend.reserve.assert_called_once_with()
                cost = 0.125 if usage else None
                spend.settle.assert_called_once_with(spend.reserve.return_value, cost)
                budget.claim.assert_called_once_with(operation, route.model)
                budget.finish.assert_called_once_with(
                    {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3} if usage else None, cost_usd=cost)
                telemetry.assert_called_once()
                self.assertEqual(telemetry.call_args.args[2], "failed")
                self.assertFalse(telemetry.call_args.args[4]["retry_pending"])
                self.assertNotIn(CANARY, json.dumps(diagnostic))
                self.assertNotIn(CANARY, json.dumps(telemetry.call_args.args))


class ImageObservationDraftTest(unittest.TestCase):
    def setUp(self):
        self.fixture = synthesis.SynthesisContractRepairTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.values[IMAGE_OPTION] = "true"

    def test_real_draft_job_keeps_partial_response_reference_in_both_modes(self):
        media = {"type": "input_image", "image_url": self.fixture.image, "detail": "high"}
        prepared = server.PreparedDraftMedia([], [[("Reference 1", media)]])
        for mode, status, interruption in product(
            ("standard", "high_quality"), (200, 400, 503),
            (None, ConnectionResetError, TimeoutError),
        ):
            with self.subTest(mode=mode, status=status, interruption=interruption):
                self.fixture.logs.clear()
                opener, response = wire_opener(status, CANARY.encode(), truncated=True, interruption=interruption)
                transport = ai_gateway.BoundedHttpsTransport(opener=opener)
                operation = "draft_high_quality" if mode == "high_quality" else "draft"
                with mock.patch.object(ai_gateway, "BoundedHttpsTransport", return_value=transport):
                    gateway = server.configured_ai_gateway()
                route = gateway.configuration.route(operation)
                configuration = server.OpenAIDraftConfiguration(mode, route.model, route.effort, gateway)
                payload = {"analysis_mode": mode, "images": [{"name": "synthetic.png", "data_url": self.fixture.image}]}
                with (mock.patch.object(server, "prepare_draft_media", return_value=prepared),
                      mock.patch.object(server, "capture_openai_draft_configuration", return_value=configuration),
                      mock.patch.object(server, "pricing_reference_snapshot_for_payload", return_value=object()),
                      mock.patch.object(server, "pricing_reference_authority_error", return_value=""),
                      mock.patch.object(server, "protected_ai_draft_mode_enabled", return_value=False),
                      mock.patch.object(server, "set_job_state") as state):
                    server.finish_draft_job("job-synthetic-partial", payload)
                state.assert_called_once()
                result = state.call_args.kwargs["result"]
                self.assertEqual(state.call_args.kwargs["status"], "blocked")
                self.assertNotIn("line_items", result)
                failure = [row for event, row in self.fixture.logs if event == "openai_draft_failed"][-1]
                self.assertEqual(result["error_reference"], failure["error_reference"])
                self.assertEqual(failure["failure_boundary"], "draft_batch")
                self.assertEqual(failure["batch_failure_reason"], "batch_provider_failure")
                self.assertEqual(failure["response_contract_reason"], "response_partial")
                self.assertEqual(failure["response_contract_name"], IMAGE_OBSERVATIONS.name)
                self.assertNotIn(CANARY, json.dumps([result, self.fixture.logs]))
                wire = json.loads(opener.open.call_args.args[0].data)
                self.assertEqual(wire["model"], route.model)
                self.assertEqual(wire["reasoning"], {"effort": route.effort})
                self.assertEqual(wire["text"], IMAGE_OBSERVATIONS.text_format())
                opener.open.assert_called_once()
                self.assertTrue(response.closed)
                self.assertEqual(self.fixture.usage()["total"]["sends"], 1)
                self.assertEqual(self.fixture.usage()["total"]["unknown_cost_attempts"], 1)
                self.assertEqual(self.fixture.usage()["total"]["unknown_usage_attempts"], 1)
                terminals = [row for event, row in self.fixture.logs if event == "ai_call_attempt"
                             and row.get("attempt_number", 0) > 0]
                self.assertEqual(len(terminals), 1)
                self.assertEqual(terminals[0]["status"], "failed")

    def test_batch_coverage_failure_stops_before_synthesis_and_keeps_job_reference(self):
        class InvalidBatch:
            def __init__(self, observations):
                self.observations = observations
                self.calls = []

            def send(self, encoded, **_kwargs):
                body = json.loads(encoded)
                self.calls.append(body)
                return responses_envelope(body["model"], self.observations, usage=USAGE)

        cases = (
            ('{"observations":["' + CANARY + '"]}', "batch_output_coverage", None),
            ('{"observations": [], "' + CANARY + '": true}', "batch_output_coverage", "schema_shape"),
        )
        for text, batch_reason, contract_reason in cases:
            with self.subTest(contract_reason=contract_reason):
                self.fixture.logs.clear()
                transport = InvalidBatch(text)
                result = self.fixture.analyse(transport, batches=1, terminal=True)
                self.assertEqual(result["status"], "blocked")
                self.assertNotIn("line_items", result)
                self.assertEqual(len(transport.calls), 1)
                self.fixture.assert_sends(transport, 1, 0)
                failure = [row for event, row in self.fixture.logs if event == "openai_draft_failed"][-1]
                self.assertEqual(failure["error_reference"], result["error_reference"])
                self.assertEqual(failure["batch_failure_reason"], batch_reason)
                self.assertEqual(failure.get("response_contract_reason"), contract_reason)
                if contract_reason:
                    self.assertEqual(failure["response_contract_name"], IMAGE_OBSERVATIONS.name)
                self.assertNotIn(CANARY, json.dumps(self.fixture.logs))

    def test_enabled_batches_preserve_the_existing_send_cap(self):
        transport = synthesis.SynthesisTransport([draft()])
        self.fixture.analyse(transport, batches=server.MAX_DRAFT_BATCH_CALLS - 1)
        self.fixture.assert_sends(transport, server.MAX_DRAFT_BATCH_CALLS - 1, 1)
        self.assertTrue(all(body["text"] == IMAGE_OBSERVATIONS.text_format() for body in transport.calls[:-1]))

        transport = synthesis.SynthesisTransport([])
        with self.assertRaises(server.DraftBatchError) as caught:
            self.fixture.analyse(transport, batches=server.MAX_DRAFT_BATCH_CALLS)
        self.assertEqual(caught.exception.diagnostics["batch_failure_reason"], "draft_call_limit")
        self.assertEqual(transport.calls, [])

    def test_ordered_observations_feed_synthesis_and_preserve_one_repair(self):
        class OrderedBatches(synthesis.SynthesisTransport):
            def send(self, encoded, **kwargs):
                body = json.loads(encoded)
                parts = body["input"][0]["content"]
                prompt = parts[0]["text"]
                if prompt.startswith("Inspect each attached visual"):
                    with self.lock:
                        self.calls.append(body)
                    labels = prompt.split("Ordered visual sources: ")[1].split("; ")
                    notes = {"observations": [f"{label} {MEASURED_NOTE}" for label in labels]}
                    return responses_envelope(body["model"], json.dumps(notes), usage=USAGE)
                return super().send(encoded, **kwargs)

        for mode in ("false", "true"):
            with self.subTest(synthesis=mode):
                self.fixture.values[SYNTHESIS_OPTION] = mode
                self.fixture.logs.clear()
                outputs = [draft()]
                if mode == "true":
                    valid = canonical_draft()
                    missing = copy.deepcopy(valid)
                    missing["quote_basis_sections"][0]["lines"][0].pop("confidence_pct")
                    outputs = [missing, valid]
                transport = OrderedBatches(outputs)
                result = self.fixture.analyse(transport, batches=3)
                final_count = 2 if mode == "true" else 1
                self.fixture.assert_sends(transport, 3, final_count)
                self.assertEqual([item["quantity"] for item in result["line_items"]], [36, 6, 4])
                self.assertEqual(self.fixture.usage()["synthesis_repair_count"], final_count - 1)
                for batch in transport.calls[:3]:
                    self.assertEqual(batch["text"], IMAGE_OBSERVATIONS.text_format())
                for final in transport.calls[3:]:
                    self.assertEqual(final.get("text"), DRAFT_SYNTHESIS.text_format() if mode == "true" else None)
                    prompt = final["input"][0]["content"][0]["text"]
                    observations, _ = json.JSONDecoder().raw_decode(prompt[prompt.index('[{"source":'):])
                    self.assertEqual([row["source"] for row in observations],
                                     [f"Reference 1, PDF page {page}" for page in range(1, 19)])
                    self.assertTrue(all(row["observation"] == f'{row["source"]} {MEASURED_NOTE}'
                                        for row in observations))


if __name__ == "__main__":
    unittest.main()
