"""Offline views of exact retained actual selections; no fresh language work."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import gzip,json,os,zipfile
import xml.etree.ElementTree as ET
import pytest
from types import SimpleNamespace
from tools.zrc_reference import pilot_answer_plan as plans
from tools.zrc_reference.common import value_hash
from pilot_backend.jobs import render_result
from pilot_backend.presentation import checked_answer_presentation,complete_answer_text,tester_view as reader_view
from pilot_backend.document_files import report_text,report_docx
from pilot_backend import unified as web
from test_coherent_consultation import retained,prepared,select,mechanical_variant
from test_ordinary_contextual_consultation import prepared as explicit_prepared,selected_output


def actual():
    return json.loads(gzip.decompress((Path(__file__).parent/'fixtures/layered_consumer_inputs_v1.json.gz').read_bytes()))


def replay(case,raw=None,registry=None):
    registry=plans.AnswerUnitRegistry.model_validate(registry or case['registry'])
    bounded=plans.process_answer_plan(raw or case['raw'],registry,locale=registry.locale)
    view=plans.answer_plan_presentation(bounded,registry)
    value=deepcopy(case['actual_result'])
    for key in ('answer_md','short_answer','display_blocks','claims','registry_sha256','rendered_result_sha256','raw_plan'):
        value[key]=deepcopy(bounded[key])
    # Retain each actual source destination; only mechanical variant block and
    # claim identities follow the replayed plan. Never infer source selection.
    cited=[]
    for block in bounded['blocks']:
        unit=next(u for u in registry.units if u['unit_id']==block['unit_id'])
        for binding in unit['bindings']:
            source=next(c for c in case['actual_result']['citations']if c['evidence_id']==binding['passage_id'])
            cited.append({**deepcopy(source),'block_id':block['block_id'],'claim_id':block['claim_id'],'unit_id':block['unit_id']})
    value['citations']=cited
    claim_templates={c['unit_id']:c for c in case['actual_result']['claims']}
    value['claims']=[{**deepcopy(claim_templates[c['unit_id']]),**c}for c in bounded['claims']]
    selected_sources={c['evidence_id']for c in cited}
    value['evidence']=[e for e in value['evidence']if web.evidence_id(e)in selected_sources]
    if view:value['answer_presentation']=view
    return bounded,view,value


def delivery(value):
    # Existing WEB consumer only. An in-memory transfer returns the saved CORE
    # result; no gateway, source preparation, network, auth or store is used.
    bundle={'contract':web.CONTRACT,'matches':deepcopy(value['evidence']),
        'gateway_bundle':{'retained_consumer_only':True},'retrieval':{'status':'complete'},'locale':'sl'}
    bundle['snapshot_sha256']=value_hash(bundle)
    result=web._generate(SimpleNamespace(request=lambda *args:deepcopy(value)),bundle,'answer','qwen')
    assert result['status']==value['status'],json.dumps(result.get('generation'),ensure_ascii=False)
    return result


def mechanical_ui_fixture(result):
    # Synthetic owner/scope contrasts are presentation controls, not authentic
    # WEB provenance acceptance. Keep their exact selected citation mapping.
    value=deepcopy(result)
    for i,e in enumerate(value['evidence']):e.update(evidence_id=web.evidence_id(e),local_number=i+1)
    for b in value['display_blocks']:
        b['evidence_ids']=list(dict.fromkeys(c['evidence_id']for c in value['citations']if c['block_id']==b['block_id']))
    return render_result(value)[0]


def save(name,value):
    folder=os.environ.get('KLIKAI_LAYERED_RECEIPTS')
    if folder:Path(folder,name+'.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


@pytest.mark.parametrize('index',[0,1])
def test_exact_actual_all_unchanged_canonical_and_complete_new_view(index):
    case=actual()[index];before=deepcopy(case);bounded,view,value=replay(case)
    previous=case['actual_result']
    assert bounded['answer_md']==previous['answer_md']
    assert bounded['display_blocks']==previous['display_blocks']
    assert bounded['raw_plan']==previous['raw_plan']
    assert bounded['registry_sha256']==previous['registry_sha256']
    assert bounded['rendered_result_sha256']==previous['rendered_result_sha256']
    for current,old in zip(bounded['claims'],previous['claims']):
        assert all(current[k]==old[k]for k in current)
    assert value['citations']==previous['citations']
    section,=view['sections']
    assert len(section['primary'])==1 and len(section['additional'])==index+1
    assert all(p['role']=='relation'for p in section['primary'])
    assert all(p['role']=='head_context'for p in section['additional'])
    assert section['intro']['text'].count('Izvirnik:')==1
    assert 'popolni zapisi'in section['footer']['text']
    assert ('Želite razjasniti'in section['footer']['text'])==(index==0)
    assert all('popolni zapisi'not in p['text']and 'Želite razjasniti'not in p['text']for p in section['additional'])
    main=section['primary'][0]['text']
    if index==0:
        assert all(s in main for s in ('pripravili pogodbo','podpisali','nista','Zaimenska navezava »je«','8 od 1095','8 od 578','neodvisna potrditev'))
    else:assert 'hitri vlak'in main and 'napoved povezave'in main and 'prednostne zamenjave'in main
    stored,display=render_result(delivery(value))
    old_stored,old_display=render_result(delivery(previous))
    assert stored['blocks']==old_stored['blocks']and display==old_display
    assert stored['answer_presentation']==view
    public=reader_view({**stored,'run_id':'retained-fixture','display_sha256':display},'fixture','fixture')
    assert public['answer_presentation']==view and public['answer_md']==previous['answer_md']
    assert public['blocks']==reader_view(old_stored,'fixture','fixture')['blocks']
    assert checked_answer_presentation(stored)==view
    full=complete_answer_text(stored)
    assert full.count(view['additional_label'])==1
    for part in section['primary']+section['additional']:
        assert part['text']in full
        assert any(c['block_id']==part['block_id']for c in stored['citations'])
    assert case==before
    save(case['case']+'-actual-view',{'evidence_class':'OFFLINE_PRESENTATION_OF_RETAINED_ACTUAL_ALL',
        'provenance':case['provenance'],'canonical_fields_unchanged':True,'selected_claims':len(stored['claims']),
        'selected_citations':len(stored['citations']),'presentation':view,'public_tier':public,'complete_export':full,
        'actual_operations':0})


@pytest.mark.parametrize('index',[0,1])
@pytest.mark.parametrize('mode',['main','head','all_reversed','same_paragraph'])
def test_retained_combinations_order_and_context_only_stay_substantive(index,mode):
    case=actual()[index];units=case['registry']['units']
    selected=[u for u in units if u['payload']['contribution_kind']==('relation'if mode=='main'else'head_context')]if mode in {'main','head'}else list(reversed(units))
    raw={'answer_language':'sl','blocks':[{'block_id':'B'+str(i+1),'paragraph_id':'P1'if mode=='same_paragraph'else'P'+str(i+1),'unit_id':u['unit_id']}for i,u in enumerate(selected)],'no_sufficient_evidence':False}
    bounded,view,value=replay(case,raw);section,=view['sections']
    assert len(section['primary'])==(len(selected)if mode=='head'else 1)
    assert len(section['additional'])==(len(selected)-1 if mode in {'all_reversed','same_paragraph'}else 0)
    assert {p['unit_id']for p in section['primary']+section['additional']}=={u['unit_id']for u in selected}
    assert all(p['text']for p in section['primary'])and checked_answer_presentation(value)==view
    if mode=='head':assert all('ne gre za predlagano zamenjavo'in p['text']for p in section['primary'])
    save(case['case']+'-'+mode,{'contrast':'mechanical retained selection, not new model output','presentation':view,'value':render_result(delivery(value))[0],'complete_export':complete_answer_text(value)})


@pytest.mark.parametrize('damage',['range','role','source','hash','answer','claim','registry'])
def test_stale_or_tampered_projection_keeps_full_canonical_view(damage):
    _,view,value=replay(actual()[0]);part=view['sections'][0]['additional'][0]
    if damage=='range':part['start']+=1
    elif damage=='role':part['role']='relation'
    elif damage=='source':part['support_ids']=['not-selected']
    elif damage=='answer':value['answer_md']+=' changed'
    elif damage=='claim':part['claim_id']='missing'
    elif damage=='registry':value['registry_sha256']='0'*64
    else:view['presentation_sha256']='0'*64
    if damage!='hash':view['presentation_sha256']=value_hash({k:v for k,v in view.items()if k!='presentation_sha256'})
    assert checked_answer_presentation(value)is None
    assert complete_answer_text(value)==value['answer_md']
    assert 'answer_presentation'not in render_result(value)[0]


def test_multiple_relations_conditions_remain_visible_without_first_one_quota():
    case=retained()[0];sources=deepcopy(case['passages'])
    index=next(i for i,p in enumerate(sources)if p.get('original_json',{}).get('entry_id')=='642994')
    sources[index]=mechanical_variant(sources[index],'unknown_component')
    payload,runtime,registry=prepared(case,sources)
    result=select(payload,runtime,registry,list(reversed(registry.units)))
    view=result['answer_presentation'];section,=view['sections']
    assert len(section['primary'])==2 and len(section['additional'])==1
    assert sum('MECHANICAL component condition'in p['text']for p in section['primary'])==1
    assert len(result['claims'])==3 and len(result['citations'])==3
    assert checked_answer_presentation(result)==view
    save('multiple-relations-unknown-condition',{'contrast':'synthetic qualification contrast over retained owners; UI fixture is not native WEB provenance acceptance','presentation':view,'result':mechanical_ui_fixture(result)})


def test_distinct_retained_scope_groups_do_not_share_a_global_heading_or_frame():
    cases=actual();registry=deepcopy(cases[0]['registry'])
    registry['units']+=deepcopy(cases[1]['registry']['units'])
    registry['registry_sha256']=value_hash({k:v for k,v in registry.items()if k!='registry_sha256'})
    registry=plans.AnswerUnitRegistry.model_validate(registry)
    raw={'answer_language':'sl','blocks':[{'block_id':'B'+str(i+1),'paragraph_id':'P1','unit_id':u['unit_id']}for i,u in enumerate(reversed(registry.units))],'no_sufficient_evidence':False}
    bounded=plans.process_answer_plan(raw,registry,locale='sl');view=plans.answer_plan_presentation(bounded,registry)
    assert len(view['sections'])==2
    assert len({s['context_key']for s in view['sections']})==2
    assert sorted(len(s['additional'])for s in view['sections'])==[1,2]
    for section in view['sections']:
        assert len(section['primary'])==1
        assert ('Želite razjasniti'in section['footer']['text'])==('Ana in Maja'in section['intro']['text'])
    citations=[]
    for block in bounded['blocks']:
        source=next(c for case in cases for c in case['actual_result']['citations']if c['unit_id']==block['unit_id'])
        for case in cases:
            citations.extend({**deepcopy(c),'block_id':block['block_id'],'claim_id':block['claim_id']}
                             for c in case['actual_result']['citations']if c['unit_id']==block['unit_id'])
    value={**bounded,'status':'validated','answer_kind':'generated','answer_support':'supported',
        'answer_presentation':view,'citations':citations,
        'evidence':[deepcopy(e)for case in cases for e in case['actual_result']['evidence']]}
    save('distinct-scopes',{'contrast':'mechanical composition of two retained scope groups; not a fresh request or native WEB provenance acceptance',
                            'presentation':view,'value':mechanical_ui_fixture(value)})


@pytest.mark.parametrize('question,source',[('Kolokacije besede pogodbo?','COLL-P'),('Sopomenke besede hiter?','SYN-P')])
def test_explicit_requested_lists_remain_open_without_implicit_projection(question,source):
    _,payload,runtime,registry=explicit_prepared(question,case=source)
    result=selected_output(payload,runtime,registry)
    assert result['answer_md']and 'answer_presentation'not in result
    assert complete_answer_text(result)==result['answer_md']
    save('explicit-'+str(abs(sum(map(ord,question)))),{'question':question,'answer':render_result(delivery(result))[0]})


@pytest.mark.parametrize('index',[0,1])
def test_historical_v1_v2_and_unbound_result_keep_exact_existing_view(index):
    old=retained()[index];registry=plans.AnswerUnitRegistry.model_validate(old['historical_registry'])
    result=plans.process_answer_plan(old['historical_selection'],registry,locale='sl')
    assert result['answer_md']==old['historical_answer']and plans.answer_plan_presentation(result,registry)is None
    canonical=plans.process_answer_plan(actual()[index]['raw'],actual()[index]['registry'],locale='sl')
    canonical['registry_sha256']='0'*64
    assert plans.answer_plan_presentation(canonical,actual()[index]['registry'])is None
    empty={'answer_language':'sl','blocks':[],'no_sufficient_evidence':True}
    result=plans.process_answer_plan(empty,actual()[index]['registry'],locale='sl')
    assert result['answer_md']==''and plans.answer_plan_presentation(result,actual()[index]['registry'])is None


@pytest.mark.parametrize('index',[0,1])
def test_italian_retained_presentation_localizes_without_new_selection(index):
    case=actual()[index];registry=deepcopy(case['registry']);registry['locale']='it'
    registry['registry_sha256']=value_hash({k:v for k,v in registry.items()if k!='registry_sha256'})
    raw=deepcopy(case['raw']);raw['answer_language']='it'
    _,view,value=replay(case,raw,registry)
    assert view['additional_label'].startswith('Contesto lessicale aggiuntivo')
    assert view['sections'][0]['intro']['text'].startswith('Testo proposto:')
    assert checked_answer_presentation(value)==view
    save(case['case']+'-italian',{'contrast':'mechanical locale rendering of retained plan; not new model output','presentation':view,'value':render_result(delivery(value))[0]})


@pytest.mark.parametrize('index',[0,1])
def test_complete_txt_word_report_keeps_selected_bodies_and_source_destinations(index):
    case=actual()[index];_,view,value=replay(case);stored,_=render_result(delivery(value))
    sources=[{**e,'frozen_source_url':'https://retained.invalid/source/'+str(i)}for i,e in enumerate(stored['evidence'])]
    report={'locale':'sl','mode':'reference','name':'Retained presentation control','state':'complete',
        'sections':[{'index':0,'text':case['registry']['units'][0]['payload']['target_scope']['text'],
                     'output':stored['answer_md'],'complete_output':complete_answer_text(stored),
                     'sources':sources,'state':'complete'}]}
    text=report_text(report).decode();docx=report_docx(report)
    with zipfile.ZipFile(BytesIO(docx))as z:
        xml=ET.fromstring(z.read('word/document.xml'))
        def word_text(node):
            if node.tag.endswith('}br'):return '\n'
            if node.tag.endswith('}tab'):return '\t'
            return (node.text or'')+''.join(word_text(child)+(child.tail or'')for child in node)
        word=word_text(xml);rels=z.read('word/_rels/document.xml.rels').decode()
    for section in view['sections']:
        for part in section['primary']+section['additional']:
            assert part['text']in text and part['text']in word
    assert view['additional_label']in text and view['additional_label']in word
    for source in sources:assert source['frozen_source_url']in text and source['frozen_source_url']in rels
    folder=os.environ.get('KLIKAI_LAYERED_RECEIPTS')
    if folder:
        Path(folder,case['case']+'-report.txt').write_text(text)
        Path(folder,case['case']+'-report.docx').write_bytes(docx)
