"""Unresolved shared case arguments request clarification before source lookup."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from pilot_backend import request_tasks,unified
from pilot_backend.store import digest
from test_pilot_unified import ORIGIN,UnifiedGateway,app_for,ask,login,shared_plan


def interpreted(question,reason,locale='sl'):
    plan=shared_plan(question,locale=locale)
    plan['target_spans']=[]
    plan['ambiguities']=[reason]
    payload={'question':question,'context':'','input_language':'und','locale':locale}
    return {'contract':'klikai.request-interpretation.v1',
        'input_sha256':digest({'contract':'klikai.request-interpretation.input.v1',**payload}),
        'request_plan':plan,'request_plan_sha256':digest(plan)}


@pytest.mark.parametrize('padding',['',' \n'])
@pytest.mark.parametrize('reason,question',[
    ('grammar_target_binding_unresolved','Rodilnik od besede'),
    ('grammar_slot_pairing_unresolved','Rodilnik in dajalnik za več izrazov'),
])
def test_shared_grammar_ambiguity_clarifies_without_sources_or_writer(tmp_path,reason,question,padding):
    question=padding+question+padding
    envelope=interpreted(question,reason)
    gateway=UnifiedGateway(interpret_response=deepcopy(envelope))
    app=app_for(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        headers=login(client)
        fid,family=ask(app,client,headers,question=question)
        tier=family['short']
        assert tier['answer_kind']=='deterministic_guidance'
        assert tier['answer_support']=='clarification'
        assert 'vir'not in tier['answer_md'].casefold()
        assert tier.get('citations',[])==[]
        assert [path for path,_,_ in gateway.calls]==['/internal/pilot/interpret']
        with app.state.store.connect()as db:
            bundle=json.loads(db.execute('SELECT bundle FROM families WHERE id=?',(fid,)).fetchone()[0])
            result=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(tier['run_id'],)).fetchone()[0])
        request_tasks.checked(bundle)
        assert bundle['question']==question
        assert bundle['shared_interpretation']['request_plan']==envelope['request_plan']
        assert bundle['decision']['ambiguities']==[reason]
        assert result['execution']['inference_performed']is False
        assert result['model_identity']['model_id']=='not_invoked'
        assert client.get('/api/pilot/families/'+fid).json()['short']==tier


def test_normal_reference_reuses_the_same_shared_envelope(monkeypatch):
    question=' \nRodilnik od besede drevo\t '
    envelope=interpreted(question,'unrelated_source_ambiguity')
    gateway=UnifiedGateway(interpret_response=envelope)
    seen={}
    def frozen(gateway,family,policy,*,interpretation):
        seen['interpretation']=deepcopy(interpretation)
        return {'ordinary_reference':True}
    monkeypatch.setattr(unified,'freeze',frozen)
    result=gateway.freeze({'task':'reference','question':question.strip(),'original_question':question,'context':'','locale':'sl','input_language':'und'},'synthetic-policy')
    assert result=={'ordinary_reference':True}
    assert seen['interpretation']['request_plan']==envelope['request_plan']
    assert [path for path,_,_ in gateway.calls]==['/internal/pilot/interpret']


def test_existing_raw_resource_guidance_still_skips_shared_interpretation():
    gateway=UnifiedGateway(interpret_failure=True)
    result=gateway.freeze({'task':'reference','question':'Kje najdem Zvezek?','context':'','locale':'sl','input_language':'und'},'synthetic-policy')
    assert result['bundle']['decision']['kind']=='source_guidance'
    assert gateway.calls==[]


def test_italian_grammar_clarification_is_frozen_in_requested_output_language():
    envelope=interpreted('Genitivo della parola','grammar_target_binding_unresolved','it')
    shared={'request_plan':envelope['request_plan'],
        'interpretation_input_sha256':envelope['input_sha256'],
        'request_plan_sha256':envelope['request_plan_sha256']}
    frozen=request_tasks.freeze({'question':'Genitivo della parola','context':'','locale':'it'},interpretation=shared)
    assert frozen['bundle']['decision']['text'].startswith('Per quale espressione')
    assert request_tasks.answer(frozen['bundle'],'short')['answer_support']=='clarification'
