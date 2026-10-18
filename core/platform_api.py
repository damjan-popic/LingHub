"""Versioned reusable LingHub APIs. Selection offsets refer to original code points."""
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

from core import morphology_api as morphology
from core.contextual import norm, lexical_sequence, content_lemmas, validate_analysis

router = APIRouter()
LIMITS = {'text_characters':10000,'selection_characters':512,'morphology_tokens':64,
    'token_characters':128,'lemma_candidates':4,'forms':100,'signatures':32,
    'signature_tokens':32,'dictionary_lemmas':8,'dictionary_records_per_lemma':20,
    'synonym_relations_per_sense':100,'synonym_senses':20,'response_bytes':2097152,
    'request_bytes':131072,'analyzer_timeout_seconds':30}
PARTIAL_PATHS = {'/v1/resources','/v1/capabilities','/v1/explore','/v1/analyze',
    '/v1/morphology/lemmatize','/v1/morphology/inflect','/v1/morphology/signatures'}


def public(value):
    """Remove incidental host paths, not linguistic source evidence."""
    private_keys = {'raw_location','package_path','model_path','source_wheel_path','path','root',
                    'interpreter','language_paths','private_evidence','working_directory'}
    if isinstance(value, dict):
        return {k:public(v) for k,v in value.items() if k not in private_keys}
    if isinstance(value, list): return [public(v) for v in value]
    return value


def bounded(value):
    value = public(value)
    if len(json.dumps(value,ensure_ascii=False).encode()) > LIMITS['response_bytes']:
        raise HTTPException(413,'Combined response exceeds bound; reduce selection or requested limits.')
    return value


def available(request, name):
    files = getattr(request.app.state,'resource_files_by_kind',{}).get(name)
    if files is None:
        return getattr(request.app.state,name if name!='morphology' else 'morphology',None) is not None
    try:
        return all((p.stat().st_dev,p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns)==identity for p,identity in files.items())
    except OSError: return False


def require(request,name):
    if not available(request,name):
        raise HTTPException(503,'Required '+name+' resource changed or is unavailable; revalidation is required.')


def present(request,name):
    files=getattr(request.app.state,'resource_files_by_kind',{}).get(name)
    return all(p.is_file() for p in files) if files is not None else getattr(request.app.state,name,None) is not None


class AnalyzerFailure(Exception):
    def __init__(self,status,reason): self.status,self.reason=status,reason


async def analyzer_request(request,route,payload=None,timeout=30):
    base=getattr(request.app.state,'lemmatizer_url',None)
    if not base: raise AnalyzerFailure('unavailable','Analyzer is not configured.')
    try:
        async with asyncio.timeout(timeout), httpx.AsyncClient(base_url=base,trust_env=False,follow_redirects=False,timeout=timeout) as client:
            async with client.stream('GET' if payload is None else 'POST',route,json=payload) as response:
                if response.status_code==422: raise AnalyzerFailure('unsupported','Requested analyzer or language is unsupported.')
                if response.status_code!=200: raise AnalyzerFailure('unavailable','Selected analyzer is unavailable.')
                data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>8*1024*1024: raise AnalyzerFailure('error','Analyzer response exceeds bound.')
                result=json.loads(data)
                if not isinstance(result,dict): raise ValueError('Invalid analyzer response')
                return public(result)
    except (httpx.HTTPError,TimeoutError): raise AnalyzerFailure('unavailable','Selected analyzer request timed out or failed.') from None
    except (ValueError,TypeError): raise AnalyzerFailure('error','Selected analyzer response is invalid.') from None


class AnalyzeRequest(BaseModel):
    lang: Literal['sl','it']
    text: str = Field(min_length=1,max_length=10000)
    engine: Literal['default','spacy','classla','compare','klikai_classla_primary_v1','klikai_experimental_v1','linghub_lexical_stable_v1']='default'
    include_loris: bool=True


