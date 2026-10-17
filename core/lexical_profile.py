"""Shared, source-backed lexical profile for direct clients and KLIKai."""
import asyncio,copy,hashlib,json,time,uuid,os
from collections import defaultdict
from typing import Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field
from starlette.concurrency import run_in_threadpool
from .lexical_planner import plan,lexical_word
from .platform_api import AnalyzeRequest,analyze_once,AnalyzerFailure,available,bounded,public,bounded_thesaurus
CONTRACT='linghub.lexical-profile.v1'
router=APIRouter()
_ACTIVE=0
class ProfileRequest(BaseModel):
 text:str=Field(min_length=1,max_length=3000)
 context:str=Field(default='',max_length=3000)
 lang:Literal['sl','it','und']='und'
 profile:Literal['klikai_experimental_v1','linghub_lexical_stable_v1','compare','spacy','classla']='klikai_experimental_v1'
 limit:int=Field(default=4,ge=1,le=8)
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
 item=public({'kind':kind,'entry_id':entry_id,'target':target,'match_relation':relation,'fields':body,'provenance':provenance});item['record_id']='lexical:'+digest(item);return item

def morphological_records(result,target,focus):
 out=[]
 for le in result['lexemes']:
  prov=le['evidence'];head={k:le[k]for k in ['source_entry_id','lemma','grammatical_type','grammatical_features','source_status','frequency_observations','submitted_form','submitted_analyses','matched_by','head_source']}
  out.append(source_record('morphology',le['source_entry_id'],head,prov,target))
  # Keep complete slots; a requested grammatical contrast precedes other slots.
  slots=sorted(le['slots'],key=lambda s:(not bool(focus and all(s['normalized_features'].get(k)==v for k,v in focus.items()if k in {'case','number'}and v)),not(len({v['form']for v in s['variants']})>1),s['slot_locator']))
  for s in slots[:4]:
   body={'lemma':le['lemma'],'grammatical_type':le['grammatical_type'],'grammatical_features':le['grammatical_features'],'source_status':le['source_status'],'slot':s}
   out.append(source_record('morphology',le['source_entry_id']+':slot:'+str(s['slot_locator']),body,prov,target))
 return out

