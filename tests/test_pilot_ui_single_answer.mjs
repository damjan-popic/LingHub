import test from 'node:test';
import assert from 'node:assert/strict';
import {ordinaryFamilyView,tierBlocks} from '../pilot_ui/core.mjs';
const run=(id,tier,state='complete')=>({run_id:id,state,source_tier:tier,short_answer:'Short '+id,answer_md:'Full '+id,evidence:[{evidence_id:id}]});
test('single answer retains actual source profile without mutating compatibility aliases',()=>{
 const answer=run('new','answer'),family={answer_format:'single',answer,short:answer,long:null};const before=JSON.stringify(family);
 const view=ordinaryFamilyView(family);assert.equal(view.short.run_id,'new');assert.equal(view.short.source_tier,'answer');assert.equal(view.long,null);assert.equal(JSON.stringify(family),before);
 assert.deepEqual(tierBlocks(view.short),[{block_id:null,text:'Full new'}]);assert.equal(ordinaryFamilyView(view),view);
});
test('historical complete long is the single displayed answer with exact source and feedback identity',()=>{
 const family={short:run('old-short','short'),long:run('old-long','long')},before=JSON.stringify(family),view=ordinaryFamilyView(family);
 assert.equal(view.short.run_id,'old-long');assert.equal(view.short.source_tier,'long');assert.deepEqual(view.short.evidence,[{evidence_id:'old-long'}]);assert.deepEqual(tierBlocks(view.short),[{block_id:null,text:'Full old-long'}]);assert.equal(view.long,null);assert.equal(JSON.stringify(family),before);
});
for(const state of ['queued','running','failed','cancelled'])test('unfinished historical long does not displace saved answer: '+state,()=>{
 const view=ordinaryFamilyView({short:run('saved','short'),long:run('other','long',state)});assert.equal(view.short.run_id,'saved');assert.equal(view.short.source_tier,'short');
});
