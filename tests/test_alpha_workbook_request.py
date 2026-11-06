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
from scripts import generate_quote as generator
from scripts.verify_current_workbook import NS, verify_workbook

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/quote-generator"
REFERENCE = FIXTURES / "pricing-references/synthetic-exhibition-fixture-pricing"


def workbook_adapter_source():
    source = (ROOT / "scripts/playwright-alpha-smoke.mjs").read_text(encoding="utf-8")
    start = source.index("    const workbookRow = (")
    end = source.index("    const expectedWorkbook = {", start)
    return source[start:end]


def browser_request_line_items(rows):
    """Run the actual request builder without a DOM or owned catalogue grants."""
    source = (ROOT / "webapp/static/app.js").read_text(encoding="utf-8")
    functions = []
    for name in ("normalizeUnit", "cleanCustomerQuoteLineText", "pricingReferenceLineText",
                 "numberOrNull", "orderNumber", "outputRowsToLineItems"):
        start = source.index("function " + name + "(")
        end = source.index("\nfunction ", start + 1)
        functions.append(source[start:end])
    script = "const state = {quoteCommercialLifecycle: 'NEW'};\n" + "\n".join(functions) + \
        "\nprocess.stdout.write(JSON.stringify(outputRowsToLineItems(" + json.dumps(rows) + ")));"
    result = subprocess.run(["node", "-e", script], cwd=ROOT, encoding="utf-8", capture_output=True, timeout=15)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class AlphaWorkbookRequestTest(unittest.TestCase):
    def test_both_actual_signature_collection_expressions_keep_exact_fields(self):
        source = (ROOT / "webapp/static/app.js").read_text(encoding="utf-8")
        expressions = __import__('re').findall(
            r'company_signatory: (elements\.[^\n]+),\n\s*company_title: (elements\.[^\n]+),', source)
        self.assertEqual(len(expressions), 2)
        script = "const assert = require('node:assert/strict');\n" + r'''
const elements = {companySignatory: {value: ' Synthetic Authorized Signatory '},
  companyTitle: {value: ' Project Manager '}};
''' + '\n'.join(
            "assert.deepEqual(({company_signatory: " + signatory + ", company_title: " + title +
            "}), {company_signatory: 'Synthetic Authorized Signatory', company_title: 'Project Manager'});"
            for signatory, title in expressions)
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_alpha_exact_signature_guard_rejects_combined_swaps(self):
        source = (ROOT / 'scripts/playwright-alpha-smoke.mjs').read_text(encoding='utf-8')
        start = source.index('    const expectedSignature = {')
        end = source.index('    requireExactSignature({', start)
        script = "const assert = require('node:assert/strict');\n" + r'''
const companyFields = [['#companySignatoryEditor', 'Synthetic Authorized Signatory'],
  ['#companyTitleEditor', 'Project Manager']];
''' + source[start:end] + r'''
requireExactSignature({company_signatory: 'Synthetic Authorized Signatory', company_title: 'Project Manager'});
assert.throws(() => requireExactSignature({company_signatory: 'Project Manager',
  company_title: 'Synthetic Authorized Signatory'}), /signature field differs/);
'''
        # Alpha's assert is the product smoke predicate, not node's object API.
        script = script.replace("const assert = require('node:assert/strict');",
                                "const nodeAssert = require('node:assert/strict');\n"
                                "function assert(ok, message) {nodeAssert.ok(ok, message);}\n"
                                "assert.throws = nodeAssert.throws;")
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_request_row_variants_match_generator_normalisation(self):
        variants = [
            {"description": description, "quantity": quantity, "unit": unit, "price_mode": mode,
             "display_price": display}
            for description, quantity, unit, mode, display in (
                ("[ Synthetic surface ]", "2", "sqm", "Priced", ""),
                ("Synthetic surface", 2, "sqm", "priced", ""),
                ("Synthetic surface", 2.0, "sqm", "PRICED", ""),
                ("Synthetic fitting", "1.0", "set", "included", ""),
                ("Synthetic fitting", "1", "sets", "Included", ""),
                ("Synthetic fitting", 1, "sets", "INCLUDED", ""),
                ("Synthetic fitting", 1, "sets", "Unpriced", "Included"),
                ("Synthetic fitting", 1, "sets", "Unpriced", ""),
                ("Synthetic fitting", 1, "sets", "Priced", "Included"),
                ("  [ Cafe\u0301  panel ] - detail\r\n here  ", "2.5", "Sq M", " priced ", ""),
                ("[ ] - detail", "2e0", "m. run", "Priced", ""),
                ("[ Synthetic ]", "2,000", "m. length", "Priced", ""),
                ("Synthetic", "2%", "pieces", "Priced", ""),
                ("Synthetic", "2", "lots", "Priced", ""),
                ("Synthetic", "2", " custom\tunit ", "Priced", ""),
            )
        ]
        # unit_hint is catalogue metadata, not a substitute for the request unit.
        variants.append({"description": "Synthetic", "quantity": "2", "unit_hint": "sqm", "price_mode": "Priced"})
        script = "const assert = require('node:assert/strict');\n" + workbook_adapter_source() + \
            "\nprocess.stdout.write(JSON.stringify(" + json.dumps(variants) + ".map(workbookRow)));"
        result = subprocess.run(["node", "-e", script], cwd=ROOT, encoding="utf-8", capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        adapted = json.loads(result.stdout)
        self.assertEqual(len(adapted), len(variants))
        for index, (raw, actual) in enumerate(zip(variants, adapted)):
            with self.subTest(index=index):
                # payload_to_brief parses quantity before prepare_lines sees it.
                mapped = {**raw, "quantity": server.parse_float_or_none(raw.get("quantity"))}
                line = generator.prepare_lines({"line_items": [mapped]}, [], False)[0]
                self.assertEqual(actual, {"description": generator.customer_quote_description(line.description),
                                          "quantity": line.quantity, "unit": line.unit, "price_mode": line.price_mode})

    def test_request_adapter_rejects_invalid_quantities(self):
        script = "const assert = require('node:assert/strict');\n" + workbook_adapter_source() + r'''
for (const quantity of ['', null, 'not a number', '0x2', '2 0', 'Infinity', '1e999']) {
  assert.throws(() => workbookRow({description: 'Synthetic', quantity, unit: 'sqm', price_mode: 'Priced'}),
    /Workbook request has invalid quantity/);
}
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

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
                            "quantity": "2" if index == 0 else 1, "unit": item["unit_hint"],
                            "pricing_keyword": item["id"], "price_mode": "Priced" if index == 0 else "Included",
                            "output_unit_locked": True,
                            **({"unit_price_override": item["price"]} if index == 0 else {"display_price": "Included"})}
                           for index, item in enumerate(items)],
        }
        payload["line_items"] = browser_request_line_items(payload["line_items"])
        self.assertEqual(len(payload["line_items"]), 2)
        self.assertIsInstance(payload["line_items"][0]["quantity"], str)
        self.assertEqual(payload["line_items"][0]["quantity"], "2")
        # Use the real mapper/normaliser with an accepted explicit price from
        # fixture data. Saved-session catalogue authority is covered by the smoke.
        with mock.patch.object(server, "profiles_root", return_value=FIXTURES / "profiles"), \
             mock.patch.object(server, "pricing_references_root", return_value=FIXTURES / "pricing-references"):
            brief = server.payload_to_brief(payload)
        self.assertEqual([item["pricing_authority"]["variant"] for item in brief["line_items"]], ["manual", "included"])
        self.assertNotIn("currency", payload)
        self.assertNotIn("header_lines", payload["company"])

        # Execute the actual smoke expression so request-to-verifier mapping cannot drift.
        source = (ROOT / "scripts/playwright-alpha-smoke.mjs").read_text(encoding="utf-8")
        start = source.index("    const workbookRow = (")
        end = source.index("    // Keep uploaded media", start)
        expression = source[start:end]
        script = "const assert = require('node:assert/strict');\nconst {createHash} = require('node:crypto');\n" + \
            "const xlsxJobRequest = " + json.dumps(payload) + ";\n" + \
            "const fixtureItems = " + json.dumps(items) + ";\n" + \
            "const referenceConfig = " + (REFERENCE / "reference.json").read_text(encoding="utf-8") + ";\n" + \
            "const expectedCustomer = " + json.dumps(payload["client"]["name"]) + ";\n" + \
            "const expectedProject = " + json.dumps(payload["project"]["title"]) + ";\n" + \
            "const expectedProjectNumber = " + json.dumps(payload["project_number"]) + ";\n" + \
            "const companyFields = " + json.dumps([["#quoteCompanyNameEditor", payload["company"]["name"]],
                                                     ["#headerDetailsEditor", payload["company"]["header_details"]],
                                                     ["#companySignatoryEditor", payload["signature"]["company_signatory"]],
                                                     ["#companyTitleEditor", payload["signature"]["company_title"]]]) + ";\n" + \
            "const logoBuffer = Buffer.from(" + json.dumps(base64.b64encode(logo).decode("ascii")) + ", 'base64');\n" + \
            "function expectations(xlsxJobRequest) {\n" + expression + "\nreturn expectedWorkbook; }\n" + r'''
const expectedWorkbook = expectations(xlsxJobRequest);
// Corrupt the request echo while preserving the values the smoke entered and
// reference config. Its expectations must not follow those stale request fields.
for (const [key, wrong] of Object.entries({quote_currency: 'XXX', quote_tax: {label: 'WRONG', rate: 0.5},
  client: {name: 'Wrong buyer'}, project: {title: 'Wrong project'}, project_number: 'Wrong number',
  company: {name: 'Wrong company', header_details: 'Wrong header'},
  signature: {company_signatory: 'Wrong signer', company_title: 'Wrong title'}})) {
  assert.deepEqual(expectations({...xlsxJobRequest, [key]: wrong}), expectedWorkbook,
    'Workbook expectations followed request echo: ' + key);
}
process.stdout.write(JSON.stringify(expectedWorkbook));
'''
        expected = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(expected.returncode, 0, expected.stderr)
        for actual, row in zip(json.loads(expected.stdout)["payload"]["line_items"], json.loads(expected.stdout)["rows"]):
            self.assertEqual({key: actual[key] for key in ("description", "quantity", "unit", "price_mode")},
                             {key: row[key] for key in ("description", "quantity", "unit", "price_mode")})
        self.assertEqual(json.loads(expected.stdout)["logo"], {"bytes": len(logo), "sha256": hashlib.sha256(logo).hexdigest()})

        # Keep the mapped server brief in memory. Its transitive auth/ledger
        # metadata must never flow into the on-disk synthetic generator fixture.
        generator_brief = {
            "_pricing_authority_enforced": True,
            "_pricing_reference_authority": {"id": REFERENCE.name, "source": "local", "currency": catalog["currency"],
                                             "digest": generator.catalog_payload_digest(catalog)},
            "company_identity": payload["company"]["name"], "quote_date": payload["quote_date"],
            "project_number": payload["project_number"],
            "client": {**payload["client"], "address": payload["client"]["address"].splitlines()}, "project": payload["project"],
            "company": {"name": payload["company"]["name"], "header_lines": [payload["company"]["header_details"]],
                        "logo_data_url": payload["company"]["logo_data_url"]},
            "currency": payload["quote_currency"], "exchange_rate": payload["quote_exchange_rate"], "tax": payload["quote_tax"],
            "acceptance": {"company_name": payload["company"]["name"], "text": payload["quote_text"]["acceptance_text"],
                           "person_label": payload["quote_text"]["person_label"], "stamp_label": payload["quote_text"]["stamp_label"],
                           "date_label": payload["quote_text"]["date_label"]},
            "signature": payload["signature"],
            "line_items": [{"section": item["section"], "description": item["description"],
                            "quantity": 2 if index == 0 else 1, "unit": item["unit_hint"], "pricing_keyword": item["id"],
                            "price_mode": "Priced" if index == 0 else "Included",
                            **({"unit_price_override": item["price"]} if index == 0 else {"display_price": "Included"})}
                           for index, item in enumerate(items)],
        }
        for index, row in enumerate(generator_brief["line_items"]):
            row["pricing_authority"] = {
                "schema": generator.PRICING_AUTHORITY_SCHEMA, "version": generator.PRICING_AUTHORITY_VERSION,
                "variant": "manual" if index == 0 else "included", "context": generator.pricing_authority_context(row),
                "price": items[index]["price"] if index == 0 else 0, "currency": catalog["currency"],
            }
        self.assertEqual(brief["_pricing_reference_authority"], generator_brief["_pricing_reference_authority"])
        fixture_lines = generator.prepare_lines(generator_brief, [], False,
                                                trusted_pricing_reference=generator_brief["_pricing_reference_authority"])
        self.assertEqual([line.match_status for line in fixture_lines], ["manual-price", "included"])
        for key in ("company_identity", "quote_date", "project_number", "client", "company", "currency",
                    "exchange_rate", "tax", "acceptance", "signature"):
            self.assertEqual(brief[key], generator_brief[key], key)
        self.assertEqual(brief["project"]["title"], generator_brief["project"]["title"])
        mapped_rows = generator.prepare_lines(brief, [], False,
                                             trusted_pricing_reference=brief["_pricing_reference_authority"])
        self.assertEqual([(line.match_status, line.amount) for line in mapped_rows],
                         [(line.match_status, line.amount) for line in fixture_lines])
        for mapped, row in zip(mapped_rows, json.loads(expected.stdout)["payload"]["line_items"]):
            self.assertEqual(row, {"description": generator.customer_quote_description(mapped.description),
                                   "quantity": mapped.quantity, "unit": mapped.unit, "price_mode": mapped.price_mode})

        (ROOT / "_tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="alpha-request-", dir=ROOT / "_tmp") as temporary:
            directory = Path(temporary)
            # Explicit LF JSON and forward-slash CLI paths match Linux CI transport.
            (directory / "brief.json").write_bytes((json.dumps(generator_brief, indent=2) + "\n").encode("utf-8"))
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
