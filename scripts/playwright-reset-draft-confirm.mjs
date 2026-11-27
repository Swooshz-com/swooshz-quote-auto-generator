// Reset Draft asks first on every step, and an exchange rate that is not above 0 is refused (#442). Each step's
// Reset Draft is pressed twice in the real UI: Cancel (button, Escape or beside the dialog) leaves the step as it
// was, and Reset Draft in the dialog clears it as before. Synthetic data, loopback only, no paid AI.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";
import path from "node:path";

const run = new SyntheticQuoteRun("reset-draft-confirm");
const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
const lines = [
  { id: "carpet", section: "Synthetic Floors", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm" },
  { id: "spotlight", section: "Synthetic Lighting And AV", text: "nos. synthetic spotlight 6 inch", quantity: 8, unit: "nos" },
].map((line) => ({ ...line, keyword: slug(line.section) + "-" + slug(line.text.replace(/^(?:sqm|nos\.) /, "")) }));
const scenario = { raw: {
  analysis_findings: [], blocking_clarification_questions: [], project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: [...new Set(lines.map((line) => line.section))].map((title) => ({ id: slug(title), title,
    lines: lines.filter((line) => line.section === title).map(({ id, text, quantity, unit, keyword }) => (
      { id, tag: "Include", text, quantity, unit, confidence_pct: 95, pricing_keyword: keyword })) })),
  line_items: lines.map(({ id, section, text, quantity, unit, keyword }) => (
    { source_basis_line_id: id, section, description: text, quantity, unit, pricing_keyword: keyword })),
} };
const QUESTION = { eyebrow: "Reset Draft", title: "Reset this step?", lost: "Your changes on this step will be lost." };
const CUSTOMER_DETAIL = "This also clears Quote Basis and Output.";
const RATE_MESSAGE = "Enter an exchange rate above 0.";
const SERVER_RATE_MESSAGE = "Enter an exchange rate above 0 on the Customer step.";
const result = { steps: {} };

const idle = (page) => page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 30000 });
const dialog = (page) => page.evaluate(() => {
  const modal = document.querySelector("#resetDraftConfirmModal");
  const detail = document.querySelector("#resetDraftConfirmDetail");
  return {
    open: !modal.hidden && getComputedStyle(modal).display !== "none",
    eyebrow: modal.querySelector(".basis-chat-eyebrow").textContent.trim(),
    title: document.querySelector("#resetDraftConfirmTitle").innerText.trim(),
    lost: modal.querySelector("#resetDraftConfirmText strong").innerText.trim(),
    detail: detail.hidden ? "" : detail.innerText.trim(),
    focused: document.activeElement?.id || "",
  };
});
const ask = async (page, buttonId, step) => {
  await page.locator("#" + buttonId).click();
  await page.locator("#resetDraftConfirmModal").waitFor({ state: "visible", timeout: 5000 });
  await page.waitForFunction(() => document.activeElement?.id === "cancelResetDraftButton", null, { timeout: 5000 });
  const seen = await dialog(page);
  const wanted = { open: true, ...QUESTION, detail: step === "customer" ? CUSTOMER_DETAIL : "", focused: "cancelResetDraftButton" };
  assert(JSON.stringify(seen) === JSON.stringify(wanted), step + ": Reset Draft did not ask the owner's question", seen);
};
const CANCELS = {
  button: (page) => page.locator("#cancelResetDraftButton").click(),
  Escape: (page) => page.keyboard.press("Escape"),
  "a click beside the dialog": (page) => page.locator("#resetDraftConfirmModal .modal-backdrop").click({ position: { x: 8, y: 8 } }),
};
// One step: change it, Reset Draft then Cancel keeps the change, Reset Draft then Reset Draft clears it.
const checkStep = async (page, { step, panel, buttonId, cancel, change, probe, changed, cleared }) => {
  await run.openPanel(page, panel);
  await change();
  await idle(page);
  const before = await probe();
  assert(changed(before), step + ": the change did not apply", before);
  await ask(page, buttonId, step);
  await CANCELS[cancel](page);
  await page.locator("#resetDraftConfirmModal").waitFor({ state: "hidden", timeout: 5000 });
  await idle(page);
  const afterCancel = await probe();
  assert(JSON.stringify(afterCancel) === JSON.stringify(before), step + ": Cancel by " + cancel + " changed the step", { before, afterCancel });
  const focused = await page.evaluate(() => document.activeElement?.id || "");
  assert(focused === buttonId, step + ": focus did not return to Reset Draft", focused);
  await ask(page, buttonId, step);
  await page.locator("#confirmResetDraftButton").click();
  await page.locator("#resetDraftConfirmModal").waitFor({ state: "hidden", timeout: 5000 });
  await idle(page);
  const afterReset = await probe();
  assert(cleared(afterReset, before), step + ": Reset Draft in the dialog did not clear the step", { before, afterReset });
  result.steps[step] = { cancel, before, afterCancel, afterReset };
};

