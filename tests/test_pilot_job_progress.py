"""Real owned job lifecycle with blocked synthetic operations; no live provider."""
import json
import threading

from fastapi.testclient import TestClient
import pytest

from pilot_backend.security import Failure
from test_pilot_unified import ORIGIN, QUERY, UnifiedGateway, app_for, login


def submit(client, headers, key):
    response=client.post('/api/pilot/families',headers=headers,
        json={'question':QUERY,'idempotency_key':key})
    assert response.status_code==200,response.text
    return response.json()


def start_blocked(app, entered):
    job=app.state.jobs.claim('model');assert job
    thread=threading.Thread(target=app.state.jobs.execute,args=(job,))
    thread.start();assert entered.wait(5),'The synthetic bounded operation did not start'
    return job,thread


def test_cancel_after_claim_does_not_begin_retrieval(tmp_path):
    gateway=UnifiedGateway();app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client);created=submit(client,headers,'cancel-before-start')
        job=app.state.jobs.claim('model');assert job
        client.post('/api/pilot/jobs/'+job['id']+'/cancel',json={},headers=headers)
        app.state.jobs.execute(job)
        assert gateway.calls==[]
        assert client.get('/api/pilot/jobs/'+job['id']).json()['state']=='cancelled'
        assert client.get('/api/pilot/families/'+created['family']['answer_family_id']).json()['short']['state']=='cancelled'


@pytest.mark.parametrize('operation_fails',[False,True])
def test_cancel_during_retrieval_prevents_generation_and_keeps_owned_capture(tmp_path,operation_fails):
    gateway=UnifiedGateway();app=app_for(tmp_path,gateway)
    entered=threading.Event();release=threading.Event();original=gateway.freeze
    def blocked(family,policy):
        frozen=original(family,policy);entered.set();assert release.wait(5)
        if operation_fails:raise Failure('source_timeout',503)
        return frozen
    gateway.freeze=blocked
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client);created=submit(client,headers,'cancel-during-retrieval')
        fid=created['family']['answer_family_id'];job,thread=start_blocked(app,entered)
        try:
            other=login(client,'unified-other')
            assert client.post('/api/pilot/jobs/'+job['id']+'/cancel',json={},headers=other).status_code==404
            headers=login(client)
            cancelled=client.post('/api/pilot/jobs/'+job['id']+'/cancel',json={},headers=headers).json()
            assert cancelled['state']=='running'and cancelled['cancel_requested']
            assert cancelled['cancellation_mode']=='stop_before_generation'
            assert cancelled['provider_interruption_supported']is False
        finally:release.set();thread.join(5)
        assert not thread.is_alive()
        status=client.get('/api/pilot/jobs/'+job['id']).json()
        family=client.get('/api/pilot/families/'+fid).json()
        assert status['state']==family['short']['state']=='cancelled'
        assert family['short']['blocks']==[{'block_id':'attempt-status','text':'Zahteva je preklicana.'}] and family['short']['display_sha256']
        assert not any(path=='/internal/pilot/generate'for path,_,_ in gateway.calls)
        if operation_fails:
            assert status['error_code']=='source_timeout'
            assert family['matches_available']is False
        else:
            matches=client.get('/api/pilot/families/'+fid+'/matches').json()
            assert matches['usage_final']is True and matches['items']
            assert all(not row['used_in_answer']for row in matches['items'])
            path='/api/pilot/families/'+fid+'/sources/'+matches['items'][0]['evidence_id']
            gateway.blocked=True
            assert client.get(path).status_code==200
            assert client.get('/api/pilot/families/'+fid+'/sources/unknown').status_code==404
            login(client,'unified-other')
            assert client.get(path).status_code==404


