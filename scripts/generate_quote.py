#!/usr/bin/env python3
"""Generate quotation XLSX files with optional PDF export.

XLSX generation uses the Python standard library. It fills a quotation layout
through ZIP/XML updates and preserves its styling, print setup and drawings.
Optional native PDF export can use PDFium to check and remove a blank spill page.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import csv
import datetime as dt
import hashlib
import html
import io
import json
import math
import os
import posixpath
import re
import shutil
import tempfile
import subprocess
import textwrap
import time
import unicodedata
import zipfile
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from xml.dom import minidom
from xml.etree import ElementTree as ET

if __package__:
    from . import quote_template
else:
    import quote_template


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def round_commercial_cents(value: Any) -> float | None:
    """Round commercial amounts with explicit decimal half-up semantics."""
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
        if not number.is_finite():
            return None
        rounded = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return float(rounded) if rounded else 0.0


DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "_output"
NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_CONTENT_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"
NS_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
NS_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
MAX_XLSX_XML_BYTES = 8 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 200
MAX_XLSX_COLUMNS = 16_384
MAX_XLSX_ROWS = 1_048_576
MAX_XLSX_ENTRIES = 4096
MAX_PDF_PAGINATION_BYTES = 16 * 1024 * 1024
MAX_PDF_PROOF_WORKBOOK_BYTES = 64 * 1024 * 1024
MAX_PDF_PROOF_SECONDS = 15
PDF_PROOF_DEADLINE_SECONDS = 75
XMLNS_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XMLNS_CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
XMLNS_DC = "http://purl.org/dc/elements/1.1/"
XMLNS_X14 = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"
XMLNS_X14AC = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
XMLNS_X15 = "http://schemas.microsoft.com/office/spreadsheetml/2010/11/main"
XMLNS_X15AC = "http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac"
XMLNS_X16R2 = "http://schemas.microsoft.com/office/spreadsheetml/2015/02/main"
XMLNS_XR = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
XMLNS_XR2 = "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2"
XMLNS_XR3 = "http://schemas.microsoft.com/office/spreadsheetml/2016/revision3"
EXPORT_STATUS_CUSTOMER_READY = {"libreoffice_exported", "excel_exported"}
WORKSHEET_CHILD_ORDER = (
    "sheetPr",
    "dimension",
    "sheetViews",
    "sheetFormatPr",
    "cols",
    "sheetData",
    "sheetCalcPr",
    "sheetProtection",
    "protectedRanges",
    "scenarios",
    "autoFilter",
    "sortState",
    "dataConsolidate",
    "customSheetViews",
    "mergeCells",
    "phoneticPr",
    "conditionalFormatting",
    "dataValidations",
    "hyperlinks",
    "printOptions",
    "pageMargins",
    "pageSetup",
    "headerFooter",
    "rowBreaks",
    "colBreaks",
    "customProperties",
    "cellWatches",
    "ignoredErrors",
    "smartTags",
    "drawing",
    "legacyDrawing",
    "legacyDrawingHF",
    "picture",
    "oleObjects",
    "controls",
    "webPublishItems",
    "tableParts",
    "extLst",
)
FIRST_PRINT_PAGE_END_ROW = 55
CONTINUATION_PAGE_START_ROW = 56
CONTINUATION_PAGE_HEIGHT = 61
CONTINUATION_TABLE_HEADER_OFFSET = 2
CONTINUATION_CURRENCY_OFFSET = 3
CONTINUATION_BODY_OFFSET = 5
TOTAL_BLOCK_HEIGHT = 3
SIGNATURE_CONTENT_HEIGHT = 8
SIGNATURE_BLOCK_PAGE_GUARD_ROWS = 0
SIGNATURE_BLOCK_HEIGHT = SIGNATURE_CONTENT_HEIGHT + SIGNATURE_BLOCK_PAGE_GUARD_ROWS
# The visible B:I body columns span 123.875 width units; allow padding and rich emphasis.
OPTIONAL_TEXT_LINE_MAX_WIDTH_EM = 104
# LibreOffice advances past three bottom rows before the declared continuation break.
OPTIONAL_TEXT_CONTINUATION_END_GUARD_ROWS = 3
QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT = "18.7"
EMU_PER_POINT = 12_700
# Anchor in the visible right-side header column, not its hidden neighbour.
HEADER_LOGO_ANCHOR_FROM = {
    "col": "8",
    "colOff": "0",
    "row": "1",
    "rowOff": "0",
}
HEADER_LOGO_ANCHOR_TO = {
    "col": "8",
    "colOff": "1720000",
    "row": "2",
    "rowOff": "415000",
}
HEADER_LOGO_CANONICAL_WIDTH_EMU = int(HEADER_LOGO_ANCHOR_TO["colOff"])
HEADER_LOGO_CANONICAL_ROW_OFFSET_EMU = int(HEADER_LOGO_ANCHOR_TO["rowOff"])
HEADER_LOGO_MAX_WIDTH_EMU = HEADER_LOGO_CANONICAL_WIDTH_EMU
HEADER_LOGO_MAX_HEIGHT_EMU = (
    round(float(QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT) * EMU_PER_POINT)
    + HEADER_LOGO_CANONICAL_ROW_OFFSET_EMU
)
HEADER_LOGO_OFFSET_X_EMU = 4_550_000
HEADER_LOGO_OFFSET_Y_EMU = 260_000
QUOTE_DATE_MERGE_REF = "A16:C16"
ET.register_namespace("cp", XMLNS_CP)
ET.register_namespace("dc", XMLNS_DC)
ET.register_namespace("", "http://schemas.openxmlformats.org/spreadsheetml/2006/main")
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
ET.register_namespace("mc", XMLNS_MC)
ET.register_namespace("x14", XMLNS_X14)
ET.register_namespace("x14ac", XMLNS_X14AC)
ET.register_namespace("x15", XMLNS_X15)
ET.register_namespace("x15ac", XMLNS_X15AC)
ET.register_namespace("x16r2", XMLNS_X16R2)
ET.register_namespace("xr", XMLNS_XR)
ET.register_namespace("xr2", XMLNS_XR2)
ET.register_namespace("xr3", XMLNS_XR3)
ET.register_namespace("xdr", "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing")
ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
ET.register_namespace("a16", "http://schemas.microsoft.com/office/drawing/2014/main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate quotation XLSX files.")
    parser.add_argument("--brief", required=True, type=Path, help="Path to quote brief JSON.")
    parser.add_argument(
        "--out",
        type=Path,
        help="Output folder. Defaults to _output/<client>/<project>/<YYYYMMDD> when omitted.",
    )
    parser.add_argument("--template", type=Path, required=True, help="Pricing catalog JSON path.")
    parser.add_argument("--layout-template", type=Path, required=True, help="Customer quotation layout XLSX path.")
    parser.add_argument(
        "--quote-template", type=Path,
        help="Quote template definition JSON (sqag.quote-template.v1). When given, it lays out the XLSX instead of --layout-template.",
    )
    parser.add_argument("--pdf-mode", choices=("auto", "workbook", "styled", "text", "none"), default="none", help="Optional PDF export mode. Defaults to none; workbook exports the generated XLSX only; auto tries Excel/LibreOffice then styled fallback, styled skips external PDF export, text writes a simple fallback.")
    parser.add_argument("--allow-ambiguous", action="store_true", help="Use the best pricing match even when multiple rows match.")
    return parser.parse_args()


@dataclass
class PriceRow:
    row_number: int
    section: str
    description: str
    unit_hint: str
    cost: float
    gst_multiplier: float
    markup: float
    remark: str
    pricing_id: str = ""
    aliases: list[str] = field(default_factory=list)
    catalog_digest: str = ""
    catalog_currency: str = ""

    @property
    def sale_unit_price(self) -> float:
        rounded = round_commercial_cents(self.cost * self.markup)
        return rounded if rounded is not None else 0.0


@dataclass
class QuoteLine:
    section: str
    quantity: float | None
    unit: str
    description: str
    pricing_keyword: str
    display_price: str
    matched_price: PriceRow | None
    amount: float | None
    match_status: str
    match_candidates: list[PriceRow]
    price_mode: str = "Priced"
    unit_price_override: float | None = None
    pricing_authority: dict[str, Any] | None = None
    diagnostic_row_id: str = ""
    source_basis_line_id: str = ""


# Printed in red on every new quotation, below the totals and before Terms & Conditions (#363).
QUOTE_EXCLUSIONS_HEADING = "The above quote excludes:"
QUOTE_EXCLUSIONS = (
    "Organiser Admin Fees",
    "Power sockets which can be directly ordered with connection fees with the organiser",
    "Connection fees for electrical fittings",
)
QUOTE_EXCLUSIONS_COLOR = "FFFF0000"


@dataclass(frozen=True)
class LayoutChunk:
    name: str
    height: int


@dataclass(frozen=True)
class PageModel:
    """Manual print pages: page 1 ends at first_page_end, later pages hold page_height rows.

    Later pages reserve a header band (gap, repeated item-table header, gap) before
    body_offset; the last guard_rows of a later page stay clear for optional text.
    """

    first_page_end: int
    page_height: int
    table_header_offset: int
    currency_offset: int
    body_offset: int
    guard_rows: int = OPTIONAL_TEXT_CONTINUATION_END_GUARD_ROWS

    def page_start(self, row_number: int) -> int | None:
        if row_number <= self.first_page_end:
            return None
        first = self.first_page_end + 1
        return first + ((row_number - first) // self.page_height) * self.page_height

    def page_end(self, row_number: int) -> int:
        page_start = self.page_start(row_number)
        if page_start is None:
            return self.first_page_end
        return page_start + self.page_height - 1

    def next_page_start(self, row_number: int) -> int:
        page_start = self.page_start(row_number)
        if page_start is None:
            return self.first_page_end + 1
        return page_start + self.page_height

    def clear_header_band(self, row_number: int) -> int:
        """Advance later-page content past the reserved header rows."""
        page_start = self.page_start(row_number)
        if page_start is not None:
            return max(row_number, page_start + self.body_offset)
        return row_number

    def summary_start(self, row_number: int, block_height: int) -> int:
        row_number = self.clear_header_band(row_number)
        if row_number + block_height - 1 <= self.page_end(row_number):
            return row_number
        return self.next_page_start(row_number) + self.body_offset

    def optional_text_capacity(self) -> int:
        return self.page_height - self.body_offset - self.guard_rows

    def optional_text_chunk_start(self, row_number: int, chunk: LayoutChunk) -> tuple[int, bool]:
        candidate_row = row_number
        row_number = self.clear_header_band(row_number)
        page_end = self.page_end(row_number)
        if self.page_start(row_number) is not None:
            page_end -= self.guard_rows
        remaining_page_rows = page_end - row_number + 1
        if chunk.height <= 0 or chunk.height <= remaining_page_rows:
            return row_number, row_number != candidate_row
        if chunk.height <= self.optional_text_capacity():
            return self.next_page_start(row_number) + self.body_offset, True
        return row_number, row_number != candidate_row

    def break_ids(self, last_row: int) -> list[int]:
        if last_row <= self.first_page_end:
            return []
        break_ids = [self.first_page_end]
        next_break = self.first_page_end + self.page_height
        while next_break < last_row:
            break_ids.append(next_break)
            next_break += self.page_height
        return break_ids


LEGACY_PAGE = PageModel(
    first_page_end=FIRST_PRINT_PAGE_END_ROW,
    page_height=CONTINUATION_PAGE_HEIGHT,
    table_header_offset=CONTINUATION_TABLE_HEADER_OFFSET,
    currency_offset=CONTINUATION_CURRENCY_OFFSET,
    body_offset=CONTINUATION_BODY_OFFSET,
)


@dataclass
class RichTextRun:
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False



def col_to_index(cell_ref: str) -> int:
    match = re.fullmatch(r"([A-Za-z]{1,3})([1-9][0-9]{0,6})?", str(cell_ref or ""))
    if not match:
        raise ValueError("Spreadsheet cell reference is not valid.")
    letters = match.group(1)
    total = 0
    for ch in letters:
        total = total * 26 + (ord(ch.upper()) - 64)
    index = total - 1
    row_number = int(match.group(2)) if match.group(2) else 1
    if index < 0 or index >= MAX_XLSX_COLUMNS or row_number > MAX_XLSX_ROWS:
        raise ValueError("Spreadsheet cell reference exceeds XLSX limits.")
    return index


def read_bounded_xlsx_part(zf: zipfile.ZipFile, name: str, *, max_bytes: int = MAX_XLSX_XML_BYTES) -> bytes:
    info = zf.getinfo(name)
    if info.file_size < 0 or info.file_size > max_bytes:
        raise ValueError(f"Spreadsheet XML part exceeds the {max_bytes}-byte limit.")
    if info.compress_size == 0 and info.file_size > 0:
        raise ValueError("Spreadsheet XML part has an invalid compression size.")
    if info.compress_size and info.file_size / info.compress_size > MAX_XLSX_COMPRESSION_RATIO:
        raise ValueError("Spreadsheet XML part exceeds the compression-ratio limit.")
    data = zf.read(info)
    if len(data) != info.file_size or len(data) > max_bytes:
        raise ValueError("Spreadsheet XML part could not be read safely.")
    return data


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        xml = read_bounded_xlsx_part(zf, "xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(xml)
    strings: list[str] = []
    for si in root.findall(f"{NS_MAIN}si"):
        parts = []
        for t in si.iter(f"{NS_MAIN}t"):
            parts.append(t.text or "")
        strings.append("".join(parts))
    return strings


def cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    value_node = cell.find(f"{NS_MAIN}v")
    inline_node = cell.find(f"{NS_MAIN}is")
    if cell_type == "inlineStr" and inline_node is not None:
        return "".join(t.text or "" for t in inline_node.iter(f"{NS_MAIN}t")).strip()
    if value_node is None:
        return None
    raw = value_node.text or ""
    if cell_type == "s":
        return shared_strings[int(raw)] if raw else ""
    if cell_type == "str":
        return raw
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except (ValueError, binascii.Error):
        return raw


def read_first_sheet_rows_with_numbers(xlsx_path: Path) -> list[tuple[int, list[Any]]]:
    with zipfile.ZipFile(xlsx_path) as zf:
        if len(zf.infolist()) > MAX_XLSX_ENTRIES:
            raise ValueError("Spreadsheet archive contains too many entries.")
        shared_strings = read_shared_strings(zf)
        sheet_xml = read_bounded_xlsx_part(zf, "xl/worksheets/sheet1.xml")
    root = ET.fromstring(sheet_xml)
    rows: list[tuple[int, list[Any]]] = []
    for row in root.iter(f"{NS_MAIN}row"):
        row_number = int(row.attrib.get("r", str(len(rows) + 1)))
        values: list[Any] = []
        for cell in row.findall(f"{NS_MAIN}c"):
            ref = cell.attrib.get("r", "A1")
            col_index = col_to_index(ref)
            while len(values) <= col_index:
                values.append(None)
            values[col_index] = cell_value(cell, shared_strings)
        rows.append((row_number, values))
    return rows


def as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return default


def clean_text(value: Any) -> str:
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", str(value or "")).strip())


def normalize_unit(unit: Any) -> str:
    text = clean_text(unit)
    lower = re.sub(r"\s+", " ", text.lower()).strip(". ")
    if lower in {"m2", "m\u00b2", "m^2", "sq m", "sq.m", "sq.m.", "square metre", "square meter", "square metres", "square meters"}:
        return "sqm"
    if lower in {"m run", "m. run"}:
        return "m run"
    if lower in {"m length", "m. length"}:
        return "m length"
    if lower in {"nos", "no", "pc", "pcs", "piece", "pieces", "unit", "units"}:
        return "nos"
    if lower in {"lot", "lots"}:
        return "lot"
    if lower in {"set", "sets"}:
        return "sets"
    return text


PRICING_AUTHORITY_SCHEMA = "swooshz.pricing-authority.v1"
PRICING_AUTHORITY_VERSION = 1
PRICING_AUTHORITY_VARIANTS = {"none", "historical", "manual", "catalog", "included"}
PRICING_AUTHORITY_SOURCES = {"company", "local", "bundled"}
PRICING_AUTHORITY_CONTEXT_FIELDS = (
    "source_basis_line_id",
    "section",
    "description",
    "unit",
    "pricing_keyword",
)
PRICING_AUTHORITY_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
PRICING_AUTHORITY_WHITESPACE_RE = re.compile(
    r"[\u0009-\u000D\u001C-\u0020\u0085\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000\uFEFF]+"
)
PRICING_REFERENCE_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


def pricing_authority_context(value: Any) -> dict[str, str]:
    row = value if isinstance(value, dict) else {}
    return {
        "source_basis_line_id": canonical_pricing_authority_text(row.get("source_basis_line_id")),
        "section": canonical_pricing_authority_text(row.get("section")) or "General",
        "description": canonical_pricing_authority_text(row.get("description")),
        "unit": canonical_pricing_authority_unit(row.get("unit")),
        "pricing_keyword": canonical_pricing_authority_text(row.get("pricing_keyword")),
    }


def canonical_pricing_authority_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return PRICING_AUTHORITY_WHITESPACE_RE.sub(" ", text).strip(" ")


def canonical_pricing_authority_unit(value: Any) -> str:
    text = canonical_pricing_authority_text(value)
    lower = text.lower().strip(". ")
    if lower in {"m2", "m\u00b2", "m^2", "sq m", "sq.m", "sq.m.", "square metre", "square meter", "square metres", "square meters"}:
        return "sqm"
    if lower in {"m run", "m. run"}:
        return "m run"
    if lower in {"m length", "m. length"}:
        return "m length"
    if lower in {"nos", "no", "pc", "pcs", "piece", "pieces", "unit", "units"}:
        return "nos"
    if lower in {"lot", "lots"}:
        return "lot"
    if lower in {"set", "sets"}:
        return "sets"
    return text


def unit_quantity_basis(unit: Any) -> str:
    """What one unit of quantity counts (mirrors the webapp); nothing is ever converted."""
    canonical = canonical_pricing_authority_unit(normalize_unit(unit)).lower()
    if not canonical:
        return ""
    if canonical in {"m", "m length", "m run"}:
        return "length:metre"
    return {"sqm": "area:sqm", "nos": "count:piece", "sets": "count:set", "lot": "count:lot"}.get(canonical, "other:" + canonical)


def pricing_authority_version_is_valid(value: Any) -> bool:
    return type(value) is int and value == PRICING_AUTHORITY_VERSION


def pricing_authority_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and PRICING_AUTHORITY_DECIMAL_RE.fullmatch(value):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return round_commercial_cents(number)


def trusted_pricing_reference_identity(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != {"id", "source", "currency", "digest"}:
        return None
    if any(not isinstance(value.get(key), str) for key in ("id", "source", "currency", "digest")):
        return None
    if (
        not value["id"]
        or value["source"] not in PRICING_AUTHORITY_SOURCES
        or not re.fullmatch(r"[A-Z]{3}", value["currency"])
        or not PRICING_REFERENCE_DIGEST_RE.fullmatch(value["digest"])
    ):
        return None
    return dict(value)


def catalog_payload_digest(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("items"), list):
        return ""
    currency = payload.get("currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        return ""
    items = [dict(item) for item in payload["items"] if isinstance(item, dict)]
    if not items:
        return ""
    canonical = {"schema_version": 1, "currency": currency, "items": items}
    raw = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def normalize_pricing_authority(
    raw: Any,
    row: dict[str, Any],
    price_rows: list[PriceRow],
    trusted_reference: dict[str, Any] | None = None,
    quote_currency: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("schema") != PRICING_AUTHORITY_SCHEMA or not pricing_authority_version_is_valid(raw.get("version")):
        return None
    if not isinstance(raw.get("variant"), str):
        return None
    variant = raw["variant"]
    if variant not in PRICING_AUTHORITY_VARIANTS:
        return None
    allowed_keys = (
        {"schema", "version", "variant", "context"}
        if variant in {"none", "historical"}
        else (
            {"schema", "version", "variant", "context", "price", "currency"}
            if variant in {"manual", "included"}
            else {
                "schema", "version", "variant", "context", "price", "currency",
                "catalog_source", "catalog_item_id", "catalog_digest",
                "catalog_section", "catalog_description", "catalog_unit",
            }
        )
    )
    if variant == "manual" and "price_currency" in raw:
        allowed_keys.add("price_currency")
    if set(raw) != allowed_keys:
        return None
    supplied_context = raw.get("context")
    if not isinstance(supplied_context, dict):
        return None
    if set(supplied_context) != set(PRICING_AUTHORITY_CONTEXT_FIELDS):
        return None
    if any(not isinstance(supplied_context.get(key), str) for key in PRICING_AUTHORITY_CONTEXT_FIELDS):
        return None
    context = pricing_authority_context(row)
    canonical_supplied = {
        "source_basis_line_id": canonical_pricing_authority_text(supplied_context.get("source_basis_line_id")),
        "section": canonical_pricing_authority_text(supplied_context.get("section")) or "General",
        "description": canonical_pricing_authority_text(supplied_context.get("description")),
        "unit": canonical_pricing_authority_unit(supplied_context.get("unit")),
        "pricing_keyword": canonical_pricing_authority_text(supplied_context.get("pricing_keyword")),
    }
    if canonical_supplied != context:
        return None
    if variant in {"none", "historical"}:
        return {
            "schema": PRICING_AUTHORITY_SCHEMA,
            "version": PRICING_AUTHORITY_VERSION,
            "variant": variant,
            "context": context,
        }
    price = pricing_authority_number(raw.get("price"))
    if price is None or (variant == "included" and price != 0):
        return None
    trusted = trusted_pricing_reference_identity(trusted_reference)
    if trusted is None or not isinstance(raw.get("currency"), str) or raw["currency"] != trusted["currency"]:
        return None
    normalized: dict[str, Any] = {
        "schema": PRICING_AUTHORITY_SCHEMA,
        "version": PRICING_AUTHORITY_VERSION,
        "variant": variant,
        "context": context,
        "price": 0.0 if variant == "included" else price,
        "currency": raw["currency"],
    }
    if variant == "manual" and "price_currency" in raw:
        if not isinstance(raw["price_currency"], str) or not quote_currency or raw["price_currency"] != quote_currency:
            return None
        normalized["price_currency"] = quote_currency
    if variant == "catalog":
        if any(not isinstance(raw.get(key), str) for key in (
            "catalog_source", "catalog_item_id", "catalog_digest",
            "catalog_section", "catalog_description", "catalog_unit",
        )):
            return None
        item_id = raw["catalog_item_id"]
        catalog_source = raw["catalog_source"]
        catalog_digest = raw["catalog_digest"]
        matches = [item for item in price_rows if item.pricing_id == item_id]
        if len(matches) != 1:
            return None
        match = matches[0]
        if (
            catalog_source != trusted["source"]
            or catalog_digest != trusted["digest"]
            or match.catalog_digest != trusted["digest"]
            or not PRICING_REFERENCE_DIGEST_RE.fullmatch(catalog_digest)
            or item_id != match.pricing_id
            or price != match.sale_unit_price
            or canonical_pricing_authority_text(raw.get("catalog_section")) != (canonical_pricing_authority_text(match.section) or "General")
            or canonical_pricing_authority_text(raw.get("catalog_description")) != canonical_pricing_authority_text(match.description)
            or canonical_pricing_authority_unit(raw.get("catalog_unit")) != canonical_pricing_authority_unit(match.unit_hint)
        ):
            return None
        normalized.update({
            "catalog_source": catalog_source,
            "catalog_item_id": item_id,
            "catalog_digest": catalog_digest,
            "catalog_section": canonical_pricing_authority_text(match.section) or "General",
            "catalog_description": canonical_pricing_authority_text(match.description),
            "catalog_unit": canonical_pricing_authority_unit(match.unit_hint),
        })
    return normalized


def slugify_segment(value: Any, fallback: str = "item") -> str:
    raw = clean_text(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug or fallback


def resolve_default_output_dir(brief: dict[str, Any], provided_out: Path | None) -> Path:
    if provided_out is not None:
        return provided_out
    client_name = slugify_segment((brief.get("client") or {}).get("name"), "client")
    project_name = slugify_segment((brief.get("project") or {}).get("title"), "project")
    date_part = slugify_segment((brief.get("quote_date") or dt.date.today().isoformat()), "quote")
    return DEFAULT_OUTPUT_ROOT / client_name / project_name / date_part


def infer_unit(description: str) -> str:
    text = description.lower()
    leading_unit = re.match(r"^\s*(m\.?\s*run|m\.?\s*length|m2|sqm|nos\.?|no\.|lot\.?|sets?|m\.?)(?=\s|$)", text)
    if leading_unit:
        return normalize_unit(leading_unit.group(1))
    for unit in ("m2", "sqm", "m length", "m run", "m", "nos", "no.", "lot", "sets"):
        if unit in text:
            return normalize_unit(unit)
    return ""


def extract_price_rows_from_catalog(template_path: Path, trusted_reference: dict[str, Any] | None = None) -> list[PriceRow]:
    payload = json.loads(template_path.read_text(encoding="utf-8-sig"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("items"), list):
        raise ValueError(f"Unsupported pricing catalog schema: {template_path}")

    catalog_digest = catalog_payload_digest(payload)
    catalog_currency = payload.get("currency") if isinstance(payload.get("currency"), str) else ""
    price_rows: list[PriceRow] = []
    for index, item in enumerate(payload["items"], start=1):
        description = clean_text(item.get("description"))
        cost = as_float(item.get("internal_cost"), 0.0)
        markup = as_float(item.get("markup_multiplier"), 1.0)
        if not description or cost <= 0 or markup <= 0:
            continue
        remarks = item.get("remarks")
        if isinstance(remarks, list):
            remark = "; ".join(clean_text(value) for value in remarks if clean_text(value))
        else:
            remark = clean_text(remarks)
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        price_rows.append(
            PriceRow(
                row_number=index,
                section=clean_text(item.get("section")),
                description=description,
                unit_hint=normalize_unit(item.get("unit_hint")) or infer_unit(description),
                cost=cost,
                gst_multiplier=as_float(item.get("gst_multiplier"), 1.0) or 1.0,
                markup=markup,
                remark=remark,
                pricing_id=clean_text(item.get("id")),
                aliases=[clean_text(alias) for alias in aliases if clean_text(alias)],
                catalog_digest=catalog_digest,
                catalog_currency=catalog_currency,
            )
        )
    return price_rows


def extract_price_rows(template_path: Path, trusted_reference: dict[str, Any] | None = None) -> list[PriceRow]:
    if template_path.suffix.lower() == ".json":
        return extract_price_rows_from_catalog(template_path, trusted_reference=trusted_reference)
    raise ValueError(
        f"Pricing source must be a JSON catalog: {template_path}. "
        "Run scripts/build_pricing_catalog.py to convert the source template first."
    )


PRICE_MATCH_TOKEN_ALIASES = {
    "aluminium": "aluminum",
    "m2": "sqm",
    "printed": "print",
    "printing": "print",
    "prints": "print",
}


def price_match_token(value: str) -> str:
    token = PRICE_MATCH_TOKEN_ALIASES.get(value.lower(), value.lower())
    if token.endswith("ies") and len(token) > 4:
        token = f"{token[:-3]}y"
    elif token.endswith("s") and len(token) > 3 and not token.endswith("ss"):
        token = token[:-1]
    return PRICE_MATCH_TOKEN_ALIASES.get(token, token)


def tokens(text: str) -> set[str]:
    stop = {
        "and",
        "or",
        "of",
        "in",
        "at",
        "with",
        "for",
        "the",
        "as",
        "per",
        "ready",
        "construct",
        "not",
        "real",
        "sqm",
        "m2",
        "lm",
        "m",
        "nos",
        "no",
        "set",
        "sets",
        "each",
        "length",
        "run",
        "lot",
        "template",
        "item",
        "items",
    }
    result = {
        price_match_token(t)
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if (len(t) > 1 or t.isdigit()) and price_match_token(t) not in stop
    }
    result.update(t[:-1] for t in list(result) if len(t) > 3 and t.endswith("s"))
    return result


def score_price_row(query: str, row: PriceRow, section: str = "", unit: str = "") -> float:
    query_tokens = tokens(query)
    if not query_tokens:
        return 0
    haystack_values = [row.pricing_id, row.section, row.description, row.remark, row.unit_hint, *row.aliases]
    normalized_query = clean_text(query).lower()
    section_bonus = 1.5 if clean_text(section).casefold() and clean_text(section).casefold() == clean_text(row.section).casefold() else 0.0
    normalized_unit = normalize_unit(unit)
    row_unit = normalize_unit(row.unit_hint)
    unit_bonus = 3.0 if normalized_unit and row_unit == normalized_unit else 0.0
    unit_penalty = -1.5 if normalized_unit and row_unit and row_unit != normalized_unit else 0.0
    best_score = 0.0
    for value in haystack_values:
        normalized_value = clean_text(value).lower()
        value_tokens = tokens(value)
        if not normalized_value or not value_tokens:
            continue
        overlap = query_tokens & value_tokens
        if not overlap:
            continue
        phrase_bonus = 0
        if normalized_query == normalized_value:
            phrase_bonus = 20
        elif normalized_query in normalized_value:
            phrase_bonus = 10
        value_ratio = len(overlap) / max(len(value_tokens), 1)
        query_ratio = len(overlap) / max(min(len(query_tokens), 12), 1)
        score = (
            len(overlap) * 2.0
            + value_ratio * 6.0
            + query_ratio * 2.0
            + phrase_bonus
            + section_bonus
            + unit_bonus
            + unit_penalty
        )
        best_score = max(best_score, score)
    return best_score


def find_price_match(query: str, price_rows: list[PriceRow], section: str = "", unit: str = "") -> tuple[str, PriceRow | None, list[PriceRow]]:
    if not query:
        return "manual-display", None, []
    scored = [(score_price_row(query, row, section=section, unit=unit), row) for row in price_rows]
    scored = [(score, row) for score, row in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], item[1].pricing_id, item[1].row_number))
    candidates = [row for _, row in scored[:5]]
    if not candidates:
        return "unmatched", None, []
    top_score = scored[0][0]
    tied = [row for score, row in scored if score == top_score]
    if len(tied) > 1:
        return "ambiguous", tied[0], tied[:5]
    if len(scored) > 1 and top_score - scored[1][0] < 0.5:
        return "ambiguous", candidates[0], candidates
    return "matched", candidates[0], candidates


LINEAR_TAKEOFF_UNITS = {"m", "m length", "m run"}
PIECE_DIMENSION_DESCRIPTION_RE = re.compile(
    r"(?i)^(?:nos\.?\s+of\s+1m\s+length\s+x|m\.?\s*length\s*x)\b"
)


def is_piece_dimension_description(description: str, match: PriceRow | None = None) -> bool:
    values = [clean_text(description)]
    if match is not None:
        values.append(clean_text(match.description))
    return any(
        PIECE_DIMENSION_DESCRIPTION_RE.search(value) and len(re.findall(r"(?i)\bx\b", value)) >= 2
        for value in values
    )


def suspicious_piece_dimension_quantity(
    description: str,
    quantity: float | None,
    unit: str,
    match: PriceRow | None = None,
) -> bool:
    if not is_piece_dimension_description(description, match):
        return False
    normalized_unit = normalize_unit(unit).lower()
    if normalized_unit in LINEAR_TAKEOFF_UNITS:
        return True
    if quantity is None:
        return False
    return quantity > 0 and abs(quantity - round(quantity)) > 0.0001


def suspicious_linear_catalog_quantity(quantity: float | None, unit: str, match: PriceRow | None = None) -> bool:
    if quantity is None or abs(quantity - 1.0) > 0.0001:
        return False
    if normalize_unit(unit).lower() not in LINEAR_TAKEOFF_UNITS:
        return False
    if match is None:
        return False
    return normalize_unit(match.unit_hint).lower() in LINEAR_TAKEOFF_UNITS


def pricing_keyword_exactly_matches_catalog_id(pricing_keyword: str, match: PriceRow | None = None) -> bool:
    if match is None:
        return False
    return bool(pricing_keyword) and pricing_keyword.casefold() == clean_text(match.pricing_id).casefold()


REQUIRED_TOP_LEVEL = ("company_identity", "quote_date", "client", "project", "line_items")


def load_brief(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def validate_brief(brief: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for key in REQUIRED_TOP_LEVEL:
        if not brief.get(key):
            missing.append(key)
    client = brief.get("client") or {}
    project = brief.get("project") or {}
    if not client.get("name"):
        missing.append("client.name")
    if not client.get("attention"):
        missing.append("client.attention")
    if not project.get("title"):
        missing.append("project.title")
    return missing


def prepare_lines(
    brief: dict[str, Any],
    price_rows: list[PriceRow],
    allow_ambiguous: bool,
    *,
    authority_required: bool = False,
    trusted_pricing_reference: dict[str, Any] | None = None,
) -> list[QuoteLine]:
    prepared: list[QuoteLine] = []
    for item in brief.get("line_items", []):
        if not isinstance(item, dict):
            continue
        display_price = str(item.get("display_price") or "")
        price_mode = clean_text(item.get("price_mode")).title()
        if price_mode not in {"Priced", "Included"}:
            price_mode = "Included" if display_price.lower() == "included" else "Priced"
        unit_price_override = item.get("unit_price_override")
        unit_price_override_num = as_float(unit_price_override, 0.0) if unit_price_override not in (None, "") else None
        strict_manual_price = (
            pricing_authority_number(unit_price_override)
            if unit_price_override not in (None, "")
            else None
        )
        pricing_keyword = clean_text(item.get("pricing_keyword"))
        query = pricing_keyword or clean_text(item.get("description") or "")
        quantity = item.get("quantity")
        quantity_num = as_float(quantity, 0.0) if quantity not in (None, "") else None
        normalized_unit = normalize_unit(item.get("unit"))
        amount: float | None = None
        match: PriceRow | None = None
        candidates: list[PriceRow] = []
        authority = None
        strict_authority = authority_required or bool(brief.get("_pricing_authority_enforced")) or "pricing_authority" in item
        if strict_authority:
            authority = normalize_pricing_authority(item.get("pricing_authority"), {
                "source_basis_line_id": item.get("source_basis_line_id"),
                "section": clean_text(item.get("section")),
                "description": clean_text(item.get("description")),
                "unit": normalized_unit,
                "pricing_keyword": pricing_keyword,
            }, price_rows, trusted_reference=trusted_pricing_reference,
                quote_currency=clean_text(brief.get("currency")))
            variant = clean_text(authority.get("variant") if authority else "").lower()
            if (
                variant == "none"
                and brief.get("_pricing_authority_enforced") is True
                and isinstance(item.get("deferred_pricing"), str)
                and re.fullmatch(r"[0-9a-f]{64}", item["deferred_pricing"])
                and item.get("source_basis_line_id")
                and price_mode == "Priced"
                and not pricing_keyword and not display_price and unit_price_override in (None, "")
                and clean_text(item.get("description")) and normalized_unit
                and quantity_num is not None and math.isfinite(quantity_num) and quantity_num > 0
            ):
                status = "deferred-pricing"
                display_price = "Price to be discussed"
            elif authority is None or variant not in {"manual", "catalog", "included"}:
                status = "unmatched"
            elif variant == "included":
                status = "included"
                amount = 0.0
                display_price = "Included"
                price_mode = "Included"
            elif variant == "manual":
                status = "manual-price"
                unit_price_override_num = pricing_authority_number(authority.get("price"))
                amount = (
                    round_commercial_cents((quantity_num or 0.0) * unit_price_override_num)
                    if unit_price_override_num is not None
                    else None
                )
            else:
                item_id = clean_text(authority.get("catalog_item_id"))
                match = next((row for row in price_rows if row.pricing_id == item_id), None)
                row_basis = unit_quantity_basis(normalized_unit)
                if (
                    match is None
                    or match.sale_unit_price != pricing_authority_number(authority.get("price"))
                    or not row_basis
                    or row_basis != unit_quantity_basis(authority.get("catalog_unit"))
                ):
                    status = "unmatched"
                    match = None
                else:
                    status = "matched"
                    candidates = [match]
                    amount = round_commercial_cents((quantity_num or 0.0) * match.sale_unit_price)
        else:
            status, match, candidates = find_price_match(
                query,
                price_rows,
                section=clean_text(item.get("section")),
                unit=clean_text(item.get("unit")),
            )
            exact_catalog_id_match = pricing_keyword_exactly_matches_catalog_id(pricing_keyword, match)
            if price_mode == "Included":
                status = "included"
                amount = 0.0
                display_price = "Included"
                match = None
            elif unit_price_override_num is not None and strict_manual_price is not None:
                status = "manual-price"
                unit_price_override_num = strict_manual_price
                amount = round_commercial_cents((quantity_num or 0.0) * strict_manual_price)
                match = None
            elif unit_price_override_num is not None:
                status = "unmatched"
                unit_price_override_num = None
                amount = None
                match = None
            elif display_price:
                status = "manual-display"
            elif suspicious_piece_dimension_quantity(clean_text(item.get("description")), quantity_num, normalized_unit, match):
                status = "quantity-review"
                match = None
                amount = None
            elif suspicious_linear_catalog_quantity(quantity_num, normalized_unit, match) and not exact_catalog_id_match:
                status = "quantity-review"
                match = None
                amount = None
            elif status == "matched" or (status == "ambiguous" and allow_ambiguous):
                amount = round_commercial_cents((quantity_num or 0.0) * (match.sale_unit_price if match else 0.0))
                if status == "ambiguous" and allow_ambiguous:
                    status = "matched-from-ambiguous"
        prepared.append(
            QuoteLine(
                section=clean_text(item.get("section")),
                quantity=quantity_num,
                unit=normalized_unit,
                description=clean_text(item.get("description")),
                pricing_keyword=query,
                display_price=display_price,
                price_mode=price_mode,
                unit_price_override=unit_price_override_num,
                matched_price=match if amount is not None else None,
                amount=amount,
                match_status=status,
                match_candidates=candidates,
                pricing_authority=authority,
                diagnostic_row_id=str(item.get("diagnostic_row_id") or ""),
                source_basis_line_id=str(item.get("source_basis_line_id") or ""),
            )
        )
    return prepared


def confirmation_issues(missing: list[str], lines: list[QuoteLine]) -> list[str]:
    issues = [f"Missing required field: {field}" for field in missing]
    if any(line.match_status == "deferred-pricing" for line in lines) and not any(
        line.match_status in {"matched", "matched-from-ambiguous", "manual-price", "included"} for line in lines
    ):
        issues.append("Resolve pricing for at least one line item before generating Excel.")
    for line in lines:
        if line.match_status == "unmatched":
            issues.append(f"Unmatched pricing: {line.description} / keyword `{line.pricing_keyword}`")
        display_price = clean_text(line.display_price)
        unresolved_display_price = not display_price or display_price.lower() == "manual display price"
        if line.match_status == "manual-display" and unresolved_display_price and line.amount is None:
            issues.append(
                f"Manual display pricing required: {line.description} / "
                "enter a display price, choose a catalog keyword, or remove this line"
            )
        if line.match_status == "quantity-review":
            issues.append(
                f"Quantity needs review: {line.description} / "
                "confirm measured quantity before pricing"
            )
        if line.match_status == "ambiguous":
            options = "; ".join(
                f"{row.pricing_id}: {row.section} - {row.description} ({row.sale_unit_price:.2f})"
                for row in line.match_candidates[:3]
            )
            issues.append(f"Ambiguous pricing: {line.description}. Candidate matches: {options}")
    return issues


def print_missing_confirmation(issues: list[str]) -> None:
    print("Missing / Need Confirmation")
    for issue in issues:
        print(f"- {issue}")


def excel_col(index: int) -> str:
    result = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def xlsx_cell(row: int, col: int, value: Any) -> str:
    ref = f"{excel_col(col)}{row}"
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    safe = html.escape(str(value))
    return f'<c r="{ref}" t="inlineStr"><is><t>{safe}</t></is></c>'


def sheet_xml(rows: list[list[Any]]) -> str:
    row_xml = []
    for r_index, row in enumerate(rows, start=1):
        cells = "".join(xlsx_cell(r_index, c_index, value) for c_index, value in enumerate(row))
        row_xml.append(f'<row r="{r_index}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        + "".join(row_xml)
        + '</sheetData></worksheet>'
    )


def write_minimal_xlsx(path: Path, rows: list[list[Any]]) -> None:
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    workbook = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Quotation" sheetId="1" r:id="rId1"/></sheets>
</workbook>'''
    workbook_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml(rows))


def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_text_pdf(path: Path, title: str, lines: list[str]) -> None:
    width, height = 595, 842
    margin_x, y_start, line_height = 42, 800, 14
    pages = [lines[i:i + 50] for i in range(0, len(lines), 50)] or [[]]
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_refs = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{page_refs}] /Count {len(pages)} >>".encode("ascii"))
    for page_index, page_lines in enumerate(pages):
        content_obj = 4 + page_index * 2
        stream_lines = ["BT", "/F1 10 Tf", f"{margin_x} {y_start} Td"]
        stream_lines.append(f"({pdf_escape(title)}) Tj")
        stream_lines.append(f"0 -{line_height * 2} Td")
        for line in page_lines:
            stream_lines.append(f"({pdf_escape(line[:110])}) Tj")
            stream_lines.append(f"0 -{line_height} Td")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1", errors="replace")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << /Font << /F1 {3 + len(pages) * 2} 0 R >> >> /Contents {content_obj} 0 R >>".encode("ascii"))
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref_offset = sum(len(chunk) for chunk in chunks)
    chunks.append(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        chunks.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    chunks.append(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    path.write_bytes(b"".join(chunks))


def money(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.2f}"
    return clean_text(value)


def spreadsheet_safe_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped and stripped[0] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


class QuoteRichTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[list[RichTextRun]] = [[]]
        self.bold_depth = 0
        self.italic_depth = 0
        self.underline_depth = 0
        self.skip_depth = 0

    def _append_line(self) -> None:
        self.lines.append([])

    def _append_text(self, text: str) -> None:
        if not text:
            return
        run = RichTextRun(
            text,
            bold=self.bold_depth > 0,
            italic=self.italic_depth > 0,
            underline=self.underline_depth > 0,
        )
        current = self.lines[-1]
        if current and (
            current[-1].bold,
            current[-1].italic,
            current[-1].underline,
        ) == (run.bold, run.italic, run.underline):
            current[-1].text += run.text
        else:
            current.append(run)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"div", "p"}:
            if self.lines[-1]:
                self._append_line()
            return
        if tag == "br":
            self._append_line()
            return
        if tag in {"b", "strong"}:
            self.bold_depth += 1
        elif tag in {"i", "em"}:
            self.italic_depth += 1
        elif tag == "u":
            self.underline_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in {"div", "p"}:
            if self.lines[-1]:
                self._append_line()
            return
        if tag in {"b", "strong"}:
            self.bold_depth = max(0, self.bold_depth - 1)
        elif tag in {"i", "em"}:
            self.italic_depth = max(0, self.italic_depth - 1)
        elif tag == "u":
            self.underline_depth = max(0, self.underline_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        parts = data.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for index, part in enumerate(parts):
            if index:
                self._append_line()
            self._append_text(part)

    def parsed_lines(self) -> list[list[RichTextRun]]:
        while len(self.lines) > 1 and not self.lines[-1]:
            self.lines.pop()
        return self.lines


def parse_rich_text_html(value: Any) -> list[list[RichTextRun]]:
    raw = str(value or "")
    if not raw:
        return []
    parser = QuoteRichTextParser()
    parser.feed(raw)
    parser.close()
    return parser.parsed_lines()


def plain_rich_text_lines(lines: list[Any], *, bold: bool = False) -> list[list[RichTextRun]]:
    return [[RichTextRun(str(line), bold=bold)] for line in lines]


def brief_rich_text_lines(
    brief: dict[str, Any],
    key: str,
    fallback_lines: list[Any],
    *,
    fallback_bold: bool = False,
) -> list[list[RichTextRun]]:
    rich_text = brief.get("rich_text") if isinstance(brief.get("rich_text"), dict) else {}
    parsed = parse_rich_text_html(rich_text.get(key)) if isinstance(rich_text, dict) else []
    return parsed if parsed else plain_rich_text_lines(fallback_lines, bold=fallback_bold)


def brief_rich_text_items(
    brief: dict[str, Any], key: str, fallback_items: list[Any],
) -> list[list[RichTextRun]]:
    """Keep explicit lines within their supplied numbered item, without truncating rich text."""
    rich_text = brief.get("rich_text") if isinstance(brief.get("rich_text"), dict) else {}
    parsed = parse_rich_text_html(rich_text.get(key))
    if not parsed:
        return plain_rich_text_lines(fallback_items)
    grouped: list[list[RichTextRun]] = []
    cursor = 0
    for item in fallback_items:
        parts = str(item).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        lines = parsed[cursor:cursor + len(parts)]
        if len(lines) != len(parts) or any(
            clean_text("".join(run.text for run in runs)) != clean_text(text)
            for runs, text in zip(lines, parts)
        ):
            # Rich text remains authoritative; preserve every parsed line if grouping cannot be proved.
            return parsed
        runs: list[RichTextRun] = []
        for index, line in enumerate(lines):
            if index:
                runs.append(RichTextRun("\n"))
            runs.extend(line)
        grouped.append(runs)
        cursor += len(parts)
    return grouped + parsed[cursor:]


def brief_rich_text_cell_runs(
    brief: dict[str, Any],
    key: str,
    fallback: Any,
    *,
    prefix: str = "",
    fallback_bold: bool = False,
) -> list[RichTextRun]:
    lines = brief_rich_text_lines(brief, key, [fallback], fallback_bold=fallback_bold)
    runs = lines[0] if lines else [RichTextRun(str(fallback or ""), bold=fallback_bold)]
    return ([RichTextRun(prefix)] if prefix else []) + runs


def quote_tax_config(brief: dict[str, Any]) -> tuple[str, float]:
    tax = brief.get("tax") if isinstance(brief.get("tax"), dict) else {}
    label = clean_text(tax.get("label")).upper() or "GST"
    if label not in {"GST", "VAT"}:
        label = "GST"
    rate = as_float(tax.get("rate"), 0.09)
    if rate > 1:
        rate = rate / 100
    if rate < 0:
        rate = 0.0
    return label, min(rate, 1.0)


def quote_tax_rate(brief: dict[str, Any]) -> float:
    return quote_tax_config(brief)[1]


def quote_tax_label(brief: dict[str, Any]) -> str:
    label, rate = quote_tax_config(brief)
    percent = rate * 100
    if abs(percent - round(percent)) < 0.001:
        percent_text = str(int(round(percent)))
    else:
        percent_text = f"{percent:.2f}".rstrip("0").rstrip(".")
    return f"{label} {percent_text}%"


def quote_total_including_tax_label(brief: dict[str, Any]) -> str:
    label, _rate = quote_tax_config(brief)
    return f"Total including {label}"


def quote_exchange_rate(brief: dict[str, Any]) -> float:
    rate = as_float(brief.get("exchange_rate"), 1.0)
    return rate if rate > 0 else 1.0


def quote_amount_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def quote_amount(value: Any, exchange_rate: float = 1.0) -> float | None:
    amount = quote_amount_number(value)
    if amount is None:
        return None
    return round_commercial_cents(amount * exchange_rate)


def quote_subtotal(entries: list[dict[str, Any]]) -> float:
    total = 0.0
    for entry in entries:
        amount = entry.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            total += float(amount)
    rounded = round_commercial_cents(total)
    return rounded if rounded is not None else 0.0


def quantity_number_text(line: QuoteLine) -> str:
    if line.quantity is None:
        return ""
    if float(line.quantity).is_integer():
        return str(int(line.quantity))
    return str(line.quantity)


def quantity_text(line: QuoteLine) -> str:
    if line.quantity is None:
        return ""
    return f"{quantity_number_text(line)} {line.unit}".strip()


def build_quote_rows(brief: dict[str, Any], lines: list[QuoteLine]) -> list[list[Any]]:
    client = brief["client"]
    project = brief["project"]
    currency = brief.get("currency", "SGD")
    rows: list[list[Any]] = [
        ["", "", "", "", "ESTIMATE"],
        [client.get("name", "")],
        *[[line] for line in client.get("address", [])],
        ["Attention:"],
        ["", client.get("attention", "")],
        ["", client.get("title", "")],
        [],
        [],
        [brief.get("quote_date", "")],
        [project.get("title", "")],
        [],
        ["Pos.", "Quantity", "Service", "Estimate"],
        ["", "", "", currency],
    ]
    entries = render_quote_entries(lines, brief)
    for entry in entries:
        if entry["kind"] == "section":
            rows.append([entry["number"], "", entry["section"], money(entry.get("amount"))])
            if entry.get("coverage"):
                rows.append(["", "", "", entry["coverage"]])
            continue
        rows.append([entry["number"], entry["quantity"], " ".join(entry["description_lines"]), money(entry.get("amount"))])
    discount = round_commercial_cents(as_float(brief.get("discount"), 0.0)) or 0.0
    subtotal = round_commercial_cents(max(quote_subtotal(entries) - discount, 0.0)) or 0.0
    tax_rate = quote_tax_rate(brief)
    tax_amount = round_commercial_cents(subtotal * tax_rate) or 0.0
    final_total = round_commercial_cents(subtotal + tax_amount) or 0.0
    rows.extend([[], ["", "", "Total", money(subtotal), currency]])
    if discount:
        rows.insert(-1, ["", "", "Less goodwill discount", money(discount), currency])
    rows.append(["", "", quote_tax_label(brief), money(tax_amount), currency])
    rows.append(["", "", quote_total_including_tax_label(brief), money(final_total), currency])
    terms_heading = clean_text(brief.get("terms_heading"))
    payment_terms = brief.get("payment_terms") or []
    if terms_heading or payment_terms:
        rows.append([])
        if terms_heading:
            rows.append([terms_heading])
    for idx, term in enumerate(payment_terms, start=1):
        rows.append([idx, term])
    notes_heading = clean_text(brief.get("notes_heading"))
    standard_notes = brief.get("standard_notes") or []
    if notes_heading or standard_notes:
        rows.append([])
        if notes_heading:
            rows.append([notes_heading])
    for idx, note in enumerate(standard_notes, start=1):
        rows.append([idx, note])
    acceptance = brief.get("acceptance") if isinstance(brief.get("acceptance"), dict) else {}
    signature = brief.get("signature") if isinstance(brief.get("signature"), dict) else {}
    rows.extend([
        [],
        [brief.get("company_identity", "")],
        [],
        ["_____________________________", "", "_____________________________________"],
        [signature.get("company_signatory", ""), "", acceptance.get("person_label", "")],
        [signature.get("company_title", ""), "", acceptance.get("stamp_label", "")],
        [signature.get("company_date_label", ""), "", acceptance.get("date_label", "")],
    ])
    return rows


def build_pdf_lines(rows: list[list[Any]]) -> list[str]:
    result = []
    for row in rows:
        text = "  ".join(str(value) for value in row if value not in (None, ""))
        if text:
            wrapped = textwrap.wrap(text, width=105) or [text]
            result.extend(wrapped)
        else:
            result.append("")
    return result


def cell_ref(row: int, col: int) -> str:
    return f"{excel_col(col - 1)}{row}"


def parse_cell_ref(ref: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)([0-9]+)", ref.upper())
    if not match:
        raise ValueError(f"Invalid cell reference: {ref}")
    return int(match.group(2)), col_to_index(match.group(1)) + 1


def row_sort_key(row: ET.Element) -> int:
    return int(row.attrib.get("r", "0"))


def cell_sort_key(cell: ET.Element) -> int:
    return parse_cell_ref(cell.attrib.get("r", "A1"))[1]


def get_or_create_row(sheet_data: ET.Element, row_number: int) -> ET.Element:
    for row in sheet_data.findall(f"{NS_MAIN}row"):
        if int(row.attrib.get("r", "0")) == row_number:
            return row
    row = ET.Element(f"{NS_MAIN}row", {"r": str(row_number)})
    rows = sheet_data.findall(f"{NS_MAIN}row")
    insert_at = len(rows)
    for index, existing in enumerate(rows):
        if row_sort_key(existing) > row_number:
            insert_at = index
            break
    sheet_data.insert(insert_at, row)
    return row


def get_or_create_cell(row: ET.Element, row_number: int, col_number: int, style: str | None = None) -> ET.Element:
    ref = cell_ref(row_number, col_number)
    for cell in row.findall(f"{NS_MAIN}c"):
        if cell.attrib.get("r") == ref:
            if style is not None:
                cell.attrib["s"] = style
            return cell
    cell_attrib = {"r": ref}
    if style is not None:
        cell_attrib["s"] = style
    cell = ET.Element(f"{NS_MAIN}c", cell_attrib)
    cells = row.findall(f"{NS_MAIN}c")
    insert_at = len(cells)
    for index, existing in enumerate(cells):
        if cell_sort_key(existing) > col_number:
            insert_at = index
            break
    row.insert(insert_at, cell)
    return cell


def clear_cell(cell: ET.Element) -> None:
    for child in list(cell):
        if child.tag in {f"{NS_MAIN}v", f"{NS_MAIN}is", f"{NS_MAIN}f"}:
            cell.remove(child)
    cell.attrib.pop("t", None)


def set_ooxml_cell(root: ET.Element, row_number: int, col_number: int, value: Any, style: str | None = None) -> None:
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        raise ValueError("Layout workbook is missing sheetData.")
    row = get_or_create_row(sheet_data, row_number)
    cell = get_or_create_cell(row, row_number, col_number, style)
    clear_cell(cell)
    if value in (None, ""):
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = ET.SubElement(cell, f"{NS_MAIN}v")
        number.text = str(value)
        return
    cell.attrib["t"] = "inlineStr"
    inline = ET.SubElement(cell, f"{NS_MAIN}is")
    text = ET.SubElement(inline, f"{NS_MAIN}t")
    text.text = str(value)
    if str(value) != str(value).strip():
        text.attrib["{http://www.w3.org/XML/1998/namespace}space"] = "preserve"


def append_ooxml_text_run(
    inline: ET.Element,
    text_value: str,
    *,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    font_name: str | None = None,
    font_size: str | None = None,
    color: str | None = None,
) -> None:
    run = ET.SubElement(inline, f"{NS_MAIN}r")
    run_props = ET.SubElement(run, f"{NS_MAIN}rPr")
    if font_name:
        ET.SubElement(run_props, f"{NS_MAIN}rFont", {"val": font_name})
        ET.SubElement(run_props, f"{NS_MAIN}family", {"val": "2"})
    ET.SubElement(run_props, f"{NS_MAIN}b").attrib["val"] = "1" if bold else "0"
    if italic:
        ET.SubElement(run_props, f"{NS_MAIN}i")
    if color:
        ET.SubElement(run_props, f"{NS_MAIN}color", {"rgb": color})
    if font_size:
        ET.SubElement(run_props, f"{NS_MAIN}sz", {"val": font_size})
    if underline:
        ET.SubElement(run_props, f"{NS_MAIN}u")
    text = ET.SubElement(run, f"{NS_MAIN}t")
    text.text = text_value
    if text_value != text_value.strip():
        text.attrib["{http://www.w3.org/XML/1998/namespace}space"] = "preserve"


def normalize_ooxml_text_run(value: Any) -> RichTextRun:
    if isinstance(value, RichTextRun):
        return value
    if isinstance(value, dict):
        return RichTextRun(
            str(value.get("text") or ""),
            bold=bool(value.get("bold")),
            italic=bool(value.get("italic")),
            underline=bool(value.get("underline")),
        )
    if isinstance(value, tuple):
        text = str(value[0]) if value else ""
        return RichTextRun(
            text,
            bold=bool(value[1]) if len(value) > 1 else False,
            italic=bool(value[2]) if len(value) > 2 else False,
            underline=bool(value[3]) if len(value) > 3 else False,
        )
    return RichTextRun(str(value or ""))


def set_ooxml_rich_text_cell(
    root: ET.Element,
    row_number: int,
    col_number: int,
    runs: list[Any],
    style: str | None = None,
    *,
    font_name: str | None = None,
    font_size: str | None = None,
    color: str | None = None,
) -> None:
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        raise ValueError("Layout workbook is missing sheetData.")
    row = get_or_create_row(sheet_data, row_number)
    cell = get_or_create_cell(row, row_number, col_number, style)
    clear_cell(cell)
    cleaned_runs = [normalize_ooxml_text_run(run) for run in runs]
    cleaned_runs = [run for run in cleaned_runs if run.text]
    if not cleaned_runs:
        return
    cell.attrib["t"] = "inlineStr"
    inline = ET.SubElement(cell, f"{NS_MAIN}is")
    for run in cleaned_runs:
        append_ooxml_text_run(
            inline,
            run.text,
            bold=run.bold,
            italic=run.italic,
            underline=run.underline,
            font_name=font_name,
            font_size=font_size,
            color=color,
        )


def cached_number_text(value: float | int) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.12g}"


def set_ooxml_formula(
    root: ET.Element,
    row_number: int,
    col_number: int,
    formula: str,
    style: str | None = None,
    cached_value: float | int | None = None,
) -> None:
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        raise ValueError("Layout workbook is missing sheetData.")
    row = get_or_create_row(sheet_data, row_number)
    cell = get_or_create_cell(row, row_number, col_number, style)
    clear_cell(cell)
    formula_node = ET.SubElement(cell, f"{NS_MAIN}f")
    formula_node.text = formula[1:] if formula.startswith("=") else formula
    if cached_value is not None:
        value_node = ET.SubElement(cell, f"{NS_MAIN}v")
        value_node.text = cached_number_text(cached_value)


def ensure_worksheet_child(root: ET.Element, local_name: str) -> ET.Element:
    tag = f"{NS_MAIN}{local_name}"
    child = root.find(tag)
    if child is None:
        child = ET.Element(tag)
        root.append(child)
    return child


def ensure_merge_ref(root: ET.Element, ref: str) -> None:
    merge_cells = root.find(f"{NS_MAIN}mergeCells")
    if merge_cells is None:
        merge_cells = ET.Element(f"{NS_MAIN}mergeCells")
        root.append(merge_cells)

    for merge_cell in merge_cells.findall(f"{NS_MAIN}mergeCell"):
        if merge_cell.attrib.get("ref") == ref:
            merge_cells.attrib["count"] = str(len(merge_cells))
            return

    ET.SubElement(merge_cells, f"{NS_MAIN}mergeCell", {"ref": ref})
    merge_cells.attrib["count"] = str(len(merge_cells))


def ensure_quote_layout_page_settings(root: ET.Element) -> None:
    ensure_worksheet_child(root, "sheetPr")

    sheet_format = root.find(f"{NS_MAIN}sheetFormatPr")
    if sheet_format is None:
        sheet_format = ensure_worksheet_child(root, "sheetFormatPr")
        sheet_format.attrib.update({
            "defaultColWidth": "9.125",
            "defaultRowHeight": "17",
            f"{{{XMLNS_X14AC}}}dyDescent": "0.2",
        })

    if root.find(f"{NS_MAIN}pageMargins") is None:
        page_margins = ET.Element(
            f"{NS_MAIN}pageMargins",
            {
                "left": "0.82677165354330717",
                "right": "0.39370078740157483",
                "top": "0.74803149606299213",
                "bottom": "0.74803149606299213",
                "header": "0.31496062992125984",
                "footer": "0.31496062992125984",
            },
        )
        root.append(page_margins)

    if root.find(f"{NS_MAIN}pageSetup") is None:
        page_setup = ET.Element(
            f"{NS_MAIN}pageSetup",
            {
                "paperSize": "9",
                "scale": "70",
                "firstPageNumber": "0",
                "fitToHeight": "0",
                "orientation": "portrait",
                "horizontalDpi": "300",
                "verticalDpi": "300",
            },
        )
        root.append(page_setup)

    if root.find(f"{NS_MAIN}headerFooter") is None:
        root.append(ET.Element(f"{NS_MAIN}headerFooter", {"alignWithMargins": "0"}))


def ensure_quote_layout_row_heights(
    root: ET.Element,
    last_row: int,
    existing_row_numbers: set[int] | None = None,
) -> None:
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        return
    existing_row_numbers = existing_row_numbers or set()
    for row_number in range(1, last_row + 1):
        row = get_or_create_row(sheet_data, row_number)
        if row_number in existing_row_numbers:
            continue
        if row.attrib.get("ht") in {None, ""} and row.findall(f"{NS_MAIN}c"):
            row.attrib["ht"] = QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT
            row.attrib["customHeight"] = "1"


def complete_quote_layout_worksheet(
    root: ET.Element,
    last_row: int,
    existing_row_numbers: set[int] | None = None,
) -> None:
    ensure_quote_layout_page_settings(root)
    ensure_quote_layout_row_heights(root, last_row, existing_row_numbers)
    ensure_merge_ref(root, QUOTE_DATE_MERGE_REF)


def clear_ooxml_range(root: ET.Element, min_row: int, max_row: int, min_col: int, max_col: int) -> None:
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        return
    for row in sheet_data.findall(f"{NS_MAIN}row"):
        row_number = int(row.attrib.get("r", "0"))
        if not min_row <= row_number <= max_row:
            continue
        for cell in list(row.findall(f"{NS_MAIN}c")):
            _, col_number = parse_cell_ref(cell.attrib.get("r", "A1"))
            if min_col <= col_number <= max_col:
                row.remove(cell)


def ensure_content_type_override(parts: dict[str, bytes], part_name: str, content_type: str) -> None:
    content_types_name = "[Content_Types].xml"
    part_name = "/" + part_name.lstrip("/")
    if content_types_name in parts:
        root = ET.fromstring(parts[content_types_name])
    else:
        root = ET.Element(f"{NS_CONTENT_TYPES}Types")
    for child in root.findall(f"{NS_CONTENT_TYPES}Override"):
        if child.attrib.get("PartName") == part_name:
            child.attrib["ContentType"] = content_type
            break
    else:
        ET.SubElement(
            root,
            f"{NS_CONTENT_TYPES}Override",
            {"PartName": part_name, "ContentType": content_type},
        )
    parts[content_types_name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


def sanitize_core_properties(parts: dict[str, bytes]) -> None:
    core = ET.Element(f"{{{XMLNS_CP}}}coreProperties")
    tool_name = "Swooshz Quote Generator"
    ET.SubElement(core, f"{{{XMLNS_DC}}}creator").text = tool_name
    ET.SubElement(core, f"{{{XMLNS_CP}}}lastModifiedBy").text = tool_name
    parts["docProps/core.xml"] = ET.tostring(core, encoding="utf-8", xml_declaration=True)
    ensure_content_type_override(
        parts,
        "/docProps/core.xml",
        "application/vnd.openxmlformats-package.core-properties+xml",
    )
    rels_name = "_rels/.rels"
    rels_root = ET.fromstring(parts[rels_name]) if rels_name in parts else empty_relationships_root()
    has_core_rel = False
    for rel in rels_root.findall(f"{NS_PACKAGE_REL}Relationship"):
        rel_type = rel.attrib.get("Type", "")
        target = rel.attrib.get("Target", "")
        if rel_type.endswith("/metadata/core-properties") or target == "docProps/core.xml":
            rel.attrib["Type"] = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
            rel.attrib["Target"] = "docProps/core.xml"
            has_core_rel = True
            break
    if not has_core_rel:
        ET.SubElement(
            rels_root,
            f"{NS_PACKAGE_REL}Relationship",
            {
                "Id": next_relationship_id(rels_root),
                "Type": "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                "Target": "docProps/core.xml",
            },
        )
    parts[rels_name] = ET.tostring(rels_root, encoding="utf-8", xml_declaration=True)


def remove_workbook_absolute_paths(parts: dict[str, bytes]) -> None:
    workbook_xml = parts.get("xl/workbook.xml")
    if workbook_xml is None:
        return
    text = workbook_xml.decode("utf-8")
    text = re.sub(
        r"<mc:AlternateContent\b(?=[\s\S]*?\babsPath\b)[\s\S]*?</mc:AlternateContent>",
        "",
        text,
        count=1,
    )
    text = re.sub(
        r"<x15ac:absPath\b[^>]*/>",
        "",
        text,
    )
    parts["xl/workbook.xml"] = text.encode("utf-8")


def strip_stale_workbook_parts(parts: dict[str, bytes]) -> None:
    sanitize_core_properties(parts)
    remove_workbook_absolute_paths(parts)
    parts.pop("xl/sharedStrings.xml", None)
    for name in list(parts):
        if name.casefold().startswith("customxml/"):
            parts.pop(name, None)
    parts.pop("xl/calcChain.xml", None)
    if "[Content_Types].xml" in parts:
        root = ET.fromstring(parts["[Content_Types].xml"])
        for child in list(root):
            part_name = child.attrib.get("PartName")
            if (
                part_name in {"/xl/sharedStrings.xml", "/xl/calcChain.xml"}
                or (part_name or "").casefold().startswith("/customxml/")
            ):
                root.remove(child)
        parts["[Content_Types].xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if "_rels/.rels" in parts:
        root = ET.fromstring(parts["_rels/.rels"])
        for child in list(root):
            target = child.attrib.get("Target", "")
            rel_type = child.attrib.get("Type", "")
            if (
                target.lstrip("/").casefold().startswith("customxml/")
                or rel_type.rstrip("/").casefold().endswith("/customxml")
            ):
                root.remove(child)
        parts["_rels/.rels"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if "xl/_rels/workbook.xml.rels" in parts:
        root = ET.fromstring(parts["xl/_rels/workbook.xml.rels"])
        for child in list(root):
            target = child.attrib.get("Target")
            rel_type = child.attrib.get("Type", "")
            if (
                target in {"sharedStrings.xml", "calcChain.xml"}
                or rel_type.endswith("/sharedStrings")
                or rel_type.endswith("/calcChain")
            ):
                root.remove(child)
        parts["xl/_rels/workbook.xml.rels"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


def append_border(styles_root: ET.Element, top: str | None = None, bottom: str | None = None) -> int:
    borders = styles_root.find(f"{NS_MAIN}borders")
    if borders is None:
        borders = ET.SubElement(styles_root, f"{NS_MAIN}borders")

    border = ET.Element(f"{NS_MAIN}border")
    ET.SubElement(border, f"{NS_MAIN}left")
    ET.SubElement(border, f"{NS_MAIN}right")
    top_node = ET.SubElement(border, f"{NS_MAIN}top")
    if top:
        top_node.attrib["style"] = top
        ET.SubElement(top_node, f"{NS_MAIN}color", {"auto": "1"})
    bottom_node = ET.SubElement(border, f"{NS_MAIN}bottom")
    if bottom:
        bottom_node.attrib["style"] = bottom
        ET.SubElement(bottom_node, f"{NS_MAIN}color", {"auto": "1"})
    ET.SubElement(border, f"{NS_MAIN}diagonal")

    borders.append(border)
    borders.attrib["count"] = str(len(borders))
    return len(borders) - 1


def clone_cell_style(
    styles_root: ET.Element,
    base_style: str,
    *,
    border_id: int | None = None,
    font_id: str | None = None,
    num_fmt_id: str | None = None,
    horizontal: str | None = None,
    vertical: str | None = None,
    wrap_text: bool | None = None,
) -> str:
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    if cell_xfs is None:
        raise ValueError("Layout workbook is missing cellXfs styles.")

    style = copy.deepcopy(cell_xfs[int(base_style)])
    if border_id is not None:
        style.attrib["borderId"] = str(border_id)
        style.attrib["applyBorder"] = "1"
    if font_id is not None:
        style.attrib["fontId"] = font_id
        style.attrib["applyFont"] = "1"
    if num_fmt_id is not None:
        style.attrib["numFmtId"] = num_fmt_id
        style.attrib["applyNumberFormat"] = "1"
    if horizontal is not None or vertical is not None or wrap_text is not None:
        alignment = style.find(f"{NS_MAIN}alignment")
        if alignment is None:
            alignment = ET.SubElement(style, f"{NS_MAIN}alignment")
        if horizontal is not None:
            alignment.attrib["horizontal"] = horizontal
        if vertical is not None:
            alignment.attrib["vertical"] = vertical
        if wrap_text is not None:
            alignment.attrib["wrapText"] = "1" if wrap_text else "0"
        style.attrib["applyAlignment"] = "1"
    cell_xfs.append(style)
    cell_xfs.attrib["count"] = str(len(cell_xfs))
    return str(len(cell_xfs) - 1)


def ensure_bold_font_for_style(styles_root: ET.Element, base_style: str) -> str:
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    fonts = styles_root.find(f"{NS_MAIN}fonts")
    if cell_xfs is None or fonts is None:
        raise ValueError("Layout workbook is missing cellXfs or fonts styles.")

    base_font_id = int(cell_xfs[int(base_style)].attrib.get("fontId", "0"))
    base_font = fonts[base_font_id]
    if base_font.find(f"{NS_MAIN}b") is not None:
        return str(base_font_id)

    bold_font = copy.deepcopy(base_font)
    bold_font.insert(0, ET.Element(f"{NS_MAIN}b"))
    fonts.append(bold_font)
    fonts.attrib["count"] = str(len(fonts))
    return str(len(fonts) - 1)


def ensure_regular_font_for_style(styles_root: ET.Element, base_style: str, *, bold: bool = False) -> str:
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    fonts = styles_root.find(f"{NS_MAIN}fonts")
    if cell_xfs is None or fonts is None:
        raise ValueError("Layout workbook is missing cellXfs or fonts styles.")

    base_font_id = int(cell_xfs[int(base_style)].attrib.get("fontId", "0"))
    regular_font = copy.deepcopy(fonts[base_font_id])
    for child in list(regular_font):
        if child.tag in {f"{NS_MAIN}i", f"{NS_MAIN}color"}:
            regular_font.remove(child)
    if bold and regular_font.find(f"{NS_MAIN}b") is None:
        regular_font.insert(0, ET.Element(f"{NS_MAIN}b"))
    fonts.append(regular_font)
    fonts.attrib["count"] = str(len(fonts))
    return str(len(fonts) - 1)


def ensure_font_for_style(
    styles_root: ET.Element,
    base_style: str,
    *,
    font_name: str | None = None,
    font_size: str | None = None,
    color: str | None = None,
) -> str:
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    fonts = styles_root.find(f"{NS_MAIN}fonts")
    if cell_xfs is None or fonts is None:
        raise ValueError("Layout workbook is missing cellXfs or fonts styles.")

    base_font_id = int(cell_xfs[int(base_style)].attrib.get("fontId", "0"))
    font = copy.deepcopy(fonts[base_font_id])
    if color is not None:
        set_font_color(font, color)
    if font_name is not None:
        name = font.find(f"{NS_MAIN}name")
        if name is None:
            name = ET.SubElement(font, f"{NS_MAIN}name")
        name.attrib["val"] = font_name
    if font_size is not None:
        size = font.find(f"{NS_MAIN}sz")
        if size is None:
            size = ET.SubElement(font, f"{NS_MAIN}sz")
        size.attrib["val"] = font_size
    fonts.append(font)
    fonts.attrib["count"] = str(len(fonts))
    return str(len(fonts) - 1)


def set_font_color(font: ET.Element, color: str) -> None:
    """Give a cloned font an explicit RGB colour, kept in the schema's child order."""
    for child in font.findall(f"{NS_MAIN}color"):
        font.remove(child)
    element = ET.Element(f"{NS_MAIN}color", {"rgb": color})
    later = (f"{NS_MAIN}name", f"{NS_MAIN}family", f"{NS_MAIN}charset", f"{NS_MAIN}scheme")
    for index, child in enumerate(list(font)):
        if child.tag in later:
            font.insert(index, element)
            return
    font.append(element)


