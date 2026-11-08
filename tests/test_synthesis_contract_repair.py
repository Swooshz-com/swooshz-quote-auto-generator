"""Offline synthesis missing-field repair, strict admission and safe diagnostics."""
import copy
import json
import socket
import threading
import unittest
from unittest import mock

from tests.gateway_test_support import responses_envelope, synthetic_configuration
from tests.test_basis_quantity_retention import draft
from tests.test_openai_draft_request_contract import synthetic_image
from webapp import ai_gateway, server


CANARY = "synthetic-content-must-not-enter-diagnostics"
PATH = "quote_basis_sections[0].lines[0].confidence"


def missing_confidence():
    value = draft()
    value["quote_basis_sections"][0]["lines"][0].pop("confidence_pct")
    value["quote_basis_sections"][0]["lines"][0]["text"] = CANARY
    return value


class SynthesisTransport:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.lock = threading.Lock()

    def send(self, encoded, **kwargs):
        body = json.loads(encoded)
        with self.lock:
            self.calls.append(body)
        parts = body["input"][0]["content"]
        if parts[0]["text"].startswith("Inspect each attached visual"):
            text = json.dumps({"observations": [CANARY] * (len(parts) - 1)})
            envelope = responses_envelope(body["model"], text)
        else:
            value = next(self.outputs)
            if value == "refusal":
                envelope = responses_envelope(body["model"], "")
                envelope["output"][0]["content"] = [{"type": "refusal", "refusal": CANARY}]
            else:
                envelope = responses_envelope(body["model"], value if isinstance(value, str) else json.dumps(value))
        envelope["usage"] = {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3, "cost": 0.125}
        return envelope


