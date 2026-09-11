"""Authenticated composition admission and immutable history, with no GPU/model calls."""
import json,time
import httpx,pytest
from fastapi.testclient import TestClient
from pilot_backend.api import create_app
from pilot_backend.adapter import Gateway
from pilot_backend.security import Failure
from pilot_backend import cycles,replay
from pilot_backend.store import canonical
from test_pilot_backend import Adapter,PASSWORD,login,drain

class CapacityAdapter(Adapter):
    ready=False
    def capabilities(self):
        return {'generation':{'status':'AVAILABLE'if self.ready else'GENERATION_WAITING_CAPACITY','models':{name:{'available':self.ready,'status':'AVAILABLE'if self.ready else'GENERATION_WAITING_CAPACITY'}for name in ('qwen','gemma')}},'dense':{'available':False,'status':'DENSE_ENCODING_WAITING_CAPACITY'},'lexical':{'available':True},'tools':{'available':True}}
    def require_generation(self,models):
        if not self.ready:raise Failure('generation_waiting_capacity',503)

@pytest.fixture
def cpu(tmp_path):
    adapter=CapacityAdapter();app=create_app({'store':str(tmp_path/'private'/'pilot.sqlite'),'allowed_origins':['http://127.0.0.1:18310'],'secure_cookies':False,'tasks':['reference','source_search','terminology','text_check']},adapter=adapter,start_workers=False)
    for name in ('tester','reviewer'):app.state.auth.create(name+'-user',PASSWORD,origin='synthetic',must_change=False)
    with TestClient(app,base_url='http://127.0.0.1:18310')as client:yield app,client,adapter,login(client)

def create(c,h,**changes):
    return c.post('/api/pilot/families',headers=h,json={'question':'Pojasni razliko.','task':'reference','locale':'sl','model':'qwen','idempotency_key':'cpu-test-creation',**changes})

def counts(app):
    with app.state.store.connect()as db:return {table:db.execute('SELECT count(*)FROM '+table).fetchone()[0]for table in ('families','jobs','runs','feedback','assignments')}

def test_capacity_admission_is_fast_no_new_family_or_queue_and_private_auth(cpu):
    app,c,a,h=cpu
    assert c.post('/api/pilot/logout',headers=h,json={}).status_code==200
    assert c.get('/api/pilot/capabilities').status_code==401
    h=login(c);before=counts(app);started=time.monotonic();r=create(c,h)
    assert r.status_code==503 and r.json()['error']['code']=='generation_waiting_capacity'
    assert time.monotonic()-started<1 and counts(app)==before and a.calls==a.freezes==[]
    observed=c.get('/api/pilot/capabilities').json()
    assert observed['generation']['models']['qwen']['available']is False and observed['lexical']['available']is True
    assert 'secret-model-pin'not in str(observed)

@pytest.mark.parametrize('task', ['terminology','text_check','source_search'])
def test_cpu_tasks_do_not_need_generation_capacity(cpu,task):
    app,c,a,h=cpu
    r=create(c,h,task=task,input_language='it',locale='sl',question='delibere')
    assert r.status_code==200;drain(app)
    out=c.get('/api/pilot/families/'+r.json()['family']['answer_family_id']).json()
    assert out['short']['state']=='complete' and out['short']['capability']==task
    assert len(a.freezes)==1

@pytest.mark.parametrize('question,task,language,field',[('Preveri morfologijo in sopomenke.','reference','sl','focus'),('Preveri morfologijo za »otrok«.','reference','und','input_language'),('Besedilo','text_check','und','input_language')])
def test_clarification_precedes_capacity_and_allocates_nothing(cpu,question,task,language,field):
    app,c,a,h=cpu;before=counts(app)
    for _ in range(2):
        r=create(c,h,question=question,task=task,input_language=language)
        if task=='text_check':assert r.status_code==400 and r.json()['error']['code']=='input_language_required'
        else:
            assert r.status_code==200 and r.json()['status']=='clarification_required'
            assert r.json()['clarification']['fields']==[field]and'family'not in r.json()
    assert counts(app)==before and a.freezes==[]

def test_focus_continuation_preserves_question_language_and_idempotency(cpu):
    app,c,a,h=cpu;a.ready=True
    body={'question':'Preveri morfologijo in sopomenke.','locale':'it','interface_locale':'sl','input_language':'sl','focus':'otrok'}
    first=create(c,h,**body);second=create(c,h,**body)
    assert first.status_code==second.status_code==200 and first.json()['family']['answer_family_id']==second.json()['family']['answer_family_id']
    fid=first.json()['family']['answer_family_id'];drain(app);out=c.get('/api/pilot/families/'+fid).json()
    assert out['question']==body['question']and out['focus']=='otrok'and out['locale']=='it'and out['input_language']=='sl'
    with app.state.store.connect()as db:
        assert db.execute('SELECT interface_locale FROM families WHERE id=?',(fid,)).fetchone()[0]=='sl'
        with pytest.raises(Exception):db.execute('UPDATE family_focus SET focus=?WHERE family_id=?',('spremenjeno',fid))
    a.ready=False
    assert create(c,h,**body).status_code==200 # Idempotent retry opens the existing receipt while no capacity.
    assert counts(app)['families']==counts(app)['jobs']==1