async def analyze_once(request,req):
    if not req.text.strip(): raise HTTPException(400,'Text is empty.')
    from .analysis_profiles import PROFILES, analyze as profile_analysis
    if req.engine in PROFILES or (req.engine in {'classla','spacy'} and getattr(request.app.state,'lexical_classla_url',None)):
        return await profile_analysis(request,req)
    result=await analyzer_request(request,'/v1/analyze',req.model_dump())
    try:
        validate_analysis(result,req.text,'/loris/check')
        if result.get('text')!=req.text or result.get('lang')!=req.lang or result.get('offset_convention')!='unicode_codepoint_half_open':
            raise ValueError('Response identity mismatch')
        used=result.get('engine_used')
        expected='spacy' if req.engine=='default' else req.engine
        if used!=expected: raise ValueError('Unexpected analyzer engine; fallback is forbidden')
    except (ValueError,TypeError,KeyError):
        raise AnalyzerFailure('error','Analyzer schema, identity or original-text offsets are invalid.') from None
    return result


@router.post('/v1/analyze')
async def analyze(req:AnalyzeRequest,request:Request):
    try: return bounded(await analyze_once(request,req))
    except AnalyzerFailure as error:
        raise HTTPException(422 if error.status=='unsupported' else 503 if error.status=='unavailable' else 502,
            {'status':error.status,'message':error.reason}) from None


def resource_catalogue(request):
    state=request.app.state
    resources=[]
    for name,attr in [('synonyms','synonyms'),('collocations','collocations')]:
        reader=getattr(state,attr,None)
        if reader is None: continue
        manifest=reader.manifest; source=manifest['source']
        resources.append({'id':'cjvt-thesaurus' if name=='synonyms' else 'cjvt-ksss',
            'name':source.get('title',name),'version':source['version'],'languages':['sl'],
            'license':source['license'],'fingerprint':manifest.get('fingerprint',manifest['build_id']),
            'source_identifier':source['handle'],'source_url':source.get('landing_url'),
            'provenance_kind':'official_release_acquisition','artifact_present':present(request,name),
            'artifact_validated':available(request,name),'capability_usable':available(request,name),
            'features':['sense_grouped_synonyms','antonyms'] if name=='synonyms' else ['collocations','grammatical_combinations'],
            'counts':manifest['counts'],'source_caveats':manifest.get('source_caveats',{'accounting':'4425942 collocations plus2109 separate grammatical combinations; catalogue definitions differ'})})
    reader=getattr(state,'morphology',None)
    if reader is not None:
        # The immutable adopted release object is cached; no per-request hashing.
        release=getattr(state,'morphology_release',None) or reader.load_release()
        for lang,item in release.manifest['languages'].items():
            source=item['source']
            resources.append({'id':'sloleks' if lang=='sl' else 'morph-it','name':source['name'],
                'version':source['version'],'languages':[lang],'license':source['license'],
                'fingerprint':release.release_fingerprint,'source_identifier':source['identifier'],
                'source_url':source['url'],'artifact_present':present(request,'morphology'),
                'artifact_validated':available(request,'morphology'),'capability_usable':available(request,'morphology'),
                'features':['lexical_lemmas','native_msd_inflections','lexical_signatures'],
                'normalization':release.normalization_version})
    return resources


async def analyzer_capabilities(request):
    """One bounded observation shared by catalogue and current health responses."""
    try:
        backend=await analyzer_request(request,'/v1/capabilities',timeout=2)
        if not isinstance(backend.get('engines'),dict) or not isinstance(backend.get('defaults'),dict):
            raise AnalyzerFailure('error','Analyzer capability shape is invalid.')
        return backend,True
    except AnalyzerFailure:
        return {'defaults':{'sl':'spacy','it':'spacy'},'engines':{},'status':'unavailable'},False


