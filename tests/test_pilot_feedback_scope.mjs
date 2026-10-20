import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../pilot_ui/pilot.mjs',import.meta.url),'utf8');
const between=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const line=start=>source.split('\n').find(row=>row.trim().startsWith(start));
class Node {
  constructor(){this.textContent='';this.value='';this.children=[];this.events={};this.hidden=false;this.disabled=false;this.open=false;this.dataset={};this.classList={toggle(){}};}
  addEventListener(name,handler){this.events[name]=handler;}
  dispatch(name,extra={}){this.events[name]?.({preventDefault(){},submitter:this,currentTarget:this,...extra});}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  querySelector(){return this.child ||= new Node();}
  querySelectorAll(){return [];}
  setAttribute(){}
  close(){this.open=false;this.closeCount=(this.closeCount||0)+1;}
  reset(){}
  focus(){}
}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness(handler){
  const nodes=new Map(),$=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
  const family=id=>({answer_family_id:id,question:'Question '+id,locale:'sl',task:'reference',short:{run_id:'run-'+id,state:'complete'},long:null});
  const state={boot:0,user:{id:'fixture'},locale:'sl',family:family('a'),feedbackTarget:null,feedback:new Map(),feedbackEvents:new Map(),feedbackDrafts:new Map(),delivered:new Set(),requestKeys:new Map(),polling:new Map(),capabilities:{},longOpen:false};
  const renders=[],calls=[];
  const context={state,$,submission:{clear(){},prepare:(owner,payload)=>({...payload,idempotency_key:'fixture-key'})},document:{querySelectorAll:()=>[...nodes.values()].filter(n=>n.open),addEventListener(){}},sourceRequest:0,
    beta:()=>true,api:(path,options)=>{calls.push({path,options});return handler(path,options);},familyId:()=>state.family?.answer_family_id,
    textStatus:(id,value)=>{$(id).textContent=value;},errorText:()=> 'request_failed',t:x=>x,el:()=>new Node(),clear:n=>n.replaceChildren(),
    resetMatches(){},setURL(){},clearClarification(){},renderSourceLegend(){},captureSelection(){},renderFeedback:(tier,kind)=>renders.push({run:tier.run_id,kind}),
    modelText:()=>'',renderGenerationNotice(){},syncMatches(){},renderLexical(){},renderText(){},sourceButtons(){},
    jobState:t=>t?.state,tierBlocks:()=>[],acceptedStates:new Set(['complete']),pendingStates:new Set(['pending']),isCpuTask:()=>false,
    sourceExplorer:{render(){}},unwrapFamily:x=>x.family||x,keyFor:()=> 'synthetic-key',pollJob(){},taskControls(){},showClarification(){},
  };
  vm.createContext(context);
  vm.runInContext([
    line('async function action('),
    between('function clearFeedbackContext()','function authenticated('),
    between('async function recordDelivered(','function renderFeedback('),
    between('function renderFamily(','async function loadFeedback('),
    between('async function submitQuestion(','async function expand('),
    between('async function saveFeedback(','async function historyList('),
    line("document.addEventListener('selectionchange'"),
    line("$('new-question').addEventListener('click'"),
    line("$('question-form').addEventListener('submit'"),
  ].join('\n'),context);
  function target(id='a'){
    const value={tier:{run_id:'run-'+id},kind:'short',target:{kind:'answer'},key:'key-'+id};state.feedbackTarget=value;$('feedback-dialog').open=true;return value;
  }
  target();$('question').value='New question';$('task').value='reference';$('answer-locale').value='sl';
  return {state,$,context,renders,calls,target,family,run:code=>vm.runInContext(code,context)};
}

test('the actual feedback submit event acknowledges only the server-confirmed current target',async()=>{
  const response=deferred(),h=harness(()=>response.promise);h.$('feedback-comment').value='Original comment';
  h.$('feedback-form').dispatch('submit');
  assert.equal(h.$('answer-status').textContent,'');assert.equal(h.$('feedback-dialog').open,true);
  assert.equal(h.calls[0].options.body.run_id,'run-a');
  response.resolve({acknowledged:true,feedback_id:'saved'});await flush();
  assert.equal(h.$('answer-status').textContent,'feedback_saved');assert.equal(h.$('feedback-dialog').open,false);
  assert.equal(h.state.feedbackTarget,null);assert.equal(h.state.feedback.get('run-a').feedback_id,'saved');
});

