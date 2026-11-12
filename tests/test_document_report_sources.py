import hashlib
import json
from pilot_backend.document_report_sources import retained_writer_ids,report_section


def snapshot(text,ids):
    data={'contract':'klikai.document-transform.v1','source_text':text,
          'task':{'source_sha256':hashlib.sha256(text.encode()).hexdigest()},
          'source_evidence':[{'passage_id':eid}for eid in ids]}
    return {'stages':{'generation':{'writer':{'requests':[{'request':{
        'messages':[{'role':'user','content':json.dumps(data)}]}}]}}}}


def test_report_keeps_input_context_separate_from_claimed_support_and_retrieval():
    text='Source';section={'text':text,'family_id':'family-1','sources':[
        {'evidence_id':'proof','title':'Proof'},{'evidence_id':'extra','title':'Retrieved'}],
        'document_result':{'edits':[{'evidence_ids':['proof']}]}}
    bundle={'source_bundle':{'matches':[{'evidence_id':'writer','title':'Writer input',
        'source':{'source_revision':'revision','locator':{'capture_sha256':'revision'}}}]}}
    result=report_section(section,bundle,snapshot(text,['writer']),'https://example.org/pilot/api/pilot')
    rows={r['evidence_id']:r for r in result['sources']}
    assert rows['proof']['report_role']=='edit_support'
    assert rows['extra']['report_role']=='retrieval_context'
    assert rows['writer']['report_role']=='writer_input_context'
    assert rows['writer']['source_revision']=='revision'
    assert rows['writer']['frozen_source_url']=='https://example.org/pilot/api/pilot/families/family-1/sources/writer?tier=answer'
    assert len(section['sources'])==2


def test_missing_or_different_operand_trace_does_not_invent_selection():
    assert retained_writer_ids({},'Source')is None
    assert retained_writer_ids(snapshot('Other',['writer']),'Source')is None
    section={'text':'Source','family_id':'family-1','sources':[{'evidence_id':'extra'}]}
    result=report_section(section,{},snapshot('Other',['writer']),'/api/pilot')
    assert result['writer_input_selection']=='unknown'
    assert result['sources'][0]['report_role']=='selection_unknown'


def test_native_receipt_explicitly_records_no_admitted_source_without_fake_json_request():
    text='Exact original'
    native={'source_sha256':hashlib.sha256(text.encode()).hexdigest(),
        'source_evidence_ids':[],'translation_profile_sha256':'a'*64}
    receipt={'stages':{'generation':{'writer':{'native_translation':native,'requests':[{'request':{'prompt':[2,3,4]}}]}}}}
    assert retained_writer_ids(receipt,text)==[]
    assert retained_writer_ids(receipt,'Different original')is None
    section={'text':text,'family_id':'family-1','sources':[{'evidence_id':'extra','title':'Additional retrieved record'}]}
    result=report_section(section,{},receipt,'/api/pilot')
    assert result['writer_input_selection']=='retained'
    assert result['sources'][0]['report_role']=='retrieval_context'
