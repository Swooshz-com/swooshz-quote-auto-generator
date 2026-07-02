import { spawn } from "node:child_process";
import fsSync from "node:fs";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { seedQuoteDraftFromTestFixture } from "./playwright-test-seeded-setup.mjs";

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
const quoteDataRoot = path.join(root, "_tmp", "playwright-ai-basis-chat-quote-data");

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

function quoteBasisSections(platformText = "100mm raised platform with needle punch carpet.", electricalTag = "Confirm") {
  return [
    {
      id: "surfaces",
      title: "Surfaces",
      lines: [
        { tag: "Include", text: "Painted booth structure and fascia from uploaded render images.", confidence_pct: 90 },
      ],
    },
    {
      id: "platform",
      title: "Platform",
      lines: [
        { tag: "Confirm", text: platformText, quantity: 36, unit: "sqm", confidence_pct: 90 },
      ],
    },
    {
      id: "electrical",
      title: "Lighting and Electrical",
      lines: [
        { tag: electricalTag, text: "Standard 13A sockets and LED lighting only.", confidence_pct: 85 },
      ],
    },
    {
      id: "av-equipment-rental-items",
      title: "AV Equipment Rental Items",
      lines: [
        {
          tag: "Custom",
          text: "Custom - Large format LED video wall mounted on deep-blue feature wall.",
          quantity: 1,
          unit: "lot",
          confidence_pct: 78,
          custom_pricing: true,
          possible_pricing_matches: [
            {
              pricing_keyword: "av-equipment-rental-items-nos-85-led-tv-monitor-with-speaker-full-hd",
              description: 'nos. 85" LED TV Monitor (With Speaker - Full HD)',
              section: "AV Equipment Rental Items",
              unit: "nos",
            },
          ],
        },
      ],
    },
    {
      id: "graphics",
      title: "Graphics",
      lines: [
        { tag: "Custom", text: "Printed graphics pending manual pricing.", confidence_pct: 70, custom_pricing: true },
      ],
    },
  ];
}

function quoteBasisFromSections(sections) {
  return Object.fromEntries(sections.map((section) => [
    section.id,
    section.lines.map((line) => `${line.tag}: ${line.text}`).join("\n"),
  ]));
}

function draftResult() {
  const sections = quoteBasisSections();
  return {
    status: "drafted",
    source: "playwright-mock",
    quote_basis: quoteBasisFromSections(sections),
    quote_basis_sections: sections,
    line_items: [
      {
        section: "Floor Design",
        quantity: "36",
        unit: "sqm",
        description: "Needle punch carpet in colour",
        pricing_keyword: "",
        display_price: "Included",
        source_basis_line_id: "platform-1",
      },
    ],
    project: {
      booth_width: "6",
      booth_depth: "6",
      booth_size: "6m x 6m",
      dimension_source: "user",
    },
  };
}

function basisChatResult(payload) {
  const chat = payload?.basis_chat || {};
  const question = String(chat.question || "").toLowerCase();
  if (chat.scope === "line" && question.includes("150mm")) {
    if (String(chat.quantity || "") !== "36" || chat.unit !== "sqm" || chat.quantity_label !== "36 sqm") {
      return {
        status: "failed",
        errors: [`Selected line quantity was not included in basis chat payload: ${JSON.stringify(chat)}`],
      };
    }
    const sections = quoteBasisSections("150mm raised platform with needle punch carpet.");
    return {
      status: "answered",
      type: "proposal",
      source: "playwright-mock",
      proposal: {
        message: "Change the selected platform line to 150mm?",
        quote_basis: quoteBasisFromSections(sections),
        quote_basis_sections: sections,
        line_items: draftResult().line_items,
      },
    };
  }
  if (question.includes("what does")) {
    return {
      status: "answered",
      type: "answer",
      source: "playwright-mock",
      answer: "- **Meaning:** This basis line describes scope that needs operator review.",
    };
  }
  if (question.includes("include all lighting")) {
    const sections = quoteBasisSections("150mm raised platform with needle punch carpet.", "Include");
    return {
      status: "answered",
      type: "proposal",
      source: "playwright-mock",
      proposal: {
        message: "Mark lighting and electrical as included?",
        quote_basis: quoteBasisFromSections(sections),
        quote_basis_sections: sections,
        line_items: draftResult().line_items,
      },
    };
  }
  return {
    status: "failed",
    errors: ["I could not understand that basis change. Please rephrase it."],
  };
}

