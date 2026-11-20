// The paid-AI cap for one release run (owner plan: US$1 per run, owner OK needed).
// One ledger file is shared by every writing check in the run (seed, verify), so
// the cap covers the whole run, not each command. The ledger holds labels and
// US$ amounts only.
import fs from "node:fs/promises";

export const LEDGER_SCHEMA = "sqag-ai-ledger/v1";
export const AI_CAP_MAX_USD = 1;
// Admitting a call reserves its expected worst case before it is sent: the
// measured image analysis cost about US$0.06 (rehearsals d07c910 and 25068d9),
// so four times that. The quote's reported cost replaces the reserve afterwards.
export const AI_DRAFT_RESERVE_USD = 0.25;
// A new pricing reference runs AI enrichment whose cost the app does not report
// per quote, so it is counted at this fixed amount instead (never measured).
export const AI_PRICING_ENRICHMENT_USD = 0.1;

export class AiCapError extends Error {}

const round = (value) => Math.round(value * 10000) / 10000;

// The quote readback's ai_spend.total ({status, display}) as US$, or null when
// the app could not report every call.
export function reportedUsd(total) {
  const status = total?.status;
  if (status === "none") return 0;
  if (status !== "reported") return null;
  const display = String(total.display || "");
  if (display === "< USD 0.01") return 0.01;
  const match = display.match(/^USD ([0-9][0-9,]*\.[0-9]{2})$/);
  return match ? Number(match[1].replace(/,/g, "")) : null;
}

export class AiBudget {
  constructor({ ok, capUsd, entries = [] }) {
    if (ok !== true) throw new AiCapError("paid AI needs --ai-ok (the owner's OK for this run)");
    if (!Number.isFinite(capUsd) || capUsd <= 0 || capUsd > AI_CAP_MAX_USD) {
      throw new AiCapError(`--ai-cap-usd must be above 0 and at most ${AI_CAP_MAX_USD}`);
    }
    this.capUsd = capUsd;
    this.entries = entries.map((entry) => ({ ...entry }));
    this.closed = this.entries.some((entry) => entry.usd === null);
  }

  get spentUsd() {
    return round(this.entries.reduce((total, entry) => total + (entry.usd ?? 0), 0));
  }

  // Throws before a paid call when the run cannot afford its reserve.
  admit(label, reserveUsd = AI_DRAFT_RESERVE_USD) {
    if (this.closed) throw new AiCapError(`${label}: an earlier AI cost was not reported, so no more paid AI runs`);
    if (round(this.spentUsd + reserveUsd) > this.capUsd) {
      throw new AiCapError(`${label}: US$${this.spentUsd} spent, a reserve of US$${reserveUsd} would pass the US$${this.capUsd} cap`);
    }
  }

  record(label, usd, source) {
    this.entries.push({ label, usd: usd === null ? null : round(usd), source });
    if (usd === null) {
      this.closed = true;
      throw new AiCapError(`${label}: the app did not report the AI cost, so the run cannot prove it stayed under the cap`);
    }
    if (this.spentUsd > this.capUsd) throw new AiCapError(`${label}: AI spend US$${this.spentUsd} is over the US$${this.capUsd} cap`);
  }

  summary() {
    return { cap_usd: this.capUsd, spent_usd: this.spentUsd, within_cap: !this.closed && this.spentUsd <= this.capUsd, entries: this.entries };
  }
}

export async function loadBudget(file, { ok, capUsd }) {
  let entries = [];
  let text = null;
  try {
    text = await fs.readFile(file, "utf8");
  } catch (error) {
    if (error?.code !== "ENOENT") throw new AiCapError("--ai-ledger is unreadable");
  }
  if (text !== null) {
    let ledger;
    try {
      ledger = JSON.parse(text);
    } catch {
      throw new AiCapError("--ai-ledger is not a ledger file");
    }
    if (ledger?.schema !== LEDGER_SCHEMA || !Array.isArray(ledger.entries)) throw new AiCapError("--ai-ledger is not a ledger file");
    if (ledger.cap_usd !== capUsd) throw new AiCapError("--ai-cap-usd differs from the cap this run's ledger started with");
    entries = ledger.entries.filter((entry) => typeof entry?.label === "string" && (entry.usd === null || Number.isFinite(entry.usd)));
  }
  return new AiBudget({ ok, capUsd, entries });
}

export async function saveBudget(file, budget) {
  await fs.writeFile(file, JSON.stringify({ schema: LEDGER_SCHEMA, cap_usd: budget.capUsd, entries: budget.entries }, null, 2) + "\n", "utf8");
}
