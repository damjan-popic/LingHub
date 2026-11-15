"""Inflected original scopes through mocked production producer/consumers.

All source units are retained; syntax and selections are explicit fixtures.
No source/analyzer/model/service/app is contacted or bootstrapped.
"""
from copy import deepcopy
import pytest
from test_contextual_consultation_delivery import family,lexical,mock_analysis,paired_delivery,web,NS
from test_contextual_inflection import CASES,capture
from tools.zrc_reference.common import value_hash
from tools.zrc_reference.contextual_lexical_scope import lexical_context_scope


def fixture_response(text,case,damage=None):
    scope={'scope':'question','start':0,'end':len(text),'text':text}
    return capture(scope,case,damage)['captures'][0]['response']


@pytest.mark.parametrize('kind',['synonym','verbal','adjectival','nominal'])
def test_inflected_producer_shared_helper_primary_inventory_and_web_delivery(monkeypatch,tmp_path,kind):
    case=CASES[kind];f=family(case['question'],case['context'])
    result=paired_delivery(monkeypatch,tmp_path,family_override=f,case=case['case'],
        observation_factory=lambda text:fixture_response(text,case))
    assert result['mocked_analysis_inputs']==[case['context'].strip()]
    response=result['request']['syntax_evidence']['captures'][0]['response']
    assert response['target_scope']['scope']=='context'and response['target_scope']['start']==3
    units=result['registry']['units'];options=[o for u in units for o in u['payload']['nomination']['options']]
    assert bool(options)==(kind!='nominal')
    if kind!='nominal':
        assert all(record['admitted_occurrences']for u in units for record in u['payload']['records'])
        assert case['anchor']in result['web_result']['answer_md']
    assert result['web_result']['citations']and result['web_result']['answer_md']==result['core_result']['answer_md']


@pytest.mark.parametrize('damage',['lemma','offset'])
def test_wrong_inflection_analysis_retains_web_source_facts_without_an_occurrence(monkeypatch,tmp_path,damage):
    case=CASES['synonym'];f=family(case['question'],case['context'])
    result=paired_delivery(monkeypatch,tmp_path,family_override=f,case=case['case'],
        observation_factory=lambda text:fixture_response(text,case,damage))
    assert result['mocked_analysis_inputs']==[case['context'].strip()]
    units=result['registry']['units']
    assert all(not record['admitted_occurrences']for u in units for record in u['payload']['records'])
    assert all(not u['payload']['nomination']['options']for u in units)
    assert 'ekspresen'in result['web_result']['answer_md']and 'Zgornji podatki vira ostajajo uporabni'in result['web_result']['answer_md']
    assert ('syntax_evidence'in result['request'])==(damage!='offset')


def test_inflected_preanalysis_nomination_keeps_native_hash_and_primary_eligibility_guards(monkeypatch):
    case=CASES['synonym'];f=family(case['question'],case['context']);tool=lexical(f,case['case'])
    calls=mock_analysis(monkeypatch,observation_factory=lambda text:fixture_response(text,case))
    for damage in ('hash','secondary'):
        changed=deepcopy(tool);cap=changed['captures'][0];unit=cap['response']['evidence'][0]
        # Keep only this unit to test the particular nomination predicate.
        cap['response']['evidence']=[unit]
        if damage=='hash':unit['fields']['headword']='changed_without_native_unit_hash'
        else:
            unit['selection']['state']='secondary'
            unit['record_id']='lexical:'+value_hash({k:v for k,v in unit.items()if k!='record_id'})
        cap['response_sha256']=value_hash(cap['response']);changed['capture_set_sha256']=value_hash(changed['captures'])
        assert web.collect_native_context_syntax(NS(comma_checker={'syntax_url':'mock'}),f,[],changed)==(None,None)
    assert calls==[]
