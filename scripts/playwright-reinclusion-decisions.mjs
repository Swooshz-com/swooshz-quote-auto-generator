// The last decision on a line X'd, saved and ticked back in is the one that is saved, reopened, priced and
// exported, whatever the route to it. Each row of the tables below is one route on its own line; a line ends
// as `Include` (priced at Confirm) or `Exclude` (never in Output or the workbook). Synthetic data, no paid AI.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

const run = new SyntheticQuoteRun("reinclusion-decisions");
const catalogue = [
  ["Synthetic Floors", "sqm", ["sqm synthetic carpet tile", "sqm synthetic raised deck panel", "sqm synthetic vinyl floor finish", "sqm synthetic reusable floor cover"]],
  ["Synthetic Graphics", "sqm", ["sqm synthetic printed wall graphic", "sqm synthetic hanging banner"]],
  ["Synthetic Graphics", "nos", ["nos. synthetic printed system panel", "nos. synthetic counter decal"]],
  ["Synthetic Lighting And AV", "nos", ["nos. synthetic spotlight 6 inch", "nos. synthetic spotlight 3 inch", "nos. synthetic socket point"]],
  ["Synthetic Rentals", "nos", ["nos. synthetic cafe chair", "nos. synthetic round table", "nos. synthetic storage cabinet"]],
];
const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
const items = catalogue.flatMap(([section, unit, texts]) => texts.map((text) => ({
  section, unit, text, keyword: slug(section) + "-" + slug(text.replace(/^(?:sqm|nos\.) /, "")),
})));
const held = { carpet: 0, deck: 1, graphic: 4, banner: 5, spotlight6: 8, spotlight3: 9, socket: 10 };
const notHeld = { vinyl: 2, cover: 3, panel: 6, decal: 7, chair: 11, table: 12, cabinet: 13 };
const lines = Object.entries({ ...held, ...notHeld }).sort((a, b) => a[1] - b[1]).map(([id, index]) => ({ id, quantity: 4 + index, ...items[index] }));
const ids = lines.map((line) => line.id);
const sectionTitles = [...new Set(lines.map((line) => line.section))];
const scenario = { basis_chat_tag: "Exclude", raw: {
  analysis_findings: [], blocking_clarification_questions: [], project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: sectionTitles.map((title) => ({ id: slug(title), title, lines: lines.filter((line) => line.section === title)
    .map(({ id, text, quantity, unit, keyword }) => ({ id, tag: "Include", text, quantity, unit, confidence_pct: 95, pricing_keyword: keyword })) })),
  line_items: lines.map(({ id, section, text, quantity, unit, keyword }) => ({ source_basis_line_id: id, section, description: text, quantity, unit, pricing_keyword: keyword })),
} };

// One route per line: what staff press, and the decision they are left with. The same routes run on lines the
// older Output still holds and on lines it does not.
const FLOORS = "synthetic-floors";
const GRAPHICS = "synthetic-graphics";
const ROUTES = [
  { route: "tick", held: "spotlight6", notHeld: "chair", press: ["Include"], ends: "Include" },
  { route: "tick, X", held: "spotlight3", notHeld: "table", press: ["Include", "Exclude"], ends: "Exclude" },
  { route: "tick, X, tick", held: "socket", notHeld: "cabinet", press: ["Include", "Exclude", "Include"], ends: "Include" },
  { route: "block tick", held: "carpet", notHeld: "vinyl", block: [FLOORS, "Include"], ends: "Include" },
  { route: "block tick, X", held: "deck", notHeld: "cover", block: [FLOORS, "Include"], press: ["Exclude"], ends: "Exclude" },
  { route: "tick, block X", held: "graphic", notHeld: "panel", press: ["Include"], after: [GRAPHICS, "Exclude"], ends: "Exclude" },
  { route: "X left as it is", held: "banner", notHeld: "decal", ends: "Exclude" },
];
const endsOf = (routes) => Object.fromEntries(routes.flatMap((entry) => [[entry.held, entry.ends], [entry.notHeld, entry.ends]]));

const tagsOf = (page) => page.evaluate((wanted) => Object.fromEntries(state.quoteBasisSections.flatMap((section) => section.lines || [])
  .filter((line) => wanted.includes(line.id)).map((line) => [line.id, normalizeBasisTag(line.tag)])), ids);
