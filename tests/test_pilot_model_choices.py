"""Automatic/explicit selection through the real owned pilot, synthetic writers."""
import json

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import httpx
import pytest

from pilot_backend.adapter import Gateway
from pilot_backend.api import create_app,config_checked
from pilot_backend.security import Failure
from test_pilot_unified import ORIGIN,QUERY,UnifiedGateway,login


class ChoiceGateway(UnifiedGateway):
    def __init__(self,shared=True,gemma_available=True,wrong_identity=None):
        super().__init__();self.shared=shared;self.gemma_available=gemma_available;self.wrong_identity=wrong_identity
    def require_generation(self,models):pass
    def request(self,path,payload,base=None):
        result=super().request(path,payload,base)
        if path!='/internal/pilot/generate':return result
        selected=payload['model']
        if selected=='gemma'and not self.gemma_available:raise Failure('generation_waiting_capacity',503)
        shared=selected=='auto'and self.shared
        result['model_identity']={'repository':'synthetic-shared-Qwen'if shared else'synthetic-own-Gemma'if selected=='gemma'else'synthetic-own-Qwen','revision':'synthetic-pinned-model'}
        result['execution']={'device':'gpu'if shared or selected=='gemma'else'cpu','provider_scope':'shared'if shared else'own','selection':selected,'route_reason':'shared_ready'if shared else'shared_unavailable_own'if selected=='auto'else'own_cpu_fallback','private_url':'PRIVATE_ENDPOINT','token':'PRIVATE_TOKEN'}
        result['provider_attempts']=[{'route':'shared'if shared else'own_gpu'if selected=='gemma'else'own_cpu','model':result['model_identity']['repository'],'stage':'generation','status':'completed','raw_response':'PRIVATE_RESPONSE'}]
        if self.wrong_identity=='shared_explicit':result['execution']['provider_scope']='shared'
        if self.wrong_identity=='different_selection':result['execution']['selection']='qwen'if selected=='gemma'else'gemma'
        if self.wrong_identity=='missing':result['execution']={}
        return result


def app_choices(tmp_path,gateway):
    app=create_app({'store':str(tmp_path/'private'/'pilot.sqlite'),'allowed_origins':[ORIGIN],
        'secure_cookies':False,'unified_answers':True,'klikai_url':gateway.klikai_url,
        'models':['auto','qwen','gemma']},adapter=gateway,
        hasher=PasswordHasher(time_cost=1,memory_cost=8,parallelism=1),start_workers=False)
    for name in ('unified-owner','unified-other'):
        app.state.auth.create(name,'synthetic-unified-test-password',origin='synthetic',must_change=False)
    return app


def submit(app,client,headers,model=None,question=QUERY):
    response=client.post('/api/pilot/families',headers=headers,json={'question':question,
        'idempotency_key':'synthetic-choice-test',**({'model':model}if model is not None else{})})
    assert response.status_code==200,response.text
    job=app.state.jobs.claim('model');assert job;app.state.jobs.execute(job)
    fid=response.json()['family']['answer_family_id']
    return client.get('/api/pilot/families/'+fid).json(),job


@pytest.mark.parametrize('selection,shared,scope,actual',[
    (None,True,'shared','synthetic-shared-Qwen'),('auto',False,'own','synthetic-own-Qwen'),
    ('qwen',True,'own','synthetic-own-Qwen'),('gemma',True,'own','synthetic-own-Gemma')])
