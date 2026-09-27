import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, request } from "playwright";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixtureDir = path.join(root, "tests", "fixtures", "quote-generator", "pricing-references", "synthetic-exhibition-fixture-pricing");
const serverLogDir = path.join(root, "_logs", "server", "playwright-alpha-smoke");
const browserLogDir = path.join(root, "_logs", "browser", "playwright-alpha-smoke");
const python = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? "python" : "python3");
const workspaceId = "workspace-internal-alpha";
const expectedCustomer = "Synthetic Alpha Customer";
const expectedProject = "Synthetic Internal Exhibition Alpha";
const expectedProjectNumber = "ALPHA-" + Date.now().toString(36).toUpperCase();
const trace = { quoteSaves: [], quoteSaveRequests: [], normalizations: [], jobs: [], externalRequests: [], aiDraftRequests: [], consoleProblems: [], pageErrors: [], pending: [] };
const quoteSaveRequestRecords = new WeakMap();
let quoteSaveSequence = 0;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function generationRunIdBinding(accepted, terminal) {
  const acceptedId = typeof accepted?.generation_run_id === "string" ? accepted.generation_run_id.trim() : "";
  if (!acceptedId) return "";
  if (terminal?.generation_run_id !== acceptedId || terminal?.result?.generation_run_id !== acceptedId) return "";
  return acceptedId;
}

function requireGenerationRunIdBinding(accepted, terminal, kind) {
  const runId = generationRunIdBinding(accepted, terminal);
  assert(runId, `${kind} accepted job and terminal/result generation run IDs are missing or mismatched.`);
  return runId;
}

function verifyGenerationRunIdBindingControls() {
  const accepted = { generation_run_id: "run-positive-control" };
  const matching = {
    generation_run_id: "run-positive-control",
    result: { generation_run_id: "run-positive-control" },
  };
  assert(generationRunIdBinding(accepted, matching) === "run-positive-control", "Generation run-ID equality positive control failed.");
  assert(generationRunIdBinding(accepted, {
    generation_run_id: "run-negative-control",
    result: { generation_run_id: "run-negative-control" },
  }) === "", "Generation run-ID mismatch negative control was accepted.");
  assert(generationRunIdBinding(accepted, {
    generation_run_id: "run-positive-control",
    result: { generation_run_id: "run-negative-control" },
  }) === "", "Mismatched terminal result run-ID negative control was accepted.");
}

function safeJsonRequest(req) {
  try { return req.postDataJSON(); } catch { return null; }
}

function safeErrorText(value) {
  return String(value?.message || value || "unknown error").replace(/[\r\n]+/g, " ").slice(0, 500);
}

async function waitUntil(predicate, label, timeoutMs = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = await predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

function spawnCollect(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true, ...options });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr?.on("data", (chunk) => { stderr += chunk.toString(); });
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
}

