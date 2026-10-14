import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

for (const key of Object.keys(process.env)) {
  if (/(API_KEY|TOKEN|SECRET|CREDENTIAL)/i.test(key)) process.env[key] = "";
}
// Portable synthetic-only production-handler smoke; caller owns its output and watchdog.
const sourceRoot = fileURLToPath(new URL("..", import.meta.url));
const pinnedRequire = createRequire(path.join(sourceRoot,"package.json"));
const { chromium, request } = pinnedRequire("playwright");
const packageInfo = JSON.parse(await fs.readFile(path.join(sourceRoot,"node_modules/playwright/package.json"), "utf8"));
if (packageInfo.version !== "1.60.0") throw new Error("Expected pinned Playwright 1.60.0; found " + packageInfo.version);

const assert = (ok, message) => { if (!ok) throw new Error(message); };
const evidenceRoot = process.env.SQAG_ALPHA1_EVIDENCE_ROOT || path.join(sourceRoot,"_logs/browser/alpha1-fresh");
if (!evidenceRoot || !path.isAbsolute(evidenceRoot)) throw new Error("SQAG_ALPHA1_EVIDENCE_ROOT must be an absolute, caller-qualified evidence path.");
const runRoot = path.join(evidenceRoot, "fresh-path");
const dirs = {
  data: path.join(runRoot, "data"), output: path.join(runRoot, "output"), temp: path.join(runRoot, "temp"),
  pricing: path.join(runRoot, "pricing"), profiles: path.join(runRoot, "profiles"),
  logs: path.join(runRoot, "logs"), downloads: path.join(runRoot, "downloads"), captures: path.join(runRoot, "captures"),
};
await Promise.all(Object.values(dirs).map((directory) => fs.mkdir(directory, { recursive: true })));
const writeJson = (file, value) => fs.writeFile(path.join(runRoot, file), JSON.stringify(value, null, 2) + "\n");
let stdoutLog = "";
let stderrLog = "";
async function emitStdout(value) {
  const text = String(value) + "\n"; stdoutLog += text;
  await fs.writeFile(path.join(dirs.logs, "runner.stdout.log"), stdoutLog);
  process.stdout.write(text);
}
async function emitStderr(value) {
  const text = String(value) + "\n"; stderrLog += text;
  await fs.writeFile(path.join(dirs.logs, "runner.stderr.log"), stderrLog);
  process.stderr.write(text);
}
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const fixtureId = "synthetic-exhibition-fixture-pricing";
const profileId = "synthetic-exhibition-fixture-template";
const expectedCompany = {
  header_details: "Synthetic Internal Alpha1 | Singapore",
  name: "Synthetic Alpha1 Fabrication",
  company_signatory: "Synthetic Authorized Signatory",
  company_title: "Project Manager",
};
const fixtureDir = path.join(sourceRoot, "tests/fixtures/quote-generator/pricing-references", fixtureId);
const profileDir = path.join(sourceRoot, "tests/fixtures/quote-generator/profiles", profileId);
const trace = { saves: [], saveRequests: [], normalizations: [], jobs: [], external: [], draftRequests: [], console: [], pageErrors: [], pending: [] };
const saveRecords = new WeakMap();
const draftRecords = new WeakMap();
let draftSequence = 0;
let saveSequence = 0;
let browser;
let context;
let page;
let api;
let anonymousApi;
let server;
let serverOut = "";
let serverErr = "";
let serverLineBuffer = "";

