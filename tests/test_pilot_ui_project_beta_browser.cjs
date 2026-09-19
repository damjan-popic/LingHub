/* Candidate UI acceptance, offline saved-response replay. No model/operational calls. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://beta.test';
const input=process.env.KLIKAI_BETA_UI_FIXTURE,output=process.env.KLIKAI_BETA_UI_OUTPUT;
if(!input)throw Error('KLIKAI_BETA_UI_FIXTURE must name private saved-fixtures JSON; restricted originals are never committed');
const fixtures=JSON.parse(fs.readFileSync(input)),report={scope:'Candidate frontend, saved actual answers/sources plus synthetic pending/error transitions. No generation or operational requests.',input,model_calls:0,checks:[],errors:[]};
let browser;
async function scenario(saved,mode,width){
 const context=await browser.newContext({viewport:{width,height:950}}),page=await context.newPage();const calls=[],family=structuredClone(saved.family);let complete=mode!=='pending',feedback,submissions=0;
 if(mode==='no-support'){family.short.answer_kind='no_support';family.short.generation_status='not_generated';family.short.blocks=[];}
 if(mode==='qualification'){family.short.limitations=[{code:'unsupported_inference',scope:'answer'}];family.short.evidence[0].source_status='draft';family.short.evidence[0].publication_scope='annual_additions';}
 if(mode==='error'){family.short.state='failed';family.short.generation_status='failed';family.short.blocks=[];family.short.evidence=[];}
 const response=()=>complete?family:{...family,short:{state:'running',job_id:'fixture-job',progress:{stage:'generation'}}};
 page.on('pageerror',e=>report.errors.push(e.message));
 await context.route('**/*',async route=>{
  const request=route.request(),u=new URL(request.url());assert.equal(u.origin,origin,'External requests forbidden');
  const json=v=>route.fulfill({contentType:'application/json',body:JSON.stringify(v)});
  if(u.pathname.startsWith('/assets/')){const file=u.pathname.slice(8);assert(!file.includes('..'));return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(assets,file))});}
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__','sl')});
  const api=u.pathname.slice('/api/pilot'.length);calls.push({path:api,method:request.method(),query:u.search});
  if(api==='/session')return json({authenticated:true,csrf_token:'fixture-csrf',user:{username:'saved-preview',role:'tester',can_change_password:false,shared_group:true},capabilities:{project_beta:true,unified_answers:true,comparisons:false,beta_updated:'Posodobljeno 19. 9. 2026'}});
  if(api==='/capabilities')return json({project_beta:true});
  if(api==='/feedback'&&request.method()==='GET')return json({feedback:[]});
  if(api==='/feedback'){feedback=request.postDataJSON();return json({acknowledged:true,feedback_id:'private-feedback'});}
  if(api==='/families'&&request.method()==='POST'){const submitted=request.postDataJSON();assert(!('model'in submitted));assert.equal(submitted.task,'reference');submissions++;if(mode==='clarification'&&submissions===1)return json({status:'clarification_required',clarification:{code:'input_language_required',fields:['input_language']}});if(mode==='clarification')assert.equal(submitted.input_language,'sl');return json({family:response()});}
  if(api==='/families'&&request.method()==='GET')return json({families:[]});
  if(api==='/families/'+family.answer_family_id)return json(response());
  if(api==='/jobs/fixture-job')return json({job_id:'fixture-job',state:complete?'complete':'running',progress:{stage:'generation',matches_ready:true}});
  if(api.endsWith('/sources'))return json({...saved.sources,usage_final:complete});
  if(api.endsWith('/matches')){const source=u.searchParams.get('source'),offset=Number(u.searchParams.get('offset')),rows=saved.records.filter(r=>r.source_slug===source);assert.equal(u.searchParams.get('limit'),'10');return json({items:rows.slice(offset,offset+10).map(r=>({...r,used_in_answer:complete?r.used_in_answer:null})),returned_count:rows.length,next_offset:offset+10<rows.length?offset+10:null,usage_final:complete});}
  if(api.includes('/sources/')){const eid=decodeURIComponent(api.split('/').at(-1));assert(saved.drawers[eid]);return json(saved.drawers[eid]);}
  if(api.endsWith('/delivered')){assert.deepEqual(request.postDataJSON(),{delivery_token:family.short.delivery_token,expanded:false});return json({acknowledged:true});}
  throw Error('Unexpected fixture route '+api);
 });
 try{
  await page.goto(origin+'/?answer='+family.answer_family_id);await page.locator('#answer-card').waitFor({state:'visible'});
  assert.equal(await page.locator('#beta-explorer').isVisible(),false);assert.equal(await page.locator('#options-toggle').isVisible(),false);assert.equal(await page.locator('#account-button').isVisible(),false);assert.equal(await page.locator('#answer-model').isVisible(),false);assert.equal(await page.locator('#compare-button').isVisible(),false);assert.equal(await page.locator('.quality-form').count(),0);
  if(mode==='saved'&&width===390){
   await page.locator('#history-toggle').click();await page.locator('#history-list p').waitFor();
   assert.equal(await page.locator('#shared-history-note').innerText(),'Prejšnja vprašanja so skupna vsem, ki uporabljajo isti projektni račun.');
   assert.equal(await page.locator('#history-list').innerText(),'S tem projektnim računom še ni bilo poslano nobeno vprašanje.');
   await page.locator('[data-locale=it]').click();
   assert.equal(await page.locator('#shared-history-note').innerText(),'Le domande precedenti sono condivise da tutte le persone che usano lo stesso account di progetto.');
   assert.equal(await page.locator('#history-list').innerText(),'Non sono ancora state inviate domande con questo account di progetto.');
   await page.locator('[data-locale=sl]').click();await page.locator('#history-toggle').click();
  }
  const answer=await page.locator('#short-answer').textContent();
  if(mode==='qualification'){const notes=await page.locator('.answer-notes').innerText();assert(notes.includes('Podpora za del sklepanja'));assert(notes.includes('osnutek'));assert(notes.includes('letni dodatek'));assert.equal(await page.locator('#beta-explorer').isVisible(),false);}
  if(mode==='saved'){await page.waitForFunction(()=>!document.querySelector('#short-feedback button')?.disabled);assert.equal(await page.locator('#short-feedback button').innerText(),'Sporoči težavo');assert.equal(await page.locator('#short-answer [data-block-id]').count(),family.short.blocks.length);assert.deepEqual(await page.locator('#short-answer [data-block-id]').allTextContents(),family.short.blocks.map(b=>b.text));
   if(output)await page.screenshot({path:path.join(output,`${saved.case_id}-${width}-collapsed.png`),fullPage:true});
   const cite=page.locator('#short-answer .citation').first();await cite.click();await page.locator('#source-content blockquote').waitFor();const citedText=await page.locator('#source-content blockquote').textContent();assert(Object.values(saved.drawers).some(d=>d.excerpt===citedText));await page.locator('[data-close=source-dialog]').click();
  }
  await page.locator('#expand-button').click();await page.locator('.explorer-badge').first().waitFor();assert.equal(await page.locator('.explorer-badge').count(),saved.sources.sources.length);
  let reached=[];
  for(const source of saved.sources.sources){await page.locator(`.explorer-badge[data-source="${source.source_slug}"]`).click();await page.waitForFunction(()=>document.querySelectorAll('.explorer-record').length>0);while(true){const ids=await page.locator('.explorer-record').evaluateAll(rows=>rows.map(n=>n.dataset.recordId));reached.push(...ids);assert(ids.length<=10);
    for(const record of saved.records.filter(r=>ids.includes(r.record_id)&&r.evidence_ids.length>1)){
     await page.locator(`.explorer-record[data-record-id="${record.record_id}"] .explorer-entry`).click();await page.locator('#source-content blockquote').waitFor();
     for(const eid of record.evidence_ids){const part=page.locator(`[data-record-part="${eid}"]`);if(!await part.isDisabled()){await part.click();await page.waitForFunction(expected=>document.querySelector('#source-content blockquote')?.textContent===expected,saved.drawers[eid].excerpt);}assert.equal(await page.locator('#source-content blockquote').textContent(),saved.drawers[eid].excerpt);}
     await page.locator('[data-close=source-dialog]').click();
    }
    if(mode==='pending'){assert.equal(await page.locator('.entry-used').count(),0);assert.equal(await page.locator('.explorer-record[data-used=pending]').count(),ids.length);}if(!await page.locator('#explorer-next').isVisible())break;await page.locator('#explorer-next').click();await page.waitForFunction(first=>!!document.querySelector('.explorer-record')&&document.querySelector('.explorer-record').dataset.recordId!==first,ids[0]);}}
  assert.deepEqual([...new Set(reached)].sort(),saved.records.map(r=>r.record_id).sort());
  const many=saved.sources.sources.find(s=>s.returned_count>10)||saved.sources.sources[0];await page.locator(`.explorer-badge[data-source="${many.source_slug}"]`).click();await page.waitForFunction(slug=>document.querySelector('.explorer-badge[aria-pressed=true]').dataset.source===slug,many.source_slug);
  const pageBefore=await page.locator('.explorer-record').evaluateAll(rows=>rows.map(n=>n.dataset.recordId));const beforeCalls=calls.filter(c=>c.path.endsWith('/matches')).length;
  if(output)await page.screenshot({path:path.join(output,`${saved.case_id}-${width}-${mode}-expanded.png`),fullPage:true});
  await page.locator('.explorer-entry').first().click();await page.locator('#source-content blockquote').waitFor();const actual=await page.locator('#source-content blockquote').textContent();assert(Object.values(saved.drawers).some(d=>d.excerpt===actual),'Exact saved full original survives');
  if(output)await page.screenshot({path:path.join(output,`${saved.case_id}-${width}-${mode}-original.png`),fullPage:true});await page.locator('[data-close=source-dialog]').click();
  assert.deepEqual(await page.locator('.explorer-record').evaluateAll(rows=>rows.map(n=>n.dataset.recordId)),pageBefore);
  await page.locator('#expand-button').click();await page.locator('#expand-button').click();assert.equal(calls.filter(c=>c.path.endsWith('/matches')).length,beforeCalls);assert.equal(await page.locator('#short-answer').textContent(),answer);
  if(mode==='pending'){complete=true;await page.waitForFunction(()=>document.querySelector('#job-state').hidden);assert.equal(await page.locator('.explorer-record[data-used=pending]').count(),0);}
  if(mode==='no-support'){assert.equal(await page.locator('#explorer-heading').innerText(),'Drugi najdeni zadetki');assert.equal(await page.locator('.entry-used').count(),0);}
  if(mode==='saved'){await page.locator('#short-feedback button').click();await page.locator('#feedback-comment').fill('Isolated fixture feedback');await page.locator('#beta-feedback-rating input[value=useful]').check();await page.locator('#feedback-tags input[value=source]').check();await page.locator('#feedback-form button[type=submit]').click();await page.waitForFunction(()=>!document.querySelector('#feedback-dialog').open);assert.equal(feedback.run_id,family.short.run_id);assert.equal(feedback.rating,'useful');assert.deepEqual(feedback.tags,['source']);}
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No horizontal overflow');assert(!/Qwen|Gemma|GPU|CPU|sha256|fixture-csrf/.test(await page.locator('body').innerText()),'No visible technical metadata');
  assert.equal(calls.filter(c=>c.path.endsWith('/expand')).length,0);assert.equal(calls.filter(c=>c.path==='/families'&&c.method==='POST').length,0);
  if(mode==='saved'&&width===390){await page.locator('#ask-button').click();await page.waitForFunction(()=>!document.querySelector('#ask-button').disabled);assert.equal(calls.filter(c=>c.path==='/families'&&c.method==='POST').length,1);}
  if(mode==='clarification'){await page.locator('#ask-button').click();await page.locator('#input-language').waitFor({state:'visible'});assert.equal(await page.locator('#model').isVisible(),false);assert.equal(await page.locator('#task').isVisible(),false);await page.locator('#input-language').selectOption('sl');await page.locator('#ask-button').click();await page.waitForFunction(()=>document.querySelector('#clarification').hidden);assert.equal(submissions,2);}
  report.checks.push({case:saved.case_id,mode,width,status:'PASS',raw_match_count:saved.raw_match_count,unique_records:reached.length,all_sources:saved.sources.sources.length,all_records_reachable:true,exact_original:true});
 }finally{await context.close();}
}
async function raceScenario(saved,stage){
 const context=await browser.newContext({viewport:{width:390,height:850}}),page=await context.newPage();let release,heldResolve,gets=0,feedbackGets=0;
 const held=new Promise(resolve=>heldResolve=resolve),deferred=new Promise(resolve=>release=resolve),id=saved.family.answer_family_id;
 const old={...saved.family,matches_available:stage==='feedback',short:{state:'running',job_id:'old-job'}};
 const next={...saved.family,answer_family_id:'new-family',question:'A new question',matches_available:false,short:{state:'running',job_id:'new-job'}};
 page.on('pageerror',e=>report.errors.push(e.message));
 await context.route('**/*',async route=>{const req=route.request(),u=new URL(req.url());assert.equal(u.origin,origin);const json=v=>route.fulfill({contentType:'application/json',body:JSON.stringify(v)});
  if(u.pathname.startsWith('/assets/')){const file=u.pathname.slice(8);return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(assets,file))});}
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__','sl')});
  const api=u.pathname.slice('/api/pilot'.length);
  if(api==='/session')return json({authenticated:true,user:{username:'fixture',role:'tester'},capabilities:{project_beta:true},csrf_token:'fixture'});
  if(api==='/capabilities')return json({project_beta:true});
  if(api==='/feedback'){feedbackGets++;if(stage==='feedback'&&feedbackGets===2){heldResolve();await deferred;}return json({feedback:[]});}
  if(api==='/families/'+id){gets++;if(stage==='early'&&gets===2){heldResolve();await deferred;return json({...old,matches_available:true});}return json(old);}
  if(api==='/jobs/old-job')return json({state:stage==='feedback'?'complete':'running',progress:{stage:'generation',matches_ready:true}});
  if(api==='/jobs/new-job')return json({state:'running',progress:{stage:'retrieval'}});
  if(api==='/families'&&req.method()==='POST')return json({family:next,job:{job_id:'new-job',state:'running'}});
  if(api.endsWith('/sources'))return json(saved.sources);
  throw Error('Unexpected race route '+api);
 });
 try{await page.goto(origin+'/?answer='+id);await held;await page.locator('#question').fill('A new question');await page.locator('#ask-button').click();await page.waitForURL('**answer=new-family');await page.waitForFunction(()=>document.querySelector('#job-state').dataset.stage==='retrieval');release();await page.waitForTimeout(200);assert.equal(new URL(page.url()).searchParams.get('answer'),'new-family');assert.equal(await page.locator('#question').inputValue(),'A new question');assert(await page.locator('#job-state').isVisible());assert.equal(await page.locator('#job-state').getAttribute('data-stage'),'retrieval');report.checks.push({mode:'superseded-'+stage,status:'PASS',late_response_discarded:true});}finally{release();await context.close();}
}
(async()=>{try{if(output)fs.mkdirSync(output,{recursive:true});browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--no-sandbox']});for(const f of fixtures.fixtures){for(const width of [1100,390])await scenario(f,'saved',width);}for(const mode of ['pending','no-support','error','qualification','clarification'])await scenario(fixtures.fixtures[1],mode,390);for(const stage of ['early','feedback'])await raceScenario(fixtures.fixtures[0],stage);assert.deepEqual(report.errors,[]);report.status='PASS';}catch(e){report.status='FAIL';report.errors.push(e.stack);process.exitCode=1;}finally{await browser?.close();if(output)fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));}})();
