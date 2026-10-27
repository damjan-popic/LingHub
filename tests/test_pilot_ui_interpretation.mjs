import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {answerNotes,noAnswerFeedback} from '../pilot_ui/core.mjs';
import {answerCopyText} from '../pilot_ui/interface-details.mjs';

test('source-present interpretation blockers show one notice without citation laundering',()=>{
  const tier={status:'validated',answer_kind:'source_only',generation_status:'returned_processing_limitation',
    evidence_state:{status:'source_present_unresolved_interpretation'},
    blocks:[{block_id:'notice',text:'Source excerpt remains available.'}],
    evidence:[{citation_id:'source',source_slug:'advice',external_id:'record-1',title:'Original source'},
      {citation_id:'source-second-span',source_slug:'advice',external_id:'record-1',title:'Same original'},
      {citation_id:'other',source_slug:'dictionary',external_id:'record-2',title:'Other original'}],
    limitations:[{code:'unsupported_interpretation',scope:'answer',claim_ids:[]}]};
  const before=JSON.stringify(tier),notes=answerNotes(tier);
  assert.equal(noAnswerFeedback(tier),false);
  assert.deepEqual(notes,[],'The answer block is already the one processing notice');
  assert.equal(answerCopyText('Notice',tier,item=>item.source_slug,'Sources'),'Notice');
  const noteOnly=answerNotes({...tier,blocks:[]});
  assert.deepEqual(noteOnly.map(note=>[note.key,note.evidence]),[
    ['limitation_unsupported_interpretation',[]]
  ],'A standalone processing note does not cite unrelated retrieved records');
  const substantive=answerNotes({...tier,answer_kind:'generated',generation_status:'generated',
    blocks:[{block_id:'claim',text:'Supported bounded answer.',evidence_ids:['source']}]});
  assert.deepEqual(substantive.map(note=>[note.key,note.evidence.map(item=>item.citation_id)]),[
    ['limitation_unsupported_interpretation',['source']]
  ],'A substantive answer keeps its interpretation qualification and exact supporting source');
  assert.equal(JSON.stringify(tier),before);
  assert.equal(answerNotes({...tier,limitations:[{code:'arbitrary_private_code',scope:'answer',claim_ids:[]}]}).length,0);
  for(const malformed of [
    {...tier,evidence_state:{status:'source_present_not_admitted_for_answer'}},
    {...tier,evidence_state:{status:'no_selected_evidence'}},
    {...tier,evidence_state:{status:'arbitrary_private_state'}},
    {...tier,evidence:[]},
    {...tier,limitations:[{code:'unsupported_interpretation',scope:'claim',claim_ids:['notice']}]},
  ])assert.equal(answerNotes(malformed).length,0);
});

test('genuine empty evidence requests feedback without conflating other failures',()=>{
  assert.equal(noAnswerFeedback({status:'no_hit',generation_status:'returned_abstention',
    evidence_state:{status:'no_selected_evidence'},evidence:[]}),true);
  for(const tier of [
    {status:'validated',generation_status:'returned_processing_limitation',evidence_state:{status:'source_present_unresolved_interpretation'}},
    {status:'validated',generation_status:'returned_abstention',evidence_state:{status:'source_present_not_admitted_for_answer'}},
    {status:'validated',generation:{status:'no_support'},evidence_state:{status:'source_present_not_admitted_for_answer'}},
    {status:'failed',generation_status:'failed',generation:{status:'failed'}},
    {status:'no_hit',generation_status:'returned_abstention',evidence_state:{status:'source_present_unresolved_interpretation'}},
    {status:'no_hit',generation_status:'returned_abstention',evidence_state:{status:'no_selected_evidence'},generation:{status:'failed'}},
  ])assert.equal(noAnswerFeedback(tier),false);
});

test('Slovene and Italian interpretation labels are readable and the note region is named',()=>{
  for(const locale of ['sl','it']){
    const labels=JSON.parse(fs.readFileSync(new URL('../pilot_ui/i18n/'+locale+'.json',import.meta.url)));
    const text=labels.limitation_unsupported_interpretation;
    assert.equal(typeof text,'string');assert(text.length>70);assert(!/[<>{}]/.test(text));assert(text.endsWith('.'));
    assert(!/odlomk|passagg/i.test(text),'A processing failure does not promise excerpts or cited passages');
  }
  const source=fs.readFileSync(new URL('../pilot_ui/pilot.mjs',import.meta.url),'utf8');
  assert.match(source,/class:'answer-notes',lang:state\.locale,'aria-label':t\('answer_notes'\)/);
  assert.match(source,/evidence\.local_number\|\|evidence\.number\|\|evidence\.note_number/);
});

test('opaque provenance bindings are never rendered and logout clears source-reader state',()=>{
  const token='psb1.private-opaque-binding';
  const tier={blocks:[{block_id:'C1',text:'Bound answer.',evidence_ids:['source-1']}],
    evidence:[{evidence_id:'source-1',source_slug:'advice',title:'Visible source',
      local_number:1,source_binding:token}]};
  const copied=answerCopyText('Bound answer [1]',tier,item=>item.source_slug,'Sources');
  assert(!copied.includes(token));
  const source=fs.readFileSync(new URL('../pilot_ui/pilot.mjs',import.meta.url),'utf8');
  assert(!source.includes('source_binding'),'The opaque verifier field is not UI content');
  const reset=source.slice(source.indexOf('function resetPrivate()'),
    source.indexOf('function authenticated('));
  assert.match(reset,/sourceRequest\+\+/);
  assert.match(reset,/state\.family=null/);
  assert.match(reset,/clear\(\$\('source-content'\)\)/);
  assert.match(reset,/dialog\[open\].*\.close\(\)/);
  const logout=source.slice(source.indexOf("$('beta-logout')"));
  assert.match(logout,/api\('\/logout'.*resetPrivate\(\)/s);
});
