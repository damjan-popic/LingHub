/* Offline partial-comparison reader flow; all network routes are synthetic. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://comparison.test';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--disable-dev-shm-usage']});
 try{for(const locale of ['sl','it'])for(const code of ['comparison_coverage_incomplete','comparison_explanation_incomplete']){
  const context=await browser.newContext(),page=await context.newPage(),errors=[],calls=[];
  const labels=JSON.parse(fs.readFileSync(path.join(assets,'i18n',locale+'.json'))),title='Complete <original> explanation';
  const family={answer_family_id:'synthetic-family',question:'Option A or option B?',locale,task:'reference',unified_answers:true,
   short:{state:'complete',status:'partial',run_id:'synthetic-run',delivery_token:'synthetic-delivery',generation_status:'generated',answer_kind:'generated',
    blocks:[{block_id:'C1',text:'Supported part.',evidence_ids:['cited']}],
    evidence:[{evidence_id:'cited',local_number:1,source_slug:'jezikovna_posta',title:'Cited original'}],
    limitations:[{code,scope:'answer',claim_ids:[]}],decision_sources:[{evidence_id:'explanation',title}]}};
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',async route=>{
   const request=route.request(),u=new URL(request.url());assert.equal(u.origin,origin,'No external network access');
   const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
   if(u.pathname.startsWith('/assets/')){const file=u.pathname.slice(8);assert(!file.includes('..'));return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(assets,file))});}
   if(u.pathname==='/'||u.pathname==='/it/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__',locale)});
   const api=u.pathname.slice('/api/pilot'.length);calls.push({path:api,method:request.method()});
   if(api==='/session')return json({authenticated:true,csrf_token:'synthetic',user:{username:'synthetic',role:'tester',shared_group:true,can_change_password:false},capabilities:{project_beta:true,unified_answers:true,comparisons:false,documentary_sources:[]}});
   if(api==='/capabilities')return json({project_beta:true});
   if(api==='/feedback'||api.endsWith('/feedback'))return json({feedback:[],events:[]});
   if(api==='/families')return json({families:[]});
   if(api==='/families/synthetic-family')return json(family);
   if(api.endsWith('/delivered'))return json({acknowledged:true});
   if(api.endsWith('/sources'))return json({sources:[],usage_final:true});
   if(api.endsWith('/sources/explanation'))return json({title,source_label:'Synthetic original',excerpt:'Complete source with its necessary condition.',bibliography:{authors:['Synthetic author'],signed_date_text:['January 2013']}});
   throw Error('Unexpected route '+api);
  });
  await page.goto(origin+(locale==='it'?'/it/':'/')+'?answer=synthetic-family');
  const button=page.locator('button.decision-source');await button.waitFor();
  assert.equal(await button.innerText(),labels.comparison_original_link.replace('{title}',title));
  assert((await page.locator('.answer-notes').innerText()).includes(labels['limitation_'+code]));
  assert.equal(await page.locator('#short-answer .citation').count(),2,'Claim and scoped limitation retain existing citation; source link is unnumbered');
  assert.equal(await page.locator('#short-answer [data-block-id]').innerText(),'Supported part.');
  assert.equal(await page.locator('.decision-source original').count(),0,'Title is text, not injected markup');
  await button.click();await page.locator('#source-content blockquote').waitFor();
  assert.equal(await page.locator('#source-content blockquote').innerText(),'Complete source with its necessary condition.');
  assert(calls.some(call=>call.path==='/families/synthetic-family/sources/explanation'&&call.method==='GET'));
  assert(!calls.some(call=>call.method==='POST'&&!call.path.endsWith('/delivered')),'No question, feedback or model call');
  assert.deepEqual(errors,[]);await context.close();
 }
 console.log(JSON.stringify({status:'PASS',scenarios:4,locales:['sl','it'],model_calls:0,external_requests:0}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
