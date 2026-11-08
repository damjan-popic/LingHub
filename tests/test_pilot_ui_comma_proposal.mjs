import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {commaConsentFields,commaTermsLink,commaProposalView,commaExplanationLinks,
  commaExplanationHeading,renderCommaProposal} from '../pilot_ui/comma-proposal.mjs';
import {errorKey} from '../pilot_ui/core.mjs';

const messages=JSON.parse(fs.readFileSync(new URL('../pilot_ui/i18n/sl.json',import.meta.url)));
const t=(key,values={})=>(messages[key]||key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??''));
const element=(tag,text,attrs={})=>({tag,text:text??'',attrs,children:[],events:{},hidden:false,
  append(...items){this.children.push(...items);},replaceChildren(...items){this.children=items;},
  addEventListener(name,handler){this.events[name]=handler;},
  set textContent(value){this.text=value;},get textContent(){return this.text;},
  set innerHTML(value){throw Error('Provider HTML must not execute');}});
const nodes=root=>[root,...root.children.flatMap(nodes)];
const text=root=>nodes(root).map(node=>node.text).join('\n');
const proposal=(original,proposed,edits=[])=>({status:'ok',original_text:original,
  proposed_text:proposed,edits,offset_convention:'unicode_codepoint_half_open',
  checked_extent:{start:0,end:Array.from(original).length,complete:true},
  tool_execution:{neural_prediction_received:true,model_version:null}});

test('disagreement stays visible even when the proposed edit misses the required boundary',()=>{
  const saved=proposal('Če ne pride ne grem.','Če ne pride ne, grem.',[
    {start:14,end:14,replacement:',',kind:'insert'}]);
  saved.explanation_disagreement=true;saved.explanation_links=[];
  const before=JSON.stringify(saved),root=element('section');
  renderCommaProposal(root,saved,{element,translate:t,copyText:async()=>{}});
  assert(text(root).includes(messages.comma_explanation_disagreement));
  assert.equal(JSON.stringify(saved),before);
  assert(text(root).includes(saved.original_text)&&text(root).includes(saved.proposed_text));
});

test('consent is explicit, defaults false and adds no field when the feature is absent',()=>{
  const enabled={comma_checker:{configured:true}};
  assert.deepEqual(commaConsentFields({},true),{});
  assert.deepEqual(commaConsentFields({comma_checker:{configured:false}},true),{});
  assert.deepEqual(commaConsentFields(enabled,undefined),{comma_checker_consent:false});
  assert.deepEqual(commaConsentFields(enabled,false),{comma_checker_consent:false});
  assert.deepEqual(commaConsentFields(enabled,true),{comma_checker_consent:true});
  assert.deepEqual(commaConsentFields(enabled,'true'),{comma_checker_consent:false});
});

test('CJVT terms links use explicit public HTTPS hosts without widening source links',()=>{
  assert.equal(commaTermsLink('https://orodja.cjvt.si/vejice/about'),'https://orodja.cjvt.si/vejice/about');
  for(const value of ['javascript:alert(1)','http://orodja.cjvt.si/','https://secret@orodja.cjvt.si/',
    'https://127.0.0.1/','https://orodja.cjvt.si.evil.invalid/'])assert.equal(commaTermsLink(value),null);
});

test('comma additions and removals keep exact Unicode, decimal commas and line endings',()=>{
  const original='🙂 Ko pridem, domov bom poklical.\r\n3,14 in otrok otrok.';
  const proposed='🙂 Ko pridem domov, bom poklical.\r\n3,14 in otrok otrok.';
  const saved=proposal(original,proposed,[
    {start:18,end:18,replacement:',',kind:'insert'},
    {start:11,end:12,replacement:'',kind:'remove'}]);
  const before=JSON.stringify(saved),view=commaProposalView(saved);
  assert.equal(view.status,'ok');assert.equal(view.proposed,proposed);
  assert.equal(view.parts.filter(p=>p.kind!=='insert').map(p=>p.text).join(''),original);
  assert.equal(view.parts.filter(p=>p.kind!=='remove').map(p=>p.text).join(''),proposed);
  assert.deepEqual([view.added,view.removed],[1,1]);
  assert.equal(JSON.stringify(saved),before,'Opening history must not mutate the stored result');
});

test('repeated substrings use their exact occurrence, including combining marks',()=>{
  const original='e\u0301 otrok otrok ko pride';
  const proposed='e\u0301 otrok otrok, ko pride';
  const view=commaProposalView(proposal(original,proposed,[
    {start:14,end:14,replacement:',',kind:'insert'}]));
  assert.equal(view.status,'ok');assert.equal(view.proposed,proposed);
  assert.equal(view.parts[0].text,'e\u0301 otrok otrok');
});

