"""Check the maintained smoke's workbook against independently entered values.

Uses only the standard library. The library accepts an explicit template_path;
the CLI checks the bundled templates/quote-layout/quotation-layout.xlsx geometry.
Descriptions, quantities and prices are accepted fixture inputs.
Currency/tax come from reference config and header text from entered form values,
never the generation request echo.
"""
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def verify_workbook(data, payload, expected_rows, logo, *, template_path=None):
    """Bind values to their own rows, rather than finding numbers anywhere."""
    template_path = template_path or Path(__file__).resolve().parents[1] / "templates/quote-layout/quotation-layout.xlsx"
    money = lambda value: Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None, "Corrupt workbook"
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", NS)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        cells = {}
        for cell in sheet.findall(".//m:sheetData/m:row/m:c", NS):
            value = cell.findtext("m:v", None, NS)
            if cell.get("t") == "s":
                value = shared[int(value)]
            elif cell.get("t") == "inlineStr":
                value = "".join(cell.find("m:is", NS).itertext())
            cells[cell.get("r")] = {"value": value, "type": cell.get("t", ""),
                                    "formula": cell.findtext("m:f", "", NS), "style": int(cell.get("s", "0"))}

        def unique_row(column, text):
            found = [int(ref[len(column):]) for ref, cell in cells.items()
                     if re.fullmatch(column + r"\d+", ref) and cell["value"] == text]
            assert len(found) == 1, "Missing or ambiguous workbook row: " + text
            return found[0]

        def numeric(ref, expected):
            assert cells[ref]["type"] not in ("s", "inlineStr", "str", "e"), "Non-numeric amount: " + ref
            assert Decimal(cells[ref]["value"]) == expected, "Wrong amount in its own row: " + ref

        assert expected_rows and len(expected_rows) == len(payload["line_items"]), "Missing expected rows"
        row_indices, quantity_indices, subtotal = [], [], Decimal(0)
        for row_index, (actual, expected) in enumerate(zip(payload["line_items"], expected_rows)):
            for key in ("description", "quantity", "unit", "price_mode"):
                assert key in actual and key in expected and actual[key] == expected[key], (
                    f"Generation rows differ from accepted edits: row index {row_index}, key {key}"
                )
            index = unique_row("C", expected["description"])
            row_indices.append(index)
            quantity = format(Decimal(str(expected["quantity"])).normalize(), "f")
            # The layout writer wraps long units into consecutive cells in the
            # same item block. Never borrow quantity text from another item.
            quantity_parts = [cells[f"B{index}"]["value"]]
            quantity_indices.append(index)
            continuation = index + 1
            while cells.get(f"B{continuation}", {}).get("value"):
                assert not any(cells.get(f"{column}{continuation}", {}).get("value") is not None
                               for column in ("A", "E")), "Quantity crosses an item boundary"
                quantity_parts.append(cells[f"B{continuation}"]["value"])
                quantity_indices.append(continuation)
                continuation += 1
            assert " ".join(quantity_parts) == quantity + " " + expected["unit"], "Wrong quantity/unit in its own row"
            amount = Decimal(0) if expected["price_mode"] == "Included" else money(Decimal(str(expected["quantity"])) * Decimal(str(expected["unit_price"])))
            numeric(f"E{index}", amount)
            subtotal += amount
        assert len(set(row_indices)) == len(row_indices), "Rows collapsed"

        tax = payload["tax"]
        rate = Decimal(str(tax["rate"]))
        label = tax["label"].upper()
        tax_text = format((rate * 100).normalize(), "f")
        labels = ("Total", label + " " + tax_text + "%", "Total including " + label)
        totals = [unique_row("D", text) for text in labels]
        assert totals[1] == totals[0] + 1 and totals[2] == totals[1] + 1, "Totals block is not contiguous"
        amounts = (subtotal, money(subtotal * rate), subtotal + money(subtotal * rate))
        for index, amount in zip(totals, amounts):
            numeric(f"E{index}", amount)
            assert cells[f"F{index}"]["value"] == payload["currency"], "Wrong total currency"
        start, end = re.fullmatch(r"SUM\(E(\d+):E(\d+)\)", cells[f"E{totals[0]}"]["formula"]).groups()
        assert int(start) <= min(row_indices) <= max(row_indices) <= int(end) < totals[0], "Subtotal excludes a line"
        assert cells[f"E{totals[1]}"]["formula"] == f"ROUND(E{totals[0]}*{rate:.6f},2)", "Wrong tax formula"
        assert cells[f"E{totals[2]}"]["formula"] == f"SUM(E{totals[0]}:E{totals[1]})", "Wrong final total formula"
        assert cells["A6"]["value"] == payload["client"]["name"], "Customer name differs in A6"
        assert cells["A18"]["value"] == payload["project"]["title"], "Project title differs in A18"
        assert any(ref.startswith("B") and cell["value"] == payload["company"]["name"] for ref, cell in cells.items()), "Company name differs in workbook header"

        # The company-side footer follows its signature rule, not arbitrary
        # whole-sheet occurrences of the entered strings on either party's side.
        signature_rows = [int(ref[1:]) for ref, cell in cells.items()
                          if re.fullmatch(r"B\d+", ref) and cell["value"] == "_____________________________"]
        assert len(signature_rows) == 1, "Missing or ambiguous company signature rule"
        for offset, key in enumerate(("company_signatory", "company_title"), 1):
            assert cells.get(f"B{signature_rows[0] + offset}", {}).get("value") == payload["signature"][key], "Company signature field differs: " + key

        styles = ET.fromstring(archive.read("xl/styles.xml"))
        xfs, fonts = styles.find("m:cellXfs", NS), styles.find("m:fonts", NS)
        for ref in ("A20", "B20", "C20", "E20"):
            xf = xfs[cells[ref]["style"]]
            assert fonts[int(xf.get("fontId", "0"))].find("m:b", NS) is not None, "Header is not bold"
        # The selected quotation layout's rules require centred quantities and
        # right-aligned estimates, including the table headers.
        for ref, alignment in (("B20", "center"), ("E20", "right")):
            node = xfs[cells[ref]["style"]].find("m:alignment", NS)
            assert node is not None and node.get("horizontal") == alignment, "Wrong header alignment: " + ref
        for index in quantity_indices:
            assert xfs[cells[f"B{index}"]["style"]].find("m:alignment", NS).get("horizontal") == "center", "Quantity is not centred"
        for index in row_indices + totals:
            xf = xfs[cells[f"E{index}"]["style"]]
            assert xf.get("numFmtId") == "4" and xf.find("m:alignment", NS).get("horizontal") == "right", "Money format changed"
        columns = sheet.find("m:cols", NS)
        assert all(any(int(col.get("min")) <= index <= int(col.get("max")) and col.get("hidden") == "1" for col in columns) for index in (7, 8)), "Internal columns exposed"

        drawing = ET.fromstring(archive.read("xl/drawings/drawing1.xml"))
        pictures = [anchor for anchor in drawing if anchor.find("xdr:pic/xdr:nvPicPr/xdr:cNvPr", NS) is not None
                    and anchor.find("xdr:pic/xdr:nvPicPr/xdr:cNvPr", NS).get("name") == "Header Logo"]
        assert len(pictures) == 1, "Missing original header logo"
        picture = pictures[0]
        embed = picture.find("xdr:pic/xdr:blipFill/a:blip", NS).get("{" + NS["r"] + "}embed")
        relationships = ET.fromstring(archive.read("xl/drawings/_rels/drawing1.xml.rels"))
        relation = next(node for node in relationships if node.get("Id") == embed)
        assert relation.get("Target") == "../media/header_logo.png", "Header logo relationship target differs"
        image = archive.read("xl/media/header_logo.png")
        assert len(image) == logo["bytes"] and hashlib.sha256(image).hexdigest() == logo["sha256"], "Embedded logo differs from uploaded bytes"
        headers = [anchor for anchor in drawing if anchor.find("xdr:sp/xdr:txBody", NS) is not None
                   and payload["company"]["header_details"] in "".join(anchor.find("xdr:sp/xdr:txBody", NS).itertext())]
        assert len(headers) == 1, "Missing header details"
        header = headers[0]
        assert "Project No: " + payload["project_number"] in "".join(header.itertext()), "Project number differs in header details"
        assert header.find("xdr:sp/xdr:txBody/a:bodyPr", NS).get("anchor") == "t", "Header text vertical anchor differs"
        assert all(run.get("sz") == "900" for run in header.findall("xdr:sp/xdr:txBody/a:p/a:r/a:rPr", NS)), "Header text run font size differs"
        assert int(header.findtext("xdr:from/xdr:row", namespaces=NS)) > int(picture.findtext("xdr:to/xdr:row", namespaces=NS)), "Header overlaps logo"
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        print_area = workbook.find("m:definedNames/m:definedName[@name='_xlnm.Print_Area']", NS).text
        last_used = max(int(re.search(r"\d+", ref)[0]) for ref, cell in cells.items() if cell["value"] is not None)
        assert print_area == f"Quotation!$A$1:$I${last_used + 1}", "Print area changed"
        area = re.search(r"!\$([A-Z]+)\$(\d+):\$([A-Z]+)\$(\d+)$", print_area)
        assert area is not None, "Invalid print area bounds"

        def column_index(letters):
            number = 0
            for letter in letters:
                number = number * 26 + ord(letter) - ord("A") + 1
            return number - 1  # DrawingML columns are zero-based.

        first_column, last_column = column_index(area[1]), column_index(area[3])
        first_row, last_row = int(area[2]) - 1, int(area[4]) - 1
        # Header/logo placement belongs to the selected layout. Read its source
        # geometry rather than accepting two equally misplaced generated shapes.
        with zipfile.ZipFile(template_path) as template:
            template_drawing = ET.fromstring(template.read("xl/drawings/drawing1.xml"))
        template_header = next(anchor for anchor in template_drawing if anchor.find("xdr:sp", NS) is not None)
        for anchor in (picture, header):
            start_column = int(anchor.findtext("xdr:from/xdr:col", namespaces=NS))
            end_column = int(anchor.findtext("xdr:to/xdr:col", namespaces=NS))
            start_row = int(anchor.findtext("xdr:from/xdr:row", namespaces=NS))
            end_row = int(anchor.findtext("xdr:to/xdr:row", namespaces=NS))
            assert first_column <= start_column <= end_column <= last_column, "Header/logo anchor outside print area columns"
            assert first_row <= start_row <= end_row <= last_row, "Header/logo anchor outside print area rows"
        assert header.findtext("xdr:from/xdr:col", namespaces=NS) == picture.findtext("xdr:from/xdr:col", namespaces=NS), "Header details are not aligned to logo"
        # The template contains the details shape; the logo is inserted above
        # it with image-dependent height. Bind both columns to that source shape
        # and the header rows exactly, retaining the logo/no-overlap check above.
        for anchor in (picture, header):
            for marker in ("from", "to"):
                for axis in (("col", "row") if anchor is header else ("col",)):
                    coordinate = f"xdr:{marker}/xdr:{axis}"
                    assert anchor.findtext(coordinate, namespaces=NS) == template_header.findtext(coordinate, namespaces=NS), "Header/logo anchor differs from selected template: " + marker + "/" + axis
    return {"status": "valid", "rows_bound": len(row_indices), "totals_bound": len(totals), "original_logo": True}


if __name__ == "__main__":
    expected = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(json.dumps(verify_workbook(Path(sys.argv[1]).read_bytes(), expected["payload"], expected["rows"], expected["logo"])))