def add_quote_layout_styles(parts: dict[str, bytes]) -> dict[str, str]:
    styles_root = ET.fromstring(parts["xl/styles.xml"])
    total_border = append_border(styles_root, top="thin")
    grand_border = append_border(styles_root, top="thin", bottom="double")
    regular_amount_font = ensure_regular_font_for_style(styles_root, "5")
    bold_amount_font = ensure_regular_font_for_style(styles_root, "5", bold=True)
    small_heading_font = ensure_font_for_style(styles_root, "37")
    small_number_font = ensure_font_for_style(styles_root, "40")
    small_body_font = ensure_font_for_style(styles_root, "41")
    exclusions_heading_font = ensure_font_for_style(styles_root, "37", color=QUOTE_EXCLUSIONS_COLOR)
    exclusions_body_font = ensure_font_for_style(styles_root, "41", color=QUOTE_EXCLUSIONS_COLOR)
    signature_text_font = ensure_font_for_style(styles_root, "2")
    signature_line_font = ensure_font_for_style(styles_root, "33")
    client_name_font = ensure_font_for_style(styles_root, "12")
    client_address_font = ensure_font_for_style(styles_root, "93")
    client_attention_font = ensure_font_for_style(styles_root, "26")
    client_title_font = ensure_font_for_style(styles_root, "24")
    style_ids = {
        "quote_date": "98",
        "header_pos": clone_cell_style(styles_root, "23", font_id=ensure_bold_font_for_style(styles_root, "23")),
        "header_quantity": clone_cell_style(styles_root, "24", font_id=ensure_bold_font_for_style(styles_root, "24"), horizontal="center", vertical="center"),
        "header_service": clone_cell_style(styles_root, "21", font_id=ensure_bold_font_for_style(styles_root, "21"), horizontal="left", vertical="center"),
        "header_estimate": clone_cell_style(styles_root, "87", font_id=ensure_bold_font_for_style(styles_root, "87"), horizontal="right", vertical="center"),
        "header_currency": clone_cell_style(styles_root, "95", font_id=ensure_bold_font_for_style(styles_root, "95"), horizontal="right", vertical="center"),
        "client_name": clone_cell_style(styles_root, "12", font_id=client_name_font),
        "client_address": clone_cell_style(styles_root, "93", font_id=client_address_font, horizontal="left", vertical="center"),
        "client_attention": clone_cell_style(styles_root, "26", font_id=client_attention_font),
        "client_title": clone_cell_style(styles_root, "24", font_id=client_title_font),
        "price_amount": clone_cell_style(styles_root, "5", font_id=regular_amount_font, num_fmt_id="4", horizontal="right", vertical="center"),
        "total_label": clone_cell_style(styles_root, "34", border_id=total_border, horizontal="right", vertical="center"),
        "total_amount": clone_cell_style(styles_root, "5", font_id=bold_amount_font, border_id=total_border, num_fmt_id="4", horizontal="right", vertical="center"),
        "total_currency": clone_cell_style(styles_root, "84", border_id=total_border, horizontal="center", vertical="center"),
        "gst_label": clone_cell_style(styles_root, "34"),
        "gst_amount": clone_cell_style(styles_root, "5", font_id=bold_amount_font, num_fmt_id="4", horizontal="right", vertical="center"),
        "gst_currency": clone_cell_style(styles_root, "84", horizontal="center", vertical="center"),
        "grand_label": clone_cell_style(styles_root, "34", border_id=grand_border, horizontal="right", vertical="center"),
        "grand_amount": clone_cell_style(styles_root, "5", font_id=bold_amount_font, border_id=grand_border, num_fmt_id="4", horizontal="right", vertical="center"),
        "grand_currency": clone_cell_style(styles_root, "84", border_id=grand_border, horizontal="center", vertical="center"),
        "terms_heading": clone_cell_style(styles_root, "37", font_id=small_heading_font, wrap_text=True),
        "terms_number": clone_cell_style(styles_root, "40", font_id=small_number_font),
        "terms_body": clone_cell_style(styles_root, "41", font_id=small_body_font, wrap_text=True),
        "exclusions_heading": clone_cell_style(styles_root, "37", font_id=exclusions_heading_font, wrap_text=True),
        "exclusions_body": clone_cell_style(styles_root, "41", font_id=exclusions_body_font, wrap_text=True),
        "signature_text": clone_cell_style(styles_root, "2", font_id=signature_text_font),
        "signature_line": clone_cell_style(styles_root, "33", font_id=signature_line_font),
    }
    parts["xl/styles.xml"] = serialize_excel_styles(styles_root)
    return style_ids