test('the saved proposal renders literal markup safely and copies only on an explicit click',async()=>{
  const original='<img src=x onerror=steal()> Ko pridem domov bom poklical.';
  const at=Array.from(original.slice(0,original.indexOf(' bom'))).length;
  const proposed=original.slice(0,original.indexOf(' bom'))+','+original.slice(original.indexOf(' bom'));
  const saved=proposal(original,proposed,[{start:at,end:at,replacement:',',kind:'insert'}]);
  let copied=[];const root=element('section');
  const options={element,translate:t,copyText:async value=>{copied.push(value);}};
  renderCommaProposal(root,saved,options);
  assert.equal(root.hidden,false);assert.equal(nodes(root).filter(n=>n.tag==='ins').length,1);
  assert.equal(nodes(root).filter(n=>n.tag==='img').length,0);
  assert(nodes(root).some(n=>n.text===original));assert(nodes(root).some(n=>n.text===proposed));
  assert(text(root).includes('Samodejni predlog'));assert(!text(root).includes('94%'));
  assert(!text(root).includes('ločena'));
  assert.deepEqual(copied,[],'Showing a result must not copy, apply or dispatch it');
  await nodes(root).find(n=>n.tag==='button').events.click();
  assert.deepEqual(copied,[proposed]);
  renderCommaProposal(root,saved,options);
  assert.deepEqual(copied,[proposed],'Reopening history only renders the saved observation');
});

test('unchanged text is a no-change proposal, not a correctness certificate',()=>{
  const original='Vrednost je 3,14.\n"Otrok, otrok."';const root=element('section');
  renderCommaProposal(root,proposal(original,original),{element,translate:t,copyText:async()=>{}});
  assert(text(root).includes('Orodje ni predlagalo sprememb.'));
  assert(!/zagotovo pravilno|brez napak|brez modela|brez inference/.test(text(root)));
  assert.equal(nodes(root).filter(n=>n.tag==='ins'||n.tag==='del').length,0);
});

const explanationFixture=()=>{
  const saved=proposal('Ko pridem domov bom poklical.','Ko pridem domov, bom poklical.',[
    {start:15,end:15,replacement:',',kind:'insert'}]);
  const link={edit_index:0,edit_start:15,edit_end:15,claim_id:'C1',block_id:'B1',unit_id:'U1',
    relation:'supports_proposal',citation_indexes:[0],evidence_ids:['SP1']};
  saved.explanation_status='applied';saved.explanation_links=[link];
  return {saved,link,blocks:[{block_id:'B1',text:'Bounded source explanation.',evidence_ids:['SP1']}],
    citations:[{claim_id:'C1',block_id:'B1',unit_id:'U1',evidence_id:'SP1'}]};
};

test('explicit edit links navigate to the existing claim without making a tool or source call',()=>{
  const {saved,link,blocks,citations}=explanationFixture(),root=element('section'),opened=[];
  const before=JSON.stringify(saved);
  assert.deepEqual(commaExplanationLinks(saved,blocks,citations),[link]);
  renderCommaProposal(root,saved,{element,translate:t,copyText:async()=>{},blocks,citations,
    openExplanation:value=>opened.push(value)});
  const button=nodes(root).find(node=>node.attrs.class==='plain comma-explanation-link');
  assert.equal(button.text,'Razlaga spremembe 1');assert.deepEqual(opened,[]);
  button.events.click();assert.deepEqual(opened,[link]);
  assert.equal(JSON.stringify(saved),before);
  assert(nodes(root).some(node=>node.tag==='ins'&&node.attrs.title==='Sprememba 1'));
  delete saved.explanation_links;
  renderCommaProposal(root,saved,{element,translate:t,copyText:async()=>{},blocks,citations});
  assert(!nodes(root).some(node=>node.attrs.class==='plain comma-explanation-link'),
    'Nearby citations alone cannot create an edit-support link');
});

test('unmatched edit, block, claim, unit and citation identities never acquire navigation',()=>{
  const {saved,link,blocks,citations}=explanationFixture();
  for(const damage of [{edit_index:1},{edit_start:14},{block_id:'unknown'},
    {claim_id:'unrelated'},{unit_id:'different'},{citation_indexes:[1]},
    {evidence_ids:['unrelated']},{relation:'generic_related_source'}]){
    const changed={...saved,explanation_links:[{...link,...damage}]};
    assert.deepEqual(commaExplanationLinks(changed,blocks,citations),[]);
  }
  for(const relation of ['supports_proposal','disagrees_with_proposal','conditional_support']){
    const root=element('section');renderCommaProposal(root,{...saved,explanation_links:[{...link,relation}]},
      {element,translate:t,copyText:async()=>{},blocks,citations});
    assert(text(root).includes(t('comma_link_'+relation,{number:1})));
  }
});

