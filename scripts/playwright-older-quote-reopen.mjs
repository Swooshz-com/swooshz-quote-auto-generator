// Older saved quotes reopen where staff left them (#385). A quote saved before the Known details box
// existed has no known_details in its saved Customer step. Build and generate a quote in the real UI,
// restart the server on that quote saved in the older shape, then reopen it from the dashboard with
// Modify quote: it opens at Output with its pricing rows. A real Customer step change made after
// that still sends the reopen back to Customer details. Synthetic only: the owner-UAT mock harness
// replaces the AI provider with a deterministic reply; no network, live data or paid AI.
import { signIn } from "./internal-password-browser-test.mjs";
import { SyntheticBrowserBoundary, freshBrowserRun } from "./synthetic-browser-boundary.mjs";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

for (const key of Object.keys(process.env)) {
  if (/(API_KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)) process.env[key] = "";
}
const sourceRoot = fileURLToPath(new URL("..", import.meta.url));
const { chromium } = createRequire(path.join(sourceRoot, "package.json"))("playwright");
const evidenceRoot = process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(sourceRoot, "_logs/browser/older-quote-reopen");
const runRoot = await freshBrowserRun(evidenceRoot, "run-");
const dirs = Object.fromEntries(["data", "output", "temp", "pricing", "profiles", "logs"].map((name) => [name, path.join(runRoot, name)]));
await Promise.all(Object.values(dirs).map((dir) => fs.mkdir(dir, { recursive: true })));
const assert = (ok, message, detail) => { if (!ok) throw new Error(message + (detail === undefined ? "" : ": " + JSON.stringify(detail))); };
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const fixtureId = "synthetic-exhibition-fixture-pricing";
const profileId = "synthetic-exhibition-fixture-template";
const carpet = "synthetic-floors-synthetic-carpet-tile";
const graphic = "synthetic-graphics-synthetic-printed-wall-graphic";
const customer = "Synthetic Older Quote Customer";
const show = "Synthetic Show";

