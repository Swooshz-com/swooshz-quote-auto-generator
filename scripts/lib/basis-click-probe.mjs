// Quote Basis click probe: counts the work each tick or X causes in the page.
// Shared by the CI click-budget job (scripts/playwright-basis-click-budget.mjs, on a synthetic
// real-sized quote) and Pass A of the live-data test (scripts/rehearsal-check.mjs --click-budget,
// on the copy of live). The page functions it reads (state, renderQuoteBasisMessage,
// QUOTE_SESSION_STORAGE_KEY) are the app's own globals in webapp/static/app.js.

// A deferred quote save is armed 650 ms after a click; 1.5 s covers it. Work before the
// 400 ms mark belongs to the click itself.
export const CLICK_WINDOW_MS = 1500;
export const CLICK_PHASE_MS = 400;

// The exact counts. A click writes one browser copy of the quote and renders the Quote Basis
// once; it never rewrites reference files to IndexedDB, rebuilds unchanged rows or saves more
// than once per burst. Byte budgets are for the synthetic quote (about 210 KB per click copy,
// 1.3 MB per save); a larger quote's own size is added by the caller (see byteBudget).
export const CLICK_COUNT_BUDGET = Object.freeze({
  clickQuoteLocalStorageWrites: 1, indexedDbPuts: 0, basisRenders: 1, rebuiltRows: 0, quoteSavesPerBurst: 1,
  serialisedBytesPerClick: 256 * 1024, serialisedBytesPerQuoteSave: 1.5 * 1024 * 1024, encodedBytesPerClick: 16 * 1024,
});

// A real quote can be larger than the synthetic one: one click may copy the quote once and a
// save may copy, compare and post it three times, so the byte budgets grow with its size.
export function byteBudget(budget, quoteBytes) {
  return {
    ...budget,
    serialisedBytesPerClick: Math.max(budget.serialisedBytesPerClick, Math.ceil(1.25 * quoteBytes)),
    serialisedBytesPerQuoteSave: Math.max(budget.serialisedBytesPerQuoteSave, Math.ceil(3.5 * quoteBytes)),
  };
}

// Probes in the page. Each counts only while armed, so the caller's own page reads never count.
export function installClickProbe(page) {
  return page.evaluate(() => {
    const probe = window.__clickProbe = { armed: false, events: [], writers: [] };
    const log = (kind, amount = 1) => { if (probe.armed) probe.events.push({ kind, at: performance.now(), amount }); };
    const setItem = Storage.prototype.setItem;
    // Name the code that wrote, so a failure says where an extra write came from.
    const caller = () => (new Error().stack || "").split("\n").slice(3, 7).map((frame) => (frame.match(/at (\S+)/) || [])[1] || "?").join("<");
    Storage.prototype.setItem = function (key, value) {
      if (key === QUOTE_SESSION_STORAGE_KEY && probe.armed) { log("quoteLocalStorageWrite"); probe.writers.push(caller()); }
      return setItem.call(this, key, value);
    };
    const put = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.put = function (...values) { log("indexedDbPut"); return put.apply(this, values); };
    const stringify = JSON.stringify;
    JSON.stringify = function (...values) { const text = stringify.apply(this, values); if (typeof text === "string") log("serialisedBytes", text.length); return text; };
    const encode = TextEncoder.prototype.encode;
    TextEncoder.prototype.encode = function (...values) { const bytes = encode.apply(this, values); log("encodedBytes", bytes.byteLength); return bytes; };
    // A render may re-enter itself once to build its pricing index; count only the outer call.
    const render = window.renderQuoteBasisMessage;
    let depth = 0;
    window.renderQuoteBasisMessage = function (...values) {
      if (!depth) log("basisRender");
      depth += 1;
      try { return render.apply(this, values); } finally { depth -= 1; }
    };
    document.addEventListener("click", () => log("click"), true);
    new PerformanceObserver((list) => { for (const entry of list.getEntries()) if (probe.armed) probe.events.push({ kind: "longFrame", at: entry.startTime, amount: entry.duration }); })
      .observe({ type: "long-animation-frame" });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) if (probe.armed && ["pointerdown", "pointerup", "mousedown", "mouseup", "click"].includes(entry.name)) probe.events.push({ kind: "inputToPaint", at: entry.startTime, amount: entry.duration });
    }).observe({ type: "event", durationThreshold: 16 });
  });
}

