"""Output step: greyed Download Excel and View PDF say why, and the status follows Quote Basis (#443).

Runs the production browser functions in Node with synthetic state. File freshness, row
validation and pricing stats are inputs to the functions under test, so they are set per case.
"""
import subprocess
import unittest

from tests.test_webapp import ROOT, require_node

SCRIPT = r"""
const fs = require("fs");
const assert = require("assert");
const source = fs.readFileSync("webapp/static/app.js", "utf8");
function extract(name) {
  const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
  assert.ok(match, `Missing production function ${name}`);
  const body = source.indexOf(") {", match.index) + 2;
  let depth = 0;
  for (let i = body; i < source.length; i++) {
    if (source[i] === "{") depth++;
    if (source[i] === "}" && --depth === 0) return source.slice(match.index, i + 1);
  }
  throw new Error(`Unclosed production function ${name}`);
}

function button() {
  const attributes = {};
  return { title: "", href: "", download: "", textContent: "", tabIndex: 0,
    classList: { values: new Set(), toggle(name, on) { on ? this.values.add(name) : this.values.delete(name); } },
    setAttribute(name, value) { attributes[name] = value; }, getAttribute(name) { return attributes[name]; } };
}
const elements = { sideDownloadButton: button(), sideViewPdfButton: button() };
let state = {};
let rowsValid = true;
let reviewRequired = false;
let freshXlsx = false;
let freshPdf = false;
let pendingRows = 0;
function outputRowsValid() { return { valid: rowsValid, errors: [] }; }
function quoteCommercialReviewRequired() { return reviewRequired; }
function downloadFileIsFresh() { return freshXlsx; }
function pdfFileIsFresh() { return freshPdf; }
function matchSummaryStats() { return { totalPending: pendingRows }; }
eval(["downloadButtonBlockReason", "updateDownloadButton", "outputHeaderStatus"].map(extract).join("\n"));

function check(name, setup, expected) {
  state = { activeSidePanel: "output", isGenerating: false, isPreparingOutput: false, quoteViewOnly: false,
    downloadFile: { url: "/synthetic.xlsx", name: "quotation.xlsx" }, pdfFile: { url: "/synthetic.pdf" } };
  rowsValid = true; reviewRequired = false; freshXlsx = true; freshPdf = true;
  setup();
  updateDownloadButton();
  const { sideDownloadButton: excel, sideViewPdfButton: pdf } = elements;
  assert.deepStrictEqual(
    { excel: [excel.title, excel.getAttribute("aria-disabled")], pdf: [pdf.title, pdf.getAttribute("aria-disabled")] },
    expected, name);
}

check("ready", () => {}, { excel: ["", "false"], pdf: ["", "false"] });
check("a row needs a price", () => { rowsValid = false; },
  { excel: ["Price every row first.", "true"], pdf: ["Price every row first.", "true"] });
check("commercial review", () => { reviewRequired = true; },
  { excel: ["Check the quote's commercial details first.", "true"], pdf: ["Check the quote's commercial details first.", "true"] });
check("preparing", () => { state.isPreparingOutput = true; },
  { excel: ["Preparing the quote...", "true"], pdf: ["Preparing the quote...", "true"] });
check("generating", () => { state.isGenerating = true; },
  { excel: ["Preparing the quote...", "true"], pdf: ["Preparing the quote...", "true"] });
check("view only with a workbook but no PDF", () => { state.quoteViewOnly = true; freshPdf = false; },
  { excel: ["", "false"], pdf: ["Only an existing file can be opened in a view-only quote.", "true"] });
check("not on Output", () => { state.activeSidePanel = "basis"; rowsValid = false; },
  { excel: ["", "true"], pdf: ["", "true"] });

// The Output status says Ready only once Quote Basis is confirmed.
state = { basisConfirmed: true };
pendingRows = 0;
assert.strictEqual(outputHeaderStatus([{ description: "Synthetic row" }]).label, "Ready");
pendingRows = 1;
assert.strictEqual(outputHeaderStatus([{ description: "Synthetic row" }]).label, "Needs pricing");
state = { basisConfirmed: false };
pendingRows = 0;
assert.deepStrictEqual(outputHeaderStatus([{ description: "Synthetic row" }]), { label: "Confirm Quote Basis", className: "is-warn" });
assert.strictEqual(outputHeaderStatus([]).label, "Draft");
console.log("ok");
"""


class OutputButtonReasonsTest(unittest.TestCase):
    def test_greyed_downloads_say_why_and_status_follows_quote_basis(self):
        completed = subprocess.run([require_node(self), "-e", SCRIPT], cwd=str(ROOT), text=True,
                                   capture_output=True, check=False, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(completed.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
