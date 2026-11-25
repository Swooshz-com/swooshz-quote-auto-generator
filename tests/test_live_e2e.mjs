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
import { assertNoWriteAttempted, basisPageChecks, browserGuard, compareWithPassA, DB_GUARD_MAX_AGE_MS, exportFacts, loadRunInputs, needsDbGuard, parseArgs, readDbGuard, quoteSaveWaitMs, readPassAFiles, savedAck, saveStub, showAllSessions, UsageError } from "../scripts/rehearsal-check.mjs";
// The ids-first listing (#390 run 2, hold 3) through namespaces, so the rest of
// this file still loads where those exports do not exist.
import * as liveCases from "../scripts/lib/live-cases.mjs";
import * as rehearsalCheck from "../scripts/rehearsal-check.mjs";
import * as clickProbe from "../scripts/lib/basis-click-probe.mjs";

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

// A passing set-service record (scripts/rehearsal_database_url.py): the copy service's
// database URL was written from a URL the production-branch guard passed.
async function guardFile(overrides = {}) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-db-guard-"));
  const file = path.join(dir, "guard.json");
  await fs.writeFile(file, JSON.stringify({
    schema: "sqag-db-url-set/v1", status: "pass", service_name: "quote-rehearsal-copy", service_uuid: "fakeserviceuuid000001", key: "SQAG_DATABASE_URL",
    host: "ep-quiet-copy-a1b2c3.example.test", endpoint_id: "ep-quiet-copy-a1b2c3", branch_id: "br-quiet-copy-a1b2c3",
    branch_name: "rehearsal-copy-abc1234", neon_api: "checked", checked_at: new Date().toISOString(), reason: null, ...overrides,
  }));
  return file;
}

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

test("copy ids: the manifest quotes, then every owned quote newest first, up to the bucket copy's cap", async () => {
  const { copyTargets, MAX_COPY_QUOTES } = liveCases;
  assert.equal(typeof copyTargets, "function", "live-cases.mjs has no copyTargets");
  // The copy script takes at most this many ids; the two caps must stay equal.
  const copyScript = await fs.readFile(path.join(root, "scripts", "copy_live_objects_to_rehearsal.py"), "utf8");
  assert.equal(MAX_COPY_QUOTES, Number(/^MAX_QUOTES = ([0-9]+)$/m.exec(copyScript)[1]));
  const own = [
    { session_id: id(2), updated_at: "2026-11-01" },
    { session_id: id(3), updated_at: "2026-11-19" },
    { session_id: id(1), updated_at: "2026-11-20" },
    { session_id: "quote-Planted Name", updated_at: "2026-11-21" },
  ];
  assert.deepEqual(copyTargets([id(2)], own), [id(2), id(1), id(3)]);
  assert.deepEqual(copyTargets([], own), [id(1), id(3), id(2)]);
  const many = Array.from({ length: 60 }, (_, index) => ({ session_id: `quote-${index.toString(16).padStart(24, "0")}`, updated_at: `2026-11-${String(index % 28 + 1).padStart(2, "0")}T${String(index).padStart(2, "0")}` }));
  const capped = copyTargets([many[0].session_id], many);
  assert.equal(capped.length, MAX_COPY_QUOTES);
  assert.equal(capped[0], many[0].session_id);
  assert.equal(new Set(capped).size, MAX_COPY_QUOTES);
  // Every walk target is among the copied quotes, so Pass A finds each one's files.
  assert.ok(walkTargets([many[0].session_id], many, 5).every((target) => capped.includes(target)));
  assert.throws(() => targetsDocument(many.map((session) => session.session_id), []), ManifestError);
});

// A run with only what writeWalkTargets uses: its options and a step recorder.
function listingRun(file) {
  const steps = [];
  return {
    steps,
    options: { targetsOut: file },
    async step(name, action) {
      const entry = { name, status: "running" };
      steps.push(entry);
      try {
        entry.detail = await action();
        entry.status = "pass";
        return entry.detail;
      } catch (error) {
        entry.status = "fail";
        entry.error = error.message;
        throw error;
      }
    },
  };
}

