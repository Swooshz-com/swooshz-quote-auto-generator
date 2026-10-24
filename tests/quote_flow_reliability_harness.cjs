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
  const elements = Object.fromEntries(["excelGeneratingModal", "excelGeneratingSpinner", "excelGeneratingActions", "excelGeneratingActionButton", "excelGeneratingCloseButton", "excelGeneratingEyebrow", "excelGeneratingTitle", "excelGeneratingMessage"].map((name) => [name, node()]));
  elements.sideNextButton = { getAttribute() { return "false"; } };
  const state = { isPreparingOutput: false, activeSidePanel: "customer", quoteSessionLoadError: "" };
  const context = vm.createContext({ state, elements, calls, Promise,
    appIsBusy: () => state.isPreparingOutput,
    GENERIC_FAILURE_MESSAGE: "Failed. Please try again.",
    newClientErrorReference: () => "ERR-1234ABCD",
    genericFailureMessage: () => "Failed. Reference: ERR-1234ABCD.",
    genericFailureMessages: () => ["Failed. Reference: ERR-1234ABCD."],
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

// Ordinary beforeunload is tentative: Chrome can stop before pagehide/commit.
// Dispatch the real registered handlers, rather than injecting a terminal flag.
function lifecycleFixture(names = []) {
  const f = fixture(["showExcelGeneratingModal", "hideExcelGeneratingModal", "markPageUnloading", "handleBeforeUnload", ...names]);
  const { context: c, state, elements } = f;
  const handlers = new Map();
  state.isPageUnloading = false;
  state.isRecoveryScopeTransitioning = false;
  state.lineItems = [{}]; state.outputRows = []; state.pendingQuoteDependencyChanges = ["pricing_reference"];
  c.window = { addEventListener: (event, handler) => handlers.set(event, handler) };
  c.pricingReferenceShouldWarnBeforeUnload = () => false;
  vm.runInContext(source.split(/\r?\n/).filter(line => /^  window\.addEventListener\("(?:beforeunload|pagehide)"/.test(line)).join("\n"), c);
  assert.equal(handlers.get("beforeunload"), c.handleBeforeUnload);
  assert.equal(handlers.get("pagehide"), c.markPageUnloading);
  f.beforeunload = () => handlers.get("beforeunload")({ preventDefault() { throw Error("Unexpected warning"); } });
  f.pagehide = () => handlers.get("pagehide")({ persisted: false });
  return f;
}
async function lifecycleCases() {
  const families = ["images", "customer", "quote_company", "dependency_customer", "dependency_images", "dependency_basis", "basis"];
  let cases = 0;
  for (const family of families) for (const phase of ["ordinary", "cancel_before", "cancel_pending", "pagehide_pending"]) for (const outcome of ["success", "failure", "throw"]) {
    const f = lifecycleFixture(["withQuoteStepLoading", "advanceCustomerStep", "goToNextSidePanel", "confirmQuoteDependencyChange", "confirmBasis"]);
    const { context: c, state, elements } = f;
    const panel = family.replace("dependency_", "");
    const panels = ["images", "customer", "quote_company", "basis", "output"];
    state.activeSidePanel = panel;
    c.SIDE_PANEL_SEQUENCE = panels;
    c.activeSidePanelIndex = () => panels.indexOf(state.activeSidePanel);
    c.setSidePanel = next => { state.activeSidePanel = next; return true; };
    c.quoteHasDerivedResults = () => true;
    c.quoteSessionHasFreshOutputExports = () => false;
    c.ensureClientQuoteSessionId = () => "quote-test";
    c.customerStepCheckpointForCurrent = () => ({ sessionId: "quote-test" });
    c.hasReferenceFilesForNavigation = () => true;
    c.markQuoteSessionDraftSaveStartedAfterCustomerStep = () => { state.quoteSessionDraftSaveStarted = true; };
    c.currentQuoteSessionDraftState = () => ({});
    c.safeQuoteSessionId = id => id;
    c.quoteCommercialStrictDataEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    c.pendingPricingReferenceSelectionChanged = () => false;
    c.saveSessionState = () => {};
    c.updateSidePanelNav = () => {};
    c.hideQuoteDependencyConfirmModal = () => {};
    c.clearGeneratedQuoteState = () => {};
    c.setWorkflowStage = () => {};
    c.applyPendingPricingReferenceSelection = () => false;
    c.normalizeActiveJob = value => value;
    c.newClientOperationId = () => "operation-test";
    c.basisConfirmBlockReason = () => "";
    c.missingDetailFields = () => [];
    c.clearActiveJob = () => { state.activeJob = null; };
    c.showBlockedBasisAction = () => {};
    c.refreshOutputRowsFromLineItems = () => {};
    c.snapshotOutputRows = rows => rows;
    c.outputRowsToLineItems = () => [{}];
    c.renderPricingMatches = () => {};
    c.renderMatchSummary = () => {};
    c.setResultStatus = () => {};
    c.outputRowsValid = () => ({ errors: [] });
    c.renderOutputValidationMessages = () => {};
    let release, requests = 0;
    const gate = new Promise(resolve => { release = resolve; });
    async function operation() {
      requests++; await gate;
      if (outcome === "throw") throw Error("synthetic-controlled-error");
      if (outcome === "failure") { state.quoteSessionLoadError = "Synthetic failure. Reference: ERR-1234ABCD."; return null; }
      return { session_id: "quote-test" };
    }
    c.saveQuoteSessionDraftState = family === "basis" ? async () => ({ session_id: "quote-test" }) : operation;
    c.startQuoteSessionDraftSaveAfterCustomerStep = operation;
    c.refreshLineItemsFromServer = async () => ({ ok: Boolean(await operation()), data: {} });
    if (phase === "cancel_before") f.beforeunload();
    const pending = family.startsWith("dependency_") ? c.confirmQuoteDependencyChange() : c.goToNextSidePanel();
    // Attach rejection handling before releasing the synthetic transport.
    const settled = pending.then(value => ({ value }), error => ({ error }));
    await new Promise(setImmediate);
    assert.equal(requests, 1, family);
    assert(state.isPreparingOutput && !elements.excelGeneratingModal.hidden);
    await c.goToNextSidePanel();
    assert.equal(requests, 1, "Busy clicks cannot dispatch another save");
    if (phase === "cancel_pending") f.beforeunload();
    if (phase === "pagehide_pending") { f.beforeunload(); f.pagehide(); }
    assert.equal(state.isPageUnloading, phase === "pagehide_pending");
    release(); const result = await settled;
    assert.equal(state.isPreparingOutput, false);
    assert.equal(requests, 1);
    if (phase !== "pagehide_pending") {
      if (family === "basis" || outcome === "success") assert(elements.excelGeneratingModal.hidden, family + ": terminal loading must close");
      else assert(!elements.excelGeneratingModal.hidden && elements.excelGeneratingSpinner.hidden && !elements.excelGeneratingActions.hidden);
    }
    if (family === "basis" && outcome === "throw") assert(result.error);
    if (family === "customer" && outcome !== "success") assert.equal(state.activeSidePanel, "customer");
    cases++;
  }
  const f = lifecycleFixture(["withQuoteStepLoading", "goToNextSidePanel", "postJsonFetchFailure", "jsonFromResponse", "getJson", "fetchCompanyProfileExport", "finishDashboardOperation"]);
  const { context: c, state, calls } = f;
  let logged = 0, cleared = 0;
  c.logClientEvent = () => { logged++; };
  c.fetchFailureLogDetails = url => ({ url, reason: "fetch_failed" });
  c.safeProfileId = value => value;
  c.clearDashboardOperation = () => { cleared++; };
  c.fetch = async () => { throw Error("synthetic-fetch-failure"); };
  f.beforeunload();
  assert.equal(c.postJsonFetchFailure("/synthetic").data.page_unloading, false);
  assert.equal((await c.getJson("/synthetic")).data.page_unloading, false);
  await c.fetchCompanyProfileExport("profile-test");
  c.finishDashboardOperation(); assert.equal(cleared, 1); assert.equal(logged, 3);
  const abortedBody = { json: async () => { const error = Error("synthetic-body"); error.name = "AbortError"; throw error; } };
  assert.equal((await c.jsonFromResponse(abortedBody, { persistSession: false })).page_unloading, false);
  f.pagehide();
  assert.equal(c.postJsonFetchFailure("/synthetic").data.page_unloading, true);
  assert.equal((await c.getJson("/synthetic")).data.page_unloading, true);
  await c.fetchCompanyProfileExport("profile-test");
  c.finishDashboardOperation(); assert.equal(cleared, 1); assert.equal(logged, 3);
  assert.equal((await c.jsonFromResponse(abortedBody, { persistSession: false })).page_unloading, true);
  const mode = lifecycleFixture(["withQuoteStepLoading", "goToNextSidePanel"]);
  mode.beforeunload(); mode.state.activeSidePanel = "quote_company";
  await mode.context.goToNextSidePanel(); assert(mode.calls.includes("analysis_choice"));
  mode.state.activeSidePanel = "output"; const before = mode.calls.length;
  await mode.context.goToNextSidePanel(); assert.equal(mode.calls.length, before);
  const transfer = lifecycleFixture(); transfer.state.isRecoveryScopeTransitioning = true;
  transfer.beforeunload(); assert.equal(transfer.state.isPageUnloading, true, "Explicit scope transfer fence remains terminal");
  for (const departure of ["stopped", "pagehide", "signout"]) {
    const auth = lifecycleFixture(["recoverRequiredAuthentication"]);
    const a = auth.context, s = auth.state;
    a.authenticationRecoveryPromise = null; a.browserRecoveryEpoch = 1;
    a.currentBrowserRecoveryScope = () => "synthetic-scope";
    a.clearQuoteSessionDraftSaveTimer = () => {};
    a.renderAuthState = () => {};
    a.renderMessages = () => {};
    a.commitActiveOutputEditor = () => "blocked";
    s.authenticated = true; s.csrfToken = "synthetic-token";
    auth.beforeunload();
    if (departure === "pagehide") auth.pagehide();
    if (departure === "signout") s.isSigningOut = true;
    assert.equal(await a.recoverRequiredAuthentication(401, { status: "auth_required" }), true);
    assert.equal(Boolean(s.authenticationRecoveryRequested), departure === "stopped");
    assert.equal(s.csrfToken, departure === "stopped" ? "" : "synthetic-token");
  }
  console.log(JSON.stringify({ lifecycle_cases: cases, handler_events: "actual beforeunload/pagehide", provider_requests: 0 }));
}

(async () => {
  const mode = process.argv[2];
  if (mode === "loading") {
    await lifecycleCases();
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
