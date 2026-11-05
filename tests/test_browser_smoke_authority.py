"""Browser fixture authority controls without a browser or provider connection."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BrowserSmokeAuthorityTest(unittest.TestCase):
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