def ooxml_style_text_font(parts: dict[str, bytes], style: str) -> dict[str, str | None]:
    """Make rich runs use the selected cell font across spreadsheet readers."""
    styles = ET.fromstring(parts["xl/styles.xml"])
    cell_style = styles.find(f"{NS_MAIN}cellXfs")[int(style)]
    font = styles.find(f"{NS_MAIN}fonts")[int(cell_style.attrib.get("fontId", "0"))]
    name = font.find(f"{NS_MAIN}name")
    size = font.find(f"{NS_MAIN}sz")
    return {
        "font_name": name.attrib["val"] if name is not None else None,
        "font_size": size.attrib["val"] if size is not None else None,
    }


def update_drawing_project_number(xml: bytes, project_number: str) -> bytes:
    project_number = clean_text(project_number)
    if not project_number:
        return xml
    text = xml.decode("utf-8")
    replacement = html.escape(project_number, quote=False)
    updated, count = re.subn(
        r"(<a:t>\s*Project No:\s*</a:t>.*?<a:t>)(.*?)(</a:t>)",
        rf"\g<1>{replacement}\g<3>",
        text,
        count=1,
        flags=re.DOTALL,
    )
    return updated.encode("utf-8") if count else xml


def append_drawing_text_run(paragraph: ET.Element, run: RichTextRun) -> None:
    drawing_run = ET.SubElement(paragraph, f"{NS_A}r")
    run_props = ET.SubElement(drawing_run, f"{NS_A}rPr")
    run_props.attrib.update(
        {
            "lang": "en-US",
            "sz": "900",
            "b": "1" if run.bold else "0",
            "i": "1" if run.italic else "0",
            "baseline": "0",
        }
    )
    if run.underline:
        run_props.attrib["u"] = "sng"
    ET.SubElement(run_props, f"{NS_A}latin").attrib["typeface"] = "+mn-lt"
    ET.SubElement(run_props, f"{NS_A}ea").attrib["typeface"] = "+mn-ea"
    ET.SubElement(run_props, f"{NS_A}cs").attrib["typeface"] = "+mn-cs"
    text = ET.SubElement(drawing_run, f"{NS_A}t")
    text.text = run.text


