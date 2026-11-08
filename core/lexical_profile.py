"""Shared, source-backed lexical profile for direct clients and KLIKai."""
import asyncio,copy,hashlib,json,time,uuid,os
from collections import defaultdict
from typing import Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field
from starlette.concurrency import run_in_threadpool
from .lexical_planner import plan,lexical_word
from .lexical_identity import source_occurrence_key,morphology_key
from .loris_roles import loris_advice_roles
from .platform_api import AnalyzeRequest,analyze_once,AnalyzerFailure,available,bounded,public,bounded_thesaurus
CONTRACT='linghub.lexical-profile.v1'
router=APIRouter()
_ACTIVE=0
class ProfileRequest(BaseModel):
 text:str=Field(min_length=1,max_length=3000)
 context:str=Field(default='',max_length=3000)
 lang:Literal['sl','it','und']='und'
 profile:Literal['klikai_classla_primary_v1','klikai_experimental_v1','linghub_lexical_stable_v1','compare','spacy','classla']='klikai_experimental_v1'
 limit:int=Field(default=4,ge=1,le=8)
 request_plan:dict|None=None
 source_advice_captures:list[dict]=Field(default_factory=list,max_length=16)
class LexicalProfile(BaseModel):
 request_schema_version:str
 request_id:str
 query:dict
 interpretation:dict
 analysis:dict
 facets:dict
 evidence:list[dict]
 coverage:dict
 provenance:dict

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def aggregate(parts):
 for status in ('matched','unavailable','no_match'):
  if any(x['status']==status for x in parts):return status
 return 'not_applicable'

def facet(status,data=None,**extra):return {'status':status,'data':data,**extra}
def source_record(kind,entry_id,body,provenance,target,relation='recorded_target'):
 item=public({'kind':kind,'entry_id':entry_id,'target':target,'match_relation':relation,'fields':body,'provenance':provenance})
 occurrence=source_occurrence_key(provenance)if kind=='morphology'else None
 if occurrence:item['source_occurrence_key']=occurrence
 item['record_id']='lexical:'+digest(item);return item

def full_paradigm_occurrences(interpretation,lexemes):
 """Retain complete recorded paradigms for an explicitly identified class.

 A name/surname is a nominal lexical role, even when the request did not repeat
 its part of speech. This is the same nominal interpretation used by the
 source-feature answer builder; it does not choose gender or source variants.
 """
 if (interpretation.get('slot_focus') or interpretation.get('requested_grammar_slots')
     or not {'morphology','inflection','conjugation'}&set(interpretation.get('requested_facets',[]))):return set()
 choice=interpretation.get('form_choice')or(interpretation.get('request_plan')or{}).get('form_choice')or{}
 explicit=choice.get('explicit_lexemes')or interpretation.get('explicit_lexemes')or[]
 def matches(le,x):
  category=x.get('grammatical_type')
  if category is None and x.get('lexical_kind')in {'proper_name','surname','common_noun'}:category='noun'
  features=le.get('grammatical_features',{})
  return (category is not None and le['lemma']==x.get('text')and le['grammatical_type']==category
      and(not x.get('gender')or features.get('gender')==x['gender'])
      and(x.get('lexical_kind')not in {'proper_name','surname'}or features.get('type')=='proper'))
 return {morphology_key(le)for le in lexemes if any(matches(le,x)for x in explicit)}

