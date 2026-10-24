"""Fresh provider data admission; no real model, remote service or customer data."""
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_webapp as existing
from tests.test_basis_source_provenance import CATALOG
from tests.test_openai_draft_request_contract import synthetic_image

s = existing.webapp


def draft():
    text = "[ sqm Synthetic surface ] - Separate physical installation"
    return {"project": {"booth_width": 4, "booth_depth": 3}, "quote_basis_sections": [
        {"id": "surfaces", "title": "Surfaces", "lines": [
            {"id": "dimension-note", "tag": "Confirm", "text": text, "quantity": 12,
             "unit": "sqm", "confidence_pct": 90, "pricing_keyword": "synthetic-surface"}]}],
        "line_items": [{"source_basis_line_id": "dimension-note", "section": "Surfaces", "description": text,
                        "quantity": 12, "unit": "sqm", "pricing_keyword": "synthetic-surface"}]}


class ProviderAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(s.urllib.request, "urlopen", side_effect=AssertionError("NETWORK_DENIED")))

    def admit(self, value):
        return s.admit_provider_quote_draft(value, require_confidence=True)

    def test_full_draft_prompt_declares_the_admitted_physical_row_contract(self):
        with mock.patch.dict(os.environ, existing.isolated_env(), clear=True), \
             mock.patch.object(s, "read_dotenv_value", side_effect=existing.env_only_dotenv_value), \
             mock.patch.object(s, "profiles_root", return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT / "profiles"), \
             mock.patch.object(s, "pricing_references_root", return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT / "pricing-references"):
            prompt = s.build_quote_draft_prompt(existing.valid_payload())
        for text in ("one unique stable id", "same id in its linked line_items source_basis_line_id",
                     "linked to exactly one physical basis line", "positive JSON numbers",
                     "Repeated measurements in text, structured fields and linked rows must agree",
                     "confidence_pct as an integer from 0 to 100",
                     "Pure informational notes may omit measurements and need no output item"):
            self.assertIn(text, prompt)
        self.assertNotIn("source_basis_line_id where possible", prompt)

    def test_actual_draft_request_declares_string_ids_and_existing_alphabet(self):
        payload = existing.valid_payload()
        payload["images"] = [{"name": "synthetic.png", "data_url": synthetic_image()}]
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"output_text":"{}"}'
        with mock.patch.dict(os.environ, existing.isolated_env(), clear=True), \
             mock.patch.object(s, "read_dotenv_value", side_effect=existing.env_only_dotenv_value), \
             mock.patch.object(s, "profiles_root", return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT / "profiles"), \
             mock.patch.object(s, "pricing_references_root", return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT / "pricing-references"), \
             mock.patch.object(s, "catalog_visual_image_entries_for_payload", return_value=[]), \
             mock.patch.object(s, "normalize_ai_draft", return_value={"ok": True}), \
             mock.patch.object(s.urllib.request, "urlopen", return_value=response) as send:
            s.request_openai_quote_basis(payload, "synthetic-mocked-key")
        self.assertEqual(send.call_count, 1)
        body = json.loads(send.call_args.args[0].data)
        self.assertEqual(set(body), {"model", "input", "reasoning"})
        self.assertEqual(len(body["input"]), 1)
        self.assertEqual(body["input"][0]["role"], "user")
        prompt = body["input"][0]["content"][0]["text"]
        self.assertIn("All section id, physical line id, and linked source_basis_line_id values must be JSON strings", prompt)
        self.assertIn("unique section ids matching ^[a-z0-9]+(?:-[a-z0-9]+)*$", prompt)
        self.assertIn("Physical line id and linked source_basis_line_id values must match ^[A-Za-z0-9_-]+$", prompt)
        self.assertIn("ASCII letters, digits, underscores, and hyphens only", prompt)
        self.assertIn("Use the same id in its linked line_items source_basis_line_id", prompt)

    def test_existing_row_identity_guard_accepts_declared_strings_and_rejects_other_types(self):
        self.assertEqual(s.PROFILE_ID_RE.pattern, r"^[A-Za-z0-9_-]+$")
        for identity in ("Physical_A-09", "7", "_"):
            raw = draft()
            raw["quote_basis_sections"][0]["id"] = "section-a-09"
            raw["quote_basis_sections"][0]["lines"][0]["id"] = identity
            raw["line_items"][0]["source_basis_line_id"] = identity
            admitted = self.admit(raw)
            self.assertEqual(admitted["line_items"][0]["source_basis_line_id"], identity)
        for target in ("basis", "item"):
            for identity in ("physical row", "physical.row", "physical/row", "\u00e9", 7, True, [], {}):
                raw = draft()
                row = raw["quote_basis_sections"][0]["lines"][0] if target == "basis" else raw["line_items"][0]
                row["id" if target == "basis" else "source_basis_line_id"] = identity
                with self.subTest(target=target, identity=identity), self.assertRaises(s.AIModelOutputError) as rejected:
                    self.admit(raw)
                self.assertEqual(rejected.exception.diagnostics["response_contract_reason"], "row_identity_shape")

    def test_existing_section_canonical_type_guard_and_normalisation_collisions(self):
        for identity in ("surface-7", "7", "a"):
            raw = draft()
            raw["quote_basis_sections"][0]["id"] = identity
            self.assertEqual(s.normalize_ai_draft(raw, require_confidence=True)["quote_basis_sections"][0]["id"], identity)
        for identity in (7, True, [], {}):
            raw = draft()
            raw["quote_basis_sections"][0]["id"] = identity
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "section id must be a string"):
                s.canonical_quote_basis_sections(raw)
        raw = draft()
        raw["quote_basis_sections"][0]["id"] = "NonCanonical_Section"
        self.assertEqual(s.normalize_ai_draft(raw, require_confidence=True)["quote_basis_sections"][0]["id"], "surfaces")
        raw["quote_basis_sections"].append({"id": "Different_NonCanonical", "title": "Surfaces", "lines": []})
        with self.assertRaisesRegex(ValueError, "colliding identities"):
            s.normalize_ai_draft(raw, require_confidence=True)
        raw = draft()
        raw["quote_basis_sections"].append({"id": "surfaces", "title": "Other", "lines": []})
        with self.assertRaises(s.AIModelOutputError) as rejected:
            self.admit(raw)
        self.assertEqual(rejected.exception.diagnostics["response_contract_reason"], "section_identity_collision")

    def test_quantity_contract_accepts_exact_positive_canonical_values(self):
        for value in (1, 12, 12.0, 12.5, "12", "12.0", "12.5", "0.25"):
            with self.subTest(value=value):
                raw = draft()
                raw["quote_basis_sections"][0]["lines"][0]["quantity"] = value
                raw["line_items"][0]["quantity"] = value
                before = copy.deepcopy(raw)
                self.assertEqual(self.admit(raw), raw)
                self.assertEqual(raw, before)
        raw = draft()
        raw["quote_basis_sections"][0]["lines"][0]["quantity"] = "12.0"
        self.admit(raw)  # Numeric equality is semantic, not spelling equality.

    def test_quantities_reject_before_any_normalisation_or_pricing(self):
        for value in ("12,5", "1,000", "12%", "1e2", "NaN", "Infinity", "-2", "+2", 0, -1,
                      float("inf"), float("nan"), "0." + "0" * 400 + "1", True, False, [], {}):
            for field in ("basis", "item"):
                with self.subTest(value=value, field=field):
                    raw = draft()
                    row = raw["line_items"][0] if field == "item" else raw["quote_basis_sections"][0]["lines"][0]
                    row["quantity"] = value
                    with mock.patch.object(s, "capture_pricing_reference_snapshot", side_effect=AssertionError("PRICING_REACHED")), \
                         self.assertRaises(s.AIModelOutputError):
                        s.normalize_ai_draft(raw, {}, require_confidence=True)

    def test_prefix_only_and_identity_only_measurements_are_preserved(self):
        raw = draft()
        line, row = raw["quote_basis_sections"][0]["lines"][0], raw["line_items"][0]
        line.pop("quantity"); line.pop("unit")
        row.update(description="12 m\u00b2 " + row["description"], quantity=None, unit="")
        admitted = self.admit(raw)
        self.assertEqual(s.pricing_analysis_source_units(admitted["line_items"]), {"dimension-note": "sqm"})
        self.assertNotIn("quantity", admitted["quote_basis_sections"][0]["lines"][0])
        self.assertEqual(s.provider_row_measurement(admitted["line_items"][0], row["description"])[0], 12)

    def test_prefix_and_linked_measurement_conflicts_reject(self):
        for case in ("basis-prefix", "item-prefix", "both-prefix", "quantity", "unit", "bad-prefix-comma", "bad-prefix-percent", "bad-prefix-negative"):
            with self.subTest(case=case):
                raw = draft(); line = raw["quote_basis_sections"][0]["lines"][0]; row = raw["line_items"][0]
                if case in ("basis-prefix", "both-prefix"): line["text"] = "3 sqm " + line["text"]
                if case in ("item-prefix", "both-prefix"): row["description"] = "3 sqm " + row["description"]
                if case == "quantity": line["quantity"] = 7
                if case == "unit": line["unit"] = "lot"
                if case.startswith("bad-prefix"):
                    row["description"] = {"bad-prefix-comma": "12,5", "bad-prefix-percent": "12%", "bad-prefix-negative": "-12"}[case] + " sqm " + row["description"]
                with self.assertRaises(s.AIModelOutputError): self.admit(raw)

    def test_rebound_conflicts_unidentified_scope_and_orphan_ids_reject(self):
        for case in ("unbound-conflict", "missing-both", "orphan"):
            raw = draft()
            if case == "unbound-conflict":
                raw["line_items"][0].pop("source_basis_line_id")
                raw["quote_basis_sections"][0]["lines"][0]["quantity"] = 7
            elif case == "missing-both":
                raw["line_items"][0].pop("source_basis_line_id")
                raw["quote_basis_sections"][0]["lines"][0].pop("id")
            else:
                raw["line_items"][0]["source_basis_line_id"] = "foreign-row"
            with self.subTest(case=case), self.assertRaises(s.AIModelOutputError): self.admit(raw)

    def test_identified_items_only_shape_and_legacy_basis_remain_supported(self):
        raw = draft(); raw.pop("quote_basis_sections")
        self.assertEqual(self.admit(raw), raw)
        raw["quote_basis"] = {"surfaces": "Confirm: Synthetic scope"}
        self.assertEqual(self.admit(raw), raw)

    def test_coherent_identity_aliases_canonicalise_without_unit_inference(self):
        for alias in ("id", "source_line_item_id", "source_basis_line_id"):
            raw = draft(); row = raw["line_items"][0]; row.pop("source_basis_line_id")
            row[alias] = "dimension-note"; row["unit"] = "m\u00b2"
            admitted = self.admit(raw)
            self.assertEqual(admitted["line_items"][0]["source_basis_line_id"], "dimension-note")
            self.assertEqual(s.pricing_analysis_source_units(admitted["line_items"]), {"dimension-note": "sqm"})
            row["unit"] = ""
            self.assertEqual(s.pricing_analysis_source_units(self.admit(raw)["line_items"]), {"dimension-note": ""})

    def test_identity_collisions_and_conflicting_aliases_reject(self):
        for case in ("duplicate-basis", "duplicate-item", "basis-alias", "item-alias", "invalid-id"):
            raw = draft(); lines = raw["quote_basis_sections"][0]["lines"]; rows = raw["line_items"]
            if case == "duplicate-basis": lines.append({**lines[0], "text": "Other physical scope"})
            elif case == "duplicate-item": rows.append(copy.deepcopy(rows[0]))
            elif case == "basis-alias": lines[0]["source_line_item_id"] = "foreign-row"
            elif case == "item-alias": rows[0]["id"] = "foreign-row"
            else: rows[0]["source_basis_line_id"] = True
            with self.subTest(case=case), self.assertRaises(s.AIModelOutputError): self.admit(raw)

    def test_declared_confidence_is_integer_percentage_not_fractional_scale(self):
        for value in (0, 1, 95, 100):
            raw = draft(); line = raw["quote_basis_sections"][0]["lines"][0]
            line.update(confidence_pct=value, confidence=value)
            self.admit(raw)
        for value in (.95, 95.0, "95%", "95", True, -1, 101, None):
            raw = draft(); raw["quote_basis_sections"][0]["lines"][0]["confidence_pct"] = value
            with self.subTest(value=value), self.assertRaises(s.AIModelOutputError): self.admit(raw)
        raw = draft(); raw["quote_basis_sections"][0]["lines"][0]["confidence"] = 10
        with self.assertRaises(s.AIModelOutputError): self.admit(raw)
        # Legacy saved/user consumers remain intentionally separate from fresh admission.
        self.assertEqual(s.normalize_confidence_percent("95%"), 95)
        self.assertEqual(s.parse_float_or_none("1,000"), 1000)

    def test_material_structural_mutations_never_drop_rows_into_partial_success(self):
        mutations = [lambda d: d.update(line_items={"row": d["line_items"][0]}),
            lambda d: d["line_items"].append("unknown row"),
            lambda d: d.update(quote_basis_sections={}),
            lambda d: d["quote_basis_sections"].append(12),
            lambda d: d["quote_basis_sections"][0].update(lines={}),
            lambda d: d["quote_basis_sections"][0]["lines"].append({"name": "Missing supported text", "quantity": 7}),
            lambda d: d["quote_basis_sections"][0]["lines"].append(12),
            lambda d: d.update(project=[]), lambda d: d.update(quote_basis={"scope": {"unhandled": True}})]
        for index, mutation in enumerate(mutations):
            raw = draft(); mutation(raw)
            with self.subTest(index=index), self.assertRaises(s.AIModelOutputError): self.admit(raw)
        self.assertEqual(self.admit({}), {})
        self.assertEqual(self.admit({"line_items": [], "quote_basis_sections": []}), {"line_items": [], "quote_basis_sections": []})

    def test_json_single_object_fences_and_bounded_prose(self):
        for text in ('{"quantity":12}', '\x60\x60\x60json\n{"quantity":12}\n\x60\x60\x60', 'Result follows: {"quantity":12} End of result.'):
            self.assertEqual(s.parse_json_object(text), {"quantity": 12})
        for text in ('[{"quantity":12}]', '{"quantity":12,"quantity":7}', '{"row":{"unit":"sqm","unit":"lot"}}',
                     '{"quantity":12}\n{"quantity":7}', 'Reason {uncertain} then {"quantity":12}',
                     '{"quantity":NaN}', '{"quantity":Infinity}', '{"quantity":1e9999}', '{"nested":[1e9999]}', '{"quantity":12} []'):
            with self.subTest(text=text), self.assertRaises(s.OpenAIAnalysisError): s.parse_json_object(text)

    def test_unclosed_whitespace_fences_reject_with_a_bounded_child(self):
        code = "from tests.test_webapp import webapp as s\n" + (
            "for width in (1000, 4000, 16000):\n"
            " for prefix in (chr(96) * 3, chr(96) * 3 + 'json'):\n"
            "  try: s.parse_json_object(prefix + ' ' * width + 'x')\n"
            "  except s.OpenAIAnalysisError: pass\n"
            "  else: raise AssertionError('unclosed fence admitted')\n"
            "print('FENCE_BOUND_PASS')\n"
        )
        result = subprocess.run([sys.executable, "-c", code],
            cwd=Path(__file__).resolve().parents[1], env={**os.environ, "SQAG_DISABLE_DOTENV": "1"},
            capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "FENCE_BOUND_PASS")

    def test_openai_shared_envelope_rejects_partial_refused_or_conflicting_text(self):
        for status in ("incomplete", "failed", "queued", "in_progress", "cancelled", "unknown"):
            with self.subTest(status=status), self.assertRaises(s.OpenAIAnalysisError):
                s.response_output_text({"status": status, "output_text": '{"ok":true}'})
        for value in ({"refusal": "synthetic", "output_text": '{}'},
                      {"output": [{"type": "message", "content": [{"type": "refusal", "refusal": "synthetic"}]}], "output_text": '{}'},
                      {"output_text": '{}', "output": [{"content": [{"text": '{"other":1}'}]}]},
                      {"output": [{"content": [{"text": 4}]}]}, {"output": [4]}):
            with self.subTest(value=value), self.assertRaises(s.OpenAIAnalysisError): s.response_output_text(value)
        self.assertEqual(s.response_output_text({"status": "completed", "output_text": "", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{}'}]}]}), '{}')
        self.assertEqual(s.response_output_text({"output_text": '{}'}), '{}')  # Retained normalized legacy envelope consumer.

    def test_deepseek_shared_envelope_enforces_finish_and_refusal(self):
        for finish in ("length", "content_filter", "tool_calls"):
            with self.subTest(finish=finish), self.assertRaises(s.AIModelOutputError):
                s.chat_completions_output_text({"choices": [{"finish_reason": finish, "message": {"content": '{}'}}]})
        with self.assertRaises(s.AIModelOutputError):
            s.chat_completions_output_text({"choices": [{"finish_reason": "stop", "message": {"refusal": "synthetic", "content": '{}'}}]})
        self.assertEqual(s.chat_completions_output_text({"choices": [{"finish_reason": "stop", "message": {"content": '{}'}}]}), '{}')


class ProviderAdapterContractTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("NETWORK_DENIED")))
        self.enterContext(mock.patch.dict(os.environ, existing.isolated_env(), clear=True))
        self.enterContext(mock.patch.object(s, "read_dotenv_value", side_effect=existing.env_only_dotenv_value))
        self.enterContext(mock.patch.object(s, "write_local_log", return_value=True))
        for name in ("build_basis_chat_prompt", "build_pricing_catalog_import_prompt", "build_pricing_catalog_metadata_prompt", "build_quote_draft_prompt"):
            self.enterContext(mock.patch.object(s, name, return_value="Synthetic prompt"))
        self.enterContext(mock.patch.object(s, "catalog_visual_image_entries_for_payload", return_value=[]))
        self.detail = {"id": "synthetic-response-reference", "source": "local", "currency": "SGD", "tax": {"label": "GST", "rate": 0}, "items": CATALOG}
        self.detail["digest_sha256"] = s.pricing_reference_catalog_digest(self.detail)
        self.snapshot = s.PricingReferenceSnapshot(source="local", reference_id=self.detail["id"],
            digest=self.detail["digest_sha256"], currency="SGD", detail_json=json.dumps(self.detail))

    def test_all_openai_routes_share_completion_admission_after_one_mock_send(self):
        for route in ("draft", "chat", "import", "metadata"):
            for mode in ("completed", "incomplete", "refusal"):
                parsed = {"intent": "answer", "answer": "Synthetic answer"} if route == "chat" else {"items": []} if route in ("import", "metadata") else draft()
                envelope = {"status": "incomplete" if mode == "incomplete" else "completed", "output_text": json.dumps(parsed)}
                if mode == "refusal": envelope["refusal"] = "Synthetic refusal"
                response = mock.MagicMock(); response.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
                with self.subTest(route=route, mode=mode), mock.patch.object(s.urllib.request, "urlopen", return_value=response) as send:
                    def call():
                        if route == "draft": return s.request_openai_quote_basis({"images": [{"name": "synthetic.png", "data_url": synthetic_image()}],
                            "pricing_reference_id": self.detail["id"], "pricing_reference_source": "local",
                            "pricing_reference": self.detail, "_pricing_reference_snapshot": self.snapshot}, "synthetic-key")
                        if route == "chat": return s.request_openai_basis_chat_with_model({"basis_chat": {"question": "What is this?"}}, "synthetic-key", "gpt-6-luna")
                        if route == "import": return s.request_openai_pricing_catalog_import("synthetic", {}, {}, "synthetic-key")
                        return s.request_openai_pricing_catalog_metadata("synthetic", [], "synthetic-key")
                    if mode == "completed": self.assertIsInstance(call(), dict)
                    else:
                        with self.assertRaises(s.OpenAIAnalysisError): call()
                    self.assertEqual(send.call_count, 1)

    def test_unrepresentable_derived_amount_rejects_before_pricing_or_seal(self):
        raw = draft()
        raw["line_items"][0]["quantity"] = 1e308
        raw["quote_basis_sections"][0]["lines"][0]["quantity"] = 1e308
        payload = {"pricing_reference_id": self.detail["id"], "pricing_reference_source": "local",
                   "pricing_reference": self.detail, "_pricing_reference_snapshot": self.snapshot}
        with mock.patch.object(s, "normalize_line_items", side_effect=AssertionError("PRICING_REACHED")), \
             self.assertRaises(s.AIModelOutputError):
            s.normalize_ai_draft(raw, payload, require_confidence=True)

    def test_deepseek_adapters_reach_mock_transport_and_refuse_truncation(self):
        for route in ("json", "chat", "import", "metadata"):
            for finish in ("stop", "length"):
                parsed = {"intent": "answer", "answer": "Synthetic answer"} if route == "chat" else {"items": []}
                envelope = {"choices": [{"finish_reason": finish, "message": {"content": json.dumps(parsed)}}]}
                response = mock.MagicMock(); response.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
                with self.subTest(route=route, finish=finish), mock.patch.object(s.urllib.request, "urlopen", return_value=response) as send:
                    def call():
                        if route == "json": return s.request_deepseek_json_object("Synthetic prompt", "synthetic-key", s.configured_deepseek_pricing_import_model(), 1200, "pricing import")
                        if route == "chat": return s.request_deepseek_basis_chat_with_model({"basis_chat": {"question": "What is this?"}}, "synthetic-key", s.configured_deepseek_basis_answer_model())
                        if route == "import": return s.request_deepseek_pricing_catalog_import("synthetic", {}, {}, "synthetic-key")
                        return s.request_deepseek_pricing_catalog_metadata("synthetic", [], "synthetic-key")
                    if finish == "stop": self.assertIsInstance(call(), dict)
                    else:
                        with self.assertRaises(s.OpenAIAnalysisError): call()
                    self.assertEqual(send.call_count, 1)




class ProviderHTTPContractTest(unittest.TestCase):
    def setUp(self):
        self.fixture = existing.WebappServerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def run_http(self, raw, *, exercise_selection=False):
        from tests import test_basis_source_provenance as provenance
        with tempfile.TemporaryDirectory(dir=existing.test_temp_root()) as temp:
            reference_id = "response-admission-reference"
            existing.write_test_pricing_reference(Path(temp), reference_id, CATALOG)
            payload = existing.valid_payload()
            payload.update(images=[{"name": "synthetic.png", "data_url": synthetic_image()}],
                pricing_reference_id=reference_id, pricing_reference_source="local",
                pricing_reference={"id": reference_id, "source": "local", "currency": "SGD"},
                quote_session={"session_id": "quote-response-contract"})
            env = existing.isolated_env(OPENAI_API_KEY="synthetic-mocked-only", AUTH_REQUIRED="false",
                                       QUOTE_DATA_ROOT=str(self.fixture._test_data_root))
            envelope = {"status": "completed", "output_text": json.dumps(raw)}
            response = mock.MagicMock(); response.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(s, "read_dotenv_value", side_effect=existing.env_only_dotenv_value), \
                 mock.patch.object(s, "pricing_references_root", return_value=Path(temp)), \
                 mock.patch.object(s.urllib.request, "urlopen", return_value=response) as send, \
                 mock.patch.object(s, "write_local_log"), existing.LocalRunnerServer() as runner:
                headers = self.fixture._ledger_runner_headers(runner)
                drafted = self.fixture._ledger_post(runner, headers, "/api/draft", payload)
                self.assertEqual(send.call_count, 1)
                result = drafted["body"]
                if not exercise_selection:
                    self.assertEqual(result["status"], "blocked")
                    self.assertTrue(result["error_reference"])
                    self.assertNotIn("line_items", result)
                    self.assertNotIn("pricing_analysis_seal", result)
                    return
                self.assertEqual(result["status"], "drafted", drafted["text"][-1000:])
                reference = s.exact_pricing_reference_authority(payload)["detail"]
                request = {**payload, "line_items": result["line_items"], "quote_basis_sections": result["quote_basis_sections"],
                           "pricing_analysis_seal": result["pricing_analysis_seal"]}
                expected_ids = {"physical-row", "dimension-note"}
                lines = [line for section in result["quote_basis_sections"] for line in section["lines"]]
                self.assertEqual({line["id"] for line in lines}, expected_ids)
                self.assertEqual(len(lines), 2)
                self.assertEqual({row["source_basis_line_id"] for row in result["line_items"]}, expected_ids)
                self.assertEqual({row["unit"] for row in result["pricing_analysis_seal"]["rows"]}, {"sqm"})
                self.fixture._ledger_sync(request, pricing_basis={"currency": reference["currency"], "source": "local",
                    "id": reference_id, "digest": reference["digest_sha256"]})
                chosen = provenance.ProviderSourceSelectionTest.browser(self, request, reference)
                request["quote_basis_sections"] = chosen["sections"]
                self.fixture._ledger_sync(request)
                selected = self.fixture._ledger_post(runner, headers, "/api/quote-sessions/pricing-selections",
                                                     {**request, "pricing_selections": chosen["pending"]})
                self.assertEqual(selected["status"], 200, selected["text"][-1000:])
                self.assertEqual(selected["body"]["selections"], [{"source_basis_line_id": "dimension-note", "status": "selected"}])
                request["line_items"] = selected["body"]["line_items"]
                # Include both actual physical identities before production Output mapping.
                for section in request["quote_basis_sections"]:
                    for line in section["lines"]: line["tag"] = "Include"
                output = provenance.ProviderSourceSelectionTest.browser(self, request, reference, request["line_items"])["output"]
                self.assertEqual({row["id"] for row in output}, expected_ids)
                self.assertEqual({row["unit"] for row in output}, {"sqm"})
                self.assertEqual({row["id"]: row["amount"] for row in output}, {"physical-row": "516.00", "dimension-note": "301.00"})
                self.fixture._ledger_sync(request)
                saved = self.fixture._ledger_post(runner, headers, "/api/quote-sessions", request)
                self.assertEqual(saved["status"], 200, saved["text"][-1000:])
                reopened = self.fixture.http_json(runner, "GET", "/api/quote-sessions/quote-response-contract")
                self.assertEqual(reopened["status"], 200)
                restored = reopened["body"]["quote_session"]["draft_state"]["lineItems"]
                self.assertEqual({row["source_basis_line_id"] for row in restored}, expected_ids)
                self.assertTrue(all(row["pricing_authority"]["variant"] == "catalog" for row in restored))

    def test_real_draft_alias_unit_selection_two_same_text_rows_and_saved_reopen(self):
        from tests import test_basis_source_provenance as provenance
        raw = provenance.ProviderSourceSelectionTest.raw_draft(self)
        for index, (line, row) in enumerate(zip(raw["quote_basis_sections"][0]["lines"], raw["line_items"])):
            for item in (line, row): item.update(quantity=12 if index == 0 else 7, unit="m\u00b2")
            line["text"] = row["description"] = "Synthetic surface physical installation"
        raw["line_items"][1]["id"] = "dimension-note"  # Coherent alternate explicit identity.
        self.run_http(raw, exercise_selection=True)

    def test_real_draft_invalid_quantity_missing_scope_and_collision_reject_without_partial_result(self):
        for case in ("comma", "prefix-conflict", "missing-ids", "orphan-id", "malformed-extra", "duplicate-id"):
            raw = draft()
            if case == "comma": raw["line_items"][0]["quantity"] = "12,5"
            elif case == "prefix-conflict": raw["line_items"][0]["description"] = "3 sqm " + raw["line_items"][0]["description"]
            elif case == "missing-ids":
                raw["line_items"][0].pop("source_basis_line_id")
                raw["quote_basis_sections"][0]["lines"][0].pop("id")
            elif case == "orphan-id": raw["line_items"][0]["source_basis_line_id"] = "foreign-row"
            elif case == "malformed-extra": raw["quote_basis_sections"][0]["lines"].append({"name": "Dropped physical scope"})
            else: raw["quote_basis_sections"][0]["lines"].append(copy.deepcopy(raw["quote_basis_sections"][0]["lines"][0]))
            with self.subTest(case=case): self.run_http(raw)


if __name__ == "__main__":
    unittest.main()
