import { spawn } from "node:child_process";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { TEST_REFERENCE_FILE_NAME, seedQuoteDraftFromTestFixture } from "./playwright-test-seeded-setup.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const args = process.argv.slice(2);

function readArg(name, fallback = "") {
  const index = args.indexOf(name);
  if (index >= 0 && args[index + 1]) return args[index + 1];
  const prefix = `${name}=`;
  const inline = args.find((arg) => arg.startsWith(prefix));
  return inline ? inline.slice(prefix.length) : fallback;
}

const options = {
  screenshots: args.includes("--screenshots") || args.includes("--screenshot"),
  headed: args.includes("--headed"),
  keepServer: args.includes("--keep-server"),
  host: readArg("--host", "127.0.0.1"),
  port: Number(readArg("--port", process.env.PLAYWRIGHT_PORT || "8765")),
};

const baseUrl = `http://${options.host}:${options.port}`;
const outputDir = path.join(root, "_output", "playwright");
const quoteDataRoot = path.join(root, "_tmp", "playwright-quote-data");

function pythonCommand() {
  if (process.env.PYTHON) return process.env.PYTHON;
  if (process.platform !== "win32") return "python3";
  const bundled = path.join(os.homedir(), ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe");
  if (fsSync.existsSync(bundled)) return bundled;
  return "python";
}

async function healthOk() {
  try {
    const response = await fetch(`${baseUrl}/api/health`, { signal: AbortSignal.timeout(1200) });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForHealth(timeoutMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await healthOk()) return true;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return false;
}

function startServer() {
  const server = spawn(
    pythonCommand(),
    ["webapp/server.py", "--host", options.host, "--port", String(options.port)],
    {
      cwd: root,
      env: { ...process.env, APP_MODE: "local", QUOTE_DATA_ROOT: process.env.QUOTE_DATA_ROOT || quoteDataRoot },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  const output = [];
  const collect = (chunk) => {
    output.push(String(chunk));
    if (output.join("").length > 8000) output.shift();
  };
  server.stdout.on("data", collect);
  server.stderr.on("data", collect);
  return { server, output };
}

async function stopServer(serverInfo) {
  if (!serverInfo || options.keepServer) return;
  if (serverInfo.server.killed) return;
  serverInfo.server.kill();
  await new Promise((resolve) => serverInfo.server.once("exit", resolve));
}

async function screenshot(page, name) {
  if (!options.screenshots) return "";
  await fs.mkdir(outputDir, { recursive: true });
  const filePath = path.join(outputDir, name);
  await page.screenshot({ path: filePath, fullPage: false });
  return filePath;
}

async function expectQuoteSessionDeleteButtonFocused(page) {
  await page.waitForFunction(() => document.activeElement?.id === "confirmQuoteSessionDeleteButton", null, { timeout: 15000 });
}

async function expectTopbarPrimaryAction(page, expectedAction) {
  const dashboardVisible = await page.locator("#backToDashboardButton").isVisible();
  const newQuoteVisible = await page.locator("#newQuoteButton").isVisible();
  const settingsVisible = await page.locator("#settingsButton").isVisible();
  if (!settingsVisible) {
    throw new Error("Pricing Reference topbar action should stay visible.");
  }
  if (expectedAction === "dashboard" && (!dashboardVisible || newQuoteVisible)) {
    throw new Error(`Expected Dashboard-only topbar action, found ${JSON.stringify({ dashboardVisible, newQuoteVisible })}.`);
  }
  if (expectedAction === "new-quote" && (!newQuoteVisible || dashboardVisible)) {
    throw new Error(`Expected New Quote-only topbar action, found ${JSON.stringify({ dashboardVisible, newQuoteVisible })}.`);
  }
}

async function dashboardPanelActionMetrics(page, label) {
  await page.locator("#dashboardSelectedSessionPanel").scrollIntoViewIfNeeded();
  const panelBox = await page.locator("#dashboardSelectedSessionPanel").boundingBox();
  const actionBox = await page.locator("#dashboardSelectedSessionPanel .dashboard-selected-actions").boundingBox();
  const firstActionBox = await page.locator("#dashboardSelectedSessionPanel .dashboard-selected-actions button, #dashboardSelectedSessionPanel .dashboard-selected-actions a").first().boundingBox();
  const actionPositions = await page.locator("#dashboardSelectedSessionPanel").evaluate((panel) => {
    const panelRect = panel.getBoundingClientRect();
    const actions = panel.querySelector(".dashboard-selected-actions");
    const actionRect = actions?.getBoundingClientRect();
    const modifyButton = actions?.querySelector('[data-dashboard-panel-action="modify-session"]');
    const deleteButton = actions?.querySelector('[data-dashboard-panel-action="delete-session"], [data-dashboard-panel-action="delete-selected"]');
    const clearButton = actions?.querySelector('[data-dashboard-panel-action="clear-selection"]');
    const modifyRect = modifyButton?.getBoundingClientRect();
    const deleteRect = deleteButton?.getBoundingClientRect();
    const clearRect = clearButton?.getBoundingClientRect();
    return {
      actionX: actionRect ? Math.round(actionRect.x - panelRect.x) : null,
      actionWidth: actionRect ? Math.round(actionRect.width) : null,
      actionTop: actionRect ? Math.round(actionRect.y - panelRect.y) : null,
      modifyTop: modifyRect ? Math.round(modifyRect.y - panelRect.y) : null,
      modifyHeight: modifyRect ? Math.round(modifyRect.height) : null,
      deleteTop: deleteRect ? Math.round(deleteRect.y - panelRect.y) : null,
      deleteHeight: deleteRect ? Math.round(deleteRect.height) : null,
      clearTop: clearRect ? Math.round(clearRect.y - panelRect.y) : null,
      clearHeight: clearRect ? Math.round(clearRect.height) : null,
    };
  });
  if (!panelBox || !actionBox || !firstActionBox) {
    throw new Error(`Dashboard ${label} action footer was not measurable.`);
  }
  if (actionPositions.deleteTop === null || actionPositions.clearTop === null) {
    throw new Error(`Dashboard ${label} shared delete/clear actions were not measurable.`);
  }
  const deleteClearGap = actionPositions.clearTop - actionPositions.deleteTop;
  if (deleteClearGap < 38 || deleteClearGap > 54) {
    throw new Error(`Dashboard ${label} delete/clear spacing is unexpected: ${deleteClearGap}px.`);
  }
  const modifyDeleteGap = actionPositions.modifyTop === null ? null : actionPositions.deleteTop - actionPositions.modifyTop;
  const deleteClearActualGap = actionPositions.deleteHeight === null
    ? null
    : actionPositions.clearTop - (actionPositions.deleteTop + actionPositions.deleteHeight);
  const modifyDeleteActualGap = actionPositions.modifyHeight === null
    ? null
    : actionPositions.deleteTop - (actionPositions.modifyTop + actionPositions.modifyHeight);
  if (modifyDeleteActualGap !== null && deleteClearActualGap !== null && Math.abs(modifyDeleteActualGap - deleteClearActualGap) > 4) {
    throw new Error(`Dashboard ${label} modify/delete gap differs from delete/clear gap: ${modifyDeleteActualGap}px vs ${deleteClearActualGap}px.`);
  }
  const bottomGap = Math.round((panelBox.y + panelBox.height) - (actionBox.y + actionBox.height));
  if (bottomGap < 8 || bottomGap > 36) {
    throw new Error(`Dashboard ${label} action footer is not bottom anchored: ${bottomGap}px gap.`);
  }
  return {
    bottomGap,
    actionViewportTop: Math.round(actionBox.y),
    firstActionTop: Math.round(firstActionBox.y),
    deleteClearGap,
    modifyDeleteGap,
    deleteClearActualGap,
    modifyDeleteActualGap,
    ...actionPositions,
  };
}

async function verifyMobileHeaderOrder(page) {
  await page.setViewportSize({ width: 520, height: 720 });
  const metrics = await page.evaluate(() => {
    const visibleRect = (selector) => {
      const element = document.querySelector(selector);
      if (!element || element.hidden) return null;
      const style = window.getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden") return null;
      const rect = element.getBoundingClientRect();
      if (!rect.width || !rect.height) return null;
      return {
        top: Math.round(rect.top),
        bottom: Math.round(rect.bottom),
        left: Math.round(rect.left),
        width: Math.round(rect.width),
        text: element.textContent?.trim() || "",
      };
    };
    return {
      auth: visibleRect("#topbarAuthState"),
      dashboard: visibleRect("#backToDashboardButton"),
      pricing: visibleRect("#settingsButton"),
      privacy: visibleRect(".topbar-privacy-link"),
    };
  });
  if (!metrics.dashboard || !metrics.pricing || !metrics.privacy) {
    throw new Error(`Mobile header actions were not measurable: ${JSON.stringify(metrics)}.`);
  }
  const actionTop = Math.min(metrics.dashboard.top, metrics.pricing.top);
  const actionBottom = Math.max(metrics.dashboard.bottom, metrics.pricing.bottom);
  if (metrics.auth && metrics.auth.bottom > actionTop + 4) {
    throw new Error(`Mobile auth status should appear before dashboard/pricing actions: ${JSON.stringify(metrics)}.`);
  }
  if (actionBottom > metrics.privacy.top + 4) {
    throw new Error(`Mobile Privacy Notice should appear below dashboard/pricing actions: ${JSON.stringify(metrics)}.`);
  }
  if (metrics.dashboard.width < 120 || metrics.pricing.width < 120) {
    throw new Error(`Mobile dashboard/pricing actions should remain prominent: ${JSON.stringify(metrics)}.`);
  }
}

async function verifyMobileBasisLegendAndOutputCards(page) {
  await page.setViewportSize({ width: 520, height: 720 });
  await page.evaluate(() => {
    state.quoteBasis = {};
    state.quoteBasisSections = [{
      id: "graphics",
      title: "Graphics",
      lines: [
        { tag: "Include", text: "Printed wall graphics", confidence: 92, quantity: 12, unit: "sqm" },
        { tag: "Confirm", text: "Confirm counter finish", confidence: 71, quantity: 1, unit: "lot" },
        { tag: "Custom", text: "AI proposed curved counter", confidence: 88, custom_confirmed: true, quantity: 1, unit: "set" },
        { tag: "Exclude", text: "Exclude back-room storage", confidence: 93, quantity: 1, unit: "lot" },
      ],
    }];
    state.aiFailed = false;
    state.draftSource = "edited";
    updateQuoteBasisCard("edited");
    setSidePanel("basis", { force: true });
  });
  await page.locator(".basis-tag-legend").waitFor({ state: "visible", timeout: 15000 });
  const legendMetrics = await page.locator(".basis-tag-legend").evaluate((legend) => {
    const items = Array.from(legend.querySelectorAll(".basis-tag-legend-item")).map((item) => {
      const rect = item.getBoundingClientRect();
      const text = item.querySelector("span")?.getBoundingClientRect();
      return {
        width: Math.round(rect.width),
        left: Math.round(rect.left),
        textWidth: text ? Math.round(text.width) : 0,
        text: item.textContent?.trim() || "",
      };
    });
    return {
      columns: window.getComputedStyle(legend).gridTemplateColumns,
      width: Math.round(legend.getBoundingClientRect().width),
      items,
    };
  });
  if (!legendMetrics.items.some((item) => item.text.includes("Include"))
    || !legendMetrics.items.some((item) => item.text.includes("AI Proposal"))
    || !legendMetrics.items.some((item) => item.text.includes("92%"))) {
    throw new Error(`Mobile basis legend is missing expected labels: ${JSON.stringify(legendMetrics)}.`);
  }
  if (legendMetrics.items.some((item) => item.width < 260 || item.textWidth < 140)) {
    throw new Error(`Mobile basis legend items are too cramped: ${JSON.stringify(legendMetrics)}.`);
  }

  await page.evaluate(() => {
    state.basisConfirmed = true;
    state.outputRows = [
      {
        section: "Graphics",
        description: "Printed wall graphics",
        quantity: 12,
        unit: "sqm",
        price_mode: "Priced",
        unit_price_override: 45,
        catalog_unit_price: 45,
        amount: 540,
      },
      {
        section: "Custom",
        description: "Curved service counter",
        quantity: 1,
        unit: "set",
        price_mode: "Included",
        unit_price_override: "Included",
        catalog_unit_price: "",
        amount: 0,
      },
    ];
    state.lineItems = outputRowsToLineItems();
    renderPricingMatches(state.outputRows);
    setSidePanel("output", { force: true });
  });
  await page.locator("#pricingMatchesBody tr").first().waitFor({ state: "visible", timeout: 15000 });
  const outputMetrics = await page.locator("#pricingMatchesBody tr").first().evaluate((row) => {
    const cells = Array.from(row.querySelectorAll("td")).map((cell) => ({
      label: cell.getAttribute("data-output-label") || "",
      display: window.getComputedStyle(cell).display,
      before: window.getComputedStyle(cell, "::before").content,
      width: Math.round(cell.getBoundingClientRect().width),
    }));
    const rowRect = row.getBoundingClientRect();
    return {
      rowDisplay: window.getComputedStyle(row).display,
      rowWidth: Math.round(rowRect.width),
      tableHeadDisplay: window.getComputedStyle(document.querySelector(".output-match-table thead")).display,
      cells,
    };
  });
  const expectedLabels = ["Section", "Description", "Quantity", "Unit", "Unit price", "Amount", "Delete"];
  if (outputMetrics.rowDisplay !== "grid" || outputMetrics.tableHeadDisplay !== "none") {
    throw new Error(`Mobile output rows should render as cards: ${JSON.stringify(outputMetrics)}.`);
  }
  if (expectedLabels.some((label) => !outputMetrics.cells.some((cell) => cell.label === label && cell.before.includes(label)))) {
    throw new Error(`Mobile output card labels were missing: ${JSON.stringify(outputMetrics)}.`);
  }
  if (outputMetrics.rowWidth > 500 || outputMetrics.cells.some((cell) => cell.width > 500)) {
    throw new Error(`Mobile output card overflows the viewport: ${JSON.stringify(outputMetrics)}.`);
  }
  await page.locator('#pricingMatchesBody tr:first-child [data-output-edit-field="unit_price_override"]').click();
  await page.locator('[data-output-editor-field="unit_price_override"]').waitFor({ state: "visible", timeout: 15000 });
  await page.locator('[data-output-included-action="true"]').waitFor({ state: "visible", timeout: 15000 });
  await page.keyboard.press("Escape");
  await page.locator('#pricingMatchesBody tr:first-child [data-output-delete-row]').click();
  await page.locator("#outputDeleteModal").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#cancelOutputDeleteButton").click();
  await page.locator("#outputDeleteModal").waitFor({ state: "hidden", timeout: 15000 });
}

async function installMockProfiles(page) {
  await page.route("**/api/settings/pricing-references/synthetic-exhibition-fixture-pricing**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        pricing_reference: {
          id: "synthetic-exhibition-fixture-pricing",
          label: "Synthetic Exhibition Fixture Pricing",
          description: "Test-only pricing reference for the Playwright smoke.",
          source: "local",
          schema_version: 1,
          currency: "SGD",
          tax: { label: "GST", rate: 0.09 },
          item_count: 1,
          items: [{
            id: "synthetic-floor-needle-punch-carpet",
            section: "Floor Design",
            description: "Needle punch carpet in colour",
            unit_hint: "sqm",
            internal_cost: 10,
            markup_multiplier: 1.5,
            remarks: "Synthetic smoke fixture row",
          }],
        },
      }),
    });
  });
  await page.route("**/api/profiles", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        profiles: [{
          id: "synthetic-exhibition-fixture-template",
          label: "Synthetic Exhibition Fixture Template",
          description: "Test-only profile for the Playwright smoke.",
          default_pricing_reference: "synthetic-exhibition-fixture-pricing",
          default_quote_detail_preset: "synthetic-fixture-default",
          quote_detail_presets: [{
            id: "synthetic-fixture-default",
            name: "Synthetic Fallback Quote Company",
            details: {
              company: {
                name: "Synthetic Fallback Quote Company Pte Ltd",
                header_details: "Synthetic Fallback Quote Company Pte Ltd\n1 Synthetic Way\nSingapore 000001",
                logo_data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
              },
              quote_text: {
                payment_terms: ["70% synthetic deposit upon confirmation."],
                cheque_payee: "Synthetic Fallback Quote Company Pte Ltd",
              },
              signature: {
                company_signatory: "Synthetic Signatory",
                company_title: "Synthetic Title",
                company_date_label: "Date:",
              },
            },
          }],
        }],
        pricing_references: [{
          id: "synthetic-exhibition-fixture-pricing",
          label: "Synthetic Exhibition Fixture Pricing",
          source: "local",
          currency: "SGD",
          tax: { label: "GST", rate: 0.09 },
          item_count: 1,
        }],
        default_profile_id: "synthetic-exhibition-fixture-template",
        default_pricing_reference_id: "synthetic-exhibition-fixture-pricing",
        company_id: "default",
        workspace: {
          company: { id: "default", slug: "default", display_name: "Quote Generator Workspace" },
          workspace: { id: "default", slug: "default", display_name: "Quote Generator Workspace" },
          runtime_dependencies: {},
        },
      }),
    });
  });
}

