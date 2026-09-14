import test from 'node:test';
import assert from 'node:assert/strict';
import {blockTextPresentation,sourceExcerptPresentation,safeSourceLink} from '../pilot_ui/core.mjs';

test('complete editorial record keeps literal question and entire qualified answer',()=>{
  const question='Original <img src=x onerror=alert(1)> question [1]?';
  const answer='Original answer. '+ 'context '.repeat(150)+'\n\nOnly under the final condition. [2]';
  const block={block_id:'editorial-record-0',text:question+'\n\n'+answer};
  const tier={deterministic_result:{task:'editorial_search',presentation:'records',complete_qa_available:true},evidence:[{evidence_id:'owned-capture'}]};
  const view=blockTextPresentation(tier,block);
  assert.deepEqual(view.parts,[{text:block.text}]);
  assert.equal(view.separateCitation.evidence.evidence_id,'owned-capture');
  const drawer={source_label:'Jezikovna pošta / Druga pomoč',excerpt:block.text};
  assert.equal(sourceExcerptPresentation(drawer).text,block.text);
  assert.equal(safeSourceLink('javascript:alert(1)'),null);
});