function safeJson(req) { try { return req.postDataJSON(); } catch { return null; } }
function attachTrace(target, baseUrl) {
  target.on("console", (msg) => { if (["error", "warning"].includes(msg.type())) trace.console.push(msg.text()); });
  target.on("pageerror", (error) => trace.pageErrors.push(error.message));
  target.on("request", (req) => {
    const u = new URL(req.url());
    if (u.origin !== baseUrl) trace.external.push(u.origin);
    const envelope = safeJson(req);
    if (u.origin === baseUrl && req.method() === "POST" && (u.pathname === "/api/draft" || (u.pathname === "/api/jobs" && envelope?.type === "draft"))) {
      const record = { sequence: ++draftSequence, url: req.url(), method: req.method(), pathname: u.pathname, rawEnvelope: req.postData(), envelope, payload: u.pathname === "/api/jobs" ? envelope.payload : envelope };
      draftRecords.set(req, record); trace.draftRequests.push(record);
    }
    if (u.origin === baseUrl && u.pathname === "/api/quote-sessions" && req.method() === "POST") {
      const record = { sequence: ++saveSequence, payload: safeJson(req) };
      saveRecords.set(req, record); trace.saveRequests.push(record);
    }
  });
  target.on("response", (res) => {
    const task = (async () => {
      const req = res.request(), u = new URL(res.url());
      if (u.origin !== baseUrl || !res.headers()["content-type"]?.includes("application/json")) return;
      if (u.pathname === "/api/quote-sessions" && req.method() === "POST") {
        trace.saves.push({ ...(saveRecords.get(req) || { sequence: 0, payload: safeJson(req) }), status: res.status(), body: await res.json() });
      } else if (u.pathname === "/api/line-items/normalize" && req.method() === "POST") {
        trace.normalizations.push({ status: res.status(), payload: safeJson(req), body: await res.json() });
      } else if (u.pathname === "/api/jobs" && req.method() === "POST") {
        trace.jobs.push({ request: safeJson(req), draftRequest: draftRecords.get(req) || null, acceptedStatus: res.status(), accepted: await res.json(), polls: [] });
      } else {
        const match = u.pathname.match(/^\/api\/jobs\/([A-Za-z0-9_-]+)$/);
        if (match && req.method() === "GET") {
          const body = await res.json();
          const job = [...trace.jobs].reverse().find((entry) => entry.accepted?.job_id === match[1]);
          if (job) job.polls.push({ statusCode: res.status(), body });
        }
      }
    })();
    trace.pending.push(task); task.catch((error) => trace.pageErrors.push(String(error)));
  });
}
async function waitFor(predicate, label, timeout = 30000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const value = await predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Timed out waiting for " + label);
}
function startServer(shimPath, args) {
  const child = spawn(python, [shimPath, sourceRoot, ...args], {
    cwd: sourceRoot, windowsHide: true,
    env: { ...process.env, OPENAI_API_KEY: "", DEEPSEEK_API_KEY: "", SQAG_DISABLE_DOTENV: "1", PYTHONUNBUFFERED: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const baseUrlPromise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Local synthetic server did not become ready: " + serverErr.slice(-1000))), 30000);
    child.stdout.on("data", (chunk) => {
      const text = chunk.toString(); serverOut += text; serverLineBuffer += text;
      const lines = serverLineBuffer.split(/\r?\n/); serverLineBuffer = lines.pop() || "";
      for (const line of lines) if (/^http:\/\/127\.0\.0\.1:\d+$/.test(line.trim())) { clearTimeout(timer); resolve(line.trim()); return; }
    });
    child.stderr.on("data", (chunk) => { serverErr += chunk.toString(); if (serverErr.length > 200000) serverErr = serverErr.slice(-200000); });
    child.once("error", (error) => { clearTimeout(timer); reject(error); });
    child.once("exit", (code) => { clearTimeout(timer); if (child.exitCode !== null && !serverOut.includes("http://127.0.0.1:")) reject(new Error("Local server exited before readiness (" + code + ")")); });
  });
  return { child, baseUrlPromise };
}
async function stopServer() {
  if (server?.child && server.child.exitCode === null) {
    server.child.kill();
    await Promise.race([new Promise((resolve) => server.child.once("exit", resolve)), new Promise((resolve) => setTimeout(resolve, 5000))]);
  }
  await Promise.all([
    fs.writeFile(path.join(dirs.logs, "server.stdout.log"), serverOut),
    fs.writeFile(path.join(dirs.logs, "server.stderr.log"), serverErr),
  ]);
}
async function png(target, svg) {
  await target.setContent(svg, { waitUntil: "domcontentloaded" });
  return target.locator("svg").screenshot({ type: "png" });
}
async function readSession(sessionId, baseUrl) {
  const response = await api.get(baseUrl + "/api/quote-sessions/" + encodeURIComponent(sessionId));
  assert(response.status() === 200, "Independent quote-session readback returned HTTP " + response.status());
  const body = await response.json();
  assert(body.quote_session?.session_id === sessionId, "Readback returned a different quote session.");
  return body.quote_session;
}
function sortedValue(value) {
  if (Array.isArray(value)) return value.map(sortedValue);
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortedValue(value[key])]));
  return value;
}
function hasOwn(value, key) { return Object.prototype.hasOwnProperty.call(value, key); }
function requirePersistedQuoteCompany(details, logoFileKey) {
  const company = details?.company || {}, signature = details?.signature || {};
  const logoKey = String(company.logo_session_file_key || "").trim();
  const logoData = String(company.logo_data_url || "").trim();
  assert(Boolean(logoKey || logoData) && (logoKey ? logoKey === logoFileKey : /^data:image\/png;base64,/.test(logoData)), "Protected Quote Company lost its nonblank original logo reference.");
  assert(String(company.header_details || "").trim() === expectedCompany.header_details, "Protected Quote Company header_details is missing, blank or differs from the filled value.");
  assert(String(company.name || "").trim() === expectedCompany.name, "Protected Quote Company name is missing, blank or differs from the filled value.");
  assert(String(signature.company_signatory || "").trim() === expectedCompany.company_signatory, "Protected Quote Company signatory is missing, blank or differs from the filled value.");
  assert(String(signature.company_title || "").trim() === expectedCompany.company_title, "Protected Quote Company signatory title is missing, blank or differs from the filled value.");
}
function requireDurablePricingAuthority(durable) {
  assert(durable.pricing_reference?.id === fixtureId, "Protected commercial readback has missing or mismatched pricing-reference ID.");
  assert(durable.pricing_reference?.source === "local", "Protected commercial readback has missing or mismatched pricing-reference source.");
}
function requireVisibleEditedQuantity(text, stage) {
  assert(String(text).trim() === "2", "Visible Output quantity is not exactly 2 " + stage + ".");
}
function requireControlRejection(check, label) {
  let rejected = false;
  try { check(); } catch { rejected = true; }
  assert(rejected, "Coverage assertion accepted its invalid control: " + label);
}
function verifySharedCoverageAssertionControls(logoFileKey) {
  const valid = { company: { logo_session_file_key: logoFileKey, header_details: expectedCompany.header_details, name: expectedCompany.name }, signature: { company_signatory: expectedCompany.company_signatory, company_title: expectedCompany.company_title } };
  requirePersistedQuoteCompany(valid, logoFileKey);
  for (const [group, key] of [["company", "logo_session_file_key"], ["company", "header_details"], ["company", "name"], ["signature", "company_signatory"], ["signature", "company_title"]]) {
    for (const variant of ["missing", "blank"]) {
      const invalid = structuredClone(valid);
      if (variant === "missing") delete invalid[group][key]; else invalid[group][key] = "   ";
      // Matching captured/persisted omissions must never satisfy independent company completeness.
      completeDurableDraftProof({ lineItems: [], quoteDetails: structuredClone(invalid) }, { lineItems: [], quoteDetails: structuredClone(invalid) });
      requireControlRejection(() => requirePersistedQuoteCompany(invalid, logoFileKey), group + "." + key + ":" + variant);
    }
  }
  requireDurablePricingAuthority({ pricing_reference: { id: fixtureId, source: "local" } });
  for (const pricing_reference of [undefined, { id: "wrong", source: "local" }, { id: fixtureId }, { id: fixtureId, source: "company" }]) {
    requireControlRejection(() => requireDurablePricingAuthority({ pricing_reference }), "durable pricing authority");
  }
  requireVisibleEditedQuantity("2", "positive control");
  for (const text of ["1", ""]) requireControlRejection(() => requireVisibleEditedQuantity(text, "negative control with saved quantity2"), "visible quantity");
  return { companyPositive: 1, companyMatchingOmissionNegatives: 10, durableAuthorityPositive: 1, durableAuthorityNegatives: 4, visibleQuantityPositive: 1, visibleQuantityNegatives: 2 };
}
function completeDurableDraftProof(targetDraft, persistedDraft) {
  assert(targetDraft && typeof targetDraft === "object" && !Array.isArray(targetDraft), "Captured target lacks a complete draft.");
  assert(persistedDraft && typeof persistedDraft === "object" && !Array.isArray(persistedDraft), "Protected readback lacks a complete persisted draft.");
  const expected = JSON.parse(JSON.stringify(targetDraft));
  const actual = JSON.parse(JSON.stringify(persistedDraft));
  for (const key of ["savedAt", "activeAppView", "activeSidePanel"]) { delete expected[key]; delete actual[key]; }
  if (hasOwn(expected, "quoteCommercialReview") && expected.quoteCommercialReview === null) {
    assert(!hasOwn(actual, "quoteCommercialReview"), "Explicit clear-review target must correspond to an absent persisted review key.");
    delete expected.quoteCommercialReview;
  } else if (!hasOwn(expected, "quoteCommercialReview")) {
    assert(!hasOwn(actual, "quoteCommercialReview"), "Absent target review acquired a persisted review value.");
  }
  const topLevelEmptyOrderPaths = [];
  assert(Array.isArray(expected.lineItems) && Array.isArray(actual.lineItems) && actual.lineItems.length === expected.lineItems.length, "Protected top-level lineItems array shape differs from the captured target.");
  for (let index = 0; index < expected.lineItems.length; index++) {
    const expectedRow = expected.lineItems[index];
    const actualRow = actual.lineItems[index];
    assert(expectedRow && typeof expectedRow === "object" && !Array.isArray(expectedRow) && actualRow && typeof actualRow === "object" && !Array.isArray(actualRow), "Protected top-level lineItems row shape differs from the captured target.");
    if (hasOwn(expectedRow, "basis_order") && expectedRow.basis_order === "") {
      assert(!hasOwn(actualRow, "basis_order"), "Empty top-level basis_order target requires actual persisted absence.");
      delete expectedRow.basis_order;
      topLevelEmptyOrderPaths.push("lineItems[" + index + "].basis_order");
    }
  }
  const snapshotEmptyOrderPaths = [];
  const expectedSnapshot = expected.originalAnalysisSnapshot;
  if (expectedSnapshot && typeof expectedSnapshot === "object" && !Array.isArray(expectedSnapshot) && Array.isArray(expectedSnapshot.line_items)) {
    const actualSnapshot = actual.originalAnalysisSnapshot;
    assert(actualSnapshot && typeof actualSnapshot === "object" && !Array.isArray(actualSnapshot) && Array.isArray(actualSnapshot.line_items) && actualSnapshot.line_items.length === expectedSnapshot.line_items.length, "Protected original-analysis snapshot line-item array shape differs from the captured target.");
    for (let index = 0; index < expectedSnapshot.line_items.length; index++) {
      const expectedRow = expectedSnapshot.line_items[index];
      const actualRow = actualSnapshot.line_items[index];
      assert(expectedRow && typeof expectedRow === "object" && !Array.isArray(expectedRow) && actualRow && typeof actualRow === "object" && !Array.isArray(actualRow), "Protected original-analysis snapshot row shape differs from the captured target.");
      if (hasOwn(expectedRow, "basis_order") && expectedRow.basis_order === "") {
        assert(!hasOwn(actualRow, "basis_order"), "Empty snapshot basis_order target requires actual persisted absence.");
        delete expectedRow.basis_order;
        snapshotEmptyOrderPaths.push("originalAnalysisSnapshot.line_items[" + index + "].basis_order");
      }
    }
  }
  assert(JSON.stringify(sortedValue(actual)) === JSON.stringify(sortedValue(expected)), "Protected complete persisted draft differs from the captured final target.");
  return { completeDraftMatch: true, objectKeysDeepSorted: true, arrayOrderPreserved: true, topLevelEmptyOrderPaths, topLevelOrderCanonicalization: "target-own-empty-string-to-actual-absence-only", snapshotEmptyOrderPaths, snapshotOrderCanonicalization: "target-own-empty-string-to-actual-absence-only", excludedEphemera: ["savedAt", "activeAppView", "activeSidePanel"], reviewCanonicalization: hasOwn(targetDraft, "quoteCommercialReview") && targetDraft.quoteCommercialReview === null ? "target-null-to-persisted-absent" : "exact" };
}
function verifyCompleteDurableDraftComparatorControls() {
  const draft = {
    lineItems: [
      { description: "First synthetic row", quantity: 2, pricing_keyword: "first", category_order: 1, item_order: 1, source_basis_line_id: "first", pricing_authority: { id: "synthetic", source: "local" } },
      { description: "Second synthetic row", quantity: 3, pricing_keyword: "second", category_order: 2, item_order: 2, source_basis_line_id: "second", pricing_authority: { id: "synthetic", source: "local" } },
    ],
    quoteDetails: { unrelatedRequiredField: "preserve" },
    originalAnalysisSnapshot: { line_items: [{ description: "Original first", basis_order: "" }, { description: "Original second", basis_order: 2 }] },
    quoteCommercialReview: null,
  };
  const baseline = () => {
    const target = structuredClone(draft), actual = structuredClone(draft);
    delete actual.originalAnalysisSnapshot.line_items[0].basis_order;
    delete actual.quoteCommercialReview;
    return { target, actual };
  };
  const positives = [], negatives = [];
  const unchangedProof = (target, actual) => {
    const before = JSON.stringify([target, actual]);
    const proof = completeDurableDraftProof(target, actual);
    assert(JSON.stringify([target, actual]) === before, "Comparator mutated its input drafts.");
    return proof;
  };
  const positive = (label, mutate, mappedPaths) => {
    const { target, actual } = baseline(); mutate(target, actual);
    const proof = unchangedProof(target, actual);
    assert(JSON.stringify(proof.topLevelEmptyOrderPaths) === JSON.stringify(mappedPaths), "Comparator mapped unexpected top-level paths: " + label);
    positives.push({ label, proof });
  };
  positive("exact unchanged draft", (target, actual) => {
    actual.originalAnalysisSnapshot = structuredClone(target.originalAnalysisSnapshot);
    delete target.originalAnalysisSnapshot.line_items[0].basis_order;
    delete actual.originalAnalysisSnapshot.line_items[0].basis_order;
    delete target.quoteCommercialReview;
  }, []);
  positive("retained two-row empty-to-absent", (target) => { target.lineItems.forEach((row) => { row.basis_order = ""; }); }, ["lineItems[0].basis_order", "lineItems[1].basis_order"]);
  positive("absent/absent", () => {}, []);
  positive("positive integer unchanged", (target, actual) => { target.lineItems[0].basis_order = 1; actual.lineItems[0].basis_order = 1; }, []);
  const mismatch = "Protected complete persisted draft differs";
  const arrayShape = "Protected top-level lineItems array shape differs";
  const rowShape = "Protected top-level lineItems row shape differs";
  const emptyRequiresAbsence = "Empty top-level basis_order target requires actual persisted absence";
  const negative = (label, mutate, reason = mismatch) => {
    const { target, actual } = baseline(); unchangedProof(target, actual);
    mutate(target, actual);
    const before = JSON.stringify([target, actual]); let message = "";
    try { completeDurableDraftProof(target, actual); } catch (error) { message = String(error.message); }
    assert(message.includes(reason), "Comparator failed to causally reject " + label + ": " + message);
    assert(JSON.stringify([target, actual]) === before, "Comparator mutated negative inputs: " + label);
    negatives.push({ label, rejectedBy: message });
  };
  for (const [label, value] of [["integer1", 1], ["string1", "1"], ["zero", 0], ["null", null], ["false", false], ["array", []], ["object", {}], ["whitespace", " "]]) {
    negative("target " + label + " versus actual absence", (target) => { target.lineItems[0].basis_order = value; });
  }
  for (const [label, value] of [["empty", ""], ["null", null], ["integer1", 1]]) {
    negative("target empty versus actual " + label, (target, actual) => { target.lineItems[0].basis_order = ""; actual.lineItems[0].basis_order = value; }, emptyRequiresAbsence);
  }
  negative("integer1 to integer2", (target, actual) => { target.lineItems[0].basis_order = 1; actual.lineItems[0].basis_order = 2; });
  negative("target absent actual integer1", (_target, actual) => { actual.lineItems[0].basis_order = 1; });
  negative("dropped row", (_target, actual) => { actual.lineItems.pop(); }, arrayShape);
  negative("extra row", (_target, actual) => { actual.lineItems.push(structuredClone(actual.lineItems[0])); }, arrayShape);
  negative("reversed rows", (_target, actual) => { actual.lineItems.reverse(); });
  negative("duplicated row", (_target, actual) => { actual.lineItems[1] = structuredClone(actual.lineItems[0]); });
  for (const [label, value] of [["null", null], ["array", []], ["scalar", 1]]) {
    negative("matching invalid " + label + " rows", (target, actual) => { target.lineItems[0] = value; actual.lineItems[0] = structuredClone(value); }, rowShape);
  }
  negative("missing actual lineItems", (_target, actual) => { delete actual.lineItems; }, arrayShape);
  negative("nonarray actual lineItems", (_target, actual) => { actual.lineItems = {}; }, arrayShape);
  for (const [key, value] of [["quantity", 9], ["description", "Changed"], ["pricing_keyword", "changed"], ["category_order", 9], ["item_order", 9], ["source_basis_line_id", "changed"], ["pricing_authority", { id: "changed", source: "local" }]]) {
    negative("changed " + key, (_target, actual) => { actual.lineItems[0][key] = value; });
  }
  negative("unrelated dropped field", (_target, actual) => { delete actual.quoteDetails.unrelatedRequiredField; });
  negative("snapshot empty-order rule retained", (_target, actual) => { actual.originalAnalysisSnapshot.line_items[0].basis_order = ""; }, "Empty snapshot basis_order target requires actual persisted absence");
  negative("review clear rule retained", (_target, actual) => { actual.quoteCommercialReview = null; }, "Explicit clear-review target must correspond to an absent persisted review key");
  assert(positives.length === 4 && negatives.length === 32, "Incomplete comparator control coverage.");
  return { status: "PURE_COMPARATOR_CONTROLS_PASS", positives, negatives, inputsUnchanged: true };
}
async function captureConfirmedSaveTarget(originalUploads, stage) {
  const target = await page.evaluate(() => {
    const draft = currentQuoteSessionDraftState();
    return {
      sessionId: state.quoteSessionId,
      draft,
      draftKey: quoteSessionDraftComparisonKey(draft),
      files: sessionFileRecordsFromDraft().map((file) => ({
        session_file_key: file.session_file_key,
        file_role: file.file_role,
        name: file.name,
        type: file.type,
        size: file.size,
        content_fingerprint: normalizedContentFingerprint(file.content_fingerprint),
      })),
    };
  });
  assert(typeof target.sessionId === "string" && /^[A-Za-z0-9_-]+$/.test(target.sessionId), "Final save target lacks a nonempty exact session ID.");
  assert(typeof target.draftKey === "string" && target.draftKey.length > 0, "Final save target lacks its raw comparison key.");
  assert(target.files.length === 2 && originalUploads.length === 2, "Final save target must contain exactly the original render and logo.");
  assert(new Set(target.files.map((file) => file.session_file_key)).size === 2 && new Set(target.files.map((file) => file.name)).size === 2, "Captured file keys/names are duplicated.");
  target.originals = originalUploads.map((upload) => ({ name: upload.name, file_role: upload.file_role, type: "image/png", bytes: upload.buffer.length, sha256: createHash("sha256").update(upload.buffer).digest("hex") }));
  for (const expected of target.originals) {
    const file = target.files.find((item) => item.name === expected.name);
    assert(file && typeof file.session_file_key === "string" && file.session_file_key.length > 0, "Captured original file is missing its exact key.");
    assert(file.file_role === expected.file_role && file.type === expected.type && Number(file.size) === expected.bytes, "Captured original file role/MIME/size differs from the uploaded buffer.");
    assert(/^sha256:[a-f0-9]{64}$/.test(file.content_fingerprint) && file.content_fingerprint === "sha256:" + expected.sha256, "Captured original file fingerprint differs from independently hashed upload bytes.");
  }
  await writeJson(stage + "-save-target.json", target);
  return target;
}
function protectedOriginalFileProof(target, durable) {
  const records = durable.draft_files;
  assert(Array.isArray(records) && records.length === 2, "Protected detail must contain exactly the two original files.");
  assert(new Set(records.map((file) => file.session_file_key)).size === 2 && new Set(records.map((file) => file.name)).size === 2, "Protected detail has duplicate file keys/names.");
  const verified = [];
  for (const expected of target.files) {
    const original = target.originals.find((item) => item.name === expected.name);
    const file = records.find((item) => item.session_file_key === expected.session_file_key);
    assert(file && file.name === expected.name && file.file_role === expected.file_role && file.type === expected.type && Number(file.size) === Number(expected.size), "Protected file key/role/name/MIME/size differs from the captured tuple.");
    const match = typeof file.data_url === "string" ? file.data_url.match(/^data:([^;,]+);base64,([A-Za-z0-9+/]+={0,2})$/) : null;
    assert(match && match[1] === expected.type && match[2].length % 4 === 0, "Protected original data URL is malformed or has a mismatched MIME.");
    const bytes = Buffer.from(match[2], "base64");
    assert(bytes.toString("base64") === match[2] && bytes.length === original.bytes && bytes.length === Number(expected.size), "Protected original bytes have noncanonical encoding or incorrect length.");
    const digest = createHash("sha256").update(bytes).digest("hex");
    assert(expected.content_fingerprint === "sha256:" + digest && digest === original.sha256, "Protected original bytes differ from both the captured fingerprint and uploaded buffer hash.");
    if (hasOwn(file, "content_fingerprint")) assert(file.content_fingerprint === expected.content_fingerprint, "Protected fingerprint metadata contradicts original bytes.");
    verified.push({ session_file_key: file.session_file_key, file_role: file.file_role, name: file.name, type: file.type, bytes: bytes.length, sha256: digest, matchesCapturedTupleAndOriginalUpload: true });
  }
  return verified;
}
async function waitForConfirmedQuoteSave(target, stage, baseUrl, additionalPredicate = () => true) {
  const receipt = await waitFor(async () => {
    await Promise.allSettled(trace.pending);
    for (const entry of [...trace.saves].reverse()) {
      if (entry.status !== 200 || entry.body?.status !== "saved"
          || entry.payload?.session_id !== target.sessionId
          || entry.body?.quote_session?.session_id !== target.sessionId
          || !additionalPredicate(entry)) continue;
      const exact = await page.evaluate(({ draft, expectedKey }) => quoteSessionDraftComparisonKey(draft) === expectedKey, { draft: entry.payload.draft_state, expectedKey: target.draftKey });
      if (exact) return entry;
    }
    return null;
  }, "UI confirmed " + stage + " quote save");
  await writeJson(stage + "-save-receipt.json", { target, receipt, actualSuccessfulPostRequired: true });
  const protectedResponse = await api.get(baseUrl + "/api/quote-sessions/" + encodeURIComponent(target.sessionId));
  const protectedBody = await protectedResponse.json().catch(() => null);
  await writeJson(stage + "-save-observation.json", { target, receipt, protectedGet: { status: protectedResponse.status(), body: protectedBody }, validation: "PENDING" });
  assert(protectedResponse.status() === 200, "Independent quote-session readback returned HTTP " + protectedResponse.status());
  assert(protectedBody?.quote_session?.session_id === target.sessionId, "Readback returned a different quote session.");
  const durable = protectedBody.quote_session;
  const draftProof = completeDurableDraftProof(target.draft, durable.draft_state);
  const filesProof = protectedOriginalFileProof(target, durable);
  await writeJson(stage + "-save-proof.json", { target, receipt, protectedDetail: durable, draftProof, filesProof, actualSuccessfulPostRequired: true, protectedSameSessionHttp200Required: true, manualSaveOrStateRepair: false });
  return receipt;
}


