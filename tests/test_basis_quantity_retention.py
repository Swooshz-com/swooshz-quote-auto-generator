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
    def __init__(self, retain=True, repair_measures=False, single_measured=True):
        self.retain = retain
        self.repair_measures = repair_measures
        self.single_measured = single_measured
        self.calls = []
        self.lock = threading.Lock()

    def send(self, encoded, **kwargs):
        body = json.loads(encoded)
        with self.lock:
            self.calls.append(body)
        parts = body["input"][0]["content"]
        text = parts[0]["text"]
        if "Quantity repair." in text:
            result = draft(self.repair_measures)
        elif text.startswith("Inspect each attached visual"):
            note = MEASURED_NOTE if self.retain else "Floor panel, perimeter rail and visitor chair; measurements unavailable."
            result = {"observations": [note] * (len(parts) - 1)}
        elif any(part["type"] == "input_file" for part in parts):
            result = draft(self.single_measured)
        else:
            observations = json.loads(text[text.index('[{"source":'):])
            result = draft(any(MEASURED_NOTE in row["observation"] for row in observations))
        return responses_envelope(body["model"], json.dumps(result))


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
        # synthesis without sizes; the one re-ask still gives none, so the booth-size rule fills them.
        batched, calls = self.analyse(13, retain=False)
        self.assertEqual(len(calls), 5)
        self.assertIn("Quantity repair. These physical lines have no usable quantity: floor, rail, chair.",
                      calls[-1]["input"][0]["content"][0]["text"])
        lines = {line["id"]: line for section in batched["quote_basis_sections"] for line in section["lines"]}
        self.assertEqual((float(lines["floor"]["quantity"]), lines["floor"]["unit"]), (36, "sqm"))
        self.assertEqual((float(lines["rail"]["quantity"]), lines["rail"]["unit"]), (1, "lot"))
        self.assertEqual((float(lines["chair"]["quantity"]), lines["chair"]["unit"]), (1, "lot"))
        self.assertTrue(all(lines[identity]["confidence"] <= server.PREDICTED_QUANTITY_RULE_CONFIDENCE
                            for identity in ("floor", "rail", "chair")))
        self.assertNotIn("quantity", lines["concept"])
        self.assertEqual(batched["analysis_findings"][0]["id"], "booth-size-rule")
        self.assertIn("Floor panel = 36 sqm", batched["analysis_findings"][0]["text"])

    def test_one_ai_reask_supplies_missing_sizes_on_both_paths(self):
        batched, calls = self.analyse(13, retain=False, repair_measures=True)
        single, single_calls = self.analyse(12, single_measured=False, repair_measures=True)
        self.assertEqual((len(calls), len(single_calls)), (5, 2))
        for result in (batched, single):
            lines = {line["id"]: line for section in result["quote_basis_sections"] for line in section["lines"]}
            for identity, _, quantity, unit in MEASUREMENTS:
                self.assertEqual((float(lines[identity]["quantity"]), lines[identity]["unit"]), (quantity, unit))
            self.assertFalse(any(finding["id"] == "booth-size-rule" for finding in result["analysis_findings"]))
            self.assertEqual(len(result["line_items"]), 3)
        # The single path re-asks with the same media and catalogue parts, only the prompt text grows.
        first, repair = single_calls
        self.assertEqual(first["input"][0]["content"][1:], repair["input"][0]["content"][1:])
        self.assertTrue(repair["input"][0]["content"][0]["text"].startswith(first["input"][0]["content"][0]["text"]))

    def test_measured_drafts_send_no_reask(self):
        for pages, sends in ((12, 1), (13, 4)):
            with self.subTest(pages=pages):
                _, calls = self.analyse(pages)
                self.assertEqual(len(calls), sends)
                self.assertFalse(any("Quantity repair." in body["input"][0]["content"][0]["text"] for body in calls))

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
        units = {"grey-carpet": "sqm", "spotlight": "nos"}
        with mock.patch.object(server, "raw_draft_catalog_units", return_value=units):
            self.assertEqual([line["id"] for line in server.raw_draft_lines_missing_quantity(parsed, units)], ["carpet"])
            completed = server.complete_predicted_quantities(copy.deepcopy(parsed), {}, None)
        carpet, spot = completed["quote_basis_sections"][0]["lines"]
        self.assertEqual((carpet["quantity"], carpet["unit"], carpet["confidence_pct"]), (72, "sqm", 30))
        self.assertEqual((completed["line_items"][0]["quantity"], completed["line_items"][0]["unit"]), (72, "sqm"))
        self.assertEqual((spot["quantity"], spot["unit"], spot["confidence_pct"]), (1, "lot", 82))
        self.assertIn("Grey Carpet Tile = 72 sqm", completed["analysis_findings"][0]["text"])

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
