"""Browser fixture authority controls without a browser or provider connection."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BrowserSmokeAuthorityTest(unittest.TestCase):
    def test_alpha_invokes_continuity_on_actual_before_and_after_results(self):
        script = r'''
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {browserAuthority, assertBrowserAuthorityUnchanged} from './scripts/internal-password-browser-test.mjs';
const source = fs.readFileSync('scripts/playwright-alpha-smoke.mjs', 'utf8');
const before = source.slice(source.indexOf('    const profileData ='), source.indexOf('    const selectedReference ='));
const after = source.slice(source.indexOf('    const authSession ='), source.indexOf('    const beforeSeed ='));
// Execute actual fetch/snapshot/comparison statements. Workspace fixture checks
// are separately retained in alpha; exclude them here to isolate continuity.
const statements = (before + after).split('\n').filter(line => !line.trim().startsWith('assert(')).join('\n');
const session = {authenticated: true, user: null, browser_recovery_scope: 'scope-one', permissions: {canGenerateQuote: true}};
const profiles = {workspace: {workspace: {id: 'workspace-one'}}};
async function exercise(postSession, postProfiles) {
  let profileReads = 0, sessionReads = 0;
  const scope = {browserAuthority, assertBrowserAuthorityUnchanged, authState: {body: session}, baseUrl: 'http://127.0.0.1',
    getJson: async value => value, api: {get: async url => {
      if (url.endsWith('/api/session')) {sessionReads++; return postSession;}
      profileReads++; return profileReads === 1 ? profiles : postProfiles;
    }}};
  await vm.runInNewContext('(async () => {' + statements + '})()', scope);
  assert.equal(profileReads, 2); assert.equal(sessionReads, 1);
}
await exercise(session, profiles);
await assert.rejects(exercise({...session, browser_recovery_scope: 'scope-two'}, profiles), /authority changed/);
await assert.rejects(exercise(session, {workspace: {workspace: {id: 'workspace-two'}}}), /authority changed/);
await assert.rejects(exercise({...session, permissions: {canGenerateQuote: false}}, profiles), /authority is incomplete/);
'''
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_public_auth_snapshot_preserves_scope_and_workspace(self):
        script = r'''
import assert from "node:assert/strict";
import { browserAuthority, assertBrowserAuthorityUnchanged } from "./scripts/internal-password-browser-test.mjs";
const session = { authenticated: true, user: null, browser_recovery_scope: "opaque-synthetic-scope", permissions: {canGenerateQuote: true} };
for (const workspace of ["workspace-one", "workspace-two"]) {
  const profiles = {workspace: {workspace: {id: workspace}}};
  const before = browserAuthority(session, profiles);
  assert.deepEqual(before, {scope: session.browser_recovery_scope, workspace});
  assert.doesNotThrow(() => assertBrowserAuthorityUnchanged(before, browserAuthority({...session}, profiles)));
  assert.throws(() => assertBrowserAuthorityUnchanged(before, browserAuthority({...session, browser_recovery_scope: "other-scope"}, profiles)));
  assert.throws(() => assertBrowserAuthorityUnchanged(before, browserAuthority(session, {workspace: {workspace: {id: "other-workspace"}}})));
  for (const bad of [{...session, authenticated: false}, {...session, user: {account: workspace}}, {...session, browser_recovery_scope: ""}, {...session, permissions: {canGenerateQuote: false}}]) {
    assert.throws(() => browserAuthority(bad, profiles));
  }
  for (const bad of [null, {}, {workspace: {workspace: {id: ""}}}, {workspace: {workspace: {id: "bad/workspace"}}}]) {
    assert.throws(() => browserAuthority(session, bad));
  }
}
assert.throws(() => assertBrowserAuthorityUnchanged({}, {}));
'''
        result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
