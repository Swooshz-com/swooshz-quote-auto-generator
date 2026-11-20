#!/usr/bin/env node
// Remote checks for the internal redeploy rehearsal and the live read-only check.
// Run order and setup: docs/internal-uat-coolify-deploy.md, "Internal redeployment flow".
//
//   node scripts/rehearsal-check.mjs --profile seed|verify|reopen|live-readonly
//        --base-url https://<host> --result <file.json>
//        [--seed-file <seed result.json>] [--asset-out <app.js file>] [--no-ai]
//        [--ai-ok --ai-ledger <run ledger.json> [--ai-cap-usd 1]]
//        [--manifest deploy/rehearsal/live-cases.json [--targets-out <file.json> [--targets-only]]
//         [--download-files [--click-budget] | --expect-files <Pass A result.json>]]
//        [--db-guard <neon_branch_guard result.json>]
//        [--allow-loopback-http [--file-storage]]
//
// Database guard: a writing profile (seed, verify, reopen) on any non-loopback
// host, and every run on the copy of live (a host that says rehearsal-copy),
// needs --db-guard: the result of scripts/neon_branch_guard.py on the database
// URL that app was given, passed and at most 12 hours old. The guard refuses
// live's default (production) branch, so a mistyped database URL stops the run
// before it writes to live. Its host and branch name go into the result.
//
// Paid AI (seed's pricing-reference enrichment, verify's image analysis) needs
// --ai-ok and a ledger file shared by every check in the run; the run stops
// before a call that could pass the cap (at most US$1, scripts/lib/ai-budget.mjs).
// verify --no-ai needs neither.
// live-readonly with --manifest is the live-data walk (Pass A on the copy of
// live, Pass B on live): it reopens the manifest quotes and the newest quotes
// the login owns, checks their files, blocks, booth box and quantity boxes,
// and fails if anything tried to write. --targets-only stops after writing the
// target ids (for the bucket copy) and the manifest candidates.
// Downloads write a telemetry row on the server, so only Pass A (a rehearsal
// host) may pass --download-files: it fetches each available export and checks
// its bytes against the recorded hash. Pass B on live downloads nothing: it
// compares each quote's recorded export hashes with Pass A's (--expect-files),
// unless the quote changed after the copy was taken. --targets-out and
// --download-files are refused on any host that does not say rehearsal.
// --click-budget (Pass A only) runs the Quote Basis click probe
// (scripts/lib/basis-click-probe.mjs, the CI click-budget job's) on the largest
// manifest quotes, in a separate browser where every save is answered locally
// and counted, so the copy stays unchanged. Exact counts fail the walk;
// timings are recorded only. Never on live: a click there would save.
//
// Credentials come from the environment only, never arguments:
//   SQAG_REHEARSAL_LOGIN_EMAIL, SQAG_REHEARSAL_LOGIN_PASSWORD: the rehearsal app's
//     sign-in for seed, verify and reopen (the rehearsal host has no proxy auth,
//     so the app's sign-in is its access control), and for live-readonly on a
//     rehearsal host (Pass A on the copy of live, with the copy's run login)
//   SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL, SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD:
//     the operator's live sign-in for live-readonly on any other host, fed from the laptop credential
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
import { AI_CAP_MAX_USD, AI_PRICING_ENRICHMENT_USD, AiCapError, loadBudget, reportedUsd, saveBudget } from "./lib/ai-budget.mjs";
import { derivedReasons, ManifestError, targetsDocument, validateManifest, walkTargets } from "./lib/live-cases.mjs";
import { byteBudget, CLICK_COUNT_BUDGET, CLICK_WINDOW_MS, groupActions, installClickProbe, judgeClickCounts, lineButton, linesIn, measureClicks } from "./lib/basis-click-probe.mjs";
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
const VALUE_FLAGS = new Set(["--profile", "--base-url", "--result", "--seed-file", "--asset-out", "--manifest", "--targets-out", "--ai-ledger", "--ai-cap-usd", "--expect-files", "--db-guard"]);
const BOOLEAN_FLAGS = new Map([
  ["--no-ai", "noAi"], ["--allow-loopback-http", "allowLoopbackHttp"], ["--file-storage", "fileStorage"],
  ["--ai-ok", "aiOk"], ["--targets-only", "targetsOnly"], ["--download-files", "downloadFiles"], ["--click-budget", "clickBudget"],
]);
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
export const PRICING_SAVE_TIMEOUT_MS = 120000;

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
  const options = { noAi: false, allowLoopbackHttp: false, fileStorage: false, aiOk: false, targetsOnly: false };
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
  checkWalkFlags(options);
  checkAiFlags(options);
  options.baseUrl = checkBaseUrl(options.baseUrl, options.profile, options.allowLoopbackHttp, env);
  checkWalkHost(options);
  if (needsDbGuard(options) && !options.dbGuard) {
    throw new UsageError("this run writes to a rehearsal database: run scripts/neon_branch_guard.py on its database URL first and pass --db-guard <result.json>");
  }
  options.credentials = readCredentials(env, loginNames(options.profile, options.baseUrl));
  return options;
}

