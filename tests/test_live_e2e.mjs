// Contract tests for the live-data end-to-end checks (#390 slice 2): the quote
// manifest, the paid-AI cap and the read-only guard of the walk.
// Run: node --test tests/test_live_e2e.mjs (CI: npm run playwright:rehearsal-check).
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { AI_CAP_MAX_USD, AiBudget, AiCapError, loadBudget, reportedUsd, saveBudget } from "../scripts/lib/ai-budget.mjs";
import { derivedReasons, ManifestError, REASON_CODES, targetsDocument, validateManifest, walkTargets } from "../scripts/lib/live-cases.mjs";
import { assertNoWriteAttempted, basisPageChecks, browserGuard, loadRunInputs, parseArgs, UsageError } from "../scripts/rehearsal-check.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const script = path.join(root, "scripts", "rehearsal-check.mjs");
const committedManifest = path.join(root, "deploy", "rehearsal", "live-cases.json");
const credentials = {
  SQAG_REHEARSAL_LOGIN_EMAIL: "alpha-admin" + "@" + "example.test",
  SQAG_REHEARSAL_LOGIN_PASSWORD: "synthetic-browser-admin-password",
  SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL: "alpha-admin" + "@" + "example.test",
  SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD: "synthetic-browser-admin-password",
};
const env = { ...process.env, ...credentials };
const id = (digit) => `quote-${String(digit).repeat(24)}`;

async function manifest(overrides = {}) {
  const base = JSON.parse(await fs.readFile(committedManifest, "utf8"));
  return { ...base, approval: { approved: true, approved_at: "2026-11-20", record: "issuecomment-1234567890" }, cases: [{ id: id(1), reasons: ["generated"] }], ...overrides };
}

test("the committed manifest is well formed, unapproved and holds no quote yet", async () => {
  const committed = JSON.parse(await fs.readFile(committedManifest, "utf8"));
  assert.deepEqual(validateManifest(committed, { requireApproval: false }), { ids: [], newest: 3, approved: false });
  assert.throws(() => validateManifest(committed), /not approved/);
});

test("the manifest refuses anything but opaque ids, fixed reasons and an approval record", async () => {
  const base = await manifest();
  assert.equal(validateManifest(base).ids[0], id(1));
  const refused = [
    { cases: [{ id: "quote-Synthetic Customer Name", reasons: ["generated"] }] },
    { cases: [{ id: id(1), reasons: ["generated"], note: "customer name" }] },
    { cases: [{ id: id(1), reasons: ["big stand for a customer"] }] },
    { cases: [{ id: id(1), reasons: [] }] },
    { cases: [{ id: id(1), reasons: ["generated", "generated"] }] },
    { cases: [{ id: id(1), reasons: ["generated"] }, { id: id(1), reasons: ["draft"] }] },
    { cases: Array.from({ length: 21 }, (_, index) => ({ id: `quote-${index.toString(16).padStart(24, "0")}`, reasons: ["draft"] })) },
    { approval: { approved: true, approved_at: "2026-11-20", record: "Owner said yes in chat" } },
    { approval: { approved: true, approved_at: "yesterday", record: "46b5c59" } },
    { approval: { approved: false, approved_at: null, record: "46b5c59" } },
    { approval: { approved: true, approved_at: "2026-11-20", record: "46b5c59", by: "owner name" } },
    { newest_own_quotes: 6 },
    { newest_own_quotes: 0, cases: [] },
    { reason_codes: { ...base.reason_codes, "customer-x": "free text" } },
    { extra: "free text" },
    { schema: "sqag-live-cases/v2" },
  ];
  for (const overrides of refused) {
    assert.throws(() => validateManifest({ ...base, ...overrides }), ManifestError, JSON.stringify(overrides).slice(0, 80));
  }
});

test("walk targets: manifest quotes first, then the newest owned quotes, ids only", () => {
  const own = [
    { session_id: id(2), updated_at: "2026-11-01" },
    { session_id: id(3), updated_at: "2026-11-19" },
    { session_id: id(1), updated_at: "2026-11-20" },
    { session_id: id(4), updated_at: "2026-11-18" },
  ];
  assert.deepEqual(walkTargets([id(1)], own, 2), [id(1), id(3), id(4)]);
  assert.deepEqual(walkTargets([], own, 0), []);
  const doc = targetsDocument([id(1)], [{ id: id(1), reasons: ["generated", "made-up"], sections: 2, lines: 5 }]);
  assert.deepEqual(doc, { schema: "sqag-live-walk-targets/v1", quote_ids: [id(1)], candidates: [{ id: id(1), reasons: ["generated"], sections: 2, lines: 5 }] });
  assert.throws(() => targetsDocument(["quote-name"], []), ManifestError);
});

