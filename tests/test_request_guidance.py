"""Guidance records use owned sources and never call a writer or account history."""
import json
import pytest
from fastapi.testclient import TestClient
from pilot_backend import request_tasks as tasks
from pilot_backend.adapter import Gateway
from test_pilot_unified import app_for, login, ORIGIN

@pytest.mark.parametrize('q,key',[
 ('Kje najdem obrazec za samoizjavo?','forms'),('Kje so obrazci za samoizjavo?','forms'),
 ('Iščem bančni slovarček ma ga ne najdem. Kje je?','bank'),
 ('Kje najdem Informascuole v slovenščini?','school'),
 ('Kje dobim italijansko ustavo v slovenščini?','constitution'),
 ('Ali lahko prenesem zvezek na svoj računalnik?','zvezek'),('zvezek ne dela','zvezek')])
def test_verified_navigation(q,key):
 decision=tasks.interpret(q)
 assert decision['resources'][0]['key']==key
 assert decision['resources'][0]['url']==tasks.RESOURCES[key][1]
 assert 'offline' not in decision['resources'][0]['capability']

@pytest.mark.parametrize('q', ['Kaj pomeni samoizjava?', 'Kako prevedemo sportello?', 'Kaj pomeni bančni račun?', 'Zvezek ali zvezka?', 'Kaj pomeni tožba?',
 'Iščem sopomenko za obrazec.', 'Kje v stavku uporabim zvezek?', 'Iščem prevod za samoizjavo.',
 'Kje se uporablja beseda sportello?', 'Dove trovo un sinonimo di modulo?',
 'Kje je moj zvezek?', 'Kje kupim zvezek?', 'Kje se ustavi vlak?', 'Kje dobim brezvezek?'])
def test_navigation_does_not_replace_language_question(q):
 assert tasks.interpret(q) is None

def test_scope_missing_and_unknown_resource_never_invent_url():
 assert tasks.interpret('Kje dobim ustavo v slovenščini?')['kind']=='resource_scope_required'
 assert tasks.interpret('Kje najdem slovar neznane ustanove?')['resources']==[]
 assert tasks.interpret('a se to tudi v Sloveniji uporablja?')['kind']=='missing_referent'
 assert tasks.interpret('a se to tudi v Sloveniji uporablja?', 'Izraz SCIA') is None
 assert tasks.interpret('Ku moram oddat davčno prijavo, na kjeri sportello se moram oglasit v Duinu?')['kind']=='service_location_unverified'
 assert tasks.interpret('Kje oddam obrazec za samoizjavo?')['kind']=='service_location_unverified'
 assert tasks.interpret('Kje najdem slovensko ustavo v italijanščini?')['resources']==[]
 assert tasks.interpret('Dove trovo la costituzione slovena in italiano?')['resources']==[]
 assert tasks.interpret('Kje dobim prevod italijanske ustave v slovenščini?')['resources'][0]['key']=='constitution'
 assert tasks.interpret('Obrazci ne delujejo, kje jih najdem?')['kind']=='source_guidance'
 assert tasks.interpret('a se to tudi v Sloveniji uporablja?', 'Izraza »gorska pot« in »gozdna pot«.')['kind']=='missing_referent'

class NoNetwork(Gateway):
 def __init__(self):super().__init__('http://127.0.0.1:18320','synthetic-token-'*4,klikai_url='http://127.0.0.1:8000',unified_answers=True)
 def request(self,*args,**kwargs):raise AssertionError('Guidance must not contact any upstream')
 def require_generation(self,*args):raise AssertionError('No writer capacity required')

