#!/usr/bin/env node
/* Synthetic browser fixture only; exercises actual auth/store/UI without model calls. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const args=process.argv.slice(2),arg=k=>args[args.indexOf(k)+1],base=arg('--base-url'),dir=arg('--fixture-directory'),output=arg('--output');
assert.equal(fs.statSync(dir).mode&0o077,0);assert(['127.0.0.1','localhost'].includes(new URL(base).hostname));
const accounts=JSON.parse(fs.readFileSync(path.join(dir,'accounts.json'))),api=base+'/api/pilot';
const report={schema:'klikai.human-feedback-ui.v1',synthetic_accounts:true,synthetic_generator:true,checks:[],errors:[]};let browser,page;
const check=async(name,fn)=>{await fn();report.checks.push({name,status:'PASS'});};
const login=async name=>{await page.locator('#username').fill(name);await page.locator('#password').fill(accounts[name].password);await page.locator('#login-form button[type=submit]').click();await page.locator('#app-view').waitFor({state:'visible'});};
const wait=kind=>page.waitForFunction(k=>{const input=document.querySelector('#'+k+'-feedback input[type=radio]');return !document.querySelector('#answer-card').hidden&&input&&!input.disabled;},kind);
const logout=async()=>{await page.locator('#account-button').click();await page.locator('#logout-button').click();await page.locator('#login-view').waitFor({state:'visible'});};
const req=async(route,body)=>{const s=await(await page.request.get(api+'/session')).json();return body?page.request.post(api+route,{headers:{Origin:base,'X-CSRF-Token':s.csrf_token},data:body}):page.request.get(api+route);};
const display=async kind=>page.locator('#'+kind+'-answer > p').evaluateAll(nodes=>nodes.map(n=>n.textContent).join('\n\n'));
const records=[];
(async()=>{try{
 browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--disable-dev-shm-usage']});page=await browser.newPage({viewport:{width:1280,height:980}});page.on('pageerror',e=>report.errors.push(e.message));
 await page.goto(base+'/');await login('ui-tester');
 for(const locale of ['sl','it'])await check(locale+'_unselected_five_point_submit_short_and_expanded',async()=>{
  if(records.length)await page.locator('#new-question').click();await page.locator('[data-locale='+locale+']').click();
  const original='  SYNTHETIC '+locale+' — izvirno vprašanje 🙂\nDodatna vrstica.  ';
  await page.locator('#question').fill(original);await page.locator('#ask-button').click();await wait('short');
  assert.equal(await page.locator('#short-feedback input[type=radio]').count(),5);assert.equal(await page.locator('#short-feedback input:checked').count(),0);assert.equal(await page.locator('#short-feedback button[type=submit]').count(),1);assert.equal(await page.locator('#short-feedback button[type=submit]').isDisabled(),true);assert.equal(await page.locator('#compare-button').isVisible(),false);
  for(const kind of ['short','long']){
   if(kind==='long'){await page.locator('#expand-button').click();await wait('long');assert.equal(await page.locator('#long-feedback input:checked').count(),0);}
   const form=page.locator('#'+kind+'-feedback'),before=(await(await req('/feedback')).json()).feedback.length;
   await form.locator('input[value="4"]').check();assert.equal((await(await req('/feedback')).json()).feedback.length,before,'selection must not submit');
   await form.locator('textarea').fill('SYNTHETIC '+locale+' '+kind+' — komentar 🙂');
   const response=page.waitForResponse(r=>r.url()===api+'/feedback'&&r.request().method()==='POST');await form.locator('button[type=submit]').click();assert.equal((await response).status(),200);await form.locator('.feedback-ack').filter({hasText:/./}).waitFor();
   const exact=await display(kind),family=(await(await req('/families')).json()).families[0];records.push({run_id:family[kind].run_id,original,display:exact,kind,locale});
   await page.reload();await wait(kind);assert.equal(await page.locator('#'+kind+'-feedback input[value="4"]').isChecked(),true);assert.equal(await page.locator('#question').inputValue(),original);assert.equal(await display(kind),exact);
  }
 });
 await check('mobile_form_and_locale_draft_continuity',async()=>{
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.locator('#short-feedback input[value="3"]').check();await page.locator('#short-feedback textarea').fill('SYNTHETIC pending draft');await page.locator('[data-locale=sl]').click();assert.equal(await page.locator('#short-feedback input[value="3"]').isChecked(),true);assert.equal(await page.locator('#short-feedback textarea').inputValue(),'SYNTHETIC pending draft');assert.equal(await page.locator('#short-feedback .feedback-ack').textContent(),'Spremembe še niso shranjene.');await page.locator('[data-locale=it]').click();
  await page.screenshot({path:path.join(dir,'human-feedback-it-mobile.png'),fullPage:true});
 });
 await check('admin_exact_prompt_answer_rating_and_exports',async()=>{
  await logout();await login('ui-admin');await page.locator('#account-button').click();await page.locator('#account-admin').click();await page.locator('[data-admin-tab=feedback]').click();await page.locator('.admin-record').first().waitFor();assert.equal(await page.locator('.admin-record').count(),records.length);
  const data=await(await req('/admin/export',{reauth_password:accounts['ui-admin'].password})).json(),events=data.accounts.flatMap(a=>a.feedback_revisions);
  for(const saved of records){const event=events.find(e=>e.observation?.run_id===saved.run_id);assert(event);assert.equal(event.quality_score,4);assert.equal(event.observation.original_prompt,saved.original);assert.equal(event.observation.displayed_answer,saved.display);assert.equal(event.observation.tier,saved.kind);assert.equal(event.observation.human_eligible,false);assert.equal(event.observation.participant.origin,'synthetic');}
  const csv=await req('/admin/export',{reauth_password:accounts['ui-admin'].password,format:'csv'});assert.equal(csv.status(),200);assert((await csv.text()).includes('original_prompt'));assert((await csv.text()).includes('SYNTHETIC'));
  assert.equal(await page.locator('[data-i18n=admin_export_csv]').count(),1);assert.equal(await page.locator('[data-i18n=admin_export_json]').count(),1);
 });
 assert.deepEqual(report.errors,[]);report.status='PASS';report.saved_feedback_events=records.length;
}catch(error){report.status='FAIL';report.failure={name:error.name,message:error.message};process.exitCode=1;}finally{if(browser)await browser.close();fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n',{mode:0o600});console.log(JSON.stringify({status:report.status,checks:report.checks.length,output}));}})();
