import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

for (const key of Object.keys(process.env)) {
  if (/(API_KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)) process.env[key] = "";
}
// Synthetic-only owner-UAT U1/U2 regression: production server, UI, normalisation and XLSX code;
// only the AI provider is replaced by a deterministic caller-written draft (no network, no paid AI).
const sourceRoot = fileURLToPath(new URL("..", import.meta.url));
const { chromium, request } = createRequire(path.join(sourceRoot, "package.json"))("playwright");
const args = process.argv.slice(2);
const measureOnly = args.includes("--measure-only");
const fillerLines = Number((args.find((arg) => arg.startsWith("--lines=")) || "--lines=60").split("=")[1]);
const latencyMs = Number((args.find((arg) => arg.startsWith("--latency-ms=")) || "--latency-ms=1500").split("=")[1]);
const cpuProfile = args.includes("--cpu-profile");
// m2: start a draft autosave that is still in flight (delayed by --latency-ms) when Confirm is clicked.
const inflightSave = args.includes("--inflight-save");
const freshAnalysis = args.includes("--fresh-analysis");
const cpuThrottle = Number((args.find((arg) => arg.startsWith("--cpu-throttle=")) || "--cpu-throttle=1").split("=")[1]);
const profiledFunctions = (args.find((arg) => arg.startsWith("--profile=")) || "--profile=").split("=")[1].split(",").filter(Boolean);
const extraCatalogItems = Number((args.find((arg) => arg.startsWith("--catalog-extra=")) || "--catalog-extra=0").split("=")[1]);
const evidenceRoot = process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(sourceRoot, "_logs/browser/ouat");
const runRoot = path.join(evidenceRoot, Date.now().toString(36));
const dirs = Object.fromEntries(["data", "output", "temp", "pricing", "profiles", "logs", "downloads"].map((name) => [name, path.join(runRoot, name)]));
await Promise.all(Object.values(dirs).map((dir) => fs.mkdir(dir, { recursive: true })));
const writeJson = (file, value) => fs.writeFile(path.join(runRoot, file), JSON.stringify(value, null, 2) + "\n");
const assert = (ok, message, detail) => { if (!ok) throw new Error(message + (detail === undefined ? "" : ": " + JSON.stringify(detail))); };
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const fixtureId = "synthetic-exhibition-fixture-pricing";
const profileId = "synthetic-exhibition-fixture-template";
const quoteCurrency = (args.find((arg) => arg.startsWith("--currency=")) || "--currency=USD").split("=")[1];
const fxRate = quoteCurrency === "SGD" ? 1 : 0.7391;
const cents = (value) => Math.round((value + Number.EPSILON) * 100) / 100;

// Synthetic catalogue with deliberately similar entries; no shared prefix token across sections.
const slug = (value) => value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
function catalogItem(section, unit, name, price, extraFamilies = [], extraTerms = [], order = [0, 0]) {
  const prefix = { nos: "nos.", sqm: "sqm", lot: "lot", "m length": "m length" }[unit];
  const description = prefix + " " + name;
  const words = name.toLowerCase().match(/[a-z0-9]+/g);
  return {
    category_order: order[0], item_order: order[1], section, id: slug(section) + "-" + slug(name), description, unit_hint: unit,
    sale_unit_price: price, default_quote_amount: price, internal_cost: price, markup_multiplier: 1, gst_multiplier: 1, remarks: [],
    aliases: [description, name, section + " " + name],
    match_terms: [...new Set([name.toLowerCase(), ...words, ...extraTerms])], object_families: [slug(name).replace(/-/g, "_"), ...extraFamilies],
  };
}
const catalogSpec = [
  ["AV Equipment Rental Items", "nos", [["24 inch LED TV Monitor", 80.0, ["led_tv"], ["display", "screen", "video"]], ["43 inch LED TV Monitor", 132.35, ["led_tv"], ["display", "screen", "video"]], ["55 inch LED TV Monitor", 220.55, ["led_tv"], ["display", "screen", "video"]], ["65 inch LED TV Monitor", 309.9, ["led_tv"], ["display", "screen", "video"]], ["85 inch LED TV Monitor", 480.0, ["led_tv"], ["display", "screen", "video"]], ["Wireless Microphone Set", 64.5]]],
  ["Graphics and Printing", "sqm", [["Printed Wall Graphic", 21.15, ["print"]], ["Printed Wall Panel", 27.4], ["Printed Fabric Banner", 18.6], ["Printed Floor Sticker", 15.25]]],
  ["Flooring", "sqm", [["Grey Carpet Tile", 14.45], ["Blue Carpet Tile", 14.95], ["Needle Punch Carpet", 9.8], ["Vinyl Plank Flooring", 22.3]]],
  ["Lighting and Electrical", "nos", [["3 inch Spotlight", 11.7], ["6 inch Spotlight", 14.3], ["LED Flood Light", 26.0], ["13A Single Socket Point", 12.0], ["15A Power Point", 18.5]]],
  ["Furniture Rental", "nos", [["Cafe Chair", 6.05], ["Bar Stool", 9.5], ["Round Table", 15.0], ["Lockable Display Counter", 85.75]]],
];
const catalog = [];
catalogSpec.forEach(([section, unit, rows], sectionIndex) => rows.forEach(([name, price, families, terms], itemIndex) => catalog.push(catalogItem(section, unit, name, price, families || [], terms || [], [sectionIndex + 1, itemIndex + 1]))));
// Optional bulk entries model a production-sized catalogue without touching the owner cases.
for (let index = 0; index < extraCatalogItems; index++) {
  const [section, unit] = catalogSpec[index % catalogSpec.length];
  catalog.push(catalogItem(section, unit, "Reference Variant " + String.fromCharCode(65 + (index % 26)) + Math.floor(index / 26) + " Kit", 10 + (index % 50), [], [], [1 + (index % catalogSpec.length), 100 + index]));
}
// Imported catalogues can preserve a source section distinct from their canonical category.
catalog.find((item) => item.description.endsWith(" Bar Stool")).reference_section = "Seating and Hospitality";
const byName = (name) => catalog.find((item) => item.description.endsWith(" " + name));

