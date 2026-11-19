// Runs the production Quote Basis code for the standard service rows (#363) in a VM and prints what it did.
// Only screen effects (rendering, chat authority, status text) are replaced with no-ops.
const fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("webapp/static/app.js", "utf8");
const inputText = fs.readFileSync(0, "utf8");
const funcs = [...source.matchAll(/^(?:async )?function ([A-Za-z0-9_]+)\([^]*?^}/gm)].map((m) => m[0]);
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const constants = [
  source.slice(0, source.indexOf("const state = {")),
  slice("const QUOTE_BASIS_LEGACY_ORDER", "function canonicalQuoteBasis("),
  slice("const AUTHORITATIVE_BASIS_LIMITS", "function authoritativeBasisKeyClass"),
  "const AUTHORITATIVE_BASIS_KEY_SAFETY=new Map();",
  "let basisPricingIndex = null;",
].join("\n");
// Parsed inside the VM so the drafts are plain objects of its own realm, as in the page.
const ctx = { inputText, TextEncoder, console, result: null };
vm.createContext(ctx);
vm.runInContext(constants + "\n" + funcs.join("\n"), ctx);
vm.runInContext(`
const input = JSON.parse(inputText);
var state = {
  quoteBasisSections: [], quoteBasis: {}, lineItems: [], outputRows: [], originalOutputRows: [], outputErrors: [],
  boothDimensions: {}, analysisFindings: [], blockingClarificationQuestions: [], basisConfirmed: false,
  originalAnalysisSnapshot: null, standardServiceRows: input.decisions === undefined ? newStandardServiceDecisions() : input.decisions,
};
// A selected pricing reference, as the page always has once an analysis has run.
const reference = { id: "standard-rows-reference", source: "local", currency: "SGD", digest_sha256: "sha256:" + "a".repeat(64), items: [] };
currentPricingReference = () => reference;
appIsBusy = () => false;
collectQuoteCurrency = () => "SGD";
commitActiveOutputEditor = () => "committed";
referenceFilesDependencySignature = () => "";
[
  "invalidateBasisChatAuthority", "setDownloadFiles", "clearPricingReviewMessages", "setWorkflowStage",
  "updateQuoteBasisCard", "syncControlStates", "markOutputRowsDirty", "updateBasisLines", "queueQuoteSessionDraftStateSave",
  "renderMatchSummary", "renderPricingMatches", "setResultStatus", "showBlockedAction",
].forEach((name) => { globalThis[name] = () => {}; });
const view = () => ({
  sections: state.quoteBasisSections.map((section) => ({
    id: section.id, title: section.title,
    lines: section.lines.map((line) => ({ id: line.id, tag: line.tag, text: line.text, quantity: line.quantity, unit: line.unit })),
  })),
  line_items: state.lineItems.map((item) => ({
    source_basis_line_id: item.source_basis_line_id, description: item.description, quantity: item.quantity, unit: item.unit,
    price_mode: item.price_mode, variant: item.pricing_authority?.variant || null, price: item.pricing_authority?.price ?? null,
  })),
  basis_agrees: JSON.stringify(state.quoteBasis) === JSON.stringify({ ...state.quoteBasis, ...quoteBasisFromSections(state.quoteBasisSections) }),
  decisions: state.standardServiceRows,
  saved: standardServiceDecisionsForSave(),
});
// An analysis result is adopted the way the app adopts one: basis, rows, standard rows, then the Reset snapshot.
const adopt = (result) => {
  const draft = { quote_basis: {}, ...result };
  applyDraftBasis(draft);
  applyDraftLineItems(draft.line_items || []);
  applyStandardServiceRows();
  captureOriginalAnalysisSnapshot(draft);
};
// The tick and X controls call applyBasisTag with the row's section and index.
const setTag = (id, tag) => {
  const section = state.quoteBasisSections.find((item) => item.lines.some((line) => line.id === id));
  applyBasisTag(section.id, tag, section.lines.findIndex((line) => line.id === id));
};
const steps = [];
for (const step of input.steps) {
  if (step.adopt) adopt(step.adopt);
  if (step.tag) setTag(step.tag.id, step.tag.tag);
  if (step.reset) resetQuoteBasisToOriginal();
  // Starting an analysis (stopped here before any request: no images), then the basis cleared as a
  // failed analysis or a clarification clears it.
  if (step.analyseThenClear) {
    state.images = [];
    state.pendingFeedback = "";
    handleDraftBasis({});
    clearAiFailedDraftState();
  }
  if (step.restore !== undefined) state.standardServiceRows = normalizeStandardServiceDecisions(step.restore);
  steps.push(view());
}
result = { steps, rows: STANDARD_SERVICE_ROWS };
`, ctx);
console.log(JSON.stringify(ctx.result));