const idle = (page) => page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 20000 });
const toDashboard = async (page) => {
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 20000 });
};
const reopen = async (page, sessionId) => {
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "basis");
};
const onBasis = async (sessionId) => {
  const page = await run.signedInPage();
  await reopen(page, sessionId);
  return page;
};
const reload = async (page) => {
  // A save cut off by F5 may or may not have reached the server, so the page asks for a reopen: settle first.
  await idle(page);
  await page.waitForLoadState("networkidle");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.quoteBasisSections.length > 0 && !appIsBusy(), null, { timeout: 30000 });
  assert(await page.evaluate(() => state.activeSidePanel) === "basis", "F5 left Quote Basis");
};
const blockTag = async (page, [section, tag]) => {
  await page.locator(`#basisReviewSurface [data-basis-section="${section}"][data-basis-section-group="Matched"][data-basis-section-action="${tag}"]`).click();
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 20000 });
};
const confirmToOutput = async (page) => {
  await run.confirm(page);
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await idle(page);
};
const outputRows = (page) => page.evaluate((wanted) => Object.fromEntries(state.outputRows
  .filter((row) => wanted.includes(row.source_basis_line_id))
  .map((row) => [row.source_basis_line_id, { variant: row.pricing_authority?.variant, price: effectiveOutputUnitPrice(row) }])), ids);
const savedQuote = async (sessionId) => {
  const { draft_state: draft, pricing_ledger: ledger } = await run.record(sessionId);
  return {
    tags: Object.fromEntries(draft.quoteBasisSections.flatMap((section) => section.lines).filter((line) => ids.includes(line.id)).map((line) => [line.id, line.tag])),
    waiting: draft.lineItems.filter((row) => row.reincluded_catalog_selection).map((row) => row.source_basis_line_id).sort(),
    live: Object.entries(ledger.rows).filter(([, row]) => row.grant?.state === "live").map(([id]) => id),
  };
};

