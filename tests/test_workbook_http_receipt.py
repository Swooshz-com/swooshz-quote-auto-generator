"""Exercise the maintained receipt owner without Playwright or network access."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r'''
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { LocalWorkbookReceipt } from './scripts/workbook-receipt.mjs';
const dir = process.argv[1], session = 'quote-synthetic', job = 'job-synthetic';
const bytes = Buffer.from('synthetic workbook bytes');
const sha256 = createHash('sha256').update(bytes).digest('hex');
const artifact = {session_id: session, filename: 'quotation.xlsx', publication_id: 'publication-synthetic',
  run_id: 'run-synthetic', size_bytes: bytes.length, sha256};
const proof = {schema: 'swooshz.sqag.quote-publication-proof.v2', version: 2,
  commercial_state_schema: 'swooshz.sqag.commercial-state.v1', commercial_state_digest: 'a'.repeat(64), output_revision: 3,
  subject: {session_id: session, scope: 'workspace', workspace_id: 'local-workspace', owner_id: 'local-user'},
  publication: {id: artifact.publication_id, run_id: artifact.run_id, job_id: ''}, artifacts: {xlsx: artifact}};
// Independently spell the versioned comparison tuple; a URL is never its own expectation.
const comparison = createHash('sha256').update(JSON.stringify(['quote-publication-comparison-v1', 'local',
  'local-workspace', 'local-user', session, artifact.publication_id, artifact.run_id, '', 3,
  proof.commercial_state_schema, proof.commercial_state_digest, 'xlsx', 'quotation.xlsx',
  artifact.publication_id, artifact.run_id, sha256, bytes.length])).digest('hex');
const url = `/api/quote-sessions/${session}/download/xlsx?publication=${comparison}`;
const exported = {...artifact, exists: true, stale: false, url};
const metadata = {session_id: session, status: {quote_generated: true}, draft_state: {outputRevision: 3},
  publication: {state: 'published', active_publication_id: artifact.publication_id, run_id: artifact.run_id, job_id: '', proof},
  exports: {xlsx: {...artifact}}};
const dataRoot = path.join(dir, 'data'), outputRoot = path.join(dir, 'output');
const metadataFile = path.join(dataRoot, 'quote-sessions', session, 'quote-session.json');
const generatedFile = path.join(outputRoot, job, 'quotation.xlsx');
await fs.mkdir(path.dirname(metadataFile), {recursive: true});
await fs.mkdir(path.dirname(generatedFile), {recursive: true});
await fs.writeFile(generatedFile, bytes);
const writeMetadata = value => fs.writeFile(metadataFile, JSON.stringify(value));
await writeMetadata(metadata);
const child = {stderr: new EventEmitter(), exitCode: null, pid: 123};
const subject = new LocalWorkbookReceipt(child, dataRoot, outputRoot);
try {
  const receipt = await subject.committed(session, job, exported);
  assert.equal(receipt.url, url);
  const download = {url: () => 'http://127.0.0.1:17' + url, suggestedFilename: () => 'quotation.xlsx'};
  const line = code => `127.0.0.1 - - [synthetic] "GET ${url} HTTP/1.1" ${code} -\n`;
  // A prior response cannot supply the new browser download's HTTP observation.
  child.stderr.emit('data', line(404));
  const sequence = subject.sequence;
  const pending = subject.browserDownload(receipt, download, bytes, 'http://127.0.0.1:17', sequence);
  const success = line(200);
  child.stderr.emit('data', success.slice(0, 20));
  child.stderr.emit('data', success.slice(20));
  assert.deepEqual(await pending, {sequence: 2, pathname: url, status: 200, serverPid: 123});
  for (const mutate of [
    m => {m.session_id = 'quote-other';},
    m => {m.status.quote_generated = false;},
    m => {m.publication.state = 'staging';},
    m => {m.publication.proof.schema = 'unknown';},
    m => {m.publication.proof.subject.owner_id = 'other';},
    m => {m.publication.proof.subject.workspace_id = 'other';},
    m => {m.publication.proof.subject.session_id = 'quote-other';},
    m => {m.publication.proof.output_revision = 2;},
    m => {m.publication.proof.publication.job_id = job;},
    m => {m.publication.proof.artifacts.xlsx.filename = 'other.xlsx';},
    m => {m.exports.xlsx.sha256 = 'b'.repeat(64);},
  ]) {
    const bad = structuredClone(metadata); mutate(bad); await writeMetadata(bad);
    await assert.rejects(subject.committed(session, job, exported));
  }
  await writeMetadata(metadata);
  await assert.rejects(subject.committed('../quote-other', job, exported));
  await assert.rejects(subject.committed(session, '../job-other', exported));
  await assert.rejects(subject.committed(session, job, exported, artifact.publication_id));
  for (const bad of [{...exported, url: url.replace(comparison, '0'.repeat(64))}, {...exported, exists: false}, {...exported, stale: true}]) {
    await assert.rejects(subject.committed(session, job, bad));
  }
  await fs.writeFile(generatedFile, 'wrong generator bytes');
  await assert.rejects(subject.committed(session, job, exported), /Accepted job workbook bytes/);
  await fs.writeFile(generatedFile, bytes);
  for (const [record, content] of [
    [{...download, suggestedFilename: () => 'other.xlsx'}, bytes],
    [{...download, url: () => 'http://127.0.0.1:18' + url}, bytes],
    [download, Buffer.from('wrong bytes')],
  ]) await assert.rejects(subject.browserDownload(receipt, record, content, 'http://127.0.0.1:17', sequence));
  await assert.rejects(subject.browserDownload(receipt, download, bytes, 'http://127.0.0.1:17', 0), /did not serve/);
  child.exitCode = 1;
  await assert.rejects(subject.committed(session, job, exported));
  child.exitCode = null;
  child.stderr.emit('data', 'x'.repeat(32769));
  await assert.rejects(subject.committed(session, job, exported));
  assert.equal(child.stderr.listenerCount('data'), 1);
} finally {
  subject.close();
  assert.equal(child.stderr.listenerCount('data'), 0);
}
console.log(JSON.stringify({status: 'PASS'}));
'''


class WorkbookHttpReceiptTest(unittest.TestCase):
    def test_job_metadata_url_http_and_browser_bytes_bind_and_reject_invalid_controls(self):
        (ROOT / "_tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="wr-", dir=ROOT / "_tmp") as temporary:
            result = subprocess.run(["node", "--input-type=module", "-e", DRIVER, temporary],
                                    cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "PASS"})


if __name__ == "__main__":
    unittest.main()
