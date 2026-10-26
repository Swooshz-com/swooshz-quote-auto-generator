const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("webapp/static/app.js", "utf8");
function extract(name) {
  const match = new RegExp("(?:async )?function " + name + "\\(").exec(source);
  assert(match, "Missing production function " + name);
  const rest = source.slice(match.index);
  const end = rest.slice(1).search(/\n(?:async )?function /);
  return end < 0 ? rest : rest.slice(0, end + 1);
}
async function generationOutputCases() {
  function productionContext() {
    const names = [...source.matchAll(/^(?:async )?function ([A-Za-z_$][\w$]*)\(/gm)].map(match => match[1]);
    const functions = names.map(name => {
      const start = new RegExp('^(?:async )?function ' + name + '\\(', 'm').exec(source).index;
      const rest = source.slice(start);
      return rest.slice(0, /^\}/m.exec(rest).index + 1);
    });
    const constants = source.slice(0, source.indexOf('const state =')) + '\n' + source.match(/^const GENERIC_FAILURE_MESSAGE = .*;$/m)[0];
    const c = vm.createContext({ console, Set, Map });
    vm.runInContext(constants + '\n' + functions.join('\n'), c);
    vm.runInContext(`var state = { quoteCommercialLifecycle:'RECOVERED', quoteCommercialSnapshot:{lifecycle:'RECOVERED'},
      activeSidePanel:'output', activeAppView:'quote', basisConfirmed:true, aiFailed:false,
      quoteBasisSections:[], pricingIssues:[], outputSortMode:'pricing_reference', outputRevision:0,
      quoteSessionId:'quote-synthetic-target', outputRows:[], lineItems:[] };
      var activeOutputEditor=null; var outputRowIdentities=new WeakMap();`, c);
    c.currentPricingReference = () => ({id:'synthetic-reference',source:'local',currency:'SGD',digest_sha256:'sha256:'+'a'.repeat(64),items:[]});
    c.collectQuoteCurrency = () => 'AUD'; c.collectQuoteExchangeRate = () => 1.37;
    c.selectedPricingReferenceCurrency = () => 'SGD';
    vm.runInContext(`var manual = {diagnostic_row_id:'row-0',source_basis_line_id:'manual-row',section:'General',description:'Synthetic handling',
      quantity:3,unit:'nos',price_mode:'Priced',unit_price_override:1.01};
      manual.pricing_authority=buildPricingAuthority('manual',manual,{price:1.01,priceCurrency:'AUD'});
      var rejected = {diagnostic_row_id:'row-1',source_basis_line_id:'rejected-row',section:'General',description:'Synthetic review',
      quantity:2,unit:'nos',price_mode:'Priced',pricing_keyword:''};
      rejected.pricing_authority=buildPricingAuthority('historical',rejected);
      var originals=[manual,rejected];`, c);
    return c;
  }
  let count = 0;
  for (const resumed of [false,true]) for (const terminal of ['failed','needs_confirmation','completed'])
    for (const contract of ['current','legacy','invalid','empty-legacy']) for (const prior of [false,true]) {
      const c = productionContext(); const st = c.state;
      vm.runInContext('state.outputRows=originals.map(outputRowFromLineItem);state.lineItems=originals.map(normalizeLineItem);',c);
      const before = JSON.stringify(st.outputRows);
      const data = JSON.parse(JSON.stringify({status:terminal,return_code:terminal==='completed'?0:2,
        pricing_matches:[{status:'matched',unit_price:'999999',amount:'999999'}],
        files:[{name:'quotation.xlsx',url:'/api/synthetic-current.xlsx'}]}));
      if (contract === 'current' || contract === 'invalid') {
        data.output_rows = {schema:'swooshz.quote-output-rows.v1', rows:JSON.parse(vm.runInContext('JSON.stringify(originals)',c))};
        data.output_rows.rows[0].status='manual-price';data.output_rows.rows[1].status='unmatched';
        if (contract === 'invalid') data.output_rows.rows[1].diagnostic_row_id = 'row-0';
      }
      c.resultPacket=JSON.stringify(data);
      vm.runInContext('var resultData=JSON.parse(resultPacket);',c);
      if (prior) st.downloadFile = {name:'quotation.xlsx',url:'/api/synthetic-prior.xlsx'};
      let downloads = 0; const statuses=[]; let polls=0;
      const noop=()=>{};
      Object.assign(c, {
        commitActiveOutputEditor:()=> 'none',quoteCommercialReviewRequired:()=>false,outputRowsValid:()=>({valid:true,errors:[]}),
        missingDetailFields:()=>[],quoteSessionHasFreshOutputExports:()=>prior,normalizeActiveJob:x=>x,
        newClientJobId:()=> 'job-synthetic-output',saveSessionState:noop,setWorkflowStage:noop,
        setResultStatus:label=>statuses.push(label),renderMessages:noop,clearPricingReviewMessages:noop,syncControlStates:noop,
        ensureQuoteSession:async()=>st.quoteSessionId,buildPayload:()=>({line_items:st.lineItems}),startJob:async()=>{assert.equal(st.activeJob.outputRowsSchema,'swooshz.quote-output-rows.v1');return{ok:true,data:{job_id:'job-synthetic-output'}};},
        currentGenerationContext:()=>({session_id:st.quoteSessionId,run_id:''}),transitionGenerationContext:noop,
        pollJob:async()=>{polls++;return{ok:true,data:{status:terminal==='needs_confirmation'?'needs_review':terminal,result:c.resultData}};},
        isInterruptedJobPoll:()=>false,clearActiveJob:()=>{st.activeJob=null;},
        renderPricingMatches:rows=>{assert.equal(rows,st.outputRows);},renderMatchSummary:noop,
        saveQuoteSessionDraftState:async()=>({session_id:st.quoteSessionId}),setSidePanel:noop,
        showExcelGeneratingModal:noop,hideExcelGeneratingModal:noop,generationLoadingModalOptions:()=>({}),generationFinalizingModalOptions:()=>({}),
        ensureSavedServerJobStarted:async()=>({ok:true}),showGeneratedExportReadyModal:()=>false,
        setDownloadFiles:()=>{downloads++;},
      });
      // Empty legacy state is observed after request admission, as on a terminal resume.
      if (contract === 'empty-legacy') c.pollJob = async()=>{polls++;st.outputRows=[];return {ok:true,data:{status:terminal,result:c.resultData}};};
      if (resumed) {st.activeJob={id:'job-synthetic-output',type:'generate',phase:'running',...(['legacy','empty-legacy'].includes(contract)?{}:{outputRowsSchema:'swooshz.quote-output-rows.v1'})};await c.resumeSavedJob();}
      else await c.handleGenerate();
      assert.equal(polls,1);
      if (contract !== 'empty-legacy') {
        if(terminal==='completed'||contract!=='current')assert.equal(JSON.stringify(st.outputRows),before,`${resumed}/${terminal}/${contract}/${prior}`);
        assert.equal(st.outputRows[0].source_basis_line_id,'manual-row');
        assert.equal(st.outputRows[0].pricing_authority.variant,'manual');
        assert.equal(st.outputRows[1].source_basis_line_id,'rejected-row');
        assert.equal(st.outputRows[1].pricing_authority.variant,'historical');
      }
      else assert.equal(st.outputRows.length,0);
      assert.equal(downloads, terminal==='completed' && (contract==='current'||(resumed&&contract==='legacy')) ? 1 : 0, `${resumed}/${terminal}/${contract}/${prior}`);
      if (['invalid','empty-legacy'].includes(contract)||(!resumed&&contract==='legacy'&&terminal!=='failed')) assert(statuses.includes('failed'));
      assert(!st.outputRows.some(row=>row.approved_quote_amount===999999));
      count++;
    }
  // Direct strict reader controls: unknown schemas, missing IDs and source collisions.
  for (const mutation of ['schema','source','authority','catalog-collision','truncated','extra','changed-id']) {
    const c=productionContext();
    vm.runInContext('var packet={status:"completed",output_rows:{schema:"swooshz.quote-output-rows.v1",rows:JSON.parse(JSON.stringify(originals))}};',c);
    vm.runInContext('state.lineItems=originals.map(normalizeLineItem);state.outputRows=originals.map(outputRowFromLineItem);',c);
    assert.equal(c.adoptGenerationOutputRows(c.packet).status,'completed','Positive control must validate before mutation');
    if(mutation==='truncated')c.packet.output_rows.rows.pop();
    if(mutation==='extra')c.packet.output_rows.rows.push({...c.packet.output_rows.rows[0],diagnostic_row_id:'row-2'});
    if(mutation==='changed-id')c.packet.output_rows.rows[0].source_basis_line_id='wrong';
    if(mutation==='schema')c.packet.output_rows.schema='unknown';
    if(mutation==='source')delete c.packet.output_rows.rows[0].source_basis_line_id;
    if(mutation==='authority')delete c.packet.output_rows.rows[0].pricing_authority;
    if(mutation==='catalog-collision')c.packet.output_rows.rows.forEach(row=>{row.source_basis_line_id='same';row.pricing_authority.variant='catalog';});
    assert.equal(c.adoptGenerationOutputRows(c.packet).status,'failed');
  }
  const emptyCurrent=productionContext();
  vm.runInContext('state.lineItems=originals.map(normalizeLineItem);var emptyPacket={status:"completed",files:[{name:"quotation.xlsx"}],output_rows:{schema:"swooshz.quote-output-rows.v1",rows:originals}};',emptyCurrent);
  const emptyRefusal=emptyCurrent.adoptGenerationOutputRows(emptyCurrent.emptyPacket);
  assert.equal(emptyRefusal.status,'failed');assert.equal(emptyRefusal.files.length,0);assert.equal(emptyCurrent.state.outputRows.length,0);
  let duplicateCases=0;
  for(const outcome of ['success','different-reference','failure','malformed','malformed-snapshot','operation','scope','epoch','target','auth','departure']) {
    const c=productionContext(); const st=c.state; let scope='scope-synthetic';
    vm.runInContext(`var browserRecoveryEpoch=1;
      var sourceDraft={lineItems:originals.map(row=>({...row,quantity:3})),outputRows:[{...manual,quantity:7}],
        originalOutputRows:[{...manual,quantity:4},{...manual,source_basis_line_id:'reset-only',quantity:9,
          pricing_authority:{variant:'catalog'},catalog_unit_price:99,unit_price_override:99}],
        originalAnalysisSnapshot:{pricing_analysis_seal:{source:'bound-to-original'}}};`,c);
    const actualApplySnapshot=c.applyQuoteSessionSnapshot;
    if(outcome==='malformed-snapshot')vm.runInContext('sourceDraft.version=QUOTE_SESSION_STATE_VERSION;sourceDraft.originalAnalysisSnapshot.line_items={invalid:true};',c);
    const sourceReference=c.currentPricingReference;
    if(outcome==='different-reference'){c.currentPricingReference=()=>({...sourceReference(),currency:'EUR'});c.collectQuoteCurrency=()=> 'EUR';}
    const sourceBefore=JSON.stringify(c.sourceDraft); let saved, sent, release, requests=0;
    const gate=new Promise(resolve=>{release=resolve;}); const noop=()=>{};
    Object.assign(c, {
      currentBrowserRecoveryScope:()=>scope,commitActiveOutputEditor:()=> 'none',appIsBusy:()=>false,
      safeQuoteSessionId:x=>x,newClientQuoteSessionId:()=> 'quote-target',
      saveDashboardOperation:op=>op,clearQuoteSessionDraftSaveTimer:noop,setDashboardLoadingState:noop,dashboardOperationLoadingOptions:()=>({}),
      loadQuoteSessionDetail:async()=>({session_id:'quote-original',draft_state:c.sourceDraft,draft_files:[]}),
      mergeDashboardDraftSummaryDetails:d=>d,hydrateDashboardDraftImagePayloads:d=>d,
      applyQuoteSessionSnapshot:async snapshot=>{Object.assign(st,snapshot);c.currentPricingReference=sourceReference;c.collectQuoteCurrency=()=> 'AUD';return true;},
      buildLineItemNormalizePayload:()=>({line_items:st.lineItems,quote_session:{session_id:st.quoteSessionId},
        pricing_analysis_seal:st.originalAnalysisSnapshot?.pricing_analysis_seal}),
      postJson:async(path,payload)=>{assert.equal(path,'/api/line-items/normalize');requests++;sent=payload;await gate;
        return outcome==='failure'?{ok:false,data:{status:'failed'}}:{ok:true,data:{line_items:outcome==='malformed'?[]:payload.line_items}};},
      saveCurrentQuoteSession:async()=>{saved=JSON.stringify({lineItems:st.lineItems,outputRows:st.outputRows,originalOutputRows:st.originalOutputRows});return{session_id:'quote-target'};},
      clearDashboardOperation:()=>{st.dashboardOperation=null;},finishDashboardOperation:()=>{st.dashboardOperation=null;},dashboardRestoreError:noop,
      renderQuoteDashboard:noop,scrollDashboardSessionIntoView:noop,
    });
    if(outcome==='malformed-snapshot')c.applyQuoteSessionSnapshot=actualApplySnapshot;
    const pending=c.duplicateDashboardQuote('quote-original');
    if(outcome==='malformed-snapshot') {
      assert.equal(await pending,false);assert.equal(requests,0);assert.equal(saved,undefined);
      assert.equal(JSON.stringify(c.sourceDraft),sourceBefore);assert.equal(st.quoteSessionId,'quote-synthetic-target');
      assert.equal(st.dashboardOperation,null);assert.equal(st.quoteSessionRestoreBusy,false);
      duplicateCases++;continue;
    }
    await new Promise(setImmediate);assert.equal(requests,1,outcome);
    assert.equal(sent.line_items.length,1,'Current Output subset is authoritative for copy');
    assert.equal(sent.line_items[0].quantity,7,'Output edit survives stale analysis lineItems');
    assert.equal(sent.pricing_analysis_seal,null);
    assert.equal(sent.line_items[0].pricing_authority.variant,'manual');
    assert.equal(sent.line_items[0].pricing_authority.price_currency,'AUD');
    if(outcome==='operation')st.dashboardOperation={type:'different-operation'};
    if(outcome==='scope')scope='scope-new';
    if(outcome==='epoch')vm.runInContext('browserRecoveryEpoch++;',c);
    if(outcome==='target')st.quoteSessionId='quote-unrelated';
    if(outcome==='auth')st.authenticationRecoveryRequested=true;
    if(outcome==='departure')st.isPageUnloading=true;
    if(['operation','scope','epoch','target','auth','departure'].includes(outcome))st.outputRows=[{source_basis_line_id:'unrelated-sentinel'}];
    release(); const ok=await pending;
    assert.equal(ok,['success','different-reference'].includes(outcome),outcome);assert.equal(Boolean(saved),['success','different-reference'].includes(outcome));
    assert.equal(JSON.stringify(c.sourceDraft),sourceBefore,'Source object must be unchanged');
    if(['success','different-reference'].includes(outcome)) {
      assert.equal(st.outputRows.length,1);assert.equal(st.outputRows[0].quantity,7);
      assert.equal(st.originalOutputRows.length,2);assert.equal(st.originalOutputRows[0].quantity,4);
      assert.equal(st.originalOutputRows[1].quantity,9);assert.notEqual(st.originalOutputRows[1].pricing_authority.variant,'catalog');
      assert.equal(c.pricingAuthorityPrice(c.outputPriceAuthority(st.originalOutputRows[1])),null,'Reset cannot revive a source grant');
      Object.assign(c,{markOutputRowsDirty:noop,renderPricingMatches:noop,renderMatchSummary:noop,renderOutputValidationMessages:noop,
        outputRowsValid:()=>({errors:[]}),setResultStatus:noop,syncControlStates:noop});
      st.quoteSessionRestoreBusy=false;await c.resetOutputDraft();
      assert.equal(st.outputRows.length,2);assert.equal(st.outputRows[0].quantity,4);assert.equal(st.outputRows[1].quantity,9);
      assert.equal(c.pricingAuthorityPrice(c.outputPriceAuthority(st.outputRows[1])),null);
    } else if(['operation','scope','epoch','target','auth','departure'].includes(outcome))assert.equal(st.outputRows[0].source_basis_line_id,'unrelated-sentinel');
    duplicateCases++;
  }
  console.log(JSON.stringify({generation_output_cases:count,negative_reader_cases:8,duplicate_cases:duplicateCases,provider_requests:0}));
}

