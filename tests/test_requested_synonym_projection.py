"""Requested synonym groups are chosen before the bounded source projection."""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from core.lexical_profile import ProfileRequest,make_profile


@pytest.fixture
def selected_core(monkeypatch):
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')or os.environ.get('KLIKAI_CORE_REPO')
    if not configured:pytest.skip('Select CORE for the native profile/answer contract test')
    root=Path(configured).resolve()
    if not(root/'tools/zrc_reference/pilot_lexical_relations.py').is_file():
        pytest.skip('Selected CORE does not contain the native lexical-relations contract')
    monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))


def sense(number,kind='synonym',extra=False):
    relations=[{'relation_type':kind,'raw_text':'izraz'+str(number),'target_headword':'izraz'+str(number),
        'synonym_type':'core','synonym_score':0.5,'source_order':1}]
    if extra:relations.append({'relation_type':'antonym','raw_text':'nasprotje','source_order':2})
    return {'sense_id':'sense-'+str(number),'sense_order':number,'metadata':[],
            'source_attributes':{},'relations':relations}


def entry(number,senses,headword='vzorec'):
    return {'source_entry_id':'entry-'+str(number),'entry_order':number,'headword':headword,
            'head_status':'ssss-automatic','senses':senses,'lexical_units':[],
            'source_attributes':{},'head_metadata':[]}


def produce(monkeypatch,entries,*,requested=True):
    from tools.zrc_reference.request_interpretation import authoritative_request_plan
    async def analysis(*args,**kwargs):
        return {'status':'ok','raw_engines':{},'engines_used':[],'loris_status':'no_match'}
    monkeypatch.setattr('core.analysis_profiles.analyze',analysis)
    question='Sopomenke besede vzorec?'if requested else'vzorec'
    plan=authoritative_request_plan(question)
    original={'entries':deepcopy(entries),'count_entries':len(entries),
        'count_senses':sum(len(e['senses'])for e in entries),'ordering':'source order'}
    before=deepcopy(original)
    class Reader:
        manifest={'fingerprint':'fixture-fingerprint','source':{'xml_sha256':'a'*64,'title':'Sopomenke'}}
        def lookup_evidence(self,lemma,normalized=True):
            assert lemma=='vzorec';return original
    state=NS(synonyms=Reader(),sloleks_profiles=None,collocations=None,morphology=None)
    request=NS(app=NS(state=state))
    value=asyncio.run(make_profile(ProfileRequest(text=question,lang='sl',request_plan=plan),request))
    assert original==before,'Task projection cannot mutate the loaded source result'
    return value,plan,before


def result(value):return value['facets']['synonyms']['data'][0]['lookups'][0]['result']


def native_answer(value,plan):
    from tools.zrc_reference.pilot_tool_evidence import tool_passages,validate_capture_set
    from tools.zrc_reference.common import value_hash
    from tools.zrc_reference.grounded import packet
    from tools.zrc_reference.pilot_evidence import annotate,source_unit_supports
    from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
    cap={'operation':'lexical_profile','request':value['query'],'response':value,
         'response_sha256':value_hash(value),'duration_ms':0}
    capture={'version':'klikai.lexical-collation.v1','plan':{'status':'ready',
        'routing_kind':'automatic_lexical_profile','focus':value['query']['text']},
        'captures':[cap],'capture_set_sha256':value_hash([cap])}
    validate_capture_set(capture);sources=tool_passages(capture)
    rows=annotate(source_unit_supports(packet(sources,budget=sum(len(r['text'])for r in sources)),sources))
    runtime=_plan_runtime();payload=_plan_runtime_payload(plan['original'],rows,sources,request_plan=plan)
    registry=runtime._answer_registry(payload,'answer')
    selector=runtime._single_required_answer_plan(registry,'sl');assert selector is not None
    answer=runtime._process_fresh_answer_plan(selector,registry,payload=payload,tier='answer')
    return answer,registry,sources


def test_more_than_four_positive_groups_keep_known_parent_coverage_through_core(selected_core,monkeypatch):
    value,plan,original=produce(monkeypatch,[entry(1,[sense(i,extra=i==1)for i in range(1,7)])])
    data=result(value);assert data['original_result']==original
    units=[u for u in value['evidence']if u['kind']=='synonyms'];assert len(units)==4
    assert [u['fields']['sense']['sense_order']for u in units]==[1,2,3,4]
    assert units[0]['fields']['sense']['relations']==original['entries'][0]['senses'][0]['relations']
    for unit in units:
        coverage=unit['fields']['coverage']
        assert coverage['senses_total_in_returned_source']==6
        assert coverage['synonym_senses_available']==6 and coverage['senses_returned']==4
        assert coverage['senses_truncated']is True and coverage['selection_truncated']is True
        assert coverage['entries_truncated']is False
    answer,registry,sources=native_answer(value,plan)
    assert answer['answer_support']=='supported'and len(answer['citations'])==4
    assert answer['answer_md'].count('Prikazan je zajeti izbor skupin iz vira.')==1
    assert 'izraz4'in answer['answer_md']and'izraz5'not in answer['answer_md']
    assert 'nasprotje'not in answer['answer_md']
    group=registry.units[0]['payload']['groups'][0]
    assert group['coverage']==units[0]['fields']['coverage']


def test_fifth_positive_group_is_not_lost_behind_four_antonym_only_groups(selected_core,monkeypatch):
    entries=[entry(1,[sense(i,'antonym')for i in range(1,5)]+[sense(5)])]
    value,plan,original=produce(monkeypatch,entries)
    data=result(value);assert data['original_result']==original
    units=[u for u in value['evidence']if u['kind']=='synonyms'];assert len(units)==1
    assert units[0]['fields']['sense']['sense_order']==5
    coverage=units[0]['fields']['coverage']
    assert coverage['senses_total_in_returned_source']==5
    assert coverage['synonym_senses_available']==coverage['senses_returned']==1
    assert all(coverage[k]is False for k in ['entries_truncated','senses_truncated','selection_truncated'])
    answer,_,_=native_answer(value,plan)
    assert 'izraz5'in answer['answer_md']and len(answer['citations'])==1
    assert 'Prikazan je zajeti izbor skupin iz vira.'not in answer['answer_md']


def test_identity_compatible_positive_entry_is_selected_before_entry_cap(selected_core,monkeypatch):
    entries=[entry(1,[sense(1)],'drug'),entry(2,[sense(2)],'drugačen'),entry(3,[sense(3)])]
    value,_,original=produce(monkeypatch,entries)
    units=[u for u in value['evidence']if u['kind']=='synonyms']
    assert len(units)==1 and units[0]['fields']['headword']=='vzorec'
    assert units[0]['fields']['sense']['sense_id']=='sense-3'
    assert units[0]['fields']['coverage']['synonym_entries_available']==1
    assert result(value)['original_result']==original


def test_eligible_entry_cap_and_ordinary_nonrelation_profile_remain_bounded(selected_core,monkeypatch):
    entries=[entry(i,[sense(i)])for i in range(1,4)]
    value,_,_=produce(monkeypatch,entries)
    units=[u for u in value['evidence']if u['kind']=='synonyms'];assert len(units)==2
    assert all(u['fields']['coverage']['entries_truncated']is True for u in units)
    original_entries=[entry(1,[sense(i,'antonym')for i in range(1,5)]+[sense(5)])]
    ordinary,_,_=produce(monkeypatch,original_entries,requested=False)
    normal=result(ordinary)
    assert 'original_result'not in normal
    assert len(normal['entries'][0]['senses'])==4
    assert all('coverage'not in u['fields']for u in ordinary['evidence']if u['kind']=='synonyms')
