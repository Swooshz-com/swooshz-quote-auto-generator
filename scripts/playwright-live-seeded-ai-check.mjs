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
  headed: args.includes("--headed"),
  keepServer: args.includes("--keep-server"),
  host: readArg("--host", "127.0.0.1"),
  port: Number(readArg("--port", process.env.PLAYWRIGHT_PORT || "8765")),
  timeoutMs: Number(readArg("--timeout-ms", "420000")),
};

const baseUrl = `http://${options.host}:${options.port}`;

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
      env: { ...process.env, APP_MODE: "local" },
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

function catalogTextAfterBracket(lineText) {
  const match = String(lineText || "").match(/^\[ ([^\]]+) \](?:\s+-\s+(.+))?$/);
  if (!match) return null;
  return {
    catalog: match[1],
    detail: match[2] || "",
  };
}

function isDefaultDimensionLine(line) {
  const text = String(line?.text || "").toLowerCase();
  return (
    text.includes("default booth size")
    || text.includes("booth size defaults")
    || text.includes("booth footprint")
    || text.includes("booth dimensions")
    || text.includes("quotation takeoff")
  );
}

function firstNonSpaceChar(text) {
  return String(text || "").trim().match(/\S/)?.[0] || "";
}

function safeReferenceId(value) {
  const text = String(value || "").trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return text || "synthetic-exhibition-fixture-pricing";
}

function catalogDescriptionForDisplay(value) {
  return String(value || "").trim().replace(/\bm2\b/gi, "sqm").replace(/\s+/g, " ");
}

