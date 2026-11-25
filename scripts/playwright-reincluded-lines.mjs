// A line X'd before Confirm and ticked back in on a completed quote: the quote keeps saving, the tick and
// what staff typed after it survive Dashboard, leaving, tab close and another browser, nothing unconfirmed
// is exported, and Confirm alone restores the catalogue price or says why it cannot.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";
import fs from "node:fs/promises";

const run = new SyntheticQuoteRun("reincluded-lines");
const lines = [
  { id: "re-carpet", section: "Synthetic Floors", keyword: "synthetic-floors-synthetic-carpet-tile", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", price: 14.4 },
  { id: "re-vinyl", section: "Synthetic Floors", keyword: "synthetic-floors-synthetic-vinyl-floor-finish", text: "sqm synthetic vinyl floor finish", quantity: 8, unit: "sqm", price: 10.4 },
  { id: "re-graphic", section: "Synthetic Graphics", keyword: "synthetic-graphics-synthetic-printed-wall-graphic", text: "sqm synthetic printed wall graphic", quantity: 12, unit: "sqm", price: 21 },
  { id: "re-banner", section: "Synthetic Graphics", keyword: "synthetic-graphics-synthetic-hanging-banner", text: "sqm synthetic hanging banner", quantity: 6, unit: "sqm", price: 21.6 },
];
const titles = [...new Set(lines.map((line) => line.section))];
const scenario = { raw: {
  analysis_findings: [], blocking_clarification_questions: [], project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: titles.map((title) => ({ id: title.toLowerCase().replace(/ /g, "-"), title, lines: lines.filter((line) => line.section === title)
    .map(({ id, text, quantity, unit, keyword }) => ({ id, tag: "Include", text, quantity, unit, confidence_pct: 95, pricing_keyword: keyword })) })),
  line_items: lines.map(({ id, section, text, quantity, unit, keyword }) => ({ source_basis_line_id: id, section, description: text, quantity, unit, pricing_keyword: keyword })),
} };
const excluded = ["re-vinyl", "re-graphic", "re-banner"];
const tagsOf = (page) => page.evaluate((ids) => Object.fromEntries(state.quoteBasisSections.flatMap((section) => section.lines || [])
  .filter((line) => ids.includes(line.id)).map((line) => [line.id, normalizeBasisTag(line.tag)])), lines.map((line) => line.id));
const savedTags = (record) => Object.fromEntries(record.draft_state.quoteBasisSections.flatMap((section) => section.lines)
  .filter((line) => lines.some((entry) => entry.id === line.id)).map((line) => [line.id, line.tag]));
const outputRow = (page, id) => page.evaluate((rowId) => {
  const row = state.outputRows.find((entry) => entry.source_basis_line_id === rowId);
  return row ? { variant: row.pricing_authority?.variant, price: effectiveOutputUnitPrice(row), quantity: Number(row.quantity) } : null;
}, id);
const toDashboard = async (page) => {
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 20000 });
};
const confirmToOutput = async (page) => {
  await run.confirm(page);
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSavePromise, null, { timeout: 60000 });
};
const onBasis = async (sessionId) => {
  const page = await run.signedInPage();
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "basis");
  return page;
};
const dialog = (page) => page.evaluate(() => {
  const modal = document.querySelector("#excelGeneratingModal");
  return modal && !modal.hidden && modal.offsetParent !== null || modal?.classList.contains("is-open")
    ? { title: document.querySelector("#excelGeneratingTitle").innerText, message: document.querySelector("#excelGeneratingMessage").innerText } : null;
});