test('missing acknowledgement never displays saved',async()=>{
  const h=harness(async()=>({acknowledged:false}));h.$('feedback-form').dispatch('submit');await flush();
  assert.equal(h.$('answer-status').textContent,'');assert.equal(h.$('feedback-status').textContent,'request_failed');assert.equal(h.$('feedback-dialog').open,true);
});

test('family switch clears feedback context and ignores the old acknowledgement',async()=>{
  const response=deferred(),h=harness(()=>response.promise);h.$('answer-status').textContent='old acknowledgement';h.$('feedback-form').dispatch('submit');
  h.context.nextFamily=h.family('b');h.run('renderFamily(nextFamily)');
  assert.equal(h.state.feedbackTarget,null);assert.equal(h.$('answer-status').textContent,'');
  h.target('b');h.$('answer-status').textContent='current answer';
  response.resolve({acknowledged:true});await flush();
  assert.equal(h.$('answer-status').textContent,'current answer');assert.equal(h.$('feedback-dialog').open,true);assert.equal(h.state.feedback.has('run-a'),false);
  assert.equal(h.renders.some(x=>x.run==='run-a'),false);
});

test('a replaced feedback dialog suppresses stale errors even within one family',async()=>{
  const response=deferred(),h=harness(()=>response.promise);h.$('feedback-form').dispatch('submit');
  h.target('other-target');h.$('feedback-status').textContent='current dialog';response.reject(Error('old request'));await flush();
  assert.equal(h.$('feedback-status').textContent,'current dialog');assert.equal(h.$('feedback-dialog').open,true);
});

test('new-question click and submit both clear acknowledgement and pending target',async()=>{
  for(const event of ['click','submit']){
    const feedback=deferred(),question=deferred(),h=harness(path=>path==='/feedback'?feedback.promise:question.promise);
    h.$('feedback-form').dispatch('submit');h.$('answer-status').textContent='old acknowledgement';
    h.$(event==='click'?'new-question':'question-form').dispatch(event);
    assert.equal(h.state.feedbackTarget,null);assert.equal(h.$('answer-status').textContent,'');
    feedback.resolve({acknowledged:true});await flush();assert.equal(h.$('answer-status').textContent,'');
    if(event==='submit'){question.resolve(h.family('b'));await flush();}
  }
});

test('session reset cannot receive old saved feedback or delivery state',async()=>{
  const response=deferred(),h=harness(()=>response.promise);h.$('feedback-form').dispatch('submit');h.$('answer-status').textContent='old';
  h.run('resetPrivate()');assert.equal(h.state.feedbackTarget,null);assert.equal(h.$('answer-status').textContent,'');
  response.resolve({acknowledged:true});await flush();assert.equal(h.state.feedback.size,0);assert.equal(h.$('answer-status').textContent,'');
});

test('delayed delivery failure belongs only to its captured family and run',async()=>{
  const response=deferred(),h=harness(()=>response.promise);h.state.family.short.display_sha256='hash';h.run("acknowledgeDelivery(state.family.short,'short')");
  h.context.nextFamily=h.family('b');h.run('renderFamily(nextFamily)');h.$('answer-status').textContent='current answer';
  response.reject(Error('old delivery'));await flush();assert.equal(h.$('answer-status').textContent,'current answer');
  const current=deferred(),same=harness(()=>current.promise);same.state.family.short.display_sha256='hash';same.run("acknowledgeDelivery(state.family.short,'short')");
  current.reject(Error('current delivery'));await flush();assert.equal(same.$('answer-status').textContent,'feedback_pending');
});

test('successful delivery from an old login cannot repopulate delivered state',async()=>{
  const response=deferred(),h=harness(()=>response.promise);h.state.family.short.display_sha256='hash';h.run("acknowledgeDelivery(state.family.short,'short')");
  h.run('resetPrivate()');response.resolve({acknowledged:true});await flush();assert.equal(h.state.delivered.size,0);
});
