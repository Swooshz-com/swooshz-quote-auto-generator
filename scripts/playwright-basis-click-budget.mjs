// Quote Basis click budget (#334): ticks and Xs must stay cheap on a real-sized quote.
// Extends the shared synthetic owner-UAT carrier with real-shaped groups (Matched, Review and
// Unmatched lines carrying server pricing offers, AI proposals, estimated sizes) and about 16 MB of
// reference files uploaded through the real intake. No live provider, live data or paid AI is used.
// It counts the work each click causes for 1.5 s after it, in which the deferred quote save must be
// sent, and until that save is answered, however slow the server is. It fails when a click rewrites
// files, copies them, saves more than once or late, or rebuilds the list.
// Anchor checks fail closed if the carrier changes. The page probes, the exact count checks and
// the click helpers live in scripts/lib/basis-click-probe.mjs, shared with Pass A of the
// live-data test; the timing checks below are this job's own.
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
replaceOnce('import { signIn } from "./internal-password-browser-test.mjs";', `import { signIn } from ${local('internal-password-browser-test.mjs')};
import { CLICK_COUNT_BUDGET, groupActions, installClickProbe, judgeClickCounts, judgeQuoteSizes, lineButton, linesIn, measureClicks, measureQuoteSizes, probeOpenQuote, quoteClickBudget } from ${local('lib/basis-click-probe.mjs')};`);
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
    const budget = {
      ...CLICK_COUNT_BUDGET,
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

    // 2. Probes (scripts/lib/basis-click-probe.mjs). Exact counts, then this job's time budgets.
    await installClickProbe(page);
    const judge = (sample, posts) => {
      judgeClickCounts(sample, posts, budget, check);
      const label = sample.group + ":" + sample.action;
      check(sample.clickFrames.every((ms) => ms <= budget.clickFrameMs), label + " took too long to handle the click", sample.clickFrames);
      check(sample.laterFrames.every((ms) => ms <= budget.laterFrameMs), label + " froze the page after the click", sample.laterFrames);
    };
    const measure = (group, action, clicks, expectedClicks = 1, touched = []) =>
      measureClicks(page, { group, action, clicks, expectedClicks, touched, quotePosts, judge });

    await cdp.send("Emulation.setCPUThrottlingRate", { rate: budget.cpuThrottle });
    // 3. Tick, X, block X and block tick in every group.
    for (const group of ["Matched", "Review", "Unmatched"]) {
      const lineId = (await linesIn(page, group, "filler-"))[0];
      assert(lineId, "no filler line in group", group);
      for (const [action, click] of groupActions(page, lineId)) result.clickBudget.samples.push(await measure(group, action, [click]));
    }
    // 4. A burst of five fast clicks on Unmatched lines arms one deferred quote save, not five.
    const unmatched = (await linesIn(page, "Unmatched", "filler-")).slice(0, 3);
    assert(unmatched.length === 3, "too few Unmatched lines for the burst", unmatched);
    result.clickBudget.bursts.push(await measure("Unmatched", "burst", [
      () => lineButton(page, unmatched[0], true), () => lineButton(page, unmatched[1], true), () => lineButton(page, unmatched[2], true),
      () => lineButton(page, unmatched[0], false), () => lineButton(page, unmatched[1], false),
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

    // 5. The same quote reopened from the dashboard, as Pass A opens a real one, and grown to the size of
    // the largest real quote probed on the copy of live (#390 hold 8: 440,528 characters of browser copy).
    // A reopened quote's browser copy also holds the restore key, which the steps above never have.
    await finishPendingQuoteSaves(page, sessionId);
    assert(await page.evaluate(async (id) => {
      state.analysisFindings = Array.from({ length: 200 }, (_, index) => ({ text: "Synthetic finding " + (index + 1) + ": " + "the rear wall, the counter and the ceiling feature were read from the booth render. ".repeat(2), confidence_pct: 80 }));
      return await ensureQuoteSession({ quoteGenerated: false, synchronizeDraft: true }) === id;
    }, sessionId), "the grown quote was not saved");
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && !appIsBusy(), null, { timeout: 60000 });
    await page.evaluate(() => showDashboard());
    await page.locator('#dashboardSessionsList [data-quote-session-id="' + sessionId + '"]').waitFor({ state: "attached", timeout: 30000 });
    assert(await page.evaluate((id) => modifyDashboardQuote(id), sessionId) === true, "the quote did not reopen");
    await page.locator("#quoteBasisButton").click();
    await page.locator("#basisReviewSurface li[data-basis-key]").first().waitFor({ state: "visible", timeout: 30000 });
    const reopened = result.clickBudget.reopened = {};
    // This synthetic quote's draft is 224,430 characters; a constant, so the app under test cannot raise it.
    const draftLimit = 240000;
    const reopenedProbe = await probeOpenQuote(page, { quotePosts, facts: reopened, draftLimit, check: (ok, message, detail) => check(ok, "reopened quote: " + message, detail) });
    assert(reopened.browser_copy_bytes >= 440528 && reopened.restored_key_bytes >= reopened.quote_bytes, "the reopened quote is smaller than the largest real one", reopened);
    check(reopened.bursts.some((burst) => burst.group === "Unmatched" && burst.quote_saves === 1), "the reopened quote's Unmatched burst did not end in exactly one quote save", reopened.bursts);

    // 6. A click on a line already in that state changes and writes nothing, and the probe says so.
    const faultLine = (await linesIn(page, "Unmatched"))[0];
    assert(faultLine, "the reopened quote has no Unmatched line for the fault checks");
    const clickOnce = (exclude, budget, caught) => measureClicks(page, { group: "Unmatched", action: "fault", clicks: [() => lineButton(page, faultLine, exclude)], quotePosts,
      judge: (sample, posts) => judgeClickCounts(sample, posts, budget, (ok, message) => { if (!ok) caught.push(message); }) });
    const idleCaught = [];
    const idle = [await clickOnce(true, reopenedProbe.budget, idleCaught), await clickOnce(true, reopenedProbe.budget, idleCaught)];
    check(idleCaught.length === 0 && idle[0].changingClicks === 1 && idle[1].idleClicks === 1 && idle[1].changingClicks === 0 && idle[1].quoteLocalStorageWrites === 0,
      "a second X on an excluded line is not recorded as a click with nothing to change", { idleCaught, idle: idle.map((sample) => [sample.changingClicks, sample.idleClicks, sample.quoteLocalStorageWrites]) });
    await clickOnce(false, reopenedProbe.budget, idleCaught);

    // 7. The faults the checks exist for, put into the page on this quote before its sizes are read, as a
    // faulty app would have them: each fails its own checks only. The last list is judged on the sizes alone.
    const tooMuch = "serialised too much data";
    const clickFaults = [
      ["no fault", "saveSessionState", "original();", []],
      ["a second copy of the quote per click", "saveSessionState", "original(); JSON.stringify(buildSessionSnapshot());", [tooMuch]],
      ["a second copy of the draft per click", "saveSessionState", "original(); JSON.stringify(currentQuoteSessionDraftState());", [tooMuch]],
      ["a second browser copy written per click", "saveSessionState", "original(); original();", ["more than once per click", "outside the click and its save", tooMuch]],
      ["a second copy of the draft per save", "saveQuoteSessionDraftState", "JSON.stringify(currentQuoteSessionDraftState()); return original();", [tooMuch]],
      ["a changed quote never written to the browser copy", "saveSessionState", "", ["without writing it to browser storage"]],
      ["a change only its save writes to the browser copy", "syncControlStates", "const real = window.saveSessionState; window.saveSessionState = () => {}; try { return original(); } finally { window.saveSessionState = real; }", ["without writing it to browser storage"]],
      ["a click the app ignores", "applyBasisTag", "", ["changed nothing although a line"]],
      ["a plan that finds nothing to change", "planBasisTagChanges", "return null;", ["changed nothing although a line"]],
      ["a save sent late", "queueQuoteSessionDraftStateSave", "return original({ ...values[0], delay: 2500 });", ["sent a quote save after the"]],
      ["the restore key in the workspace view", "buildWorkspaceViewSnapshot", "return { ...original(), restoreKey: state.quoteSessionRestoredDraftKey };", ["the workspace view holds more", tooMuch]],
    ];
    const sizeFaults = [
      ["a second draft in the workspace view", "buildWorkspaceViewSnapshot", "return { ...original(), quoteDraft: currentQuoteSessionDraftState() };", ["the workspace view holds more"]],
      ["the draft twice in the browser copy", "buildSessionSnapshot", "const copy = original(); return { ...copy, again: { ...copy, quoteSessionRestoredDraftKey: '' } };", ["holds the quote more than once beside its restore key"]],
      ["a second restore key in the browser copy", "buildSessionSnapshot", "const copy = original(); return { ...copy, again: copy.quoteSessionRestoredDraftKey };", ["holds the quote more than once beside its restore key"]],
      ["a restore key that holds the quote twice", "buildSessionSnapshot", "const copy = original(); return { ...copy, quoteSessionRestoredDraftKey: copy.quoteSessionRestoredDraftKey + copy.quoteSessionRestoredDraftKey };", ["restore key is larger than one copy"]],
      ["the analysis snapshot twice in the draft", "buildSessionSnapshot", "const copy = original(); return { ...copy, originalAnalysisSnapshot: { ...copy.originalAnalysisSnapshot, again: copy.originalAnalysisSnapshot } };", ["the draft is larger than the quote"]],
    ];
    reopened.faults = [];
    for (const [fault, name, body, expected, sizesOnly] of [...clickFaults, ...sizeFaults.map((entry) => [...entry, true])]) {
      await page.evaluate(({ name, body }) => {
        const real = window[name];
        const faulty = new Function("original", "values", body);
        window.__withoutFault = () => { window[name] = real; };
        window[name] = function (...values) { return faulty((...other) => real.apply(this, other.length ? other : values), values); };
      }, { name, body });
      const caught = [];
      const sizes = await measureQuoteSizes(page);
      judgeQuoteSizes(sizes, draftLimit, (ok, message) => { if (!ok) caught.push(message); });
      if (!sizesOnly) for (const exclude of [true, false]) await clickOnce(exclude, quoteClickBudget(sizes), caught);
      await page.evaluate(() => window.__withoutFault());
      reopened.faults.push({ fault, caught });
      check(expected.every((text) => caught.some((message) => message.includes(text))) && caught.every((message) => expected.some((text) => message.includes(text))),
        "the click budget did not catch " + fault, caught);
    }
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
