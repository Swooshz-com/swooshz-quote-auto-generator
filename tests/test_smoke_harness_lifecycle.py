"""Prevent G3 startup failures from becoming successful, empty wrapper output."""
import json
import os
import subprocess
import unittest
from pathlib import Path
from tests import test_webapp as existing

ROOT = Path(__file__).resolve().parents[1]
DRIVER = r"""
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { spawn } from 'node:child_process';
import { EventEmitter, once } from 'node:events';
import { SyntheticBrowserBoundary } from './scripts/synthetic-browser-boundary.mjs';
const source = fs.readFileSync('scripts/playwright-smoke.mjs', 'utf8');
function extract(start, end) {
  assert.equal(source.split(start).length, 2);
  assert.equal(source.split(end).length, 2);
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}
const stopSource = extract('async function stopServer(serverInfo)', 'async function screenshot(page, name)');
const runSource = extract('async function runG3PricingScope()', 'async function main()');
const scope = { options: { keepServer: false, host: '127.0.0.1', port: 17 }, baseUrl: 'http://127.0.0.1:17', outputDir: 'prior-output', path, os, alpha1Scope: false, process: { env: {} }, root: 'synthetic-repo', freshBrowserRun: async () => 'fresh-evidence', SyntheticBrowserBoundary };
Object.defineProperty(scope, 'outputDir', { get: () => 'prior-output', set: () => { throw new Error('SYNTHETIC_OUTPUT_GLOBAL_MUTATION'); } });
vm.runInNewContext(stopSource + '\n' + runSource + '\nglobalThis.subjectStop = stopServer; globalThis.subjectRun = runG3PricingScope;', scope);
const timeout = setTimeout(() => { console.error('Synthetic lifecycle operation did not settle'); process.exit(1); }, 8000);
try {
  const mode = process.argv[1];
  if (mode === 'synchronous-exit') {
    const child = new EventEmitter();
    Object.assign(child, { exitCode: null, signalCode: null, killed: false });
    let kills = 0;
    child.kill = () => { kills++; child.killed = true; child.signalCode = 'SIGTERM'; child.emit('exit', null, 'SIGTERM'); return true; };
    await scope.subjectStop({ server: child });
    assert.equal(kills, 1);
  } else {
    const child = spawn(process.execPath, ['-e', 'process.exit(23)'], { windowsHide: true, stdio: 'ignore' });
    await once(child, 'exit');
    assert.equal(child.exitCode, 23);
    child.kill = () => { throw new Error('Already-exited child must not be signalled'); };
    if (mode === 'already-exited') {
      await scope.subjectStop({ server: child });
    } else if (mode === 'startup-failure') {
      let removals = 0;
      scope.fs = { mkdtemp: async () => 'synthetic-g3-root', mkdir: async () => {}, rm: async () => { removals++; } };
      scope.unusedLoopbackPort = async () => 18;
      scope.startServer = (options) => { assert.equal(options.logRoot, path.join('fresh-evidence', 'server')); return { server: child, output: ['SYNTHETIC_STARTUP_FAILURE'] }; };
      scope.waitForHealth = async () => false;
      await assert.rejects(scope.subjectRun(), /Could not start isolated G3 webapp.*SYNTHETIC_STARTUP_FAILURE/s);
      assert.equal(removals, 1);
      assert.equal(scope.baseUrl, 'http://127.0.0.1:17');
      assert.equal(scope.options.port, 17);
      assert.equal(scope.outputDir, 'prior-output');
    } else {
      throw new Error('Unknown lifecycle fixture mode');
    }
    assert.equal(child.exitCode, 23);
  }
  console.log(JSON.stringify({ status: 'PASS', mode }));
} finally {
  clearTimeout(timeout);
}
"""


