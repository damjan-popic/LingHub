import test from 'node:test';
import assert from 'node:assert/strict';
import {sourcePresentation,legendSources,answerCopyText,longerWaitMessage,releaseLabel} from '../pilot_ui/interface-details.mjs';
import {publicRelease} from '../pilot_ui/public-release.mjs';

const registry={sources:[
  {suggested_key:'collection',labels:{sl:'Polno ime',it:'Nome completo'},initials_fallback:'PI'},
  {suggested_key:'epravopis',labels:{sl:'ePravopis',it:'ePravopis'},initials_fallback:'eP'},
  {suggested_key:'disconnected',labels:{sl:'Not enabled'}}
]};
const t=(key,values={})=>({answer_wait_long:'Long wait.',answer_wait_sources:'Browse sources.',public_release:'Version '+values.version+' · '+values.date}[key]||key);

test('configured legend shares presentation and deduplicates collections independently of hits',()=>{
  const rows=legendSources(registry,['epravopis_2025','collection','epravopis_2026','ontology','tokenizer'],'it');
  assert.deepEqual(rows.map(row=>row.key),['collection','epravopis']);
  assert.deepEqual(rows[0],sourcePresentation(registry,{source_slug:'collection'},'it'));
  assert.equal(sourcePresentation(registry,'epravopis_2025').key,'epravopis_2025');
  assert.equal(sourcePresentation(registry,'epravopis_2025').label,'ePravopis');
  assert.equal(rows[0].label,'Nome completo');assert.equal(rows[0].initials,'PI');
  assert.deepEqual(legendSources(registry,undefined),[]);
});

test('copy retains displayed qualifiers and credits exactly cited records, preserving distinct titles and numbers',()=>{
  const tier={state:'complete',generation_status:'generated',answer_kind:'generated',blocks:[{block_id:'C1',text:'Claim [2].',evidence_ids:['a']}],evidence:[
    {evidence_id:'a',local_number:1,source_slug:'collection',title:'First entry',source_status:'draft'},
    {evidence_id:'b',local_number:2,source_slug:'collection',title:'Second entry'},
    {evidence_id:'unused',local_number:3,source_slug:'other',title:'Uncited discovery record',url:'https://private/api?session=secret'}
  ]};
  const text=answerCopyText('Claim [2].[1]\n\nDraft source [1]',tier,item=>sourcePresentation(registry,item).label,'Viri');
  assert.equal(text,'Claim [2].[1]\n\nDraft source [1]\n\nViri\n[2] Polno ime — Second entry\n[1] Polno ime — First entry');
  assert(!text.includes('Uncited'));assert(!text.includes('session'));assert(!text.includes('unused'));
  assert.equal(answerCopyText('No supported answer.',{blocks:[{block_id:'C1',text:'No supported answer.'}],evidence:tier.evidence},()=>'', 'Viri'),'No supported answer.');
});

test('long wait uses pending state, threshold and actual returned results only',()=>{
  const job={state:'running'};
  assert.equal(longerWaitMessage(job,1000,20999,true,t),'');
  assert.equal(longerWaitMessage(job,1000,21000,false,t),'Long wait.');
  assert.equal(longerWaitMessage(job,1000,21000,true,t),'Long wait. Browse sources.');
  for(const state of ['complete','failed','cancelled'])assert.equal(longerWaitMessage({state},1000,99999,true,t),'');
  assert.equal(longerWaitMessage({...job,cancel_requested:true},1000,99999,true,t),'');
});

test('release label is immutable served metadata, independent of browser time and refreshed capabilities',()=>{
  assert(Object.isFrozen(publicRelease));
  const release={version:'0.1.1',date:'2026-09-19'};
  assert.equal(releaseLabel(release,'sl',t),'Version 0.1.1 · 19. 9. 2026');
  assert.equal(releaseLabel(release,'it',t),'Version 0.1.1 · 19.9.2026');
  const before=releaseLabel(publicRelease,'sl',t),now=Date.now;
  try{Date.now=()=>9999999999999;assert.equal(releaseLabel(publicRelease,'sl',t),before);}finally{Date.now=now;}
  assert.equal(releaseLabel({version:'0.1.0',date:'2026-09-18'},'sl',t),'Version 0.1.0 · 18. 9. 2026');
});
