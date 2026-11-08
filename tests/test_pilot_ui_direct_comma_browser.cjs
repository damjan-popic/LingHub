/* Intercepted browser requests only; no service, account, model or checker call. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://comma-ui.test';
const question='Preveri vejice: Če ne pride ne grem.',target='Če ne pride ne grem.';
const calls=[],errors=[];let submitted;
const family={answer_family_id:'comma-family',question,locale:'sl',task:'reference',matches_available:false,
 short:{state:'complete',answer_kind:'tool_prediction',run_id:'comma-run',blocks:[],evidence:[],
  comma_proposal:{label:'Predlog postavitve vejic · CJVT Vejice',status:'ok',original_text:target,
   proposed_text:'Če ne pride, ne grem.',edits:[{start:11,end:11,replacement:',',kind:'insert'}],
   offset_convention:'unicode_codepoint_half_open',checked_extent:{start:0,end:target.length,complete:true},
   explanation_status:'unavailable'}}};
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  for(const width of [390,1280]){
   const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
   page.on('pageerror',e=>errors.push(e.message));
   await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());assert.equal(url.origin,origin,'No external request');
    const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    if(url.pathname.startsWith('/assets/')){
     const file=url.pathname.slice(8);assert(!file.includes('..'));
     return route.fulfill({contentType:({'.mjs':'text/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/json',body:fs.readFileSync(path.join(assets,file))});
    }
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(assets,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__','sl')});
    const api=url.pathname.slice('/api/pilot'.length);calls.push({api,method:req.method()});
    const capabilities={project_beta:true,comma_checker:{configured:true,provider_label:'CJVT Vejice',
     disclosure_sl:'Posebni pogoji te povezave niso potrjeni; stara opomba.',terms_url:'https://orodja.cjvt.si/vejice/about'}};
    if(api==='/session')return json({authenticated:true,user:{id:'fixture',username:'fixture',role:'tester'},csrf_token:'fixture',capabilities});
    if(api==='/capabilities')return json(capabilities);
    if(api.startsWith('/feedback')||api.endsWith('/feedback'))return json({feedback:[]});
    if(api==='/families'&&req.method()==='POST'){submitted=req.postDataJSON();return json({family});}
    if(api==='/families/comma-family')return json({family});
    if(api.endsWith('/delivered'))return json({acknowledged:true});
    throw Error('Unexpected fixture API '+api);
   });
   await page.goto(origin+'/');await page.locator('#app-view').waitFor({state:'visible'});
   assert.equal(await page.locator('#comma-checker-control,#comma-checker-consent,#comma-checker-terms').count(),0);
   assert(!(await page.locator('body').innerText()).includes('Posebni pogoji te povezave'));
   assert(!(await page.locator('body').innerText()).includes('Izbirni pregled vejic'));
   const before=calls.length;await page.locator('#question').fill(question);assert.equal(calls.length,before,'Typing makes no provider request');
   await page.locator('#ask-button').click();await page.locator('#comma-proposal').waitFor({state:'visible'});
   assert.equal(submitted.question,question);assert(!('comma_checker_consent'in submitted),'No fabricated checkbox consent');
   assert.equal(await page.locator('#comma-proposal .comma-proposed').innerText(),'Če ne pride, ne grem.');
   assert.equal(calls.filter(c=>c.api==='/families'&&c.method==='POST').length,width===390?1:2);
   await context.close();
  }
  assert.deepEqual(errors,[]);console.log(JSON.stringify({status:'pass',viewports:[390,1280],submission:'explicit ordinary question; legacy consent field omitted',external_requests:0}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
