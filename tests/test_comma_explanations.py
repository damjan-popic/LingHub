"""Exact edit/application links; related prose never becomes edit certification."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path

import pytest

from core.comma_checker import align
from pilot_backend.comma_explanations import connect, reader
from test_comma_checker import response


EXAMPLE='Pozimi in kadar je mraz, moramo zakuriti.'
UNIT='U'+'a'*16


def proposal(original, proposed):
    text,edits=align(original,response(original,proposed))
    return {'status':'ok','original_text':original,'proposed_text':text,'edits':edits,
            'offset_convention':'unicode_codepoint_half_open'}


def task(original, start=30, scope='question'):
    return {'target':{'text':original,'start':start,'end':start+len(original),'scope':scope},'explain':True}


def graph(example=EXAMPLE,kind='construction_application',payload=None):
    quote='Primarni vir navaja: '+example
    binding={'support_id':'E1.S1','evidence_id':'E1','passage_id':'frozen-source',
        'source_slug':'sp2001','source_id':'source-record','source_revision':'c'*64,
        'char_start':10,'char_end':10+len(quote),
        'text_sha256':hashlib.sha256(quote.encode()).hexdigest()}
    receipt={'unit_id':UNIT,'unit_type':kind,'bindings':[binding],
        'payload':payload or {'construction':'coordination_punctuation','examples':{'ordinary':example}}}
    explained={'claims':[{'unit_id':UNIT,'claim_id':'C1','text':'Already rendered source application.',
        'paragraph_id':'P1','support_ids':['E1.S1']}],
        'display_blocks':[{'unit_id':UNIT,'unit_type':kind,'claim_id':'C1','block_id':'B1',
            'text':'Already rendered source application.','paragraph_id':'P1','support_ids':['E1.S1']}],
        'citations':[{'unit_id':UNIT,'claim_id':'C1','block_id':'B1','evidence_id':'frozen-source',
            'source_key':'sp2001','source_id':'source-record','source_revision':'c'*64,
            'support_id':'E1.S1','char_start':10,'char_end':10+len(quote),'snippet':quote}]}
    return explained,[receipt]


def test_source_attested_comma_is_linked_at_the_exact_edit_position():
    original=EXAMPLE.replace(',','');value=proposal(original,EXAMPLE)
    explained,units=graph();before=deepcopy(explained)
    result=connect(value,task(original),explained,units)
    assert result['status']=='applied'and result['unexplained_edit_indexes']==[]
    link,=result['links']
    assert link['edit_index']==0 and link['edit_start']==original.index(' moramo')
    assert link['relation']=='supports_proposal'and link['citation_indexes']==[0]
    assert link['claim_id']=='C1'and link['unit_id']==UNIT
    assert explained==before


def test_wrong_prediction_is_an_explicit_source_disagreement():
    wrong=EXAMPLE.replace(' in ', ', in ')
    explained,units=graph();value=proposal(EXAMPLE,wrong)
    result=connect(value,task(EXAMPLE),explained,units)
    assert result['links'][0]['relation']=='disagrees_with_proposal'
    assert result['applications'][0]['relation']=='disagrees_with_proposal'


def test_whitespace_recognition_keeps_original_crlf_edit_offsets():
    original=EXAMPLE.replace(', ', '\r\n')
    proposed=original.replace('\r\n', ',\r\n')
    explained,units=graph();value=proposal(original,proposed)
    result=connect(value,task(original),explained,units)
    assert result['status']=='applied'
    assert result['links'][0]['edit_start']==original.index('\r\n')
    assert value['original_text']==original and '\r\n'in value['proposed_text']


@pytest.mark.parametrize('original',[
    'Poleti in kadar je mraz moramo zakuriti.',
    'Pozimi in kadar ni mraz moramo zakuriti.',
    'Pozimi in kadar je 3,14 stopinje moramo zakuriti.',
    'pozimi in kadar je mraz moramo zakuriti.',
    'Pozimi in kadar je mraz moramo zakuriti!',
])
def test_words_negation_numbers_case_and_other_punctuation_cannot_be_fuzzed(original):
    proposed=original.replace(' moramo', ', moramo')
    explained,units=graph();result=connect(proposal(original,proposed),task(original),explained,units)
    assert result['status']=='related_guidance'and result['links']==[]
    assert result['unexplained_edit_indexes']==[0]


def test_generic_cited_guidance_never_certifies_every_comma():
    original=EXAMPLE.replace(',','');explained,units=graph()
    result=connect(proposal(original,EXAMPLE),task(original),explained,[])
    assert result['status']=='related_guidance'and result['links']==[]
    assert result['related_guidance'][0]['citation_indexes']==[0]


@pytest.mark.parametrize('damage',['snippet','source_revision','span','unit','duplicate'])
def test_private_receipt_must_match_the_actual_selected_claim_and_source_bindings(damage):
    original=EXAMPLE.replace(',','');explained,units=graph()
    if damage=='snippet':explained['citations'][0]['snippet']='Different source text.'
    elif damage=='source_revision':units[0]['bindings'][0]['source_revision']='f'*64
    elif damage=='span':units[0]['bindings'][0]['char_end']-=1
    elif damage=='unit':units[0]['unit_id']='U'+'b'*16
    else:explained['citations'].append(deepcopy(explained['citations'][0]))
    result=connect(proposal(original,EXAMPLE),task(original),explained,units)
    assert result['status']=='related_guidance'and result['links']==[]


def test_receipt_example_must_actually_occur_in_an_exact_cited_passage():
    original='Poleti in kadar je vroče moramo zalivati.'
    explained,units=graph()
    units[0]['payload']['examples']['ordinary']='Poleti in kadar je vroče, moramo zalivati.'
    result=connect(proposal(original,original.replace(' moramo',', moramo')),task(original),explained,units)
    assert result['status']=='related_guidance'and not result['links']


def test_owned_source_application_uses_its_scope_and_exact_selected_occurrence():
    original='1,2; 3,4';selected=task(original,start=50)
    application={'kind':'numeric_list_permission','input_text':original,
        'input_span':{'scope':'question','start':50,'end':50+len(original),
            'text_sha256':hashlib.sha256(original.encode()).hexdigest()},
        'outputs':[original],'edits':[{'start':0,'end':len(original),'replacement':original}]}
    explained,units=graph(example=original,kind='source_advice',payload={'application':application})
    result=connect(proposal(original,original),selected,explained,units)
    assert result['status']=='applied'and result['links']==[]
    assert result['applications'][0]['basis']=='owned_source_application'
    units[0]['payload']['application']['input_span']['scope']='context'
    assert connect(proposal(original,original),selected,explained,units)['status']=='related_guidance'


def test_no_changes_is_not_a_claim_that_every_comma_is_verified():
    explained,units=graph();result=connect(proposal(EXAMPLE,EXAMPLE),task(EXAMPLE),explained,units)
    assert result['status']=='applied'and result['links']==[]
    assert result['coverage']=='matched_applications_only'
    wrong=EXAMPLE.replace(',','')
    result=connect(proposal(wrong,wrong),task(wrong),explained,units)
    assert result['links']==[]and result['applications'][0]['relation']=='disagrees_with_proposal'


def test_mismatched_target_or_positions_fail_locally_and_reader_is_closed():
    original=EXAMPLE.replace(',','');value=proposal(original,EXAMPLE);explained,units=graph()
    selected=task(original);selected['target']['start']+=1
    assert connect(value,selected,explained,units)['status']=='unsupported'
    result=connect(value,task(original),explained,units)
    result['applied_unit_receipts']=units
    result['links'][0]['private_provider_payload']={'secret':'not exposed'}
    public=reader(result)
    assert 'applied_unit_receipts'not in public
    assert 'private_provider_payload'not in public['links'][0]


def test_real_core_coordination_registry_and_citations_supply_the_application(monkeypatch):
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')
    if not configured:pytest.skip('Cross-repository fixture requires the existing pinned CORE tree')
    root=Path(configured)
    for path in [root,root/'apps/api',root/'tests',root/'tests/zrc']:
        monkeypatch.syspath_prepend(str(path))
    from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
    from test_coordination_punctuation import selected_fixture
    from tools.zrc_reference.request_interpretation import response as interpretation
    original=EXAMPLE.replace(',','');question='Preveri vejice in pojasni: '+original
    plan=interpretation(question,'','sl','sl')['request_plan']
    evidence,passages,_=selected_fixture(question=question)
    payload=_plan_runtime_payload(question,evidence,passages,request_plan=plan)
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    unit=next(row for row in registry.units if row['unit_type']=='construction_application'
              and row['payload'].get('construction')=='coordination_punctuation')
    explained=runtime._process_fresh_answer_plan({'answer_language':'sl','blocks':[
        {'block_id':'B1','unit_id':unit['unit_id'],'paragraph_id':'P1'}],
        'no_sufficient_evidence':False},registry,payload=payload,tier='answer')
    result=connect(proposal(original,EXAMPLE),plan['comma_check'],explained,explained['applied_unit_receipts'])
    assert result['status']=='applied'and result['links'][0]['relation']=='supports_proposal'
    direct=[explained['citations'][index]for index in result['links'][0]['citation_indexes']]
    assert direct and len(direct)<len(unit['bindings'])
    assert all(row['source_key']=='sp2001'for row in direct)
    assert all(EXAMPLE in ' '.join(row['snippet'].split())for row in direct)


def finite_boundary(original, expected, position):
    explained,receipts=graph(example='Vejica loči nadredni in odvisni stavek.')
    binding=receipts[0]['bindings'][0]
    receipts[0]['payload']={'construction':'finite_subordinate_clause_boundary',
        'input_text':original,'input_sha256':hashlib.sha256(original.encode()).hexdigest(),
        'input_span':dict(task(original)['target'],text_sha256=hashlib.sha256(original.encode()).hexdigest()),
        'boundary':{'insert_at':position},'output_text':expected,
        'source_record':{'parts':[{'binding':binding}]}}
    return explained,receipts

def test_general_clause_application_links_only_the_independently_identified_boundary():
    original='Če ne pride ne grem domov.'
    expected='Če ne pride, ne grem domov.'
    explained,receipts=finite_boundary(original,expected,original.index(' ne grem'))
    value=proposal(original,'Če, ne pride, ne grem domov.')
    result=connect(value,task(original),explained,receipts)
    assert result['status']=='applied'
    assert result['links'][0]['edit_index']==1
    assert result['links'][0]['relation']=='supports_proposal'
    assert result['unexplained_edit_indexes']==[0]
    assert result['applications'][0]['basis']=='syntax_bound_source_application'

def test_general_clause_application_disagrees_with_removing_the_required_comma():
    original='Če ne pride, ne grem domov.'
    explained,receipts=finite_boundary(original,original,original.index(','))
    result=connect(proposal(original,original.replace(',','')),task(original),explained,receipts)
    assert result['links'][0]['relation']=='disagrees_with_proposal'

def test_general_clause_receipt_for_a_different_target_does_not_certify_edits():
    original='Če ne pride ne grem domov.'
    explained,receipts=finite_boundary(original,original.replace(' ne grem',', ne grem'),11)
    receipts[0]['payload']['input_span']['start']+=1
    result=connect(proposal(original,original.replace(' ne grem',', ne grem')),task(original),explained,receipts)
    assert not result['links'] and result['status']=='related_guidance'


def test_contextual_reading_is_conditional_in_both_application_and_edit_link():
    original='Ker je mraz zakurimo.'; expected='Ker je mraz, zakurimo.'
    explained,receipts=finite_boundary(original,expected,11)
    receipts[0]['payload']['analysis_basis']='comma_context'
    result=connect(proposal(original,expected),task(original),explained,receipts)
    assert result['applications'][0]['relation']=='conditional_support'
    assert result['applications'][0]['basis']=='punctuation_context_source_application'
    assert result['links'][0]['relation']=='conditional_support'
    assert reader(result)['links'][0]['relation']=='conditional_support'


@pytest.mark.parametrize(('original','proposed','basis','expected_relation'),[
    ('Če ne pride ne grem.','Če ne pride, ne grem.','original','supports_proposal'),
    ('Če ne pride ne grem.','Če ne pride ne, grem.','original','disagrees_with_proposal'),
    ('Ker je mraz zakurimo.','Ker je mraz, zakurimo.','comma_context','conditional_support'),
    ('Če je hladno oblečem jopico.','Če je hladno, oblečem jopico.','comma_context','conditional_support'),
])
def test_real_predicate_registry_renderer_and_web_keep_the_reading_basis(monkeypatch,original,proposed,basis,expected_relation):
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')
    if not configured:pytest.skip('Cross-repository fixture requires pinned CORE')
    root=Path(configured)
    for path in [root,root/'apps/api',root/'tests',root/'tests/zrc']:
        monkeypatch.syspath_prepend(str(path))
    from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
    from test_clause_punctuation import attach
    from test_clause_predicate_groups import contextual
    from tools.zrc_reference.request_interpretation import response as interpretation
    question,context,evidence,passages=attach(contextual(original,proposed))
    for row in evidence+passages:
        if row.get('tool_kind')=='syntax':
            row['span']={'char_start':0,'char_end':len(row['text']),
                'offset_basis':'canonical_record_json_unicode_codepoints'}
            row.update(title='Jezikovna analiza',url='https://www.jeziknaklik.it/',
                tool_scope='Automatic prediction; candidate is conditional, not normative evidence.',
                tool_provenance=row['original_json']['analysis']['model'])
    plan=interpretation(question,context,'sl','sl')['request_plan']
    payload=_plan_runtime_payload(question,evidence,passages,request_plan=plan)
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    unit=next(row for row in registry.units if row.get('payload',{}).get('construction')
        =='finite_subordinate_clause_boundary')
    assert unit['payload']['analysis_basis']==basis and unit['unit_id'] in registry.required_unit_ids
    explained=runtime._process_fresh_answer_plan({'answer_language':'sl','blocks':[
        {'block_id':'B1','unit_id':unit['unit_id'],'paragraph_id':'P1'}],
        'no_sufficient_evidence':False},registry,payload=payload,tier='answer')
    result=connect(proposal(original,proposed),plan['comma_check'],explained,explained['applied_unit_receipts'])
    assert result['applications'][0]['relation']==expected_relation
    if expected_relation!='disagrees_with_proposal':
        assert result['links'][0]['relation']==expected_relation
        direct=[explained['citations'][i]for i in result['links'][0]['citation_indexes']]
        assert direct and all(row['source_key']=='sp2001'for row in direct)
    assert any(row['source_key']=='linguistic-analysis'for row in explained['citations'])
    assert ('V predlaganem zapisu'in explained['answer_md'])==(basis=='comma_context')


def test_actual_classla_observation_through_core_registry_renderer_and_web_links(monkeypatch):
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')
    if not configured:pytest.skip('Cross-repository fixture requires pinned CORE')
    root=Path(configured)
    for path in [root,root/'apps/api',root/'tests',root/'tests/zrc']:
        monkeypatch.syspath_prepend(str(path))
    from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
    from test_clause_punctuation import attach,classla_observation
    from tools.zrc_reference.request_interpretation import response as interpretation
    question,context,evidence,passages=attach(classla_observation(0))
    for row in evidence+passages:
        if row.get('tool_kind')=='syntax':row['span']={'char_start':0,'char_end':len(row['text']),
            'offset_basis':'canonical_record_json_unicode_codepoints'}
        if row.get('tool_kind')=='syntax':row.update(title='Jezikovna analiza',url='https://www.jeziknaklik.it/',
            tool_scope='Automatic prediction; not normative evidence.',tool_provenance=classla_observation(0)['analysis']['model'])
    for row in evidence:
        if row.get('tool_kind')=='syntax':
            for key in ('original_json','source_id','source_revision'):row.pop(key,None)
    plan=interpretation(question,context,'sl','sl')['request_plan']
    payload=_plan_runtime_payload(question,evidence,passages,request_plan=plan)
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    unit=next(row for row in registry.units if row.get('payload',{}).get('construction')
              =='finite_subordinate_clause_boundary')
    assert unit['unit_id'] in registry.required_unit_ids
    explained=runtime._process_fresh_answer_plan({'answer_language':'sl','blocks':[
        {'block_id':'B1','unit_id':unit['unit_id'],'paragraph_id':'P1'}],
        'no_sufficient_evidence':False},registry,payload=payload,tier='answer')
    original=plan['comma_check']['target']['text']
    result=connect(proposal(original,'Ko dobimo odgovor, vam sporočimo.'),
        plan['comma_check'],explained,explained['applied_unit_receipts'])
    assert result['status']=='applied' and result['unexplained_edit_indexes']==[]
    link,=result['links'];assert link['edit_start']==17 and link['relation']=='supports_proposal'
    direct=[explained['citations'][index] for index in link['citation_indexes']]
    assert direct and all(row['source_key']=='sp2001' for row in direct)
    assert all('Ta vejica se stavi ne glede' in ' '.join(row['snippet'].split()) for row in direct)
    assert any(row['source_key']=='linguistic-analysis' for row in explained['citations'])
    assert 'Ko dobimo odgovor' in explained['answer_md']
