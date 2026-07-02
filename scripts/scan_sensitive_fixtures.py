#!/usr/bin/env python3
"""Scan committed quote-generator fixtures for sensitive fixture markers.

The scanner reports only path and category. It must not print matched values.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree


TEXT_SUFFIXES = {
    ".csv",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
MEDIA_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DEFAULT_SCAN_ROOTS = ("profiles/", "pricing-references/", "fixtures/", "tests/fixtures/quote-generator/")
GENERATED_OUTPUT_DIRS = {"_output", "output", "generated-output", "generated-outputs"}
LOCAL_OUTPUT_ARTIFACT_DIRS = {"screenshots", "_screenshots", "playwright-screenshots", "playwright-report", "test-results"}
PROFILE_EXPORT_RE = re.compile(r"quote-company-profile", re.IGNORECASE)
PRIVATE_PRICING_RE = re.compile(r"(private|local|uploaded)[-_ ]?pricing", re.IGNORECASE)
REAL_COMPANY_RE = re.compile(r"koncept(?:\s+|-)+images?(?:\s+|-)+pte", re.IGNORECASE)
WORKSPACE_ID_RE = re.compile(r"koncept-images-pte-ltd", re.IGNORECASE)
CUSTOMER_SAMPLE_RE = re.compile(r"kent(?:\s+|-)+group", re.IGNORECASE)
INTERNAL_PRICING_RE = re.compile(r"\b(internal_cost|markup_multiplier|supplier_notes?|supplier)\b", re.IGNORECASE)
SECRET_RE = re.compile(
    r"(BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY|OPENAI_API_KEY|sk-[A-Za-z0-9_-]{16,}|"
    r"password\s*[:=]|secret\s*[:=]|token\s*[:=])",
    re.IGNORECASE,
)
PAYMENT_RE = re.compile(
    r"(account\s*(?:no\.?|number)|bank\s+(?:account|code|name)|swift|iban|paynow|"
    r"uen\s*[:#]?\s*[0-9])",
    re.IGNORECASE,
)
KNOWN_SYNTHETIC_REVIEW_ALLOWLIST = {
    ("committed-pdf-sample", "tests/fixtures/quote-generator/samples/kent-group/kent-group.pdf"),
    (
        "committed-fixture-media",
        "tests/fixtures/quote-generator/pricing-references/synthetic-exhibition-fixture-pricing/pricing-catalog-images/synthetic-chip.png",
    ),
    ("customer-sample-marker", "tests/fixtures/quote-generator/samples/kent-group/sample.json"),
    (
        "embedded-logo-reference",
        "tests/fixtures/quote-generator/profiles/synthetic-exhibition-fixture-template/profile.json",
    ),
    ("internal-pricing-field", "docs/pricing-catalog-import.md"),
    (
        "internal-pricing-field",
        "tests/fixtures/quote-generator/pricing-references/synthetic-exhibition-fixture-pricing/pricing-catalog.json",
    ),
    (
        "xlsx-defined-name",
        "tests/fixtures/quote-generator/profiles/synthetic-exhibition-fixture-template/quotation-layout.xlsx",
    ),
}


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    category: str
    severity: str


def repo_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def add_finding(findings: set[Finding], path: Path, root: Path, category: str, severity: str) -> None:
    rel_path = repo_relative(path, root)
    if severity == "review" and (category, rel_path) in KNOWN_SYNTHETIC_REVIEW_ALLOWLIST:
        return
    findings.add(Finding(path=rel_path, category=category, severity=severity))


def tracked_files(root: Path) -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        return []
    names = completed.stdout.decode("utf-8", errors="replace").split("\0")
    return [root / name for name in names if name and (root / name).is_file() and in_default_scan_scope(name)]


def in_default_scan_scope(rel_path: str) -> bool:
    lower = normalize_rel_path(rel_path)
    if is_private_profile_export_path(lower):
        return True
    if is_private_pricing_upload_path(lower):
        return True
    if is_generated_output_rel_path(lower):
        return True
    if is_local_output_artifact_path(lower):
        return True
    if lower.startswith(DEFAULT_SCAN_ROOTS):
        return True
    if not lower.startswith("docs/"):
        return False
    name = lower.rsplit("/", 1)[-1]
    if PROFILE_EXPORT_RE.search(name) or PRIVATE_PRICING_RE.search(name):
        return True
    if name.endswith((".xlsx", ".csv", ".json", ".pdf")):
        return True
    return any(token in name for token in ("profile", "pricing", "quotation", "quote", "generated", "export"))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def marker_severity(category: str, rel_path: str) -> str:
    return "review" if (category, rel_path) in KNOWN_SYNTHETIC_REVIEW_ALLOWLIST else "block"


def normalize_rel_path(rel_path: str) -> str:
    return rel_path.replace("\\", "/").lower().lstrip("/")


def rel_path_name(rel_path: str) -> str:
    return normalize_rel_path(rel_path).rsplit("/", 1)[-1]


def rel_path_suffix(rel_path: str) -> str:
    name = rel_path_name(rel_path)
    dot = name.rfind(".")
    return name[dot:] if dot != -1 else ""


def is_private_profile_export_path(rel_path: str) -> bool:
    return bool(PROFILE_EXPORT_RE.search(rel_path_name(rel_path)) and rel_path_suffix(rel_path) == ".json")


def is_private_pricing_upload_path(rel_path: str) -> bool:
    return bool(
        PRIVATE_PRICING_RE.search(normalize_rel_path(rel_path))
        and rel_path_suffix(rel_path) in {".xlsx", ".csv", ".json", ".md"}
    )


def is_generated_output_rel_path(rel_path: str) -> bool:
    lower = normalize_rel_path(rel_path)
    lower_name = rel_path_name(lower)
    parts = set(lower.split("/"))
    suffix = rel_path_suffix(lower)
    if parts & GENERATED_OUTPUT_DIRS:
        return True
    if lower_name in {"quotation.xlsx", "quotation.pdf", "quote.pdf", "quote.xlsx"}:
        return True
    if lower_name.startswith(("generated-quote", "quote-output", "quotation-output")):
        return True
    if lower_name.startswith("quotation-") and suffix in {".xlsx", ".pdf"}:
        return bool(re.match(r"quotation[-_](?:\d|output|draft|generated)", lower_name))
    return False


def is_local_output_artifact_path(rel_path: str) -> bool:
    lower = normalize_rel_path(rel_path)
    parts = set(lower.split("/"))
    if parts & LOCAL_OUTPUT_ARTIFACT_DIRS:
        return True
    return rel_path_name(lower).endswith(".screenshot.png")


def is_generated_output_path(path: Path, rel_path: str) -> bool:
    return is_generated_output_rel_path(rel_path or path.name)


def scan_text_content(path: Path, root: Path, text: str, findings: set[Finding]) -> None:
    rel_path = repo_relative(path, root)
    if SECRET_RE.search(text):
        add_finding(findings, path, root, "secret-credential-marker", "block")
    if PAYMENT_RE.search(text):
        add_finding(findings, path, root, "bank-payment-marker", "block")
    if REAL_COMPANY_RE.search(text) or WORKSPACE_ID_RE.search(text):
        add_finding(
            findings,
            path,
            root,
            "real-company-identity-marker",
            marker_severity("real-company-identity-marker", rel_path),
        )
    if CUSTOMER_SAMPLE_RE.search(text):
        add_finding(
            findings,
            path,
            root,
            "customer-sample-marker",
            marker_severity("customer-sample-marker", rel_path),
        )
    if INTERNAL_PRICING_RE.search(text):
        add_finding(findings, path, root, "internal-pricing-field", "review")


def scan_json_structure(path: Path, root: Path, text: str, findings: set[Finding]) -> None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return

    def walk(value: object, key_path: str = "") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                lowered = key_text.lower()
                if lowered in {"logo_data_url", "logo_path"}:
                    add_finding(findings, path, root, "embedded-logo-reference", "review")
                if lowered in {"internal_cost", "markup_multiplier", "supplier", "supplier_notes"}:
                    add_finding(findings, path, root, "internal-pricing-field", "review")
                if lowered in {"bank", "bank_details", "account_number", "swift", "iban", "paynow"}:
                    add_finding(findings, path, root, "bank-payment-marker", "block")
                walk(child, f"{key_path}.{key_text}" if key_path else key_text)
        elif isinstance(value, list):
            for child in value:
                walk(child, key_path)

    walk(data)


def xml_text(xml_bytes: bytes) -> str:
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return ""
    return " ".join(text for text in root.itertext() if text)


def scan_xlsx(path: Path, root: Path, findings: set[Finding]) -> None:
    try:
        workbook = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return
    with workbook:
        names = workbook.namelist()
        for name in names:
            lower = name.lower()
            if lower.startswith("xl/media/"):
                add_finding(findings, path, root, "xlsx-embedded-media", "review")
            if lower.startswith("xl/externallinks/"):
                add_finding(findings, path, root, "xlsx-external-link", "block")
            if lower.startswith("xl/comments") or lower.startswith("xl/threadedcomments"):
                add_finding(findings, path, root, "xlsx-comments-notes", "review")
        for name in names:
            lower = name.lower()
            if not lower.endswith(".xml"):
                continue
            try:
                data = workbook.read(name)
            except KeyError:
                continue
            text = xml_text(data)
            if lower == "xl/workbook.xml":
                if re.search(r'state="(?:hidden|veryHidden)"', data.decode("utf-8", errors="replace"), re.IGNORECASE):
                    add_finding(findings, path, root, "xlsx-hidden-sheet", "review")
                if "definedName" in data.decode("utf-8", errors="replace"):
                    add_finding(findings, path, root, "xlsx-defined-name", "review")
            if lower.startswith("docprops/"):
                scan_text_content(path, root, text, findings)
                if text.strip():
                    add_finding(findings, path, root, "xlsx-document-properties", "review")
            if lower.startswith("xl/worksheets/"):
                raw = data.decode("utf-8", errors="replace")
                if "<f" in raw:
                    add_finding(findings, path, root, "xlsx-formula", "review")
                if "headerFooter" in raw:
                    add_finding(findings, path, root, "xlsx-header-footer", "review")
            if lower == "xl/sharedstrings.xml":
                scan_text_content(path, root, text, findings)
                if text.strip():
                    add_finding(findings, path, root, "xlsx-shared-strings", "review")


def scan_file(path: Path, root: Path) -> set[Finding]:
    findings: set[Finding] = set()
    rel_path = repo_relative(path, root)
    lower_rel = rel_path.lower()
    suffix = path.suffix.lower()

    if is_private_profile_export_path(rel_path):
        add_finding(findings, path, root, "private-profile-export", "block")
    if is_private_pricing_upload_path(rel_path):
        add_finding(findings, path, root, "private-pricing-upload", "block")
    if is_generated_output_path(path, rel_path):
        add_finding(findings, path, root, "generated-quote-output", "block")
    if is_local_output_artifact_path(rel_path):
        add_finding(findings, path, root, "local-output-artifact", "block")
    if suffix in MEDIA_SUFFIXES and ("pricing-catalog-images" in lower_rel or "asset-packs" in lower_rel):
        add_finding(findings, path, root, "committed-fixture-media", "review")
    if suffix == ".pdf" and "fixtures/" in lower_rel:
        add_finding(findings, path, root, "committed-pdf-sample", "review")

    if suffix == ".xlsx":
        scan_xlsx(path, root, findings)
    elif suffix in TEXT_SUFFIXES:
        text = read_text(path)
        scan_text_content(path, root, text, findings)
        if suffix == ".json":
            scan_json_structure(path, root, text, findings)

    return findings


def scan_paths(paths: Iterable[Path], root: Path) -> list[Finding]:
    findings: set[Finding] = set()
    for path in paths:
        resolved = path if path.is_absolute() else root / path
        if resolved.is_file():
            findings.update(scan_file(resolved, root))
    return sorted(findings)


def scan_tracked_files(root: Path) -> list[Finding]:
    return scan_paths(tracked_files(root), root)


def print_findings(findings: list[Finding]) -> None:
    block_count = sum(1 for finding in findings if finding.severity == "block")
    review_count = sum(1 for finding in findings if finding.severity == "review")
    print(f"Sensitive fixture scan: {block_count} blocking, {review_count} review findings")
    for finding in findings:
        print(f"{finding.severity.upper()}\t{finding.category}\t{finding.path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--path", action="append", default=[], help="Specific file path to scan. Repeatable.")
    parser.add_argument("--fail-on-review", action="store_true", help="Return non-zero when review findings exist.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    paths = [Path(value) for value in args.path]
    findings = scan_paths(paths, root) if paths else scan_tracked_files(root)
    print_findings(findings)
    has_blocking = any(finding.severity == "block" for finding in findings)
    has_review = any(finding.severity == "review" for finding in findings)
    return 1 if has_blocking or (args.fail_on_review and has_review) else 0


if __name__ == "__main__":
    raise SystemExit(main())
