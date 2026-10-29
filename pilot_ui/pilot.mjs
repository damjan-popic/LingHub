import {submissionJournal,pollDecision} from './submission.mjs';
import {publicRelease} from './public-release.mjs';
import {noAnswerFeedback,canOpenCurrentOriginal,ordinaryFamilyView} from './core.mjs';
import {sourcePresentation,legendSources,answerCopyText,longerWaitMessage,releaseLabel,appendSourceReaderBlocks,historyCreatedDate} from './interface-details.mjs';
import {createSourceExplorer,recordUsed,citedEvidenceIds} from './source-explorer.mjs';
import {basePath,localePath,anchorForText,tierBlocks,paragraphGroups,isCpuTask,modelChoiceKey,modelAvailabilityKey,savedProvider,progressLabel,matchGroups,settledMatchUsage,matchUseLabel,generationNotice,answerNotes,sourceStatusNotes,blockTextPresentation,sourceKey,usedBadges,safeSourceLink,sourceExcerptPresentation,jobState,readableLocator,pendingStates,acceptedStates,errorKey} from './core.mjs';

const $ = id => document.getElementById(id);
const base = basePath(document.documentElement.dataset.pilotBase);
const state = {locale:document.documentElement.lang==='it'?'it':'sl',messages:{},csrf:null,user:null,capabilities:{},family:null,comparison:null,job:null,longOpen:false,feedback:new Map(),feedbackDrafts:new Map(),selection:null,feedbackTarget:null,requestKeys:new Map(),polling:new Map(),delivered:new Set(),boot:0,clarification:null,answerLocaleExplicit:false,followup:null,feedbackEvents:new Map(),submitting:false};
let matches = {family:null,phase:null,items:[],next:null,loaded:false,loading:false,coverage:[],returned:null}, matchesRequest=0, sourceRequest=0, familyRequest=0;
let registry = {sources:[]};
let signInResume = null;
const loginDraftKey = 'klikai-login-draft:'+base;
const submission=submissionJournal({getItem:key=>sessionStorage.getItem(key),setItem:(key,value)=>sessionStorage.setItem(key,value),removeItem:key=>sessionStorage.removeItem(key)},'klikai:'+base);
const beta = () => state.capabilities.project_beta === true;
const t = (key, values={}) => (state.messages[state.locale]?.[beta() && state.messages[state.locale]?.['beta_'+key] ? 'beta_'+key : key] || key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??''));
const keyFor = name => {if(!state.requestKeys.has(name))state.requestKeys.set(name,crypto.randomUUID());return state.requestKeys.get(name);};
const clear = node => node.replaceChildren();
function el(tag,text,attrs={}) { const n=document.createElement(tag);if(text!==null&&text!==undefined)n.textContent=String(text);for(const [k,v]of Object.entries(attrs)){if(k==='class')n.className=v;else n.setAttribute(k,String(v));}return n; }
function textStatus(id,key,values={}) {$(id).textContent=key?t(key,values):'';}
function localButton(key,handler,className='plain') {const b=el('button',t(key),{type:'button',class:className,'data-i18n':key});b.addEventListener('click',event=>{const report=error=>{const target=$('admin-dialog').open?'admin-status':'global-status';$(target).textContent=errorText(error);};try{const result=handler(event);if(result?.catch)result.catch(report);}catch(error){report(error);}});return b;}
function showDialog(id) { if(!$(id).open)$(id).showModal(); }
function unwrapFamily(data) {return data?.family || data;}
function familyId() {return state.family?.answer_family_id;}
function setURL() {const u=new URL(location.href);u.pathname=localePath(base,state.locale);u.search='';if(familyId())u.searchParams.set('answer',familyId());if(state.comparison?.comparison_id)u.searchParams.set('comparison',state.comparison.comparison_id);history.replaceState({},'',u);}
function setLocale(locale,push=true) {
  state.locale=locale==='it'?'it':'sl';document.documentElement.lang=state.locale;document.title='klikai · '+(state.locale==='it'?'Domande e risposte':'Vprašanja in odgovori');
  document.querySelectorAll('[data-i18n]').forEach(n=>n.textContent=t(n.dataset.i18n));
  document.querySelectorAll('[data-i18n-aria]').forEach(n=>n.setAttribute('aria-label',t(n.dataset.i18nAria)));
  document.querySelectorAll('[data-i18n-placeholder]').forEach(n=>n.setAttribute('placeholder',t(n.dataset.i18nPlaceholder)));
  $('public-release').textContent=releaseLabel(publicRelease,state.locale,t);
  $('login-locale').value=state.locale;
  document.querySelectorAll('[data-locale]').forEach(n=>{const active=n.dataset.locale===state.locale;n.href=localePath(base,n.dataset.locale)+location.search;n.toggleAttribute('aria-current',false);if(active)n.setAttribute('aria-current','page');});
  if(!state.answerLocaleExplicit)$('answer-locale').value=state.locale;
  if(state.clarification)clarificationText();
  modelControls();capacityControls();
  $('locale-note').hidden=!state.family||state.family.locale===state.locale;
  $('expand-button').querySelector('span').textContent=t(state.longOpen?'collapse_long':'expand_long');
  if(state.family){if(acceptedStates.has(jobState(state.family.short)))renderText(state.family.short,'short',$('short-answer'));if(state.longOpen&&acceptedStates.has(jobState(state.family.long)))renderText(state.family.long,'long',$('long-answer'));$('answer-model').textContent=modelText(state.family.short);$('long-model').textContent=modelText(state.family.long);renderFeedback(state.family.short,'short',$('short-feedback'));if(state.longOpen)renderFeedback(state.family.long,'long',$('long-feedback'));}
  renderGenerationNotice();renderMatches();renderSourceLegend();renderWaitMessage();
  if(beta()){applyBetaPresentation();sourceExplorer.render();}
  if(push)setURL();
}
async function api(path, options={}) {
  const method=options.method || 'GET',controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
  const headers={'Accept':'application/json','Accept-Language':state.locale,'X-Pilot-Locale':state.locale};
  if(method!=='GET'){headers['Content-Type']='application/json';if(state.csrf)headers['X-CSRF-Token']=state.csrf;}
  try {
    const res=await fetch(base+'/api/pilot'+path,{method,credentials:'same-origin',cache:'no-store',redirect:'error',headers,body:method==='GET'?undefined:JSON.stringify(options.body||{}),signal:controller.signal});
    const data=options.response==='text'&&res.ok?await res.text():await res.json().catch(()=>({}));
    if(!res.ok){const err=Error(data.error?.message||t(errorKey(data.error?.code)));err.code=data.error?.code;err.localized=data.error?.locale===state.locale;err.status=res.status;
      if(res.status===401&&path!=='/login'){const resume=signInResume||{question:$('question').value,answer:familyId(),comparison:state.comparison?.comparison_id};resetPrivate();signInResume=resume;$('login-view').hidden=false;textStatus('login-error','session_expired');}throw err;}
    return data;
  } catch(err){if(err.name==='AbortError'){err.code='timeout';err.localized=false;}throw err;}finally{clearTimeout(timer);}
}
function errorText(error) {return !beta()&&error.localized?error.message:t(errorKey(error.code));}
async function action(button,fn,errorId='global-status') {if(button?.disabled)return;if(button)button.disabled=true;try{return await fn();}catch(error){$(errorId).textContent=errorText(error);}finally{if(button)button.disabled=false;}}
function clearFeedbackContext() {state.feedbackTarget=null;textStatus('answer-status','');textStatus('feedback-status','');if($('feedback-dialog').open)$('feedback-dialog').close();}
function resetPrivate() {
  sourceRequest++;resetMatches();clearFeedbackContext();
  state.boot++;for(const cancel of state.polling.values())cancel();state.polling.clear();state.user=null;state.csrf=null;state.family=null;state.comparison=null;state.selection=null;state.feedback.clear();state.feedbackEvents.clear();state.followup=null;state.submitting=false;state.feedbackDrafts.clear();state.delivered.clear();state.requestKeys.clear();state.job=null;state.longOpen=false;
  $('app-view').hidden=true;$('account-button').hidden=true;$('beta-logout').hidden=true;$('password-view').hidden=true;clear($('short-answer'));clear($('long-answer'));clear($('short-sources'));clear($('long-sources'));clear($('short-feedback'));clear($('long-feedback'));clear($('history-list'));clear($('admin-content'));clear($('source-content'));clear($('side-a-answer'));clear($('side-b-answer'));$('question').value='';document.querySelectorAll('dialog[open]').forEach(d=>d.close());setURL();
  clearClarification();$('focus').value='';$('model').value='auto';state.answerLocaleExplicit=false;state.capabilities={};$('capacity-state').hidden=true;renderSourceLegend();
}
function authenticated(session) {
  state.csrf=session.csrf_token;state.user=session.user;state.capabilities=session.capabilities||{};$('login-view').hidden=true;$('account-button').hidden=false;$('account-name').textContent=session.user.username;
  $('account-admin').hidden=!['admin','reviewer'].includes(session.user.role);$('password-view').hidden=!session.user.must_change_password;$('app-view').hidden=!!session.user.must_change_password;$('password-cancel').hidden=!!session.user.must_change_password;
  document.querySelectorAll('[data-admin-tab]').forEach(b=>b.hidden=session.user.role==='reviewer'&&b.dataset.adminTab!=='feedback');
  const tasks=state.capabilities.tasks;if(Array.isArray(tasks))for(const option of $('task').options)option.hidden=option.disabled=!tasks.some(x=>(typeof x==='string'?x:x.id)===option.value);
  if($('task').selectedOptions[0]?.disabled)$('task').value='reference';
  applyBetaPresentation();modelControls();taskControls();
}
function applyBetaPresentation(){
  document.documentElement.classList.toggle('project-beta',beta());
  document.querySelectorAll('[data-i18n]').forEach(n=>n.textContent=t(n.dataset.i18n));
  document.querySelectorAll('[data-i18n-placeholder]').forEach(n=>n.setAttribute('placeholder',t(n.dataset.i18nPlaceholder)));
  const feedback=$('short-feedback'), actions=$('expand-button').parentElement;
  if(beta())actions.append(feedback);else if(feedback.parentElement===actions)actions.before(feedback);
  $('beta-notice').hidden=!beta();$('beta-logout').hidden=!beta()||!state.user;
  $('account-button').hidden=beta()||!state.user;
  $('account-password').hidden=state.user?.can_change_password===false||state.user?.shared_group===true;
  $('shared-history-note').hidden=state.user?.shared_group!==true;renderSourceLegend();
  $('beta-feedback-rating').hidden=!beta();
  const languageControl=$('text-language-control');if(beta())$('clarification').append(languageControl);else if(languageControl.parentElement!==$('options'))$('options').append(languageControl);
  if(beta()){$('task').value='reference';$('options').hidden=true;$('long-section').hidden=true;$('comparison').hidden=true;state.longOpen=false;}
}
function modelControls(){
  if(beta()){$('model').hidden=true;$('model-availability').hidden=true;return;}
  const models=state.capabilities.models;
  for(const option of $('model').options){
    const configured=!models||!!(Array.isArray(models)?models.find(m=>(typeof m==='string'?m:m.id||m.model)===option.value):models[option.value]);
    option.disabled=option.hidden=!configured;
    const observed=state.capabilities.generation?.models?.[option.value];option.textContent=t(modelChoiceKey(option.value))+(observed?' · '+t(modelAvailabilityKey(observed)):'');
  }
  if($('model').selectedOptions[0]?.disabled){const preferred=state.capabilities.default_model;$('model').value=[...$('model').options].find(o=>!o.disabled&&o.value===preferred)?.value||[...$('model').options].find(o=>!o.disabled)?.value||'';}
  const selected=$('model').value,observed=state.capabilities.generation?.models?.[selected];
  $('model-availability').hidden=isCpuTask($('task').value);
  $('model-availability').textContent=t(selected==='auto'?'model_auto_policy':'model_explicit_policy')+' '+t(modelAvailabilityKey(observed));
}
function taskControls(){const task=$('task').value,cpu=isCpuTask(task),check=['text_check','morphology'].includes(task),note=['jezikovna_urgenca','jezikovna_posta'].includes(task)?'editorial_task_note':'deterministic_task_note';$('model').disabled=cpu;$('model').hidden=cpu;document.querySelector('label[for="model"]').hidden=cpu;$('deterministic-task-note').hidden=!cpu;$('deterministic-task-note').dataset.i18n=note;textStatus('deterministic-task-note',note);$('text-language-control').hidden=!(check||task==='reference');$('input-language').required=false;modelControls();capacityControls();}
function capacityControls(){if(beta()){$('capacity-state').hidden=true;return;}const generation=state.capabilities.generation,model=generation?.models?.[$('model').value];$('capacity-state').hidden=$('task').value!=='reference'||!model||model.available===true;if(!$('capacity-state').hidden)textStatus('capacity-message',$('model').value==='auto'?'generation_waiting_capacity':'selected_model_unavailable');}
async function refreshCapabilities(){state.capabilities={...state.capabilities,...await api('/capabilities')};applyBetaPresentation();modelControls();capacityControls();}
function clearClarification(){state.clarification=null;$('clarification').hidden=true;$('focus-control').hidden=true;$('ask-button').querySelector('span').dataset.i18n='ask';$('ask-button').querySelector('span').textContent=t('ask');}
function clarificationText(){textStatus('clarification-message',state.clarification?.code==='input_language_required'?'input_language_required':'focus_required');$('ask-button').querySelector('span').dataset.i18n='continue_question';$('ask-button').querySelector('span').textContent=t('continue_question');}
function showClarification(value){clearFeedbackContext();resetMatches();state.clarification=value;state.family=null;state.comparison=null;state.longOpen=false;$('answer-card').hidden=true;$('comparison').hidden=true;$('job-state').hidden=true;$('empty-state').hidden=true;$('clarification').hidden=false;$('focus-control').hidden=!value.fields.includes('focus');if(beta()){$('text-language-control').hidden=!value.fields.includes('input_language');}else if(value.fields.includes('input_language')){$('options').hidden=false;$('options-toggle').setAttribute('aria-expanded','true');}clarificationText();setURL();$(value.fields.includes('focus')?'focus':'input-language').focus();}
async function bootstrap() {
  const before=new URL(location.href),answer=before.searchParams.get('answer'),comparison=before.searchParams.get('comparison');textStatus('global-status','loading');
  const loginError=before.searchParams.get('login_error');
  if(loginError){before.searchParams.delete('login_error');history.replaceState({},'',before);}
  const session=await api('/session');state.csrf=session.csrf_token;state.capabilities=session.capabilities||{};applyBetaPresentation();
  if(!session.authenticated){$('login-view').hidden=false;textStatus('global-status','');if(['login_failed','login_throttled'].includes(loginError))textStatus('login-error',loginError);return;}
  authenticated(session);textStatus('global-status','');if(state.user.must_change_password)return;
  await refreshCapabilities();
  await loadFeedback();
  if(!answer){const pending=submission.read(state.user.id);if(pending){try{const recovered=await api('/submissions/'+encodeURIComponent(pending.payload.idempotency_key));submission.clear();await loadFamily(unwrapFamily(recovered).answer_family_id);return;}catch(error){if(error.status!==404)throw error;$('question').value=pending.payload.question;textStatus('global-status','submission_unconfirmed');return;}}}
  if(answer){await loadFamily(answer);if(comparison&&!beta())await loadComparison(comparison);}
  try{const draft=sessionStorage.getItem(loginDraftKey);sessionStorage.removeItem(loginDraftKey);if(draft&&!answer)$('question').value=draft;}catch{}
}
function evidenceId(item) {return item?.evidence_id||item?.passage_id||item?.id||item?.citation_id;}
function evidenceFor(tier) {return Array.isArray(tier?.evidence)?tier.evidence:[];}
function findSource(tier,key) {return evidenceFor(tier).find(e=>sourceKey(e)===key);}
function sourceLabel(item) {return sourcePresentation(registry,item,state.locale);}
function renderSourceLegend(){
  const rows=legendSources(registry,state.capabilities.documentary_sources,state.locale);
  $('source-legend-open').hidden=!state.user||!rows.length;
  clear($('source-legend-list'));
  for(const source of rows){const row=el('li',null,{'data-source':source.key}),description=el('span',source.label);if(source.coverageNote)description.append(el('small',source.coverageNote,{class:'source-coverage'}));row.append(el('span',source.initials,{class:'source-symbol','aria-hidden':'true'}),description);$('source-legend-list').append(row);}
}
function renderWaitMessage(){
  if(!state.job)return;
  const text=longerWaitMessage(state.job,state.job.waitStarted,performance.now(),beta()?sourceExplorer.hasResults:matches.items.length>0,t);
  if(text&&$('job-message').textContent!==text)$('job-message').textContent=text;
}
function sourceButtons(tier,kind,node,reference={}) {
  clear(node);if(beta())return;const badges=usedBadges(tier);if(!badges.length)return;
  node.append(el('span',t('used_sources'),{class:'source-heading','data-i18n':'used_sources'}));
  const add = item=>{const label=sourceLabel(item),evidence=findSource(tier,label.key),b=el('button',null,{type:'button',class:'source-button','aria-label':label.label});const pill=el('span',null,{class:'source-badge'});pill.append(el('span',label.initials,{class:'source-initial','aria-hidden':'true'}),el('span',label.short));b.append(pill);b.addEventListener('click',()=>openSource(tier,evidenceId(evidence),kind,{...reference,sourceKey:label.key}));node.append(b);};
  badges.slice(0,4).forEach(add);if(badges.length>4){const more=el('button','+'+(badges.length-4),{type:'button',class:'plain','aria-label':t('more_sources')});more.addEventListener('click',()=>{more.remove();badges.slice(4).forEach(add);});node.append(more);}
}
function renderText(tier,kind,node,reference={}) {
  clear(node);node.lang=state.family?.locale||state.locale;if((tier?.source_tier||kind)==='long'&&tier?.correction?.required)node.append(el('p',t('long_correction_notice'),{class:'qualified-note','data-i18n':'long_correction_notice',role:'status'}));const blocks=tierBlocks(tier,kind);
  if(!blocks.length){node.append(el('p',t(['no_hit','no_answer'].includes(jobState(tier))?'no_hit':jobState(tier)==='rejected'?'answer_guard_failed':jobState(tier)==='cancelled'?'cancelled':jobState(tier)==='failed'?'request_failed':'source_unavailable')));return;}
  const citationButton=part=>{const button=el('button',part.text,{type:'button',class:'citation','aria-label':t('source_number',{number:part.number})});button.addEventListener('click',()=>openSource(tier,evidenceId(part.evidence),kind,reference));return button;};
  for(const group of paragraphGroups(blocks)){
    const p=el('p',null);if(group.id)p.dataset.paragraphId=group.id;
    for(const [index,block] of group.blocks.entries()){
      if(index)p.append(document.createTextNode(' '));
      const span=el('span',null);if(block.block_id){span.dataset.blockId=block.block_id;span.dataset.runId=tier.run_id||'';span.dataset.tier=kind;}
      const presentation=blockTextPresentation(tier,block);
      for(const part of presentation.parts)span.append(part.evidence?citationButton(part):document.createTextNode(part.text));
      p.append(span);
      // Citations stay beside their assertion, outside its feedback anchor.
      if(presentation.separateCitation)p.append(citationButton(presentation.separateCitation));
      for(const citation of presentation.separateCitations||[])p.append(citationButton(citation));
    }
    node.append(p);
  }
  const notes=answerNotes(tier);
  if(notes.length){
    const aside=el('aside',null,{class:'answer-notes',lang:state.locale,'aria-label':t('answer_notes')});
    aside.append(el('p',t('answer_notes'),{class:'answer-notes-heading'}));
    for(const note of notes){const p=el('p',note.text||t(note.key,note.values),{'data-note-kind':note.category});
      for(const evidence of note.evidence){const number=evidence.local_number||evidence.number||evidence.note_number;if(Number.isInteger(number)&&number>0)p.append(citationButton({text:'['+number+']',number,evidence}));}
      aside.append(p);
      if(note.decisionSources?.length){const links=el('p',t('comparison_originals_intro'),{class:'comparison-originals'});
        for(const source of note.decisionSources){const button=el('button',t('comparison_original_link',{title:source.title}),{type:'button',class:'plain decision-source','data-decision-source':source.evidence_id});
          button.addEventListener('click',()=>openSource(tier,source.evidence_id,kind,{...reference,unusedMatch:true}));links.append(document.createTextNode(' '),button);}
        aside.append(links);}}
    node.append(aside);
  }
}
async function recordDelivered(tier,expanded) {
  if(!tier?.run_id||!(tier.display_sha256||tier.delivery_token))return;const key=tier.run_id+':'+expanded;if(state.delivered.has(key))return;
  const boot=state.boot;await api('/runs/'+encodeURIComponent(tier.run_id)+'/delivered',{method:'POST',body:{...(beta()?{delivery_token:tier.delivery_token}:{display_sha256:tier.display_sha256}),expanded}});if(boot===state.boot)state.delivered.add(key);
}
function acknowledgeDelivery(tier,kind) {
  const boot=state.boot,id=familyId(),run=tier?.run_id;
  const current=()=>boot===state.boot&&id===familyId()&&run===state.family?.[kind]?.run_id;
  return recordDelivered(tier,(tier?.source_tier||kind)==='long').then(()=>{if(current())renderFeedback(tier,kind,$(kind+'-feedback'));}).catch(()=>{if(current())textStatus('answer-status','feedback_pending');});
}
function renderFeedback(tier,kind,node) {
  clear(node);if(!tier?.run_id||!(['complete','failed','cancelled'].includes(tier.state)||acceptedStates.has(jobState(tier))))return;
  const delivered=state.delivered.has(tier.run_id+':'+((tier?.source_tier||kind)==='long'));
  if(beta()){renderSavedFeedback(tier,kind,node);const missing=noAnswerFeedback(tier);if(missing)node.append(el('p',t('no_answer_feedback'),{class:'quality-hint'}));const mark=localButton(state.feedback.has(tier.run_id)?'feedback_addendum':missing?'no_answer_feedback_action':'mark_error',()=>openFeedback(tier,kind),'feedback-mark');mark.disabled=!delivered;node.append(mark);return;}
  const old=state.feedback.get(tier.run_id),draft=state.feedbackDrafts.get(tier.run_id)||{quality_score:old?.quality_score??null,comment:old?.comment||''};
  const form=el('form',null,{class:'quality-form'}),scale=el('fieldset',null,{class:'quality-scale'});scale.append(el('legend',t('quality_question')));
  for(let score=1;score<=5;score++){const label=el('label',null,{class:'quality-choice'}),input=el('input',null,{type:'radio',name:'quality-'+tier.run_id,value:score,'aria-label':t('quality_score',{score})});input.checked=draft.quality_score===score;label.append(input,el('span',score));scale.append(label);input.addEventListener('change',()=>{draft.quality_score=score;update();});}
  const endpoints=el('p',t('quality_endpoints'),{class:'quality-hint'}),label=el('label',t('quality_comment')),comment=el('textarea',null,{rows:'2',maxlength:'8000',class:'quality-comment'});comment.value=draft.comment;label.append(comment);
  const submit=el('button',t('feedback_save'),{type:'submit',class:'primary quality-submit'}),ack=el('div',old?t('feedback_saved'):'',{class:'feedback-ack',role:'status'});
  function update(){draft.comment=comment.value;state.feedbackDrafts.set(tier.run_id,{...draft});submit.disabled=!delivered||(draft.quality_score===null&&!draft.comment.trim());const changed=draft.quality_score!==(old?.quality_score??null)||draft.comment!==(old?.comment||'');ack.textContent=changed?t('feedback_unsaved'):old?t('feedback_saved'):'';}
  comment.addEventListener('input',update);form.append(scale,endpoints,label,submit,ack);const mark=localButton('mark_error',()=>openFeedback(tier,kind),'feedback-mark');mark.disabled=!delivered;node.append(form,mark);update();
  form.addEventListener('submit',event=>{event.preventDefault();const boot=state.boot,id=familyId(),current=()=>boot===state.boot&&id===familyId()&&tier.run_id===state.family?.[kind]?.run_id;action(submit,async()=>{try{const body={run_id:tier.run_id,quality_score:draft.quality_score,display_locale:state.locale,rating:old?.rating||null,comment:comment.value,tags:old?.tags||[],idempotency_key:keyFor('quality:'+tier.run_id+':'+JSON.stringify(draft))};if(old?.feedback_id)body.feedback_id=old.feedback_id;const result=await api('/feedback',{method:'POST',body});if(!current())return;if(!result.acknowledged)throw Error();state.feedbackDrafts.delete(tier.run_id);state.feedback.set(tier.run_id,{...old,...body,...result});renderFeedback(tier,kind,node);}catch(error){if(current())throw error;}},'answer-status');});
  if(!delivered)form.querySelectorAll('input,textarea').forEach(control=>control.disabled=true);
}
function renderSavedFeedback(tier,kind,node){
  const events=state.feedbackEvents.get(tier.run_id);
  if(!events){const boot=state.boot,id=familyId();api('/runs/'+encodeURIComponent(tier.run_id)+'/feedback').then(data=>{if(boot!==state.boot||id!==familyId())return;state.feedbackEvents.set(tier.run_id,data.events||[]);renderFeedback(tier,kind,node);}).catch(()=>{});return;}
  if(!events.length)return;
  const details=el('details',null,{class:'saved-feedback',open:''});details.append(el('summary',t('saved_feedback_heading')));
  details.append(el('p',t('feedback_group_note'),{class:'small muted'}));
  for(const event of events){const row=el('section',null,{class:'feedback-event'});row.append(el('p',String(event.group||'')+' · '+new Date(event.created*1000).toLocaleString(state.locale==='it'?'it-IT':'sl-SI'),{class:'small muted'}));if(event.rating)row.append(el('p',t(event.rating)));if(event.comment)row.append(el('p',event.comment));if(event.correction)row.append(el('p',t('feedback_correction')+': '+event.correction));if(event.tags?.length)row.append(el('p',event.tags.map(tag=>t('issue_'+tag)).join(' · '),{class:'small muted'}));if(event.anchor?.quote)row.append(el('blockquote',event.anchor.quote));details.append(row);}
  node.append(details);
}
function modelText(tier){if(beta()||state.family?.task!=='reference')return '';const value=savedProvider(tier);return value.model?(tier?.generation?.status==='generated'?'':t('attempted_model')+' ')+value.model+(value.backend?' · '+t(value.backend):'')+(value.reason?' · '+t(value.reason):''):'';}
function renderGenerationNotice(){const tier=state.family?.short,notice=generationNotice(tier);let key=notice;if(beta()){key=tier?.generation_status==='source_supported_partial'?'generation_source_excerpt':null;if(tier?.answer_kind==='no_support'||tier?.generation_status==='no_support')key='generation_no_support';else if(['failed','unavailable'].includes(tier?.generation_status)||jobState(tier)==='failed')key='generation_fallback';}$('generation-notice').hidden=!key;textStatus('generation-notice',key);}
function resetMatches(){sourceExplorer.reset();matchesRequest++;matches={family:null,phase:null,items:[],next:null,loaded:false,loading:false,coverage:[],returned:null};$('matches-panel').hidden=true;delete $('matches-panel').dataset.familyId;clear($('matches-list'));clear($('coverage-list'));$('matches-source').replaceChildren(el('option',t('all_loaded_sources'),{value:''}));textStatus('matches-status','');}
function renderMatches(){
  if(beta()){sourceExplorer.render();return;}
  if(!matches.family||matches.family!==familyId()){$('matches-panel').hidden=true;return;}
  $('matches-panel').hidden=false;$('matches-panel').dataset.familyId=matches.family;
  const filter=$('matches-source').value;clear($('matches-source'));$('matches-source').append(el('option',t('all_loaded_sources'),{value:''}));
  for(const group of matchGroups(matches.items))$('matches-source').append(el('option',group.records[0].source_label||sourceLabel({source_slug:group.source_slug}).label,{value:group.source_slug}));
  if([...$('matches-source').options].some(o=>o.value===filter))$('matches-source').value=filter;
  textStatus('matches-summary','matches_loaded',{count:matches.items.length,returned:Number.isInteger(matches.returned)?matches.returned:'—'});
  clear($('matches-list'));
  for(const group of matchGroups(matches.items,$('matches-source').value)){
    const section=el('section',null,{class:'match-group','data-source':group.source_slug});section.append(el('h3',group.records[0].source_label||sourceLabel({source_slug:group.source_slug}).label));
    for(const item of group.records){const record=el('article',null,{class:'match-record','data-evidence-id':item.evidence_id,'data-used':matches.phase==='pending'?'pending':String(item.used_in_answer===true)});record.append(el('p',t(matchUseLabel(item,matches.phase==='pending')),{class:'small match-use'}),el('h4',item.title),el('p',item.snippet,{class:'match-snippet'}));const fid=matches.family;record.append(localButton('inspect_original',()=>{if(familyId()===fid)return openSource(state.family.short||{},item.evidence_id,'short',{sourceKey:item.source_slug,unusedMatch:item.used_in_answer!==true});},'plain'));section.append(record);}
    $('matches-list').append(section);
  }
  if(matches.loaded&&!matches.items.length)$('matches-list').append(el('p',t('no_returned_matches')));
  else if(matches.items.length&&!$('matches-list').children.length)$('matches-list').append(el('p',t('no_loaded_matches')));
  clear($('coverage-list'));for(const source of matches.coverage){const status=['ok','no_match','partial','timeout','unavailable','unsupported','not_documentary'].includes(source.status)?source.status:'unavailable',label=source.label||source.source_label||(state.messages[state.locale]?.['retrieval_family_'+source.family]?t('retrieval_family_'+source.family):t('connected_resource'));const item=el('li',label+': '+t('retrieval_status_'+status));if(Number.isInteger(source.returned_count))item.append(document.createTextNode(' · '+t('returned_matches',{count:source.returned_count})));if(source.has_more===true||Number.isInteger(source.limit)&&source.returned_count>=source.limit)item.append(document.createTextNode(' · '+t('retrieval_limit')));$('coverage-list').append(item);}
  $('retrieval-coverage').hidden=!matches.coverage.length;
  const partial=matches.coverage.some(c=>['partial','timeout','unavailable','unsupported'].includes(c.status))||state.family?.short?.retrieval?.status==='partial',limited=matches.coverage.some(c=>c.has_more===true||Number.isInteger(c.limit)&&c.returned_count>=c.limit);
  $('retrieval-notice').hidden=!partial&&!limited;textStatus('retrieval-notice',partial?'retrieval_partial':limited?'retrieval_limited':'');
  $('matches-more').hidden=!Number.isInteger(matches.next);$('matches-more').disabled=matches.loading;
}
async function loadMatches(id,append=false){
  if(matches.loading||id!==familyId())return;const request=++matchesRequest,boot=state.boot,offset=append?matches.next:0;if(!Number.isInteger(offset)||offset<0)return;matches.loading=true;renderMatches();textStatus('matches-status','loading');
  try{const data=await api('/families/'+encodeURIComponent(id)+'/matches?offset='+offset+'&limit=20');if(request!==matchesRequest||boot!==state.boot||id!==familyId())return;
    const items=matchGroups(data.items).flatMap(g=>g.records),merged=append?[...matches.items,...items]:items;matches.items=settledMatchUsage(matchGroups(merged).flatMap(g=>g.records),state.family);matches.coverage=Array.isArray(data.coverage)?data.coverage:[];matches.returned=Number.isInteger(data.returned_count)?data.returned_count:null;matches.next=Number.isInteger(data.next_offset)&&data.next_offset>offset?data.next_offset:null;matches.loaded=true;textStatus('matches-status','');
  }catch(error){if(request===matchesRequest&&id===familyId())textStatus('matches-status','matches_unavailable');}
  finally{if(request===matchesRequest){matches.loading=false;renderMatches();}}
}
function syncMatches(family){
  if(beta()){sourceExplorer.sync(family);return;}
  if(!family.matches_available){if(matches.family!==family.answer_family_id)resetMatches();return;}
  const phase=['short','long'].some(tier=>family[tier]&&pendingStates.has(jobState(family[tier])))?'pending':'settled';
  if(matches.family!==family.answer_family_id){resetMatches();matches.family=family.answer_family_id;matches.phase=phase;loadMatches(matches.family);}
  else{const settledNow=matches.phase==='pending'&&phase==='settled';matches.phase=phase;matches.items=settledMatchUsage(matches.items,family);renderMatches();if(settledNow&&!matches.loaded&&!matches.loading)loadMatches(matches.family);}
}

