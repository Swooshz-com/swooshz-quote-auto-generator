// Standard service rows and quote exclusions (#363). Extends the shared synthetic owner-UAT carrier;
// no live provider, live data or paid AI is used. Anchor checks fail closed if the carrier changes.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { freshBrowserRun } from './synthetic-browser-boundary.mjs';
const root = fileURLToPath(new URL('..', import.meta.url));
const evidence = process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(root, '_logs/browser/standard-service-rows');
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
// The analysis also suggests both services in its own words; neither may appear twice.
replaceOnce('const cases = [', `const cases = [
  { id: "s-pm-suggested", kind: "unmatched", section: "Project management", text: "Project management and site supervision", quantity: 1, unit: "lot" },
  { id: "s-waste-suggested", kind: "unmatched", section: "Site services", text: "Waste disposal after dismantling", quantity: 1, unit: "lot" },`);
replaceOnce('  result.basisLines = draftLines.length;', `  result.basisLines = draftLines.length;
  {
    const PM = "standard-project-management-fee";
    const WASTE = "standard-waste-disposal";
    const surface = page.locator("#basisReviewSurface");
    const standardRows = () => page.evaluate(() => {
      const lines = state.quoteBasisSections.flatMap((section) => (section.lines || []).map((line) => ({ section, line })));
      return {
        rows: lines.filter(({ line }) => String(line.id || "").startsWith("standard-")).map(({ section, line }) => ({
          section: section.title, id: line.id, tag: normalizeBasisTag(line.tag), text: line.text, quantity: Number(line.quantity), unit: normalizeUnit(line.unit),
        })),
        suggested: lines.filter(({ line }) => String(line.id || "").startsWith("s-")).map(({ line }) => line.id),
      };
    });
    const expectRows = async (pmTag, wasteTag, label) => {
      const seen = await standardRows();
      const expected = [
        { section: "Project management fee", id: PM, tag: pmTag, text: "Lump sum for project management", quantity: 1, unit: "nos" },
        { section: "Waste disposal", id: WASTE, tag: wasteTag, text: "Waste disposal / Recycling at production venue", quantity: 1, unit: "nos" },
      ];
      assert(JSON.stringify(seen) === JSON.stringify({ rows: expected, suggested: [] }), label, seen);
      for (const id of [PM, WASTE]) {
        const rows = surface.locator('[data-basis-key="' + id + '"]');
        assert(await rows.count() === 1, label + ": standard row is not shown exactly once", { id, count: await rows.count() });
      }
    };
    const rowOf = async (id) => (await basisControl(page, id, '[data-basis-tag="Exclude"]')).locator("xpath=ancestor::li[1]");
    // The badge is the decision (owner, 25 Nov 2026): Confirm with the amber ? while undecided, then
    // Included in green or Exclude in red. The colour and the mark come from the row's own class.
    const badges = { Confirm: ["Confirm", "rgb(180, 83, 9)", "?"], Include: ["Included", "rgb(13, 105, 68)", "\u2713"], Exclude: ["Exclude", "rgb(161, 56, 47)", "X"] };
    const expectBadges = async (tags, label) => {
      for (const [id, tag] of Object.entries(tags)) {
        const seen = await (await rowOf(id)).evaluate((row) => {
          const pill = row.querySelector(".basis-line-pill");
          return [pill.innerText.trim(), getComputedStyle(pill).color, getComputedStyle(row.querySelector(".basis-line-icon"), "::before").content.replaceAll('"', "").trim()];
        });
        assert(seen[0] === badges[tag][0] && seen[1] === badges[tag][1], label + ": wrong badge", { id, tag, seen });
        badgeSeen[label + ":" + id] = seen;
      }
    };
    const badgeSeen = {};
    const idle = () => page.waitForFunction(() => !appIsBusy() && !state.isPreparingOutput, null, { timeout: 90000 });

    // 1. A new quote offers both rows once, unresolved, with the existing tick and X; the suggestions
    // offering the same services are not added again.
    await expectRows("Confirm", "Confirm", "new quote does not offer both standard rows once");
    await expectBadges({ [PM]: "Confirm", [WASTE]: "Confirm" }, "new quote");
    // Confirm pressed with undecided rows says what is needed, not that the AI analysis is blocked.
    await page.locator("#sideNextButton").click({ force: true }); // greyed, but it answers a press with the reason
    const banner = (await page.locator("#aiFailureBanner").innerText()).replace(/\\s+/g, " ").trim();
    assert(banner === "Rows need a decision. Resolve all review lines before confirming quotation basis.", "Confirm with undecided rows shows the wrong message", banner);
    for (const id of [PM, WASTE]) {
      const row = await rowOf(id);
      assert(await row.locator("[data-basis-tag='Include']").count() === 1 && await row.locator("[data-basis-tag='Exclude']").count() === 1,
        "standard row lacks the tick and X", id);
      assert((await row.locator("[data-basis-quantity-edit]").innerText()).trim().startsWith("1 nos"), "standard row quantity is not 1 nos", id);
    }

    // 2. Tick includes it at zero (Included); X leaves it out.
    const tickAndX = async (label) => {
      await (await basisControl(page, PM, "[data-basis-tag='Include']")).click();
      await (await basisControl(page, WASTE, '[data-basis-tag="Exclude"]')).click();
      await expectRows("Include", "Exclude", label);
      await expectBadges({ [PM]: "Include", [WASTE]: "Exclude" }, label);
    };
    await tickAndX("tick or X did not apply");
    const resetDraft = async (label) => {
      await page.locator("#resetQuoteBasisButton").click();
      await idle();
      await expectRows("Confirm", "Confirm", label);
      await expectBadges({ [PM]: "Confirm", [WASTE]: "Confirm" }, label);
    };
    const reopen = async () => {
      const id = await page.evaluate(() => state.quoteSessionId);
      await page.locator("#backToDashboardButton").click();
      await page.waitForFunction(() => !appIsBusy() && state.activeAppView === "dashboard", null, { timeout: 30000 });
      await page.locator('[data-quote-session-id="' + id + '"]').first().click();
      await page.locator('[data-dashboard-panel-action="modify-session"]').click();
      await page.waitForFunction((id) => !appIsBusy() && state.activeAppView === "quote" && state.quoteSessionId === id, id, { timeout: 30000 });
      if (!(await page.locator("#quoteBasisPanel").isVisible())) await page.locator('button[data-side-panel="basis"]').click();
      await page.locator("#quoteBasisPanel").waitFor({ state: "visible", timeout: 30000 });
    };

    // 3. The owner's path (INT-SQAG-202611200003): Dashboard, back with Modify, then Reset Draft without
    // Re-Analyse returns both rows to Confirm, as it does every other line.
    await reopen();
    await expectRows("Include", "Exclude", "Modify did not reopen the standard row decisions");
    await expectBadges({ [PM]: "Include", [WASTE]: "Exclude" }, "reopened decided");
    await resetDraft("Reset Draft after Dashboard and Modify did not return the standard rows to Confirm");

    await reopen();
    await expectBadges({ [PM]: "Confirm", [WASTE]: "Confirm" }, "reopened undecided");

    // 4. The same without leaving the quote; then Re-Analyse keeps both decisions.
    await tickAndX("tick or X did not apply after Reset");
    await resetDraft("Reset Draft did not return the standard rows to Confirm");
    await tickAndX("tick or X did not apply before Re-Analyse");
    // Re-Analyse replaces the analysis snapshot that Reset returns to; wait for the new one.
    await page.evaluate(() => { window.__standardRowsSnapshot = state.originalAnalysisSnapshot; });
    await page.locator("#analyseAgainButton").click();
    const confirmStart = page.locator("#analysisConfirmStartButton");
    await confirmStart.waitFor({ state: "visible", timeout: 30000 });
    await confirmStart.click();
    await page.waitForFunction(() => state.originalAnalysisSnapshot && state.originalAnalysisSnapshot !== window.__standardRowsSnapshot && !appIsBusy(), null, { timeout: 90000 });
    await expectRows("Include", "Exclude", "Re-Analyse lost the standard row decisions or duplicated a row");
    await expectBadges({ [PM]: "Include", [WASTE]: "Exclude" }, "Re-Analyse");
    result.standardServiceBadges = badgeSeen;
    result.standardServiceRows = { newQuote: "PASS", tickAndX: "PASS", dashboardModifyReset: "PASS", reset: "PASS", reAnalyse: "PASS" };
  }`);
