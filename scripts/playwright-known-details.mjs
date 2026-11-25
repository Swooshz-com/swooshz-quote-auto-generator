// Known details box (#385): type a booth size before analysis, check the request, the size proposal
// (Apply and Discard), the Detected booth line, the resize note, save/reload restore and re-send. Synthetic only: the owner-UAT mock harness replaces
// the AI provider with a deterministic reply (staff size 93 sqm read, booth 6m x 6m, 36 sqm carpet); no network, live data or paid AI.
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
const graphic = "synthetic-graphics-synthetic-printed-wall-graphic";
const knownDetails = "Booth is 93 sqm, 2 open sides\nMax height 4 m";

// The fake AI reads the staff size (project.staff_booth_size) but sizes the booth 6m x 6m from the
// references with a 36 sqm full-floor carpet line and a 12 sqm wall graphic; staff Apply or Discard
// the Known details size, after ticking or editing other lines first.
const scenarioPath = path.join(runRoot, "scenario.json");
await fs.writeFile(scenarioPath, JSON.stringify({ raw: {
  analysis_findings: [], blocking_clarification_questions: [],
  project: { booth_width: 6, booth_depth: 6, staff_booth_size: { booth_width: null, booth_depth: null, booth_area_sqm: 93, stated: "clear" } },
  quote_basis_sections: [{ id: "synthetic-floors", title: "Synthetic Floors", lines: [
    { id: "kd-carpet", tag: "Confirm", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", confidence_pct: 90, pricing_keyword: carpet },
    { id: "kd-graphic", tag: "Confirm", text: "sqm synthetic printed wall graphic", quantity: 12, unit: "sqm", confidence_pct: 80, pricing_keyword: graphic },
    // The booth profile as an AI draft once offered it: a sized line with its own row (#334, #385).
    { id: "kd-profile", tag: "Confirm", text: "Provisional booth footprint 6m W x 6m D, totalling 36 sqm", quantity: 36, unit: "sqm", confidence_pct: 70, pricing_keyword: "" },
  ] }],
  line_items: [
    { source_basis_line_id: "kd-carpet", section: "Synthetic Floors", description: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", pricing_keyword: carpet },
    { source_basis_line_id: "kd-graphic", section: "Synthetic Floors", description: "sqm synthetic printed wall graphic", quantity: 12, unit: "sqm", pricing_keyword: graphic },
    { source_basis_line_id: "kd-profile", section: "Synthetic Floors", description: "Provisional booth footprint 6m W x 6m D, totalling 36 sqm", quantity: 36, unit: "sqm", pricing_keyword: "" },
  ],
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
    const card = page.locator("#basisReviewSurface [data-known-details-size-proposal]");
    const readDraft = () => page.evaluate((keyword) => ({
      booth: state.boothDimensions,
      sizeState: state.knownDetailsSize,
      carpet: state.lineItems.find((row) => row.pricing_keyword === keyword)?.quantity,
      lines: state.quoteBasisSections.flatMap((section) => (section.lines || []).map((line) => line.text)),
      graphic: (() => {
        const line = state.quoteBasisSections.flatMap((section) => section.lines || []).find((entry) => entry.id === "kd-graphic");
        const row = state.lineItems.find((entry) => entry.source_basis_line_id === "kd-graphic");
        return { tag: normalizeBasisTag(line?.tag), line: Number(line?.quantity), row: Number(row?.quantity) };
      })(),
      standard: STANDARD_SERVICE_ROWS.map((row) => ({
        line: state.quoteBasisSections.some((section) => (section.lines || []).some((line) => line.id === row.id)),
        row: state.lineItems.some((item) => item.source_basis_line_id === row.id),
      })),
    }), carpet);
    const graphicControl = async (selector) => {
      const target = await page.evaluate(() => {
        for (const section of state.quoteBasisSections) {
          const index = (section.lines || []).findIndex((line) => line.id === "kd-graphic");
          if (index >= 0) return [section.id, index];
        }
        return null;
      });
      assert(target, "graphic line missing");
      return page.locator(`#basisReviewSurface ${selector}[data-basis-section="${target[0]}"][data-basis-line-index="${target[1]}"]`).first();
    };
    // Apply keeps staff edits, resizes the full-floor line and keeps the standard service rows (G2 round 3).
    const assertApplied = (draft, size, graphicLine) => {
      assert(draft.booth.dimension_source === "staff" && draft.booth.booth_area_sqm === String(size), "state booth size is not the staff size", draft.booth);
      assert(Number(draft.carpet) === size, `full-floor carpet line was not resized to ${size} sqm`, draft.carpet);
      assert(JSON.stringify(draft.graphic) === JSON.stringify(graphicLine), "Apply lost the staff edit on another line", draft.graphic);
      assert(draft.standard.every((entry) => entry.line && entry.row), "Apply dropped the standard service rows", draft.standard);
      assert(!draft.lines.some((text) => text.includes("defaults to 6m x 6m")), "default-size note left beside the staff size", draft.lines);
      assert(draft.lines.some((text) => text.includes(`Known details is ${size} sqm, but the AI analysis used 36 sqm`)), "resize note missing from the quote basis", draft.lines);
      assert(draft.sizeState.decision?.decision === "applied" && draft.sizeState.decision?.booth_size === `${size} sqm`, "Apply was not recorded", draft.sizeState);
    };
    // As staff would: edit the box in References, save the Customer step again, return to the basis.
    const editKnownDetails = async (text, area) => {
      await page.locator('button[data-side-panel="images"]').click();
      await page.locator("#knownDetails").fill(text);
      await page.locator('button[data-side-panel="customer"]').click();
      await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
      await page.locator("#sideNextButton").click();
      await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
      await page.locator('button[data-side-panel="basis"]').click();
      await page.locator("#quoteBasisPanel").waitFor({ state: "visible" });
      const scenario = JSON.parse(await fs.readFile(scenarioPath, "utf8"));
      scenario.raw.project.staff_booth_size.booth_area_sqm = area;
      await fs.writeFile(scenarioPath, JSON.stringify(scenario, null, 2));
    };
    const reload = async () => {
      await page.evaluate(async () => { await saveQuoteSessionDraftState({ quoteGenerated: false }); });
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.quoteBasisSections.length > 0 && !appIsBusy(), null, { timeout: 60000 });
      if (!(await page.locator("#quoteBasisPanel").isVisible())) await page.locator('button[data-side-panel="basis"]').click();
    };
    const analyseAgain = async () => {
      const before = draftBodies.length;
      await page.locator("#analyseAgainButton").click();
      const confirm = page.locator("#analysisConfirmStartButton");
      if (await confirm.isVisible({ timeout: 5000 }).catch(() => false)) await confirm.click();
      const deadline = Date.now() + 90000;
      while (draftBodies.length === before && Date.now() < deadline) await page.waitForTimeout(250);
      await page.waitForFunction(() => !appIsBusy(), null, { timeout: 90000 });
      assert(draftBodies.length === before + 1, "analysis did not run again", draftBodies.length);
      return draftBodies[draftBodies.length - 1];
    };

    // The booth profile is under Detected booth, never a quote line: no row, no tick or X, nothing in Output.
    const profileOffered = async () => ({
      quoteLine: (await page.locator("#basisReviewSurface .basis-line-row").allInnerTexts()).some((text) => text.includes("Provisional booth footprint")),
      row: await page.evaluate(() => state.lineItems.some((row) => row.source_basis_line_id === "kd-profile")),
      detected: (await booth.allInnerTexts()).join(" "),
    });
    const assertProfile = async (stage, sizeText) => {
      const profile = await profileOffered();
      assert(!profile.quoteLine && !profile.row, "the booth profile is offered as a quote line after " + stage, profile);
      assert(profile.detected.includes(sizeText), "Detected booth does not state " + sizeText + " after " + stage, profile.detected);
      return profile.detected;
    };
    await assertProfile("analysis", "Provisional booth footprint 6m W x 6m D, totalling 36 sqm");

    // 1. The AI's reading is offered with the Re proposal controls; nothing applies yet.
    await card.waitFor({ state: "visible" });
    result.proposal = (await card.innerText()).replace(/\s+/g, " ").trim();
    assert(result.proposal.includes("Booth size from Known details: 93 sqm"), "proposal does not show the Known details size", result.proposal);
    let draft = await readDraft();
    assert(draft.booth.dimension_source !== "staff" && Number(draft.carpet) === 36, "the Known details size applied before Apply", draft);
    assert(!(await booth.allInnerTexts()).join(" ").includes("(from Known details)"), "Detected booth claims Known details before Apply");
    await page.locator("#basisReviewSurface").screenshot({ path: path.join(runRoot, "basis-known-details-proposal-1280.png") });

    // 2. A pending proposal survives save and reopen.
    await reload();
    assert(await card.isVisible(), "proposal lost on reopen");
    result.restoredText = await page.locator("#knownDetails").inputValue();
    assert(result.restoredText === knownDetails, "Known details text was not restored", result.restoredText);

    // 3. Apply after staff tick another line: the staff size wins, the full-floor line is resized with
    // the Confirm note, the tick and the standard service rows stay, and the default-size note goes.
    draft = await readDraft();
    assert(draft.standard.every((entry) => entry.line && entry.row) && draft.lines.some((text) => text.includes("defaults to 6m x 6m")),
      "analysis did not show the standard service rows and the default-size note", draft);
    await (await graphicControl('[data-basis-tag="Include"]')).click();
    await page.waitForFunction(() => !appIsBusy(), null, { timeout: 30000 });
    assert((await readDraft()).graphic.tag === "Include", "tick did not apply");
    await page.locator('#basisReviewSurface [data-known-details-size="apply"]').click();
    await card.waitFor({ state: "detached" });
    result.detectedBooth = (await booth.allInnerTexts()).join(" | ");
    assert(result.detectedBooth.includes("Booth size 93 sqm (from Known details)") && !result.detectedBooth.includes("defaults to"),
      "Detected booth does not show only the Known details size", result.detectedBooth);
    // No line contradicts the applied size.
    assert(!(await assertProfile("Apply", "Provisional booth footprint 93 sqm, totalling 93 sqm")).includes("6m W x 6m D"), "a line contradicts the applied size");
    assert(!(await page.locator("#basisReviewSurface").innerText()).includes("6m W x 6m D"), "Quote Basis still shows the size that was replaced");
    draft = await readDraft();
    assertApplied(draft, 93, { tag: "Include", line: 12, row: 12 });
    result.resizeNote = draft.lines.find((text) => text.includes("but the AI analysis used 36 sqm"));
    result.carpetQuantity = Number(draft.carpet);
    await page.locator("#basisReviewSurface").screenshot({ path: path.join(runRoot, "basis-detected-booth-1280.png") });

    // 4. Reopen and Re-Analyse: the applied size holds and is not offered again.
    await reload();
    result.restoredBooth = await page.evaluate(() => state.boothDimensions);
    assert(result.restoredBooth.dimension_source === "staff", "restored booth size lost its source", result.restoredBooth);
    let body = await analyseAgain();
    assert(body.known_details === knownDetails && body.known_details_size_decision?.decision === "applied", "re-analysis did not send the Known details text and decision", body);
    draft = await readDraft();
    assert(!(await card.count()) && draft.booth.dimension_source === "staff" && Number(draft.carpet) === 93, "applied size not kept after Re-Analyse", draft);
    result.reanalysedBooth = await assertProfile("Re-Analyse", "Provisional booth footprint 93 sqm, totalling 93 sqm");

    // 5. Staff type a new size: it is offered again. Apply after a quantity edit on another line.
    await editKnownDetails("Booth is 120 sqm, 2 open sides\nMax height 4 m", 120);
    body = await analyseAgain();
    assert(body.known_details.startsWith("Booth is 120 sqm"), "re-analysis did not send the new text", body);
    await card.waitFor({ state: "visible" });
    assert((await card.innerText()).includes("Booth size from Known details: 120 sqm"), "changed size was not offered again");
    // The card names where the current size came from (G2 round 3 L3).
    const currentLabels = await page.evaluate(() => {
      const saved = state.boothDimensions;
      const label = (dimensions) => {
        state.boothDimensions = dimensions;
        return document.createRange().createContextualFragment(knownDetailsSizeProposalHtml()).querySelector(".basis-chat-compare-card p").textContent;
      };
      try {
        return {
          references: label({ ...saved, booth_size: "9m x 10m", dimension_source: "analysis" }),
          staff: label({ ...saved, booth_size: "93 sqm", dimension_source: "staff" }),
        };
      } finally {
        state.boothDimensions = saved;
      }
    });
    assert(currentLabels.references === "9m x 10m (from the references)" && currentLabels.staff === "93 sqm (from Known details)",
      "the card mislabels where the current size came from", currentLabels);
    result.currentLabels = currentLabels;
    await (await graphicControl("[data-basis-quantity-edit]")).click();
    const quantityInput = page.locator("#basisReviewSurface .basis-quantity-text input").first();
    await quantityInput.fill("15");
    await quantityInput.press("Enter");
    await page.waitForFunction(() => !appIsBusy(), null, { timeout: 30000 });
    draft = await readDraft();
    assert(draft.graphic.line === 15 && Number(draft.carpet) === 36, "quantity edit did not apply", draft.graphic);
    await page.locator('#basisReviewSurface [data-known-details-size="apply"]').click();
    await card.waitFor({ state: "detached" });
    draft = await readDraft();
    assertApplied(draft, 120, { tag: draft.graphic.tag, line: 15, row: draft.graphic.row });
    assert(draft.graphic.row === 15, "the edited quantity did not reach the pricing row", draft.graphic);
    result.applyAfterEdit = { carpet: Number(draft.carpet), graphic: draft.graphic };

    // 6. Another new size; Discard keeps the drawing size.
    const newDetails = "Booth is 150 sqm, 2 open sides\nMax height 4 m";
    await editKnownDetails(newDetails, 150);
    body = await analyseAgain();
    assert(body.known_details === newDetails, "re-analysis did not send the new text", body);
    await card.waitFor({ state: "visible" });
    assert((await card.innerText()).includes("Booth size from Known details: 150 sqm"), "changed size was not offered again");
    await page.locator('#basisReviewSurface [data-known-details-size="discard"]').click();
    await card.waitFor({ state: "detached" });
    draft = await readDraft();
    assert(draft.booth.dimension_source !== "staff" && Number(draft.carpet) === 36, "Discard did not keep the drawing size", draft);
    assert(draft.sizeState.decision?.decision === "discarded" && draft.sizeState.decision?.booth_size === "150 sqm", "Discard was not recorded", draft.sizeState);

    // 7. The Discard survives reopen and Re-Analyse of the same size.
    await reload();
    assert(!(await card.count()), "discarded proposal came back on reopen");
    body = await analyseAgain();
    assert(body.known_details_size_decision?.decision === "discarded", "re-analysis did not send the Discard", body);
    assert(!(await card.count()), "discarded size was offered again");
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
