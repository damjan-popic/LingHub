"""Authentic consumer records, mechanical plans; no runtime/native work."""
from copy import deepcopy
from pathlib import Path
import gzip,json,os
import pytest

from tools.zrc_reference import pilot_answer_plan as plans
from tools.zrc_reference import pilot_contextual_consultation as consultation
from tools.zrc_reference.common import canonical_json,value_hash
from tools.zrc_reference.pilot_tool_evidence import writer_unit
from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
from test_ordinary_contextual_consultation import selected_output

def retained():
    return json.loads(gzip.decompress((Path(__file__).parent/'fixtures/coherent_consumer_inputs_v1.json.gz').read_bytes()))

def prepared(case,passages=None,*,syntax=True):
    passages=deepcopy(case['passages']if passages is None else passages)
    evidence=[{**deepcopy(s),'evidence_id':'E'+str(i+1),'supports':[{'support_id':'E'+str(i+1)+'.S1',
        'text':s['text'],'char_start':0,'char_end':len(s['text']),'source_role':'source_passage'}]}for i,s in enumerate(passages)]
    payload=_plan_runtime_payload(case['question'],evidence,passages,request_plan=case['request_plan'])
    payload.update(context=case['context'],tool_evidence=case['tool_evidence'])
    if syntax:payload['syntax_evidence']=case['syntax_evidence']
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    return payload,runtime,registry

def select(payload,runtime,registry,units,*,same_paragraph=False):
    raw={'answer_language':'sl','blocks':[{'block_id':'B'+str(i+1),'paragraph_id':'P1'if same_paragraph else'P'+str(i+1),
        'unit_id':u['unit_id']}for i,u in enumerate(units)],'no_sufficient_evidence':False}
    return runtime._process_fresh_answer_plan(raw,registry,payload=payload,tier='answer')

