"""Offline measurement retention through real draft transport and normalisation."""
import copy
import json
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.gateway_test_support import responses_envelope, synthetic_configuration
from tests.test_openai_draft_request_contract import synthetic_pdf
from webapp import ai_gateway, server


MEASUREMENTS = (("floor", "Floor panel", 36, "sqm"),
                ("rail", "Perimeter rail", 6, "m"),
                ("chair", "Visitor chair", 4, "nos"))
REASK_PREFIX = "Quantity prediction for an exhibition booth quote."
MEASURED_NOTE = " ".join(f"{text}: quantity={quantity}; unit={unit}."
                         for _, text, quantity, unit in MEASUREMENTS)


def draft(measured=True):
    lines, items = [], []
    for identity, text, quantity, unit in MEASUREMENTS:
        fields = {"quantity": quantity, "unit": unit} if measured else {}
        lines.append({"id": identity, "tag": "Confirm", "text": text,
                      "confidence_pct": 90, **fields})
        if measured:
            items.append({"source_basis_line_id": identity, "description": text,
                          "section": "Scope", "pricing_keyword": "", **fields})
    lines.append({"id": "concept", "tag": "Confirm", "confidence_pct": 100,
                  "text": "Booth footprint 6m x 6m; design selection for approval"})
    return {"quote_basis_sections": [{"id": "scope", "title": "Scope", "lines": lines}],
            "line_items": items, "project": {"booth_width": 6, "booth_depth": 6}}


class EvidenceTransport:
    """A deterministic channel probe, not a simulation of model compliance."""
    def __init__(self, retain=True, repair_measures=False, single_measured=True, repair_answer=None, repair_failure=""):
        self.repair_failure = repair_failure
        self.retain = retain
        self.repair_measures = repair_measures
        self.repair_answer = repair_answer
        self.single_measured = single_measured
        self.calls = []
        self.lock = threading.Lock()

    def send(self, encoded, **kwargs):
        body = json.loads(encoded)
        with self.lock:
            self.calls.append(body)
        parts = body["input"][0]["content"]
        text = parts[0]["text"]
        if text.startswith(REASK_PREFIX) and self.repair_failure:
            if self.repair_failure == "connection":
                raise ai_gateway.GatewayError("connection_failed", boundary="provider_transport", retryable=True)
            envelope = responses_envelope(body["model"], "", status=self.repair_failure if self.repair_failure != "refusal" else "completed")
            if self.repair_failure == "refusal":
                envelope["output"][0]["content"] = [{"type": "refusal", "refusal": "synthetic refusal"}]
            if self.repair_failure == "incomplete":
                envelope["incomplete_details"] = {"reason": "max_output_tokens"}
            return envelope
        if text.startswith(REASK_PREFIX):
            wanted = [line["id"] for line in json.loads(text.split("Context JSON: ", 1)[1])["lines"]]
            sizes = {identity: (quantity, unit) for identity, _, quantity, unit in MEASUREMENTS}
            result = self.repair_answer if self.repair_answer is not None else {"quantities": [
                {"id": identity, "quantity": sizes[identity][0], "unit": sizes[identity][1]}
                for identity in wanted if self.repair_measures]}
        elif text.startswith("Inspect each attached visual"):
            note = MEASURED_NOTE if self.retain else "Floor panel, perimeter rail and visitor chair; measurements unavailable."
            result = {"observations": [note] * (len(parts) - 1)}
        elif any(part["type"] == "input_file" for part in parts):
            result = draft(self.single_measured)
        else:
            observations = json.loads(text[text.index('[{"source":'):])
            result = draft(any(MEASURED_NOTE in row["observation"] for row in observations))
        return responses_envelope(body["model"], result if isinstance(result, str) else json.dumps(result))


