import json,threading,time
from pathlib import Path
import pytest
from argon2 import PasswordHasher,Type
from fastapi.testclient import TestClient
from pilot_backend.api import create_app
from pilot_backend.security import Failure,Auth
from pilot_backend.store import Store,canonical,digest,stamp
from pilot_backend.domain import Domain
from pilot_backend.jobs import Jobs,render_result

PASSWORD='a long Unicode geslo ž😀'
class Adapter:
    def __init__(self):self.calls=[];self.freezes=[]
    def freeze(self,family,policy):
        self.freezes.append(family['id']);return {'bundle':{'question':family['question'],'snapshot':'fixed','policy':policy},'identity':{'snapshot':'fixed','models':{'qwen':'secret-model-pin','gemma':'other-private-pin'}}}
    def generate(self,bundle,tier,model,parent=None):
        self.calls.append((bundle,tier,model,parent));answer='Čebelica 😀 želi pomoč.'+(' Dolga razlaga.'if tier=='long'else'')
        return {'status':'validated','short_answer':answer,'answer_md':answer,'source_badges':[{'source_id':'s','source_label':'Vir'}],'evidence':[{'evidence_id':'E1','excerpt':'izvirnik','source_id':'s'}],'model_identity':model,'generation_profile_id':'PRIVATE_PROFILE','timings':{'seconds':123},'limitations':[]}
    def source(self,bundle,eid,tier):return {'evidence_id':eid,'source_label':'Vir','excerpt':'izvirnik','url':'https://example.org/source','bibliography':{'authors':['Avtor']}}
@pytest.fixture
def rig(tmp_path):
    adapter=Adapter();app=create_app({'store':str(tmp_path/'private'/'pilot.sqlite'),'allowed_origins':['http://127.0.0.1:18310'],'secure_cookies':False},adapter=adapter,hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1,type=Type.ID),start_workers=False)
    auth=app.state.auth
    ids={r:auth.create(r+'-user',PASSWORD,role=r,origin='synthetic',must_change=False)for r in ('admin','tester','reviewer')}
    with TestClient(app,base_url='http://127.0.0.1:18310')as c:yield app,c,adapter,ids

def login(c,role='tester'):
    r=c.post('/api/pilot/login',json={'username':role+'-user','password':PASSWORD},headers={'Origin':'http://127.0.0.1:18310'});assert r.status_code==200,r.text
    return {'Origin':'http://127.0.0.1:18310','X-CSRF-Token':r.json()['csrf_token']}
def family(c,h,key='family-00001'):
    r=c.post('/api/pilot/families',json={'question':'Kako?','locale':'sl','model':'qwen','task':'reference','idempotency_key':key},headers=h);assert r.status_code==200,r.text;return r.json()
def drain(app):
    while job:=app.state.jobs.claim():app.state.jobs.execute(job)
def completed(app,c,h):
    f=family(c,h);drain(app);return c.get('/api/pilot/families/'+f['family']['answer_family_id']).json()
def test_family_expansion_idempotency_frozen(rig):
    app,c,adapter,ids=rig;h=login(c);f=completed(app,c,h);fid=f['answer_family_id'];short=f['short'];assert short['state']=='complete'
    r=c.post(f'/api/pilot/families/{fid}/expand',json={'idempotency_key':'expand-key0001'},headers=h);assert r.status_code==200,r.text
    again=c.post(f'/api/pilot/families/{fid}/expand',json={'idempotency_key':'expand-key0001'},headers=h);assert again.json()['job']['job_id']==r.json()['job']['job_id']
    drain(app);final=c.get('/api/pilot/families/'+fid).json();assert final['short']==short;assert final['long']['parent_run_id']==short['run_id'];assert len(adapter.freezes)==1;assert adapter.calls[0][0]==adapter.calls[1][0];assert adapter.calls[1][3]['answer_md']==short['answer_md']
    conflict=c.post('/api/pilot/families',json={'question':'changed','idempotency_key':'family-00001'},headers=h);assert conflict.status_code==409