// Rows the clicks changed may be redrawn; every other row must keep its DOM node.
export function armClickProbe(page, touched = []) {
  return page.evaluate((touched) => {
    window.__touchedRows = new Set(touched);
    window.__rowsBefore = new Map([...document.querySelectorAll("#basisReviewSurface li[data-basis-key]")].map((row) => [row.dataset.basisKey, { row, html: row.outerHTML }]));
    window.__clickProbe.events = [];
    window.__clickProbe.writers = [];
    window.__ownerUatProfile = {};
    window.__clickProbe.armed = true;
  }, touched);
}

export function readClickProbe(page, phaseMs = CLICK_PHASE_MS) {
  return page.evaluate((phaseMs) => {
    const probe = window.__clickProbe;
    probe.armed = false;
    const clicks = probe.events.filter((event) => event.kind === "click").map((event) => event.at);
    // A long frame starts before the click handler runs, so the click's own frame begins up to 100 ms earlier.
    const inClickPhase = (at) => clicks.some((click) => at >= click - 100 && at < click + phaseMs);
    const sum = (kind, phase = null) => probe.events.filter((event) => event.kind === kind && (phase === null || inClickPhase(event.at) === phase)).reduce((total, event) => total + event.amount, 0);
    let rebuilt = 0, kept = 0;
    for (const row of document.querySelectorAll("#basisReviewSurface li[data-basis-key]")) {
      const before = window.__rowsBefore.get(row.dataset.basisKey);
      if (!before || before.html !== row.outerHTML || window.__touchedRows.has(row.dataset.basisKey)) continue;
      if (before.row === row) kept += 1; else rebuilt += 1;
    }
    const profileMs = Object.fromEntries(Object.entries(window.__ownerUatProfile || {}).map(([name, entry]) => [name, Math.round(entry.ms)]));
    // A partial redraw must leave exactly the DOM a full render of the same state gives.
    const fresh = document.createElement("div");
    fresh.innerHTML = renderQuoteBasisMessage(state.quoteBasis, "edited");
    return {
      matchesFullRender: fresh.innerHTML === document.querySelector("#basisReviewSurface").innerHTML,
      writers: probe.writers,
      // Time per app function (the CI carrier wraps them; add more with --profile=name,name).
      profileMs,
      clicks: clicks.length,
      clickQuoteLocalStorageWrites: sum("quoteLocalStorageWrite", true),
      quoteLocalStorageWrites: sum("quoteLocalStorageWrite"),
      indexedDbPuts: sum("indexedDbPut"),
      basisRenders: sum("basisRender"),
      serialisedBytes: sum("serialisedBytes"),
      encodedBytes: sum("encodedBytes"),
      inputToPaintMs: Math.max(0, ...probe.events.filter((event) => event.kind === "inputToPaint").map((event) => event.amount)),
      clickFrames: probe.events.filter((event) => event.kind === "longFrame" && inClickPhase(event.at)).map((event) => Math.round(event.amount)),
      laterFrames: probe.events.filter((event) => event.kind === "longFrame" && !inClickPhase(event.at)).map((event) => Math.round(event.amount)),
      rebuiltRows: rebuilt, keptRows: kept,
    };
  }, phaseMs);
}

