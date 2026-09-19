"""Project beta routes, persistent shared accounts and frozen source browsing."""
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import quote

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import pytest

from pilot_backend.api import create_app, config_checked
from pilot_backend.jobs import Jobs
from pilot_backend.project_accounts import provision
from pilot_backend.security import Auth, Failure
from pilot_backend.store import Store, canonical, digest
from pilot_backend import unified

ORIGIN = 'http://127.0.0.1:18310'


def hasher():return PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class BetaAdapter:
    def __init__(self, no_support=False):
        self.calls=[];self.freezes=[];self.no_support=no_support;self.on_generate=None

    def freeze(self, family, policy):
        self.freezes.append(family['id'])
        matches=[]
        for index in range(25):
            matches.append({'evidence_id':f'passage-{index}', 'source_slug':'jezikovna_posta',
                'source_label':'Jezikovna pošta', 'external_id':f'original-{index}',
                'title':f'Original {index}', 'snippet':'Short preview.', 'capture_sha256':'source-revision',
                'source':{'title':f'Original {index}', 'excerpt':f'Complete original {index}. '+'Necessary condition. '*30,
                    'source_label':'Jezikovna pošta','edition':'2024','status':'published',
                    'locator':{'capture_sha256':'PRIVATE_SOURCE_HASH','char_start':0},
                    'bibliography':{'authors':['Avtor'],'date':'2024-01-01','private_hash':'PRIVATE_BIB_HASH'}}})
        duplicate=deepcopy(matches[0]);duplicate['evidence_id']='passage-0-second-span';matches.append(duplicate)
        edition=deepcopy(matches[0]);edition['evidence_id']='passage-0-new-edition'
        edition['source']['edition']='2025';edition['capture_sha256']='new-revision';matches.append(edition)
        late=deepcopy(matches[0]);late.update(evidence_id='late-source-passage',source_slug='zkb',source_label='ZKB')
        late['source'].update(source_label='ZKB');matches.append(late)
        body={'contract':unified.CONTRACT,'task':'reference','query':family['question'],'locale':family['locale'],
              'matches':matches,'retrieval':{'status':'complete','coverage':[]}}
        body['snapshot_sha256']=digest(body)
        return {'bundle':body,'identity':{'private_model':'PRIVATE_IDENTITY'}}

    def generate(self,bundle,tier,model,parent=None):
        self.calls.append(model)
        if self.on_generate:self.on_generate()
        item=bundle['matches'][0]
        evidence=[{'evidence_id':item['evidence_id'],'local_number':1,'source_slug':item['source_slug'],
                   'title':item['title'],'source_revision':'PRIVATE_SOURCE_HASH','locator':item['source']['locator']}]
        answer='Ni dovolj opore za odgovor.'if self.no_support else'Podprt odgovor [1].'
        return {'status':'no_answer'if self.no_support else'validated','answer_md':answer,'short_answer':answer,
            'answer_kind':'source_only'if self.no_support else'generated',
            'generation_status':'not_generated'if self.no_support else'generated',
            'blocks':[{'block_id':'C1','text':answer,'evidence_ids':[]if self.no_support else[item['evidence_id']]}],
            'evidence':evidence,'citations':[]if self.no_support else[{'claim_id':'C1','evidence_id':item['evidence_id']}],
            'model_identity':{'repository':'Qwen/PRIVATE_WRITER','revision':'PRIVATE_REVISION'},
            'execution':{'device':'gpu','provider_scope':'own','selection':model},
            'generation':{'status':'not_generated'if self.no_support else'generated','provider':{'repository':'PRIVATE_PROVIDER'}},
            'quality_actions':[{'code':'PRIVATE_GUARD'}],'usage':{'prompt_tokens':123},
            'limitations':[{'code':'source_scope','scope':'claim','claim_ids':['C1'],'message':'PRIVATE_SCOPE_DETAIL'},
                {'code':'proposal_status','scope':'claim','claim_ids':['C1']},
                {'code':'PRIVATE_GUARD','scope':'answer','claim_ids':[],'message':'PRIVATE_GUARD_DETAIL'}],
            'generation_profile_id':'PRIVATE_PROFILE','source_badges':[{'source_slug':'jezikovna_posta','label':'Pošta'}]}

    def source(self,bundle,eid,tier):return unified.source(bundle,eid)