async function currentQuoteSessionId(page) {
  return page.evaluate(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
      return String(saved.quoteSessionId || "");
    } catch {
      return "";
    }
  });
}

async function verifyBrowserRecoveryScopeIsolation(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
  const seeded = await page.evaluate(async () => {
    clearQuoteSessionDraftSaveTimer();
    state.isBooting = true;
    state.activeAppView = "quote";
    state.quoteSessionId = "quote-workspace-a-private";
    state.quoteSessionDraftSaveStarted = true;
    elements.clientName.value = "Workspace A Private Customer";
    state.images = [{
      name: "workspace-a-private.png",
      type: "image/png",
      size: 1,
      session_file_key: "workspace-a-private-file",
      data_url: "data:image/png;base64,AA==",
    }];
    const snapshot = buildSessionSnapshot();
    snapshot.browserRecoveryScope = "workspace-a-recovery-scope";
    window.localStorage.setItem("swooshz_quote_session_v1", JSON.stringify(snapshot));
    const db = await openSessionFileDb();
    await new Promise((resolve, reject) => {
      const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readwrite");
      transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME).put({
        name: "workspace-a-private.png",
        type: "image/png",
        size: 1,
        session_file_key: "workspace-a-private-file",
        file_role: "reference",
        data_url: "data:image/png;base64,AA==",
        browserRecoveryScope: "workspace-a-recovery-scope",
      });
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error || new Error("Could not seed foreign session file"));
      transaction.onabort = () => reject(transaction.error || new Error("Foreign session file seeding aborted"));
    });
    return {
      currentScope: state.browserRecoveryScope || "",
      seededScope: snapshot.browserRecoveryScope,
    };
  });
  if (!seeded.currentScope || seeded.currentScope === seeded.seededScope) {
    throw new Error(`Browser recovery test requires distinct current and seeded scopes, found ${JSON.stringify(seeded)}.`);
  }

  const recoveryPage = await page.context().newPage();
  const recoveryProblems = [];
  recoveryPage.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) recoveryProblems.push(`${message.type()}: ${message.text()}`);
  });
  recoveryPage.on("pageerror", (error) => recoveryProblems.push(`pageerror: ${error.message}`));
  recoveryPage.on("response", (response) => {
    if (response.status() >= 400) recoveryProblems.push(`${response.status()} ${response.url()}`);
  });
  let result;
  try {
    await recoveryPage.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await recoveryPage.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await recoveryPage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
    result = await recoveryPage.evaluate(async () => {
      const db = await openSessionFileDb();
      const indexedDbCount = await new Promise((resolve, reject) => {
        const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readonly");
        const request = transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME).count();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("Could not count session files"));
      });
      return {
        currentScope: state.browserRecoveryScope || "",
        clientName: elements.clientName.value,
        quoteSessionId: state.quoteSessionId,
        storedSnapshot: window.localStorage.getItem("swooshz_quote_session_v1"),
        indexedDbCount,
      };
    });
  } finally {
    await recoveryPage.close();
  }
  if (result.currentScope !== seeded.currentScope) {
    throw new Error(`Recovery page booted with an unexpected scope: ${JSON.stringify({ seeded, result })}.`);
  }
  if (recoveryProblems.length) {
    throw new Error(`Recovery page reported failures: ${JSON.stringify(recoveryProblems)}.`);
  }
  if (result.clientName.includes("Workspace A") || result.quoteSessionId === "quote-workspace-a-private") {
    throw new Error(`Mismatched browser recovery state crossed scope: ${JSON.stringify(result)}.`);
  }
  if (result.storedSnapshot !== null || result.indexedDbCount !== 0) {
    throw new Error(`Mismatched browser recovery state was not purged: ${JSON.stringify(result)}.`);
  }
}

