// Offline browser control: every request is fulfilled from local test assets.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const origin='https://scopes.invalid',assets=path.resolve(__dirname,'../pilot_ui');
const harness=String.raw`
import {documentUploads} from '/document-uploads.mjs';
const text='Ana je doma.',raw='Ana je je doma.';
const section={index:0,text:raw,state:'complete',location:'p1',limitations:[],document_result:{mode:'review',status:'draft',proposed_text:raw,edits:[]}};
const span={start:0,end:raw.length,text:raw};
const doc={document_id:'doc-1',name:'scoped-control.docx',state:'complete',has_word_original:true,processed:1,total:1,warnings:[],
 options:{mode:'review',review_strategy:'evidence_review_v1'},sections:[section],
 review_manifest:{counts:{qualified_advice:0,recorded_contrast:0,recorded_form_context:0,executable_application:0}},
 ai_suggestions:{state:'partial',stale:false,review_complete:false,partial:true,can_start:false,can_cancel:false,coverage:{provided_sections:[0]},
 units:[{section_index:0,original_span:span,state:'completed'},{section_index:0,original_span:{...span,text:'<script>window.pwned=true</script>'},state:'unavailable_input_limit'}],
 items:[],decisions:{},source_references:{},selected_revision:{revision_id:'Uoffline-control',partial_advice:true,
 sections:[{index:0,document_result:{proposed_text:text}}]}}};
window.downloads=[];
const api=async(url,options={})=>{if(url==='/documents')return {documents:[{document_id:'doc-1',name:doc.name,state:doc.state}]};
 if(url==='/documents/doc-1')return structuredClone(doc);if(url.includes('/selected/')){window.downloads.push(url);return new Blob(['Synthetic Word bytes, not a document artifact']);}throw Error(url);};
const ui=documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:()=>{},errorText:e=>e.message});
ui.session({documents:{enabled:true,evidence_review:true,ai_suggestions:true}});
`;
(async()=>{const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});const checks=[];
try{for(const locale of ['sl','it'])for(const width of [390,1280]){
 const context=await browser.newContext({viewport:{width,height:950}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{const url=new URL(route.request().url());assert.equal(url.origin,origin);
  const name=url.pathname.slice(1);const body=name==='harness.mjs'?harness:name.endsWith('.mjs')||name==='pilot.css'?fs.readFileSync(path.join(assets,name),'utf8'):
   '<!doctype html><html lang="'+locale+'"><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/pilot.css"></head><body><main><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>';
  await route.fulfill({status:200,contentType:name.endsWith('.mjs')?'text/javascript':name.endsWith('.css')?'text/css':'text/html',body});});
 await page.goto(origin);await page.locator('[data-document-list] option[value="doc-1"]').waitFor({state:'attached'});
 await page.locator('#workspace > details > summary').click();await page.locator('[data-document-list]').selectOption('doc-1');
 const group=page.locator('[data-ai-suggestions-group]');await group.locator('[data-ai-partial="true"]').waitFor();
 assert.match(await group.textContent(),locale==='it'?/senza risultato/:/brez rezultata/);
 assert.match(await group.textContent(),locale==='it'?/Oltre il limite IA/:/Presega omejitev AI koraka/);
 assert.equal(await page.evaluate(()=>window.pwned),undefined);
 const download=group.getByRole('button',{name:locale==='it'?'Scarica Word parziale con modifiche IA selezionate':'Prenesi delni Word z izbranimi AI spremembami',exact:true});
 await download.click();assert.deepEqual(await page.evaluate(()=>window.downloads),['/documents/doc-1/selected/docx?revision=Uoffline-control&partial=true']);
 assert.equal(await group.getByRole('button',{name:/TXT/}).count(),0);assert.deepEqual(errors,[]);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 checks.push({locale,width,partial_visible:true,unavailable_scope_visible:true,labelled_download_only:true,inert_text:true});await context.close();
}console.log(JSON.stringify({passed:checks.length,kind:'OFFLINE synthetic UI controls only; no app/services/model',checks}));}finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1);});
