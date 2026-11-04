const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('webapp/static/app.js','utf8'), input=JSON.parse(fs.readFileSync(0,'utf8'));
const funcs=[...source.matchAll(/^(?:async )?function ([A-Za-z0-9_]+)\([^]*?^}/gm)].map(m=>m[0]);
const constants=source.slice(0,source.indexOf('const QUOTE_COMMERCIAL_REVIEW_REASONS'))+'\n'+source.slice(source.indexOf('const AUTHORITATIVE_BASIS_LIMITS'),source.indexOf('function authoritativeBasisKeyClass'))+'\n'+source.slice(source.indexOf('const QUOTE_BASIS_LEGACY_ORDER'),source.indexOf('function canonicalQuoteBasis('))+'\nconst AUTHORITATIVE_BASIS_KEY_SAFETY=new Map();';
const ctx={input,TextEncoder,console,result:null};vm.createContext(ctx);vm.runInContext(constants+'\n'+funcs.join('\n'),ctx);
vm.runInContext(`
const reference=input.reference;
const state={quoteBasisSections:input.request.quote_basis_sections,lineItems:input.request.line_items,pricingLedgerRevisions:{},quoteSessionId:input.request.quote_session.session_id,outputRows:[],originalOutputRows:[],quoteCommercialLifecycle:'EXISTING',quoteCommercialReview:null,pricingReferences:[reference],quoteCommercialSnapshot:{pricing_basis:{currency:reference.currency,source:reference.source,id:reference.id,digest:reference.digest_sha256}}};
currentPricingReference=()=>reference;collectQuoteCurrency=()=>'SGD';collectQuoteExchangeRate=()=>1;
// Replace effectful rendering/download invalidation with inert no-ops; selection/cloning/pending logic is production.
setDownloadFiles=()=>{};markOutputRowsDirty=()=>{};updateQuoteBasisCard=()=>{};syncControlStates=()=>{};
const section=state.quoteBasisSections.find(s=>s.lines.some(l=>l.id==='dimension-note'));
const index=section.lines.findIndex(l=>l.id==='dimension-note');
const before=JSON.parse(JSON.stringify(section.lines[index]));
if (input.response_rows) {
 const output=outputRowsAlignedToBasis(input.response_rows.map(outputRowFromLineItem));
 result={output:output.map(r=>({id:r.source_basis_line_id,quantity:r.quantity,unit:r.unit,authority:outputPriceAuthority(r)?.variant||null,quoted:outputCellDisplayValue(r,'quoted_unit_price'),amount:outputCellDisplayValue(r,'amount')}))};
} else {
applyPossiblePricingMatch(section.id,index,0);
const selected=state.quoteBasisSections.find(s=>s.id===section.id).lines[index];
const pending=pendingPricingSelections();
result={before,selected,pending,sections:state.quoteBasisSections,source_rows_unchanged:JSON.stringify(state.lineItems)===JSON.stringify(input.request.line_items)};
}
`,ctx);
if (!input.response_rows) { assert.equal(ctx.result.selected.tag,'Include');assert.equal(ctx.result.selected.custom_pricing,undefined);assert.equal(ctx.result.pending.length,1);assert.equal(ctx.result.source_rows_unchanged,true); }
console.log(JSON.stringify(ctx.result));
