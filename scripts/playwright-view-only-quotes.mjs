// An admin who opens a quote made by another staff member reads every step and downloads its files, and
// nothing is written: no save, no copy, no AI or generate action. Database storage, as live: quotes have
// owners. Staff see no more than before, and own quotes are unchanged. Synthetic data, no paid AI.
import { SyntheticQuoteRun, assert } from "./lib/synthetic-quote-run.mjs";
import path from "node:path";

const run = new SyntheticQuoteRun("view-only-quotes");
const NOTE = "View only: this quote was made by another staff member. You can read every step and download its files, but not change it.";
const carpet = "synthetic-floors-synthetic-carpet-tile";
const graphic = "synthetic-graphics-synthetic-printed-wall-graphic";
const scenario = { raw: {
  analysis_findings: [], blocking_clarification_questions: [], project: { booth_width: 6, booth_depth: 6 },
  quote_basis_sections: [
    { id: "synthetic-floors", title: "Synthetic Floors", lines: [{ id: "vo-carpet", tag: "Include", text: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", confidence_pct: 95, pricing_keyword: carpet }] },
    { id: "synthetic-graphics", title: "Synthetic Graphics", lines: [{ id: "vo-graphic", tag: "Include", text: "sqm synthetic printed wall graphic", quantity: 12, unit: "sqm", confidence_pct: 95, pricing_keyword: graphic }] },
  ],
  line_items: [
    { source_basis_line_id: "vo-carpet", section: "Synthetic Floors", description: "sqm synthetic carpet tile", quantity: 36, unit: "sqm", pricing_keyword: carpet },
    { source_basis_line_id: "vo-graphic", section: "Synthetic Graphics", description: "sqm synthetic printed wall graphic", quantity: 12, unit: "sqm", pricing_keyword: graphic },
  ],
} };
const idle = (page) => page.waitForFunction(() => !appIsBusy() && !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise, null, { timeout: 60000 });
const toDashboard = async (page) => {
  await page.locator("#backToDashboardButton").click();
  await page.waitForFunction(() => state.activeAppView === "dashboard" && !appIsBusy(), null, { timeout: 20000 });
};
const shown = async (page) => (await (await page.request.get(run.server.baseUrl + "/api/quote-sessions")).json()).quote_sessions.map((item) => item.session_id).sort();
const detail = async (page, id) => {
  const response = await page.request.get(run.server.baseUrl + "/api/quote-sessions/" + id);
  return { status: response.status(), session: (await response.json().catch(() => ({}))).quote_session };
};
// Every write a page sends. /api/log is the page's own telemetry and /api/line-items/normalize stores nothing.
const watchWrites = (page) => {
  const writes = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (!["GET", "HEAD"].includes(request.method()) && pathname !== "/api/log") writes.push(request.method() + " " + pathname);
  });
  return writes;
};
const view = (page) => page.evaluate(() => ({
  panel: state.activeSidePanel,
  note: document.querySelector("#sideDrawerSubtitle").innerText.trim(),
  noteShown: document.querySelector("#sideDrawerSubtitle").getClientRects().length > 0,
  rail: Object.fromEntries([...document.querySelectorAll("button[data-side-panel]")].map((button) => [button.dataset.sidePanel, button.disabled ? button.title || "disabled" : "open"])),
  open: [...document.querySelectorAll("#panel-analysis button, #panel-analysis input, #panel-analysis select, #panel-analysis textarea, #panel-analysis [contenteditable='true']")]
    .filter((control) => control.getClientRects().length && !control.disabled && !control.matches("button[data-side-panel], #sideBackButton, #sideNextButton"))
    .map((control) => control.id || control.className),
}));
// Every control of the quote workspace and whether it can be used, as the app left it.
const controlStates = (page) => page.evaluate(() => Object.fromEntries([...document.querySelectorAll("#panel-analysis [id]")]
  .filter((control) => control.matches("button, input, select, textarea, [contenteditable]"))
  .map((control) => [control.id, control.matches("[contenteditable]") ? control.getAttribute("contenteditable") : !control.disabled])));
// Staff type into a Customer rich-text field and go to the Dashboard; the save on the way carries it.
const editCustomerAddress = async (page, text) => {
  if (await page.evaluate(() => state.activeSidePanel !== "customer")) await run.openPanel(page, "customer");
  assert(await page.locator("#clientAddressEditor").getAttribute("contenteditable") === "true", "the Customer address cannot be typed into");
  const saved = page.waitForRequest((request) => {
    if (request.method() !== "POST" || new URL(request.url()).pathname !== "/api/quote-sessions") return false;
    try { return JSON.stringify(request.postDataJSON()?.draft_state?.quoteDetails || {}).includes(text); } catch { return false; }
  }, { timeout: 30000 });
  await page.locator("#clientAddressEditor").fill(text);
  await toDashboard(page);
  await saved;
};
const readStep = async (page, panel) => {
  await run.openPanel(page, panel);
  const seen = await view(page);
  assert(seen.panel === panel && seen.note === NOTE && seen.noteShown, "the step does not say it is view only", seen);
  assert(!seen.open.length, "a control can still be used on a quote that is view only", { panel, open: seen.open });
  return seen;
};

const result = {};
try {
  await run.start(scenario, { storage: "sqlite" });
  await run.seedDatabase();

  // The operator's quotes: one generated, one left at Quote Basis. The admin's own: at Quote Basis.
  let operator = await run.signedInPage({ operator: true });
  await run.buildToBasis(operator);
  await run.tickOpenLines(operator);
  await run.confirm(operator);
  await operator.locator("#outputSidePanel.is-active").waitFor({ state: "visible", timeout: 60000 });
  await idle(operator);
  let generatePayload = null;
  operator.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/jobs" && request.postDataJSON()?.type === "generate") generatePayload = request.postDataJSON().payload;
  });
  const firstDownload = operator.waitForEvent("download", { timeout: 120000 });
  await operator.locator("#sideDownloadButton").click();
  await firstDownload;
  await idle(operator);
  const generatedId = await operator.evaluate(() => state.quoteSessionId);
  await operator.locator("#excelGeneratingCloseButton").click().catch(() => {});
  await toDashboard(operator);
  await run.buildToBasis(operator);
  await idle(operator);
  const draftId = await operator.evaluate(() => state.quoteSessionId);
  await toDashboard(operator);
  const admin = await run.signedInPage();
  await run.buildToBasis(admin);
  await idle(admin);
  const ownId = await admin.evaluate(() => state.quoteSessionId);
  await toDashboard(admin);
  const before = { generated: await detail(operator, generatedId), draft: await detail(operator, draftId) };
  const listBefore = await shown(admin);
  assert(before.generated.session.draft_version && !("view_only" in before.generated.session), "the owner's own quote is marked view only", before.generated.session);
  assert((await detail(admin, generatedId)).session.view_only === true, "the server does not say the quote is view only");

  // 1. The generated quote: every step opens and reads, its file downloads, nothing can be changed.
  const writes = watchWrites(admin);
  await run.openDashboardQuote(admin, generatedId);
  let seen = await view(admin);
  assert(seen.panel === "output" && Object.values(seen.rail).every((value) => value === "open"), "a generated quote of another user does not open all its steps", seen);
  for (const panel of ["images", "customer", "quote_company", "basis", "output"]) await readStep(admin, panel);
  result.output = await admin.locator("#pricingMatchesBody").innerText();
  assert(result.output.includes("synthetic carpet tile") && result.output.includes("518.40"), "the Output rows do not read", result.output);
  const cell = admin.locator('#pricingMatchesBody [data-output-price-entry="quote"]').first();
  await cell.dblclick();
  await cell.focus();
  await cell.press("Enter");
  assert(!(await admin.locator("#pricingMatchesBody [data-output-editor-field]").count()), "an Output price can be edited");
  const file = admin.waitForEvent("download", { timeout: 30000 });
  await admin.locator("#sideDownloadButton").click();
  result.outputDownload = (await file).suggestedFilename();
  await run.openPanel(admin, "basis");
  await (await run.basisControl(admin, "vo-carpet", '[data-basis-tag="Exclude"]')).click({ force: true });
  await (await run.basisControl(admin, "vo-carpet", "[data-basis-quantity-edit]")).click({ force: true });
  assert(await admin.evaluate(() => state.quoteBasisSections.flatMap((section) => section.lines).find((line) => line.id === "vo-carpet").tag === "Include")
    && !(await admin.locator("#basisReviewSurface .basis-quantity-text input").count()), "Quote Basis can be changed");
  assert((await admin.locator("#basisReviewSurface").innerText()).includes("synthetic printed wall graphic"), "Quote Basis does not read");
  // Next moves on without saving, from every step.
  await run.openPanel(admin, "images");
  for (const panel of ["customer", "quote_company", "basis", "output"]) {
    await admin.locator("#sideNextButton").click();
    await admin.waitForFunction((name) => state.activeSidePanel === name, panel, { timeout: 10000 });
  }
  result.screenshots = [];
  for (const [width, height] of [[1440, 900], [375, 812]]) {
    await admin.setViewportSize({ width, height });
    for (const panel of ["basis", "output"]) {
      await admin.evaluate((name) => setSidePanel(name), panel);
      await admin.evaluate(() => window.scrollTo(0, 0));
      const shot = path.join(run.runRoot, `view-only-${panel}-${width}.png`);
      await admin.screenshot({ path: shot });
      result.screenshots.push(shot);
      const box = await admin.locator("#sideDrawerSubtitle").boundingBox();
      assert(box && box.x >= 0 && box.x + box.width <= width && await admin.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "the note does not fit the screen", { width, box });
    }
  }
  await admin.setViewportSize({ width: 1280, height: 900 });
  // A reload keeps it view only, and leaving goes straight to the Dashboard.
  await admin.reload({ waitUntil: "domcontentloaded" });
  await admin.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && state.activeAppView === "quote" && !appIsBusy(), null, { timeout: 30000 });
  assert(await admin.evaluate(() => state.quoteViewOnly === true && state.activeSidePanel === "output"), "a reload made the quote editable or moved it to another step");
  await readStep(admin, "basis");
  await toDashboard(admin);
  const dashboardFile = admin.waitForEvent("download", { timeout: 30000 });
  await admin.locator(`#dashboardSessionsList [data-quote-session-id="${generatedId}"]`).click();
  await admin.locator("#dashboardSelectedSessionPanel .dashboard-export-action-row").getByText("XLSX").first().click();
  result.dashboardDownload = (await dashboardFile).suggestedFilename();

  // 2. The draft at Quote Basis: readable, and the step without content says so.
  await run.openDashboardQuote(admin, draftId);
  seen = await view(admin);
  assert(seen.panel === "basis" && seen.rail.output === "This quote has no Output yet.", "a draft of another user does not open at Quote Basis", seen);
  for (const panel of ["images", "customer", "quote_company", "basis"]) await readStep(admin, panel);
  assert(await admin.evaluate(() => ["Synthetic Customer", "Synthetic Project"].every((text) => document.querySelector("#customerDetailsPanel").textContent.includes(text))), "the Customer step does not read");
  await toDashboard(admin);
  assert(!writes.length, "opening another user's quote wrote something", writes);
  const after = { generated: await detail(operator, generatedId), draft: await detail(operator, draftId) };
  for (const id of ["generated", "draft"]) {
    assert(after[id].session.draft_version === before[id].session.draft_version && after[id].session.updated_at === before[id].session.updated_at, "another user's quote changed", id);
  }

  // 3. Direct requests, whatever they carry: refused, and no quote is made.
  const { session_id, customer_summary, quote_company_profile, pricing_reference, commercials, draft_state, draft_version } = before.draft.session;
  const body = { session_id, customer_summary: { ...customer_summary, project_name: "Synthetic Overwrite" }, quote_company_profile, pricing_reference, commercials, status: {}, draft_state };
  const refused = {};
  for (const [name, payload] of Object.entries({
    withoutVersion: body, withVersion: { ...body, expected_draft_version: draft_version }, asNew: { ...body, expected_draft_version: "" },
  })) {
    const answer = await run.api(admin, "POST", "/api/quote-sessions", payload);
    refused[name] = answer.status;
    assert(answer.status === 403 && !answer.body.quote_session, "a direct save of another user's quote was not refused", { name, answer });
  }
  // The owner's own generate request, sent by the admin: refused before a file is built, with or without a version.
  const { expected_draft_version, ...unversioned } = generatePayload;
  // Also when only the top-level session_id names the quote, as the final save would read it.
  const { quote_session: { session_id: named, ...unnamed }, ...rest } = unversioned;
  for (const [name, payload] of Object.entries({
    "generate without a version": unversioned, "generate with a version": { ...unversioned, expected_draft_version },
    "generate naming the quote at the top": { ...rest, quote_session: unnamed, session_id: named },
  })) {
    const started = await run.api(admin, "POST", "/api/jobs", { type: "generate", payload });
    assert(started.status === 202 && started.body.job_id, "the generate job was not started", started);
    let job = null;
    for (let poll = 0; poll < 120 && !["failed", "blocked", "completed", "needs_review"].includes(job?.status); poll++) {
      await admin.waitForTimeout(250);
      job = await (await admin.request.get(run.server.baseUrl + "/api/jobs/" + started.body.job_id)).json();
    }
    refused[name] = job.status;
    assert(["failed", "blocked"].includes(job.status) && !job.result?.quote_session && !job.result?.files?.length, "a generate of another user's quote was not refused", { name, job });
  }
  assert((await detail(operator, generatedId)).session.exports.xlsx.sha256 === before.generated.session.exports.xlsx.sha256, "a generate by another user changed the quote's file");
  result.refused = refused;
  assert(JSON.stringify(await shown(admin)) === JSON.stringify(listBefore), "a direct save left a new quote", await shown(admin));
  assert((await detail(operator, draftId)).session.draft_version === before.draft.session.draft_version, "a direct save changed the quote");

  // 4. Own quotes are unchanged, also right after a view-only one in the same page.
  await run.openDashboardQuote(admin, ownId);
  assert(await admin.evaluate(() => state.quoteViewOnly === false && document.querySelector("#sideDrawerSubtitle").textContent !== ""), "the admin's own quote opened view only");
  if (await admin.evaluate(() => state.activeSidePanel !== "basis")) await run.openPanel(admin, "basis");
  await run.tag(admin, "vo-carpet", "Exclude");
  assert(await admin.evaluate(() => state.quoteBasisSections.flatMap((section) => section.lines).find((line) => line.id === "vo-carpet").tag === "Exclude"), "the admin's own quote cannot be edited");
  await toDashboard(admin);
  assert(writes.some((entry) => entry === "POST /api/quote-sessions"), "the admin's own quote was not saved", writes);
  // Leaving view only gives every control back: an own quote and a New Quote opened right after one, in the same page.
  await run.openDashboardQuote(admin, generatedId);
  await readStep(admin, "customer");
  await toDashboard(admin);
  await run.openDashboardQuote(admin, ownId);
  await editCustomerAddress(admin, "Synthetic own address after viewing");
  const newQuote = async () => {
    await admin.locator("#newQuoteButton").click();
    await admin.locator("#imageIntake").waitFor({ state: "visible" });
    await admin.waitForFunction(() => !appIsBusy(), null, { timeout: 20000 });
    return controlStates(admin);
  };
  // The same page's New Quote after its own quote, and then after a view-only one.
  const afterOwn = await newQuote();
  await toDashboard(admin);
  await run.openDashboardQuote(admin, generatedId);
  await readStep(admin, "basis");
  await toDashboard(admin);
  const afterView = await newQuote();
  const differs = Object.keys({ ...afterOwn, ...afterView }).filter((id) => afterOwn[id] !== afterView[id]);
  assert(await admin.evaluate(() => state.quoteViewOnly === false) && !differs.length, "a New Quote after a view-only quote differs from one after an own quote",
    Object.fromEntries(differs.map((id) => [id, [afterOwn[id], afterView[id]]])));
  await admin.locator("#imageInput").setInputFiles({ name: "view-only-new-render.png", mimeType: "image/png", buffer: await run.png(admin, 800, 600) });
  await admin.locator("#fileList .file-item").first().waitFor({ state: "visible" });
  await admin.locator("#sideNextButton").click();
  await admin.locator("#customerDetailsPanel").waitFor({ state: "visible" });
  await editCustomerAddress(admin, "Synthetic new address after viewing");

  // 5. Staff see no more than before: not the admin's quote, and their own opens for editing.
  assert((await detail(operator, ownId)).status === 404 && !(await shown(operator)).includes(ownId), "an operator can see the admin's quote");
  result.operatorSees = (await shown(operator)).length;
  await operator.reload({ waitUntil: "domcontentloaded" });
  await operator.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && !appIsBusy(), null, { timeout: 30000 });
  if (await operator.evaluate(() => state.activeAppView !== "dashboard")) await toDashboard(operator);
  await run.openDashboardQuote(operator, draftId);
  assert(await operator.evaluate(() => state.quoteViewOnly === false) && (await view(operator)).open.length > 0, "an operator's own quote opened view only");
  await operator.context().close();
  await admin.context().close();

  // 6. On a copy service (another session secret) the recovery scope differs: an own quote still locks at
  // Customer until Next, and another user's quote still opens for reading.
  await run.api(await run.signedInPage(), "POST", "/__harness/session-secret", { copy: true });
  operator = await run.signedInPage({ operator: true });
  await run.openDashboardQuote(operator, generatedId);
  seen = await view(operator);
  assert(seen.panel === "customer" && seen.rail.output.startsWith("Click Next in Customer"), "an own quote with another recovery scope does not lock", seen);
  const copyAdmin = await run.signedInPage();
  const copyWrites = watchWrites(copyAdmin);
  await run.openDashboardQuote(copyAdmin, generatedId);
  seen = await view(copyAdmin);
  assert(seen.panel === "output" && Object.values(seen.rail).every((value) => value === "open") && seen.note === NOTE, "another user's quote locks when the recovery scope differs", seen);
  await toDashboard(copyAdmin);
  assert(!copyWrites.length, "viewing on the copy wrote something", copyWrites);
  run.finish(result);
} catch (error) {
  run.fail(error);
} finally {
  await run.close();
}
