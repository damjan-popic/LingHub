/* Delayed document responses are protocol fixtures; no services or models. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const origin='https://document-navigation.test',assets=path.resolve(__dirname,'../pilot_ui');
const harness=String.raw`
import {documentUploads} from '/document-uploads.mjs';
const running={document_id:'old',name:'previous.txt',state:'running',options:{mode:'review',review_strategy:'evidence_review_v1'},warnings:[],processed:0,total:1,sections:[{index:0,state:'pending',text:'Prejšnji izvirnik.',output:''}]};
const uploaded={document_id:'new',name:'new.txt',state:'draft',options:{},warnings:[],processed:0,total:2,sections:[{index:0,state:'pending',text:'Nadaljni opis je bil pripravljen s pomočjo tolmača.',output:''},{index:1,state:'pending',text:'Drugi odstavek ohrani svoj izvirnik.',output:''}]};
let rows=[running],reads=0,listReads=0;window.calls=[];window.failUpload=false;
const api=async(url,options={})=>{
 window.calls.push({url,method:options.method||'GET'});
 if(url==='/documents'){if(++listReads===1)return new Promise((resolve,reject)=>{window.releaseInitialList=()=>resolve({documents:[]});window.rejectInitialList=()=>reject(Error('Old initial list failure'));});return {documents:rows.map(d=>({document_id:d.document_id,name:d.name,state:d.state}))};}
 if(url==='/documents/old'){
  if(++reads%2===1)return structuredClone(running);
  return new Promise((resolve,reject)=>{window.releaseOld=()=>resolve({...structuredClone(running),state:'complete',processed:1,sections:[{...structuredClone(running.sections[0]),state:'complete',output:'Old completed output.',document_result:{mode:'review',status:'draft',edits:[],proposed_text:'Old completed draft.'}}]});window.rejectOld=()=>reject(Error('Old poll failure'));window.pollWaiting=true;});
 }
 if(url==='/documents/upload'){
  if(window.failUpload){const e=Error('fixture');e.code='document_invalid';throw e;}
  if(!rows.some(d=>d.document_id===uploaded.document_id))rows.push(uploaded);return structuredClone(uploaded);
 }
 if(url==='/documents/new/export/txt')return new Blob(['new document report']);
 if(url==='/documents/new/delete'){rows=rows.filter(d=>d.document_id!=='new');return {};}
 throw Error(url);
};
const ui=documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:()=>{},errorText:e=>e.message});
window.reopenSession=()=>{ui.session({documents:{enabled:false}});listReads=0;ui.session({documents:{enabled:true,evidence_review:true,evidence_review_only:true}});};
ui.session({documents:{enabled:true,evidence_review:true,evidence_review_only:true}});
`;
(async()=>{const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']}),checks=[];
try{for(const locale of ['sl','it'])for(const width of [390,1280])for(const listOutcome of ['success','failure']){
 const context=await browser.newContext({viewport:{width,height:1000},acceptDownloads:true}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());assert.equal(url.origin,origin);
  const name=url.pathname.slice(1),body=name==='harness.mjs'?harness:['document-uploads.mjs','pilot.css'].includes(name)?fs.readFileSync(path.join(assets,name),'utf8'):`<!doctype html><html lang="${locale}"><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/pilot.css"></head><body><main><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>`;
  await route.fulfill({status:200,contentType:name.endsWith('.mjs')?'text/javascript':name.endsWith('.css')?'text/css':'text/html',body});
 });
 await page.goto(origin);await page.locator('#workspace>details>summary').click();
 const text=await page.locator('#workspace').innerText();assert(text.includes('.docx'));assert(text.includes('UTF-8'));assert(text.includes('2 MB'));
 if(listOutcome==='failure'){await page.evaluate(()=>window.rejectInitialList());await page.waitForFunction(()=>document.querySelector('[data-document-status]').textContent==='Old initial list failure');await page.evaluate(()=>window.reopenSession());await page.locator('#workspace>details>summary').click();}
 const file=page.locator('input[type=file]');
 // The initial list request can finish after an upload's refreshed list.
 await file.setInputFiles({name:'new.txt',mimeType:'text/plain',buffer:Buffer.from('initial upload')});await page.locator('#workspace form button[type=submit]').first().click();await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='new.txt');
 await page.evaluate(failed=>failed?window.rejectInitialList():window.releaseInitialList(),listOutcome==='failure');await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,30)));
 assert.equal(await page.locator('[data-document-list]').inputValue(),'new');assert.equal(await page.locator('[data-document-list] option[value=new]').count(),1);assert.equal(await page.locator('[data-document-status]').textContent(),'');
 await page.locator('[data-document-list]').selectOption('old');await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='previous.txt');
 await page.waitForFunction(()=>window.pollWaiting===true);
 await file.setInputFiles({name:'new.txt',mimeType:'text/plain',buffer:Buffer.from('new file')});
 await page.locator('#workspace form button[type=submit]').first().click();
 await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='new.txt');
 assert.equal(await page.locator('[data-document-preview-note]').count(),1);assert.equal(await page.locator('.document-section').count(),2);
 assert.equal(await page.locator('.document-section pre').nth(1).textContent(),'Drugi odstavek ohrani svoj izvirnik.');
 // Release a request issued before the upload. It must not replace the preview.
 await page.evaluate(()=>window.releaseOld());await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,30)));
 assert.equal(await page.locator('[data-document-detail] h3').textContent(),'new.txt');assert.equal(await page.locator('[data-document-list]').inputValue(),'new');
 assert.equal(await page.locator('[data-clean-draft]').count(),0);assert(!(await page.locator('[data-document-detail]').innerText()).includes('Old completed output.'));
 assert((await page.locator('[data-document-detail]').innerText()).includes('0 / 2'));
 const report=page.waitForEvent('download');await page.getByRole('button',{name:locale==='sl'?'Prenesite poročilo TXT':'Scarica rapporto TXT',exact:true}).click();assert.equal((await report).suggestedFilename(),'klikai-report.txt');
 assert.equal(await page.evaluate(()=>window.calls.at(-1).url),'/documents/new/export/txt');
 assert.equal(await page.evaluate(()=>window.calls.filter(c=>c.url.endsWith('/start')).length),0);
 // A failed action displays its error. A later successful upload clears it.
 await page.evaluate(()=>{window.failUpload=true;});await file.setInputFiles({name:'broken.txt',mimeType:'text/plain',buffer:Buffer.from('invalid fixture')});
 await page.locator('#workspace form button[type=submit]').first().click();await page.waitForFunction(()=>document.querySelector('[data-document-status]').textContent.length>0);
 await page.evaluate(()=>{window.failUpload=false;});await file.setInputFiles({name:'new.txt',mimeType:'text/plain',buffer:Buffer.from('valid fixture')});
 await page.locator('#workspace form button[type=submit]').first().click();await page.waitForFunction(()=>!document.querySelector('#workspace').hasAttribute('aria-busy'));
 assert.equal(await page.locator('[data-document-status]').textContent(),'');assert.equal(await page.locator('[data-document-detail] h3').textContent(),'new.txt');
 // A rejected old poll must not replace the status or error of a new preview.
 await page.evaluate(()=>{window.pollWaiting=false;});await page.locator('[data-document-list]').selectOption('old');await page.waitForFunction(()=>window.pollWaiting===true);
 await file.setInputFiles({name:'new.txt',mimeType:'text/plain',buffer:Buffer.from('valid fixture')});await page.locator('#workspace form button[type=submit]').first().click();await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='new.txt');
 await page.evaluate(()=>window.rejectOld());await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,30)));
 assert.equal(await page.locator('[data-document-status]').textContent(),'');assert.equal(await page.locator('[data-document-detail] h3').textContent(),'new.txt');assert.equal(await page.locator('[data-clean-draft]').count(),0);
 assert.equal(await page.locator('.document-options select').count(),3);assert.equal(await page.locator('.document-options select').nth(0).inputValue(),'review');assert.equal(await page.locator('.document-options select').nth(1).inputValue(),'sl');
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);assert.deepEqual(errors,[]);
 if(process.env.KLIKAI_DOCUMENT_NAV_OUTPUT){fs.mkdirSync(process.env.KLIKAI_DOCUMENT_NAV_OUTPUT,{recursive:true});await page.screenshot({path:path.join(process.env.KLIKAI_DOCUMENT_NAV_OUTPUT,`navigation-${locale}-${width}-${listOutcome}.png`),fullPage:true});}
 checks.push({locale,width,listOutcome,late_initial_list_ignored:true,late_initial_list_error_ignored:listOutcome==='failure',relevant_initial_list_error_visible:listOutcome==='failure',late_previous_complete_poll_ignored:true,stale_status_error_and_draft_controls_ignored:true,new_report_download_identity:true,two_section_preview:true,explicit_start_only:true,error_recovery:true,formats_limits_visible:true,evidence_only_preserved:true});await context.close();
}console.log(JSON.stringify({status:'PASS',scope:'offline browser protocol fixtures; zero service/model calls',checks}));}finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exit(1);});
