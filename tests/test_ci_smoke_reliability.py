"""Execute maintained smoke guards with synthetic inputs and no browser/server."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CiSmokeReliabilityTest(unittest.TestCase):
    def run_node(self, script):
        result = subprocess.run(['node', '-e', script], cwd=ROOT, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_recovery_waits_and_rejects_failed_final_save(self):
        self.run_node(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/playwright-smoke.mjs', 'utf8');
const start = source.indexOf('async function verifyGenerationLoadingModalSurvivesRefresh(');
const end = source.indexOf('  const cases = [', start);
const scope = {alpha1Scope: false};
vm.runInNewContext(source.slice(start, end) + '\n}\nglobalThis.drain = verifyGenerationLoadingModalSurvivesRefresh;', scope);
(async () => {
  let waited = 0;
  await scope.drain({waitForTimeout: async ms => {waited += ms;}});
  assert.equal(waited, 60000, 'Default path did not drain the server write window');
  const saveStart = source.indexOf('      const finalSaveResponse =', start);
  const saveEnd = source.indexOf('      await page.waitForFunction', saveStart);
  const body = source.slice(saveStart, saveEnd);
  for (const status of [200, 429, 500]) {
    const calls = [];
    const response = {status: () => status, url: () => 'http://127.0.0.1/api/quote-sessions',
      request: () => ({method: () => 'POST'})};
    const context = {URL, alpha1Scope: false,
      page: {waitForResponse: (predicate, options) => {
        calls.push('listen'); assert(predicate(response)); assert.equal(options.timeout, 15000);
        assert(!predicate({...response, request: () => ({method: () => 'GET'})}));
        return Promise.resolve(response);
      }}, releaseFinalSave: () => calls.push('release')};
    const promise = vm.runInNewContext('(async () => {' + body + '})()', context);
    if (status === 200) await promise;
    else await assert.rejects(promise, new RegExp('Actual resumed final save returned HTTP ' + status));
    assert.deepEqual(calls, ['listen', 'release']);
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
''')

    def test_quote_session_429_fails_and_screenshots_skip_only_g3_tail(self):
        self.run_node(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('scripts/playwright-smoke.mjs', 'utf8');
const start = source.indexOf('function requireNoQuoteSessionRateLimits(');
const end = source.indexOf('async function main()', start);
const scope = {};
vm.runInNewContext(source.slice(start, end) + '\nglobalThis.guard = requireNoQuoteSessionRateLimits;', scope);
for (const suffix of ['', '/saved-id', '/pricing-selections', '?mode=save']) {
  assert.throws(() => scope.guard(['429 http://127.0.0.1/api/quote-sessions' + suffix]), /rate limited/);
}
for (const problems of [[], ['400 http://127.0.0.1/api/quote-sessions'], ['429 http://127.0.0.1/api/unrelated']]) scope.guard(problems);
const main = source.slice(source.indexOf('async function main()'));
assert.equal(main.split('requireNoQuoteSessionRateLimits(networkProblems);').length - 1, 2);
const tailStart = source.indexOf('  // Keep real mocked-provider pricing');
const tailEnd = source.indexOf('  if (alpha1Scope)', tailStart);
(async () => {
  for (const screenshots of [false, true]) {
    let calls = 0;
    await vm.runInNewContext('(async () => {' + source.slice(tailStart, tailEnd) + '})()', {
      options: {screenshots}, runG3PricingScope: async () => {calls++;}});
    assert.equal(calls, screenshots ? 0 : 1);
  }
  const g3 = source.slice(source.indexOf('async function runG3PricingScope()'), start);
  assert(g3.includes('chromium.launch({ headless: !options.headed })'));
  assert(!g3.includes('channel: "chrome"'));
})().catch(error => {console.error(error); process.exitCode = 1;});
''')

    def test_protected_upload_checkpoint_and_included_guards_reject_corruption(self):
        self.run_node(r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {createHash} = require('node:crypto');
const source = fs.readFileSync('scripts/playwright-alpha-smoke.mjs', 'utf8');
const start = source.indexOf('function assert(condition, message)');
const end = source.indexOf('function generationRunIdBinding(', start);
const scope = {Buffer, createHash};
vm.runInNewContext(source.slice(start, end) + '\nglobalThis.subjects = {requireOriginalUploadBytes, requirePersistedIncluded, requireCustomerCheckpoint};', scope);
const guards = scope.subjects;
const originals = [{name: 'Synthetic render', file_role: 'reference', type: 'image/png', buffer: Buffer.from('render')},
  {name: 'Synthetic logo', file_role: 'quote_company_logo', type: 'image/png', buffer: Buffer.from('logo')}];
const detail = {session_id: 'synthetic-session', draft_files: originals.map((file, i) => ({
  session_file_key: 'file-' + i, name: file.name, file_role: file.file_role, type: file.type, size: file.buffer.length,
  data_url: 'data:' + file.type + ';base64,' + file.buffer.toString('base64'),
  content_fingerprint: 'sha256:' + createHash('sha256').update(file.buffer).digest('hex')})),
  draft_state: {customerStepCheckpoint: {sessionId: 'synthetic-session', pricingReferenceDigest: 'sha256:' + 'a'.repeat(64)},
    outputRows: [{description: 'Synthetic fitting', price_mode: 'Included', pricing_authority: {variant: 'included', price: 0}, amount: 0}]}};
const checkpoint = {current: true, checkpoint: {sessionId: 'synthetic-session'}, reference: {digest_sha256: 'sha256:' + 'a'.repeat(64)}};
guards.requireOriginalUploadBytes(detail, originals);
guards.requirePersistedIncluded(detail, 'Synthetic fitting');
guards.requireCustomerCheckpoint(detail, checkpoint);
for (let index = 0; index < originals.length; index++) {
  for (const key of Object.keys(detail.draft_files[index])) {
    const bad = structuredClone(detail); bad.draft_files[index][key] = 'wrong';
    // A changed but still unique opaque key is allowed; byte proof is keyed by
    // independently uploaded name/role, not a copied request tuple.
    if (key === 'session_file_key') bad.draft_files[index][key] = '';
    assert.throws(() => guards.requireOriginalUploadBytes(bad, originals), /Protected original upload/);
  }
  // Even internally consistent metadata/hash cannot authorise replacement bytes.
  const replacement = Buffer.alloc(originals[index].buffer.length, 120);
  const bad = structuredClone(detail);
  bad.draft_files[index].data_url = 'data:image/png;base64,' + replacement.toString('base64');
  bad.draft_files[index].content_fingerprint = 'sha256:' + createHash('sha256').update(replacement).digest('hex');
  assert.throws(() => guards.requireOriginalUploadBytes(bad, originals), /upload bytes changed/);
}
for (const key of ['sessionId', 'pricingReferenceDigest']) {
  const bad = structuredClone(detail); bad.draft_state.customerStepCheckpoint[key] = 'wrong';
  assert.throws(() => guards.requireCustomerCheckpoint(bad, checkpoint), /Protected Customer checkpoint/);
}
assert.throws(() => guards.requireCustomerCheckpoint(detail, {...checkpoint, current: false}), /current reference digest/);
for (const change of [row => row.price_mode = 'Priced', row => row.amount = 12,
  row => row.pricing_authority.variant = 'catalog', row => row.pricing_authority.price = 12,
  row => row.pricing_authority = null]) {
  const bad = structuredClone(detail); change(bad.draft_state.outputRows[0]);
  assert.throws(() => guards.requirePersistedIncluded(bad, 'Synthetic fitting'), /Included authority/);
}
''')


if __name__ == '__main__':
    unittest.main()
