"""Synthetic delivery boundaries; private review records are replayed separately."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from pilot_backend import unified
from pilot_backend.store import digest


def deliver(result, locale='sl', matches=None):
    body = {'contract': unified.CONTRACT, 'locale': locale,
            'matches': matches or [{'evidence_id': 'known'}], 'gateway_bundle': {'synthetic': True},
            'retrieval': {'status': 'complete'}}
    return unified.generate(SimpleNamespace(request=lambda *args: deepcopy(result)),
                            dict(body, snapshot_sha256=digest(body)), 'short', 'auto')


def partial():
    return {'status': 'validated', 'answer_kind': 'source_only',
        'generation_status': 'source_supported_partial', 'synthesis_mode': 'source_supported_partial',
        'validation': {'status': 'bounded_checks_completed', 'semantic_entailment_certified': False},
        'answer_md': 'Exact source excerpt.', 'short_answer': 'Exact source excerpt.',
        'display_blocks': [{'block_id': 'C1', 'text': 'Exact source excerpt.', 'evidence_ids': ['known']}],
        'evidence': [{'evidence_id': 'known'}], 'citations': [{'claim_id': 'C1', 'evidence_id': 'known'}],
        'execution': {'selection': 'auto', 'provider_scope': 'shared'},
        'model_identity': {'repository': 'synthetic-model'}}


def test_validated_source_excerpt_survives_without_claiming_generated_answer():
    value = partial()
    result = deliver(value)
    assert result['answer_md'] == value['answer_md']
    assert result['answer_kind'] == 'source_only'
    assert result['generation_status'] == 'source_supported_partial'
    assert result['generation']['status'] == 'partial'
    assert result['evidence'][0]['evidence_id'] == 'known'


@pytest.mark.parametrize('change,stage', [
    ({'execution': {}}, 'provider_identity'),
    ({'evidence': []}, 'citation_validation'),
    ({'citations': [{'claim_id': 'C1', 'evidence_id': 'unknown'}]}, 'citation_validation'),
    ({'display_blocks': [{'block_id': 'C1', 'text': 'Excerpt', 'evidence_ids': ['unknown']}]}, 'citation_validation'),
])
def test_partial_delivery_keeps_provider_and_citation_guards(change, stage):
    result = deliver(dict(partial(), **change))
    assert result['generation']['status'] == 'failed'
    assert result['generation']['stage'] == stage
    assert not result['evidence']


def test_unvalidated_source_only_is_not_promoted():
    result = deliver(dict(partial(), validation={}))
    assert result['generation']['error_code'] == 'undelivered_answer'
    assert result['generation_status'] == 'not_generated'


@pytest.mark.parametrize('result,status,code,stage', [
    ({'limitations': [{'reason': 'empty_indexed_evidence'}]}, 'no_support', 'insufficient_selected_evidence', 'evidence_selection'),
    ({'validation': {'status': 'invalid_composition_envelope'}}, 'failed', 'invalid_composition_envelope', 'composition_validation'),
    ({'validation': {'status': 'all_claims_withheld'}}, 'failed', 'all_claims_withheld', 'composition_validation'),
    ({'provider_failure': {'code': 'generation_waiting_capacity', 'stage': 'writer_request'}}, 'failed', 'generation_waiting_capacity', 'writer_request'),
    ({'private_error': 'SECRET'}, 'failed', 'undelivered_answer', 'provider_result'),
])
def test_actual_stage_is_not_replaced_by_admission_guess(result, status, code, stage):
    value = deliver(result)
    assert {k: value['generation'][k] for k in ('status', 'error_code', 'stage')} == {
        'status': status, 'error_code': code, 'stage': stage}
    assert 'SECRET' not in str(value)


def test_freeze_producer_uses_the_durable_nested_json_order(monkeypatch):
    import json
    from pilot_backend.store import canonical
    # Lists retain source order; only object key order is canonicalized.
    capture={'term_entries':[{'term':'B','lang':'sl','motivation':'Exact note.',
        'motivationSource':[{'url':'https://example.org','label':'Original'}]},
        {'term':'A','lang':'it'}]}
    original=deepcopy(capture)
    row={'source_slug':'synthetic','external_id':'term:1','capture':capture,
         'capture_sha256':digest(capture),'capture_kind':'terminology_record','score':1,
         'source':{'excerpt':'Exact note.'}}
    monkeypatch.setattr(unified,'registry',lambda g:[{}])
    monkeypatch.setattr(unified,'collect_source',lambda *a:([row],{'family':'synthetic','status':'ok'}))
    monkeypatch.setattr(unified,'collect_tools',lambda *a:(None,None))
    monkeypatch.setattr(unified,'merge_source_matches',lambda rows:rows)
    sent=[]
    def request(path, body):
        if path=='/internal/pilot/interpret':
            plan={'version':'klikai.request-plan.v1',
                'interpretation_contract':'klikai.request-interpretation.v1',
                'original':body['question'],
                'context':body['context'],'target_spans':[],
                'requested_facets':[],'requested_grammar_slots':[],
                'ambiguities':[],
                'source_language':body['input_language']
                    if body['input_language']in {'sl','it'}else None,
                'response_language':body['locale']}
            return {'contract':'klikai.request-interpretation.v1',
                'input_sha256':digest({'contract':'klikai.request-interpretation.input.v1',
                                       **body}),'request_plan':plan,
                'request_plan_sha256':digest(plan)}
        assert path=='/internal/pilot/freeze'
        sent.append(deepcopy(body))
        return {'bundle':{'payload':body},'matches':[dict(deepcopy(row),evidence_id='known')]}
    result=unified.freeze(SimpleNamespace(klikai_url='fixture',request=request),{'question':'Term?','locale':'sl'}, {})
    assert result['bundle']['gateway_bundle'] is not None
    assert len(sent)==1
    assert json.dumps(sent[0],ensure_ascii=False)==json.dumps(json.loads(canonical(sent[0])),ensure_ascii=False)
    transmitted=sent[0]['unified_evidence']['matches'][0]['capture']
    assert transmitted==original and capture==original
    assert [x['term'] for x in transmitted['term_entries']]==['B','A']
    assert digest(transmitted)==digest(original)


def test_comparison_original_links_are_bound_to_frozen_matches_and_not_citations():
    value=partial();value.update(status='partial',limitations=[{'code':'comparison_explanation_incomplete','scope':'answer','claim_ids':[]}],
        decision_sources=[{'evidence_id':'explanation','title':'Untrusted replacement title'},
                          {'evidence_id':'known','title':'Already cited'},
                          {'evidence_id':'explanation','title':'Duplicate'}])
    matches=[{'evidence_id':'known','title':'Cited original'},
             {'evidence_id':'explanation','title':'Complete original explanation'}]
    result=deliver(value,matches=matches)
    assert result['decision_sources']==[{'evidence_id':'explanation','title':'Complete original explanation'}]
    assert [row['evidence_id']for row in result['evidence']]==['known']
    assert [row['evidence_id']for row in result['citations']]==['known']


@pytest.mark.parametrize('sources',[
    [{'evidence_id':'unknown','title':'Forged'}],
    [{'evidence_id':'known','title':'Original','url':'https://example.org/'}],
    [{'evidence_id':None,'title':'Original'}],
    {'evidence_id':'known','title':'Original'},
])
def test_comparison_original_links_reject_unknown_or_unbounded_records(sources):
    value=partial();value.update(status='partial',limitations=[{'code':'comparison_coverage_incomplete','scope':'answer','claim_ids':[]}],decision_sources=sources)
    result=deliver(value,matches=[{'evidence_id':'known','title':'Original'}])
    assert result['generation']['error_code']=='unknown_citation'
    assert 'decision_sources'not in result


def test_comparison_links_are_not_accepted_as_unscoped_extra_sources():
    value=partial();value['decision_sources']=[{'evidence_id':'known','title':'Original'}]
    assert deliver(value)['generation']['error_code']=='unknown_citation'