def test_capacity_loss_does_not_break_saved_source_feedback_or_ownership(cpu):
    app,c,a,h=cpu;a.ready=True;r=create(c,h);fid=r.json()['family']['answer_family_id'];drain(app)
    out=c.get('/api/pilot/families/'+fid).json();short=out['short'];a.ready=False
    assert c.get('/api/pilot/families/'+fid).json()==out
    assert c.get('/api/pilot/families/'+fid+'/sources/E1').status_code==200
    assert c.post('/api/pilot/runs/'+short['run_id']+'/delivered',headers=h,json={'display_sha256':short['display_sha256']}).status_code==200
    feedback={'run_id':short['run_id'],'rating':'useful','idempotency_key':'saved-cpu-feedback'}
    assert c.post('/api/pilot/feedback',headers=h,json=feedback).status_code==200
    target=c.get('/api/pilot/feedback').json()['feedback'][0]['target'];assert target['capability']=='reference'and target['answer_method']=='unknown'
    before=counts(app)
    assert c.post('/api/pilot/families/'+fid+'/expand',headers=h,json={'idempotency_key':'cpu-expand-rejected'}).status_code==503
    assert c.post('/api/pilot/comparisons',headers=h,json={'family_id':fid,'idempotency_key':'cpu-compare-rejected'}).status_code==503
    assert counts(app)==before
    h=login(c,'reviewer');assert c.get('/api/pilot/families/'+fid).status_code==404
    assert c.post('/api/pilot/feedback',headers=h,json=feedback).status_code==404

def test_worker_rechecks_capacity_before_freeze(cpu):
    app,c,a,h=cpu;a.ready=True;r=create(c,h);a.ready=False;drain(app)
    job=c.get('/api/pilot/jobs/'+r.json()['job']['job_id']).json()
    assert job['state']=='failed'and job['error_code']=='generation_waiting_capacity'and a.freezes==[]

def test_retained_clarification_cannot_expand_compare_or_rate(cpu):
    app,c,a,h=cpu;a.ready=True
    a.generate=lambda *args:{'status':'validated','answer_kind':'clarification','answer_md':'Izberite jezik.','clarification_code':'input_language_required'}
    r=create(c,h);fid=r.json()['family']['answer_family_id'];drain(app);short=c.get('/api/pilot/families/'+fid).json()['short']
    assert short['clarification_code']=='input_language_required'
    c.post('/api/pilot/runs/'+short['run_id']+'/delivered',headers=h,json={'display_sha256':short['display_sha256']})
    assert c.post('/api/pilot/feedback',headers=h,json={'run_id':short['run_id'],'rating':'useful','idempotency_key':'no-clarification-feedback'}).status_code==409
    assert c.post('/api/pilot/families/'+fid+'/expand',headers=h,json={'idempotency_key':'no-clarification-expand'}).status_code==409
    assert c.post('/api/pilot/comparisons',headers=h,json={'family_id':fid,'idempotency_key':'no-clarification-comparison'}).status_code==409
    assert counts(app)['feedback']==counts(app)['assignments']==0

def test_gateway_missing_endpoint_is_unavailable_without_provider_post(monkeypatch):
    calls=[]
    transport=httpx.MockTransport(lambda request:(calls.append(request)or httpx.Response(404,json={'error':'missing'})))
    original=httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=transport,**kw))
    gateway=Gateway('http://127.0.0.1:18311','x'*40)
    assert gateway.capabilities()['generation']['models']['qwen']['available']is False
    with pytest.raises(Failure,match='generation_waiting_capacity'):gateway.require_generation(['qwen'])
    assert calls and all(r.method=='GET'and r.url.path=='/internal/pilot/capabilities'for r in calls)

def test_replay_explicitly_defers_missing_model_without_freeze(cpu,tmp_path):
    app,c,a,h=cpu
    data={'schema':replay.SCHEMA,'origin':'previously_exposed_regression','cases':[{'case_id':'synthetic','question':'Test.','context':'','locale':'sl','task':'reference','tier':'short','model':'gemma','sources':['jezikovna_svetovalnica'],'source_groups':['synthetic'],'original_case_sha256':'a'*64,'exposure_set':'exposed_regression'}]}
    path=tmp_path/'cases.json';path.write_text(canonical(data));path.chmod(0o600)
    out=replay.run(app.state.store,a,path,replay.file_sha(path),force_test=True)
    assert out['status']=='DEFERRED_CAPACITY'and out['model_requests']==0 and a.calls==a.freezes==[]

