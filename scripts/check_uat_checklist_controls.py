#!/usr/bin/env python3
"""Keep the release UAT checklist in step with the app's controls.

Reads every interactive control the app defines in ``webapp/static/index.html``
and ``webapp/static/app.js``: HTML tags (buttons, links, inputs, selects, text
areas, editable areas, focusable elements with ``tabindex`` 0 or more, and
elements with a button-like role) in the page and in the HTML templates inside
app.js, controls the Quote Templates editor builds
with ``qtNode("<tag>", {...})``, and controls made with
``document.createElement("<tag>")``. It also reads the ``Controls`` column of
``docs/internal-uat.md``. It fails when:

- a control has no checklist line naming it (an unlisted control),
- a checklist line names a control that no longer exists (a stale name),
- a Controls token is not a name this check can read,
- a control the scan finds but cannot name has no allow-list entry, or its
  kind's count changed, or
- an allow-list entry is no longer needed or has no reason.

A control is covered only by its most specific name, the first of these it
has: its id (``#saveButton``; a computed id with a fixed start reads as
``#qtMore-*``); its action attributes (data attributes ending in ``-action`` or
``-command``, with the value where it is fixed:
``[data-dashboard-panel-action="duplicate-session"]``); its other data
attributes with a fixed value or none (``[data-dashboard-select]``); its data
attributes that carry a computed value (``[data-remove-image]``); its link path
(``/privacy``); the id of the form it sits in; its own classes (a class that
a control with a more specific name also wears is a style, not a name). A name a control
merely carries (a quote id, its form, a shared class) never covers it when a
more specific name exists. A checklist name without a value covers a data
attribute name without a value.

What the scan cannot name: a control with none of those names (for example
the Output table editors, whose attributes are joined from a string variable,
or a bare ``<button>``; a value joined in a JavaScript string counts as
computed), a ``qtNode`` call with no readable name, and every
``document.createElement`` control. Each kind needs an entry under ``unnamed``
in ``docs/internal-uat-controls-allowlist.json`` with its exact count and a
reason, so a new one fails until someone looks at it. The same file holds
``unlisted`` (controls with no line) and ``stale`` (names the scan cannot find)
exceptions, each with a reason. A hidden field that only backs a rich-text
editor (named by the editor's ``data-rich-text-source``) is not a control: the
editor is. Messages and the testing plan are not read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("webapp/static/index.html", "webapp/static/app.js")
CHECKLIST = "docs/internal-uat.md"
ALLOWLIST = "docs/internal-uat-controls-allowlist.json"

CONTROL_TAGS = {"a", "button", "input", "select", "textarea"}
CONTROL_ROLES = {"button", "checkbox", "link", "menuitem", "radio", "switch", "tab"}
TAG_OR_END = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9-]*)\b")
ATTRIBUTE = re.compile(r"""([A-Za-z_:][-A-Za-z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
QT_NODE = re.compile(r"""\bqtNode\(\s*["'](a|button|input|select|textarea)["']\s*,\s*""")
CREATE_ELEMENT = re.compile(r"""\bdocument\.createElement\(\s*["'](a|button|input|select|textarea)["']\s*\)""")
PLACEHOLDER = "\x00"
JOINED = re.compile(r"""(['"])\s*\+.*?\+\s*\1""")
TOKEN = re.compile(r"`([^`]+)`")
LINE_ID = re.compile(r"^[A-Z]+-\d+$")
SELECTOR = re.compile(
    r"""^(?:[a-z]+)?(?:#(?P<id>[A-Za-z][-A-Za-z0-9_:.]*(?:\*)?)|\.(?P<cls>[A-Za-z][-A-Za-z0-9_]*)"""
    r"""|\[(?P<attr>data-[-a-z0-9]+)(?:=["'](?P<value>[^"']*)["'])?\]|(?P<path>/[-a-z0-9/_.]*))$"""
)


@dataclass(frozen=True)
class Control:
    names: tuple[str, ...]  # the most specific names: the ones that can cover it
    source: str
    line: int
    kind: str = ""  # set when the scan cannot name the control

    @property
    def key(self) -> str:
        return self.names[0] if self.names else self.kind


@dataclass
class Report:
    controls: int = 0
    unlisted: list[Control] = field(default_factory=list)
    unnamed: list[tuple[str, list[Control], str]] = field(default_factory=list)
    stale: list[tuple[str, str]] = field(default_factory=list)
    unread_tokens: list[tuple[str, str]] = field(default_factory=list)
    allowlist_problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.unlisted or self.unnamed or self.stale or self.unread_tokens or self.allowlist_problems)


def _skip_placeholder(text: str, i: int) -> int:
    """Return the index just past the ``${...}`` that starts at ``i``."""
    depth = 0
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _tag_text(text: str, start: int) -> str:
    """Return the tag that starts at ``start`` up to its ``>``, with each ``${...}`` as a placeholder."""
    out: list[str] = []
    i = start
    quote = ""
    while i < len(text):
        if text.startswith("${", i):
            i = _skip_placeholder(text, i)
            out.append(PLACEHOLDER)
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
    body = re.sub(r"^<[A-Za-z][A-Za-z0-9-]*", "", tag)
    for match in ATTRIBUTE.finditer(body):
        value = next((g for g in match.groups()[1:] if g is not None), None)
        if value is not None:
            # A value joined in a JavaScript string (`id="' + name + '"`) is computed, like `${...}`.
            value = JOINED.sub(PLACEHOLDER, value)
        attrs.setdefault(match.group(1).lower(), value)
    return attrs


def _object_literal(text: str, start: int) -> tuple[dict[str, str | None], bool]:
    """Read the top-level ``key: value`` pairs of the ``{...}`` at ``start``.

    A string value is kept (a template literal's ``${...}`` as a placeholder);
    any other value counts as computed (``PLACEHOLDER``). Returns the pairs and
    whether the literal could be read.
    """
    if start >= len(text) or text[start] != "{":
        return {}, text.startswith("null", start)
    pairs: dict[str, str | None] = {}
    i = start + 1
    depth = 0
    key = ""
    value: list[str] = []
    reading = "key"
    simple = True
    while i < len(text):
        ch = text[i]
        if ch in "\"'`":
            end = i + 1
            chunk: list[str] = []
            while end < len(text) and text[end] != ch:
                if ch == "`" and text.startswith("${", end):
                    end = _skip_placeholder(text, end)
                    chunk.append(PLACEHOLDER)
                    continue
                if text[end] == "\\":
                    end += 1
                chunk.append(text[end])
                end += 1
            literal = "".join(chunk)
            if reading == "key":
                key = literal
            elif depth == 0:
                value.append(literal)
            i = end + 1
            continue
        if ch in "([{":
            depth += 1
            simple = False
        elif ch in ")]}":
            if depth == 0:
                if key:
                    pairs[key.lower()] = "".join(value) if simple and value else PLACEHOLDER
                return pairs, True
            depth -= 1
        elif depth == 0 and ch == ":" and reading == "key":
            reading = "value"
            value = []
            simple = True
        elif depth == 0 and ch == ",":
            if key:
                pairs[key.lower()] = "".join(value) if simple and value else PLACEHOLDER
            key, value, reading, simple = "", [], "key", True
        elif reading == "key" and (ch.isalnum() or ch in "_$-"):
            key += ch
        elif reading == "value" and not ch.isspace():
            simple = False
        i += 1
    return pairs, False


def _is_control(tag_name: str, attrs: dict[str, str | None]) -> bool:
    if tag_name == "input" and (attrs.get("type") or "").lower() == "hidden":
        return False
    if tag_name in CONTROL_TAGS:
        return True
    if "contenteditable" in attrs and (attrs["contenteditable"] or "true").lower() != "false":
        return True
    if (attrs.get("tabindex") or "-1").strip() not in {"-1", PLACEHOLDER}:
        # Made focusable to act on, like the Output table's click-to-edit cells.
        return True
    return (attrs.get("role") or "").lower() in CONTROL_ROLES


def _fixed(value: str | None) -> bool:
    return value is not None and PLACEHOLDER not in value


def _classes(attrs: dict[str, str | None]) -> list[str]:
    """Return the fixed whole class names (``qt-block-${type}`` is a partial name and is skipped)."""
    names = []
    for token in (attrs.get("class") or "").split():
        fixed = token.split(PLACEHOLDER, 1)[0]
        if fixed and (PLACEHOLDER not in token or not fixed.endswith("-")):
            names.append(fixed)
    return names


def _tiers(attrs: dict[str, str | None], form_id: str | None) -> list[list[str]]:
    """Return the control's names from most to least specific; the last tier is its classes."""
    ids: list[str] = []
    element_id = attrs.get("id")
    if element_id:
        if _fixed(element_id):
            ids = ["#" + element_id]
        elif element_id.split(PLACEHOLDER, 1)[0]:
            ids = [f"#{element_id.split(PLACEHOLDER, 1)[0]}*"]
    data = [(name, value) for name, value in attrs.items() if name.startswith("data-")]
    actions = [
        f'[{name}="{value}"]' if value and _fixed(value) else f"[{name}]"
        for name, value in data
        if name.endswith(("-action", "-command"))
    ]
    plain = [f"[{name}]" for name, value in data if not name.endswith(("-action", "-command")) and (value is None or _fixed(value))]
    carried = [f"[{name}]" for name, value in data if not name.endswith(("-action", "-command")) and value is not None and not _fixed(value)]
    href = attrs.get("href") or ""
    path = [href.split("?", 1)[0]] if href.startswith("/") and _fixed(href) else []
    form = ["#" + form_id] if form_id else []
    return [ids, actions, plain, carried, path, form, ["." + c for c in _classes(attrs)]]


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


@dataclass
class _Found:
    tiers: list[list[str]]
    source: str
    line: int
    kind: str


def _scan(sources: dict[str, str]) -> tuple[list[_Found], set[str]]:
    found: list[_Found] = []
    backing_fields: set[str] = set()
    for source, text in sources.items():
        forms: list[str | None] = []
        for match in TAG_OR_END.finditer(text):
            tag_name = match.group(2).lower()
            if match.group(1):
                if tag_name == "form" and forms:
                    forms.pop()
                continue
            tag = _tag_text(text, match.start())
            line = _line(text, match.start())
            if not tag:
                if tag_name in CONTROL_TAGS:
                    found.append(_Found([], source, line, f"<{tag_name}> the scan cannot read in {source}"))
                continue
            attrs = _attributes(tag)
            if tag_name == "form":
                forms.append(attrs["id"] if _fixed(attrs.get("id")) else None)
                continue
            if _fixed(attrs.get("data-rich-text-source")):
                backing_fields.add("#" + attrs["data-rich-text-source"])
            if _is_control(tag_name, attrs):
                found.append(_Found(_tiers(attrs, forms[-1] if forms else None), source, line, f"<{tag_name}> with no name in {source}"))
        for match in QT_NODE.finditer(text):
            tag_name = match.group(1)
            attrs, readable = _object_literal(text, match.end())
            line = _line(text, match.start())
            if not readable:
                found.append(_Found([], source, line, f'qtNode("{tag_name}") the scan cannot read in {source}'))
            elif _is_control(tag_name, attrs):
                found.append(_Found(_tiers(attrs, None), source, line, f'qtNode("{tag_name}") with no name in {source}'))
        for match in CREATE_ELEMENT.finditer(text):
            found.append(_Found([], source, _line(text, match.start()), f'document.createElement("{match.group(1)}") in {source}'))
    return found, backing_fields


def app_controls(sources: dict[str, str]) -> list[Control]:
    found, backing_fields = _scan(sources)
    # A class that any control with a more specific name also wears is a style, not a name.
    style_classes = {name for f in found if any(f.tiers[:-1]) for name in f.tiers[-1]}
    controls: dict[tuple, Control] = {}
    for f in found:
        tiers = f.tiers[:-1] + [[c for c in f.tiers[-1] if c not in style_classes]] if f.tiers else []
        names = next((tuple(tier) for tier in tiers if tier), ())
        if len(names) == 1 and names[0] in backing_fields:
            continue
        key = names if names else (f.kind, f.source, f.line)
        controls.setdefault(key, Control(names, f.source, f.line, "" if names else f.kind))
    return sorted(controls.values(), key=lambda c: (c.key, c.source, c.line))


def checklist_names(checklist: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Return each name the Controls column gives (with the first line naming it) and the tokens it cannot read."""
    names: dict[str, str] = {}
    unread: list[tuple[str, str]] = []
    for row in checklist.splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) != 7 or not LINE_ID.match(cells[0]):
            continue
        for token in TOKEN.findall(cells[6]):
            match = SELECTOR.match(token.strip())
            if match:
                names.setdefault(_canonical(match), cells[0])
            else:
                unread.append((token, cells[0]))
    return names, unread


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
    if listed.startswith("[") and name.startswith("[") and "=" not in name:
        # A data attribute named without a value (a plain or carried one) is covered by any listing of that attribute.
        return listed[1:-1].split("=", 1)[0] == name[1:-1]
    return False


def _exists(listed: str, all_text: str) -> bool:
    match = SELECTOR.match(listed)
    if match.group("path"):
        # A page path may be served by the server rather than linked from these files.
        return True
    if match.group("id"):
        element_id = match.group("id")
        if element_id.endswith("*"):
            pattern = r"""\bid["']?\s*[:=]\s*["'`]""" + re.escape(element_id[:-1]) + r"""\$\{"""
        else:
            pattern = r"""(?:\bid["']?\s*[:=]\s*|\.id\s*=\s*)["'`]""" + re.escape(element_id) + r"""["'`]"""
        return re.search(pattern, all_text) is not None
    if match.group("cls"):
        return re.search(r"(?<![-\w])" + re.escape(match.group("cls")) + r"(?![-\w])", all_text) is not None
    attr = match.group("attr")
    camel = re.sub(r"-([a-z0-9])", lambda m: m.group(1).upper(), attr[len("data-"):])
    present = re.search(r"""(?<![-\w])""" + re.escape(attr) + r"""(?![-\w])|\bdataset\.""" + re.escape(camel) + r"\b", all_text)
    if not present:
        return False
    value = match.group("value")
    if value is None:
        return True
    return re.search(re.escape(attr) + r"""["']?\s*[=:]\s*["'`]""" + re.escape(value) + r"""["'`]""", all_text) is not None


def load_allowlist(path: Path) -> tuple[dict, list[str]]:
    sections: dict = {"unlisted": {}, "stale": {}, "unnamed": {}}
    if not path.exists():
        return sections, []
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = [f"{path.name}: unknown section {key}." for key in data if key not in sections]
    for section in sections:
        for key, entry in data.get(section, {}).items():
            reason = entry.get("reason") if section == "unnamed" and isinstance(entry, dict) else entry
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"{path.name}: {section} entry {key} has no reason.")
            if section == "unnamed" and not (isinstance(entry, dict) and isinstance(entry.get("count"), int)):
                problems.append(f"{path.name}: unnamed entry {key} needs a whole-number count.")
        sections[section] = dict(data.get(section, {}))
    return sections, problems


def check(sources: dict[str, str], checklist: str, allowlist: dict | None = None) -> Report:
    allowlist = allowlist or {"unlisted": {}, "stale": {}, "unnamed": {}}
    allow_unlisted = allowlist.get("unlisted", {})
    allow_stale = allowlist.get("stale", {})
    allow_unnamed = allowlist.get("unnamed", {})
    controls = app_controls(sources)
    report = Report(controls=len(controls))
    listed, report.unread_tokens = checklist_names(checklist)
    all_text = "\n".join(sources.values())

    used_unlisted: set[str] = set()
    kinds: dict[str, list[Control]] = defaultdict(list)
    for control in controls:
        if control.kind:
            kinds[control.kind].append(control)
            continue
        if any(_covers(name, own) for name in listed for own in control.names):
            continue
        allowed = [own for own in control.names if own in allow_unlisted]
        if allowed:
            used_unlisted.update(allowed)
            continue
        report.unlisted.append(control)
    for kind, members in sorted(kinds.items()):
        entry = allow_unnamed.get(kind)
        if not isinstance(entry, dict):
            report.unnamed.append((kind, members, "no allow-list entry"))
        elif entry.get("count") != len(members):
            report.unnamed.append((kind, members, f"the allow-list counts {entry.get('count')}, the app has {len(members)}"))

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
    for key in sorted(set(allow_unnamed) - set(kinds)):
        report.allowlist_problems.append(f"Allow-list entry unnamed {key} is no longer needed: remove it.")
    return report


def run(root: Path) -> tuple[int, list[str]]:
    sources = {name: (root / name).read_text(encoding="utf-8") for name in SOURCES}
    checklist = (root / CHECKLIST).read_text(encoding="utf-8")
    allowlist, problems = load_allowlist(root / ALLOWLIST)
    report = check(sources, checklist, allowlist)
    report.allowlist_problems[:0] = problems
    out = []
    for control in report.unlisted:
        out.append(
            f"Unlisted control {' '.join(control.names)} ({control.source}:{control.line}): name it in the "
            f"Controls column of the {CHECKLIST} line that uses it, or add it to {ALLOWLIST} with a reason."
        )
    for kind, members, why in report.unnamed:
        where = ", ".join(f"{c.source}:{c.line}" for c in members)
        out.append(
            f"Control the check cannot name: {kind} ({why}; at {where}). Give it an id or a data attribute and a "
            f"checklist line, or record the kind under unnamed in {ALLOWLIST} with its count and a reason."
        )
    for name, line_id in report.stale:
        out.append(f"Stale control {name} on line {line_id} of {CHECKLIST}: the app no longer has it. Update the line.")
    for token, line_id in report.unread_tokens:
        out.append(f"Unread name `{token}` on line {line_id} of {CHECKLIST}: write it as #id, #prefix-*, [data-name], [data-name=\"value\"], .class or /path.")
    out.extend(report.allowlist_problems)
    if report.ok:
        out.append(f"The release UAT checklist names all {report.controls} app controls the check can read.")
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
            print(f"{control.key}\t{control.source}:{control.line}")
        return 0
    code, lines = run(args.root)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