class BasisQuantityRetentionTest(unittest.TestCase):
    def setUp(self):
        values = synthetic_configuration(credential="synthetic")
        self.enterContext(mock.patch.object(socket.socket, "connect", side_effect=AssertionError("Offline only")))
        self.enterContext(mock.patch.object(socket, "create_connection", side_effect=AssertionError("Offline only")))
        self.enterContext(mock.patch.object(server, "read_dotenv_value", side_effect=values.get))
        self.enterContext(mock.patch.object(server, "write_local_log"))
        # Attempt logging stays real (its writers are mocked) so each send is recorded before the next.
        self.enterContext(mock.patch.object(server, "append_ai_attempt_telemetry"))
        self.enterContext(mock.patch.object(server, "profile_defaults_source_for_payload",
                                           return_value=SimpleNamespace(config={"label": "Synthetic exhibition"})))
        self.enterContext(mock.patch.object(server, "profile_prompt_summary", return_value={}))
        self.enterContext(mock.patch.object(server, "pricing_reference_section_names_for_payload", return_value=[]))
        self.enterContext(mock.patch.object(server, "pricing_catalog_prompt_rows_for_payload", return_value=[]))
        self.enterContext(mock.patch.object(server, "catalog_visual_image_entries_for_payload", return_value=[]))
        # Keep real admission, normalisation and reconciliation, without a saved catalogue/profile.
        normalize = server.normalize_ai_draft
        self.enterContext(mock.patch.object(server, "normalize_ai_draft",
                                           side_effect=lambda parsed, *args, **kwargs: normalize(parsed, require_confidence=True)))

    def analyse(self, pages, retain=True, **transport_options):
        transport = EvidenceTransport(retain, **transport_options)
        with mock.patch.object(ai_gateway, "BoundedHttpsTransport", return_value=transport):
            gateway = server.configured_ai_gateway()
        route = gateway.configuration.route("draft")
        configuration = server.OpenAIDraftConfiguration("standard", route.model, route.effort, gateway)
        result = server.request_openai_quote_basis(
            {"images": [{"name": "synthetic.pdf", "data_url": synthetic_pdf(pages)}]},
            "synthetic", draft_configuration=configuration)
        return result, transport.calls

    def test_both_real_paths_retain_supplied_measurements_and_leave_notes_unmeasured(self):
        for pages, sends in ((12, 1), (13, 4)):
            with self.subTest(pages=pages):
                result, calls = self.analyse(pages)
                self.assertEqual(len(calls), sends)
                lines = {line["id"]: line for section in result["quote_basis_sections"] for line in section["lines"]}
                self.assertEqual(len(lines), 4)
                for identity, _, quantity, unit in MEASUREMENTS:
                    self.assertEqual(float(lines[identity]["quantity"]), quantity)
                    self.assertEqual(lines[identity]["unit"], unit)
                self.assertNotIn("quantity", lines["concept"])
                self.assertNotIn("unit", lines["concept"])
                self.assertEqual(len(result["line_items"]), 3)
                if pages > 12:
                    self.assertTrue(all(part["type"] == "input_text"
                                        for part in calls[-1]["input"][0]["content"]))

    def test_missing_sizes_get_one_ai_reask_then_the_booth_size_rule(self):
        # Owner decision 18 Nov 2026: every physical line ends with a size. Lossy observations leave the
        # synthesis without sizes; the one re-ask gives none, so the booth-size rule fills them. Without a
        # catalogue floor section nothing takes the booth area, so each line gets 1 lot at low confidence.
        for answer in (None, "not json", {"quantities": "none"}, {"quantities": [
                {"id": "floor", "quantity": 0, "unit": "sqm"}, {"id": "rail", "quantity": 3, "unit": "furlong"},
                {"id": "chair", "quantity": True, "unit": "nos"}]}):
            with self.subTest(answer=answer):
                batched, calls = self.analyse(13, retain=False, repair_answer=answer)
                self.assertEqual(len(calls), 5)
                self.assertTrue(calls[-1]["input"][0]["content"][0]["text"].startswith(REASK_PREFIX))
                lines = {line["id"]: line for section in batched["quote_basis_sections"] for line in section["lines"]}
                for identity in ("floor", "rail", "chair"):
                    self.assertEqual((float(lines[identity]["quantity"]), lines[identity]["unit"]), (1, "lot"))
                    self.assertLessEqual(lines[identity]["confidence"], server.PREDICTED_QUANTITY_RULE_CONFIDENCE)
                self.assertNotIn("quantity", lines["concept"])
                self.assertEqual(sorted(row["source_basis_line_id"] for row in batched["line_items"]), ["chair", "floor", "rail"])
                self.assertEqual(batched["analysis_findings"][0]["id"], "booth-size-rule")
                self.assertIn("Floor panel = 1 lot", batched["analysis_findings"][0]["text"])

    def test_a_provider_failure_on_the_reask_falls_back_to_the_rule(self):
        # G2 (#392) round 2: a provider-level failure on the re-ask must not fail the whole analysis.
        for failure in ("connection", "failed", "incomplete", "refusal"):
            for pages, options in ((13, {"retain": False}), (12, {"single_measured": False})):
                with self.subTest(failure=failure, pages=pages):
                    result, calls = self.analyse(pages, repair_failure=failure, **options)
                    self.assertTrue(calls[-1]["input"][0]["content"][0]["text"].startswith(REASK_PREFIX))
                    lines = {line["id"]: line for section in result["quote_basis_sections"] for line in section["lines"]}
                    for identity in ("floor", "rail", "chair"):
                        self.assertEqual((float(lines[identity]["quantity"]), lines[identity]["unit"]), (1, "lot"))
                    self.assertEqual(result["analysis_findings"][0]["id"], "booth-size-rule")

    def test_one_ai_reask_supplies_missing_sizes_on_both_paths(self):
        batched, calls = self.analyse(13, retain=False, repair_measures=True)
        single, single_calls = self.analyse(12, single_measured=False, repair_measures=True)
        self.assertEqual((len(calls), len(single_calls)), (5, 2))
        for result in (batched, single):
            lines = {line["id"]: line for section in result["quote_basis_sections"] for line in section["lines"]}
            for identity, _, quantity, unit in MEASUREMENTS:
                self.assertEqual((float(lines[identity]["quantity"]), lines[identity]["unit"]), (quantity, unit))
                self.assertLessEqual(lines[identity]["confidence"], server.PREDICTED_QUANTITY_AI_CONFIDENCE)
            self.assertFalse(any(finding["id"] == "booth-size-rule" for finding in result["analysis_findings"]))
            self.assertEqual(result["analysis_findings"][0]["id"], "predicted-sizes")
            self.assertEqual(len(result["line_items"]), 3)
        # Both re-asks are small and text-only: no media, no catalogue, no draft schema, only the lines to size.
        for reask in (calls[-1], single_calls[-1]):
            self.assertEqual([part["type"] for part in reask["input"][0]["content"]], ["input_text"])
            self.assertNotIn("text", reask)
            text = reask["input"][0]["content"][0]["text"]
            self.assertTrue(text.startswith(REASK_PREFIX))
            context = json.loads(text.split("Context JSON: ", 1)[1])
            self.assertEqual([line["id"] for line in context["lines"]], ["floor", "rail", "chair"])
            self.assertEqual(context["booth"]["booth_area_sqm"], 36)

    def test_the_reask_can_only_size_the_listed_lines(self):
        # G2 (#392): the re-ask answer must not replace the first answer. Extra ids, a second entry for the same
        # line, and text or tag changes are ignored; only quantity and unit for listed lines are taken.
        answer = {"quantities": [{"id": "floor", "quantity": 40, "unit": "sqm"},
                                 {"id": "floor", "quantity": 999, "unit": "sqm"},
                                 {"id": "concept", "quantity": 5, "unit": "nos", "text": "Injected"},
                                 {"id": "rail", "quantity": 12, "unit": "m", "tag": "Exclude"},
                                 {"id": "chair", "quantity": 4, "unit": "nos"}]}
        result, _ = self.analyse(13, retain=False, repair_answer=answer)
        lines = {line["id"]: line for section in result["quote_basis_sections"] for line in section["lines"]}
        self.assertEqual((float(lines["floor"]["quantity"]), lines["floor"]["unit"]), (40, "sqm"))
        self.assertEqual((float(lines["rail"]["quantity"]), lines["rail"]["unit"]), (12, "m"))
        self.assertEqual(lines["rail"]["tag"], "Confirm")
        self.assertNotIn("quantity", lines["concept"])
        self.assertEqual(lines["concept"]["text"], "Booth footprint 6m x 6m; design selection for approval")
        self.assertEqual([line["text"] for line in lines.values()],
                         ["Floor panel", "Perimeter rail", "Visitor chair", lines["concept"]["text"]])

    def test_measured_drafts_send_no_reask(self):
        for pages, sends in ((12, 1), (13, 4)):
            with self.subTest(pages=pages):
                _, calls = self.analyse(pages)
                self.assertEqual(len(calls), sends)
                self.assertFalse(any(body["input"][0]["content"][0]["text"].startswith(REASK_PREFIX) for body in calls))

    def test_batch_prompts_require_object_measurements_and_safe_synthesis(self):
        _, calls = self.analyse(13)
        batches = [body["input"][0]["content"][0]["text"] for body in calls[:-1]]
        for prompt in batches:
            self.assertIn("Retain explicit quantities and units", prompt)
            self.assertIn("object and location", prompt)
            self.assertIn("dimensions separate from billable quantities", prompt)
            self.assertIn("Do not invent measurements", prompt)
        synthesis = calls[-1]["input"][0]["content"][0]["text"]
        self.assertIn("Analyze the uploaded reference images", synthesis)
        self.assertIn("Carry supported quantities and units", synthesis)
        self.assertIn("linked line_items", synthesis)
        self.assertIn("Do not replace a missing measurement with 1 lot", synthesis)
        self.assertIn("Area or length quantities derived from stated booth or object dimensions", synthesis)
        self.assertIn("including floor area from booth size", synthesis)
        self.assertIn("except for services genuinely priced per lot in the catalog", synthesis)
        self.assertIn(server.PREDICTED_QUANTITY_RULE, synthesis)
        self.assertIn("predict the quantity in the catalog unit instead", synthesis)
        self.assertNotIn("Do not invent quantities or units", synthesis)
        observations = json.loads(synthesis[synthesis.index('[{"source":'):])
        self.assertEqual([row["source"] for row in observations],
                         [f"Reference 1, PDF page {page}" for page in range(1, 14)])
        self.assertTrue(all(row["observation"] == MEASURED_NOTE for row in observations))

    def test_one_lot_on_an_area_catalogue_item_is_sized_not_left_as_one_lot(self):
        # G2 (#392): "1 lot" against a per-sqm catalogue item was later cleared or shown as 1 sqm unpriced.
        parsed = {"quote_basis_sections": [{"id": "flooring", "title": "Flooring", "lines": [
            {"id": "carpet", "tag": "Confirm", "text": "Grey Carpet Tile", "confidence_pct": 82, "quantity": 1,
             "unit": "lot", "pricing_keyword": "grey-carpet"},
            {"id": "spot", "tag": "Confirm", "text": "6 inch Spotlight", "confidence_pct": 82, "quantity": 1,
             "unit": "lot", "pricing_keyword": "spotlight"}]}],
            "line_items": [{"source_basis_line_id": "carpet", "section": "Flooring", "description": "Grey Carpet Tile",
                            "quantity": 1, "unit": "lot", "pricing_keyword": "grey-carpet"}],
            "project": {"booth_width": 8, "booth_depth": 9}}
        catalog = {"grey-carpet": {"unit": "sqm", "section": "Flooring"}, "spotlight": {"unit": "nos", "section": "Lighting"}}
        with mock.patch.object(server, "raw_draft_catalog_entries", return_value=catalog):
            self.assertEqual([line["id"] for _, line in server.raw_draft_lines_missing_quantity(parsed, catalog)], ["carpet"])
            completed = server.complete_predicted_quantities(copy.deepcopy(parsed), {}, None)
        carpet, spot = completed["quote_basis_sections"][0]["lines"]
        self.assertEqual((carpet["quantity"], carpet["unit"], carpet["confidence_pct"]), (72, "sqm", 30))
        self.assertEqual((completed["line_items"][0]["quantity"], completed["line_items"][0]["unit"]), (72, "sqm"))
        self.assertEqual((spot["quantity"], spot["unit"], spot["confidence_pct"]), (1, "lot", 82))
        self.assertIn("Grey Carpet Tile = 72 sqm", completed["analysis_findings"][0]["text"])

    def test_the_rule_uses_catalogue_data_and_adds_a_row_for_a_catalogue_line_without_one(self):
        # G2 (#392): the floor rule comes from the saved catalogue section, not from words in the line, and a
        # catalogue line with no linked row gets one before normalisation so Confirm can price it.
        parsed = {"quote_basis_sections": [{"id": "s", "title": "Booth", "lines": [
            {"id": "carpet", "tag": "Include", "text": "Carpet", "confidence_pct": 80, "pricing_keyword": "grey-carpet"},
            {"id": "sticker", "tag": "Include", "text": "Floor Sticker", "confidence_pct": 80, "pricing_keyword": "floor-sticker"},
            {"id": "rail", "tag": "Include", "text": "Glass rail", "confidence_pct": 80, "pricing_keyword": "glass-rail"},
            {"id": "misc", "tag": "Include", "text": "Site supervision", "confidence_pct": 80}]}],
            "line_items": [], "project": {"booth_width": 8, "booth_depth": 9}}
        catalog = {"grey-carpet": {"unit": "sqm", "section": "Flooring"},
                   "floor-sticker": {"unit": "sqm", "section": "Graphics"},
                   "glass-rail": {"unit": "m", "section": "Structure"}}
        with mock.patch.object(server, "raw_draft_catalog_entries", return_value=catalog):
            completed = server.complete_predicted_quantities(copy.deepcopy(parsed), {}, None)
        lines = {line["id"]: line for line in completed["quote_basis_sections"][0]["lines"]}
        self.assertEqual((lines["carpet"]["quantity"], lines["carpet"]["unit"]), (72, "sqm"))
        self.assertEqual((lines["sticker"]["quantity"], lines["sticker"]["unit"]), (1, "sqm"))
        self.assertEqual((lines["rail"]["quantity"], lines["rail"]["unit"]), (1, "m"))
        self.assertEqual((lines["misc"]["quantity"], lines["misc"]["unit"]), (1, "lot"))
        rows = {row["source_basis_line_id"]: row for row in completed["line_items"]}
        self.assertEqual(set(rows), {"carpet", "sticker", "rail", "misc"})
        self.assertEqual(rows["misc"]["pricing_keyword"], "")
        self.assertEqual((rows["carpet"]["quantity"], rows["carpet"]["unit"], rows["carpet"]["pricing_keyword"]),
                         (72, "sqm", "grey-carpet"))
        self.assertEqual(rows["carpet"]["section"], "Booth")

    def test_booth_area_is_the_shared_base_for_predicted_quantities(self):
        # One key carries the booth area, so later sources (#385 known details) fill it in one place.
        self.assertEqual(server.booth_dimensions_from_payload({"project": {"booth_width": 8, "booth_depth": 9}})["booth_area_sqm"], 72)
        self.assertEqual(server.booth_dimensions_from_payload({"project": {"booth_size": "9m x 10.5m"}})["booth_area_sqm"], 94.5)
        self.assertGreater(server.booth_dimensions_from_payload({})["booth_area_sqm"], 0)

    def test_page_note_budget_tracks_result_limit_and_actual_batch_size(self):
        for limit in (server.MAX_DRAFT_BATCH_RESULT_CHARS, server.MAX_DRAFT_BATCH_RESULT_CHARS // 2):
            with self.subTest(limit=limit), mock.patch.object(server, "MAX_DRAFT_BATCH_RESULT_CHARS", limit):
                _, calls = self.analyse(13)
                page_counts = [len(body["input"][0]["content"]) - 1 for body in calls[:-1]]
                self.assertEqual(sorted(page_counts), [1, server.MAX_DRAFT_BATCH_MEDIA, server.MAX_DRAFT_BATCH_MEDIA])
                for body, pages in zip(calls[:-1], page_counts):
                    expected_budget = limit * 4 // (5 * pages)
                    prompt = body["input"][0]["content"][0]["text"]
                    self.assertIn(f"Keep each observation within {expected_budget} characters.", prompt)
                    self.assertLess(expected_budget * pages, limit)
                    self.assertLess(len(json.dumps({"observations": ["x" * expected_budget] * pages})), limit)

    def test_compact_page_notes_keep_quantity_unit_object_and_location(self):
        _, calls = self.analyse(13)
        for body in calls[:-1]:
            prompt = body["input"][0]["content"][0]["text"]
            self.assertIn("object at location: value unit", prompt)
            self.assertIn("measurements first, without repeated prose", prompt)
            self.assertIn("Retain explicit quantities and units with their object and location", prompt)
            self.assertIn("visible counts, areas, lengths and dimension labels", prompt)

    def test_real_quantity_admission_and_normalisation_matrix(self):
        for unit in ("sqm", "m", "nos"):
            for quantity in ("6m x 6m", "36 sqm", "36", 36, None, "", "25%", 0, -1):
                with self.subTest(quantity=quantity, unit=unit):
                    line = {"tag": "Confirm", "text": "Synthetic panel", "confidence_pct": 25,
                            "quantity": quantity, "unit": unit}
                    original = copy.deepcopy(line)
                    normalized = server.normalize_basis_line(line)
                    self.assertEqual(normalized.get("quantity"), str(quantity) if quantity not in (None, "", 0) else None)
                    self.assertEqual(normalized["unit"], unit)
                    self.assertEqual(server.canonical_basis_section_line(line)["quantity"], quantity)
                    self.assertEqual(line, original)
                    if quantity in ("6m x 6m", "36 sqm", "25%", 0, -1):
                        with self.assertRaises(server.OpenAIAnalysisError):
                            server.provider_row_measurement(line, line["text"])
                    else:
                        number, admitted_unit = server.provider_row_measurement(line, line["text"])
                        self.assertEqual(number, 36 if quantity in ("36", 36) else None)
                        self.assertEqual(admitted_unit, unit)

    def test_only_explicit_leading_measurements_are_extracted_from_text(self):
        for text, expected in (("36 sqm Floor panel", 36),
                               ("6m x 6m pavilion footprint", None),
                               ("Design Concept 2 8m x 9m booth footprint, 72sqm floor area and 6m maximum overall height; design selection for approval", None)):
            with self.subTest(text=text):
                normalized = server.normalize_basis_line({"text": text, "quantity": None, "unit": ""})
                self.assertEqual(normalized.get("quantity"), expected)
                if expected is not None:
                    self.assertEqual(normalized["unit"], "sqm")


if __name__ == "__main__":
    unittest.main()