function renderLexical(family) {
  let node=$('lexical-profile');if(!node){node=el('section',null,{id:'lexical-profile',class:'lexical-profile'});$('short-answer').after(node);}
  clear(node);const p=family.lexical_profile||family.short?.lexical_profile;node.hidden=!p?.sections?.length;if(node.hidden)return;
  node.append(el('h3',p.title),el('p',p.note,{class:'small muted'}));
  for(const section of p.sections){const box=el('details');box.append(el('summary',section.title));for(const row of section.items){const item=el('article',null,{class:'lexical-entry'});item.append(el('h4',row.title),el('p',row.text),el('small',row.label));for(const special of row.items||[])item.append(el('p',special.title+': '+special.text,{class:'lexical-alternative'}));if(row.details?.length){const detail=el('details');detail.append(el('summary',state.locale==='it'?'Dettagli della fonte':'Podrobnosti vira'));for(const field of row.details)detail.append(el('p',field.title+': '+field.text));item.append(detail);}if(row.forms?.length){const forms=el('details');forms.append(el('summary',state.locale==='it'?'Paradigma completo':'Celotno pregibanje'));for(const form of row.forms)forms.append(el('p',form.title+': '+form.text));item.append(forms);}if(row.patterns?.length){const patterns=el('details');patterns.append(el('summary',state.locale==='it'?'Schemi registrati nella fonte':'Vzorci, zapisani v viru'));for(const pat of row.patterns){const group=el('details');group.append(el('summary',pat.title));for(const form of pat.items)group.append(el('p',form.title+': '+form.text));patterns.append(group);}item.append(patterns);}box.append(item);}node.append(box);}
}
function renderFamily(family) {
  family=ordinaryFamilyView(family);state.longOpen=false;
  if(family.answer_family_id!==familyId()||family.short?.run_id!==state.family?.short?.run_id||family.long?.run_id!==state.family?.long?.run_id)clearFeedbackContext();
  if(['clarification','clarification_required'].includes(family.short?.answer_kind)){const language=family.short?.clarification_code==='input_language_required';showClarification({code:language?'input_language_required':'focus_required',fields:[language?'input_language':'focus']});return;}
  clearClarification();
  state.family=family;$('answer-model').textContent=modelText(family.short);$('long-model').textContent=modelText(family.long);$('answer-card').hidden=false;$('empty-state').hidden=true;$('new-question').hidden=false;$('revision-notice').hidden=!family.revised&&!family.short?.supersedes_run_id;$('locale-note').hidden=family.locale===state.locale;
  renderGenerationNotice();syncMatches(family);renderLexical(family);$('copy-answer').hidden=!acceptedStates.has(jobState(family.short))||!tierBlocks(family.short).length;
  if(acceptedStates.has(jobState(family.short))||['rejected','failed','cancelled'].includes(jobState(family.short))){renderText(family.short,'short',$('short-answer'));sourceButtons(family.short,'short',$('short-sources'));renderFeedback(family.short,'short',$('short-feedback'));acknowledgeDelivery(family.short,'short');}else{clear($('short-answer'));clear($('short-sources'));clear($('short-feedback'));}
  $('expand-button').disabled=!acceptedStates.has(jobState(family.short))||isCpuTask(family.task)||jobState(family.short)==='no_hit'||(family.short?.historical_evidence&&!family.long);$('compare-button').disabled=$('expand-button').disabled||Boolean(family.short?.historical_evidence)||state.capabilities.comparisons===false;$('compare-button').hidden=state.capabilities.comparisons===false;
  $('long-section').hidden=true;$('expand-button').hidden=true;$('expand-button').setAttribute('aria-expanded','false');$('expand-button').querySelector('span').textContent=t(state.longOpen?'collapse_long':'expand_long');
  if(state.longOpen&&acceptedStates.has(jobState(family.long))){textStatus('long-status','');renderText(family.long,'long',$('long-answer'));sourceButtons(family.long,'long',$('long-sources'));renderFeedback(family.long,'long',$('long-feedback'));acknowledgeDelivery(family.long,'long');}else if(state.longOpen){clear($('long-answer'));clear($('long-sources'));clear($('long-feedback'));textStatus('long-status',pendingStates.has(jobState(family.long))?'generating_long':jobState(family.long)==='not_requested'?'':'answer_guard_failed');}
  if(beta()){$('long-section').hidden=true;$('locale-note').hidden=true;$('compare-button').hidden=true;$('matches-panel').hidden=true;
    $('expand-button').disabled=!family.matches_available;$('expand-button').hidden=!family.matches_available;sourceExplorer.render();
    $('answer-card').classList.toggle('answer-pending',pendingStates.has(jobState(family.short)));}
  $('ask-button').disabled=pendingStates.has(jobState(family.short));
  $('followup-button').hidden=!beta()||family.task!=='reference'||pendingStates.has(jobState(family.short));
  $('retry-answer').hidden=!beta()||!['failed','cancelled'].includes(jobState(family.short));
  setURL();
}
async function loadFamily(id) {const request=++familyRequest,boot=state.boot;if(id!==familyId()){clearFeedbackContext();state.followup=null;$('followup-context').hidden=true;}const data=unwrapFamily(await api('/families/'+encodeURIComponent(id)));if(request!==familyRequest||boot!==state.boot)return null;$('question').value=data.question;$('focus').value=data.focus||'';$('task').value=data.task;$('input-language').value=['sl','it'].includes(data.input_language)?data.input_language:'';taskControls();state.longOpen=false;renderFamily(data);for(const tier of ['short','long'])if(pendingStates.has(jobState(data[tier]))&&data[tier]?.job_id)pollJob(data[tier].job_id,id,tier);return data;}
async function loadFeedback(){const boot=state.boot;const data=await api('/feedback');if(boot!==state.boot)return;for(const f of data.feedback||data.items||data||[])if(f.run_id)state.feedback.set(f.run_id,f);state.feedbackEvents.clear();}
function setJob(job,tier) {
  const waitStarted=state.job?.job_id===job.job_id?state.job.waitStarted:performance.now();state.job={...job,tier,waitStarted};
  $('job-state').hidden=false;$('job-state').dataset.stage=job.progress?.stage||jobState(job);$('job-state').setAttribute('aria-busy',String(pendingStates.has(jobState(job))));
  $('job-elapsed').textContent=Number.isFinite(job.elapsed_seconds)?t('elapsed_time',{seconds:Math.floor(job.elapsed_seconds)}):'';
  $('cancel-job').hidden=!pendingStates.has(jobState(job))||job.cancel_requested===true;$('retry-job').hidden=true;
  let message=t(progressLabel(job,state.family?.task,tier));
  if(job.position&&!beta())message=t('queued_position',{position:job.position});
  const model=job.model||state.family?.model,observed=state.capabilities.generation?.models?.[model];
  if(!beta()&&model&&state.family?.task==='reference'){message+=' · '+t(modelChoiceKey(model));if(observed?.device==='cpu')message+=' — '+t('cpu_slower');}
  message=longerWaitMessage(state.job,waitStarted,performance.now(),beta()?sourceExplorer.hasResults:matches.items.length>0,t)||message;
  if($('job-message').textContent!==message)$('job-message').textContent=message;
}
async function pollJob(id,idFamily,tier) {
  if(state.polling.has(id))return;setJob({job_id:id,state:'queued'},tier);let cancelled=false,failures=0;const started=performance.now(),boot=state.boot;
  const current=()=>!cancelled&&boot===state.boot&&idFamily===familyId();
  state.polling.set(id,()=>{cancelled=true;});
  try{while(current()){
    try{
      const data=await api('/jobs/'+encodeURIComponent(id)),job=data.job||data;if(!current())return;failures=0;setJob({...job,job_id:id},tier);
      if((job.progress?.matches_ready===true||['generation','validation'].includes(job.progress?.stage))&&!state.family?.matches_available){
        const family=unwrapFamily(await api('/families/'+encodeURIComponent(idFamily)));if(!current())return;renderFamily(family);
      }
      if(!pendingStates.has(jobState(job))){
        await loadFeedback();if(!current())return;
        const family=unwrapFamily(await api('/families/'+encodeURIComponent(idFamily)));if(!current())return;renderFamily(family);
        if(state.job?.job_id===id){$('job-state').hidden=true;state.job=null;}break;
      }
    }catch(error){if(!current())return;if(error.status===401||error.status===404)throw error;failures++;textStatus('job-message','reconnecting');$('retry-job').hidden=false;}
    if(pollDecision({failures,started,now:performance.now()})==='pause'){textStatus('job-message','poll_paused');$('retry-job').hidden=false;break;}
    await new Promise(resolve=>setTimeout(resolve,1200));
  }}catch(error){if(current())$('global-status').textContent=errorText(error);}finally{state.polling.delete(id);}
}
async function submitQuestion(button,overrides={}) {
  if(state.submitting)return;
  if(pendingStates.has(jobState(state.family?.short))&&state.family&&!overrides.parent_run_id){textStatus('global-status','question_pending');return;}
  const question=overrides.question||$('question').value;if(!question.trim()){textStatus('global-status','question_required');$('question').focus();return;}
  state.submitting=true;
  try{await action(button,async()=>{
    familyRequest++;textStatus('global-status','');clearFeedbackContext();resetMatches();for(const stop of state.polling.values())stop();state.polling.clear();state.job=null;state.longOpen=false;state.selection=null;state.comparison=null;state.family=null;$('comparison').hidden=true;$('answer-card').hidden=true;setURL();
    const locale=overrides.locale||$('answer-locale').value;
    let payload={question,locale,interface_locale:state.locale,input_language:['text_check','morphology','reference'].includes($('task').value)&&['sl','it'].includes($('input-language').value)?$('input-language').value:'und',model:overrides.model||$('model').value,task:$('task').value};
    if(beta()){delete payload.model;payload.task='reference';}
    if($('task').value==='reference'&&$('focus').value.trim())payload.focus=$('focus').value.trim();
    if(state.followup){payload.parent_run_id=state.followup.run_id;payload.parent_relation='followup';}
    if(overrides.parent_run_id){payload.parent_run_id=overrides.parent_run_id;payload.parent_relation=overrides.parent_relation;}
    if(overrides.context)payload.context=overrides.context;
    payload=submission.prepare(state.user.id,payload,()=>crypto.randomUUID());
    textStatus('job-message','checking_request');$('job-state').hidden=false;$('empty-state').hidden=true;
    try{
      const result=await api('/families',{method:'POST',body:payload});submission.clear();state.followup=null;$('followup-context').hidden=true;
      if(result.status==='clarification_required'){showClarification(result.clarification);return;}
      renderFamily(unwrapFamily(result));const job=result.job;const id=job?.job_id||job?.id||state.family?.short?.job_id;if(id)pollJob(id,familyId(),'short');else $('job-state').hidden=true;
    }catch(error){
      $('job-state').hidden=true;
      if(error.code==='input_language_required'){showClarification({code:error.code,fields:['input_language']});return;}
      if(error.code==='generation_waiting_capacity'){clearClarification();$('capacity-state').hidden=beta();textStatus(beta()?'global-status':'capacity-message','generation_waiting_capacity');return;}
      if(error.status&&error.status<500&&error.status!==401)submission.clear();
      throw error;
    }
  });}finally{state.submitting=false;if(state.family&&pendingStates.has(jobState(state.family.short)))$('ask-button').disabled=true;}
}
function expand() {
  // Source browsing never creates another answer or generation request.
  if(beta()&&state.family?.matches_available)sourceExplorer.toggle();
}

