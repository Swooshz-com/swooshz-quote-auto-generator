#!/usr/bin/env node
// Remote checks for the internal redeploy rehearsal and the live read-only check.
// Run order and setup: docs/internal-uat-coolify-deploy.md, "Internal redeployment flow".
//
//   node scripts/rehearsal-check.mjs --profile seed|verify|reopen|live-readonly
//        --base-url https://<host> --result <file.json>
//        [--seed-file <seed result.json>] [--asset-out <app.js file>] [--no-ai]
//        [--allow-loopback-http [--file-storage]]
//
// Credentials come from the environment only, never arguments:
//   SQAG_REHEARSAL_LOGIN_EMAIL, SQAG_REHEARSAL_LOGIN_PASSWORD: the rehearsal app's
//     sign-in for seed, verify and reopen (the rehearsal host has no proxy auth,
//     so the app's sign-in is its access control)
//   SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL, SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD:
//     the operator's live sign-in for live-readonly, fed from the laptop credential
//     store with `toolkit env run`, so rehearsal logins never reach live
//   SQAG_REHEARSAL_LIVE_HOSTS (comma-separated hosts that writing profiles refuse)
// live-readonly is read-only on every channel the script opens: the browser
// context and every API request context allow only GET and HEAD (plus the
// browser's sign-in form POST), and anything else is refused and recorded.
// A loopback host skips the "must say rehearsal" rule and needs
// --allow-loopback-http, so never tunnel a live app to localhost.
// Rehearsal and live run database storage, where generation needs a saved
// company profile, so seed saves one and every new quote selects it.
// --file-storage (loopback test servers only) is for a file-storage server,
// which cannot save company profile layouts and does not need one.
// The result file holds metadata only: step status, timings, opaque ids and hashes.
import { signIn } from "./internal-password-browser-test.mjs";
import { pdfFontNames, unexpectedPdfFonts, validatePdf, validateXlsx } from "./lib/artifact-checks.mjs";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixtureDir = path.join(root, "tests", "fixtures", "quote-generator", "pricing-references", "synthetic-exhibition-fixture-pricing");
const layoutFixture = path.join(root, "tests", "fixtures", "quote-generator", "profiles", "synthetic-exhibition-fixture-template", "quotation-layout.xlsx");
const RESULT_SCHEMA = "sqag-rehearsal-check/v1";
const PROFILES = ["seed", "verify", "reopen", "live-readonly"];
const VALUE_FLAGS = new Set(["--profile", "--base-url", "--result", "--seed-file", "--asset-out"]);
const BOOLEAN_FLAGS = new Map([["--no-ai", "noAi"], ["--allow-loopback-http", "allowLoopbackHttp"], ["--file-storage", "fileStorage"]]);
const REHEARSAL_LOGIN_ENV = { email: "SQAG_REHEARSAL_LOGIN_EMAIL", password: "SQAG_REHEARSAL_LOGIN_PASSWORD" };
const LIVE_LOGIN_ENV = { email: "SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL", password: "SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD" };
const CREDENTIAL_ENV = [...Object.values(REHEARSAL_LOGIN_ENV), ...Object.values(LIVE_LOGIN_ENV)];
const READ_METHODS = new Set(["GET", "HEAD"]);
const WRITE_HELPERS = ["post", "put", "patch", "delete"];
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const PACK_ID = "rehearsal-synthetic-pricing";
const PACK_LABEL = "Rehearsal Synthetic Pricing";
// Database storage mode refuses generation without a saved company profile that
// carries a quotation layout, so the seed saves one and every new quote selects it.
// The server assigns a new profile its id, so the seed finds it again by label.
const COMPANY_PROFILE_ID = "rehearsal-synthetic-company"; // requested id; database mode assigns its own
const COMPANY_PROFILE_LABEL = "Rehearsal Synthetic Company";
const COMPANY_PROFILE_PREFIX = "company:";
const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const CUSTOMER = "Rehearsal Synthetic Customer";
const PROJECT = "Rehearsal Synthetic Exhibition";
const COMPANY = "Rehearsal Synthetic Fabrication";
const SIGNATORY = "Rehearsal Signatory";
const TERMINAL = ["completed", "degraded", "needs_review", "blocked", "failed"];
const AI_DRAFT_TIMEOUT_MS = 240000;
const GENERATION_TIMEOUT_MS = 120000;
const SAVE_TIMEOUT_MS = 30000;
// The pricing-reference save runs AI enrichment on a real server (about 36 s
// on the d07c910 rehearsal), so it gets a longer limit than other saves.
const PRICING_SAVE_TIMEOUT_MS = 120000;

export class UsageError extends Error {}
export class ReadOnlyViolation extends Error {}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

// Error text in the result never carries URLs, emails or long tokens.
export function scrub(value) {
  return String(value?.message || value || "unknown error")
    .replace(/[a-z][a-z0-9+.-]*:\/\/\S+/gi, "<url>")
    .replace(/\S+@\S+/g, "<email>")
    .replace(/[A-Za-z0-9+/=_-]{32,}/g, "<token>")
    .replace(/[\r\n]+/g, " ")
    .slice(0, 400);
}

