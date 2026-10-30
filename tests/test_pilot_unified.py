"""Unified normal-query application flow with synthetic external providers.

These tests execute the real router, owned store, adapter orchestration, worker
and source routes. The provider response is a fixture, not live model evidence.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import pytest

from pilot_backend import editorial, terminology, unified
from pilot_backend.adapter import Gateway
from pilot_backend.api import create_app, config_checked
from pilot_backend.security import Failure
from pilot_backend.store import digest

ORIGIN='http://127.0.0.1:18310'
QUERY='synthetic common expression'


def core_test_root():
    """Use the release-selected CORE tree; keep the sibling only for local tests."""
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')
    root=(Path(configured).absolute() if configured else
          Path(__file__).resolve().parents[2]/'klikai-human-testing-20260911')
    if not(root/'tools/zrc_reference/pilot_contracts.py').is_file():
        raise RuntimeError('KLIKAI_PINNED_CORE_ROOT does not identify a CORE tree')
    return root


def term(slug,rid='shared-id',rationale='Qualification of this distinct sense.'):
    return {'source_slug':slug,'source_display_name':'Synthetic '+slug,'external_id':rid,
            'title':'Common expression','citation_id':rid,'record_kind':'terminology_decision',
            'rights_mode':'restricted','original_terms':[QUERY],'standardized_terms':['shared equivalent'],
            'proposals':[],'categories':[],'subcategories':[],'rationale':[rationale],
            'snippet':rationale,'matched_forms':[{'matched_query':QUERY,'match_tier':'exact',
            'field':'original','ambiguity_cost':0}],'score':10.0}


def full_record():
    return {'contract':editorial.RECORD_CONTRACT,'resource_id':'shared-id','source_slug':'jezikovna_posta',
            'source_label':editorial.SOURCES['jezikovna_posta'],'source_badge':'POŠTA','rights_mode':'restricted',
            'title':'Original published title','question_original':'Independent original question?',
            'answer_text':'Faithful original answer. '+('A necessary source qualification. '*20),
            'article_text':None,'additional_info':None,'references':[],'source_date':'2026-09-14',
            'url':None,'content_status':'complete_qa','complete_qa':True,'missing_fields':[],
            'release_id':'synthetic-release','content_hash':'a'*64,
            'access_policy':{'policy_id':editorial.OPERATOR_POLICY_ID,'scope':'authenticated_local_pilot',
                             'decision':'operator_enabled','source_slug':'jezikovna_posta'}}


class UnifiedGateway(Gateway):
    def __init__(self,*,source_failure=None,provider_failure=False,unknown_citation=False,freeze_failure=False,no_hits=False,
                 interpret_failure=False,interpret_response=None):
        super().__init__('http://127.0.0.1:18320','synthetic-token-'*4,
            klikai_url='http://127.0.0.1:8000',editorial_full_records=True,
            editorial_operator_policy_id=editorial.OPERATOR_POLICY_ID,unified_answers=True)
        self.calls=[];self.lock=threading.Lock();self.blocked=False
        self.source_failure=source_failure;self.provider_failure=provider_failure
        self.unknown_citation=unknown_citation;self.freeze_failure=freeze_failure;self.no_hits=no_hits
        self.interpret_failure=interpret_failure;self.interpret_response=interpret_response
        self.record=full_record();self.on_generate=None;self.barrier=None

    def require_generation(self,models):
        raise AssertionError('Normal unified query must retrieve before provider admission')

    def request(self,path,payload,base=None):
        assert not self.blocked,'Frozen sources must not contact any upstream'
        with self.lock:self.calls.append((path,deepcopy(payload),base))
        if path=='/internal/pilot/interpret':
            if self.interpret_failure:raise Failure('answer_unavailable',503)
            if self.interpret_response is not None:return deepcopy(self.interpret_response)
            from core.lexical_planner import plan
            request_plan=plan(payload['question'],payload['context'],payload['input_language'],
                              {'raw_engines':{}})['request_plan']
            request_plan['interpretation_contract']='klikai.request-interpretation.v1'
            request_plan['source_language']=(payload['input_language']
                if payload['input_language']in {'sl','it'}else None)
            request_plan['response_language']=payload['locale']
            return {'contract':'klikai.request-interpretation.v1',
                'input_sha256':digest({'contract':'klikai.request-interpretation.input.v1',
                                       **payload}),'request_plan':request_plan,
                'request_plan_sha256':digest(request_plan)}
        if path=='/api/sources':
            return {'sources':[{'slug':slug,'display_name':'Synthetic '+slug,'adapter_key':adapter,
                'rights_mode':rights,'source_type':'owned','is_active':True,'root_path':'PRIVATE_REGISTRY_PATH'}
                for slug,adapter,rights in [('zkb','luka_jsonl_decision','restricted'),
                    ('solska','luka_jsonl_decision','restricted'),('jezikovna_posta','luka_jsonl_qa','restricted'),
                    ('zadrege','ontology_answer_bank_json','owned'),('gradivo','obrazci_gradivo_manifest','linked_only'),
                    ('ontology','ontology_v2_jsonl','owned')]]}
        parsed=urlsplit(path);params=parse_qs(parsed.query)
        if parsed.path in {'/api/search','/api/terminology/lookup'}:
            source=params['sources'][0]
            if self.barrier is not None and source in {'zkb','solska'}:self.barrier.wait(timeout=3)
            if source==self.source_failure:raise Failure('source_timeout',503)
            if parsed.path=='/api/terminology/lookup':
                assert params['allow_partial']==params['include_prose']==params['use_morphology']==['true']
                hits=[term(source)]
                if source=='zkb':hits += [deepcopy(hits[0]),term(source,rationale='Different source sense and exception.')]
            else:
                hits=[{'source_slug':source,'resource_id':'shared-id','title':self.record['title']if source=='jezikovna_posta'else'Generic original',
                    'rights_mode':'restricted'if source=='jezikovna_posta'else'linked_only'if source=='gradivo'else'owned',
                    'snippet':'Synthetic documentary text.','format':'qa','chunk_kind':'structured_qa','chunk_index':0,'score':3.0}]
                if source=='zadrege':hits[0].update(original_text='Synthetic complete answer-bank original. A necessary qualification absent from the snippet.',source_status='draft',source_revision='synthetic-revision')
            if self.no_hits:hits=[]
            return {'query':params['q'][0],'sources':[source],'count':len(hits),'hits':hits}
        if parsed.path.startswith('/api/editorial/records/'):
            assert unquote(parsed.path.rsplit('/',1)[1])==self.record['resource_id']
            return deepcopy(self.record)
        if path=='/internal/pilot/freeze':
            if self.freeze_failure:raise Failure('answer_unavailable',503)
            packet=payload['unified_evidence']
            assert packet['version']==unified.PACKET
            matches=[]
            for incoming in packet['matches']:
                row=deepcopy(incoming);capture=row.pop('capture')
                assert digest(capture)==row['capture_sha256']
                row['evidence_id']='unified:'+digest({k:row[k]for k in('capture_kind','source_slug','external_id','capture_sha256')})
                row['source']={'title':row['title'],'source_label':row['source_label'],
                    'snippet':incoming['context_text'],'url':None,'bibliographic':{'rights_mode':row['rights_mode']}}
                matches.append(row)
            if not self.no_hits:
                matches.append({'evidence_id':'zrc:synthetic-original','source_slug':'jezikovna_svetovalnica',
                    'source_label':'Jezikovna svetovalnica','external_id':'zrc-original','title':'Reference original',
                    'snippet':'Synthetic connected ZRC answer.','url':None,'rights_mode':'authenticated_pilot',
                    'source':{'title':'Reference original','snippet':'Synthetic connected ZRC answer.','bibliographic':{}}})
            return {'bundle':{'synthetic_provider_capture':matches},'matches':matches,
                    'retrieval':packet['retrieval']+[{'family':'jezikovna_svetovalnica','status':'no_match'if self.no_hits else'ok',
                    'returned_count':0 if self.no_hits else 1,'limit':28,'has_more':None}]}
        if path=='/internal/pilot/generate':
            if self.on_generate:self.on_generate(payload)
            if self.provider_failure:raise Failure('answer_unavailable',503)
            matches=payload['bundle']['synthetic_provider_capture']
            chosen=[next(row for row in matches if row['source_slug']==slug)for slug in ('zkb','jezikovna_posta')]
            evidence=[{'evidence_id':row['evidence_id'],'external_id':row['external_id'],'source_slug':row['source_slug'],
                       'local_number':i+1,'title':row['title']}for i,row in enumerate(chosen)]
            if self.unknown_citation:evidence[0]['evidence_id']='unknown:not-admitted'
            return {'status':'validated','answer_kind':'generated','generation_status':'generated',
                'short_answer':'Synthetic provider synthesis [1] [2].','answer_md':'Synthetic provider synthesis [1] [2].',
                'evidence':evidence,'citations':[{'evidence_id':row['evidence_id']}for row in evidence],
                'source_badges':[{'source_slug':row['source_slug'],'label':row['source_slug']}for row in chosen],
                'model_identity':{'repository':'synthetic-provider','revision':'fixture','private_token':'DO_NOT_EXPORT'},
                'execution':{'device':'cpu','runtime':'fixture','private_origin':'DO_NOT_EXPORT'}}
        raise AssertionError('Unexpected request '+path)


def app_for(tmp_path,gateway):
    app=create_app({'store':str(tmp_path/'private/pilot.sqlite'),'allowed_origins':[ORIGIN],
        'secure_cookies':False,'tasks':['reference','terminology','jezikovna_posta'],
        'unified_answers':True,'klikai_url':gateway.klikai_url},adapter=gateway,
        hasher=PasswordHasher(time_cost=1,memory_cost=8,parallelism=1),start_workers=False)
    for name in ('unified-owner','unified-other'):
        app.state.auth.create(name,'synthetic-unified-test-password',origin='synthetic',must_change=False)
    return app


def login(client,name='unified-owner'):
    result=client.post('/api/pilot/login',json={'username':name,'password':'synthetic-unified-test-password'},headers={'Origin':ORIGIN})
    assert result.status_code==200
    return {'Origin':ORIGIN,'X-CSRF-Token':result.json()['csrf_token']}


def ask(app,client,headers,**fields):
    response=client.post('/api/pilot/families',headers=headers,json={'question':QUERY,'idempotency_key':'unified-default-001',**fields})
    assert response.status_code==200,response.text
    fid=response.json()['family']['answer_family_id']
    assert response.json()['family']['task']=='reference'
    job=app.state.jobs.claim('model');assert job and app.state.jobs.claim('cpu')is None
    app.state.jobs.execute(job)
    return fid,client.get('/api/pilot/families/'+fid).json()


def shared_plan(question,context='',target=None,input_language='und',locale='sl'):
    target=question if target is None else target
    start=question.index(target)
    return {'version':'klikai.request-plan.v1',
        'interpretation_contract':'klikai.request-interpretation.v1',
        'original':question,'context':context,
        'target_spans':[{'text':target,'start':start,'end':start+len(target),
                         'basis':'authenticated CORE interpretation'}],
        'candidate_lemmas':[],'operation':None,
        'source_language':input_language if input_language in {'sl','it'}else None,
        'translation_language':None,'response_language':locale,'requested_facets':[],
        'requested_grammar_slots':[],'domain':None,'jurisdiction':None,
        'requested_source':None,'requested_edition':None,'ambiguities':[],
        'confidence':'bounded_surface'}


def test_default_query_combines_all_families_generates_and_browses_owned_frozen_matches(tmp_path):
    gateway=UnifiedGateway();gateway.barrier=threading.Barrier(2)
    app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client)
        assert client.get('/api/pilot/session').json()['capabilities']['unified_answers']
        fid,family=ask(app,client,headers);short=family['short']
        assert short['state']=='complete'and short['generation']['status']=='generated'
        assert short['answer_kind']=='generated'and short['generation_status']=='generated'
        assert short['generation']['provider']=={'repository':'synthetic-provider','revision':'fixture','device':'cpu','runtime':'fixture'}
        assert family['matches_available']and short['retrieval']['status']=='complete'
        assert len([path for path,_,_ in gateway.calls if path=='/internal/pilot/generate'])==1
        first=client.get('/api/pilot/families/'+fid+'/matches?offset=0&limit=2').json()
        assert first['returned_count']==7 and first['next_offset']==2 and first['corpus_total']is None
        allrows=[];offset=0
        while offset is not None:
            page=client.get('/api/pilot/families/'+fid+f'/matches?offset={offset}&limit=2').json()
            allrows.extend(page['items']);offset=page['next_offset']
        assert len({row['evidence_id']for row in allrows})==7
        assert len([row for row in allrows if row['source_slug']=='zkb'])==2
        assert {row['source_slug']for row in allrows}>={'zkb','solska','jezikovna_posta','zadrege','gradivo','jezikovna_svetovalnica'}
        assert sum(row['used_in_answer']for row in allrows)==2
        body=json.dumps(first)
        assert 'PRIVATE_REGISTRY_PATH'not in body and 'DO_NOT_EXPORT'not in json.dumps(short)
        chosen=next(row for row in allrows if row['source_slug']=='jezikovna_posta')
        route='/api/pilot/families/'+fid+'/sources/'+chosen['evidence_id']
        drawer=client.get(route).json()
        assert gateway.record['answer_text']in drawer['excerpt']and gateway.record['question_original']in drawer['excerpt']
        unused=next(row for row in allrows if not row['used_in_answer'])
        assert client.get('/api/pilot/families/'+fid+'/sources/'+unused['evidence_id']).status_code==200
        gateway.blocked=True
        assert client.get(route).json()==drawer
        assert client.get('/api/pilot/families/'+fid).json()==family
        assert client.get('/api/pilot/families/'+fid+'/sources/unknown').status_code==404
        assert client.get('/api/pilot/families/'+fid+'/matches?limit=51').status_code==400
        login(client,'unified-other')
        for path in ('/api/pilot/families/'+fid,'/api/pilot/families/'+fid+'/matches',route):
            assert client.get(path).status_code==404


def test_normal_route_uses_shared_interpretation_first_without_local_fallback(
        tmp_path,monkeypatch):
    original='  '+QUERY+'  ';context='Nespremenjeni dodatni kontekst.'
    request={'question':original,'context':context,'input_language':'sl','locale':'sl'}
    request_plan=shared_plan(original,context,QUERY,input_language='sl')
    response={'contract':'klikai.request-interpretation.v1',
        'input_sha256':digest({'contract':'klikai.request-interpretation.input.v1',
                               **request}),'request_plan':deepcopy(request_plan),
        'request_plan_sha256':digest(request_plan)}
    gateway=UnifiedGateway(interpret_response=response)
    from core import lexical_planner
    monkeypatch.setattr(lexical_planner,'plan',lambda *args,**kwargs:(_ for _ in ()).throw(
        AssertionError('Normal WEB route must not plan locally')))
    app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        _,family=ask(app,client,login(client),question=original,context=context,
                     input_language='sl')
        assert family['short']['generation']['status']=='generated'
    assert gateway.calls[0]==('/internal/pilot/interpret',request,None)
    frozen=next(payload for path,payload,_ in gateway.calls
                if path=='/internal/pilot/freeze')
    assert (frozen['question'],frozen['context'],frozen['input_language'])==(
        original,context,'sl')
    assert frozen['request_plan']==request_plan
    assert frozen['request_plan_sha256']==response['request_plan_sha256']


@pytest.mark.parametrize('failure',[
    'upstream','input_digest','plan_digest','offset','language_binding'])
def test_interpretation_failure_stops_before_registry_tools_or_sources(failure):
    request={'question':QUERY,'context':'','input_language':'und','locale':'sl'}
    request_plan=shared_plan(QUERY)
    response={'contract':'klikai.request-interpretation.v1',
        'input_sha256':digest({'contract':'klikai.request-interpretation.input.v1',
                               **request}),'request_plan':deepcopy(request_plan),
        'request_plan_sha256':digest(request_plan)}
    if failure=='input_digest':response['input_sha256']='0'*64
    if failure=='plan_digest':response['request_plan_sha256']='0'*64
    if failure=='offset':
        response['request_plan']['target_spans'][0]['end']-=1
        response['request_plan_sha256']=digest(response['request_plan'])
    if failure=='language_binding':
        response['request_plan']['source_language']='sl'
        response['request_plan_sha256']=digest(response['request_plan'])
    gateway=UnifiedGateway(interpret_failure=failure=='upstream',
                           interpret_response=None if failure=='upstream'else response)
    with pytest.raises(Failure):
        unified.freeze(gateway,{'question':QUERY,'context':'','input_language':'und',
                                'locale':'sl'}, {})
    assert gateway.calls==[('/internal/pilot/interpret',request,None)]


def test_core_plan_mismatch_is_request_local_failure_not_source_only():
    class MismatchGateway(UnifiedGateway):
        def request(self,path,payload,base=None):
            if path=='/internal/pilot/freeze':
                with self.lock:self.calls.append((path,deepcopy(payload),base))
                raise Failure('request_plan_mismatch',409)
            return super().request(path,payload,base)
    gateway=MismatchGateway()
    with pytest.raises(Failure,match='request_plan_mismatch')as failure:
        unified.freeze(gateway,{'question':QUERY,'context':'',
            'input_language':'und','locale':'sl'}, {})
    assert failure.value.status==409
    assert any(path=='/internal/pilot/freeze'for path,_,_ in gateway.calls)


@pytest.mark.parametrize('option', ['provider_failure','unknown_citation','freeze_failure'])
def test_generation_failures_preserve_retrieved_results_without_generated_success(tmp_path,option):
    gateway=UnifiedGateway(**{option:True});app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        fid,family=ask(app,client,login(client));short=family['short']
        assert short['state']=='complete'and short['answer_kind']=='source_only'
        assert short['generation_status']=='not_generated'and short['generation']['status']in {'failed','unavailable'}
        assert short['evidence']==[]and short['source_badges']==[]
        if option!='freeze_failure':
            assert short['generation']['error_code']==('unknown_citation'if option=='unknown_citation'else'answer_unavailable')
            assert short['generation']['stage']==('citation_validation'if option=='unknown_citation'else'provider_request')
        page=client.get('/api/pilot/families/'+fid+'/matches').json()
        assert len(page['items'])>=6 and not any(row['used_in_answer']for row in page['items'])
        gateway.blocked=True
        assert client.get('/api/pilot/families/'+fid+'/sources/'+page['items'][0]['evidence_id']).status_code==200


@pytest.mark.parametrize('aliases', [
    {'evidence_id':'known'}, {'citation_id':'known'}, {'passage_id':'known'},
    {'evidence_id':'known','citation_id':'known','passage_id':'known'},
])
def test_generated_evidence_normalizes_validated_alias_without_rewriting_source_or_prose(aliases):
    from types import SimpleNamespace
    evidence=dict(aliases,source_slug='synthetic',external_id='original-id',
        source_revision='retained-revision',bibliographic={'date':'2026-09-14'},snippet='Exact original.')
    result={'status':'validated','answer_kind':'generated','answer_md':'Exact signed claim.',
        'short_answer':'Exact signed claim.','display_sha256':'signed-display-pin',
        'display_blocks':[{'block_id':'C1','text':'Exact signed claim.','evidence_ids':['known']}],
        'evidence':[deepcopy(evidence)],'citations':[{'claim_id':'C1','evidence_id':'known'}]}
    before=deepcopy(result)
    body={'contract':unified.CONTRACT,'locale':'sl','matches':[{'evidence_id':'known'}],
        'gateway_bundle':{'synthetic':True},'retrieval':{'status':'complete'}}
    output=unified.generate(SimpleNamespace(request=lambda *args:result),
        dict(body,snapshot_sha256=digest(body)),'short','qwen')
    assert output['generation']['status']=='generated'
    assert output['evidence']==[dict(evidence,evidence_id='known',local_number=1)]
    for key in ('answer_md','short_answer','display_sha256','display_blocks','citations'):
        assert output[key]==before[key]
    assert {eid for block in output['display_blocks']for eid in block['evidence_ids']}=={
        row['evidence_id']for row in output['evidence']}
    assert all(type(row['local_number'])is int for row in output['evidence'])


@pytest.mark.parametrize('field', ['evidence','citations'])
@pytest.mark.parametrize('conflict', ['other-known','unknown',17])
def test_generated_evidence_rejects_conflicting_aliases_even_when_preferred_id_is_frozen(field,conflict):
    from types import SimpleNamespace
    result={'status':'validated','answer_kind':'generated','answer_md':'Claim [1].',
        'evidence':[{'evidence_id':'known'}],'citations':[{'evidence_id':'known'}]}
    result[field][0]['citation_id']=conflict
    body={'contract':unified.CONTRACT,'locale':'sl','matches':[{'evidence_id':'known'},{'evidence_id':'other-known'}],
        'gateway_bundle':{'synthetic':True},'retrieval':{'status':'complete'}}
    output=unified.generate(SimpleNamespace(request=lambda *args:result),
        dict(body,snapshot_sha256=digest(body)),'short','qwen')
    assert {key:output['generation'][key]for key in ('status','error_code','stage')}=={
        'status':'failed','error_code':'unknown_citation','stage':'citation_validation'}
    assert output['evidence']==[]and output['generation_status']=='not_generated'


def bounded_answer_plan_citation(*,evidence_id='known',support_id='S1',claim_id='C1',
        block_id='B1',unit_id='U0000000000000001'):
    return {'block_id':block_id,'claim_id':claim_id,'unit_id':unit_id,
        'evidence_id':evidence_id,'support_id':support_id,'source_key':'synthetic',
        'snippet':'Exact source support.','locator':{'synthetic':True},
        'char_start':0,'char_end':21,'source_id':'synthetic:record-1',
        'source_revision':'b'*64}


def bounded_answer_plan_evidence(evidence_id='known'):
    return {'evidence_id':evidence_id,'source_slug':'synthetic',
        'external_id':'record-1','source_revision':'b'*64,
        'snippet':'Exact source support.','locator':{'synthetic':True}}


def bounded_answer_plan_result():
    return {'status':'validated','answer_kind':'generated',
        'generation_status':'deterministic_bounded_answer_plan',
        'synthesis_mode':'deterministic_bounded_answer_plan',
        'answer_support':'supported',
        'answer_md':'Exact server-rendered claim.','short_answer':'Exact server-rendered claim.',
        'display_blocks':[{'block_id':'B1','paragraph_id':'P1','unit_id':'U0000000000000001',
            'unit_type':'documentary_fact','claim_id':'C1','text':'Exact server-rendered claim.',
            'support_ids':['S1']}],
        'claims':[{'claim_id':'C1','paragraph_id':'P1','unit_id':'U0000000000000001',
            'text':'Exact server-rendered claim.','support_ids':['S1'],'fact_ids':['F1'],
            'section':'answer','depends_on':[],
            'support_mode':'deterministic_bounded_answer_plan'}],
        'evidence':[bounded_answer_plan_evidence()],
        'citations':[bounded_answer_plan_citation()],'limitations':[],
        'validation':{'status':'bounded_answer_plan_rendered',
            'semantic_entailment_certified':False}}


def coverage_answer_plan_result():
    text='Exact server-rendered coverage limitation.'
    unit_id='U0000000000000002'
    return {'status':'partial','answer_kind':'source_only',
        'generation_status':'deterministic_bounded_answer_plan',
        'synthesis_mode':'deterministic_bounded_answer_plan','answer_support':'partial',
        'answer_md':text,'short_answer':text,
        'display_blocks':[{'block_id':'B1','paragraph_id':'P1','unit_id':unit_id,
            'unit_type':'coverage_limitation','claim_id':None,'text':text,'support_ids':[]}],
        'claims':[],'citations':[],'evidence':[],'source_badges':[],
        'limitations':[{'code':'partial_evidence','scope':'answer','claim_ids':[],
            'unit_id':unit_id,'message':text,'coverage_receipt_id':'coverage-1',
            'coverage_receipt_sha256':'a'*64}],
        'validation':{'status':'bounded_answer_plan_rendered',
            'semantic_entailment_certified':False}}


def mixed_answer_plan_result():
    result=bounded_answer_plan_result();coverage=coverage_answer_plan_result()
    block=deepcopy(coverage['display_blocks'][0]);block['block_id']='B2';block['paragraph_id']='P2'
    result['display_blocks'].append(block);result['limitations']=deepcopy(coverage['limitations'])
    result.update(status='partial',answer_support='partial')
    result['answer_md']=result['short_answer']='Exact server-rendered claim.\n\n'+block['text']
    return result


def run_bounded_answer_plan(result,matches=None):
    from types import SimpleNamespace
    known={'known','other-known'}|{unified.evidence_id(row)for row in
        [*result.get('evidence',[]),*result.get('citations',[])]}
    body={'contract':unified.CONTRACT,'locale':'sl',
        'matches':(matches if matches is not None else
            [{'evidence_id':value}for value in sorted(known)if value]),
        'gateway_bundle':{'synthetic':True},'retrieval':{'status':'complete'}}
    return unified.generate(SimpleNamespace(request=lambda *args:result),
        dict(body,snapshot_sha256=digest(body)),'short','qwen')


def frozen_match_from_evidence(evidence):
    external_id=evidence['external_id'];source_key=evidence['source_slug']
    source_id=(external_id if ':'in external_id else source_key+':'+external_id)
    return {'evidence_id':unified.evidence_id(evidence),'source_slug':source_key,
        'external_id':external_id,'source':{'locator':deepcopy(evidence['locator'])},
        '_citation_binding':{'source_key':source_key,'external_id':external_id,
            'source_id':source_id,'source_revision':evidence['source_revision'],
            'locator_sha256':digest(evidence['locator']),
            'text_sha256':digest(evidence['snippet']),
            'text_length':len(evidence['snippet'])}}


def frozen_matches_from_result(result):
    return [frozen_match_from_evidence(row)for row in result['evidence']]


def bounded_frozen_match():
    return frozen_match_from_evidence(bounded_answer_plan_evidence())


def test_frozen_citation_binding_accepts_only_exact_citation_and_repairs_reader_projection():
    result=bounded_answer_plan_result()
    result['evidence'][0].update(source_slug='display-drift',external_id='display-drift',
        source_revision='display-drift',locator={'display-drift':True})
    output=run_bounded_answer_plan(result,[bounded_frozen_match()])
    assert output['generation']['status']=='generated'
    assert {key:output['evidence'][0][key]for key in
            ('source_slug','external_id','source_revision','locator')}=={
        'source_slug':'synthetic','external_id':'record-1',
        'source_revision':'b'*64,'locator':{'synthetic':True}}


def test_frozen_documentary_binding_preserves_core_namespace_for_colon_native_id():
    result=bounded_answer_plan_result()
    result['display_blocks'][0]['unit_type']='grammar_slot'
    result['evidence'][0]['external_id']='synthetic:native-record-1'
    result['citations'][0]['source_id']='synthetic:synthetic:native-record-1'
    evidence=result['evidence'][0]
    match={'evidence_id':unified.evidence_id(evidence),
        'source_slug':evidence['source_slug'],'capture_sha256':'c'*64,
        'source':{'locator':deepcopy(evidence['locator'])}}
    view=dict(evidence,citation_id=unified.evidence_id(evidence))
    match['_citation_binding']=unified.frozen_citation_binding(match,view)
    assert match['_citation_binding']['source_id']=='synthetic:synthetic:native-record-1'
    output=run_bounded_answer_plan(result,[match])
    assert output['generation']['status']=='generated'


def test_grammar_and_colon_native_documentary_citations_share_strict_graph():
    result=bounded_answer_plan_result()
    result['display_blocks'][0]['unit_type']='grammar_slot'
    result['display_blocks'].append({'block_id':'B2','paragraph_id':'P2',
        'unit_id':'U0000000000000002','unit_type':'exact_source_excerpt',
        'claim_id':'C2','text':'Additional exact documentary support.',
        'support_ids':['S2']})
    result['claims'].append({'claim_id':'C2','paragraph_id':'P2',
        'unit_id':'U0000000000000002','text':'Additional exact documentary support.',
        'support_ids':['S2'],'fact_ids':[],'section':'answer','depends_on':[],
        'support_mode':'deterministic_bounded_answer_plan'})
    documentary={'evidence_id':'documentary','source_slug':'synthetic',
        'external_id':'synthetic:native-record-2','source_revision':'c'*64,
        'snippet':'Exact documentary support.','locator':{'documentary':True}}
    result['evidence'].append(documentary)
    result['citations'].append({'block_id':'B2','claim_id':'C2',
        'unit_id':'U0000000000000002','evidence_id':'documentary',
        'support_id':'S2','source_key':'synthetic',
        'snippet':'Exact documentary support.','locator':{'documentary':True},
        'char_start':0,'char_end':26,
        'source_id':'synthetic:synthetic:native-record-2',
        'source_revision':'c'*64})
    result['answer_md']=result['short_answer']=unified.deterministic_answer_text(
        result['display_blocks'])
    match={'evidence_id':'documentary','source_slug':'synthetic',
        'capture_sha256':'c'*64,'source':{'locator':{'documentary':True}}}
    view=dict(documentary,citation_id='documentary')
    match['_citation_binding']=unified.frozen_citation_binding(match,view)
    output=run_bounded_answer_plan(
        result,[frozen_match_from_evidence(result['evidence'][0]),match])
    assert output['generation']['status']=='generated'
    assert {row['source_id']for row in output['citations']}=={
        'synthetic:record-1','synthetic:synthetic:native-record-2'}


@pytest.mark.parametrize(('damage','reason'),[
    ('source_key','source_key'),('locator','locator'),('source_id','source_id'),
    ('source_revision','source_revision'),('snippet','snippet'),('range','range'),
    ('evidence_text','evidence_text')])
def test_frozen_citation_binding_rejects_every_changed_citation_fact(damage,reason):
    result=bounded_answer_plan_result()
    if damage=='source_key':result['citations'][0]['source_key']='forged'
    elif damage=='locator':result['citations'][0]['locator']={'forged':True}
    elif damage=='source_id':result['citations'][0]['source_id']='synthetic:forged'
    elif damage=='source_revision':result['citations'][0]['source_revision']='f'*64
    elif damage=='snippet':result['citations'][0]['snippet']='Forged source support.'
    elif damage=='range':result['citations'][0]['char_end']=999
    elif damage=='evidence_text':result['evidence'][0]['snippet']='Changed source support.'
    output=run_bounded_answer_plan(result,[bounded_frozen_match()])
    assert {key:output['generation'][key]for key in
            ('status','error_code','stage','citation_mismatch')}=={
        'status':'failed','error_code':'unknown_citation',
        'stage':'citation_validation','citation_mismatch':reason}
    assert output['evidence']==[]


def test_duplicate_frozen_evidence_identity_is_rejected_before_binding_lookup():
    match=bounded_frozen_match()
    output=run_bounded_answer_plan(
        bounded_answer_plan_result(),[match,deepcopy(match)])
    assert {key:output['generation'][key]for key in ('status','error_code','stage')}=={
        'status':'failed','error_code':'unknown_citation','stage':'citation_validation'}


def test_deterministic_bounded_answer_plan_requires_and_binds_the_exact_structured_graph():
    output=run_bounded_answer_plan(bounded_answer_plan_result())
    assert output['generation']['status']=='generated'
    assert output['display_blocks'][0]['evidence_ids']==['known']


@pytest.mark.parametrize('unit_type',[
    'curated_recommendation','comparison_synthesis','grammar_slot'])
def test_deterministic_claim_unit_types_use_the_same_bound_graph(unit_type):
    result=bounded_answer_plan_result()
    result['display_blocks'][0]['unit_type']=unit_type
    output=run_bounded_answer_plan(result,frozen_matches_from_result(result))
    assert output['generation']['status']=='generated'
    assert output['generation_status']=='deterministic_bounded_answer_plan'
    assert output['display_blocks'][0]['evidence_ids']==['known']


def test_real_core_comparison_and_curated_units_cross_the_web_consumer(monkeypatch):
    """Use CORE's real registries/renderers, not relabelled WEB fixtures."""
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
    from test_documentary_constraints import evidence
    from test_pilot_answer_plan import curated_item
    from tools.zrc_reference.request_interpretation import response as interpret_response

    runtime=_plan_runtime()
    question='lesenka';curated_rows=[curated_item(key='curated')]
    curated_rows[0].update(span={'external_id':'curated'},source_id='source:curated',
        source_revision='c'*64,url='https://www.jeziknaklik.it/')
    curated_payload=_plan_runtime_payload(question,curated_rows,deepcopy(curated_rows),
        request_plan=interpret_response(question,'','sl','sl')['request_plan'])
    curated_registry=runtime._answer_registry(curated_payload,'answer')
    curated_unit=next(row for row in curated_registry.units
                      if row['unit_type']=='curated_recommendation')
    curated_result=runtime._process_fresh_answer_plan({'answer_language':'sl','blocks':[{
        'block_id':'B1','unit_id':curated_unit['unit_id'],'paragraph_id':'P1'}],
        'no_sufficient_evidence':False},curated_registry,
        payload=curated_payload,tier='answer')

    question='Je prav "glodalec" ali "glodavec"?'
    advice,_=evidence('Pravopis dopušča obe obliki, glodalec in glodavec. '
        'Slovar daje prednost zapisu glodavec.',key='advice',
        source_slug='jezikovna_svetovalnica',signed_date_text=['januar 2013'])
    usage,_=evidence('V preteklosti je bila pogostejša oblika glodavec, danes pa '
        'je v rabi pogostejša oblika glodalec.',key='usage',
        source_slug='jezikovna_svetovalnica',signed_date_text=['junij 2023'])
    comparison_rows=[advice,usage];passages=[]
    for index,item in enumerate(comparison_rows,1):
        item['span']={'post_id':str(index),'char_start':0,'char_end':len(item['text'])}
        passages.append(dict(item,source_id=f'source-record:{index}',
            source_revision=str(index)*64,title=f'Nasvet {index}',
            url=f'https://example.invalid/{index}',role='expert_answer',
            rights_mode='authenticated_pilot',context_eligible=True,
            span=deepcopy(item['span'])))
    comparison_payload=_plan_runtime_payload(question,comparison_rows,passages,
        request_plan=interpret_response(question,'','sl','sl')['request_plan'])
    comparison_registry=runtime._answer_registry(comparison_payload,'answer')
    comparison_unit=next(row for row in comparison_registry.units
                         if row['unit_type']=='comparison_synthesis')
    comparison_result=runtime._process_fresh_answer_plan({
        'answer_language':'sl','blocks':[{'block_id':'B1',
            'unit_id':comparison_unit['unit_id'],'paragraph_id':'P1'}],
        'no_sufficient_evidence':False},comparison_registry,
        payload=comparison_payload,tier='answer')

    for result,unit_type in ((curated_result,'curated_recommendation'),
                             (comparison_result,'comparison_synthesis')):
        output=run_bounded_answer_plan(result,frozen_matches_from_result(result))
        assert output['generation']['status']=='generated',json.dumps(output,indent=2)
        assert output['generation_status']=='deterministic_bounded_answer_plan'
        assert output['display_blocks'][0]['unit_type']==unit_type
        assert output['display_blocks'][0]['evidence_ids']


