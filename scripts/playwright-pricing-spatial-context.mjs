// Focused extension of the existing synthetic owner-UAT carrier. No live provider is used.
// Anchor checks fail closed if the shared carrier changes instead of silently skipping coverage.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { freshBrowserRun } from './synthetic-browser-boundary.mjs';
const root = fileURLToPath(new URL('..', import.meta.url));
const evidence = process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(root, '_logs/browser/pricing-spatial-context');
const carrierRoot = await freshBrowserRun(evidence, 'carrier-');
let code = await fs.readFile(path.join(root, 'scripts/playwright-owner-uat-basis-repair.mjs'), 'utf8');
function replaceOnce(before, after) {
  if (code.split(before).length !== 2) throw new Error('Shared pricing carrier anchor changed: ' + before.slice(0, 90));
  code = code.replace(before, after);
}
replaceOnce('const sourceRoot = fileURLToPath(new URL("..", import.meta.url));', 'const sourceRoot = process.cwd();');
replaceOnce('import { signIn } from "./internal-password-browser-test.mjs";',
  `import { signIn } from ${JSON.stringify(pathToFileURL(path.join(root, 'scripts/internal-password-browser-test.mjs')).href)};`);
replaceOnce('import path from "node:path";', 'import path from "node:path";\nimport os from "node:os";');
replaceOnce('import { SyntheticBrowserBoundary, freshBrowserRun } from "./synthetic-browser-boundary.mjs";',
  `import { SyntheticBrowserBoundary, freshBrowserRun } from ${JSON.stringify(pathToFileURL(path.join(root, 'scripts/synthetic-browser-boundary.mjs')).href)};`);
replaceOnce('const dirs = Object.fromEntries(["data", "output", "temp", "pricing", "profiles", "logs", "downloads"].map((name) => [name, path.join(runRoot, name)]));', `
const nativeRunRoot = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-spatial-"));
const dirs = Object.fromEntries(["data", "output", "temp", "pricing", "profiles", "logs", "downloads"].map((name) => [name,
  path.join(["logs", "downloads"].includes(name) ? runRoot : nativeRunRoot, name)]));`);
replaceOnce('Bar Stool seating around the display', 'Bar Stools at Round Tables.');
replaceOnce('const cases = [', `const cases = [
  { id: "u1-excluded-match", kind: "confirm", section: "Lighting and Electrical", text: "LED Flood Light", quantity: 3, unit: "nos", claim: "LED Flood Light" },
  { id: "u1-unmatched", kind: "unmatched", section: "Lighting and Electrical", text: "Kinetic sculpture", quantity: 2, unit: "nos" },`);
