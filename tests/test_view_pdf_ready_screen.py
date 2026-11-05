"""Source contracts for the existing PDF ready screen and Excel delivery."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ViewPdfReadyScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "webapp/static/app.js").read_text(encoding="utf-8")
        cls.pdf_handler = cls.js.split("async function handleViewPdf(event)", 1)[1].split(
            "function pricingMatchStatus", 1
        )[0]

    def test_no_waiting_tab_or_prepared_window(self):
        self.assertNotIn('window.open("about:blank", "_blank")', self.js)
        self.assertNotIn("preparedWindow", self.js)
        self.assertNotIn("window.open", self.pdf_handler)
        self.assertNotIn("viewCurrentPdfFile(", self.pdf_handler)

    def test_saved_pdf_reuses_ready_screen(self):
        reuse = "if (pdfFileIsFresh(state.pdfFile)) return showGeneratedExportReadyModal(true);"
        self.assertIn(reuse, self.pdf_handler)
        self.assertLess(self.pdf_handler.index(reuse), self.pdf_handler.index("await handleGenerate"))

    def test_generation_precedes_ready_and_cleanup_is_conditional(self):
        ready = "if (generated) pdfReady = showGeneratedExportReadyModal(true);"
        self.assertIn(ready, self.pdf_handler)
        self.assertIn("let pdfReady = false;", self.pdf_handler)
        self.assertIn("if (!pdfReady) hideExcelGeneratingModal();", self.pdf_handler)
        self.assertIn("showExcelGeneratingModal(generationLoadingModalOptions(true));", self.pdf_handler)
        self.assertLess(self.pdf_handler.index("try {"), self.pdf_handler.index("await waitForUiPaint();"))
        self.assertLess(self.pdf_handler.index("await handleGenerate({ viewPdf: true });"), self.pdf_handler.index(ready))

    def test_open_action_and_fallback_keep_guards(self):
        viewer = self.js.split("function viewCurrentPdfFile(", 1)[1].split("async function handleViewPdf", 1)[0]
        self.assertIn('if (commitActiveOutputEditor() === "blocked") return false;', viewer)
        self.assertIn("if (!file?.url) return false;", viewer)
        self.assertIn('window.open(file.url, "_blank")', viewer)
        self.assertIn("if (!opened.closed) opened.opener = null;", viewer)
        self.assertIn('link.target = "_blank";', viewer)
        self.assertIn('link.rel = "noopener";', viewer)
        self.assertNotIn("window.location.assign(file.url)", self.js)
        action = self.js.split("function handleGeneratedExportReadyAction()", 1)[1].split("function generatedExportReadyModalIsOpen", 1)[0]
        self.assertIn('if (commitActiveOutputEditor() === "blocked") return false;', action)
        self.assertIn('action === "pdf" ? viewCurrentPdfFile()', action)
        self.assertNotIn("await ", action)

    def test_excel_keeps_automatic_download(self):
        handler = self.js.split('elements.sideDownloadButton.addEventListener("click", async (event) => {', 1)[1].split(
            "  elements.sideViewPdfButton.addEventListener", 1
        )[0]
        for text in ("event.preventDefault();", "await waitForUiPaint();",
                     'if (commitActiveOutputEditor() === "blocked") return;',
                     "showExcelGeneratingModal(generationLoadingModalOptions(false));",
                     "await handleGenerate();", "downloadCurrentExcelFile();", "hideExcelGeneratingModal();"):
            self.assertIn(text, handler)
        self.assertNotIn("showGeneratedExportReadyModal", handler)
        self.assertNotIn("downloadFileIsFresh", handler)
        self.assertNotIn("existingFile", handler)
