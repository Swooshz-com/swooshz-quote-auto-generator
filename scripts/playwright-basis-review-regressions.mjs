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
  { id: "g-matched", kind: "confirm", section: "Lighting and Electrical", text: "LED Flood Light", quantity: 3, unit: "nos", claim: "LED Flood Light", estimate: { quantity: 3, unit: "nos" } },
  { id: "g-unmatched", kind: "unmatched", section: "Lighting and Electrical", text: "Kinetic sculpture", quantity: 2, unit: "nos" },
  { id: "g-review", kind: "review", section: "Lighting and Electrical", text: "Bespoke neon signage with custom lettering", quantity: 1, unit: "lot", rawUnit: "" },
  { id: "q-blank-graphic", kind: "blank", section: "Graphics and Printing", text: "[ sqm Printed Wall Graphic ] - Printed graphic on rear feature wall, size pending approval", quantity: null, unit: "" },
  { id: "q-blank-unknown", kind: "blank", section: "Graphics and Printing", text: "[ sqm Holographic Wall Wrap ] - Side wall wrap, size pending approval", quantity: null, unit: "" },
  { id: "q-blank-fill", kind: "blank", section: "Graphics and Printing", text: "Hanging banner over the counter", quantity: null, unit: "" },
  { id: "q-blank-kw", kind: "confirm", section: "Graphics and Printing", text: "Printed Fabric Banner", quantity: null, unit: "", claim: "Printed Fabric Banner" },`);
// The AI names a catalogue item but measures nothing, so it emits no line item for those lines.
replaceOnce('const scenarioPath = path.join(runRoot, "scenario.json");', `lineItems.splice(0, lineItems.length, ...lineItems.filter((item) => !String(item.source_basis_line_id || "").startsWith("q-blank")));
const scenarioPath = path.join(runRoot, "scenario.json");`);
// A size the server predicted carries quantity_estimate, which the Quote Basis shows as "Est." (owner 17:18Z, 18 Nov).
replaceOnce('confidence_pct: 82 };', 'confidence_pct: 82, ...(entry.estimate ? { quantity_estimate: entry.estimate } : {}) };');
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
    // Every Detected booth variant (detected size, Known details size, 6m x 6m default note) has one look: the
    // "Detected booth" heading is the size and weight of the "Matched" heading and every line is 15px regular;
    // the card keeps its place and colours and fits at desktop and phone widths (owner, INT-SQAG-202611200005).
    const defaultNote = "Booth size defaults to 6m x 6m because no clear dimensions were found; confirm before generating.";
    const boothVariant = async (variant, label) => {
      const look = await page.evaluate(({ variant, defaultNote }) => {
        const savedDimensions = state.boothDimensions;
        const noteLine = { tag: "Confirm", text: defaultNote };
        const noteLines = state.quoteBasisSections[0].lines;
        window.restoreBoothVariant = () => {
          if (noteLines.includes(noteLine)) noteLines.splice(noteLines.indexOf(noteLine), 1);
          state.boothDimensions = savedDimensions;
          updateQuoteBasisCard("edited");
        };
        if (variant === "knownDetails") state.boothDimensions = { ...savedDimensions, booth_width: "", booth_depth: "", booth_area_sqm: 93, dimension_source: "staff" };
        if (variant === "defaultNote") {
          state.boothDimensions = { ...DEFAULT_BOOTH_DIMENSIONS };
          noteLines.push(noteLine);
        }
        updateQuoteBasisCard("edited");
        const style = (node) => {
          const computed = node && getComputedStyle(node);
          return computed ? { fontSize: parseFloat(computed.fontSize), fontWeight: Number(computed.fontWeight), color: computed.color } : null;
        };
        const box = document.querySelector("#basisReviewSurface .basis-visual-display");
        const card = box && getComputedStyle(box);
        const rect = box && box.getBoundingClientRect();
        const matchedHeading = document.querySelector('#basisReviewSurface .basis-review-grid[aria-label="Matched"] > h4');
        const found = box ? {
          lines: [...box.querySelectorAll("li")].map((line) => ({ text: line.textContent.trim(), ...style(line) })),
          heading: { text: box.querySelector("strong").textContent.trim(), ...style(box.querySelector("strong")) },
          matchedHeading: style(matchedHeading),
          card: { background: card.backgroundColor, border: card.borderTopColor, padding: card.padding, radius: card.borderTopLeftRadius },
          abovePriceGroups: Boolean(box.compareDocumentPosition(matchedHeading) & Node.DOCUMENT_POSITION_FOLLOWING),
          fits: box.scrollWidth <= box.clientWidth && rect.left >= 0 && rect.right <= document.documentElement.clientWidth,
        } : null;
        return found;
      }, { variant, defaultNote });
      if (look) await surface.locator(".basis-visual-display").screenshot({ path: path.join(runRoot, "detected-booth-" + label + "-" + variant + ".png") });
      await page.evaluate(() => window.restoreBoothVariant());
      return look;
    };
    const boothCardLooks = {};
    const desktopViewport = page.viewportSize();
    for (const [width, label] of [[desktopViewport.width, "desktop"], [390, "phone"]]) {
      if (label === "phone") await page.setViewportSize({ width, height: 844 });
      for (const variant of ["detected", "knownDetails", "defaultNote"]) boothCardLooks[label + ":" + variant] = await boothVariant(variant, label);
    }
    await page.setViewportSize(desktopViewport);
    const expectedText = { detected: "Booth size 9m x 10.5m (94.5 sqm)", knownDetails: "Booth size 93 sqm (from Known details)", defaultNote };
    for (const [key, look] of Object.entries(boothCardLooks)) {
      const variant = key.split(":")[1];
      assert(look && look.heading.text === "Detected booth" && look.lines.some((line) => line.text === expectedText[variant]),
        "Detected booth variant did not render", { key, look });
      assert(look.heading.fontSize === look.matchedHeading.fontSize && look.heading.fontWeight === look.matchedHeading.fontWeight,
        "Detected booth heading is not the size and weight of the Matched heading", { key, look });
      assert(look.lines.every((line) => line.fontSize === 15 && line.fontWeight < 700),
        "Detected booth lines are not all 15px regular", { key, look });
      assert(look.heading.color === "rgb(24, 48, 71)" && look.lines.every((line) => line.color === "rgb(24, 48, 71)")
        && JSON.stringify(look.card) === JSON.stringify({ background: "rgb(247, 251, 255)", border: "rgb(213, 227, 238)", padding: "14px 18px", radius: "8px" })
        && look.abovePriceGroups, "Detected booth card changed its colours, box or place", { key, look });
      assert(look.fits, "Detected booth card overflows", { key, look });
    }
    result.detectedBooth = { structured: (await boothBox.innerText()).trim(), unknownDimensions: unknownBooth, looks: boothCardLooks };

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
    // 5. Quantities read as plain text; clicking one opens its box (owner 18 Nov 2026: editable inline,
    // without Re, but not looking like a form by default). Blank-quantity catalogue picks keep the named
    // item and its price and ask only for the quantity.
    const openQuantity = async (id) => {
      const row = await rowFor(id);
      await row.locator("[data-basis-quantity-edit]").click();
      const input = row.locator("input.basis-quantity-input");
      await input.waitFor({ state: "visible", timeout: 5000 });
      return input;
    };
    assert(await surface.locator("input.basis-quantity-input").count() === 0, "a quantity box is open before any click");
    const measuredButton = (await rowFor("g-matched")).locator("[data-basis-quantity-edit]");
    assert(await measuredButton.count() === 1 && (await measuredButton.innerText()).trim().startsWith("3 nos"), "measured quantity is not shown as plain clickable text", await measuredButton.innerText());
    // The predicted size shows "Est." beside it; sizes with no estimate show none.
    assert(await (await rowFor("g-matched")).locator("[data-basis-quantity-est]").innerText() === "Est.", "a predicted size has no Est. tag");
    assert(await surface.locator("[data-basis-quantity-est]").count() === 1, "Est. shows on sizes nobody predicted");
    // "Est." has the size and colour of its quantity, so "3 nos Est." reads as one (owner, INT-SQAG-202611200004).
    const estStyle = await (await rowFor("g-matched")).evaluate((row) => {
      const style = (node) => { const computed = getComputedStyle(node); return { fontSize: computed.fontSize, color: computed.color }; };
      return { quantity: style(row.querySelector("[data-basis-quantity-edit]")), est: style(row.querySelector("[data-basis-quantity-est]")) };
    });
    assert(JSON.stringify(estStyle.est) === JSON.stringify(estStyle.quantity), "Est. tag size or colour differs from its quantity", estStyle);
    result.estTag = estStyle;
    if (process.env.SQAG_BASIS_SCREENSHOT_DIR) {
      await (await rowFor("g-matched")).locator("xpath=ancestor::*[contains(@class,'basis-review-item')][1]").screenshot({ path: process.env.SQAG_BASIS_SCREENSHOT_DIR + "/est-tag.png" });
    }
    const blankRow = await rowFor("q-blank-graphic");
    const unknownRow = await rowFor("q-blank-unknown");
    // Owner (18 Nov 2026): no "Add qty". A blank line (only quotes saved before the server sizing guarantee)
    // shows the booth-size rule size: 1 in its catalogue unit here, as Graphics is not a floor section.
    assert(!/add qty/i.test(await surface.innerText()), "a basis line still asks to Add qty");
    assert((await blankRow.locator("[data-basis-quantity-edit]").innerText()).trim() === "1 sqm", "blank catalogue line does not show its rule size");
    assert((await (await rowFor("q-blank-fill")).locator("[data-basis-quantity-edit]").innerText()).trim() === "1 lot", "blank plain line does not show its rule size");
    assert((await unknownRow.locator("[data-basis-quantity-edit]").innerText()).trim() === "1 sqm", "blank line does not take the unit its bracketed reference names");
    const graphic = byName("Printed Wall Graphic");
    let quantityInput = await openQuantity("q-blank-graphic");
    assert(await quantityInput.getAttribute("data-basis-quantity-item") === graphic.id && await quantityInput.inputValue() === "1",
      "blank-quantity catalogue line has no quantity prompt for its catalogue item, prefilled with its rule size");
    await quantityInput.press("Escape");
    assert(await blankRow.locator("input.basis-quantity-input").count() === 0, "Escape did not close the quantity box");
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
    // 6. Quantity is mandatory (owner rule 18 Nov 2026): a line without a catalogue match takes its own
    // quantity and unit, is never labelled "Not measured", and Confirm fills any still blank with its rule size.
    const lineById = (id) => page.evaluate((lineId) => {
      const line = state.quoteBasisSections.flatMap((section) => section.lines).find((entry) => entry.id === lineId);
      return { quantity: line.quantity, unit: normalizeUnit(line.unit), needsQuantity: basisLineNeedsQuantity(line) };
    }, id);
    assert(!/not measured/i.test(await surface.innerText()), "a basis line is still labelled Not measured");
    let freeInput = await openQuantity("q-blank-unknown");
    assert(await freeInput.getAttribute("data-basis-quantity-free") !== null && await freeInput.evaluate((input) => input.required),
      "line without a quantity has no required quantity box");
    assert((await lineById("q-blank-unknown")).needsQuantity, "blank quantity line is not flagged as needing a quantity");
    const probeBlocks = await page.evaluate(() => {
      const saved = { sections: state.quoteBasisSections, rows: state.lineItems };
      state.quoteBasisSections = [{ id: "probe", title: "Probe", lines: [{ id: "probe-line", tag: "Include", text: "Probe wall", quantity: null, unit: "" }] }];
      try {
        const fillable = basisConfirmBlockReason(state.quoteBasisSections);
        // Confirm cannot fill a line with more than one output row, so that one still blocks.
        state.lineItems = [{ source_basis_line_id: "probe-line" }, { source_basis_line_id: "probe-line" }];
        return { fillable, multiRow: basisConfirmBlockReason(state.quoteBasisSections) };
      } finally { state.quoteBasisSections = saved.sections; state.lineItems = saved.rows; }
    });
    assert(probeBlocks.fillable === "" && probeBlocks.multiRow === "Enter a quantity for every line before confirming quotation basis.",
      "Confirm blocking for blank quantities is wrong", probeBlocks);
    await freeInput.fill("lots");
    await freeInput.press("Enter");
    assert((await lineById("q-blank-unknown")).needsQuantity, "an invalid quantity was accepted");
    // Leaving the box with an invalid value puts the old value back instead of keeping it half-typed.
    await page.locator("body").click({ position: { x: 2, y: 2 } });
    assert(await (await rowFor("q-blank-unknown")).locator("input.basis-quantity-input").count() === 0, "an invalid quantity box stayed open after leaving it");
    freeInput = await openQuantity("q-blank-unknown");
    await freeInput.fill("6 sqm");
    await freeInput.press("Enter");
    const freeEntered = await lineById("q-blank-unknown");
    assert(Number(freeEntered.quantity) === 6 && freeEntered.unit === "sqm" && !freeEntered.needsQuantity,
      "typed quantity and unit were not kept on the line", freeEntered);
    assert(await (await rowFor("q-blank-unknown")).locator("input.basis-quantity-input").count() === 0, "quantity box stayed after a valid quantity");
    assert((await (await rowFor("q-blank-unknown")).locator("[data-basis-quantity-edit]").innerText()).trim().startsWith("6 sqm"), "entered quantity is not shown as text");
    result.freeQuantityLine = freeEntered;
    // 7. A line already naming its catalogue item but saved without a quantity (only quotes analysed before
    // the server sizing guarantee) takes its size in the box and reaches Output with it. Its price stays in
    // review: the pricing analysis seal recorded no measured unit for it, and that seal is never rewritten.
    const keywordInput = await openQuantity("q-blank-kw");
    assert(await keywordInput.getAttribute("data-basis-quantity-free") !== null, "catalogue line missing a quantity has no quantity box");
    await keywordInput.fill("5 sqm");
    await keywordInput.press("Enter");
    assert((await lineById("q-blank-kw")).quantity === 5, "typed quantity was not kept on the catalogue line");
    await (await rowFor("q-blank-kw")).locator('[data-basis-tag="Include"]').click();
    // The free-box line goes to Output too, so its typed size is carried into the quote.
    await (await rowFor("q-blank-unknown")).locator('[data-basis-tag="Include"]').click();
    // This one stays blank until Confirm, which writes its shown rule size.
    await (await rowFor("q-blank-fill")).locator('[data-basis-tag="Include"]').click();
    assert((await lineById("q-blank-fill")).needsQuantity, "the Confirm-fill probe line is no longer blank");
    // A measured catalogue line is edited in place too (no Re) and keeps its catalogue price at the new size.
    await (await rowFor("g-matched")).locator('[data-basis-tag="Include"]').click();
    const matchedInput = await openQuantity("g-matched");
    await matchedInput.fill("5");
    await matchedInput.press("Enter");
    assert(Number((await lineById("g-matched")).quantity) === 5, "measured quantity edit was not kept");
    assert(await (await rowFor("g-matched")).locator("[data-basis-quantity-est]").count() === 0, "a typed size still shows Est.");
    quantityInput = await openQuantity("q-blank-graphic");
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
    const outputOf = (id) => page.evaluate((lineId) => {
      const row = state.outputRows.find((entry) => entry.source_basis_line_id === lineId);
      const authority = row ? outputPriceAuthority(row) : null;
      return row ? { variant: authority?.variant || "", price: pricingAuthorityPrice(authority), quantity: Number(row.quantity), unit: normalizeUnit(row.unit), keyword: row.pricing_keyword || "" } : null;
    }, id);
    const keywordOutput = await outputOf("q-blank-kw");
    assert(keywordOutput?.quantity === 5 && keywordOutput.unit === "sqm" && keywordOutput.price === null,
      "catalogue line typed quantity did not reach Output, or was priced without seal evidence", keywordOutput);
    const freeOutput = await outputOf("q-blank-unknown");
    assert(freeOutput?.quantity === 6 && freeOutput.unit === "sqm", "typed free quantity did not reach Output", freeOutput);
    result.keywordQuantityOutput = keywordOutput;
    const filledOutput = await outputOf("q-blank-fill");
    assert(filledOutput?.quantity === 1 && filledOutput.unit === "lot", "Confirm did not write the shown rule size into a blank line", filledOutput);
    result.confirmFilledOutput = filledOutput;
    const matchedOutput = await outputOf("g-matched");
    assert(matchedOutput?.quantity === 5 && matchedOutput.unit === "nos" && matchedOutput.variant === "catalog" && matchedOutput.price !== null,
      "edited measured catalogue quantity was not priced at the new size", matchedOutput);
    result.measuredQuantityOutput = matchedOutput;
    result.freeQuantityOutput = freeOutput;
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
