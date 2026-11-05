"""Pin invocation sites as well as guard bodies, with executable negative controls."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SmokeWiringTest(unittest.TestCase):
    def run_node(self, script, source):
        result = subprocess.run(['node', '-e', script], input=source, cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_live_terminal_mode_runs_each_case_with_its_own_g3_lifecycle(self):
        source = (ROOT/'scripts/playwright-smoke.mjs').read_text(encoding='utf-8')
        self.run_node(r'''
const assert = require('node:assert/strict'), vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('async function main()');
const end = source.indexOf('  let serverInfo = null;', start);
assert(start >= 0 && end > start);
const body = source.slice(start, end) + '}\nglobalThis.subject = main;';
const statuses = ['needs_review', 'blocked', 'failed', 'interrupted'];
async function check(failAt) {
  const calls = [], options = {};
  const scope = {args: ['--terminal-only'], options, runG3PricingScope: async () => {
    calls.push(options.terminalStatus);
    if (options.terminalStatus === failAt) throw new Error('Synthetic terminal failure');
  }};
  vm.runInNewContext(body, scope);
  if (failAt) {
    await assert.rejects(scope.subject(), /Synthetic terminal failure/);
    assert.deepEqual(calls, statuses.slice(0, statuses.indexOf(failAt) + 1));
  } else {
    await scope.subject(); assert.deepEqual(calls, statuses);
  }
}
(async () => {await check(); for (const status of statuses) await check(status);})()
  .catch(error => {console.error(error); process.exitCode = 1;});
''', source)

    def test_recovery_mode_calls_expired_job_and_customer_next_checks(self):
        source = (ROOT/'scripts/playwright-smoke.mjs').read_text(encoding='utf-8')
        self.run_node(r'''
const assert = require('node:assert/strict'), vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
const start = source.indexOf('    if (args.includes("--recovery-only"))');
const end = source.indexOf('    await verifyBrowserRecoveryScopeIsolation(page);', start);
assert(start >= 0 && end > start);
const body = source.slice(start, end);
const names = ['verifyConfirmBasisSurvivesImmediateRefresh', 'verifyGenerationLoadingModalSurvivesRefresh',
  'verifyGenerationTerminalRecoveryAfterRefresh', 'verifyExpiredQuoteJobsDoNotResume',
  'verifyPricingReferenceSelectionCommitsOnCustomerNext'];
async function check(code) {
  const calls = [], locator = {waitFor: async () => {}};
  const context = {args: ['--recovery-only'], baseUrl: 'http://127.0.0.1:17',
    page: {goto: async () => {}, getByRole: () => locator, locator: () => locator},
    networkProblems: [], consoleProblems: [], console: {log: () => {}},
    requireNoQuoteSessionRateLimits: () => calls.push('rate-limit')};
  for (const name of names) context[name] = async () => calls.push(name);
  await vm.runInNewContext('(async () => {' + code + '})()', context);
  assert.deepEqual(calls, [...names, 'rate-limit']);
}
(async () => {
  await check(body);
  for (const name of names.slice(-2)) {
    const call = 'await ' + name + '(page);'; assert(body.includes(call));
    await assert.rejects(check(body.replace(call, '')), /deep-equal/);
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
''', source)

    def test_all_protected_upload_and_checkpoint_calls_are_retained(self):
        source = (ROOT/'scripts/playwright-alpha-smoke.mjs').read_text(encoding='utf-8')
        expected = ['requireCustomerCheckpoint(beforeSeed, customerCheckpoint);',
                    'requireOriginalUploadBytes(beforeSeed, originalUploads);',
                    'requireOriginalUploadBytes(durableAfterEdit, originalUploads);',
                    'requireOriginalUploadBytes(xlsxReadback, originalUploads);',
                    'requireOriginalUploadBytes(postGenerationState, originalUploads);']
        def check(text):
            calls = re.findall(r'^\s*(require(?:CustomerCheckpoint|OriginalUploadBytes)\([^\n]+;)', text, re.M)
            self.assertEqual(calls, expected)
        check(source)
        for call in expected:
            with self.subTest(call=call), self.assertRaises(AssertionError):
                check(source.replace(call, '', 1))

    def test_workbook_verifier_nonzero_exit_is_fatal_before_and_after_pdf(self):
        source = (ROOT/'scripts/playwright-alpha-smoke.mjs').read_text(encoding='utf-8')
        self.run_node(r'''
const assert = require('node:assert/strict'), vm = require('node:vm');
const source = require('node:fs').readFileSync(0, 'utf8');
async function exercise(body, variable) {
  for (const code of [0, 1, null]) {
    let called = 0;
    const scope = {python: 'synthetic-python', xlsxPath: 'before.xlsx', currentXlsxPath: 'after.xlsx',
      workbookExpectationsPath: 'expected.json', root: 'synthetic',
      safeErrorText: () => 'synthetic failure', assert: (ok, message) => {if (!ok) throw new Error(message);},
      spawnCollect: async (binary, args) => {called++; assert.equal(args[0], 'scripts/verify_current_workbook.py');
        return {code, stdout: '{}', stderr: 'synthetic failure'};}};
    const promise = vm.runInNewContext('(async () => {' + body + '})()', scope);
    if (code === 0) await promise;
    else await assert.rejects(promise, /workbook verification failed/);
    assert.equal(called, 1);
  }
}
(async () => {
  for (const variable of ['strictXlsx', 'strictCurrentXlsx']) {
    const start = source.indexOf('    const ' + variable + ' = await spawnCollect(');
    const end = source.indexOf('\n    const ', start + 10);
    assert(start >= 0 && end > start);
    const body = source.slice(start, end);
    await exercise(body, variable);
    assert(body.includes(variable + '.code === 0'));
    await assert.rejects(exercise(body.replace(variable + '.code === 0', 'true'), variable));
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
''', source)

    def test_rate_limit_observation_covers_other_tabs_and_popups(self):
        source = (ROOT/'scripts/playwright-smoke.mjs').read_text(encoding='utf-8')
        self.run_node(r'''
const assert = require('node:assert/strict'), vm = require('node:vm'), {EventEmitter} = require('node:events');
const source = require('node:fs').readFileSync(0, 'utf8');
const main = source.slice(source.indexOf('async function main()'));
const start = main.indexOf('context.on("response", (response) => {');
const end = main.indexOf('\n  });', start);
assert(start >= 0 && end > start, 'Responses must be observed on the whole browser context');
const guardStart = source.indexOf('function requireNoQuoteSessionRateLimits(');
const guardEnd = source.indexOf('async function main()', guardStart);
const context = new EventEmitter(), networkProblems = [];
const scope = {context, networkProblems};
vm.runInNewContext(main.slice(start, end + 6) + '\n' + source.slice(guardStart, guardEnd)
  + '\nglobalThis.guard = requireNoQuoteSessionRateLimits;', scope);
for (const tab of ['main', 'other-tab', 'popup']) {
  networkProblems.length = 0;
  context.emit('response', {status: () => 429, url: () => 'http://127.0.0.1/api/quote-sessions?tab=' + tab});
  assert.throws(() => scope.guard(networkProblems), /rate limited/);
}
''', source)
