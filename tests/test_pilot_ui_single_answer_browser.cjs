/* Offline ordinary-answer/history flow; every network response is a fixture. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://single-answer.test';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--disable-dev-shm-usage']});
 try{for(const locale of ['sl','it'])for(const mode of ['single','historical-short','historical-long']){
  const context=await browser.newContext(),page=await context.newPage(),errors=[],calls=[];
  const labels=JSON.parse(fs.readFileSync(path.join(assets,'i18n',locale+'.json'))),tier=mode==='single'?'answer':mode==='historical-long'?'long':'short';
  const run=(name,sourceTier)=>({state:'complete',status:'validated',run_id:name,source_tier:sourceTier,delivery_token:'delivery-'+name,
   generation_status:'generated',answer_kind:'generated',answer_md:'Complete '+name,
   blocks:[{block_id:'C1',text:'Complete '+name,evidence_ids:['source-'+name]}],
   evidence:[{evidence_id:'source-'+name,local_number:1,source_slug:'jezikovna_posta',title:'Original '+name}],limitations:[]});
  const answer=run('new-answer','answer'),short=run('old-short','short'),long=run('old-long','long');
  const family={answer_family_id:'synthetic-family',question:'Which alternative?',locale,task:'reference',unified_answers:true,matches_available:true,
    ...(mode==='single'?{answer_format:'single',answer,short:answer,long:null}:{short,long:mode==='historical-long'?long:null})};
  const selected=mode==='single'?answer:mode==='historical-long'?long:short;
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',async route=>{
   const request=route.request(),u=new URL(request.url());assert.equal(u.origin,origin,'No external network access');
   const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   if(u.pathname.startsWith('/assets/')){const file=u.pathname.slice(8);assert(!file.includes('..'));return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(assets,file))});}
   if(u.pathname==='/'||u.pathname==='/it/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__',locale)});
   const api=u.pathname.slice('/api/pilot'.length);calls.push({path:api,search:u.search,method:request.method(),body:request.postDataJSON()});
   if(api==='/session')return json({authenticated:true,csrf_token:'synthetic',user:{username:'synthetic',role:'tester',shared_group:true,can_change_password:false},capabilities:{project_beta:true,single_answers:true,unified_answers:true,comparisons:false,documentary_sources:[]}});
   if(api==='/capabilities')return json({project_beta:true,single_answers:true});
   if(api==='/feedback'&&request.method()==='POST')return json({acknowledged:true,feedback_id:'fixture-feedback'});
   if(api==='/feedback'||api.endsWith('/feedback'))return json({feedback:[],events:[]});
   if(api==='/families')return json({families:[family]});
   if(api==='/families/synthetic-family')return json(family);
   if(api.endsWith('/delivered'))return json({acknowledged:true});
   if(api.endsWith('/sources'))return json({sources:[],usage_final:true});
   if(api.includes('/sources/source-'))return json({title:'Retained original',source_label:'Source',excerpt:'Complete retained source for '+selected.run_id});
   throw Error('Unexpected route '+api);
  });
  await page.goto(origin+(locale==='it'?'/it/':'/')+'?answer=synthetic-family');
  await page.locator('#short-answer [data-block-id]').waitFor();
  assert.equal(await page.locator('#short-answer [data-block-id]').innerText(),'Complete '+selected.run_id);
  assert.equal(await page.locator('#short-heading').innerText(),labels.beta_short_heading);
  assert.equal(await page.locator('#long-section').isVisible(),false);
  assert.equal(await page.locator('#compare-button').isVisible(),false);
  assert.equal(await page.locator('#expand-button span').innerText(),labels.beta_more);
  await page.locator('#short-answer .citation').first().click();await page.locator('#source-content blockquote').waitFor();
  assert.equal(await page.locator('#source-content blockquote').innerText(),'Complete retained source for '+selected.run_id);
  assert(calls.some(c=>c.path.endsWith('/sources/source-'+selected.run_id)&&c.search==='?tier='+tier));
  assert(calls.some(c=>c.path==='/runs/'+selected.run_id+'/delivered'&&c.body.expanded===(tier==='long')));
  assert(calls.some(c=>c.path==='/runs/'+selected.run_id+'/feedback'));
  assert(!calls.some(c=>c.path.includes('/expand')||c.method==='POST'&&!c.path.endsWith('/delivered')),'Viewing one answer/history never requests generation');
  await page.locator('#source-content').getByRole('button',{name:labels.beta_mark_error||labels.mark_error,exact:true}).click();
  await page.locator('#feedback-comment').fill('Synthetic source feedback');
  await page.locator('#feedback-form button[type=submit]').click();
  await page.locator('#feedback-dialog').waitFor({state:'hidden'});
  const saved=calls.find(c=>c.path==='/feedback'&&c.method==='POST');
  assert.equal(saved.body.run_id,selected.run_id);assert.deepEqual(saved.body.target,{kind:'source',evidence_id:'source-'+selected.run_id});
  assert(!calls.some(c=>c.path.includes('/expand')||c.method==='POST'&&!c.path.endsWith('/delivered')&&c.path!=='/feedback'));
  assert.deepEqual(errors,[]);await context.close();
 }
 console.log(JSON.stringify({status:'PASS',scenarios:6,locales:['sl','it'],new_answer:true,historical_short:true,historical_long:true,source_tiers_preserved:true,model_calls:0,external_requests:0}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
