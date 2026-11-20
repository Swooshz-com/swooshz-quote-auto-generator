#!/usr/bin/env python3
"""Read and check admin-built quote template definitions (sqag.quote-template.v1).

A quote template says where each part of a quotation sits on the Excel grid and how
it looks. This module only reads and checks a definition; it never renders one.
The reader is default-deny: unknown keys, wrong types, out-of-range numbers,
invisible or control characters and missing required blocks are all refused with a
QuoteTemplateError that names the offending path. Standard library only.

The full contract is in docs/quote-template-schema.md.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

SCHEMA_ID = "sqag.quote-template.v1"
MAX_DEFINITION_BYTES = 64 * 1024
MAX_BLOCKS = 30
MAX_FREE_TEXT_BLOCKS = 10
MAX_TABLE_COLUMNS = 8
MIN_GRID_COLUMNS = 4
MAX_GRID_COLUMNS = 12
# Longest number text accepted in definition JSON; every real value is far shorter.
MAX_NUMBER_CHARACTERS = 32
STANDARD_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "quote-template" / "standard.json"

# Fonts installed in the deploy image and checked by CI before PDF rendering
# (Calibri renders as Carlito, Arial as Liberation Sans).
ALLOWED_FONTS = ("Calibri", "Arial")
ALIGNMENTS = ("left", "center", "right")
VERTICAL_ALIGNMENTS = ("top", "middle", "bottom")
HEADER_ONLY_TYPES = ("logo", "company_details", "client_details", "quote_details")
BODY_ONLY_TYPES = ("item_table", "totals", "terms", "notes", "signature", "acceptance")
BLOCK_TYPES = HEADER_ONLY_TYPES + BODY_ONLY_TYPES + ("free_text",)
TABLE_COLUMN_KEYS = ("position", "quantity", "unit", "description", "unit_price", "amount")
COMPANY_FIELDS = ("header_lines", "project_number", "blank")
CLIENT_FIELDS = ("client_name", "client_address", "attention", "client_title", "blank")
QUOTE_FIELDS = ("quote_date", "project_title", "project_number", "blank")
FORMULA_PREFIXES = ("=", "+", "-", "@")
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
# Characters that hide, reorder or break text: every control, format, surrogate,
# private-use and unassigned code point, plus line and paragraph separators.
REFUSED_CHARACTER_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}


class QuoteTemplateError(ValueError):
    """A quote template definition was refused. `path` names where."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _refuse(path: str, message: str) -> None:
    raise QuoteTemplateError(path, message)


