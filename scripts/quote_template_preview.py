#!/usr/bin/env python3
"""Render a sample quote with a draft quote template, for the Settings > Quote Templates preview.

The webapp runs this as a child process (standard library only, like generate_quote.py). It never
reads customer data: the quote below is fixed synthetic sample data, and the logo is a plain grey
placeholder so the admin can see where the logo sits and how big it prints.

Usage: quote_template_preview.py --quote-template <definition.json> --out <folder> --format xlsx|pdf

Writes <folder>/quote-template-preview.xlsx, plus quote-template-preview.pdf for --format pdf.
Exit codes: 0 written; 1 the definition was refused or rendering failed; 3 no PDF converter
(LibreOffice or Excel) is available on this server.
"""
from __future__ import annotations

import argparse
import base64
import struct
import sys
import zlib
from pathlib import Path

try:
    from . import generate_quote as quote
    from . import quote_template
except ImportError:
    import generate_quote as quote
    import quote_template

PREVIEW_STEM = "quote-template-preview"
PDF_UNAVAILABLE_EXIT = 3
# Placeholder logo: 4:1, light grey. Its printed size comes from the template's logo block.
PLACEHOLDER_LOGO_SIZE = (240, 60)
PLACEHOLDER_LOGO_GREY = 0xD9


def placeholder_logo_data_url() -> str:
    width, height = PLACEHOLDER_LOGO_SIZE
    row = b"\x00" + bytes([PLACEHOLDER_LOGO_GREY]) * width
    raw = zlib.compress(row * height, 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def sample_brief() -> dict:
    """A fixed, made-up quote. Nothing here comes from a real company or customer."""
    return {
        "quote_date": "2026-01-15",
        "project_number": "SAMPLE-001",
        "client": {"name": "Sample Client Pte Ltd", "attention": "Sample Contact", "title": "Event Manager",
                   "address": ["1 Sample Road", "#01-01 Sample Building", "Sample City 000000"]},
        "project": {"title": "Sample exhibition booth"},
        "currency": "SGD",
        "tax": {"label": "GST", "rate": 0.09},
        "company": {"name": "Sample Company Pte Ltd",
                    "header_lines": ["Sample Company Pte Ltd", "2 Sample Street, Sample City 000000"],
                    "logo_data_url": placeholder_logo_data_url()},
        "acceptance": {"company_name": "Sample Client Pte Ltd", "text": "We accept this quotation",
                       "person_label": "Name", "stamp_label": "Company stamp", "date_label": "Date:"},
        "signature": {"company_signatory": "Sample Signatory", "company_title": "Director",
                      "company_date_label": "Date:"},
        "terms_heading": "Terms", "payment_terms": ["50% deposit on confirmation", "Balance on completion"],
        "notes_heading": "Notes", "standard_notes": ["Prices are valid for 30 days."],
        "line_items": [],
    }


SAMPLE_ROWS = (
    # section, quantity, unit, description, unit price
    ("Booth structure", 12, "sqm", "Sample booth wall panels with printed graphics", 120.0),
    ("Booth structure", 1, "set", "Sample reception counter with storage", 840.0),
    ("Flooring", 18, "sqm", "Sample carpet with underlay", 22.5),
    ("Lighting", 6, "pc", "Sample LED spotlight", 45.0),
)


def sample_lines() -> list:
    lines = []
    for section, quantity, unit, description, unit_price in SAMPLE_ROWS:
        lines.append(quote.QuoteLine(
            section=section, quantity=float(quantity), unit=unit, description=description,
            pricing_keyword=description, display_price="", matched_price=None,
            amount=quote.round_commercial_cents(quantity * unit_price), match_status="manual-price",
            match_candidates=[], unit_price_override=unit_price,
        ))
    return lines


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a sample quote with a draft quote template.")
    parser.add_argument("--quote-template", required=True, type=Path, help="Quote template definition JSON.")
    parser.add_argument("--out", required=True, type=Path, help="Folder for the preview files.")
    parser.add_argument("--format", required=True, choices=("xlsx", "pdf"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        definition = quote_template.parse_quote_template_json(args.quote_template.read_bytes())
        args.out.mkdir(parents=True, exist_ok=True)
        xlsx_path = args.out / f"{PREVIEW_STEM}.xlsx"
        quote.write_template_quote_xlsx(definition, xlsx_path, sample_brief(), sample_lines())
    except quote_template.QuoteTemplateError as exc:
        print(f"Template refused: {exc}", file=sys.stderr)
        return 1
    if args.format == "pdf":
        pdf_path = args.out / f"{PREVIEW_STEM}.pdf"
        if not quote.export_layout_pdf(xlsx_path, pdf_path) or not pdf_path.is_file():
            print("No PDF converter is available.", file=sys.stderr)
            return PDF_UNAVAILABLE_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