export function parseArgs(argv, env = process.env) {
  const credentialValues = CREDENTIAL_ENV.map((name) => env[name]).filter((value) => typeof value === "string" && value.length >= 4);
  const options = { noAi: false, allowLoopbackHttp: false, fileStorage: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = String(argv[index]);
    if (credentialValues.some((value) => arg.includes(value))) {
      throw new UsageError("a credential was passed as an argument; credentials come from the environment only");
    }
    const equals = arg.startsWith("--") ? arg.indexOf("=") : -1;
    const flag = equals > 0 ? arg.slice(0, equals) : arg;
    if (BOOLEAN_FLAGS.has(flag) && equals < 0) {
      options[BOOLEAN_FLAGS.get(flag)] = true;
    } else if (VALUE_FLAGS.has(flag)) {
      const value = equals > 0 ? arg.slice(equals + 1) : argv[++index];
      if (typeof value !== "string" || !value || value.startsWith("--")) throw new UsageError(`${flag} needs a value`);
      if (credentialValues.some((secret) => value.includes(secret))) {
        throw new UsageError("a credential was passed as an argument; credentials come from the environment only");
      }
      options[flag.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
    } else if (/pass|secret|token|user|email|login|auth|cred/i.test(flag)) {
      throw new UsageError("credentials come from the environment only, never arguments");
    } else {
      throw new UsageError("unknown argument (not shown)");
    }
  }
  if (!PROFILES.includes(options.profile)) throw new UsageError(`--profile must be one of ${PROFILES.join(", ")}`);
  if (!options.baseUrl) throw new UsageError("--base-url is required");
  if (!options.result) throw new UsageError("--result is required");
  if (["verify", "reopen"].includes(options.profile) && !options.seedFile) throw new UsageError(`--seed-file is required for ${options.profile}`);
  if (options.fileStorage && !options.allowLoopbackHttp) throw new UsageError("--file-storage is only for a loopback test server");
  options.baseUrl = checkBaseUrl(options.baseUrl, options.profile, options.allowLoopbackHttp, env);
  options.credentials = readCredentials(env, options.profile === "live-readonly" ? LIVE_LOGIN_ENV : REHEARSAL_LOGIN_ENV);
  return options;
}

export function checkBaseUrl(raw, profile, allowLoopbackHttp, env = process.env) {
  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new UsageError("--base-url is not a URL");
  }
  if (url.username || url.password) throw new UsageError("--base-url must not carry credentials");
  if (url.pathname !== "/" || url.search || url.hash) throw new UsageError("--base-url must be an origin with no path");
  const host = url.hostname.toLowerCase();
  const loopback = LOOPBACK_HOSTS.has(host);
  if (allowLoopbackHttp && !loopback) throw new UsageError("--allow-loopback-http is only for a loopback test server");
  if (url.protocol === "http:") {
    if (!allowLoopbackHttp) throw new UsageError("--base-url must use https");
  } else if (url.protocol !== "https:") {
    throw new UsageError("--base-url must use https");
  }
  if (profile !== "live-readonly") {
    if (loopback && !allowLoopbackHttp) {
      throw new UsageError(`profile ${profile} writes data, so a loopback host needs --allow-loopback-http (local test servers only)`);
    }
    const denied = String(env.SQAG_REHEARSAL_LIVE_HOSTS || "").split(",").map((item) => item.trim().toLowerCase()).filter(Boolean);
    if (denied.includes(host)) throw new UsageError(`profile ${profile} writes data and refuses a live host`);
    if (!loopback && !host.includes("rehearsal")) {
      throw new UsageError(`profile ${profile} writes data, so its host name must say rehearsal`);
    }
  }
  return url.origin;
}

function readCredentials(env, names) {
  const email = String(env[names.email] || "");
  const password = String(env[names.password] || "");
  if (!email || !password) throw new UsageError(`set ${names.email} and ${names.password}`);
  return { email, password };
}

function blockedPath(raw, baseUrl) {
  try {
    const url = new URL(String(raw), baseUrl);
    return url.origin === baseUrl ? url.pathname.replace(/[A-Za-z0-9_-]{16,}/g, "<id>") : "<other origin>";
  } catch {
    return "<unparsed>";
  }
}

function requestTarget(urlOrRequest) {
  if (typeof urlOrRequest === "string") return { url: urlOrRequest, method: "" };
  return { url: urlOrRequest?.url?.() ?? "", method: String(urlOrRequest?.method?.() ?? "") };
}