// Reload restores the saved decisions, and the generated workbook carries the ticked row at zero only.
replaceOnce('    result.saveReopen = "PASS";', `    result.saveReopen = "PASS";
    const reopenedStandard = await page.evaluate(() => state.quoteBasisSections.flatMap((section) => section.lines || [])
      .filter((line) => String(line.id || "").startsWith("standard-")).map((line) => [line.id, normalizeBasisTag(line.tag)]));
    assert(JSON.stringify(reopenedStandard) === JSON.stringify([["standard-project-management-fee", "Include"], ["standard-waste-disposal", "Exclude"]]),
      "reopen lost the standard row decisions", reopenedStandard);
    const includedRow = await rowFor("standard-project-management-fee");
    assert(includedRow?.authority?.variant === "included" && includedRow.amount === "Included" && !(await rowFor("standard-waste-disposal")), "Output does not hold only the ticked standard row, Included", includedRow);
    result.standardServiceRowsReopen = { decisions: reopenedStandard, output: includedRow.authority.variant };`);
replaceOnce('      { id: "u1-review", description: cases.find((entry) => entry.id === "u1-review").text, quantity: quantityText(1, "lot"), amount: 500 },',
  `      { id: "u1-review", description: cases.find((entry) => entry.id === "u1-review").text, quantity: quantityText(1, "lot"), amount: 500 },
      { id: "standard-project-management-fee", description: "Lump sum for project management", quantity: quantityText(1, "nos"), amount: "Included" },`);
