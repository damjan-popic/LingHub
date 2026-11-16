/* Existing UI, fully intercepted APIs, retained selections; no running app. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'playwright');
const folder=process.env.KLIKAI_LAYERED_RECEIPTS;
assert(folder,'Run the focused offline consumer checks with KLIKAI_LAYERED_RECEIPTS first');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://layered-presentation.test';
const read=name=>JSON.parse(fs.readFileSync(path.join(folder,name+'.json')));
const cases=[];
for(const id of ['I1_implicit_event_contrast','I2_implicit_adjective_example']){
 const actual=read(id+'-actual-view');cases.push({name:id+'-actual',tier:actual.public_tier,widths:[390,1280]});
 for(const mode of ['main','head','all_reversed','same_paragraph'])cases.push({name:id+'-'+mode,tier:read(id+'-'+mode).value,widths:[390]});
 cases.push({name:id+'-italian',tier:read(id+'-italian').value,widths:[390]});
 cases.push({name:id+'-legacy',tier:{...actual.public_tier,answer_presentation:undefined},widths:[390]});
}
cases.push({name:'multiple-relations',tier:read('multiple-relations-unknown-condition').result,widths:[390]});
cases.push({name:'distinct-scopes',tier:read('distinct-scopes').value,widths:[390]});
cases.push({name:'additional-qualified-note',tier:{...read('I2_implicit_adjective_example-actual-view').public_tier,
 limitations:[{code:'source_scope',scope:'claim',claim_ids:['C1']}]},widths:[390]});
for(const name of fs.readdirSync(folder).filter(n=>/^explicit-\d+\.json$/.test(n)))cases.push({name:name.slice(0,-5),tier:read(name.slice(0,-5)).answer,widths:[390]});

(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});const checks=[];
 try{for(const item of cases)for(const width of item.widths){
  const locale=item.tier.answer_presentation?.locale||(item.name.includes('italian')?'it':'sl');
  const tier={...item.tier,state:'complete',status:'validated',run_id:'retained-run',delivery_token:'fixture'};
  const family={answer_family_id:'retained-family',question:'Retained offline presentation',locale,task:'reference',answer_format:'single',answer:tier,short:tier,long:null,matches_available:false};
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),calls=[],errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.copiedText=text}}));
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());assert.equal(url.origin,origin,'External requests prohibited');
   const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   if(url.pathname.startsWith('/assets/')){
    const file=url.pathname.slice(8);assert(!file.includes('..'));
    return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':file.endsWith('.jpg')?'image/jpeg':file.endsWith('.png')?'image/png':'application/json',body:fs.readFileSync(path.join(assets,file))});
   }
   if(url.pathname==='/'||url.pathname==='/it/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__',locale)});
   const api=url.pathname.slice('/api/pilot'.length);calls.push({api,method:request.method(),body:request.postDataJSON()});
   if(api==='/session')return json({authenticated:true,csrf_token:'fixture',user:{id:'fixture',username:'fixture',role:'tester'},capabilities:{project_beta:true}});
   if(api==='/capabilities')return json({project_beta:true});
   if(api==='/feedback'&&request.method()==='POST')return json({acknowledged:true,feedback_id:'fixture-feedback'});
   if(api==='/feedback'||api.endsWith('/feedback'))return json({feedback:[],events:[]});
   if(api==='/families/retained-family')return json(family);
   if(api.endsWith('/delivered'))return json({acknowledged:true});
   if(api.includes('/sources/'))return json({source_label:'Retained source',title:'Retained original',excerpt:'Retained source '+decodeURIComponent(api.split('/sources/')[1])});
   throw Error('Unexpected mock route '+api);
  });
  await page.goto(origin+(locale==='it'?'/it/':'/')+'?answer=retained-family');
  await page.locator('#short-answer [data-block-id]').first().waitFor();
  await page.locator('#short-feedback .feedback-mark:not(:disabled)').waitFor();
  const answer=page.locator('#short-answer'),view=tier.answer_presentation,groups=view?.sections.filter(s=>s.context_key)||[];
  const expectedPanels=groups.filter(s=>s.additional.length).length;
  assert.equal(await answer.locator('.additional-lexical-context').count(),expectedPanels);
  if(view){
   assert.equal(await answer.getAttribute('data-presentation-id'),view.presentation_sha256);
   for(const group of groups){
    const node=answer.locator('[data-context-key="'+group.context_key+'"]');
    for(const frame of [group.intro,group.footer]){
     const part=node.locator('[data-block-id="'+frame.block_id+'"][data-text-start="'+frame.start+'"]');
     assert.equal(await part.textContent(),frame.text);assert(await part.isVisible());
     assert.equal(await part.evaluate(n=>n.closest('.additional-lexical-context')===null),true);
    }
    for(const part of group.primary){
     const span=node.locator('[data-block-id="'+part.block_id+'"][data-text-start="'+part.start+'"]');
     assert.equal(await span.textContent(),part.text);assert(await span.isVisible());
     assert.equal(await span.evaluate(n=>n.closest('.additional-lexical-context')===null),true);
    }
    for(const part of group.additional){
     const span=node.locator('.additional-lexical-context [data-block-id="'+part.block_id+'"]');
     assert.equal(await span.textContent(),part.text);assert.equal(await span.isVisible(),false);
    }
   }
   await page.locator('#copy-answer').click();const copied=await page.evaluate(()=>window.copiedText);
   for(const group of groups)for(const part of [...group.primary,...group.additional])assert(copied.includes(part.text),'Copy retains folded source context');
   if(expectedPanels)assert(copied.includes(view.additional_label));
   for(const note of await answer.locator('[data-note-kind]').all())assert(copied.includes(await note.innerText()),'Copy retains visible qualification notes');
   for(const block of tier.blocks){
    for(const eid of block.evidence_ids||[]){const e=tier.evidence.find(e=>e.evidence_id===eid);assert(e&&copied.includes('['+e.local_number+']'));}
   }
   const collapsedText=await answer.innerText();
   const before=calls.length;
   for(const summary of await answer.locator('.additional-lexical-context > summary').all()){
    await summary.focus();await page.keyboard.press('Enter');assert.equal(await summary.evaluate(n=>n.parentElement.open),true);
    const focus=await summary.evaluate(n=>getComputedStyle(n).outlineStyle);assert.notEqual(focus,'none');
   }
   assert.equal(calls.length,before,'Expansion uses retained content without API calls');
   for(const group of groups)for(const part of group.additional){
    const span=answer.locator('[data-context-key="'+group.context_key+'"] .additional-lexical-context [data-block-id="'+part.block_id+'"]');assert(await span.isVisible());
    const block=tier.blocks.find(b=>b.block_id===part.block_id),expected=block.evidence_ids||[];
    assert.equal(await span.locator('..').locator('.citation').count(),expected.length);
    for(const button of await span.locator('..').locator('.citation').all()){
     await button.click();await page.locator('#source-content blockquote').waitFor();assert((await page.locator('#source-content blockquote').textContent()).startsWith('Retained source '));await page.keyboard.press('Escape');
    }
   }
   // The visible body moved past its shared intro; feedback must still address
   // the exact old canonical Unicode positions, including the hidden sections.
   const part=groups[0].primary[0],span=answer.locator('[data-context-key="'+groups[0].context_key+'"] [data-block-id="'+part.block_id+'"][data-text-start="'+part.start+'"]');
   const quote=Array.from(part.text).slice(0,24).join('');
   await span.evaluate((node,quote)=>{const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);walker.nextNode();const range=document.createRange();range.setStart(walker.currentNode,0);range.setEnd(walker.currentNode,quote.length);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);document.dispatchEvent(new Event('selectionchange'));},quote);
   await page.locator('#short-feedback .feedback-mark:not(:disabled)').click();assert.equal(await page.locator('#feedback-selection').innerText(),quote);
   await page.locator('#feedback-comment').fill('Offline canonical offset control');await page.locator('#feedback-form button[type=submit]').click();await page.locator('#feedback-dialog').waitFor({state:'hidden'});
   const anchor=calls.find(c=>c.api==='/feedback'&&c.method==='POST').body.anchor;
   assert.deepEqual([anchor.block_id,anchor.start,anchor.end,anchor.quote],[part.block_id,part.start,part.start+Array.from(quote).length,quote]);
   await answer.locator('.complete-answer > summary').click();assert.equal(await answer.locator('.complete-answer pre').textContent(),tier.answer_md);
   await answer.locator('.complete-answer > summary').click();
   const expandedText=await answer.innerText();
   if(item.name.endsWith('-actual')){
    for(const summary of await answer.locator('.additional-lexical-context > summary').all())await summary.click();
    await page.screenshot({path:path.join(folder,item.name+'-'+width+'-collapsed.png'),fullPage:true});
    for(const summary of await answer.locator('.additional-lexical-context > summary').all())await summary.click();
    await page.screenshot({path:path.join(folder,item.name+'-'+width+'-expanded.png'),fullPage:true});
    fs.writeFileSync(path.join(folder,item.name+'-'+width+'-dom.json'),JSON.stringify({collapsedText,expandedText,html:await answer.innerHTML(),copy:copied},null,2)+'\n');
   }
  }else{
   assert.equal(await answer.locator('.additional-lexical-context,.complete-answer').count(),0);
   for(const block of tier.blocks)assert.equal(await answer.locator('[data-block-id="'+block.block_id+'"]').textContent(),block.text);
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Mobile layout does not overflow');
  assert.deepEqual(errors,[]);assert(!calls.some(c=>c.api==='/families'&&c.method==='POST'));
  checks.push({case:item.name,width,locale,panels:expectedPanels,complete_selected_content:true,keyboard_accessible:true,retained_citations:true,canonical_feedback_offsets:!!view,actual_operations:0});
  await context.close();
 }}finally{await browser.close();}
 console.log(JSON.stringify({status:'PASS',checks,external_requests:0,actual_operations:0}));
})().catch(error=>{console.error(error.stack);process.exit(1);});
