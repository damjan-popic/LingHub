"""Retained analyses replayed through the real shared lexical projection."""
import asyncio,copy,json,os,sys
from pathlib import Path
from types import SimpleNamespace
import pytest
from core import lexical_profile
from pilot_backend import request_tasks,unified


@pytest.fixture
def core_plan():
    root=os.environ.get('KLIKAI_PINNED_CORE_ROOT')
    if not root:pytest.skip('Paired CORE root not configured')
    sys.path.insert(0,root)
    from tools.zrc_reference.request_interpretation import authoritative_request_plan
    return authoritative_request_plan


@pytest.mark.parametrize('position',[0,1])
def test_actual_saved_incident_analysis_is_bound_without_a_new_analyzer_or_source_call(monkeypatch,core_plan,position):
    saved=json.loads((Path(__file__).parent/'fixtures/knowledge_access_saved_incident.json').read_text())[position]
    analysis=copy.deepcopy(saved['analysis'])
    from core import analysis_profiles
    async def recorded(*args,**kwargs):return copy.deepcopy(analysis)
    monkeypatch.setattr(analysis_profiles,'analyze',recorded)
    monkeypatch.setattr(lexical_profile,'httpx_client',lambda:(_ for _ in ()).throw(AssertionError('Network forbidden')))
    state=SimpleNamespace(resource_readiness={},resource_files_by_kind={},
        synonyms=None,collocations=None,sloleks_profiles=None)
    request=SimpleNamespace(app=SimpleNamespace(state=state))
    old=saved['historical_request_plan'];q=saved['question']
    before=asyncio.run(lexical_profile.make_profile(lexical_profile.ProfileRequest(
        text=q,lang='sl',request_plan=old),request))
    current=core_plan(q,saved['context'],input_language='sl',locale='sl')
    after=asyncio.run(lexical_profile.make_profile(lexical_profile.ProfileRequest(
        text=q,lang='sl',request_plan=current),request))
    assert [r['text']for r in after['interpretation']['targets']]==['lesenka']
    records=[r for r in after['evidence']if r['kind']=='loris']
    assert len(records)==1 and records[0]['match_relation']=='whole_expression'
    raw=next(x for x in analysis['spans']+analysis['issues']if x['rule_id']=='paronym_lesenka')
    assert records[0]['fields']['payload']==raw['payload']
    assert raw['start']==current['target_spans'][0]['start']
    assert raw['end']==current['target_spans'][0]['end']
    assert len([r for r in before['evidence']if r['kind']=='loris'])==(0 if position==0 else 1)
    assert saved['analysis']==analysis
    # Continue the actual profile through the existing source-unit and renderer
    # path. This verifies useful qualified advice, not only a restored hit count.
    import hashlib
    from tools.zrc_reference.common import value_hash
    from tools.zrc_reference.pilot_tool_evidence import lexical_passages
    from tools.zrc_reference.pilot_answer_plan import build_answer_unit_registry,process_answer_plan
    from tools.zrc_reference.pilot_evidence import sign
    passage=next(row for row in lexical_passages({'captures':[{
        'response':after,'response_sha256':value_hash(after)}]})if row['tool_kind']=='loris')
    selected=copy.deepcopy(passage);selected['evidence_id']='recorded-incident'
    selected['supports']=[{'support_id':'recorded-incident.S1','text':selected['text'],
        'char_start':0,'char_end':len(selected['text'])}]
    key=b'offline-question-access-test-key'
    profile=sign({'profile_id':'recorded-incident','locale':'sl',
        'question_sha256':hashlib.sha256(q.encode()).hexdigest(),
        'context_sha256':hashlib.sha256(saved['context'].encode()).hexdigest(),
        'selected_evidence_sha256':value_hash([selected]),
        'query_plan_sha256':value_hash(current),
        'answer_plan_requirements':{'required_unit_types':['curated_recommendation']}},key)
    registry=build_answer_unit_registry(locale='sl',question=q,context=saved['context'],
        selected_evidence=[selected],signed_input_profile=profile,profile_signing_key=key,
        request_plan=current)
    unit=next(row for row in registry.units if row['unit_type']=='curated_recommendation')
    rendered=process_answer_plan({'answer_language':'sl','blocks':[{'block_id':'B1',
        'unit_id':unit['unit_id'],'paragraph_id':'P1'}],'no_sufficient_evidence':False},registry,locale='sl')
    assert all(text in rendered['answer_text']for text in
        ['LORIS priporoča','barvica, barvni svinčnik','matita colorata',raw['payload']['Vslo']])
    assert rendered['citations'][0]['support_id']=='recorded-incident.S1'


def test_unresolved_subject_uses_existing_clarification_route(core_plan):
    p=core_plan('Je to pravilna beseda?',input_language='sl',locale='sl')
    decision=request_tasks.grammar_decision({'request_plan':p},'sl')
    assert decision['kind']=='lexical_request_clarification'
    assert 'kateri izraz'in decision['text']
    assert decision['resources']==[]


def test_distinct_evidence_failure_states_have_distinct_user_visible_explanations():
    b={'locale':'sl','retrieval':{}}
    no_source=unified.fallback(b,'no_support',error={'error_code':'no_selected_evidence'})
    no_judgment=unified.fallback(b,'no_support',error={'error_code':'insufficient_judgment_support'})
    processing=unified.fallback(b,'failed',error={'error_code':'provider_unavailable'})
    assert len({r['answer_md']for r in (no_source,no_judgment,processing)})==3
    assert 'dovolj relevantnega gradiva'in no_source['answer_md']
    assert 'zahtevano presojo'in no_judgment['answer_md']
    assert 'ni na voljo'in processing['answer_md']
