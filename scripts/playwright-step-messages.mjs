// Step messages you can see (#250): one popup at the bottom right shows them. An error stays until its X is pressed;
// a warning fades by itself and never hides an error. Cases: an analysis that finds no booth (wrong upload), an Excel
// refused because the quote's company profile was deleted, Next while offline, and a save refused for its exchange
// rate. Synthetic data, mock provider, loopback only, no paid AI.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";
import fs from "node:fs/promises";
import path from "node:path";

const run = new SyntheticQuoteRun("step-messages");
const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
const carpet = { id: "carpet", section: "Synthetic Floors", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm" };
const keyword = slug(carpet.section) + "-" + slug(carpet.text.replace(/^sqm /, ""));
const booth = { raw: {
  analysis_findings: [], blocking_clarification_questions: [], project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: [{ id: slug(carpet.section), title: carpet.section, lines: [
    { id: carpet.id, tag: "Include", text: carpet.text, quantity: carpet.quantity, unit: carpet.unit, confidence_pct: 95, pricing_keyword: keyword }] }],
  line_items: [{ source_basis_line_id: carpet.id, section: carpet.section, description: carpet.text, quantity: carpet.quantity, unit: carpet.unit, pricing_keyword: keyword }],
} };
// What the AI answers for a file with no booth in it: nothing to quote.
const noBooth = { raw: { analysis_findings: [], blocking_clarification_questions: [], project: {}, quote_basis_sections: [], line_items: [] } };
// An answer the server refuses with a support reference (a basis line without its confidence).
const unusable = { raw: { ...booth.raw, quote_basis_sections: booth.raw.quote_basis_sections.map((section) => ({ ...section,
  lines: section.lines.map(({ confidence_pct, ...line }) => line) })) } };
const NO_BOOTH = "Please upload the booth render images first so I can analyze the design and prepare the quote. This analysis was still charged.";
const result = {};
// Review-round checks are collected, so one run says every one that fails.
const broken = [];
const check = (ok, message, detail) => { if (!ok) broken.push(message + (detail === undefined ? "" : ": " + JSON.stringify(detail))); };

const idle = (page) => page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 60000 });
const popup = (page) => page.evaluate(() => {
  const box = document.querySelector("#stepMessagePopup");
  const rect = box.getBoundingClientRect();
  return { shown: !box.hidden && getComputedStyle(box).display !== "none", error: box.classList.contains("is-error"),
    text: box.querySelector("#stepMessagePopupMessages").innerText.replace(/\s+/g, " ").trim(),
    inView: rect.top >= 0 && rect.left >= 0 && rect.bottom <= innerHeight && rect.right <= innerWidth,
    banner: !document.querySelector("#aiFailureBanner").hidden };
});
const waitForPopup = (page) => page.waitForFunction(() => !document.querySelector("#stepMessagePopup").hidden, null, { timeout: 60000 });
// An error stays past the warning fade time, and its X closes it.
const expectErrorStaysUntilClosed = async (page, label) => {
  await page.waitForTimeout(7000);
  const later = await popup(page);
  assert(later.shown && later.error, label + ": the error faded by itself", later);
  await page.locator("#stepMessagePopupClose").click();
  assert(!(await popup(page)).shown, label + ": the X did not close the error");
};

