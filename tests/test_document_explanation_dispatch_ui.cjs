// Focused mechanical EX01/terminal-history access checks. No app/model/source calls.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {chromium}=require('/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://dispatch-check.invalid';
const fixtures=JSON.parse(cp.execFileSync('/data/envs/lingstack/pilot/bin/python',['-c',`import sys,json,copy
sys.path.insert(0,'tests')
from test_document_ai_outcomes import saved
from test_document_explanation_dispatch import no_evidence_public
from pilot_backend.document_ai_outcomes import presentation
case=saved('A');old=copy.deepcopy(case['public']);old['presentation']=presentation(old,case['results']);old['can_request_explanations']=True
empty=no_evidence_public();print(json.dumps({'old':old,'empty':empty}))`],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));
(async()=>{const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});const checks=[];
try{for(const mode of ['terminal','active','empty'])for(const locale of ['sl','it']){
 const public=structuredClone(mode==='empty'?fixtures.empty:fixtures.old);
 if(mode==='active'){public.state='running';public.finished=false;public.can_request_explanations=false;}
 const doc={document_id:'MECHANICAL-dispatch-'+mode,name:'MECHANICAL DISPATCH FIXTURE ONLY',state:'complete',processed:1,total:1,warnings:[],
  options:{mode:'review',review_strategy:'evidence_review_v1'},ai_suggestions:public,sections:[{index:0,text:'Potrdilo o vpisu',state:'complete',limitations:[],document_result:{mode:'review',status:'draft',proposed_text:'Potrdilo o vpisu',edits:[]}}]};
 const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const harness=`import {documentUploads} from '/document-uploads.mjs';let doc=${JSON.stringify(doc)};window.calls=[];
 const api=async(url,options)=>{if(url==='/documents')return {documents:[doc]};if(url==='/documents/'+doc.document_id)return structuredClone(doc);
 window.calls.push({url,options});if(url==='/documents/'+doc.document_id+'/ai/prepare'){doc.ai_suggestions=${JSON.stringify(fixtures.empty)};return structuredClone(doc);}throw Error(url);};
 const ui=documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:()=>{},errorText:e=>e.message});ui.session({documents:{enabled:true,evidence_review:true,ai_suggestions:true}});`;
 await context.route('**/*',async route=>{const url=new URL(route.request().url());assert.equal(url.origin,origin);const file=url.pathname.slice(1);
  const body=file==='harness.mjs'?harness:file.endsWith('.mjs')||file==='pilot.css'?fs.readFileSync(path.join(assets,file),'utf8'):
   '<!doctype html><html lang="'+locale+'"><head><meta charset="utf-8"><link rel="stylesheet" href="/pilot.css"></head><body><main id="workspace"></main><script type="module" src="/harness.mjs"></script></body></html>';
  await route.fulfill({status:200,contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body});});
 await page.goto(origin);await page.locator('[data-document-list] option[value="'+doc.document_id+'"]').waitFor({state:'attached'});
 await page.locator('#workspace > details > summary').click();await page.locator('[data-document-list]').selectOption(doc.document_id);
 const group=page.locator('[data-ai-suggestions-group]');await group.locator('[data-ai-outcome-counts]').waitFor();
 const action=group.getByRole('button',{name:locale==='sl'?'Pripravi razlage AI za ta pregled':'Prepara spiegazioni IA per questa revisione',exact:true});
 assert.equal(await action.count(),mode==='terminal'?1:0);
 if(mode==='terminal'){
  assert.equal(await group.locator('[data-ai-item-outcome="eligible_change"]').count(),1); // old result remains until explicit action
  await action.click();await group.locator('[data-ai-unit-outcome="no_explanatory_evidence"]').waitFor();
  assert.deepEqual(await page.evaluate(()=>window.calls),[{url:'/documents/'+doc.document_id+'/ai/prepare',options:{method:'POST',body:{purpose:'evidence_explanation'}}}]);
 }else assert.deepEqual(await page.evaluate(()=>window.calls),[]);
 if(mode!=='active'){
  const text=await group.textContent();assert.match(text,locale==='sl'?/AI ni bila uporabljena/:/IA non utilizzata/);
  assert.equal(await group.locator('[data-ai-unit-outcome="valid_empty"],[data-ai-raw-response]').count(),0);
  assert.equal(await group.getByRole('button',{name:/Pridobi razlage AI|Richiedi spiegazioni IA/}).count(),0);
 }
 assert.deepEqual(errors,[]);checks.push({mode,locale});await context.close();
}console.log(JSON.stringify({passed:checks.length,scope:'Mechanical EX01 terminal state and explicit history-purpose action only',checks}));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
