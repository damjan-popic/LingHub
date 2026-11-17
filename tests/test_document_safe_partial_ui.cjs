/* Offline browser fixtures only; every request is fulfilled locally. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const origin='https://document-safe-partial.test',assets=path.resolve(__dirname,'../pilot_ui');
const harness=String.raw`
import {documentUploads} from '/document-uploads.mjs';
const text='🏛️ Nadaljni zapis.',edit={start:3,end:11,original:'Nadaljni',replacement:'Nadaljnji'};
const doc={document_id:'safe',name:'safe.docx',state:'complete',has_word_original:true,options:{mode:'review',review_strategy:'evidence_review_v1'},warnings:['document_page_footer_not_reviewed'],processed:1,total:1,
 draft_exports:{complete_available:false,partial_review:{available:true,applied_edits:1,withheld_edits:0,incomplete_sections:1,sections:[{index:0,applied_edits:1,applied_edit_indexes:[0],original_preserved:false,incomplete:true}]}},
 sections:[{index:0,location:'word/document.xml/body[1]/p[1]@0:18',state:'complete',text,output:'Stored review summary.',limitations:[{code:'document_transform_failed'}],document_result:{mode:'review',status:'draft',proposed_text:'UNSUPPORTED WHOLE PROPOSAL',edits:[edit],review:{overview:{counts:{}},findings:[],checks:[{check_id:'comma_prediction',status:'partial',checked_extent:{start:0,end:8,complete:false,offset_convention:'unicode_codepoint_half_open'}}]}}}]};
const unsupported={document_id:'it',name:'italiano.txt',state:'draft',options:{},warnings:[],processed:0,total:1,sections:[{index:0,state:'pending',text:'Testo italiano.',output:''}]};
window.calls=[];
const api=async(url,options={})=>{
 window.calls.push({url,method:options.method||'GET',body:options.body});
 if(url==='/documents')return{documents:[doc,unsupported].map(({document_id,name,state})=>({document_id,name,state}))};
 if(url==='/documents/safe')return structuredClone(doc);
 if(url==='/documents/it')return structuredClone(unsupported);
 if(url==='/documents/safe/draft/txt?partial=true'||url==='/documents/safe/draft/docx?partial=true')return new Blob(['Synthetic labeled partial']);
 if(url.endsWith('/start'))throw Object.assign(Error('GENERIC API MESSAGE'),{code:'document_review_ineligible'});
 throw Error('Unexpected fixture route '+url);
};
const ui=documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:()=>{},errorText:e=>e.message});
window.ui=ui;
// Evidence is on, optional AI/translation/summary remain off.
ui.session({documents:{enabled:true,evidence_review:true,evidence_review_only:false,ai_suggestions:false,private_translation:false,editorial_summary:false}});
`;
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']}),checks=[];
 try{
  for(const locale of ['sl','it'])for(const width of [390,1100]){
   const context=await browser.newContext({viewport:{width,height:1000},acceptDownloads:true,serviceWorkers:'block'}),page=await context.newPage(),errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await context.route('**/*',async route=>{
    const url=new URL(route.request().url());assert.equal(url.origin,origin);
    const name=url.pathname.slice(1);let body,contentType;
    if(name==='harness.mjs'){body=harness;contentType='text/javascript';}
    else if(['document-uploads.mjs','pilot.css'].includes(name)){body=fs.readFileSync(path.join(assets,name),'utf8');contentType=name.endsWith('.mjs')?'text/javascript':'text/css';}
    else{assert.equal(name,'');body='<!doctype html><html lang="'+locale+'"><head><meta name="viewport" content="width=device-width"></head><body><main><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>';contentType='text/html';}
    await route.fulfill({status:200,body,contentType});
   });
   await page.goto(origin);await page.locator('#workspace>details>summary').click();await page.locator('[data-document-list]').selectOption('safe');
   await page.locator('[data-partial-draft=txt]').waitFor();
   assert.equal(await page.locator('[data-partial-draft]').count(),2);
   assert.equal(await page.locator('[data-clean-draft]').count(),0);
   assert.equal(await page.locator('[data-safe-partial-counts]').count(),1);
   assert.equal(await page.locator('[data-safe-partial-section]').count(),1);
    assert((await page.locator('[data-document-detail]').textContent()).includes('8/18'));
   const preview=page.locator('.document-section .document-text').last();
   assert.equal(await preview.textContent(),'🏛️ Nadaljnji zapis.');
   assert(!((await page.locator('[data-document-detail]').textContent()).includes('UNSUPPORTED WHOLE PROPOSAL')));
   for(const fmt of ['txt','docx']){
    const downloaded=page.waitForEvent('download');await page.locator('[data-partial-draft='+fmt+']').click();
    assert.equal((await downloaded).suggestedFilename(),'klikai-partial-draft.'+fmt);
   }
   assert.deepEqual(await page.evaluate(()=>window.calls.filter(x=>x.url.includes('/draft/')).map(x=>x.url)),['/documents/safe/draft/txt?partial=true','/documents/safe/draft/docx?partial=true']);
   await page.locator('[data-document-list]').selectOption('it');
   await page.locator('.document-options').waitFor();
   const source=page.locator('.document-options select').nth(1);await source.selectOption('it');
   const start=page.locator('.document-options button[type=submit]');assert.equal(await start.isDisabled(),true);
   assert.equal(await source.inputValue(),'it','Unsupported Italian is not silently changed to Slovene');
   assert.equal(await page.locator('[data-document-review-language]').isVisible(),true);
   await page.locator('.document-options').evaluate(form=>form.dispatchEvent(new Event('submit',{cancelable:true,bubbles:true})));
   assert.equal(await page.evaluate(()=>window.calls.filter(x=>x.url.endsWith('/start')).length),0);
   await source.selectOption('sl');await start.click();
    const message=await page.locator('[data-document-status]').textContent();
    assert(message.includes(locale==='it'?'La revisione in italiano non è ancora disponibile':'Pregled italijanskih dokumentov v tem načinu še ni na voljo'));
    assert(!message.includes('GENERIC API MESSAGE'));
    assert.equal(await page.evaluate(()=>window.calls.filter(x=>x.url.endsWith('/start')).length),1);
    assert.deepEqual(errors,[]);checks.push({locale,width,partialTXT:true,partialDOCX:true,AIoffPreview:true,unsupportedITNotDispatched:true});
   await context.close();
  }
  console.log(JSON.stringify({state:'PASS',checks,actual_network_provider_store_calls:0}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
