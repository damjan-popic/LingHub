/* Browser mechanics with explicit protocol fixtures, no linguistic/model claim. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.KLIKAI_PLAYWRIGHT_MODULE||'/home/damjan/projects/KLIKai-before-office-20261103/node_modules/.pnpm/playwright@1.59.1/node_modules/playwright');
const assets=path.resolve(__dirname,'../pilot_ui'),origin='https://documents.test';
const harness=String.raw`
import {documentUploads} from '/document-uploads.mjs';
let locale='sl',saved=[],doc=null;
window.calls=[];window.sourceOpened=null;
const original='Čebelica 🧑‍🏫 in človek. <script>window.pwned=true</script>';
const api=async(path,options={})=>{
 window.calls.push({path,method:options.method||'GET',body:options.body,headers:options.headers});
 if(path==='/documents')return {documents:saved};
 if(path==='/documents/upload'){
  window.partialFixture=false;
  doc={document_id:'doc-1',name:'vloga.docx',state:'draft',created:1,options:{},warnings:['document_images_not_reviewed'],processed:0,total:1,sections:[{index:0,location:'body/paragraph1',text:original,state:'pending',family_id:null,output:''}]};
  saved=[{document_id:doc.document_id,name:doc.name,state:doc.state}];return structuredClone(doc);
 }
 if(path.endsWith('/start')){
  doc.state='running';doc.options=options.body;saved[0].state='running';return structuredClone(doc);
 }
 if(path==='/documents/doc-1'){
  const candidate=doc.options.translation_provider==='translategemma_sl_it',proposal=candidate?'Bozza italiana sintetica.':'Čebelica.';
  doc.state=window.partialFixture||window.nativeFailedFixture?'failed':'complete';doc.processed=1;saved[0].state=doc.state;Object.assign(doc.sections[0],{state:window.nativeFailedFixture?'failed':'complete',family_id:'family-1',output:window.nativeFailedFixture?'Incomplete provider operation retained.':candidate?'Bozza da controllare.\n\nPRIVATE_REPEATED_TEST_NOTE\nPRIVATE_REPEATED_SOURCE_NOTE\nPRIVATE_REPEATED_INLINE_NOTE\n\n'+proposal:'Predlog: Čebelica.',document_result:window.nativeFailedFixture?null:{mode:doc.options.mode,status:'draft',proposed_text:proposal,edits:[],requires_human_review:true},limitations:window.partialFixture||window.nativeFailedFixture?[{code:'document_transform_failed'}]:[]});return structuredClone(doc);
 }
 if(path.endsWith('/delete')){saved=[];doc=null;return {acknowledged:true};}
 if(path.includes('/export/'))return new Blob(['Original/draft report fixture']);
 if(path.endsWith('/draft/docx?partial=true'))return new Blob(['Explicit partial draft fixture']);
 if(path.endsWith('/draft/txt'))return new Blob(['Bozza italiana sintetica.'],{type:'text/plain;charset=utf-8'});
 if(path.endsWith('/draft/docx'))return new Blob(['Simple translated Word protocol fixture']);
 throw Error('Unexpected '+path);
};
const ui=documentUploads({root:document.getElementById('workspace'),api,locale:()=>locale,openFamily:async id=>{window.sourceOpened=id;},errorText:error=>error.message||'Napaka'});
window.ui=ui;window.changeLocale=value=>{locale=value;ui.locale();};
window.showReplayWarning=()=>{doc.warnings.push('document_repeated_translation');document.querySelector('[data-document-list]').dispatchEvent(new Event('change'));};
window.showPartialReview=()=>{window.partialFixture=true;doc.options.mode='review';doc.warnings=[];document.querySelector('[data-document-list]').dispatchEvent(new Event('change'));};
window.fixtureSnapshot=()=>structuredClone(doc);
window.showNativeFailure=()=>{window.nativeFailedFixture=true;document.querySelector('[data-document-list]').dispatchEvent(new Event('change'));};
window.showGuidedNative=()=>{doc.options.translation_guidance=true;document.querySelector('[data-document-list]').dispatchEvent(new Event('change'));};
ui.session({documents:{enabled:true}});
`;
(async()=>{
 const browser=await chromium.launch({headless:true,args:['--disable-dev-shm-usage']}),checks=[];
 try{for(const locale of ['sl','it'])for(const width of [390,1280]){
  const context=await browser.newContext({viewport:{width,height:950},acceptDownloads:true}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route(origin+'/**',async route=>{
   const file=new URL(route.request().url()).pathname.slice(1);
   const content=file==='harness.mjs'?harness:file==='document-uploads.mjs'?fs.readFileSync(path.join(assets,file),'utf8'):file==='pilot.css'?fs.readFileSync(path.join(assets,file),'utf8'):'<!doctype html><html><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/pilot.css"></head><body><main><section id="workspace" class="subpanel" hidden></section></main><script type="module" src="/harness.mjs"></script></body></html>';
   await route.fulfill({status:200,contentType:file.endsWith('.mjs')?'text/javascript':file.endsWith('.css')?'text/css':'text/html',headers:{'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'"},body:content});
  });
  await page.goto(origin);await page.locator('#workspace>details>summary').waitFor();
  if(locale==='it')await page.evaluate(()=>window.changeLocale('it'));
  await page.locator('#workspace>details>summary').click();
  const file=page.locator('input[type=file]');
  await file.setInputFiles({name:'vloga.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from('test upload fixture')});
  await page.locator('#workspace form button[type=submit]').first().click();
  await page.locator('[data-document-detail] h3').waitFor();assert.equal(await page.locator('[data-document-detail] h3').textContent(),'vloga.docx');
  assert((await page.locator('#workspace').innerText()).includes(locale==='sl'?'Vsebina slik ni pregledana ali prevedena.':'Il contenuto delle immagini non è revisionato né tradotto.'));
  const controls=page.locator('.document-options select');await controls.nth(0).selectOption('translate');await controls.nth(1).selectOption('it');assert.equal(await controls.nth(2).inputValue(),'sl');
  await page.locator('.document-options button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('[data-document-detail]')?.textContent.includes('Predlog: Čebelica.'));
  const section=page.locator('.document-section');assert.equal(await section.locator('pre').textContent(),'Čebelica 🧑‍🏫 in človek. <script>window.pwned=true</script>');
  assert.equal(await section.locator('div.document-text').textContent(),'Predlog: Čebelica.');
  assert.equal(await page.evaluate(()=>window.pwned),undefined);assert.equal(await page.locator('.document-section script').count(),0);
  await section.locator('button').click();assert.equal(await page.evaluate(()=>window.sourceOpened),'family-1');
  const start=await page.evaluate(()=>window.calls.find(c=>c.path.endsWith('/start')).body);
  assert.deepEqual({mode:start.mode,source:start.source_language,target:start.target_language},{mode:'translate',source:'it',target:'sl'});
  const download=page.waitForEvent('download');await page.getByRole('button',{name:locale==='sl'?'Prenesite poročilo TXT':'Scarica rapporto TXT',exact:true}).click();assert.equal((await download).suggestedFilename(),'klikai-report.txt');
  await page.evaluate(()=>window.showReplayWarning());
  await page.waitForFunction(()=>document.querySelector('[data-document-detail]')?.textContent.includes('Word')&&document.querySelectorAll('.document-actions .secondary').length===0);
  assert((await page.locator('[data-document-detail]').innerText()).includes(locale==='sl'?'Wordov osnutek ni na voljo; preverite poročilo.':'La bozza Word non è disponibile; controlla il rapporto.'));
  await page.evaluate(()=>window.showPartialReview());await page.locator('[data-partial-draft]').waitFor();
  assert((await page.locator('[data-document-detail]').innerText()).includes(locale==='sl'?'Delni osnutek ohrani izvirnik':'La bozza parziale conserva l’originale'));
  const partialDownload=page.waitForEvent('download');await page.locator('[data-partial-draft]').click();
  assert.equal((await partialDownload).suggestedFilename(),'klikai-partial-draft.docx');
  if(process.env.KLIKAI_DOCUMENT_UI_OUTPUT){fs.mkdirSync(process.env.KLIKAI_DOCUMENT_UI_OUTPUT,{recursive:true});await page.locator('#workspace').screenshot({path:path.join(process.env.KLIKAI_DOCUMENT_UI_OUTPUT,`documents-${locale}-${width}.png`)});}
  // A separate private-choice fixture verifies the new download controls. Its
  // fake server never contacts a provider and makes no language-quality claim.
  await page.getByRole('button',{name:locale==='sl'?'Izbrišite dokument in njegove odgovore':'Elimina documento e relative risposte',exact:true}).click();
  await page.evaluate(()=>window.ui.session({documents:{enabled:true,private_translation:true}}));
  await page.locator('#workspace>details>summary').click();
  await page.locator('input[type=file]').setInputFiles({name:'plain.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from('private plain document fixture')});
  await page.locator('#workspace form button[type=submit]').first().click();
  await page.locator('[data-document-detail] h3').waitFor();
  const privateControls=page.locator('.document-options select');
  await privateControls.nth(0).selectOption('translate');await privateControls.nth(1).selectOption('sl');
  assert.equal(await privateControls.nth(2).inputValue(),'it');
  await privateControls.nth(3).selectOption('translategemma_sl_it');
  assert((await page.locator('.document-options').innerText()).includes(locale==='sl'?'Poročilo loči gradivo KLIKai':'Il rapporto distingue il materiale KLIKai'));
  await page.locator('.document-options button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('[data-document-detail]')?.textContent.includes('Bozza italiana sintetica.'));
  assert.equal(await page.locator('[data-native-scope-note]').count(),1);
  assert.equal(await page.locator('.document-section div.document-text').textContent(),'Bozza italiana sintetica.');
  assert(!(await page.locator('[data-document-detail]').innerText()).includes('PRIVATE_REPEATED_'));
  assert((await page.evaluate(()=>window.fixtureSnapshot().sections[0].output)).includes('PRIVATE_REPEATED_SOURCE_NOTE'));
  const privateStart=await page.evaluate(()=>window.calls.filter(c=>c.path.endsWith('/start')).at(-1).body);
  assert.deepEqual({mode:privateStart.mode,source:privateStart.source_language,target:privateStart.target_language,provider:privateStart.translation_provider},{mode:'translate',source:'sl',target:'it',provider:'translategemma_sl_it'});
  assert(!Object.hasOwn(privateStart,'translation_profile'));
  const cleanDownload=page.waitForEvent('download');await page.locator('[data-clean-draft]').click();
  const clean=await cleanDownload;assert.equal(clean.suggestedFilename(),'klikai-draft.txt');
  const stream=await clean.createReadStream(),chunks=[];for await(const chunk of stream)chunks.push(chunk);
  assert.equal(Buffer.concat(chunks).toString('utf8'),'Bozza italiana sintetica.');
  assert.equal(await page.evaluate(()=>window.calls.filter(c=>c.path.endsWith('/draft/txt')).length),1);
  const simpleWordDownload=page.waitForEvent('download');await page.getByRole('button',{name:locale==='sl'?'Prenesite osnutek Word':'Scarica bozza Word',exact:true}).click();
  assert.equal((await simpleWordDownload).suggestedFilename(),'klikai-draft.docx');
  if(process.env.KLIKAI_DOCUMENT_UI_OUTPUT)await page.locator('#workspace').screenshot({path:path.join(process.env.KLIKAI_DOCUMENT_UI_OUTPUT,`documents-private-sl-it-${locale}-${width}.png`)});
  await page.evaluate(()=>window.showGuidedNative());
  await page.waitForFunction(()=>document.querySelector('[data-native-scope-note]')?.textContent.includes('KLIKai:'));
  assert.equal(await page.locator('[data-native-scope-note]').count(),1);
  assert((await page.locator('[data-native-scope-note]').innerText()).includes(locale==='sl'?'poročilo pokaže':'il rapporto mostra'));
  assert.equal(await page.locator('.document-section div.document-text').textContent(),'Bozza italiana sintetica.');
  if(process.env.KLIKAI_DOCUMENT_UI_OUTPUT)await page.locator('#workspace').screenshot({path:path.join(process.env.KLIKAI_DOCUMENT_UI_OUTPUT,`documents-guided-sl-it-${locale}-${width}.png`)});
  await page.evaluate(()=>window.showNativeFailure());
  await page.waitForFunction(()=>document.querySelector('.document-section div.document-text')?.textContent==='Incomplete provider operation retained.');
  assert.equal(await page.locator('[data-native-scope-note]').count(),1);
  assert.equal(await page.locator('[data-clean-draft]').count(),0);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
  await page.evaluate(()=>window.ui.reset());assert(await page.locator('#workspace').isHidden());assert.equal(await page.locator('#workspace').textContent(),'');
  checks.push({locale,width,status:'PASS',upload_preview:true,explicit_direction:true,progress:true,unicode_and_html_text_preserved:true,source_navigation:true,report_download:true,replayed_translation_blocks_word:true,private_sl_it_choice:true,browser_does_not_author_profile:true,clean_translation_txt_download:true,simple_word_download:true,native_scope_note_once:true,accepted_native_proposal_only:true,retained_raw_output_unchanged:true,failed_native_output_preserved:true,ordinary_output_unchanged:true,logout_clears_text:true});await context.close();
 }}finally{await browser.close();}
 console.log(JSON.stringify({status:'PASS',scope:'UI protocol fixtures only; no source or model calls',checks}));
})().catch(error=>{console.error(error.stack);process.exit(1);});
