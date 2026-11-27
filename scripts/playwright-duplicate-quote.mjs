// Duplicate Quote lands the copy where its source ended (#445). A finished quote's copy opens at Output with the
// same customer, company, Quote Basis decisions and prices, priced afresh by the server for the copy, with no Excel
// or PDF of its own until staff press Generate; the source keeps its own files. A quote that stopped at Quote Basis
// gives a copy at Quote Basis, and Modify still opens a finished quote at Output. Synthetic data, no paid AI.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";
import path from "node:path";

const run = new SyntheticQuoteRun("duplicate-quote");
const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
const lines = [
  { id: "carpet", section: "Synthetic Floors", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm" },
  { id: "spotlight", section: "Synthetic Lighting And AV", text: "nos. synthetic spotlight 6 inch", quantity: 8, unit: "nos" },
  { id: "chair", section: "Synthetic Rentals", text: "nos. synthetic cafe chair", quantity: 4, unit: "nos" },
].map((line) => ({ ...line, keyword: slug(line.section) + "-" + slug(line.text.replace(/^(?:sqm|nos\.) /, "")) }));
const scenario = { raw: {
  analysis_findings: [], blocking_clarification_questions: [], project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: [...new Set(lines.map((line) => line.section))].map((title) => ({ id: slug(title), title,
    lines: lines.filter((line) => line.section === title).map(({ id, text, quantity, unit, keyword }) => (
      { id, tag: "Include", text, quantity, unit, confidence_pct: 95, pricing_keyword: keyword })) })),
  line_items: lines.map(({ id, section, text, quantity, unit, keyword }) => (
    { source_basis_line_id: id, section, description: text, quantity, unit, pricing_keyword: keyword })),
} };
const result = {};

const idle = (page) => page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 60000 });
const toDashboard = async (page) => {
  await idle(page);
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 30000 });
};
// What staff see of a quote: the step it opens at, its customer and company, its Quote Basis decisions and its Output.
const quoteOnScreen = (page) => page.evaluate(() => {
  syncRichTextSources();
  return {
    step: state.activeSidePanel,
    customer: elements.clientName.value,
    project: elements.projectTitle.value,
    company: elements.quoteCompanyName.value,
    tags: Object.fromEntries(state.quoteBasisSections.flatMap((section) => section.lines || [])
      .map((line) => [line.id, [normalizeBasisTag(line.tag), Number(line.quantity)]])),
    output: Object.fromEntries(state.outputRows.map((row) => [row.source_basis_line_id || row.description,
      [row.price_mode, row.pricing_authority?.variant || "", effectiveOutputUnitPrice(row), Number(row.quantity)]])),
    files: [state.downloadFile?.url || "", state.pdfFile?.url || ""],
    outputErrors: state.outputErrors.length,
  };
});
const duplicate = async (page, sourceId) => {
  await toDashboard(page);
  const card = page.locator(`#dashboardSessionsList [data-quote-session-id="${sourceId}"]`);
  await card.waitFor({ state: "visible", timeout: 20000 });
  await card.click();
  await page.locator(`#dashboardSelectedSessionPanel [data-dashboard-panel-action="duplicate-session"]`).click();
  await page.waitForFunction((id) => !appIsBusy() && !state.quoteSessionRestoreBusy && state.activeAppView === "dashboard"
    && state.dashboardActiveSessionId && state.dashboardActiveSessionId !== id, sourceId, { timeout: 90000 });
  return page.evaluate(() => state.dashboardActiveSessionId);
};
const savedFiles = async (sessionId) => {
  const record = await run.record(sessionId);
  return Object.fromEntries(Object.entries(record.exports || {}).map(([kind, file]) => [kind, Boolean(file?.filename)]));
};