async function generationContractCases() {
  const schema='swooshz.quote-output-rows.v1';let cases=0;
  for(const route of ['direct','starting','running']) for(const marker of ['missing','valid','invalid'])
    for(const status of ['completed','needs_confirmation','failed']) for(const format of ['present','absent']) {
      let effects=0,saves=0;
      const c=vm.createContext({SERVER_JOB_TYPES:new Set(['generate','generate_pdf','draft']),ACTIVE_JOB_CLOCK_SKEW_MS:60000,
        PRICING_AUTHORITY_VARIANTS:new Set(['manual','catalog','included','none','historical']),GENERIC_FAILURE_MESSAGE:'Synthetic generic failure',
        currentBrowserRecoveryScope:()=> 'synthetic-scope',activeJobMaxAgeMs:()=>3600000,outputRowFromLineItem:row=>({...row})});
      vm.runInContext(['isPlainObject','sameLineItemSourceIdentities','adoptGenerationOutputRows','normalizeActiveJob','ensureSavedServerJobStarted'].map(extract).join('\n'),c);
      vm.runInContext(`var state={quoteSessionId:'quote-synthetic',outputRows:[{description:'Synthetic current row',quantity:1,unit:'nos',
        source_basis_line_id:'same',price_mode:'Priced',pricing_authority:{variant:'manual'}}],lineItems:[{source_basis_line_id:'same'}]};
        var operation={id:'job-synthetic-contract',type:'generate',phase:'${route==='running'?'running':'starting'}',startedAt:new Date().toISOString(),browserRecoveryScope:'synthetic-scope'};`,c);
      if(marker!=='missing')c.operation.outputRowsSchema=marker==='valid'?schema:'unsupported';
      const normalized=c.normalizeActiveJob(c.operation,{restoring:true});
      if(marker==='invalid'){assert.equal(normalized,null);cases++;continue;}
      c.state.activeJob=normalized;
      Object.assign(c,{saveSessionState:()=>{saves++;assert.equal(c.state.activeJob.outputRowsSchema,schema);},
        ensureQuoteSession:async()=>{effects++;assert.equal(c.state.activeJob.outputRowsSchema,schema,'Persist marker before save/dispatch');assert(saves>0);return 'quote-synthetic';},
        buildPayload:()=>({line_items:c.state.lineItems}),startJob:async()=>{effects++;assert.equal(c.state.activeJob.outputRowsSchema,schema);return {ok:true,data:{job_id:c.operation.id}};}});
      if(route!=='running')await c.ensureSavedServerJobStarted(normalized);
      assert.equal(effects,route==='running'?0:2);
      c.packetJson=JSON.stringify({status,files:status==='completed'?[{name:'quotation.xlsx'}]:[],
        ...(format==='present'?{output_rows:{schema,rows:[{diagnostic_row_id:'row-0',source_basis_line_id:'same',description:'Synthetic current row',
          quantity:1,unit:'nos',price_mode:'Priced',pricing_authority:{variant:'manual'}}]}}:{})});
      vm.runInContext('var resultPacket=JSON.parse(packetJson);',c);
      const allowLegacyRows=route==='running'&&marker==='missing';
      const result=c.adoptGenerationOutputRows(c.resultPacket,c.state.lineItems,{allowLegacyRows});
      const expected=status==='failed'?'failed':format==='absent'&&!allowLegacyRows?'failed':status;
      assert.equal(result.status,expected,`${route}/${marker}/${status}/${format}`);
      if(expected==='failed')assert.equal(result.files.length,0);
      assert.equal(c.state.outputRows[0].source_basis_line_id,'same');cases++;
    }
  console.log(JSON.stringify({generation_contract_cases:cases,provider_requests:0}));
}

