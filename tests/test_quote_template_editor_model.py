"""The Quote Templates editor model (webapp/static/app.js) against the Python reader it mirrors.

Each scenario makes edits through the page's own model in node, then the edited definition goes to
scripts/quote_template.read_quote_template: the editor must report a problem exactly when the reader
refuses, so staff never save a layout the server turns away, nor see a warning the server would not give.
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts import quote_template

ROOT = Path(__file__).resolve().parents[1]
TABLE = {"type": "item_table"}
TOTALS = {"type": "totals"}


def reader_accepts(definition):
    try:
        quote_template.read_quote_template(definition)
    except quote_template.QuoteTemplateError:
        return False
    return True


@unittest.skipUnless(shutil.which("node"), "node is required for the editor model harness")
class QuoteTemplateEditorModelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.standard = quote_template.load_standard_quote_template()

    def run_scenarios(self, scenarios, definition=None):
        completed = subprocess.run(
            [shutil.which("node"), "tests/quote_template_editor_harness.cjs"],
            input=json.dumps({"definition": definition or self.standard, "scenarios": scenarios}),
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        results = json.loads(completed.stdout)
        for result in results:
            with self.subTest(scenario=result["name"]):
                self.assertTrue(result["keysFollowBlocks"])
                self.assertEqual(not result["problems"], reader_accepts(result["definition"]),
                                 f"model problems {result['problems']} disagree with the reader")
        return {result["name"]: result for result in results}

    def test_standard_has_no_problems_and_the_same_page_fit_as_the_reader(self):
        result = self.run_scenarios([{"name": "untouched", "ops": []}])["untouched"]
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["definition"], self.standard)
        theme, page, grid = self.standard["theme"], self.standard["page"], self.standard["grid"]
        self.assertEqual(result["printScale"], quote_template.print_scale(page, grid, theme))
        self.assertEqual(result["printableRows"], quote_template.printable_rows_per_page(page, grid, theme))

    def test_moving_a_block_changes_only_the_order_below_the_top_section(self):
        results = self.run_scenarios([
            {"name": "terms up twice", "ops": [["moveInOrder", {"type": "terms"}, -1], ["moveInOrder", {"type": "terms"}, -1]]},
            {"name": "first block up", "ops": [["moveInOrder", TABLE, -1]]},
            {"name": "last block down", "ops": [["moveInOrder", {"type": "acceptance"}, 1]]},
            {"name": "header block", "ops": [["moveInOrder", {"type": "logo"}, 1]]},
            {"name": "totals above table", "ops": [["moveInOrder", TOTALS, -1]]},
        ])
        self.assertEqual(results["terms up twice"]["returned"], [True, True])
        self.assertEqual(results["terms up twice"]["bodyOrder"][:4], ["item_table", "terms", "totals", "free_text"])
        self.assertEqual(results["terms up twice"]["problems"], [])
        for name in ("first block up", "last block down", "header block"):
            self.assertEqual(results[name]["returned"], [False], name)
            self.assertEqual(results[name]["definition"], self.standard, name)
        self.assertIn("Totals must come after the item table. Move it down.",
                      [problem["text"] for problem in results["totals above table"]["problems"]])

    def test_adding_and_renaming_a_table_column(self):
        results = self.run_scenarios([
            {"name": "widen then add", "ops": [["setSpan", TABLE, 6], ["addTableColumn", TABLE]]},
            {"name": "add with no room", "ops": [["addTableColumn", TABLE]]},
            {"name": "add every kind", "ops": [["setSpan", TABLE, 9], ["addTableColumn", TABLE], ["addTableColumn", TABLE],
                                               ["addTableColumn", TABLE], ["addTableColumn", TABLE]]},
        ])
        added = results["widen then add"]
        self.assertEqual(added["returned"], [None, ""])
        table = next(block for block in added["definition"]["blocks"] if block["type"] == "item_table")
        self.assertEqual(table["span"], 6)
        new_column = table["columns"][-1]
        self.assertEqual((new_column["key"], new_column["col"], new_column["span"]), ("unit", 6, 1))
        self.assertEqual(results["add with no room"]["returned"],
                         ["No free page column inside the table. Widen the table or remove a column first."])
        self.assertEqual(results["add every kind"]["returned"][-1], "Every kind of column is already in the table.")
        # A renamed heading is plain text; formula-like or hidden characters are refused by both sides.
        renamed = json.loads(json.dumps(added["definition"]))
        next(b for b in renamed["blocks"] if b["type"] == "item_table")["columns"][-1]["label"] = "Units"
        hidden = json.loads(json.dumps(renamed))
        next(b for b in hidden["blocks"] if b["type"] == "item_table")["columns"][-1]["label"] = "Units​"
        self.assertEqual(self.run_scenarios([{"name": "renamed", "ops": []}], renamed)["renamed"]["problems"], [])
        hidden_result = self.run_scenarios([{"name": "hidden", "ops": []}], hidden)["hidden"]
        self.assertEqual([p["text"] for p in hidden_result["problems"]],
                         ["The unit column heading has a hidden character. Retype it."])

    def test_required_columns_cannot_be_removed(self):
        results = self.run_scenarios([
            {"name": "description", "ops": [["removeTableColumn", TABLE, "description"]]},
            {"name": "amount", "ops": [["removeTableColumn", TABLE, "amount"]]},
            {"name": "quantity", "ops": [["removeTableColumn", TABLE, "quantity"]]},
            {"name": "missing", "ops": [["removeTableColumn", TABLE, "unit"]]},
        ])
        for name in ("description", "amount", "missing"):
            self.assertEqual(results[name]["returned"], [False], name)
            self.assertEqual(results[name]["definition"], self.standard, name)
        self.assertEqual(results["quantity"]["returned"], [True])
        table = next(block for block in results["quantity"]["definition"]["blocks"] if block["type"] == "item_table")
        self.assertEqual([column["key"] for column in table["columns"]], ["position", "description", "amount"])

    def test_moving_sideways_carries_table_columns_and_totals_labels(self):
        results = self.run_scenarios([
            {"name": "totals right", "ops": [["setCol", TOTALS, 5]]},
            {"name": "table right", "ops": [["setCol", TABLE, 2]]},
            {"name": "narrow table", "ops": [["setSpan", TABLE, 3]]},
        ])
        totals = next(b for b in results["totals right"]["definition"]["blocks"] if b["type"] == "totals")
        self.assertEqual((totals["col"], totals["label_col"], totals["currency_col"]), (5, 5, 7))
        table = next(b for b in results["table right"]["definition"]["blocks"] if b["type"] == "item_table")
        self.assertEqual([(c["key"], c["col"]) for c in table["columns"]],
                         [("position", 2), ("quantity", 3), ("description", 4), ("amount", 6)])
        narrow = next(b for b in results["narrow table"]["definition"]["blocks"] if b["type"] == "item_table")
        self.assertTrue(all(c["col"] + c["span"] - 1 <= 3 for c in narrow["columns"]))
        # Each of these is either fine or reported; the harness already checked the reader agrees.
        self.assertTrue(results["narrow table"]["problems"])

    def test_page_column_edits_and_their_problems(self):
        results = self.run_scenarios([
            {"name": "narrow description", "ops": [["setColumnWidth", 2, 20]]},
            {"name": "hide every column", "ops": [["setColumnHidden", index, True] for index in range(9)]},
            {"name": "remove used column", "ops": [["removeGridColumn"]]},
            {"name": "add page column", "ops": [["addGridColumn"]]},
            {"name": "hide column I", "ops": [["setColumnHidden", 8, True]]},
        ])
        # A narrower page prints larger, so fewer rows fit than the layout needs.
        self.assertEqual([p["text"] for p in results["narrow description"]["problems"]],
                         ["At this text size and column width the page holds 47 rows, fewer than this layout needs (61). "
                          "Use smaller text or narrower columns."])
        hide = results["hide every column"]["returned"]
        self.assertEqual(hide[-1], False)  # the last printing column cannot be hidden
        self.assertEqual(results["remove used column"]["returned"],
                         ["Column I is used by Logo, Company details, Free text, Terms, Notes, Acceptance. Move them first."])
        self.assertEqual(results["add page column"]["returned"], [""])
        self.assertEqual(len(results["add page column"]["definition"]["grid"]["columns"]), 10)
        self.assertIn("Logo is only on hidden columns, so it would not print. Move it to a column that prints.",
                      [p["text"] for p in results["hide column I"]["problems"]])


if __name__ == "__main__":
    unittest.main()
