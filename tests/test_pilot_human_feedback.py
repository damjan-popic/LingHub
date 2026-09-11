"""Human-testing contracts using synthetic answers and a disposable account store."""
import csv,hashlib,io,json,sqlite3
import httpx,pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pilot_backend.api import create_app
from pilot_backend.adapter import Gateway
from pilot_backend.store import Store,canonical
from pilot_backend import cycles,replay
from test_pilot_backend import Adapter,PASSWORD,login,drain

class CPUAdapter(Adapter):
    def capabilities(self):
        return {'generation':{'models':{m:{'available':True,'device':'cpu','gpu_available':False,'status':'CPU_READY','cpu':{'available':True,'phase':'ready','queued_or_running':0,'queue_capacity':2}}for m in ('qwen','gemma')}},'lexical':{'available':True}}
    def require_generation(self,models):pass
    def generate(self,bundle,tier,model,parent):
        out=super().generate(bundle,tier,model,parent)
        out.update(model_identity={'repository':'fixture/'+model,'revision':'fixed-model-revision'},execution={'device':'cpu','dtype':'bfloat16','runtime':'transformers','revision':'fixed-model-revision'},source_snapshot_ids=['source-v1'],vector_snapshot_id='vector-v1',policy_version='writer-v1')
        out['evidence'][0]['source_revision']='source-revision-1'
        out['blocks']=[{'block_id':'claim','text':'Trditev 🙂 [1]'}, {'block_id':'qualification','text':'Velja samo v tem sobesedilu.\nDruga možnost ni izključena.'}]
        if tier=='long':out['correction']={'required':True}
        return out

@pytest.fixture
def human(tmp_path):
    config={'store':str(tmp_path/'private'/'pilot.sqlite'),'allowed_origins':['http://127.0.0.1:18310'],'secure_cookies':False,'human_testing_mode':True}
    adapter=CPUAdapter();app=create_app(config,adapter,PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1),start_workers=False)
    # Test-created human rows exercise eligibility; these never enter a live store.
    for role,origin in [('tester','human'),('admin','synthetic'),('reviewer','synthetic')]:app.state.auth.create(role+'-user',PASSWORD,role=role,origin=origin,must_change=False)
    with TestClient(app,base_url='http://127.0.0.1:18310')as client:yield app,client,config,adapter

def answered(app,c,h,question='  Izvirno?\n🙂  ',key='human-question-01',tier='short'):
    r=c.post('/api/pilot/families',headers=h,json={'question':question,'interface_locale':'it','locale':'sl','model':'qwen','idempotency_key':key});assert r.status_code==200,r.text
    fid=r.json()['family']['answer_family_id'];drain(app)
    if tier=='long':
        assert c.post('/api/pilot/families/'+fid+'/expand',headers=h,json={'idempotency_key':key+'-long'}).status_code==200;drain(app)
    family=c.get('/api/pilot/families/'+fid).json();run=family[tier]
    assert c.post('/api/pilot/runs/'+run['run_id']+'/delivered',headers=h,json={'display_sha256':run['display_sha256'],'expanded':tier=='long'}).status_code==200
    return family,run

def rate(c,h,run,score=4,**extra):
    return c.post('/api/pilot/feedback',headers=h,json={'run_id':run['run_id'],'quality_score':score,'display_locale':'it','comment':'Vsebuje omejitev. 🙂','idempotency_key':'quality-'+run['run_id'],**extra})

def test_exact_prompt_display_and_versions_are_server_bound_and_survive_reopen(human):
    app,c,config,a=human;h=login(c);family,run=answered(app,c,h,tier='long')
    assert family['question']=='  Izvirno?\n🙂  '
    assert run['model_identity']['repository']=='fixture/qwen'and run['execution']['device']=='cpu'
    saved=rate(c,h,run);assert saved.status_code==200,saved.text
    assert rate(c,h,run).json()==saved.json()
    item=c.get('/api/pilot/feedback').json()['feedback'][0];o=item['observation']
    assert item['quality_score']==4 and item['rating']is None
    assert o['original_prompt']==family['question']and o['original_prompt_exact']is True
    assert o['displayed_answer']=='\n\n'.join(o['display_paragraphs'])
    assert o['display_text_sha256']==hashlib.sha256(o['displayed_answer'].encode()).hexdigest()
    assert o['display_paragraphs'][0].startswith('La spiegazione estesa propone')
    assert o['display_paragraphs'][1:]==[b['text']for b in run['blocks']]
    assert o['display_sha256']==run['display_sha256']and o['tier']=='long'
    assert o['actual_model']['revision']=='fixed-model-revision'and o['execution']['device']=='cpu'
    assert o['versions']['source_snapshot_ids']==['source-v1']and o['versions']['vector_snapshot_id']=='vector-v1'
    assert o['versions']['evidence'][0]['source_revision']=='source-revision-1'
    assert 'answer_pin'not in o['versions']
    reopened=Store(config['store'])
    with reopened.connect()as db:
        assert json.loads(db.execute('SELECT snapshot FROM feedback_observations').fetchone()[0])==o
        assert db.execute('SELECT count(*)FROM feedback_events').fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE feedback_observations SET quality_score=1')
    from pilot_backend.backup import backup,restore
    from pathlib import Path
    root=Path(config['store']).parent.parent;archive=root/'feedback-backup.tar.gz'
    receipt=backup(reopened,archive);restore(archive,root/'recovery',expected_sha256=receipt['sha256'])
    with Store(root/'recovery'/'pilot.sqlite').connect()as db:
        assert json.loads(db.execute('SELECT snapshot FROM feedback_observations').fetchone()[0])==o
        assert db.execute('SELECT original_question FROM family_inputs').fetchone()[0]==family['question']
        assert db.execute('SELECT quality_score FROM feedback_observations').fetchone()[0]==4