def test_auth_boundaries_and_ownership(rig):
    app,c,adapter,ids=rig
    assert c.get('/api/pilot/families').status_code==401
    assert c.get('/docs').status_code==404
    assert c.get('/health',headers={'Host':'evil.example'}).status_code==403
    assert c.post('/api/pilot/login',json={'username':'tester-user','password':PASSWORD}).status_code==403
    h=login(c);f=completed(app,c,h);fid=f['answer_family_id']
    assert c.post('/api/pilot/families',json={'question':'x'},headers={'Origin':h['Origin']}).status_code==403
    assert c.get('/api/pilot/admin/accounts').status_code==403
    login(c,'reviewer');assert c.get('/api/pilot/families/'+fid).status_code==404
    assert c.get(f'/api/pilot/families/{fid}/sources/E1').status_code==404
    assert c.get('/api/pilot/admin/feedback').json()=={'feedback':[]}

def test_feedback_exact_unicode_revision_and_source_target(rig):
    app,c,adapter,ids=rig;h=login(c);f=completed(app,c,h);r=f['short'];rid=r['run_id'];s=r['blocks'][0]['text'];start=s.index('😀')
    body={'run_id':rid,'rating':None,'tags':['accent'],'target':{'kind':'source','evidence_id':'E1'},'anchor':{'block_id':'answer','start':start,'end':start+1,'quote':'😀','prefix':s[:start],'suffix':s[start+1:]},'idempotency_key':'feedback0001'}
    assert c.post('/api/pilot/feedback',json=body,headers=h).status_code==409
    assert c.post('/api/pilot/runs/'+rid+'/delivered',json={'display_sha256':r['display_sha256']},headers=h).status_code==200
    out=c.post('/api/pilot/feedback',json=body,headers=h);assert out.status_code==200,out.text;assert out.json()['revision']==1
    assert c.post('/api/pilot/feedback',json=body,headers=h).json()==out.json()
    body['idempotency_key']='feedback0002';body['rating']='partly_useful';assert c.post('/api/pilot/feedback',json=body,headers=h).json()['revision']==2
    body['idempotency_key']='feedback0003';body['anchor']['end']+=1;assert c.post('/api/pilot/feedback',json=body,headers=h).status_code==400
    history=c.get('/api/pilot/feedback').json()['feedback'];assert len(history)==1;assert history[0]['revision']==2;assert history[0]['target']['kind']=='source'

def test_blind_mapping_delivery_reveal_no_leaks(rig):
    app,c,adapter,ids=rig;h=login(c);f=completed(app,c,h)
    p={'family_id':f['answer_family_id'],'blind':True,'idempotency_key':'comparison0001'}
    r=c.post('/api/pilot/comparisons',json=p,headers=h);assert r.status_code==200,r.text;aid=r.json()['comparison_id'];drain(app)
    out=c.get('/api/pilot/comparisons/'+aid).json();encoded=json.dumps(out)
    for forbidden in ('qwen','gemma','PRIVATE_PROFILE','123','display_sha256','timings'):assert forbidden not in encoded
    assert c.post(f'/api/pilot/comparisons/{aid}/reveal',json={},headers=h).status_code==409
    vote={'choice':'A','tier':'short','idempotency_key':'vote0000001'}
    assert c.post(f'/api/pilot/comparisons/{aid}/vote',json=vote,headers=h).status_code==409
    assert c.post(f'/api/pilot/comparisons/{aid}/delivered',json={'tier':'short'},headers=h).status_code==200
    assert c.post(f'/api/pilot/comparisons/{aid}/vote',json=vote,headers=h).status_code==200
    reveal=c.post(f'/api/pilot/comparisons/{aid}/reveal',json={},headers=h).json();assert{r['model']for r in reveal['sides'].values()}=={'qwen','gemma'}
    assert c.get(f'/api/pilot/comparisons/{aid}/sources/E1?side=A').status_code==200
    c.post(f'/api/pilot/comparisons/{aid}/expand',json={'idempotency_key':'expandcompare1'},headers=h);drain(app);long=c.get('/api/pilot/comparisons/'+aid).json();assert long['tier']=='long';assert long['short']==reveal['short'];assert all(r['state']=='complete'for r in long['sides'].values());assert len(adapter.freezes)==1

def test_cancel_capacity_recovery_and_fairness(rig):
    app,c,adapter,ids=rig;h=login(c);f=family(c,h);jid=f['job']['job_id']
    assert c.post('/api/pilot/jobs/'+jid+'/cancel',json={},headers=h).json()['state']=='cancelled';drain(app);assert not adapter.calls
    f=family(c,h,'family000002');j=app.state.jobs.claim();assert j
    app.state.store.recover_jobs();assert c.get('/api/pilot/jobs/'+j['id']).json()['state']=='failed'
    for n in range(4):family(c,h,'capacity'+str(n).zfill(3))
    r=c.post('/api/pilot/families',json={'question':'overflow','idempotency_key':'overflow01'},headers=h);assert r.status_code==429

