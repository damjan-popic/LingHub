"""Unified normal-query application flow with synthetic external providers.

These tests execute the real router, owned store, adapter orchestration, worker
and source routes. The provider response is a fixture, not live model evidence.
"""
from copy import deepcopy
import json
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
    def __init__(self,*,source_failure=None,provider_failure=False,unknown_citation=False,freeze_failure=False,no_hits=False):
        super().__init__('http://127.0.0.1:18320','synthetic-token-'*4,
            klikai_url='http://127.0.0.1:8000',editorial_full_records=True,
            editorial_operator_policy_id=editorial.OPERATOR_POLICY_ID,unified_answers=True)
        self.calls=[];self.lock=threading.Lock();self.blocked=False
        self.source_failure=source_failure;self.provider_failure=provider_failure
        self.unknown_citation=unknown_citation;self.freeze_failure=freeze_failure;self.no_hits=no_hits
        self.record=full_record();self.on_generate=None;self.barrier=None

    def require_generation(self,models):
        raise AssertionError('Normal unified query must retrieve before provider admission')

    def request(self,path,payload,base=None):
        assert not self.blocked,'Frozen sources must not contact any upstream'
        with self.lock:self.calls.append((path,deepcopy(payload),base))
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


def test_missing_explicit_tool_context_preserves_other_retrieval(tmp_path):
    gateway=UnifiedGateway();app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as user:
        _,family=ask(app,user,login(user),question='Pojasni morfologijo »izraz«.')
        short=family['short']
        assert short['generation']['status']=='generated'and short['retrieval']['status']=='partial'
        tool=next(row for row in short['retrieval']['coverage']if row['family']=='linghub_tools')
        assert tool['reason']=='explicit_tool_context_required'
        assert not any(path.startswith('/v1/')for path,_,_ in gateway.calls)
        assert 'tool_evidence'not in next(payload for path,payload,_ in gateway.calls if path=='/internal/pilot/freeze')


def test_application_capture_consumes_real_gateway_contract_and_composition(tmp_path,monkeypatch):
    """Real producer→signed gateway→validated composition→owned pilot consumer.

    Only documentary HTTP responses and the model transport are synthetic. The
    current sibling gateway validates/project/freezes the actual app packet.
    """
    from pathlib import Path
    from unittest.mock import Mock
    import sys
    root=Path(__file__).resolve().parents[2]/'klikai-human-testing-20260911'
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_fulltext_grounded import store as zrc_store
    from test_pilot import KEY,counts,client as model_client,composition
    from tools.zrc_reference.grounded import ReferenceEngine
    from tools.zrc_reference.pilot_runtime import PilotRuntime
    from tools.zrc_reference.pilot_contracts import FreezeRequest,GenerateRequest,MODEL_PINS
    monkeypatch.setattr(sys.modules[__name__],'QUERY','Hugo')
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
            if path=='/internal/pilot/freeze':
                self.calls.append((path,deepcopy(payload),base))
                frozen=runtime.freeze(FreezeRequest(**payload),fixed_evidence={'passages':store.lexical('Hugo'),'retrieval':'disposable-fixture'})
                self.frozen=frozen
                return frozen
            if path=='/internal/pilot/generate':
                self.calls.append((path,deepcopy(payload),base))
                packet=payload['bundle']['payload']['packets']['short']['evidence']
                ids=[]
                for slug in ('jezikovna_posta','jezikovna_svetovalnica'):
                    entry=next(row for row in packet if row['source_slug']==slug)
                    ids.append(next(s['support_id']for s in entry['supports']if 'Huga' in s['text']))
                raw=composition()
                raw['claims'][0]['support_ids']=ids
                clients['qwen'].post.return_value['choices'][0]['message']['content']=json.dumps(raw)
                return runtime.generate(GenerateRequest(**payload))
            return super().request(path,payload,base)

    gateway=ActualContractGateway()
    gateway.record.update(title='Hugo',question_original='Kako sklanjamo ime Hugo?',answer_text='Rodilnik imena Hugo je Huga.')
    app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as user:
        fid,family=ask(app,user,login(user));short=family['short']
        assert short['generation']['status']=='generated',short
        assert {row['source_slug']for row in short['evidence']}=={'jezikovna_posta','jezikovna_svetovalnica'}
        assert clients['qwen'].post.call_count==1 and clients['gemma'].post.call_count==0
        assert short['blocks'][0]['evidence_ids']==[row['evidence_id']for row in short['citations']]
        assert '['not in short['answer_md']
        rows=user.get('/api/pilot/families/'+fid+'/matches').json()['items']
        assert len(rows)>len(short['evidence'])and sum(row['used_in_answer']for row in rows)==2
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
        login(user,'unified-other')
        assert user.get('/api/pilot/families/'+fid+'/matches').status_code==404
