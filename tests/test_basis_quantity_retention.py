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
                  "text": "Design selection for approval; 6m x 6m pavilion footprint"})
    return {"quote_basis_sections": [{"id": "scope", "title": "Scope", "lines": lines}],
            "line_items": items, "project": {"booth_width": 6, "booth_depth": 6}}


class EvidenceTransport:
    """A deterministic channel probe, not a simulation of model compliance."""
    def __init__(self, retain=True):
        self.retain = retain
        self.calls = []
        self.lock = threading.Lock()

    def send(self, encoded, **kwargs):
        body = json.loads(encoded)
        with self.lock:
            self.calls.append(body)
        parts = body["input"][0]["content"]
        text = parts[0]["text"]
        if text.startswith("Inspect each attached visual"):
            note = MEASURED_NOTE if self.retain else "Floor panel, perimeter rail and visitor chair; measurements unavailable."
            result = {"observations": [note] * (len(parts) - 1)}
        elif any(part["type"] == "input_file" for part in parts):
            result = draft()
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
        self.enterContext(mock.patch.object(server, "log_ai_call_attempt"))
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

    def analyse(self, pages, retain=True):
        transport = EvidenceTransport(retain)
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

    def test_lossy_observations_cannot_be_recovered_by_synthesis_or_normalisation(self):
        single, _ = self.analyse(12, retain=False)
        batched, calls = self.analyse(13, retain=False)
        quantities = lambda result: sum(line.get("quantity") not in (None, "")
                                       for section in result["quote_basis_sections"] for line in section["lines"])
        self.assertEqual(quantities(single), 3)
        self.assertEqual(quantities(batched), 0)
        self.assertEqual(len(calls), 4)
        self.assertEqual(batched["line_items"], [])

    def test_batch_prompts_require_object_measurements_and_safe_synthesis(self):
        _, calls = self.analyse(13)
        batches = [body["input"][0]["content"][0]["text"] for body in calls[:-1]]
        for prompt in batches:
            self.assertIn("Retain every explicit quantity and unit", prompt)
            self.assertIn("object and location", prompt)
            self.assertIn("dimensions separate from billable quantities", prompt)
            self.assertIn("Do not invent measurements", prompt)
        synthesis = calls[-1]["input"][0]["content"][0]["text"]
        self.assertIn("Analyze the uploaded reference images", synthesis)
        self.assertIn("Carry supported quantities and units", synthesis)
        self.assertIn("linked line_items", synthesis)
        self.assertIn("Do not replace missing measurements with 1 lot", synthesis)
        observations = json.loads(synthesis[synthesis.index('[{"source":'):])
        self.assertEqual([row["source"] for row in observations],
                         [f"Reference 1, PDF page {page}" for page in range(1, 14)])
        self.assertTrue(all(row["observation"] == MEASURED_NOTE for row in observations))

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