test('related guidance and partially explained edits retain their actual scope',()=>{
  const {saved,link,blocks,citations}=explanationFixture();
  assert.equal(commaExplanationHeading({...saved,explanation_status:'related_guidance'},citations),
    'comma_related_guidance_heading');
  assert.equal(commaExplanationHeading(saved,citations),'comma_explanation_heading');
  assert.equal(commaExplanationHeading(saved,[]),null);
  const partial=proposal('Ko pridem bom, poklical.','Ko pridem, bom poklical.',[
    {start:9,end:9,replacement:',',kind:'insert'},
    {start:13,end:14,replacement:'',kind:'remove'}]);
  partial.explanation_status='applied';partial.explanation_links=[{...link,edit_start:9,edit_end:9}];
  const root=element('section');renderCommaProposal(root,partial,{element,translate:t,copyText:async()=>{},blocks,citations});
  assert.equal(nodes(root).filter(node=>node.text===messages.comma_partial_explanation).length,1);
  assert.equal(nodes(root).filter(node=>node.attrs.class==='plain comma-explanation-link').length,1);
  assert(!/nepodprt|ni mogoče pojasniti|certifikat/.test(text(root)));
});

test('failed and consent-required checks preserve the preview and offer no proposal copy',()=>{
  for(const status of ['consent_required','target_required','too_long','unavailable','timeout',
    'alignment_failed','malformed_response','unsupported_language','unknown_remote_code']){
    const root=element('section');renderCommaProposal(root,{status,original_text:'Ko pridem bom poklical.',
      message:'<script>private remote error</script>'},{element,translate:t,copyText:async()=>assert.fail()});
    assert(text(root).includes('Ko pridem bom poklical.'));
    assert.equal(nodes(root).filter(n=>n.tag==='button').length,0);
    assert(!text(root).includes('private remote error'));
    assert(!text(root).includes('comma_status_'));
  }
});

test('malformed edit maps preserve the original instead of guessing a replacement',()=>{
  for(const saved of [
    proposal('Ko pridem bom poklical.','Changed words',[{start:9,end:9,replacement:',',kind:'insert'}]),
    proposal('Ko pridem bom poklical.','Ko pridem NE bom poklical.',[{start:9,end:9,replacement:' NE',kind:'insert'}]),
    proposal('🙂 otrok otrok','🙂 otrok, otrok',[{start:9,end:9,replacement:',',kind:'insert'}]),
    proposal('a, b','a b',[{start:1,end:3,replacement:'',kind:'remove'},
      {start:2,end:2,replacement:' ',kind:'insert'}]),
  ]){
    assert.equal(commaProposalView(saved).status,'alignment_failed');
    const root=element('section');renderCommaProposal(root,saved,{element,translate:t,copyText:async()=>assert.fail()});
    assert(nodes(root).some(node=>node.text===saved.original_text));
    assert.equal(nodes(root).filter(n=>n.tag==='button').length,0);
  }
});

test('scope notes, localization and ordinary composer wiring remain explicit',()=>{
  const saved=proposal('Ko pridem bom poklical.','Ko pridem, bom poklical.',[
    {start:9,end:9,replacement:',',kind:'insert'}]);saved.checked_extent.complete=false;
  const root=element('section');renderCommaProposal(root,saved,{element,translate:t,copyText:async()=>{}});
  assert(text(root).includes(messages.comma_partial));
  for(const locale of ['sl','it']){
    const labels=JSON.parse(fs.readFileSync(new URL('../pilot_ui/i18n/'+locale+'.json',import.meta.url)));
    for(const key of Object.keys(messages).filter(key=>key.startsWith('comma_')))assert.equal(typeof labels[key],'string',key);
  }
  const html=fs.readFileSync(new URL('../pilot_ui/index.html',import.meta.url),'utf8');
  const composer=html.match(/<form id="question-form"[\s\S]*?<\/form>/)[0];
  assert(!composer.includes('comma-checker-control'));
  assert(!composer.includes('comma-checker-consent'));
  assert(!composer.includes('comma-checker-terms'));
  assert(!html.match(/<form id="login-form"[\s\S]*?<\/form>/)[0].includes('comma-checker'));
  const answer=html.slice(html.indexOf('id="answer-card"'),html.indexOf('id="beta-explorer"'));
  assert(answer.indexOf('id="comma-proposal"')<answer.indexOf('id="short-answer"'));
  assert(answer.includes('<div id="comma-proposal"'),'The proposal belongs to the one answer section');
  const ui=fs.readFileSync(new URL('../pilot_ui/pilot.mjs',import.meta.url),'utf8');
  assert(!ui.includes('commaConsentFields('));
  assert(!ui.includes('comma-checker-consent'));
  assert.match(ui,/renderCommaSuggestion\(family\.short\)/);
  assert.match(ui,/if\(!blocks\.length\)\{if\(tier\?\.comma_proposal/,
    'A check-only result must not acquire an invented source-unavailable refusal');
  assert(!/comma-checker-consent'\)\.addEventListener/.test(ui),'Checking the box cannot submit or replay a request');
  assert.equal(errorKey('comma_checker_consent_required'),'comma_checker_consent_required');
});