async function validateXlsx(file) {
  const code = [
    "import json,math,sys,zipfile,xml.etree.ElementTree as ET",
    "p=sys.argv[1]; expected=json.loads(sys.argv[2]); values=[float(v) for v in json.loads(sys.argv[3])]; included=sys.argv[4]",
    "ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}",
    "z=zipfile.ZipFile(p); bad=z.testzip(); assert bad is None, 'bad XLSX member '+str(bad)",
    "names=set(z.namelist()); assert '[Content_Types].xml' in names and 'xl/workbook.xml' in names, 'incomplete XLSX package'",
    "shared=[]",
    "if 'xl/sharedStrings.xml' in names: shared=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns)]",
    "sheets=sorted(n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')); assert sheets, 'no worksheet'",
    "texts=[]; nums=[]; included_zero=False",
    "for name in sheets:",
    " root=ET.fromstring(z.read(name))",
    " for row in root.findall('.//m:sheetData/m:row',ns):",
    "  rt=[]; rn=[]",
    "  for c in row.findall('m:c',ns):",
    "   kind=c.attrib.get('t',''); v=c.find('m:v',ns); t=''",
    "   if kind=='s' and v is not None: t=shared[int(v.text or '0')]",
    "   elif kind=='inlineStr':",
    "    el=c.find('m:is',ns); t=''.join(el.itertext()) if el is not None else ''",
    "   elif kind in ('str','e') and v is not None: t=v.text or ''",
    "   if t: rt.append(t); texts.append(t)",
    "   elif v is not None:",
    "    try: n=float(v.text or 'nan'); rn.append(n); nums.append(n)",
    "    except ValueError: pass",
    "  if included in rt and any(math.isfinite(n) and abs(n)<=.011 for n in rn): included_zero=True",
    "xml=''.join(z.read(n).decode('utf8','replace') for n in names if n.startswith('xl/') and n.endswith('.xml')); alltext='\\n'.join(texts)+xml",
    "missing=[x for x in expected if x not in alltext]; assert not missing, 'missing quote text '+json.dumps(missing)",
    "assert included_zero, 'Included row is not zero amount'",
    "for val in values: assert any(math.isfinite(n) and abs(n-val)<=.011 for n in nums), 'missing expected total/value '+str(val)",
    "assert any(n.startswith('xl/media/') for n in names), 'no embedded logo/media'; assert any(n.startswith('xl/drawings/') for n in names), 'no workbook drawing/logo anchor'",
    "print(json.dumps({'status':'valid','worksheets':len(sheets),'text_cells':len(texts),'numeric_cells':len(nums),'logo_media':True}))",
  ].join("\n");
  const child = spawn(python, ["-c", code, file, JSON.stringify(expectedStrings), JSON.stringify(expectedNumbers), includedDescription], { cwd: sourceRoot, env: { ...process.env, OPENAI_API_KEY: "", DEEPSEEK_API_KEY: "" }, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let stdout = "", stderr = "";
  child.stdout.on("data", (b) => { stdout += b.toString(); }); child.stderr.on("data", (b) => { stderr += b.toString(); });
  const codeValue = await new Promise((resolve, reject) => { child.once("error", reject); child.once("close", resolve); });
  await fs.writeFile(path.join(dirs.logs, "xlsx-validator.stdout.log"), stdout);
  await fs.writeFile(path.join(dirs.logs, "xlsx-validator.stderr.log"), stderr);
  assert(codeValue === 0, "XLSX structure/content validation failed: " + stderr.slice(-1200) + stdout.slice(-1200));
  return JSON.parse(stdout.trim().split(/\r?\n/).at(-1));
}

let phase='setup'; const completedChecks=[];
async function captureActualView(error) {
  const view = page && !page.isClosed() ? await page.evaluate(() => typeof state === "undefined" ? {url:location.pathname,appStateAvailable:false} : ({url:location.pathname,view:state.activeAppView,panel:state.activeSidePanel,workflowStage:state.workflowStage,sessionId:state.quoteSessionId,checkpoint:state.customerStepCheckpoint,checkpointCurrent:customerStepCheckpointIsCurrent(),restoreBusy:state.quoteSessionRestoreBusy,loadError:state.quoteSessionLoadError,resultStatus:document.querySelector('#resultStatus')?.innerText,outputStatus:document.querySelector('#outputStatusPill')?.innerText,visiblePanels:[...document.querySelectorAll('.side-panel-section.is-active')].map(x=>x.id),messages:[...document.querySelectorAll('.message')].map(x=>x.innerText).slice(0,20)})).catch(e=>({captureError:String(e)})) : {pageAvailable:false};
  await writeJson('actual-terminal-view.json',{phase,completedChecks,error:error?String(error?.stack||error):null,view,console:trace.console,pageErrors:trace.pageErrors,unreached:phase==='browser_complete'?[]:['All checks after failed phase, including native workbook verification unless separately recorded']});
}
const watchdog=setTimeout(async()=>{await captureActualView('170-second browser shutdown threshold;180-second hard bound').catch(()=>{}); if(context) await context.close().catch(()=>{}); if(browser) await browser.close().catch(()=>{}); await stopServer().catch(()=>{}); process.exit(124);},170000);
let sessionId = "";
let expectedStrings = [];
let expectedNumbers = [];
let includedDescription = "";
try {
  assert(await fs.stat(sourceRoot).then(() => true).catch(() => false), "Pinned source root is not mounted.");
  const referenceConfig = JSON.parse(await fs.readFile(path.join(fixtureDir, "reference.json"), "utf8"));
  const fixtureCatalog = JSON.parse(await fs.readFile(path.join(fixtureDir, "pricing-catalog.json"), "utf8"));
  assert(Array.isArray(fixtureCatalog.items)&&fixtureCatalog.items.length>=2,"Synthetic pricing fixture has fewer than two catalog items.");
  assert(fixtureCatalog.items.slice(0,2).every(item=>item.id&&item.description&&Number.isFinite(Number(item.sale_unit_price??item.default_quote_amount))),"Synthetic pricing fixture does not contain two usable items.");
  const fixtureCatalogById = new Map(fixtureCatalog.items.map((item) => [item.id, item]));
  const profileConfig = JSON.parse(await fs.readFile(path.join(profileDir, "profile.json"), "utf8"));
  assert(profileConfig.id === profileId && profileConfig.default_pricing_reference === fixtureId, "Fixture profile/reference binding changed.");
  await fs.cp(fixtureDir, path.join(dirs.pricing, fixtureId), { recursive: true });
  await fs.cp(profileDir, path.join(dirs.profiles, profileId), { recursive: true });

  const shimPath = path.join(sourceRoot,'tests/alpha1_fresh_mock_harness.py');
  server = startServer(shimPath, [dirs.data, dirs.output, dirs.temp, dirs.logs, dirs.pricing, dirs.profiles]);
  const baseUrl = await server.baseUrlPromise;
  const health = await fetch(baseUrl + "/api/health");
  assert(health.status === 200 && (await health.json()).status === "ok", "Local synthetic app health failed.");
  browser = await chromium.launch({ headless: true });
  context = await browser.newContext({ acceptDownloads: true });
  page = await context.newPage(); attachTrace(page, baseUrl);
  await page.route("**/*", (route) => new URL(route.request().url()).origin === baseUrl ? route.continue() : route.abort());
  anonymousApi = await request.newContext();
  const anonymousStatus=(await anonymousApi.get(baseUrl + "/api/quote-sessions")).status(); assert(anonymousStatus === 401, "Anonymous quote-session access was not denied.");
  phase='synthetic_auth_and_fresh_customer'; await writeJson('progress.json',{phase,completedChecks});
  await page.goto(baseUrl + "/login", { waitUntil: "domcontentloaded" });
  const auth = await waitFor(async () => page.evaluate(async () => { const r=await fetch("/api/session"); return {status:r.status,body:await r.json()}; }).then((x) => x.status === 200 && x.body.authenticated ? x : false), "synthetic local auth");
  assert(auth.body.user?.subject === "synthetic-browser-subject" && auth.body.user?.account === "workspace-internal-alpha", "Unexpected synthetic identity/workspace.");
  assert(auth.body.permissions?.canGenerateQuote === true, "Synthetic user cannot generate quotes.");
  api = await request.newContext({ storageState: await context.storageState() });
  const profileResponse = await api.get(baseUrl + "/api/profiles");
  assert(profileResponse.status() === 200, "Authenticated profile lookup failed.");
  const profileData = await profileResponse.json();
  assert(profileData.workspace?.workspace?.id === "workspace-internal-alpha", "Profile lookup returned a different authenticated workspace.");
  assert(profileData.pricing_references?.some((item) => item.id === fixtureId && item.source === "local"), "Selected local pricing reference is missing.");
  assert(profileData.pricing_references.find(item=>item.id===fixtureId&&item.source==="local")?.label===referenceConfig.label,"Authenticated UI reference label differs from the selected fixture.");

  await page.locator("#newQuoteButton").click();
  await page.locator("#imageIntake").waitFor({ state: "visible" });
  const renderSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#eee"/><rect x="60" y="55" width="520" height="250" fill="#aabdb5"/><path d="M60 180h520M320 55v250" stroke="#384c45" stroke-width="8"/></svg>';
  const logoSvg = '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="96"><rect width="240" height="96" rx="10" fill="#19392f"/><text x="25" y="61" fill="white" font-family="Arial" font-size="28">ALPHA1</text></svg>';
  const assetPage = await context.newPage();
  const renderPng = await png(assetPage, renderSvg), logoPng = await png(assetPage, logoSvg); await assetPage.close();
  await page.locator("#imageInput").setInputFiles({ name: "alpha1-synthetic-render.png", mimeType: "image/png", buffer: renderPng });
  await page.locator("#fileList .file-item").filter({ hasText: "alpha1-synthetic-render.png" }).waitFor({ state: "visible" });
  await page.locator("#sideNextButton").click();
  await page.locator("#customerDetailsPanel").waitFor({ state: "visible" });
  await page.locator("#profileSelect").selectOption({ label: referenceConfig.label });
  const customers = [["#clientNameEditor","Synthetic Alpha Customer"],["#clientAttentionEditor","Alpha Review Team"],["#clientTitleEditor","Synthetic Project Lead"],["#clientAddressEditor","Synthetic workspace, Singapore"],["#projectTitleEditor","Synthetic Internal Alpha1"],["#showName","Synthetic Alpha1 Showcase"],["#projectNumberEditor","ALPHA1-"+Date.now().toString(36).toUpperCase()]];
  for (const [selector,value] of customers) await page.locator(selector).fill(value);
  await page.locator("#quoteDate").fill(new Date().toISOString().slice(0,10));
  await page.locator("#sideNextButton").click();
  await page.locator("#quoteCompanyPanel").waitFor({ state: "visible" });
  const preset = await page.locator("#presetSelect option").evaluateAll((options,id)=>options.map((o)=>({value:o.value,label:o.textContent})).find((o)=>o.value.startsWith("profile:"+id+":")),profileId).catch(()=>null);
  assert(preset?.value, "Synthetic saved profile is not offered by the normal profile selector.");
  await page.locator("#presetSelect").selectOption(preset.value);
  await page.locator("#loadPresetButton").click();
  await page.locator("#confirmProfileLoadButton").waitFor({ state: "visible" });
  await page.locator("#confirmProfileLoadButton").click();
  assert(await page.evaluate(() => generationProfileIdForPayload()) === ("profile:"+profileId), "Visible saved-profile loading did not bind the selected layout.");
  await page.locator("#headerLogoInput").setInputFiles({ name: "alpha1-synthetic-logo.png", mimeType: "image/png", buffer: logoPng });
  const company = [["#headerDetailsEditor",expectedCompany.header_details],["#quoteCompanyNameEditor",expectedCompany.name],["#acceptanceTextEditor","Synthetic approval for local acceptance testing"],["#companySignatoryEditor",expectedCompany.company_signatory],["#companyTitleEditor",expectedCompany.company_title],["#companyDateLabelEditor","Date:"],["#personLabelEditor","Name:"],["#stampLabelEditor","Company stamp"],["#dateLabelEditor","Date:"]];
  for (const [selector,value] of company) await page.locator(selector).fill(value);
  const projectNumber = customers.at(-1)[1];
  await page.screenshot({ path: path.join(dirs.captures,"01-company-form.png"), fullPage: true });
  const originalUploads = [
    { name: "alpha1-synthetic-render.png", file_role: "reference", buffer: renderPng },
    { name: "alpha1-synthetic-logo.png", file_role: "quote_company_logo", buffer: logoPng },
  ];
  const checkpoint = await page.evaluate(() => ({ current: customerStepCheckpointIsCurrent(), checkpoint: state.customerStepCheckpoint, profile: generationProfileIdForPayload(), reference: currentPricingReference(), panel: state.activeSidePanel }));
  assert(checkpoint.current && checkpoint.panel === 'quote_company', 'Customer Next did not acknowledge a current checkpoint.');
  assert(checkpoint.reference?.id === fixtureId && checkpoint.reference.source === 'local' && /^sha256:[a-f0-9]{64}$/.test(checkpoint.reference.digest_sha256), 'Committed pricing identity/digest mismatch.');
  assert(checkpoint.profile === 'profile:' + profileId, 'Saved profile/layout not bound.');
  sessionId = checkpoint.checkpoint.sessionId;
  const checkpointDetail = await readSession(sessionId, baseUrl);
  assert(checkpointDetail.draft_state.customerStepCheckpoint?.sessionId === sessionId && checkpointDetail.draft_state.customerStepCheckpoint.pricingReferenceDigest === checkpoint.reference.digest_sha256, 'Acknowledged Customer checkpoint absent from protected same-session GET.');
  const creationTarget=await captureConfirmedSaveTarget(originalUploads,"creation");
  const creationReceipt=await waitForConfirmedQuoteSave(creationTarget,"creation",baseUrl);
  assert(creationReceipt.payload?.pricing_reference?.id===fixtureId&&creationReceipt.payload.pricing_reference.source==="local","Fresh UI creation save lost selected pricing authority.");
  const creationDetail=await readSession(sessionId,baseUrl);
  const logoFileKey=creationTarget.files.find(file=>file.file_role==="quote_company_logo")?.session_file_key;
  assert(typeof logoFileKey==="string"&&logoFileKey.trim(),"Creation target lacks the original logo file key.");
  requirePersistedQuoteCompany(creationDetail.draft_state?.quoteDetails,logoFileKey);
  await writeJson("durable-draft-comparator-controls.json", verifyCompleteDurableDraftComparatorControls());
  const sharedCoverageControls=verifySharedCoverageAssertionControls(logoFileKey);
  await writeJson("shared-coverage-assertion-controls.json",sharedCoverageControls);
  const referenceSignature=await page.evaluate(()=>referenceFilesDependencySignature());
  assert(await page.evaluate(value=>referenceFileSignatureIsStrong(value),referenceSignature),"Synthetic booth image has no stable analysis dependency signature.");
  assert(creationDetail.session_id===sessionId&&creationDetail.customer_summary?.customer_name==="Synthetic Alpha Customer","Created quote identity or customer context is incorrect.");
  assert(creationDetail.pricing_reference?.id===fixtureId&&creationDetail.pricing_reference.source==="local","Created quote lost its selected pricing authority.");
  assert(Array.isArray(creationDetail.draft_state.outputRows)&&creationDetail.draft_state.outputRows.length===0,"Fresh pre-analysis save unexpectedly injected output rows.");
  const createdAuthResponse=await api.get(baseUrl+"/api/session");
  assert(createdAuthResponse.status()===200,"Authenticated session disappeared after fresh quote creation.");
  const createdAuth=await createdAuthResponse.json();
  assert(createdAuth.authenticated&&createdAuth.user?.account==="workspace-internal-alpha","Authenticated workspace changed after fresh quote creation.");
  await writeJson('customer-checkpoint-proof.json', { checkpoint, protectedDetail: checkpointDetail, status: 'PASS', manualSaveOrStateRepair: false });
  completedChecks.push('customer_next_committed_profile_reference_checkpoint'); phase = 'normal_mock_analysis'; await writeJson('progress.json', {phase,completedChecks});
  // Fresh company-step Next opens the existing Normal Analyse confirmation.
  await page.locator("#sideNextButton").click();
  await page.locator("#analysisConfirmStartButton").waitFor({ state: "visible", timeout: 15000 });
  assert((await page.locator("#analysisConfirmStartButton").innerText()).match(/Run Analysis/i), "Confirmation is not the ordinary analysis action.");
  await page.screenshot({ path: path.join(dirs.captures,"04-normal-analyse-confirm.png"), fullPage: true });
  await page.locator("#analysisConfirmStartButton").click();
  await page.locator("#quoteBasisPanel").waitFor({ state: "visible", timeout: 90000 });
  await waitFor(()=>trace.draftRequests.length===1,"exactly one mocked normal draft request",90000);
  assert(trace.draftRequests.length===1, "Expected exactly one mocked draft request.");
  const draftDispatch=trace.draftRequests[0];
  assert(draftDispatch.pathname==="/api/jobs" && draftDispatch.envelope?.type==="draft" && draftDispatch.payload?.analysis_mode==="standard", "Normal Analyse did not dispatch exactly one ordinary standard draft job.");
  assert(JSON.stringify(JSON.parse(draftDispatch.rawEnvelope))===JSON.stringify(draftDispatch.envelope), "Captured draft envelope changed.");
  await waitFor(()=>trace.jobs.some((job)=>job.draftRequest?.sequence===draftDispatch.sequence && job.polls.some((poll)=>["completed","failed","needs_review"].includes(poll.body?.status))), "terminal mocked Normal Analyse job",90000);
  const draftJob=trace.jobs.find((job)=>job.draftRequest?.sequence===draftDispatch.sequence);
  const draftTerminal=draftJob.polls.filter((poll)=>["completed","failed","needs_review"].includes(poll.body?.status)).at(-1);
  assert(draftJob.acceptedStatus===202 && draftJob.accepted?.type==="draft" && draftJob.accepted.job_id===draftDispatch.envelope.job_id, "Draft accepted HTTP/envelope/job identity mismatch.");
  assert(draftTerminal.statusCode===200 && draftTerminal.body?.status==="completed" && draftTerminal.body.type==="draft" && draftTerminal.body.result?.status==="drafted" && draftTerminal.body.result.analysis_mode==="standard", "Normal Analyse did not complete successfully in standard mode.");
  assert(draftJob.accepted.job_id===draftTerminal.body.job_id && draftTerminal.body.result.job_id===draftJob.accepted.job_id, "Draft accepted/terminal/result job identities do not bind.");
  assert(draftJob.accepted.generation_run_id && draftJob.accepted.generation_run_id===draftTerminal.body.generation_run_id && draftTerminal.body.result.generation_run_id===draftJob.accepted.generation_run_id, "Draft accepted/terminal/result generation-run identities do not bind.");
  const providerContract=JSON.parse(await fs.readFile(path.join(runRoot,"provider-contract.json"),"utf8"));
  assert(providerContract.dimensions?.dimension_source==="default" && providerContract.canonical_note && providerContract.expected_catalog_rows?.length===2, "Provider double did not capture the actual default dimensions and two dynamic rows.");
  const rawNotes=providerContract.raw.quote_basis_sections.flatMap((section)=>section.lines||[]).filter((line)=>line.text===providerContract.canonical_note);
  assert(rawNotes.length===1 && rawNotes[0].tag==="Confirm" && Object.keys(rawNotes[0]).every((key)=>["tag","text","confidence_pct"].includes(key)) && providerContract.raw.line_items.length===2, "Raw informational note gained an ID, selector, quantity, unit or output row.");
  await writeJson("draft-job-proof.json",{dispatch:draftDispatch,acceptedStatus:draftJob.acceptedStatus,accepted:draftJob.accepted,terminal:draftTerminal,providerContract});
  await page.screenshot({ path: path.join(dirs.captures,"05-basis-review.png"), fullPage: true });
  const beforeApprovalBasis=await page.evaluate(()=>JSON.parse(JSON.stringify(state.quoteBasisSections)));
  const beforeApprovalLines=beforeApprovalBasis.flatMap((section)=>section.lines||[]);
  assert(beforeApprovalLines.length===3 && beforeApprovalLines.every((line)=>line.tag==="Confirm") && beforeApprovalLines.filter((line)=>line.text===providerContract.canonical_note).length===1, "Complete initial basis is not exactly two Confirm catalogue rows and one Confirm dimension note.");
  for (const expected of providerContract.expected_catalog_rows) assert(beforeApprovalLines.some((line)=>line.id===expected.source_basis_line_id && line.pricing_keyword===expected.pricing_keyword), "Initial basis lost a dynamic catalogue identity/selector.");
  const normalSaveTarget=await captureConfirmedSaveTarget(originalUploads,"normal");
  await waitForConfirmedQuoteSave(normalSaveTarget,"normal",baseUrl);
  const normalDetail=await readSession(sessionId,baseUrl);
  assert(JSON.stringify(sortedValue(normalDetail.draft_state?.quoteBasisSections))===JSON.stringify(sortedValue(beforeApprovalBasis)),"Protected Normal save lost the complete accepted basis.");
  assert(typeof draftTerminal.body.result.source==="string"&&draftTerminal.body.result.source.trim()&&normalDetail.draft_state?.draftSource===draftTerminal.body.result.source&&normalDetail.draft_state?.aiFailed===false,"Protected Normal save lost its successful source/non-failed state.");
  assert(Array.isArray(normalDetail.draft_state?.lineItems)&&normalDetail.draft_state.lineItems.length===2&&providerContract.expected_catalog_rows.every(expected=>normalDetail.draft_state.lineItems.some(row=>row.source_basis_line_id===expected.source_basis_line_id&&row.pricing_keyword===expected.pricing_keyword)),"Protected Normal save lost the two actual dynamic line identities.");
  assert(await page.locator('#basisReviewSurface .basis-review-grid .basis-line-row').count()===2 && await page.locator('#basisReviewSurface .basis-review-grid').getByText(providerContract.canonical_note,{exact:true}).count()===0, "Review grid includes unexpected rows or the informational dimension note.");
  const basisRows=page.locator("[data-basis-tag='Include']");
  assert(await basisRows.count()===2, "Expected two individually actionable basis-line Include controls.");
  for (let i=0;i<2;i++) await basisRows.nth(i).click();
  const basis=await page.evaluate(()=>JSON.parse(JSON.stringify(state.quoteBasisSections)));
  const allBasisLines=basis.flatMap((section)=>section.lines||[]);
  const dimensionNotes=allBasisLines.filter((line)=>line.text===providerContract.canonical_note);
  const noninformational=allBasisLines.filter((line)=>line.text!==providerContract.canonical_note);
  assert(allBasisLines.length===3 && dimensionNotes.length===1 && noninformational.length===2, "Complete basis contains missing/duplicate informational notes or extra noninformational rows.");
  assert(dimensionNotes[0].tag==="Confirm" && !dimensionNotes[0].pricing_keyword && !dimensionNotes[0].catalog_item_id && !dimensionNotes[0].pricing_authority && !dimensionNotes[0].source_basis_line_id && !dimensionNotes[0].catalog_description && !dimensionNotes[0].pricing_reference_description && !dimensionNotes[0].line_items, "Informational dimension note gained approval/catalog/output authority.");
  assert(new Set(noninformational.map((line)=>line.id)).size===2 && new Set(noninformational.map((line)=>line.pricing_keyword)).size===2, "Basis row identities/selectors are not unique.");
  for (const expected of providerContract.expected_catalog_rows) {
    const line=noninformational.find((entry)=>entry.id===expected.source_basis_line_id);
    const catalogItem=fixtureCatalogById.get(expected.pricing_keyword);
    assert(line && line.tag==="Include" && line.pricing_keyword===expected.pricing_keyword && catalogItem && (line.catalog_description||line.pricing_reference_description)===catalogItem.description && line.unit===expected.unit && Number(line.quantity)===Number(expected.quantity), "Individual Include approval lost the expected dynamic catalogue identity/selector.");
  }
  const boothDetails=page.locator('#basisReviewSurface .basis-visual-display[aria-label="Booth display details"]');
  assert(await boothDetails.count()===1 && await boothDetails.isVisible(), "Informational dimensions are not visible in Booth Details.");
  assert((await boothDetails.locator("strong").innerText()).trim()==="Booth Details" && await boothDetails.locator("li").count()===1 && (await boothDetails.locator("li").innerText()).trim()===providerContract.canonical_note, "Booth Details does not show exactly the canonical default note.");
  await writeJson("basis-approval-proof.json",{completeBasis:basis,expectedCatalogRows:providerContract.expected_catalog_rows,canonicalNote:providerContract.canonical_note,individualApprovals:2,boothDetailsVisible:true,extraNoninformationalRows:0});
  const normBefore=trace.normalizations.length;
  await page.locator("#sideNextButton").click();
  await waitFor(()=>trace.normalizations.length>normBefore,"real quote-basis normalize response");
  const norm=trace.normalizations.at(-1);
  assert(norm.status===200&&norm.body?.status==="normalized", "Real normalize route failed: "+JSON.stringify({status:norm.status,body:norm.body}));
  assert(Array.isArray(norm.body.line_items)&&norm.body.line_items.length===2, "Normalization returned HTTP 200 but not exactly two resolved rows: "+JSON.stringify(norm.body));
  assert(norm.payload?.pricing_reference_id===fixtureId || norm.payload?.pricing_reference?.id===fixtureId, "Normalization used a different pricing reference.");
  assert(norm.body.line_items.every((row)=>providerContract.expected_catalog_rows.some((expected)=>row.source_basis_line_id===expected.source_basis_line_id && row.pricing_keyword===expected.pricing_keyword)) && !norm.body.line_items.some((row)=>row.description===providerContract.canonical_note), "Normalized output contains an unexpected row or informational dimension note.");
  await page.locator("#pricingMatchesBody tr").nth(1).waitFor({ state: "visible" });
  const items=norm.body.line_items;
  for (const item of items) {
    const catalogItem = fixtureCatalogById.get(item.pricing_keyword);
    const catalogPrice = Number(catalogItem?.sale_unit_price ?? catalogItem?.default_quote_amount);
    assert(catalogItem && Number.isFinite(catalogPrice), "Normalized selector is not in the unchanged authoritative fixture catalog.");
    assert(item.pricing_authority?.variant === "catalog", "Normalized row did not carry server-bound catalog authority.");
    assert(Math.abs(Number(item.catalog_unit_price ?? item.effective_unit_price) - catalogPrice) < 0.011, "Normalized row price differs from the unchanged authoritative catalog.");
  }
  const priced=items[0], included=items[1];
  includedDescription=included.description;
  expectedStrings=["Synthetic Alpha Customer","Synthetic Internal Alpha1",projectNumber,priced.description,included.description,"Synthetic Alpha1 Fabrication","Synthetic Internal Alpha1 | Singapore"];
  const price=Number(fixtureCatalogById.get(priced.pricing_keyword).sale_unit_price ?? fixtureCatalogById.get(priced.pricing_keyword).default_quote_amount);
  assert(Number.isFinite(price)&&price>=0, "Normalized first row lacks a resolved catalog price.");
  const pricedRow=page.locator("#pricingMatchesBody tr").filter({hasText:priced.description}).first();
  const includedRow=page.locator("#pricingMatchesBody tr").filter({hasText:included.description}).first();
  await pricedRow.waitFor({state:"visible"}); await includedRow.waitFor({state:"visible"});
  const visiblePrice=Number((await pricedRow.locator('[data-output-edit-field="unit_price_override"] .output-cell-text').innerText()).replace(/[^0-9.]/g,""));
  assert(Math.abs(visiblePrice-price)<0.011,"UI price differs from normalized selected-catalog price.");
  await pricedRow.locator('[data-output-edit-field="quantity"]').click();
  const qty=pricedRow.locator('[data-output-editor-field="quantity"]');
  await qty.fill("2"); await qty.press("Enter");
  await includedRow.locator('[data-output-edit-field="unit_price_override"]').click();
  await page.evaluate(() => {
    window.__alpha1IncludedEvents=[];
    for (const capture of [true,false]) document.querySelector("#pricingMatchesBody").addEventListener("click",event=>{
      if(event.target.closest("[data-output-included-action]")) window.__alpha1IncludedEvents.push({type:"click",phase:capture?"capture":"bubble"});
    },capture);
  });
  await includedRow.locator("[data-output-included-action]").click();
  const includedEvents=await page.evaluate(()=>window.__alpha1IncludedEvents);
  assert(includedEvents.some(e=>e.type==="click"&&e.phase==="capture"),"Included action click did not reach the live pricing table.");
  assert(includedEvents.some(e=>e.type==="click"&&e.phase==="bubble"),"Included action click did not bubble through the live pricing table.");
  await includedRow.locator("[data-output-edit-field='unit_price_override']").filter({hasText:"Included"}).waitFor({state:"visible"});
  assert(Math.abs(Number((await includedRow.locator(".amount-cell").innerText()).replace(/[^0-9.-]/g,"")))<0.005,"Included row amount is not zero.");
  expectedNumbers=[Math.round(price*2*100)/100,Math.round(price*2*1.09*100)/100];
  await page.screenshot({path:path.join(dirs.captures,"06-edited-lines.png"),fullPage:true});
  const editSaveTarget=await captureConfirmedSaveTarget(originalUploads, "commercial");
  const editSave=await waitForConfirmedQuoteSave(editSaveTarget, "commercial", baseUrl, (e)=>e.status===200&&e.body?.quote_session?.session_id===sessionId&&(e.payload?.draft_state?.outputRows||[]).some((r)=>r.description===priced.description&&Number(r.quantity)===2)&&(e.payload?.draft_state?.outputRows||[]).some((r)=>r.description===included.description&&r.price_mode==="Included"));
  assert(editSave.payload?.pricing_reference?.id===fixtureId&&editSave.payload?.pricing_reference?.source==="local","Commercial edit save lost the reference.");
  let durable=await readSession(sessionId,baseUrl);
  requireDurablePricingAuthority(durable);
  requireVisibleEditedQuantity(await pricedRow.locator('[data-output-edit-field="quantity"] .output-cell-text').innerText(),"after editing");
  assert(Number(durable.draft_state?.outputRows?.find((r)=>r.description===priced.description)?.quantity)===2,"Independent readback lost quantity 2.");
  assert(durable.draft_state?.outputRows?.find((r)=>r.description===included.description)?.price_mode==="Included","Independent readback lost Included state.");
  const beforeGen=trace.jobs.length;
  assert(await page.locator('#outputSidePanel.is-active').isVisible(), 'Fresh confirmation did not reach Output.');
  assert(await page.locator('#sideDownloadButton').getAttribute('aria-disabled') !== 'true', 'Current XLSX action is disabled.');
  completedChecks.push('normal_mock_analysis','catalogue_normalization_and_arithmetic','production_autosave_protected_readback'); phase='generate_and_download'; await writeJson('progress.json',{phase,completedChecks});  const downloadPromise=page.waitForEvent("download",{timeout:90000});
  await page.locator("#sideDownloadButton").click();
  const download=await downloadPromise;
  const downloadPath=path.join(dirs.downloads,"alpha1-current.xlsx");
  await download.saveAs(downloadPath);
  await waitFor(()=>trace.jobs.slice(beforeGen).some((job)=>job.request?.type==="generate"&&job.polls.some((p)=>["completed","failed","needs_review"].includes(p.body?.status))),"terminal native XLSX job",90000);
  const job=trace.jobs.slice(beforeGen).find((j)=>j.request?.type==="generate");
  const terminal=job.polls.map((p)=>p.body).filter((body)=>["completed","failed","needs_review"].includes(body?.status)).at(-1);
  assert(terminal?.status==="completed"&&terminal.result?.status==="completed","Native XLSX job did not complete: "+JSON.stringify(terminal));
  assert(terminal.result?.quote_session?.session_id===sessionId,"XLSX job returned a different session.");
  assert(job.accepted?.generation_run_id&&terminal.generation_run_id===job.accepted.generation_run_id&&terminal.result?.generation_run_id===job.accepted.generation_run_id,"XLSX accepted/terminal/result run IDs do not bind.");
  const xlsxReadback=await readSession(sessionId,baseUrl);
  const exp=xlsxReadback.exports?.xlsx;
  assert(exp?.exists===true&&exp.stale===false&&exp.publication_id,"Generated XLSX is not current/published.");
  assert(xlsxReadback.status?.quote_generated===true,"Completed result was not persisted as current.");
  assert(terminal.result.quote_session.exports.xlsx.sha256===exp.sha256,"XLSX result and independent readback differ on artifact hash.");
  assert(terminal.result.quote_session.exports.xlsx.publication_id===exp.publication_id,"XLSX result and readback differ on publication identity.");
  assert(job.request.payload.quote_session.session_id===sessionId,"XLSX request lost quote-session identity.");
  assert(job.request.payload.pricing_reference_source==="local","XLSX request lost pricing-reference source.");
  assert(Number(xlsxReadback.draft_state.outputRows.find(r=>r.description===priced.description)?.quantity)===2,"Post-generation durable state changed the latest edit.");
  requireVisibleEditedQuantity(await pricedRow.locator('[data-output-edit-field="quantity"] .output-cell-text').innerText(),"after generation");
  assert((await includedRow.locator('[data-output-edit-field="unit_price_override"]').innerText()).includes("Included"),"Visible Output lost the Included edit after generation.");
  assert(xlsxReadback.draft_state.outputRows.find(r=>r.description===included.description)?.price_mode==="Included","Post-generation durable state lost Included edit.");
  assert(new URL(await page.locator("#sideDownloadButton").getAttribute("href"),baseUrl).toString()===new URL(exp.url,baseUrl).toString(),"Fresh quote does not point to current XLSX.");
  const downloaded=await fs.readFile(downloadPath);
  const digest=createHash("sha256").update(downloaded).digest("hex");
  assert(digest===exp.sha256,"UI native XLSX download hash differs from protected metadata.");
  assert(Number(exp.size_bytes)===downloaded.length,"XLSX download size differs from protected metadata.");
  const validated=await validateXlsx(downloadPath);
  assert(validated.status==="valid","XLSX package/content checks failed.");
  assert(Array.isArray(job.request?.payload?.line_items)&&job.request.payload.line_items.length===2,"Native XLSX job did not use exactly the two accepted rows.");
  assert(job.request?.payload?.pricing_reference?.id===fixtureId,"Native XLSX job lost its selected pricing reference.");
  assert(/completed/i.test(await page.locator("#resultStatus").innerText()),"UI did not report completed XLSX generation.");
  assert(/^Ready$/i.test((await page.locator("#outputStatusPill").innerText()).trim()),"XLSX status did not return to Ready.");
  const protectedUrl=new URL(exp.url,baseUrl).toString();
  const protectedResponse=await api.get(protectedUrl);
  assert(protectedResponse.status()===200,"Protected XLSX download returned HTTP "+protectedResponse.status());
  assert((protectedResponse.headers()["content-type"]||"").includes("spreadsheetml")||(protectedResponse.headers()["content-type"]||"").includes("application/vnd"),"Protected route has the wrong MIME type.");
  const protectedBytes=Buffer.from(await protectedResponse.body());
  assert(createHash("sha256").update(protectedBytes).digest("hex")===digest,"Protected XLSX response does not match UI download.");
  await fs.writeFile(path.join(dirs.downloads,"alpha1-protected.xlsx"),protectedBytes);
  await page.screenshot({path:path.join(dirs.captures,"08-current-xlsx-ready.png"),fullPage:true});
  const beforeLogoutResponse=await api.get(baseUrl+"/api/session");
  const beforeLogoutStatus=beforeLogoutResponse.status(); assert(beforeLogoutStatus===200,"Auth disappeared before logout.");
  assert((await beforeLogoutResponse.json()).authenticated===true,"Authenticated API context was not authenticated before logout.");
  const logout=page.waitForResponse((r)=>new URL(r.url()).pathname==="/logout"&&r.request().method()==="POST");
  await page.locator("#topbarLogoutLink").click();
  assert((await logout).status()===204,"UI logout did not return 204.");
  await page.waitForURL("**/signed-out");
  await page.screenshot({path:path.join(dirs.captures,"09-signed-out.png"),fullPage:true});
  const afterLogoutSessionStatus=(await api.get(baseUrl+"/api/session")).status(); assert(afterLogoutSessionStatus===401,"Retained session still authenticates after logout.");
  const afterLogoutListStatus=(await api.get(baseUrl+"/api/quote-sessions")).status(); assert(afterLogoutListStatus===401,"Quote-session route not denied after logout.");
  const afterLogoutXlsxStatus=(await api.get(protectedUrl)).status(); assert(afterLogoutXlsxStatus===401,"Protected XLSX still works after logout.");
  await Promise.allSettled(trace.pending);
  await writeJson('access-denial-proof.json',{anonymousStatus,beforeLogoutStatus,afterLogoutSessionStatus,afterLogoutListStatus,afterLogoutXlsxStatus,logoutStatus:204,sessionId,protectedUrl});
  assert(trace.draftRequests.length===1,"Expected exactly one mock draft request and zero retries.");
  assert(trace.external.length===0,"Browser attempted external network requests: "+JSON.stringify([...new Set(trace.external)]));
  assert(trace.console.length===0,"Browser console errors/warnings: "+trace.console.slice(0,5).join(" | "));
  assert(trace.pageErrors.length===0,"Browser errors: "+trace.pageErrors.slice(0,5).join(" | "));
  assert(!trace.jobs.some((entry)=>entry.request?.type==="generate_pdf"),"Harness unexpectedly invoked the PDF generation path.");
  completedChecks.push("generate_download_current_session_job_hash","xlsx_content_and_ui","logout_access_denial"); phase="browser_complete"; await writeJson("progress.json",{phase,completedChecks}); await writeJson("result.json",{result:"FRESH_ONLY_MOCK_ALPHA1_BROWSER_PHASE_COMPLETE",modifyExecuted:false,sessionId,workspace:"workspace-internal-alpha",profileId,pricingReference:fixtureId,mockDraftInvocations:1,realProviderCalls:0,normalizedRows:items.map((row)=>({selector:row.pricing_keyword,id:row.catalog_item_id,description:row.description,unit:row.unit,price:row.unit_price_override??row.effective_unit_price??row.catalog_unit_price})),includedDescription,digest,size:downloaded.length,xlsxValidation:validated,evidenceRoot:runRoot});
  await emitStdout(JSON.stringify({result:"FRESH_ONLY_MOCK_ALPHA1_BROWSER_PHASE_COMPLETE",sessionId,realProviderCalls:0,mockDraftInvocations:1,digest,evidenceRoot:runRoot}));
} catch (error) {
  await captureActualView(error).catch(()=>{});
  await writeJson("failure.json",{result:"FAIL",message:String(error?.message||error),stack:String(error?.stack||"")}).catch(()=>{});
  await emitStderr(error?.stack||error?.message||error);
  process.exitCode=1;
} finally {
  clearTimeout(watchdog); await captureActualView(null).catch(()=>{});
  const screen = (value) => Array.isArray(value) ? value.map(screen) : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, /token|password|authorization|cookie|secret|api_key|headers/i.test(key) ? "[SCREENED]" : screen(item)])) : value;
  await Promise.allSettled(trace.pending);
  await writeJson("trace-screened.json", screen({ ...trace, pending: undefined })).catch(()=>{});
  if (page && !page.isClosed()) await page.screenshot({ path: path.join(dirs.captures, "terminal.png"), fullPage: true }).catch(()=>{});
  if (api) await api.dispose().catch(()=>{});
  if (anonymousApi) await anonymousApi.dispose().catch(()=>{});
  if (context) await context.close().catch(()=>{});
  if (browser) await browser.close().catch(()=>{});
  await stopServer().catch(()=>{});
  await fs.writeFile(path.join(dirs.logs,"runner.stdout.log"),stdoutLog).catch(()=>{});
  await fs.writeFile(path.join(dirs.logs,"runner.stderr.log"),stderrLog).catch(()=>{});
}

