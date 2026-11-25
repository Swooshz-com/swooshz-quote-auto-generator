// Pricing Reference import as an admin does it: rows the file gives no cost or markup stay in Review Rows to
// complete, a row that is left out is named with its reason, in staff words, and no price is made up.
// The owner-UAT mock harness answers the AI import with a synthetic reply; no paid AI.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";

const run = new SyntheticQuoteRun("pricing-import-rows");
const item = (description, extra = {}) => ({
  section: "Synthetic AV", description, unit_hint: "nos", internal_cost: null, markup_multiplier: null, remarks: [], aliases: [],
  category_order: 1, item_order: 1, source_row_index: null, warning: null, status: null, ...extra,
});
const names = ["Synthetic display", "Synthetic speaker", "Synthetic stand light"];
const scenario = { raw: {}, pricing_import: { currency: "SGD", items: [
  ...names.map((name, index) => item(name, { item_order: index + 1 })),
  item(null, { item_order: 4, section: "Synthetic Furniture", unit_hint: "sets" }),
] } };
const csv = ["Category,Item,Unit,Unit price", ...names.map((name, index) => `Synthetic AV,${name},nos,${100 + index}`), "Synthetic Furniture,,sets,40"].join("\n");

const result = {};
try {
  await run.start(scenario);
  const page = await run.signedInPage();
  await page.locator("#settingsButton").click();
  await page.locator("#pricingReferenceImportTab").click();
  await page.locator("#pricingReferenceFile").setInputFiles({ name: "synthetic-catalogue.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.locator("#pricingReferencePreview .pricing-reference-attention").waitFor({ state: "visible", timeout: 60000 });
  const attention = () => page.locator("#pricingReferencePreview").innerText().then((text) => text.replace(/\s+/g, " ").trim());
  result.attention = await attention();
  // The row that cannot be a catalogue row is named, with why; every other row is kept to complete.
  assert(result.attention.includes("Row 4 (Synthetic Furniture, sets) left out: no description."), "the left-out row is not named with its reason", result.attention);
  assert(result.attention.includes("3 rows need edit") && result.attention.includes("row 1: cost required; markup required"), "the rows without a price are not listed in staff words", result.attention);
  assert(!/unit_hint|internal_cost|markup_multiplier/.test(result.attention), "the import shows field names", result.attention);
  assert(!result.attention.includes("pricing reference name") && !(await page.locator("#pricingReferenceName").isVisible()), "the import asks for a name it does not show", result.attention);
  assert(await page.locator("#pricingReferenceSaveButton").isDisabled(), "rows without a price can be saved");

  await page.locator("[data-pricing-reference-table-open]").click();
  const rows = page.locator("#pricingReferenceTableBody tr[data-preview-row]");
  const row = (index) => rows.nth(index);
  const status = (index) => row(index).locator(".pricing-preview-status").innerText();
  const fill = (index, field, value) => row(index).locator(`[data-preview-field="${field}"]`).fill(value);
  assert(await rows.count() === 3, "the rows without a price were dropped", await rows.count());
  result.kept = await rows.locator('[data-preview-field="description"]').evaluateAll((inputs) => inputs.map((input) => input.value));
  assert(JSON.stringify(result.kept) === JSON.stringify(names), "the kept rows are not the file's rows", result.kept);
  // No price is made up from the unit price.
  assert((await rows.locator('[data-preview-field="internal_cost"], [data-preview-field="markup_multiplier"]').evaluateAll((inputs) => inputs.map((input) => input.value))).every((value) => value === ""), "a price was made up");
  for (let index = 0; index < 3; index++) {
    await fill(index, "internal_cost", "80");
    await fill(index, "markup_multiplier", "0");
    assert(await status(index) === "markup must be above 0", "a markup of 0 is accepted", await status(index));
    await fill(index, "markup_multiplier", "1.25");
    assert(await status(index) === "OK", "a completed row is still flagged", await status(index));
  }
  // A duplicate flag leaves both rows as soon as either one is changed.
  await fill(1, "description", names[0]);
  assert((await status(0)).includes("duplicate pricing row") && (await status(1)).includes("duplicate pricing row"), "the duplicate rows are not flagged", [await status(0), await status(1)]);
  await fill(1, "description", names[1]);
  result.afterDuplicate = [await status(0), await status(1)];
  assert(result.afterDuplicate.every((text) => text === "OK"), "a row keeps the duplicate flag after the duplicate is gone", result.afterDuplicate);
  await page.locator("#pricingReferenceTableOverlay .primary-button[data-pricing-reference-table-close]").click();
  await page.locator("#pricingReferenceName").waitFor({ state: "visible", timeout: 10000 });
  await page.locator("#pricingReferenceName").fill("Synthetic Import " + Date.now().toString(36));
  assert(!(await page.locator("#pricingReferenceSaveButton").isDisabled()), "the completed import cannot be saved", await attention());
  run.finish(result);
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
