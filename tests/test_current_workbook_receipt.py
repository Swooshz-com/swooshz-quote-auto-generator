"""Workbook receipt checks use real generator output and hostile ZIP edits."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET

from scripts import generate_quote as generator
from scripts.verify_current_workbook import NS, verify_workbook

ROOT = Path(__file__).resolve().parents[1]


class CurrentWorkbookReceiptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "_tmp").mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="wb-", dir=ROOT / "_tmp")
        cls.logo = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
        cls.logo_receipt = {"bytes": len(cls.logo), "sha256": hashlib.sha256(cls.logo).hexdigest()}
        cls.rows = [
            {"description": "Synthetic measured surface", "quantity": 2, "unit": "sqm", "price_mode": "Priced", "unit_price": 37.5},
            {"description": "Synthetic included fitting", "quantity": 1, "unit": "set", "price_mode": "Included", "unit_price": 18},
        ]
        cls.payload = {"client": {"name": "Synthetic Buyer"}, "project": {"title": "Synthetic Build"},
                       "project_number": "SYNTHETIC-1", "currency": "SGD", "exchange_rate": 1,
                       "tax": {"label": "GST", "rate": 0.09}, "line_items": cls.rows,
                       "company": {"name": "Synthetic Fabrication", "header_details": "Synthetic Header", "header_lines": ["Synthetic Header"],
                                   "logo_data_url": "data:image/png;base64," + base64.b64encode(cls.logo).decode("ascii")},
                       "acceptance": {"text": "Synthetic approval", "person_label": "Name:", "stamp_label": "Company stamp", "date_label": "Date:"},
                       "signature": {"company_signatory": "Synthetic Signatory", "company_title": "Project Manager", "company_date_label": "Date:"}}
        lines = [generator.QuoteLine(
            section="Synthetic", quantity=row["quantity"], unit=row["unit"], description=row["description"],
            pricing_keyword="", display_price="Included" if row["price_mode"] == "Included" else "",
            matched_price=None, amount=0 if row["price_mode"] == "Included" else 75,
            match_status="manual-price", match_candidates=[], price_mode=row["price_mode"],
            unit_price_override=row["unit_price"], pricing_authority={"variant": "manual", "price_currency": "SGD"}) for row in cls.rows]
        cls.lines = lines
        file = Path(cls.temporary.name) / "quotation.xlsx"
        generator.write_quote_layout_xlsx(ROOT / "templates/quote-layout/quotation-layout.xlsx", file, cls.payload, lines)
        cls.data = file.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def verify(self, data=None):
        return verify_workbook(self.data if data is None else data, self.payload, self.rows, self.logo_receipt)

    def mutate(self, member, edit):
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(self.data)) as source, zipfile.ZipFile(out, "w") as target:
            for name in source.namelist():
                contents = source.read(name)
                if name == member:
                    tree = ET.fromstring(contents)
                    edit(tree)
                    contents = ET.tostring(tree)
                target.writestr(name, contents)
        return out.getvalue()

    def row_cell(self, tree, description, column):
        row = next(row for row in tree.findall(".//m:sheetData/m:row", NS) if description in "".join(row.itertext()))
        return next(cell for cell in row if cell.get("r") == column + row.get("r"))

    def test_real_generated_workbook_binds_rows_totals_styles_and_original_logo(self):
        self.assertEqual(self.verify(), {"status": "valid", "rows_bound": 2, "totals_bound": 3, "original_logo": True})

    def test_request_row_mismatches_name_index_and_key_without_values(self):
        for index in range(len(self.rows)):
            for key in ("description", "quantity", "unit", "price_mode"):
                for missing in (False, True):
                    with self.subTest(index=index, key=key, missing=missing):
                        rows = [dict(row) for row in self.rows]
                        if missing:
                            rows[index].pop(key)
                        else:
                            rows[index][key] = "private-value-must-not-appear"
                        with self.assertRaises(AssertionError) as error:
                            verify_workbook(self.data, {**self.payload, "line_items": rows}, self.rows, self.logo_receipt)
                        self.assertEqual(str(error.exception),
                                         f"Generation rows differ from accepted edits: row index {index}, key {key}")

    def test_cli_reads_expectations_from_file_instead_of_a_large_command_argument(self):
        expectations = Path(self.temporary.name) / "expectations.json"
        payload = {**self.payload, "images": [{"synthetic_unused_media": "x" * 40000}]}
        expectations.write_text(json.dumps({"payload": payload, "rows": self.rows, "logo": self.logo_receipt}), encoding="utf-8")
        result = subprocess.run([sys.executable, "scripts/verify_current_workbook.py",
                                 str(Path(self.temporary.name) / "quotation.xlsx"), str(expectations)],
                                cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.verify())

    def test_swapped_line_amounts_reject_even_when_all_original_numbers_remain(self):
        def swap(tree):
            priced = self.row_cell(tree, self.rows[0]["description"], "E").find("m:v", NS)
            included = self.row_cell(tree, self.rows[1]["description"], "E").find("m:v", NS)
            priced.text, included.text = included.text, priced.text
        with self.assertRaisesRegex(AssertionError, "Wrong amount"):
            self.verify(self.mutate("xl/worksheets/sheet1.xml", swap))

    def test_company_signature_fields_are_bound_to_footer_order(self):
        for mutation, field in (('swap', 'company_signatory'),
                                ('blank', 'company_signatory'), ('replace', 'company_signatory'),
                                ('blank', 'company_title'), ('replace', 'company_title')):
            with self.subTest(mutation=mutation, field=field):
                def edit(tree):
                    texts = {key: next(node for cell in tree.findall('.//m:c', NS)
                                       for node in cell.findall('.//m:t', NS)
                                       if node.text == value)
                             for key, value in self.payload['signature'].items()
                             if key != 'company_date_label'}
                    signer, title = texts['company_signatory'], texts['company_title']
                    if mutation == 'swap':
                        signer.text, title.text = title.text, signer.text
                    else:
                        # Preserve the original text elsewhere, so a whole-sheet
                        # membership search cannot supply the failure verdict.
                        row = tree.find('.//m:row', NS)
                        cell = ET.SubElement(row, '{' + NS['m'] + '}c', {'r': 'I1', 't': 'inlineStr'})
                        ET.SubElement(ET.SubElement(cell, '{' + NS['m'] + '}is'), '{' + NS['m'] + '}t').text = texts[field].text
                        texts[field].text = '' if mutation == 'blank' else 'Other synthetic footer value'
                with self.assertRaisesRegex(AssertionError, 'Company signature field differs: ' + field):
                    self.verify(self.mutate('xl/worksheets/sheet1.xml', edit))

    def test_wrong_quantity_in_the_correct_description_row_rejects(self):
        def edit(tree):
            cell = self.row_cell(tree, self.rows[0]["description"], "B")
            cell.find(".//m:t", NS).text = "1 sqm"
        with self.assertRaisesRegex(AssertionError, "quantity/unit"):
            self.verify(self.mutate("xl/worksheets/sheet1.xml", edit))

    def test_cached_correct_total_does_not_hide_broken_formula_or_currency(self):
        for field in ("formula", "currency"):
            with self.subTest(field=field):
                def edit(tree):
                    if field == "formula":
                        self.row_cell(tree, "Total including GST", "E").find("m:f", NS).text = "SUM(E1:E2)"
                    else:
                        self.row_cell(tree, "Total including GST", "F").find(".//m:t", NS).text = "USD"
                with self.assertRaises(AssertionError):
                    self.verify(self.mutate("xl/worksheets/sheet1.xml", edit))

    def test_logo_identity_and_print_area_are_checked(self):
        with self.assertRaisesRegex(AssertionError, "logo differs"):
            verify_workbook(self.data, self.payload, self.rows, {"bytes": len(self.logo), "sha256": "0" * 64})
        def edit(tree):
            tree.find("m:definedNames/m:definedName[@name='_xlnm.Print_Area']", NS).text = "Quotation!$A$1:$E$20"
        with self.assertRaisesRegex(AssertionError, "Print area"):
            self.verify(self.mutate("xl/workbook.xml", edit))

    def test_drawing_failures_name_only_the_affected_field(self):
        def relationship(tree):
            next(node for node in tree if node.get('Target') == '../media/header_logo.png').set('Target', '../media/private-canary.png')

        def vertical(tree):
            tree.find('.//xdr:sp/xdr:txBody/a:bodyPr', NS).set('anchor', 'b')

        def font(tree):
            tree.find('.//xdr:sp/xdr:txBody/a:p/a:r/a:rPr', NS).set('sz', '1200')

        for member, edit, message in (
                ('xl/drawings/_rels/drawing1.xml.rels', relationship, 'Header logo relationship target differs'),
                ('xl/drawings/drawing1.xml', vertical, 'Header text vertical anchor differs'),
                ('xl/drawings/drawing1.xml', font, 'Header text run font size differs')):
            with self.subTest(message=message), self.assertRaises(AssertionError) as caught:
                self.verify(self.mutate(member, edit))
            self.assertEqual(str(caught.exception), message)

    def test_exposed_internal_columns_reject(self):
        def edit(tree):
            for column in tree.find("m:cols", NS):
                column.attrib.pop("hidden", None)
        with self.assertRaisesRegex(AssertionError, "Internal columns"):
            self.verify(self.mutate("xl/worksheets/sheet1.xml", edit))

    def test_wrong_context_shared_by_request_and_workbook_rejects_entered_expectations(self):
        # Generate each wrong workbook with the same wrong inputs a stale browser
        # request would carry. Verification still uses the independently entered
        # text and reference currency/tax, never that corrupted request echo.
        for path, wrong in ((('currency',), 'USD'), (('tax', 'rate'), 0.12),
                            (('tax', 'label'), 'VAT'), (('company', 'name'), 'Wrong company'),
                            (('company', 'header_details'), 'Wrong header')):
            with self.subTest(path=path):
                request = copy.deepcopy(self.payload)
                target = request
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = wrong
                if path == ('company', 'header_details'):
                    request['company']['header_lines'] = [wrong]
                file = Path(self.temporary.name) / 'wrong-context.xlsx'
                generator.write_quote_layout_xlsx(ROOT / 'templates/quote-layout/quotation-layout.xlsx',
                                                 file, request, self.lines)
                with self.assertRaises(AssertionError):
                    self.verify(file.read_bytes())

    def test_header_or_logo_anchor_outside_print_columns_rejects(self):
        for shape in ('sp', 'pic'):
            for marker in ('from', 'to'):
                with self.subTest(shape=shape, marker=marker):
                    def edit(tree):
                        anchor = next(node for node in tree if node.find('xdr:' + shape, NS) is not None)
                        anchor.find('xdr:' + marker + '/xdr:col', NS).text = '14'
                    with self.assertRaisesRegex(AssertionError, 'outside print area columns'):
                        self.verify(self.mutate('xl/drawings/drawing1.xml', edit))
        def misalign(tree):
            header = next(node for node in tree if node.find('xdr:sp', NS) is not None)
            header.find('xdr:from/xdr:col', NS).text = '7'
        with self.assertRaisesRegex(AssertionError, 'not aligned to logo'):
            self.verify(self.mutate('xl/drawings/drawing1.xml', misalign))

    def test_changed_quantity_or_estimate_header_alignment_rejects(self):
        sheet = ET.fromstring(zipfile.ZipFile(io.BytesIO(self.data)).read('xl/worksheets/sheet1.xml'))
        for ref in ('B20', 'E20'):
            with self.subTest(ref=ref):
                style = int(next(cell for cell in sheet.findall('.//m:c', NS) if cell.get('r') == ref).get('s'))
                def edit(tree):
                    tree.find('m:cellXfs', NS)[style].find('m:alignment', NS).set('horizontal', 'left')
                with self.assertRaisesRegex(AssertionError, 'Wrong header alignment: ' + ref):
                    self.verify(self.mutate('xl/styles.xml', edit))


if __name__ == "__main__":
    unittest.main()
