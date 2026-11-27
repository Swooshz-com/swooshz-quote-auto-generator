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
      created_by: "alpha-operator" + "@" + "example.test", dashboard_summary_only: true,
      // A row as an older server sent it: the card must still show no AI spend (#443).
      ai_spend: { total: { status: "reported", display: "USD 0.25" } } },
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
  // #443 LF-02: keyboard focus on the top bar links shows the standard 2 px teal outline.
  await page.keyboard.press("Shift");
  for (const link of [page.locator("#topbarLogoutLink"), page.locator(".topbar-privacy-link", { hasText: "Terms" }),
    page.locator(".topbar-privacy-link", { hasText: "Privacy Notice" })]) {
    await link.focus();
    const outline = await link.evaluate(element => {
      const style = getComputedStyle(element);
      return [element.matches(":focus-visible"), style.outlineStyle, style.outlineWidth, style.outlineColor, style.outlineOffset];
    });
    assert.deepEqual(outline, [true, "solid", "2px", "rgb(15, 118, 110)", "2px"], await link.textContent());
  }
  await page.locator("body").evaluate(() => document.activeElement?.blur());
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
  const pdfLimit = 48 * 1024 * 1024;
  await fileInput.setInputFiles({ name: "synthetic-large.pdf", mimeType: "application/pdf",
    buffer: Buffer.alloc(pdfLimit + 1) });
  await page.getByText("Choose files up to 48 MiB each.", { exact: true }).waitFor({ state: "attached" });
  assert.equal(await page.evaluate(() => state.images.length), 0);
  await fileInput.setInputFiles({ name: "synthetic-boundary.pdf", mimeType: "application/pdf",
    buffer: Buffer.alloc(pdfLimit) });
  await page.waitForFunction(size => state.images.length === 1 && state.images[0].size === size, pdfLimit);
  await page.evaluate(() => removeImageAt(0));
  // Images share the 48 MiB per-file limit (#384).
  const imageLimit = 48 * 1024 * 1024;
  await page.evaluate(() => setImageUploadStatus(""));
  await fileInput.setInputFiles({ name: "synthetic-large.jpg", mimeType: "image/jpeg",
    buffer: Buffer.alloc(imageLimit + 1) });
  await page.getByText("Choose files up to 48 MiB each.", { exact: true }).waitFor({ state: "attached" });
  assert.equal(await page.evaluate(() => state.images.length), 0);
  await fileInput.setInputFiles({ name: "synthetic-boundary.jpg", mimeType: "image/jpeg",
    buffer: Buffer.alloc(imageLimit) });
  await page.waitForFunction(size => state.images.length === 1 && state.images[0].size === size, imageLimit);
  await page.evaluate(() => removeImageAt(0));
  // #443: a GIF is refused at Upload, by the picker and by drag and drop, with what to add instead.
  const typeMessage = "Add JPG, PNG, WebP or PDF files only.";
  const gif = { name: "synthetic.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a synthetic") };
  await page.evaluate(() => setImageUploadStatus(""));
  await fileInput.setInputFiles(gif);
  await page.locator("#imageUploadStatus").getByText(typeMessage, { exact: true }).waitFor({ state: "attached" });
  assert.equal(await page.evaluate(() => state.images.length), 0);
  await page.evaluate(() => setImageUploadStatus(""));
  await page.evaluate(async () => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["GIF89a synthetic"], "dropped.gif", { type: "image/gif" }));
    elements.dropzone.dispatchEvent(new DragEvent("drop", { dataTransfer: transfer, bubbles: true, cancelable: true }));
  });
  await page.locator("#imageUploadStatus").getByText(typeMessage, { exact: true }).waitFor({ state: "attached" });
  assert.equal(await page.evaluate(() => state.images.length), 0);
  await fileInput.setInputFiles([gif, { name: "synthetic.webp", mimeType: "image/webp", buffer: Buffer.from("RIFF synthetic webp") }]);
  await page.waitForFunction(() => state.images.length === 1 && state.images[0].name === "synthetic.webp");
  assert.ok((await page.locator("#imageUploadStatus").textContent()).endsWith(typeMessage), "a skipped GIF is not explained");
  await page.evaluate(() => removeImageAt(0));
  // #443: a GIF or a logo over 2 MB is refused at once with a plain message; the logo stays as it was.
  const logoInput = page.locator("#headerLogoInput");
  const logoMessage = "Use a PNG or JPEG logo of 2 MB or less.";
  for (const logo of [
    { name: "synthetic-logo.gif", mimeType: "image/gif", buffer: Buffer.from("GIF89a synthetic") },
    { name: "synthetic-logo.png", mimeType: "image/png", buffer: Buffer.alloc(2 * 1024 * 1024 + 1) },
  ]) {
    await page.evaluate(() => renderPresetStatus(""));
    await logoInput.setInputFiles(logo);
    await page.locator("#presetStatus").getByText(logoMessage, { exact: true }).waitFor({ state: "attached" });
    assert.equal(await page.evaluate(() => state.headerLogo), null, "a refused logo was taken");
  }
  await logoInput.setInputFiles({ name: "synthetic-logo.png", mimeType: "image/png", buffer: Buffer.alloc(2 * 1024 * 1024) });
  await page.waitForFunction(() => state.headerLogo?.name === "synthetic-logo.png");
  await page.evaluate(() => { state.headerLogo = null; renderHeaderLogoPreview(); renderPresetStatus(""); });
  const cards = page.locator(".dashboard-session-card");
  await cards.nth(1).waitFor();
  assert.equal(await cards.count(), 2);
  const mappedCardText = await cards.filter({ hasText: "Mapped fixture" }).innerText();
  assert.ok(!mappedCardText.includes("AI spend") && !mappedCardText.includes("USD 0.25"), "The Dashboard card shows AI spend");
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
  // #443: staff see why Settings is off.
  assert.equal(await page.locator("#settingsButton").getAttribute("title"), "Only admins can open Settings.");
  assert.equal(await page.locator("#settingsButton").isDisabled(), true);
  assert.equal(selected.authenticated,true); assert.notEqual(selected.browser_recovery_scope,session.browser_recovery_scope);
  assert.equal((await copied.get(base+"/api/session")).status(),401);
  assert.deepEqual(external,[]);
  await copied.dispose(); await context.close();
  // #251: staff stay signed in until 3 days without use; an ended session sends
  // every staff action to sign-in, and a new sign-in opens the Dashboard.
  const HOUR = 60 * 60;
  const ENDED_NOTE = "Your session has ended. Sign in again to continue.";
  let idleClients = 0;
  async function idleContext() {
    // Each context is its own trusted-proxy client, so the sign-in form rate
    // limit (10 a minute per client) applies per context as in real use.
    idleClients += 1;
    const idle = await browser.newContext({ acceptDownloads: true,
      extraHTTPHeaders: { "X-Forwarded-For": "192.0.2." + (10 + idleClients) } });
    const blocked = [];
    await idle.route("**/*", route => {
      if (new URL(route.request().url()).origin === base) return route.fallback();
      blocked.push("external request blocked"); return route.abort("blockedbyclient");
    });
    const idlePage = await idle.newPage();
    idlePage.setDefaultTimeout(10000);
    await idlePage.route("**/api/log", route => route.fulfill({ status: 204, body: "" }));
    return { idle, idlePage, blocked };
  }
  async function advanceClock(seconds, options = {}) {
    const response = await request.newContext().then(async api => {
      try { return await api.post(base + "/__harness/clock", { data: { advance_seconds: seconds, ...options } }); }
      finally { await api.dispose(); }
    });
    assert.equal(response.status(), 200);
  }
  async function booted(target) {
    await target.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && !state.quoteSessionDashboardBusy);
  }
  async function onDashboard(target) {
    await booted(target);
    assert.equal(await target.evaluate(() => state.activeAppView), "dashboard");
    assert.equal(await target.locator("#quoteDashboardPanel").evaluate(node => node.classList.contains("is-active")), true);
  }
  async function endedSignInPage(target) {
    await target.waitForURL(base + "/login");
    await target.getByRole("heading", { name: "Staff sign in", exact: true }).waitFor();
    assert.equal(await target.locator("#authPageMessage").innerText(), ENDED_NOTE);
  }
  const idleQuotes = [{
    session_id: "quote-idle-fixture", customer_summary: { customer_name: "Idle fixture" },
    created_by: "alpha-admin" + "@" + "example.test", dashboard_summary_only: true,
    exports: { xlsx: { exists: true, url: "/api/quote-sessions/quote-idle-fixture/exports/quotation.xlsx" } },
  }];
  {
    const { idle, idlePage, blocked } = await idleContext();
    await idlePage.route("**/api/quote-sessions", route => route.request().method() === "GET"
      ? route.fulfill({ json: { quote_sessions: idleQuotes } }) : route.fallback());
    await signIn(idlePage, base);
    await onDashboard(idlePage);
    const loginCookie = (await idle.cookies()).find(cookie => cookie.name === "swooshz_quote_session");
    assert.ok(loginCookie && loginCookie.httpOnly && loginCookie.sameSite === "Lax" && loginCookie.path === "/");
    assert.ok(loginCookie.expires - Date.now() / 1000 > 400 * 24 * HOUR - 120, "Cookie lasts 400 days, so the ended note can show after any absence");
    // E2: used every 48 h, the session never ends.
    for (const step of [1, 2]) {
      await advanceClock(48 * HOUR);
      const reloaded = await idlePage.reload();
      assert.equal(reloaded.status(), 200, "Signed out after " + (48 * step) + " h of regular use");
      await onDashboard(idlePage);
    }
    // E4: a refresh in the same session keeps the open quote screen.
    await idlePage.evaluate(() => {
      clearQuoteSessionDraftSaveTimer();
      state.quoteSessionId = "quote-idle-refresh";
      setInputValue(elements.clientName, "Synthetic refresh customer");
      showQuoteFlow();
      saveSessionState();
    });
    await idlePage.reload();
    await booted(idlePage);
    assert.equal(await idlePage.evaluate(() => state.activeAppView), "quote");
    assert.equal(await idlePage.locator("#clientName").inputValue(), "Synthetic refresh customer");
    await idlePage.evaluate(() => { clearSessionState(); resetCurrentQuoteDraftState(); showDashboard({ load: false }); saveSessionState(); });
    await idlePage.reload();
    await onDashboard(idlePage);
    // E5: an expired session never starts a dashboard download.
    await idlePage.locator(".dashboard-session-card").filter({ hasText: "Idle fixture" }).click();
    const exportLink = idlePage.locator("#dashboardSidePanel a.dashboard-export-link").first();
    await exportLink.waitFor();
    await advanceClock(72 * HOUR + 1);
    let downloads = 0;
    idlePage.on("download", () => { downloads++; });
    await exportLink.click({ noWaitAfter: true });
    await endedSignInPage(idlePage);
    assert.equal(downloads, 0);
    // E8: after signing in again the Dashboard opens.
    await signIn(idlePage, base);
    await onDashboard(idlePage);
    // E1: Delete after 3 days idle goes to sign-in; nothing is deleted.
    await advanceClock(72 * HOUR + 1);
    const deletes = [];
    idlePage.on("response", response => { if (response.request().method() === "DELETE") deletes.push(response.status()); });
    await idlePage.evaluate(() => requestQuoteSessionDelete("quote-idle-fixture"));
    await idlePage.locator("#confirmQuoteSessionDeleteButton").click({ noWaitAfter: true });
    await endedSignInPage(idlePage);
    assert.deepEqual(deletes, [401]);
    await signIn(idlePage, base);
    await onDashboard(idlePage);
    await idlePage.locator(".dashboard-session-card").filter({ hasText: "Idle fixture" }).waitFor();
    // E7: Sign out after the session already ended finishes signing out.
    await advanceClock(72 * HOUR + 1);
    const signOut = idlePage.waitForResponse(r => r.url() === base + "/logout" && r.request().method() === "POST");
    await idlePage.locator("#topbarLogoutLink").click();
    assert.equal((await signOut).status(), 401);
    await idlePage.waitForURL(base + "/signed-out");
    assert.deepEqual(blocked, []);
    await idle.close();
  }
  {
    // E3: unsaved work in an open quote survives the ended session and comes
    // back when that quote is opened from the Dashboard after signing in.
    // The harness server stores the quote for real (synthetic file storage).
    const { idle, idlePage, blocked } = await idleContext();
    await signIn(idlePage, base);
    await onDashboard(idlePage);
    const quoteId = await idlePage.evaluate(() => {
      clearQuoteSessionDraftSaveTimer();
      state.pricingDraftVersion = "create";
      state.quoteSessionDraftSaveStarted = true;
      state.images = [{ name: "synthetic-booth.png", type: "image/png", data_url: "data:image/png;base64,AQID" }];
      setInputValue(elements.clientName, "Synthetic unsaved customer");
      showQuoteFlow();
      const id = ensureClientQuoteSessionId();
      saveSessionState();
      return id;
    });
    await advanceClock(72 * HOUR + 1);
    const rejectedSave = idlePage.waitForResponse(r => r.url() === base + "/api/quote-sessions" && r.request().method() === "POST");
    await idlePage.locator("#topbarBrandButton").click({ noWaitAfter: true });
    assert.equal((await rejectedSave).status(), 401);
    await endedSignInPage(idlePage);
    const savedAfterSignIn = idlePage.waitForResponse(r => r.url() === base + "/api/quote-sessions" && r.request().method() === "POST");
    await signIn(idlePage, base);
    assert.equal((await savedAfterSignIn).status(), 200, "The recovered draft is saved before the Dashboard opens");
    await onDashboard(idlePage);
    await idlePage.evaluate(id => { clearSessionState(); resetCurrentQuoteDraftState(); return id; }, quoteId);
    assert.equal(await idlePage.evaluate(id => modifyDashboardQuote(id), quoteId), true);
    assert.equal(await idlePage.evaluate(() => state.activeAppView), "quote");
    assert.equal(await idlePage.locator("#clientName").inputValue(), "Synthetic unsaved customer");
    assert.deepEqual(blocked, []);
    await idle.close();
  }
  {
    // E6: raw staff actions and generate go to sign-in on a 401 without
    // showing their own failure first.
    const actions = [
      ["profile delete", "**/api/settings/profiles/**", page => page.evaluate(() => {
        state.companyProfiles = [{ id: "profile-idle", name: "Idle profile", source: "company", revision: 1 }];
        state.profileDeleteConfirmId = "profile-idle";
        return deleteSelectedPreset();
      })],
      ["pricing reference delete", "**/api/settings/pricing-references/**", page => page.evaluate(() => {
        state.pricingReferences = [{ id: "reference-idle", source: "company", label: "Idle reference" }];
        return deleteRepoPricingReference("reference-idle", "company");
      })],
      ["profile export", "**/api/settings/profiles/**", page => page.evaluate(() => fetchCompanyProfileExport("profile-idle"))],
      ["feedback context", "**/api/feedback/context**", page => page.evaluate(() => loadFeedbackContext())],
      ["generate", "**/api/jobs", page => page.evaluate(() => {
        state.basisConfirmed = true;
        state.lineItems = [{ description: "Synthetic item", quantity: 1, unit: "lot", unit_price: 1 }];
        missingDetailFields = () => [];
        ensureQuoteSession = async () => "quote-idle-generate";
        return handleGenerate();
      })],
    ];
    for (const [label, pattern, act] of actions) {
      const { idle, idlePage, blocked } = await idleContext();
      await idlePage.route("**/api/quote-sessions", route => route.fulfill({ json: { quote_sessions: [] } }));
      await signIn(idlePage, base);
      await onDashboard(idlePage);
      await idlePage.route(pattern, route => route.fulfill({ status: 401, json: { status: "auth_required", login_url: "/login" } }));
      const failures = [];
      await idlePage.exposeFunction("recordVisibleFailure", text => failures.push(text));
      await idlePage.evaluate(() => new MutationObserver(() => {
        const text = elements.stepMessagePopupMessages.textContent.trim();
        if (text) window.recordVisibleFailure(text);
      }).observe(elements.stepMessagePopupMessages, { childList: true, subtree: true }));
      const outcome = act(idlePage).then(value => ({ value }), error => ({ error: String(error).slice(0, 300) }));
      await idlePage.waitForURL(base + "/login").catch(async error => {
        throw new Error(label + " did not reach sign-in: " + JSON.stringify(await Promise.race([outcome, Promise.resolve("pending")])) + " " + error.message.split(String.fromCharCode(10))[0]);
      });
      assert.deepEqual(failures, [], label + " showed a failure before sign-in");
      assert.deepEqual(blocked, []);
      await idle.close();
    }
  }
  {
    // E9: after the session really ended (server clock past 72 h), downloads,
    // the PDF tab and feedback submit go to sign-in: no file, no tab left open.
    const xlsxUrl = "/api/quote-sessions/quote-idle-fixture/exports/quotation.xlsx";
    const actions = [
      ["Excel download", null, page => page.evaluate(url => {
        state.outputRevision = 1;
        state.downloadFileRevision = 1;
        return downloadCurrentExcelFile({ url, name: "quotation.xlsx" });
      }, xlsxUrl)],
      ["PDF tab", null, page => page.evaluate(() => viewCurrentPdfFile({ url: "/api/quote-sessions/quote-idle-fixture/view/pdf" }))],
      ["pricing reference template", page => page.evaluate(() => canManagePricingReferences()),
        page => page.evaluate(() => downloadPricingReferenceTemplate({ preventDefault() {} }))],
      ["pricing reference export", page => page.evaluate(() => {
        deletionPricingReference = () => ({ id: "reference-idle", source: "company", label: "Idle reference" });
        return pricingReferenceExportBlockReason() === "";
      }), page => page.evaluate(() => exportSelectedPricingReference({ preventDefault() {} }))],
      ["feedback submit", page => page.evaluate(async () => {
        // Opened while signed in (it loads its context), submitted after the end.
        await showFeedbackModal();
        elements.feedbackShortTitle.value = "Synthetic idle feedback";
        elements.feedbackMessage.value = "Synthetic message after the session ended.";
        return !elements.feedbackModal.hidden && elements.feedbackForm.checkValidity();
      }), page => page.evaluate(() => submitFeedback({ preventDefault() {} }))],
    ];
    for (const [label, prepare, act] of actions) {
      const { idle, idlePage, blocked } = await idleContext();
      await idlePage.route("**/api/quote-sessions", route => route.request().method() === "GET"
        ? route.fulfill({ json: { quote_sessions: idleQuotes } }) : route.fallback());
      await signIn(idlePage, base);
      await onDashboard(idlePage);
      if (prepare) assert.equal(await prepare(idlePage), true, label + " was not ready to run");
      await advanceClock(72 * HOUR + 1);
      let downloads = 0;
      const tabs = [];
      idle.on("page", tab => tabs.push(tab));
      idlePage.on("download", () => { downloads++; });
      const failures = [];
      await idlePage.exposeFunction("recordVisibleFailure", text => failures.push(text));
      await idlePage.evaluate(() => new MutationObserver(() => {
        const text = elements.stepMessagePopupMessages.textContent.trim();
        if (text) window.recordVisibleFailure(text);
      }).observe(elements.stepMessagePopupMessages, { childList: true, subtree: true }));
      void act(idlePage).catch(() => {});
      await endedSignInPage(idlePage);
      assert.equal(downloads, 0, label + " downloaded a file after the session ended");
      for (const tab of tabs) if (!tab.isClosed()) await tab.waitForEvent("close");
      assert.equal(tabs.length, label === "PDF tab" ? 1 : 0, label + " opened an unexpected tab");
      assert.deepEqual(failures, [], label + " showed a failure before sign-in");
      assert.deepEqual(blocked, []);
      await idle.close();
    }
  }
  console.log(JSON.stringify({status:"passed",browser:channel||"chromium",real_provider_calls:0}));
} finally {
  if(browser) await browser.close();
  if(harness.exitCode===null) { const exited=new Promise(resolve=>harness.once("exit",resolve)); harness.kill(); await exited; }
}