test("on a fresh copy of live the ids go to the targets file before any quote is opened, and stay there when the readback fails", async () => {
  const { writeWalkTargets } = rehearsalCheck;
  assert.equal(typeof writeWalkTargets, "function", "rehearsal-check.mjs has no writeWalkTargets");
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-walk-targets-"));
  const file = path.join(dir, "walk-targets.json");
  const own = [1, 2, 3, 4, 5].map((digit) => ({ session_id: id(digit), updated_at: `2026-11-0${digit}` }));
  const opened = [];
  // The copy's answer before its files are copied (run 2: object_artifact_storage_unavailable).
  const missingFiles = async (quoteId) => {
    opened.push(quoteId);
    const onDisk = JSON.parse(await fs.readFile(file, "utf8"));
    assert.deepEqual(onDisk, { schema: "sqag-live-walk-targets/v1", quote_ids: [id(2), id(5), id(4), id(3), id(1)], candidates: [] }, "the ids were not written before the first readback");
    throw new Error("Quote readback returned HTTP 503.");
  };
  const first = listingRun(file);
  await assert.rejects(writeWalkTargets(first, [id(2)], own, missingFiles), /HTTP 503\. .*copy_live_objects_to_rehearsal\.py.*--targets-only/);
  assert.deepEqual(opened, [id(2)]);
  assert.deepEqual(first.steps.map((entry) => [entry.name, entry.status]), [["copy ids file", "pass"], ["targets file", "fail"]]);
  assert.deepEqual(first.steps[0].detail, { quote_ids: 5, own_not_listed: 0 });
  assert.deepEqual(JSON.parse(await fs.readFile(file, "utf8")).quote_ids, [id(2), id(5), id(4), id(3), id(1)]);
  // After the copy, the same listing opens each quote and writes the candidates with their reasons.
  const copied = async (quoteId) => ({ session_id: quoteId, status: { quote_generated: quoteId === id(2) }, draft_state: { quoteBasisSections: [{ lines: [{}, {}] }] }, draft_files: [] });
  const second = listingRun(file);
  await writeWalkTargets(second, [id(2)], own, copied);
  assert.deepEqual(second.steps.map((entry) => [entry.name, entry.status]), [["copy ids file", "pass"], ["targets file", "pass"]]);
  assert.deepEqual(second.steps[1].detail, { candidates: 5 });
  const listed = JSON.parse(await fs.readFile(file, "utf8"));
  assert.deepEqual(listed.quote_ids, [id(2), id(5), id(4), id(3), id(1)]);
  assert.deepEqual(listed.candidates.map((entry) => [entry.id, entry.reasons, entry.sections, entry.lines]), [
    [id(2), ["generated"], 1, 2], [id(5), ["draft"], 1, 2], [id(4), ["draft"], 1, 2], [id(3), ["draft"], 1, 2], [id(1), ["draft"], 1, 2],
  ]);
  // The listing writes nothing on disk but the targets file.
  assert.deepEqual(await fs.readdir(dir), ["walk-targets.json"]);
  await fs.rm(dir, { recursive: true, force: true });
});

test("the candidate list opens at most 30 quotes and reports the owned quotes past the copy's cap", async () => {
  const { writeWalkTargets, MAX_COPY_QUOTES } = { ...rehearsalCheck, ...liveCases };
  assert.equal(typeof writeWalkTargets, "function", "rehearsal-check.mjs has no writeWalkTargets");
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-walk-targets-"));
  const file = path.join(dir, "walk-targets.json");
  const own = Array.from({ length: 55 }, (_, index) => ({ session_id: `quote-${index.toString(16).padStart(24, "0")}`, updated_at: `2026-11-20T${String(index).padStart(4, "0")}` }));
  const opened = [];
  const run = listingRun(file);
  await writeWalkTargets(run, [], own, async (quoteId) => { opened.push(quoteId); return { status: {} }; });
  assert.deepEqual(run.steps[0].detail, { quote_ids: MAX_COPY_QUOTES, own_not_listed: 55 - MAX_COPY_QUOTES });
  assert.equal(opened.length, 30);
  const listed = JSON.parse(await fs.readFile(file, "utf8"));
  assert.equal(listed.quote_ids.length, MAX_COPY_QUOTES);
  assert.deepEqual(opened, listed.quote_ids.slice(0, 30));
  assert.equal(listed.candidates.length, 30);
  await fs.rm(dir, { recursive: true, force: true });
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
  const rehearsal = ["--base-url", "https://quote-rehearsal.example.test", "--result", "r.json", "--db-guard", "g.json"];
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
  const walk = (host) => parseArgs(["--profile", "live-readonly", "--base-url", host, "--result", "r.json", "--db-guard", "g.json"], split).credentials.password;
  assert.equal(walk("https://quote-rehearsal-copy.example.test"), "synthetic-copy-run-password");
  assert.equal(walk("https://quote-internal.example.test"), "synthetic-live-password");
  const noLive = { ...split, SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_EMAIL: "", SWOOSHZ_SQAG_LIVE_READONLY_LOGIN_PASSWORD: "" };
  assert.equal(parseArgs(["--profile", "live-readonly", "--base-url", "https://quote-rehearsal-copy.example.test", "--result", "r.json", "--db-guard", "g.json"], noLive).credentials.password, "synthetic-copy-run-password");
});