def requested_synonym_projection(records,facets,interpretation,limit):
 """Select requested source groups after existing identity checks, before caps."""
 if 'synonyms'not in interpretation.get('requested_facets',[]):return records
 from .lexical_focus import focus_profile
 # Reuse the same identity decision with the complete loaded records. Final
 # source IDs/annotations are built once from the bounded original records.
 focused=focus_profile({'interpretation':interpretation,'facets':facets,'evidence':records})
 primary={u['captured_unit_id']for u in focused['evidence']
     if u['kind']=='synonyms'and u['selection']['state']=='primary'}
 key=lambda u:(u['target'],u['fields']['headword'],u['entry_id'])
 units={key(u):u for u in records if u['kind']=='synonyms'}
 selected={}
 for part in facets['synonyms'].get('data')or[]:
  for lookup in part['lookups']:
   data=lookup['result'];entries=data['entries'];eligible=[]
   for entry in entries:
    senses=[]
    for sense in entry['senses']:
     unit=units.get((part['target'],entry['headword'],str(sense['sense_id'])))
     if (unit and unit['record_id']in primary and any(
         r.get('relation_type')=='synonym'for r in sense['relations'])):senses.append(sense)
    if senses:eligible.append((entry,senses))
   chosen=eligible[:min(2,limit)]
   truncated=len(chosen)<len(eligible)or any(len(senses)>min(4,limit)for _,senses in eligible)
   projected=[]
   for entry,senses in chosen:
    returned=senses[:min(4,limit)];value=copy.deepcopy(entry)
    value.update(senses=copy.deepcopy(returned),senses_total_in_returned_source=len(entry['senses']),
        senses_truncated=len(returned)<len(senses),synonym_senses_available=len(senses))
    projected.append(value)
    coverage={'selection_kind':'requested_synonyms','entries_total':len(entries),
        'entries_returned':len(chosen),'synonym_entries_available':len(eligible),
        'entries_truncated':len(chosen)<len(eligible),
        'senses_total_in_returned_source':len(entry['senses']),
        'senses_returned':len(returned),'synonym_senses_available':len(senses),
        'senses_truncated':len(returned)<len(senses),'selection_truncated':truncated}
    for sense in returned:
     identity=(part['target'],entry['headword'],str(sense['sense_id']))
     if identity not in selected:selected[identity]=copy.deepcopy(coverage)
     elif truncated:selected[identity]['selection_truncated']=True
   data.update(entries=projected,entries_total=len(entries),entries_returned=len(chosen),
       synonym_entries_available=len(eligible),entries_truncated=len(chosen)<len(eligible))
 out=[]
 for unit in records:
  if unit['kind']!='synonyms':out.append(unit);continue
  coverage=selected.get(key(unit))
  if coverage is None:continue
  fields={**unit['fields'],'coverage':coverage}
  out.append(source_record(unit['kind'],unit['entry_id'],fields,unit['provenance'],
      unit['target'],unit['match_relation']))
 return out

def requested_collocation_projection(records,facets,interpretation):
 """Use the existing identity/membership decision before the answer bound."""
 if 'collocations'not in interpretation.get('requested_facets',[]):return records
 from .lexical_focus import focus_profile
 focused=focus_profile({'interpretation':interpretation,'facets':facets,'evidence':records})
 original={u['record_id']:u for u in records if u['kind']=='collocations'}
 primaries={u['captured_unit_id']:u for u in focused['evidence']
     if u['kind']=='collocations'and u['selection']['state']=='primary'}
 membership={m['record_id']:ident for ident,u in primaries.items()
     for m in u.get('source_memberships',[{'record_id':ident}])}
 by_occurrence={(u['target'],str(u['entry_id'])):u for u in original.values()}
 selected={};ranks={}
 for part in facets['collocations'].get('data')or[]:
  for lookup in part['lookups']:
   data=lookup['result'];loaded=data['collocations'];eligible=[];seen=set()
   for rec in loaded:
    unit=by_occurrence.get((part['target'],str(rec['occurrence_id'])))
    ident=membership.get(unit['record_id'])if unit else None
    if rec.get('kind')=='collocation'and ident is not None and ident not in seen:
     eligible.append((ident,rec));seen.add(ident)
   chosen=eligible[:8]
   total=data.get('count_total');known=type(total)is int and total>=len(loaded)
   source_truncated=total>len(loaded)if known else None
   selection_truncated=len(chosen)<len(eligible)
   truncated=True if source_truncated is True or selection_truncated else False if source_truncated is False else None
   options=data.get('filters')or{}
   for ident,_ in chosen:
    if ident not in ranks:ranks[ident]=len(ranks)+1
    coverage={'selection_kind':'requested_collocations','selection_unit':'source_observation_with_memberships',
        'selection_rank':ranks[ident],'query_headword':lookup['query'],
        'count_total':total if known else None,'source_count_returned':len(loaded),
        'eligible_count_in_returned_source':len(eligible),'count_returned':len(chosen),
        'source_truncated':source_truncated,'selection_truncated':selection_truncated,
        'truncated':truncated,'sort':options.get('sort','freq'),'order':options.get('order','desc'),
        'kind':'collocation'}
    for member in primaries[ident].get('source_memberships',[{'record_id':ident}]):
     raw=member['record_id']
     if raw not in selected:selected[raw]=copy.deepcopy(coverage)
     elif truncated is not False:selected[raw]['truncated']=truncated
   data.update(collocations=[copy.deepcopy(rec)for _,rec in chosen],
       count_returned=len(chosen),searched_records=len(loaded),truncated=truncated)
 out=[]
 for unit in records:
  if unit['kind']!='collocations':out.append(unit);continue
  coverage=selected.get(unit['record_id'])
  if coverage is not None:
   out.append(source_record(unit['kind'],unit['entry_id'],{**unit['fields'],'coverage':coverage},
       unit['provenance'],unit['target'],unit['match_relation']))
 return out

