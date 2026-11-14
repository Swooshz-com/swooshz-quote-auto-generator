const fs = require('fs');
const assert = require('assert');
const source = fs.readFileSync('webapp/static/app.js', 'utf8');
const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
function extract(name) {
  let start = source.indexOf('function ' + name + '(');
  if (source.slice(start-6,start)==='async ') start-=6;
  if (start < 0) throw new Error('Missing production function ' + name);
  const body = source.indexOf(') {', start) + 2;
  let depth = 0;
  for (let i = body; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error('Unclosed production function ' + name);
}
const state = { outputRows: [], lineItems: [], quoteBasisSections: [], quoteCommercialLifecycle:'EXISTING', dashboardSearch:'restored-hidden-search',quoteSessions:[{}],dashboardStatusFilter:'all',dashboardDateFilter:'all' };
let activeOutputEditor=null;
let blockedOutputDraftSaveOptions=null;
const outputRowIdentities=new WeakMap();
const nodes = new Map();
function node() { return {value:'',textContent:'',hidden:false,querySelector(){return null;},querySelectorAll(){return [];},classList:{toggle(){},add(){},remove(){}}}; }
const elements = new Proxy({}, { get(_t,k) {if(!nodes.has(k))nodes.set(k,node());return nodes.get(k);} });
const document = { getElementById(id) {
  if (!nodes.has(id)) nodes.set(id, node());
  return nodes.get(id);
} };
function noop() {}
const markOutputRowsDirty=noop,renderOutputValidationMessages=noop,renderPricingMatches=noop,renderMatchSummary=noop,syncControlStates=noop,queueQuoteSessionDraftStateSave=noop,updateQuoteBasisCard=noop,setDownloadFiles=noop;
const resetOutputSortModeToPricingReference=noop,updateDashboardSummary=noop,ensureDashboardCustomDateDefaults=noop,syncDashboardCustomDateRangeControls=noop,renderDashboardPageControls=noop,pruneDashboardSelection=noop;
const renderDashboardSelectedPanel=noop,renderDashboardSelectionControls=noop,renderDashboardLoadingModal=noop;
const GENERIC_FAILURE_MESSAGE="Synthetic failure";
function dashboardSessionCard(){return "";}
function outputRowsValid(){return {valid:true,errors:[]};}
function outputRowsToLineItems(){return state.outputRows.map(r=>({...r}));}
function refreshOutputRowsFromLineItems(){state.outputRows=state.lineItems.map(r=>({...r,pricing_keyword:state.quoteBasisSections[0].lines[0].pricing_keyword}));}
function collectQuoteExchangeRate(){return input.fx || 2;}
function collectQuoteCurrency(){return input.currency || 'AUD';}
function quoteCommercialStateIsOwned(){return false;}
function escapeHtml(v){return String(v).replace(/&/g,'&amp;').replace(/"/g,'&quot;');}
function canonicalPricingAuthorityText(v){return String(v || '');}
function normalizeCategoryTitle(v){return v;}
function cloneQuoteBasisSections(v){return JSON.parse(JSON.stringify(v));}
function normalizePossiblePricingMatches(v){return v;}
function pricingReferenceLineText(v){return v;}
function quoteBasisFromSections(v){return {selected:v[0].lines[0].text};}
function catalogBackedPossibleMatchText(line,match){return '[ '+match.description+' ] - '+line.text;}
function buildPricingAuthority(kind,row,options={}) {return {variant:kind,price:options.price,context:{},...(options.priceCurrency?{price_currency:options.priceCurrency}: {})};}
function normalizeOutputPricingAuthority(a){return a;}
// This extracted unit harness has no admitted catalogue; live rendered tests cover trust.
function outputCatalogueBaseline(){return null;}
function currentPricingReference(){return {items:[]};}
function selectedPricingReferenceCurrency(){return 'SGD';}
function pricingReferenceCatalogItem(){return null;}
const PRICING_AUTHORITY_TRUSTED_VARIANTS=new Set(['manual','catalog','included']);
const DEFAULT_CURRENCY_LABEL='SGD';
function normalizeCurrencyLabel(v){return String(v).toUpperCase();}
function filteredDashboardSessions(){return state.dashboardSearch?[]:state.quoteSessions;}
function pagedDashboardSessions(v){return v;}
function dashboardPageRange(n){return {start:n?1:0,end:n};}
function dashboardDateFilterSummaryText(){return '';}
for (const name of ['normalizeBasisTag', 'retainExcludedBasisPricingRows']) eval(extract(name));
for (const name of ['normalizeUnit','numberOrNull','formatAmount','pricingAuthorityPrice','pricingAuthorityNumber','unitPriceEditKind','roundCommercialCents','quoteFxMultiplier','quoteAmountValue','effectiveOutputUnitPrice','synchronizeOwnedOutputRowPrice','recalculateOutputRow','outputPriceAuthority','outputPriceDenomination','admittedOutputPrice','revokeOutputEditor','resumeBlockedOutputDraftSave','preserveOutputRowIdentity','outputRowIdentity','outputPricingIsDeferred','outputCellDisplayValue','outputEditorPlaceholder','outputEditorHtml','commitOutputEditor','applyPossiblePricingMatch','safeQuoteSessionId','quoteAiSpendText','renderQuoteAiSpend','renderQuoteDashboard']) eval(extract(name));
if(source.includes('function outputRowFxMultiplier('))eval(extract('outputRowFxMultiplier'));
if (input.mode==='reopen') {
  for(const variant of ['manual','catalog']) for(const denomination of variant==='catalog'?['reference']:['reference','quote']) {
    for(const price of [0,1,1.01,1e20,1e22,1.2345678901234568e22]) {
      const authority={variant,price,...(denomination==='quote'?{price_currency:'AUD'}:{})};
      const row={quantity:1,unit:'nos',description:'Synthetic canonical price',price_mode:'Priced',unit_price_override:price,pricing_authority:authority};state.outputRows=[row];
      const text=outputEditorHtml(row,0,'unit_price_override').match(/ value="([^"]*)"/)[1];
      assert(!/[eE]/.test(text));assert.equal(admittedOutputPrice(text),price);
      const editor={dataset:{outputEditorField:'unit_price_override',outputRow:'0'},value:text,isConnected:true};
      activeOutputEditor={editor,row,revision:state.outputRevision,denomination,staged:false};
      const before=JSON.stringify({rows:state.outputRows,lineItems:state.lineItems,revision:state.outputRevision});
      assert.equal(commitOutputEditor(editor),'unchanged');
      assert.equal(JSON.stringify({rows:state.outputRows,lineItems:state.lineItems,revision:state.outputRevision}),before);
    }
  }
  console.log(JSON.stringify({status:'FXUI_CANONICAL_DECIMAL_REOPEN_PASS',variants:['manual-reference','manual-quote','catalog-reference']}));
} else if (input.mode==='dispatch') {
  const vm=require('node:vm');
  (async()=>{
    for(const type of ['generate','generate_pdf','draft','basis_chat']) {
      let posts=0;const options=[];
      const c={blocked:true,commitActiveOutputEditor:value=>{options.push(value);return c.blocked?'blocked':'unchanged';},postJson:async()=>{posts++;return {ok:true,data:{job_id:'synthetic-job'}};}};
      vm.createContext(c);vm.runInContext(extract('startJob'),c);
      const rejected=await c.startJob(type,{},{});assert.equal(rejected.ok,false);assert.equal(rejected.data.status,'blocked');assert.equal(posts,0);assert.equal(options[0].commit,false);
      c.blocked=false;assert.equal((await c.startJob(type,{},{})).ok,true);assert.equal(posts,1);
    }
    console.log(JSON.stringify({status:'FXUI_ASYNC_DISPATCH_ADMISSION_PASS',paths:['generate','generate_pdf','draft','basis_chat'],negativePosts:0,positivePostsPerPath:1}));
  })().catch(e=>{console.error(e);process.exitCode=1;});
} else if (input.mode==='selection-ordering') {
  const vm = require('node:vm');
  const code = ['validQuoteDraftVersion', 'validPricingLedgerReceipt', 'unchangedDraftLedgerMatches', 'pricingSelectionRefusalReason',
    'selectedCatalogueAuthorityMatches', 'pricingSelectionAcknowledgement', 'rememberPricingSelectionAcknowledgement',
    'requireQuoteDraftReopen', 'refreshLineItemsFromServer',
    'saveQuoteSessionDraftState', 'queueQuoteSessionDraftStateSave',
    'clearQuoteSessionDraftSaveTimer', 'resumeBlockedOutputDraftSave'].map(extract).join('\n');
  const turn = () => new Promise(resolve => setImmediate(resolve));
  async function check({ inflight, outcome, queueWhileSelecting = false, blockRetry = false, replacementKind }) {
    const timers = new Map(), writes = [], releases = [], selections = [];
    let nextTimer = 0, serverRevision = 4, releasePaint, releaseSelection;
    function fireTimers() {
      const pending = [...timers.values()];
      timers.clear();
      for (const timer of pending) timer();
      return pending.length;
    }
    const c = {
      state: { quoteSessionId: 'synthetic-quote', pricingLedgerRevisions: { 'synthetic-quote': { row: 4 } },
        pricingDraftVersion: 'a'.repeat(64), lineItems: [{ price: null }], value: 1, outputRows: [] },
      quoteSessionDraftSaveTimer: null, quoteSessionDraftSavePromise: null,
      browserRecoveryEpoch: 0,
      blockedOutputDraftSaveOptions: null, blocked: false,
      waitForUiPaint: () => new Promise(resolve => { releasePaint = resolve; }),
      commitActiveOutputEditor: () => c.blocked ? 'blocked' : 'unchanged', quoteSessionDraftStateCanSave: () => true,
      saveSessionState: () => {}, currentQuoteSessionDraftState: () => ({ value: c.state.value }),
      sessionFileRecordsFromDraft: () => [], quoteSessionDraftComparisonKey: JSON.stringify,
      quoteSessionDraftFileComparisonKey: JSON.stringify, quoteSessionDraftIsConfirmed: () => false,
      quoteSessionDraftFilesAreConfirmed: () => true, safeQuoteSessionId: value => value || '',
      ensureClientQuoteSessionId: () => c.state.quoteSessionId,
      isPlainObject: value => Boolean(value && typeof value === 'object'),
      normalizeLineItem: row => row, retainExcludedBasisPricingRows: rows => rows,
      pricingAuthorityPrice: authority => authority?.price ?? null,
      pendingPricingSelections: () => [{ source_basis_line_id: 'row', catalog_item_id: 'item',
        expected_revision: c.state.pricingLedgerRevisions['synthetic-quote'].row }],
      buildLineItemNormalizePayload: () => ({ draft: c.state.value }),
      saveCurrentQuoteSession: async options => {
        writes.push({ draft: options.draftState.value, generated: options.quoteGenerated });
        await new Promise(resolve => releases.push(resolve));
        c.state.pricingLedgerRevisions['synthetic-quote'].row = ++serverRevision;
        return { session_id: c.state.quoteSessionId };
      },
      postJson: async (url, payload, { isCurrent }) => {
        assert.equal(url, '/api/quote-sessions/pricing-selections');
        selections.push({ draft: payload.draft, expected: payload.pricing_selections[0].expected_revision });
        await new Promise(resolve => { releaseSelection = resolve; });
        if (!isCurrent()) return { ok: false, status: 409, data: { status: 'conflict', request_superseded: true } };
        if (selections[0].expected !== serverRevision) return { ok: false, status: 409, data: { status: 'conflict' } };
        if (outcome === 'failed') return { ok: false, status: 503, data: { status: 'failed' } };
        return { ok: true, status: 200, data: { status: 'normalized',
          line_items: [{ source_basis_line_id: 'row', price: 12, pricing_authority: { variant: 'catalog', catalog_item_id: 'item', price: 12 } }],
          selections: [{ source_basis_line_id: 'row', status: 'selected' }],
          quote_session: { session_id: 'synthetic-quote', draft_version: 'b'.repeat(64),
            pricing_ledger: { revision: 8, rows: { row: ++serverRevision } } } } };
      },
      window: { setTimeout: fn => { timers.set(++nextTimer, fn); return nextTimer; }, clearTimeout: id => timers.delete(id) },
    };
    vm.createContext(c);
    vm.runInContext(code, c);
    const sentSave = inflight ? c.saveQuoteSessionDraftState({ quoteGenerated: false }) : null;
    c.state.value = 2;
    c.queueQuoteSessionDraftStateSave({ quoteGenerated: false });
    assert.equal(timers.size, 1, 'the autosave must be queued before Confirm');
    const cancelledTimer = [...timers.values()][0];
    const refresh = c.refreshLineItemsFromServer({ waitForPaint: true });
    await turn();
    assert.equal(fireTimers(), 0, 'queued autosave can fire while Confirm owns selection');
    assert.equal(selections.length, 0, 'selection started before the preparing state could paint');
    const activeHold = c.quoteSessionDraftSaveTimer;
    c.clearQuoteSessionDraftSaveTimer();
    cancelledTimer();
    assert.equal(c.quoteSessionDraftSaveTimer, activeHold, 'timer cleanup disturbed the selection hold');
    releasePaint();
    await turn();
    if (sentSave) {
      assert.equal(selections.length, 0, 'Confirm sent selection before save acknowledgement');
      releases.shift()();
      await sentSave;
      await turn();
    }
    assert.deepEqual(selections, [{ draft: 2, expected: inflight ? 5 : 4 }]);
    assert.equal(writes.length, inflight ? 1 : 0, 'Confirm wrote a redundant draft before selection');
    if (queueWhileSelecting) {
      c.state.value = 3;
      c.queueQuoteSessionDraftStateSave({ quoteGenerated: true });
    }
    assert.equal(fireTimers(), 0, 'autosave can fire while selection is in flight');
    if (replacementKind === 'timer') {
      c.state.quoteSessionId = 'replacement-quote';
      c.state.pricingDraftVersion = 'c'.repeat(64);
      c.queueQuoteSessionDraftStateSave({ quoteGenerated: true });
    } else if (replacementKind === 'selection') {
      c.quoteSessionDraftSaveTimer = { kind: 'selection', sessionId: c.state.quoteSessionId, options: { quoteGenerated: true } };
    }
    const replacement = c.quoteSessionDraftSaveTimer;
    if (outcome === 'stale') serverRevision++;
    c.blocked = blockRetry;
    releaseSelection();
    const result = await refresh;
    if (replacementKind) {
      assert.equal(result.data.request_superseded, true);
      assert.equal(c.quoteSessionDraftSaveTimer, replacement, 'stale selection cleared the replacement slot');
      assert.deepEqual(replacement.options, { quoteGenerated: true }, 'stale selection restored its own options');
      assert.equal(timers.size, replacementKind === 'timer' ? 1 : 0);
      assert.equal(writes.length, 0);
    } else if (outcome === 'selected') {
      assert.equal(result.ok, true);
      assert.equal(c.state.lineItems[0].price, 12);
      assert.equal(c.state.pricingLedgerRevisions['synthetic-quote'].row, serverRevision);
      assert.equal(timers.size, 0, 'selected draft was scheduled for a duplicate save');
    } else if (outcome === 'stale') {
      assert.equal(result.status, 409);
      assert.equal(c.state.lineItems[0].price, null);
      assert.equal(c.state.pricingLedgerRevisions['synthetic-quote'].row, 4);
      assert.equal(timers.size, 0, 'stale local draft can overwrite the newer server revision');
    } else {
      assert.equal(result.status, 503);
      c.blocked = false;
      c.resumeBlockedOutputDraftSave();
      assert.equal(fireTimers(), 0, 'an unknown commit must not cause a retry');
      assert.equal(c.state.pricingDraftVersion, false);
      assert.equal(c.state.value, queueWhileSelecting ? 3 : 2, 'retain local edits for recovery');
      assert.deepEqual(writes, []);
    }
  }
  (async () => {
    await check({ outcome: 'selected' });
    await check({ inflight: true, outcome: 'selected' });
    await check({ outcome: 'stale' });
    await check({ outcome: 'failed' });
    await check({ outcome: 'failed', queueWhileSelecting: true, blockRetry: true });
    await check({ outcome: 'failed', replacementKind: 'timer' });
    await check({ outcome: 'failed', replacementKind: 'selection' });
    console.log(JSON.stringify({ status: 'CONFIRM_DRAFT_ORDERING_PASS' }));
  })().catch(error => { console.error(error); process.exitCode = 1; });
} else if (input.mode==='temporal') {
  const vm=require('node:vm');
  const code=['saveQuoteSessionDraftState','queueQuoteSessionDraftStateSave','clearQuoteSessionDraftSaveTimer','resumeBlockedOutputDraftSave'].map(extract).join('\n');
  function carrier() {
    const writes=[],releases=[],timers=new Map();let timerId=0;
    const c={state:{quoteSessionId:'session-a',value:1,basisConfirmed:true,outputRows:[{}]},quoteSessionDraftSavePromise:null,quoteSessionDraftSaveTimer:null,blockedOutputDraftSaveOptions:null,blocked:false,Promise,JSON,Number,
      commitActiveOutputEditor:()=>c.blocked?'blocked':'unchanged',quoteSessionDraftStateCanSave:()=>true,saveSessionState:()=>{},
      currentQuoteSessionDraftState:()=>({value:c.state.value}),sessionFileRecordsFromDraft:()=>[],quoteSessionDraftComparisonKey:JSON.stringify,quoteSessionDraftFileComparisonKey:JSON.stringify,
      ensureClientQuoteSessionId:()=>c.state.quoteSessionId,safeQuoteSessionId:v=>v,quoteSessionDraftIsConfirmed:()=>false,quoteSessionDraftFilesAreConfirmed:()=>true,
      saveCurrentQuoteSession:async options=>{writes.push(JSON.parse(JSON.stringify(options.draftState)));await new Promise(r=>releases.push(r));return {session_id:c.state.quoteSessionId};},
      window:{setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id)}};
    vm.createContext(c);vm.runInContext(code,c);return {c,writes,releases,timers};
  }
  (async()=>{
    for(const explicit of [false,true]) {
      const {c,writes,releases}=carrier();const first=c.saveQuoteSessionDraftState();
      const second=c.saveQuoteSessionDraftState(explicit?{draftState:{value:7}}:{});c.state.value=8;
      assert.deepEqual(writes,[{value:1}]);releases.shift()();await first;
      for(let i=0;i<5&&!releases.length;i++)await Promise.resolve();
      assert.deepEqual(writes,[{value:1},{value:explicit?7:8}]);releases.shift()();await second;
      assert.equal(c.state.value,8,'Late save completion cannot overwrite current editor/row state');
    }
    {
      const {c,writes,releases}=carrier();const first=c.saveQuoteSessionDraftState();const second=c.saveQuoteSessionDraftState();
      c.state.quoteSessionId='session-b';releases.shift()();await first;assert.equal(await second,null);assert.equal(writes.length,1);
    }
    {
      const {c,writes,releases,timers}=carrier();c.queueQuoteSessionDraftStateSave();c.state.value=9;c.queueQuoteSessionDraftStateSave();
      assert.equal(timers.size,1);const fn=[...timers.values()][0];timers.clear();fn();assert.deepEqual(writes,[{value:9}]);releases.shift()();
      c.blocked=true;c.queueQuoteSessionDraftStateSave();assert.equal(await c.saveQuoteSessionDraftState(),null);assert.equal(timers.size,0);assert.equal(writes.length,1);
    }
    {
      const {c,writes,releases,timers}=carrier();c.queueQuoteSessionDraftStateSave();c.state.value=12;c.blocked=true;
      const fn=[...timers.values()][0];timers.clear();fn();assert.equal(writes.length,0);assert.ok(c.blockedOutputDraftSaveOptions);
      c.blocked=false;c.resumeBlockedOutputDraftSave();assert.equal(timers.size,1);
      const resumed=[...timers.values()][0];timers.clear();resumed();assert.deepEqual(writes,[{value:12}]);releases.shift()();
    }
    console.log(JSON.stringify({status:'FXUI_R05_R10_TEMPORAL_PASS',cases:['coalesced timer','explicit queued snapshot','ordinary recapture','session replacement','late completion','invalid pending editor','blocked timer resumed after cancel']}));
  })().catch(e=>{console.error(e);process.exitCode=1;});
} else if (input.mode==='fx') {
  const row={quantity:3,unit:'nos',description:'Synthetic manual row',price_mode:'Priced',unit_price_override:100,pricing_authority:{variant:'manual',price:100}};
  state.outputRows=[row];
  assert(outputEditorHtml(row,0,'unit_price_override').includes('value="100"'),'Reference editor must retain source denomination');
  const editor={dataset:{outputEditorField:'unit_price_override',outputRow:'0'},value:'1',isConnected:true};
  activeOutputEditor={editor,row,revision:state.outputRevision,denomination:'reference',staged:false};
  assert.strictEqual(commitOutputEditor(editor),'committed');
  assert.strictEqual(outputCellDisplayValue(state.outputRows[0],'unit_price_override'),'1.00');
  assert.strictEqual(outputCellDisplayValue(state.outputRows[0],'quoted_unit_price'),formatAmount(input.fx));
  assert.strictEqual(outputCellDisplayValue(state.outputRows[0],'amount'),formatAmount(3*input.fx));
  const reference=state.outputRows[0];
  const override={dataset:{outputEditorField:'unit_price_override',outputRow:'0'},value:'1',isConnected:true};
  activeOutputEditor={editor:override,row:reference,revision:state.outputRevision,denomination:'quote',staged:true};
  assert.strictEqual(commitOutputEditor(override),'committed');
  assert.strictEqual(commitOutputEditor(override),'unchanged');
  assert.strictEqual(outputCellDisplayValue(state.outputRows[0],'quoted_unit_price'),'1.00');
  assert.strictEqual(outputCellDisplayValue(state.outputRows[0],'amount'),'3.00');
  for(const invalid of ['', '-1','no','1.005','1.00000000000000000001','9007199254740993']) assert.strictEqual(admittedOutputPrice(invalid),null,invalid);
  assert.strictEqual(admittedOutputPrice('1.000'),1);
  assert.strictEqual(admittedOutputPrice('0'),0);
  assert.strictEqual(admittedOutputPrice('10000000000000000000000'),1e22);
  assert.strictEqual(admittedOutputPrice('0.10000000000000000000'),.1);
  assert.strictEqual(quoteAmountValue(roundCommercialCents(3*1.01),1.37),4.15);
  assert.strictEqual(roundCommercialCents(.1*1.15),.11);
  console.log(JSON.stringify(state.outputRows[0]));
} else if (input.mode==='alignment') {
  const sectionTitleKey=(value)=>String(value||'').toLowerCase();
  const normalizeQuoteBasisTitle=(value)=>value;
  const outputRowCoversBasisLine=()=>true;
  eval(extract('outputRowSectionMatchesBasis'));
  eval(extract('outputRowBasisMatchRank'));
  const entry={section:{title:'Imported reference heading'},line:{id:'basis-row',source_line_item_id:'source-row',pricing_keyword:'selected-item'}};
  const ownedIds=new Set(['basis-row','source-row','other-row']);
  for(const variant of ['catalog','manual','included','historical']) {
    const row={section:'Canonical catalogue category',source_basis_line_id:'source-row',pricing_keyword:'selected-item',pricing_authority:{variant}};
    assert.strictEqual(outputRowBasisMatchRank(row,entry,ownedIds,new Set(['source-row'])),4,'Stable source identity must survive a catalogue section change');
    assert.strictEqual(outputRowBasisMatchRank(row,entry,ownedIds,new Set()),0,'A non-unique source identity must not cross sections');
    assert.strictEqual(outputRowBasisMatchRank({...row,source_basis_line_id:'other-row'},entry,ownedIds),0,'Another owned row must never transfer by keyword');
    assert.strictEqual(outputRowBasisMatchRank({...row,source_basis_line_id:''},entry,ownedIds),0,'An unidentified row must remain section-scoped');
    assert.strictEqual(outputRowBasisMatchRank({...row,source_basis_line_id:'unrelated-row'},entry,ownedIds),0,'An unrelated row must remain section-scoped');
  }
  const normalizeQuoteBasisSections=(sections)=>sections;
  const quoteBasisOutputEntries=(sections)=>sections.flatMap((section)=>(section.lines||[]).map((line)=>({section,line})));
  const inheritBasisOutputFieldsForEntry=(row)=>row;
  const outputRowFromBasisEntry=({line})=>({source_basis_line_id:line.id});
  eval(extract('outputRowsAlignedToBasis'));
  const row={section:'Canonical catalogue category',source_basis_line_id:'source-row',pricing_authority:{variant:'catalog'}};
  state.quoteBasisSections=[{...entry.section,lines:[entry.line]}];
  assert.strictEqual(outputRowsAlignedToBasis([row])[0],row,'Unique basis and generated source IDs preserve the admitted row');
  state.quoteBasisSections=[{...entry.section,lines:[entry.line,{...entry.line,id:'duplicate-basis-row'}]}];
  assert(outputRowsAlignedToBasis([row]).every((output)=>!output.pricing_authority),'Duplicate basis source IDs must not cross sections');
  state.quoteBasisSections=[{...entry.section,lines:[entry.line]}];
  assert(outputRowsAlignedToBasis([row,{...row}]).every((output)=>!output.pricing_authority),'Duplicate generated source IDs must not cross sections');
  state.quoteBasisSections=[{...entry.section,lines:[{...entry.line,id:'source-row'}]}];
  assert.strictEqual(outputRowsAlignedToBasis([row])[0],row,'Repeating the same ID within one basis line is one owner');
  console.log(JSON.stringify({status:'STABLE_ROW_SECTION_ALIGNMENT_PASS'}));
} else if (input.mode==='selection') {
  for (const name of [
    'cleanCustomerQuoteLineText', 'bracketedCatalogReferenceParts', 'leadingNumber', 'formatQuantityNumber',
    'normalizeQuantityPrefixUnit', 'leadingQuantityPrefix', 'quantityUnitAliases', 'startsWithQuantityUnit',
    'stripLeadingQuantityCountFromLineText', 'normalizedLineTextQuantityParts',
    'isNonQuoteNote', 'isInformationalDimensionText', 'basisLineIsInformationalDimension',
  ]) eval(extract(name));
  state.lineItems=[input.row];state.outputRows=[{...input.row}];
  const line={...input.line,tag:'Custom',custom_pricing:true,possible_pricing_matches:[input.match]};
  delete line.pricing_keyword;state.quoteBasisSections=[{id:'selected',lines:[line]}];
  applyPossiblePricingMatch('selected',0,0);
  assert.strictEqual(state.lineItems[0].pricing_keyword,'','A UI selection must not stamp an unbound selector into authoritative lineItems');
  assert.strictEqual(state.outputRows.length,0,'Old pricing output must invalidate until server admission');
  console.log(JSON.stringify({line_items:state.lineItems,quote_basis_sections:state.quoteBasisSections}));
} else if(input.mode==='dashboard') {
  renderQuoteDashboard();
  assert.strictEqual(elements.dashboardSearchInput.value,'restored-hidden-search','Visible search must reflect restored filtering state');
  console.log('{}');
}