test("the walk refuses an unapproved manifest unless it only lists candidates", async () => {
  const copy = ["--profile", "live-readonly", "--base-url", "https://quote-rehearsal-copy.example.test", "--result", "r.json", "--manifest", committedManifest, "--db-guard", await guardFile()];
  await assert.rejects(loadRunInputs(parseArgs(copy, env)), /not approved/);
  const listing = await loadRunInputs(parseArgs([...copy, "--targets-out", "t.json", "--targets-only"], env));
  assert.deepEqual(listing.manifestIds, { ids: [], newest: 3, approved: false });
});

test("live gets no download and no candidate list: those are for the copy of live only", () => {
  const on = (host, ...extra) => () => parseArgs(["--profile", "live-readonly", "--base-url", host, "--result", "r.json", "--manifest", committedManifest, "--db-guard", "g.json", ...extra], env);
  const live = "https://quote-internal.example.test";
  const copy = "https://quote-rehearsal-copy.example.test";
  assert.throws(on(live, "--download-files"), /copy of live only/);
  assert.throws(on(live, "--targets-out", "t.json"), /copy of live only/);
  // The ids listing runs only with --targets-out, so Pass B on live never opens quotes for it or writes a targets file.
  assert.throws(on(live, "--targets-out", "t.json", "--targets-only"), /copy of live only/);
  assert.throws(on(live, "--expect-files", "a.json", "--targets-only"), /needs --targets-out/);
  assert.equal(on(copy, "--download-files")().downloadFiles, true);
  assert.equal(on(live, "--expect-files", "a.json")().expectFiles, "a.json");
  assert.throws(on(copy, "--download-files", "--expect-files", "a.json"), /not both/);
  assert.throws(() => parseArgs(["--profile", "live-readonly", "--base-url", copy, "--result", "r.json", "--download-files", "--db-guard", "g.json"], env), /need --manifest/);
  assert.throws(() => parseArgs(["--profile", "reopen", "--seed-file", "s.json", "--base-url", copy, "--result", "r.json", "--download-files"], env), /live-readonly only/);
  // The click probe saves on every click, so it runs only in Pass A on the copy, never on live.
  assert.equal(on(copy, "--download-files", "--click-budget")().clickBudget, true);
  assert.throws(on(copy, "--click-budget"), /Pass A only/);
  assert.throws(on(live, "--expect-files", "a.json", "--click-budget"), /Pass A only/);
  assert.throws(on(live, "--download-files", "--click-budget"), /copy of live only/);
});

