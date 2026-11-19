"""The red exclusions block printed on every new quotation (#363)."""

import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from tests.test_generate_quote import (
    KONCEPT_LAYOUT,
    NS_MAIN,
    cell_style,
    cell_value,
    find_cell_ref,
    font_color,
    font_for_style,
    header_fidelity_case,
    manual_print_page_for_row,
    normalized_pdf_phrase_bbox,
    quote,
)

EXPECTED_LINES = [
    "The above quote excludes:",
    "Organiser Admin Fees",
    "Power sockets which can be directly ordered with connection fees with the organiser",
    "Connection fees for electrical fittings",
]
RED = "FFFF0000"


def write_workbook(brief, lines):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "quotation.xlsx"
    quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        styles = ET.fromstring(archive.read("xl/styles.xml"))
    return tmp, path, sheet, styles


def row_of(sheet, text):
    ref = find_cell_ref(sheet, text)
    if not ref:
        raise AssertionError(f"{text!r} is not in the workbook")
    return quote.parse_cell_ref(ref)


def cell_node(sheet, ref):
    return next(node for node in sheet.iter(f"{NS_MAIN}c") if node.attrib.get("r") == ref)


class QuoteExclusionsWorkbookTest(unittest.TestCase):
    def test_exact_lines_print_in_red_below_totals_and_before_terms(self):
        brief, lines = header_fidelity_case()
        lines = lines[:3]
        brief.update({"terms_heading": "Terms & Conditions:", "payment_terms": ["50% deposit on confirmation."]})
        tmp, _path, sheet, styles = write_workbook(brief, lines)
        self.addCleanup(tmp.cleanup)

        self.assertEqual([quote.QUOTE_EXCLUSIONS_HEADING, *quote.QUOTE_EXCLUSIONS], EXPECTED_LINES)
        grand_row, _ = row_of(sheet, "Total including GST")
        positions = [row_of(sheet, text) for text in EXPECTED_LINES]
        self.assertEqual(positions, [(grand_row + 3, 1), (grand_row + 4, 2), (grand_row + 5, 2), (grand_row + 6, 2)])
        terms_row, _ = row_of(sheet, "Terms & Conditions:")
        self.assertGreater(terms_row, positions[-1][0])
        self.assertEqual(terms_row, positions[-1][0] + 2)

        for (row, column), text in zip(positions, EXPECTED_LINES):
            ref = f"{'AB'[column - 1]}{row}"
            node = cell_node(sheet, ref)
            self.assertEqual(node.attrib.get("t"), "inlineStr", ref)
            self.assertIsNone(node.find(f"{NS_MAIN}f"), ref)
            self.assertEqual(font_color(font_for_style(styles, cell_style(sheet, ref))).get("rgb"), RED, ref)
            runs = node.findall(f"{NS_MAIN}is/{NS_MAIN}r")
            self.assertEqual("".join(run.findtext(f"{NS_MAIN}t") for run in runs), text)
            for run in runs:
                color = run.find(f"{NS_MAIN}rPr/{NS_MAIN}color")
                self.assertIsNotNone(color, ref)
                self.assertEqual(color.attrib, {"rgb": RED})
            # Plain lines: no item number beside the excluded items.
            if column == 2:
                self.assertEqual(cell_value(sheet, f"A{row}"), "")

        # Totals still sum only the quotation lines; nothing else on the sheet is red.
        total_row, _ = row_of(sheet, "Total")
        formula = cell_node(sheet, f"E{total_row}").findtext(f"{NS_MAIN}f")
        self.assertEqual(formula, f"SUM(E22:E{total_row - 1})")
        red_refs = {
            node.attrib["r"] for node in sheet.iter(f"{NS_MAIN}c")
            if cell_value(sheet, node.attrib["r"]) and node.attrib.get("s")
            and font_color(font_for_style(styles, int(node.attrib["s"]))).get("rgb") == RED
        }
        self.assertEqual(red_refs, {f"{'AB'[column - 1]}{row}" for row, column in positions})

    def test_block_moves_whole_to_the_next_page_instead_of_splitting(self):
        brief, lines = header_fidelity_case()
        found_moved = False
        for count in range(1, 16):
            with self.subTest(count=count):
                tmp, _path, sheet, _styles = write_workbook(brief, lines[:count])
                self.addCleanup(tmp.cleanup)
                rows = [row_of(sheet, text)[0] for text in EXPECTED_LINES]
                self.assertEqual(rows, list(range(rows[0], rows[0] + 4)))
                self.assertEqual(len({manual_print_page_for_row(row) for row in rows}), 1, rows)
                grand_row, _ = row_of(sheet, "Total including GST")
                if rows[0] != grand_row + 3:
                    found_moved = True
                    self.assertEqual(rows[0], quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_BODY_OFFSET)
        self.assertTrue(found_moved, "no case pushed the block past the first page")

    def test_empty_terms_still_print_the_exclusions_once(self):
        brief, lines = header_fidelity_case()
        brief.update({"terms_heading": "", "payment_terms": [], "notes_heading": "", "standard_notes": []})
        tmp, _path, sheet, _styles = write_workbook(brief, lines[:2])
        self.addCleanup(tmp.cleanup)
        texts = [cell_value(sheet, node.attrib["r"]) for node in sheet.iter(f"{NS_MAIN}c")]
        for text in EXPECTED_LINES:
            self.assertEqual(texts.count(text), 1, text)


