// Real app/DOM regression only. All API responses and uploaded media are synthetic.
// Run: npm run playwright:customer-summary -- --port 18820
// No provider, database, installed-service or export qualification is implied.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";
import { SyntheticBrowserBoundary } from "./synthetic-browser-boundary.mjs";

const root = new URL("../", import.meta.url);
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=", "base64");
const references = [
  { id: "synthetic-local", label: "Synthetic Euro Reference", source: "local", currency: "EUR",
    tax: { label: "VAT", rate: 0.2 }, digest_sha256: `sha256:${"a".repeat(64)}` },
  { id: "synthetic-workspace", label: "Synthetic Dollar Reference", source: "company", currency: "USD",
    tax: { label: "GST", rate: 0.05 }, digest_sha256: `sha256:${"b".repeat(64)}` },
];

function profiles(defaultIndex = 0) {
  return {
    profiles: [{ id: "synthetic-template", label: "Synthetic Template",
      default_pricing_reference: references[defaultIndex].id, quote_detail_presets: [] }],
    pricing_references: structuredClone(references),
    default_profile_id: "synthetic-template",
    default_pricing_reference_id: references[defaultIndex].id,
    company_profiles: [],
    workspace: { company: { id: "synthetic" }, workspace: { id: "synthetic" } },
  };
}

const args = process.argv.slice(2);
const portIndex = args.indexOf("--port");
const port = Number(portIndex < 0 ? 18820 : args[portIndex + 1]);
assert(Number.isInteger(port) && port >= 18820 && port <= 18829, "Use an owned port from 18820 to 18829.");
assert(args.length === 0 || (portIndex === 0 && args.length === 2), "Usage: --port 18820");
const origin = `http://127.0.0.1:${port}`;

// Serve only checked-in application files, never directory listings or runtime data.
const staticFiles = new Map([
  ["/", ["webapp/static/index.html", "text/html"]],
  ["/static/app.js", ["webapp/static/app.js", "text/javascript"]],
  ["/static/styles.css", ["webapp/static/styles.css", "text/css"]],
  ...["inter-latin-wght-normal", "inter-latin-400-normal", "inter-latin-500-normal",
    "inter-latin-600-normal", "inter-latin-700-normal", "inter-latin-800-normal"].map((name) => [
    `/static/assets/fonts/${name}.woff2`, [`webapp/static/assets/fonts/${name}.woff2`, "font/woff2"],
  ]),
]);
const unexpected = [];
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, origin).pathname;
  try {
    if (request.method === "GET" && pathname === "/static/assets/swooshz-mark.png") {
      response.writeHead(200, { "Content-Type": "image/png" }).end(png);
      return;
    }
    const file = staticFiles.get(pathname);
    if (request.method !== "GET" || !file) {
      unexpected.push(`${request.method} ${pathname}`);
      response.writeHead(404).end();
      return;
    }
    const bytes = await readFile(new URL(file[0], root));
    response.writeHead(200, { "Content-Type": file[1], "Cache-Control": "no-store" }).end(bytes);
  } catch {
    unexpected.push(`Static file unavailable: ${pathname}`);
    response.writeHead(500).end();
  }
});

async function json(route, value) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
}

class CustomerFixture {
  constructor(boundary, browser, defaultIndex = 0) {
    this.boundary = boundary;
    this.browser = browser;
    this.profileData = profiles(defaultIndex);
    this.sessions = new Map();
    this.errors = [];
    this.profileRequests = [];
    this.holdProfiles = false;
    this.waiters = [];
  }

  async open() {
    this.context = await this.boundary.newContext(this.browser, { viewport: { width: 1365, height: 900 } });
    await this.context.route(`${origin}/api/**`, (route) => this.respond(route));
    this.page = await this.context.newPage();
    this.page.setDefaultTimeout(7000);
    this.page.on("pageerror", (error) => this.errors.push(error.message));
    this.page.on("console", (message) => {
      if (message.type() === "error") this.errors.push(message.text());
    });
    await this.page.goto(origin);
    await this.page.waitForFunction(() => state.isBooting === false);
    await this.page.locator("#quoteDashboardPanel").waitFor({ state: "visible" });
    await this.page.locator("#dashboardEmptyNewQuoteButton").click();
    await this.page.locator("#imageInput").setInputFiles({ name: "synthetic-render.png", mimeType: "image/png", buffer: png });
    await this.page.locator("#fileList .file-item").waitFor({ state: "visible" });
    await this.page.locator("#sideNextButton").click();
    await this.page.locator("#customerDetailsPanel.is-active").waitFor({ state: "visible" });
    await this.page.waitForFunction(() => Boolean(state.quoteCommercialSnapshot?.pricing_basis?.digest));
  }

