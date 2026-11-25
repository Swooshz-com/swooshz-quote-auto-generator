"""The booth profile is never a quote line (#334), and no line contradicts an applied booth size (#385).

Synthetic data only. The fake AI reply runs through the real Responses transport, the draft contract,
sizing, normalisation and finalisation; no network and no paid calls.
"""
import copy
import json
import unittest
from unittest import mock

from tests import test_known_details as known
from webapp import ai_response_contracts as contracts

webapp = known.webapp

# Wording with no name list behind it: whatever it calls the booth's size, shape or review, it names only the booth.
PROFILE_TEXTS = (
    "Provisional booth footprint 6m W x 6m D, totalling 36 sqm",
    "Booth size review: confirm 8m x 9m (72 sqm)",
    "Confirm booth size from drawings",
    "Overall booth area 72 sqm, 2 open sides",
    "Exhibition stand footprint approx. 6 x 6 m",
    "Booth layout: island, 4 sides open, height 4 m",
    "Stand dimensions 8000 x 9000 mm to be verified",
    "Booth area 72 m²",
)
# Something is built, supplied, hired or done: a quote line even where it states the booth's size.
PHYSICAL_TEXTS = (
    "Raised platform to full booth footprint 6m x 6m",
    "Carpet for booth area 36 sqm",
    "Booth back wall 6m x 3m",
    "Raw space 36 sqm",
    "Booth space rental",
    "Brochure stand",
    "Booth layout drawing submission",
    "Booth fascia with name, open sides",
    "Stand lighting for 2 open sides",
    "Booth space 36 sqm",
)