replaceOnce('  // Resolve any remaining server-added review line', `
  const spotlightTick = await basisControl(page, "u1-confirmed", '[data-basis-tag="Include"]');
  const spotlightCross = await basisControl(page, "u1-confirmed", '[data-basis-tag="Exclude"]');
  const sectionId = await spotlightTick.getAttribute("data-basis-section");
  const spotlight = spotlightTick.locator("xpath=ancestor::li[1]");
  const sculpture = page.locator('.basis-line-row').filter({ hasText: "Kinetic sculpture" });
  const sectionActions = page.locator('#basisReviewSurface [data-basis-section="' + sectionId + '"][data-basis-section-action]');
  const tickAll = sectionActions.and(page.locator('[data-basis-section-action="Include"]')).first();
  const crossAll = sectionActions.and(page.locator('[data-basis-section-action="Exclude"]')).first();
  async function assertBasisRow(row, group, tag) {
    assert(await row.isVisible(), group + " row disappeared after a basis action");
    assert(await row.evaluate((element, expected) =>
      element.closest(".basis-review-grid").getAttribute("aria-label") === expected.group
      && element.classList.contains("basis-line-" + expected.tag), { group, tag }),
      "basis row lost its pricing group or selection styling", { group, tag });
  }
  await assertBasisRow(spotlight, "Matched", "include");
  await tickAll.click();
  await assertBasisRow(spotlight, "Matched", "include");
  await crossAll.click();
  await assertBasisRow(spotlight, "Matched", "exclude");
  await assertBasisRow(sculpture, "Unmatched", "exclude");
  assert(await sculpture.locator('[data-basis-defer-pricing]').count() === 0, "excluded row still offers Keep");
  await tickAll.click();
  await assertBasisRow(spotlight, "Matched", "include");
  assert(await sculpture.evaluate((row) => !row.classList.contains("basis-line-exclude")), "Tick all missed the other pricing group");
  await spotlightCross.click();
  await assertBasisRow(spotlight, "Matched", "exclude");
  assert(await sculpture.evaluate((row) => !row.classList.contains("basis-line-exclude")), "single X excluded a different row");
  await spotlightTick.click();
  await assertBasisRow(spotlight, "Matched", "include");
  assert(await sectionActions.count() === 2, "one section rendered duplicate bulk controls across groups");
  assert(await tickAll.getAttribute("title") === "Accept all review lines"
    && await crossAll.getAttribute("title") === "Exclude all review lines", "original bulk tooltips missing");
  const sectionTitle = await tickAll.locator('xpath=ancestor::div[contains(@class, "basis-section-heading")]/h4').innerText();
  assert(await tickAll.getAttribute("aria-label") === "Accept all review lines in " + sectionTitle
    && await crossAll.getAttribute("aria-label") === "Exclude all review lines in " + sectionTitle, "bulk labels do not describe section-wide scope");
  assert(await tickAll.innerText() === await spotlightTick.innerText() && await crossAll.innerText() === await spotlightCross.innerText(),
    "bulk controls do not use the original row glyphs");
  assert(await tickAll.locator('xpath=..').locator('.basis-section-action-spacer').count() === 1,
    "bulk actions lost the alignment spacer for the Re column");
  result.basisActions = "PASS";
  const keep = await basisControl(page, "u1-unmatched", "[data-basis-defer-pricing]");
  assert(await keep.isVisible(), "unmatched physical row has no Keep control", await page.evaluate(() =>
    state.lineItems.find((row) => row.source_basis_line_id === "u1-unmatched")));
  assert(await page.locator('#basisReviewSurface [aria-label="Unmatched"]').getByText("Kinetic sculpture").isVisible(), "unmatched line rendered in the wrong group");
  assert(await page.locator('#basisReviewSurface [aria-label="Matched"]').getByText("6 inch Spotlight", { exact: false }).first().isVisible(), "resolved reference not in Matched");
  assert(await page.locator('#basisReviewSurface [aria-label="Review"]').getByText("Large wall-mounted video display", { exact: false }).first().isVisible(), "unit conflict not in Review");
  assert(await page.locator('#basisReviewSurface').getByText("Detected booth", { exact: true }).isVisible(), "Detected booth header missing");
  await keep.click();
  assert(await keep.innerText() === "Undo keep", "keep decision was not shown");
  await keep.click();
  assert(await keep.innerText() === "Keep as unpriced", "keep decision could not be undone");
  await keep.click();
  const sculptureCross = await basisControl(page, "u1-unmatched", '[data-basis-tag="Exclude"]');
  const floodCross = await basisControl(page, "u1-excluded-match", '[data-basis-tag="Exclude"]');
  const flood = floodCross.locator("xpath=ancestor::li[1]");
  await sculptureCross.click();
  await floodCross.click();
  // Resolve any remaining server-added review line`);
