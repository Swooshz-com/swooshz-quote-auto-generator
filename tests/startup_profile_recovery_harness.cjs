// Production startup, with bounded synthetic prerequisite/persistence adapters.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('webapp/static/app.js', 'utf8');
const boot = source.slice(source.lastIndexOf('async function boot() {'), source.lastIndexOf('\nboot();'));
assert.ok(boot.startsWith('async function boot() {'));

async function check(mode) {
  const calls = [];
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const c = {
    state: { isBooting: true, activeAppView: 'quote', activeJob: null },
    setDashboardLoadingState: (busy, text) => calls.push(['loading', busy, text]),
    wireEvents() {}, clearLegacyLocalCompanyPresets() {},
    initializeSession: async () => mode !== 'session-failed',
    restoredWorkspaceViewState: () => null,
    authenticationRecoveryMarkerPresent: () => false,
    restoredDashboardOperation: () => null,
    loadProfiles: async () => {
      calls.push(['profiles']);
      if (mode === 'throw') throw new Error('synthetic prerequisite failure');
      if (mode === 'pending') return pending;
      return mode === 'ready';
    },
    setInitialValues: async () => { calls.push(['restore']); return true; },
    applyWorkspaceViewState() {}, showQuoteFlow: () => calls.push(['quote']),
    showDashboard: () => calls.push(['dashboard']), checkHealth() {},
    syncControlStates: () => calls.push(['persist']),
    finishAuthenticationRecovery: () => calls.push(['auth-finish']),
    resumeSavedJob: async () => calls.push(['resume-job']),
    resumeDashboardOperation: async () => calls.push(['resume-dashboard']),
    loadQuoteDashboard: async () => calls.push(['load-dashboard']),
    restoreRestorableOverlay: () => calls.push(['overlay']),
  };
  vm.createContext(c);
  vm.runInContext(boot, c);
  const result = c.boot();
  if (mode === 'pending') {
    await new Promise(setImmediate);
    assert.ok(c.state.isBooting, 'pending prerequisite released the write fence');
    assert.deepStrictEqual(calls.filter(x => ['restore','persist','resume-job','resume-dashboard'].includes(x[0])), []);
    release(false);
  }
  await result;
  if (mode === 'ready') {
    assert.strictEqual(c.state.isBooting, false);
    for (const event of ['restore','persist','resume-job','auth-finish','overlay']) {
      assert.strictEqual(calls.filter(x => x[0] === event).length, 1, event);
    }
  } else {
    assert.deepStrictEqual(calls.filter(x => ['restore','persist','resume-job','resume-dashboard','auth-finish','overlay'].includes(x[0])), [], mode);
    if (mode !== 'session-failed') {
      assert.ok(c.state.isBooting, 'failed prerequisite released the write fence');
      assert.ok(calls.some(x => x[0] === 'loading' && x[2]?.title === 'Workspace unavailable'));
    }
  }
}
(async () => {
  for (const mode of ['session-failed','false','throw','pending','ready']) await check(mode);
  process.stdout.write(JSON.stringify({status:'PASS', cases:5})+'\n');
})().catch(e => { console.error(e); process.exitCode=1; });
