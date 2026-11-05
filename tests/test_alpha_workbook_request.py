"""Exercise the maintained smoke's expectations with the browser request schema."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile
import xml.etree.ElementTree as ET

from webapp import server
from scripts.verify_current_workbook import NS, verify_workbook

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/quote-generator"
REFERENCE = FIXTURES / "pricing-references/synthetic-exhibition-fixture-pricing"


class AlphaWorkbookRequestTest(unittest.TestCase):
    def test_verifier_error_keeps_final_exception_without_traceback_source(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/playwright-alpha-smoke.mjs', 'utf8');
const start = source.indexOf('function safeErrorText(');
const end = source.indexOf('async function waitUntil(', start);
assert(start >= 0 && end > start);
const scope = {};
vm.runInNewContext(source.slice(start, end) + '\nglobalThis.subject = safeErrorText;', scope);
const traceback = 'Traceback (most recent call last):\r\n' + 'source context '.repeat(100)
  + '\r\nKeyError: \'currency\'\r\n';
assert.equal(scope.subject(traceback, true), "KeyError: 'currency'");
assert.equal(scope.subject('AssertionError: ' + 'x'.repeat(500), true).length, 400);
assert.equal(scope.subject('short diagnostic'), 'short diagnostic');
for (const result of ['strictXlsx', 'strictCurrentXlsx']) {
  assert(source.includes(`safeErrorText(${result}.stderr || ${result}.stdout, true)`));
}
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_smoke_request_expectations_verify_real_generator_workbook(self):
        catalog = json.loads((REFERENCE / "pricing-catalog.json").read_text(encoding="utf-8"))
        usable = [item for item in catalog["items"] if item.get("id") and item.get("description")
                  and item.get("section") and item.get("unit_hint")
                  and isinstance(item.get("sale_unit_price", item.get("default_quote_amount")), (int, float))]
        first = next((item for item in usable if item["unit_hint"] == "sqm"), usable[0])
        second = next(item for item in usable if item["id"] != first["id"] and item["section"] != first["section"])
        items = [{**item, "price": item.get("sale_unit_price", item["default_quote_amount"])} for item in (first, second)]
        logo = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
        payload = {
            "profile_id": "synthetic-exhibition-fixture-template",
            "pricing_reference_id": REFERENCE.name, "pricing_reference_source": "local",
            "pricing_reference": {"id": REFERENCE.name, "source": "local", "currency": "SGD",
                                  "tax": {"label": "GST", "rate": 0.09}},
            "confirmed": True, "quote_date": "2026-06-06", "project_number": "SYNTHETIC-ALPHA",
            "client": {"name": "Synthetic Alpha Customer", "attention": "Alpha Review Team",
                       "title": "Synthetic Project Lead", "address": "Synthetic workspace\nSingapore"},
            "project": {"title": "Synthetic Internal Exhibition Alpha"},
            "company": {"name": "Synthetic Alpha Fabrication", "header_details": "Synthetic Internal Alpha | Singapore",
                        "logo_data_url": "data:image/png;base64," + base64.b64encode(logo).decode("ascii"),
                        "logo_name": "synthetic-alpha-logo.png", "logo_type": "image/png", "logo_size": len(logo)},
            "tax": {"label": "GST", "rate": 0.09}, "quote_tax": {"label": "GST", "rate": 0.09},
            "quote_currency": "SGD", "quote_exchange_rate": 1,
            "quote_text": {"acceptance_text": "Synthetic approval for testing", "person_label": "Name:",
                           "stamp_label": "Company stamp", "date_label": "Date:"},
            "signature": {"company_signatory": "Synthetic Authorized Signatory", "company_title": "Project Manager",
                          "company_date_label": "Date:"},
            "line_items": [{"section": item["section"], "description": item["description"],
                            "quantity": 2 if index == 0 else 1, "unit": item["unit_hint"],
                            "pricing_keyword": item["id"], "price_mode": "Priced" if index == 0 else "Included",
                            "output_unit_locked": True,
                            **({"unit_price_override": item["price"]} if index == 0 else {"display_price": "Included"})}
                           for index, item in enumerate(items)],
        }
        # Use the real mapper/normaliser with an accepted explicit price from
        # fixture data. Saved-session catalogue authority is covered by the smoke.
        with mock.patch.object(server, "profiles_root", return_value=FIXTURES / "profiles"), \
             mock.patch.object(server, "pricing_references_root", return_value=FIXTURES / "pricing-references"):
            payload["line_items"] = server.normalize_line_items(payload)
            for item in payload["line_items"]:
                item["output_unit_locked"] = True
            brief = server.payload_to_brief(payload)
        self.assertEqual([item["pricing_authority"]["variant"] for item in payload["line_items"]], ["manual", "included"])
        self.assertNotIn("currency", payload)
        self.assertNotIn("header_lines", payload["company"])

        # Execute the actual smoke expression so request-to-verifier mapping cannot drift.
        source = (ROOT / "scripts/playwright-alpha-smoke.mjs").read_text(encoding="utf-8")
        start = source.index("    const expectedWorkbook = {")
        end = source.index("    // Keep uploaded media", start)
        expression = source[start:end]
        script = "const {createHash} = require('node:crypto');\n" + \
            "const xlsxJobRequest = " + json.dumps(payload) + ";\n" + \
            "const fixtureItems = " + json.dumps(items) + ";\n" + \
            "const logoBuffer = Buffer.from(" + json.dumps(base64.b64encode(logo).decode("ascii")) + ", 'base64');\n" + \
            expression + "\nprocess.stdout.write(JSON.stringify(expectedWorkbook));"
        expected = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(expected.returncode, 0, expected.stderr)
        for actual, row in zip(json.loads(expected.stdout)["payload"]["line_items"], json.loads(expected.stdout)["rows"]):
            self.assertEqual({key: actual[key] for key in ("description", "quantity", "unit", "price_mode")},
                             {key: row[key] for key in ("description", "quantity", "unit", "price_mode")})
        self.assertEqual(json.loads(expected.stdout)["logo"], {"bytes": len(logo), "sha256": hashlib.sha256(logo).hexdigest()})

        (ROOT / "_tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="alpha-request-", dir=ROOT / "_tmp") as temporary:
            directory = Path(temporary)
            # Explicit LF JSON and forward-slash CLI paths match Linux CI transport.
            (directory / "brief.json").write_bytes((json.dumps(brief, indent=2) + "\n").encode("utf-8"))
            (directory / "expected.json").write_bytes((expected.stdout + "\n").encode("utf-8"))
            generated = subprocess.run([sys.executable, "scripts/generate_quote.py", "--brief", (directory / "brief.json").as_posix(),
                                        "--out", directory.as_posix(), "--template", (REFERENCE / "pricing-catalog.json").as_posix(),
                                        "--layout-template", (ROOT / "templates/quote-layout/quotation-layout.xlsx").as_posix(),
                                        "--pdf-mode", "none"], cwd=ROOT, text=True, capture_output=True, timeout=20)
            self.assertEqual(generated.returncode, 0, generated.stderr + generated.stdout)
            verified = subprocess.run([sys.executable, "scripts/verify_current_workbook.py",
                                       (directory / "quotation.xlsx").as_posix(), (directory / "expected.json").as_posix()],
                                      cwd=ROOT, text=True, capture_output=True, timeout=15)
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(json.loads(verified.stdout), {"status": "valid", "rows_bound": 2, "totals_bound": 3, "original_logo": True})

            expectations = json.loads(expected.stdout)
            workbook = (directory / "quotation.xlsx").read_bytes()
            with self.assertRaisesRegex(AssertionError, "Generation rows differ from accepted edits"):
                verify_workbook(workbook, payload, expectations["rows"], expectations["logo"])
            with self.assertRaisesRegex(KeyError, "currency"):
                verify_workbook(workbook, {**payload, "line_items": expectations["payload"]["line_items"]},
                                expectations["rows"], expectations["logo"])
            # Corrupt the wrapped unit, then mark its continuation as another
            # item. Neither a wrong suffix nor text borrowed across rows passes.
            for corruption in ("unit", "boundary", "alignment"):
                with self.subTest(corruption=corruption):
                    output = io.BytesIO()
                    with zipfile.ZipFile(io.BytesIO(workbook)) as original, zipfile.ZipFile(output, "w") as changed:
                        for name in original.namelist():
                            data = original.read(name)
                            if name == "xl/worksheets/sheet1.xml":
                                tree = ET.fromstring(data)
                                cell = next(cell for cell in tree.findall(".//m:c", NS)
                                            if cell.get("r").startswith("B") and "".join(cell.itertext()) == "length")
                                if corruption == "unit":
                                    cell.find(".//m:t", NS).text = "wrong-unit"
                                elif corruption == "boundary":
                                    row = next(row for row in tree.findall(".//m:row", NS) if cell in list(row))
                                    marker = ET.SubElement(row, "{" + NS["m"] + "}c", {"r": "A" + row.get("r")})
                                    ET.SubElement(marker, "{" + NS["m"] + "}v").text = "99"
                                else:
                                    amount = next(cell for cell in tree.findall(".//m:c", NS)
                                                  if cell.get("r").startswith("E") and cell.find("m:f", NS) is not None)
                                    cell.set("s", amount.get("s"))
                                data = ET.tostring(tree)
                            changed.writestr(name, data)
                    with self.assertRaisesRegex(AssertionError, "Wrong quantity/unit|Quantity crosses an item boundary|Quantity is not centred"):
                        verify_workbook(output.getvalue(), expectations["payload"], expectations["rows"], expectations["logo"])


if __name__ == "__main__":
    unittest.main()