def test_real_core_marko_homograph_grammar_unit_crosses_the_web_consumer(monkeypatch):
    """Exercise the exact CORE producer shape used by the ordinary name question."""
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
    from test_pilot_answer_plan import recorded_grammar_slot_item
    from tools.zrc_reference.request_interpretation import authoritative_request_plan

    question='Prosim, kako se sklanja ime Marko v rodilniku?'
    masculine,masculine_source=recorded_grammar_slot_item(
        'Marko','Marka',entry='lexeme:masculine',key='marko-masculine')
    feminine,feminine_source=recorded_grammar_slot_item(
        'Marko','Marko',entry='lexeme:feminine',key='marko-feminine',
        gender='feminine')
    rows=[masculine,feminine];passages=[masculine_source,feminine_source]
    for item,source in zip(rows,passages):
        item.update(span=deepcopy(source['span']),source_id=source['source_id'],
                    source_revision=source['source_revision'])
    request_plan=authoritative_request_plan(
        question,'',input_language='sl',locale='sl')
    payload=_plan_runtime_payload(
        question,rows,passages,request_plan=request_plan)
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    unit=next(row for row in registry.units if row['unit_type']=='grammar_slot')
    result=runtime._process_fresh_answer_plan({'answer_language':'sl','blocks':[{
        'block_id':'B1','unit_id':unit['unit_id'],'paragraph_id':'P1'}],
        'no_sufficient_evidence':False},registry,payload=payload,tier='answer')

    output=run_bounded_answer_plan(result,frozen_matches_from_result(result))
    assert output['generation']['status']=='generated',json.dumps(output,indent=2)
    assert output['answer_md']==(
        'Če gre za moško ime »Marko«, je rodilnik »Marka«; če gre za žensko '
        'ime »Marko«, je rodilnik »Marko« (vir: Sloleks).')
    assert output['display_blocks'][0]['unit_type']=='grammar_slot'
    assert len(output['display_blocks'][0]['evidence_ids'])==2


