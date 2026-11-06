"""Ordinary owned question flow with real request interpretation and tool fixtures."""
import asyncio
from copy import deepcopy
import json
import sys
from pathlib import Path
import httpx
from fastapi.testclient import TestClient
from argon2 import PasswordHasher
import pytest
from test_pilot_unified import UnifiedGateway, ORIGIN, login
from test_comma_checker import response as checker_response
from core import comma_checker
from pilot_backend.api import create_app
from pilot_backend import comma_tasks
from tools.zrc_reference.request_interpretation import response as core_interpret

QUESTION='Preveri vejice: Ko pridem domov bom poklical.'

class CommaGateway(UnifiedGateway):
 def request(self,path,payload,base=None):
  if path=='/internal/pilot/interpret':
   self.calls.append((path,deepcopy(payload),base))
   return core_interpret(**payload)
  return super().request(path,payload,base)

def application(tmp_path,monkeypatch,status='ok'):
 gateway=CommaGateway();gateway.comma_checker={'enabled':True,'key_file':str(tmp_path/'key')}
 key=tmp_path/'key';key.write_text('synthetic-key');key.chmod(0o600)
 calls=[]
 actual_check=comma_checker.check
 async def check(text,config):
  calls.append(text)
  if status=='timeout':
   def handler(request):raise httpx.ReadTimeout('private upstream text')
  else:
   def handler(request):
    data=checker_response(text,text.replace('domov bom','domov, bom'))
    return httpx.Response(200,json=data)
  config=comma_checker.CommaCheckerConfig(key_file=key,transport=httpx.MockTransport(handler))
  return await actual_check(text,config)
 monkeypatch.setattr(comma_checker,'check',check)
 app=create_app({'store':str(tmp_path/'private/store.sqlite'),'allowed_origins':[ORIGIN],
  'secure_cookies':False,'tasks':['reference'],'models':['auto'],'unified_answers':True,
  'project_beta':True,'klikai_url':gateway.klikai_url,'comma_checker':gateway.comma_checker},
  adapter=gateway,hasher=PasswordHasher(time_cost=1,memory_cost=8,parallelism=1),start_workers=False)
 for name in ('unified-owner','unified-other'):
  app.state.auth.create(name,'synthetic-unified-test-password',origin='synthetic',must_change=False)
 return app,gateway,calls

def ask(app,client,headers,consent=False,key='comma-test-request',question=QUESTION):
 r=client.post('/api/pilot/families',headers=headers,json={'question':question,
   'idempotency_key':key,'comma_checker_consent':consent})
 assert r.status_code==200,r.text
 fid=r.json()['family']['answer_family_id']
 job=app.state.jobs.claim('cpu');assert job
 app.state.jobs.execute(job)
 result=client.get('/api/pilot/families/'+fid)
 assert result.status_code==200
 return fid,result.json()

def test_explicit_consent_saved_proposal_history_and_other_account(tmp_path,monkeypatch):
 app,gateway,calls=application(tmp_path,monkeypatch)
 # This ownership fixture has no supporting punctuation source. Make its
 # no-writer source-only outcome explicit; the real registry is tested separately.
 from pilot_backend import unified
 monkeypatch.setattr(unified,'generate',lambda *a,**k:{'answer_kind':'source_only',
     'execution':{'inference_performed':False}})
 with TestClient(app,base_url=ORIGIN)as client:
  headers=login(client)
  assert client.get('/api/pilot/session').json()['capabilities']['comma_checker']['configured']
  fid,missing=ask(app,client,headers)
  assert calls==[]
  assert missing['short']['comma_proposal']['status']=='consent_required'
  fid,family=ask(app,client,headers,True,'comma-consented-request')
  answer=family['short'];proposal=answer['comma_proposal']
  assert proposal['status']=='ok' and proposal['proposed_text']=='Ko pridem domov, bom poklical.'
  assert proposal['edits'][0]['start']==proposal['edits'][0]['end']==15
  assert proposal['edits'][0]['kind']=='insert'
  assert answer['tool_execution']['attempted'] is True
  assert answer['tool_execution']['neural_prediction_received'] is True
  assert answer['tool_execution']['writer_inference_performed'] is False
  assert answer['tool_execution']['inference_performed'] is None
  assert calls==['Ko pridem domov bom poklical.']
  assert 'raw_result'not in json.dumps(family) and 'synthetic-key'not in json.dumps(family)
  assert client.get('/api/pilot/families/'+fid).json()==family and len(calls)==1
  matches=client.get('/api/pilot/families/'+fid+'/matches')
  assert matches.status_code==200 and isinstance(matches.json()['items'],list) and len(calls)==1
  login(client,'unified-other')
  assert client.get('/api/pilot/families/'+fid).status_code==404
  assert not any(path=='/internal/pilot/generate'for path,_,_ in gateway.calls)

