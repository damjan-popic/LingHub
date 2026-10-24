"""Reader delivery distinguishes interpretation blockers without leaking diagnostics."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from pilot_backend import unified
from pilot_backend.jobs import render_result
from pilot_backend.presentation import tester_view as reader_view
from pilot_backend.store import digest
from test_pilot_project_beta import ORIGIN, ask, beta_rig, finish, login


PRIVATE = 'PRIVATE_UNRESOLVED_UNIT_DIAGNOSTIC'
STATES = {
    'source_present_unresolved_interpretation',
    'source_present_not_admitted_for_answer',
    'no_selected_evidence',
}


def state_result(state):
    present = state != 'no_selected_evidence'
    processing = state == 'source_present_unresolved_interpretation'
    return {'status': 'validated' if present else 'no_hit',
        'answer_kind': 'source_only',
        'generation_status': 'returned_processing_limitation' if processing else 'returned_abstention',
        'evidence': ([{'citation_id': 'source-1', 'title': 'Visible source'}]
            if present else []),
        'evidence_state': {'status': state, 'no_hit': not present,
            'reason': PRIVATE, 'unresolved': [{'reason': PRIVATE}]}}


def test_job_result_retains_evidence_state_for_all_successful_outcomes():
    for status in STATES:
        value = {'status': 'no_hit' if status == 'no_selected_evidence' else 'validated',
            'evidence_state': {'status': status, 'reason': PRIVATE,
                'unresolved': [{'reason': PRIVATE}]}}
        rendered, _ = render_result(value)
        assert rendered['evidence_state'] == value['evidence_state']
        assert rendered['status'] == value['status']


def test_reader_evidence_state_is_closed_and_diagnostics_are_private():
    for status in STATES:
        value=state_result(status)
        value['evidence_state']['limitation_code']='unsupported_interpretation'
        value['evidence_state']['unresolved']=[{'source':PRIVATE,'reason':PRIVATE}]
        public = reader_view(value, 'owner', 'secret')
        assert public['evidence_state'] == {'status': status}
        assert PRIVATE not in json.dumps(public)
    assert 'evidence_state' not in reader_view(
        {**state_result('source_present_unresolved_interpretation'),
            'evidence_state': {'status': 'arbitrary_private_state'}}, 'owner', 'secret')


def test_reader_omits_contradictory_evidence_states_and_dependent_limitation():
    limitation={'code':'unsupported_interpretation','scope':'answer','claim_ids':[]}
    unresolved=state_result('source_present_unresolved_interpretation')
    no_selected=state_result('no_selected_evidence')
    attacks=[
        {**unresolved,'evidence':[]},
        {**no_selected,'status':'validated','evidence':[{'citation_id':'source-1'}]},
        {**no_selected,'evidence':[{'citation_id':'source-1'}]},
        {**state_result('source_present_not_admitted_for_answer'),'status':'no_hit'},
        {**unresolved,'evidence_state':{**unresolved['evidence_state'],'no_hit':0}},
    ]
    for value in attacks:
        public=reader_view({**value,'limitations':[limitation]},'owner','secret')
        assert 'evidence_state'not in public
        assert public['limitations']==[]


def test_reader_interpretation_limitation_requires_matching_state_and_evidence():
    limitation = {'code': 'unsupported_interpretation', 'scope': 'answer',
        'claim_ids': [], 'reason': PRIVATE}
    base = {**state_result('source_present_unresolved_interpretation'),
        'limitations': [limitation]}
    matched = reader_view(base, 'owner', 'secret')
    assert matched['limitations'] == [{key: limitation[key] for key in
        ('code', 'scope', 'claim_ids')}]
    for mismatch in (
        {**base, 'evidence_state': {'status': 'source_present_not_admitted_for_answer',
            'no_hit': False}},
        {**base, 'evidence_state': {'status': 'arbitrary_private_state'}},
        {**base, 'limitations': [{**limitation, 'scope': 'claim',
            'claim_ids': ['notice']}]},
        {**base, 'evidence': []},
    ):
        assert reader_view(mismatch, 'owner', 'secret').get('limitations') == []


def test_unified_consumer_delivers_bounded_source_only_outcome_without_claim_citations():
    result={**state_result('source_present_unresolved_interpretation'),
        'short_answer':'Vira ni mogoče varno razložiti.',
        'answer_md':'Vira ni mogoče varno razložiti.',
        'display_blocks':[{'block_id':'notice','text':'Vira ni mogoče varno razložiti.'}],
        'citations':[],'source_badges':[],
        'synthesis_mode':'abstention',
        'limitations':[{'code':'unsupported_interpretation','scope':'answer','claim_ids':[]}],
        'model_identity':{'repository':'synthetic/current-writer'},
        'execution':{'device':'gpu'}}
    body={'contract':unified.CONTRACT,'locale':'sl',
        'matches':[{'evidence_id':'source-1'}],
        'gateway_bundle':{'synthetic':True},'retrieval':{'status':'complete'}}
    output=unified.generate(SimpleNamespace(request=lambda *args:result),
        dict(body,snapshot_sha256=digest(body)),'short','qwen')
    assert (output['status'],output['answer_kind'],output['generation_status'])==(
        'validated','source_only','returned_processing_limitation')
    assert output['evidence_state']['status']=='source_present_unresolved_interpretation'
    assert output['evidence']==[{'citation_id':'source-1','title':'Visible source',
        'evidence_id':'source-1','local_number':1}]
    assert output['generation']['status']=='partial'


def test_project_api_keeps_interpretation_limitation_sources_and_original(beta_rig):
    app, adapter, _, _, credentials = beta_rig
    generate = adapter.generate

    def blocked(*args, **kwargs):
        result = generate(*args, **kwargs)
        result.update(status='validated', answer_kind='source_only',
            generation_status='returned_processing_limitation',
            blocks=[{'block_id': 'notice',
                'text': 'Samodejnega povzetka ne prikazujem kot zanesljivega nasveta.'}],
            evidence=[{'citation_id': 'passage-0', 'source_slug': 'jezikovna_posta',
                'title': 'Original 0', 'snippet': 'Preserved source excerpt.',
                'external_id': 'original-0', 'source_revision': 'source-revision'}],
            citations=[],
            evidence_state={'status': 'source_present_unresolved_interpretation',
                'reason': PRIVATE, 'no_hit': False,
                'limitation_code': 'unsupported_interpretation',
                'unresolved': [{'source': 'documentary_analysis', 'reason': PRIVATE}]},
            limitations=[{'code': 'unsupported_interpretation', 'scope': 'answer',
                'claim_ids': [], 'reason': PRIVATE},
                {'code': 'ARBITRARY_PRIVATE_CODE', 'scope': 'answer',
                    'claim_ids': [], 'reason': PRIVATE}])
        return result

    adapter.generate = blocked
    with TestClient(app, base_url=ORIGIN) as client:
        headers = login(client, credentials)
        started = ask(client, headers)
        finish(app)
        fid = started['family']['answer_family_id']
        family = client.get('/api/pilot/families/' + fid).json()
        run = family['short']
        assert run['status'] == 'validated'
        assert run['answer_kind'] == 'source_only'
        assert run['generation_status'] == 'returned_processing_limitation'
        assert run['evidence_state'] == {
            'status': 'source_present_unresolved_interpretation'}
        assert run['limitations'] == [{'code': 'unsupported_interpretation',
            'scope': 'answer', 'claim_ids': []}]
        assert run['blocks'] == [{'block_id': 'notice',
            'text': 'Samodejnega povzetka ne prikazujem kot zanesljivega nasveta.'}]
        assert run['evidence'][0]['citation_id'] == 'passage-0'
        assert 'local_number' not in run['evidence'][0]
        assert run['source_badges']
        assert PRIVATE not in json.dumps(family)
        assert 'ARBITRARY_PRIVATE_CODE' not in json.dumps(family)
        original = client.get(
            f'/api/pilot/families/{fid}/sources/passage-0?tier=short')
        assert original.status_code == 200
        assert 'Complete original 0.' in original.json()['excerpt']
        with app.state.store.connect() as db:
            stored = json.loads(db.execute(
                'SELECT result FROM runs WHERE id=?', (run['run_id'],)).fetchone()[0])
        assert stored['evidence_state']['reason'] == PRIVATE
        assert stored['limitations'][0]['reason'] == PRIVATE


@pytest.mark.parametrize('evidence_state,expected_status,source_present',[
    ('source_present_unresolved_interpretation',
     'source_present_unresolved_interpretation',True),
    ('source_present_not_admitted_for_answer',
     'source_present_not_admitted_for_answer',True),
    ('no_selected_evidence','no_support',False),
])
def test_match_pages_share_closed_evidence_state_usage(
        beta_rig,evidence_state,expected_status,source_present):
    app,adapter,_,_,credentials=beta_rig
    generate=adapter.generate

    def outcome(*args,**kwargs):
        result=generate(*args,**kwargs)
        processing=evidence_state=='source_present_unresolved_interpretation'
        result.update(
            status='validated'if source_present else'no_hit',
            answer_kind='source_only',
            generation_status=('returned_processing_limitation'if processing
                else'returned_abstention'),
            blocks=[{'block_id':'notice','text':'Bounded source-only outcome.'}],
            evidence=result['evidence']if source_present else[],citations=[],
            evidence_state={'status':evidence_state,'no_hit':not source_present,
                'reason':PRIVATE,'unresolved':[]},
            limitations=[])
        return result

    adapter.generate=outcome
    with TestClient(app,base_url=ORIGIN)as client:
        started=ask(client,login(client,credentials));finish(app)
        fid=started['family']['answer_family_id']
        plain=client.get(f'/api/pilot/families/{fid}/matches?limit=50').json()
        summary=client.get(f'/api/pilot/families/{fid}/sources').json()
        records=client.get(
            f'/api/pilot/families/{fid}/matches?source=jezikovna_posta&limit=50').json()
    expected={'passage-0'}if source_present else set()
    assert {row['evidence_id']for row in plain['items']if row['used_in_answer']}==expected
    assert summary['answer_status']==expected_status
    assert records['answer_status']==expected_status
    assert records['items'][0]['used_in_answer']is source_present
    assert not any(row['used_in_answer']for row in records['items'][1:])
