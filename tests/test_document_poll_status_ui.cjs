/* Deterministic polling/status ownership fixtures; no real endpoints or models. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const origin='https://document-poll-status.test',assets=path.resolve(__dirname,'../pilot_ui');
const harness=String.raw`
import {documentUploads} from '/document-uploads.mjs';
const first={document_id:'first',name:'first.txt',state:'running',options:{mode:'review',review_strategy:'evidence_review_v1'},warnings:[],processed:0,total:1,sections:[{index:0,state:'pending',text:'First original.',output:''}]};
const second={document_id:'second',name:'second.txt',state:'draft',options:{},warnings:[],processed:0,total:1,sections:[{index:0,state:'pending',text:'Second original.',output:''}]};
let reads=0,nextTimer=1000000000;const pollTimers=new Map(),nativeSetTimeout=window.setTimeout,nativeClearTimeout=window.clearTimeout;
window.calls=[];window.polls=[];window.failDownload=false;
window.setTimeout=(fn,ms,...args)=>{if(ms===1500){const id=nextTimer++;pollTimers.set(id,()=>fn(...args));return id;}return nativeSetTimeout(fn,ms,...args);};
window.clearTimeout=id=>pollTimers.delete(id)||nativeClearTimeout(id);
window.firePoll=()=>{const item=pollTimers.entries().next().value;if(!item)throw Error('No scheduled poll');pollTimers.delete(item[0]);item[1]();};
window.pendingPollCount=()=>pollTimers.size;
window.rejectPoll=index=>window.polls[index].reject(Error('Temporary polling failure'));
window.completePoll=index=>window.polls[index].resolve({...structuredClone(first),state:'complete',processed:1,sections:[{...structuredClone(first.sections[0]),state:'complete',output:'Current completed result.',document_result:{mode:'review',status:'draft',edits:[],proposed_text:'Current completed draft.'}}]});
const api=async(url,options={})=>{
 window.calls.push({url,method:options.method||'GET'});
 if(url==='/documents')return {documents:[first,second].map(({document_id,name,state})=>({document_id,name,state}))};
 if(url==='/documents/first'){
  if(++reads===1)return structuredClone(first);
  return new Promise((resolve,reject)=>window.polls.push({resolve,reject}));
 }
 if(url==='/documents/second')return structuredClone(second);
 if(url==='/documents/first/export/txt'){
  if(window.failDownload)throw Error('Newer download failure');
  return new Blob(['first report']);
 }
 throw Error(url);
};
documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:()=>{},errorText:e=>e.message}).session({documents:{enabled:true,evidence_review:true,evidence_review_only:true}});
`;

const listRaceHarness=harness.replace('const api=async(url,options={})=>{','const api=(url,options={})=>{').replace(
 "if(url==='/documents')return {documents:[first,second].map(({document_id,name,state})=>({document_id,name,state}))};",
 `if(url==='/documents'){
   if(!window.rejectInitialList)return new Promise((resolve,reject)=>{window.rejectInitialList=()=>{reject(Error('Previous list failure'));queueMicrotask(()=>document.querySelector('#workspace form').requestSubmit());};});
   return {documents:[second].map(({document_id,name,state})=>({document_id,name,state}))};
  }
  if(url==='/documents/upload')return structuredClone(second);`);

async function pageFor(browser,locale,width,moduleHarness=harness,selectFirst=true){
 const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());assert.equal(url.origin,origin);
  const name=url.pathname.slice(1),body=name==='harness.mjs'?moduleHarness:['document-uploads.mjs','pilot.css'].includes(name)?fs.readFileSync(path.join(assets,name),'utf8'):`<!doctype html><html lang="${locale}"><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/pilot.css"></head><body><main><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>`;
  await route.fulfill({status:200,contentType:name.endsWith('.mjs')?'text/javascript':name.endsWith('.css')?'text/css':'text/html',body});
 });
 await page.goto(origin);await page.locator('#workspace>details>summary').click();
 if(selectFirst){await page.locator('[data-document-list]').selectOption('first');
 await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='first.txt');}
 return {context,page,errors};
}
async function poll(page,index){
 await page.evaluate(()=>window.firePoll());await page.waitForFunction(index=>window.polls.length>index,index);
}
const status=page=>page.locator('[data-document-status]').textContent();
const settle=page=>page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,20)));

(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']}),checks=[];
 try{
  for(const locale of ['sl','it'])for(const width of [390,1280]){
   {
    const {context,page,errors}=await pageFor(browser,locale,width);
    await poll(page,0);await page.evaluate(()=>window.rejectPoll(0));
    await page.waitForFunction(()=>document.querySelector('[data-document-status]').textContent==='Temporary polling failure');
    await poll(page,1);await page.evaluate(()=>window.completePoll(1));
    await page.waitForFunction(()=>document.querySelector('[data-document-detail]').textContent.includes('Current completed result.'));
    assert.equal(await status(page),'','Successful current poll must clear its own failure');
    assert.equal(await page.evaluate(()=>window.pendingPollCount()),0);
    assert.equal(await page.locator('[data-document-list]').inputValue(),'first');
    assert.deepEqual(errors,[]);checks.push({locale,width,case:'poll_failure_then_completion',poll_owned_error_cleared:true});await context.close();
   }
   {
    const {context,page,errors}=await pageFor(browser,locale,width);
    await poll(page,0);await page.evaluate(()=>window.rejectPoll(0));
    await page.waitForFunction(()=>document.querySelector('[data-document-status]').textContent==='Temporary polling failure');
    // A second old-generation poll is pending when a newer download fails.
    await poll(page,1);await page.evaluate(()=>{window.failDownload=true;});
    await page.getByRole('button',{name:locale==='sl'?'Prenesite poročilo TXT':'Scarica rapporto TXT',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('[data-document-status]').textContent==='Newer download failure');
    await page.evaluate(()=>window.completePoll(1));await settle(page);
    assert.equal(await status(page),'Newer download failure');
    assert(!(await page.locator('[data-document-detail]').textContent()).includes('Current completed result.'));
    await poll(page,2);await page.evaluate(()=>window.completePoll(2));
    await page.waitForFunction(()=>document.querySelector('[data-document-detail]').textContent.includes('Current completed result.'));
    assert.equal(await status(page),'Newer download failure','Poll recovery must preserve newer action errors');
    assert.deepEqual(errors,[]);checks.push({locale,width,case:'newer_action_error',old_poll_inert:true,newer_download_error_retained:true});await context.close();
   }
   for(const outcome of ['complete','failure']){
    const {context,page,errors}=await pageFor(browser,locale,width);
    await poll(page,0);await page.locator('[data-document-list]').selectOption('second');
    await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='second.txt');
    await page.evaluate(outcome=>outcome==='complete'?window.completePoll(0):window.rejectPoll(0),outcome);await settle(page);
    assert.equal(await status(page),'');assert.equal(await page.locator('[data-document-list]').inputValue(),'second');
    assert.equal(await page.locator('[data-document-detail] h3').textContent(),'second.txt');
    assert(!(await page.locator('[data-document-detail]').textContent()).includes('Current completed result.'));
    assert.deepEqual(errors,[]);checks.push({locale,width,case:'late_previous_selection',outcome,old_poll_inert:true});await context.close();
   }
   {
    const {context,page,errors}=await pageFor(browser,locale,width,listRaceHarness,false);
    await page.locator('input[type=file]').setInputFiles({name:'second.txt',mimeType:'text/plain',buffer:Buffer.from('Second original.')});
    // refresh rethrows while current; the queued upload changes selection before
    // the outer session catch runs. Its error must retain the initial owner.
    await page.evaluate(()=>window.rejectInitialList());
    await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='second.txt');
    await settle(page);
    assert.equal(await status(page),'','Old list error must not acquire newer upload ownership');
    assert.equal(await page.locator('[data-document-list]').inputValue(),'second');
    assert.deepEqual(errors,[]);checks.push({locale,width,case:'list_rethrow_selection_microtask',old_list_error_inert:true});await context.close();
   }
  }
  console.log(JSON.stringify({status:'PASS',scope:'Offline protocol fixtures; no services, sources, databases or models',checks}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exit(1);});
