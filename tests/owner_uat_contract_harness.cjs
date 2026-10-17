const fs = require('fs');
const assert = require('assert');
const source = fs.readFileSync('webapp/static/app.js', 'utf8');
const input = JSON.parse(fs.readFileSync(0, 'utf8') || '{}');
function extract(name) {
  const start = source.indexOf('function ' + name + '(');
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
const nodes = new Map();
function node() { return {value:'',textContent:'',hidden:false,querySelector(){return null;},querySelectorAll(){return [];},classList:{toggle(){},add(){},remove(){}}}; }
const elements = new Proxy({}, { get(_t,k) {if(!nodes.has(k))nodes.set(k,node());return nodes.get(k);} });
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
function currentPricingReference(){return {items:[]};}
function pricingReferenceCatalogItem(){return null;}
const PRICING_AUTHORITY_TRUSTED_VARIANTS=new Set(['manual','catalog','included']);
const DEFAULT_CURRENCY_LABEL='SGD';
function normalizeCurrencyLabel(v){return String(v).toUpperCase();}
function filteredDashboardSessions(){return state.dashboardSearch?[]:state.quoteSessions;}
function pagedDashboardSessions(v){return v;}
function dashboardPageRange(n){return {start:n?1:0,end:n};}
function dashboardDateFilterSummaryText(){return '';}
for (const name of ['numberOrNull','formatAmount','pricingAuthorityPrice','pricingAuthorityNumber','unitPriceEditKind','roundCommercialCents','quoteFxMultiplier','quoteAmountValue','effectiveOutputUnitPrice','synchronizeOwnedOutputRowPrice','recalculateOutputRow','outputCellDisplayValue','outputEditorPlaceholder','outputEditorHtml','commitOutputEditor','applyPossiblePricingMatch','renderQuoteDashboard']) eval(extract(name));
if(source.includes('function outputRowFxMultiplier('))eval(extract('outputRowFxMultiplier'));
if (input.mode==='fx') {
  state.outputRows=[{quantity:3,unit:'nos',description:'Synthetic manual row',price_mode:'Priced',unit_price_override:100}];
  const initial=outputEditorHtml(state.outputRows[0],0,'unit_price_override');
  assert(initial.includes('value="'+String(100*input.fx)+'"'),'Editor must open at the displayed quote-currency price');
  commitOutputEditor({dataset:{outputEditorField:'unit_price_override',outputRow:'0'},value:'1',isConnected:true});
  const display=outputCellDisplayValue(state.outputRows[0],'unit_price_override');
  assert.strictEqual(display,'1.00','Typed quote price1 must remain1 at FX '+input.fx);
  assert(outputEditorHtml(state.outputRows[0],0,'unit_price_override').includes('value="1"'));
  assert.strictEqual(outputCellDisplayValue(state.outputRows[0],'amount'),'3.00');
  console.log(JSON.stringify(state.outputRows[0]));
} else if (input.mode==='selection') {
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
