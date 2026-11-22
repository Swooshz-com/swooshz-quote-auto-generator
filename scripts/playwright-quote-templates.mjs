// Settings > Quote Templates, end to end in a real browser (#167 S5).
// Runs the loopback password harness on a new SQLite database (synthetic users and data only), then:
// admin copies Standard, moves a block, adds and renames a table column, changes the heading colour,
// previews the PDF and Excel file, saves, makes it the workspace default, picks it in Company Profile,
// fills the workspace to the 10-template limit and deletes one; an operator sees Settings greyed out.
// QT_SCREENSHOT_DIR (default _tmp/quote-templates-shots) receives screenshots at 1440/1024/880/375 px.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "playwright";
import { signIn } from "./internal-password-browser-test.mjs";

const OPERATOR = ["alpha-operator" + "@" + "example.test", "synthetic-browser-operator-password"];
const SHOTS = process.env.QT_SCREENSHOT_DIR || path.join("_tmp", "quote-templates-shots");
const WIDTHS = [1440, 1024, 880, 375];
const TEAL = "rgb(15, 118, 110)";
mkdirSync(SHOTS, { recursive: true });

const harness = spawn(process.env.PYTHON || "python", ["tests/internal_password_browser_harness.py", "--storage", "sqlite"], {
  cwd: process.cwd(), env: { ...process.env, PYTHONUNBUFFERED: "1", SQAG_DISABLE_DOTENV: "1" },
  stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
});
harness.stderr.resume();
let browser;
const step = (text) => console.log(`- ${text}`);

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

async function focusRing(page, selector) {
  await page.keyboard.press("Shift");
  await page.locator(selector).focus();
  return page.locator(selector).evaluate((node) => {
    const style = getComputedStyle(node);
    return { style: style.outlineStyle, width: style.outlineWidth, color: style.outlineColor, offset: style.outlineOffset };
  });
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
  page.on("console", (message) => { if (message.type() === "error") problems.push(`console: ${message.text()}`); });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (response.status() >= 400 && !(url.pathname === "/api/settings/quote-templates/preview" && response.status() === 503)) {
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
  await shot(page, "editor-problem", ".pricing-reference-settings-panel");
  await page.getByRole("button", { name: "Select column C" }).click();
  await page.locator("#qtSide").getByLabel("Width", { exact: true }).fill("49.375");
  await page.locator("#qtSide").getByLabel("Width", { exact: true }).press("Tab");
  assert.equal(await page.locator("#qtStatus").innerText(), "Ready to save");

  step("preview the PDF and the Excel file");
  const pdfResponse = page.waitForResponse((response) => response.url().endsWith("/api/settings/quote-templates/preview"));
  const popup = context.waitForEvent("page", { timeout: 10000 }).catch(() => null);
  await page.locator("#qtPreviewPdf").click();
  const pdf = await pdfResponse;
  if (process.env.CI) assert.equal(pdf.status(), 200, "PDF preview needs LibreOffice in CI");
  if (pdf.status() === 200) {
    assert.equal(pdf.headers()["content-type"], "application/pdf");
    assert.ok((await pdf.body()).subarray(0, 5).toString() === "%PDF-");
  } else {
    assert.equal(pdf.status(), 503);
    await page.locator("#qtFooterNote").getByText("PDF preview is not available", { exact: false }).waitFor();
  }
  (await popup)?.close();
  const download = page.waitForEvent("download");
  await page.locator("#qtPreviewExcel").click();
  assert.equal((await download).suggestedFilename(), "quote-template-preview.xlsx");

  step("save, then make it the workspace default");
  await page.locator("#qtSaveButton").click();
  await page.locator("#qtListStatus").getByText("Saved.").waitFor();
  assert.equal(await page.locator("#qtCounter").innerText(), "1 of 10 saved");
  await page.getByRole("button", { name: "More actions for Standard copy" }).click();
  await page.getByRole("menuitem", { name: "Make workspace default" }).click();
  await page.locator("#qtListStatus").getByText("Default changed.").waitFor();
  assert.equal(await page.locator("#qtList .qt-row").nth(1).locator(".qt-badge").first().innerText(), "Default");
  await shot(page, "list-saved", ".pricing-reference-settings-panel");

  step("Company Profile picks the template");
  const csrf = await page.evaluate(() => ({ [state.csrfHeaderName]: state.csrfToken }));
  const created = await context.request.post(base + "/api/settings/profiles", {
    headers: { ...csrf, Origin: base },
    data: { operation: "create", id: "synthetic-template-profile", label: "Synthetic Template Profile", defaults: {} },
  });
  assert.equal(created.status(), 200);
  await page.locator("#pricingReferenceCloseButton").click();
  await page.evaluate(async () => { await loadProfiles(); renderPresetOptions(); });
  await page.locator("#newQuoteButton").click();
  await page.evaluate(() => setSidePanel("quote_company"));
  await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
  await page.locator("#presetSelect").selectOption("company:synthetic-template-profile");
  await page.locator("#quoteTemplateField").scrollIntoViewIfNeeded();
  assert.equal(await page.locator("#quoteTemplateNote").innerText(), "Workspace default: Standard copy");
  const templateId = await page.locator("#quoteTemplateSelect option", { hasText: "Standard copy" }).getAttribute("value");
  const picked = page.waitForResponse((response) => response.url().endsWith("/api/settings/profiles") && response.request().method() === "POST");
  await page.locator("#quoteTemplateSelect").selectOption(templateId);
  assert.equal((await picked).status(), 200);
  await page.locator("#presetStatus").getByText("will use Standard copy for new quotes").waitFor();
  await shot(page, "profile-dropdown", "#quoteCompanyPanel");

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
  assert.deepEqual(problems, [], "admin page problems");

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
  console.log(`PASS quote templates (screenshots in ${SHOTS})`);
} finally {
  await browser?.close();
  harness.kill();
}
