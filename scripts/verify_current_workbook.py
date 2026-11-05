"""Check the maintained smoke's actual workbook against its generation payload.

Uses only the standard library. Mechanical cell positions describe the selected
quotation layout; descriptions, quantities, prices, tax and currency are inputs.
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


def verify_workbook(data, payload, expected_rows, logo):
    """Bind values to their own rows, rather than finding numbers anywhere."""
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
        for actual, expected in zip(payload["line_items"], expected_rows):
            assert all(actual[key] == expected[key] for key in ("description", "quantity", "unit", "price_mode")), "Generation rows differ from accepted edits"
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
        assert cells["A6"]["value"] == payload["client"]["name"]
        assert cells["A18"]["value"] == payload["project"]["title"]
        assert any(ref.startswith("B") and cell["value"] == payload["company"]["name"] for ref, cell in cells.items())

        styles = ET.fromstring(archive.read("xl/styles.xml"))
        xfs, fonts = styles.find("m:cellXfs", NS), styles.find("m:fonts", NS)
        for ref in ("A20", "B20", "C20", "E20"):
            xf = xfs[cells[ref]["style"]]
            assert fonts[int(xf.get("fontId", "0"))].find("m:b", NS) is not None, "Header is not bold"
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
        assert relation.get("Target") == "../media/header_logo.png"
        image = archive.read("xl/media/header_logo.png")
        assert len(image) == logo["bytes"] and hashlib.sha256(image).hexdigest() == logo["sha256"], "Embedded logo differs from uploaded bytes"
        headers = [anchor for anchor in drawing if anchor.find("xdr:sp/xdr:txBody", NS) is not None
                   and payload["company"]["header_details"] in "".join(anchor.find("xdr:sp/xdr:txBody", NS).itertext())]
        assert len(headers) == 1, "Missing header details"
        header = headers[0]
        assert "Project No: " + payload["project_number"] in "".join(header.itertext())
        assert header.find("xdr:sp/xdr:txBody/a:bodyPr", NS).get("anchor") == "t"
        assert all(run.get("sz") == "900" for run in header.findall("xdr:sp/xdr:txBody/a:p/a:r/a:rPr", NS))
        assert int(header.findtext("xdr:from/xdr:row", namespaces=NS)) > int(picture.findtext("xdr:to/xdr:row", namespaces=NS)), "Header overlaps logo"
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        print_area = workbook.find("m:definedNames/m:definedName[@name='_xlnm.Print_Area']", NS).text
        last_used = max(int(re.search(r"\d+", ref)[0]) for ref, cell in cells.items() if cell["value"] is not None)
        assert print_area == f"Quotation!$A$1:$I${last_used + 1}", "Print area changed"
    return {"status": "valid", "rows_bound": len(row_indices), "totals_bound": len(totals), "original_logo": True}


if __name__ == "__main__":
    expected = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    print(json.dumps(verify_workbook(Path(sys.argv[1]).read_bytes(), expected["payload"], expected["rows"], expected["logo"])))