def test_inflight_cancel_waits_without_starting_another_model_job_or_publishing_answer(tmp_path):
    gateway=UnifiedGateway();app=app_for(tmp_path,gateway)
    entered=threading.Event();release=threading.Event()
    def blocked(payload):entered.set();assert release.wait(5)
    gateway.on_generate=blocked
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client);first=submit(client,headers,'cancel-during-generation')
        fid=first['family']['answer_family_id'];job,thread=start_blocked(app,entered)
        try:
            running=client.get('/api/pilot/families/'+fid).json()
            assert running['short']['state']=='running'
            assert running['short']['progress']=={'stage':'generation','matches_ready':True}
            assert not running['short'].get('answer_md')and not running['short'].get('blocks')
            preview=client.get('/api/pilot/families/'+fid+'/matches?limit=2').json()
            assert preview['usage_final']is False and preview['next_offset']==2
            drawer='/api/pilot/families/'+fid+'/sources/'+preview['items'][0]['evidence_id']
            original=client.get(drawer).json()
            cancelled=client.post('/api/pilot/jobs/'+job['id']+'/cancel',json={},headers=headers).json()
            assert cancelled['state']=='running'and cancelled['cancellation_mode']=='finish_current_operation'
            other=login(client,'unified-other');second=submit(client,other,'other-job-after-cancellation')
            assert app.state.jobs.claim('model')is None,'In-flight provider still owns its bounded slot'
        finally:release.set();thread.join(5)
        assert not thread.is_alive()
        login(client)
        final=client.get('/api/pilot/families/'+fid).json()
        assert final['short']['state']=='cancelled' and final['short']['blocks']==[{'block_id':'attempt-status','text':'Zahteva je preklicana.'}]
        assert client.get(drawer).json()==original
        gateway.on_generate=None
        second_job=app.state.jobs.claim('model');assert second_job and second_job['id']==second['job']['job_id']
        app.state.jobs.execute(second_job)
        login(client,'unified-other')
        assert client.get('/api/pilot/families/'+second['family']['answer_family_id']).json()['short']['state']=='complete'
        assert len([path for path,_,_ in gateway.calls if path=='/internal/pilot/generate'])==2


def test_first_returned_match_observation_is_distinct_from_final_answer_and_display(tmp_path):
    gateway=UnifiedGateway();app=app_for(tmp_path,gateway)
    original=gateway.freeze
    def freeze(family,policy):
        frozen=original(family,policy)
        frozen['timings'].update(registry_ms=3,source_collection_ms=8,gateway_freeze_ms=2,evidence_preparation_ms=1)
        return frozen
    gateway.freeze=freeze
    with TestClient(app,base_url=ORIGIN)as client:
        created=submit(client,login(client),'match-readiness-observation')
        job=app.state.jobs.claim('model');app.state.jobs.execute(job)
        with app.state.store.connect()as db:
            result=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(job['run_id'],)).fetchone()[0])
        timing=result['timings']
        assert 0<=timing['first_returned_matches_ready_ms']<=timing['first_useful_validated_response_ms']
        assert timing['first_useful_display_ms']is None
        for key in ('registry_ms','source_collection_ms','gateway_freeze_ms','evidence_preparation_ms'):assert timing[key]>=0
        matches=client.get('/api/pilot/families/'+created['family']['answer_family_id']+'/matches').json()
        assert matches['usage_final']is True


@pytest.mark.parametrize('failure',['generation','render_validation'])
def test_failed_answer_keeps_the_same_owned_frozen_originals(tmp_path,failure):
    gateway=UnifiedGateway();app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        created=submit(client,login(client),'failed-answer-frozen-originals')
        fid=created['family']['answer_family_id'];saved={};generate=gateway.generate
        def fail_after_preview(bundle,tier,model,parent=None):
            saved['page']=client.get('/api/pilot/families/'+fid+'/matches').json()
            saved['route']='/api/pilot/families/'+fid+'/sources/'+saved['page']['items'][0]['evidence_id']
            response=client.get(saved['route']);assert response.status_code==200
            saved['original']=response.json()
            if failure=='generation':raise RuntimeError('Synthetic failed provider operation')
            result=generate(bundle,tier,model,parent)
            result['short_answer']='x'*100001
            return result
        gateway.generate=fail_after_preview
        job=app.state.jobs.claim('model');app.state.jobs.execute(job)
        family=client.get('/api/pilot/families/'+fid).json()
        assert family['short']['state']=='failed'and family['matches_available']is True
        assert family['short']['error_code']==('invalid_answer'if failure=='render_validation'else'answer_unavailable')
        assert family['short']['blocks'][0]['block_id']=='attempt-status' and family['short']['display_sha256']
        gateway.blocked=True
        page=client.get('/api/pilot/families/'+fid+'/matches').json()
        assert saved['page']['usage_final']is False and page['usage_final']is True
        assert page['items']==saved['page']['items']
        response=client.get(saved['route'])
        assert response.status_code==200 and response.json()==saved['original']
        assert client.get('/api/pilot/families/'+fid+'/sources/unknown').status_code==404
        login(client,'unified-other')
        assert client.get(saved['route']).status_code==404
        assert client.get('/api/pilot/families/'+fid+'/matches').status_code==404
