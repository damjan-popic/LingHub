"""Retained genuine explicit senses; new render, unchanged selection evidence."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import gzip,json,os,zipfile
import xml.etree.ElementTree as ET
import pytest
from tools.zrc_reference import pilot_answer_plan as plans,pilot_lexical_relations as lexical
from tools.zrc_reference.common import canonical_json,value_hash
from tools.zrc_reference.pilot_tool_evidence import writer_unit
from tools.zrc_reference.request_interpretation import authoritative_request_plan
from test_bounded_recomposition import _plan_runtime,_plan_runtime_payload
from test_layered_consultation import delivery
from pilot_backend.jobs import render_result
from pilot_backend.presentation import tester_view as public_view
from pilot_backend.document_files import report_text,report_docx


def retained():return json.loads(gzip.decompress((Path(__file__).parent/'fixtures/explicit_synonym_consumer_v1.json.gz').read_bytes()))


def consumer(case,*,indices=None,locale='sl',passages=None,plan=None,reverse=False,same_paragraph=False):
    passages=deepcopy(case['passages']if passages is None else passages)
    evidence=deepcopy(case['evidence'])
    if not evidence or passages!=case['passages']:
        evidence=[{**deepcopy(s),'evidence_id':'E'+str(i+1),'supports':[{'support_id':'E'+str(i+1)+'.S1',
            'text':s['text'],'char_start':0,'char_end':len(s['text']),'source_role':'source_passage'}]}for i,s in enumerate(passages)]
    request_plan=deepcopy(plan or case['request_plan'])
    payload=_plan_runtime_payload(case['question'],evidence,passages,request_plan=request_plan)
    payload.update(context=case['context'],locale=locale)
    runtime=_plan_runtime();registry=runtime._answer_registry(payload,'answer')
    units=registry.units
    if indices is not None:units=[units[i]for i in indices]
    if reverse:units=list(reversed(units))
    raw={'answer_language':locale,'blocks':[{'block_id':'B'+str(i+1),'paragraph_id':'P1'if same_paragraph else'P'+str(i+1),'unit_id':u['unit_id']}for i,u in enumerate(units)],'no_sufficient_evidence':False}
    result=runtime._process_fresh_answer_plan(raw,registry,payload=payload,tier='answer')
    return registry,result,render_result(delivery(result))[0]


def save(name,value):
    folder=os.environ.get('KLIKAI_EXPLICIT_RECEIPTS')
    if folder:
        Path(folder,name+'.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
        if 'answer'in value:Path(folder,name+'.txt').write_text(value['answer']['answer_md']+'\n')


@pytest.mark.parametrize('locale',['sl','it'])
def test_exact_actual_all_senses_still_selected_with_one_common_frame(locale):
    case=retained()[0];registry,result,web=consumer(case,locale=locale)
    assert len(registry.units)==len(result['claims'])==len(result['citations'])==4
    assert registry.required_unit_ids==[]
    assert all(u['payload']['version']==lexical.EXPLICIT_SYNONYM_VERSION for u in registry.units)
    assert {c['evidence_id']for c in result['citations']}=={pid for row in case['actual_selected_mapping']for pid in row['exact_passage_ids']}
    assert [g['sense_id']for u in registry.units for g in u['payload']['groups']]==[
        g['sense_id']for u in case['baseline_registry']['units']for g in u['payload']['groups']]
    text=result['answer_md'];intro='Per «hiter»'if locale=='it'else'Za »hiter«'
    assert text.count(intro)==1
    assert text.count('I dati sono preparati automaticamente.'if locale=='it'else'Podatki so samodejno pripravljeni.')==1
    assert text.count('La sostituzione adatta dipende'if locale=='it'else'Ustrezna zamenjava je odvisna')==1
    for word in ('uren','učinkovit','nagel','ekspresen','pravo','železnica'):assert word in text
    for claim in result['claims']:
        citations=[c for c in result['citations']if c['claim_id']==claim['claim_id']]
        unit=next(u for u in registry.units if u['unit_id']==claim['unit_id'])
        assert set(claim['support_ids'])=={c['support_id']for c in citations}
        assert {c['evidence_id']for c in citations}=={b['passage_id']for b in unit['bindings']}
        labels=unit['payload']['groups'][0]['metadata']
        if labels:
            label=lexical._metadata_display(labels,lambda v:v,locale)
            assert claim['text'].index(label)<claim['text'].index('- ')
        assert 'ssss-automatic'in claim['text']and '2.2'in claim['text']
    assert 'answer_presentation'not in result # explicit content stays directly open
    assert web['answer_md']==text and len(web['blocks'])==4
    save('actual-ALL-'+locale,{'evidence_class':'OFFLINE_NEW_RENDER_OF_RETAINED_ACTUAL_ALL_SELECTION',
        'actual_raw_plan_unchanged':case['actual_raw_plan'],'actual_selection_changed':False,
        'before_answer':case['baseline_result']['answer_md'],'before_equals_actual':case['baseline_equals_actual_answer'],
        'answer':web,'registry':registry.model_dump(),'actual_operations':0})


@pytest.mark.parametrize('indices',[[0],[2],[2,3],[0,1,2,3]])
@pytest.mark.parametrize('same_paragraph',[False,True])
def test_one_several_all_senses_and_paragraphs_remain_complete(indices,same_paragraph):
    case=retained()[0];registry,result,web=consumer(case,indices=indices,same_paragraph=same_paragraph)
    assert len(result['claims'])==len(indices)==len(result['citations'])
    assert result['answer_md'].count('Za »hiter«')==1
    assert all(c['text']for c in result['claims'])
    assert all(len(b['evidence_ids'])==1 for b in web['blocks'])


def test_preserves_selected_order_and_explicit_all_groups_fast_path():
    case=retained()[0];registry,result,_=consumer(case,reverse=True)
    assert [c['unit_id']for c in result['claims']]==[u['unit_id']for u in reversed(registry.units)]
    assert result['answer_md'].index('ekspresen')<result['answer_md'].index('nagel')<result['answer_md'].index('uren')
    plain=deepcopy(case);plain['question']='Sopomenke besede hiter?'
    plain['request_plan']=authoritative_request_plan(plain['question'],'','sl','sl')
    registry,result,web=consumer(plain)
    assert len(registry.units)==1 and len(registry.units[0]['payload']['groups'])==4
    assert len(result['citations'])==4 and result['answer_md'].count('Za »hiter«')==1
    assert _plan_runtime()._single_required_answer_plan(registry,'sl')is not None
    save('explicit-all-fast-path',{'contrast':'mechanical uncontextual all-group request over the same genuine four senses','answer':web})


def synthetic_source(source,damage):
    source=deepcopy(source);unit=source['original_json'];fields=unit['fields']
    if damage=='status':
        fields['head_status']='manual';unit['editorial_status'].update(raw='manual',normalized='manual')
        fields['head_metadata'][0]['text']='manual'
    elif damage=='head':fields['headword']='MECHANICAL second head'
    elif damage=='condition':fields['head_metadata'].append({'tag':'note','attributes':{},'children':[],
        'text':'MECHANICAL condition: restricted register','tail':''})
    elif damage=='unknown':fields['unresolved_material_condition']={'notice':'MECHANICAL unknown applicability'}
    elif damage=='edition':unit['provenance']['version']='MECHANICAL edition'
    elif damage=='rights':unit['provenance']['source']['license']='MECHANICAL separate rights'
    elif damage=='relation':fields['sense']['relations'][0]['synonym_type']='MECHANICAL related relation'
    elif damage=='context':return source
    source['text']=canonical_json(writer_unit(unit))
    source['source_revision']=value_hash(unit);source['span']['record_sha256']=value_hash(unit)
    return source


@pytest.mark.parametrize('damage',['status','head','condition','unknown','edition','rights','relation','revision'])
def test_incompatible_local_qualifications_and_owner_boundaries_are_not_smoothed(damage):
    case=retained()[0];sources=deepcopy([p for p in case['passages']if p.get('tool_kind')=='synonyms'][:2]);sources[1]=synthetic_source(sources[1],damage)
    registry,result,web=consumer(case,passages=sources)
    assert len(result['claims'])==2 and len(result['citations'])==2
    # Different source revision and native parent/conditions retain their frames.
    assert result['answer_md'].count('Za »hiter«')==2
    if damage in {'head','condition','unknown','edition','relation'}:assert 'MECHANICAL'in result['claims'][1]['text']
    if damage=='status':assert 'status vira: manual'in result['claims'][1]['text']
    save('mechanical-'+damage,{'contrast':'explicitly synthetic source qualification/identity control, not new source evidence',
                              'answer':web,'registry':registry.model_dump()})


def test_separate_request_contexts_keep_independent_framing():
    case=retained()[0];registry,_,_=consumer(case);value=registry.model_dump()
    second=value['units'][1];second['payload']['request_context']['text']='X'+second['payload']['request_context']['text'][1:]
    second['builder_output_sha256']=value_hash(second['payload']);second['unit_id']='U'+value_hash({k:v for k,v in second.items()if k!='unit_id'})[:16]
    value['registry_sha256']=value_hash({k:v for k,v in value.items()if k!='registry_sha256'})
    raw={'answer_language':'sl','blocks':[{'block_id':'B'+str(i+1),'paragraph_id':'P1','unit_id':u['unit_id']}for i,u in enumerate(value['units'][:2])],'no_sufficient_evidence':False}
    result=plans.process_answer_plan(raw,value,locale='sl')
    assert result['answer_md'].count('Za »hiter«')==2


def test_nonadjacent_compatible_senses_do_not_cross_another_owner():
    case=retained()[0];registry,_,_=consumer(case);value=registry.model_dump()
    middle=value['units'][1];middle['payload']['request_context']['text']='X'+middle['payload']['request_context']['text'][1:]
    middle['builder_output_sha256']=value_hash(middle['payload']);middle['unit_id']='U'+value_hash({k:v for k,v in middle.items()if k!='unit_id'})[:16]
    value['registry_sha256']=value_hash({k:v for k,v in value.items()if k!='registry_sha256'})
    raw={'answer_language':'sl','blocks':[{'block_id':'B'+str(i+1),'paragraph_id':'P1','unit_id':u['unit_id']}for i,u in enumerate(value['units'][:3])],'no_sufficient_evidence':False}
    result=plans.process_answer_plan(raw,value,locale='sl')
    assert result['answer_md'].count('Za »hiter«')==3 and len(result['claims'])==3


def test_same_spelling_without_single_parent_scope_does_not_join_owners():
    case=retained()[0];sources=deepcopy([p for p in case['passages']if p.get('tool_kind')=='synonyms'][:2])
    for source in sources:
        source['original_json']['fields']['coverage'].update(entries_returned=2,entries_total=2)
        source['text']=canonical_json(writer_unit(source['original_json']))
        source['source_revision']=value_hash('mechanical shared capture with two parent entries')
    _,result,_=consumer(case,passages=sources)
    assert result['answer_md'].count('Za »hiter«')==2 and len(result['citations'])==2


def test_another_retained_lexical_target_is_substantive_and_open():
    case=retained()[1];registry,result,web=consumer(case)
    assert len(result['claims'])==1 and len(result['citations'])==1
    assert 'črta, pas'in result['answer_md']and 'proga'in result['answer_md']
    assert 'answer_presentation'not in result
    save('retained-proga-control',{'contrast':case['control_label'],'answer':web})


def test_all_historical_v3_ids_text_hashes_and_older_source_versions_remain_exact():
    case=retained()[0];registry=plans.AnswerUnitRegistry.model_validate(case['baseline_registry'])
    assert registry.model_dump()==case['baseline_registry']
    assert plans.process_answer_plan(case['baseline_plan'],registry,locale='sl')==case['baseline_bounded']
    assert case['baseline_equals_actual_answer']and case['baseline_result']['answer_md']==case['actual_saved_answer']
    assert all(lexical.common_presentation_parts(u['payload'],'sl',lambda v:v)is None for u in registry.units)
    old_path=Path(plans.__file__).resolve().parents[2]/'tests/zrc/fixtures/retained_scoped_relations_v44.json.gz'
    old=next(c for c in json.loads(gzip.decompress(old_path.read_bytes()))['cases']if c['case']=='SYN-P')
    historical=plans.AnswerUnitRegistry.model_validate(old['original_registry']);unit,=historical.units
    raw={'answer_language':'sl','blocks':[{'block_id':'B1','paragraph_id':'P1','unit_id':unit['unit_id']}],'no_sufficient_evidence':False}
    result=plans.process_answer_plan(raw,historical,locale='sl')
    assert result['answer_md']==old['original_selection_catalog']['units'][0]['rendered_text']
    assert historical.model_dump()==old['original_registry']and len(result['citations'])==4


@pytest.mark.parametrize('index',[0,1])
def test_frozen_implicit_canonical_result_and_layered_identity_are_preserved(index):
    data=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/layered_consumer_inputs_v1.json.gz').read_bytes()))[index]
    result=plans.process_answer_plan(data['raw'],data['registry'],locale='sl')
    assert result['answer_md']==data['actual_result']['answer_md']
    assert result['rendered_result_sha256']==data['actual_result']['rendered_result_sha256']
    expected=['91410901774335b5aa3f4fe1134f1446c5bcf6326e9330efdedf199ef48126e7',
              '24158357a9e1dc96fe3f9042266ecb944cdfe75257f78fcc97c6a609820cb3e5']
    assert plans.answer_plan_presentation(result,data['registry'])['presentation_sha256']==expected[index]


def test_complete_existing_txt_word_report_contains_every_new_body_and_destination():
    _,_,web=consumer(retained()[0]);sources=[{**e,'frozen_source_url':'https://retained.invalid/'+str(i)}for i,e in enumerate(web['evidence'])]
    report={'locale':'sl','mode':'reference','name':'Offline explicit-synonym export fixture','state':'complete',
            'sections':[{'index':0,'text':'Retained explicit input','output':web['answer_md'],'sources':sources,'state':'complete'}]}
    txt=report_text(report).decode();docx=report_docx(report)
    with zipfile.ZipFile(BytesIO(docx))as package:
        xml=ET.fromstring(package.read('word/document.xml'))
        def text(node):return '\n'if node.tag.endswith('}br')else(node.text or'')+''.join(text(c)+(c.tail or'')for c in node)
        word=text(xml);rels=package.read('word/_rels/document.xml.rels').decode()
    assert web['answer_md']in txt and web['answer_md']in word
    for source in sources:assert source['frozen_source_url']in txt and source['frozen_source_url']in rels
    folder=os.environ.get('KLIKAI_EXPLICIT_RECEIPTS')
    if folder:Path(folder,'complete-report.txt').write_text(txt);Path(folder,'complete-report.docx').write_bytes(docx)
