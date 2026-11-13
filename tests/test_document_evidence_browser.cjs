const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const origin='https://evidence.test',assets=path.resolve(__dirname,'../pilot_ui');
const harness=String.raw`
import {documentUploads} from '/document-uploads.mjs';
const id='Faaaaaaaaaaaaaaaaaaaaaaaa';
const finding={finding_id:id,type:'qualified_advice',included_in_draft:false,notice:'Uporabnost ostaja nerazrešena.',
 match:{observed:{start:0,end:8,text:'Izvirnik'}},source:{evidence_id:'E1',focus:{text:'Celotno priporočilo. Izjema velja za pomoč osebe. <script>window.pwned=true</script>'},
 recorded_advice:{recommendation_force:'recommendation_with_exception',scope_note:'Ohrani izjemo in alternative.',mapping_status:'proposed_needs_review'}}};
const doc={document_id:'doc-1',name:'review.docx',state:'complete',options:{mode:'review',review_strategy:'evidence_review_v1'},warnings:[],processed:1,total:1,
 review_manifest:{counts:{qualified_advice:1,recorded_contrast:0,recorded_form_context:0,executable_application:0}},
 editorial_summary:{status:'available',historical:false,output:{points:[{text:'Nasvet ostaja odprt.',finding_ids:[id]}]}},
 sections:[{index:0,text:'Izvirnik',state:'complete',family_id:'family-1',output:'Pregled iz virov.',limitations:[],
 document_result:{mode:'review',status:'draft',proposed_text:'Izvirnik',edits:[],review:{findings:[finding],overview:{counts:{qualified_advice:1,recorded_contrast:0,recorded_form_context:0,executable_application:0}},checks:[{check_id:'captured_source_forms',status:'completed',scope:'Bounded.'}]}}}]};
window.opened=null;window.downloads=[];
const api=async(url,options={})=>{if(url==='/documents')return {documents:[{document_id:'doc-1',name:doc.name,state:doc.state}]};
 if(url==='/documents/doc-1')return structuredClone(doc);if(url.includes('/draft/')){window.downloads.push(url);return new Blob(['unchanged draft']);}throw Error(url);};
const ui=documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:(id,options)=>{window.opened=id;window.sourceOptions=options;},errorText:e=>e.message});
ui.session({documents:{enabled:true,evidence_review:true,editorial_summary:true}});
`;
(async()=>{const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});const checks=[];
try{for(const locale of ['sl','it'])for(const width of [390,1280]){
const context=await browser.newContext({viewport:{width,height:950}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
await context.route(origin+'/**',async route=>{const name=new URL(route.request().url()).pathname.slice(1);
const body=name==='harness.mjs'?harness:name.endsWith('.mjs')||name==='pilot.css'?fs.readFileSync(path.join(assets,name),'utf8'):`<!doctype html><html lang="${locale}"><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/pilot.css"></head><body><main><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>`;
await route.fulfill({status:200,contentType:name.endsWith('.mjs')?'text/javascript':name.endsWith('.css')?'text/css':'text/html',body});});
await page.goto(origin);await page.locator('[data-document-list] option[value="doc-1"]').waitFor({state:'attached'});
await page.locator('#workspace > details > summary').click();await page.locator('[data-document-list]').selectOption('doc-1');
await page.locator('[data-evidence-review-strategy]').waitFor();
assert.equal(await page.locator('[data-document-review-counts]').textContent(),(locale==='it'?'Indicazioni / confronti / contesto / modifiche: ':'Priporočila / primerjave / kontekst / spremembe: ')+'1 / 0 / 0 / 0');
await page.locator('a[href="#Faaaaaaaaaaaaaaaaaaaaaaaa"]').click();
assert.equal(await page.locator('[data-finding-id]').evaluate(n=>n.open),true);
assert.match(await page.locator('[data-finding-id]').textContent(),/Izjema velja za pomoč osebe/);assert.equal(await page.evaluate(()=>window.pwned),undefined);
await page.locator('[data-finding-id] button').click();assert.equal(await page.evaluate(()=>window.opened),'family-1');assert.equal(await page.evaluate(()=>window.sourceOptions.sourceId),'E1');
assert.equal(await page.locator('[data-clean-draft]').count(),1);assert.deepEqual(errors,[]);
const overflow=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(n=>n.getBoundingClientRect().right>innerWidth+1).map(n=>({tag:n.tagName,cls:n.className,width:n.getBoundingClientRect().width,text:n.textContent.slice(0,80)})).slice(-8));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,JSON.stringify(overflow));
if(process.env.KLIKAI_EVIDENCE_SCREENSHOTS){await page.screenshot({path:path.join(process.env.KLIKAI_EVIDENCE_SCREENSHOTS,`review-${locale}-${width}.png`),fullPage:true});}
checks.push({locale,width,located_finding:true,exception_visible:true,source_action:true,summary_links:true,original_preserved_download:true,script_text_inert:true});await context.close();
}console.log(JSON.stringify({passed:checks.length,kind:'UI contract fixture, not language-quality evidence',checks}));}finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1);});