// The exact checks; they block. `posts` are the quote saves seen during the sample.
export function judgeClickCounts(sample, posts, budget, check) {
  const label = sample.group + ":" + sample.action;
  check(sample.clicks === sample.expectedClicks, label + " click was not seen by the page", sample.clicks);
  check(sample.clickQuoteLocalStorageWrites <= budget.clickQuoteLocalStorageWrites * sample.clicks, label + " wrote the quote to browser storage more than once per click", sample.clickQuoteLocalStorageWrites);
  // A deferred quote save writes its acknowledgement once; nothing else may write after the click.
  check(sample.quoteLocalStorageWrites <= sample.clicks + posts.length, label + " wrote the quote to browser storage outside the click and its save", { writes: sample.quoteLocalStorageWrites, writers: sample.writers });
  check(sample.indexedDbPuts === budget.indexedDbPuts, label + " rewrote unchanged reference files to IndexedDB", sample.indexedDbPuts);
  check(sample.basisRenders <= budget.basisRenders * sample.clicks, label + " rendered the Quote Basis more than once per click", sample.basisRenders);
  check(sample.matchesFullRender, label + " left the Quote Basis different from a full render of the same state");
  check(sample.rebuiltRows === budget.rebuiltRows, label + " rebuilt unchanged basis rows", { rebuilt: sample.rebuiltRows, kept: sample.keptRows });
  check(sample.serialisedBytes <= budget.serialisedBytesPerClick * sample.clicks + budget.serialisedBytesPerQuoteSave * posts.length,
    label + " serialised too much data", sample.serialisedBytes);
  check(sample.encodedBytes <= budget.encodedBytesPerClick * sample.clicks, label + " text-encoded too much data", sample.encodedBytes);
  check(posts.length <= budget.quoteSavesPerBurst, label + " saved the quote more than once", posts.length);
  check(posts.every((post) => post.files === 0), label + " sent reference files the server already has", posts.map((post) => post.files));
}

// Where a basis line is now, by its id.
export function basisTarget(page, lineId) {
  return page.evaluate((id) => {
    for (const section of state.quoteBasisSections) {
      const index = (section.lines || []).findIndex((line) => line.id === id);
      if (index >= 0) {
        const row = document.querySelector('#basisReviewSurface li[data-basis-key="' + CSS.escape(id) + '"]');
        return { section: section.id, index, tag: normalizeBasisTag(section.lines[index].tag), group: row?.closest(".basis-review-grid")?.getAttribute("aria-label") || "" };
      }
    }
    return null;
  }, lineId);
}

export async function lineButton(page, lineId, exclude) {
  const at = await basisTarget(page, lineId);
  if (!at) throw new Error("basis line missing: " + lineId);
  return page.locator('#basisReviewSurface [data-basis-section="' + at.section + '"][data-basis-line-index="' + at.index + '"]'
    + (exclude ? '[data-basis-tag="Exclude"]' : "[data-basis-tag]:not([data-basis-tag='Exclude'])")).first();
}

export async function blockButton(page, lineId, action) {
  const at = await basisTarget(page, lineId);
  if (!at?.group) throw new Error("basis line has no group: " + lineId);
  return page.locator('#basisReviewSurface .basis-review-grid[aria-label="' + at.group + '"] .basis-review-item[data-basis-key="' + at.group + ":" + at.section + '"] [data-basis-section-action="' + action + '"]').first();
}

// Basis line ids shown in one group (Matched, Review or Unmatched), optionally only those with a prefix.
export function linesIn(page, group, prefix = "") {
  return page.evaluate(({ group, prefix }) => [...document.querySelectorAll('#basisReviewSurface .basis-review-grid[aria-label="' + group + '"] li[data-basis-key]')]
    .map((row) => row.dataset.basisKey).filter((key) => key.startsWith(prefix)), { group, prefix });
}

// One sample: arm, click, wait out the deferred save, read and judge the counts.
// `quotePosts` is the caller's live list of quote saves; `judge(sample, posts)` adds the caller's checks.
export async function measureClicks(page, { group, action, clicks, expectedClicks = 1, touched = [], quotePosts, judge }) {
  const postsBefore = quotePosts.length;
  await armClickProbe(page, touched);
  for (const [index, click] of clicks.entries()) {
    if (index) await page.waitForTimeout(200);
    await (await click()).click();
  }
  await page.waitForTimeout(CLICK_WINDOW_MS);
  const sample = { group, action, expectedClicks, ...(await readClickProbe(page)) };
  const posts = quotePosts.slice(postsBefore);
  sample.quoteSaves = posts.length;
  judge(sample, posts);
  return sample;
}

// The four clicks every group gets: X then tick on one line, then the block's X and tick.
export function groupActions(page, lineId) {
  return [
    ["line_cross", () => lineButton(page, lineId, true)],
    ["line_tick", () => lineButton(page, lineId, false)],
    ["block_cross", () => blockButton(page, lineId, "Exclude")],
    ["block_tick", () => blockButton(page, lineId, "Include")],
  ];
}
