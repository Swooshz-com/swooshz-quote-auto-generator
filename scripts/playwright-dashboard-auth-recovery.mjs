import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const { chromium } = createRequire(import.meta.url)("playwright");
const evidence = process.env.SQAG_AUTH_RECOVERY_EVIDENCE_ROOT || path.join(root, "_logs/browser/dashboard-auth-recovery");
await fs.mkdir(evidence, { recursive: true });
const baseline = process.argv.includes("--baseline");
const appOverride = process.env.SQAG_AUTH_RECOVERY_APP_SOURCE ? await fs.readFile(process.env.SQAG_AUTH_RECOVERY_APP_SOURCE, "utf8") : null;
const appSource = appOverride || await fs.readFile(path.join(root, "webapp/static/app.js"), "utf8");
const logoutStart = appSource.indexOf("async (event) => {", appSource.indexOf("function wireEvents()"));
let logoutDepth = 0, logoutEnd = logoutStart;
for (let i = appSource.indexOf("{", logoutStart); i < appSource.length; i++) {
  if (appSource[i] === "{") logoutDepth++;
  if (appSource[i] === "}" && --logoutDepth === 0) { logoutEnd = i + 1; break; }
}
const logoutCallbackSource = appSource.slice(logoutStart, logoutEnd);
const harness = spawn(process.env.PYTHON || "python", ["tests/internal_google_browser_harness.py"], {
  cwd: root, env: { ...process.env, PYTHONUNBUFFERED: "1" }, stdio: ["ignore", "pipe", "pipe"], windowsHide: true,
});
// Harness output can contain synthetic protocol parameters: retain only readiness.
harness.stderr.resume();
const base = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("Synthetic auth harness readiness timeout")), 15000);
  harness.stdout.once("data", chunk => { clearTimeout(timer); resolve(chunk.toString().trim().split(/\r?\n/)[0]); });
  harness.once("exit", () => { clearTimeout(timer); reject(new Error("Synthetic auth harness exited before readiness")); });
});
assert.equal(new URL(base).hostname, "127.0.0.1");
const browser = await chromium.launch({ headless: true });
const cases = [];
async function freshPage() {
  const context = await browser.newContext();
  await context.route("**/*", route => new URL(route.request().url()).origin === new URL(base).origin ? route.fallback() : route.abort("blockedbyclient"));
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  await page.route("**/api/log", route => route.fulfill({ status: 204, body: "" }));
  if (appOverride) await page.route("**/static/app.js*", route => route.fulfill({ status: 200, contentType: "text/javascript", body: appOverride }));
  await page.route("**/api/quote-sessions", route => route.fulfill({ status: 200, contentType: "application/json", body: '{"quote_sessions":[]}' }));
  await page.goto(base + "/login");
  await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
  await page.waitForFunction(() => !state.quoteSessionDashboardBusy);
  return { page, context };
}
async function seed(page) {
  return page.evaluate(() => {
    clearQuoteSessionDraftSaveTimer();
    state.activeAppView = "quote";
    state.quoteSessionId = "quote-auth-recovery-synthetic";
    setInputValue(elements.clientName, "Synthetic unsaved customer");
    setInputValue(elements.projectTitle, "Synthetic unsaved project");
    return currentBrowserRecoveryScope();
  });
}
async function authFailure(page, url = "**/api/quote-sessions") {
  await page.route(url, route => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ status: "auth_required", login_url: "https://untrusted.example.test/forged" }) }));
}
async function loginBoundary(page) {
  const counter = { count: 0 };
  await page.route("**/login", route => { counter.count++; return route.fulfill({ status: 200, contentType: "text/html", body: "Synthetic login boundary" }); });
  return counter;
}
async function rejectedDashboard(page) {
  await authFailure(page);
  await page.evaluate(() => { void loadQuoteDashboard(); });
}
async function blockedRecovery(page) {
  await page.waitForFunction(() => state.authenticationRecoveryRequested && authenticationRecoveryPromise === null);
  assert.notEqual(new URL(page.url()).pathname, "/login");
}
async function runAdditionalCases() {
  if (!process.argv.includes("--review-logout-red") && !process.argv.includes("--review-boot-red")) {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(() => { elements.basisChatPrompt.value = "Synthetic unsent basis request"; });
    await rejectedDashboard(page);
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    assert.equal(await page.evaluate(() => elements.basisChatPrompt.value), "Synthetic unsent basis request");
    await page.evaluate(() => { elements.basisChatPrompt.value = "  "; });
    await page.locator("#topbarBrandButton").click({ noWaitAfter: true });
    await page.waitForURL(base + "/login");
    cases.push({ id: "unsent-basis-prompt-blocks-login-until-resolved", result: "PASS" });
    await context.close();
  }
  if (!process.argv.includes("--review-boot-red")) {
    const { page, context } = await freshPage();
    await seed(page);
    await page.evaluate(() => saveSessionState());
    const login = await loginBoundary(page);
    let releaseDashboard, releaseLogout;
    await page.route("**/api/quote-sessions", route => new Promise(resolve => {
      releaseDashboard = async () => { await route.fulfill({ status: 401, contentType: "application/json", body: '{"status":"auth_required"}' }); resolve(); };
    }));
    await page.route("**/logout", route => new Promise(resolve => {
      releaseLogout = async () => { await route.fulfill({ status: 204, headers: { "X-SQAG-Logout-Location": "/signed-out" } }); resolve(); };
    }));
    await page.evaluate(callbackSource => {
      // Run the exact production callback; adapt navigation only so the old
      // document's delayed response can settle deterministically after purge.
      window.logoutRecoveryTargets = [];
      const action = new Function("window", "return (" + callbackSource + ");")({ location: { assign: target => window.logoutRecoveryTargets.push(target) } });
      void action({ preventDefault() {} });
    }, logoutCallbackSource);
    for (let attempt = 0; !releaseLogout && attempt < 50; attempt++) await page.waitForTimeout(20);
    assert.equal(typeof releaseLogout, "function");
    await page.evaluate(() => { window.lateDashboardSettled = false; void loadQuoteDashboard().finally(() => { window.lateDashboardSettled = true; }); });
    for (let attempt = 0; !releaseDashboard && attempt < 50; attempt++) await page.waitForTimeout(20);
    assert.equal(typeof releaseDashboard, "function");
    await releaseLogout();
    await page.waitForFunction(() => window.logoutRecoveryTargets.length === 1);
    assert.equal(await page.evaluate(() => localStorage.getItem("swooshz_quote_session_v1")), null);
    await releaseDashboard();
    await page.waitForFunction(() => window.lateDashboardSettled);
    const after = await page.evaluate(() => ({ quote: localStorage.getItem(QUOTE_SESSION_STORAGE_KEY), workspace: localStorage.getItem(WORKSPACE_VIEW_STORAGE_KEY), selection: localStorage.getItem(LAST_SELECTION_STORAGE_KEY) }));
    assert.equal(after.quote === null && after.workspace === null && after.selection === null, true, "Late dashboard response repopulated recovery after logout purge");
    assert.equal(login.count, 0);
    cases.push({ id: "production-logout-purge-before-delayed401-no-repopulation", result: "PASS", navigationAdapterOnly: true });
    await context.close();
  }
  for (const fault of ["403", "500", "network"]) {
    const { page, context } = await freshPage();
    await seed(page);
    const before = await page.evaluate(() => { saveSessionState(); return localStorage.getItem(QUOTE_SESSION_STORAGE_KEY); });
    await page.route("**/api/session", route => fault === "network" ? route.abort("connectionfailed") : route.fulfill({ status: Number(fault), contentType: "application/json", body: '{"status":"failed"}' }));
    await page.reload();
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
    const failedBoot = await page.evaluate(() => ({ busy: state.quoteSessionDashboardBusy, dashboardDisabled: elements.topbarBrandButton.disabled, customer: elements.clientName.value, scope: currentBrowserRecoveryScope(), stored: localStorage.getItem(QUOTE_SESSION_STORAGE_KEY) }));
    assert.equal(failedBoot.busy, false, "Unverified boot left dashboard loading stuck");
    assert.equal(failedBoot.dashboardDisabled, false);
    assert.equal(failedBoot.customer, "");
    assert.equal(failedBoot.scope, "");
    assert.equal(failedBoot.stored, before);
    cases.push({ id: "initial-session-" + fault + "-releases-loading-without-restoring", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const before = await page.evaluate(() => { saveSessionState(); return localStorage.getItem(QUOTE_SESSION_STORAGE_KEY); });
    const login = await loginBoundary(page);
    await authFailure(page, "**/api/session");
    await page.addInitScript(() => {
      window.restoreAuthMarkerStorage = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) { if (key === "swooshz_auth_recovery_v1") throw new Error("Synthetic quota"); return window.restoreAuthMarkerStorage.call(this, key, value); };
    });
    await page.reload();
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && authenticationRecoveryPromise === null);
    assert.equal(login.count, 0);
    assert.equal(await page.evaluate(() => localStorage.getItem(QUOTE_SESSION_STORAGE_KEY)), before);
    const guidance = await page.evaluate(() => document.body.textContent);
    assert(guidance.includes("Keep this page open") && guidance.includes("browser storage") && guidance.includes("try Dashboard again"));
    assert.equal(await page.locator("#topbarBrandButton").isEnabled(), true);
    await page.evaluate(() => {
      Storage.prototype.setItem = window.restoreAuthMarkerStorage;
      state.images = [{ name: "new-unverified.png", type: "image/png", data_url: "data:image/png;base64,AQID" }];
      setInputValue(elements.clientName, "New unverified edit");
    });
    await page.locator("#topbarBrandButton").click({ noWaitAfter: true });
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    assert.equal(await page.evaluate(() => state.images.length), 1);
    assert.equal(await page.evaluate(() => localStorage.getItem(QUOTE_SESSION_STORAGE_KEY)), before);
    await page.evaluate(() => { state.images = []; setInputValue(elements.clientName, ""); });
    await page.locator("#topbarBrandButton").click({ noWaitAfter: true });
    await page.waitForURL(base + "/login");
    assert.equal(login.count, 1);
    assert.equal(await page.evaluate(() => localStorage.getItem("swooshz_quote_session_v1")), before);
    cases.push({ id: "initial401-marker-storage-failure-existing-control-retry", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    await page.evaluate(() => saveSessionState());
    await page.reload();
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && !state.quoteSessionDashboardBusy);
    assert.equal(await page.locator("#clientName").inputValue(), "Synthetic unsaved customer");
    assert.equal(await page.evaluate(() => localStorage.getItem(AUTH_RECOVERY_STORAGE_KEY)), null);
    cases.push({ id: "ordinary-refresh-retains-draft-without-auth-marker", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const snapshot = await page.evaluate(() => buildSessionSnapshot());
    let duplicatePosts = 0;
    let normalizePosts = 0;
    await page.route("**/api/line-items/normalize", route => {
      normalizePosts++;
      const payload = route.request().postDataJSON();
      assert.equal(payload.quote_session?.session_id, "quote-auth-ordinary-copy");
      assert.equal(payload.pricing_analysis_seal, undefined);
      assert(Array.isArray(payload.line_items));
      assert.equal(payload.line_items.some(row => row.pricing_authority?.variant === "catalog"), false);
      // This empty-draft transport fixture grants no authority. Real pricing
      // normalisation and explicit confirmation retain separate guarded tests.
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "normalized", line_items: payload.line_items }) });
    });
    await page.route("**/api/quote-sessions/quote-auth-ordinary-source", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ quote_session: { session_id: "quote-auth-ordinary-source", draft_state: snapshot } }) }));
    await page.route("**/api/quote-sessions", route => {
      if (route.request().method() !== "POST") return route.fulfill({ status: 200, contentType: "application/json", body: '{"quote_sessions":[]}' });
      duplicatePosts++;
      const payload = JSON.parse(route.request().postData());
      assert.equal(payload.session_id, "quote-auth-ordinary-copy");
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "saved", quote_session: { session_id: payload.session_id } }) });
    });
    await page.evaluate(() => {
      saveSessionState();
      saveDashboardOperation({ type: "duplicate", sourceSessionId: "quote-auth-ordinary-source", targetSessionId: "quote-auth-ordinary-copy" });
    });
    await page.reload();
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && !state.quoteSessionRestoreBusy && state.dashboardActiveSessionId === "quote-auth-ordinary-copy");
    assert.equal(normalizePosts, 1);
    assert.equal(duplicatePosts, 1);
    assert.equal(await page.evaluate(() => localStorage.getItem(DASHBOARD_OPERATION_STORAGE_KEY)), null);
    cases.push({ id: "ordinary-refresh-resumes-owned-duplicate-once", result: "PASS", duplicatePosts, normalizePosts });
    await context.close();
  }
  for (const fault of ["plain401", "403", "500", "network"]) {
    const { page, context } = await freshPage();
    const login = await loginBoundary(page);
    await page.route("**/api/quote-sessions", route => fault === "network" ? route.abort("connectionfailed") : route.fulfill({
      status: fault === "plain401" ? 401 : Number(fault), contentType: "application/json", body: '{"status":"failed","errors":["Synthetic unavailable"]}',
    }));
    await page.evaluate(() => loadQuoteDashboard());
    assert.equal(login.count, 0);
    assert.equal(await page.evaluate(() => state.authenticationRecoveryRequested), false);
    assert.equal(await page.evaluate(() => Boolean(state.quoteSessionLoadError)), true);
    cases.push({ id: "ordinary-error-" + fault, result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    let posts = 0;
    await page.route("**/api/quote-sessions", route => {
      if (route.request().method() !== "POST") return route.fulfill({ status: 200, contentType: "application/json", body: '{"quote_sessions":[]}' });
      posts++;
      return route.fulfill({ status: 401, contentType: "application/json", body: '{"status":"auth_required"}' });
    });
    await page.evaluate(() => {
      state.quoteSessionDraftSaveStarted = true;
      state.images = [{ name: "synthetic.png", type: "image/png", size: 68,
        data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII=" }];
      state.activeSidePanel = "customer";
      void returnToDashboard();
    });
    await page.waitForURL(base + "/login");
    assert.equal(posts, 1);
    assert.equal(login.count, 1);
    const durable = await page.evaluate(async () => {
      const saved = JSON.parse(localStorage.getItem("swooshz_quote_session_v1"));
      const db = await new Promise((resolve, reject) => { const r = indexedDB.open("swooshz_quote_session_files_v1"); r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
      const record = await new Promise((resolve, reject) => {
        const transaction = db.transaction("reference_files", "readonly");
        const request = transaction.objectStore("reference_files").get(saved.images[0].session_file_key);
        request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
      });
      return { customer: saved.quoteDetails.client.name, images: saved.images.length,
        payloadDurable: Boolean(record?.data_url), fileScopeMatches: record?.browserRecoveryScope === saved.browserRecoveryScope };
    });
    assert.equal(durable.customer, "Synthetic unsaved customer");
    assert.equal(durable.images, 1);
    assert.equal(durable.payloadDurable, true);
    assert.equal(durable.fileScopeMatches, true);
    await page.unroute("**/login");
    await page.unroute("**/api/quote-sessions");
    await page.route("**/api/quote-sessions", route => route.fulfill({ status: 200, contentType: "application/json", body: '{"quote_sessions":[]}' }));
    await page.goto(base + "/login");
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
    assert.equal(await page.evaluate(() => state.images.length === 1 && Boolean(state.images[0].data_url)), true);
    cases.push({ id: "current-quote-dashboard-save401-no-replay", result: "PASS", originalPosts: posts });
    await context.close();
  }
  for (const storage of ["localStorage", "indexedDB"]) {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(kind => {
      if (kind === "localStorage") {
        window.restoreCheckpointStorage = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) { if (key === AUTH_RECOVERY_STORAGE_KEY) throw new Error("Synthetic quota"); return window.restoreCheckpointStorage.call(this, key, value); };
      } else {
        window.restoreCheckpointStorage = persistSessionFiles;
        persistSessionFiles = () => Promise.reject(new Error("Synthetic IDB failure"));
      }
    }, storage);
    await rejectedDashboard(page);
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    const noWrite = await page.evaluate(async () => {
      let sends = 0;
      const original = window.fetch;
      window.fetch = async (...args) => { sends++; return original(...args); };
      await postJson("/api/quote-sessions", {});
      queueQuoteSessionDraftStateSave({ delay: 0 });
      await new Promise(resolve => setTimeout(resolve, 30));
      window.fetch = original;
      return sends;
    });
    assert.equal(noWrite, 0);
    await page.evaluate(kind => {
      if (kind === "localStorage") Storage.prototype.setItem = window.restoreCheckpointStorage;
      else persistSessionFiles = window.restoreCheckpointStorage;
      void handleTopbarBrandClick();
    }, storage);
    await page.waitForURL(base + "/login");
    assert.equal(login.count, 1);
    cases.push({ id: storage + "-checkpoint-block-explicit-retry", result: "PASS", blockedNewSends: noWrite });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(() => {
      elements.pricingReferenceModal.hidden = false;
      state.pendingPricingReference = { items: [] };
      state.editingPricingReferenceId = "";
    });
    await rejectedDashboard(page);
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    await page.evaluate(() => { state.pendingPricingReference = null; elements.pricingReferenceModal.hidden = true; void handleTopbarBrandClick(); });
    await page.waitForURL(base + "/login");
    cases.push({ id: "dirty-import-blocks-login-until-cancelled", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(() => {
      state.outputRows = [normalizeOutputRow({ description: "Synthetic original", section: "Synthetic", source_basis_line_id: "synthetic-auth-row", quantity: 2, unit: "nos" })];
      renderPricingMatches(state.outputRows);
      const cell = elements.pricingMatchesBody.querySelector('[data-output-edit-field="unit_price_override"]');
      if (!cell) throw new Error("Expected production price editor");
      openOutputCellEditor(cell);
      activeOutputEditor.editor.value = "invalid";
    });
    await rejectedDashboard(page);
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    await page.evaluate(() => {
      revokeOutputEditor();
      renderPricingMatches(state.outputRows);
      const cell = elements.pricingMatchesBody.querySelector('[data-output-edit-field="description"]');
      openOutputCellEditor(cell);
      activeOutputEditor.editor.value = "Synthetic pending edit";
      void handleTopbarBrandClick();
    });
    await page.waitForURL(base + "/login");
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("swooshz_quote_session_v1")));
    assert.equal(saved.outputRows[0].description, "Synthetic pending edit");
    cases.push({ id: "invalid-editor-block-valid-pending-editor-durable", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(() => {
      window.originalAuthFilePersistence = persistSessionFiles;
      persistSessionFiles = async records => { await new Promise(resolve => { window.releaseAuthCheckpoint = resolve; }); return window.originalAuthFilePersistence(records); };
    });
    // Start requests only after their explicit local adapters are installed.
    await page.route("**/api/auth-parallel-*", route => route.fulfill({ status: 401, contentType: "application/json", body: '{"status":"auth_required"}' }));
    await page.evaluate(() => { void getJson("/api/auth-parallel-a"); void getJson("/api/auth-parallel-b"); });
    await page.waitForFunction(() => typeof window.releaseAuthCheckpoint === "function");
    assert.equal(login.count, 0);
    await page.evaluate(() => window.releaseAuthCheckpoint());
    await page.waitForURL(base + "/login");
    assert.equal(login.count, 1);
    cases.push({ id: "parallel401-single-durable-flight", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(() => {
      fileToDataUrl = () => new Promise(resolve => { window.finishPendingLogo = () => resolve("data:image/png;base64,AQID"); });
    });
    await page.locator("#headerLogoInput").setInputFiles({ name: "pending-logo.png", mimeType: "image/png", buffer: Buffer.from([1, 2, 3]) });
    await page.waitForFunction(() => state.browserRecoveryFileOperations === 1);
    await rejectedDashboard(page);
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    await page.evaluate(() => window.finishPendingLogo());
    await page.waitForFunction(() => state.browserRecoveryFileOperations === 0 && Boolean(state.headerLogo));
    await page.evaluate(() => { void handleTopbarBrandClick(); });
    await page.waitForURL(base + "/login");
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("swooshz_quote_session_v1")).quoteDetails.company.logo_name), "pending-logo.png");
    cases.push({ id: "inflight-logo-blocks-login-until-durable-retry", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(async () => {
      state.images = [{ name: "existing.png", type: "image/png", data_url: "data:image/png;base64,ZXhpc3Rpbmc=" }];
      const db = await openSessionFileDb();
      const gate = new Promise(resolve => { window.releaseChangedDraftCheckpoint = () => resolve(db); });
      openSessionFileDb = () => gate;
      void recoverRequiredAuthentication(401, { status: "auth_required" });
    });
    await page.waitForFunction(() => typeof window.releaseChangedDraftCheckpoint === "function");
    await page.evaluate(() => {
      setInputValue(elements.clientName, "Synthetic edit during checkpoint");
      window.releaseChangedDraftCheckpoint();
    });
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    await page.evaluate(() => { void handleTopbarBrandClick(); });
    await page.waitForURL(base + "/login");
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("swooshz_quote_session_v1")).quoteDetails.client.name), "Synthetic edit during checkpoint");
    cases.push({ id: "edit-during-IDB-refuses-stale-checkpoint-explicit-retry", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(() => {
      fileToDataUrl = () => new Promise(resolve => { window.finishPendingUpload = () => resolve("data:image/png;base64,AQID"); });
      void addImagesFromFiles([new File([new Uint8Array([1, 2, 3])], "pending.png", { type: "image/png" })]);
    });
    await page.waitForFunction(() => state.browserRecoveryFileOperations === 1);
    await rejectedDashboard(page);
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    await page.evaluate(() => window.finishPendingUpload());
    await page.waitForFunction(() => state.browserRecoveryFileOperations === 0 && state.images.length === 1);
    await page.evaluate(() => { void handleTopbarBrandClick(); });
    await page.waitForURL(base + "/login");
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("swooshz_quote_session_v1")).images[0].name), "pending.png");
    cases.push({ id: "inflight-upload-blocks-login-until-durable-retry", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(async () => {
      state.images = [{ name: "existing.png", type: "image/png", data_url: "data:image/png;base64,ZXhpc3Rpbmc=" }];
      const db = await openSessionFileDb();
      const gate = new Promise(resolve => { window.releaseLateUploadCheckpoint = () => resolve(db); });
      openSessionFileDb = () => gate;
      void recoverRequiredAuthentication(401, { status: "auth_required" });
    });
    await page.waitForFunction(() => typeof window.releaseLateUploadCheckpoint === "function");
    await page.evaluate(() => {
      void addImagesFromFiles([new File([new Uint8Array([1, 2, 3])], "late.png", { type: "image/png" })]);
    });
    await page.waitForFunction(() => state.images.length === 2);
    await page.evaluate(() => window.releaseLateUploadCheckpoint());
    await blockedRecovery(page);
    assert.equal(login.count, 0);
    await page.waitForFunction(() => state.browserRecoveryFileOperations === 0);
    await page.evaluate(() => { void handleTopbarBrandClick(); });
    await page.waitForURL(base + "/login");
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem("swooshz_quote_session_v1")).images.length), 2);
    cases.push({ id: "upload-during-IDB-refuses-stale-checkpoint-explicit-retry", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    await page.evaluate(async () => {
      state.images = [{ name: "synthetic.png", type: "image/png", data_url: "data:image/png;base64,c3ludGhldGlj" }];
      const db = await openSessionFileDb();
      const gate = new Promise(resolve => { window.releaseAuthDatabase = () => resolve(db); });
      openSessionFileDb = () => gate;
      void recoverRequiredAuthentication(401, { status: "auth_required" });
    });
    await page.waitForFunction(() => typeof window.releaseAuthDatabase === "function");
    await page.evaluate(() => { void purgeBrowserRecoveryState(); window.releaseAuthDatabase(); });
    await page.waitForFunction(() => authenticationRecoveryPromise === null);
    assert.equal(login.count, 0);
    assert.equal(await page.evaluate(() => localStorage.getItem("swooshz_quote_session_v1")), null);
    cases.push({ id: "purge-during-IDB-checkpoint-cancels-navigation", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    await page.evaluate(() => saveSessionState());
    const login = await loginBoundary(page);
    let releaseLogout;
    await page.route("**/logout", route => new Promise(resolve => { releaseLogout = async () => { await route.fulfill({ status: 204, headers: { "X-SQAG-Logout-Location": "/signed-out" } }); resolve(); }; }));
    await page.locator("#topbarLogoutLink").click();
    await page.waitForFunction(() => state.isSigningOut);
    await rejectedDashboard(page);
    await page.waitForFunction(() => !state.quoteSessionDashboardBusy);
    assert.equal(login.count, 0);
    await releaseLogout();
    await page.waitForURL(base + "/signed-out");
    assert.equal(await page.evaluate(() => localStorage.getItem("swooshz_quote_session_v1")), null);
    cases.push({ id: "logout-inflight-suppresses-auth-checkpoint", result: "PASS" });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    let mutationPosts = 0;
    await page.route("**/api/auth-refresh-action", route => { mutationPosts++; return route.fulfill({ status: 403, contentType: "application/json", body: '{"status":"blocked"}' }); });
    await authFailure(page, "**/api/session");
    await page.evaluate(() => { void postJson("/api/auth-refresh-action", {}); });
    await page.waitForURL(base + "/login");
    assert.equal(mutationPosts, 1);
    assert.equal(login.count, 1);
    cases.push({ id: "403-refresh-session401-no-mutation-replay", result: "PASS", originalPosts: mutationPosts });
    await context.close();
  }
  for (const type of ["confirm_basis", "draft", "generate"]) {
    const { page, context } = await freshPage();
    const scope = await seed(page);
    const original = await page.evaluate(kind => {
      const snapshot = buildSessionSnapshot();
      snapshot.workflowStage = kind === "draft" ? "analyzing" : "generating";
      snapshot.activeJob = { id: kind === "confirm_basis" ? "operation-auth-synthetic" : "job-auth-synthetic", type: kind, phase: "starting", startedAt: new Date().toISOString(), browserRecoveryScope: currentBrowserRecoveryScope() };
      const text = JSON.stringify(snapshot);
      localStorage.setItem(QUOTE_SESSION_STORAGE_KEY, text);
      saveDashboardOperation({ type: "duplicate", sourceSessionId: "quote-auth-source", targetSessionId: "quote-auth-copy" });
      return { snapshot: text, operation: localStorage.getItem(DASHBOARD_OPERATION_STORAGE_KEY) };
    }, type);
    const login = await loginBoundary(page);
    await authFailure(page, "**/api/session");
    await page.reload();
    await page.waitForURL(base + "/login");
    const retained = await page.evaluate(() => ({ snapshot: localStorage.getItem("swooshz_quote_session_v1"), operation: localStorage.getItem("swooshz_dashboard_operation_v1"), marker: localStorage.getItem("swooshz_auth_recovery_v1") }));
    assert.equal(retained.snapshot, original.snapshot);
    assert.equal(retained.operation, original.operation);
    assert.equal(retained.marker, "1");
    await page.unroute("**/api/session");
    await page.unroute("**/login");
    let replayPosts = 0;
    await page.route("**/api/**", route => {
      if (route.request().method() === "POST" && !route.request().url().endsWith("/api/log")) { replayPosts++; return route.fulfill({ status: 500, contentType: "application/json", body: '{"status":"failed"}' }); }
      return route.fallback();
    });
    await page.goto(base + "/login");
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting && !state.quoteSessionDashboardBusy);
    const recovered = await page.evaluate(() => ({ scope: currentBrowserRecoveryScope(), job: state.activeJob, stage: state.workflowStage, customer: elements.clientName.value, operation: state.dashboardOperation, marker: localStorage.getItem("swooshz_auth_recovery_v1") }));
    assert.equal(recovered.scope, scope);
    assert.equal(recovered.customer, "Synthetic unsaved customer");
    assert.equal(recovered.job, null);
    assert.equal(recovered.operation, null);
    assert.equal(recovered.marker, null);
    assert(!["analyzing", "generating"].includes(recovered.stage));
    assert.equal(replayPosts, 0);
    assert.equal(login.count, 1);
    cases.push({ id: "cleanboot401-" + type + "-and-duplicate-replay-fenced", result: "PASS", replayPosts });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    await seed(page);
    const login = await loginBoundary(page);
    const originalSession = await page.evaluate(async () => (await fetch("/api/session")).json());
    let releaseSession;
    let mutationPosts = 0;
    await page.route("**/api/session", route => new Promise(resolve => {
      releaseSession = async () => { await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(originalSession) }); resolve(); };
    }));
    await page.route("**/api/refresh-race-action", route => { mutationPosts++; return route.fulfill({ status: 403, contentType: "application/json", body: '{"status":"blocked"}' }); });
    await authFailure(page, "**/api/parallel-expired-session");
    await page.evaluate(() => {
      window.originalRacePersistence = persistSessionFiles;
      persistSessionFiles = async records => { await new Promise(resolve => { window.releaseRecoveryRace = resolve; }); return window.originalRacePersistence(records); };
      void postJson("/api/refresh-race-action", {});
    });
    for (let attempt = 0; !releaseSession && attempt < 50; attempt++) await page.waitForTimeout(20);
    assert.equal(typeof releaseSession, "function");
    await page.evaluate(() => { void getJson("/api/parallel-expired-session"); });
    await page.waitForFunction(() => typeof window.releaseRecoveryRace === "function");
    await releaseSession();
    await page.waitForFunction(() => state.authenticated);
    await page.evaluate(() => window.releaseRecoveryRace());
    await blockedRecovery(page);
    assert.equal(mutationPosts, 1);
    assert.equal(login.count, 0);
    await page.evaluate(() => { persistSessionFiles = window.originalRacePersistence; void handleTopbarBrandClick(); });
    await page.waitForURL(base + "/login");
    cases.push({ id: "concurrent401-fences-inflight403-refresh-replay", result: "PASS", originalPosts: mutationPosts });
    await context.close();
  }
  {
    const { page, context } = await freshPage();
    const scope = await seed(page);
    const originalSession = await page.evaluate(async () => (await fetch("/api/session")).json());
    await loginBoundary(page);
    await rejectedDashboard(page);
    await page.waitForURL(base + "/login");
    await page.unroute("**/login");
    // The new identity has a valid session; retire the old identity's expired
    // Dashboard response before booting so it cannot start another login.
    await page.unroute("**/api/quote-sessions");
    await page.route("**/api/quote-sessions", route => route.fulfill({ status: 200, contentType: "application/json", body: '{"quote_sessions":[]}' }));
    const nextScope = scope + "-other-identity";
    await page.route("**/api/session", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...originalSession, browser_recovery_scope: nextScope, user: { subject: "synthetic-other-subject" } }) }));
    await page.goto(base);
    await page.waitForFunction(expectedScope => typeof state !== "undefined" && !state.isBooting && !state.isRecoveryScopeTransitioning && !state.isPageUnloading && currentBrowserRecoveryScope() === expectedScope, nextScope);
    assert.notEqual(await page.locator("#clientName").inputValue(), "Synthetic unsaved customer");
    assert.equal(await page.evaluate(() => state.images.length), 0);
    assert.notEqual(await page.evaluate(() => state.quoteSessionId), "quote-auth-recovery-synthetic");
    cases.push({ id: "different-identity-workspace-scope-purges-draft", result: "PASS" });
    await context.close();
  }
}
try {
  const { page, context } = await freshPage();
  const scope = await seed(page);
  let loginCount = 0;
  await page.route("**/login", route => { loginCount++; return route.fulfill({ status: 200, contentType: "text/html", body: "Synthetic login boundary" }); });
  await authFailure(page);
  await page.evaluate(() => { void loadQuoteDashboard(); });
  if (baseline) {
    await page.waitForFunction(() => Boolean(state.quoteSessionLoadError));
    const result = await page.evaluate(() => ({ genericFailure: Boolean(state.quoteSessionLoadError), cachedAuthenticated: state.authenticated, sessions: state.quoteSessions.length }));
    assert.deepEqual(result, { genericFailure: true, cachedAuthenticated: true, sessions: 0 });
    assert.equal(loginCount, 0);
    cases.push({ id: "deployed-401-generic-dashboard", result: "REPRODUCED_RED", ...result, loginCount });
  } else {
    await page.waitForURL(base + "/login", { timeout: 5000 });
    assert.equal(loginCount, 1);
    const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("swooshz_quote_session_v1")));
    assert.equal(saved.browserRecoveryScope, scope);
    assert.equal(saved.quoteDetails.client.name, "Synthetic unsaved customer");
    assert.equal(saved.quoteDetails.project.title, "Synthetic unsaved project");
    assert.equal(saved.activeJob, null);
    await page.unroute("**/login");
    await page.unroute("**/api/quote-sessions");
    await page.route("**/api/quote-sessions", route => route.fulfill({ status: 200, contentType: "application/json", body: '{"quote_sessions":[]}' }));
    await page.goto(base + "/login");
    await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
    assert.equal(await page.locator("#clientName").inputValue(), "Synthetic unsaved customer");
    assert.equal(await page.evaluate(() => currentBrowserRecoveryScope()), scope);
    cases.push({ id: "dashboard-401-fixed-login-same-scope-draft", result: "PASS", loginCount });
  }
  await context.close();
  if (!baseline) await runAdditionalCases();
  const result = { result: baseline ? "REPRODUCED_RED" : "PASS", cases, localSyntheticOnly: true };
  await fs.writeFile(path.join(evidence, "result.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result));
} catch (error) {
  await fs.writeFile(path.join(evidence, "failure.json"), JSON.stringify({ result: "FAIL", completedCases: cases, error: String(error.message) }, null, 2));
  throw error;
} finally {
  await browser.close();
  harness.kill("SIGTERM");
  await new Promise(resolve => harness.exitCode !== null ? resolve() : harness.once("exit", resolve));
}