// Wraps a Playwright API request context so only GET and HEAD reach the server.
// Every other method throws ReadOnlyViolation before any request is sent and is
// recorded in `blocked` (method and scrubbed path only).
export function readOnlyRequestContext(api, blocked, baseUrl) {
  const refuse = (method, raw) => {
    blocked.push({ channel: "api", method, path: blockedPath(raw, baseUrl) });
    throw new ReadOnlyViolation(`live-readonly refused a ${method} request`);
  };
  return new Proxy(api, {
    get(target, property) {
      if (WRITE_HELPERS.includes(property)) {
        return async (url) => refuse(property.toUpperCase(), url);
      }
      if (property === "get" || property === "head") {
        const expected = property.toUpperCase();
        return async (url, options = {}) => {
          const method = String(options?.method || expected).toUpperCase();
          if (method !== expected) return refuse(method, url);
          return target[property](url, options);
        };
      }
      if (property === "fetch") {
        return async (urlOrRequest, options = {}) => {
          const requested = requestTarget(urlOrRequest);
          const method = String(options?.method || requested.method || "GET").toUpperCase();
          if (!READ_METHODS.has(method)) return refuse(method, requested.url);
          return target.fetch(urlOrRequest, options);
        };
      }
      const value = Reflect.get(target, property, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

// In live-readonly every request context the run opens comes through here.
function guardedPlaywright(playwright, run) {
  const request = {
    newContext: async (options) => readOnlyRequestContext(await playwright.request.newContext(options), run.readOnly.blocked, run.baseUrl),
  };
  return new Proxy(playwright, {
    get(target, property) {
      if (property === "request") return request;
      return Reflect.get(target, property, target);
    },
  });
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function waitUntil(predicate, label, timeoutMs = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const value = await predicate();
    if (value) return value;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

class Run {
  constructor(options) {
    this.options = options;
    this.baseUrl = options.baseUrl;
    this.result = {
      schema: RESULT_SCHEMA, profile: options.profile, result: "running",
      no_ai: options.noAi, started_at: new Date().toISOString(), steps: [],
    };
    this.trace = { jobs: [], saves: [], normalizations: [], pending: [], sequence: 0 };
    this.readOnly = null;
    if (options.profile === "live-readonly") {
      // Attached before the first step so a failed run still shows what was tried.
      this.readOnly = { writes: [], blocked: [] };
      this.result.write_requests = this.readOnly.writes;
      this.result.blocked_requests = this.readOnly.blocked;
    }
  }

  async step(name, action) {
    const started = Date.now();
    const entry = { name, status: "running" };
    this.result.steps.push(entry);
    try {
      const detail = await action();
      entry.status = "pass";
      if (detail !== undefined) entry.detail = detail;
      return detail;
    } catch (error) {
      entry.status = "fail";
      entry.error = scrub(error);
      throw error;
    } finally {
      entry.ms = Date.now() - started;
    }
  }

  skip(name, reason) {
    this.result.steps.push({ name, status: "skipped", reason });
  }
}

async function loadFixture() {
  const reference = JSON.parse(await fs.readFile(path.join(fixtureDir, "reference.json"), "utf8"));
  const catalog = JSON.parse(await fs.readFile(path.join(fixtureDir, "pricing-catalog.json"), "utf8"));
  const usable = (catalog.items || []).filter((item) => {
    const price = Number(item.sale_unit_price ?? item.default_quote_amount);
    return item?.id && item.description && item.section && item.unit_hint && Number.isFinite(price) && price > 0;
  });
  const first = usable.find((item) => item.unit_hint === "sqm") || usable[0];
  const second = usable.find((item) => item.id !== first?.id && item.section !== first?.section) || usable.find((item) => item.id !== first?.id);
  assert(first && second, "Synthetic pricing fixture has fewer than two usable items.");
  return { reference, catalog, items: [first, second].map((item) => ({ ...item, price: Number(item.sale_unit_price ?? item.default_quote_amount) })) };
}

function attachTrace(run, page) {
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.origin === run.baseUrl && url.pathname === "/api/quote-sessions" && request.method() === "POST") {
      request.__rehearsalSequence = ++run.trace.sequence;
    }
  });
  page.on("response", (response) => {
    const task = (async () => {
      const request = response.request();
      const url = new URL(response.url());
      if (url.origin !== run.baseUrl || !(response.headers()["content-type"] || "").includes("application/json")) return;
      let payload = null;
      try { payload = request.postDataJSON(); } catch { payload = null; }
      if (url.pathname === "/api/quote-sessions" && request.method() === "POST") {
        run.trace.saves.push({ sequence: request.__rehearsalSequence || 0, status: response.status(), payload, body: await response.json() });
      } else if (["/api/line-items/normalize", "/api/quote-sessions/pricing-selections"].includes(url.pathname) && request.method() === "POST") {
        run.trace.normalizations.push({ status: response.status(), body: await response.json() });
      } else if (url.pathname === "/api/jobs" && request.method() === "POST") {
        run.trace.jobs.push({ type: payload?.type || "", accepted: await response.json(), status: response.status(), polls: [] });
      } else {
        const match = url.pathname.match(/^\/api\/jobs\/([A-Za-z0-9_-]+)$/);
        if (match && request.method() === "GET") {
          const job = [...run.trace.jobs].reverse().find((item) => item.accepted?.job_id === match[1]);
          if (job) job.polls.push(await response.json());
        }
      }
    })();
    run.trace.pending.push(task);
    task.catch(() => {});
  });
}

async function getJson(api, url, label) {
  const response = await api.get(url);
  assert(response.ok(), `${label} returned HTTP ${response.status()}.`);
  return response.json();
}

async function csrfFor(run) {
  const session = await getJson(run.api, `${run.baseUrl}/api/session`, "Session");
  assert(session.authenticated === true, "The app session is not authenticated.");
  assert(session.csrf_header && session.csrf_token, "The app session has no CSRF token.");
  return { [session.csrf_header]: session.csrf_token, Origin: run.baseUrl };
}

async function postJson(run, route, data, label, timeout = SAVE_TIMEOUT_MS) {
  const response = await run.api.post(`${run.baseUrl}${route}`, { data, headers: await csrfFor(run), timeout });
  const body = await response.json().catch(() => ({}));
  assert(response.ok(), `${label} returned HTTP ${response.status()} (${scrub(body.errors?.[0] || body.status || "rejected")}).`);
  return body;
}

async function quoteSession(run, sessionId) {
  const body = await getJson(run.api, `${run.baseUrl}/api/quote-sessions/${encodeURIComponent(sessionId)}`, "Quote readback");
  assert(body.quote_session?.session_id === sessionId, "Quote readback returned a different quote.");
  return body.quote_session;
}

async function download(run, record, kind) {
  assert(record?.exists === true && record?.stale === false, `Current ${kind} export is missing or stale.`);
  const response = await run.api.get(new URL(record.url, run.baseUrl).toString());
  assert(response.status() === 200, `${kind} download returned HTTP ${response.status()}.`);
  const bytes = Buffer.from(await response.body());
  const digest = sha256(bytes);
  assert(bytes.length > 0 && Number(record.size_bytes) === bytes.length, `${kind} size differs from its saved record.`);
  assert(String(record.sha256 || "").toLowerCase() === digest, `${kind} digest differs from its saved record.`);
  const file = path.join(run.workDir, `${kind}-${digest.slice(0, 12)}.${kind}`);
  await fs.writeFile(file, bytes);
  return { file, sha256: digest, bytes: bytes.length };
}

async function makePng(context, svg) {
  const page = await context.newPage();
  try {
    await page.setContent(svg, { waitUntil: "domcontentloaded" });
    return await page.locator("svg").screenshot({ type: "png" });
  } finally {
    await page.close();
  }
}

async function syntheticImages(context) {
  const render = await makePng(context, `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360"><rect width="640" height="360" fill="#f1eee7"/><rect x="68" y="58" width="500" height="236" rx="10" fill="#d8e0da"/><path d="M68 94h260v200H68z" fill="#aec5ba"/><path d="M328 94h240v200H328z" fill="#d9d2c6"/><rect x="126" y="120" width="170" height="46" rx="4" fill="#e7b75d"/><rect x="386" y="182" width="112" height="70" rx="6" fill="#55766a"/><rect x="102" y="294" width="424" height="28" rx="4" fill="#777c76"/></svg>`);
  const logo = await makePng(context, `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="96" viewBox="0 0 240 96"><rect width="240" height="96" rx="12" fill="#19392f"/><path d="M24 65V31h12l16 20 16-20h12v34H68V48L52 67 36 48v17z" fill="#e1c07a"/></svg>`);
  return { render, logo };
}

async function signInBrowser(run) {
  run.page = await run.context.newPage();
  attachTrace(run, run.page);
  await signIn(run.page, run.baseUrl, run.options.credentials.email, run.options.credentials.password);
  const state = await run.context.storageState();
  run.api = await run.playwright.request.newContext({ storageState: state });
  const session = await getJson(run.api, `${run.baseUrl}/api/session`, "Session after sign-in");
  assert(session.authenticated === true, "Sign-in did not authenticate the session.");
  assert(session.permissions?.canGenerateQuote === true, "The signed-in account cannot generate quotes.");
  return { can_manage_pricing: session.permissions?.canManagePricingReferences === true };
}

async function seedPricingPack(run, fixture) {
  const profiles = await getJson(run.api, `${run.baseUrl}/api/profiles`, "Profiles");
  const existing = (profiles.pricing_references || []).find((item) => item.label === PACK_LABEL);
  if (existing) return { pricing_reference_id: existing.id, reused: true };
  const body = await postJson(run, "/api/settings/pricing-references", {
    id: PACK_ID, source: "", label: PACK_LABEL,
    description: "Synthetic rehearsal-only pricing reference.",
    tax: fixture.reference.tax, currency: fixture.reference.currency,
    items: fixture.catalog.items, update_existing: false, editing_reference_id: "",
  }, "Pricing reference save", PRICING_SAVE_TIMEOUT_MS);
  assert(body.pricing_reference?.id, "Pricing reference save returned no id.");
  return { pricing_reference_id: body.pricing_reference.id, reused: false };
}

// The profiles list's company entries, never a text search of the whole response.
function companyProfileEntry(profiles, match) {
  return (profiles.company_profiles || []).find((item) => item?.source === "company" && item.id && match(item));
}

async function seedCompanyProfile(run) {
  const profiles = await getJson(run.api, `${run.baseUrl}/api/profiles`, "Profiles");
  const existing = companyProfileEntry(profiles, (item) => item.label === COMPANY_PROFILE_LABEL);
  if (existing) {
    run.companyProfileId = existing.id;
    return { company_profile_id: existing.id, reused: true };
  }
  const layout = await fs.readFile(layoutFixture);
  const body = await postJson(run, "/api/settings/profiles", {
    operation: "create", id: COMPANY_PROFILE_ID, label: COMPANY_PROFILE_LABEL,
    description: "Synthetic rehearsal-only company profile.", defaults: {},
    pack: { quotation_layout: { filename: "quotation-layout.xlsx", data_url: `data:${XLSX_MIME};base64,${layout.toString("base64")}` } },
  }, "Company profile save");
  const savedId = body.profile?.id;
  assert(savedId, "Company profile save returned no id.");
  const after = await getJson(run.api, `${run.baseUrl}/api/profiles`, "Profiles");
  assert(companyProfileEntry(after, (item) => item.id === savedId && item.label === COMPANY_PROFILE_LABEL), "The saved company profile is not in the profiles list.");
  run.companyProfileId = savedId;
  return { company_profile_id: savedId, reused: false };
}

async function openDashboard(run) {
  await run.page.goto(`${run.baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await run.page.locator("#newQuoteButton").waitFor({ state: "visible", timeout: 30000 });
}

async function createQuoteDraft(run, images, projectNumber) {
  await freshPage(run);
  const page = run.page;
  await openDashboard(run);
  await page.locator("#newQuoteButton").click();
  await page.locator("#imageIntake").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#imageInput").setInputFiles({ name: "rehearsal-booth-render.png", mimeType: "image/png", buffer: images.render });
  await page.locator("#fileList .file-item").filter({ hasText: "rehearsal-booth-render.png" }).waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#sideNextButton").click();
  await page.locator("#customerDetailsPanel").waitFor({ state: "visible", timeout: 15000 });
  await page.locator("#profileSelect").selectOption({ label: PACK_LABEL });
  for (const [selector, value] of [
    ["#clientNameEditor", CUSTOMER], ["#clientAttentionEditor", "Rehearsal Review Team"],
    ["#clientTitleEditor", "Synthetic Project Lead"], ["#clientAddressEditor", "Synthetic address"],
    ["#projectTitleEditor", PROJECT], ["#showName", "Rehearsal Synthetic Show"],
    ["#projectNumberEditor", projectNumber],
  ]) await page.locator(selector).fill(value);
  await page.locator("#quoteDate").fill(new Date().toISOString().slice(0, 10));
  await page.locator("#sideNextButton").click();
  await page.locator("#quoteCompanyPanel").waitFor({ state: "visible", timeout: 15000 });
  const companyOption = run.options.fileStorage ? "" : `${COMPANY_PROFILE_PREFIX}${run.companyProfileId}`;
  if (companyOption) {
    assert(run.companyProfileId, "No seeded company profile to select.");
    const option = page.locator(`#presetSelect option[value="${companyOption}"]`);
    await option.waitFor({ state: "attached", timeout: 15000 }).catch(() => {
      throw new Error("The seeded company profile is not offered on Quote Company.");
    });
    await page.locator("#presetSelect").selectOption(companyOption);
  }
  await page.locator("#headerLogoInput").setInputFiles({ name: "rehearsal-logo.png", mimeType: "image/png", buffer: images.logo });
  for (const [selector, value] of [
    ["#headerDetailsEditor", "Rehearsal Synthetic | Singapore"], ["#quoteCompanyNameEditor", COMPANY],
    ["#acceptanceTextEditor", "Synthetic approval for testing"], ["#companySignatoryEditor", SIGNATORY],
    ["#companyTitleEditor", "Project Manager"], ["#companyDateLabelEditor", "Date:"],
    ["#personLabelEditor", "Name:"], ["#stampLabelEditor", "Company stamp"], ["#dateLabelEditor", "Date:"],
  ]) await page.locator(selector).fill(value);
  const before = run.trace.sequence;
  await page.locator("#topbarBrandButton").click();
  await page.locator("#quoteDashboardPanel.is-active").waitFor({ state: "visible", timeout: 20000 });
  const saved = await waitUntil(() => [...run.trace.saves].reverse().find((item) => item.sequence > before && item.status === 200
    && item.body?.quote_session?.session_id
    && item.payload?.draft_state?.quoteDetails?.company?.name === COMPANY
    && item.payload?.draft_state?.quoteDetails?.signature?.company_signatory === SIGNATORY
    && (!companyOption || item.payload?.quote_company_profile?.id === companyOption)), "the new quote's saved details and company profile", 30000);
  const sessionId = saved.body.quote_session.session_id;
  const readback = await quoteSession(run, sessionId);
  assert(readback.customer_summary?.customer_name === CUSTOMER, "The new quote lost its customer details.");
  return sessionId;
}

// The same deterministic basis the alpha smoke uses: two catalogue lines, no AI.
async function seedBasis(run, sessionId, fixture) {
  const signature = await run.page.evaluate(() => referenceFilesDependencySignature());
  const current = await quoteSession(run, sessionId);
  const { session_id, customer_summary, quote_company_profile, pricing_reference, commercials, status, draft_state } = current;
  const payload = structuredClone({ session_id, customer_summary, quote_company_profile, pricing_reference, commercials, status, draft_state, expected_draft_version: current.draft_version });
  const sections = fixture.items.map((item, index) => ({
    id: `rehearsal-section-${index + 1}`, title: item.section,
    lines: [{ id: `rehearsal-line-${index + 1}`, text: item.description, tag: "Include", confidence: 100, quantity: 1, unit: item.unit_hint, pricing_keyword: item.id }],
  }));
  const lineItems = fixture.items.map((item, index) => ({
    section: item.section, description: item.description, quantity: 1, unit: item.unit_hint, pricing_keyword: item.id,
    source_basis_line_id: `rehearsal-line-${index + 1}`,
  }));
  Object.assign(payload.draft_state, {
    quoteBasisSections: sections, lineItems, outputRows: [], originalOutputRows: [],
    originalAnalysisSnapshot: { quote_basis_sections: sections, line_items: lineItems, source: "edited", analysis_mode: "standard", reference_file_signature: signature, warnings: [] },
    basisConfirmed: false, aiFailed: false, workflowStage: "basis_review", draftSource: "edited",
  });
  const body = await postJson(run, "/api/quote-sessions", payload, "Basis save");
  assert(body.status === "saved" && body.quote_session?.session_id === sessionId, "The basis was not saved.");
}

// A fresh browser context restores the quote from the server, not from this
// browser's own saved state, as a user on another computer would see it.
async function freshPage(run) {
  const { cookies } = await run.context.storageState();
  const storageState = { cookies, origins: [] };
  const previous = run.context;
  run.context = await run.browser.newContext({ acceptDownloads: true, storageState });
  run.page = await run.context.newPage();
  attachTrace(run, run.page);
  await previous.close().catch(() => {});
}

async function reopenQuote(run, sessionId) {
  await freshPage(run);
  const page = run.page;
  await openDashboard(run);
  const card = page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`);
  await card.waitFor({ state: "visible", timeout: 20000 });
  await card.click();
  await page.locator('#dashboardSelectedSessionPanel [data-dashboard-panel-action="modify-session"]').click();
  await page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 30000 });
}

async function waitForJob(run, type, fromIndex, timeoutMs) {
  const job = await waitUntil(() => run.trace.jobs.slice(fromIndex).find((item) => item.type === type), `the ${type} job`, 30000);
  assert(job.status === 202, `The ${type} job was refused (HTTP ${job.status}).`);
  return waitUntil(() => job.polls.find((poll) => TERMINAL.includes(poll?.status)), `the ${type} job result`, timeoutMs).catch((error) => {
    const last = job.polls.at(-1);
    throw new Error(`${error.message} Polls: ${job.polls.length}, last status: ${scrub(last?.status || "none")}.`);
  });
}

async function confirmAndGenerate(run, sessionId, fixture) {
  await reopenQuote(run, sessionId);
  const page = run.page;
  await page.locator("#sideNextButton").waitFor({ state: "visible", timeout: 15000 });
  assert((await page.locator("#sideNextButton").innerText()).includes("Confirm"), "The reopened quote is not at basis review.");
  const normalizationsBefore = run.trace.normalizations.length;
  await page.locator("#sideNextButton").click();
  const normalization = await waitUntil(() => run.trace.normalizations[normalizationsBefore], "basis confirmation", 30000).catch(async (error) => {
    const reason = await page.evaluate(() => [basisConfirmBlockReason(), missingDetailFields().join(", ")].filter(Boolean).join("; ")).catch(() => "");
    throw new Error(`${error.message} ${reason}`.trim());
  });
  assert(normalization.status === 200 && normalization.body?.status === "normalized", "Basis confirmation failed.");
  for (const item of fixture.items) {
    const row = (normalization.body.line_items || []).find((line) => line.pricing_keyword === item.id);
    assert(row && Number(row.catalog_unit_price) === item.price, "A confirmed line lost its catalogue price.");
  }
  await page.locator("#pricingMatchesBody tr").nth(fixture.items.length - 1).waitFor({ state: "visible", timeout: 20000 });

  const xlsxJobs = run.trace.jobs.length;
  const downloadPromise = page.waitForEvent("download", { timeout: GENERATION_TIMEOUT_MS }).catch(() => null);
  await page.locator("#sideDownloadButton").click();
  const xlsxTerminal = await waitForJob(run, "generate", xlsxJobs, GENERATION_TIMEOUT_MS);
  assert(xlsxTerminal.status === "completed" && xlsxTerminal.result?.status === "completed", `XLSX generation ended ${xlsxTerminal.status}.`);
  await downloadPromise;

  const pdfJobs = run.trace.jobs.length;
  await page.locator("#sideViewPdfButton").click();
  const pdfTerminal = await waitForJob(run, "generate_pdf", pdfJobs, GENERATION_TIMEOUT_MS);
  assert(pdfTerminal.status === "completed" && pdfTerminal.result?.status === "completed", `PDF generation ended ${pdfTerminal.status}.`);
  await page.locator("#excelGeneratingModal.is-ready").waitFor({ state: "visible", timeout: 30000 }).catch(() => {});
  await page.keyboard.press("Escape").catch(() => {});

  const readback = await quoteSession(run, sessionId);
  assert(readback.status?.quote_generated === true, "The generated quote is not marked current.");
  const xlsx = await download(run, readback.exports?.xlsx, "xlsx");
  const pdf = await download(run, readback.exports?.pdf, "pdf");
  assert(readback.exports.xlsx.publication_id && readback.exports.pdf.publication_id === readback.exports.xlsx.publication_id, "XLSX and PDF do not share one publication.");
  return { readback, xlsx, pdf };
}

async function checkArtifacts(run, artifacts, projectNumber, fixture) {
  const lineAmounts = fixture.items.map((item) => Math.round(item.price * 100) / 100);
  const xlsx = await validateXlsx(artifacts.xlsx.file, [CUSTOMER, PROJECT, projectNumber, ...fixture.items.map((item) => item.description)], lineAmounts, null);
  const pdf = await validatePdf(artifacts.pdf.file);
  const fonts = await pdfFontNames(artifacts.pdf.file);
  const unexpected = unexpectedPdfFonts(fonts);
  assert(fonts.length > 0, "The PDF names no fonts.");
  assert(unexpected.length === 0, `The PDF uses fonts outside the release set: ${unexpected.join(", ").slice(0, 200)}.`);
  return { xlsx_sheets: xlsx.sheets, pdf_pages: pdf.pages, pdf_fonts: fonts };
}

async function capturedAppJs(run) {
  const page = await run.api.get(`${run.baseUrl}/`);
  assert(page.status() === 200, `The app page returned HTTP ${page.status()}.`);
  const html = await page.text();
  const match = html.match(/\/static\/app\.js(?:\?v=\d+)?/);
  assert(match, "The app page does not reference app.js.");
  const response = await run.api.get(`${run.baseUrl}${match[0]}`);
  assert(response.status() === 200, `app.js returned HTTP ${response.status()}.`);
  const bytes = Buffer.from(await response.body());
  if (run.options.assetOut) await fs.writeFile(run.options.assetOut, bytes);
  return { app_js_sha256: sha256(bytes), app_js_bytes: bytes.length, saved: Boolean(run.options.assetOut) };
}

async function readSeed(file) {
  let seed;
  try {
    seed = JSON.parse(await fs.readFile(file, "utf8"));
  } catch {
    throw new UsageError("--seed-file is unreadable");
  }
  const ids = seed?.seed;
  if (seed?.schema !== RESULT_SCHEMA || seed.profile !== "seed" || seed.result !== "pass" || !ids?.published_session_id || !ids?.draft_session_id || !ids?.pricing_reference_id || !("company_profile_id" in ids)) {
    throw new UsageError("--seed-file is not a passed seed result");
  }
  return ids;
}

async function reopenSeed(run, seed) {
  await run.step("pricing reference present", async () => {
    const profiles = await getJson(run.api, `${run.baseUrl}/api/profiles`, "Profiles");
    assert((profiles.pricing_references || []).some((item) => item.id === seed.pricing_reference_id), "The seeded pricing reference is missing.");
  });
  if (run.options.fileStorage) {
    run.skip("company profile present", "--file-storage: the server has no database company profiles.");
  } else {
    await run.step("company profile present", async () => {
      const profiles = await getJson(run.api, `${run.baseUrl}/api/profiles`, "Profiles");
      const entry = companyProfileEntry(profiles, (item) => item.id === seed.company_profile_id);
      assert(entry && entry.label === COMPANY_PROFILE_LABEL, "The seeded company profile is missing.");
      run.companyProfileId = entry.id;
    });
  }
  await run.step("published quote reopens", async () => {
    const readback = await quoteSession(run, seed.published_session_id);
    assert(readback.customer_summary?.customer_name === CUSTOMER, "The published quote lost its customer details.");
    const xlsx = await download(run, readback.exports?.xlsx, "xlsx");
    const pdf = await download(run, readback.exports?.pdf, "pdf");
    assert(xlsx.sha256 === seed.published_exports.xlsx_sha256 && pdf.sha256 === seed.published_exports.pdf_sha256, "The published files changed since the seed.");
    await reopenQuote(run, seed.published_session_id);
    await run.page.locator("#pricingMatchesBody tr").first().waitFor({ state: "visible", timeout: 20000 });
    return { xlsx_sha256: xlsx.sha256, pdf_sha256: pdf.sha256 };
  });
  await run.step("draft quote reopens", async () => {
    const readback = await quoteSession(run, seed.draft_session_id);
    assert(readback.customer_summary?.customer_name === CUSTOMER, "The draft quote lost its customer details.");
    assert(readback.status?.quote_generated !== true, "The draft quote is marked generated.");
    await reopenQuote(run, seed.draft_session_id);
  });
}

async function newGeneratedQuote(run, images, fixture, label) {
  const projectNumber = `REH-${Date.now().toString(36).toUpperCase()}`;
  const sessionId = await run.step(`${label}: create quote`, () => createQuoteDraft(run, images, projectNumber));
  await run.step(`${label}: deterministic basis`, () => seedBasis(run, sessionId, fixture));
  const artifacts = await run.step(`${label}: confirm and generate XLSX and PDF`, async () => {
    const value = await confirmAndGenerate(run, sessionId, fixture);
    run.lastArtifacts = value;
    return { xlsx_sha256: value.xlsx.sha256, pdf_sha256: value.pdf.sha256 };
  });
  await run.step(`${label}: check XLSX, PDF and fonts`, () => checkArtifacts(run, run.lastArtifacts, projectNumber, fixture));
  return { sessionId, artifacts };
}

async function aiAnalysis(run, images) {
  const projectNumber = `REH-AI-${Date.now().toString(36).toUpperCase()}`;
  const sessionId = await createQuoteDraft(run, images, projectNumber);
  await reopenQuote(run, sessionId).catch(() => {});
  const jobsBefore = run.trace.jobs.length;
  // The same app function the analysis Start button calls.
  await run.page.evaluate(() => confirmStartAnalysis("standard"));
  const terminal = await waitForJob(run, "draft", jobsBefore, AI_DRAFT_TIMEOUT_MS);
  assert(terminal.status === "completed", `AI image analysis ended ${terminal.status}.`);
  const readback = await waitUntil(async () => {
    const value = await quoteSession(run, sessionId);
    return (value.draft_state?.quoteBasisSections || []).length > 0 ? value : null;
  }, "the AI basis to be saved", 30000);
  return { basis_sections: readback.draft_state.quoteBasisSections.length };
}

async function liveReadonly(run) {
  const { writes, blocked } = run.readOnly;
  await run.context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (method === "GET" || method === "HEAD" || (method === "POST" && url.origin === run.baseUrl && url.pathname === "/login")) {
      if (method === "POST") writes.push({ method, path: url.pathname });
      return route.continue();
    }
    blocked.push({ channel: "browser", method, path: blockedPath(url.toString(), run.baseUrl) });
    return route.abort();
  });
  await run.step("sign in", () => signInBrowser(run));
  await run.step("session", async () => {
    const session = await getJson(run.api, `${run.baseUrl}/api/session`, "Session");
    assert(session.authenticated === true, "The session is not authenticated.");
  });
  await run.step("quote list loads", async () => {
    const body = await getJson(run.api, `${run.baseUrl}/api/quote-sessions`, "Quote list");
    const list = body.quote_sessions ?? body.sessions ?? body.items;
    assert(Array.isArray(list), "The quote list response has no list.");
    return { quotes: list.length };
  });
  await run.step("served app.js", () => capturedAppJs(run));
}

export async function runChecks(options, playwright) {
  const run = new Run(options);
  run.playwright = run.readOnly ? guardedPlaywright(playwright, run) : playwright;
  run.workDir = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-rehearsal-"));
  let browser;
  try {
    await run.step("health", async () => {
      const api = await run.playwright.request.newContext();
      try {
        const health = await getJson(api, `${run.baseUrl}/api/health`, "Health");
        assert(health.status === "ok", "Health did not report ok.");
      } finally {
        await api.dispose();
      }
    });
    browser = await run.playwright.chromium.launch({ headless: true });
    run.browser = browser;
    run.context = await browser.newContext({ acceptDownloads: true });
    if (options.profile === "live-readonly") {
      await liveReadonly(run);
    } else {
      await run.step("sign in", () => signInBrowser(run));
      const fixture = await loadFixture();
      if (options.profile === "seed") {
        const images = await syntheticImages(run.context);
        const pack = await run.step("pricing reference", () => seedPricingPack(run, fixture));
        let company = { company_profile_id: null };
        if (options.fileStorage) run.skip("company profile", "--file-storage: the server has no database company profiles.");
        else company = await run.step("company profile", () => seedCompanyProfile(run));
        const published = await newGeneratedQuote(run, images, fixture, "published quote");
        const draftId = await run.step("draft quote", () => createQuoteDraft(run, images, `REH-DRAFT-${Date.now().toString(36).toUpperCase()}`));
        run.skip("damaged pricing reference", "No app route creates a damaged pack; the fail-closed message stays covered by the unit tests.");
        run.result.seed = {
          pricing_reference_id: pack.pricing_reference_id,
          company_profile_id: company.company_profile_id,
          published_session_id: published.sessionId,
          draft_session_id: draftId,
          published_exports: { xlsx_sha256: published.artifacts.xlsx_sha256, pdf_sha256: published.artifacts.pdf_sha256 },
        };
      } else {
        const seed = await readSeed(options.seedFile);
        await reopenSeed(run, seed);
        if (options.profile === "verify") {
          const images = await syntheticImages(run.context);
          await newGeneratedQuote(run, images, fixture, "new quote");
          if (options.noAi) run.skip("AI image analysis", "--no-ai");
          else await run.step("AI image analysis", () => aiAnalysis(run, images));
        }
        await run.step("served app.js", () => capturedAppJs(run));
      }
    }
    run.result.result = "pass";
  } catch (error) {
    run.result.result = "fail";
    if (!run.result.steps.some((entry) => entry.status === "fail")) run.result.error = scrub(error);
  } finally {
    await Promise.allSettled(run.trace.pending);
    if (run.api) await run.api.dispose().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    await fs.rm(run.workDir, { recursive: true, force: true }).catch(() => {});
    run.result.finished_at = new Date().toISOString();
  }
  return run.result;
}

async function main(argv) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    if (error instanceof UsageError) {
      console.error(`rehearsal-check: ${error.message}`);
      return 2;
    }
    throw error;
  }
  const playwright = await import("playwright");
  const result = await runChecks(options, playwright);
  await fs.writeFile(options.result, JSON.stringify(result, null, 2) + "\n", "utf8");
  console.log(JSON.stringify({ profile: result.profile, result: result.result, steps: result.steps.length }));
  return result.result === "pass" ? 0 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main(process.argv.slice(2)).then((code) => { process.exitCode = code; }, (error) => {
    console.error(`rehearsal-check: ${scrub(error)}`);
    process.exitCode = 1;
  });
}