const result = {};
try {
  await run.start(scenario);
  let page = await run.signedInPage();
  await run.buildToBasis(page);
  await run.tickOpenLines(page);
  // Before any Confirm too: X'd, left to the Dashboard (saved as excluded), then ticked back in.
  await run.tag(page, "re-carpet", "Exclude");
  await toDashboard(page);
  await run.openDashboardQuote(page, await page.evaluate(() => state.quoteSessionId));
  await run.openPanel(page, "basis");
  await run.tag(page, "re-carpet", "Include");
  for (const id of excluded) await run.tag(page, id, "Exclude");
  await confirmToOutput(page);
  const sessionId = await page.evaluate(() => state.quoteSessionId);
  assert((await outputRow(page, "re-carpet"))?.price === 14.4, "a line ticked back in before the first Confirm lost its price", await outputRow(page, "re-carpet"));
  assert(!(await outputRow(page, "re-graphic")), "the excluded lines are not left out of Output");
  await toDashboard(page);

  // 1. Tick one line back in and go to the Dashboard.
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "basis");
  const jobs = [];
  page.on("request", (request) => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/jobs") jobs.push(request.postDataJSON()?.type); });
  await run.tag(page, "re-graphic", "Include");
  await toDashboard(page).catch(async () => {
    throw new Error("the Dashboard did nothing after a line was ticked back in: " + JSON.stringify({ dialog: await dialog(page), view: await page.evaluate(() => state.activeAppView) }));
  });
  let record = await run.record(sessionId);
  // What the mark protects: the saved draft never holds an unconfirmed line as included, or its price.
  assert(savedTags(record)["re-graphic"] === "Exclude", "an unconfirmed line was saved as included", savedTags(record));
  assert(record.pricing_ledger.rows["re-graphic"].grant.state === "superseded", "an unconfirmed line got its price back", record.pricing_ledger.rows["re-graphic"]);
  result.dashboard = "PASS";
  // Nothing unconfirmed is exported: Download from the older Output goes back to Quote Basis, with the tick kept.
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "output");
  await page.locator("#sideDownloadButton").click();
  await page.waitForFunction(() => state.activeSidePanel === "basis" && !appIsBusy(), null, { timeout: 20000 });
  assert(!jobs.length, "an unconfirmed line reached generation", jobs);
  assert(await page.evaluate(() => state.lineItems.some((row) => row.source_basis_line_id === "re-graphic" && row.reincluded_catalog_selection)), "Output dropped the line waiting for Confirm");
  await toDashboard(page);
  result.exportHeld = "PASS";

  // A duplicate carries no saved price to restore, so its line stays excluded and the copy saves as usual.
  await page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`).click();
  await page.locator('#dashboardSelectedSessionPanel [data-dashboard-panel-action="duplicate-session"]').click();
  await page.waitForFunction((id) => !appIsBusy() && state.dashboardActiveSessionId && state.dashboardActiveSessionId !== id, sessionId, { timeout: 30000 });
  const copy = (await run.record(await page.evaluate(() => state.dashboardActiveSessionId))).draft_state;
  assert(copy.quoteBasisSections.flatMap((section) => section.lines).find((line) => line.id === "re-graphic").tag === "Exclude"
    && !copy.lineItems.some((row) => row.reincluded_catalog_selection), "the duplicate kept a tick it cannot price");

  // A newer save elsewhere, first met by a background autosave: every Next says so instead of doing nothing.
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "customer");
  const other = await onBasis(sessionId);
  await run.openPanel(other, "quote_company");
  await other.locator("#companyTitleEditor").fill("Synthetic Title Changed");
  await toDashboard(other);
  await other.context().close();
  await page.locator("#showName").fill("Synthetic Show Changed");
  await page.waitForFunction(() => state.pricingDraftVersion === false, null, { timeout: 15000 });
  result.staleNext = [];
  for (let attempt = 0; attempt < 2; attempt++) {
    await page.locator("#sideNextButton").click();
    await page.waitForFunction(() => !appIsBusy(), null, { timeout: 20000 });
    result.staleNext.push((await dialog(page))?.message || "");
    assert(result.staleNext[attempt].includes("changed in another tab or session"), "Next did nothing after a newer save elsewhere", result.staleNext);
    await page.locator("#excelGeneratingCloseButton").click();
  }

  // 2. Another browser shows the tick; a block tick there, a typed quantity and a step change are kept too.
  await page.context().close();
  page = await onBasis(sessionId);
  assert((await tagsOf(page))["re-graphic"] === "Include" && (await tagsOf(page))["re-vinyl"] === "Exclude", "another browser lost the tick", await tagsOf(page));
  await page.locator('#basisReviewSurface [data-basis-section="synthetic-floors"][data-basis-section-group="Matched"][data-basis-section-action="Include"]').click();
  assert((await tagsOf(page))["re-vinyl"] === "Include", "the block tick did not reach the excluded line", await tagsOf(page));
  await (await run.basisControl(page, "re-graphic", "[data-basis-quantity-edit]")).click();
  const quantity = page.locator("#basisReviewSurface .basis-quantity-text input").first();
  await quantity.fill("14");
  await quantity.press("Enter");
  await run.openPanel(page, "customer");
  await page.waitForFunction(() => !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 15000 });
  const savedGraphic = (await run.record(sessionId)).draft_state.lineItems.find((row) => row.source_basis_line_id === "re-graphic");
  assert(Number(savedGraphic?.quantity) === 14 && savedGraphic.reincluded_catalog_selection, "leaving the step did not save the typed quantity", savedGraphic);
  result.leaving = "PASS";

  // 3. Tab closed right after one more tick, without leaving the quote: same browser and another browser.
  await run.openPanel(page, "basis");
  await run.tag(page, "re-banner", "Include");
  await page.waitForFunction(() => !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 15000 });
  const context = page.context();
  await page.close();
  const expected = { "re-carpet": "Include", "re-vinyl": "Include", "re-graphic": "Include", "re-banner": "Include" };
  const sameBrowser = await context.newPage();
  await sameBrowser.goto(run.server.baseUrl, { waitUntil: "domcontentloaded" });
  await sameBrowser.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.quoteBasisSections.length > 0 && !appIsBusy(), null, { timeout: 30000 });
  assert(JSON.stringify(await tagsOf(sameBrowser)) === JSON.stringify(expected), "reopening in the same browser lost a tick", await tagsOf(sameBrowser));
  await context.close();
  page = await onBasis(sessionId);
  assert(JSON.stringify(await tagsOf(page)) === JSON.stringify(expected), "reopening in another browser lost a tick", await tagsOf(page));
  result.tabClose = "PASS";

  // 4. Confirm restores the catalogue prices at the typed quantity.
  await confirmToOutput(page);
  result.confirmed = {};
  for (const line of lines) {
    const row = await outputRow(page, line.id);
    result.confirmed[line.id] = row;
    assert(row?.variant === "catalog" && row.price === line.price && row.quantity === (line.id === "re-graphic" ? 14 : line.quantity), "Confirm did not restore the line", { id: line.id, row });
  }
  assert(await page.evaluate(() => outputRowsValid().valid), "the confirmed quote cannot be exported", await page.evaluate(() => outputRowsValid().errors));

  // 5. A refused line: X it, Confirm, and lose its saved price evidence on the server; then tick it again.
  await run.openPanel(page, "basis");
  await run.tag(page, "re-graphic", "Exclude");
  await run.tag(page, "re-banner", "Exclude");
  await confirmToOutput(page);
  await toDashboard(page);
  await page.context().close();
  record = await run.record(sessionId);
  record.pricing_ledger.rows["re-graphic"].grant.catalog_digest = "sha256:" + "0".repeat(64);
  await fs.writeFile(run.recordPath(sessionId), JSON.stringify(record, null, 2));
  await run.restartServer();
  page = await onBasis(sessionId);
  // A line Output still holds: X'd, saved, then ticked back in.
  await run.tag(page, "re-carpet", "Exclude");
  await toDashboard(page);
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "basis");
  for (const id of ["re-carpet", "re-graphic", "re-banner"]) await run.tag(page, id, "Include");
  const answer = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/quote-sessions/pricing-selections");
  await run.confirm(page);
  const refused = await answer;
  assert(refused.status() === 409, "the line without saved evidence was not refused", refused.status());
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 30000 });
  const banner = page.locator("#aiFailureBanner");
  assert(await banner.isVisible(), "the refusal is not visible on Quote Basis");
  result.refusal = (await banner.innerText()).replace(/\s+/g, " ").trim();
  assert(result.refusal.includes("synthetic printed wall graphic") && !result.refusal.includes("hanging banner"), "the refusal does not name its line", result.refusal);
  assert((await tagsOf(page))["re-graphic"] === "Include", "the refused line lost its tick");
  await toDashboard(page);
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "basis");
  await confirmToOutput(page);
  const refusedRow = await outputRow(page, "re-graphic");
  assert(refusedRow && refusedRow.variant !== "catalog" && refusedRow.price === null, "a refused line came back with a price", refusedRow);
  assert((await outputRow(page, "re-banner"))?.price === 21.6, "the other line was not restored", await outputRow(page, "re-banner"));
  assert((await outputRow(page, "re-carpet"))?.price === 14.4, "a line Output still held lost its price", await outputRow(page, "re-carpet"));
  assert(!(await page.evaluate(() => outputRowsValid().valid)) && await page.locator("#sideDownloadButton").getAttribute("aria-disabled") === "true", "a line without a price can be exported");
  result.refusedRowBlocksExport = true;

  // 6. A save the server does not take is told where staff are, and the quote stays open.
  await run.openPanel(page, "basis");
  await run.tag(page, "re-banner", "Exclude");
  await page.route("**/api/quote-sessions", (route) => (route.request().method() === "POST"
    ? route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ status: "failed", error_reference: "ERR-0A1B2C3D" }) }) : route.continue()));
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 20000 });
  result.unsavedDialog = await dialog(page);
  assert(result.unsavedDialog?.title === "Quote not saved" && result.unsavedDialog.message.includes("could not be saved"), "a failed save on leaving was silent", result.unsavedDialog);
  await page.locator("#excelGeneratingCloseButton").click();
  run.finish(result);
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