async def make_profile(req,request):
 start=time.monotonic();state=request.app.state
 if not req.text.strip():raise HTTPException(400,'Empty text')
 try:
  from .analysis_profiles import analyze as profile_analysis
  analysis_text=req.text+('\n'+req.context if req.context else'')
  analysis=await profile_analysis(request,AnalyzeRequest(text=analysis_text,lang='it'if req.lang=='it'else'sl',engine=req.profile,include_loris=True))
 except AnalyzerFailure as error:analysis={'status':'unavailable','raw_engines':{},'engines_used':[],'degraded':True,'reason':error.reason}
 interpretation=plan(req.text,req.context,req.lang,analysis);targets=interpretation['targets'];records=[];facets={};coverage={}
 # Exact surfaces lead; analyzer nominations remain visibly secondary candidates.
 async def morphology_lookup(target):
  surface=target['text'];reader=getattr(state,'sloleks_profiles',None)
  if not reader:return facet('unavailable',reason='Rich Sloleks index not configured')
  if 'sl'not in interpretation['lookup_languages']:return facet('not_applicable',reason='Slovene inventory is not applied to explicit Italian input')
  if not lexical_word(surface):return facet('not_applicable',reason='Whole phrase is not a single Sloleks entry')
  try:return facet('matched',await run_in_threadpool(reader.lookup,surface,req.limit))
  except Exception:return facet('unavailable',reason='Sloleks edition unavailable or changed')
 morph=await asyncio.gather(*(morphology_lookup(t)for t in targets));morphdata=[];lookups=[]
 for t,part in zip(targets,morph):
  d=part.get('data');status=d['status']if d else part['status'];morphdata.append({'target':t['text'],'status':status,'result':d,'reason':part.get('reason')})
  if d:
   records.extend(morphological_records(d,t['text'],interpretation['slot_focus']))
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
     data=await run_in_threadpool(state.synonyms.lookup_evidence,lemma,normalized=True)
     data['entries_total']=len(data['entries']);data['entries_truncated']=len(data['entries'])>min(2,req.limit);data['entries']=data['entries'][:min(2,req.limit)]
     # Source-defined senses stay separate; bounded relation slices are explicit.
     data['entries']=copy.deepcopy(data['entries'])
     for ent in data['entries']:
      ent['senses_total_in_returned_source']=len(ent['senses']);ent['senses_truncated']=len(ent['senses'])>min(4,req.limit);ent['senses']=ent['senses'][:min(4,req.limit)]
      for sense in ent['senses']:
       sense['relations_truncated']=False
       sense['ranking']={'method':'source_order','scores':'original synonym_score retained separately; not corpus frequency','scope':'this source sense and directed relation only'}
     hit=bool(data['entries']);relation=('whole_expression'if any(e['headword'].casefold()==t['text'].casefold()for e in data['entries'])else'component_only')if not lexical_word(t['text'])else'headword_candidate'
    else:
     multi=not lexical_word(t['text']);data=await run_in_threadpool(state.collocations.get_collocations_for_lemma,lemma,min_freq=None,limit=500 if multi else 8,kind='all')
     sequences={s.casefold()for s in t['lemma_sequences']if s};full=[]
     for rec in data['collocations']:
      comp=' '.join(c['lemma']or c['text']or'' for c in rec['components']).casefold()
      rec['match_relation']='whole_expression'if rec['surface'].casefold()==t['text'].casefold()else'inflected_equivalent'if multi and comp in sequences else'component_only'if multi else'headword_collocation'
      if rec['match_relation']in {'whole_expression','inflected_equivalent'}:full.append(rec)
     chosen=full if full else data['collocations'][:3 if multi else 8]
     data['searched_records']=data['count_returned'];data['collocations']=chosen;data['count_returned']=len(chosen);data['truncated']=data['count_total']>len(chosen);data['ranking']={'measure':'frequency','scope':'source occurrence count in Gigafida 2.0, separate from logDice','missing_values':'retained; never zero or a frequency preference'}
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
  t=matching[0];relation='whole_expression'if a==t['start']and b==t['end']else'component_only';rec={**issue,'matched_text':req.text[a:b],'match_relation':relation,'analysis_basis':'established contextual LORIS matcher; original-surface aliases preserved'};loris.append(rec)
  records.append(source_record('loris',issue['rule_id'],rec,{'resource_id':'loris',**analysis.get('provenance',{})},t['text'],relation))
 facets['loris']=facet('matched'if loris else'unavailable'if analysis['status']=='unavailable'else'not_attempted'if analysis.get('loris_status')=='not_attempted'else'no_match',loris)
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
 if not targets:
  for key in facets:facets[key]=facet('not_applicable',reason='No lexical target; URLs, numbers and identifiers are not words')
 coverage={k:{'status':v['status'],'attempted':v['status']not in {'not_attempted','not_applicable'},'matched':v['status']=='matched'}for k,v in facets.items()}
 return LexicalProfile(request_schema_version=CONTRACT,request_id=str(uuid.uuid4()),query=req.model_dump(),interpretation=interpretation,analysis=analysis,facets=facets,evidence=records,coverage=coverage,provenance={'profile':req.profile,'profile_revision':'lexical-v1-20261017','resolved_code_release':os.environ.get('LINGHUB_LEXICAL_RELEASE_ID','unpublished-working-tree'),'resource_editions':{'sloleks':getattr(state,'sloleks_profiles',None).manifest if getattr(state,'sloleks_profiles',None)else None,'synonyms':state.synonyms.manifest['fingerprint']if state.synonyms else None,'collocations':state.collocations.manifest['build_id']if state.collocations else None},'total_ms':round((time.monotonic()-start)*1000,3),'writer_required':False,'input_logging':False,'cache':'immutable SQLite indexes; no shared submitted-text cache'}).model_dump()
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
