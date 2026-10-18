"""Opt-in span-aware cooperation; legacy default stays spaCy."""
import asyncio,hashlib,json,time,copy
from collections import OrderedDict
_CACHE=OrderedDict()
_SPACY_SLOTS=asyncio.Semaphore(2)
import httpx
from fastapi import HTTPException
PROFILES={'compare','klikai_experimental_v1','linghub_lexical_stable_v1'}
REVISION='classla-primary-spacy-independent-spans-v1'
PRIMARY_PROFILE='klikai_classla_primary_v1'
PRIMARY_REVISION='classla-once-canonical-loris-v1'
PROFILES.add(PRIMARY_PROFILE)
SHORT_CHARS=512

def align(raw,text):
 """Connected overlap groups support different token counts and segmentation."""
 intervals=[]
 for engine,result in raw.items():
  for token in result.get('analysis',{}).get('tokens',[]):
   a,b=token['start'],token['end']
   if not(0<=a<b<=len(text)and text[a:b]==token['text']):raise ValueError('Analyzer offset mismatch')
   intervals.append((a,b,engine,token['id']))
 groups=[]
 for a,b,engine,tid in sorted(intervals):
  if not groups or a>=groups[-1]['end']:groups.append({'start':a,'end':b,'members':{}})
  g=groups[-1];g['end']=max(g['end'],b);g['members'].setdefault(engine,[]).append(tid)
 for g in groups:
  g['text']=text[g['start']:g['end']];g['analyses']={}
  for engine,tids in g['members'].items():
   analysis=raw[engine]['analysis'];words=[w for w in analysis['words']if w['parent_token_id']in tids]
   g['analyses'][engine]=[{k:w[k]for k in ('text','lemma','upos','xpos','feats')}for w in words]
  signatures={json.dumps([(w['lemma'],w['upos'],w['feats'])for w in words],sort_keys=True)for words in g['analyses'].values()}
  g['disagreement']=len(signatures)>1
 return groups

