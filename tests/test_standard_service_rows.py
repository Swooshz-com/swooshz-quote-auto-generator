"""Standard service rows offered on every new Quote Basis (#363), run through the production page code."""

import copy
import json
import os
import subprocess
import unittest
from unittest import mock

from tests import test_webapp as existing
from webapp import server as webapp

PM = "standard-project-management-fee"
WASTE = "standard-waste-disposal"


def line(line_id, text, *, tag="Include", quantity=1, unit="nos"):
    return {"id": line_id, "tag": tag, "text": text, "quantity": quantity, "unit": unit, "confidence": 80}


def row(line_id, section, description, keyword):
    return {
        "section": section, "description": description, "quantity": 1, "unit": "nos",
        "source_basis_line_id": line_id, "pricing_keyword": keyword,
    }


# An analysis result whose suggestions include the same two services in its own words, plus lines that only
# share one word with them and must stay.
FIRST_ANALYSIS = {
    "quote_basis_sections": [
        {"id": "structure", "title": "Booth structure", "lines": [
            line("wall", "Back wall 3m x 2.5m", quantity=7.5, unit="sqm"),
            line("bin-graphic", "Waste bin graphic panel"),
            line("signage-pm", "Project signage panel"),
        ]},
        {"id": "pm", "title": "Project management", "lines": [line("pm-line", "Project management and site supervision")]},
        {"id": "site", "title": "Site services", "lines": [line("cleanup", "Waste disposal and venue clean-up")]},
    ],
    "line_items": [
        row("wall", "Booth structure", "Back wall", "wall"),
        row("pm-line", "Project management", "Project management", "pm"),
        row("cleanup", "Site services", "Waste disposal", "waste"),
    ],
}
SECOND_ANALYSIS = {
    "quote_basis_sections": [
        {"id": "structure", "title": "Booth structure", "lines": [line("wall-2", "Back wall 4m x 2.5m", quantity=10, unit="sqm")]},
        {"id": "services", "title": "Services", "lines": [line("disposal", "Disposal of waste at the venue")]},
    ],
    "line_items": [row("wall-2", "Booth structure", "Back wall", "wall"), row("disposal", "Services", "Disposal", "waste")],
}


