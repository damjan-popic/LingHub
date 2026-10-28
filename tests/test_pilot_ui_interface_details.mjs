import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {sourcePresentation,legendSources,answerCopyText,longerWaitMessage,releaseLabel,appendSourceReaderBlocks} from '../pilot_ui/interface-details.mjs';
import {publicRelease} from '../pilot_ui/public-release.mjs';
import {sourceStatusNotes} from '../pilot_ui/core.mjs';

const registry={sources:[
  {suggested_key:'collection',labels:{sl:'Polno ime',it:'Nome completo'},initials_fallback:'PI'},
  {suggested_key:'epravopis',labels:{sl:'ePravopis',it:'ePravopis'},initials_fallback:'eP'},
  {suggested_key:'disconnected',labels:{sl:'Not enabled'}}
]};
const t=(key,values={})=>({answer_wait_long:'Long wait.',answer_wait_sources:'Browse sources.',public_release:'Version '+values.version+' · '+values.date}[key]||key);

test('packaged documentary legend distinguishes current reference, proposal, and annual additions',()=>{
  const presentation=JSON.parse(fs.readFileSync(new URL('../pilot_ui/source-presentation.json',import.meta.url)));
  const configured=JSON.parse(fs.readFileSync(new URL('../pilot_backend/enabled-documentary-sources.json',import.meta.url)));
  const rows=legendSources(presentation,configured,'sl');
  assert.equal(rows.find(row=>row.key==='sp2001').label,'Slovenski pravopis 2001');
  assert.match(rows.find(row=>row.key==='pravopis8').label,/predlog/);
  assert.match(rows.find(row=>row.key==='epravopis').label,/letni dodatki 2025/);
  const captured=rows.find(row=>row.key==='epravopis-slovar');
  assert.match(captured.label,/spletni slovar \(predlog\)/);
  for(const count of ['11.709','11.746','37'])assert(captured.coverageNote.includes(count));
  assert.match(captured.coverageNote,/Ni potrjeno, da seznam zajema celoten slovar/);
  assert.match(legendSources(presentation,configured,'it').find(row=>row.key==='pravopis8').label,/proposta/);
  assert(!rows.some(row=>row.key==='loris'),'LORIS remains a tool, not a documentary collection');
});

const textElement=(tag,text,attrs={})=>({tag,text,attrs,children:[],append(...items){this.children.push(...items);},
  set innerHTML(value){throw Error('HTML must never execute');}});
test('original table renderer preserves full ordered headers, spans, cells and literal markup as text',()=>{
  const root=textElement('div'),cells=[
    [{kind:'th',text:'Sklon',rowspan:0,colspan:1,scope:'col'},{kind:'th',text:'Ednina',rowspan:1,colspan:2,scope:'colgroup'}],
    [{kind:'td',text:'rodilnik',rowspan:1,colspan:1},{kind:'td',text:'a\nali b <script>steal()</script>',rowspan:1,colspan:1}]];
  appendSourceReaderBlocks(root,[{heading:'Celoten izvirnik',text:'Vse izvirno besedilo.'},{kind:'table',heading:'Izvirna preglednica 1',rows:cells}],textElement);
  const table=root.children[2].children[0];
  assert.equal(table.tag,'table');assert.equal(table.children[0].tag,'caption');
  const rows=table.children[1].children;
  assert.equal(rows.length,2);assert.equal(rows[0].children[0].tag,'th');
  assert.deepEqual(rows[0].children[0].attrs,{rowspan:0,colspan:1,scope:'col'});
  assert.equal(rows[1].children[1].tag,'td');assert.equal(rows[1].children[1].text,cells[1][1].text);
  assert.equal(root.children[2].attrs.tabindex,'0');
});
test('original renderer rejects arbitrary tags and attributes are never copied from captured data',()=>{
  const malicious={kind:'script',text:'steal()',rowspan:1,colspan:1};
  assert.throws(()=>appendSourceReaderBlocks(textElement('div'),[{kind:'table',rows:[[malicious]]}],textElement));
  const root=textElement('div');appendSourceReaderBlocks(root,[{kind:'table',rows:[[
    {kind:'td',text:'<img src=x onerror=steal()>',rowspan:1,colspan:1,onclick:'steal()',scope:'onload'}]]}],textElement);
  const cell=root.children[0].children[0].children[0].children[0].children[0];
  assert.deepEqual(cell.attrs,{rowspan:1,colspan:1});assert.equal(cell.tag,'td');assert.match(cell.text,/onerror/);
});
test('structured original coverage appears once while the proposal warning remains visible',()=>{
  const source={status:'Predlog',bibliography:{coverage_note:'Older capture wording'},
    reader_blocks:[{kind:'collection_coverage',heading:'Obseg zbirke',text:'Exact captured coverage.'}]};
  const notes=sourceStatusNotes(source);
  assert.equal(notes.length,1);assert.equal(notes[0].category,'proposal_status');
  delete source.reader_blocks;
  assert(sourceStatusNotes(source).some(note=>note.key==='source_coverage_note'),'historical source notes remain available');
});

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
  assert.deepEqual(publicRelease,{version:'0.5.0',date:'2026-10-27'});
  const release={version:'0.1.1',date:'2026-09-19'};
  assert.equal(releaseLabel(release,'sl',t),'Version 0.1.1 · 19. 9. 2026');
  assert.equal(releaseLabel(release,'it',t),'Version 0.1.1 · 19.9.2026');
  const before=releaseLabel(publicRelease,'sl',t),now=Date.now;
  try{Date.now=()=>9999999999999;assert.equal(releaseLabel(publicRelease,'sl',t),before);}finally{Date.now=now;}
  assert.equal(releaseLabel({version:'0.1.0',date:'2026-09-18'},'sl',t),'Version 0.1.0 · 18. 9. 2026');
});
