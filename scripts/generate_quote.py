#!/usr/bin/env python3
"""Generate quotation XLSX files with optional PDF export.

This script intentionally uses Python standard library only. It reads the
structured pricing catalog by default and fills a preserved quote
layout workbook through ZIP/XML updates so the customer-facing XLSX keeps the
same styling, print setup, drawings, and pagination rules as the reference
quotation.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import csv
import datetime as dt
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import textwrap
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def discovered_default_resource_dir(root: Path, marker_filename: str, fallback: str = "default") -> Path:
    try:
        candidates = [
            path
            for path in sorted(root.iterdir(), key=lambda item: item.name.casefold())
            if path.is_dir() and RESOURCE_ID_RE.fullmatch(path.name) and (path / marker_filename).is_file()
        ]
    except OSError:
        candidates = []
    return candidates[0] if candidates else root / fallback


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
FIRST_PRINT_PAGE_END_ROW = 64
CONTINUATION_PAGE_START_ROW = 65
CONTINUATION_PAGE_HEIGHT = 61
CONTINUATION_TABLE_HEADER_OFFSET = 2
CONTINUATION_CURRENCY_OFFSET = 3
CONTINUATION_BODY_OFFSET = 5
TOTAL_BLOCK_HEIGHT = 3
SIGNATURE_CONTENT_HEIGHT = 8
SIGNATURE_BLOCK_PAGE_GUARD_ROWS = 0
SIGNATURE_BLOCK_HEIGHT = SIGNATURE_CONTENT_HEIGHT + SIGNATURE_BLOCK_PAGE_GUARD_ROWS
QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT = "18.7"
QUOTE_LAYOUT_COLUMN_WIDTHS = {
    1: 6.125,
    2: 14.25,
    3: 45.5,
    4: 22.0,
    5: 15.5,
    6: 7.625,
    7: 15.0,
    8: 16.375,
    9: 26.875,
}
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

    @property
    def sale_unit_price(self) -> float:
        return round(self.cost * self.markup, 2)


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


@dataclass(frozen=True)
class LayoutChunk:
    name: str
    height: int


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
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_unit(unit: Any) -> str:
    text = clean_text(unit)
    lower = re.sub(r"\s+", " ", text.lower()).strip(". ")
    if lower in {"m2", "m^2", "sq m", "sq.m", "sq.m.", "square metre", "square meter", "square metres", "square meters"}:
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


def extract_price_rows_from_catalog(template_path: Path) -> list[PriceRow]:
    payload = json.loads(template_path.read_text(encoding="utf-8-sig"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("items"), list):
        raise ValueError(f"Unsupported pricing catalog schema: {template_path}")

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
            )
        )
    return price_rows


def extract_price_rows(template_path: Path) -> list[PriceRow]:
    if template_path.suffix.lower() == ".json":
        return extract_price_rows_from_catalog(template_path)
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


def prepare_lines(brief: dict[str, Any], price_rows: list[PriceRow], allow_ambiguous: bool) -> list[QuoteLine]:
    prepared: list[QuoteLine] = []
    for item in brief.get("line_items", []):
        display_price = str(item.get("display_price") or "")
        price_mode = clean_text(item.get("price_mode")).title()
        if price_mode not in {"Priced", "Included"}:
            price_mode = "Included" if display_price.lower() == "included" else "Priced"
        unit_price_override = item.get("unit_price_override")
        unit_price_override_num = as_float(unit_price_override, 0.0) if unit_price_override not in (None, "") else None
        pricing_keyword = clean_text(item.get("pricing_keyword"))
        query = pricing_keyword or clean_text(item.get("description") or "")
        status, match, candidates = find_price_match(
            query,
            price_rows,
            section=clean_text(item.get("section")),
            unit=clean_text(item.get("unit")),
        )
        exact_catalog_id_match = pricing_keyword_exactly_matches_catalog_id(pricing_keyword, match)
        quantity = item.get("quantity")
        quantity_num = as_float(quantity, 0.0) if quantity not in (None, "") else None
        normalized_unit = normalize_unit(item.get("unit"))
        amount: float | None = None
        if price_mode == "Included":
            status = "included"
            amount = 0.0
            display_price = "Included"
            match = None
        elif unit_price_override_num is not None:
            status = "manual-price"
            amount = round((quantity_num or 0.0) * unit_price_override_num, 2)
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
            amount = round((quantity_num or 0.0) * (match.sale_unit_price if match else 0.0), 2)
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
            )
        )
    return prepared


def confirmation_issues(missing: list[str], lines: list[QuoteLine]) -> list[str]:
    issues = [f"Missing required field: {field}" for field in missing]
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
    return round(amount * exchange_rate, 2)


def quote_subtotal(entries: list[dict[str, Any]]) -> float:
    total = 0.0
    for entry in entries:
        amount = entry.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool):
            total += float(amount)
    return total


def quantity_text(line: QuoteLine) -> str:
    if line.quantity is None:
        return ""
    if float(line.quantity).is_integer():
        qty = str(int(line.quantity))
    else:
        qty = str(line.quantity)
    return f"{qty} {line.unit}".strip()


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
    discount = as_float(brief.get("discount"), 0.0)
    subtotal = max(quote_subtotal(entries) - discount, 0.0)
    tax_rate = quote_tax_rate(brief)
    tax_amount = round(subtotal * tax_rate, 2)
    final_total = subtotal + tax_amount
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
) -> None:
    run = ET.SubElement(inline, f"{NS_MAIN}r")
    run_props = ET.SubElement(run, f"{NS_MAIN}rPr")
    if font_name:
        ET.SubElement(run_props, f"{NS_MAIN}rFont", {"val": font_name})
        ET.SubElement(run_props, f"{NS_MAIN}family", {"val": "2"})
    ET.SubElement(run_props, f"{NS_MAIN}b").attrib["val"] = "1" if bold else "0"
    if italic:
        ET.SubElement(run_props, f"{NS_MAIN}i")
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


def set_ooxml_column_width(root: ET.Element, col_number: int, width: float) -> None:
    cols = root.find(f"{NS_MAIN}cols")
    if cols is None:
        cols = ET.Element(f"{NS_MAIN}cols")
        root.insert(0, cols)

    for col in cols.findall(f"{NS_MAIN}col"):
        min_col = int(col.attrib.get("min", "0"))
        max_col = int(col.attrib.get("max", "0"))
        if min_col == col_number and max_col == col_number:
            col.attrib["width"] = str(width)
            col.attrib["customWidth"] = "1"
            return

    cols.append(
        ET.Element(
            f"{NS_MAIN}col",
            {
                "min": str(col_number),
                "max": str(col_number),
                "width": str(width),
                "customWidth": "1",
            },
        )
    )


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

    sheet_format = ensure_worksheet_child(root, "sheetFormatPr")
    sheet_format.attrib.setdefault("defaultColWidth", "9.125")
    if sheet_format.attrib.get("defaultRowHeight") in {None, "", "15"}:
        sheet_format.attrib["defaultRowHeight"] = "17"
    sheet_format.attrib.setdefault(f"{{{XMLNS_X14AC}}}dyDescent", "0.2")

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


def ensure_quote_layout_row_heights(root: ET.Element, last_row: int) -> None:
    sheet_data = root.find(f"{NS_MAIN}sheetData")
    if sheet_data is None:
        return
    for row_number in range(1, last_row + 1):
        row = get_or_create_row(sheet_data, row_number)
        if row.attrib.get("ht") in {None, "", "15"}:
            row.attrib["ht"] = QUOTE_LAYOUT_DEFAULT_ROW_HEIGHT
        row.attrib.setdefault("customHeight", "1")


def complete_quote_layout_worksheet(root: ET.Element, last_row: int) -> None:
    ensure_quote_layout_page_settings(root)
    for col_number, width in QUOTE_LAYOUT_COLUMN_WIDTHS.items():
        set_ooxml_column_width(root, col_number, width)
    ensure_quote_layout_row_heights(root, last_row)
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
    if horizontal is not None or vertical is not None:
        alignment = style.find(f"{NS_MAIN}alignment")
        if alignment is None:
            alignment = ET.SubElement(style, f"{NS_MAIN}alignment")
        if horizontal is not None:
            alignment.attrib["horizontal"] = horizontal
        if vertical is not None:
            alignment.attrib["vertical"] = vertical
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
) -> str:
    cell_xfs = styles_root.find(f"{NS_MAIN}cellXfs")
    fonts = styles_root.find(f"{NS_MAIN}fonts")
    if cell_xfs is None or fonts is None:
        raise ValueError("Layout workbook is missing cellXfs or fonts styles.")

    base_font_id = int(cell_xfs[int(base_style)].attrib.get("fontId", "0"))
    font = copy.deepcopy(fonts[base_font_id])
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


def normalize_arial_style_fonts(styles_root: ET.Element) -> None:
    fonts = styles_root.find(f"{NS_MAIN}fonts")
    if fonts is None:
        raise ValueError("Layout workbook is missing fonts styles.")

    for font in fonts.findall(f"{NS_MAIN}font"):
        name = font.find(f"{NS_MAIN}name")
        if name is None or name.attrib.get("val", "").lower() != "arial":
            continue
        name.attrib["val"] = "Calibri"
        size = font.find(f"{NS_MAIN}sz")
        if size is None:
            size = ET.SubElement(font, f"{NS_MAIN}sz")
        size.attrib["val"] = "13"


def add_quote_layout_styles(parts: dict[str, bytes]) -> dict[str, str]:
    styles_root = ET.fromstring(parts["xl/styles.xml"])
    normalize_arial_style_fonts(styles_root)
    total_border = append_border(styles_root, top="thin")
    grand_border = append_border(styles_root, top="thin", bottom="double")
    regular_amount_font = ensure_regular_font_for_style(styles_root, "5")
    bold_amount_font = ensure_regular_font_for_style(styles_root, "5", bold=True)
    small_heading_font = ensure_font_for_style(styles_root, "37", font_name="Calibri", font_size="10")
    small_number_font = ensure_font_for_style(styles_root, "40", font_name="Calibri", font_size="10")
    small_body_font = ensure_font_for_style(styles_root, "41", font_name="Calibri", font_size="10")
    signature_text_font = ensure_font_for_style(styles_root, "2", font_name="Calibri", font_size="10")
    signature_line_font = ensure_font_for_style(styles_root, "33", font_name="Calibri", font_size="10")
    client_name_font = ensure_font_for_style(styles_root, "12", font_name="Calibri", font_size="13")
    client_address_font = ensure_font_for_style(styles_root, "93", font_name="Calibri", font_size="13")
    client_attention_font = ensure_font_for_style(styles_root, "26", font_name="Calibri", font_size="13")
    client_title_font = ensure_font_for_style(styles_root, "24", font_name="Calibri", font_size="13")
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
        "terms_heading": clone_cell_style(styles_root, "37", font_id=small_heading_font),
        "terms_number": clone_cell_style(styles_root, "40", font_id=small_number_font),
        "terms_body": clone_cell_style(styles_root, "41", font_id=small_body_font),
        "signature_text": clone_cell_style(styles_root, "2", font_id=signature_text_font),
        "signature_line": clone_cell_style(styles_root, "33", font_id=signature_line_font),
    }
    parts["xl/styles.xml"] = serialize_excel_styles(styles_root)
    return style_ids


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
    logo_anchor = find_header_logo_anchor(root, {})
    if text_anchor is None:
        return xml

    def update_marker(anchor: ET.Element, marker: str, values: dict[str, str]) -> None:
        marker_node = anchor.find(f"{NS_DRAWING}{marker}")
        if marker_node is None:
            return
        for tag, value in values.items():
            node = marker_node.find(f"{NS_DRAWING}{tag}")
            if node is not None:
                node.text = value

    if logo_anchor is not None:
        update_marker(
            logo_anchor,
            "from",
            {"col": "7", "colOff": "0", "row": "1", "rowOff": "0"},
        )
        update_marker(
            logo_anchor,
            "to",
            {"col": "8", "colOff": "1720000", "row": "2", "rowOff": "415000"},
        )
        pic = logo_anchor.find(f"{NS_DRAWING}pic")
        pic_pr = pic.find(f"{NS_DRAWING}spPr") if pic is not None else None
        pic_xfrm = pic_pr.find(f"{NS_A}xfrm") if pic_pr is not None else None
        logo_off = pic_xfrm.find(f"{NS_A}off") if pic_xfrm is not None else None
        if logo_off is not None:
            logo_off.attrib["x"] = "4550000"
            logo_off.attrib["y"] = "260000"
        logo_ext = pic_xfrm.find(f"{NS_A}ext") if pic_xfrm is not None else None
        if logo_ext is not None:
            logo_ext.attrib["cx"] = "2970000"
            logo_ext.attrib["cy"] = "635000"

    update_marker(text_anchor, "from", {"col": "7", "colOff": "0", "row": "3", "rowOff": "90000"})
    update_marker(text_anchor, "to", {"col": "9", "colOff": "200000", "row": "13", "rowOff": "90000"})

    sp = text_anchor.find(f"{NS_DRAWING}sp")
    tx_body = sp.find(f"{NS_DRAWING}txBody") if sp is not None else None
    if tx_body is None:
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    body_pr = tx_body.find(f"{NS_A}bodyPr")
    if body_pr is not None:
        body_pr.attrib["vertOverflow"] = "overflow"
        body_pr.attrib["wrap"] = "square"
        body_pr.attrib["anchor"] = "t"
        body_pr.attrib["anchorCtr"] = "0"
        body_pr.attrib["lIns"] = "0"
        body_pr.attrib["rIns"] = "0"
        body_pr.attrib["tIns"] = "0"
        body_pr.attrib["bIns"] = "0"

    for child in list(tx_body):
        if child.tag == f"{NS_A}p":
            tx_body.remove(child)

    source_runs = header_runs or plain_rich_text_lines([clean_text(line) if line is not None else "" for line in (header_lines or [])])
    line_runs = [list(runs) for runs in source_runs]
    if project_number:
        line_runs.extend([[], [RichTextRun(f"Project No: {project_number}")]])

    for runs in line_runs:
        paragraph = ET.SubElement(tx_body, f"{NS_A}p")
        paragraph_props = ET.SubElement(paragraph, f"{NS_A}pPr")
        paragraph_props.attrib["algn"] = "l"
        if not runs:
            append_drawing_text_run(paragraph, RichTextRun(""))
            continue
        for run in runs:
            append_drawing_text_run(paragraph, run)

    sp_pr = sp.find(f"{NS_DRAWING}spPr") if sp is not None else None
    xfrm = sp_pr.find(f"{NS_A}xfrm") if sp_pr is not None else None
    off = xfrm.find(f"{NS_A}off") if xfrm is not None else None
    if off is not None:
        off.attrib["x"] = "4550000"
        off.attrib["y"] = "950000"
    ext = xfrm.find(f"{NS_A}ext") if xfrm is not None else None
    if ext is not None:
        ext.attrib["cx"] = "3350000"
        ext.attrib["cy"] = "3300000"

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


def create_header_logo_anchor(rel_id: str) -> ET.Element:
    anchor = ET.Element(f"{NS_DRAWING}twoCellAnchor")
    from_marker = ET.SubElement(anchor, f"{NS_DRAWING}from")
    for tag, value in (("col", "7"), ("colOff", "0"), ("row", "1"), ("rowOff", "0")):
        ET.SubElement(from_marker, f"{NS_DRAWING}{tag}").text = value
    to_marker = ET.SubElement(anchor, f"{NS_DRAWING}to")
    for tag, value in (("col", "8"), ("colOff", "1720000"), ("row", "2"), ("rowOff", "415000")):
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
    ET.SubElement(xfrm, f"{NS_A}off", {"x": "4550000", "y": "260000"})
    ET.SubElement(xfrm, f"{NS_A}ext", {"cx": "2970000", "cy": "635000"})
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


def ensure_header_logo_anchor(parts: dict[str, bytes], rel_id: str) -> None:
    drawing_name = "xl/drawings/drawing1.xml"
    if drawing_name not in parts:
        return
    drawing_root = ET.fromstring(parts[drawing_name])
    rel_targets = rel_targets_by_id(parts)
    anchor = find_header_logo_anchor(drawing_root, rel_targets)
    if anchor is not None:
        pic = anchor.find(f"{NS_DRAWING}pic")
        blip = pic.find(f".//{NS_A}blip")
        if blip is not None:
            blip.attrib[f"{NS_REL}embed"] = rel_id
        parts[drawing_name] = ET.tostring(drawing_root, encoding="utf-8", xml_declaration=True)
        return
    drawing_root.insert(0, create_header_logo_anchor(rel_id))
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
    ensure_header_logo_anchor(parts, logo_rel_id)

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
    text = xml.decode("utf-8")
    updated = re.sub(
        r"(<definedName[^>]*name=\"_xlnm\.Print_Titles\"[^>]*>[^<]*!\$1:\$)[0-9]+(</definedName>)",
        r"\g<1>3\2",
        text,
        count=1,
    )
    return updated.encode("utf-8")


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


def amount_value(line: QuoteLine, exchange_rate: float = 1.0) -> str | float | None:
    if line.price_mode == "Included":
        return 0.0
    if line.display_price:
        display_amount = quote_amount(line.display_price, exchange_rate)
        return display_amount if display_amount is not None else line.display_price
    return quote_amount(line.amount, exchange_rate)


def line_amount_value(line: QuoteLine, exchange_rate: float = 1.0) -> float | None:
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
    if row_number <= FIRST_PRINT_PAGE_END_ROW:
        return None
    return CONTINUATION_PAGE_START_ROW + (
        (row_number - CONTINUATION_PAGE_START_ROW) // CONTINUATION_PAGE_HEIGHT
    ) * CONTINUATION_PAGE_HEIGHT


def manual_page_end_for_row(row_number: int) -> int:
    page_start = continuation_page_start_for_row(row_number)
    if page_start is None:
        return FIRST_PRINT_PAGE_END_ROW
    return page_start + CONTINUATION_PAGE_HEIGHT - 1


def next_continuation_page_start(row_number: int) -> int:
    page_start = continuation_page_start_for_row(row_number)
    if page_start is None:
        return CONTINUATION_PAGE_START_ROW
    return page_start + CONTINUATION_PAGE_HEIGHT


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
    return max(1, len(entry["description_lines"]))


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


def summary_block_start_row(row_number: int, block_height: int) -> int:
    if row_number + block_height - 1 <= manual_page_end_for_row(row_number):
        return row_number
    page_start = next_continuation_page_start(row_number)
    return page_start + CONTINUATION_BODY_OFFSET


def layout_chunk_start_row(row_number: int, chunk: LayoutChunk) -> tuple[int, bool]:
    if chunk.height <= 0 or row_number + chunk.height - 1 <= manual_page_end_for_row(row_number):
        return row_number, False
    return next_continuation_page_start(row_number) + CONTINUATION_BODY_OFFSET, True


def manual_page_break_ids(last_row: int) -> list[int]:
    if last_row <= FIRST_PRINT_PAGE_END_ROW:
        return []
    break_ids = [FIRST_PRINT_PAGE_END_ROW]
    next_break = CONTINUATION_PAGE_START_ROW + CONTINUATION_PAGE_HEIGHT - 1
    while next_break < last_row:
        break_ids.append(next_break)
        next_break += CONTINUATION_PAGE_HEIGHT
    return break_ids


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


def printable_last_row(root: ET.Element, last_row: int, manual_pagination_enabled: bool) -> int:
    if not manual_pagination_enabled:
        return last_row
    payload_row = last_payload_row(root, last_row)
    if not payload_row:
        return last_row
    break_ids = manual_page_break_ids(last_row)
    if break_ids and break_ids[-1] >= payload_row:
        return payload_row
    return last_row


def set_manual_page_breaks(root: ET.Element, last_row: int, enabled: bool) -> None:
    row_breaks = root.find(f"{NS_MAIN}rowBreaks")
    if row_breaks is not None:
        root.remove(row_breaks)
    if not enabled:
        return

    break_ids = manual_page_break_ids(last_row)
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


def render_quote_entries(lines: list[QuoteLine], brief: dict[str, Any] | None = None) -> list[dict[str, Any]]:
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
        detail_amount = amount_value(line, exchange_rate)
        if active_section_entry and active_section_entry.get("section_grouped"):
            active_section_entry["amount"] = round(float(active_section_entry["amount"] or 0.0) + float(line_amount_value(line, exchange_rate) or 0.0), 2)
            detail_amount = None
        entries.append({
            "kind": "item",
            "number": f"{section_number}.{detail_number}",
            "quantity": quantity_text(line),
            "description_lines": wrapped_description(customer_quote_description(line.description)),
            "amount": detail_amount,
        })
    for index, entry in enumerate(entries):
        if entry["kind"] != "section" or not entry.get("section_grouped"):
            continue
        covered_numbers = []
        for follower in entries[index + 1:]:
            if follower["kind"] == "section":
                break
            covered_numbers.append(follower["number"])
        if covered_numbers:
            first, last = covered_numbers[0], covered_numbers[-1]
            entry["coverage"] = f"Covers line items {first} to {last}" if first != last else f"Covers line item {first}"
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
    clear_ooxml_range(root, 1, 1000, 1, 100)
    set_ooxml_column_width(root, 2, 14.25)
    set_ooxml_column_width(root, 3, 45.5)
    set_ooxml_column_width(root, 4, 22.0)
    price_style = layout_styles["price_amount"]

    client = brief["client"]
    project = brief["project"]
    currency = brief.get("currency", "SGD")
    company = brief.get("company") if isinstance(brief.get("company"), dict) else {}
    company_name = clean_text(company.get("name"))
    client_address_runs = brief_rich_text_lines(brief, "clientAddress", client.get("address") or [])
    header_line_runs = brief_rich_text_lines(brief, "headerDetails", company.get("header_lines") or [])
    client_block_rich_text = {"font_name": "Calibri", "font_size": "13"}

    set_ooxml_rich_text_cell(root, 6, 1, brief_rich_text_cell_runs(brief, "clientName", client.get("name", "")), layout_styles["client_name"], **client_block_rich_text)
    for offset, runs in enumerate(client_address_runs[:4], start=7):
        set_ooxml_rich_text_cell(root, offset, 1, runs, layout_styles["client_address"], **client_block_rich_text)
    set_ooxml_rich_text_cell(root, 11, 1, [RichTextRun("Attention:", bold=True)], layout_styles["client_attention"], **client_block_rich_text)
    set_ooxml_rich_text_cell(root, 12, 2, brief_rich_text_cell_runs(brief, "clientAttention", client.get("attention", ""), fallback_bold=True), layout_styles["client_attention"], **client_block_rich_text)
    set_ooxml_rich_text_cell(root, 13, 2, brief_rich_text_cell_runs(brief, "clientTitle", client.get("title", "")), layout_styles["client_title"], **client_block_rich_text)
    quote_date_runs = brief_quote_date_rich_text_runs(brief)
    if quote_date_runs:
        set_ooxml_rich_text_cell(root, 16, 1, quote_date_runs, layout_styles["quote_date"])
    else:
        set_ooxml_cell(root, 16, 1, excel_date_serial(str(brief.get("quote_date", ""))), layout_styles["quote_date"])
    set_ooxml_rich_text_cell(root, 18, 1, brief_rich_text_cell_runs(brief, "projectTitle", project.get("title", "")), "26")

    write_table_header(root, 20, 21, layout_styles)
    set_ooxml_cell(root, 21, 5, currency, layout_styles["header_currency"])

    entries = render_quote_entries(lines, brief)
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
        set_ooxml_cell(root, row_number, 1, entry["number"], "70")
        set_ooxml_cell(root, row_number, 2, entry["quantity"], "13")
        set_ooxml_cell(root, row_number, 3, description_lines[0], "5")
        set_ooxml_cell(root, row_number, 5, entry["amount"], price_style)
        for extra in description_lines[1:]:
            row_number += 1
            set_ooxml_cell(root, row_number, 3, extra, "5")
        row_number += 2

    total_candidate_row = row_number + 1
    total_row = summary_block_start_row(total_candidate_row, TOTAL_BLOCK_HEIGHT)
    manual_pagination_enabled = bool(continuation_pages) or total_row != total_candidate_row
    gst_row = total_row + 1
    grand_row = total_row + 2
    tax_rate = quote_tax_rate(brief)
    cached_total = sum(formula_cache_amount(entry.get("amount")) for entry in entries)
    cached_tax = round(cached_total * tax_rate, 2)
    cached_grand = cached_total + cached_tax
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
    next_text_row = grand_row + 3
    last_optional_row = 0
    footer_rich_text = {"font_name": "Calibri", "font_size": "10"}

    terms_heading = clean_text(brief.get("terms_heading"))
    payment_terms = brief.get("payment_terms") or []
    payment_term_runs = brief_rich_text_lines(brief, "paymentTerms", payment_terms)
    if terms_heading or payment_terms:
        if terms_heading:
            set_ooxml_rich_text_cell(root, next_text_row, 1, brief_rich_text_cell_runs(brief, "termsHeading", terms_heading), layout_styles["terms_heading"], **footer_rich_text)
            next_text_row += 1
        for index, term in enumerate(payment_terms, start=1):
            set_ooxml_cell(root, next_text_row, 1, f"{index:.2f}", layout_styles["terms_number"])
            runs = payment_term_runs[index - 1] if index - 1 < len(payment_term_runs) else [RichTextRun(term)]
            set_ooxml_rich_text_cell(root, next_text_row, 2, runs, layout_styles["terms_body"], **footer_rich_text)
            next_text_row += 1
        last_optional_row = next_text_row - 1
        next_text_row = last_optional_row + 2

    notes_heading = clean_text(brief.get("notes_heading"))
    standard_notes = brief.get("standard_notes") or []
    standard_note_runs = brief_rich_text_lines(brief, "standardNotes", standard_notes)
    if notes_heading or standard_notes:
        if notes_heading:
            set_ooxml_rich_text_cell(root, next_text_row, 1, brief_rich_text_cell_runs(brief, "notesHeading", notes_heading), layout_styles["terms_heading"], **footer_rich_text)
            next_text_row += 1
        for index, note in enumerate(standard_notes, start=1):
            set_ooxml_cell(root, next_text_row, 1, f"{index:.2f}", layout_styles["terms_number"])
            runs = standard_note_runs[index - 1] if index - 1 < len(standard_note_runs) else [RichTextRun(note)]
            set_ooxml_rich_text_cell(root, next_text_row, 2, runs, layout_styles["terms_body"], **footer_rich_text)
            next_text_row += 1
        last_optional_row = next_text_row - 1

    acceptance_candidate_row = last_optional_row + 3 if last_optional_row else next_text_row
    acceptance_row, acceptance_moved = layout_chunk_start_row(
        acceptance_candidate_row,
        LayoutChunk("acceptance_signature", SIGNATURE_BLOCK_HEIGHT),
    )
    manual_pagination_enabled = manual_pagination_enabled or acceptance_moved
    set_ooxml_rich_text_cell(root, acceptance_row, 2, brief_rich_text_cell_runs(brief, "quoteCompanyName", clean_text(acceptance.get("company_name")) or company_name), layout_styles["signature_text"], **footer_rich_text)
    set_ooxml_rich_text_cell(root, acceptance_row, 5, brief_rich_text_cell_runs(brief, "acceptanceText", clean_text(acceptance.get("text"))), layout_styles["signature_text"], **footer_rich_text)
    set_ooxml_cell(root, acceptance_row + 4, 2, "_____________________________", layout_styles["signature_line"])
    set_ooxml_cell(root, acceptance_row + 4, 5, "_____________________________________", layout_styles["signature_line"])
    set_ooxml_rich_text_cell(root, acceptance_row + 5, 2, brief_rich_text_cell_runs(brief, "companySignatory", clean_text(signature.get("company_signatory"))), layout_styles["signature_line"], **footer_rich_text)
    set_ooxml_rich_text_cell(root, acceptance_row + 5, 5, brief_rich_text_cell_runs(brief, "personLabel", clean_text(acceptance.get("person_label"))), layout_styles["signature_line"], **footer_rich_text)
    set_ooxml_rich_text_cell(root, acceptance_row + 6, 2, brief_rich_text_cell_runs(brief, "companyTitle", clean_text(signature.get("company_title"))), layout_styles["signature_line"], **footer_rich_text)
    set_ooxml_rich_text_cell(root, acceptance_row + 6, 5, brief_rich_text_cell_runs(brief, "stampLabel", clean_text(acceptance.get("stamp_label"))), layout_styles["signature_line"], **footer_rich_text)
    set_ooxml_rich_text_cell(root, acceptance_row + 7, 2, brief_rich_text_cell_runs(brief, "companyDateLabel", clean_text(signature.get("company_date_label"))), layout_styles["signature_line"], **footer_rich_text)
    set_ooxml_rich_text_cell(root, acceptance_row + 7, 5, brief_rich_text_cell_runs(brief, "dateLabel", clean_text(acceptance.get("date_label"))), layout_styles["signature_line"], **footer_rich_text)

    last_content_row = acceptance_row + SIGNATURE_CONTENT_HEIGHT
    manual_pagination_enabled = manual_pagination_enabled or last_content_row > FIRST_PRINT_PAGE_END_ROW
    last_print_row = printable_last_row(root, last_content_row, manual_pagination_enabled)
    trim_layout_worksheet(root, last_print_row)
    complete_quote_layout_worksheet(root, last_print_row)
    set_manual_page_breaks(root, last_print_row, manual_pagination_enabled)
    parts["xl/worksheets/sheet1.xml"] = serialize_excel_worksheet(root)
    if "xl/drawings/drawing1.xml" in parts:
        project_number = clean_text(brief.get("project_number") or project.get("number") or "")
        drawing_xml = update_drawing_project_number(parts["xl/drawings/drawing1.xml"], project_number)
        header_lines = company.get("header_lines") if isinstance(company.get("header_lines"), list) else None
        parts["xl/drawings/drawing1.xml"] = update_repeating_header_drawing(drawing_xml, project_number, header_lines, header_line_runs)
    replace_header_logo(parts, clean_text(company.get("logo_data_url")))
    if "xl/workbook.xml" in parts:
        workbook_xml = update_print_titles(parts["xl/workbook.xml"])
        workbook_xml = update_print_area(workbook_xml, last_print_row)
        parts["xl/workbook.xml"] = enable_workbook_recalculation(workbook_xml)
    strip_stale_workbook_parts(parts)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in parts.items():
            zf.writestr(name, content)


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
if (Test-Path -LiteralPath $repairedWorkbookPath) {{
  Remove-Item -LiteralPath $repairedWorkbookPath -Force
}}
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
if (Test-Path -LiteralPath $repairedWorkbookPath) {{
  Move-Item -LiteralPath $repairedWorkbookPath -Destination $xlsxPath -Force
}}
"""


