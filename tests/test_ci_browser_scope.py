"""Keep the CI browser commands on the maintained full paths."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CiBrowserScopeTest(unittest.TestCase):
    def test_all_four_smokes_remain_gated_by_runtime_validation(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertNotIn("alpha1_prerequisites", workflow)
        self.assertEqual(workflow.count("id: runtime_prerequisites"), 1)
        gate = "        if: always() && steps.runtime_prerequisites.outcome == 'success'\n"
        for command in ("ai-stress", "internal-auth", "smoke", "alpha-smoke"):
            self.assertIn(gate + f"        run: npm run playwright:{command}\n", workflow)
        self.assertNotIn("--alpha1", workflow)

    def test_python_restoration_exclusion_is_retained_without_other_scope_changes(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("SQAG_VALIDATION_SCOPE: alpha1\n        run: python -m unittest discover -s tests", workflow)
        tests = (ROOT / "tests/test_webapp.py").read_text(encoding="utf-8")
        self.assertEqual(tests.count('os.environ.get("SQAG_VALIDATION_SCOPE")'), 1)

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