  async respond(route) {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const method = request.method();
    if (method === "GET" && pathname === "/api/session") {
      return json(route, { csrf_token: "synthetic-session", browser_recovery_scope: "synthetic-summary" });
    }
    if (method === "GET" && pathname === "/api/profiles") {
      if (this.holdProfiles) {
        this.profileRequests.push(route);
        for (const resolve of this.waiters.splice(0)) resolve();
        return;
      }
      return json(route, this.profileData);
    }
    if (method === "GET" && pathname === "/api/health") return json(route, { status: "ok" });
    if (method === "GET" && pathname === "/api/quote-sessions") {
      return json(route, { quote_sessions: [...this.sessions.values()] });
    }
    if (method === "POST" && pathname === "/api/quote-sessions") {
      const { expected_draft_version, ...payload } = request.postDataJSON();
      const previousVersion = this.sessions.get(payload.session_id)?.draft_version ?? "";
      assert.equal(expected_draft_version, previousVersion, "The save must use this fixture's current draft version.");
      const pricingLedger = { revision: 0, rows: {} };
      const draftVersion = createHash("sha256").update(JSON.stringify([payload, pricingLedger])).digest("hex");
      const session = { ...payload, updated_at: "2026-01-01T00:00:00Z", exports: {},
        draft_version: draftVersion, pricing_ledger: pricingLedger };
      this.sessions.set(session.session_id, session);
      return json(route, { status: "saved", quote_session: session });
    }
    const reference = references.find((item) => pathname === `/api/settings/pricing-references/${item.id}`);
    if (method === "GET" && reference) return json(route, { pricing_reference: reference });
    this.errors.push(`Unexpected API: ${method} ${pathname}`);
    await route.abort("blockedbyclient");
  }

  async waitForProfileRequests(count) {
    while (this.profileRequests.length < count) {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error("Profile request did not arrive.")), 7000);
        this.waiters.push(() => { clearTimeout(timer); resolve(); });
      });
    }
  }

  async summary(referenceIndex, { label, pending = false } = {}) {
    const reference = references[referenceIndex];
    const panel = this.page.locator("#customerDetailsPanel");
    await panel.waitFor({ state: "visible" });
    assert(await panel.isVisible(), "Customer panel must actually be visible.");
    // selectedOptions alone can pass while the source badge/chips remain stale.
    const select = panel.locator("#profileSelect");
    assert(await select.isVisible(), "Selected reference label must be visible.");
    assert.equal(await select.locator("option:checked").textContent(), label || reference.label);
    const checks = [
      [".pricing-reference-source-badge", reference.source === "local" ? "Local catalog" : "Workspace catalog"],
      ["[data-reference-basis-currency]", reference.currency],
      ["[data-reference-basis-tax]", `${reference.tax.label} ${reference.tax.rate * 100}%`],
      ["#selectedPricingReferenceSummary", pending ? "Selected. Continue to apply this pricing reference." : "Managed in Settings."],
    ];
    for (const [selector, expected] of checks) {
      const element = panel.locator(selector);
      assert(await element.isVisible(), `${selector} must be visible.`);
      assert.equal(await element.innerText(), expected, selector);
    }
    assert.equal(await this.page.locator("#pricingReferenceModal").isVisible(), false, "No Settings detour.");
  }

  async overrides() {
    await this.page.locator("#quoteCurrency").selectOption("GBP");
    await this.page.locator("#quoteTaxRate").fill("7");
    await this.page.locator("#quoteTaxRate").press("Tab");
  }

  async assertOverrides() {
    assert.equal(await this.page.locator("#quoteCurrency").inputValue(), "GBP");
    assert.equal(await this.page.locator("#quoteTaxRate").inputValue(), "7");
  }

  async close() {
    if (this.context) await this.context.close();
  }
}