test("the click probe's saves are answered in the browser and never reach the copy", async () => {
  const readback = { session_id: id(1), updated_at: "t", draft_version: "a".repeat(64), draft_state: { secret: 1 }, draft_files: [{}],
    pricing_ledger: { revision: 4, rows: { "row-1": 2, "row-2": -1, "row-3": "x" } } };
  const body = { session_id: id(1), expected_draft_version: "a".repeat(64), draft_state: { quoteBasisSections: [] } };
  const ack = savedAck(readback, body);
  // The app accepts a save only with these (webapp/static/app.js saveCurrentQuoteSession).
  assert.equal(ack.status, "saved");
  assert.equal(ack.quote_session.session_id, id(1));
  assert.match(ack.quote_session.draft_version, /^[0-9a-f]{64}$/);
  assert.notEqual(ack.quote_session.draft_version, body.expected_draft_version);
  assert.deepEqual(ack.quote_session.pricing_ledger, { revision: 4, rows: { "row-1": 2 } });
  assert.ok(!("draft_state" in ack.quote_session) && !("draft_files" in ack.quote_session));

  const run = { baseUrl: "https://quote-rehearsal-copy.example.test" };
  const stubbed = [];
  const stub = saveStub(run, readback, stubbed);
  const route = (method, url, post = null) => {
    const calls = [];
    return {
      calls,
      request: () => ({ method: () => method, url: () => url, postDataJSON: () => post }),
      continue: async () => { calls.push(["continue"]); },
      abort: async () => { calls.push(["abort"]); },
      fulfill: async (response) => { calls.push(["fulfill", response.status, response.body ? JSON.parse(response.body).status : ""]); },
    };
  };
  const cases = [
    [["GET", run.baseUrl + "/api/quote-sessions"], ["continue"]],
    [["GET", "https://fonts.example.test/a.woff2"], ["continue"]],
    [["POST", run.baseUrl + "/api/quote-sessions", body], ["fulfill", 200, "saved"]],
    [["POST", run.baseUrl + "/api/client-events", {}], ["fulfill", 204, ""]],
    [["DELETE", run.baseUrl + "/api/quote-sessions/" + id(1)], ["fulfill", 204, ""]],
    [["POST", "https://other.example.test/api/quote-sessions", body], ["abort"]],
  ];
  for (const [args, expected] of cases) {
    const fake = route(...args);
    await stub(fake);
    assert.deepEqual(fake.calls, [expected], args.slice(0, 2).join(" "));
  }
  assert.deepEqual(stubbed.map((entry) => entry.method), ["POST", "POST", "DELETE"]);
  assert.ok(stubbed.every((entry) => !entry.path.includes("1111111111")), "ids in stubbed paths are masked");
});

test("writing to a hosted database, or anything on the copy of live, needs a fresh passing production-branch guard", async () => {
  const args = (profile, host, ...extra) => ["--profile", profile, "--base-url", host, "--result", "r.json", ...extra];
  const seed = (host, ...extra) => args("seed", host, "--ai-ok", "--ai-ledger", "l.json", ...extra);
  const rehearsal = "https://quote-rehearsal.example.test";
  const copy = "https://quote-rehearsal-copy.example.test";
  const live = "https://quote-internal.example.test";
  // Refused before any request: the database behind the app may be live's.
  for (const argv of [seed(rehearsal), seed(copy), args("reopen", rehearsal, "--seed-file", "s.json"), args("verify", copy, "--seed-file", "s.json", "--no-ai"), args("live-readonly", copy)]) {
    assert.throws(() => parseArgs(argv, env), /rehearsal_database_url\.py set-service/, argv.join(" "));
  }
  // Live itself and the loopback CI server take no guard: live is read-only and has no rehearsal database.
  assert.equal(needsDbGuard(parseArgs(args("live-readonly", live), env)), false);
  assert.equal(needsDbGuard(parseArgs(args("seed", "http://127.0.0.1:8000", "--allow-loopback-http", "--ai-ok", "--ai-ledger", "l.json"), env)), false);
  // A passing, recent set-service record for this service is read; its service, host and branch name are kept for the result.
  const options = await loadRunInputs(parseArgs(seed(copy, "--db-guard", await guardFile()), env));
  assert.deepEqual(Object.keys(options.dbGuardResult), ["service_name", "host", "branch_name", "neon_api", "checked_at"]);
  assert.equal(options.dbGuardResult.branch_name, "rehearsal-copy-abc1234");
  const now = Date.now();
  const refusals = [
    [{ status: "refused", reason: "target is a recorded production endpoint" }, /not a passing set-service record/],
    // A bare guard result proves nothing about what the service was given.
    [{ schema: "sqag-db-guard/v1" }, /not a passing set-service record/],
    [{ checked_at: new Date(now - DB_GUARD_MAX_AGE_MS - 60000).toISOString() }, /older than 12 hours/],
    [{ checked_at: null }, /older than 12 hours/],
    // The record of the other rehearsal service does not cover this app.
    [{ service_name: "quote-rehearsal" }, /another service/],
    [{ service_name: null }, /another service/],
    // A branch Neon's API did not confirm proves nothing about which database the URL names.
    [{ neon_api: "not_checked" }, /neon_api checked/],
    [{ neon_api: undefined }, /neon_api checked/],
    [{ neon_api: "CHECKED" }, /neon_api checked/],
  ];
  for (const [bad, message] of refusals) {
    await assert.rejects(readDbGuard(await guardFile(bad), copy, now), message, JSON.stringify(bad));
  }
  await assert.rejects(readDbGuard(path.join(os.tmpdir(), "no-such-guard.json"), copy), /unreadable/);
});

