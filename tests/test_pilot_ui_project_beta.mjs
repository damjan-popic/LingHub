import test from 'node:test';
import assert from 'node:assert/strict';
import {recordUsed,createSourceExplorer} from '../pilot_ui/source-explorer.mjs';

class Node {
  constructor(tag='div'){this.tag=tag;this.children=[];this.attributes={};this.listeners={};this.hidden=false;this.textContent='';}
  setAttribute(k,v){this.attributes[k]=v;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  addEventListener(k,fn){this.listeners[k]=fn;}
  querySelector(){return this.children[0]||=(new Node('span'));}
  click(){return this.listeners.click?.();}
}
const flush=async()=>{await new Promise(resolve=>setImmediate(resolve));};
function fixture(api){const nodes=new Map(),element=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};let family={answer_family_id:'owned-a',matches_available:true,short:{state:'running'}};
  globalThis.document={createElement:tag=>new Node(tag)};
  const opened=[],view=createSourceExplorer({api,family:()=>family,pending:()=>family.short.state==='running',t:(key,values={})=>key+JSON.stringify(values),label:s=>s.source_label,openOriginal:r=>opened.push(r),element});
  return {view,element,opened,get family(){return family;},set family(v){family=v;}};
}
test('pending use stays unknown; completion uses every exact citation target in a grouped record',()=>{
  const record={evidence_id:'first',evidence_ids:['first','second']},family={short:{state:'complete',answer_kind:'generated',generation_status:'generated',blocks:[{block_id:'C1',text:'Claim.',evidence_ids:['second']}],evidence:[{evidence_id:'second',local_number:1}]}};
  assert.equal(recordUsed(record,family,true),null);assert.equal(recordUsed(record,family,false),true);
  assert.equal(recordUsed(record,{short:{state:'complete',evidence:[]},long:{state:'complete',evidence:[{evidence_id:'second'}]}},false),false);
});
test('aggregate badges include later sources; selected pages and original targets survive switching and collapse',async()=>{
  const calls=[],rows=Array.from({length:23},(_,i)=>({record_id:'r'+i,evidence_id:'e'+i,title:'Entry '+i,evidence_ids:['e'+i]}));
  const f=fixture(async path=>{calls.push(path);if(path.endsWith('/sources'))return {sources:[{source_slug:'early',source_label:'Early',returned_count:23},{source_slug:'later',source_label:'Later',returned_count:1}]};
    const u=new URL(path,'https://test'),offset=Number(u.searchParams.get('offset')),later=u.searchParams.get('source')==='later';return {items:later?[{record_id:'last',evidence_id:'exact-last',title:'Last source'}]:rows.slice(offset,offset+10),returned_count:later?1:23,next_offset:!later&&offset+10<rows.length?offset+10:null};});
  f.view.sync(f.family);await flush();assert(f.element('beta-explorer').hidden);f.view.toggle();assert(!f.element('beta-explorer').hidden);
  assert.equal(f.element('explorer-badges').children.length,2);f.element('explorer-badges').children[0].click();await flush();assert.equal(f.element('explorer-list').children.length,10);
  assert.equal(f.element('explorer-list').children[0].attributes['data-used'],'pending');f.element('explorer-next').click();await flush();assert.equal(f.element('explorer-list').children.length,10);
  f.element('explorer-badges').children[1].click();await flush();f.element('explorer-list').children[0].children[0].click();assert.equal(f.opened[0].evidence_id,'exact-last');
  f.element('explorer-badges').children[0].click();await flush();assert.equal(f.element('explorer-list').children.length,10);const count=calls.length;f.view.toggle();f.view.toggle();assert.equal(calls.length,count);
  f.family.short={state:'complete',generation_status:'generated',blocks:[{block_id:'C1',text:'Claim.',evidence_ids:['e19']}],evidence:[{evidence_id:'e19',local_number:1}]};f.view.sync(f.family);assert.equal(f.element('explorer-list').children.at(-1).attributes['data-used'],'true');assert.equal(calls.length,count);
});
test('late private result is discarded after reset and a failed summary can be retried',async()=>{
  let finish,attempt=0;const f=fixture(()=>{attempt++;if(attempt===1)return new Promise(resolve=>finish=resolve);if(attempt===2)throw Error('offline');return {sources:[{source_slug:'safe',source_label:'Safe'}]};});
  f.view.sync(f.family);f.view.reset();finish({sources:[{source_slug:'old-private',source_label:'Old'}]});await flush();assert.equal(f.element('explorer-badges').children.length,0);
  f.family={...f.family,answer_family_id:'owned-b'};f.view.sync(f.family);await flush();assert(!f.element('explorer-retry').hidden);f.element('explorer-retry').click();await flush();assert.equal(f.element('explorer-badges').children[0].textContent,'Safe');
});

test('incidental evidence is never support for a no-support response or an uncited generated claim',()=>{
 const evidence=[{evidence_id:'incidental',local_number:1}],record={evidence_id:'incidental'};
 for(const answer_kind of ['source_only','no_support','generated'])assert.equal(recordUsed(record,{short:{state:'complete',answer_kind,evidence,blocks:[{block_id:'C1',text:'Status or other claim.'}]}},false),false);
});
