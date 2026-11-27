#!/usr/bin/env python3
"""Keep the release UAT checklist in step with the app's controls.

Reads every interactive control the app defines (buttons, links, inputs,
selects, text areas, editable areas and elements with a button-like role) in
``webapp/static/index.html`` and in the HTML templates inside
``webapp/static/app.js``, and the ``Controls`` column of
``docs/internal-uat.md``. It fails when:

- a control has no checklist line (an unlisted control),
- a checklist line names a control that no longer exists (a stale name), or
- an allow-list entry is no longer needed or has no reason.

A control's names are its literal ``id`` (``#saveButton``) and each of its
data attributes (``[data-side-panel]``); an action attribute (name ending in
``-action`` or ``-command``) with a fixed value is named with that value
(``[data-dashboard-panel-action="duplicate-session"]``). A checklist line
covers a control when its Controls column names any of these; a name without
a value covers every value. A hidden field that only backs a rich-text editor
(named by the editor's ``data-rich-text-source``) is not a control: the editor
is. Deliberate exceptions live in ``docs/internal-uat-controls-allowlist.json``
under ``unlisted`` (controls with no line) or ``stale`` (names the app builds
in a way this scan cannot read), each with a reason.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("webapp/static/index.html", "webapp/static/app.js")
CHECKLIST = "docs/internal-uat.md"
ALLOWLIST = "docs/internal-uat-controls-allowlist.json"

CONTROL_TAGS = {"a", "button", "input", "select", "textarea"}
CONTROL_ROLES = {"button", "checkbox", "link", "menuitem", "radio", "switch", "tab"}
TAG_START = re.compile(r"<([A-Za-z][A-Za-z0-9-]*)\b")
TAG_OR_END = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)\b")
ATTRIBUTE = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
PLACEHOLDER = "\x00"
TOKEN = re.compile(r"`([^`]+)`")
LINE_ID = re.compile(r"^[A-Z]+-\d+$")
SELECTOR = re.compile(
    r"""^(?:[a-z]+)?(?:#(?P<id>[A-Za-z][-A-Za-z0-9_:.]*)|\.(?P<cls>[A-Za-z][-A-Za-z0-9_]*)"""
    r"""|\[(?P<attr>data-[-a-z0-9]+)(?:=["'](?P<value>[^"']*)["'])?\]|(?P<path>/[-a-z0-9/_.]*))$"""
)


@dataclass(frozen=True)
class Control:
    names: tuple[str, ...]
    source: str
    line: int

    @property
    def key(self) -> str:
        return self.names[0]


@dataclass
class Report:
    controls: int = 0
    unlisted: list[Control] = field(default_factory=list)
    stale: list[tuple[str, str]] = field(default_factory=list)
    allowlist_problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unlisted or self.stale or self.allowlist_problems)


def _tag_text(text: str, start: int) -> str:
    """Return the tag that starts at ``start`` up to its ``>``, with each ``${...}`` as a placeholder."""
    out: list[str] = []
    i = start
    quote = ""
    while i < len(text):
        if text.startswith("${", i):
            depth = 0
            while i < len(text):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            out.append(PLACEHOLDER)
            i += 1
            continue
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == ">":
            return "".join(out)
        elif ch == "<" and i != start:
            return ""
        out.append(ch)
        i += 1
    return ""


def _attributes(tag: str) -> dict[str, str | None]:
    attrs: dict[str, str | None] = {}
    for match in ATTRIBUTE.finditer(TAG_START.sub("", tag, count=1)):
        value = next((g for g in match.groups()[1:] if g is not None), None)
        attrs.setdefault(match.group(1).lower(), value)
    return attrs


def _is_control(tag_name: str, attrs: dict[str, str | None]) -> bool:
    if tag_name == "input" and (attrs.get("type") or "").lower() == "hidden":
        return False
    if tag_name in CONTROL_TAGS:
        return True
    if "contenteditable" in attrs and (attrs["contenteditable"] or "true").lower() != "false":
        return True
    return (attrs.get("role") or "").lower() in CONTROL_ROLES


def _names(attrs: dict[str, str | None], form_id: str | None) -> tuple[str, ...]:
    names = []
    element_id = attrs.get("id")
    if element_id and PLACEHOLDER not in element_id:
        names.append("#" + element_id)
    for name, value in attrs.items():
        if not name.startswith("data-"):
            continue
        if value and PLACEHOLDER not in value and name.endswith(("-action", "-command")):
            names.append(f'[{name}="{value}"]')
        else:
            names.append(f"[{name}]")
    href = attrs.get("href") or ""
    if href.startswith("/") and PLACEHOLDER not in href:
        names.append(href.split("?", 1)[0])
    # The form a control sits in, then its class names, name it only where nothing above does.
    if form_id:
        names.append("#" + form_id)
    for class_name in (attrs.get("class") or "").split():
        if PLACEHOLDER not in class_name:
            names.append("." + class_name)
    return tuple(names)


def app_controls(sources: dict[str, str]) -> list[Control]:
    found: dict[tuple[str, ...], Control] = {}
    backing_fields: set[str] = set()
    for source, text in sources.items():
        forms: list[str | None] = []
        for match in TAG_OR_END.finditer(text):
            if match.group(1):
                if match.group(2).lower() == "form" and forms:
                    forms.pop()
                continue
            tag_name = match.group(2).lower()
            tag = _tag_text(text, match.start())
            if not tag:
                continue
            attrs = _attributes(tag)
            if tag_name == "form":
                form_id = attrs.get("id")
                forms.append(form_id if form_id and PLACEHOLDER not in form_id else None)
                continue
            if attrs.get("data-rich-text-source"):
                backing_fields.add("#" + attrs["data-rich-text-source"])
            if not _is_control(tag_name, attrs):
                continue
            names = _names(attrs, forms[-1] if forms else None)
            if names:
                line = text.count("\n", 0, match.start()) + 1
                found.setdefault(names, Control(names, source, line))
    controls = [c for c in found.values() if c.key not in backing_fields]
    return sorted(controls, key=lambda c: (c.key, c.source, c.line))