function appendCurrentOriginalAction(data,targetFamily,id,kind,current) {
  if(!canOpenCurrentOriginal(data))return;
  const section=el('section',null,{class:'source-current-original'}),output=el('div');
  const button=localButton('current_original_open',async()=>{
    button.disabled=true;clear(output);output.append(el('p',t('loading')));
    try {
      const latest=await api('/families/'+encodeURIComponent(targetFamily)+'/sources/'+encodeURIComponent(id)+'/current-original?tier='+encodeURIComponent(kind));
      if(!current())return;
      if(latest.original_view!=='supplementary_current'||typeof latest.excerpt!=='string')throw Error();
      clear(output);output.append(el('h3',t('current_original_heading')),el('p',t('current_original_notice'),{class:'source-qualifier'}));
      if(latest.title)output.append(el('h4',latest.title));
      output.append(el('blockquote',latest.excerpt));
      if(latest.bibliography?.source_date)output.append(el('p',String(latest.bibliography.source_date),{class:'source-locator'}));
      const link=safeSourceLink(latest.url);if(link)output.append(el('a',t('open_original'),{href:link,target:'_blank',rel:'noopener noreferrer',class:'plain'}));
    }catch(error){if(!current())return;clear(output);output.append(el('p',t('current_original_unavailable'),{class:'error'}));button.disabled=false;}
  },'plain');
  section.append(el('p',t('current_original_offer'),{class:'source-qualifier'}),button,output);$('source-content').append(section);
}
async function openSource(tier,id,kind,reference={}) {
  if(!reference.comparisonId)kind=tier?.source_tier || kind;
  const request=++sourceRequest,boot=state.boot,targetFamily=familyId();
  const current=()=>request===sourceRequest&&boot===state.boot&&targetFamily===familyId()&&$('source-dialog').open;
  clear($('source-content'));$('source-title').textContent=t('source_detail');$('source-content').append(el('p',t('loading')));showDialog('source-dialog');
  try{let data;if(reference.comparisonId)data=await api('/comparisons/'+encodeURIComponent(reference.comparisonId)+'/sources/'+encodeURIComponent(id)+'?side='+encodeURIComponent(reference.side)+'&tier='+kind);else if(id)data=await api('/families/'+encodeURIComponent(familyId())+'/sources/'+encodeURIComponent(id)+'?tier='+kind);else throw Error();
    if(!current())return;data=data.source||data;clear($('source-content'));$('source-title').textContent=data.source_label||data.label||sourceLabel({source_key:reference.sourceKey}).label;
    if(data.title)$('source-content').append(el('h3',data.title));
    const loc=readableLocator(data.locator,{page:t('source_page'),topic:t('source_topic')});if(loc)$('source-content').append(el('p',loc,{class:'source-locator'}));
    if(data.edition&&(!beta()||/^\d{4}$/.test(String(data.edition))))$('source-content').append(el('p',t('edition')+' '+String(data.edition),{class:'source-locator'}));
    for(const note of sourceStatusNotes(data))$('source-content').append(el('p',t(note.key,note.values),{class:'source-qualifier'}));
    const excerpt=sourceExcerptPresentation(data);
    if(data.reader_blocks?.length)appendSourceReaderBlocks($('source-content'),data.reader_blocks,el);else $('source-content').append(el('blockquote',excerpt.text||t('source_unavailable')));
    if(tier.run_id&&!reference.comparisonId&&(beta()?citedEvidenceIds(tier).has(id)&&state.delivered.has(tier.run_id+':'+(kind==='long')):!reference.unusedMatch))$('source-content').append(localButton('mark_error',()=>{$('source-dialog').close();openFeedback(tier,'short',{kind:'source',evidence_id:id});},'plain'));
    const link=safeSourceLink(data.url||data.original_url);if(link){const landing=new URL(link).pathname==='/'||/\/(?:loris|zvezek|jezikovna-posta|zkb|glosar)\/?$/.test(new URL(link).pathname);$('source-content').append(el('a',t(landing?'open_collection':'open_original'),{href:link,target:'_blank',rel:'noopener noreferrer',class:'source-external-link'}));$('source-content').append(el('p',t('frozen_source_note'),{class:'small muted'}));}else $('source-content').append(el('p',t('no_public_entry'),{class:'small muted'}));
    const selectedEvidence=evidenceFor(tier).find(item=>evidenceId(item)===id),related=evidenceFor(tier).filter(item=>sourceKey(item)===sourceKey(selectedEvidence));
    if(selectedEvidence&&related.length>1){const more=el('details'),summary=el('summary',t('other_used_passages'));more.append(summary);for(const [index,item]of related.entries()){const choice=el('button',t('source_number',{number:item.local_number||item.number||index+1}),{type:'button',class:'plain'});choice.addEventListener('click',()=>openSource(tier,evidenceId(item),kind,reference));choice.disabled=evidenceId(item)===id;more.append(choice);}$('source-content').append(more);}
    if(beta()&&reference.recordEvidenceIds?.length>1){const parts=el('div',null,{class:'record-parts'});parts.append(el('p',t('beta_record_parts'),{class:'small muted'}));for(const [index,target] of reference.recordEvidenceIds.entries()){const choice=el('button',t('beta_record_part',{number:index+1}),{type:'button',class:'plain','data-record-part':target});choice.disabled=target===id;choice.addEventListener('click',()=>openSource(tier,target,kind,reference));parts.append(choice);}$('source-content').append(parts);}
    if(beta()&&data.bibliography){const bib=data.bibliography,authors=Array.isArray(bib.authors)?bib.authors.filter(a=>typeof a==='string'):[],dates=Array.isArray(bib.signed_date_text)?bib.signed_date_text:[bib.source_date||bib.date].filter(Boolean);if(authors.length)$('source-content').append(el('p',authors.join(', '),{class:'source-locator'}));if(dates.length)$('source-content').append(el('p',dates.join('; '),{class:'source-locator'}));}
    if(data.bibliography&&!beta()){const details=el('details',null,{class:'source-bibliography'}),summary=el('summary',t('source_bibliography'));details.append(summary);const bib=typeof data.bibliography==='string'?data.bibliography:Object.entries(data.bibliography).map(([key,value])=>key+': '+String(value)).join('\n');details.append(el('p',bib));if(excerpt.attributionHidden){details.append(el('p',t('original_source_excerpt')));details.append(el('pre',excerpt.original,{class:'source-original'}));}$('source-content').append(details);}
    if(!reference.comparisonId)appendCurrentOriginalAction(data,targetFamily,id,kind,current);
  }catch(error){if(!current())return;clear($('source-content'));$('source-content').append(el('p',t('source_unavailable'),{class:'error'}));}
}
function captureSelection() {
  const selection=getSelection();if(!selection||selection.isCollapsed||!selection.rangeCount)return;const range=selection.getRangeAt(0),start=range.startContainer.nodeType===1?range.startContainer:range.startContainer.parentElement,end=range.endContainer.nodeType===1?range.endContainer:range.endContainer.parentElement,block=start.closest('[data-block-id]');if(!block||end.closest('[data-block-id]')!==block)return;
  const before=range.cloneRange();before.selectNodeContents(block);before.setEnd(range.startContainer,range.startOffset);const startOffset=before.toString().length;const anchor=anchorForText(block.dataset.blockId,block.textContent,startOffset,startOffset+range.toString().length);if(anchor)state.selection={run_id:block.dataset.runId,tier:block.dataset.tier,anchor};
}
function openFeedback(tier,kind,target={kind:'answer'}) {
  const selection=target.kind==='answer'&&state.selection?.run_id===tier.run_id?state.selection.anchor:null;state.feedbackTarget={tier,kind,anchor:selection,target,key:crypto.randomUUID()};$('feedback-target').textContent=t(target.kind==='source'?'feedback_source':kind==='long'?'feedback_long':'feedback_short');$('feedback-selection').hidden=!selection;$('feedback-selection').textContent=selection?.quote||'';$('feedback-form').reset();textStatus('feedback-status','');showDialog('feedback-dialog');
}
async function saveFeedback(button) {
  const target=state.feedbackTarget,boot=state.boot,id=familyId();if(!target)return;
  const current=()=>boot===state.boot&&id===familyId()&&state.feedbackTarget===target;
  await action(button,async()=>{try{
    const old=state.feedback.get(target.tier.run_id),body={run_id:target.tier.run_id,rating:beta()?$('beta-feedback-rating').querySelector('input:checked')?.value||null:old?.rating||null,quality_score:old?.quality_score??null,display_locale:state.locale,tags:Array.from($('feedback-tags').querySelectorAll('input:checked')).map(x=>x.value),comment:$('feedback-comment').value,proposed_correction:$('feedback-correction').value,target:target.target,idempotency_key:target.key};
    if(target.anchor)body.anchor=target.anchor;if(old?.feedback_id&&target.target.kind==='answer')body.feedback_id=old.feedback_id;
    const result=await api('/feedback',{method:'POST',body});if(!current())return;if(!result.acknowledged)throw Error();
    state.feedback.set(body.run_id,{...body,...result});state.feedbackEvents.delete(body.run_id);
    $('feedback-dialog').close();state.feedbackTarget=null;renderFeedback(target.tier,target.kind,$(target.kind+'-feedback'));textStatus('answer-status','feedback_saved');
  }catch(error){if(current())throw error;}},'feedback-status');
}
async function historyList() {
  const list=$('history-list');
  clear(list);
  list.append(el('p',t('loading'),{class:'small muted','data-i18n':'loading',role:'status'}));
  await action(null,async()=>{
    textStatus('global-status','');
    const data=await api('/families');
    clear(list);
    const families=data.families||data.items||data;
    if(!families.length){const key=state.user?.shared_group===true?'shared_history_empty':'history_empty';list.append(el('p',t(key),{class:'small muted','data-i18n':key}));return;}
    for(const family of families){
      const b=el('button',family.question,{type:'button',class:'history-item'});
      const created=historyCreatedDate(family);
      if(created)b.append(el('small',created.toLocaleString(state.locale==='it'?'it-IT':'sl-SI')));
      b.addEventListener('click',async()=>{const buttons=[...list.querySelectorAll('button.history-item')];for(const button of buttons)button.disabled=true;try{await action(null,async()=>{state.longOpen=false;state.comparison=null;$('comparison').hidden=true;const loaded=await loadFamily(family.answer_family_id);if(!loaded)return;$('history').hidden=true;$('history-toggle').setAttribute('aria-expanded','false');$('answer-card').scrollIntoView({block:'start'});});}finally{for(const button of buttons)button.disabled=false;}});
      list.append(b);
    }
  });
}
function toggleHistory(){const open=$('history').hidden;$('history').hidden=!open;$('history-toggle').setAttribute('aria-expanded',String(open));if(open)historyList();}
async function startComparison(blind,button) {await action(button,async()=>{$('compare-dialog').close();state.comparison=null;setURL();$('comparison').hidden=false;$('comparison-reveal').hidden=true;$('comparison-expand').disabled=true;$('comparison-votes').querySelectorAll('button').forEach(b=>b.disabled=true);for(const side of ['a','b']){clear($('side-'+side+'-answer'));clear($('side-'+side+'-sources'));$('side-'+side+'-title').textContent=t('answer_'+side);}textStatus('comparison-status','comparison_pending');const data=await api('/comparisons',{method:'POST',body:{family_id:familyId(),blind,tier:'short',idempotency_key:keyFor('compare:'+familyId()+':'+blind)}});renderComparison(data.comparison||data);pollComparison(state.comparison.comparison_id);});}
function renderComparison(data){state.comparison=data;$('comparison').dataset.tier=data.tier;$('comparison').hidden=false;$('blind-notice').hidden=!data.blind;$('comparison-reveal').hidden=!data.blind||data.revealed||!data.vote;let pending=false;
  for(const side of ['A','B']){const tier=data.sides?.[side];const lower=side.toLowerCase();$('side-'+lower+'-title').textContent=(!data.blind||data.revealed)&&tier?.model?(tier.model==='qwen'?'Qwen':tier.model==='gemma'?'Gemma':String(tier.model)):t(side==='A'?'answer_a':'answer_b');if(pendingStates.has(jobState(tier))){pending=true;clear($('side-'+lower+'-answer'));clear($('side-'+lower+'-sources'));$('side-'+lower+'-answer').append(el('p',t('queued')));}else{renderText(tier,data.tier,$('side-'+lower+'-answer'),{comparisonId:data.comparison_id,side});sourceButtons(tier,data.tier,$('side-'+lower+'-sources'),{comparisonId:data.comparison_id,side});}}
  $('comparison-votes').querySelectorAll('button').forEach(b=>{b.disabled=pending;b.setAttribute('aria-pressed',String((data.vote?.choice||data.vote)===b.dataset.vote));});$('comparison').dataset.ready=String(!pending);$('comparison-expand').disabled=pending||data.tier==='long'||state.capabilities.comparisons===false;textStatus('comparison-status',pending?'comparison_pending':data.vote?'comparison_vote_saved':'');setURL();
  const deliveryKey='comparison:'+data.comparison_id+':'+data.tier;
  if(!pending&&!state.delivered.has(deliveryKey)){
    $('comparison-votes').querySelectorAll('button').forEach(b=>b.disabled=true);
    api('/comparisons/'+encodeURIComponent(data.comparison_id)+'/delivered',{method:'POST',body:{tier:data.tier,expanded:data.tier==='long'}}).then(()=>{state.delivered.add(deliveryKey);if(state.comparison?.comparison_id===data.comparison_id&&state.comparison.tier===data.tier)$('comparison-votes').querySelectorAll('button').forEach(b=>b.disabled=false);}).catch(()=>textStatus('comparison-status','comparison_unavailable'));
  }
}
async function loadComparison(id){const d=await api('/comparisons/'+encodeURIComponent(id));renderComparison(d.comparison||d);pollComparison(id);}
async function pollComparison(id){const key='comparison:'+id;if(state.polling.has(key))return;let cancelled=false;state.polling.set(key,()=>{cancelled=true;});try{while(!cancelled&&state.comparison?.comparison_id===id){if(!Object.values(state.comparison.sides||{}).some(v=>pendingStates.has(jobState(v))))break;await new Promise(r=>setTimeout(r,1600));if(cancelled)break;const data=await api('/comparisons/'+encodeURIComponent(id));if(cancelled||state.comparison?.comparison_id!==id)return;renderComparison(data.comparison||data);}}catch(error){textStatus('comparison-status','comparison_unavailable');}finally{state.polling.delete(key);}}
async function vote(button){await action(button,async()=>{const body={choice:button.dataset.vote,tier:state.comparison.tier,expanded:state.comparison.tier==='long',idempotency_key:keyFor('vote:'+state.comparison.comparison_id+':'+state.comparison.tier+':'+button.dataset.vote)};await api('/comparisons/'+encodeURIComponent(state.comparison.comparison_id)+'/vote',{method:'POST',body});await loadComparison(state.comparison.comparison_id);},'comparison-status');}
function revealPasswordForm(onSubmit,titleKey='admin_reauth') {const form=el('form',null,{class:'admin-form'}),label=el('label',t(titleKey)),input=el('input',null,{type:'password',autocomplete:'current-password',required:'required',maxlength:'1024'}),submit=localButton('save',()=>{},'primary');submit.type='submit';label.append(input);form.append(label,submit);form.addEventListener('submit',event=>{event.preventDefault();action(submit,async()=>{const value=input.value;input.value='';await onSubmit(value);},'admin-status');});return form;}
async function adminView(tab='overview') {showDialog('admin-dialog');clear($('admin-content'));textStatus('admin-status','loading');try{const data=await api('/admin/'+(tab==='overview'?'overview':tab));clear($('admin-content'));textStatus('admin-status','');
    if(tab==='overview'){const heading=el('h3',t('admin_week'));$('admin-content').append(heading);const status=data.status||data.analysis?.status||data.latest_cycle?.status||data.cycles?.[0]?.status;const statusMap={INSUFFICIENT_DATA:'admin_insufficient',ANALYSIS_ONLY_NO_VALIDATED_PROMOTION:'admin_analysis_only',DEFERRED_CAPACITY:'admin_deferred',ANALYSIS_COMPLETE:'admin_analysis_complete',CANARY_PENDING:'admin_canary_pending',running:'admin_running',complete:'admin_completed',failed:'admin_failed'};if(status)$('admin-content').append(el('p',t(statusMap[status]||'admin_pending')));if(data.summary?.[state.locale]||data.summary)$('admin-content').append(el('p',typeof data.summary==='string'?data.summary:data.summary[state.locale]||''));const changes=data.changes||data.changelog||[];if(data.counts){const counts=el('dl',null,{class:'admin-counts'});for(const name of ['accounts','families','feedback','jobs'])if(Number.isInteger(data.counts[name]))counts.append(el('dt',t('admin_count_'+name)),el('dd',data.counts[name]));$('admin-content').append(counts);}for(const cycle of data.cycles||[]){const row=el('p');row.append(el('strong',t(statusMap[cycle.status]||'admin_pending')),document.createTextNode(' · '+new Date(cycle.created*1000).toLocaleString(state.locale)));$('admin-content').append(row);}for(const change of changes)$('admin-content').append(el('p',change[state.locale]||change.summary||change.title||String(change)));if(data.next_run)$('admin-content').append(el('p',t('admin_next')+': '+new Date(data.next_run).toLocaleString(state.locale)));if(!status&&!changes.length)$('admin-content').append(el('p',t('admin_no_data')));
      if(data.human_testing_mode)$('admin-content').append(el('p',t('human_testing_deferred')));if(data.human_feedback_summary){const q=data.human_feedback_summary;$('admin-content').append(el('p',t('human_quality_summary',{count:q.rated_answers,mean:q.mean===null?'—':q.mean.toFixed(2)})),el('p',Object.entries(q.histogram).map(([score,count])=>score+': '+count).join(' · ')));}if(state.user.role==='admin'&&!data.human_testing_mode){const controls=el('div',null,{class:'admin-controls'});controls.append(localButton(data.paused?'admin_resume':'admin_pause',async()=>{await api('/admin/pause',{method:'POST',body:{paused:!data.paused}});await adminView('overview');},'secondary'),localButton('admin_revert',()=>{$('admin-content').append(revealPasswordForm(async password=>{await api('/admin/revert',{method:'POST',body:{reauth_password:password}});textStatus('admin-status','operation_saved');}));},'secondary'));$('admin-content').append(controls);}}
    else if(tab==='feedback'){renderAdminFeedback(data);}
    else if(tab==='accounts'){const accounts=data.accounts||data.items||data;for(const account of accounts){const record=el('article',null,{class:'admin-record'});record.append(el('strong',account.username),el('p',t('role_'+account.role)+(account.disabled||account.enabled===false||account.enabled===0?' · '+t('admin_disabled'):'')));if(state.user.role==='admin'){record.append(localButton('admin_disable',()=>{record.append(revealPasswordForm(async password=>{await api('/admin/accounts/'+encodeURIComponent(account.id)+'/disable',{method:'POST',body:{disabled:true,reauth_password:password}});await adminView('accounts');}));},'plain'));record.append(localButton('admin_reset',()=>accountForm(record,account),'plain'));}$('admin-content').append(record);}if(state.user.role==='admin')accountForm($('admin-content'));}
  }catch(error){$('admin-status').textContent=errorText(error);}
}
function renderAdminFeedback(data){const records=data.feedback||[];for(const item of records){const record=el('article',null,{class:'admin-record'}),o=item.observation;record.append(el('strong',Number.isInteger(item.quality_score)?t('quality_result',{score:item.quality_score}):item.rating?t(item.rating):t('rating_none')),el('p',item.comment||''));if(o){record.append(el('p',t(o.human_eligible?'human_record':'synthetic_record')),el('p',o.participant.username+' · '+o.participant.id+' · '+new Date(o.timestamp*1000).toLocaleString(state.locale)),el('h4',t('original_prompt')),el('pre',o.original_prompt,{class:'admin-data'}),el('h4',t('displayed_answer')),el('pre',o.displayed_answer,{class:'admin-data'}),el('p',(o.selected_model||'')+' · '+(o.execution?.device||'unknown')+' · '+o.answer_language+' · '+t(o.tier==='long'?'long_heading':'short_heading')));const versions=el('details'),summary=el('summary',t('record_versions'));versions.append(summary,el('pre',JSON.stringify({actual_model:o.actual_model,versions:o.versions,display_sha256:o.display_sha256,display_version:o.display_version},null,2),{class:'admin-data'}));record.append(versions);}if(item.anchor?.quote)record.append(el('blockquote',item.anchor.quote));if(item.tags?.length)record.append(el('p',item.tags.map(tag=>t('issue_'+tag)).join(' · ')));$('admin-content').append(record);}if(!records.length)$('admin-content').append(el('p',t('admin_empty')));if(state.user.role==='admin')for(const format of ['json','csv'])$('admin-content').append(localButton(format==='json'?'admin_export_json':'admin_export_csv',()=>{$('admin-content').append(el('p',t('admin_export_hint')),revealPasswordForm(async password=>{const data=await api('/admin/export',{method:'POST',body:{reauth_password:password,format},response:format==='csv'?'text':undefined});download(data,'klikai-pilot-feedback.'+format,format==='csv');}));},'secondary'));}

