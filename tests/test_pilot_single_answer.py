"""One ordinary answer, preserving immutable historical storage and reader identity."""
import json
from fastapi.testclient import TestClient
from pilot_backend.jobs import Jobs
from pilot_backend.store import Store
from test_pilot_project_beta import beta_rig, ORIGIN, login, ask, finish


def test_new_family_dispatches_answer_and_refuses_expansion_without_another_job(beta_rig):
    app,adapter,config,_,credentials=beta_rig
    calls=[];base=adapter.generate
    def generate(bundle,tier,model,parent=None):
        calls.append((tier,parent));return {**base(bundle,tier,model,parent),'tier':tier}
    adapter.generate=generate
    source_calls=[];original=adapter.source
    def source(bundle,eid,tier):source_calls.append(tier);return original(bundle,eid,tier)
    adapter.source=source
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client,credentials);started=ask(client,headers);fid=started['family']['answer_family_id']
        assert started['family']['answer_format']=='single'
        assert started['family']['answer']['run_id']==started['family']['short']['run_id']
        # The mode survives a worker restart and even a later default-policy change.
        restarted=Jobs(app.state.store,adapter)
        app.state.domain.single_answers=False
        job=restarted.claim('model');restarted.execute(job)
        current=client.get('/api/pilot/families/'+fid).json()
        assert calls==[('answer',None)]
        assert current['answer']==current['short']
        assert current['answer']['source_tier']=='answer' and current['long'] is None
        for tier in ('answer','short'):
            r=client.get(f'/api/pilot/families/{fid}/sources/passage-0?tier={tier}')
            assert r.status_code==200 and 'Complete original 0.' in r.json()['excerpt']
        assert source_calls==['answer','answer']
        for key in ('single-no-expand-1','single-no-expand-2'):
            response=client.post(f'/api/pilot/families/{fid}/expand',json={'idempotency_key':key},headers=headers)
            assert response.status_code==409
        run=current['answer'];rid=run['run_id']
        client.post(f'/api/pilot/runs/{rid}/delivered',json={'delivery_token':run['delivery_token']},headers=headers).raise_for_status()
        client.post('/api/pilot/feedback',json={'run_id':rid,'comment':'Test source scope','idempotency_key':'single-answer-feedback'},headers=headers).raise_for_status()
        with app.state.store.connect()as db:
            saved=db.execute('SELECT tier,result FROM runs WHERE id=?',(rid,)).fetchone()
            assert saved['tier']=='short' # Compatibility storage slot, not generation profile.
            assert json.loads(saved['result'])['tier']=='answer'
            assert db.execute('SELECT count(*) FROM jobs WHERE run_id=?',(rid,)).fetchone()[0]==1
            assert db.execute('SELECT count(*) FROM runs WHERE family_id=?',(fid,)).fetchone()[0]==1
            observation=json.loads(db.execute('SELECT snapshot FROM feedback_observations').fetchone()[0])
            assert observation['tier']=='answer' and observation['storage_tier']=='short'
            assert observation['participant']['username']=='cu'
        assert calls==[('answer',None)]


def test_existing_populated_store_upgrade_is_additive_and_preserves_old_sources(beta_rig):
    app,adapter,config,_,credentials=beta_rig
    # Represent an older release which generated and stored both historical tiers.
    app.state.domain.single_answers=False
    source_calls=[];original=adapter.source
    def source(bundle,eid,tier):source_calls.append(tier);return original(bundle,eid,tier)
    adapter.source=source
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client,credentials);started=ask(client,headers);finish(app)
        fid=started['family']['answer_family_id']
        expanded=client.post(f'/api/pilot/families/{fid}/expand',json={'idempotency_key':'legacy-expand-001'},headers=headers)
        assert expanded.status_code==200;finish(app)
        family=client.get('/api/pilot/families/'+fid).json()
        for tier in ('short','long'):
            run=family[tier]
            client.post(f"/api/pilot/runs/{run['run_id']}/delivered",json={'delivery_token':run['delivery_token'],'expanded':tier=='long'},headers=headers).raise_for_status()
            client.post('/api/pilot/feedback',json={'run_id':run['run_id'],'comment':tier+' original feedback','idempotency_key':'legacy-feedback-'+tier},headers=headers).raise_for_status()
        before={}
        with app.state.store.connect()as db:
            for table in ('accounts','sessions','families','family_inputs','runs','jobs','deliveries','feedback','feedback_events','feedback_observations','run_observations'):
                before[table]=[tuple(row)for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]
            run_schema=db.execute("SELECT sql FROM sqlite_master WHERE type='table'AND name='runs'").fetchone()[0]
            # A genuine pre-mode store has no mode table. The empty table can be
            # removed without touching any existing family, run, feedback or FK.
            db.execute('DROP TABLE family_answer_modes')
        upgraded=Store(config['store'])
        with upgraded.connect()as db:
            for table,rows in before.items():assert [tuple(row)for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]==rows,table
            assert db.execute("SELECT sql FROM sqlite_master WHERE type='table'AND name='runs'").fetchone()[0]==run_schema
            assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            assert db.execute('SELECT count(*)FROM family_answer_modes').fetchone()[0]==0
        app.state.domain.single_answers=True
        for tier in ('short','long'):
            response=client.get(f'/api/pilot/families/{fid}/sources/passage-0?tier={tier}')
            assert response.status_code==200
        assert source_calls==['short','long']
        assert client.get(f'/api/pilot/families/{fid}/sources/passage-0?tier=answer').status_code==400
        old=client.get('/api/pilot/families/'+fid).json()
        assert 'answer_format' not in old
        assert old['short']['run_id']==family['short']['run_id'] and old['long']['run_id']==family['long']['run_id']
        assert len(adapter.calls)==2


def test_wrong_model_response_tier_is_not_relabelled(beta_rig):
    app,adapter,_,_,credentials=beta_rig
    base=adapter.generate
    adapter.generate=lambda *args,**kwargs:{**base(*args,**kwargs),'tier':'short'}
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client,credentials);started=ask(client,headers);finish(app)
        family=client.get('/api/pilot/families/'+started['family']['answer_family_id']).json()
        assert family['answer']['state']=='failed'
