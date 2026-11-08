"""Current CORE synonym output crosses the ordinary WEB answer consumer."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from test_pilot_unified import (frozen_matches_from_result,
                               run_bounded_answer_plan)


@pytest.fixture
def native_synonym_answer(tmp_path,monkeypatch):
    configured=os.environ.get('KLIKAI_PINNED_CORE_ROOT')or os.environ.get('KLIKAI_CORE_REPO')
    if not configured:
        pytest.skip('Set KLIKAI_PINNED_CORE_ROOT to run the paired CORE synonym contract test')
    root=Path(configured).resolve()
    required=['tools/zrc_reference/pilot_runtime.py','tests/zrc/test_requested_synonym_relations.py',
              'tests/zrc/fixtures/synonym_green_frozen.json.gz']
    if not all((root/name).is_file()for name in required):
        pytest.skip('Selected CORE does not provide the native synonym fixture required by this paired test')
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.syspath_prepend(str(root/'tests/zrc'))
    from test_fulltext_grounded import store as core_store
    from test_pilot import runtime as core_runtime,GenerateRequest
    from test_pilot_shared_provider import profiled_runtime
    from test_bounded_recomposition import _generation_payload
    from test_requested_synonym_relations import prepared

    location=tmp_path/'core-store';location.mkdir()
    runtime=core_runtime.__wrapped__(core_store.__wrapped__(location))
    configured,native,_=profiled_runtime(runtime,tmp_path)
    _,prepared_payload,_=prepared()
    payload=_generation_payload(configured,prepared_payload)
    monkeypatch.setattr(configured,'_unpack',lambda bundle:payload)
    monkeypatch.setattr(configured,'_check_source',lambda current:None)
    native.calls.clear();native.sleeping=True
    result=configured.generate(GenerateRequest(bundle={},tier='answer',model='auto'))
    assert result['execution']['inference_performed']is False
    assert result['model_identity']['model_id']=='not_invoked'
    assert result['provider_attempts']==[] and native.calls==[]
    assert len(result['citations'])==3
    assert all(row['unit_type']=='lexical_relations'for row in result['display_blocks'])
    for client in configured.clients.values():client.post.assert_not_called()
    return result


def test_native_core_synonym_answer_is_useful_and_keeps_its_three_sense_citations(native_synonym_answer):
    result=native_synonym_answer
    before=deepcopy(result)
    output=run_bounded_answer_plan(result,frozen_matches_from_result(result),model='auto')
    assert output['generation']['status']=='generated',json.dumps(output,ensure_ascii=False,indent=2)
    assert output['answer_md']==before['answer_md']
    assert 'neizkušen, mlad, nezrel, smrkav, naiven'in output['answer_md']
    assert 'nezdrav, bled, zelenkast'in output['answer_md']
    assert 'zeliščen, zelnat'in output['answer_md']
    assert 'samodejno pripravljeni'in output['answer_md']
    assert 'neposredno izpričana'not in output['answer_md']
    assert len(output['citations'])==3 and len(output['evidence'])==3
    assert len({row['support_id']for row in output['citations']})==3
    assert output['execution']['inference_performed']is False
    assert output['display_blocks'][0]['evidence_ids']


@pytest.mark.parametrize('damage',['citation','unknown_type','support','provider_identity'])
def test_native_synonym_answer_still_obeys_identity_and_frozen_graph_checks(native_synonym_answer,damage):
    result=native_synonym_answer
    matches=frozen_matches_from_result(result)
    if damage=='citation':result['citations'][0]['source_revision']='f'*64
    elif damage=='unknown_type':result['display_blocks'][0]['unit_type']='invented_lexical_relation'
    elif damage=='support':result['claims'][0]['support_ids']=['invented']
    else:result['model_identity']['model_id']='invented-provider'
    output=run_bounded_answer_plan(result,matches,model='auto')
    expected=('provider_identity_missing','provider_identity')if damage=='provider_identity'else('unknown_citation','citation_validation')
    assert (output['generation']['status'],output['generation']['error_code'],output['generation']['stage'])==('failed',*expected)
    assert output['evidence']==[]