async def _analyze(request,req):
 from .platform_api import analyzer_request,AnalyzerFailure
 if req.lang=='it'and req.engine=='classla':raise AnalyzerFailure('unsupported','CLASSLA profile supports Slovene only.')
 start=time.monotonic();raw={};failures={}
 async def spacy():
  async with asyncio.timeout(8),_SPACY_SLOTS:
   return await analyzer_request(request,'/v1/analyze',{'lang':req.lang,'text':req.text,'engine':'spacy','include_loris':req.include_loris},timeout=7)
 async def classla():
  base=getattr(request.app.state,'lexical_classla_url',None)
  if not base:raise RuntimeError('CLASSLA experimental worker not configured')
  async with httpx.AsyncClient(timeout=8,trust_env=False,follow_redirects=False)as client:
   r=await client.post(base+'/analyze',json={'text':req.text});r.raise_for_status();a=r.json()['analysis']
  if a['model']['engine']!='classla' or a['model']['version']!='2.2.3' or a['model']['fingerprint']!='6a12a8f0996bf623fa97f11455f1b1509fb06c6eaffe0c49c05a0bf8036866c1':raise ValueError('Engine identity mismatch')
  tokens=[]
  for t in a['tokens']:
   ws=[a['words'][i]for i in t['word_ids']]
   tokens.append({'text':t['text'],'start':t['start'],'end':t['end'],'lemma':' '.join(w['lemma']for w in ws),'pos':ws[0]['upos']})
  return {'analysis':a,'tokens':tokens,'engine_used':'classla','text':req.text,'lang':req.lang,'issues':[],'spans':[]}
 engines=['spacy']if req.lang=='it'or req.engine=='spacy'else['classla']if req.engine in {'classla',PRIMARY_PROFILE}or len(req.text)>SHORT_CHARS and req.engine!='compare' else['classla','spacy']
 # Explicit comparison always compares; old opt-in profiles retain their long-input policy.
 results=await asyncio.gather(*(spacy()if x=='spacy'else classla()for x in engines),return_exceptions=True)
 for name,result in zip(engines,results):
  if isinstance(result,BaseException):failures[name]={'status':'unavailable','error_type':type(result).__name__}
  else:
   model=result['analysis']['model']
   if name=='spacy'and req.lang=='sl'and (model.get('version')!='3.8.16'or model.get('model_version')!='3.8.0'or model.get('fingerprint')!='3105e19bd0d05135671164ee4bea8bb9cf3b6e624decbc16a9a3aa9a2a8185da'or result.get('provenance',{}).get('rules_sha256')!='0dee12b10298bb381f09f1b3c454a477df09f04eaafa6364a174f5b537511d1c'):
    failures[name]={'status':'unavailable','error_type':'ProfileModelPinMismatch'}
   else:
    try:align({name:result},req.text);raw[name]=result
    except (ValueError,KeyError,TypeError):failures[name]={'status':'unavailable','error_type':'InvalidOriginalOffsets'}
 if not raw:raise AnalyzerFailure('unavailable','Requested analysis engines are unavailable.')
 primary='classla'if'classla'in raw else'spacy';chosen=raw[primary]
 aligned=align(raw,req.text)
 # Curated rules execute only through their established full-context matcher.
 # Alternative statistical lemmas do not independently activate a LORIS rule.
 loris=raw.get('spacy',{});provenance=loris.get('provenance',{})
 if req.engine==PRIMARY_PROFILE and req.lang=='sl' and req.include_loris:
  try:
   loris=await analyzer_request(request,'/v1/loris/from-analysis',{'lang':'sl','text':req.text,'profile':PRIMARY_PROFILE,'analysis':chosen['analysis']},timeout=7)
   if loris.get('text')!=req.text or loris.get('lang')!='sl' or loris.get('provenance',{}).get('rules_sha256')!='0dee12b10298bb381f09f1b3c454a477df09f04eaafa6364a174f5b537511d1c':raise ValueError('LORIS identity mismatch')
   for issue in loris.get('issues',[])+loris.get('spans',[]):
    a,b=issue.get('start'),issue.get('end')
    if type(a)is not int or type(b)is not int or not 0<=a<b<=len(req.text):raise ValueError('LORIS offsets invalid')
   provenance=loris.get('provenance',{})
  except (AnalyzerFailure,ValueError,KeyError,TypeError)as error:
   failures['loris']={'status':'unavailable','error_type':type(error).__name__};loris={}
 if req.engine==PRIMARY_PROFILE:
  return {'status':'partial'if failures else'ok','lang':req.lang,'text':req.text,'offset_convention':'unicode_codepoint_half_open','engine_requested':req.engine,'engine_used':primary,'engines_used':list(raw),'analysis':chosen['analysis'],'tokens':chosen['tokens'],'loris_status':'not_attempted'if not req.include_loris else'unavailable'if 'loris'in failures else'checked','issues':loris.get('issues',[]),'spans':loris.get('spans',[]),'raw_engines':raw,'alignment':aligned,'disagreements':[g for g in aligned if g['disagreement']],'failures':failures,'degraded':bool(failures),'selected_analysis_basis':{'policy_revision':PRIMARY_REVISION,'primary':'CLASSLA once for Slovene; existing spaCy pipeline for Italian','uncertainty':'raw predictions retained independently of supported grammatical applications','loris_basis':'canonical matcher reuses this contextual analysis and exact original-surface aliases; no second trained model'},'provenance':{**provenance,'analysis_profile':req.engine,'analysis_profile_revision':PRIMARY_REVISION,'models':{k:v['analysis']['model']for k,v in raw.items()}},'operations':{'classla_calls':int('classla'in engines),'trained_spacy_calls':int('spacy'in engines),'loris_reused_analysis_calls':int(req.lang=='sl'and req.include_loris)},'timings':{'analysis_ms':round((time.monotonic()-start)*1000,3)}}
 return {'status':'partial'if failures else'ok','lang':req.lang,'text':req.text,'offset_convention':'unicode_codepoint_half_open','engine_requested':req.engine,'engine_used':primary,'engines_used':list(raw),'analysis':chosen['analysis'],'tokens':chosen['tokens'],'loris_status':'checked'if'spacy'in raw else'not_attempted','issues':loris.get('issues',[]),'spans':loris.get('spans',[]),'raw_engines':raw,'alignment':aligned,'disagreements':[g for g in aligned if g['disagreement']],'failures':failures,'degraded':bool(failures),'selected_analysis_basis':{'policy_revision':REVISION,'primary':'CLASSLA contextual prediction; spaCy independent lookup candidates','uncertainty':'agreement is not linguistic verification; lexical alternatives remain separate','long_input_policy':'CLASSLA primary above 512 characters; Italian spaCy only','loris_basis':'established spaCy/context matcher and exact surface aliases; not arbitrary alternate-lemma activation'},'provenance':{**provenance,'analysis_profile':req.engine,'analysis_profile_revision':REVISION,'models':{k:v['analysis']['model']for k,v in raw.items()}},'timings':{'analysis_ms':round((time.monotonic()-start)*1000,3)}}


async def analyze(request,req):
 # An authenticated external proxy supplies the scope. Unscoped internal calls
 # do not cache submitted text; source-entry caching is separate and public.
 scope=request.headers.get('x-linghub-cache-scope','')
 key=hashlib.sha256(json.dumps([scope,REVISION,req.model_dump()],sort_keys=True).encode()).hexdigest()
 now=time.monotonic()
 if scope and key in _CACHE and now-_CACHE[key][0]<60:
  result=copy.deepcopy(_CACHE[key][1]);result['cache']={'status':'hit','scope':'client','ttl_seconds':60}
  if 'operations'in result:result['operations']={name:0 for name in result['operations']}
  return result
 result=await _analyze(request,req)
 if scope and not result['degraded']:
  _CACHE[key]=(now,copy.deepcopy(result));_CACHE.move_to_end(key)
  while len(_CACHE)>128:_CACHE.popitem(last=False)
 result['cache']={'status':'miss'if scope else'disabled_unscoped','scope':'client','ttl_seconds':60}
 return result
