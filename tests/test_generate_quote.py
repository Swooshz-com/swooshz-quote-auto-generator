import base64
import csv
import html
import io
import json
import re
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
QUOTE_GENERATOR_FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "quote-generator"
KONCEPT_PROFILE = QUOTE_GENERATOR_FIXTURE_ROOT / "profiles" / "synthetic-exhibition-fixture-template"
KONCEPT_CATALOG = QUOTE_GENERATOR_FIXTURE_ROOT / "pricing-references" / "synthetic-exhibition-fixture-pricing" / "pricing-catalog.json"
KONCEPT_LAYOUT = KONCEPT_PROFILE / "quotation-layout.xlsx"
REPO_DEFAULT_LAYOUT = ROOT / "templates" / "quote-layout" / "quotation-layout.xlsx"
SANITIZED_LOGO_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
SANITIZED_LOGO_DATA_URL = "data:image/png;base64," + base64.b64encode(SANITIZED_LOGO_PNG_BYTES).decode("ascii")
sys.path.insert(0, str(ROOT / "scripts"))

import generate_quote as quote
import build_pricing_catalog as pricing_catalog


NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
NS_CONTENT_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
NS_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
NS_DC = "{http://purl.org/dc/elements/1.1/}"
NS_MC_IGNORABLE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable"
COL_SECTION_NO = 0
COL_DEFAULT_QUANTITY = 1
COL_DESCRIPTION = 2
COL_DEFAULT_ESTIMATE = 5
COL_COST = 7
COL_GST = 8
COL_MARKUP = 9
COL_REMARKS = 11


def cell_value(root, ref):
    for cell in root.iter(f"{NS_MAIN}c"):
        if cell.attrib.get("r") != ref:
            continue
        inline = cell.find(f"{NS_MAIN}is")
        if inline is not None:
            return "".join(t.text or "" for t in inline.iter(f"{NS_MAIN}t"))
        value = cell.find(f"{NS_MAIN}v")
        return value.text if value is not None else ""
    return ""


def find_cell_ref(root, expected):
    for cell in root.iter(f"{NS_MAIN}c"):
        if cell_value(root, cell.attrib.get("r", "")) == expected:
            return cell.attrib.get("r", "")
    return ""


def find_cell_refs(root, expected):
    return [
        cell.attrib.get("r", "")
        for cell in root.iter(f"{NS_MAIN}c")
        if cell_value(root, cell.attrib.get("r", "")) == expected
    ]


def cell(root, ref):
    for item in root.iter(f"{NS_MAIN}c"):
        if item.attrib.get("r") == ref:
            return item
    raise AssertionError(f"Cell {ref} was not written")


def cell_inline_runs(root, ref):
    inline = cell(root, ref).find(f"{NS_MAIN}is")
    if inline is None:
        return []
    runs = inline.findall(f"{NS_MAIN}r")
    if not runs:
        text = inline.find(f"{NS_MAIN}t")
        return [((text.text if text is not None else ""), False, False, False)]
    result = []
    for run in runs:
        text = "".join(text_node.text or "" for text_node in run.findall(f"{NS_MAIN}t"))
        run_props = run.find(f"{NS_MAIN}rPr")
        bold = run_props.find(f"{NS_MAIN}b") if run_props is not None else None
        result.append(
            (
                text,
                bold is not None and bold.attrib.get("val", "1") not in {"0", "false", "False"},
                run_props is not None and run_props.find(f"{NS_MAIN}i") is not None,
                run_props is not None and run_props.find(f"{NS_MAIN}u") is not None,
            )
        )
    return result


def cell_inline_run_fonts(root, ref):
    inline = cell(root, ref).find(f"{NS_MAIN}is")
    if inline is None:
        return []
    result = []
    for run in inline.findall(f"{NS_MAIN}r"):
        text = "".join(text_node.text or "" for text_node in run.findall(f"{NS_MAIN}t"))
        run_props = run.find(f"{NS_MAIN}rPr")
        font = run_props.find(f"{NS_MAIN}rFont") if run_props is not None else None
        size = run_props.find(f"{NS_MAIN}sz") if run_props is not None else None
        result.append((
            text,
            font.attrib.get("val") if font is not None else "",
            size.attrib.get("val") if size is not None else "",
        ))
    return result


def drawing_paragraph_runs(root):
    paragraphs = []
    for paragraph in root.findall(f".//{NS_A}p"):
        runs = []
        for run in paragraph.findall(f"{NS_A}r"):
            text = "".join(text_node.text or "" for text_node in run.findall(f"{NS_A}t"))
            run_props = run.find(f"{NS_A}rPr")
            runs.append(
                (
                    text,
                    run_props is not None and run_props.attrib.get("b") == "1",
                    run_props is not None and run_props.attrib.get("i") == "1",
                    run_props is not None and run_props.attrib.get("u") == "sng",
                )
            )
        if runs:
            paragraphs.append(runs)
    return paragraphs


def drawing_with_picture_xml(name="Product Screenshot", rel_id="rId5"):
    root = ET.Element(f"{NS_DRAWING}wsDr")
    anchor = ET.SubElement(root, f"{NS_DRAWING}twoCellAnchor")
    from_marker = ET.SubElement(anchor, f"{NS_DRAWING}from")
    for tag, value in (("col", "0"), ("colOff", "0"), ("row", "0"), ("rowOff", "0")):
        ET.SubElement(from_marker, f"{NS_DRAWING}{tag}").text = value
    to_marker = ET.SubElement(anchor, f"{NS_DRAWING}to")
    for tag, value in (("col", "1"), ("colOff", "0"), ("row", "1"), ("rowOff", "0")):
        ET.SubElement(to_marker, f"{NS_DRAWING}{tag}").text = value
    pic = ET.SubElement(anchor, f"{NS_DRAWING}pic")
    nv_pic_pr = ET.SubElement(pic, f"{NS_DRAWING}nvPicPr")
    ET.SubElement(nv_pic_pr, f"{NS_DRAWING}cNvPr", {"id": "9", "name": name})
    ET.SubElement(nv_pic_pr, f"{NS_DRAWING}cNvPicPr")
    blip_fill = ET.SubElement(pic, f"{NS_DRAWING}blipFill")
    ET.SubElement(blip_fill, f"{NS_A}blip", {f"{NS_REL}embed": rel_id})
    ET.SubElement(pic, f"{NS_DRAWING}spPr")
    ET.SubElement(anchor, f"{NS_DRAWING}clientData")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def empty_drawing_xml():
    return ET.tostring(ET.Element(f"{NS_DRAWING}wsDr"), encoding="utf-8", xml_declaration=True)


def drawing_rels_xml(*relationships):
    root = ET.Element(f"{NS_PACKAGE_REL}Relationships")
    for rel_id, target in relationships:
        ET.SubElement(
            root,
            f"{NS_PACKAGE_REL}Relationship",
            {
                "Id": rel_id,
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
                "Target": target,
            },
        )
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def empty_content_types_xml():
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types" />'
    )


def content_type_overrides(root):
    return {
        child.attrib.get("PartName"): child.attrib.get("ContentType")
        for child in root.findall(f"{NS_CONTENT_TYPES}Override")
    }


def cell_style(root, ref):
    for cell in root.iter(f"{NS_MAIN}c"):
        if cell.attrib.get("r") == ref:
            return int(cell.attrib["s"])
    raise AssertionError(f"Cell {ref} was not written")


def border_for_style(styles_root, style_id):
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    borders = styles_root.find(f"{NS_MAIN}borders")
    border_id = int(cell_xfs[style_id].attrib.get("borderId", "0"))
    return borders[border_id]


def font_for_style(styles_root, style_id):
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    fonts = styles_root.find(f"{NS_MAIN}fonts")
    font_id = int(cell_xfs[style_id].attrib.get("fontId", "0"))
    return fonts[font_id]


def alignment_for_style(styles_root, style_id):
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    return cell_xfs[style_id].find(f"{NS_MAIN}alignment")


def num_fmt_for_style(styles_root, style_id):
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    return cell_xfs[style_id].attrib.get("numFmtId")


def num_fmt_code_for_style(styles_root, style_id):
    num_fmt_id = num_fmt_for_style(styles_root, style_id)
    num_fmts = styles_root.find(f"{NS_MAIN}numFmts")
    if num_fmts is not None:
        for num_fmt in num_fmts.findall(f"{NS_MAIN}numFmt"):
            if num_fmt.attrib.get("numFmtId") == num_fmt_id:
                return num_fmt.attrib.get("formatCode")
    return num_fmt_id


def font_color(font):
    color = font.find(f"{NS_MAIN}color")
    return color.attrib if color is not None else {}


def font_name(font):
    name = font.find(f"{NS_MAIN}name")
    return name.attrib.get("val") if name is not None else ""


def font_size(font):
    size = font.find(f"{NS_MAIN}sz")
    return size.attrib.get("val") if size is not None else ""


def worksheet_formulas(root):
    return [formula.text or "" for formula in root.iter(f"{NS_MAIN}f")]


def defined_name_text(workbook, name):
    defined_name = workbook.find(f"{NS_MAIN}definedNames/{NS_MAIN}definedName[@name='{name}']")
    return defined_name.text if defined_name is not None else ""


def row_break_ids(sheet):
    row_breaks = sheet.find(f"{NS_MAIN}rowBreaks")
    if row_breaks is None:
        return []
    return [int(brk.attrib["id"]) for brk in row_breaks.findall(f"{NS_MAIN}brk")]


def payload_row_numbers(sheet):
    rows = set()
    for cell in sheet.iter(f"{NS_MAIN}c"):
        has_payload = cell.find(f"{NS_MAIN}f") is not None
        value = cell.find(f"{NS_MAIN}v")
        if value is not None and (value.text or "").strip():
            has_payload = True
        inline = cell.find(f"{NS_MAIN}is")
        if inline is not None and any((text.text or "").strip() for text in inline.iter(f"{NS_MAIN}t")):
            has_payload = True
        if has_payload:
            rows.add(quote.parse_cell_ref(cell.attrib.get("r", "A1"))[0])
    return rows


def no_trailing_blank_print_page(sheet, workbook):
    payload_rows = payload_row_numbers(sheet)
    if not payload_rows:
        return True
    breaks = row_break_ids(sheet)
    last_print = int(defined_name_text(workbook, "_xlnm.Print_Area").rsplit("$", 1)[-1])
    page_starts = [1] + [break_id + 1 for break_id in breaks]
    page_ends = breaks + [last_print]
    last_start, last_end = list(zip(page_starts, page_ends))[-1]
    return any(last_start <= row <= last_end for row in payload_rows)


def column_widths(sheet):
    widths = {}
    cols = sheet.find(f"{NS_MAIN}cols")
    if cols is None:
        return widths
    for col in cols.findall(f"{NS_MAIN}col"):
        min_col = int(col.attrib["min"])
        max_col = int(col.attrib["max"])
        for col_number in range(min_col, max_col + 1):
            widths[col_number] = col.attrib.get("width")
    return widths


def merge_refs(sheet):
    merge_cells = sheet.find(f"{NS_MAIN}mergeCells")
    if merge_cells is None:
        return []
    return [merge_cell.attrib.get("ref") for merge_cell in merge_cells.findall(f"{NS_MAIN}mergeCell")]


def dimension_last_row(sheet):
    dimension = sheet.find(f"{NS_MAIN}dimension")
    ref = dimension.attrib.get("ref", "") if dimension is not None else ""
    match = re.search(r"([0-9]+)$", ref)
    return int(match.group(1)) if match else 0


def style_counts(styles_root):
    counts = {}
    for name in ("cellXfs", "fonts", "fills", "borders", "numFmts"):
        node = styles_root.find(f"{NS_MAIN}{name}")
        counts[name] = int(node.attrib.get("count", len(node))) if node is not None else 0
    return counts


def xml_local_name(node):
    return node.tag.rsplit("}", 1)[-1]


