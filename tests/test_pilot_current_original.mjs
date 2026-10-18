import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {canOpenCurrentOriginal,safeSourceLink} from '../pilot_ui/core.mjs';

class Node {
  constructor(tag,text,attributes={}){this.tag=tag;this.textContent=text??'';this.attributes=attributes;this.children=[];this.disabled=false;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(){this.children=[];}
}
const source=fs.readFileSync(new URL('../pilot_ui/pilot.mjs',import.meta.url),'utf8');
const renderer=source.slice(source.indexOf('function appendCurrentOriginalAction('),source.indexOf('async function openSource('));
const metadata={current_original_lookup:true,original_view:'historical_unavailable'};
function harness(api,current=()=>true,data=metadata){
  const root=new Node('main'),historical=new Node('blockquote','Historical retained excerpt.');root.append(historical);
  const context={api,current,data,canOpenCurrentOriginal,safeSourceLink,encodeURIComponent,t:key=>key,
    el:(tag,text,attrs)=>new Node(tag,text,attrs),clear:node=>node.replaceChildren(),$:()=>root,
    localButton:(key,handler)=>Object.assign(new Node('button',key),{handler})};
  vm.runInNewContext(renderer+'\nappendCurrentOriginalAction(data,"owned-family","frozen:evidence","short",current);',context);
  const section=root.children[1];return {root,historical,button:section?.children[1],output:section?.children[2]};
}

test('only an explicitly marked unavailable original offers a current read',()=>{
  for(const data of [{},{current_original_lookup:true},{...metadata,current_original_lookup:'true'},
      {...metadata,original_view:'supplementary_current'}]){
    const view=harness(()=>{throw Error('No lookup permitted');},()=>true,data);
    assert.equal(view.root.children.length,1);
  }
  const view=harness(()=>{throw Error('Explicit click required');});
  assert.equal(view.root.children.length,2);assert.equal(view.button.textContent,'current_original_open');
});

test('current original appears separately and literally only after the explicit click',async()=>{
  const calls=[],excerpt='Current original <script>literal()</script> [1].';
  const view=harness(async route=>{calls.push(route);return {original_view:'supplementary_current',title:'Current title',excerpt,url:'javascript:alert(1)'};});
  assert.deepEqual(calls,[]);
  await view.button.handler();
  assert.deepEqual(calls,['/families/owned-family/sources/frozen%3Aevidence/current-original?tier=short']);
  assert.equal(view.root.children[0],view.historical);
  assert.equal(view.historical.textContent,'Historical retained excerpt.');
  assert.equal(view.output.children[0].textContent,'current_original_heading');
  assert.equal(view.output.children[1].textContent,'current_original_notice');
  assert.equal(view.output.children.find(n=>n.tag==='blockquote').textContent,excerpt);
  assert.equal(view.output.children.some(n=>n.tag==='a'||n.tag==='button'),false);
  assert.equal(view.button.disabled,true);
});

test('failed or mislabeled current read preserves the historical original and permits retry',async()=>{
  for(const api of [async()=>{throw Error('Unavailable');},async()=>({excerpt:'Unlabelled current text.'})]){
    const view=harness(api);await view.button.handler();
    assert.equal(view.root.children[0],view.historical);
    assert.equal(view.output.children[0].textContent,'current_original_unavailable');
    assert.equal(view.button.disabled,false);
  }
});

test('a stale current-original response cannot write into a newly opened source',async()=>{
  let resolve,current=true;
  const view=harness(()=>new Promise(done=>{resolve=done;}),()=>current);
  const pending=view.button.handler();current=false;
  resolve({original_view:'supplementary_current',excerpt:'Stale current text.'});await pending;
  assert.equal(view.output.children[0].textContent,'loading');
  assert.equal(view.root.children[0],view.historical);
});
