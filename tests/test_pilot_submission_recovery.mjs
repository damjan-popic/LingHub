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
test('composer drafts retain exact text and explicit parent per owned answer across reload',()=>{
  const rows=new Map(),storage={getItem:k=>rows.get(k),setItem:(k,v)=>rows.set(k,v),removeItem:k=>rows.delete(k)};
  const journal=submissionJournal(storage,'fixture'),draft={question:'Kaj pa »druga« možnost?\nNov odstavek.',focus:'druga',followup:{run_id:'owned-run',question:'Saved parent?'}};
  journal.saveDraft('owner-a','answer-a',draft);draft.question='Changed outside journal';
  journal.saveDraft('owner-a','answer-b',{question:'Independent draft'});
  const reloaded=submissionJournal(storage,'fixture');
  assert.equal(reloaded.readDraft('owner-a','answer-a').question,'Kaj pa »druga« možnost?\nNov odstavek.');
  assert.equal(reloaded.readDraft('owner-a','answer-a').followup.run_id,'owned-run');
  assert.equal(reloaded.readDraft('owner-a','answer-b').question,'Independent draft');
  assert.equal(reloaded.readDraft('owner-b','answer-a'),null);
  reloaded.clearDraft('owner-a','answer-a');assert.equal(reloaded.readDraft('owner-a','answer-a'),null);
  assert.equal(reloaded.readDraft('owner-a','answer-b').question,'Independent draft');
  reloaded.clearDrafts('owner-a');assert.equal(submissionJournal(storage,'fixture').readDraft('owner-a','answer-b'),null);
});
test('draft isolation and in-tab recovery remain available when browser storage is unavailable',()=>{
  const journal=submissionJournal({getItem(){throw Error('unavailable');},setItem(){throw Error('unavailable');},removeItem(){throw Error('unavailable');}},'fixture');
  journal.saveDraft('owner-a',null,{question:'Unsent text'});
  assert.equal(journal.readDraft('owner-a').question,'Unsent text');assert.equal(journal.readDraft('owner-b'),null);
  journal.clearDrafts('owner-a');assert.equal(journal.readDraft('owner-a'),null);
});
test('pending journal retains its known composer key without changing the API payload',()=>{
  const rows=new Map(),storage={getItem:k=>rows.get(k),setItem:(k,v)=>rows.set(k,v),removeItem:k=>rows.delete(k)};
  const journal=submissionJournal(storage,'fixture'),payload={question:'An owned follow-up',parent_run_id:'owned-run',parent_relation:'followup'};
  const sent=journal.prepare('owner',payload,()=> 'request-1','owned-family');
  assert.equal(journal.read('owner').draft_family,'owned-family');assert.equal('draft_family'in sent,false);
  assert.deepEqual(journal.prepare('owner',payload,()=> 'unused','owned-family'),sent);
});