@pytest.mark.parametrize('index,contexts',[(0,1),(1,2)])
@pytest.mark.parametrize('mode',['main','main_plus_one','all','context_only'])
@pytest.mark.parametrize('same_paragraph',[False,True])
def test_whole_combinations_have_faithful_selected_claims_and_one_shared_frame(index,contexts,mode,same_paragraph):
    case=retained()[index];before=deepcopy(case)
    payload,runtime,registry=prepared(case)
    main=[u for u in registry.units if u['payload'].get('contribution_kind')=='relation']
    context=[u for u in registry.units if u['payload'].get('contribution_kind')=='head_context']
    assert len(main)==1 and len(context)==contexts and registry.required_unit_ids==[]
    assert all(u['payload']['version']==consultation.IMPLICIT_VERSION for u in main+context)
    assert len(main[0]['bindings'])==(2 if index==0 else 1)
    chosen={'main':main,'main_plus_one':main+context[:1],'all':main+context,'context_only':context}[mode]
    result=select(payload,runtime,registry,chosen,same_paragraph=same_paragraph);text=result['answer_md']
    assert text.count('Izvirnik: »'+case['question']+'«')==1
    assert text.count('To je izbor iz zajetih podatkov;')==1
    assert text.count('Želite razjasniti')==(1 if index==0 else 0)
    expected={b['passage_id']for u in chosen for b in u['bindings']}
    assert {c['evidence_id']for c in result['citations']}==expected
    assert len(result['citations'])==sum(len(u['bindings'])for u in chosen)
    for claim in result['claims']:
        unit=next(u for u in chosen if u['unit_id']==claim['unit_id'])
        assert set(claim['support_ids'])=={b['support_id']for b in unit['bindings']}
    if mode=='main':
        assert 'vir Sopomenke'not in text
        if index==0:
            assert all(v in text for v in ('pripravili','podpisali','nista','Zaimenska navezava »je«'))
            assert text.count('podpisati pogodbo')==1 and text.count('40482')==1
            assert '1095'in text and '578'in text and 'neodvisna potrditev'in text
            assert 'pripravili pogodbo«; to je napoved'in text and 'pripravili pogodbo« ima oporo'not in text
        else:
            assert 'Vir Kolokacije izpričuje zvezo »hitri vlak«'in text
            assert 'analiza napoveduje povezavo'in text and 'prednostne zamenjave'in text
            assert 'črta, pas'not in text and 'veti, širiti se'not in text
    if mode=='context_only':
        assert 'Vir Kolokacije izpričuje zvezo'not in text and 'vir Sopomenke'in text
        assert 'ssss-automatic'in text and 'ne gre za predlagano zamenjavo'in text
    if mode in {'main_plus_one','all'}:assert 'vir Sopomenke'in text
    assert case==before and len(payload['passages'])==3 # unselected data stays accessible
    catalog=plans.answer_plan_selection_catalog(registry)
    for row in catalog['units']:
        assert case['question']in row['rendered_text']and 'popolni zapisi'in row['rendered_text']
    output=os.environ.get('KLIKAI_COHERENT_RECEIPTS')
    if output and not same_paragraph:
        receipt={'provenance':case['provenance'],'selection':'mechanical manual plan through production registry/consumer',
            'actual_operations':0,'automatic_selectivity_native_fit_latency':'unmeasured',
            'catalog_sha256':catalog['catalog_sha256'],'registry_sha256':registry.registry_sha256,'manual_plan':result['raw_plan'],
            'selected_unit_ids':[u['unit_id']for u in chosen],'retained_passage_ids':[p['passage_id']for p in payload['passages']],
            'answer':result}
        Path(output,case['case']+'-'+mode+'.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
        if mode=='main':Path(output,case['case']+'-catalog.json').write_text(json.dumps({
            'provenance':case['provenance'],'actual_operations':0,'catalog':catalog,'registry':registry.model_dump(),
            'retained_passage_ids':[p['passage_id']for p in payload['passages']]},ensure_ascii=False,indent=2)+'\n')

def test_same_relation_keeps_each_complete_owner_and_its_actual_carrier():
    case=retained()[0];_,_,registry=prepared(case)
    main,=[u for u in registry.units if u['payload'].get('contribution_kind')=='relation']
    records=main['payload']['records'];assert len(records)==2
    by_owner={r['native_passage']['original_json']['entry_id']:r for r in records}
    assert set(by_owner)=={'2761503','642994'}
    for record in records:
        source=record['native_passage']
        assert source==next(p for p in case['passages']if p['passage_id']==source['passage_id'])
    decisions={d['owner']['entry_id']:d for d in main['payload']['record_dispositions']}
    assert decisions['2761503']['anchor_constructions']and not decisions['642994']['anchor_constructions']
    assert decisions['2761503']['captured_coverage']['count_total']==1095
    assert decisions['642994']['captured_coverage']['count_total']==578

def mechanical_variant(source,damage):
    source=deepcopy(source);unit=source['original_json'];fields=unit['fields']
    if damage=='identity':
        fields['lexical_unit_id']+=1
        fields['source_attributes']['lexical_unit_id']=str(fields['lexical_unit_id'])
    elif damage=='status':
        fields['status']='automatic';fields['source_attributes']['status']='automatic'
    elif damage=='unknown_source':fields['source_attributes']['fixture_usage_condition']='MECHANICAL source condition'
    elif damage=='unknown_component':fields['components'][0]['fixture_condition']='MECHANICAL component condition'
    else:fields.setdefault('provenance',{})['fixture_condition']='MECHANICAL provenance condition'
    source['source_id']=source['passage_id']='mechanical-'+damage
    source['source_revision']=value_hash({'mechanical_capture':unit})
    source['span']['capture_sha256']=source['source_revision'];source['span']['record_sha256']=value_hash(unit)
    source['text']=canonical_json(writer_unit(unit))
    return source

@pytest.mark.parametrize('damage',['identity','status','unknown_source','unknown_component','unknown_provenance'])
def test_similar_surface_does_not_merge_distinct_native_identity_or_material_qualifications(damage):
    case=retained()[0]
    sources=[p for p in case['passages']if p['tool_kind']=='collocations']
    sources[1]=mechanical_variant(sources[1],damage) # explicit synthetic contrast, not a new native observation
    payload,runtime,registry=prepared(case,sources)
    main=[u for u in registry.units if u['payload'].get('contribution_kind')=='relation']
    assert len(main)==2 and all(len(u['bindings'])==1 for u in main)
    result=select(payload,runtime,registry,main)
    assert result['answer_md'].count('Vir Kolokacije izpričuje zvezo')==2
    assert len(result['citations'])==2
    if damage.startswith('unknown'):assert 'MECHANICAL'in result['answer_md']
    if damage=='status':assert 'status postopka v viru: automatic'in result['answer_md']and 'status postopka v viru: manual'in result['answer_md']
    output=os.environ.get('KLIKAI_COHERENT_RECEIPTS')
    if output:Path(output,'mechanical-'+damage+'-contrast.json').write_text(json.dumps({
        'provenance':'explicit synthetic native identity/qualification contrast over retained record; not new source data',
        'actual_operations':0,'answer':result,'registry':registry.model_dump()},ensure_ascii=False,indent=2)+'\n')

@pytest.mark.parametrize('index',[0,1])
def test_no_relation_and_no_syntax_still_has_complete_optional_source_context(index):
    case=retained()[index];sources=[p for p in case['passages']if p['tool_kind']=='synonyms']
    payload,runtime,registry=prepared(case,sources,syntax=False)
    assert registry.units and all(u['payload']['contribution_kind']=='head_context'for u in registry.units)
    result=select(payload,runtime,registry,registry.units)
    assert 'vir Sopomenke'in result['answer_md']and result['citations']
    assert 'Vir Kolokacije izpričuje zvezo'not in result['answer_md']
    output=os.environ.get('KLIKAI_COHERENT_RECEIPTS')
    if output:Path(output,case['case']+'-fallback.json').write_text(json.dumps({'provenance':case['provenance'],
        'contrast':'only retained complete synonym records, syntax not supplied; mechanical fallback not new discovery',
        'actual_operations':0,'answer':result},ensure_ascii=False,indent=2)+'\n')

@pytest.mark.parametrize('index',[0,1])
def test_actual_v1_v2_registries_ids_selections_answers_and_bindings_remain_exact(index):
    case=retained()[index];registry=plans.AnswerUnitRegistry.model_validate(case['historical_registry'])
    assert registry.model_dump()==case['historical_registry']
    result=plans.process_answer_plan(case['historical_selection'],registry,locale='sl')
    assert result['answer_md']==case['historical_answer']and result['raw_plan']==case['historical_selection']
    assert {c['passage_id']for c in result['citations']}=={b['passage_id']for u in registry.units for b in u['bindings']}
    old=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/implicit_prefit_candidates_v1.json.gz').read_bytes()))[index]
    old_registry=plans.AnswerUnitRegistry.model_validate(old['historical_registry'])
    result=plans.process_answer_plan(case['historical_v1_selection'],old_registry,locale='sl')
    assert result['answer_md']==old['historical_answer']

def test_tampered_contribution_cannot_attach_unselected_head_context_bindings():
    case=retained()[0];_,_,registry=prepared(case)
    main=next(u for u in registry.units if u['payload']['contribution_kind']=='relation')
    context=next(u for u in registry.units if u['payload']['contribution_kind']=='head_context')
    payload=deepcopy(main['payload']);bindings=deepcopy(main['bindings'])
    payload['records']+=context['payload']['records'];payload['record_dispositions']+=context['payload']['record_dispositions']
    bindings+=context['bindings']
    assert not consultation.validate(payload,bindings)
