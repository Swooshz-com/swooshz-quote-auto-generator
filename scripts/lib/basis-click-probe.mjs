// Quote Basis click probe: counts the work each tick or X causes in the page.
// Shared by the CI click-budget job (scripts/playwright-basis-click-budget.mjs, on a synthetic
// real-sized quote) and Pass A of the live-data test (scripts/rehearsal-check.mjs --click-budget,
// on the copy of live). The page functions it reads (state, renderQuoteBasisMessage,
// QUOTE_SESSION_STORAGE_KEY) are the app's own globals in webapp/static/app.js.

// A deferred quote save is armed 650 ms after a click, so it must be sent within 1.5 s of the last
// click; only its answer may come later (a slow server), and the sample waits for that. Work before
// the 400 ms mark belongs to the click itself.
export const CLICK_WINDOW_MS = 1500;
export const CLICK_PHASE_MS = 400;

// The exact counts. A click that changes the quote writes one browser copy of it and renders the
// Quote Basis once; it never rewrites reference files to IndexedDB, rebuilds unchanged rows or saves
// more than once per burst. A click on a line already in that state changes and writes nothing.
// The byte budgets here are the CI job's first pass, on its never-reopened synthetic quote (about
// 215 KB per click, 1.3 MB per save); any other quote gets its own (see quoteClickBudget).
export const CLICK_COUNT_BUDGET = Object.freeze({
  clickQuoteLocalStorageWrites: 1, indexedDbPuts: 0, basisRenders: 1, rebuiltRows: 0, quoteSavesPerBurst: 1,
  serialisedBytesPerClick: 256 * 1024, serialisedBytesPerIdleClick: 64 * 1024, serialisedBytesPerQuoteSave: 1.5 * 1024 * 1024,
  encodedBytesPerClick: 16 * 1024,
});

// The sizes of the open quote, measured before the first measured click, so nothing the clicks do can
// raise a budget. A reopened quote's browser copy also holds the dashboard-restore key (a JSON string
// of the whole draft), so it is about twice the draft; a quote never reopened holds the draft once.
export function measureQuoteSizes(page) {
  return page.evaluate(() => {
    const chars = (value) => (JSON.stringify(value) ?? "").length;
    const draft = currentQuoteSessionDraftState();
    const snapshot = buildSessionSnapshot();
    return {
      quote_bytes: chars(draft), quote_as_text_bytes: chars(JSON.stringify(draft)), sections_bytes: chars(draft.quoteBasisSections), basis_bytes: chars(draft.quoteBasis),
      browser_copy_bytes: chars(snapshot), restored_key_bytes: chars(snapshot.quoteSessionRestoredDraftKey), view_bytes: chars(buildWorkspaceViewSnapshot()),
    };
  });
}

// The small calls of one click or save (the view, per-row pricing checks, file keys, the post's own
// fields): at most 4,400 characters a click on the two largest real quotes and 3,400 a save in CI.
export const SMALL_CALLS_BYTES = 16 * 1024;
// What the browser copy holds besides the draft and the restore key (ledger revisions, ids): 920 to 2,270.
export const COPY_EXTRA_BYTES = 8 * 1024;
// The workspace view holds ids and dashboard choices, no quote data: 668 characters.
export const VIEW_BYTES = 4 * 1024;
// One save copies the draft, builds two comparison keys (two serialisations each) and posts it.
const DRAFT_SERIALISATIONS_PER_SAVE = 6;

// The byte budgets of a measured quote. A changing click: one browser copy, the section clone and two
// basis projections. A click with nothing to change: the section clone alone. A save: the draft six
// times, one browser copy and four basis projections. Each is exact but for SMALL_CALLS_BYTES, so a
// second copy of a quote larger than that, in a click or a save, goes over.
export function quoteClickBudget(sizes) {
  return {
    ...CLICK_COUNT_BUDGET,
    serialisedBytesPerClick: sizes.browser_copy_bytes + sizes.sections_bytes + 2 * sizes.basis_bytes + SMALL_CALLS_BYTES,
    serialisedBytesPerIdleClick: sizes.sections_bytes + SMALL_CALLS_BYTES,
    serialisedBytesPerQuoteSave: DRAFT_SERIALISATIONS_PER_SAVE * sizes.quote_bytes + sizes.browser_copy_bytes + 4 * sizes.basis_bytes + SMALL_CALLS_BYTES,
  };
}