def empty_addressed_cell_refs(path):
    refs = []
    with zipfile.ZipFile(path) as zf:
        for name in sorted(item for item in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", item)):
            sheet = ET.fromstring(zf.read(name))
            for cell_node in sheet.iter(f"{NS_MAIN}c"):
                has_content = (
                    cell_node.find(f"{NS_MAIN}v") is not None
                    or cell_node.find(f"{NS_MAIN}is") is not None
                    or cell_node.find(f"{NS_MAIN}f") is not None
                )
                if cell_node.attrib.get("r") and not has_content:
                    refs.append(f"{name}!{cell_node.attrib['r']}")
    return refs


def excel_col(index):
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def synthetic_sectioned_pricing_workbook_bytes(include_visuals=True):
    catalog = json.loads(KONCEPT_CATALOG.read_text(encoding="utf-8"))
    rows = []
    price_row_numbers = []
    current_section = ""

    for item in catalog["items"]:
        section = item["section"]
        if section != current_section:
            rows.append([item.get("category_order"), None, section])
            current_section = section
        row = [None] * 12
        row[COL_DEFAULT_QUANTITY] = item.get("default_quantity")
        row[COL_DESCRIPTION] = item["description"]
        row[COL_DEFAULT_ESTIMATE] = item.get("default_quote_amount")
        row[COL_COST] = item["internal_cost"]
        row[COL_GST] = item.get("gst_multiplier")
        row[COL_MARKUP] = item["markup_multiplier"]
        row[COL_REMARKS] = "; ".join(item.get("remarks") or [])
        rows.append(row)
        price_row_numbers.append(len(rows))

    def cell_xml(row_number, col_number, value):
        if value in (None, ""):
            return ""
        ref = f"{excel_col(col_number)}{row_number}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{html.escape(str(value))}</t></is></c>'

    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = "".join(cell_xml(row_number, col_number, value) for col_number, value in enumerate(row))
        sheet_rows.append(f'<row r="{row_number}">{cells}</row>')
    drawing = '<drawing r:id="rId1"/>' if include_visuals else ""
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheetData>"
        + "".join(sheet_rows)
        + f"</sheetData>{drawing}</worksheet>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Default Extension="png" ContentType="image/png"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>
</Types>""")
        zf.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")
        zf.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Pricing" sheetId="1" r:id="rId1"/></sheets>
</workbook>""")
        zf.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>""")
        zf.writestr("xl/worksheets/sheet1.xml", worksheet)
        if include_visuals:
            zf.writestr("xl/worksheets/_rels/sheet1.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>
</Relationships>""")
            anchors = []
            rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
            for index, row_number in enumerate(price_row_numbers[:6], start=1):
                rel_id = f"rId{index}"
                zero_based_row = row_number - 1
                anchors.append(f"""
<xdr:twoCellAnchor>
  <xdr:from><xdr:col>3</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{zero_based_row}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
  <xdr:to><xdr:col>4</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>{zero_based_row + 1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
  <xdr:pic><xdr:nvPicPr><xdr:cNvPr id="{index}" name="Synthetic visual {index}"/><xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill><xdr:spPr/></xdr:pic>
  <xdr:clientData/>
</xdr:twoCellAnchor>""")
                rels.append(
                    f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image{index}.png"/>'
                )
                zf.writestr(f"xl/media/image{index}.png", SANITIZED_LOGO_PNG_BYTES)
            rels.append("</Relationships>")
            zf.writestr("xl/drawings/drawing1.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">"""
                + "".join(anchors)
                + "</xdr:wsDr>")
            zf.writestr("xl/drawings/_rels/drawing1.xml.rels", "\n".join(rels))
    return buffer.getvalue()


def manual_print_page_for_row(row_number):
    if row_number <= quote.FIRST_PRINT_PAGE_END_ROW:
        return 1
    return 2 + ((row_number - quote.CONTINUATION_PAGE_START_ROW) // quote.CONTINUATION_PAGE_HEIGHT)


def declared_xml_prefixes(xml_text, root_name):
    match = re.search(rf"<(?:[A-Za-z0-9_.-]+:)?{re.escape(root_name)}\b[^>]*>", xml_text)
    if not match:
        raise AssertionError(f"Could not find <{root_name}> start tag")
    return set(re.findall(r"\sxmlns:([A-Za-z0-9_.-]+)=", match.group(0)))


def logo_data_url():
    return SANITIZED_LOGO_DATA_URL


def generate_layout_workbook(brief_updates=None, layout_template=KONCEPT_LAYOUT):
    brief = {
        "company_identity": "Koncept Image",
        "quote_date": "2026-06-04",
        "project_number": "KI-TEST-001",
        "client": {
            "name": "Sample Client",
            "attention": "Alex Tan",
            "address": ["10 Sample Street", "#02-03 Sample Building"],
        },
        "project": {
            "title": "Sample Project",
        },
        "line_items": [],
        "payment_terms": [],
        "company": {
            "name": "Koncept Image Pte Ltd",
            "header_lines": ["Koncept Image Pte Limited", "61 Kaki Bukit Ave 1"],
            "logo_data_url": logo_data_url(),
        },
        "acceptance": {
            "company_name": "Koncept Image Pte Ltd",
            "text": "We accept the quotation amount and the terms",
            "person_label": "Person in charge",
            "stamp_label": "Company name & stamp",
            "date_label": "Date:",
        },
        "signature": {
            "company_signatory": "Francies Cheng",
            "company_title": "Director",
            "company_date_label": "Date:",
        },
    }
    if brief_updates:
        brief.update(brief_updates)
    price = quote.PriceRow(
        row_number=1,
        section="Graphics",
        description="m2 of vinyl printed graphics",
        unit_hint="sqm",
        cost=100,
        gst_multiplier=1.09,
        markup=1,
        remark="",
    )
    line = quote.QuoteLine(
        section="Graphics",
        quantity=24,
        unit="m length",
        description="Vinyl printed graphics for a long booth fascia",
        pricing_keyword="vinyl printed graphics",
        display_price="",
        matched_price=price,
        amount=2400,
        match_status="matched",
        match_candidates=[],
    )
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / "quotation.xlsx"
    quote.write_quote_layout_xlsx(layout_template, path, brief, [line])
    return tmp, path


class GenerateQuoteRowsTest(unittest.TestCase):
    def test_pdf_generation_is_opt_in_by_default(self):
        with mock.patch.object(sys, "argv", [
            "generate_quote.py",
            "--brief",
            "brief.json",
            "--template",
            str(KONCEPT_CATALOG),
            "--layout-template",
            str(KONCEPT_LAYOUT),
        ]):
            args = quote.parse_args()

        self.assertEqual(args.pdf_mode, "none")

    def test_pricing_catalog_and_layout_paths_are_explicit_cli_inputs(self):
        with self.assertRaises(SystemExit):
            with mock.patch.object(sys, "argv", ["generate_quote.py", "--brief", "brief.json"]):
                quote.parse_args()

        with mock.patch.object(sys, "argv", [
            "generate_quote.py",
            "--brief",
            "brief.json",
            "--template",
            str(KONCEPT_CATALOG),
            "--layout-template",
            str(KONCEPT_LAYOUT),
        ]):
            args = quote.parse_args()

        self.assertEqual(args.template, KONCEPT_CATALOG)
        self.assertEqual(args.layout_template, KONCEPT_LAYOUT)

    def test_xlsx_catalog_builder_collapses_continuation_rows(self):
        rows = [
            [1, None, "Booth Structure"],
            [None, None, "m length single side partition wall at height 2.4m", None, None, 0, None, 180, None, 1.5, None, "Backwall or any partition"],
            [None, None, "wooden construct in painted finished as per design proposal", None, None, None, None, None, None, None, None, "PAINTED"],
            [None, None, "sets of 3D backlit lettering", None, None, 0, None, 400, 1.09, 1.5, None, "Backlit Lettering"],
            [None, None, None, None, None, None, None, None, None, None, None, "NOTE: Per set max 2m long"],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.xlsx"
            quote.write_minimal_xlsx(source, rows)

            catalog = pricing_catalog.build_catalog(source, source_label="source.xlsx")

        self.assertEqual(catalog["schema_version"], 1)
        self.assertNotIn("source", catalog)
        self.assertEqual(len(catalog["items"]), 2)

        wall = catalog["items"][0]
        self.assertEqual(wall["id"], "booth-structure-single-side-partition-wall-at-height-2-4m-wooden-construct-in-painted-finished-as-per-design-proposal")
        self.assertNotIn("source_row", wall)
        self.assertEqual(wall["section"], "Booth Structure")
        self.assertEqual(wall["description"], "m length single side partition wall at height 2.4m; wooden construct in painted finished as per design proposal")
        self.assertEqual(wall["remarks"], ["Backwall or any partition", "PAINTED"])
        self.assertEqual(wall["unit_hint"], "m length")
        self.assertIn("single side partition wall at height 2.4m", wall["aliases"])

        lettering = catalog["items"][1]
        self.assertEqual(lettering["id"], "booth-structure-3d-backlit-lettering")
        self.assertEqual(lettering["remarks"], ["Backlit Lettering", "NOTE: Per set max 2m long"])

        ai_reference_markdown = pricing_catalog.catalog_to_ai_reference_markdown(catalog)
        self.assertNotIn("source row", ai_reference_markdown)
        self.assertIn("Pricing Catalog AI Reference", ai_reference_markdown)
        self.assertIn("m length single side partition wall at height 2.4m; wooden construct in painted finished as per design proposal", ai_reference_markdown)
        self.assertIn("Backwall or any partition; PAINTED", ai_reference_markdown)

    def test_xlsx_catalog_builder_stores_visual_references_next_to_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "synthetic-sectioned-pricing.xlsx"
            out = Path(tmp) / "pricing-catalog.json"
            source.write_bytes(synthetic_sectioned_pricing_workbook_bytes())

            catalog = pricing_catalog.build_catalog(
                source,
                source_label="synthetic-sectioned-pricing.xlsx",
                out=out,
            )

            visual_items = [item for item in catalog["items"] if item.get("visual_references")]
            self.assertGreaterEqual(len(visual_items), 5)
            broken_refs = [
                (item["id"], ref)
                for item in visual_items
                for ref in item["visual_references"]
                if not ref.get("source")
                or not ref.get("path")
                or ref.get("data_url")
                or not (out.parent / ref["path"]).is_file()
            ]
            self.assertEqual(broken_refs, [])
            first_ref = visual_items[0]["visual_references"][0]
            self.assertIn("source", first_ref)
            self.assertIn("path", first_ref)
            self.assertNotIn("data_url", first_ref)
            self.assertTrue((out.parent / first_ref["path"]).is_file())

            ai_reference_markdown = pricing_catalog.catalog_to_ai_reference_markdown(catalog)
            self.assertIn("Visual references:", ai_reference_markdown)

    def test_v11_pricing_workbook_has_no_empty_addressed_cells(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "synthetic-sectioned-pricing.xlsx"
            source.write_bytes(synthetic_sectioned_pricing_workbook_bytes())

            self.assertEqual(empty_addressed_cell_refs(source), [])

    def test_extract_price_rows_reads_json_catalog(self):
        catalog_path = KONCEPT_CATALOG
        rows = quote.extract_price_rows(catalog_path)

        self.assertGreaterEqual(len(rows), 20)
        self.assertEqual(rows[0].pricing_id, "synthetic-floors-synthetic-carpet-tile")
        self.assertEqual(rows[0].section, "Synthetic Floors")
        self.assertEqual(rows[0].description, "sqm synthetic carpet tile")
        self.assertEqual(rows[0].unit_hint, "sqm")
        first_catalog_item = json.loads(catalog_path.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(rows[0].cost, first_catalog_item["internal_cost"])
        self.assertEqual(rows[0].gst_multiplier, 1.0)
        self.assertEqual(rows[0].markup, 1.2)
        self.assertIn("synthetic carpet", rows[0].aliases)

        wall_row = next(row for row in rows if row.pricing_id == "synthetic-structures-synthetic-wall-rail")
        self.assertEqual(wall_row.description, "m length synthetic wall rail")
        self.assertEqual(wall_row.remark, "synthetic wall rail")

        graphics_row = next(row for row in rows if row.pricing_id == "synthetic-graphics-synthetic-printed-wall-graphic")
        self.assertEqual(graphics_row.description, "sqm synthetic printed wall graphic")
        self.assertIn("synthetic print", graphics_row.remark.lower())

        self.assertFalse([row.pricing_id for row in rows if not row.unit_hint])
        truss_row = next(row for row in rows if row.pricing_id == "synthetic-structures-m-synthetic-box-truss")
        self.assertEqual(truss_row.unit_hint, "m")

    def test_catalog_id_pricing_keyword_matches_exact_catalog_item(self):
        rows = quote.extract_price_rows(KONCEPT_CATALOG)

        status, match, _ = quote.find_price_match("synthetic-graphics-synthetic-printed-wall-graphic", rows)

        self.assertEqual(status, "matched")
        self.assertIsNotNone(match)
        self.assertEqual(match.pricing_id, "synthetic-graphics-synthetic-printed-wall-graphic")

    def test_catalog_matching_uses_numeric_size_and_explicit_unit_context(self):
        rows = quote.extract_price_rows(KONCEPT_CATALOG)

        status, match, _ = quote.find_price_match(
            "synthetic spotlight 6 inch",
            rows,
            section="Synthetic Lighting And AV",
            unit="nos",
        )

        self.assertEqual(status, "matched")
        self.assertIsNotNone(match)
        self.assertEqual(
            match.pricing_id,
            "synthetic-lighting-and-av-synthetic-spotlight-6-inch",
        )

        sqm_status, sqm_match, _ = quote.find_price_match(
            "synthetic printed graphic panels",
            rows,
            section="Synthetic Graphics",
            unit="sqm",
        )
        nos_status, nos_match, _ = quote.find_price_match(
            "synthetic printed graphic panels",
            rows,
            section="Synthetic Graphics",
            unit="nos",
        )

        self.assertEqual(sqm_status, "matched")
        self.assertEqual(sqm_match.pricing_id, "synthetic-graphics-synthetic-printed-wall-graphic")
        self.assertEqual(nos_status, "matched")
        self.assertEqual(
            nos_match.pricing_id,
            "synthetic-graphics-synthetic-printed-system-panel",
        )

    def test_generated_styles_declares_ignorable_prefixes_without_excel_repair(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            styles_xml = zf.read("xl/styles.xml").decode("utf-8")
            styles = ET.fromstring(styles_xml)

        declared = declared_xml_prefixes(styles_xml, "styleSheet")
        ignorable = styles.attrib.get(NS_MC_IGNORABLE, "").split()

        self.assertTrue(ignorable)
        self.assertEqual([prefix for prefix in ignorable if prefix not in declared], [])

    def test_generated_worksheet_parts_are_schema_ordered_for_excel(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        order = {name: index for index, name in enumerate(quote.WORKSHEET_CHILD_ORDER)}
        child_names = [xml_local_name(child) for child in list(sheet)]
        child_order = [order[name] for name in child_names if name in order]

        self.assertEqual(child_order, sorted(child_order))
        self.assertLess(child_names.index("dimension"), child_names.index("cols"))
        self.assertLess(child_names.index("cols"), child_names.index("sheetData"))
        if "rowBreaks" in child_names and "drawing" in child_names:
            self.assertLess(child_names.index("rowBreaks"), child_names.index("drawing"))

    def test_test_only_layout_template_keeps_quote_formatting_shell(self):
        with zipfile.ZipFile(KONCEPT_LAYOUT) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))
            names = set(zf.namelist())

        widths = column_widths(sheet)
        rows = sheet.findall(f"{NS_MAIN}sheetData/{NS_MAIN}row")
        counts = style_counts(styles)

        self.assertGreaterEqual(len(rows), 100)
        self.assertEqual(
            sum(1 for row in rows if row.attrib.get("customHeight") == "1"),
            99,
        )
        self.assertEqual(sum(len(row.findall(f"{NS_MAIN}c")) for row in rows), 0)
        self.assertEqual(widths[1], "6.125")
        self.assertEqual(widths[2], "10.5")
        self.assertEqual(widths[3], "49.375")
        self.assertEqual(widths[4], "14")
        self.assertEqual(widths[5], "15.5")
        self.assertIn("A16:C16", merge_refs(sheet))
        self.assertIsNotNone(sheet.find(f"{NS_MAIN}pageMargins"))
        self.assertIsNotNone(sheet.find(f"{NS_MAIN}pageSetup"))
        self.assertIsNone(sheet.find(f"{NS_MAIN}headerFooter"))
        self.assertEqual(counts["cellXfs"], 99)
        self.assertEqual(counts["fonts"], 22)
        self.assertEqual(counts["numFmts"], 8)
        self.assertFalse(any(name.startswith("docProps/") for name in names))
        self.assertNotIn("xl/sharedStrings.xml", names)
        self.assertNotIn("xl/calcChain.xml", names)

    def test_generated_layout_completes_sparse_template_formatting(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        widths = column_widths(sheet)
        rows_by_number = {
            int(row.attrib["r"]): row
            for row in sheet.findall(f"{NS_MAIN}sheetData/{NS_MAIN}row")
        }
        last_row = dimension_last_row(sheet)
        page_setup = sheet.find(f"{NS_MAIN}pageSetup")
        page_margins = sheet.find(f"{NS_MAIN}pageMargins")
        header_footer = sheet.find(f"{NS_MAIN}headerFooter")
        counts = style_counts(styles)

        self.assertIsNotNone(sheet.find(f"{NS_MAIN}sheetPr"))
        self.assertEqual(sheet.find(f"{NS_MAIN}sheetFormatPr").attrib.get("defaultRowHeight"), "17")
        self.assertEqual(widths[1], "6.125")
        self.assertEqual(widths[2], "14.25")
        self.assertEqual(widths[3], "45.5")
        self.assertEqual(widths[4], "22.0")
        self.assertEqual(widths[5], "15.5")
        self.assertEqual(widths[6], "7.625")
        self.assertEqual(widths[7], "15.0")
        self.assertEqual(widths[8], "16.375")
        self.assertEqual(widths[9], "26.875")
        self.assertIn("A16:C16", merge_refs(sheet))
        self.assertEqual(page_setup.attrib.get("scale"), "70")
        self.assertEqual(page_setup.attrib.get("orientation"), "portrait")
        self.assertEqual(page_margins.attrib.get("top"), "0.74803149606299213")
        self.assertEqual(header_footer.attrib.get("alignWithMargins"), "0")
        self.assertEqual(counts["cellXfs"], 123)
        self.assertEqual(counts["fonts"], 37)
        self.assertEqual(counts["numFmts"], 8)
        self.assertGreaterEqual(last_row, 40)
        self.assertEqual(
            [
                row_number
                for row_number in range(1, last_row + 1)
                if rows_by_number.get(row_number) is None
                or rows_by_number[row_number].attrib.get("ht") != quote.QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT
                or rows_by_number[row_number].attrib.get("customHeight") != "1"
            ],
            [],
        )

    def test_repo_default_layout_template_generates_complete_quote_workbook(self):
        tmp, path = generate_layout_workbook(layout_template=REPO_DEFAULT_LAYOUT)
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        self.assertEqual(cell_value(sheet, "A6"), "Sample Client")
        self.assertEqual(cell_value(sheet, "B12"), "Alex Tan")
        self.assertEqual(cell_value(sheet, "A18"), "Sample Project")
        self.assertEqual(cell_value(sheet, "A20"), "Pos.")
        self.assertEqual(cell_value(sheet, "B20"), "Quantity")
        self.assertEqual(cell_value(sheet, "C20"), "Service")
        self.assertEqual(cell_value(sheet, "E20"), "Estimate")
        self.assertEqual(cell_value(sheet, "E21"), "SGD")
        self.assertEqual(cell_value(sheet, "D27"), "Total")
        self.assertEqual(cell_value(sheet, "D28"), "GST 9%")
        self.assertEqual(cell_value(sheet, "D29"), "Total including GST")
        self.assertEqual(cell_value(sheet, "B32"), "Koncept Image Pte Ltd")
        self.assertEqual(cell_value(sheet, "B38"), "Director")

    def test_generated_workbook_normalizes_arial_style_fonts(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        title_font = font_for_style(styles, cell_style(sheet, "A18"))
        self.assertEqual(font_name(title_font), "Calibri")
        self.assertEqual(font_size(title_font), "13")

        fonts = styles.find(f"{NS_MAIN}fonts")
        self.assertEqual([font_size(font) for font in fonts if font_name(font) == "Arial"], [])

    def test_layout_workbook_scrubs_customer_visible_template_metadata(self):
        tmp, path = generate_layout_workbook(layout_template=REPO_DEFAULT_LAYOUT)
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            package_names = set(zf.namelist())
            core_xml = zf.read("docProps/core.xml").decode("utf-8")
            core = ET.fromstring(core_xml)
            root_rels = ET.fromstring(zf.read("_rels/.rels"))
            content_types = ET.fromstring(zf.read("[Content_Types].xml"))
            workbook_xml = zf.read("xl/workbook.xml").decode("utf-8")

        creator = core.find(f"{NS_DC}creator")
        last_modified_by = core.find(f"{NS_CP}lastModifiedBy")
        root_relationships = [
            (rel.attrib.get("Type"), rel.attrib.get("Target"))
            for rel in root_rels.findall(f"{NS_PACKAGE_REL}Relationship")
        ]
        workbook = ET.fromstring(workbook_xml)
        declared = declared_xml_prefixes(workbook_xml, "workbook")
        ignorable = workbook.attrib.get(NS_MC_IGNORABLE, "").split()

        self.assertEqual(creator.text, "Swooshz Quote Generator")
        self.assertEqual(last_modified_by.text, "Swooshz Quote Generator")
        self.assertIn(
            (
                "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                "docProps/core.xml",
            ),
            root_relationships,
        )
        self.assertEqual(
            content_type_overrides(content_types).get("/docProps/core.xml"),
            "application/vnd.openxmlformats-package.core-properties+xml",
        )
        self.assertNotIn("absPath", workbook_xml)
        self.assertNotIn("/Users/", workbook_xml)
        self.assertNotIn("Dropbox", workbook_xml)
        self.assertEqual([prefix for prefix in ignorable if prefix not in declared], [])
        self.assertNotIn("<mc:Choice Requires=\"x15\" />", workbook_xml)
        self.assertNotIn("customXml/sqag-layout-rules.xml", package_names)
        self.assertEqual(
            [
                rel
                for rel in root_relationships
                if (rel[0] or "").rstrip("/").endswith("/customXml")
            ],
            [],
        )

    def test_default_generation_removes_stale_pdf_output(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Sample Project",
            },
            "line_items": [
                {
                    "section": "Furniture",
                    "quantity": 1,
                    "unit": "lot",
                    "description": "Furniture package",
                    "display_price": "Included",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief_path = tmp_path / "brief.json"
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            stale_pdf = out_dir / "quotation.pdf"
            stale_pdf.write_bytes(b"old pdf")
            brief_path.write_text(json.dumps(brief), encoding="utf-8")

            with mock.patch.object(sys, "argv", [
                "generate_quote.py",
                "--brief",
                str(brief_path),
                "--out",
                str(out_dir),
                "--template",
                str(KONCEPT_CATALOG),
                "--layout-template",
                str(KONCEPT_LAYOUT),
            ]):
                self.assertEqual(quote.main(), 0)

            self.assertTrue((out_dir / "quotation.xlsx").exists())
            self.assertFalse(stale_pdf.exists())

    def test_workbook_pdf_mode_exports_generated_xlsx_without_fallback(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Sample Project",
            },
            "line_items": [
                {
                    "section": "Furniture",
                    "quantity": 1,
                    "unit": "lot",
                    "description": "Furniture package",
                    "display_price": "Included",
                }
            ],
        }
        exported_paths = []
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief_path = tmp_path / "brief.json"
            out_dir = tmp_path / "out"
            brief_path.write_text(json.dumps(brief), encoding="utf-8")

            def fake_export(xlsx_path, pdf_path):
                self.assertTrue(xlsx_path.exists())
                self.assertEqual(xlsx_path.name, "quotation.xlsx")
                pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
                exported_paths.append((xlsx_path, pdf_path))
                return "excel_exported"

            with mock.patch.object(sys, "argv", [
                "generate_quote.py",
                "--brief",
                str(brief_path),
                "--out",
                str(out_dir),
                "--template",
                str(KONCEPT_CATALOG),
                "--layout-template",
                str(KONCEPT_LAYOUT),
                "--pdf-mode",
                "workbook",
            ]), mock.patch.object(quote, "export_layout_pdf", side_effect=fake_export), mock.patch.object(
                quote, "write_styled_pdf_fallback", side_effect=AssertionError("fallback PDF renderer should not run")
            ), mock.patch.object(quote, "write_text_pdf", side_effect=AssertionError("text PDF fallback should not run")):
                self.assertEqual(quote.main(), 0)

            self.assertEqual(len(exported_paths), 1)
            self.assertTrue((out_dir / "quotation.xlsx").exists())
            self.assertTrue((out_dir / "quotation.pdf").exists())
            self.assertIn("pdf_mode=workbook", (out_dir / "export_status.txt").read_text(encoding="utf-8"))
            self.assertIn("pdf_status=excel_exported", (out_dir / "export_status.txt").read_text(encoding="utf-8"))

    def test_layout_strips_catalog_brackets_from_customer_output_descriptions(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Sample Project",
            },
            "line_items": [],
        }
        lines = [
            quote.QuoteLine(
                section="Hospitality",
                quantity=4,
                unit="day",
                description="[ Coffee / Tea and supplies for 100 people per day ]",
                pricing_keyword="",
                display_price="Included",
                matched_price=None,
                amount=0,
                match_status="included",
                match_candidates=[],
            ),
            quote.QuoteLine(
                section="Hospitality",
                quantity=1,
                unit="lot",
                description="[ Pantry counter ] - Service counter with lockable storage",
                pricing_keyword="",
                display_price="Included",
                matched_price=None,
                amount=0,
                match_status="included",
                match_candidates=[],
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        self.assertTrue(find_cell_ref(sheet, "Coffee / Tea and supplies for 100 people per day"))
        self.assertTrue(find_cell_ref(sheet, "Pantry counter - Service counter with lockable storage"))
        self.assertFalse(find_cell_ref(sheet, "[ Coffee / Tea and supplies for 100 people per day ]"))

    def test_unresolved_manual_display_placeholder_blocks_quotation_output(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Sample Project",
            },
            "line_items": [
                {
                    "section": "Furniture Rental",
                    "quantity": 4,
                    "unit": "nos",
                    "description": "Round cafe tables for seating clusters",
                    "display_price": "Manual display price",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            brief_path = tmp_path / "brief.json"
            out_dir = tmp_path / "out"
            brief_path.write_text(json.dumps(brief), encoding="utf-8")

            with mock.patch.object(sys, "argv", [
                "generate_quote.py",
                "--brief",
                str(brief_path),
                "--out",
                str(out_dir),
                "--template",
                str(KONCEPT_CATALOG),
                "--layout-template",
                str(KONCEPT_LAYOUT),
            ]):
                self.assertEqual(quote.main(), 2)

            self.assertTrue((out_dir / "pricing_matches.csv").exists())
            self.assertFalse((out_dir / "quotation.xlsx").exists())

    def test_build_quote_rows_preserves_display_price_text(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Sample Project",
            },
            "line_items": [],
            "payment_terms": [],
        }
        line = quote.QuoteLine(
            section="Furniture Rental",
            quantity=1,
            unit="lot",
            description="Artificial green wall panels and potted plants",
            pricing_keyword="",
            display_price="Included",
            matched_price=None,
            amount=None,
            match_status="manual-display",
            match_candidates=[],
        )

        rows = quote.build_quote_rows(brief, [line])

        self.assertTrue(any(row and row[-1] == "Included" for row in rows))

    def test_build_quote_rows_includes_zero_tax_row_from_quote_config(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "currency": "IDR",
            "tax": {"label": "VAT", "rate": 0},
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Sample Project",
            },
            "line_items": [],
            "payment_terms": [],
        }
        line = quote.QuoteLine(
            section="Graphics",
            quantity=1,
            unit="lot",
            description="Printed backdrop",
            pricing_keyword="printed backdrop",
            display_price="",
            matched_price=None,
            amount=120,
            match_status="manual",
            match_candidates=[],
        )

        rows = quote.build_quote_rows(brief, [line])
        pdf_lines = quote.build_pdf_lines(rows)

        self.assertIn(["", "", "Total", "120.00", "IDR"], rows)
        self.assertIn(["", "", "VAT 0%", "0.00", "IDR"], rows)
        self.assertIn(["", "", "Total including VAT", "120.00", "IDR"], rows)
        self.assertTrue(any("VAT 0%  0.00  IDR" in line for line in pdf_lines))

    def test_pdf_cell_map_includes_zero_tax_row_from_quote_config(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "currency": "IDR",
            "tax": {"label": "VAT", "rate": 0},
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Sample Project",
            },
            "line_items": [],
            "payment_terms": [],
        }
        line = quote.QuoteLine(
            section="Graphics",
            quantity=1,
            unit="lot",
            description="Printed backdrop",
            pricing_keyword="printed backdrop",
            display_price="",
            matched_price=None,
            amount=120,
            match_status="manual",
            match_candidates=[],
        )

        cells = quote.build_pdf_cell_map(brief, [line])

        self.assertEqual(cells[(92, 5)], 120)
        self.assertEqual(cells[(93, 4)], "VAT 0%")
        self.assertEqual(cells[(93, 5)], 0)
        self.assertEqual(cells[(93, 6)], "IDR")
        self.assertEqual(cells[(94, 4)], "Total including VAT")
        self.assertEqual(cells[(94, 5)], 120)

    def test_unresolved_manual_display_rows_require_confirmation(self):
        unresolved = quote.QuoteLine(
            section="Furniture Rental",
            quantity=4,
            unit="nos",
            description="Round cafe tables for seating clusters",
            pricing_keyword="",
            display_price="",
            matched_price=None,
            amount=None,
            match_status="manual-display",
            match_candidates=[],
        )
        placeholder = quote.QuoteLine(
            section="Furniture Rental",
            quantity=4,
            unit="nos",
            description="Round cafe tables for seating clusters",
            pricing_keyword="",
            display_price="Manual display price",
            matched_price=None,
            amount=None,
            match_status="manual-display",
            match_candidates=[],
        )
        included = quote.QuoteLine(
            section="Furniture Rental",
            quantity=1,
            unit="lot",
            description="Furniture package included in package",
            pricing_keyword="",
            display_price="Included",
            matched_price=None,
            amount=None,
            match_status="manual-display",
            match_candidates=[],
        )

        issues = quote.confirmation_issues([], [unresolved])
        placeholder_issues = quote.confirmation_issues([], [placeholder])
        confirmed_issues = quote.confirmation_issues([], [included])

        self.assertIn(
            "Manual display pricing required: Round cafe tables for seating clusters / enter a display price, choose a catalog keyword, or remove this line",
            issues,
        )
        self.assertIn(
            "Manual display pricing required: Round cafe tables for seating clusters / enter a display price, choose a catalog keyword, or remove this line",
            placeholder_issues,
        )
        self.assertEqual(confirmed_issues, [])

    def test_suspicious_one_metre_structural_rows_require_quantity_review(self):
        rows = [
            quote.PriceRow(
                row_number=1,
                section="Booth Structure",
                description="m length top fascia structure at height 3.99m",
                unit_hint="m length",
                cost=250,
                gst_multiplier=1.0,
                markup=1.5,
                remark="",
            )
        ]
        brief = {
            "line_items": [{
                "section": "Booth Structure",
                "quantity": 1,
                "unit": "m",
                "description": "Top fascia branding structure integrated above the booth perimeter",
                "pricing_keyword": "top fascia structure",
            }]
        }

        [line] = quote.prepare_lines(brief, rows, allow_ambiguous=True)
        issues = quote.confirmation_issues([], [line])

        self.assertEqual(line.match_status, "quantity-review")
        self.assertIsNone(line.matched_price)
        self.assertIsNone(line.amount)
        self.assertIn(
            "Quantity needs review: Top fascia branding structure integrated above the booth perimeter / confirm measured quantity before pricing",
            issues,
        )

    def test_exact_catalog_id_one_metre_structural_row_stays_priced(self):
        rows = quote.extract_price_rows(KONCEPT_CATALOG)
        partition_keyword = "synthetic-structures-synthetic-double-side-partition"
        partition_description = "m length synthetic double side partition"
        brief = {
            "line_items": [{
                "section": "Booth Structure",
                "quantity": 1,
                "unit": "m length",
                "description": partition_description,
                "pricing_keyword": partition_keyword,
            }]
        }

        [line] = quote.prepare_lines(brief, rows, allow_ambiguous=True)

        self.assertEqual(line.match_status, "matched")
        self.assertIsNotNone(line.matched_price)
        self.assertEqual(line.matched_price.pricing_id, partition_keyword)
        self.assertEqual(line.matched_price.sale_unit_price, 40.25)
        self.assertEqual(line.amount, 40.25)

    def test_fractional_information_counter_requires_quantity_review(self):
        rows = [
            quote.PriceRow(
                row_number=1,
                section="COUNTERS AND CABINETS",
                description="nos. of 1m length x 1m height x 0.5m Width lockable information counter",
                unit_hint="nos",
                cost=800,
                gst_multiplier=1.0,
                markup=1.5,
                remark="",
                pricing_id="counters.lockable-information-counter",
                aliases=["lockable information counter"],
            )
        ]
        brief = {
            "line_items": [{
                "section": "COUNTERS AND CABINETS",
                "quantity": 0.5,
                "unit": "m length",
                "description": "0.5m length lockable information counter",
                "pricing_keyword": "lockable information counter",
            }]
        }

        [line] = quote.prepare_lines(brief, rows, allow_ambiguous=True)

        self.assertEqual(line.match_status, "quantity-review")
        self.assertIsNone(line.matched_price)
        self.assertIsNone(line.amount)

    def test_structure_sections_are_itemized_by_default(self):
        lines = [
            quote.QuoteLine(
                section="Booth Structure",
                quantity=2,
                unit="m length",
                description="Painted partition wall",
                pricing_keyword="booth-structure.partition-wall",
                display_price="",
                matched_price=None,
                amount=300,
                match_status="matched",
                match_candidates=[],
            ),
            quote.QuoteLine(
                section="Booth Structure",
                quantity=1,
                unit="each",
                description="Painted support column",
                pricing_keyword="booth-structure.support-column",
                display_price="",
                matched_price=None,
                amount=120,
                match_status="matched",
                match_candidates=[],
            ),
        ]

        entries = quote.render_quote_entries(lines, {})

        self.assertEqual(entries[0]["kind"], "section")
        self.assertIsNone(entries[0].get("amount"))
        self.assertNotIn("coverage", entries[0])
        self.assertEqual(entries[1]["amount"], 300)
        self.assertEqual(entries[2]["amount"], 120)

    def test_explicit_section_total_mode_still_groups_when_requested(self):
        lines = [
            quote.QuoteLine(
                section="Booth Structure",
                quantity=2,
                unit="m length",
                description="Painted partition wall",
                pricing_keyword="booth-structure.partition-wall",
                display_price="",
                matched_price=None,
                amount=300,
                match_status="matched",
                match_candidates=[],
            ),
            quote.QuoteLine(
                section="Booth Structure",
                quantity=1,
                unit="each",
                description="Painted support column",
                pricing_keyword="booth-structure.support-column",
                display_price="",
                matched_price=None,
                amount=120,
                match_status="matched",
                match_candidates=[],
            ),
        ]

        entries = quote.render_quote_entries(lines, {"section_pricing": {"Booth Structure": "section_total"}})

        self.assertEqual(entries[0]["amount"], 420)
        self.assertEqual(entries[1]["amount"], None)
        self.assertEqual(entries[2]["amount"], None)
        self.assertEqual(entries[0]["coverage"], "Covers line items 1.1 to 1.2")

    def test_layout_keeps_quantity_column_readable_and_signatory_title_visible(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        quantity_col = next(
            col
            for col in sheet.find(f"{NS_MAIN}cols")
            if col.attrib.get("min") == "2" and col.attrib.get("max") == "2"
        )
        self.assertGreaterEqual(float(quantity_col.attrib["width"]), 14.0)
        self.assertTrue(find_cell_ref(sheet, "Director").startswith("B"))

    def test_layout_totals_use_bordered_styles(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            workbook = ET.fromstring(zf.read("xl/workbook.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        total_ref = find_cell_ref(sheet, "Total")
        gst_ref = find_cell_ref(sheet, "GST 9%")
        grand_ref = find_cell_ref(sheet, "Total including GST")
        self.assertEqual(total_ref, "D27")
        self.assertEqual(gst_ref, "D28")
        self.assertEqual(grand_ref, "D29")
        self.assertEqual(cell_value(sheet, "F27"), "SGD")
        self.assertEqual(cell_value(sheet, "F28"), "SGD")
        self.assertEqual(cell_value(sheet, "F29"), "SGD")
        self.assertEqual(cell_value(sheet, "D92"), "")
        self.assertEqual(worksheet_formulas(sheet), ["SUM(E22:E26)", "ROUND(E27*0.090000,2)", "SUM(E27:E28)"])
        self.assertAlmostEqual(float(cell_value(sheet, "E27")), 2400.0)
        self.assertAlmostEqual(float(cell_value(sheet, "E28")), 216.0)
        self.assertAlmostEqual(float(cell_value(sheet, "E29")), 2616.0)

        for ref in ("D27", "E27", "F27"):
            border = border_for_style(styles, cell_style(sheet, ref))
            self.assertEqual(border.find(f"{NS_MAIN}top").attrib.get("style"), "thin")

        for ref in ("D28", "E28", "F28"):
            border = border_for_style(styles, cell_style(sheet, ref))
            self.assertIsNone(border.find(f"{NS_MAIN}top").attrib.get("style"))
            self.assertIsNone(border.find(f"{NS_MAIN}bottom").attrib.get("style"))

        for ref in ("D29", "E29", "F29"):
            border = border_for_style(styles, cell_style(sheet, ref))
            self.assertEqual(border.find(f"{NS_MAIN}top").attrib.get("style"), "thin")
            self.assertEqual(border.find(f"{NS_MAIN}bottom").attrib.get("style"), "double")

        cols = sheet.find(f"{NS_MAIN}cols")
        d_col = next(col for col in cols if col.attrib.get("min") == "4" and col.attrib.get("max") == "4")
        self.assertGreaterEqual(float(d_col.attrib["width"]), 20.0)
        calc_pr = workbook.find(f"{NS_MAIN}calcPr")
        self.assertIsNotNone(calc_pr)
        self.assertEqual(calc_pr.attrib.get("fullCalcOnLoad"), "1")
        self.assertEqual(calc_pr.attrib.get("forceFullCalc"), "1")

    def test_layout_totals_can_display_quote_currency_vat_and_fx_from_quote_config(self):
        tmp, path = generate_layout_workbook({"currency": "MYR", "exchange_rate": 2, "tax": {"label": "VAT", "rate": 0.2}})
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        self.assertEqual(cell_value(sheet, "E21"), "MYR")
        self.assertAlmostEqual(float(cell_value(sheet, "E24")), 4800.0)
        self.assertAlmostEqual(float(cell_value(sheet, "E27")), 4800.0)
        self.assertEqual(cell_value(sheet, "F27"), "MYR")
        self.assertEqual(cell_value(sheet, "F28"), "MYR")
        self.assertEqual(cell_value(sheet, "F29"), "MYR")
        self.assertEqual(find_cell_ref(sheet, "GST 9%"), "")
        self.assertEqual(find_cell_ref(sheet, "VAT 20%"), "D28")
        self.assertEqual(find_cell_ref(sheet, "Total including VAT"), "D29")
        self.assertEqual(worksheet_formulas(sheet), ["SUM(E22:E26)", "ROUND(E27*0.200000,2)", "SUM(E27:E28)"])
        self.assertAlmostEqual(float(cell_value(sheet, "E28")), 960.0)
        self.assertAlmostEqual(float(cell_value(sheet, "E29")), 5760.0)

    def test_layout_totals_show_zero_tax_row_from_quote_config(self):
        tmp, path = generate_layout_workbook({"currency": "IDR", "exchange_rate": 1, "tax": {"label": "VAT", "rate": 0}})
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        total_ref = find_cell_ref(sheet, "Total")
        tax_ref = find_cell_ref(sheet, "VAT 0%")
        grand_ref = find_cell_ref(sheet, "Total including VAT")
        self.assertEqual(total_ref, "D27")
        self.assertEqual(tax_ref, "D28")
        self.assertEqual(grand_ref, "D29")
        self.assertEqual(cell_value(sheet, "F27"), "IDR")
        self.assertEqual(cell_value(sheet, "F28"), "IDR")
        self.assertEqual(cell_value(sheet, "F29"), "IDR")
        self.assertEqual(worksheet_formulas(sheet), ["SUM(E22:E26)", "ROUND(E27*0.000000,2)", "SUM(E27:E28)"])
        self.assertAlmostEqual(float(cell_value(sheet, "E27")), 2400.0)
        self.assertAlmostEqual(float(cell_value(sheet, "E28")), 0.0)
        self.assertAlmostEqual(float(cell_value(sheet, "E29")), 2400.0)
        self.assertEqual(num_fmt_for_style(styles, cell_style(sheet, "E28")), "4")

    def test_layout_tax_rounds_to_cents_not_whole_dollars(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "project_number": "KI-TAX-001",
            "client": {"name": "Sample Client", "attention": "Alex Tan"},
            "project": {"title": "Tax Rounding Booth"},
            "line_items": [],
            "payment_terms": [],
            "tax": {"label": "VAT", "rate": 0.5},
            "company": {
                "name": "Koncept Image Pte Ltd",
                "header_lines": ["Koncept Image Pte Limited"],
                "logo_data_url": logo_data_url(),
            },
            "acceptance": {
                "company_name": "Koncept Image Pte Ltd",
                "text": "We accept the quotation amount and the terms",
                "person_label": "Person in charge",
                "stamp_label": "Company name & stamp",
                "date_label": "Date:",
            },
            "signature": {
                "company_signatory": "Francies Cheng",
                "company_title": "Director",
                "company_date_label": "Date:",
            },
        }
        price = quote.PriceRow(1, "Electrical", "nos. 10W LED Spotlight", "nos", 45, 1.09, 1, "")
        line = quote.QuoteLine("Electrical", 3, "nos", "10W LED Spotlight", "10W LED Spotlight", "", price, 135, "matched", [])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "quotation.xlsx"

        quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, [line])

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        total_row = quote.parse_cell_ref(find_cell_ref(sheet, "Total"))[0]
        tax_row = quote.parse_cell_ref(find_cell_ref(sheet, "VAT 50%"))[0]
        grand_row = quote.parse_cell_ref(find_cell_ref(sheet, "Total including VAT"))[0]
        self.assertEqual(worksheet_formulas(sheet), [f"SUM(E22:E{total_row - 1})", f"ROUND(E{total_row}*0.500000,2)", f"SUM(E{total_row}:E{tax_row})"])
        self.assertAlmostEqual(float(cell_value(sheet, f"E{total_row}")), 135.0)
        self.assertAlmostEqual(float(cell_value(sheet, f"E{tax_row}")), 67.5)
        self.assertAlmostEqual(float(cell_value(sheet, f"E{grand_row}")), 202.5)

    def test_layout_uses_dynamic_print_area_without_forced_extra_pages(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            workbook = ET.fromstring(zf.read("xl/workbook.xml"))

        dimension = sheet.find(f"{NS_MAIN}dimension")
        row_breaks = sheet.find(f"{NS_MAIN}rowBreaks")
        sheet_view = sheet.find(f"{NS_MAIN}sheetViews/{NS_MAIN}sheetView")

        self.assertEqual(defined_name_text(workbook, "_xlnm.Print_Area"), "Quotation!$A$1:$I$40")
        self.assertEqual(dimension.attrib.get("ref"), "A1:I40")
        self.assertIsNone(row_breaks)
        self.assertNotEqual(sheet_view.attrib.get("view"), "pageBreakPreview")
        self.assertEqual(cell_value(sheet, "B38"), "Director")
        self.assertEqual(cell_value(sheet, "B40"), "")
        self.assertFalse([
            item.attrib.get("r")
            for item in sheet.iter(f"{NS_MAIN}c")
            if quote.parse_cell_ref(item.attrib.get("r", "A1"))[0] > 40 and quote.parse_cell_ref(item.attrib.get("r", "A1"))[1] <= 9
        ])

    def test_layout_print_area_trims_trailing_blank_manual_page(self):
        root = ET.Element(f"{NS_MAIN}worksheet")
        ET.SubElement(root, f"{NS_MAIN}sheetData")
        third_break = quote.CONTINUATION_PAGE_START_ROW + (quote.CONTINUATION_PAGE_HEIGHT * 2) - 1
        quote.set_ooxml_cell(root, third_break, 1, "payload")
        expected_breaks = [
            quote.FIRST_PRINT_PAGE_END_ROW,
            quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_PAGE_HEIGHT - 1,
            third_break,
        ]
        self.assertEqual(quote.manual_page_break_ids(third_break + 1), expected_breaks)
        self.assertEqual(quote.printable_last_row(root, third_break + 1, True), third_break)
        self.assertEqual(quote.manual_page_break_ids(quote.printable_last_row(root, third_break + 1, True)), expected_breaks[:2])

    def test_layout_moves_excel_overflow_row_to_continuation_body(self):
        root = ET.Element(f"{NS_MAIN}worksheet")
        ET.SubElement(root, f"{NS_MAIN}sheetData")
        continuation_pages: set[int] = set()

        row_number = quote.ensure_quote_entry_page(
            root,
            65,
            1,
            "Boundary Booth",
            "EUR",
            {"table_header": "1", "header_currency": "2"},
            continuation_pages,
        )

        self.assertEqual(quote.FIRST_PRINT_PAGE_END_ROW, 64)
        self.assertEqual(row_number, quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_BODY_OFFSET)
        self.assertEqual(find_cell_ref(root, "Pos."), f"A{quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_TABLE_HEADER_OFFSET}")

    def test_layout_chunk_moves_to_continuation_body_below_repeated_header(self):
        start_row, moved = quote.layout_chunk_start_row(
            quote.FIRST_PRINT_PAGE_END_ROW - 3,
            quote.LayoutChunk("signature", quote.SIGNATURE_BLOCK_HEIGHT),
        )

        self.assertTrue(moved)
        self.assertEqual(start_row, quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_BODY_OFFSET)

    def test_signature_chunk_can_use_remaining_first_page_rows(self):
        start_row, moved = quote.layout_chunk_start_row(
            quote.FIRST_PRINT_PAGE_END_ROW - 7,
            quote.LayoutChunk("signature", quote.SIGNATURE_BLOCK_HEIGHT),
        )

        self.assertFalse(moved)
        self.assertEqual(start_row, quote.FIRST_PRINT_PAGE_END_ROW - 7)

    def test_layout_extends_quote_table_past_preserved_second_page(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "RE: Large Generated Booth",
            },
            "line_items": [],
            "payment_terms": [],
            "signature": {
                "company_signatory": "Francies Cheng",
                "company_title": "Director",
            },
        }
        price = quote.PriceRow(1, "Generated", "generated booth component", "lot", 100, 1.09, 1, "")
        lines = [
            quote.QuoteLine(
                section=f"Generated Section {(index - 1) // 4 + 1}",
                quantity=1,
                unit="lot",
                description=f"Generated booth component {index}",
                pricing_keyword="generated booth component",
                display_price="",
                matched_price=price,
                amount=100,
                match_status="matched",
                match_candidates=[],
            )
            for index in range(1, 41)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
                workbook = ET.fromstring(zf.read("xl/workbook.xml"))

        last_item_ref = find_cell_ref(sheet, "Generated booth component 40")
        total_ref = find_cell_ref(sheet, "Total")
        last_item_row = quote.parse_cell_ref(last_item_ref)[0]
        total_row = quote.parse_cell_ref(total_ref)[0]

        self.assertGreater(last_item_row, 91)
        self.assertGreater(total_row, last_item_row)
        self.assertEqual(cell_value(sheet, f"F{total_row}"), "SGD")
        self.assertEqual(defined_name_text(workbook, "_xlnm.Print_Area"), f"Quotation!$A$1:$I${total_row + 13}")
        self.assertEqual(
            row_break_ids(sheet)[:2],
            [
                quote.FIRST_PRINT_PAGE_END_ROW,
                quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_PAGE_HEIGHT - 1,
            ],
        )
        self.assertTrue(no_trailing_blank_print_page(sheet, workbook))

    def test_layout_uses_manual_continuation_pages_with_table_headers_only(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "RE: Large Generated Booth",
            },
            "line_items": [],
            "payment_terms": [],
            "signature": {
                "company_signatory": "Francies Cheng",
                "company_title": "Director",
            },
        }
        price = quote.PriceRow(1, "Generated", "generated booth component", "lot", 100, 1.09, 1, "")
        lines = [
            quote.QuoteLine(
                section=f"Generated Section {(index - 1) // 4 + 1}",
                quantity=1,
                unit="lot",
                description=f"Generated booth component {index}",
                pricing_keyword="generated booth component",
                display_price="",
                matched_price=price,
                amount=100,
                match_status="matched",
                match_candidates=[],
            )
            for index in range(1, 57)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
                styles = ET.fromstring(zf.read("xl/styles.xml"))
                workbook = ET.fromstring(zf.read("xl/workbook.xml"))

        header_refs = find_cell_refs(sheet, "Pos.")
        title_refs = find_cell_refs(sheet, "RE: Large Generated Booth")

        continuation_header_1 = quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_TABLE_HEADER_OFFSET
        continuation_header_2 = quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_PAGE_HEIGHT + quote.CONTINUATION_TABLE_HEADER_OFFSET
        self.assertEqual(header_refs[:3], ["A20", f"A{continuation_header_1}", f"A{continuation_header_2}"])
        self.assertEqual(title_refs, ["A18"])
        self.assertEqual(
            row_break_ids(sheet)[:2],
            [
                quote.FIRST_PRINT_PAGE_END_ROW,
                quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_PAGE_HEIGHT - 1,
            ],
        )
        self.assertTrue(no_trailing_blank_print_page(sheet, workbook))
        for ref in ("E20", "E21", f"E{continuation_header_1}", f"E{continuation_header_1 + 1}"):
            self.assertEqual(alignment_for_style(styles, cell_style(sheet, ref)).attrib.get("horizontal"), "right")

    def test_layout_keeps_totals_together_on_one_manual_page(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Totals Boundary Booth",
            },
            "line_items": [],
            "payment_terms": [],
            "signature": {
                "company_signatory": "Francies Cheng",
                "company_title": "Director",
            },
        }
        price = quote.PriceRow(1, "Generated", "generated booth component", "lot", 100, 1.09, 1, "")
        lines = [
            quote.QuoteLine(
                section=f"Generated Section {(index - 1) // 4 + 1}",
                quantity=1,
                unit="lot",
                description=f"Generated booth component {index}",
                pricing_keyword="generated booth component",
                display_price="",
                matched_price=price,
                amount=100,
                match_status="matched",
                match_candidates=[],
            )
            for index in range(1, 41)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        total_row = quote.parse_cell_ref(find_cell_ref(sheet, "Total"))[0]
        gst_row = quote.parse_cell_ref(find_cell_ref(sheet, "GST 9%"))[0]
        grand_row = quote.parse_cell_ref(find_cell_ref(sheet, "Total including GST"))[0]

        self.assertEqual(
            {manual_print_page_for_row(total_row), manual_print_page_for_row(gst_row), manual_print_page_for_row(grand_row)},
            {manual_print_page_for_row(total_row)},
        )

    def test_layout_keeps_acceptance_signature_block_together_on_one_manual_page(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Signature Boundary Booth",
            },
            "line_items": [],
            "payment_terms": [],
            "acceptance": {
                "company_name": "Koncept Image Pte Ltd",
                "text": "We accept the quotation amount and the terms",
                "person_label": "Person in charge",
                "stamp_label": "Company name & stamp",
                "date_label": "Date:",
            },
            "signature": {
                "company_signatory": "Francies Cheng",
                "company_title": "Director",
                "company_date_label": "Date:",
            },
        }
        price = quote.PriceRow(1, "Generated", "generated booth component", "lot", 100, 1.09, 1, "")
        lines = [
            quote.QuoteLine(
                section=f"Generated Section {(index - 1) // 4 + 1}",
                quantity=1,
                unit="lot",
                description=f"Generated booth component {index}",
                pricing_keyword="generated booth component",
                display_price="",
                matched_price=price,
                amount=100,
                match_status="matched",
                match_candidates=[],
            )
            for index in range(1, 34)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
                workbook = ET.fromstring(zf.read("xl/workbook.xml"))

        acceptance_refs = [
            find_cell_ref(sheet, "Koncept Image Pte Ltd"),
            find_cell_ref(sheet, "We accept the quotation amount and the terms"),
            find_cell_ref(sheet, "Francies Cheng"),
            find_cell_ref(sheet, "Director"),
            find_cell_ref(sheet, "Person in charge"),
            find_cell_ref(sheet, "Company name & stamp"),
            find_cell_ref(sheet, "_____________________________"),
            find_cell_ref(sheet, "_____________________________________"),
        ]
        acceptance_rows = [quote.parse_cell_ref(ref)[0] for ref in acceptance_refs]
        acceptance_pages = {manual_print_page_for_row(row) for row in acceptance_rows}

        self.assertEqual(acceptance_pages, {manual_print_page_for_row(acceptance_rows[0])})
        if acceptance_rows[0] > quote.FIRST_PRINT_PAGE_END_ROW:
            self.assertGreaterEqual(acceptance_rows[0], quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_BODY_OFFSET)
        self.assertTrue(no_trailing_blank_print_page(sheet, workbook))

    def test_layout_keeps_section_header_with_first_detail_before_page_break(self):
        brief = {
            "company_identity": "Synthetic Quote Co",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Section Boundary Booth",
            },
            "line_items": [],
            "payment_terms": [],
            "signature": {
                "company_signatory": "Alex Tan",
                "company_title": "Director",
            },
        }
        price = quote.PriceRow(1, "Generated", "generated component", "lot", 100, 1.09, 1, "")
        lines = [
            quote.QuoteLine(
                section=f"Generated Section {(index - 1) // 4 + 1}",
                quantity=1,
                unit="lot",
                description=f"generated component {index}",
                pricing_keyword="generated component",
                display_price="",
                matched_price=price,
                amount=100,
                match_status="matched",
                match_candidates=[],
            )
            for index in range(1, 17)
        ]
        lines.extend([
            quote.QuoteLine(
                section="Logistics and Installation",
                quantity=1,
                unit="lot",
                description="Onsite installation and dismantling for complete custom booth build and rental scope.",
                pricing_keyword="",
                display_price="Included",
                matched_price=None,
                amount=0,
                match_status="included",
                match_candidates=[],
            ),
            quote.QuoteLine(
                section="Logistics and Installation",
                quantity=1,
                unit="lot",
                description="Transportation and handling for booth build materials, graphics, furniture, AV and rental items.",
                pricing_keyword="",
                display_price="Included",
                matched_price=None,
                amount=0,
                match_status="included",
                match_candidates=[],
            ),
        ])

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        section_row = quote.parse_cell_ref(find_cell_ref(sheet, "Logistics and Installation"))[0]
        first_detail_row = quote.parse_cell_ref(
            find_cell_ref(sheet, "Onsite installation and dismantling for complete custom")
        )[0]

        self.assertEqual(manual_print_page_for_row(section_row), manual_print_page_for_row(first_detail_row))
        if first_detail_row > quote.FIRST_PRINT_PAGE_END_ROW:
            self.assertGreaterEqual(first_detail_row, quote.CONTINUATION_PAGE_START_ROW + quote.CONTINUATION_BODY_OFFSET)

    def test_layout_keeps_footer_signature_on_first_page_when_visible_rows_fit(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Footer Fit Booth",
            },
            "line_items": [],
            "terms_heading": "Terms & Conditions:",
            "payment_terms": [
                "70% payment upon confirmation and signing of contract.",
                "30% balance upon handover before show starts",
                "All cheques should be crossed and made payable to Koncept Images Pte. Ltd.",
            ],
            "notes_heading": "Note:",
            "standard_notes": [
                "The above contract does not include application fees to any relevant authorities and electrical connection fees.",
                "Any changes in design during the progress of work will delay completion schedule and it shall be deemed at the cost of the Client.",
                "Any changes agreed upon after the confirmation of contract or during the work in progress shall be deemed as Additional Orders.",
                "All designs and dimensions are subject to final site verification.",
                "For production purpose, quotation must be confirmed minimum 20 working days before date of event",
                "20% surcharge will be implied on the graphic cost, if the graphic files are not received latest by five working days before build up date.",
                "Design and Artwork of the graphics are not included in this contract.",
                "Cancellation of agreement is subject to 75% of the agreement amount.",
                "All deposit are non-refundable upon of cancellation of agreement.",
            ],
            "acceptance": {
                "company_name": "Koncept Images Pte. Ltd.",
                "text": "We accept the quotation amount and the terms",
                "person_label": "Person in charge",
                "stamp_label": "Company name & stamp",
                "date_label": "Date:",
            },
            "signature": {
                "company_signatory": "Francies Cheng",
                "company_title": "Director",
                "company_date_label": "Date:",
            },
        }
        price = quote.PriceRow(1, "Generated", "generated booth component", "lot", 100, 1.09, 1, "")
        lines = [
            quote.QuoteLine(
                section=f"Generated Section {(index - 1) // 3 + 1}",
                quantity=1,
                unit="lot",
                description=f"Generated booth component {index}",
                pricing_keyword="generated booth component",
                display_price="",
                matched_price=price,
                amount=100,
                match_status="matched",
                match_candidates=[],
            )
            for index in range(1, 5)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
                workbook = ET.fromstring(zf.read("xl/workbook.xml"))

        acceptance_rows = [
            quote.parse_cell_ref(find_cell_ref(sheet, "Koncept Images Pte. Ltd."))[0],
            quote.parse_cell_ref(find_cell_ref(sheet, "We accept the quotation amount and the terms"))[0],
            quote.parse_cell_ref(find_cell_ref(sheet, "_____________________________"))[0],
            quote.parse_cell_ref(find_cell_ref(sheet, "Francies Cheng"))[0],
            quote.parse_cell_ref(find_cell_ref(sheet, "Director"))[0],
        ]

        self.assertEqual(find_cell_ref(sheet, "All deposit are non-refundable upon of cancellation of agreement."), "B54")
        self.assertTrue(all(row <= quote.FIRST_PRINT_PAGE_END_ROW for row in acceptance_rows))
        self.assertEqual(row_break_ids(sheet), [])
        self.assertTrue(no_trailing_blank_print_page(sheet, workbook))

    def test_layout_adds_manual_break_when_footer_extends_past_first_page(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "Footer Boundary Booth",
            },
            "line_items": [],
            "terms_heading": "Terms & Conditions:",
            "payment_terms": [
                "70% payment upon confirmation and signing of contract.",
                "30% balance upon handover before show starts",
                "All cheques should be crossed and made payable to Koncept Images Pte. Ltd.",
            ],
            "notes_heading": "Note:",
            "standard_notes": [
                "The above contract does not include application fees to any relevant authorities and electrical connection fees.",
                "Any changes in design during the progress of work will delay completion schedule and it shall be deemed at the cost of the Client.",
                "Any changes agreed upon after the confirmation of contract or during the work in progress shall be deemed as Additional Orders.",
                "All designs and dimensions are subject to final site verification.",
                "For production purpose, quotation must be confirmed minimum 20 working days before date of event",
                "20% surcharge will be implied on the graphic cost, if the graphic files are not received latest by five working days before build up date.",
                "Design and Artwork of the graphics are not included in this contract.",
                "Cancellation of agreement is subject to 75% of the agreement amount.",
                "All deposit are non-refundable upon of cancellation of agreement.",
            ],
            "acceptance": {
                "company_name": "Koncept Images Pte. Ltd.",
                "text": "We accept the quotation amount and the terms",
                "person_label": "Person in charge",
                "stamp_label": "Company name & stamp",
                "date_label": "Date:",
            },
            "signature": {
                "company_signatory": "Francies Cheng",
                "company_title": "Director",
                "company_date_label": "Date:",
            },
        }
        price = quote.PriceRow(1, "Generated", "generated booth component", "lot", 100, 1.09, 1, "")
        lines = [
            quote.QuoteLine(
                section=f"Generated Section {(index - 1) // 3 + 1}",
                quantity=1,
                unit="lot",
                description=f"Generated booth component {index}",
                pricing_keyword="generated booth component",
                display_price="",
                matched_price=price,
                amount=100,
                match_status="matched",
                match_candidates=[],
            )
            for index in range(1, 10)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, lines)

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
                workbook = ET.fromstring(zf.read("xl/workbook.xml"))

        acceptance_rows = [
            quote.parse_cell_ref(find_cell_ref(sheet, "Koncept Images Pte. Ltd."))[0],
            quote.parse_cell_ref(find_cell_ref(sheet, "We accept the quotation amount and the terms"))[0],
            quote.parse_cell_ref(find_cell_ref(sheet, "_____________________________"))[0],
            quote.parse_cell_ref(find_cell_ref(sheet, "Francies Cheng"))[0],
            quote.parse_cell_ref(find_cell_ref(sheet, "Director"))[0],
        ]
        breaks = row_break_ids(sheet)

        self.assertEqual(breaks, [quote.FIRST_PRINT_PAGE_END_ROW])
        self.assertGreater(acceptance_rows[0], quote.FIRST_PRINT_PAGE_END_ROW)
        self.assertTrue(all(row > breaks[0] for row in acceptance_rows))
        self.assertTrue(no_trailing_blank_print_page(sheet, workbook))

    def test_empty_terms_and_notes_do_not_insert_default_rows(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        self.assertEqual(find_cell_ref(sheet, "Terms & Conditions:"), "")
        self.assertEqual(find_cell_ref(sheet, "Note:"), "")
        self.assertEqual(find_cell_ref(sheet, "All cheques should be crossed and made payable to Koncept Image Pte Ltd"), "")
        self.assertEqual(
            find_cell_ref(sheet, "The above contract does not include application fees to any relevant authorities and electrical connection fees."),
            "",
        )
        self.assertEqual(cell_value(sheet, "B32"), "Koncept Image Pte Ltd")
        self.assertEqual(cell_value(sheet, "E32"), "We accept the quotation amount and the terms")

    def test_company_detail_drawing_is_wide_and_top_aligned(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            drawing = ET.fromstring(zf.read("xl/drawings/drawing1.xml"))

        text_anchor = next(
            anchor
            for anchor in drawing.findall(f"{NS_DRAWING}twoCellAnchor")
            if anchor.find(f"{NS_DRAWING}sp") is not None
        )
        from_col = int(text_anchor.find(f"{NS_DRAWING}from/{NS_DRAWING}col").text)
        to_col = int(text_anchor.find(f"{NS_DRAWING}to/{NS_DRAWING}col").text)
        body_pr = text_anchor.find(f"{NS_DRAWING}sp/{NS_DRAWING}txBody/{NS_A}bodyPr")

        self.assertGreater(to_col, from_col)
        self.assertEqual(body_pr.attrib.get("anchor"), "t")

    def test_company_detail_drawing_starts_below_logo(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            drawing = ET.fromstring(zf.read("xl/drawings/drawing1.xml"))
            workbook = ET.fromstring(zf.read("xl/workbook.xml"))
            media_names = set(zf.namelist())

        anchors = drawing.findall(f"{NS_DRAWING}twoCellAnchor")
        text_anchor = next(anchor for anchor in anchors if anchor.find(f"{NS_DRAWING}sp") is not None)
        logo_anchor = next(anchor for anchor in anchors if anchor.find(f"{NS_DRAWING}pic") is not None)
        text_from_row = int(text_anchor.find(f"{NS_DRAWING}from/{NS_DRAWING}row").text)
        logo_to_row = int(logo_anchor.find(f"{NS_DRAWING}to/{NS_DRAWING}row").text)
        text_ext = text_anchor.find(f"{NS_DRAWING}sp/{NS_DRAWING}spPr/{NS_A}xfrm/{NS_A}ext")
        text_off = text_anchor.find(f"{NS_DRAWING}sp/{NS_DRAWING}spPr/{NS_A}xfrm/{NS_A}off")
        logo_ext = logo_anchor.find(f"{NS_DRAWING}pic/{NS_DRAWING}spPr/{NS_A}xfrm/{NS_A}ext")
        logo_off = logo_anchor.find(f"{NS_DRAWING}pic/{NS_DRAWING}spPr/{NS_A}xfrm/{NS_A}off")
        text_left = int(text_off.attrib["x"])
        text_top = int(text_off.attrib["y"])
        text_width = int(text_ext.attrib["cx"])
        logo_left = int(logo_off.attrib["x"])
        logo_top = int(logo_off.attrib["y"])
        logo_width = int(logo_ext.attrib["cx"])
        logo_height = int(logo_ext.attrib["cy"])
        text_from_col = int(text_anchor.find(f"{NS_DRAWING}from/{NS_DRAWING}col").text)
        text_to_col = int(text_anchor.find(f"{NS_DRAWING}to/{NS_DRAWING}col").text)
        text_from_col_off = int(text_anchor.find(f"{NS_DRAWING}from/{NS_DRAWING}colOff").text)
        logo_from_col_off = int(logo_anchor.find(f"{NS_DRAWING}from/{NS_DRAWING}colOff").text)
        logo_to_col_off = int(logo_anchor.find(f"{NS_DRAWING}to/{NS_DRAWING}colOff").text)
        logo_from_row_off = int(logo_anchor.find(f"{NS_DRAWING}from/{NS_DRAWING}rowOff").text)
        logo_to_row_off = int(logo_anchor.find(f"{NS_DRAWING}to/{NS_DRAWING}rowOff").text)

        print_titles = workbook.find(f"{NS_MAIN}definedNames/{NS_MAIN}definedName[@name='_xlnm.Print_Titles']")
        self.assertIsNotNone(print_titles)
        self.assertEqual(print_titles.text, "Quotation!$1:$3")
        self.assertGreater(text_from_row, logo_to_row)
        self.assertEqual(text_from_row, 3)
        self.assertLessEqual(text_top - (logo_top + logo_height), 220000)
        self.assertLessEqual(text_left, logo_left)
        self.assertGreaterEqual(text_left + text_width, logo_left + logo_width)
        self.assertGreaterEqual(logo_width, 2950000)
        self.assertGreaterEqual(logo_height, 630000)
        self.assertLessEqual(logo_from_col_off, 0)
        self.assertGreaterEqual(logo_to_col_off, 1720000)
        self.assertLessEqual(logo_from_row_off, 0)
        self.assertGreaterEqual(logo_to_row_off, 410000)
        self.assertGreaterEqual(text_width, 3300000)
        self.assertLessEqual(text_width, 3500000)
        self.assertEqual(text_from_col, 7)
        self.assertGreaterEqual(text_to_col, 9)
        self.assertEqual(text_from_col_off, 0)
        self.assertEqual(text_left, logo_left)

        paragraph_alignments = [
            paragraph.find(f"{NS_A}pPr").attrib.get("algn")
            for paragraph in text_anchor.findall(f"{NS_DRAWING}sp/{NS_DRAWING}txBody/{NS_A}p")
            if paragraph.find(f"{NS_A}pPr") is not None
        ]
        self.assertTrue(paragraph_alignments)
        self.assertEqual(set(paragraph_alignments), {"l"})

        paragraphs = [
            "".join(text_node.text or "" for text_node in paragraph.findall(f".//{NS_A}t"))
            for paragraph in text_anchor.findall(f"{NS_DRAWING}sp/{NS_DRAWING}txBody/{NS_A}p")
        ]
        self.assertEqual(
            paragraphs,
            [
                "Koncept Image Pte Limited",
                "61 Kaki Bukit Ave 1",
                "",
                "Project No: KI-TEST-001",
            ],
        )
        project_index = paragraphs.index("Project No: KI-TEST-001")
        self.assertIn("xl/media/header_logo.png", media_names)
        self.assertEqual(paragraphs[project_index - 1], "")
        self.assertFalse(any(current == "" and following == "" for current, following in zip(paragraphs, paragraphs[1:])))

        run_sizes = [
            int(run_props.attrib["sz"])
            for run_props in text_anchor.findall(f".//{NS_A}rPr")
            if "sz" in run_props.attrib
        ]
        self.assertTrue(run_sizes)
        self.assertEqual(min(run_sizes), 900)
        self.assertEqual(max(run_sizes), 900)

    def test_table_headers_bold_and_quantity_centered(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        for ref in ("A20", "B20", "C20", "E20"):
            self.assertIsNotNone(font_for_style(styles, cell_style(sheet, ref)).find(f"{NS_MAIN}b"))
        self.assertEqual(alignment_for_style(styles, cell_style(sheet, "B20")).attrib.get("horizontal"), "center")
        self.assertEqual(alignment_for_style(styles, cell_style(sheet, "E20")).attrib.get("horizontal"), "right")
        self.assertEqual(alignment_for_style(styles, cell_style(sheet, "E21")).attrib.get("horizontal"), "right")
        self.assertEqual(alignment_for_style(styles, cell_style(sheet, "B24")).attrib.get("horizontal"), "center")

    def test_client_address_rows_are_left_aligned(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        self.assertEqual(cell_value(sheet, "A7"), "10 Sample Street")
        self.assertEqual(cell_value(sheet, "A8"), "#02-03 Sample Building")
        self.assertEqual(alignment_for_style(styles, cell_style(sheet, "A7")).attrib.get("horizontal"), "left")
        self.assertEqual(alignment_for_style(styles, cell_style(sheet, "A8")).attrib.get("horizontal"), "left")
        for ref in ("A6", "A7", "A8"):
            font = font_for_style(styles, cell_style(sheet, ref))
            self.assertEqual(font_name(font), "Calibri", ref)
            self.assertEqual(font_size(font), "13", ref)
            for text, run_font_name, run_font_size in cell_inline_run_fonts(sheet, ref):
                if text:
                    self.assertEqual(run_font_name, "Calibri", ref)
                    self.assertEqual(run_font_size, "13", ref)

    def test_attention_contact_name_and_title_are_split_from_label(self):
        tmp, path = generate_layout_workbook({
            "client": {
                "name": "Sample Client",
                "attention": "Melissa Ong",
                "title": "Senior Event Producer",
                "address": ["10 Sample Street", "#02-03 Sample Building"],
            },
        })
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        self.assertEqual(cell_value(sheet, "A11"), "Attention:")
        self.assertEqual(cell_value(sheet, "B12"), "Melissa Ong")
        self.assertEqual(cell_value(sheet, "B13"), "Senior Event Producer")
        self.assertEqual(cell_value(sheet, "A12"), "")
        self.assertEqual(cell_value(sheet, "A14"), "")
        self.assertEqual(cell_value(sheet, "A15"), "")
        self.assertEqual(cell_value(sheet, "A16"), str(quote.excel_date_serial("2026-06-04")))
        self.assertEqual(cell_inline_runs(sheet, "A11"), [("Attention:", True, False, False)])
        self.assertEqual(cell_inline_runs(sheet, "B12"), [("Melissa Ong", True, False, False)])
        self.assertEqual(cell_inline_runs(sheet, "B13"), [("Senior Event Producer", False, False, False)])
        for ref in ("A11", "B12", "B13"):
            font = font_for_style(styles, cell_style(sheet, ref))
            self.assertEqual(font_name(font), "Calibri", ref)
            self.assertEqual(font_size(font), "13", ref)
            for text, run_font_name, run_font_size in cell_inline_run_fonts(sheet, ref):
                if text:
                    self.assertEqual(run_font_name, "Calibri", ref)
                    self.assertEqual(run_font_size, "13", ref)

    def test_quote_date_cell_uses_customer_facing_date_format(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        self.assertEqual(cell_value(sheet, "A16"), str(quote.excel_date_serial("2026-06-04")))
        self.assertEqual(num_fmt_code_for_style(styles, cell_style(sheet, "A16")).replace("\\ ", " "), "dd mmmm yyyy")

    def test_price_cells_use_thousands_separator_number_format(self):
        tmp, path = generate_layout_workbook()
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        line_font = font_for_style(styles, cell_style(sheet, "E24"))
        self.assertEqual(num_fmt_for_style(styles, cell_style(sheet, "E24")), "4")
        self.assertIsNone(line_font.find(f"{NS_MAIN}b"))
        self.assertIsNone(line_font.find(f"{NS_MAIN}i"))
        self.assertNotEqual(font_color(line_font).get("rgb"), "FFFF0000")

        for ref in ("E27", "E28", "E29"):
            font = font_for_style(styles, cell_style(sheet, ref))
            self.assertEqual(num_fmt_for_style(styles, cell_style(sheet, ref)), "4")
            self.assertIsNotNone(font.find(f"{NS_MAIN}b"))
            self.assertIsNone(font.find(f"{NS_MAIN}i"))
            self.assertNotEqual(font_color(font).get("rgb"), "FFFF0000")

    def test_notes_are_plain_numbered_and_acceptance_text_stays_out_of_notes(self):
        explicit_notes = ["First explicit note", "Second explicit note"]
        tmp, path = generate_layout_workbook({
            "notes_heading": "Note : ",
            "standard_notes": explicit_notes,
        })
        self.addCleanup(tmp.cleanup)

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))

        self.assertEqual(cell_value(sheet, "A33"), "1.00")
        self.assertEqual(cell_value(sheet, "A34"), "2.00")
        self.assertEqual(cell_value(sheet, "B33"), explicit_notes[0])
        self.assertIsNone(font_for_style(styles, cell_style(sheet, "A33")).find(f"{NS_MAIN}i"))
        self.assertIsNone(font_for_style(styles, cell_style(sheet, "A34")).find(f"{NS_MAIN}i"))
        self.assertEqual(cell_value(sheet, "E35"), "")
        self.assertEqual(cell_value(sheet, "E37"), "We accept the quotation amount and the terms")

    def test_brief_can_override_terms_notes_and_company_text(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "quotation.xlsx"
        brief = {
            "company_identity": "Other Company",
            "quote_date": "2026-06-04",
            "project_number": "OC-001",
            "client": {"name": "Sample Client", "attention": "Alex Tan"},
            "project": {"title": "Sample Project"},
            "line_items": [],
            "payment_terms": [
                "50% deposit",
                "50% before handover",
                "Warranty excluded",
                "All cheques should be crossed and made payable to Other Company Pte Ltd",
            ],
            "terms_heading": "Commercial Terms",
            "cheque_payee": "Other Company Pte Ltd",
            "notes_heading": "Editable Notes",
            "standard_notes": ["First editable note", "Second editable note"],
            "company": {
                "name": "Other Company Pte Ltd",
                "header_lines": ["Other Company Pte Ltd", "Dynamic address line", "", "Dynamic bank line"],
                "logo_data_url": "data:image/png;base64,iVBORw0KGgo=",
            },
            "acceptance": {
                "company_name": "Other Company Pte Ltd",
                "text": "Accepted by customer",
                "person_label": "Authorised signer",
                "stamp_label": "Customer stamp",
                "date_label": "Signed date:",
            },
            "signature": {
                "company_signatory": "Morgan Lee",
                "company_title": "Sales Lead",
                "company_date_label": "Company signed date:",
            },
        }
        price = quote.PriceRow(1, "Floor", "m2 needle punch carpet in colour", "sqm", 7, 1.09, 1.5, "")
        line = quote.QuoteLine("Floor", 1, "sqm", "Needle punch carpet", "needle punch carpet in colour", "", price, 10.5, "matched", [])

        quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, [line])

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            styles = ET.fromstring(zf.read("xl/styles.xml"))
            drawing = ET.fromstring(zf.read("xl/drawings/drawing1.xml"))
            drawing_rels = zf.read("xl/drawings/_rels/drawing1.xml.rels").decode("utf-8")
            media_names = set(zf.namelist())

        self.assertEqual(find_cell_ref(sheet, "Commercial Terms"), "A32")
        self.assertEqual(find_cell_ref(sheet, "50% deposit"), "B33")
        self.assertEqual(find_cell_ref(sheet, "Warranty excluded"), "B35")
        self.assertEqual(find_cell_ref(sheet, "All cheques should be crossed and made payable to Other Company Pte Ltd"), "B36")
        self.assertTrue(find_cell_ref(sheet, "Editable Notes").startswith("A"))
        self.assertTrue(find_cell_ref(sheet, "First editable note").startswith("B"))
        self.assertTrue(find_cell_ref(sheet, "Second editable note").startswith("B"))
        self.assertTrue(find_cell_ref(sheet, "Other Company Pte Ltd").startswith("B"))
        self.assertTrue(find_cell_ref(sheet, "Accepted by customer").startswith("E"))
        self.assertTrue(find_cell_ref(sheet, "Authorised signer").startswith("E"))
        self.assertTrue(find_cell_ref(sheet, "Customer stamp").startswith("E"))
        self.assertTrue(find_cell_ref(sheet, "Company signed date:").startswith("B"))
        self.assertTrue(find_cell_ref(sheet, "Signed date:").startswith("E"))
        for expected_text in (
            "Commercial Terms",
            "50% deposit",
            "Editable Notes",
            "First editable note",
            "Other Company Pte Ltd",
            "Accepted by customer",
            "Morgan Lee",
            "Sales Lead",
            "Authorised signer",
            "Customer stamp",
            "Company signed date:",
            "Signed date:",
        ):
            ref = find_cell_ref(sheet, expected_text)
            self.assertTrue(ref, f"Missing generated cell for {expected_text!r}")
            font = font_for_style(styles, cell_style(sheet, ref))
            self.assertEqual(font_name(font), "Calibri", expected_text)
            self.assertEqual(font_size(font), "10", expected_text)
            for text, run_font_name, run_font_size in cell_inline_run_fonts(sheet, ref):
                if text:
                    self.assertEqual(run_font_name, "Calibri", expected_text)
                    self.assertEqual(run_font_size, "10", expected_text)

        paragraphs = [
            "".join(text_node.text or "" for text_node in paragraph.findall(f".//{NS_A}t"))
            for paragraph in drawing.findall(f".//{NS_A}p")
        ]
        self.assertIn("Other Company Pte Ltd", paragraphs)
        self.assertIn("Dynamic address line", paragraphs)
        self.assertEqual(paragraphs[2], "")
        self.assertIn("Dynamic bank line", paragraphs)
        self.assertIn("Target=\"../media/header_logo.png\"", drawing_rels)
        self.assertIn("xl/media/header_logo.png", media_names)

    def test_payment_terms_cheque_instruction_is_not_written_twice(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "quotation.xlsx"
        cheque_instruction = "All cheques should be crossed and made payable to Other Company Pte Ltd"
        brief = {
            "company_identity": "Other Company",
            "quote_date": "2026-06-04",
            "client": {"name": "Sample Client", "attention": "Alex Tan"},
            "project": {"title": "Sample Project"},
            "line_items": [],
            "payment_terms": ["50% deposit", cheque_instruction],
            "terms_heading": "Commercial Terms",
            "cheque_payee": "Other Company Pte Ltd",
            "notes_heading": "Editable Notes",
            "standard_notes": ["First editable note"],
            "company": {"name": "Other Company Pte Ltd"},
            "rich_text": {
                "paymentTerms": (
                    "<div><strong>50%</strong> deposit</div>"
                    "<div>All cheques should be crossed and made payable to <strong>Other Company Pte Ltd</strong></div>"
                ),
            },
        }
        price = quote.PriceRow(1, "Floor", "m2 needle punch carpet in colour", "sqm", 7, 1.09, 1.5, "")
        line = quote.QuoteLine("Floor", 1, "sqm", "Needle punch carpet", "needle punch carpet in colour", "", price, 10.5, "matched", [])

        quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, [line])

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        self.assertEqual(find_cell_refs(sheet, cheque_instruction), ["B34"])
        self.assertEqual(
            cell_inline_runs(sheet, "B34"),
            [
                ("All cheques should be crossed and made payable to ", False, False, False),
                ("Other Company Pte Ltd", True, False, False),
            ],
        )
        self.assertEqual(cell_value(sheet, "B35"), "")
        self.assertEqual(find_cell_ref(sheet, "Editable Notes"), "A36")

    def test_quote_detail_rich_text_runs_are_written_to_layout_output(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "quotation.xlsx"
        brief = {
            "company_identity": "Other Company",
            "quote_date": "2026-06-04",
            "client": {
                "name": "Sample Client",
                "attention": "Alex Tan",
                "address": ["10 Sample Street", "Singapore 000010"],
            },
            "project": {"title": "Sample Project"},
            "line_items": [],
            "payment_terms": ["50% deposit"],
            "terms_heading": "Commercial Terms",
            "cheque_payee": "Other Company Pte Ltd",
            "notes_heading": "Editable Notes",
            "standard_notes": ["First editable note"],
            "company": {
                "name": "Other Company Pte Ltd",
                "header_lines": ["Other Company Pte Ltd", "Dynamic address line"],
            },
            "rich_text": {
                "quoteDate": "<div><strong><em><u>04 June 2026</u></em></strong></div>",
                "clientAddress": "<div><strong>10 Sample</strong> <em>Street</em></div><div><u>Singapore 000010</u></div>",
                "headerDetails": "<div><strong>Other Company Pte Ltd</strong></div><div><em><u>Dynamic address line</u></em></div>",
                "termsHeading": "<div><strong>Commercial Terms</strong></div>",
                "paymentTerms": "<div><strong>50%</strong> <em>deposit</em></div>",
                "standardNotes": "<div>First <u>editable</u> note</div>",
            },
        }
        price = quote.PriceRow(1, "Floor", "m2 needle punch carpet in colour", "sqm", 7, 1.09, 1.5, "")
        line = quote.QuoteLine("Floor", 1, "sqm", "Needle punch carpet", "needle punch carpet in colour", "", price, 10.5, "matched", [])

        quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, [line])

        with zipfile.ZipFile(path) as zf:
            sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
            drawing = ET.fromstring(zf.read("xl/drawings/drawing1.xml"))

        self.assertEqual(
            cell_inline_runs(sheet, "A7"),
            [
                ("10 Sample", True, False, False),
                (" ", False, False, False),
                ("Street", False, True, False),
            ],
        )
        self.assertEqual(cell_inline_runs(sheet, "A8"), [("Singapore 000010", False, False, True)])
        self.assertEqual(cell_inline_runs(sheet, "A16"), [("04 June 2026", True, True, True)])
        self.assertEqual(cell_inline_runs(sheet, find_cell_ref(sheet, "Commercial Terms")), [("Commercial Terms", True, False, False)])
        self.assertEqual(
            cell_inline_runs(sheet, "B33"),
            [
                ("50%", True, False, False),
                (" ", False, False, False),
                ("deposit", False, True, False),
            ],
        )
        note_ref = find_cell_ref(sheet, "First editable note")
        self.assertTrue(note_ref.startswith("B"))
        self.assertEqual(
            cell_inline_runs(sheet, note_ref),
            [
                ("First ", False, False, False),
                ("editable", False, False, True),
                (" note", False, False, False),
            ],
        )
        header_runs = drawing_paragraph_runs(drawing)
        self.assertIn([("Other Company Pte Ltd", True, False, False)], header_runs)
        self.assertIn([("Dynamic address line", False, True, True)], header_runs)

    def test_missing_logo_removes_template_logo_from_output(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "quotation.xlsx"
        brief = {
            "company_identity": "No Logo Company",
            "quote_date": "2026-06-04",
            "project_number": "NO-LOGO-001",
            "client": {"name": "Sample Client", "attention": "Alex Tan"},
            "project": {"title": "No Logo Project"},
            "line_items": [],
            "payment_terms": [],
            "company": {"name": "No Logo Company", "header_lines": ["No Logo Company"]},
        }
        price = quote.PriceRow(1, "Floor", "m2 needle punch carpet in colour", "sqm", 7, 1.09, 1.5, "")
        line = quote.QuoteLine("Floor", 1, "sqm", "Needle punch carpet", "needle punch carpet in colour", "", price, 10.5, "matched", [])

        quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, [line])

        with zipfile.ZipFile(path) as zf:
            drawing = ET.fromstring(zf.read("xl/drawings/drawing1.xml"))
            drawing_rels = zf.read("xl/drawings/_rels/drawing1.xml.rels").decode("utf-8")
            media_names = set(zf.namelist())

        self.assertFalse(any(anchor.find(f"{NS_DRAWING}pic") is not None for anchor in drawing.findall(f"{NS_DRAWING}twoCellAnchor")))
        self.assertNotIn("/image", drawing_rels)
        self.assertNotIn("xl/media/image1.jpeg", media_names)

    def test_header_logo_replacement_does_not_hijack_unrelated_picture(self):
        parts = {
            "xl/drawings/drawing1.xml": drawing_with_picture_xml("Product Screenshot", "rId5"),
            "xl/drawings/_rels/drawing1.xml.rels": drawing_rels_xml(("rId5", "../media/product.png")),
            "xl/media/product.png": b"product image",
            "[Content_Types].xml": empty_content_types_xml(),
        }

        quote.replace_header_logo(parts, "data:image/png;base64,iVBORw0KGgo=")

        drawing = ET.fromstring(parts["xl/drawings/drawing1.xml"])
        rels = ET.fromstring(parts["xl/drawings/_rels/drawing1.xml.rels"])
        pics = drawing.findall(f".//{NS_DRAWING}pic")
        names = [
            pic.find(f"{NS_DRAWING}nvPicPr/{NS_DRAWING}cNvPr").attrib.get("name")
            for pic in pics
        ]
        targets = {
            rel.attrib.get("Id"): rel.attrib.get("Target")
            for rel in rels.findall(f"{NS_PACKAGE_REL}Relationship")
        }

        self.assertIn("Product Screenshot", names)
        self.assertIn("Header Logo", names)
        self.assertEqual(targets["rId5"], "../media/product.png")
        self.assertIn("../media/header_logo.png", targets.values())
        self.assertEqual(parts["xl/media/product.png"], b"product image")
        self.assertIn("xl/media/header_logo.png", parts)

    def test_header_logo_replacement_creates_missing_drawing_rels_file(self):
        parts = {
            "xl/drawings/drawing1.xml": empty_drawing_xml(),
            "[Content_Types].xml": empty_content_types_xml(),
        }

        quote.replace_header_logo(parts, "data:image/png;base64,iVBORw0KGgo=")

        self.assertIn("xl/drawings/_rels/drawing1.xml.rels", parts)
        drawing = ET.fromstring(parts["xl/drawings/drawing1.xml"])
        rels = ET.fromstring(parts["xl/drawings/_rels/drawing1.xml.rels"])
        header_pic = next(
            pic
            for pic in drawing.findall(f".//{NS_DRAWING}pic")
            if pic.find(f"{NS_DRAWING}nvPicPr/{NS_DRAWING}cNvPr").attrib.get("name") == "Header Logo"
        )
        header_rel_id = header_pic.find(f".//{NS_A}blip").attrib[f"{NS_REL}embed"]
        header_rel = next(
            rel
            for rel in rels.findall(f"{NS_PACKAGE_REL}Relationship")
            if rel.attrib.get("Id") == header_rel_id
        )

        self.assertEqual(header_rel.attrib["Target"], "../media/header_logo.png")
        self.assertIn("xl/media/header_logo.png", parts)

    def test_excel_pdf_export_script_repairs_and_saves_workbook_before_export(self):
        script = quote.powershell_export_script(Path("quotation.xlsx"), Path("quotation.pdf"))

        self.assertIn("CorruptLoad", script)
        self.assertIn("$workbook.SaveAs($repairedWorkbookPath, 51)", script)
        self.assertIn("Move-Item -LiteralPath $repairedWorkbookPath -Destination $xlsxPath -Force", script)

    def test_brief_text_is_written_as_literal_xlsx_text_not_formulas(self):
        brief = {
            "company_identity": "Koncept Image",
            "quote_date": "2026-06-04",
            "client": {
                "name": "=WEBSERVICE(\"https://example.test/client\")",
                "attention": "Alex Tan",
            },
            "project": {
                "title": "=HYPERLINK(\"https://example.test/project\",\"project\")",
            },
            "line_items": [],
            "payment_terms": [
                "=WEBSERVICE(\"https://example.test/terms\")",
            ],
        }
        line = quote.QuoteLine(
            section="=WEBSERVICE(\"https://example.test/section\")",
            quantity=1,
            unit="lot",
            description="=HYPERLINK(\"https://example.test/line\",\"line\")",
            pricing_keyword="",
            display_price="=HYPERLINK(\"https://example.test/price\",\"price\")",
            matched_price=None,
            amount=None,
            match_status="manual-display",
            match_candidates=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quotation.xlsx"
            quote.write_quote_layout_xlsx(KONCEPT_LAYOUT, path, brief, [line])

            with zipfile.ZipFile(path) as zf:
                sheet = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))

        self.assertEqual(worksheet_formulas(sheet), ["SUM(E22:E26)", "ROUND(E27*0.090000,2)", "SUM(E27:E28)"])
        self.assertEqual(cell_value(sheet, "A6"), brief["client"]["name"])
        self.assertEqual(cell_value(sheet, "A18"), brief["project"]["title"])
        self.assertEqual(cell_value(sheet, "C22"), line.section)
        self.assertEqual(cell_value(sheet, "E24"), line.display_price)
        self.assertTrue(find_cell_ref(sheet, brief["payment_terms"][0]).startswith("B"))

    def test_csv_match_report_neutralizes_formula_leading_text(self):
        line = quote.QuoteLine(
            section="=WEBSERVICE(\"https://example.test/section\")",
            quantity=1,
            unit="lot",
            description="+HYPERLINK(\"https://example.test/line\",\"line\")",
            pricing_keyword="-10+20",
            display_price="",
            matched_price=None,
            amount=None,
            match_status="@manual",
            match_candidates=[],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pricing_matches.csv"
            quote.write_match_csv(path, [line])
            with path.open(newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))

        data = rows[1]
        self.assertIn("pricing_id", rows[0])
        self.assertIn("catalog_description", rows[0])
        self.assertNotIn("template_row", rows[0])
        self.assertNotIn("template_description", rows[0])
        self.assertEqual(data[0], "'@manual")
        self.assertEqual(data[1], "'=WEBSERVICE(\"https://example.test/section\")")
        self.assertEqual(data[2], "'+HYPERLINK(\"https://example.test/line\",\"line\")")
        self.assertEqual(data[3], "'-10+20")


if __name__ == "__main__":
    unittest.main()
