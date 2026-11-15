// Mechanical offline attachment/localization only. No model or application service.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {chromium}=require('/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://explanations.invalid',preview=process.env.KLIKAI_EXPLANATION_PREVIEW_DIR;
const fixture=JSON.parse(cp.execFileSync('/data/envs/lingstack/pilot/bin/python',['-c',`import sys,json
sys.path.insert(0,'tests')
from test_document_evidence_explanations import mechanical_explanation
packet,raw,public=mechanical_explanation()
doc={'document_id':packet['document_id'],'name':'MECHANICAL EXPLANATION FIXTURE ONLY','state':'complete','processed':1,'total':1,'warnings':[],
'options':{'mode':'review','review_strategy':'evidence_review_v1'},'ai_suggestions':public,'sections':[{'index':0,'text':packet['sections'][0]['original_text'],'state':'complete',
'limitations':[],'document_result':{'mode':'review','status':'draft','proposed_text':packet['sections'][0]['evidence_draft'],'edits':[]}}]}
print(json.dumps(doc))`],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));
(async()=>{const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});const checks=[];
try{for(const locale of ['sl','it'])for(const width of [390,1280]){
 const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 const harness=`import {documentUploads} from '/document-uploads.mjs';const doc=${JSON.stringify(fixture)};window.opened=[];
 const api=async url=>{if(url==='/documents')return {documents:[doc]};if(url==='/documents/'+doc.document_id)return structuredClone(doc);throw Error(url);};
 const ui=documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:(...args)=>window.opened.push(args),errorText:e=>e.message});window.markStale=()=>{doc.ai_suggestions.stale=true;document.querySelector('[data-document-list]').dispatchEvent(new Event('change'));};ui.session({documents:{enabled:true,evidence_review:true,ai_suggestions:true}});`;
 await context.route('**/*',async route=>{const url=new URL(route.request().url());assert.equal(url.origin,origin);const file=url.pathname.slice(1);
  const body=file==='harness.mjs'?harness:file.endsWith('.mjs')||file==='pilot.css'?fs.readFileSync(path.join(assets,file),'utf8'):
  '<!doctype html><html lang="'+locale+'"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/pilot.css"></head><body><p>MECHANICAL DISPLAY FIXTURE ONLY — no AI answer.</p><main><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>';
  await route.fulfill({status:200,contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body});});
 await page.goto(origin);await page.locator('[data-document-list] option[value="'+fixture.document_id+'"]').waitFor({state:'attached'});
 await page.locator('#workspace > details > summary').click();await page.locator('[data-document-list]').selectOption(fixture.document_id);
 const comment=page.locator('[data-evidence-suggestions-group] .document-section [data-ai-explanation]');await comment.waitFor();
 assert.equal(await comment.locator('h4').textContent(),locale==='sl'?'Razlaga AI':'Spiegazione IA');
 assert.equal(await page.locator('[data-ai-explanation]').count(),1);
 assert.match(await comment.textContent(),/MECHANICAL EXPLANATION DISPLAY FIXTURE ONLY/);
 assert.equal(await page.locator('[data-ai-warning]').count(),0);
 assert.equal(await page.getByRole('button',{name:/Sprejmi|Accetta/}).count(),0);
 await comment.getByRole('button',{name:'MECHANICAL SOURCE'}).click();
 assert.deepEqual(await page.evaluate(()=>window.opened),[['fixture-family',{sourceId:'fixture-source'}]]);
 assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 if(preview&&locale==='sl')await page.screenshot({path:path.join(preview,'mechanical-explanation-'+width+'.png'),fullPage:true});
 await page.evaluate(()=>window.markStale());await page.waitForFunction(()=>!document.querySelector('[data-ai-explanation]'));
 assert.equal(await page.locator('[data-ai-explanation]').count(),0);
 checks.push({locale,width});await context.close();
}console.log(JSON.stringify({passed:checks.length,scope:'Mechanical explanation attachment; no language/model evaluation',checks}));}finally{await browser.close();}})().catch(error=>{console.error(error);process.exit(1);});
