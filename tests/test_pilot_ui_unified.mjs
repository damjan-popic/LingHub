import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {progressLabel,matchGroups,generationNotice,blockTextPresentation} from '../pilot_ui/core.mjs';

test('normal question selectors are optional and default to the existing reference/Qwen route',()=>{
  const html=fs.readFileSync(new URL('../pilot_ui/index.html',import.meta.url),'utf8');
  const options=html.slice(html.indexOf('<div id="options"'),html.indexOf('</form>',html.indexOf('<div id="options"')));
  assert(options.startsWith('<div id="options" class="composer-options" hidden>'));
  for(const name of ['model','task','input-language'])assert(options.includes('id="'+name+'"'),name);
  assert.match(options,/<select id="task"><option value="reference"/);
  assert.match(options,/<select id="model"><option value="qwen"/);
  assert.equal((html.match(/id="question"/g)||[]).length,1);
});
test('public progress distinguishes retrieval from generation without inventing streaming',()=>{
  assert.equal(progressLabel({state:'queued',progress:{stage:'generation'}},'reference'),'queued');
  assert.equal(progressLabel({state:'running',progress:{stage:'retrieval'}},'reference'),'retrieving_sources');
  assert.equal(progressLabel({state:'running',progress:{stage:'generation'}},'reference'),'generating_grounded');
  assert.equal(progressLabel({state:'running'},'jezikovna_posta'),'deterministic_working');
});
test('match grouping preserves distinct sources and record identities without inventing used evidence',()=>{
  const record={evidence_id:'owned-a',source_slug:'jezikovna_posta',title:'Title <img onerror=x>',snippet:'Literal [1]',used_in_answer:false};
  const other={...record,evidence_id:'owned-b',source_slug:'solska',used_in_answer:true};
  const input=[record,other,{...record},{source_slug:'solska',title:'Unresolved',snippet:'No identity'}],before=JSON.stringify(input);
  assert.deepEqual(matchGroups(input).map(g=>[g.source_slug,g.records.map(r=>r.evidence_id)]),[['jezikovna_posta',['owned-a']],['solska',['owned-b']]]);
  assert.deepEqual(matchGroups(input,'solska'),[{source_slug:'solska',records:[other]}]);
  assert.equal(JSON.stringify(input),before);assert.equal(record.used_in_answer,false);
});
test('provider failure and no support are not called generated answers',()=>{
  assert.equal(generationNotice({generation:{status:'generated'}}),'generated_grounded');
  for(const status of ['failed','unavailable'])assert.equal(generationNotice({generation:{status}}),'generation_fallback');
  assert.equal(generationNotice({generation:{status:'no_support'}}),'generation_no_support');
  assert.equal(generationNotice({generation_status:'not_generated'}),null);
});
test('only resolved citation numbers acquire source controls',()=>{
  const tier={evidence:[{evidence_id:'owned-used',local_number:1}],generation:{status:'generated'}};
  const block={block_id:'answer',text:'Supported [1]; unknown [77]. <img src=x onerror=alert(1)>'};
  const view=blockTextPresentation(tier,block);
  assert.deepEqual(view.parts.filter(p=>p.evidence).map(p=>p.evidence.evidence_id),['owned-used']);
  assert.equal(view.parts.map(p=>p.text).join(''),block.text);
});
test('validated block evidence IDs attach citations without editing signed generated text',()=>{
  const tier={generation_status:'generated',evidence:[{evidence_id:'used',local_number:2},{evidence_id:'second',local_number:3}]};
  const block={block_id:'claim',text:'Exact signed generated claim.',evidence_ids:['used','invented','used']};
  const before=JSON.stringify(block),view=blockTextPresentation(tier,block);
  assert.equal(view.parts.map(p=>p.text).join(''),block.text);
  assert.deepEqual(view.separateCitations.map(c=>[c.number,c.evidence.evidence_id]),[[2,'used']]);
  assert.equal(JSON.stringify(block),before);
  assert.deepEqual(blockTextPresentation(tier,{...block,text:'Claim [2]',evidence_ids:['used','second']}).separateCitations.map(c=>c.evidence.evidence_id),['second']);
});
