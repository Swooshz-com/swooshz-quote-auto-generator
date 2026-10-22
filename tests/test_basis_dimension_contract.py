"""Synthetic regressions for preserving structured rows when adding booth reminders."""
import copy
import json
import unittest
from unittest import mock

from tests import test_webapp as existing

webapp = existing.webapp
DEFAULT_DIMENSIONS = {
    "booth_width": 6, "booth_depth": 6, "booth_size": "6m x 6m",
    "dimension_source": "default",
}


class BasisDimensionContractTest(unittest.TestCase):
    def structured_sections(self):
        return [{
            "id": "lighting", "title": "Lighting", "category_order": 4,
            "lines": [{
                "id": "east-row", "source_line_item_id": "east-row",
                "tag": "Confirm", "text": "[ nos wall light ] - East installation",
                "quantity": 2, "unit": "nos", "confidence": 91,
                "pricing_keyword": "arbitrary-wall-luminaire-01",
                "catalog_description": "nos wall light",
                "pricing_reference_description": "nos wall light",
                "category_order": 4, "item_order": 2,
                "pricing_authority": {"variant": "none"},
            }, {
                "id": "west-row", "tag": "Confirm",
                "text": "[ nos wall light ] - West installation",
                "quantity": 9, "unit": "nos", "confidence": 83,
                "pricing_keyword": "arbitrary-wall-luminaire-01",
            }],
        }]

    def test_reminder_addition_preserves_every_existing_field_without_aliasing(self):
        sections = self.structured_sections()
        original = copy.deepcopy(sections)
        result = webapp.quote_basis_sections_with_default_dimension_confirmation(sections, DEFAULT_DIMENSIONS)
        self.assertEqual(result[0], original[0])
        self.assertEqual(sections, original)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["lines"], [{
            "tag": "Confirm",
            "text": webapp.default_booth_dimension_confirmation_line(DEFAULT_DIMENSIONS).removeprefix("Confirm: "),
            "confidence": 50,
        }])
        result[0]["lines"][0]["pricing_authority"]["variant"] = "changed"
        self.assertEqual(sections, original)

    def test_reminder_insertion_preserves_incomplete_ambiguous_and_forged_fields(self):
        # The reminder operation must neither infer identity/measurements nor grant pricing.
        variants = [
            {}, {"id": "duplicate"}, {"id": "duplicate"}, {"id": "row-a", "source_line_item_id": "different"},
            {"quantity": 1, "unit": "lot"}, {"quantity": 7}, {"unit": "sqm"},
            {"pricing_keyword": "forged", "pricing_authority": {"variant": "catalog", "price": 99999}},
        ]
        sections = [{"id": "flooring", "title": "Flooring", "category_order": 2,
                     "lines": [{"tag": "Confirm", "text": "Same synthetic scope", "confidence": 90, **variant}
                               for variant in variants]}]
        original = copy.deepcopy(sections)
        result = webapp.quote_basis_sections_with_default_dimension_confirmation(sections, DEFAULT_DIMENSIONS)
        self.assertEqual(result[0]["lines"][:-1], original[0]["lines"])
        self.assertEqual(result[0]["category_order"], 2)
        self.assertEqual(sections, original)
        self.assertEqual(webapp.quote_basis_sections_with_default_dimension_confirmation(result, DEFAULT_DIMENSIONS), result)

    def test_existing_duplicate_reminders_are_replaced_once_without_rewriting_other_rows(self):
        sections = self.structured_sections()
        original_rows = copy.deepcopy(sections[0]["lines"])
        sections[0]["lines"].insert(1, {
            "id": "dimension-note", "tag": "Include", "text": "Default booth size is 6m x 6m",
            "confidence": 68,
        })
        sections.append({"id": "flooring", "title": "Flooring", "lines": [
            {"tag": "Confirm", "text": "Booth size defaults to 6m x 6m"},
        ]})
        result = webapp.quote_basis_sections_with_default_dimension_confirmation(sections, DEFAULT_DIMENSIONS)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lines"][0], original_rows[0])
        self.assertEqual(result[0]["lines"][2], original_rows[1])
        reminder = result[0]["lines"][1]
        self.assertEqual(reminder["id"], "dimension-note")
        self.assertEqual(reminder["tag"], "Confirm")
        self.assertEqual(reminder["confidence"], 68)
        self.assertEqual(webapp.quote_basis_sections_with_default_dimension_confirmation(result, DEFAULT_DIMENSIONS), result)

    def test_physical_items_mentioning_default_dimensions_are_never_reminders(self):
        lines = []
        for tag in ("Confirm", "Include", "Exclude", "Custom"):
            for metadata in (
                {"quantity": 4, "unit": "sqm"},
                {"pricing_keyword": "synthetic-floor", "catalog_description": "Synthetic floor"},
                {"custom_pricing": True},
                {"pricing_authority": {"variant": "manual", "price": 37}},
            ):
                lines.append({"id": f"row-{len(lines)}", "tag": tag,
                              "text": "Booth size defaults to 6m x 6m for this physical installation",
                              "confidence": 90, **metadata})
        # A bracketed catalogue description is scope even without measurements.
        lines.append({"tag": "Confirm", "text": "[ Synthetic floor ] - default booth size 6m x 6m"})
        sections = [{"id": "flooring", "title": "Flooring", "lines": lines}]
        result = webapp.quote_basis_sections_with_default_dimension_confirmation(sections, DEFAULT_DIMENSIONS)
        self.assertEqual(result[0]["lines"][:-1], lines)
        self.assertEqual(result[0]["lines"][-1]["confidence"], 50)
        self.assertEqual(webapp.quote_basis_sections_with_default_dimension_confirmation(result, DEFAULT_DIMENSIONS), result)

    def test_reminder_preserves_numeric_and_canonical_text_measurements(self):
        for quantity in (4, "4"):
            with self.subTest(quantity=quantity):
                sections = self.structured_sections()
                sections[0]["lines"][0]["quantity"] = quantity
                result = webapp.quote_basis_sections_with_default_dimension_confirmation(sections, DEFAULT_DIMENSIONS)
                self.assertEqual(result[0], sections[0])
                self.assertIs(type(result[0]["lines"][0]["quantity"]), type(quantity))

    def test_known_dimensions_return_an_independent_unchanged_copy(self):
        sections = self.structured_sections()
        result = webapp.quote_basis_sections_with_default_dimension_confirmation(
            sections, {**DEFAULT_DIMENSIONS, "dimension_source": "user"})
        self.assertEqual(result, sections)
        self.assertIsNot(result[0]["lines"][0], sections[0]["lines"][0])

    def finalized_fixture(self, *, default_dimensions=True):
        payload = existing.WebappServerTest().sqag_212_payload()
        reference = webapp.exact_pricing_reference_authority(payload)
        existing.PricingDraftG3Regression.bind_test_snapshot(payload, reference)
        provider = {"project": {"booth_width": 6, "booth_depth": 6},
                    "quote_basis_sections": self.structured_sections(),
                    "line_items": [{
                        "source_basis_line_id": line["id"], "section": "Lighting",
                        "description": "nos wall light", "quantity": line["quantity"], "unit": "nos",
                        "pricing_keyword": line["pricing_keyword"],
                        "unit_price_override": 99999,
                    } for line in self.structured_sections()[0]["lines"]]}
        normalized = webapp.normalize_ai_draft(provider, payload)
        unpacked = webapp.unpack_ai_draft(normalized, payload, provider_result=True)
        dimensions = {**DEFAULT_DIMENSIONS, "dimension_source": "default" if default_dimensions else "user"}
        with mock.patch.object(webapp, "write_local_log"):
            finalized = webapp.finalized_remote_draft_result(payload, normalized, "openai", "OpenAI", dimensions, [])
        return payload, normalized, unpacked, finalized

    def test_full_finalization_preserves_distinct_physical_rows_and_catalogue_only_output(self):
        for default_dimensions in (False, True):
            with self.subTest(default_dimensions=default_dimensions):
                payload, normalized, unpacked, finalized = self.finalized_fixture(default_dimensions=default_dimensions)
                rows = [line for section in finalized["quote_basis_sections"] for line in section["lines"]
                        if line.get("id") in {"east-row", "west-row"}]
                all_items = [line for section in finalized["quote_basis_sections"] for line in section["lines"]
                             if not webapp.line_references_default_booth_dimensions(line["text"], DEFAULT_DIMENSIONS)]
                self.assertEqual(len(all_items), 2)
                self.assertEqual([line["id"] for line in rows], ["east-row", "west-row"])
                self.assertEqual([webapp.parse_float_or_none(line["quantity"]) for line in rows], [2, 9])
                self.assertEqual([line["confidence"] for line in rows], [91, 83])
                self.assertTrue(all(line["pricing_reference_description"] == "nos wall light" for line in rows))
                self.assertIn("East installation", rows[0]["text"])
                self.assertIn("West installation", rows[1]["text"])
                self.assertEqual(len(finalized["line_items"]), 2)
                self.assertEqual(len(normalized["line_items"]), 2)
                self.assertEqual(len(unpacked[1]), 2)
                self.assertEqual([row["source_basis_line_id"] for row in finalized["line_items"]], ["east-row", "west-row"])
                self.assertEqual([webapp.parse_float_or_none(row["quantity"]) for row in finalized["line_items"]], [2, 9])
                self.assertTrue(all(row["pricing_authority"]["price"] == 40 for row in finalized["line_items"]))
                self.assertTrue(all(row["description"] == "nos wall light" for row in finalized["line_items"]))
                # Serialization/reopen and repeated normalization retain the exact row binding.
                restored = json.loads(json.dumps(finalized))
                repeated = webapp.normalize_ai_draft(restored, payload)
                self.assertEqual(repeated["line_items"], finalized["line_items"])
                approved = copy.deepcopy(restored["quote_basis_sections"])
                for section in approved:
                    for line in section["lines"]:
                        if line.get("id") in {"east-row", "west-row"}:
                            line["tag"] = "Include"
                reviewed = webapp.normalize_line_items_for_quote_basis_review({
                    **payload, "quote_basis_sections": approved, "line_items": restored["line_items"],
                    "pricing_analysis_seal": restored.get("pricing_analysis_seal"),
                })
                self.assertEqual(len(reviewed), 2)
                self.assertEqual([row["source_basis_line_id"] for row in reviewed], ["east-row", "west-row"])
                self.assertEqual([row["pricing_basis_amount"] for row in reviewed], [80, 360])

    def test_normal_full_analysis_uses_each_selected_saved_reference_without_changing_metadata(self):
        fixture = existing.WebappServerTest()
        items = fixture.sqag_212_catalog_items()
        with existing.tempfile.TemporaryDirectory() as tmp:
            root = existing.Path(tmp)
            for reference_id in ("synthetic-original", "synthetic-fresh-import"):
                directory = existing.write_test_pricing_reference(root, reference_id, items)
                if reference_id.endswith("fresh-import"):
                    metadata = json.loads((directory / "reference.json").read_text())
                    metadata.update({"currency": "USD", "tax": {"label": "VAT", "rate": 0.10}})
                    (directory / "reference.json").write_text(json.dumps(metadata))
            with (
                mock.patch.object(webapp, "pricing_references_root", return_value=root),
                mock.patch.object(webapp, "bundled_pricing_references_root", return_value=root / "empty"),
                mock.patch.object(webapp, "profiles_root", return_value=existing.QUOTE_GENERATOR_FIXTURE_ROOT / "profiles"),
                mock.patch.object(webapp, "read_dotenv_value", side_effect=lambda key: "synthetic-key" if key == "OPENAI_API_KEY" else existing.synthetic_draft_dotenv_value(key)),
                mock.patch.object(webapp, "write_local_log"),
                mock.patch.object(webapp, "log_ai_call_attempt"),
            ):
                for reference_id, currency in (("synthetic-original", "SGD"), ("synthetic-fresh-import", "USD")):
                    with self.subTest(reference_id=reference_id):
                        payload = fixture.sqag_212_payload()
                        payload.pop("pricing_reference", None)
                        payload["pricing_reference_id"] = reference_id
                        payload["project"].pop("booth_width")
                        payload["project"].pop("booth_depth")
                        provider = {"project": {"booth_width": 6, "booth_depth": 6},
                                    "quote_basis_sections": self.structured_sections(),
                                    "line_items": [{
                                        "source_basis_line_id": line["id"], "section": "Lighting",
                                        "description": "nos wall light", "quantity": line["quantity"],
                                        "unit": "nos", "pricing_keyword": line["pricing_keyword"],
                                    } for line in self.structured_sections()[0]["lines"]]}
                        def provider_response(captured, *_args, **_kwargs):
                            return webapp.normalize_ai_draft(provider, captured)
                        with mock.patch.object(webapp, "request_openai_quote_basis", side_effect=provider_response) as request:
                            result = webapp.draft_quote_basis(payload)
                        self.assertEqual(result["status"], "drafted")
                        self.assertEqual(request.call_count, 1)
                        self.assertEqual(request.call_args.kwargs["draft_configuration"].analysis_mode, "standard")
                        self.assertEqual(result["project"]["dimension_source"], "default")
                        self.assertEqual(result["pricing_reference_basis"]["id"], reference_id)
                        self.assertEqual(result["pricing_reference_basis"]["currency"], currency)
                        rows = result["line_items"]
                        self.assertEqual(len(rows), 2)
                        self.assertEqual([row["source_basis_line_id"] for row in rows], ["east-row", "west-row"])
                        self.assertTrue(all(row["pricing_authority"]["currency"] == currency for row in rows))
                        self.assertTrue(all(row["pricing_authority"]["price"] == 40 for row in rows))
                        self.assertEqual(sum(not webapp.is_informational_dimension_basis_line(line)
                                             for section in result["quote_basis_sections"] for line in section["lines"]), 2)
                        saved = json.loads((root / reference_id / "reference.json").read_text())
                        self.assertEqual(saved["currency"], currency)
                        self.assertEqual(saved["tax"], {"label": "VAT", "rate": 0.10} if currency == "USD" else {"label": "GST", "rate": 0.09})



if __name__ == "__main__":
    unittest.main()