@pytest.mark.parametrize('locator',[
    {},
    {'section':'A'},
    {'section':'A','page':2,'range':{'start':1,'tags':['x','y']},'extra':True},
    {'section':'A','page':'2','range':{'start':1,'tags':['x','y']}},
    {'section':'A','page':2,'range':{'start':1,'tags':['y','x']}},
])
def test_deterministic_citation_requires_nonempty_exact_deep_locator(locator):
    result=bounded_answer_plan_result()
    verified={'section':'A','page':2,'range':{'start':1,'tags':['x','y']}}
    result['evidence'][0]['locator']=verified
    result['citations'][0]['locator']=locator
    output=run_bounded_answer_plan(result)
    assert {key:output['generation'][key]for key in ('status','error_code','stage')}=={
        'status':'failed','error_code':'unknown_citation','stage':'citation_validation'}


def test_deterministic_citation_locator_object_key_order_is_semantically_equal():
    result=bounded_answer_plan_result()
    result['evidence'][0]['locator']={
        'section':'A','page':2,'range':{'start':1,'tags':['x','y']}}
    result['citations'][0]['locator']={
        'range':{'tags':['x','y'],'start':1},'page':2,'section':'A'}
    output=run_bounded_answer_plan(result)
    assert output['generation']['status']=='generated'


