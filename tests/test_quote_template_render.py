"""Quote templates lay out the Excel quote (#167).

The Standard template must reproduce today's bundled layout, and a changed template must move,
rename, restyle or drop exactly what it says. All data here is synthetic.
"""
import base64
import copy
import json
import os
import re
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_quote as quote  # noqa: E402
import quote_template as qt  # noqa: E402

M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XDR = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
EMU_PER_MM = 36_000
BUNDLED_LAYOUT = ROOT / "templates" / "quote-layout" / "quotation-layout.xlsx"
# The bundled workbook sets 18.7 pt on rows 1 to 89 and nothing else of that height. Rows after 89
# keep stray heights (the sheet default 17 pt, 17.35 pt, 17.7 pt) that no layout asks for, so Standard
# keeps its designed 18.7 pt there. That is the one accepted row height difference (docs/quote-template-schema.md).
BUNDLED_DESIGNED_HEIGHT_PT = 18.7
BUNDLED_DESIGNED_ROWS_END = 89
BUNDLED_STRAY_HEIGHTS_PT = {17.0, 17.35, 17.7}
# The bundled text box offsets are stored in EMU to the nearest value; 720 EMU is 0.02 mm.
TEXT_BOX_TOLERANCE_EMU = 720
FIXTURE_CATALOG = (
    ROOT / "tests" / "fixtures" / "quote-generator" / "pricing-references"
    / "synthetic-exhibition-fixture-pricing" / "pricing-catalog.json"
)
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
STANDARD_RAW = json.loads(qt.STANDARD_TEMPLATE_PATH.read_text(encoding="utf-8"))


def synthetic_brief(**updates):
    data = {
        "quote_date": "2026-06-06",
        "project_number": "SYN-001",
        "client": {"name": "Synthetic Client", "attention": "Sam Example", "title": "Manager",
                   "address": ["1 Example Road", "Unit 2", "Example City", "000000", "Extra line"]},
        "project": {"title": "Synthetic Booth"},
        "currency": "SGD",
        "tax": {"label": "GST", "rate": 0.09},
        "company": {"name": "Example Quotes Co", "header_lines": ["Example Quotes Co", "1 Example Way"],
                    "logo_data_url": "data:image/png;base64," + base64.b64encode(PNG).decode()},
        "acceptance": {"company_name": "Example Quotes Co", "text": "We accept the quotation", "person_label": "Person",
                       "stamp_label": "Stamp", "date_label": "Date:"},
        "signature": {"company_signatory": "Alex Example", "company_title": "Director", "company_date_label": "Date:"},
        "terms_heading": "Terms", "payment_terms": ["50% deposit", "Balance on completion"],
        "notes_heading": "Notes", "standard_notes": ["Prices valid for 30 days"],
        "line_items": [],
    }
    data.update(updates)
    return data


def synthetic_lines(count, sections=1, description="Printed panel with lamination ", status="matched"):
    price = quote.PriceRow(1, "Graphics", "Panel", "sqm", 120, 1, 1, "")
    return [
        quote.QuoteLine(
            section=f"Section {1 + (index - 1) * sections // count}", quantity=2.5 if index % 3 else 4, unit="sqm",
            description=description * (1 + index % 4), pricing_keyword="Panel", display_price="", matched_price=price,
            amount=300.0 + index, match_status=status, match_candidates=[],
        )
        for index in range(1, count + 1)
    ]


EQUIVALENCE_CASES = {
    "short": (synthetic_brief(), synthetic_lines(3)),
    "multi_page_sections": (synthetic_brief(), synthetic_lines(45, sections=45)),
    "long_text": (synthetic_brief(
        payment_terms=[f"Term {i}: " + "long payment wording " * 12 for i in range(6)],
        standard_notes=[f"Note {i}: " + "standard note wording " * 15 for i in range(8)],
        rich_text={"termsHeading": "<div><strong>Payment Terms</strong></div>",
                   "standardNotes": "<div><em>Italic</em> note</div>"},
    ), synthetic_lines(14, sections=3)),
    "grouped": (synthetic_brief(section_pricing={"Section 1": "section_total"}), synthetic_lines(8, sections=2)),
    "deferred": (synthetic_brief(), synthetic_lines(6, status="deferred-pricing")),
    "no_terms_no_logo": (synthetic_brief(
        terms_heading="", payment_terms=[], notes_heading="", standard_notes=[],
        company={"name": "Example Quotes Co", "header_lines": ["Example"]}, project_number="",
    ), synthetic_lines(25, sections=5)),
    "formula_text": (synthetic_brief(
        client={"name": "=HYPERLINK(\"x\")", "attention": "+1", "title": "-2", "address": ["@SUM(1)"]},
        project={"title": "=1+1"}, payment_terms=["=cmd|' /C calc'!A0"],
    ), synthetic_lines(4, description="=SUM(A1) ")),
    "long_acceptance": (synthetic_brief(acceptance={
        "company_name": "Example Quotes Co", "text": "We accept " * 40,
        "person_label": "Person", "stamp_label": "Stamp", "date_label": "Date:",
    }), synthetic_lines(20, sections=4)),
}


def workbook_summary(path, grid_columns=None):
    """What a reader sees: cell payloads, number formats, merges, columns, print area and pages."""
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        workbook = archive.read("xl/workbook.xml").decode("utf-8")
    formats = {node.get("numFmtId"): node.get("formatCode") for node in styles.iter(f"{M}numFmt")}
    formats.update({"0": "General", "2": "0.00", "4": "#,##0.00"})
    xfs = [xf.get("numFmtId") for xf in styles.find(f"{M}cellXfs")]
    cells = {}
    for cell in sheet.iter(f"{M}c"):
        formula = cell.find(f"{M}f")
        value = cell.find(f"{M}v")
        text = "".join(node.text or "" for node in cell.iter(f"{M}t"))
        if formula is not None:
            payload = ("formula", formula.text)
        elif cell.get("t") == "inlineStr" and text:
            payload = ("text", text)
        elif value is not None and cell.get("t") is None:
            payload = ("number", float(value.text))
        else:
            continue
        number_format = formats[xfs[int(cell.get("s", "0"))]] if payload[0] != "text" else None
        cells[cell.get("r")] = (payload, number_format)
    columns = [(int(col.get("min")), float(col.get("width")), col.get("hidden") == "1") for col in sheet.iter(f"{M}col")]
    if grid_columns:
        columns = [column for column in columns if column[0] <= grid_columns]
    return {
        "cells": cells,
        "merges": sorted(node.get("ref") for node in sheet.iter(f"{M}mergeCell")),
        "columns": columns,
        "print_area": re.search(r"_xlnm\.Print_Area[^>]*>([^<]*)<", workbook).group(1),
        "row_breaks": [node.get("id") for node in sheet.iter(f"{M}brk")],
        "hidden_rows": [row.get("r") for row in sheet.iter(f"{M}row") if row.get("hidden") == "1"],
    }


def near_black(rgb):
    """Print colours at or below #222222 as black: the bundled layout prints its Service header in
    #222222 and the other headers in #000000, while a template has one heading colour."""
    rgb = rgb.upper()
    return "000000" if all(int(rgb[index:index + 2], 16) <= 0x22 for index in (0, 2, 4)) else rgb


