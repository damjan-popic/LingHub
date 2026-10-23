import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {answerNotes,blockTextPresentation} from '../pilot_ui/core.mjs';
import {answerCopyText} from '../pilot_ui/interface-details.mjs';

const base={generation_status:'generated',blocks:[{block_id:'C1',text:'Supported part.',evidence_ids:['cited']}],
  evidence:[{evidence_id:'cited',title:'Cited title',local_number:1}],
  decision_sources:[{evidence_id:'explanation',title:'Unabridged source explanation'},
                    {evidence_id:'explanation',title:'Duplicate'},{evidence_id:'cited',title:'Already cited'}]};
for(const code of ['comparison_coverage_incomplete','comparison_explanation_incomplete'])test(code+' shows separate original-reader references',()=>{
  const tier={...base,limitations:[{code,scope:'answer',claim_ids:[]}]},before=JSON.stringify(tier);
  const notes=answerNotes(tier);
  assert.deepEqual(notes[0].decisionSources,[{evidence_id:'explanation',title:'Unabridged source explanation'}]);
  assert.equal(notes[0].key,'limitation_'+code);
  assert.deepEqual(blockTextPresentation(tier,tier.blocks[0]).separateCitations.map(c=>c.evidence.evidence_id),['cited']);
  assert(!answerCopyText('Supported part.',tier,item=>item.title,'Sources').includes('Unabridged'));
  assert.equal(JSON.stringify(tier),before);
  for(const locale of ['sl','it']){const labels=JSON.parse(fs.readFileSync(new URL('../pilot_ui/i18n/'+locale+'.json',import.meta.url)));
    assert(labels[notes[0].key]);assert(labels.comparison_original_link.includes('{title}'));
    assert.notEqual(labels[notes[0].key],labels.limitation_no_answer_evidence);
  }
});

test('original links do not become incidental support or appear without a scoped limitation',()=>{
  assert.equal(answerNotes(base).length,0);
  assert(!answerNotes({...base,limitations:[{code:'source_scope',scope:'answer',claim_ids:[]}]}).some(n=>n.decisionSources));
  const tier={...base,limitations:[{code:'comparison_explanation_incomplete',scope:'claim',claim_ids:['omitted']} ]};
  assert(!answerNotes(tier).some(n=>n.decisionSources));
});