def test_deterministic_coverage_only_plan_is_a_qualified_partial_not_generated_support():
    output=run_bounded_answer_plan(coverage_answer_plan_result())
    assert (output['status'],output['answer_kind'],output['generation_status'])==(
        'partial','source_only','deterministic_bounded_answer_plan')
    assert output['generation']['status']=='partial'and output['evidence']==[]
    assert output['display_blocks'][0]['evidence_ids']==[]


@pytest.mark.parametrize('damage',[
    'claim','citation','evidence','support','claim_id','unit_type','extra_block_field',
    'missing_receipt','wrong_receipt_hash','wrong_limitation_unit','wrong_limitation_text',
    'extra_limitation_field','answer_text','validated_status','source_badge',
])
def test_deterministic_coverage_only_plan_rejects_provenance_or_support_mutations(damage):
    result=coverage_answer_plan_result();block=result['display_blocks'][0]
    limitation=result['limitations'][0]
    if damage=='claim':result['claims']=[deepcopy(bounded_answer_plan_result()['claims'][0])]
    elif damage=='citation':result['citations']=[bounded_answer_plan_citation()]
    elif damage=='evidence':result['evidence']=[bounded_answer_plan_evidence()]
    elif damage=='support':block['support_ids']=['S1']
    elif damage=='claim_id':block['claim_id']='C1'
    elif damage=='unit_type':block['unit_type']='documentary_fact'
    elif damage=='extra_block_field':block['evidence_ids']=[]
    elif damage=='missing_receipt':limitation.pop('coverage_receipt_id')
    elif damage=='wrong_receipt_hash':limitation['coverage_receipt_sha256']='not-a-receipt'
    elif damage=='wrong_limitation_unit':limitation['unit_id']='Uffffffffffffffff'
    elif damage=='wrong_limitation_text':limitation['message']='Rewritten limitation.'
    elif damage=='extra_limitation_field':limitation['invented']='not-core'
    elif damage=='answer_text':result['answer_md']='Rewritten limitation.'
    elif damage=='validated_status':result['status']='validated'
    elif damage=='source_badge':result['source_badges']=[{'source_slug':'synthetic'}]
    output=run_bounded_answer_plan(result)
    assert {key:output['generation'][key]for key in ('status','error_code','stage')}=={
        'status':'failed','error_code':'unknown_citation','stage':'citation_validation'}
    assert output['generation_status']=='not_generated'and output['evidence']==[]


def test_actual_core_coverage_renderer_shape_is_admitted_without_inventing_support(monkeypatch):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_documentary_constraints import evidence as core_evidence
    from test_pilot_answer_plan import input_profile,signed,PROFILE_KEY,COVERAGE_KEY
    from tools.zrc_reference.common import value_hash
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry,process_answer_plan
    from tools.zrc_reference.pilot_documentary_constraints import source_contracts
    item,_=core_evidence('Oblika glodavec je izpričana v SP 2001.')
    profile=input_profile([item]);receipt=signed({'receipt_id':'coverage-actual',
        'target':'žbrontavec','inspected_scope':'priloženem ohranjenem gradivu',
        'reason_code':'not_confirmed_in_supplied_material',
        'profile_sha256':value_hash(profile)},COVERAGE_KEY)
    registry=build_answer_unit_registry(locale='sl',question='q',context='',
        selected_evidence=[item],fact_catalog=source_contracts([item]),
        signed_input_profile=profile,profile_signing_key=PROFILE_KEY,
        coverage_receipts=[receipt],coverage_signing_key=COVERAGE_KEY)
    unit=next(row for row in registry.units if row['unit_type']=='coverage_limitation')
    rendered=process_answer_plan({'answer_language':'sl','blocks':[{'block_id':'B1',
        'unit_id':unit['unit_id'],'paragraph_id':'P1'}],'no_sufficient_evidence':False},
        registry,locale='sl')
    original_limitation=rendered['limitations'][0]
    result={'status':'partial','answer_kind':'source_only','answer_support':'partial',
        'generation_status':'deterministic_bounded_answer_plan',
        'synthesis_mode':'deterministic_bounded_answer_plan','answer_md':rendered['answer_md'],
        'short_answer':rendered['short_answer'],'display_blocks':rendered['display_blocks'],
        'claims':rendered['claims'],'citations':[],'evidence':[],'source_badges':[],
        'limitations':[{'code':'partial_evidence','scope':'answer','claim_ids':[],
            'unit_id':unit['unit_id'],'message':rendered['display_blocks'][0]['text'],
            'coverage_receipt_id':original_limitation['receipt_id'],
            'coverage_receipt_sha256':original_limitation['receipt_sha256']}],
        'validation':rendered['validation']}
    output=run_bounded_answer_plan(result)
    assert output['generation']['status']=='partial'and output['answer_md']==rendered['answer_md']
    assert output['answer_kind']=='source_only'and output['evidence']==[]


@pytest.mark.parametrize('damage',[
    'invented_without_receipt','orphan_receipt','reused_unit','duplicate_receipt',
    'mismatched_unit','mismatched_text','mismatched_hash','mismatched_code',
    'answer_md','short_answer','block_order','paragraph_group','duplicate_task_limit',
    'task_limit_extra','task_limit_unit','unknown_answer_limit',
])
def test_mixed_deterministic_graph_rejects_unbound_coverage_and_text_mutations(damage):
    result=mixed_answer_plan_result();coverage=result['display_blocks'][1]
    limitation=result['limitations'][0]
    if damage=='invented_without_receipt':
        result['display_blocks'].append({'block_id':'B3','paragraph_id':'P3',
            'unit_id':'U0000000000000003','unit_type':'coverage_limitation',
            'claim_id':None,'text':'Invented limitation.','support_ids':[]})
    elif damage=='orphan_receipt':
        orphan=deepcopy(limitation);orphan['unit_id']='U0000000000000003'
        result['limitations'].append(orphan)
    elif damage=='reused_unit':
        coverage['unit_id']=result['display_blocks'][0]['unit_id']
        limitation['unit_id']=coverage['unit_id']
    elif damage=='duplicate_receipt':result['limitations'].append(deepcopy(limitation))
    elif damage=='mismatched_unit':limitation['unit_id']='Uffffffffffffffff'
    elif damage=='mismatched_text':limitation['message']='Invented limitation.'
    elif damage=='mismatched_hash':limitation['coverage_receipt_sha256']='f'*63
    elif damage=='mismatched_code':limitation['code']='unsupported_inference'
    elif damage=='answer_md':result['answer_md']='Fabricated answer.'
    elif damage=='short_answer':result['short_answer']='Fabricated answer.'
    elif damage=='block_order':result['display_blocks'].reverse()
    elif damage=='paragraph_group':coverage['paragraph_id']='P1'
    elif damage=='duplicate_task_limit':
        row={'code':'comparison_coverage_incomplete','scope':'answer','claim_ids':[]}
        result['limitations'].extend([row,deepcopy(row)])
    elif damage=='task_limit_extra':result['limitations'].append({
        'code':'comparison_coverage_incomplete','scope':'answer','claim_ids':[],
        'invented':'not-core'})
    elif damage=='task_limit_unit':result['limitations'].append({
        'code':'comparison_coverage_incomplete','scope':'answer','claim_ids':[],
        'unit_id':coverage['unit_id']})
    elif damage=='unknown_answer_limit':result['limitations'].append({
        'code':'invented_limitation','scope':'answer','claim_ids':[]})
    output=run_bounded_answer_plan(result)
    assert {key:output['generation'][key]for key in ('status','error_code','stage')}=={
        'status':'failed','error_code':'unknown_citation','stage':'citation_validation'}