const scenarioPath = path.join(runRoot, "scenario.json");
await fs.writeFile(scenarioPath, JSON.stringify({ raw: {
  analysis_findings: [], blocking_clarification_questions: [], project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: [{ id: "synthetic-floors", title: "Synthetic Floors", lines: [
    { id: "older-carpet", tag: "Include", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", confidence_pct: 95, pricing_keyword: carpet },
    { id: "older-graphic", tag: "Include", text: "sqm synthetic printed wall graphic", quantity: 12, unit: "sqm", confidence_pct: 95, pricing_keyword: graphic },
  ] }],
  line_items: [
    { source_basis_line_id: "older-carpet", section: "Synthetic Floors", description: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", pricing_keyword: carpet },
    { source_basis_line_id: "older-graphic", section: "Synthetic Floors", description: "sqm synthetic printed wall graphic", quantity: 12, unit: "sqm", pricing_keyword: graphic },
  ],
} }, null, 2));
const fixtureDir = path.join(dirs.pricing, fixtureId);
await fs.cp(path.join(sourceRoot, "tests/fixtures/quote-generator/pricing-references", fixtureId), fixtureDir, { recursive: true });
const canonicalize = spawnSync(python, ["-c", "import json,sys; sys.path.insert(0, sys.argv[1]); from webapp import server as w; p=sys.argv[2]; d=json.load(open(p, encoding='utf-8')); open(p, 'w', encoding='utf-8').write(json.dumps(w.pricing_reference_catalog_payload(d), indent=2))", sourceRoot, path.join(fixtureDir, "pricing-catalog.json")], { cwd: sourceRoot, windowsHide: true, encoding: "utf8", env: { ...process.env, SQAG_DISABLE_DOTENV: "1" } });
if (canonicalize.status !== 0) throw new Error("catalogue canonicalisation failed: " + canonicalize.stderr);
await fs.cp(path.join(sourceRoot, "tests/fixtures/quote-generator/profiles", profileId), path.join(dirs.profiles, profileId), { recursive: true });

let server = null, browser = null, serverErr = "";
// One server process per stage over the same data folders, as an upgrade restarts the app.
async function startServer() {
  const child = spawn(python, [path.join(sourceRoot, "tests/owner_uat_mock_harness.py"), sourceRoot, dirs.data, dirs.output, dirs.temp, dirs.logs, dirs.pricing, dirs.profiles, scenarioPath], {
    cwd: sourceRoot, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, OPENAI_API_KEY: "", DEEPSEEK_API_KEY: "", SQAG_DISABLE_DOTENV: "1", PYTHONUNBUFFERED: "1" },
  });
  child.stderr.on("data", (chunk) => { serverErr = (serverErr + chunk).slice(-100000); });
  const baseUrl = await new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("server not ready: " + serverErr.slice(-800))), 30000);
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      const match = buffer.match(/^(http:\/\/127\.0\.0\.1:\d+)$/m);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error("server exited " + code + ": " + serverErr.slice(-800))); });
  });
  server = { child, baseUrl, boundary: new SyntheticBrowserBoundary(baseUrl) };
  return server;
}
async function stopServer() {
  if (!server) return;
  const { child, boundary } = server;
  server = null;
  boundary.assertClean();
  if (child.exitCode === null) {
    const exited = new Promise((resolve) => child.once("exit", resolve));
    child.kill();
    await exited;
  }
}
async function noisePng(page, width, height) {
  const base64 = await page.evaluate(({ width, height }) => {
    const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d"); const image = context.createImageData(width, height);
    let seed = 7; for (let i = 0; i < image.data.length; i++) { seed = (seed * 1103515245 + 12345) & 0x7fffffff; image.data[i] = i % 4 === 3 ? 255 : seed >> 23; }
    context.putImageData(image, 0, 0); return canvas.toDataURL("image/png").split(",")[1];
  }, { width, height });
  return Buffer.from(base64, "base64");
}
const pageErrors = [];
async function signedInPage() {
  const context = await server.boundary.newContext(browser, { viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  const page = await context.newPage();
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await signIn(page, server.baseUrl);
  return page;
}
const visibleRows = (page) => page.evaluate(() => [...document.querySelectorAll("#pricingMatchesBody tr")]
  .filter((row) => row.offsetParent).map((row) => row.innerText.replace(/\s+/g, " ").trim()));
// Staff reopen the saved quote from the dashboard in a fresh browser, as on another computer.
async function reopen(sessionId) {
  const page = await signedInPage();
  const card = page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`);
  await card.waitFor({ state: "visible", timeout: 20000 });
  await card.click();
  await page.locator('#dashboardSelectedSessionPanel [data-dashboard-panel-action="modify-session"]').click();
  await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 30000 });
  await page.waitForFunction(() => !state.isBooting && !state.quoteSessionRestoreBusy && !appIsBusy(), null, { timeout: 30000 });
  const view = await page.evaluate(() => ({
    panel: state.activeSidePanel,
    outputVisible: Boolean(document.querySelector("#outputSidePanel")?.offsetParent),
    customerVisible: Boolean(document.querySelector("#customerDetailsPanel")?.offsetParent),
    savedRows: state.outputRows.length,
    sideNext: document.querySelector("#sideNextButton")?.offsetParent ? document.querySelector("#sideNextButton").innerText.trim() : "",
  }));
  view.rows = await visibleRows(page);
  return { page, view };
}
const recordPath = (sessionId) => path.join(dirs.data, "quote-sessions", sessionId, "quote-session.json");
// Autosave after a real edit: wait for the saved record that carries it.
async function editAndAwaitSave(page, edit, saved) {
  const response = page.waitForResponse((item) => {
    if (new URL(item.url()).pathname !== "/api/quote-sessions" || item.request().method() !== "POST" || item.status() !== 200) return false;
    try { return saved(item.request().postDataJSON()?.draft_state || {}); } catch { return false; }
  }, { timeout: 30000 });
  await edit();
  await response;
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 30000 });
}

const result = { runRoot };
try {
  await startServer();
  browser = await chromium.launch({ headless: true });

  // 1. Build and generate a quote in the real UI.
  let page = await signedInPage();
  await page.locator("#newQuoteButton").click();
  await page.locator("#imageIntake").waitFor({ state: "visible" });
  await page.locator("#imageInput").setInputFiles({ name: "older-quote-synthetic-render.png", mimeType: "image/png", buffer: await noisePng(page, 800, 600) });
  await page.locator("#fileList .file-item").first().waitFor({ state: "visible" });
  await page.locator("#sideNextButton").click();
  await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
  for (const [selector, value] of [["#clientNameEditor", customer], ["#clientAttentionEditor", "Synthetic Reviewer"], ["#clientTitleEditor", "Synthetic Lead"], ["#clientAddressEditor", "Synthetic address line 1\nSynthetic address line 2"], ["#projectTitleEditor", "Synthetic Older Quote"], ["#showName", show], ["#projectNumberEditor", "OQ-" + Date.now().toString(36).toUpperCase()]]) await page.locator(selector).fill(value);
  await page.locator("#quoteDate").fill(new Date().toISOString().slice(0, 10));
  await page.locator("#sideNextButton").click();
  await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
  const preset = await page.locator("#presetSelect option").evaluateAll((options, id) => options.map((o) => o.value).find((value) => value.startsWith("profile:" + id + ":")), profileId);
  await page.locator("#presetSelect").selectOption(preset);
  await page.locator("#loadPresetButton").click();
  await page.locator("#confirmProfileLoadButton").click();
  await page.locator("#headerLogoInput").setInputFiles({ name: "older-quote-synthetic-logo.png", mimeType: "image/png", buffer: await noisePng(page, 240, 96) });
  for (const [selector, value] of [["#headerDetailsEditor", "Synthetic Quote Company | Singapore"], ["#quoteCompanyNameEditor", "Synthetic Quote Company"], ["#companySignatoryEditor", "Synthetic Signatory"], ["#companyTitleEditor", "Synthetic Title"]]) await page.locator(selector).fill(value);
  await page.locator("#sideNextButton").click();
  await page.locator("#analysisConfirmStartButton").click();
  await page.locator("#quoteBasisPanel").waitFor({ state: "visible", timeout: 90000 });
  await page.waitForFunction(() => state.quoteBasisSections.flatMap((section) => section.lines || []).length > 0 && !appIsBusy(), null, { timeout: 90000 });
  // Staff tick every line still waiting for review, then confirm the basis.
  for (let round = 0; round < 12; round++) {
    const target = await page.evaluate(() => {
      for (const section of state.quoteBasisSections) {
        const index = (section.lines || []).findIndex((line, i) => normalizeBasisTag(line.tag) === "Confirm"
          && document.querySelector(`#basisReviewSurface .basis-line-tag-button[data-basis-section="${section.id}"][data-basis-line-index="${i}"]:is([data-basis-tag="Include"], [data-basis-tag="Custom"])`));
        if (index >= 0) return [section.id, index];
      }
      return null;
    });
    if (!target) break;
    await page.locator(`#basisReviewSurface .basis-line-tag-button[data-basis-section="${target[0]}"][data-basis-line-index="${target[1]}"]:is([data-basis-tag="Include"], [data-basis-tag="Custom"])`).first().click();
    await page.waitForFunction(() => !appIsBusy(), null, { timeout: 30000 });
  }
  assert(await page.evaluate(() => basisConfirmBlockReason()) === "", "the basis cannot be confirmed", await page.evaluate(() => basisConfirmBlockReason()));
  await page.locator("#sideNextButton").click();
  await page.locator("#pricingMatchesBody tr").first().waitFor({ state: "visible", timeout: 60000 });
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 60000 });
  const download = page.waitForEvent("download", { timeout: 120000 });
  await page.locator("#sideDownloadButton").click();
  assert(await download, "the Excel quote did not download");
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 120000 });
  await page.waitForLoadState("networkidle");
  const sessionId = await page.evaluate(() => state.quoteSessionId);
  result.generatedRows = await visibleRows(page);
  assert(result.generatedRows.length >= 2, "the generated quote shows no pricing rows", result.generatedRows);
  await page.context().close();

  // 2. Control: the quote as saved by this code reopens at Output.
  let reopened = await reopen(sessionId);
  result.currentShape = reopened.view;
  assert(reopened.view.panel === "output" && reopened.view.outputVisible, "a current quote did not reopen at Output", reopened.view);
  await reopened.page.context().close();
  await stopServer();

  // 3. The same quote saved before the Known details box existed: its Customer step and details
  // carry no known_details. The app restarts on that record.
  const file = recordPath(sessionId);
  const record = JSON.parse(await fs.readFile(file, "utf8"));
  const draft = record.draft_state;
  assert(draft?.customerStepCheckpoint?.customer?.project?.known_details === "" && draft?.quoteDetails?.project?.known_details === "",
    "the saved quote does not carry the empty Known details field", draft?.customerStepCheckpoint);
  delete draft.customerStepCheckpoint.customer.project.known_details;
  delete draft.quoteDetails.project.known_details;
  await fs.writeFile(file, JSON.stringify(record, null, 2));
  await startServer();
  reopened = await reopen(sessionId);
  result.olderShape = reopened.view;
  assert(reopened.view.panel === "output" && reopened.view.outputVisible && !reopened.view.customerVisible,
    "an older quote did not reopen at Output", reopened.view);
  assert(JSON.stringify(reopened.view.rows) === JSON.stringify(result.generatedRows), "the older quote's pricing rows are not shown as generated", reopened.view.rows);
  assert(await reopened.page.locator("#sideDownloadButton").isEnabled(), "the older quote cannot download its Excel quote");
  const reopenedRecord = JSON.parse(await fs.readFile(file, "utf8"));
  assert(!Object.prototype.hasOwnProperty.call(reopenedRecord.draft_state.customerStepCheckpoint.customer.project, "known_details"),
    "reopening rewrote the older saved Customer step");

  // 4. A real Customer change on the older quote (its show name), saved without Next: the reopen goes back to Customer.
  page = reopened.page;
  await page.locator('button[data-side-panel="customer"]').click();
  await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
  await editAndAwaitSave(page, () => page.locator("#showName").fill(show + " Changed"),
    (saved) => saved.quoteDetails?.project?.show_name === show + " Changed");
  await page.context().close();
  reopened = await reopen(sessionId);
  result.changedCustomer = reopened.view;
  assert(reopened.view.panel === "customer" && reopened.view.customerVisible && !reopened.view.outputVisible && !reopened.view.rows.length,
    "a changed Customer step did not reopen at Customer details", reopened.view);
  assert(reopened.view.sideNext === "Next: Quote Company", "the changed Customer step does not ask for Next", reopened.view.sideNext);

  // 5. Staff put the show name back, then type Known details on the older quote: still back to Customer.
  page = reopened.page;
  await editAndAwaitSave(page, () => page.locator("#showName").fill(show),
    (saved) => saved.quoteDetails?.project?.show_name === show);
  await page.locator('button[data-side-panel="images"]').click();
  await editAndAwaitSave(page, () => page.locator("#knownDetails").fill("Booth is 93 sqm"),
    (saved) => saved.quoteDetails?.project?.known_details === "Booth is 93 sqm");
  await page.context().close();
  reopened = await reopen(sessionId);
  result.typedKnownDetails = reopened.view;
  assert(reopened.view.panel === "customer" && !reopened.view.outputVisible, "Known details typed on an older quote did not reopen at Customer details", reopened.view);

  // 6. Staff clear the box again: the older quote is back as saved and reopens at Output.
  page = reopened.page;
  await page.locator('button[data-side-panel="images"]').click();
  await editAndAwaitSave(page, () => page.locator("#knownDetails").fill(""),
    (saved) => saved.quoteDetails?.project?.known_details === "");
  await page.context().close();
  reopened = await reopen(sessionId);
  result.clearedKnownDetails = reopened.view;
  assert(reopened.view.panel === "output" && reopened.view.outputVisible && reopened.view.rows.length === result.generatedRows.length,
    "the unchanged older quote did not reopen at Output", reopened.view);
  await reopened.page.context().close();

  assert(!pageErrors.length, "page errors", pageErrors);
  await stopServer();
  result.status = "PASS";
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  console.error(error?.stack || String(error));
  console.error(serverErr.slice(-2000));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  if (server) server.child.kill();
}