class StandardServiceRowsTest(unittest.TestCase):
    def run_steps(self, steps, decisions=None, *, old_draft=False):
        node = existing.require_node(self)
        value = {"steps": steps}
        if old_draft:
            value["decisions"] = None
        elif decisions is not None:
            value["decisions"] = decisions
        completed = subprocess.run(
            [node, "tests/standard_service_rows_harness.cjs"], input=json.dumps(value), cwd=existing.ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        return json.loads(completed.stdout)

    def line_ids(self, step):
        return [entry["id"] for section in step["sections"] for entry in section["lines"]]

    def tags(self, step):
        return {entry["id"]: entry["tag"] for section in step["sections"] for entry in section["lines"]}

    def assert_standard_rows(self, step, pm_tag, waste_tag):
        standard = [section for section in step["sections"] if section["id"] in {PM, WASTE}]
        self.assertEqual([(section["title"], section["lines"]) for section in standard], [
            ("Project management fee", [{"id": PM, "tag": pm_tag, "text": "Lump sum for project management", "quantity": 1, "unit": "nos"}]),
            ("Waste disposal", [{"id": WASTE, "tag": waste_tag, "text": "Waste disposal / Recycling at production venue", "quantity": 1, "unit": "nos"}]),
        ])
        rows = [item for item in step["line_items"] if item["source_basis_line_id"] in {PM, WASTE}]
        self.assertEqual(rows, [
            {"source_basis_line_id": PM, "description": "Lump sum for project management", "quantity": 1, "unit": "nos",
             "price_mode": "Included", "variant": "included", "price": 0},
            {"source_basis_line_id": WASTE, "description": "Waste disposal / Recycling at production venue", "quantity": 1,
             "unit": "nos", "price_mode": "Included", "variant": "included", "price": 0},
        ])
        self.assertTrue(step["basis_agrees"])

    def test_new_quote_offers_both_rows_once_for_review_and_drops_matching_suggestions(self):
        result = self.run_steps([{"adopt": FIRST_ANALYSIS}, {"adopt": FIRST_ANALYSIS}])
        for step in result["steps"]:
            self.assert_standard_rows(step, "Confirm", "Confirm")
            ids = self.line_ids(step)
            # The suggestions offering the same services, and their pricing rows, are gone; lines that only
            # share a word ("waste bin", "project signage") stay.
            self.assertEqual(ids, ["wall", "bin-graphic", "signage-pm", PM, WASTE])
            self.assertEqual(
                [item["source_basis_line_id"] for item in step["line_items"]], ["wall", PM, WASTE],
            )
            self.assertEqual(step["saved"], {"standardServiceRows": {PM: "Confirm", WASTE: "Confirm"}})

    def test_tick_and_x_survive_reanalyse_reset_and_reopen(self):
        result = self.run_steps([
            {"adopt": FIRST_ANALYSIS},
            {"tag": {"id": PM, "tag": "Include"}},
            {"tag": {"id": WASTE, "tag": "Exclude"}},
            {"adopt": SECOND_ANALYSIS},
            {"reset": True},
        ])
        ticked, crossed, reanalysed, reset = result["steps"][1:]
        self.assertEqual(self.tags(ticked)[PM], "Include")
        self.assert_standard_rows(crossed, "Include", "Exclude")
        self.assertEqual(crossed["saved"], {"standardServiceRows": {PM: "Include", WASTE: "Exclude"}})
        # Re-Analyse keeps the decisions and drops the new result's own disposal suggestion.
        self.assert_standard_rows(reanalysed, "Include", "Exclude")
        self.assertEqual(self.line_ids(reanalysed), ["wall-2", PM, WASTE])
        self.assert_standard_rows(reset, "Include", "Exclude")
        self.assertEqual(self.line_ids(reset), ["wall-2", PM, WASTE])

        # Reopening a saved quote restores the record, and a later Reset or Re-Analyse still keeps it.
        saved = crossed["saved"]["standardServiceRows"]
        reopened = self.run_steps([{"adopt": SECOND_ANALYSIS}, {"reset": True}], decisions=saved)["steps"]
        for step in reopened:
            self.assert_standard_rows(step, "Include", "Exclude")
            self.assertEqual(step["saved"], {"standardServiceRows": saved})

    def test_draft_started_before_the_change_is_left_as_saved(self):
        result = self.run_steps([{"adopt": FIRST_ANALYSIS}, {"reset": True}], old_draft=True)
        for step in result["steps"]:
            self.assertEqual(self.line_ids(step), ["wall", "bin-graphic", "signage-pm", "pm-line", "cleanup"])
            self.assertEqual(
                [item["source_basis_line_id"] for item in step["line_items"]], ["wall", "pm-line", "cleanup"],
            )
            self.assertIsNone(step["decisions"])
            # Saving it adds no decisions record, so it stays an older draft.
            self.assertEqual(step["saved"], {})

    def test_saved_record_is_read_strictly(self):
        result = self.run_steps([
            {"restore": {PM: "Include", WASTE: "=cmd"}},
            {"restore": ["Include"]},
            {"restore": {}},
        ])
        first, listed, empty = result["steps"]
        self.assertEqual(first["decisions"], {PM: "Include", WASTE: "Confirm"})
        self.assertIsNone(listed["decisions"])
        self.assertEqual(empty["decisions"], {PM: "Confirm", WASTE: "Confirm"})
        self.assertEqual(
            [(entry["id"], entry["title"], entry["text"], entry["quantity"], entry["unit"]) for entry in result["rows"]],
            [
                (PM, "Project management fee", "Lump sum for project management", 1, "nos"),
                (WASTE, "Waste disposal", "Waste disposal / Recycling at production venue", 1, "nos"),
            ],
        )


class StandardServiceRowsServerTest(unittest.TestCase):
    """Confirming the basis keeps a ticked standard row as one Included row at zero; X and unresolved drop it."""

    def payload(self, pm_tag, waste_tag):
        payload = existing.WebappServerTest().sqag_212_payload()
        existing.PricingDraftG3Regression.bind_test_snapshot(payload, webapp.exact_pricing_reference_authority(payload))
        included = lambda line_id, section, text: {
            "section": section, "source_basis_line_id": line_id, "quantity": 1, "unit": "nos", "description": text,
            "pricing_keyword": "", "price_mode": "Included", "display_price": "Included",
        }
        payload["quote_basis_sections"] = [
            {"id": "lighting", "title": "Lighting", "lines": [
                {"id": "light", "tag": "Include", "text": "Wall light", "quantity": 2, "unit": "nos"},
            ]},
            {"id": PM, "title": "Project management fee", "lines": [
                {"id": PM, "tag": pm_tag, "text": "Lump sum for project management", "quantity": 1, "unit": "nos"},
            ]},
            {"id": WASTE, "title": "Waste disposal", "lines": [
                {"id": WASTE, "tag": waste_tag, "text": "Waste disposal / Recycling at production venue", "quantity": 1, "unit": "nos"},
            ]},
        ]
        payload["line_items"] = [
            # A typed price with no catalogue match or accepted proposal still cannot reach the quote.
            {"section": "Lighting", "source_basis_line_id": "light", "quantity": 2, "unit": "nos", "description": "Wall light",
             "pricing_keyword": "", "unit_price_override": 10},
            included(PM, "Project management fee", "Lump sum for project management"),
            included(WASTE, "Waste disposal", "Waste disposal / Recycling at production venue"),
        ]
        return payload

    def reviewed(self, pm_tag, waste_tag):
        with mock.patch.dict(os.environ, {"SQAG_DISABLE_DOTENV": "1"}):
            rows = webapp.normalize_line_items_for_quote_basis_review(copy.deepcopy(self.payload(pm_tag, waste_tag)))
        return [
            {key: row.get(key) for key in ("source_basis_line_id", "description", "quantity", "unit", "price_mode", "approved_quote_amount")}
            | {"variant": (row.get("pricing_authority") or {}).get("variant")}
            for row in rows
        ]

    def expected(self, line_id, text):
        return {"source_basis_line_id": line_id, "description": text, "quantity": 1.0, "unit": "nos",
                "price_mode": "Included", "approved_quote_amount": 0, "variant": "included"}

    def test_ticked_row_is_included_at_zero_and_crossed_or_unresolved_rows_are_left_out(self):
        self.assertEqual(self.reviewed("Include", "Exclude"), [self.expected(PM, "Lump sum for project management")])
        self.assertEqual(
            self.reviewed("Confirm", "Include"),
            [self.expected(WASTE, "Waste disposal / Recycling at production venue")],
        )
        self.assertEqual(self.reviewed("Exclude", "Exclude"), [])


if __name__ == "__main__":
    unittest.main()
