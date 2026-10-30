// Production startup, with bounded synthetic prerequisite/persistence adapters.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('webapp/static/app.js', 'utf8');
const boot = source.slice(source.lastIndexOf('async function boot() {'), source.lastIndexOf('\nboot();'));
const snapshotParser = source.slice(source.indexOf('function quoteSessionSnapshotForRestoration('), source.indexOf('async function applyQuoteSessionSnapshot('));
const readiness = snapshotParser + source.slice(source.indexOf('function startupPricingReferenceIsAvailable() {'), source.lastIndexOf('async function boot() {'));
function extract(name) {
  const start = source.indexOf('function ' + name + '(');
  assert.ok(start >= 0, name);
  const body = source.indexOf(') {', start) + 2;
  let depth = 0;
  for (let i = body; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
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
      'quoteCommercialStrictDataEqual','isPlainObject'].map(extract).join('\n');
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
  process.stdout.write(JSON.stringify({status:'PASS', cases:5, referenceCases:cases.length, contentCases:contentCases.length})+'\n');
})().catch(e => { console.error(e); process.exitCode=1; });
