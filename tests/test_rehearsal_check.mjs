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
import { checkBaseUrl, parseArgs, readOnlyRequestContext, ReadOnlyViolation, scrub, UsageError } from "../scripts/rehearsal-check.mjs";

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
  const seedArgs = ["--profile", "seed", "--base-url", "https://quote-rehearsal.example.test", "--result", "r.json"];
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
  const harness = await startHarness(dirs, storage);
  const front = await loggingProxy(harness.origin);
  const storageFlags = storage === "file" ? ["--file-storage"] : [];
  let stopped = false;
  try {
    const result = async (profile, extra = []) => {
      const file = path.join(dirs.results, `${profile}.json`);
      const outcome = await run(["--profile", profile, "--base-url", front.origin, "--allow-loopback-http", ...storageFlags, "--result", file, ...extra]);
      const text = await fs.readFile(file, "utf8");
      for (const value of Object.values(credentials)) assert.ok(!text.includes(value), `${profile} result holds a credential`);
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
    const seed = await result("seed");
    assert.ok(front.log.some((line) => line === "POST /api/settings/pricing-references"), "seed did not save the pricing reference");
    assert.ok(seed.parsed.seed.published_session_id && seed.parsed.seed.draft_session_id && seed.parsed.seed.pricing_reference_id);
    if (storage === "database") {
      // Database mode refuses generation without a saved company profile.
      assert.ok(front.log.some((line) => line === "POST /api/settings/profiles"), "seed did not save the company profile");
      assert.match(String(seed.parsed.seed.company_profile_id), /^[A-Za-z0-9_-]+$/);
      assert.equal(step(seed.parsed, "company profile").status, "pass");
    } else {
      assert.equal(seed.parsed.seed.company_profile_id, null);
      assert.equal(step(seed.parsed, "company profile").status, "skipped");
    }
    const asset = path.join(dirs.results, "app.js");
    const verify = await result("verify", ["--seed-file", seed.file, "--no-ai", "--asset-out", asset]);
    const served = step(verify.parsed, "served app.js").detail;
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

test("--file-storage is refused outside a loopback test server", () => {
  const args = ["--profile", "seed", "--base-url", "https://quote-rehearsal.example.test", "--result", "r.json", "--file-storage"];
  assert.throws(() => parseArgs(args, env()), /loopback/);
});