async function installMockJobs(page) {
  const jobs = new Map();
  let counter = 0;

  await page.route("**/api/profiles", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        profiles: [{
          id: "synthetic-playwright-profile",
          label: "Synthetic Playwright Profile",
          description: "Test-only profile for the AI basis chat smoke.",
          default_pricing_reference: "synthetic-playwright-pricing",
          default_quote_detail_preset: "synthetic-playwright-default",
          quote_detail_presets: [{
            id: "synthetic-playwright-default",
            name: "Synthetic Playwright Quote Company",
            details: {
              company: {
                name: "Synthetic Playwright Quote Company Pte Ltd",
                header_details: "Synthetic Playwright Quote Company Pte Ltd\n1 Synthetic Way\nSingapore 000001",
                logo_data_url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
              },
              quote_text: {
                payment_terms: ["70% synthetic deposit upon confirmation."],
                cheque_payee: "Synthetic Playwright Quote Company Pte Ltd",
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
          id: "synthetic-playwright-pricing",
          label: "Synthetic Playwright Pricing",
          source: "local",
          currency: "SGD",
          tax: { label: "GST", rate: 0.09 },
          item_count: 1,
        }],
        default_profile_id: "synthetic-playwright-profile",
        default_pricing_reference_id: "synthetic-playwright-pricing",
        company_id: "default",
        workspace: {
          company: { id: "default", slug: "default", display_name: "Quote Generator Workspace" },
          workspace: { id: "default", slug: "default", display_name: "Quote Generator Workspace" },
          runtime_dependencies: {},
        },
      }),
    });
  });

  await page.route("**/api/jobs", async (route) => {
    if (route.request().method() !== "POST") {
      await route.fallback();
      return;
    }
    const body = JSON.parse(route.request().postData() || "{}");
    const jobId = `playwright-job-${counter += 1}`;
    if (body.type === "draft") {
      jobs.set(jobId, { status: "completed", result: draftResult() });
    } else if (body.type === "basis_chat") {
      const result = basisChatResult(body.payload || {});
      jobs.set(jobId, {
        status: result.status === "failed" ? "failed" : "completed",
        result,
        errors: result.errors || [],
      });
    } else {
      jobs.set(jobId, { status: "failed", errors: [`Unexpected job type: ${body.type}`] });
    }
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: JSON.stringify({ status: "queued", job_id: jobId, created_at: new Date().toISOString() }),
    });
  });

  await page.route("**/api/jobs/*", async (route) => {
    const jobId = route.request().url().split("/").pop();
    const job = jobs.get(jobId) || { status: "failed", errors: [`Unknown mocked job: ${jobId}`] };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(job),
    });
  });
}

async function submitBasisChat(page, text) {
  await page.locator("#basisChatPrompt").fill(text);
  await page.locator("#basisChatSendButton").click();
}

function basisLineRow(page, text) {
  return page.locator(".basis-line-row", { hasText: text });
}

