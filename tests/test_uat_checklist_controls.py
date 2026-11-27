"""The release UAT checklist control check (scripts/check_uat_checklist_controls.py).

The check itself runs on the repository in the fast `Release UAT checklist controls` CI job;
these tests run it on small synthetic pages.
"""

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

# A small page modelled on the app: a step rail, a feedback form and a Dashboard panel template.
PAGE = """<nav>
  <button class="rail-button" type="button" data-side-panel="images">Upload</button>
  <button class="rail-button" type="button" id="customerDetailsButton" data-side-panel="customer">Customer</button>
</nav>
<form id="feedbackForm">
  <input id="feedbackShortTitle" type="text">
  <button class="primary-button" type="submit">Send feedback</button>
</form>
<a href="/privacy">Privacy Notice</a>
"""
SCRIPT_JS = """function panel(session) {
  return `<button class="secondary-button" type="button" data-dashboard-panel-action="duplicate-session" data-quote-session-id="${session.id}">Duplicate Quote</button>
    <article class="dashboard-session-card" data-quote-session-id="${session.id}" role="button" tabindex="0"></article>`;
}
function editorButtons(draft) {
  return [qtNode("button", { type: "button", id: "qtAddColumn", class: "secondary-button", onclick: () => draft.add() }, ["Add column"])];
}
"""
LINES = (
    ("UP-01", "`button[data-side-panel]`, `#customerDetailsButton`"),
    ("FB-01", "`#feedbackForm`, `#feedbackShortTitle`"),
    ("DB-08", "`[data-quote-session-id]`, `[data-dashboard-panel-action=\"duplicate-session\"]`"),
    ("QT-04", "`#qtAddColumn`"),
    ("SI-06", "`/privacy`"),
)


def checklist(*rows: tuple[str, str]) -> str:
    return HEADER + "".join(f"| {line_id} | Do it. | It works. | B | D | | {controls} |\n" for line_id, controls in rows)


class SyntheticRepo:
    """A throwaway repository root with the files the check reads."""

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
        return subprocess.run([sys.executable, "-I", str(SCRIPT), "--root", str(self.root)], capture_output=True, text=True, check=False)

    def close(self) -> None:
        self._dir.cleanup()


