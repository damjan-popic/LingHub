"""Optional syntax shares the existing runner and preserves ordinary analysis."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from core import syntax_service as service


def document(text):
    word=SimpleNamespace(id=1,text=text,lemma='teči',upos='VERB',feats='VerbForm=Fin',head=0,deprel='root')
    token=SimpleNamespace(text=text,start_char=0,end_char=len(text),words=[word])
    return SimpleNamespace(sentences=[SimpleNamespace(words=[word],tokens=[token])])


class ExistingRunner:
    """The existing runner's one executor/admission contract, without models."""
    def __init__(self,pipeline,model,timeout=25,queue_limit=0):
        self.pipeline=pipeline;self.model=model;self.timeout=timeout
        self.slot=BoundedSemaphore(1+queue_limit)
        self.executor=ThreadPoolExecutor(max_workers=1)

    async def analyze(self,text):
        if not self.slot.acquire(blocking=False):raise HTTPException(503,detail={'code':'backend_busy'})
        future=asyncio.get_running_loop().run_in_executor(self.executor,self.pipeline,text)
        future.add_done_callback(lambda done:self.slot.release())
        return await asyncio.wait_for(asyncio.shield(future),self.timeout)

    def close(self):self.executor.shutdown(wait=True)


@pytest.fixture
def worker(monkeypatch):
    loads=[];dependencies=[]
    model={'processors':['tokenize','pos','lemma'],'fingerprint':'unchanged-original-identity'}
    diagnostics={'load_seconds':1.0}
    def load(device):
        loads.append(device);return document,model,diagnostics
    def dependency(*args):
        dependencies.append(args)
        return lambda doc:doc,service.model_metadata('observed-torch',['tokenize','pos','lemma','depparse'])
    monkeypatch.setattr(service,'_dependency_pipeline',dependency)
    monkeypatch.setitem(__import__('sys').modules,'torch',SimpleNamespace(load=lambda *a,**k:None))
    value=SimpleNamespace(app=FastAPI(),load_pipeline=load,BoundedRunner=ExistingRunner)
    service.install(value)
    yield value,loads,dependencies,model,diagnostics


def initialized(worker,**kwargs):
    value,*_=worker
    pipeline,model,_=value.load_pipeline('cpu')
    runner=value.BoundedRunner(pipeline,model,**kwargs)
    value.app.state.runner=runner
    return value,runner


def test_cached_dependency_uses_existing_base_doc_and_does_not_change_default_identity(worker):
    value,runner=initialized(worker)
    try:
        original_loader=value.load_pipeline
        service.install(value)
        assert value.load_pipeline is original_loader
        assert runner.model is worker[3] and runner.model['processors']==['tokenize','pos','lemma']
        assert len(worker[1])==len(worker[2])==1
        with TestClient(value.app) as client:
            health=client.get('/syntax/health').json()
            assert health['ready'] and health['model']['pipeline'][-1]=='depparse'
            for _ in range(2):
                response=client.post('/syntax',json={'text':'Teče'})
                assert response.status_code==200
                analysis=response.json()['analysis']
                assert analysis['text']=='Teče' and analysis['words'][0]['head_id']==0
                assert analysis['model']['resource_sha256']==service.MODEL_HASHES
            assert client.post('/syntax',json={'text':'Teče','other':'not-accepted'}).status_code==422
        assert len(worker[1])==len(worker[2])==1
    finally:runner.close()


def test_optional_dependency_failure_leaves_ordinary_pipeline_and_identity_available(worker,monkeypatch):
    def unavailable(*args):raise RuntimeError('private path details')
    monkeypatch.setattr(service,'_dependency_pipeline',unavailable)
    value,runner=initialized(worker)
    try:
        assert asyncio.run(runner.analyze('Teče')).sentences
        assert runner.model is worker[3]
        with TestClient(value.app) as client:
            assert client.get('/syntax/health').json()=={
                'status':'unavailable','ready':False,'model':None,'limits':{'max_text_chars':3000}}
            response=client.post('/syntax',json={'text':'Teče'})
            assert response.status_code==503 and 'private path'not in response.text
    finally:runner.close()


def test_ordinary_analysis_and_syntax_share_the_same_admission_and_executor(worker):
    value,runner=initialized(worker)
    entered=Event();release=Event()
    def blocked(text):entered.set();release.wait(2);return document(text)
    runner.pipeline=blocked
    async def scenario():
        ordinary=asyncio.create_task(runner.analyze('Teče'))
        try:
            for _ in range(100):
                if entered.is_set():break
                await asyncio.sleep(.005)
            assert entered.is_set()
            with pytest.raises(HTTPException)as error:await runner.syntax('Teče')
            assert error.value.detail['code']=='backend_busy'
        finally:release.set();await ordinary
    try:asyncio.run(scenario())
    finally:release.set();runner.close()


def test_timed_out_syntax_keeps_its_slot_until_existing_executor_finishes(worker):
    value,runner=initialized(worker,timeout=.02)
    release=Event()
    def blocked(text):release.wait(2);return document(text)
    runner.pipeline=blocked
    async def scenario():
        with pytest.raises(HTTPException)as error:await runner.syntax('Teče')
        assert error.value.status_code==504
        with pytest.raises(HTTPException)as busy:await runner.analyze('Teče')
        assert busy.value.detail['code']=='backend_busy'
        release.set()
        for _ in range(100):
            if runner.slot.acquire(blocking=False):
                runner.slot.release();break
            await asyncio.sleep(.005)
        else:pytest.fail('Completed work must release admission')
    try:asyncio.run(scenario())
    finally:release.set();runner.close()
