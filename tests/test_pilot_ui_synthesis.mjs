import test from 'node:test';
import assert from 'node:assert/strict';
import {paragraphGroups,blockTextPresentation,anchorForText} from '../pilot_ui/core.mjs';

test('one connected paragraph preserves distinct citations and feedback anchors',()=>{
  const blocks=[{block_id:'C1',paragraph_id:'P1',text:'Prva trditev.',evidence_ids:['a']},
    {block_id:'C2',paragraph_id:'P1',text:'Druga trditev.',evidence_ids:['b']},
    {block_id:'C3',paragraph_id:'P2',text:'Omejitev.',evidence_ids:['b']}];
  const groups=paragraphGroups(blocks);
  assert.deepEqual(groups.map(g=>g.blocks.length),[2,1]);
  const tier={generation_status:'generated',evidence:[{evidence_id:'a',local_number:1},{evidence_id:'b',local_number:2}]};
  assert.deepEqual(groups[0].blocks.map(b=>blockTextPresentation(tier,b).separateCitations.map(c=>c.number)),[[1],[2]]);
  assert.deepEqual(anchorForText('C2',blocks[1].text,0,5),{block_id:'C2',start:0,end:5,quote:'Druga',prefix:'',suffix:' trditev.'});
});

test('old and noncontiguous blocks do not silently become one paragraph',()=>{
  assert.equal(paragraphGroups([{block_id:'C1',text:'One'},{block_id:'C2',text:'Two'}]).length,2);
  assert.equal(paragraphGroups(['P1','P2','P1'].map(paragraph_id=>({paragraph_id,text:'x'}))).length,3);
  assert.equal(paragraphGroups([{paragraph_id:'<script>',text:'x'},{paragraph_id:'<script>',text:'y'}]).length,2);
});
