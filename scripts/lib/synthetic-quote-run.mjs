// Shared setup for browser checks that build a quote in the real UI against the owner-UAT mock harness
// (tests/owner_uat_mock_harness.py): a deterministic AI reply, synthetic files, loopback only, no paid AI.
import { signIn } from "../internal-password-browser-test.mjs";
import { SyntheticBrowserBoundary, freshBrowserRun } from "../synthetic-browser-boundary.mjs";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const sourceRoot = fileURLToPath(new URL("../..", import.meta.url));
const { chromium } = createRequire(path.join(sourceRoot, "package.json"))("playwright");
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const fixtureId = "synthetic-exhibition-fixture-pricing";
const profileId = "synthetic-exhibition-fixture-template";
const canonicalCatalogue = "import json,sys; sys.path.insert(0, sys.argv[1]); from webapp import server as w; p=sys.argv[2]; d=json.load(open(p, encoding='utf-8')); open(p, 'w', encoding='utf-8').write(json.dumps(w.pricing_reference_catalog_payload(d), indent=2))";

export const assert = (ok, message, detail) => {
  if (!ok) throw new Error(message + (detail === undefined ? "" : ": " + JSON.stringify(detail)));
};

export class SyntheticQuoteRun {
  constructor(name) {
    this.name = name;
    this.server = null;
    this.browser = null;
    this.serverErr = "";
    this.pageErrors = [];
  }

  // storage "sqlite" keeps quotes with their owners in a database, as live does.
  async start(scenario, { storage = "file" } = {}) {
    this.storage = storage;
    for (const key of Object.keys(process.env)) {
      if (/(API_KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)) process.env[key] = "";
    }
    this.runRoot = await freshBrowserRun(process.env.SQAG_OWNER_UAT_EVIDENCE_ROOT || path.join(sourceRoot, "_logs/browser", this.name), "run-");
    this.dirs = Object.fromEntries(["data", "output", "temp", "pricing", "profiles", "logs"].map((dir) => [dir, path.join(this.runRoot, dir)]));
    await Promise.all(Object.values(this.dirs).map((dir) => fs.mkdir(dir, { recursive: true })));
    this.scenarioPath = path.join(this.runRoot, "scenario.json");
    await fs.writeFile(this.scenarioPath, JSON.stringify(scenario, null, 2));
    const fixtureDir = path.join(this.dirs.pricing, fixtureId);
    await fs.cp(path.join(sourceRoot, "tests/fixtures/quote-generator/pricing-references", fixtureId), fixtureDir, { recursive: true });
    const canonical = spawnSync(python, ["-c", canonicalCatalogue, sourceRoot, path.join(fixtureDir, "pricing-catalog.json")],
      { cwd: sourceRoot, windowsHide: true, encoding: "utf8", env: { ...process.env, SQAG_DISABLE_DOTENV: "1" } });
    if (canonical.status !== 0) throw new Error("catalogue canonicalisation failed: " + canonical.stderr);
    await fs.cp(path.join(sourceRoot, "tests/fixtures/quote-generator/profiles", profileId), path.join(this.dirs.profiles, profileId), { recursive: true });
    await this.startServer();
    this.browser = await chromium.launch({ headless: true });
  }