async function verifyStaleTabMutationIsRejected(page) {
  const stalePage = await page.context().newPage();
  const unexpectedProblems = [];
  stalePage.on("console", (message) => {
    const text = message.text();
    const expectedRejection = message.type() === "error"
      && text.includes("Failed to load resource")
      && text.includes("403");
    if (["error", "warning"].includes(message.type()) && !expectedRejection) unexpectedProblems.push(message.type() + ": " + text);
  });
  stalePage.on("pageerror", (error) => unexpectedProblems.push("pageerror: " + error.message));
  stalePage.on("response", (response) => {
    const expectedRejection = response.status() === 403 && response.url().endsWith("/api/quote-sessions");
    if (response.status() >= 400 && !expectedRejection) {
      unexpectedProblems.push(response.status() + " " + response.url());
    }
  });

  try {
    await stalePage.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await stalePage.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await stalePage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });
    const initialSession = await stalePage.evaluate(async () => {
      const response = await fetch("/api/session");
      return response.json();
    });
    if (!initialSession.browser_recovery_scope || !initialSession.csrf_token) {
      throw new Error("Stale-tab regression requires a complete initial browser session.");
    }

    const nextScope = initialSession.browser_recovery_scope + "-next-workspace";
    const nextSession = {
      ...initialSession,
      browser_recovery_scope: nextScope,
      csrf_token: "next-workspace-csrf-token-0123456789abcdef",
    };
    const interceptedPosts = [];
    await stalePage.route("**/api/quote-sessions", async (route) => {
      if (route.request().method() !== "POST") {
        await route.fallback();
        return;
      }
      interceptedPosts.push({
        headers: route.request().headers(),
        payload: JSON.parse(route.request().postData() || "{}"),
      });
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({
          status: "blocked",
          errors: ["Missing or invalid local session token."],
        }),
      });
    });
    await stalePage.route("**/api/session", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(nextSession),
      });
    });

    const staleSessionId = "quote-stale-workspace-a-private";
    const navigation = stalePage.waitForNavigation({ waitUntil: "domcontentloaded", timeout: 15000 });
    await stalePage.evaluate((sessionId) => {
      clearQuoteSessionDraftSaveTimer();
      state.activeAppView = "quote";
      state.quoteSessionId = sessionId;
      state.quoteSessionDraftSaveStarted = true;
      elements.clientName.value = "Workspace A Private Customer";
      saveSessionState();
      void saveQuoteSessionDraftState({ quoteGenerated: false }).catch(() => {});
    }, staleSessionId);
    await navigation;
    await stalePage.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await stalePage.waitForFunction(() => state.isBooting === false, null, { timeout: 15000 });

    const result = await stalePage.evaluate(async () => {
      const db = await openSessionFileDb();
      const indexedDbCount = await new Promise((resolve, reject) => {
        const transaction = db.transaction(QUOTE_SESSION_FILE_STORE_NAME, "readonly");
        const request = transaction.objectStore(QUOTE_SESSION_FILE_STORE_NAME).count();
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("Could not count stale-tab session files"));
      });
      return {
        currentScope: state.browserRecoveryScope,
        isTransitioning: state.isRecoveryScopeTransitioning,
        quoteSessionId: state.quoteSessionId,
        storedSnapshot: window.localStorage.getItem("swooshz_quote_session_v1"),
        indexedDbCount,
      };
    });

    if (interceptedPosts.length !== 1) {
      throw new Error("Stale tab must issue exactly one rejected mutation: " + JSON.stringify(interceptedPosts));
    }
    const stalePost = interceptedPosts[0];
    if (stalePost.payload.session_id !== staleSessionId) {
      throw new Error("Stale-tab regression did not exercise the intended private draft: " + JSON.stringify(stalePost.payload));
    }
    const csrfHeader = String(initialSession.csrf_header || "").toLowerCase();
    if (!csrfHeader || stalePost.headers[csrfHeader] !== initialSession.csrf_token) {
      throw new Error("Stale-tab mutation did not carry the original session-bound CSRF token.");
    }
    if (
      result.currentScope !== nextScope
      || result.isTransitioning
      || result.quoteSessionId === staleSessionId
      || result.storedSnapshot !== null
      || result.indexedDbCount !== 0
    ) {
      throw new Error("Stale-tab scope transition did not purge before reload: " + JSON.stringify(result));
    }
    if (unexpectedProblems.length) {
      throw new Error("Stale-tab regression reported unexpected failures: " + JSON.stringify(unexpectedProblems));
    }
  } finally {
    await stalePage.close();
  }
}

async function dashboardQuoteSessionDetail(page, sessionId) {
  return page.evaluate(async (safeSessionId) => {
    const response = await fetch(`/api/quote-sessions/${encodeURIComponent(safeSessionId)}`);
    return response.json();
  }, sessionId);
}