def appearance_summary(path):
    """How text and the logo look: each text run's font, size, bold, italic and colour, and where each
    logo picture starts and how big it is (to 0.1 mm)."""
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        drawing = archive.read("xl/drawings/drawing1.xml") if "xl/drawings/drawing1.xml" in archive.namelist() else None
    fonts = styles.find(f"{M}fonts").findall(f"{M}font")
    xfs = styles.find(f"{M}cellXfs").findall(f"{M}xf")

    def font_props(node, base):
        if node is None:
            return base
        name = node.find(f"{M}rFont") if node.find(f"{M}rFont") is not None else node.find(f"{M}name")
        size, bold, italic, color = (node.find(f"{M}{tag}") for tag in ("sz", "b", "i", "color"))
        return (
            name.get("val") if name is not None else base[0],
            float(size.get("val")) if size is not None else base[1],
            bold.get("val", "1") != "0" if bold is not None else base[2],
            italic.get("val", "1") != "0" if italic is not None else base[3],
            near_black((color.get("rgb") or "")[-6:]) if color is not None and color.get("rgb") else base[4],
        )

    text_fonts = {}
    for cell in sheet.iter(f"{M}c"):
        if cell.get("t") != "inlineStr" or not "".join(node.text or "" for node in cell.iter(f"{M}t")):
            continue
        base = font_props(fonts[int(xfs[int(cell.get("s", "0"))].get("fontId"))], ("", 0.0, False, False, "000000"))
        runs = [run for run in cell.findall(f"{M}is/{M}r") if run.findtext(f"{M}t")]
        text_fonts[cell.get("r")] = sorted({font_props(run.find(f"{M}rPr"), base) for run in runs} or {base})
    pictures = []
    if drawing is not None:
        for anchor in ET.fromstring(drawing):
            if anchor.find(f"{XDR}pic") is None:
                continue
            marker = anchor.find(f"{XDR}from")
            extent = anchor.find(f"{XDR}pic/{XDR}spPr/{A}xfrm/{A}ext")
            pictures.append((
                tuple(int(marker.findtext(f"{XDR}{tag}")) for tag in ("col", "colOff", "row", "rowOff")),
                tuple(round(int(extent.get(axis)) / (EMU_PER_MM / 10)) for axis in ("cx", "cy")),
            ))
    return {"text_fonts": text_fonts, "pictures": sorted(pictures)}


def render(definition, brief, lines):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "quotation.xlsx"
    quote.write_template_quote_xlsx(definition, path, brief, lines)
    return tmp, path


def cell_text(summary, ref):
    entry = summary["cells"].get(ref)
    return entry[0][1] if entry else None


def find_refs(summary, text):
    return [ref for ref, ((kind, value), _) in summary["cells"].items() if kind == "text" and value == text]


def split_ref(ref):
    letters, digits = re.match(r"([A-Z]+)(\d+)$", ref).groups()
    return letters, int(digits)


def custom_template():
    """Seven columns, no position column, unit and rate columns, logo bottom-left, client on the right."""
    return {
        "schema": qt.SCHEMA_ID,
        "name": "Rates layout",
        "page": {
            "size": "A4", "orientation": "portrait",
            "margins_mm": {"left": 12, "right": 12, "top": 15, "bottom": 15, "header": 6, "footer": 6},
            "row_height_pt": 16, "first_page_rows": 40, "page_rows": 44, "continuation_top_rows": 1,
        },
        "header": {"rows": 7},
        "grid": {"columns": [{"width": 40}, {"width": 9}, {"width": 8}, {"width": 13}, {"width": 15}, {"width": 7}, {"width": 12}]},
        "theme": {"font": "Arial", "size_pt": 10, "color": "#1F3864", "heading_color": "#C00000"},
        "labels": {"total": "Subtotal", "attention": "Attn:", "project_number": "Ref:"},
        "blocks": [
            {"type": "quote_details", "area": "header", "row": 1, "rows": 3, "col": 1, "span": 2,
             "fields": ["project_title", "quote_date", "project_number"]},
            {"type": "client_details", "area": "header", "row": 2, "rows": 3, "col": 5, "span": 3,
             "fields": ["client_name", "attention"], "address_rows": 1, "indent_cols": 0},
            {"type": "logo", "area": "header", "row": 5, "rows": 2, "col": 1, "span": 1,
             "logo": {"width_mm": 30, "height_mm": 10, "align": "right", "valign": "top", "keep_aspect": False, "repeat": False}},
            {"type": "item_table", "area": "body", "col": 1, "span": 5, "gap_before": 0, "currency_row": False, "columns": [
                {"key": "description", "label": "Item", "col": 1, "span": 1, "align": "left"},
                {"key": "quantity", "label": "Qty", "col": 2, "span": 1, "align": "center"},
                {"key": "unit", "label": "Unit", "col": 3, "span": 1, "align": "center"},
                {"key": "unit_price", "label": "Rate", "col": 4, "span": 1, "align": "right"},
                {"key": "amount", "label": "Amount", "col": 5, "span": 1, "align": "right"},
            ]},
            {"type": "totals", "area": "body", "col": 4, "span": 3, "gap_before": 1, "label_col": 4, "currency_col": 6},
            {"type": "terms", "area": "body", "col": 1, "span": 7, "gap_before": 1},
            {"type": "acceptance", "area": "body", "col": 4, "span": 4, "gap_before": 2, "line_chars": 20},
        ],
    }


def standard_with_theme(font, size, rows, logo_width_mm):
    """Standard in another theme font with both page lengths at `rows` and the logo `logo_width_mm` wide."""
    definition = copy.deepcopy(STANDARD_RAW)
    definition["theme"].update({"font": font, "size_pt": size})
    definition["page"].update({"first_page_rows": rows, "page_rows": rows})
    next(block for block in definition["blocks"] if block["type"] == "logo")["logo"]["width_mm"] = logo_width_mm
    return definition


def small_font_template():
    """Standard at Calibri 9 at the most rows the reader accepts for that font (42 of 18.7 pt). Column I
    holds a logo of at most 39.1 mm at Calibri 9, so the logo is 35 mm."""
    return standard_with_theme("Calibri", 9, 42, 35)


def exact_width_template():
    """Standard at Arial 16 at its 85-row limit. Arial 16's unit is the exact digit width (8.90 pt; every
    whole-pixel width is wider), so this limit is set by the width LibreOffice prints with."""
    return standard_with_theme("Arial", 16, 85, 47.8)


