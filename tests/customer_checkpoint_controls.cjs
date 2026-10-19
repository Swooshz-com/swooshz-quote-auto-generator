// Synthetic DOM and transport adapters; navigation, checkpoint, selection,
// payload and save-acknowledgement handlers are extracted from production source.
const fs = require("fs");
const assert = require("assert");
const source = fs.readFileSync("webapp/static/app.js", "utf8");
const digestValidatorSource = source.match(/^const PRICING_REFERENCE_DIGEST_RE = (.+);$/m);
assert.ok(digestValidatorSource, "Missing production pricing digest validator");
const PRICING_REFERENCE_DIGEST_RE = eval(digestValidatorSource[1]);
function extract(name) {
  const match = new RegExp(`(?:async )?function ${name}\\(`).exec(source);
  assert.ok(match, `Missing production function ${name}`);
  const start = match.index;
  const body = source.indexOf(") {", start) + 2;
  let depth = 0;
  for (let i = body; i < source.length; i++) {
    if (source[i] === "{") depth++;
    if (source[i] === "}" && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error(`Unclosed production function ${name}`);
}
const clone = value => JSON.parse(JSON.stringify(value));
const SIDE_PANEL_SEQUENCE = ["images", "customer", "quote_company", "basis", "output"];
const MAX_REFERENCE_IMAGES = 12;
const QUOTE_SESSION_STATE_VERSION = 1;
const QUOTE_COMMERCIAL_REVIEW_STATUS = "review_required";
const MISSING_PRICING_REFERENCES_MESSAGE = "No synthetic pricing references available";
const ANALYSIS_MODE_STANDARD = "standard";
const EMPTY_BASIS = {};
const PRICING_REFERENCE_SOURCES = new Set(["company", "local", "bundled"]);
const controls = [];
let state = {};
let saveMode = "success";
let activeOutputEditor = null;
let committedOutputCurrency = "";
let blockedEditorCommits = 0;
function commitOutputEditor(editor) {
  assert.strictEqual(editor, activeOutputEditor.editor);
  assert.strictEqual(editor.value, "1.005");
  blockedEditorCommits++;
  return "blocked";
}
let postCount = 0;
let releaseSave = null;
let savedDraft = null;
let scope = "synthetic-scope";
let quoteSessionInitialSavePromise = null;
let quoteSessionDraftSavePromise = null;
let quoteSessionConfirmedDraftSessionId = "";
let quoteSessionConfirmedDraftKey = "";
let quoteSessionConfirmedDraftFileKey = "";
function node(value = "Synthetic") {
  return {value, textContent: "", hidden: false, disabled: false, title: "", attrs: {},
    dataset: {}, classList: {toggle() {}, add() {}, remove() {}}, focus() {}, blur() {},
    setAttribute(k, v) { this.attrs[k] = v; }, getAttribute(k) { return this.attrs[k]; },
    querySelector() { return node(); }};
}
const elements = new Proxy({}, {get(target, key) {return target[key] ||= node();}});
const rail = SIDE_PANEL_SEQUENCE.map(panel => ({...node(), dataset: {sidePanel: panel}}));
const document = {body: {dataset: {}}, querySelectorAll(selector) {
  return selector === "button[data-side-panel]" ? rail : [];
}};
const window = {setTimeout(callback) { callback(); }};
global.fetch = () => { throw new Error("Unexpected network/provider request"); };
function syncRichTextSources() {}
function collectQuoteDetails() {
  return {quote_date: elements.quoteDate.value, project_number: elements.projectNumber.value,
    client: {name: elements.clientName.value, attention: elements.clientAttention.value,
      title: elements.clientTitle.value, address: elements.clientAddress.value},
    project: {title: elements.projectTitle.value, show_name: elements.showName.value}};
}
function currentBrowserRecoveryScope() {return scope;}
function currentGenerator() { return {label: "Synthetic", intakeSubtitle: "Synthetic", analyzeLabel: "Start Analysis"}; }
function currentProfile() {return {label: "Synthetic Profile"};}
function selectedPreset() {return null;}
function generationProfileIdForPayload() {return "synthetic-profile";}
function quoteCommercialStateIsOwned() {return false;}
function quoteSessionHasFreshOutputExports() {return Boolean(state.downloadFile);}
function dashboardCommercialsFromState() {return {};}
function collectTaxDetails() {return {rate: 0};}
function selectedPricingReferenceTax() {return {rate: 0};}
function selectedPricingReferenceCurrency() {return "SGD";}
function collectQuoteCurrency() {return "SGD";}
function collectQuoteExchangeRate() {return 1;}
function collectRichTextDetails() {return {};}
function normalizeAnalysisMode() {return "standard";}
function normalizeRestorableOverlay(value) {return value;}
function normalizeQuoteCommercialTouched(value) {return value;}
function quoteDetailsWithSessionLogoMetadata(value) {return value;}
function basisChatBrowserSnapshot() {return null;}
function activeJobBrowserSnapshot() {return null;}
function sessionImageMetadata(value) {return value;}
function quoteBasisPersistenceProjection() {return {quote_basis: state.quoteBasis, quote_basis_sections: state.quoteBasisSections};}
function outputRowsToLineItems(value) {return value;}
function sessionFileRecordsFromDraft() {return state.images;}
function currentGenerationContext() {return {session_id: state.quoteSessionId, run_id: ""};}
function transitionGenerationContext(id) {state.quoteSessionId = id;}
function ensureClientQuoteSessionId() {return state.quoteSessionId;}
function currentQuoteSessionIsRestoredFromDashboard() {return false;}
function mergeDashboardQuoteSession() {}
function saveSessionState() {}
function serverQuoteCommercialReviewFromResponse() {return null;}
function errorReferenceFrom() {return "";}
function newClientErrorReference() {return "ERR-12345678";}
function quoteSessionDraftSynchronizationFailureMessage() {return "Synthetic save failure";}
function logClientEvent() {}
async function postJson(url, payload) {
  assert.strictEqual(url, "/api/quote-sessions", "Only synthetic checkpoint saves are permitted");
  postCount++;
  const mode = saveMode;
  if (mode === "deferred") await new Promise(resolve => {releaseSave = resolve;});
  if (mode === "failure") return {ok: false, status: 500, data: {}};
  savedDraft = clone(payload.draft_state);
  return {ok: true, status: 200, data: {status: mode === "bad_status" ? "unknown" : "saved",
    quote_session: {session_id: mode === "wrong_session" ? "quote-other" : payload.session_id}}};
}
function hasReferenceFilesForNavigation() {return state.images.length > 0;}
function hasReferenceFilesForAnalysis() {return hasReferenceFilesForNavigation();}
function initializeFreshPricingReferenceForCustomer() {}
function resetQuoteFlowScroll() {}
function renderPendingPricingReferenceBasis() {}
function renderClarificationQuestions() {}
function renderProfileOptions() {elements.profileSelect.value = `${state.pricingReferenceSource}::${state.pricingReferenceId}`;}
function persistLastPricingReferenceSelection() {}
function syncSelectedPricingReference() {}
function syncControlStates() {updateSidePanelNav();}
function setWorkflowStage(value) {state.workflowStage = value;}
function invalidateBasisChatAuthority() {}
function renderBasisEmptyState() {}
function clearAiFailureBanner() {}
function renderMessages() {}
function renderMatchSummary() {}
function renderPricingMatches() {}
function clearPricingReviewMessages() {}
function setResultStatus() {}
function renderQuoteCommercialReviewState() {}
function updateDownloadButton() {}
function hasSubmittedQuoteBasis() {return state.lineItems.length > 0;}
function hasCompletedQuoteBasis() {return state.basisConfirmed;}
function basisConfirmBlockReason() {return "";}
function referenceFilesChangedSinceAnalysis() {return false;}
function requestStartAnalysis() {throw new Error("Analysis must never be chained");}
function confirmBasis() {throw new Error("Basis confirmation not part of these controls");}
function showBlockedAction() {}
function showBlockedBasisAction() {}
const functions = ["safeQuoteSessionId", "isPlainObject", "revisionNumber", "splitLines",
  "normalizeTextNewlines", "normalizedContentFingerprint", "pendingPricingReference",
  "safeQuoteSessionErrorReference", "canStartAnalysis", "startQuoteSessionDraftSaveAfterCustomerStep",
  "currentPricingReference", "pricingReferenceSelectionFromValue", "pricingReferenceFromSelectionValue",
  "pricingReferenceSelectValue", "pendingPricingReferenceSelection", "pendingPricingReferenceSelectionChanged",
  "applyPendingPricingReferenceSelection", "resetPendingPricingReferenceSelection", "handleProfileSelectionChange",
  "quoteCommercialReviewRequired", "clearQuoteCommercialReview", "clearGeneratedQuoteState", "setDownloadFiles",
  "quoteDraftHasAiAnalysis", "quoteDraftHasOutputState", "quoteHasDerivedResults", "quoteDependencyChangesForNext",
  "quoteDependencyChangeCopy", "hideQuoteDependencyConfirmModal", "requestQuoteDependencyChangeConfirmation",
  "confirmQuoteDependencyChange", "missingCustomerFields", "customerDetailsBlockReason",
  "missingQuoteCompanyFields", "missingDetailFields", "startAnalysisBlockReason", "quoteCompanyDetailsBlockReason",
  "buildSessionSnapshot", "currentQuoteSessionDraftState", "currentQuoteSessionPayload", "buildPayload",
  "quoteSessionDraftComparisonKey", "quoteSessionDraftFileComparisonKey", "rememberConfirmedQuoteSessionDraft",
  "quoteSessionDraftFilesAreConfirmed", "quoteSessionDraftIsConfirmed", "saveCurrentQuoteSession",
  "saveQuoteSessionDraftState", "quoteSessionDraftStateCanSave", "quoteSessionSaveFailureFrom",
  "markQuoteSessionDraftSaveStartedAfterCustomerStep", "quoteSessionDraftReachedCustomerStep",
  "quoteCommercialStrictDataEqual", "customerStepCheckpointForCurrent", "customerStepCheckpointIsCurrent",
  "customerNextBlockReason",
  "checkpointGatedSidePanel", "advanceCustomerStep", "sidePanelBlockReason", "setSidePanel",
  "activeSidePanelIndex", "updateSidePanelNav", "appIsBusy", "appBusyTitle", "quoteOutputProgressForNavigation",
  "commitActiveOutputEditor", "goToNextSidePanel", "goToPreviousSidePanel", "furthestQuoteSessionSidePanel", "restoredQuoteSessionSidePanel"];
eval(functions.map(extract).join("\n"));
function fresh() {
  state = {quoteSessionId: "quote-synthetic-checkpoint", activeSidePanel: "customer",
    quoteSessionDraftSaveStarted: true, customerStepCheckpoint: null, customerStepCheckpointSaveBusy: false,
    pricingReferenceId: "reference-a", pricingReferenceSource: "company", quoteCommercialLifecycle: "NEW_UNINITIALISED",
    pricingReferenceSelectionIntent: null, quoteCommercialSnapshot: null, quoteCommercialReview: null,
    pricingReferences: ["a", "b"].map(id => ({id: `reference-${id}`, source: "company", digest_sha256: `sha256:${id.repeat(64)}`})),
    images: [{name: "synthetic.png", type: "image/png", size: 1, content_fingerprint: `sha256:${"c".repeat(64)}`}],
    quoteBasis: {}, quoteBasisSections: [], lineItems: [], outputRows: [], originalOutputRows: [],
    analysisFindings: [], blockingClarificationQuestions: [], pendingQuoteDependencyChanges: [],
    basisConfirmed: false, outputRevision: 0, downloadFile: null, pdfFile: null, boothDimensions: {},
    activeAppView: "quote", workflowStage: "details_review", isRecoveryScopeTransitioning: false};
  scope = "synthetic-scope"; saveMode = "success"; savedDraft = null;
  activeOutputEditor = null; blockedEditorCommits = 0;
  quoteSessionInitialSavePromise = null; quoteSessionDraftSavePromise = null;
  quoteSessionConfirmedDraftSessionId = ""; quoteSessionConfirmedDraftKey = ""; quoteSessionConfirmedDraftFileKey = "";
  elements.profileSelect.value = "company::reference-a";
  elements.quoteDate.value = "2026-10-13";
  elements.quoteDependencyConfirmModal.hidden = true;
  elements.clientName.value = "Synthetic Customer";
  state.headerLogo = {data_url: "synthetic"};
  updateSidePanelNav();
}
function selectB() {elements.profileSelect.value = "company::reference-b"; handleProfileSelectionChange();}
function committedCheckpoint() {return clone(state.customerStepCheckpoint);}
function control(name, action) {action(); controls.push(name);}
(async () => {
  fresh();
  const noEditorPosts = postCount;
  await goToNextSidePanel();
  control("no Output editor permits acknowledged Customer Next", () => {
    assert.strictEqual(state.activeSidePanel, "quote_company");
    assert.strictEqual(postCount, noEditorPosts + 1);
    assert.strictEqual(customerStepCheckpointIsCurrent(), true);
  });
  fresh(); selectB();
  activeOutputEditor = {editor: {value: "1.005"}};
  const blockedState = clone(state), blockedPosts = postCount;
  await goToNextSidePanel();
  goToPreviousSidePanel();
  control("blocked Output editor prevents forward/backward navigation and save", () => {
    assert.strictEqual(blockedEditorCommits, 2);
    assert.deepStrictEqual(state, blockedState);
    assert.strictEqual(postCount, blockedPosts);
    assert.strictEqual(savedDraft, null);
    assert.strictEqual(activeOutputEditor.editor.value, "1.005");
  });
  for (const [name, digest] of [
    ["bare hex", "a".repeat(64)],
    ["malformed", "sha256:invalid"],
    ["truncated", `sha256:${"a".repeat(63)}`],
  ]) {
    fresh(); state.pricingReferences[0].digest_sha256 = digest;
    await goToNextSidePanel();
    control(`${name} pricing digest cannot save or unlock Customer`, () => {
      assert.strictEqual(customerStepCheckpointForCurrent(), null);
      assert.strictEqual(state.customerStepCheckpoint, null);
      assert.strictEqual(savedDraft, null);
      assert.strictEqual(state.activeSidePanel, "customer");
      assert.strictEqual(setSidePanel("quote_company"), false);
    });
  }
  fresh(); state.pricingReferences = [];
  control("missing pricing reference keeps Next disabled", () => {
    updateSidePanelNav();
    assert.strictEqual(elements.sideNextButton.disabled, true);
    assert.strictEqual(elements.sideNextButton.getAttribute("aria-disabled"), "true");
  });
  fresh(); selectB();
  control("fresh rail locked and selection remains pending", () => {
    assert.ok(rail.find(n => n.dataset.sidePanel === "quote_company").disabled);
    assert.strictEqual(elements.sideNextButton.disabled, false);
    assert.strictEqual(setSidePanel("quote_company", {notify: true}), false);
    assert.strictEqual(state.pricingReferenceId, "reference-a");
    assert.strictEqual(state.pricingReferenceSelectionIntent.id, "reference-b");
  });
  await goToNextSidePanel();
  control("Next acknowledged before rail unlock and payload identity", () => {
    assert.strictEqual(state.activeSidePanel, "quote_company");
    assert.strictEqual(rail.find(n => n.dataset.sidePanel === "quote_company").disabled, false);
    assert.strictEqual(savedDraft.customerStepCheckpoint.pricingReferenceId, "reference-b");
    assert.strictEqual(savedDraft.customerStepCheckpoint.pricingReferenceDigest, `sha256:${"b".repeat(64)}`);
    assert.strictEqual(savedDraft.activeSidePanel, "quote_company");
    assert.strictEqual(buildPayload().pricing_reference_id, "reference-b");
    assert.strictEqual(buildPayload().pricing_reference_source, "company");
    assert.strictEqual(buildPayload().pricing_reference.digest_sha256, `sha256:${"b".repeat(64)}`);
    assert.strictEqual(buildPayload().quote_session.draft_state.customerStepCheckpoint.pricingReferenceDigest, `sha256:${"b".repeat(64)}`);
    assert.deepStrictEqual(buildSessionSnapshot().customerStepCheckpoint, committedCheckpoint());
    assert.deepStrictEqual(currentQuoteSessionDraftState().customerStepCheckpoint, committedCheckpoint());
    assert.strictEqual(buildPayload().client.name, "Synthetic Customer");
  });
  const checkpoint = committedCheckpoint();
  control("valid refresh and Modify checkpoint", () => {
    assert.strictEqual(restoredQuoteSessionSidePanel({activeSidePanel: "quote_company"}), "quote_company");
    assert.strictEqual(restoredQuoteSessionSidePanel({activeSidePanel: "customer", outputRows: [{}]}, {restoreFurthestPanel: true}), "output");
    state.customerStepCheckpoint = Object.fromEntries(Object.entries(checkpoint).reverse());
    assert.strictEqual(customerStepCheckpointIsCurrent(), true, "JSON key order is not authority");
  });
  for (const [name, change] of [
    ["missing", () => {state.customerStepCheckpoint = null;}],
    ["malformed", () => {state.customerStepCheckpoint = {version: 1};}],
    ["wrong session", () => {state.quoteSessionId = "quote-replacement";}],
    ["wrong recovery scope", () => {scope = "other-scope";}],
    ["stale digest", () => {state.pricingReferences[1].digest_sha256 = `sha256:${"d".repeat(64)}`;}],
    ["changed Customer", () => {elements.clientName.value = "Changed Synthetic Customer";}],
    ["commercial review", () => {state.quoteCommercialReview = {status: QUOTE_COMMERCIAL_REVIEW_STATUS};}],
  ]) {
    fresh(); state.pricingReferenceId = "reference-b"; state.customerStepCheckpoint = clone(checkpoint); change();
    control(`${name} checkpoint cannot restore or rail-forward`, () => {
      state.outputRows = [{}];
      assert.strictEqual(setSidePanel("quote_company"), false);
      assert.strictEqual(restoredQuoteSessionSidePanel({activeSidePanel: "output"}), "customer");
    });
  }
  for (const mode of ["failure", "wrong_session", "bad_status"]) {
    fresh(); selectB(); saveMode = mode; await goToNextSidePanel();
    control(`${mode} acknowledgement keeps Customer locked`, () => {
      assert.strictEqual(state.activeSidePanel, "customer");
      assert.strictEqual(customerStepCheckpointIsCurrent(), false);
      assert.strictEqual(state.customerStepCheckpoint, null);
      assert.strictEqual(elements.clientName.value, "Synthetic Customer");
    });
  }
  fresh(); selectB(); saveMode = "deferred";
  const advancing = goToNextSidePanel();
  assert.ok(releaseSave);
  control("pending acknowledgement does not unlock", () => {
    assert.strictEqual(state.activeSidePanel, "customer");
    assert.strictEqual(setSidePanel("quote_company"), false);
  });
  elements.clientName.value = "New Synthetic Input"; releaseSave(); await advancing;
  control("stale acknowledgement cannot unlock replacement inputs", () => {
    assert.strictEqual(state.activeSidePanel, "customer");
    assert.strictEqual(state.customerStepCheckpoint, null);
  });
  fresh(); await goToNextSidePanel(); setSidePanel("customer");
  const previous = committedCheckpoint();
  state.lineItems = [{description: "Synthetic existing basis"}]; state.outputRows = [{description: "Synthetic row"}];
  state.downloadFile = {url: "/synthetic.xlsx"}; selectB(); await goToNextSidePanel();
  control("existing results require confirmation", () => {
    assert.strictEqual(state.activeSidePanel, "customer");
    assert.deepStrictEqual(state.pendingQuoteDependencyChanges, ["pricing_reference"]);
    assert.strictEqual(state.pricingReferenceId, "reference-a"); assert.strictEqual(state.outputRows.length, 1);
  });
  hideQuoteDependencyConfirmModal();
  control("cancel preserves committed checkpoint and results", () => {
    assert.deepStrictEqual(state.customerStepCheckpoint, previous);
    assert.strictEqual(state.pricingReferenceId, "reference-a"); assert.strictEqual(state.outputRows.length, 1);
    assert.strictEqual(setSidePanel("quote_company"), false, "pending uncommitted intent is not the checkpoint");
  });
  goToPreviousSidePanel();
  control("backward move discards only uncommitted selection", () => {
    assert.strictEqual(state.pricingReferenceSelectionIntent, null);
    assert.strictEqual(elements.profileSelect.value, "company::reference-a");
    assert.deepStrictEqual(state.customerStepCheckpoint, previous); assert.strictEqual(state.outputRows.length, 1);
  });
  setSidePanel("customer"); selectB(); await goToNextSidePanel(); await confirmQuoteDependencyChange();
  control("accept commits saves invalidates stale exports without analysis", () => {
    assert.strictEqual(state.activeSidePanel, "quote_company"); assert.strictEqual(state.pricingReferenceId, "reference-b");
    assert.strictEqual(state.lineItems.length, 0); assert.strictEqual(state.outputRows.length, 0);
    assert.strictEqual(state.downloadFile, null); assert.strictEqual(customerStepCheckpointIsCurrent(), true);
  });
  setSidePanel("customer"); elements.profileSelect.value = "company::reference-b"; handleProfileSelectionChange();
  const before = postCount; await goToNextSidePanel();
  control("unchanged reference no dependency confirmation", () => {
    assert.strictEqual(elements.quoteDependencyConfirmModal.hidden, true);
    assert.strictEqual(customerStepCheckpointIsCurrent(), true); assert.ok(postCount >= before);
  });
  setSidePanel("customer"); selectB();
  control("same panel preserves selection intent", () => {
    const intent = clone(state.pricingReferenceSelectionIntent); setSidePanel("customer");
    assert.deepStrictEqual(state.pricingReferenceSelectionIntent, intent);
  });
  console.log(JSON.stringify({controls, synthetic_session_save_requests: postCount, provider_requests: 0}));
})().catch(error => {console.error(error); process.exitCode = 1;});