const cases = [
  ...[["fresh-local", 0], ["fresh-workspace", 1]].map(([name, index]) => [name, index, async (fixture) => {
    await fixture.summary(index);
    assert.equal(await fixture.page.locator("#quoteCurrency").inputValue(), references[index].currency);
    assert.equal(await fixture.page.locator("#quoteTaxLabel").inputValue(), references[index].tax.label);
    assert.equal(await fixture.page.locator("#quoteTaxRate").inputValue(), String(references[index].tax.rate * 100));
  }]),
  ["repeated-entry", 0, async (fixture) => {
    await fixture.overrides();
    await fixture.page.locator("#sideBackButton").click();
    await fixture.page.locator("#imageIntake.is-active").waitFor({ state: "visible" });
    await fixture.page.locator("#sideNextButton").click();
    await fixture.summary(0);
    await fixture.assertOverrides();
  }],
  ...[["saved-authority", "EXISTING"], ["recovered-authority", "RECOVERED"]].map(([name, lifecycle]) => [name, 1, async (fixture) => {
    await fixture.overrides();
    await fixture.page.evaluate(async (lifecycle) => {
      clearQuoteSessionDraftSaveTimer();
      await Promise.all([quoteSessionInitialSavePromise, quoteSessionDraftSavePromise].filter(Boolean));
      // Synthetic saved/recovered fixture using the application's own serializer.
      const saved = buildSessionSnapshot();
      saved.quoteCommercialLifecycle = lifecycle;
      saved.quoteDetails.commercial_snapshot.lifecycle = lifecycle;
      saved.quoteDetails.commercial_snapshot.origin = lifecycle === "EXISTING" ? "captured" : "session_recovery";
      window.localStorage.setItem(QUOTE_SESSION_STORAGE_KEY, JSON.stringify(saved));
    }, lifecycle);
    // A different workspace default must not replace the quote-owned reference.
    fixture.profileData = profiles(0);
    await fixture.page.reload();
    await fixture.page.waitForFunction(() => state.isBooting === false);
    assert.equal(await fixture.page.evaluate(() => state.quoteCommercialLifecycle), lifecycle);
    await fixture.summary(1);
    await fixture.assertOverrides();
  }]),
  ["pending-selection", 0, async (fixture) => {
    await fixture.overrides();
    const before = await fixture.page.evaluate(() => JSON.stringify(state.quoteCommercialSnapshot));
    await fixture.page.locator("#profileSelect").selectOption("company::synthetic-workspace");
    assert.equal(await fixture.page.evaluate(() => initializeFreshPricingReferenceForCustomer()), false);
    await fixture.summary(1, { pending: true });
    await fixture.assertOverrides();
    assert.equal(await fixture.page.evaluate(() => state.pricingReferenceId), references[0].id);
    assert.equal(await fixture.page.evaluate(() => JSON.stringify(state.quoteCommercialSnapshot)), before);
  }],
  ["out-of-order-profiles", 0, async (fixture) => {
    fixture.holdProfiles = true;
    await fixture.page.evaluate(() => { window.customerSummaryRequests = [loadProfiles(), loadProfiles()]; });
    await fixture.waitForProfileRequests(2);
    const current = profiles(0);
    current.pricing_references[0].label = "Synthetic Current Reference";
    await json(fixture.profileRequests[1], current);
    assert.equal(await fixture.page.evaluate(() => window.customerSummaryRequests[1]), true);
    await fixture.summary(0, { label: "Synthetic Current Reference" });
    const stale = profiles(1);
    stale.pricing_references[0].label = "Synthetic Stale Reference";
    await json(fixture.profileRequests[0], stale);
    assert.equal(await fixture.page.evaluate(() => window.customerSummaryRequests[0]), false);
    await fixture.summary(0, { label: "Synthetic Current Reference" });
  }],
  ["stale-visible-negative-controls", 0, async (fixture) => {
    // Correct state/dropdown values must not conceal stale or hidden summary text.
    for (const selector of [".pricing-reference-source-badge", "[data-reference-basis-currency]", "[data-reference-basis-tax]"]) {
      const element = fixture.page.locator(`#customerDetailsPanel ${selector}`);
      const original = await element.textContent();
      await element.evaluate((node) => { node.textContent = "Synthetic stale value"; });
      await assert.rejects(() => fixture.summary(0), { code: "ERR_ASSERTION" });
      await element.evaluate((node, value) => { node.textContent = value; }, original);
    }
    const badge = fixture.page.locator("#customerDetailsPanel .pricing-reference-source-badge");
    await badge.evaluate((node) => { node.hidden = true; });
    await assert.rejects(() => fixture.summary(0), { code: "ERR_ASSERTION" });
    await badge.evaluate((node) => { node.hidden = false; });
    await fixture.summary(0);
  }],
];

const result = { status: "PASS", passed: [], failed: [] };
let browser;
try {
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
  const boundary = new SyntheticBrowserBoundary(origin);
  browser = await chromium.launch({ headless: true, ...(process.env.CI ? {} : { channel: "chrome" }) });
  for (const [name, defaultIndex, run] of cases) {
    const fixture = new CustomerFixture(boundary, browser, defaultIndex);
    try {
      await fixture.open();
      await run(fixture);
      assert.deepEqual(fixture.errors, [], "Browser/API diagnostics must be clean.");
      result.passed.push(name);
    } catch (error) {
      result.failed.push({ name, error: error.message });
    } finally {
      await fixture.close();
    }
  }
  boundary.assertClean();
  assert.deepEqual(unexpected, [], "Unrecognised local requests.");
  if (result.failed.length) result.status = "FAIL";
} finally {
  if (browser) await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
console.log(JSON.stringify(result));
if (result.status !== "PASS") process.exitCode = 1;
