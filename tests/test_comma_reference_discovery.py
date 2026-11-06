"""Explain-only reference expansion uses CORE's plan and existing registry."""
from concurrent.futures import Future
from copy import deepcopy
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from pilot_backend import unified
from pilot_backend.store import digest
from pilot_backend.security import Failure


QUESTION='Preveri vejice in pojasni: Pozimi in kadar je mraz moramo zakuriti.'
HEADINGS=('Med nadrednim in odvisnim stavkom', 'Nestavčno-stavčni enakovredni deli')


class ImmediatePool:
    def submit(self, function, *args, **kwargs):
        result=Future()
        try:result.set_result(function(*args,**kwargs))
        except BaseException as error:result.set_exception(error)
        return result


class RegistryGateway:
    klikai_url='http://registry.invalid'
    def __init__(self, *, sp_active=True, adapter='official_reference_jsonl'):
        self.rows=[{'slug':'sp2001','display_name':'Slovenski pravopis 2001',
            'adapter_key':adapter,'rights_mode':'owned','source_type':'owned',
            'is_active':sp_active,'root_path':'PRIVATE_NOT_NEEDED'},
            {'slug':'pravopis8','display_name':'Pravopis 8.0','adapter_key':'official_reference_jsonl',
             'rights_mode':'owned','source_type':'owned','is_active':True}]
    def request(self,path,payload,base=None):
        assert path=='/api/sources'and payload is None and base==self.klikai_url
        return {'sources':deepcopy(self.rows)}


@pytest.fixture
def interpreted(monkeypatch):
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')
    if not configured:pytest.skip('This integration test needs the existing pinned CORE tree')
    root=Path(configured)
    monkeypatch.syspath_prepend(str(root))
    from tools.zrc_reference.request_interpretation import response
    def family(question=QUESTION):
        plan=response(question,'','sl','sl')['request_plan']
        return {'question':question,'original_question':question,'context':'',
                'locale':'sl','input_language':'sl','task':'reference','request_plan':plan}
    return family


def captured(identity,heading):
    capture={'resource_id':identity,'source_slug':'sp2001','title':heading,
        'original_text':heading+'\n\nComplete retained governing rule.',
        'normative_status':'current_reference','rights_mode':'owned',
        'source_status':'SP 2001','chunk_kind':'official_reference'}
    return {'source_slug':'sp2001','external_id':identity,'capture_kind':'resource_record',
            'capture':capture,'capture_sha256':digest(capture),'title':heading,'score':1.0}


def coverage(question):
    return [{'family':'sp2001','source_label':'Slovenski pravopis 2001','status':'ok',
        'returned_count':1,'limit':10,'has_more':None,'elapsed_ms':3.0,'search_ms':2.0,
        'detail_ms':0.0,'detail_requests':0,'detail_reused':0,'details':[],
        'query':question,'query_variants':[question],'original_question':question}]


def collector(monkeypatch, expected_details):
    calls=[]
    def collect(gateway,family,definition,detail_pool=None,*,query_override=None,lemma_candidate=None):
        assert detail_pool is expected_details and lemma_candidate is None
        assert definition['slug']=='sp2001'and definition['adapter_key']=='official_reference_jsonl'
        assert 'root_path'not in definition
        calls.append({'question':family['question'],'original_question':family['original_question'],
            'plan':deepcopy(family['request_plan']),'query':query_override})
        index=HEADINGS.index(query_override)
        rows=[captured('main-boundary'if index==0 else'equivalent-parts',query_override)]
        extra={'family':'sp2001','source_label':'Slovenski pravopis 2001','status':'ok',
            'returned_count':1,'limit':10,'has_more':None,'elapsed_ms':7.0 if index==0 else 13.0,
            'search_ms':5.0 if index==0 else 11.0,'detail_ms':0.0,'detail_requests':0,
            'query':query_override,'query_variants':[query_override],
            'original_question':family['question'],'candidates':[]}
        return rows,extra
    monkeypatch.setattr(unified,'collect_source',collect)
    return calls


def test_both_authoritative_heading_queries_run_despite_a_surface_hit(interpreted,monkeypatch):
    family=interpreted();before=deepcopy(family)
    queries=family['request_plan']['comma_check']['reference_queries']
    assert queries==[{'source_slug':'sp2001','query':value}for value in HEADINGS]
    gateway=RegistryGateway();definitions=unified.registry(gateway)
    found=[captured('equivalent-parts',HEADINGS[1])]
    initial_capture=deepcopy(found[0]);status=coverage(family['question'])
    details=object();calls=collector(monkeypatch,details)
    unified.expand_request_reference_rules(gateway,family,definitions,found,status,details,ImmediatePool())
    assert [row['query']for row in calls]==list(HEADINGS)
    assert all(row['question']==QUESTION and row['original_question']==QUESTION
               and row['plan']==before['request_plan']for row in calls)
    assert family==before and initial_capture in found
    merged=unified.merge_source_matches(found)
    assert {row['external_id']for row in merged}=={'main-boundary','equivalent-parts'}
    assert len(merged)==2
    assert status[0]['returned_count']==2
    assert status[0]['query_variants']==[QUESTION,*HEADINGS]
    assert status[0]['search_ms']==18.0 and status[0]['elapsed_ms']==23.0
    assert status[0]['original_question']==QUESTION and status[0]['query']==QUESTION


