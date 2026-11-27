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
const NO_BOOTH = "Please upload the booth render images first so I can analyze the design and prepare the quote. This analysis was still charged.";
const result = {};

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
    (result.screenshots ||= {})[width] = file;
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await expectErrorStaysUntilClosed(page, "no booth");
  result.noBooth = noBoothSeen.text.replace(/ERR-[0-9A-F]{8}/, "ERR-…");

  // 2. A warning fades by itself, and a warning never hides an error.
  await page.evaluate(() => renderMessages(["Synthetic warning."], "warn"));
  assert((await popup(page)).shown && !(await popup(page)).error, "a warning did not show");
  await page.waitForFunction(() => document.querySelector("#stepMessagePopup").hidden, null, { timeout: 10000 });
  await page.evaluate(() => { renderMessages(["Synthetic error."], "error"); renderMessages(["Synthetic warning."], "warn"); renderMessages([]); });
  const kept = await popup(page);
  assert(kept.shown && kept.error && kept.text === "Synthetic error.", "a warning or a clear hid an open error", kept);
  await page.locator("#stepMessagePopupClose").click();
  result.warningFades = true;

  // 3. A real quote; its company profile is deleted; Download Excel says why, in view, while Output is scrolled down.
  await fs.writeFile(run.scenarioPath, JSON.stringify(booth));
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 30000 });
  await run.buildToBasis(page);
  await run.tickOpenLines(page);
  await run.confirm(page);
  await page.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await idle(page);
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
  run.finish(result);
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
