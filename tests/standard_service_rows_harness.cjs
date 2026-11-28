// Runs the production Quote Basis code for the standard service rows (#363) in a VM and prints what it did.
// It starts from the page's own state, constants and functions. Only screen effects (rendering, chat authority,
// status text) are replaced with no-ops, and the DOM element table is a stand-in that ignores every update.
const fs = require("node:fs"), vm = require("node:vm");
const source = fs.readFileSync("webapp/static/app.js", "utf8");
const inputText = fs.readFileSync(0, "utf8");
const funcs = [...source.matchAll(/^(?:async )?function ([A-Za-z0-9_]+)\([^]*?^}/gm)].map((m) => m[0]);
// Every top-level constant and counter of the page, in source order, except the state object (built below)
// and the DOM element table (a stand-in that ignores every screen update).
const declarations = [...source.matchAll(/^(?:const|let) ([A-Za-z0-9_]+)[^]*?(?=^(?:async function|function|const|let|class|boot\(\))\b)/gm)]
  .filter((m) => m[1] !== "state" && m[1] !== "elements").map((m) => m[0]);
// Top-level classes come first: some constants are instances of them (the page's owner objects).
const classes = [...source.matchAll(/^class [A-Za-z0-9_]+ [^]*?^}/gm)].map((m) => m[0]);
const constants = [...classes, ...declarations].join("\n");
// Parsed inside the VM so the drafts are plain objects of its own realm, as in the page.
const ignored = new Proxy(function () {}, {
  get: (target, key) => (key === Symbol.toPrimitive ? () => "" : key === "length" ? 0 : ignored),
  apply: () => ignored, set: () => true,
});
// A failed step rejects the run, which exits non-zero with the error.
const finish = (value) => console.log(JSON.stringify(value));
const ctx = { inputText, TextEncoder, console, finish, elements: new Proxy({}, { get: () => ignored }), document: ignored, window: {},
  quoteTemplatesSettings: ignored };
vm.createContext(ctx);
vm.runInContext(constants + "\n" + funcs.join("\n"), ctx);
const pageState = source.match(/^const state = (\{[^]*?^\});/m)[1];
vm.runInContext(`
const input = JSON.parse(inputText);
// The page's own starting state; a reopened older draft passes decisions: null.
var state = ${pageState};
if (input.decisions !== undefined) state.standardServiceRows = input.decisions;
// A selected pricing reference, as the page always has once an analysis has run.
// Its catalogue holds every row the steps give a catalog_price, at that price.
const catalogItem = (row) => ({
  id: row.pricing_keyword, pricing_keyword: row.pricing_keyword, section: row.section, description: row.description,
  unit: row.unit, sale_unit_price: row.catalog_price,
});
const catalogue = [...new Map(input.steps.flatMap((step) => [...(step.adopt?.line_items || []), ...(step.reopen?.lineItems || [])])
  .filter((row) => row.catalog_price !== undefined).map((row) => [row.pricing_keyword, catalogItem(row)])).values()];
const reference = { id: "standard-rows-reference", source: "local", currency: "SGD", digest_sha256: "sha256:" + "a".repeat(64), items: catalogue };
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
  // What the priced rows add up to (price x quantity), so a dropped priced row shows as a lower total.
  priced_total: state.lineItems.reduce((sum, item) => sum + (Number(item.pricing_authority?.price) || 0) * (Number(item.quantity) || 0), 0),
  basis_agrees: JSON.stringify(state.quoteBasis) === JSON.stringify({ ...state.quoteBasis, ...quoteBasisFromSections(state.quoteBasisSections) }),
  decisions: state.standardServiceRows,
  saved: standardServiceDecisionsForSave(),
});
// A row given a catalog_price carries a catalogue pricing authority, as a priced analysis row does.
const priced = ({ catalog_price: price, ...item }) => (price === undefined ? item : {
  ...item,
  pricing_authority: buildPricingAuthority("catalog", normalizeLineItem(item), { price, catalogItem: catalogItem({ ...item, catalog_price: price }) }),
});
// An analysis result is adopted the way the app adopts one: basis, rows, standard rows, then the Reset snapshot.
const adopt = (result) => {
  const draft = { quote_basis: {}, ...result, line_items: (result.line_items || []).map(priced) };
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
// A saved draft reopened through the page's own restore, as the saved quotes list and a reload do.
const reopen = async (saved) => {
  const restored = await applyQuoteSessionSnapshot({
    version: QUOTE_SESSION_STATE_VERSION, browserRecoveryScope: currentBrowserRecoveryScope(), images: [], quoteBasis: {},
    outputRows: [], originalOutputRows: [], analysisFindings: [], blockingClarificationQuestions: [], pricingMatches: [],
    basisChat: {}, ...saved, lineItems: (saved.lineItems || []).map(priced),
  }, { forceQuoteView: true });
  if (restored !== true) throw new Error("the saved draft was not restored");
};
(async () => {
const steps = [];
for (const step of input.steps) {
  if (step.reopen) await reopen(step.reopen);
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
return { steps, rows: STANDARD_SERVICE_ROWS };
})().then(finish);
`, ctx);