function comparableCatalogText(value) {
  return catalogDescriptionForDisplay(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

async function loadCatalogById(referenceId) {
  const catalogPath = path.join(root, "pricing-references", safeReferenceId(referenceId), "pricing-catalog.json");
  try {
    const raw = JSON.parse(await fs.readFile(catalogPath, "utf8"));
    return new Map((raw.items || [])
      .filter((item) => item && item.id && item.description)
      .map((item) => [String(item.id), catalogDescriptionForDisplay(item.description)]));
  } catch {
    return new Map();
  }
}

function lineContainsDisplayTerm(line) {
  return /\b(tv|display|screen|monitor|lcd|led)\b/i.test([
    line?.text,
    line?.pricing_reference_description,
    line?.catalog_description,
  ].filter(Boolean).join(" "));
}

function pricingOrderNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? Math.trunc(numeric) : null;
}

function validateReferenceOrder(sections) {
  const orderViolations = [];
  let previousCategoryOrder = -Infinity;
  let previousItemOrderByCategory = new Map();
  let previousSectionKey = "";

  for (const section of sections) {
    for (const line of section.lines || []) {
      const categoryOrder = pricingOrderNumber(line.category_order);
      const itemOrder = pricingOrderNumber(line.item_order);
      const hasCatalogOrder = categoryOrder !== null;
      if (!hasCatalogOrder) continue;

      const sectionKey = String(section.title || section.id || "");
      if (categoryOrder < previousCategoryOrder) {
        orderViolations.push(
          `Section/order moved backwards at ${sectionKey}: category_order ${categoryOrder} after ${previousCategoryOrder}.`,
        );
      }
      if (categoryOrder === previousCategoryOrder && sectionKey !== previousSectionKey && itemOrder !== null) {
        const previousItemOrder = previousItemOrderByCategory.get(categoryOrder) ?? -Infinity;
        if (itemOrder < previousItemOrder) {
          orderViolations.push(
            `Item order moved backwards inside category ${categoryOrder} at ${sectionKey}: item_order ${itemOrder} after ${previousItemOrder}.`,
          );
        }
      }
      previousCategoryOrder = Math.max(previousCategoryOrder, categoryOrder);
      previousSectionKey = sectionKey;
      if (itemOrder !== null) {
        previousItemOrderByCategory.set(categoryOrder, itemOrder);
      }
    }
  }

  return orderViolations;
}

function validateLiveBasis(snapshot, catalogById) {
  const sections = Array.isArray(snapshot?.quoteBasisSections) ? snapshot.quoteBasisSections : [];
  const violations = [];
  if (!sections.length) {
    violations.push("No quote_basis_sections were saved after live AI analysis.");
  }
  if (snapshot?.aiFailed || snapshot?.draftSource === "local") {
    violations.push(`Live AI draft fell back to local source: ${snapshot?.draftSource || "unknown"}.`);
  }

  for (const section of sections) {
    for (const line of section.lines || []) {
      const tag = String(line.tag || "");
      const text = String(line.text || "");
      const hasCatalog = Boolean(line.pricing_reference_description || line.catalog_description || line.pricing_keyword);
      const bracket = catalogTextAfterBracket(text);

      if (hasCatalog && !bracket) {
        violations.push(`${section.title}: catalog-backed line is not bracketed: ${text}`);
      }
      if (bracket && line.pricing_keyword && catalogById.has(String(line.pricing_keyword))) {
        const expectedCatalogText = catalogById.get(String(line.pricing_keyword));
        if (comparableCatalogText(bracket.catalog) !== comparableCatalogText(expectedCatalogText)) {
          violations.push(`${section.title}: bracket text does not match pricing_keyword ${line.pricing_keyword}: expected [ ${expectedCatalogText} ], got [ ${bracket.catalog} ]`);
        }
      }
      if (!hasCatalog && tag === "Confirm" && !isDefaultDimensionLine(line)) {
        violations.push(`${section.title}: Confirm line has no catalog backing: ${text}`);
      }
      if (!hasCatalog && tag === "Custom" && bracket) {
        violations.push(`${section.title}: Custom line should not use catalog brackets: ${text}`);
      }
      const detailStart = firstNonSpaceChar(bracket?.detail);
      if (/[a-z]/.test(detailStart)) {
        violations.push(`${section.title}: observed detail after hyphen should start with uppercase: ${text}`);
      }
      if (/AV Equipment Rental Items/i.test(String(section.title || "")) && tag === "Confirm" && lineContainsDisplayTerm(line) && !hasCatalog) {
        violations.push(`${section.title}: display/TV/monitor wording is not catalog-backed: ${text}`);
      }
    }
  }
  violations.push(...validateReferenceOrder(sections));

  const sectionTitles = sections.map((section) => section.title || section.id || "");
  const lineSummary = sections.map((section) => ({
    title: section.title,
    lines: (section.lines || []).map((line) => ({
      tag: line.tag,
      quantity: line.quantity ?? "",
      unit: line.unit ?? "",
      confidence: line.confidence ?? line.confidence_pct ?? "",
      pricing_keyword: line.pricing_keyword || "",
      text: line.text || "",
    })),
  }));

  return {
    ok: violations.length === 0,
    violations,
    draftSource: snapshot?.draftSource || "",
    sectionTitles,
    lineSummary,
  };
}

async function writeReport(report) {
  const logDir = path.join(root, "_logs", "browser");
  await fs.mkdir(logDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const reportPath = path.join(logDir, `live-seeded-ai-check-${stamp}.json`);
  await fs.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
  return reportPath;
}

async function main() {
  let serverInfo = null;
  if (!(await healthOk())) {
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
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`);
  });
  page.on("pageerror", (error) => consoleProblems.push(`pageerror: ${error.message}`));

  try {
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await seedQuoteDraftFromTestFixture(page);
    await page.locator("#fileList .file-item", { hasText: TEST_REFERENCE_FILE_NAME }).waitFor({ timeout: 15000 });

    await page.locator('[data-side-panel="quote_company"]:not([disabled])').waitFor({ timeout: 15000 });
    await page.locator('[data-side-panel="quote_company"]').click();
    await page.locator("#sideNextButton:not(.is-disabled)").waitFor({ timeout: 15000 });
    await page.locator("#sideNextButton").click();
    await page.locator("#analysisConfirmModal:not([hidden])").waitFor({ timeout: 15000 });
    await page.locator("#analysisConfirmStartButton").click();
    await page.locator("#quoteBasisPanel.is-active").waitFor({ timeout: options.timeoutMs });
    await page.locator(".basis-review-item").first().waitFor({ timeout: options.timeoutMs });

    await page.waitForFunction(() => {
      const raw = window.localStorage.getItem("swooshz_quote_session_v1");
      if (!raw) return false;
      try {
        const saved = JSON.parse(raw);
        return Array.isArray(saved.quoteBasisSections) && saved.quoteBasisSections.length > 0;
      } catch {
        return false;
      }
    }, null, { timeout: 15000 });

    const snapshot = await page.evaluate(() => JSON.parse(window.localStorage.getItem("swooshz_quote_session_v1") || "{}"));
    const catalogById = await loadCatalogById(snapshot.pricingReferenceId);
    const result = validateLiveBasis(snapshot, catalogById);
    const report = {
      status: result.ok && consoleProblems.length === 0 ? "ok" : "failed",
      url: page.url(),
      consoleProblems,
      ...result,
    };
    const reportPath = await writeReport(report);
    console.log(JSON.stringify({
      status: report.status,
      reportPath,
      draftSource: report.draftSource,
      sectionTitles: report.sectionTitles,
      violations: report.violations,
      consoleProblems,
    }, null, 2));

    if (report.status !== "ok") {
      throw new Error(`Live seeded AI check failed; see ${reportPath}`);
    }
  } finally {
    await browser.close();
    await stopServer(serverInfo);
  }
}

main().catch((error) => {
  console.error(error.message || error);
  process.exitCode = 1;
});
