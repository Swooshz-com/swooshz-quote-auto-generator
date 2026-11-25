// Known details Apply with a floor area, through Confirm to the workbook: lines whose words start with the booth's
// size keep their quantity, unit and match, the whole-floor line takes the applied area, and the booth profile
// under Detected booth states only that area. Synthetic data, a fake AI reply, no paid AI.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";
import { spawnSync } from "node:child_process";
import path from "node:path";

const run = new SyntheticQuoteRun("applied-size");
const LINES = [
  { id: "as-carpet", section: "Synthetic Floors", text: "36 sqm synthetic carpet tile", quantity: 36, unit: "sqm", keyword: "synthetic-floors-synthetic-carpet-tile", after: 93 },
  { id: "as-table", section: "Synthetic Rentals", text: "6m x 6m booth round table, white", quantity: 2, unit: "nos", keyword: "synthetic-rentals-synthetic-round-table", after: 2 },
  { id: "as-structure", section: "Synthetic Structure", text: "6m x 6m booth structure in system panel", quantity: 1, unit: "lot", keyword: "", after: 1 },
];
const PROFILE = { id: "as-profile", section: "Synthetic Floors", text: "6m x 6m booth footprint, corner" };
const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
const sections = [...new Set(LINES.map((line) => line.section))];
const scenario = { raw: {
  analysis_findings: [], blocking_clarification_questions: [],
  project: { booth_width: 6, booth_depth: 6, staff_booth_size: { booth_width: null, booth_depth: null, booth_area_sqm: 93, stated: "clear" } },
  quote_basis_sections: sections.map((title) => ({ id: slug(title), title, lines: [
    ...LINES.filter((line) => line.section === title).map(({ id, text, quantity, unit, keyword }) => ({ id, tag: "Include", text, quantity, unit, confidence_pct: 90, pricing_keyword: keyword })),
    ...(PROFILE.section === title ? [{ id: PROFILE.id, tag: "Confirm", text: PROFILE.text, quantity: null, unit: null, confidence_pct: 90, pricing_keyword: "" }] : []),
  ] })),
  line_items: LINES.map(({ id, section, text, quantity, unit, keyword }) => ({ source_basis_line_id: id, section, description: text, quantity, unit, pricing_keyword: keyword })),
} };
const held = (page) => page.evaluate((ids) => Object.fromEntries(ids.map((id) => {
  const line = state.quoteBasisSections.flatMap((section) => section.lines || []).find((entry) => entry.id === id);
  const row = state.lineItems.find((entry) => entry.source_basis_line_id === id);
  return [id, { line: [Number(line?.quantity), line?.unit], row: [Number(row?.quantity), row?.unit], variant: row?.pricing_authority?.variant }];
})), LINES.map((line) => line.id));
const detected = async (page) => (await page.locator("#basisReviewSurface .basis-visual-display").allInnerTexts()).join(" ").replace(/\s+/g, " ");
const check = async (page, stage, floor) => {
  const now = await held(page);
  for (const { id, quantity, unit, keyword } of LINES) {
    const size = [id === "as-carpet" ? floor : quantity, unit];
    assert(JSON.stringify([now[id].line, now[id].row, now[id].variant]) === JSON.stringify([size, size, keyword ? "catalog" : "none"]),
      stage + ": the line no longer reads as drafted", { id, ...now[id], expected: size });
  }
  const profile = await detected(page);
  assert(profile.includes(`${floor === 93 ? "93 sqm" : "6m x 6m"} booth footprint, corner`), stage + ": Detected booth does not state the booth's size", profile);
  assert(floor !== 93 || !profile.includes("6m x 6m"), stage + ": Detected booth still states the replaced size", profile);
};

try {
  await run.start(scenario);
  let page = await run.signedInPage();
  await run.buildToBasis(page, { knownDetails: "Booth is 93 sqm" });
  await check(page, "analysed", 36);
  await page.locator('#basisReviewSurface [data-known-details-size="apply"]').click();
  await page.locator("#basisReviewSurface [data-known-details-size-proposal]").waitFor({ state: "detached" });
  await check(page, "applied", 93);
  const sessionId = await page.evaluate(() => state.quoteSessionId);
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 20000 });
  await page.context().close();
  page = await run.signedInPage();
  await run.openDashboardQuote(page, sessionId);
  await run.openPanel(page, "basis");
  await check(page, "reopened in another browser", 93);
  await page.locator("#analyseAgainButton").click();
  const again = page.locator("#analysisConfirmStartButton");
  await again.waitFor({ state: "visible", timeout: 10000 });
  await again.click();
  await page.waitForFunction(() => appIsBusy(), null, { timeout: 20000 }).catch(() => {});
  await run.basisReady(page);
  await check(page, "analysed again", 93);
  await run.tickOpenLines(page);
  // The unmatched line is kept with its own price, as an AI proposal.
  const keep = await run.basisControl(page, "as-structure", ':is([data-basis-tag="Include"], [data-basis-tag="Custom"])');
  if (await keep.count()) await keep.click();
  await run.confirm(page);
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 60000 });
  const index = await page.evaluate(() => state.outputRows.findIndex((row) => row.source_basis_line_id === "as-structure"));
  await page.locator(`tr[data-output-row="${index}"] [data-output-edit-field="unit_price_override"]`).click();
  const editor = page.locator("[data-output-editor-field]").first();
  await editor.fill("500");
  await editor.press("Enter");
  await page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 20000 });
  const output = await page.evaluate(() => Object.fromEntries(state.outputRows.map((row) => [row.source_basis_line_id, [Number(row.quantity), row.unit, effectiveOutputUnitPrice(row)]])));
  for (const [id, expected] of Object.entries({ "as-carpet": [93, "sqm", 14.4], "as-table": [2, "nos", 15], "as-structure": [1, "lot", 500] })) {
    assert(JSON.stringify(output[id]) === JSON.stringify(expected), "Output: the line no longer reads as drafted", { id, output: output[id], expected });
  }
  assert(!("as-profile" in output), "the booth profile reached Output", output);
  const download = page.waitForEvent("download", { timeout: 90000 });
  await page.locator("#sideDownloadButton").click();
  const workbook = path.join(run.runRoot, "applied.xlsx");
  await (await download).saveAs(workbook);
  const read = spawnSync(process.env.PYTHON || (process.platform === "win32" ? "python" : "python3"), ["-c",
    "import sys,zipfile,re,html,json; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps([[html.unescape(v) for v in re.findall(r'<(?:v|t)[^>]*>([^<]*)<', row)] for n in z.namelist() if 'worksheets/sheet' in n for row in re.findall(r'<row .*?</row>', z.read(n).decode('utf8'), re.S)]))", workbook],
  { encoding: "utf8", maxBuffer: 1 << 26 });
  assert(read.status === 0, "the workbook cannot be read", read.stderr);
  const row = (words) => JSON.parse(read.stdout).find((cells) => cells.some((cell) => cell.includes(words)));
  const exported = { structure: row("booth structure in system panel"), carpet: row("synthetic carpet tile") };
  assert(exported.structure?.includes("1 lot") && exported.structure.includes("500.0"), "the workbook does not hold the line as drafted", exported);
  assert(exported.carpet?.includes("93 sqm"), "the workbook does not hold the applied area", exported);
  run.finish({ exported });
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
