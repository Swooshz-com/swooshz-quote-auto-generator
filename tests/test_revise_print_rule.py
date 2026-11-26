"""A detail staff add through Re to a catalogue line prints after the catalogue name (#430, owner decision
INT-SQAG-202611250017), only while the item stays the same and the detail is still the line's text after the
dash. The AI's own draft detail, own lines and older quotes print as before.

Each case runs the real Re path (the server's admission of a mocked provider reply), then the server's
Confirm path for a quote without its own rows, and the generator for Excel and a saved template.
Synthetic catalogue, mocked AI reply, no network and no paid AI."""
import copy
import re
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from tests import test_webapp as existing
from tests import test_quote_template_generation as templates
from tests.gateway_test_support import install_mock_responses_transport, json_response, synthetic_configuration

webapp = existing.webapp

BANNER = "synthetic-graphics-synthetic-hanging-banner"
CARPET = "synthetic-floors-synthetic-carpet-tile"
VINYL = "synthetic-floors-synthetic-vinyl-floor-finish"
NAMES = {BANNER: "sqm synthetic hanging banner", CARPET: "sqm synthetic carpet tile", VINYL: "sqm synthetic vinyl floor finish"}
SECTION = {"id": "synthetic-graphics", "title": "Synthetic Graphics"}


def catalogue_line(line_id, keyword, detail="", quote_detail=None, quantity=6):
    name = NAMES[keyword]
    line = {"id": line_id, "tag": "Include", "text": f"[ {name} ] - {detail}" if detail else f"[ {name} ]",
            "quantity": quantity, "unit": "sqm", "confidence": 95, "pricing_keyword": keyword,
            "catalog_description": name, "pricing_reference_description": name}
    if quote_detail is not None:
        line["quote_detail"] = quote_detail
    return line


OWN_LINE = {"id": "own", "tag": "Custom", "text": "Feature wall 4m W x 3m H with laminate finish", "quantity": 12,
            "unit": "sqm", "confidence": 95, "custom_pricing": True, "custom_confirmed": True}
LONG = "x" * 201