def test_password_rotation_admin_and_deletion(rig):
    app,c,adapter,ids=rig;h=login(c,'admin')
    r=c.post('/api/pilot/admin/accounts',json={'username':'new-person','temporary_password':PASSWORD,'role':'tester','reauth_password':PASSWORD},headers=h);assert r.status_code==200,r.text;aid=r.json()['account_id']
    assert 'password_hash'not in c.get('/api/pilot/admin/accounts').text
    assert c.post('/api/pilot/admin/accounts/'+ids['admin']+'/disable',json={'reauth_password':PASSWORD},headers=h).status_code==409
    assert c.post('/api/pilot/admin/accounts/'+aid+'/delete',json={'reauth_password':PASSWORD},headers=h).status_code==200
    assert len(app.state.store.deletion_records())==1
    c.post('/api/pilot/logout',json={},headers=h)
    r=c.post('/api/pilot/login',json={'username':'new-person','password':PASSWORD},headers={'Origin':h['Origin']});assert r.status_code==401

def test_initial_password_gate_and_cookie_rotation(rig):
    app,c,adapter,ids=rig;app.state.auth.create('temporary-user',PASSWORD,origin='synthetic')
    r=c.post('/api/pilot/login',json={'username':'temporary-user','password':PASSWORD},headers={'Origin':'http://127.0.0.1:18310'});h={'Origin':'http://127.0.0.1:18310','X-CSRF-Token':r.json()['csrf_token']};old=c.cookies.get('klikai_session')
    assert c.get('/api/pilot/families').status_code==403
    out=c.post('/api/pilot/password',json={'current_password':PASSWORD,'new_password':'another long secure geslo'},headers=h);assert out.status_code==200;assert c.cookies.get('klikai_session')!=old
    with pytest.raises(Failure):app.state.auth.session(old)
    assert c.get('/api/pilot/families').status_code==200

def test_login_throttle_and_no_validation_input_echo(rig):
    app,c,adapter,ids=rig
    for i in range(8):assert c.post('/api/pilot/login',json={'username':'absent-user','password':'bad'},headers={'Origin':'http://127.0.0.1:18310'}).status_code==401
    assert c.post('/api/pilot/login',json={'username':'absent-user','password':'bad'},headers={'Origin':'http://127.0.0.1:18310'}).status_code==429
    r=c.post('/api/pilot/login',json={'username':{'password':'SECRET'},'password':'SECRET'},headers={'Origin':'http://127.0.0.1:18310'});assert r.status_code==400;assert'SECRET'not in r.text

def test_prefix_and_secure_cookie(tmp_path):
    app=create_app({'store':str(tmp_path/'private'/'pilot.sqlite'),'base_path':'/pilot','allowed_origins':['https://pilot.example'],'secure_cookies':True,'edge_token':'a'*40},adapter=Adapter(),hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1),start_workers=False)
    app.state.auth.create('test-user',PASSWORD,must_change=False)
    with TestClient(app,base_url='https://pilot.example')as c:
        assert c.get('/pilot/health').status_code==403
        r=c.post('/pilot/api/pilot/login',json={'username':'test-user','password':PASSWORD},headers={'Origin':'https://pilot.example','X-Pilot-Edge-Token':'a'*40,'X-Pilot-Client-IP':'192.0.2.1'});assert r.status_code==200
        assert'Secure'in r.headers['set-cookie']and'HttpOnly'in r.headers['set-cookie']and'Path=/pilot/'in r.headers['set-cookie']
        page=c.get('/pilot/it/',headers={'X-Pilot-Edge-Token':'a'*40,'X-Pilot-Client-IP':'192.0.2.1'});assert page.status_code==200;assert'__PILOT_'not in page.text

def test_bootstrap_is_atomic_replay_without_password_reset(rig):
    app,c,adapter,ids=rig
    # Separate clean store, no pre-existing administrator.
    store=Store(app.state.store.path.parent/'bootstrap'/'pilot.sqlite');auth=Auth(store,app.state.auth.hasher)
    aid=auth.create('first-admin',PASSWORD,'admin',bootstrap_id='deployment-0001')
    assert auth.create('first-admin','another password ignored','admin',bootstrap_id='deployment-0001')==aid
    with store.connect()as db:row=db.execute('SELECT password_hash FROM accounts WHERE id=?',(aid,)).fetchone()
    assert auth.verify(PASSWORD,row[0]);assert not auth.verify('another password ignored',row[0])
    with pytest.raises(Failure):auth.create('different-admin',PASSWORD,'admin',bootstrap_id='deployment-0001')
    with pytest.raises(Failure):auth.create('first-admin',PASSWORD,'admin',bootstrap_id='deployment-0002')

