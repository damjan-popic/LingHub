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

def construction_alternatives_bundle(count):
 bundle=dictionary_alternatives_bundle();packets=bundle['gateway_bundle']['payload']['packets']
 ids=[f'proposal:homograph-{i}'for i in range(count)]
 family={'source_slug':'proposal-dictionary','target':'Example','query':'Example','passage_ids':ids,
         'introduced':True,'complete':False,'missing_passage_ids':ids[1:]}
 short=packets['short']['input_profile']['dictionary_alternatives']
 short.update(expected_passage_ids=[],missing_passage_ids=[],
              required_construction_passage_ids=['construction:rule'],families=[family])
 wide=packets['lexical_overview'];complete=copy.deepcopy(short);complete.update(complete=True)
 complete['families'][0].update(complete=True,missing_passage_ids=[])
 wide['input_profile']['dictionary_alternatives']=complete
 wide['model_evidence']=[{'passage_id':pid}for pid in ['construction:rule',*ids]]
 return bundle

@pytest.mark.parametrize('count',[2,3])
def test_construction_and_introduced_homograph_family_widen_together(count):
 bundle=construction_alternatives_bundle(count);before=copy.deepcopy(bundle)
 assert lexical.overview(bundle)is True and bundle==before

@pytest.mark.parametrize('damage',['lost_rule','different_rule','no_rule_proof','wrong_source','wrong_target',
                                  'wrong_query','different_family','missing_homograph','incomplete_family',
                                  'omitted_family','duplicate_family','not_introduced','pure_construction'])
def test_construction_widening_cannot_drop_rules_or_substitute_families(damage):
 bundle=construction_alternatives_bundle(3);packets=bundle['gateway_bundle']['payload']['packets']
 short=packets['short']['input_profile']['dictionary_alternatives']
 wide=packets['lexical_overview'];proof=wide['input_profile']['dictionary_alternatives']
 if damage=='lost_rule':wide['model_evidence']=wide['model_evidence'][1:]
 if damage=='different_rule':proof['required_construction_passage_ids']=['different:rule']
 if damage=='no_rule_proof':short['required_construction_passage_ids']=[]
 if damage=='wrong_source':proof['families'][0]['source_slug']='unrelated'
 if damage=='wrong_target':proof['families'][0]['target']='unrelated'
 if damage=='wrong_query':proof['families'][0]['query']='unrelated'
 if damage=='different_family':proof['families'][0]['passage_ids']=['other:a','other:b']
 if damage=='missing_homograph':wide['model_evidence'].pop()
 if damage=='incomplete_family':proof['families'][0]['complete']=False
 if damage=='omitted_family':proof['families']=[]
 if damage=='duplicate_family':proof['families']*=2
 if damage=='not_introduced':short['families'][0]['introduced']=False
 if damage=='pure_construction':short['families']=[];proof['families']=[]
 assert lexical.overview(bundle)is False

def test_pure_construction_keeps_existing_short_tier_without_dictionary_obligation():
 bundle=construction_alternatives_bundle(2);packets=bundle['gateway_bundle']['payload']['packets']
 packets['short']['input_profile']['dictionary_alternatives'].update(complete=True,families=[])
 assert lexical.overview(bundle)is False

def test_construction_widening_preserves_already_complete_other_family_too():
 bundle=construction_alternatives_bundle(2);packets=bundle['gateway_bundle']['payload']['packets']
 family={'source_slug':'other-dictionary','target':'Example','query':'Example','passage_ids':['other:a','other:b'],
         'introduced':True,'complete':True,'missing_passage_ids':[]}
 packets['short']['input_profile']['dictionary_alternatives']['families'].append(copy.deepcopy(family))
 wide=packets['lexical_overview']
 assert lexical.overview(bundle)is False
 wide['input_profile']['dictionary_alternatives']['families'].append(family)
 wide['model_evidence'].extend({'passage_id':x}for x in family['passage_ids'])
 assert lexical.overview(bundle)is True