replaceOnce('  await writeJson("timings-raw.json", { timings, confirm: result.confirm });', `
  await writeJson("timings-raw.json", { timings, confirm: result.confirm });
  const excludedIds = ["u1-unmatched", "u1-excluded-match"];
  async function assertExcludedOutput() {
    assert(await page.evaluate((ids) =>
      state.outputRows.every((row) => !ids.includes(row.source_basis_line_id)), excludedIds),
      "excluded pricing row reached Output");
  }
  async function assertExcludedBasis() {
    await assertBasisRow(flood, "Matched", "exclude");
    await assertBasisRow(sculpture, "Unmatched", "exclude");
    assert(await sculpture.locator('[data-basis-defer-pricing]').count() === 0, "excluded row offers Keep");
  }
  const basisTab = page.locator('button[data-side-panel="basis"]').first();
  await assertExcludedOutput();
  await basisTab.click();
  await assertExcludedBasis();
  const excludedSession = await page.evaluate(() => state.quoteSessionId);
  await page.waitForFunction(async ({ id, ids }) => {
    const detail = await fetch("/api/quote-sessions/" + encodeURIComponent(id)).then((response) => response.json());
    const draft = detail.quote_session?.draft_state;
    return draft?.basisConfirmed && ids.every((rowId) =>
      draft.lineItems.some((row) => row.source_basis_line_id === rowId)
      && !draft.outputRows.some((row) => row.source_basis_line_id === rowId));
  }, { id: excludedSession, ids: excludedIds }, { timeout: 15000, polling: 500 });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.outputRows.length > 0);
  await assertExcludedOutput();
  if (!(await sculpture.isVisible())) await basisTab.click();
  await assertExcludedBasis();
  await (await basisControl(page, "u1-excluded-match", '[data-basis-tag="Include"]')).click();
  await assertBasisRow(flood, "Matched", "include");
  await floodCross.click();
  await assertExcludedBasis();
  // Exercise an older saved shape with no active excluded rows and verify the snapshot
  // cannot override a current edit, even when the edited row is absent from the active set.
  const recovery = await page.evaluate((ids) => {
    const previous = state.lineItems;
    const section = state.quoteBasisSections.find((entry) => entry.lines.some((line) => line.id === ids[0]));
    const line = section.lines.find((entry) => entry.id === ids[0]);
    const matched = section.lines.find((entry) => entry.id === ids[1]);
    const unpriced = previous.find((row) => row.source_basis_line_id === ids[0]);
    state.lineItems = previous.map((row) => row === unpriced ? { ...row, description: "Edited active sculpture" } : row);
    const activeEdit = basisPricingGroup(line, section);
    state.lineItems = previous.filter((row) => !ids.includes(row.source_basis_line_id));
    const recovered = [basisPricingGroup(line, section), basisPricingGroup(matched, section)];
    const stale = [
      basisPricingGroup({ ...line, id: "edited-sculpture" }, section),
      basisPricingGroup({ ...line, text: "Edited sculpture" }, section),
      basisPricingGroup({ ...line, unit: "sets" }, section),
    ];
    state.lineItems = previous;
    return { activeEdit, recovered, stale };
  }, excludedIds);
  assert(recovery.activeEdit === "Review" && JSON.stringify(recovery.recovered) === '["Unmatched","Matched"]'
    && recovery.stale.every((group) => group === "Review"), "snapshot recovery accepted an edit or lost classification", recovery);
  await (await basisControl(page, "u1-unmatched", '[data-basis-tag]:not([data-basis-tag="Exclude"])')).click();
  const editedGroups = await page.evaluate(() => {
    const section = state.quoteBasisSections.find((entry) => entry.lines.some((line) => line.id === "u1-unmatched"));
    const line = section.lines.find((entry) => entry.id === "u1-unmatched");
    const original = { ...line };
    const referenceId = state.pricingReferenceId;
    const references = state.pricingReferences;
    const replacement = { ...currentPricingReference(), id: "other-synthetic-reference" };
    const results = [];
    for (const edit of [{ id: "edited-sculpture" }, { text: "Edited sculpture" }, { unit: "sets" }, {}]) {
      Object.assign(line, original, edit);
      if (!Object.keys(edit).length) {
        state.pricingReferences = [...references, replacement];
        state.pricingReferenceId = replacement.id;
      }
      updateQuoteBasisCard();
      const row = [...document.querySelectorAll("#basisReviewSurface .basis-line-row")].find((element) =>
        element.textContent.includes(line.text));
      results.push(row?.closest(".basis-review-grid")?.getAttribute("aria-label") === "Review"
        && !row.querySelector("[data-basis-defer-pricing]"));
    }
    Object.assign(line, original);
    state.pricingReferences = references;
    state.pricingReferenceId = referenceId;
    updateQuoteBasisCard();
    return results;
  });
  assert(editedGroups.every(Boolean), "an edited or re-referenced row retained the Keep control", editedGroups);
  await (await basisControl(page, "u1-excluded-match", '[data-basis-tag="Include"]')).click();
  const editedSelection = await page.evaluate(() => {
    const line = state.quoteBasisSections.flatMap((section) => section.lines).find((entry) => entry.id === "u1-excluded-match");
    const original = line.text;
    line.text += " for another position";
    const selected = pendingPricingSelections().some((entry) => entry.source_basis_line_id === line.id);
    line.text = original;
    return selected;
  });
  assert(!editedSelection, "edited row automatically requested its prior catalogue selection");
  assert(await keep.innerText() === "Keep as unpriced", "re-included row did not offer a fresh Keep decision");
  await keep.click();
  assert(await keep.innerText() === "Undo keep", "re-included row could not be kept");
  await page.locator("#sideNextButton").click();
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 30000 });
  await page.waitForFunction(() => !appIsBusy());
  const reopenedOutput = await page.evaluate(() => {
    const matched = state.outputRows.find((row) => row.source_basis_line_id === "u1-excluded-match");
    const deferredIndex = state.outputRows.findIndex((row) => row.source_basis_line_id === "u1-unmatched");
    const deferred = state.outputRows[deferredIndex];
    const cell = (selector) => document.querySelector(selector)?.textContent.trim();
    return {
      catalogue: matched?.pricing_authority?.variant === "catalog",
      quoted: outputCellDisplayValue(matched, "quoted_unit_price"), amount: outputCellDisplayValue(matched, "amount"),
      deferred: outputPricingIsDeferred(deferred),
      deferredUnit: cell('[data-output-price-entry="quote"][data-output-row="' + deferredIndex + '"] .output-cell-text'),
      deferredAmount: document.querySelector('[data-output-price-entry="quote"][data-output-row="' + deferredIndex + '"]')
        ?.closest('tr')?.querySelector('[data-output-label="Amount"]')?.textContent.trim(),
    };
  });
  const floodPrice = byName("LED Flood Light").sale_unit_price;
  assert(reopenedOutput.catalogue && reopenedOutput.quoted === cents(floodPrice * fxRate).toFixed(2)
    && reopenedOutput.amount === cents(3 * floodPrice * fxRate).toFixed(2), "re-included catalogue FX or amount missing", reopenedOutput);
  assert(reopenedOutput.deferred && reopenedOutput.deferredUnit === "-" && reopenedOutput.deferredAmount === "Price to be discussed",
    "re-included deferred price rendered incorrectly", reopenedOutput);
  result.reincludedPricing = "PASS";
  result.excludedReopen = "PASS";`);
