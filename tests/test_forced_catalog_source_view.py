"""Paired actual CORE results cross the ordinary WEB identity/source consumer."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_pilot_unified import core_test_root,frozen_matches_from_result,run_bounded_answer_plan
from pilot_backend import unified
from pilot_backend.store import digest

@pytest.fixture
def actual_forced_source_view(tmp_path,monkeypatch):
    root=core_test_root();monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_fulltext_grounded import store
    from test_pilot import runtime
    from test_pilot_shared_provider import profiled_runtime
    from test_actual_question_catalog_repairs import zero_dispatch_actual_result
    source=tmp_path/'core-store';source.mkdir()
    configured,native,_=profiled_runtime(runtime.__wrapped__(store.__wrapped__(source)),tmp_path)
    result=zero_dispatch_actual_result(configured,monkeypatch,native)
    assert native.calls==[]
    for client in configured.clients.values():client.post.assert_not_called()
    return result


def test_actual_forced_abstention_has_no_provider_and_keeps_frozen_source_readers(actual_forced_source_view):
    result=actual_forced_source_view
    output=run_bounded_answer_plan(result,frozen_matches_from_result(result),model='auto')
    assert output['generation']['status']=='partial',output
    assert output['generation_status']=='returned_processing_limitation'
    assert output['answer_kind']=='source_only'and not output['claims']and not output['citations']
    assert output['execution']['inference_performed']is False
    assert output['evidence']and output['model_identity']['model_id']=='not_invoked'


@pytest.mark.parametrize('damage',['runtime','reason','inference','model','dispatch','attempt','accounting'])
def test_forced_source_view_requires_closed_identity_and_zero_accounting(actual_forced_source_view,damage):
    result=actual_forced_source_view
    if damage=='runtime':result['execution']['runtime']='unknown'
    elif damage=='reason':result['execution']['route_reason']='unknown'
    elif damage=='inference':result['execution']['inference_performed']=True
    elif damage=='model':result['model_identity']['model_id']='unknown'
    elif damage=='dispatch':result['generation_accounting']['primary_provider_dispatches']=1
    elif damage=='attempt':result['provider_attempts']=[{'status':'completed'}]
    else:result.pop('generation_accounting')
    output=run_bounded_answer_plan(result,frozen_matches_from_result(result),model='auto')
    assert output['generation']['status']=='failed'
    assert output['generation']['error_code']=='provider_identity_missing'


@pytest.mark.parametrize('case',['recorded-alias','qualified-multiword'])
def test_actual_source_explanation_rendering_crosses_web_with_original_frozen_ids(monkeypatch,case):
    root=core_test_root();monkeypatch.syspath_prepend(str(root));monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_actual_question_catalog_repairs import render_actual
    _,payload,registry,result=render_actual(case)
    # Synthetic identity is only a test envelope around the real source-bound
    # result; it is not a new model completion or a serving observation.
    result['execution']={'selection':'qwen','provider_scope':'own'}
    result['model_identity']={'model_id':'offline-selector-test-envelope'}
    matches=frozen_matches_from_result(result)
    body={'contract':unified.CONTRACT,'locale':'it','matches':matches,
          'gateway_bundle':{'offline':True},'retrieval':{'status':'complete'}}
    output=unified.generate(SimpleNamespace(request=lambda *args:result),
        dict(body,snapshot_sha256=digest(body)),'answer','qwen')
    assert output['generation']['status']=='generated',output
    assert output['citations']and output['claims']
    assert {row['evidence_id']for row in output['citations']}=={
        row['evidence_id']for row in result['citations']}
    assert all(block['unit_type']=='terminology_explanation'for block in output['display_blocks'])
