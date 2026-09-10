#!/usr/bin/env node
/* Existing real source, synthetic login; no new family, generation or vote. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const args=process.argv.slice(2),arg=key=>args[args.indexOf(key)+1];
const out=arg('--output-directory'),accounts=arg('--accounts'),home=arg('--base-url'),familyId=arg('--family-id');
assert(out&&accounts&&home&&familyId);assert.equal(fs.statSync(out).mode&0o077,0);
const hash=value=>crypto.createHash('sha256').update(value).digest('hex');
const save=(name,data)=>{const file=path.join(out,name);fs.writeFileSync(file,JSON.stringify(data,null,2)+'\n',{mode:0o600});return {path:file,sha256:hash(fs.readFileSync(file))};};
const report={schema:'klikai.ui.source-attribution.v1',at:new Date().toISOString(),synthetic_account:true,existing_family:familyId,new_generation:false,checks:[],errors:[]};
let browser,page;
(async()=>{try{
  browser=await chromium.launch({headless:true,executablePath:'/opt/google/chrome/chrome',args:['--disable-dev-shm-usage']});
  page=await browser.newPage({viewport:{width:1440,height:1050}});
  page.on('pageerror',error=>report.errors.push(error.message));
  const forbidden=[];page.on('request',request=>{if(request.method()==='POST'&&(/\/families$|\/expand$|\/comparisons$|\/vote$/.test(request.url())))forbidden.push(request.url());});
  const account=JSON.parse(fs.readFileSync(accounts)).find(item=>item.role==='tester');
  await page.goto(home+'/');await page.locator('#username').fill(account.username);await page.locator('#password').fill(account.password);await page.locator('#login-form button[type=submit]').click();await page.locator('#app-view').waitFor({state:'visible'});
  const api=home+'/api/pilot',beforeResponse=await page.request.get(api+'/families/'+familyId);assert.equal(beforeResponse.status(),200);const before=await beforeResponse.json();
  report.original_family=save('family-before.json',before);
  for(const locale of ['sl','it']){
    await page.setViewportSize(locale==='sl'?{width:1440,height:1050}:{width:390,height:844});
    await page.goto(home+(locale==='it'?'/it/':'/')+'?answer='+familyId);
    await page.locator('#short-answer [data-block-id]').first().waitFor();
    const responsePromise=page.waitForResponse(response=>response.url().includes('/families/'+familyId+'/sources/')&&response.status()===200);
    await page.locator('#short-sources .source-button').first().click();
    const response=await responsePromise,data=await response.json(),original=data.excerpt;
    assert(original.includes('Helena Dobrovoljc (junij 2026)'));assert(original.includes('Odgovor je bil pripravljen v sklopu projekta'));
    await page.locator('#source-content .source-original').waitFor({state:'attached'});
    const visible=await page.locator('#source-content').innerText(),quotation=await page.locator('#source-content > blockquote').textContent();
    assert(!visible.includes('Helena Dobrovoljc'));assert(!visible.includes('Odgovor je bil pripravljen v sklopu projekta'));
    assert(quotation.includes('Hugo'));assert.equal(quotation,original.slice(0,original.indexOf('\nHelena Dobrovoljc (junij 2026)')).trimEnd());
    assert.equal(await page.locator('#source-content .source-bibliography').getAttribute('open'),null);
    assert(!(await page.locator('#source-content .source-original').isVisible()));
    const shot=path.join(out,'hugo-source-'+locale+'.png');await page.screenshot({path:shot,fullPage:true});fs.chmodSync(shot,0o600);
    assert((await page.evaluate(()=>document.documentElement.scrollWidth))<= (locale==='sl'?1440:390));
    await page.locator('#source-content .source-bibliography > summary').click();
    assert(await page.locator('#source-content .source-original').isVisible());
    assert.equal(await page.locator('#source-content .source-original').textContent(),original);
    const opened=path.join(out,'hugo-source-bibliography-'+locale+'.png');await page.screenshot({path:opened,fullPage:true});fs.chmodSync(opened,0o600);
    const afterSource=await page.request.get(response.url());assert.equal(afterSource.status(),200);assert.deepEqual(await afterSource.json(),data);
    report.checks.push({name:locale+'_default_drawer_no_byline_complete_original_on_request',status:'PASS',source:save('source-'+locale+'.json',data),original_sha256:hash(original),display_prefix_sha256:hash(quotation),canonical_source_unchanged:true,screenshots:[shot,opened].map(file=>({path:file,sha256:hash(fs.readFileSync(file))}))});
  }
  const after=await(await page.request.get(api+'/families/'+familyId)).json();
  for(const tier of ['short','long']){assert.deepEqual(after[tier]?.blocks,before[tier]?.blocks);assert.equal(after[tier]?.display_sha256,before[tier]?.display_sha256);}
  report.checks.push({name:'canonical_answer_blocks_and_display_hashes_unchanged',status:'PASS'});
  assert.deepEqual(forbidden,[]);assert.deepEqual(report.errors,[]);report.status='PASS';
}catch(error){report.status='FAIL';report.error=error.message;process.exitCode=1;}
finally{await browser?.close();const receipt=save('source-attribution-browser.json',report);console.log(JSON.stringify({status:report.status,checks:report.checks.length,...receipt}));}})();