def checklist_names(checklist: str) -> dict[str, str]:
    """Map each control the checklist's Controls column names to the first line naming it."""
    names: dict[str, str] = {}
    for row in checklist.splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != 7 or not LINE_ID.match(cells[0]):
            continue
        for token in TOKEN.findall(cells[6]):
            match = SELECTOR.match(token.strip())
            if match:
                names.setdefault(_canonical(match), cells[0])
    return names


def _canonical(match: re.Match[str]) -> str:
    if match.group("id"):
        return "#" + match.group("id")
    if match.group("cls"):
        return "." + match.group("cls")
    if match.group("path"):
        return match.group("path")
    if match.group("value") is not None:
        return f'[{match.group("attr")}="{match.group("value")}"]'
    return f'[{match.group("attr")}]'


def _covers(listed: str, name: str) -> bool:
    if listed == name:
        return True
    if listed.startswith("[") and name.startswith("["):
        listed_attr = listed[1:-1].split("=", 1)[0]
        name_attr = name[1:-1].split("=", 1)[0]
        # A name without a value covers every value, and one with a value covers the plain attribute.
        return listed_attr == name_attr and ("=" not in listed or "=" not in name)
    return False


def _exists(listed: str, all_text: str) -> bool:
    match = SELECTOR.match(listed)
    if match.group("path"):
        # A page path may be served by the server rather than linked from these files.
        return True
    if match.group("id"):
        element_id = re.escape(match.group("id"))
        return re.search(r"""(?:\bid\s*=\s*|\.id\s*=\s*)["'`]""" + element_id + r"""["'`]""", all_text) is not None
    if match.group("cls"):
        class_name = re.escape(match.group("cls"))
        return re.search(r"""(?<![-\w])""" + class_name + r"""(?![-\w])""", all_text) is not None
    attr = match.group("attr")
    camel = re.sub(r"-([a-z0-9])", lambda m: m.group(1).upper(), attr[len("data-"):])
    if not re.search(re.escape(attr) + r"(?![-a-z0-9])|\bdataset\." + re.escape(camel) + r"\b", all_text):
        return False
    value = match.group("value")
    return value is None or re.search(r"""["'`]""" + re.escape(value) + r"""["'`]""", all_text) is not None


def load_allowlist(path: Path) -> tuple[dict[str, str], dict[str, str], list[str]]:
    if not path.exists():
        return {}, {}, []
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = [f"{path.name}: unknown section {key}" for key in data if key not in {"unlisted", "stale"}]
    sections = []
    for section in ("unlisted", "stale"):
        entries = data.get(section, {})
        for key, reason in entries.items():
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"{path.name}: {section} entry {key} has no reason")
        sections.append(dict(entries))
    return sections[0], sections[1], problems


def check(sources: dict[str, str], checklist: str, allow_unlisted: dict[str, str], allow_stale: dict[str, str]) -> Report:
    controls = app_controls(sources)
    report = Report(controls=len(controls))
    listed = checklist_names(checklist)
    all_text = "\n".join(sources.values())
    used_unlisted: set[str] = set()
    for control in controls:
        if any(_covers(name, alias) for name in listed for alias in control.names):
            continue
        allowed = [alias for alias in control.names if alias in allow_unlisted]
        if allowed:
            used_unlisted.update(allowed)
            continue
        report.unlisted.append(control)
    used_stale: set[str] = set()
    for name, line_id in sorted(listed.items()):
        if _exists(name, all_text):
            continue
        if name in allow_stale:
            used_stale.add(name)
            continue
        report.stale.append((name, line_id))
    for key in sorted(set(allow_unlisted) - used_unlisted):
        report.allowlist_problems.append(f"Allow-list entry unlisted {key} is no longer needed: remove it.")
    for key in sorted(set(allow_stale) - used_stale):
        report.allowlist_problems.append(f"Allow-list entry stale {key} is no longer needed: remove it.")
    return report


def run(root: Path) -> tuple[int, list[str]]:
    sources = {name: (root / name).read_text(encoding="utf-8") for name in SOURCES}
    checklist = (root / CHECKLIST).read_text(encoding="utf-8")
    allow_unlisted, allow_stale, problems = load_allowlist(root / ALLOWLIST)
    report = check(sources, checklist, allow_unlisted, allow_stale)
    report.allowlist_problems[:0] = problems
    out = []
    for control in report.unlisted:
        out.append(
            f"Unlisted control {' '.join(control.names)} ({control.source}:{control.line}): name it in the "
            f"Controls column of the {CHECKLIST} line that uses it, or add it to {ALLOWLIST} with a reason."
        )
    for name, line_id in report.stale:
        out.append(f"Stale control {name} on line {line_id} of {CHECKLIST}: the app no longer has it. Update the line.")
    out.extend(report.allowlist_problems)
    if report.ok:
        out.append(f"The release UAT checklist names all {report.controls} app controls.")
        return 0, out
    return 1, out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep the release UAT checklist in step with the app's controls.")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="repository root to check")
    parser.add_argument("--list", action="store_true", help="print every control found, then exit")
    args = parser.parse_args(argv)
    if args.list:
        sources = {name: (args.root / name).read_text(encoding="utf-8") for name in SOURCES}
        for control in app_controls(sources):
            print(f"{' '.join(control.names)}\t{control.source}:{control.line}")
        return 0
    code, lines = run(args.root)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
