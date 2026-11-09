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
async function setup({legacy=false}={}){
 const context=await browser.newContext({viewport:{width:1100,height:900}}),page=await context.newPage(),calls=[];
 const savedA=legacy?{...a,long:{...a.short,run_id:'run-a-long'}}:a;
 const controls={expired:false,hold:null,release:null,historyFailure:false,sourceFailure:false,sourceHold:null,sourceRelease:null,losePost:false,submissionMissing:false};
 page.on('pageerror',e=>report.errors.push(e.message));
 await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{window.copiedText=text;}}}));
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());if(url.origin!==origin)throw Error('Unexpected external request '+url.origin);
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(url.pathname.startsWith('/assets/')){const file=url.pathname.slice(8);return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(assets,file))});}
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__','sl')});
  const api=url.pathname.slice('/api/pilot'.length);calls.push({api,method:req.method(),body:req.method()==='POST'?req.postData():null});
  if(api==='/login'){controls.expired=false;return route.fulfill({contentType:'text/html',body:'<script>location.replace("/?answer=a")</script>'});}
  if(api==='/session')return json({authenticated:true,user:{id:'synthetic',username:'fixture',role:'tester'},csrf_token:'fixture',capabilities:{project_beta:true}});
  if(api==='/capabilities')return json({project_beta:true});
  if(api==='/feedback')return json({feedback:[]});
  if(api.startsWith('/runs/')&&api.endsWith('/feedback'))return json({events:[]});
  if(api==='/families'&&req.method()==='GET')return controls.expired?route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({error:{code:'unauthorized'}})}):json({families:[a,b]});
  if(api==='/families'&&req.method()==='POST'){if(controls.losePost)return route.abort('failed');const held=controls.hold;if(held)await held;const payload=JSON.parse(req.postData());return json({family:family('new',payload.question)});}
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
 await page.goto(origin+'/?answer=a');await page.locator('#followup-button').waitFor({state:'visible'});
 return {context,page,calls,controls};
}
async function followup(f,text='Katere so njegove sopomenke?'){
 await f.page.locator('#followup-button').click();await f.page.locator('#question').fill(text);
 return {draft:await f.page.locator('#question').inputValue(),banner:await f.page.locator('#followup-context').innerText()};
}
(async()=>{try{
 if(out)fs.mkdirSync(out,{recursive:true});browser=await chromium.launch({headless:true});
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
   f.controls.losePost=false;await f.page.locator('#ask-button').click();await f.page.waitForURL('**/?answer=new');
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
