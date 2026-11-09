/* Read-only product audit: intercepted synthetic browser responses, no service/account writes. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://followup-audit.test';
const out=process.env.KLIKAI_FOLLOWUP_UI_OUTPUT;
const report={scope:'Offline intercepted browser exercise. Synthetic account/families only; no operational service, model, account, feedback or corpus writes. Login-success navigation is simulated; real authentication remains covered by backend tests.',checks:[],errors:[]};
const family=(id,question)=>({answer_family_id:id,question,locale:'sl',task:'reference',context:'',matches_available:false,
 short:{state:'complete',answer_kind:'generated',generation_status:'generated',run_id:'run-'+id,blocks:[{block_id:'C1',text:'Saved answer '+id,evidence_ids:[]}],evidence:[]}});
const a=family('a','Kaj pomeni izraz zelen?'),b=family('b','Drugo shranjeno vprašanje?');
a.short.evidence=[{evidence_id:'e1',local_number:1,source_slug:'epravopis',title:'First exact source'},
 {evidence_id:'e2',local_number:2,source_slug:'epravopis',title:'Second exact source'}];
a.short.blocks[0].evidence_ids=['e1','e2'];
let browser;
async function setup({legacy=false,initialDelay=null,answer=true,presetNewDraft=null}={}){
 const context=await browser.newContext({viewport:{width:1100,height:900}}),page=await context.newPage(),calls=[];
 const savedA=legacy?{...a,long:{...a.short,run_id:'run-a-long'}}:a;
 const controls={expired:false,hold:null,release:null,historyFailure:false,sourceFailure:false,sourceHold:null,sourceRelease:null,losePost:false,submissionMissing:false};
 if(initialDelay)controls.initialHold=new Promise(resolve=>{controls.initialRelease=resolve;});
 page.on('pageerror',e=>report.errors.push(e.message));
 await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copiedText=text;}}}));
 if(presetNewDraft)await page.addInitScript(question=>{if(!sessionStorage.getItem('klikai::composer-drafts:synthetic'))sessionStorage.setItem('klikai::composer-drafts:synthetic',JSON.stringify({new:{question,followup:null}}));},presetNewDraft);
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());if(url.origin!==origin)throw Error('Unexpected external request '+url.origin);
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(url.pathname.startsWith('/assets/')){const file=url.pathname.slice(8);let body=fs.readFileSync(path.join(assets,file));if(file==='pilot.mjs')body=body.toString().replace('bind();await bootstrap();','bind();await bootstrap();globalThis.fixtureBootstrapComplete=true;');return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'application/json',body});}
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__','sl')});
  const api=url.pathname.slice('/api/pilot'.length);calls.push({api,method:req.method(),body:req.method()==='POST'?req.postData():null});
  if(initialDelay===api)await controls.initialHold;
  if(api==='/login'){controls.expired=false;return route.fulfill({contentType:'text/html',body:'<script>location.replace("/?answer=a")</script>'});}
  if(api==='/session')return json({authenticated:true,user:{id:'synthetic',username:'fixture',role:'tester'},csrf_token:'fixture',capabilities:{project_beta:true}});
  if(api==='/capabilities')return json({project_beta:true});
  if(api==='/feedback')return json({feedback:[]});
  if(api.startsWith('/runs/')&&api.endsWith('/feedback'))return json({events:[]});
  if(api==='/families'&&req.method()==='GET')return controls.expired?route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({error:{code:'unauthorized'}})}):json({families:[a,b]});
  if(api==='/families'&&req.method()==='POST'){if(controls.losePost)return route.abort('failed');const held=controls.hold;if(held)await held;if(controls.inputLanguageRequired)return route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:{code:'input_language_required'}})});if(controls.clarify)return json({status:'clarification_required',clarification:{code:'focus_required',fields:['focus']}});const payload=JSON.parse(req.postData());return json({family:family('new',payload.question)});}
  if(api.startsWith('/submissions/'))return controls.submissionMissing?route.fulfill({status:404,contentType:'application/json',body:'{}'}):json({family:family('new','Recovered exact submitted question')});
  if(api==='/families/new')return json(family('new','Recovered exact submitted question'));
  if(api==='/families/a')return json(savedA);
  if(api==='/families/b')return controls.historyFailure?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'unavailable'}})}):json(b);
  if(api.includes('/sources/e')){if(controls.sourceHold)await controls.sourceHold;return controls.sourceFailure?route.fulfill({status:503,contentType:'application/json',body:'{}'}):json({title:api.endsWith('e1')?'First exact source':'Second exact source',excerpt:'Frozen source passage.',source_slug:'epravopis',source_label:'ePravopis'});}
  if(api.endsWith('/delivered'))return json({acknowledged:true});
  if(api.endsWith('/sources'))return json({sources:[],usage_final:true});
  if(api.includes('/matches'))return json({items:[],returned_count:0,next_offset:null,usage_final:true});
  throw Error('Unexpected synthetic API '+api);
 });
 await page.goto(origin+(answer?'/?answer=a':'/'));if(initialDelay)await page.locator('#app-view').waitFor({state:'visible'});else if(answer)await page.locator('#followup-button').waitFor({state:'visible'});else await page.waitForFunction(()=>globalThis.fixtureBootstrapComplete===true);
 return {context,page,calls,controls};
}
async function followup(f,text='Katere so njegove sopomenke?'){
 await f.page.locator('#followup-button').click();await f.page.locator('#question').fill(text);
 return {draft:await f.page.locator('#question').inputValue(),banner:await f.page.locator('#followup-context').innerText()};
}
(async()=>{try{
 if(out)fs.mkdirSync(out,{recursive:true});browser=await chromium.launch({headless:true});
 for(const [initialDelay,answer]of [['/capabilities',false],['/feedback',false],['/feedback',true],['/families/a',true]]){
  const f=await setup({initialDelay,answer});await f.page.locator('#question').fill('Question typed before initialization completes');
  f.controls.initialRelease();await f.page.waitForFunction(()=>globalThis.fixtureBootstrapComplete===true);
  assert.equal(await f.page.locator('#question').inputValue(),'Question typed before initialization completes');
  assert.equal(await f.page.locator('#followup-context').isHidden(),true,'Loading an owned answer never invents a parent relation for early text');
  await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');
  const sent=JSON.parse(f.calls.find(call=>call.api==='/families'&&call.method==='POST').body);
  assert.equal(sent.question,'Question typed before initialization completes');assert.equal('parent_run_id'in sent,false);
  report.checks.push({case:'early-typing-survives-delayed-initialization',initialDelay,answer,status:'PASS'});await f.context.close();
 }
 for(const answer of [false,true]){
  const f=await setup({initialDelay:'/feedback',answer});await f.page.locator('#question').fill('Question submitted before initialization completes');
  await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');f.controls.initialRelease();
  await f.page.waitForFunction(()=>globalThis.fixtureBootstrapComplete===true);
  assert.equal(f.page.url(),origin+'/?answer=new');assert.equal(await f.page.locator('#question').inputValue(),'Question submitted before initialization completes');
  assert.equal(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').length,1);
  assert.equal(await f.page.locator('#followup-context').isHidden(),true);
  report.checks.push({case:'early-submit-keeps-ownership-over-late-bootstrap',answer,status:'PASS'});await f.context.close();
 }
 {
  const f=await setup({initialDelay:'/families/a',presetNewDraft:'Independent New question draft'});
  await f.page.locator('#question').fill('Early draft for the owned saved answer');f.controls.initialRelease();
  await f.page.waitForFunction(()=>globalThis.fixtureBootstrapComplete===true);
  assert.equal(await f.page.locator('#question').inputValue(),'Early draft for the owned saved answer');
  assert.equal(await f.page.evaluate(()=>JSON.parse(sessionStorage.getItem('klikai::composer-drafts:synthetic')).new.question),'Independent New question draft');
  report.checks.push({case:'early-saved-answer-edit-preserves-independent-new-question-draft',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup({initialDelay:'/feedback',answer:false,presetNewDraft:'Independent older New draft'});f.controls.losePost=true;f.controls.submissionMissing=true;
  await f.page.locator('#question').fill('Actual early submitted question');await f.page.locator('#ask-button').click();
  await f.page.waitForFunction(()=>document.querySelector('#global-status').textContent.length>0);assert.equal(await f.page.locator('#ask-button').isDisabled(),false);const first=JSON.parse(f.calls.find(call=>call.api==='/families'&&call.method==='POST').body);
  f.controls.initialRelease();await f.page.waitForFunction(()=>globalThis.fixtureBootstrapComplete===true);await f.page.reload();await f.page.waitForFunction(()=>globalThis.fixtureBootstrapComplete===true);
  assert.equal(await f.page.locator('#question').inputValue(),'Actual early submitted question');
  assert.equal(await f.page.evaluate(()=>JSON.parse(sessionStorage.getItem('klikai::composer-drafts:synthetic')).new.question),'Independent older New draft');
  assert.equal(f.calls.some(call=>call.api==='/families/opening'),false);
  f.controls.losePost=false;await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');
  const retried=JSON.parse(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').at(-1).body);assert.equal(retried.question,first.question);assert.equal(retried.idempotency_key,first.idempotency_key);
  assert.equal(await f.page.evaluate(()=>JSON.parse(sessionStorage.getItem('klikai::composer-drafts:synthetic')).new.question),'Independent older New draft');
  report.checks.push({case:'early-lost-response-retries-opening-draft-without-consuming-independent-new-draft',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup();await followup(f,'First submitted follow-up');f.controls.hold=new Promise(resolve=>{f.controls.release=resolve;});await f.page.locator('#ask-button').click();
  await f.page.locator('#question').fill('Newer unsent follow-up');await f.page.evaluate(()=>{for(const[id,value]of [['answer-locale','it'],['input-language','sl'],['focus','newer focus']]){const n=document.getElementById(id);n.value=value;n.dispatchEvent(new Event('change',{bubbles:true}));}});
  const finished=f.page.waitForResponse(response=>response.request().method()==='POST'&&response.url()===origin+'/api/pilot/families');f.controls.release();await(await finished).finished();await f.page.waitForTimeout(100);
  assert.equal(await f.page.locator('#question').inputValue(),'Newer unsent follow-up');assert.equal(await f.page.locator('#followup-context').isHidden(),false);assert.equal(f.page.url(),origin+'/?answer=a');
  await f.page.reload();await f.page.locator('#followup-button').waitFor({state:'visible'});assert.equal(await f.page.locator('#question').inputValue(),'Newer unsent follow-up');
  assert.equal(await f.page.locator('#answer-locale').inputValue(),'it');assert.equal(await f.page.locator('#focus').inputValue(),'newer focus');assert.equal(await f.page.locator('#input-language').inputValue(),'sl');
  await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');const bodies=f.calls.filter(call=>call.api==='/families'&&call.method==='POST').map(call=>JSON.parse(call.body));
  assert.equal(bodies[1].question,'Newer unsent follow-up');assert.equal(bodies[1].parent_run_id,'run-a');assert.equal(bodies[1].locale,'it');assert.equal(bodies[1].focus,'newer focus');assert.notEqual(bodies[1].idempotency_key,bodies[0].idempotency_key);
  report.checks.push({case:'accepted-q1-preserves-newer-q2-settings-and-original-explicit-parent-through-reload',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup();await followup(f,'Follow-up needing clarification');await f.page.evaluate(()=>{const n=document.getElementById('answer-locale');n.value='it';n.dispatchEvent(new Event('change',{bubbles:true}));});f.controls.clarify=true;
  await f.page.locator('#ask-button').click();await f.page.locator('#clarification').waitFor({state:'visible'});await f.page.reload();await f.page.locator('#clarification').waitFor({state:'visible'});
  assert.equal(await f.page.locator('#question').inputValue(),'Follow-up needing clarification');assert.equal(await f.page.locator('#followup-context').isHidden(),false);assert.equal(await f.page.locator('#answer-locale').inputValue(),'it');
  f.controls.expired=true;await f.page.locator('#history-toggle').click();await f.page.locator('#login-view').waitFor({state:'visible'});await f.page.locator('#username').fill('fixture');await f.page.locator('#password').fill('synthetic-only-password');await f.page.locator('#login-form button[type=submit]').click();await f.page.locator('#clarification').waitFor({state:'visible'});
  assert.equal(await f.page.locator('#question').inputValue(),'Follow-up needing clarification');assert.equal(await f.page.locator('#followup-context').isHidden(),false);assert.equal(await f.page.locator('#answer-locale').inputValue(),'it');
  await f.page.locator('#focus').fill('clarified focus');f.controls.clarify=false;await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');
  const bodies=f.calls.filter(call=>call.api==='/families'&&call.method==='POST').map(call=>JSON.parse(call.body));assert.equal(bodies[1].parent_run_id,'run-a');assert.equal(bodies[1].locale,'it');assert.equal(bodies[1].focus,'clarified focus');assert.notEqual(bodies[1].idempotency_key,bodies[0].idempotency_key);
  report.checks.push({case:'clarification-retains-owned-followup-and-settings-through-reload-and-signin',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup();await followup(f,'Clarification workspace question');f.controls.clarify=true;await f.page.locator('#ask-button').click();await f.page.locator('#clarification').waitFor({state:'visible'});
  await f.page.locator('#focus').fill('resolved focus');f.controls.clarify=false;f.controls.hold=new Promise(resolve=>{f.controls.release=resolve;});await f.page.locator('#ask-button').click();
  await f.page.locator('#question').fill('Newer question while clarification retry is pending');const finished=f.page.waitForResponse(response=>response.request().method()==='POST'&&JSON.parse(response.request().postData()).focus==='resolved focus');
  f.controls.release();await(await finished).finished();await f.page.waitForURL('**/?answer=a');assert.equal(await f.page.locator('#followup-context').isHidden(),false);
  await f.page.reload();await f.page.locator('#followup-button').waitFor({state:'visible'});assert.equal(await f.page.locator('#question').inputValue(),'Newer question while clarification retry is pending');assert.equal(await f.page.locator('#followup-context').isHidden(),false);
  await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');const sent=JSON.parse(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').at(-1).body);assert.equal(sent.parent_run_id,'run-a');
  report.checks.push({case:'newer-composer-keeps-parent-from-clarification-workspace-with-no-displayed-family',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup();await followup(f,'Follow-up needing input language');f.controls.inputLanguageRequired=true;await f.page.locator('#ask-button').click();await f.page.locator('#clarification').waitFor({state:'visible'});
  await f.page.reload();await f.page.locator('#clarification').waitFor({state:'visible'});assert.equal(await f.page.locator('#question').inputValue(),'Follow-up needing input language');assert.equal(await f.page.locator('#followup-context').isHidden(),false);
  f.controls.inputLanguageRequired=false;await f.page.locator('#input-language').selectOption('sl');await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');
  const sent=JSON.parse(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').at(-1).body);assert.equal(sent.input_language,'sl');assert.equal(sent.parent_run_id,'run-a');
  report.checks.push({case:'input-language-error-uses-same-owned-clarification-recovery',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup();await followup(f,'Same text with newer settings');f.controls.losePost=true;await f.page.locator('#ask-button').click();await f.page.waitForFunction(()=>document.querySelector('#global-status').textContent.length>0);
  const first=JSON.parse(f.calls.find(call=>call.api==='/families'&&call.method==='POST').body);await f.page.evaluate(()=>{const n=document.getElementById('answer-locale');n.value='it';n.dispatchEvent(new Event('change',{bubbles:true}));});
  await f.page.reload();await f.page.locator('#followup-button').waitFor({state:'visible'});assert.equal(f.page.url(),origin+'/?answer=a');assert.equal(await f.page.locator('#question').inputValue(),first.question);assert.equal(await f.page.locator('#answer-locale').inputValue(),'it');assert.equal(await f.page.locator('#followup-context').isHidden(),false);
  f.controls.losePost=false;await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');const retried=JSON.parse(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').at(-1).body);
  assert.equal(retried.parent_run_id,'run-a');assert.equal(retried.locale,'it');assert.notEqual(retried.idempotency_key,first.idempotency_key);
  report.checks.push({case:'accepted-lost-response-keeps-newer-settings-for-same-question-and-parent',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup({answer:false});await f.page.locator('#question').fill('First real request from base');await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');await f.page.goBack();await f.page.waitForURL(origin+'/');
  assert.equal(await f.page.locator('#answer-card').isHidden(),true);assert.equal(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').length,1);
  report.checks.push({case:'first-base-submission-keeps-browser-back-entry-without-regeneration',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup();await f.page.evaluate(()=>{const n=document.getElementById('answer-locale');n.value='it';n.dispatchEvent(new Event('change',{bubbles:true}));});await f.page.locator('#history-toggle').click();await f.page.locator('#history-list button').filter({hasText:b.question}).click();await f.page.waitForURL('**/?answer=b');await f.page.goBack();await f.page.waitForURL('**/?answer=a');
  await f.page.reload();await f.page.locator('#followup-button').waitFor({state:'visible'});assert.equal(await f.page.locator('#question').inputValue(),a.question);assert.equal(await f.page.locator('#answer-locale').inputValue(),'it');assert.equal(await f.page.locator('html').getAttribute('lang'),'sl');
  report.checks.push({case:'explicit-answer-language-persists-for-unchanged-question-and-sl-interface',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup(),before=await followup(f);await f.page.locator('#history-toggle').click();
  await f.page.locator('#history-list button').filter({hasText:b.question}).click();await f.page.waitForURL('**/?answer=b');
  const focus=await f.page.evaluate(()=>document.activeElement.id);assert.equal(focus,'short-heading');
  report.checks.push({case:'history-switch-preserves-independent-composer',before,after:{question:await f.page.locator('#question').inputValue(),followup_banner_hidden:await f.page.locator('#followup-context').isHidden()},focus});
  const length=await f.page.evaluate(()=>history.length);await f.page.goBack();
  await f.page.waitForFunction(()=>document.querySelector('#question').value==='Katere so njegove sopomenke?');
  assert.equal(f.page.url(),origin+'/?answer=a');assert.equal(await f.page.locator('#followup-context').isHidden(),false);
  report.checks.push({case:'browser-back-restores-exact-draft-and-parent',history_length_after_selection:length,url_after_back:f.page.url(),question:await f.page.locator('#question').inputValue()});
  await f.page.reload();await f.page.locator('#followup-button').waitFor({state:'visible'});
  assert.equal(await f.page.locator('#question').inputValue(),before.draft);assert.equal(await f.page.locator('#followup-context').isHidden(),false);
  await f.page.goForward();await f.page.waitForFunction(()=>document.querySelector('#question').value==='Drugo shranjeno vprašanje?');
  assert.equal(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').length,0);
  assert.equal(await f.page.evaluate(()=>history.length),length);
  report.checks.push({case:'reload-and-forward-preserve-owned-draft-with-no-generation-or-stack-growth',status:'PASS'});
  await f.context.close();
 }
 {
  const f=await setup();f.controls.hold=new Promise(resolve=>{f.controls.release=resolve;});
  await f.page.locator('#question').fill('Submitted before history navigation');await f.page.locator('#ask-button').click();
  await f.page.locator('#history-toggle').click();await f.page.locator('#history-list button').filter({hasText:b.question}).click();await f.page.waitForURL('**/?answer=b');
  const completed=f.page.waitForResponse(response=>response.request().method()==='POST'&&response.url()===origin+'/api/pilot/families');f.controls.release();await(await completed).finished();await f.page.waitForTimeout(100);
  assert.equal(f.page.url(),origin+'/?answer=b');assert.equal(await f.page.locator('#question').inputValue(),b.question);
  assert((await f.page.locator('#short-answer').innerText()).includes('Saved answer b'));
  report.checks.push({case:'late-accepted-submission-does-not-replace-selected-history',status:'PASS'});await f.context.close();
 }
 for(const missing of [false,true]){
  const f=await setup();await followup(f,'Submitted follow-up with lost response');f.controls.losePost=true;f.controls.submissionMissing=missing;
  await f.page.locator('#ask-button').click();await f.page.waitForFunction(()=>document.querySelector('#global-status').textContent.length>0);
  const first=JSON.parse(f.calls.find(call=>call.api==='/families'&&call.method==='POST').body);
  await f.page.reload();await f.page.locator('#followup-button').waitFor({state:'visible'});
  if(missing){
   assert.equal(await f.page.locator('#question').inputValue(),'Submitted follow-up with lost response');assert.equal(await f.page.locator('#followup-context').isHidden(),false);
   const historyLength=await f.page.evaluate(()=>history.length);f.controls.losePost=false;await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');assert.equal(await f.page.evaluate(()=>history.length),historyLength);
   const retried=JSON.parse(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').at(-1).body);
   assert.equal(retried.idempotency_key,first.idempotency_key);assert.equal(retried.parent_run_id,'run-a');
  }else{assert.equal(f.page.url(),origin+'/?answer=new');assert.equal(f.calls.filter(call=>call.api==='/families'&&call.method==='POST').length,1);}
  report.checks.push({case:'lost-response-recovery-preserves-exact-submission-and-followup-parent',submission_missing:missing,status:'PASS'});await f.context.close();
 }
 {
  const f=await setup({legacy:true});await followup(f,'Follow-up to displayed historical long');await f.page.locator('#history-toggle').click();
  await f.page.locator('#history-list button').filter({hasText:b.question}).click();await f.page.waitForURL('**/?answer=b');await f.page.goBack();
  await f.page.waitForFunction(()=>document.querySelector('#question').value==='Follow-up to displayed historical long');
  assert.equal(await f.page.locator('#followup-context').isHidden(),false);await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');
  assert.equal(JSON.parse(f.calls.find(call=>call.api==='/families'&&call.method==='POST').body).parent_run_id,'run-a-long');
  report.checks.push({case:'historical-displayed-long-keeps-exact-parent-on-return',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup();f.controls.hold=new Promise(resolve=>{f.controls.release=resolve;});
  await f.page.locator('#question').fill('First held submit');await f.page.locator('#ask-button').click();await f.page.locator('#new-question').click();
  await f.page.locator('#question').fill('Second held submit');const secondHold=new Promise(resolve=>{f.controls.secondRelease=resolve;});
  // The first handler already captured its own held promise.
  f.controls.hold=secondHold;await f.page.locator('#ask-button').click();
  const firstDone=f.page.waitForResponse(response=>response.request().method()==='POST'&&JSON.parse(response.request().postData()).question==='First held submit');
  f.controls.release();await(await firstDone).finished();await f.page.waitForTimeout(100);
  assert.equal(await f.page.locator('#ask-button').isDisabled(),true);
  const secondDone=f.page.waitForResponse(response=>response.request().method()==='POST'&&JSON.parse(response.request().postData()).question==='Second held submit');
  f.controls.secondRelease();await(await secondDone).finished();await f.page.waitForURL('**/?answer=new');
  assert.equal(await f.page.locator('#ask-button').isDisabled(),false);
  report.checks.push({case:'stale-submit-cannot-reenable-button-owned-by-active-submit',status:'PASS'});await f.context.close();
 }
 {
  const f=await setup(),before=await followup(f);f.controls.expired=true;await f.page.locator('#history-toggle').click();await f.page.locator('#login-view').waitFor({state:'visible'});
  await f.page.locator('#username').fill('fixture');await f.page.locator('#password').fill('synthetic-only-password');
  await f.page.locator('#login-form button[type=submit]').click();
  try{await f.page.locator('#app-view').waitFor({state:'visible',timeout:5000});}catch(e){report.debug={url:f.page.url(),requests:f.calls.map(({api,method})=>({api,method})),body:await f.page.evaluate(()=>document.body.innerText.slice(0,200))};throw e;}
  await f.page.locator('#followup-button').waitFor({state:'visible'});
  const after={question:await f.page.locator('#question').inputValue(),followup_banner_hidden:await f.page.locator('#followup-context').isHidden(),stored_draft:await f.page.evaluate(()=>sessionStorage.getItem('klikai-login-draft:'))};
  await f.page.locator('#question').fill(before.draft);await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');
  const sent=JSON.parse(f.calls.find(call=>call.api==='/families'&&call.method==='POST').body);
  assert.equal(after.question,before.draft);assert.equal(after.followup_banner_hidden,false);assert.equal(sent.parent_run_id,'run-a');assert.equal(sent.parent_relation,'followup');
  report.checks.push({case:'session-expiry-restores-draft-and-explicit-parent',before,after,resubmitted_parent_run_id:sent.parent_run_id});
  await f.context.close();
 }
 {
  const f=await setup();f.controls.hold=new Promise(resolve=>{f.controls.release=resolve;});
  await f.page.locator('#question').fill('Submitted synthetic question');await f.page.locator('#ask-button').click();
  await f.page.waitForFunction(()=>!document.querySelector('#answer-card').hidden===false);
  await f.page.locator('#new-question').click();await f.page.locator('#question').fill('Later unsent draft');
  const completed=f.page.waitForResponse(response=>response.request().method()==='POST'&&response.url()===origin+'/api/pilot/families');f.controls.release();await(await completed).finished();await f.page.waitForTimeout(100);
  assert.equal(await f.page.locator('#question').inputValue(),'Later unsent draft');assert.equal(await f.page.locator('#answer-card').isHidden(),true);assert.equal(f.page.url(),origin+'/');
  report.checks.push({case:'late-submit-response-keeps-newer-workspace',question_after_response:await f.page.locator('#question').inputValue(),answer_hidden:await f.page.locator('#answer-card').isHidden(),url:f.page.url()});
  await f.context.close();
 }
 {
  const f=await setup();await f.page.locator('#question').fill('Already typed follow-up');await f.page.locator('#followup-button').click();
  assert.equal(await f.page.locator('#question').inputValue(),'Already typed follow-up');
  f.controls.historyFailure=true;await f.page.locator('#history-toggle').click();await f.page.locator('#history-list button').filter({hasText:b.question}).click();
  await f.page.waitForFunction(()=>document.querySelector('#global-status').textContent.length>0);
  assert.equal(await f.page.locator('#question').inputValue(),'Already typed follow-up');assert.equal(await f.page.locator('#followup-context').isHidden(),false);
  report.checks.push({case:'followup-preserves-typed-text-and-failed-history-keeps-parent',status:'PASS'});await f.context.close();
 }
 for(const failure of [false,true]){
  const f=await setup();await f.page.locator('#short-answer .citation').first().click();await f.page.locator('#source-content h3').waitFor();
  f.controls.sourceFailure=failure;await f.page.locator('#source-content details summary').click();await f.page.locator('#source-content button').filter({hasText:'2'}).click();
  await f.page.waitForFunction(failed=>failed?document.querySelector('#source-content .error'):document.querySelector('#source-content h3')?.textContent==='Second exact source',failure);
  assert.equal(await f.page.evaluate(()=>document.activeElement.id),'source-title');
  report.checks.push({case:'source-passage-replacement-retains-modal-focus',failure,status:'PASS'});await f.context.close();
 }
 {
  const f=await setup();await f.page.locator('#short-answer .citation').first().click();await f.page.locator('#source-content h3').waitFor();
  f.controls.sourceHold=new Promise(resolve=>{f.controls.sourceRelease=resolve;});await f.page.locator('#source-content details summary').click();await f.page.locator('#source-content button').filter({hasText:'2'}).click();
  await f.page.locator('[data-close=source-dialog]').focus();f.controls.sourceRelease();
  await f.page.waitForFunction(()=>document.querySelector('#source-content h3')?.textContent==='Second exact source');
  assert.equal(await f.page.evaluate(()=>document.activeElement.dataset.close),'source-dialog');
  report.checks.push({case:'late-source-response-does-not-steal-new-keyboard-focus',status:'PASS'});await f.context.close();
 }
 for(const check of report.checks)check.status='PASS';report.status='PASS';
}catch(e){report.status='ERROR';report.errors.push(e.stack);process.exitCode=1;}finally{await browser?.close();if(out)fs.writeFileSync(path.join(out,'followup-browser-report.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));}})();