function startHarness(scriptPath, args, repoRoot) {
  const child = spawn(python, [scriptPath, repoRoot, ...args], {
    cwd: repoRoot,
    windowsHide: true,
    env: { ...process.env, PYTHONUNBUFFERED: "1", OPENAI_API_KEY: "", DEEPSEEK_API_KEY: "", SQAG_DISABLE_DOTENV: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stdout = "";
  let stderr = "";
  let lineBuffer = "";
  const baseUrlPromise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Synthetic auth harness did not start. ${stderr.slice(-800)}`)), 20000);
    child.stdout.on("data", (chunk) => {
      const text = chunk.toString();
      stdout += text;
      lineBuffer += text;
      const lines = lineBuffer.split(/\r?\n/);
      lineBuffer = lines.pop() || "";
      for (const line of lines) {
        if (/^http:\/\/127\.0\.0\.1:\d+$/.test(line.trim())) {
          clearTimeout(timer);
          resolve(line.trim());
          return;
        }
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
      if (stderr.length > 8000) stderr = stderr.slice(-8000);
    });
    child.once("error", (error) => { clearTimeout(timer); reject(error); });
    child.once("exit", (code) => {
      clearTimeout(timer);
      if (!stdout.includes("http://127.0.0.1:")) reject(new Error(`Synthetic auth harness exited before readiness (${code}). ${stderr.slice(-800)}`));
    });
  });
  return { child, baseUrlPromise };
}

async function stopHarness(harness) {
  if (!harness?.child || harness.child.exitCode !== null) return;
  harness.child.kill();
  await Promise.race([new Promise((resolve) => harness.child.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 5000))]);
}

function attachTrace(page, baseUrl) {
  page.on("console", (message) => { if (["error", "warning"].includes(message.type())) trace.consoleProblems.push(message.text()); });
  page.on("pageerror", (error) => trace.pageErrors.push(error.message));
  page.on("request", (browserRequest) => {
    const url = new URL(browserRequest.url());
    if (url.origin === baseUrl && url.pathname === "/api/quote-sessions" && browserRequest.method() === "POST") {
      const record = { sequence: ++quoteSaveSequence, payload: safeJsonRequest(browserRequest) };
      quoteSaveRequestRecords.set(browserRequest, record);
      trace.quoteSaveRequests.push(record);
    }
    if (url.origin !== baseUrl) trace.externalRequests.push(url.origin);
    if (url.pathname === "/api/draft") trace.aiDraftRequests.push(browserRequest.method());
  });
  page.on("response", (response) => {
    const task = (async () => {
      const req = response.request();
      const url = new URL(response.url());
      if (url.origin !== baseUrl || !response.headers()["content-type"]?.includes("application/json")) return;
      if (url.pathname === "/api/quote-sessions" && req.method() === "POST") {
        const record = quoteSaveRequestRecords.get(req) || { sequence: 0, payload: safeJsonRequest(req) };
        trace.quoteSaves.push({ ...record, status: response.status(), body: await response.json() });
      } else if (url.pathname === "/api/line-items/normalize" && req.method() === "POST") {
        trace.normalizations.push({ status: response.status(), payload: safeJsonRequest(req), body: await response.json() });
      } else if (url.pathname === "/api/jobs" && req.method() === "POST") {
        trace.jobs.push({ type: safeJsonRequest(req)?.type || "", request: safeJsonRequest(req), accepted: await response.json(), polls: [] });
      } else {
        const match = url.pathname.match(/^\/api\/jobs\/([A-Za-z0-9_-]+)$/);
        if (match && req.method() === "GET") {
          const jobId = match[1];
          const job = [...trace.jobs].reverse().find((item) => item.accepted?.job_id === jobId || item.request?.job_id === jobId);
          const body = await response.json();
          if (job) job.polls.push({ statusCode: response.status(), body });
        }
      }
    })();
    trace.pending.push(task);
    task.catch((error) => trace.pageErrors.push(safeErrorText(error)));
  });
}

async function makePng(page, svg) {
  await page.setContent(svg, { waitUntil: "domcontentloaded" });
  return page.locator("svg").screenshot({ type: "png" });
}

async function getJson(response, label) {
  assert(response.ok(), `${label} returned HTTP ${response.status()}.`);
  return response.json();
}

async function quoteSessionDetail(api, baseUrl, sessionId) {
  const response = await api.get(`${baseUrl}/api/quote-sessions/${encodeURIComponent(sessionId)}`);
  const body = await getJson(response, "Independent quote-session readback");
  assert(body.quote_session?.session_id === sessionId, "Quote-session readback returned a different session.");
  return body.quote_session;
}

async function authenticatedDownload(api, baseUrl, exportRecord, destination, kind) {
  assert(exportRecord?.exists === true && exportRecord?.stale === false, `Current ${kind} export is missing or stale.`);
  const url = new URL(exportRecord.url, baseUrl).toString();
  const response = await api.get(url);
  assert(response.status() === 200, `Authorized ${kind} download returned HTTP ${response.status()}.`);
  const bytes = Buffer.from(await response.body());
  const sha256 = createHash("sha256").update(bytes).digest("hex");
  assert(bytes.length > 0, `${kind} download was empty.`);
  assert(Number(exportRecord.size_bytes) === bytes.length, `${kind} artifact size differs from persisted metadata.`);
  assert(String(exportRecord.sha256 || "").toLowerCase() === sha256, `${kind} artifact digest differs from persisted metadata.`);
  await fs.writeFile(destination, bytes);
  return { url, bytes, sha256, size: bytes.length, contentType: response.headers()["content-type"] || "" };
}

async function validateXlsx(filePath, expectedStrings, expectedNumbers, includedDescription) {
  const validation = `import json, math, sys, zipfile, xml.etree.ElementTree as ET
path, strings_json, numbers_json, included_description = sys.argv[1:5]
included_description = json.loads(included_description)
needles = json.loads(strings_json)
expected = [float(value) for value in json.loads(numbers_json)]
ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
with zipfile.ZipFile(path) as archive:
    bad = archive.testzip()
    if bad:
        raise SystemExit("corrupt XLSX member: " + bad)
    names = set(archive.namelist())
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise SystemExit("XLSX package structure is incomplete")
    shared = []
    if "xl/sharedStrings.xml" in names:
        tree = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(node.itertext()) for node in tree.findall("m:si", ns)]
    texts, numbers = [], []
    sheets = sorted(name for name in names if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
    if not sheets:
        raise SystemExit("XLSX contains no worksheets")
    for name in sheets:
        sheet = ET.fromstring(archive.read(name))
        for cell in sheet.findall(".//m:c", ns):
            kind = cell.attrib.get("t", "")
            value = cell.find("m:v", ns)
            if kind == "s" and value is not None:
                texts.append(shared[int(value.text or "0")])
            elif kind == "inlineStr":
                inline = cell.find("m:is", ns)
                if inline is not None:
                    texts.append("".join(inline.itertext()))
            elif kind in {"str", "e"} and value is not None:
                texts.append(value.text or "")
            elif value is not None:
                try:
                    numbers.append(float(value.text or "nan"))
                except ValueError:
                    pass
    included_row_has_zero = False
    for name in sheets:
        sheet = ET.fromstring(archive.read(name))
        for row in sheet.findall(".//m:sheetData/m:row", ns):
            row_text, row_numbers = [], []
            for cell in row.findall("m:c", ns):
                kind = cell.attrib.get("t", "")
                value = cell.find("m:v", ns)
                text = ""
                if kind == "s" and value is not None:
                    text = shared[int(value.text or "0")]
                elif kind == "inlineStr":
                    inline = cell.find("m:is", ns)
                    if inline is not None:
                        text = "".join(inline.itertext())
                elif kind in {"str", "e"} and value is not None:
                    text = value.text or ""
                if text:
                    row_text.append(text)
                elif value is not None:
                    try:
                        row_numbers.append(float(value.text or "nan"))
                    except ValueError:
                        pass
            if included_description in row_text and any(math.isfinite(number) and abs(number) <= 0.011 for number in row_numbers):
                included_row_has_zero = True
    package_xml = "".join(archive.read(name).decode("utf-8", errors="replace") for name in names if name.startswith("xl/") and name.endswith(".xml"))
all_text = "\\n".join(texts) + package_xml
missing = [value for value in needles if value not in all_text]
if missing:
    relevant = [value for value in texts if any(token in value.casefold() for token in ("alpha", "included", "wall rail", "carpet tile"))]
    raise SystemExit("XLSX is missing expected quote text: " + json.dumps({"missing": missing, "relevant_cells": relevant}))
if not included_row_has_zero:
    raise SystemExit("XLSX does not show the Included row at zero amount: " + included_description)
for value in expected:
    if not any(math.isfinite(actual) and abs(actual - value) <= 0.011 for actual in numbers):
        raise SystemExit("XLSX is missing expected current total/value: " + str(value))
print(json.dumps({"status": "valid", "sheets": len(sheets), "text_cells": len(texts), "numeric_cells": len(numbers)}))
`;
  const result = await spawnCollect(python, ["-c", validation, filePath, JSON.stringify(expectedStrings), JSON.stringify(expectedNumbers), JSON.stringify(includedDescription)], { cwd: root });
  assert(result.code === 0, `XLSX validation failed: ${safeErrorText(result.stderr || result.stdout)}`);
  return JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1));
}

async function validatePdf(filePath) {
  const bytes = await fs.readFile(filePath);
  assert(bytes.subarray(0, 5).toString("ascii") === "%PDF-", "Generated PDF has no PDF signature.");
  assert(bytes.includes(Buffer.from("%%EOF")), "Generated PDF has no EOF marker.");
  assert(bytes.length > 512, "Generated PDF is unexpectedly small.");
  const validation = `import json, sys
try:
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(sys.argv[1])
    page_count = len(document)
    if page_count < 1:
        raise ValueError("PDF has no pages")
    bitmap = document[0].render(scale=1.0, rotation=0)
    if bitmap.width <= 0 or bitmap.height <= 0:
        raise ValueError("PDF first page did not render")
    print(json.dumps({"status": "valid", "pages": page_count, "rendered_first_page": True}))
except Exception as error:
    sys.stderr.write(type(error).__name__)
    raise SystemExit(1)
`;
  const result = await spawnCollect(python, ["-c", validation, filePath], { cwd: root });
  assert(result.code === 0, `PDF parsing/rendering failed: ${safeErrorText(result.stderr || result.stdout)}`);
  return { ...JSON.parse(result.stdout.trim().split(/\r?\n/).at(-1)), bytes: bytes.length };
}

async function verifyPdfValidatorRejectsMalformedControl(downloadsRoot) {
  const malformedPath = path.join(downloadsRoot, "malformed-pdf-negative-control.pdf");
  const malformed = Buffer.alloc(600, 0x61);
  malformed.write("%PDF-1.4\n", 0, "ascii");
  malformed.write("%%EOF", malformed.length - 5, "ascii");
  await fs.writeFile(malformedPath, malformed);
  try {
    let rejected = false;
    try {
      await validatePdf(malformedPath);
    } catch {
      rejected = true;
    }
    assert(rejected, "PDF parser accepted a marker-only malformed PDF negative control.");
  } finally {
    await fs.rm(malformedPath, { force: true });
  }
}

async function main() {
  verifyGenerationRunIdBindingControls();
  let runRoot = "";
  let harness;
  let browser;
  let context;
  let reopenContext;
  let api;
  let anonymousApi;
  try {
    const referenceConfig = JSON.parse(await fs.readFile(path.join(fixtureDir, "reference.json"), "utf8"));
    const catalog = JSON.parse(await fs.readFile(path.join(fixtureDir, "pricing-catalog.json"), "utf8"));
    assert(Array.isArray(catalog.items) && catalog.items.length >= 2, "Synthetic pricing fixture has fewer than two catalog items.");
    const usableItems = catalog.items.filter((item) => {
      const price = Number(item.sale_unit_price ?? item.default_quote_amount);
      return item && item.id && item.description && item.section && item.unit_hint && Number.isFinite(price) && price >= 0;
    });
    const first = usableItems.find((item) => item.unit_hint === "sqm") || usableItems[0];
    const second = usableItems.find((item) => item.id !== first.id && item.section !== first.section) || usableItems.find((item) => item.id !== first.id);
    assert(first && second, "Synthetic pricing fixture does not contain two usable items.");
    const fixtureItems = [first, second].map((item) => ({ ...item, price: Number(item.sale_unit_price ?? item.default_quote_amount) }));

    runRoot = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-a-"));
    const dataRoot = path.join(runRoot, "d");
    const outputRoot = path.join(runRoot, "o");
    const tmpRoot = path.join(runRoot, "t");
    const localPricingRoot = path.join(runRoot, "p");
    const fixtureCopy = path.join(localPricingRoot, referenceConfig.id);
    const downloadsRoot = path.join(runRoot, "x");
    await Promise.all([
      fs.mkdir(dataRoot, { recursive: true }), fs.mkdir(outputRoot, { recursive: true }),
      fs.mkdir(tmpRoot, { recursive: true }), fs.mkdir(serverLogDir, { recursive: true }),
      fs.mkdir(browserLogDir, { recursive: true }), fs.mkdir(localPricingRoot, { recursive: true }),
      fs.mkdir(downloadsRoot, { recursive: true }),
    ]);
    await verifyPdfValidatorRejectsMalformedControl(downloadsRoot);
    await fs.cp(fixtureDir, fixtureCopy, { recursive: true });

    let harnessSource = await fs.readFile(path.join(root, "tests", "internal_google_browser_harness.py"), "utf8");
    const rootMarker = "ROOT = Path(__file__).resolve().parents[1]";
    const appModeMarker = '"APP_MODE": "deploy",';
    const harnessNewline = harnessSource.includes(String.fromCharCode(13,10)) ? String.fromCharCode(13,10) : String.fromCharCode(10);
    const resetMarker = "    webapp.INTERNAL_AUTH_STATE.reset()" + harnessNewline + "    webapp.is_allowed_host_header = lambda _host: True";
    assert(harnessSource.split(rootMarker).length === 2, "Synthetic auth harness root marker changed; refusing a mismatched fixture setup.");
    assert(harnessSource.split(appModeMarker).length === 2, "Synthetic auth harness app-mode marker changed; refusing a mismatched fixture setup.");
    assert(harnessSource.split(resetMarker).length === 2, "Synthetic auth harness setup marker changed; refusing a mismatched fixture setup.");
    harnessSource = harnessSource
      .replace(rootMarker, "ROOT = Path(sys.argv[1]).resolve()")
      .replace(appModeMarker, '"APP_MODE": "local",')
      .replace(resetMarker, `    os.environ.update({
            "SQAG_DISABLE_DOTENV": "1",
            "QUOTE_DATA_ROOT": sys.argv[2],
            "QUOTE_OUTPUT_ROOT": sys.argv[3],
            "QUOTE_TMP_ROOT": sys.argv[4],
            "QUOTE_LOG_ROOT": sys.argv[5],
            "SQAG_LOCAL_PRICING_REFERENCES_ROOT": sys.argv[6],
            "OPENAI_API_KEY": "",
            "DEEPSEEK_API_KEY": "",
        })
    _alpha_default_runtime_workspace = webapp.default_runtime_workspace
    def _alpha_local_runtime_workspace():
        value = _alpha_default_runtime_workspace()
        for key in ("company", "workspace"):
            target = value.get(key)
            if isinstance(target, dict):
                target["id"] = "workspace-internal-alpha"
                target["slug"] = "workspace-internal-alpha"
                target["display_name"] = "Synthetic Internal Alpha"
        return value
    webapp.default_runtime_workspace = _alpha_local_runtime_workspace
    webapp.INTERNAL_AUTH_STATE.reset()
    webapp.is_allowed_host_header = lambda _host: True
`);
    const shimPath = path.join(runRoot, "internal_google_browser_harness.py");
    await fs.writeFile(shimPath, harnessSource, "utf8");

    harness = startHarness(shimPath, [dataRoot, outputRoot, tmpRoot, serverLogDir, localPricingRoot], root);
    const baseUrl = await harness.baseUrlPromise;
    const health = await fetch(`${baseUrl}/api/health`);
    assert(health.ok && (await health.json()).status === "ok", "Synthetic alpha server health check failed.");

    browser = await chromium.launch({ headless: true });
    context = await browser.newContext({ acceptDownloads: true });
    let page = await context.newPage();
    attachTrace(page, baseUrl);
    anonymousApi = await request.newContext();
    const anonymousSessions = await anonymousApi.get(`${baseUrl}/api/quote-sessions`);
    assert(anonymousSessions.status() === 401, `Anonymous protected quote-session request returned HTTP ${anonymousSessions.status()}.`);

    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded", timeout: 20000 });
    const authState = await waitUntil(async () => {
      const response = await page.evaluate(async () => {
        const result = await fetch("/api/session");
        return { status: result.status, body: await result.json() };
      });
      return response.status === 200 && response.body.authenticated ? response : false;
    }, "synthetic internal authentication", 20000);
    assert(authState.body.user?.subject === "synthetic-browser-subject", "Synthetic auth established an unexpected user identity.");
    assert(authState.body.user?.account === workspaceId, "Synthetic auth did not establish the expected workspace.");
    assert(authState.body.permissions?.canGenerateQuote === true, "Synthetic authenticated user cannot generate quotes.");

    api = await request.newContext({ storageState: await context.storageState() });
    const profileData = await getJson(await api.get(`${baseUrl}/api/profiles`), "Authenticated profile/workspace context");
    assert(profileData.workspace?.workspace?.id === workspaceId, "Temporary local alpha storage did not resolve the synthetic authenticated workspace.");
    const selectedReference = profileData.pricing_references?.find((item) => item.id === referenceConfig.id && item.source === "local");
    assert(selectedReference?.label === referenceConfig.label, "Synthetic local pricing reference is not available to the authenticated UI.");

    await page.locator("#newQuoteButton").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#newQuoteButton").click();
    await page.locator("#imageIntake").waitFor({ state: "visible", timeout: 15000 });
    const renderSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360"><rect width="640" height="360" fill="#f1eee7"/><rect x="68" y="58" width="500" height="236" rx="10" fill="#d8e0da"/><path d="M68 94h260v200H68z" fill="#aec5ba"/><path d="M328 94h240v200H328z" fill="#d9d2c6"/><rect x="126" y="120" width="170" height="46" rx="4" fill="#e7b75d"/><rect x="386" y="182" width="112" height="70" rx="6" fill="#55766a"/><rect x="102" y="294" width="424" height="28" rx="4" fill="#777c76"/><path d="M126 120h170M386 182h112" stroke="#434b48" stroke-width="4"/></svg>`;
    const logoSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="96" viewBox="0 0 240 96"><rect width="240" height="96" rx="12" fill="#19392f"/><path d="M24 65V31h12l16 20 16-20h12v34H68V48L52 67 36 48v17z" fill="#e1c07a"/><text x="92" y="58" fill="#fff" font-family="Arial,sans-serif" font-size="25" font-weight="700">ALPHA</text></svg>`;
    const fixturePage = await context.newPage();
    const renderBuffer = await makePng(fixturePage, renderSvg);
    const logoBuffer = await makePng(fixturePage, logoSvg);
    await fixturePage.close();
    await page.locator("#imageInput").setInputFiles({ name: "synthetic-booth-render.png", mimeType: "image/png", buffer: renderBuffer });
    await page.locator("#fileList .file-item").filter({ hasText: "synthetic-booth-render.png" }).waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#sideNextButton").click();
    await page.locator("#customerDetailsPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#profileSelect").selectOption({ label: referenceConfig.label });
    const customerFields = [
      ["#clientNameEditor", expectedCustomer], ["#clientAttentionEditor", "Alpha Review Team"],
      ["#clientTitleEditor", "Synthetic Project Lead"], ["#clientAddressEditor", "Synthetic workspace, Singapore"],
      ["#projectTitleEditor", expectedProject], ["#showName", "Synthetic Alpha Showcase"],
      ["#projectNumberEditor", expectedProjectNumber],
    ];
    for (const [selector, value] of customerFields) await page.locator(selector).fill(value);
    await page.locator("#quoteDate").fill(new Date().toISOString().slice(0, 10));
    await page.locator("#sideNextButton").click();
    await page.locator("#quoteCompanyPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#headerLogoInput").setInputFiles({ name: "synthetic-alpha-logo.png", mimeType: "image/png", buffer: logoBuffer });
    const companyFields = [
      ["#headerDetailsEditor", "Synthetic Internal Alpha | Singapore"],
      ["#quoteCompanyNameEditor", "Synthetic Alpha Fabrication"],
      ["#acceptanceTextEditor", "Synthetic approval for testing"],
      ["#companySignatoryEditor", "Synthetic Authorized Signatory"],
      ["#companyTitleEditor", "Project Manager"], ["#companyDateLabelEditor", "Date:"],
      ["#personLabelEditor", "Name:"], ["#stampLabelEditor", "Company stamp"], ["#dateLabelEditor", "Date:"],
    ];
    for (const [selector, value] of companyFields) await page.locator(selector).fill(value);
    const enteredCompanyDetails = await page.evaluate(() => {
      const details = collectQuoteDetails();
      return {
        state: { quoteSessionDraftSaveStarted: state.quoteSessionDraftSaveStarted, sessionId: Boolean(state.quoteSessionId), logo: Boolean(state.headerLogo?.data_url) },
        dom: { headerDetails: document.querySelector("#headerDetailsEditor")?.innerText || "", companyName: document.querySelector("#quoteCompanyNameEditor")?.innerText || "", signatory: document.querySelector("#companySignatoryEditor")?.innerText || "", signatoryTitle: document.querySelector("#companyTitleEditor")?.innerText || "" },
        collected: { headerDetails: details.company?.header_details || "", companyName: details.company?.name || "", signatory: details.signature?.company_signatory || "", signatoryTitle: details.signature?.company_title || "" },
      };
    });
    const createSaveBeforeDashboard = trace.quoteSaveRequests.length;
    await page.locator("#topbarBrandButton").click();
    await page.locator("#quoteDashboardPanel.is-active").waitFor({ state: "visible", timeout: 20000 });
    const hasCompanyDetails = (item) => {
      const details = item.payload?.draft_state?.quoteDetails || {};
      const company = details.company || {};
      const signature = details.signature || {};
      return Boolean(
        (company.logo_session_file_key || company.logo_data_url)
        && String(company.header_details || "").trim()
        && String(company.name || "").trim()
        && String(signature.company_signatory || "").trim()
        && String(signature.company_title || "").trim()
      );
    };
    await waitUntil(() => trace.quoteSaves.some((item) => item.sequence > createSaveBeforeDashboard && item.status === 200 && item.body?.quote_session?.session_id && hasCompanyDetails(item)), "post-form UI quote-session save with entered Quote Company details");
    const createdSave = [...trace.quoteSaves].reverse().find((item) => item.sequence > createSaveBeforeDashboard && item.status === 200 && item.body?.quote_session?.session_id && hasCompanyDetails(item));
    const sessionId = createdSave.body.quote_session.session_id;
    assert(createdSave.payload?.pricing_reference?.id === referenceConfig.id && createdSave.payload?.pricing_reference?.source === "local", "UI-created quote did not retain the selected pricing authority.");
    const authSession = await getJson(await api.get(`${baseUrl}/api/session`), "Authenticated session continuity before fixture setup");
    assert(authSession.authenticated && authSession.user?.account === workspaceId, "Authenticated workspace changed after quote creation.");
    const beforeSeed = await quoteSessionDetail(api, baseUrl, sessionId);
    assert(beforeSeed.session_id === sessionId && beforeSeed.customer_summary?.customer_name === expectedCustomer, "Created quote identity or customer context is incorrect.");
    assert(beforeSeed.pricing_reference?.id === referenceConfig.id && beforeSeed.pricing_reference?.source === "local", "Created quote lost its selected pricing authority.");

    // This is only the deterministic basis fixture for a local synthetic run. The edit and generation actions still use normal UI controls and real application routes.
    const basisSections = fixtureItems.map((item, index) => ({
      id: `alpha-section-${index + 1}`,
      title: item.section,
      lines: [{ id: `alpha-basis-line-${index + 1}`, text: item.description, tag: "Include", confidence: 100, quantity: 1, unit: item.unit_hint }],
    }));
    const referenceSignature = await page.evaluate(() => referenceFilesDependencySignature());
    assert(await page.evaluate((value) => referenceFileSignatureIsStrong(value), referenceSignature), "Synthetic booth image has no stable analysis signature.");
    const seedPayload = structuredClone(createdSave.payload);
    seedPayload.draft_state.quoteBasisSections = basisSections;
    seedPayload.draft_state.lineItems = fixtureItems.map((item, index) => ({
      section: item.section, description: item.description, quantity: 1, unit: item.unit_hint,
      source_basis_line_id: `alpha-basis-line-${index + 1}`,
    }));
    seedPayload.draft_state.originalAnalysisSnapshot = {
      quote_basis_sections: basisSections,
      line_items: seedPayload.draft_state.lineItems,
      source: "edited",
      analysis_mode: "standard",
      reference_file_signature: referenceSignature,
      warnings: [],
    };
    seedPayload.draft_state.outputRows = [];
    seedPayload.draft_state.originalOutputRows = [];
    seedPayload.draft_state.basisConfirmed = false;
    seedPayload.draft_state.aiFailed = false;
    seedPayload.draft_state.workflowStage = "basis_review";
    seedPayload.draft_state.draftSource = "edited";
    const csrf = { header: authSession.csrf_header, token: authSession.csrf_token };
    const seedResponse = await api.post(`${baseUrl}/api/quote-sessions`, {
      data: seedPayload, headers: { Origin: baseUrl, [csrf.header]: csrf.token },
    });
    const seededBody = await seedResponse.json().catch(() => ({}));
    const seedError = String(seededBody.errors?.[0] || seededBody.status || "request rejected");
    assert(seedResponse.ok(), "Authenticated synthetic basis fixture save returned HTTP " + seedResponse.status() + ": " + seedError);
    assert(seededBody.status === "saved" && seededBody.quote_session?.session_id === sessionId, "Initial synthetic basis fixture was not persisted.");
    const seededReadback = await quoteSessionDetail(api, baseUrl, sessionId);
    const savedDetails = seededReadback.draft_state?.quoteDetails || {};
    const savedCompany = savedDetails.company || {};
    const savedSignature = savedDetails.signature || {};
    const detailPresence = { logo: Boolean(savedCompany.logo_session_file_key || savedCompany.logo_data_url), headerDetails: Boolean(String(savedCompany.header_details || "").trim()), companyName: Boolean(String(savedCompany.name || "").trim()), signatory: Boolean(String(savedSignature.company_signatory || "").trim()), signatoryTitle: Boolean(String(savedSignature.company_title || "").trim()) };
    const requestDetails = createdSave.payload?.draft_state?.quoteDetails || {};
    const requestCompany = requestDetails.company || {};
    const requestSignature = requestDetails.signature || {};
    const requestPresence = { draftState: Boolean(createdSave.payload?.draft_state), logo: Boolean(requestCompany.logo_session_file_key || requestCompany.logo_data_url), headerDetails: Boolean(String(requestCompany.header_details || "").trim()), companyName: Boolean(String(requestCompany.name || "").trim()), signatory: Boolean(String(requestSignature.company_signatory || "").trim()), signatoryTitle: Boolean(String(requestSignature.company_title || "").trim()) };
    assert(Object.values(detailPresence).every(Boolean), "UI-entered synthetic Quote Company details did not persist: " + JSON.stringify({ enteredCompanyDetails, readback: detailPresence, request: requestPresence }));
    assert(seededReadback.draft_state?.outputRows?.length === 0, "Fixture setup unexpectedly injected output rows.");
    assert(seededReadback.draft_state?.quoteBasisSections?.length === fixtureItems.length, "Initial basis fixture did not persist.");
    assert(seededReadback.draft_state?.draftSource === "edited" && seededReadback.draft_state?.aiFailed === false && seededReadback.draft_state?.lineItems?.length === fixtureItems.length, "Initial deterministic basis fixture did not persist in a non-failed state.");
    assert(trace.aiDraftRequests.length === 0, "AI analysis was called during the no-provider alpha run.");

    reopenContext = await browser.newContext({ acceptDownloads: true, storageState: await context.storageState() });
    page = await reopenContext.newPage();
    attachTrace(page, baseUrl);
    await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 20000 });
    await page.locator("#dashboardSessionsList [data-quote-session-id]").first().waitFor({ state: "visible", timeout: 20000 });
    const sessionCard = page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`);
    await sessionCard.waitFor({ state: "visible", timeout: 15000 });
    await sessionCard.click();
    await page.locator('#dashboardSelectedSessionPanel [data-dashboard-panel-action="modify-session"]').click();
    try {
      await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 20000 });
    } catch (error) {
      const flowActive = await page.locator("#panel-analysis").evaluate((element) => element.classList.contains("is-active"));
      const restoreMessage = (await page.locator("#dashboardSelectedSessionPanel .dashboard-restore-message").textContent().catch(() => "")) || "";
      const dashboardError = (await page.locator("#dashboardErrorText").textContent().catch(() => "")) || "";
      throw new Error("Saved quote did not reopen: " + JSON.stringify({ flowActive, restoreMessage, dashboardError, original: safeErrorText(error) }));
    }
    await page.locator("#quoteBasisPanel").waitFor({ state: "visible", timeout: 15000 });
    await page.locator("#sideNextButton").waitFor({ state: "visible", timeout: 15000 });
    assert((await page.locator("#sideNextButton").innerText()).includes("Confirm"), "Reopened quote did not restore its basis review stage.");
    const normalizationBefore = trace.normalizations.length;
    await page.locator("#sideNextButton").click();
    try {
      await waitUntil(() => trace.normalizations.length > normalizationBefore, "application quote-basis normalization response");
    } catch (error) {
      const banner = (await page.locator("#aiFailureBanner").innerText().catch(() => "")).trim();
      const result = (await page.locator("#resultStatus").innerText().catch(() => "")).trim();
      const next = await page.locator("#sideNextButton").evaluate((element) => ({ text: element.innerText, title: element.title, disabled: element.getAttribute("aria-disabled") }));
      const stateSummary = await page.evaluate(() => typeof state === "undefined" ? { available: false } : { available: true, aiFailed: state.aiFailed, draftSource: state.draftSource, workflowStage: state.workflowStage, activeSidePanel: state.activeSidePanel, lineItems: state.lineItems?.length, quoteBasisSections: state.quoteBasisSections?.length, quoteSessionId: state.quoteSessionId, appBusy: appIsBusy(), isAnalysisRunning: state.isAnalysisRunning, isGenerating: state.isGenerating, isPreparingOutput: state.isPreparingOutput, quoteSessionRestoreBusy: state.quoteSessionRestoreBusy, quoteSessionDashboardBusy: state.quoteSessionDashboardBusy, missingDetails: missingDetailFields(), basisBlockReason: basisConfirmBlockReason(), commercialReviewRequired: quoteCommercialReviewRequired(), unresolvedConfirmLines: unresolvedConfirmLines().length, activeJob: state.activeJob?.type });
      throw new Error("Normalization not triggered: " + JSON.stringify({ banner, result, next, stateSummary, original: safeErrorText(error) }));
    }
    const normalization = trace.normalizations.at(-1);
    assert(normalization.status === 200 && normalization.body.status === "normalized", "Application quote-basis normalization failed.");
    assert(Array.isArray(normalization.body.line_items) && normalization.body.line_items.length === fixtureItems.length, "Application normalization did not resolve both fixture lines.");
    assert(normalization.payload?.pricing_reference_id === referenceConfig.id || normalization.payload?.pricing_reference?.id === referenceConfig.id, "Normalization used a different pricing authority.");
    await page.locator("#pricingMatchesBody tr").nth(fixtureItems.length - 1).waitFor({ state: "visible", timeout: 15000 });

    const pricedItem = fixtureItems[0];
    const includedItem = fixtureItems[1];
    const pricedRow = page.locator("#pricingMatchesBody tr").filter({ hasText: pricedItem.description }).first();
    const includedRow = page.locator("#pricingMatchesBody tr").filter({ hasText: includedItem.description }).first();
    await pricedRow.waitFor({ state: "visible", timeout: 15000 });
    await includedRow.waitFor({ state: "visible", timeout: 15000 });
    const displayedPrice = Number((await pricedRow.locator('[data-output-edit-field="unit_price_override"] .output-cell-text').innerText()).replace(/[^0-9.]/g, ""));
    assert(Number.isFinite(displayedPrice) && Math.abs(displayedPrice - pricedItem.price) < 0.011, "Visible pricing row does not use the selected synthetic reference price.");
    const saveRequestBeforeEdit = trace.quoteSaveRequests.length;
    await page.evaluate(() => {
      window.__alphaIncludedEvents = [];
      const body = document.querySelector("#pricingMatchesBody");
      for (const type of ["pointerdown", "mousedown", "click"]) {
        const observe = (phase) => (event) => {
          if (!event.target.closest("[data-output-included-action]")) return;
          const button = event.target.closest("[data-output-included-action]");
          const snapshot = { type, phase, eventPhase: event.eventPhase, defaultPrevented: event.defaultPrevented, row: button.dataset.outputRow, targetTag: event.target.tagName, bodyIsAppTarget: body === elements.pricingMatchesBody };
          queueMicrotask(() => window.__alphaIncludedEvents.push({ ...snapshot, defaultPreventedAfter: event.defaultPrevented, priceModes: state.outputRows.map((row) => row.price_mode) }));
        };
        body.addEventListener(type, observe("capture"), true);
        body.addEventListener(type, observe("bubble"));
      }
    });
    await pricedRow.locator('[data-output-edit-field="quantity"]').click();
    const quantityEditor = pricedRow.locator('[data-output-editor-field="quantity"]');
    await quantityEditor.fill("2");
    await quantityEditor.press("Enter");
    await pricedRow.locator('[data-output-edit-field="quantity"]').filter({ hasText: "2" }).waitFor({ state: "visible", timeout: 10000 });
    await includedRow.locator('[data-output-edit-field="unit_price_override"]').click();
    await includedRow.locator("[data-output-included-action]").click();
    await includedRow.locator("[data-output-included-action]").waitFor({ state: "detached", timeout: 10000 });
    await includedRow.locator('[data-output-edit-field="unit_price_override"]').filter({ hasText: "Included" }).waitFor({ state: "visible", timeout: 10000 });
    const includedAmountCell = includedRow.locator(".amount-cell");
    await includedAmountCell.waitFor({ state: "visible", timeout: 10000 });
    const includedAmountText = (await includedAmountCell.innerText()).trim();
    const includedAmountValue = Number(includedAmountText.replace(/[^0-9.-]/g, ""));
    assert(Number.isFinite(includedAmountValue) && Math.abs(includedAmountValue) < 0.005, "Included row amount did not render as zero: " + includedAmountText);
    const editedState = await page.evaluate((descriptions) => ({
      rows: state.outputRows.map((row) => ({ description: row.description, quantity: row.quantity, price_mode: row.price_mode })),
      includedEvents: window.__alphaIncludedEvents || [],
      quoteSessionDraftSaveStarted: state.quoteSessionDraftSaveStarted,
      canSave: quoteSessionDraftStateCanSave(),
      isRecoveryScopeTransitioning: state.isRecoveryScopeTransitioning,
      outputRevision: state.outputRevision,
    }), [pricedItem.description, includedItem.description]);
    assert(editedState.rows.find((row) => row.description === includedItem.description)?.price_mode === "Included", "Visible Included action did not update current app state: " + JSON.stringify(editedState));
    assert(editedState.includedEvents.some((event) => event.type === "click" && event.phase === "capture"), "Included action click did not reach the live pricing table.");
    assert(editedState.includedEvents.some((event) => event.type === "click" && event.phase === "bubble"), "Included action click did not bubble through the live pricing table.");
    const expectedLineAmount = Math.round(pricedItem.price * 2 * 100) / 100;
    const latestEditSaved = (item) => {
      const rows = item.payload?.draft_state?.outputRows || [];
      return item.status === 200 && item.body?.status === "saved"
        && item.body?.quote_session?.session_id === sessionId
        && Number(rows.find((row) => row.description === pricedItem.description)?.quantity) === 2
        && rows.find((row) => row.description === includedItem.description)?.price_mode === "Included";
    };
    await page.locator("#topbarBrandButton").click();
    await page.locator("#quoteDashboardPanel.is-active").waitFor({ state: "visible", timeout: 20000 });
    try {
      await waitUntil(() => trace.quoteSaves.some((item) => item.sequence > saveRequestBeforeEdit && latestEditSaved(item)), "successful save of both visible commercial edits");
    } catch (error) {
      const savedRows = trace.quoteSaves.filter((item) => item.sequence > saveRequestBeforeEdit).map((item) => ({ sequence: item.sequence, status: item.status, bodyStatus: item.body?.status, sessionId: item.body?.quote_session?.session_id, topRows: item.payload?.draft_state?.outputRows?.map((row) => ({ description: row.description, quantity: row.quantity, price_mode: row.price_mode })) || [], nestedRows: item.payload?.quote_session?.draft_state?.outputRows?.map((row) => ({ description: row.description, quantity: row.quantity, price_mode: row.price_mode })) || [] }));
      throw new Error("Visible edits did not reach a successful save: " + JSON.stringify({ saves: savedRows.slice(-5), requests: trace.quoteSaveRequests.filter((record) => record.sequence > saveRequestBeforeEdit).map((record) => (record.payload?.draft_state?.outputRows || []).map((row) => ({ description: row.description, quantity: row.quantity, price_mode: row.price_mode }))), editedState, original: safeErrorText(error) }));
    }
    const editSave = [...trace.quoteSaves].reverse().find((item) => item.sequence > saveRequestBeforeEdit && latestEditSaved(item));
    assert(editSave.payload?.pricing_reference?.id === referenceConfig.id && editSave.payload?.pricing_reference?.source === "local", "Visible edit save lost pricing-reference authority.");

    const durableAfterEdit = await quoteSessionDetail(api, baseUrl, sessionId);
    const durableRows = durableAfterEdit.draft_state?.outputRows || [];
    assert(Number(durableRows.find((row) => row.description === pricedItem.description)?.quantity) === 2, "Independent readback lost the latest visible quantity.");
    assert(durableRows.find((row) => row.description === includedItem.description)?.price_mode === "Included", "Independent readback lost the visible Included-row edit.");
    assert(durableAfterEdit.pricing_reference?.id === referenceConfig.id && durableAfterEdit.pricing_reference?.source === "local", "Durable readback changed the pricing authority.");

    const editCard = page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`);
    await editCard.waitFor({ state: "visible", timeout: 15000 });
    await editCard.click();
    await page.locator('#dashboardSelectedSessionPanel [data-dashboard-panel-action="modify-session"]').click();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 20000 });
    const reopenedPricedRow = page.locator("#pricingMatchesBody tr").filter({ hasText: pricedItem.description }).first();
    const reopenedIncludedRow = page.locator("#pricingMatchesBody tr").filter({ hasText: includedItem.description }).first();
    await reopenedPricedRow.waitFor({ state: "visible", timeout: 15000 });
    await reopenedIncludedRow.waitFor({ state: "visible", timeout: 15000 });
    assert((await reopenedPricedRow.locator('[data-output-edit-field="quantity"]').innerText()).trim() === "2", "Refresh/reopen made an earlier quantity current.");
    assert((await reopenedIncludedRow.locator('[data-output-edit-field="unit_price_override"]').innerText()).includes("Included"), "Refresh/reopen lost the Included-row edit.");
    assert(await page.locator("#sideDownloadButton").getAttribute("aria-disabled") !== "true", "Current XLSX action is not enabled after reopen.");

    const firstJobIndex = trace.jobs.length;
    const xlsxTerminalPromise = waitUntil(() => {
      const job = trace.jobs.slice(firstJobIndex).find((item) => item.type === "generate");
      return job?.polls.find((poll) => ["completed", "failed", "needs_review"].includes(poll.body?.status)) || null;
    }, "terminal XLSX generation result", 90000);
    const xlsxDownloadPromise = page.waitForEvent("download", { timeout: 90000 });
    await page.locator("#sideDownloadButton").click();
    const xlsxOutcome = await Promise.race([
      xlsxDownloadPromise.then((download) => ({ kind: "download", download })),
      xlsxTerminalPromise.then((poll) => ({ kind: "terminal", poll })),
    ]);
    if (xlsxOutcome.kind === "terminal" && xlsxOutcome.poll.body?.status !== "completed") {
      const xlsxJob = trace.jobs.slice(firstJobIndex).find((item) => item.type === "generate");
      const terminal = xlsxOutcome.poll.body;
      const result = terminal?.result || {};
      const matchedRows = (result.pricing_matches || []).map((row) => ({ status: row.status, description: row.description, amount: row.amount }));
      throw new Error(`Real XLSX generation ended ${terminal?.status || "unknown"}: ${JSON.stringify({ errors: result.errors || terminal.errors || [], matchedRows, job: xlsxJob?.accepted?.job_id || "" })}`);
    }
    const xlsxDownload = xlsxOutcome.kind === "download" ? xlsxOutcome.download : await xlsxDownloadPromise;
    const xlsxPath = path.join(downloadsRoot, "first-current-quotation.xlsx");
    await xlsxDownload.saveAs(xlsxPath);
    await page.locator("#outputStatusPill").waitFor({ state: "visible", timeout: 30000 });
    assert(/Completed/i.test(await page.locator("#resultStatus").innerText()), "UI did not report a completed XLSX generation.");
    assert(/^Ready$/i.test((await page.locator("#outputStatusPill").innerText()).trim()), "Output status did not return to Ready after XLSX generation.");
    await waitUntil(() => trace.jobs.length > firstJobIndex && trace.jobs.slice(firstJobIndex).some((job) => job.type === "generate"), "real XLSX generation job");
    const xlsxJob = trace.jobs.slice(firstJobIndex).find((job) => job.type === "generate");
    const xlsxTerminalPoll = xlsxOutcome.kind === "terminal" ? xlsxOutcome.poll : await xlsxTerminalPromise;
    const xlsxTerminal = xlsxTerminalPoll.body;
    assert(xlsxTerminal.status === "completed" && xlsxTerminal.result?.status === "completed", "Real XLSX generation did not complete.");
    assert(xlsxTerminal.result?.quote_session?.session_id === sessionId, "XLSX result belongs to a different quote.");
    const resultRunIdXlsx = requireGenerationRunIdBinding(xlsxJob?.accepted, xlsxTerminal, "XLSX");
    const xlsxJobRequest = xlsxJob.request?.payload || {};
    assert(xlsxJobRequest.quote_session?.session_id === sessionId, "XLSX request lost quote-session identity.");
    assert(xlsxJobRequest.pricing_reference?.id === referenceConfig.id && xlsxJobRequest.pricing_reference_source === "local", "XLSX request lost pricing-reference authority.");
    assert(Array.isArray(xlsxJobRequest.line_items) && xlsxJobRequest.line_items.length === fixtureItems.length, "XLSX request did not contain normalized line items.");

    const xlsxReadback = await quoteSessionDetail(api, baseUrl, sessionId);
    assert(xlsxReadback.exports?.xlsx?.exists === true && xlsxReadback.exports.xlsx.stale === false, "Generated XLSX is not current.");
    const xlsxJobSession = xlsxTerminal.result.quote_session;
    assert(xlsxJobSession.exports?.xlsx?.sha256 === xlsxReadback.exports.xlsx.sha256, "XLSX result and independent readback differ on artifact hash.");
    assert(xlsxJobSession.exports?.xlsx?.publication_id === xlsxReadback.exports.xlsx.publication_id, "XLSX result and readback differ on publication identity.");
    assert(xlsxReadback.exports.xlsx.publication_id, "Published XLSX has no publication identity.");
    const downloadedXlsxDigest = createHash("sha256").update(await fs.readFile(xlsxPath)).digest("hex");
    assert(downloadedXlsxDigest === xlsxReadback.exports.xlsx.sha256, "UI-downloaded XLSX differs from the published artifact.");
    const xlsxValidation = await validateXlsx(xlsxPath, [
      expectedCustomer, expectedProject, expectedProjectNumber, ...fixtureItems.map((item) => item.description),
    ], [expectedLineAmount, Math.round(expectedLineAmount * 1.09 * 100) / 100], includedItem.description);

    const pdfPopupRequests = [];
    const pdfPopupResponses = [];
    const observedPdfPopups = [];
    page.on("popup", (popup) => {
      observedPdfPopups.push(popup);
      popup.on("request", (request) => pdfPopupRequests.push(request.url()));
      popup.on("response", (response) => pdfPopupResponses.push({
        url: response.url(),
        status: response.status(),
        contentType: response.headers()["content-type"] || "",
        contentDisposition: response.headers()["content-disposition"] || "",
      }));
    });
    const pdfJobIndex = trace.jobs.length;
    const popupPromise = page.waitForEvent("popup", { timeout: 90000 }).then((popup) => ({ popup }), (error) => ({ error }));
    await page.locator("#sideViewPdfButton").click();
    const popupResult = await popupPromise;
    assert(popupResult.popup, `View PDF did not open a popup: ${popupResult.error?.message || "unknown browser error"}.`);
    const pdfPopup = popupResult.popup;
    await pdfPopup.waitForLoadState("domcontentloaded", { timeout: 30000 }).catch(() => {});
    await waitUntil(() => trace.jobs.length > pdfJobIndex && trace.jobs.slice(pdfJobIndex).some((job) => job.type === "generate_pdf"), "real PDF generation job");
    const pdfJob = trace.jobs.slice(pdfJobIndex).find((job) => job.type === "generate_pdf");
    await waitUntil(() => pdfJob.polls.some((poll) => ["completed", "failed", "needs_review"].includes(poll.body?.status)), "terminal PDF generation result", 90000);
    const pdfTerminal = [...pdfJob.polls].reverse().find((poll) => ["completed", "failed", "needs_review"].includes(poll.body?.status)).body;
    assert(pdfTerminal.status === "completed" && pdfTerminal.result?.status === "completed", "Real application PDF generation did not complete.");
    assert(pdfTerminal.result?.quote_session?.session_id === sessionId, "PDF result belongs to a different quote.");
    const resultRunIdPdf = requireGenerationRunIdBinding(pdfJob?.accepted, pdfTerminal, "PDF");
    const afterPdf = await quoteSessionDetail(api, baseUrl, sessionId);
    const pdfJobSession = pdfTerminal.result.quote_session;
    const pdfViewUrl = afterPdf.exports?.pdf?.view_url;
    assert(pdfViewUrl && pdfViewUrl === pdfJobSession.exports?.pdf?.view_url, "Completed PDF result did not publish the current protected inline view URL.");
    const expectedPdfViewUrl = new URL(pdfViewUrl, baseUrl).href;
    await waitUntil(() => pdfPopupResponses.some((response) => response.url === expectedPdfViewUrl), "protected inline PDF preview response", 30000);
    const pdfPopupUrl = await pdfPopup.url();
    const sidePdfHref = await page.locator("#sideViewPdfButton").getAttribute("href").catch(() => null);
    const pdfViewResponse = pdfPopupResponses.find((response) => response.url === expectedPdfViewUrl);
    // Chromium headless keeps the popup at about:blank for its native PDF viewer; verify the protected inline response instead.
    assert(sidePdfHref === pdfViewUrl && pdfViewResponse?.status === 200 && pdfViewResponse.contentType.toLowerCase().includes("application/pdf") && pdfViewResponse.contentDisposition.toLowerCase().startsWith("inline;") && pdfPopupRequests.includes(expectedPdfViewUrl) && observedPdfPopups.length === 1, `View PDF did not request the persisted protected artifact inline (popup=${pdfPopupUrl}, expected=${expectedPdfViewUrl}, sideHref=${sidePdfHref}, response=${JSON.stringify(pdfViewResponse)}, requests=${JSON.stringify(pdfPopupRequests)}, popups=${observedPdfPopups.length}).`);
    assert(afterPdf.status?.quote_generated === true, "Completed result was not persisted as current.");
    assert(afterPdf.exports?.xlsx?.exists === true && afterPdf.exports.xlsx.stale === false, "Post-PDF quote has no current XLSX.");
    assert(afterPdf.exports?.pdf?.exists === true && afterPdf.exports.pdf.stale === false, "Post-PDF quote has no current PDF.");
    const publicationId = afterPdf.exports.xlsx.publication_id;
    assert(publicationId && afterPdf.exports.pdf.publication_id === publicationId, "XLSX and PDF do not share publication identity.");
   for (const kind of ["xlsx", "pdf"]) {
      assert(pdfJobSession.exports?.[kind]?.sha256 === afterPdf.exports?.[kind]?.sha256, `PDF result and readback disagree on current ${kind} hash.`);
      assert(pdfJobSession.exports?.[kind]?.publication_id === afterPdf.exports?.[kind]?.publication_id, `PDF result and readback disagree on current ${kind} publication.`);
    }
    const currentXlsxPath = path.join(downloadsRoot, "current-quotation.xlsx");
    const currentPdfPath = path.join(downloadsRoot, "current-quotation.pdf");
    const currentXlsx = await authenticatedDownload(api, baseUrl, afterPdf.exports.xlsx, currentXlsxPath, "XLSX");
    const currentPdf = await authenticatedDownload(api, baseUrl, afterPdf.exports.pdf, currentPdfPath, "PDF");
    assert(currentXlsx.contentType.includes("spreadsheetml") || currentXlsx.contentType.includes("application/vnd"), "Protected XLSX route returned the wrong content type.");
    assert(currentPdf.contentType.includes("application/pdf"), "Protected PDF route returned the wrong content type.");
    const currentXlsxValidation = await validateXlsx(currentXlsxPath, [
      expectedCustomer, expectedProject, expectedProjectNumber, ...fixtureItems.map((item) => item.description),
    ], [expectedLineAmount, Math.round(expectedLineAmount * 1.09 * 100) / 100], includedItem.description);
    const pdfValidation = await validatePdf(currentPdfPath);
    await pdfPopup.close();

    await page.locator("#topbarBrandButton").click();
    await page.locator("#quoteDashboardPanel.is-active").waitFor({ state: "visible", timeout: 20000 });
    const currentCard = page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`);
    await currentCard.waitFor({ state: "visible", timeout: 15000 });
    await currentCard.click();
    await page.locator('#dashboardSelectedSessionPanel [data-dashboard-panel-action="modify-session"]').click();
    await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 20000 });
    const currentPricedRow = page.locator("#pricingMatchesBody tr").filter({ hasText: pricedItem.description }).first();
    const currentIncludedRow = page.locator("#pricingMatchesBody tr").filter({ hasText: includedItem.description }).first();
    await currentPricedRow.waitFor({ state: "visible", timeout: 15000 });
    await currentIncludedRow.waitFor({ state: "visible", timeout: 15000 });
    assert((await currentPricedRow.locator('[data-output-edit-field="quantity"]').innerText()).trim() === "2", "Post-generation reopen resurrected a stale quantity.");
    assert((await currentIncludedRow.locator('[data-output-edit-field="unit_price_override"]').innerText()).includes("Included"), "Post-generation reopen lost the Included edit.");
    const currentXlsxUrl = new URL(afterPdf.exports.xlsx.url, baseUrl).toString();
    const currentPdfUrl = new URL(afterPdf.exports.pdf.url, baseUrl).toString();
    assert(new URL(await page.locator("#sideDownloadButton").getAttribute("href"), baseUrl).toString() === currentXlsxUrl, "Reopened quote does not point to current XLSX.");
    const currentPdfViewUrl = new URL(afterPdf.exports.pdf.view_url, baseUrl).toString();
    assert(new URL(await page.locator("#sideViewPdfButton").getAttribute("href"), baseUrl).toString() === currentPdfViewUrl, "Reopened quote does not point to the current protected PDF view.");
    const postGenerationState = await quoteSessionDetail(api, baseUrl, sessionId);
    assert(postGenerationState.exports?.xlsx?.sha256 === currentXlsx.sha256 && postGenerationState.exports?.pdf?.sha256 === currentPdf.sha256, "Post-generation readback changed artifact identity.");
    assert(Number(postGenerationState.draft_state?.outputRows?.find((row) => row.description === pricedItem.description)?.quantity) === 2, "Post-generation durable state changed the latest edit.");

    const authorizedSessionBeforeLogout = await api.get(baseUrl + "/api/session");
    assert(authorizedSessionBeforeLogout.status() === 200, "Authenticated API context lost its session before logout.");
    assert((await authorizedSessionBeforeLogout.json()).authenticated === true, "Authenticated API context was not authenticated before logout.");
    const authorizedPdfViewBeforeLogout = await api.get(currentPdfViewUrl);
    assert(authorizedPdfViewBeforeLogout.status() === 200, "Authenticated API context could not read the current inline PDF before logout.");
    assert((authorizedPdfViewBeforeLogout.headers()["content-type"] || "").toLowerCase().includes("application/pdf"), "Authorized inline PDF response had the wrong content type.");
    assert((authorizedPdfViewBeforeLogout.headers()["content-disposition"] || "").toLowerCase().startsWith("inline;"), "Authorized inline PDF response was not inline.");
    await authorizedPdfViewBeforeLogout.dispose();

    const logoutPromise = page.waitForResponse((response) => new URL(response.url()).pathname === "/logout" && response.request().method() === "POST", { timeout: 15000 });
    await page.locator("#topbarLogoutLink").click();
    const logoutResponse = await logoutPromise;
    assert(logoutResponse.status() === 204, "Application logout returned HTTP " + logoutResponse.status() + ".");
    await page.waitForURL("**/signed-out", { timeout: 15000 });
    assert((await api.get(baseUrl + "/api/session")).status() === 401, "Retained authenticated session cookie was not revoked after logout.");
    assert((await api.get(baseUrl + "/api/quote-sessions")).status() === 401, "Retained authenticated quote-session request was not denied after logout.");
    assert((await api.get(currentXlsxUrl)).status() === 401, "Retained authenticated XLSX request was not denied after logout.");
    assert((await api.get(currentPdfUrl)).status() === 401, "Retained authenticated PDF request was not denied after logout.");
    assert((await api.get(currentPdfViewUrl)).status() === 401, "Retained authenticated inline PDF view was not denied after logout.");

    await Promise.allSettled(trace.pending);
    assert(trace.externalRequests.length === 0, `Browser requested external origins: ${[...new Set(trace.externalRequests)].join(", ")}`);
    assert(trace.aiDraftRequests.length === 0, "AI draft endpoint was called despite provider calls being unauthorized.");
    assert(trace.consoleProblems.length === 0, `Browser console errors/warnings: ${trace.consoleProblems.slice(0, 5).join(" | ")}`);
    assert(trace.pageErrors.length === 0, `Browser runtime errors: ${trace.pageErrors.slice(0, 5).join(" | ")}`);

    console.log(JSON.stringify({
      result: "LOCAL_SYNTHETIC_ALPHA_A1_A10_PASS",
      workspace: workspaceId,
      quote_session_id: sessionId,
      pricing_reference: { id: referenceConfig.id, source: "local", label: referenceConfig.label },
      synthetic_basis_items: fixtureItems.map((item) => ({ section: item.section, description: item.description, unit: item.unit_hint, sale_unit_price: item.price })),
      visible_edit: { quantity: 2, included_row: includedItem.description, amount: 0 },
      generations: {
        xlsx: { run_id: resultRunIdXlsx, status: xlsxTerminal.status, initial_download_sha256: downloadedXlsxDigest },
        pdf: { run_id: resultRunIdPdf, status: pdfTerminal.status, publication_id: publicationId },
      },
      artifacts: {
        xlsx: { sha256: currentXlsx.sha256, bytes: currentXlsx.size, validation: currentXlsxValidation },
        pdf: { sha256: currentPdf.sha256, bytes: currentPdf.size, validation: pdfValidation },
      },
      matrix: {
        A1: "PASS - synthetic auth, expected workspace, anonymous protected request denied",
        A2: "PASS - UI-created quote reopened in the synthetic workspace",
        A3: "PASS - visible quantity edit and Included row use selected pricing reference",
        A4: "PASS - UI save response, independent readback, dashboard reopen, latest edit current",
        A5: "PASS - real normalize/generation routes; no AI provider request; context retained",
        A6: "PASS - accepted, terminal, and result generation run IDs match the expected quote",
        A7: "PASS - UI XLSX download matches current hash, content, and totals",
        A8: "PASS - protected current PDF download parsed and its first page rendered",
        A9: "PASS - post-generation reopen retains edit, publication, and both artifact URLs",
        A10: "PASS - logout followed by fresh state/XLSX/PDF requests returns 401",
      },
      authority: {
        mode: "explicit local synthetic auth and temporary local JSON/artifact roots",
        live_provider_called: false,
        hosted_oidc_finality_claimed: false,
        hosted_database_or_object_storage_claimed: false,
        accepted_g2_backend_evidence_reused: true,
      },
    }, null, 2));
  } finally {
    if (api) await api.dispose().catch(() => {});
    if (anonymousApi) await anonymousApi.dispose().catch(() => {});
    if (reopenContext) await reopenContext.close().catch(() => {});
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    await stopHarness(harness).catch(() => {});
    if (runRoot) {
      const tempRoot = path.resolve(os.tmpdir()) + path.sep;
      const target = path.resolve(runRoot);
      if (target.startsWith(tempRoot) && path.basename(target).startsWith("sqag-a-")) {
        await fs.rm(target, { recursive: true, force: true }).catch(() => {});
      }
    }
  }
}

main().catch((error) => {
  console.error(error?.stack || error?.message || error);
  process.exitCode = 1;
});