@pytest.mark.parametrize('score',[0,6,True,'5',3.5])
def test_invalid_scores_do_not_create_feedback(human,score):
    app,c,_,_=human;h=login(c);_,run=answered(app,c,h)
    assert rate(c,h,run,score).status_code==400
    assert c.get('/api/pilot/feedback').json()=={'feedback':[]}

def test_old_feedback_preserved_new_scale_independent_and_admin_exports(human):
    app,c,_,_=human;h=login(c);_,run=answered(app,c,h,question='=IMPORTDATA("do not execute")\nOriginal')
    old=c.post('/api/pilot/feedback',headers=h,json={'run_id':run['run_id'],'rating':'partly_useful','idempotency_key':'legacy-rating-0001'});assert old.status_code==200
    assert c.get('/api/pilot/feedback').json()['feedback'][0]['quality_score']is None
    assert rate(c,h,run,5).status_code==200
    h=login(c,'admin');rows=c.get('/api/pilot/admin/feedback').json()['feedback'];assert rows[0]['quality_score']==5
    data=c.post('/api/pilot/admin/export',headers=h,json={'reauth_password':PASSWORD}).json()
    who=next(a for a in data['accounts']if a['account']['username']=='tester-user');events=who['feedback_revisions']
    assert len(events)==2 and events[0]['rating']=='partly_useful'and events[0]['quality_score']is None
    assert events[1]['observation']['original_prompt'].startswith('=IMPORTDATA')
    raw=c.post('/api/pilot/admin/export',headers=h,json={'reauth_password':PASSWORD,'format':'csv'});assert raw.status_code==200
    table=list(csv.DictReader(io.StringIO(raw.text)));assert len(table)==2
    assert table[-1]['quality_score']=='5'and table[-1]['backend']=='cpu'
    assert table[-1]['original_prompt'].startswith("'=IMPORTDATA")
    assert table[-1]['displayed_answer']==events[1]['observation']['displayed_answer']
    assert 'password_hash'not in raw.text and 'csrf'not in raw.text

def test_human_summary_excludes_synthetic_and_defers_even_force_replay(human):
    app,c,_,_=human;h=login(c);_,human_run=answered(app,c,h);assert rate(c,h,human_run,2).status_code==200
    h=login(c,'admin');_,synthetic_run=answered(app,c,h,key='synthetic-01');assert rate(c,h,synthetic_run,5).status_code==200
    report=cycles.analyse(app.state.store);q=report['counts']['human_quality']
    assert q['rated_answers']==1 and q['mean']==2 and q['histogram']['2']==1
    assert report['counts']['nonhuman_quality_by_origin']['synthetic']['mean']==5
    overview=c.get('/api/pilot/admin/overview').json();assert overview['human_feedback_summary']==q and overview['human_testing_mode']is True
    assert cycles.adapt(app.state.store)['status']=='DEFERRED_HUMAN_TESTING'
    assert cycles.monitor_canary(app.state.store)['status']=='DEFERRED_HUMAN_TESTING'
    assert replay.run(app.state.store,None,'not-read','not-read',force_test=True)['model_requests']==0
    assert c.post('/api/pilot/admin/pause',headers=h,json={'paused':False}).status_code==409
    assert c.get('/api/pilot/session').json()['capabilities']['comparisons']is False
    assert c.post('/api/pilot/comparisons',headers=h,json={'family_id':synthetic_run['run_id'],'idempotency_key':'deferred-compare'}).status_code==409
    # An owner export must not disclose another participant's analysis.
    h=login(c);export=c.post('/api/pilot/account/export',headers=h,json={'current_password':PASSWORD}).json();assert export['latest_analysis']is None

def test_cpu_capability_is_accepted_with_sanitized_status_and_timeout(monkeypatch):
    calls=[]
    def handler(request):
        if request.url.path.endswith('capabilities'):return httpx.Response(200,json={'generation':{'models':{'qwen':{'available':True,'gpu_available':False,'device':'cpu','status':'CPU_LOADING','cpu':{'available':True,'phase':'loading','queued_or_running':1,'queue_capacity':2,'private':'SECRET'}}}},'lexical':{'available':True}})
        return httpx.Response(200,json={'status':'validated'})
    original=httpx.Client
    def client(**kwargs):calls.append(kwargs['timeout'].read);return original(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(httpx,'Client',client);gateway=Gateway('http://127.0.0.1:8031','a'*40)
    observed=gateway.capabilities();assert observed['generation']['models']['qwen']['status']=='CPU_LOADING'
    assert 'SECRET'not in str(observed);gateway.require_generation(['qwen'])
    gateway.request('/internal/pilot/generate',{});assert calls[-1]==3600
