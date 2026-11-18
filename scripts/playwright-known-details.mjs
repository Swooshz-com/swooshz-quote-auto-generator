// Known details box (#385): type a booth size before analysis, check the request, the Detected booth line,
// the AI resize note, save/reload restore and re-send. Synthetic only: the owner-UAT mock harness replaces
// the AI provider with a deterministic reply (6m x 6m, 36 sqm carpet); no network, live data or paid AI.
// --screenshots-only captures the Reference uploads panel at 390 px and 1280 px and exits (also on builds
// without the box, for before/after evidence).
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
const sourceRoot = process.env.SQAG_KNOWN_DETAILS_SOURCE_ROOT || fileURLToPath(new URL("..", import.meta.url));
const { chromium } = createRequire(path.join(fileURLToPath(new URL("..", import.meta.url)), "package.json"))("playwright");
const screenshotsOnly = process.argv.includes("--screenshots-only");
const label = (process.argv.find((arg) => arg.startsWith("--label=")) || "--label=after").split("=")[1];
const evidenceRoot = process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(sourceRoot, "_logs/browser/known-details");
const runRoot = await freshBrowserRun(evidenceRoot, "run-");
const dirs = Object.fromEntries(["data", "output", "temp", "pricing", "profiles", "logs"].map((name) => [name, path.join(runRoot, name)]));
await Promise.all(Object.values(dirs).map((dir) => fs.mkdir(dir, { recursive: true })));
const assert = (ok, message, detail) => { if (!ok) throw new Error(message + (detail === undefined ? "" : ": " + JSON.stringify(detail))); };
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const fixtureId = "synthetic-exhibition-fixture-pricing";
const profileId = "synthetic-exhibition-fixture-template";
const carpet = "synthetic-floors-synthetic-carpet-tile";
const knownDetails = "Booth is 93 sqm, 2 open sides\nMax height 4 m";

// The fake AI ignores the staff size: 6m x 6m and a 36 sqm full-floor carpet line.
const scenarioPath = path.join(runRoot, "scenario.json");
await fs.writeFile(scenarioPath, JSON.stringify({ raw: {
  analysis_findings: [], blocking_clarification_questions: [],
  project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: [{ id: "synthetic-floors", title: "Synthetic Floors", lines: [
    { id: "kd-carpet", tag: "Confirm", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", confidence_pct: 90, pricing_keyword: carpet },
  ] }],
  line_items: [{ source_basis_line_id: "kd-carpet", section: "Synthetic Floors", description: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", pricing_keyword: carpet }],
} }, null, 2));
const fixtureDir = path.join(dirs.pricing, fixtureId);
await fs.cp(path.join(sourceRoot, "tests/fixtures/quote-generator/pricing-references", fixtureId), fixtureDir, { recursive: true });
const canonicalize = spawnSync(python, ["-c", "import json,sys; sys.path.insert(0, sys.argv[1]); from webapp import server as w; p=sys.argv[2]; d=json.load(open(p, encoding='utf-8')); open(p, 'w', encoding='utf-8').write(json.dumps(w.pricing_reference_catalog_payload(d), indent=2))", sourceRoot, path.join(fixtureDir, "pricing-catalog.json")], { cwd: sourceRoot, windowsHide: true, encoding: "utf8", env: { ...process.env, SQAG_DISABLE_DOTENV: "1" } });
if (canonicalize.status !== 0) throw new Error("catalogue canonicalisation failed: " + canonicalize.stderr);
await fs.cp(path.join(sourceRoot, "tests/fixtures/quote-generator/profiles", profileId), path.join(dirs.profiles, profileId), { recursive: true });

let server, browser, serverErr = "";
function startServer() {
  const child = spawn(python, [path.join(sourceRoot, "tests/owner_uat_mock_harness.py"), sourceRoot, dirs.data, dirs.output, dirs.temp, dirs.logs, dirs.pricing, dirs.profiles, scenarioPath], {
    cwd: sourceRoot, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, OPENAI_API_KEY: "", DEEPSEEK_API_KEY: "", SQAG_DISABLE_DOTENV: "1", PYTHONUNBUFFERED: "1" },
  });
  child.stderr.on("data", (chunk) => { serverErr = (serverErr + chunk).slice(-100000); });
  const ready = new Promise((resolve, reject) => {
    let buffer = "";
    const timer = setTimeout(() => reject(new Error("server not ready: " + serverErr.slice(-800))), 30000);
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      const match = buffer.match(/^(http:\/\/127\.0\.0\.1:\d+)$/m);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error("server exited " + code + ": " + serverErr.slice(-800))); });
  });
  return { child, ready };
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

