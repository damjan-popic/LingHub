// Offline rendering of saved actual outcomes and labelled mechanical fixtures.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {chromium}=require('/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://outcomes.invalid',preview=process.env.KLIKAI_AUTHORING_PREVIEW_DIR;
const python=String.raw`
import sys,json,copy
sys.path.insert(0,'tests')
from test_document_ai_outcomes import mechanical,legacy_invalid_fixture
from pilot_backend.document_ai_outcomes import presentation
f=json.load(open('tests/fixtures/document_ai_saved_outcomes_20261115.json'));docs={}
for name,case in f['cases'].items():
 ai=copy.deepcopy(case['public']);ai['presentation']=presentation(ai,case['results'])
 docs[name]={'document_id':'offline-'+name,'name':'Saved outcome display: '+name,'state':'complete','processed':4,'total':4,'warnings':[],
  'options':{'mode':'review','review_strategy':'evidence_review_v1'},'sections':[{'index':s['index'],'text':s['text'],'state':'complete','limitations':[],
   'document_result':{'mode':'review','status':'draft','proposed_text':s['text'],'edits':[]}}for s in case['context_sections']], 'ai_suggestions':ai}
for kind in ('empty','comment'):
 ai,results=mechanical(kind);ai['presentation']=presentation(ai,results)
 doc=copy.deepcopy(docs['remaining']);doc['name']='MECHANICAL FIXTURE ONLY: '+kind;doc['ai_suggestions']=ai;docs['fixture_'+kind]=doc
ai,results=legacy_invalid_fixture();ai['presentation']=presentation(ai,results)
doc=copy.deepcopy(docs['remaining']);doc['name']='MECHANICAL FIXTURE ONLY: legacy invalid response';doc['ai_suggestions']=ai;docs['fixture_legacy_invalid']=doc
print(json.dumps(docs,ensure_ascii=False))
`;
const docs=preview?JSON.parse(fs.readFileSync(path.join(preview,'browser-fixtures.json'),'utf8')):JSON.parse(cp.execFileSync('/data/envs/lingstack/pilot/bin/python',['-c',python],{cwd:path.resolve(__dirname,'..'),encoding:'utf8'}));
const names=process.argv.includes('--legacy-only')?['fixture_legacy_invalid']:['remaining','A','fixture_comment','fixture_empty',...(!preview?['fixture_legacy_invalid']:[])];
(async()=>{const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});const checks=[];
try{for(const name of names)for(const locale of ['sl','it'])for(const width of (name==='remaining'?[390,1280]:[1280])){
 const doc=docs[name],context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const harness=`import {documentUploads} from '/document-uploads.mjs';const doc=${JSON.stringify(doc)};const api=async url=>{if(url==='/documents')return {documents:[{document_id:doc.document_id,name:doc.name,state:doc.state}]};if(url==='/documents/'+doc.document_id)return structuredClone(doc);throw Error(url);};const ui=documentUploads({root:document.querySelector('#workspace'),api,locale:()=>document.documentElement.lang,openFamily:()=>{},errorText:e=>e.message});ui.session({documents:{enabled:true,evidence_review:true,ai_suggestions:true}});`;
 await context.route('**/*',async route=>{const url=new URL(route.request().url());assert.equal(url.origin,origin);
  const file=url.pathname.slice(1),body=file==='harness.mjs'?harness:file.endsWith('.mjs')||file==='pilot.css'?fs.readFileSync(path.join(assets,file),'utf8'):
   '<!doctype html><html lang="'+locale+'"><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/pilot.css"></head><body><p>OFFLINE: saved outcome presentation; no new AI answers.</p><main><section id="workspace"></section></main><script type="module" src="/harness.mjs"></script></body></html>';
  await route.fulfill({status:200,contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',body});});
 await page.goto(origin);await page.locator('[data-document-list] option[value="'+doc.document_id+'"]').waitFor({state:'attached'});
 await page.locator('#workspace > details > summary').click();await page.locator('[data-document-list]').selectOption(doc.document_id);
 const group=page.locator('[data-ai-suggestions-group]');await group.locator('[data-ai-outcome-counts]').waitFor();
 assert.equal(await group.locator('[data-ai-outcome-counts]').textContent(),doc.ai_suggestions.presentation.labels[locale].counts);
 if(name==='remaining'){
  assert.equal(await group.locator('[data-ai-item-outcome="rejected_item"]').count(),3);
  assert.equal(await group.locator('[data-ai-rejection-reason="no_op"]').count(),1);
  assert.equal(await group.locator('[data-ai-rejection-reason="span_not_bound"]').count(),2);
  assert.equal(await group.getByRole('button',{name:/Sprejmi|Accetta/}).count(),0);
  const summaries=await group.locator('[data-ai-item-outcome="rejected_item"] > summary').allTextContents();
  assert.ok(summaries.every(s=>!s.includes('Neodločen')&&!s.includes('da decidere')));
  assert.equal(await group.locator('[data-ai-unit-outcome="invalid_response"]').count(),1);
  const raw=group.locator('[data-ai-raw-response="U88a25f89dedd86533b586dfb"]');await raw.locator('summary').click();
  assert.equal(await raw.locator('pre').textContent(),doc.ai_suggestions.presentation.units.U88a25f89dedd86533b586dfb.raw_content);
  assert.match(await group.textContent(),/ValueError/);
  await group.locator('[data-ai-item-outcome="rejected_item"]').first().locator('summary').click();
  assert.match(await group.textContent(),locale==='sl'?/enako obravnavanemu/:/identico/);
  if(preview&&locale==='sl')await page.screenshot({path:path.join(preview,'updated-outcomes-sl-'+width+'.png'),fullPage:true});
 }else if(name==='A'){
  assert.equal(await group.locator('[data-ai-item-outcome="eligible_change"]').count(),1);
  await group.locator('[data-ai-item-outcome="eligible_change"] > summary').click();
  assert.equal(await group.getByRole('button',{name:/Sprejmi|Accetta/}).count(),1);
  assert.match(await group.textContent(),/The phrase/); // actual English output is not silently translated
 }else if(name==='fixture_comment'){
  assert.equal(await group.locator('[data-ai-item-outcome="valid_comment"]').count(),1);
  assert.equal(await group.locator('[data-ai-rejection-reason]').count(),0);
  assert.equal(await group.getByRole('button',{name:/Sprejmi|Accetta/}).count(),0);
 }else if(name==='fixture_legacy_invalid'){
  const legacy=doc.ai_suggestions.presentation.legacy_response;
  assert.equal(await group.locator('[data-ai-legacy-outcome="invalid_response"]').textContent(),legacy.labels[locale].title);
  assert.match(await group.textContent(),/ValueError/);
  const raw=group.locator('[data-ai-raw-response="legacy"]');await raw.locator('summary').click();
  assert.equal(await raw.locator('pre').textContent(),legacy.raw_content);
  assert.equal(await group.locator('[data-ai-item-outcome]').count(),0);
  assert.equal(await group.getByRole('button',{name:/Sprejmi|Accetta/}).count(),0);
 }else{
  assert.equal(await group.locator('[data-ai-unit-outcome="valid_empty"]').count(),1);
  assert.equal(await group.locator('[data-ai-item-outcome]').count(),0);
 }
 assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 checks.push({name,locale,width});await context.close();
}console.log(JSON.stringify({passed:checks.length,scope:'Offline saved-outcome/fixture presentation only',checks}));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1);});