def morphological_records(result,target,focus,construction=None,requested_slots=None,full_paradigm=False):
 out=[]
 focus={k:v for k,v in (focus or{}).items()if k!='basis'and v is not None}
 construction=construction or{}
 cases=set(construction.get('case_candidates',[]));numbers=set(construction.get('number_candidates',[]))
 for le in result['lexemes']:
  prov=le['evidence'];head={k:le[k]for k in ['source_entry_id','lemma','grammatical_type','grammatical_features','source_status','frequency_observations','submitted_form','submitted_analyses','matched_by','head_source']}
  head['paradigm_slot_count']=len(le['slots'])
  out.append(source_record('morphology',le['source_entry_id'],head,prov,target))
  # Keep complete slots; a requested grammatical contrast precedes other slots.
  slots=sorted(le['slots'],key=lambda s:(not bool(focus and all(s['normalized_features'].get(k)==v for k,v in focus.items())),not(len({v['form']for v in s['variants']})>1),s['slot_locator']))
  # A submitted alternative can be wrong for the explicit construction.
  # Retain its intended lemma's actual required slot, not just matching forms.
  expand_full=(full_paradigm is True or isinstance(full_paradigm,(set,frozenset,list,tuple))and morphology_key(le)in full_paradigm)
  required=[s for s in slots if
      expand_full or(focus and all(s['normalized_features'].get(k)==v for k,v in focus.items()))
      or ((cases or numbers)and(not cases or s['normalized_features'].get('case')in cases)
          and(not numbers or s['normalized_features'].get('number')in numbers))
      or any(all(s['normalized_features'].get(k)==v for k,v in {**focus,**wanted}.items()if v is not None)
          for wanted in requested_slots or[])]
  selected=required+[s for s in slots if s not in required][:max(0,4-len(required))]
  for s in selected:
   body={'lemma':le['lemma'],'grammatical_type':le['grammatical_type'],'grammatical_features':le['grammatical_features'],'source_status':le['source_status'],'slot':s}
   out.append(source_record('morphology',le['source_entry_id']+':slot:'+str(s['slot_locator']),body,prov,target))
 return out

async def source_advice_morphology(req,request):
 """Capture source-nominated forms without adding user targets or a verdict."""
 if not req.source_advice_captures:return None,[]
 from tools.zrc_reference.pilot_answer_plan import source_advice_application_requests
 requests=source_advice_application_requests(req.text,req.context,req.source_advice_captures)
 if not requests:return facet('not_applicable',[]),[]
 reader=getattr(request.app.state,'sloleks_profiles',None)
 if reader is None:return facet('unavailable',[],reason='Rich Sloleks index not configured'),[]
 parts=[];records=[]
 for wanted in requests:
  lemma=wanted['lemma'];features=wanted['features'];basis=wanted['basis']
  try:
   result=await run_in_threadpool(reader.discover,lemma,32,(lemma,))
  except Exception:
   parts.append({'request':copy.deepcopy(wanted),'application_support':copy.deepcopy(basis),'status':'unavailable','result':None})
   continue
  lexemes=[le for le in result['lexemes']if le['lemma']==lemma
      and le['grammatical_type']==wanted['grammatical_type']
      and any(all(s['normalized_features'].get(k)==v for k,v in features.items())for s in le['slots'])]
  parts.append({'request':copy.deepcopy(wanted),'application_support':copy.deepcopy(basis),
      'status':'matched'if lexemes else'no_match','result':result,
      'source_occurrence_keys':[source_occurrence_key(le['evidence'])for le in lexemes]})
  for unit in morphological_records({**result,'lexemes':lexemes},lemma,features):
   slot=unit['fields'].get('slot')
   if slot is not None and not all(slot['normalized_features'].get(k)==v for k,v in features.items()):continue
   unit['application_support']=copy.deepcopy(basis)
   unit['match_relation']='source_advice_application_support'
   unit['selection']={'state':'primary','reason':'source_advice_application_support'}
   unit['captured_unit_id']=unit['record_id']
   unit['record_id']='lexical:'+digest({k:v for k,v in unit.items()if k!='record_id'})
   records.append(unit)
 return facet(aggregate(parts),parts),records

