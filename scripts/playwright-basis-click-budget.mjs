// Quote Basis click budget (#334): ticks and Xs must stay cheap on a real-sized quote.
// Extends the shared synthetic owner-UAT carrier with real-shaped groups (Matched, Review and
// Unmatched lines carrying server pricing offers, AI proposals, estimated sizes) and about 16 MB of
// reference files uploaded through the real intake. No live provider, live data or paid AI is used.
// It counts the work each click causes for 1.5 s after it (which covers the deferred quote save)
// and fails when a click rewrites files, copies them, saves more than once or rebuilds the list.
// Anchor checks fail closed if the carrier changes.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { freshBrowserRun } from './synthetic-browser-boundary.mjs';
const root = fileURLToPath(new URL('..', import.meta.url));
const evidence = process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(root, '_logs/browser/basis-click-budget');
const carrierRoot = await freshBrowserRun(evidence, 'carrier-');
let code = await fs.readFile(path.join(root, 'scripts/playwright-owner-uat-basis-repair.mjs'), 'utf8');
function replaceOnce(before, after) {
  if (code.split(before).length !== 2) throw new Error('Shared basis carrier anchor changed: ' + before.slice(0, 90));
  code = code.replace(before, after);
}
const local = (file) => JSON.stringify(pathToFileURL(path.join(root, 'scripts', file)).href);
replaceOnce('const sourceRoot = fileURLToPath(new URL("..", import.meta.url));', 'const sourceRoot = process.cwd();');
replaceOnce('import { signIn } from "./internal-password-browser-test.mjs";', `import { signIn } from ${local('internal-password-browser-test.mjs')};`);
replaceOnce('import { SyntheticBrowserBoundary, freshBrowserRun } from "./synthetic-browser-boundary.mjs";',
  `import { SyntheticBrowserBoundary, freshBrowserRun } from ${local('synthetic-browser-boundary.mjs')};`);
// Real-shaped fillers: lines with a possible catalogue match (Review), bespoke lines with no catalogue
// match (Unmatched, with a server pricing offer), AI proposals, and catalogue lines whose size the AI
// predicted (Matched, shown as "Est.").
replaceOnce('  cases.push({ id: "filler-" + (index + 1), kind: "filler", section: catalog.find((item) => item.description.toLowerCase().includes(noun)).section, text: "Additional " + noun + " for zone " + (index + 1),',
  `  const shape = ["catalogue", "bespoke", "proposal", "estimate"][index % 4];
  const shapeItem = catalog.find((item) => item.description.toLowerCase().includes(noun));
  if (shape === "bespoke") { cases.push({ id: "filler-" + (index + 1), shape, kind: "filler", section: shapeItem.section, text: "Bespoke kinetic sculpture for zone " + (index + 1), quantity: 2, unit: "nos" }); continue; }
  if (shape === "proposal") { cases.push({ id: "filler-" + (index + 1), shape, kind: "filler", proposal: true, section: shapeItem.section, text: "Custom fabricated " + noun + " feature for zone " + (index + 1), quantity: 2, unit: "nos" }); continue; }
  cases.push({ shape, id: "filler-" + (index + 1), kind: "filler", section: shapeItem.section, text: "Additional " + noun + " for zone " + (index + 1),`);
replaceOnce('}\nif (sourceProvenance) cases.push(', `  if (shape === "estimate") Object.assign(cases[cases.length - 1], { kind: "confirm", estimated: true, claim: shapeItem.description.slice(shapeItem.description.indexOf(" ") + 1) });
}
if (sourceProvenance) cases.push(`);
replaceOnce('confidence_pct: 82 };', 'confidence_pct: 82, ...(entry.estimated ? { quantity_estimate: { quantity: entry.quantity, unit: entry.unit } } : {}) };');
// About 16 MB of reference files (as data URL text), uploaded through the real intake so the server holds them.
replaceOnce('  await page.locator("#imageInput").setInputFiles({ name: "owner-uat-synthetic-render.png", mimeType: "image/png", buffer: render });\n  await page.locator("#fileList .file-item").first().waitFor({ state: "visible" });',
  `  const extraRenders = [];
  for (let index = 0; index < 3; index++) extraRenders.push({ name: "click-budget-synthetic-render-" + (index + 1) + ".png", mimeType: "image/png", buffer: await noisePng(page, 2000 + index, 1500) });
  await page.locator("#imageInput").setInputFiles([{ name: "owner-uat-synthetic-render.png", mimeType: "image/png", buffer: render }, ...extraRenders]);
  await page.waitForFunction(() => document.querySelectorAll("#fileList .file-item").length === 4, null, { timeout: 60000 });`);
