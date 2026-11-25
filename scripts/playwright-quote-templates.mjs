// Settings > Quote Templates, end to end in a real browser (#167 S5).
// Runs the loopback password harness on a new SQLite database (synthetic users and data only), then:
// admin copies Standard, moves a block, adds and renames a table column, changes the heading colour,
// previews the PDF and Excel file, saves, makes it the workspace default, picks it in Company Profile,
// fills the workspace to the 10-template limit and deletes one; an operator sees Settings greyed out.
// It also checks every way out of the editor with unsaved changes, what two admins working at once see,
// the profile dropdown by keyboard and in its own-layout, read-only and unavailable states, and that a
// support code on the page is in the server log.
// QT_SCREENSHOT_DIR (default _tmp/quote-templates-shots) receives screenshots at 1440/1024/880/375 px.
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { chromium } from "playwright";
import { signIn } from "./internal-password-browser-test.mjs";

const OPERATOR = ["alpha-operator" + "@" + "example.test", "synthetic-browser-operator-password"];
const SHOTS = process.env.QT_SCREENSHOT_DIR || path.join("_tmp", "quote-templates-shots");
const WIDTHS = [1440, 1024, 880, 375];
const TEAL = "rgb(15, 118, 110)";
mkdirSync(SHOTS, { recursive: true });

// Everything the server writes (database, logs) goes to a folder of this run, removed at the end.
const RUN = mkdtempSync(path.join(os.tmpdir(), "qt-run-"));
const dir = (name) => path.join(RUN, name);
const PYTHON = process.env.PYTHON || "python";
const harness = spawn(PYTHON, ["tests/internal_password_browser_harness.py",
  process.cwd(), ...["data", "output", "tmp", "logs", "pricing"].map(dir), "--storage", "sqlite"], {
  cwd: process.cwd(), env: { ...process.env, PYTHONUNBUFFERED: "1", SQAG_DISABLE_DOTENV: "1" },
  stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
});
harness.stderr.resume();
let browser;
const step = (text) => console.log(`- ${text}`);

// One statement on this run's synthetic database, for states no screen can make (a profile from before
// templates existed; a database without migration 013).
const databaseFiles = () => readdirSync(dir("tmp")).filter((name) => name.endsWith(".sqlite3"));
function sql(statement, ...values) {
  const [file] = databaseFiles();
  execFileSync(PYTHON, ["-c", "import sqlite3, sys\nc = sqlite3.connect(sys.argv[1])\nc.execute(sys.argv[2], sys.argv[3:])\nc.commit()",
    path.join(dir("tmp"), file), statement, ...values]);
}

function serverLogHolds(reference) {
  const files = readdirSync(dir("logs"), { recursive: true }).map(String).filter((name) => name.endsWith(".jsonl") && !name.startsWith("client"));
  return files.some((name) => readFileSync(path.join(dir("logs"), name), "utf8").includes(`"error_reference": "${reference}"`));
}

const supportCode = (text) => text.match(/contact support with code (ERR-[0-9A-F]{8})/)?.[1] || "";
const focusId = (page) => page.evaluate(() => document.activeElement.id || document.activeElement.tagName);

const editorState = (page) => page.evaluate(() => ({
  settingsOpen: !document.querySelector("#pricingReferenceModal").hidden,
  editing: Boolean(quoteTemplatesSettings.editor),
  cancel: document.querySelector("#qtCancelButton").textContent,
  note: document.querySelector("#qtFooterNote").textContent,
}));
const ASKED = { settingsOpen: true, editing: true, cancel: "Discard changes", note: "Your unsaved changes will be lost." };
const EDITING = { settingsOpen: true, editing: true, cancel: "Cancel", note: "Unsaved changes" };

const listReady = (page) => page.waitForFunction(() => quoteTemplatesSettings.list.status === "ready");

async function openTemplates(page) {
  if (await page.locator("#pricingReferenceModal").isHidden()) await page.locator("#settingsButton").click();
  await page.locator("#settingsTemplatesTab").click();
  await page.locator("#qtList .qt-row").first().waitFor();
}

async function startChangedCopy(page, name) {
  await openTemplates(page);
  await page.getByRole("button", { name: "Make a copy", exact: true }).first().click();
  await page.locator("#qtNameInput").fill(name);
  assert.deepEqual(await editorState(page), EDITING);
}

async function discard(page) {
  if ((await editorState(page)).cancel === "Cancel") await page.locator("#qtCancelButton").click();
  assert.deepEqual(await editorState(page), ASKED);
  await page.locator("#qtCancelButton").click();
  assert.equal((await editorState(page)).editing, false, "Discard changes closes the editor");
}

