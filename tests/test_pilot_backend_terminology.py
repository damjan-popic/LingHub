"""Synthetic records exercise exact admission and immutable owned CPU results."""
import json
from copy import deepcopy
from urllib.parse import parse_qs,urlsplit
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pilot_backend.adapter import Gateway
from pilot_backend.api import create_app
from pilot_backend.security import Failure
from pilot_backend.terminology import freeze_lookup,answer,source
from pilot_backend.store import Store,BASE_POLICY,canonical,digest,stamp
from pilot_backend.domain import Domain
from pilot_backend.security import Auth

def hit(query='učiteljskemu zboru',term='učiteljski zbor',tier='morphology_exact',field='standardized',cost=0):
    return {'source_slug':'fixture','source_display_name':'Synthetic terminology','source_badge':'TEST','rights_mode':'restricted','trust_tier':1,'external_id':'fixture:record-1','citation_id':'fixture:record-1','upstream_key':'record-1','record_kind':'terminology_decision','source_date':'2026-01-01','title':'original → equivalent','categories':['Domain A'],'subcategories':['Sense A'],'original_terms':['original A'],'standardized_terms':['equivalent A','equivalent B'],'proposals':['proposal only'],'rationale':['Exact original qualification.'],'examples':[],'references':[],'snippet':'Original supplied snippet.','matched_forms':[{'term':term,'match_tier':tier,'field':field,'language':'sl','ambiguity_cost':cost,'matched_query':query,'query_signature':term,'lexicon_release_id':'fixture-lexicon-v1'}]}

def lookup(query,items):return {'query':query,'sources':['fixture'],'count':len(items),'hits':items}

def test_whole_query_exactness_rejects_contained_morphology_and_proposals():
    q='učiteljskemu zboru';bad=[hit(query='zboru',term='zbor'),hit(tier='partial'),hit(tier='tokenized_exact'),hit(field='proposals'),hit(cost=1)]
    result=freeze_lookup(lookup(q,[hit(),*bad]),q,'sl');assert len(result['bundle']['hits'])==1
    assert result['identity']['lexicon_release_ids']==['fixture-lexicon-v1']
    exact=freeze_lookup(lookup('BES',[hit('BES','BES','exact','original')]),'BES','it');assert len(exact['bundle']['hits'])==1

def test_records_senses_equivalents_and_originals_remain_separate():
    q='učiteljskemu zboru';a=hit();b=hit();b.update(external_id='fixture:record-2',citation_id='fixture:record-2',categories=['Domain B'],subcategories=['Sense B'],standardized_terms=['other equivalent'])
    raw=lookup(q,[a,b]);before=deepcopy(raw);bundle=freeze_lookup(raw,q,'it')['bundle'];result=answer(bundle,'short')
    assert raw==before;assert result['deterministic_result']['records']==bundle['hits'];assert len(result['evidence'])==2
    for s in ['Domain A','Sense A','Domain B','Sense B','equivalent A','equivalent B','other equivalent','Exact original qualification.']:assert s in result['answer_md']
    assert result['answer_kind']=='deterministic'and result['generation_status']=='not_generated'
    original=deepcopy(bundle);first=source(bundle,result['evidence'][0]['evidence_id'],'short');assert 'Domain A'in first['excerpt']and 'Domain B'not in first['excerpt'];assert bundle==original
    raw['hits'][0]['standardized_terms']=['live source changed'];assert source(bundle,result['evidence'][0]['evidence_id'],'short')==first
    with pytest.raises(Failure):source(bundle,'unknown','short')
    bundle['hits'][0]['title']='tampered'
    with pytest.raises(Failure):answer(bundle,'short')

def test_no_hit_never_creates_definition_or_sources():
    b=freeze_lookup(lookup('qzxqzx',[]),'qzxqzx','it')['bundle'];r=answer(b,'short')
    assert r['status']=='no_hit'and r['evidence']==[]and r['source_badges']==[]
    assert 'Nessuna corrispondenza'in r['short_answer']
    with pytest.raises(Failure):answer(b,'long')

def test_same_external_id_in_different_sources_has_distinct_frozen_drawers():
    a=hit();b=hit();b.update(source_slug='second',source_display_name='Second source',categories=['Different domain'],standardized_terms=['different equivalent'])
    data=lookup('učiteljskemu zboru',[a,b]);data['sources'].append('second')
    bundle=freeze_lookup(data,'učiteljskemu zboru','sl')['bundle'];result=answer(bundle,'short');ids=[e['evidence_id']for e in result['evidence']]
    assert len(set(ids))==2
    assert source(bundle,ids[0],'short')['source_label']=='Synthetic terminology'
    second=source(bundle,ids[1],'short');assert second['source_label']=='Second source'and'different equivalent'in second['excerpt']

class ReadOnlyGateway(Gateway):
    def __init__(self):super().__init__('http://127.0.0.1:18320','x'*40,'http://127.0.0.1:8010','http://127.0.0.1:8000');self.calls=[];self.current=hit()
    def request(self,path,payload,base=None):
        self.calls.append((path,deepcopy(payload),base))
        if path.startswith('/api/terminology/lookup?'):
            assert payload is None and base==self.klikai_url
            params=parse_qs(urlsplit(path).query);assert params['allow_partial']==['false']and params['include_prose']==['false']and params['use_morphology']==['true']
            q=params['q'][0];return lookup(q,[self.current])
        if path=='/v1/capabilities':return {'identity':'fixture-capability-v1','analyzer_service_reachable':True,'defaults':{'sl':'spacy','it':'spacy'},'resources':[],'models':{'sl':'exact-model-sl','it':'exact-model-it'}}
        if path=='/v1/analyze':return {'text':payload['text'],'lang':payload['lang'],'engine_used':'spacy','offset_convention':'unicode_codepoint_half_open','issues':[],'spans':[]}
        raise AssertionError('Private model gateway must not be called')

