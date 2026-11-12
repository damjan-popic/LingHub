"""Qualified input-delivery contracts with synthetic frozen sources only.

No worker, native tokenizer, running application or model endpoint is used.
Passing these checks does not establish linguistic support or improvement.
"""
from copy import deepcopy
import pytest

from pilot_backend import document_transform as doc
from pilot_backend.adapter import Gateway as HttpGateway
from pilot_backend.document_report_sources import report_section
from pilot_backend.private_translation import native_references, profile_checked
from pilot_backend.security import Failure
from pilot_backend.store import canonical,digest
from test_document_transform import Gateway, family, native_frozen, response as writer_response
from test_private_document_translation import pin as baseline_pin
from test_private_document_translation import rig, login, upload, start

VERSION='klikai.document-guidance.v1'
GUIDED='translategemma_sl_it_guided'
REQUEST_CONTRACT='klikai.qualified-guidance-translation.v1'


def guided_pin():
    base=baseline_pin();value={k:deepcopy(v)for k,v in base.items()if k!='profile_sha256'}
    value.update(profile_id=GUIDED,base_profile_sha256=base['profile_sha256'],
        request_contract=REQUEST_CONTRACT,guidance_renderer_sha256='c'*64,
        source_guidance='qualified_klikai_v1',custom_rendering=True,version='klikai.private-translation-profile.v2-guided')
    value['capabilities'].update(source_consultation=True,glossary_enforcement=False)
    return {**value,'profile_sha256':digest(value)}


def signed_packet(body):
    body={k:deepcopy(v)for k,v in body.items()if k!='packet_sha256'}
    return {**body,'packet_sha256':digest(body)}


def task_binding(task):
    return {k:deepcopy(task[k])for k in ('mode','source_language','target_language','locale',
        'source_sha256','translation_request_id','document_binding')if k in task}


def packet_receipt(packet):
    fields=('version','task_binding','source_sha256','capture_envelope_sha256','profile_sha256',
        'packet_sha256','retrieved_evidence_ids','admitted_evidence_ids','omitted','coverage',
        'support_claims','independently_checked_support','compliance')
    return {**{k:deepcopy(packet[k])for k in fields},'delivery_state':'response_received'}


def prepared_input(source,packet):
    references=native_references(packet)
    prefix='<bos><start_of_turn>user\nSynthetic protocol fixture.\nBEGIN QUALIFIED KLIKAI REFERENCES\n'+canonical(references)+'\nEND QUALIFIED KLIKAI REFERENCES\n\nBEGIN ORIGINAL DOCUMENT\n'
    prompt=prefix+source+'\nEND ORIGINAL DOCUMENT<end_of_turn>\n<start_of_turn>model\n'
    return {'native_messages':None,'rendering_contract':REQUEST_CONTRACT,
        'rendered_prompt':prompt,'rendered_prompt_sha256':doc.sha_text(prompt),
        'original_span':{'start':len(prefix),'end':len(prefix)+len(source),
            'utf8_byte_start':len(prefix.encode()),'utf8_byte_end':len((prefix+source).encode()),
            'sha256':doc.sha_text(source)},'guidance_payload':references,
        'guidance_payload_sha256':digest(references),'guidance_packet_sha256':packet['packet_sha256']}