def powershell_pdf_export(xlsx_path: Path, pdf_path: Path) -> str | None:
    if os.name != "nt" or shutil.which("powershell") is None:
        return None
    script = powershell_export_script(xlsx_path, pdf_path)
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=120,
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


def libreoffice_pdf_export(xlsx_path: Path, pdf_path: Path) -> str | None:
    executables = libreoffice_candidates()
    if not executables:
        return None
    result = subprocess.run(
        [
            executables[0],
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_path.parent),
            str(xlsx_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=120,
    )
    converted = pdf_path.parent / f"{xlsx_path.stem}.pdf"
    if result.returncode == 0 and converted.exists():
        if converted != pdf_path:
            converted.replace(pdf_path)
        return "libreoffice_exported"
    return None


def export_layout_pdf(xlsx_path: Path, pdf_path: Path) -> str | None:
    return libreoffice_pdf_export(xlsx_path, pdf_path) or powershell_pdf_export(xlsx_path, pdf_path)


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
    tax_amount = round(subtotal * tax_rate, 2)
    cells[(92, 5)] = subtotal
    cells[(93, 5)] = tax_amount
    cells[(93, 6)] = currency
    cells[(94, 5)] = subtotal + tax_amount
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
                if col_number == 5 and text in {"Estimate", "SGD"}:
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
        writer.writerow(["status", "section", "description", "keyword", "pricing_id", "catalog_description", "quantity", "unit_price", "amount"])
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
    price_rows = extract_price_rows(args.template)
    lines = prepare_lines(brief, price_rows, args.allow_ambiguous)
    issues = confirmation_issues(missing, lines)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_match_csv(out_dir / "pricing_matches.csv", lines)
    if issues:
        print_missing_confirmation(issues)
        return 2
    rows = build_quote_rows(brief, lines)
    xlsx_path = out_dir / "quotation.xlsx"
    pdf_path = out_dir / "quotation.pdf"
    if args.layout_template.exists():
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
