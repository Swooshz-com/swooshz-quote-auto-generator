"""Offline image observation wire, admission and server handoff tests."""

import copy
import json
import socket
import unittest
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