class QuoteExclusionsPdfTest(unittest.TestCase):
    def test_real_libreoffice_pdf_shows_red_exclusions_between_totals_and_terms(self):
        running_in_ci = os.environ.get("CI", "").lower() in {"1", "true", "yes"}
        if not quote.libreoffice_candidates():
            message = "LibreOffice is unavailable locally; hosted CI must render the exclusions PDF."
            if running_in_ci:
                self.fail(message)
            self.skipTest(message)
        import pypdfium2 as pdfium

        brief, lines = header_fidelity_case()
        brief.update({"terms_heading": "Terms & Conditions:", "payment_terms": ["50% deposit on confirmation."]})
        with tempfile.TemporaryDirectory() as tmp:
            xlsx_path = Path(tmp) / "quotation.xlsx"
            pdf_path = Path(tmp) / "quotation.pdf"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, xlsx_path, brief, lines[:3])
            self.assertEqual(quote.libreoffice_pdf_export(xlsx_path, pdf_path), "libreoffice_exported")
            document = pdfium.PdfDocument(str(pdf_path))
            try:
                page = document[0]
                width, height = page.get_size()
                textpage = page.get_textpage()
                text = re.sub(r"\s+", " ", textpage.get_text_range() or "")
                boxes = {phrase: normalized_pdf_phrase_bbox(textpage, phrase) for phrase in EXPECTED_LINES}
                total_box = normalized_pdf_phrase_bbox(textpage, "Total including GST")
                terms_box = normalized_pdf_phrase_bbox(textpage, "Terms & Conditions:")
                scale = 2.0
                image = page.render(scale=scale).to_pil().convert("RGB")
            finally:
                document.close()

        for phrase in EXPECTED_LINES:
            self.assertIn(phrase, text)
        # PDF y grows upwards: totals, then the four lines in order, then the terms.
        tops = [boxes[phrase][3] for phrase in EXPECTED_LINES]
        self.assertLess(tops[0], total_box[1])
        self.assertEqual(tops, sorted(tops, reverse=True))
        self.assertGreater(boxes[EXPECTED_LINES[-1]][1], terms_box[3])
        for phrase, (left, bottom, right, top) in boxes.items():
            self.assertLessEqual(right, width, phrase)
            ink = [
                image.getpixel((x, y))
                for x in range(int(left * scale), int(right * scale))
                for y in range(int((height - top) * scale), int((height - bottom) * scale))
            ]
            # Text ink is every pixel off white. Red text and its anti-aliased edges blend red with
            # white, so they stay red-dominant; black or grey text would be neutral.
            ink = [pixel for pixel in ink if min(pixel) < 200]
            self.assertTrue(ink, phrase)
            red = [pixel for pixel in ink if pixel[0] > 150 and pixel[0] - max(pixel[1], pixel[2]) > 60]
            neutral = [pixel for pixel in ink if max(pixel) - min(pixel) < 40]
            self.assertGreater(len(red) / len(ink), 0.75, phrase)
            self.assertLess(len(neutral) / len(ink), 0.1, phrase)


if __name__ == "__main__":
    unittest.main()
