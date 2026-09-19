/* Offline browser checks: no service, account, generation, or feedback mutations. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://interface.test',output=process.env.KLIKAI_INTERFACE_UI_OUTPUT;
const configured=['jezikovna_svetovalnica','epravopis','zvezek','crossterm','zkb','tretji_sektor','solska','jezikovna_posta','jezikovna_urgenca','zadrege','gradivo'];
const registry=JSON.parse(fs.readFileSync(path.join(assets,'source-presentation.json'))),expected=registry.sources.filter(s=>configured.includes(s.suggested_key));
const fixture={answer_family_id:'saved-answer',question:'Previously saved question',locale:'sl',task:'reference',matches_available:true,short:{state:'complete',answer_kind:'generated',generation_status:'generated',run_id:'saved-run',delivery_token:'opaque',blocks:[{block_id:'C1',text:'A saved answer.',evidence_ids:['first','second']}],evidence:[{evidence_id:'first',local_number:1,source_slug:'epravopis',title:'First cited entry',source_status:'draft'},{evidence_id:'second',local_number:2,source_slug:'epravopis',title:'Second cited entry'},{evidence_id:'uncited',local_number:3,source_slug:'zkb',title:'Uncited discovery entry'}]}};
const report={scope:'Offline intercepted browser fixtures. Responsive browser viewports, not a physical phone or public-site check.',model_calls:0,operational_requests:0,checks:[],errors:[]};
let browser;
async function setup(width,{pending=false,matches=true,authenticated=true}={}){
 const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),calls=[];
 let complete=!pending,jobState='running';
 await page.clock.install();
 await page.addInitScript(()=>{Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>{if(window.clipboardFailure)throw Error('denied');window.copiedText=value;}}});});
 page.on('pageerror',e=>report.errors.push(e.message));
 const family=()=>complete?fixture:{...fixture,matches_available:matches,short:{state:jobState,job_id:'pending-job'}};
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());assert.equal(u.origin,origin,'No external requests');
  const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
  if(u.pathname.startsWith('/assets/')){const name=u.pathname.slice(8);assert(!name.includes('..'));return route.fulfill({contentType:name.endsWith('.mjs')?'text/javascript':name.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(assets,name))});}
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__','sl')});
  const api=u.pathname.slice('/api/pilot'.length);calls.push({api,method:req.method()});
  if(api==='/session')return json({authenticated,user:authenticated?{username:'fixture',role:'tester',shared_group:true,can_change_password:false}:null,csrf_token:'fixture',capabilities:{project_beta:true,documentary_sources:configured,beta_updated:'UNRELATED OLD DATE'}});
  if(api==='/capabilities')return json({project_beta:true,documentary_sources:configured,beta_updated:'UNRELATED NEW DATE'});
  if(api==='/feedback')return json({feedback:[]});
  if(api==='/families/saved-answer')return json(family());
  if(api==='/jobs/pending-job')return json({job_id:'pending-job',state:complete?'complete':jobState,progress:{stage:matches?'generation':'retrieval',matches_ready:matches}});
  if(api.endsWith('/cancel')){jobState='cancelled';return json({job_id:'pending-job',state:jobState});}
  if(api.endsWith('/sources'))return json({sources:matches?[{source_slug:'epravopis',returned_count:2}]:[],usage_final:complete});
  if(api.includes('/matches?')||api.endsWith('/matches'))return json({items:[{record_id:'r1',evidence_id:'first',evidence_ids:['first'],source_slug:'epravopis',title:'First cited entry'}],returned_count:1,next_offset:null,usage_final:complete});
  if(api.endsWith('/sources/first'))return json({title:'First cited entry',excerpt:'Full saved original.',source_slug:'epravopis',source_label:'ePravopis'});
  if(api.endsWith('/delivered'))return json({acknowledged:true});
  throw Error('Unexpected fixture API '+api);
 });
 return {context,page,calls,finish(){complete=true;},fail(){jobState='failed';}};
}
async function screenshot(page,name){if(output)await page.screenshot({path:path.join(output,name+'.png'),fullPage:true});}
async function legendAndCopy(width){
 const f=await setup(width),{page,calls}=f;
 try{
  await page.goto(origin+'/');await page.locator('#app-view').waitFor({state:'visible'});
  const label=await page.locator('#public-release').innerText();assert.match(label,/^Različica \d+\.\d+\.\d+ · Posodobljeno \d+\. \d+\. \d{4}$/);assert(!label.includes('UNRELATED'));
  assert.equal(await page.locator('[data-info]').count(),0);
  for(const key of ['about','notices','privacy'])assert.equal(await page.locator('footer [data-i18n='+key+']').evaluate(e=>e.tagName),'SPAN');
  await page.locator('#question').fill('Unsubmitted question draft');
  await page.locator('#source-legend-open').scrollIntoViewIfNeeded();await screenshot(page,'footer-'+width);
  const before=calls.length;await page.locator('#source-legend-open').click();
  assert.equal(await page.locator('#source-legend-list li').count(),expected.length);
  assert.deepEqual(await page.locator('#source-legend-list li>span:last-child').allTextContents(),expected.map(s=>s.labels.sl));
  assert.equal(await page.locator('#source-legend-list [aria-hidden=true]').count(),expected.length);
  assert.equal(await page.locator('#source-legend-list a, #source-legend-list button').count(),0);
  assert(await page.locator('#source-legend-list li[data-source=gradivo]').isVisible(),'Configured collection appears without any query hits');
  assert.equal(await page.locator('#source-legend-list li[data-source=loris],#source-legend-list li[data-source=ontology]').count(),0);
  await screenshot(page,'legend-'+width);await page.keyboard.press('Escape');
  assert.equal(await page.locator('#source-legend-dialog').getAttribute('open'),null);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'source-legend-open');
  await page.keyboard.press('Enter');await page.locator('[data-close=source-legend-dialog]').click();
  assert.equal(calls.length,before,'Opening/closing legend makes no requests');
  assert.equal(await page.locator('#question').inputValue(),'Unsubmitted question draft');
  await page.clock.setSystemTime(new Date('2035-01-01T00:00:00Z'));assert.equal(await page.locator('#public-release').innerText(),label);
  await page.goto(origin+'/?answer=saved-answer');await page.locator('#short-answer .citation').first().waitFor();
  const body=await page.locator('#short-answer').innerText(),answer=await page.locator('#short-answer').textContent();
  await page.locator('#copy-answer').click();assert.equal(await page.locator('#answer-status').innerText(),'Kopirano');assert.equal(await page.evaluate(()=>document.activeElement.id),'copy-answer','Successful copy retains button focus');
  const copied=await page.evaluate(()=>window.copiedText);
  assert.equal(copied,body.trim()+'\n\nViri\n[1] ePravopis — First cited entry\n[2] ePravopis — Second cited entry');
  assert(copied.includes('osnutek'));assert(!copied.includes('Uncited'));assert(!copied.includes('opaque'));assert(!copied.includes('/api/'));
  await screenshot(page,'copy-'+width);await page.evaluate(()=>window.clipboardFailure=true);await page.locator('#copy-answer').click();assert.equal(await page.locator('#answer-status').innerText(),'Besedilo lahko označite in kopirate.');assert.equal(await page.evaluate(()=>document.activeElement.id),'copy-answer','Clipboard failure retains button focus');
  const noRequest=calls.length;await page.locator('#source-legend-open').click();await page.keyboard.press('Escape');assert.equal(calls.length,noRequest);assert.equal(await page.locator('#short-answer').textContent(),answer);
  await page.locator('#expand-button').click();await page.locator('.explorer-badge').click();await page.locator('.explorer-entry').waitFor();
  const symbol=await page.locator('.explorer-badge .source-symbol').textContent();await page.locator('#source-legend-open').click();assert.equal(await page.locator('#source-legend-list [data-source=epravopis] .source-symbol').textContent(),symbol);await page.keyboard.press('Escape');assert.equal(await page.locator('.explorer-entry').count(),1);
  await page.locator('#short-answer .citation').first().click();await page.waitForFunction(()=>document.querySelector('#source-content').textContent.includes('Full saved original.'));await page.keyboard.press('Escape');
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.equal(calls.filter(c=>c.method==='POST'&&c.api==='/families').length,0);
  report.checks.push({case:'legend-copy-release',width,status:'PASS',configured_collections:expected.length,no_query_required:true,clipboard_failure_honest:true,copy_focus_retained_on_success_and_failure:true});
 }finally{await f.context.close();}
}
async function waiting(matches,ending){
 const f=await setup(390,{pending:true,matches}),{page,calls}=f;
 try{
  await page.goto(origin+'/?answer=saved-answer');await page.locator('#job-state').waitFor({state:'visible'});await page.waitForFunction(()=>document.querySelector('#job-state').dataset.stage==='generation'||document.querySelector('#job-state').dataset.stage==='retrieval');
  assert(!(await page.locator('#job-message').innerText()).includes('nekoliko dlje'));
  await page.clock.fastForward(21000);await page.waitForFunction(()=>document.querySelector('#job-message').textContent.includes('nekoliko dlje'));
  assert.equal(await page.locator('#job-message').innerText(),'Priprava odgovora traja nekoliko dlje.'+(matches?' Medtem lahko pod »Preberi več« pregledate najdene vire.':''));
  if(matches){await page.locator('#expand-button').click();await page.locator('.explorer-badge').click();await page.locator('.explorer-entry').waitFor();}
  const jobs=calls.filter(c=>c.api==='/jobs/pending-job').length;await page.locator('#source-legend-open').click();await page.keyboard.press('Escape');assert.equal(calls.filter(c=>c.api==='/jobs/pending-job').length,jobs);
  await screenshot(page,'waiting-'+(matches?'sources':'no-sources')+'-'+ending);
  if(ending==='complete')f.finish();else if(ending==='failed')f.fail();else await page.locator('#cancel-job').click();
  await page.clock.fastForward(1500);await page.waitForFunction(()=>document.querySelector('#job-state').hidden);
  assert.equal(calls.filter(c=>c.api==='/families'&&c.method==='POST').length,0);
  report.checks.push({case:'long-wait',matches,ending,status:'PASS'});
 }finally{await f.context.close();}
}
(async()=>{try{if(output)fs.mkdirSync(output,{recursive:true});browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--no-sandbox']});for(const width of [1100,390])await legendAndCopy(width);for(const [matches,ending] of [[true,'complete'],[false,'complete'],[true,'cancelled'],[false,'failed']])await waiting(matches,ending);
 const f=await setup(390,{authenticated:false});try{await f.page.goto(origin+'/');await f.page.locator('#login-view').waitFor({state:'visible'});assert.equal(await f.page.locator('footer [data-info]').count(),0);assert.equal(await f.page.locator('#source-legend-open').isVisible(),false);report.checks.push({case:'login-passive-footer',status:'PASS'});}finally{await f.context.close();}
 assert.deepEqual(report.errors,[]);report.status='PASS';}catch(e){report.status='FAIL';report.errors.push(e.stack);process.exitCode=1;}finally{await browser?.close();if(output)fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));}})();
