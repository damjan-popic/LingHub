"""Authentic candidate sets; offline ordering/manual production-consumer replay.

No native token measurement or normal selector call occurs in these tests.
"""
from copy import deepcopy
from pathlib import Path
import gzip,importlib.util,json,os

import pytest
from tools.zrc_reference import pilot_cpu_input as cpu
from tools.zrc_reference import pilot_contextual_consultation as consultation
from tools.zrc_reference.common import canonical_json,value_hash
from tools.zrc_reference.pilot_tool_evidence import writer_unit
from tools.zrc_reference.request_interpretation import authoritative_request_plan
from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
from test_ordinary_contextual_consultation import selected_output

def retained():
    return json.loads(gzip.decompress((Path(__file__).parent/'fixtures/implicit_prefit_candidates_v1.json.gz').read_bytes()))

def evidence(passages):
    return [{**deepcopy(source),'evidence_id':'E'+str(i+1),'supports':[{
        'support_id':'E'+str(i+1)+'.S1','text':source['text'],'char_start':0,'char_end':len(source['text']),
        'source_role':'source_passage'}]}for i,source in enumerate(passages)]

def prepared(case,order='original',syntax=True):
    passages=deepcopy(case['passages'])
    if order=='reverse':passages.reverse()
    elif order=='interleave':passages=passages[::2]+passages[1::2]
    decisions=consultation.supporting_dispositions(passages,case['request_plan'],case['tool_evidence'],case['syntax_evidence']if syntax else None)
    groups,profile=cpu.prepare(evidence(passages),passages,'answer',query=case['question'],
        request_plan=case['request_plan'],all_candidates=True,consultation=decisions)
    return passages,decisions,groups,profile

def production_consumer(case,passages,groups,*,window=1,syntax=True):
    # A manual whole-group window is NOT native fitting or a normal selection.
    chosen=cpu.flatten(groups[:window])
    payload=_plan_runtime_payload(case['question'],chosen,passages,request_plan=case['request_plan'])
    payload['context']=case['context'];payload['tool_evidence']=case['tool_evidence']
    if syntax:payload['syntax_evidence']=case['syntax_evidence']
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    units=[u for u in registry.units if u['payload'].get('version')==consultation.IMPLICIT_VERSION]
    assert len(units)==1 and registry.required_unit_ids==[]
    result=selected_output(payload,runtime,registry,units)
    return registry,result

@pytest.mark.parametrize('index,count',[(0,34),(1,43)])
@pytest.mark.parametrize('order',['original','reverse','interleave'])
def test_authentic_candidates_share_eligibility_before_fit_and_in_consumer(index,count,order):
    case=retained()[index];before=deepcopy(case)
    passages,decisions,groups,profile=prepared(case,order)
    assert len(passages)==count and case==before
    records=decisions['records']
    assert len(records)==count
    for entry in profile['packing_order']:
        assert entry['writer_eligible']
        assert all(d['usable']for d in entry['consultation_dispositions'])
    exclusions=profile['writer_exclusion_decisions']
    assert exclusions and all(e['reason']=='not_usable_by_implicit_consultation'for e in exclusions)
    bad='pripraviti program'if index==0 else'železniška proga'
    rejected=[p for p in passages if p['original_json']['fields'].get('surface')==bad]
    assert rejected and all(not records[p['passage_id']]['usable']for p in rejected)
    assert all(p['passage_id']not in {r['passage_id']for g in groups for r in g}for p in rejected)
    first=next(p for p in passages if p['passage_id']==groups[0][0]['passage_id'])
    assert first['original_json']['fields']['surface']==('podpisati pogodbo'if index==0 else'hitri vlak')
    assert records[first['passage_id']]['basis']==('all_component_copresence'if index==0 else'matched_retained_construction')
    registry,result=production_consumer(case,passages,groups)
    text=result['answer_md']
    assert case['question']in text and 'Vir Kolokacije izpričuje zvezo'in text
    assert 'status postopka v viru: manual'in text and result['citations']
    if index==0:
        assert all(v in text for v in ('podpisati pogodbo','pripravili','podpisali','nista','Zaimenska navezava »je«'))
        assert 'pripravili pogodbo«; to je napoved'in text or not records[first['passage_id']]['anchor_constructions']
        assert 'pripravili pogodbo« ima oporo'not in text and 'popravi'not in text
    else:
        assert 'Ob »hitri vlak« v izvirniku analiza napoveduje povezavo'in text
        assert 'prednostne zamenjave'in text and 'železniška proga'not in text
    unit=next(u for u in registry.units if u['payload'].get('version')==consultation.IMPLICIT_VERSION)
    assert unit['payload']['record_dispositions']==[consultation.supporting_record_disposition(
        r['native_passage'],consultation.supporting_context(case['request_plan'],case['tool_evidence'],case['syntax_evidence']))for r in unit['payload']['records']]
    output=os.environ.get('KLIKAI_PREFIT_RECEIPTS')
    if output and order=='original':
        receipt={'provenance':case['provenance'],'replay':{'selection':'manual first whole source group after production ordering',
            'token_counts':'NOT measured; no historical count reused','native_fit':'NOT executed','normal_answer':False,'actual_operations':0},
            'before':case['old_dispositions'],'after':decisions,'input_profile':profile,
            'ordered_passage_ids':[[r['passage_id']for r in g]for g in groups],
            'registry':registry.model_dump(),'answer':result}
        Path(output,case['case']+'-offline.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')