def test_text_language_and_output_locale_are_independent_and_required():
    adapter=ReadOnlyGateway();f={'task':'text_check','question':'Slovensko besedilo.','input_language':'sl','locale':'it'}
    frozen=adapter.freeze(f,{});assert frozen['bundle']['lang']=='sl'and frozen['bundle']['output_locale']=='it'
    result=adapter.generate(frozen['bundle'],'short','gemma');assert result['short_answer'].startswith('Le regole')and result['deterministic_result']['lang']=='sl'
    assert next(p for route,p,_ in adapter.calls if route=='/v1/analyze')['lang']=='sl'
    for value in [None,'und','mixed','fr']:
        with pytest.raises(Failure):adapter.freeze({**f,'input_language':value},{})

def test_authenticated_cpu_mode_sources_survive_live_change_and_other_account_is_denied(tmp_path):
    adapter=ReadOnlyGateway();config={'store':str(tmp_path/'private'/'pilot.sqlite'),'allowed_origins':['http://127.0.0.1:18310'],'secure_cookies':False,'tasks':['reference','terminology','text_check']}
    app=create_app(config,adapter=adapter,hasher=PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1),start_workers=False)
    password='synthetic long test password'
    for name in ['owner','other']:app.state.auth.create(name,password,origin='synthetic',must_change=False)
    def login(c,name):
        response=c.post('/api/pilot/login',json={'username':name,'password':password},headers={'Origin':'http://127.0.0.1:18310'});assert response.status_code==200;return {'Origin':'http://127.0.0.1:18310','X-CSRF-Token':response.json()['csrf_token']}
    with TestClient(app,base_url='http://127.0.0.1:18310')as client:
        headers=login(client,'owner');response=client.post('/api/pilot/families',json={'question':'učiteljskemu zboru','task':'terminology','locale':'it','model':'gemma','idempotency_key':'term-example-001'},headers=headers);assert response.status_code==200
        fid=response.json()['family']['answer_family_id'];job=app.state.jobs.claim('cpu');assert job and job['model']=='cpu';assert app.state.jobs.claim('model')is None;app.state.jobs.execute(job)
        family=client.get('/api/pilot/families/'+fid).json();assert family['short']['state']=='complete';eid=family['short']['evidence'][0]['evidence_id'];route='/api/pilot/families/'+fid+'/sources/'+eid
        first=client.get(route);assert first.status_code==200;adapter.current['title']='changed live record';assert client.get(route).json()==first.json()
        assert client.post('/api/pilot/families/'+fid+'/expand',json={'idempotency_key':'no-term-expand'},headers=headers).status_code==409
        assert client.post('/api/pilot/comparisons',json={'family_id':fid,'idempotency_key':'no-term-comparison'},headers=headers).status_code==400
        bad=client.post('/api/pilot/families',json={'question':'Slovensko.','task':'text_check','locale':'it','idempotency_key':'text-lang-required'},headers=headers);assert bad.status_code==400 and bad.json()['error']['code']=='input_language_required'
        login(client,'other');assert client.get(route).status_code==404;assert client.get('/api/pilot/families/'+fid).status_code==404
        assert len(adapter.calls)==1

def test_non_reference_families_keep_baseline_even_for_canary_selected_owner(tmp_path):
    store=Store(tmp_path/'private'/'pilot.sqlite');auth=Auth(store,PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1));owner=auth.create('synthetic-canary-owner','long synthetic test password',origin='synthetic',must_change=False)
    domain=Domain(store,tasks=('reference','source_search','text_check','terminology'))
    baseline=store.meta('active_policy');candidate={**BASE_POLICY,'context_passage_limit':12};pid=digest(candidate)
    canary=next('fixture-canary-'+str(i)for i in range(1000)if int(digest([owner,'fixture-canary-'+str(i)])[:8],16)%10==0)
    with store.tx()as db:
        db.execute('INSERT INTO policies VALUES(?,?,?,?,?)',(pid,canonical(candidate),baseline,stamp(),'candidate'))
        db.execute('INSERT INTO cycles VALUES(?,?,?,?,?,?,?,?,?)',('fixture-cycle','adaptation',0,0,'synthetic-hash','canary','{}',stamp(),None))
        db.execute('INSERT INTO canaries(id,cycle_id,baseline,candidate,state,created,expires)VALUES(?,?,?,?,?,?,?)',(canary,'fixture-cycle',baseline,pid,'running',stamp(),stamp()+3600))
        assert domain.select_policy(db,owner)==pid
    for task in ('source_search','text_check','terminology','reference'):
        created=domain.create_family(owner,{'question':'Synthetic term','task':task,'input_language':'sl','locale':'it','idempotency_key':'canary-routing-'+task})
        with store.connect()as db:actual=db.execute('SELECT policy_id FROM families WHERE id=?',(created['family']['answer_family_id'],)).fetchone()[0]
        assert actual==(pid if task=='reference'else baseline),task