def test_actual_core_mixed_claim_and_coverage_graph_is_admitted(monkeypatch):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
    from test_fulltext_grounded import passage
    from tools.zrc_reference.grounded import packet
    from tools.zrc_reference.pilot_evidence import annotate
    sources=[passage(text='glodavec — geslo SP 2001.',passage_id='glodavec-entry'),
             passage(text='Slovar daje prednost zapisu glodavec.',passage_id='preference')]
    rows=annotate(packet(sources));rows[0].update(role='documentary_capture',
        normative_status='current_reference',publication_scope='resource_record',title='glodavec')
    payload=_plan_runtime_payload('Ali je prav "glodavec" ali "žbrontavec"?',rows,sources,
        coverage={'complete':False,'targets':['glodavec','žbrontavec'],
                  'missing_targets':['žbrontavec']})
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    units={row['unit_id']:row for row in registry.units}
    selected=[units[unit_id]for unit_id in registry.required_unit_ids]
    raw={'answer_language':'sl','blocks':[{'block_id':f'B{index}',
        'unit_id':unit['unit_id'],'paragraph_id':f'P{index}'}
        for index,unit in enumerate(selected,1)],'no_sufficient_evidence':False}
    result=runtime._process_fresh_answer_plan(raw,registry,payload=payload,tier='answer')
    assert any(row['unit_type']=='coverage_limitation'for row in result['display_blocks'])
    assert result['claims']and result['citations']and result['status']=='partial'
    visible={row['citation_id']:row for row in result['evidence']}
    for citation in result['citations']:
        source=visible[citation['evidence_id']]
        expected=(source['external_id']if':'in source['external_id']else
                  source['source_slug']+':'+source['external_id'])
        assert citation['source_id']==expected
        assert citation['source_revision']==source['source_revision']
        assert citation['locator']and citation['locator']==source['locator']
    output=run_bounded_answer_plan(result)
    assert output['generation']['status']=='partial'
    assert output['answer_kind']=='generated'and output['evidence']


@pytest.mark.parametrize('damage',['drop_processing','relabel_supported','validation_status'])
def test_actual_core_broad_inflection_cannot_drop_or_relabel_processing_limit(monkeypatch,damage):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_bounded_recomposition import _plan_runtime
    from test_pilot_answer_plan import exact_excerpt_item,input_profile,PROFILE_KEY
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry
    item,source=exact_excerpt_item();question='Kako sklanjamo Hugo?'
    locator={'synthetic':True};item['span']=locator
    source.update(span=locator,url=None)
    profile=input_profile([item],question=question)
    registry=build_answer_unit_registry(locale='sl',question=question,context='',
        selected_evidence=[item],signed_input_profile=profile,
        profile_signing_key=PROFILE_KEY,source_passages=[source])
    unit=next(row for row in registry.units if row['unit_type']=='exact_source_excerpt')
    payload={'locale':'sl','question':question,'context':'','passages':[source],
        'packets':{'answer':{'evidence':[item],'input_profile':profile}}}
    result=_plan_runtime()._process_fresh_answer_plan({'answer_language':'sl',
        'blocks':[{'block_id':'B1','unit_id':unit['unit_id'],'paragraph_id':'P1'}],
        'no_sufficient_evidence':False},registry,payload=payload,tier='answer')
    assert result['limitations']==[{'code':'partial_case_only_not_full_inflection',
        'scope':'processing','claim_ids':['C1'],'unit_id':unit['unit_id']}]
    assert run_bounded_answer_plan(deepcopy(result))['generation']['status']=='partial'
    if damage=='drop_processing':result['limitations']=[]
    elif damage=='relabel_supported':
        result['limitations']=[];result.update(status='validated',answer_support='supported')
    elif damage=='validation_status':result['validation']['status']='bounded_checks_completed'
    output=run_bounded_answer_plan(result)
    assert {key:output['generation'][key]for key in ('status','error_code','stage')}=={
        'status':'failed','error_code':'unknown_citation','stage':'citation_validation'}


@pytest.mark.parametrize('damage',[
    'inline_marker','missing_claims','empty_claims','empty_citations','duplicate_claim',
    'unknown_citation_claim','unknown_block_claim','paragraph_mismatch',
    'extra_cited_evidence','missing_cited_evidence','missing_unit_ids','missing_unit_type',
    'unknown_unit_type','claim_as_coverage','coverage_claim','coverage_evidence',
    'coverage_support','coverage_citation','citation_wrong_unit','citation_wrong_block',
    'citation_bogus_support','fabricated_supports','extra_claim_field',
    'extra_citation_field','missing_citation_block','missing_citation_unit',
    'missing_citation_support','citation_wrong_source','citation_wrong_locator',
    'citation_wrong_snippet','citation_wrong_range','missing_validation',
    'semantic_certified','missing_answer_support','fabricated_answer_md',
    'fabricated_short_answer','false_partial_label','missing_source_id',
    'forged_source_id','missing_source_revision','forged_source_revision',
    'self_asserted_source_identity',
])
def test_deterministic_bounded_answer_plan_rejects_incomplete_or_inconsistent_structure(damage):
    result=bounded_answer_plan_result()
    coverage={'block_id':'B2','paragraph_id':'P2','unit_id':'U0000000000000002',
        'unit_type':'coverage_limitation','claim_id':None,
        'text':'Exact server-rendered coverage limitation.','support_ids':[]}
    if damage=='inline_marker':result['answer_md']='Exact server-rendered claim. [1]'
    elif damage=='missing_claims':result.pop('claims')
    elif damage=='empty_claims':result['claims']=[]
    elif damage=='empty_citations':result['citations']=[]
    elif damage=='duplicate_claim':result['claims'].append(deepcopy(result['claims'][0]))
    elif damage=='unknown_citation_claim':result['citations'][0]['claim_id']='C2'
    elif damage=='unknown_block_claim':result['display_blocks'][0]['claim_id']='C2'
    elif damage=='paragraph_mismatch':result['claims'][0]['paragraph_id']='P2'
    elif damage=='extra_cited_evidence':
        result['display_blocks'][0]['support_ids'].append('S2')
        result['claims'][0]['support_ids'].append('S2')
        result['citations'].append(bounded_answer_plan_citation(
            evidence_id='other-known',support_id='S2'))
    elif damage=='missing_cited_evidence':
        result['evidence'].append(bounded_answer_plan_evidence('other-known'))
    elif damage=='missing_unit_ids':
        result['display_blocks'][0].pop('unit_id');result['claims'][0].pop('unit_id')
    elif damage=='missing_unit_type':result['display_blocks'][0].pop('unit_type')
    elif damage=='unknown_unit_type':result['display_blocks'][0]['unit_type']='unknown_unit'
    elif damage=='claim_as_coverage':result['display_blocks'][0]['unit_type']='coverage_limitation'
    elif damage=='coverage_claim':
        coverage['claim_id']='C1';result['display_blocks'].append(coverage)
    elif damage=='coverage_evidence':
        coverage['evidence_ids']=['known'];result['display_blocks'].append(coverage)
    elif damage=='coverage_support':
        coverage['support_ids']=['S1'];result['display_blocks'].append(coverage)
    elif damage=='coverage_citation':
        result['display_blocks'].append(coverage)
        result['citations'].append(bounded_answer_plan_citation(
            claim_id=None,block_id='B2',unit_id='U0000000000000002',support_id='S2'))
    elif damage=='citation_wrong_unit':result['citations'][0]['unit_id']='Uffffffffffffffff'
    elif damage=='citation_wrong_block':result['citations'][0]['block_id']='B9'
    elif damage=='citation_bogus_support':result['citations'][0]['support_id']='bogus.S9'
    elif damage=='fabricated_supports':
        result['display_blocks'][0]['support_ids']=result['claims'][0]['support_ids']=['bogus.S9']
    elif damage=='extra_claim_field':result['claims'][0]['invented']='not-core'
    elif damage=='extra_citation_field':result['citations'][0]['invented']='not-core'
    elif damage=='missing_citation_block':result['citations'][0].pop('block_id')
    elif damage=='missing_citation_unit':result['citations'][0].pop('unit_id')
    elif damage=='missing_citation_support':result['citations'][0].pop('support_id')
    elif damage=='citation_wrong_source':result['citations'][0]['source_key']='invented'
    elif damage=='citation_wrong_locator':result['citations'][0]['locator']={'invented':True}
    elif damage=='citation_wrong_snippet':result['citations'][0]['snippet']='Fabricated support.'
    elif damage=='citation_wrong_range':result['citations'][0]['char_end']=20
    elif damage=='missing_validation':result.pop('validation')
    elif damage=='semantic_certified':result['validation']['semantic_entailment_certified']=True
    elif damage=='missing_answer_support':result.pop('answer_support')
    elif damage=='fabricated_answer_md':result['answer_md']='Fabricated answer.'
    elif damage=='fabricated_short_answer':result['short_answer']='Fabricated answer.'
    elif damage=='false_partial_label':result.update(status='partial',answer_support='partial')
    elif damage=='missing_source_id':result['citations'][0].pop('source_id')
    elif damage=='forged_source_id':result['citations'][0]['source_id']='synthetic:forged'
    elif damage=='missing_source_revision':result['citations'][0].pop('source_revision')
    elif damage=='forged_source_revision':result['citations'][0]['source_revision']='f'*64
    elif damage=='self_asserted_source_identity':
        result['evidence'][0].pop('external_id');result['evidence'][0].pop('source_revision')
    output=run_bounded_answer_plan(result)
    assert {key:output['generation'][key]for key in ('status','error_code','stage')}=={
        'status':'failed','error_code':'unknown_citation','stage':'citation_validation'}
    assert output['evidence']==[]and output['generation_status']=='not_generated'


def test_source_timeout_is_partial_and_does_not_suppress_other_sources_or_generation(tmp_path):
    gateway=UnifiedGateway(source_failure='solska');app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        _,family=ask(app,client,login(client));short=family['short']
        assert short['generation']['status']=='generated'and short['retrieval']['status']=='partial'
        statuses={row['family']:row['status']for row in short['retrieval']['coverage']}
        assert statuses['solska']=='timeout'and statuses['zkb']==statuses['jezikovna_posta']=='ok'
        assert statuses['ontology']=='not_documentary'


def test_no_support_skips_generation_without_claiming_corpus_completeness(tmp_path):
    gateway=UnifiedGateway(no_hits=True);app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        fid,family=ask(app,client,login(client));short=family['short']
        assert short['generation']['status']=='no_support'and short['status']=='no_hit'
        assert not any(path=='/internal/pilot/generate'for path,_,_ in gateway.calls)
        page=client.get('/api/pilot/families/'+fid+'/matches').json()
        assert page['returned_count']==0 and page['corpus_total']is None