def test_choice_is_forwarded_once_and_actual_identity_survives_frozen_reopening(tmp_path,selection,shared,scope,actual):
    gateway=ChoiceGateway(shared=shared);app=app_choices(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client)
        assert client.get('/api/pilot/session').json()['capabilities']['default_model']=='auto'
        family,job=submit(app,client,headers,selection);selected=selection or'auto'
        assert family['model']==family['short']['model']==selected
        generated=[payload for path,payload,_ in gateway.calls if path=='/internal/pilot/generate']
        assert len(generated)==1 and generated[0]['model']==selected
        assert family['short']['generation']['provider']['repository']==actual
        assert family['short']['generation']['provider']['provider_scope']==scope
        assert family['short']['generation']['provider']['selection']==selected
        assert 'PRIVATE_ENDPOINT'not in json.dumps(family)and'PRIVATE_TOKEN'not in json.dumps(family)
        fid=family['answer_family_id'];eid=family['short']['evidence'][0]['evidence_id']
        route='/api/pilot/families/'+fid+'/sources/'+eid;original=client.get(route).json()
        with app.state.store.connect()as db:
            stored=db.execute('SELECT model,result FROM runs WHERE id=?',(job['run_id'],)).fetchone()
            assert stored['model']==selected and json.loads(stored['result'])['execution']['provider_scope']==scope
            assert len(json.loads(stored['result'])['provider_attempts'])==1
            assert 'PRIVATE_RESPONSE'not in stored['result']
        assert 'provider_attempts'not in family['short']
        gateway.shared=not shared;gateway.blocked=True
        assert client.get('/api/pilot/families/'+fid).json()==family
        assert client.get(route).json()==original
        login(client,'unified-other')
        assert client.get('/api/pilot/families/'+fid).status_code==client.get(route).status_code==404


def test_unavailable_explicit_gemma_never_becomes_qwen(tmp_path):
    gateway=ChoiceGateway(gemma_available=False);app=app_choices(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        family,_=submit(app,client,login(client),'gemma')
        assert family['model']=='gemma'and family['short']['generation_status']=='not_generated'
        assert family['matches_available']is True
        assert family['short']['generation']['error_code']=='generation_waiting_capacity'
        assert [payload['model']for path,payload,_ in gateway.calls if path=='/internal/pilot/generate']==['gemma']


@pytest.mark.parametrize('selection,violation',[('auto','missing'),('qwen','shared_explicit'),('gemma','different_selection')])
def test_missing_or_substituted_provider_identity_cannot_be_published_as_selected_model(tmp_path,selection,violation):
    gateway=ChoiceGateway(wrong_identity=violation);app=app_choices(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        family,job=submit(app,client,login(client),selection)
        assert family['short']['generation_status']=='not_generated'
        assert family['short']['generation']['error_code']in {'provider_identity_missing','provider_selection_mismatch'}
        assert family['short']['generation']['stage']=='provider_identity'
        assert family['short']['evidence']==[]and family['matches_available']
        assert family['short']['generation']['provider']=={}
        with app.state.store.connect()as db:
            result=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(job['run_id'],)).fetchone()[0])
        assert len(result['provider_attempts'])==1
        assert result['provider_attempts'][0]['status']=='completed'
        assert result['generation_status']=='not_generated'
        assert 'PRIVATE_RESPONSE'not in json.dumps(result)


def test_automatic_option_does_not_enter_existing_two_model_comparisons(tmp_path):
    gateway=ChoiceGateway();app=app_choices(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client);family,_=submit(app,client,headers)
        response=client.post('/api/pilot/comparisons',headers=headers,json={
            'family_id':family['answer_family_id'],'blind':False,'idempotency_key':'synthetic-choice-comparison'})
        assert response.status_code==200,response.text
        with app.state.store.connect()as db:
            choices={row[0]for row in db.execute('SELECT model FROM runs WHERE assignment_id=?',(response.json()['comparison_id'],))}
        assert choices=={'qwen','gemma'}