def _object(value: Any, path: str, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> dict[str, Any]:
    if not isinstance(value, dict):
        _refuse(path, "must be an object")
    unknown = sorted(key for key in value if key not in required and key not in optional)
    if unknown:
        _refuse(f"{path}.{unknown[0]}", "is not a known key")
    missing = [key for key in required if key not in value]
    if missing:
        _refuse(f"{path}.{missing[0]}", "is required")
    return value


def _integer(value: Any, path: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _refuse(path, "must be a whole number")
    if not low <= value <= high:
        _refuse(path, f"must be between {low} and {high}")
    return value


def _number(value: Any, path: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _refuse(path, "must be a number")
    in_range = low <= value <= high if isinstance(value, int) else math.isfinite(value) and low <= value <= high
    if not in_range:
        _refuse(path, f"must be between {low:g} and {high:g}")
    return float(value)


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        _refuse(path, "must be true or false")
    return value


def _choice(value: Any, path: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        _refuse(path, "must be one of: " + ", ".join(choices))
    return value


def _text(value: Any, path: str, max_length: int, *, allow_empty: bool = True) -> str:
    """Return checked display text; formula-like text is kept as plain text with a leading apostrophe."""
    if not isinstance(value, str):
        _refuse(path, "must be text")
    for character in value:
        if unicodedata.category(character) in REFUSED_CHARACTER_CATEGORIES:
            _refuse(path, f"contains a hidden or control character (U+{ord(character):04X})")
    text = unicodedata.normalize("NFC", value).strip()
    if not text and not allow_empty:
        _refuse(path, "must not be empty")
    if text.startswith(FORMULA_PREFIXES):
        text = "'" + text
    if len(text) > max_length:
        _refuse(path, f"must be at most {max_length} characters")
    return text


def _color(value: Any, path: str) -> str:
    if not isinstance(value, str) or not HEX_COLOR.fullmatch(value):
        _refuse(path, "must be a colour like #1A2B3C")
    return value.upper()


def _list(value: Any, path: str, low: int, high: int) -> list[Any]:
    if not isinstance(value, list):
        _refuse(path, "must be a list")
    if not low <= len(value) <= high:
        _refuse(path, f"must have between {low} and {high} entries")
    return value


def _fields(value: Any, path: str, allowed: tuple[str, ...]) -> list[str]:
    items = _list(value, path, 1, 8)
    result = []
    for index, item in enumerate(items):
        field = _choice(item, f"{path}[{index}]", allowed)
        if field != "blank" and field in result:
            _refuse(f"{path}[{index}]", "is listed twice")
        result.append(field)
    if all(field == "blank" for field in result):
        _refuse(path, "must name at least one field other than blank")
    return result


def _read_page(value: Any) -> dict[str, Any]:
    page = _object(
        value, "page",
        ("size", "orientation", "margins_mm", "row_height_pt", "first_page_rows", "page_rows"),
        ("continuation_top_rows",),
    )
    margins = _object(page["margins_mm"], "page.margins_mm", ("left", "right", "top", "bottom", "header", "footer"))
    result = {
        "size": _choice(page["size"], "page.size", ("A4",)),
        "orientation": _choice(page["orientation"], "page.orientation", ("portrait", "landscape")),
        "margins_mm": {
            key: _number(margins[key], f"page.margins_mm.{key}", *((0, 20) if key in {"header", "footer"} else (5, 40)))
            for key in ("left", "right", "top", "bottom", "header", "footer")
        },
        "row_height_pt": _number(page["row_height_pt"], "page.row_height_pt", 12, 40),
        "first_page_rows": _integer(page["first_page_rows"], "page.first_page_rows", 20, 150),
        "page_rows": _integer(page["page_rows"], "page.page_rows", 20, 150),
        "continuation_top_rows": _integer(page.get("continuation_top_rows", 2), "page.continuation_top_rows", 0, 10),
    }
    for key in ("header", "footer"):
        side = "top" if key == "header" else "bottom"
        if result["margins_mm"][key] > result["margins_mm"][side]:
            _refuse(f"page.margins_mm.{key}", f"must not be larger than the {side} margin")
    return result


def _read_grid(value: Any) -> dict[str, Any]:
    grid = _object(value, "grid", ("columns",))
    columns = []
    for index, column in enumerate(_list(grid["columns"], "grid.columns", MIN_GRID_COLUMNS, MAX_GRID_COLUMNS)):
        path = f"grid.columns[{index}]"
        column = _object(column, path, ("width",), ("hidden",))
        columns.append({
            "width": _number(column["width"], f"{path}.width", 4, 60),
            "hidden": _boolean(column.get("hidden", False), f"{path}.hidden"),
        })
    if all(column["hidden"] for column in columns):
        _refuse("grid.columns", "must keep at least one column visible")
    return {"columns": columns}


def _read_theme(value: Any) -> dict[str, Any]:
    theme = _object(value, "theme", ("font", "size_pt", "color", "heading_color"))
    return {
        "font": _choice(theme["font"], "theme.font", ALLOWED_FONTS),
        "size_pt": _number(theme["size_pt"], "theme.size_pt", 7, 20),
        "color": _color(theme["color"], "theme.color"),
        "heading_color": _color(theme["heading_color"], "theme.heading_color"),
    }


def _read_labels(value: Any) -> dict[str, str]:
    labels = _object(value, "labels", ("total", "attention", "project_number"))
    return {
        "total": _text(labels["total"], "labels.total", 40, allow_empty=False),
        "attention": _text(labels["attention"], "labels.attention", 40),
        "project_number": _text(labels["project_number"], "labels.project_number", 40),
    }


def _read_style(value: Any, path: str) -> dict[str, Any]:
    style = _object(value, path, (), ("font", "size_pt", "bold", "italic", "color", "align"))
    result: dict[str, Any] = {}
    if "font" in style:
        result["font"] = _choice(style["font"], f"{path}.font", ALLOWED_FONTS)
    if "size_pt" in style:
        result["size_pt"] = _number(style["size_pt"], f"{path}.size_pt", 7, 20)
    for key in ("bold", "italic"):
        if key in style:
            result[key] = _boolean(style[key], f"{path}.{key}")
    if "color" in style:
        result["color"] = _color(style["color"], f"{path}.color")
    if "align" in style:
        result["align"] = _choice(style["align"], f"{path}.align", ALIGNMENTS)
    return result


TYPE_KEYS: dict[str, tuple[str, ...]] = {
    "logo": ("logo",),
    "company_details": ("fields",),
    "client_details": ("fields", "address_rows", "indent_cols"),
    "quote_details": ("fields",),
    "item_table": ("columns", "currency_row"),
    "totals": ("label_col", "currency_col"),
    "terms": (),
    "notes": (),
    "signature": ("line_chars",),
    "acceptance": ("line_chars",),
    "free_text": ("heading", "lines"),
}


def _read_block(value: Any, index: int, grid_columns: int, header_rows: int) -> dict[str, Any]:
    path = f"blocks[{index}]"
    if not isinstance(value, dict):
        _refuse(path, "must be an object")
    block_type = _choice(value.get("type"), f"{path}.type", BLOCK_TYPES)
    area = _choice(value.get("area"), f"{path}.area", ("header", "body"))
    if block_type in HEADER_ONLY_TYPES and area != "header":
        _refuse(f"{path}.area", f"a {block_type} block belongs in the header")
    if block_type in BODY_ONLY_TYPES and area != "body":
        _refuse(f"{path}.area", f"a {block_type} block belongs in the body")
    place = ("row", "rows") if area == "header" else ()
    body_optional = ("beside", "gap_before") if area == "body" else ()
    _object(value, path, ("type", "area", "col", "span") + place + TYPE_KEYS[block_type], ("style",) + body_optional)

    col = _integer(value["col"], f"{path}.col", 1, grid_columns)
    span = _integer(value["span"], f"{path}.span", 1, grid_columns)
    if col + span - 1 > grid_columns:
        _refuse(f"{path}.span", f"runs past the last grid column ({grid_columns})")
    block: dict[str, Any] = {"type": block_type, "area": area, "col": col, "span": span}
    if area == "header":
        block["row"] = _integer(value["row"], f"{path}.row", 1, header_rows)
        block["rows"] = _integer(value["rows"], f"{path}.rows", 1, header_rows)
        if block["row"] + block["rows"] - 1 > header_rows:
            _refuse(f"{path}.rows", f"runs past the last header row ({header_rows})")
    else:
        block["beside"] = _boolean(value.get("beside", False), f"{path}.beside")
        block["gap_before"] = _integer(value.get("gap_before", 1), f"{path}.gap_before", 0, 10)
    block["style"] = _read_style(value.get("style", {}), f"{path}.style")

    if block_type == "logo":
        logo = _object(value["logo"], f"{path}.logo", ("width_mm", "height_mm", "align", "valign", "keep_aspect", "repeat"))
        block["logo"] = {
            "width_mm": _number(logo["width_mm"], f"{path}.logo.width_mm", 5, 120),
            "height_mm": _number(logo["height_mm"], f"{path}.logo.height_mm", 5, 80),
            "align": _choice(logo["align"], f"{path}.logo.align", ALIGNMENTS),
            "valign": _choice(logo["valign"], f"{path}.logo.valign", VERTICAL_ALIGNMENTS),
            "keep_aspect": _boolean(logo["keep_aspect"], f"{path}.logo.keep_aspect"),
            "repeat": _boolean(logo["repeat"], f"{path}.logo.repeat"),
        }
    elif block_type == "company_details":
        block["fields"] = _fields(value["fields"], f"{path}.fields", COMPANY_FIELDS)
    elif block_type == "client_details":
        block["fields"] = _fields(value["fields"], f"{path}.fields", CLIENT_FIELDS)
        block["address_rows"] = _integer(value["address_rows"], f"{path}.address_rows", 1, 6)
        block["indent_cols"] = _integer(value["indent_cols"], f"{path}.indent_cols", 0, 3)
        if block["indent_cols"] >= span:
            _refuse(f"{path}.indent_cols", "must be smaller than the block span")
    elif block_type == "quote_details":
        block["fields"] = _fields(value["fields"], f"{path}.fields", QUOTE_FIELDS)
    elif block_type in {"signature", "acceptance"}:
        block["line_chars"] = _integer(value["line_chars"], f"{path}.line_chars", 5, 60)
    if block_type in {"client_details", "quote_details"} and _field_rows(block) > block["rows"]:
        _refuse(f"{path}.rows", f"must be at least {_field_rows(block)} to fit the fields")
    elif block_type == "item_table":
        block["currency_row"] = _boolean(value["currency_row"], f"{path}.currency_row")
        block["columns"] = _read_table_columns(value["columns"], f"{path}.columns", col, span)
    elif block_type == "totals":
        block["label_col"] = _integer(value["label_col"], f"{path}.label_col", col, col + span - 1)
        block["currency_col"] = _integer(value["currency_col"], f"{path}.currency_col", 0, col + span - 1)
        if 0 < block["currency_col"] < col:
            _refuse(f"{path}.currency_col", f"must be 0 (none) or between {col} and {col + span - 1}")
    elif block_type in {"terms", "notes"} and span < 2:
        _refuse(f"{path}.span", "must be at least 2 (number column and text)")
    elif block_type == "free_text":
        block["heading"] = _text(value["heading"], f"{path}.heading", 80)
        lines = _list(value["lines"], f"{path}.lines", 0, 20)
        block["lines"] = [_text(line, f"{path}.lines[{line_index}]", 200) for line_index, line in enumerate(lines)]
        if not block["heading"] and not any(block["lines"]):
            _refuse(path, "free text needs a heading or at least one line")
        if area == "body" and block["lines"] and span < 2:
            _refuse(f"{path}.span", "must be at least 2 when the text has lines under a heading")
        needed = bool(block["heading"]) + len(block["lines"])
        if area == "header" and needed > block["rows"]:
            _refuse(f"{path}.rows", f"must be at least {needed} to fit the heading and lines")
    return block


def _field_rows(block: dict[str, Any]) -> int:
    """Rows a cell-based header block needs: the address takes address_rows, attention a label and a name row."""
    sizes = {"client_address": block.get("address_rows", 1), "attention": 2}
    return sum(sizes.get(field, 1) for field in block["fields"])


def _read_table_columns(value: Any, path: str, block_col: int, block_span: int) -> list[dict[str, Any]]:
    columns = []
    used: set[int] = set()
    for index, column in enumerate(_list(value, path, 2, MAX_TABLE_COLUMNS)):
        column_path = f"{path}[{index}]"
        column = _object(column, column_path, ("key", "label", "col", "span", "align"))
        key = _choice(column["key"], f"{column_path}.key", TABLE_COLUMN_KEYS)
        if any(existing["key"] == key for existing in columns):
            _refuse(f"{column_path}.key", "is listed twice")
        last = block_col + block_span - 1
        col = _integer(column["col"], f"{column_path}.col", block_col, last)
        span = _integer(column["span"], f"{column_path}.span", 1, block_span)
        if col + span - 1 > last:
            _refuse(f"{column_path}.span", "runs past the item table")
        cells = set(range(col, col + span))
        if cells & used:
            _refuse(f"{column_path}.col", "overlaps another table column")
        used |= cells
        columns.append({
            "key": key,
            "label": _text(column["label"], f"{column_path}.label", 40),
            "col": col,
            "span": span,
            "align": _choice(column["align"], f"{column_path}.align", ALIGNMENTS),
        })
    keys = {column["key"] for column in columns}
    for required in ("description", "amount"):
        if required not in keys:
            _refuse(path, f"the item table needs a {required} column")
    return columns


def _check_layout(blocks: list[dict[str, Any]], grid: dict[str, Any]) -> None:
    counts: dict[str, int] = {}
    for block in blocks:
        counts[block["type"]] = counts.get(block["type"], 0) + 1
    for block_type in BLOCK_TYPES:
        limit = MAX_FREE_TEXT_BLOCKS if block_type == "free_text" else 1
        if counts.get(block_type, 0) > limit:
            _refuse("blocks", f"may have at most {limit} {block_type} block(s)")
    for required in ("item_table", "totals"):
        if not counts.get(required):
            _refuse("blocks", f"needs a {required} block")

    header = [(index, block) for index, block in enumerate(blocks) if block["area"] == "header"]
    for position, (index, block) in enumerate(header):
        for other_index, other in header[:position]:
            rows_overlap = block["row"] <= other["row"] + other["rows"] - 1 and other["row"] <= block["row"] + block["rows"] - 1
            cols_overlap = block["col"] <= other["col"] + other["span"] - 1 and other["col"] <= block["col"] + block["span"] - 1
            if rows_overlap and cols_overlap:
                _refuse(f"blocks[{index}]", f"overlaps blocks[{other_index}] in the header")

    body = [(index, block) for index, block in enumerate(blocks) if block["area"] == "body"]
    standalone = {"item_table", "totals"}
    band: list[tuple[int, dict[str, Any]]] = []
    for position, (index, block) in enumerate(body):
        if block["beside"]:
            if position == 0:
                _refuse(f"blocks[{index}].beside", "the first body block has nothing to sit beside")
            if block["type"] in standalone or band[0][1]["type"] in standalone:
                _refuse(f"blocks[{index}].beside", "item tables and totals sit on their own")
            for other_index, other in band:
                if block["col"] <= other["col"] + other["span"] - 1 and other["col"] <= block["col"] + block["span"] - 1:
                    _refuse(f"blocks[{index}]", f"overlaps blocks[{other_index}] beside it")
            band.append((index, block))
        else:
            band = [(index, block)]
    order = [block["type"] for _, block in body]
    if order.index("totals") < order.index("item_table"):
        _refuse("blocks", "the totals block must come after the item table")
    table = next(block for block in blocks if block["type"] == "item_table")
    totals = next(block for block in blocks if block["type"] == "totals")
    amount = next(column for column in table["columns"] if column["key"] == "amount")
    amount_cols = set(range(amount["col"], amount["col"] + amount["span"]))
    if not totals["col"] <= amount["col"] <= totals["col"] + totals["span"] - 1:
        _refuse("blocks", "the totals block must cover the item table's amount column")
    if amount_cols & {totals["label_col"], totals["currency_col"]}:
        _refuse("blocks", "totals labels and currency need their own columns, not the amount column")
    if totals["label_col"] == totals["currency_col"]:
        _refuse("blocks", "totals labels and currency need separate columns")
    hidden = {index + 1 for index, column in enumerate(grid["columns"]) if column["hidden"]}
    for index, block in enumerate(blocks):
        if set(range(block["col"], block["col"] + block["span"])) <= hidden:
            _refuse(f"blocks[{index}]", "sits only on hidden grid columns")
    for column in table["columns"]:
        if column["col"] in hidden:
            _refuse("blocks", f"the {column['key']} column starts on a hidden grid column")
    if totals["label_col"] in hidden or totals["currency_col"] in hidden:
        _refuse("blocks", "totals labels and currency must be on visible grid columns")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _refuse("definition", f"repeats the key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> Any:
    _refuse("definition", f"uses {name}, which is not a number")


def _parse_int(text: str) -> int:
    if len(text) > MAX_NUMBER_CHARACTERS:
        _refuse("definition", "has a number with too many digits")
    return int(text)


def _parse_float(text: str) -> float:
    value = float(text) if len(text) <= MAX_NUMBER_CHARACTERS else math.inf
    if not math.isfinite(value):
        _refuse("definition", "has a number with too many digits")
    return value


def parse_quote_template_json(raw: bytes | str) -> Any:
    """Parse definition JSON strictly: size cap, UTF-8 only, no duplicate keys or NaN."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes):
        _refuse("definition", "must be JSON text")
    if len(raw) > MAX_DEFINITION_BYTES:
        _refuse("definition", f"is larger than {MAX_DEFINITION_BYTES // 1024} KiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _refuse("definition", "is not UTF-8 text")
    if text.startswith("\ufeff"):
        _refuse("definition", "starts with a byte order mark")
    try:
        return json.loads(
            text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant,
            parse_int=_parse_int, parse_float=_parse_float,
        )
    except RecursionError:
        _refuse("definition", "is nested too deeply")
    except json.JSONDecodeError as exc:
        _refuse("definition", f"is not valid JSON (line {exc.lineno}, column {exc.colno})")


def read_quote_template(value: Any) -> dict[str, Any]:
    """Check a definition (JSON text, bytes or an already-parsed object) and return it normalised.

    The result has every optional key filled in, so two definitions that mean the same
    thing normalise to the same canonical JSON and fingerprint.
    """
    if isinstance(value, (bytes, str)):
        value = parse_quote_template_json(value)
    else:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            _refuse("definition", "must contain only JSON values")
        try:
            size = len(encoded.encode("utf-8"))
        except UnicodeEncodeError:
            _refuse("definition", "contains a lone surrogate character")
        if size > MAX_DEFINITION_BYTES:
            _refuse("definition", f"is larger than {MAX_DEFINITION_BYTES // 1024} KiB")
        value = copy.deepcopy(value)
    root = _object(value, "definition", ("schema", "name", "page", "header", "grid", "theme", "labels", "blocks"))
    if root["schema"] != SCHEMA_ID:
        _refuse("schema", f"must be {SCHEMA_ID}")
    grid = _read_grid(root["grid"])
    header = _object(root["header"], "header", ("rows",))
    header_rows = _integer(header["rows"], "header.rows", 1, 40)
    page = _read_page(root["page"])
    if header_rows >= page["first_page_rows"]:
        _refuse("header.rows", "must leave room for the item table on the first page")
    blocks = [
        _read_block(block, index, len(grid["columns"]), header_rows)
        for index, block in enumerate(_list(root["blocks"], "blocks", 2, MAX_BLOCKS))
    ]
    _check_layout(blocks, grid)
    return {
        "schema": SCHEMA_ID,
        "name": _text(root["name"], "name", 60, allow_empty=False),
        "page": page,
        "header": {"rows": header_rows},
        "grid": grid,
        "theme": _read_theme(root["theme"]),
        "labels": _read_labels(root["labels"]),
        "blocks": blocks,
    }


def canonical_quote_template_json(definition: dict[str, Any]) -> str:
    return json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def quote_template_fingerprint(definition: dict[str, Any]) -> str:
    """SHA-256 of the normalised definition, recorded with each quote made from it."""
    return hashlib.sha256(canonical_quote_template_json(read_quote_template(definition)).encode("utf-8")).hexdigest()


def load_standard_quote_template() -> dict[str, Any]:
    """The built-in read-only Standard template that mirrors the bundled quotation layout."""
    return read_quote_template(STANDARD_TEMPLATE_PATH.read_bytes())