replaceOnce('    result.saveReopen = "PASS";', `
    result.saveReopen = "PASS";
    const deferredAfterReload = await rowFor("u1-unmatched");
    assert(deferredAfterReload?.authority?.variant === "none" && deferredAfterReload.amount === "Price to be discussed",
      "deferred price did not survive Confirm and reopen", deferredAfterReload);
    result.deferredReopened = true;`);
replaceOnce('trusted: pricingAuthorityPrice(outputPriceAuthority(row)) !== null', 'trusted: outputPricingIsDeferred(row) || pricingAuthorityPrice(outputPriceAuthority(row)) !== null');
replaceOnce('    // The analysis seal is opaque transport', `
    expectedRows.push({ id: "u1-unmatched", description: "Kinetic sculpture", quantity: "2 nos", amount: "Price to be discussed" });
    expectedRows.push({ id: "u1-excluded-match", description: byName("LED Flood Light").description,
      quantity: "3 nos", amount: cents(3 * floodPrice * fxRate) });
    // The analysis seal is opaque transport`);
replaceOnce("abs(float(get(hit[0],'E') or 'nan')-e['amount'])>0.005",
  "(hit[0].get('E') != (e['amount'], None) if isinstance(e['amount'], str) else abs(float(get(hit[0],'E') or 'nan')-e['amount'])>0.005)");
