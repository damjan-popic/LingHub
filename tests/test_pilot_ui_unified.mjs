import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {progressLabel,matchGroups,settledMatchUsage,matchUseLabel,modelChoiceKey,modelAvailabilityKey,savedProvider,generationNotice,answerNotes,sourceStatusNotes,blockTextPresentation} from '../pilot_ui/core.mjs';

test('normal question selectors are optional and default to reference and offer automatic or exact own-model choices',()=>{
  const html=fs.readFileSync(new URL('../pilot_ui/index.html',import.meta.url),'utf8');
  const options=html.slice(html.indexOf('<div id="options"'),html.indexOf('</form>',html.indexOf('<div id="options"')));
  assert(options.startsWith('<div id="options" class="composer-options" hidden>'));
  for(const name of ['model','task','input-language'])assert(options.includes('id="'+name+'"'),name);
  assert.match(options,/<select id="task"><option value="reference"/);
  assert.match(options,/<select id="model"><option value="auto"/);
  for(const value of ['qwen','gemma'])assert(options.includes('value="'+value+'"'));
  assert(options.includes('Qwen3.8-27B')&&options.includes('Gemma-4-31B-it'));
  assert.equal((html.match(/id="question"/g)||[]).length,1);
});
test('public progress distinguishes retrieval from generation without inventing streaming',()=>{
  assert.equal(progressLabel({state:'queued',progress:{stage:'generation'}},'reference'),'queued');
  assert.equal(progressLabel({state:'running',progress:{stage:'retrieval'}},'reference'),'retrieving_sources');
  assert.equal(progressLabel({state:'running',progress:{stage:'generation'}},'reference'),'generating_grounded');
  assert.equal(progressLabel({state:'running'},'jezikovna_posta'),'deterministic_working');
  assert.equal(progressLabel({state:'running',progress:{stage:'validation'}},'reference'),'validating');
});
test('optional model readiness distinguishes shared GPU, own CPU and bounded loading',()=>{
  assert.equal(modelChoiceKey('auto'),'model_auto');
  assert.equal(modelChoiceKey('qwen'),'model_qwen');
  assert.equal(modelChoiceKey('gemma'),'model_gemma');
  assert.equal(modelAvailabilityKey({available:true,status:'SHARED_READY',device:'gpu'}),'model_shared_ready');
  assert.equal(modelAvailabilityKey({available:true,status:'CPU_READY',device:'cpu'}),'model_cpu_ready');
  assert.equal(modelAvailabilityKey({available:true,status:'CPU_LOADING',device:'cpu'}),'model_loading');
  assert.equal(modelAvailabilityKey({available:true,status:'CPU_READY',device:'cpu',cpu:{loaded:false,availability:'lazy'}}),'model_cpu_lazy');
  assert.equal(modelAvailabilityKey({available:false,status:'CPU_BUSY',device:'cpu'}),'model_queue_busy');
  assert.equal(modelAvailabilityKey({available:false,status:'GENERATION_WAITING_CAPACITY'}),'model_not_available');
});
test('answer provider labels come only from the saved actual result, not its selected alias',()=>{
  assert.deepEqual(savedProvider({model:'auto'}),{model:'',backend:null,reason:null});
  assert.deepEqual(savedProvider({model:'qwen'}),{model:'',backend:null,reason:null});
  const saved={model:'auto',generation:{provider:{model_id:'synthetic-shared-model',device:'gpu',provider_scope:'shared',route_reason:'shared_ready'}}};
  assert.deepEqual(savedProvider(saved),{model:'synthetic-shared-model',backend:'backend_shared_gpu',reason:'route_shared_ready'});
  assert.deepEqual(savedProvider({model:'gemma',generation:{provider:{repository:'synthetic-own-gemma',device:'cpu',provider_scope:'own',route_reason:'own_cpu_fallback'}}}),{model:'synthetic-own-gemma',backend:'backend_cpu',reason:'route_own_cpu_fallback'});
  assert.equal(savedProvider({model:'auto',execution:{device:'gpu',route_reason:'PRIVATE_ENDPOINT'},model_identity:{repository:'synthetic'}}).reason,null);
});
test('cancellation distinguishes requested stage boundary from completed cancellation',()=>{
  assert.equal(progressLabel({state:'running',cancel_requested:true,cancellation_mode:'stop_before_generation',progress:{stage:'retrieval'}},'reference'),'cancelling_before_generation');
  assert.equal(progressLabel({state:'running',cancel_requested:true,cancellation_mode:'finish_current_operation'},'reference'),'cancelling_current_operation');
  assert.equal(progressLabel({state:'cancelled',cancel_requested:true},'reference'),'cancelled');
});
test('early matches remain undecided and final usage updates all loaded pages without replacing captures',()=>{
  const items=[{evidence_id:'first',snippet:'Frozen first',used_in_answer:false},{evidence_id:'page-two',snippet:'Frozen second',used_in_answer:false}];
  const pending={short:{state:'running'}};
  assert.equal(matchUseLabel(items[0],true),'match_pending');
  assert.equal(settledMatchUsage(items,pending),items);
  const final=settledMatchUsage(items,{short:{state:'complete',evidence:[{evidence_id:'page-two'}]}});
  assert.deepEqual(final.map(item=>[item.evidence_id,item.snippet,item.used_in_answer]),[['first','Frozen first',false],['page-two','Frozen second',true]]);
  assert.equal(items[1].used_in_answer,false);
  assert.equal(matchUseLabel(final[1],false),'match_used');
  assert.equal(matchUseLabel(final[0],false),'match_other');
  assert(settledMatchUsage(final,{short:{state:'cancelled'}}).every(item=>item.used_in_answer===false));
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
  assert.equal(generationNotice({generation:{status:'failed',error_code:'ambiguous_remote_generation'}}),'generation_outcome_unknown');
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
test('saved source status and signed dates yield readable notes without rewriting claims or inventing current validity',()=>{
  const draft={evidence_id:'draft',local_number:1,source_slug:'synthetic',external_id:'draft-1',source_status:'osnutek – potreben partnerski/uredniški pregled'},
    history={evidence_id:'history-1',local_number:2,source_slug:'advice',external_id:'historical-record',publication_scope:'individual_advisory_answer',bibliographic:{date:'2026-01-01',signed_date_text:['januar 2014']}};
  const tier={generation_status:'generated',blocks:[{block_id:'C1',text:'Exact answer.',evidence_ids:['draft','history-1']}],evidence:[draft,history,{...history,evidence_id:'history-2',local_number:3}],limitations:[{code:'unsupported_inference',scope:'claim',claim_ids:['C1']}]};
  const before=JSON.stringify(tier),notes=answerNotes(tier);
  assert.deepEqual(notes.map(note=>note.key),['limitation_unsupported_inference','source_status_note','source_signed_date_note']);
  assert.deepEqual(notes[2].values,{date:'januar 2014'});assert.deepEqual(notes[2].evidence,[history]);
  assert.equal(JSON.stringify(tier),before);
  assert.deepEqual(sourceStatusNotes({...history,bibliographic:{date:'2026-01-01'}}),[],'An upload date does not establish the answer date');
  assert.deepEqual(sourceStatusNotes({snippet:'This quoted source says osnutek and januar 2014.'}),[],'Do not infer metadata from source prose');
});
test('explicit limitations are shown once and supersede matching source status notes only for their own cited records',()=>{
  const proposal={evidence_id:'proposal',local_number:1,source_slug:'dictionary',external_id:'entry',source_status:'Predlog',annual_scope:true};
  const tier={generation_status:'generated',blocks:[{block_id:'C1',text:'Exact answer.',evidence_ids:['proposal']}],evidence:[proposal],limitations:[{code:'source_scope',message:'Annual addition, not the entire dictionary.',scope:'claim',claim_ids:['C1']},{code:'proposal_status',message:'This entry is a proposal.',scope:'claim',claim_ids:['C1']},{code:'proposal_status',message:'This entry is a proposal.',scope:'claim',claim_ids:['C1']},{code:'unsupported_inference',scope:'claim',claim_ids:['WITHHELD']}]};
  assert.deepEqual(answerNotes(tier).map(note=>note.text),['Annual addition, not the entire dictionary.','This entry is a proposal.']);
  assert.deepEqual(answerNotes({blocks:[{block_id:'status',text:'No eligible support.'}],limitations:['No eligible support.'],evidence:[proposal]}),[],'No duplicate fallback and no status from incidental hits');
  const unrelated={...proposal,evidence_id:'other',external_id:'other-entry'};
  assert.equal(answerNotes({...tier,evidence:[proposal,unrelated]}).filter(note=>note.key==='source_proposal_note').length,1);
});