@pytest.mark.parametrize('question',[
    'Ali pred kot vedno pišemo vejico?',
])
def test_abstract_rule_question_does_not_run_sentence_expansion(interpreted,monkeypatch,question):
    family=interpreted(question);before=deepcopy(family)
    comma=family['request_plan'].get('comma_check')
    assert comma is None or not comma.get('reference_queries')
    gateway=RegistryGateway();definitions=unified.registry(gateway)
    found=[captured('existing','Already retained')];status=coverage(question)
    prior=deepcopy((found,status));details=object();calls=collector(monkeypatch,details)
    unified.expand_request_reference_rules(gateway,family,definitions,found,status,details,ImmediatePool())
    assert calls==[]and (found,status)==prior and family==before


@pytest.mark.parametrize(('active','adapter','has_coverage'),[
    (False,'official_reference_jsonl',True),
    (True,'ontology_answer_bank_json',True),
    (True,'official_reference_jsonl',False),
])
def test_no_lookup_via_disabled_nonofficial_or_uncollected_source(
        interpreted,monkeypatch,active,adapter,has_coverage):
    family=interpreted();gateway=RegistryGateway(sp_active=active,adapter=adapter)
    definitions=unified.registry(gateway)
    found=[];status=coverage(QUESTION)if has_coverage else[]
    prior=deepcopy((found,status));details=object();calls=collector(monkeypatch,details)
    unified.expand_request_reference_rules(gateway,family,definitions,found,status,details,ImmediatePool())
    assert calls==[]and (found,status)==prior


class FreezeGateway:
    """Record real orchestration HTTP contracts without source/model dispatch."""
    klikai_url='http://127.0.0.1:8000'
    lexical_profiles=False
    editorial_full_records=False
    def __init__(self,family,*,heading_present=True):
        self.family=deepcopy(family);self.calls=[];self.heading_present=heading_present
        self.rows=[{'slug':'sp2001','display_name':'Slovenski pravopis 2001',
            'adapter_key':'official_reference_jsonl','rights_mode':'open','source_type':'owned',
            'is_active':True}]+[{'slug':'other-'+str(i),'display_name':'Other '+str(i),
                'adapter_key':'official_reference_jsonl','rights_mode':'open','source_type':'owned',
                'is_active':True}for i in range(16)]
    def request(self,path,payload,base=None):
        self.calls.append((path,deepcopy(payload),base))
        if path=='/internal/pilot/interpret':
            return {'contract':'klikai.request-interpretation.v1',
                'input_sha256':digest({'contract':'klikai.request-interpretation.input.v1',**payload}),
                'request_plan':deepcopy(self.family['request_plan']),
                'request_plan_sha256':digest(self.family['request_plan'])}
        if path=='/api/sources':return {'sources':deepcopy(self.rows)}
        params=parse_qs(urlsplit(path).query)
        if urlsplit(path).path=='/api/search':
            query=params['q'][0];slug=params['sources'][0]
            assert params['limit']==['10']
            hits=[]
            for rank in range(10):
                text=((query.replace(' ','\n',1)+'\n\nComplete retained rule.')
                    if slug=='sp2001'and query in HEADINGS and rank==3 and self.heading_present
                    else 'An unrelated retained dictionary or publication entry.')
                hits.append({'source_slug':slug,'resource_id':slug+':'+str(rank)+':'+str(HEADINGS.index(query)if query in HEADINGS else 2),
                    'title':'Retained original '+str(rank),'rights_mode':'open',
                    'snippet':text[:100],'original_text':text,'score':10-rank,
                    'normative_status':'current_reference','source_revision':'a'*64,
                    'source_status':'Current reference','release_id':'unchanged-reference',
                    'chunk_kind':'official_reference'})
            return {'query':query,'sources':[slug],'count':10,'hits':hits}
        if path=='/internal/pilot/freeze':
            matches=[]
            for incoming in payload['unified_evidence']['matches']:
                row=deepcopy(incoming);capture=row.pop('capture')
                assert row['capture_sha256']==digest(capture)
                row['evidence_id']='unified:'+digest(capture)
                row['source']={'title':row['title'],'source_label':row['source_label'],
                    'excerpt':capture['original_text'],'bibliography':{},'url':None}
                matches.append(row)
            return {'bundle':{'fixture_only':True},'matches':matches,'retrieval':[]}
        raise AssertionError('Unexpected HTTP request '+path)