async function retagBasisLineAndWait(page, text, tag, expectedPill = tag) {
  const row = basisLineRow(page, text);
  await row.evaluate((element) => element.scrollIntoView({ block: "center", inline: "nearest" }));
  await row.locator(`[data-basis-tag="${tag}"]`).click();
  try {
    await basisLineRow(page, text).locator(".basis-line-pill", { hasText: expectedPill }).waitFor({ timeout: 15000 });
  } catch (error) {
    console.error(`Retag wait failed for ${JSON.stringify(text)} -> ${expectedPill}`);
    console.error(await basisLineRow(page, text).innerHTML().catch(() => "<row not found>"));
    throw error;
  }
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
  const page = await browser.newPage({ viewport: { width: 1365, height: 768 } });
  const consoleProblems = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      const location = message.location();
      const source = location?.url ? ` (${location.url}:${location.lineNumber || 0})` : "";
      consoleProblems.push(`${message.type()}: ${message.text()}${source}`);
    }
  });
  page.on("pageerror", (error) => consoleProblems.push(`pageerror: ${error.message}`));

  try {
    await installMockJobs(page);
    await page.goto(baseUrl, { waitUntil: "domcontentloaded" });
    await page.getByRole("heading", { name: "Swooshz Quote Generator" }).waitFor();
    await page.locator("#quoteDashboardPanel").waitFor({ state: "visible" });
    const emptyNewQuoteButton = page.locator("#dashboardEmptyNewQuoteButton:not([disabled])");
    if (await emptyNewQuoteButton.isVisible()) {
      await emptyNewQuoteButton.click();
    } else {
      await page.locator("#newQuoteButton:not([disabled])").waitFor({ timeout: 15000 });
      await page.locator("#newQuoteButton").click();
    }
    await page.locator("#imageIntake").waitFor({ state: "visible" });
    await seedQuoteDraftFromTestFixture(page);
    await page.locator("#sideNextButton", { hasText: "Next: Customer" }).click();
    await page.locator("#customerDetailsPanel").waitFor({ state: "visible", timeout: 15000 });
    if (!(await page.locator("#showName").inputValue()).trim()) {
      await page.locator("#showName").fill("Synthetic Expo");
    }

    await page.locator('[data-side-panel="quote_company"]:not([disabled])').waitFor({ timeout: 15000 });
    await page.locator('[data-side-panel="quote_company"]').click();
    await page.locator("#sideNextButton").click();
    await page.locator("#analysisConfirmModal:not([hidden])").waitFor({ timeout: 15000 });
    await page.locator("#analysisConfirmStartButton").click();
    await page.locator("#quoteBasisPanel.is-active").waitFor({ timeout: 15000 });
    await page.locator('[data-revise-section="platform"]').first().waitFor({ timeout: 15000 });
    const possibleMatchText = "Large format LED video wall mounted on deep-blue feature wall.";
    const possibleMatchReference = 'nos. 85" LED TV Monitor (With Speaker - Full HD)';
    await basisLineRow(page, possibleMatchText).locator(".basis-line-possible-match", { hasText: possibleMatchReference }).waitFor({ timeout: 15000 });
    const basisShot = await screenshot(page, "ai-basis-chat-basis.png");

    const customGraphicsText = "Printed graphics pending manual pricing.";
    await retagBasisLineAndWait(page, customGraphicsText, "Exclude");
    await page.locator(".basis-review-item", { hasText: "Graphics" }).locator('[data-basis-section-action="Include"]').click();
    await basisLineRow(page, customGraphicsText).locator(".basis-line-pill", { hasText: "AI Proposal" }).waitFor({ timeout: 15000 });
    await page.locator(".basis-review-item", { hasText: "Graphics" }).locator('[data-basis-section-action="Exclude"]').click();
    await basisLineRow(page, customGraphicsText).locator(".basis-line-pill", { hasText: "Exclude" }).waitFor({ timeout: 15000 });
    await retagBasisLineAndWait(page, customGraphicsText, "Custom", "AI Proposal");

    await page.locator('[data-revise-section="platform"]').first().click();
    await page.locator("#basisChatOverlay:not([hidden])").waitFor({ timeout: 15000 });
    await page.locator(".basis-chat-selected-quantity", { hasText: "36 sqm" }).waitFor({ timeout: 15000 });
    await submitBasisChat(page, "change 100mm to 150mm");
    await page.locator("#basisChatProposal:not([hidden])").waitFor({ timeout: 15000 });
    await page.locator("#basisChatApplyButton:not([disabled])").click();
    await page.locator(".basis-line-text", { hasText: "150mm raised platform" }).waitFor({ timeout: 15000 });
    await page.locator("#basisChatOverlay").waitFor({ state: "hidden", timeout: 15000 });

    await basisLineRow(page, "Standard 13A sockets and LED lighting only.").locator("[data-revise-section]").click();
    await page.locator("#basisChatOverlay:not([hidden])").waitFor({ timeout: 15000 });
    await submitBasisChat(page, "what does this mean?");
    await page.locator("#basisChatMessages", { hasText: "This basis line describes scope" }).waitFor({ timeout: 15000 });
    await submitBasisChat(page, "include all lighting and electrical lines");
    await page.locator("#basisChatProposal:not([hidden])", { hasText: "Mark lighting and electrical as included" }).waitFor({ timeout: 15000 });
    await page.locator("#basisChatKeepButton:not([disabled])").click();
    await page.locator("#basisChatMessages", { hasText: "Kept the current quote basis unchanged." }).waitFor({ timeout: 15000 });
    await submitBasisChat(page, "banana everything but also delete it");
    await page.locator("#basisChatMessages", { hasText: "Failed. Please try again." }).waitFor({ timeout: 15000 });
    const chatShot = await screenshot(page, "ai-basis-chat-stress.png");

    await page.locator("#basisChatCloseButton").click();
    await page.locator("#basisChatOverlay").waitFor({ state: "hidden", timeout: 15000 });
    await basisLineRow(page, possibleMatchText).locator(".basis-line-possible-match", { hasText: possibleMatchReference }).click();
    await basisLineRow(page, possibleMatchText).locator(".basis-line-pill", { hasText: "Include" }).waitFor({ timeout: 15000 });
    await basisLineRow(page, possibleMatchText).locator(".basis-line-catalog-reference", { hasText: possibleMatchReference }).waitFor({ timeout: 15000 });
    const remainingPossibleMatchButtons = await basisLineRow(page, possibleMatchText).locator(".basis-line-possible-match").count();
    if (remainingPossibleMatchButtons !== 0) {
      throw new Error("Possible pricing match button remained after selecting the pricing reference candidate.");
    }
    for (let remaining = await page.locator(".basis-line-row.basis-line-confirm").count(); remaining > 0;) {
      await page.locator(".basis-line-row.basis-line-confirm [data-basis-tag=\"Include\"]").first().click();
      remaining = await page.locator(".basis-line-row.basis-line-confirm").count();
    }
    await page.locator("#sideNextButton:not(.is-disabled)").waitFor({ timeout: 15000 });
    await page.locator("#sideNextButton").click();
    await page.locator("#outputSidePanel.is-active").waitFor({ timeout: 15000 });
    const pendingUnitPriceCells = page.locator(".output-unit-price-cell", { hasText: "???" });
    await pendingUnitPriceCells.first().waitFor({ timeout: 15000 });
    const outputIncludedRows = await pendingUnitPriceCells.evaluateAll((cells) => (
      cells.slice(0, 2).map((cell) => cell.getAttribute("data-output-row")).filter(Boolean)
    ));
    if (!outputIncludedRows.length) {
      throw new Error("Expected at least one pending output unit price cell.");
    }
    for (const row of outputIncludedRows) {
      const unitPriceCell = page.locator(`.output-unit-price-cell[data-output-row="${row}"]`);
      await unitPriceCell.locator(".output-cell-text").click();
      const includedButton = unitPriceCell.locator(".output-unit-price-editor .output-included-button");
      await includedButton.waitFor({ timeout: 15000 });
      await includedButton.click();
    }
    const outputUnitPrices = await page.locator("#pricingMatchesBody tr").evaluateAll((rows, selectedRows) => (
      selectedRows.map((row) => rows[Number(row)]?.querySelector(".output-unit-price-cell .output-cell-text")?.textContent?.trim() || "")
    ), outputIncludedRows);
    if (outputUnitPrices.some((value) => value !== "Included")) {
      throw new Error(`Sequential Included clicks did not persist: ${JSON.stringify(outputUnitPrices)}.`);
    }
    const outputShot = await screenshot(page, "ai-output-included-overlay.png");

    const bodyText = await page.locator("body").innerText();
    for (const forbidden of ["Traceback", "Request body must be valid JSON", "OpenAI chat returned invalid JSON"]) {
      if (bodyText.includes(forbidden)) throw new Error(`Raw internal error leaked to UI: ${forbidden}`);
    }
    if (consoleProblems.length) {
      const serverOutput = serverInfo?.output?.join("")?.trim();
      throw new Error(`Console errors detected: ${consoleProblems.join("; ")}${serverOutput ? `\n\nServer output:\n${serverOutput}` : ""}`);
    }

    console.log(JSON.stringify({
      status: "ok",
      url: page.url(),
      screenshots: [basisShot, chatShot, outputShot].filter(Boolean),
      consoleProblems,
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