@pytest.fixture
def beta_rig(tmp_path):
    adapter=BetaAdapter()
    config={'store':str(tmp_path/'private/pilot.sqlite'),'allowed_origins':[ORIGIN],'secure_cookies':False,
            'unified_answers':True,'klikai_url':'http://127.0.0.1:8000','models':['auto','qwen','gemma'],
            'project_beta':True,'beta_updated':'19. september 2026','release_id':'project-beta-test',
            'beta_release':'EXACT_INTERNAL_RELEASE'}
    app=create_app(config,adapter=adapter,hasher=hasher(),start_workers=False)
    note=tmp_path/'credentials.json'
    provision(app.state.store,note,hasher=hasher())
    credentials={r['username']:r for r in json.loads(note.read_text())['accounts']}
    app.state.auth.create('operator','operator test password',role='admin',must_change=False)
    return app,adapter,config,note,credentials


def login(client,credentials,name='cu'):
    password=credentials[name]['password']if name in credentials else'operator test password'
    result=client.post('/api/pilot/login',json={'username':name,'password':password},headers={'Origin':ORIGIN})
    assert result.status_code==200,result.text
    return {'Origin':ORIGIN,'X-CSRF-Token':result.json()['csrf_token']}


def ask(client,headers,key='beta-question-001',model='gemma'):
    result=client.post('/api/pilot/families',json={'question':'Vprašanje?','model':model,'idempotency_key':key},headers=headers)
    assert result.status_code==200,result.text
    return result.json()


def finish(app):
    job=app.state.jobs.claim('model');assert job
    app.state.jobs.execute(job)


def test_project_provisioning_is_idempotent_and_preserves_sessions(beta_rig):
    app,_,config,note,credentials=beta_rig
    assert set(credentials)=={'cu','slori'}and credentials['cu']['password']!=credentials['slori']['password']
    assert note.stat().st_mode&0o077==0
    with app.state.store.connect()as db:before=[dict(r)for r in db.execute('SELECT *FROM accounts ORDER BY id')]
    token,_=app.state.auth.login('cu',credentials['cu']['password'],'test')
    saved=note.read_bytes();again=provision(app.state.store,note,hasher=hasher())
    assert [r['status']for r in again['accounts']]==['existing','existing']and note.read_bytes()==saved
    reloaded=create_app(config,adapter=BetaAdapter(),hasher=hasher(),start_workers=False)
    assert reloaded.state.auth.session(token)['username']=='cu'
    with app.state.store.connect()as db:after=[dict(r)for r in db.execute('SELECT *FROM accounts ORDER BY id')]
    assert before==after
    for row in after:
        if row['username']in credentials:
            assert row['role']=='tester'and row['cohort']=='project_beta'and row['origin']=='human'
            assert not row['must_change']and row['password_expires']is None


def test_existing_account_without_credential_note_is_not_adopted_or_reset(tmp_path):
    store=Store(tmp_path/'private/pilot.sqlite');auth=Auth(store,hasher=hasher())
    aid=auth.create('cu','existing shared password',must_change=False)
    with pytest.raises(Failure,match='project_account_credentials_required'):
        provision(store,tmp_path/'missing-note.json',hasher=hasher())
    assert not(tmp_path/'missing-note.json').exists()
    assert auth.login('cu','existing shared password','test')[1]['id']==aid
    with store.connect()as db:assert db.execute('SELECT count(*)FROM accounts').fetchone()[0]==1


def test_interrupted_account_transaction_reuses_private_note_without_partial_accounts(tmp_path):
    store=Store(tmp_path/'private/pilot.sqlite');note=tmp_path/'credentials.json'
    class InterruptedHasher:
        def __init__(self):self.calls=0;self.real=hasher()
        def hash(self,value):
            self.calls+=1
            if self.calls==3:raise RuntimeError('simulated interrupted second account')
            return self.real.hash(value)
    with pytest.raises(RuntimeError,match='simulated interrupted'):
        provision(store,note,hasher=InterruptedHasher())
    saved=note.read_bytes()
    with store.connect()as db:assert db.execute('SELECT count(*)FROM accounts').fetchone()[0]==0
    result=provision(store,note,hasher=hasher())
    assert note.read_bytes()==saved and [r['status']for r in result['accounts']]==['created','created']
    for row in json.loads(saved)['accounts']:
        assert Auth(store,hasher()).login(row['username'],row['password'],'test')[1]['id']==row['account_id']


