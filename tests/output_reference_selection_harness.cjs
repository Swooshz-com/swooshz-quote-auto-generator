const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
const source = fs.readFileSync(input.source || 'webapp/static/app.js', 'utf8');
function extract(name) {
  const declaration = new RegExp('(?:async )?function ' + name + '\\(').exec(source);
  assert(declaration, 'Missing production function ' + name);
  const start = declaration.index;
  const body = source.indexOf(') {', start) + 2;
  let depth = 0;
  for (let i = body; i < source.length; i++) {
    if (source[i] === '{') depth++;
    if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw Error('Unclosed production function');
}
const clone = value => JSON.parse(JSON.stringify(value));
async function run(test = {}) {
  const row = clone(input.row || {source_basis_line_id:'selected-row', section:'Synthetic', description:'Synthetic panel', quantity:2, unit:'sqm', pricing_keyword:'synthetic-panel', price_mode:'Priced', pricing_authority:{variant:test.variant || 'historical'}});
  row.pricing_authority.variant = test.variant || row.pricing_authority.variant;
  const other = {source_basis_line_id:'manual-row', description:'Synthetic manual', quantity:8, unit:'nos', pricing_authority:{variant:'manual',price:45,price_currency:'SGD'}};
  const state = {quoteSessionId:'quote-selection', browserRecoveryScope:'synthetic-scope', outputRevision:5, outputRows:[other,row], lineItems:[other,row], quoteBasisSections:clone(input.sections || [{lines:[{id:'selected-row',pricing_keyword:row.pricing_keyword,tag:'Include'},{id:'removed-row',tag:'Include'}]}]), quoteCommercialSnapshot:{pricing_basis:{id:'synthetic-reference'}}, originalOutputRows:[{source_basis_line_id:'removed-row'}], pricingLedgerRevisions:{'quote-selection':{'selected-row':2}}, outputReferenceSelection:null};
  const initial = JSON.stringify({rows:state.outputRows,reset:state.originalOutputRows});
  const calls = [], messages = [];
  let currentReference = {id:'synthetic-reference',source:'local',digest:'synthetic-digest'};
  const editor = {isConnected:true};
  const button = {isConnected:true,closest:()=>({contains:value=>value===editor})};
  const c = {state,activeOutputEditor:{row,editor,revision:5},browserRecoveryEpoch:1, JSON,Number,Map,Set,Object,Array,Boolean,String,
    elements:{pricingMatchesBody:{contains:()=>true}},
    appIsBusy:()=>Boolean(state.outputReferenceSelection),
    currentBrowserRecoveryScope:()=>state.browserRecoveryScope,
    safeQuoteSessionId:value=>value,
    currentPricingReference:()=>currentReference,
    isPlainObject:value=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value),
    outputCatalogueBaseline:()=>test.noReference?null:18,
    pricingReferenceCatalogItem:()=>({id:row.pricing_keyword,sale_unit_price:18}),
    outputReferenceCatalogItem:()=>({id:row.pricing_keyword,sale_unit_price:18}),
    buildPricingAuthority:(variant,value)=>({variant,price:18,catalog_item_id:value.pricing_keyword}),
    normalizeOutputPricingAuthority:value=>value,
    outputPriceAuthority:value=>value.pricing_authority,
    applyPricingAuthorityProjection:(value,authority)=>({...value,pricing_authority:authority,unit_price_override:authority.price}),
    recalculateOutputRow:value=>value,normalizeOutputRow:value=>value,
    preserveOutputRowIdentity:(_old,value)=>value,
    revokeOutputEditor:()=>{c.activeOutputEditor=null;},
    outputRowsToLineItems:(rows=state.outputRows)=>clone(rows),
    outputRowsValid:()=>({valid:true,errors:[]}),
    markOutputRowsDirty:()=>{state.outputRevision++;},
    syncControlStates:()=>{},renderPricingMatches:()=>{},renderMatchSummary:()=>{},
    renderOutputValidationMessages:value=>{if(value.length) messages.push(value);},
    queueQuoteSessionDraftStateSave:()=>calls.push('save'),resumeBlockedOutputDraftSave:()=>{},
    PRICING_SELECTION_STALE_MESSAGE:'Synthetic stale selection',
    genericFailureMessage:()=> 'Synthetic safe failure',
    quoteSessionDraftSynchronizationFailureMessage:()=> 'Synthetic save failure',
    ensureQuoteSession:async()=>{
      calls.push('synchronise');
      assert.equal(JSON.stringify({rows:state.outputRows,reset:state.originalOutputRows}),initial,'No local catalogue promotion before server acknowledgement');
      if(test.concurrentClick) assert.equal(await c.applyOutputReferencePricing(button),'unchanged');
      state.pricingLedgerRevisions['quote-selection']['selected-row']=7;
      if(test.changeAt==='save') change();
      return test.saveFailure?'':'quote-selection';
    },
    buildLineItemNormalizePayload:()=>({...(clone(input.payload || {})),line_items:clone(state.lineItems),quote_session:{session_id:state.quoteSessionId,draft_state:{lineItems:clone(state.lineItems),outputRows:clone(state.outputRows),quoteBasisSections:clone(state.quoteBasisSections)}}}),
    postJson:async(url,payload)=>{
      calls.push({url,payload:clone(payload)});
      assert.equal(JSON.stringify({rows:state.outputRows,reset:state.originalOutputRows}),initial);
      if(test.changeAt==='request') change();
      if(test.throwRequest) throw Error('synthetic transport interruption');
      const selected = clone(payload.line_items.find(value=>value.source_basis_line_id===row.source_basis_line_id));
      selected.pricing_authority={...selected.pricing_authority,variant:'catalog',price:18,catalog_item_id:row.pricing_keyword};
      if(url==='/api/line-items/normalize') {
        if(!test.existingGrant) selected.pricing_authority={variant:'historical'};
        return {ok:!test.normalizeFailure,data:{status:'normalized',line_items:test.badNormalization?[]:[selected]}};
      }
      if(test.changeAt==='selection') change();
      const data = clone(input.response || {status:'normalized',selections:[{source_basis_line_id:row.source_basis_line_id,status:'selected'}],line_items:[selected,{source_basis_line_id:'removed-row'}],quote_session:{session_id:'quote-selection',pricing_ledger:{rows:{'selected-row':8}}}});
      if(test.refused) data.selections[0]={source_basis_line_id:row.source_basis_line_id,status:'refused',reason:test.refused};
      if(test.malformed==='session') data.quote_session.session_id='quote-other';
      if(test.malformed==='rows') data.line_items.push(clone(data.line_items[0]));
      if(test.malformed==='outcome') data.selections=[];
      if(test.malformed==='authority') data.line_items[0].pricing_authority.variant='historical';
      return {ok:!test.httpFailure,status:test.httpFailure || 200,data};
    },
  };
  function change() {
    if(test.change==='scope') state.browserRecoveryScope='other-scope';
    if(test.change==='epoch') c.browserRecoveryEpoch++;
    if(test.change==='session') state.quoteSessionId='quote-other';
    if(test.change==='revision') state.outputRevision++;
    if(test.change==='row') state.outputRows[1]={...row};
    if(test.change==='basis') state.quoteBasisSections[0].lines[0].tag='Exclude';
    if(test.change==='reference') currentReference={...currentReference,digest:'replacement'};
    if(test.change==='commercials') state.quoteCommercialSnapshot.pricing_basis.id='replacement';
    if(test.change==='unload') state.isPageUnloading=true;
    if(test.change==='auth') state.isRecoveryScopeTransitioning=true;
  }
  if(test.noEditor) c.activeOutputEditor=null;
  if(test.detached) button.isConnected=false;
  if(test.duplicate) state.outputRows.push({...row});
  vm.createContext(c);vm.runInContext(extract('applyOutputReferencePricing'),c);
  const result=await c.applyOutputReferencePricing(button);
  const requests=calls.filter(value=>typeof value==='object'&&value.url==='/api/quote-sessions/pricing-selections');
  const shouldSelect=!test.noReference&&!test.noEditor&&!test.detached&&!test.duplicate&&!test.saveFailure&&test.changeAt!=='save'&&test.changeAt!=='request'&&!test.existingGrant&&!test.normalizeFailure&&!test.badNormalization&&!test.throwRequest;
  assert.equal(requests.length,shouldSelect?1:0,'Exactly one conditional selection for an eligible explicit action');
  if(requests.length) {
    assert.equal(requests[0].url,'/api/quote-sessions/pricing-selections');
    assert.deepEqual(requests[0].payload.pricing_selections,[{source_basis_line_id:row.source_basis_line_id,catalog_item_id:row.pricing_keyword,expected_revision:7}]);
    assert.deepEqual(requests[0].payload.line_items.map(value=>value.source_basis_line_id),['manual-row',row.source_basis_line_id]);
    assert.equal(requests[0].payload.quote_session.draft_state.lineItems[1].pricing_authority.variant,'catalog');
  }
  const success=(shouldSelect||test.existingGrant)&&!test.changeAt&&!test.refused&&!test.httpFailure&&!test.malformed&&!test.throwRequest;
  assert.equal(result,success?'committed':(test.noReference||test.noEditor||test.detached||test.duplicate?'unchanged':'blocked'));
  assert.equal(state.outputRows[0],other,'Other Output rows retain their identity and manual prices');
  assert.deepEqual(state.originalOutputRows,[{source_basis_line_id:'removed-row'}]);
  if(success) {
    assert.equal(state.outputRows.length,2);
    assert.equal(state.outputRows[1].pricing_authority.variant,'catalog');
    assert.equal(state.pricingLedgerRevisions['quote-selection']['selected-row'],test.existingGrant?7:8);
  } else if(!test.changeAt) assert.equal(state.outputRows[1],row,'No adoption on refusal or failure');
  assert.equal(state.outputReferenceSelection,null);
  return {result,requests,messages};
}
(async()=>{
  const reference={items:[{id:'synthetic-panel'},{id:'synthetic-other'}]};
  const bindingCases=[{}, {alias:true}, {keyword:'synthetic-panel'}, {keyword:'missing',blocked:true},
    {missingId:true,blocked:true}, {tag:'Exclude',blocked:true}, {tag:'Custom',blocked:true},
    {tag:'Confirm',blocked:true}, {custom:true,blocked:true}, {duplicate:true,blocked:true},
    {missingLine:true,blocked:true}, {missingItem:true,blocked:true}];
  for(const test of bindingCases) {
    const row={source_basis_line_id:test.missingId?'':'row',pricing_keyword:test.keyword || ''};
    const line={id:test.alias?'alias':'row',...(test.alias?{source_line_item_id:'row'}:{}),tag:test.tag || 'Include',custom_pricing:Boolean(test.custom),pricing_keyword:test.missingItem?'missing':'synthetic-panel'};
    const c={state:{quoteBasisSections:[{lines:test.missingLine?[]:test.duplicate?[line,{...line}]:[line]}]},String,
      normalizeBasisTag:value=>value,currentPricingReference:()=>reference,
      pricingReferenceCatalogItem:(ref,id)=>ref.items.find(item=>item.id===id) || null};
    vm.createContext(c);vm.runInContext(extract('outputReferenceCatalogItem'),c);
    assert.equal(c.outputReferenceCatalogItem(row)?.id || null,test.blocked?null:'synthetic-panel',JSON.stringify(test));
  }
  if(input.single) return console.log(JSON.stringify(await run(input.single)));
  const cases=['catalog','manual','included','historical','none'].map(variant=>({variant}));
  cases.push({concurrentClick:true},{existingGrant:true},{normalizeFailure:true},{badNormalization:true},{noReference:true},{noEditor:true},{detached:true},{duplicate:true},{saveFailure:true},{throwRequest:true});
  for(const httpFailure of [401,403,409,500]) cases.push({httpFailure});
  for(const refused of ['unit_review_required','contradiction','not_in_catalogue','reference_changed','ineligible']) cases.push({refused});
  for(const malformed of ['session','rows','outcome','authority']) cases.push({malformed});
  for(const changeAt of ['save','request','selection']) for(const change of ['scope','epoch','session','revision','row','basis','reference','commercials','unload','auth']) cases.push({changeAt,change});
  for(const test of cases) {try {await run(test);} catch(error) {error.message=JSON.stringify(test)+': '+error.message;throw error;}}
  console.log(JSON.stringify({status:'PASS',cases:cases.length,bindingCases:bindingCases.length}));
})().catch(error=>{console.error(error);process.exitCode=1;});