// Every mismatch is collected, so one run says which routes break and where.
const broken = [];
const expect = (where, actual, wanted) => {
  for (const [id, value] of Object.entries(wanted)) {
    if (actual[id] !== value) broken.push(`${where}: ${id} is ${actual[id]}, not ${value}`);
  }
};
// A draft never holds an unconfirmed line as included or with its price; only a ticked line waits for Confirm.
const expectSaved = async (where, sessionId, ends) => {
  const saved = await savedQuote(sessionId);
  expect(where + " (saved)", saved.tags, Object.fromEntries(Object.keys(ends).map((id) => [id, "Exclude"])));
  const waiting = Object.keys(ends).filter((id) => ends[id] === "Include").sort();
  const held = saved.waiting.filter((id) => id in ends);
  if (JSON.stringify(held) !== JSON.stringify(waiting)) broken.push(`${where} (saved): lines waiting for Confirm are ${held}, not ${waiting}`);
  const priced = saved.live.filter((id) => id in ends);
  if (priced.length) broken.push(`${where} (saved): price kept without Confirm for ${priced}`);
};
const priceList = {};
const expectConfirmed = async (where, page, ends) => {
  const rows = await outputRows(page);
  for (const [id, end] of Object.entries(ends)) {
    const row = rows[id];
    if (end === "Exclude" ? row : row?.variant !== "catalog" || row.price !== priceList[id]) {
      broken.push(`${where}: ${id} ends ${end}, Output holds ${JSON.stringify(row || null)}, catalogue price ${priceList[id]}`);
    }
  }
};
const build = async (options) => {
  const page = await run.signedInPage();
  await run.buildToBasis(page, options);
  await run.tickOpenLines(page);
  Object.assign(priceList, await page.evaluate((wanted) => Object.fromEntries(state.lineItems.filter((row) => wanted.includes(row.source_basis_line_id))
    .map((row) => [row.source_basis_line_id, pricingAuthorityPrice(row.pricing_authority)])), ids));
  assert(ids.every((id) => priceList[id] > 0), "the fixture lines have no catalogue price", priceList);
  return { page, sessionId: await page.evaluate(() => state.quoteSessionId) };
};
// A finished quote whose older Output still holds lines X'd and saved since, beside lines it never held.
const finished = async (excluded = Object.keys(held)) => {
  const { page, sessionId } = await build();
  for (const id of Object.keys(notHeld)) await run.tag(page, id, "Exclude");
  await confirmToOutput(page);
  await run.openPanel(page, "basis");
  for (const id of excluded) await run.tag(page, id, "Exclude");
  await toDashboard(page);
  await reopen(page, sessionId);
  return { page, sessionId };
};
const lineAt = (page, id) => page.evaluate((lineId) => {
  for (const section of state.quoteBasisSections) {
    const index = (section.lines || []).findIndex((line) => line.id === lineId);
    if (index >= 0) return { at: [section.id, index], text: section.lines[index].text };
  }
  return null;
}, id);
// Re on one line, as staff do it: the selected line, a message, the preview, then Apply. The mock Re reply is
// the scenario's tag (sections 1-5) or, once the scenario asks for it, `replacement` (section 6).
const revise = async (page, id, message, replacement = null) => {
  await run.openPanel(page, "basis");
  if (replacement) await fs.writeFile(path.join(run.runRoot, "basis-chat-reply.json"), JSON.stringify({ tag: null, quantity: null, unit: null, ...replacement }));
  const { at } = await lineAt(page, id);
  await page.locator(`#basisReviewSurface [data-revise-section="${at[0]}"][data-revise-line-index="${at[1]}"]`).click();
  await page.locator("#basisChatPrompt").fill(message);
  await page.locator("#basisChatSendButton").click();
  await page.locator("#basisChatApplyButton:not([disabled])").click({ timeout: 30000 });
  await page.locator("#basisChatOverlay").waitFor({ state: "hidden", timeout: 15000 });
};
const reExclude = (page, id) => revise(page, id, "Remove this line");
// One Confirm: a refusal here means the old price was refused by name.
const confirmOnce = async (where, page) => {
  await run.confirm(page);
  await page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSavePromise, null, { timeout: 60000 });
  if (await page.evaluate(() => state.activeSidePanel) !== "output") {
    broken.push(where + ", Confirm refused: " + (await page.locator("#aiFailureBanner").innerText()).replace(/\s+/g, " "));
    await confirmToOutput(page);
  }
};
// After Confirm, each route's line ends with its old price at the line's size ("price") or with a row that has
// no price, for Output to ask for ("asks").
const expectPriced = async (page, table) => {
  const { rows, sizes } = await page.evaluate((wanted) => ({
    rows: Object.fromEntries(state.outputRows.filter((row) => wanted.includes(row.source_basis_line_id))
      .map((row) => [row.source_basis_line_id, { variant: row.pricing_authority?.variant || null, price: effectiveOutputUnitPrice(row), quantity: Number(row.quantity) }])),
    sizes: Object.fromEntries(state.quoteBasisSections.flatMap((section) => section.lines || [])
      .filter((line) => wanted.includes(line.id)).map((line) => [line.id, Number(line.quantity)])),
  }), table.map((entry) => entry.id));
  for (const { route, id, ends } of table) {
    const row = rows[id];
    const kept = ends === "price"
      ? row?.variant === "catalog" && row.price === priceList[id] && row.quantity === sizes[id]
      : Boolean(row) && !["catalog", "manual", "included"].includes(row.variant) && row.price === null;
    if (!kept) broken.push(`${route}: ${id} should end with ${ends === "price" ? `its old price ${priceList[id]} at ${sizes[id]}` : "no price"}, Output holds ${JSON.stringify(row || null)}`);
  }
};