class BoothProfileLineTest(unittest.TestCase):
    def setUp(self):
        self.drafts = known.KnownDetailsDraftTest("test_clear_reading_is_offered_and_nothing_applies_until_apply")
        self.drafts.setUp()
        self.addCleanup(self.drafts.doCleanups)

    def reply(self, *extra, project=None, linked=True):
        reply = self.drafts.fake_ai(project or {"booth_width": 6, "booth_depth": 6})
        for index, line in enumerate(extra):
            line = {"id": f"extra-{index}", "tag": "Confirm", "quantity": 36, "unit": "sqm", "confidence_pct": 70, **line}
            reply["quote_basis_sections"][0]["lines"].append(line)
            if linked:
                reply["line_items"].append({"source_basis_line_id": line["id"], "section": "Flooring", "description": line["text"],
                                            "quantity": line["quantity"] or 1, "unit": line["unit"] or "lot", "pricing_keyword": ""})
        return reply

    def quote_lines(self, result):
        return [line["text"] for line in self.drafts.basis_lines(result) if not webapp.is_informational_dimension_basis_line(line)]

    def test_general_rule_reads_the_words_not_a_list_of_names(self):
        for text in PROFILE_TEXTS:
            with self.subTest(profile=text):
                self.assertTrue(webapp.names_only_the_booth(text))
        for text in PHYSICAL_TEXTS:
            with self.subTest(physical=text):
                self.assertFalse(webapp.names_only_the_booth(text))

    def test_draft_and_reanalysis_never_offer_the_booth_profile(self):
        # One line per rule: the words, and the AI's own mark on a line whose words are not enough.
        marked = "Structure and layout note for the two-sided corner plot"
        for text, extra in ((PROFILE_TEXTS[0], {}), (PROFILE_TEXTS[2], {"quantity": None, "unit": None}), (marked, {"booth_profile": True})):
            reply = self.reply({"text": text, **extra})
            first, prompt = self.drafts.run_draft(self.drafts.payload(""), reply)
            # Re-Analyse sends the draft on screen back as context and runs the same path again.
            again = self.drafts.payload("")
            again["quote_basis_sections"] = copy.deepcopy(first["quote_basis_sections"])
            again["line_items"] = copy.deepcopy(first["line_items"])
            second, _prompt = self.drafts.run_draft(again, reply)
            for stage, result in (("draft", first), ("re-analyse", second)):
                with self.subTest(text=text, stage=stage):
                    self.assertNotIn(text, self.quote_lines(result))
                    self.assertFalse(any(row.get("source_basis_line_id") == "extra-0" for row in result["line_items"]))
                    # Shown under Detected booth instead, so nothing the AI said is lost.
                    self.assertIn(text, [line["text"] for line in self.drafts.basis_lines(result)])
                    self.assertEqual(self.drafts.row(result, known.CARPET)["quantity"], 36)
            self.assertIn("The booth itself is never a quote line", prompt)

    def test_a_marked_line_without_size_or_row_is_accepted_as_the_contract_asks(self):
        reply = self.reply({"text": "Two-sided corner plot note", "quantity": None, "unit": None, "booth_profile": True}, linked=False)
        result, _prompt = self.drafts.run_draft(self.drafts.payload(""), reply)
        self.assertEqual(result["source"], "openai")
        self.assertNotIn("Two-sided corner plot note", self.quote_lines(result))
        self.assertEqual(len(result["line_items"]), 2)

    def test_physical_and_priced_lines_stay_quote_lines(self):
        for text in PHYSICAL_TEXTS:
            with self.subTest(text=text):
                result, _prompt = self.drafts.run_draft(self.drafts.payload(""), self.reply({"text": text}))
                self.assertIn(text, self.quote_lines(result))
                self.assertEqual(sum(row.get("source_basis_line_id") == "extra-0" for row in result["line_items"]), 1)
        # A line the AI matched to the catalogue is a quote line whatever its words or mark say.
        reply = self.reply({"text": "Booth area 36 sqm", "pricing_keyword": known.CARPET, "booth_profile": True})
        reply["line_items"][-1]["pricing_keyword"] = known.CARPET
        result, _prompt = self.drafts.run_draft(self.drafts.payload(""), reply)
        self.assertEqual(sum(row.get("source_basis_line_id") == "extra-0" for row in result["line_items"]), 1)

    def test_an_unsized_booth_profile_line_is_never_sized_into_a_quote_line(self):
        # The sizing guarantee would otherwise give a line without a quantity "1 lot" and a row.
        parsed = self.reply({"text": PROFILE_TEXTS[2], "quantity": None, "unit": None}, linked=False)
        with mock.patch.object(webapp, "raw_draft_catalog_entries", return_value={}):
            webapp.complete_predicted_quantities(parsed, {}, None)
        line = parsed["quote_basis_sections"][0]["lines"][-1]
        self.assertTrue(line["booth_profile"])
        self.assertNotIn("quantity", line)
        self.assertFalse(any(row["source_basis_line_id"] == "extra-0" for row in parsed["line_items"]))

    def test_a_reply_without_line_items_reaches_admission(self):
        # Admission, not the booth rule, answers a reply of the wrong shape.
        for line_items in ("absent", None, {}):
            parsed = self.reply({"text": PROFILE_TEXTS[2], "quantity": None, "unit": None}, linked=False)
            parsed.pop("line_items")
            if line_items != "absent":
                parsed["line_items"] = line_items
            with self.subTest(line_items=line_items), mock.patch.object(webapp, "raw_draft_catalog_entries", return_value={}):
                webapp.complete_predicted_quantities(parsed, {}, None)
                self.assertTrue(parsed["quote_basis_sections"][0]["lines"][-1]["booth_profile"])

    def test_any_normalised_reply_is_separated(self):
        # Not only the sizing step: a reply normalised on its own keeps the booth out of the quote lines too.
        text = "Structure and layout note for the two-sided corner plot"
        result = webapp.normalize_ai_draft(self.reply({"text": text, "booth_profile": True}), self.drafts.payload(""), require_confidence=True)
        self.assertNotIn(text, self.quote_lines(result))
        self.assertFalse(any(row.get("source_basis_line_id") == "extra-0" for row in result["line_items"]))

    def test_contract_carries_the_mark_and_accepts_replies_from_before_it(self):
        schema = contracts.DRAFT_SYNTHESIS.text_format()["format"]["schema"]
        line = schema["properties"]["quote_basis_sections"]["items"]["properties"]["lines"]["items"]
        self.assertEqual(line["properties"]["booth_profile"]["type"], ["boolean", "null"])
        self.assertIn("booth_profile", line["required"])
        older = {"id": "a", "tag": "Confirm", "text": "Synthetic", "confidence_pct": 50, "quantity": 1, "unit": "lot",
                 "quantity_estimated": None, "pricing_keyword": None}
        field = dict(dict(contracts.DRAFT_SYNTHESIS.root.fields)["quote_basis_sections"].item.fields)["lines"].item
        missing = []
        field.inspect(older, "", missing)
        self.assertEqual(missing, [])


