"""Lexical integration boundaries; linguistic examples are retained-source fixtures."""
import copy,hashlib,json,os
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pilot_backend import lexical,linghub_api,collation
from pilot_backend.store import digest

def test_scoped_api_revocation_and_closed_routes(tmp_path):
 secret='x'*40;path=tmp_path/'clients.json'
 clients={'clients':{'reader':{'secret_sha256':hashlib.sha256(secret.encode()).hexdigest(),'scopes':['read'],'profiles':['stable']}}}
 path.write_text(json.dumps(clients));path.chmod(0o600)
 app=FastAPI();linghub_api.install(app,{'linghub_clients_file':str(path),'linghub_url':'http://127.0.0.1:1'})
 with TestClient(app)as client:
  headers={'Authorization':'Bearer reader.'+secret}
  assert client.get('/api/linghub/v1/openapi.json').status_code==401
  assert client.get('/api/linghub/v1/openapi.json',headers=headers).status_code==200
  assert client.post('/api/linghub/v1/profile',json={'text':'private submitted text'},headers=headers).status_code==403
  assert client.get('/api/linghub/v1/internal/models',headers=headers).status_code==404
  clients['clients']['reader']['revoked']=True;path.write_text(json.dumps(clients))
  assert client.get('/api/linghub/v1/openapi.json',headers=headers).status_code==401

def test_real_frozen_parallel_forms_and_no_writer(tmp_path):
 root=os.environ.get('LINGHUB_LEXICAL_TEST_RESULTS')
 if not root:pytest.skip('Set retained local profile evidence directory for source-backed integration')
 p=json.loads((Path(root)/'Luka.json').read_text())
 bundle={'locale':'sl','analysis':{'version':'klikai.lexical-collation.v1','captures':[{'response':p}]}}
 before=copy.deepcopy(bundle);view=lexical.reader(bundle);assert bundle==before
 masculine=next(r for r in view['sections'][0]['items']if r['title']=='Luka · lastno ime, moški spol')
 gen=next(r for r in masculine['forms']if r['title'].startswith('rodilnik · ednina'))
 nom=next(r for r in masculine['forms']if r['title'].startswith('imenovalnik · ednina'))
 assert gen['text']=='Luka / Luke'and nom['text']=='Luka'
 assert any(r['title'].startswith('luka · občno ime')for r in view['sections'][0]['items'])
 frozen=json.loads(json.dumps(bundle));assert lexical.reader(frozen)==view

def test_client_legacy_profile_does_not_probe_experimental():
 class Old:
  linghub_url='http://127.0.0.1:1'
  def request(self,*a):raise AssertionError('Unselected feature must not call a service')
 assert collation.collect(Old(),{'question':'https://example.test/123'},automatic=True)is None

@pytest.mark.parametrize('basis,expected',[
 ('degraded lexical candidate; source match required',False),
 ('analyzer content span',False),('single lexical surface',True),
 ('requested lexical object',True)])
def test_component_nominations_do_not_turn_a_broad_question_into_a_lexical_overview(basis,expected):
 p={'interpretation':{'query_kind':'comparison','requested_facets':[],
     'targets':[{'text':'seznam','basis':basis}]},
    'evidence':[{'kind':kind,'match_relation':'exact'}for kind in ('morphology','terminology')]}
 bundle={'analysis':{'version':'klikai.lexical-collation.v1','captures':[{'response':p}]}}
 before=copy.deepcopy(bundle)
 assert lexical.overview(bundle)is expected and bundle==before

def dictionary_alternatives_bundle():
 p={'interpretation':{'query_kind':'question','requested_facets':['morphology'],
     'targets':[{'text':'Example','basis':'named lexical object; descriptor context retained'}]},
    'evidence':[{'kind':'morphology','match_relation':'recorded_target'}]}
 expected=['current:entry','proposal:homograph-a','proposal:homograph-b']
 return {'analysis':{'version':'klikai.lexical-collation.v1','captures':[{'response':p}]},
  'gateway_bundle':{'payload':{'packets':{
   'short':{'input_profile':{'dictionary_alternatives':{'expected_passage_ids':expected,
       'missing_passage_ids':['proposal:homograph-b'],'complete':False}}},
   'lexical_overview':{'input_profile':{'dictionary_alternatives':{'expected_passage_ids':list(expected),
       'missing_passage_ids':[],'complete':True}},'model_evidence':[{'passage_id':x}for x in expected]}}}}}

def test_practical_question_uses_existing_wider_tier_for_complete_source_alternatives():
 bundle=dictionary_alternatives_bundle();before=copy.deepcopy(bundle)
 assert lexical.overview(bundle)is True and bundle==before

@pytest.mark.parametrize('damage',['different_set','incomplete','missing','unprovided','translation',
                                  'orthography','broad_analyzer_target','source_view','empty_expected'])
def test_dictionary_overview_never_replaces_incomplete_or_unrelated_evidence(damage):
 bundle=dictionary_alternatives_bundle();packets=bundle['gateway_bundle']['payload']['packets']
 wide=packets['lexical_overview'];proof=wide['input_profile']['dictionary_alternatives']
 interpretation=bundle['analysis']['captures'][0]['response']['interpretation']
 if damage=='different_set':proof['expected_passage_ids']=['unrelated']
 if damage=='incomplete':proof['complete']=False
 if damage=='missing':proof['missing_passage_ids']=['proposal:homograph-b']
 if damage=='unprovided':wide['model_evidence'].pop()
 if damage in {'translation','orthography'}:interpretation['requested_facets'].append(damage)
 if damage=='broad_analyzer_target':interpretation['targets'][0]['basis']='analyzer content span'
 if damage=='source_view':wide['source_view_reason']='complete_dictionary_alternatives_exceed_selected_input'
 if damage=='empty_expected':packets['short']['input_profile']['dictionary_alternatives']['expected_passage_ids']=[]
 assert lexical.overview(bundle)is False