  async startServer() {
    const { data, output, temp, logs, pricing, profiles } = this.dirs;
    const child = spawn(python, [path.join(sourceRoot, "tests/owner_uat_mock_harness.py"), sourceRoot, data, output, temp, logs, pricing, profiles, this.scenarioPath, "--storage", this.storage], {
      cwd: sourceRoot, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, SQAG_DISABLE_DOTENV: "1", PYTHONUNBUFFERED: "1" },
    });
    child.stderr.on("data", (chunk) => { this.serverErr = (this.serverErr + chunk).slice(-100000); });
    const baseUrl = await new Promise((resolve, reject) => {
      let buffer = "";
      const timer = setTimeout(() => reject(new Error("server not ready: " + this.serverErr.slice(-800))), 30000);
      child.stdout.on("data", (chunk) => {
        buffer += chunk;
        const match = buffer.match(/^(http:\/\/127\.0\.0\.1:\d+)$/m);
        if (match) { clearTimeout(timer); resolve(match[1]); }
      });
      child.once("exit", (code) => { clearTimeout(timer); reject(new Error("server exited " + code + ": " + this.serverErr.slice(-800))); });
    });
    this.server = { child, baseUrl, boundary: new SyntheticBrowserBoundary(baseUrl) };
  }

  // A write as the signed-in user of this page, through the app's own API.
  async api(page, method, route, data) {
    const session = await (await page.request.get(this.server.baseUrl + "/api/session")).json();
    const response = await page.request.fetch(this.server.baseUrl + route, {
      method, data, headers: { [session.csrf_header]: session.csrf_token, Origin: this.server.baseUrl },
    });
    return { status: response.status(), body: await response.json().catch(() => ({})) };
  }

  // Database storage has no bundled packs: an admin saves the pricing reference and the company profile.
  async seedDatabase() {
    const page = await this.signedInPage();
    const catalog = JSON.parse(await fs.readFile(path.join(this.dirs.pricing, fixtureId, "pricing-catalog.json"), "utf8"));
    const reference = await this.api(page, "POST", "/api/settings/pricing-references", {
      id: "synthetic-reference", label: "Synthetic Reference", tax: { label: "GST", rate: 0.09 }, currency: "SGD", items: catalog.items,
    });
    assert(reference.status === 200, "the pricing reference was not saved", reference);
    const layout = await fs.readFile(path.join(this.dirs.profiles, profileId, "quotation-layout.xlsx"));
    const profile = await this.api(page, "POST", "/api/settings/profiles", {
      operation: "create", id: "synthetic-profile", label: "Synthetic Profile", defaults: {},
      pack: { quotation_layout: { filename: "quotation-layout.xlsx", data_url: "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64," + layout.toString("base64") } },
    });
    assert(profile.status === 200, "the company profile was not saved", profile);
    const shared = await this.api(page, "POST", "/api/settings/profiles", {
      operation: "access", id: profile.body.profile.id, revision: profile.body.profile.revision, access: { version: 1, owner_user_id: null, visibility: "shared" },
    });
    assert(shared.status === 200, "the company profile was not shared", shared);
    this.companyPreset = "company:" + profile.body.profile.id;
    await page.context().close();
  }

  // The app restarts on the same data, which also starts its per-minute request limits afresh.
  async restartServer() {
    const { child, boundary } = this.server;
    boundary.assertClean();
    const exited = new Promise((resolve) => child.once("exit", resolve));
    if (child.exitCode === null) { child.kill(); await exited; }
    await this.startServer();
  }

  // A fresh browser, as on another computer.
  async signedInPage({ operator = false } = {}) {
    const context = await this.server.boundary.newContext(this.browser, { viewport: { width: 1280, height: 900 }, acceptDownloads: true });
    const page = await context.newPage();
    page.on("pageerror", (error) => this.pageErrors.push(error.message));
    await signIn(page, this.server.baseUrl, ...(operator ? ["alpha-operator" + "@" + "example.test", "synthetic-browser-operator-password"] : []));
    return page;
  }

  async png(page, width, height) {
    const base64 = await page.evaluate(({ width, height }) => {
      const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
      const context = canvas.getContext("2d"); const image = context.createImageData(width, height);
      let seed = 7; for (let i = 0; i < image.data.length; i++) { seed = (seed * 1103515245 + 12345) & 0x7fffffff; image.data[i] = i % 4 === 3 ? 255 : seed >> 23; }
      context.putImageData(image, 0, 0); return canvas.toDataURL("image/png").split(",")[1];
    }, { width, height });
    return Buffer.from(base64, "base64");
  }

  // Dashboard to Quote Basis, as staff do it.
  async buildToBasis(page, { knownDetails = "" } = {}) {
    await page.locator("#newQuoteButton").click();
    await page.locator("#imageIntake").waitFor({ state: "visible" });
    await page.locator("#imageInput").setInputFiles({ name: this.name + "-render.png", mimeType: "image/png", buffer: await this.png(page, 800, 600) });
    await page.locator("#fileList .file-item").first().waitFor({ state: "visible" });
    if (knownDetails) await page.locator("#knownDetails").fill(knownDetails);
    await page.locator("#sideNextButton").click();
    await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
    const customer = [["#clientNameEditor", "Synthetic Customer"], ["#clientAttentionEditor", "Synthetic Reviewer"], ["#clientTitleEditor", "Synthetic Lead"],
      ["#clientAddressEditor", "Synthetic address"], ["#projectTitleEditor", "Synthetic Project"], ["#showName", "Synthetic Show"],
      ["#projectNumberEditor", "SQ-" + Date.now().toString(36).toUpperCase()]];
    for (const [selector, value] of customer) await page.locator(selector).fill(value);
    await page.locator("#quoteDate").fill(new Date().toISOString().slice(0, 10));
    await page.locator("#sideNextButton").click();
    await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
    const preset = this.companyPreset
      || await page.locator("#presetSelect option").evaluateAll((options, id) => options.map((option) => option.value).find((value) => value.startsWith("profile:" + id + ":")), profileId);
    await page.locator("#presetSelect").selectOption(preset);
    await page.locator("#loadPresetButton").click();
    await page.locator("#confirmProfileLoadButton").click();
    await page.locator("#headerLogoInput").setInputFiles({ name: this.name + "-logo.png", mimeType: "image/png", buffer: await this.png(page, 240, 96) });
    const company = [["#headerDetailsEditor", "Synthetic Quote Company | Singapore"], ["#quoteCompanyNameEditor", "Synthetic Quote Company"],
      ["#companySignatoryEditor", "Synthetic Signatory"], ["#companyTitleEditor", "Synthetic Title"]];
    for (const [selector, value] of company) await page.locator(selector).fill(value);
    await page.locator("#sideNextButton").click();
    await page.locator("#analysisConfirmStartButton").click();
    await this.basisReady(page);
  }

  async basisReady(page) {
    await page.locator("#quoteBasisPanel").waitFor({ state: "visible", timeout: 90000 });
    await page.waitForFunction(() => state.quoteBasisSections.flatMap((section) => section.lines || []).length > 0 && !appIsBusy(), null, { timeout: 90000 });
  }

  // A line's own control: "Include" ticks it, "Exclude" X's it.
  async basisControl(page, lineId, selector) {
    const target = await page.evaluate((id) => {
      for (const section of state.quoteBasisSections) {
        const index = (section.lines || []).findIndex((line) => line.id === id);
        if (index >= 0) return [section.id, index];
      }
      return null;
    }, lineId);
    assert(target, "basis line missing", lineId);
    return page.locator(`#basisReviewSurface ${selector}[data-basis-section="${target[0]}"][data-basis-line-index="${target[1]}"]`).first();
  }

  async tag(page, lineId, tag) {
    await (await this.basisControl(page, lineId, `[data-basis-tag="${tag}"]`)).click();
    await page.waitForFunction(() => !appIsBusy(), null, { timeout: 30000 });
  }

  // Tick every line still waiting for review.
  async tickOpenLines(page) {
    const ticks = ':is([data-basis-tag="Include"], [data-basis-tag="Custom"])';
    for (let round = 0; round < 24; round++) {
      const target = await page.evaluate((ticks) => {
        for (const section of state.quoteBasisSections) {
          const index = (section.lines || []).findIndex((line, i) => normalizeBasisTag(line.tag) === "Confirm"
            && document.querySelector(`#basisReviewSurface .basis-line-tag-button[data-basis-section="${section.id}"][data-basis-line-index="${i}"]${ticks}`));
          if (index >= 0) return [section.id, index];
        }
        return null;
      }, ticks);
      if (!target) return;
      await page.locator(`#basisReviewSurface .basis-line-tag-button[data-basis-section="${target[0]}"][data-basis-line-index="${target[1]}"]${ticks}`).first().click();
      await page.waitForFunction(() => !appIsBusy(), null, { timeout: 30000 });
    }
  }

  async confirm(page) {
    const reason = await page.evaluate(() => basisConfirmBlockReason());
    assert(reason === "", "the basis cannot be confirmed", reason);
    await page.locator("#sideNextButton").click();
  }

  async openDashboardQuote(page, sessionId) {
    const card = page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`);
    await card.waitFor({ state: "visible", timeout: 20000 });
    await card.click();
    await page.locator('#dashboardSelectedSessionPanel [data-dashboard-panel-action="modify-session"]').click();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 30000 });
    await page.waitForFunction(() => !state.isBooting && !state.quoteSessionRestoreBusy && !appIsBusy(), null, { timeout: 30000 });
  }

  async openPanel(page, panel) {
    await page.locator(`button[data-side-panel="${panel}"]`).first().click();
    await page.waitForFunction((name) => state.activeSidePanel === name && !appIsBusy(), panel, { timeout: 30000 });
  }

  recordPath(sessionId) {
    return path.join(this.dirs.data, "quote-sessions", sessionId, "quote-session.json");
  }

  async record(sessionId) {
    return JSON.parse(await fs.readFile(this.recordPath(sessionId), "utf8"));
  }

  finish(result) {
    assert(!this.pageErrors.length, "page errors", this.pageErrors);
    this.server.boundary.assertClean();
    result.status = "PASS";
    console.log(JSON.stringify(result, null, 2));
  }

  fail(error) {
    console.error(error?.stack || String(error));
    console.error(this.serverErr.slice(-2000));
    process.exitCode = 1;
  }

  async close() {
    if (this.browser) await this.browser.close().catch(() => {});
    if (this.server && this.server.child.exitCode === null) this.server.child.kill();
  }
}
