import { spawn } from "node:child_process";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("..", import.meta.url));
const host = "127.0.0.1";
const port = Number(process.env.PLAYWRIGHT_PORT || "8797");
const baseUrl = `http://${host}:${port}`;
const outputDir = path.join(root, "_logs", "browser", "forensics-feedback-retention");
const quoteDataRoot = path.join(root, "_tmp", "playwright-forensics-feedback-data");

function pythonCommand() {
  if (process.env.PYTHON) return process.env.PYTHON;
  if (process.platform !== "win32") return "python3";
  const bundled = path.join(
    os.homedir(),
    ".cache",
    "codex-runtimes",
    "codex-primary-runtime",
    "dependencies",
    "python",
    "python.exe",
  );
  return fsSync.existsSync(bundled) ? bundled : "python";
}

async function healthOk() {
  try {
    const response = await fetch(`${baseUrl}/api/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(1200),
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForHealth(expected, timeoutMs = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if ((await healthOk()) === expected) return true;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  return false;
}

function startServer() {
  const server = spawn(
    pythonCommand(),
    ["webapp/server.py", "--host", host, "--port", String(port)],
    {
      cwd: root,
      env: {
        ...process.env,
        APP_MODE: "local",
        SQAG_STORAGE_MODE: "local",
        SQAG_ARTIFACT_STORAGE_MODE: "local",
        SQAG_LIVE_OBJECT_STORAGE_EVIDENCE: "0",
        SQAG_LIVE_DATABASE_EVIDENCE: "0",
        SQAG_LIVE_DB_OBJECT_BACKUP_RESTORE_EVIDENCE: "0",
        SQAG_LIVE_RETENTION_DELETE_EVIDENCE: "0",
        QUOTE_DATA_ROOT: quoteDataRoot,
      },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    },
  );
  const output = [];
  const collect = (chunk) => {
    output.push(String(chunk));
    while (output.join("").length > 12000) output.shift();
  };
  server.stdout.on("data", collect);
  server.stderr.on("data", collect);
  return { server, output };
}

async function stopServer(serverInfo) {
  if (!serverInfo || serverInfo.server.exitCode !== null) return;
  serverInfo.server.kill();
  await Promise.race([
    new Promise((resolve) => serverInfo.server.once("exit", resolve)),
    new Promise((resolve) => setTimeout(resolve, 8000)),
  ]);
}

async function capture(page, name) {
  const target = path.join(outputDir, name);
  await page.screenshot({ path: target, fullPage: true });
  return target;
}

let serverInfo = null;
let browser = null;
const screenshots = [];
const browserProblems = [];

const feedbackContextRequests = [];
let feedbackContextEvidence = {};
try {
  if (await healthOk()) {
    throw new Error(`Port ${port} already serves an SQAG health response; refusing to reuse a stale server.`);
  }

  await fs.rm(quoteDataRoot, { recursive: true, force: true });
  await fs.mkdir(outputDir, { recursive: true });
  serverInfo = startServer();
  if (!(await waitForHealth(true))) {
    throw new Error(`Fresh SQAG server did not become healthy. ${serverInfo.output.join("").trim()}`);
  }

  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.on("pageerror", (error) => browserProblems.push(`pageerror:${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 400) {
      browserProblems.push(`http:${response.status()}:${new URL(response.url()).pathname}`);
    }
  });
  page.on("console", (message) => {
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) {
      browserProblems.push(`console:${message.text()}`);
    }
  });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/feedback/context") {
      feedbackContextRequests.push(url.toString());
    }
  });

  await page.goto(baseUrl, { waitUntil: "networkidle" });
  await page.locator("#feedbackButton").waitFor({ state: "visible" });
  screenshots.push(await capture(page, "01-app-shell-feedback-terms-privacy.png"));
  const generationScenario = await page.evaluate(() => {
    transitionGenerationContext("quote-synthetic-a", "run-synthetic-a123");
    const savedA = buildSessionSnapshot();
    resetCurrentQuoteDraftState();
    const sessionB = ensureClientQuoteSessionId();
    return {
      savedA,
      sessionB,
      afterStartB: currentGenerationContext(),
    };
  });
  if (generationScenario.afterStartB.run_id) {
    throw new Error("Starting quote B retained quote A generation run.");
  }
  const firstContextRequest = page.waitForRequest((request) => (
    new URL(request.url()).pathname === "/api/feedback/context"
  ));
  await page.locator("#feedbackButton").click();
  await page.locator("#feedbackModal").waitFor({ state: "visible" });
  const quoteBContextUrl = new URL((await firstContextRequest).url());
  if (quoteBContextUrl.searchParams.get("run_id")) {
    throw new Error("Quote B feedback lookup sent quote A generation run.");
  }
  if (quoteBContextUrl.searchParams.get("session_id") !== generationScenario.sessionB) {
    throw new Error("Quote B feedback lookup did not send the active quote session.");
  }
  await page.locator("#cancelFeedbackButton").click();
  await page.locator("#feedbackModal").waitFor({ state: "hidden" });

  const restoreScenario = await page.evaluate(async ({ savedA, sessionB }) => {
    await applyQuoteSessionSnapshot(savedA, {
      sessionId: "quote-synthetic-a",
      forceQuoteView: true,
    });
    const authoritativeA = currentGenerationContext();
    const mismatchedRecovery = {
      ...savedA,
      quoteSessionId: sessionB,
      generationContext: {
        session_id: "quote-synthetic-a",
        run_id: "run-synthetic-a123",
      },
    };
    await applyQuoteSessionSnapshot(mismatchedRecovery, {
      sessionId: sessionB,
      forceQuoteView: true,
    });
    return {
      authoritativeA,
      mismatchedB: currentGenerationContext(),
    };
  }, { savedA: generationScenario.savedA, sessionB: generationScenario.sessionB });
  if (restoreScenario.authoritativeA.run_id !== "run-synthetic-a123") {
    throw new Error("Authoritative quote A run/session pair was not restored.");
  }
  if (restoreScenario.mismatchedB.run_id) {
    throw new Error("Browser recovery accepted a mismatched quote A run for quote B.");
  }
  feedbackContextEvidence = {
    quote_b_lookup_omitted_stale_run: true,
    authoritative_pair_restored: true,
    mismatched_recovery_rejected: true,
  };


  await page.locator("#feedbackButton").click();
  await page.locator("#feedbackModal").waitFor({ state: "visible" });
  await page.locator("#feedbackCategory").selectOption("incorrect_output");
  await page.locator("#feedbackShortTitle").fill("Synthetic output mismatch");
  await page.locator("#feedbackMessage").fill("Synthetic UAT feedback: verify privacy-safe diagnostic linking.");
  screenshots.push(await capture(page, "02-feedback-modal.png"));

  await page.locator("#submitFeedbackButton").click();
  await page.locator("#feedbackStatus").filter({ hasText: "Feedback submitted" }).waitFor({ state: "visible" });
  const feedbackStatus = (await page.locator("#feedbackStatus").textContent())?.trim() || "";
  screenshots.push(await capture(page, "03-feedback-submitted.png"));

  await page.goto(`${baseUrl}/terms`, { waitUntil: "networkidle" });
  const termsText = await page.locator("body").innerText();
  for (const required of ["Terms", "Effective date", "Version", "LEGAL OWNER PLACEHOLDER"]) {
    if (!termsText.includes(required)) throw new Error(`Terms page is missing required text: ${required}`);
  }
  screenshots.push(await capture(page, "04-terms.png"));

  await page.goto(`${baseUrl}/privacy`, { waitUntil: "networkidle" });
  const privacyText = (await page.locator("body").innerText()).toLowerCase();
  for (const required of ["three calendar years", "90 days", "30 days", "legal hold"]) {
    if (!privacyText.includes(required)) throw new Error(`Privacy page is missing required text: ${required}`);
  }
  screenshots.push(await capture(page, "05-privacy.png"));

  if (browserProblems.length) {
    throw new Error(`Browser errors detected: ${browserProblems.join("; ")}`);
  }

  console.log(JSON.stringify({
    status: "passed",
    base_url: baseUrl,
    feedback_status: feedbackStatus,
    screenshots,
    feedback_context_evidence: feedbackContextEvidence,
    feedback_context_requests: feedbackContextRequests.length,
    fresh_server_health_verified: true,
    production_ready: false,
  }, null, 2));
} finally {
  if (browser) await browser.close();
  await stopServer(serverInfo);
  if (serverInfo && !(await waitForHealth(false, 10000))) {
    throw new Error(`Task-owned SQAG server stopped but port ${port} did not release.`);
  }
}