async def capabilities_data(request):
    resources=resource_catalogue(request)
    backend,reachable=await analyzer_capabilities(request)
    identity={'resources':[{k:r.get(k) for k in ('id','version','fingerprint')} for r in resources],
              'analyzers':backend.get('identity',backend.get('engines',{})),
              'defaults':backend.get('defaults',{'sl':'spacy','it':'spacy'}),
              'contract':'linghub.platform.v1'}
    experimental={}
    if getattr(request.app.state,'lexical_classla_url',None):
        try:
            async with httpx.AsyncClient(timeout=2,trust_env=False)as client:
                r=await client.get(request.app.state.lexical_classla_url+'/health');r.raise_for_status();experimental=r.json()['model']
        except (httpx.HTTPError,ValueError,KeyError):experimental={'status':'unavailable'}
    models=[]
    for engine,info in backend.get('engines',{}).items():
        if isinstance(info,dict):
            models.extend(info.get('models',{}).values())
            if info.get('model'): models.append(info['model'])
    return {'status':'ok','lexical_profile':{'available':getattr(request.app.state,'sloleks_profiles',None)is not None,'schema':'linghub.lexical-profile.v1','profiles':['klikai_classla_primary_v1','klikai_experimental_v1','linghub_lexical_stable_v1','compare'],'legacy_default_unchanged':True,'profile_policies':{'klikai_classla_primary_v1':'CLASSLA once for Slovene; canonical LORIS reuses annotations; Italian spaCy','compare':'explicit independent engine comparison','linghub_lexical_stable_v1':'legacy profile unchanged','klikai_experimental_v1':'legacy profile unchanged'},'resolved_release':__import__('os').environ.get('LINGHUB_LEXICAL_RELEASE_ID','unpublished-working-tree'),'classla':experimental,'policy':'classla-primary-spacy-independent-spans-v1','sloleks':getattr(request.app.state,'sloleks_profiles',None).manifest if getattr(request.app.state,'sloleks_profiles',None)else None},'resources':resources,'models':models,'engines':backend.get('engines',{}),
        'defaults':backend.get('defaults',{'sl':'spacy','it':'spacy'}),'analyzer_service_reachable':reachable,
        'analyzer_capabilities':backend,'limits':LIMITS,'identity':hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'capabilities':{'morphology':available(request,'morphology'),'synonyms':available(request,'synonyms'),
            'collocations':available(request,'collocations'),'contextual_analysis':reachable,'explore':True},
        'production_parity':'pending','privacy':{'input_logging':False,'raw_text_persistence':False,'telemetry':False},
        'code_reuse':{'origin':'KLIKai d71ac7f980b68bdf81cb1770e9ddf9ad4da50f85','license':morphology.LOCK['license']}}


@router.get('/v1/resources')
@router.get('/v1/capabilities')
async def catalogue(request:Request): return bounded(await capabilities_data(request))


class LemmatizeRequest(BaseModel):
    lang:Literal['sl','it']
    tokens:list[str]=Field(min_length=1,max_length=64)
    limit:int=Field(4,ge=1,le=4)
    @model_validator(mode='after')
    def valid_tokens(self):
        if any(not t.strip() or len(t)>128 or any(c.isspace() for c in t.strip()) for t in self.tokens):
            raise ValueError('Supply one nonempty token per item, at most128characters.')
        return self


class InflectRequest(BaseModel):
    lang:Literal['sl','it']
    lemma:str=Field(min_length=1,max_length=128)
    limit:int=Field(40,ge=1,le=100)
    msd:str|None=Field(None,min_length=1,max_length=128)


class SignatureRequest(BaseModel):
    lang:Literal['sl','it']
    text:str=Field(min_length=1,max_length=512)
    limit:int=Field(32,ge=1,le=32)


@router.post('/v1/morphology/lemmatize')
def lemmatize(req:LemmatizeRequest,request:Request):
    require(request,'morphology')
    return bounded(morphology.lemmatize(request.app.state.morphology,req.lang,req.tokens,req.limit))


@router.post('/v1/morphology/inflect')
def inflect(req:InflectRequest,request:Request):
    require(request,'morphology')
    if not req.lemma.strip(): raise HTTPException(400,'Lemma is empty.')
    return bounded(morphology.inflect(request.app.state.morphology,req.lang,req.lemma,req.limit,req.msd))


