import assert from 'node:assert/strict';
import {blockTextPresentation,generationNotice,hasSupportedAnswer} from '../pilot_ui/core.mjs';
import {citedEvidenceIds} from '../pilot_ui/source-explorer.mjs';
const tier={state:'complete',answer_kind:'source_only',generation_status:'source_supported_partial',
  blocks:[{block_id:'C1',text:'Exact source text.',evidence_ids:['kept']}],
  evidence:[{evidence_id:'kept',local_number:1}]};
assert.equal(hasSupportedAnswer(tier),true);
assert.equal(generationNotice(tier),'generation_source_excerpt');
assert.equal(blockTextPresentation(tier,tier.blocks[0]).separateCitations[0].evidence.evidence_id,'kept');
assert.deepEqual([...citedEvidenceIds(tier)],['kept']);
assert.equal(hasSupportedAnswer({...tier,generation_status:'not_generated'}),false);
console.log('review excerpt presentation passed');
