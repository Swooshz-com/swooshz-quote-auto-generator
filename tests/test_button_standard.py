"""Text contracts for the three approved button groups; no source execution."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ButtonStandardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "webapp/static/styles.css").read_text(encoding="utf-8")
        cls.rules = cls.css.split("/* Shared control sizing for approved quote actions. */", 1)[1]
        cls.js = (ROOT / "webapp/static/app.js").read_text(encoding="utf-8")

    def test_shared_standard_geometry_and_focus(self):
        standard = self.rules.split(".basis-line-actions > button {", 1)[0]
        for selector in (
            ".company-preset-profile-card .company-preset-action-panel > button",
            ".company-preset-profile-card .company-preset-more > .company-preset-more-button",
            '.excel-generating-actions:has([data-export-action="pdf"]) > button',
        ):
            self.assertIn(selector, standard)
            self.assertIn(selector + ":focus-visible", self.rules)
        for declaration in ("height: 40px;", "min-height: 40px;", "min-width: 96px;",
                            "padding: 0 16px;", "font-size: 14px;", "gap: 8px;",
                            "border-radius: var(--radius-md);"):
            self.assertIn(declaration, standard)
        self.assertIn("outline: 2px solid var(--green);", self.rules)
        self.assertIn("outline-offset: 2px;", self.rules)
        self.assertIn("justify-content: flex-end;", self.rules)
        self.assertNotIn("--quote-button-", self.rules)

    def test_review_squares_and_mobile_targets(self):
        desktop, mobile = self.rules.split("@media (max-width: 640px)", 1)
        self.assertIn("grid-template-columns: repeat(3, 32px);", desktop)
        self.assertIn("width: 32px;", desktop)
        self.assertIn("height: 32px;", desktop)
        self.assertIn(".basis-line-actions > button:focus-visible", desktop)
        for declaration in ("height: 44px;", "min-height: 44px;", "width: 44px;",
                            "grid-template-columns: repeat(3, 44px);", "width: max-content;"):
            self.assertIn(declaration, mobile)

    def test_pdf_order_and_excel_restoration(self):
        ready = self.js.split("function showGeneratedExportReadyModal(", 1)[1].split(
            "function handleGeneratedExportReadyAction(", 1)[0]
        self.assertIn("""if (viewPdf) {
    elements.excelGeneratingActions?.prepend(elements.excelGeneratingCloseButton);
  } else {
    elements.excelGeneratingActions?.append(elements.excelGeneratingCloseButton);
  }""", ready)
        self.assertLess(ready.index(".prepend("), ready.index('classList.add("is-open", "is-ready")'))

    def test_profile_icons_keep_their_width(self):
        self.assertIn("""@media (min-width: 881px) {
  .company-preset-card.company-preset-profile-card {
    grid-template-columns: minmax(0, 258px) 168px;
  }
}""", self.rules)
        self.assertIn(""".company-preset-profile-card .company-preset-action-panel > button > svg,
.company-preset-profile-card .company-preset-more-button > svg {
  flex-shrink: 0;
}""", self.rules)
        mobile = self.rules.split("@media (max-width: 640px)", 1)[1]
        self.assertIn(""".company-preset-profile-card .company-preset-action-panel > button {
    padding-inline: 4px;
  }""", mobile)


    def test_follow_ups_338(self):
        follow = self.css.split("/* Button standard follow-ups (#338). */", 1)[1]
        desktop, rest = follow.split("@media (max-width: 640px)", 1)
        mobile, narrow = rest.split("@media (max-width: 379px)", 1)
        # L3: section check/X use the row grid, size and gap.
        self.assertIn(""".basis-section-heading {
  padding-right: 9px;
}""", desktop)
        self.assertIn(""".basis-section-actions {
  grid-template-columns: repeat(3, 32px);
  gap: 8px;
}""", desktop)
        self.assertIn("width: 32px;\n  height: 32px;", desktop)
        self.assertIn("grid-template-columns: repeat(3, 44px);", mobile)
        self.assertIn("height: 44px;", mobile)
        # L2: the copy column fills the panel only while the PDF ready actions show.
        self.assertIn(""".excel-generating-copy:has(> .excel-generating-actions:not([hidden]) > [data-export-action="pdf"]) {
  flex: 1 1 auto;
}""", desktop)
        # N3: narrow phones stack the profile actions and give review actions the full row.
        self.assertIn(""".company-preset-profile-card .company-preset-action-panel {
    grid-template-columns: minmax(0, 1fr);
  }""", narrow)
        self.assertIn("grid-template-columns: repeat(auto-fill, 44px);", narrow)
        self.assertIn("grid-column: 1 / -1;", narrow)
        self.assertIn("margin-left: 9px;", narrow)

if __name__ == "__main__":
    unittest.main()
