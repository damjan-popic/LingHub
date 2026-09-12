#!/usr/bin/env node
/* Real browser/auth/migration/storage, explicitly synthetic data and generation. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const args=process.argv.slice(2),arg=k=>args[args.indexOf(k)+1],base=arg('--base-url'),dir=arg('--fixture-directory'),output=arg('--output');
assert(['127.0.0.1','localhost'].includes(new URL(base).hostname));assert.equal(fs.statSync(dir).mode&0o077,0);
const account=JSON.parse(fs.readFileSync(path.join(dir,'accounts.json'))),api=base+'/api/pilot';
const report={schema:'pilot-adoption-browser-v1',synthetic_accounts:true,synthetic_generator:true,checks:[],page_errors:[]};let browser,page;
const check=async(name,fn)=>{await fn();report.checks.push({name,status:'PASS'});};
const login=async password=>{await page.locator('#username').fill(account.username);await page.locator('#password').fill(password);await page.locator('#login-form button[type=submit]').click();};
const req=async(route,body)=>{const s=await(await page.request.get(api+'/session')).json();return body?page.request.post(api+route,{headers:{Origin:base,'X-CSRF-Token':s.csrf_token},data:body}):page.request.get(api+route);};
(async()=>{try{
 browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--disable-dev-shm-usage']});page=await browser.newPage({viewport:{width:1280,height:980}});page.on('pageerror',e=>report.page_errors.push(e.name));
 await page.goto(base+'/');
 await check('retired_session_password_rejected',async()=>{
  const old=await page.request.get(api+'/session',{headers:{Cookie:'klikai_session='+account.old_session}});assert.equal((await old.json()).authenticated,false);
  const response=page.waitForResponse(r=>r.url()===api+'/login');await login(account.old_password);assert.equal((await response).status(),401);assert(await page.locator('#login-view').isVisible());
 });
 await check('individual_operator_login_forced_password_change',async()=>{
  await login(account.password);await page.locator('#password-view').waitFor({state:'visible'});
  await page.locator('#current-password').fill(account.password);await page.locator('#new-password').fill(account.permanent_password);await page.locator('#confirm-password').fill(account.permanent_password);await page.locator('#password-form button[type=submit]').click();await page.locator('#app-view').waitFor({state:'visible'});
 });
 let original;
 await check('retained_saved_answer_exact_source_and_ownership',async()=>{
  const before=await(await req('/families/'+account.saved_family)).json();original=before.short.answer_md;assert.equal(before.short.historical_evidence.status,'exact_retained_record');
  await page.locator('#history-toggle').click();await page.locator('#history-list button').filter({hasText:'SYNTHETIC retained browser question'}).click();await page.locator('#answer-card').waitFor({state:'visible'});
  assert((await page.locator('#short-answer').textContent()).includes(original));assert(await page.locator('#expand-button').isDisabled());
  const source=await req('/families/'+account.saved_family+'/sources/probe-e1');assert.equal(source.status(),200);assert.equal((await source.json()).excerpt,'SYNTHETIC retained browser question');
 });
 await check('new_question_signed_answer_feedback_saved_reload',async()=>{
  await page.locator('#new-question').click();await page.locator('#question').fill('SYNTHETIC new adoption browser question');await page.locator('#ask-button').click();
  await page.waitForFunction(()=>{const input=document.querySelector('#short-feedback input[type=radio]');return input&&!input.disabled;});
  await page.locator('#short-feedback input[value="4"]').check();await page.locator('#short-feedback textarea').fill('SYNTHETIC adoption browser feedback');
  const posted=page.waitForResponse(r=>r.url()===api+'/feedback'&&r.request().method()==='POST');await page.locator('#short-feedback button[type=submit]').click();assert.equal((await posted).status(),200);
  await page.reload();await page.locator('#short-feedback input[value="4"]').waitFor();assert(await page.locator('#short-feedback input[value="4"]').isChecked());
  assert.equal((await(await req('/families/'+account.saved_family)).json()).short.answer_md,original);
 });
 await check('logout_clears_authentication',async()=>{
  await page.locator('#account-button').click();await page.locator('#logout-button').click();await page.locator('#login-view').waitFor({state:'visible'});assert.equal((await(await req('/session')).json()).authenticated,false);
 });
 assert.deepEqual(report.page_errors,[]);report.status='PASS';
}catch(error){report.status='FAIL';report.failure_type=error.name;report.failure_message=String(error.message).replaceAll(account.password,'[private]').replaceAll(account.old_password,'[private]').replaceAll(account.permanent_password,'[private]');process.exitCode=1;}finally{if(browser)await browser.close();fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n',{mode:0o600});console.log(JSON.stringify({status:report.status,checks:report.checks.length,output}));}})();