// U1 owner cases: explicit selections the server records as pricing grants, a normal AI-confirmed row, and controls.
const cases = [
  // M3: 2 lot is not 2 pieces; the selection is refused for unit review and the row stays unpriced.
  { id: "u1-video", kind: "unit-review", section: "AV Equipment Rental Items", text: "Large wall-mounted video display for exterior feature wall", quantity: 2, unit: "lot", select: "55 inch LED TV Monitor" },
  { id: "u1-count", kind: "select", section: "Seating and Hospitality", text: "Bar Stool seating around the display", quantity: 4, unit: "pcs", select: "Bar Stool", proposal: true },
  { id: "u1-graphic", kind: "select", section: "Graphics and Printing", text: "Printed graphic on rear feature wall", quantity: 12.5, unit: "sqm", select: "Printed Wall Graphic" },
  { id: "u1-carpet", kind: "select", section: "Flooring", text: "Grey carpet tile flooring across the full booth footprint", quantity: 36, unit: "sqm", claim: "Printed Fabric Banner", select: "Grey Carpet Tile" },
  // Rows saved as HISTORICAL after a rejected AI claim. A fresh server-recorded selection on the reopened
  // quote prices; the other row gets no selection, so its old state is never promoted.
  { id: "u1-historical", kind: "historical", section: "Flooring", text: "Blue carpet tile flooring for the lounge zone", quantity: 18, unit: "sqm", claim: "Printed Floor Sticker", select: "Blue Carpet Tile" },
  { id: "u1-legacy", kind: "legacy", section: "Flooring", text: "Needle punch carpet for the storage corner", quantity: 6, unit: "sqm", claim: "Printed Wall Panel" },
  { id: "u1-confirmed", kind: "confirm", section: "Lighting and Electrical", text: "6 inch Spotlight", quantity: 7, unit: "nos", claim: "6 inch Spotlight" },
  { id: "u1-review", kind: "review", section: "Furniture Rental", text: "Custom reception desk with curved timber cladding", quantity: 1, unit: "lot" },
];
const fillerNouns = ["chair", "stool", "table", "counter", "spotlight", "socket point", "carpet tile", "wall graphic", "fabric banner", "floor sticker"];
for (let index = 0; index < fillerLines; index++) {
  const noun = fillerNouns[index % fillerNouns.length];
  cases.push({ id: "filler-" + (index + 1), kind: "filler", section: catalog.find((item) => item.description.toLowerCase().includes(noun)).section, text: "Additional " + noun + " for zone " + (index + 1), quantity: (index % 4) + 1, unit: noun.includes("carpet") || noun.includes("graphic") || noun.includes("banner") || noun.includes("sticker") ? "sqm" : "nos" });
}
const sections = new Map();
const lineItems = [];
for (const entry of cases) {
  const claimed = entry.claim ? byName(entry.claim) : null;
  const line = { id: entry.id, tag: entry.proposal ? "Custom" : "Confirm", ...(entry.proposal ? { custom_pricing: true } : {}), text: claimed && entry.kind === "confirm" ? claimed.description : entry.text, quantity: entry.quantity, unit: entry.unit, confidence_pct: 82 };
  if (claimed) line.pricing_keyword = claimed.id;
  if (!sections.has(entry.section)) sections.set(entry.section, { id: slug(entry.section), title: entry.section, lines: [] });
  sections.get(entry.section).lines.push(line);
  lineItems.push({ section: entry.section, source_basis_line_id: entry.id, description: line.text, quantity: entry.quantity, unit: entry.unit, ...(claimed ? { pricing_keyword: claimed.id } : {}) });
}
const scenarioPath = path.join(runRoot, "scenario.json");
await fs.writeFile(scenarioPath, JSON.stringify({ raw: { analysis_findings: [], blocking_clarification_questions: [], quote_basis_sections: [...sections.values()], line_items: lineItems } }, null, 2));
const fixtureDir = path.join(dirs.pricing, fixtureId);
await fs.cp(path.join(sourceRoot, "tests/fixtures/quote-generator/pricing-references", fixtureId), fixtureDir, { recursive: true });
await fs.writeFile(path.join(fixtureDir, "pricing-catalog.json"), JSON.stringify({ schema_version: 1, currency: "SGD", items: catalog }, null, 2));
// Store it in the canonical enriched form the product writes for saved references (server and generator digests agree).
const canonicalize = spawnSync(python, ["-c", "import json,sys; sys.path.insert(0, sys.argv[1]); from webapp import server as w; p=sys.argv[2]; d=json.load(open(p, encoding='utf-8')); open(p, 'w', encoding='utf-8').write(json.dumps(w.pricing_reference_catalog_payload(d), indent=2))", sourceRoot, path.join(fixtureDir, "pricing-catalog.json")], { cwd: sourceRoot, windowsHide: true, encoding: "utf8", env: { ...process.env, SQAG_DISABLE_DOTENV: "1" } });
if (canonicalize.status !== 0) throw new Error("catalogue canonicalisation failed: " + canonicalize.stderr);
for (const item of JSON.parse(await fs.readFile(path.join(fixtureDir, "pricing-catalog.json"), "utf8")).items) catalog[catalog.findIndex((entry) => entry.id === item.id)] = item;
await fs.cp(path.join(sourceRoot, "tests/fixtures/quote-generator/profiles", profileId), path.join(dirs.profiles, profileId), { recursive: true });