const hex = (digit) => String(digit).repeat(64);
const available = (digit, size) => ({ exists: true, stale: false, availability: "available", sha256: hex(digit), size_bytes: size });

test("an Excel-only quote (the default) is checked on its XLSX and never fails on the missing PDF", () => {
  // The readback shape of an XLSX-only generation: the PDF is not recorded.
  const excelOnly = { exports: { xlsx: available(1, 900), pdf: { filename: null, exists: false, missing: false, stale: false, availability: "not_recorded" } } };
  assert.deepEqual(exportFacts(excelOnly), {
    xlsx: { available: true, sha256: hex(1), size_bytes: 900 },
    pdf: { available: false, availability: "not_recorded" },
  });
  const stale = { exports: { xlsx: { ...available(1, 900), stale: true }, pdf: available(2, 50) } };
  assert.equal(exportFacts(stale).xlsx.available, false);
  assert.equal(exportFacts(stale).pdf.available, true);
  assert.deepEqual(exportFacts({}), { xlsx: { available: false, availability: "not_recorded" }, pdf: { available: false, availability: "not_recorded" } });
});

test("Pass B compares live's recorded hashes with Pass A's, unless the quote changed after the copy", () => {
  const files = { xlsx: { available: true, sha256: hex(1), size_bytes: 900 }, pdf: { available: false, availability: "not_recorded" } };
  const expected = new Map([[id(1), { updated_at: "2026-11-20T01:00:00Z", files }]]);
  assert.deepEqual(compareWithPassA(id(1), { updated_at: "2026-11-20T01:00:00Z" }, files, expected), { pass_a: "same files" });
  const changed = { ...files, xlsx: { ...files.xlsx, sha256: hex(9) } };
  assert.throws(() => compareWithPassA(id(1), { updated_at: "2026-11-20T01:00:00Z" }, changed, expected), /xlsx file differs/);
  const gainedPdf = { ...files, pdf: { available: true, sha256: hex(2), size_bytes: 40 } };
  assert.throws(() => compareWithPassA(id(1), { updated_at: "2026-11-20T01:00:00Z" }, gainedPdf, expected), /pdf file differs/);
  assert.deepEqual(compareWithPassA(id(1), { updated_at: "2026-11-20T02:00:00Z" }, changed, expected), { pass_a: "changed since" });
  assert.deepEqual(compareWithPassA(id(2), { updated_at: "x" }, files, expected), { pass_a: "not walked" });
});

test("Pass B takes only a passing Pass A walk that downloaded the files", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "sqag-pass-a-"));
  const write = async (body) => { const file = path.join(dir, "a.json"); await fs.writeFile(file, JSON.stringify(body)); return file; };
  const step = { name: "quote 1 reopens read-only", status: "pass", detail: { id: id(1), updated_at: "t", files: { xlsx: { available: true, sha256: hex(1), size_bytes: 9 } } } };
  const good = { schema: "sqag-rehearsal-check/v1", profile: "live-readonly", result: "pass", downloaded_files: true, steps: [step] };
  assert.deepEqual([...(await readPassAFiles(await write(good))).keys()], [id(1)]);
  for (const bad of [{ ...good, result: "fail" }, { ...good, downloaded_files: false }, { ...good, profile: "seed" }, { ...good, steps: [] }]) {
    await assert.rejects(readPassAFiles(await write(bad)), UsageError);
  }
  await assert.rejects(readPassAFiles(path.join(dir, "missing.json")), /unreadable/);
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

test("a new quote waits until the run's last quote save has left the app's save window", () => {
  assert.equal(quoteSaveWaitMs([], 1_000_000), 0);
  assert.equal(quoteSaveWaitMs([900_000], 1_000_000), 0);
  assert.equal(quoteSaveWaitMs([940_000, 990_000], 1_000_000), 50_000);
  assert.equal(quoteSaveWaitMs([1_000_000], 1_000_000), 60_000);
});