def test_gateway_exposes_shared_readiness_and_only_fixed_routing_labels(monkeypatch):
    def response(request):
        return httpx.Response(200,json={'generation':{'models':{
            'auto':{'available':True,'status':'SHARED_READY','device':'gpu','gpu_available':True,'provider_scope':'shared','selection':'auto','route_reason':'shared_ready','url':'PRIVATE_ENDPOINT'},
            'qwen':{'available':True,'status':'CPU_READY','device':'cpu','provider_scope':'own','selection':'qwen','route_reason':'own_cpu_fallback','cpu':{'loaded':False,'availability':'lazy','phase':'generating','resident_model':'PRIVATE_RESIDENT'}},
            'gemma':{'available':False,'status':'GENERATION_WAITING_CAPACITY','provider_scope':'own','selection':'gemma','route_reason':'UNTRUSTED_PRIVATE_DETAIL'}}}})
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    observed=Gateway('http://127.0.0.1:18320','synthetic-token-'*4).capabilities()
    models=observed['generation']['models']
    assert models['auto']['status']=='SHARED_READY'and models['auto']['provider_scope']=='shared'
    assert models['qwen']['device']=='cpu'and models['gemma']['available']is False
    assert models['qwen']['cpu']=={'loaded':False,'availability':'lazy','phase':'generating'}
    assert 'route_reason'not in models['gemma']
    assert 'PRIVATE_ENDPOINT'not in json.dumps(observed)and'UNTRUSTED_PRIVATE_DETAIL'not in json.dumps(observed)


def test_auto_requires_unified_configuration_and_preserves_legacy_configurations():
    config={'allowed_origins':[ORIGIN],'secure_cookies':False,'models':['auto','qwen','gemma']}
    with pytest.raises(ValueError,match='requires unified'):config_checked(config)
    assert config_checked({**config,'unified_answers':True,'klikai_url':'http://127.0.0.1:8000'})['models']==['auto','qwen','gemma']
    assert config_checked({'allowed_origins':[ORIGIN],'secure_cookies':False})['models']==['qwen','gemma']


@pytest.mark.parametrize('code,status',[('ambiguous_remote_generation','unknown'),('shared_response_identity_mismatch','rejected')])
def test_remote_failure_is_retained_without_a_second_generation(tmp_path,code,status):
    gateway=ChoiceGateway();app=app_choices(tmp_path,gateway);request=gateway.request
    def unknown(path,payload,base=None):
        result=request(path,payload,base)
        if path=='/internal/pilot/generate':
            result.update(answer_kind='source_only',provider_failure={'code':code,'selection':'auto'},
                provider_attempts=[{'route':'shared','model':'synthetic-shared-Qwen','stage':'generation','status':status,'http_code':504,'private_body':'PRIVATE_BODY'}])
        return result
    gateway.request=unknown
    with TestClient(app,base_url=ORIGIN)as client:
        family,job=submit(app,client,login(client))
        assert family['short']['generation_status']=='not_generated'
        assert family['short']['generation']['error_code']==code
        assert family['matches_available']
        assert len([path for path,_,_ in gateway.calls if path=='/internal/pilot/generate'])==1
        with app.state.store.connect()as db:
            result=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(job['run_id'],)).fetchone()[0])
        assert result['provider_attempts']==[{'route':'shared','model':'synthetic-shared-Qwen','stage':'generation','status':status,'http_code':504}]
        assert result['provider_failure']=={'code':code,'selection':'auto'}
        assert 'PRIVATE_BODY'not in json.dumps(result)


def test_attempt_diagnostics_are_bounded_and_ignore_unknown_fields():
    from pilot_backend.human_feedback import provider_diagnostics
    assert provider_diagnostics({'provider_attempts':[{}, {}, {}]})=={}
    assert provider_diagnostics({'provider_attempts':[{'route':{},'model':'https://secret@127.0.0.1/path','status':'unknown','http_code':999,'body':'private'}]})=={'provider_attempts':[{'status':'unknown'}]}
    assert provider_diagnostics({'provider_failure':{'code':{},'selection':[]}})=={}