# (case, the line before, the operator's message, the AI's replacement line,
#  expected: same item?, quote_detail after Apply, the printed description)
RE_CASES = (
    ("1 detail added", catalogue_line("t", BANNER), "should be black",
     {"text": "[ sqm synthetic hanging banner ] - black"}, True, "black", "sqm synthetic hanging banner - black"),
    ("2 AI draft detail changed", catalogue_line("t", CARPET, "Charcoal loop"), "should be ivory loop",
     {"text": "[ sqm synthetic carpet tile ] - ivory loop"}, True, "ivory loop", "sqm synthetic carpet tile - ivory loop"),
    ("3 detail on a hand-priced line", catalogue_line("t", VINYL), "should be matt",
     {"text": "[ sqm synthetic vinyl floor finish ] - matt"}, True, "matt", "sqm synthetic vinyl floor finish - matt"),
    ("4 own line", OWN_LINE, "finish should be veneer",
     {"text": "Feature wall 4m W x 3m H with veneer finish"}, None, None, "Feature wall 4m W x 3m H with veneer finish"),
    ("5 name changed", catalogue_line("t", CARPET), "should be red rug",
     {"text": "red rug"}, False, None, "red rug"),
    ("6 detail naming another item", catalogue_line("t", BANNER), "should be printed wall graphic",
     {"text": "[ sqm synthetic hanging banner ] - printed wall graphic"}, False, None,
     "sqm synthetic hanging banner - printed wall graphic"),
    ("7 detail naming another item's shape", catalogue_line("t", BANNER), "should be 3m W x 1m H, double sided",
     {"text": "[ sqm synthetic hanging banner ] - 3m W x 1m H, double sided"}, False, None,
     "sqm synthetic hanging banner - 3m W x 1m H, double sided"),
    ("conflicting material", catalogue_line("t", BANNER), "should be vinyl finish",
     {"text": "[ sqm synthetic hanging banner ] - vinyl finish"}, False, None, "sqm synthetic hanging banner - vinyl finish"),
    ("detail changed", catalogue_line("t", BANNER, "black", "black"), "should be white",
     {"text": "[ sqm synthetic hanging banner ] - white"}, True, "white", "sqm synthetic hanging banner - white"),
    ("detail removed", catalogue_line("t", BANNER, "black", "black"), "remove the black",
     {"text": "[ sqm synthetic hanging banner ]"}, True, None, "sqm synthetic hanging banner"),
    ("quantity only on an AI draft detail", catalogue_line("t", CARPET, "Charcoal loop", quantity=36), "quantity should be 40",
     {"text": "[ sqm synthetic carpet tile ] - Charcoal loop", "quantity": 40, "unit": "sqm"}, True, None, "sqm synthetic carpet tile"),
    ("AI draft detail recased with a quantity", catalogue_line("t", CARPET, "Charcoal loop", quantity=36), "quantity should be 40",
     {"text": "[ sqm synthetic carpet tile ] - charcoal loop", "quantity": 40, "unit": "sqm"}, True, None, "sqm synthetic carpet tile"),
    ("quantity only on a Re detail", catalogue_line("t", BANNER, "black", "black"), "quantity should be 8",
     {"text": "[ sqm synthetic hanging banner ] - black", "quantity": 8, "unit": "sqm"}, True, "black",
     "sqm synthetic hanging banner - black"),
    ("detail written without brackets", catalogue_line("t", CARPET, "red", "red"), "should be blue",
     {"text": "sqm synthetic carpet tile - blue"}, True, "blue", "sqm synthetic carpet tile - blue"),
    ("name that starts a longer name", catalogue_line("t", CARPET), "should be carpet tile border",
     {"text": "[ sqm synthetic carpet tile border ]"}, False, None, "sqm synthetic carpet tile border"),
    ("formula-like detail", catalogue_line("t", BANNER), "should be =1+2",
     {"text": "[ sqm synthetic hanging banner ] - =1+2"}, True, "=1+2", "sqm synthetic hanging banner - =1+2"),
)