try {
  await run.start(scenario);
  const page = await run.signedInPage();

  // A finished quote: Quote Basis decided (one line X'd), Output confirmed, one row deleted in Output, an Excel generated.
  await run.buildToBasis(page);
  await run.tickOpenLines(page);
  await run.tag(page, "chair", "Exclude");
  await run.confirm(page);
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await idle(page);
  const deletedIndex = await page.evaluate(() => state.outputRows.findIndex((row) => row.source_basis_line_id === "spotlight"));
  assert(deletedIndex >= 0, "the source has no spotlight row to delete");
  await page.locator(`[data-output-delete-row="${deletedIndex}"]`).click();
  await page.locator("#confirmOutputDeleteButton").click();
  await idle(page);
  const sourceId = await page.evaluate(() => state.quoteSessionId);
  const download = page.waitForEvent("download", { timeout: 90000 });
  await page.locator("#sideDownloadButton").click();
  await (await download).saveAs(path.join(run.runRoot, "source.xlsx"));
  await idle(page);
  const source = await quoteOnScreen(page);
  assert(source.step === "output" && source.files[0] && !source.outputErrors, "the source quote did not finish", source);
  assert(Object.values(source.output).some(([mode, variant, price]) => mode === "Priced" && variant === "catalog" && price > 0)
    && !("spotlight" in source.output), "the source has no catalogue price or kept the deleted row", source.output);
  const sourceDraft = (await run.record(sourceId)).draft_state;
  const sourceRecord = JSON.stringify(sourceDraft.outputRows);
  const rowIds = (rows) => (rows || []).map((row) => row.source_basis_line_id);
  const sourceFiles = await savedFiles(sourceId);
  assert(sourceFiles.xlsx, "the source has no saved Excel", sourceFiles);

  // Duplicate, then open the copy: Output, same quote, priced, no files of its own.
  const copyId = await duplicate(page, sourceId);
  await run.openDashboardQuote(page, copyId);
  const copy = await quoteOnScreen(page);
  const same = (key) => JSON.stringify(copy[key]) === JSON.stringify(source[key]);
  assert(copy.step === "output", "the copy of a finished quote did not open at Output", { step: copy.step });
  for (const key of ["customer", "project", "company", "tags", "output"]) assert(same(key), "the copy differs from its source: " + key, { copy: copy[key], source: source[key] });
  assert(!copy.files[0] && !copy.files[1] && !copy.outputErrors, "the copy carries the source's files or asks for prices", copy);
  const copyRecord = await run.record(copyId);
  assert(copyRecord.draft_state.activeSidePanel === "output" && !Object.values(await savedFiles(copyId)).some(Boolean),
    "the saved copy is not at Output or holds files", { step: copyRecord.draft_state.activeSidePanel, files: await savedFiles(copyId) });
  result.finished = { source: sourceId, copy: copyId, step: copy.step, output: copy.output };

  // Generate works on the copy, and the source keeps its own rows and files.
  const copyDownload = page.waitForEvent("download", { timeout: 90000 });
  await page.locator("#sideDownloadButton").click();
  await (await copyDownload).saveAs(path.join(run.runRoot, "copy.xlsx"));
  await idle(page);
  assert((await savedFiles(copyId)).xlsx, "Generate on the copy saved no Excel", await savedFiles(copyId));
  assert(JSON.stringify((await run.record(sourceId)).draft_state.outputRows) === sourceRecord
    && JSON.stringify(await savedFiles(sourceId)) === JSON.stringify(sourceFiles), "duplicating or generating the copy changed the source");

  // Modify of the finished source still opens at Output.
  await toDashboard(page);
  await run.openDashboardQuote(page, sourceId);
  const reopened = await quoteOnScreen(page);
  assert(reopened.step === "output" && JSON.stringify(reopened.output) === JSON.stringify(source.output), "Modify did not open the source at Output", reopened);

  // If the copy cannot be priced (the pricing request fails), it keeps its source's Output and Reset rows, the row deleted in
  // Output stays deleted, and it opens at Output with its catalogue rows asking for a price; nothing is rebuilt from the basis.
  const refusePricing = (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ status: "failed", errors: ["Synthetic failure."] }) });
  await page.route("**/api/quote-sessions/pricing-selections", refusePricing);
  const unpricedId = await duplicate(page, sourceId);
  await page.unroute("**/api/quote-sessions/pricing-selections", refusePricing);
  await run.openDashboardQuote(page, unpricedId);
  const unpriced = await quoteOnScreen(page);
  const unpricedDraft = (await run.record(unpricedId)).draft_state;
  const kept = {
    step: unpriced.step, confirmed: unpricedDraft.basisConfirmed,
    saved: rowIds(unpricedDraft.outputRows), reset: rowIds(unpricedDraft.originalOutputRows), shown: Object.keys(unpriced.output),
    catalogue: Object.values(unpriced.output).filter(([, variant]) => variant === "catalog").length,
  };
  assert(kept.step === "output" && kept.confirmed === true && JSON.stringify(unpriced.tags) === JSON.stringify(source.tags)
    && JSON.stringify(kept.saved) === JSON.stringify(rowIds(sourceDraft.outputRows))
    && JSON.stringify(kept.reset) === JSON.stringify(rowIds(sourceDraft.originalOutputRows))
    && JSON.stringify(kept.shown) === JSON.stringify(Object.keys(source.output)) && !kept.catalogue,
    "a copy whose pricing failed lost its source's Output, brought back the deleted row or kept a source price", kept);
  result.pricingFailed = { copy: unpricedId, step: unpriced.step };

  // A quote that stopped at Quote Basis gives a copy at Quote Basis with the same decisions.
  await toDashboard(page);
  await run.buildToBasis(page);
  await run.tag(page, "spotlight", "Exclude");
  await idle(page);
  const basisSource = await quoteOnScreen(page);
  const basisSourceId = await page.evaluate(() => state.quoteSessionId);
  const basisCopyId = await duplicate(page, basisSourceId);
  await run.openDashboardQuote(page, basisCopyId);
  const basisCopy = await quoteOnScreen(page);
  assert(basisCopy.step === "basis" && JSON.stringify(basisCopy.tags) === JSON.stringify(basisSource.tags),
    "the copy of a quote at Quote Basis did not open there with the same decisions", { copy: basisCopy, source: basisSource });
  result.basis = { source: basisSourceId, copy: basisCopyId, step: basisCopy.step };
  run.finish(result);
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
