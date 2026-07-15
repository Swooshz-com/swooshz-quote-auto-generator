#!/usr/bin/env python3
"""Build a structured pricing catalog from an uploaded pricing workbook."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import generate_quote as quote
import pricing_reference_cleanup
import pricing_reference_enrichment


CATALOG_SCHEMA_VERSION = 1
CATALOG_VISUALS_DIR_NAME = "pricing-catalog-images"
MAX_CATALOG_VISUALS = 80
MAX_CATALOG_VISUAL_BYTES = 512 * 1024
MAX_CATALOG_VISUALS_PER_ITEM = 3

NS_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS_PACKAGE_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

COL_SECTION_NO = 0
COL_DEFAULT_QUANTITY = 1
COL_DESCRIPTION = 2
COL_DEFAULT_ESTIMATE = 5
COL_COST = 7
COL_GST = 8
COL_MARKUP = 9
COL_REMARKS = 11


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a pricing workbook into a root-level pricing reference catalog.")
    parser.add_argument("--source", required=True, type=Path, help="Pricing source .xlsx file.")
    parser.add_argument("--source-label", help=argparse.SUPPRESS)
    parser.add_argument("--out", required=True, type=Path, help="Output pricing catalog JSON path.")
    parser.add_argument("--ai-reference-md-out", type=Path, help="Optional generated Markdown view for AI catalog reading.")
    return parser.parse_args()


def normalized_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\xa0", " ")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip()


def apply_pricing_workbook_text_fixes(value: Any) -> str:
    return normalized_text(pricing_reference_cleanup.apply_import_text_cleanup(value))


def numeric_value(value: Any) -> float | None:
    if value in (None, ""):
        return None
    parsed = quote.as_float(value, 0.0)
    if parsed == 0.0 and str(value).strip() not in {"0", "0.0", "0.00"}:
        return None
    return parsed


PIECE_DIMENSION_DESCRIPTION_RE = re.compile(
    r"(?i)^(?:nos\.?\s+of\s+1m\s+length\s+x|m\.?\s*length\s*x)\b"
)


def positive_order(value: Any) -> int | None:
    parsed = numeric_value(value)
    if parsed is None or parsed <= 0:
        return None
    return int(parsed)


def is_piece_dimension_description(description: Any) -> bool:
    text = normalized_text(description)
    return bool(PIECE_DIMENSION_DESCRIPTION_RE.search(text)) and len(re.findall(r"(?i)\bx\b", text)) >= 2


def normalize_piece_dimension_description(description: Any) -> str:
    text = normalized_text(description)
    if not is_piece_dimension_description(text):
        return text
    return re.sub(
        r"(?i)^m\.?\s*length\s*x\b",
        "nos. of 1m length x",
        text,
        count=1,
    )


def cell(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def text_cell(row: list[Any], index: int) -> str:
    return normalized_text(cell(row, index))


def is_section_row(row: list[Any]) -> bool:
    return numeric_value(cell(row, COL_SECTION_NO)) is not None and bool(text_cell(row, COL_DESCRIPTION)) and numeric_value(cell(row, COL_COST)) is None


def is_price_row(row: list[Any]) -> bool:
    return bool(text_cell(row, COL_DESCRIPTION)) and (numeric_value(cell(row, COL_COST)) or 0.0) > 0


def append_unique(values: list[str], value: str) -> None:
    cleaned = normalized_text(value)
    if not cleaned:
        return
    lowered = {item.lower() for item in values}
    if cleaned.lower() not in lowered:
        values.append(cleaned)


def pricing_reference_description_cell_note(value: Any) -> str:
    text = normalized_text(value)
    text = re.sub(r"^(?:\s|[-*]|\u2022|\u00e2\u20ac\u00a2)+", "", text).strip()
    if not text:
        return ""
    if re.search(r"\bprices?\b.*\bnot\s+inclusive\b", text, flags=re.IGNORECASE):
        return text
    if re.search(r"\bnot\s+inclusive\s+of\b", text, flags=re.IGNORECASE):
        return text
    if re.search(r"\b(?:price|prices?)\b.*\b(?:exclude|excluding|excluded)\b", text, flags=re.IGNORECASE):
        return text
    return ""


def append_description_part(item: dict[str, Any], value: str) -> None:
    cleaned = normalized_text(value)
    if not cleaned:
        return
    if cleaned.startswith("\u2022") or cleaned.lower().startswith(("note:", "notes:")):
        note = pricing_reference_description_cell_note(cleaned)
        if note:
            append_unique(item["description_parts"], note)
            return
        append_unique(item["remarks"], cleaned)
        return
    append_unique(item["description_parts"], cleaned)


def append_remark(item: dict[str, Any], value: str) -> None:
    append_unique(item["remarks"], value)


def strip_leading_unit(value: str) -> str:
    return normalized_text(
        re.sub(
            r"^(?:m2|sqm|m\.?\s*length|m\.?\s*run|nos\.?|no\.|sets?|lot\.?)\s+(?:of\s+|rental\s+of\s+)?",
            "",
            value,
            flags=re.IGNORECASE,
        )
    )


def alias_candidates(section: str, description_parts: list[str], remarks: list[str], unit_hint: str) -> list[str]:
    aliases: list[str] = []
    source_values = [*description_parts, *remarks]
    for value in source_values:
        append_unique(aliases, value)
        append_unique(aliases, strip_leading_unit(value))
        for part in re.split(r"[;/,]", value):
            append_unique(aliases, strip_leading_unit(part))
    if section:
        for value in description_parts:
            append_unique(aliases, f"{section} {strip_leading_unit(value)}")
    if unit_hint:
        for value in description_parts:
            append_unique(aliases, f"{strip_leading_unit(value)} {unit_hint}")
    return aliases


def catalog_id(section: str, description: str, existing_ids: set[str]) -> str:
    section_slug = quote.slugify_segment(section, "pricing")
    item_slug = quote.slugify_segment(strip_leading_unit(description), "item")
    base = f"{section_slug}-{item_slug}"
    candidate = base
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    existing_ids.add(candidate)
    return candidate


def finalize_item(item: dict[str, Any], items: list[dict[str, Any]]) -> None:
    if not item:
        return
    description_parts = item.pop("description_parts")
    remarks = item["remarks"]
    section = item["section"]
    description = normalize_piece_dimension_description("; ".join(description_parts))
    id_description_parts = [
        part for part in description_parts
        if not pricing_reference_description_cell_note(part)
    ]
    id_description = normalize_piece_dimension_description("; ".join(id_description_parts)) or description
    unit_hint = quote.infer_unit(description)
    if is_piece_dimension_description(description):
        unit_hint = "nos"
    alias_description_parts = [description, *[part for part in description_parts if normalized_text(part) != description]]
    existing_ids = {existing["id"] for existing in items}
    item.update(
        {
            "id": catalog_id(section, id_description, existing_ids),
            "description": description,
            "unit_hint": unit_hint,
            "aliases": alias_candidates(section, alias_description_parts, remarks, unit_hint),
            "sale_unit_price": round(float(item["internal_cost"]) * float(item["markup_multiplier"]), 2),
        }
    )
    pricing_reference_enrichment.enrich_pricing_reference_item(item)
    items.append(item)


def item_from_price_row(section: str, section_order: int | None, row_number: int, row: list[Any]) -> dict[str, Any]:
    default_quantity = numeric_value(cell(row, COL_DEFAULT_QUANTITY))
    default_estimate = numeric_value(cell(row, COL_DEFAULT_ESTIMATE))
    gst_multiplier = numeric_value(cell(row, COL_GST)) or 1.0
    item = {
        "_source_row": row_number,
        "category_order": section_order,
        "item_order": row_number,
        "section": section,
        "description_parts": [apply_pricing_workbook_text_fixes(text_cell(row, COL_DESCRIPTION))],
        "default_quantity": default_quantity,
        "default_quote_amount": default_estimate if default_estimate and default_estimate > 0 else None,
        "internal_cost": numeric_value(cell(row, COL_COST)) or 0.0,
        "gst_multiplier": gst_multiplier,
        "markup_multiplier": numeric_value(cell(row, COL_MARKUP)) or 1.0,
        "remarks": [],
        "extra_values": [],
    }
    append_remark(item, apply_pricing_workbook_text_fixes(text_cell(row, COL_REMARKS)))
    for value in row[COL_REMARKS + 1:]:
        cleaned = normalized_text(value)
        if cleaned:
            item["extra_values"].append(cleaned)
    return item


def normalize_drawing_target(base_dir: str, target: str) -> str:
    clean_target = normalized_text(target).replace("\\", "/")
    if not clean_target:
        return ""
    if clean_target.startswith("/"):
        normalized = posixpath.normpath(clean_target.lstrip("/"))
    else:
        normalized = posixpath.normpath(posixpath.join(base_dir, clean_target))
    return normalized if normalized.startswith("xl/media/") else ""


def xlsx_visual_references(source: Path) -> list[dict[str, Any]]:
    visual_refs: list[dict[str, Any]] = []
    with zipfile.ZipFile(source) as zf:
        if len(zf.infolist()) > quote.MAX_XLSX_ENTRIES:
            raise ValueError("Spreadsheet archive contains too many entries.")
        media_sizes = {
            info.filename: info.file_size
            for info in zf.infolist()
            if info.filename.startswith("xl/media/")
        }
        drawing_names = sorted(
            name
            for name in zf.namelist()
            if re.fullmatch(r"xl/drawings/drawing\d+\.xml", name)
        )
        for drawing_name in drawing_names:
            if len(visual_refs) >= MAX_CATALOG_VISUALS:
                break
            rels_name = f"{posixpath.dirname(drawing_name)}/_rels/{posixpath.basename(drawing_name)}.rels"
            try:
                rels_root = ET.fromstring(quote.read_bounded_xlsx_part(zf, rels_name, max_bytes=1024 * 1024))
                drawing_root = ET.fromstring(quote.read_bounded_xlsx_part(zf, drawing_name, max_bytes=2 * 1024 * 1024))
            except (KeyError, ET.ParseError):
                continue
            base_dir = posixpath.dirname(drawing_name)
            rels = {
                normalized_text(rel.attrib.get("Id")): normalize_drawing_target(base_dir, rel.attrib.get("Target", ""))
                for rel in rels_root.findall(f"{NS_PACKAGE_REL}Relationship")
                if normalized_text(rel.attrib.get("Type")).endswith("/image")
            }
            for anchor in list(drawing_root):
                if len(visual_refs) >= MAX_CATALOG_VISUALS:
                    break
                if not anchor.tag.endswith("Anchor"):
                    continue
                from_node = anchor.find(f"{NS_DRAWING}from")
                pic_node = anchor.find(f"{NS_DRAWING}pic")
                if from_node is None or pic_node is None:
                    continue
                row_node = from_node.find(f"{NS_DRAWING}row")
                col_node = from_node.find(f"{NS_DRAWING}col")
                blip = pic_node.find(f".//{NS_A}blip")
                rel_id = normalized_text(blip.attrib.get(f"{NS_REL}embed") if blip is not None else "")
                image_source = rels.get(rel_id, "")
                size = media_sizes.get(image_source, 0)
                if not image_source or size <= 0 or size > MAX_CATALOG_VISUAL_BYTES:
                    continue
                mime_type = mimetypes.guess_type(image_source)[0] or "image/png"
                if mime_type.lower() not in {"image/png", "image/jpeg", "image/webp"}:
                    continue
                try:
                    anchor_row = int(row_node.text or "0") + 1 if row_node is not None else 0
                    anchor_col = int(col_node.text or "0") + 1 if col_node is not None else 0
                except ValueError:
                    continue
                visual_refs.append(
                    {
                        "source": image_source,
                        "anchor_row": anchor_row,
                        "anchor_col": anchor_col,
                        "_bytes": quote.read_bounded_xlsx_part(zf, image_source, max_bytes=MAX_CATALOG_VISUAL_BYTES),
                    }
                )
    return visual_refs


def unique_visual_filename(source: str, used: set[str]) -> str:
    name = posixpath.basename(source)
    suffix = Path(name).suffix.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    if suffix not in {".png", ".jpg", ".webp"}:
        suffix = ".png"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip(".-_") or "catalog-visual"
    candidate = f"{stem}{suffix}"
    index = 2
    while candidate.lower() in used:
        candidate = f"{stem}-{index}{suffix}"
        index += 1
    used.add(candidate.lower())
    return candidate


def persist_visual_references(visual_refs: list[dict[str, Any]], out: Path | None = None) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    used_names: set[str] = set()
    source_paths: dict[str, str] = {}
    assets_dir = out.parent / CATALOG_VISUALS_DIR_NAME if out else None
    if assets_dir:
        assets_dir.mkdir(parents=True, exist_ok=True)
    for visual_ref in visual_refs:
        source = normalized_text(visual_ref.get("source")).replace("\\", "/")
        image_bytes = visual_ref.get("_bytes")
        if not source or not isinstance(image_bytes, bytes) or not image_bytes:
            continue
        ref: dict[str, Any] = {"source": source}
        anchor_row = int(numeric_value(visual_ref.get("anchor_row")) or 0)
        anchor_col = int(numeric_value(visual_ref.get("anchor_col")) or 0)
        if anchor_row > 0:
            ref["anchor_row"] = anchor_row
        if anchor_col > 0:
            ref["anchor_col"] = anchor_col
        if assets_dir:
            relative_path = source_paths.get(source)
            if not relative_path:
                filename = unique_visual_filename(source, used_names)
                (assets_dir / filename).write_bytes(image_bytes)
                relative_path = f"{CATALOG_VISUALS_DIR_NAME}/{filename}"
                source_paths[source] = relative_path
            ref["path"] = relative_path
        else:
            mime_type = mimetypes.guess_type(source)[0] or "image/png"
            ref["data_url"] = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        persisted.append(ref)
    return persisted


def attach_visual_references_to_items(items: list[dict[str, Any]], visual_refs: list[dict[str, Any]]) -> None:
    priced_rows = [
        (int(item.get("_source_row") or 0), item)
        for item in items
        if int(item.get("_source_row") or 0) > 0
    ]
    if not priced_rows:
        return
    for visual_ref in visual_refs:
        anchor_row = int(visual_ref.get("anchor_row") or 0)
        if not anchor_row:
            continue
        nearest_row, nearest_item = min(
            priced_rows,
            key=lambda item: (abs(item[0] - anchor_row), 0 if item[0] >= anchor_row else 1),
        )
        if abs(nearest_row - anchor_row) > 6:
            continue
        item_refs = nearest_item.setdefault("visual_references", [])
        if len(item_refs) >= MAX_CATALOG_VISUALS_PER_ITEM:
            continue
        item_refs.append(visual_ref)


def build_catalog_from_xlsx(source: Path, out: Path | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    rows = quote.read_first_sheet_rows_with_numbers(source)
    current_section = ""
    current_section_order: int | None = None
    fallback_section_order = 1
    current_item: dict[str, Any] | None = None

    for row_number, row in rows:
        if is_section_row(row):
            finalize_item(current_item or {}, items)
            current_item = None
            current_section = text_cell(row, COL_DESCRIPTION)
            current_section_order = positive_order(cell(row, COL_SECTION_NO)) or fallback_section_order
            fallback_section_order = max(fallback_section_order + 1, current_section_order + 1)
            continue

        if is_price_row(row):
            finalize_item(current_item or {}, items)
            current_item = item_from_price_row(current_section, current_section_order, row_number, row)
            continue

        if current_item is None:
            continue

        description = apply_pricing_workbook_text_fixes(text_cell(row, COL_DESCRIPTION))
        remark = apply_pricing_workbook_text_fixes(text_cell(row, COL_REMARKS))
        if description:
            append_description_part(current_item, description)
        if remark:
            append_remark(current_item, remark)
        for value in row[COL_REMARKS + 1:]:
            cleaned = normalized_text(value)
            if cleaned:
                current_item["extra_values"].append(cleaned)

    finalize_item(current_item or {}, items)
    attach_visual_references_to_items(items, persist_visual_references(xlsx_visual_references(source), out))
    for item in items:
        item.pop("_source_row", None)
    return catalog_payload(items)


def catalog_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "currency": "SGD",
        "items": items,
    }


def build_catalog(source: Path, source_label: str | None = None, out: Path | None = None) -> dict[str, Any]:
    suffix = source.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return build_catalog_from_xlsx(source, out=out)
    raise ValueError(f"Pricing catalog source must be an .xlsx workbook: {source}")


def write_catalog(catalog: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def catalog_to_ai_reference_markdown(catalog: dict[str, Any], catalog_name: str = "pricing-catalog.json") -> str:
    currency = normalized_text(catalog.get("currency")) or "SGD"
    lines = [
        "---",
        "title: Pricing Catalog AI Reference",
        "kind: generated_pricing_ai_reference",
        f"source_catalog: {catalog_name}",
        "---",
        "",
        "# Pricing Catalog AI Reference",
        "",
        "Generated from the structured pricing catalog. Do not edit this file directly.",
        "Use this Markdown for AI readability; use the JSON catalog for pricing logic.",
        "",
    ]
    current_section = None
    for item in catalog.get("items", []):
        section = normalized_text(item.get("section")) or "Unsectioned"
        if section != current_section:
            lines.extend([f"## {section}", ""])
            current_section = section
        lines.extend(
            [
                f"### {item.get('id', '')}",
                "",
                f"- **Item:** {item.get('description', '')}",
                f"- **Unit hint:** {item.get('unit_hint') or 'not specified'}",
            ]
        )
        if item.get("default_quantity") is not None:
            lines.append(f"- **Default quantity:** {item.get('default_quantity')}")
        if item.get("default_quote_amount") is not None:
            lines.append(f"- **Default quote amount:** SGD {item.get('default_quote_amount')}")
        if item.get("sale_unit_price") is not None:
            lines.append(f"- **Sale unit price:** {currency} {float(item.get('sale_unit_price')):.2f}")
        remarks = item.get("remarks") or []
        if remarks:
            lines.append(f"- **Remarks:** {'; '.join(str(value) for value in remarks)}")
        aliases = item.get("aliases") or []
        if aliases:
            lines.append(f"- **Search aliases:** {'; '.join(str(value) for value in aliases[:12])}")
        match_terms = item.get("match_terms") or []
        if match_terms:
            lines.append(f"- **Match terms:** {'; '.join(str(value) for value in match_terms[:12])}")
        object_families = item.get("object_families") or []
        if object_families:
            lines.append(f"- **Object families:** {'; '.join(str(value) for value in object_families[:8])}")
        extra_values = item.get("extra_values") or []
        if extra_values:
            values = "; ".join(str(value) for value in extra_values[:8])
            lines.append(f"- **Extra values:** {values}")
        visual_references = item.get("visual_references") if isinstance(item.get("visual_references"), list) else []
        visual_values = [
            normalized_text(ref.get("path") or ref.get("source"))
            for ref in visual_references[:3]
            if isinstance(ref, dict) and normalized_text(ref.get("path") or ref.get("source"))
        ]
        if visual_values:
            lines.append(f"- **Visual references:** {'; '.join(visual_values)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_ai_reference_markdown(catalog: dict[str, Any], out: Path, catalog_name: str = "pricing-catalog.json") -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(catalog_to_ai_reference_markdown(catalog, catalog_name), encoding="utf-8")


def main() -> None:
    args = parse_args()
    catalog = build_catalog(args.source, args.source_label, out=args.out)
    write_catalog(catalog, args.out)
    print(f"Wrote {args.out}")
    if args.ai_reference_md_out:
        write_ai_reference_markdown(catalog, args.ai_reference_md_out, args.out.name)
        print(f"Wrote {args.ai_reference_md_out}")


if __name__ == "__main__":
    main()
