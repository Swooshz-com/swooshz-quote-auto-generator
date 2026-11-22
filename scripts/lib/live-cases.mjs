// The live-data quote manifest (deploy/rehearsal/live-cases.json) and the walk's
// target list. Both hold opaque quote ids and fixed reason codes only, never a
// customer name, file name or any free text, so they can be committed and stored
// as evidence. Run order: docs/internal-uat-coolify-deploy.md, step 8.
export const MANIFEST_SCHEMA = "sqag-live-cases/v1";
export const TARGETS_SCHEMA = "sqag-live-walk-targets/v1";
export const QUOTE_ID_RE = /^quote-[0-9a-f]{24}$/;
export const REASON_CODES = Object.freeze([
  "generated", "draft", "large", "many-sections", "pdf-brief", "image-brief", "older", "owner-pick",
]);
export const MAX_CASES = 20;
export const MAX_NEWEST = 5;
export const MAX_CANDIDATES = 30;
// The bucket copy (scripts/copy_live_objects_to_rehearsal.py MAX_QUOTES) takes at most this many quote ids.
export const MAX_COPY_QUOTES = 50;
const MANIFEST_KEYS = ["schema", "purpose", "approval", "newest_own_quotes", "reason_codes", "cases"];
const APPROVAL_KEYS = ["approved", "approved_at", "record"];
const CASE_KEYS = ["id", "reasons"];
// An approval record is a GitHub issue comment id or a store commit, never text.
const RECORD_RE = /^(issuecomment-\d{6,20}|[0-9a-f]{7,40})$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export class ManifestError extends Error {}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ManifestError(`${label} must be an object`);
  const extra = Object.keys(value).filter((key) => !keys.includes(key));
  if (extra.length) throw new ManifestError(`${label} has unknown keys (not shown)`);
  const missing = keys.filter((key) => !(key in value));
  if (missing.length) throw new ManifestError(`${label} is missing ${missing.join(", ")}`);
}

// Default-deny: anything outside the exact shape is refused, so free text
// (a customer name, a note) cannot ride along in a case or an approval.
export function validateManifest(manifest, { requireApproval = true } = {}) {
  exactKeys(manifest, MANIFEST_KEYS, "manifest");
  if (manifest.schema !== MANIFEST_SCHEMA) throw new ManifestError(`manifest schema must be ${MANIFEST_SCHEMA}`);
  if (JSON.stringify(manifest.reason_codes ? Object.keys(manifest.reason_codes) : []) !== JSON.stringify(REASON_CODES)) {
    throw new ManifestError("manifest reason_codes must list exactly the fixed reason codes");
  }
  const newest = manifest.newest_own_quotes;
  if (!Number.isInteger(newest) || newest < 0 || newest > MAX_NEWEST) throw new ManifestError(`newest_own_quotes must be 0 to ${MAX_NEWEST}`);
  exactKeys(manifest.approval, APPROVAL_KEYS, "manifest approval");
  const { approved, approved_at: approvedAt, record } = manifest.approval;
  if (typeof approved !== "boolean") throw new ManifestError("approval.approved must be true or false");
  if (approved) {
    if (!DATE_RE.test(String(approvedAt || ""))) throw new ManifestError("approval.approved_at must be a YYYY-MM-DD date");
    if (!RECORD_RE.test(String(record || ""))) throw new ManifestError("approval.record must be an issue comment id or a commit");
  } else if (approvedAt !== null || record !== null) {
    throw new ManifestError("an unapproved manifest has null approved_at and record");
  }
  if (requireApproval && !approved) throw new ManifestError("the manifest is not approved by the owner yet");
  if (!Array.isArray(manifest.cases) || manifest.cases.length > MAX_CASES) throw new ManifestError(`cases must be a list of at most ${MAX_CASES}`);
  const seen = new Set();
  for (const entry of manifest.cases) {
    exactKeys(entry, CASE_KEYS, "case");
    if (typeof entry.id !== "string" || !QUOTE_ID_RE.test(entry.id)) throw new ManifestError("a case id is not an opaque quote id");
    if (seen.has(entry.id)) throw new ManifestError("a case id is listed twice");
    seen.add(entry.id);
    if (!Array.isArray(entry.reasons) || entry.reasons.length === 0 || entry.reasons.some((code) => !REASON_CODES.includes(code))) {
      throw new ManifestError("a case reason is not one of the fixed reason codes");
    }
    if (new Set(entry.reasons).size !== entry.reasons.length) throw new ManifestError("a case lists a reason twice");
  }
  if (manifest.cases.length === 0 && newest === 0) throw new ManifestError("the manifest selects no quote");
  return { ids: manifest.cases.map((entry) => entry.id), newest, approved };
}

// Reason codes the walk can see for a quote the signed-in login owns.
export function derivedReasons(detail, now = Date.now()) {
  const reasons = [];
  const generated = detail?.status?.quote_generated === true;
  reasons.push(generated ? "generated" : "draft");
  const sections = Array.isArray(detail?.draft_state?.quoteBasisSections) ? detail.draft_state.quoteBasisSections : [];
  const lines = sections.reduce((total, section) => total + (Array.isArray(section?.lines) ? section.lines.length : 0), 0);
  if (sections.length >= 8) reasons.push("many-sections");
  if (lines >= 40) reasons.push("large");
  const files = Array.isArray(detail?.draft_files) ? detail.draft_files : [];
  const types = files.map((file) => String(file?.content_type || file?.type || "").toLowerCase());
  if (types.some((type) => type === "application/pdf")) reasons.push("pdf-brief");
  if (types.some((type) => type.startsWith("image/"))) reasons.push("image-brief");
  const created = Date.parse(String(detail?.created_at || ""));
  if (Number.isFinite(created) && now - created > 90 * 24 * 3600 * 1000) reasons.push("older");
  return { reasons, sections: sections.length, lines };
}

// Manifest quotes first, then the newest quotes the login owns that are not
// already listed. Ids only.
export function walkTargets(manifestIds, ownSessions, newest) {
  const targets = [...manifestIds];
  const ordered = [...ownSessions].sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  for (const session of ordered) {
    if (targets.length >= manifestIds.length + newest) break;
    if (QUOTE_ID_RE.test(session.session_id) && !targets.includes(session.session_id)) targets.push(session.session_id);
  }
  return targets;
}

// The quotes whose files the bucket copy brings: the manifest quotes, then every
// other quote the login owns, newest first, up to the copy's cap. It needs only
// the quote list, so it is known before any quote is opened, and it holds every
// walk target (the newest few owned quotes come first after the manifest's).
export function copyTargets(manifestIds, ownSessions) {
  return walkTargets(manifestIds, ownSessions, Math.max(0, MAX_COPY_QUOTES - manifestIds.length));
}

// quote_ids: the copy ids (copyTargets). candidates: what the owner picks manifest cases from.
export function targetsDocument(targets, candidates) {
  if (targets.length > MAX_COPY_QUOTES) throw new ManifestError(`the targets file holds at most ${MAX_COPY_QUOTES} quote ids`);
  for (const id of targets) if (!QUOTE_ID_RE.test(id)) throw new ManifestError("a target is not an opaque quote id");
  return {
    schema: TARGETS_SCHEMA,
    quote_ids: targets,
    candidates: candidates.slice(0, MAX_CANDIDATES).map((item) => ({
      id: item.id, reasons: item.reasons.filter((code) => REASON_CODES.includes(code)), sections: item.sections, lines: item.lines,
    })),
  };
}