// A page whose page-size control sends the given requests when it changes.
function pagingPage(value, sends) {
  const listeners = new Set();
  return {
    selected: [],
    on(event, fn) { if (event === "request") listeners.add(fn); },
    off(event, fn) { if (event === "request") listeners.delete(fn); },
    waitForTimeout: async () => {},
    locator(selector) {
      assert.equal(selector, "#dashboardPageSizeSelect:enabled");
      const page = this;
      return {
        waitFor: async () => {},
        inputValue: async () => value,
        async selectOption(option) {
          page.selected.push(option);
          for (const method of sends) for (const fn of listeners) fn({ method: () => method });
        },
      };
    },
  };
}

test("showing all quotes on the dashboard fails the run if it sends anything but a read", async () => {
  const readOnly = pagingPage("5", ["GET"]);
  assert.equal(await showAllSessions(readOnly), true);
  assert.deepEqual(readOnly.selected, ["0"]);
  await assert.rejects(showAllSessions(pagingPage("5", ["GET", "POST"])), /1 request\(s\) that are not reads: POST/);
  const already = pagingPage("0", ["POST"]);
  assert.equal(await showAllSessions(already), false);
  assert.deepEqual(already.selected, []);
});

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

// The two largest real quotes' sizes and clicks on the copy of live (#390 hold 8), then the CI job's
// reopened quote with its real saves: character counts only.
const measuredQuotes = [
  { sizes: { quote_bytes: 145344, quote_as_text_bytes: 157700, browser_copy_bytes: 305149, restored_key_bytes: 157538, sections_bytes: 26872, basis_bytes: 7860, view_bytes: 668 }, clicks: [350222, 350686, 351609, 352076, 352073], idle: 27336, burst: [4, 1, 1431126] },
  { sizes: { quote_bytes: 209210, quote_as_text_bytes: 229500, browser_copy_bytes: 440527, restored_key_bytes: 229332, sections_bytes: 29071, basis_bytes: 8248, view_bytes: 668 }, clicks: [486763], idle: 29071, burst: [5, 0, 2435538] },
  { sizes: { quote_bytes: 224430, quote_as_text_bytes: 247640, browser_copy_bytes: 473873, restored_key_bytes: 247551, sections_bytes: 23372, basis_bytes: 3596, view_bytes: 668 }, clicks: [505105, 505158], idle: 23372, burst: [5, 0, 2526786], savingClicks: [2339189, 2342143, 2342829, 2343234] },
];
function judged(sample, saves, budget) {
  const failures = [];
  const changing = sample.changingClicks ?? 1;
  const clicks = sample.clicks ?? 1;
  clickProbe.judgeClickCounts({
    group: "Review", action: "click", clicks, expectedClicks: clicks, changingClicks: changing, idleClicks: clicks - changing, ignoredClicks: 0,
    clickQuoteLocalStorageWrites: changing, quoteLocalStorageWrites: changing + saves, writers: [], indexedDbPuts: 0, basisRenders: 0, matchesFullRender: true,
    rebuiltRows: 0, keptRows: 40, serialisedBytes: 0, encodedBytes: 0, lateSaves: 0, ...sample,
  }, Array.from({ length: saves }, () => ({ files: 0 })), budget, (ok, message) => { if (!ok) failures.push(message.replace("Review:click ", "")); });
  return failures;
}

test("the click budget follows the browser copy a click writes, and fails on a second copy of the quote", () => {
  const tooMuch = ["serialised too much data"];
  for (const { sizes, clicks, idle, burst, savingClicks = [] } of measuredQuotes) {
    const budget = clickProbe.quoteClickBudget(sizes);
    for (const serialisedBytes of clicks) {
      // The old budget followed the draft state, which is half of what one click writes on a reopened quote.
      assert.ok(serialisedBytes > Math.max(256 * 1024, Math.ceil(1.25 * sizes.quote_bytes)));
      assert.deepEqual(judged({ serialisedBytes }, 0, budget), [], "one copy of the quote");
      assert.deepEqual(judged({ serialisedBytes: serialisedBytes + sizes.browser_copy_bytes }, 0, budget), tooMuch, "a second browser copy");
      assert.deepEqual(judged({ serialisedBytes: serialisedBytes + sizes.quote_bytes }, 0, budget), tooMuch, "a second draft copy");
    }
    // A click with nothing to change writes nothing; it may not copy the quote either.
    assert.deepEqual(judged({ changingClicks: 0, serialisedBytes: idle }, 0, budget), []);
    assert.deepEqual(judged({ changingClicks: 0, serialisedBytes: idle + sizes.browser_copy_bytes }, 0, budget), tooMuch);
    // A burst: one copy for each click that changed something, however many clicks there were.
    const [changing, idleClicks, serialisedBytes] = burst;
    const fast = { clicks: changing + idleClicks, changingClicks: changing };
    assert.deepEqual(judged({ ...fast, serialisedBytes }, 0, budget), []);
    assert.deepEqual(judged({ ...fast, serialisedBytes: serialisedBytes + sizes.quote_bytes }, 0, budget), tooMuch);
    // Clicks with their real save: a seventh copy of the draft goes over, and more saves than a burst may make never raise the budget.
    for (const saving of savingClicks) {
      assert.deepEqual(judged({ serialisedBytes: saving }, 1, budget), [], "one save");
      assert.deepEqual(judged({ serialisedBytes: saving + sizes.quote_bytes }, 1, budget), tooMuch, "a seventh draft copy in a save");
      assert.deepEqual(judged({ serialisedBytes: 2 * saving - clicks[0] }, 2, budget), [...tooMuch, "saved the quote more than once"]);
    }
    // What the workspace view holds never adds to a budget.
    assert.deepEqual(clickProbe.quoteClickBudget({ ...sizes, view_bytes: 400000 }), budget);
  }
});