def update_repeating_header_drawing(
    xml: bytes,
    project_number: str,
    header_lines: list[str] | None = None,
    header_runs: list[list[RichTextRun]] | None = None,
) -> bytes:
    root = ET.fromstring(xml)
    anchors = root.findall(f"{NS_DRAWING}twoCellAnchor")
    text_anchor = next((anchor for anchor in anchors if anchor.find(f"{NS_DRAWING}sp") is not None), None)
    if text_anchor is None:
        return xml

    sp = text_anchor.find(f"{NS_DRAWING}sp")
    tx_body = sp.find(f"{NS_DRAWING}txBody") if sp is not None else None
    if tx_body is None:
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    source_paragraph = tx_body.find(f"{NS_A}p")
    source_paragraph_props = (
        copy.deepcopy(source_paragraph.find(f"{NS_A}pPr"))
        if source_paragraph is not None and source_paragraph.find(f"{NS_A}pPr") is not None
        else None
    )
    source_end_para_props = (
        copy.deepcopy(source_paragraph.find(f"{NS_A}endParaRPr"))
        if source_paragraph is not None and source_paragraph.find(f"{NS_A}endParaRPr") is not None
        else None
    )

    for child in list(tx_body):
        if child.tag == f"{NS_A}p":
            tx_body.remove(child)

    source_runs = header_runs or plain_rich_text_lines([clean_text(line) if line is not None else "" for line in (header_lines or [])])
    line_runs = [list(runs) for runs in source_runs]
    if project_number:
        line_runs.extend([[], [RichTextRun(f"Project No: {project_number}")]])

    for runs in line_runs:
        paragraph = ET.SubElement(tx_body, f"{NS_A}p")
        if source_paragraph_props is not None:
            paragraph.append(copy.deepcopy(source_paragraph_props))
        else:
            paragraph_props = ET.SubElement(paragraph, f"{NS_A}pPr")
            paragraph_props.attrib["algn"] = "l"
        if not runs:
            append_drawing_text_run(paragraph, RichTextRun(""))
        else:
            for run in runs:
                append_drawing_text_run(paragraph, run)
        if source_end_para_props is not None:
            paragraph.append(copy.deepcopy(source_end_para_props))

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def drawing_target_part(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    normalized = Path("xl/drawings") / target
    parts: list[str] = []
    for item in normalized.as_posix().split("/"):
        if item == "..":
            if parts:
                parts.pop()
            continue
        if item and item != ".":
            parts.append(item)
    return "/".join(parts)


def empty_relationships_root() -> ET.Element:
    return ET.Element(f"{NS_PACKAGE_REL}Relationships")


def rel_targets_by_id(parts: dict[str, bytes], rels_name: str = "xl/drawings/_rels/drawing1.xml.rels") -> dict[str, str]:
    if rels_name not in parts:
        return {}
    root = ET.fromstring(parts[rels_name])
    return {
        rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
        for rel in root.findall(f"{NS_PACKAGE_REL}Relationship")
    }


def picture_rel_id(pic: ET.Element | None) -> str:
    if pic is None:
        return ""
    blip = pic.find(f".//{NS_A}blip")
    return blip.attrib.get(f"{NS_REL}embed", "") if blip is not None else ""


def picture_name(pic: ET.Element | None) -> str:
    if pic is None:
        return ""
    node = pic.find(f"{NS_DRAWING}nvPicPr/{NS_DRAWING}cNvPr")
    return node.attrib.get("name", "") if node is not None else ""


def is_header_logo_target(target: str) -> bool:
    name = Path(drawing_target_part(target)).name.lower()
    return "logo" in name or name in {"image1.jpeg", "image1.jpg", "image1.png"}


def is_header_logo_anchor(anchor: ET.Element, rel_targets: dict[str, str]) -> bool:
    pic = anchor.find(f"{NS_DRAWING}pic")
    if pic is None:
        return False
    if "logo" in picture_name(pic).lower():
        return True
    rel_id = picture_rel_id(pic)
    return bool(rel_id and is_header_logo_target(rel_targets.get(rel_id, "")))


def find_header_logo_anchor(root: ET.Element, rel_targets: dict[str, str]) -> ET.Element | None:
    return next(
        (
            anchor
            for anchor in root.findall(f"{NS_DRAWING}twoCellAnchor")
            if is_header_logo_anchor(anchor, rel_targets)
        ),
        None,
    )


def remove_header_logo(parts: dict[str, bytes]) -> None:
    drawing_name = "xl/drawings/drawing1.xml"
    rels_name = "xl/drawings/_rels/drawing1.xml.rels"
    removed_rel_ids: set[str] = set()
    rel_targets = rel_targets_by_id(parts, rels_name)
    if drawing_name in parts:
        drawing_root = ET.fromstring(parts[drawing_name])
        for anchor in list(drawing_root.findall(f"{NS_DRAWING}twoCellAnchor")):
            if not is_header_logo_anchor(anchor, rel_targets):
                continue
            rel_id = picture_rel_id(anchor.find(f"{NS_DRAWING}pic"))
            if rel_id:
                removed_rel_ids.add(rel_id)
            drawing_root.remove(anchor)
        parts[drawing_name] = ET.tostring(drawing_root, encoding="utf-8", xml_declaration=True)

    if rels_name not in parts:
        return
    rels_root = ET.fromstring(parts[rels_name])
    removed_targets: list[str] = []
    for rel in list(rels_root.findall(f"{NS_PACKAGE_REL}Relationship")):
        is_removed_rel = bool(removed_rel_ids and rel.attrib.get("Id") in removed_rel_ids)
        is_lonely_image = not removed_rel_ids and rel.attrib.get("Type", "").endswith("/image") and is_header_logo_target(rel.attrib.get("Target", ""))
        if not (is_removed_rel or is_lonely_image):
            continue
        target = rel.attrib.get("Target", "")
        if target:
            removed_targets.append(drawing_target_part(target))
        rels_root.remove(rel)
    parts[rels_name] = ET.tostring(rels_root, encoding="utf-8", xml_declaration=True)
    for target in removed_targets:
        parts.pop(target, None)


def next_relationship_id(root: ET.Element) -> str:
    existing: set[int] = set()
    for rel in root.findall(f"{NS_PACKAGE_REL}Relationship"):
        match = re.fullmatch(r"rId([0-9]+)", rel.attrib.get("Id", ""))
        if match:
            existing.add(int(match.group(1)))
    next_id = 1
    while next_id in existing:
        next_id += 1
    return f"rId{next_id}"


def image_dimensions(image_bytes: bytes, mime_type: str) -> tuple[int, int] | None:
    if mime_type == "image/png" and image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(image_bytes) >= 24 and image_bytes[12:16] == b"IHDR":
            width = int.from_bytes(image_bytes[16:20], "big")
            height = int.from_bytes(image_bytes[20:24], "big")
            if width > 0 and height > 0:
                return width, height
        return None

    if mime_type != "image/jpeg" or not image_bytes.startswith(b"\xff\xd8"):
        return None

    index = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while index + 3 < len(image_bytes):
        if image_bytes[index] != 0xFF:
            index += 1
            continue
        while index < len(image_bytes) and image_bytes[index] == 0xFF:
            index += 1
        if index >= len(image_bytes):
            break
        marker = image_bytes[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            break
        if index + 2 > len(image_bytes):
            break
        segment_length = int.from_bytes(image_bytes[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(image_bytes):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(image_bytes[index + 3:index + 5], "big")
            width = int.from_bytes(image_bytes[index + 5:index + 7], "big")
            if width > 0 and height > 0:
                return width, height
        index += segment_length
    return None


def header_logo_row_height_emu(parts: dict[str, bytes] | None = None) -> int:
    row_height = QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT
    if parts and "xl/worksheets/sheet1.xml" in parts:
        worksheet = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
        sheet_format = worksheet.find(f"{NS_MAIN}sheetFormatPr")
        default_height = (
            sheet_format.attrib.get("defaultRowHeight")
            if sheet_format is not None
            else None
        )
        row_height = default_height or row_height
        for row in worksheet.findall(f"{NS_MAIN}sheetData/{NS_MAIN}row"):
            if row.attrib.get("r") == "2":
                row_height = row.attrib.get("ht") or row_height
                break
    try:
        return max(1, round(float(row_height) * EMU_PER_POINT))
    except (TypeError, ValueError):
        return round(float(QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT) * EMU_PER_POINT)


def header_logo_region_extent(parts: dict[str, bytes] | None = None) -> tuple[int, int]:
    return (
        HEADER_LOGO_CANONICAL_WIDTH_EMU,
        header_logo_row_height_emu(parts) + HEADER_LOGO_CANONICAL_ROW_OFFSET_EMU,
    )


def fitted_header_logo_extent(
    dimensions: tuple[int, int] | None,
    region_extent: tuple[int, int] | None = None,
) -> tuple[int, int]:
    max_width, max_height = region_extent or (HEADER_LOGO_MAX_WIDTH_EMU, HEADER_LOGO_MAX_HEIGHT_EMU)
    if not dimensions or dimensions[0] <= 0 or dimensions[1] <= 0:
        return max_width, max_height
    width, height = dimensions
    aspect = width / height
    max_aspect = max_width / max_height
    if aspect >= max_aspect:
        fitted_width = max_width
        fitted_height = max(1, round(fitted_width / aspect))
    else:
        fitted_height = max_height
        fitted_width = max(1, round(fitted_height * aspect))
    return fitted_width, fitted_height


def header_logo_anchor_to_values(
    extent: tuple[int, int],
    row_height_emu: int,
) -> dict[str, str]:
    width, height = extent
    if height < row_height_emu:
        row = HEADER_LOGO_ANCHOR_FROM["row"]
        row_offset = height
    else:
        row = HEADER_LOGO_ANCHOR_TO["row"]
        row_offset = height - row_height_emu
    return {
        "col": HEADER_LOGO_ANCHOR_TO["col"],
        "colOff": str(width),
        "row": row,
        "rowOff": str(row_offset),
    }


def set_header_logo_anchor_extent(
    anchor: ET.Element,
    extent: tuple[int, int],
    row_height_emu: int,
) -> None:
    from_marker = anchor.find(f"{NS_DRAWING}from")
    if from_marker is not None:
        for tag, value in HEADER_LOGO_ANCHOR_FROM.items():
            node = from_marker.find(f"{NS_DRAWING}{tag}")
            if node is not None:
                node.text = value
    to_marker = anchor.find(f"{NS_DRAWING}to")
    if to_marker is not None:
        for tag, value in header_logo_anchor_to_values(extent, row_height_emu).items():
            node = to_marker.find(f"{NS_DRAWING}{tag}")
            if node is not None:
                node.text = value


def set_header_logo_extent(
    pic: ET.Element,
    dimensions: tuple[int, int] | None,
    region_extent: tuple[int, int] | None = None,
) -> tuple[int, int]:
    # Replacement media supplies the complete logo, without a template crop.
    crop = pic.find(f"{NS_DRAWING}blipFill/{NS_A}srcRect")
    if crop is not None:
        crop.attrib.clear()
    pic_pr = pic.find(f"{NS_DRAWING}spPr")
    if pic_pr is None:
        return fitted_header_logo_extent(dimensions, region_extent)
    pic_xfrm = pic_pr.find(f"{NS_A}xfrm")
    if pic_xfrm is None:
        pic_xfrm = ET.SubElement(pic_pr, f"{NS_A}xfrm")
    logo_off = pic_xfrm.find(f"{NS_A}off")
    if logo_off is None:
        logo_off = ET.SubElement(pic_xfrm, f"{NS_A}off")
    # The admitted logo region is the source-layout contract used by the
    # outer anchor.  Reset stale source offsets so the inner transform and
    # worksheet anchor remain one placement after image replacement.
    logo_off.attrib.update({"x": str(HEADER_LOGO_OFFSET_X_EMU), "y": str(HEADER_LOGO_OFFSET_Y_EMU)})
    logo_ext = pic_xfrm.find(f"{NS_A}ext")
    if logo_ext is None:
        logo_ext = ET.SubElement(pic_xfrm, f"{NS_A}ext")
    width, height = fitted_header_logo_extent(dimensions, region_extent)
    logo_ext.attrib.update({"cx": str(width), "cy": str(height)})
    return width, height


def create_header_logo_anchor(
    rel_id: str,
    dimensions: tuple[int, int] | None = None,
    region_extent: tuple[int, int] | None = None,
    row_height_emu: int | None = None,
) -> ET.Element:
    fitted_extent = fitted_header_logo_extent(dimensions, region_extent)
    row_height_emu = row_height_emu or header_logo_row_height_emu()
    anchor = ET.Element(f"{NS_DRAWING}twoCellAnchor")
    from_marker = ET.SubElement(anchor, f"{NS_DRAWING}from")
    for tag, value in HEADER_LOGO_ANCHOR_FROM.items():
        ET.SubElement(from_marker, f"{NS_DRAWING}{tag}").text = value
    to_marker = ET.SubElement(anchor, f"{NS_DRAWING}to")
    for tag, value in header_logo_anchor_to_values(fitted_extent, row_height_emu).items():
        ET.SubElement(to_marker, f"{NS_DRAWING}{tag}").text = value

    pic = ET.SubElement(anchor, f"{NS_DRAWING}pic")
    nv_pic_pr = ET.SubElement(pic, f"{NS_DRAWING}nvPicPr")
    ET.SubElement(nv_pic_pr, f"{NS_DRAWING}cNvPr", {"id": "6833", "name": "Header Logo"})
    c_nv_pic_pr = ET.SubElement(nv_pic_pr, f"{NS_DRAWING}cNvPicPr")
    ET.SubElement(c_nv_pic_pr, f"{NS_A}picLocks", {"noChangeAspect": "1"})

    blip_fill = ET.SubElement(pic, f"{NS_DRAWING}blipFill")
    ET.SubElement(blip_fill, f"{NS_A}blip", {f"{NS_REL}embed": rel_id, "cstate": "print"})
    ET.SubElement(blip_fill, f"{NS_A}srcRect")
    stretch = ET.SubElement(blip_fill, f"{NS_A}stretch")
    ET.SubElement(stretch, f"{NS_A}fillRect")

    sp_pr = ET.SubElement(pic, f"{NS_DRAWING}spPr", {"bwMode": "auto"})
    xfrm = ET.SubElement(sp_pr, f"{NS_A}xfrm")
    ET.SubElement(xfrm, f"{NS_A}off", {"x": str(HEADER_LOGO_OFFSET_X_EMU), "y": str(HEADER_LOGO_OFFSET_Y_EMU)})
    logo_width, logo_height = fitted_extent
    ET.SubElement(xfrm, f"{NS_A}ext", {"cx": str(logo_width), "cy": str(logo_height)})
    prst_geom = ET.SubElement(sp_pr, f"{NS_A}prstGeom", {"prst": "rect"})
    ET.SubElement(prst_geom, f"{NS_A}avLst")
    ET.SubElement(sp_pr, f"{NS_A}noFill")
    line = ET.SubElement(sp_pr, f"{NS_A}ln", {"w": "9525"})
    ET.SubElement(line, f"{NS_A}noFill")
    ET.SubElement(line, f"{NS_A}miter", {"lim": "800000"})
    ET.SubElement(line, f"{NS_A}headEnd")
    ET.SubElement(line, f"{NS_A}tailEnd")
    ET.SubElement(anchor, f"{NS_DRAWING}clientData")
    return anchor


def ensure_header_logo_anchor(
    parts: dict[str, bytes],
    rel_id: str,
    dimensions: tuple[int, int] | None = None,
) -> None:
    drawing_name = "xl/drawings/drawing1.xml"
    if drawing_name not in parts:
        return
    drawing_root = ET.fromstring(parts[drawing_name])
    rel_targets = rel_targets_by_id(parts)
    anchor = find_header_logo_anchor(drawing_root, rel_targets)
    region_extent = header_logo_region_extent(parts)
    row_height_emu = header_logo_row_height_emu(parts)
    fitted_extent = fitted_header_logo_extent(dimensions, region_extent)
    if anchor is not None:
        pic = anchor.find(f"{NS_DRAWING}pic")
        blip = pic.find(f".//{NS_A}blip")
        if blip is not None:
            blip.attrib[f"{NS_REL}embed"] = rel_id
        if pic is not None:
            set_header_logo_extent(pic, dimensions, region_extent)
        set_header_logo_anchor_extent(anchor, fitted_extent, row_height_emu)
        parts[drawing_name] = ET.tostring(drawing_root, encoding="utf-8", xml_declaration=True)
        return
    drawing_root.insert(
        0,
        create_header_logo_anchor(
            rel_id,
            dimensions,
            region_extent,
            row_height_emu,
        ),
    )
    parts[drawing_name] = ET.tostring(drawing_root, encoding="utf-8", xml_declaration=True)


def replace_header_logo(parts: dict[str, bytes], logo_data_url: str) -> None:
    if not logo_data_url:
        remove_header_logo(parts)
        return
    match = re.match(r"data:(image/(?:jpeg|jpg|png));base64,(.+)", logo_data_url, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        remove_header_logo(parts)
        return

    mime_type = match.group(1).lower().replace("image/jpg", "image/jpeg")
    try:
        logo_bytes = base64.b64decode(match.group(2), validate=True)
    except ValueError:
        remove_header_logo(parts)
        return
    if not logo_bytes:
        remove_header_logo(parts)
        return
    dimensions = image_dimensions(logo_bytes, mime_type)

    extension = "png" if mime_type == "image/png" else "jpeg"
    media_name = f"xl/media/header_logo.{extension}"
    parts[media_name] = logo_bytes

    rels_name = "xl/drawings/_rels/drawing1.xml.rels"
    logo_rel_id = ""
    rels_root = ET.fromstring(parts[rels_name]) if rels_name in parts else empty_relationships_root()
    header_anchor_rel_ids: set[str] = set()
    if "xl/drawings/drawing1.xml" in parts:
        drawing_root = ET.fromstring(parts["xl/drawings/drawing1.xml"])
        rel_targets = rel_targets_by_id(parts, rels_name)
        for anchor in drawing_root.findall(f"{NS_DRAWING}twoCellAnchor"):
            if is_header_logo_anchor(anchor, rel_targets):
                rel_id = picture_rel_id(anchor.find(f"{NS_DRAWING}pic"))
                if rel_id:
                    header_anchor_rel_ids.add(rel_id)

    for rel in rels_root.findall(f"{NS_PACKAGE_REL}Relationship"):
        rel_id = rel.attrib.get("Id", "")
        target = rel.attrib.get("Target", "")
        if rel.attrib.get("Type", "").endswith("/image") and (rel_id in header_anchor_rel_ids or is_header_logo_target(target)):
            old_target = drawing_target_part(target)
            rel.attrib["Target"] = f"../media/header_logo.{extension}"
            logo_rel_id = rel_id
            if old_target != media_name:
                parts.pop(old_target, None)
            break
    if not logo_rel_id:
        logo_rel_id = next_relationship_id(rels_root)
        ET.SubElement(
            rels_root,
            f"{NS_PACKAGE_REL}Relationship",
            {
                "Id": logo_rel_id,
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
                "Target": f"../media/header_logo.{extension}",
            },
        )
    parts[rels_name] = ET.tostring(rels_root, encoding="utf-8", xml_declaration=True)
    ensure_header_logo_anchor(parts, logo_rel_id, dimensions)

    content_types_name = "[Content_Types].xml"
    if content_types_name not in parts:
        return
    content_type = "image/png" if extension == "png" else "image/jpeg"
    content_root = ET.fromstring(parts[content_types_name])
    has_default = any(
        child.tag == f"{NS_CONTENT_TYPES}Default" and child.attrib.get("Extension") == extension
        for child in content_root
    )
    if not has_default:
        ET.SubElement(
            content_root,
            f"{NS_CONTENT_TYPES}Default",
            {"Extension": extension, "ContentType": content_type},
        )
        parts[content_types_name] = ET.tostring(content_root, encoding="utf-8", xml_declaration=True)


def update_print_titles(xml: bytes) -> bytes:
    """The quotation draws its page headers explicitly, without repeat rows."""
    return re.sub(
        r'<definedName\b(?=[^>]*\bname="_xlnm\.Print_Titles")'
        r'(?=[^>]*\blocalSheetId="0")[^>]*>[^<]*</definedName>',
        "",
        xml.decode("utf-8"),
    ).encode("utf-8")


def add_continuation_header_logos(parts: dict[str, bytes], last_row: int) -> None:
    """Reuse only the profile logo on each explicitly paginated later page."""
    drawing_name = "xl/drawings/drawing1.xml"
    sheet_name = "xl/worksheets/sheet1.xml"
    if drawing_name not in parts or sheet_name not in parts:
        return
    drawing = ET.fromstring(parts[drawing_name])
    targets = rel_targets_by_id(parts)
    logo = find_header_logo_anchor(drawing, targets)
    if logo is None:
        return
    # A regenerated template must not carry stale continuation-logo copies.
    for anchor in list(drawing):
        if anchor is not logo and is_header_logo_anchor(anchor, targets):
            drawing.remove(anchor)
    sheet = ET.fromstring(parts[sheet_name])
    page_starts = [
        int(brk.attrib["id"]) + 1
        for brk in sheet.findall(f"{NS_MAIN}rowBreaks/{NS_MAIN}brk")
        if int(brk.attrib["id"]) < last_row
    ]
    sheet_format = sheet.find(f"{NS_MAIN}sheetFormatPr")
    default_height = float(sheet_format.attrib.get("defaultRowHeight", QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT))
    heights = {
        int(row.attrib["r"]): 0 if row.attrib.get("hidden") == "1" else
        round(float(row.attrib.get("ht", default_height)) * EMU_PER_POINT)
        for row in sheet.findall(f"{NS_MAIN}sheetData/{NS_MAIN}row")
    }
    default_height_emu = round(default_height * EMU_PER_POINT)
    row_offsets = [0]
    for row_number in range(1, last_row + 1):
        row_offsets.append(row_offsets[-1] + heights.get(row_number, default_height_emu))
    next_id = max(
        (int(node.attrib["id"]) for node in drawing.findall(f".//{NS_DRAWING}cNvPr")),
        default=0,
    ) + 1
    for page_number, page_start in enumerate(page_starts, start=2):
        anchor = copy.deepcopy(logo)
        identity = anchor.find(f"{NS_DRAWING}pic/{NS_DRAWING}nvPicPr/{NS_DRAWING}cNvPr")
        identity.attrib.update({"id": str(next_id), "name": f"Header Logo - Page {page_number}"})
        next_id += 1
        start = anchor.find(f"{NS_DRAWING}from")
        start_row = int(start.find(f"{NS_DRAWING}row").text) + page_start - 1
        start.find(f"{NS_DRAWING}row").text = str(start_row)
        start_offset = int(start.find(f"{NS_DRAWING}rowOff").text)
        transform = anchor.find(f"{NS_DRAWING}pic/{NS_DRAWING}spPr/{NS_A}xfrm")
        transform.find(f"{NS_A}off").attrib["y"] = str(row_offsets[start_row] + start_offset)
        remaining = int(transform.find(f"{NS_A}ext").attrib["cy"]) + start_offset
        end_row = start_row
        # Continuation rows need not have the first page's logo-row height.
        while remaining >= heights.get(end_row + 1, default_height_emu):
            remaining -= heights.get(end_row + 1, default_height_emu)
            end_row += 1
        end = anchor.find(f"{NS_DRAWING}to")
        end.find(f"{NS_DRAWING}row").text = str(end_row)
        end.find(f"{NS_DRAWING}rowOff").text = str(remaining)
        drawing.append(anchor)
    parts[drawing_name] = ET.tostring(drawing, encoding="utf-8", xml_declaration=True)


def update_print_area(xml: bytes, last_row: int, last_col: int = 9) -> bytes:
    text = xml.decode("utf-8")
    print_area = f"Quotation!$A$1:${excel_col(last_col - 1)}${last_row}"
    updated, count = re.subn(
        r"(<definedName[^>]*name=\"_xlnm\.Print_Area\"[^>]*>)[^<]*(</definedName>)",
        rf"\g<1>{print_area}\2",
        text,
        count=1,
    )
    if count:
        return updated.encode("utf-8")

    insert = (
        f'<definedName name="_xlnm.Print_Area" localSheetId="0">'
        f"{print_area}</definedName>"
    )
    if "</definedNames>" in text:
        return text.replace("</definedNames>", f"{insert}</definedNames>", 1).encode("utf-8")
    return text.replace("</workbook>", f"<definedNames>{insert}</definedNames></workbook>", 1).encode("utf-8")


def enable_workbook_recalculation(xml: bytes) -> bytes:
    original_xml = xml.decode("utf-8")
    original_declarations = {
        f"xmlns:{prefix}": uri
        for prefix, uri in re.findall(r"\sxmlns:([A-Za-z0-9_]+)=\"([^\"]+)\"", original_xml)
    }
    root = ET.fromstring(xml)
    calc_pr = root.find(f"{NS_MAIN}calcPr")
    if calc_pr is None:
        calc_pr = ET.SubElement(root, f"{NS_MAIN}calcPr")
    calc_pr.attrib["calcMode"] = "auto"
    calc_pr.attrib["fullCalcOnLoad"] = "1"
    calc_pr.attrib["forceFullCalc"] = "1"
    updated = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    return ensure_root_namespace_declarations(updated, "workbook", original_declarations).encode("utf-8")


def excel_date_serial(value: str) -> float | str:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return value
    epoch = dt.date(1899, 12, 30)
    return float((parsed - epoch).days)


def quote_date_display_text(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%d %B %Y")


def brief_quote_date_rich_text_runs(brief: dict[str, Any]) -> list[RichTextRun]:
    rich_text = brief.get("rich_text") if isinstance(brief.get("rich_text"), dict) else {}
    parsed = parse_rich_text_html(rich_text.get("quoteDate")) if isinstance(rich_text, dict) else []
    source_runs = [run for line in parsed for run in line if clean_text(run.text)]
    if not source_runs:
        return []
    bold = any(run.bold for run in source_runs)
    italic = any(run.italic for run in source_runs)
    underline = any(run.underline for run in source_runs)
    if not (bold or italic or underline):
        return []
    text = quote_date_display_text(str(brief.get("quote_date", "")))
    return [RichTextRun(text, bold=bold, italic=italic, underline=underline)]


def wrapped_description(description: str, width: int = 58) -> list[str]:
    return textwrap.wrap(description, width=width) or [description]


def customer_quote_description(description: str) -> str:
    text = clean_text(description)
    match = re.match(r"^\[\s*([\s\S]*?)\s*\](?:\s*-\s*([\s\S]+))?$", text)
    if not match:
        return text
    reference = clean_text(match.group(1))
    detail = clean_text(match.group(2) or "")
    if reference and detail:
        return f"{reference} - {detail}"
    return reference or text


def line_quote_exchange_rate(line: QuoteLine, exchange_rate: float) -> float:
    authority = line.pricing_authority
    return 1.0 if isinstance(authority, dict) and authority.get("variant") == "manual" and authority.get("price_currency") else exchange_rate


# Customer amount text for a line priced as Included. It is text, not a number, so
# SUM totals and cached formula values count it as zero.
INCLUDED_AMOUNT_TEXT = "Included"


def amount_value(line: QuoteLine, exchange_rate: float = 1.0) -> str | float | None:
    exchange_rate = line_quote_exchange_rate(line, exchange_rate)
    if line.price_mode == "Included":
        return 0.0
    if line.display_price:
        display_amount = quote_amount(line.display_price, exchange_rate)
        return display_amount if display_amount is not None else line.display_price
    return quote_amount(line.amount, exchange_rate)


def line_amount_value(line: QuoteLine, exchange_rate: float = 1.0) -> float | None:
    exchange_rate = line_quote_exchange_rate(line, exchange_rate)
    if line.price_mode == "Included":
        return 0.0
    if line.amount is not None:
        return quote_amount(line.amount, exchange_rate)
    if line.display_price:
        return quote_amount(line.display_price, exchange_rate)
    return None


def formula_cache_amount(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return as_float(value, 0.0)


def continuation_page_start_for_row(row_number: int) -> int | None:
    return LEGACY_PAGE.page_start(row_number)


def manual_page_end_for_row(row_number: int) -> int:
    return LEGACY_PAGE.page_end(row_number)


def next_continuation_page_start(row_number: int) -> int:
    return LEGACY_PAGE.next_page_start(row_number)


def write_continuation_quote_header(
    root: ET.Element,
    page_start: int,
    project_title: str,
    currency: str,
    styles: dict[str, str],
    written_pages: set[int] | None = None,
) -> int:
    if written_pages is not None:
        if page_start in written_pages:
            return page_start + CONTINUATION_BODY_OFFSET
        written_pages.add(page_start)
    write_table_header(root, page_start + CONTINUATION_TABLE_HEADER_OFFSET, page_start + CONTINUATION_CURRENCY_OFFSET, styles)
    set_ooxml_cell(root, page_start + CONTINUATION_CURRENCY_OFFSET, 5, currency, styles.get("header_currency", "95"))
    return page_start + CONTINUATION_BODY_OFFSET


def quote_entry_height(entry: dict[str, Any]) -> int:
    if entry["kind"] == "section":
        return 1
    return max(1, len(entry["description_lines"]), len(entry.get("quantity_lines") or []))


def quote_entry_keep_height(entries: list[dict[str, Any]], index: int) -> int:
    entry = entries[index]
    height = quote_entry_height(entry)
    if entry.get("kind") == "section" and index + 1 < len(entries):
        next_entry = entries[index + 1]
        if next_entry.get("kind") != "section":
            height = 2 + quote_entry_height(next_entry)
    return height


def ensure_quote_entry_page(
    root: ET.Element,
    row_number: int,
    entry_height: int,
    project_title: str,
    currency: str,
    styles: dict[str, str],
    continuation_pages: set[int],
) -> int:
    page_start = continuation_page_start_for_row(row_number)
    if page_start is not None and row_number < page_start + CONTINUATION_BODY_OFFSET:
        row_number = write_continuation_quote_header(
            root,
            page_start,
            project_title,
            currency,
            styles,
            continuation_pages,
        )
    if row_number + entry_height - 1 <= manual_page_end_for_row(row_number):
        return row_number
    return write_continuation_quote_header(
        root,
        next_continuation_page_start(row_number),
        project_title,
        currency,
        styles,
        continuation_pages,
    )


def clear_continuation_header_band(row_number: int) -> int:
    """Advance continuation-page content past the reserved header rows."""
    return LEGACY_PAGE.clear_header_band(row_number)


def summary_block_start_row(row_number: int, block_height: int) -> int:
    return LEGACY_PAGE.summary_start(row_number, block_height)


def layout_chunk_start_row(row_number: int, chunk: LayoutChunk) -> tuple[int, bool]:
    candidate_row = row_number
    row_number = clear_continuation_header_band(row_number)
    if chunk.height <= 0 or row_number + chunk.height - 1 <= manual_page_end_for_row(row_number):
        return row_number, row_number != candidate_row
    return next_continuation_page_start(row_number) + CONTINUATION_BODY_OFFSET, True


def optional_text_chunk_start_row(row_number: int, chunk: LayoutChunk) -> tuple[int, bool]:
    return LEGACY_PAGE.optional_text_chunk_start(row_number, chunk)


def optional_text_character_width(character: str) -> int:
    if character == "\t":
        return 4
    if unicodedata.combining(character) or unicodedata.category(character) in {"Mn", "Me"}:
        return 0
    if unicodedata.east_asian_width(character) in {"F", "W"}:
        return 2
    return 1


def rich_text_character_width(
    character: str, formatting: tuple[bool, bool, bool], font_size: float | None,
) -> float:
    """Keep the existing body budget; use conservative point advances for narrow blocks."""
    width = optional_text_character_width(character)
    if font_size is None or width == 0:
        return width
    if width == 2 or ord(character) > 127:
        advance = 1.2
    elif character in "MWmw@%&":
        advance = 1.0
    elif character.isspace():
        advance = 0.4 * width
    elif character in "ilI.,:;'!|":
        advance = 0.45
    elif character.isupper():
        advance = 0.9
    else:
        advance = 0.8
    return advance * font_size * (1.12 if formatting[0] else 1.0) * (1.05 if formatting[1] else 1.0)


def quantity_character_width(
    character: str, formatting: tuple[bool, bool, bool], font_size: float | None,
) -> float:
    """Budget numeric cells in points without shrinking their selected font."""
    width = optional_text_character_width(character)
    if width == 0:
        return 0.0
    if width == 2 or ord(character) > 127:
        advance = 1.2
    elif character in "MWmw@%&":
        advance = 1.0
    elif character.isspace():
        advance = 0.35 * width
    elif character in "ilI.,:;'!|":
        advance = 0.4
    elif character.isdigit():
        advance = quote_template.QUANTITY_DIGIT_EM
    elif character.isupper():
        advance = 0.9
    else:
        advance = 0.6
    return advance * (font_size or 1.0)


def rich_text_line_runs(characters: list[tuple[str, tuple[bool, bool, bool]]]) -> list[RichTextRun]:
    runs: list[RichTextRun] = []
    for character, formatting in characters:
        if runs and (runs[-1].bold, runs[-1].italic, runs[-1].underline) == formatting:
            runs[-1].text += character
        else:
            runs.append(RichTextRun(character, bold=formatting[0], italic=formatting[1], underline=formatting[2]))
    return runs


def combining_text_clusters(
    characters: list[tuple[str, tuple[bool, bool, bool]]],
) -> list[list[tuple[str, tuple[bool, bool, bool]]]]:
    """Keep a base and its following Unicode marks together, including split rich runs."""
    clusters: list[list[tuple[str, tuple[bool, bool, bool]]]] = []
    for item in characters:
        if clusters and unicodedata.category(item[0]).startswith("M"):
            clusters[-1].append(item)
        else:
            clusters.append([item])
    return clusters


def wrap_optional_text_segment(
    characters: list[tuple[str, tuple[bool, bool, bool]]],
    *,
    max_width: float = OPTIONAL_TEXT_LINE_MAX_WIDTH_EM,
    font_size: float | None = None,
    measure_character: Callable[[str, tuple[bool, bool, bool], float | None], float] | None = None,
) -> list[list[RichTextRun]]:
    measure = measure_character or rich_text_character_width
    lines: list[list[RichTextRun]] = []
    current: list[tuple[str, tuple[bool, bool, bool]]] = []
    current_width = 0
    last_break_after: int | None = None

    def refresh_line_metrics() -> tuple[float, int | None]:
        width = 0
        break_after = None
        length = 0
        for cluster in combining_text_clusters(current):
            width += sum(measure(c, f, font_size) for c, f in cluster)
            length += len(cluster)
            if cluster[0][0].isspace():
                break_after = length
        return width, break_after

    for cluster in combining_text_clusters(characters):
        cluster_width = sum(measure(c, f, font_size) for c, f in cluster)
        if cluster_width > max_width:
            raise ValueError("A quotation text glyph exceeds the available column width.")
        while current and current_width + cluster_width > max_width:
            cut = last_break_after if last_break_after is not None else len(current)
            if cut <= 0:
                cut = len(current)
            lines.append(rich_text_line_runs(current[:cut]))
            current = current[cut:]
            current_width, last_break_after = refresh_line_metrics()
        current.extend(cluster)
        current_width += cluster_width
        if cluster[0][0].isspace():
            last_break_after = len(current)

    if current or not lines:
        lines.append(rich_text_line_runs(current))
    return lines


def wrap_rich_text_runs(
    runs: list[Any], *, max_width: float = OPTIONAL_TEXT_LINE_MAX_WIDTH_EM,
    font_size: float | None = None,
    measure_character: Callable[[str, tuple[bool, bool, bool], float | None], float] | None = None,
) -> list[list[RichTextRun]]:
    """Reflow rich text into printable-width rows without losing run emphasis."""
    if not math.isfinite(max_width) or max_width <= 0:
        raise ValueError("Invalid rich-text width.")
    if font_size is not None and (not math.isfinite(font_size) or font_size <= 0):
        raise ValueError("Invalid rich-text font size.")
    segments: list[list[tuple[str, tuple[bool, bool, bool]]]] = [[]]
    for value in runs:
        run = normalize_ooxml_text_run(value)
        formatting = (run.bold, run.italic, run.underline)
        for character in run.text.replace("\r\n", "\n").replace("\r", "\n"):
            if character == "\n":
                segments.append([])
            else:
                segments[-1].append((character, formatting))
    return [line for segment in segments for line in wrap_optional_text_segment(
        segment, max_width=max_width, font_size=font_size, measure_character=measure_character,
    )]


def visible_column_width_points(root: ET.Element, first: int, last: int) -> float:
    """Measure the visible worksheet columns, excluding hidden columns and cell padding."""
    sheet_format = root.find(f"{NS_MAIN}sheetFormatPr")
    default_width = float(sheet_format.get("defaultColWidth", "8.43")) if sheet_format is not None else 8.43
    cols = root.find(f"{NS_MAIN}cols")
    width = 0.0
    for number in range(first, last + 1):
        attributes = {"width": str(default_width)}
        if cols is not None:
            for col in cols:
                if int(col.get("min", "0")) <= number <= int(col.get("max", "0")):
                    attributes.update(col.attrib)
        if attributes.get("hidden", "").lower() in {"1", "true"}:
            continue
        column_width = float(attributes["width"])
        if not math.isfinite(column_width) or column_width < 0:
            raise ValueError("Invalid quotation layout column width.")
        # Excel width units use the default digit advance. Dropping padding is conservative.
        width += math.floor(column_width * 7) * 72 / 96
    if width <= 6:
        raise ValueError("Quotation text block has no usable visible width.")
    return width - 6


QUOTE_ENTRY_TOO_TALL_MESSAGE = "A quotation line item exceeds the available page height."


def prepare_quantity_lines(
    entries: list[dict[str, Any]], root: ET.Element, parts: dict[str, bytes] | None,
    *, columns: tuple[int, int] | None = (2, 2), font_size: float | None = None,
    page: PageModel = LEGACY_PAGE, width: float | None = None,
) -> None:
    """Give quantity and description one shared row budget before page admission.

    `columns` is the quantity column range; None means the layout shows no quantity. `width` is the
    quantity text width in points when the caller measures it (quote templates); otherwise it is read
    from the worksheet's columns.
    """
    if columns is not None:
        if width is None:
            width = visible_column_width_points(root, *columns)
        if font_size is None:
            selected_font = ooxml_style_text_font(parts, "13")
            if not selected_font["font_size"]:
                raise ValueError("Quotation quantity font size is unavailable.")
            font_size = float(selected_font["font_size"])
    for entry in entries:
        if entry["kind"] != "item":
            continue
        if columns is None:
            entry["quantity_lines"] = []
            continue
        text = entry["quantity"]
        number = text.split(maxsplit=1)[0] if text else ""
        if sum(quantity_character_width(c, (False, False, False), font_size) for c in number) > width:
            raise ValueError("A quotation quantity number exceeds the available column width.")
        entry["quantity_lines"] = [
            "".join(run.text for run in runs).strip()
            for runs in wrap_rich_text_runs(
                [RichTextRun(text)], max_width=width, font_size=font_size,
                measure_character=quantity_character_width,
            )
        ]
    capacity = page.page_height - page.body_offset
    if any(quote_entry_keep_height(entries, index) > capacity for index in range(len(entries))):
        raise ValueError(QUOTE_ENTRY_TOO_TALL_MESSAGE)


def acceptance_text_lines(
    brief: dict[str, Any], key: str, fallback: Any, width: float, font_size: float,
) -> list[list[RichTextRun]]:
    lines = brief_rich_text_lines(brief, key, [str(fallback or "")])
    return [
        wrapped
        for runs in lines
        for wrapped in wrap_rich_text_runs(runs, max_width=width, font_size=font_size)
    ] or [[]]


@dataclass(frozen=True)
class SigningColumns:
    """One side of the signing band: its column range, fonts, cell styles and line length."""

    first: int
    last: int
    text_style: str
    label_style: str
    text_font: dict[str, str | None]
    label_font: dict[str, str | None]
    line_chars: int


def write_acceptance_signature(
    root: ET.Element, row_number: int, brief: dict[str, Any],
    acceptance: dict[str, Any], signature: dict[str, Any], company_name: str,
    styles: dict[str, str], parts: dict[str, bytes],
) -> tuple[int, bool]:
    """Own both acceptance columns, their signature offsets and continuation admission."""
    text_font = ooxml_style_text_font(parts, styles["signature_text"])
    label_font = ooxml_style_text_font(parts, styles["signature_line"])
    sides = [
        SigningColumns(first, last, styles["signature_text"], styles["signature_line"], text_font, label_font, chars)
        for first, last, chars in ((2, 4, 29), (5, 9, 37))
    ]
    return write_signing_band(root, row_number, brief, acceptance, signature, company_name, sides[0], sides[1])


def write_signing_band(
    root: ET.Element, row_number: int, brief: dict[str, Any],
    acceptance: dict[str, Any], signature: dict[str, Any], company_name: str,
    company_side: SigningColumns | None, customer_side: SigningColumns | None,
    *, page: PageModel = LEGACY_PAGE, row_height: str = QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT,
) -> tuple[int, bool]:
    """Write the company signature (left) and customer acceptance (right) side by side.

    Either side may be absent; each pair of lines shares rows so the signing lines align.
    """
    sides = (company_side, customer_side)
    present = [side for side in sides if side is not None]
    if not present:
        return row_number, False
    sizes = []
    for side in sides:
        if side is None:
            sizes.append((11.0, 11.0))
            continue
        side_sizes = (float(side.text_font["font_size"] or 11), float(side.label_font["font_size"] or 11))
        if not all(math.isfinite(size) and size > 0 for size in side_sizes):
            raise ValueError("Invalid quotation acceptance font size.")
        sizes.append(side_sizes)
    widths = [visible_column_width_points(root, side.first, side.last) if side else 0.0 for side in sides]

    def pair(left_key: str, left: Any, right_key: str, right: Any, label: bool):
        left_lines = acceptance_text_lines(brief, left_key, left, widths[0], sizes[0][label]) if company_side else []
        right_lines = acceptance_text_lines(brief, right_key, right, widths[1], sizes[1][label]) if customer_side else []
        return [
            (left_lines[i] if i < len(left_lines) else [],
             right_lines[i] if i < len(right_lines) else [])
            for i in range(max(len(left_lines), len(right_lines)))
        ]

    def signing_line(index: int) -> list[RichTextRun]:
        side = sides[index]
        if side is None:
            return []
        return [RichTextRun("_" * max(1, min(side.line_chars, int(widths[index] / (sizes[index][1] * 0.6)))))]

    text_rows = pair("quoteCompanyName", acceptance.get("company_name") or company_name,
                     "acceptanceText", acceptance.get("text"), False)
    # The three blank rows preserve the existing short-text signature placement.
    gap_rows = [([], []) for _ in range(3)]
    line_rows = [(signing_line(0), signing_line(1))]
    groups = [
        (text_rows, False),
        (gap_rows, False),
        (line_rows, True),
        (pair("companySignatory", signature.get("company_signatory"),
              "personLabel", acceptance.get("person_label"), True), True),
        (pair("companyTitle", signature.get("company_title"),
              "stampLabel", acceptance.get("stamp_label"), True), True),
        (pair("companyDateLabel", signature.get("company_date_label"),
              "dateLabel", acceptance.get("date_label"), True), True),
    ]
    groups = [
        (rows, label, 1 if index == 1 else max(
            1, math.ceil(max(
                float((side.label_font if label else side.text_font)["font_size"] or 11) for side in present
            ) * 1.4 / float(row_height)),
        ))
        for index, (rows, label) in enumerate(groups)
    ]
    total_height = max(SIGNATURE_BLOCK_HEIGHT, sum(len(rows) * span for rows, _, span in groups))
    capacity = page.optional_text_capacity()
    if any(span > capacity for _, _, span in groups):
        raise ValueError("Quotation acceptance font is too large for a printable page.")
    row_number, moved = page.optional_text_chunk_start(
        row_number, LayoutChunk("acceptance_signature", total_height),
    )
    oversized = total_height > capacity
    first_col = min(side.first for side in present)
    last_col = max(side.last for side in present)
    last_row = row_number - 1
    for group_index, (rows, label, span) in enumerate(groups):
        if oversized:
            remaining = sum(len(group[0]) * group[2] for group in groups[group_index:])
            keep = remaining if group_index > 0 and remaining <= capacity else len(rows) * span
            row_number, group_moved = page.optional_text_chunk_start(row_number, LayoutChunk("acceptance_part", keep))
            moved = moved or group_moved
        for line_pair in rows:
            if oversized:
                row_number, line_moved = page.optional_text_chunk_start(row_number, LayoutChunk("acceptance_line", span))
                moved = moved or line_moved
            # Replace stale template merges only in the newly owned acceptance row.
            merges = root.find(f"{NS_MAIN}mergeCells")
            if merges is not None:
                for merge in list(merges):
                    ends = merge.get("ref", "").split(":")
                    start_row, start_col = parse_cell_ref(ends[0])
                    end_row, end_col = parse_cell_ref(ends[-1])
                    if start_row <= row_number + span - 1 and end_row >= row_number and start_col <= last_col and end_col >= first_col:
                        merges.remove(merge)
                merges.set("count", str(len(merges)))
            for side, runs in zip(sides, line_pair):
                if side is None:
                    continue
                font = side.label_font if label else side.text_font
                set_ooxml_rich_text_cell(root, row_number, side.first, runs, side.label_style if label else side.text_style, **font)
                ensure_merge_ref(root, f"{cell_ref(row_number, side.first)}:{cell_ref(row_number + span - 1, side.last)}")
            # Count larger fonts as physical row slots, rather than growing an
            # uncounted row beyond the manual page's capacity.
            sheet_data = root.find(f"{NS_MAIN}sheetData")
            for slot in range(row_number, row_number + span):
                row = get_or_create_row(sheet_data, slot)
                row.set("ht", row_height)
                row.set("customHeight", "1")
                row.attrib.pop("hidden", None)
            last_row = row_number + span - 1
            row_number += span
    return last_row + 1, moved


def write_optional_text_block(
    root: ET.Element,
    row_number: int,
    runs: list[Any],
    *,
    name: str,
    text_column: int,
    style: str,
    number: int | None = None,
    number_style: str | None = None,
    admission_height: int | None = None,
    font_name: str | None = None,
    font_size: str | None = None,
    color: str | None = None,
    number_column: int = 1,
    last_column: int = 9,
    max_width: float = OPTIONAL_TEXT_LINE_MAX_WIDTH_EM,
    page: PageModel = LEGACY_PAGE,
) -> tuple[int, int, bool]:
    lines = wrap_rich_text_runs(runs, max_width=max_width)
    if not lines:
        return row_number, row_number - 1, False

    block_row, moved = page.optional_text_chunk_start(
        row_number,
        LayoutChunk(name, admission_height if admission_height is not None else len(lines)),
    )
    row_number = block_row
    last_row = block_row - 1
    for line_index, line_runs in enumerate(lines):
        row_number, line_moved = page.optional_text_chunk_start(
            row_number,
            LayoutChunk(f"{name}_line", 1),
        )
        moved = moved or line_moved
        if number is not None and line_index == 0:
            set_ooxml_cell(root, row_number, number_column, f"{number:.2f}", number_style)
        set_ooxml_rich_text_cell(
            root, row_number, text_column, line_runs, style,
            font_name=font_name, font_size=font_size, color=color,
        )
        ensure_merge_ref(root, f"{cell_ref(row_number, text_column)}:{cell_ref(row_number, last_column)}")
        last_row = row_number
        row_number += 1
    return row_number, last_row, moved


def write_numbered_text_section(
    root: ET.Element,
    row_number: int,
    heading_runs: list[RichTextRun],
    item_runs: list[list[RichTextRun]],
    *,
    name: str,
    has_heading: bool,
    heading_style: str,
    body_style: str,
    number_style: str,
    heading_font: dict[str, str | None],
    body_font: dict[str, str | None],
    number_column: int = 1,
    last_column: int = 9,
    max_width: float = OPTIONAL_TEXT_LINE_MAX_WIDTH_EM,
    page: PageModel = LEGACY_PAGE,
) -> tuple[int, int, bool]:
    """Write a heading and numbered items (terms or notes); the heading stays with the first item."""
    moved = False
    last_row = 0
    heading_admission_height = len(wrap_rich_text_runs(heading_runs, max_width=max_width))
    if has_heading and item_runs:
        first_section_height = heading_admission_height + len(wrap_rich_text_runs(item_runs[0], max_width=max_width))
        if first_section_height <= page.page_height - page.body_offset:
            heading_admission_height = first_section_height
    if has_heading:
        row_number, last_row, moved = write_optional_text_block(
            root, row_number, heading_runs, name=f"{name}_heading", text_column=number_column,
            style=heading_style, admission_height=heading_admission_height,
            last_column=last_column, max_width=max_width, page=page, **heading_font,
        )
    for index, runs in enumerate(item_runs, start=1):
        row_number, last_row, item_moved = write_optional_text_block(
            root, row_number, runs, name=name, text_column=number_column + 1, style=body_style,
            number=index, number_style=number_style, number_column=number_column,
            last_column=last_column, max_width=max_width, page=page, **body_font,
        )
        moved = moved or item_moved
    return row_number, last_row, moved


def manual_page_break_ids(last_row: int, page: PageModel = LEGACY_PAGE) -> list[int]:
    return page.break_ids(last_row)


def ooxml_cell_has_payload(cell: ET.Element) -> bool:
    if cell.find(f"{NS_MAIN}f") is not None:
        return True
    value = cell.find(f"{NS_MAIN}v")
    if value is not None and (value.text or "").strip():
        return True
    inline = cell.find(f"{NS_MAIN}is")
    if inline is None:
        return False
    return any((text.text or "").strip() for text in inline.iter(f"{NS_MAIN}t"))


def last_payload_row(root: ET.Element, max_row: int) -> int:
    result = 0
    for cell in root.iter(f"{NS_MAIN}c"):
        if not ooxml_cell_has_payload(cell):
            continue
        row_number, _ = parse_cell_ref(cell.attrib.get("r", "A1"))
        if row_number <= max_row:
            result = max(result, row_number)
    return result


def hide_empty_continuation_guard_rows(root: ET.Element, last_row: int, page: PageModel = LEGACY_PAGE) -> None:
    """Keep reserved blank padding from forcing an automatic print page."""
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        return
    payload_cells = [
        parse_cell_ref(cell.attrib["r"])
        for cell in sheet_data.iter(f"{NS_MAIN}c")
        if ooxml_cell_has_payload(cell)
    ]
    payload_rows = {row for row, _ in payload_cells}
    occupied_merges = []
    for merge in root.findall(f"{NS_MAIN}mergeCells/{NS_MAIN}mergeCell"):
        start, end = merge.attrib["ref"].split(":")
        first_row, first_col = parse_cell_ref(start)
        final_row, final_col = parse_cell_ref(end)
        if final_row > first_row and any(
            first_row <= row <= final_row and first_col <= col <= final_col
            for row, col in payload_cells
        ):
            occupied_merges.append((first_row, final_row))
    rows = {int(row.attrib["r"]): row for row in sheet_data}
    for page_end in page.break_ids(last_row)[1:]:
        for row_number in range(
            page_end, page_end - page.guard_rows, -1
        ):
            if row_number in payload_rows or any(
                start <= row_number <= end for start, end in occupied_merges
            ):
                break
            if row_number in rows:
                rows[row_number].attrib["hidden"] = "1"


def printable_last_row(
    root: ET.Element, last_row: int, manual_pagination_enabled: bool, page: PageModel = LEGACY_PAGE,
) -> int:
    if not manual_pagination_enabled:
        return last_row
    payload_row = last_payload_row(root, last_row)
    if not payload_row:
        return last_row
    break_ids = page.break_ids(last_row)
    if break_ids and break_ids[-1] >= payload_row:
        return payload_row
    return last_row


def set_manual_page_breaks(root: ET.Element, last_row: int, enabled: bool, page: PageModel = LEGACY_PAGE) -> None:
    row_breaks = root.find(f"{NS_MAIN}rowBreaks")
    if row_breaks is not None:
        root.remove(row_breaks)
    if not enabled:
        return

    break_ids = page.break_ids(last_row)
    if not break_ids:
        return
    row_breaks = ET.Element(
        f"{NS_MAIN}rowBreaks",
        {"count": str(len(break_ids)), "manualBreakCount": str(len(break_ids))},
    )
    for break_id in break_ids:
        ET.SubElement(
            row_breaks,
            f"{NS_MAIN}brk",
            {"id": str(break_id), "max": "16383", "man": "1"},
        )

    insert_at = len(root)
    for index, child in enumerate(list(root)):
        if child.tag in {
            f"{NS_MAIN}drawing",
            f"{NS_MAIN}legacyDrawing",
            f"{NS_MAIN}legacyDrawingHF",
            f"{NS_MAIN}picture",
            f"{NS_MAIN}oleObjects",
            f"{NS_MAIN}controls",
            f"{NS_MAIN}webPublishItems",
            f"{NS_MAIN}tableParts",
            f"{NS_MAIN}extLst",
        }:
            insert_at = index
            break
    root.insert(insert_at, row_breaks)


def next_quote_row(row_number: int) -> int:
    return row_number


def grouped_section_names(brief: dict[str, Any], lines: list[QuoteLine]) -> set[str]:
    configured = brief.get("section_pricing") or {}
    return {
        clean_text(section)
        for section, mode in configured.items()
        if clean_text(mode).lower() in {"section_total", "section-total", "lump_sum", "lump-sum"}
    }


def render_quote_entries(
    lines: list[QuoteLine], brief: dict[str, Any] | None = None, *, description_width: int = 58,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    grouped_sections = grouped_section_names(brief or {}, lines)
    exchange_rate = quote_exchange_rate(brief or {})
    current_section = None
    section_number = 0
    detail_number = 0
    active_section_entry: dict[str, Any] | None = None
    for line in lines:
        if line.section != current_section:
            current_section = line.section
            section_number += 1
            detail_number = 0
            section_grouped = clean_text(current_section) in grouped_sections
            active_section_entry = {
                "kind": "section",
                "number": f"{section_number}.0",
                "section": current_section,
                "amount": 0.0 if section_grouped else None,
                "remark": "",
                "section_grouped": section_grouped,
            }
            entries.append(active_section_entry)
        detail_number += 1
        detail_amount = INCLUDED_AMOUNT_TEXT if line.price_mode == "Included" else amount_value(line, exchange_rate)
        if active_section_entry and active_section_entry.get("section_grouped"):
            active_section_entry["amount"] = round_commercial_cents(
                float(active_section_entry["amount"] or 0.0) + float(line_amount_value(line, exchange_rate) or 0.0)
            ) or 0.0
            if line.match_status != "deferred-pricing":
                detail_amount = None
        unit_price = line.unit_price_override
        if unit_price is None and line.matched_price is not None:
            unit_price = line.matched_price.sale_unit_price
        shows_unit_price = (
            unit_price is not None
            and line.price_mode != "Included"
            and isinstance(detail_amount, (int, float))
            and not isinstance(detail_amount, bool)
        )
        entries.append({
            "kind": "item",
            "number": f"{section_number}.{detail_number}",
            "quantity": quantity_text(line),
            "quantity_number": quantity_number_text(line),
            "unit": clean_text(line.unit) if line.quantity is not None else "",
            "unit_price": quote_amount(unit_price, line_quote_exchange_rate(line, exchange_rate)) if shows_unit_price else None,
            "description_lines": wrapped_description(customer_quote_description(line.description), description_width),
            "amount": detail_amount,
            "deferred_pricing": line.match_status == "deferred-pricing",
        })
    for index, entry in enumerate(entries):
        if entry["kind"] != "section" or not entry.get("section_grouped"):
            continue
        covered_numbers = []
        for follower in entries[index + 1:]:
            if follower["kind"] == "section":
                break
            if not follower["deferred_pricing"]:
                covered_numbers.append(follower["number"])
        if covered_numbers:
            first, last = covered_numbers[0], covered_numbers[-1]
            if len(covered_numbers) != int(last.split(".")[-1]) - int(first.split(".")[-1]) + 1:
                entry["coverage"] = "Covers resolved line items only"
            else:
                entry["coverage"] = f"Covers line items {first} to {last}" if first != last else f"Covers line item {first}"
        else:
            entry["amount"] = None
    return entries


def write_table_header(root: ET.Element, row_number: int, currency_row: int | None = None, styles: dict[str, str] | None = None) -> None:
    styles = styles or {}
    set_ooxml_cell(root, row_number, 1, "Pos.", styles.get("header_pos", "23"))
    set_ooxml_cell(root, row_number, 2, "Quantity", styles.get("header_quantity", "24"))
    set_ooxml_cell(root, row_number, 3, "Service", styles.get("header_service", "21"))
    set_ooxml_cell(root, row_number, 5, "Estimate", styles.get("header_estimate", "87"))
    if currency_row is not None:
        set_ooxml_cell(root, currency_row, 5, "SGD", styles.get("header_currency", "95"))


def ensure_root_namespace_declarations(xml: str, root_name: str, required_declarations: dict[str, str]) -> str:
    root_start_index = xml.find(f"<{root_name}")
    root_tag_end = xml.find(">", root_start_index)
    if root_start_index == -1 or root_tag_end == -1:
        return xml

    insertions = []
    root_start = xml[root_start_index:root_tag_end]
    for name, uri in required_declarations.items():
        if f"{name}=" not in root_start:
            insertions.append(f' {name}="{uri}"')
    if not insertions:
        return xml
    return xml[:root_tag_end] + "".join(insertions) + xml[root_tag_end:]


def serialize_excel_styles(root: ET.Element) -> bytes:
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    required_declarations = {
        "xmlns:mc": XMLNS_MC,
        "xmlns:x14ac": XMLNS_X14AC,
        "xmlns:x16r2": XMLNS_X16R2,
        "xmlns:xr": XMLNS_XR,
    }
    return ensure_root_namespace_declarations(xml, "styleSheet", required_declarations).encode("utf-8")


def normalize_worksheet_child_order(root: ET.Element) -> None:
    child_order = {name: index for index, name in enumerate(WORKSHEET_CHILD_ORDER)}

    def order_key(indexed_child: tuple[int, ET.Element]) -> tuple[int, int]:
        index, child = indexed_child
        local_name = child.tag.rsplit("}", 1)[-1]
        return child_order.get(local_name, len(child_order)), index

    root[:] = [
        child
        for _, child in sorted(enumerate(list(root)), key=order_key)
    ]


def serialize_excel_worksheet(root: ET.Element) -> bytes:
    normalize_worksheet_child_order(root)
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
    required_declarations = {
        "xmlns:mc": XMLNS_MC,
        "xmlns:x14ac": XMLNS_X14AC,
        "xmlns:xr": XMLNS_XR,
        "xmlns:xr2": XMLNS_XR2,
        "xmlns:xr3": XMLNS_XR3,
    }
    return ensure_root_namespace_declarations(xml, "worksheet", required_declarations).encode("utf-8")


def trim_layout_worksheet(root: ET.Element, last_row: int, last_col: int = 9) -> None:
    dimension = root.find(f"{NS_MAIN}dimension")
    if dimension is not None:
        dimension.attrib["ref"] = f"A1:{excel_col(last_col - 1)}{last_row}"

    row_breaks = root.find(f"{NS_MAIN}rowBreaks")
    if row_breaks is not None:
        root.remove(row_breaks)

    sheet_view = root.find(f"{NS_MAIN}sheetViews/{NS_MAIN}sheetView")
    if sheet_view is not None:
        sheet_view.attrib.pop("view", None)
        sheet_view.attrib.pop("topLeftCell", None)
        selection = sheet_view.find(f"{NS_MAIN}selection")
        if selection is not None:
            selection.attrib["activeCell"] = "A1"
            selection.attrib["sqref"] = "A1"

    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        return
    for row in list(sheet_data.findall(f"{NS_MAIN}row")):
        row_number = int(row.attrib.get("r", "0"))
        if row_number > last_row:
            sheet_data.remove(row)
            continue
        for cell in list(row.findall(f"{NS_MAIN}c")):
            _, col_number = parse_cell_ref(cell.attrib.get("r", "A1"))
            if col_number > last_col:
                row.remove(cell)


def write_quote_layout_xlsx(layout_template: Path, path: Path, brief: dict[str, Any], lines: list[QuoteLine]) -> None:
    if not layout_template.exists():
        raise FileNotFoundError(f"Quotation layout template not found: {layout_template}")

    with zipfile.ZipFile(layout_template) as zf:
        parts = {name: zf.read(name) for name in zf.namelist()}

    layout_styles = add_quote_layout_styles(parts)
    root = ET.fromstring(parts["xl/worksheets/sheet1.xml"])
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    existing_row_numbers = {
        int(row.attrib["r"])
        for row in (sheet_data.findall(f"{NS_MAIN}row") if sheet_data is not None else [])
        if row.attrib.get("r", "").isdigit()
    }
    clear_ooxml_range(root, 1, 1000, 1, 100)
    price_style = layout_styles["price_amount"]

    client = brief["client"]
    project = brief["project"]
    currency = brief.get("currency", "SGD")
    company = brief.get("company") if isinstance(brief.get("company"), dict) else {}
    company_name = clean_text(company.get("name"))
    client_address_runs = brief_rich_text_lines(brief, "clientAddress", client.get("address") or [])
    header_line_runs = brief_rich_text_lines(brief, "headerDetails", company.get("header_lines") or [])
    set_ooxml_rich_text_cell(root, 6, 1, brief_rich_text_cell_runs(brief, "clientName", client.get("name", "")), layout_styles["client_name"])
    for offset, runs in enumerate(client_address_runs[:4], start=7):
        set_ooxml_rich_text_cell(root, offset, 1, runs, layout_styles["client_address"])
    set_ooxml_rich_text_cell(root, 11, 1, [RichTextRun("Attention:", bold=True)], layout_styles["client_attention"])
    set_ooxml_rich_text_cell(root, 12, 2, brief_rich_text_cell_runs(brief, "clientAttention", client.get("attention", ""), fallback_bold=True), layout_styles["client_attention"])
    set_ooxml_rich_text_cell(root, 13, 2, brief_rich_text_cell_runs(brief, "clientTitle", client.get("title", "")), layout_styles["client_title"])
    quote_date_runs = brief_quote_date_rich_text_runs(brief)
    if quote_date_runs:
        set_ooxml_rich_text_cell(root, 16, 1, quote_date_runs, layout_styles["quote_date"])
    else:
        set_ooxml_cell(root, 16, 1, excel_date_serial(str(brief.get("quote_date", ""))), layout_styles["quote_date"])
    set_ooxml_rich_text_cell(root, 18, 1, brief_rich_text_cell_runs(brief, "projectTitle", project.get("title", "")), "26")

    write_table_header(root, 20, 21, layout_styles)
    set_ooxml_cell(root, 21, 5, currency, layout_styles["header_currency"])

    entries = render_quote_entries(lines, brief)
    prepare_quantity_lines(entries, root, parts)
    continuation_pages: set[int] = set()
    row_number = 22
    for index, entry in enumerate(entries):
        row_number = ensure_quote_entry_page(
            root,
            next_quote_row(row_number),
            quote_entry_keep_height(entries, index),
            clean_text(project.get("title")),
            currency,
            layout_styles,
            continuation_pages,
        )

        if entry["kind"] == "section":
            set_ooxml_cell(root, row_number, 1, entry["number"], "28")
            set_ooxml_cell(root, row_number, 3, entry["section"], "18")
            next_row_number = row_number + 2
            if entry.get("amount") is not None:
                set_ooxml_cell(root, row_number, 5, entry["amount"], price_style)
            if entry.get("coverage"):
                coverage_row = next_quote_row(row_number + 1)
                set_ooxml_cell(root, coverage_row, 5, entry["coverage"], "5")
                next_row_number = max(next_row_number, coverage_row + 1)
            if entry.get("remark"):
                set_ooxml_cell(root, row_number, 6, entry["remark"], "94")
            row_number = next_row_number
            continue

        description_lines = entry["description_lines"]
        quantity_lines = entry["quantity_lines"]
        set_ooxml_cell(root, row_number, 1, entry["number"], "70")
        set_ooxml_cell(root, row_number, 5, entry["amount"], price_style)
        for offset in range(quote_entry_height(entry)):
            if offset < len(quantity_lines):
                set_ooxml_cell(root, row_number + offset, 2, quantity_lines[offset], "13")
            if offset < len(description_lines):
                set_ooxml_cell(root, row_number + offset, 3, description_lines[offset], "5")
        row_number += quote_entry_height(entry) + 1

    total_candidate_row = row_number + 1
    total_row = summary_block_start_row(total_candidate_row, TOTAL_BLOCK_HEIGHT)
    manual_pagination_enabled = bool(continuation_pages) or total_row != total_candidate_row
    gst_row = total_row + 1
    grand_row = total_row + 2
    tax_rate = quote_tax_rate(brief)
    cached_total = round_commercial_cents(sum(formula_cache_amount(entry.get("amount")) for entry in entries)) or 0.0
    cached_tax = round_commercial_cents(cached_total * tax_rate) or 0.0
    cached_grand = round_commercial_cents(cached_total + cached_tax) or 0.0
    set_ooxml_cell(root, total_row, 4, "Total", layout_styles["total_label"])
    set_ooxml_formula(
        root,
        total_row,
        5,
        f"SUM(E22:E{max(22, total_row - 1)})",
        layout_styles["total_amount"],
        cached_total,
    )
    set_ooxml_cell(root, total_row, 6, currency, layout_styles["total_currency"])
    set_ooxml_cell(root, gst_row, 4, quote_tax_label(brief), layout_styles["gst_label"])
    set_ooxml_formula(
        root,
        gst_row,
        5,
        f"ROUND(E{total_row}*{tax_rate:.6f},2)",
        layout_styles["gst_amount"],
        cached_tax,
    )
    set_ooxml_cell(root, gst_row, 6, currency, layout_styles["gst_currency"])
    set_ooxml_cell(root, grand_row, 4, quote_total_including_tax_label(brief), layout_styles["grand_label"])
    set_ooxml_formula(
        root,
        grand_row,
        5,
        f"SUM(E{total_row}:E{gst_row})",
        layout_styles["grand_amount"],
        cached_grand,
    )
    set_ooxml_cell(root, grand_row, 6, currency, layout_styles["grand_currency"])

    acceptance = brief.get("acceptance") if isinstance(brief.get("acceptance"), dict) else {}
    signature = brief.get("signature") if isinstance(brief.get("signature"), dict) else {}
    optional_text_fonts = {
        name: ooxml_style_text_font(parts, layout_styles[name])
        for name in ("terms_heading", "terms_body", "exclusions_heading", "exclusions_body")
    }
    next_text_row = grand_row + 3
    last_optional_row = 0
    optional_text_moved = False

    # Every quotation states its exclusions in red below the totals, before Terms & Conditions.
    exclusion_heading_runs = [RichTextRun(QUOTE_EXCLUSIONS_HEADING)]
    exclusion_runs = [[RichTextRun(text)] for text in QUOTE_EXCLUSIONS]
    exclusions_height = len(wrap_rich_text_runs(exclusion_heading_runs)) + sum(
        len(wrap_rich_text_runs(runs)) for runs in exclusion_runs
    )
    next_text_row, last_optional_row, moved = write_optional_text_block(
        root,
        next_text_row,
        exclusion_heading_runs,
        name="exclusions_heading",
        text_column=1,
        style=layout_styles["exclusions_heading"],
        admission_height=exclusions_height,
        color=QUOTE_EXCLUSIONS_COLOR,
        **optional_text_fonts["exclusions_heading"],
    )
    optional_text_moved = optional_text_moved or moved
    for runs in exclusion_runs:
        next_text_row, last_optional_row, moved = write_optional_text_block(
            root,
            next_text_row,
            runs,
            name="exclusion",
            text_column=2,
            style=layout_styles["exclusions_body"],
            color=QUOTE_EXCLUSIONS_COLOR,
            **optional_text_fonts["exclusions_body"],
        )
        optional_text_moved = optional_text_moved or moved
    next_text_row = last_optional_row + 2

    for heading_key, items_key, rich_heading, rich_items, name in (
        ("terms_heading", "payment_terms", "termsHeading", "paymentTerms", "payment_term"),
        ("notes_heading", "standard_notes", "notesHeading", "standardNotes", "standard_note"),
    ):
        heading = clean_text(brief.get(heading_key))
        item_runs = brief_rich_text_items(brief, rich_items, brief.get(items_key) or [])
        if not (heading or item_runs):
            continue
        next_text_row, section_last_row, moved = write_numbered_text_section(
            root, next_text_row, brief_rich_text_cell_runs(brief, rich_heading, heading), item_runs,
            name=name, has_heading=bool(heading),
            heading_style=layout_styles["terms_heading"], body_style=layout_styles["terms_body"],
            number_style=layout_styles["terms_number"],
            heading_font=optional_text_fonts["terms_heading"], body_font=optional_text_fonts["terms_body"],
        )
        optional_text_moved = optional_text_moved or moved
        if section_last_row:
            last_optional_row = section_last_row
            if name == "payment_term":
                next_text_row = last_optional_row + 2

    manual_pagination_enabled = manual_pagination_enabled or optional_text_moved
    acceptance_candidate_row = last_optional_row + 3 if last_optional_row else next_text_row
    last_content_row, acceptance_moved = write_acceptance_signature(
        root, acceptance_candidate_row, brief, acceptance, signature, company_name,
        layout_styles, parts,
    )
    manual_pagination_enabled = manual_pagination_enabled or acceptance_moved

    manual_pagination_enabled = manual_pagination_enabled or last_content_row > FIRST_PRINT_PAGE_END_ROW
    last_print_row = printable_last_row(root, last_content_row, manual_pagination_enabled)
    trim_layout_worksheet(root, last_print_row)
    complete_quote_layout_worksheet(root, last_print_row, existing_row_numbers)
    if manual_pagination_enabled and (last_optional_row or acceptance_moved):
        hide_empty_continuation_guard_rows(root, last_print_row)
    set_manual_page_breaks(root, last_print_row, manual_pagination_enabled)
    parts["xl/worksheets/sheet1.xml"] = serialize_excel_worksheet(root)
    if "xl/drawings/drawing1.xml" in parts:
        project_number = clean_text(brief.get("project_number") or project.get("number") or "")
        drawing_xml = update_drawing_project_number(parts["xl/drawings/drawing1.xml"], project_number)
        header_lines = company.get("header_lines") if isinstance(company.get("header_lines"), list) else None
        parts["xl/drawings/drawing1.xml"] = update_repeating_header_drawing(drawing_xml, project_number, header_lines, header_line_runs)
    replace_header_logo(parts, clean_text(company.get("logo_data_url")))
    add_continuation_header_logos(parts, last_print_row)
    if "xl/workbook.xml" in parts:
        workbook_xml = update_print_titles(parts["xl/workbook.xml"])
        workbook_xml = update_print_area(workbook_xml, last_print_row)
        parts["xl/workbook.xml"] = enable_workbook_recalculation(workbook_xml)
    strip_stale_workbook_parts(parts)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            zf.writestr(name, content)


# Quote templates (sqag.quote-template.v1). One checked definition drives the Excel file and,
# through LibreOffice or Excel conversion, the PDF. The bundled layout's wrap budgets carry
# over by width: optional text wraps at 104 em across the 123.875 visible width units of B:I,
# item descriptions at 58 characters across the 63.375 units of C:D.
TEMPLATE_TEXT_EM_PER_WIDTH_UNIT = OPTIONAL_TEXT_LINE_MAX_WIDTH_EM / 123.875
TEMPLATE_DESCRIPTION_CHARS_PER_WIDTH_UNIT = 58 / 63.375
TEMPLATE_DATE_FORMAT = "dd\\ mmmm\\ yyyy"
TEMPLATE_AMOUNT_FORMAT = "#,##0.00"
BUILTIN_NUMBER_FORMATS = {"General": 0, "0.00": 2, "#,##0.00": 4}
EMU_PER_PIXEL = 9525
EMU_PER_MM = 36_000
MM_PER_INCH = 25.4
SPREADSHEET_CONTENT_TYPES = {
    "xl/workbook.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "xl/worksheets/sheet1.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml",
    "xl/styles.xml": "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml",
    "xl/drawings/drawing1.xml": "application/vnd.openxmlformats-officedocument.drawing+xml",
}
REL_OFFICE_DOCUMENT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_WORKSHEET = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
REL_STYLES = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"
REL_DRAWING = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing"
REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"


def template_argb(color: str) -> str:
    return "FF" + color[1:].upper()


def template_size_text(size: float) -> str:
    return f"{size:g}"


class TemplateStyleBook:
    """Owns the generated styles.xml: each distinct cell format is stored once at a fixed index."""

    def __init__(self, theme: dict[str, Any]) -> None:
        self.theme = theme
        self._fonts: list[tuple[str, str, bool, bool, str]] = []
        self._borders: list[tuple[str | None, str | None]] = [(None, None)]
        self._num_fmts: dict[str, int] = {}
        self._xfs: list[tuple[int, int, int, str | None, str, bool]] = []
        self.style()

    def _font_id(self, name: str, size: str, bold: bool, italic: bool, color: str) -> int:
        key = (name, size, bold, italic, color)
        if key not in self._fonts:
            self._fonts.append(key)
        return self._fonts.index(key)

    def _border_id(self, top: str | None, bottom: str | None) -> int:
        if (top, bottom) not in self._borders:
            self._borders.append((top, bottom))
        return self._borders.index((top, bottom))

    def _num_fmt_id(self, code: str) -> int:
        if code in BUILTIN_NUMBER_FORMATS:
            return BUILTIN_NUMBER_FORMATS[code]
        return self._num_fmts.setdefault(code, 164 + len(self._num_fmts))

    def style(
        self, *, font: str | None = None, size: float | None = None, bold: bool = False, italic: bool = False,
        color: str | None = None, horizontal: str | None = None, vertical: str = "center", wrap: bool = False,
        num_fmt: str = "General", top: str | None = None, bottom: str | None = None,
    ) -> str:
        font_id = self._font_id(
            font or self.theme["font"], template_size_text(size or self.theme["size_pt"]),
            bold, italic, template_argb(color or self.theme["color"]),
        )
        key = (font_id, self._border_id(top, bottom), self._num_fmt_id(num_fmt), horizontal, vertical, wrap)
        if key not in self._xfs:
            self._xfs.append(key)
        return str(self._xfs.index(key))

    def font(self, style_id: str) -> dict[str, str | None]:
        """Run properties for rich text in a cell of this style (Excel reads runs, not the cell font)."""
        name, size, _, _, color = self._fonts[self._xfs[int(style_id)][0]]
        return {"font_name": name, "font_size": size, "color": color}

    def xml(self) -> bytes:
        root = ET.Element(f"{NS_MAIN}styleSheet")
        if self._num_fmts:
            num_fmts = ET.SubElement(root, f"{NS_MAIN}numFmts", {"count": str(len(self._num_fmts))})
            for code, number in self._num_fmts.items():
                ET.SubElement(num_fmts, f"{NS_MAIN}numFmt", {"numFmtId": str(number), "formatCode": code})
        fonts = ET.SubElement(root, f"{NS_MAIN}fonts", {"count": str(len(self._fonts))})
        for name, size, bold, italic, color in self._fonts:
            font = ET.SubElement(fonts, f"{NS_MAIN}font")
            if bold:
                ET.SubElement(font, f"{NS_MAIN}b")
            if italic:
                ET.SubElement(font, f"{NS_MAIN}i")
            ET.SubElement(font, f"{NS_MAIN}sz", {"val": size})
            ET.SubElement(font, f"{NS_MAIN}color", {"rgb": color})
            ET.SubElement(font, f"{NS_MAIN}name", {"val": name})
            ET.SubElement(font, f"{NS_MAIN}family", {"val": "2"})
        fills = ET.SubElement(root, f"{NS_MAIN}fills", {"count": "2"})
        for pattern in ("none", "gray125"):
            ET.SubElement(ET.SubElement(fills, f"{NS_MAIN}fill"), f"{NS_MAIN}patternFill", {"patternType": pattern})
        borders = ET.SubElement(root, f"{NS_MAIN}borders", {"count": str(len(self._borders))})
        for top, bottom in self._borders:
            border = ET.SubElement(borders, f"{NS_MAIN}border")
            ET.SubElement(border, f"{NS_MAIN}left")
            ET.SubElement(border, f"{NS_MAIN}right")
            for tag, line in (("top", top), ("bottom", bottom)):
                side = ET.SubElement(border, f"{NS_MAIN}{tag}")
                if line:
                    side.attrib["style"] = line
                    ET.SubElement(side, f"{NS_MAIN}color", {"auto": "1"})
            ET.SubElement(border, f"{NS_MAIN}diagonal")
        cell_style_xfs = ET.SubElement(root, f"{NS_MAIN}cellStyleXfs", {"count": "1"})
        ET.SubElement(cell_style_xfs, f"{NS_MAIN}xf", {"numFmtId": "0", "fontId": "0", "fillId": "0", "borderId": "0"})
        cell_xfs = ET.SubElement(root, f"{NS_MAIN}cellXfs", {"count": str(len(self._xfs))})
        for font_id, border_id, num_fmt_id, horizontal, vertical, wrap in self._xfs:
            xf = ET.SubElement(cell_xfs, f"{NS_MAIN}xf", {
                "numFmtId": str(num_fmt_id), "fontId": str(font_id), "fillId": "0", "borderId": str(border_id),
                "xfId": "0", "applyNumberFormat": "1", "applyFont": "1", "applyBorder": "1", "applyAlignment": "1",
            })
            alignment = {"vertical": vertical}
            if horizontal:
                alignment["horizontal"] = horizontal
            if wrap:
                alignment["wrapText"] = "1"
            ET.SubElement(xf, f"{NS_MAIN}alignment", alignment)
        cell_styles = ET.SubElement(root, f"{NS_MAIN}cellStyles", {"count": "1"})
        ET.SubElement(cell_styles, f"{NS_MAIN}cellStyle", {"name": "Normal", "xfId": "0", "builtinId": "0"})
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class TemplateQuoteRenderer:
    """Renders one quote with one checked template into a new workbook (standard library only).

    Calculations (line amounts, totals, tax) are the same as every other quote; the template
    only decides where they sit. All brief and template text is written as inline strings.
    """

    def __init__(self, template: Any, brief: dict[str, Any], lines: list[QuoteLine]) -> None:
        self.template = quote_template.read_quote_template(template)
        self.brief = brief
        self.lines = lines
        self.grid = self.template["grid"]["columns"]
        self.last_col = len(self.grid)
        self.styles = TemplateStyleBook(self.template["theme"])
        page = self.template["page"]
        self.row_height = template_size_text(page["row_height_pt"])
        self.blocks = self.template["blocks"]
        self.table = self._block("item_table")
        top = page["continuation_top_rows"]
        header_rows = 2 if self.table["currency_row"] else 1
        self.page = PageModel(
            first_page_end=page["first_page_rows"],
            page_height=page["page_rows"],
            table_header_offset=top,
            currency_offset=top + 1,
            body_offset=top + header_rows + 1,
        )
        self.root = self._new_worksheet()
        self.continuation_pages: set[int] = set()
        self.manual_pagination = False
        self.optional_text_written = False
        self.signing_moved = False
        self.table_rows: tuple[int, int] | None = None
        self.entries: list[dict[str, Any]] = []
        self.previous_band: str | None = None

    def _block(self, block_type: str) -> dict[str, Any] | None:
        return next((block for block in self.blocks if block["type"] == block_type), None)

    def _block_style(self, block: dict[str, Any], **overrides: Any) -> str:
        style = block.get("style", {})
        options = {
            "font": style.get("font"), "size": style.get("size_pt"), "color": style.get("color"),
            "bold": style.get("bold", False), "italic": style.get("italic", False),
            "horizontal": style.get("align"),
        }
        options.update(overrides)
        return self.styles.style(**options)

    def _block_size(self, block: dict[str, Any]) -> float:
        return float(block.get("style", {}).get("size_pt") or self.template["theme"]["size_pt"])

    def _width_units(self, first: int, last: int) -> float:
        return sum(column["width"] for column in self.grid[first - 1:last] if not column["hidden"])

    def _text_budget(self, first: int, last: int) -> float:
        return math.floor(self._width_units(first, last) * TEMPLATE_TEXT_EM_PER_WIDTH_UNIT + 1e-9)

    def _new_worksheet(self) -> ET.Element:
        root = ET.Element(f"{NS_MAIN}worksheet")
        sheet_pr = ET.SubElement(root, f"{NS_MAIN}sheetPr")
        ET.SubElement(sheet_pr, f"{NS_MAIN}pageSetUpPr", {"fitToPage": "1"})
        ET.SubElement(root, f"{NS_MAIN}dimension", {"ref": "A1"})
        views = ET.SubElement(root, f"{NS_MAIN}sheetViews")
        view = ET.SubElement(views, f"{NS_MAIN}sheetView", {"tabSelected": "1", "workbookViewId": "0"})
        ET.SubElement(view, f"{NS_MAIN}selection", {"activeCell": "A1", "sqref": "A1"})
        ET.SubElement(root, f"{NS_MAIN}sheetFormatPr", {"defaultColWidth": "9.125", "defaultRowHeight": self.row_height})
        cols = ET.SubElement(root, f"{NS_MAIN}cols")
        default_style = self.styles.style()
        for index, column in enumerate(self.grid, start=1):
            attributes = {
                "min": str(index), "max": str(index), "width": f"{column['width']:g}",
                "style": default_style, "customWidth": "1",
            }
            if column["hidden"]:
                attributes["hidden"] = "1"
            ET.SubElement(cols, f"{NS_MAIN}col", attributes)
        ET.SubElement(root, f"{NS_MAIN}sheetData")
        margins = self.template["page"]["margins_mm"]
        ET.SubElement(root, f"{NS_MAIN}pageMargins", {
            key: f"{margins[key] / MM_PER_INCH:.6f}" for key in ("left", "right", "top", "bottom", "header", "footer")
        })
        ET.SubElement(root, f"{NS_MAIN}pageSetup", {
            "paperSize": "9", "orientation": self.template["page"]["orientation"],
            "fitToWidth": "1", "fitToHeight": "0",
        })
        return root

    # Header blocks -------------------------------------------------------------------------

    def _render_header(self) -> None:
        for block in self.blocks:
            if block["area"] != "header":
                continue
            if block["type"] == "client_details":
                self._render_client_details(block)
            elif block["type"] == "quote_details":
                self._render_quote_details(block)
            elif block["type"] == "free_text":
                self._render_header_free_text(block)

    def _rich(self, row: int, col: int, runs: list[Any], style: str) -> None:
        set_ooxml_rich_text_cell(self.root, row, col, runs, style, **self.styles.font(style))

    def _render_client_details(self, block: dict[str, Any]) -> None:
        client = self.brief.get("client") if isinstance(self.brief.get("client"), dict) else {}
        col, indent_col = block["col"], block["col"] + block["indent_cols"]
        bold = self._block_style(block, bold=True)
        regular = self._block_style(block)
        row = block["row"]
        for field in block["fields"]:
            if field == "client_name":
                self._rich(row, col, brief_rich_text_cell_runs(self.brief, "clientName", client.get("name", "")), bold)
                row += 1
            elif field == "client_address":
                address = brief_rich_text_lines(self.brief, "clientAddress", client.get("address") or [])
                for offset, runs in enumerate(address[:block["address_rows"]]):
                    self._rich(row + offset, col, runs, bold)
                row += block["address_rows"]
            elif field == "attention":
                label = self.template["labels"]["attention"]
                self._rich(row, col, [RichTextRun(label, bold=True)] if label else [], bold)
                self._rich(
                    row + 1, indent_col,
                    brief_rich_text_cell_runs(self.brief, "clientAttention", client.get("attention", ""), fallback_bold=True), bold,
                )
                row += 2
            elif field == "client_title":
                self._rich(row, indent_col, brief_rich_text_cell_runs(self.brief, "clientTitle", client.get("title", "")), regular)
                row += 1
            else:
                row += 1

    def _project_number(self) -> str:
        project = self.brief.get("project") if isinstance(self.brief.get("project"), dict) else {}
        return clean_text(self.brief.get("project_number") or project.get("number") or "")

    def _render_quote_details(self, block: dict[str, Any]) -> None:
        project = self.brief.get("project") if isinstance(self.brief.get("project"), dict) else {}
        col, last = block["col"], block["col"] + block["span"] - 1
        row = block["row"]
        for field in block["fields"]:
            if field == "quote_date":
                date_style = self._block_style(block, num_fmt=TEMPLATE_DATE_FORMAT, horizontal="left")
                runs = brief_quote_date_rich_text_runs(self.brief)
                if runs:
                    self._rich(row, col, runs, date_style)
                else:
                    set_ooxml_cell(self.root, row, col, excel_date_serial(str(self.brief.get("quote_date", ""))), date_style)
                if last > col:
                    ensure_merge_ref(self.root, f"{cell_ref(row, col)}:{cell_ref(row, last)}")
            elif field == "project_title":
                self._rich(
                    row, col, brief_rich_text_cell_runs(self.brief, "projectTitle", project.get("title", "")),
                    self._block_style(block, bold=True),
                )
            elif field == "project_number" and self._project_number():
                label = self.template["labels"]["project_number"]
                set_ooxml_cell(self.root, row, col, f"{label} {self._project_number()}".strip(), self._block_style(block))
            row += 1

    def _render_header_free_text(self, block: dict[str, Any]) -> None:
        texts = ([(block["heading"], True)] if block["heading"] else []) + [(line, False) for line in block["lines"]]
        color = block["style"].get("color")
        for offset, (text, bold) in enumerate(texts[:block["rows"]]):
            row = block["row"] + offset
            set_ooxml_cell(self.root, row, block["col"], text, self._block_style(block, bold=bold or block["style"].get("bold", False), color=color))
            if block["span"] > 1:
                ensure_merge_ref(self.root, f"{cell_ref(row, block['col'])}:{cell_ref(row, block['col'] + block['span'] - 1)}")

    # Body blocks ---------------------------------------------------------------------------

    def _bands(self) -> list[list[dict[str, Any]]]:
        bands: list[list[dict[str, Any]]] = []
        for block in self.blocks:
            if block["area"] != "body":
                continue
            if block["beside"] and bands:
                bands[-1].append(block)
            else:
                bands.append([block])
        return bands

    def _render_body(self) -> int:
        cursor = self.template["header"]["rows"] + 1
        for band in self._bands():
            start = cursor + band[0]["gap_before"]
            results = []
            signing = [block for block in band if block["type"] in {"signature", "acceptance"}]
            if signing:
                results.append(self._render_signing(start, signing))
            for block in band:
                if block not in signing:
                    results.append(self._render_block(block, start))
            written = [result for result in results if result is not None]
            if not written:
                continue
            self.previous_band = band[0]["type"]
            cursor = max(end for _, end in written)
        return cursor

    def _render_block(self, block: dict[str, Any], start: int) -> tuple[int, int] | None:
        if block["type"] == "item_table":
            return self._render_item_table(block, start)
        if block["type"] == "totals":
            return self._render_totals(block, start)
        if block["type"] in {"terms", "notes"}:
            return self._render_numbered_text(block, start)
        return self._render_body_free_text(block, start)

    def _table_columns(self) -> dict[str, dict[str, Any]]:
        return {column["key"]: column for column in self.table["columns"]}

    def _write_table_header(self, row: int) -> None:
        heading_color = self.template["theme"]["heading_color"]
        for column in self.table["columns"]:
            set_ooxml_cell(self.root, row, column["col"], column["label"], self._block_style(
                self.table, bold=True, color=heading_color, horizontal=column["align"],
            ))
        if self.table["currency_row"]:
            amount = self._table_columns()["amount"]
            set_ooxml_cell(self.root, row + 1, amount["col"], self.brief.get("currency", "SGD"), self._block_style(
                self.table, bold=True, color=heading_color, horizontal=amount["align"],
            ))

    def _ensure_entry_page(self, row: int, height: int) -> int:
        page = self.page

        def continuation_header(page_start: int) -> int:
            if page_start not in self.continuation_pages:
                self.continuation_pages.add(page_start)
                self._write_table_header(page_start + page.table_header_offset)
            return page_start + page.body_offset

        page_start = page.page_start(row)
        if page_start is not None and row < page_start + page.body_offset:
            row = continuation_header(page_start)
        if row + height - 1 <= page.page_end(row):
            return row
        return continuation_header(page.next_page_start(row))

    def _render_item_table(self, block: dict[str, Any], start: int) -> tuple[int, int]:
        columns = self._table_columns()
        description = columns["description"]
        description_width = max(10, math.floor(
            self._width_units(description["col"], description["col"] + description["span"] - 1)
            * TEMPLATE_DESCRIPTION_CHARS_PER_WIDTH_UNIT + 1e-9,
        ))
        entries = render_quote_entries(self.lines, self.brief, description_width=description_width)
        quantity = columns.get("quantity")
        if "unit" in columns:
            for entry in entries:
                if entry["kind"] == "item":
                    entry["quantity"] = entry["quantity_number"]
        try:
            prepare_quantity_lines(
                entries, self.root, None,
                columns=(quantity["col"], quantity["col"] + quantity["span"] - 1) if quantity else None,
                font_size=self._block_size(block), page=self.page,
                width=quote_template.cell_text_width_points(
                    self.template["grid"], quantity["col"], quantity["span"],
                ) if quantity else None,
            )
        except ValueError as exc:
            if str(exc) != QUOTE_ENTRY_TOO_TALL_MESSAGE:
                raise
            raise quote_template.QuoteTemplateError(
                "page.page_rows",
                "a line item is taller than a later page at these settings; shorten its description or raise page_rows",
            ) from exc
        self._write_table_header(start)
        first_body_row = start + (2 if block["currency_row"] else 1)

        def item_style(key: str, **overrides: Any) -> str:
            return self._block_style(block, horizontal=columns[key]["item_align"], **overrides)

        def section_style(key: str, **overrides: Any) -> str:
            return self._block_style(block, horizontal=columns[key]["align"], **overrides)

        amount_col = columns["amount"]["col"]
        section_price_style = section_style("amount", num_fmt=TEMPLATE_AMOUNT_FORMAT)
        item_price_style = item_style("amount", num_fmt=TEMPLATE_AMOUNT_FORMAT)
        row = first_body_row
        for index, entry in enumerate(entries):
            row = self._ensure_entry_page(row, quote_entry_keep_height(entries, index))
            if entry["kind"] == "section":
                if "position" in columns:
                    set_ooxml_cell(self.root, row, columns["position"]["col"], entry["number"], section_style("position", bold=True))
                set_ooxml_cell(self.root, row, description["col"], entry["section"], section_style("description", bold=True))
                if entry.get("amount") is not None:
                    set_ooxml_cell(self.root, row, amount_col, entry["amount"], section_price_style)
                if entry.get("coverage"):
                    set_ooxml_cell(self.root, row + 1, amount_col, entry["coverage"], self._block_style(block))
                row += 2
                continue
            height = quote_entry_height(entry)
            if "position" in columns:
                set_ooxml_cell(self.root, row, columns["position"]["col"], entry["number"], item_style("position"))
            set_ooxml_cell(self.root, row, amount_col, entry["amount"], item_price_style)
            if "unit" in columns:
                set_ooxml_cell(self.root, row, columns["unit"]["col"], entry["unit"], item_style("unit"))
            if "unit_price" in columns:
                set_ooxml_cell(self.root, row, columns["unit_price"]["col"], entry["unit_price"], item_style("unit_price", num_fmt=TEMPLATE_AMOUNT_FORMAT))
            for offset in range(height):
                if quantity and offset < len(entry["quantity_lines"]):
                    set_ooxml_cell(self.root, row + offset, quantity["col"], entry["quantity_lines"][offset], item_style("quantity"))
                if offset < len(entry["description_lines"]):
                    set_ooxml_cell(self.root, row + offset, description["col"], entry["description_lines"][offset], item_style("description"))
            row += height + 1
        self.table_rows = (first_body_row, row - 1)
        self.entries = entries
        self.manual_pagination = self.manual_pagination or bool(self.continuation_pages)
        return start, row - 1

    def _render_totals(self, block: dict[str, Any], start: int) -> tuple[int, int]:
        total_row = self.page.summary_start(start, TOTAL_BLOCK_HEIGHT)
        self.manual_pagination = self.manual_pagination or total_row != start
        amount = self._table_columns()["amount"]
        letter = excel_col(amount["col"] - 1)
        first_body_row, table_end = self.table_rows
        sum_end = total_row - 1 if self.previous_band == "item_table" else table_end
        tax_rate = quote_tax_rate(self.brief)
        cached_total = round_commercial_cents(sum(formula_cache_amount(entry.get("amount")) for entry in self.entries)) or 0.0
        cached_tax = round_commercial_cents(cached_total * tax_rate) or 0.0
        cached_grand = round_commercial_cents(cached_total + cached_tax) or 0.0
        currency = self.brief.get("currency", "SGD")
        rows = (
            (self.template["labels"]["total"], f"SUM({letter}{first_body_row}:{letter}{max(first_body_row, sum_end)})", cached_total, ("thin", None)),
            (quote_tax_label(self.brief), f"ROUND({letter}{total_row}*{tax_rate:.6f},2)", cached_tax, (None, None)),
            (quote_total_including_tax_label(self.brief), f"SUM({letter}{total_row}:{letter}{total_row + 1})", cached_grand, ("thin", "double")),
        )
        for offset, (label, formula, cached, (top, bottom)) in enumerate(rows):
            row = total_row + offset
            set_ooxml_cell(self.root, row, block["label_col"], label, self._block_style(block, bold=True, horizontal="right", top=top, bottom=bottom))
            set_ooxml_formula(self.root, row, amount["col"], formula, self._block_style(
                block, bold=True, horizontal="right", num_fmt=TEMPLATE_AMOUNT_FORMAT, top=top, bottom=bottom,
            ), cached)
            if block["currency_col"]:
                set_ooxml_cell(self.root, row, block["currency_col"], currency, self._block_style(block, bold=True, horizontal="center", top=top, bottom=bottom))
        return total_row, total_row + TOTAL_BLOCK_HEIGHT

    def _render_body_free_text(self, block: dict[str, Any], start: int) -> tuple[int, int] | None:
        col, last = block["col"], block["col"] + block["span"] - 1
        line_col = col + 1 if block["span"] > 1 else col
        max_width = self._text_budget(line_col, last)
        heading_style = self._block_style(block, bold=True, vertical="top", wrap=True)
        body_style = self._block_style(block, vertical="top", wrap=True)
        heading_runs = [RichTextRun(block["heading"])] if block["heading"] else []
        line_runs = [[RichTextRun(line)] for line in block["lines"]]
        total_height = sum(len(wrap_rich_text_runs(runs, max_width=max_width)) for runs in ([heading_runs] if heading_runs else []) + line_runs)
        row, last_row = start, 0
        for index, (runs, text_col, style) in enumerate(
            ([(heading_runs, col, heading_style)] if heading_runs else []) + [(runs, line_col, body_style) for runs in line_runs]
        ):
            row, last_row, moved = write_optional_text_block(
                self.root, row, runs, name="free_text", text_column=text_col, style=style,
                admission_height=total_height if index == 0 else None,
                last_column=last, max_width=max_width, page=self.page, **self.styles.font(style),
            )
            self.manual_pagination = self.manual_pagination or moved
        if not last_row:
            return None
        self.optional_text_written = True
        return start, last_row + 1

    def _render_numbered_text(self, block: dict[str, Any], start: int) -> tuple[int, int] | None:
        keys = {
            "terms": ("terms_heading", "payment_terms", "termsHeading", "paymentTerms", "payment_term"),
            "notes": ("notes_heading", "standard_notes", "notesHeading", "standardNotes", "standard_note"),
        }[block["type"]]
        heading_key, items_key, rich_heading, rich_items, name = keys
        heading = clean_text(self.brief.get(heading_key))
        item_runs = brief_rich_text_items(self.brief, rich_items, self.brief.get(items_key) or [])
        if not (heading or item_runs):
            return None
        col, last = block["col"], block["col"] + block["span"] - 1
        heading_style = self._block_style(block, bold=True, vertical="top", wrap=True)
        body_style = self._block_style(block, vertical="top", wrap=True)
        number_style = self._block_style(block, vertical="top", horizontal="left")
        _, last_row, moved = write_numbered_text_section(
            self.root, start, brief_rich_text_cell_runs(self.brief, rich_heading, heading), item_runs,
            name=name, has_heading=bool(heading), heading_style=heading_style, body_style=body_style,
            number_style=number_style, heading_font=self.styles.font(heading_style), body_font=self.styles.font(body_style),
            number_column=col, last_column=last, max_width=self._text_budget(col + 1, last), page=self.page,
        )
        self.manual_pagination = self.manual_pagination or moved
        self.optional_text_written = True
        return start, last_row + 1

    def _render_signing(self, start: int, blocks: list[dict[str, Any]]) -> tuple[int, int]:
        sides: dict[str, SigningColumns] = {}
        for block in blocks:
            style = self._block_style(block)
            label_style = self._block_style(block, horizontal="left")
            sides[block["type"]] = SigningColumns(
                block["col"], block["col"] + block["span"] - 1, style, label_style,
                self.styles.font(style), self.styles.font(label_style), block["line_chars"],
            )
        company = self.brief.get("company") if isinstance(self.brief.get("company"), dict) else {}
        acceptance = self.brief.get("acceptance") if isinstance(self.brief.get("acceptance"), dict) else {}
        signature = self.brief.get("signature") if isinstance(self.brief.get("signature"), dict) else {}
        end, moved = write_signing_band(
            self.root, start, self.brief, acceptance, signature, clean_text(company.get("name")),
            sides.get("signature"), sides.get("acceptance"), page=self.page, row_height=self.row_height,
        )
        self.signing_moved = self.signing_moved or moved
        self.manual_pagination = self.manual_pagination or moved
        return start, end

    # Drawing, package -----------------------------------------------------------------------

    def _column_emu(self, col: int) -> int:
        return quote_template.drawing_column_pixels(self.grid[col - 1], self.template["theme"]) * EMU_PER_PIXEL

    def _row_emu(self) -> int:
        return round(float(self.row_height) * EMU_PER_POINT)

    def _anchor_marker(self, parent: ET.Element, tag: str, col: int, x: int, row: int, y: int) -> None:
        """Write a from/to marker for a point x EMU right of column `col` and y EMU below row `row`."""
        while col < self.last_col and x >= self._column_emu(col):
            x -= self._column_emu(col)
            col += 1
        row_emu = self._row_emu()
        row += y // row_emu
        y %= row_emu
        marker = ET.SubElement(parent, f"{NS_DRAWING}{tag}")
        for name, value in (("col", col - 1), ("colOff", x), ("row", row - 1), ("rowOff", y)):
            ET.SubElement(marker, f"{NS_DRAWING}{name}").text = str(value)

    def _rect_emu(self, block: dict[str, Any]) -> tuple[int, int]:
        width = sum(self._column_emu(col) for col in range(block["col"], block["col"] + block["span"]))
        return width, block["rows"] * self._row_emu()

    def _logo_anchors(self, page_starts: list[int]) -> tuple[list[ET.Element], dict[str, bytes]]:
        block = self._block("logo")
        company = self.brief.get("company") if isinstance(self.brief.get("company"), dict) else {}
        match = re.match(r"data:(image/(?:jpeg|jpg|png));base64,(.+)", clean_text(company.get("logo_data_url")), flags=re.IGNORECASE | re.DOTALL)
        if block is None or not match:
            return [], {}
        mime_type = match.group(1).lower().replace("image/jpg", "image/jpeg")
        try:
            image = base64.b64decode(match.group(2), validate=True)
        except ValueError:
            return [], {}
        if not image:
            return [], {}
        logo = block["logo"]
        rect_width, rect_height = self._rect_emu(block)
        box_width = round(logo["width_mm"] * EMU_PER_MM)
        box_height = round(logo["height_mm"] * EMU_PER_MM)
        width, height = box_width, box_height
        dimensions = image_dimensions(image, mime_type)
        if logo["keep_aspect"] and dimensions:
            width, height = fitted_header_logo_extent(dimensions, (box_width, box_height))
        x = {"left": 0, "center": max(0, rect_width - width) // 2, "right": max(0, rect_width - width)}[logo["align"]]
        y = {"top": 0, "middle": max(0, rect_height - height) // 2, "bottom": max(0, rect_height - height)}[logo["valign"]]
        extension = "png" if mime_type == "image/png" else "jpeg"
        anchors = []
        rows = [block["row"]] + ([start + block["row"] - 1 for start in page_starts] if logo["repeat"] else [])
        for number, row in enumerate(rows, start=1):
            anchor = ET.Element(f"{NS_DRAWING}oneCellAnchor")
            self._anchor_marker(anchor, "from", block["col"], x, row, y)
            ET.SubElement(anchor, f"{NS_DRAWING}ext", {"cx": str(width), "cy": str(height)})
            pic = ET.SubElement(anchor, f"{NS_DRAWING}pic")
            nv = ET.SubElement(pic, f"{NS_DRAWING}nvPicPr")
            ET.SubElement(nv, f"{NS_DRAWING}cNvPr", {"id": str(number + 1), "name": "Header Logo" if number == 1 else f"Header Logo - Page {number}"})
            ET.SubElement(ET.SubElement(nv, f"{NS_DRAWING}cNvPicPr"), f"{NS_A}picLocks", {"noChangeAspect": "1"})
            fill = ET.SubElement(pic, f"{NS_DRAWING}blipFill")
            ET.SubElement(fill, f"{NS_A}blip", {f"{NS_REL}embed": "rId1"})
            ET.SubElement(ET.SubElement(fill, f"{NS_A}stretch"), f"{NS_A}fillRect")
            sp_pr = ET.SubElement(pic, f"{NS_DRAWING}spPr")
            xfrm = ET.SubElement(sp_pr, f"{NS_A}xfrm")
            ET.SubElement(xfrm, f"{NS_A}off", {"x": "0", "y": "0"})
            ET.SubElement(xfrm, f"{NS_A}ext", {"cx": str(width), "cy": str(height)})
            ET.SubElement(ET.SubElement(sp_pr, f"{NS_A}prstGeom", {"prst": "rect"}), f"{NS_A}avLst")
            ET.SubElement(anchor, f"{NS_DRAWING}clientData")
            anchors.append(anchor)
        return anchors, {f"xl/media/header_logo.{extension}": image}

    def _company_details_anchor(self) -> ET.Element | None:
        block = self._block("company_details")
        if block is None:
            return None
        company = self.brief.get("company") if isinstance(self.brief.get("company"), dict) else {}
        header_runs = brief_rich_text_lines(self.brief, "headerDetails", company.get("header_lines") or [])
        lines: list[list[RichTextRun] | None] = []
        for field in block["fields"]:
            if field == "header_lines":
                lines.extend(header_runs)
            elif field == "project_number" and self._project_number():
                lines.append([RichTextRun(f"{self.template['labels']['project_number']} {self._project_number()}".strip())])
            elif field == "blank":
                lines.append(None)
        while lines and lines[-1] is None:
            lines.pop()
        if not lines:
            return None
        style = block["style"]
        size = str(round(float(style.get("size_pt") or self.template["theme"]["size_pt"]) * 100))
        color = (style.get("color") or self.template["theme"]["color"])[1:].upper()
        typeface = style.get("font") or self.template["theme"]["font"]
        rect_width, rect_height = self._rect_emu(block)
        box = block["box"]
        left = round(box["left_mm"] * EMU_PER_MM)
        top = round(box["top_mm"] * EMU_PER_MM)
        right = round(box["right_mm"] * EMU_PER_MM)
        if left + right >= rect_width or top >= rect_height:
            raise quote_template.QuoteTemplateError(
                f"blocks[{self.blocks.index(block)}].box", "leaves no room for the company details inside its cells",
            )
        anchor = ET.Element(f"{NS_DRAWING}twoCellAnchor")
        self._anchor_marker(anchor, "from", block["col"], left, block["row"], top)
        self._anchor_marker(anchor, "to", block["col"], rect_width - right, block["row"], rect_height)
        shape = ET.SubElement(anchor, f"{NS_DRAWING}sp", {"macro": "", "textlink": ""})
        nv = ET.SubElement(shape, f"{NS_DRAWING}nvSpPr")
        ET.SubElement(nv, f"{NS_DRAWING}cNvPr", {"id": "1", "name": "Company Details"})
        ET.SubElement(nv, f"{NS_DRAWING}cNvSpPr", {"txBox": "1"})
        sp_pr = ET.SubElement(shape, f"{NS_DRAWING}spPr")
        ET.SubElement(ET.SubElement(sp_pr, f"{NS_A}prstGeom", {"prst": "rect"}), f"{NS_A}avLst")
        ET.SubElement(sp_pr, f"{NS_A}noFill")
        ET.SubElement(ET.SubElement(sp_pr, f"{NS_A}ln"), f"{NS_A}noFill")
        body = ET.SubElement(shape, f"{NS_DRAWING}txBody")
        ET.SubElement(body, f"{NS_A}bodyPr", {"vertOverflow": "clip", "wrap": "square", "lIns": "18288", "tIns": "0", "rIns": "0", "bIns": "0", "anchor": "t"})
        ET.SubElement(body, f"{NS_A}lstStyle")
        align = {"left": "l", "center": "ctr", "right": "r"}[style.get("align", "left")]
        for runs in lines:
            paragraph = ET.SubElement(body, f"{NS_A}p")
            ET.SubElement(paragraph, f"{NS_A}pPr", {"algn": align})
            for run in runs or [RichTextRun("")]:
                drawing_run = ET.SubElement(paragraph, f"{NS_A}r")
                props = ET.SubElement(drawing_run, f"{NS_A}rPr", {
                    "lang": "en-US", "sz": size, "b": "1" if run.bold or style.get("bold") else "0",
                    "i": "1" if run.italic or style.get("italic") else "0",
                })
                if run.underline:
                    props.attrib["u"] = "sng"
                ET.SubElement(ET.SubElement(props, f"{NS_A}solidFill"), f"{NS_A}srgbClr", {"val": color})
                ET.SubElement(props, f"{NS_A}latin", {"typeface": typeface})
                ET.SubElement(drawing_run, f"{NS_A}t").text = run.text
        ET.SubElement(anchor, f"{NS_DRAWING}clientData")
        return anchor

    def _package(self, last_print_row: int, page_starts: list[int]) -> dict[str, bytes]:
        def xml(element: ET.Element) -> bytes:
            return ET.tostring(element, encoding="utf-8", xml_declaration=True)

        def relationships(*items: tuple[str, str, str]) -> bytes:
            root = empty_relationships_root()
            for rel_id, rel_type, target in items:
                ET.SubElement(root, f"{NS_PACKAGE_REL}Relationship", {"Id": rel_id, "Type": rel_type, "Target": target})
            return xml(root)

        parts: dict[str, bytes] = {}
        anchors, media = self._logo_anchors(page_starts)
        details = self._company_details_anchor()
        if details is not None:
            anchors.insert(0, details)
        if anchors:
            drawing = ET.Element(f"{NS_DRAWING}wsDr")
            drawing.extend(anchors)
            parts["xl/drawings/drawing1.xml"] = xml(drawing)
            media_rels = [("rId1", REL_IMAGE, "../media/" + name.rsplit("/", 1)[1]) for name in media]
            parts["xl/drawings/_rels/drawing1.xml.rels"] = relationships(*media_rels)
            parts.update(media)
            ET.SubElement(self.root, f"{NS_MAIN}drawing", {f"{NS_REL}id": "rId1"})
            parts["xl/worksheets/_rels/sheet1.xml.rels"] = relationships(("rId1", REL_DRAWING, "../drawings/drawing1.xml"))
        parts["xl/worksheets/sheet1.xml"] = serialize_excel_worksheet(self.root)
        parts["xl/styles.xml"] = self.styles.xml()
        workbook = ET.Element(f"{NS_MAIN}workbook")
        ET.SubElement(ET.SubElement(workbook, f"{NS_MAIN}bookViews"), f"{NS_MAIN}workbookView")
        sheets = ET.SubElement(workbook, f"{NS_MAIN}sheets")
        ET.SubElement(sheets, f"{NS_MAIN}sheet", {"name": "Quotation", "sheetId": "1", f"{NS_REL}id": "rId1"})
        workbook_xml = update_print_area(xml(workbook), last_print_row, self.last_col)
        parts["xl/workbook.xml"] = enable_workbook_recalculation(workbook_xml)
        parts["xl/_rels/workbook.xml.rels"] = relationships(
            ("rId1", REL_WORKSHEET, "worksheets/sheet1.xml"), ("rId2", REL_STYLES, "styles.xml"),
        )
        parts["_rels/.rels"] = relationships(("rId1", REL_OFFICE_DOCUMENT, "xl/workbook.xml"))
        types = ET.Element(f"{NS_CONTENT_TYPES}Types")
        for extension, content_type in (
            ("rels", "application/vnd.openxmlformats-package.relationships+xml"), ("xml", "application/xml"),
            ("png", "image/png"), ("jpeg", "image/jpeg"),
        ):
            ET.SubElement(types, f"{NS_CONTENT_TYPES}Default", {"Extension": extension, "ContentType": content_type})
        for name, content_type in SPREADSHEET_CONTENT_TYPES.items():
            if name in parts:
                ET.SubElement(types, f"{NS_CONTENT_TYPES}Override", {"PartName": "/" + name, "ContentType": content_type})
        parts["[Content_Types].xml"] = xml(types)
        sanitize_core_properties(parts)
        return parts

    def write(self, path: Path) -> None:
        self._render_header()
        last_content_row = self._render_body()
        manual = self.manual_pagination or last_content_row > self.page.first_page_end
        last_print_row = printable_last_row(self.root, last_content_row, manual, self.page)
        trim_layout_worksheet(self.root, last_print_row, self.last_col)
        sheet_data = self.root.find(f"{NS_MAIN}sheetData")
        for row_number in range(1, last_print_row + 1):
            row = get_or_create_row(sheet_data, row_number)
            row.set("ht", self.row_height)
            row.set("customHeight", "1")
        if manual and (self.optional_text_written or self.signing_moved):
            hide_empty_continuation_guard_rows(self.root, last_print_row, self.page)
        set_manual_page_breaks(self.root, last_print_row, manual, self.page)
        page_starts = [break_id + 1 for break_id in self.page.break_ids(last_print_row) if manual and break_id < last_print_row]
        parts = self._package(last_print_row, page_starts)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in parts.items():
                zf.writestr(name, content)


def write_template_quote_xlsx(template: Any, path: Path, brief: dict[str, Any], lines: list[QuoteLine]) -> None:
    """Write the quote with a quote template definition instead of a profile layout workbook."""
    TemplateQuoteRenderer(template, brief, lines).write(path)


def powershell_literal(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def powershell_export_script(xlsx_path: Path, pdf_path: Path) -> str:
    xlsx_export_path = xlsx_path.resolve()
    pdf_export_path = pdf_path.resolve()
    return f"""
$ErrorActionPreference = 'Stop'
$xlsxPath = '{powershell_literal(xlsx_export_path)}'
$pdfPath = '{powershell_literal(pdf_export_path)}'
$repairedWorkbookPath = [System.IO.Path]::Combine(
  [System.IO.Path]::GetDirectoryName($xlsxPath),
  ([System.IO.Path]::GetFileNameWithoutExtension($xlsxPath) + '.excel-repaired.xlsx')
)
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
try {{
  # CorruptLoad 1 lets Excel repair stale template metadata before print export.
  $workbook = $excel.Workbooks.Open($xlsxPath, 0, $false, 5, '', '', $true, 1, '', $false, $false, $null, $false, $true, 1)
  try {{
    $workbook.SaveAs($repairedWorkbookPath, 51)
    $workbook.ExportAsFixedFormat(0, $pdfPath)
  }} finally {{
    $workbook.Close($false)
  }}
}} finally {{
  $excel.Quit()
}}
"""


def powershell_pdf_export(xlsx_path: Path, pdf_path: Path, *, timeout: float = 120) -> str | None:
    if os.name != "nt" or shutil.which("powershell") is None:
        return None
    with tempfile.TemporaryDirectory(prefix="sqag-excel-") as temp_dir:
        conversion_path = Path(temp_dir) / xlsx_path.name
        shutil.copyfile(xlsx_path, conversion_path)
        script = powershell_export_script(conversion_path, pdf_path)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    return "excel_exported" if result.returncode == 0 and pdf_path.exists() else None


def libreoffice_candidates() -> list[str]:
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
    ]
    if os.name == "nt":
        candidates.extend([
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        ])
    seen: set[str] = set()
    result: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        path = str(candidate)
        key = path.lower()
        if key not in seen and Path(path).exists():
            seen.add(key)
            result.append(path)
    return result


def write_pdf_conversion_workbook(xlsx_path: Path, conversion_path: Path) -> None:
    """Make ambiguous PDF font defaults explicit without changing the master."""
    with zipfile.ZipFile(xlsx_path) as source:
        entries = source.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > MAX_XLSX_ENTRIES or len(names) != len(set(names)):
            raise ValueError("PDF workbook package has too many or duplicate parts.")
        drawings: dict[str, bytes] = {}
        aliases: set[str] = set()
        for name in names:
            if not name.startswith("xl/drawings/") or not name.endswith(".xml"):
                continue
            drawing_xml = read_bounded_xlsx_part(source, name)
            drawing = ET.fromstring(drawing_xml)
            referenced = {
                node.attrib.get("typeface", "")
                for node in drawing.iter(f"{NS_A}latin")
            } & {"+mn-lt", "+mj-lt"}
            if referenced:
                drawings[name] = drawing_xml
                aliases.update(referenced)
        rich_text: dict[str, bytes] = {}
        default_font: dict[str, str] = {}
        for name in names:
            if name != "xl/sharedStrings.xml" and not (name.startswith("xl/worksheets/") and name.endswith(".xml")):
                continue
            part_xml = read_bounded_xlsx_part(source, name)
            part = ET.fromstring(part_xml)
            partial = [
                props for container in part.iter()
                if container.tag in {f"{NS_MAIN}is", f"{NS_MAIN}si"}
                for run in container.findall(f"{NS_MAIN}r")
                if (props := run.find(f"{NS_MAIN}rPr")) is not None
                and (props.find(f"{NS_MAIN}rFont") is None or props.find(f"{NS_MAIN}sz") is None)
            ]
            if not partial:
                continue
            rich_text[name] = part_xml
            for props in partial:
                scheme = props.find(f"{NS_MAIN}scheme")
                if props.find(f"{NS_MAIN}rFont") is None and scheme is not None:
                    alias = {"minor": "+mn-lt", "major": "+mj-lt", "none": ""}.get(scheme.attrib.get("val"))
                    if alias is None:
                        raise ValueError("PDF rich text font scheme is unavailable.")
                    if alias:
                        aliases.add(alias)
        if rich_text:
            if "xl/styles.xml" not in names:
                raise ValueError("PDF rich text requires workbook font defaults.")
            styles = ET.fromstring(read_bounded_xlsx_part(source, "xl/styles.xml"))
            first_font = styles.find(f"{NS_MAIN}fonts/{NS_MAIN}font")
            if first_font is None:
                raise ValueError("PDF rich text requires workbook font defaults.")
            default_font = {node.tag.removeprefix(NS_MAIN): node.attrib.get("val", "") for node in first_font}
            try:
                size = float(default_font.get("sz", ""))
            except ValueError:
                size = math.nan
            if not default_font.get("name", "").strip() or not math.isfinite(size) or size <= 0:
                raise ValueError("PDF rich text font defaults are unavailable.")
            if default_font.get("scheme") in {"minor", "major"}:
                aliases.add("+mn-lt" if default_font["scheme"] == "minor" else "+mj-lt")
        fonts: dict[str, str] = {}
        if aliases:
            relationships = ET.fromstring(read_bounded_xlsx_part(source, "xl/_rels/workbook.xml.rels"))
            themes = [
                relationship for relationship in relationships
                if relationship.attrib.get("Type") == f"{NS_REL[1:-1]}/theme"
            ]
            if len(themes) != 1 or themes[0].attrib.get("TargetMode", "Internal") != "Internal":
                raise ValueError("PDF workbook requires one internal theme.")
            target = themes[0].attrib.get("Target", "")
            if not target or "\\" in target:
                raise ValueError("PDF workbook theme target is invalid.")
            theme_part = posixpath.normpath(target.lstrip("/") if target.startswith("/") else f"xl/{target}")
            if theme_part.startswith("../") or theme_part not in names:
                raise ValueError("PDF workbook theme part is unavailable.")
            theme = ET.fromstring(read_bounded_xlsx_part(source, theme_part))
            for alias, family in (("+mn-lt", "minorFont"), ("+mj-lt", "majorFont")):
                if alias not in aliases:
                    continue
                font = theme.find(f"{NS_A}themeElements/{NS_A}fontScheme/{NS_A}{family}/{NS_A}latin")
                typeface = font.attrib.get("typeface", "").strip() if font is not None else ""
                if not typeface or typeface.startswith("+"):
                    raise ValueError("PDF workbook theme font is unavailable.")
                fonts[alias] = typeface
        replacements: dict[str, bytes] = {}
        for name, drawing_xml in drawings.items():
            # Compatibility attributes contain namespace prefixes as text.
            # Preserve their declarations, including nested scopes, when writing.
            with minidom.parseString(drawing_xml) as drawing:
                for node in drawing.getElementsByTagNameNS(NS_A[1:-1], "latin"):
                    alias = node.getAttribute("typeface")
                    if alias in fonts:
                        node.setAttribute("typeface", fonts[alias])
                replacements[name] = drawing.toxml(encoding="utf-8")
        for name, part_xml in rich_text.items():
            with minidom.parseString(part_xml) as part:
                for container in part.getElementsByTagNameNS(NS_MAIN[1:-1], "is") + part.getElementsByTagNameNS(NS_MAIN[1:-1], "si"):
                    for run in container.childNodes:
                        if run.namespaceURI != NS_MAIN[1:-1] or run.localName != "r":
                            continue
                        props = next((node for node in run.childNodes if node.namespaceURI == NS_MAIN[1:-1] and node.localName == "rPr"), None)
                        if props is None:
                            continue
                        existing = {node.localName: node for node in props.childNodes if node.namespaceURI == NS_MAIN[1:-1]}
                        if "rFont" in existing and "sz" in existing:
                            continue
                        # A partial rich-text rPr uses workbook font zero defaults
                        # in Excel. Calc otherwise substitutes its application font.
                        scheme = existing["scheme"].getAttribute("val") if "scheme" in existing else default_font.get("scheme")
                        alias = {"minor": "+mn-lt", "major": "+mj-lt"}.get(scheme)
                        if "rFont" in existing:
                            typeface = existing["rFont"].getAttribute("val")
                        else:
                            typeface = fonts[alias] if alias else default_font["name"]
                        for tag, value in (("rFont", typeface), ("sz", default_font["sz"])):
                            if tag in existing:
                                continue
                            node = part.createElementNS(NS_MAIN[1:-1], f"{props.prefix}:{tag}" if props.prefix else tag)
                            node.setAttribute("val", value)
                            props.appendChild(node)
                replacements[name] = part.toxml(encoding="utf-8")
        with zipfile.ZipFile(conversion_path, "w") as converted:
            for entry in entries:
                if entry.filename in replacements:
                    converted.writestr(copy.copy(entry), replacements[entry.filename])
                else:
                    with source.open(entry) as incoming, converted.open(copy.copy(entry), "w") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)


def libreoffice_pdf_export(xlsx_path: Path, pdf_path: Path, *, timeout: float = 120) -> str | None:
    executables = libreoffice_candidates()
    if not executables:
        return None
    with tempfile.TemporaryDirectory(prefix="sqag-libreoffice-") as profile_dir:
        conversion_dir = Path(profile_dir) / "input"
        conversion_dir.mkdir()
        conversion_path = conversion_dir / xlsx_path.name
        write_pdf_conversion_workbook(xlsx_path, conversion_path)
        result = subprocess.run(
            [
                executables[0],
                f"-env:UserInstallation={Path(profile_dir).resolve().as_uri()}",
                "--headless",
                "--convert-to",
                "pdf:calc_pdf_Export",
                "--outdir",
                str(pdf_path.parent),
                str(conversion_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    converted = pdf_path.parent / f"{xlsx_path.stem}.pdf"
    if result.returncode == 0 and converted.exists():
        if converted != pdf_path:
            converted.replace(pdf_path)
        return "libreoffice_exported"
    return None


@dataclass(frozen=True)
class PdfOverflowBoundary:
    print_area: str
    first_text: set[str]
    continuation_text: set[str]


def pdf_overflow_boundary(xlsx_path: Path) -> PdfOverflowBoundary | None:
    """Find the single authored boundary on the printed worksheet."""
    book_part = "xl/workbook.xml"
    sheet_part = "xl/worksheets/sheet1.xml"
    try:
        with zipfile.ZipFile(xlsx_path) as workbook:
            sheet = ET.fromstring(read_bounded_xlsx_part(workbook, sheet_part))
            book = ET.fromstring(read_bounded_xlsx_part(workbook, book_part))
            rels = ET.fromstring(read_bounded_xlsx_part(workbook, "xl/_rels/workbook.xml.rels"))
    except (KeyError, OSError, ValueError, RuntimeError, ET.ParseError, zipfile.BadZipFile):
        return None

    sheets = book.findall(f"{NS_MAIN}sheets/{NS_MAIN}sheet")
    if len(sheets) != 1 or sheets[0].get("state", "visible") != "visible":
        return None
    sheet_name = sheets[0].get("name", "")
    rel_id = sheets[0].get(f"{NS_REL}id")
    sheet_rels = [rel for rel in rels.findall(f"{NS_PACKAGE_REL}Relationship") if rel.get("Id") == rel_id]
    if not sheet_name or not rel_id or len(sheet_rels) != 1:
        return None
    rel = sheet_rels[0]
    target = rel.get("Target", "")
    target_path = posixpath.normpath(target[1:] if target.startswith("/") else f"xl/{target}")
    if (
        rel.get("Type") != f"{NS_REL[1:-1]}/worksheet"
        or rel.get("TargetMode", "Internal") != "Internal"
        or target_path != sheet_part
    ):
        return None

    row_breaks = sheet.findall(f"{NS_MAIN}rowBreaks")
    col_breaks = [node for node in sheet if node.tag.rsplit("}", 1)[-1].casefold() == "colbreaks"]
    if (
        len(row_breaks) != 1 or len(row_breaks[0]) != 1
        or row_breaks[0][0].tag != f"{NS_MAIN}brk"
        or row_breaks[0][0].attrib != {"id": str(FIRST_PRINT_PAGE_END_ROW), "max": "16383", "man": "1"}
        or len(col_breaks) > 1
    ):
        return None
    if col_breaks:
        column_break = col_breaks[0]
        # Accept XML unsignedInt spellings of zero without converting untrusted counts to integers.
        if (
            column_break.tag != f"{NS_MAIN}colBreaks" or len(column_break) or (column_break.text or "").strip()
            or any(
                key not in {"count", "manualBreakCount"} or not re.fullmatch(r"[+-]?0+", value.strip(" \t\r\n"))
                for key, value in column_break.attrib.items()
            )
        ):
            return None

    print_names = book.findall(f"{NS_MAIN}definedNames/{NS_MAIN}definedName")
    if any(name.get("name", "").casefold() == "_xlnm.print_titles" for name in print_names):
        return None
    areas = [name for name in print_names if name.get("name", "").casefold() == "_xlnm.print_area"]
    if len(areas) != 1 or areas[0].get("localSheetId") != "0":
        return None
    print_area = areas[0].text or ""
    reference, _, cells = print_area.rpartition("!")
    quoted_sheet_name = "'" + sheet_name.replace("'", "''") + "'"
    area = re.fullmatch(r"\$A\$1:\$I\$([1-9][0-9]{0,6})", cells)
    if area is None or reference.casefold() not in {sheet_name.casefold(), quoted_sheet_name.casefold()}:
        return None
    last_row = int(area[1])
    if not FIRST_PRINT_PAGE_END_ROW < last_row < CONTINUATION_PAGE_START_ROW + CONTINUATION_PAGE_HEIGHT:
        return None

    first_text, next_text = set(), set()
    last_first_payload = 0
    for row in sheet.findall(f"{NS_MAIN}sheetData/{NS_MAIN}row"):
        row_id = row.get("r", "")
        if not re.fullmatch(r"[1-9][0-9]{0,6}", row_id):
            return None
        number = int(row_id)
        if number > last_row or row.get("hidden") in {"1", "true"}:
            continue
        on_first = number <= FIRST_PRINT_PAGE_END_ROW
        for cell in row.findall(f"{NS_MAIN}c"):
            if not re.fullmatch(r"[A-I]" + row_id, cell.get("r", "")):
                continue
            if on_first and ooxml_cell_has_payload(cell):
                last_first_payload = max(last_first_payload, number)
            inline = cell.find(f"{NS_MAIN}is")
            if inline is not None:
                value = "".join(node.text or "" for node in inline.iter(f"{NS_MAIN}t"))
                value = " ".join(value.casefold().split())
                if len(value) >= 4:
                    (first_text if on_first else next_text).add(value)
    if last_first_payload >= FIRST_PRINT_PAGE_END_ROW or not first_text or not next_text:
        return None
    return PdfOverflowBoundary(print_area, first_text, next_text)


def pdf_first_section_matches(
    xlsx_path: Path,
    print_area: str,
    native_export: Callable[[Path, Path], str | None],
    expected: tuple[tuple[str, tuple, bytes, bool], ...],
    pdfium,
) -> bool:
    """Render just the authored first section and compare its native pages."""
    original_area = f">{print_area}</".encode("utf-8")
    first_section = print_area.rsplit("$", 1)[0] + f"${FIRST_PRINT_PAGE_END_ROW}"
    first_area = f">{first_section}</".encode("utf-8")
    book_part = "xl/workbook.xml"
    with tempfile.TemporaryDirectory(prefix="sqag-pdf-boundary-") as work:
        workbook_path = Path(work) / "input" / xlsx_path.name
        workbook_path.parent.mkdir()
        with zipfile.ZipFile(xlsx_path) as source:
            entries = source.infolist()
            if (
                len(entries) > MAX_XLSX_ENTRIES
                or len({entry.filename for entry in entries}) != len(entries)
                or sum(entry.file_size for entry in entries) > MAX_PDF_PROOF_WORKBOOK_BYTES
            ):
                return False
            book = read_bounded_xlsx_part(source, book_part)
            if book.count(original_area) != 1:
                return False
            with zipfile.ZipFile(workbook_path, "w") as workbook:
                for entry in entries:
                    if entry.filename == book_part:
                        workbook.writestr(copy.copy(entry), book.replace(original_area, first_area, 1))
                    else:
                        with source.open(entry) as incoming, workbook.open(copy.copy(entry), "w") as outgoing:
                            shutil.copyfileobj(incoming, outgoing)

        probe_path = Path(work) / workbook_path.with_suffix(".pdf").name
        if not native_export(workbook_path, probe_path):
            return False
        with probe_path.open("rb") as probe:
            data = probe.read(MAX_PDF_PAGINATION_BYTES + 1)
        if len(data) > MAX_PDF_PAGINATION_BYTES:
            return False
        native = pdfium.raw
        with pdfium.PdfDocument(data) as document:
            if len(document) != len(expected) or not native.FPDF_DocumentHasValidCrossReferenceTable(document):
                return False
            for index, snapshot in enumerate(expected):
                page = document[index]
                try:
                    if native.FPDFPage_GetAnnotCount(page) != 0 or pdf_page_export_snapshot(page, pdfium) != snapshot:
                        return False
                    if index == 1 and native.FPDFPage_CountObjects(page) != 0:
                        return False
                finally:
                    page.close()
    return True


def pdf_page_export_snapshot(page, pdfium) -> tuple[str, tuple, bytes, bool]:
    width, height = page.get_size()
    if not (0 < width <= 1440 and 0 < height <= 1440):
        raise ValueError("Unsupported PDF page size for pagination.")
    textpage = page.get_textpage()
    try:
        text = textpage.get_text_range()
    finally:
        textpage.close()
    bitmap = page.render(scale=1, force_bitmap_format=pdfium.raw.FPDFBitmap_BGRA)
    try:
        pixels = bytes(bitmap.buffer)
        white = pixels.count(255) == len(pixels)
        geometry = (page.get_size(), page.get_bbox(), page.get_rotation())
        return text, geometry, hashlib.sha256(pixels).digest(), white
    finally:
        bitmap.close()


def pdfium_flat_page_ids(data: bytes) -> list[bytes]:
    # PDFium's full save expands object streams. Admit its flat, zero-generation
    # page tree only, so external references to the removed page can be detected.
    if len(data) > MAX_PDF_PAGINATION_BYTES or re.search(
        rb"/(?:ObjStm|OpenAction|AA|Names|Dests|Outlines|PageLabels|OCProperties|AcroForm|"
        rb"Annots|Perms|Encrypt|Collection|PrintPageRange|Threads)(?=[\x00\s/<>\[\]()%])", data
    ):
        return []
    trees = re.findall(rb"(?m)^\d+ 0 obj\r?\n<</Count (\d+)/Kids\[([ 0-9R]+)\][^\r\n]*?/Type/Pages>>\r?$", data)
    if len(trees) != 1:
        return []
    count, kids = trees[0]
    ids = re.findall(rb"([1-9][0-9]*) 0 R", kids)
    return ids if count == str(len(ids)).encode("ascii") and len(set(ids)) == len(ids) else []


def remove_blank_pdf_overflow(
    xlsx_path: Path,
    pdf_path: Path,
    boundary: PdfOverflowBoundary,
    native_export: Callable[[Path, Path], str | None],
) -> None:
    """Keep the native bytes unless page two is proven to be incidental overflow."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return

    try:
        with pdf_path.open("rb") as export:
            original = export.read(MAX_PDF_PAGINATION_BYTES + 1)
        if len(original) > MAX_PDF_PAGINATION_BYTES:
            return
        native = pdfium.raw
        with pdfium.PdfDocument(original) as document:
            if (
                len(document) != 3 or not native.FPDF_DocumentHasValidCrossReferenceTable(document)
                or next(document.get_toc(), None) is not None
            ):
                return
            with_pages = []
            for index in (1, 0, 2):
                page = document[index]
                try:
                    if native.FPDFPage_GetAnnotCount(page) != 0:
                        return
                    if index == 1 and native.FPDFPage_CountObjects(page) != 0:
                        return
                    snapshot = pdf_page_export_snapshot(page, pdfium)
                finally:
                    page.close()
                with_pages.append(snapshot)
            blank, first, continuation = with_pages
            if blank[0] or not blank[3]:
                return
            first_text = " ".join(first[0].casefold().split())
            next_text = " ".join(continuation[0].casefold().split())
            if not all(
                any(value in text for value in group)
                for group, text in ((boundary.first_text, first_text), (boundary.continuation_text, next_text))
            ):
                return

            with io.BytesIO() as expanded:
                document.save(expanded, flags=native.FPDF_NO_INCREMENTAL)
                data = expanded.getvalue()
            page_ids = pdfium_flat_page_ids(data)
            if len(page_ids) != 3:
                return
            removed_id = page_ids[1]
            ref_pattern = rb"(?<![0-9.])" + removed_id + rb"\s+0\s+R\b"
            if len(re.findall(ref_pattern, data)) != 1:
                return
            blank_dictionary = re.search(rb"(?m)^" + removed_id + rb" 0 obj\r?\n(<</[^\r\n]+)\r?$", data)
            if blank_dictionary is None or re.search(rb"/StructParents?\b", blank_dictionary[1]):
                return

            # Only a native match for both pages places the white page before
            # the authored break. A blank at the start of the continuation fails
            # this proof even when the surrounding pages contain matching text.
            if not pdf_first_section_matches(xlsx_path, boundary.print_area, native_export, (first, blank), pdfium):
                return

            metadata = document.get_metadata_dict()
            tagged = document.is_tagged()
            document.del_page(1)
            with io.BytesIO() as output:
                document.save(output, flags=native.FPDF_NO_INCREMENTAL)
                corrected = output.getvalue()

        if pdfium_flat_page_ids(corrected) != [page_ids[0], page_ids[2]]:
            return
        with pdfium.PdfDocument(corrected) as document:
            if len(document) != 2 or document.get_metadata_dict() != metadata or document.is_tagged() != tagged:
                return
            for index, expected in enumerate((first, continuation)):
                page = document[index]
                try:
                    if pdf_page_export_snapshot(page, pdfium) != expected:
                        return
                finally:
                    page.close()
        with tempfile.TemporaryDirectory(prefix="sqag-pdf-", dir=pdf_path.parent) as scratch:
            candidate = Path(scratch) / pdf_path.name
            candidate.write_bytes(corrected)
            candidate.replace(pdf_path)
    except (KeyError, OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, zipfile.BadZipFile, pdfium.PdfiumError):
        # A failed inspection, proof or rewrite must leave the valid native export in place.
        return


def export_layout_pdf(xlsx_path: Path, pdf_path: Path) -> str | None:
    deadline = time.monotonic() + PDF_PROOF_DEADLINE_SECONDS
    native_export = libreoffice_pdf_export
    status = native_export(xlsx_path, pdf_path)
    if not status:
        native_export = powershell_pdf_export
        status = native_export(xlsx_path, pdf_path)
    if status and (boundary := pdf_overflow_boundary(xlsx_path)):
        # The web generator has a 90-second deadline. Leave time to publish
        # the native PDF if the additional render is too slow to prove ownership.
        def export_first_section(source: Path, output: Path) -> str | None:
            timeout = min(MAX_PDF_PROOF_SECONDS, deadline - time.monotonic())
            return native_export(source, output, timeout=timeout) if timeout > 0 else None

        remove_blank_pdf_overflow(xlsx_path, pdf_path, boundary, export_first_section)
    return status


def pdf_date_text(value: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%d %B %Y")


def display_amount(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return money(float(value))
    return str(value)


def build_pdf_cell_map(brief: dict[str, Any], lines: list[QuoteLine]) -> dict[tuple[int, int], Any]:
    client = brief["client"]
    project = brief["project"]
    currency = brief.get("currency", "SGD")
    company = brief.get("company") if isinstance(brief.get("company"), dict) else {}
    acceptance = brief.get("acceptance") if isinstance(brief.get("acceptance"), dict) else {}
    signature = brief.get("signature") if isinstance(brief.get("signature"), dict) else {}
    company_name = clean_text(acceptance.get("company_name")) or clean_text(company.get("name"))
    cells: dict[tuple[int, int], Any] = {
        (6, 1): client.get("name", ""),
        (11, 1): "Attention:",
        (12, 2): client.get("attention", ""),
        (13, 2): client.get("title", ""),
        (16, 1): pdf_date_text(str(brief.get("quote_date", ""))),
        (18, 1): project.get("title", ""),
        (20, 1): "Pos.",
        (20, 2): "Quantity",
        (20, 3): "Service",
        (20, 5): "Estimate",
        (21, 5): currency,
        (53, 1): "Pos.",
        (53, 2): "Quantity",
        (53, 3): "Service",
        (53, 5): "Estimate",
        (92, 4): "Total",
        (92, 6): currency,
        (93, 4): quote_tax_label(brief),
        (94, 4): quote_total_including_tax_label(brief),
        (94, 6): currency,
        (106, 5): clean_text(acceptance.get("text")),
        (117, 2): company_name,
        (121, 2): "_____________________________",
        (121, 5): "_____________________________________",
        (122, 2): clean_text(signature.get("company_signatory")),
        (122, 5): clean_text(acceptance.get("person_label")),
        (123, 2): clean_text(signature.get("company_title")),
        (123, 5): clean_text(acceptance.get("stamp_label")),
        (124, 2): clean_text(signature.get("company_date_label")),
        (124, 5): clean_text(acceptance.get("date_label")),
    }
    for offset, address_line in enumerate((client.get("address") or [])[:4], start=7):
        cells[(offset, 1)] = address_line

    entries = render_quote_entries(lines, brief)
    row_number = 22
    for entry in entries:
        row_number = next_quote_row(row_number)
        if row_number >= 53 and row_number < 55:
            row_number = 55
        if row_number > 91:
            break
        if entry["kind"] == "section":
            cells[(row_number, 1)] = entry["number"]
            cells[(row_number, 3)] = entry["section"]
            if entry.get("amount") is not None:
                cells[(row_number, 5)] = entry["amount"]
            if entry.get("coverage"):
                cells[(row_number + 1, 5)] = entry["coverage"]
            if entry.get("remark"):
                cells[(row_number, 6)] = entry["remark"]
            row_number += 2
            continue
        cells[(row_number, 1)] = entry["number"]
        cells[(row_number, 2)] = entry["quantity"]
        cells[(row_number, 3)] = entry["description_lines"][0]
        cells[(row_number, 5)] = entry["amount"]
        for extra in entry["description_lines"][1:]:
            row_number += 1
            if row_number == 53:
                row_number = 55
            if row_number > 91:
                break
            cells[(row_number, 3)] = extra
        row_number += 2

    subtotal = quote_subtotal(entries)
    tax_rate = quote_tax_rate(brief)
    tax_amount = round_commercial_cents(subtotal * tax_rate) or 0.0
    cells[(92, 5)] = subtotal
    cells[(93, 5)] = tax_amount
    cells[(93, 6)] = currency
    cells[(94, 5)] = round_commercial_cents(subtotal + tax_amount) or 0.0
    text_row = 99
    terms_heading = clean_text(brief.get("terms_heading"))
    payment_terms = brief.get("payment_terms") or []
    if terms_heading:
        cells[(text_row, 1)] = terms_heading
        text_row += 1
    for index, term in enumerate(payment_terms, start=1):
        cells[(text_row, 1)] = f"{index:.2f}"
        cells[(text_row, 2)] = term
        text_row += 1
    if terms_heading or payment_terms:
        text_row += 1

    notes_heading = clean_text(brief.get("notes_heading"))
    standard_notes = brief.get("standard_notes") or []
    if notes_heading:
        cells[(text_row, 1)] = notes_heading
        text_row += 1
    for index, note in enumerate(standard_notes, start=1):
        cells[(text_row, 1)] = f"{index:.2f}"
        cells[(text_row, 2)] = note
        text_row += 1
    return cells


def image_data_url_stream(data_url: Any) -> io.BytesIO | None:
    match = re.match(r"data:image/(?:jpeg|jpg|png);base64,(.+)", clean_text(data_url), flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    try:
        return io.BytesIO(base64.b64decode(match.group(1), validate=True))
    except (ValueError, binascii.Error):
        return None


def company_header_lines(brief: dict[str, Any]) -> list[str]:
    company = brief.get("company") if isinstance(brief.get("company"), dict) else {}
    header_lines = company.get("header_lines") if isinstance(company.get("header_lines"), list) else []
    return [clean_text(line) if line is not None else "" for line in header_lines]


def write_styled_pdf_fallback(path: Path, brief: dict[str, Any], lines: list[QuoteLine], layout_template: Path) -> bool:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except Exception:
        return False

    cells = build_pdf_cell_map(brief, lines)
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    page_ranges = [(1, 52), (53, 95), (96, 124)]
    x_positions = {1: 58, 2: 104, 3: 162, 4: 398, 5: 506, 6: 548}
    row_height = 13.0
    company = brief.get("company") if isinstance(brief.get("company"), dict) else {}
    logo = image_data_url_stream(company.get("logo_data_url"))

    for page_number, (start_row, end_row) in enumerate(page_ranges, start=1):
        top_y = height - (135 if page_number > 1 else 58)
        if logo is not None:
            logo.seek(0)
            c.drawImage(ImageReader(logo), width - 152, height - 54, width=124, height=25, preserveAspectRatio=True, mask="auto")
            c.setFont("Helvetica", 5.5)
            detail_y = height - 80
            for text in company_header_lines(brief):
                c.drawRightString(width - 28, detail_y, text)
                detail_y -= 7

        for row_number in range(start_row, end_row + 1):
            y = top_y - ((row_number - start_row) * row_height)
            row_values = {col: cells.get((row_number, col)) for col in x_positions}
            if all(value in (None, "") for value in row_values.values()):
                continue
            is_section = row_values.get(1) not in (None, "") and row_values.get(2) in (None, "") and row_values.get(3) not in (None, "") and row_number not in {20, 53}
            is_bold = row_number in {1, 6, 12, 18, 20, 53, 92, 93, 94, 99, 103, 117} or is_section
            c.setFont("Helvetica-Bold" if is_bold else "Helvetica", 10 if row_number < 96 else 8.4)
            if row_number in {92, 94}:
                c.setLineWidth(1.4)
                c.line(284, y + 6.5, width - 28, y + 6.5)
            for col_number, value in row_values.items():
                if value in (None, ""):
                    continue
                if page_number == 3 and col_number == 5 and row_number < 117:
                    continue
                text = display_amount(value) if col_number == 5 else str(value)
                x = x_positions[col_number]
                if row_number in {93, 94}:
                    if col_number == 4:
                        c.drawRightString(386, y, text)
                    elif col_number == 5:
                        c.drawCentredString(462, y, text)
                    elif col_number == 6:
                        c.drawRightString(548, y, text)
                    continue
                if col_number == 5 and text in {"Estimate", "SGD", INCLUDED_AMOUNT_TEXT}:
                    c.drawCentredString(x, y, text)
                    continue
                if col_number == 5 and isinstance(value, str):
                    c.drawString(438, y, text[:35])
                    continue
                if page_number == 3 and col_number == 5 and row_number >= 121:
                    c.drawString(345, y, text)
                    continue
                if col_number == 5:
                    c.drawCentredString(x, y, text)
                elif col_number == 6:
                    c.drawRightString(x, y, text)
                else:
                    c.drawString(x, y, text[:78])
        c.setFont("Helvetica", 8)
        c.drawCentredString(width / 2, 24, f"Page {page_number} of {len(page_ranges)}")
        c.showPage()
    c.save()
    return path.exists()


def write_match_csv(path: Path, lines: list[QuoteLine]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["status", "section", "description", "keyword", "pricing_id", "catalog_description", "quantity", "unit_price", "amount", "diagnostic_row_id", "source_basis_line_id"])
        for line in lines:
            match = line.matched_price
            writer.writerow([spreadsheet_safe_text(value) for value in [
                line.match_status,
                line.section,
                line.description,
                line.pricing_keyword,
                match.pricing_id if match else "",
                match.description if match else "",
                quantity_text(line),
                f"{line.unit_price_override:.2f}" if line.unit_price_override is not None else (f"{match.sale_unit_price:.2f}" if match else ""),
                money(amount_value(line)),
                line.diagnostic_row_id,
                line.source_basis_line_id,
            ]])


def write_export_status(path: Path, status: str, pdf_mode: str) -> None:
    if status == "skipped":
        readiness = "not_generated"
    else:
        readiness = "customer_ready" if status in EXPORT_STATUS_CUSTOMER_READY else "review_only"
    path.write_text(
        "\n".join([
            f"pdf_status={status}",
            f"pdf_readiness={readiness}",
            f"pdf_mode={pdf_mode}",
        ])
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    brief = load_brief(args.brief)
    out_dir = resolve_default_output_dir(brief, args.out)
    missing = validate_brief(brief)
    trusted_pricing_reference = brief.get("_pricing_reference_authority")
    price_rows = extract_price_rows(args.template, trusted_reference=trusted_pricing_reference)
    lines = prepare_lines(
        brief,
        price_rows,
        args.allow_ambiguous,
        authority_required=brief.get("_pricing_authority_enforced") is True,
        trusted_pricing_reference=trusted_pricing_reference,
    )
    issues = confirmation_issues(missing, lines)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_match_csv(out_dir / "pricing_matches.csv", lines)
    if issues:
        print_missing_confirmation(issues)
        return 2
    rows = build_quote_rows(brief, lines)
    xlsx_path = out_dir / "quotation.xlsx"
    pdf_path = out_dir / "quotation.pdf"
    if args.quote_template:
        write_template_quote_xlsx(
            quote_template.parse_quote_template_json(args.quote_template.read_bytes()), xlsx_path, brief, lines,
        )
    elif args.layout_template.exists():
        write_quote_layout_xlsx(args.layout_template, xlsx_path, brief, lines)
    else:
        print(f"Quotation layout template not found, writing minimal XLSX fallback: {args.layout_template}")
        write_minimal_xlsx(xlsx_path, rows)
    pdf_status = "skipped"
    if pdf_path.exists():
        pdf_path.unlink()
    if args.pdf_mode == "workbook":
        pdf_status = export_layout_pdf(xlsx_path, pdf_path) or "workbook_export_unavailable"
        if pdf_status == "workbook_export_unavailable":
            print("Workbook PDF export unavailable; no fallback PDF was written.")
    elif args.pdf_mode == "auto":
        pdf_status = export_layout_pdf(xlsx_path, pdf_path) or ""
        if not pdf_status:
            print("Excel/LibreOffice PDF export unavailable, writing styled PDF fallback.")
            if not write_styled_pdf_fallback(pdf_path, brief, lines, args.layout_template):
                print("Styled PDF fallback unavailable, writing text PDF fallback.")
                write_text_pdf(pdf_path, f"Quotation - {brief['project']['title']}", build_pdf_lines(rows))
            pdf_status = "fallback_review_only"
    elif args.pdf_mode == "styled":
        if not write_styled_pdf_fallback(pdf_path, brief, lines, args.layout_template):
            print("Styled PDF fallback unavailable, writing text PDF fallback.")
            write_text_pdf(pdf_path, f"Quotation - {brief['project']['title']}", build_pdf_lines(rows))
        pdf_status = "fallback_review_only"
    elif args.pdf_mode == "text":
        write_text_pdf(pdf_path, f"Quotation - {brief['project']['title']}", build_pdf_lines(rows))
        pdf_status = "fallback_review_only"
    write_export_status(out_dir / "export_status.txt", pdf_status, args.pdf_mode)
    print(f"Wrote {out_dir / 'quotation.xlsx'}")
    if args.pdf_mode != "none" and pdf_path.exists():
        print(f"Wrote {out_dir / 'quotation.pdf'}")
    print(f"Wrote {out_dir / 'pricing_matches.csv'}")
    print(f"PDF export status: {pdf_status}")
    if args.pdf_mode == "workbook" and pdf_status == "workbook_export_unavailable":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
