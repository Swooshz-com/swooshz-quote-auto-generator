"""Raw provider measurements must retain identity before any catalogue projection."""
import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import test_webapp as existing

webapp = existing.webapp
CATALOG = [{"id": "synthetic-surface", "section": "Surfaces", "description": "sqm Synthetic surface",
            "unit_hint": "sqm", "sale_unit_price": 43, "match_terms": ["synthetic surface"],
            "object_families": ["synthetic surface"]}]
DETAIL = "Provisional synthetic surface installation footprint 4m x 3m; separate design area 5m x 3m requires confirmation"


class ProviderSourceIdentityTest(unittest.TestCase):
    def row_and_sections(self):
        row = {"description": "Synthetic reusable surface installation", "quantity": 8, "unit": "sqm"}
        line = {"id": "surface-row", "tag": "Custom", "text": row["description"],
                "quantity": 8, "unit": "sqm", "confidence": 70}
        return row, [{"id": "surfaces", "title": "Surfaces", "lines": [line]}]

    def test_only_unique_id_is_copied_and_inputs_are_unchanged(self):
        row, sections = self.row_and_sections()
        row["untrusted_extra"] = {"retained": [1]}
        original = copy.deepcopy((row, sections))
        result = webapp.provider_rows_with_unique_basis_ids([row], sections)
        self.assertEqual(result, [{**row, "source_basis_line_id": "surface-row"}])
        self.assertEqual((row, sections), original)
        self.assertEqual(webapp.provider_rows_with_unique_basis_ids(result, sections), result)
        result[0]["untrusted_extra"]["retained"].append(2)
        self.assertEqual((row, sections), original)
        self.assertEqual(webapp.pricing_analysis_source_units(result), {"surface-row": "sqm"})

    def test_ambiguous_or_conflicting_ownership_is_not_rebound(self):
        for case in ("duplicate-raw", "duplicate-basis", "duplicate-id-other-text", "conflicting-basis-ids",
                     "claimed-id", "claimed-alternate-id", "explicit-id-collision", "different-detail"):
            with self.subTest(case=case):
                row, sections = self.row_and_sections()
                rows = [row]
                line = sections[0]["lines"][0]
                if case == "duplicate-raw": rows.append(copy.deepcopy(row))
                elif case == "duplicate-basis": sections[0]["lines"].append({**line, "id": "another-row"})
                elif case == "duplicate-id-other-text": sections.append({"id": "other", "title": "Other", "lines": [{**line, "text": "Different scope"}]})
                elif case == "conflicting-basis-ids": line["source_line_item_id"] = "different-row"
                elif case == "claimed-id": row["source_basis_line_id"] = "conflicting-source"
                elif case == "claimed-alternate-id": row["id"] = "alternate-source"
                elif case == "explicit-id-collision": rows.append({**row, "description": "Another scope", "source_basis_line_id": "surface-row"})
                elif case == "different-detail":
                    row["description"] = "[ sqm Synthetic surface ] - West installation"
                    line["text"] = "[ sqm Synthetic surface ] - East installation"
                before = copy.deepcopy(rows)
                self.assertEqual(webapp.provider_rows_with_unique_basis_ids(rows, sections), before)

    def test_missing_or_conflicting_measurements_are_never_inferred(self):
        for key, value in (("unit", ""), ("unit", "lot"), ("quantity", None), ("quantity", 9),
                           ("quantity", 0), ("quantity", float("inf")), ("quantity", True)):
            with self.subTest(key=key, value=value):
                row, sections = self.row_and_sections()
                row[key] = value
                self.assertNotIn("source_basis_line_id", webapp.provider_rows_with_unique_basis_ids([row], sections)[0])
        row, sections = self.row_and_sections()
        sections[0]["lines"][0].pop("quantity")
        self.assertEqual(webapp.provider_rows_with_unique_basis_ids([row], sections), [row])
        # The previous identity-only physical-row contract is retained for explicitly linked rows.
        row["source_basis_line_id"] = "surface-row"
        self.assertEqual(webapp.provider_rows_with_unique_basis_ids([row], sections), [row])

    def test_raw_text_prefix_is_evidence_only_when_structured_fields_do_not_conflict(self):
        for quantity, unit, expected in ((None, "", True), (8, "m2", True), ("8", "sqm", True),
                                         (9, "sqm", False), (8, "lot", False), (9, "lot", False),
                                         (True, "sqm", False)):
            with self.subTest(quantity=quantity, unit=unit):
                row, sections = self.row_and_sections()
                row.update(description="8 sqm " + row["description"], quantity=quantity, unit=unit)
                result = webapp.provider_rows_with_unique_basis_ids([row], sections)
                self.assertEqual(result[0].get("source_basis_line_id") == "surface-row", expected)
                if expected: self.assertEqual(webapp.pricing_analysis_source_units(result), {"surface-row": "sqm"})
                # Existing explicit-ID prefix precedence is not changed by this new reconciliation.
                row["source_basis_line_id"] = "existing-row"
                self.assertEqual(webapp.provider_rows_with_unique_basis_ids([row], sections), [row])

    def test_distinct_physical_rows_keep_separate_identity_with_same_catalogue_or_text(self):
        row, sections = self.row_and_sections()
        second = {**row, "quantity": 3}
        sections[0]["lines"].append({**sections[0]["lines"][0], "id": "second-row", "quantity": 3})
        for line in sections[0]["lines"]: line["pricing_keyword"] = "synthetic-surface"
        result = webapp.provider_rows_with_unique_basis_ids([row, second], sections)
        self.assertEqual([entry["source_basis_line_id"] for entry in result], ["surface-row", "second-row"])
        self.assertEqual([entry["quantity"] for entry in result], [8, 3])

    def test_candidate_with_explicit_provenance_map_requires_nonblank_owned_unit(self):
        line = {"id": "dimension-note", "tag": "Custom", "custom_pricing": True, "text": DETAIL,
                "quantity": 12, "unit": "sqm", "confidence": 30}
        row = {"source_basis_line_id": "dimension-note", "section": "Surfaces", "description": DETAIL,
               "quantity": 12, "unit": "sqm"}
        for units, expected in (({}, False), ({"dimension-note": ""}, False),
                                ({"different-row": "sqm"}, False), ({"dimension-note": "lot"}, False),
                                ({"dimension-note": "sqm"}, True)):
            with self.subTest(units=units):
                sections = webapp.quote_basis_sections_with_catalog_exact_lines(
                    [{"id": "surfaces", "title": "Surfaces", "lines": [line]}], [row], CATALOG,
                    provider_result=True, source_units=units)
                matches = [match for section in sections for entry in section["lines"]
                           for match in entry.get("possible_pricing_matches", [])]
                self.assertEqual(bool(matches), expected)


class ProviderSourceSelectionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = existing.WebappServerTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def raw_draft(self, *, missing_unit=False, ambiguous=False):
        raw = {"project": {"booth_width": 4, "booth_depth": 3}, "quote_basis_sections": [{
            "id": "surfaces", "title": "Surfaces", "lines": [
                {"id": "physical-row", "tag": "Confirm", "text": "Synthetic surface physical installation",
                 "quantity": 12, "unit": "sqm", "confidence": 90, "pricing_keyword": "synthetic-surface"},
                {"id": "dimension-note", "tag": "Custom", "text": DETAIL, "quantity": 12, "unit": "sqm", "confidence": 30},
            ]}], "line_items": [
                {"source_basis_line_id": "physical-row", "section": "Surfaces", "description": "Synthetic surface physical installation",
                 "quantity": 12, "unit": "sqm", "pricing_keyword": "synthetic-surface"},
                {"section": "Surfaces", "description": DETAIL, "quantity": 12, "unit": "" if missing_unit else "sqm"},
            ]}
        if missing_unit:
            raw["line_items"][1]["source_basis_line_id"] = "dimension-note"
        if ambiguous:
            raw["line_items"].append(copy.deepcopy(raw["line_items"][1]))
            raw["quote_basis_sections"][0]["lines"].append({**raw["quote_basis_sections"][0]["lines"][1], "id": "second-note"})
        return raw

    def browser(self, request, reference, response_rows=None):
        node = existing.require_node(self)
        value = {"request": {key: item for key, item in request.items() if not key.startswith("_")}, "reference": reference}
        if response_rows is not None: value["response_rows"] = response_rows
        completed = subprocess.run([node, "tests/basis_source_provenance_harness.cjs"], input=json.dumps(value),
                                   cwd=existing.ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        return json.loads(completed.stdout)

    def test_post_draft_missing_raw_id_can_select_save_reopen_and_display_real_price(self):
        self.run_draft_selection(missing_unit=False)

    def test_post_draft_missing_raw_unit_and_saved_blank_seal_are_never_upgraded(self):
        self.run_draft_selection(missing_unit=True)

    def run_draft_selection(self, *, missing_unit):
        reference_id = "source-provenance-reference"
        with tempfile.TemporaryDirectory(dir=existing.test_temp_root()) as temp:
            existing.write_test_pricing_reference(Path(temp), reference_id, CATALOG)
            payload = existing.valid_payload()
            payload.update(pricing_reference_id=reference_id, pricing_reference_source="local",
                           pricing_reference={"id": reference_id, "source": "local", "currency": "SGD"},
                           quote_session={"session_id": "quote-source-unit-" + str(int(missing_unit))})
            raw = self.raw_draft(missing_unit=missing_unit)
            before_raw = copy.deepcopy(raw)
            env = existing.isolated_env(OPENAI_API_KEY="ordinary-mock-only-no-network", AUTH_REQUIRED="false",
                                        QUOTE_DATA_ROOT=str(self.fixture._test_data_root))
            def provider(request, _key, auth_session=None, **_kwargs):
                return webapp.normalize_ai_draft(copy.deepcopy(raw), request, require_confidence=True, auth_session=auth_session)
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(webapp, "pricing_references_root", return_value=Path(temp)), \
                 mock.patch.object(webapp, "read_dotenv_value", side_effect=existing.env_only_dotenv_value), \
                 mock.patch.object(webapp, "request_openai_quote_basis", side_effect=provider) as remote, \
                 mock.patch.object(webapp, "write_local_log"), existing.LocalRunnerServer() as runner:
                headers = self.fixture._ledger_runner_headers(runner)
                drafted = self.fixture._ledger_post(runner, headers, "/api/draft", payload)
                self.assertEqual(drafted["status"], 200, drafted["text"][-3000:])
                result = drafted["body"]
                self.assertEqual(remote.call_count, 1)
                self.assertEqual(raw, before_raw)
                reference = webapp.exact_pricing_reference_authority(payload)["detail"]
                request = copy.deepcopy(payload)
                request.update(line_items=result["line_items"], quote_basis_sections=result["quote_basis_sections"],
                               pricing_analysis_seal=result["pricing_analysis_seal"])
                self.fixture._ledger_sync(request, pricing_basis={"currency": reference["currency"], "source": "local",
                    "id": reference_id, "digest": reference["digest_sha256"]})
                line = self.fixture._ledger_line(request, "dimension-note")
                sealed = next(row for row in result["pricing_analysis_seal"]["rows"] if row["id"] == "dimension-note")
                self.assertEqual(line["tag"], "Custom")
                self.assertNotIn("pricing_keyword", line)
                self.assertNotIn("catalog_item_id", sealed)
                self.assertEqual(sealed["unit"], "" if missing_unit else "sqm")
                matches = line.get("possible_pricing_matches", [])
                self.assertEqual(bool(matches), not missing_unit)
                if missing_unit:
                    # A stale pre-fix candidate cannot bypass the unchanged server grant boundary.
                    line["possible_pricing_matches"] = [{"pricing_keyword": CATALOG[0]["id"],
                        "description": CATALOG[0]["description"], "unit": "sqm", "section": "Surfaces"}]
                chosen = self.browser(request, reference)
                request["quote_basis_sections"] = chosen["sections"]
                self.fixture._ledger_sync(request)
                selected = self.fixture._ledger_post(runner, headers, "/api/quote-sessions/pricing-selections",
                                                     {**request, "pricing_selections": chosen["pending"]})
                self.assertEqual(selected["status"], 200, selected["text"][-3000:])
                body = selected["body"]
                expected = {"source_basis_line_id": "dimension-note", "status": "refused", "reason": "unit_review_required"} if missing_unit else {"source_basis_line_id": "dimension-note", "status": "selected"}
                self.assertEqual(body["selections"], [expected])
                row = next(entry for entry in body["line_items"] if entry["source_basis_line_id"] == "dimension-note")
                self.assertEqual(row["pricing_authority"]["variant"], "none" if missing_unit else "catalog")
                display = self.browser(request, reference, body["line_items"])["output"]
                displayed = next(entry for entry in display if entry["id"] == "dimension-note")
                self.assertEqual(displayed["quoted"], "???" if missing_unit else "43.00")
                self.assertEqual(displayed["amount"], "???" if missing_unit else "516.00")
                # Save the actual server rows, reopen via GET and re-normalise using the same ledger.
                request["line_items"] = body["line_items"]
                self.fixture._ledger_sync(request)
                saved = self.fixture._ledger_post(runner, headers, "/api/quote-sessions", request)
                self.assertEqual(saved["status"], 200, saved["text"][-2000:])
                session_id = request["quote_session"]["session_id"]
                reopened = self.fixture.http_json(runner, "GET", "/api/quote-sessions/" + session_id)
                self.assertEqual(reopened["status"], 200)
                request["line_items"] = reopened["body"]["quote_session"]["draft_state"]["lineItems"]
                normalized = self.fixture._ledger_post(runner, headers, "/api/line-items/normalize", request)
                self.assertEqual(normalized["status"], 200)
                again = next(entry for entry in normalized["body"]["line_items"] if entry["source_basis_line_id"] == "dimension-note")
                self.assertEqual(again["pricing_authority"]["variant"], "none" if missing_unit else "catalog")
                self.assertEqual(result["pricing_analysis_seal"]["rows"], request["pricing_analysis_seal"]["rows"])

    def test_post_draft_ambiguous_raw_ownership_does_not_offer_a_candidate(self):
        raw = self.raw_draft(ambiguous=True)
        payload = self.fixture.sqag_212_payload(CATALOG)
        env = existing.isolated_env(OPENAI_API_KEY="ordinary-mock-only-no-network", AUTH_REQUIRED="false",
                                    QUOTE_DATA_ROOT=str(self.fixture._test_data_root))
        def provider(request, _key, auth_session=None, **_kwargs):
            return webapp.normalize_ai_draft(copy.deepcopy(raw), request, require_confidence=True, auth_session=auth_session)
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(webapp, "read_dotenv_value", side_effect=existing.env_only_dotenv_value), \
             mock.patch.object(webapp, "request_openai_quote_basis", side_effect=provider), \
             mock.patch.object(webapp, "write_local_log"), existing.LocalRunnerServer() as runner:
            headers = self.fixture._ledger_runner_headers(runner)
            drafted = self.fixture._ledger_post(runner, headers, "/api/draft", payload)
            self.assertEqual(drafted["body"]["status"], "blocked")
            self.assertTrue(drafted["body"]["error_reference"])
            self.assertNotIn("pricing_analysis_seal", drafted["body"])
            self.assertNotIn("line_items", drafted["body"])

    def test_rendered_missing_source_identity_selection_survives_output_and_reopen(self):
        node = existing.require_node(self)
        if not (existing.ROOT / "node_modules" / "playwright" / "package.json").exists():
            if os.environ.get("CI"): self.fail("Playwright is required for source-provenance regression.")
            self.skipTest("Playwright is not installed.")
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.run([node, "scripts/playwright-owner-uat-basis-repair.mjs", "--lines=0",
                "--latency-ms=0", "--fresh-analysis", "--source-provenance", "--currency=SGD"],
                cwd=existing.ROOT, env={**os.environ, "SQAG_OWNER_UAT_EVIDENCE_ROOT": temp, "PYTHON": os.sys.executable},
                capture_output=True, text=True, encoding="utf-8", timeout=600)
            self.assertEqual(completed.returncode, 0, (completed.stderr or completed.stdout)[-4000:])
            [run] = [path for path in Path(temp).iterdir() if path.is_dir()]
            result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["saveReopen"], "PASS")
        self.assertEqual(result["xlsx"]["status"], "PASS")
        self.assertEqual(result["sourceProvenance"]["missingIdsRecovered"], ["u1-provenance", "u1-second-provenance"])
        self.assertTrue(result["sourceProvenance"]["unmeasuredOutputUnpriced"])
        for row_id in ("u1-provenance", "u1-second-provenance"):
            self.assertIn({"source_basis_line_id": row_id, "status": "selected"}, result["selectionOutcomes"])



if __name__ == "__main__":
    unittest.main()
