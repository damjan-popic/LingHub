#!/usr/bin/env node
const {openComposerOptions,selectComposerOption}=require('./pilot_ui_options.cjs');
/* Actual UI/auth/jobs/SQLite; synthetic writer explicitly controlled in private fixture only. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const args=process.argv.slice(2),arg=k=>args[args.indexOf(k)+1],home=arg('--base-url'),dir=arg('--fixture-directory'),output=arg('--output');
assert.equal(fs.statSync(dir).mode&0o077,0);assert(['127.0.0.1','localhost'].includes(new URL(home).hostname));
const accounts=JSON.parse(fs.readFileSync(path.join(dir,'accounts.json'))),cap=path.join(dir,'synthetic-capacity.txt'),api=home+'/api/pilot';
const report={schema:'klikai.inline-clarification-browser.v1',synthetic_generator:true,synthetic_accounts:true,checks:[],errors:[],requests:[]};let browser,page;
const check=async(name,fn)=>{await fn();report.checks.push({name,status:'PASS'});};
const wait=()=>page.waitForFunction(()=>document.querySelector('#short-feedback .rating')&&!document.querySelector('#short-feedback .rating').disabled);
const req=async(route,body)=>{const s=await(await page.request.get(api+'/session')).json();return body?page.request.post(api+route,{headers:{Origin:home,'X-CSRF-Token':s.csrf_token},data:body}):page.request.get(api+route);};
const families=async()=>((await(await req('/families')).json()).families);
const submit=async()=>{const response=page.waitForResponse(r=>r.url()===api+'/families'&&r.request().method()==='POST');await page.locator('#ask-button').click();return response;};
(async()=>{try{
 browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--disable-dev-shm-usage']});page=await browser.newPage({viewport:{width:1280,height:980}});
 page.on('pageerror',e=>report.errors.push(e.message));page.on('request',r=>{if(r.url().startsWith(api)&&r.method()==='POST')report.requests.push(new URL(r.url()).pathname);});
 await page.goto(home+'/it/');await page.locator('#username').fill('ui-tester');await page.locator('#password').fill(accounts['ui-tester'].password);await page.locator('#login-form button[type=submit]').click();await page.locator('#app-view').waitFor({state:'visible'});
 let before=await families();let focusFamily;
 await check('focus_clarification_inline_no_family_rating_or_generation',async()=>{
  await page.locator('#question').fill('Preveri morfologijo in sopomenke.');await selectComposerOption(page,'#input-language','sl');await openComposerOptions(page);await page.locator('#answer-locale').selectOption('it');
  let response=await submit();assert.equal(response.status(),200);assert.equal((await response.json()).status,'clarification_required');await page.locator('#focus-control').waitFor({state:'visible'});
  const labelBox=await page.locator('label[for=focus]').boundingBox(),inputBox=await page.locator('#focus').boundingBox();assert(inputBox.y-labelBox.y-labelBox.height>=8);assert.equal(await page.locator('#answer-card').isVisible(),false);assert.equal(await page.locator('#comparison').isVisible(),false);assert.equal(await page.locator('#short-feedback .rating').count(),0);assert.equal((await families()).length,before.length);assert.equal(await page.locator('#ask-button').innerText(),'Continua');
  await page.locator('[data-locale=sl]').click();assert.equal(await page.locator('#input-language').inputValue(),'sl');assert.equal(await page.locator('#answer-locale').inputValue(),'it');assert.equal(await page.locator('#question').inputValue(),'Preveri morfologijo in sopomenke.');
  await page.locator('#focus').fill('otrok');
  response=page.waitForResponse(r=>r.url()===api+'/families'&&r.request().method()==='POST');await page.locator('#ask-button').evaluate(b=>{b.click();b.click();});const posted=await response;assert.equal(posted.status(),200);await wait();
  const all=await families();assert.equal(all.length,before.length+1);focusFamily=all[0];assert.equal(focusFamily.focus,'otrok');assert.equal(focusFamily.question,'Preveri morfologijo in sopomenke.');assert.equal(focusFamily.input_language,'sl');assert.equal(focusFamily.locale,'it');assert.equal(await page.locator('#clarification').isVisible(),false);
  await page.reload();await wait();assert.equal(await page.locator('#focus').inputValue(),'otrok');assert.equal((await families()).length,all.length);
 });
 await check('missing_language_continues_once_without_quote_syntax',async()=>{
  await page.locator('#new-question').click();await selectComposerOption(page,'#task','reference');await page.locator('#question').fill('Poišči kolokacije.');await selectComposerOption(page,'#input-language','');
  const count=(await families()).length;await submit();await page.locator('#focus-control').waitFor({state:'visible'});await page.locator('#focus').fill('mesto');
  let response=await submit();assert.equal((await response.json()).clarification.code,'input_language_required');assert.equal((await families()).length,count);assert.equal(await page.locator('#focus').inputValue(),'mesto');assert.equal(await page.locator('#answer-card').isVisible(),false);
  await selectComposerOption(page,'#input-language','sl');response=await submit();assert.equal(response.status(),200);await wait();assert.equal((await families()).length,count+1);
 });
 await check('capacity_failure_is_not_answer_and_source_mode_remains_usable',async()=>{
  fs.writeFileSync(cap,'unavailable');await page.locator('#new-question').click();await page.locator('#question').fill('SYNTHETIC new reference question.');const count=(await families()).length;
  const response=await submit();assert.equal(response.status(),503);assert.equal((await response.json()).error.code,'generation_waiting_capacity');await page.locator('#capacity-state').waitFor({state:'visible'});assert.equal(await page.locator('#answer-card').isVisible(),false);assert.equal(await page.locator('#job-state').isVisible(),false);assert.equal((await families()).length,count);
  await page.locator('#capacity-sources').click();assert.equal(await page.locator('#task').inputValue(),'source_search');assert.equal(await page.locator('#question').inputValue(),'SYNTHETIC new reference question.');assert.equal((await submit()).status(),200);await wait();assert.equal((await families()).length,count+1);
 });
 await check('saved_source_feedback_and_clarification_exclusion_after_capacity_loss',async()=>{
  await page.goto(home+'/?answer='+focusFamily.answer_family_id);await wait();await page.locator('#short-sources .source-button').first().click();await page.locator('#source-content blockquote').waitFor();assert((await page.locator('#source-content blockquote').innerText()).includes('SYNTHETIC SOURCE'));await page.locator('[data-close=source-dialog]').click();
  await page.locator('#short-feedback .rating').first().click();await page.waitForFunction(()=>document.querySelector('#short-feedback .feedback-ack').textContent.length>0);await page.reload();await wait();assert.equal(await page.locator('#short-feedback .rating').first().getAttribute('aria-pressed'),'true');
  const feedback=(await(await req('/feedback')).json()).feedback;assert.equal(feedback.length,1);assert.equal(feedback[0].target.capability,'reference');
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 });
 assert.deepEqual(report.errors,[]);report.status='PASS';
}catch(e){report.status='FAIL';report.failure={name:e.name,message:e.message};process.exitCode=1;if(page)await page.screenshot({path:path.join(dir,'clarification-failure.png'),fullPage:true});}finally{if(browser)await browser.close();fs.writeFileSync(output,JSON.stringify(report,null,2)+'\n',{mode:0o600});console.log(JSON.stringify({status:report.status,checks:report.checks.length,output}));}})();
