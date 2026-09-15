/* Real UI assets and intercepted synthetic responses; no live model/service. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {selectComposerOption}=require('./pilot_ui_options.cjs');
const root=path.resolve(__dirname,'../pilot_ui'),origin='https://pilot.test';
const report={scope:'isolated model-choice browser with synthetic API',live_requests:0,model_calls:0,checks:[],errors:[]};
let browser;
(async()=>{try{
  browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--disable-dev-shm-usage']});
  const context=await browser.newContext(),page=await context.newPage(),records=new Map(),submissions=[];
  let sharedAvailable=true,gemmaAvailable=true;
  const models=['auto','qwen','gemma'],baseCaps={tasks:['reference','terminology'],models,default_model:'auto',comparisons:false,unified_answers:true};
  function capabilities(){return {...baseCaps,generation:{models:{
    auto:{available:true,status:sharedAvailable?'SHARED_READY':'CPU_READY',device:sharedAvailable?'gpu':'cpu',provider_scope:sharedAvailable?'shared':'own'},
    qwen:{available:true,status:'CPU_READY',device:'cpu',provider_scope:'own'},
    gemma:{available:gemmaAvailable,status:gemmaAvailable?'CPU_READY':'GENERATION_WAITING_CAPACITY',device:'cpu',provider_scope:'own'}}}};}
  function family(row){
    const complete=row.phase==='complete',eid='synthetic-evidence-'+row.id;
    return {answer_family_id:row.id,question:'Synthetic common question',task:'reference',model:row.selected,locale:'sl',input_language:'und',unified_answers:true,matches_available:row.phase!=='retrieval',long:null,
      short:{run_id:'run-'+row.id,job_id:'job-'+row.id,model:row.selected,state:complete?'complete':'running',progress:{stage:row.phase,matches_ready:row.phase!=='retrieval'},...(complete?{
        status:'validated',answer_kind:'generated',generation_status:'generated',display_sha256:'synthetic-display',
        blocks:[{block_id:'claim',text:'Synthetic qualified generated answer [1].',evidence_ids:[eid]}],
        evidence:[{evidence_id:eid,source_slug:'jezikovna_posta',local_number:1}],source_badges:[],
        generation:{status:'generated',provider:row.provider},retrieval:{status:'complete'}}:{})}};
  }
  page.on('pageerror',error=>report.errors.push(error.message));
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url());assert.equal(url.origin,origin);
    const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
    if(url.pathname.startsWith('/assets/')){const file=url.pathname.slice(8);assert(!file.includes('..'));return route.fulfill({contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'application/json',body:fs.readFileSync(path.join(root,file))});}
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'index.html'),'utf8').replaceAll('__PILOT_BASE__','').replaceAll('__PILOT_LOCALE__','sl')});
    const api=url.pathname.slice('/api/pilot'.length);assert(url.pathname.startsWith('/api/pilot'));
    if(api==='/session')return json({authenticated:true,csrf_token:'synthetic-csrf',user:{id:'synthetic-owner',username:'synthetic-owner',role:'tester',must_change_password:false},capabilities:baseCaps});
    if(api==='/capabilities')return json(capabilities());
    if(api==='/feedback')return json({feedback:[]});
    if(api==='/families'&&req.method()==='POST'){
      const input=req.postDataJSON();assert.equal(input.task,'reference');assert(models.includes(input.model));submissions.push(input.model);
      const shared=input.model==='auto'&&sharedAvailable,id='family-'+submissions.length;
      const row={id,selected:input.model,phase:'retrieval',polls:0,provider:{repository:shared?'synthetic/shared-Qwen':input.model==='gemma'?'synthetic/own-Gemma':'synthetic/own-Qwen',revision:'synthetic-frozen-revision',device:shared?'gpu':'cpu',provider_scope:shared?'shared':'own',selection:input.model,route_reason:shared?'shared_ready':input.model==='auto'?'shared_unavailable_own':'own_cpu_fallback'}};
      records.set(id,row);return json({family:family(row),job:{job_id:'job-'+id,state:'queued',model:input.model}});
    }
    if(api.startsWith('/jobs/job-')){const row=records.get(api.slice('/jobs/job-'.length));assert(row);row.polls++;row.phase=row.polls===1?'retrieval':row.polls===2?'generation':'complete';return json({job_id:'job-'+row.id,state:row.phase==='complete'?'complete':'running',model:row.selected,progress:{stage:row.phase,matches_ready:row.phase!=='retrieval'}});}
    if(api.startsWith('/families/')){
      const [,id,reader]=api.slice(1).split('/'),row=records.get(id);assert(row);
      if(!reader)return json(family(row));
      if(reader==='matches')return json({items:[{evidence_id:'synthetic-evidence-'+id,source_slug:'jezikovna_posta',source_label:'Jezikovna pošta',title:'Synthetic original',snippet:'Synthetic matched advice',used_in_answer:row.phase==='complete'}],returned_count:1,next_offset:null,coverage:[],usage_final:row.phase==='complete'});
      if(reader==='sources')return json({evidence_id:'synthetic-evidence-'+id,source_label:'Jezikovna pošta',excerpt:'Synthetic original question?\n\nComplete original answer and qualification.'});
    }
    if(api.startsWith('/runs/')&&api.endsWith('/delivered'))return json({acknowledged:true});
    throw Error('Unexpected synthetic browser route '+api);
  });
  await page.goto(origin);await page.locator('#app-view').waitFor({state:'visible'});
  assert.equal(await page.locator('#options').isVisible(),false);assert.equal(await page.locator('#model').inputValue(),'auto');
  let sharedAnswer;
  async function ask(selection){
    if(submissions.length)await page.locator('#new-question').click();
    if(selection!=='auto'||submissions.length)await selectComposerOption(page,'#model',selection);
    await page.locator('#question').fill('Synthetic common question');await page.locator('#ask-button').click();
    await page.waitForFunction(()=>document.querySelector('#job-state').dataset.stage==='generation');
    await page.locator('.match-record').waitFor();assert.equal(await page.locator('#short-answer').textContent(),'');
    await page.waitForFunction(()=>document.querySelector('#job-state').hidden);
    const label=await page.locator('#answer-model').textContent();
    assert.equal(submissions.at(-1),selection);assert.equal(await page.locator('#short-answer .citation').count(),1);
    await page.locator('#short-answer .citation').click();await page.locator('#source-content blockquote').waitFor();assert((await page.locator('#source-content blockquote').textContent()).includes('Complete original answer'));await page.locator('[data-close=source-dialog]').click();
    report.checks.push({selection,label,status:'PASS'});return label;
  }
  sharedAnswer=await ask('auto');assert(sharedAnswer.includes('synthetic/shared-Qwen')&&sharedAnswer.includes('deljeni GPU'));
  const sharedURL=page.url();
  const qwen=await ask('qwen');assert(qwen.includes('synthetic/own-Qwen')&&qwen.includes('CPU')&&!qwen.includes('deljeni GPU'));
  const gemma=await ask('gemma');assert(gemma.includes('synthetic/own-Gemma')&&gemma.includes('CPU')&&!gemma.includes('Qwen'));
  sharedAvailable=false;await page.goto(sharedURL);await page.waitForFunction(()=>document.querySelector('#short-answer .citation'));
  assert.equal(await page.locator('#answer-model').textContent(),sharedAnswer);assert.equal(submissions.length,3,'Reopening saved answer must not generate again');
  const fallback=await ask('auto');assert(fallback.includes('synthetic/own-Qwen')&&fallback.includes('CPU')&&fallback.includes('deljeni model ni bil na voljo'));
  gemmaAvailable=false;await page.reload();await page.locator('#app-view').waitFor({state:'visible'});await selectComposerOption(page,'#model','gemma');
  assert.equal(await page.locator('#model option[value=gemma]').isDisabled(),false);
  await page.locator('#capacity-state').waitFor({state:'visible'});assert((await page.locator('#capacity-message').textContent()).includes('ne bo preusmerjena'));
  assert.equal(await page.locator('#model').inputValue(),'gemma');assert.equal(submissions.length,4);
  await page.locator('#capacity-models').click();assert.equal(await page.locator('#options').isVisible(),true);
  assert.deepEqual(report.errors,[]);report.frozen_shared_label_preserved=true;report.unavailable_choice_not_substituted=true;report.status='PASS';
}catch(error){report.status='FAIL';report.error={name:error.name,message:error.message};process.exitCode=1;}finally{if(browser)await browser.close();console.log(JSON.stringify(report));}})();