test("a click that changed nothing must have had nothing to change, and a change must be written", () => {
  const budget = clickProbe.quoteClickBudget(measuredQuotes[0].sizes);
  const idle = { changingClicks: 0, serialisedBytes: 1 };
  const tooMany = "wrote the quote to browser storage more than once per click that changed it";
  const outside = "wrote the quote to browser storage outside the click and its save";
  const unwritten = "changed the quote without writing it to browser storage";
  assert.deepEqual(judged(idle, 0, budget), []);
  assert.deepEqual(judged({ ...idle, idleClicks: 0, ignoredClicks: 1 }, 0, budget), ["changed nothing although a line it acts on was not in that state yet"]);
  assert.deepEqual(judged({ ...idle, clickQuoteLocalStorageWrites: 1, quoteLocalStorageWrites: 1 }, 0, budget), [tooMany, outside]);
  assert.deepEqual(judged({ clickQuoteLocalStorageWrites: 0, quoteLocalStorageWrites: 0 }, 0, budget), [unwritten]);
  assert.deepEqual(judged({ clickQuoteLocalStorageWrites: 2, quoteLocalStorageWrites: 2 }, 0, budget), [tooMany, outside]);
  // A slow click's own write may come after the click phase; a save's acknowledgement never stands in for it.
  assert.deepEqual(judged({ clickQuoteLocalStorageWrites: 0 }, 1, budget), []);
  assert.deepEqual(judged({ clickQuoteLocalStorageWrites: 0, quoteLocalStorageWrites: 1 }, 1, budget), [unwritten]);
});

test("a quote save sent after the click window fails, whenever it is answered", () => {
  const budget = clickProbe.quoteClickBudget(measuredQuotes[2].sizes);
  assert.deepEqual(judged({}, 1, budget), []);
  assert.deepEqual(judged({ lateSaves: 1 }, 1, budget), ["sent a quote save after the 1500 ms click window"]);
});

test("every size a byte budget adds is bounded by something the page cannot raise", () => {
  const over = (sizes, limit) => {
    const failures = [];
    clickProbe.judgeQuoteSizes(sizes, limit, (ok, message) => { if (!ok) failures.push(message); });
    return failures;
  };
  const inDraft = "the draft is larger than the quote it was opened from allows";
  const inCopy = "the browser copy holds the quote more than once beside its restore key";
  const inKey = "the browser copy's restore key is larger than one copy of the quote";
  const inView = "the workspace view holds more than ids and dashboard choices";
  // The small seeded CI quote too: no allowance is as large as its draft.
  const small = { quote_bytes: 18570, quote_as_text_bytes: 20450, browser_copy_bytes: 39834, restored_key_bytes: 20344, sections_bytes: 505, basis_bytes: 122, view_bytes: 668 };
  for (const sizes of [...measuredQuotes.map((quote) => quote.sizes), small]) {
    const limit = Math.floor(1.75 * sizes.quote_bytes);
    const more = (name, amount) => ({ ...sizes, [name]: sizes[name] + amount });
    assert.deepEqual(over(sizes, limit), []);
    assert.deepEqual(over(more("browser_copy_bytes", sizes.quote_bytes), limit), [inCopy], "a second draft in the copy");
    assert.deepEqual(over(more("browser_copy_bytes", Math.ceil(0.24 * sizes.quote_bytes) + 8192), limit), [inCopy], "a quarter of a draft in the copy");
    assert.deepEqual(over(more("browser_copy_bytes", sizes.restored_key_bytes), limit), [inCopy], "a second restore key");
    assert.deepEqual(over({ ...more("browser_copy_bytes", sizes.restored_key_bytes), restored_key_bytes: 2 * sizes.restored_key_bytes }, limit), [inKey], "a key that holds the quote twice");
    assert.deepEqual(over({ ...more("view_bytes", sizes.restored_key_bytes) }, limit), [inView], "the restore key in the view");
    assert.deepEqual(over({ ...sizes, quote_bytes: 2 * sizes.quote_bytes, quote_as_text_bytes: 2 * sizes.quote_as_text_bytes, browser_copy_bytes: sizes.browser_copy_bytes + sizes.quote_bytes }, limit), [inDraft], "a second draft in the draft");
  }
  // A missing or unreadable limit fails; it never means no limit.
  for (const limit of [undefined, Number.NaN, 0]) assert.deepEqual(over(small, limit), [inDraft]);
});