// Credentials follow the host: the live sign-in only ever goes to a host that
// does not say rehearsal, so Pass A's walk on the copy of live (a rehearsal
// host) signs in with the copy's run-only login, never live's password.
export function loginNames(profile, baseUrl) {
  if (profile !== "live-readonly") return REHEARSAL_LOGIN_ENV;
  return new URL(baseUrl).hostname.toLowerCase().includes("rehearsal") ? REHEARSAL_LOGIN_ENV : LIVE_LOGIN_ENV;
}

function checkWalkFlags(options) {
  const walk = options.profile === "live-readonly";
  if (!walk && (options.manifest || options.targetsOut || options.targetsOnly)) {
    throw new UsageError("--manifest, --targets-out and --targets-only are for live-readonly only");
  }
  if (!walk && (options.downloadFiles || options.expectFiles)) {
    throw new UsageError("--download-files and --expect-files are for live-readonly only");
  }
  if (options.targetsOut && !options.manifest) throw new UsageError("--targets-out needs --manifest");
  if (options.targetsOnly && !options.targetsOut) throw new UsageError("--targets-only needs --targets-out");
  if ((options.downloadFiles || options.expectFiles) && !options.manifest) throw new UsageError("--download-files and --expect-files need --manifest");
  if (options.downloadFiles && options.expectFiles) throw new UsageError("use --download-files (Pass A) or --expect-files (Pass B), not both");
  if (options.clickBudget && !(walk && options.downloadFiles)) {
    throw new UsageError("--click-budget is for Pass A only (live-readonly with --download-files on the copy of live)");
  }
}

// Writes on the server (telemetry rows for downloads, full quote reads for the
// candidate list) are only for the copy of live or a loopback test server.
function checkWalkHost(options) {
  if (!(options.downloadFiles || options.targetsOut)) return;
  const host = new URL(options.baseUrl).hostname.toLowerCase();
  if (!(LOOPBACK_HOSTS.has(host) || host.includes("rehearsal"))) {
    throw new UsageError("--download-files and --targets-out are for the copy of live only (a host that says rehearsal)");
  }
}

// Writing to a hosted database, or anything on the copy of live, needs the
// production-branch guard's pass first.
export function needsDbGuard(options) {
  const host = new URL(options.baseUrl).hostname.toLowerCase();
  if (LOOPBACK_HOSTS.has(host)) return false;
  return options.profile !== "live-readonly" || host.includes("rehearsal-copy");
}

export const DB_GUARD_MAX_AGE_MS = 12 * 60 * 60 * 1000;

export async function readDbGuard(file, now = Date.now()) {
  let guard;
  try {
    guard = JSON.parse(await fs.readFile(file, "utf8"));
  } catch {
    throw new UsageError("--db-guard is unreadable");
  }
  if (guard?.schema !== "sqag-db-guard/v1" || guard.status !== "pass") {
    throw new UsageError("--db-guard is not a passing production-branch guard result: the database may be live's, so nothing was sent");
  }
  const checked = Date.parse(String(guard.checked_at || ""));
  if (!Number.isFinite(checked) || checked > now + 60000 || now - checked > DB_GUARD_MAX_AGE_MS) {
    throw new UsageError("--db-guard is older than 12 hours or undated: run the guard again");
  }
  return { host: String(guard.host || ""), branch_name: guard.branch_name ?? null, neon_api: String(guard.neon_api || ""), checked_at: guard.checked_at };
}

// Paid AI runs only with the owner's OK and a run ledger, refused before any request.
export function aiNeeded(options) {
  return options.profile === "seed" || (options.profile === "verify" && !options.noAi);
}