const result = { runRoot, label };
try {
  server = startServer();
  const baseUrl = await server.ready;
  browser = await chromium.launch({ headless: true });
  const boundary = new SyntheticBrowserBoundary(baseUrl);
  const context = await boundary.newContext(browser, { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
  const page = await context.newPage();
  const pageErrors = [];
  const draftBodies = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/jobs" && request.method() === "POST" && request.postDataJSON()?.type === "draft") {
      draftBodies.push(request.postDataJSON().payload?.project || {});
    }
  });
  await signIn(page, baseUrl);
  await page.locator("#newQuoteButton").click();
  await page.locator("#imageIntake").waitFor({ state: "visible" });
  await page.locator("#imageInput").setInputFiles({ name: "known-details-synthetic-render.png", mimeType: "image/png", buffer: await noisePng(page, 800, 600) });
  await page.locator("#fileList .file-item").first().waitFor({ state: "visible" });
  const box = page.locator("#knownDetails");
  if (await box.count()) await box.fill(knownDetails);
  const shots = [];
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.locator("#imageIntake").scrollIntoViewIfNeeded();
    const file = path.join(runRoot, `intake-${label}-${width}.png`);
    await page.locator("#imageIntake").screenshot({ path: file });
    shots.push(file);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    assert(!overflow || label === "before", "intake panel scrolls sideways", width);
  }
  result.screenshots = shots;
  await page.setViewportSize({ width: 1280, height: 900 });
  if (screenshotsOnly) {
    result.status = "PASS";
  } else {
    assert(await box.count() === 1, "Known details box missing");
    assert(await box.getAttribute("maxlength") === "2000", "Known details box has no 2,000 character limit");
    result.boxLabel = (await page.locator("label[for=knownDetails]").innerText()).split("\n")[0].trim();
    await page.locator("#sideNextButton").click();
    await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
    for (const [selector, value] of [["#clientNameEditor", "Synthetic Known Details Customer"], ["#clientAttentionEditor", "Synthetic Reviewer"], ["#clientTitleEditor", "Synthetic Lead"], ["#clientAddressEditor", "Synthetic address"], ["#projectTitleEditor", "Synthetic Known Details"], ["#showName", "Synthetic Show"], ["#projectNumberEditor", "KD-" + Date.now().toString(36).toUpperCase()]]) await page.locator(selector).fill(value);
    await page.locator("#quoteDate").fill(new Date().toISOString().slice(0, 10));
    await page.locator("#sideNextButton").click();
    await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
    const preset = await page.locator("#presetSelect option").evaluateAll((options, id) => options.map((o) => o.value).find((value) => value.startsWith("profile:" + id + ":")), profileId);
    await page.locator("#presetSelect").selectOption(preset);
    await page.locator("#loadPresetButton").click();
    await page.locator("#confirmProfileLoadButton").click();
    await page.locator("#headerLogoInput").setInputFiles({ name: "known-details-synthetic-logo.png", mimeType: "image/png", buffer: await noisePng(page, 240, 96) });
    for (const [selector, value] of [["#headerDetailsEditor", "Synthetic Quote Company | Singapore"], ["#quoteCompanyNameEditor", "Synthetic Quote Company"], ["#companySignatoryEditor", "Synthetic Signatory"], ["#companyTitleEditor", "Synthetic Title"]]) await page.locator(selector).fill(value);
    await page.locator("#sideNextButton").click();
    await page.locator("#analysisConfirmStartButton").click();
    await page.locator("#quoteBasisPanel").waitFor({ state: "visible", timeout: 90000 });
    await page.waitForFunction(() => state.quoteBasisSections.flatMap((section) => section.lines || []).length > 0 && !appIsBusy(), null, { timeout: 90000 });

    assert(draftBodies.length === 1 && draftBodies[0].known_details === knownDetails, "analysis request lacks the Known details text", draftBodies);
    const booth = page.locator("#basisReviewSurface .basis-visual-display");
    result.detectedBooth = (await booth.allInnerTexts()).join(" | ");
    assert(result.detectedBooth.includes("Booth size 93 sqm (from Known details)"), "Detected booth does not show the Known details size", result.detectedBooth);
    const draft = await page.evaluate((keyword) => ({
      booth: state.boothDimensions,
      carpet: state.lineItems.find((row) => row.pricing_keyword === keyword)?.quantity,
      lines: state.quoteBasisSections.flatMap((section) => (section.lines || []).map((line) => line.text)),
    }), carpet);
    assert(draft.booth.dimension_source === "staff" && draft.booth.booth_area_sqm === "93", "state booth size is not the staff size", draft.booth);
    assert(Number(draft.carpet) === 93, "full-floor carpet line was not resized to 93 sqm", draft.carpet);
    assert(!draft.lines.some((text) => /defaults to 6m x 6m/.test(text)), "default 6m x 6m reminder shown", draft.lines);
    result.resizeNote = draft.lines.find((text) => text.includes("but the AI analysis used 36 sqm")) || "";
    assert(result.resizeNote, "resize note missing from the quote basis", draft.lines);
    result.carpetQuantity = Number(draft.carpet);
    await page.locator("#basisReviewSurface").screenshot({ path: path.join(runRoot, "basis-detected-booth-1280.png") });

    // Save, reload: the box text (with its line break) comes back, and analysis sends it again.
    await page.evaluate(async () => { await saveQuoteSessionDraftState({ quoteGenerated: false }); });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.quoteBasisSections.length > 0 && !appIsBusy(), null, { timeout: 60000 });
    result.restoredText = await page.locator("#knownDetails").inputValue();
    assert(result.restoredText === knownDetails, "Known details text was not restored", result.restoredText);
    result.restoredBooth = await page.evaluate(() => state.boothDimensions);
    assert(result.restoredBooth.dimension_source === "staff", "restored booth size lost its source", result.restoredBooth);
    if (!(await page.locator("#quoteBasisPanel").isVisible())) await page.locator('button[data-side-panel="basis"]').click();
    await page.locator("#analyseAgainButton").click();
    const confirm = page.locator("#analysisConfirmStartButton");
    if (await confirm.isVisible({ timeout: 5000 }).catch(() => false)) await confirm.click();
    const deadline = Date.now() + 90000;
    while (draftBodies.length < 2 && Date.now() < deadline) await page.waitForTimeout(250);
    await page.waitForFunction(() => !appIsBusy(), null, { timeout: 90000 });
    assert(draftBodies.length === 2 && draftBodies[1].known_details === knownDetails, "re-analysis did not send the Known details text again", draftBodies.length);
    assert(!pageErrors.length, "page errors", pageErrors);
    boundary.assertClean();
    result.status = "PASS";
  }
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  console.error(error?.stack || String(error));
  console.error(serverErr.slice(-2000));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  if (server) server.child.kill();
}
