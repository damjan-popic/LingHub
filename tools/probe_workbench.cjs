#!/usr/bin/env node
/* Real Chrome acceptance; only the explicitly marked XSS/unavailable fixtures are mocked. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { chromium } = require('/home/damjan/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const args = process.argv.slice(2);
function option(name) { const i = args.indexOf(name); if (i < 0 || !args[i + 1]) throw Error('Required '+name); return args[i + 1]; }
const base = new URL(option('--base-url')), reportPath = path.resolve(option('--report'));
if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname) || !base.port || base.pathname !== '/' || base.username || base.password || base.search || base.hash) throw Error('Explicit loopback HTTP origin required');
const skipK = args.includes('--skip-klikai');
const report = {schema:'lingstack.workbench.browser.v1', status:'RUNNING', at:new Date().toISOString(), origin:base.origin, checks:[], skips:[], http:[], browser:'Chrome', synthetic_inputs_only:true};
let browser, page, activeCheck = 'setup';
const hash = text => crypto.createHash('sha256').update(text).digest('hex');
async function check(name, fn) { activeCheck = name; const t = Date.now(); const detail = await fn(); report.checks.push({name,status:'PASS',milliseconds:Date.now()-t,...(detail||{})}); }
async function finished() { await page.waitForFunction(() => !document.getElementById('status').textContent.startsWith('Obdelava'), null, {timeout:60000}); }
async function clickHTTP(id, route) {
  const responsePromise = page.waitForResponse(r => new URL(r.url()).pathname === route && r.request().method() !== 'OPTIONS', {timeout:60000});
  await page.locator('#'+id).click(); const response = await responsePromise; const raw = await response.text();
  report.http.push({route,status:response.status(),bytes:Buffer.byteLength(raw),sha256:hash(raw)});
  assert.equal(response.status(),200,route+' '+raw.slice(0,180)); await finished();
  return {data:JSON.parse(raw),request:response.request().postDataJSON()};
}
async function textInput(text, lang='sl', engine='default') { await page.locator('#lang').selectOption(lang); await page.locator('#text').fill(text); await page.locator('#engine').selectOption(engine); }
(async()=>{
  try {
    const st = fs.statSync(path.dirname(reportPath)); if (st.uid !== process.getuid() || (st.mode & 0o077)) throw Error('Report parent must be owned and private (0700)');
    browser = await chromium.launch({headless:true, executablePath:'/opt/google/chrome/chrome', args:['--disable-dev-shm-usage']});
    const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
    page = await context.newPage(); const pageErrors=[]; page.on('pageerror',e=>pageErrors.push(e.message));
    let downloads=0; page.on('download',()=>downloads++);
    await check('local_page_catalogue_real_models',async()=>{
      const response = await page.goto(base.origin+'/workbench/',{waitUntil:'networkidle'});
      assert.equal(response.status(),200); assert(response.headers()['content-security-policy'].includes("script-src 'self'"));
      await page.waitForFunction(()=>document.querySelector('#capabilities').textContent.includes('Privzeto:'));
      assert((await page.title()).includes('Jezikovna delavnica'));
      assert((await page.locator('#catalogue').textContent()).includes('nekomercialne'));
      assert(!(await page.locator('#engine option[value=classla]').evaluate(el=>el.disabled)));
      assert((await page.locator('#capabilities').textContent()).includes('Oblikoslovje: na voljo'));
      return {csp:true,model_noncommercial_notice:true};
    });
    await check('real_loris_advice_and_token_offsets',async()=>{
      await textInput('abonma compact disc gazdina Trst.'); const {data} = await clickHTTP('analyze','/v1/analyze');
      assert.equal(data.engine_used,'spacy'); assert(data.issues.some(x=>x.rule_id==='paronym_abonma'));
      assert((await page.locator('#analysis .advice').allTextContents()).some(x=>x.includes('abbonamento')));
      assert(await page.locator('#analysis mark').count()>0); assert.equal(await page.locator('#analysis tbody tr').count(),data.tokens.length);
      const token = data.tokens.find(t=>t.text==='gazdina'); await page.locator('#analysis button.token').filter({hasText:/^gazdina$/}).click();
      assert((await page.locator('#selection-label').textContent()).includes(`${token.start}–${token.end}`));
      return {rules:data.issues.map(x=>x.rule_id),advice_visible:true};
    });
    await check('unicode_repeated_selection_real_explore',async()=>{
      const text='🙂 otrok otrok, e\u0301 in Trst.'; await textInput(text);
      const startUtf16=text.lastIndexOf('otrok'), endUtf16=startUtf16+5, expected={start:Array.from(text.slice(0,startUtf16)).length,end:Array.from(text.slice(0,endUtf16)).length};
      await page.locator('#text').evaluate((el,range)=>{el.focus();el.setSelectionRange(...range);el.dispatchEvent(new Event('select'));},[startUtf16,endUtf16]);
      const {data,request} = await clickHTTP('explore','/v1/explore');
      assert.deepEqual(request.selection,expected); assert.equal(data.selection.text,'otrok'); assert.equal(data.provenance.analysis_calls,1);
      assert.equal(data.results.synonyms.status,'ok'); assert.equal(data.results.collocations.status,'ok');
      assert((await page.locator('#lexical-results').textContent()).includes('Jedrne:'));
      assert((await page.locator('#lexical-results').textContent()).includes('Pogostnost:'));
      return {selection:expected,analysis_calls:1};
    });
    await check('real_spacy_classla_comparison_alignment',async()=>{
      await textInput('🙂 Otroci berejo knjige.\nV Trst je prišla.');
      const responses=[]; const listen=async response=>{if(new URL(response.url()).pathname==='/v1/analyze'&&response.request().method()==='POST') responses.push(await response.json());};
      page.on('response',listen); await page.locator('#compare').click();
      await page.waitForFunction(()=>document.querySelector('#comparison .compare-grid')&&document.querySelector('#comparison').textContent.includes('Poravnava'),null,{timeout:60000}); await finished(); page.off('response',listen);
      assert.equal(responses.length,2); assert.deepEqual(responses.map(x=>x.engine_used).sort(),['classla','spacy']);
      assert.equal(await page.locator('#comparison .compare-grid > div').count(),2);
      assert((await page.locator('#comparison').textContent()).includes('Natančno'));
      return {engines:responses.map(x=>x.engine_used),default_unchanged:await page.locator('#engine').inputValue()};
    });
    await check('real_inflections_native_msds_and_lemma',async()=>{
      await page.locator('#lemma').fill('zbor'); const {data}=await clickHTTP('inflect','/v1/morphology/inflect');
      assert(data.forms.length>0); const cells=await page.locator('#lexical-results tbody tr').first().locator('td').allTextContents();
      assert.equal(cells[1],'zbor'); assert.equal(cells[2],data.forms[0].msds.join(', ')); assert.equal(cells[3],'Natančno');
      return {forms:data.forms.length,native_msd:cells[2]};
    });
    await check('real_source_grouped_synonyms_and_collocations',async()=>{
      await page.locator('#lemma').fill('otrok'); const syn=await clickHTTP('synonyms','/v1/resources/thesaurus');
      assert(syn.data.entries.length); let rendered=await page.locator('#lexical-results').textContent(); assert(rendered.includes('Jedrne:')&&rendered.includes('Bližnje:')&&rendered.includes('Pomen '));
      const coll=await clickHTTP('collocations','/v1/resources/collocations'); assert(coll.data.collocations.length); rendered=await page.locator('#lexical-results').textContent();
      assert(rendered.includes('Pogostnost:')&&rendered.includes('logDice:')&&rendered.includes('struktura:'));
      return {synonym_entries:syn.data.entries.length,collocations:coll.data.collocations.length};
    });
    await check('real_dictionary_no_hit',async()=>{
      await page.locator('#lemma').fill('zqxv987654'); const {data}=await clickHTTP('synonyms','/v1/resources/thesaurus');
      assert.equal(data.count_entries,0); assert.deepEqual(data.entries,[]); assert((await page.locator('#lexical-results').textContent()).includes('Ni slovarskega zadetka'));
    });
    await check('italian_analysis_and_explicit_unsupported_dictionary',async()=>{
      await textInput('I bambini leggono libri a Trieste.','it'); assert(await page.locator('#engine option[value=classla]').evaluate(el=>el.disabled));
      await page.locator('#synonyms').click(); await finished(); assert((await page.locator('#status').textContent()).includes('podpirata slovenščino'));
      const {data}=await clickHTTP('analyze','/v1/analyze'); assert.equal(data.engine_used,'spacy'); assert(data.tokens.some(t=>t.lemma==='bambino')); return {lang:data.lang,engine:data.engine_used};
    });
    await check('empty_text_validation',async()=>{await textInput('  ');await page.locator('#analyze').click();await finished();assert((await page.locator('#status').textContent()).includes('Vnesite besedilo'));});
    await check('xss_payload_rendered_as_literal_text_deliberate_fixture',async()=>{
      const attack='<img src=x onerror="window.__lingstack_xss=1">';
      await page.route('**/v1/resources/thesaurus?*',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({status:'ok',entries:[{headword:attack,senses:[{sense_id:attack,relations:[{relation_type:'synonym',synonym_type:'core',text:attack}]}]}]})}),{times:1});
      await page.locator('#lemma').fill('otrok'); await clickHTTP('synonyms','/v1/resources/thesaurus');
      assert((await page.locator('#lexical-results').textContent()).includes(attack));assert.equal(await page.locator('#lexical-results img').count(),0);assert.equal(await page.evaluate(()=>window.__lingstack_xss),undefined);
      return {deliberate_mock:true};
    });
    await check('unavailable_optional_engine_visible_deliberate_fixture',async()=>{
      const actual = await (await page.request.get(base.origin+'/v1/capabilities')).json(); actual.engines.classla.available=false;
      await page.route('**/v1/capabilities',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(actual)}),{times:1});
      await page.locator('#refresh').click();await finished();assert(await page.locator('#compare').isDisabled());assert((await page.locator('#capabilities').textContent()).includes('CLASSLA: ni dosegljivo'));
      await page.locator('#lemma').fill('zbor');await clickHTTP('inflect','/v1/morphology/inflect');
      await page.locator('#refresh').click();await finished();assert(!(await page.locator('#compare').isDisabled()));return {deliberate_mock:true,independent_morphology_real:true};
    });
    if(skipK){report.skips.push({name:'klikai_terminology_real_citations',status:'SKIP',reason:'Explicit --skip-klikai: isolated KLIKai database candidate awaits final deployment transaction.'},{name:'klikai_real_tool_linghub_chain',status:'SKIP',reason:'Explicit --skip-klikai; no claim of complete integration.'});}
    else{
      await check('klikai_terminology_real_citations',async()=>{await page.locator('#term-query').fill('BES');const {data}=await clickHTTP('term-search','/workbench-api/terminology');assert(data.result.hits.length>0);assert(data.result.hits.every(h=>h.citation_id&&h.source_display_name));assert((await page.locator('#term-results').textContent()).includes('Citat:'));return{hits:data.result.hits.length,citation_ids:data.result.hits.map(h=>h.citation_id)};});
      await check('klikai_real_tool_linghub_chain',async()=>{await textInput('abonma compact disc gazdina Trst.');const {data}=await clickHTTP('klikai-check','/workbench-api/tool');assert.equal(data.result.status,'ok');assert(JSON.stringify(data.result).includes('paronym_abonma'));assert.equal(data.result.trace.provider,'linghub');assert(data.result.trace.routes.includes('/v1/explore'));assert.equal(data.result.trace.input_stored,false);assert.equal(data.result.results.loris.data.advice_payload_withheld,true);assert(data.result.results.loris.data.matches.every(x=>!('payload' in x)));assert((await page.locator('#term-results').textContent()).includes('paronym_abonma'));assert((await page.locator('#term-results').textContent()).includes('Dejanski klic KLIKai'));return{tool_status:data.result.status};});
    }
    await check('explicit_utf8_exports_and_no_browser_storage',async()=>{
      assert.equal(downloads,0);await textInput('🙂 Ana bere knjige.');await clickHTTP('analyze','/v1/analyze');
      for(const [id,name]of [['export-json','session.json'],['export-tsv','tokens.tsv']]){
        const wait=page.waitForEvent('download');await page.locator('#'+id).click();const download=await wait;const target=path.join(path.dirname(reportPath),name);await download.saveAs(target);fs.chmodSync(target,0o600);const content=fs.readFileSync(target,'utf8');assert(content.includes('🙂'));
        if(id==='export-json')assert.equal(JSON.parse(content).analysis.text,'🙂 Ana bere knjige.');else assert(content.startsWith('surface\tlemma\tupos\tstart\tend\tengine\n'));
      }
      assert.equal(downloads,2);assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0});
      return{explicit_downloads:2,local_storage_entries:0,session_storage_entries:0};
    });
    await check('fresh_page_has_no_prior_text_or_analysis',async()=>{
      const clean=await context.newPage();await clean.goto(base.origin+'/workbench/',{waitUntil:'networkidle'});assert(!(await clean.locator('#text').inputValue()).includes('🙂'));assert.equal(await clean.locator('#analysis table').count(),0);await clean.close();
    });
    assert.deepEqual(pageErrors,[]);report.page_errors=pageErrors;report.screenshot=path.basename(reportPath,'.json')+'.png';await page.screenshot({path:path.join(path.dirname(reportPath),report.screenshot),fullPage:true});
    fs.chmodSync(path.join(path.dirname(reportPath),report.screenshot),0o600);
    report.status=skipK?'PASS_WITH_KLIKAI_PENDING':'PASS';
  }catch(error){report.status='FAIL';report.failed_check=activeCheck;report.error={name:error.name,message:error.message};if(page){try{await page.screenshot({path:reportPath+'.failure.png',fullPage:true});fs.chmodSync(reportPath+'.failure.png',0o600);}catch{}}process.exitCode=1;}
  finally{if(browser)await browser.close();fs.writeFileSync(reportPath,JSON.stringify(report,null,2)+'\n',{mode:0o600});fs.chmodSync(reportPath,0o600);console.log(JSON.stringify({status:report.status,checks:report.checks.length,skips:report.skips.length,report:reportPath,error:report.error}));}
})();