def test_owned_guidance_pipeline_sources_and_trace(tmp_path):
 gateway=NoNetwork(); app=app_for(tmp_path,gateway)
 with TestClient(app,base_url=ORIGIN) as c:
  h=login(c)
  r=c.post('/api/pilot/families',headers=h,json={'question':'Kje najdem obrazec za samoizjavo?','idempotency_key':'guidance-00001'})
  assert r.status_code==200,r.text
  fid=r.json()['family']['answer_family_id']
  comparison=c.post('/api/pilot/comparisons',headers=h,json={'family_id':fid,'idempotency_key':'guidance-compare-001'})
  assert comparison.status_code==409 and comparison.json()['error']['code']=='comparison_unavailable'
  assert app.state.jobs.claim('model') is None
  job=app.state.jobs.claim('cpu');assert job;app.state.jobs.execute(job)
  family=c.get('/api/pilot/families/'+fid).json()
  assert family['short']['state']=='complete',family
  assert family['short']['generation_status']=='not_generated'
  assert family['matches_available']
  expansion=c.post('/api/pilot/families/'+fid+'/expand',headers=h,json={'idempotency_key':'guidance-expand-001'})
  assert expansion.status_code==409 and expansion.json()['error']['code']=='expansion_unavailable'
  assert family['short']['answer_method']=='deterministic'
  assert family['short']['evidence'][0]['local_number']==1
  eid=family['short']['evidence'][0]['evidence_id']
  source=c.get('/api/pilot/families/'+fid+'/sources/'+eid+'?tier=short')
  assert source.status_code==200,source.text
  assert source.json()['url']==tasks.RESOURCES['forms'][1]
  assert c.get('/api/pilot/families/'+fid+'/sources').json()['returned_count']==1
  records=c.get('/api/pilot/families/'+fid+'/matches?source=jezik_na_klik&limit=1').json()
  assert records['items'][0]['evidence_ids']==[eid] and records['items'][0]['snippet']==tasks.RESOURCES['forms'][1]
  with app.state.store.connect() as db:
   captured=db.execute('SELECT bundle FROM families WHERE id=?',(fid,)).fetchone()[0]
  assert tasks.RESOURCES['forms'][1] in captured
  login(c,'unified-other')
  assert c.get('/api/pilot/families/'+fid+'/sources/'+eid+'?tier=short').status_code==404

def test_followup_binding_requires_explicit_owned_parent(tmp_path):
 gateway=NoNetwork();app=app_for(tmp_path,gateway)
 with TestClient(app,base_url=ORIGIN) as c:
  h=login(c)
  parent=c.post('/api/pilot/families',headers=h,json={'question':'Kaj pomeni SCIA?','idempotency_key':'parent-00001'}).json()
  rid=parent['family']['short']['run_id']
  q='a se to tudi v Sloveniji uporablja?'
  unbound=c.post('/api/pilot/families',headers=h,json={'question':q,'idempotency_key':'unbound-00001'}).json()
  job=app.state.jobs.claim('cpu');assert job;app.state.jobs.execute(job)
  family=c.get('/api/pilot/families/'+unbound['family']['answer_family_id']).json()
  assert 'Na kateri izraz' in family['short']['short_answer']
  assert family['short']['answer_method']=='clarification_required'
  bound=c.post('/api/pilot/families',headers=h,json={'question':q,'idempotency_key':'bound-00001','parent_run_id':rid})
  assert bound.status_code==200,bound.text
  with app.state.store.connect() as db:
   row=db.execute('SELECT question,context FROM families WHERE id=?',(bound.json()['family']['answer_family_id'],)).fetchone()
  assert row['question']==q and row['context']=='Kaj pomeni SCIA?'
  explicit=c.post('/api/pilot/families',headers=h,json={'question':q,'context':'Izraz gorska pot','idempotency_key':'explicit-bound-00001','parent_run_id':rid})
  assert explicit.status_code==200,explicit.text
  with app.state.store.connect() as db:
   assert db.execute('SELECT context FROM families WHERE id=?',(explicit.json()['family']['answer_family_id'],)).fetchone()[0]=='Izraz gorska pot'
  login(c,'unified-other');other=login(c,'unified-other')
  denied=c.post('/api/pilot/families',headers=other,json={'question':q,'idempotency_key':'other-bound-0001','parent_run_id':rid})
  assert denied.status_code==404
