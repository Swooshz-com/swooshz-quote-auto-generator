import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chromium, request } from "playwright";
import { signIn } from "./internal-password-browser-test.mjs";

const harness = spawn(process.env.PYTHON || "python", ["tests/internal_password_browser_harness.py"], {
  cwd: process.cwd(), env: { ...process.env, PYTHONUNBUFFERED: "1", SQAG_DISABLE_DOTENV: "1" },
  stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
});
harness.stderr.resume();
let browser;
try {
  const base = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Synthetic password harness readiness timeout.")), 15000);
    harness.stdout.once("data", chunk => { clearTimeout(timer); resolve(chunk.toString().trim().split(/\r?\n/)[0]); });
    harness.once("exit", code => { clearTimeout(timer); reject(new Error(`Synthetic password harness exited (${code}).`)); });
  });
  assert.equal(new URL(base).hostname, "127.0.0.1");
  const channel = process.env.CI ? undefined : "chrome";
  browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
  const context = await browser.newContext();
  const external = [];
  await context.route("**/*", route => {
    if (new URL(route.request().url()).origin === base) return route.fallback();
    external.push("external request blocked"); return route.abort("blockedbyclient");
  });
  const page = await context.newPage();
  // HTTP tests independently verify server-side ownership and row admission.
  const quoteSummaries = [
    { session_id: "quote-browser-mapped", customer_summary: { customer_name: "Mapped fixture" },
      created_by: "alpha-operator" + "@" + "example.test", dashboard_summary_only: true },
    { session_id: "quote-browser-unmapped", customer_summary: { customer_name: "Legacy fixture" },
      created_by: "", dashboard_summary_only: true },
  ];
  await page.route("**/api/quote-sessions", route => route.fulfill({ json: { quote_sessions: quoteSummaries } }));
  await page.route("**/api/log", route => route.fulfill({status:204,body:""}));
  assert.equal((await context.request.get(base+"/api/session")).status(),401);
  async function assertAuthLayout(title, action) {
    await page.getByRole("heading", { name: title, exact: true }).waitFor();
    await page.locator(".auth-brand").getByText("Swooshz Quote Generator").waitFor();
    assert.equal(await page.getByRole("link", { name: "Privacy Notice" }).getAttribute("href"), "/privacy");
    const panel = await page.locator(".auth-panel").boundingBox();
    const viewport = page.viewportSize();
    assert.ok(panel && panel.x >= 0 && panel.x + panel.width <= viewport.width);
    assert.ok(await action.evaluate(element => element.getBoundingClientRect().height >= 44));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), viewport.width);
  }
  for (const [width, height, alias, password] of [
    [1280, 800, "unknown" + "@" + "example.test", "synthetic-browser-admin-password"],
    [375, 667, "alpha-admin" + "@" + "example.test", "synthetic-wrong-password"],
  ]) {
    await page.setViewportSize({ width, height });
    await page.goto(base + "/login");
    await assertAuthLayout("Staff sign in", page.getByRole("button", { name: "Sign in", exact: true }));
    await page.keyboard.press("Tab");
    assert.equal(await page.locator(":focus").getAttribute("name"), "email");
    await page.getByLabel("Email",{exact:true}).fill(alias);
    await page.getByLabel("Password",{exact:true}).fill(password);
    const failed=page.waitForResponse(r=>r.url()===base+"/login"&&r.request().method()==="POST");
    await page.getByRole("button",{name:"Sign in",exact:true}).click();
    assert.equal((await failed).status(),403);
    const retry = page.getByRole("link", { name: "Try again", exact: true });
    await assertAuthLayout("Could not sign in", retry);
    assert.match(await page.locator("#authPageMessage").innerText(), /support with reference /);
    assert.equal(await retry.getAttribute("href"), "/login");
    assert.equal((await context.request.get(base+"/api/session")).status(),401);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
  await signIn(page,base);
  const session=await (await context.request.get(base+"/api/session")).json();
  assert.equal(session.authenticated,true); assert.equal(session.user,null); assert.ok(session.browser_recovery_scope);
  assert.deepEqual(session.staff_identity, { email: "alpha-admin" + "@" + "example.test", role: "admin" });
  await page.locator("#topbarAuthText").getByText("Signed in as alpha-admin" + "@" + "example.test (Admin)", { exact: true }).waitFor();
  const copied=await request.newContext({storageState:await context.storageState()});
  assert.equal((await copied.get(base+"/logout")).status(),405);
  assert.equal((await copied.post(base+"/logout",{headers:{Origin:base}})).status(),403);
  await page.waitForFunction(()=>typeof state!=="undefined"&&!state.isBooting);
  await page.evaluate(() => {
    state.pricingReferences = [
      { id: "fixture-catalogue", source: "company", label: "Workspace fixture", currency: "SGD",
        tax: { label: "GST", rate: 0.09 } },
    ];
    state.pricingReferenceId = "fixture-catalogue";
    state.pricingReferenceSource = "bundled";
    state.quoteCommercialLifecycle = "RECOVERED";
    renderProfileOptions();
    elements.profileSelect.value = "company::fixture-catalogue";
    elements.profileSelect.dispatchEvent(new Event("change", { bubbles: true }));
  });
  assert.equal(await page.locator("#selectedPricingReferenceSummary").textContent(),
    "Selected. Continue to apply this pricing reference.");
  assert.equal(await page.locator("#customerDetailsPanel [data-reference-basis-currency]").textContent(), "SGD");
  assert.equal(await page.locator("#customerDetailsPanel [data-reference-basis-tax]").textContent(), "GST 9%");
  assert.equal(await page.evaluate(() => currentPricingReference()), null);
  await page.evaluate(() => resetPendingPricingReferenceSelection());
  assert.equal(await page.locator("#profileSelect").inputValue(), "");
  assert.equal(await page.locator("#selectedPricingReferenceSummary").textContent(), "Select a pricing reference.");
  assert.equal(await page.locator("#customerDetailsPanel [data-reference-basis-currency]").textContent(), "-");
  assert.equal(await page.locator("#customerDetailsPanel [data-reference-basis-tax]").textContent(), "-");
  const feedback = page.locator("#feedbackButton");
  assert.equal(await feedback.isVisible(), false);
  // The independent session response shape has no staff_identity field.
  const { staff_identity: _staffIdentity, ...independentSession } = session;
  assert.equal(await page.evaluate(data => applySessionData(data), independentSession), true);
  assert.equal(await feedback.isVisible(), true);
  assert.equal(await page.evaluate(() => refreshSessionToken()), true);
  assert.equal(await feedback.isVisible(), false);
  const fileInput = page.locator("#imageInput");
  const pdfLimit = 24 * 1024 * 1024;
  await fileInput.setInputFiles({ name: "synthetic-large.pdf", mimeType: "application/pdf",
    buffer: Buffer.alloc(pdfLimit + 1) });
  await page.getByText("Choose images up to 12 MiB and PDFs up to 24 MiB each.", { exact: true }).waitFor({ state: "attached" });
  assert.equal(await page.evaluate(() => state.images.length), 0);
  await fileInput.setInputFiles({ name: "synthetic-boundary.pdf", mimeType: "application/pdf",
    buffer: Buffer.alloc(pdfLimit) });
  await page.waitForFunction(size => state.images.length === 1 && state.images[0].size === size, pdfLimit);
  await page.evaluate(() => removeImageAt(0));
  const cards = page.locator(".dashboard-session-card");
  await cards.nth(1).waitFor();
  assert.equal(await cards.count(), 2);
  await cards.filter({ hasText: "Mapped fixture" }).click();
  await page.getByText("Created by alpha-operator" + "@" + "example.test", { exact: true }).waitFor();
  assert.equal(await page.getByRole("button", { name: "Modify quote" }).isDisabled(), true);
  for (const creator of ["ALPHA-OPERATOR" + "@" + "EXAMPLE.TEST", "OPERATOR"]) {
    await page.locator("#dashboardSearchInput").fill(creator);
    await cards.filter({ hasText: "Mapped fixture" }).waitFor();
    assert.equal(await cards.count(), 1);
  }
  for (const creator of ["UNKNOWN", "knoWN"]) {
    await page.locator("#dashboardSearchInput").fill(creator);
    await cards.filter({ hasText: "Legacy fixture" }).waitFor();
    assert.equal(await cards.count(), 1);
  }
  assert.equal(await page.evaluate(() => dashboardSessionSearchText({}).includes("unknown")), false);
  await page.locator("#dashboardSearchInput").fill("Legacy fixture");
  await cards.filter({ hasText: "Legacy fixture" }).waitFor();
  assert.equal(await cards.count(), 1);
  await cards.first().click();
  await page.getByText("Created by Unknown", { exact: true }).waitFor();
  await page.locator("#dashboardSearchInput").fill("");
  const deleted = [];
  await page.route("**/api/quote-sessions/*", async route => {
    if (route.request().method() !== "DELETE") return route.fallback();
    const id = new URL(route.request().url()).pathname.split("/").at(-1);
    const index = quoteSummaries.findIndex(summary => summary.session_id === id);
    assert.ok(index >= 0);
    deleted.push(id);
    quoteSummaries.splice(index, 1);
    await route.fulfill({ json: { status: "deleted" } });
  });
  await page.evaluate(() => requestQuoteSessionDelete("quote-browser-mapped"));
  assert.match(await page.locator("#quoteSessionDeleteText").textContent(), /this quote session.*from the workspace/);
  await page.locator("#cancelQuoteSessionDeleteButton").click();
  assert.deepEqual(deleted, []);
  await page.evaluate(() => requestQuoteSessionDelete(
    ["quote-browser-mapped", "quote-browser-unmapped"], { bulk: true },
  ));
  assert.match(await page.locator("#quoteSessionDeleteText").textContent(), /selected quote sessions.*from the workspace/);
  await page.locator("#confirmQuoteSessionDeleteButton").click();
  await page.locator("#quoteSessionDeleteModal").waitFor({ state: "hidden" });
  assert.deepEqual(deleted, ["quote-browser-mapped", "quote-browser-unmapped"]);

  const logout=page.waitForResponse(r=>r.url()===base+"/logout"&&r.request().method()==="POST");
  await page.locator("#topbarLogoutLink").click();
  assert.equal((await logout).status(),204); await page.waitForURL(base+"/signed-out");
  assert.equal((await copied.get(base+"/api/session")).status(),401);
  for(let i=0;i<2;i++) {
    assert.equal((await page.goto(base+"/")).status(),401);
    assert.equal(await page.locator("#topbarLogoutLink").count(),0);
  }
  await signIn(page,base,"alpha-operator"+"@"+"example.test","synthetic-browser-operator-password");
  const selected=await (await context.request.get(base+"/api/session")).json();
  assert.deepEqual(selected.staff_identity, { email: "alpha-operator" + "@" + "example.test", role: "operator" });
  await page.locator("#topbarAuthText").getByText("Signed in as alpha-operator" + "@" + "example.test (Operator)", { exact: true }).waitFor();
  assert.equal(selected.authenticated,true); assert.notEqual(selected.browser_recovery_scope,session.browser_recovery_scope);
  assert.equal((await copied.get(base+"/api/session")).status(),401);
  assert.deepEqual(external,[]);
  await copied.dispose(); await context.close();
  console.log(JSON.stringify({status:"passed",browser:channel||"chromium",real_provider_calls:0}));
} finally {
  if(browser) await browser.close();
  if(harness.exitCode===null) { const exited=new Promise(resolve=>harness.once("exit",resolve)); harness.kill(); await exited; }
}
