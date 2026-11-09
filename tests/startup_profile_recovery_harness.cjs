// Production startup, with bounded synthetic prerequisite/persistence adapters.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('webapp/static/app.js', 'utf8');
const boot = source.slice(source.lastIndexOf('async function boot() {'), source.lastIndexOf('\nboot();'));
const snapshotParser = source.slice(source.indexOf('function quoteSessionSnapshotForRestoration('), source.indexOf('async function applyQuoteSessionSnapshot('));
const readiness = snapshotParser + source.slice(source.indexOf('function startupPricingReferenceIsAvailable() {'), source.lastIndexOf('async function boot() {'));
function extract(name, text = source) {
  let start = text.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name);
  if (text.slice(start - 6, start) === 'async ') start -= 6;
  const body = text.indexOf(') {', start) + 2;
  let depth = 0;
  for (let i = body; i < text.length; i++) {
    if (text[i] === '{') depth++;
    if (text[i] === '}' && --depth === 0) return text.slice(start, i + 1);
  }
  throw Error('Unclosed ' + name);
}

assert.ok(boot.startsWith('async function boot() {'));

async function check(mode) {
  const calls = [];
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const c = {
    state: { isBooting: true, activeAppView: 'quote', activeJob: null },
    safeSessionJson: () => null,
    isPlainObject: value => value && typeof value === 'object' && !Array.isArray(value),
    QUOTE_SESSION_STATE_VERSION: 1,
    currentBrowserRecoveryScope: () => 'current',
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
  vm.runInContext(readiness + boot, c);
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
// Run the maintained driver's actual baseline prefix with the production save
// owner and explicitly released acknowledgements, without browser timing.
async function checkSaveCompletionBoundary() {
  const driver = fs.readFileSync('scripts/playwright-owner-uat-basis-repair.mjs', 'utf8');
  const loop = '      for (const fault of profileRecoveryFaults) {';
  const begin = driver.indexOf(loop) + loop.length;
  const end = driver.indexOf('        const metadataBeforeOutage =', begin);
  assert.ok(begin >= loop.length && end > begin);
  const boundary = driver.slice(begin, end);
  const completion = /^        await finishPendingQuoteSaves\(page, targetId\);\r?\n/m;
  assert.strictEqual(boundary.split(completion).length, 2);
  const priorBoundary = boundary.replace(completion, '');
  const helper = extract('finishPendingQuoteSaves', driver);
  const owner = ['clearQuoteSessionDraftSaveTimer', 'ensureQuoteSession', 'saveQuoteSessionDraftState'].map(name => extract(name)).join('\n');
  let cases = 0;
  const turn = () => new Promise(setImmediate);
  async function run(mode, corrected = true) {
    const storage = new Map(), requests = [], pending = [];
    let revision = 0, confirmed = mode === 'timer-confirmed', captured = null, cleared = 0;
    const c = {
      state: { quoteSessionId: 'synthetic-session', authenticated: true, isBooting: false,
        isSigningOut: false, authenticationRecoveryRequested: false, isRecoveryScopeTransitioning: false,
        basisConfirmed: true, outputRows: [] },
      targetId: 'synthetic-session', scope: 'synthetic-scope', fault: 'unavailable',
      QUOTE_SESSION_STORAGE_KEY: 'quote', WORKSPACE_VIEW_STORAGE_KEY: 'workspace',
      performance: { now: () => 0 }, window: { clearTimeout: () => { cleared++; } },
      localStorage: { getItem: key => storage.get(key) || null },
      currentBrowserRecoveryScope: () => c.scope,
      commitActiveOutputEditor: () => null, quoteSessionDraftStateCanSave: () => true,
      currentQuoteSessionDraftState: () => ({ rows: ['synthetic-row'] }), sessionFileRecordsFromDraft: () => [],
      quoteSessionDraftComparisonKey: () => 'draft', quoteSessionDraftFileComparisonKey: () => 'files',
      safeQuoteSessionId: value => value || '', ensureClientQuoteSessionId: () => c.state.quoteSessionId,
      quoteSessionDraftIsConfirmed: () => confirmed, quoteSessionDraftFilesAreConfirmed: () => confirmed,
      saveSessionState: () => {
        storage.set('quote', JSON.stringify({ savedAt: ++revision, rows: ['synthetic-row'] }));
        storage.set('workspace', JSON.stringify({ savedAt: revision, menu: 'output' }));
      },
      saveCurrentQuoteSession: () => {
        requests.push(true);
        return new Promise(resolve => pending.push(success => {
          confirmed = success;
          resolve(success ? { session_id: c.state.quoteSessionId } : null);
        }));
      },
    };
    c.saveSessionState();
    c.page = { evaluate: async (callback, argument) => {
      const result = await callback(argument);
      if (result && Object.prototype.hasOwnProperty.call(result, 'quote') && Object.prototype.hasOwnProperty.call(result, 'workspace')) captured = result;
      return result;
    } };
    vm.createContext(c);
    vm.runInContext('let quoteSessionDraftSaveTimer = null, quoteSessionDraftSavePromise = null;\n' + owner + '\n' + helper, c);
    const saves = [];
    if (['pending', 'queued', 'failed', 'scope-changed'].includes(mode)) {
      saves.push(vm.runInContext('saveQuoteSessionDraftState({quoteGenerated:true})', c));
      if (mode === 'queued') saves.push(vm.runInContext('saveQuoteSessionDraftState({quoteGenerated:true})', c));
    }
    if (mode.startsWith('timer-')) vm.runInContext('quoteSessionDraftSaveTimer = { kind: "timer", id: 7, options: {} };', c);
    if (mode === 'wrong-target') c.state.quoteSessionId = 'different-session';
    const completionResult = vm.runInContext('(async () => {' + (corrected ? boundary : priorBoundary) + '\n return storageBeforeOutage; })()', c)
      .then(value => ({ value }), error => ({ error }));
    await turn();
    const beforeRelease = captured !== null;
    if (['pending', 'queued', 'failed', 'scope-changed', 'timer-pending'].includes(mode)) {
      assert.strictEqual(pending.length, 1, mode + ': controlled acknowledgement not pending');
      if (mode === 'scope-changed') c.scope = 'changed-synthetic-scope';
      pending.shift()(mode !== 'failed');
    }
    const outcome = await completionResult;
    await Promise.all(saves);
    if (['failed', 'scope-changed', 'wrong-target'].includes(mode)) {
      assert.ok(outcome.error, mode + ': invalid completion admitted a baseline');
      assert.strictEqual(captured, null, mode + ': captured a baseline after failure/context change');
    } else {
      assert.ok(!outcome.error, mode);
      assert.ok(captured, mode + ': baseline missing');
      const unchanged = captured.quote === storage.get('quote') && captured.workspace === storage.get('workspace');
      assert.strictEqual(unchanged, corrected, mode + ': save completion/baseline ordering');
      if (mode === 'pending' || mode === 'queued' || mode === 'timer-pending') assert.strictEqual(beforeRelease, !corrected, mode + ': baseline did not wait for owner');
      assert.strictEqual(vm.runInContext('quoteSessionDraftSavePromise === null && quoteSessionDraftSaveTimer === null', c), true);
      if (mode.startsWith('timer-')) assert.strictEqual(cleared, 1, mode);
      if (mode === 'settled' || mode === 'timer-confirmed') assert.strictEqual(requests.length, 0, mode + ': unnecessary save');
    }
    cases++;
  }
  await run('pending', false); // Former prefix observes the old owner's savedAt rewrite after baseline.
  for (const mode of ['settled', 'pending', 'timer-pending', 'timer-confirmed', 'queued', 'failed', 'scope-changed', 'wrong-target']) await run(mode);
  return cases;
}

(async () => {
  for (const mode of ['session-failed','false','throw','pending','ready']) await check(mode);
  const reference = {id:'reference-a',source:'local',currency:'SGD',digest_sha256:'sha256:'+'a'.repeat(64)};
  const saved = {version:1,browserRecoveryScope:'current',pricingReferenceId:'reference-a',pricingReferenceSource:'local',outputRows:[{source_basis_line_id:'row-a'}]};
  const cases = [
    [saved, [reference], true], [saved, [], false],
    [saved, [{}], false], [saved, [null], false],
    [saved, [{...reference,id:'other'}], false],
    [saved, [{...reference,source:'company'}], false],
    [saved, [reference,reference], false],
    [{...saved,pricingReferenceId:''}, [reference], false],
    [{...saved,browserRecoveryScope:'other'}, [], true],
    [{...saved,version:2}, [], true],
    [{...saved,outputRows:[]}, [], true], [null, [], true],
    [{...saved,outputRows:[],originalOutputRows:[{}]}, [], false],
  ];
  for (const [snapshot,references,expected] of cases) {
    const c = {state:{pricingReferences:references},safeSessionJson:()=>snapshot,QUOTE_SESSION_STATE_VERSION:1,currentBrowserRecoveryScope:()=> 'current',
      isPlainObject:v=>v && typeof v==='object' && !Array.isArray(v),
      pricingReferenceAuthorityBasis:r=>r && r.id && r.source && r.currency && r.digest_sha256 ? r : null};
    const before=JSON.stringify({snapshot,references});vm.createContext(c);vm.runInContext(readiness,c);
    assert.strictEqual(c.startupPricingReferenceIsAvailable(),expected);
    assert.strictEqual(JSON.stringify({snapshot,references}),before);
  }
  const authorityCode = source.slice(source.indexOf('const PRICING_REFERENCE_SOURCES ='), source.indexOf('const QUOTE_COMMERCIAL_REVIEW_SCHEMA ='))
    + ['canonicalPricingAuthorityText','canonicalPricingAuthorityUnit','pricingAuthorityVersionIsValid',
      'pricingAuthorityContext','pricingAuthorityContextMatches','pricingAuthorityNumber','pricingReferenceAuthorityBasis',
      'pricingReferenceCatalogItem','pricingAuthorityCatalogDescription','normalizePricingAuthority',
      'pricingAuthorityPresentationRow','normalizeOutputPricingAuthority','bracketedCatalogReferenceParts',
      'quoteCommercialStrictDataEqual','isPlainObject'].map(name => extract(name)).join('\n');
  const item = {id:'synthetic-item',section:'Synthetic',description:'Synthetic panel',unit_hint:'sqm',sale_unit_price:9.5};
  const catalogReference = {...reference,authority_items:[item]};
  const row = {source_basis_line_id:'row-a',section:item.section,description:item.description,unit:'sqm',pricing_keyword:item.id};
  row.pricing_authority = {schema:'swooshz.pricing-authority.v1',version:1,variant:'catalog',context:{...row},currency:'SGD',price:9.5,
    catalog_item_id:item.id,catalog_source:reference.source,catalog_digest:reference.digest_sha256,
    catalog_section:item.section,catalog_description:item.description,catalog_unit:'sqm'};
  const contentCases = [];
  const faults = [[],[null],[{...item,id:'missing'}],[item,item],
    [{...item,sale_unit_price:'invalid'}],[{...item,sale_unit_price:-1}],
    [{...item,sale_unit_price:10.5}],[{...item,unit_hint:'lot'}],
    [{...item,description:'Different synthetic panel'}],[{...item,section:'Other synthetic section'}]];
  for (const field of ['lineItems','outputRows','originalOutputRows']) {
    const snapshot = {...saved,outputRows:[],[field]:[row]};
    contentCases.push([snapshot,[catalogReference],true]);
    for (const items of faults) contentCases.push([snapshot,[{...catalogReference,authority_items:items}],false]);
    for (const value of [null,{},'invalid']) contentCases.push([snapshot,[{...catalogReference,authority_items:value}],false]);
  }
  const priced = {...saved,outputRows:[row]};
  contentCases.push(
    [priced,[{...catalogReference,authority_items:undefined,items:[item]}],true],
    [priced,[{...catalogReference,authority_items:[null,item]}],true],
    [priced,[{...catalogReference,digest_sha256:'sha256:'+'b'.repeat(64),authority_items:[]}],true],
    [priced,[{...catalogReference,currency:'USD',authority_items:[]}],true],
    [{...saved,outputRows:[{...row,pricing_authority:{variant:'manual'}}]},[{...catalogReference,authority_items:[]}],true],
    [{version:1,browserRecoveryScope:'current',draft_state:{pricingReferenceId:reference.id,pricingReferenceSource:reference.source,outputRows:[row]}},[catalogReference],true],
    [{version:1,browserRecoveryScope:'current',quote_session:{draft_state:{pricingReferenceId:reference.id,pricingReferenceSource:reference.source,outputRows:[row]}}},[{...catalogReference,authority_items:[]}],false]
  );
  for (const [snapshot,references,expected] of contentCases) {
    const c = {state:{pricingReferences:references},safeSessionJson:()=>snapshot,QUOTE_SESSION_STATE_VERSION:1,currentBrowserRecoveryScope:()=> 'current',
      roundCommercialCents:value=>Math.round(value*100)/100,
      snapshotText:JSON.stringify(snapshot),referenceText:JSON.stringify(references)};
    const before=JSON.stringify({snapshot,references});vm.createContext(c);vm.runInContext(authorityCode+readiness,c);
    // Production plain-object checks must see objects from the same VM realm.
    vm.runInContext('const snapshot = JSON.parse(snapshotText); state.pricingReferences = JSON.parse(referenceText); safeSessionJson = () => snapshot;',c);
    assert.strictEqual(c.startupPricingReferenceIsAvailable(),expected,JSON.stringify({snapshot,references}));
    assert.strictEqual(vm.runInContext('JSON.stringify({snapshot,references:state.pricingReferences})',c),before);
    assert.strictEqual(JSON.stringify({snapshot,references}),before);
  }
  const completionCases = await checkSaveCompletionBoundary();
  process.stdout.write(JSON.stringify({status:'PASS', cases:5, referenceCases:cases.length, contentCases:contentCases.length, completionCases, baseCompletionRed:true, headCompletionGreen:true})+'\n');
})().catch(e => { console.error(e); process.exitCode=1; });