// The lock facts on a page with the app's own names (webapp/static/app.js) and its strict compare.
test("the Quote Basis lock facts name every checkpoint difference and why the current checkpoint is unavailable", async () => {
  const { chromium } = await import("playwright");
  const source = await fs.readFile(path.join(root, "webapp", "static", "app.js"), "utf8");
  const cut = (from) => source.slice(source.indexOf(from), source.indexOf("\n}\n", source.indexOf(from)) + 3);
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    const digest = `sha256:${"a".repeat(64)}`;
    const facts = async (saved, current, reference = { digest_sha256: digest }) => {
      await page.setContent(`<button id="quoteBasisButton" class="is-locked"></button><script>
        window.isPlainObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
        ${cut("function quoteCommercialStrictDataEqual(")}
        window.state = { quoteSessionId: "quote-1", customerStepCheckpoint: ${saved}, customerStepCheckpointSaveBusy: false };
        window.PRICING_REFERENCE_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
        window.customerStepCheckpointWithAddedFieldDefaults = (value) => value;
        window.customerStepCheckpointForCurrent = () => (${current});
        window.safeQuoteSessionId = (value) => value;
        window.currentBrowserRecoveryScope = () => "scope";
        window.currentPricingReference = () => (${JSON.stringify(reference)});
        window.customerDetailsBlockReason = () => "";
        window.quoteCommercialReviewRequired = () => false;
        window.pendingPricingReferenceSelectionChanged = () => false;
        window.sidePanelBlockReason = () => "Click Next in Customer to save this step before continuing.";
      </script>`);
      return rehearsalCheck.basisLockFacts(page);
    };
    const saved = '{ recoveryScope: "a", customer: { project: { title: "t" } } }';
    const copyOnly = await facts(saved, '{ recoveryScope: "b", customer: { project: { title: "t" } } }');
    assert.deepEqual([copyOnly.differs, copyOnly.copy_only], [["recoveryScope"], true]);
    // A field only the current checkpoint has locks the quote in the app even when its value is
    // undefined, at any level, so it is never the copy's own secret alone.
    for (const [current, field] of [
      ['{ recoveryScope: "b", customer: { project: { title: "t", added: undefined } } }', "customer.project.added"],
      ['{ recoveryScope: "b", added: undefined, customer: { project: { title: "t" } } }', "added"],
    ]) {
      const extra = await facts(saved, current);
      assert.deepEqual([extra.differs.includes(field), extra.copy_only], [true, false], `${field}: ${extra.differs}`);
    }
    // No current checkpoint because the reference's digest is malformed: the facts say so.
    const malformed = await facts(saved, "null", { digest_sha256: "sha256:bad" });
    assert.deepEqual([malformed.current_unavailable, malformed.copy_only], [["reference_digest"], false]);
    assert.deepEqual((await facts(saved, "null", null)).current_unavailable, ["pricing_reference"]);
    // Every fact's name survives scrub, so a failure message names it.
    for (const name of Object.keys(copyOnly)) assert.ok(rehearsalCheck.scrub(`facts {"${name}":true}`).includes(`"${name}"`), name);
  } finally {
    await browser.close();
  }
});