@pytest.mark.parametrize('support_state',['empty','ineligible'])
@pytest.mark.parametrize('locale,expected',[
    ('sl','Oprostite, za to vprašanje v virih ne najdem pravega odgovora.'),
    ('it','Mi dispiace, per questa domanda non trovo nelle fonti una risposta adeguata.'),
])
def test_no_eligible_support_never_invokes_provider_and_keeps_owned_results(tmp_path,support_state,locale,expected):
    class AdmissionGateway(UnifiedGateway):
        forbid_generation=False
        generation_invocations=0
        def request(self,path,payload,base=None):
            if path=='/internal/pilot/generate':
                self.generation_invocations+=1
                assert not self.forbid_generation,'No eligible evidence may invoke a provider'
            result=super().request(path,payload,base)
            if path=='/internal/pilot/freeze'and self.forbid_generation and support_state=='ineligible':
                for row in result['matches']:row['context_eligible']=False
            return result
    gateway=AdmissionGateway();app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client)
        # A previous successful request must not count against this case's
        # no-generation requirement, nor change its admission decision.
        earlier_id,earlier=ask(app,client,headers,idempotency_key='eligible-control',locale=locale)
        assert earlier['short']['generation_status']=='generated'
        with app.state.store.connect()as db:
            earlier_stored=db.execute('SELECT result FROM runs WHERE family_id=?',(earlier_id,)).fetchone()[0]
        before=gateway.generation_invocations
        assert before==1
        gateway.forbid_generation=True
        gateway.no_hits=support_state=='empty'
        fid,family=ask(app,client,headers,idempotency_key='no-eligible-support',locale=locale)
        short=family['short']
        assert gateway.generation_invocations-before==0
        assert short['state']=='complete'and short['status']=='no_hit'
        assert short['generation']['status']=='no_support'and short['generation_status']=='not_generated'
        assert short['short_answer']==expected
        assert client.get('/api/pilot/families/'+earlier_id).json()==earlier
        with app.state.store.connect()as db:
            assert db.execute('SELECT result FROM runs WHERE family_id=?',(earlier_id,)).fetchone()[0]==earlier_stored
        assert short['evidence']==[]and short['source_badges']==[]
        matches_route='/api/pilot/families/'+fid+'/matches'
        page=client.get(matches_route).json()
        assert page['corpus_total']is None
        assert not any(row['used_in_answer']for row in page['items'])
        drawers={}
        if support_state=='empty':assert page['returned_count']==0
        else:
            assert page['returned_count']==7
            for row in page['items']:
                route='/api/pilot/families/'+fid+'/sources/'+row['evidence_id']
                response=client.get(route)
                assert response.status_code==200
                drawers[route]=response.json()
        gateway.blocked=True
        assert client.get('/api/pilot/families/'+fid).json()==family
        assert client.get(matches_route).json()==page
        for route,original in drawers.items():assert client.get(route).json()==original
        login(client,'unified-other')
        for route in ['/api/pilot/families/'+fid,matches_route,*drawers]:
            assert client.get(route).status_code==404


def test_frozen_matches_are_available_during_generation(tmp_path):
    gateway=UnifiedGateway();app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        def during(payload):
            with app.state.store.connect()as db:
                row=db.execute('SELECT id,owner FROM families').fetchone()
            family=app.state.domain.family(row['owner'],row['id'])
            assert family['matches_available']and family['short']['progress']['stage']=='generation'
            page=client.get('/api/pilot/families/'+row['id']+'/matches').json()
            assert page['returned_count']==7 and not any(item['used_in_answer']for item in page['items'])
            assert client.get('/api/pilot/families/'+row['id']+'/sources/'+page['items'][0]['evidence_id']).status_code==200
        gateway.on_generate=during
        _,family=ask(app,client,login(client))
        assert family['short']['generation']['status']=='generated'


def test_unified_configuration_is_explicit_and_boolean():
    base={'allowed_origins':[ORIGIN],'secure_cookies':False}
    assert config_checked(base)['unified_answers']is False
    for bad in [True,'true',1]:
        with pytest.raises(ValueError):config_checked(dict(base,unified_answers=bad))


def test_explicit_existing_cpu_tool_capture_joins_default_documentary_freeze(tmp_path):
    class ToolsGateway(UnifiedGateway):
        def __init__(self):
            super().__init__();self.linghub_url='http://127.0.0.1:8010';self.cpu_calls=[]
        def request(self,path,payload,base=None):
            if path.startswith('/v1/'):
                self.cpu_calls.append((path,deepcopy(payload),base))
                assert base==self.linghub_url
                if path=='/v1/capabilities':return {'identity':'a'*64,'analyzer_service_reachable':True}
                assert path=='/v1/explore'and payload=={'text':'lep','lang':'sl','engine':'default','include':['synonyms'],'limit':3}
                return {'text':'lep','lang':'sl','offset_convention':'unicode_codepoint_half_open','results':{'synonyms':{'status':'no_hit','data':{}}}}
            return super().request(path,payload,base)
    gateway=ToolsGateway();app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as user:
        _,family=ask(app,user,login(user),question='Sopomenke za »lep«.',input_language='sl')
        assert family['short']['generation']['status']=='generated'
        frozen=next(payload for path,payload,_ in gateway.calls if path=='/internal/pilot/freeze')
        tools=frozen['tool_evidence']
        assert tools['capture_set_sha256']==digest(tools['captures'])
        assert tools['captures'][0]['request']['include']==['synonyms']
        assert len(gateway.cpu_calls)==3 and len(frozen['unified_evidence']['matches'])==6


def test_unsigned_web_tool_intent_does_not_override_authenticated_plan(tmp_path):
    gateway=UnifiedGateway();app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as user:
        _,family=ask(app,user,login(user),question='Pojasni morfologijo »izraz«.')
        short=family['short']
        assert short['generation']['status']=='generated'and short['retrieval']['status']=='complete'
        tool=next(row for row in short['retrieval']['coverage']if row['family']=='linghub_tools')
        assert tool['reason']=='no_applicable_lookup_route'
        assert not any(path.startswith('/v1/')for path,_,_ in gateway.calls)
        assert 'tool_evidence'not in next(payload for path,payload,_ in gateway.calls if path=='/internal/pilot/freeze')


@pytest.mark.parametrize('status', ['Predlog',None])
def test_real_source_view_status_is_preserved_in_frozen_original_drawer(monkeypatch,status):
    from pathlib import Path
    from types import SimpleNamespace
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    from tools.zrc_reference.pilot_evidence import source_views
    passage={'source_slug':'epravopis','title':'Synthetic dictionary entry','url':None,
        'text':'Synthetic original source text.','passage_id':'synthetic-entry-passage',
        'source_id':'synthetic-entry','source_revision':'a'*64,'span':{},
        'edition':'2025','source_status':status}
    view=source_views([passage])[0][0]
    assert view['source_status']==status and 'status'not in view
    response={'bundle':{'synthetic':True},'matches':[{'evidence_id':view['citation_id'],
        'source_slug':'epravopis','source':view}]}
    def request(path,payload):
        if path=='/internal/pilot/interpret':
            request_plan=shared_plan(payload['question'],payload['context'])
            return {'contract':'klikai.request-interpretation.v1',
                'input_sha256':digest({'contract':'klikai.request-interpretation.input.v1',
                                       **payload}),'request_plan':request_plan,
                'request_plan_sha256':digest(request_plan)}
        assert path=='/internal/pilot/freeze'
        assert payload['request_plan_sha256']==digest(payload['request_plan'])
        return deepcopy(response)
    monkeypatch.setattr(unified,'registry',lambda gateway:[])
    monkeypatch.setattr(unified,'collect_tools',lambda gateway,family:(None,None))
    gateway=SimpleNamespace(klikai_url='synthetic-unused',request=request)
    frozen=unified.freeze(gateway,{'question':'synthetic','locale':'sl'}, {})['bundle']
    drawer=unified.source(frozen,view['citation_id'])
    assert drawer['status']==status
    assert drawer['excerpt']==passage['text']and drawer['edition']=='2025'
    assert view==source_views([passage])[0][0]


@pytest.mark.parametrize('language,question,context,facets,target',[
    ('sl','Kako se glasi rodilnik imena Hugo?','Popravek: množina.',
     ['genitive','plural'],'Hugo'),
    ('sl','Kako se glasi rodilnik imena Hugo?','Popravek: dajalnik, ime Miha.',
     ['dative'],'Miha'),
    ('it','Genitivo del nome Hugo?','Correzione: plurale.',
     ['genitive','plural'],'Hugo'),
    ('it','Genitivo del nome Hugo?','Correzione: dativo, nome Miha.',
     ['dative'],'Miha'),
    ('sl','Kako se glasi rodilnik imena Hugo?','Popravek: ime je »Miha«.',
     ['genitive'],'Miha'),
    ('it','Genitivo del nome Hugo?','Correzione: nome “Miha”.',
     ['genitive'],'Miha'),
    ('sl','Kako se glasi rodilnik imena Hugo?',
     'Popravek: namesto Miha uporabi Luka.',['genitive'],'Luka'),
    ('it','Come si flette il nome Hugo?',
     'Prima abbiamo discusso il genitivo plurale.',['inflection'],'Hugo'),
])
def test_web_signed_correction_and_history_plan_matches_core_slot_authority(
        monkeypatch,language,question,context,facets,target):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    from core.lexical_planner import plan
    from tools.zrc_reference.query_relevance import interpret
    signed=plan(question,context,language,{'raw_engines':{}})['request_plan']
    checked=interpret(question+'\n'+context,request_plan=signed)
    assert checked['requested_facets']==facets
    assert [row['text']for row in checked['target_spans']]==[target]


def test_web_signed_multi_target_correction_stays_ambiguous_for_core(monkeypatch):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    from core.lexical_planner import plan
    from tools.zrc_reference.query_relevance import interpret
    question='Kako se glasi rodilnik imena Hugo?'
    context='Popravek: ime je »Miha« ali »Luka«.'
    signed=plan(question,context,'sl',{'raw_engines':{}})['request_plan']
    checked=interpret(question+'\n'+context,request_plan=signed)
    assert [row['text']for row in checked['target_spans']]==['Miha','Luka']


@pytest.mark.parametrize('locale,question,context,source_text,form,role_context',[
    ('sl','Kako se glasi rodilnik imena Hugo?',
     'Popravek: namesto imena Hugo uporabi ime Miha.',
     'Rodilnik imena Miha je Mihe.','Mihe','ime Miha'),
    ('it','Genitivo del nome Hugo?',
     'Correzione: invece del nome sloveno Hugo usa il nome italiano Miha.',
     'Il genitivo del nome Miha è Miha.','Miha','il nome italiano Miha'),
])
def test_web_signed_replacement_plan_builds_real_core_registry_for_new_target(
        monkeypatch,locale,question,context,source_text,form,role_context):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from core.lexical_planner import plan
    from test_pilot_answer_plan import exact_excerpt_item,input_profile,PROFILE_KEY
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry
    item,source=exact_excerpt_item(source_text,title='Miha')
    signed=plan(question,context,locale,{'raw_engines':{}})['request_plan']
    profile=input_profile([item],question=question,context=context,locale=locale,
                          request_plan=signed)
    registry=build_answer_unit_registry(locale=locale,question=question,context=context,
        selected_evidence=[item],signed_input_profile=profile,
        profile_signing_key=PROFILE_KEY,source_passages=[source],request_plan=signed)
    unit,=[row for row in registry.units if row['unit_type']=='exact_source_excerpt']
    assert registry.required_unit_ids==[unit['unit_id']]
    assert unit['payload']['target']=='Miha'and unit['payload']['form']==form
    assert [row['text']for row in signed['target_spans']]==['Miha']
    assert signed['target_spans'][0]['context_span']['text']==role_context
    assert 'Hugo'not in [row['text']for row in signed['target_spans']]


