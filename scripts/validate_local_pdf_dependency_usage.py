#!/usr/bin/env python3
"""Fail CI if PDF rendering dependencies are used outside the local-only path."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_SOURCE_ROOTS = (
    ROOT / "webapp",
    ROOT / "scripts",
)
SKIP_PATHS = {
    Path(__file__).resolve(),
}

PDF_LOCAL_FUNCTIONS = {
    "ai_image_data_url_from_bytes",
    "compressed_prompt_image_data_url",
    "extract_pdf_embedded_page_images",
    "pdf_reference_page_images",
    "persist_pdf_page_debug_images",
    "prompt_image_data_url_from_pil",
    "render_pdf_pages_with_pdfium",
}

FORBIDDEN_PIL_IMPORTS = {
    "EpsImagePlugin",
    "GifImagePlugin",
    "ImageGrab",
    "ImageShow",
}

FORBIDDEN_LOCAL_NAMES = {
    "ImageGrab",
    "ImageShow",
    "requests",
    "socket",
    "subprocess",
}

FORBIDDEN_LOCAL_CALL_PREFIXES = (
    "http.client",
    "os.popen",
    "os.system",
    "requests",
    "shutil.which",
    "socket",
    "subprocess",
    "urllib.request.Request",
    "urllib.request.urlopen",
)


def iter_python_sources() -> list[Path]:
    files: list[Path] = []
    for root in PRODUCTION_SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            resolved = path.resolve()
            if resolved in SKIP_PATHS:
                continue
            if any(part in {"__pycache__", ".venv", "venv"} for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def full_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = full_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return full_name(node.func)
    return ""


def node_location(path: Path, node: ast.AST) -> str:
    line = getattr(node, "lineno", 1)
    return f"{path.relative_to(ROOT)}:{line}"


def validate_imports(path: Path, tree: ast.AST) -> list[str]:
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "pypdfium2" and alias.asname == "pdfium":
                    continue
                if alias.name == "pypdfium2" or alias.name.startswith("pypdfium2."):
                    errors.append(f"{node_location(path, node)}: import pypdfium2 only as `import pypdfium2 as pdfium`.")
                if alias.name == "PIL" or alias.name.startswith("PIL."):
                    errors.append(f"{node_location(path, node)}: import Pillow only as `from PIL import Image`.")

        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "PIL":
                for alias in node.names:
                    if alias.name != "Image":
                        errors.append(f"{node_location(path, node)}: Pillow import `{alias.name}` is not allowed; use only `from PIL import Image`.")
                    if alias.name in FORBIDDEN_PIL_IMPORTS:
                        errors.append(f"{node_location(path, node)}: Pillow module `{alias.name}` can invoke non-local system features.")
            elif module.startswith("PIL."):
                errors.append(f"{node_location(path, node)}: Pillow submodule imports are not allowed; use only `from PIL import Image`.")

            if module == "pypdfium2" or module.startswith("pypdfium2."):
                errors.append(f"{node_location(path, node)}: pypdfium2 submodule imports are not allowed; use `import pypdfium2 as pdfium`.")
    return errors


def is_io_bytesio_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and full_name(node.func) == "io.BytesIO"


def validate_local_function(path: Path, function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    errors: list[str] = []
    for node in ast.walk(function):
        if node is function:
            continue
        name = full_name(node)
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_LOCAL_NAMES:
            errors.append(f"{node_location(path, node)}: `{node.id}` is not allowed inside local PDF/Pillow rendering functions.")
        if isinstance(node, ast.Attribute):
            if name == "Image.show" or name.endswith(".show"):
                errors.append(f"{node_location(path, node)}: image display helpers are not allowed in local PDF/Pillow rendering functions.")
            for prefix in FORBIDDEN_LOCAL_CALL_PREFIXES:
                if name == prefix or name.startswith(f"{prefix}."):
                    errors.append(f"{node_location(path, node)}: `{name}` is not allowed inside local PDF/Pillow rendering functions.")
        if isinstance(node, ast.Call):
            call_name = full_name(node.func)
            if call_name == "Image.open":
                if not node.args or not is_io_bytesio_call(node.args[0]):
                    errors.append(f"{node_location(path, node)}: `Image.open` must read from `io.BytesIO(...)`, not from local paths or URLs.")
            for prefix in FORBIDDEN_LOCAL_CALL_PREFIXES:
                if call_name == prefix or call_name.startswith(f"{prefix}."):
                    errors.append(f"{node_location(path, node)}: `{call_name}` is not allowed inside local PDF/Pillow rendering functions.")
    return errors


def validate_local_functions(path: Path, tree: ast.AST) -> list[str]:
    errors: list[str] = []
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if path == ROOT / "webapp" / "server.py":
        missing = sorted(PDF_LOCAL_FUNCTIONS - set(functions))
        for name in missing:
            errors.append(f"{path.relative_to(ROOT)}: expected local PDF/Pillow function `{name}` was not found.")
    for name in sorted(PDF_LOCAL_FUNCTIONS & set(functions)):
        errors.extend(validate_local_function(path, functions[name]))
    return errors


def main() -> int:
    errors: list[str] = []
    for path in iter_python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"{path.relative_to(ROOT)}:{exc.lineno}: Python syntax error: {exc.msg}")
            continue
        errors.extend(validate_imports(path, tree))
        errors.extend(validate_local_functions(path, tree))

    if errors:
        print("Local PDF dependency usage guard failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Local PDF dependency usage guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