def test_shared_relation_owners_are_preserved_without_duplicate_breadth_or_corroboration():
    case=retained()[0];passages,decisions,groups,profile=prepared(case)
    signing=[p for p in passages if p['original_json']['fields'].get('surface')=='podpisati pogodbo']
    assert {p['original_json']['entry_id']for p in signing}=={'2761503','642994'}
    a,b=[decisions['records'][p['passage_id']]for p in signing]
    assert a['relation_contribution']==b['relation_contribution']
    assert a['occurrence_contributions']==b['occurrence_contributions']
    assert a['owner']!=b['owner']and a['captured_coverage']!=b['captured_coverage']
    assert a['overlap_counterparts'][0]['interpretation']=='same_source_relation_distinct_owner_not_independent_corroboration'
    rows=[r for r in profile['packing_order']if any(pid in {p['passage_id']for p in signing}for pid in r['passage_ids'])]
    assert len(rows)==2 and rows[1]['additional_source_relations']==[]and rows[1]['additional_original_occurrences']==[]
    _,result=production_consumer(case,passages,groups,window=2)
    assert 'to ni neodvisna potrditev'in result['answer_md']and len(result['citations'])==2
    assert all(p['original_json']['fields']==next(o['original_json']['fields']for o in case['passages']if o['passage_id']==p['passage_id'])for p in signing)

@pytest.mark.parametrize('index',[0,1])
def test_missing_or_invalid_syntax_falls_back_to_copresence_without_prediction(index):
    case=retained()[index];passages,decisions,groups,_=prepared(case,syntax=False)
    relevant=[d for d in decisions['records'].values()if d['usable']and d['basis']=='all_component_copresence']
    assert relevant and all(not d['construction_witnesses']for d in relevant)
    _,result=production_consumer(case,passages,groups,syntax=False)
    assert 'Ločena analiza odvisnosti izvirnika ni na voljo'in result['answer_md']
    assert 'analiza napoveduje povezavo'not in result['answer_md']

def test_construction_obligation_precedes_optional_support_and_atomic_mixed_group_stays_whole():
    case=retained()[0];passages,decisions,groups,_=prepared(case)
    unusable=next(p for p in passages if not decisions['records'][p['passage_id']]['usable'])
    usable=next(p for p in passages if decisions['records'][p['passage_id']]['basis']=='all_component_copresence')
    by_pid={e['passage_id']:e for e in evidence(passages)}
    task={'kind':'documentary_advice','targets':[],'requested_tool_kinds':[]}
    mixed=[by_pid[usable['passage_id']],by_pid[unusable['passage_id']]]
    ordered,_,_=cpu.task_order([mixed],passages,task,request_plan=case['request_plan'],consultation=decisions)
    assert ordered==[mixed] # whole group, including the otherwise unusable owner
    ordered,_,_=cpu.task_order([[by_pid[usable['passage_id']]],[by_pid[unusable['passage_id']]]],passages,task,
        request_plan=case['request_plan'],consultation=decisions,construction_passage_ids=[unusable['passage_id']])
    assert ordered[0][0]['passage_id']==unusable['passage_id']

@pytest.mark.parametrize('question',['Kolokacije za pogodba.','Sopomenke za hiter.','Dajalnik imena Luka?',
    'Kako prevedemo izraz pogodba v italijanščino?','Pokaži samo vire za pogodbo.'])
def test_explicit_narrow_translation_and_source_only_do_not_gain_the_implicit_filter(question):
    case=retained()[0];plan=authoritative_request_plan(question,input_language='sl',locale='sl')
    assert consultation.supporting_context(plan,case['tool_evidence'],case['syntax_evidence'])is None

