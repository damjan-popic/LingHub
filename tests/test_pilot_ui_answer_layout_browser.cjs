/* Exact text, Unicode feedback offsets and source links over intercepted APIs. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://answer-layout.test';
const text='Za »zelen« vir navaja naslednji skupini:\n\n- mlad, neizkušen in nezrel [1]; izbira je odvisna od pomena besede v povedi.\n\n- Druga, daljša skupina: širša zamenjava 🧑‍🏫 v stavku o človeku; zamenjava mora ohraniti njegov pomen in ne sme izgubiti odločilnih pogojev.\n\nPodatki ostanejo samodejno pripravljeni; logDice 11.52291 ni pogostnost 19882.';
const second='1. januar 2026 ostane navadno besedilo; 30-dnevni ni alineja.',quote='širša zamenjava 🧑‍🏫 v stavku o človeku';
const tier={state:'complete',status:'validated',run_id:'saved-run',delivery_token:'fixture',answer_kind:'generated',generation_status:'generated',
 blocks:[{block_id:'B1',paragraph_id:'P1',text,evidence_ids:['e1','e2']},{block_id:'B2',paragraph_id:'P1',text:second,evidence_ids:['e2']}],
 evidence:[{evidence_id:'e1',local_number:1,source_slug:'cjvt-thesaurus',title:'Prva skupina'},{evidence_id:'e2',local_number:2,source_slug:'cjvt-thesaurus',title:'Druga skupina'}]};
const family={answer_family_id:'saved-family',question:'Sopomenke besede zelen?',locale:'sl',task:'reference',answer_format:'single',answer:tier,short:tier,long:null,matches_available:false};
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});
 const checks=[];
 try{for(const locale of ['sl','it'])for(const width of [390,1280]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),calls=[],errors=[];
  const messages=JSON.parse(fs.readFileSync(path.join(assets,'i18n',locale+'.json')));
  await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.copiedText=text}}));
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',async route=>{
   const request=route.request(),u=new URL(request.url());assert.equal(u.origin,origin,'No external requests');
   const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   if(u.pathname.startsWith('/assets/')){const file=u.pathname.slice(8);assert(!file.includes('..'));return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':file.endsWith('.jpg')?'image/jpeg':file.endsWith('.png')?'image/png':'application/json',body:fs.readFileSync(path.join(assets,file))});}
   if(u.pathname==='/'||u.pathname==='/it/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__',locale)});
   const api=u.pathname.slice('/api/pilot'.length);calls.push({api,method:request.method(),body:request.postDataJSON()});
   if(api==='/session')return json({authenticated:true,csrf_token:'fixture',user:{id:'fixture',username:'fixture',role:'tester'},capabilities:{project_beta:true}});
   if(api==='/capabilities')return json({project_beta:true});
   if(api==='/feedback'&&request.method()==='POST')return json({acknowledged:true,feedback_id:'fixture-feedback'});
   if(api==='/feedback'||api.endsWith('/feedback'))return json({feedback:[],events:[]});
   if(api==='/families/saved-family')return json(family);
   if(api.endsWith('/delivered'))return json({acknowledged:true});
   if(api.includes('/sources/'))return json({source_label:'Sopomenke',title:'Exact original',excerpt:'Saved source '+api.split('/sources/')[1]});
   throw Error('Unexpected fixture route '+api);
  });
  await page.goto(origin+(locale==='it'?'/it/':'/')+'?answer=saved-family');
  const block=page.locator('[data-block-id=B1]');await block.waitFor();
  assert.equal(await block.textContent(),text);
  assert.equal(await page.locator('[data-block-id=B2]').textContent(),second);
  assert.equal(await page.locator('[data-block-id=B2] [role=listitem]').count(),0);
  assert.deepEqual(await block.evaluate(node=>[node.dataset.blockId,node.dataset.runId,node.dataset.tier]),['B1','saved-run','short']);
  assert.equal(await block.getByRole('list').count(),1);assert.equal(await block.getByRole('listitem').count(),2);
  assert.equal(await block.locator('.citation').count(),1,'The inline citation keeps its block position');
  assert.equal(await block.locator('.citation').innerText(),'[1]');
  assert.equal(await page.locator('#short-answer > p > .citation').count(),2,'Separate citations stay outside the canonical feedback blocks');
  assert(await block.getByRole('listitem').first().evaluate(node=>parseFloat(getComputedStyle(node).paddingInlineStart)>0));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.locator('#copy-answer').click();const copied=await page.evaluate(()=>window.copiedText);
  assert(copied.includes('logDice 11.52291 ni pogostnost 19882.'));
  assert(copied.endsWith('\n\n'+messages.copy_sources+'\n[1] cjvt-thesaurus — Prva skupina\n[2] cjvt-thesaurus — Druga skupina'));
  await block.locator('.citation').click();await page.locator('#source-content blockquote').waitFor();
  assert.equal(await page.locator('#source-content blockquote').innerText(),'Saved source e1');await page.keyboard.press('Escape');
  await block.evaluate((node,quote)=>{
   const offset=node.textContent.indexOf(quote),walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT),nodes=[];
   while(walker.nextNode())nodes.push(walker.currentNode);
   const at=position=>{for(const child of nodes){if(position<=child.length)return [child,position];position-=child.length;}throw Error('Offset outside text');};
   const range=document.createRange();range.setStart(...at(offset));range.setEnd(...at(offset+quote.length));
   const selection=getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));
  },quote);
  await page.locator('#short-feedback .feedback-mark:not(:disabled)').click();
  assert.equal(await page.locator('#feedback-selection').innerText(),quote);
  await page.locator('#feedback-comment').fill('Exact offset fixture');await page.locator('#feedback-form button[type=submit]').click();
  await page.locator('#feedback-dialog').waitFor({state:'hidden'});
  const feedback=calls.find(call=>call.api==='/feedback'&&call.method==='POST').body;
  const start=Array.from(text.slice(0,text.indexOf(quote))).length;
  assert.deepEqual([feedback.run_id,feedback.anchor.block_id,feedback.anchor.start,feedback.anchor.end,feedback.anchor.quote],['saved-run','B1',start,start+Array.from(quote).length,quote]);
  assert(!calls.some(call=>call.api==='/families'&&call.method==='POST'));assert.deepEqual(errors,[]);
  checks.push({locale,width,exact_block_text:true,inline_and_separate_citations:true,unicode_feedback_offsets:true,copy_source_credits:true});await context.close();
 }}finally{await browser.close();}
 console.log(JSON.stringify({status:'PASS',checks,external_requests:0,model_calls:0}));
})().catch(error=>{console.error(error.stack);process.exit(1);});