class RevisePrintRuleTest(unittest.TestCase):
    def setUp(self):
        helper = existing.WebappServerTest("test_local_mutable_storage_defaults_stay_inside_repo")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        install_mock_responses_transport(self)

    @staticmethod
    def payload(lines, rows=None):
        payload = existing.valid_payload()
        sections = [{**SECTION, "lines": copy.deepcopy(list(lines))}]
        payload["quote_basis_sections"] = sections
        payload["quote_basis"] = webapp.quote_basis_from_sections(sections)
        payload["line_items"] = copy.deepcopy(rows) if rows is not None else [
            {"source_basis_line_id": line["id"], "section": SECTION["title"], "quantity": line["quantity"], "unit": line["unit"],
             "description": NAMES[line["pricing_keyword"]] if line.get("pricing_keyword") else line["text"],
             "pricing_keyword": line.get("pricing_keyword", "")}
            for line in lines
        ]
        return payload

    def revise(self, line, question, reply):
        payload = self.payload([line])
        payload["basis_chat"] = {"question": question, "scope": "line", "field": SECTION["id"], "line_index": 0,
                                 "line": f"{line['tag']}: {line['text']}"}
        content = {"intent": "proposal", "proposal": {"message": "Change this line?",
                                                      "replacement_line": {"confidence_pct": 95, **reply}}}
        with (
            mock.patch.object(webapp, "read_dotenv_value", side_effect=synthetic_configuration(credential="synthetic-key").get),
            mock.patch.object(webapp.urllib.request, "urlopen", return_value=json_response("synthetic/basis_line", content)),
        ):
            result = webapp.request_configured_basis_chat(payload)
        return result["proposal"]["quote_basis_sections"][0]["lines"][0]

    def confirmed_description(self, line, row=None):
        """Confirm on a quote without its own rows: the server builds the row from the line."""
        payload = self.payload([line], [row] if row else None)
        rows = webapp.normalize_line_items_for_quote_basis_review(payload)
        [row] = [entry for entry in rows if entry.get("source_basis_line_id") == line["id"]]
        return row["description"]

    def test_re_cases_set_the_detail_and_print_it_only_for_the_same_item(self):
        for case, before, question, reply, same_item, quote_detail, printed in RE_CASES:
            with self.subTest(case):
                after = self.revise(before, question, reply)
                if same_item is not None:
                    self.assertEqual(bool(after.get("pricing_keyword")), same_item, after)
                    self.assertEqual(bool(after.get("custom_pricing")), not same_item, after)
                self.assertEqual(after.get("quote_detail"), quote_detail, after)
                self.assertEqual(webapp.printed_basis_line_detail(after), quote_detail or "")
                # The line's own row before Confirm still names the old wording; Confirm prints the line.
                self.assertEqual(self.confirmed_description(after), printed)

    def test_a_detail_over_200_characters_is_refused(self):
        with self.assertRaises(webapp.OpenAIAnalysisError):
            self.revise(catalogue_line("t", BANNER), "should be " + LONG, {"text": f"[ sqm synthetic hanging banner ] - {LONG}"})

    def test_older_and_forged_lines_print_the_name_only(self):
        cases = (
            ("older quote, AI draft detail, no field", catalogue_line("t", CARPET, "Charcoal loop"), "sqm synthetic carpet tile"),
            ("quote_detail differs from the text", catalogue_line("t", BANNER, "black", "white"), "sqm synthetic hanging banner"),
            ("quote_detail without a detail", catalogue_line("t", BANNER, "", "black"), "sqm synthetic hanging banner"),
            ("quote_detail over 200 characters", catalogue_line("t", BANNER, LONG, LONG), "sqm synthetic hanging banner"),
            ("quote_detail on an own line", {**OWN_LINE, "quote_detail": "laminate finish"}, OWN_LINE["text"]),
            ("quote_detail not text", catalogue_line("t", BANNER, "black", ["black"]), "sqm synthetic hanging banner"),
        )
        for case, line, printed in cases:
            with self.subTest(case):
                self.assertEqual(self.confirmed_description(line), printed)
                [normalised] = webapp.normalize_basis_lines(line)
                self.assertEqual(webapp.printed_basis_line_detail(normalised), "")

    def test_the_detail_is_kept_through_save_and_reopen_normalisation(self):
        line = catalogue_line("t", BANNER, "black", "black")
        [normalised] = webapp.normalize_basis_lines(line)
        self.assertEqual(normalised["quote_detail"], "black")
        sections = webapp.normalize_quote_basis_sections(self.payload([line]), [])
        self.assertEqual(sections[0]["lines"][0]["quote_detail"], "black")

    def test_the_ai_draft_never_sets_a_printed_detail(self):
        # Even when a reply (or a forged one) carries the field, the draft drops it: only Re sets it.
        drafts = known_detail_drafts(self)
        reply = drafts.fake_ai({"booth_width": 6, "booth_depth": 6})
        carpet = reply["quote_basis_sections"][0]["lines"][0]
        carpet.update({"text": "[ sqm synthetic carpet tile ] - Charcoal loop", "quote_detail": "Charcoal loop"})
        result = webapp.normalize_ai_draft(reply, drafts.payload(""), require_confidence=True)
        lines = [line for section in result["quote_basis_sections"] for line in section["lines"]]
        self.assertTrue(lines)
        self.assertFalse(any("quote_detail" in line for line in lines), lines)


def known_detail_drafts(test):
    from tests import test_known_details as known
    drafts = known.KnownDetailsDraftTest("test_clear_reading_is_offered_and_nothing_applies_until_apply")
    drafts.setUp()
    test.addCleanup(drafts.doCleanups)
    return drafts


