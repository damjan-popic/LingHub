import test from 'node:test';
import assert from 'node:assert/strict';
import {toCodePoints,toUtf16,exactSlice,alignSpans} from '../workbench/coordinates.js';
test('original Unicode selection round-trips emoji, combining marks, repetitions and newlines',()=>{
  const text='🙂 otrok otrok, e\u0301\nTrst.';
  for(let i=0;i<=Array.from(text).length;i++)assert.equal(toCodePoints(text,toUtf16(text,i)),i);
  assert.equal(exactSlice(text,2,7),'otrok');
  assert.equal(exactSlice(text,8,13),'otrok');
  assert.equal(exactSlice(text,15,17),'e\u0301');
});
test('half a surrogate and invalid offsets fail instead of inventing spans',()=>{
  assert.throws(()=>toCodePoints('🙂',1));assert.throws(()=>toUtf16('a',2));assert.throws(()=>toCodePoints('a',-1));
});
test('split merge and unmatched comparisons remain explicit',()=>{
  const left=[{start:0,end:4},{start:5,end:9}],right=[{start:0,end:2},{start:2,end:4},{start:11,end:12}];
  const rows=alignSpans(left,right);assert.deepEqual(rows.map(r=>r.mapping),['split_merge','unmatched','unmatched']);
  assert.equal(rows[0].right.length,2);
});
