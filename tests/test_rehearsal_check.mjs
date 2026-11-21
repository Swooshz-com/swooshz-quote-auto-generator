// Acceptance tests for scripts/rehearsal-check.mjs against a loopback synthetic server.
// Run: node --test tests/test_rehearsal_check.mjs (CI browser job).
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { checkBaseUrl, parseArgs, PRICING_SAVE_TIMEOUT_MS, readOnlyRequestContext, ReadOnlyViolation, scrub, UsageError } from "../scripts/rehearsal-check.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? "python" : "python3");
const script = path.join(root, "scripts", "rehearsal-check.mjs");
const syntheticEmail = "alpha-admin" + "@" + "example.test";
const syntheticPassword = "synthetic-browser-admin-password";
// The harness has one synthetic admin, so both sign-ins use it here.
const credentials = {
  SQAG_REHEARSAL_LOGIN_EMAIL: syntheticEmail,
  SQAG_REHEARSAL_LOGIN_PASSWORD: syntheticPassword,
  SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL: syntheticEmail,
  SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD: syntheticPassword,
};
const env = (extra = {}) => ({ ...process.env, ...credentials, ...extra });

function run(args, extraEnv = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, ...args], { cwd: root, env: env(extraEnv), windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", reject);
    child.once("close", (code) => resolve({ code, stdout, stderr }));
  });
}