async function createDashboardSmokeSession(page, suffix, options = {}) {
  return page.evaluate(async ({ suffix, sessionIdPrefix, customerName, projectName }) => {
    const sessionResponse = await fetch("/api/session");
    if (!sessionResponse.ok) throw new Error(`Session bootstrap failed: ${sessionResponse.status}`);
    const session = await sessionResponse.json();
    const headers = { "Content-Type": "application/json" };
    if (session.csrf_token) headers[session.csrf_header || "X-CSRF-Token"] = session.csrf_token;
    const safeSuffix = String(suffix || "session").replace(/[^A-Za-z0-9_-]/g, "-");
    const safePrefix = String(sessionIdPrefix || `playwright-bulk-${safeSuffix}`).replace(/[^A-Za-z0-9_-]/g, "-");
    const customer = customerName === undefined ? "Marina Bay Product Launch" : String(customerName);
    const project = projectName === undefined ? "Orchard Road Pop-up Booth" : String(projectName);
    const response = await fetch("/api/quote-sessions", {
      method: "POST",
      headers,
      body: JSON.stringify({
        session_id: `${safePrefix}-${Date.now()}`,
        customer_summary: {
          customer_name: customer,
          project_name: project,
        },
        quote_company_profile: {
          id: "synthetic-fixture-default",
          display_name: "Demo Quote Company",
        },
        pricing_reference: {
          id: "synthetic-exhibition-fixture-pricing",
          display_name: "Synthetic Exhibition Fixture Pricing",
        },
        commercials: {
          currency: "SGD",
          tax_label: "GST",
          tax_rate: 0.09,
          subtotal: 1200,
          tax_amount: 108,
          grand_total: 1308,
        },
        status: {
          quote_generated: true,
        },
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(`Quote session fixture failed: ${response.status}`);
    return data.quote_session?.session_id || "";
  }, {
    suffix,
    sessionIdPrefix: options.sessionIdPrefix || "",
    customerName: Object.prototype.hasOwnProperty.call(options, "customerName") ? options.customerName : undefined,
    projectName: Object.prototype.hasOwnProperty.call(options, "projectName") ? options.projectName : undefined,
  });
}

async function verifyConcurrentInitialDraftSaveUsesSingleSession(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
  const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
  if (await emptyNewQuoteButton.isVisible()) {
    await emptyNewQuoteButton.click();
  } else {
    await page.locator("#newQuoteButton:not([disabled])").click();
  }
  await seedQuoteDraftFromTestFixture(page);
  const raceResult = await page.evaluate(async () => {
    setSidePanel("customer", { force: true });
    state.quoteSessionId = "";
    state.quoteSessionDraftSaveStarted = true;
    saveSessionState();
    const beforeData = await fetch("/api/quote-sessions").then((response) => response.json());
    const beforeIds = new Set((beforeData.quote_sessions || []).map((session) => session.session_id));
    const responses = await Promise.all([
      saveQuoteSessionDraftState({ quoteGenerated: false }),
      saveQuoteSessionDraftState({ quoteGenerated: false }),
    ]);
    const afterData = await fetch("/api/quote-sessions").then((response) => response.json());
    const afterIds = (afterData.quote_sessions || []).map((session) => session.session_id);
    const createdIds = afterIds.filter((sessionId) => !beforeIds.has(sessionId));
    await Promise.all(createdIds.map((sessionId) => deleteQuoteSessionRecord(sessionId)));
    window.localStorage.removeItem("swooshz_quote_session_v1");
    return {
      createdIds,
      responseIds: responses.map((session) => session?.session_id || ""),
      stateSessionId: state.quoteSessionId,
    };
  });
  const uniqueCreatedIds = new Set(raceResult.createdIds.filter(Boolean));
  const uniqueResponseIds = new Set(raceResult.responseIds.filter(Boolean));
  if (raceResult.createdIds.length !== 1 || uniqueCreatedIds.size !== 1 || uniqueResponseIds.size !== 1) {
    throw new Error(`Concurrent initial draft saves should create one session, found ${JSON.stringify(raceResult)}.`);
  }
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
}

async function verifyInitialDraftSaveReservesSessionIdBeforeNetwork(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
  const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
  if (await emptyNewQuoteButton.isVisible()) {
    await emptyNewQuoteButton.click();
  } else {
    await page.locator("#newQuoteButton:not([disabled])").click();
  }
  await seedQuoteDraftFromTestFixture(page);
  const reservation = await page.evaluate(async () => {
    setSidePanel("customer", { force: true });
    state.quoteSessionId = "";
    state.quoteSessionDraftSaveStarted = false;
    saveSessionState();
    const savePromise = startQuoteSessionDraftSaveAfterCustomerStep();
    const savedDuringStart = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
    const reservedSessionId = String(savedDuringStart.quoteSessionId || "");
    const stateSessionIdDuringStart = String(state.quoteSessionId || "");
    const session = await savePromise;
    const createdSessionId = session?.session_id || state.quoteSessionId || "";
    await deleteQuoteSessionRecord(createdSessionId);
    window.localStorage.removeItem("swooshz_quote_session_v1");
    return {
      reservedSessionId,
      stateSessionIdDuringStart,
      createdSessionId,
    };
  });
  if (!reservation.reservedSessionId || !reservation.stateSessionIdDuringStart) {
    throw new Error(`Initial draft save should reserve a client session id before the network response, found ${JSON.stringify(reservation)}.`);
  }
  if (reservation.createdSessionId !== reservation.reservedSessionId) {
    throw new Error(`Initial draft save should use the reserved session id, found ${JSON.stringify(reservation)}.`);
  }
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
}

async function verifyDashboardNewQuoteDoesNotSaveHiddenDraft(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
  const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
  if (await emptyNewQuoteButton.isVisible()) {
    await emptyNewQuoteButton.click();
  } else {
    await page.locator("#newQuoteButton:not([disabled])").click();
  }
  await seedQuoteDraftFromTestFixture(page);
  const beforeIds = await page.evaluate(async () => {
    const beforeData = await fetch("/api/quote-sessions").then((response) => response.json());
    const ids = (beforeData.quote_sessions || []).map((session) => session.session_id);
    setSidePanel("customer", { force: true });
    state.quoteSessionId = "";
    state.quoteSessionDraftSaveStarted = true;
    saveSessionState();
    showDashboard({ load: false });
    return ids;
  });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#newQuoteButton:not([disabled])").click();
  await seedQuoteDraftFromTestFixture(page);
  await page.locator("#sideNextButton", { hasText: "Next: Customer" }).click();
  await page.waitForFunction(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
      return Boolean(saved.quoteSessionId);
    } catch {
      return false;
    }
  }, null, { timeout: 15000 });
  await page.locator("#backToDashboardButton", { hasText: "Dashboard" }).click();
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  const result = await page.evaluate(async (beforeIds) => {
    const beforeSet = new Set(beforeIds);
    const afterData = await fetch("/api/quote-sessions").then((response) => response.json());
    const afterIds = (afterData.quote_sessions || []).map((session) => session.session_id);
    const createdIds = afterIds.filter((sessionId) => !beforeSet.has(sessionId));
    await Promise.all(createdIds.map((sessionId) => deleteQuoteSessionRecord(sessionId)));
    window.localStorage.removeItem("swooshz_quote_session_v1");
    return { createdIds, afterIds };
  }, beforeIds);
  if (result.createdIds.length !== 1) {
    throw new Error(`Dashboard New Quote should not save a hidden stale draft, found ${JSON.stringify(result)}.`);
  }
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
}

async function verifyDashboardClearsStaleSessionsBeforeRefresh(page) {
  await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
  await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.waitForFunction(() => {
    const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
    const emptyState = document.querySelector("#dashboardEmptyState");
    const sessionList = document.querySelector("#dashboardSessionsList");
    return !/Loading sessions/i.test(countText)
      && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
  }, null, { timeout: 15000 });
  const staleCount = await page.evaluate(() => {
    const staleSession = (suffix) => ({
      session_id: `quote-stale-${suffix}`,
      created_at: "2026-06-23T00:00:00Z",
      updated_at: "2026-06-23T00:00:00Z",
      customer_summary: { customer_name: "Stale Customer", project_name: `Stale Project ${suffix}` },
      status: { quote_generated: false, xlsx_exported: false, pdf_exported: false },
      exports: {},
      has_draft_state: true,
    });
    state.quoteSessionLoadError = "";
    state.dashboardStatusFilter = "all";
    state.dashboardSearch = "";
    state.dashboardPageIndex = 0;
    if (elements.dashboardStatusFilter) elements.dashboardStatusFilter.value = "all";
    if (elements.dashboardSearchInput) elements.dashboardSearchInput.value = "";
    state.quoteSessions = [staleSession("a"), staleSession("b")];
    state.dashboardActiveSessionId = "quote-stale-a";
    state.dashboardSelectedSessionIds = [];
    renderQuoteDashboard();
    return document.querySelectorAll(".dashboard-session-card").length;
  });
  if (staleCount !== 2) {
    throw new Error(`Expected two injected stale dashboard rows before refresh, found ${staleCount}.`);
  }
  let releaseDashboardFetch;
  const releaseFetch = new Promise((resolve) => {
    releaseDashboardFetch = resolve;
  });
  let resolveStarted;
  const started = new Promise((resolve) => {
    resolveStarted = resolve;
  });
  await page.route("**/api/quote-sessions", async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    resolveStarted();
    await releaseFetch;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ quote_sessions: [] }),
    });
  });
  await page.evaluate(() => showDashboard());
  await started;
  await page.waitForTimeout(100);
  const staleCountDuringRefresh = await page.locator(".dashboard-session-card").count();
  releaseDashboardFetch();
  await page.waitForFunction(() => {
    const emptyState = document.querySelector("#dashboardEmptyState");
    return emptyState && !emptyState.hidden;
  }, null, { timeout: 15000 });
  await page.unroute("**/api/quote-sessions");
  if (staleCountDuringRefresh !== 0) {
    throw new Error(`Dashboard should clear stale rows before refreshed sessions load, found ${staleCountDuringRefresh}.`);
  }
}
async function main() {
  let serverInfo = null;
  const hasExistingServer = await healthOk();
  if (!hasExistingServer) {
    await fs.rm(quoteDataRoot, { recursive: true, force: true });
    serverInfo = startServer();
    if (!(await waitForHealth())) {
      const serverOutput = serverInfo.output.join("").trim();
      await stopServer(serverInfo);
      throw new Error(`Could not start webapp at ${baseUrl}.${serverOutput ? `\n\n${serverOutput}` : ""}`);
    }
  }

  const browser = await chromium.launch({ headless: !options.headed });
  const context = await browser.newContext({ viewport: { width: 1365, height: 768 } });
  const page = await context.newPage();
  const consoleProblems = [];
  const networkProblems = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`);
  });
  page.on("pageerror", (error) => consoleProblems.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 400) networkProblems.push(`${response.status()} ${response.url()}`);
  });

  try {
    await installMockProfiles(page);
    await verifyBrowserRecoveryScopeIsolation(page);
    await verifyStaleTabMutationIsRejected(page);
    await verifyConcurrentInitialDraftSaveUsesSingleSession(page);
    await verifyInitialDraftSaveReservesSessionIdBeforeNetwork(page);
    await verifyDashboardNewQuoteDoesNotSaveHiddenDraft(page);
    await verifyDashboardClearsStaleSessionsBeforeRefresh(page);
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    const statusFilterLabels = await page.locator("#dashboardStatusFilter option").evaluateAll((options) => (
      options.map((option) => option.textContent?.trim())
    ));
    if (JSON.stringify(statusFilterLabels) !== JSON.stringify(["All", "Draft", "Draft Modified", "Generated"])) {
      throw new Error(`Unexpected dashboard status filters: ${JSON.stringify(statusFilterLabels)}`);
    }
    await expectTopbarPrimaryAction(page, "new-quote");
    await page.waitForFunction(() => {
      const countText = document.querySelector("#dashboardSessionCount")?.textContent || "";
      const emptyState = document.querySelector("#dashboardEmptyState");
      const sessionList = document.querySelector("#dashboardSessionsList");
      return !/Loading sessions/i.test(countText)
        && ((emptyState && !emptyState.hidden) || (sessionList && !sessionList.hidden));
    }, null, { timeout: 15000 });
    const dashboardShot = await screenshot(page, "dashboard.png");
    const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
    if (await emptyNewQuoteButton.isVisible()) {
      await emptyNewQuoteButton.click();
    } else {
      await page.locator("#newQuoteButton:not([disabled])").click();
    }
    await page.locator("#imageIntake").waitFor({ state: "visible" });
    await expectTopbarPrimaryAction(page, "dashboard");
    const homeShot = await screenshot(page, "home.png");

    await page.locator("#topbarBrandButton", { hasText: "Swooshz Quote Generator" }).click();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardEmptyState").waitFor({ state: "visible", timeout: 15000 });
    const blankDraftRows = await page.locator(".dashboard-session-card").count();
    if (blankDraftRows !== 0) {
      throw new Error(`Blank quote draft should be discarded on dashboard return, found ${blankDraftRows} dashboard rows.`);
    }
    await page.locator("#newQuoteButton:not([disabled])").click();
    await page.locator("#imageIntake").waitFor({ state: "visible" });
    await expectTopbarPrimaryAction(page, "dashboard");

    await seedQuoteDraftFromTestFixture(page);
    const preCustomerQuoteSessionId = await currentQuoteSessionId(page);
    if (preCustomerQuoteSessionId) {
      throw new Error(`Expected dashboard draft saving to wait until Next: Customer, found ${preCustomerQuoteSessionId}.`);
    }
    await page.setViewportSize({ width: 520, height: 720 });
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForFunction(() => window.scrollY > 40, null, { timeout: 15000 });
    await page.locator("#sideNextButton", { hasText: "Next: Customer" }).click();
    await page.locator("#customerDetailsPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.waitForTimeout(50);
    const postNextScrollY = await page.evaluate(() => window.scrollY);
    if (postNextScrollY > 2) {
      throw new Error(`Next: Customer should reset the page scroll to the top, found scrollY=${postNextScrollY}.`);
    }
    await page.setViewportSize({ width: 1365, height: 768 });
    if (!(await page.locator("#showName").inputValue()).trim()) {
      await page.locator("#showName").fill("Synthetic Expo");
    }
    await page.waitForFunction(() => {
      try {
        const saved = JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}");
        return Boolean(saved.quoteSessionId);
      } catch {
        return false;
      }
    }, null, { timeout: 15000 });
    await page.locator('.rail-button[data-side-panel="quote_company"]:not([disabled])').waitFor({ timeout: 15000 });
    await page.locator('.rail-button[data-side-panel="quote_company"]').click();
    await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
    const seededPresetValue = await page.locator("#presetSelect").inputValue();
    if (seededPresetValue !== "profile:synthetic-fixture-default") {
      throw new Error(`Expected seeded setup to select the synthetic fixture preset, found ${seededPresetValue}.`);
    }
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#quoteCompanyPanel.is-active").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "dashboard");
    const refreshedActiveRailTexts = await page.locator(".rail-button.is-active").evaluateAll((buttons) => (
      buttons.map((button) => button.textContent?.trim() || "")
    ));
    if (refreshedActiveRailTexts.length !== 1 || refreshedActiveRailTexts[0] !== "Quote Company") {
      throw new Error(`Expected refresh to restore the last quote menu, found ${JSON.stringify(refreshedActiveRailTexts)}.`);
    }
    const restoredQuoteSessionId = await currentQuoteSessionId(page);
    if (!restoredQuoteSessionId) {
      throw new Error("Expected refresh recovery to keep the current quote session id.");
    }
    await page.locator("#backToDashboardButton", { hasText: "Dashboard" }).click();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "new-quote");
    await page.locator("#dashboardLoadingModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.evaluate((storageKey) => {
      const saved = JSON.parse(window.localStorage.getItem(storageKey) || "{}");
      saved.quoteSessionId = "quote-unrelated-regression";
      saved.activeAppView = "dashboard";
      window.localStorage.setItem(storageKey, JSON.stringify(saved));
    }, "swooshz_quote_session_v1");
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    const unrelatedLocalQuoteSessionId = await currentQuoteSessionId(page);
    if (unrelatedLocalQuoteSessionId === restoredQuoteSessionId) {
      throw new Error(`Expected Modify quote regression to use a non-current browser draft, found ${unrelatedLocalQuoteSessionId}.`);
    }
    await page.locator(`.dashboard-session-card[data-quote-session-id="${restoredQuoteSessionId}"]`).click();
    await page.locator('[data-dashboard-panel-action="modify-session"]', { hasText: "Modify quote" }).waitFor({ timeout: 15000 });
    const restoredDetailBeforeModify = await dashboardQuoteSessionDetail(page, restoredQuoteSessionId);
    const restoredUpdatedAtBeforeModify = restoredDetailBeforeModify.quote_session?.updated_at || "";
    if (!restoredUpdatedAtBeforeModify) {
      throw new Error("Expected dashboard session detail to expose updated_at before Modify quote.");
    }
    await page.locator('[data-dashboard-panel-action="modify-session"]', { hasText: "Modify quote" }).click();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "dashboard");
    const modifiedQuoteSessionId = await currentQuoteSessionId(page);
    if (modifiedQuoteSessionId !== restoredQuoteSessionId) {
      throw new Error(`Expected Modify quote to restore saved dashboard session ${restoredQuoteSessionId}, found ${modifiedQuoteSessionId}.`);
    }
    const restoredActiveRailTexts = await page.locator(".rail-button.is-active").evaluateAll((buttons) => (
      buttons.map((button) => button.textContent?.trim() || "")
    ));
    if (restoredActiveRailTexts.length !== 1 || !["Upload", "Customer", "Quote Company"].includes(restoredActiveRailTexts[0])) {
      throw new Error(`Expected Modify quote to restore a usable quote panel, found ${JSON.stringify(restoredActiveRailTexts)}.`);
    }
    const restoredFiles = await page.locator("#fileList .file-item").evaluateAll((items) => (
      items.map((item) => item.textContent?.trim() || "")
    ));
    if (restoredFiles.length !== 1 || !restoredFiles[0].includes(TEST_REFERENCE_FILE_NAME)) {
      throw new Error(`Expected refresh to preserve the seeded PDF reference, found ${JSON.stringify(restoredFiles)}.`);
    }
    await page.locator("#backToDashboardButton", { hasText: "Dashboard" }).click();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "new-quote");
    const restoredCardAfterCleanReturn = page.locator(`.dashboard-session-card[data-quote-session-id="${restoredQuoteSessionId}"]`);
    await restoredCardAfterCleanReturn.waitFor({ state: "visible", timeout: 15000 });
    const restoredDetailAfterCleanReturn = await dashboardQuoteSessionDetail(page, restoredQuoteSessionId);
    const restoredUpdatedAtAfterCleanReturn = restoredDetailAfterCleanReturn.quote_session?.updated_at || "";
    if (restoredUpdatedAtAfterCleanReturn !== restoredUpdatedAtBeforeModify) {
      throw new Error(`Modify -> Dashboard without edits should not rewrite or reorder the session: ${restoredUpdatedAtBeforeModify} -> ${restoredUpdatedAtAfterCleanReturn}.`);
    }
    await restoredCardAfterCleanReturn.click();
    await page.locator('[data-dashboard-panel-action="modify-session"]', { hasText: "Modify quote" }).waitFor({ timeout: 15000 });
    await page.locator('[data-dashboard-panel-action="modify-session"]', { hasText: "Modify quote" }).click();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "dashboard");
    await page.locator('.rail-button[data-side-panel="quote_company"]:not([disabled])').waitFor({ timeout: 15000 });
    await page.locator('.rail-button[data-side-panel="quote_company"]').click();
    await page.locator("#quoteCompanyPanel").waitFor({ state: "visible", timeout: 15000 });
    const restoredPresetValue = await page.locator("#presetSelect").inputValue();
    if (restoredPresetValue !== "profile:synthetic-fixture-default") {
      throw new Error(`Expected refresh to preserve company preset, found ${restoredPresetValue}.`);
    }
    const presetSelectBox = await page.locator("#presetSelect").boundingBox();
    if (!presetSelectBox || presetSelectBox.width < 200) {
      throw new Error("Company preset dropdown is unexpectedly narrow.");
    }
    await page.locator('.rail-button[data-side-panel="customer"]:not([disabled])').waitFor({ timeout: 15000 });
    await page.locator('.rail-button[data-side-panel="customer"]').click();
    await page.mouse.move(4, 4);
    await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
    await page.locator(".workspace-pane-scroll").evaluate((element) => {
      element.scrollTop = 0;
    });
    const pricingSummary = await page.locator("#selectedPricingReferenceSummary").innerText();
    if (!pricingSummary.includes("Managed in Settings")) {
      throw new Error(`Unexpected pricing reference summary: ${pricingSummary}`);
    }
    const quoteCurrencyValue = await page.locator("#quoteCurrency").inputValue();
    const quoteTaxValue = await page.locator("#quoteTaxLabel").inputValue();
    const quoteRateValue = await page.locator("#quoteTaxRate").inputValue();
    const quoteExchangeRateValue = await page.locator("#quoteExchangeRate").inputValue();
    if (!quoteCurrencyValue) {
      throw new Error("Quote currency field did not have a selected value.");
    }
    if (!/GST|VAT/i.test(quoteTaxValue)) {
      throw new Error(`Unexpected quote tax value: ${quoteTaxValue}`);
    }
    if (!quoteRateValue) {
      throw new Error("Quote tax rate field did not have a value.");
    }
    if (quoteExchangeRateValue !== "1") {
      throw new Error(`Expected same-currency exchange rate to default to 1, found ${quoteExchangeRateValue}.`);
    }
    const currencyBox = await page.locator("#quoteCurrency").boundingBox();
    const exchangeRateBox = await page.locator("#quoteExchangeRate").boundingBox();
    const taxBox = await page.locator("#quoteTaxLabel").boundingBox();
    const taxRateBox = await page.locator("#quoteTaxRate").boundingBox();
    if (!currencyBox || !exchangeRateBox || !taxBox || !taxRateBox) {
      throw new Error("Quote commercial fields were not measurable.");
    }
    if (Math.abs(currencyBox.y - exchangeRateBox.y) > 8 || Math.abs(taxBox.y - taxRateBox.y) > 8 || taxBox.y <= currencyBox.y) {
      throw new Error(`Quote commercial fields are not in the expected 2x2 order: ${JSON.stringify({ currencyBox, exchangeRateBox, taxBox, taxRateBox })}`);
    }
    const selectedPricingValue = await page.locator("#profileSelect").inputValue();
    if (!selectedPricingValue) {
      throw new Error("Pricing reference select did not have a selected value.");
    }
    const profileSelectBox = await page.locator("#profileSelect").boundingBox();
    if (!profileSelectBox || profileSelectBox.width < 200) {
      throw new Error("Pricing reference dropdown is unexpectedly narrow.");
    }
    if (presetSelectBox.width > profileSelectBox.width) {
      throw new Error(`Company preset dropdown width ${presetSelectBox.width} should stay no wider than pricing reference dropdown width ${profileSelectBox.width}.`);
    }
    const customerPricingShot = await screenshot(page, "customer-pricing.png");
    await page.locator("#settingsButton").click();
    await page.locator("#pricingReferenceModal").waitFor({ state: "visible" });
    await page.getByRole("heading", { name: "Pricing Reference Settings" }).waitFor();
    await page.keyboard.press("Escape");
    await page.locator("#pricingReferenceModal").waitFor({ state: "hidden" });
    await page.locator("#quoteDate").waitFor({ state: "visible" });
    const dateBoldButton = page.locator('[data-date-format-command="bold"]');
    await page.locator("#quoteDate").focus();
    await dateBoldButton.waitFor({ state: "visible" });
    await dateBoldButton.click();
    if ((await dateBoldButton.getAttribute("aria-pressed")) !== "true") {
      throw new Error("Quote date bold formatting did not toggle on.");
    }
    await page.mouse.move(4, 4);
    const activeRailTexts = await page.locator(".rail-button.is-active").evaluateAll((buttons) => (
      buttons.map((button) => button.textContent?.trim() || "")
    ));
    if (activeRailTexts.length !== 1 || activeRailTexts[0] !== "Customer") {
      throw new Error(`Expected only Customer rail item to be active, found ${JSON.stringify(activeRailTexts)}.`);
    }
    const customerShot = await screenshot(page, "customer.png");

    const footerBox = await page.locator(".workspace-pane-footer").boundingBox();
    const viewport = page.viewportSize();
    if (!footerBox || !viewport || footerBox.y + footerBox.height > viewport.height + 1) {
      throw new Error("Workspace footer is not inside the current viewport.");
    }

    await page.setViewportSize({ width: 520, height: 720 });
    await page.evaluate(() => window.scrollTo(0, 0));
    const mobileScrollExtent = await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
    if (mobileScrollExtent < 80) {
      throw new Error(`Mobile layout did not create enough page scroll distance: ${mobileScrollExtent}.`);
    }
    const scrollPaneBox = await page.locator(".workspace-pane-scroll").boundingBox();
    if (!scrollPaneBox) {
      throw new Error("Workspace scroll pane was not visible in mobile layout.");
    }
    const mobileScrollBefore = await page.evaluate(() => window.scrollY);
    const paneScrollBefore = await page.locator(".workspace-pane-scroll").evaluate((element) => element.scrollTop);
    await page.mouse.move(scrollPaneBox.x + scrollPaneBox.width / 2, scrollPaneBox.y + Math.min(scrollPaneBox.height / 2, 260));
    await page.mouse.wheel(0, 520);
    await page.waitForTimeout(100);
    let mobileScrollAfter = await page.evaluate(() => window.scrollY);
    let paneScrollAfter = await page.locator(".workspace-pane-scroll").evaluate((element) => element.scrollTop);
    if (mobileScrollAfter <= mobileScrollBefore + 10 && paneScrollAfter <= paneScrollBefore + 10) {
      await page.evaluate(() => {
        if (document.activeElement instanceof HTMLElement) {
          document.activeElement.blur();
        }
        document.body.focus();
      });
      await page.keyboard.press("PageDown");
      await page.waitForTimeout(100);
      mobileScrollAfter = await page.evaluate(() => window.scrollY);
      paneScrollAfter = await page.locator(".workspace-pane-scroll").evaluate((element) => element.scrollTop);
    }
    if (mobileScrollAfter <= mobileScrollBefore + 10 && paneScrollAfter <= paneScrollBefore + 10) {
      throw new Error(`Mobile layout did not scroll from wheel or keyboard input: page ${mobileScrollBefore} -> ${mobileScrollAfter}, pane ${paneScrollBefore} -> ${paneScrollAfter}.`);
    }

    const bodyText = await page.locator("body").innerText();
    if (!bodyText.includes("Upload") || !bodyText.includes("Customer") || !bodyText.includes("Quote date")) {
      throw new Error("Rendered page did not include the expected workspace text.");
    }

    await page.setViewportSize({ width: 1365, height: 768 });
    await page.locator("#backToDashboardButton", { hasText: "Dashboard" }).waitFor({ state: "visible", timeout: 15000 });
    const currentDashboardSessionId = await currentQuoteSessionId(page);
    if (!currentDashboardSessionId) {
      throw new Error("Expected the current quote session id before returning to the dashboard.");
    }
    await page.locator("#backToDashboardButton").click();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible", timeout: 15000 });
    await expectTopbarPrimaryAction(page, "new-quote");
    await page.locator("#quoteDashboardPanel").evaluate((element) => {
      element.scrollTop = 0;
    });
    const currentDashboardCard = page.locator(`.dashboard-session-card[data-quote-session-id="${currentDashboardSessionId}"]`);
    await currentDashboardCard.waitFor({ state: "visible", timeout: 15000 });
    const savedStepPills = await currentDashboardCard.locator(".dashboard-status-pill.is-progress").allTextContents();
    if (!savedStepPills.some((text) => /Saved at Customer/i.test(text))) {
      throw new Error(`Expected dashboard card to show saved step pill for the current draft, found ${JSON.stringify(savedStepPills)}.`);
    }
    const modifiedDateMetrics = await currentDashboardCard.locator(".dashboard-session-meta-zone div").nth(1).locator("dd").evaluate((element) => {
      const range = document.createRange();
      range.selectNodeContents(element);
      const lineRects = Array.from(range.getClientRects()).filter((rect) => rect.width > 1 && rect.height > 1);
      range.detach();
      return {
        text: element.textContent?.trim() || "",
        lines: lineRects.length,
        height: Math.round(element.getBoundingClientRect().height),
        lineHeight: Number.parseFloat(window.getComputedStyle(element).lineHeight) || 0,
      };
    });
    if (modifiedDateMetrics.lines > 1 || modifiedDateMetrics.height > Math.ceil(modifiedDateMetrics.lineHeight * 1.35)) {
      throw new Error(`Dashboard modified timestamp should stay on one line, found ${JSON.stringify(modifiedDateMetrics)}.`);
    }
    const currentDashboardDetail = await dashboardQuoteSessionDetail(page, currentDashboardSessionId);
    const currentDraftState = currentDashboardDetail.quote_session?.draft_state || {};
    if (!Array.isArray(currentDraftState.images) || !currentDraftState.images.some((image) => image.name === TEST_REFERENCE_FILE_NAME)) {
      throw new Error(`Expected dashboard session draft state to include the reference PDF metadata, found ${JSON.stringify(currentDraftState.images || [])}.`);
    }
    if (JSON.stringify(currentDraftState).includes("data:application/pdf")) {
      throw new Error("Dashboard session draft state should not store raw PDF data URLs.");
    }
    await currentDashboardCard.click();
    await page.keyboard.press("Delete");
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    const keyboardSingleDeleteTitle = await page.locator("#quoteSessionDeleteTitle").innerText();
    if (keyboardSingleDeleteTitle !== "Delete quote session?") {
      throw new Error(`Unexpected keyboard single delete confirmation title: ${keyboardSingleDeleteTitle}`);
    }
    const keyboardSingleDeleteCopy = await page.locator("#quoteSessionDeleteText").innerText();
    if (keyboardSingleDeleteCopy !== "This removes the local dashboard record and any saved local exports for this quote session. This cannot be undone.") {
      throw new Error(`Unexpected keyboard single delete confirmation copy: ${keyboardSingleDeleteCopy}`);
    }
    await page.locator("#cancelQuoteSessionDeleteButton").click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator("#dashboardSelectedSessionPanel").waitFor({ state: "visible", timeout: 15000 });
    const normalModeCheckboxVisible = await currentDashboardCard.locator(".dashboard-session-select-control").isVisible();
    if (normalModeCheckboxVisible) {
      throw new Error("Row checkbox should stay hidden until Select mode is enabled.");
    }
    const singlePanelActionMetrics = await dashboardPanelActionMetrics(page, "single selection");
    const dashboardSingleSelectedShot = await screenshot(page, "dashboard-single-selected.png");
    await createDashboardSmokeSession(page, "reference-c", {
      sessionIdPrefix: "quote-4f-search-row",
      customerName: "Internal Test Exhibition Booth",
      projectName: "Four Foxtrot Smoke Search",
    });
    await page.getByRole("button", { name: "Clear selected session", exact: true }).click();
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    await page.locator("#dashboardSearchInput").fill("Four Foxtrot Smoke Search");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const characterSearchRows = await page.locator(".dashboard-session-card").count();
    if (characterSearchRows !== 1) {
      throw new Error(`Expected search for Four Foxtrot Smoke Search to match only the REF QUOTE-4F row, found ${characterSearchRows}.`);
    }
    const characterSearchText = await page.locator(".dashboard-session-card").first().innerText();
    if (!characterSearchText.includes("REF QUOTE-4F")) {
      throw new Error(`Search for Four Foxtrot Smoke Search did not return the visible quote reference row: ${characterSearchText}`);
    }
    await page.locator("#dashboardSearchInput").fill("");
    await createDashboardSmokeSession(page, "alpha", { sessionIdPrefix: "quote-7a-playwright-alpha" });
    await createDashboardSmokeSession(page, "beta", { sessionIdPrefix: "quote-2c-hidden-3" });
    await createDashboardSmokeSession(page, "gamma", { sessionIdPrefix: "quote-bulk-extra-1" });
    await createDashboardSmokeSession(page, "delta", { sessionIdPrefix: "quote-bulk-extra-2" });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    await page.locator("#dashboardSearchInput").fill("7a");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const referenceSearchTexts = await page.locator(".dashboard-session-card").evaluateAll((cards) => (
      cards.map((card) => card.innerText || "")
    ));
    if (!referenceSearchTexts.some((text) => text.includes("REF QUOTE-7A"))) {
      throw new Error(`Search for 7a did not return the visible quote reference row among ${referenceSearchTexts.length} visible cards.`);
    }
    await page.locator("#dashboardSearchInput").fill("7a");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const digitSearchTexts = await page.locator(".dashboard-session-card").evaluateAll((cards) => (
      cards.map((card) => card.innerText || "")
    ));
    if (!digitSearchTexts.some((text) => text.includes("REF QUOTE-7A"))) {
      throw new Error(`Repeated search for 7a did not return the visible quote reference row among ${digitSearchTexts.length} visible cards.`);
    }
    await page.locator("#dashboardSearchInput").fill("");
    await createDashboardSmokeSession(page, "untitled-visible", {
      sessionIdPrefix: "quote-2c-untitled-visible",
      customerName: "",
      projectName: "",
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Quote List" }).waitFor();
    await page.locator("#dashboardSearchInput").fill("untitled customer");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const untitledCustomerTexts = await page.locator(".dashboard-session-card").evaluateAll((cards) => (
      cards.map((card) => card.innerText || "")
    ));
    if (!untitledCustomerTexts.some((text) => text.includes("Untitled customer") && text.includes("REF QUOTE-2C"))) {
      throw new Error(`Search for Untitled customer did not return the visible REF QUOTE-2C fallback row: ${JSON.stringify(untitledCustomerTexts)}`);
    }
    await page.locator("#dashboardSearchInput").fill("untitled quote");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const untitledProjectTexts = await page.locator(".dashboard-session-card").evaluateAll((cards) => (
      cards.map((card) => card.innerText || "")
    ));
    if (!untitledProjectTexts.some((text) => text.includes("Untitled quote") && text.includes("REF QUOTE-2C"))) {
      throw new Error(`Search for Untitled quote did not return the visible REF QUOTE-2C fallback row: ${JSON.stringify(untitledProjectTexts)}`);
    }
    await page.locator("#dashboardSearchInput").fill("Marina Bay Product Launch");
    await page.locator(".dashboard-session-card").first().waitFor({ state: "visible", timeout: 15000 });
    const visibleBulkRows = await page.locator(".dashboard-session-card").count();
    if (visibleBulkRows < 2) {
      throw new Error(`Expected at least two bulk smoke rows, found ${visibleBulkRows}.`);
    }
    const filteredFirstCard = page.locator(".dashboard-session-card").first();
    const selectModeButton = page.locator("#dashboardSelectModeButton", { hasText: "Select" });
    const selectModeBox = await selectModeButton.boundingBox();
    if (!selectModeBox || selectModeBox.height > 40 || selectModeBox.width > 100) {
      throw new Error(`Select mode control is too large: ${JSON.stringify(selectModeBox)}.`);
    }
    await selectModeButton.click();
    await page.locator("#dashboardSelectModeButton", { hasText: "Select all visible" }).waitFor({ state: "visible", timeout: 15000 });
    await filteredFirstCard.locator(".dashboard-session-select-control").waitFor({ state: "visible", timeout: 15000 });
    const firstCardTopBeforeBulk = await filteredFirstCard.evaluate((element) => element.getBoundingClientRect().top);
    await filteredFirstCard.click();
    const firstCardTopAfterBulk = await filteredFirstCard.evaluate((element) => element.getBoundingClientRect().top);
    if (Math.abs(firstCardTopAfterBulk - firstCardTopBeforeBulk) > 12) {
      throw new Error(`Bulk panel selection shifted the session list: ${firstCardTopBeforeBulk} -> ${firstCardTopAfterBulk}.`);
    }
    const rowCheckboxBox = await filteredFirstCard.locator("[data-dashboard-select]").boundingBox();
    if (!rowCheckboxBox || rowCheckboxBox.width > 18 || rowCheckboxBox.height > 18) {
      throw new Error(`Row checkbox is too large: ${JSON.stringify(rowCheckboxBox)}.`);
    }
    await page.locator(".dashboard-session-card.is-selected").first().waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardSelectedSessionPanel", { hasText: "SELECTED SESSION" }).waitFor({ state: "visible", timeout: 15000 });
    const selectAllButton = page.locator("#dashboardSelectModeButton", { hasText: "Select all visible" });
    const selectAllBox = await selectAllButton.boundingBox();
    if (!selectAllBox || selectAllBox.height > 40 || selectAllBox.width > 180) {
      throw new Error(`Select all control is too large: ${JSON.stringify(selectAllBox)}.`);
    }
    await selectAllButton.click();
    await page.locator(".dashboard-bulk-selection-summary", { hasText: `${visibleBulkRows} quote sessions selected` }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardSelectedSessionPanel", { hasText: `${visibleBulkRows} quote sessions selected` }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardSelectedSessionPanel", { hasText: "Bulk selection" }).waitFor({ state: "visible", timeout: 15000 });
    const bulkPanelActionMetrics = await dashboardPanelActionMetrics(page, "bulk selection");
    if (Math.abs(bulkPanelActionMetrics.bottomGap - singlePanelActionMetrics.bottomGap) > 8) {
      throw new Error(`Dashboard action footer bottom moved between single and bulk states: ${singlePanelActionMetrics.bottomGap}px -> ${bulkPanelActionMetrics.bottomGap}px.`);
    }
    if (Math.abs(bulkPanelActionMetrics.actionX - singlePanelActionMetrics.actionX) > 4) {
      throw new Error(`Dashboard action footer x offset moved between single and bulk states: ${singlePanelActionMetrics.actionX}px -> ${bulkPanelActionMetrics.actionX}px.`);
    }
    if (Math.abs(bulkPanelActionMetrics.actionWidth - singlePanelActionMetrics.actionWidth) > 4) {
      throw new Error(`Dashboard action footer width changed between single and bulk states: ${singlePanelActionMetrics.actionWidth}px -> ${bulkPanelActionMetrics.actionWidth}px.`);
    }
    if (Math.abs(bulkPanelActionMetrics.deleteClearGap - singlePanelActionMetrics.deleteClearGap) > 4) {
      throw new Error(`Dashboard delete/clear spacing changed between single and bulk states: ${singlePanelActionMetrics.deleteClearGap}px -> ${bulkPanelActionMetrics.deleteClearGap}px.`);
    }
    await page.setViewportSize({ width: 520, height: 720 });
    const mobileBulkPanelActionMetrics = await dashboardPanelActionMetrics(page, "mobile bulk selection");
    if (mobileBulkPanelActionMetrics.bottomGap < 8 || mobileBulkPanelActionMetrics.bottomGap > 36) {
      throw new Error(`Mobile bulk action footer is not bottom anchored: ${mobileBulkPanelActionMetrics.bottomGap}px gap.`);
    }
    const dashboardSelectedMobileShot = await screenshot(page, "dashboard-selected-mobile.png");
    await page.setViewportSize({ width: 1365, height: 768 });
    await page.locator("#dashboardSelectedSessionPanel", { hasText: "Bulk selection" }).waitFor({ state: "visible", timeout: 15000 });
    const bulkExtraBlocks = await page.locator(".dashboard-bulk-breakdown, .dashboard-bulk-value-card").count();
    if (bulkExtraBlocks !== 0) {
      throw new Error("Bulk panel should not show status breakdown or combined value blocks.");
    }
    const bulkPanelText = await page.locator("#dashboardSelectedSessionPanel").innerText();
    if (bulkPanelText.includes("Status breakdown") || bulkPanelText.includes("Combined Value")) {
      throw new Error("Bulk panel copy still includes removed status or combined value sections.");
    }
    const selectAllChecked = await page.locator("#dashboardSelectModeButton").evaluate((button) => button.classList.contains("is-all-selected"));
    if (!selectAllChecked) {
      throw new Error("Select all control did not enter the checked visual state after selecting all visible rows.");
    }
    await selectAllButton.click();
    const selectedAfterDeselectAll = await page.locator(".dashboard-session-card.is-selected").count();
    if (selectedAfterDeselectAll !== 0) {
      throw new Error(`Select all toggle did not deselect visible rows: ${selectedAfterDeselectAll} remain selected.`);
    }
    await page.locator("#dashboardSelectedSessionPanel").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator("#dashboardSelectModeButton", { hasText: "Select" }).waitFor({ state: "visible", timeout: 15000 });
    const returnedToNoneMode = await page.locator("#dashboardSelectModeButton").evaluate((button) => (
      !button.classList.contains("is-all-selected")
        && !button.classList.contains("is-selecting")
        && button.getAttribute("aria-pressed") === "false"
    ));
    if (!returnedToNoneMode) {
      throw new Error("Select all toggle did not return the control to the original Select mode.");
    }
    const rowCheckboxVisibleAfterDeselectAll = await filteredFirstCard.locator(".dashboard-session-select-control").isVisible();
    if (rowCheckboxVisibleAfterDeselectAll) {
      throw new Error("Row checkboxes stayed visible after Select all visible was toggled off.");
    }
    await page.locator("#dashboardSelectModeButton", { hasText: "Select" }).click();
    await page.locator("#dashboardSelectModeButton", { hasText: "Select all visible" }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#dashboardSelectModeButton", { hasText: "Select all visible" }).click();
    await page.locator(".dashboard-bulk-selection-summary", { hasText: `${visibleBulkRows} quote sessions selected` }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#quoteDashboardPanel").evaluate((panel) => {
      panel.scrollTop = 0;
    });
    const dashboardSelectedShot = await screenshot(page, "dashboard-selected.png");
    await page.keyboard.press("Delete");
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    const keyboardDeleteTitle = await page.locator("#quoteSessionDeleteTitle").innerText();
    if (keyboardDeleteTitle !== "Delete selected quote sessions?") {
      throw new Error(`Unexpected keyboard bulk delete confirmation title: ${keyboardDeleteTitle}`);
    }
    await page.locator("#cancelQuoteSessionDeleteButton").click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator('[data-dashboard-panel-action="delete-selected"]', { hasText: "Delete selected" }).click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    const dashboardDeleteModalShot = await screenshot(page, "dashboard-delete-modal.png");
    const bulkDeleteTitle = await page.locator("#quoteSessionDeleteTitle").innerText();
    if (bulkDeleteTitle !== "Delete selected quote sessions?") {
      throw new Error(`Unexpected bulk delete confirmation title: ${bulkDeleteTitle}`);
    }
    const bulkDeleteCopy = await page.locator("#quoteSessionDeleteText").innerText();
    if (bulkDeleteCopy !== "This removes the selected local dashboard records and any saved local exports for those quote sessions. This cannot be undone.") {
      throw new Error(`Unexpected bulk delete confirmation copy: ${bulkDeleteCopy}`);
    }
    await page.locator("#cancelQuoteSessionDeleteButton").click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "hidden", timeout: 15000 });
    await page.locator('[data-dashboard-panel-action="delete-selected"]', { hasText: "Delete selected" }).click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    await page.locator("#confirmQuoteSessionDeleteButton").click();
    await page.waitForFunction(() => document.querySelectorAll(".dashboard-session-card").length === 0, null, { timeout: 15000 });
    await page.locator("#dashboardSearchInput").fill("");
    await currentDashboardCard.waitFor({ state: "visible", timeout: 15000 });
    await currentDashboardCard.click();
    await page.locator('[data-dashboard-panel-action="delete-session"]', { hasText: "Delete session" }).click();
    await page.locator("#quoteSessionDeleteModal").waitFor({ state: "visible", timeout: 15000 });
    await expectQuoteSessionDeleteButtonFocused(page);
    const singleDeleteCopy = await page.locator("#quoteSessionDeleteText").innerText();
    if (singleDeleteCopy !== "This removes the local dashboard record and any saved local exports for this quote session. This cannot be undone.") {
      throw new Error(`Unexpected single delete confirmation copy: ${singleDeleteCopy}`);
    }
    await page.keyboard.press("Enter");
    await currentDashboardCard.waitFor({ state: "detached", timeout: 15000 });
    await page.locator("#newQuoteButton:not([disabled])").click();
    await page.locator("#imageIntake").waitFor({ state: "visible", timeout: 15000 });
    await verifyMobileHeaderOrder(page);
    await verifyMobileBasisLegendAndOutputCards(page);

    console.log(JSON.stringify({
      status: "ok",
      url: page.url(),
      screenshots: [
        dashboardShot,
        homeShot,
        customerPricingShot,
        customerShot,
        dashboardSingleSelectedShot,
        dashboardSelectedShot,
        dashboardSelectedMobileShot,
        dashboardDeleteModalShot,
      ].filter(Boolean),
      consoleProblems,
      networkProblems,
    }, null, 2));
  } finally {
    await browser.close();
    await stopServer(serverInfo);
  }
}

main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});