test("candidate reasons are derived from counts and types, never text", () => {
  const detail = {
    status: { quote_generated: true }, created_at: "2026-01-01T00:00:00Z",
    draft_state: { quoteBasisSections: Array.from({ length: 8 }, () => ({ lines: Array.from({ length: 5 }, () => ({ text: "Planted Customer Line" })) })) },
    draft_files: [{ content_type: "application/pdf", name: "Planted Customer.pdf" }, { content_type: "image/png" }],
  };
  const derived = derivedReasons(detail, Date.parse("2026-11-20T00:00:00Z"));
  assert.deepEqual(derived, { reasons: ["generated", "many-sections", "large", "pdf-brief", "image-brief", "older"], sections: 8, lines: 40 });
  assert.ok(derived.reasons.every((code) => REASON_CODES.includes(code)));
  assert.ok(!JSON.stringify(derived).includes("Planted"));
});

test("the AI budget needs the owner's OK and never passes US$1", () => {
  assert.equal(AI_CAP_MAX_USD, 1);
  assert.throws(() => new AiBudget({ ok: false, capUsd: 1 }), AiCapError);
  assert.throws(() => new AiBudget({ ok: true, capUsd: 1.5 }), AiCapError);
  assert.throws(() => new AiBudget({ ok: true, capUsd: 0 }), AiCapError);
  const budget = new AiBudget({ ok: true, capUsd: 1 });
  budget.admit("first");
  budget.record("first", 0.06, "quote ai_spend");
  budget.record("pricing", 0.1, "fixed");
  assert.equal(budget.spentUsd, 0.16);
  budget.record("big", 0.7, "quote ai_spend");
  // 0.86 spent: a 0.25 reserve would pass the cap, so the next call is refused before it is sent.
  assert.throws(() => budget.admit("next"), /cap/);
  assert.throws(() => budget.record("over", 0.2, "quote ai_spend"), /over the US\$1 cap/);
  assert.equal(budget.summary().within_cap, false);
});

test("an unreported AI cost stops every later paid call", () => {
  const budget = new AiBudget({ ok: true, capUsd: 1 });
  assert.throws(() => budget.record("analysis", null, "quote ai_spend"), /did not report/);
  assert.throws(() => budget.admit("again"), /not reported/);
  for (const [total, usd] of [
    [{ status: "none", display: null }, 0], [{ status: "reported", display: "USD 0.05" }, 0.05],
    [{ status: "reported", display: "< USD 0.01" }, 0.01], [{ status: "reported", display: "USD 1,234.50" }, 1234.5],
    [{ status: "partial", display: "USD 0.05" }, null], [{ status: "unavailable", display: null }, null],
    [{ status: "reported", display: "free" }, null], [undefined, null],
  ]) assert.equal(reportedUsd(total), usd, JSON.stringify(total));
});

test("one ledger caps the whole run across seed and verify", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-ai-ledger-"));
  const ledger = path.join(dir, "ledger.json");
  try {
    const seed = await loadBudget(ledger, { ok: true, capUsd: 1 });
    seed.record("pricing reference enrichment", 0.1, "fixed");
    seed.record("earlier analysis", 0.7, "quote ai_spend");
    await saveBudget(ledger, seed);
    const verify = await loadBudget(ledger, { ok: true, capUsd: 1 });
    assert.equal(verify.spentUsd, 0.8);
    assert.throws(() => verify.admit("AI image analysis"), AiCapError);
    await assert.rejects(loadBudget(ledger, { ok: true, capUsd: 0.5 }), /differs/);
    await fs.writeFile(ledger, "not json");
    await assert.rejects(loadBudget(ledger, { ok: true, capUsd: 1 }), /not a ledger/);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});