def test_forwarded_spoof_ignored_without_edge_and_security_headers(rig):
    app,c,adapter,ids=rig;seen=[];original=app.state.auth.login
    def capture(username,password,source):seen.append(source);return original(username,password,source)
    app.state.auth.login=capture
    r=c.post('/api/pilot/login',json={'username':'tester-user','password':PASSWORD},headers={'Origin':'http://127.0.0.1:18310','X-Forwarded-For':'198.51.100.1','X-Pilot-Client-IP':'198.51.100.2'});assert r.status_code==200;assert seen==['testclient']
    r=c.post('/api/pilot/login',json={},headers={'Origin':'https://evil','X-Pilot-Locale':'it'});assert r.status_code==403;assert r.headers['Cache-Control']=='no-store';assert r.json()['error']['locale']=='it';assert'Content-Security-Policy'in r.headers

def test_immutable_finished_families_votes_and_hidden_reasoning(rig):
    app,c,adapter,ids=rig;h=login(c);f=completed(app,c,h)
    import sqlite3
    with app.state.store.connect()as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE families SET question=?WHERE id=?',('different',f['answer_family_id']))
        with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE runs SET result=?WHERE id=?',('{}',f['short']['run_id']))
    result,_=render_result(dict(status='validated',short_answer='x',answer_md='x',source_badges=[],evidence=[],reasoning='DO NOT STORE',reasoning_content='private'))
    assert'reasoning'not in result and'reasoning_content'not in result

def test_asymmetric_blind_completion_is_held_and_job_poll_masked(rig):
    app,c,adapter,ids=rig;h=login(c);f=completed(app,c,h)
    pair=c.post('/api/pilot/comparisons',json={'family_id':f['answer_family_id'],'blind':True,'idempotency_key':'asymmetric-pair'},headers=h).json();aid=pair['comparison_id']
    assert pair['pair_state']=='preparing';assert pair['sides']=={'A':{'state':'queued'},'B':{'state':'queued'}}
    first=app.state.jobs.claim();app.state.jobs.execute(first)
    held=c.get('/api/pilot/comparisons/'+aid).json();assert held['sides']==pair['sides'];assert held['pair_state']=='preparing'
    polled=c.get('/api/pilot/jobs/'+first['id']).json();assert polled['state']=='queued'and polled['pair_state']=='preparing'and polled['position']is None
    drain(app);released=c.get('/api/pilot/comparisons/'+aid).json();assert released['pair_state']=='ready';assert all(r['state']=='complete'for r in released['sides'].values());assert all('job_id'not in r for r in released['sides'].values())

def test_cpu_source_search_is_not_blocked_by_running_generator(rig):
    app,c,adapter,ids=rig;h=login(c);family(c,h);running=app.state.jobs.claim('model');assert running
    r=c.post('/api/pilot/families',json={'question':'Poišči vir','task':'source_search','idempotency_key':'source-priority'},headers=h);assert r.status_code==200
    cpu=app.state.jobs.claim('cpu');assert cpu and cpu['model']=='cpu';app.state.jobs.execute(cpu);assert c.get('/api/pilot/jobs/'+cpu['id']).json()['state']=='complete'

