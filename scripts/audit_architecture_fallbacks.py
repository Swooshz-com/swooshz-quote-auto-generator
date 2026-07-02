"""Static architecture/fallback audit helper for KQAG.

The output is intentionally metadata-only: relative paths, line numbers,
pattern names, and counts. It does not echo source lines, absolute roots, env
values, tokens, private local paths, generated quote contents, or fixture text.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "swooshz.kqag.architecture-fallback-audit.v1"

SKIP_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "_logs",
    "_output",
    "_tmp",
    "_pricing-references",
}

SKIP_FILES = {
    "architecture-dead-code-fallback-audit.md",
    "package-lock.json",
}

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".ps1",
    ".py",
    ".sql",
    ".txt",
    ".yaml",
    ".yml",
}

PATTERNS = (
    "Load Sample",
    "load sample",
    "loadSample",
    "sample quote",
    "fallback",
    "fallback_to",
    "bundled",
    "synthetic",
    "Kent",
    "sample",
    "fixture",
    "local",
    "local mode",
    "local pack",
    "profile pack",
    "pricing reference",
    "QUOTE_DATA_ROOT",
    "QUOTE_OUTPUT_ROOT",
    "QUOTE_TMP_ROOT",
    "KQAG_LOCAL_PRICING_REFERENCES_ROOT",
    "KQAG_STORAGE_MODE",
    "KQAG_ARTIFACT_STORAGE_MODE",
    "load_profile_pack",
    "list_local_pricing_references",
    "list_bundled_pricing_references",
    "pricing_reference_pack_detail",
    "send_download",
    "/api/jobs",
    "compatibility",
    "legacy",
    "deprecated",
    "TODO",
    "FIXME",
    "pass",
    "except Exception",
    "try:",
    "catch",
    "default",
    "mock",
    "demo",
    "fake",
    "example",
    "seed",
    "hardcoded",
    "TODO remove",
    "unused",
    "orphan",
)

PATTERN_CATEGORIES = {
    "Load Sample": "load_sample_surface",
    "load sample": "load_sample_surface",
    "loadSample": "load_sample_surface",
    "sample quote": "load_sample_surface",
    "fallback": "fallback_path",
    "fallback_to": "fallback_path",
    "bundled": "sample_or_bundled_data",
    "synthetic": "sample_or_bundled_data",
    "Kent": "sample_or_bundled_data",
    "sample": "sample_or_bundled_data",
    "fixture": "sample_or_bundled_data",
    "mock": "sample_or_bundled_data",
    "demo": "sample_or_bundled_data",
    "fake": "sample_or_bundled_data",
    "example": "sample_or_bundled_data",
    "seed": "sample_or_bundled_data",
    "local": "local_storage_dependency",
    "local mode": "local_storage_dependency",
    "local pack": "local_storage_dependency",
    "profile pack": "local_storage_dependency",
    "pricing reference": "pricing_reference_boundary",
    "QUOTE_DATA_ROOT": "local_storage_dependency",
    "QUOTE_OUTPUT_ROOT": "local_storage_dependency",
    "QUOTE_TMP_ROOT": "local_storage_dependency",
    "KQAG_LOCAL_PRICING_REFERENCES_ROOT": "local_storage_dependency",
    "KQAG_STORAGE_MODE": "storage_mode_boundary",
    "KQAG_ARTIFACT_STORAGE_MODE": "storage_mode_boundary",
    "load_profile_pack": "profile_boundary",
    "list_local_pricing_references": "pricing_reference_boundary",
    "list_bundled_pricing_references": "pricing_reference_boundary",
    "pricing_reference_pack_detail": "pricing_reference_boundary",
    "send_download": "artifact_download_boundary",
    "/api/jobs": "artifact_download_boundary",
    "compatibility": "legacy_or_compatibility",
    "legacy": "legacy_or_compatibility",
    "deprecated": "legacy_or_compatibility",
    "TODO": "dead_code_marker",
    "FIXME": "dead_code_marker",
    "TODO remove": "dead_code_marker",
    "unused": "dead_code_marker",
    "orphan": "dead_code_marker",
    "hardcoded": "hardcoded_marker",
    "except Exception": "broad_exception_boundary",
    "try:": "exception_or_default_boundary",
    "catch": "exception_or_default_boundary",
    "pass": "exception_or_default_boundary",
    "default": "exception_or_default_boundary",
}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    pattern: str
    category: str


@dataclass(frozen=True)
class Definition:
    path: str
    line: int
    name: str
    kind: str
    reference_count: int
    classification: str


def repo_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def should_skip(path: Path, root: Path) -> bool:
    rel = repo_relative(path, root)
    if Path(rel).name in SKIP_FILES:
        return True
    return any(part in SKIP_PARTS for part in Path(rel).parts)


def tracked_files(root: Path) -> list[Path]:
    try:
        top_level = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if (
            top_level.returncode != 0
            or os.path.normcase(str(Path(top_level.stdout.strip()).resolve()))
            != os.path.normcase(str(root.resolve()))
        ):
            return [path for path in root.rglob("*") if path.is_file()]
        completed = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        completed = None
    if completed and completed.returncode == 0:
        paths = []
        for raw in completed.stdout.splitlines():
            rel = raw.strip()
            if rel:
                paths.append(root / rel)
        return paths
    return [path for path in root.rglob("*") if path.is_file()]


def audit_files(root: Path) -> list[Path]:
    files = []
    for path in tracked_files(root):
        if not path.is_file() or should_skip(path, root):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        files.append(path)
    return sorted(files, key=lambda item: repo_relative(item, root))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def scan_patterns(paths: Iterable[Path], root: Path) -> tuple[list[Hit], Counter[str], Counter[str]]:
    hits: list[Hit] = []
    pattern_totals: Counter[str] = Counter()
    category_totals: Counter[str] = Counter()
    lower_patterns = [(pattern, pattern.casefold()) for pattern in PATTERNS]
    for path in paths:
        rel = repo_relative(path, root)
        for line_number, line in enumerate(read_text(path).splitlines(), start=1):
            folded = line.casefold()
            for pattern, folded_pattern in lower_patterns:
                if folded_pattern not in folded:
                    continue
                category = PATTERN_CATEGORIES[pattern]
                hits.append(Hit(rel, line_number, pattern, category))
                pattern_totals[pattern] += 1
                category_totals[category] += 1
    return hits, pattern_totals, category_totals


TOKEN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


def source_token_counts(paths: Iterable[Path]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in paths:
        if path.suffix.lower() in {".py", ".js", ".mjs", ".html", ".css"}:
            counts.update(TOKEN_RE.findall(read_text(path)))
    return counts


def classify_path(rel_path: str) -> str:
    path = rel_path.replace("\\", "/")
    if path.startswith("tests/") or "/fixtures/" in path or path.startswith("fixtures/"):
        return "test-only"
    if path.startswith("docs/"):
        return "docs"
    if path.startswith("scripts/"):
        return "tooling"
    return "app-code"


def python_definitions(path: Path, root: Path, token_counts: Counter[str]) -> list[Definition]:
    text = read_text(path)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    rel = repo_relative(path, root)
    results: list[Definition] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            if name.startswith("__") and name.endswith("__"):
                continue
            reference_count = token_counts.get(name, 0)
            if reference_count <= 1:
                classification = "possible-unused-follow-up-needed"
            elif classify_path(rel) in {"test-only", "docs"}:
                classification = classify_path(rel)
            else:
                classification = "referenced"
            results.append(Definition(rel, int(node.lineno), name, type(node).__name__, reference_count, classification))
    return results


JS_FUNCTION_RE = re.compile(
    r"\bfunction\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(|\b(?:const|let)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*(?:async\s*)?\(",
)


def javascript_definitions(path: Path, root: Path, token_counts: Counter[str]) -> list[Definition]:
    rel = repo_relative(path, root)
    results: list[Definition] = []
    for line_number, line in enumerate(read_text(path).splitlines(), start=1):
        for match in JS_FUNCTION_RE.finditer(line):
            name = match.group(1) or match.group(2)
            reference_count = token_counts.get(name, 0)
            classification = "possible-unused-follow-up-needed" if reference_count <= 1 else "referenced"
            if classify_path(rel) in {"test-only", "docs"}:
                classification = classify_path(rel)
            results.append(Definition(rel, line_number, name, "JavaScriptFunction", reference_count, classification))
    return results


def collect_definitions(paths: Iterable[Path], root: Path) -> list[Definition]:
    path_list = list(paths)
    token_counts = source_token_counts(path_list)
    definitions: list[Definition] = []
    for path in path_list:
        suffix = path.suffix.lower()
        if suffix == ".py":
            definitions.extend(python_definitions(path, root, token_counts))
        elif suffix in {".js", ".mjs"}:
            definitions.extend(javascript_definitions(path, root, token_counts))
    return sorted(definitions, key=lambda item: (item.classification, item.path, item.line, item.name))


def limited_hits(hits: list[Hit], max_hits_per_pattern: int) -> tuple[list[dict[str, object]], dict[str, bool]]:
    remaining = {pattern: max_hits_per_pattern for pattern in PATTERNS}
    truncated = {pattern: False for pattern in PATTERNS}
    selected: list[dict[str, object]] = []
    for hit in hits:
        if remaining[hit.pattern] > 0:
            selected.append({
                "path": hit.path,
                "line": hit.line,
                "pattern": hit.pattern,
                "category": hit.category,
            })
            remaining[hit.pattern] -= 1
        else:
            truncated[hit.pattern] = True
    return selected, {key: value for key, value in truncated.items() if value}


def build_report(root: Path, max_hits_per_pattern: int = 25, max_possible_unused: int = 100) -> dict[str, object]:
    resolved_root = root.resolve()
    paths = audit_files(resolved_root)
    hits, pattern_totals, category_totals = scan_patterns(paths, resolved_root)
    selected_hits, truncated = limited_hits(hits, max_hits_per_pattern)
    definitions = collect_definitions(paths, resolved_root)
    possible_unused = [
        item
        for item in definitions
        if item.classification == "possible-unused-follow-up-needed"
    ][:max_possible_unused]
    return {
        "schema": SCHEMA,
        "scan_scope": "tracked text files; runtime/private output roots excluded",
        "scanned_files_count": len(paths),
        "pattern_totals": {pattern: pattern_totals.get(pattern, 0) for pattern in PATTERNS},
        "category_totals": dict(sorted(category_totals.items())),
        "hits": selected_hits,
        "truncated_patterns": truncated,
        "definition_totals": dict(Counter(item.classification for item in definitions)),
        "possible_unused_definitions": [
            {
                "path": item.path,
                "line": item.line,
                "name": item.name,
                "kind": item.kind,
                "reference_count": item.reference_count,
                "classification": item.classification,
            }
            for item in possible_unused
        ],
        "notes": [
            "Hits are metadata-only and do not echo source contents.",
            "Possible-unused definitions are static heuristics and require follow-up verification before deletion.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT), help="Repository root. Defaults to this checkout.")
    parser.add_argument("--max-hits-per-pattern", type=int, default=25)
    parser.add_argument("--max-possible-unused", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(
        Path(args.root),
        max_hits_per_pattern=max(args.max_hits_per_pattern, 0),
        max_possible_unused=max(args.max_possible_unused, 0),
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