def test_morphology_uses_existing_general_api_and_freezes_exact_candidates(tmp_path):
    from copy import deepcopy
    from pilot_backend.store import digest
    from pilot_backend.morphology import checked
    class MorphGateway(Gateway):
        def __init__(self):super().__init__('http://127.0.0.1:9','x'*40,linghub_url='http://127.0.0.1:8010');self.observed=[]
        def require_generation(self,models):raise AssertionError('No writer allowed for morphology')
        def request(self,path,payload,base=None):
            self.observed.append((path,deepcopy(payload),base))
            assert path=='/v1/morphology/lemmatize'and base==self.linghub_url
            return {'status':'ok','lang':payload['lang'],'items':[{'input':token,'candidates':[{'language':payload['lang'],'lemma':'SOURCE LEMMA','msds':['SOURCE TAG'],'pos_families':['SOURCE POS'],'matched_by':'exact'}],'ambiguous':False,'truncated':False}for token in payload['tokens']],'provenance':{'resource_id':'SYNTHETIC MORPHOLOGY','version':'fixture','source_identifier':'synthetic','source_url':'https://example.org/fixture','license':'synthetic','fingerprint':'a'*64,'analysis_kind':'lexical_candidates','contextual':False}}
    gateway=MorphGateway();app=create_app({'store':str(tmp_path/'private'/'pilot.sqlite'),'allowed_origins':['http://127.0.0.1:18310'],'secure_cookies':False,'tasks':['morphology']},adapter=gateway,start_workers=False)
    app.state.auth.create('tester-user',PASSWORD,origin='synthetic',must_change=False)
    with TestClient(app,base_url='http://127.0.0.1:18310')as c:
        h=login(c);r=create(c,h,question='besedi',task='morphology',input_language='sl',locale='it');assert r.status_code==200
        drain(app);family=c.get('/api/pilot/families/'+r.json()['family']['answer_family_id']).json();short=family['short']
        assert short['state']=='complete'and short['answer_kind']=='deterministic'and short['answer_method']=='deterministic'and short['capability']=='morphology'
        assert 'SOURCE LEMMA'in short['answer_md']and 'SOURCE TAG'in short['answer_md']and 'Non è una disambiguazione'in short['answer_md']
        assert gateway.observed==[('/v1/morphology/lemmatize',{'lang':'sl','tokens':['besedi'],'limit':4},gateway.linghub_url)]
        with app.state.store.connect()as db:
            bundle=json.loads(db.execute('SELECT bundle FROM families WHERE id=?',(family['answer_family_id'],)).fetchone()[0]);assert checked(bundle)==bundle
        saved_source=c.get('/api/pilot/families/'+family['answer_family_id']+'/sources/'+short['evidence'][0]['evidence_id'])
        assert saved_source.status_code==200 and 'SOURCE LEMMA'in saved_source.json()['excerpt']and len(gateway.observed)==1
        bundle['data']['items'][0]['candidates'][0]['lemma']='MUTATED'
        with pytest.raises(Failure):checked(bundle)


def test_legacy_non_answer_feedback_excluded_without_editing_records(cpu):
    app,c,a,h=cpu
    # Classification is independent of source-specific text or evaluation names.
    for kind in ('clarification','clarification_required','capability_unavailable'):
        row={'result':canonical({'answer_kind':kind})}
        original=dict(row);assert not cycles.answer_feedback(row)and row==original
    for kind in ('source_only','deterministic','generated','mixed'):
        assert cycles.answer_feedback({'result':canonical({'answer_kind':kind})})

def test_tool_health_observation_is_distinct_from_configured_urls(monkeypatch):
    observed=[]
    def handler(request):
        observed.append((request.method,request.url.path))
        if request.url.path=='/api/health':return httpx.Response(200,json={'status':'ok','morphology':{'usable':True}})
        if request.url.path=='/health':return httpx.Response(200,json={'status':'ok','resources':{'morphology':{'available':False}},'analyzer_service_reachable':True})
        return httpx.Response(404,json={})
    original=httpx.Client;transport=httpx.MockTransport(handler)
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=transport,**kw))
    gateway=Gateway('http://127.0.0.1:9','x'*40,linghub_url='http://127.0.0.1:8010',klikai_url='http://127.0.0.1:8000')
    caps=gateway.capabilities();assert caps['tools']['configured']and not caps['tools']['available']
    assert caps['tools']['tasks']['terminology']['available']and not caps['tools']['tasks']['morphology']['available']
    assert caps['tools']['tasks']['text_check']['available']and not caps['generation']['models']['qwen']['available']
    assert observed==[('GET','/api/health'),('GET','/health'),('GET','/internal/pilot/capabilities')]