try {
  await run.start(scenario);
  let page = await run.signedInPage();
  await run.buildToBasis(page);
  await run.tickOpenLines(page);
  await run.confirm(page);
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await idle(page);

  // Output: a typed quantity.
  await checkStep(page, {
    step: "output", panel: "output", buttonId: "resetOutputButton", cancel: "button",
    change: async () => {
      const index = await page.evaluate(() => state.outputRows.findIndex((row) => row.source_basis_line_id === "carpet"));
      await page.locator(`tr[data-output-row="${index}"] [data-output-edit-field="quantity"]`).click();
      const editor = page.locator("[data-output-editor-field]").first();
      await editor.fill("41");
      await editor.press("Enter");
    },
    probe: () => page.evaluate(() => state.outputRows.map((row) => [row.source_basis_line_id, Number(row.quantity)])),
    changed: (rows) => rows.some(([id, quantity]) => id === "carpet" && quantity === 41),
    cleared: (rows) => rows.some(([id, quantity]) => id === "carpet" && quantity === 36),
  });

  // Quote Basis: a line X'd.
  const basisTags = () => page.evaluate(() => Object.fromEntries(state.quoteBasisSections.flatMap((section) => section.lines || [])
    .map((line) => [line.id, normalizeBasisTag(line.tag)])));
  await checkStep(page, {
    step: "basis", panel: "basis", buttonId: "resetQuoteBasisButton", cancel: "Escape",
    change: () => run.tag(page, "spotlight", "Exclude"),
    probe: basisTags,
    changed: (tags) => tags.spotlight === "Exclude",
    // Reset returns every line, the standard rows too, to Confirm as the analysis left them.
    cleared: (tags) => Object.values(tags).every((tag) => tag === "Confirm"),
  });

  // Quote Company: a typed signatory.
  const signatory = () => page.evaluate(() => { syncRichTextSources(); return elements.companySignatory.value; });
  await checkStep(page, {
    step: "quote_company", panel: "quote_company", buttonId: "clearQuoteCompanyButton", cancel: "a click beside the dialog",
    change: () => page.locator("#companySignatoryEditor").fill("Changed Synthetic Signatory"),
    probe: signatory,
    changed: (value) => value === "Changed Synthetic Signatory",
    cleared: (value) => value !== "Changed Synthetic Signatory",
  });

  // Customer: a typed client name; its Reset Draft also clears Quote Basis and Output.
  await checkStep(page, {
    step: "customer", panel: "customer", buttonId: "clearCustomerButton", cancel: "button",
    change: () => page.locator("#clientNameEditor").fill("Changed Synthetic Customer"),
    probe: () => page.evaluate(() => ({ client: elements.clientName.value, basisLines: state.quoteBasisSections.flatMap((section) => section.lines || []).length })),
    changed: (seen) => seen.client === "Changed Synthetic Customer" && seen.basisLines > 0,
    cleared: (seen) => seen.client === "" && seen.basisLines === 0,
  });
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: width > 400 ? 900 : 812 });
    await page.locator("#clearCustomerButton").click();
    await page.locator("#resetDraftConfirmModal").waitFor({ state: "visible", timeout: 5000 });
    const file = path.join(run.runRoot, `reset-draft-customer-${width}.png`);
    await page.screenshot({ path: file });
    const box = await page.locator("#resetDraftConfirmModal .modal-panel").boundingBox();
    assert(box && box.x >= 0 && box.x + box.width <= width, "the dialog does not fit at " + width + " px", box);
    await page.locator("#cancelResetDraftButton").click();
    result.screenshots = { ...(result.screenshots || {}), [width]: file };
  }
  await page.setViewportSize({ width: 1280, height: 900 });

  // Work that starts while the dialog is open greys out its Reset Draft, with the reason, until it ends.
  await run.openPanel(page, "images");
  await ask(page, "resetImagesButton", "images");
  const busyDialog = (busy) => page.evaluate((busy) => {
    state.isPreparingOutput = busy;
    syncControlStates();
  }, busy);
  const confirmState = () => page.evaluate(() => [elements.confirmResetDraftButton.disabled, elements.confirmResetDraftButton.title, state.images.length]);
  await busyDialog(true);
  await page.waitForFunction(() => elements.confirmResetDraftButton.disabled, null, { timeout: 5000 });
  await page.locator("#confirmResetDraftButton").click({ force: true });
  const whileBusy = await confirmState();
  assert(JSON.stringify(whileBusy) === JSON.stringify([true, "Output is being prepared.", 1]) && (await dialog(page)).open,
    "Reset Draft in the dialog acted while the page was busy", whileBusy);
  await busyDialog(false);
  await page.waitForFunction(() => !elements.confirmResetDraftButton.disabled, null, { timeout: 5000 });
  result.busyDialog = { whileBusy, afterBusy: await confirmState() };
  await page.locator("#cancelResetDraftButton").click();

  // Upload: the reference file.
  await checkStep(page, {
    step: "images", panel: "images", buttonId: "resetImagesButton", cancel: "Escape",
    change: async () => {},
    probe: () => page.evaluate(() => state.images.length),
    changed: (count) => count === 1,
    cleared: (count) => count === 0,
  });
  await page.context().close();

  // Exchange rate: a foreign currency needs a rate above 0 before Customer Next, and the server refuses one too.
  page = await run.signedInPage();
  await page.locator("#newQuoteButton").click();
  await page.locator("#imageInput").setInputFiles({ name: "rate-render.png", mimeType: "image/png", buffer: await run.png(page, 800, 600) });
  await page.locator("#fileList .file-item").first().waitFor({ state: "visible" });
  await page.locator("#sideNextButton").click();
  await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
  for (const [selector, value] of [["#clientNameEditor", "Synthetic Customer"], ["#clientAttentionEditor", "Synthetic Reviewer"],
    ["#clientTitleEditor", "Synthetic Lead"], ["#clientAddressEditor", "Synthetic address"], ["#projectTitleEditor", "Synthetic Project"],
    ["#showName", "Synthetic Show"], ["#projectNumberEditor", "SQ-RATE-" + Date.now().toString(36).toUpperCase()]]) {
    await page.locator(selector).fill(value);
  }
  await page.locator("#quoteDate").fill(new Date().toISOString().slice(0, 10));
  const next = async () => {
    await page.waitForFunction(() => !appIsBusy() && !state.customerStepCheckpointSaveBusy, null, { timeout: 30000 });
    return page.evaluate(() => ({ disabled: elements.sideNextButton.disabled, reason: elements.sideNextButton.title,
      field: elements.quoteExchangeRate.validationMessage, rate: collectQuoteExchangeRate() }));
  };
  const sameCurrency = await next();
  assert(!sameCurrency.disabled && sameCurrency.rate === 1, "the reference currency does not keep a rate of 1", sameCurrency);
  await page.locator("#quoteCurrency").selectOption("USD");
  const rate = page.locator("#quoteExchangeRate");
  result.refusedRates = {};
  for (const typed of ["0", "-1", "", "abc"]) {
    await rate.fill("");
    if (typed) await rate.pressSequentially(typed);
    await rate.blur();
    const seen = await next();
    assert(seen.disabled && seen.reason.includes("Exchange rate above 0") && seen.field === RATE_MESSAGE && seen.rate === null,
      "Customer Next took an exchange rate of " + JSON.stringify(typed), seen);
    result.refusedRates[typed || "blank"] = seen;
  }
  const refused = await run.api(page, "POST", "/api/quote-sessions", {
    session_id: "quote-rate-refused", customer_summary: { customer_name: "Synthetic Customer" },
    commercials: { currency: "USD", tax_label: "GST", tax_rate: 0.09, exchange_rate: 0 }, status: { quote_generated: false },
  });
  assert(refused.status === 400 && JSON.stringify(refused.body.errors) === JSON.stringify([SERVER_RATE_MESSAGE]), "the server saved a rate of 0", refused);
  await rate.fill("1.35");
  await rate.blur();
  const accepted = await next();
  assert(!accepted.disabled && accepted.field === "" && accepted.rate === 1.35, "a rate of 1.35 was not accepted", accepted);
  await page.locator("#sideNextButton").click();
  await page.locator("#quoteCompanyPanel").waitFor({ state: "visible", timeout: 30000 });
  await idle(page);
  const sessionId = await page.evaluate(() => state.quoteSessionId);
  const saved = await run.record(sessionId);
  assert(saved.commercials?.exchange_rate === 1.35, "the saved quote does not hold the typed rate", saved.commercials);
  result.exchangeRate = { refusedByPage: Object.keys(result.refusedRates), refusedByServer: refused.status, saved: saved.commercials.exchange_rate };
  run.finish(result);
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
