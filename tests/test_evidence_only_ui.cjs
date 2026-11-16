/* Evidence-only feature controls in the existing document UI, offline. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const origin='https://evidence-only.test',assets=path.resolve(__dirname,'../pilot_ui');
const harness=String.raw`
import {documentUploads} from '/document-uploads.mjs';
const doc={document_id:'existing-fixture',name:'review.txt',state:'draft',options:{},warnings:[],processed:0,total:1,
 sections:[{index:0,text:'Nadaljni opis je bil pripravljen s pomočjo tolmača.',state:'pending',limitations:[]}]};
window.calls=[];
const api=async(url,options={})=>{window.calls.push({url,options});if(url==='/documents')return {documents:[{document_id:doc.document_id,name:doc.name,state:doc.state}]};
 if(url==='/documents/'+doc.document_id)return structuredClone(doc);
 if(url.endsWith('/start'))return {...structuredClone(doc),state:'complete',options:options.body};throw Error(url);};
window.ui=documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:()=>{},errorText:e=>e.message});
ui.session({documents:{enabled:true,evidence_review:true,evidence_review_only:true,private_translation:false,editorial_summary:false,ai_suggestions:false}});
`;
(async()=>{const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});const checks=[];
try{for(const locale of ['sl','it'])for(const width of [390,1280]){
 const context=await browser.newContext({viewport:{width,height:950}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());assert.equal(url.origin,origin);const name=url.pathname.slice(1);
  const body=name==='harness.mjs'?harness:name.endsWith('.mjs')||name==='pilot.css'?fs.readFileSync(path.join(assets,name),'utf8'):`<!doctype html><html lang="${locale}"><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/pilot.css"></head><body><main><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>`;
  await route.fulfill({status:200,contentType:name.endsWith('.mjs')?'text/javascript':name.endsWith('.css')?'text/css':'text/html',body});
 });
 await page.goto(origin);await page.locator('[data-document-list] option[value="existing-fixture"]').waitFor({state:'attached'});
 const summary=page.locator('#workspace > details > summary');assert.equal(await summary.textContent(),locale==='it'?'Revisione del testo con fonti — prova':'Pregled besedila z viri — preizkus');
 await summary.click();await page.locator('[data-document-list]').selectOption('existing-fixture');
 const form=page.locator('.document-options');await form.waitFor();
 assert.deepEqual(await form.locator('select').nth(0).locator('option').evaluateAll(ns=>ns.map(n=>n.value)),['review']);
 assert.deepEqual(await form.locator('select').nth(1).locator('option').evaluateAll(ns=>ns.map(n=>n.value)),['sl']);
 assert.equal(await form.locator('select').nth(2).isVisible(),false);
 assert.equal(await form.locator('select').count(),3);
 await form.locator('button[type=submit]').click();
 const submitted=await page.evaluate(()=>window.calls.find(c=>c.url.endsWith('/start')).options.body);
 assert.equal(submitted.mode,'review');assert.equal(submitted.source_language,'sl');assert.equal(submitted.review_strategy,'evidence_review_v1');assert.equal(submitted.translation_provider,undefined);
 assert.deepEqual(errors,[]);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 await page.evaluate(()=>window.ui.session({}));assert.equal(await page.locator('#workspace').isVisible(),false);
 checks.push({locale,width,evidence_only:true,ordinary_feature_off_hidden:true,legacy_translation_absent:true,actual_operations:0});await context.close();
}console.log(JSON.stringify({status:'PASS',checks,external_requests:0,actual_operations:0}));}finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exit(1);});
