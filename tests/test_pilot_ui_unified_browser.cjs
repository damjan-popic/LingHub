/* Isolated DOM acceptance: actual UI assets, intercepted synthetic API fixtures.
 * No listener, operational endpoint, credential, database or model is used.
 * This tests rendering/orchestration only; it is never live generation evidence.
 */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {selectComposerOption}=require('./pilot_ui_options.cjs');
const root=path.resolve(__dirname,'../pilot_ui'),origin='https://pilot.test';
const report={scope:'isolated browser with intercepted API fixtures; optional saved answer payloads are replayed without generation',live_requests:0,model_calls:0,checks:[],errors:[]};
let browser;
async function scenario(mode){
  const context=await browser.newContext({viewport:{width:1100,height:900}}),page=await context.newPage();
  const calls=[],frozen='SYNTHETIC ORIGINAL QUESTION?\n\nSYNTHETIC COMPLETE ANSWER. Final qualification.';
  let logged=true,polls=0,created=false,phase='retrieval',finishGeneration=false,cancelRequested=false,matchRequests=0;
  const used={evidence_id:'frozen-used',source_slug:'jezikovna_posta',source_label:'Jezikovna pošta / Druga pomoč',external_id:'posta:test',title:'Synthetic agriturismo',snippet:'Synthetic related advice.',used_in_answer:mode==='generated'};
  const other={evidence_id:'frozen-other',source_slug:'solska',source_label:'Šolska',external_id:'solska:test',title:'Synthetic additional <img src=x onerror=window.unsafe=1>',snippet:'Literal search reference [1], not a generated claim.',used_in_answer:false};
  const coverage=[{family:'editorial',status:'ok',returned_count:2,limit:20,has_more:null},{family:'reference',status:mode==='partial'?'timeout':'no_match',returned_count:0,limit:8,has_more:null},{family:'linghub_tools',source_label:'LingHub',status:'not_documentary',returned_count:0,limit:null,has_more:null}];
  function family(){const settled=phase==='complete',generated=mode==='generated'||mode==='partial',text=generated?'SYNTHETIC GENERATED FIXTURE with a supported claim and unknown [77]. <img src=x onerror=window.unsafe=1>':'SYNTHETIC EXCERPT FALLBACK — generation failed.';
    const blocks=mode==='generated'?[{block_id:'C1',paragraph_id:'P1',text,evidence_ids:['frozen-used']},{block_id:'C2',paragraph_id:'P1',text:'SYNTHETIC SECOND ASSERTION.',evidence_ids:['frozen-used']}]:[{block_id:'body',text,evidence_ids:generated?['frozen-used']:[]}];
    return {answer_family_id:'owned-family',question:'agriturismo',task:'reference',model:'qwen',locale:'sl',unified_answers:true,matches_available:phase!=='retrieval',long:null,
      short:{run_id:'synthetic-run',job_id:'synthetic-job',state:phase==='cancelled'?'cancelled':settled?'complete':'running',status:settled?generated?'validated':'source_only':undefined,model:'qwen',progress:{stage:phase},...(settled?{answer_method:generated?'generated':'source_only',generation_status:generated?'generated':'not_generated',blocks,display_sha256:'synthetic-display',evidence:generated?[{...used,used_in_answer:true,local_number:1}]:[],source_badges:generated?[{source_slug:used.source_slug,label:'POŠTA'}]:[],generation:{status:generated?'generated':'failed',provider:generated?{repository:'synthetic/test-provider',device:'cpu'}:null,elapsed_ms:123,first_content_ms:null},retrieval:{status:mode==='partial'?'partial':'complete',coverage,elapsed_ms:12,returned_count:2}}:{})}};
  }
  page.on('pageerror',e=>report.errors.push(e.message));
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());assert.equal(url.origin,origin,'No external network request is permitted');
    const fulfill=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    if(url.pathname.startsWith('/assets/')){const relative=url.pathname.slice('/assets/'.length);assert(!relative.includes('..'));return route.fulfill({contentType:relative.endsWith('.mjs')?'text/javascript':relative.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(root,relative))});}
    if(url.pathname==='/'||url.pathname==='/it/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__',url.pathname==='/it/'?'it':'sl')});
    assert(url.pathname.startsWith('/api/pilot/'));const api=url.pathname.slice('/api/pilot'.length);calls.push({path:api,method:req.method(),query:url.search});
    if(api==='/session')return fulfill(logged?{authenticated:true,csrf_token:'synthetic-csrf',user:{id:'synthetic-owner',username:'synthetic-owner',role:'tester',must_change_password:false},capabilities:{tasks:['reference','terminology','jezikovna_posta'],models:['qwen','gemma'],comparisons:false,unified_answers:true}}:{authenticated:false});
    if(api==='/capabilities')return fulfill({unified_answers:true,generation:{models:{qwen:{available:true,device:'cpu'},gemma:{available:false}}}});
    if(api==='/feedback')return fulfill({feedback:[]});
    if(api==='/families'&&req.method()==='POST'){const data=req.postDataJSON();assert.equal(data.question,'agriturismo');assert.equal(data.task,'reference');assert.equal(data.model,'qwen');assert.equal(data.input_language,'und');created=true;return fulfill({family:family(),job:{job_id:'synthetic-job',state:'queued'}});}
    if(api==='/jobs/synthetic-job'){assert(created);polls++;phase=polls===1?'retrieval':finishGeneration?cancelRequested?'cancelled':'complete':'generation';return fulfill({job_id:'synthetic-job',state:['complete','cancelled'].includes(phase)?phase:'running',cancel_requested:cancelRequested,cancellation_mode:cancelRequested?'finish_current_operation':null,model:'qwen',progress:{stage:phase,matches_ready:phase!=='retrieval'}});}
    if(api==='/jobs/synthetic-job/cancel'){assert.equal(req.method(),'POST');assert.equal(phase,'generation');cancelRequested=true;return fulfill({job_id:'synthetic-job',state:'running',cancel_requested:true,cancellation_mode:'finish_current_operation',provider_interruption_supported:false,progress:{stage:'generation',matches_ready:true}});}
    if(api==='/families/owned-family')return fulfill(family());
    if(api==='/families/owned-family/matches'){assert(logged);matchRequests++;if(mode==='matches_retry'&&matchRequests===1)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'source_unavailable'})});const offset=Number(url.searchParams.get('offset'));assert.equal(url.searchParams.get('limit'),'20');assert([0,1].includes(offset));return fulfill({items:offset===0?[{...used,used_in_answer:phase==='complete'&&(mode==='generated'||mode==='partial')}]:[other],returned_count:2,offset,limit:20,next_offset:offset===0?1:null,coverage,corpus_total:null});}
    if(api.startsWith('/families/owned-family/sources/')){assert(logged);const eid=decodeURIComponent(api.split('/').at(-1));assert(['frozen-used','frozen-other'].includes(eid));return fulfill({evidence_id:eid,source_label:eid==='frozen-used'?used.source_label:other.source_label,title:eid==='frozen-used'?used.title:other.title,excerpt:frozen,url:null,bibliography:{record_id:eid,rights_mode:'restricted',source_date:null}});}
    if(api==='/runs/synthetic-run/delivered')return fulfill({acknowledged:true});
    if(api==='/logout'){logged=false;return fulfill({acknowledged:true});}
    throw Error('Unexpected intercepted request '+req.method()+' '+api);
  });
  try{
    await page.goto(origin+'/');await page.locator('#app-view').waitFor({state:'visible'});
    assert.equal(await page.locator('#options').isVisible(),false);assert.equal(await page.locator('#model').isVisible(),false);assert.equal(await page.locator('#task').isVisible(),false);assert.equal(await page.locator('#input-language').isVisible(),false);
    await page.locator('#question').fill('agriturismo');await page.locator('#ask-button').click();
    await page.waitForFunction(()=>document.querySelector('#job-message').textContent.includes('povezanih virih'));
    await page.waitForFunction(()=>document.querySelector('#job-message').textContent.includes('ohranjenih virov'));
    await page.locator('#matches-panel').waitFor({state:'visible'});
    if(mode==='matches_retry'){
      await page.waitForFunction(()=>document.querySelector('#matches-status').textContent.includes('ni mogoče odpreti'));
      assert.equal(matchRequests,1);finishGeneration=true;await page.waitForFunction(()=>document.querySelector('#job-state').hidden);
      await page.locator('.match-record').waitFor();assert.equal(matchRequests,2);
      assert.equal(await page.locator('#matches-panel').getAttribute('data-family-id'),'owned-family');
      report.checks.push({mode,status:'PASS',initial_list_failure_retried_once_on_completion:true,browser_api_fixtures_only:true});return;
    }
    await page.locator('.match-record').waitFor();
    assert.equal(await page.locator('#job-state').getAttribute('data-stage'),'generation');
    assert.equal(await page.locator('#matches-panel').getAttribute('data-family-id'),'owned-family');
    assert.equal(new URL(page.url()).searchParams.get('answer'),'owned-family');
    assert.equal(await page.locator('#short-answer').textContent(),'');
    assert.equal(await page.locator('.match-record[data-used=pending]').count(),1);
    assert((await page.locator('.match-use').innerText()).includes('še ni določena'));
    await page.locator('.match-record button').click();await page.locator('#source-content blockquote').waitFor();assert.equal(await page.locator('#source-content blockquote').textContent(),frozen);await page.locator('[data-close=source-dialog]').click();
    await page.locator('#matches-more').click();await page.waitForFunction(()=>document.querySelectorAll('.match-record').length===2);
    assert.equal(await page.locator('.match-record[data-used=pending]').count(),2);
    if(mode==='cancel'){
      await page.locator('#cancel-job').click();await page.waitForFunction(()=>document.querySelector('#job-message').textContent.includes('Preklic je zahtevan'));
      assert.equal(await page.locator('#cancel-job').isVisible(),false);
      assert.equal(await page.locator('#short-answer').textContent(),'');
    }
    finishGeneration=true;
    await page.waitForFunction(()=>document.querySelector('#job-state').hidden);
    if(mode==='cancel'){
      assert((await page.locator('#short-answer').textContent()).includes('preklicana'));
      assert.equal(await page.locator('#short-answer .citation').count(),0);
      assert.equal(await page.locator('.match-record').count(),2);
      await page.locator('.match-record button').first().click();await page.locator('#source-content blockquote').waitFor();assert.equal(await page.locator('#source-content blockquote').textContent(),frozen);
      assert.equal(calls.filter(c=>c.path==='/jobs/synthetic-job/cancel').length,1);
      assert.equal(calls.filter(c=>c.path==='/runs/synthetic-run/delivered').length,0);
      report.checks.push({mode,status:'PASS',cancel_request_reported_honestly:true,frozen_matches_after_cancel:true,browser_api_fixtures_only:true});return;
    }
    assert.equal(calls.filter(c=>c.path==='/families'&&c.method==='POST').length,1);
    assert.equal(await page.locator('#short-answer img').count(),0);assert.equal(await page.evaluate(()=>window.unsafe),undefined);
    assert.equal(await page.locator('#short-answer .citation').count(),mode==='failure'?0:mode==='generated'?2:1);
    if(mode==='generated'){
      assert.equal(await page.locator('#short-answer > p[data-paragraph-id=P1]').count(),1);
      assert.deepEqual(await page.locator('#short-answer [data-block-id]').evaluateAll(nodes=>nodes.map(n=>({id:n.dataset.blockId,text:n.textContent,citation:n.nextElementSibling?.textContent}))),family().short.blocks.map(b=>({id:b.block_id,text:b.text,citation:'[1]'})));
      assert.equal(await page.locator('#short-answer [data-block-id] .citation').count(),0,'Citation controls must not alter feedback offsets');
    }
    const notice=await page.locator('#generation-notice').innerText();assert(notice.includes(mode==='failure'?'Model ni pripravil odgovora':'Odgovor je pripravil model'));
    if(mode==='partial')assert((await page.locator('#retrieval-notice').innerText()).includes('Iskanje je delno'));
    assert((await page.locator('#coverage-list').textContent()).includes('ni dokumentarni vir'));
    assert.equal(await page.locator('.match-record').count(),2,'Final answer preserves all pages loaded during generation');
    assert.equal(calls.filter(c=>c.path==='/families/owned-family/matches').length,2,'Finalization reuses the same frozen list instead of retrieving it again');
    assert((await page.locator('#matches-summary').innerText()).includes('2 od 2'));assert.equal(await page.locator('.match-record img').count(),0);
    assert.equal(await page.locator('.match-record[data-used=true]').count(),mode==='failure'?0:1);
    await page.locator('#matches-source').selectOption('solska');assert.equal(await page.locator('.match-record').count(),1);assert((await page.locator('.match-use').innerText()).includes('ni citiran'));
    await page.locator('.match-record button').click();await page.locator('#source-content blockquote').waitFor();assert.equal(await page.locator('#source-content blockquote').textContent(),frozen);await page.locator('[data-close=source-dialog]').click();
    if(mode!=='failure'){await page.locator('#short-answer .citation').first().click();await page.locator('#source-content blockquote').waitFor();assert.equal(await page.locator('#source-content blockquote').textContent(),frozen);assert((await page.locator('#source-title').innerText()).includes('pošta'));await page.locator('[data-close=source-dialog]').click();}
    await selectComposerOption(page,'#task','jezikovna_posta');assert(await page.locator('#task').isVisible());assert(await page.locator('#model').isDisabled());
    await page.setViewportSize({width:360,height:800});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.locator('#account-button').click();await page.locator('#logout-button').click();await page.locator('#login-view').waitFor({state:'visible'});assert.equal(await page.locator('.match-record').count(),0);assert.equal(await page.locator('#matches-panel').isVisible(),false);
    report.checks.push({mode,status:'PASS',primary_submissions:1,progress_observed:['retrieval','generation'],early_original_opened:true,early_pagination_preserved:true,browser_api_fixtures_only:true});
  }finally{await context.close();}
}
async function metadataScenario(label,tier,expected){
  const context=await browser.newContext({viewport:{width:1100,height:900}}),page=await context.newPage(),before=JSON.stringify(tier),calls=[];
  const family={answer_family_id:'saved-metadata',question:'Saved metadata display',task:'reference',model:'qwen',locale:'sl',unified_answers:true,matches_available:false,long:null,short:tier};
  page.on('pageerror',e=>report.errors.push(e.message));
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());assert.equal(url.origin,origin,'No external request is permitted');
    const fulfill=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    if(url.pathname.startsWith('/assets/')){const relative=url.pathname.slice('/assets/'.length);assert(!relative.includes('..'));return route.fulfill({contentType:relative.endsWith('.mjs')?'text/javascript':relative.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(root,relative))});}
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__','sl')});
    const api=url.pathname.slice('/api/pilot'.length);calls.push({api,method:req.method()});
    if(api==='/session')return fulfill({authenticated:true,csrf_token:'synthetic',user:{id:'synthetic-owner',username:'synthetic-owner',role:'tester',must_change_password:false},capabilities:{tasks:['reference'],models:['qwen','gemma'],comparisons:false,unified_answers:true}});
    if(api==='/capabilities')return fulfill({unified_answers:true,generation:{models:{qwen:{available:true},gemma:{available:true}}}});
    if(api==='/feedback')return fulfill({feedback:[]});
    if(api==='/families/saved-metadata')return fulfill(family);
    if(api.startsWith('/runs/')&&api.endsWith('/delivered'))return fulfill({acknowledged:true});
    if(api.startsWith('/families/saved-metadata/sources/')){const eid=decodeURIComponent(api.split('/').at(-1)),source=tier.evidence.find(row=>(row.evidence_id||row.citation_id)===eid);assert(source);return fulfill({...source,source_label:source.source_label||source.source_slug,excerpt:source.snippet||'Synthetic original.',status:source.source_status,bibliography:source.bibliographic});}
    throw Error('Unexpected metadata fixture request '+req.method()+' '+api);
  });
  try{
    await page.goto(origin+'/?answer=saved-metadata');await page.locator('#short-answer [data-block-id]').first().waitFor();
    assert.deepEqual(await page.locator('#short-answer [data-block-id]').allTextContents(),tier.blocks.map(block=>block.text),'Saved answer/feedback text must remain unchanged');
    const notes=page.locator('#short-answer .answer-notes');assert.equal(await notes.count(),expected.length?1:0);
    if(expected.length){
      const text=await notes.innerText();for(const value of expected)assert(text.includes(value),label+': missing '+value);
      assert(!text.includes('unsupported_inference'));assert.equal(await notes.locator('p[data-note-kind]').count(),expected.length,'Do not repeat record notes across source passages');
      assert.equal(await notes.locator('[data-block-id]').count(),0,'Supplementary notes must not change canonical claim anchors');
      const button=notes.locator('.citation').first();if(await button.count()){await button.click();await page.locator('#source-content blockquote').waitFor();await page.locator('[data-close=source-dialog]').click();}
    }
    await page.setViewportSize({width:360,height:800});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.equal(JSON.stringify(tier),before);assert(!calls.some(call=>call.api==='/families'&&call.method==='POST'));
    report.checks.push({mode:'saved-metadata',case:label,status:'PASS',notes:expected.length,saved_answer_text_unchanged:true,model_calls:0});
  }finally{await context.close();}
}
async function metadataScenarios(){
  const draft={evidence_id:'draft',local_number:1,source_slug:'synthetic',external_id:'draft-record',source_status:'osnutek – potreben partnerski/uredniški pregled'},history={evidence_id:'history',local_number:2,source_slug:'advice',external_id:'old-record',publication_scope:'individual_advisory_answer',bibliographic:{date:'2026-01-01',signed_date_text:['januar 2014']}};
  await metadataScenario('synthetic-status-date',{state:'complete',status:'validated',run_id:'synthetic',generation_status:'generated',blocks:[{block_id:'C1',text:'Exact saved answer.',evidence_ids:['draft','history']}],evidence:[draft,history,{...history,evidence_id:'history2',local_number:3}],limitations:[{code:'unsupported_inference',scope:'claim',claim_ids:['C1']}]},['Podpora za del sklepanja','osnutek – potreben partnerski/uredniški pregled','januar 2014']);
  // Optional replay reads private API captures in place; no source payload is
  // committed to the public repository and no provider request is made.
  if(process.env.KLIKAI_SAVED_QUALITY_REPORT){
    const input=fs.readFileSync(process.env.KLIKAI_SAVED_QUALITY_REPORT),saved=JSON.parse(input);
    report.saved_quality_report={path:process.env.KLIKAI_SAVED_QUALITY_REPORT,sha256:require('node:crypto').createHash('sha256').update(input).digest('hex')};
    for(const row of saved.cases.filter(row=>['U07','U09','U11','U12'].includes(row.case_id))){
      const expected=row.case_id==='U07'?['Podpora za del sklepanja','osnutek – potreben partnerski/uredniški pregled']:row.case_id==='U09'?['letni dodatek','predlog']:row.case_id==='U11'?['osnutek – potreben partnerski/uredniški pregled','januar 2014']:[];
      await metadataScenario(row.case_id+'/'+row.selection,row.rendered_result,expected);
    }
  }
}
(async()=>{try{browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--disable-dev-shm-usage']});for(const mode of ['generated','partial','failure','cancel','matches_retry'])await scenario(mode);await metadataScenarios();assert.deepEqual(report.errors,[]);report.status='PASS';}catch(e){report.status='FAIL';report.error={name:e.name,message:e.message};process.exitCode=1;}finally{if(browser)await browser.close();console.log(JSON.stringify(report));}})();
