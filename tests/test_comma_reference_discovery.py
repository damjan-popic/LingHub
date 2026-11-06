"""Explain-only reference expansion uses CORE's plan and existing registry."""
from concurrent.futures import Future
from copy import deepcopy
import os
from pathlib import Path

import pytest

from pilot_backend import unified
from pilot_backend.store import digest


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
    'Preveri vejice: Ko pridem domov bom poklical.',
    'Ali pred kot vedno pišemo vejico?',
])
def test_plain_checks_and_general_rule_questions_do_not_expand(interpreted,monkeypatch,question):
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