async function resumedPdfStatusCases() {
  const rows = [];
  for (const scenario of ['current_pdf', 'missing_pdf', 'download_without_view', 'stale_no_entry', 'postcommit_warning', 'pricing_review', 'excel', 'workbook_unavailable', 'no_files']) {
    const calls = { resumedStart: 0, poll: 0, persistentSave: 0, localSave: 0, updateDownload: 0, modalAttempt: 0, readyShown: 0, statuses: [], messages: [] };
    const state = { activeJob: { id: 'job-synthetic-resume', type: scenario === 'excel' ? 'generate' : 'generate_pdf', phase: 'running', viewPdf: scenario !== 'excel' }, outputRevision: 5, outputRows: [{price_mode:'Priced',description:'Synthetic saved row',quantity:1,unit:'nos',pricing_authority:{variant:'manual'}}], lineItems:[], pdfFile: null };
    const xlsx = { name: 'quotation.xlsx', url: '/api/quote-sessions/quote-synthetic/download/xlsx' };
    const pdf = { name: 'quotation.pdf', url: '/api/quote-sessions/quote-synthetic/download/pdf' };
    const current = scenario === 'current_pdf';
    if (scenario === 'workbook_unavailable') state.activeJob.viewPdf = true;
    const data = { status: scenario === 'pricing_review' ? 'needs_confirmation' : 'completed',
      files: scenario === 'no_files' ? [] : [xlsx, ...(['current_pdf', 'download_without_view'].includes(scenario) ? [pdf] : [])],
      quote_session: { session_id: 'quote-synthetic', exports: { xlsx: { url: xlsx.url }, pdf: { url: current ? pdf.url : null, view_url: current ? '/api/quote-sessions/quote-synthetic/view/pdf' : null } } } };
    if (scenario === 'workbook_unavailable') data.export_status = { pdf_status: 'workbook_export_unavailable' };
    if (scenario === 'postcommit_warning') {
      data.post_commit_projection = { code: 'post_commit_projection_failed', retryable: true, message: 'Generation completed, but the session view could not be refreshed. Refresh to retry.' };
      data.committed_files = [xlsx, pdf];
    }
    const context = vm.createContext({ state, Promise,
      normalizeActiveJob: value => value, discardInvalidRestoredActiveJob() {},
      setWorkflowStage() {}, setSidePanel() {}, showExcelGeneratingModal() {}, hideExcelGeneratingModal() {},
      generationLoadingModalOptions: () => ({}), generationFinalizingModalOptions: () => ({}),
      syncControlStates() {}, clearActiveJob: () => { state.activeJob = null; },
      ensureSavedServerJobStarted: async () => { calls.resumedStart++; return { ok: true }; },
      pollJob: async () => { calls.poll++; return { ok: true, data: { status: 'completed', result: data } }; },
      isInterruptedJobPoll: () => false, currentGenerationContext: () => ({ session_id: 'quote-synthetic', run_id: 'run-synthetic' }),
      safeQuoteSessionId: value => value, transitionGenerationContext() {}, PRICING_AUTHORITY_VARIANTS:new Set(['manual']), isPlainObject: value => !!value && typeof value === 'object' && !Array.isArray(value),
      setResultStatus: (label, tone) => calls.statuses.push({ label, tone }),
      renderMessages: (messages, tone) => calls.messages.push({ messages, tone }),
      clearPricingReviewMessages() {}, renderPricingMatches() {}, renderMatchSummary() {},
      saveSessionState: () => { calls.localSave++; },
      saveQuoteSessionDraftState: async () => { calls.persistentSave++; },
      updateDownloadButton: () => { calls.updateDownload++; },
      showGeneratedExportReadyModal: () => { calls.modalAttempt++; const ready = !!state.pdfFile; if (ready) calls.readyShown++; return ready; },
    });
    vm.runInContext(['adoptGenerationOutputRows', 'resumeSavedJob', 'setDownloadFiles', 'revisionNumber', 'postCommitProjectionWarningFrom', 'postCommitProjectionWarningMessage'].map(extract).join('\n'), context);
    await context.resumeSavedJob();
    assert.equal(calls.resumedStart, 1); assert.equal(calls.poll, 1);
    assert.equal(state.isGenerating, false); assert.equal(state.activeJob, null);
    if (scenario === 'postcommit_warning') {
      assert.equal(calls.persistentSave, 0); assert.equal(calls.localSave, 1); assert.equal(calls.modalAttempt, 0);
    } else assert.equal(calls.persistentSave, 1);
    const final = calls.statuses.at(-1);
    const expected = scenario === 'excel' ? 'Completed' : current ? 'PDF ready' : scenario === 'postcommit_warning' ? 'Completed - refresh needed' : scenario === 'pricing_review' ? 'Needs pricing review' : 'PDF unavailable';
    assert.equal(final.label, expected, scenario);
    if (!['pricing_review', 'no_files'].includes(scenario)) assert(state.downloadFile, scenario + ' retains Excel');
    if (scenario === 'no_files') {
      assert.equal(state.downloadFile, null);
      assert(!calls.messages.at(-1).messages[0].includes('Download Excel is still available'));
    } else if (['missing_pdf', 'download_without_view', 'stale_no_entry', 'workbook_unavailable'].includes(scenario)) {
      assert(calls.messages.at(-1).messages[0].endsWith('Download Excel is still available.'));
    }
    if (scenario === 'workbook_unavailable') assert(calls.messages.at(-1).messages[0].startsWith('Workbook PDF export was unavailable'));
    if (['missing_pdf', 'download_without_view', 'stale_no_entry', 'workbook_unavailable', 'no_files'].includes(scenario)) assert(!calls.statuses.some(item => item.label === 'PDF ready'));
    rows.push({ scenario, expected_status: expected, actual_status: final.label, actual_tone: final.tone,
      pdf_file_present: !!state.pdfFile, excel_file_present: !!state.downloadFile, calls,
      contract_pass: final.label === expected });
  }
  assert(rows.every(row => row.contract_pass));
  console.log(JSON.stringify({ resume_status_cases: rows }));
}

