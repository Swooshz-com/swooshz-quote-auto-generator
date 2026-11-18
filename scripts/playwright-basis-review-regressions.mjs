// Quote Basis group-block regressions (#353 follow-up). Extends the shared synthetic owner-UAT carrier;
// no live provider, live data or paid AI is used. Anchor checks fail closed if the carrier changes.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { freshBrowserRun } from './synthetic-browser-boundary.mjs';
const root = fileURLToPath(new URL('..', import.meta.url));
const evidence = process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(root, '_logs/browser/basis-review-regressions');
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
// One section (Lighting and Electrical) lands in all three pricing groups, plus blank-quantity catalogue picks.
replaceOnce('const cases = [', `const cases = [
  { id: "g-matched", kind: "confirm", section: "Lighting and Electrical", text: "LED Flood Light", quantity: 3, unit: "nos", claim: "LED Flood Light" },
  { id: "g-unmatched", kind: "unmatched", section: "Lighting and Electrical", text: "Kinetic sculpture", quantity: 2, unit: "nos" },
  { id: "g-review", kind: "review", section: "Lighting and Electrical", text: "Bespoke neon signage with custom lettering", quantity: 1, unit: "lot", rawUnit: "" },
  { id: "q-blank-graphic", kind: "blank", section: "Graphics and Printing", text: "[ sqm Printed Wall Graphic ] - Printed graphic on rear feature wall, size pending approval", quantity: null, unit: "" },
  { id: "q-blank-unknown", kind: "blank", section: "Graphics and Printing", text: "[ sqm Holographic Wall Wrap ] - Side wall wrap, size pending approval", quantity: null, unit: "" },`);