def test_explicit_own_terminal_rejection_keeps_safe_failure_and_one_attempt(tmp_path):
    gateway=ChoiceGateway();app=app_choices(tmp_path,gateway);request=gateway.request
    attempt={'route':'own_cpu','model':'synthetic-own-Qwen','stage':'generation','status':'rejected','http_code':422}
    def rejected(path,payload,base=None):
        result=request(path,payload,base)
        if path=='/internal/pilot/generate':
            result.update(answer_kind='source_only',provider_failure={'code':'provider_terminal_rejection','selection':'qwen'},
                provider_attempts=[{**attempt,'private_body':'PRIVATE_BODY'}])
        return result
    gateway.request=rejected
    with TestClient(app,base_url=ORIGIN)as client:
        family,job=submit(app,client,login(client),'qwen')
        assert family['model']=='qwen'and family['short']['generation_status']=='not_generated'
        assert family['short']['generation']['error_code']=='provider_terminal_rejection'
        assert family['matches_available']
        assert [payload['model']for path,payload,_ in gateway.calls if path=='/internal/pilot/generate']==['qwen']
        with app.state.store.connect()as db:
            stored=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(job['run_id'],)).fetchone()[0])
        assert stored['provider_attempts']==[attempt]
        assert stored['provider_failure']=={'code':'provider_terminal_rejection','selection':'qwen'}
        assert 'PRIVATE_BODY'not in json.dumps(stored)and'provider_attempts'not in family['short']


@pytest.mark.parametrize('body,expected',[
    ({'error':{'code':'ambiguous_remote_generation','message':'PRIVATE_TOKEN'}},'ambiguous_remote_generation'),
    ({'detail':{'code':'selected_provider_unavailable','traceback':'PRIVATE_ENDPOINT'}},'selected_provider_unavailable'),
    ({'detail':{'code':'PILOT_BUSY','message':'PRIVATE_TOKEN'}},'PILOT_BUSY'),
    ({'error':{'code':'PILOT_TIMEOUT'}},'PILOT_TIMEOUT'),
    ({'error':{'code':'PILOT_FROZEN_RESOURCE_UNAVAILABLE'}},'PILOT_FROZEN_RESOURCE_UNAVAILABLE'),
    ({'error':{'code':'PILOT_INVALID_FROZEN_REQUEST'}},'PILOT_INVALID_FROZEN_REQUEST'),
    ({'error':{'code':'malformed_provider_response'}},'malformed_provider_response'),
    ({'error':{'code':'invalid_generated_content'}},'invalid_generated_content'),
    ({'error':{'code':'invalid_application_composition'}},'invalid_application_composition'),
    ({'error':{'code':'provider_identity_verification_failed'}},'provider_identity_verification_failed'),
    ({'error':{'code':'PRIVATE_TOKEN'}},'answer_unavailable'),
    ({'error':{'code':['PRIVATE_TOKEN']}},'answer_unavailable'),
    ({'padding':'x'*32769},'answer_unavailable')])
def test_generation_http_failure_preserves_status_and_only_known_code(monkeypatch,body,expected):
    from pilot_backend.unified import diagnostic
    calls=[]
    def handler(request):calls.append(request.url.path);return httpx.Response(503,json=body)
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(Failure)as error:
        Gateway('http://127.0.0.1:18320','synthetic-token-'*4).request('/internal/pilot/generate',{'model':'auto'})
    observed=diagnostic(error.value,'provider_request')
    assert {k:v for k,v in observed.items() if k!='technical_cause'}=={'error_code':expected,'stage':'provider_request','http_status':503}
    assert observed['technical_cause']['error_type']=='Failure'
    assert calls==['/internal/pilot/generate']


def test_freeze_plan_mismatch_http_failure_remains_request_local(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(409,json={'detail':'PILOT_REQUEST_PLAN_MISMATCH'})
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:original(
        transport=httpx.MockTransport(handler),**kwargs))
    with pytest.raises(Failure,match='request_plan_mismatch')as error:
        Gateway('http://127.0.0.1:18320','synthetic-token-'*4).request(
            '/internal/pilot/freeze',{'request_plan':{}})
    assert error.value.status==409
    assert calls==['/internal/pilot/freeze']