replaceOnce("and get(r,'B')!='Quantity']", "and get(r,'B')!='Quantity' and get(r,'E')!='Price to be discussed']");
replaceOnce('    result.xlsx = { status: "PASS", expectedRows, validator: checkOut.trim() };', `
    const workbookTotals = JSON.parse(checkOut).totals;
    const savedTotals = detail.quote_session.commercials;
    assert(savedTotals?.subtotal === workbookTotals.Total && savedTotals?.tax_amount === workbookTotals["GST 9%"]
      && savedTotals?.grand_total === workbookTotals["Total including GST"], "saved and workbook totals disagree", { savedTotals, workbookTotals });
    result.xlsx = { status: "PASS", expectedRows, validator: checkOut.trim() };`);
replaceOnce('  // U2: Confirm Quotation Basis must show its preparing state before slow network work completes.', `
  const beforeConfirm = await page.evaluate(() => {
    const row = state.lineItems.find((entry) => entry.source_basis_line_id === "u1-count");
    return { variant: row?.pricing_authority?.variant, price: pricingAuthorityPrice(row?.pricing_authority) };
  });
  assert(!["catalog", "manual", "included"].includes(beforeConfirm.variant) && beforeConfirm.price === null,
    "candidate selection became quoted pricing before Confirm", beforeConfirm);
  result.spatialBeforeConfirm = beforeConfirm;
  // U2: Confirm Quotation Basis must show its preparing state before slow network work completes.`);
replaceOnce('    await writeJson("u1-pricing.json", pricing);', `
    const refusalReview = await page.evaluate(() => ({
      issues: state.pricingIssues,
      errors: outputRowsValid().errors,
      visible: document.querySelector("#pricingReviewMessages").innerText,
    }));
    assert(refusalReview.issues.some((issue) => issue.source_basis_line_id === "u1-video" && issue.reason === "unit_review_required"),
      "HTTP 200 partial refusal was not retained as a finite row issue", refusalReview);
    assert(refusalReview.errors.some((error) => error.includes("different quantity unit")) && refusalReview.visible.includes("different quantity unit"),
      "unit refusal reason was not rendered in the existing review area", refusalReview);
    await page.screenshot({ path: path.join(runRoot, "pricing-refusal.png"), fullPage: true });
    const finiteControls = await page.evaluate(() => {
      const row = state.outputRows.find((entry) => entry.source_basis_line_id === "u1-video");
      const duplicate = pricingSelectionReviewMessage(row, [row, { ...row }]);
      const saved = state.pricingIssues;
      state.pricingIssues = [{ source_basis_line_id: row.source_basis_line_id, reason: "<script>untrusted</script>" }];
      const unknown = pricingSelectionReviewMessage(row, [row]);
      state.pricingIssues = saved;
      return { duplicate, unknown };
    });
    assert(finiteControls.duplicate === "" && finiteControls.unknown === "", "ambiguous ids or unknown reasons escaped the finite wording map", finiteControls);
    const video = await rowFor("u1-video");
    await page.locator('[data-output-row="' + video.index + '"][data-output-edit-field="unit_price_override"][data-output-denomination="quote"]').first().click();
    await page.locator('[data-output-editor-field="unit_price_override"]').fill("12");
    await page.locator('[data-output-editor-field="unit_price_override"]').press("Enter");
    const resolvedVideo = await rowFor("u1-video");
    assert(resolvedVideo.authority?.variant === "manual" && resolvedVideo.quoted === "12.00",
      "manual review price did not resolve refused selection", resolvedVideo);
    assert(!(await page.evaluate(() => outputRowsValid().errors)).some((error) => error.includes("different quantity unit")),
      "resolved manual price retained a stale unit refusal");
    result.spatialPartialRefusal = { refusalReview, resolvedVideo };
    await writeJson("u1-pricing.json", pricing);`);
const runtime = path.join(carrierRoot, 'pricing-spatial-carrier.mjs');
await fs.writeFile(runtime, code);
const child = spawn(process.execPath, [runtime, '--lines=0', '--latency-ms=100', '--fresh-analysis', ...process.argv.slice(2)], {
  cwd: root, windowsHide: true, stdio: 'inherit',
  env: { ...process.env, SQAG_OWNER_UAT_EVIDENCE_ROOT: carrierRoot },
});
const exitCode = await new Promise((resolve) => child.once('exit', resolve));
process.exitCode = exitCode ?? 1;