function checkAiFlags(options) {
  const needed = aiNeeded(options);
  if (!needed) {
    if (options.aiOk || options.aiLedger || options.aiCapUsd) {
      throw new UsageError(`--ai-ok, --ai-ledger and --ai-cap-usd are only for paid AI runs (seed, verify without --no-ai)`);
    }
    return;
  }
  if (!options.aiOk) throw new UsageError(`profile ${options.profile} can make paid AI calls: add --ai-ok (the owner's OK) and --ai-ledger, or use --no-ai`);
  if (!options.aiLedger) throw new UsageError("--ai-ok needs --ai-ledger <file> shared by every check in the run");
  const cap = options.aiCapUsd === undefined ? AI_CAP_MAX_USD : Number(options.aiCapUsd);
  if (!/^[0-9]+(\.[0-9]{1,2})?$/.test(String(options.aiCapUsd ?? cap)) || !(cap > 0) || cap > AI_CAP_MAX_USD) {
    throw new UsageError(`--ai-cap-usd must be above 0 and at most ${AI_CAP_MAX_USD}`);
  }
  options.aiCapUsd = cap;
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

const NO_PAID_AI = Object.freeze({
  admit(label) { throw new AiCapError(`${label}: this check has no AI ledger, so it makes no paid AI call`); },
  record(label) { throw new AiCapError(`${label}: this check has no AI ledger, so it makes no paid AI call`); },
});

class Run {
  constructor(options) {
    this.options = options;
    this.baseUrl = options.baseUrl;
    this.result = {
      schema: RESULT_SCHEMA, profile: options.profile, result: "running",
      no_ai: options.noAi, started_at: new Date().toISOString(), steps: [],
      // The production-branch guard's verdict on this app's database (host and branch name only).
      db_guard: options.dbGuardResult ?? null,
    };
    this.trace = { jobs: [], saves: [], normalizations: [], pending: [], sequence: 0 };
    // Paid AI goes through the run's capped ledger; a check without one refuses any paid call.
    this.budget = options.budget ?? NO_PAID_AI;
    this.readOnly = null;
    if (options.profile === "live-readonly") {
      // Attached before the first step so a failed run still shows what was tried.
      this.readOnly = { writes: [], blocked: [] };
      this.result.write_requests = this.readOnly.writes;
      this.result.blocked_requests = this.readOnly.blocked;
      // Which app was walked (a hash, never the URL) and how, so the rollback
      // can refuse a result from the copy or from another run.
      this.result.walked_origin_sha256 = sha256(options.baseUrl);
      this.result.downloaded_files = options.downloadFiles === true;
      this.result.compared_with_pass_a = Boolean(options.expectFiles);
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
  // A new pricing reference runs AI enrichment on a server with an AI key.
  run.budget.admit("pricing reference enrichment", AI_PRICING_ENRICHMENT_USD);
  run.budget.record("pricing reference enrichment", AI_PRICING_ENRICHMENT_USD, "fixed amount, cost not reported per quote");
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

// Every browser context the run opens. In live-readonly each one gets the
// read-only guard before its first page exists.
async function newContext(run, options) {
  const context = await run.browser.newContext(options);
  if (run.readOnly) await context.route("**/*", browserGuard(run));
  return context;
}

// A fresh browser context restores the quote from the server, not from this
// browser's own saved state, as a user on another computer would see it.
async function freshPage(run) {
  const { cookies } = await run.context.storageState();
  const storageState = { cookies, origins: [] };
  const previous = run.context;
  run.context = await newContext(run, { acceptDownloads: true, storageState });
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

async function confirmAndGenerate(run, sessionId, fixture, { pdf: makePdf = true } = {}) {
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
  if (!makePdf) {
    // Excel-only, the app's default output: the PDF is never made.
    const readback = await quoteSession(run, sessionId);
    assert(readback.status?.quote_generated === true, "The generated quote is not marked current.");
    assert(readback.exports?.pdf?.exists !== true, "An Excel-only quote has a PDF.");
    return { readback, xlsx: await download(run, readback.exports?.xlsx, "xlsx"), pdf: null };
  }

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
  if (!artifacts.pdf) return { xlsx_sheets: xlsx.sheets };
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
  if (seed?.schema !== RESULT_SCHEMA || seed.profile !== "seed" || seed.result !== "pass" || !ids?.published_session_id || !ids?.draft_session_id || !ids?.excel_only_session_id || !ids?.pricing_reference_id || !("company_profile_id" in ids)) {
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
  await run.step("Excel-only quote reopens", async () => {
    const readback = await quoteSession(run, seed.excel_only_session_id);
    const files = exportFacts(readback);
    assert(files.xlsx.available && !files.pdf.available, "The Excel-only quote's files changed: it should have an XLSX and no PDF.");
    const xlsx = await download(run, readback.exports.xlsx, "xlsx");
    assert(xlsx.sha256 === seed.excel_only_xlsx_sha256, "The Excel-only quote's XLSX changed since the seed.");
    await reopenQuote(run, seed.excel_only_session_id);
    return { xlsx_sha256: xlsx.sha256 };
  });
  await run.step("draft quote reopens", async () => {
    const readback = await quoteSession(run, seed.draft_session_id);
    assert(readback.customer_summary?.customer_name === CUSTOMER, "The draft quote lost its customer details.");
    assert(readback.status?.quote_generated !== true, "The draft quote is marked generated.");
    await reopenQuote(run, seed.draft_session_id);
  });
}

async function newGeneratedQuote(run, images, fixture, label, { pdf = true } = {}) {
  const projectNumber = `REH-${Date.now().toString(36).toUpperCase()}`;
  const sessionId = await run.step(`${label}: create quote`, () => createQuoteDraft(run, images, projectNumber));
  await run.step(`${label}: deterministic basis`, () => seedBasis(run, sessionId, fixture));
  const files = pdf ? "XLSX and PDF" : "XLSX only";
  const artifacts = await run.step(`${label}: confirm and generate ${files}`, async () => {
    const value = await confirmAndGenerate(run, sessionId, fixture, { pdf });
    run.lastArtifacts = value;
    return { xlsx_sha256: value.xlsx.sha256, pdf_sha256: value.pdf?.sha256 ?? null };
  });
  await run.step(pdf ? `${label}: check XLSX, PDF and fonts` : `${label}: check XLSX`, () => checkArtifacts(run, run.lastArtifacts, projectNumber, fixture));
  return { sessionId, artifacts };
}

async function aiAnalysis(run, images) {
  const projectNumber = `REH-AI-${Date.now().toString(36).toUpperCase()}`;
  const sessionId = await createQuoteDraft(run, images, projectNumber);
  await reopenQuote(run, sessionId).catch(() => {});
  const jobsBefore = run.trace.jobs.length;
  run.budget.admit("AI image analysis");
  // The same app function the analysis Start button calls.
  await run.page.evaluate(() => confirmStartAnalysis("standard"));
  let terminal;
  try {
    terminal = await waitForJob(run, "draft", jobsBefore, AI_DRAFT_TIMEOUT_MS);
  } finally {
    // The quote's own spend record, whether or not the job finished.
    const spent = await quoteSession(run, sessionId).then((value) => reportedUsd(value.ai_spend?.total), () => null);
    run.budget.record("AI image analysis", spent, "quote ai_spend");
  }
  assert(terminal.status === "completed", `AI image analysis ended ${terminal.status}.`);
  const readback = await waitUntil(async () => {
    const value = await quoteSession(run, sessionId);
    return (value.draft_state?.quoteBasisSections || []).length > 0 ? value : null;
  }, "the AI basis to be saved", 30000);
  return { basis_sections: readback.draft_state.quoteBasisSections.length, ai_usd: run.budget.entries.at(-1).usd };
}

// The browser half of the read-only guard: only reads and the sign-in form go
// out; anything else is aborted before it is sent and recorded.
export function browserGuard(run) {
  const { writes, blocked } = run.readOnly;
  return async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (READ_METHODS.has(method) || (method === "POST" && url.origin === run.baseUrl && url.pathname === "/login")) {
      if (method === "POST") writes.push({ method, path: url.pathname });
      return route.continue();
    }
    blocked.push({ channel: "browser", method, path: blockedPath(url.toString(), run.baseUrl) });
    return route.abort();
  };
}

// A refused write is a failure: on the copy it means the app would write to
// live when the same walk runs there.
export function assertNoWriteAttempted(readOnly) {
  const refused = readOnly.blocked.length;
  assert(refused === 0, `The read-only walk tried ${refused} write${refused === 1 ? "" : "s"} (refused before sending).`);
  assert(readOnly.writes.every((entry) => entry.method === "POST" && entry.path === "/login"), "A write other than the sign-in form was sent.");
  return { refused_writes: 0, sign_in_posts: readOnly.writes.length };
}

async function quoteList(run) {
  const body = await getJson(run.api, `${run.baseUrl}/api/quote-sessions`, "Quote list");
  const list = body.quote_sessions ?? body.sessions ?? body.items;
  assert(Array.isArray(list), "The quote list response has no list.");
  return list;
}

// What a person checks by eye on a reopened quote, read from the page only:
// every block has one tick-all and one X, a Detected booth box has content or
// is absent, and every included line with no quantity has a quantity box.
export async function basisPageChecks(page) {
  return page.evaluate(() => {
    const surface = document.querySelector("#basisReviewSurface");
    const blocks = surface ? [...surface.querySelectorAll(".basis-review-item")] : [];
    const badBlocks = blocks.filter((item) => item.querySelectorAll('.basis-section-heading [data-basis-section-action="Include"]').length !== 1
      || item.querySelectorAll('.basis-section-heading [data-basis-section-action="Exclude"]').length !== 1).length;
    const booths = surface ? [...surface.querySelectorAll(".basis-visual-display")] : [];
    const emptyBooths = booths.filter((box) => !box.innerText.replace("Detected booth", "").trim()).length;
    let blankLines = 0;
    let missingBoxes = 0;
    if (blocks.length && typeof state === "object" && Array.isArray(state.quoteBasisSections)) {
      for (const section of state.quoteBasisSections) {
        (section.lines || []).forEach((line, index) => {
          const blank = line.quantity === undefined || line.quantity === null || String(line.quantity).trim() === "";
          if (!blank || normalizeBasisTag(line.tag) !== "Include") return;
          const row = surface.querySelector(`[data-basis-section="${CSS.escape(section.id)}"][data-basis-line-index="${index}"][data-basis-tag="Exclude"]`)?.closest("li");
          if (!row) return;
          blankLines += 1;
          if (!row.querySelector("[data-basis-quantity-edit]")) missingBoxes += 1;
        });
      }
    }
    return { blocks: blocks.length, bad_blocks: badBlocks, booth_boxes: booths.length, empty_booth_boxes: emptyBooths, blank_quantity_lines: blankLines, missing_quantity_boxes: missingBoxes };
  });
}

// An export is checked only when the quote has it now: Excel-only quotes (the
// default) have no PDF, and that is recorded, never a failure.
export function exportFacts(readback) {
  const facts = {};
  for (const kind of ["xlsx", "pdf"]) {
    const record = readback?.exports?.[kind];
    const available = record?.exists === true && record?.stale === false && /^[0-9a-f]{64}$/i.test(String(record?.sha256 || ""));
    facts[kind] = available
      ? { available: true, sha256: String(record.sha256).toLowerCase(), size_bytes: Number(record.size_bytes) }
      : { available: false, availability: String(record?.availability || "not_recorded") };
  }
  return facts;
}

// Pass B: the recorded hashes on live must equal what Pass A downloaded from the
// copy, unless the quote was saved again after the copy was taken.
export function compareWithPassA(id, readback, files, expected) {
  const before = expected.get(id);
  if (!before) return { pass_a: "not walked" };
  if (String(before.updated_at ?? "") !== String(readback?.updated_at ?? "")) return { pass_a: "changed since" };
  for (const kind of ["xlsx", "pdf"]) {
    const was = before.files?.[kind];
    const now = files[kind];
    if (!was?.available && !now.available) continue;
    assert(was?.available === now.available && was?.sha256 === now.sha256 && was?.size_bytes === now.size_bytes,
      `The ${kind} file differs from the one Pass A checked on the copy.`);
  }
  return { pass_a: "same files" };
}

async function walkQuote(run, sessionId) {
  const readback = await quoteSession(run, sessionId);
  const files = exportFacts(readback);
  const { sections, lines } = derivedReasons(readback);
  const detail = { generated: readback.status?.quote_generated === true, updated_at: readback.updated_at ?? null, sections, lines, files };
  if (run.options.downloadFiles) {
    // Pass A only: a GET of what is already stored, checked against its record.
    for (const kind of ["xlsx", "pdf"]) {
      if (files[kind].available) files[kind].downloaded = (await download(run, readback.exports[kind], kind)).sha256 === files[kind].sha256;
    }
  }
  if (run.options.expectedFiles) Object.assign(detail, compareWithPassA(sessionId, readback, files, run.options.expectedFiles));
  await freshPage(run);
  await openDashboard(run);
  await run.page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`).waitFor({ state: "attached", timeout: 20000 });
  const opened = await run.page.evaluate((id) => modifyDashboardQuote(id), sessionId);
  assert(opened === true, "The quote did not reopen.");
  await run.page.locator("#panel-analysis.is-active").waitFor({ state: "visible", timeout: 30000 });
  const page = await basisPageChecks(run.page);
  assert(page.bad_blocks === 0, `${page.bad_blocks} block(s) lack exactly one tick-all and one X.`);
  assert(page.empty_booth_boxes === 0, "A Detected booth box is empty.");
  assert(page.missing_quantity_boxes === 0, `${page.missing_quantity_boxes} line(s) with no quantity have no quantity box.`);
  return { ...detail, ...page };
}

async function liveWalk(run, manifest) {
  const { ids, newest } = manifest;
  let list = [];
  await run.step("quote list matches the dashboard", async () => {
    // A teammate may add or delete a quote between the two reads on live, so
    // a mismatch is read again once before it fails the walk.
    let mismatch = "";
    for (let attempt = 0; attempt < 2; attempt += 1) {
      list = await quoteList(run);
      await openDashboard(run);
      // The dashboard fetches its list after the page shows; a timeout falls through to the count check.
      await run.page.waitForFunction((count) => state.quoteSessions.length >= count, list.length, { timeout: 20000 }).catch(() => {});
      const shown = await run.page.evaluate(() => state.quoteSessions.map((session) => session.session_id));
      const listed = list.map((session) => session.session_id);
      if (shown.length === listed.length && listed.every((id) => shown.includes(id))) {
        return { quotes: listed.length, own: list.filter((session) => !session.dashboard_summary_only).length, attempts: attempt + 1 };
      }
      mismatch = `The dashboard holds ${shown.length} quotes, the API lists ${listed.length}.`;
    }
    throw new Error(mismatch);
  });
  const own = list.filter((session) => !session.dashboard_summary_only);
  await run.step("walk targets", () => {
    for (const id of ids) {
      const entry = list.find((session) => session.session_id === id);
      assert(entry, "A manifest quote is not listed.");
      assert(!entry.dashboard_summary_only, "A manifest quote is not owned by this login, so it cannot be reopened.");
    }
    const value = walkTargets(ids, own, newest);
    run.result.targets = value;
    return { manifest: ids.length, newest: value.length - ids.length };
  });
  if (run.options.targetsOut) {
    await run.step("targets file", async () => {
      const candidates = [];
      for (const session of own.slice(0, 30)) {
        const detail = await quoteSession(run, session.session_id);
        candidates.push({ id: session.session_id, ...derivedReasons(detail) });
      }
      await fs.writeFile(run.options.targetsOut, JSON.stringify(targetsDocument(run.result.targets, candidates), null, 2) + "\n", "utf8");
      return { candidates: candidates.length };
    });
  }
  if (run.options.targetsOnly) return;
  const walked = [];
  for (const [index, id] of run.result.targets.entries()) {
    const detail = await run.step(`quote ${index + 1} reopens read-only`, async () => ({ id, ...(await walkQuote(run, id)) }));
    if (ids.includes(id)) walked.push(detail);
  }
  if (run.options.clickBudget) {
    const largest = walked.filter((detail) => detail.lines > 0).sort((a, b) => b.lines - a.lines).slice(0, CLICK_BUDGET_QUOTES);
    if (!largest.length) await run.step("click budget", () => { throw new Error("No manifest quote has basis lines to click."); });
    for (const [index, detail] of largest.entries()) {
      await run.step(`click budget on quote ${index + 1}`, () => clickBudgetOnQuote(run, detail.id));
    }
  }
}

// Pass A's click probe runs on this many of the largest manifest quotes.
const CLICK_BUDGET_QUOTES = 2;

// The answer the server gives a quote save, built locally from the quote's
// readback and the save body, so the app carries on as after a real save.
// A new draft version each time; the ledger rows are the readback's.
export function savedAck(readback, body) {
  const { draft_state: _state, draft_files: _files, ...summary } = readback || {};
  const rows = Object.fromEntries(Object.entries(readback?.pricing_ledger?.rows || {}).filter(([, revision]) => Number.isSafeInteger(revision) && revision >= 0));
  const revision = Number.isSafeInteger(readback?.pricing_ledger?.revision) ? readback.pricing_ledger.revision : 0;
  const draftVersion = sha256(JSON.stringify([body?.expected_draft_version ?? "", body?.draft_state ?? null, Date.now()]));
  return { status: "saved", quote_session: { ...summary, session_id: body?.session_id, draft_version: draftVersion, pricing_ledger: { revision, rows } } };
}

// The probe browser's only route: reads go out; every write is answered here
// and counted, never sent (quote saves get savedAck, anything else 204).
export function saveStub(run, readback, stubbed) {
  return async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    if (READ_METHODS.has(method)) return route.continue();
    if (url.origin !== run.baseUrl) return route.abort();
    stubbed.push({ method, path: blockedPath(url.toString(), run.baseUrl) });
    if (method === "POST" && url.pathname === "/api/quote-sessions") {
      let body = null;
      try { body = request.postDataJSON(); } catch { body = null; }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(savedAck(readback, body)) });
    }
    return route.fulfill({ status: 204, body: "" });
  };
}

async function clickBudgetOnQuote(run, sessionId) {
  const readback = await quoteSession(run, sessionId);
  const { cookies } = await run.context.storageState();
  const context = await run.browser.newContext({ storageState: { cookies, origins: [] } });
  const stubbed = [];
  await context.route("**/*", saveStub(run, readback, stubbed));
  try {
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.name));
    const quotePosts = [];
    page.on("request", (request) => {
      if (request.method() !== "POST" || new URL(request.url()).pathname !== "/api/quote-sessions") return;
      let files = -1;
      try { const body = request.postDataJSON(); files = Array.isArray(body?.draft_files) ? body.draft_files.length : 0; } catch { files = -1; }
      quotePosts.push({ files });
    });
    await page.goto(`${run.baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.locator(`#dashboardSessionsList [data-quote-session-id="${sessionId}"]`).waitFor({ state: "attached", timeout: 30000 });
    assert(await page.evaluate((id) => modifyDashboardQuote(id), sessionId) === true, "The quote did not reopen for the click probe.");
    // A reopened quote may open on another panel: open Quote Basis as a user would, then let
    // any save the switch armed finish before the first measured click.
    await page.locator("#quoteBasisButton").click();
    await page.locator("#basisReviewSurface li[data-basis-key]").first().waitFor({ state: "visible", timeout: 30000 });
    await page.waitForTimeout(CLICK_WINDOW_MS);
    // The quote's own size sets the byte budgets (a click copies it once, a save about three times).
    const quoteBytes = await page.evaluate(() => JSON.stringify(currentQuoteSessionDraftState()).length);
    const budget = byteBudget(CLICK_COUNT_BUDGET, quoteBytes);
    await installClickProbe(page);
    const failures = [];
    const check = (ok, message, detail) => { if (!ok) failures.push(message + (detail === undefined ? "" : ": " + JSON.stringify(detail))); };
    const judge = (sample, posts) => judgeClickCounts(sample, posts, budget, check);
    const samples = [];
    const groups = {};
    for (const group of ["Matched", "Review", "Unmatched"]) {
      const lines = await linesIn(page, group);
      groups[group] = lines.length;
      if (!lines.length) continue;
      for (const [action, click] of groupActions(page, lines[0])) samples.push(await measureClicks(page, { group, action, clicks: [click], quotePosts, judge }));
    }
    assert(samples.length > 0, "The quote shows no basis line to click.");
    // Five fast clicks on three lines of the largest group arm one save, not five.
    const burstGroup = Object.entries(groups).sort((a, b) => b[1] - a[1]).find(([, count]) => count >= 3)?.[0];
    let burst = null;
    if (burstGroup) {
      const three = (await linesIn(page, burstGroup)).slice(0, 3);
      burst = await measureClicks(page, {
        group: burstGroup, action: "burst", expectedClicks: 5, touched: three, quotePosts, judge,
        clicks: [() => lineButton(page, three[0], true), () => lineButton(page, three[1], true), () => lineButton(page, three[2], true),
          () => lineButton(page, three[0], false), () => lineButton(page, three[1], false)],
      });
    }
    check(pageErrors.length === 0, "the page threw errors", pageErrors);
    const counts = (sample) => ({
      group: sample.group, action: sample.action, clicks: sample.clicks, quote_local_storage_writes: sample.quoteLocalStorageWrites,
      indexed_db_puts: sample.indexedDbPuts, basis_renders: sample.basisRenders, rebuilt_rows: sample.rebuiltRows, kept_rows: sample.keptRows,
      serialised_bytes: sample.serialisedBytes, encoded_bytes: sample.encodedBytes, quote_saves: sample.quoteSaves,
      // Timings vary with the laptop: recorded, never judged here.
      input_to_paint_ms: Math.round(sample.inputToPaintMs), longest_frame_ms: Math.max(0, ...sample.clickFrames, ...sample.laterFrames),
    });
    const detail = {
      id: sessionId, lines: derivedReasons(readback).lines, quote_bytes: quoteBytes, groups,
      samples: samples.map(counts), burst: burst && counts(burst),
      stubbed_saves: stubbed.filter((entry) => entry.method === "POST" && entry.path === "/api/quote-sessions").length,
      stubbed_other: stubbed.length - stubbed.filter((entry) => entry.method === "POST" && entry.path === "/api/quote-sessions").length,
    };
    assert(failures.length === 0, `Click budget: ${failures.join("; ")}`);
    return detail;
  } finally {
    await context.close().catch(() => {});
  }
}

async function liveReadonly(run) {
  await run.step("sign in", () => signInBrowser(run));
  await run.step("session", async () => {
    const session = await getJson(run.api, `${run.baseUrl}/api/session`, "Session");
    assert(session.authenticated === true, "The session is not authenticated.");
  });
  await run.step("quote list loads", async () => ({ quotes: (await quoteList(run)).length }));
  if (run.options.manifestIds) await liveWalk(run, run.options.manifestIds);
  await run.step("served app.js", () => capturedAppJs(run));
  await run.step("no write attempted", () => assertNoWriteAttempted(run.readOnly));
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
    run.context = await newContext(run, { acceptDownloads: true });
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
        const excelOnly = await newGeneratedQuote(run, images, fixture, "Excel-only quote", { pdf: false });
        const draftId = await run.step("draft quote", () => createQuoteDraft(run, images, `REH-DRAFT-${Date.now().toString(36).toUpperCase()}`));
        run.skip("damaged pricing reference", "No app route creates a damaged pack; the fail-closed message stays covered by the unit tests.");
        run.result.seed = {
          pricing_reference_id: pack.pricing_reference_id,
          company_profile_id: company.company_profile_id,
          published_session_id: published.sessionId,
          draft_session_id: draftId,
          published_exports: { xlsx_sha256: published.artifacts.xlsx_sha256, pdf_sha256: published.artifacts.pdf_sha256 },
          excel_only_session_id: excelOnly.sessionId,
          excel_only_xlsx_sha256: excelOnly.artifacts.xlsx_sha256,
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
    if (options.budget) run.result.ai_budget = options.budget.summary();
    run.result.finished_at = new Date().toISOString();
  }
  return run.result;
}

// Pass A's walk result: each reopened quote's id, saved time and the export
// hashes it downloaded and checked on the copy.
export async function readPassAFiles(file) {
  let result;
  try {
    result = JSON.parse(await fs.readFile(file, "utf8"));
  } catch {
    throw new UsageError("--expect-files is unreadable");
  }
  if (result?.schema !== RESULT_SCHEMA || result.profile !== "live-readonly" || result.result !== "pass" || result.downloaded_files !== true) {
    throw new UsageError("--expect-files must be a passing Pass A walk result (live-readonly with --download-files)");
  }
  const expected = new Map();
  for (const step of result.steps || []) {
    if (/^quote [0-9]+ reopens read-only$/.test(step?.name) && step.status === "pass" && step.detail?.id) {
      expected.set(step.detail.id, { updated_at: step.detail.updated_at ?? null, files: step.detail.files || {} });
    }
  }
  if (!expected.size) throw new UsageError("--expect-files holds no reopened quote");
  return expected;
}

// File inputs, read before any request so a bad manifest or ledger stops the run.
export async function loadRunInputs(options) {
  if (options.manifest) {
    let manifest;
    try {
      manifest = JSON.parse(await fs.readFile(options.manifest, "utf8"));
    } catch {
      throw new UsageError("--manifest is unreadable");
    }
    // Listing candidates for the owner to approve is the one use of an unapproved manifest.
    options.manifestIds = validateManifest(manifest, { requireApproval: !options.targetsOnly });
  }
  if (options.dbGuard) options.dbGuardResult = await readDbGuard(options.dbGuard);
  if (options.expectFiles) options.expectedFiles = await readPassAFiles(options.expectFiles);
  if (aiNeeded(options)) options.budget = await loadBudget(options.aiLedger, { ok: options.aiOk, capUsd: options.aiCapUsd });
  return options;
}

async function main(argv) {
  let options;
  try {
    options = parseArgs(argv);
    await loadRunInputs(options);
  } catch (error) {
    if (error instanceof UsageError || error instanceof ManifestError || error instanceof AiCapError) {
      console.error(`rehearsal-check: ${error.message}`);
      return 2;
    }
    throw error;
  }
  const playwright = await import("playwright");
  const result = await runChecks(options, playwright);
  if (options.budget) await saveBudget(options.aiLedger, options.budget);
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
