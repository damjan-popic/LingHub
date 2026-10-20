import test from 'node:test';
import assert from 'node:assert/strict';
import {submissionJournal,pollDecision} from '../pilot_ui/submission.mjs';

test('retrying a lost POST preserves key across reload, deliberate repeat gets new key',()=>{
  const rows=new Map(),storage={getItem:k=>rows.get(k),setItem:(k,v)=>rows.set(k,v),removeItem:k=>rows.delete(k)};
  let n=0;const uuid=()=> 'submission-'+(++n),journal=submissionJournal(storage,'test');
  const first=journal.prepare('cu',{question:'Kako?',locale:'sl'},uuid);
  const reloaded=submissionJournal(storage,'test');
  assert.deepEqual(reloaded.read('cu').payload,first);
  assert.equal(reloaded.read('slori'),null);
  assert.deepEqual(reloaded.prepare('cu',{locale:'sl',question:'Kako?'},uuid),first);
  reloaded.clear();
  assert.notEqual(reloaded.prepare('cu',{question:'Kako?',locale:'sl'},uuid).idempotency_key,first.idempotency_key);
});
test('changing payload cannot reuse key, tabs remain independent, storage holds no session',()=>{
  const rows=new Map(),storage={getItem:k=>rows.get(k),setItem:(k,v)=>rows.set(k,v),removeItem:k=>rows.delete(k)};
  let n=0;const j=submissionJournal(storage,'test'),uuid=()=>String(++n);
  const a=j.prepare('cu',{question:'Kaj je SCIA?'},uuid),b=j.prepare('cu',{question:'Kako se prevede?',parent_run_id:'run-1'},uuid);
  assert.notEqual(a.idempotency_key,b.idempotency_key);
  assert.deepEqual(Object.keys(j.read('cu')).sort(),['owner','payload']);
});
test('polling stops after repeated network failure or one hour; reopening can check existing job',()=>{
  assert.equal(pollDecision({failures:4,started:0,now:20000}),'continue');
  assert.equal(pollDecision({failures:5,started:0,now:20000}),'pause');
  assert.equal(pollDecision({failures:0,started:0,now:3600000}),'pause');
});
