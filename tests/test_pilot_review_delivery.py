"""Synthetic delivery boundaries; private review records are replayed separately."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from pilot_backend import unified
from pilot_backend.store import digest


def deliver(result, locale='sl'):
    body = {'contract': unified.CONTRACT, 'locale': locale,
            'matches': [{'evidence_id': 'known'}], 'gateway_bundle': {'synthetic': True},
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