@pytest.mark.parametrize('code,stage,reason,error_type',[
    ('invalid_generated_content','response_json','invalid_final_json','JSONDecodeError'),
    ('malformed_provider_response','response_shape','invalid_content_type','ValueError'),
    ('invalid_application_composition','correction','correction_without_parent','ValueError'),
    ('malformed_provider_response','response_shape','invalid_response_json','JSONDecodeError'),
    ('malformed_provider_response','response_shape','invalid_execution_shape','ValueError'),
    ('provider_identity_verification_failed','post_identity','post_generation_identity_mismatch','PilotUnavailable'),
])
def test_completed_response_diagnostic_survives_owned_fallback_without_private_fields(tmp_path,code,stage,reason,error_type):
    detail={'code':code,'selection':'auto','stage':stage,
        'reason':reason,'error_type':error_type,
        'response':{'choices_count':1,'finish_reason':'stop','content_type':'string','reasoning_present':False,
                    'completion_tokens':106,'json_error_position':0},
        'message':'PRIVATE_EXCEPTION','raw_response':'PRIVATE_RESPONSE'}
    class RejectedResponse(ChoiceGateway):
        def request(self,path,payload,base=None):
            result=super().request(path,payload,base)
            if path=='/internal/pilot/generate':
                result.update(status='failed',answer_kind='source_only',provider_failure=detail)
                result['provider_failure']['response']['content']='PRIVATE_CONTENT'
            return result
    gateway=RejectedResponse();app=app_choices(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        family,job=submit(app,client,login(client),'auto')
        assert family['short']['generation']['status']=='failed'
        assert family['short']['generation']['error_code']==code
        with app.state.store.connect()as db:
            stored=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(job['run_id'],)).fetchone()[0])
        diagnostic=stored['provider_failure']
        assert diagnostic['reason']==reason and diagnostic['stage']==stage
        assert diagnostic['error_type']==error_type
        assert diagnostic['response']['completion_tokens']==106
        assert stored['provider_attempts'][0]['status']=='completed'
        assert 'PRIVATE_'not in json.dumps(diagnostic)
        assert 'PRIVATE_'not in json.dumps(family)
        assert 'provider_failure'not in family['short']
        assert sum(path=='/internal/pilot/generate'for path,_,_ in gateway.calls)==1


@pytest.mark.parametrize('status,expected',[(422,422),(503,503),(True,None),(99,None),(600,None),('503',None)])
def test_provider_http_status_is_retained_as_bounded_integer(status,expected):
    from pilot_backend.human_feedback import provider_diagnostics
    failure={'code':'provider_terminal_rejection','selection':'gemma','stage':'generation',
        'reason':'generation_request_failed','error_type':'HTTPError','http_status':status,
        'url':'PRIVATE_ENDPOINT','headers':{'Authorization':'PRIVATE_TOKEN'},'body':'PRIVATE_BODY'}
    projected=provider_diagnostics({'provider_failure':failure})['provider_failure']
    assert projected.get('http_status')==expected
    assert ('http_status'in projected)==(expected is not None)
    assert 'PRIVATE_'not in json.dumps(projected)


def test_provider_response_diagnostic_allowlist_rejects_untrusted_values():
    from pilot_backend.human_feedback import provider_diagnostics
    result=provider_diagnostics({'provider_failure':{'code':'selected_provider_unavailable','selection':'auto',
        'stage':'PRIVATE_STAGE','reason':['PRIVATE_REASON'],'error_type':'PRIVATE_TYPE',
        'response':{'choices_count':True,'completion_tokens':-1,'total_tokens':2**63,
          'reasoning_tokens':'123','json_error_position':False,'finish_reason':'PRIVATE_CONTENT',
          'content_type':{},'reasoning_present':1,'content':'PRIVATE_RESPONSE'}}})
    assert result=={'provider_failure':{'code':'selected_provider_unavailable','selection':'auto'}}
