"""The release UAT checklist control check (scripts/check_uat_checklist_controls.py)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "check_uat_checklist_controls.py"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import check_uat_checklist_controls as checker  # noqa: E402

HEADER = "| Line | Do | Expect | Who | Width | Eye | Controls |\n| --- | --- | --- | --- | --- | --- | --- |\n"


def checklist(*rows: tuple[str, str]) -> str:
    return HEADER + "".join(f"| {line_id} | Do it. | It works. | B | D | | {controls} |\n" for line_id, controls in rows)


class SyntheticRepo:
    """A throwaway repository root with the three files the check reads."""

    def __init__(self, html: str, js: str, uat: str, allowlist: dict | None = None):
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        (self.root / "webapp" / "static").mkdir(parents=True)
        (self.root / "docs").mkdir()
        (self.root / "webapp" / "static" / "index.html").write_text(html, encoding="utf-8")
        (self.root / "webapp" / "static" / "app.js").write_text(js, encoding="utf-8")
        (self.root / "docs" / "internal-uat.md").write_text(uat, encoding="utf-8")
        if allowlist is not None:
            (self.root / "docs" / "internal-uat-controls-allowlist.json").write_text(json.dumps(allowlist), encoding="utf-8")

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", str(SCRIPT), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def close(self) -> None:
        self._dir.cleanup()


class EndToEndTests(unittest.TestCase):
    def repo(self, html: str, js: str = "", uat: str = HEADER, allowlist: dict | None = None) -> SyntheticRepo:
        repo = SyntheticRepo(html, js, uat, allowlist)
        self.addCleanup(repo.close)
        return repo

    def test_repository_checklist_names_every_app_control(self):
        result = subprocess.run([sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("The release UAT checklist names all", result.stdout)

    def test_unlisted_button_on_a_synthetic_page_fails(self):
        html = '<button type="button" id="saveButton">Save</button>\n<button type="button" id="exportButton">Export</button>\n'
        result = self.repo(html, uat=checklist(("XX-01", "`#saveButton`"))).run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unlisted control #exportButton (webapp/static/index.html:2)", result.stdout)
        self.assertNotIn("#saveButton", result.stdout)

    def test_checklist_name_the_app_no_longer_has_fails(self):
        html = '<button type="button" id="saveButton">Save</button>\n'
        result = self.repo(html, uat=checklist(("XX-01", "`#saveButton`, `#oldButton`, `[data-gone-action]`"))).run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Stale control #oldButton on line XX-01", result.stdout)
        self.assertIn("Stale control [data-gone-action] on line XX-01", result.stdout)

    def test_allow_list_entry_excuses_a_control_and_needs_a_reason(self):
        html = '<button type="button" id="debugButton">Debug</button>\n'
        allowed = self.repo(html, allowlist={"unlisted": {"#debugButton": "Shown only in local debug builds."}}).run()
        self.assertEqual(allowed.returncode, 0, allowed.stdout)
        no_reason = self.repo(html, allowlist={"unlisted": {"#debugButton": " "}}).run()
        self.assertEqual(no_reason.returncode, 1)
        self.assertIn("unlisted entry #debugButton has no reason", no_reason.stdout)

    def test_allow_list_entry_no_longer_needed_fails(self):
        html = '<button type="button" id="saveButton">Save</button>\n'
        result = self.repo(
            html,
            uat=checklist(("XX-01", "`#saveButton`")),
            allowlist={"unlisted": {"#removedButton": "Was a test-only control."}, "stale": {"#saveButton": "Built at run time."}},
        ).run()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Allow-list entry unlisted #removedButton is no longer needed", result.stdout)
        self.assertIn("Allow-list entry stale #saveButton is no longer needed", result.stdout)


class ControlScanTests(unittest.TestCase):
    def names(self, html: str = "", js: str = "") -> list[tuple[str, ...]]:
        return [c.names for c in checker.app_controls({"index.html": html, "app.js": js})]

    def test_template_controls_in_app_js_are_named_by_data_attributes(self):
        js = (
            "const row = `<button class=\"output-delete-button\" type=\"button\" data-output-delete-row=\"${index}\""
            " aria-label=\"Delete ${label > 1 ? 'rows' : 'row'}\">Del</button>`;\n"
            "const panel = `<button type=\"button\" data-panel-action=\"duplicate-session\">Duplicate</button>`;\n"
            "const dynamic = `<button type=\"button\" data-panel-action=\"${action}\">Go</button>`;\n"
        )
        self.assertEqual(
            self.names(js=js),
            [
                ('[data-output-delete-row]', '.output-delete-button'),
                ('[data-panel-action="duplicate-session"]',),
                ('[data-panel-action]',),
            ],
        )

    def test_non_controls_hidden_inputs_and_rich_text_backing_fields_are_skipped(self):
        html = (
            '<section id="panel"><span id="label">x</span></section>\n'
            '<input type="hidden" id="token">\n'
            '<div id="noteEditor" contenteditable="true" data-rich-text-source="note"></div>\n'
            '<textarea id="note" hidden></textarea>\n'
        )
        self.assertEqual(self.names(html), [("#noteEditor", "[data-rich-text-source]")])

    def test_form_and_link_path_name_controls_without_an_id(self):
        html = '<form id="questionForm"><button class="primary-button" type="submit">Send</button></form>\n<a href="/privacy">Privacy</a>\n'
        self.assertEqual(self.names(html), [("#questionForm", ".primary-button"), ("/privacy",)])

    def test_a_name_without_a_value_covers_every_value(self):
        controls = checker.app_controls({"app.js": '`<button data-panel-action="delete">x</button>`'})
        report = checker.check({"app.js": '`<button data-panel-action="delete">x</button>`'}, checklist(("XX-01", "`[data-panel-action]`")), {}, {})
        self.assertEqual(len(controls), 1)
        self.assertTrue(report.ok, report)
        valued = checker.check({"app.js": '`<button data-panel-action="delete">x</button>`'}, checklist(("XX-01", '`[data-panel-action="open"]`')), {}, {})
        self.assertEqual([c.key for c in valued.unlisted], ['[data-panel-action="delete"]'])


if __name__ == "__main__":
    unittest.main()