def sheet_strings(path):
    with zipfile.ZipFile(path) as archive:
        text = "".join(archive.read(name).decode("utf-8") for name in archive.namelist()
                       if name.startswith("xl/worksheets/sheet") or name == "xl/sharedStrings.xml")
        formulas = [formula for name in archive.namelist() if name.startswith("xl/worksheets/sheet")
                    for formula in re.findall(r"<f[^>]*>([^<]*)</f>", archive.read(name).decode("utf-8"))]
    return text, formulas


class RevisePrintRuleWorkbookTest(unittest.TestCase):
    """The rows Confirm saves ("name - detail") through the real job path and generator: the profile's own layout
    and a saved template print the detail exactly as applied, formula-like detail stays text, and a row whose
    line has no Re detail (an older or hand-typed one) is written as before."""

    LINES = [
        catalogue_line("detail-banner", BANNER, "black colour", "black colour"),
        catalogue_line("detail-formula", BANNER, "=1+2", "=1+2", quantity=4),
        # Hand-typed in Output, no Re detail on its line: today's handling.
        catalogue_line("detail-typed", VINYL, "matt finish", quantity=8),
        catalogue_line("detail-none", CARPET, "Charcoal loop", quantity=12),
    ]
    DESCRIPTIONS = {"detail-banner": "sqm synthetic hanging banner - black colour",
                    "detail-formula": "sqm synthetic hanging banner - =1+2",
                    "detail-typed": "sqm synthetic vinyl floor finish - matt finish",
                    "detail-none": "sqm synthetic carpet tile"}
    BRIEF = {"detail-banner": "[ sqm synthetic hanging banner ] - black colour",
             "detail-formula": "[ sqm synthetic hanging banner ] - =1+2",
             "detail-typed": "[ sqm synthetic vinyl floor finish ] - Matt finish",
             "detail-none": "[ sqm synthetic carpet tile ]"}

    # The template suite's real job path: synthetic profile, catalogue and template store, real generator.
    setUp = templates.QuoteTemplateGenerationTest.setUp

    def generate_rows(self, choice, pdf_mode="none"):
        self.choice = choice
        payload = RevisePrintRuleTest.payload(self.LINES, [
            {"source_basis_line_id": line["id"], "section": line_section(line), "quantity": line["quantity"], "unit": "sqm",
             "description": self.DESCRIPTIONS[line["id"]], "pricing_keyword": line["pricing_keyword"]}
            for line in self.LINES])
        existing.with_server_analysis_seal(payload)
        result = webapp.run_quote_job(payload, output_root=self.root / "out", tmp_root=self.root / "tmp", pdf_mode=pdf_mode)
        self.assertEqual(result["status"], "completed", result)
        return result, self.commands[-1]

    def test_excel_and_a_saved_template_print_the_detail(self):
        saved = self.store.create(templates.definition("Detail layout"))
        for choice in (None, saved["id"]):
            with self.subTest(template=choice or "profile's own layout"):
                result, _command = self.generate_rows(choice)
                text, formulas = sheet_strings(Path(result["output_dir"]) / "quotation.xlsx")
                for row_id in ("detail-banner", "detail-formula"):
                    self.assertIn(self.DESCRIPTIONS[row_id], text)
                self.assertIn("sqm synthetic vinyl floor finish - Matt finish", text)
                self.assertNotIn("Charcoal", text)
                self.assertFalse([formula for formula in formulas if "1+2" in formula], formulas)

    def test_the_brief_keeps_the_applied_detail_and_older_rows_as_before(self):
        captured = []
        real_brief = webapp.normalize_line_items_for_final_brief

        def brief(*args, **kwargs):
            rows = real_brief(*args, **kwargs)
            captured.append({row["source_basis_line_id"]: row["description"] for row in rows})
            return rows

        with mock.patch.object(webapp, "normalize_line_items_for_final_brief", side_effect=brief):
            self.generate_rows(None)
        self.assertEqual(captured[-1], self.BRIEF)


def line_section(line):
    return {BANNER: "Synthetic Graphics", CARPET: "Synthetic Floors", VINYL: "Synthetic Floors"}[line["pricing_keyword"]]


if __name__ == "__main__":
    unittest.main()