class SynthesisContractRepairTest(unittest.TestCase):
    def setUp(self):
        self.values = synthetic_configuration(credential="synthetic")
        self.enterContext(mock.patch.dict(server.os.environ, {"APP_MODE": "local", "SQAG_DISABLE_DOTENV": "1"}, clear=True))
        self.enterContext(mock.patch.object(server, "read_dotenv_value", side_effect=self.values.get))
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(server.urllib.request, "urlopen", side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(server, "build_quote_draft_prompt", return_value="Synthetic original contract"))
        self.enterContext(mock.patch.object(server, "catalog_visual_image_entries_for_payload", return_value=[]))
        # Exercise real admission and normalisation without saved profile/catalogue I/O.
        normalize = server.normalize_ai_draft
        self.enterContext(mock.patch.object(server, "normalize_ai_draft", side_effect=lambda parsed, *args, **kwargs:
                                           normalize(parsed, require_confidence=True)))
        self.logs = []
        self.log_lock = threading.Lock()
        self.enterContext(mock.patch.object(server, "write_local_log", side_effect=self.log))
        self.enterContext(mock.patch.object(server, "append_ai_attempt_telemetry", return_value={}))
        self.image = synthetic_image()

    def log(self, event, record, **kwargs):
        with self.log_lock:
            self.logs.append((event, copy.deepcopy(record)))
        return True

    def analyse(self, transport, *, batches=12, terminal=False, single=False):
        media = {"type": "input_image", "image_url": self.image, "detail": "high"}
        prepared = server.PreparedDraftMedia([media] if single else [], [] if single else [
            [(f"Reference 1, PDF page {index * 6 + page}", media) for page in range(1, 7)]
            for index in range(batches)
        ])
        with mock.patch.object(ai_gateway, "BoundedHttpsTransport", return_value=transport):
            gateway = server.configured_ai_gateway()
        route = gateway.configuration.route("draft")
        config = server.OpenAIDraftConfiguration("standard", route.model, route.effort, gateway)
        payload = {"images": [{"name": "synthetic.png", "data_url": self.image}]}
        with mock.patch.object(server, "prepare_draft_media", return_value=prepared):
            if not terminal:
                return server.request_openai_quote_basis(payload, "synthetic", draft_configuration=config)
            with mock.patch.object(server, "capture_openai_draft_configuration", return_value=config), \
                 mock.patch.object(server, "pricing_reference_snapshot_for_payload", return_value=object()), \
                 mock.patch.object(server, "pricing_reference_authority_error", return_value=""), \
                 mock.patch.object(server, "protected_ai_draft_mode_enabled", return_value=True), \
                 mock.patch.object(server, "new_error_reference", return_value="ERR-01234567"):
                return server.draft_quote_basis(payload)

    def usage(self):
        return [record for event, record in self.logs if event == "draft_batch_usage"][-1]

    def assert_sends(self, transport, batches, syntheses):
        batch_calls = [body for body in transport.calls
                       if body["input"][0]["content"][0]["text"].startswith("Inspect each attached visual")]
        self.assertEqual(len(batch_calls), batches)
        self.assertEqual(len(transport.calls), batches + syntheses)
        self.assertEqual(len({body["input"][0]["content"][0]["text"] for body in batch_calls}), batches)
        usage = self.usage()["total"]
        self.assertEqual(usage["sends"], batches + syntheses)
        self.assertEqual(usage["input_tokens"], 2 * (batches + syntheses))
        self.assertEqual(usage["output_tokens"], batches + syntheses)
        self.assertEqual(usage["reported_cost_usd"], 0.125 * (batches + syntheses))
        terminals = [record for event, record in self.logs if event == "ai_call_attempt"
                     and record.get("attempt_number", 0) > 0]
        self.assertEqual(len(terminals), batches + syntheses)
        self.assertNotIn(CANARY, json.dumps(self.logs))

    def test_nested_missing_confidence_path_has_no_model_content(self):
        value = draft()
        value["quote_basis_sections"] = [copy.deepcopy(value["quote_basis_sections"][0]) for _ in range(4)]
        value["line_items"] = []
        for index, section in enumerate(value["quote_basis_sections"]):
            section["id"] = f"section-{index}"
            section["title"] = CANARY
            section["lines"] = [{"id": f"line_{index}_{j}", "text": CANARY, "confidence": 90} for j in range(8)]
        value["quote_basis_sections"][3]["lines"][7].pop("confidence")
        with self.assertRaises(server.AIModelOutputError) as caught:
            server.admit_provider_quote_draft(value, require_confidence=True)
        details = server.ai_failure_metadata(caught.exception, error_reference="ERR-01234567")
        self.assertEqual(details["response_contract_missing_paths"], ["quote_basis_sections[3].lines[7].confidence"])
        self.assertEqual(details["response_contract_reason"], "confidence_missing")
        self.assertNotIn(CANARY, json.dumps(details))
        self.assertNotIn(CANARY, str(caught.exception))

    def test_diagnostic_path_projection_rejects_supplied_keys_and_unbounded_paths(self):
        for paths in ([CANARY], ["quote_basis_sections[0].lines[0]." + CANARY], [PATH] * 33,
                      ["quote_basis_sections[12345678].lines[0].confidence"], PATH, [PATH, CANARY]):
            with self.subTest(paths=paths):
                self.assertEqual(server.safe_ai_output_diagnostics({"response_contract_missing_paths": paths}), {})
        self.assertEqual(server.safe_ai_output_diagnostics({"response_contract_missing_paths": [PATH]}),
                         {"response_contract_missing_paths": [PATH]})

    def test_one_repair_succeeds_without_replaying_batches_or_changing_input(self):
        transport = SynthesisTransport([missing_confidence(), draft()])
        result = self.analyse(transport)
        self.assertTrue(result["quote_basis_sections"])
        self.assert_sends(transport, 12, 2)
        self.assertEqual(self.usage()["synthesis_repair_count"], 1)
        self.assertEqual(self.usage()["synthesis_repair_outcome"], "success")
        self.assertEqual(self.usage()["response_contract_missing_paths"], [PATH])
        original, repaired = copy.deepcopy(transport.calls[-2:])
        original_text = original["input"][0]["content"][0]["text"]
        repair_text = repaired["input"][0]["content"][0]["text"]
        self.assertTrue(repair_text.startswith(original_text))
        note = repair_text[len(original_text):]
        self.assertIn(PATH, note)
        self.assertIn("confidence must be a JSON integer from 0 to 100", note)
        self.assertNotIn(CANARY, note)
        self.assertLess(len(note), 1200)
        self.assertEqual([part["type"] for part in repaired["input"][0]["content"]], ["input_text"])
        repaired["input"][0]["content"][0]["text"] = original_text
        self.assertEqual(repaired, original)

    def test_second_contract_failure_uses_normal_error_reference_and_settles_both_sends(self):
        transport = SynthesisTransport([missing_confidence(), missing_confidence()])
        result = self.analyse(transport, terminal=True)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error_reference"], "ERR-01234567")
        self.assertNotIn("line_items", result)
        self.assert_sends(transport, 12, 2)
        self.assertEqual(self.usage()["synthesis_repair_count"], 1)
        self.assertEqual(self.usage()["synthesis_repair_outcome"], "failed")
        failure = [row for event, row in self.logs if event == "openai_draft_failed"][-1]
        self.assertEqual(failure["failure_kind"], "model_output_invalid")
        self.assertEqual(failure["failure_boundary"], "model_output_contract")
        self.assertEqual(failure["response_contract_missing_paths"], [PATH])
        self.assertEqual(failure["error_reference"], result["error_reference"])

    def test_budget_exhausted_prevents_repair_and_keeps_original_contract_failure(self):
        transport = SynthesisTransport([missing_confidence()])
        # Seventeen batches plus synthesis consume the unchanged real maximum.
        with self.assertRaises(server.AIModelOutputError) as caught:
            self.analyse(transport, batches=17)
        self.assertEqual(caught.exception.diagnostics["response_contract_reason"], "confidence_missing")
        self.assert_sends(transport, 17, 1)
        self.assertEqual(self.usage()["synthesis_repair_count"], 0)
        self.assertEqual(self.usage()["synthesis_repair_outcome"], "budget_exhausted")

    def test_remaining_budget_permits_only_one_repair(self):
        budget = server.DraftSendBudget("draft", "synthetic/draft", 0)
        for stage in ({"synthesis": True}, {"synthesis_repair": True}):
            budget.next_stage(**stage)
            budget.claim("draft", "synthetic/draft")
            budget.finish(None)
        with self.assertRaises(server.DraftBatchError):
            budget.next_stage(synthesis_repair=True)
        self.assertEqual(budget.summary()["total"]["sends"], 2)

    def test_disabled_repair_keeps_missing_field_failure(self):
        transport = SynthesisTransport([missing_confidence()])
        with mock.patch.object(server, "ENABLE_SYNTHESIS_CONTRACT_REPAIR", False):
            with self.assertRaises(server.AIModelOutputError):
                self.analyse(transport, batches=3)
        self.assert_sends(transport, 3, 1)
        self.assertEqual(self.usage()["synthesis_repair_count"], 0)

    def test_single_send_path_never_repairs_missing_fields(self):
        transport = SynthesisTransport([missing_confidence()])
        with self.assertRaises(server.AIModelOutputError):
            self.analyse(transport, single=True)
        self.assert_sends(transport, 0, 1)

    def test_non_missing_failures_get_no_repair(self):
        invalid = draft()
        invalid["quote_basis_sections"][0]["lines"][0]["confidence_pct"] = None
        conflict = draft()
        conflict["quote_basis_sections"][0]["lines"][0]["confidence"] = 1
        blank = draft()
        blank["quote_basis_sections"][0]["lines"][0]["text"] = ""
        for value in ("invalid JSON " + CANARY, "refusal", invalid, conflict, blank):
            with self.subTest(value=value):
                self.logs.clear()
                transport = SynthesisTransport([value])
                with self.assertRaises(server.OpenAIAnalysisError):
                    self.analyse(transport, batches=3)
                self.assert_sends(transport, 3, 1)
                self.assertEqual(self.usage()["synthesis_repair_count"], 0)

    def test_equivalent_absent_fields_repair_but_supplied_invalid_fields_do_not(self):
        cases = (("basis_row_text", "quote_basis_sections[0].lines[0].text", lambda value:
                  value["quote_basis_sections"][0]["lines"][0].pop("text")),
                 ("basis_rows_shape", "quote_basis_sections[0].lines", lambda value:
                  value["quote_basis_sections"][0].pop("lines")),
                 ("item_row_shape", "line_items[0].description", lambda value:
                  value["line_items"][0].pop("description")),
                 ("supplementary_row_shape", "analysis_findings[0].text", lambda value:
                  value.update(analysis_findings=[{"id": "synthetic-finding"}])),
                 ("supplementary_row_shape", "blocking_clarification_questions[0].question", lambda value:
                  value.update(blocking_clarification_questions=[{"id": "synthetic-question"}])))
        for reason, path, change in cases:
            with self.subTest(reason=reason, path=path):
                self.logs.clear()
                value = draft()
                change(value)
                transport = SynthesisTransport([value, draft()])
                self.analyse(transport, batches=3)
                self.assert_sends(transport, 3, 2)
                self.assertEqual(self.usage()["response_contract_reason"], reason)
                self.assertEqual(self.usage()["response_contract_missing_paths"], [path])

    def test_synthesis_prompt_ends_in_required_field_restatement_before_observations(self):
        transport = SynthesisTransport([draft()])
        self.analyse(transport, batches=3)
        text = transport.calls[-1]["input"][0]["content"][0]["text"]
        instructions = text[:text.index('[{"source":')]
        tail = instructions[instructions.index("Required fields:"):]
        self.assertIn("Leave quantity and unit absent for genuinely unmeasured or purely informational lines.", instructions)
        self.assertIn("Keep unmeasured physical lines in the basis for review.", instructions)
        self.assertIn("Do not emit line_items for informational lines.", instructions)
        for required in ("id, tag, text and confidence_pct (or confidence)", "Measured physical lines also have quantity and unit",
                         "JSON integer from 0 to 100", "supplied aliases must agree",
                         "Pure informational notes may omit quantity and unit", "source_basis_line_id"):
            self.assertIn(required, tail)
        self.assertTrue(instructions.endswith("\\n"))


if __name__ == "__main__":
    unittest.main()
