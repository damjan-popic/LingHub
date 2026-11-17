/* Controlled pending UI promises; every browser request is a synthetic fixture. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const assets=process.env.KLIKAI_DOCUMENT_BUSY_ASSETS||path.resolve(__dirname,'../pilot_ui'),origin='https://document-busy-controls.test';
const harness=String.raw`
import {documentUploads} from '/document-uploads.mjs';
const fixture=(id,name,state='complete')=>({document_id:id,name,state,options:{mode:'review'},warnings:[],processed:state==='complete'?1:0,total:1,sections:[{index:0,state:state==='complete'?'complete':'pending',text:'Fixture original.',output:state==='complete'?'Fixture result.':'',...(state==='complete'?{document_result:{mode:'review',status:'draft',proposed_text:'Fixture original.',edits:[]}}:{})}]});
let rows=[fixture('first','first.txt'),fixture('second','second.txt')];
window.calls=[];window.pending=[];window.chooserOpens=0;
const focusWatches=new Set(),nativeAdd=document.addEventListener.bind(document),nativeRemove=document.removeEventListener.bind(document);
document.addEventListener=(type,listener,options)=>{if(type==='focusin')focusWatches.add(listener);return nativeAdd(type,listener,options);};
document.removeEventListener=(type,listener,options)=>{if(type==='focusin')focusWatches.delete(listener);return nativeRemove(type,listener,options);};
window.focusWatchCount=()=>focusWatches.size;
const api=async(url,options={})=>{
 window.calls.push({url,method:options.method||'GET'});
 if(url==='/documents')return {documents:rows.map(({document_id,name,state})=>({document_id,name,state}))};
 if(url==='/documents/first'||url==='/documents/second'){
  const doc=rows.find(row=>url.endsWith('/'+row.document_id));
  if(window.deferLoad){window.deferLoad=false;return new Promise((resolve,reject)=>window.pending.push({kind:'load',resolve:()=>resolve(structuredClone(doc)),reject:()=>reject(Error('Load fixture failure'))}));}
  return structuredClone(doc);
 }
 if(url==='/documents/upload')return new Promise((resolve,reject)=>{
  const name=options.blob.name,id='uploaded-'+window.pending.length;
  window.pending.push({kind:'upload',resolve:()=>{const doc=fixture(id,name,'draft');rows.push(doc);resolve(structuredClone(doc));},reject:()=>{const error=Error('Invalid document fixture');error.code='document_invalid';reject(error);}});
 });
 if(url.endsWith('/export/txt'))return new Promise((resolve,reject)=>window.pending.push({kind:'download',resolve:()=>resolve(new Blob(['Synthetic report'])),reject:()=>reject(Error('Download fixture failure'))}));
 throw Error('Unexpected synthetic API path '+url);
};
const ui=documentUploads({root:document.querySelector('#workspace'),opener:document.querySelector('#document-open'),api,locale:()=>document.documentElement.lang,openFamily:()=>{},errorText:error=>error.message});
window.ui=ui;window.changeLocale=locale=>{document.documentElement.lang=locale;ui.locale();};
window.reopen=()=>{ui.reset();ui.session({documents:{enabled:true,evidence_review:true,evidence_review_only:true}});};
window.observeChooser=()=>{document.querySelector('input[type=file]').click=()=>window.chooserOpens++;};
ui.session({documents:{enabled:true,evidence_review:true,evidence_review_only:true}});
`;
const controls=['input[type=file]','[data-document-file-choose]','#workspace>div>form button[type=submit]','[data-document-list]'];
const locales=process.env.KLIKAI_DOCUMENT_BUSY_LOCALE?[process.env.KLIKAI_DOCUMENT_BUSY_LOCALE]:['sl','it'];
assert(locales.every(locale=>['sl','it'].includes(locale)));
async function pageFor(browser,locale){
 const context=await browser.newContext({acceptDownloads:true,serviceWorkers:'block'}),page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());assert.equal(url.origin,origin);const name=url.pathname.slice(1);
  let body,contentType;
  if(name==='harness.mjs'){body=harness;contentType='text/javascript';}
  else if(['document-uploads.mjs','pilot.css'].includes(name)){body=fs.readFileSync(path.join(assets,name),'utf8');contentType=name.endsWith('.mjs')?'text/javascript':'text/css';}
  else{assert.equal(name,'');body='<!doctype html><html lang="'+locale+'"><head><link rel="stylesheet" href="/pilot.css"></head><body><main><button id="document-open" type="button" aria-controls="workspace" hidden></button><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>';contentType='text/html';}
  await route.fulfill({status:200,contentType,body});
 });
 await page.goto(origin);await page.locator('#document-open').click();await page.locator('[data-document-list] option[value=first]').waitFor({state:'attached'});
 return {context,page,errors};
}
async function locked(page){
 assert.equal(await page.locator('#workspace').getAttribute('aria-busy'),'true');
 for(const selector of controls)assert.equal(await page.locator(selector).isDisabled(),true,selector+' must be disabled during an action');
 const before=await page.evaluate(()=>window.chooserOpens);
 await page.locator('[data-document-file-choose]').evaluate(node=>node.click());
 assert.equal(await page.evaluate(()=>window.chooserOpens),before,'Disabled chooser must not open a picker');
}
async function unlocked(page,{uploadDisabled=false}={}){
 await page.waitForFunction(()=>!document.querySelector('#workspace').hasAttribute('aria-busy'));
 assert.equal(await page.evaluate(()=>window.focusWatchCount()),0,'Completed action must remove its focus observer');
 for(const selector of controls)assert.equal(await page.locator(selector).isDisabled(),selector==='#workspace>div>form button[type=submit]'&&uploadDisabled,selector+' must restore its intended state');
}
async function upload(page,name){
 await page.locator('input[type=file]').setInputFiles({name,mimeType:'text/plain',buffer:Buffer.from('Synthetic upload')});
 await page.locator('#workspace>div>form button[type=submit]').click();
}
async function pending(page,count){await page.waitForFunction(count=>window.pending.length===count,count);}
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']}),checks=[];
 try{
  for(const locale of locales)for(const outcome of ['resolve','reject']){
   const {context,page,errors}=await pageFor(browser,locale),nextLocale=locale==='sl'?'it':'sl';
   await page.evaluate(()=>window.observeChooser());await upload(page,'first-upload.txt');await pending(page,1);await locked(page);
   assert.equal(await page.locator('input[type=file]').evaluate(node=>node.files[0].name),'first-upload.txt');
   await page.evaluate(locale=>window.changeLocale(locale),nextLocale);await locked(page);
   assert.equal(await page.locator('[data-document-file-choose]').textContent(),nextLocale==='it'?'Scegli file':'Izberite datoteko');
   assert.equal(await page.locator('[data-document-file-name]').textContent(),'first-upload.txt');
   await page.evaluate(outcome=>window.pending[0][outcome](),outcome);await unlocked(page);
   assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.url==='/documents/upload').length),1);
   if(outcome==='resolve'){
    assert.equal(await page.locator('[data-document-detail] h3').textContent(),'first-upload.txt');
    assert.equal(await page.locator('input[type=file]').evaluate(node=>node.files.length),0);
    assert.equal(await page.locator('[data-document-file-name]').textContent(),nextLocale==='it'?'Nessun file selezionato':'Datoteka ni izbrana');
   }else{
    assert.equal(await page.locator('input[type=file]').evaluate(node=>node.files[0].name),'first-upload.txt');
    assert.equal(await page.locator('[data-document-file-name]').textContent(),'first-upload.txt');
    assert.equal(await page.locator('[data-document-status]').textContent(),nextLocale==='it'?'Il file non è un documento Word valido o testo UTF-8.':'Datoteka ni veljaven dokument Word ali besedilo UTF-8.');
    await page.locator('input[type=file]').setInputFiles([]);await page.locator('#workspace>div>form button[type=submit]').click();
    assert.equal(await page.evaluate(()=>document.activeElement===document.querySelector('[data-document-file-choose]')),true);
    assert.equal(await page.locator('[data-document-status]').textContent(),nextLocale==='it'?'Scegli un file.':'Izberite datoteko.');
   }
   assert.deepEqual(errors,[]);checks.push({locale,case:'pending_upload_locale_rerender',outcome,controls_locked:true,selection_preserved_while_pending:true,restored_after_settlement:true,...(outcome==='reject'?{required_validation_focus_preserved:true}:{})});await context.close();
  }
  for(const locale of locales)for(const outcome of ['resolve','reject']){
   const {context,page,errors}=await pageFor(browser,locale);
   await page.locator('[data-document-list]').selectOption('first');await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='first.txt');
   await page.evaluate(()=>{window.observeChooser();document.querySelector('#workspace>div>form button[type=submit]').disabled=true;});
   const download=outcome==='resolve'?page.waitForEvent('download'):null;
   await page.getByRole('button',{name:locale==='sl'?'Prenesite poročilo TXT':'Scarica rapporto TXT',exact:true}).click();
   await pending(page,1);await locked(page);assert.equal(await page.locator('[data-document-list]').inputValue(),'first');
   await page.locator('[data-document-list]').press('ArrowDown');assert.equal(await page.locator('[data-document-list]').inputValue(),'first');
   assert.equal(await page.evaluate(()=>window.calls.filter(call=>call.url==='/documents/second').length),0);
   await page.evaluate(outcome=>window.pending[0][outcome](),outcome);await unlocked(page,{uploadDisabled:true});
   if(download)assert.equal((await download).suggestedFilename(),'klikai-report.txt');else assert.equal(await page.locator('[data-document-status]').textContent(),'Download fixture failure');
   assert.equal(await page.locator('[data-document-detail] h3').textContent(),'first.txt');
   await page.locator('[data-document-list]').selectOption('second');await page.waitForFunction(()=>document.querySelector('[data-document-detail] h3')?.textContent==='second.txt');
   assert.deepEqual(errors,[]);checks.push({locale,case:'pending_download',outcome,selector_stays_consistent:true,preexisting_disabled_state_preserved:true,subsequent_navigation_works:true});await context.close();
  }
  for(const locale of locales)for(const focusHistory of ['unmoved','other','other_then_body']){
   const {context,page,errors}=await pageFor(browser,locale);
   await page.locator('[data-document-list]').focus();await page.evaluate(()=>{window.deferLoad=true;});
   await page.locator('[data-document-list]').selectOption('first');await pending(page,1);await locked(page);
   if(focusHistory!=='unmoved')await page.locator('#document-open').focus();
   if(focusHistory==='other_then_body'){
    const {width,height}=page.viewportSize();await page.mouse.click(width-20,height-20);
    assert.equal(await page.evaluate(()=>document.activeElement===document.body),true,'Blank click must return focus to BODY');
   }
   await page.evaluate(()=>window.pending[0].resolve());await unlocked(page);
   const target=focusHistory==='unmoved'?'[data-document-list]':focusHistory==='other'?'#document-open':'body';
   assert.equal(await page.evaluate(target=>document.activeElement===document.querySelector(target),target),true,'Completion must respect focus history: '+focusHistory);
   assert.deepEqual(errors,[]);checks.push({locale,case:'pending_load_focus',focusHistory,previous_focus_restored_only_when_unmoved:true});await context.close();
  }
  for(const locale of locales){
   const {context,page,errors}=await pageFor(browser,locale);
   await upload(page,'old-session.txt');await pending(page,1);await locked(page);
   await page.evaluate(()=>window.reopen());assert.equal(await page.locator('#workspace').isHidden(),true);
   await page.locator('#document-open').click();await page.locator('[data-document-list] option[value=first]').waitFor({state:'attached'});await unlocked(page);
   await upload(page,'new-session.txt');await pending(page,2);await locked(page);
   await page.evaluate(()=>window.pending[0].resolve());await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,20)));
   await locked(page);assert.equal(await page.evaluate(()=>window.focusWatchCount()),1,'Old completion must not clear the new action focus observer');assert.equal(await page.locator('input[type=file]').evaluate(node=>node.files[0].name),'new-session.txt');assert.equal(await page.locator('[data-document-detail] h3').count(),0);
   await page.evaluate(()=>window.pending[1].resolve());await unlocked(page);
   assert.equal(await page.locator('[data-document-detail] h3').textContent(),'new-session.txt');assert.equal(await page.locator('input[type=file]').evaluate(node=>node.files.length),0);
   assert.deepEqual(errors,[]);checks.push({locale,case:'reset_then_new_pending_upload',old_completion_cannot_unlock_new_session:true,new_selection_owned:true});await context.close();
  }
  console.log(JSON.stringify({status:'PASS',scope:'Fully intercepted synthetic browser fixtures; zero live API/source/provider calls',checks}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exit(1);});