const duplicate = async (page, sessionId) => {
  await page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`).click();
  await page.locator('#dashboardSelectedSessionPanel [data-dashboard-panel-action="duplicate-session"]').click();
  await page.waitForFunction((id) => !appIsBusy() && state.dashboardActiveSessionId && state.dashboardActiveSessionId !== id, sessionId, { timeout: 30000 });
  return page.evaluate(() => state.dashboardActiveSessionId);
};

try {
  await run.start(scenario);

  // 1. Finished quote: every route on lines Output holds and on lines it does not.
  let { page, sessionId } = await finished();
  for (const entry of ROUTES) {
    if (entry.block && entry.route === "block tick") await blockTag(page, entry.block);
  }
  for (const entry of ROUTES) {
    for (const id of [entry.held, entry.notHeld]) for (const tag of entry.press || []) await run.tag(page, id, tag);
  }
  for (const entry of ROUTES) if (entry.after) await blockTag(page, entry.after);
  const ends = endsOf(ROUTES);
  expect("on screen", await tagsOf(page), ends);
  await idle(page);
  await expectSaved("autosave", sessionId, ends);
  // Opened, not left: a step change there would be a newer save.
  const other = await run.signedInPage();
  await run.openDashboardQuote(other, sessionId);
  expect("another browser, quote not left", await tagsOf(other), ends);
  await other.context().close();
  await reload(page);
  expect("F5", await tagsOf(page), ends);
  await toDashboard(page);
  await expectSaved("Dashboard", sessionId, ends);
  await reopen(page, sessionId);
  expect("Modify", await tagsOf(page), ends);
  await toDashboard(page);
  // A copy has no saved price to restore: none of these lines is ticked or waiting in it.
  await expectSaved("duplicate", await duplicate(page, sessionId), Object.fromEntries(ids.map((id) => [id, "Exclude"])));
  await page.context().close();
  page = await onBasis(sessionId);
  expect("another browser", await tagsOf(page), ends);
  await confirmToOutput(page);
  await expectConfirmed("Confirm", page, ends);
  const download = page.waitForEvent("download", { timeout: 90000 });
  await page.locator("#sideDownloadButton").click();
  const workbook = path.join(run.runRoot, "decisions.xlsx");
  await (await download).saveAs(workbook);
  const read = spawnSync(process.env.PYTHON || (process.platform === "win32" ? "python" : "python3"), ["-c",
    "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); sys.stdout.buffer.write(b' '.join(z.read(n) for n in z.namelist() if n.endswith('.xml')))", workbook],
  { encoding: "utf8", maxBuffer: 1 << 26 });
  assert(read.status === 0, "the workbook cannot be read", read.stderr);
  for (const line of lines) {
    if (read.stdout.includes(line.text) !== (ends[line.id] === "Include")) broken.push(`workbook: ${line.id} ends ${ends[line.id]} but is ${ends[line.id] === "Include" ? "absent" : "present"}`);
  }
  await page.context().close();
  await run.restartServer();

  // 2. Before the first Confirm, and X, save and tick in one sitting without reopening the quote.
  await fs.writeFile(run.scenarioPath, JSON.stringify({ ...scenario, raw: { ...scenario.raw,
    project: { booth_width: 6, booth_depth: 6, staff_booth_size: { booth_width: null, booth_depth: null, booth_area_sqm: 93, stated: "clear" } } } }));
  ({ page, sessionId } = await build({ knownDetails: "Booth is 93 sqm" }));
  await fs.writeFile(run.scenarioPath, JSON.stringify(scenario));
  await run.tag(page, "banner", "Exclude");
  await run.openPanel(page, "customer");
  await idle(page);
  await run.openPanel(page, "basis");
  await run.tag(page, "banner", "Include");
  // Applying the Known details size redrafts the rows; a tick waiting for Confirm goes on waiting.
  await page.locator('#basisReviewSurface [data-known-details-size="apply"]').click();
  await page.locator("#basisReviewSurface [data-known-details-size-proposal]").waitFor({ state: "detached" });
  for (const id of ["carpet", "deck", "graphic", "chair"]) await run.tag(page, id, "Exclude");
  await toDashboard(page);
  await reopen(page, sessionId);
  await run.tag(page, "carpet", "Include");
  await run.tag(page, "deck", "Include");
  await run.tag(page, "deck", "Exclude");
  const draftEnds = { carpet: "Include", deck: "Exclude", graphic: "Exclude", chair: "Exclude", banner: "Include" };
  await idle(page);
  await expectSaved("before the first Confirm", sessionId, draftEnds);
  await reload(page);
  expect("before the first Confirm, F5", await tagsOf(page), draftEnds);
  // Right after F5, X then tick before the autosave starts: the tick still asks for the price.
  await (await run.basisControl(page, "banner", '[data-basis-tag="Exclude"]')).click();
  await (await run.basisControl(page, "banner", '[data-basis-tag="Include"]')).click();
  await idle(page);
  await expectSaved("X and tick right after F5", sessionId, draftEnds);
  // Stale tab: the decision saved last, in another browser, stands.
  const later = await onBasis(sessionId);
  expect("before the first Confirm, another browser", await tagsOf(later), draftEnds);
  await run.tag(later, "carpet", "Exclude");
  await toDashboard(later);
  await later.context().close();
  draftEnds.carpet = "Exclude";
  await run.tag(page, "deck", "Include");
  await page.locator("#backToDashboardButton").click();
  await page.locator("#excelGeneratingModal:not([hidden])").waitFor({ state: "visible", timeout: 20000 });
  await page.context().close();
  page = await onBasis(sessionId);
  expect("after a stale tab", await tagsOf(page), draftEnds);
  // In one sitting, straight through to Confirm: X, leave the step (saved), come back, tick.
  await run.tag(page, "table", "Exclude");
  await run.openPanel(page, "customer");
  await idle(page);
  await run.openPanel(page, "basis");
  await run.tag(page, "table", "Include");
  draftEnds.table = "Include";
  await confirmToOutput(page);
  await expectConfirmed("first Confirm", page, draftEnds);
  await page.context().close();
  await run.restartServer();

  // 3. Re excludes a line ticked back in; Reset Draft leaves nothing waiting.
  ({ page, sessionId } = await finished());
  for (const id of ["carpet", "chair", "graphic"]) await run.tag(page, id, "Include");
  for (const id of ["carpet", "chair"]) await reExclude(page, id);
  const reEnds = { carpet: "Exclude", chair: "Exclude", graphic: "Include" };
  expect("Re, on screen", await tagsOf(page), reEnds);
  await reload(page);
  expect("Re, F5", await tagsOf(page), reEnds);
  await toDashboard(page);
  await expectSaved("Re", sessionId, reEnds);
  await reopen(page, sessionId);
  expect("Re, Modify", await tagsOf(page), reEnds);
  await page.locator("#resetQuoteBasisButton").click();
  await page.locator("#confirmResetDraftButton").click();
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 20000 });
  await toDashboard(page);
  expect("Reset Draft", { waiting: (await savedQuote(sessionId)).waiting.join() }, { waiting: "" });
  await page.context().close();
  await run.restartServer();

  // 4. Everything that rebuilds the rows from the older Output keeps a tick waiting for Confirm, with the
  // line still excluded and unpriced in the saved quote.
  ({ page, sessionId } = await finished(["carpet"]));
  const outputIndex = (id) => page.evaluate((rowId) => state.outputRows.findIndex((row) => row.source_basis_line_id === rowId), id);
  const editRow = async (id, field, value) => {
    // A save that lands while the editor is open commits what is typed and closes it.
    await idle(page);
    const index = await outputIndex(id);
    await page.locator(`tr[data-output-row="${index}"] [data-output-edit-field="${field}"]`).click();
    if (value) {
      const editor = page.locator("[data-output-editor-field]").first();
      await editor.fill(value);
      await editor.press("Enter");
    }
    return index;
  };
  const REBUILDS = {
    "Download from the older Output": async () => {
      await page.locator("#sideDownloadButton").click();
      await page.waitForFunction(() => state.activeSidePanel === "basis" && !appIsBusy(), null, { timeout: 20000 });
    },
    "an edit of another row": () => editRow("deck", "quantity", "9"),
    "an edit of the line's own row": () => editRow("carpet", "quantity", "7"),
    "Included on another row": async () => {
      const index = await editRow("deck", "unit_price_override");
      await page.locator(`[data-output-included-action][data-output-row="${index}"]`).click();
    },
    "Reset Output": async () => {
      await page.locator("#resetOutputButton").click();
      await page.locator("#confirmResetDraftButton").click();
    },
    "a deleted row": async () => {
      await page.locator(`[data-output-delete-row="${await outputIndex("socket")}"]`).click();
      await page.locator("#confirmOutputDeleteButton").click();
    },
  };
  for (const [name, rebuild] of Object.entries(REBUILDS)) {
    await run.tag(page, "carpet", "Include");
    await run.openPanel(page, "output");
    await rebuild();
    await idle(page);
    await expectSaved(name, sessionId, { carpet: "Include" });
    await toDashboard(page);
    await reopen(page, sessionId);
    expect(name + ", Modify", await tagsOf(page), { carpet: "Include" });
    await run.tag(page, "carpet", "Exclude");
    await toDashboard(page);
    await page.context().close();
    await run.restartServer();
    page = await onBasis(sessionId);
  }
  await run.tag(page, "carpet", "Include");
  await run.openPanel(page, "output");
  await page.locator("#sideDownloadButton").click();
  await page.waitForFunction(() => state.activeSidePanel === "basis" && !appIsBusy(), null, { timeout: 20000 });
  await confirmToOutput(page);
  await expectConfirmed("Confirm after Output rebuilds", page, { carpet: "Include" });
  // A price typed on the waiting line's own row waits for Confirm too, and a copy does not carry the tick.
  await run.openPanel(page, "basis");
  await run.tag(page, "carpet", "Exclude");
  await toDashboard(page);
  await page.context().close();
  await run.restartServer();
  page = await onBasis(sessionId);
  await run.tag(page, "carpet", "Include");
  await run.openPanel(page, "output");
  await editRow("carpet", "unit_price_override", "5");
  await idle(page);
  await expectSaved("a price typed on the line's own row", sessionId, { carpet: "Include" });
  await toDashboard(page);
  await expectSaved("duplicate of it", await duplicate(page, sessionId), { carpet: "Exclude" });
  await page.context().close();
  page = await onBasis(sessionId);
  expect("a price typed on the line's own row, another browser", await tagsOf(page), { carpet: "Include" });
  await run.tag(page, "banner", "Exclude");
  await toDashboard(page);
  await expectSaved("a price typed on the line's own row, saved again", sessionId, { carpet: "Include" });
  await reopen(page, sessionId);
  await confirmToOutput(page);
  const typed = (await outputRows(page)).carpet;
  if (typed?.variant !== "manual" || typed.price !== 5) broken.push("Confirm: the typed price is " + JSON.stringify(typed || null));
  await page.context().close();
  await run.restartServer();

  // 5. The older Output out of step with the line. The tick marks the line's own row, and Confirm prices every
  // line at its Quote Basis size, so a size changed on one side only still gets the old price back ("price").
  // A row whose price staff took away in Output (deleted or retyped) keeps the tick, waits as excluded, and
  // comes back at Confirm without a price for Output to ask for, as any line whose row was deleted ("asks").
  const OUT_OF_STEP = [
    { route: "size typed on Quote Basis, then X", id: "carpet", basis: "9", ends: "price" },
    { route: "size typed in Output, then X", id: "deck", before: ["quantity", "3"], ends: "price" },
    { route: "size typed in Output after the tick", id: "graphic", after: ["quantity", "3"], ends: "price" },
    { route: "own row deleted in Output after the tick", id: "banner", after: ["delete"], ends: "asks" },
    { route: "own row retyped in Output after the tick", id: "spotlight6", after: ["description", "retyped words"], ends: "asks" },
  ];
  const stepIds = OUT_OF_STEP.map((entry) => entry.id);
  const stepEnds = Object.fromEntries(stepIds.map((id) => [id, "Include"]));
  const lineQuantities = () => page.evaluate((wanted) => Object.fromEntries(state.quoteBasisSections.flatMap((section) => section.lines || [])
    .filter((line) => wanted.includes(line.id)).map((line) => [line.id, Number(line.quantity)])), stepIds);
  ({ page, sessionId } = await build());
  for (const id of Object.keys(notHeld)) await run.tag(page, id, "Exclude");
  await confirmToOutput(page);
  for (const { id, before } of OUT_OF_STEP) if (before) await editRow(id, ...before);
  await idle(page);
  await run.openPanel(page, "basis");
  for (const { id, basis } of OUT_OF_STEP) {
    if (!basis) continue;
    await (await run.basisControl(page, id, "[data-basis-quantity-edit]")).click();
    const input = page.locator("#basisReviewSurface .basis-quantity-text input").first();
    await input.fill(basis);
    await input.press("Enter");
    await page.waitForFunction(() => !appIsBusy(), null, { timeout: 20000 });
  }
  for (const id of stepIds) await run.tag(page, id, "Exclude");
  await toDashboard(page);
  await reopen(page, sessionId);
  for (const id of stepIds) await run.tag(page, id, "Include");
  await idle(page);
  await expectSaved("out of step, ticked", sessionId, stepEnds);
  // The waiting row is the draft's own: the size typed on Quote Basis, not the older Output's.
  const sizes = await lineQuantities();
  const savedSizes = Object.fromEntries((await run.record(sessionId)).draft_state.lineItems
    .filter((row) => row.reincluded_catalog_selection && stepIds.includes(row.source_basis_line_id))
    .map((row) => [row.source_basis_line_id, Number(row.quantity)]));
  for (const { route, id, basis } of OUT_OF_STEP) {
    if (basis && savedSizes[id] !== sizes[id]) broken.push(`${route}: the tick marked a row of ${savedSizes[id]}, not the line's ${sizes[id]}`);
  }
  await run.openPanel(page, "output");
  for (const { id, after } of OUT_OF_STEP) {
    if (!after) continue;
    if (after[0] !== "delete") {
      await editRow(id, ...after);
      continue;
    }
    await idle(page);
    await page.locator(`[data-output-delete-row="${await outputIndex(id)}"]`).click();
    await page.locator("#confirmOutputDeleteButton").click();
  }
  await idle(page);
  await expectSaved("out of step, after Output edits", sessionId, stepEnds);
  await run.openPanel(page, "basis");
  await reload(page);
  expect("out of step, F5", await tagsOf(page), stepEnds);
  await confirmOnce("out of step", page);
  await expectPriced(page, OUT_OF_STEP);
  await page.context().close();

  // 6. Once staff take a waiting row's price away, no later edit of the line brings it back: Re's new words or
  // size, before or after a reopen, still leave Confirm without a price for Output to ask for. Only a new tick
  // or Reset Output does. Controls: Re without the delete keeps the price the row held (R1), and Re on an
  // ordinary line whose row was deleted asks.
  await fs.writeFile(run.scenarioPath, JSON.stringify({ ...scenario, basis_chat_reply: true }));
  await run.restartServer();
  const RELEASED = [
    { route: "own row deleted, then Re new words", id: "carpet", waits: true, del: true, re: "words", ends: "asks" },
    { route: "own row deleted, then Re new size", id: "deck", waits: true, del: true, re: "size", ends: "asks" },
    { route: "own row deleted, reopened, then Re new words", id: "graphic", waits: true, del: true, re: "words", reopened: true, ends: "asks" },
    { route: "own row deleted, then X and tick again", id: "banner", waits: true, del: true, retick: true, ends: "price" },
    { route: "control: Re new words without the delete", id: "spotlight6", waits: true, re: "words", ends: "price" },
    { route: "control: an ordinary line's row deleted, then Re new words", id: "spotlight3", del: true, re: "words", ends: "asks" },
  ];
  const reviseLine = async ({ id, re }) => {
    const { text } = await lineAt(page, id);
    if (re === "size") await revise(page, id, "quantity should be 20 sqm", { text, quantity: 20, unit: "sqm" });
    else await revise(page, id, "colour should be grey", { text: text + " - grey colour" });
  };
  const waiting = RELEASED.filter((entry) => entry.waits).map((entry) => entry.id);
  ({ page, sessionId } = await build());
  for (const id of Object.keys(notHeld)) await run.tag(page, id, "Exclude");
  await confirmToOutput(page);
  await run.openPanel(page, "basis");
  for (const id of waiting) await run.tag(page, id, "Exclude");
  await toDashboard(page);
  await reopen(page, sessionId);
  for (const id of waiting) await run.tag(page, id, "Include");
  await idle(page);
  await run.openPanel(page, "output");
  for (const { id, del } of RELEASED) {
    if (!del) continue;
    await idle(page);
    await page.locator(`[data-output-delete-row="${await outputIndex(id)}"]`).click();
    await page.locator("#confirmOutputDeleteButton").click();
  }
  await idle(page);
  await run.openPanel(page, "basis");
  for (const { id, retick } of RELEASED) if (retick) for (const tag of ["Exclude", "Include"]) await run.tag(page, id, tag);
  for (const entry of RELEASED) if (entry.re && !entry.reopened) await reviseLine(entry);
  await idle(page);
  await toDashboard(page);
  await reopen(page, sessionId);
  for (const entry of RELEASED) if (entry.reopened) await reviseLine(entry);
  await idle(page);
  const released = RELEASED.filter((entry) => entry.ends === "asks" && entry.waits && entry.del).map((entry) => entry.id);
  await expectSaved("released, after Re", sessionId, Object.fromEntries(released.map((id) => [id, "Include"])));
  await confirmOnce("released", page);
  await expectPriced(page, RELEASED);

  if (broken.length) throw new Error(broken.length + " decisions were not kept:\n" + broken.join("\n"));
  run.finish({});
} catch (error) {
  if (broken.length) console.error(broken.join("\n"));
  run.fail(error);
} finally {
  await run.close();
}
