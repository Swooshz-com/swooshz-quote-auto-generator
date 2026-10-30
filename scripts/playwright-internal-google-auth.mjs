import { spawn } from "node:child_process";
import { chromium, request } from "playwright";

const python = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || "python";
const harness = spawn(python, ["tests/internal_google_browser_harness.py"], {
  cwd: process.cwd(),
  env: { ...process.env, PYTHONUNBUFFERED: "1" },
  stdio: ["ignore", "pipe", "pipe"],
});

let stderr = "";
harness.stderr.on("data", (chunk) => {
  stderr += chunk.toString();
});

const baseUrl = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("Synthetic auth harness did not start.")), 10000);
  harness.stdout.once("data", (chunk) => {
    clearTimeout(timer);
    resolve(chunk.toString().trim().split(/\r?\n/, 1)[0]);
  });
  harness.once("exit", (code) => {
    clearTimeout(timer);
    reject(new Error(`Synthetic auth harness exited early (${code}). ${stderr}`));
  });
});

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
const anonymousApi = await request.newContext();
let authenticatedApi;
const consoleProblems = [];
let expectedDocumentAuthFailures = 0;
page.on("console", (message) => {
  if (["error", "warning"].includes(message.type())) {
    const location = message.location().url;
    if (message.text() === "Failed to load resource: the server responded with a status of 401 (Unauthorized)"
        && location === baseUrl + "/") {
      expectedDocumentAuthFailures++;
      return;
    }
    consoleProblems.push(location ? `${message.text()} (${location})` : message.text());
  }
});
page.on("pageerror", (error) => consoleProblems.push(error.message));
await page.route("**/api/quote-sessions", async (route) => {
  if (route.request().method() === "GET") {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ quote_sessions: [] }),
    });
    return;
  }
  await route.fallback();
});
await page.route("**/api/log", (route) => route.fulfill({ status: 204, body: "" }));