def guided_frozen():
    gateway=Gateway();f=family('translate')
    f['request_options']['document_task'].update(translation_profile=guided_pin(),
        translation_request_id='document-synthetic-guided-0',
        document_binding={'document_id':'synthetic-guided','section_index':0})
    bundle=doc.freeze(gateway,f,{})['bundle'];task=bundle['document_task']
    match=bundle['source_bundle']['matches'][0];eid=match['evidence_id']
    packet=signed_packet({'version':VERSION,'task_binding':task_binding(task),
        'source_sha256':task['source_sha256'],'capture_envelope_sha256':digest(bundle['unified_evidence']),
        'profile_sha256':task['translation_profile']['profile_sha256'],
        'retrieved_evidence_ids':[eid],'admitted_evidence_ids':[eid],
        'sources':[{'evidence_id':eid,'parent':{'source_revision':match['capture_sha256'],
            'evidence_id':eid,'external_id':match['external_id']},
            'model_source':{'passage_id':eid,'source_slug':match['source_slug'],'title':match['title'],
                'text':match['capture']['original_text'],
                'representation':{'version':'document-source-representation.v2',
                    'kind':'complete_passage','selection_status':'no_inspected_plan'},
                'source_status':'synthetic qualified record','scope_note':'Synthetic restricted processing scope.',
                'force':'qualified_explanation'},'recorded_nomination':None}],
        'omitted':[],'coverage':'partial_context_not_complete_linguistic_review',
        'scope_assumption':'Recorded nominations and any explicit inspected plan; not automatic semantic scope certification.',
        'support_claims':[],'independently_checked_support':[],'compliance':'unproven'})
    profile=task['translation_profile']
    prepared=prepared_input(task['source_text'],packet)
    gateway.result={'contract':doc.RESPONSE_CONTRACT,'status':'draft',
        'document_result':{**{k:task[k]for k in ('mode','source_language','target_language','locale','source_sha256')},
            'proposed_text':'Proposta sintetica.','edits':[],
            'notes':['private_translation_candidate','qualified_source_guidance_supplied','semantic_inline_alignment_not_supported'],
            'requires_human_review':True,'status':'draft','validation_scope':'translation_structure_and_protected_content_only'},
        'evidence':[{'evidence_id':eid,'title':match['title']}],
        'execution':{'translation_profile':deepcopy(profile),'profile_hash':profile['profile_sha256'],
            'guidance_delivery':packet_receipt(packet)},
        'diagnostics':{'writer':{'native_translation':{'source_sha256':task['source_sha256'],
            'source_evidence_ids':[eid],'translation_profile_sha256':profile['profile_sha256']},
            'guidance_packet':packet,'prepared_input':prepared,'requests':[{'request':{'prompt':[2,10,11]},
                'rendered_prompt':prepared['rendered_prompt'],'rendered_prompt_sha256':prepared['rendered_prompt_sha256']}]}}}
    return gateway,bundle


def test_derived_profile_preserves_baseline_identity_and_has_own_hash():
    base=baseline_pin();guided=guided_pin()
    assert profile_checked(base)==base
    assert profile_checked(guided)==guided
    assert guided['base_profile_sha256']==base['profile_sha256']
    assert guided['profile_sha256']!=base['profile_sha256']
    assert guided['capabilities']['source_consultation']is True
    assert guided['capabilities']['glossary_enforcement']is False
    broken=deepcopy(guided);broken['request_contract']='changed'
    with pytest.raises(Failure):profile_checked(broken)


def test_old_unaided_native_result_stays_compatible_without_guidance_claim():
    gateway,bundle=native_frozen();result=doc.generate(gateway,bundle,'answer','auto')
    assert result['evidence']==[]
    assert 'terminology_sources_not_supplied'in result['document_result']['notes']
    assert 'guidance_delivery'not in result['execution']


def test_bound_guidance_reaches_adapter_and_remains_context_not_approved_support():
    gateway,bundle=guided_frozen();packet=deepcopy(gateway.result['diagnostics']['writer']['guidance_packet'])
    result=doc.generate(gateway,bundle,'answer','auto')
    assert result['diagnostics']['writer']['guidance_packet']==packet
    assert result['execution']['guidance_delivery']['packet_sha256']==packet['packet_sha256']
    assert result['document_result']['edits']==[]
    assert result['execution']['guidance_delivery']['compliance']=='unproven'
    assert result['execution']['guidance_delivery']['support_claims']==[]
    assert result['execution']['guidance_delivery']['independently_checked_support']==[]
    assert packet['sources'][0]['model_source']['scope_note']=='Synthetic restricted processing scope.'
    assert packet['sources'][0]['model_source']['representation']['kind']=='complete_passage'
    sent=gateway.calls[-1][1]
    assert sent['document_binding']==bundle['document_task']['document_binding']
    assert sent['text']==bundle['document_task']['source_text']
    assert 'Synthetic restricted processing scope.'not in result['document_result']['proposed_text']


