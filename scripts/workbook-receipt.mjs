import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

const digest = (bytes) => createHash("sha256").update(bytes).digest("hex");
const requireReceipt = (condition, message) => {
  if (!condition) throw new Error(message);
};

// The owning local harness supplies filesystem and HTTP evidence independently
// of the result URL. Local publication proofs intentionally have no job ID;
// the accepted job is joined through its actual generator workbook bytes.
export class LocalWorkbookReceipt {
  constructor(child, dataRoot, outputRoot) {
    this.child = child;
    this.dataRoot = dataRoot;
    this.outputRoot = outputRoot;
    this.sequence = 0;
    this.responses = [];
    this.pending = "";
    this.overflow = false;
    this.observe = (chunk) => {
      this.pending += String(chunk);
      if (this.pending.length > 32768) { this.overflow = true; return; }
      const lines = this.pending.split("\n");
      this.pending = lines.pop();
      for (const line of lines) {
        const match = line.match(/"GET (\/api\/quote-sessions\/[^/]+\/download\/xlsx\?\S+) HTTP\/[0-9.]+" ([0-9]{3}) /);
        if (!match) continue;
        if (this.responses.length >= 32) { this.overflow = true; continue; }
        this.responses.push({ sequence: ++this.sequence, pathname: match[1], status: Number(match[2]), serverPid: child.pid });
      }
    };
    child.stderr.on("data", this.observe);
  }

  async committed(sessionId, jobId, exported, previousPublicationId = "") {
    requireReceipt(this.child.exitCode === null && !this.overflow
      && /^quote-[a-z0-9-]+$/.test(sessionId) && /^job-[a-z0-9-]+$/.test(jobId),
    "Workbook receipt needs the original live server and accepted session/job.");
    const metadataBytes = await fs.readFile(path.join(this.dataRoot, "quote-sessions", sessionId, "quote-session.json"));
    const metadata = JSON.parse(metadataBytes.toString("utf8"));
    const publication = metadata.publication, proof = publication?.proof;
    const committed = proof?.publication, artifact = proof?.artifacts?.xlsx;
    requireReceipt(metadata.session_id === sessionId && metadata.status?.quote_generated === true
      && publication?.state === "published" && proof?.schema === "swooshz.sqag.quote-publication-proof.v2"
      && proof?.version === 2 && proof?.commercial_state_schema === "swooshz.sqag.commercial-state.v1"
      && proof?.subject?.session_id === sessionId && proof?.subject?.scope === "workspace"
      && proof?.subject?.workspace_id === "local-workspace" && proof?.subject?.owner_id === "local-user"
      && committed?.id && committed.id !== previousPublicationId && committed.id === publication.active_publication_id
      && committed.run_id === publication.run_id && committed.job_id === "" && publication.job_id === ""
      && Number.isSafeInteger(proof.output_revision) && proof.output_revision >= 0
      && proof.output_revision === metadata.draft_state?.outputRevision
      && /^[0-9a-f]{64}$/.test(proof.commercial_state_digest)
      && artifact?.session_id === sessionId && artifact?.filename === "quotation.xlsx"
      && artifact?.publication_id === committed.id && artifact?.run_id === committed.run_id
      && Number.isSafeInteger(artifact?.size_bytes) && artifact.size_bytes > 0 && /^[0-9a-f]{64}$/.test(artifact?.sha256)
      && exported?.exists === true && exported?.stale === false
      && ["filename", "publication_id", "run_id", "sha256", "size_bytes"].every((key) =>
        artifact[key] === metadata.exports?.xlsx?.[key] && artifact[key] === exported[key]),
    "Committed workbook proof, owner, revision or export metadata differs.");
    const generated = await fs.readFile(path.join(this.outputRoot, jobId, "quotation.xlsx"));
    requireReceipt(generated.length === artifact.size_bytes && digest(generated) === artifact.sha256,
      "Accepted job workbook bytes differ from the persisted publication.");
    const identity = ["quote-publication-comparison-v1", "local", "local-workspace", "local-user", sessionId,
      committed.id, committed.run_id, committed.job_id, proof.output_revision,
      proof.commercial_state_schema, proof.commercial_state_digest, "xlsx", artifact.filename,
      artifact.publication_id, artifact.run_id, artifact.sha256, artifact.size_bytes];
    const binding = digest(JSON.stringify(identity));
    const url = `/api/quote-sessions/${sessionId}/download/xlsx?publication=${binding}`;
    requireReceipt(exported.url === url, "Protected workbook URL differs from independently persisted identity.");
    return { url, bytes: artifact.size_bytes, sha256: artifact.sha256, metadataSha256: digest(metadataBytes) };
  }

  async browserDownload(receipt, download, bytes, origin, afterSequence) {
    requireReceipt(download.suggestedFilename() === "quotation.xlsx" && download.url() === new URL(receipt.url, origin).href
      && bytes.length === receipt.bytes && digest(bytes) === receipt.sha256,
    "Native browser workbook URL, filename, size or digest differs from the committed receipt.");
    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      requireReceipt(!this.overflow && this.child.exitCode === null, "Original workbook HTTP receipt source is unavailable.");
      const response = this.responses.find((item) => item.sequence > afterSequence && item.pathname === receipt.url);
      if (response) {
        requireReceipt(response.status === 200, "Original owning server did not serve the protected workbook successfully.");
        return response;
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
    throw new Error("Original owning-server protected workbook HTTP receipt was not observed.");
  }

  close() {
    this.child.stderr.off("data", this.observe);
  }
}