async function shot(page, name, target = null) {
  for (const width of WIDTHS) {
    await page.setViewportSize({ width, height: 900 });
    await page.waitForTimeout(150);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    assert.ok(overflow <= 0, `${name} scrolls sideways at ${width} px (${overflow} px)`);
    await (target ? page.locator(target) : page).screenshot({ path: path.join(SHOTS, `${name}-${width}.png`) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
}

// The outline a keyboard user sees once the control has focus and its ring has finished fading in
// (the Settings tabs and close X have `transition: all 0.2s`). Focus is set again on each try because
// the opening window may still be placing its own focus; the ring is read only when this control has
// keyboard focus and no transition is running, so a slow runner never reads a half-drawn ring.
async function focusRing(page, selector) {
  const target = page.locator(selector);
  let ring = null;
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await page.keyboard.press("Shift");
    await target.focus();
    await page.waitForTimeout(250);
    ring = await target.evaluate((node) => {
      const style = getComputedStyle(node);
      return {
        settled: document.activeElement === node && node.matches(":focus-visible") && node.getAnimations().length === 0,
        ring: { style: style.outlineStyle, width: style.outlineWidth, color: style.outlineColor, offset: style.outlineOffset },
      };
    });
    if (ring.settled) break;
  }
  assert.ok(ring.settled, `${selector} never held keyboard focus with its ring drawn`);
  return ring.ring;
}

async function draftState(page) {
  return page.evaluate(() => {
    const t = quoteTemplatesSettings.editor.draft.t;
    return {
      body: t.blocks.filter((block) => block.area === "body").map((block) => block.type),
      table: t.blocks.find((block) => block.type === "item_table"),
      heading: t.theme.heading_color,
      problems: quoteTemplatesSettings.editor.draft.problems(),
    };
  });
}

try {
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Synthetic password harness readiness timeout.")), 30000);
    harness.stdout.once("data", (chunk) => { clearTimeout(timer); resolve(chunk.toString().trim().split(/\r?\n/)[0]); });
    harness.once("exit", (code) => { clearTimeout(timer); reject(new Error(`Synthetic password harness exited (${code}).`)); });
  });
  assert.equal(new URL(base).hostname, "127.0.0.1");
  const channel = process.env.CI ? undefined : "chrome";
  browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  await context.route("**/*", (route) => (new URL(route.request().url()).origin === base ? route.fallback() : route.abort("blockedbyclient")));
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    // A failed response is judged by its status in the response listener below.
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) problems.push(`console: ${message.text()}`);
  });
  let expectedFailures = []; // statuses a step provokes on purpose on the template routes
  page.on("response", (response) => {
    const url = new URL(response.url());
    const expected = url.pathname.startsWith("/api/settings/quote-templates") && expectedFailures.includes(response.status());
    if (response.status() >= 400 && !expected && !(url.pathname === "/api/settings/quote-templates/preview" && response.status() === 503)) {
      problems.push(`${response.status()} ${url.pathname}`);
    }
  });
  await page.route("**/api/log", (route) => route.fulfill({ status: 204, body: "" }));

  step("admin signs in and opens Settings");
  await signIn(page, base);
  await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
  await page.locator("#settingsButton").click();
  await page.locator("#pricingReferenceModal").waitFor({ state: "visible" });
  assert.equal(await page.locator("#settingsPricingTab").getAttribute("aria-selected"), "true");
  for (const selector of ["#pricingReferenceCloseButton", "#pricingReferenceManageTab", "#pricingReferenceImportTab", "#settingsTemplatesTab"]) {
    assert.deepEqual(await focusRing(page, selector), { style: "solid", width: "2px", color: TEAL, offset: "2px" }, `${selector} focus outline`);
  }

  step("Quote Templates lists the built-in Standard template");
  await page.locator("#settingsTemplatesTab").click();
  await page.locator("#qtList .qt-row").first().waitFor();
  assert.equal(await page.locator("#qtList .qt-row").count(), 1);
  assert.equal(await page.locator("#qtCounter").innerText(), "0 of 10 saved");
  assert.ok(await page.locator("#qtEmpty").isVisible());
  assert.ok(await page.locator("#settingsPricingFooter").isHidden());
  await shot(page, "list-empty", ".pricing-reference-settings-panel");

  step("copy Standard, move Terms above Totals, add and rename a column, change the heading colour");
  await page.getByRole("button", { name: "Make a copy", exact: true }).click();
  await page.locator("#qtEditorView").waitFor({ state: "visible" });
  assert.equal(await page.locator("#qtNameInput").inputValue(), "Standard copy");
  assert.ok(await page.locator("#qtEditorFooter").isVisible());
  assert.ok(await page.locator(".pricing-reference-settings-panel.is-wide").count());
  assert.equal(await page.locator("#qtStatus").innerText(), "Ready to save");
  await shot(page, "editor-page-style", ".pricing-reference-settings-panel");
  const terms = page.locator('#qtPaperWrap [data-block-type="terms"]');
  await terms.click();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  let draft = await draftState(page);
  assert.deepEqual(draft.body.slice(0, 3), ["item_table", "terms", "totals"]);
  assert.equal(await page.locator("#qtStatus").innerText(), "Ready to save");
  const table = page.locator('#qtPaperWrap [data-block-type="item_table"]');
  await table.click();
  await page.keyboard.press("Shift+ArrowRight");
  await page.locator("#qtSide").getByRole("button", { name: "Add column" }).click();
  draft = await draftState(page);
  assert.equal(draft.table.span, 6);
  const unit = draft.table.columns.find((column) => column.key === "unit");
  assert.deepEqual([unit.col, unit.label], [6, "Unit"]);
  await page.getByLabel("Heading for the unit column").fill("Units");
  draft = await draftState(page);
  assert.equal(draft.table.columns.find((column) => column.key === "unit").label, "Units");
  assert.deepEqual(draft.problems, []);
  await shot(page, "editor-item-table", ".pricing-reference-settings-panel");
  await page.locator("#qtSide").getByRole("button", { name: "Done, show page style" }).click();
  await page.getByLabel("Heading colour: hex code").fill("#1D4ED8");
  await page.getByLabel("Heading colour: hex code").press("Tab");
  assert.equal((await draftState(page)).heading, "#1D4ED8");

  step("a problem is marked and blocks Save until fixed");
  await page.getByRole("button", { name: "Select column C" }).click();
  await page.locator("#qtSide").getByLabel("Width", { exact: true }).fill("20");
  await page.locator("#qtSide").getByLabel("Width", { exact: true }).press("Tab");
  assert.match(await page.locator("#qtStatus").innerText(), /thing.? to fix/);
  await page.locator("#qtStatus").click();
  assert.ok(await page.locator("#qtSideProblems").isVisible());
  const sent = [];
  const countSent = (request) => { if (request.method() === "POST" && request.url().includes("/api/settings/")) sent.push(new URL(request.url()).pathname); };
  page.on("request", countSent);
  await page.locator("#qtSaveButton").click();
  assert.equal(await page.locator("#qtFooterNote").innerText(), "Fix the item marked in red, then save.");
  await page.locator("#qtPreviewExcel").click();
  assert.equal(await page.locator("#qtFooterNote").innerText(), "Fix the item marked in red, then preview.");
  await page.waitForTimeout(300);
  page.off("request", countSent);
  assert.deepEqual(sent, [], "nothing is sent while something is marked to fix");
  assert.equal((await editorState(page)).editing, true);
  await shot(page, "editor-problem", ".pricing-reference-settings-panel");
  await page.getByRole("button", { name: "Select column C" }).click();
  await page.locator("#qtSide").getByLabel("Width", { exact: true }).fill("49.375");
  await page.locator("#qtSide").getByLabel("Width", { exact: true }).press("Tab");
  assert.equal(await page.locator("#qtStatus").innerText(), "Ready to save");

  step("preview the PDF and the Excel file");
  const pdfResponse = page.waitForResponse((response) => response.url().endsWith("/api/settings/quote-templates/preview"));
  const pdfDownload = page.waitForEvent("download", { timeout: 60000 }).catch(() => null);
  await page.locator("#qtPreviewPdf").click();
  const pdf = await pdfResponse;
  if (process.env.CI) assert.equal(pdf.status(), 200, "PDF preview needs LibreOffice in CI");
  if (pdf.status() === 200) {
    const file = await pdfDownload;
    assert.equal(file?.suggestedFilename(), "quote-template-preview.pdf");
    const saved = path.join(SHOTS, "quote-template-preview.pdf");
    await file.saveAs(saved);
    assert.equal(readFileSync(saved).subarray(0, 5).toString("latin1"), "%PDF-");
  } else {
    assert.equal(pdf.status(), 503);
    await page.locator("#qtFooterNote").getByText("PDF preview is not available", { exact: false }).waitFor();
  }
  const download = page.waitForEvent("download");
  await page.locator("#qtPreviewExcel").click();
  assert.equal((await download).suggestedFilename(), "quote-template-preview.xlsx");
  await page.locator("#qtFooterNote").getByText("The sample Excel file was downloaded.").waitFor();
  assert.equal(await focusId(page), "qtPreviewExcel", "the pressed preview button keeps the focus");

  const csrf = await page.evaluate(() => ({ [state.csrfHeaderName]: state.csrfToken }));
  // What another admin does meanwhile, through the same routes the page uses.
  const otherAdmin = async (method, route, data) => {
    const response = await context.request.fetch(base + route, { method, headers: { ...csrf, Origin: base }, ...(data ? { data } : {}) });
    assert.equal(response.status(), 200, `${method} ${route}`);
    return response.json();
  };
  const API = "/api/settings/quote-templates";

  step("save, then make it the workspace default");
  await page.locator("#qtSaveButton").click();
  await page.locator("#qtListStatus").getByText("Saved.").waitFor();
  assert.equal(await page.locator("#qtCounter").innerText(), "1 of 10 saved");
  const copyId = await page.evaluate(() => quoteTemplatesSettings.savedTemplates()[0].id);
  assert.equal(await focusId(page), `qtMore-${copyId}`, "the saved template's row has the focus");
  await page.getByRole("button", { name: "More actions for Standard copy" }).click();
  await page.getByRole("menuitem", { name: "Make workspace default" }).click();
  await page.locator("#qtListStatus").getByText("Default changed.").waitFor();
  assert.equal(await focusId(page), `qtMore-${copyId}`, "the new default's row has the focus");
  assert.equal(await page.locator("#qtList .qt-row").nth(1).locator(".qt-badge").first().innerText(), "Default");
  await shot(page, "list-saved", ".pricing-reference-settings-panel");

  step("every way out of the editor asks, and only Discard changes discards");
  await startChangedCopy(page, "Unsaved");
  const ways = [
    ["the close X", () => page.locator("#pricingReferenceCloseButton").click()],
    ["the close X again", () => page.locator("#pricingReferenceCloseButton").click()],
    ["a double click on the close X", () => page.locator("#pricingReferenceCloseButton").dblclick()],
    ["a click outside the window", () => page.mouse.click(10, 450)],
    ["a double click outside the window", () => page.mouse.dblclick(10, 450)],
    ["All templates", () => page.locator("#qtBackButton").click()],
    ["a double click on All templates", () => page.locator("#qtBackButton").dblclick()],
    ["Enter twice on the close X", async () => {
      await page.locator("#pricingReferenceCloseButton").focus();
      await page.keyboard.press("Enter");
      await page.keyboard.press("Enter");
    }],
    ["Enter held down on the close X", async () => {
      await page.locator("#pricingReferenceCloseButton").focus();
      for (let n = 0; n < 4; n += 1) await page.keyboard.down("Enter");
      await page.keyboard.up("Enter");
    }],
    ["Escape in the Pricing Reference section", async () => {
      await page.locator("#settingsPricingTab").click();
      await page.keyboard.press("Escape");
      assert.equal(await page.locator("#settingsTemplatesTab").getAttribute("aria-selected"), "true");
    }],
    ["Cancel in the Pricing Reference section, Enter twice", async () => {
      await page.locator("#settingsPricingTab").click();
      await page.locator("#pricingReferenceCancelButton").focus();
      await page.keyboard.press("Enter");
      await page.keyboard.press("Enter");
    }],
  ];
  for (const [label, leave] of ways) {
    await leave();
    assert.deepEqual(await editorState(page), ASKED, label);
    assert.equal(await page.locator("#qtNameInput").inputValue(), "Unsaved", label);
    // Any edit takes the question back, so each way above is asked from the start.
    await page.locator("#qtNameInput").fill("Unsaved more");
    assert.deepEqual(await editorState(page), EDITING, `${label}, then an edit`);
    await page.locator("#qtNameInput").fill("Unsaved");
  }
  await page.locator("#qtCancelButton").dblclick();
  assert.deepEqual(await editorState(page), ASKED, "a double click on Cancel only asks");
  // Enter held down on Cancel asks; the repeats of the same press never discard.
  await page.locator("#qtNameInput").fill("Unsaved more");
  await page.locator("#qtCancelButton").focus();
  for (let n = 0; n < 4; n += 1) await page.keyboard.down("Enter");
  await page.keyboard.up("Enter");
  assert.deepEqual(await editorState(page), ASKED, "Enter held down on Cancel only asks");
  // An edit on the page (not only in the name) takes the question back too.
  await page.locator('#qtPaperWrap [data-block-type="terms"]').click();
  assert.deepEqual(await editorState(page), ASKED, "selecting a block is not an edit");
  await page.keyboard.press("ArrowUp");
  assert.deepEqual(await editorState(page), EDITING, "moving a block is an edit");
  // A note about something else gives way to the warning again.
  await page.locator("#pricingReferenceCloseButton").click();
  await page.route(`**${API}/preview`, (route) => route.fulfill({ status: 429, contentType: "application/json", body: "{}" }));
  expectedFailures = [429];
  await page.locator("#qtPreviewExcel").click();
  await page.locator("#qtFooterNote").getByText("Too many previews in a minute.").waitFor();
  await page.unroute(`**${API}/preview`);
  await page.locator("#qtFooterNote").getByText("Your unsaved changes will be lost.").waitFor({ timeout: 8000 });
  assert.deepEqual(await editorState(page), ASKED);
  expectedFailures = [];

  step("leaving the page asks too");
  let leaveAsked = "";
  page.once("dialog", async (dialog) => { leaveAsked = dialog.type(); await dialog.dismiss(); });
  await page.reload({ timeout: 3000 }).catch(() => null);
  assert.equal(leaveAsked, "beforeunload");
  assert.equal((await editorState(page)).editing, true);
  await shot(page, "editor-discard", ".pricing-reference-settings-panel");
  await discard(page);
  assert.equal(await focusId(page), "qtMore-standard", "back on the row the copy came from");
  assert.equal(await page.locator("#qtCounter").innerText(), "1 of 10 saved");

  step("Settings stays open while a save runs, and the page takes no edits until it is back");
  await startChangedCopy(page, "Saved slowly");
  let release = null;
  await page.route(`**${API}`, async (route) => {
    if (route.request().method() === "POST") await new Promise((resolve) => { release = resolve; });
    await route.fallback();
  });
  await page.locator("#qtSaveButton").click();
  await page.locator("#qtSaveButton").getByText("Saving").waitFor();
  await page.locator("#pricingReferenceCloseButton").click();
  await page.mouse.click(10, 450);
  let during = await editorState(page);
  assert.deepEqual([during.settingsOpen, during.editing, during.cancel], [true, true, "Cancel"]);
  assert.notEqual(during.note, ASKED.note);
  await page.locator("#qtNameInput").focus();
  await page.keyboard.type(" lost edit");
  assert.equal(await page.locator("#qtNameInput").inputValue(), "Saved slowly");
  release();
  await page.locator("#qtListStatus").getByText('"Saved slowly" is ready').waitFor();
  await page.unroute(`**${API}`);
  assert.equal((await editorState(page)).settingsOpen, true);
  assert.equal(await page.locator("#qtCounter").innerText(), "2 of 10 saved");

  step("Enter in a template field never saves the pricing reference");
  const catalog = JSON.parse(readFileSync("tests/fixtures/quote-generator/pricing-references/synthetic-exhibition-fixture-pricing/pricing-catalog.json", "utf8"));
  await otherAdmin("POST", "/api/settings/pricing-references", {
    id: "synthetic-reference", label: "Synthetic reference", tax: { label: "GST", rate: 0.09 }, currency: "SGD", items: catalog.items.slice(0, 2),
  });
  await page.locator("#pricingReferenceCloseButton").click();
  await page.locator("#pricingReferenceModal").waitFor({ state: "hidden" });
  await page.evaluate(() => loadProfiles());
  await page.locator("#settingsButton").click();
  await page.locator("#pricingReferenceManageStatus").getByRole("button", { name: "Review" }).click();
  await page.locator("#pricingReferenceTableBody [data-preview-field='description']").first().fill("Synthetic changed row");
  await page.locator("#pricingReferenceTableCloseButton").click();
  assert.equal(await page.evaluate(() => pricingReferenceHasPendingChanges(state.pendingPricingReference)), true, "a pricing change waits to be saved");
  await startChangedCopy(page, "Enter pressed");
  sent.length = 0;
  page.on("request", countSent);
  await page.locator("#qtNameInput").press("Enter");
  await page.getByLabel("Heading colour: hex code").press("Enter");
  await page.getByLabel("Width of column C").press("Enter");
  await page.locator('#qtPaperWrap [data-block-type="item_table"]').click();
  await page.getByLabel("Heading for the quantity column").press("Enter");
  await page.waitForTimeout(300);
  page.off("request", countSent);
  assert.deepEqual(sent, []);
  assert.equal((await editorState(page)).editing, true);
  await discard(page);

  step("Edit saves over the same template, with the save number it was opened at");
  await page.getByRole("button", { name: "Edit Saved slowly", exact: true }).click();
  await page.locator("#qtNameInput").fill("Saved twice");
  const update = page.waitForRequest((request) => request.method() === "POST" && request.url().includes(`${API}/`));
  await page.locator("#qtSaveButton").click();
  assert.equal((await update).postDataJSON().save_number, 1);
  await page.locator("#qtListStatus").getByText('"Saved twice" is ready').waitFor();
  assert.equal(await page.locator("#qtCounter").innerText(), "2 of 10 saved");
  let records = await page.evaluate(() => quoteTemplatesSettings.savedTemplates().map(({ id, name, save_number }) => ({ id, name, save_number })));
  assert.deepEqual(records.map(({ name, save_number }) => [name, save_number]), [["Standard copy", 1], ["Saved twice", 2]]);

  step("another admin saved it first: the changes stay, and opening it again shows the new version");
  const shared = records[1].id;
  const layout = (name) => page.evaluate(([id, value]) => ({ ...quoteTemplatesSettings.template(id).definition, name: value }), [shared, name]);
  await page.getByRole("button", { name: "Edit Saved twice", exact: true }).click();
  await page.locator("#qtNameInput").fill("My version");
  await otherAdmin("POST", `${API}/${shared}`, { definition: await layout("Their version"), save_number: 2 });
  expectedFailures = [409];
  for (let n = 0; n < 2; n += 1) { // trying again is refused again
    await page.locator("#qtSaveButton").click();
    await page.locator("#qtSaveError").getByText("Someone saved this template since you opened it. Go back to All templates and open it again.").waitFor();
    assert.equal(await page.locator("#qtNameInput").inputValue(), "My version");
  }
  await page.locator("#qtBackButton").click();
  await discard(page);
  await page.getByRole("button", { name: "Edit Their version", exact: true }).click();
  assert.equal(await page.locator("#qtNameInput").inputValue(), "Their version");
  await page.locator("#qtNameInput").fill("My version");
  await page.locator("#qtSaveButton").click();
  await page.locator("#qtListStatus").getByText('"My version" is ready').waitFor();

  step("Delete never removes a version the dialog did not show");
  await page.getByRole("button", { name: "More actions for My version" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await otherAdmin("POST", `${API}/${shared}`, { definition: await layout("Renamed meanwhile"), save_number: 4 });
  await page.locator("#qtDeleteConfirm").click();
  await page.locator("#qtDeleteError").getByText("Someone changed this template since the list loaded.").waitFor();
  await page.waitForFunction(() => quoteTemplatesSettings.list.status === "ready" && quoteTemplatesSettings.list.templates.some((item) => item.name === "Renamed meanwhile"));
  assert.equal(await page.locator("#qtDeleteTitle").innerText(), 'Delete "My version"?');
  assert.ok(await page.locator("#qtDeleteConfirm").isDisabled(), "Delete stays off until the dialog is opened again");
  assert.equal(await focusId(page), "qtDeleteCancel");
  await page.evaluate(() => quoteTemplatesSettings.confirmDelete()); // the button is off; the request itself is refused too
  assert.ok(await page.locator("#qtDeleteModal").isVisible());
  await listReady(page);
  await page.locator("#qtDeleteCancel").click();
  assert.equal(await focusId(page), `qtMore-${shared}`);
  records = (await (await context.request.get(base + API)).json()).templates.map(({ name }) => name);
  assert.deepEqual(records, ["Standard", "Standard copy", "Renamed meanwhile"]);

  step("a template another admin deleted: Save and Delete say so");
  await page.getByRole("button", { name: "Edit Renamed meanwhile", exact: true }).click();
  await page.locator("#qtNameInput").fill("Too late");
  await otherAdmin("DELETE", `${API}/${shared}?save_number=5`);
  expectedFailures = [404];
  await page.locator("#qtSaveButton").click();
  await page.locator("#qtSaveError").getByText("Someone deleted this template since you opened it, so it cannot be saved. Go back to All templates.").waitFor();
  await discard(page);
  await listReady(page);
  assert.equal(await page.locator("#qtCounter").innerText(), "1 of 10 saved");
  const short = (await otherAdmin("POST", API, { definition: await page.evaluate(() => ({ ...quoteTemplatesSettings.template("standard").definition, name: "Short lived" })) })).template;
  await page.locator("#settingsPricingTab").click();
  await openTemplates(page);
  await page.getByRole("button", { name: "More actions for Short lived" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await otherAdmin("DELETE", `${API}/${short.id}?save_number=1`);
  await page.locator("#qtDeleteConfirm").click();
  await page.locator("#qtDeleteError").getByText("This template was already deleted.").waitFor();
  await listReady(page);
  assert.ok(await page.locator("#qtDeleteConfirm").isDisabled());
  await page.locator("#qtDeleteCancel").click();
  assert.equal(await focusId(page), "qtNewButton");
  expectedFailures = [];

  step("a failed preview and a failed save show the support code, and the code stays");
  await startChangedCopy(page, "Fails");
  const failure = { status: 500, contentType: "application/json", body: JSON.stringify({ status: "failed", errors: ["Failed."], error_reference: "ERR-1234ABCD" }) };
  expectedFailures = [500];
  await page.route(`**${API}/preview`, (route) => route.fulfill(failure));
  await page.locator("#qtPreviewPdf").click();
  await page.locator("#qtFooterNote").getByText("The preview could not be made. Try again, or contact support with code ERR-1234ABCD.").waitFor();
  await shot(page, "preview-failed", ".pricing-reference-settings-panel");
  await page.waitForTimeout(4500); // longer than a passing note
  assert.equal(supportCode(await page.locator("#qtFooterNote").innerText()), "ERR-1234ABCD");
  await page.unroute(`**${API}/preview`);
  await page.route(`**${API}`, (route) => (route.request().method() === "POST" ? route.fulfill(failure) : route.fallback()));
  await page.locator("#qtSaveButton").click();
  await page.locator("#qtSaveError").getByText("Your changes are still here; try Save again. If it keeps happening, contact support with code ERR-1234ABCD.").waitFor();
  await page.unroute(`**${API}`);
  await discard(page);
  expectedFailures = [];

  step("Company Profile picks the template");
  const newProfile = async (label) => {
    const created = await context.request.post(base + "/api/settings/profiles", {
      headers: { ...csrf, Origin: base }, data: { operation: "create", id: "synthetic-profile", label, defaults: {} },
    });
    assert.equal(created.status(), 200);
    return (await created.json()).profile; // database storage names new profiles itself
  };
  const profileId = (await newProfile("Synthetic Template Profile")).id;
  await page.locator("#pricingReferenceCloseButton").click();
  await page.evaluate(async () => { await loadProfiles(); renderPresetOptions(); });
  await page.locator("#newQuoteButton").click();
  // The step bar keeps Quote Company locked until images are uploaded; the profile card does not need them.
  await page.evaluate(() => setSidePanel("quote_company", { force: true }));
  await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
  await page.locator("#presetSelect").selectOption(`company:${profileId}`);
  await page.locator("#quoteTemplateField").scrollIntoViewIfNeeded();
  assert.equal(await page.locator("#quoteTemplateNote").innerText(), "Workspace default: Standard copy");
  const templateId = await page.locator("#quoteTemplateSelect option", { hasText: "Standard copy" }).getAttribute("value");
  const picked = page.waitForResponse((response) => response.url().endsWith("/api/settings/profiles") && response.request().method() === "POST");
  await page.locator("#quoteTemplateSelect").selectOption(templateId);
  assert.equal((await picked).status(), 200);
  await page.locator("#presetStatus").getByText("will use Standard copy for new quotes").waitFor();
  await shot(page, "profile-dropdown", "#quoteCompanyPanel");

  step("Company Profile by keyboard: arrow keys only look, Enter or leaving the field saves, the focus stays");
  const field = page.locator("#quoteTemplateSelect");
  const posts = [];
  page.on("request", (request) => { if (request.method() === "POST" && request.url().endsWith("/api/settings/profiles")) posts.push(request.postDataJSON().quote_template_id); });
  const settled = () => page.waitForFunction(() => !quoteTemplatesSettings.profileSaveBusy);
  await field.focus();
  await page.keyboard.press("ArrowUp"); // Standard (built in)
  await page.keyboard.press("ArrowUp"); // Workspace default
  await page.waitForTimeout(400);
  assert.deepEqual([await field.inputValue(), posts, await focusId(page)], ["default", [], "quoteTemplateSelect"]);
  await page.evaluate(() => updatePresetButtons()); // a redraw keeps the option being looked at
  assert.equal(await field.inputValue(), "default");
  await page.keyboard.press("Escape");
  assert.deepEqual([await field.inputValue(), posts], [templateId, []], "Escape goes back to the saved template");
  await page.keyboard.press("ArrowUp");
  await page.route("**/api/settings/profiles", async (route) => { await new Promise((resolve) => setTimeout(resolve, 600)); await route.fallback(); });
  await page.keyboard.press("Enter");
  assert.deepEqual([await field.inputValue(), await focusId(page)], ["standard", "quoteTemplateSelect"], "the focus stays while it saves");
  await page.keyboard.press("Enter"); // never sends the same choice twice
  await page.keyboard.press("ArrowUp"); // not taken until the save is back
  assert.deepEqual([await field.inputValue(), posts], ["standard", ["standard"]]);
  await page.locator("#presetStatus").getByText("will use Standard for new quotes").waitFor();
  await settled();
  await page.unroute("**/api/settings/profiles");
  assert.deepEqual([posts, await field.inputValue(), await focusId(page)], [["standard"], "standard", "quoteTemplateSelect"]);
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Tab");
  await page.locator("#presetStatus").getByText("will use the workspace default for new quotes").waitFor();
  await settled();
  assert.deepEqual([posts, await field.inputValue()], [["standard", "default"], "default"]);
  assert.notEqual(await focusId(page), "quoteTemplateSelect");
  await field.focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("ArrowUp"); // back on the saved one: nothing to save
  await page.keyboard.press("Tab");
  await page.waitForTimeout(300);
  assert.deepEqual(posts, ["standard", "default"]);
  // A mouse press ends the looking even while a key is still held down, so the pick that follows saves at once.
  await field.focus();
  await page.keyboard.down("ArrowDown");
  assert.deepEqual([await field.inputValue(), posts], ["standard", ["standard", "default"]]);
  await field.evaluate((node) => node.addEventListener("mousedown", (event) => event.preventDefault(), { once: true })); // keeps the browser's own list closed
  const place = await field.boundingBox();
  await page.mouse.click(place.x + place.width / 2, place.y + place.height / 2);
  await field.selectOption(templateId);
  await page.locator("#presetStatus").getByText("will use Standard copy for new quotes").waitFor();
  await settled();
  await page.keyboard.up("ArrowDown");
  assert.deepEqual(posts, ["standard", "default", templateId]);
  await field.selectOption("default");
  await page.locator("#presetStatus").getByText("will use the workspace default for new quotes").waitFor();
  await settled();
  posts.length = 2;
  assert.equal(await field.locator("option[value=own]").count(), 0, "a profile without a layout of its own is not offered one");

  step("Company Profile saved before templates existed: one key never changes it, and it can be switched back");
  const own = await newProfile("Synthetic Own Layout");
  sql("update sqag_profiles set payload_json = json_remove(payload_json, '$.quote_template_id') where profile_id = ?", own.id);
  await page.evaluate(async () => { await loadProfiles(); renderPresetOptions(); });
  await page.locator("#presetSelect").selectOption(`company:${own.id}`);
  assert.equal(await field.locator("option:checked").innerText(), "Profile's own layout");
  assert.ok(await field.isEnabled());
  await field.focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  assert.equal(await field.locator("option:checked").innerText(), "Profile's own layout");
  assert.deepEqual(posts, ["standard", "default"], "one arrow key saves nothing");
  posts.length = 0;
  await page.keyboard.press("ArrowDown"); // Workspace default
  await page.keyboard.press("ArrowDown"); // Standard (built in)
  await page.keyboard.press("Enter");
  await page.locator("#presetStatus").getByText('"Synthetic Own Layout" will use Standard for new quotes.').waitFor();
  await settled();
  assert.equal(await field.inputValue(), "standard");
  assert.equal(await field.locator("option[value=own]").innerText(), "Profile's own layout");
  await shot(page, "profile-own-layout", "#quoteCompanyPanel");
  await page.evaluate(async () => { await loadProfiles(); renderPresetOptions(); }); // also after the page reads it again
  await page.locator("#presetSelect").selectOption(`company:${own.id}`);
  assert.equal(await field.locator("option[value=own]").innerText(), "Profile's own layout");
  await field.focus();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  assert.deepEqual([await field.inputValue(), posts], ["own", ["standard"]]);
  await page.keyboard.press("Enter");
  await page.locator("#presetStatus").getByText('"Synthetic Own Layout" will use its own layout for new quotes.').waitFor();
  await settled();
  assert.deepEqual([await field.inputValue(), posts, await focusId(page)], ["own", ["standard", "own"], "quoteTemplateSelect"]);
  const restored = (await (await context.request.get(base + "/api/profiles")).json()).company_profiles.find((item) => item.id === own.id);
  assert.deepEqual([restored.own_layout, "quote_template_id" in restored], [true, false]);
  own.revision = restored.revision;
  await otherAdmin("POST", "/api/settings/profiles", {
    operation: "access", id: own.id, revision: own.revision, access: { version: 1, owner_user_id: null, visibility: "shared" },
  });
  const conflict = page.waitForResponse((response) => response.url().endsWith("/api/settings/profiles") && response.request().method() === "POST");
  await field.selectOption("standard");
  assert.equal((await conflict).status(), 409);
  await page.locator("#presetStatus").getByText("This profile changed since the page loaded. Reload the page, then pick the template again.").waitFor();
  assert.equal(await field.locator("option:checked").innerText(), "Profile's own layout");
  problems.splice(problems.indexOf("409 /api/settings/profiles"), 1);

  step("at the limit New template is off and the limit message shows; delete one");
  const standard = await (await context.request.get(base + "/api/settings/quote-templates")).json();
  const definition = standard.templates.find((template) => template.builtin).definition;
  for (let n = 2; n <= 10; n += 1) {
    const saved = await context.request.post(base + "/api/settings/quote-templates", {
      headers: { ...csrf, Origin: base }, data: { definition: { ...definition, name: `Synthetic ${n}` } },
    });
    assert.equal(saved.status(), 200);
  }
  await page.locator("#settingsButton").click();
  await page.locator("#settingsTemplatesTab").click();
  await page.locator("#qtCounter").getByText("10 of 10 saved").waitFor();
  assert.ok(await page.locator("#qtLimitMessage").isVisible());
  assert.ok(await page.locator("#qtNewButton").isDisabled());
  assert.ok(await page.getByRole("button", { name: "Make a copy", exact: true }).isDisabled());
  await shot(page, "list-limit", ".pricing-reference-settings-panel");
  await page.getByRole("button", { name: "More actions for Synthetic 10" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  await page.locator("#qtDeleteModal").waitFor({ state: "visible" });
  assert.equal(await page.locator("#qtDeleteTitle").innerText(), 'Delete "Synthetic 10"?');
  await shot(page, "delete-confirm");
  await page.locator("#qtDeleteConfirm").click();
  await page.locator("#qtListStatus").getByText("Deleted.").waitFor();
  assert.equal(await page.locator("#qtCounter").innerText(), "9 of 10 saved");
  assert.ok(await page.locator("#qtLimitMessage").isHidden());
  assert.ok(await page.locator("#qtNewButton").isEnabled());
  // Deleting the default: profiles that follow it move on without a note, so the dialog does not promise one.
  await page.getByRole("button", { name: "More actions for Standard copy" }).click();
  await page.getByRole("menuitem", { name: "Delete" }).click();
  assert.equal(await page.locator("#qtDeleteDetail").innerText(),
    "1 company profile uses it. It will switch to the workspace default (Standard). It is the workspace default, so Standard becomes the default. Quotes already made keep their layout.");
  await page.locator("#qtDeleteCancel").click();

  step("the last free place is taken meanwhile: the draft stays, and Save works once a place is free");
  const answers = []; // the server's answer to each new template the page sends
  page.on("response", (response) => { if (response.request().method() === "POST" && response.url() === base + API) answers.push(response.status()); });
  const pressSave = async () => {
    const before = answers.length;
    await page.locator("#qtSaveButton").click();
    for (let n = 0; n < 40 && (answers.length === before || await page.evaluate(() => quoteTemplatesSettings.saveBusy)); n += 1) await page.waitForTimeout(50);
    return answers.slice(before);
  };
  await startChangedCopy(page, "No room");
  const took = (await otherAdmin("POST", API, { definition: { ...definition, name: "Took the place" } })).template;
  expectedFailures = [409];
  for (let n = 0; n < 2; n += 1) { // the second time the page's list says there is no room, but the server is still asked
    assert.deepEqual(await pressSave(), [409], "the server says whether there is room");
    await page.locator("#qtSaveError").getByText("You have 10 saved templates, the most a workspace can keep.").waitFor();
    await listReady(page);
    assert.equal(await page.locator("#qtNameInput").inputValue(), "No room");
    assert.deepEqual([await focusId(page), (await editorState(page)).editing], ["qtSaveButton", true]);
  }
  await otherAdmin("DELETE", `${API}/${took.id}?save_number=1`);
  assert.deepEqual(await pressSave(), [200], "with a place free the same draft is saved");
  await page.locator("#qtListStatus").getByText('"No room" is ready to pick in Company Profile.').waitFor();
  assert.equal(await page.locator("#qtCounter").innerText(), "10 of 10 saved");

  step("the last free place is taken under the draft's own name: Save works once that template is deleted");
  const kept = await page.evaluate(() => quoteTemplatesSettings.savedTemplates().find((template) => template.name === "No room").id);
  await otherAdmin("DELETE", `${API}/${kept}?save_number=1`);
  await page.locator("#settingsPricingTab").click();
  await openTemplates(page);
  await listReady(page);
  await startChangedCopy(page, "Same name");
  const same = (await otherAdmin("POST", API, { definition: { ...definition, name: "Same name" } })).template;
  assert.deepEqual(await pressSave(), [409]);
  await listReady(page);
  assert.deepEqual(await pressSave(), [], "a name that is taken is never sent");
  assert.equal(await page.locator("#qtNameError").innerText(), "Another template already has this name.");
  assert.deepEqual([await focusId(page), (await editorState(page)).editing], ["qtNameInput", true]);
  await otherAdmin("DELETE", `${API}/${same.id}?save_number=1`);
  assert.deepEqual(await pressSave(), [200], "the name is free again, so the same draft is saved");
  await page.locator("#qtListStatus").getByText('"Same name" is ready to pick in Company Profile.').waitFor();
  expectedFailures = [];

  step("a workspace that may keep no templates: New template is off and the page says so");
  await page.route(`**${API}`, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    await route.fulfill({ response, json: { ...body, limit: 0, saved_count: 0, templates: body.templates.filter((item) => item.builtin), default_template_id: "standard" } });
  });
  await page.locator("#settingsPricingTab").click();
  await openTemplates(page);
  assert.equal(await page.locator("#qtCounter").innerText(), "0 of 0 saved");
  assert.ok(await page.locator("#qtNewButton").isDisabled());
  assert.ok(await page.getByRole("button", { name: "Make a copy", exact: true }).isDisabled());
  assert.ok(await page.locator("#qtLimitMessage").getByText("This workspace cannot keep saved templates.").isVisible());
  assert.ok(await page.locator("#qtEmpty").isHidden());
  await page.unroute(`**${API}`);
  await page.locator("#pricingReferenceCloseButton").click();

  step("operator: Settings is greyed out and template changes are refused");
  const operatorContext = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await operatorContext.route("**/*", (route) => (new URL(route.request().url()).origin === base ? route.fallback() : route.abort("blockedbyclient")));
  const operator = await operatorContext.newPage();
  await operator.route("**/api/log", (route) => route.fulfill({ status: 204, body: "" }));
  await signIn(operator, base, ...OPERATOR);
  await operator.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
  assert.ok(await operator.locator("#settingsButton").isDisabled());
  const operatorCsrf = await operator.evaluate(() => ({ [state.csrfHeaderName]: state.csrfToken }));
  const listing = await (await operatorContext.request.get(base + "/api/settings/quote-templates")).json();
  assert.ok(listing.templates.every((template) => !("definition" in template)));
  const refused = await operatorContext.request.post(base + "/api/settings/quote-templates/preview", {
    headers: { ...operatorCsrf, Origin: base }, data: { definition, format: "xlsx" },
  });
  assert.equal(refused.status(), 403);

  step("operator: a shared profile's dropdown is read-only");
  await operator.evaluate(async () => { await loadProfiles(); renderPresetOptions(); });
  await operator.locator("#newQuoteButton").click();
  await operator.evaluate(() => setSidePanel("quote_company", { force: true }));
  await operator.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
  await operator.locator("#presetSelect").selectOption(`company:${own.id}`);
  assert.ok(await operator.locator("#quoteTemplateSelect").isDisabled());
  assert.equal(await operator.locator("#quoteTemplateNote").innerText(), "Read-only profile");

  step("a database without migration 013: the page loads, the dropdown is locked and Settings shows a logged support code");
  sql("drop table sqag_quote_templates");
  await page.reload();
  await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
  await page.evaluate(async () => { await loadProfiles(); renderPresetOptions(); });
  await page.evaluate(() => setSidePanel("quote_company", { force: true })); // a reload reopens the quote
  await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
  await page.locator("#presetSelect").selectOption(`company:${profileId}`);
  assert.ok(await page.locator("#quoteTemplateSelect").isDisabled());
  assert.equal(await page.locator("#quoteTemplateNote").innerText(), "Quote templates are not available on this server.");
  expectedFailures = [503];
  await page.locator("#settingsButton").click();
  await page.locator("#settingsTemplatesTab").click();
  await page.locator("#qtListError").waitFor({ state: "visible" });
  const listError = await page.locator("#qtListErrorText").innerText();
  assert.match(listError, /^Saved templates are not available on this server yet\. If it keeps happening, contact support with code ERR-[0-9A-F]{8}\.$/);
  assert.ok(serverLogHolds(supportCode(listError)), "the support code on the page is in the server log");
  await shot(page, "list-error", ".pricing-reference-settings-panel");
  assert.deepEqual(problems, [], "admin page problems");

  console.log(`PASS quote templates (screenshots in ${SHOTS})`);
} finally {
  await browser?.close();
  const ended = new Promise((resolve) => harness.once("exit", resolve));
  if (harness.exitCode === null) { harness.kill(); await ended; }
  // Windows ends the process without running its clean-up; everywhere else the database file must be gone.
  const left = process.platform === "win32" ? [] : databaseFiles();
  rmSync(RUN, { recursive: true, force: true, maxRetries: 5 });
  assert.deepEqual(left, [], "the stopped harness removed its database file");
}