def test_native_reference_wrapper_reduction_keeps_qualified_content_and_closed_mapping():
    gateway,bundle=guided_frozen()
    packet=deepcopy(gateway.result['diagnostics']['writer']['guidance_packet'])
    first=packet['sources'][0]['model_source']
    first.update(title='Synthetic qualified source',source_label='Synthetic reference',
        text='Synthetic qualified source\n\nOnly in the recorded domain; exception: retain an ambiguous referent.',
        edition=None,source_status='captured_original_record',normative_status='qualified_reference',
        retained_summary='Whole bounded frozen summary: constitutional domain; status qualified; missing labels are not unrestricted scope.',
        recorded_advice_annotations=[{'recommendation_force':'recommendation_with_exception',
            'scope_note':'Task-local sense only; no automatic replacement.','mapping_status':'proposed_needs_review'}])
    second=deepcopy(packet['sources'][0]);second['evidence_id']='unified:synthetic-second'
    second['model_source'].update(passage_id=second['evidence_id'],title='Distinct source title',
        edition='Recorded edition 2021',source_status='Qualified published advice',
        representation={'kind':'selected_excerpts_with_required_context','selection_status':'selected',
            'required_context_ranges':[[0,15]],'scope_assumption':'Inspected dependencies, not certified completeness.'},
        segments=[{'role':'governing_scope','text':'Qualified scope'},
            {'role':'recorded_advice','text':'Whole advice including its exception.'}])
    packet['sources'].append(second);packet['admitted_evidence_ids'].append(second['evidence_id'])
    before=deepcopy(packet);references=native_references(packet)
    assert packet==before
    assert [row['reference']for row in references]==['K1','K2']
    assert [row['evidence_id']for row in packet['sources']]==packet['admitted_evidence_ids']
    for row,item in zip(references,packet['sources']):
        original=item['model_source']
        for key in ('text','force','scope_note','normative_status','recorded_advice_annotations','retained_summary'):
            assert row[key]==original[key]
        assert 'passage_id'not in row and 'source_slug'not in row
    assert 'title'not in references[0] and 'edition'not in references[0] and 'source_status'not in references[0]
    assert 'representation'not in references[0]
    assert first['representation']['kind']=='complete_passage'
    assert references[1]['title']=='Distinct source title'
    for key in ('edition','source_status','representation','segments'):
        assert references[1][key]==second['model_source'][key]


@pytest.mark.parametrize('change',[
    lambda r:r['execution'].update(profile_hash='b'*64),
    lambda r:r['execution']['guidance_delivery'].update(source_sha256='b'*64),
    lambda r:r['execution']['guidance_delivery']['task_binding'].update(target_language='sl'),
    lambda r:r['execution']['guidance_delivery']['task_binding']['document_binding'].update(document_id='other'),
    lambda r:r['execution']['guidance_delivery']['task_binding']['document_binding'].update(section_index=1),
    lambda r:r['execution']['guidance_delivery']['task_binding'].update(translation_request_id='document-other-0'),
    lambda r:r['execution']['guidance_delivery'].update(packet_sha256='b'*64),
    lambda r:r['diagnostics']['writer']['guidance_packet']['sources'][0]['model_source'].update(scope_note='qualifications removed'),
    lambda r:r['diagnostics']['writer']['native_translation'].update(source_evidence_ids=['unified:unknown']),
    lambda r:r['execution']['guidance_delivery'].update(admitted_evidence_ids=['unified:unknown']),
    lambda r:r['execution']['guidance_delivery'].update(compliance='proven'),
    lambda r:r['execution']['guidance_delivery'].update(support_claims=[{'decision':'entire translation is supported'}]),
])
def test_guidance_mismatch_is_failed_validation_never_usable_unchanged_result(change):
    gateway,bundle=guided_frozen();change(gateway.result)
    with pytest.raises(Failure):doc.generate(gateway,bundle,'answer','auto')


def test_report_calls_delivered_material_input_context_not_linguistic_support():
    gateway,bundle=guided_frozen();result=doc.generate(gateway,bundle,'answer','auto')
    section={'text':bundle['document_task']['source_text'],'family_id':'synthetic-guided-family',
        'sources':result['evidence'],'document_result':result['document_result']}
    snapshot={'stages':{'generation':deepcopy(result['diagnostics'])}}
    report=report_section(section,bundle,snapshot,'/api/pilot')
    assert report['writer_input_selection']=='retained'
    assert {row['report_role']for row in report['sources']}=={'writer_input_context'}
    assert all('/sources/'in row['frozen_source_url']for row in report['sources'])