def test_tester_forces_qwen_clean_presentation_and_retains_internal_feedback(beta_rig):
    app,adapter,_,_,credentials=beta_rig
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client,credentials)
        session=client.get('/api/pilot/session').json()
        assert session['capabilities']=={'project_beta':True,'tasks':['reference'],'unified_answers':True,
            'comparisons':False,'beta_updated':'19. september 2026'}
        assert session['user']['shared_group']and not session['user']['can_change_password']
        assert not session['user']['must_change_password']
        started=ask(client,headers,model={'untrusted':'automatic-shared'})
        finish(app);fid=started['family']['answer_family_id']
        family=client.get('/api/pilot/families/'+fid).json();run=family['short']
        assert adapter.calls==['qwen']and run['answer_md']=='Podprt odgovor [1].'
        assert run['limitations']==[{'code':'source_scope','scope':'claim','claim_ids':['C1']},
            {'code':'proposal_status','scope':'claim','claim_ids':['C1']}]
        responses=[family,client.get('/api/pilot/families').json(),client.get('/api/pilot/jobs/'+started['job']['job_id']).json()]
        for body in responses:
            encoded=json.dumps(body)
            for forbidden in ('qwen','gemma','PRIVATE_','model_identity','execution','display_sha256','quality_actions','prompt_tokens','generation_profile_id'):
                assert forbidden not in encoded
        assert client.post('/api/pilot/runs/'+run['run_id']+'/delivered',json={'delivery_token':'wrong'},headers=headers).status_code==409
        assert client.post('/api/pilot/runs/'+run['run_id']+'/delivered',json={'delivery_token':run['delivery_token']},headers=headers).status_code==200
        assert client.post('/api/pilot/feedback',json={'run_id':run['run_id'],'comment':'Popravite poved.',
            'rating':'partly_useful','idempotency_key':'beta-feedback-001'},headers=headers).status_code==200
        assert 'PRIVATE_'not in client.get('/api/pilot/feedback').text
        with app.state.store.connect()as db:
            stored=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(run['run_id'],)).fetchone()[0])
            observation=json.loads(db.execute('SELECT snapshot FROM feedback_observations').fetchone()[0])
        assert stored['model_identity']['repository']=='Qwen/PRIVATE_WRITER'
        assert observation['participant']['username']=='cu'and observation['participant']['cohort']=='project_beta'
        assert observation['selected_model']=='qwen'and observation['versions']['application_release']=='EXACT_INTERNAL_RELEASE'
        assert client.get('/health').json()=={'status':'ok','release_id':'project-beta-test'}


def test_source_badges_count_all_unique_records_and_keep_each_exact_target(beta_rig):
    app,adapter,_,_,credentials=beta_rig
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client,credentials);started=ask(client,headers);fid=started['family']['answer_family_id']
        def during_generation():
            summary=client.get(f'/api/pilot/families/{fid}/sources').json()
            page=client.get(f'/api/pilot/families/{fid}/matches?source=jezikovna_posta').json()
            assert summary['answer_status']=='pending'and not summary['usage_final']
            assert all(row['used_in_answer']is None for row in page['items'])
        adapter.on_generate=during_generation
        finish(app)
        summary=client.get(f'/api/pilot/families/{fid}/sources').json()
        assert [(s['source_slug'],s['returned_count'])for s in summary['sources']]==[('jezikovna_posta',26),('zkb',1)]
        assert summary['returned_count']==27 and summary['usage_final']
        first=client.get(f'/api/pilot/families/{fid}/matches?source=jezikovna_posta&limit=20').json()
        second=client.get(f'/api/pilot/families/{fid}/matches?source=jezikovna_posta&offset=20&limit=20').json()
        late=client.get(f'/api/pilot/families/{fid}/matches?source=zkb').json()
        assert first['next_offset']==20 and second['next_offset']is None
        assert len(first['items'])==20 and len(second['items'])==6 and len(late['items'])==1
        rows=first['items']+second['items']+late['items']
        assert rows[0]['evidence_ids']==['passage-0','passage-0-second-span']and rows[0]['used_in_answer']
        assert not any(r['used_in_answer']for r in rows[1:])
        assert rows[25]['edition']=='2025'and rows[25]['record_id']!=rows[0]['record_id']
        for row in rows:
            for eid in row['evidence_ids']:
                response=client.get(f'/api/pilot/families/{fid}/sources/'+quote(eid,safe=''))
                assert response.status_code==200 and len(response.json()['excerpt'])>360
                assert 'PRIVATE_'not in response.text and 'locator'not in response.json()
        assert adapter.calls==['qwen']and adapter.freezes==[fid]
        assert client.get(f'/api/pilot/families/{fid}/matches?source=zkb').json()==late
        assert client.get(f'/api/pilot/families/{fid}/matches?source=zkb&limit=0').status_code==400