def test_provider_failure_and_no_target_are_saved_local_results(tmp_path,monkeypatch):
 app,gateway,calls=application(tmp_path,monkeypatch,'timeout')
 with TestClient(app,base_url=ORIGIN)as client:
  headers=login(client)
  _,family=ask(app,client,headers,True)
  assert family['short']['comma_proposal']['status']=='timeout'
  assert family['short']['comma_proposal']['original_text']=='Ko pridem domov bom poklical.'
  assert len(calls)==1
  _,family=ask(app,client,headers,True,'comma-no-target-request','Preveri vejice v dokumentu.')
  assert family['short']['comma_proposal']['status']=='target_required' and len(calls)==1
  assert client.get('/health').status_code==200

def test_general_rule_does_not_call_checker(tmp_path,monkeypatch):
 app,gateway,calls=application(tmp_path,monkeypatch)
 family={'question':'Ali pred kot vedno pišemo vejico?','context':'','locale':'sl','input_language':'und'}
 assert comma_tasks.freeze(gateway,family,{}) is None
 assert calls==[]


def test_owned_why_followup_reuses_prediction_without_another_checker_call(tmp_path,monkeypatch):
 app,gateway,calls=application(tmp_path,monkeypatch)
 from pilot_backend import unified
 monkeypatch.setattr(unified,'freeze',lambda *args:{'bundle':{'contract':'fixture-explanation'}})
 monkeypatch.setattr(unified,'generate',lambda *args:{'answer_kind':'source_only',
   'execution':{'provider_scope':'none','inference_performed':False},'provider_attempts':[]})
 with TestClient(app,base_url=ORIGIN)as client:
  headers=login(client)
  _,parent=ask(app,client,headers,True)
  parent_run=parent['short']['run_id']
  r=client.post('/api/pilot/families',headers=headers,json={
   'question':'Zakaj si tu dodal vejico?','idempotency_key':'comma-owned-why-001',
   'parent_run_id':parent_run,'parent_relation':'followup','comma_checker_consent':False})
  assert r.status_code==200,r.text
  fid=r.json()['family']['answer_family_id']
  job=app.state.jobs.claim('cpu');assert job
  app.state.jobs.execute(job)
  current=client.get('/api/pilot/families/'+fid).json()
  assert current['short']['comma_proposal']['proposed_text']=='Ko pridem domov, bom poklical.'
  assert current['short']['tool_execution']['observation_reused'] is True
  assert current['short']['tool_execution']['attempted'] is False
  assert current['context']=='Ko pridem domov bom poklical.'
  assert calls==['Ko pridem domov bom poklical.']
  login(client,'unified-other')
  r=client.post('/api/pilot/families',headers=login(client,'unified-other'),json={
   'question':'Zakaj si tu dodal vejico?','idempotency_key':'comma-other-why-001',
   'parent_run_id':parent_run,'parent_relation':'followup'})
  assert r.status_code==404
  assert len(calls)==1


def test_explanation_uses_existing_answer_allowance_and_preserves_unsuccessful_writer_attempt(tmp_path,monkeypatch):
 app,gateway,calls=application(tmp_path,monkeypatch)
 from pilot_backend import unified
 tiers=[]
 monkeypatch.setattr(unified,'freeze',lambda *args:{'bundle':{'contract':'fixture-explanation'}})
 def generate(gateway,bundle,tier,*args):
  tiers.append(tier)
  return {'answer_kind':'source_only','execution':{'inference_performed':True,'provider_scope':'shared'},
      'provider_attempts':[{'status':'completed','model':'synthetic-writer'}],
      'usage':{'prompt_tokens':12,'completion_tokens':3},'model_identity':{'model_id':'synthetic-writer'}}
 monkeypatch.setattr(unified,'generate',generate)
 with TestClient(app,base_url=ORIGIN)as client:
  headers=login(client);fid,current=ask(app,client,headers,True)
  assert tiers==['answer']
  with app.state.store.connect()as db:
   run=db.execute('SELECT result FROM runs WHERE id=?',(current['short']['run_id'],)).fetchone()
  result=json.loads(run['result'])
  assert result['answer_kind']=='tool_prediction'
  assert result['tool_execution']['writer_inference_performed'] is True
  assert result['provider_attempts']==[{'status':'completed','model':'synthetic-writer'}]
  assert result['model_identity']['model_id']=='synthetic-writer'
  assert result['usage']['completion_tokens']==3
  assert result['deterministic_result']['source_explanation_result']['execution']['inference_performed'] is True


def test_unchanged_prediction_has_a_local_limit_without_inventing_a_change(tmp_path,monkeypatch):
 app,gateway,calls=application(tmp_path,monkeypatch)
 from pilot_backend import unified
 monkeypatch.setattr(unified,'freeze',lambda *args:{'bundle':{'contract':'fixture-explanation'}})
 monkeypatch.setattr(unified,'generate',lambda *args:{'answer_kind':'source_only',
     'execution':{'inference_performed':False}})
 with TestClient(app,base_url=ORIGIN)as client:
  headers=login(client);_,current=ask(app,client,headers,True,question='Preveri vejice: Teče kot veter.')
  answer=current['short']
  assert answer['comma_proposal']['edits']==[]
  assert answer['comma_proposal']['proposed_text']=='Teče kot veter.'
  assert answer['answer_md'].startswith('Za ta primer')
  assert 'spremembo' not in answer['answer_md'] and 'poseg' not in answer['answer_md']