replaceOnce('try {\n  server = startServer();', 'carrier: try {\n  server = startServer();');
replaceOnce('  result.basisLines = draftLines.length;', `  result.basisLines = draftLines.length;
  {
    const windowMs = 1500;
    // A deferred quote save is armed 650 ms after a click; work before this mark belongs to the click itself.
    const clickPhaseMs = 400;
    const budget = {
      clickQuoteLocalStorageWrites: 1, indexedDbPuts: 0, basisRenders: 1, rebuiltRows: 0, quoteSavesPerBurst: 1,
      // A click writes one browser copy of the quote (about 210 KB here); a quote save copies, compares and
      // posts the draft state (about 1.3 MB). Copying the 16 MB of files, or text-encoding every string,
      // breaks these by far.
      serialisedBytesPerClick: 256 * 1024, serialisedBytesPerQuoteSave: 1.5 * 1024 * 1024, encodedBytesPerClick: 16 * 1024,
      // Time budgets at 4x CPU: 1.5x the worst of five runs of the fixed build (19 Nov 2026). Counts are
      // the exact checks; these catch a slowdown the counts miss. A slower runner may raise them by env.
      medianInputToPaintMs: Number(process.env.SQAG_CLICK_BUDGET_MEDIAN_MS || 480),
      maxInputToPaintMs: Number(process.env.SQAG_CLICK_BUDGET_MAX_MS || 552),
      clickFrameMs: Number(process.env.SQAG_CLICK_BUDGET_CLICK_FRAME_MS || 462),
      // Any frame after the click's own work, such as the deferred quote save.
      laterFrameMs: Number(process.env.SQAG_CLICK_BUDGET_LATER_FRAME_MS || 155),
      cpuThrottle: 4,
    };
    const failures = [];
    const check = (ok, message, detail) => { if (!ok) failures.push(message + (detail === undefined ? "" : ": " + JSON.stringify(detail))); };
    const quotePosts = [];
    page.on("request", (request) => {
      if (request.method() !== "POST" || new URL(request.url()).pathname !== "/api/quote-sessions") return;
      let files = -1;
      try { const body = request.postDataJSON(); files = Array.isArray(body?.draft_files) ? body.draft_files.length : 0; } catch { files = -1; }
      quotePosts.push({ at: Date.now(), files });
    });

    // 1. Settle: the quote and every reference file are saved on the server and nothing is pending.
    const sessionId = await page.evaluate(async () => ensureQuoteSession({ quoteGenerated: false, synchronizeDraft: true }));
    assert(sessionId, "the settled quote was not saved");
    await finishPendingQuoteSaves(page, sessionId);
    const files = await page.evaluate(() => {
      const records = sessionFileRecordsFromDraft();
      return { count: records.length, dataUrlChars: records.reduce((total, record) => total + record.data_url.length, 0),
        onServer: quoteSessionDraftFilesAreConfirmed(state.quoteSessionId, quoteSessionDraftFileComparisonKey(records)) };
    });
    assert(files.count >= 5 && files.dataUrlChars >= 16_000_000 && files.onServer, "the fixture lacks real-sized saved reference files", files);
    const shape = await page.evaluate(() => {
      const groups = {};
      for (const grid of document.querySelectorAll("#basisReviewSurface .basis-review-grid")) groups[grid.getAttribute("aria-label")] = grid.querySelectorAll("li[data-basis-key]").length;
      return { groups, estimated: document.querySelectorAll("#basisReviewSurface [data-basis-quantity-est]").length,
        proposals: document.querySelectorAll('#basisReviewSurface [data-basis-tag="Custom"]').length,
        lines: state.quoteBasisSections.flatMap((section) => section.lines || []).length };
    });
    assert(shape.lines >= 60 && shape.groups.Matched > 0 && shape.groups.Review > 0 && shape.groups.Unmatched >= 5
      && shape.estimated > 0 && shape.proposals > 0, "the fixture is not real-shaped", shape);
    result.clickBudget = { budget, files, shape, samples: [], bursts: [] };

    // 2. Probes. Each counts only while armed, so the test's own page reads never count.
    await page.evaluate(() => {
      const probe = window.__clickProbe = { armed: false, events: [], writers: [] };
      const log = (kind, amount = 1) => { if (probe.armed) probe.events.push({ kind, at: performance.now(), amount }); };
      const setItem = Storage.prototype.setItem;
      // Name the code that wrote, so a failure says where an extra write came from.
      const caller = () => (new Error().stack || "").split("\\n").slice(3, 7).map((frame) => (frame.match(/at (\\S+)/) || [])[1] || "?").join("<");
      Storage.prototype.setItem = function (key, value) {
        if (key === QUOTE_SESSION_STORAGE_KEY && probe.armed) { log("quoteLocalStorageWrite"); probe.writers.push(caller()); }
        return setItem.call(this, key, value);
      };
      const put = IDBObjectStore.prototype.put;
      IDBObjectStore.prototype.put = function (...values) { log("indexedDbPut"); return put.apply(this, values); };
      const stringify = JSON.stringify;
      JSON.stringify = function (...values) { const text = stringify.apply(this, values); if (typeof text === "string") log("serialisedBytes", text.length); return text; };
      const encode = TextEncoder.prototype.encode;
      TextEncoder.prototype.encode = function (...values) { const bytes = encode.apply(this, values); log("encodedBytes", bytes.byteLength); return bytes; };
      // A render may re-enter itself once to build its pricing index; count only the outer call.
      const render = window.renderQuoteBasisMessage;
      let depth = 0;
      window.renderQuoteBasisMessage = function (...values) {
        if (!depth) log("basisRender");
        depth += 1;
        try { return render.apply(this, values); } finally { depth -= 1; }
      };
      document.addEventListener("click", () => log("click"), true);
      new PerformanceObserver((list) => { for (const entry of list.getEntries()) if (probe.armed) probe.events.push({ kind: "longFrame", at: entry.startTime, amount: entry.duration }); })
        .observe({ type: "long-animation-frame" });
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) if (probe.armed && ["pointerdown", "pointerup", "mousedown", "mouseup", "click"].includes(entry.name)) probe.events.push({ kind: "inputToPaint", at: entry.startTime, amount: entry.duration });
      }).observe({ type: "event", durationThreshold: 16 });
    });
    // Rows the clicks changed may be redrawn; every other row must keep its DOM node.
    const arm = (touched) => page.evaluate((touched) => {
      window.__touchedRows = new Set(touched);
      window.__rowsBefore = new Map([...document.querySelectorAll("#basisReviewSurface li[data-basis-key]")].map((row) => [row.dataset.basisKey, { row, html: row.outerHTML }]));
      window.__clickProbe.events = [];
      window.__clickProbe.writers = [];
      window.__ownerUatProfile = {};
      window.__clickProbe.armed = true;
    }, touched);
    const disarm = (phaseMs) => page.evaluate((phaseMs) => {
      const probe = window.__clickProbe;
      probe.armed = false;
      const clicks = probe.events.filter((event) => event.kind === "click").map((event) => event.at);
      // A long frame starts before the click handler runs, so the click's own frame begins up to 100 ms earlier.
      const inClickPhase = (at) => clicks.some((click) => at >= click - 100 && at < click + phaseMs);
      const sum = (kind, phase = null) => probe.events.filter((event) => event.kind === kind && (phase === null || inClickPhase(event.at) === phase)).reduce((total, event) => total + event.amount, 0);
      let rebuilt = 0, kept = 0;
      for (const row of document.querySelectorAll("#basisReviewSurface li[data-basis-key]")) {
        const before = window.__rowsBefore.get(row.dataset.basisKey);
        if (!before || before.html !== row.outerHTML || window.__touchedRows.has(row.dataset.basisKey)) continue;
        if (before.row === row) kept += 1; else rebuilt += 1;
      }
      const profileMs = Object.fromEntries(Object.entries(window.__ownerUatProfile || {}).map(([name, entry]) => [name, Math.round(entry.ms)]));
      // A partial redraw must leave exactly the DOM a full render of the same state gives.
      const fresh = document.createElement("div");
      fresh.innerHTML = renderQuoteBasisMessage(state.quoteBasis, "edited");
      return {
        matchesFullRender: fresh.innerHTML === document.querySelector("#basisReviewSurface").innerHTML,
        writers: probe.writers,
        // Time per app function (the shared carrier wraps them; add more with --profile=name,name).
        profileMs,
        clicks: clicks.length,
        clickQuoteLocalStorageWrites: sum("quoteLocalStorageWrite", true),
        quoteLocalStorageWrites: sum("quoteLocalStorageWrite"),
        indexedDbPuts: sum("indexedDbPut"),
        basisRenders: sum("basisRender"),
        serialisedBytes: sum("serialisedBytes"),
        encodedBytes: sum("encodedBytes"),
        inputToPaintMs: Math.max(0, ...probe.events.filter((event) => event.kind === "inputToPaint").map((event) => event.amount)),
        clickFrames: probe.events.filter((event) => event.kind === "longFrame" && inClickPhase(event.at)).map((event) => Math.round(event.amount)),
        laterFrames: probe.events.filter((event) => event.kind === "longFrame" && !inClickPhase(event.at)).map((event) => Math.round(event.amount)),
        rebuiltRows: rebuilt, keptRows: kept,
      };
    }, phaseMs);
    const target = (lineId) => page.evaluate((id) => {
      for (const section of state.quoteBasisSections) {
        const index = (section.lines || []).findIndex((line) => line.id === id);
        if (index >= 0) {
          const row = document.querySelector('#basisReviewSurface li[data-basis-key="' + CSS.escape(id) + '"]');
          return { section: section.id, index, tag: normalizeBasisTag(section.lines[index].tag), group: row?.closest(".basis-review-grid")?.getAttribute("aria-label") || "" };
        }
      }
      return null;
    }, lineId);
    const lineButton = async (lineId, exclude) => {
      const at = await target(lineId);
      assert(at, "basis line missing", lineId);
      return page.locator('#basisReviewSurface [data-basis-section="' + at.section + '"][data-basis-line-index="' + at.index + '"]'
        + (exclude ? '[data-basis-tag="Exclude"]' : "[data-basis-tag]:not([data-basis-tag='Exclude'])")).first();
    };
    const blockButton = async (lineId, action) => {
      const at = await target(lineId);
      assert(at?.group, "basis line has no group", lineId);
      return page.locator('#basisReviewSurface .basis-review-grid[aria-label="' + at.group + '"] .basis-review-item[data-basis-key="' + at.group + ":" + at.section + '"] [data-basis-section-action="' + action + '"]').first();
    };
    const linesIn = (group) => page.evaluate((group) => [...document.querySelectorAll('#basisReviewSurface .basis-review-grid[aria-label="' + group + '"] li[data-basis-key]')]
      .map((row) => row.dataset.basisKey).filter((key) => key.startsWith("filler-")), group);
    const judge = (sample, posts) => {
      const label = sample.group + ":" + sample.action;
      check(sample.clicks === sample.expectedClicks, label + " click was not seen by the page", sample.clicks);
      check(sample.clickQuoteLocalStorageWrites <= budget.clickQuoteLocalStorageWrites * sample.clicks, label + " wrote the quote to browser storage more than once per click", sample.clickQuoteLocalStorageWrites);
      // A deferred quote save writes its acknowledgement once; nothing else may write after the click.
      check(sample.quoteLocalStorageWrites <= sample.clicks + posts.length, label + " wrote the quote to browser storage outside the click and its save", { writes: sample.quoteLocalStorageWrites, writers: sample.writers });
      check(sample.indexedDbPuts === budget.indexedDbPuts, label + " rewrote unchanged reference files to IndexedDB", sample.indexedDbPuts);
      check(sample.basisRenders <= budget.basisRenders * sample.clicks, label + " rendered the Quote Basis more than once per click", sample.basisRenders);
      check(sample.matchesFullRender, label + " left the Quote Basis different from a full render of the same state");
      check(sample.rebuiltRows === budget.rebuiltRows, label + " rebuilt unchanged basis rows", { rebuilt: sample.rebuiltRows, kept: sample.keptRows });
      check(sample.serialisedBytes <= budget.serialisedBytesPerClick * sample.clicks + budget.serialisedBytesPerQuoteSave * posts.length,
        label + " serialised too much data", sample.serialisedBytes);
      check(sample.encodedBytes <= budget.encodedBytesPerClick * sample.clicks, label + " text-encoded too much data", sample.encodedBytes);
      check(posts.length <= budget.quoteSavesPerBurst, label + " saved the quote more than once", posts.length);
      check(posts.every((post) => post.files === 0), label + " sent reference files the server already has", posts.map((post) => post.files));
      check(sample.clickFrames.every((ms) => ms <= budget.clickFrameMs), label + " took too long to handle the click", sample.clickFrames);
      check(sample.laterFrames.every((ms) => ms <= budget.laterFrameMs), label + " froze the page after the click", sample.laterFrames);
    };
    const measure = async (group, action, clicks, expectedClicks = 1, touched = []) => {
      const postsBefore = quotePosts.length;
      await arm(touched);
      for (const [index, click] of clicks.entries()) {
        if (index) await page.waitForTimeout(200);
        await (await click()).click();
      }
      await page.waitForTimeout(windowMs);
      const sample = { group, action, expectedClicks, ...(await disarm(clickPhaseMs)) };
      const posts = quotePosts.slice(postsBefore);
      sample.quoteSaves = posts.length;
      judge(sample, posts);
      return sample;
    };

    await cdp.send("Emulation.setCPUThrottlingRate", { rate: budget.cpuThrottle });
    // 3. Tick, X, block X and block tick in every group.
    for (const group of ["Matched", "Review", "Unmatched"]) {
      const lineId = (await linesIn(group))[0];
      assert(lineId, "no filler line in group", group);
      for (const [action, click] of [
        ["line_cross", () => lineButton(lineId, true)],
        ["line_tick", () => lineButton(lineId, false)],
        ["block_cross", () => blockButton(lineId, "Exclude")],
        ["block_tick", () => blockButton(lineId, "Include")],
      ]) result.clickBudget.samples.push(await measure(group, action, [click]));
    }
    // 4. A burst of five fast clicks on Unmatched lines arms one deferred quote save, not five.
    const unmatched = (await linesIn("Unmatched")).slice(0, 3);
    assert(unmatched.length === 3, "too few Unmatched lines for the burst", unmatched);
    result.clickBudget.bursts.push(await measure("Unmatched", "burst", [
      () => lineButton(unmatched[0], true), () => lineButton(unmatched[1], true), () => lineButton(unmatched[2], true),
      () => lineButton(unmatched[0], false), () => lineButton(unmatched[1], false),
    ], 5, unmatched));
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });

    const samples = result.clickBudget.samples;
    const unmatchedSaves = samples.filter((sample) => sample.group === "Unmatched").reduce((total, sample) => total + sample.quoteSaves, 0);
    check(unmatchedSaves > 0, "no Unmatched click took the deferred quote save path, so the fixture does not test it", unmatchedSaves);
    check(result.clickBudget.bursts[0].quoteSaves === 1, "the Unmatched burst did not end in exactly one quote save", result.clickBudget.bursts[0].quoteSaves);
    const paints = samples.map((sample) => sample.inputToPaintMs).sort((a, b) => a - b);
    result.clickBudget.inputToPaint = { medianMs: paints[Math.floor(paints.length / 2)], maxMs: paints[paints.length - 1] };
    check(result.clickBudget.inputToPaint.medianMs <= budget.medianInputToPaintMs, "median click input-to-paint is over budget", result.clickBudget.inputToPaint);
    check(result.clickBudget.inputToPaint.maxMs <= budget.maxInputToPaintMs, "a click's input-to-paint is over budget", result.clickBudget.inputToPaint);
    result.clickBudget.failures = failures;
    assert(pageErrors.length === 0, "page errors", pageErrors);
    result.status = failures.length ? "FAIL" : "PASS";
    await writeJson("result.json", result);
    console.log(JSON.stringify(result.clickBudget, null, 2));
    if (failures.length) process.exitCode = 1;
  }
  break carrier;`);
const runtime = path.join(carrierRoot, 'basis-click-budget-carrier.mjs');
await fs.writeFile(runtime, code);
const child = spawn(process.execPath, [runtime, '--lines=60', '--latency-ms=100', '--fresh-analysis', ...process.argv.slice(2)], {
  cwd: root, windowsHide: true, stdio: 'inherit',
  env: { ...process.env, SQAG_OWNER_UAT_EVIDENCE_ROOT: carrierRoot },
});
const exitCode = await new Promise((resolve) => child.once('exit', resolve));
process.exitCode = exitCode ?? 1;
