"""Connected prose retains per-assertion citations and immutable feedback text."""
from copy import deepcopy

import pytest
from pilot_backend.jobs import render_result
from pilot_backend.human_feedback import display_paragraphs
from pilot_backend.security import Failure
from pilot_backend.store import digest


def result():
    return {'status':'validated','short_answer':'First. Second.','answer_md':'First. Second.',
        'display_blocks':[
            {'block_id':'C1','text':'First.','paragraph_id':'P1','evidence_ids':['source-a']},
            {'block_id':'C2','text':'Second.','paragraph_id':'P1','evidence_ids':['source-b']}],
        'claims':[{'claim_id':'C1','paragraph_id':'P1'},{'claim_id':'C2','paragraph_id':'P1'}]}


def test_display_keeps_individual_text_ids_and_scope_in_grouped_paragraph():
    value=result();rendered,sha=render_result(value)
    assert rendered['blocks']==value['display_blocks']
    assert sha==digest(value['display_blocks'])
    assert rendered['blocks'][0]['evidence_ids']==['source-a']
    assert rendered['blocks'][1]['evidence_ids']==['source-b']
    assert display_paragraphs(rendered,'short','sl')==['First. Second.']
    old=deepcopy(value)
    for block in old['display_blocks']:block.pop('paragraph_id')
    assert render_result(old)[0]['blocks']==old['display_blocks']
    assert render_result(old)[1]!=sha
    assert display_paragraphs(render_result(old)[0],'short','sl')==['First.','Second.']


@pytest.mark.parametrize('identity',['<script>',None,2,'P0','P15','P1 P2'])
def test_invalid_paragraph_identity_is_rejected(identity):
    value=result();value['display_blocks'][0]['paragraph_id']=identity
    with pytest.raises(Failure):render_result(value)