def wide_png(width, height):
    """A solid-colour PNG built with the standard library."""
    raw = b"".join(b"\x00" + bytes([200, 30, 30]) * width for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def full_summary(path, grid_columns):
    return {**workbook_summary(path, grid_columns), **appearance_summary(path)}


def cell_alignments(path):
    """Effective horizontal alignment of every cell that holds text, a number or a formula. General
    alignment reads as left for text and right for numbers, as Excel shows it."""
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
    xfs = styles.find(f"{M}cellXfs").findall(f"{M}xf")
    result = {}
    for cell in sheet.iter(f"{M}c"):
        if cell.find(f"{M}f") is not None:
            kind = "number"
        elif cell.get("t") == "inlineStr":
            if not "".join(node.text or "" for node in cell.iter(f"{M}t")):
                continue
            kind = "text"
        elif cell.get("t") is None and cell.find(f"{M}v") is not None:
            kind = "number"
        else:
            continue
        alignment = xfs[int(cell.get("s", "0"))].find(f"{M}alignment")
        horizontal = alignment.get("horizontal") if alignment is not None else None
        result[cell.get("r")] = horizontal or ("left" if kind == "text" else "right")
    return result


def row_heights(path):
    """Rows 1 to the last row written: (height in points, whether the height is fixed). A row with no
    height of its own takes the sheet default and is not fixed, so Excel may size it to its content."""
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    default = float(sheet.find(f"{M}sheetFormatPr").get("defaultRowHeight"))
    rows = sheet.findall(f"{M}sheetData/{M}row")
    explicit = {int(row.get("r")): (float(row.get("ht")), row.get("customHeight") == "1") for row in rows if row.get("ht")}
    last = max(int(row.get("r")) for row in rows)
    return {number: explicit.get(number, (default, False)) for number in range(1, last + 1)}


def text_box_markers(path):
    """Where the company details text box starts and ends, as (col, colOff, row, rowOff) tuples."""
    with zipfile.ZipFile(path) as archive:
        if "xl/drawings/drawing1.xml" not in archive.namelist():
            return None
        drawing = ET.fromstring(archive.read("xl/drawings/drawing1.xml"))
    for anchor in drawing:
        if anchor.find(f"{XDR}sp") is None:
            continue
        return {
            tag: tuple(int(anchor.findtext(f"{XDR}{tag}/{XDR}{name}")) for name in ("col", "colOff", "row", "rowOff"))
            for tag in ("from", "to")
        }
    return None


def layout_details(path):
    return {"alignments": cell_alignments(path), "heights": row_heights(path), "text_box": text_box_markers(path)}


def cached_formula_values(path):
    """The results saved with each formula, which a viewer shows before it recalculates."""
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return {
        cell.get("r"): float(cell.findtext(f"{M}v"))
        for cell in sheet.iter(f"{M}c") if cell.find(f"{M}f") is not None
    }


def cell_borders(path, ref):
    """The (top, bottom) border styles of one cell."""
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
    cell = next(node for node in sheet.iter(f"{M}c") if node.get("r") == ref)
    xf = styles.find(f"{M}cellXfs")[int(cell.get("s", "0"))]
    border = styles.find(f"{M}borders")[int(xf.get("borderId"))]
    return tuple(border.find(f"{M}{side}").get("style") for side in ("top", "bottom"))


def page_setup_and_margins(path):
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return sheet.find(f"{M}pageSetup").attrib, sheet.find(f"{M}pageMargins").attrib


def item_rows(summary, prefix="Printed panel"):
    """The first row of each synthetic item: its description starts with the item text and it carries an amount."""
    rows = []
    for ref, ((kind, value), _) in summary["cells"].items():
        letter, row = split_ref(ref)
        amount = summary["cells"].get(f"E{row}")
        if letter == "A" and kind == "text" and value.startswith(prefix) and amount and amount[0][0] == "number":
            rows.append(row)
    return sorted(rows)


class StandardTemplateEquivalenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.standard = qt.load_standard_quote_template()
        cls.grid_columns = len(cls.standard["grid"]["columns"])
        cls.legacy = {}
        cls.legacy_details = {}
        for name, (brief, lines) in EQUIVALENCE_CASES.items():
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "legacy.xlsx"
                quote.write_quote_layout_xlsx(BUNDLED_LAYOUT, path, copy.deepcopy(brief), lines)
                cls.legacy[name] = full_summary(path, cls.grid_columns)
                cls.legacy_details[name] = layout_details(path)

    def template_summaries(self, definition):
        summaries = {}
        for name, (brief, lines) in EQUIVALENCE_CASES.items():
            tmp, path = render(definition, copy.deepcopy(brief), lines)
            self.addCleanup(tmp.cleanup)
            summaries[name] = full_summary(path, self.grid_columns)
        return summaries

    def template_details(self, definition):
        details = {}
        for name, (brief, lines) in EQUIVALENCE_CASES.items():
            tmp, path = render(definition, copy.deepcopy(brief), lines)
            self.addCleanup(tmp.cleanup)
            details[name] = layout_details(path)
        return details

    def test_standard_template_matches_the_bundled_layout(self):
        for name, template in self.template_summaries(self.standard).items():
            with self.subTest(case=name):
                for key, legacy_value in self.legacy[name].items():
                    self.assertEqual(template[key], legacy_value, key)

    def test_standard_matches_the_bundled_alignment_row_heights_and_text_box(self):
        for name, template in self.template_details(self.standard).items():
            legacy = self.legacy_details[name]
            with self.subTest(case=name, part="alignment"):
                differing = {
                    ref: (legacy["alignments"][ref], template["alignments"].get(ref))
                    for ref in legacy["alignments"] if legacy["alignments"][ref] != template["alignments"].get(ref)
                }
                self.assertEqual(differing, {}, "cell alignment as (legacy, template)")
            with self.subTest(case=name, part="row heights"):
                for row, legacy_height in legacy["heights"].items():
                    template_height = template["heights"][row]
                    if row <= BUNDLED_DESIGNED_ROWS_END:
                        self.assertEqual(legacy_height, (BUNDLED_DESIGNED_HEIGHT_PT, True), f"row {row} in the bundled layout")
                        self.assertEqual(template_height, legacy_height, f"row {row} height and fixed flag")
                    else:
                        self.assertIn(legacy_height[0], BUNDLED_STRAY_HEIGHTS_PT | {BUNDLED_DESIGNED_HEIGHT_PT}, f"row {row}")
                        self.assertEqual(template_height, (BUNDLED_DESIGNED_HEIGHT_PT, True), f"row {row} height and fixed flag")
            with self.subTest(case=name, part="text box"):
                legacy_box, template_box = legacy["text_box"], template["text_box"]
                self.assertIsNotNone(legacy_box)
                self.assertIsNotNone(template_box)
                for edge in ("from", "to"):
                    self.assertEqual(template_box[edge][0], legacy_box[edge][0], f"{edge} column")
                    self.assertEqual(template_box[edge][2], legacy_box[edge][2], f"{edge} row")
                    for index in (1, 3):
                        self.assertLessEqual(
                            abs(template_box[edge][index] - legacy_box[edge][index]), TEXT_BOX_TOLERANCE_EMU,
                            f"{edge} offset index {index}: legacy {legacy_box[edge]}, template {template_box[edge]}",
                        )

    def test_changes_to_the_standard_template_are_caught(self):
        # Each change is a valid template, so only the comparison with the bundled layout can catch it.
        def block(definition, block_type):
            return next(item for item in definition["blocks"] if item["type"] == block_type)

        changes = {
            "logo moves right": (lambda d: block(d, "logo")["logo"].update({"align": "right"}), "pictures"),
            "logo shrinks": (lambda d: block(d, "logo")["logo"].update({"height_mm": 15}), "pictures"),
            "logo stops repeating": (lambda d: block(d, "logo")["logo"].update({"repeat": False}), "pictures"),
            "totals move down": (lambda d: block(d, "totals").update({"gap_before": 3}), "cells"),
            "totals label moves": (lambda d: block(d, "totals").update({"col": 3, "span": 4, "label_col": 3}), "cells"),
            "signature line shortens": (lambda d: block(d, "signature").update({"line_chars": 20}), "cells"),
            "terms font grows": (lambda d: block(d, "terms")["style"].update({"size_pt": 11}), "text_fonts"),
            "attention label changes": (lambda d: d["labels"].update({"attention": "Attn:"}), "cells"),
            "address rows drop": (lambda d: block(d, "client_details").update({"address_rows": 3}), "cells"),
        }
        for name, (change, key) in changes.items():
            with self.subTest(change=name):
                definition = copy.deepcopy(self.standard)
                change(definition)
                definition = qt.read_quote_template(definition)
                summaries = self.template_summaries(definition)
                self.assertTrue(any(summaries[case][key] != self.legacy[case][key] for case in summaries), key)

    def test_multi_page_case_really_paginates(self):
        brief, lines = EQUIVALENCE_CASES["multi_page_sections"]
        tmp, path = render(qt.load_standard_quote_template(), copy.deepcopy(brief), lines)
        self.addCleanup(tmp.cleanup)
        summary = workbook_summary(path)
        breaks = [int(row) for row in summary["row_breaks"]]
        self.assertGreaterEqual(len(breaks), 2)
        # The item table header repeats two rows into each later page that carries items.
        header_rows = sorted(split_ref(ref)[1] for ref in find_refs(summary, "Estimate"))
        self.assertEqual(header_rows[0], 20)
        self.assertGreaterEqual(len(header_rows), 3)
        self.assertEqual(header_rows[1:], [page_end + 3 for page_end in breaks[:len(header_rows) - 1]])


class TemplateRenderTest(unittest.TestCase):
    def setUp(self):
        self.brief = synthetic_brief()
        self.lines = synthetic_lines(3)

    def summary(self, definition, brief=None, lines=None):
        tmp, path = render(definition, brief or self.brief, lines or self.lines)
        self.addCleanup(tmp.cleanup)
        return workbook_summary(path), path

    def test_columns_are_added_removed_renamed_and_reordered(self):
        summary, _ = self.summary(custom_template())
        header_row = 8  # header.rows 7, gap 0
        self.assertEqual([cell_text(summary, f"{letter}{header_row}") for letter in "ABCDE"], ["Item", "Qty", "Unit", "Rate", "Amount"])
        self.assertEqual(find_refs(summary, "Pos."), [])
        self.assertEqual(find_refs(summary, "Estimate"), [])
        # No currency row: section 1.0 starts on the next row; its first item follows.
        self.assertEqual(cell_text(summary, f"A{header_row + 1}"), "Section 1")
        item_row = header_row + 3
        self.assertTrue(cell_text(summary, f"A{item_row}").startswith("Printed panel"))
        self.assertEqual(cell_text(summary, f"B{item_row}"), "2.5")
        self.assertEqual(cell_text(summary, f"C{item_row}"), "sqm")
        self.assertEqual(summary["cells"][f"D{item_row}"], (("number", 120.0), "#,##0.00"))
        self.assertEqual(summary["cells"][f"E{item_row}"], (("number", 301.0), "#,##0.00"))
        self.assertEqual(summary["columns"], [(index, width, False) for index, width in enumerate([40, 9, 8, 13, 15, 7, 12], start=1)])
        self.assertEqual(summary["print_area"].split(":")[0], "Quotation!$A$1")
        self.assertTrue(summary["print_area"].startswith("Quotation!$A$1:$G$"))

    def test_totals_follow_the_amount_column(self):
        summary, _ = self.summary(custom_template())
        (label_ref,) = find_refs(summary, "Subtotal")
        letter, total_row = split_ref(label_ref)
        self.assertEqual(letter, "D")
        first_body_row = 9
        self.assertEqual(summary["cells"][f"E{total_row}"][0], ("formula", f"SUM(E{first_body_row}:E{total_row - 1})"))
        self.assertEqual(summary["cells"][f"E{total_row + 1}"][0], ("formula", f"ROUND(E{total_row}*0.090000,2)"))
        self.assertEqual(summary["cells"][f"E{total_row + 2}"][0], ("formula", f"SUM(E{total_row}:E{total_row + 1})"))
        self.assertEqual([cell_text(summary, f"F{total_row + offset}") for offset in range(3)], ["SGD"] * 3)
        last_item_row = max(split_ref(ref)[1] for ref in summary["cells"] if split_ref(ref)[1] < total_row and ref[0] in "ABCDE")
        self.assertEqual(total_row, last_item_row + 2)  # gap_before 1: one blank row

    def test_header_blocks_sit_where_the_template_places_them(self):
        summary, path = self.summary(custom_template())
        self.assertEqual(cell_text(summary, "A1"), "Synthetic Booth")
        self.assertEqual(summary["cells"]["A2"], (("number", 46179.0), "dd\\ mmmm\\ yyyy"))
        self.assertIn("A2:B2", summary["merges"])
        self.assertEqual(cell_text(summary, "A3"), "Ref: SYN-001")
        self.assertEqual(cell_text(summary, "E2"), "Synthetic Client")
        self.assertEqual(cell_text(summary, "E3"), "Attn:")
        self.assertEqual(cell_text(summary, "E4"), "Sam Example")
        self.assertEqual(find_refs(summary, "1 Example Road"), [])
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        # No company_details block: no text box, only the logo picture.
        drawing = ET.fromstring(zipfile.ZipFile(path).read("xl/drawings/drawing1.xml"))
        self.assertEqual(len(drawing.findall(f"{XDR}twoCellAnchor")), 0)
        self.assertIn("xl/media/header_logo.png", names)

    def test_theme_and_heading_colours_reach_the_styles(self):
        _, path = self.summary(custom_template())
        with zipfile.ZipFile(path) as archive:
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(archive.read("xl/styles.xml"))
        fonts = styles.find(f"{M}fonts").findall(f"{M}font")
        xfs = styles.find(f"{M}cellXfs").findall(f"{M}xf")

        def font_of(ref):
            cell = next(node for node in sheet.iter(f"{M}c") if node.get("r") == ref)
            font = fonts[int(xfs[int(cell.get("s"))].get("fontId"))]
            return (font.find(f"{M}name").get("val"), font.find(f"{M}sz").get("val"),
                    font.find(f"{M}color").get("rgb"), font.find(f"{M}b") is not None)

        self.assertEqual(font_of("A8"), ("Arial", "10", "FFC00000", True))
        self.assertEqual(font_of("E11"), ("Arial", "10", "FF1F3864", False))
        self.assertEqual({font.find(f"{M}name").get("val") for font in fonts}, {"Arial"})
        # Rich text runs repeat the block font, so Excel and LibreOffice agree.
        client = next(node for node in sheet.iter(f"{M}c") if node.get("r") == "E2")
        run = client.find(f"{M}is/{M}r/{M}rPr")
        self.assertEqual(run.find(f"{M}rFont").get("val"), "Arial")
        self.assertEqual(run.find(f"{M}color").get("rgb"), "FF1F3864")

    def test_logo_size_place_and_repeat_follow_the_template(self):
        brief, lines = synthetic_brief(), synthetic_lines(45, sections=45)
        definition = custom_template()
        _, path = self.summary(definition, brief, lines)
        with zipfile.ZipFile(path) as archive:
            drawing = ET.fromstring(archive.read("xl/drawings/drawing1.xml"))
            sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        self.assertGreaterEqual(len(sheet.findall(f"{M}rowBreaks/{M}brk")), 1)
        (anchor,) = drawing.findall(f"{XDR}oneCellAnchor")
        self.assertEqual(anchor.find(f"{XDR}ext").attrib, {"cx": str(30 * 36000), "cy": str(10 * 36000)})
        marker = anchor.find(f"{XDR}from")
        # 40 Arial 10 units: 0.92 of a 5.25 pt digit is 257 whole 96-dpi pixels, narrower than the 285 px placement width.
        column_emu = 257 * 9525
        self.assertEqual(marker.findtext(f"{XDR}col"), "0")
        self.assertEqual(marker.findtext(f"{XDR}colOff"), str(column_emu - 30 * 36000))
        self.assertEqual(marker.findtext(f"{XDR}row"), "4")

        qt_block = next(block for block in definition["blocks"] if block["type"] == "logo")
        qt_block["logo"]["repeat"] = True
        _, repeated = self.summary(definition, brief, lines)
        with zipfile.ZipFile(repeated) as archive:
            drawing = ET.fromstring(archive.read("xl/drawings/drawing1.xml"))
            breaks = ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall(f"{M}rowBreaks/{M}brk")
        anchors = drawing.findall(f"{XDR}oneCellAnchor")
        self.assertEqual(len(anchors), len(breaks) + 1)
        self.assertEqual(
            [int(anchor.findtext(f"{XDR}from/{XDR}row")) for anchor in anchors[1:]],
            [int(brk.get("id")) + 4 for brk in breaks],
        )

    def test_formula_like_text_never_becomes_a_live_formula(self):
        definition = custom_template()
        definition["labels"]["total"] = "=SUM(A1:A9)"
        definition["blocks"][3]["columns"][0]["label"] = "@cmd"
        definition["blocks"].append({"type": "free_text", "area": "body", "col": 1, "span": 7,
                                     "heading": "+42", "lines": ["-1+1", "=HYPERLINK(\"x\")"]})
        brief = synthetic_brief(client={"name": "=HYPERLINK(\"x\")", "attention": "+1"},
                                project={"title": "=1+1"}, payment_terms=["=cmd|' /C calc'!A0"])
        summary, _ = self.summary(definition, brief, synthetic_lines(2, description="=SUM(A1) "))
        formulas = sorted(value for (kind, value), _ in summary["cells"].values() if kind == "formula")
        self.assertEqual(len(formulas), 3)
        self.assertTrue(all(re.fullmatch(r"(SUM|ROUND)\(E\d+[:*][E0-9.,]+\)", formula) for formula in formulas), formulas)
        for text in ("'=SUM(A1:A9)", "'@cmd", "'+42", "'-1+1", "'=HYPERLINK(\"x\")", "=HYPERLINK(\"x\")", "=1+1"):
            self.assertTrue(find_refs(summary, text), text)

    def test_a_table_without_quantity_or_position_shows_neither(self):
        definition = custom_template()
        definition["blocks"][3]["columns"] = [
            {"key": "description", "label": "Item", "col": 1, "span": 4, "align": "left"},
            {"key": "amount", "label": "Amount", "col": 5, "span": 1, "align": "right"},
        ]
        summary, _ = self.summary(definition)
        texts = {value for (kind, value), _ in summary["cells"].values() if kind == "text"}
        self.assertFalse(any(value in {"2.5 sqm", "4 sqm", "2.5", "sqm"} for value in texts))
        self.assertNotIn("1.1", texts)
        self.assertIn("Amount", texts)

    def test_square_metres_print_as_sqm(self):
        definition = custom_template()
        definition["grid"]["columns"][3]["width"] = 30  # the description needs at least 30 width units
        definition["blocks"][3]["columns"] = [
            {"key": "position", "label": "No.", "col": 1, "span": 1, "align": "left"},
            {"key": "quantity", "label": "Qty", "col": 2, "span": 2, "align": "center"},
            {"key": "description", "label": "Item", "col": 4, "span": 1, "align": "left"},
            {"key": "amount", "label": "Amount", "col": 5, "span": 1, "align": "right"},
        ]
        summary, _ = self.summary(definition)
        texts = {value for (kind, value), _ in summary["cells"].values() if kind == "text"}
        self.assertIn("2.5 sqm", texts)
        self.assertFalse(any("m2" in value for value in texts))

    def test_unit_price_is_hidden_when_its_amount_is_hidden(self):
        # Every item in a grouped section shows no amount, so its unit price stays hidden too.
        lines = synthetic_lines(3, sections=1)
        grouped, _ = self.summary(custom_template(), synthetic_brief(section_pricing={"Section 1": "section_total"}), lines)
        ungrouped, _ = self.summary(custom_template(), synthetic_brief(), lines)
        rows = item_rows(ungrouped)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(ungrouped["cells"][f"D{row}"], (("number", 120.0), "#,##0.00"))
            self.assertNotIn(f"D{row}", grouped["cells"])
            self.assertNotIn(f"E{row}", grouped["cells"])

    def test_unit_price_uses_the_exchange_rate(self):
        summary, _ = self.summary(custom_template(), synthetic_brief(currency="USD", exchange_rate=0.74))
        row = item_rows(summary)[0]
        self.assertEqual(summary["cells"][f"D{row}"], (("number", round(120 * 0.74, 2)), "#,##0.00"))
        self.assertEqual(summary["cells"][f"E{row}"], (("number", round(301 * 0.74, 2)), "#,##0.00"))

    def test_unit_price_uses_a_manual_override(self):
        lines = synthetic_lines(3)
        lines[1].unit_price_override = 99.5
        summary, _ = self.summary(custom_template(), self.brief, lines)
        second = item_rows(summary)[1]
        self.assertEqual(summary["cells"][f"D{second}"], (("number", 99.5), "#,##0.00"))
        self.assertEqual(summary["cells"][f"D{item_rows(summary)[0]}"], (("number", 120.0), "#,##0.00"))

    def test_cached_totals_equal_the_item_amounts(self):
        summary, path = self.summary(custom_template())
        subtotal_row = split_ref(find_refs(summary, "Subtotal")[0])[1]
        cached = cached_formula_values(path)
        expected_subtotal = round(sum(line.amount for line in self.lines), 2)
        expected_tax = round(expected_subtotal * 0.09, 2)
        self.assertEqual(cached[f"E{subtotal_row}"], expected_subtotal)
        self.assertEqual(cached[f"E{subtotal_row + 1}"], expected_tax)
        self.assertEqual(cached[f"E{subtotal_row + 2}"], round(expected_subtotal + expected_tax, 2))

    def test_currency_row_and_totals_use_the_brief_currency(self):
        # Standard: the currency sits under the Estimate heading (E21) and on each of the three totals rows (F).
        summary, _ = self.summary(qt.load_standard_quote_template(), synthetic_brief(currency="USD"))
        self.assertEqual(cell_text(summary, "E21"), "USD")
        self.assertEqual(len(find_refs(summary, "USD")), 4)
        self.assertFalse(find_refs(summary, "SGD"))

    def test_grand_total_keeps_its_double_bottom_border(self):
        summary, path = self.summary(qt.load_standard_quote_template(), synthetic_brief())
        (total_label,) = find_refs(summary, "Total")
        total_row = split_ref(total_label)[1]
        self.assertEqual(cell_borders(path, f"E{total_row}"), ("thin", None))
        self.assertEqual(cell_borders(path, f"E{total_row + 2}"), ("thin", "double"))

    def test_landscape_orientation_and_margins_are_written(self):
        definition = custom_template()
        # Landscape holds 29 rows of 16 pt (30.6 counted at 16.75 pt, one row spare) on this paper and
        # margin set, with room for items.
        definition["page"].update({"orientation": "landscape", "first_page_rows": 28, "page_rows": 29})
        with self.assertRaisesRegex(qt.QuoteTemplateError, r"page\.page_rows"):
            qt.read_quote_template(with_rows(definition, 30, first_page_rows=28))
        _, path = self.summary(definition)
        setup, margins = page_setup_and_margins(path)
        self.assertEqual(setup.get("orientation"), "landscape")
        self.assertEqual(setup.get("paperSize"), "9")
        for key, value in qt.read_quote_template(definition)["page"]["margins_mm"].items():
            self.assertAlmostEqual(float(margins.get(key)), value / 25.4, places=6, msg=key)

    def test_an_item_taller_than_a_later_page_is_refused_with_a_path(self):
        huge = [quote.QuoteLine(
            section="Section 1", quantity=1, unit="nos", description="word " * 400, pricing_keyword="", display_price="",
            matched_price=None, amount=10.0, match_status="matched", match_candidates=[],
        )]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(qt.QuoteTemplateError) as caught:
                quote.write_template_quote_xlsx(custom_template(), Path(tmp) / "quotation.xlsx", self.brief, huge)
        self.assertEqual(caught.exception.path, "page.page_rows")
        self.assertIn("taller", caught.exception.message)

    def test_cli_quote_template_option_lays_out_the_quote(self):
        brief = {
            "company_identity": "Example Quotes Co",
            "quote_date": "2026-06-04",
            "client": {"name": "Sample Client", "attention": "Alex Example"},
            "project": {"title": "Sample Project"},
            "line_items": [{"section": "Furniture", "quantity": 1, "unit": "lot",
                            "description": "Furniture package", "display_price": "Included"}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief_path = tmp_path / "brief.json"
            template_path = tmp_path / "template.json"
            brief_path.write_text(json.dumps(brief), encoding="utf-8")
            template_path.write_text(json.dumps(custom_template()), encoding="utf-8")
            argv = ["generate_quote.py", "--brief", str(brief_path), "--out", str(tmp_path / "out"),
                    "--template", str(FIXTURE_CATALOG), "--layout-template", str(tmp_path / "missing.xlsx"),
                    "--quote-template", str(template_path)]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(quote.main(), 0)
            summary = workbook_summary(tmp_path / "out" / "quotation.xlsx")
        self.assertEqual(cell_text(summary, "A8"), "Item")
        self.assertEqual(find_refs(summary, "Pos."), [])

    def test_cli_refuses_an_unsafe_template(self):
        definition = custom_template()
        definition["grid"]["columns"][0]["colour"] = "#FFFFFF"
        with tempfile.TemporaryDirectory() as tmp:
            template_path = Path(tmp) / "template.json"
            template_path.write_text(json.dumps(definition), encoding="utf-8")
            with self.assertRaisesRegex(qt.QuoteTemplateError, r"grid\.columns\[0\]"):
                quote.write_template_quote_xlsx(
                    qt.parse_quote_template_json(template_path.read_bytes()),
                    Path(tmp) / "quotation.xlsx", self.brief, self.lines,
                )
            self.assertFalse((Path(tmp) / "quotation.xlsx").exists())


def narrow_company_column(box, **theme):
    """Standard with the company details column (I) narrowed to 8 width units and the given box."""
    definition = copy.deepcopy(qt.load_standard_quote_template())
    definition["grid"]["columns"][8]["width"] = 8
    definition["page"].update({"first_page_rows": 41, "page_rows": 41})
    definition["theme"].update(theme)
    for block in definition["blocks"]:
        if block["type"] == "logo":
            block["logo"].update({"width_mm": 10, "height_mm": 5})
        if block["type"] == "company_details":
            block["box"] = box
    return definition


class CompanyBoxRoomTest(unittest.TestCase):
    """A company details box must leave room inside its cells, checked when read and again when rendered."""

    def setUp(self):
        self.brief = synthetic_brief()
        self.lines = synthetic_lines(3)
        self.index = next(i for i, block in enumerate(qt.load_standard_quote_template()["blocks"]) if block["type"] == "company_details")

    def assert_reader_refuses(self, definition):
        with self.assertRaises(qt.QuoteTemplateError) as caught:
            qt.read_quote_template(definition)
        self.assertEqual(caught.exception.path, f"blocks[{self.index}].box")
        self.assertEqual(caught.exception.message, "leaves no room for the company details inside its cells")

    def test_reader_refuses_a_box_wider_than_its_column(self):
        # 8 units place as 61 px (16.1 mm) and print as 8 Calibri 13 digits (18.6 mm): 15 mm of margins fit, 20 mm do not.
        fits = narrow_company_column({"left_mm": 7.5, "top_mm": 0, "right_mm": 7.5})
        qt.read_quote_template(fits)
        with tempfile.TemporaryDirectory() as tmp:
            quote.write_template_quote_xlsx(fits, Path(tmp) / "quotation.xlsx", self.brief, self.lines)
            self.assertTrue((Path(tmp) / "quotation.xlsx").exists())
        self.assert_reader_refuses(narrow_company_column({"left_mm": 10, "top_mm": 0, "right_mm": 10}))

    def test_reader_uses_the_printed_width_of_a_small_font(self):
        # At Calibri 11 the same 8 units count as 8 x 0.92 x 5.25 pt: 51 whole pixels (13.5 mm), narrower than the 16.1 mm placement width.
        self.assert_reader_refuses(narrow_company_column({"left_mm": 7, "top_mm": 0, "right_mm": 7}, size_pt=11))
        qt.read_quote_template(narrow_company_column({"left_mm": 6.5, "top_mm": 0, "right_mm": 6.5}, size_pt=11))

    def test_hidden_columns_give_the_box_no_room(self):
        # The company block spans hidden column H (16.375 units) and column I (8 units). Only I is drawn, so
        # 20 mm of margins leave no room, though the two columns' widths together would hold them.
        def spanning_hidden(box):
            definition = narrow_company_column(box)
            definition["blocks"][self.index].update({"col": 8, "span": 2})
            return definition
        qt.read_quote_template(spanning_hidden({"left_mm": 7.5, "top_mm": 0, "right_mm": 7.5}))
        self.assert_reader_refuses(spanning_hidden({"left_mm": 10, "top_mm": 0, "right_mm": 10}))
        # The renderer measures the block the same way.
        read = qt.read_quote_template(spanning_hidden({"left_mm": 0, "top_mm": 0, "right_mm": 0}))
        read["blocks"][self.index]["box"] = {"left_mm": 10, "top_mm": 0, "right_mm": 10}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(quote.quote_template, "read_quote_template", return_value=read):
            with self.assertRaisesRegex(qt.QuoteTemplateError, rf"blocks\[{self.index}\]\.box"):
                quote.write_template_quote_xlsx(read, Path(tmp) / "quotation.xlsx", self.brief, self.lines)

    def test_reader_refuses_a_top_margin_below_the_block(self):
        def one_row(top):
            definition = narrow_company_column({"left_mm": 0, "top_mm": top, "right_mm": 0})
            definition["blocks"][self.index]["rows"] = 1
            return definition
        qt.read_quote_template(one_row(6.5))  # one 18.7 pt row is 6.6 mm
        self.assert_reader_refuses(one_row(6.7))

    def test_renderer_still_refuses_a_box_with_no_room(self):
        # A definition that reached the renderer without this reader check (for example saved by an older build).
        read = qt.read_quote_template(narrow_company_column({"left_mm": 0, "top_mm": 0, "right_mm": 0}))
        read["blocks"][self.index]["box"] = {"left_mm": 10, "top_mm": 0, "right_mm": 10}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(quote.quote_template, "read_quote_template", return_value=read):
            with self.assertRaisesRegex(qt.QuoteTemplateError, rf"blocks\[{self.index}\]\.box"):
                quote.write_template_quote_xlsx(read, Path(tmp) / "quotation.xlsx", self.brief, self.lines)
            self.assertFalse((Path(tmp) / "quotation.xlsx").exists())


class LogoFitTest(unittest.TestCase):
    """A logo's box must fit its block's width as Excel draws it, or Excel cuts it at the print area.

    At Calibri 9 Excel draws column I (26.875 units) narrowest at 120 dpi, with 7 px digits: 188 px,
    112.8 pt. Standard's 47.8 mm (135.5 pt) logo runs past the print area there and is cut."""

    COLUMN_I_AT_120_DPI_EMU = round(188 * 72 / 120 * 12_700)

    def test_a_logo_wider_than_its_block_is_refused(self):
        with self.assertRaises(qt.QuoteTemplateError) as caught:
            qt.read_quote_template(standard_with_theme("Calibri", 9, 42, 47.8))
        self.assertEqual(caught.exception.path, "blocks[0].logo.width_mm")
        self.assertIn("(at most 39.1 mm)", caught.exception.message)
        qt.read_quote_template(standard_with_theme("Calibri", 9, 42, 39.1))
        # Standard itself keeps its logo: at Calibri 13 column I is placed at 193 px (51.1 mm).
        qt.load_standard_quote_template()

    def test_a_right_aligned_wide_logo_stays_inside_column_i(self):
        definition = standard_with_theme("Calibri", 9, 42, 39)
        next(block for block in definition["blocks"] if block["type"] == "logo")["logo"]["align"] = "right"
        brief = synthetic_brief()
        brief["company"]["logo_data_url"] = "data:image/png;base64," + base64.b64encode(wide_png(600, 100)).decode()
        tmp, path = render(definition, brief, synthetic_lines(3))
        with tmp, zipfile.ZipFile(path) as archive:
            drawing = ET.fromstring(archive.read("xl/drawings/drawing1.xml"))
        anchors = drawing.findall(f"{XDR}oneCellAnchor")  # one a page: Standard repeats its logo
        self.assertTrue(anchors)
        for anchor in anchors:
            self.assertEqual(anchor.findtext(f"{XDR}from/{XDR}col"), "8")
            right_edge = int(anchor.findtext(f"{XDR}from/{XDR}colOff")) + int(anchor.find(f"{XDR}ext").get("cx"))
            self.assertLessEqual(right_edge, self.COLUMN_I_AT_120_DPI_EMU)


class QuantityColumnTest(unittest.TestCase):
    """The reader refuses a quantity column the renderer would refuse ordinary quantities in. Both measure
    the column with quote_template.cell_text_width_points and a quantity digit by digit."""

    def test_r4_case_is_refused_where_it_used_to_fail_at_render_time(self):
        # The compact grid's quantity column is 6 units (25.5 pt of text); at Calibri 17 "2.5" needs 25.5 pt and a hair.
        with self.assertRaises(qt.QuoteTemplateError) as caught:
            qt.read_quote_template(with_rows(compact_template("Calibri", 17), 30))
        self.assertEqual(caught.exception.path, "blocks[3].columns[1]")
        self.assertEqual(caught.exception.message, "the quantity column is too narrow for 3 digits at 17 pt")

    def test_every_accepted_quantity_column_renders_three_digits(self):
        lines = synthetic_lines(3)
        for line in lines:
            line.quantity = 999
        accepted = refused = 0
        for font in qt.ALLOWED_FONTS:
            for size in [half / 2 for half in range(14, 41)]:
                definition = with_rows(compact_template(font, size), 30)
                try:
                    qt.read_quote_template(definition)
                except qt.QuoteTemplateError as exc:
                    self.assertEqual(exc.path, "blocks[3].columns[1]", (font, size))
                    refused += 1
                    continue
                accepted += 1
                with self.subTest(font=font, size=size):
                    tmp, path = render(definition, synthetic_brief(), lines)
                    with tmp:
                        self.assertTrue(path.exists())
        # Both fonts hold 3 digits up to 15 pt in 6 units, and refuse them from 15.5 pt.
        self.assertEqual((accepted, refused), (34, 20))


def compact_template(font, size):
    """The custom layout with a 74-unit grid, which prints at 100 % for every allowed font up to 13 pt."""
    definition = custom_template()
    definition["theme"].update({"font": font, "size_pt": size})
    for column, width in zip(definition["grid"]["columns"], (30, 6, 6, 9, 10, 5, 8)):
        column["width"] = width
    return definition


def with_rows(definition, rows, **page):
    definition = copy.deepcopy(definition)
    definition["page"].update({"first_page_rows": rows, "page_rows": rows, **page})
    return definition


class PrintedRowFitTest(unittest.TestCase):
    """Excel prints rows taller than their set height, by up to about 5 % at 192 dpi depending on the
    theme font (28.2 pt Calibri 8 rows land 29.06 pt apart). The reader counts each row 0.75 pt taller
    and keeps one row spare on every page. Each refused count below adds automatic page breaks in
    Excel's A4 page break view at 96 or 192 dpi, or sits within one row of doing so."""

    def assert_rows_limit(self, definition, rows):
        qt.read_quote_template(with_rows(definition, rows))
        with self.assertRaises(qt.QuoteTemplateError) as caught:
            qt.read_quote_template(with_rows(definition, rows + 1))
        self.assertEqual(caught.exception.path, "page.first_page_rows")

    def test_a_compact_grid_at_full_size_keeps_a_row_spare(self):
        # 267 mm (756.9 pt) of 16 pt rows: 47.3 at the set height, 45.19 at 16.75 pt, 44 with one row spare.
        for font, size in (("Calibri", 11), ("Arial", 10)):
            with self.subTest(font=font, size=size):
                self.assert_rows_limit(compact_template(font, size), 44)
                with self.assertRaises(qt.QuoteTemplateError):
                    qt.read_quote_template(with_rows(compact_template(font, size), 46))

    def test_a_small_font_at_full_size_keeps_a_row_spare(self):
        # Calibri 7 prints the custom layout's 104 units at 100 %: 44 rows of 16 pt; with 19 mm margins
        # (734.2 pt) and 18.7 pt rows, 36 rows (37.75 counted).
        calibri7 = custom_template()
        calibri7["theme"].update({"font": "Calibri", "size_pt": 7})
        self.assert_rows_limit(calibri7, 44)
        for font, size in (("Calibri", 7), ("Calibri", 8), ("Arial", 7)):
            definition = custom_template()
            definition["theme"].update({"font": font, "size_pt": size})
            definition["page"].update({"row_height_pt": 18.7, "margins_mm": {**definition["page"]["margins_mm"], "top": 19, "bottom": 19}})
            with self.subTest(font=font, size=size):
                self.assert_rows_limit(definition, 36)

    def test_tall_rows_at_a_small_font_are_refused_where_excel_adds_breaks(self):
        # At 192 dpi Excel holds 27, not 28, rows of 28.305 pt Calibri 8 with 5 mm margins (28.0 counted
        # rows), and 21, not 22, rows of 36.229 pt Calibri 9 (22.0 counted rows).
        tall = custom_template()
        tall["theme"].update({"font": "Calibri", "size_pt": 8})
        tall["page"].update({"row_height_pt": 28.305})
        tall["page"]["margins_mm"].update({"top": 5, "bottom": 5, "header": 5, "footer": 5})
        compact = compact_template("Calibri", 9)
        compact["page"].update({"row_height_pt": 36.229, "continuation_top_rows": 0})
        compact["page"]["margins_mm"].update({"top": 5, "bottom": 5, "header": 0, "footer": 0})
        compact["header"]["rows"] = 1
        compact["blocks"] = [block for block in compact["blocks"] if block["area"] != "header"]
        for definition, rows in ((tall, 28), (compact, 22)):
            with self.subTest(rows=rows):
                with self.assertRaises(qt.QuoteTemplateError) as caught:
                    qt.read_quote_template(with_rows(definition, rows))
                self.assertEqual(caught.exception.path, "page.first_page_rows")
                self.assertIn(f"at most {rows - 1})", caught.exception.message)

    def test_columns_too_wide_to_print_above_13_percent_are_refused(self):
        # Excel prints at 10 % or more, and its digit can be a fifth wider than the reader's unit, so a
        # sheet needing under 13 % is refused. 9 columns of 60 units on a 130 mm wide page: Arial 20
        # (11 pt a unit) needs 6 %; Calibri 14 (6.75 pt) needs 10.1 %, where Excel prints at 10 % and
        # breaks the page across; Calibri 7 (3.4 pt) needs 20 %.
        def wide(font, size):
            definition = copy.deepcopy(STANDARD_RAW)
            definition["theme"].update({"font": font, "size_pt": size})
            definition["page"]["margins_mm"].update({"left": 40, "right": 40})
            for column in definition["grid"]["columns"]:
                column["width"] = 60
            return definition
        # The floor sits at 13 %: Calibri 13.75 (6.75 pt) needs 12.998 % and is refused, Calibri 13.5
        # (6.6 pt) needs 13.29 % and is accepted.
        for font, size in (("Arial", 20), ("Calibri", 14), ("Calibri", 13.75)):
            with self.subTest(font=font, size=size):
                with self.assertRaises(qt.QuoteTemplateError) as caught:
                    qt.read_quote_template(wide(font, size))
                self.assertEqual(caught.exception.path, "grid.columns")
        qt.read_quote_template(wide("Calibri", 13.5))
        qt.read_quote_template(wide("Calibri", 7))


class TemplatePdfSmokeTest(unittest.TestCase):
    """LibreOffice prints both layouts on the expected pages with nothing pushed off the page."""

    def test_standard_and_custom_layouts_print_within_their_pages(self):
        running_in_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
        if not quote.libreoffice_candidates():
            message = "LibreOffice is unavailable locally; hosted CI must render the quote template PDFs."
            if running_in_ci:
                self.fail(message)
            self.skipTest(message)
        import pypdfium2 as pdfium

        layouts = {
            "standard": (qt.load_standard_quote_template(), ["Estimate", "Total including GST", "We accept the quotation"]),
            "custom": (custom_template(), ["Rate", "Subtotal", "We accept the quotation"]),
            # A small font at the most rows the reader accepts for it (Calibri 9: 42 rows of 18.7 pt).
            "small font": (small_font_template(), ["Estimate", "Total including GST", "We accept the quotation"]),
            # A limit set by the exact digit width LibreOffice prints with (Arial 16: 85 rows of 18.7 pt).
            "exact width": (exact_width_template(), ["Estimate", "Total including GST", "We accept the quotation"]),
        }
        for name, (definition, phrases) in layouts.items():
            with self.subTest(layout=name), tempfile.TemporaryDirectory() as tmp:
                xlsx_path, pdf_path = Path(tmp) / "quotation.xlsx", Path(tmp) / "quotation.pdf"
                count = 120 if name == "exact width" else 45
                quote.write_template_quote_xlsx(definition, xlsx_path, synthetic_brief(), synthetic_lines(count, sections=9))
                expected_pages = len(workbook_summary(xlsx_path)["row_breaks"]) + 1
                self.assertGreater(expected_pages, 1)
                self.assertEqual(quote.libreoffice_pdf_export(xlsx_path, pdf_path), "libreoffice_exported")
                document = pdfium.PdfDocument(str(pdf_path))
                try:
                    self.assertEqual(len(document), expected_pages)
                    text = ""
                    for page in document:
                        width, height = page.get_size()
                        # The sheet's A4 paper is what the reader's row limit is worked out for.
                        self.assertAlmostEqual(min(width, height), 595.3, delta=1)
                        self.assertAlmostEqual(max(width, height), 841.9, delta=1)
                        textpage = page.get_textpage()
                        page_text = textpage.get_text_range() or ""
                        self.assertTrue(page_text.strip(), "blank page")
                        for index in range(textpage.count_chars()):
                            left, bottom, right, top = textpage.get_charbox(index)
                            if right > left:
                                self.assertGreaterEqual(left, 0)
                                self.assertLessEqual(right, width)
                                self.assertGreaterEqual(bottom, 0)
                                self.assertLessEqual(top, height)
                        text += re.sub(r"\s+", " ", page_text) + " "
                finally:
                    document.close()
                for phrase in phrases:
                    self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
