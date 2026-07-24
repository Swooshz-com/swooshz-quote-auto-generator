import { spawn } from "node:child_process";
import { chromium } from "playwright";

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
const consoleProblems = [];
page.on("console", (message) => {
  if (["error", "warning"].includes(message.type())) consoleProblems.push(message.text());
});
page.on("pageerror", (error) => consoleProblems.push(error.message));

try {
  const denied = await page.request.get(`${baseUrl}/api/session`);
  if (denied.status() !== 401) throw new Error(`Expected unauthenticated 401, got ${denied.status()}.`);

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

  const unsafeLogout = await page.request.get(`${baseUrl}/logout`);
  if (unsafeLogout.status() !== 405) throw new Error("GET logout was not rejected.");

  const logout = await page.evaluate(async ({ header, token }) => {
    const response = await fetch("/logout", {
      method: "POST",
      headers: { [header]: token },
    });
    return {
      status: response.status,
      location: response.headers.get("X-SQAG-Logout-Location"),
    };
  }, { header: session.body.csrf_header, token: session.body.csrf_token });
  if (logout.status !== 204 || logout.location !== "/signed-out") {
    throw new Error("CSRF-safe logout did not revoke the local session.");
  }

  const revoked = await page.request.get(`${baseUrl}/api/session`);
  if (revoked.status() !== 401) throw new Error("Logged-out session remained usable.");
  if (consoleProblems.length) throw new Error(`Browser console problems: ${consoleProblems.join(" | ")}`);
  console.log("Internal Google synthetic Playwright flow passed.");
} finally {
  await browser.close();
  harness.kill("SIGTERM");
}