// The AI names a catalogue item but measures nothing, so it emits no line item for those lines.
replaceOnce('const scenarioPath = path.join(runRoot, "scenario.json");', `lineItems.splice(0, lineItems.length, ...lineItems.filter((item) => !String(item.source_basis_line_id || "").startsWith("q-blank")));
const scenarioPath = path.join(runRoot, "scenario.json");`);
replaceOnce('line_items: lineItems } }', 'line_items: lineItems, project: { booth_width: 9, booth_depth: 10.5 } } }');
replaceOnce('try {\n  server = startServer();', 'carrier: try {\n  server = startServer();');
replaceOnce('  result.basisLines = draftLines.length;', `  result.basisLines = draftLines.length;
  {
    const surface = page.locator("#basisReviewSurface");
    const blockOf = (lineId) => page.evaluate((id) => {
      for (const section of state.quoteBasisSections) {
        const index = (section.lines || []).findIndex((line) => line.id === id);
        if (index >= 0) return { section: section.id, index };
      }
      return null;
    }, lineId);
    const rowFor = async (lineId) => {
      const target = await blockOf(lineId);
      assert(target, "basis line missing", lineId);
      return surface.locator('[data-basis-section="' + target.section + '"][data-basis-line-index="' + target.index + '"][data-basis-tag="Exclude"]').locator("xpath=ancestor::li[1]");
    };
    const tagOf = (lineId) => page.evaluate((id) => normalizeBasisTag(state.quoteBasisSections.flatMap((section) => section.lines).find((line) => line.id === id)?.tag), lineId);
    const groupOf = (row) => row.evaluate((element) => element.closest(".basis-review-grid").getAttribute("aria-label"));

    // 1. Every rendered section block in every group has its own tick-all and X.
    const blocks = await surface.locator(".basis-review-item").evaluateAll((items) => items.map((item) => ({
      group: item.closest(".basis-review-grid").getAttribute("aria-label"),
      title: item.querySelector(".basis-section-heading h4").textContent.trim(),
      include: item.querySelectorAll('.basis-section-heading [data-basis-section-action="Include"]').length,
      exclude: item.querySelectorAll('.basis-section-heading [data-basis-section-action="Exclude"]').length,
      groups: [...item.querySelectorAll("[data-basis-section-action]")].map((button) => button.dataset.basisSectionGroup),
      labels: [...item.querySelectorAll("[data-basis-section-action]")].map((button) => button.getAttribute("aria-label")),
    })));
    const lighting = blocks.filter((block) => block.title === "Lighting and Electrical").map((block) => block.group);
    assert(JSON.stringify(lighting) === '["Matched","Review","Unmatched"]', "fixture section does not span all three groups", blocks);
    assert(blocks.length > 3 && blocks.every((block) => block.include === 1 && block.exclude === 1
      && block.groups.every((group) => group === block.group)
      && block.labels[0] === "Accept all " + block.group + " lines in " + block.title
      && block.labels[1] === "Exclude all " + block.group + " lines in " + block.title), "a section block lacks its own scoped controls", blocks);
    result.sectionBlocks = blocks.map((block) => block.group + ":" + block.title);

    // 2. A later block's X changes only that block's lines; its tick restores only them.
    const matched = await rowFor("g-matched");
    const review = await rowFor("g-review");
    const unmatched = await rowFor("g-unmatched");
    assert(await groupOf(matched) === "Matched" && await groupOf(review) === "Review" && await groupOf(unmatched) === "Unmatched", "fixture rows are in the wrong groups");
    const blockAction = (group, action) => surface.locator('.basis-review-grid[aria-label="' + group + '"] .basis-review-item')
      .filter({ has: page.locator("h4", { hasText: /^Lighting and Electrical$/ }) })
      .locator('[data-basis-section-action="' + action + '"]');
    const before = { matched: await tagOf("g-matched"), review: await tagOf("g-review"), unmatched: await tagOf("g-unmatched") };
    await blockAction("Unmatched", "Exclude").click();
    assert(await tagOf("g-unmatched") === "Exclude" && await tagOf("g-matched") === before.matched && await tagOf("g-review") === before.review,
      "Unmatched block X changed lines outside its block", { before, after: { matched: await tagOf("g-matched"), review: await tagOf("g-review"), unmatched: await tagOf("g-unmatched") } });
    await blockAction("Review", "Exclude").click();
    assert(await tagOf("g-review") === "Exclude" && await tagOf("g-matched") === before.matched, "Review block X changed the Matched block");
    await blockAction("Matched", "Exclude").click();
    assert(await tagOf("g-matched") === "Exclude", "Matched block X did not exclude its line");
    await blockAction("Review", "Include").click();
    assert(await tagOf("g-review") !== "Exclude" && await tagOf("g-matched") === "Exclude" && await tagOf("g-unmatched") === "Exclude",
      "Review block tick changed lines outside its block");
    await blockAction("Unmatched", "Include").click();
    await blockAction("Matched", "Include").click();
    assert(await tagOf("g-matched") === "Include" && await tagOf("g-unmatched") !== "Exclude", "block ticks did not restore their lines");
    assert(await groupOf(await rowFor("g-matched")) === "Matched" && await groupOf(await rowFor("g-unmatched")) === "Unmatched", "block actions moved rows between groups");
    result.blockScopedActions = "PASS";

    // 3. Detected booth shows the structured booth size; it is hidden, never empty, when nothing is known.
    const boothBox = surface.locator(".basis-visual-display");
    assert(await boothBox.count() === 1 && (await boothBox.innerText()).includes("Booth size 9m x 10.5m (94.5 sqm)"), "Detected booth lacks the structured booth size", await boothBox.allInnerTexts());
    const unknownBooth = await page.evaluate(() => {
      const saved = state.boothDimensions;
      state.boothDimensions = { ...DEFAULT_BOOTH_DIMENSIONS };
      updateQuoteBasisCard("edited");
      const boxes = [...document.querySelectorAll("#basisReviewSurface .basis-visual-display")].map((box) => box.innerText);
      state.boothDimensions = saved;
      updateQuoteBasisCard("edited");
      return boxes;
    });
    assert(unknownBooth.length === 0 || unknownBooth.every((text) => text.replace("Detected booth", "").trim()), "empty Detected booth box rendered", unknownBooth);
    result.detectedBooth = { structured: (await boothBox.innerText()).trim(), unknownDimensions: unknownBooth };

    // 4. Click latency on the 60+ line basis: line tick/X and section-block X stay inside a budget,
    // and rows the click did not change keep their DOM nodes (no full rebuild).
    const latency = [];
    const probeRow = async () => (await rowFor("g-matched")).elementHandle();
    const fillers = cases.filter((entry) => entry.kind === "filler").slice(0, 12);
    let reused = 0;
    let probes = 0;
    for (const [index, entry] of fillers.entries()) {
      const before = await probeRow();
      const control = await basisControl(page, entry.id, index % 2 ? '[data-basis-tag="Exclude"]' : "[data-basis-tag]:not([data-basis-tag='Exclude'])");
      await timedClick(page, control, index % 2 ? "line_cross" : "line_tick", latency);
      probes += 1;
      if (await before.evaluate((node, after) => node.isConnected && node === after, await probeRow())) reused += 1;
    }
    for (const [group, action] of [["Review", "Exclude"], ["Review", "Include"], ["Unmatched", "Exclude"], ["Unmatched", "Include"]]) {
      const before = await probeRow();
      await timedClick(page, blockAction(group, action), "section_" + action.toLowerCase(), latency);
      probes += 1;
      if (await before.evaluate((node, after) => node.isConnected && node === after, await probeRow())) reused += 1;
    }
    // A line moving between groups changes the block and line counts; other rows must survive.
    const reviewNode = await (await rowFor("g-review")).elementHandle();
    const moved = await page.evaluate(() => {
      // A line with a possible catalogue match is no longer an unmatched offer, so it moves to Review.
      const saved = state.quoteBasisSections;
      state.quoteBasisSections = saved.map((section) => ({ ...section, lines: section.lines.map((line) => line.id === "g-unmatched"
        ? { ...line, possible_pricing_matches: [{ pricing_keyword: "synthetic-move-probe", description: "Synthetic move probe" }] } : line) }));
      updateQuoteBasisCard("edited");
      const group = (id) => document.querySelector('#basisReviewSurface [data-basis-key="' + id + '"]')?.closest(".basis-review-grid")?.getAttribute("aria-label");
      const during = group("g-unmatched");
      state.quoteBasisSections = saved;
      updateQuoteBasisCard("edited");
      return { during, after: group("g-unmatched") };
    });
    assert(moved.during === "Review" && moved.after === "Unmatched", "fixture line did not move between groups", moved);
    assert(await reviewNode.evaluate((node, after) => node.isConnected && node === after, await (await rowFor("g-review")).elementHandle()),
      "moving a line between groups rebuilt unrelated rows");
    const timing = summarize(latency);
    result.latency = { basisLines: await page.evaluate(() => state.quoteBasisSections.flatMap((section) => section.lines).length), budgetMs: 150, timing, reusedProbeNodes: reused + "/" + probes };
    assert(result.latency.basisLines >= 60, "latency fixture is smaller than 60 basis lines", result.latency.basisLines);
    // Budget is for the click handler's own work (input processing), measured by the browser's Event Timing.
    const slow = Object.entries(timing).filter(([, value]) => value.medianProcessingMs > 150);
    assert(!slow.length, "basis click handler exceeded its latency budget", timing);
    assert(reused === probes, "unchanged basis rows were rebuilt by a click", result.latency);
    // 5. Blank-quantity catalogue picks keep the named item and its price and ask only for the quantity.
    const blankRow = await rowFor("q-blank-graphic");
    const unknownRow = await rowFor("q-blank-unknown");
    const graphic = byName("Printed Wall Graphic");
    const quantityInput = blankRow.locator("input[data-basis-quantity-item]");
    assert(await quantityInput.count() === 1 && await quantityInput.getAttribute("data-basis-quantity-item") === graphic.id,
      "blank-quantity catalogue line has no quantity prompt for its catalogue item");
    const blankText = await blankRow.innerText();
    assert(blankText.includes("sqm Printed Wall Graphic") && blankText.includes(Number(graphic.sale_unit_price).toFixed(2)),
      "blank-quantity line hides its catalogue item or price", blankText);
    assert(await unknownRow.locator("input[data-basis-quantity-item]").count() === 0, "unknown catalogue reference was offered a catalogue price");
    const ambiguous = await page.evaluate((itemId) => {
      const reference = currentPricingReference();
      const key = Array.isArray(reference.authority_items) ? "authority_items" : "items";
      const saved = reference[key];
      reference[key] = [...saved, { ...saved.find((item) => item.id === itemId), id: itemId + "-duplicate" }];
      const section = state.quoteBasisSections.find((entry) => entry.lines.some((line) => line.id === "q-blank-graphic"));
      const offered = basisCatalogueQuantityItem(section.lines.find((line) => line.id === "q-blank-graphic"));
      reference[key] = saved;
      return offered;
    }, graphic.id);
    assert(ambiguous === null, "ambiguous catalogue reference was offered a catalogue price", ambiguous);
    // 6. Quantity is mandatory (owner rule 18 Nov 2026): a line without a catalogue match asks for its own
    // quantity and unit, is never labelled "Not measured", and blocks Confirm until it is filled.
    const lineById = (id) => page.evaluate((lineId) => {
      const line = state.quoteBasisSections.flatMap((section) => section.lines).find((entry) => entry.id === lineId);
      return { quantity: line.quantity, unit: normalizeUnit(line.unit), needsQuantity: basisLineNeedsQuantity(line) };
    }, id);
    assert(!/not measured/i.test(await surface.innerText()), "a basis line is still labelled Not measured");
    const freeInput = unknownRow.locator("input[data-basis-quantity-free]");
    assert(await freeInput.count() === 1 && await freeInput.evaluate((input) => input.required), "line without a quantity has no required quantity box");
    assert((await lineById("q-blank-unknown")).needsQuantity, "blank quantity line is not flagged as needing a quantity");
    assert(await page.evaluate(() => {
      const saved = state.quoteBasisSections;
      state.quoteBasisSections = [{ id: "probe", title: "Probe", lines: [{ id: "probe-line", tag: "Include", text: "Probe wall", quantity: null, unit: "" }] }];
      try { return basisConfirmBlockReason(state.quoteBasisSections); } finally { state.quoteBasisSections = saved; }
    }) === "Enter a quantity for every line before confirming quotation basis.", "a blank quantity did not block Confirm");
    await freeInput.fill("lots");
    await freeInput.press("Enter");
    assert((await lineById("q-blank-unknown")).needsQuantity, "an invalid quantity was accepted");
    await freeInput.fill("6 sqm");
    await freeInput.press("Enter");
    const freeEntered = await lineById("q-blank-unknown");
    assert(Number(freeEntered.quantity) === 6 && freeEntered.unit === "sqm" && !freeEntered.needsQuantity,
      "typed quantity and unit were not kept on the line", freeEntered);
    assert(await (await rowFor("q-blank-unknown")).locator("input[data-basis-quantity-free]").count() === 0, "quantity box stayed after a valid quantity");
    result.freeQuantityLine = freeEntered;
    await quantityInput.fill("0");
    await quantityInput.press("Enter");
    assert(await page.evaluate(() => !state.quoteBasisSections.flatMap((section) => section.lines).find((line) => line.id === "q-blank-graphic").pricing_keyword),
      "a zero quantity admitted the catalogue item");
    await quantityInput.fill("12.5");
    await quantityInput.press("Enter");
    const entered = await page.evaluate(() => {
      const line = state.quoteBasisSections.flatMap((section) => section.lines).find((entry) => entry.id === "q-blank-graphic");
      return { ...line, unit: normalizeUnit(line.unit), tag: normalizeBasisTag(line.tag) };
    });
    assert(entered.pricing_keyword === graphic.id && Number(entered.quantity) === 12.5 && entered.unit === "sqm" && entered.tag === "Include",
      "entered quantity did not keep the catalogue item in its unit", entered);
    result.blankQuantityLine = { pricing_keyword: entered.pricing_keyword, quantity: entered.quantity, unit: entered.unit, tag: entered.tag };
    // The entered quantity prices through the normal Confirm admission (no client price authority).
    for (let guard = 0; guard < 200; guard++) {
      const pending = await page.evaluate(() => {
        for (const section of state.quoteBasisSections) {
          const index = (section.lines || []).findIndex((line) => !basisLineIsInformationalDimension(line) && (normalizeBasisTag(line.tag) === "Confirm" || isPendingAiProposalLine(line)));
          if (index >= 0) return { section: section.id, index };
        }
        return null;
      });
      if (!pending) break;
      await surface.locator('[data-basis-section="' + pending.section + '"][data-basis-line-index="' + pending.index + '"][data-basis-tag="Exclude"]').click();
    }
    await page.locator("#sideNextButton").click();
    await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 90000 });
    await page.waitForFunction(() => !state.isPreparingOutput && !appIsBusy(), null, { timeout: 90000 });
    const priced = await page.evaluate(() => {
      const row = state.outputRows.find((entry) => entry.source_basis_line_id === "q-blank-graphic");
      const authority = row ? outputPriceAuthority(row) : null;
      return row ? { variant: authority?.variant, price: pricingAuthorityPrice(authority), quantity: row.quantity, unit: normalizeUnit(row.unit), keyword: row.pricing_keyword } : null;
    });
    assert(priced?.variant === "catalog" && priced.price === graphic.sale_unit_price && Number(priced.quantity) === 12.5 && priced.unit === "sqm"
      && priced.keyword === graphic.id, "entered catalogue quantity was not priced by the server admission", priced);
    result.blankQuantityPriced = priced;
  }
  assert(pageErrors.length === 0, "page errors", pageErrors);
  result.status = "PASS";
  await writeJson("result.json", result);
  console.log(JSON.stringify(result, null, 2));
  break carrier;`);
const runtime = path.join(carrierRoot, 'basis-review-carrier.mjs');
await fs.writeFile(runtime, code);
const child = spawn(process.execPath, [runtime, '--lines=60', '--latency-ms=100', '--fresh-analysis', ...process.argv.slice(2)], {
  cwd: root, windowsHide: true, stdio: 'inherit',
  env: { ...process.env, SQAG_OWNER_UAT_EVIDENCE_ROOT: carrierRoot },
});
const exitCode = await new Promise((resolve) => child.once('exit', resolve));
process.exitCode = exitCode ?? 1;