replaceOnce(`      "assert '???' not in xml, 'unresolved marker in workbook'",`, `      "assert '???' not in xml, 'unresolved marker in workbook'",
      "assert 'Waste disposal / Recycling at production venue' not in xml, 'crossed standard row reached the workbook'",
      "assert all(text in xml for text in ('The above quote excludes:', 'Organiser Admin Fees', 'Power sockets which can be directly ordered with connection fees with the organiser', 'Connection fees for electrical fittings')), 'exclusions missing from the workbook'",`);
// The ticked row's amount is the literal text Included, never a formula, and adds nothing to the total.
replaceOnce("abs(float(get(hit[0],'E') or 'nan')-e['amount'])>0.005",
  "(hit[0].get('E') != (e['amount'], None) if isinstance(e['amount'], str) else abs(float(get(hit[0],'E') or 'nan')-e['amount'])>0.005)");
replaceOnce("and get(r,'B')!='Quantity']", "and get(r,'B')!='Quantity' and get(r,'E')!='Included']");
const runtime = path.join(carrierRoot, 'standard-service-rows-carrier.mjs');
await fs.writeFile(runtime, code);
const child = spawn(process.execPath, [runtime, '--lines=8', '--latency-ms=100', '--fresh-analysis', ...process.argv.slice(2)], {
  cwd: root, windowsHide: true, stdio: 'inherit',
  env: { ...process.env, SQAG_OWNER_UAT_EVIDENCE_ROOT: carrierRoot },
});
const exitCode = await new Promise((resolve) => child.once('exit', resolve));
process.exitCode = exitCode ?? 1;