function accountForm(parent,account=null){const form=el('form',null,{class:'admin-form'});form.append(el('h3',t(account?'admin_reset':'admin_create')));const fields={};for(const [name,label,type]of [...(account?[]:[['username','username','text']]),['temporary_password','temporary_password','password'],['reauth_password','admin_reauth','password']]){const wrap=el('label',t(label)),input=el('input',null,{type,required:'required',autocomplete:name==='reauth_password'?'current-password':type==='password'?'new-password':'off',maxlength:'1024'});fields[name]=input;wrap.append(input);form.append(wrap);}if(!account){const label=el('label',t('role')),select=el('select');for(const role of ['tester','reviewer','admin'])select.append(el('option',t('role_'+role),{value:role}));fields.role=select;label.append(select);form.append(label);}const button=el('button',t('save'),{type:'submit',class:'primary'});form.append(button);form.addEventListener('submit',event=>{event.preventDefault();action(button,async()=>{const body=Object.fromEntries(Object.entries(fields).map(([k,input])=>[k,input.value]));for(const field of Object.values(fields))if(field.type==='password')field.value='';await api(account?'/admin/accounts/'+encodeURIComponent(account.id)+'/reset':'/admin/accounts',{method:'POST',body});await adminView('accounts');textStatus('admin-status','operation_saved');},'admin-status');});parent.append(form);}
function download(data,name,raw=false){const blob=new Blob([raw?data:JSON.stringify(data,null,2)],{type:raw?'text/csv;charset=utf-8':'application/json'}),url=URL.createObjectURL(blob),a=el('a','',{href:url,download:name});document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}

