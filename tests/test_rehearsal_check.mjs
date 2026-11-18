// Acceptance tests for scripts/rehearsal-check.mjs against a loopback synthetic server.
// Run: node --test tests/test_rehearsal_check.mjs (CI browser job).
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { checkBaseUrl, parseArgs, scrub, UsageError } from "../scripts/rehearsal-check.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = process.env.PYTHON || process.env.PYTHON_EXECUTABLE || (process.platform === "win32" ? "python" : "python3");
const script = path.join(root, "scripts", "rehearsal-check.mjs");
const credentials = {
  SQAG_REHEARSAL_LOGIN_EMAIL: "alpha-admin" + "@" + "example.test",
  SQAG_REHEARSAL_LOGIN_PASSWORD: "synthetic-browser-admin-password",
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
  assert.throws(() => parseArgs(base, { ...process.env, SQAG_REHEARSAL_LOGIN_EMAIL: "" }), /SQAG_REHEARSAL_LOGIN_EMAIL/);
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

async function startHarness(dirs) {
  const child = spawn(python, [path.join(root, "tests", "internal_password_browser_harness.py"), dirs.data, dirs.output, dirs.tmp, dirs.logs, dirs.pricing], {
    cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PYTHONUNBUFFERED: "1", SQAG_DISABLE_DOTENV: "1" },
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-4000); });
  const origin = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("harness did not start: " + stderr)), 30000);
    let buffer = "";
    child.stdout.on("data", (chunk) => {
      buffer += chunk;
      const match = buffer.match(/http:\/\/127\.0\.0\.1:\d+/);
      if (match) { clearTimeout(timer); resolve(match[0]); }
    });
    child.once("exit", (code) => { clearTimeout(timer); reject(new Error(`harness exited ${code}: ${stderr}`)); });
  });
  return { child, origin };
}

test("seed, verify, reopen and live-readonly pass against a local synthetic server", { timeout: 600000 }, async () => {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-rehearsal-test-"));
  const dirs = Object.fromEntries(["data", "output", "tmp", "pricing", "results"].map((name) => [name, path.join(base, name)]));
  dirs.logs = path.join(root, "_logs", "server", "rehearsal-check");
  await Promise.all(Object.values(dirs).map((dir) => fs.mkdir(dir, { recursive: true })));
  const harness = await startHarness(dirs);
  try {
    const result = async (profile, extra = []) => {
      const file = path.join(dirs.results, `${profile}.json`);
      const outcome = await run(["--profile", profile, "--base-url", harness.origin, "--allow-loopback-http", "--result", file, ...extra]);
      const text = await fs.readFile(file, "utf8");
      for (const value of Object.values(credentials)) assert.ok(!text.includes(value), `${profile} result holds a credential`);
      assert.ok(!/https?:\/\//.test(text), `${profile} result holds a URL`);
      const parsed = JSON.parse(text);
      const failed = parsed.steps.filter((step) => step.status === "fail").map((step) => `${step.name}: ${step.error}`);
      assert.equal(outcome.code, 0, `${profile} failed: ${failed.join(" | ") || parsed.error || outcome.stderr}`);
      assert.equal(parsed.result, "pass");
      return { parsed, file };
    };
    const seed = await result("seed");
    assert.ok(seed.parsed.seed.published_session_id && seed.parsed.seed.draft_session_id && seed.parsed.seed.pricing_reference_id);
    const asset = path.join(dirs.results, "app.js");
    const verify = await result("verify", ["--seed-file", seed.file, "--no-ai", "--asset-out", asset]);
    const served = verify.parsed.steps.find((step) => step.name === "served app.js").detail;
    const source = await fs.readFile(path.join(root, "webapp", "static", "app.js"));
    assert.deepEqual(await fs.readFile(asset), source);
    assert.equal(served.app_js_bytes, source.length);
    assert.ok(verify.parsed.steps.some((step) => step.name === "AI image analysis" && step.status === "skipped"));
    const fonts = verify.parsed.steps.find((step) => step.name === "new quote: check XLSX, PDF and fonts").detail.pdf_fonts;
    assert.ok(fonts.length > 0);
    await result("reopen", ["--seed-file", seed.file]);
    const live = await result("live-readonly");
    assert.deepEqual(live.parsed.write_requests, [{ method: "POST", path: "/login" }]);
  } finally {
    harness.child.kill();
    await fs.rm(base, { recursive: true, force: true });
  }
});