@router.post('/v1/morphology/signatures')
def signatures(req:SignatureRequest,request:Request):
    require(request,'morphology')
    if not req.text.strip(): raise HTTPException(400,'Text is empty.')
    if len(morphology.tokenize(req.text))>32: raise HTTPException(422,'At most32signaturetokens are allowed.')
    return bounded(morphology.signatures(request.app.state.morphology,req.lang,req.text,req.limit))


class Selection(BaseModel):
    start:int=Field(ge=0)
    end:int=Field(gt=0)


Tool=Literal['analysis','loris','morphology','synonyms','collocations']
class ExploreRequest(AnalyzeRequest):
    selection:Selection|None=None
    include:list[Tool]=Field(default_factory=lambda:['analysis','loris','morphology','synonyms','collocations'],min_length=1,max_length=5)
    limit:int=Field(5,ge=1,le=20)
    @model_validator(mode='after')
    def selection_valid(self):
        if len(set(self.include))!=len(self.include): raise ValueError('Duplicate requested tools.')
        if self.selection:
            if not 0<=self.selection.start<self.selection.end<=len(self.text): raise ValueError('Selection is outside original text.')
            if self.selection.end-self.selection.start>512: raise ValueError('Selected span exceeds512characters.')
            if not self.text[self.selection.start:self.selection.end].strip(): raise ValueError('Selection is empty.')
        return self


def part(status,data=None,provenance=None,truncated=False,reason=None):
    return {'status':status,'data':data,'provenance':provenance or {},'truncated':truncated,**({'reason':reason} if reason else {})}


def bounded_thesaurus(reader,lemma,limit,normalized=True):
    data=reader.lookup_evidence(lemma,normalized=normalized)
    truncated=False
    entries=data['entries'][:limit]
    truncated |= len(entries)<len(data['entries'])
    for entry in entries:
        senses=entry['senses'][:20]; truncated |= len(senses)<len(entry['senses']); entry['senses']=senses
        for sense in senses:
            relations=sense['relations'][:100]; truncated |= len(relations)<len(sense['relations']); sense['relations']=relations
            sense['namespaced_id']='cjvt-thesaurus:2.2:'+sense['sense_id']
    data['entries']=entries;data['truncated']=bool(truncated)
    return data