// Native readability is a required gate of this same minimum-smoke execution.
if (!process.exitCode) {
  const child=spawn(python,[path.join(sourceRoot,"scripts/verify-alpha1-current-workbook.py"),runRoot],{cwd:sourceRoot,windowsHide:true,env:{...process.env,OPENAI_API_KEY:"",DEEPSEEK_API_KEY:""},stdio:["ignore","pipe","pipe"]});
  let output="",error="";child.stdout.on("data",b=>{output+=b;});child.stderr.on("data",b=>{error+=b;});
  const exit=await new Promise((resolve,reject)=>{child.once("error",reject);child.once("close",resolve);});
  await fs.writeFile(path.join(dirs.logs,"native-verifier.stdout"),output);await fs.writeFile(path.join(dirs.logs,"native-verifier.stderr"),error);
  if(exit!==0) throw new Error("Native workbook verification failed: "+error+output);
  const native=JSON.parse(output.trim().split(/\r?\n/).at(-1));
  assert(native.status==="NATIVE_WORKBOOK_OFFLINE_VERIFICATION_PASS","Native verifier did not report complete readability.");
  console.log(JSON.stringify({status:"ok",scope:"alpha1",result:"ALPHA1_FRESH_NORMAL_CURRENT_XLSX_ACCESS_AND_NATIVE_WORKBOOK_PASS",sessionId,native,modifyExecuted:false,realProviderCalls:0}));
}