@pytest.mark.parametrize('context,targets,ambiguities',[
    ('Correzione: invece del nome Hugo usa il nome tedesco Miha.',[],[]),
    ('Correzione: invece del nome Hugo usa il nome Miha e Luca.',
     ['Miha e Luca'],['coordinated_target_unresolved']),
    ('Prima abbiamo discusso: invece del nome Hugo usa il nome italiano Miha.',
     ['Hugo'],[]),
])
def test_web_signed_unresolved_or_historical_replacement_cannot_build_core_source_unit(
        monkeypatch,context,targets,ambiguities):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from core.lexical_planner import plan
    from test_pilot_answer_plan import exact_excerpt_item,input_profile,PROFILE_KEY
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry
    question='Genitivo del nome Hugo?'
    item,source=exact_excerpt_item(
        'Il genitivo del nome Miha è Miha. Il genitivo del nome Luca è Luca.',
        title='Miha e Luca')
    signed=plan(question,context,'it',{'raw_engines':{}})['request_plan']
    assert [row['text']for row in signed['target_spans']]==targets
    assert signed['ambiguities']==ambiguities
    registry=build_answer_unit_registry(locale='it',question=question,context=context,
        selected_evidence=[item],signed_input_profile=input_profile(
            [item],question=question,context=context,locale='it',request_plan=signed),
        profile_signing_key=PROFILE_KEY,source_passages=[source],request_plan=signed)
    assert registry.required_unit_ids==[]
    assert not any(row['unit_type']=='exact_source_excerpt'for row in registry.units)


def test_web_signed_italian_ui_matrix_request_keeps_exact_names_and_role_context(monkeypatch):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    from core.lexical_planner import plan
    from tools.zrc_reference.query_relevance import interpret
    question=('Come si declina il nome sloveno Hugo e in che cosa differisce '
              'dal cognome francese Hugo?')
    signed=plan(question,'','it',{'raw_engines':{}})['request_plan']
    checked=interpret(question,request_plan=signed)
    assert checked['requested_facets']==['inflection']
    assert [row['text']for row in checked['target_spans']]==['Hugo','Hugo']
    assert [row['context_span']['text']for row in checked['target_spans']]==[
        'nome sloveno Hugo','cognome francese Hugo']
    assert all(question[row['start']:row['end']]=='Hugo'
               for row in checked['target_spans'])


@pytest.mark.parametrize('locale,question,slots',[
    ('sl','Rodilnik ednine in dajalnik množine imena Hugo?',[
        {'case':'genitive','number':'singular'},
        {'case':'dative','number':'plural'}]),
    ('it','Genitivo singolare e dativo plurale del nome Hugo?',[
        {'case':'genitive','number':'singular'},
        {'case':'dative','number':'plural'}]),
    ('sl','Dajalnik množine in rodilnik ednine imena Hugo?',[
        {'case':'dative','number':'plural'},
        {'case':'genitive','number':'singular'}]),
    ('it','Quali sono il dativo plurale e il genitivo singolare del nome Hugo?',[
        {'case':'dative','number':'plural'},
        {'case':'genitive','number':'singular'}]),
    ('sl','Rodilnik ednine ter dajalnik množine imena Hugo?',[
        {'case':'genitive','number':'singular'},
        {'case':'dative','number':'plural'}]),
    ('it','Genitivo singolare ed dativo plurale del nome Hugo?',[
        {'case':'genitive','number':'singular'},
        {'case':'dative','number':'plural'}]),
])
def test_web_signed_ordered_grammar_slot_contract_matches_core_direct_parse(
        monkeypatch,locale,question,slots):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    from core.lexical_planner import plan
    from tools.zrc_reference.query_relevance import interpret
    signed=plan(question,'',locale,{'raw_engines':{}})['request_plan']
    direct=interpret(question)
    assert signed['requested_grammar_slots']==slots
    assert direct['requested_grammar_slots']==slots
    assert [(row['text'],row['start'],row['end'])for row in signed['target_spans']]==[
        (row['text'],row['start'],row['end'])for row in direct['target_spans']]
    assert signed['ambiguities']==direct['ambiguities']==[]
    checked=interpret(question,request_plan=signed)
    assert checked['requested_grammar_slots']==slots