def test_five_sessions_bounded_worker_recovery_and_model_fairness(rig):
    app,c,adapter,ids=rig;store=app.state.store;auth=app.state.auth;domain=app.state.domain
    class Slow(Adapter):
        def __init__(self):super().__init__();self.lock=threading.Lock();self.counts={'qwen':0,'gemma':0};self.maxima={'qwen':0,'gemma':0};self.max_total=0
        def generate(self,bundle,tier,model,parent=None):
            with self.lock:self.counts[model]+=1;self.maxima[model]=max(self.maxima[model],self.counts[model]);self.max_total=max(self.max_total,sum(self.counts.values()))
            time.sleep(.035)
            try:return super().generate(bundle,tier,model,parent)
            finally:
                with self.lock:self.counts[model]-=1
    slow=Slow();worker=Jobs(store,slow)
    for i in range(5):
        owner=auth.create('load-user'+str(i),PASSWORD,origin='synthetic',must_change=False);domain.create_family(owner,{'question':'simulated workload','model':'qwen'if i%2 else'gemma','idempotency_key':'loadrequest0001'})
    worker.start()
    try:
        with pytest.raises(RuntimeError):Jobs(store,slow).start()
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with store.connect()as db:unfinished=db.execute("SELECT count(*)FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
            if not unfinished:break
            time.sleep(.02)
        assert unfinished==0;assert slow.maxima=={'qwen':1,'gemma':1};assert slow.max_total==2;assert len(slow.calls)==5
    finally:worker.close()

def test_source_search_requires_no_selected_generator_with_real_adapter_mock():
    from pilot_backend.adapter import Gateway
    class Private(Gateway):
        def __init__(self):self.linghub_url='http://127.0.0.1:1';self.calls=[]
        def request(self,path,payload,base=None):
            self.calls.append((path,payload))
            if path=='/v1/capabilities':return {'identity':'pin','analyzer_service_reachable':True,'defaults':{'sl':'spacy'},'resources':[],'models':[]}
            if path=='/v1/analyze':return {'text':payload['text'],'lang':'sl','engine_used':'spacy','offset_convention':'unicode_codepoint_half_open','issues':[{'rule_id':'fixture','start':0,'end':1,'payload':{'SteMislili':'izvirna možnost'}}],'spans':[]}
            raise AssertionError('Generator called')
    adapter=Private();f=adapter.freeze({'task':'text_check','question':'ž','locale':'sl'},{})
    result=adapter.generate(f['bundle'],'short','qwen');assert result['answer_kind']=='deterministic';assert'izvirna možnost'in result['answer_md'];assert'{'not in result['answer_md'];assert all('internal/pilot'not in path for path,_ in adapter.calls)

def test_readonly_bootstrap_status_and_password_file_cli(tmp_path):
    import subprocess,sys,os
    path=tmp_path/'private'/'pilot.sqlite';password=tmp_path/'bootstrap-password';password.write_text(PASSWORD);password.chmod(0o600)
    base=[sys.executable,'-B','-m','pilot_backend','--store',str(path)]
    result=subprocess.run(base+['invite-admin','--username','first-admin','--password-file',str(password),'--bootstrap-id','test-activation-01'],capture_output=True,text=True);assert result.returncode==0,result.stderr;assert PASSWORD not in result.stdout+result.stderr
    before=path.stat().st_mtime_ns
    status=subprocess.run(base+['bootstrap-status','--bootstrap-id','test-activation-01'],capture_output=True,text=True);assert status.returncode==0;assert json.loads(status.stdout)['status']=='ready';assert before==path.stat().st_mtime_ns

@pytest.mark.parametrize('field,value',[('participant_id','other-user'),('owner','other-user'),('model',{}),('locale',[]),('task',{})])
def test_spoofed_participant_and_malformed_family_rejected(rig,field,value):
    app,c,adapter,ids=rig;h=login(c);payload={'question':'Test','idempotency_key':'malformed0001',field:value}
    r=c.post('/api/pilot/families',json=payload,headers=h);assert r.status_code==400
    with app.state.store.connect()as db:assert db.execute('SELECT count(*)FROM families').fetchone()[0]==0

def test_admin_saved_summary_and_actual_scheduler_observation(rig,tmp_path):
    from datetime import datetime,timezone
    from pilot_backend.cycles import analyse
    app,c,adapter,ids=rig;h=login(c,'admin');analysis=analyse(app.state.store)
    response=c.get('/api/pilot/admin/overview').json();assert response['summary']==analysis['reports'];assert response['analysis_cycle_id']==analysis['cycle_id'];assert'next_run'not in response
    path=tmp_path/'scheduler.json';app.state.config['scheduler_status_file']=str(path)
    next_run=datetime.fromtimestamp(stamp()+3600,timezone.utc).isoformat();path.write_text(json.dumps({'installed':True,'observed_at':stamp(),'next_run':next_run}));assert c.get('/api/pilot/admin/overview').json()['next_run']==next_run
    path.write_text(json.dumps({'installed':False,'observed_at':stamp(),'next_run':next_run}));assert'next_run'not in c.get('/api/pilot/admin/overview').json()
    path.write_text(json.dumps({'installed':True,'observed_at':stamp()-90000,'next_run':next_run}));assert'next_run'not in c.get('/api/pilot/admin/overview').json()
    path.write_text('broken');assert c.get('/api/pilot/admin/overview').status_code==200