try {
  const denied = await anonymousApi.get(`${baseUrl}/api/session`);
  if (denied.status() !== 401) throw new Error(`Expected unauthenticated 401, got ${denied.status()}.`);

  for (const claimCase of [
    "unknown-sub",
    "same-email-different-sub",
    "same-sub-different-email",
  ]) {
    const selected = await anonymousApi.get(
      `${baseUrl}/__synthetic_oidc/case?value=${encodeURIComponent(claimCase)}`,
    );
    if (selected.status() !== 200) throw new Error(`Could not select ${claimCase}.`);
    const deniedContext = await browser.newContext();
    const deniedPage = await deniedContext.newPage();
    const deniedResponse = await deniedPage.goto(`${baseUrl}/login`, {
      waitUntil: "domcontentloaded",
      timeout: 15000,
    });
    if (!deniedResponse || deniedResponse.status() !== 403) {
      throw new Error(`${claimCase} was not rejected before session creation.`);
    }
    const cookies = await deniedContext.cookies(baseUrl);
    if (cookies.some((cookie) => cookie.name === "swooshz_quote_session")) {
      throw new Error(`${claimCase} received a session cookie.`);
    }
    const protectedResponse = await deniedContext.request.get(`${baseUrl}/api/session`);
    if (protectedResponse.status() !== 401) {
      throw new Error(`${claimCase} reached a protected API.`);
    }
    await deniedContext.close();
  }
  const approvedCase = await anonymousApi.get(
    `${baseUrl}/__synthetic_oidc/case?value=approved`,
  );
  if (approvedCase.status() !== 200) throw new Error("Could not restore approved identity.");

  await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded", timeout: 15000 });
  if (new URL(page.url()).origin !== new URL(baseUrl).origin) {
    throw new Error("Synthetic OIDC flow escaped the local harness.");
  }

  const session = await page.evaluate(async () => {
    const response = await fetch("/api/session");
    return { status: response.status, body: await response.json() };
  });
  if (session.status !== 200 || !session.body.authenticated) {
    throw new Error("Synthetic exact-allowlist authentication did not create a session.");
  }
  if (session.body.user?.subject !== "synthetic-browser-subject") {
    throw new Error("Stable synthetic subject was not the primary session identity.");
  }

  authenticatedApi = await request.newContext({
    storageState: await page.context().storageState(),
  });
  const unsafeLogout = await authenticatedApi.get(`${baseUrl}/logout`);
  if (unsafeLogout.status() !== 405) throw new Error("GET logout was not rejected.");

  await page.waitForFunction(() => typeof state !== "undefined" && !state.isBooting);
  const logoutResponse = page.waitForResponse(response =>
    new URL(response.url()).pathname === "/logout" && response.request().method() === "POST");
  await page.locator("#topbarLogoutLink").click();
  const logout = await logoutResponse;
  if (logout.status() !== 204 || logout.headers()["x-sqag-logout-location"] !== "/signed-out") {
    throw new Error("CSRF-safe logout did not revoke the local session.");
  }
  await page.waitForURL(baseUrl + "/signed-out");
  if ((await page.context().cookies(baseUrl)).some(cookie => cookie.name === "swooshz_quote_session")) {
    throw new Error("Logout retained the application session cookie.");
  }
  const revoked = await authenticatedApi.get(baseUrl + "/api/session");
  if (revoked.status() !== 401) throw new Error("Logged-out session remained usable.");

  const interactive = await anonymousApi.get(baseUrl + "/__synthetic_oidc/case?value=approved-account-choice");
  if (interactive.status() !== 200) throw new Error("Could not select interactive synthetic provider.");
  const providerRequests = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (url.pathname === "/__synthetic_oidc/authorize") providerRequests.push(url);
  });
  for (let revisit = 0; revisit < 2; revisit++) {
    const response = await page.goto(baseUrl + "/", { waitUntil: "domcontentloaded" });
    if (response.status() !== 401 || new URL(page.url()).pathname !== "/") throw new Error("Domain revisit silently signed back in.");
    if (providerRequests.length !== 0) throw new Error("Domain revisit started provider sign-in without user action.");
    if (await page.locator("#topbarLogoutLink").count()) throw new Error("Workspace rendered after logout.");
    if ((await page.request.get(baseUrl + "/api/session")).status() !== 401) throw new Error("Domain revisit created a session.");
  }
  await page.getByRole("link", { name: "Sign in", exact: true }).click();
  await page.getByRole("link", { name: "Choose synthetic account" }).waitFor();
  if (providerRequests.length !== 1) throw new Error("Explicit sign-in did not start exactly one provider flow.");
  if (providerRequests[0].searchParams.get("prompt") !== "select_account") throw new Error("Google account selection was not requested.");
  if ((await page.request.get(baseUrl + "/api/session")).status() !== 401) throw new Error("Account-choice screen already had an application session.");
  await page.getByRole("link", { name: "Choose synthetic account" }).click();
  await page.waitForURL(baseUrl + "/");
  if ((await page.request.get(baseUrl + "/api/session")).status() !== 200) throw new Error("Explicit account choice did not restore access.");
  if ((await authenticatedApi.get(baseUrl + "/api/session")).status() !== 401) throw new Error("New login reactivated the revoked old session.");
  if (expectedDocumentAuthFailures !== 2) throw new Error("Expected exactly two explicit domain-visit denials.");
  if (consoleProblems.length) throw new Error(`Browser console problems: ${consoleProblems.join(" | ")}`);
  console.log("Internal Google synthetic Playwright flow passed.");
} finally {
  if (authenticatedApi) await authenticatedApi.dispose();
  await anonymousApi.dispose();
  await browser.close();
  harness.kill("SIGTERM");
  await new Promise(resolve => harness.exitCode !== null ? resolve() : harness.once("exit", resolve));
}

// Keep expired-session navigation/recovery in the existing synthetic auth gate.
await import("./playwright-dashboard-auth-recovery.mjs");
