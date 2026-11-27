"""An analysis the app cannot use names the rule that refused it (#250, ERR-C35DE5D2 in the 83309d9 owner UAT).

An answer with no quote basis (a wrong upload: no booth found) is refused with reason ``no_quote_basis``, so the page
can ask for booth renders; every failure record names the refusing rule or the error class. No model text, customer
data or key reaches the logs. Synthetic data, mocked provider, no paid AI.
"""
import json
import unittest
from unittest import mock

from tests import test_webapp as fixtures

webapp = fixtures.webapp
FAILURE_EVENTS = ("ai_call_attempt", "openai_draft_failed", "ai_draft_protected_mode_blocked")


def provider_answer(draft: dict) -> dict:
    return {"output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": json.dumps(draft)}]}],
            "model": fixtures.SYNTHETIC_DRAFT_CONFIG["OPENROUTER_DRAFT_MODEL"], "status": "completed"}


def failure_details(records: list[dict]) -> dict[str, dict]:
    found = {}
    for record in records:
        if record.get("event") in FAILURE_EVENTS:
            details = record.get("details", {})
            found[record["event"]] = details.get("details", details) if record["event"] == "ai_call_attempt" else details
    return found


class AnalysisFailureRuleTest(unittest.TestCase):
    def setUp(self):
        # The webapp suite's synthetic profile, catalogue and media setup for protected draft runs.
        self.helper = fixtures.WebappServerTest("test_run410_non_string_top_level_output_text_is_shape_failure")
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)

    def run_draft(self, draft: dict, label: str):
        return self.helper._run_run410_protected_draft_fixture(provider_answer(draft), label)

    def test_answer_without_a_quote_basis_is_refused_as_no_quote_basis_and_names_its_rule(self):
        empty = {"quote_basis_sections": [], "line_items": [], "analysis_findings": [], "blocking_clarification_questions": [],
                 "project": {"booth_width": None, "booth_depth": None}}
        payload, result, records = self.run_draft(empty, "no-basis")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result.get("reason_code"), "no_quote_basis")
        self.assertRegex(result["error_reference"], r"^ERR-[0-9A-F]{8}$")
        details = failure_details(records)
        self.assertEqual(set(details), set(FAILURE_EVENTS))
        for event, detail in details.items():
            with self.subTest(event=event):
                self.assertEqual(detail.get("normalization_rule"), "no_usable_quote_basis")
                self.assertEqual(detail.get("failure_boundary"), "application_normalization")
        serialized = json.dumps([result, records])
        for private in ("sk-test-observability", payload["client"]["name"], payload["images"][0]["data_url"]):
            self.assertNotIn(private, serialized)

    def test_an_error_inside_normalisation_names_its_class_without_its_text(self):
        answer = {"quote_basis_sections": [{"id": "floors", "title": "Floors", "lines": [
            {"id": "carpet", "tag": "Include", "text": "sqm synthetic carpet tile", "quantity": 36, "unit": "sqm", "confidence_pct": 90}]}],
            "line_items": [], "analysis_findings": [], "blocking_clarification_questions": [], "project": {"booth_width": 6, "booth_depth": 6}}
        with mock.patch.object(webapp, "unpack_ai_draft", side_effect=KeyError("PRIVATE_NORMALISATION_KEY")):
            _, result, records = self.run_draft(answer, "keyerror")
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("reason_code", result)
        for event, detail in failure_details(records).items():
            with self.subTest(event=event):
                self.assertEqual(detail.get("normalization_rule"), "keyerror")
        self.assertNotIn("PRIVATE_NORMALISATION_KEY", json.dumps(records))

    def test_the_rule_is_a_kept_diagnostic_and_only_plain_names_pass(self):
        self.assertEqual(webapp.safe_ai_output_diagnostics({"normalization_rule": "no_usable_quote_basis"}),
                         {"normalization_rule": "no_usable_quote_basis"})
        self.assertEqual(webapp.safe_ai_output_diagnostics({"normalization_rule": "a rule with model text"}), {})


if __name__ == "__main__":
    unittest.main()