test("paid-AI profiles refuse to start without the owner's OK flag", () => {
  const rehearsal = ["--base-url", "https://quote-rehearsal.example.test", "--result", "r.json"];
  const refused = [
    [["--profile", "seed"], /--ai-ok/],
    [["--profile", "verify", "--seed-file", "s.json"], /--ai-ok/],
    [["--profile", "seed", "--ai-ok"], /--ai-ledger/],
    [["--profile", "seed", "--ai-ok", "--ai-ledger", "l.json", "--ai-cap-usd", "2"], /at most 1/],
    [["--profile", "seed", "--ai-ok", "--ai-ledger", "l.json", "--ai-cap-usd", "0.5x"], /at most 1/],
    [["--profile", "reopen", "--seed-file", "s.json", "--ai-ok"], /only for paid AI/],
    [["--profile", "verify", "--seed-file", "s.json", "--no-ai", "--ai-ledger", "l.json"], /only for paid AI/],
    [["--profile", "seed", "--ai-ok", "--ai-ledger", "l.json", "--manifest", "m.json"], /live-readonly only/],
  ];
  for (const [args, message] of refused) assert.throws(() => parseArgs([...args, ...rehearsal], env), message, args.join(" "));
  assert.equal(parseArgs(["--profile", "verify", "--seed-file", "s.json", "--no-ai", ...rehearsal], env).noAi, true);
  assert.equal(parseArgs(["--profile", "seed", "--ai-ok", "--ai-ledger", "l.json", ...rehearsal], env).aiCapUsd, 1);
  assert.equal(parseArgs(["--profile", "seed", "--ai-ok", "--ai-ledger", "l.json", "--ai-cap-usd", "0.5", ...rehearsal], env).aiCapUsd, 0.5);
  const live = ["--profile", "live-readonly", "--base-url", "https://quote-internal.example.test", "--result", "r.json"];
  assert.throws(() => parseArgs([...live, "--targets-out", "t.json"], env), /needs --manifest/);
  assert.throws(() => parseArgs([...live, "--manifest", "m.json", "--targets-only"], env), /needs --targets-out/);
  assert.throws(() => parseArgs([...live, "--ai-ok"], env), UsageError);
});

test("live's sign-in goes only to a host that is not a rehearsal; the copy of live gets the run's login", () => {
  const split = {
    ...process.env,
    SQAG_REHEARSAL_LOGIN_EMAIL: "copy-operator" + "@" + "example.test",
    SQAG_REHEARSAL_LOGIN_PASSWORD: "synthetic-copy-run-password",
    SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL: "live-operator" + "@" + "example.test",
    SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD: "synthetic-live-password",
  };
  const walk = (host) => parseArgs(["--profile", "live-readonly", "--base-url", host, "--result", "r.json"], split).credentials.password;
  assert.equal(walk("https://quote-rehearsal-copy.example.test"), "synthetic-copy-run-password");
  assert.equal(walk("https://quote-internal.example.test"), "synthetic-live-password");
  const noLive = { ...split, SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL: "", SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD: "" };
  assert.equal(parseArgs(["--profile", "live-readonly", "--base-url", "https://quote-rehearsal-copy.example.test", "--result", "r.json"], noLive).credentials.password, "synthetic-copy-run-password");
});

test("the walk refuses an unapproved manifest unless it only lists candidates", async () => {
  const live = ["--profile", "live-readonly", "--base-url", "https://quote-internal.example.test", "--result", "r.json", "--manifest", committedManifest];
  await assert.rejects(loadRunInputs(parseArgs(live, env)), /not approved/);
  const listing = await loadRunInputs(parseArgs([...live, "--targets-out", "t.json", "--targets-only"], env));
  assert.deepEqual(listing.manifestIds, { ids: [], newest: 3, approved: false });
});

function fakeRoute(method, url) {
  const calls = [];
  return {
    calls,
    request: () => ({ method: () => method, url: () => url }),
    continue: async () => { calls.push("continue"); },
    abort: async () => { calls.push("abort"); },
  };
}

test("the browser guard sends only reads and the sign-in form, and any refused write fails the walk", async () => {
  const run = { baseUrl: "https://quote-internal.example.test", readOnly: { writes: [], blocked: [] } };
  const guard = browserGuard(run);
  const cases = [
    ["GET", "/api/quote-sessions", "continue"], ["HEAD", "/static/app.js", "continue"], ["POST", "/login", "continue"],
    ["POST", "/api/quote-sessions", "abort"], ["PUT", "/api/x", "abort"], ["PATCH", "/api/x", "abort"],
    ["DELETE", "/api/quote-sessions/quote-aaaaaaaaaaaaaaaaaaaaaaaa", "abort"], ["POST", "/api/jobs", "abort"],
  ];
  for (const [method, pathname, expected] of cases) {
    const route = fakeRoute(method, run.baseUrl + pathname);
    await guard(route);
    assert.deepEqual(route.calls, [expected], `${method} ${pathname}`);
  }
  const otherOrigin = fakeRoute("POST", "https://other.example.test/login");
  await guard(otherOrigin);
  assert.deepEqual(otherOrigin.calls, ["abort"]);
  assert.equal(run.readOnly.blocked.length, 6);
  assert.ok(run.readOnly.blocked.every((entry) => !entry.path.includes("aaaaaaaaaaaaaaaa")), "ids in refused paths are masked");
  assert.throws(() => assertNoWriteAttempted(run.readOnly), /tried 6 writes/);
  assert.deepEqual(assertNoWriteAttempted({ writes: [{ method: "POST", path: "/login" }], blocked: [] }), { refused_writes: 0, sign_in_posts: 1 });
  assert.throws(() => assertNoWriteAttempted({ writes: [{ method: "POST", path: "/api/x" }], blocked: [] }), /other than the sign-in/);
});