async function countingServer() {
  const server = http.createServer((request, response) => {
    server.hits += 1;
    response.end("{}");
  });
  server.hits = 0;
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

test("base URL rules", () => {
  const live = { SQAG_REHEARSAL_LIVE_HOSTS: "quote-internal.example.test" };
  assert.throws(() => checkBaseUrl("http://quote-rehearsal.example.test", "seed", false, live), UsageError);
  assert.throws(() => checkBaseUrl("http://127.0.0.1:9", "seed", false, live), /https/);
  assert.throws(() => checkBaseUrl("https://127.0.0.1:9", "seed", false, live), /allow-loopback-http/);
  assert.throws(() => checkBaseUrl("https://quote-rehearsal.example.test", "seed", true, live), /loopback/);
  assert.throws(() => checkBaseUrl("https://u:p@quote-rehearsal.example.test", "seed", false, live), /credentials/);
  assert.throws(() => checkBaseUrl("https://quote-rehearsal.example.test/app", "seed", false, live), /origin/);
  assert.throws(() => checkBaseUrl("https://quote-internal.example.test", "verify", false, live), /live host/);
  assert.throws(() => checkBaseUrl("https://quote-other.example.test", "reopen", false, live), /rehearsal/);
  assert.equal(checkBaseUrl("https://quote-internal.example.test", "live-readonly", false, live), "https://quote-internal.example.test");
  assert.equal(checkBaseUrl("https://quote-rehearsal.example.test", "seed", false, live), "https://quote-rehearsal.example.test");
  assert.equal(checkBaseUrl("http://127.0.0.1:9", "seed", true, live), "http://127.0.0.1:9");
});

test("credentials are refused as arguments and never echoed", () => {
  const base = ["--profile", "live-readonly", "--base-url", "https://quote-internal.example.test", "--result", "r.json"];
  for (const extra of [
    ["--password", "x"], ["--email=someone"], ["--login-user", "x"],
    ["--result", credentials.SQAG_REHEARSAL_LOGIN_PASSWORD], [credentials.SQAG_REHEARSAL_LOGIN_PASSWORD],
  ]) {
    assert.throws(() => parseArgs([...base, ...extra], env()), (error) => {
      assert.ok(error instanceof UsageError);
      assert.ok(!error.message.includes(credentials.SQAG_REHEARSAL_LOGIN_PASSWORD));
      return true;
    }, extra.join(" "));
  }
  assert.throws(() => parseArgs([...base, "positional-value"], env()), /not shown/);
  assert.throws(() => parseArgs(base, env({ SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL: "" })), /SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL/);
  const seedArgs = ["--profile", "seed", "--base-url", "https://quote-rehearsal.example.test", "--result", "r.json", "--ai-ok", "--ai-ledger", "l.json", "--db-guard", "g.json"];
  assert.throws(() => parseArgs(seedArgs, env({ SQAG_REHEARSAL_LOGIN_EMAIL: "" })), /SQAG_REHEARSAL_LOGIN_EMAIL/);
  // Rehearsal logins never reach live: live-readonly reads only the live sign-in names.
  const rehearsalOnly = { ...process.env, SQAG_REHEARSAL_LOGIN_EMAIL: syntheticEmail, SQAG_REHEARSAL_LOGIN_PASSWORD: syntheticPassword };
  delete rehearsalOnly.SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL;
  delete rehearsalOnly.SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD;
  assert.throws(() => parseArgs(base, rehearsalOnly), /SWOOSHZ_SQAG_LIVE_READONLY_LOGIN/);
  assert.equal(parseArgs(seedArgs, rehearsalOnly).credentials.email, syntheticEmail);
  assert.throws(() => parseArgs(["--profile", "verify", "--base-url", "https://quote-rehearsal.example.test", "--result", "r.json"], env()), /seed-file/);
});

test("scrub removes URLs, emails and tokens", () => {
  const text = scrub("failed at https://host.example.test/x for someone" + "@" + "example.test token " + "a".repeat(40));
  assert.ok(!text.includes("host.example.test") && !text.includes("example.test") && !text.includes("a".repeat(40)));
});

test("an http base URL or an argv credential exits before any request", async () => {
  const { server, origin } = await countingServer();
  try {
    const plain = await run(["--profile", "seed", "--base-url", origin, "--result", path.join(os.tmpdir(), "unused.json")]);
    assert.equal(plain.code, 2);
    const leaked = await run(["--profile", "live-readonly", "--base-url", origin, "--allow-loopback-http", "--result", "r.json", "--password", credentials.SQAG_REHEARSAL_LOGIN_PASSWORD]);
    assert.equal(leaked.code, 2);
    assert.ok(!leaked.stderr.includes(credentials.SQAG_REHEARSAL_LOGIN_PASSWORD) && !leaked.stdout.includes(credentials.SQAG_REHEARSAL_LOGIN_PASSWORD));
    assert.equal(server.hits, 0);
  } finally {
    server.close();
  }
});

test("live-readonly API contexts refuse every write before it is sent", async () => {
  const { server, origin } = await countingServer();
  const { request } = await import("playwright");
  const blocked = [];
  const api = readOnlyRequestContext(await request.newContext(), blocked, origin);
  try {
    const attempts = [
      () => api.post(`${origin}/api/settings/pricing-references`, { data: {} }),
      () => api.put(`${origin}/api/x`),
      () => api.patch(`${origin}/api/x`),
      () => api.delete(`${origin}/api/x`),
      () => api.fetch(`${origin}/api/x`, { method: "POST" }),
      () => api.get(`${origin}/api/x`, { method: "DELETE" }),
    ];
    for (const attempt of attempts) await assert.rejects(attempt, ReadOnlyViolation);
    assert.equal(server.hits, 0);
    assert.deepEqual(blocked.map((entry) => entry.method), ["POST", "PUT", "PATCH", "DELETE", "POST", "DELETE"]);
    assert.ok(blocked.every((entry) => entry.channel === "api" && !entry.path.includes("127.0.0.1")));
    assert.equal((await api.get(`${origin}/api/health`)).status(), 200);
    assert.equal((await api.fetch(`${origin}/api/health`)).status(), 200);
    assert.equal(server.hits, 2);
  } finally {
    await api.dispose();
    server.close();
  }
});

// A logging reverse proxy in front of the harness: what the server actually received.
async function loggingProxy(upstream) {
  const target = new URL(upstream);
  const log = [];
  const proxy = http.createServer((request, response) => {
    log.push(`${request.method} ${request.url.split("?")[0]}`);
    const forward = http.request({ host: target.hostname, port: target.port, method: request.method, path: request.url, headers: request.headers }, (reply) => {
      response.writeHead(reply.statusCode, reply.headers);
      reply.pipe(response);
    });
    forward.on("error", () => { response.statusCode = 502; response.end(); });
    request.pipe(forward);
  });
  await new Promise((resolve) => proxy.listen(0, "127.0.0.1", resolve));
  return { proxy, log, origin: `http://127.0.0.1:${proxy.address().port}` };
}

// The test databases and app roles on the disposable PostgreSQL service, so a
// run can prove the harness removed what it created.
function postgresLeftovers() {
  const code = [
    "import os, psycopg",
    "url = 'postgresql://%s@%s:%s/postgres' % (os.environ['SQAG_TEST_POSTGRES_USER'], os.environ['SQAG_TEST_POSTGRES_HOST'], os.environ['SQAG_TEST_POSTGRES_PORT'])",
    "with psycopg.connect(url) as c:",
    "    dbs = [r[0] for r in c.execute(\"select datname from pg_catalog.pg_database where datname like 'sqag_ci_database_mode_%'\")]",
    "    roles = [r[0] for r in c.execute(\"select rolname from pg_catalog.pg_roles where rolname in ('sqag_runtime', 'sqag_migrator', 'sqag_maintenance')\")]",
    "print(' '.join(sorted(dbs)) + '|' + ' '.join(sorted(roles)))",
  ].join("\n");
  const probe = spawnSync(python, ["-c", code], { cwd: root, encoding: "utf8" });
  assert.equal(probe.status, 0, `PostgreSQL probe failed: ${probe.stderr}`);
  const [dbs, roles] = probe.stdout.trim().split("|");
  return { databases: dbs.split(" ").filter(Boolean), roles: roles.split(" ").filter(Boolean) };
}

// Row counts of every app table in the harness's database (the one test
// database the run created), so a walk can prove it wrote nothing, telemetry
// included.
function tableRowCounts() {
  const code = [
    "import os, json, psycopg",
    "base = 'postgresql://%s@%s:%s/' % (os.environ['SQAG_TEST_POSTGRES_USER'], os.environ['SQAG_TEST_POSTGRES_HOST'], os.environ['SQAG_TEST_POSTGRES_PORT'])",
    "with psycopg.connect(base + 'postgres') as c:",
    "    dbs = [r[0] for r in c.execute(\"select datname from pg_catalog.pg_database where datname like 'sqag_ci_database_mode_%'\")]",
    "assert len(dbs) == 1, dbs",
    "counts = {}",
    "with psycopg.connect(base + dbs[0]) as c:",
    "    tables = [r[0] for r in c.execute(\"select tablename from pg_catalog.pg_tables where schemaname = 'public' and tablename like 'sqag_%' order by tablename\")]",
    "    for t in tables:",
    "        counts[t] = c.execute('select count(*) from public.\"%s\"' % t).fetchone()[0]",
    "print(json.dumps(counts))",
  ].join("\n");
  const probe = spawnSync(python, ["-c", code], { cwd: root, encoding: "utf8" });
  assert.equal(probe.status, 0, `PostgreSQL row count failed: ${probe.stderr}`);
  return JSON.parse(probe.stdout.trim());
}

async function startHarness(dirs, storage) {
  // The harness takes the repository root first, then its data folders.
  const child = spawn(python, [path.join(root, "tests", "internal_password_browser_harness.py"), root, dirs.data, dirs.output, dirs.tmp, dirs.logs, dirs.pricing, "--storage", storage], {
    cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PYTHONUNBUFFERED: "1", SQAG_DISABLE_DOTENV: "1" },
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-4000); });
  const exited = new Promise((resolve) => child.once("exit", (code, signal) => resolve({ code, signal })));
  const origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("harness did not start: " + stderr)), 60000);
    let buffer = "";
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      const match = buffer.match(/http:\/\/127\.0\.0\.1:\d+/);
      if (match) { clearTimeout(timer); resolve(match[0]); }
    });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`harness exited ${code}: ${stderr}`)); });
  });
  // Stops the harness and waits, so a database-mode run has dropped its database.
  const stop = async () => {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    return exited;
  };
  return { child, origin, stop, stderr: () => stderr };
}

