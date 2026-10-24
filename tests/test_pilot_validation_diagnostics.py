"""Strict gateway validation reaches the owned pilot result without private data."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

def pinned_support_root(variable,marker):
    """Resolve only an explicitly packaged or PYTHONPATH-selected support tree."""
    configured=os.environ.get(variable)
    candidates=([Path(configured)]if configured else[
        Path(value)for value in os.environ.get('PYTHONPATH','').split(os.pathsep)
        if value and(Path(value)/marker).is_file()])
    if len(candidates)!=1:
        raise RuntimeError(variable+' must identify exactly one commit-pinned support tree')
    root=candidates[0].absolute()
    if (configured and(root.is_symlink()or root.resolve()!=root)
            or not(root/marker).is_file()):
        raise RuntimeError(variable+' is not a direct commit-pinned support tree')
    return root


KLIKAI=pinned_support_root('KLIKAI_PINNED_CORE_ROOT','tools/zrc_reference/pilot_contracts.py')
INFRA=pinned_support_root('KLIKAI_PINNED_INFRA_ROOT','scripts/pilot_editorial_probe.py')
sys.path.insert(0, str(KLIKAI / 'tests/zrc'))
from test_pilot import runtime, store
from test_pilot_shared_provider import shared
sys.path.remove(str(KLIKAI / 'tests/zrc'))
from tools.zrc_reference.pilot_contracts import FreezeRequest, GenerateRequest
from pilot_backend.human_feedback import generation_validation_diagnostics, provider_diagnostics
from test_pilot_unified import ORIGIN, UnifiedGateway, login
from test_pilot_model_choices import app_choices, submit


@pytest.mark.parametrize('raw,expected', [
    ({'answer_language':'sl','no_sufficient_evidence':True},
     {'type':'missing','loc':['blocks']}),
    ({'answer_language':'sl','blocks':[{'unit_id':'U'+'0'*16,'paragraph_id':'P1'}],
      'no_sufficient_evidence':False},
     {'type':'missing','loc':['blocks',0,'block_id']}),
    ({'answer_language':'sl','blocks':[],'no_sufficient_evidence':True,
      'limitations':[{'code':'partial_evidence','scope':'answer','claim_ids':[]}]},
     {'type':'extra_forbidden','loc':['limitations']}),
])
def test_actual_writer_adapter_owned_api_and_saved_failure_preserve_exact_plan_error(
        runtime, tmp_path, raw, expected):
    _, native = shared(runtime, tmp_path)
    runtime.settings['unified_policy'] = {'policy_id': 'synthetic-validation-policy', 'sources': ['zvezek'],
                                          'editorial_operator_policy_id': 'synthetic-editorial-policy'}
    native.response['choices'][0]['message']['content'] = json.dumps(raw)
    class RealWriterGateway(UnifiedGateway):
        def require_generation(self, models):pass
        def request(self, path, payload, base=None):
            self.calls.append((path, deepcopy(payload), base))
            if path == '/api/sources':return {'sources': []}
            if path == '/internal/pilot/freeze':
                return runtime.freeze(FreezeRequest.model_validate(payload),
                    fixed_evidence={'passages': runtime.engine.store.lexical('Hugo'), 'retrieval': 'fixed'})
            if path == '/internal/pilot/generate':return runtime.generate(GenerateRequest.model_validate(payload))
            raise AssertionError('Unexpected synthetic upstream request: ' + path)
    gateway = RealWriterGateway();app = app_choices(tmp_path, gateway)
    with TestClient(app, base_url=ORIGIN) as client:
        family, job = submit(app, client, login(client),question='Kako sklanjamo Hugo?')
        short = family['short']
        assert short['state'] == 'complete' and short['generation_status'] == 'not_generated'
        assert short['generation'].get('error_code') == 'invalid_composition_envelope', (short['generation'], short.get('retrieval'), [row[0] for row in gateway.calls])
        validation=short['generation_validation']['validation']
        assert validation['error_type']=='ValidationError'
        assert validation['pydantic_errors'] == [expected]
        fid = family['answer_family_id']
        with app.state.store.connect() as db:
            owner = db.execute('SELECT owner FROM families WHERE id=?', (fid,)).fetchone()[0]
            saved = json.loads(db.execute('SELECT result FROM runs WHERE id=?', (job['run_id'],)).fetchone()[0])
        assert saved['generation_validation']['validation'] == validation
        assert len(saved['provider_attempts']) == 1 and saved['provider_attempts'][0]['status'] == 'completed'
        spec = importlib.util.spec_from_file_location('validation_owned_probe', INFRA / 'scripts/pilot_editorial_probe.py')
        probe = importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
        detail = probe.saved_case_failure({'store': str(tmp_path / 'private/pilot.sqlite')}, owner, fid)
        assert detail['generation_validation']['validation'] == validation
        assert 'signature' not in json.dumps(detail) and 'synthetic-unified-test-password' not in json.dumps(detail)
        assert client.get('/api/pilot/families/' + fid).json() == family
        login(client, 'unified-other')
        assert client.get('/api/pilot/families/' + fid).status_code == 404
        with pytest.raises(probe.ProbeFailure, match='failed_owned_run_missing'):
            probe.saved_case_failure({'store': str(tmp_path / 'private/pilot.sqlite')}, 'not-owner', fid)
    assert len([call for call in native.calls if call[:2] == ('POST', '/v1/chat/completions')]) == 1
    for writer in runtime.clients.values():writer.post.assert_not_called()


def test_validation_boundary_is_bounded_idempotent_and_drops_private_fields():
    source = {'validation': {'status': 'invalid_composition_envelope', 'error_type': 'ValidationError',
        'semantic_entailment_certified': False, 'pydantic_errors': [
            {'type': 'value_error', 'loc': ['limitations', 0], 'reason': 'answer_limitation_has_claim_ids',
             'input': 'PRIVATE_INPUT', 'ctx': {'error': 'PRIVATE_ERROR'}, 'msg': 'PRIVATE_MESSAGE'},
            {'type': 'extra_forbidden', 'loc': ['PRIVATE_FIELD', True, -1, 1025], 'reason': 'PRIVATE_REASON'},
            {'type': 'PRIVATE_TYPE', 'loc': ['claims']},
            {'type': 'missing', 'loc': ['claims'] * 30},
        ] * 20, 'raw_response': 'PRIVATE_RESPONSE'}}
    projected = generation_validation_diagnostics(source)
    assert generation_validation_diagnostics({'generation_validation': projected}) == projected
    assert provider_diagnostics({'generation_validation': projected})['generation_validation'] == projected
    errors = projected['validation']['pydantic_errors']
    assert len(errors) == 24 and max(len(row['loc']) for row in errors) == 12
    assert errors[0] == {'type': 'value_error', 'loc': ['limitations', 0], 'reason': 'answer_limitation_has_claim_ids'}
    assert errors[1] == {'type': 'extra_forbidden', 'loc': ['other_field'] * 4}
    assert 'PRIVATE' not in json.dumps(projected)


@pytest.mark.parametrize('reason',[
    'invalid_composition_envelope','tier_claim_bound_exceeded','duplicate_claim_ids',
    'requested_locale_differs','abstention_contradicts_claims','empty_supported_answer',
    'limitation_names_unknown_claims','no_evidence_caveat_contradicts_claims','support_bytes_differ'])
def test_fixed_validation_reasons_survive_without_raw_exception_or_claim_links(reason):
    source={'validation':{'status':'invalid_composition_envelope','error_type':'ValueError','reason':reason,
        'message':'PRIVATE_MESSAGE','claim_ids':['PRIVATE_ID'],'support_ids':['PRIVATE_SUPPORT']}}
    result=generation_validation_diagnostics(source)
    assert result=={'validation':{'status':'invalid_composition_envelope','error_type':'ValueError','reason':reason}}
    assert generation_validation_diagnostics({'generation_validation':result})==result
    assert 'PRIVATE'not in json.dumps(result)


@pytest.mark.parametrize('reason',[None,[],{},True,17,'PRIVATE_MESSAGE'])
def test_validation_reason_rejects_unknown_and_malformed_values(reason):
    result=generation_validation_diagnostics({'validation':{
        'status':'invalid_composition_envelope','error_type':'ValueError','reason':reason}})
    assert result=={'validation':{'status':'invalid_composition_envelope','error_type':'ValueError'}}


@pytest.mark.parametrize('errors', [None, 'PRIVATE_ERROR', {'type': 'value_error'}, [None, {'type': []}, {'type': 'value_error', 'loc': 'PRIVATE_LOCATION'}]])
def test_malformed_validation_details_do_not_replace_original_failure_category(errors):
    result = generation_validation_diagnostics({'validation': {'status': 'invalid_composition_envelope',
        'error_type': 'ValidationError', 'pydantic_errors': errors}})
    assert result['validation']['status'] == 'invalid_composition_envelope'
    assert result['validation']['error_type'] == 'ValidationError'
    assert not result['validation'].get('pydantic_errors') and 'PRIVATE' not in json.dumps(result)
