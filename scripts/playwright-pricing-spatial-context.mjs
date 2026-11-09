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
  const sculptureStatus = sculpture.getByText("Price to be discussed", { exact: true });
  const sculptureTick = () => basisControl(page, "u1-unmatched", '[data-basis-tag]:not([data-basis-tag="Exclude"])');
  const sculptureCross = await basisControl(page, "u1-unmatched", '[data-basis-tag="Exclude"]');
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
  assert(!await sculptureStatus.isVisible() && await page.evaluate(() => state.lineItems.some((row) =>
    row.source_basis_line_id === "u1-unmatched" && Boolean(row.unmatched_pricing_offer) && !row.deferred_pricing)),
    "untouched unmatched row already carries a deferral");
  assert(await sculpture.locator('button').count() === 3, "unmatched row has an extra action");
  await tickAll.click();
  await assertBasisRow(spotlight, "Matched", "include");
  await assertBasisRow(sculpture, "Unmatched", "custom");
  assert(await sculptureStatus.isVisible(), "section tick did not defer eligible unmatched row");
  await crossAll.click();
  await assertBasisRow(spotlight, "Matched", "exclude");
  await assertBasisRow(sculpture, "Unmatched", "exclude");
  assert(!await sculptureStatus.isVisible(), "excluded row still shows a deferral");
  await tickAll.click();
  await assertBasisRow(spotlight, "Matched", "include");
  await assertBasisRow(sculpture, "Unmatched", "custom");
  assert(await sculptureStatus.isVisible(), "section tick did not defer after bulk exclusion");
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
  assert(await page.locator('#basisReviewSurface [aria-label="Unmatched"]').getByText("Kinetic sculpture").isVisible(), "unmatched line rendered in the wrong group");
  assert(await page.locator('#basisReviewSurface [aria-label="Matched"]').getByText("6 inch Spotlight", { exact: false }).first().isVisible(), "resolved reference not in Matched");
  assert(await page.locator('#basisReviewSurface [aria-label="Review"]').getByText("Large wall-mounted video display", { exact: false }).first().isVisible(), "unit conflict not in Review");
  const manualReview = page.locator('#basisReviewSurface [aria-label="Review"] .basis-line-row')
    .filter({ hasText: "Custom reception desk" });
  assert(await manualReview.isVisible() && !await manualReview.getByText("Price to be discussed").isVisible(),
    "ticking a Review row granted a pricing deferral");
  assert(await page.locator('#basisReviewSurface').getByText("Detected booth", { exact: true }).isVisible(), "Detected booth header missing");
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
    assert(!await sculptureStatus.isVisible(), "excluded row still shows a deferral");
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
  await page.waitForFunction(() => !quoteSessionDraftSavePromise);
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
      results.push(basisPricingGroup(line, section) === "Review"
        && !basisUnmatchedOffer(line, basisPricingRow(line, section), section));
    }
    Object.assign(line, original);
    state.pricingReferences = references;
    state.pricingReferenceId = referenceId;
    updateQuoteBasisCard();
    return results;
  });
  assert(editedGroups.every(Boolean), "an edited or re-referenced row retained eligibility", editedGroups);
  // Persist and reopen the legacy shape, so both browser and server must recover
  // the excluded catalogue row from saved evidence rather than a retained item.
  assert(await page.evaluate(async (id) => {
    state.lineItems = state.lineItems.filter((row) => row.source_basis_line_id !== id);
    const saved = await saveQuoteSessionDraftState({ quoteGenerated: true });
    return Boolean(saved);
  }, "u1-excluded-match"), "could not save the legacy excluded draft");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.outputRows.length > 0);
  if (!(await sculpture.isVisible())) await basisTab.click();
  assert(await page.evaluate(() => !state.lineItems.some((row) => row.source_basis_line_id === "u1-excluded-match")),
    "legacy fixture unexpectedly has a retained catalogue row");
  await assertExcludedBasis();
  await (await basisControl(page, "u1-excluded-match", '[data-basis-tag="Include"]')).click();
  assert(await page.evaluate(() => pendingPricingSelections().some((entry) =>
    entry.source_basis_line_id === "u1-excluded-match" && entry.reinclude)), "legacy tick did not request catalogue restoration");
  const editedSelection = await page.evaluate(() => {
    const line = state.quoteBasisSections.flatMap((section) => section.lines).find((entry) => entry.id === "u1-excluded-match");
    const original = line.text;
    line.text += " for another position";
    const selected = pendingPricingSelections().some((entry) => entry.source_basis_line_id === line.id);
    line.text = original;
    return selected;
  });
  assert(!editedSelection, "edited row automatically requested its prior catalogue selection");
  const staleRecovery = await page.evaluate(async (rowId) => {
    const url = "/api/quote-sessions/" + encodeURIComponent(state.quoteSessionId);
    const before = (await fetch(url).then((response) => response.json())).quote_session;
    const payload = buildLineItemNormalizePayload();
    payload.quote_session.draft_state = before.draft_state;
    payload.quote_session.customer_summary = { ...before.customer_summary, project_name: "Synthetic newer tab project" };
    const newer = await fetchPostJsonResponse("/api/quote-sessions", payload);
    if (!newer.ok) throw new Error("Second tab could not save");
    const newerSession = (await newer.json()).quote_session;
    const refused = await refreshLineItemsFromServer();
    const localPending = state.lineItems.some((row) => row.source_basis_line_id === rowId && row.reincluded_catalog_selection);
    const reread = (await fetch(url).then((response) => response.json())).quote_session;
    return refused.status === 409 && localPending && state.pricingDraftVersion === false
      && newerSession.draft_version === reread.draft_version
      && newerSession.draft_version !== before.draft_version;
  }, "u1-excluded-match");
  assert(staleRecovery, "stale browser overwrote a newer unrelated edit or lost the local recovery draft");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
  assert(await page.evaluate(async () => state.pricingDraftVersion === false
    && state.lineItems.some((row) => row.reincluded_catalog_selection)
    && !(await saveQuoteSessionDraftState())
    && (await refreshLineItemsFromServer()).data.status === "conflict"),
    "reload resumed a refused save or lost the recovery draft");
  if (!(await sculpture.isVisible())) await basisTab.click();
  await floodCross.click();
  assert(await page.evaluate(async () => !state.lineItems.some((row) => row.reincluded_catalog_selection)
    && !(await saveQuoteSessionDraftState())), "cancelling the selection released the stale draft for autosave");
  await page.locator("#topbarBrandButton").click();
  await page.locator('.dashboard-session-card[data-quote-session-id="' + excludedSession + '"]').click();
  await page.locator('[data-dashboard-panel-action="modify-session"][data-quote-session-id="' + excludedSession + '"]').click();
  await page.waitForFunction(() => state.activeAppView === "quote" && !state.quoteSessionRestoreBusy);
  if (!(await sculpture.isVisible())) await basisTab.click();
  await assertExcludedBasis();
  await (await basisControl(page, "u1-excluded-match", '[data-basis-tag="Include"]')).click();
  result.staleRecovery = "PASS";
  assert(!await sculptureStatus.isVisible(), "reopened row has a deferral before ticking");
  await (await sculptureTick()).click();
  await assertBasisRow(sculpture, "Unmatched", "custom");
  assert(await sculptureStatus.isVisible(), "single tick did not defer the re-included row");
  const mixedResponse = page.waitForResponse((response) =>
    new URL(response.url()).pathname === "/api/quote-sessions/pricing-selections");
  await page.locator("#sideNextButton").click();
  const mixed = await mixedResponse;
  const outcomes = (await mixed.json()).selections || [];
  const submitted = mixed.request().postDataJSON().pricing_selections;
  assert(mixed.status() === 200 && submitted.some((entry) => entry.source_basis_line_id === "u1-excluded-match" && entry.reinclude)
    && submitted.some((entry) => entry.source_basis_line_id === "u1-video" && !entry.reinclude)
    && outcomes.some((entry) => entry.source_basis_line_id === "u1-excluded-match" && entry.status === "selected")
    && outcomes.some((entry) => entry.source_basis_line_id === "u1-video" && entry.status === "refused"
      && entry.reason === "unit_review_required"), "mixed selection did not return both outcomes", outcomes);
  result.mixedSelectionStatus = mixed.status();
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 30000 });
  await page.waitForFunction(() => !appIsBusy());
  const reopenedOutput = await page.evaluate(() => {
    const matched = state.outputRows.find((row) => row.source_basis_line_id === "u1-excluded-match");
    const spotlight = state.outputRows.find((row) => row.source_basis_line_id === "u1-confirmed");
    const deferredIndex = state.outputRows.findIndex((row) => row.source_basis_line_id === "u1-unmatched");
    const deferred = state.outputRows[deferredIndex];
    const cell = (selector) => document.querySelector(selector)?.textContent.trim();
    return {
      catalogue: matched?.pricing_authority?.variant === "catalog",
      quoted: outputCellDisplayValue(matched, "quoted_unit_price"), amount: outputCellDisplayValue(matched, "amount"),
      spotlight: spotlight?.pricing_authority?.variant === "catalog" && [
        outputCellDisplayValue(spotlight, "quoted_unit_price"), outputCellDisplayValue(spotlight, "amount"),
      ],
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
  const spotlightPrice = byName("6 inch Spotlight").sale_unit_price;
  assert(reopenedOutput.spotlight?.[0] === cents(spotlightPrice * fxRate).toFixed(2)
    && reopenedOutput.spotlight?.[1] === cents(7 * spotlightPrice * fxRate).toFixed(2),
    "catalogue Spotlight price changed after the unmatched tick", reopenedOutput.spotlight);
  result.tickOnlyDeferred = "PASS";
  result.reincludedPricing = "PASS";
  result.excludedReopen = "PASS";`);
replaceOnce('    result.saveReopen = "PASS";', `
    result.saveReopen = "PASS";
    const deferredAfterReload = await rowFor("u1-unmatched");
    assert(deferredAfterReload?.authority?.variant === "none" && deferredAfterReload.amount === "Price to be discussed",
      "deferred price did not survive Confirm and reopen", deferredAfterReload);
    result.deferredReopened = true;`);
replaceOnce('trusted: pricingAuthorityPrice(outputPriceAuthority(row)) !== null', 'trusted: outputPricingIsDeferred(row) || pricingAuthorityPrice(outputPriceAuthority(row)) !== null');
replaceOnce('    // Protected XLSX: generated workbook carries the admitted amounts.', `
    const deferredRow = page.locator('tr[data-output-row]').filter({ hasText: 'Kinetic sculpture' });
    const deferredCell = deferredRow.locator('[data-output-price-entry="quote"]');
    const deferredEditor = deferredCell.locator('[data-output-editor-field="unit_price_override"]');
    const deferredAmount = deferredRow.locator('[data-output-label="Amount"]');
    const beforeDismissal = await page.evaluateHandle(() => ({
      revision: state.outputRevision,
      rows: JSON.stringify(state.outputRows),
      lineItems: JSON.stringify(state.lineItems),
    }));
    async function assertDeferredEditorState(open) {
      assert(await deferredEditor.isVisible() === open, open ? 'deferred editor did not open' : 'deferred editor stayed open');
      assert(await deferredAmount.innerText() === 'Price to be discussed', 'deferred amount changed');
      if (!open) assert(await deferredCell.innerText() === '-', 'deferred dash was not restored');
      assert(await page.evaluate((before) => state.outputRevision === before.revision
        && JSON.stringify(state.outputRows) === before.rows && JSON.stringify(state.lineItems) === before.lineItems,
        beforeDismissal), 'dismissal changed quote rows, receipt or revision');
    }
    await deferredCell.click();
    await assertDeferredEditorState(true);
    assert(await deferredEditor.inputValue() === '', 'deferred editor is not empty');
    assert(await page.evaluate(() => commitActiveOutputEditor({ commit: false })) === 'unchanged', 'blank deferred editor blocked preflight');
    await assertDeferredEditorState(true);
    await deferredEditor.press('Enter');
    await assertDeferredEditorState(false);
    await deferredCell.click();
    await deferredAmount.click();
    await assertDeferredEditorState(false);
    await deferredCell.click();
    await deferredEditor.fill('1.005');
    await deferredEditor.press('Enter');
    assert(await deferredEditor.getAttribute('aria-invalid') === 'true', 'invalid typed price was admitted');
    assert(await page.evaluate(() => commitActiveOutputEditor({ commit: false })) === 'blocked', 'invalid typed price passed preflight');
    await assertDeferredEditorState(true);
    await deferredEditor.press('Escape');
    await assertDeferredEditorState(false);
    await deferredCell.click();
    await assertDeferredEditorState(true);
    await beforeDismissal.dispose();
    // Download directly with the blank editor still open.
    // Protected XLSX: generated workbook carries the admitted amounts.`);
replaceOnce('    const download = await downloadPromise;', `
    const download = await downloadPromise;
    assert(!await deferredEditor.isVisible() && await deferredCell.innerText() === '-'
      && await deferredAmount.innerText() === 'Price to be discussed', 'Download did not dismiss the blank deferred editor');
    result.deferredEditorDismissal = 'PASS';
`);
replaceOnce('    // The analysis seal is opaque transport', `
    expectedRows.push({ id: "u1-unmatched", description: "Kinetic sculpture", quantity: "2 nos", amount: "Price to be discussed" });
    expectedRows.push({ id: "u1-excluded-match", description: byName("LED Flood Light").description,
      quantity: "3 nos", amount: cents(3 * floodPrice * fxRate) });
    // The analysis seal is opaque transport`);
replaceOnce('[state.originalAnalysisSnapshot?.pricing_analysis_seal?.mac].filter(Boolean)',
  '[state.originalAnalysisSnapshot?.pricing_analysis_seal?.mac, state.lineItems.find((row) => row.source_basis_line_id === "u1-unmatched")?.deferred_pricing].filter(Boolean)');
replaceOnce('assert(receipts.length === 1, "analysis seal missing from the browser draft", receipts.length);',
  'assert(receipts.length === 2, "analysis seal or deferral receipt missing from the browser draft", receipts.length);');
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
      "Mixed HTTP 200 partial refusal was not retained as a finite row issue", refusalReview);
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
    const { index: videoIndex } = await rowFor("u1-video");
    await page.locator('[data-output-row="' + videoIndex + '"][data-output-edit-field="unit_price_override"][data-output-denomination="quote"]').first().click();
    await page.locator('[data-output-editor-field="unit_price_override"]').fill("12");
    await page.locator('[data-output-editor-field="unit_price_override"]').press("Enter");
    assert(await page.evaluate(() => !outputRowsValid().errors.some((error) => error.includes("different quantity unit"))),
      "resolved manual price retained a stale unit refusal");
    result.spatialPartialRefusal = { refusalReview, mixedStatus: result.mixedSelectionStatus };
    const resolvedVideo = await rowFor("u1-video");
    assert(resolvedVideo.authority?.variant === "manual" && resolvedVideo.quoted === "12.00",
      "manual review price did not resolve the mixed refusal", resolvedVideo);
    result.spatialPartialRefusal.resolvedVideo = resolvedVideo;
    await writeJson("u1-pricing.json", pricing);`);
const runtime = path.join(carrierRoot, 'pricing-spatial-carrier.mjs');
await fs.writeFile(runtime, code);
const child = spawn(process.execPath, [runtime, '--lines=0', '--latency-ms=100', '--fresh-analysis', ...process.argv.slice(2)], {
  cwd: root, windowsHide: true, stdio: 'inherit',
  env: { ...process.env, SQAG_OWNER_UAT_EVIDENCE_ROOT: carrierRoot },
});
const exitCode = await new Promise((resolve) => child.once('exit', resolve));
process.exitCode = exitCode ?? 1;