// Every profile in order, against one harness. `storage` is the harness's
// storage mode: file (the old CI shape) or database (live's modes).
async function runAllProfiles(t, storage) {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-rehearsal-test-"));
  const dirs = Object.fromEntries(["data", "output", "tmp", "logs", "pricing", "results"].map((name) => [name, path.join(base, name)]));
  await Promise.all(Object.values(dirs).map((dir) => fs.mkdir(dir, { recursive: true })));
  const before = storage === "database" ? postgresLeftovers() : null;
  const harness = await startHarness(dirs, storage);
  const front = await loggingProxy(harness.origin);
  const aiFlags = ["--ai-ok", "--ai-ledger", path.join(dirs.results, "ai-ledger.json")];
  const storageFlags = storage === "file" ? ["--file-storage"] : [];
  let stopped = false;
  try {
    const result = async (profile, extra = [], name = profile) => {
      const file = path.join(dirs.results, `${name}.json`);
      const outcome = await run(["--profile", profile, "--base-url", front.origin, "--allow-loopback-http", ...storageFlags, "--result", file, ...extra]);
      const text = await fs.readFile(file, "utf8");
      for (const value of Object.values(credentials)) assert.ok(!text.includes(value), `${profile} result holds a credential`);
      assert.ok(!text.includes("Rehearsal Synthetic"), `${name} result holds quote text`);
      assert.ok(!/https?:\/\//.test(text), `${profile} result holds a URL`);
      const parsed = JSON.parse(text);
      const failed = parsed.steps.filter((step) => step.status === "fail").map((step) => `${step.name}: ${step.error}`);
      assert.equal(outcome.code, 0, `${storage} ${profile} failed: ${failed.join(" | ") || parsed.error || outcome.stderr}`);
      assert.equal(parsed.result, "pass");
      const scan = spawnSync(python, [path.join(root, "scripts", "scan_sensitive_fixtures.py"), "--root", root, "--path", file, "--fail-on-review"], { cwd: root, encoding: "utf8" });
      assert.equal(scan.status, 0, `${profile} result has sensitive-fixture findings: ${scan.stdout}`);
      return { parsed, file };
    };
    const step = (parsed, name) => parsed.steps.find((entry) => entry.name === name);
    // live-readonly runs first, on an empty app, so any write it made would land.
    const live = await result("live-readonly");
    assert.deepEqual(live.parsed.write_requests, [{ method: "POST", path: "/login" }]);
    assert.deepEqual(front.log.filter((line) => !/^(GET|HEAD) /.test(line)), ["POST /login"], "live-readonly sent a write to the server");
    front.log.length = 0;
    const seed = await result("seed", aiFlags);
    assert.ok(front.log.some((line) => line === "POST /api/settings/pricing-references"), "seed did not save the pricing reference");
    // The new pricing reference's AI enrichment is counted against the run's US$1 cap.
    assert.deepEqual(seed.parsed.ai_budget, { cap_usd: 1, spent_usd: 0.1, within_cap: true, entries: [{ label: "pricing reference enrichment", usd: 0.1, source: "fixed amount, cost not reported per quote" }] });
    assert.ok(seed.parsed.seed.published_session_id && seed.parsed.seed.draft_session_id && seed.parsed.seed.pricing_reference_id);
    // The app's quote-save rate limit is real on the rehearsal too: the seed paces its quotes and is never refused.
    assert.deepEqual(seed.parsed.refused_requests.filter((line) => line.endsWith(" 429")), [], "the seed met the app's rate limit");
    assert.ok(Number.isInteger(seed.parsed.quote_saves) && seed.parsed.quote_saves > 0, JSON.stringify(seed.parsed.quote_saves));
    if (storage === "database") {
      // Database mode refuses generation without a saved company profile.
      assert.ok(front.log.some((line) => line === "POST /api/settings/profiles"), "seed did not save the company profile");
      assert.match(String(seed.parsed.seed.company_profile_id), /^[A-Za-z0-9_-]+$/);
      assert.equal(step(seed.parsed, "company profile").status, "pass");
      // A rerun on the same database reuses the profile and pricing reference (the d07c910 rehearsal needed two seeds).
      const again = await result("seed", aiFlags, "seed-again");
      assert.deepEqual(step(again.parsed, "company profile").detail, { company_profile_id: seed.parsed.seed.company_profile_id, reused: true });
      assert.equal(step(again.parsed, "pricing reference").detail.reused, true);
      assert.equal(again.parsed.ai_budget.spent_usd, 0.1, "a reused pricing reference must not count again");
      assert.deepEqual(again.parsed.refused_requests.filter((line) => line.endsWith(" 429")), [], "the second seed met the app's rate limit");
    } else {
      assert.equal(seed.parsed.seed.company_profile_id, null);
      assert.equal(step(seed.parsed, "company profile").status, "skipped");
    }
    assert.ok(seed.parsed.seed.excel_only_session_id && /^[0-9a-f]{64}$/.test(seed.parsed.seed.excel_only_xlsx_sha256), "seed made no Excel-only quote");
    // The live-data walk (Pass A and Pass B) on the seeded quotes, read-only on every channel.
    const manifestFile = path.join(dirs.results, "live-cases.json");
    const committed = JSON.parse(await fs.readFile(path.join(root, "deploy", "rehearsal", "live-cases.json"), "utf8"));
    await fs.writeFile(manifestFile, JSON.stringify({
      ...committed,
      approval: { approved: true, approved_at: "2026-11-20", record: "0000000" },
      cases: [
        { id: seed.parsed.seed.published_session_id, reasons: ["generated"] },
        { id: seed.parsed.seed.excel_only_session_id, reasons: ["generated"] },
        { id: seed.parsed.seed.draft_session_id, reasons: ["draft"] },
      ],
    }));
    // Pass A (the copy of live): downloads each available file and checks it against its record.
    front.log.length = 0;
    const targetsFile = path.join(dirs.results, "targets.json");
    const countsBeforeA = storage === "database" ? tableRowCounts() : null;
    const passA = await result("live-readonly", ["--manifest", manifestFile, "--targets-out", targetsFile, "--download-files", "--click-budget"], "pass-a");
    assert.deepEqual(front.log.filter((line) => !/^(GET|HEAD) /.test(line)), ["POST /login"], "Pass A sent a write to the server");
    assert.equal(step(passA.parsed, "no write attempted").status, "pass");
    assert.equal(passA.parsed.downloaded_files, true);
    if (storage === "database") {
      // Two seeds leave more quotes than one dashboard page, so the walk must find quotes past the first page.
      const pageSize = Number(/const DASHBOARD_DEFAULT_PAGE_SIZE = ([0-9]+);/.exec(await fs.readFile(path.join(root, "webapp", "static", "app.js"), "utf8"))[1]);
      assert.ok(step(passA.parsed, "quote list matches the dashboard").detail.quotes > pageSize, "database mode seeded no more quotes than one dashboard page");
      assert.ok(passA.parsed.dashboard_all_sessions > 0, "Pass A never showed all quotes on the dashboard");
    }
    const published = step(passA.parsed, "quote 1 reopens read-only").detail;
    assert.equal(published.id, seed.parsed.seed.published_session_id);
    assert.equal(published.generated, true);
    assert.deepEqual([published.files.xlsx.sha256, published.files.xlsx.downloaded], [seed.parsed.seed.published_exports.xlsx_sha256, true]);
    assert.deepEqual([published.files.pdf.sha256, published.files.pdf.downloaded], [seed.parsed.seed.published_exports.pdf_sha256, true]);
    assert.ok(published.bad_blocks === 0 && published.empty_booth_boxes === 0 && published.missing_quantity_boxes === 0, JSON.stringify(published));
    // The Excel-only quote (the app's default output) passes on its XLSX; its missing PDF is recorded, not failed.
    const excelOnly = step(passA.parsed, "quote 2 reopens read-only").detail;
    assert.equal(excelOnly.id, seed.parsed.seed.excel_only_session_id);
    assert.deepEqual([excelOnly.generated, excelOnly.files.xlsx.sha256, excelOnly.files.pdf.available], [true, seed.parsed.seed.excel_only_xlsx_sha256, false]);
    assert.equal(step(passA.parsed, "quote 3 reopens read-only").detail.generated, false);
    // The click probe ran on the largest manifest quotes; its saves were answered in the browser, never sent.
    const clickSteps = passA.parsed.steps.filter((entry) => /^click budget on quote [0-9]+$/.test(entry.name));
    assert.ok(clickSteps.length >= 1 && clickSteps.every((entry) => entry.status === "pass"), JSON.stringify(clickSteps));
    const probed = clickSteps[0].detail;
    assert.ok([seed.parsed.seed.published_session_id, seed.parsed.seed.excel_only_session_id].includes(probed.id));
    assert.ok(probed.samples.length >= 4 && probed.samples.every((sample) => sample.clicks === 1 && sample.indexed_db_puts === 0 && sample.rebuilt_rows === 0), JSON.stringify(probed.samples));
    // Whatever the clicks saved was answered in the browser: the proxy check above saw only the sign-in.
    assert.ok(Number.isInteger(probed.stubbed_saves) && Number.isInteger(probed.stubbed_other), JSON.stringify(probed));
    const targets = JSON.parse(await fs.readFile(targetsFile, "utf8"));
    assert.deepEqual(targets.quote_ids.slice(0, 3), [seed.parsed.seed.published_session_id, seed.parsed.seed.excel_only_session_id, seed.parsed.seed.draft_session_id]);
    assert.ok(targets.quote_ids.every((id) => /^quote-[0-9a-f]{24}$/.test(id)));
    assert.ok(!JSON.stringify(targets).includes("Rehearsal Synthetic"), "the targets file holds quote text");
    if (countsBeforeA) {
      // Downloads write telemetry rows, which is why only Pass A on the copy downloads.
      assert.ok(tableRowCounts().sqag_telemetry_events > countsBeforeA.sqag_telemetry_events, "the row count did not see Pass A's download telemetry");
    }
    // Pass B (live): no download, no candidate list; live's recorded hashes must equal Pass A's.
    front.log.length = 0;
    const countsBeforeB = storage === "database" ? tableRowCounts() : null;
    const passB = await result("live-readonly", ["--manifest", manifestFile, "--expect-files", passA.file], "pass-b");
    assert.deepEqual(front.log.filter((line) => !/^(GET|HEAD) /.test(line)), ["POST /login"], "Pass B sent a write to the server");
    assert.deepEqual(front.log.filter((line) => line.includes("/download/")), [], "Pass B downloaded a file on live");
    assert.equal(passB.parsed.downloaded_files, false);
    assert.equal(passB.parsed.compared_with_pass_a, true);
    // Pass B also showed all quotes on the dashboard, and the proxy check above saw no write from it.
    assert.ok(passB.parsed.dashboard_all_sessions > 0, "Pass B never showed all quotes on the dashboard");
    for (const n of [1, 2]) assert.equal(step(passB.parsed, `quote ${n} reopens read-only`).detail.pass_a, "same files");
    if (countsBeforeB) assert.deepEqual(tableRowCounts(), countsBeforeB, "Pass B wrote rows to the database (telemetry included)");
    const asset = path.join(dirs.results, "app.js");
    const verify = await result("verify", ["--seed-file", seed.file, "--no-ai", "--asset-out", asset]);
    const served = step(verify.parsed, "served app.js").detail;
    assert.deepEqual(verify.parsed.refused_requests.filter((line) => line.endsWith(" 429")), [], "verify met the app's rate limit");
    const source = await fs.readFile(path.join(root, "webapp", "static", "app.js"));
    assert.deepEqual(await fs.readFile(asset), source);
    assert.equal(served.app_js_bytes, source.length);
    assert.ok(verify.parsed.steps.some((entry) => entry.name === "AI image analysis" && entry.status === "skipped"));
    assert.equal(step(verify.parsed, "company profile present").status, storage === "database" ? "pass" : "skipped");
    const fonts = step(verify.parsed, "new quote: check XLSX, PDF and fonts").detail.pdf_fonts;
    assert.ok(fonts.length > 0);
    await result("reopen", ["--seed-file", seed.file]);
    const exit = await harness.stop();
    stopped = true;
    assert.equal(exit.code, storage === "database" ? 0 : null, `harness did not stop cleanly: ${harness.stderr()}`);
    if (before) assert.deepEqual(postgresLeftovers(), before, "the harness left a test database or app role behind");
  } finally {
    front.proxy.close();
    if (!stopped) await harness.stop();
    await fs.rm(base, { recursive: true, force: true });
  }
}

test("seed, verify, reopen and live-readonly pass against a local file-storage server", { timeout: 600000 }, (t) => runAllProfiles(t, "file"));

// Live runs database storage, so CI runs the same profiles in that mode on its
// disposable PostgreSQL service. Without one (a laptop), the test is skipped,
// except under CI, where a missing service is a failure.
const postgresConfigured = ["SQAG_TEST_POSTGRES_HOST", "SQAG_TEST_POSTGRES_PORT", "SQAG_TEST_POSTGRES_USER"].every((name) => process.env[name]);
test("seed, verify, reopen and live-readonly pass against a local database-storage server", {
  timeout: 600000,
  skip: !postgresConfigured && process.env.CI !== "true" ? "no disposable PostgreSQL service (SQAG_TEST_POSTGRES_*)" : false,
}, (t) => {
  assert.ok(postgresConfigured, "CI must provide the disposable PostgreSQL service for the database-mode run");
  return runAllProfiles(t, "database");
});

test("the pricing-reference save waits up to 120 s for AI enrichment", () => {
  // The d07c910 rehearsal's save took about 36 s; the old 30 s limit failed it.
  assert.equal(PRICING_SAVE_TIMEOUT_MS, 120000);
});

test("--file-storage is refused outside a loopback test server", () => {
  const args = ["--profile", "seed", "--base-url", "https://quote-rehearsal.example.test", "--result", "r.json", "--file-storage"];
  assert.throws(() => parseArgs(args, env()), /loopback/);
});
