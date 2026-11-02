"""Synthetic browser egress admission and fresh evidence ownership contracts."""
import json
import os
from pathlib import Path
import subprocess
import unittest

from tests import test_webapp as fixtures
from tests.rendered_browser_support import owned_browser_evidence_directory

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { SyntheticBrowserBoundary, freshBrowserRun } from './scripts/synthetic-browser-boundary.mjs';
const mode = process.argv[1];
if (mode === 'contexts') {
  for (const invalid of ['https://127.0.0.1:123', 'http://external.invalid', 'http://127.0.0.1:123/path', 'http://user@127.0.0.1:123']) {
    assert.throws(() => new SyntheticBrowserBoundary(invalid), /owned loopback origin/);
  }
  for (const destination of ['http://127.0.0.1:123/owned', 'http://127.0.0.1:124/other', 'http://external.invalid/remote', 'https://127.0.0.1:123/other', 'http://user@127.0.0.1:123/private', 'data:text/plain,external']) {
    const boundary = new SyntheticBrowserBoundary('http://127.0.0.1:123');
    let requestRoute, socketRoute, options, fallbacks = 0, aborts = 0;
    const context = { route: async (_pattern, handler) => { requestRoute = handler; }, routeWebSocket: async (_pattern, handler) => { socketRoute = handler; } };
    const browser = { newContext: async (supplied) => { options = supplied; return context; } };
    assert.equal(await boundary.newContext(browser, { acceptDownloads: true, serviceWorkers: 'allow' }), context);
    assert.deepEqual(options, { acceptDownloads: true, serviceWorkers: 'block' });
    await requestRoute({ request: () => ({ url: () => destination }), fallback: async () => { fallbacks++; }, abort: async (reason) => { assert.equal(reason, 'blockedbyclient'); aborts++; } });
    if (destination === 'http://127.0.0.1:123/owned') {
      assert.equal(fallbacks, 1); assert.equal(aborts, 0); boundary.assertClean();
    } else {
      assert.equal(fallbacks, 0); assert.equal(aborts, 1); assert.throws(() => boundary.assertClean(), /outside its owned origin/);
    }
    let closed = 0;
    socketRoute({ close: (reason) => { closed++; assert.equal(reason.code, 1008); } });
    assert.equal(closed, 1); assert.throws(() => boundary.assertClean(), /outside its owned origin/);
  }
} else if (mode === 'api') {
  for (const destination of ['http://127.0.0.1:123/owned', 'http://127.0.0.1:124/other', 'https://external.invalid/download']) {
    const boundary = new SyntheticBrowserBoundary('http://127.0.0.1:123');
    let calls = 0;
    const api = { get: async (url, options) => { calls++; assert.equal(url, destination); assert.deepEqual(options, { maxRedirects: 0 }); return { status: () => 200 }; } };
    if (destination.endsWith('/owned')) { await boundary.get(api, destination); assert.equal(calls, 1); }
    else { await assert.rejects(boundary.get(api, destination), /outside its owned origin/); assert.equal(calls, 0); }
  }
  const boundary = new SyntheticBrowserBoundary('http://127.0.0.1:123');
  for (const status of [301, 302, 303, 307, 308]) {
    await assert.rejects(boundary.get({ get: async (_url, options) => { assert.equal(options.maxRedirects, 0); return { status: () => status }; } }, 'http://127.0.0.1:123/redirect'), /redirects are forbidden/);
  }
} else if (mode === 'files') {
  const parent = await fs.mkdtemp(path.join(os.tmpdir(), 'boundary-'));
  try {
    const prior = path.join(parent, 'prior');
    await fs.mkdir(prior);
    await fs.writeFile(path.join(prior, 'sentinel'), 'preserved', { flag: 'wx' });
    const first = await freshBrowserRun(parent, 'run-');
    const second = await freshBrowserRun(parent, 'run-');
    assert.notEqual(first, second);
    assert.equal(path.dirname(first), parent);
    assert.deepEqual(await fs.readdir(first), []);
    assert.equal(await fs.readFile(path.join(prior, 'sentinel'), 'utf8'), 'preserved');
  } finally { await fs.rm(parent, { recursive: true, force: true }); }
} else { throw new Error('Unknown boundary fixture mode'); }
console.log(JSON.stringify({ status: 'PASS', mode }));
"""


class RenderedBrowserBoundaryTest(unittest.TestCase):
    def run_case(self, mode):
        env = {name: os.environ[name] for name in ('PATH', 'SystemRoot', 'COMSPEC', 'TEMP', 'TMP') if name in os.environ}
        result = subprocess.run([fixtures.require_node(self), '--input-type=module', '-e', DRIVER, mode],
                                cwd=ROOT, env=env, text=True, capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout), {'status': 'PASS', 'mode': mode})

    def test_all_contexts_block_foreign_origins_workers_and_websockets(self):
        self.run_case('contexts')

    def test_api_requests_refuse_foreign_exports_and_never_follow_redirects(self):
        self.run_case('api')

    def test_fresh_runs_cannot_overwrite_existing_evidence(self):
        self.run_case('files')

    def test_python_evidence_contexts_remain_distinct_and_preserve_prior_bytes(self):
        with owned_browser_evidence_directory() as first:
            target = Path(first) / 'sentinel.txt'
            target.write_text('preserved', encoding='utf-8')
        with owned_browser_evidence_directory() as second:
            self.assertNotEqual(first, second)
            self.assertEqual(list(Path(second).iterdir()), [])
            self.assertEqual(target.read_text(encoding='utf-8'), 'preserved')
