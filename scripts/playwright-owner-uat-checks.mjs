import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

// Component fixtures are synthetic and run only against the isolated G3 server.
// Operator actions, normalisation, persistence and XLSX generation use real code.
export async function verifyOwnerUatChecks(page, sessionId, outputDir) {
  const predecessorBytes=await fs.readFile(new URL('../tests/fixtures/fxui-c17-predecessor.json',import.meta.url));
  assert.equal(createHash('sha256').update(predecessorBytes).digest('hex'),'1db57ff326ad072ecef74f146c5c5c1965fe5d55c1028de0ff30dcf0ea5a0d99');
  const predecessor=JSON.parse(predecessorBytes);
  const fixture = await page.evaluate(() => {
    const reference=currentPricingReference();
    const catalog=(reference.authority_items || reference.items).find(item=>item.sale_unit_price>0);
    if(!catalog)throw new Error('Synthetic catalog is missing');
    const unit=normalizeUnit(catalog.unit || catalog.unit_hint);
    const controlCatalog=(reference.authority_items || reference.items).find(item=>item.sale_unit_price>0 && item.id!==catalog.id);
    if(!controlCatalog)throw new Error('Synthetic positive control catalog is missing');
    const control={section:controlCatalog.section,description:controlCatalog.description,quantity:2,unit:normalizeUnit(controlCatalog.unit || controlCatalog.unit_hint),pricing_keyword:controlCatalog.id,source_basis_line_id:'c17-control',price_mode:'Priced'};
    control.pricing_authority=buildPricingAuthority('catalog',control,{price:controlCatalog.sale_unit_price,catalogItem:controlCatalog,reference});
    const review={section:catalog.section,description:'Synthetic review '+catalog.description,quantity:2,unit,pricing_keyword:'',source_basis_line_id:'c17-selected',price_mode:'Priced'};
    review.pricing_authority=buildPricingAuthority('none',review);
    state.lineItems=[control,review];
    state.quoteBasisSections=[{id:'c17-control-section',title:controlCatalog.section,lines:[{id:'c17-control',tag:'Include',text:controlCatalog.description,quantity:2,unit:control.unit,pricing_keyword:controlCatalog.id,catalog_description:controlCatalog.description}]},{id:'c17-review',title:catalog.section,lines:[
      {id:'c17-selected',tag:'Custom',text:review.description,quantity:2,unit,custom_pricing:true,custom_confirmed:true,possible_pricing_matches:[{pricing_keyword:catalog.id,description:catalog.description,unit,catalog_unit_price:999999}]},
    ]}];
    state.quoteBasis=quoteBasisFromSections(state.quoteBasisSections);
    state.basisConfirmed=true;updateQuoteBasisCard('edited');refreshOutputRowsFromLineItems();setWorkflowStage('completed');setSidePanel('output',{force:true});
    return {catalogPrice:catalog.sale_unit_price,catalogId:catalog.id};
  });
  await page.locator('#outputSidePanel.is-active').waitFor();
  await page.locator('#quoteBasisButton').click();
  await page.locator('[data-basis-possible-match-index="0"]').click();
  const normalizationPromise=page.waitForResponse(r=>r.url().endsWith('/api/line-items/normalize'));
  await page.locator('#sideNextButton',{hasText:'Confirm Quotation Basis'}).click();
  const normalizationResponse=await normalizationPromise;
  await fs.mkdir(outputDir,{recursive:true});
  await fs.writeFile(path.join(outputDir,'owner-uat-c17-normalization.json'),JSON.stringify({request:normalizationResponse.request().postDataJSON(),response:await normalizationResponse.json()},null,2));
  await page.locator('#outputSidePanel.is-active').waitFor({timeout:30000});
  await page.waitForFunction(()=>!state.isPreparingOutput);
  const selected=await page.evaluate(()=>state.lineItems.find(r=>r.source_basis_line_id==='c17-selected'));
  assert.equal(selected?.pricing_authority?.variant,'catalog',JSON.stringify(await page.evaluate(()=>({lineItems:state.lineItems,outputRows:state.outputRows,basis:state.quoteBasisSections,commercial:state.quoteCommercialLifecycle}))));
  assert.equal(selected.pricing_authority.catalog_item_id,fixture.catalogId);
  assert.equal(selected.pricing_authority.price,fixture.catalogPrice);
  assert.notEqual(selected.pricing_authority.price,999999);

  await page.locator('#customerDetailsButton').click();
  await page.locator('#quoteCurrency').selectOption('AUD');
  await page.locator('#quoteExchangeRate').fill('3');
  await page.locator('#quoteExchangeRate').press('Tab');
  await page.locator('#sideNextButton',{hasText:'Next: Quote Company'}).click();
  await page.locator('#quoteCompanyPanel.is-active').waitFor();
  await page.locator('#sideNextButton',{hasText:'Next: Quote Basis'}).click();
  await page.locator('#sideNextButton',{hasText:'Confirm Quotation Basis'}).click();
  await page.locator('#outputSidePanel.is-active').waitFor({timeout:30000});
  await page.waitForFunction(()=>!state.isPreparingOutput);
  const index=await page.evaluate(()=>state.outputRows.findIndex(r=>r.source_basis_line_id==='c17-selected'));
  const cell=page.locator('[data-output-edit-field="unit_price_override"][data-output-row="'+index+'"]');
  const editor=page.locator('[data-output-editor-field="unit_price_override"]');
  const rowSnapshot=()=>page.evaluate(i=>({row:JSON.stringify(state.outputRows[i]),revision:state.outputRevision}),index);
  await page.evaluate(async()=>{clearQuoteSessionDraftSaveTimer();await saveQuoteSessionDraftState();});
  await verifyOutputModeKeyboardActivation(page,index,outputDir);
  const catalogueBefore=await rowSnapshot();
  assert.equal(await cell.locator('.output-cell-text').innerText(),fixture.catalogPrice.toFixed(2));
  await cell.locator('.output-cell-text').click();assert.equal(await editor.inputValue(),String(fixture.catalogPrice),JSON.stringify(await page.evaluate(()=>({pending:activeOutputEditor && {field:activeOutputEditor.editor.dataset.outputEditorField,staged:activeOutputEditor.staged,row:activeOutputEditor.row},rows:state.outputRows,html:elements.pricingMatchesBody.innerHTML}))));
  await editor.fill(fixture.catalogPrice.toFixed(3));await editor.press('Enter');
  assert.deepEqual(await rowSnapshot(),catalogueBefore,'R03 catalogue no-op must retain authority and revision');
  await cell.locator('.output-cell-text').click();await editor.fill('999');await editor.press('Escape');
  assert.deepEqual(await rowSnapshot(),catalogueBefore,'R03 Escape must preserve catalogue authority');
  // R05/R06: stage empty, retain invalid text, block real downstream entries.
  await page.locator('[data-output-price-mode="quote"][data-output-row="'+index+'"]').click();
  assert.equal(await editor.inputValue(),'');
  assert.equal(await page.locator('tr[data-output-row="'+index+'"] [data-output-edit-field="unit_price_override"]').count(),1);
  assert.equal(await page.locator('.output-fx-cell input').count(),0);
  const staged=await page.evaluate(()=>quoteSessionDraftComparisonKey(currentQuoteSessionDraftState()));
  const effects=[];const observe=r=>{if(r.method()!=='GET')effects.push(r.url());};page.on('request',observe);
  for(const text of ['', '-1','nonsense','1.005','1.00000000000000000001']) {
    await editor.fill(text);await editor.press('Enter');
    assert.equal(await editor.inputValue(),text);
    assert.equal(await editor.getAttribute('aria-invalid'),'true');
    const pageCount=page.context().pages().length;
    const blocked=await page.evaluate(async()=>{
      const before={revision:state.outputRevision,panel:state.activeSidePanel,rows:JSON.stringify(state.outputRows)};
      const save=await saveQuoteSessionDraftState();const generation=await handleGenerate();const moved=setSidePanel('customer',{force:true});
      const fresh=await startNewQuote();const discard=await discardCurrentQuoteDraftSession();const restore=await modifyDashboardQuote('blocked-synthetic');const duplicate=await duplicateDashboardQuote('blocked-synthetic');
      return {save,generation,moved,fresh,discard,restore,duplicate,before,after:{revision:state.outputRevision,panel:state.activeSidePanel,rows:JSON.stringify(state.outputRows)}};
    });
    assert.equal(blocked.save,null);assert.equal(blocked.generation,false);assert.equal(blocked.moved,false);for(const key of ['fresh','discard','restore','duplicate'])assert.equal(blocked[key],false);assert.deepEqual(blocked.after,blocked.before);assert.equal(page.context().pages().length,pageCount);
  }
  await page.locator('#sideDownloadButton').click();
  await page.locator('#sideViewPdfButton').evaluate(e=>e.click());
  assert.equal(await page.locator('#excelGeneratingModal').isVisible(),false);
  assert.equal(effects.length,0,'Invalid editor must reject before mutable HTTP requests');page.off('request',observe);
  assert.equal(await page.evaluate(()=>quoteSessionDraftComparisonKey(currentQuoteSessionDraftState())),staged);
  await editor.press('Escape');assert.deepEqual(await rowSnapshot(),catalogueBefore);
  // R01: reference commit is source-denominated, and change/blur cannot replay it.
  await cell.locator('.output-cell-text').click();await editor.fill('1');await editor.press('Enter');
  const reference=await rowSnapshot();
  assert.equal(JSON.parse(reference.row).pricing_authority.price,1);
  assert.equal(JSON.parse(reference.row).pricing_authority.price_currency,undefined);
  assert.equal(await cell.locator('.output-cell-text').innerText(),'1.00');
  assert.equal(await page.locator('tr[data-output-row="'+index+'"] td:nth-child(7) .output-cell-text').innerText(),'3.00');
  // R02: equal number still commits an explicit denomination change.
  await page.locator('[data-output-price-mode="quote"][data-output-row="'+index+'"]').click();
  assert.equal(await editor.inputValue(),'');await editor.fill('1');
  await editor.evaluate(e=>{globalThis.detachedFxEditor=e;});await editor.press('Enter');
  await page.evaluate(()=>{detachedFxEditor.dispatchEvent(new Event('change',{bubbles:true}));detachedFxEditor.dispatchEvent(new FocusEvent('focusout',{bubbles:true}));});
  const overridden=await rowSnapshot();assert.equal(overridden.revision,reference.revision+1);
  assert.equal(JSON.parse(overridden.row).pricing_authority.price_currency,'AUD');
  assert.equal(await cell.locator('.output-cell-text').innerText(),'1.00');
  // A late/background browser checkpoint must not consume valid but uncommitted text.
  await cell.locator('.output-cell-text').click();await editor.fill('2');
  const checkpoint=await page.evaluate(()=>{const before={rows:JSON.stringify(state.outputRows),revision:state.outputRevision};saveSessionState();renderPricingMatches(state.outputRows);return {before,after:{rows:JSON.stringify(state.outputRows),revision:state.outputRevision},pending:activeOutputEditor?.editor.value};});
  assert.deepEqual(checkpoint.after,checkpoint.before);assert.equal(checkpoint.pending,'2');await editor.press('Escape');
  // R07: select/custom prospective currencies cannot reinterpret committed tags.
  const currencyGuard=await page.evaluate(()=>{
    const before=JSON.stringify(state.outputRows);const revision=state.outputRevision;
    elements.quoteCurrency.value='USD';handleQuoteDetailFieldChange({target:elements.quoteCurrency});
    const select=collectQuoteCurrency();
    elements.quoteCurrency.value=CUSTOM_CURRENCY_VALUE;elements.quoteCurrencyCustom.value='XYZ';handleQuoteDetailFieldChange({target:elements.quoteCurrencyCustom});
    const custom=collectQuoteCurrency();
    elements.quoteExchangeRate.value='2';handleQuoteDetailFieldChange({target:elements.quoteExchangeRate});
    const amount=outputCellDisplayValue(state.outputRows.find(r=>r.source_basis_line_id==='c17-selected'),'amount');
    elements.quoteExchangeRate.value='3';handleQuoteDetailFieldChange({target:elements.quoteExchangeRate});
    return {before,after:JSON.stringify(state.outputRows),select,custom,amount,revision};
  });
  assert.equal(currencyGuard.select,'AUD');assert.equal(currencyGuard.custom,'AUD');assert.equal(currencyGuard.amount,'2.00');
  assert.equal(currencyGuard.before,currencyGuard.after);
  // R04: consume the exact C17 producer's immutable untagged manual authority in
  // the browser under its original reference context, then restore the carrier.
  const legacy=await page.evaluate(payload=>{
    const prior={id:state.pricingReferenceId,source:state.pricingReferenceSource,references:state.pricingReferences,currency:collectQuoteCurrency(),fx:elements.quoteExchangeRate.value};
    try {
      const reference={...payload.pricing_reference,id:payload.pricing_reference_id,source:payload.pricing_reference_source,digest_sha256:payload.quote_session.draft_state.quoteDetails.commercial_snapshot.pricing_basis.digest};
      state.pricingReferences=[reference];state.pricingReferenceId=reference.id;state.pricingReferenceSource=reference.source;
      setQuoteCurrencyControls(payload.currency || payload.quote_session.commercials.currency);elements.quoteExchangeRate.value='2';
      const row=normalizeOutputRow(payload.line_items[0]);
      return {mode:outputPriceDenomination(row),tag:row.pricing_authority.price_currency,price:outputPriceAuthority(row)?.price,amount:outputCellDisplayValue(row,'amount')};
    } finally {state.pricingReferenceId=prior.id;state.pricingReferenceSource=prior.source;state.pricingReferences=prior.references;setQuoteCurrencyControls(prior.currency);elements.quoteExchangeRate.value=prior.fx;}
  },predecessor);
  assert.equal(legacy.mode,'reference');assert.equal(legacy.tag,undefined);assert.equal(legacy.price,1);assert.equal(legacy.amount,'4.00');
  // R08 uses actual production math; no unit-first or arbitrary-decimal rewrite.
  const math=await page.evaluate(()=>({line:quoteAmountValue(roundCommercialCents(3*1.01),1.37),unit:quoteAmountValue(1.01,1.37),edge:roundCommercialCents(.1*1.15)}));
  assert.deepEqual(math,{line:4.15,unit:1.38,edge:.11});
  const headings=await page.locator('.output-match-table thead th').allTextContents();
  assert.equal(headings[4],'Reference price SGD');assert.equal(headings[5],'FX AUD per SGD');assert.equal(headings[6],'Quoted unit price AUD');
  // R09 actual text bounds must remain inside every desktop heading cell.
  for (const width of [1440, 1100]) {
    await page.setViewportSize({width,height:1000});
    const bounds=await page.locator('.output-match-table th').evaluateAll(cells=>cells.map(cell=>{
      const range=document.createRange();range.selectNodeContents(cell);
      const text=range.getBoundingClientRect(),box=cell.getBoundingClientRect();
      const words=[...cell.textContent.matchAll(/\S+/g)].map(match=>{const word=document.createRange();word.setStart(cell.firstChild,match.index);word.setEnd(cell.firstChild,match.index+match[0].length);return word.getClientRects().length;});
      return {label:cell.textContent,left:text.left-box.left,right:box.right-text.right,overflow:cell.scrollWidth-cell.clientWidth,words};
    }));
    assert.equal(bounds.every(cell=>cell.left>=-1 && cell.right>=-1 && cell.overflow<=1 && cell.words.every(lines=>lines===1)),true,JSON.stringify({width,bounds}));
  }
  // R09 narrow cards retain both denominations and currency labels.
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('tr[data-output-row="'+index+'"] td[data-output-label^="Reference price"]').isVisible(),true);
  assert.equal(await page.locator('tr[data-output-row="'+index+'"] td[data-output-label^="Quoted unit price"]').isVisible(),true);
  await page.screenshot({path:path.join(outputDir,'fxui-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  // Explicitly exercise Included and zero, then restore the final quote override.
  await cell.locator('.output-cell-text').click();await editor.fill('0');await editor.press('Tab');
  assert.equal((await rowSnapshot()).row.includes('"variant":"manual"'),true);
  await cell.locator('.output-cell-text').click();await page.locator('[data-output-included-action]').click();
  assert.equal(JSON.parse((await rowSnapshot()).row).pricing_authority.variant,'included');
  await page.locator('[data-output-price-mode="quote"][data-output-row="'+index+'"]').click();
  await editor.fill('1');await editor.press('Enter');
  assert.equal(await cell.locator('.output-cell-text').innerText(),'1.00');
  // Resolve through reference mode, then exercise positive and incomplete-Q admission.
  await page.locator('[data-output-price-mode="reference"][data-output-row="'+index+'"]').click();
  await editor.fill('1');await editor.press('Enter');
  const prospective=await page.evaluate(async()=>{
    elements.quoteCurrency.value='USD';handleQuoteDetailFieldChange({target:elements.quoteCurrency});
    const resolved=collectQuoteCurrency();const revision=state.outputRevision;
    elements.quoteCurrency.value=CUSTOM_CURRENCY_VALUE;elements.quoteCurrencyCustom.value='';handleQuoteDetailFieldChange({target:elements.quoteCurrency});
    const save=await saveQuoteSessionDraftState();const generation=await handleGenerate();const after=state.outputRevision;
    elements.quoteCurrencyCustom.value='JPY';handleQuoteDetailFieldChange({target:elements.quoteCurrencyCustom});const custom=collectQuoteCurrency();
    elements.quoteCurrency.value='AUD';handleQuoteDetailFieldChange({target:elements.quoteCurrency});
    return {resolved,revision,save,generation,after,custom};
  });
  assert.equal(prospective.resolved,'USD');assert.equal(prospective.custom,'JPY');assert.equal(prospective.save,null);assert.equal(prospective.generation,false);assert.equal(prospective.after,prospective.revision);
  await page.locator('[data-output-price-mode="quote"][data-output-row="'+index+'"]').click();await editor.fill('1');await editor.press('Enter');
  await page.evaluate(async()=>{clearQuoteSessionDraftSaveTimer();await saveQuoteSessionDraftState();});
  // Accepted large decimals must reopen in the author's decimal grammar.
  for(const denomination of ['reference','quote']) {
    await page.locator('[data-output-price-mode="'+denomination+'"][data-output-row="'+index+'"]').click();
    await editor.fill('10000000000000000000000');await editor.press('Enter');
    assert.equal(await page.evaluate(i=>outputPriceAuthority(state.outputRows[i])?.price,index),1e22,'Approved decimal must actually commit before reopening');
    await page.evaluate(async()=>{clearQuoteSessionDraftSaveTimer();await saveQuoteSessionDraftState();});
    const effects=[];const observe=request=>{if(request.method()!=='GET')effects.push(request.url());};page.on('request',observe);
    try {
      for(const key of ['Enter','Tab']) {
        const before=await page.evaluate(i=>({row:JSON.stringify(state.outputRows[i]),revision:state.outputRevision,key:quoteSessionDraftComparisonKey(currentQuoteSessionDraftState()),file:state.downloadFile,fileRevision:state.downloadFileRevision}),index);
        await cell.locator('.output-cell-text').click();assert.equal(await editor.inputValue(),'10000000000000000000000');
        await editor.press(key);
        const after=await page.evaluate(i=>({row:JSON.stringify(state.outputRows[i]),revision:state.outputRevision,key:quoteSessionDraftComparisonKey(currentQuoteSessionDraftState()),file:state.downloadFile,fileRevision:state.downloadFileRevision}),index);
        await fs.writeFile(path.join(outputDir,'fxui-large-'+denomination+'-'+key+'-noop.json'),JSON.stringify({before,after},null,2));
        assert.deepEqual(after,before,'Large admitted '+denomination+' reopen must be an exact no-op');
      }
      assert.equal(effects.length,0,'Untouched large-price editor must not save');
    } finally {page.off('request',observe);}
  }
  await cell.locator('.output-cell-text').click();await editor.fill('1');await editor.press('Enter');
  await page.evaluate(async()=>{clearQuoteSessionDraftSaveTimer();await saveQuoteSessionDraftState();});
  const identity=await page.evaluate(()=>{
    const prior={rows:structuredClone(state.outputRows),lineItems:structuredClone(state.lineItems),revision:state.outputRevision,sort:state.outputSortMode,errors:[...state.outputErrors]};
    try {
      const selected=state.outputRows.findIndex(r=>r.source_basis_line_id==='c17-selected');
      const other=state.outputRows.findIndex(r=>r.source_basis_line_id==='c17-control');
      state.outputSortMode='name';
      const description=elements.pricingMatchesBody.querySelector('tr[data-output-row="'+selected+'"] td[data-output-edit-field="description"]');
      const destination=elements.pricingMatchesBody.querySelector('tr[data-output-row="'+other+'"] td[data-output-edit-field="unit_price_override"]');
      openOutputCellEditor(description);activeOutputEditor.editor.value='AAA reordered synthetic row';
      openOutputCellEditor(destination);
      return activeOutputEditor?.row.source_basis_line_id;
    } finally {
      revokeOutputEditor();clearQuoteSessionDraftSaveTimer();state.outputRows=prior.rows;state.lineItems=prior.lineItems;state.outputRevision=prior.revision;state.outputSortMode=prior.sort;renderOutputValidationMessages(prior.errors);renderPricingMatches(state.outputRows);renderMatchSummary({pricing_matches:state.outputRows});
    }
  });
  assert.equal(identity,'c17-control','R09 commit-triggered sort must retain the clicked row identity');
  await page.locator('#backToDashboardButton').click();
  await page.locator('#dashboardLoadingModal').waitFor({state:'hidden'});
  await page.locator('#dashboardSearchInput').fill('Synthetic');
  await page.reload();
  await page.locator('#dashboardLoadingModal').waitFor({state:'hidden'});
  assert.equal(await page.locator('#dashboardSearchInput').inputValue(),'Synthetic');
  await page.locator('#dashboardSearchInput').fill('');
  await page.locator('.dashboard-session-card[data-quote-session-id="'+sessionId+'"]').click();
  await page.locator('[data-dashboard-panel-action="modify-session"]').click();
  await page.locator('#outputSidePanel.is-active').waitFor({timeout:30000});
  assert.equal(await cell.locator('.output-cell-text').innerText(),'1.00');
  await fs.mkdir(outputDir,{recursive:true});
  await page.screenshot({path:path.join(outputDir,'owner-uat-c17-output.png'),fullPage:true});
  const downloadPromise=page.waitForEvent('download',{timeout:60000});
  const terminalPromise=page.waitForResponse(async r=>{if(!/\/api\/jobs\//.test(r.url()))return false;try {return ['completed','failed','blocked','needs_review'].includes((await r.json()).status);}catch{return false;}});
  await page.locator('#sideDownloadButton').click();
  const terminal=await terminalPromise;
  const terminalData=await terminal.json();
  await fs.writeFile(path.join(outputDir,'owner-uat-c17-generation.json'),JSON.stringify(terminalData,null,2));
  if(terminalData.status!=='completed'){await downloadPromise.catch(()=>{});assert.equal(terminalData.status,'completed',JSON.stringify(terminalData));}
  const download=await downloadPromise;
  const target=path.join(outputDir,'owner-uat-c17-current.xlsx');await download.saveAs(target);
  const result=await page.evaluate(async id=>getJson('/api/quote-sessions/'+id),sessionId);
  assert.equal(result.ok,true);
  const saved=result.data.quote_session;
  const row=saved.draft_state.outputRows.find(r=>r.source_basis_line_id==='c17-selected');
  assert.equal(row.pricing_authority.price_currency,'AUD');
  assert.equal(row.pricing_authority.price,1);
  assert.equal(row.approved_quote_amount,2);
  const savedLine=saved.draft_state.lineItems.find(r=>r.source_basis_line_id==='c17-selected');
  assert.deepEqual(savedLine.pricing_authority,row.pricing_authority);
  const bytes=await fs.readFile(target),sha256=createHash('sha256').update(bytes).digest('hex');
  assert.equal(sha256,saved.exports.xlsx.sha256);
  assert.equal(saved.exports.xlsx.stale,false);
  const proof={status:'FXUI_G3_RENDERED_SERVER_CURRENT_XLSX_PASS',requirements:['R01','R02','R03','R05','R06','R07','R08','R09','R10','R11'],pageErrors:0,sessionId,fixture,fx:3,manualQuoteUnitPrice:1,approvedQuoteAmount:2,bytes:bytes.length,sha256,providerCalls:0,seed:'Existing local G3 mock response plus explicit component fixture; no live injection'};
  await fs.writeFile(path.join(outputDir,'owner-uat-c17-proof.json'),JSON.stringify(proof,null,2));
  proof.delayedGeneration=await verifyDelayedGenerationAdmission(page,cell,editor,index,outputDir);
  await fs.writeFile(path.join(outputDir,'owner-uat-c17-proof.json'),JSON.stringify(proof,null,2));
  return proof;
}


async function verifyDelayedGenerationAdmission(page,cell,editor,index,outputDir) {
  await page.evaluate(()=>hideExcelGeneratingModal());
  await cell.locator('.output-cell-text').click();await editor.fill('2');await editor.press('Enter');
  await page.evaluate(()=>clearQuoteSessionDraftSaveTimer());
  let releaseSave,signalSave;
  const gate=new Promise(resolve=>releaseSave=resolve),entered=new Promise(resolve=>signalSave=resolve);
  const pauseSave=async route=>{if(route.request().method()==='POST'){signalSave();await gate;}await route.continue();};
  let jobs=0;const observe=request=>{if(request.method()==='POST' && new URL(request.url()).pathname==='/api/jobs')jobs++;};
  await page.route('**/api/quote-sessions',pauseSave);page.on('request',observe);
  try {
    await page.locator('#sideDownloadButton').click();await entered;
    const focusTrail=[];let reachedPrice=false;
    for(let count=0;count<40;count++) {
      await page.keyboard.press('Shift+Tab');
      const focus=await page.evaluate(()=>({tag:document.activeElement.tagName,id:document.activeElement.id,field:document.activeElement.dataset.outputEditField}));focusTrail.push(focus);
      if(focus.field==='unit_price_override'){reachedPrice=true;break;}
    }
    assert.equal(reachedPrice,true,'Exercise keyboard traversal behind the existing modal');
    await page.keyboard.press('Enter');assert.equal(await editor.count(),0,'Busy generation must refuse opening another editor');
    // Inject only a transient editor fixture to challenge final dispatch even if
    // a future entry or delayed event bypasses the busy opening guard.
    await page.evaluate(i=>{
      const row=state.outputRows[i],cell=elements.pricingMatchesBody.querySelector('tr[data-output-row="'+i+'"] [data-output-edit-field="unit_price_override"]');
      cell.innerHTML=outputEditorHtml(row,i,'unit_price_override',{denomination:outputPriceDenomination(row)});
      const editor=cell.querySelector('[data-output-editor-field]');editor.value='1.005';
      activeOutputEditor={editor,row,revision:state.outputRevision,denomination:outputPriceDenomination(row),staged:false};editor.focus();
    },index);
    const before=await page.evaluate(()=>({revision:state.outputRevision,rows:JSON.stringify(state.outputRows),admission:commitActiveOutputEditor({commit:false})}));
    assert.equal(before.admission,'blocked');releaseSave();
    await page.waitForFunction(()=>!state.isGenerating);
    assert.equal(jobs,0,'Invalid editor after awaited synchronization must block final job dispatch');
    assert.equal(await editor.inputValue(),'1.005');
    assert.deepEqual(await page.evaluate(()=>({revision:state.outputRevision,rows:JSON.stringify(state.outputRows)})),{revision:before.revision,rows:before.rows});
    await editor.press('Escape');await page.evaluate(()=>hideExcelGeneratingModal());
    await page.unroute('**/api/quote-sessions',pauseSave);
    const download=page.waitForEvent('download',{timeout:60000});await page.locator('#sideDownloadButton').click();await download;
    await page.waitForFunction(()=>!state.isGenerating);
    assert.equal(jobs,1,'Resolved editor positive control dispatches exactly one real generation job');
    const evidence={busyOpeningRefused:true,focusTrail,invalidRetained:'1.005',negativeJobs:0,positiveJobs:jobs,revisionPreserved:true};
    await fs.writeFile(path.join(outputDir,'fxui-delayed-generation-proof.json'),JSON.stringify(evidence,null,2));return evidence;
  } finally {releaseSave();await page.unroute('**/api/quote-sessions',pauseSave);page.off('request',observe);}
}


// Native activation and delegated handlers run in the real loaded application.
// Synthetic row seeding and save-queue counts are isolated here; the surrounding
// owner flow separately verifies real HTTP persistence and protected XLSX output.
async function verifyOutputModeKeyboardActivation(page,index,outputDir) {
  await page.evaluate(i=>{
    clearQuoteSessionDraftSaveTimer();
    globalThis.fxModeTest={prior:{...state},fx:elements.quoteExchangeRate.value,
      queue:queueQuoteSessionDraftStateSave,blocked:blockedOutputDraftSaveOptions,
      row:structuredClone(state.outputRows[i]),saves:0};
    queueQuoteSessionDraftStateSave=()=>{fxModeTest.saves++;};
    blockedOutputDraftSaveOptions=null;
    globalThis.seedFxModeTest=mode=>{
      revokeOutputEditor();
      Object.assign(state,fxModeTest.prior);
      elements.quoteExchangeRate.value='2';
      const row={...structuredClone(fxModeTest.row),quantity:3,price_mode:'Priced'};
      delete row.display_price;
      const priced=synchronizeOwnedOutputRowPrice(row,'10',{force:true,quoteCurrencyPrice:mode==='quote'});
      state.outputRows=fxModeTest.prior.outputRows.map((r,j)=>normalizeOutputRow(j===i?priced:structuredClone(r)));
      fxModeTest.saves=0;blockedOutputDraftSaveOptions=null;
      renderPricingMatches(state.outputRows);state.lineItems=outputRowsToLineItems();syncControlStates();
    };
  },index);
  const editor=page.locator('[data-output-editor-field="unit_price_override"]');
  const snapshot=()=>page.evaluate(i=>({rows:JSON.stringify(state.outputRows),lineItems:JSON.stringify(state.lineItems),
    revision:state.outputRevision,draft:JSON.parse(quoteSessionDraftComparisonKey(currentQuoteSessionDraftState())),
    file:state.downloadFile,fileRevision:state.downloadFileRevision,pdf:state.pdfFile,pdfRevision:state.pdfFileRevision,saves:fxModeTest.saves}),index);
  const activate=async(mode,action)=>{
    const button=page.locator('[data-output-price-mode="'+mode+'"][data-output-row="'+index+'"]');
    if(action==='pointer')await button.click();
    else {await button.focus();await page.keyboard.press(action);}
  };
  const outcomes=[],expected=[],effects=[];
  const observe=request=>{if(request.method()!=='GET')effects.push(request.url());};page.on('request',observe);
  try {
    assert.equal(await page.evaluate(()=>selectedPricingReferenceCurrency()),'SGD');
    assert.equal(await page.evaluate(()=>collectQuoteCurrency()),'AUD');
    for(const mode of ['quote','reference'])for(const action of ['Enter','Space','pointer']) {
      await page.evaluate(mode=>seedFxModeTest(mode==='quote'?'reference':'quote'),mode);
      const before=await snapshot();
      await activate(mode,action);
      const opened=await page.evaluate(()=>({value:activeOutputEditor?.editor.value,mode:activeOutputEditor?.denomination,staged:activeOutputEditor?.staged}));
      assert.deepEqual(await snapshot(),before,'Mode selection alone must preserve authority, revision, draft, publication and price');
      await editor.fill('1.005');await editor.press('Enter');
      assert.equal(await editor.inputValue(),'1.005');assert.equal(await editor.getAttribute('aria-invalid'),'true');
      assert.equal(await page.evaluate(()=>commitActiveOutputEditor({commit:false})),'blocked');
      assert.deepEqual(await snapshot(),before,'Invalid keyboard edit must have zero commercial effects');
      await editor.press('Escape');assert.deepEqual(await snapshot(),before,'Escape must cancel exact authority');
      await activate(mode,action);await editor.fill('1');
      await editor.evaluate(e=>{globalThis.fxModeDetachedEditor=e;});await editor.press('Enter');
      await page.evaluate(()=>{fxModeDetachedEditor.dispatchEvent(new Event('change',{bubbles:true}));fxModeDetachedEditor.dispatchEvent(new FocusEvent('focusout',{bubbles:true}));});
      const committed=await page.evaluate(i=>({mode:outputPriceDenomination(state.outputRows[i]),
        price:outputPriceAuthority(state.outputRows[i])?.price,tag:outputPriceAuthority(state.outputRows[i])?.price_currency??null,
        amount:outputCellDisplayValue(state.outputRows[i],'amount'),revision:state.outputRevision,saves:fxModeTest.saves}),index);
      outcomes.push({mode,action,opened,committed});
      expected.push({mode,action,opened:{value:'',mode,staged:true},committed:{mode,price:1,tag:mode==='quote'?'AUD':null,
        amount:mode==='quote'?'3.00':'6.00',revision:before.revision+1,saves:1}});
    }
    // Ordinary cell shortcuts still open the current denomination and no-op safely.
    for(const mode of ['reference','quote'])for(const key of ['Enter','F2']) {
      await page.evaluate(mode=>seedFxModeTest(mode),mode);const before=await snapshot();
      const cell=page.locator('tr[data-output-row="'+index+'"] [data-output-edit-field="unit_price_override"]');
      await cell.focus();await page.keyboard.press(key);assert.equal(await editor.inputValue(),'10');
      assert.equal(await page.evaluate(()=>activeOutputEditor.denomination),mode);
      await editor.press('Tab');assert.deepEqual(await snapshot(),before);
    }
    // Included is a nested native button too; invalid input must not swallow it.
    for(const action of ['Enter','Space','pointer']) {
      await page.evaluate(()=>seedFxModeTest('reference'));
      const cell=page.locator('tr[data-output-row="'+index+'"] [data-output-edit-field="unit_price_override"]');
      await cell.focus();await page.keyboard.press('F2');await editor.fill('1.005');
      const included=page.locator('[data-output-included-action][data-output-row="'+index+'"]');
      if(action==='pointer')await included.click();else {await included.focus();await page.keyboard.press(action);}
      assert.deepEqual(await page.evaluate(i=>({variant:outputPriceAuthority(state.outputRows[i])?.variant,
        amount:outputCellDisplayValue(state.outputRows[i],'amount'),saves:fxModeTest.saves}),index),{variant:'included',amount:'0.00',saves:1});
      assert.equal(await editor.count(),0);
    }
    assert.equal(effects.length,0,'Isolated event fixtures must not send mutable HTTP requests');
  } finally {
    await fs.writeFile(path.join(outputDir,'fxui-native-mode-activation.json'),JSON.stringify({outcomes,expected,effects},null,2));
    page.off('request',observe);
    await page.evaluate(()=>{
      revokeOutputEditor();clearQuoteSessionDraftSaveTimer();Object.assign(state,fxModeTest.prior);
      elements.quoteExchangeRate.value=fxModeTest.fx;queueQuoteSessionDraftStateSave=fxModeTest.queue;
      blockedOutputDraftSaveOptions=fxModeTest.blocked;
      renderPricingMatches(state.outputRows);renderOutputValidationMessages(state.outputErrors);syncControlStates();
      delete globalThis.seedFxModeTest;delete globalThis.fxModeTest;delete globalThis.fxModeDetachedEditor;
    });
  }
  assert.deepEqual(outcomes,expected,'Both native buttons must select the requested empty denomination for Enter, Space and pointer');
}