@router.post('/v1/explore')
async def explore(req:ExploreRequest,request:Request):
    if not req.text.strip(): raise HTTPException(400,'Text is empty.')
    requested=set(req.include); results={}; annotation=None; failure=None
    try:
        annotation=await analyze_once(request,AnalyzeRequest(lang=req.lang,text=req.text,engine=req.engine,include_loris='loris' in requested))
    except AnalyzerFailure as error: failure=error
    selection=req.selection
    if selection is None:
        # Whole-input exploration is bounded; long input requires an explicit span
        # only for lexical subtools. Full contextual analysis remains available.
        selection=Selection(start=0,end=min(len(req.text),512))
    start,end=selection.start,selection.end; surface=req.text[start:end]
    lexical_truncated=req.selection is None and len(req.text)>512
    if 'analysis' in requested:
        results['analysis']=part('ok',annotation,annotation.get('provenance',{}) if annotation else {}) if annotation else part(failure.status,reason=failure.reason)
    if 'loris' in requested:
        results['loris']=part('ok' if annotation['issues'] or annotation['spans'] else 'no_hit',
            {k:annotation[k] for k in ('tokens','issues','spans')}, {'source_id':'loris','source_slug':'loris',**annotation.get('provenance',{})}) if annotation else part(failure.status,reason=failure.reason)
    selected_tokens=[t for t in annotation['tokens'] if t['start']>=start and t['end']<=end] if annotation else []
    lexical_tokens=list(dict.fromkeys(morphology.tokenize(surface)))[:64]
    lexical=None; lexical_failed=False
    if available(request,'morphology') and lexical_tokens:
        try: lexical=await run_in_threadpool(morphology.lemmatize,request.app.state.morphology,req.lang,lexical_tokens,4)
        except Exception: lexical_failed=True
    if 'morphology' in requested:
        if lexical:
            forms=[]
            for lemma in list(dict.fromkeys(c['lemma_normalized'] for i in lexical['items'] for c in i['candidates']))[:4]:
                forms.append(await run_in_threadpool(morphology.inflect,request.app.state.morphology,req.lang,lemma,12))
            lexical['inflections']=forms
        results['morphology']=part(lexical['status'],lexical,lexical['provenance'],lexical['truncated'] or lexical_truncated or any(f['truncated'] for f in lexical.get('inflections',[]))) if lexical else part('unavailable' if lexical_failed or not available(request,'morphology') else 'no_hit')
    # Contextual lemmas nominate lexical entries, never dictionary senses. Surface
    # remains an explicit fallback when the selected analyzer is unavailable.
    candidates=[]
    if selected_tokens: candidates.extend(content_lemmas(selected_tokens,{'NOUN','VERB','ADJ','PROPN','ADV'}))
    if lexical:
        candidates.extend(c['lemma_normalized'] for item in lexical['items'] for c in item['candidates'])
    candidates.extend([norm(surface),*(norm(t) for t in lexical_tokens)])
    candidates=list(dict.fromkeys(c for c in candidates if c and len(c)<=256))
    selected_candidates=candidates[:8]; candidate_truncated=len(candidates)>8 or lexical_truncated
    sequence=lexical_sequence(selected_tokens) if selected_tokens else []
    for name in ('synonyms','collocations'):
        if name not in requested: continue
        if req.lang!='sl': results[name]=part('unsupported',reason='These CJVT dictionaries are Slovene-only.');continue
        if not available(request,name): results[name]=part('unavailable',reason='Required dictionary resource changed or is unavailable.');continue
        entries=[];truncated=candidate_truncated
        for lemma in selected_candidates:
            if name=='synonyms':
                data=await run_in_threadpool(bounded_thesaurus,request.app.state.synonyms,lemma,req.limit)
                hit=data['count_entries']>0;truncated |= data['truncated']
            else:
                data=await run_in_threadpool(request.app.state.collocations.get_collocations_for_lemma,lemma,
                    min_freq=None,limit=req.limit,kind='all')
                hit=data['count_total']>0;truncated |= data['count_total']>data['count_returned']
                for record in data['collocations']:
                    record['namespaced_sense_id']='cjvt-ksss:2.2:'+str(record.get('sense_id'))
                    components=[norm(c['lemma']) for c in record['components'] if c.get('lemma')]
                    record['matched_by']=[]
                    if norm(record.get('surface') or '')==norm(surface): record['matched_by'].append('surface')
                    if sequence and components==sequence: record['matched_by'].append('lemma_sequence')
            entries.append({'lemma':lemma,'status':'ok' if hit else 'no_hit','evidence':data})
        results[name]=part('ok' if any(e['status']=='ok' for e in entries) else 'no_hit',
            {'join_kind':'lemma_based_candidates; dictionary senses are not contextually resolved',
             'contextual_candidates':annotation is not None,'lookups':entries,'lemma_candidates':selected_candidates},
            {'resource_id':'cjvt-thesaurus' if name=='synonyms' else 'cjvt-ksss',
             'version':'2.2','license':'CC-BY-SA-4.0',
             'fingerprint':request.app.state.synonyms.manifest['fingerprint'] if name=='synonyms' else request.app.state.collocations.manifest['build_id']},bool(truncated))
    status='partial' if any(v['status'] in {'error','unavailable','unsupported'} for v in results.values()) else 'ok'
    return bounded({'status':status,'lang':req.lang,'text':req.text,'selection':{'start':start,'end':end,'text':surface},
        'offset_convention':'unicode_codepoint_half_open','engine':annotation.get('engine_used') if annotation else req.engine,
        'results':results,'provenance':{'contract':'linghub.platform.v1','analysis_calls':1,
            'contextual_analysis_available':annotation is not None,'production_parity':'pending'}})