class AppliedBoothSizeTextTest(unittest.TestCase):
    """After Apply no line states a booth size that contradicts the applied one."""

    setUp = BoothProfileLineTest.setUp
    reply = BoothProfileLineTest.reply

    def applied(self, *texts, project=None, booth_size="93 sqm", area="93", **extra):
        reply = self.reply(*({"text": text, **extra} for text in texts),
                           project=project or {"booth_width": 6, "booth_depth": 6, "staff_booth_size": known.staff_reply(area=93)})
        result, _prompt = self.drafts.run_draft(
            self.drafts.applied_payload("Booth is 93 sqm, 8m x 9m", booth_size=booth_size, area=area), reply)
        lines = {line["id"]: line["text"] for line in self.drafts.basis_lines(result) if str(line.get("id", "")).startswith("extra-")}
        rows = {row["source_basis_line_id"]: row["description"] for row in result["line_items"]}
        return [lines[f"extra-{index}"] for index in range(len(texts))], rows

    def test_the_line_from_the_uat(self):
        [text], rows = self.applied("Provisional booth footprint 6m W x 6m D, totalling 36 sqm")
        self.assertEqual(text, "Provisional booth footprint 93 sqm, totalling 93 sqm")
        self.assertNotIn("extra-0", rows)

    def test_full_floor_and_booth_worded_lines_follow_the_applied_size(self):
        texts, rows = self.applied(
            "Raised platform to full footprint 6m x 6m (36 sqm)",
            "Skirting around 6000 x 6000 mm booth",
            "Platform 6m W x 6m D x 100mm H",
        )
        self.assertEqual(texts, [
            "Raised platform to full footprint 93 sqm (93 sqm)",
            "Skirting around 93 sqm booth",
            "Platform 93 sqm, 100mm H",
        ])
        self.assertEqual([rows[f"extra-{index}"] for index in range(3)], texts)

    def test_other_sizes_stay_as_drafted(self):
        texts = ("Truss 6m x 6m over the reception", "Meeting room 3m x 4m, 12 sqm", "Wall graphic 6m x 3m", "Tiles 1m x 1m")
        kept, _rows = self.applied(*texts, quantity=4, unit="nos")
        self.assertEqual(tuple(kept), texts)

    def test_a_catalogue_item_keeps_the_size_in_its_own_name(self):
        catalogue = webapp.pricing_references_root() / "synthetic-exhibition-fixture-pricing" / "pricing-catalog.json"
        data = json.loads(catalogue.read_text(encoding="utf-8"))
        carpet = next(item for item in data["items"] if item["id"] == known.CARPET)
        sized = {"canopy": ("nos 6m x 6m booth canopy", "nos"), "kit": ("sqm booth floor 36 sqm kit", "sqm")}
        for item_id, (description, unit) in sized.items():
            data["items"].append({**carpet, "id": item_id, "description": description, "unit_hint": unit, "aliases": [description],
                                  "match_terms": [], "object_families": [], "remarks": [], "visual_references": [], "item_order": 90 + len(item_id)})
        catalogue.write_text(json.dumps(data), encoding="utf-8")
        canopy = "nos 6m x 6m booth canopy"
        # (line, item, quantity, unit, the line once 93 sqm is applied to the AI's 6m x 6m booth)
        cases = (
            (f"[ {canopy} ] - White fabric", "canopy", 1, "nos", f"[ {canopy} ] - White fabric"),
            (canopy, "canopy", 1, "nos", f"[ {canopy} ]"),
            ("6m x 6m booth canopy in white", "canopy", 1, "nos", f"[ {canopy} ] - In white"),
            ("[ sqm booth floor 36 sqm kit ]", "kit", 36, "sqm", "[ sqm booth floor 36 sqm kit ]"),
            # What the line adds after the item's name is the booth's, and follows the applied size.
            (f"[ {canopy} ] - Over booth footprint 6m x 6m", "canopy", 1, "nos", f"[ {canopy} ] - Over booth footprint 93 sqm"),
            ("[ sqm synthetic carpet tile ] - To booth footprint 6m x 6m", known.CARPET, 36, "sqm",
             "[ sqm synthetic carpet tile ] - To booth footprint 93 sqm"),
        )
        for text, item_id, quantity, unit, expected in cases:
            with self.subTest(text=text):
                def reply():
                    draft = self.reply({"text": text, "quantity": quantity, "unit": unit, "pricing_keyword": item_id},
                                       project={"booth_width": 6, "booth_depth": 6, "staff_booth_size": known.staff_reply(area=93)})
                    draft["line_items"][-1].update(pricing_keyword=item_id, section="Synthetic Floors")
                    return draft
                applied, _prompt = self.drafts.run_draft(self.drafts.applied_payload("Booth is 93 sqm"), reply())
                plain, _prompt = self.drafts.run_draft(self.drafts.payload(""), reply())
                def read(result):
                    self.assertEqual(result["source"], "openai")
                    line = next(line for line in self.drafts.basis_lines(result) if line.get("id") == "extra-0")
                    row = next(row for row in result["line_items"] if row["source_basis_line_id"] == "extra-0")
                    return {"text": line["text"], "item": line.get("pricing_keyword"), "status": row["status"],
                            "variant": row["pricing_authority"]["variant"], "price": row.get("catalog_unit_price"), "quantity": row["quantity"]}
                self.assertEqual(read(applied), {**read(plain), "text": expected,
                                                 "quantity": 93 if unit == "sqm" else quantity})
                self.assertEqual((read(applied)["item"], read(applied)["variant"]), (item_id, "catalog"))

    def test_a_booth_line_that_contradicts_itself_is_corrected_when_the_ai_used_the_staff_size(self):
        [text], _rows = self.applied("Provisional booth footprint 6m W x 6m D, totalling 93 sqm",
                                     project={"booth_width": None, "booth_depth": None, "booth_area_sqm": 93,
                                              "staff_booth_size": known.staff_reply(area=93)})
        self.assertEqual(text, "Provisional booth footprint 93 sqm, totalling 93 sqm")

    def test_text_rules_by_staff_size(self):
        staff = {"booth_width": 8, "booth_depth": 9, "booth_area_sqm": 72}
        def restated(text, whole_floor=False, informational=True):
            return webapp.staff_sized_booth_text(text, staff, 36, whole_floor=whole_floor, informational=informational, own_sizes=set())
        self.assertEqual(restated("Booth footprint 6m (W) x 6m (D) = 36 sqm"), "Booth footprint 8m x 9m = 72 sqm")
        self.assertEqual(restated("Booth 6 x 6 x 4m H"), "Booth 8m x 9m x 4m H")
        self.assertEqual(restated("Booth W 6m x D 6m"), "Booth 8m x 9m")
        self.assertEqual(restated("Booth W6000 x D6000mm, 36 sqm"), "Booth 8m x 9m, 72 sqm")
        self.assertEqual(restated("Booth size 8m x 9m, 72 sqm"), "Booth size 8m x 9m, 72 sqm")
        self.assertEqual(restated("Floor 1m x 1m tiles", whole_floor=True, informational=False), "Floor 1m x 1m tiles")

    def test_long_text_stays_fast(self):
        import time
        staff = {"booth_width": None, "booth_depth": None, "booth_area_sqm": 93}
        for text in ("6m" + " " * 4000, ("6 x" + " " * 40) * 80, "6m w" * 900, "9" * 4000, "w " * 2000, "6 x w" * 800):
            started = time.monotonic()
            webapp.staff_sized_booth_text(text, staff, 36, whole_floor=True, informational=True, own_sizes=set())
            self.assertLess(time.monotonic() - started, 0.5, text[:12])


if __name__ == "__main__":
    unittest.main()
