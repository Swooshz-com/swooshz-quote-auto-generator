import copy
import json
import sys
import unittest
from unittest import mock
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_quote as quote  # noqa: E402
import quote_template as qt  # noqa: E402

NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
BUNDLED_LAYOUT = ROOT / "templates" / "quote-layout" / "quotation-layout.xlsx"
STANDARD_RAW = json.loads(qt.STANDARD_TEMPLATE_PATH.read_text(encoding="utf-8"))


def block(definition, block_type):
    return next(item for item in definition["blocks"] if item["type"] == block_type)


def mutated(change):
    definition = copy.deepcopy(STANDARD_RAW)
    change(definition)
    return definition


class StandardTemplateTest(unittest.TestCase):
    def test_standard_reads_and_normalises_idempotently(self):
        standard = qt.load_standard_quote_template()
        self.assertEqual(standard["schema"], "sqag.quote-template.v1")
        self.assertEqual(standard["name"], "Standard")
        self.assertEqual(qt.read_quote_template(standard), standard)
        self.assertEqual(
            qt.quote_template_fingerprint(standard),
            qt.quote_template_fingerprint(STANDARD_RAW),
        )
        self.assertRegex(qt.quote_template_fingerprint(standard), r"^[0-9a-f]{64}$")

    def test_fingerprint_changes_with_meaning_not_with_key_order_or_defaults(self):
        reordered = json.loads(json.dumps(STANDARD_RAW), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
        self.assertEqual(qt.quote_template_fingerprint(reordered), qt.quote_template_fingerprint(STANDARD_RAW))
        explicit = mutated(lambda d: block(d, "terms").update({"beside": False}))
        self.assertEqual(qt.quote_template_fingerprint(explicit), qt.quote_template_fingerprint(STANDARD_RAW))
        renamed = mutated(lambda d: block(d, "item_table")["columns"][2].update({"label": "Description"}))
        self.assertNotEqual(qt.quote_template_fingerprint(renamed), qt.quote_template_fingerprint(STANDARD_RAW))

    def test_standard_matches_the_bundled_quotation_layout(self):
        standard = qt.load_standard_quote_template()
        with zipfile.ZipFile(BUNDLED_LAYOUT) as layout:
            sheet = ET.fromstring(layout.read("xl/worksheets/sheet1.xml"))
            workbook = layout.read("xl/workbook.xml").decode("utf-8")
            rules_xml = layout.read("customXml/sqag-layout-rules.xml").decode("utf-8")
            styles = ET.fromstring(layout.read("xl/styles.xml"))
        bundled_columns = {}
        for col in sheet.find(f"{NS_MAIN}cols"):
            for number in range(int(col.get("min")), int(col.get("max")) + 1):
                bundled_columns[number] = (float(col.get("width")), col.get("hidden") == "1")
        print_area_columns = range(1, quote.col_to_index("I") + 2)
        self.assertEqual(
            [(column["width"], column["hidden"]) for column in standard["grid"]["columns"]],
            [bundled_columns[number] for number in print_area_columns],
        )
        self.assertIn("Quotation!$A$1:$I$", workbook)

        margins = sheet.find(f"{NS_MAIN}pageMargins")
        for key, value in standard["page"]["margins_mm"].items():
            self.assertAlmostEqual(value, float(margins.get(key)) * 25.4, delta=0.05, msg=key)
        page_setup = sheet.find(f"{NS_MAIN}pageSetup")
        self.assertEqual(page_setup.get("paperSize"), "9")  # A4
        self.assertEqual(standard["page"]["orientation"], page_setup.get("orientation"))
        self.assertEqual(standard["page"]["first_page_rows"], quote.FIRST_PRINT_PAGE_END_ROW)
        self.assertEqual(standard["page"]["page_rows"], quote.CONTINUATION_PAGE_HEIGHT)
        self.assertEqual(standard["page"]["row_height_pt"], float(quote.QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT))
        self.assertEqual(
            standard["page"]["first_page_rows"] + 1 + standard["page"]["continuation_top_rows"],
            quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_TABLE_HEADER_OFFSET,
        )

        rules = json.loads(rules_xml.split("<json>", 1)[1].split("</json>", 1)[0])
        self.assertIs(
            block(standard, "logo")["logo"]["repeat"],
            rules["header"]["company_block"]["repeat_logo_on_continuation_pages"],
        )
        table = block(standard, "item_table")
        self.assertEqual([column["label"] for column in table["columns"]], rules["line_items"]["columns"])
        self.assertEqual(standard["labels"]["total"], rules["totals"]["labels"][0])
        exclusions = block(standard, "free_text")
        self.assertEqual(exclusions["heading"], quote.QUOTE_EXCLUSIONS_HEADING)
        self.assertEqual(tuple(exclusions["lines"]), quote.QUOTE_EXCLUSIONS)
        self.assertEqual(exclusions["style"]["color"], "#" + quote.QUOTE_EXCLUSIONS_COLOR[2:])

        body_style = styles.find(f"{NS_MAIN}cellXfs")[5]
        body_font = styles.find(f"{NS_MAIN}fonts")[int(body_style.get("fontId"))]
        self.assertEqual(standard["theme"]["font"], body_font.find(f"{NS_MAIN}name").get("val"))
        self.assertEqual(standard["theme"]["size_pt"], float(body_font.find(f"{NS_MAIN}sz").get("val")))


class HostileDefinitionTest(unittest.TestCase):
    def assert_refused(self, definition, path_prefix):
        with self.assertRaises(qt.QuoteTemplateError) as caught:
            qt.read_quote_template(definition)
        self.assertTrue(
            caught.exception.path.startswith(path_prefix),
            f"refused at {caught.exception.path!r} ({caught.exception.message}), expected {path_prefix!r}",
        )

    def test_unknown_keys_are_refused_at_every_level(self):
        cases = {
            "definition.extra": lambda d: d.update({"extra": 1}),
            "page.zoom": lambda d: d["page"].update({"zoom": 2}),
            "page.margins_mm.gutter": lambda d: d["page"]["margins_mm"].update({"gutter": 1}),
            "header.sticky": lambda d: d["header"].update({"sticky": True}),
            "grid.columns[0].style": lambda d: d["grid"]["columns"][0].update({"style": "5"}),
            "theme.background": lambda d: d["theme"].update({"background": "#FFFFFF"}),
            "labels.tax": lambda d: d["labels"].update({"tax": "VAT"}),
            "blocks[0].formula": lambda d: d["blocks"][0].update({"formula": "=1+1"}),
            "blocks[0].logo.url": lambda d: d["blocks"][0]["logo"].update({"url": "https://example.test/logo.png"}),
            "blocks[4].columns[0].formula": lambda d: block(d, "item_table")["columns"][0].update({"formula": "=A1"}),
            "blocks[7].style.underline": lambda d: block(d, "terms")["style"].update({"underline": True}),
            "blocks[0].beside": lambda d: d["blocks"][0].update({"beside": True}),
            "blocks[4].row": lambda d: block(d, "item_table").update({"row": 3}),
        }
        for path, change in cases.items():
            with self.subTest(path=path):
                self.assert_refused(mutated(change), path)

    def test_wrong_types_and_out_of_range_numbers_are_refused(self):
        cases = {
            "page.first_page_rows": lambda d: d["page"].update({"first_page_rows": True}),
            "page.page_rows": lambda d: d["page"].update({"page_rows": 61.0}),
            "page.row_height_pt": lambda d: d["page"].update({"row_height_pt": "18.7"}),
            "page.margins_mm.left": lambda d: d["page"]["margins_mm"].update({"left": 1e308}),
            "page.margins_mm.top": lambda d: d["page"]["margins_mm"].update({"top": -5}),
            "page.margins_mm.header": lambda d: d["page"]["margins_mm"].update({"header": 30}),
            "page.size": lambda d: d["page"].update({"size": "Letter"}),
            "page.orientation": lambda d: d["page"].update({"orientation": "Portrait"}),
            "grid.columns[2].width": lambda d: d["grid"]["columns"][2].update({"width": 600}),
            "grid.columns[1].hidden": lambda d: d["grid"]["columns"][1].update({"hidden": 1}),
            "grid.columns": lambda d: d["grid"].update({"columns": d["grid"]["columns"][:3]}),
            "theme.size_pt": lambda d: d["theme"].update({"size_pt": 72}),
            "header.rows": lambda d: d["header"].update({"rows": 0}),
            "header.repeat_rows": lambda d: d["header"].update({"repeat_rows": 2}),
            "blocks[0].logo.repeat": lambda d: d["blocks"][0]["logo"].update({"repeat": "yes"}),
            "blocks[9].line_chars": lambda d: block(d, "signature").update({"line_chars": 200}),
            "blocks[10].line_chars": lambda d: block(d, "acceptance").pop("line_chars"),
            "blocks[0].col": lambda d: d["blocks"][0].update({"col": 10}),
            "blocks[2].span": lambda d: block(d, "client_details").update({"col": 2, "span": 9}),
            "blocks[3].rows": lambda d: block(d, "quote_details").update({"rows": 5}),
            "blocks[0].logo.width_mm": lambda d: d["blocks"][0]["logo"].update({"width_mm": 0}),
            "blocks[5].gap_before": lambda d: block(d, "totals").update({"gap_before": 50}),
            "blocks": lambda d: d.update({"blocks": "item_table"}),
            "name": lambda d: d.update({"name": ""}),
            "schema": lambda d: d.update({"schema": "sqag.quote-template.v2"}),
        }
        for path, change in cases.items():
            with self.subTest(path=path):
                self.assert_refused(mutated(change), path)

    def test_bad_colours_and_fonts_are_refused(self):
        for colour in ("red", "#FFF", "#GGGGGG", "#FF000000", "FF0000", " #FF0000", "#FF0000\n"):
            with self.subTest(colour=colour):
                self.assert_refused(mutated(lambda d: d["theme"].update({"color": colour})), "theme.color")
        for font in ("Comic Sans MS", "calibri", "Calibri ", "", "../../fonts/x.ttf"):
            with self.subTest(font=font):
                self.assert_refused(mutated(lambda d: block(d, "terms")["style"].update({"font": font})), "blocks[7].style.font")

    def test_hidden_and_control_characters_in_text_are_refused(self):
        for character in ("\u202e", "\u2066", "\u200b", "\ufeff", "\u0000", "\n", "\r", "\t", "\u2028", "\u2029", "\ue000", "\u0085"):
            with self.subTest(code_point=f"U+{ord(character):04X}"):
                label = f"Pos{character}."
                self.assert_refused(
                    mutated(lambda d: block(d, "item_table")["columns"][0].update({"label": label})),
                    "blocks[4].columns[0].label",
                )
                self.assert_refused(mutated(lambda d: d.update({"name": "Std" + character})), "name")

    def test_formula_like_text_is_kept_as_plain_text(self):
        for text in ("=SUM(A1:A9)", "+1", "-1+cmd|' /C calc'!A0", "@SUM(1)"):
            with self.subTest(text=text):
                definition = qt.read_quote_template(mutated(lambda d: block(d, "free_text")["lines"].__setitem__(0, text)))
                self.assertEqual(block(definition, "free_text")["lines"][0], "'" + text)
                self.assertEqual(qt.read_quote_template(definition), definition)
        definition = qt.read_quote_template(mutated(lambda d: d["labels"].update({"total": "=1+1"})))
        self.assertEqual(definition["labels"]["total"], "'=1+1")
        plain = qt.read_quote_template(mutated(lambda d: d["labels"].update({"total": "  Total (SGD)  "})))
        self.assertEqual(plain["labels"]["total"], "Total (SGD)")
        self.assert_refused(mutated(lambda d: d["labels"].update({"total": "=" + "x" * 39})), "labels.total")

    def test_required_blocks_and_layout_rules_are_enforced(self):
        def drop(block_type):
            return lambda d: d.update({"blocks": [b for b in d["blocks"] if b["type"] != block_type]})

        def table_columns(change):
            return lambda d: change(block(d, "item_table")["columns"])

        cases = {
            "missing item table": (drop("item_table"), "blocks"),
            "missing totals": (drop("totals"), "blocks"),
            "no description column": (table_columns(lambda c: c.pop(2)), "blocks[4].columns"),
            "no amount column": (table_columns(lambda c: c.pop(3)), "blocks[4].columns"),
            "duplicate column key": (table_columns(lambda c: c[1].update({"key": "position"})), "blocks[4].columns[1].key"),
            "overlapping columns": (table_columns(lambda c: c[1].update({"col": 3})), "blocks[4].columns[2].col"),
            "column outside table": (table_columns(lambda c: c[3].update({"col": 6})), "blocks[4].columns[3].col"),
            "column on hidden grid column": (
                lambda d: (block(d, "item_table").update({"span": 7}), block(d, "item_table")["columns"][3].update({"col": 7}),
                           block(d, "totals").update({"col": 4, "span": 4})),
                "blocks",
            ),
            "totals before table": (
                lambda d: d["blocks"].insert(4, d["blocks"].pop(5)), "blocks",
            ),
            "totals miss amount column": (lambda d: block(d, "totals").update({"col": 6, "span": 1, "label_col": 6, "currency_col": 0}), "blocks"),
            "totals label on amount column": (lambda d: block(d, "totals").update({"label_col": 5}), "blocks"),
            "totals label and currency share a column": (lambda d: block(d, "totals").update({"currency_col": 4}), "blocks"),
            "totals currency on second amount cell": (
                lambda d: (block(d, "item_table").update({"span": 6}), block(d, "item_table")["columns"][3].update({"span": 2})),
                "blocks",
            ),
            "body block wholly on hidden columns": (lambda d: d["blocks"].append({
                "type": "free_text", "area": "body", "col": 7, "span": 2, "heading": "Hidden", "lines": [],
            }), "blocks[11]"),
            "header block wholly on hidden columns": (lambda d: block(d, "logo").update({"col": 8}), "blocks[0]"),
            "header blocks overlap": (lambda d: block(d, "quote_details").update({"row": 12}), "blocks[3]"),
            "header type in body": (lambda d: block(d, "logo").update({"area": "body"}), "blocks[0].area"),
            "body type in header": (lambda d: block(d, "terms").update({"area": "header", "row": 1, "rows": 1}), "blocks[7].area"),
            "first body block beside": (lambda d: block(d, "item_table").update({"beside": True}), "blocks[4].beside"),
            "beside overlaps": (lambda d: block(d, "acceptance").update({"col": 4}), "blocks[10]"),
            "beside the totals": (lambda d: block(d, "free_text").update({"beside": True}), "blocks[6].beside"),
            "two logos": (lambda d: d["blocks"].append(copy.deepcopy(d["blocks"][0])), "blocks"),
            "too many free text": (lambda d: d["blocks"].extend(copy.deepcopy(block(d, "free_text")) for _ in range(10)), "blocks"),
            "too many blocks": (lambda d: d["blocks"].extend([copy.deepcopy(block(d, "free_text"))] * 20), "blocks"),
            "empty free text": (lambda d: block(d, "free_text").update({"heading": "", "lines": [" "]}), "blocks[6]"),
            "header free text overflows rows": (lambda d: d["blocks"].append({
                "type": "free_text", "area": "header", "row": 1, "rows": 1, "col": 1, "span": 3,
                "heading": "Quotation", "lines": ["Valid for 30 days"],
            }), "blocks[11].rows"),
            "terms with one column": (lambda d: block(d, "terms").update({"span": 1}), "blocks[7].span"),
            "client fields overflow rows": (lambda d: block(d, "client_details").update({"address_rows": 5}), "blocks[2].rows"),
            "quote fields overflow rows": (lambda d: block(d, "quote_details")["fields"].append("project_number"), "blocks[3].rows"),
            "indent fills block": (lambda d: block(d, "client_details").update({"indent_cols": 3}), "blocks[2].indent_cols"),
            "only blank fields": (lambda d: block(d, "quote_details").update({"fields": ["blank"]}), "blocks[3].fields"),
            "unknown field": (lambda d: block(d, "quote_details").update({"fields": ["customer_bank_account"]}), "blocks[3].fields[0]"),
            "header crowds first page": (lambda d: (d["header"].update({"rows": 40}), d["page"].update({"first_page_rows": 40})), "header.rows"),
            "every column hidden": (lambda d: [c.update({"hidden": True}) for c in d["grid"]["columns"]], "grid.columns"),
        }
        for name, (change, path) in cases.items():
            with self.subTest(case=name):
                self.assert_refused(mutated(change), path)

    def test_hostile_json_text_is_refused(self):
        standard_text = json.dumps(STANDARD_RAW)
        cases = {
            "duplicate key": standard_text.replace('"name": "Standard"', '"name": "Standard", "name": "Other"'),
            "NaN": standard_text.replace('"row_height_pt": 18.7', '"row_height_pt": NaN'),
            "Infinity": standard_text.replace('"row_height_pt": 18.7', '"row_height_pt": Infinity'),
            "byte order mark": "\ufeff" + standard_text,
            "trailing garbage": standard_text + "]",
            "not an object": "[]",
            "deep nesting": '{"schema": ' + "[" * 30000 + "]" * 30000 + "}",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                self.assert_refused(text, "definition")
        self.assert_refused(standard_text.encode("utf-16"), "definition")
        self.assert_refused(b"\xff\xfe\x00", "definition")
        self.assertEqual(qt.read_quote_template(standard_text.encode("utf-8"))["name"], "Standard")

    def test_parsed_objects_must_hold_only_json_values(self):
        for name, change in {
            "set": lambda d: d["theme"].update({"size_pt": {13}}),
            "NaN float": lambda d: d["theme"].update({"size_pt": float("nan")}),
            "object": lambda d: d.update({"name": object()}),
        }.items():
            with self.subTest(case=name):
                self.assert_refused(mutated(change), "definition")
        source = mutated(lambda d: None)
        qt.read_quote_template(source)
        self.assertEqual(source, STANDARD_RAW)

    def test_definitions_over_64_kib_are_refused_only_for_their_size(self):
        def fill(definition):
            for _ in range(9):
                definition["blocks"].append({
                    "type": "free_text", "area": "body", "col": 1, "span": 9,
                    "heading": "Notes", "lines": ["\u20ac" * 200] * 20,
                })

        oversized = mutated(fill)
        text = json.dumps(oversized, ensure_ascii=False)
        self.assertGreater(len(text.encode("utf-8")), qt.MAX_DEFINITION_BYTES)
        self.assert_refused(oversized, "definition")
        self.assert_refused(text, "definition")
        self.assert_refused(text.encode("utf-8"), "definition")
        # Without the cap the same definition is valid, so the cap is what refuses it.
        with mock.patch.object(qt, "MAX_DEFINITION_BYTES", 10 * qt.MAX_DEFINITION_BYTES):
            self.assertEqual(len(qt.read_quote_template(oversized)["blocks"]), 20)
            self.assertEqual(len(qt.read_quote_template(text)["blocks"]), 20)

    def test_huge_numbers_and_lone_surrogates_are_refused_with_a_path(self):
        standard_text = json.dumps(STANDARD_RAW)
        for digits in (400, 5000):
            with self.subTest(digits=digits):
                huge = "1" + "0" * digits
                self.assert_refused(standard_text.replace('"row_height_pt": 18.7', f'"row_height_pt": {huge}'), "definition")
                self.assert_refused(standard_text.replace('"page_rows": 61', f'"page_rows": {huge}'), "definition")
                self.assert_refused(standard_text.replace('"row_height_pt": 18.7', f'"row_height_pt": {huge}.5'), "definition")
        self.assert_refused(standard_text.replace('"row_height_pt": 18.7', '"row_height_pt": 1e400'), "definition")
        self.assert_refused(mutated(lambda d: d["page"].update({"row_height_pt": 10 ** 400})), "page.row_height_pt")
        self.assert_refused(mutated(lambda d: d["page"].update({"row_height_pt": -(10 ** 400)})), "page.row_height_pt")
        self.assert_refused(mutated(lambda d: d.update({"name": "Std\ud800"})), "definition")
        self.assert_refused(mutated(lambda d: d["theme"].update({"\udc00": 1})), "definition")

    def test_lone_surrogates_in_text_and_mixed_unknown_keys_are_refused_with_a_path(self):
        standard_text = json.dumps(STANDARD_RAW)
        # A str that holds a lone surrogate cannot be encoded as UTF-8: refused at the definition, not a UnicodeEncodeError.
        self.assert_refused(standard_text.replace('"Standard"', '"Std\ud800"'), "definition")
        # Python callers may pass int keys; unknown keys of mixed types are refused as unknown, not with a TypeError.
        for extra in ({1: 2, "zoom": 3}, {"zoom": 3, 1: 2}):
            with self.subTest(keys=sorted(map(str, extra))):
                with self.assertRaises(qt.QuoteTemplateError) as caught:
                    qt.read_quote_template(mutated(lambda d: d["page"].update(extra)))
                self.assertTrue(caught.exception.path.startswith("page."), caught.exception.path)
                self.assertEqual(caught.exception.message, "is not a known key")

    def test_reader_names_a_path_for_every_refusal(self):
        error = None
        try:
            qt.read_quote_template({"schema": qt.SCHEMA_ID})
        except qt.QuoteTemplateError as exc:
            error = exc
        self.assertIsNotNone(error)
        self.assertEqual(error.path, "definition.name")
        self.assertIn("is required", str(error))


if __name__ == "__main__":
    unittest.main()