function bind() {
  document.querySelectorAll('[data-locale]').forEach(a=>a.addEventListener('click',event=>{event.preventDefault();setLocale(a.dataset.locale);}));window.addEventListener('popstate',()=>setLocale(location.pathname.endsWith('/it/')?'it':'sl',false));
  document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));
  $('login-form').addEventListener('submit',()=>{const current=new URL(location.href);$('login-locale').value=state.locale;$('login-answer').value=signInResume?.answer||current.searchParams.get('answer')||'';$('login-comparison').value=signInResume?.comparison||current.searchParams.get('comparison')||'';try{if(signInResume?.question)sessionStorage.setItem(loginDraftKey,signInResume.question);}catch{}textStatus('login-error','');$('login-form').setAttribute('aria-busy','true');});
  $('password-visibility').addEventListener('click',()=>{const show=$('password').type==='password';$('password').type=show?'text':'password';$('password-visibility').textContent=t(show?'hide_password':'show_password');});
  $('password-form').addEventListener('submit',event=>{event.preventDefault();if($('new-password').value!==$('confirm-password').value){textStatus('password-error','password_mismatch');return;}action(event.submitter,async()=>{await api('/password',{method:'POST',body:{current_password:$('current-password').value,new_password:$('new-password').value}});$('password-form').reset();authenticated(await api('/session'));textStatus('global-status','password_changed');},'password-error');});
  $('password-cancel').addEventListener('click',()=>{if(state.user&&!state.user.must_change_password){$('password-view').hidden=true;$('app-view').hidden=false;}});
  $('account-button').addEventListener('click',()=>showDialog('account-dialog'));$('account-password').addEventListener('click',()=>{$('account-dialog').close();$('app-view').hidden=true;$('password-view').hidden=false;$('password-cancel').hidden=!!state.user.must_change_password;});$('account-history').addEventListener('click',()=>{$('account-dialog').close();$('history').hidden=true;toggleHistory();});$('account-admin').addEventListener('click',()=>{$('account-dialog').close();adminView(state.user.role==='reviewer'?'feedback':'overview');});
  $('beta-logout').addEventListener('click',()=>$('logout-button').click());
  $('logout-button').addEventListener('click',event=>action(event.currentTarget,async()=>{await api('/logout',{method:'POST'});submission.clear();resetPrivate();await bootstrap();}));
  $('question-form').addEventListener('submit',event=>{event.preventDefault();submitQuestion(event.submitter);});$('question').addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){event.preventDefault();$('question-form').requestSubmit($('ask-button'));}});
  $('question').addEventListener('input',()=>{$('focus').value='';clearClarification();});
  $('task').addEventListener('change',()=>{clearClarification();taskControls();});$('model').addEventListener('change',()=>{modelControls();capacityControls();});$('answer-locale').addEventListener('change',()=>{state.answerLocaleExplicit=true;});taskControls();
  $('capacity-models').addEventListener('click',()=>{$('options').hidden=false;$('options-toggle').setAttribute('aria-expanded','true');$('model').focus();});$('capacity-sources').addEventListener('click',()=>{$('task').value='source_search';clearClarification();taskControls();$('ask-button').focus();});
  $('capacity-tools').addEventListener('click',()=>{$('task').value='terminology';clearClarification();taskControls();$('question').focus();});
  $('matches-source').addEventListener('change',renderMatches);$('matches-more').addEventListener('click',()=>loadMatches(familyId(),true));
  $('options-toggle').addEventListener('click',()=>{const open=$('options').hidden;$('options').hidden=!open;$('options-toggle').setAttribute('aria-expanded',String(open));});$('history-toggle').addEventListener('click',toggleHistory);
  $('new-question').addEventListener('click',()=>{submission.clear();state.followup=null;$('followup-context').hidden=true;$('ask-button').disabled=false;clearFeedbackContext();resetMatches();state.family=null;state.comparison=null;state.selection=null;state.longOpen=false;state.requestKeys.clear();for(const stop of state.polling.values())stop();state.polling.clear();$('answer-card').hidden=true;$('comparison').hidden=true;$('new-question').hidden=true;$('job-state').hidden=true;$('empty-state').hidden=false;$('question').value='';$('focus').value='';clearClarification();setURL();$('question').focus();});
  $('followup-button').addEventListener('click',()=>{state.followup={run_id:state.family.short.run_id,question:state.family.question};$('followup-context').hidden=false;$('followup-context').textContent=t('followup_context',{question:state.family.question});$('question').value='';$('question').focus();});
  $('retry-answer').addEventListener('click',event=>{submission.clear();submitQuestion(event.currentTarget,{question:state.family.question,context:state.family.context,parent_run_id:state.family.short.run_id,parent_relation:'retry'});});
  $('testing-help').addEventListener('click',()=>showDialog('testing-guide'));
  $('expand-button').addEventListener('click',event=>expand(event.currentTarget));$('translate-answer').addEventListener('click',event=>submitQuestion(event.currentTarget,{question:state.family.question,locale:state.locale,model:state.family.model}));
  // Keep the clipboard button enabled so copying does not drop keyboard focus.
  $('copy-answer').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(answerCopyText($('short-answer').innerText,state.family?.short,item=>sourceLabel(item).label,t('copy_sources')));textStatus('answer-status','copied');}catch{ textStatus('answer-status','copy_failed');}});
  $('cancel-job').addEventListener('click',event=>action(event.currentTarget,async()=>{const current=state.job;if(!current?.job_id)return;const result=await api('/jobs/'+encodeURIComponent(current.job_id)+'/cancel',{method:'POST'});if(state.job?.job_id===current.job_id)setJob(result.job||result,current.tier);}));$('retry-job').addEventListener('click',()=>{if(state.job?.job_id)pollJob(state.job.job_id,familyId(),state.job.tier);});
  document.addEventListener('selectionchange',captureSelection);$('feedback-form').addEventListener('submit',event=>{event.preventDefault();saveFeedback(event.submitter);});for(const tag of ['linguistic','missing_context','unsupported','source','accent','language','interface','slow','other']){const label=el('label'),input=el('input',null,{type:'checkbox',value:tag});label.append(input,el('span',t('issue_'+tag),{'data-i18n':'issue_'+tag}));$('feedback-tags').append(label);}
  $('compare-button').addEventListener('click',()=>showDialog('compare-dialog'));$('start-blind').addEventListener('click',event=>startComparison(true,event.currentTarget));$('start-named').addEventListener('click',event=>startComparison(false,event.currentTarget));$('comparison-close').addEventListener('click',()=>{$('comparison').hidden=true;});
  $('comparison-votes').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>vote(b)));$('comparison-reveal').addEventListener('click',event=>action(event.currentTarget,async()=>{const result=await api('/comparisons/'+encodeURIComponent(state.comparison.comparison_id)+'/reveal',{method:'POST'});renderComparison(result.comparison||result);},'comparison-status'));$('comparison-expand').addEventListener('click',event=>action(event.currentTarget,async()=>{$('comparison').dataset.ready='false';$('comparison-votes').querySelectorAll('button').forEach(b=>b.disabled=true);const data=await api('/comparisons/'+encodeURIComponent(state.comparison.comparison_id)+'/expand',{method:'POST',body:{idempotency_key:keyFor('comparison-expand:'+state.comparison.comparison_id)}});renderComparison(data.comparison||data);pollComparison(state.comparison.comparison_id);},'comparison-status'));
  const chooseTab=side=>{for(const other of ['a','b']){$('tab-'+other).setAttribute('aria-selected',String(side===other));$('tab-'+other).tabIndex=side===other?0:-1;$('side-'+other).dataset.mobileHidden=String(side!==other);}};for(const side of ['a','b']){$('tab-'+side).addEventListener('click',()=>chooseTab(side));$('tab-'+side).addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?'a':event.key==='End'?'b':side==='a'?'b':'a';chooseTab(next);$('tab-'+next).focus();}});}chooseTab('a');
  document.querySelectorAll('[data-admin-tab]').forEach(b=>b.addEventListener('click',()=>adminView(b.dataset.adminTab)));$('source-legend-open').addEventListener('click',()=>{renderSourceLegend();showDialog('source-legend-dialog');});$('source-legend-dialog').addEventListener('close',()=>{if(!$('source-legend-open').hidden)$('source-legend-open').focus({preventScroll:true});});
  window.addEventListener('pageshow',event=>{if(event.persisted){resetPrivate();bootstrap().catch(e=>$('global-status').textContent=errorText(e));}});
}
const sourceExplorer=createSourceExplorer({api,family:()=>state.family,pending:()=>['short','long'].some(tier=>state.family?.[tier]&&pendingStates.has(jobState(state.family[tier]))),t,element:$,label:item=>sourceLabel(item).label,icon:item=>sourceLabel(item).initials,onChange:renderWaitMessage,openOriginal:record=>openSource(state.family.short||{},record.evidence_id,'short',{sourceKey:record.source_slug,recordEvidenceIds:record.evidence_ids,unusedMatch:recordUsed(record,state.family,pendingStates.has(jobState(state.family?.short)))!==true})});

try {
  const [sl,it,sources]=await Promise.all(['sl','it','source-presentation'].map(name=>fetch(base+'/assets/'+(name==='source-presentation'?'source-presentation.json':'i18n/'+name+'.json'),{credentials:'same-origin'}).then(r=>{if(!r.ok)throw Error('Missing locale');return r.json();})));
  state.messages={sl,it};registry=sources;setLocale(state.locale,false);bind();await bootstrap();if(new URL(location.href).searchParams.get('help')==='testing')showDialog('testing-guide');
} catch(error){$('global-status').textContent=state.messages[state.locale]?errorText(error):'Povezava ni na voljo. / Connessione non disponibile.';}