let server, browser, serverErr = "";
const network = [];
function startServer() {
  const child = spawn(python, [path.join(sourceRoot, "tests/alpha1_fresh_mock_harness.py"), sourceRoot, dirs.data, dirs.output, dirs.temp, dirs.logs, dirs.pricing, dirs.profiles, scenarioPath], {
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
// Locate a basis line control by its stable line id (sections may be regrouped by the server).
async function basisControl(page, lineId, selector) {
  const target = await page.evaluate((id) => {
    for (const section of state.quoteBasisSections) {
      const index = (section.lines || []).findIndex((line) => line.id === id);
      if (index >= 0) return { section: section.id, index };
    }
    return null;
  }, lineId);
  assert(target, "basis line missing", lineId);
  return page.locator('#basisReviewSurface [data-basis-section="' + target.section + '"][data-basis-line-index="' + target.index + '"]' + selector).first();
}
async function possibleMatchControl(page, lineId, catalogId) {
  const target = await page.evaluate(({ id, catalogId }) => {
    for (const section of state.quoteBasisSections) {
      const index = (section.lines || []).findIndex((line) => line.id === id);
      if (index < 0) continue;
      const matches = normalizePossiblePricingMatches(section.lines[index].possible_pricing_matches || []);
      return { section: section.id, index, match: matches.findIndex((match) => match.pricing_keyword === catalogId), offered: matches.map((match) => match.pricing_keyword) };
    }
    return null;
  }, { id: lineId, catalogId });
  assert(target && target.match >= 0, "selected catalogue entry is not an offered possible match", { lineId, catalogId, target });
  return page.locator('#basisReviewSurface [data-basis-section="' + target.section + '"][data-basis-line-index="' + target.index + '"][data-basis-possible-match-index="' + target.match + '"]').first();
}
// Click with a real pointer and read the browser's Event Timing record (input to next paint).
async function timedClick(page, locator, label, samples) {
  await locator.scrollIntoViewIfNeeded();
  await page.evaluate(() => { window.__ownerUatProfile = {}; window.__ownerUatEvents = []; });
  const started = Date.now();
  await locator.click();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0))));
  const wall = Date.now() - started;
  await page.waitForTimeout(120);
  const timing = await page.evaluate(() => ({ events: window.__ownerUatEvents, profile: window.__ownerUatProfile }));
  const click = timing.events.filter((entry) => ["pointerup", "click", "pointerdown", "mousedown", "mouseup"].includes(entry.name));
  samples.push({ label, requests: network.filter((entry) => entry.at >= started).map((entry) => entry.method + " " + entry.path), wallToPaintMs: wall, eventDurationMs: Math.max(0, ...click.map((entry) => entry.duration)), processingMs: Math.max(0, ...click.map((entry) => entry.processing)), profile: timing.profile });
}
const summarize = (samples) => {
  const out = {};
  for (const sample of samples) (out[sample.label] ||= []).push(sample);
  return Object.fromEntries(Object.entries(out).map(([label, rows]) => {
    const sorted = (key) => rows.map((row) => row[key]).sort((a, b) => a - b);
    const median = (key) => sorted(key)[Math.floor(rows.length / 2)];
    const profileKeys = [...new Set(rows.flatMap((row) => Object.keys(row.profile || {})))];
    return [label, { n: rows.length, medianEventDurationMs: median("eventDurationMs"), maxEventDurationMs: Math.max(...sorted("eventDurationMs")), medianProcessingMs: median("processingMs"), medianWallToPaintMs: median("wallToPaintMs"),
      medianProfileMs: Object.fromEntries(profileKeys.map((key) => [key, rows.map((row) => row.profile?.[key]?.ms || 0).sort((a, b) => a - b)[Math.floor(rows.length / 2)]])) }];
  }));
};

