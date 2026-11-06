"""Check the maintained alpha smoke trace on both normalisation POST routes."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CiBrowserScopeTest(unittest.TestCase):
    def test_existing_normalization_fix_observes_both_real_post_routes(self):
        # Execute the actual maintained trace callback with controlled responses.
        # This needs no browser package, server, credentials or provider.
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const source = fs.readFileSync('scripts/playwright-alpha-smoke.mjs', 'utf8');
const start = source.indexOf('function attachTrace(page, baseUrl) {');
const end = source.indexOf('async function makePng(', start);
assert(start >= 0 && end > start);
const trace = {normalizations: [], pending: [], pageErrors: []};
const scope = {URL, trace, safeJsonRequest: request => request.payload,
  safeErrorText: String, quoteSaveRequestRecords: new WeakMap()};
vm.runInNewContext(source.slice(start, end) + '\nglobalThis.subject = attachTrace;', scope);
const page = new EventEmitter(), origin = 'http://127.0.0.1:17';
scope.subject(page, origin);
function emit(path, method = 'POST', host = origin) {
  const request = {method: () => method, payload: {marker: path}};
  page.emit('response', {request: () => request, url: () => host + path,
    headers: () => ({'content-type': 'application/json'}), status: () => 200,
    json: async () => ({status: 'normalized', line_items: [{id: path}]})});
}
(async () => {
  emit('/api/line-items/normalize');
  emit('/api/quote-sessions/pricing-selections');
  emit('/api/line-items/normalize', 'GET');
  emit('/api/line-items/normalize', 'POST', 'http://127.0.0.1:18');
  emit('/api/unrelated');
  await Promise.all(trace.pending);
  assert.equal(trace.normalizations.length, 2);
  assert.deepEqual(trace.normalizations.map(row => row.payload.marker),
    ['/api/line-items/normalize', '/api/quote-sessions/pricing-selections']);
  assert.equal(trace.pageErrors.length, 0);
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
        result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