class SmokeHarnessLifecycleTest(unittest.TestCase):
    def run_case(self, mode):
        env = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'COMSPEC', 'TEMP', 'TMP') if key in os.environ}
        result = subprocess.run([existing.require_node(self), '--input-type=module', '-e', DRIVER, mode],
                                cwd=ROOT, env=env, text=True, capture_output=True, encoding='utf-8', timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(json.loads(result.stdout), {'status': 'PASS', 'mode': mode})

    def test_already_exited_nonzero_child_is_not_signalled_or_awaited_again(self):
        self.run_case('already-exited')

    def test_exit_listener_precedes_a_synchronous_stop(self):
        self.run_case('synchronous-exit')

    def test_g3_startup_failure_survives_cleanup_and_rejects_the_wrapper(self):
        self.run_case('startup-failure')

    def test_g3_metadata_fixture_admits_only_the_declared_source_rows(self):
        from unittest import mock
        from webapp import ai_gateway, server
        from tests.g3_gateway_fixture import G3GatewayFixture
        from tests.gateway_test_support import synthetic_configuration
        values=synthetic_configuration(credential="synthetic-playwright-provider-key")
        values.update({"APP_MODE":"local","SQAG_DISABLE_DOTENV":"true"})
        for suffix in ai_gateway.ROUTE_KEYS.values():values["OPENROUTER_"+suffix+"_PROVIDER_ONLY"]='["synthetic-fixture"]'
        rows=[{"id":"synthetic-row","description":"Synthetic panel","unit_hint":"sqm","internal_cost":37}]
        body={"model":"synthetic/pricing_metadata","reasoning":{"effort":"high"},"input":[{"role":"user","content":[{"type":"input_text","text":server.build_pricing_catalog_metadata_prompt("synthetic",rows)}]}]}
        with mock.patch.dict(os.environ,values,clear=True),G3GatewayFixture() as fixture:
            gateway=ai_gateway.AiGateway(ai_gateway.GatewayConfiguration.capture(values.get),credential_reader=lambda:values["OPENROUTER_API_KEY"])
            response=gateway.request("pricing_metadata",body)
            result=json.loads(response["output"][0]["content"][0]["text"])
            self.assertEqual([row["id"] for row in result["items"]],["synthetic-row"])
            self.assertEqual(set(result["items"][0]),{"id","match_terms","object_families"})
            self.assertEqual(rows[0]["internal_cost"],37)
            self.assertEqual(fixture.metadata_requests,1)
            self.assertEqual(fixture.external_attempts,0)

    def test_g3_fixture_rejects_other_operations_and_external_network(self):
        import socket
        from unittest import mock
        from webapp import ai_gateway
        from tests.g3_gateway_fixture import G3GatewayFixture
        from tests.gateway_test_support import synthetic_configuration
        values=synthetic_configuration(credential="synthetic-playwright-provider-key")
        values.update({"APP_MODE":"local","SQAG_DISABLE_DOTENV":"true"})
        for suffix in ai_gateway.ROUTE_KEYS.values():values["OPENROUTER_"+suffix+"_PROVIDER_ONLY"]='["synthetic-fixture"]'
        with mock.patch.dict(os.environ,values,clear=True):
            for operation in ai_gateway.ROUTE_KEYS:
                if operation=="pricing_metadata":continue
                with self.subTest(operation=operation),G3GatewayFixture() as fixture:
                    gateway=ai_gateway.AiGateway(ai_gateway.GatewayConfiguration.capture(values.get),credential_reader=lambda:values["OPENROUTER_API_KEY"])
                    route=gateway.configuration.route(operation)
                    with self.assertRaises(AssertionError):gateway.request(operation,{"model":route.model,"reasoning":{"effort":route.effort},"input":[{"role":"user","content":[{"type":"input_text","text":"synthetic"}]}]})
                    self.assertEqual(fixture.metadata_requests,0)
            for mode in ("dns","socket"):
                with self.subTest(mode=mode),self.assertRaises(AssertionError):
                    with G3GatewayFixture():
                        if mode=="dns":socket.getaddrinfo("external.invalid",443)
                        else:
                            with socket.socket() as connection:connection.connect(("192.0.2.1",443))
