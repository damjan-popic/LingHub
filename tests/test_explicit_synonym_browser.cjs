/* New explicit render only; UI APIs are fully intercepted, no app server. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'playwright');
const folder=process.env.KLIKAI_EXPLICIT_RECEIPTS;assert(folder,'Run the focused explicit consumer tests first');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://explicit-synonym.test';
const read=name=>JSON.parse(fs.readFileSync(path.join(folder,name+'.json')));
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']});const checks=[];
 try{for(const name of ['actual-ALL-sl','actual-ALL-it','retained-proga-control'])for(const width of name.startsWith('actual')?[390,1280]:[390]){
  const saved=read(name),locale=name.endsWith('-it')?'it':'sl',tier={...saved.answer,state:'complete',run_id:'retained-run',delivery_token:'fixture'};
  const family={answer_family_id:'retained-family',question:name.startsWith('actual')?'V železniškem pomenu iščem sopomenko za hiter v zvezi hiter vlak. Kaj ponujajo Sopomenke?':'Sopomenke besede »progi«?',locale,task:'reference',answer_format:'single',answer:tier,short:tier,long:null,matches_available:false};
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),calls=[],errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.copiedText=text}}));
  await context.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());assert.equal(url.origin,origin,'External traffic prohibited');
   const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   if(url.pathname.startsWith('/assets/')){const file=url.pathname.slice(8);assert(!file.includes('..'));return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':file.endsWith('.jpg')?'image/jpeg':file.endsWith('.png')?'image/png':'application/json',body:fs.readFileSync(path.join(assets,file))});}
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
  await page.locator('#short-answer [data-block-id]').first().waitFor();await page.locator('#short-feedback .feedback-mark:not(:disabled)').waitFor();
  const answer=page.locator('#short-answer');assert.equal(await answer.locator('.additional-lexical-context,.complete-answer').count(),0);
  for(const block of tier.blocks){
   const span=answer.locator('[data-block-id="'+block.block_id+'"]');assert.equal(await span.textContent(),block.text);assert(await span.isVisible());
   const refs=span.locator('..').locator('.citation');assert.equal(await refs.count(),block.evidence_ids.length);
   for(const [i,button]of(await refs.all()).entries()){
    await button.click();await page.locator('#source-content blockquote').waitFor();
    assert.equal(await page.locator('#source-content blockquote').textContent(),'Retained source '+block.evidence_ids[i]);await page.keyboard.press('Escape');
   }
  }
  await page.locator('#copy-answer').click();const copy=await page.evaluate(()=>window.copiedText);
  if(name.startsWith('actual')){
   assert.equal(copy.split(locale==='it'?'Per «hiter»':'Za »hiter«').length-1,1);
   for(const word of ['uren','učinkovit','nagel','ekspresen','pravo','železnica'])assert(copy.includes(word));
  }else assert(copy.includes('črta, pas'));
  for(const block of tier.blocks)for(const eid of block.evidence_ids){const e=tier.evidence.find(e=>e.evidence_id===eid);assert(copy.includes('['+e.local_number+']'));}
  const block=tier.blocks.at(-1),quote=name.startsWith('actual')?'ekspresen':'črta, pas',span=answer.locator('[data-block-id="'+block.block_id+'"]');
  await span.evaluate((node,quote)=>{
   let position=node.textContent.indexOf(quote);
   const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);let child;while(walker.nextNode()){if(position<walker.currentNode.length){child=walker.currentNode;break;}position-=walker.currentNode.length;}
   const range=document.createRange();range.setStart(child,position);range.setEnd(child,position+quote.length);const selected=getSelection();selected.removeAllRanges();selected.addRange(range);document.dispatchEvent(new Event('selectionchange'));
  },quote);
  await page.locator('#short-feedback .feedback-mark:not(:disabled)').click();assert.equal(await page.locator('#feedback-selection').innerText(),quote);
  await page.locator('#feedback-comment').fill('Offline new explicit render anchor');await page.locator('#feedback-form button[type=submit]').click();await page.locator('#feedback-dialog').waitFor({state:'hidden'});
  const anchor=calls.find(c=>c.api==='/feedback'&&c.method==='POST').body.anchor,start=Array.from(block.text.slice(0,block.text.indexOf(quote))).length;
  assert.deepEqual([anchor.block_id,anchor.start,anchor.end,anchor.quote],[block.block_id,start,start+Array.from(quote).length,quote]);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  assert(!calls.some(c=>c.api==='/families'&&c.method==='POST'));
  fs.writeFileSync(path.join(folder,name+'-'+width+'-dom.json'),JSON.stringify({text:await answer.innerText(),html:await answer.innerHTML(),copy},null,2)+'\n');
  if(width===390||name==='actual-ALL-sl')await page.screenshot({path:path.join(folder,name+'-'+width+'.png'),fullPage:true});
  checks.push({case:name,locale,width,all_selected_groups_open:true,source_citations:tier.blocks.flatMap(b=>b.evidence_ids).length,
      complete_copy:true,canonical_feedback_anchor:true,external_requests:0,actual_operations:0});await context.close();
 }}finally{await browser.close();}
 console.log(JSON.stringify({status:'PASS',checks,external_requests:0,actual_operations:0}));
})().catch(error=>{console.error(error.stack);process.exit(1);});