// Every size a budget adds is bounded, so a fault that is there before the sizes are read cannot raise
// its own budget:
// - the draft by the caller's limit, which the page does not set (Pass A: the quote as live saved it;
//   CI: a constant of its synthetic quote);
// - the sections and the basis by the draft they are read from;
// - the restore key by that draft as a JSON string, and the browser copy by the draft, the key and
//   COPY_EXTRA_BYTES;
// - the view by VIEW_BYTES; no budget adds its size.
export function judgeQuoteSizes(sizes, draftLimit, check) {
  check(Number.isSafeInteger(draftLimit) && sizes.quote_bytes <= draftLimit, "the draft is larger than the quote it was opened from allows", { ...sizes, draft_limit: draftLimit });
  check(sizes.restored_key_bytes <= sizes.quote_as_text_bytes + COPY_EXTRA_BYTES, "the browser copy's restore key is larger than one copy of the quote", sizes);
  check(sizes.browser_copy_bytes - sizes.restored_key_bytes <= sizes.quote_bytes + COPY_EXTRA_BYTES, "the browser copy holds the quote more than once beside its restore key", sizes);
  check(sizes.view_bytes <= VIEW_BYTES, "the workspace view holds more than ids and dashboard choices", sizes);
}

// Probes in the page. Each counts only while armed, so the caller's own page reads never count.
export function installClickProbe(page) {
  return page.evaluate(() => {
    const probe = window.__clickProbe = { armed: false, events: [], writers: [], clickStates: [] };
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
    // Whether each click changed the quote: its sections and rows just before the app handles the
    // click and once it has. A click that changed nothing must have had nothing to change, judged
    // here both by the line's own tag and confirmation and by the app's own plan for that click, so
    // neither a click dropped after the plan nor a plan that finds nothing counts as nothing to change.
    const quoteNow = () => stringify([state.quoteBasisSections, state.lineItems]);
    const settled = (line, wanted) => {
      const tag = normalizeBasisTag(line.tag);
      const custom = isCustomPricingBasisLine(line);
      if (wanted === "Exclude") return tag === "Exclude" && !(custom && line.custom_confirmed);
      return (tag === wanted || tag === "Custom") && (!custom || Boolean(line.custom_confirmed));
    };
    const linesToChange = (target) => {
      const button = target?.closest?.("#basisReviewSurface [data-basis-section-action], #basisReviewSurface [data-basis-tag]");
      const section = state.quoteBasisSections.find((item) => item.id === button?.dataset.basisSection);
      if (!section) return 1;
      const { basisSectionAction: blockAction, basisSectionGroup: group, basisTag, basisLineIndex } = button.dataset;
      const lines = blockAction
        ? withBasisPricingIndex(() => section.lines.filter((line) => !group || (!basisLineIsInformationalDimension(line) && basisPricingGroup(line, section) === group)))
        : [section.lines[Number(basisLineIndex)]];
      const unsettled = lines.filter((line) => !line || !settled(line, blockAction || basisTag)).length;
      const plan = () => (blockAction ? planBasisTagChanges(section.id, blockAction, null, group || null) : planBasisTagChanges(section.id, normalizeBasisTag(basisTag), Number(basisLineIndex)));
      return unsettled || (withBasisPricingIndex(plan) ? 1 : 0);
    };
    document.addEventListener("click", (event) => {
      log("click");
      if (!probe.armed) return;
      const entry = { changed: null, linesToChange: 0 };
      probe.clickStates.push(entry);
      const before = quoteNow();
      setTimeout(() => {
        entry.changed = quoteNow() !== before;
        if (entry.changed) return;
        // The app's own functions, read while nothing is counted.
        const armed = probe.armed;
        probe.armed = false;
        // A read that throws leaves the click ignored, never "nothing to change".
        entry.linesToChange = 1;
        try { entry.linesToChange = linesToChange(event.target); } finally { probe.armed = armed; }
      }, 0);
    }, true);
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
    window.__clickProbe.clickStates = [];
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
      changingClicks: probe.clickStates.filter((entry) => entry.changed === true).length,
      // Nothing to change: the lines were already in the clicked state. Ignored: they were not.
      idleClicks: probe.clickStates.filter((entry) => entry.changed === false && entry.linesToChange === 0).length,
      ignoredClicks: probe.clickStates.filter((entry) => entry.changed === false && entry.linesToChange > 0).length,
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
  check(sample.changingClicks + sample.idleClicks === sample.clicks, label + " changed nothing although a line it acts on was not in that state yet",
    { changing: sample.changingClicks, nothing_to_change: sample.idleClicks, ignored: sample.ignoredClicks });
  check(sample.clickQuoteLocalStorageWrites <= budget.clickQuoteLocalStorageWrites * sample.changingClicks, label + " wrote the quote to browser storage more than once per click that changed it",
    { writes: sample.clickQuoteLocalStorageWrites, changing: sample.changingClicks });
  // Any time in the sample: on a slow machine the click's own write can come after the click phase. A
  // save's acknowledgement is its own write, so it never stands in for the click's.
  check(sample.quoteLocalStorageWrites >= sample.changingClicks + posts.length, label + " changed the quote without writing it to browser storage",
    { writes: sample.quoteLocalStorageWrites, changing: sample.changingClicks, saves: posts.length });
  // A deferred quote save writes its acknowledgement once; nothing else may write after the click.
  check(sample.quoteLocalStorageWrites <= sample.changingClicks + posts.length, label + " wrote the quote to browser storage outside the click and its save", { writes: sample.quoteLocalStorageWrites, writers: sample.writers });
  check(sample.indexedDbPuts === budget.indexedDbPuts, label + " rewrote unchanged reference files to IndexedDB", sample.indexedDbPuts);
  check(sample.basisRenders <= budget.basisRenders * sample.clicks, label + " rendered the Quote Basis more than once per click", sample.basisRenders);
  check(sample.matchesFullRender, label + " left the Quote Basis different from a full render of the same state");
  check(sample.rebuiltRows === budget.rebuiltRows, label + " rebuilt unchanged basis rows", { rebuilt: sample.rebuiltRows, kept: sample.keptRows });
  // Only the clicks the probe made and the saves a burst may make add to the budget.
  const changing = Math.min(sample.changingClicks, sample.expectedClicks);
  const allowed = budget.serialisedBytesPerClick * changing + budget.serialisedBytesPerIdleClick * (sample.expectedClicks - changing)
    + budget.serialisedBytesPerQuoteSave * Math.min(posts.length, budget.quoteSavesPerBurst);
  check(sample.serialisedBytes <= allowed, label + " serialised too much data", { serialised: sample.serialisedBytes, allowed });
  check(sample.encodedBytes <= budget.encodedBytesPerClick * sample.clicks, label + " text-encoded too much data", sample.encodedBytes);
  check(posts.length <= budget.quoteSavesPerBurst, label + " saved the quote more than once", posts.length);
  check(sample.lateSaves === 0, label + " sent a quote save after the " + CLICK_WINDOW_MS + " ms click window", sample.lateSaves);
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

// A slow server's answer to a quote save lands in the sample that caused it, never in the next one.
// Only the answer may be late: a save sent after the click window fails its sample (lateSaves).
const finishQuoteSaves = (page) => page.waitForFunction(() => !quoteSessionDraftSaveTimer && !quoteSessionDraftSavePromise && !appIsBusy(), null, { timeout: 30000 });

// One sample: arm, click, wait out the click window and the answer to a save sent in it, read and judge.
// `quotePosts` is the caller's live list of quote saves; `judge(sample, posts)` adds the caller's checks.
export async function measureClicks(page, { group, action, clicks, expectedClicks = 1, touched = [], quotePosts, judge }) {
  const postsBefore = quotePosts.length;
  await armClickProbe(page, touched);
  for (const [index, click] of clicks.entries()) {
    if (index) await page.waitForTimeout(200);
    await (await click()).click();
  }
  await page.waitForTimeout(CLICK_WINDOW_MS);
  const postsInWindow = quotePosts.length;
  await finishQuoteSaves(page);
  const sample = { group, action, expectedClicks, ...(await readClickProbe(page)) };
  const posts = quotePosts.slice(postsBefore);
  sample.quoteSaves = posts.length;
  sample.lateSaves = quotePosts.length - postsInWindow;
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

// One sample's facts for a result file: counts and timings only, never quote text. Timings vary
// with the machine: recorded, never judged here.
export const clickFacts = (sample) => ({
  group: sample.group, action: sample.action, clicks: sample.clicks,
  clicks_that_changed: sample.changingClicks, clicks_with_nothing_to_change: sample.idleClicks, clicks_ignored: sample.ignoredClicks,
  quote_local_storage_writes: sample.quoteLocalStorageWrites, indexed_db_puts: sample.indexedDbPuts, basis_renders: sample.basisRenders,
  rebuilt_rows: sample.rebuiltRows, kept_rows: sample.keptRows, matches_full_render: sample.matchesFullRender,
  serialised_bytes: sample.serialisedBytes, encoded_bytes: sample.encodedBytes, quote_saves: sample.quoteSaves, late_saves: sample.lateSaves,
  input_to_paint_ms: Math.round(sample.inputToPaintMs), longest_frame_ms: Math.max(0, ...sample.clickFrames, ...sample.laterFrames),
});

// The whole probe of the quote shown in Quote Basis: its own size and budgets, the four clicks in
// every group, then five fast clicks on three lines of the largest group and of Unmatched, whose
// clicks save: five clicks arm one save, not five. `draftLimit` is the largest draft this quote may
// have (see judgeQuoteSizes). `facts` is filled as it goes, so a probe that stops half way keeps what
// it measured.
export async function probeOpenQuote(page, { quotePosts, facts, check, draftLimit }) {
  // Any save that opening Quote Basis armed finishes before the first measured click.
  await page.waitForTimeout(CLICK_WINDOW_MS);
  await finishQuoteSaves(page);
  const sizes = await measureQuoteSizes(page);
  const budget = quoteClickBudget(sizes);
  Object.assign(facts, { ...sizes, draft_limit: draftLimit, budget, groups: {}, samples: [], bursts: [] });
  judgeQuoteSizes(sizes, draftLimit, check);
  await installClickProbe(page);
  const measure = async (options) => clickFacts(await measureClicks(page, { ...options, quotePosts, judge: (sample, posts) => judgeClickCounts(sample, posts, budget, check) }));
  for (const group of ["Matched", "Review", "Unmatched"]) {
    const lines = await linesIn(page, group);
    facts.groups[group] = lines.length;
    if (!lines.length) continue;
    const first = facts.samples.length;
    for (const [action, click] of groupActions(page, lines[0])) facts.samples.push(await measure({ group, action, clicks: [click] }));
    check(facts.samples.slice(first).some((sample) => sample.clicks_that_changed > 0), group + " clicks all had nothing to change, so the group was not measured");
  }
  check(facts.samples.length > 0, "the quote shows no basis line to click");
  const largest = Object.entries(facts.groups).sort((a, b) => b[1] - a[1])[0][0];
  for (const group of new Set([largest, "Unmatched"])) {
    const three = (await linesIn(page, group)).slice(0, 3);
    if (three.length < 3) continue;
    const burst = await measure({
      group, action: "burst", expectedClicks: 5, touched: three,
      clicks: [() => lineButton(page, three[0], true), () => lineButton(page, three[1], true), () => lineButton(page, three[2], true),
        () => lineButton(page, three[0], false), () => lineButton(page, three[1], false)],
    });
    facts.bursts.push(burst);
    check(burst.clicks_that_changed > 0, group + " burst clicks all had nothing to change, so the burst was not measured");
  }
  return { sizes, budget };
}