function fixture(names) {
  const calls = [];
  const node = () => ({ hidden: true, textContent: "", dataset: {}, focus() {}, classList: { add() {}, remove() {} } });
  const elements = Object.fromEntries(["excelGeneratingModal", "excelGeneratingSpinner", "excelGeneratingActions", "excelGeneratingActionButton", "excelGeneratingCloseButton", "excelGeneratingEyebrow", "excelGeneratingTitle", "excelGeneratingMessage"].map((name) => [name, node()]));
  elements.sideNextButton = { getAttribute() { return "false"; } };
  const state = { isPreparingOutput: false, activeSidePanel: "customer", quoteSessionLoadError: "" };
  const context = vm.createContext({ state, elements, calls, Promise,
    appIsBusy: () => state.isPreparingOutput,
    GENERIC_FAILURE_MESSAGE: "Failed. Please try again.",
    newClientErrorReference: () => "ERR-1234ABCD",
    genericFailureMessage: () => "Failed. Reference: ERR-1234ABCD.",
    genericFailureMessages: () => ["Failed. Reference: ERR-1234ABCD."],
    logClientEvent: (_event, details) => { calls.push({ errorReference: details.error_reference }); },
    syncControlStates() {}, waitForUiPaint: async () => { calls.push("paint"); },
    showExcelGeneratingModal() { elements.excelGeneratingModal.hidden = false; elements.excelGeneratingSpinner.hidden = false; calls.push("show"); },
    hideExcelGeneratingModal() { elements.excelGeneratingModal.hidden = true; calls.push("hide"); },
    commitActiveOutputEditor: () => "none", activeSidePanelIndex: () => 1,
    quoteDependencyChangesForNext: () => [], quoteHasDerivedResults: () => false,
    applyPendingPricingReferenceSelection() {}, advanceCustomerStep: async () => true,
    requestStartAnalysis() { calls.push("analysis_choice"); },
    requestQuoteDependencyChangeConfirmation() { calls.push("dependency_choice"); },
  });
  vm.runInContext(names.map(extract).join("\n"), context);
  return { context, calls, state, elements };
}