async def make_profile(req,request):
 start=time.monotonic();state=request.app.state
 if not req.text.strip():raise HTTPException(400,'Empty text')
 try:
  from .analysis_profiles import analyze as profile_analysis
  analysis_text=req.text+('\n'+req.context if req.context else'')
  analysis=await profile_analysis(request,AnalyzeRequest(text=analysis_text,lang='it'if req.lang=='it'else'sl',engine=req.profile,include_loris=True))
 except AnalyzerFailure as error:analysis={'status':'unavailable','raw_engines':{},'engines_used':[],'degraded':True,'reason':error.reason}
 interpretation=plan(req.text,req.context,req.lang,analysis,request_plan=req.request_plan);targets=interpretation['targets'];records=[];facets={};coverage={}
 # Exact surfaces lead; analyzer nominations remain visibly secondary candidates.
 async def morphology_lookup(target):
  surface=target['text'];reader=getattr(state,'sloleks_profiles',None)
  if not reader:return facet('unavailable',reason='Rich Sloleks index not configured')
  if 'sl'not in interpretation['lookup_languages']:return facet('not_applicable',reason='Slovene inventory is not applied to explicit Italian input')
  if not lexical_word(surface):return facet('not_applicable',reason='Whole phrase is not a single Sloleks entry')
  try:
   discover=(interpretation['query_kind']=='comparison' or bool(interpretation.get('form_choice'))
       or bool(interpretation.get('explicit_lexemes')))
   # The existing 256-unit capture bound leaves room for complete slot groups
   # and dictionary/rule evidence; this is a candidate budget, not a card limit.
   budget=max(8,32//max(1,len(targets)))
   lookup=reader.discover if discover and hasattr(reader,'discover')else reader.lookup
   if discover and hasattr(reader,'discover'):
    choice=interpretation.get('form_choice')or(interpretation.get('request_plan')or{}).get('form_choice')or{}
    explicit=choice.get('explicit_lexemes')or interpretation.get('explicit_lexemes')or[]
    nominations=[x.get('text')for x in explicit]+target.get('lemma_candidates',[])
    return facet('matched',await run_in_threadpool(lookup,surface,budget,nominations))
   return facet('matched',await run_in_threadpool(lookup,surface,req.limit))
  except Exception:return facet('unavailable',reason='Sloleks edition unavailable or changed')
 morph=await asyncio.gather(*(morphology_lookup(t)for t in targets));morphdata=[];lookups=[]
 for t,part in zip(targets,morph):
  d=part.get('data');status=d['status']if d else part['status'];morphdata.append({'target':t['text'],'status':status,'result':d,'reason':part.get('reason')})
  if d:
   choice=interpretation.get('form_choice')or(interpretation.get('request_plan')or{}).get('form_choice')or{}
   full_occurrences=full_paradigm_occurrences(interpretation,d['lexemes'])
   records.extend(morphological_records(d,t['text'],interpretation['slot_focus'],choice.get('construction'),interpretation.get('requested_grammar_slots'),full_occurrences))
   exact=[le for le in d['lexemes']if le['matched_by']=='exact_headword']
   t['resource_identity_preference']={'basis':'exact case-preserving headword; alternatives retained, not a frequency decision','source_entry_ids':[le['source_entry_id']for le in exact],'proper_name_primary':bool(exact)and all(le['grammatical_features'].get('type')=='proper'for le in exact)}
   candidates=[x['lemma']for x in d['lexemes']]
  else:candidates=[]
  candidates=[t['text'],*candidates,*t['lemma_candidates']]
  lookups.append((t,list(dict.fromkeys(candidates))[:8]))
 facets['morphology']=facet(aggregate(morphdata),morphdata)
 italian=[]
 if 'it'in interpretation['lookup_languages']and available(request,'morphology'):
  from .morphology_api import lemmatize,inflect
  for t in targets:
   if lexical_word(t['text']):
    try:
     found=await run_in_threadpool(lemmatize,state.morphology,'it',[t['text']],4)
     forms=[]
     for candidate in found['items'][0]['candidates']:
      forms.append(await run_in_threadpool(inflect,state.morphology,'it',candidate['lemma'],40))
     italian.append({'target':t['text'],'analysis':found,'paradigms':forms})
     for form in forms:records.append(source_record('morphology',form['lemma'],form,form['provenance'],t['text']))
    except Exception:italian.append({'target':t['text'],'status':'unavailable'})
 facets['italian_morphology']=facet('matched'if any(x.get('paradigms')for x in italian)else'unavailable'if any(x.get('status')=='unavailable'for x in italian)else'no_match'if 'it'in interpretation['lookup_languages']else'not_applicable',italian)
 frequencies=[]
 for x in morphdata:
  for le in (x.get('result')or{}).get('lexemes',[]):
   frequencies.append({'target':x['target'],'source_entry_id':le['source_entry_id'],'lemma':le['lemma'],'observations':le['frequency_observations'],'form_observations':[{'form':v['form'],'slot_locator':s['slot_locator'],'observations':v['frequency_observations']}for s in le['slots']for v in s['variants']if v['form'].casefold()==x['target'].casefold()],'aggregation':'none; source lexeme total, forms and readings remain distinct'})
 facets['frequency']=facet('matched'if any(x['observations']or x['form_observations']for x in frequencies)else facets['morphology']['status'] if facets['morphology']['status'] in {'not_applicable','unavailable'} else 'no_match',frequencies)
 async def dictionary(kind,t,candidates):
  if 'sl'not in interpretation['lookup_languages']:return {'target':t['text'],'status':'not_applicable','lookups':[]}
  if not available(request,kind):return {'target':t['text'],'status':'unavailable','lookups':[]}
  found=[];failures=0
  for lemma in candidates:
   try:
    if kind=='synonyms':
     original=await run_in_threadpool(state.synonyms.lookup_evidence,lemma,normalized=True)
     data=copy.deepcopy(original)
     requested='synonyms'in interpretation['requested_facets']
     if requested:
      # Retain the complete original lookup and all relation types beside its
      # task projection; no extra query or source mutation is involved.
      data['original_result']=copy.deepcopy(original)
     else:
      data['entries_total']=len(data['entries']);data['entries_truncated']=len(data['entries'])>min(2,req.limit);data['entries']=data['entries'][:min(2,req.limit)]
     for ent in data['entries']:
      if not requested:
       ent['senses_total_in_returned_source']=len(ent['senses']);ent['senses_truncated']=len(ent['senses'])>min(4,req.limit);ent['senses']=ent['senses'][:min(4,req.limit)]
      for sense in ent['senses']:
       sense.setdefault('relations_truncated',False)
       sense['ranking']={'method':'source_order','scores':'original synonym_score retained separately; not corpus frequency','scope':'this source sense and directed relation only'}
     hit=bool(data['entries']);relation=('whole_expression'if any(e['headword'].casefold()==t['text'].casefold()for e in data['entries'])else'component_only')if not lexical_word(t['text'])else'headword_candidate'
    else:
     multi=not lexical_word(t['text']);requested='collocations'in interpretation['requested_facets']
     ordering=(interpretation.get('request_plan')or{}).get('collocation_request')or{}
     options={'min_freq':None,'limit':500 if requested or multi else 8,'kind':'collocation'if requested else'all'}
     if requested:options.update(sort=ordering.get('sort')or'freq',order=ordering.get('order')or'desc')
     original=await run_in_threadpool(state.collocations.get_collocations_for_lemma,lemma,**options)
     data=copy.deepcopy(original)
     if requested:data['original_result']=copy.deepcopy(original)
     sequences={s.casefold()for s in t['lemma_sequences']if s};full=[]
     for rec in data['collocations']:
      comp=' '.join(c['lemma']or c['text']or'' for c in rec['components']).casefold()
      rec['match_relation']='whole_expression'if rec['surface'].casefold()==t['text'].casefold()else'inflected_equivalent'if multi and comp in sequences else'component_only'if multi else'headword_collocation'
      if rec['match_relation']in {'whole_expression','inflected_equivalent'}:full.append(rec)
     chosen=data['collocations']if requested else full if full else data['collocations'][:3 if multi else 8]
     data.update(searched_records=data['count_returned'],collocations=chosen,count_returned=len(chosen))
     data['truncated']=data['count_total']>len(chosen)if type(data.get('count_total'))is int else None
     data['ranking']={'measure':'logDice'if options.get('sort')=='logdice'else'frequency',
         'scope':('source-native ordering for this queried headword; frequency and logDice remain separate observations'
             if requested else'source occurrence count in Gigafida 2.0, separate from logDice'),
         'missing_values':'retained; never zero or a frequency preference'}
     hit=bool(chosen);relation='whole_expression'if full else'component_only'if multi else'headword_collocation'
    if hit:found.append({'query':lemma,'basis':'original_surface'if lemma==t['text']else'lexical_or_analyzer_candidate','match_relation':relation,'result':data})
   except Exception:failures+=1;continue
   if len(found)>=2:break
  return {'target':t['text'],'status':'matched'if found else'unavailable'if failures else'no_match','lookups':found,'failed_lookups':failures,'search_scope':'bounded headword candidates; no whole-dictionary absence claim'}
 dict_jobs=[(kind,t,candidates)for t,candidates in lookups for kind in ('synonyms','collocations')]
 results=await asyncio.gather(*(dictionary(*j)for j in dict_jobs))
 for kind in ('synonyms','collocations'):
  ds=[v for j,v in zip(dict_jobs,results)if j[0]==kind];facets[kind]=facet(aggregate(ds),ds)
  reader=getattr(state,kind,None);prov={'resource_id':'cjvt-thesaurus'if kind=='synonyms'else'cjvt-ksss','version':'2.2','source':reader.manifest['source']if reader else{}}
  for d in ds:
   for lookup in d['lookups']:
    data=lookup['result']
    if kind=='synonyms':
     for ent in data['entries']:
      for sense in ent['senses']:
       target=next(t for t in targets if t['text']==d['target']);relation='alternative_lexical_identity'if target.get('resource_identity_preference',{}).get('proper_name_primary')and ent['headword'][:1].islower()else lookup['match_relation']
       records.append(source_record(kind,str(sense['sense_id']),{'headword':ent['headword'],'head_status':ent.get('head_status'),'sense':sense},prov,d['target'],relation))
    else:
     for rec in data['collocations']:
      target=next(t for t in targets if t['text']==d['target']);relation='alternative_lexical_identity'if target.get('resource_identity_preference',{}).get('proper_name_primary')and rec['headword'][:1].islower()else rec['match_relation']
      records.append(source_record(kind,str(rec['occurrence_id']),rec,prov,d['target'],relation))
 loris=[];seen=set()
 for issue in analysis.get('spans',[])+analysis.get('issues',[]):
  a,b=issue.get('start'),issue.get('end');identity=(issue.get('rule_id'),a,b)
  if identity in seen or type(a)is not int or type(b)is not int:continue
  seen.add(identity);matching=[t for t in targets if t['start']<=a<b<=t['end']]
  if not matching:continue
  t=matching[0];relation='whole_expression'if a==t['start']and b==t['end']else'component_only';rec={**issue,'matched_text':analysis_text[a:b],'match_relation':relation,'analysis_basis':'established contextual LORIS matcher; original-surface aliases preserved'}
  roles=loris_advice_roles(rec)
  if roles:rec['normalized_advice']=roles
  loris.append(rec)
  records.append(source_record('loris',issue['rule_id'],rec,{'resource_id':'loris',**analysis.get('provenance',{})},t['text'],relation))
 facets['loris']=facet('matched'if loris else'unavailable'if analysis['status']=='unavailable'or analysis.get('loris_status')=='unavailable'else'not_attempted'if analysis.get('loris_status')=='not_attempted'else'no_match',loris)
 # The same existing local KLIKai record service, not a parallel import stack.
 term=[];documentary=[];base=getattr(state,'lexical_klikai_url',None)
 if base:
  async with httpx_client()as client:
   for t in targets:
    try:
     resp=await client.get(base+'/api/terminology/lookup',params={'q':t['text'],'allow_partial':'false','include_prose':'false','use_morphology':'true','limit':6});resp.raise_for_status();data=resp.json();term.append({'target':t['text'],'status':'matched'if data.get('hits')else'no_match','result':data})
    except Exception:term.append({'target':t['text'],'status':'unavailable','result':None})
 facets['terminology']=facet('matched'if any(t['status']=='matched'for t in term)else'unavailable'if not base or any(t['status']=='unavailable'for t in term)else'no_match',term)
 # Search discovery uses existing rights-labelled endpoints. Linked excerpts
 # never become complete normative evidence; KLIKai freezes full authorized records.
 if base:
  async with httpx_client()as client:
   for t in targets[:2]:
    try:
     r=await client.get(base+'/api/search',params={'q':t['text'],'limit':3});r.raise_for_status();d=r.json()
     documentary.append({'target':t['text'],'status':'matched'if d.get('hits')else'no_match','result':d,'evidence_scope':'discovery; completeness and rights remain source-labelled'})
    except Exception:documentary.append({'target':t['text'],'status':'unavailable'})
 facets['documentary_advice']=facet('matched'if any(x['status']=='matched'for x in documentary)else'unavailable'if not base or any(x['status']=='unavailable'for x in documentary)else'no_match',documentary)
 for part in term:
  for hit in (part.get('result')or{}).get('hits',[]):
   exact=[f for f in hit.get('matched_forms',[])if f.get('match_tier')in {'exact','morphology_exact'}and f.get('ambiguity_cost')==0]
   if exact:records.append(source_record('terminology',hit['external_id'],hit,{'resource_id':hit['source_slug'],'capture_basis':'exact existing terminology response','rights_mode':hit['rights_mode']},part['target']))
 records=list({r['record_id']:r for r in records}.values())
 # Phrase morphology is a separate, explicitly predictive layer. It must not
 # borrow grammatical authority from a collocation's citation-form tags.
 phrase_analysis=[]
 if 'translation'not in interpretation['requested_facets'] and any(not lexical_word(t['text'])for t in targets):
  for target in targets:
   engines={};forms={}
   for engine,raw in analysis.get('raw_engines',{}).items():
    tokens=[]
    for w in raw.get('analysis',{}).get('words',[]):
     if type(w.get('start'))is not int or not target['start']<=w['start']<w['end']<=target['end']:continue
     tokens.append({k:w.get(k)for k in ('text','lemma','upos','feats','start','end')})
     if w.get('upos')in {'NOUN','ADJ','PROPN','VERB','ADV'}and w['text']not in forms:
      part=await morphology_lookup({'text':w['text']});readings=[];source_slots=[]
      for le in (part.get('data')or{}).get('lexemes',[]):
       for matched in le['submitted_analyses']:
        if not any(morphology_key(s)==morphology_key(le)and s['slot']['slot_locator']==matched['slot_locator']for s in source_slots):
         source_slots.append({'source_entry_id':le['source_entry_id'],'lemma':le['lemma'],'source_status':le['source_status'],'provenance':le['evidence'],'slot':copy.deepcopy(le['slots'][matched['slot_locator']])})
        readings.append({'lemma':le['lemma'],'grammatical_type':le['grammatical_type'],'source_entry_id':le['source_entry_id'],'source_status':le['source_status'],
             'source_occurrence_key':source_occurrence_key(le['evidence']),
            'selection':'primary'if le['lemma']==w['lemma']else'alternative',
            'features':matched['features'],'original_msd':matched['original_msd'],'source_locator':matched['variant_locator'],'slot_locator':matched['slot_locator'],'exact_surface':matched['exact_surface']})
      forms[w['text']]={'status':part['status'],'recorded_readings':readings,'source_slots':source_slots,'candidate_inventory_truncated':bool((part.get('data')or{}).get('truncated'))}
    engines[engine]={'model':raw.get('analysis',{}).get('model'), 'tokens':tokens}
   body={'submitted_expression':target['text'],'start':target['start'],'end':target['end'],
         'engines':engines,'recorded_forms':forms,'authority':'contextual predictions and recorded candidates; not a normative rule',
         'uncertainty':'Keep engine disagreements and all recorded case candidates; phrase without context may be ambiguous'}
   from .phrase_interpretation import interpret_phrase
   body.update(interpret_phrase(body,analysis_text))
   phrase_analysis.append(body)
   if any(e['tokens']for e in engines.values()):records.append(source_record('analysis','tokens:'+str(target['start'])+':'+str(target['end']),body,{'resource_id':'linguistic-analysis','version':'analysis-layer-v1'},target['text']))
 facets['phrase_analysis']=facet('matched'if phrase_analysis else'not_applicable',phrase_analysis)
 if not targets:
  for key in facets:facets[key]=facet('not_applicable',reason='No lexical target; URLs, numbers and identifiers are not words')
 records=requested_synonym_projection(records,facets,interpretation,req.limit)
 records=requested_collocation_projection(records,facets,interpretation)
 coverage={k:{'status':v['status'],'attempted':v['status']not in {'not_attempted','not_applicable'},'matched':v['status']=='matched'}for k,v in facets.items()}
 from .lexical_focus import focus_profile
 query=req.model_dump(exclude=set()if 'source_advice_captures'in req.model_fields_set else{'source_advice_captures'})
 value=focus_profile(LexicalProfile(request_schema_version=CONTRACT,request_id=str(uuid.uuid4()),query=query,interpretation=interpretation,analysis=analysis,facets=facets,evidence=records,coverage=coverage,provenance={'profile':req.profile,'profile_revision':'lexical-v4-20261103','resolved_code_release':os.environ.get('LINGHUB_LEXICAL_RELEASE_ID','unpublished-working-tree'),'resource_editions':{'sloleks':getattr(state,'sloleks_profiles',None).manifest if getattr(state,'sloleks_profiles',None)else None,'synonyms':state.synonyms.manifest['fingerprint']if state.synonyms else None,'collocations':state.collocations.manifest['build_id']if state.collocations else None},'total_ms':round((time.monotonic()-start)*1000,3),'writer_required':False,'input_logging':False,'cache':'immutable SQLite indexes; no shared submitted-text cache'}).model_dump())
 support,units=await source_advice_morphology(req,request)
 if support is not None:
  value['facets']['source_advice_morphology']=support
  value['coverage']['source_advice_morphology']={'status':support['status'],'attempted':support['status']!='not_applicable','matched':support['status']=='matched'}
  value['evidence'].extend(units)
 return value
def httpx_client():
 import httpx
 return httpx.AsyncClient(timeout=5,trust_env=False,follow_redirects=False)
@router.post('/v1/lexical/profile',response_model=LexicalProfile)
async def lexical_profile(req:ProfileRequest,request:Request):
 global _ACTIVE
 if _ACTIVE>=4:raise HTTPException(429,'Lexical capacity occupied; retry shortly')
 _ACTIVE+=1
 try:return bounded(await make_profile(req,request))
 finally:_ACTIVE-=1
@router.get('/v1/lexical/paradigm')
def paradigm(request:Request,surface:str,limit:int=4,offset:int=0):
 if not lexical_word(surface)or len(surface)>128 or not 1<=limit<=8 or offset<0 or offset>1000:raise HTTPException(422,'Invalid bounded lexical lookup')
 reader=getattr(request.app.state,'sloleks_profiles',None)
 if reader is None:raise HTTPException(503,'Sloleks profile unavailable')
 return bounded(reader.lookup(surface,limit,offset))