@pytest.mark.parametrize('field,value',[
    ('document_binding',{'document_id':'browser-created','section_index':0}),
    ('source_selections',[{'authority':'inspected_server_selection'}]),
    ('guidance_packet',{'source_sha256':'b'*64}),
    ('translation_profile',{'profile_id':GUIDED}),
])
def test_browser_cannot_supply_source_or_document_binding_authority(rig,field,value):
    app,client,adapter=rig;headers=login(client);identifier=upload(client,headers)
    response=start(client,headers,identifier,**{field:value})
    assert response.status_code in {400,422}
    assert not adapter.preflights and not adapter.freezes


def test_original_only_guided_preflight_does_not_certify_guidance_fit():
    gateway=object.__new__(HttpGateway);profile=guided_pin();texts=[{'text':'A','source_sha256':'a'*64}]
    observed={'contract':profile['version'],'profile_hash':profile['profile_sha256'],
        'status':'pending_guidance','scope':'source_text_only_guidance_checked_before_section_dispatch','guidance_complete':False,
        'sections':[{'source_sha256':'a'*64,'native_input_tokens':None,'input_limit':2048,'fits':None}]}
    gateway.request=lambda *args:deepcopy(observed)
    result=gateway.translation_preflight(profile,texts)
    assert result['guidance_complete']is False
    assert result['scope']=='source_text_only_guidance_checked_before_section_dispatch'


def check_direct(response,bundle):
    return doc._checked_guidance_delivery(response,bundle['document_task'],
        {r['evidence_id']:r for r in bundle['source_bundle']['matches']},bundle['unified_evidence'])


def resynchronize_packet(response,bundle):
    packet=signed_packet(response['diagnostics']['writer']['guidance_packet'])
    response['diagnostics']['writer']['guidance_packet']=packet
    response['execution']['guidance_delivery']=packet_receipt(packet)
    prepared=prepared_input(bundle['document_task']['source_text'],packet)
    response['diagnostics']['writer']['prepared_input']=prepared
    response['diagnostics']['writer']['requests'][0].update(
        rendered_prompt=prepared['rendered_prompt'],rendered_prompt_sha256=prepared['rendered_prompt_sha256'])


def test_direct_delivery_validator_returns_only_the_verified_receipt():
    gateway,bundle=guided_frozen()
    assert check_direct(gateway.result,bundle)==gateway.result['execution']['guidance_delivery']


@pytest.mark.parametrize('kind',['parent_revision','capture_envelope','duplicate_id','unknown_omitted',
    'unclosed_retrieved','claims','independently_checked','no_admitted_content'])
def test_self_consistent_packet_hash_cannot_override_frozen_identity_or_closed_ids(kind):
    gateway,bundle=guided_frozen();value=gateway.result;packet=value['diagnostics']['writer']['guidance_packet']
    eid=packet['admitted_evidence_ids'][0]
    if kind=='parent_revision':packet['sources'][0]['parent']['source_revision']='d'*64
    elif kind=='capture_envelope':packet['capture_envelope_sha256']='d'*64
    elif kind=='duplicate_id':
        packet['admitted_evidence_ids'].append(eid);packet['sources'].append(deepcopy(packet['sources'][0]))
    elif kind=='unknown_omitted':packet['omitted'].append({'evidence_id':'unified:unknown','reason':'not applicable'})
    elif kind=='unclosed_retrieved':packet['retrieved_evidence_ids'].append('unified:unknown')
    elif kind=='claims':packet['support_claims']=[{'claim':'whole translation supported'}]
    elif kind=='independently_checked':packet['independently_checked_support']=[{'claim':'approved'}]
    elif kind=='no_admitted_content':
        packet['admitted_evidence_ids']=[];packet['sources']=[]
        packet['omitted']=[{'evidence_id':eid,'reason':'not applicable'}]
        value['evidence']=[];value['diagnostics']['writer']['native_translation']['source_evidence_ids']=[]
    resynchronize_packet(value,bundle)
    with pytest.raises(Failure):check_direct(value,bundle)