// Ordinary beforeunload is tentative: Chrome can stop before pagehide/commit.
// Dispatch the real registered handlers, rather than injecting a terminal flag.
function lifecycleFixture(names = []) {
  const f = fixture(["showExcelGeneratingModal", "hideExcelGeneratingModal", "markPageUnloading", "handleBeforeUnload", ...names]);
  const { context: c, state, elements } = f;
  const handlers = new Map();
  state.isPageUnloading = false;
  state.isRecoveryScopeTransitioning = false;
  state.lineItems = [{}]; state.outputRows = []; state.pendingQuoteDependencyChanges = ["pricing_reference"];
  c.window = { addEventListener: (event, handler) => handlers.set(event, handler) };
  c.pricingReferenceShouldWarnBeforeUnload = () => false;
  vm.runInContext(source.split(/\r?\n/).filter(line => /^  window\.addEventListener\("(?:beforeunload|pagehide)"/.test(line)).join("\n"), c);
  assert.equal(handlers.get("beforeunload"), c.handleBeforeUnload);
  assert.equal(handlers.get("pagehide"), c.markPageUnloading);
  f.beforeunload = () => handlers.get("beforeunload")({ preventDefault() { throw Error("Unexpected warning"); } });
  f.pagehide = () => handlers.get("pagehide")({ persisted: false });
  return f;
}
async function lifecycleCases() {
  const families = ["images", "customer", "quote_company", "dependency_customer", "dependency_images", "dependency_basis", "basis"];
  let cases = 0;
  for (const family of families) for (const phase of ["ordinary", "cancel_before", "cancel_pending", "pagehide_pending"]) for (const outcome of ["success", "failure", "throw"]) {
    const f = lifecycleFixture(["withQuoteStepLoading", "advanceCustomerStep", "goToNextSidePanel", "confirmQuoteDependencyChange", "confirmBasis"]);
    const { context: c, state, elements } = f;
    const panel = family.replace("dependency_", "");
    const panels = ["images", "customer", "quote_company", "basis", "output"];
    state.activeSidePanel = panel;
    c.SIDE_PANEL_SEQUENCE = panels;
    c.activeSidePanelIndex = () => panels.indexOf(state.activeSidePanel);
    c.setSidePanel = next => { state.activeSidePanel = next; return true; };
    c.quoteHasDerivedResults = () => true;
    c.quoteSessionHasFreshOutputExports = () => false;
    c.ensureClientQuoteSessionId = () => "quote-test";
    c.customerStepCheckpointForCurrent = () => ({ sessionId: "quote-test" });
    c.hasReferenceFilesForNavigation = () => true;
    c.markQuoteSessionDraftSaveStartedAfterCustomerStep = () => { state.quoteSessionDraftSaveStarted = true; };
    c.currentQuoteSessionDraftState = () => ({});
    c.safeQuoteSessionId = id => id;
    c.quoteCommercialStrictDataEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    c.pendingPricingReferenceSelectionChanged = () => false;
    c.saveSessionState = () => {};
    c.updateSidePanelNav = () => {};
    c.hideQuoteDependencyConfirmModal = () => {};
    c.clearGeneratedQuoteState = () => {};
    c.setWorkflowStage = () => {};
    c.applyPendingPricingReferenceSelection = () => false;
    c.normalizeActiveJob = value => value;
    c.newClientOperationId = () => "operation-test";
    c.basisConfirmBlockReason = () => "";
    c.missingDetailFields = () => [];
    c.clearActiveJob = () => { state.activeJob = null; };
    c.showBlockedBasisAction = () => {};
    c.refreshOutputRowsFromLineItems = () => {};
    c.snapshotOutputRows = rows => rows;
    c.outputRowsToLineItems = () => [{}];
    c.renderPricingMatches = () => {};
    c.renderMatchSummary = () => {};
    c.setResultStatus = () => {};
    c.outputRowsValid = () => ({ errors: [] });
    c.renderOutputValidationMessages = () => {};
    let release, requests = 0;
    const gate = new Promise(resolve => { release = resolve; });
    async function operation() {
      requests++; await gate;
      if (outcome === "throw") throw Error("synthetic-controlled-error");
      if (outcome === "failure") { state.quoteSessionLoadError = "Synthetic failure. Reference: ERR-1234ABCD."; return null; }
      return { session_id: "quote-test" };
    }
    c.saveQuoteSessionDraftState = family === "basis" ? async () => ({ session_id: "quote-test" }) : operation;
    c.startQuoteSessionDraftSaveAfterCustomerStep = operation;
    c.refreshLineItemsFromServer = async () => ({ ok: Boolean(await operation()), data: {} });
    if (phase === "cancel_before") f.beforeunload();
    const pending = family.startsWith("dependency_") ? c.confirmQuoteDependencyChange() : c.goToNextSidePanel();
    // Attach rejection handling before releasing the synthetic transport.
    const settled = pending.then(value => ({ value }), error => ({ error }));
    await new Promise(setImmediate);
    assert.equal(requests, 1, family);
    assert(state.isPreparingOutput && !elements.excelGeneratingModal.hidden);
    await c.goToNextSidePanel();
    assert.equal(requests, 1, "Busy clicks cannot dispatch another save");
    if (phase === "cancel_pending") f.beforeunload();
    if (phase === "pagehide_pending") { f.beforeunload(); f.pagehide(); }
    assert.equal(state.isPageUnloading, phase === "pagehide_pending");
    release(); const result = await settled;
    assert.equal(state.isPreparingOutput, false);
    assert.equal(requests, 1);
    if (phase !== "pagehide_pending") {
      if (family === "basis" || outcome === "success") assert(elements.excelGeneratingModal.hidden, family + ": terminal loading must close");
      else assert(!elements.excelGeneratingModal.hidden && elements.excelGeneratingSpinner.hidden && !elements.excelGeneratingActions.hidden);
    }
    if (family === "basis" && outcome === "throw") assert(result.error);
    if (family === "customer" && outcome !== "success") assert.equal(state.activeSidePanel, "customer");
    cases++;
  }
  const f = lifecycleFixture(["withQuoteStepLoading", "goToNextSidePanel", "postJsonFetchFailure", "jsonFromResponse", "getJson", "fetchCompanyProfileExport", "finishDashboardOperation"]);
  const { context: c, state, calls } = f;
  let logged = 0, cleared = 0;
  c.logClientEvent = () => { logged++; };
  c.fetchFailureLogDetails = url => ({ url, reason: "fetch_failed" });
  c.safeProfileId = value => value;
  c.clearDashboardOperation = () => { cleared++; };
  c.fetch = async () => { throw Error("synthetic-fetch-failure"); };
  f.beforeunload();
  assert.equal(c.postJsonFetchFailure("/synthetic").data.page_unloading, false);
  assert.equal((await c.getJson("/synthetic")).data.page_unloading, false);
  await c.fetchCompanyProfileExport("profile-test");
  c.finishDashboardOperation(); assert.equal(cleared, 1); assert.equal(logged, 3);
  const abortedBody = { json: async () => { const error = Error("synthetic-body"); error.name = "AbortError"; throw error; } };
  assert.equal((await c.jsonFromResponse(abortedBody, { persistSession: false })).page_unloading, false);
  f.pagehide();
  assert.equal(c.postJsonFetchFailure("/synthetic").data.page_unloading, true);
  assert.equal((await c.getJson("/synthetic")).data.page_unloading, true);
  await c.fetchCompanyProfileExport("profile-test");
  c.finishDashboardOperation(); assert.equal(cleared, 1); assert.equal(logged, 3);
  assert.equal((await c.jsonFromResponse(abortedBody, { persistSession: false })).page_unloading, true);
  const mode = lifecycleFixture(["withQuoteStepLoading", "goToNextSidePanel"]);
  mode.beforeunload(); mode.state.activeSidePanel = "quote_company";
  await mode.context.goToNextSidePanel(); assert(mode.calls.includes("analysis_choice"));
  mode.state.activeSidePanel = "output"; const before = mode.calls.length;
  await mode.context.goToNextSidePanel(); assert.equal(mode.calls.length, before);
  const transfer = lifecycleFixture(); transfer.state.isRecoveryScopeTransitioning = true;
  transfer.beforeunload(); assert.equal(transfer.state.isPageUnloading, true, "Explicit scope transfer fence remains terminal");
  for (const departure of ["stopped", "pagehide", "signout"]) {
    const auth = lifecycleFixture(["recoverRequiredAuthentication"]);
    const a = auth.context, s = auth.state;
    a.authenticationRecoveryPromise = null; a.browserRecoveryEpoch = 1;
    a.currentBrowserRecoveryScope = () => "synthetic-scope";
    a.clearQuoteSessionDraftSaveTimer = () => {};
    a.renderAuthState = () => {};
    a.renderMessages = () => {};
    a.commitActiveOutputEditor = () => "blocked";
    s.authenticated = true; s.csrfToken = "synthetic-token";
    auth.beforeunload();
    if (departure === "pagehide") auth.pagehide();
    if (departure === "signout") s.isSigningOut = true;
    assert.equal(await a.recoverRequiredAuthentication(401, { status: "auth_required" }), true);
    assert.equal(Boolean(s.authenticationRecoveryRequested), departure === "stopped");
    assert.equal(s.csrfToken, departure === "stopped" ? "" : "synthetic-token");
  }
  console.log(JSON.stringify({ lifecycle_cases: cases, handler_events: "actual beforeunload/pagehide", provider_requests: 0 }));
}

(async () => {
  const mode = process.argv[2];
  if (mode === "loading") {
    await lifecycleCases();
    const { context: c, calls, state, elements } = fixture(["withQuoteStepLoading"]);
    let release;
    const pending = new Promise((resolve) => { release = resolve; });
    const first = c.withQuoteStepLoading(async () => { calls.push("request"); return pending; });
    await new Promise(setImmediate);
    assert.deepEqual(calls, ["show", "paint", "request"]);
    assert(state.isPreparingOutput && !elements.excelGeneratingModal.hidden);
    assert.equal(await c.withQuoteStepLoading(() => { throw Error("duplicate dispatched"); }), false);
    release(true);
    assert.equal(await first, true);
    assert(!state.isPreparingOutput && elements.excelGeneratingModal.hidden);
    assert.equal(await c.withQuoteStepLoading(async () => { throw Error("private-controlled-failure"); }), false);
    assert(!state.isPreparingOutput && !elements.excelGeneratingModal.hidden);
    assert(!JSON.stringify(calls).includes("private-controlled-failure"));
    assert(calls.some((entry) => entry.errorReference === "ERR-1234ABCD"));
    assert.equal(await c.withQuoteStepLoading(async () => true), true);
    assert.equal(await c.withQuoteStepLoading(async () => {
      state.quoteSessionLoadError = "Quote could not be saved. Reference: ERR-1234ABCD.";
      return false;
    }), false);
    assert(!state.isPreparingOutput && !elements.excelGeneratingModal.hidden);
    assert(elements.excelGeneratingSpinner.hidden && !elements.excelGeneratingActions.hidden);
  } else if (mode === "admission") {
    const { context: c, calls, state } = fixture(["withQuoteStepLoading", "goToNextSidePanel"]);
    c.elements.sideNextButton.getAttribute = () => "true";
    c.elements.sideNextButton.blur = () => {};
    await c.goToNextSidePanel();
    assert.deepEqual(calls, []);
    c.elements.sideNextButton.getAttribute = () => "false";
    c.quoteDependencyChangesForNext = () => ["pricing_reference"];
    await c.goToNextSidePanel();
    assert.deepEqual(calls, ["dependency_choice"]);
    calls.length = 0;
    c.quoteDependencyChangesForNext = () => [];
    state.activeSidePanel = "quote_company";
    await c.goToNextSidePanel();
    assert.deepEqual(calls, ["analysis_choice"]);
    calls.length = 0;
    state.activeSidePanel = "basis";
    let release, settled = false;
    c.confirmBasis = () => new Promise((resolve) => { release = resolve; });
    const confirmation = c.goToNextSidePanel().then(() => { settled = true; });
    await Promise.resolve();
    assert.equal(settled, false, "Next must await the nested basis operation");
    release(); await confirmation;
    assert.equal(settled, true);
    assert.deepEqual(calls, []);
  } else if (mode === "detail") {
    const { context: c, state } = fixture(["loadQuoteSessionDetail"]);
    c.safeQuoteSessionId = (id) => id;
    c.genericFailureMessage = () => "Failed. Reference: ERR-1234ABCD.";
    c.renderQuoteDashboard = () => {};
    for (const response of [{ ok: false, data: {} }, { ok: true, data: {} }, { ok: true, data: { quote_session: { session_id: "wrong" } } }]) {
      c.getJson = async () => response;
      assert.equal(await c.loadQuoteSessionDetail("quote-test"), null);
      assert(state.quoteSessionLoadError);
    }
    c.getJson = async () => ({ ok: true, data: { quote_session: { session_id: "quote-test", draft_state: {} } } });
    assert.equal((await c.loadQuoteSessionDetail("quote-test")).session_id, "quote-test");
    assert.equal(state.quoteSessionLoadError, "");
  } else if (mode === "pdf") {
    const { context: c, calls, state, elements } = fixture(["revisionNumber", "pdfFileIsFresh", "handleViewPdf"]);
    elements.sideViewPdfButton = { getAttribute: () => "false" };
    state.outputRevision = 5;
    state.pdfFileRevision = 5;
    state.pdfFile = { url: "/api/quote-sessions/quote-test/view/pdf" };
    c.viewCurrentPdfFile = (file = state.pdfFile, prepared) => { calls.push({ viewed: file.url, prepared: Boolean(prepared) }); return true; };
    c.handleGenerate = async () => { throw Error("fresh PDF must not regenerate"); };
    const event = { preventDefault() {} };
    await c.handleViewPdf(event);
    assert.deepEqual(calls, [{ viewed: state.pdfFile.url, prepared: false }]);
    calls.length = 0;
    let popup;
    c.window = { open(url) { assert.equal(url, "about:blank"); popup = { closed: false, close() { this.closed = true; } }; return popup; } };
    c.generationLoadingModalOptions = () => ({});
    state.pdfFileRevision = 4;
    c.handleGenerate = async () => false;
    await c.handleViewPdf(event);
    assert(popup.closed && elements.excelGeneratingModal.hidden, "failed generation must close the waiting tab and spinner");
    assert(!calls.some((entry) => entry.viewed), "stale PDF was viewed");
    c.handleGenerate = async () => { throw Error("controlled PDF failure"); };
    await assert.rejects(c.handleViewPdf(event), /controlled PDF failure/);
    assert(popup.closed && elements.excelGeneratingModal.hidden);
    c.handleGenerate = async () => { state.pdfFile = { url: "/api/quote-sessions/quote-test/view/pdf?current" }; state.pdfFileRevision = 5; return true; };
    calls.length = 0;
    await c.handleViewPdf(event);
    assert(!popup.closed && calls.some((entry) => entry.viewed === state.pdfFile.url && entry.prepared));
    assert(elements.excelGeneratingModal.hidden);
    calls.length = 0;
    elements.sideViewPdfButton.getAttribute = () => "true";
    c.outputRowsValid = () => ({ valid: false, errors: ["Review required"] });
    c.renderOutputValidationMessages = () => { calls.push("blocked"); };
    await c.handleViewPdf(event);
    assert.deepEqual(calls, ["blocked"]);
  } else if (mode === "ack") {
    for (const panel of ["images", "quote_company"]) {
      const { context: c, state, elements } = fixture(["withQuoteStepLoading", "goToNextSidePanel"]);
      const panels = ["images", "customer", "quote_company", "basis", "output"];
      c.SIDE_PANEL_SEQUENCE = panels;
      c.activeSidePanelIndex = () => panels.indexOf(state.activeSidePanel);
      c.setSidePanel = (next) => { state.activeSidePanel = next; return true; };
      c.quoteHasDerivedResults = () => true;
      c.quoteSessionHasFreshOutputExports = () => false;
      let requests = 0, fail = true;
      const save = async () => { requests++; if (fail) { state.quoteSessionLoadError = "Could not be saved. Reference: ERR-1234ABCD."; return null; } return { session_id: "quote-test" }; };
      c.saveQuoteSessionDraftState = save;
      c.startQuoteSessionDraftSaveAfterCustomerStep = save;
      state.activeSidePanel = panel;
      assert.equal(await c.goToNextSidePanel(), false);
      assert.equal(state.activeSidePanel, panel, "failed Next save must restore its source step");
      assert(!state.isPreparingOutput && !elements.excelGeneratingModal.hidden && elements.excelGeneratingSpinner.hidden);
      assert.equal(requests, 1);
      fail = false;
      assert.equal(await c.goToNextSidePanel(), true);
      assert.equal(state.activeSidePanel, panel === "images" ? "customer" : "basis");
      assert(elements.excelGeneratingModal.hidden && !state.isPreparingOutput);
      assert.equal(requests, 2, "one retry must produce one save");
    }

  } else if (mode === "generation-contract") {
    await generationContractCases();
  } else if (mode === "generation-output") {
    await generationOutputCases();
  } else if (mode === "resume-status") {
    await resumedPdfStatusCases();
  } else if (mode === "checkpoint") {
    const { context: c, state } = fixture(["canonicalPricingAuthorityText", "customerStepCheckpointForCurrent"]);
    vm.runInContext(source.match(/^const PRICING_AUTHORITY_WHITESPACE_RE = .+;/m)[0], c);
    c.PRICING_REFERENCE_DIGEST_RE = /^sha256:[a-f0-9]{64}$/;
    c.safeQuoteSessionId = (id) => id;
    c.currentBrowserRecoveryScope = () => "synthetic-scope";
    c.currentPricingReference = () => ({ digest_sha256: "sha256:" + "a".repeat(64) });
    c.customerDetailsBlockReason = () => "";
    c.quoteCommercialReviewRequired = () => false;
    state.quoteSessionId = "quote-test";
    state.pricingReferenceId = "reference-test";
    state.pricingReferenceSource = "company";
    const details = { client: { name: "Synthetic", address: "Cafe\u0301 Street\nSingapore" }, project: { title: "Synthetic" }, quote_date: "2026-10-21", project_number: "TEST-1" };
    c.collectQuoteDetails = () => details;
    const original = details.client.address;
    const before = JSON.stringify(c.customerStepCheckpointForCurrent());
    assert.equal(details.client.address, original, "checkpoint normalisation must not alter visible/export address data");
    details.client.address = "Caf\u00e9 Street Singapore";
    assert.equal(JSON.stringify(c.customerStepCheckpointForCurrent()), before);
    details.client.address = "Different Street Singapore";
    assert.notEqual(JSON.stringify(c.customerStepCheckpointForCurrent()), before);
    c.customerDetailsBlockReason = () => "missing";
    assert.equal(c.customerStepCheckpointForCurrent(), null);
  } else throw Error("Unknown mode");
  console.log(JSON.stringify({ mode, status: "PASS" }));
})().catch((error) => { console.error(error); process.exitCode = 1; });
