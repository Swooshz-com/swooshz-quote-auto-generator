// Re on one line, Apply, then Confirm, save, reopen, Confirm again and Excel: the quote row follows the line,
// a typed price and Included stay, a catalogue line turned into another item asks for a price, and every
// other row is unchanged. Synthetic data, mocked AI reply, loopback only, no paid AI.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";

const run = new SyntheticQuoteRun("revise-line");
const LINES = [
  { id: "rv-wall", section: "Synthetic Structures", text: "Feature wall 4m W x 3m H with laminate finish", quantity: 12, unit: "sqm", tag: "Include", estimated: true },
  { id: "rv-counter", section: "Synthetic Structures", text: "Reception counter with timber top", quantity: 2, unit: "nos", tag: "Confirm" },
  { id: "rv-door", section: "Synthetic Structures", text: "Storage room door with lock", quantity: 1, unit: "nos", tag: "Include" },
  { id: "rv-sign", section: "Synthetic Structures", text: "Wall sign with company name", quantity: 1, unit: "nos", tag: "Include" },
  { id: "rv-carpet", section: "Synthetic Floors", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", keyword: "synthetic-floors-synthetic-carpet-tile", estimated: true },
  { id: "rv-vinyl", section: "Synthetic Floors", text: "sqm synthetic vinyl floor finish", quantity: 8, unit: "sqm", keyword: "synthetic-floors-synthetic-vinyl-floor-finish" },
  { id: "rv-cover", section: "Synthetic Floors", text: "sqm synthetic reusable floor cover", quantity: 10, unit: "sqm", keyword: "synthetic-floors-synthetic-reusable-floor-cover" },
  { id: "rv-banner", section: "Synthetic Graphics", text: "sqm synthetic hanging banner", quantity: 6, unit: "sqm", keyword: "synthetic-graphics-synthetic-hanging-banner" },
  { id: "rv-graphic", section: "Synthetic Graphics", text: "sqm synthetic printed wall graphic", quantity: 14, unit: "sqm", keyword: "synthetic-graphics-synthetic-printed-wall-graphic" },
  { id: "rv-table", section: "Synthetic Rentals", text: "nos. synthetic round table", quantity: 2, unit: "nos", keyword: "synthetic-rentals-synthetic-round-table" },
  { id: "rv-chair", section: "Synthetic Rentals", text: "nos. synthetic cafe chair", quantity: 4, unit: "nos", keyword: "synthetic-rentals-synthetic-cafe-chair" },
  { id: "rv-cabinet", section: "Synthetic Rentals", text: "nos. synthetic storage cabinet", quantity: 3, unit: "nos", keyword: "synthetic-rentals-synthetic-storage-cabinet" },
  { id: "rv-socket", section: "Synthetic Lighting And AV", text: "nos. synthetic socket point", quantity: 5, unit: "nos", keyword: "synthetic-lighting-and-av-synthetic-socket-point" },
  { id: "rv-light", section: "Synthetic Lighting And AV", text: "nos. synthetic spotlight 6 inch", quantity: 6, unit: "nos", keyword: "synthetic-lighting-and-av-synthetic-spotlight-6-inch" },
  // X'd and saved, then ticked back in, then revised: by size, by detail, into another item.
  { id: "rv-panel", section: "Synthetic Graphics", text: "nos. synthetic printed system panel", quantity: 4, unit: "nos", keyword: "synthetic-graphics-synthetic-printed-system-panel" },
  { id: "rv-decal", section: "Synthetic Graphics", text: "nos. synthetic counter decal", quantity: 6, unit: "nos", keyword: "synthetic-graphics-synthetic-counter-decal" },
  { id: "rv-spot", section: "Synthetic Lighting And AV", text: "nos. synthetic spotlight 3 inch", quantity: 7, unit: "nos", keyword: "synthetic-lighting-and-av-synthetic-spotlight-3-inch" },
];
const TICKED_BACK = ["rv-panel", "rv-decal", "rv-spot"];
const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
const titles = [...new Set(LINES.map((line) => line.section))];
const scenario = { basis_chat_reply: true, mark_estimates: true, raw: {
  analysis_findings: [], blocking_clarification_questions: [], project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: titles.map((title) => ({ id: slug(title), title, lines: LINES.filter((line) => line.section === title).map((line) => ({
    id: line.id, tag: line.tag || "Include", text: line.text, quantity: line.quantity, unit: line.unit, confidence_pct: 95,
    quantity_estimated: Boolean(line.estimated), pricing_keyword: line.keyword || "",
  })) })),
  line_items: LINES.map((line) => ({
    source_basis_line_id: line.id, section: line.section, description: line.text, quantity: line.quantity, unit: line.unit, pricing_keyword: line.keyword || "",
  })),
} };
const IDS = [...LINES.map((line) => line.id), "standard-project-management-fee", "standard-waste-disposal"];

const idle = (page) => page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 60000 });
const reply = (line) => fs.writeFile(path.join(run.runRoot, "basis-chat-reply.json"), JSON.stringify({ confidence_pct: 95, ...line }));
const basisLine = (page, id) => page.evaluate((wanted) => state.quoteBasisSections.flatMap((section) => section.lines || []).find((line) => line.id === wanted) || null, id);
const target = (page, id) => page.evaluate((wanted) => {
  for (const section of state.quoteBasisSections) {
    const index = (section.lines || []).findIndex((line) => line.id === wanted);
    if (index >= 0) return [section.id, index];
  }
  return null;
}, id);
// What Output shows for each row of the quote: wording, size, price kind and unit price.
const outputRows = (page) => page.evaluate((wanted) => Object.fromEntries(state.outputRows.filter((row) => wanted.includes(row.source_basis_line_id))
  .map((row) => [row.source_basis_line_id, { description: row.description, quantity: Number(row.quantity), unit: row.unit,
    variant: row.pricing_authority?.variant || null, price: effectiveOutputUnitPrice(row), mode: row.price_mode, deferred: outputPricingIsDeferred(row) }])), IDS);