class CheckTestCase(unittest.TestCase):
    def run_check(self, html: str = PAGE, js: str = SCRIPT_JS, uat: str | None = None, allowlist: dict | None = None):
        repo = SyntheticRepo(html, js, checklist(*LINES) if uat is None else uat, allowlist)
        self.addCleanup(repo.close)
        return repo.run()

    def assertFailsNaming(self, result: subprocess.CompletedProcess[str], text: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(text, result.stdout)


class BaselineTests(CheckTestCase):
    def test_synthetic_page_with_every_control_listed_passes(self):
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("names all 8 app controls the check can read", result.stdout)

    def test_unlisted_button_on_a_synthetic_page_fails(self):
        html = PAGE + '<button type="button" id="exportButton">Export</button>\n'
        self.assertFailsNaming(self.run_check(html=html), "Unlisted control #exportButton (webapp/static/index.html:10)")

    def test_checklist_name_the_app_no_longer_has_fails(self):
        uat = checklist(*LINES, ("XX-01", "`#oldButton`, `[data-gone-action]`"))
        result = self.run_check(uat=uat)
        self.assertFailsNaming(result, "Stale control #oldButton on line XX-01")
        self.assertIn("Stale control [data-gone-action] on line XX-01", result.stdout)


class MostSpecificNameTests(CheckTestCase):
    """Review probes A to C: a name a control merely carries never covers it."""

    def test_probe_a_new_panel_action_carrying_a_listed_quote_id_fails(self):
        js = SCRIPT_JS.replace(
            ">Duplicate Quote</button>",
            '>Duplicate Quote</button>\n    <button type="button" data-dashboard-panel-action="archive-session" data-quote-session-id="${session.id}">Archive quote</button>',
        )
        self.assertFailsNaming(self.run_check(js=js), 'Unlisted control [data-dashboard-panel-action="archive-session"]')

    def test_probe_b_new_field_with_an_id_inside_a_listed_form_fails(self):
        html = PAGE.replace('<input id="feedbackShortTitle" type="text">', '<input id="feedbackShortTitle" type="text">\n  <input id="feedbackPhoneNumber" type="tel">')
        self.assertFailsNaming(self.run_check(html=html), "Unlisted control #feedbackPhoneNumber")

    def test_probe_c_new_step_button_with_its_own_id_fails(self):
        html = PAGE.replace("</nav>", '  <button class="rail-button" type="button" id="approvalButton" data-side-panel="approval">Approval</button>\n</nav>')
        self.assertFailsNaming(self.run_check(html=html), "Unlisted control #approvalButton")

    def test_action_value_needs_its_own_listing(self):
        uat = checklist(*LINES[:2], ("DB-08", "`[data-quote-session-id]`, `[data-dashboard-panel-action]`"), *LINES[3:])
        self.assertFailsNaming(self.run_check(uat=uat), 'Unlisted control [data-dashboard-panel-action="duplicate-session"]')

    def test_shared_style_class_does_not_name_a_control(self):
        # .primary-button also sits on controls with better names, so it cannot name the submit button;
        # the form names it instead, and a listing of the class alone does not cover it.
        names = [c.names for c in checker.app_controls({"index.html": PAGE + '<button class="primary-button" id="x">x</button>'})]
        self.assertIn(("#feedbackForm",), names)
        self.assertNotIn((".primary-button",), names)


class UnnamedControlTests(CheckTestCase):
    """Review probes H, K and L: controls the scan finds but cannot name fail."""

    def test_probe_h_new_quote_template_editor_button_fails(self):
        js = SCRIPT_JS.replace(
            '["Add column"])]',
            '["Add column"]), qtNode("button", { type: "button", id: "qtResetColumns", class: "secondary-button", onclick: () => draft.reset() }, ["Reset columns"])]',
        )
        self.assertFailsNaming(self.run_check(js=js), "Unlisted control #qtResetColumns (webapp/static/app.js:6)")

    def test_probe_k_bare_button_fails(self):
        html = PAGE + '<button type="button">Export all</button>\n'
        self.assertFailsNaming(self.run_check(html=html), "Control the check cannot name: <button> with no name in webapp/static/index.html")

    def test_probe_l_bare_external_link_fails(self):
        html = PAGE + '<a href="https://example.com/help" target="_blank">Help</a>\n'
        self.assertFailsNaming(self.run_check(html=html), "Control the check cannot name: <a> with no name in webapp/static/index.html (no allow-list entry; at webapp/static/index.html:10)")

    def test_id_joined_in_a_javascript_string_is_not_a_name(self):
        js = SCRIPT_JS + "function help() { return '<button type=\"button\" id=\"' + helpId + '\">Help</button>'; }\n"
        self.assertFailsNaming(self.run_check(js=js), "Control the check cannot name: <button> with no name in webapp/static/app.js")

    def test_created_element_kind_is_pinned_to_its_count(self):
        js = SCRIPT_JS + 'function save(url) { const link = document.createElement("a"); link.href = url; link.click(); }\n'
        kind = 'document.createElement("a") in webapp/static/app.js'
        self.assertFailsNaming(self.run_check(js=js), f"Control the check cannot name: {kind} (no allow-list entry")
        allowed = {"unnamed": {kind: {"count": 1, "reason": "A hidden link clicked to save a file."}}}
        self.assertEqual(self.run_check(js=js, allowlist=allowed).returncode, 0)
        js_two = js + 'function open(url) { const link = document.createElement("a"); link.href = url; link.click(); }\n'
        self.assertFailsNaming(self.run_check(js=js_two, allowlist=allowed), "the allow-list counts 1, the app has 2")

    def test_focusable_cell_counts_as_a_control(self):
        js = SCRIPT_JS + 'const cell = `<td data-output-edit-field="${field}" data-output-row="${i}" tabindex="0">x</td>`;\n'
        self.assertFailsNaming(self.run_check(js=js), "Unlisted control [data-output-edit-field] [data-output-row]")


class ChecklistTokenTests(CheckTestCase):
    def test_unread_token_fails(self):
        uat = checklist(*LINES, ("QT-08", "`qtMore-*`"))
        self.assertFailsNaming(self.run_check(uat=uat), "Unread name `qtMore-*` on line QT-08")

    def test_prefix_id_names_a_run_time_id(self):
        js = SCRIPT_JS + 'const more = qtNode("button", { type: "button", id: `qtMore-${template.id}` }, ["More"]);\n'
        self.assertFailsNaming(self.run_check(js=js), "Unlisted control #qtMore-*")
        uat = checklist(*LINES, ("QT-08", "`#qtMore-*`"))
        self.assertEqual(self.run_check(js=js, uat=uat).returncode, 0)


class AllowListTests(CheckTestCase):
    def test_allow_list_entry_excuses_a_control_and_needs_a_reason(self):
        html = PAGE + '<button type="button" id="debugButton">Debug</button>\n'
        allowed = self.run_check(html=html, allowlist={"unlisted": {"#debugButton": "Shown only in local debug builds."}})
        self.assertEqual(allowed.returncode, 0, allowed.stdout)
        self.assertFailsNaming(self.run_check(html=html, allowlist={"unlisted": {"#debugButton": " "}}), "unlisted entry #debugButton has no reason")

    def test_allow_list_entry_no_longer_needed_fails(self):
        allowlist = {
            "unlisted": {"#removedButton": "Was a test-only control."},
            "stale": {"#customerDetailsButton": "Built at run time."},
            "unnamed": {'<button> with no name in webapp/static/index.html': {"count": 1, "reason": "Gone."}},
        }
        result = self.run_check(allowlist=allowlist)
        self.assertFailsNaming(result, "Allow-list entry unlisted #removedButton is no longer needed")
        self.assertIn("Allow-list entry stale #customerDetailsButton is no longer needed", result.stdout)
        self.assertIn("Allow-list entry unnamed <button> with no name in webapp/static/index.html is no longer needed", result.stdout)

    def test_repository_allow_list_is_well_formed(self):
        allowlist, problems = checker.load_allowlist(PROJECT_ROOT / checker.ALLOWLIST)
        self.assertEqual(problems, [])
        for kind, entry in allowlist["unnamed"].items():
            self.assertGreater(entry["count"], 0, kind)


if __name__ == "__main__":
    unittest.main()
