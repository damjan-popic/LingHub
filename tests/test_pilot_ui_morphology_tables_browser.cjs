/* Actual retained source records → current Python reader → real UI, with CSP.
 * Every API response is intercepted. Frequency ranks are explicit protocol
 * fixtures; this does not claim their values are native corpus measurements. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'playwright');
const repo=path.resolve(__dirname,'..'),assets=path.join(repo,'pilot_ui'),origin='https://morphology.test';
const python=String.raw`
import gzip,json,sys,tempfile
from pathlib import Path
sys.path.insert(0,'tests')
from test_generic_paradigm_presentation import ENTRIES,full_cards
from test_pilot_unified import app_for,UnifiedGateway,ORIGIN
from fastapi.testclient import TestClient
names=json.loads(gzip.decompress(Path('tests/fixtures/language_choice_sloleks.json.gz').read_bytes()))['lexemes']
out={}
for locale in ['sl','it']:
 rows=[]
 for entry in ENTRIES:
  focus={'degree':'comparative','gender':'feminine','case':'genitive'} if entry['lemma']=='dober'and entry['grammatical_type']=='adjective'else None
  rows.extend(full_cards([entry],locale,focus))
 out[locale]={'rows':rows,'luka':full_cards([entry for entry in names if entry['lemma']=='Luka'],locale)}
with tempfile.TemporaryDirectory() as root:
 with TestClient(app_for(Path(root),UnifiedGateway()),base_url=ORIGIN) as client:
  response=client.get('/');assert response.status_code==200
  out['csp']=response.headers['content-security-policy']
  assert client.get('/assets/morphology-tables.mjs').status_code==200
print(json.dumps(out,ensure_ascii=False))
`;
const captured=JSON.parse(execFileSync(process.env.KLIKAI_PYTHON||'python3',['-B','-c',python],{cwd:repo,encoding:'utf8',maxBuffer:8*1024*1024}));
const text='Nespremenjeni odgovor 🧑‍🏫 o človeku.[1]',quote='🧑‍🏫 o človeku';
const position={version:'sloleks-lexeme-frequency-position-v1',corpus:'Gigafida 2.0',inventory:'Sloleks 3.1',
 population_unit:'source_lexeme_occurrence',frequency:1000,eligible_total:10,rank_min:3,rank_max:9,
 percentile_low:10,percentile_high:80,percentile_mid:45,quartile_min:1,quartile_max:4,build_id:'explicit-ui-fixture'};
(async()=>{
 const helpers=await import(pathToFileURL(path.join(assets,'morphology-tables.mjs')));
 const {sourcePresentation}=await import(pathToFileURL(path.join(assets,'interface-details.mjs')));
 const registry=JSON.parse(fs.readFileSync(path.join(assets,'source-presentation.json')));
 let sourceSlots=0;
 for(const row of captured.sl.rows){
  const before=JSON.stringify(row),all=Object.fromEntries(helpers.morphologyControls(row).map(control=>[control.key,null]));
  const shown=helpers.morphologyTableView(row,all).groups.flatMap(group=>group.forms);
  assert.equal(shown.length,row.forms.length);assert.deepEqual(shown.map(form=>form.slot_locator).sort((a,b)=>a-b),row.forms.map(form=>form.slot_locator).sort((a,b)=>a-b));
  assert.equal(JSON.stringify(row),before);sourceSlots+=shown.length;
 }
 assert.equal(sourceSlots,617);
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']}),checks=[];
 try{for(const locale of ['sl','it'])for(const width of [390,1280]){
  const sourceRows=structuredClone(captured[locale].rows),luka=structuredClone(captured[locale].luka);
  const adjective=sourceRows.find(row=>row.grammatical_type==='adjective'&&row.forms.length===167);
  adjective.frequency_position=position;
  luka[0].frequency_position={...position,eligible_total:365000,rank_min:1,rank_max:1,percentile_low:99.9997260274,percentile_high:100,percentile_mid:99.9998630137,quartile_min:4,quartile_max:4};
  const fixture=structuredClone(adjective);fixture.title='Explicit variant-condition fixture';fixture.forms=fixture.forms.slice(0,1);fixture.paradigm_focus={};delete fixture.frequency_position;
  const variant=structuredClone(fixture.forms[0].variants[0]);variant.source_locator+=':fixture';variant.labels=[{tag:'label',attributes:{type:'fixture'},text:'<redko>',children:[]}];variant.source_pattern_ids=['fixture-other-pattern'];fixture.forms[0].variants.push(variant);
  fixture.forms[0].pattern_memberships.push({title:'Fixture pattern',source_pattern_id:'fixture-other-pattern',forms:[variant.form]});
  const legacy={title:'Legacy flattened source',text:'Existing count',label:'Sloleks',forms:[{title:'rodilnik',text:'A / B / C',pattern_memberships:[{title:'Pattern A',forms:['A']},{title:'Pattern B',forms:['B']}]}]};
  const rows=[...sourceRows,...luka,fixture,legacy];
  const tier={state:'complete',status:'validated',run_id:'saved-run',delivery_token:'fixture',answer_kind:'generated',generation_status:'generated',
   blocks:[{block_id:'C1',text,evidence_ids:['e1']}],evidence:[{evidence_id:'e1',local_number:1,source_slug:'sloleks',title:'Retained slot'}]};
  const family={answer_family_id:'saved-family',question:'Oblike izraza',locale,task:'reference',answer_format:'single',answer:tier,short:tier,long:null,matches_available:false,
   lexical_profile:{title:'Podatki o izrazu',note:'Retained source records',sections:[{kind:'morphology',title:'Source morphology',items:rows}]}};
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),calls=[],errors=[];
  const messages=JSON.parse(fs.readFileSync(path.join(assets,'i18n',locale+'.json')));
  await page.addInitScript(()=>{window.policyViolations=[];document.addEventListener('securitypolicyviolation',event=>window.policyViolations.push(event.violatedDirective));Object.defineProperty(navigator,'clipboard',{value:{writeText:async value=>window.copiedText=value}});});
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());assert.equal(url.origin,origin,'No external requests');
   const json=value=>route.fulfill({contentType:'application/json',headers:{'Content-Security-Policy':captured.csp},body:JSON.stringify(value)});
   if(url.pathname.startsWith('/assets/')){const file=url.pathname.slice(8);return route.fulfill({contentType:({'.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.jpg':'image/jpeg','.png':'image/png'})[path.extname(file)]||'application/json',headers:{'Content-Security-Policy':captured.csp},body:fs.readFileSync(path.join(assets,file))});}
   if(url.pathname==='/'||url.pathname==='/it/')return route.fulfill({contentType:'text/html',headers:{'Content-Security-Policy':captured.csp},body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__',locale)});
   const api=url.pathname.slice('/api/pilot'.length);calls.push({api,method:request.method(),body:request.postDataJSON()});
   if(api==='/session')return json({authenticated:true,csrf_token:'fixture',user:{id:'fixture',username:'fixture',role:'tester'},capabilities:{project_beta:true}});
   if(api==='/capabilities')return json({project_beta:true});
   if(api==='/feedback'&&request.method()==='POST')return json({acknowledged:true,feedback_id:'fixture-feedback'});
   if(api==='/feedback'||api.endsWith('/feedback'))return json({feedback:[],events:[]});
   if(api==='/families/saved-family')return json(family);
   if(api.endsWith('/delivered'))return json({acknowledged:true});
   if(api.includes('/sources/'))return json({title:'Exact original',source_label:'Sloleks',excerpt:'Retained original e1'});
   throw Error('Unexpected fixture route '+api);
  });
  await page.goto(origin+(locale==='it'?'/it/':'/')+'?answer=saved-family');await page.locator('#short-answer [data-block-id]').waitFor();
  const canonical=await page.locator('#short-answer').textContent(),block=page.locator('#short-answer [data-block-id]');assert.equal(await block.textContent(),text);
  assert(await page.locator('#lexical-profile > details').evaluate(node=>node.open));
  assert(await page.locator('.frequency-position').first().isVisible());
  assert.equal(await page.locator('.morphology-tables[open]').count(),0);
  const exactCard=title=>page.locator('#lexical-profile article').filter({has:page.getByRole('heading',{name:title,exact:true})});
  const adj=exactCard(adjective.title);await adj.locator('.morphology-tables > summary').click();
  assert.equal(await adj.locator('.morphology-tables > summary').innerText(),messages.morphology_inflection);
  assert.equal(await adj.locator('[data-morphology-control=gender]').inputValue(),JSON.stringify('feminine'));
  assert.equal(await adj.locator('[data-morphology-control=degree]').inputValue(),JSON.stringify('comparative'));
  assert.equal(await adj.locator('tr[data-requested-case=genitive]').count(),1);
  await adj.locator('[data-morphology-control=gender]').selectOption(JSON.stringify('masculine'));
  await adj.locator('[data-morphology-control=degree]').selectOption(JSON.stringify('positive'));
  const nominative=adjective.forms.find(form=>form.normalized_features.case==='nominative').feature_labels.case;
  const nominal=adj.locator('tbody tr').filter({has:page.locator('th[scope=row]').filter({hasText:nominative})}).first();
  assert((await nominal.locator('td').first().innerText()).includes('dober'));assert((await nominal.locator('td').first().innerText()).includes('dobri'));
  assert.equal(await nominal.locator('td').first().locator('.morphology-slot').count(),2);
  for(const selector of await adj.locator('.morphology-controls select').all())await selector.selectOption('');
  assert.equal(await adj.locator('.morphology-slot').count(),167);
  const personal=sourceRows.find(row=>row.grammatical_type==='pronoun'&&row.forms.length===23),pronoun=exactCard(personal.title);
  await pronoun.locator('.morphology-tables > summary').click();await pronoun.locator('[data-morphology-control=gender]').selectOption(JSON.stringify('feminine'));
  assert((await pronoun.innerText()).includes('mano'));assert((await pronoun.innerText()).includes('menoj'));
  assert((await pronoun.locator('caption').allTextContents()).some(caption=>caption.includes(messages.morphology_unspecified.replace('{feature}',messages.morphology_gender))));
  const numeral=exactCard(sourceRows.find(row=>row.grammatical_type==='numeral').title);await numeral.locator('.morphology-tables > summary').click();
  assert.equal(await numeral.locator('thead th').count(),2);
  const auxiliary=sourceRows.find(row=>row.grammatical_type==='verb'&&row.forms.length===62),verb=exactCard(auxiliary.title);
  await verb.locator('.morphology-tables > summary').click();await verb.locator('[data-morphology-control=vform]').selectOption(JSON.stringify('present'));
  assert((await verb.innerText()).includes('nisem'));assert((await verb.innerText()).includes('sem'));
  assert.equal(await verb.locator('[data-morphology-control=gender]').count(),0);
  await verb.locator('[data-morphology-control=vform]').selectOption(JSON.stringify('participle'));
  assert.equal(await verb.locator('tbody tr').count(),3);
  await verb.locator('[data-morphology-control=vform]').selectOption(JSON.stringify('infinitive'));
  assert.equal(await verb.locator('tbody tr').count(),1);
  const main=exactCard(sourceRows.find(row=>row.grammatical_type==='verb'&&row.grammatical_features.type==='main'&&row.title.startsWith('biti')).title);
  await main.locator('.morphology-tables > summary').click();await main.locator('[data-morphology-control=vform]').selectOption(JSON.stringify('present'));
  assert((await main.innerText()).includes('bijem'));assert(!(await main.locator('.morphology-slot').allTextContents()).some(value=>value.trim()==='sem'));
  for(const row of luka){const node=exactCard(row.title);await node.locator('.morphology-tables > summary').click();assert.equal(await node.locator('.morphology-slot').count(),18);assert.equal(await node.locator('[data-morphology-control=gender]').count(),0);}
  const old=exactCard(legacy.title);await old.locator('.morphology-tables > summary').click();assert.equal(await old.locator('.morphology-controls').count(),0);assert((await old.innerText()).includes('A / B / C'));assert((await old.innerText()).includes('Pattern A: A'));
  const condition=exactCard(fixture.title);await condition.locator('.morphology-tables > summary').click();assert((await condition.innerText()).includes('<redko>'));assert.equal(await condition.locator('redko').count(),0);
  assert.equal(await adj.locator('.frequency-position-band').getAttribute('x'),'10');assert.equal(await adj.locator('.frequency-position-band').getAttribute('width'),'70');
  assert.equal(await adj.locator('.frequency-position-point').getAttribute('x1'),'45');assert((await adj.locator('.frequency-position').innerText()).includes('3–9'));
  assert.equal(await page.locator('.frequency-position [style]').count(),0);
  const unique=exactCard(luka[0].title);assert(await unique.locator('.frequency-position-point').evaluate(node=>getComputedStyle(node).strokeWidth==='2px'&&getComputedStyle(node).vectorEffect==='non-scaling-stroke'));
  assert.equal(await page.locator('#short-answer').textContent(),canonical);
  await page.locator('#copy-answer').click();const copied=await page.evaluate(()=>window.copiedText);assert(copied.includes(text));assert(!copied.includes(messages.morphology_view_count.split('{')[0]));assert(copied.includes('[1] '+sourcePresentation(registry,tier.evidence[0],locale).label+' — Retained slot'));
  await block.locator('.citation').click();await page.locator('#source-content blockquote').waitFor();assert.equal(await page.locator('#source-content blockquote').innerText(),'Retained original e1');await page.keyboard.press('Escape');
  await block.evaluate((node,quote)=>{const start=node.textContent.indexOf(quote),walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT),nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);const at=n=>{for(const child of nodes){if(n<=child.length)return [child,n];n-=child.length;}throw Error('Offset');};const range=document.createRange();range.setStart(...at(start));range.setEnd(...at(start+quote.length));const selected=getSelection();selected.removeAllRanges();selected.addRange(range);document.dispatchEvent(new Event('selectionchange'));},quote);
  await page.locator('#short-feedback .feedback-mark:not(:disabled)').click();await page.locator('#feedback-comment').fill('Synthetic table-offset check');await page.locator('#feedback-form button[type=submit]').click();await page.locator('#feedback-dialog').waitFor({state:'hidden'});
  const feedback=calls.find(row=>row.api==='/feedback'&&row.method==='POST').body,start=Array.from(text.slice(0,text.indexOf(quote))).length;
  assert.deepEqual([feedback.anchor.start,feedback.anchor.end,feedback.anchor.quote],[start,start+Array.from(quote).length,quote]);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(await page.evaluate(()=>window.policyViolations),[]);assert.deepEqual(errors,[]);
  if(process.env.KLIKAI_MORPHOLOGY_UI_OUTPUT){await adj.locator('[data-morphology-control=gender]').selectOption(JSON.stringify('feminine'));await adj.locator('[data-morphology-control=degree]').selectOption(JSON.stringify('comparative'));fs.mkdirSync(process.env.KLIKAI_MORPHOLOGY_UI_OUTPUT,{recursive:true});await adj.screenshot({path:path.join(process.env.KLIKAI_MORPHOLOGY_UI_OUTPUT,'tables-'+locale+'-'+width+'.png')});await unique.locator('.frequency-position').screenshot({path:path.join(process.env.KLIKAI_MORPHOLOGY_UI_OUTPUT,'unique-frequency-'+locale+'-'+width+'.png')});}
  assert(!calls.some(row=>row.api==='/families'&&row.method==='POST'));
  checks.push({locale,width,status:'PASS',source_slots:617,typed_controls_and_unknown_groups:true,source_occurrences_separate:true,canonical_answer_offsets_copy_citations:true,actual_security_header:true,frequency_visible_without_opening_source_section:true,paradigms_initially_collapsed:true,frequency_tie_band_and_visible_unique_marker:true});await context.close();
 }}finally{await browser.close();}
 console.log(JSON.stringify({status:'PASS',scope:'Retained source-backed reader fixtures through intercepted UI under the real API CSP header; frequency positions are explicit protocol fixtures, not real corpus-rank measurements.',source_slots:617,checks,external_requests:0,model_calls:0}));
})().catch(error=>{console.error(error.stack);process.exit(1);});