function runScript(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, ...args], { cwd: root, env, windowsHide: true });
    let stderr = "";
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.once("error", reject);
    child.once("close", (code) => resolve({ code, stderr }));
  });
}

test("a refused AI OK or manifest exits before any request", async () => {
  const server = http.createServer((request, response) => { server.hits += 1; response.end("{}"); });
  server.hits = 0;
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    const seed = await runScript(["--profile", "seed", "--base-url", origin, "--allow-loopback-http", "--result", path.join(os.tmpdir(), "unused.json")]);
    assert.equal(seed.code, 2);
    assert.match(seed.stderr, /--ai-ok/);
    const walk = await runScript(["--profile", "live-readonly", "--base-url", origin, "--allow-loopback-http", "--result", path.join(os.tmpdir(), "unused.json"), "--manifest", committedManifest]);
    assert.equal(walk.code, 2);
    assert.match(walk.stderr, /not approved/);
    assert.equal(server.hits, 0);
  } finally {
    server.close();
  }
});

// The page checks on markup shaped like the app's basis review surface
// (webapp/static/app.js; the same selectors scripts/playwright-basis-review-regressions.mjs uses).
test("the page checks catch a block without its own tick and X, an empty booth box and a missing quantity box", async () => {
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const block = (section, actions, lines) => `<div class="basis-review-item"><div class="basis-section-heading"><h4>${section}</h4>${actions}</div><ul>${lines}</ul></div>`;
    const both = '<button data-basis-section-action="Include"></button><button data-basis-section-action="Exclude"></button>';
    const line = (section, index, box) => `<li><button data-basis-section="${section}" data-basis-line-index="${index}" data-basis-tag="Exclude"></button>${box ? '<button data-basis-quantity-edit>1 sqm</button>' : ""}</li>`;
    const render = async (surface, sections) => page.setContent(`<div id="basisReviewSurface">${surface}</div><script>
      window.state = { quoteBasisSections: ${JSON.stringify(sections)} };
      window.normalizeBasisTag = (tag) => (tag === "Exclude" ? "Exclude" : tag === "Confirm" ? "Confirm" : "Include");
    </script>`);
    const sections = [{ id: "s1", lines: [{ tag: "Include", quantity: "" }, { tag: "Include", quantity: 2 }, { tag: "Exclude", quantity: "" }] }, { id: "s2", lines: [{ tag: "Include" }] }];
    await render(block("s1", both, line("s1", 0, true) + line("s1", 1, false) + line("s1", 2, false)) + block("s2", both, line("s2", 0, true))
      + '<div class="basis-visual-display">Detected booth<br>Booth size 6m x 6m (36 sqm)</div>', sections);
    assert.deepEqual(await basisPageChecks(page), { blocks: 2, bad_blocks: 0, booth_boxes: 1, empty_booth_boxes: 0, blank_quantity_lines: 2, missing_quantity_boxes: 0 });
    await render(block("s1", '<button data-basis-section-action="Include"></button>', line("s1", 0, false) + line("s1", 1, false) + line("s1", 2, false))
      + block("s2", both + both, line("s2", 0, true)) + '<div class="basis-visual-display">Detected booth</div>', sections);
    assert.deepEqual(await basisPageChecks(page), { blocks: 2, bad_blocks: 2, booth_boxes: 1, empty_booth_boxes: 1, blank_quantity_lines: 2, missing_quantity_boxes: 1 });
    await page.setContent("<main></main>");
    assert.deepEqual(await basisPageChecks(page), { blocks: 0, bad_blocks: 0, booth_boxes: 0, empty_booth_boxes: 0, blank_quantity_lines: 0, missing_quantity_boxes: 0 });
  } finally {
    await browser.close();
  }
});