const toOutput = async (page) => {
  await run.confirm(page);
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await idle(page);
};
// With one currency, staff type a price in the quote currency; its authority names that currency.
const typePrice = async (page, id, value) => {
  const index = await page.evaluate((wanted) => state.outputRows.findIndex((row) => row.source_basis_line_id === wanted), id);
  assert(index >= 0, "no Output row to price", id);
  await page.locator(`tr[data-output-row="${index}"] [data-output-edit-field="unit_price_override"][data-output-denomination="quote"]`).click();
  const editor = page.locator("[data-output-editor-field]").first();
  await editor.fill(value);
  await editor.press("Enter");
  await idle(page);
};
// Re on one line, as staff do it: the selected line, a message, the preview, then Apply.
const revise = async (page, id, message, replacement, { apply = true, shot = "" } = {}) => {
  await run.openPanel(page, "basis");
  await reply(replacement);
  const at = await target(page, id);
  assert(at, "basis line missing", id);
  await page.locator(`#basisReviewSurface [data-revise-section="${at[0]}"][data-revise-line-index="${at[1]}"]`).first().click();
  await page.locator("#basisChatOverlay.is-open").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#basisChatPrompt").fill(message);
  await page.locator("#basisChatSendButton").click();
  await page.locator("#basisChatApplyButton:not([disabled])").waitFor({ state: "visible", timeout: 30000 }).catch(async (error) => {
    throw new Error("no preview for " + id + ": " + await page.locator("#basisChatMessages").innerText(), { cause: error });
  });
  const card = (await page.locator("#basisChatProposal").innerText()).replace(/\s+/g, " ").trim();
  if (shot) await page.locator("#basisChatOverlay .basis-chat-panel").first().screenshot({ path: path.join(run.runRoot, shot) });
  if (apply) {
    await page.locator("#basisChatApplyButton").click();
    await page.locator("#basisChatOverlay").waitFor({ state: "hidden", timeout: 15000 });
    await idle(page);
  }
  return card;
};
const toDashboard = async (page) => {
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 20000 });
};
const savedRows = async (sessionId) => {
  const { draft_state: draft } = await run.record(sessionId);
  return {
    lines: Object.fromEntries(draft.quoteBasisSections.flatMap((section) => section.lines).filter((line) => IDS.includes(line.id))
      .map((line) => [line.id, { text: line.text, tag: line.tag, quantity: line.quantity, estimate: Boolean(line.quantity_estimate) }])),
    rows: Object.fromEntries(draft.lineItems.filter((row) => IDS.includes(row.source_basis_line_id))
      .map((row) => [row.source_basis_line_id, { description: row.description, variant: row.pricing_authority?.variant, price: row.pricing_authority?.price ?? null,
        currency: row.pricing_authority?.price_currency || "", amount: row.pricing_basis_amount ?? null, mark: Boolean(row.reincluded_catalog_selection) }])),
  };
};
const workbookRows = (file) => {
  const read = spawnSync(process.env.PYTHON || (process.platform === "win32" ? "python" : "python3"), ["-c",
    "import sys,zipfile,re,html,json; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps([[html.unescape(v) for v in re.findall(r'<(?:v|t)[^>]*>([^<]*)<', row)] for n in z.namelist() if 'worksheets/sheet' in n for row in re.findall(r'<row .*?</row>', z.read(n).decode('utf8'), re.S)]))", file],
  { encoding: "utf8", maxBuffer: 1 << 26 });
  assert(read.status === 0, "the workbook cannot be read", read.stderr);
  return JSON.parse(read.stdout);
};