const result = { runRoot, fillerLines, latencyMs, cpuThrottle, inflightSave, freshAnalysis, catalogItems: catalog.length, cases: cases.length, selectionStatuses: [] };
const timings = [];
try {
  server = startServer();
  const baseUrl = await server.ready;
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  if (cpuThrottle > 1) await cdp.send("Emulation.setCPUThrottlingRate", { rate: cpuThrottle });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("response", (response) => {
    if (new URL(response.url()).pathname === "/api/quote-sessions/pricing-selections") result.selectionStatuses.push(response.status());
  });
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== baseUrl) return route.abort();
    // Model hosted round-trip latency on the endpoints the basis flow waits for.
    if (latencyMs > 0 && route.request().method() === "POST" && ["/api/line-items/normalize", "/api/quote-sessions", "/api/quote-sessions/pricing-selections"].includes(url.pathname)) {
      await new Promise((resolve) => setTimeout(resolve, latencyMs));
    }
    network.push({ at: Date.now(), method: route.request().method(), path: url.pathname });
    if (["/api/line-items/normalize", "/api/quote-sessions/pricing-selections"].includes(url.pathname)) await fs.writeFile(path.join(runRoot, url.pathname.split("/").pop() + "-request-" + network.length + ".json"), route.request().postData() || "");
    return route.continue();
  });
  await page.addInitScript(() => {
    window.__ownerUatEvents = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) window.__ownerUatEvents?.push({ name: entry.name, duration: entry.duration, processing: entry.processingEnd - entry.processingStart });
    }).observe({ type: "event", durationThreshold: 16, buffered: true });
  });
  await page.goto(baseUrl + "/login", { waitUntil: "domcontentloaded" });
  await page.waitForFunction(async () => (await fetch("/api/session").then((r) => r.json())).authenticated === true, null, { timeout: 30000 });
  const api = await request.newContext({ storageState: await context.storageState() });
  await page.locator("#newQuoteButton").click();
  await page.locator("#imageIntake").waitFor({ state: "visible" });
  const render = await noisePng(page, 1600, 1100);
  const logo = await noisePng(page, 240, 96);
  result.renderBytes = render.length;
  await page.locator("#imageInput").setInputFiles({ name: "owner-uat-synthetic-render.png", mimeType: "image/png", buffer: render });
  await page.locator("#fileList .file-item").first().waitFor({ state: "visible" });
  await page.locator("#sideNextButton").click();
  await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
  for (const [selector, value] of [["#clientNameEditor", "Synthetic UAT Customer"], ["#clientAttentionEditor", "Synthetic Reviewer"], ["#clientTitleEditor", "Synthetic Lead"], ["#clientAddressEditor", "Synthetic address"], ["#projectTitleEditor", "Synthetic Owner UAT"], ["#showName", "Synthetic Show"], ["#projectNumberEditor", "UAT-" + Date.now().toString(36).toUpperCase()]]) await page.locator(selector).fill(value);
  await page.locator("#quoteDate").fill(new Date().toISOString().slice(0, 10));
  if (quoteCurrency !== "SGD") {
    await page.locator("#quoteCurrency").selectOption(quoteCurrency);
    await page.locator("#quoteExchangeRate").fill(String(fxRate));
    await page.locator("#quoteExchangeRate").press("Tab");
  }
  await page.locator("#sideNextButton").click();
  await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
  const preset = await page.locator("#presetSelect option").evaluateAll((options, id) => options.map((o) => o.value).find((value) => value.startsWith("profile:" + id + ":")), profileId);
  await page.locator("#presetSelect").selectOption(preset);
  await page.locator("#loadPresetButton").click();
  await page.locator("#confirmProfileLoadButton").click();
  await page.locator("#headerLogoInput").setInputFiles({ name: "owner-uat-synthetic-logo.png", mimeType: "image/png", buffer: logo });
  for (const [selector, value] of [["#headerDetailsEditor", "Synthetic Quote Company | Singapore"], ["#quoteCompanyNameEditor", "Synthetic Quote Company"], ["#companySignatoryEditor", "Synthetic Signatory"], ["#companyTitleEditor", "Synthetic Title"]]) await page.locator(selector).fill(value);
  await page.locator("#sideNextButton").click();
  await page.locator("#analysisConfirmStartButton").click();
  await page.locator("#quoteBasisPanel").waitFor({ state: "visible", timeout: 90000 });
  await page.waitForFunction(() => state.quoteBasisSections.flatMap((section) => section.lines || []).length > 0 && !appIsBusy(), null, { timeout: 90000 });
  await page.waitForTimeout(500);

  if (!freshAnalysis) {
    // Save the quote holding HISTORICAL rows (an earlier AI claim on them was rejected), then reopen it.
    const historicalIds = cases.filter((entry) => ["historical", "legacy"].includes(entry.kind)).map((entry) => entry.id);
    const historicalSessionId = await page.evaluate(async (ids) => {
      state.lineItems = state.lineItems.map((item) => ids.includes(item.source_basis_line_id)
        ? normalizeLineItem({ ...item, pricing_authority: { ...item.pricing_authority, variant: "historical" } })
        : item);
      await saveQuoteSessionDraftState({});
      return state.quoteSessionId;
    }, historicalIds);
    const historicalShape = (draftState) => {
      const lines = (draftState?.quoteBasisSections || []).flatMap((section) => section.lines || []);
      return {
        variants: historicalIds.map((id) => (draftState?.lineItems || []).find((item) => item.source_basis_line_id === id)?.pricing_authority?.variant),
        offered: historicalIds.map((id) => (lines.find((line) => line.id === id)?.possible_pricing_matches || []).length),
        receiptFields: lines.flatMap((line) => line.possible_pricing_matches || []).filter((match) => "selection_receipt" in match).length,
      };
    };
    const savedDetail = (await (await api.get(baseUrl + "/api/quote-sessions/" + encodeURIComponent(historicalSessionId))).json()).quote_session;
    const savedShape = historicalShape(savedDetail?.draft_state);
    assert(savedShape.variants.every((variant) => variant === "historical") && savedShape.offered.every((count) => count > 0) && savedShape.receiptFields === 0, "saved HISTORICAL quote shape", savedShape);
    // The server ledger tracks the saved rows; the browser only echoes their revisions back.
    assert(historicalIds.every((id) => Number.isInteger(savedDetail?.pricing_ledger?.rows?.[id])), "saved quote has no server row revisions", savedDetail?.pricing_ledger);
    assert(!JSON.stringify(savedDetail).includes('"grant"'), "server pricing grants leaked to the client", savedDetail?.pricing_ledger);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.quoteBasisSections.length > 0 && !appIsBusy(), null, { timeout: 60000 });
    if (!(await page.locator("#quoteBasisPanel").isVisible())) await page.locator('button[data-side-panel="basis"]').click();
    await page.locator("#quoteBasisPanel").waitFor({ state: "visible", timeout: 30000 });
    const reopenedShape = await page.evaluate(() => ({ quoteSessionId: state.quoteSessionId, lifecycle: state.quoteCommercialLifecycle, lineItems: state.lineItems, quoteBasisSections: state.quoteBasisSections }));
    assert(reopenedShape.quoteSessionId === historicalSessionId && JSON.stringify(historicalShape(reopenedShape)) === JSON.stringify(savedShape), "reopened HISTORICAL quote shape", { saved: savedShape, reopened: historicalShape(reopenedShape), lifecycle: reopenedShape.lifecycle });
    result.historicalReopen = { lifecycle: reopenedShape.lifecycle, ...savedShape };
  }

  // Function-level attribution of each click (classic-script globals are rebindable).
  await page.evaluate((profiledNames) => {
    for (const name of ["updateQuoteBasisCard", "syncControlStates", "saveSessionState", "buildSessionSnapshot", "persistSessionFiles", "sessionFileRecordsFromDraft", "renderQuoteBasisMessage", "basisConfirmBlockReason", "quoteDependencyChangesForNext", "saveQuoteSessionDraftState", "queueQuoteSessionDraftStateSave", ...profiledNames]) {
      const original = window[name];
      if (typeof original !== "function") continue;
      window[name] = function (...callArgs) {
        const started = performance.now();
        try { return original.apply(this, callArgs); } finally {
          const profile = (window.__ownerUatProfile ||= {});
          const record = (profile[name] ||= { calls: 0, ms: 0 });
          record.calls += 1; record.ms += performance.now() - started;
        }
      };
    }
  }, profiledFunctions);
  const draftLines = await page.evaluate(() => state.quoteBasisSections.flatMap((section) => (section.lines || []).map((line) => ({ id: line.id, tag: line.tag, keyword: line.pricing_keyword || "", matches: (line.possible_pricing_matches || []).map((match) => match.pricing_keyword) }))));
  await writeJson("draft-lines.json", draftLines);
  result.basisLines = draftLines.length;
  result.countProposalBeforeConfirm = await page.evaluate(() => {
    const line = state.quoteBasisSections.flatMap((section) => section.lines || []).find((entry) => entry.id === "u1-count");
    const row = state.lineItems.find((entry) => entry.source_basis_line_id === "u1-count");
    return { tag: line?.tag, custom: line?.custom_pricing, variant: row?.pricing_authority?.variant, unit: row?.unit };
  });
  assert((result.countProposalBeforeConfirm.tag === "Custom" || result.countProposalBeforeConfirm.custom) && !["catalog", "manual", "included"].includes(result.countProposalBeforeConfirm.variant), "count selection must start as an unpriced AI proposal", result.countProposalBeforeConfirm);

  // Fresh proposals offer only choices Confirm can honour. Keep a synthetic old cached
  // candidate to prove that a pre-upgrade invalid choice is still refused at the real boundary.
  result.unitCandidateBeforeConfirm = await page.evaluate(() => {
    const line = state.quoteBasisSections.flatMap((section) => section.lines || []).find((entry) => entry.id === "u1-video");
    return { unit: line?.unit, candidates: line?.possible_pricing_matches || [] };
  });
  assert(result.unitCandidateBeforeConfirm.unit === "lot" && result.unitCandidateBeforeConfirm.candidates.length === 0,
    "incompatible unit candidates were offered as actionable choices", result.unitCandidateBeforeConfirm);
  await page.evaluate((item) => {
    const line = state.quoteBasisSections.flatMap((section) => section.lines || []).find((entry) => entry.id === "u1-video");
    line.possible_pricing_matches = [{ pricing_keyword: item.id, description: item.description, unit: item.unit_hint, section: item.section }];
    updateQuoteBasisCard("edited");
  }, byName("55 inch LED TV Monitor"));

  // U2: tick / cross / possible-match responsiveness on the large basis.
  const fillers = cases.filter((entry) => entry.kind === "filler");
  for (const [index, entry] of fillers.entries()) {
    const draft = draftLines.find((line) => line.id === entry.id);
    if (index % 3 === 0 && draft?.matches.length) await timedClick(page, await possibleMatchControl(page, entry.id, draft.matches[0]), "possible_match", timings);
    else if (index % 3 === 1) await timedClick(page, await basisControl(page, entry.id, '[data-basis-tag="Exclude"]'), "cross", timings);
    else await timedClick(page, await basisControl(page, entry.id, "[data-basis-tag]:not([data-basis-tag='Exclude'])"), "tick", timings);
  }
  if (cpuProfile) {
    // Self-time attribution for one representative tick on the large basis.
    const probe = fillers.find((entry) => draftLines.find((line) => line.id === entry.id)?.tag !== "Exclude");
    await cdp.send("Profiler.enable");
    await cdp.send("Profiler.setSamplingInterval", { interval: 200 });
    await cdp.send("Profiler.start");
    await timedClick(page, await basisControl(page, probe.id, '[data-basis-tag="Exclude"]'), "profiled_cross", timings);
    const { profile } = await cdp.send("Profiler.stop");
    const selfTime = new Map();
    const byId = new Map(profile.nodes.map((node) => [node.id, node]));
    const deltas = profile.timeDeltas;
    profile.samples.forEach((id, index) => {
      const frame = byId.get(id).callFrame;
      const key = (frame.functionName || "(anonymous)") + (frame.url.endsWith("app.js") || frame.url.includes("app.js?") ? ":" + (frame.lineNumber + 1) : "");
      selfTime.set(key, (selfTime.get(key) || 0) + (deltas[index] || 0) / 1000);
    });
    result.cpuProfileTop = [...selfTime.entries()].sort((a, b) => b[1] - a[1]).slice(0, 20).map(([name, ms]) => name + " " + ms.toFixed(1) + "ms");
  }
  // U1 owner selections and the normal AI-confirmed row.
  for (const entry of cases.filter((item) => ["select", "historical", "unit-review"].includes(item.kind))) await timedClick(page, await possibleMatchControl(page, entry.id, byName(entry.select).id), "possible_match", timings);
  await timedClick(page, await basisControl(page, "u1-confirmed", '[data-basis-tag="Include"]'), "tick", timings);
  for (const id of ["u1-review", "u1-legacy"]) await timedClick(page, await basisControl(page, id, "[data-basis-tag]:not([data-basis-tag='Exclude'])"), "tick", timings);
  // Resolve any remaining server-added review line (for example an id-less duplicate) with a real cross.
  for (let guard = 0; guard < 10; guard++) {
    const pending = await page.evaluate(() => {
      for (const section of state.quoteBasisSections) {
        const index = (section.lines || []).findIndex((line) => !basisLineIsInformationalDimension(line) && (normalizeBasisTag(line.tag) === "Confirm" || isPendingAiProposalLine(line)));
        if (index >= 0) return { section: section.id, index, text: section.lines[index].text };
      }
      return null;
    });
    if (!pending) break;
    (result.extraResolvedLines ||= []).push(pending.text);
    await page.locator('#basisReviewSurface [data-basis-section="' + pending.section + '"][data-basis-line-index="' + pending.index + '"][data-basis-tag="Exclude"]').first().click();
  }
  const unresolved = await page.evaluate(() => ({ reason: basisConfirmBlockReason(), lines: unresolvedConfirmLines(state.quoteBasisSections), pending: state.quoteBasisSections.flatMap((section) => (section.lines || []).filter((line) => normalizeBasisTag(line.tag) === "Confirm" || isPendingAiProposalLine(line)).map((line) => ({ section: section.id, id: line.id, tag: line.tag, text: line.text, keyword: line.pricing_keyword }))) }));
  assert(!unresolved.reason, "basis still blocked after resolving every line", unresolved);

  // U2: Confirm Quotation Basis must show its preparing state before slow network work completes.
  const selectionResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/quote-sessions/pricing-selections");
  const confirmStarted = Date.now();
  const networkBefore = network.length;
  await page.evaluate(() => { window.__ownerUatProfile = {}; window.__ownerUatEvents = []; });
  if (inflightSave) {
    // The save carries the clicked lines, so it bumps those rows' server revisions while Confirm runs.
    await page.evaluate(() => { window.__ownerUatInflightSave = saveQuoteSessionDraftState({}); });
  }
  await page.locator("#sideNextButton").click();
  await page.locator("#excelGeneratingModal.is-open").waitFor({ state: "visible", timeout: 30000 });
  const preparingVisibleMs = Date.now() - confirmStarted;
  const preparingTitle = await page.locator("#excelGeneratingTitle").innerText();
  const confirmEvents = await page.evaluate(() => ({ events: window.__ownerUatEvents, profile: window.__ownerUatProfile }));
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 90000 });
  await page.waitForFunction(() => !state.isPreparingOutput && !appIsBusy(), null, { timeout: 90000 });
  const selectionHttpResponse = await selectionResponse;
  const selectionBody = await selectionHttpResponse.json();
  const selectionRequest = selectionHttpResponse.request().postDataJSON();
  result.selectedCountRequest = selectionRequest.pricing_selections.find((entry) => entry.source_basis_line_id === "u1-count");
  assert(result.selectedCountRequest?.catalog_item_id === byName("Bar Stool").id && Number.isInteger(result.selectedCountRequest?.expected_revision), "Confirm did not submit this row and selected item revision", result.selectedCountRequest);
  result.selectionOutcomes = selectionBody.selections;
  assert(selectionBody.selections.some((entry) => entry.source_basis_line_id === "u1-video" && entry.status === "refused" && entry.reason === "unit_review_required"),
    "cached incompatible choice did not receive a truthful refusal", selectionBody.selections);
  const selectedVideoLine = selectionRequest.quote_basis_sections.flatMap((section) => section.lines || []).find((line) => line.id === "u1-video");
  assert(selectedVideoLine?.unit === "lot", "cached choice silently converted the basis quantity unit", selectedVideoLine);
  const selectedCount = selectionBody.line_items.find((row) => row.source_basis_line_id === "u1-count");
  const pricingBasis = await page.evaluate(() => collectQuoteDetails().commercial_snapshot.pricing_basis);
  assert(selectionBody.selections.some((entry) => entry.source_basis_line_id === "u1-count" && entry.status === "selected"), "count proposal was not admitted at Confirm", selectionBody.selections);
  assert(selectedCount?.pricing_authority?.variant === "catalog" && selectedCount.pricing_authority.catalog_item_id === byName("Bar Stool").id && selectedCount.pricing_authority.catalog_digest === pricingBasis.digest && selectedCount.pricing_reference_id === pricingBasis.id && selectedCount.pricing_authority.price === byName("Bar Stool").sale_unit_price, "server admission did not bind the selected row to the current reference", selectedCount);
  const selectedCountLine = selectionRequest.quote_basis_sections.flatMap((section) => section.lines || []).find((line) => line.id === "u1-count");
  assert(selectedCount.quantity === 4 && selectedCountLine?.quantity === 4 && selectedCount.pricing_basis_amount === cents(4 * byName("Bar Stool").sale_unit_price),
    "Confirm failed to price the valid proposal at its saved basis quantity", { selectedCount, selectedCountLine });
  result.selectedCountBoundary = { section: selectedCount.section, quantity: selectedCount.quantity, basisQuantity: selectedCountLine.quantity, authority: selectedCount.pricing_authority, pricingBasis };
  result.confirm = {
    preparingVisibleMs, preparingTitle, outputReadyMs: Date.now() - confirmStarted,
    clickEventDurationMs: Math.max(0, ...confirmEvents.events.filter((entry) => ["click", "pointerup"].includes(entry.name)).map((entry) => entry.duration)),
    profileBeforeModal: confirmEvents.profile,
    networkDuringConfirm: network.slice(networkBefore).map((entry) => entry.method + " " + entry.path + " +" + (entry.at - confirmStarted) + "ms"),
  };
  assert(preparingTitle.trim() === "Preparing Output", "confirm did not show the preparing state", preparingTitle);
  // The preparing state must paint before the (delayed) normalisation round trip can finish.
  assert(latencyMs < 500 || preparingVisibleMs < latencyMs, "preparing state waited for network work", result.confirm);
  assert(timings.every((sample) => !sample.requests.some((entry) => entry.endsWith("/api/line-items/normalize") || entry.endsWith("/api/quote-sessions/pricing-selections"))), "a basis click waited on normalisation", timings.map((sample) => sample.requests));
  // A save of this same tab is never reported as a change from another tab.
  assert(result.selectionStatuses.length === 1 && result.selectionStatuses[0] === 200, "single-tab Confirm selection was refused", result.selectionStatuses);
  // The selection clicks become prices only through one server-recorded selection request at Confirm.
  assert(result.confirm.networkDuringConfirm.filter((entry) => entry.startsWith("POST /api/quote-sessions/pricing-selections ")).length === 1, "Confirm did not record the selections with the server", result.confirm.networkDuringConfirm);
  result.clickTimings = summarize(timings);
  await writeJson("timings-raw.json", { timings, confirm: result.confirm });
  if (measureOnly) {
    result.status = "MEASURED";
  } else {
    // U1: trusted authority, quoted-unit price and amount for each explicit selection and the AI-confirmed row.
    const rowFor = (id) => page.evaluate((lineId) => {
      const index = state.outputRows.findIndex((row) => row.source_basis_line_id === lineId);
      const row = state.outputRows[index];
      const cells = [...document.querySelectorAll('#pricingMatchesBody tr')].find((tr) => tr.querySelector('[data-output-row="' + index + '"]'));
      const text = (selector) => cells?.querySelector(selector)?.innerText.trim() || "";
      return row ? { index, authority: outputPriceAuthority(row), quantity: row.quantity, rowAmount: row.amount, reference: text('.output-unit-price-cell:not([data-output-edit-field]) .output-cell-text'), quoted: text('[data-output-denomination="quote"] .output-cell-text'), amount: text('.amount-cell') } : null;
    }, id);
    const expectations = [...cases.filter((item) => ["select", "historical"].includes(item.kind)).map((item) => ({ id: item.id, item: byName(item.select), quantity: item.quantity })), { id: "u1-confirmed", item: byName("6 inch Spotlight"), quantity: 7 }];
    const pricing = [];
    for (const expected of expectations) {
      const row = await rowFor(expected.id);
      const price = expected.item.sale_unit_price;
      const quoted = cents(price * fxRate);
      const amount = cents(cents(expected.quantity * price) * fxRate);
      pricing.push({ id: expected.id, catalogId: expected.item.id, price, quoted, amount, row });
      assert(row?.authority?.variant === "catalog" && row.authority.catalog_item_id === expected.item.id && row.authority.price === price, "selected catalogue authority missing or wrong", { expected, row });
      assert(Number(row.reference) === price && Number(row.quoted.replace(/,/g, "")) === quoted && Number(row.amount.replace(/,/g, "")) === amount, "quoted price/amount differ from catalogue x FX", { expected: { price, quoted, amount }, row });
    }
    for (const id of ["u1-review", "u1-legacy", "u1-video"]) {
      const review = await rowFor(id);
      assert(review && !["catalog", "manual", "included"].includes(review.authority?.variant) && review.quoted === "???" && review.amount === "???", "unselected, review-only or incompatible-unit row was priced", { id, review });
      pricing.push({ id, row: review });
    }
    await writeJson("u1-pricing.json", pricing);
    result.u1 = pricing.map((entry) => ({ id: entry.id, catalogId: entry.catalogId, quoted: entry.row?.quoted, amount: entry.row?.amount, expected: entry.amount }));

    // Output edits on an admitted selection: quantity, Included, Use Reference Price, manual quote-currency override.
    const editedItem = byName("Printed Wall Graphic");
    const editedRow = () => rowFor("u1-graphic");
    const cellFor = async (field, extra = "") => page.locator('[data-output-row="' + (await editedRow()).index + '"][data-output-edit-field="' + field + '"]' + extra).first();
    await (await cellFor("quantity")).click();
    await page.locator('[data-output-editor-field="quantity"]').fill("3");
    await page.locator('[data-output-editor-field="quantity"]').press("Enter");
    let edited = await editedRow();
    assert(Number(edited.amount.replace(/,/g, "")) === cents(cents(3 * editedItem.sale_unit_price) * fxRate), "quantity edit amount", edited);
    await (await cellFor("unit_price_override", '[data-output-denomination="quote"]')).click();
    await page.locator("[data-output-included-action]").click();
    edited = await editedRow();
    assert(edited.authority?.variant === "included" && edited.quoted === "Included" && Number(edited.amount) === 0, "Included action", edited);
    await (await cellFor("unit_price_override", '[data-output-denomination="quote"]')).click();
    await page.locator("[data-output-reference-action]").click();
    edited = await editedRow();
    assert(edited.authority?.variant === "catalog" && Number(edited.quoted.replace(/,/g, "")) === cents(editedItem.sale_unit_price * fxRate), "Use reference price", edited);
    await (await cellFor("unit_price_override", '[data-output-denomination="quote"]')).click();
    await page.locator('[data-output-editor-field="unit_price_override"]').fill("199.99");
    await page.locator('[data-output-editor-field="unit_price_override"]').press("Enter");
    edited = await editedRow();
    assert(edited.authority?.variant === "manual" && edited.authority.price_currency === quoteCurrency && edited.quoted === "199.99" && Number(edited.amount.replace(/,/g, "")) === cents(3 * 199.99), "manual quote-currency override", edited);
    result.outputEdits = "PASS";

    // Save, reload and reopen: authority and amounts survive the protected round trip.
    await page.waitForFunction(() => !appIsBusy(), null, { timeout: 30000 });
    const sessionId = await page.evaluate(() => state.quoteSessionId);
    await page.waitForFunction(async (id) => {
      const detail = await fetch("/api/quote-sessions/" + encodeURIComponent(id)).then((r) => r.json());
      const row = detail.quote_session?.draft_state?.outputRows?.find((item) => item.source_basis_line_id === "u1-graphic");
      return row?.pricing_authority?.variant === "manual" && Number(row.quantity) === 3;
    }, sessionId, { timeout: 30000, polling: 500 });
    const beforeReload = await Promise.all(expectations.map((expected) => rowFor(expected.id)));
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.outputRows.length > 0, null, { timeout: 60000 });
    if (!(await page.locator("#outputSidePanel.is-active").isVisible())) await page.locator('button[data-side-panel="output"]').click();
    const afterReload = await Promise.all(expectations.map((expected) => rowFor(expected.id)));
    assert(JSON.stringify(afterReload.map((row) => [row.authority?.variant, row.quoted, row.amount])) === JSON.stringify(beforeReload.map((row) => [row.authority?.variant, row.quoted, row.amount])), "reopen changed pricing", { beforeReload, afterReload });
    result.saveReopen = "PASS";

    // The review-only row needs an explicit operator price before export is allowed.
    assert(await page.locator("#sideDownloadButton").getAttribute("aria-disabled") === "true", "export allowed while a row is unpriced");
    const reviewIndex = (await rowFor("u1-review")).index;
    await page.locator('[data-output-row="' + reviewIndex + '"][data-output-edit-field="unit_price_override"][data-output-denomination="quote"]').first().click();
    await page.locator('[data-output-editor-field="unit_price_override"]').fill("500");
    await page.locator('[data-output-editor-field="unit_price_override"]').press("Enter");
    const reviewPriced = await rowFor("u1-review");
    assert(reviewPriced.authority?.variant === "manual" && reviewPriced.amount === "500.00", "manual price on review row", reviewPriced);
    // Accepted AI proposals are custom rows: price each remaining one through the real editor.
    const unpriced = await page.evaluate(() => state.outputRows.map((row, index) => ({ index, id: row.source_basis_line_id, trusted: pricingAuthorityPrice(outputPriceAuthority(row)) !== null })).filter((row) => !row.trusted));
    result.manuallyPricedRows = unpriced.length;
    for (const row of unpriced) {
      await page.locator('[data-output-row="' + row.index + '"][data-output-edit-field="unit_price_override"][data-output-denomination="quote"]').first().click();
      await page.locator('[data-output-editor-field="unit_price_override"]').fill("10");
      await page.locator('[data-output-editor-field="unit_price_override"]').press("Enter");
    }
    const exportBlock = () => page.evaluate(() => ({ disabled: document.querySelector("#sideDownloadButton")?.getAttribute("aria-disabled"), title: document.querySelector("#sideDownloadButton")?.title, errors: outputRowsValid().errors }));
    await page.waitForFunction(() => document.querySelector("#sideDownloadButton")?.getAttribute("aria-disabled") !== "true", null, { timeout: 30000 }).catch(async () => assert(false, "export still blocked", await exportBlock()));
    // Protected XLSX: generated workbook carries the admitted amounts.
    const downloadPromise = page.waitForEvent("download", { timeout: 90000 });
    await page.locator("#sideDownloadButton").click();
    const download = await downloadPromise;
    const xlsxPath = path.join(dirs.downloads, "owner-uat.xlsx");
    await download.saveAs(xlsxPath);
    const detail = await (await api.get(baseUrl + "/api/quote-sessions/" + encodeURIComponent(sessionId))).json();
    assert(detail.quote_session?.exports?.xlsx?.exists === true && detail.quote_session.exports.xlsx.stale === false, "protected XLSX export not current", detail.quote_session?.exports);
    const protectedBytes = await (await api.get(new URL(detail.quote_session.exports.xlsx.url, baseUrl).toString())).body();
    assert(protectedBytes.equals(await fs.readFile(xlsxPath)), "protected XLSX differs from the UI download");
    // Bind each amount to its own workbook row (description + quantity) and check the totals block.
    const quantityText = (quantity, unit) => String(quantity) + " " + unit;
    const expectedRows = [
      ...expectations.filter((entry) => entry.id !== "u1-graphic").map((entry) => ({ id: entry.id, description: entry.item.description, quantity: quantityText(entry.quantity, entry.item.unit_hint), amount: pricing.find((row) => row.id === entry.id).amount })),
      { id: "u1-graphic", description: editedItem.description, quantity: quantityText(3, editedItem.unit_hint), amount: cents(3 * 199.99) },
      { id: "u1-review", description: cases.find((entry) => entry.id === "u1-review").text, quantity: quantityText(1, "lot"), amount: 500 },
    ];
    // The analysis seal is opaque transport between this browser and the server; it never reaches the workbook.
    const receipts = await page.evaluate(() => [state.originalAnalysisSnapshot?.pricing_analysis_seal?.mac].filter(Boolean));
    assert(receipts.length === 1, "analysis seal missing from the browser draft", receipts.length);
    const check = spawn(python, ["-c", [
      "import json,sys,zipfile,xml.etree.ElementTree as ET",
      "z=zipfile.ZipFile(sys.argv[1]); M='{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'",
      "xml=''.join(z.read(n).decode('utf8','replace') for n in z.namelist() if n.endswith('.xml'))",
      "assert '???' not in xml, 'unresolved marker in workbook'",
      "assert not any(r in xml for r in json.loads(sys.argv[3])) and 'pricing_analysis_seal' not in xml, 'analysis seal leaked into workbook'",
      "ss=[''.join(t.text or '' for t in si.iter(M+'t')) for si in ET.fromstring(z.read('xl/sharedStrings.xml')).iter(M+'si')] if 'xl/sharedStrings.xml' in z.namelist() else []",
      "def cell(c):",
      " v=c.find(M+'v'); i=c.find(M+'is'); t=c.get('t'); f=c.find(M+'f')",
      " text=ss[int(v.text)] if t=='s' and v is not None else (''.join(x.text or '' for x in i.iter(M+'t')) if i is not None else (v.text if v is not None else ''))",
      " return (text, f.text if f is not None else None)",
      "rows=[{''.join(ch for ch in c.get('r') if ch.isalpha()):cell(c) for c in r.iter(M+'c')} for n in z.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml') for r in ET.fromstring(z.read(n)).iter(M+'row')]",
      "get=lambda r,k: r.get(k,('',None))[0]",
      "bad=[]",
      "for e in json.loads(sys.argv[2]):",
      " hit=[r for r in rows if get(r,'C')==e['description'] and get(r,'B')==e['quantity']]",
      " if len(hit)!=1 or abs(float(get(hit[0],'E') or 'nan')-e['amount'])>0.005: bad.append({'expected':e,'rows':[{k:v[0] for k,v in r.items()} for r in hit]})",
      "items=[float(get(r,'E')) for r in rows if get(r,'B') and get(r,'C') and get(r,'E') and get(r,'B')!='Quantity']",
      "totals={get(r,'D'):(float(get(r,'E')),r['E'][1]) for r in rows if get(r,'D') in ('Total','GST 9%','Total including GST') and get(r,'E')}",
      "t=totals.get('Total'); g=totals.get('GST 9%'); a=totals.get('Total including GST')",
      "ok=bool(t and g and a) and abs(t[0]-round(sum(items),2))<=0.005 and (t[1] or '').startswith('SUM(') and abs(g[0]-round(t[0]*0.09+1e-9,2))<=0.005 and abs(a[0]-round(t[0]+g[0],2))<=0.005",
      "bad += [] if ok else [{'totals':totals,'item_sum':round(sum(items),2)}]",
      "print(json.dumps({'bad':bad,'checked_rows':len(json.loads(sys.argv[2])),'item_rows':len(items),'totals':{k:v[0] for k,v in totals.items()}})); sys.exit(1 if bad else 0)",
    ].join("\n"), xlsxPath, JSON.stringify(expectedRows), JSON.stringify(receipts)], { cwd: sourceRoot, windowsHide: true });
    let checkOut = ""; check.stdout.on("data", (chunk) => { checkOut += chunk; }); check.stderr.on("data", (chunk) => { checkOut += chunk; });
    const exitCode = await new Promise((resolve) => check.once("close", resolve));
    assert(exitCode === 0, "XLSX amounts", checkOut);
    result.xlsx = { status: "PASS", expectedRows, validator: checkOut.trim() };
    result.status = "PASS";
  }
  assert(pageErrors.length === 0, "page errors", pageErrors);
  await writeJson("result.json", result);
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  result.status = "FAIL";
  result.failureView = await browser?.contexts()[0]?.pages()[0]?.evaluate(() => ({
    modals: [...document.querySelectorAll(".modal-overlay:not([hidden]), [role=dialog]:not([hidden])")].map((node) => node.id + ": " + node.innerText.slice(0, 300)),
    status: document.querySelector("#resultStatus")?.innerText, messages: [...document.querySelectorAll(".message, .blocked-action, [role=alert]")].map((node) => node.innerText.slice(0, 200)).slice(0, 8),
    busy: typeof appIsBusy === "function" ? appIsBusy() : null, job: typeof state !== "undefined" ? state.activeJob : null,
    pricingIssues: typeof state !== "undefined" ? state.pricingIssues : null, outputErrors: typeof state !== "undefined" ? state.outputErrors : null,
    unmatched: typeof state !== "undefined" ? (state.pricingMatches || []).filter((row) => !["matched", "manual", "manual-display", "included"].includes(String(row.status || "").toLowerCase())).slice(0, 8) : null,
    statusBySection: typeof state !== "undefined" ? (state.pricingMatches || []).reduce((acc, row) => { const key = row.section + " | " + row.status; acc[key] = (acc[key] || 0) + 1; return acc; }, {}) : null,
    outputAuthorityBySection: typeof state !== "undefined" ? state.outputRows.reduce((acc, row) => { const key = row.section + " | " + (row.pricing_authority?.variant || "absent"); acc[key] = (acc[key] || 0) + 1; return acc; }, {}) : null,
  })).catch((captureError) => String(captureError));
  result.error = String(error?.stack || error);
  result.clickTimings = summarize(timings);
  await writeJson("result.json", result).catch(() => {});
  console.error(JSON.stringify(result, null, 2));
  process.exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  if (server?.child && server.child.exitCode === null) server.child.kill();
}
