"""A pricing import keeps rows without a price for staff to complete, and names the rows it leaves out (#364).

docs/pricing-catalog-import.md: invalid rows remain visible for correction where possible, and Save stays
disabled until required fields are valid. Synthetic data only; the AI answer is a fake, no paid calls.
"""
import base64
import json
import os
import subprocess
import unittest
from unittest import mock

from tests import test_webapp as fixtures
from tests.rendered_browser_support import owned_browser_evidence_directory

webapp = fixtures.webapp
HEADERS = list(webapp.PRICING_REFERENCE_TEMPLATE_COLUMNS)


def row(description="Synthetic display", **extra):
    return {"section": "Synthetic AV", "description": description, "unit_hint": "nos",
            "internal_cost": 100, "markup_multiplier": 1.2, **extra}


def preview(csv, ai_items=None):
    with mock.patch.object(webapp, "read_dotenv_value", side_effect={
            **fixtures.SYNTHETIC_DRAFT_CONFIG, "OPENROUTER_API_KEY": "sk-test-redacted"}.get), \
            mock.patch.object(webapp, "request_openai_pricing_catalog_import", return_value={"items": ai_items or []}) as ai:
        result = webapp.pricing_reference_import_preview({
            "filename": "synthetic.csv",
            "data_url": "data:text/csv;base64," + base64.b64encode(csv.encode("utf-8")).decode("ascii"),
        })
    return result, ai


class PricingImportRowsTest(unittest.TestCase):
    def test_ai_rows_with_a_unit_price_but_no_cost_or_markup_stay_to_complete(self):
        items = [row(name, internal_cost=None, markup_multiplier=None) for name in ("Display", "Speaker", "Light", "Screen")]
        result, ai = preview("Category,Item,Unit,Unit price\nSynthetic AV,Display,nos,120\n", items)
        ai.assert_called_once()
        self.assertEqual([item["description"] for item in result["items"]], ["Display", "Speaker", "Light", "Screen"])
        # No price is made up, and nothing incomplete can be saved.
        for item in result["items"]:
            self.assertEqual((item["internal_cost"], item["markup_multiplier"]), (None, None))
            self.assertNotIn("sale_unit_price", item)
        self.assertFalse(result["canSave"])
        self.assertEqual((result["rowCount"], result["skipped"], result["skippedRows"], result["errors"]), (4, 0, [], []))
        with self.assertRaisesRegex(ValueError, "At least one valid pricing row is required"):
            webapp.normalize_pricing_reference_payload({"id": "synthetic", "label": "Synthetic", "items": result["items"]})

    def test_left_out_rows_are_named_with_their_reason(self):
        result = webapp.validate_pricing_reference_rows([
            row(),
            row("", section="Synthetic Furniture", unit_hint="sets"),
            row("", section="", unit_hint=""),
        ], HEADERS, "synthetic.csv")
        self.assertEqual(result["skippedRows"], [
            {"row": 2, "label": "Synthetic Furniture, sets", "reason": "no description"},
            {"row": 3, "label": "", "reason": "no description"},
        ])
        self.assertEqual(result["skipped"], 2)
        self.assertTrue(result["canSave"])

    def test_rows_are_numbered_as_read_before_continuation_rows_join(self):
        continuation = {"section": "", "description": "continued wording", "unit_hint": "", "internal_cost": "", "markup_multiplier": ""}
        result = webapp.validate_pricing_reference_rows([row(), continuation, row("")], HEADERS, "synthetic.csv")
        self.assertEqual([skipped["row"] for skipped in result["skippedRows"]], [3])
        self.assertIn("continued wording", result["items"][0]["description"])

    def test_rows_over_the_limit_are_named(self):
        with mock.patch.object(webapp, "MAX_PRICING_REFERENCE_ROWS", 2):
            result = webapp.validate_pricing_reference_rows([row(f"Item {index}") for index in range(4)], HEADERS, "synthetic.csv")
        self.assertEqual([(skipped["row"], skipped["reason"]) for skipped in result["skippedRows"]],
                         [(3, "over the limit of 2 rows"), (4, "over the limit of 2 rows")])
        self.assertEqual(result["rowCount"], 2)

    def test_a_template_with_some_unpriced_rows_keeps_them_without_an_ai_call(self):
        csv = ("section,description,unit_hint,internal_cost,markup_multiplier\n"
               "Synthetic AV,Display,nos,100,1.2\nSynthetic AV,Speaker,nos,,\nSynthetic AV,Light,nos,POA,1.5\n")
        result, ai = preview(csv)
        ai.assert_not_called()
        self.assertEqual([item.get("sale_unit_price") for item in result["items"]], [120, None, None])
        self.assertEqual(result["unpricedRowCount"], 2)
        self.assertFalse(result["canSave"])

    def test_a_file_whose_rows_are_all_unpriced_still_goes_to_the_ai_reader(self):
        csv = "section,description,unit_hint,internal_cost,markup_multiplier\nSynthetic AV,Display,nos,,\n"
        result, ai = preview(csv, [row("Display")])
        ai.assert_called_once()
        self.assertEqual(result["items"][0]["sale_unit_price"], 120)

    def test_saving_still_takes_only_priced_rows(self):
        self.assertIsNone(webapp.sanitize_pricing_reference_item(row(internal_cost=None)))
        self.assertIsNone(webapp.sanitize_pricing_reference_item(row(markup_multiplier=0)))

    def test_rendered_import_names_left_out_rows_and_keeps_the_rest(self):
        with owned_browser_evidence_directory() as evidence_root:
            result = subprocess.run(
                [fixtures.require_node(self), 'scripts/playwright-pricing-import-rows.mjs'],
                env={**os.environ, 'SQAG_OWNER_UAT_EVIDENCE_ROOT': evidence_root},
                text=True, capture_output=True, cwd=fixtures.ROOT, timeout=180,
            )
        self.assertEqual(result.returncode, 0, (result.stderr or result.stdout)[-4000:])
        evidence = json.loads(result.stdout[result.stdout.index('{'):])
        self.assertEqual(evidence['status'], 'PASS')
        self.assertIn('Row 4 (Synthetic Furniture, sets) left out: no description.', evidence['attention'])
        self.assertEqual(evidence['afterDuplicate'], ['OK', 'OK'])


if __name__ == '__main__':
    unittest.main()
