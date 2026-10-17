import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

// Component fixtures are synthetic and run only against the isolated G3 server.
// Operator actions, normalisation, persistence and XLSX generation use real code.
export async function verifyOwnerUatChecks(page, sessionId, outputDir) {
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
  assert.equal((await cell.innerText()).trim(),(fixture.catalogPrice*3).toFixed(2));
  await cell.click();
  const editor=page.locator('[data-output-editor-field="unit_price_override"]');
  assert.equal(await editor.inputValue(),String(fixture.catalogPrice*3));
  await editor.fill('1');await editor.press('Enter');
  assert.equal((await cell.innerText()).trim(),'1.00');
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
  assert.equal((await cell.innerText()).trim(),'1.00');
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
  const bytes=await fs.readFile(target),sha256=createHash('sha256').update(bytes).digest('hex');
  assert.equal(sha256,saved.exports.xlsx.sha256);
  assert.equal(saved.exports.xlsx.stale,false);
  const proof={status:'OWNER_UAT_C17_RENDERED_SERVER_CURRENT_XLSX_PASS',sessionId,fixture,fx:3,manualQuoteUnitPrice:1,approvedQuoteAmount:2,bytes:bytes.length,sha256,providerCalls:0,seed:'Existing local G3 mock response plus explicit component fixture; no live injection'};
  await fs.writeFile(path.join(outputDir,'owner-uat-c17-proof.json'),JSON.stringify(proof,null,2));
  return proof;
}