try {
  await run.start(noBooth);
  const page = await run.signedInPage();

  // 1. A file with no booth: the analysis says to upload booth renders, and that it was still charged.
  const realBasisReady = run.basisReady.bind(run);
  run.basisReady = async () => {};
  await run.buildToBasis(page);
  run.basisReady = realBasisReady;
  await waitForPopup(page);
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 60000 });
  const noBoothSeen = await popup(page);
  assert(noBoothSeen.shown && noBoothSeen.error && noBoothSeen.inView && !noBoothSeen.banner
    && noBoothSeen.text.startsWith(NO_BOOTH) && /Reference: ERR-[0-9A-F]{8}\./.test(noBoothSeen.text),
    "a file with no booth did not ask for booth renders in the popup", noBoothSeen);
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: width > 400 ? 900 : 812 });
    const file = path.join(run.runRoot, `step-message-error-${width}.png`);
    await page.screenshot({ path: file });
    const fits = await popup(page);
    assert(fits.inView, "the popup does not fit at " + width + " px", fits);
    const close = await page.locator("#stepMessagePopupClose").boundingBox();
    check(width > 640 || (close.width >= 44 && close.height >= 44), "the X is smaller than 44 px on a phone", close);
    (result.screenshots ||= {})[width] = file;
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  result.noBooth = noBoothSeen.text.replace(/ERR-[0-9A-F]{8}/, "ERR-…");

  // 1b. The right file, analysed again: the no-booth error goes when the new analysis starts and does not come back.
  await fs.writeFile(run.scenarioPath, JSON.stringify(booth));
  await page.locator("#analyseAgainButton").click();
  await page.locator("#analysisConfirmStartButton").click();
  await run.basisReady(page);
  const retried = await popup(page);
  check(!retried.shown, "the no-booth error stayed after a successful analysis", retried);
  result.retryClearsAnalysisError = true;

  // 2. A warning fades by itself, and a warning never hides an error.
  if ((await popup(page)).shown) await page.locator("#stepMessagePopupClose").click();
  await page.evaluate(() => renderMessages(["Synthetic warning."], "warn"));
  assert((await popup(page)).shown && !(await popup(page)).error, "a warning did not show");
  await page.waitForFunction(() => document.querySelector("#stepMessagePopup").hidden, null, { timeout: 10000 });
  await page.evaluate(() => { renderMessages(["Synthetic error."], "error"); renderMessages(["Synthetic warning."], "warn"); renderMessages([]); });
  const kept = await popup(page);
  assert(kept.shown && kept.error && kept.text === "Synthetic error.", "a warning or a clear hid an open error", kept);
  await page.locator("#stepMessagePopupClose").click();
  result.warningFades = true;
  // Escape closes an error as its X does.
  await page.evaluate(() => { document.activeElement?.blur?.(); renderMessages(["Synthetic Escape error."], "error"); });
  await page.keyboard.press("Escape");
  check(!(await popup(page)).shown, "Escape did not close the error");

  // 3. A real quote; its company profile is deleted; Download Excel says why, in view, while Output is scrolled down.
  await fs.writeFile(run.scenarioPath, JSON.stringify(booth));
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 30000 });
  await run.buildToBasis(page);
  await run.tickOpenLines(page);
  await run.confirm(page);
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await idle(page);

  // Output's own warnings keep their spacing: 8 px between boxes, 14 px before the table, hidden when there are none.
  const warningLayout = await page.evaluate(() => {
    renderOutputValidationMessages(["Synthetic warning one.", "Synthetic warning two."]);
    const list = elements.pricingReviewMessages;
    const [first, second] = [...list.children].map((node) => node.getBoundingClientRect());
    const next = list.nextElementSibling.getBoundingClientRect();
    const shown = { gap: Math.round(second.top - first.bottom), after: Math.round(next.top - list.getBoundingClientRect().bottom) };
    renderOutputValidationMessages([]);
    return { ...shown, emptyDisplay: getComputedStyle(list).display };
  });
  check(warningLayout.gap === 8 && warningLayout.after === 14 && warningLayout.emptyDisplay === "none", "Output warnings lost their spacing", warningLayout);
  result.outputWarningLayout = warningLayout;

  // The popup never covers Back, at any width, with the page scrolled to its end (Quote Basis stacks Back over Next
  // on narrow screens).
  await run.openPanel(page, "basis");
  for (const width of [1440, 1024, 768, 375]) {
    await page.setViewportSize({ width, height: width > 400 ? 900 : 812 });
    await page.evaluate((width) => { renderMessages([`Synthetic footer check at ${width} px.`], "error"); window.scrollTo(0, document.body.scrollHeight); }, width);
    await page.waitForTimeout(150);
    const overlap = await page.evaluate(() => {
      const box = document.querySelector("#stepMessagePopup").getBoundingClientRect();
      const back = document.querySelector("#sideBackButton").getBoundingClientRect();
      return { popupBottom: Math.round(box.bottom), backTop: Math.round(back.top),
        overlaps: box.left < back.right && box.right > back.left && box.top < back.bottom && box.bottom > back.top };
    });
    check(!overlap.overlaps, "the popup covers Back at " + width + " px", overlap);
    await page.locator("#stepMessagePopupClose").click();
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await run.openPanel(page, "output");
  const profileDirs = await fs.readdir(run.dirs.profiles);
  await Promise.all(profileDirs.map((dir) => fs.rm(path.join(run.dirs.profiles, dir), { recursive: true, force: true })));
  await page.locator("#sideDownloadButton").scrollIntoViewIfNeeded();
  await page.locator("#sideDownloadButton").click();
  await waitForPopup(page);
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 60000 });
  const refused = await popup(page);
  assert(refused.shown && refused.error && refused.inView && refused.text.includes("Select a valid company profile before generating a quote."),
    "the Excel refusal after a profile delete is not visible", refused);
  await expectErrorStaysUntilClosed(page, "Excel refusal");
  result.exportRefusal = refused.text;

  // 4. Next on Customer while offline says why once, in the step's "Quote not saved" dialog (no second popup).
  await run.openPanel(page, "customer");
  await page.locator("#clientNameEditor").fill("Synthetic Customer Renamed");
  await idle(page);
  await page.context().setOffline(true);
  await page.locator("#sideNextButton").click();
  await page.locator("#excelGeneratingModal.is-open.is-ready").waitFor({ state: "visible", timeout: 30000 });
  const offline = { dialog: (await page.locator("#excelGeneratingModal").innerText()).replace(/\s+/g, " ").trim(), popup: await popup(page) };
  await page.context().setOffline(false);
  assert(offline.dialog.includes("Quote could not be saved because the connection was interrupted.") && !offline.popup.shown,
    "Next while offline did not say why once", offline);
  await page.locator("#excelGeneratingCloseButton").click();
  await page.locator("#excelGeneratingModal").waitFor({ state: "hidden", timeout: 10000 });
  result.offlineNext = offline.dialog;

  // 4b. Start Analysis whose request cannot reach the server says so in the popup, in view (it used to fill a banner on
  // Quote Basis only).
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 30000 });
  const cutOff = (route) => (route.request().method() === "POST" ? route.abort("internetdisconnected") : route.continue());
  await page.route("**/api/jobs", cutOff);
  run.basisReady = async () => {};
  await run.buildToBasis(page);
  run.basisReady = realBasisReady;
  await waitForPopup(page);
  await page.unroute("**/api/jobs", cutOff);
  await page.waitForFunction(() => !appIsBusy(), null, { timeout: 60000 });
  const startFailed = await popup(page);
  assert(startFailed.shown && startFailed.error && startFailed.inView && !startFailed.banner && startFailed.text.startsWith("AI analysis failed."),
    "Start Analysis without a connection did not say so in view", startFailed);
  await page.locator("#stepMessagePopupClose").click();
  result.offlineAnalysis = startFailed.text;

  // 5. A save refused for its exchange rate names the rate, not a general failure.
  await page.evaluate(() => {
    const failure = quoteSessionSaveFailureFrom({ status: 400, data: { status: "blocked", reason_code: "exchange_rate_invalid",
      errors: ["Enter an exchange rate above 0 on the Customer step."] } });
    renderMessages([quoteSessionDraftSynchronizationFailureMessage(failure)], "error");
  });
  const rate = await popup(page);
  assert(rate.text === "Quote could not be saved. Enter an exchange rate above 0 on the Customer step.", "a rate refusal reads as a general failure", rate);
  await page.locator("#stepMessagePopupClose").click();
  result.rateRefusal = rate.text;

  // 6. Step messages belong to the steps: leaving for the Dashboard closes an open error, so it never covers the Dashboard.
  await page.evaluate(() => renderMessages(["Synthetic step error."], "error"));
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 30000 });
  assert(!(await popup(page)).shown, "a step error stayed open over the Dashboard");
  result.closesOnDashboard = true;

  // 7. An ordinary analysis failure (refused with a support reference) shows in the popup, not the page banner.
  await fs.writeFile(run.scenarioPath, JSON.stringify(unusable));
  run.basisReady = async () => {};
  await run.buildToBasis(page);
  run.basisReady = realBasisReady;
  await page.waitForFunction(() => !appIsBusy() && (!document.querySelector("#stepMessagePopup").hidden || !document.querySelector("#aiFailureBanner").hidden),
    null, { timeout: 60000 });
  const failed = await popup(page);
  check(failed.shown && failed.error && !failed.banner && failed.text.startsWith("AI analysis failed. Please try again.")
    && /Reference: ERR-[0-9A-F]{8}\./.test(failed.text), "an ordinary analysis failure did not show in the popup", failed);
  if (failed.shown) await page.locator("#stepMessagePopupClose").click();
  result.analysisFailure = failed.text.replace(/ERR-[0-9A-F]{8}/, "ERR-…");

  // 8. A pricing review error shows once; closed, it stays closed (and silent) on every step until its cause changes.
  await page.evaluate(() => {
    window.__stepMessageWrites = 0;
    new MutationObserver(() => { window.__stepMessageWrites += 1; }).observe(elements.stepMessagePopupMessages, { childList: true, characterData: true, subtree: true });
    setQuoteCommercialReview("pricing_reference_digest_mismatch");
    syncControlStates();
    syncControlStates();
  });
  const review = await popup(page);
  check(review.shown && review.error && review.text.startsWith("Pricing review required"), "the pricing review error did not show", review);
  if (review.shown) await page.locator("#stepMessagePopupClose").click();
  await run.openPanel(page, "customer");
  await page.locator("#clientAttentionEditor").fill("Synthetic Reviewer Two");
  await page.evaluate(() => syncControlStates());
  const afterClose = { ...(await popup(page)), writes: await page.evaluate(() => window.__stepMessageWrites) };
  check(!afterClose.shown && afterClose.writes <= 2, "a closed review error came back or was announced again", afterClose);
  await page.evaluate(() => { clearQuoteCommercialReview(); syncControlStates(); setQuoteCommercialReview("pricing_reference_digest_mismatch"); syncControlStates(); });
  check((await popup(page)).shown, "a new pricing review did not show after the earlier one cleared");
  await page.evaluate(() => { clearQuoteCommercialReview(); syncControlStates(); });
  check(!(await popup(page)).shown, "the review error stayed after the review cleared");
  result.reviewShownOnce = afterClose.writes;
  if (broken.length) throw new Error(broken.length + " step message checks failed: " + broken.join(" | "));
  run.finish(result);
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