@pytest.mark.parametrize('index',[0,1])
def test_historical_v1_actual_units_keep_exact_whole_rendered_answer(index):
    case=retained()[index];unit,=case['historical_registry']['units']
    assert unit['payload']['version']==consultation.LEGACY_IMPLICIT_VERSION
    assert consultation.validate(unit['payload'],unit['bindings'])
    assert consultation.render(unit['payload'],'sl',lambda value:value)==case['historical_answer']

def test_wrong_complete_source_binding_is_not_a_usable_optional_record():
    case=retained()[1];source=deepcopy(case['passages'][0])
    context=consultation.supporting_context(case['request_plan'],case['tool_evidence'],case['syntax_evidence'])
    source['span']['record_sha256']='0'*64
    assert not consultation.supporting_record_disposition(source,context)['usable']

def test_material_qualification_contrast_is_kept_separate_from_same_relation_identity():
    case=retained()[0]
    context=consultation.supporting_context(case['request_plan'],case['tool_evidence'],case['syntax_evidence'])
    original=next(p for p in case['passages']if p['original_json']['entry_id']=='2761503')
    changed=deepcopy(original)
    # Synthetic qualification contrast over the complete retained row. This
    # minted test capture is not an actual new source observation.
    changed['passage_id']=changed['source_id']='mechanical-qualified-owner'
    changed['original_json']['fields']['source_attributes']['fixture_usage_condition']='MECHANICAL scope condition'
    changed['source_revision']=value_hash({'mechanical_capture':changed['original_json']})
    changed['span']['capture_sha256']=changed['source_revision']
    changed['span']['record_sha256']=value_hash(changed['original_json'])
    changed['text']=canonical_json(writer_unit(changed['original_json']))
    a=consultation.supporting_record_disposition(original,context)
    b=consultation.supporting_record_disposition(changed,context)
    assert a['usable']and b['usable']and a['relation_contribution']==b['relation_contribution']
    assert a['material_qualification_contribution']!=b['material_qualification_contribution']
    entries=evidence([original,changed])
    payload=_plan_runtime_payload(case['question'],entries,[original,changed],request_plan=case['request_plan'])
    payload.update(tool_evidence=case['tool_evidence'],syntax_evidence=case['syntax_evidence'])
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    units=[u for u in registry.units if u['payload'].get('version')==consultation.IMPLICIT_VERSION]
    result=selected_output(payload,runtime,registry,units)
    assert 'MECHANICAL scope condition'in result['answer_md']and len(result['citations'])==2
    assert len(units)==2 and all(len(u['payload']['records'])==1 for u in units)

def test_comparable_head_alternatives_add_original_occurrence_coverage_without_quotas():
    case=retained()[1];_,_,_,profile=prepared(case)
    alternatives=[r for r in profile['packing_order']if r.get('relevance_basis_rank')==2]
    assert alternatives[0]['additional_original_occurrences']and alternatives[1]['additional_original_occurrences']
    assert len(alternatives)>2 # no per-head cap or fixed number of sources

def test_unusable_rows_are_excluded_before_any_native_counter_and_dispositions_are_reused(monkeypatch):
    case=retained()[0]
    decisions=consultation.supporting_dispositions(case['passages'],case['request_plan'],case['tool_evidence'],case['syntax_evidence'])
    monkeypatch.setattr(consultation,'supporting_record_disposition',lambda *a:pytest.fail('Recomputed frozen decisions in fitting'))
    for tier in ('answer','short','long'):
        groups,profile=cpu.prepare(evidence(case['passages']),case['passages'],tier,query=case['question'],
            request_plan=case['request_plan'],all_candidates=True,consultation=decisions)
        assert len(groups)==12 and len(profile['writer_exclusion_decisions'])==22
        assert all(d['usable']for g in groups for d in [decisions['records'][g[0]['passage_id']]])

@pytest.mark.parametrize('index',[0,1])
def test_invalid_retained_syntax_cannot_upgrade_copresence_to_a_construction(index):
    case=retained()[index];bad=deepcopy(case['syntax_evidence'])
    bad['captures'][0]['response']['target_scope']['start']+=1
    context=consultation.supporting_context(case['request_plan'],case['tool_evidence'],bad)
    assert context['syntax_status']=='syntax_invalid'and context['observation']is None
    dispositions=[consultation.supporting_record_disposition(p,context)for p in case['passages']]
    assert any(d['basis']=='all_component_copresence'for d in dispositions)
    assert not any(d['basis']=='matched_retained_construction'for d in dispositions)