def test_no_support_keeps_incidental_hits_browsable_without_used_badges(beta_rig):
    app,adapter,_,_,credentials=beta_rig;adapter.no_support=True
    with TestClient(app,base_url=ORIGIN)as client:
        started=ask(client,login(client,credentials));finish(app);fid=started['family']['answer_family_id']
        summary=client.get(f'/api/pilot/families/{fid}/sources').json()
        assert summary['answer_status']=='no_support'and summary['returned_count']==27
        page=client.get(f'/api/pilot/families/{fid}/matches?source=jezikovna_posta').json()
        assert all(row['used_in_answer']is False for row in page['items'])


def test_shared_sessions_have_distinct_jobs_logout_and_cross_account_access(beta_rig):
    app,_,_,_,credentials=beta_rig
    with TestClient(app,base_url=ORIGIN)as one,TestClient(app,base_url=ORIGIN)as two,TestClient(app,base_url=ORIGIN)as other:
        h1=login(one,credentials);h2=login(two,credentials);h3=login(other,credentials,'slori')
        assert one.cookies.get('klikai_session')!=two.cookies.get('klikai_session')
        first=ask(one,h1,'one-browser-question');second=ask(two,h2,'two-browser-question')
        assert first['job']['job_id']!=second['job']['job_id']and first['family']['answer_family_id']!=second['family']['answer_family_id']
        fid=first['family']['answer_family_id'];jid=first['job']['job_id']
        assert other.get('/api/pilot/families/'+fid).status_code==404
        assert other.get('/api/pilot/jobs/'+jid).status_code==404
        assert other.get(f'/api/pilot/families/{fid}/sources').status_code==404
        assert other.get(f'/api/pilot/families/{fid}/sources/passage-0').status_code==404
        assert one.post('/api/pilot/jobs/'+jid+'/cancel',json={},headers=h1).status_code==200
        assert two.get('/api/pilot/jobs/'+second['job']['job_id']).json()['state']=='queued'
        assert one.post('/api/pilot/logout',json={},headers=h1).status_code==200
        assert two.get('/api/pilot/session').json()['authenticated']
        assert other.get('/api/pilot/session').json()['authenticated']
        assert one.get('/api/pilot/families/'+fid).status_code==401


def test_group_mutations_and_tester_diagnostics_are_server_denied_operator_unchanged(beta_rig):
    app,adapter,_,_,credentials=beta_rig
    with TestClient(app,base_url=ORIGIN)as client:
        h=login(client,credentials)
        for path in ('/password','/account/delete','/account/export','/comparisons'):
            assert client.post('/api/pilot'+path,json={'current_password':credentials['cu']['password']},headers=h).status_code==403
        assert client.get('/api/pilot/comparisons/arbitrary').status_code==403
        assert client.get('/api/pilot/admin/feedback').status_code==403
        login(client,credentials,'operator')
        capabilities=client.get('/api/pilot/capabilities').json()
        assert capabilities['models']==['auto','qwen','gemma']and not capabilities.get('project_beta')
        assert client.get('/api/pilot/admin/accounts').status_code==200


def test_capacity_failure_has_plain_localized_message_and_no_diagnostics(beta_rig):
    app,_,config,_,credentials=beta_rig
    def unavailable(*_args):raise Failure('generation_waiting_capacity',503)
    app.state.domain.create_family=unavailable
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client,credentials)
        response=client.post('/api/pilot/families',json={'question':'Vprašanje?','idempotency_key':'capacity-question'},headers=headers)
        assert response.status_code==503
        assert response.json()=={'error':{'code':'generation_waiting_capacity','locale':'sl',
            'message':'Priprava odgovora trenutno ni na voljo. Najdene vire lahko še vedno pregledate.'}}
    for models in (None,False,17):
        with pytest.raises(ValueError,match='supported model choices'):config_checked({**config,'models':models})


def test_shutdown_stops_dequeue_and_uses_one_bounded_drain_budget(tmp_path,monkeypatch):
    jobs=Jobs(Store(tmp_path/'private/pilot.sqlite'),BetaAdapter())
    joined=[]
    class Worker:
        def join(self,timeout):
            assert jobs.stop_event.is_set();joined.append(timeout)
        def is_alive(self):return False
    clock=iter((0.,1.,3.))
    monkeypatch.setattr('pilot_backend.jobs.time.monotonic',lambda:next(clock))
    jobs.threads=[Worker(),Worker()]
    jobs.close(grace_seconds=10)
    assert joined==[9.,7.]
