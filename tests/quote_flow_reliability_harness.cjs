const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("webapp/static/app.js", "utf8");
function extract(name) {
  const match = new RegExp("(?:async )?function " + name + "\\(").exec(source);
  assert(match, "Missing production function " + name);
  const rest = source.slice(match.index);
  const end = rest.slice(1).search(/\n(?:async )?function /);
  return end < 0 ? rest : rest.slice(0, end + 1);
}
function fixture(names) {
  const calls = [];
  const node = () => ({ hidden: true, textContent: "", dataset: {}, focus() {}, classList: { add() {}, remove() {} } });
  const elements = Object.fromEntries(["excelGeneratingModal", "excelGeneratingSpinner", "excelGeneratingActions", "excelGeneratingActionButton", "excelGeneratingCloseButton"].map((name) => [name, node()]));
  elements.sideNextButton = { getAttribute() { return "false"; } };
  const state = { isPreparingOutput: false, activeSidePanel: "customer", quoteSessionLoadError: "" };
  const context = vm.createContext({ state, elements, calls, Promise,
    appIsBusy: () => state.isPreparingOutput,
    GENERIC_FAILURE_MESSAGE: "Failed. Please try again.",
    newClientErrorReference: () => "ERR-1234ABCD",
    genericFailureMessage: () => "Failed. Reference: ERR-1234ABCD.",
    logClientEvent: (_event, details) => { calls.push({ errorReference: details.error_reference }); },
    syncControlStates() {}, waitForUiPaint: async () => { calls.push("paint"); },
    showExcelGeneratingModal() { elements.excelGeneratingModal.hidden = false; elements.excelGeneratingSpinner.hidden = false; calls.push("show"); },
    hideExcelGeneratingModal() { elements.excelGeneratingModal.hidden = true; calls.push("hide"); },
    commitActiveOutputEditor: () => "none", activeSidePanelIndex: () => 1,
    quoteDependencyChangesForNext: () => [], quoteHasDerivedResults: () => false,
    applyPendingPricingReferenceSelection() {}, advanceCustomerStep: async () => true,
    requestStartAnalysis() { calls.push("analysis_choice"); },
    requestQuoteDependencyChangeConfirmation() { calls.push("dependency_choice"); },
  });
  vm.runInContext(names.map(extract).join("\n"), context);
  return { context, calls, state, elements };
}
(async () => {
  const mode = process.argv[2];
  if (mode === "loading") {
    const { context: c, calls, state, elements } = fixture(["withQuoteStepLoading"]);
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    const first = c.withQuoteStepLoading(async () => { calls.push("request"); return pending; });
    await new Promise(setImmediate);
    assert.deepEqual(calls, ["show", "paint", "request"]);
    assert(state.isPreparingOutput && !elements.excelGeneratingModal.hidden);
    assert.equal(await c.withQuoteStepLoading(() => { throw Error("duplicate dispatched"); }), false);
    release(true);
    assert.equal(await first, true);
    assert(!state.isPreparingOutput && elements.excelGeneratingModal.hidden);
    assert.equal(await c.withQuoteStepLoading(async () => { throw Error("private-controlled-failure"); }), false);
    assert(!state.isPreparingOutput && !elements.excelGeneratingModal.hidden);
    assert(!JSON.stringify(calls).includes("private-controlled-failure"));
    assert(calls.some((entry) => entry.errorReference === "ERR-1234ABCD"));
    assert.equal(await c.withQuoteStepLoading(async () => true), true);
    assert.equal(await c.withQuoteStepLoading(async () => {
      state.quoteSessionLoadError = "Quote could not be saved. Reference: ERR-1234ABCD.";
      return false;
    }), false);
    assert(!state.isPreparingOutput && !elements.excelGeneratingModal.hidden);
    assert(elements.excelGeneratingSpinner.hidden && !elements.excelGeneratingActions.hidden);
  } else if (mode === "admission") {
    const { context: c, calls, state } = fixture(["withQuoteStepLoading", "goToNextSidePanel"]);
    c.elements.sideNextButton.getAttribute = () => "true";
    c.elements.sideNextButton.blur = () => {};
    await c.goToNextSidePanel();
    assert.deepEqual(calls, []);
    c.elements.sideNextButton.getAttribute = () => "false";
    c.quoteDependencyChangesForNext = () => ["pricing_reference"];
    await c.goToNextSidePanel();
    assert.deepEqual(calls, ["dependency_choice"]);
    calls.length = 0;
    c.quoteDependencyChangesForNext = () => [];
    state.activeSidePanel = "quote_company";
    await c.goToNextSidePanel();
    assert.deepEqual(calls, ["analysis_choice"]);
    calls.length = 0;
    state.activeSidePanel = "basis";
    let release, settled = false;
    c.confirmBasis = () => new Promise((resolve) => { release = resolve; });
    const confirmation = c.goToNextSidePanel().then(() => { settled = true; });
    await Promise.resolve();
    assert.equal(settled, false, "Next must await the nested basis operation");
    release(); await confirmation;
    assert.equal(settled, true);
    assert.deepEqual(calls, []);
  } else if (mode === "detail") {
    const { context: c, state } = fixture(["loadQuoteSessionDetail"]);
    c.safeQuoteSessionId = (id) => id;
    c.genericFailureMessage = () => "Failed. Reference: ERR-1234ABCD.";
    c.renderQuoteDashboard = () => {};
    for (const response of [{ ok: false, data: {} }, { ok: true, data: {} }, { ok: true, data: { quote_session: { session_id: "wrong" } } }]) {
      c.getJson = async () => response;
      assert.equal(await c.loadQuoteSessionDetail("quote-test"), null);
      assert(state.quoteSessionLoadError);
    }
    c.getJson = async () => ({ ok: true, data: { quote_session: { session_id: "quote-test", draft_state: {} } } });
    assert.equal((await c.loadQuoteSessionDetail("quote-test")).session_id, "quote-test");
    assert.equal(state.quoteSessionLoadError, "");
  } else if (mode === "pdf") {
    const { context: c, calls, state, elements } = fixture(["revisionNumber", "pdfFileIsFresh", "handleViewPdf"]);
    elements.sideViewPdfButton = { getAttribute: () => "false" };
    state.outputRevision = 5;
    state.pdfFileRevision = 5;
    state.pdfFile = { url: "/api/quote-sessions/quote-test/view/pdf" };
    c.viewCurrentPdfFile = (file = state.pdfFile, prepared) => { calls.push({ viewed: file.url, prepared: Boolean(prepared) }); return true; };
    c.handleGenerate = async () => { throw Error("fresh PDF must not regenerate"); };
    const event = { preventDefault() {} };
    await c.handleViewPdf(event);
    assert.deepEqual(calls, [{ viewed: state.pdfFile.url, prepared: false }]);
    calls.length = 0;
    let popup;
    c.window = { open(url) { assert.equal(url, "about:blank"); popup = { closed: false, close() { this.closed = true; } }; return popup; } };
    c.generationLoadingModalOptions = () => ({});
    state.pdfFileRevision = 4;
    c.handleGenerate = async () => false;
    await c.handleViewPdf(event);
    assert(popup.closed && elements.excelGeneratingModal.hidden, "failed generation must close the waiting tab and spinner");
    assert(!calls.some((entry) => entry.viewed), "stale PDF was viewed");
    c.handleGenerate = async () => { throw Error("controlled PDF failure"); };
    await assert.rejects(c.handleViewPdf(event), /controlled PDF failure/);
    assert(popup.closed && elements.excelGeneratingModal.hidden);
    c.handleGenerate = async () => { state.pdfFile = { url: "/api/quote-sessions/quote-test/view/pdf?current" }; state.pdfFileRevision = 5; return true; };
    calls.length = 0;
    await c.handleViewPdf(event);
    assert(!popup.closed && calls.some((entry) => entry.viewed === state.pdfFile.url && entry.prepared));
    assert(elements.excelGeneratingModal.hidden);
    calls.length = 0;
    elements.sideViewPdfButton.getAttribute = () => "true";
    c.outputRowsValid = () => ({ valid: false, errors: ["Review required"] });
    c.renderOutputValidationMessages = () => { calls.push("blocked"); };
    await c.handleViewPdf(event);
    assert.deepEqual(calls, ["blocked"]);
  } else if (mode === "ack") {
    for (const panel of ["images", "quote_company"]) {
      const { context: c, state, elements } = fixture(["withQuoteStepLoading", "goToNextSidePanel"]);
      const panels = ["images", "customer", "quote_company", "basis", "output"];
      c.SIDE_PANEL_SEQUENCE = panels;
      c.activeSidePanelIndex = () => panels.indexOf(state.activeSidePanel);
      c.setSidePanel = (next) => { state.activeSidePanel = next; return true; };
      c.quoteHasDerivedResults = () => true;
      c.quoteSessionHasFreshOutputExports = () => false;
      let requests = 0, fail = true;
      const save = async () => { requests++; if (fail) { state.quoteSessionLoadError = "Could not be saved. Reference: ERR-1234ABCD."; return null; } return { session_id: "quote-test" }; };
      c.saveQuoteSessionDraftState = save;
      c.startQuoteSessionDraftSaveAfterCustomerStep = save;
      state.activeSidePanel = panel;
      assert.equal(await c.goToNextSidePanel(), false);
      assert.equal(state.activeSidePanel, panel, "failed Next save must restore its source step");
      assert(!state.isPreparingOutput && !elements.excelGeneratingModal.hidden && elements.excelGeneratingSpinner.hidden);
      assert.equal(requests, 1);
      fail = false;
      assert.equal(await c.goToNextSidePanel(), true);
      assert.equal(state.activeSidePanel, panel === "images" ? "customer" : "basis");
      assert(elements.excelGeneratingModal.hidden && !state.isPreparingOutput);
      assert.equal(requests, 2, "one retry must produce one save");
    }
  } else if (mode === "checkpoint") {
    const { context: c, state } = fixture(["canonicalPricingAuthorityText", "customerStepCheckpointForCurrent"]);
    vm.runInContext(source.match(/^const PRICING_AUTHORITY_WHITESPACE_RE = .+;/m)[0], c);
    c.PRICING_REFERENCE_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
    c.safeQuoteSessionId = (id) => id;
    c.currentBrowserRecoveryScope = () => "synthetic-scope";
    c.currentPricingReference = () => ({ digest_sha256: "sha256:" + "a".repeat(64) });
    c.customerDetailsBlockReason = () => "";
    c.quoteCommercialReviewRequired = () => false;
    state.quoteSessionId = "quote-test";
    state.pricingReferenceId = "reference-test";
    state.pricingReferenceSource = "company";
    const details = { client: { name: "Synthetic", address: "Cafe\u0301 Street\nSingapore" }, project: { title: "Synthetic" }, quote_date: "2026-10-21", project_number: "TEST-1" };
    c.collectQuoteDetails = () => details;
    const original = details.client.address;
    const before = JSON.stringify(c.customerStepCheckpointForCurrent());
    assert.equal(details.client.address, original, "checkpoint normalisation must not alter visible/export address data");
    details.client.address = "Caf\u00e9 Street Singapore";
    assert.equal(JSON.stringify(c.customerStepCheckpointForCurrent()), before);
    details.client.address = "Different Street Singapore";
    assert.notEqual(JSON.stringify(c.customerStepCheckpointForCurrent()), before);
    c.customerDetailsBlockReason = () => "missing";
    assert.equal(c.customerStepCheckpointForCurrent(), null);
  } else throw Error("Unknown mode");
  console.log(JSON.stringify({ mode, status: "PASS" }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
