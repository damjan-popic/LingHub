"""Withheld generation retains safe technical reasons, never its rejected prose."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from pilot_backend.human_feedback import generation_validation_diagnostics,provider_diagnostics
from pilot_backend.jobs import render_result
from test_pilot_unified import UnifiedGateway,app_for,ask,login,ORIGIN

PRIVATE='DO_NOT_EXPORT_VALIDATION_TEXT'


def rejected_result():
    return {
        'status':'validated','answer_kind':'source_only','generation_status':'not_generated',
        'validation':{'status':'all_claims_withheld','semantic_entailment_certified':False,
                      'error_type':'ValueError','exception':PRIVATE},
        'omitted_claims':[{'claim_id':'C1','claim_index':0,'reason':'unresolved_support_id',
                           'text':PRIVATE,'support_ids':[PRIVATE]}],
        'quality_actions':[{'claim_id':'C1','action':'omit','text':PRIVATE,
            'issues':[{'code':'source_uncertainty_not_preserved','action':'withhold','part_index':0,'source':PRIVATE}],
            'original_part_count':2,'retained_part_count':0,'source_spans':[{'passage_id':PRIVATE}]}],
        'limitations':[{'reason':'no_supported_generated_claims','message':PRIVATE}],
        'short_answer':PRIVATE,'answer_md':PRIVATE,'hidden_reasoning':PRIVATE,
        'provider_attempts':[{'route':'shared','model':'synthetic-shared','status':'completed','stage':'generation'}]}


def test_safe_validation_metadata_is_idempotent_through_existing_result_renderer():
    raw=rejected_result();before=deepcopy(raw)
    safe=generation_validation_diagnostics(raw)
    assert safe=={
        'gateway_status':'validated','gateway_answer_kind':'source_only','gateway_generation_status':'not_generated',
        'validation':{'status':'all_claims_withheld','semantic_entailment_certified':False,'error_type':'ValueError'},
        'omitted_claims':[{'claim_id':'C1','claim_index':0,'reason':'unresolved_support_id'}],
        'quality_actions':[{'claim_id':'C1','action':'omit',
            'issues':[{'code':'source_uncertainty_not_preserved','action':'withhold','part_index':0}],
            'original_part_count':2,'retained_part_count':0}],
        'limitation_reasons':['no_supported_generated_claims']}
    wrapped={'status':'source_only','generation_validation':safe}
    assert generation_validation_diagnostics(wrapped)==safe
    assert provider_diagnostics(wrapped)=={'generation_validation':safe}
    assert render_result(wrapped)[0]['generation_validation']==safe
    assert raw==before and PRIVATE not in json.dumps(safe)


@pytest.mark.parametrize('outcome', [
    {'status':'no_hit','answer_kind':'source_only','generation_status':'not_generated',
     'limitations':[{'reason':'model_found_insufficient_evidence'}]},
    {'status':'validated','answer_kind':'source_only','generation_status':'not_generated',
     'validation':{'status':'invalid_composition_envelope','error_type':'ValidationError','semantic_entailment_certified':False},
     'limitations':[{'reason':'invalid_or_inconsistently_scoped_composition'}]},
    {'status':'validated','answer_kind':'source_only','generation_status':'source_supported_partial',
     'synthesis_mode':'source_supported_partial',
     'validation':{'status':'bounded_checks_completed','semantic_entailment_certified':False}},
    rejected_result(),
])
def test_owned_frozen_api_keeps_withholding_diagnostics_without_admitting_generation(tmp_path,outcome):
    class WithheldGateway(UnifiedGateway):
        def request(self,path,payload,base=None):
            result=super().request(path,payload,base)
            if path=='/internal/pilot/generate':
                result.update(deepcopy(outcome))
                result.update(evidence=[],citations=[],display_blocks=[],short_answer=PRIVATE,answer_md=PRIVATE)
            return result
    gateway=WithheldGateway();app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        fid,family=ask(app,client,login(client));run=family['short']
        no_support=outcome.get('status')=='no_hit'
        assert run['state']=='complete'and run['status']==('no_hit' if no_support else 'source_only')
        assert run['answer_kind']=='source_only'and run['generation_status']=='not_generated'
        assert run['generation']['status']==('no_support' if no_support else 'failed')
        expected=('insufficient_selected_evidence' if no_support else
                  'unknown_citation' if outcome.get('generation_status')=='source_supported_partial' else
                  outcome['validation']['status'])
        assert run['generation']['error_code']==expected
        assert run['generation_validation']==generation_validation_diagnostics(outcome)
        assert PRIVATE not in json.dumps(run)
        assert sum(path=='/internal/pilot/generate'for path,_,_ in gateway.calls)==1
        with app.state.store.connect()as db:
            saved=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(run['run_id'],)).fetchone()[0])
        assert saved['generation_validation']==run['generation_validation']
        assert PRIVATE not in json.dumps(saved)
        gateway.blocked=True
        assert client.get('/api/pilot/families/'+fid).json()==family
        login(client,'unified-other')
        assert client.get('/api/pilot/families/'+fid).status_code==404


def test_validation_diagnostic_limits_and_unknown_values_cannot_leak_private_data():
    row={'claim_id':PRIVATE,'claim_index':True,'reason':'unresolved_support_id','text':PRIVATE}
    action={'claim_id':'C99','action':'omit','text':PRIVATE,'original_part_count':True,
            'retained_part_count':2**80,'issues':[{'code':'attached_exception_lost','action':PRIVATE,
                                               'part_index':True,'source':PRIVATE}]*50}
    raw={'status':PRIVATE,'answer_kind':{},'generation_status':PRIVATE,
         'validation':{'status':PRIVATE,'semantic_entailment_certified':True,'error_type':PRIVATE,'message':PRIVATE},
         'omitted_claims':[row]*50,'quality_actions':[action]*50,
         'limitations':[{'reason':PRIVATE,'message':PRIVATE}]*50}
    result=generation_validation_diagnostics(raw)
    assert set(result)=={'omitted_claims','quality_actions'}
    assert len(result['omitted_claims'])==len(result['quality_actions'])==14
    assert result['omitted_claims'][0]=={'reason':'unresolved_support_id'}
    assert result['quality_actions'][0]=={'action':'omit','issues':[{'code':'attached_exception_lost'}]*14}
    assert PRIVATE not in json.dumps(result)
    assert generation_validation_diagnostics({'generation_validation':result})==result


@pytest.mark.parametrize('value',[None,[],True,17,PRIVATE])
def test_malformed_diagnostic_containers_are_omitted(value):
    assert generation_validation_diagnostics(value)=={}
    assert generation_validation_diagnostics({'validation':value,'omitted_claims':value,
        'quality_actions':value,'limitations':value})=={}
