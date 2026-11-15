"""Saved actual outcomes + labelled mechanical empty/comment controls."""
from pathlib import Path
from copy import deepcopy
import json
import pytest
from pilot_backend.document_ai_outcomes import presentation
from pilot_backend.document_ai_suggestions import report_blocks
from pilot_backend.document_ai_contract import PACKET,digest,sha,checked_output


def saved(name):return json.loads((Path(__file__).parent/'fixtures/document_ai_saved_outcomes_20261115.json').read_text())['cases'][name]


def test_actual_rejected_items_and_invalid_response_are_not_successful_advice():
    case=saved('remaining');before=deepcopy(case);display=presentation(case['public'],case['results'])
    assert display['counts']=={'returned_items':4,'checked_items':3,'eligible_edits':0,'valid_comments':0,'rejected_items':3,'invalid_responses':1}
    assert [row['kind']for row in display['units'].values()]==['cancelled','only_rejected','only_rejected','unavailable_input_limit','invalid_response']
    rejected=list(display['items'].values());assert len(rejected)==3
    assert [i['labels']['sl']['reasons'][0]['code']for i in rejected]==['no_op','span_not_bound','span_not_bound']
    invalid=next(v for v in display['units'].values()if v['kind']=='invalid_response')
    original=next(v for v in case['results'].values()if v['status']=='unavailable')
    assert invalid['recorded_failure_type']=='ValueError'and invalid['raw_content']==original['raw_content']
    assert case==before


@pytest.mark.parametrize('locale',['sl','it'])
def test_actual_rejection_reasons_shared_with_report_without_undecided_headers(locale):
    case=saved('remaining');ai=deepcopy(case['public']);ai['presentation']=presentation(ai,case['results'])
    blocks=list(report_blocks(ai,locale));text='\n'.join(str(value)for _,value in blocks)
    assert 'no_op'in text and text.count('span_not_bound')==2 and 'ValueError'in text
    assert ('Predlogi pripravljeni'not in text and 'Neodločen'not in text)
    assert ('Suggerimenti pronti'not in text and 'Da decidere'not in text)
    assert ('enako obravnavanemu'in text)if locale=='sl'else('identico'in text)
    assert 'S6eaf49a750b58c1e'in text # unchanged raw rejected-response reference remains accessible


def test_actual_A_remains_eligible_with_unchanged_English_output_and_ids():
    case=saved('A');before=deepcopy(case);display=presentation(case['public'],case['results'])
    assert display['counts']['eligible_edits']==1 and display['counts']['rejected_items']==0
    item,=case['public']['items'];assert display['items'][item['suggestion_id']]['kind']=='eligible_change'
    assert item['explanation'].startswith('The phrase')and case==before


def mechanical(kind):
    """Clearly synthetic, prechecked display fixture; no language evaluation."""
    item={'replacement':None,
        'section_index':0,'start':0,'end':5,'observed_text':'alpha','explanation':'MECHANICAL FIXTURE ONLY',
        'uncertainty':'','kind':'additional_suggestion','finding_ids':[],'source_refs':[]}
    packet={'version':PACKET,'document_id':'MECHANICAL_FIXTURE','document_revision':'0'*64,'evidence_manifest_sha256':'1'*64,
        'locale':'sl','sections':[{'index':0,'location':'fixture','original_text':'alpha','evidence_draft':'alpha',
            'original_sha256':sha('alpha'),'draft_sha256':sha('alpha'),'evidence_edits':[],'findings':[]}],
        'sources':{},'coverage':{'provided_sections':[0]}}
    packet['packet_sha256']=digest(packet);raw={'items':[]if kind=='empty'else[item]}
    checked=checked_output(raw,packet);items=[{**item,'unit_id':'fixture'}for item in checked]
    result={'status':'available','items':checked,'raw_content':json.dumps(raw),'inference_performed':False}
    ai={'state':'available','items':items,'units':[{'unit_id':'fixture','state':'empty'if kind=='empty'else'completed',
        'section_index':0,'original_span':{'start':0,'end':5,'text':'alpha'}}], 'decisions':{},'coverage':{'provided_sections':[0]},'review_complete':True}
    return ai,{'fixture':result}


@pytest.mark.parametrize('kind',['empty','comment'])
def test_valid_empty_and_null_comment_are_distinct_from_rejection(kind):
    ai,results=mechanical(kind);before=deepcopy((ai,results));display=presentation(ai,results)
    assert display['counts']['eligible_edits']==display['counts']['rejected_items']==display['counts']['invalid_responses']==0
    if kind=='comment':
        uid=ai['items'][0]['suggestion_id']
        assert display['counts']['valid_comments']==1 and display['items'][uid]['kind']=='valid_comment'
        assert display['items'][uid]['labels']['sl']['reasons']==[]
    else:assert display['units']['fixture']['kind']=='valid_empty'and display['counts']['returned_items']==0
    assert (ai,results)==before


def test_unknown_issue_is_preserved_without_inventing_a_diagnosis():
    ai,results=mechanical('comment');item=ai['items'][0];item['mechanical_issues']=['future_unknown_reason']
    results['fixture']['items']=ai['items'];display=presentation(ai,results)
    reason=display['items'][item['suggestion_id']]['labels']['sl']['reasons'][0]
    assert reason['code']=='future_unknown_reason'and 'Drug zabeležen'in reason['text']


def legacy_invalid_fixture():
    """Mechanical retained-response fixture, not a language/model result."""
    ai={'state':'unavailable','items':[],'decisions':{},'coverage':{'provided_sections':[0]}}
    result={'status':'unavailable','items':[],'raw_content':'{"items":[{"fixture":"invalid retained response"}]}',
        'inference_performed':True,'failure_type':'ValueError'}
    return ai,{'legacy':result}


def test_legacy_invalid_response_keeps_recorded_reason_and_raw_text_on_reports():
    from pilot_backend import document_files
    from io import BytesIO
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    ai,results=legacy_invalid_fixture();before=deepcopy((ai,results));display=presentation(ai,results)
    assert display['counts']['invalid_responses']==1 and display['counts']['returned_items']==1
    assert display['legacy_response']['raw_content']==results['legacy']['raw_content']
    assert display['legacy_response']['recorded_failure_type']=='ValueError'
    for locale in ('sl','it'):
        public={**ai,'presentation':display};blocks=list(report_blocks(public,locale));text='\n'.join(str(v)for _,v in blocks)
        assert results['legacy']['raw_content']in text and 'ValueError'in text
        with ZipFile(BytesIO(document_files._text_docx(blocks,styled=True)))as package:
            word='\n'.join(n.text or''for n in ET.fromstring(package.read('word/document.xml')).iter(document_files.W+'t'))
        assert results['legacy']['raw_content']in word and 'ValueError'in word
    assert (ai,results)==before