@pytest.mark.parametrize('field,value',[('rights_mode','linked_only'),('context_eligible',False)])
def test_known_but_ineligible_source_does_not_become_admitted_guidance(field,value):
    gateway,bundle=guided_frozen();bundle['source_bundle']['matches'][0][field]=value
    with pytest.raises(Failure):check_direct(gateway.result,bundle)


@pytest.mark.parametrize('part',['original_span','rendered_hash','request_rendering','guidance_payload'])
def test_delivery_requires_the_effective_rendered_input_not_just_ids(part):
    gateway,bundle=guided_frozen();writer=gateway.result['diagnostics']['writer'];prepared=writer['prepared_input']
    if part=='original_span':prepared['original_span']['start']+=1
    elif part=='rendered_hash':prepared['rendered_prompt_sha256']='d'*64
    elif part=='request_rendering':writer['requests'][0]['rendered_prompt']='Different actual effective input'
    elif part=='guidance_payload':prepared['guidance_payload'][0]['scope_note']='qualification dropped'
    with pytest.raises(Failure):check_direct(gateway.result,bundle)


def test_unrendered_guidance_payload_cannot_be_reported_as_delivered():
    gateway,bundle=guided_frozen();writer=gateway.result['diagnostics']['writer']
    prepared=writer['prepared_input'];source=bundle['document_task']['source_text']
    # All hashes and source offsets are internally consistent, and the packet
    # still lists qualified references. This effective prompt omits them.
    prefix='<bos><start_of_turn>user\nBEGIN ORIGINAL DOCUMENT\n'
    rendered=prefix+source+'\nEND ORIGINAL DOCUMENT<end_of_turn>\n<start_of_turn>model\n'
    prepared.update(rendered_prompt=rendered,rendered_prompt_sha256=doc.sha_text(rendered))
    prepared['original_span'].update(start=len(prefix),end=len(prefix)+len(source),
        utf8_byte_start=len(prefix.encode()),utf8_byte_end=len((prefix+source).encode()))
    writer['requests'][0].update(rendered_prompt=rendered,rendered_prompt_sha256=doc.sha_text(rendered))
    with pytest.raises(Failure):check_direct(gateway.result,bundle)


@pytest.mark.parametrize('binding',[
    {'document_id':'','section_index':0},{'document_id':3,'section_index':0},
    {'document_id':'synthetic','section_index':True},{'document_id':'synthetic','section_index':-1},
    {'document_id':'synthetic','section_index':100},
    {'document_id':'synthetic','section_index':0,'authority':'browser'},
])
def test_malformed_server_binding_is_not_frozen_as_a_valid_task(binding):
    t=family()['request_options']['document_task'];t['document_binding']=binding
    with pytest.raises(Failure):doc.task_checked(t)


def test_source_guidance_does_not_convert_noop_edit_into_unchanged_review():
    gateway=Gateway();bundle=doc.freeze(gateway,family(),{})['bundle'];source=bundle['document_task']['source_text']
    gateway.result=writer_response(bundle,edits=[],proposed=source)
    valid=doc.generate(gateway,bundle,'answer','auto')
    assert valid['document_result']['proposed_text']==source
    assert valid['document_result']['edits']==[] and valid['document_result']['requires_human_review']is True
    eid=bundle['source_bundle']['matches'][0]['evidence_id']
    gateway.result=writer_response(bundle,proposed=source,edits=[{'start':0,'end':len(source),
        'original':source,'replacement':source,'explanation':'Synthetic no-op must reject',
        'evidence_ids':[eid],'support_quote':'ima čebelo'}])
    with pytest.raises(Failure):doc.generate(gateway,bundle,'answer','auto')


def test_delivery_claim_does_not_override_failed_translation_export_policy():
    from pilot_backend.document_files import draft_blocks
    report={'state':'failed','options':{'mode':'translate'},'sections':[{'index':0,
        'text':'Original','state':'failed','document_result':None,
        'execution':{'guidance_delivery':{'delivery_state':'response_received'}},
        'limitations':[{'code':'document_transform_failed'}]}]}
    with pytest.raises(Failure):draft_blocks(report)