const SIBLINGS = ["rv-graphic", "rv-chair", "rv-light", "standard-project-management-fee"];
const result = {};
try {
  await run.start(scenario);
  const page = await run.signedInPage();
  await run.buildToBasis(page);
  const sessionId = await page.evaluate(() => state.quoteSessionId);
  // Decisions staff make before the first Confirm: every line ticked (the counter becomes price to be
  // discussed, the standard rows Included), then three catalogue lines X'd.
  await run.tickOpenLines(page);
  // AI proposals (own lines) wait for their own tick too.
  for (const id of await page.evaluate(() => state.quoteBasisSections.flatMap((section) => section.lines || [])
    .filter((line) => isPendingAiProposalLine(line)).map((line) => line.id))) {
    await (await run.basisControl(page, id, '[data-basis-tag]:not([data-basis-tag="Exclude"])')).click();
    await idle(page);
  }
  for (const id of ["rv-cover", "rv-cabinet", "rv-socket", ...TICKED_BACK]) await run.tag(page, id, "Exclude");
  await toOutput(page);
  for (const [id, price] of [["rv-wall", "55.50"], ["rv-vinyl", "9.25"], ["rv-table", "20"]]) await typePrice(page, id, price);
  const before = await outputRows(page);
  result.before = before;

  const cards = {};
  cards.wallQuantity = await revise(page, "rv-wall", "quantity should be 15 sqm", { text: "Feature wall 4m W x 3m H with laminate finish", quantity: 15, unit: "sqm" }, { shot: "preview-quantity.png" });
  cards.wall = await revise(page, "rv-wall", "finish should be veneer", { text: "Feature wall 4m W x 3m H with veneer finish" });
  cards.vinyl = await revise(page, "rv-vinyl", "should be matt finish", { text: "[ sqm synthetic vinyl floor finish ] - matt finish" });
  cards.banner = await revise(page, "rv-banner", "colour should be black", { text: "[ sqm synthetic hanging banner ] - black colour" });
  cards.carpet = await revise(page, "rv-carpet", "should be red rug", { text: "red rug" }, { shot: "preview-notice.png" });
  cards.table = await revise(page, "rv-table", "should be bar table", { text: "bar table" });
  cards.cover = await revise(page, "rv-cover", "colour should be blue", { text: "[ sqm synthetic reusable floor cover ] - blue colour", tag: "Exclude" });
  cards.counter = await revise(page, "rv-counter", "top should be marble", { text: "Reception counter with marble top" });
  cards.door = await revise(page, "rv-door", "door should be glass", { text: "Storage room glass door with lock" });
  // Text that starts like a formula stays text in the workbook.
  cards.sign = await revise(page, "rv-sign", "sign should be +65 6123 4567 hotline", { text: "+65 6123 4567 hotline on wall sign" });
  cards.waste = await revise(page, "standard-waste-disposal", "should be recycling at the venue", { text: "Waste disposal and recycling at the venue" });
  cards.cabinet = await revise(page, "rv-cabinet", "include this please", { text: "[ nos. synthetic storage cabinet ]", tag: "Include" });
  await run.tag(page, "rv-socket", "Include");
  for (const id of TICKED_BACK) await run.tag(page, id, "Include");
  const panelText = (await basisLine(page, "rv-panel")).text;
  cards.panel = await revise(page, "rv-panel", "quantity should be 5 nos", { text: panelText, quantity: 5, unit: "nos" });
  cards.decal = await revise(page, "rv-decal", "colour should be black", { text: "[ nos. synthetic counter decal ] - black colour" });
  cards.spot = await revise(page, "rv-spot", "should be wall washer", { text: "wall washer" });
  result.cards = cards;
  // The preview shows each size, and the notice only where a catalogue price will go.
  assert(cards.wallQuantity.includes("12 sqm Feature wall") && cards.wallQuantity.includes("15 sqm Feature wall"), "the preview hides the size change", cards.wallQuantity);
  const NOTICE = "This is no longer the same catalogue item, so its catalogue price will not be kept.";
  for (const [name, card] of Object.entries(cards)) {
    assert(card.includes(NOTICE) === ["carpet", "spot"].includes(name), "the catalogue notice is wrong", { name, card });
  }
  await idle(page);
  // Re's Include is the line's own tick: the same saved tag and mark, and the same price at Confirm.
  const applied = await savedRows(sessionId);
  result.reinclude = { re: [applied.lines["rv-cabinet"].tag, applied.rows["rv-cabinet"].mark], tick: [applied.lines["rv-socket"].tag, applied.rows["rv-socket"].mark] };
  assert(JSON.stringify(result.reinclude.re) === JSON.stringify(["Exclude", true]) && JSON.stringify(result.reinclude.tick) === JSON.stringify(["Exclude", true]),
    "Re Include saved something the tick does not", result.reinclude);
  assert(!applied.lines["rv-wall"].estimate && applied.lines["rv-carpet"].estimate, "Est. is wrong after Apply", applied.lines);
  // The saved row already agrees with its line: the kept price times the new size.
  assert(applied.rows["rv-wall"].amount === 832.5, "the saved row's amount is not for the new size", applied.rows["rv-wall"]);
  // A typed price stays in the currency it was typed in (a reference-currency price: the table below).
  assert(["rv-wall", "rv-vinyl", "rv-table"].every((id) => applied.rows[id].currency === "SGD"), "a typed price changed currency", applied.rows);
  // A line ticked back in and then revised is still saved as excluded with its tick until Confirm, on this
  // page and after a reopen (PR 432: no unconfirmed line is saved as included).
  const waiting = (saved) => Object.fromEntries(TICKED_BACK.map((id) => [id, [saved.lines[id].tag, saved.rows[id].mark]]));
  result.tickedBack = { applied: waiting(applied) };
  const wanted = Object.fromEntries(TICKED_BACK.map((id) => [id, ["Exclude", true]]));
  assert(JSON.stringify(result.tickedBack.applied) === JSON.stringify(wanted), "a revised line ticked back in was saved as included", result.tickedBack);
  // Seventeen Re edits in a minute reach the app's per-minute save limit (30), which staff never do: let it pass.
  await page.waitForTimeout(61000);
  await toDashboard(page);
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "basis");
  result.tickedBack.reopened = await page.evaluate((ids) => Object.fromEntries(ids.map((id) => [id, [
    normalizeBasisTag(state.quoteBasisSections.flatMap((section) => section.lines).find((line) => line.id === id).tag),
    Boolean(state.lineItems.find((row) => row.source_basis_line_id === id)?.reincluded_catalog_selection)]])), TICKED_BACK);
  assert(JSON.stringify(result.tickedBack.reopened) === JSON.stringify(Object.fromEntries(TICKED_BACK.map((id) => [id, ["Include", true]]))),
    "the reopened quote lost a tick waiting for Confirm", result.tickedBack);
  assert(JSON.stringify(waiting(await savedRows(sessionId))) === JSON.stringify(wanted), "the reopen saved a waiting line as included");

  await toOutput(page);
  const expected = {
    ...Object.fromEntries(SIBLINGS.map((id) => [id, before[id]])),
    "rv-wall": { ...before["rv-wall"], description: "Feature wall 4m W x 3m H with veneer finish", quantity: 15 },
    "rv-vinyl": before["rv-vinyl"],
    "rv-banner": before["rv-banner"],
    "rv-carpet": { ...before["rv-carpet"], description: "red rug", variant: "none", price: null },
    "rv-table": { ...before["rv-table"], description: "bar table" },
    "rv-counter": { ...before["rv-counter"], description: "Reception counter with marble top", deferred: false },
    "rv-door": { ...before["rv-door"], description: "Storage room glass door with lock" },
    "rv-sign": { ...before["rv-sign"], description: "+65 6123 4567 hotline on wall sign" },
    "standard-waste-disposal": { ...before["standard-waste-disposal"], description: "Waste disposal and recycling at the venue" },
    "rv-cabinet": { description: "nos. synthetic storage cabinet", quantity: 3, unit: "nos", variant: "catalog", price: 21.6, mode: "Priced", deferred: false },
    "rv-socket": { description: "nos. synthetic socket point", quantity: 5, unit: "nos", variant: "catalog", price: 12, mode: "Priced", deferred: false },
    // Ticked back in, revised, reopened: the same item gets its catalogue price back; another item asks again.
    "rv-panel": { description: "nos. synthetic printed system panel", quantity: 5, unit: "nos", variant: "catalog", price: 28.8, mode: "Priced", deferred: false },
    "rv-decal": { description: "nos. synthetic counter decal", quantity: 6, unit: "nos", variant: "catalog", price: 8.75, mode: "Priced", deferred: false },
    "rv-spot": { description: "wall washer", quantity: 7, unit: "nos", variant: "none", price: null, mode: "Priced", deferred: false },
  };
  const same = (where, actual, wanted) => {
    for (const id of new Set([...Object.keys(actual), ...Object.keys(wanted)])) {
      assert(JSON.stringify(actual[id]) === JSON.stringify(wanted[id]), where + ": a row is wrong", { id, actual: actual[id], wanted: wanted[id] });
    }
  };
  result.confirmed = await outputRows(page);
  same("first Confirm after Apply", result.confirmed, expected);
  const missing = await page.evaluate(() => state.outputErrors);
  const unpriced = await page.evaluate(() => state.outputRows.filter((row) => effectiveOutputUnitPrice(row) === null && row.price_mode !== "Included").map((row) => row.source_basis_line_id).sort());
  assert(JSON.stringify(unpriced) === JSON.stringify(["rv-carpet", "rv-counter", "rv-door", "rv-sign", "rv-spot"]) && missing.length === 5
    && missing.every((text) => text.endsWith("Unit price is required.")), "Output asks for the wrong prices", { missing, unpriced });

  for (const [id, price] of [["rv-carpet", "30"], ["rv-counter", "40"], ["rv-door", "70"], ["rv-sign", "15"], ["rv-spot", "18"]]) {
    await typePrice(page, id, price);
    expected[id] = { ...expected[id], variant: "manual", price: Number(price) };
  }
  same("priced", await outputRows(page), expected);
  const saved = await savedRows(sessionId);
  for (const id of Object.keys(expected)) {
    assert(saved.rows[id]?.description === expected[id].description && saved.rows[id].variant === expected[id].variant && saved.rows[id].price === expected[id].price,
      "the saved quote differs from Output", { id, saved: saved.rows[id], expected: expected[id] });
  }
  await toDashboard(page);
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "output");
  same("reopened", await outputRows(page), expected);
  await run.openPanel(page, "basis");
  await toOutput(page);
  same("second Confirm", await outputRows(page), expected);
  assert(!(await page.evaluate(() => state.outputErrors)).length, "Output still asks for a price");

  // The prices, reopen and Confirm since then reach the same limit again.
  await page.waitForTimeout(61000);
  const download = page.waitForEvent("download", { timeout: 90000 });
  await page.locator("#sideDownloadButton").click();
  const workbook = path.join(run.runRoot, "revised.xlsx");
  await (await download.catch(async (error) => {
    throw new Error("no workbook: " + JSON.stringify(await page.evaluate(() => ({ panel: state.activeSidePanel, errors: state.outputErrors,
      text: [...document.querySelectorAll(".modal-overlay.is-open, [role=alertdialog], .status-message, .output-validation, .error")].map((node) => node.innerText).join(" | ").slice(0, 1500) }))), { cause: error });
  })).saveAs(workbook);
  const cells = workbookRows(workbook);
  result.workbook = {};
  for (const [id, row] of Object.entries(expected)) {
    // Text starting like a formula is written with a leading apostrophe (scripts/generate_quote.py).
    const found = cells.filter((cell) => cell.includes(row.description) || cell.includes("'" + row.description));
    assert(found.length === 1, "the workbook does not hold the row once", { id, description: row.description, found });
    result.workbook[id] = found[0];
    // The workbook holds each row's amount: quantity times the unit price, or the word Included.
    const amount = row.mode === "Priced" ? String(Math.round(row.quantity * row.price * 100) / 100) : "Included";
    assert(found[0].some((cell) => (row.mode === "Priced" ? Number(cell) === Number(amount) : cell === amount)), "the workbook amount differs", { id, row: found[0], amount });
  }
  for (const old of ["laminate", "sqm synthetic carpet tile", "round table", "timber top", "room door with lock", "reusable floor cover", "production venue"]) {
    assert(!cells.some((cell) => cell.some((value) => value.includes(old))), "the workbook still holds old wording", old);
  }
  const formulas = spawnSync(process.env.PYTHON || (process.platform === "win32" ? "python" : "python3"), ["-c",
    "import sys,zipfile,re,json; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps([f for n in z.namelist() if 'worksheets/sheet' in n for f in re.findall(r'<f[^>]*>([^<]*)</f>', z.read(n).decode('utf8'))]))", workbook],
  { encoding: "utf8" });
  assert(formulas.status === 0 && !JSON.parse(formulas.stdout).some((formula) => formula.includes("hotline") || formula.includes("6123")), "Re text became a formula", formulas.stdout);

  // The price rule as a table, on the app's own functions and rows of this quote, including what Re cannot
  // reach today (a new unit) and prices that are no longer valid. Each change is then sent to the server's
  // own check, which must keep exactly the prices the page kept.
  const table = await page.evaluate(async () => {
    const rowOf = (id) => JSON.parse(JSON.stringify(state.lineItems.find((row) => row.source_basis_line_id === id)));
    const lineOf = (id) => JSON.parse(JSON.stringify(state.quoteBasisSections.flatMap((section) => section.lines).find((line) => line.id === id)));
    const withAuthority = (id, change) => { const row = rowOf(id); change(row); return row; };
    const HELD = {
      manualOwn: ["rv-wall", rowOf("rv-wall")],
      manualReference: ["rv-light", withAuthority("rv-light", (row) => { row.pricing_authority = buildPricingAuthority("manual", row, { price: 16.25 }); })],
      manualCatalogue: ["rv-vinyl", rowOf("rv-vinyl")],
      catalogue: ["rv-banner", rowOf("rv-banner")],
      included: ["standard-waste-disposal", rowOf("standard-waste-disposal")],
      // Included once, but its authority no longer holds (another currency): it becomes an ordinary priced row.
      includedStale: ["standard-project-management-fee", withAuthority("standard-project-management-fee", (row) => { row.pricing_authority.currency = "USD"; })],
      discussed: ["rv-counter", withAuthority("rv-counter", (row) => { row.pricing_authority = buildPricingAuthority("none", row); delete row.unit_price_override; row.unmatched_pricing_offer = row.deferred_pricing = "a".repeat(64); })],
      historical: ["rv-door", withAuthority("rv-door", (row) => { row.pricing_authority = buildPricingAuthority("historical", row); })],
      tampered: ["rv-graphic", withAuthority("rv-graphic", (row) => { row.pricing_authority.price = 1; })],
      otherCurrency: ["rv-table", withAuthority("rv-table", (row) => { row.pricing_authority.price_currency = "USD"; })],
      none: ["rv-carpet", withAuthority("rv-carpet", (row) => { row.pricing_authority = buildPricingAuthority("none", row); })],
    };
    const other = { sqm: "lot", nos: "lot" };
    const alias = { sqm: "m2", nos: "pcs" };
    const CHANGES = {
      wording: (line) => ({ ...line, text: line.pricing_keyword ? `[ ${outputCatalogDescription(line)} ] - revised detail` : line.text + " revised" }),
      otherItem: (line) => { const { pricing_keyword, catalog_description, pricing_reference_description, catalog_unit_price, ...rest } = line; return { ...rest, text: "another item", custom_pricing: true }; },
      unit: (line) => ({ ...line, unit: other[normalizeUnit(line.unit)] }),
      unitAlias: (line) => ({ ...line, unit: alias[normalizeUnit(line.unit)] }),
      quantity: (line) => ({ ...line, quantity: Number(line.quantity) + 1 }),
      tag: (line) => ({ ...line, tag: normalizeBasisTag(line.tag) === "Exclude" ? "Include" : "Exclude" }),
      wordingAndQuantity: (line) => CHANGES.quantity(CHANGES.wording(line)),
    };
    const out = {};
    const saved = { sections: state.quoteBasisSections, basis: state.quoteBasis };
    for (const [change, edit] of Object.entries(CHANGES)) {
      const rows = state.lineItems.map((row) => JSON.parse(JSON.stringify(row)));
      const sections = cloneQuoteBasisSections(state.quoteBasisSections);
      for (const [held, [id, row]] of Object.entries(HELD)) {
        const before = lineOf(id);
        const after = edit(before);
        const next = revisedQuoteRowForApply(row, before, after);
        out[held + " " + change] = next === row ? "unchanged"
          : { variant: next.pricing_authority?.variant, price: pricingAuthorityPrice(next.pricing_authority), discussed: Boolean(next.deferred_pricing), mode: next.price_mode };
        rows[rows.findIndex((entry) => entry.source_basis_line_id === id)] = next;
        sections.forEach((section) => { section.lines = section.lines.map((line) => (line.id === id ? after : line)); });
      }
      // The server reads the edited lines with the rows, as it does at Confirm after Apply.
      state.quoteBasisSections = sections;
      state.quoteBasis = quoteBasisFromSections(sections);
      let payload;
      try { payload = buildLineItemNormalizePayload(); } finally { state.quoteBasisSections = saved.sections; state.quoteBasis = saved.basis; }
      payload.line_items = rows;
      const response = await postJson("/api/line-items/normalize", payload);
      const served = new Map((response?.data?.line_items || []).map((row) => [row.source_basis_line_id, row]));
      for (const [held, [id]] of Object.entries(HELD)) {
        const row = served.get(id);
        out["server " + held + " " + change] = row ? { variant: row.pricing_authority?.variant, price: pricingAuthorityPrice(row.pricing_authority), mode: row.price_mode } : null;
      }
    }
    return out;
  });
  result.table = table;
  // Expected, written out: what each held price becomes for each change, in the page and on the server.
  const KEPT = { manualReference: ["manual", 16.25], manualOwn: ["manual", 55.5], manualCatalogue: ["manual", 9.25], catalogue: ["catalog", 21.6], included: ["included", 0] };
  const KEEPS = {
    wording: ["manualReference", "manualOwn", "manualCatalogue", "catalogue", "included"],
    otherItem: ["manualReference", "manualOwn", "manualCatalogue", "included"],
    unit: ["included"],
    quantity: ["manualReference", "manualOwn", "manualCatalogue", "catalogue", "included"],
    wordingAndQuantity: ["manualReference", "manualOwn", "manualCatalogue", "catalogue", "included"],
  };
  const HELD_KINDS = ["manualReference", "manualOwn", "manualCatalogue", "catalogue", "included", "includedStale", "discussed", "historical", "tampered", "otherCurrency", "none"];
  // A changed unit alias or tag alone is no change to the row: it is left exactly as it was.
  const UNTOUCHED_ON_SERVER = { manualReference: ["manual", 16.25], manualOwn: ["manual", 55.5], manualCatalogue: ["manual", 9.25], catalogue: ["catalog", 21.6], included: ["included", 0],
    includedStale: ["historical", null], discussed: ["none", null], historical: ["historical", null], otherCurrency: ["historical", null], none: ["none", null] };
  const wrong = [];
  for (const change of ["wording", "otherItem", "unit", "unitAlias", "quantity", "tag", "wordingAndQuantity"]) {
    for (const held of HELD_KINDS) {
      const page = table[held + " " + change];
      const server = table["server " + held + " " + change];
      if (!KEEPS[change]) {
        if (page !== "unchanged") wrong.push([held, change, "page", page]);
        const wanted = held === "tampered" ? (change === "tag" ? ["historical", null] : ["catalog", 21]) : UNTOUCHED_ON_SERVER[held];
        if (JSON.stringify([server?.variant, server?.price]) !== JSON.stringify(wanted)) wrong.push([held, change, "server", server, wanted]);
        continue;
      }
      const kept = KEEPS[change].includes(held) ? KEPT[held] : ["none", null];
      if (JSON.stringify(page) !== JSON.stringify({ variant: kept[0], price: kept[1], discussed: false, mode: kept[0] === "included" ? "Included" : "Priced" })) wrong.push([held, change, "page", page, kept]);
      // The server's own live grant gives the real catalogue price back to a forged one while the item stays the same.
      const served = held === "tampered" && ["wording", "quantity", "wordingAndQuantity"].includes(change) ? ["catalog", 21] : kept;
      if (JSON.stringify([server?.variant, server?.price]) !== JSON.stringify(served)) wrong.push([held, change, "server", server, served]);
    }
  }
  // A row that was Included but no longer holds that authority is priced again, never left reading Included.
  for (const change of Object.keys(KEEPS)) {
    if (table["server includedStale " + change]?.mode !== "Priced") wrong.push(["includedStale", change, "server mode", table["server includedStale " + change]]);
  }
  assert(!wrong.length, "the price rule differs from its table", wrong);

  run.finish(result);
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