@pytest.mark.parametrize('locale,question,ambiguity',[
    ('sl','Rodilnik ednine dajalnik množine imena Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('it','Genitivo singolare dativo plurale del nome Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('sl','Rodilnik in dajalnik ednine in množine imena Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('it','Genitivo e dativo singolare e plurale del nome Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('sl','Rodilnik ednine in dajalnik množine imena Hugo in Miha?',
     'grammar_target_binding_unresolved'),
    ('it','Genitivo singolare e dativo plurale del nome Hugo e Miha?',
     'grammar_target_binding_unresolved'),
    ('sl','Rodilnik ali dajalnik imena Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('it','Genitivo o dativo del nome Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('it','Genitivo singolare oppure plurale del nome Hugo?',
     'grammar_slot_pairing_unresolved'),
])
def test_web_and_core_preserve_same_closed_grammar_slot_ambiguity(
        monkeypatch,locale,question,ambiguity):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    from core.lexical_planner import plan
    from tools.zrc_reference.query_relevance import interpret
    signed=plan(question,'',locale,{'raw_engines':{}})['request_plan']
    direct=interpret(question)
    assert signed['requested_grammar_slots']==direct['requested_grammar_slots']==[]
    assert [(row['text'],row['start'],row['end'])for row in signed['target_spans']]==[
        (row['text'],row['start'],row['end'])for row in direct['target_spans']]
    assert signed['target_spans']==[]
    assert ambiguity in signed['ambiguities']and ambiguity in direct['ambiguities']


@pytest.mark.parametrize('locale,question',[
    ('sl','Kako se glasi rodilnik imena?'),
    ('it','Qual è il genitivo del nome?'),
])
def test_web_and_core_reject_grammar_role_without_lexical_target(
        monkeypatch,locale,question):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    from core.lexical_planner import plan
    from tools.zrc_reference.query_relevance import interpret
    signed=plan(question,'',locale,{'raw_engines':{}})['request_plan']
    direct=interpret(question)
    for request in (signed,direct):
        assert request['target_spans']==[]
        assert request['requested_grammar_slots']==[]
        assert request['ambiguities']==['grammar_target_binding_unresolved']


@pytest.mark.parametrize('locale,question,source_text,expected_number,form',[
    ('sl','Rodilnik imena Hugo?','Rodilnik imena Hugo je Huga.',None,'Huga'),
    ('it','Genitivo singolare del nome Hugo?',
     'Genitivo singolare del nome Hugo è Huga.','singular','Huga'),
])
def test_web_signed_single_grammar_slot_builds_real_core_answer_unit(
        monkeypatch,locale,question,source_text,expected_number,form):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from core.lexical_planner import plan
    from test_pilot_answer_plan import exact_excerpt_item,input_profile,PROFILE_KEY
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry
    item,source=exact_excerpt_item(source_text,title='Hugo')
    signed=plan(question,'',locale,{'raw_engines':{}})['request_plan']
    registry=build_answer_unit_registry(locale=locale,question=question,context='',
        selected_evidence=[item],signed_input_profile=input_profile(
            [item],question=question,locale=locale,request_plan=signed),
        profile_signing_key=PROFILE_KEY,source_passages=[source],request_plan=signed)
    unit,=[row for row in registry.units if row['unit_type']=='exact_source_excerpt']
    assert registry.required_unit_ids==[unit['unit_id']]
    assert (unit['payload']['target'],unit['payload']['requested_facet'],
            unit['payload']['requested_number'],unit['payload']['form'])==(
        'Hugo','genitive',expected_number,form)
    assert not any(row.get('source')=='answer_plan_agreement'for row in registry.unresolved)


@pytest.mark.parametrize('locale,question,sources,slots',[
    ('sl','Rodilnik ednine in dajalnik množine imena Hugo?',(
        'Rodilnik ednine imena Hugo je Huga.',
        'Dajalnik množine imena Hugo je Hugom.'),[
        {'case':'genitive','number':'singular'},
        {'case':'dative','number':'plural'}]),
    ('it','Quali sono il dativo plurale e il genitivo singolare del nome Hugo?',(
        'Dativo plurale del nome Hugo è Hugu.',
        'Genitivo singolare del nome Hugo è Huga.'),[
        {'case':'dative','number':'plural'},
        {'case':'genitive','number':'singular'}]),
])
def test_web_signed_multi_slot_reaches_core_without_first_slot_or_cross_product(
        monkeypatch,locale,question,sources,slots):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from core.lexical_planner import plan
    from test_pilot_answer_plan import exact_excerpt_units,input_profile,PROFILE_KEY
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry
    item,source=exact_excerpt_units(*sources)
    signed=plan(question,'',locale,{'raw_engines':{}})['request_plan']
    assert signed['requested_grammar_slots']==slots
    registry=build_answer_unit_registry(locale=locale,question=question,context='',
        selected_evidence=[item],signed_input_profile=input_profile(
            [item],question=question,locale=locale,request_plan=signed),
        profile_signing_key=PROFILE_KEY,source_passages=[source],request_plan=signed)
    units=[row for row in registry.units
           if row['unit_type']=='exact_source_excerpt']
    assert [(row['payload']['requested_facet'],
             row['payload']['requested_number']) for row in units]==[
        (slot['case'],slot['number']) for slot in slots]
    assert registry.required_unit_ids==[row['unit_id'] for row in units]
    assert len(units)==len(slots)
    assert len({tuple(row['covers_requirement_ids']) for row in units})==len(slots)
    assert not registry.unresolved
    assert not any(row.get('reason')=='signed_request_plan_slot_mismatch'
                   for row in registry.unresolved)


def test_web_signed_multi_slot_rejects_unbound_arbitrary_form(monkeypatch):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from core.lexical_planner import plan
    from test_pilot_answer_plan import exact_excerpt_units,input_profile,PROFILE_KEY
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry
    question='Quali sono il dativo plurale e il genitivo singolare del nome Hugo?'
    item,source=exact_excerpt_units(
        'Dativo plurale del nome Hugo è Hughi.',
        'Genitivo singolare del nome Hugo è Huga.')
    signed=plan(question,'','it',{'raw_engines':{}})['request_plan']
    registry=build_answer_unit_registry(locale='it',question=question,context='',
        selected_evidence=[item],signed_input_profile=input_profile(
            [item],question=question,locale='it',request_plan=signed),
        profile_signing_key=PROFILE_KEY,source_passages=[source],request_plan=signed)
    assert not any(row['unit_type']=='exact_source_excerpt'
                   for row in registry.units)
    assert not any(row.get('payload',{}).get('form')=='Hughi'
                   for row in registry.units)
    assert any(row.get('reason')=='unsupported_multi_slot'and row['blocking']
               for row in registry.unresolved)


@pytest.mark.parametrize('locale,question,reason',[
    ('sl','Rodilnik ednine dajalnik množine imena Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('sl','Rodilnik in dajalnik ednine in množine imena Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('it','Genitivo e dativo singolare e plurale del nome Hugo?',
     'grammar_slot_pairing_unresolved'),
    ('it','Qual è il genitivo del nome?',
     'grammar_target_binding_unresolved'),
])
def test_web_signed_grammar_ambiguity_blocks_real_core_registry_with_source_present(
        monkeypatch,locale,question,reason):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from core.lexical_planner import plan
    from test_pilot_answer_plan import exact_excerpt_item,input_profile,PROFILE_KEY
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry
    source_text=('Rodilnik ednine imena Hugo je Huga.'if locale=='sl'else
                 'Genitivo del nome Hugo è Huga.')
    item,source=exact_excerpt_item(source_text,title='Hugo')
    signed=plan(question,'',locale,{'raw_engines':{}})['request_plan']
    registry=build_answer_unit_registry(locale=locale,question=question,context='',
        selected_evidence=[item],signed_input_profile=input_profile(
            [item],question=question,locale=locale,request_plan=signed),
        profile_signing_key=PROFILE_KEY,source_passages=[source],request_plan=signed)
    assert registry.required_unit_ids==[]
    assert not any(row['unit_type']=='exact_source_excerpt'for row in registry.units)
    assert any(row.get('reason')==reason and row['blocking']
               for row in registry.unresolved)


@pytest.mark.parametrize('mutation',[
    lambda plan:plan['requested_grammar_slots'].reverse(),
    lambda plan:plan.pop('requested_grammar_slots'),
    lambda plan:plan['requested_grammar_slots'].append(
        {'case':'genitive','number':'singular'}),
])
def test_tampered_web_grammar_slots_block_real_core_registry(monkeypatch,mutation):
    from pathlib import Path
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from core.lexical_planner import plan
    from test_pilot_answer_plan import exact_excerpt_units,input_profile,PROFILE_KEY
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry
    question='Genitivo singolare e dativo plurale del nome Hugo?'
    item,source=exact_excerpt_units(
        'Genitivo singolare del nome Hugo è Huga.',
        'Dativo plurale del nome Hugo è Hughi.')
    signed=plan(question,'','it',{'raw_engines':{}})['request_plan']
    mutation(signed)
    registry=build_answer_unit_registry(locale='it',question=question,context='',
        selected_evidence=[item],signed_input_profile=input_profile(
            [item],question=question,locale='it',request_plan=signed),
        profile_signing_key=PROFILE_KEY,source_passages=[source],request_plan=signed)
    assert registry.required_unit_ids==[]
    assert any(row.get('reason')=='signed_request_plan_slot_mismatch'
               and row['blocking']for row in registry.unresolved)


@pytest.mark.parametrize('connected,question,expected_status',[
    (False,'Kako se glasi rodilnik imena Hugo?','validated'),
    (True,'Kako se glasi rodilnik imena Hugo?','validated'),
    (False,'Kako sklanjamo ime Hugo?','partial'),
])
def test_application_capture_consumes_real_gateway_contract_and_answer_plan(
        tmp_path,monkeypatch,connected,question,expected_status):
    """Real producer→signed gateway→validated answer plan→owned pilot consumer.

    Only documentary HTTP responses and the model transport are synthetic. The
    current sibling gateway validates/project/freezes the actual app packet.
    """
    from pathlib import Path
    from unittest.mock import Mock
    import subprocess,sys
    root=core_test_root()
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_fulltext_grounded import store as zrc_store
    from test_pilot import KEY,counts,client as model_client
    from tools.zrc_reference.grounded import ReferenceEngine
    from tools.zrc_reference.pilot_runtime import PilotRuntime
    from tools.zrc_reference.pilot_contracts import FreezeRequest,GenerateRequest,MODEL_PINS
    from tools.zrc_reference.request_interpretation import response as interpret_response
    monkeypatch.setattr(sys.modules[__name__],'QUERY',question)
    directory=tmp_path/'zrc';directory.mkdir()
    store=zrc_store.__wrapped__(directory)
    engine=ReferenceEngine(store,Mock(),{})
    policy={'policy_id':'synthetic-unified-operator','sources':['zkb','solska','jezikovna_posta','zadrege','gradivo'],
            'editorial_operator_policy_id':editorial.OPERATOR_POLICY_ID}
    clients={model:model_client(model)for model in MODEL_PINS}
    runtime=PilotRuntime(engine,{'unified_policy':policy},KEY,clients=clients,token_counter=counts)

    class ActualContractGateway(UnifiedGateway):
        def request(self,path,payload,base=None):
            assert not self.blocked
            if path=='/internal/pilot/interpret':
                self.calls.append((path,deepcopy(payload),base))
                return interpret_response(payload['question'],payload['context'],
                    payload['input_language'],payload['locale'])
            if path=='/internal/pilot/freeze':
                self.calls.append((path,deepcopy(payload),base))
                assert payload['request_plan_sha256']==digest(payload['request_plan'])
                frozen=runtime.freeze(FreezeRequest(**payload),fixed_evidence={
                    'passages':store.lexical('Hugo'),'retrieval':'disposable-fixture'})
                self.frozen=frozen
                return frozen
            if path=='/internal/pilot/generate':
                self.calls.append((path,deepcopy(payload),base))
                producer_payload=payload['bundle']['payload']
                registry=runtime._answer_registry(producer_payload,payload['tier'])
                assert registry.required_unit_ids
                raw={'answer_language':producer_payload['locale'],'blocks':[
                    {'block_id':f'B{index}','unit_id':unit_id,
                     'paragraph_id':'P1'if connected else f'P{index}'}
                    for index,unit_id in enumerate(registry.required_unit_ids,1)],
                    'no_sufficient_evidence':not bool(registry.required_unit_ids)}
                clients['qwen'].post.return_value['choices'][0]['message']['content']=json.dumps(raw)
                result=runtime.generate(GenerateRequest(**payload))
                self.producer_result=deepcopy(result)
                return result
            return super().request(path,payload,base)

    gateway=ActualContractGateway()
    gateway.record.update(title='Hugo',question_original=question,answer_text='Rodilnik imena Hugo je Huga.')
    app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as user:
        fid,family=ask(app,user,login(user));short=family['short']
        assert (short.get('status'),short.get('answer_kind'),short.get('generation_status'))==(
            expected_status,'generated','deterministic_bounded_answer_plan'),json.dumps(short,indent=2)
        assert short['evidence']
        assert clients['qwen'].post.call_count==1 and clients['gemma'].post.call_count==0
        assert short['blocks'][0]['evidence_ids']==[row['evidence_id']for row in short['citations'] if row['claim_id']=='C1']
        if connected:
            assert all(b['paragraph_id']=='P1'for b in short['blocks'])
        else:
            assert [b['paragraph_id']for b in short['blocks']]==[
                f'P{index}'for index in range(1,len(short['blocks'])+1)]
        assert '['not in short['answer_md']
        # Exercise the real source_views producer shape, rather than replacing it
        # with the canonical evidence_id shape used by synthetic browser fixtures.
        original=gateway.producer_result
        claim_units={'documentary_fact','construction_application','terminology_equivalence',
                     'bounded_attestation','exact_source_excerpt'}
        claims={row['claim_id']:row for row in original['claims']}
        assert all(set(row)=={'claim_id','text','support_ids','fact_ids','section',
                              'paragraph_id','depends_on','unit_id','support_mode'}
                   for row in claims.values())
        blocks_by_claim={row['claim_id']:row for row in original['display_blocks']
                         if row['claim_id'] is not None}
        for block in original['display_blocks']:
            assert set(block)=={'block_id','paragraph_id','unit_id','unit_type','text',
                                'claim_id','support_ids'}
            if block['claim_id'] is None:
                assert block['unit_type']=='coverage_limitation'and block['support_ids']==[]
            else:
                claim=claims[block['claim_id']]
                assert block['unit_type']in claim_units
                assert (claim['unit_id'],claim['paragraph_id'],claim['text'],claim['support_ids'])==(
                    block['unit_id'],block['paragraph_id'],block['text'],block['support_ids'])
        citation_required={'block_id','claim_id','unit_id','evidence_id','support_id',
                           'source_key','snippet','locator','char_start','char_end'}
        citation_optional={'source_id','source_revision'}
        evidence_by_id={row['citation_id']:row for row in original['evidence']}
        for citation in original['citations']:
            assert citation_required<=set(citation)<=citation_required|citation_optional
            block=blocks_by_claim[citation['claim_id']]
            assert (citation['block_id'],citation['unit_id'])==(
                block['block_id'],block['unit_id'])
            assert citation['support_id']in block['support_ids']
            evidence_row=evidence_by_id[citation['evidence_id']]
            expected_source_id=(evidence_row['external_id']if':'in evidence_row['external_id']
                else evidence_row['source_slug']+':'+evidence_row['external_id'])
            assert citation['source_id']==expected_source_id
            assert citation['source_revision']==evidence_row['source_revision']
            assert citation['locator']and citation['locator']==evidence_row['locator']
        if expected_status=='partial':
            assert any(row.get('code')=='partial_case_only_not_full_inflection'
                       for row in original['limitations'])
        assert original['evidence']and all('citation_id'in row and'evidence_id'not in row for row in original['evidence'])
        assert short['evidence']==[dict(row,evidence_id=row['citation_id'],local_number=i+1)
                                  for i,row in enumerate(original['evidence'])]
        for field in ('answer_md','short_answer','citations'):
            assert short[field]==original[field]
        expected_blocks=[]
        for block in original['display_blocks']:
            ids=list(dict.fromkeys(row['evidence_id']for row in original['citations']
                                   if row['claim_id']==block['claim_id']))
            expected_blocks.append({'block_id':block['block_id'],'paragraph_id':block['paragraph_id'],
                                    'text':block['text'],'evidence_ids':ids})
        assert short['blocks']==expected_blocks
        helper=Path(__file__).resolve().parents[1]/'pilot_ui/core.mjs'
        rendered=subprocess.run(['node','--input-type=module','-e',
            "import fs from 'node:fs';"
            "const {blockTextPresentation}=await import(process.argv[1]);"
            "const tier=JSON.parse(fs.readFileSync(0,'utf8'));"
            "process.stdout.write(JSON.stringify(tier.blocks.map(block=>{"
            "const view=blockTextPresentation(tier,block);return {"
            "text:view.parts.map(part=>part.text).join(''),"
            "citations:(view.separateCitations||[]).map(part=>({"
            "evidence_id:part.evidence.evidence_id,citation_id:part.evidence.citation_id,number:part.number}))};})));",
            helper.as_uri()],input=json.dumps(short),text=True,capture_output=True,check=True,timeout=10)
        rendered=json.loads(rendered.stdout)
        assert [row['text']for row in rendered]==[row['text']for row in original['display_blocks']]
        for block,row in zip(short['blocks'],rendered):
            assert [citation['evidence_id']for citation in row['citations']]==block['evidence_ids']
            assert all(citation['evidence_id']==citation['citation_id']and type(citation['number'])is int
                       for citation in row['citations'])
        rows=user.get('/api/pilot/families/'+fid+'/matches').json()['items']
        used={row['evidence_id']for row in short['citations']}
        assert len(rows)>len(short['evidence'])and sum(row['used_in_answer']for row in rows)==len(used)>0
        assert {row['capture_sha256']for row in gateway.frozen['matches']if row['capture_sha256']}=={
            row['capture_sha256']for row in next(p for path,p,_ in gateway.calls if path=='/internal/pilot/freeze')['unified_evidence']['matches']}
        gateway.blocked=True
        for row in rows:
            response=user.get('/api/pilot/families/'+fid+'/sources/'+row['evidence_id'])
            assert response.status_code==200 and response.json()['excerpt']
            if row['source_slug']=='zadrege':
                drawer=response.json()
                assert 'A necessary qualification absent from the snippet.'in drawer['excerpt']
                assert 'draft'in drawer['excerpt']and drawer['bibliography']['source_status']=='draft'
                assert drawer['status']=='draft'
        login(user,'unified-other')
        assert user.get('/api/pilot/families/'+fid+'/matches').status_code==404