def no_tool(monkeypatch):
    monkeypatch.setattr(unified,'collect_tools',lambda *args:
        (None,{'family':'linghub_tools','status':'not_documentary','returned_count':0,
               'limit':None,'has_more':None}))


def test_literal_freeze_uses_two_local_heading_searches_and_reuses_authenticated_interpretation(
        interpreted,monkeypatch):
    family=interpreted();before=deepcopy(family);gateway=FreezeGateway(family);no_tool(monkeypatch)
    interpretation=unified.shared_interpretation(gateway,QUESTION,'','sl','sl')
    result=unified.freeze(gateway,family,{},interpretation=interpretation)
    paths=[path for path,_,_ in gateway.calls]
    assert paths.count('/internal/pilot/interpret')==1
    assert paths.count('/api/sources')==1 and paths.count('/internal/pilot/freeze')==1
    searches=[parse_qs(urlsplit(path).query)for path in paths if path.startswith('/api/search?')]
    assert len(searches)==2 and {row['q'][0]for row in searches}==set(HEADINGS)
    assert all(row['sources']==['sp2001']and row['limit']==['10']for row in searches)
    frozen=next(payload for path,payload,_ in gateway.calls if path=='/internal/pilot/freeze')
    assert len(frozen['unified_evidence']['matches'])==2
    assert all(row['capture']['resource_id'].startswith('sp2001:3:')
        for row in frozen['unified_evidence']['matches']) # match deliberately ranked fourth
    assert frozen['question']==QUESTION and frozen['context']==''
    assert frozen['request_plan']==before['request_plan']and family==before
    bundle=result['bundle'];assert bundle['retrieval']['route']=='bounded_local_reference_headings'
    status=next(row for row in bundle['retrieval']['coverage']if row['family']=='sp2001')
    assert status['returned_count']==2 and status['limit']==20
    assert len(status['reference_scope_lookups'])==2
    assert all(row['discovered_count']==10 and row['returned_count']==1
        and row['selection']=='exact_requested_heading'for row in status['reference_scope_lookups'])
    assert set(bundle['retrieval']['timings'])=={
        'registry_ms','source_collection_ms','evidence_preparation_ms','gateway_freeze_ms'}


def test_no_exact_heading_is_not_replaced_by_an_unrelated_high_ranked_hit(interpreted,monkeypatch):
    family=interpreted();gateway=FreezeGateway(family,heading_present=False);no_tool(monkeypatch)
    result=unified.freeze(gateway,family,{})
    frozen=next(payload for path,payload,_ in gateway.calls if path=='/internal/pilot/freeze')
    assert frozen['unified_evidence']['matches']==[]
    status=next(row for row in result['bundle']['retrieval']['coverage']if row['family']=='sp2001')
    assert status['status']=='no_match'and status['returned_count']==0
    assert all(row['discovered_count']==10 for row in status['reference_scope_lookups'])


@pytest.mark.parametrize('override',['explicit_source','source_search','ordinary_question'])
def test_explicit_source_search_and_other_questions_keep_normal_registry_route(
        interpreted,monkeypatch,override):
    family=interpreted('Ali pred kot vedno pišemo vejico?'if override=='ordinary_question'else QUESTION)
    if override=='explicit_source':family['request_plan']['requested_source']='sp2001'
    if override=='source_search':family['task']='source_search'
    gateway=FreezeGateway(family);no_tool(monkeypatch)
    result=unified.freeze(gateway,family,{})
    searches=[parse_qs(urlsplit(path).query)for path,_,_ in gateway.calls if path.startswith('/api/search?')]
    assert {row['sources'][0]for row in searches}=={row['slug']for row in gateway.rows}
    assert result['bundle']['retrieval']['route']=='normal_registry'


@pytest.mark.parametrize('field',['original','context','source_language','response_language','digest'])
def test_reused_interpretation_must_match_complete_request_binding(interpreted,field):
    family=interpreted();gateway=FreezeGateway(family)
    saved=unified.shared_interpretation(gateway,QUESTION,'','sl','sl');before=len(gateway.calls)
    if field=='digest':saved['request_plan_sha256']='0'*64
    else:
        saved['request_plan'][field]='altered'
        saved['request_plan_sha256']=digest(saved['request_plan'])
    with pytest.raises(Failure):unified.freeze(gateway,family,{},interpretation=saved)
    assert len(gateway.calls)==before
