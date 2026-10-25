"""One bounded corrective completion is observable, never a public raw trace."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from pilot_backend.human_feedback import derived_attempt_analysis, provider_diagnostics, attempt_case
from pilot_backend.jobs import render_result
from pilot_backend.presentation import tester_view as reader_view
from test_pilot_model_choices import ChoiceGateway, app_choices, submit, login, ORIGIN


PRIVATE='PRIVATE_RECOMPOSITION_PROMPT_OR_OUTPUT'


def summary(status='accepted'):
    return dict(status=status,selected_phase='corrective'if status=='accepted'else'primary',
        corrective_generation_calls=0 if status=='skipped'else 1,max_corrective_generation_calls=1,
        trigger_codes=['comparison_decision_removed_by_processing'],
        reason={'accepted':'required_content_restored','skipped':'corrective_provider_not_admitted',
            'retained_primary':'corrective_answer_still_inadequate'}[status])


def attempts(status='completed'):
    return [dict(route='shared',model='fixture-model',stage='generation',status='rejected',http_code=429),
        dict(route='own_cpu',model='fixture-model',stage='generation',status='completed'),
        dict(route='own_cpu',model='fixture-model',stage='corrective_generation',status=status)]


def accounting(rows):
    return dict(primary_provider_dispatches=sum(r['stage']=='generation'for r in rows),
        corrective_provider_dispatches=sum(r['stage']=='corrective_generation'for r in rows),
        completed_primary_calls=sum(r['stage']=='generation'and r['status']=='completed'for r in rows),
        completed_corrective_calls=sum(r['stage']=='corrective_generation'and r['status']=='completed'for r in rows),
        unknown_provider_outcomes=sum(r['status']=='unknown'for r in rows))


def phases(selected='corrective'):
    primary=json.dumps({'claims':[{'text':'Priloženi viri ne vsebujejo podatkov.'}],
        'no_sufficient_evidence':True})
    corrective=json.dumps({'claims':[{'text':'Supported option and condition.'}]})
    return dict(selected_phase=selected,raw_final_content=primary,status='returned',
        phases=[dict(phase='primary',status='returned',raw_final_content=primary,
            requests=[{'request':PRIVATE}],result_checks={'validation':{'status':'consistent_abstention'}}),
            dict(phase='corrective',status='returned',raw_final_content=corrective,
                requests=[{'request':PRIVATE}],result_checks={'validation':{'status':'bounded_checks_completed'}})])


def test_three_dispatches_keep_phase_and_explicit_counts_without_private_data():
    rows=attempts();value=dict(recomposition={**summary(),'prompt':PRIVATE},provider_attempts=deepcopy(rows),
        generation_accounting={**accounting(rows),'raw':PRIVATE},diagnostics={'writer':phases()})
    value['provider_attempts'][-1]['raw_response']=PRIVATE
    original=deepcopy(value);safe=provider_diagnostics(value)
    assert safe==dict(recomposition=summary(),provider_attempts=rows,generation_accounting=accounting(rows))
    assert provider_diagnostics(safe)==safe
    rendered,_=render_result({'status':'validated',**value})
    assert {key:rendered[key]for key in safe}==safe
    assert PRIVATE not in json.dumps(rendered)
    assert value==original
    public=reader_view(rendered,'owner','secret')
    assert not {'recomposition','provider_attempts'}&public.keys()
    assert public['generation_accounting']=={**accounting(rows),'provider_dispatches_total':3}
    assert PRIVATE not in json.dumps(public)


def test_reader_dispatch_projection_is_closed_bounded_and_route_only():
    counts=accounting(attempts())
    value={'generation_accounting':counts,
        'execution':{'selection':'auto','provider_scope':'shared',
            'route_reason':'shared_ready','private_url':PRIVATE},
        'provider_attempts':attempts(),'recomposition':summary(),'private':PRIVATE}
    public=reader_view(value,'owner','secret')
    assert public=={'generation_accounting':{**counts,'provider_dispatches_total':3},
        'execution':{'selection':'auto','provider_scope':'shared'}}
    assert PRIVATE not in json.dumps(public)


@pytest.mark.parametrize('change',[
    {'primary_provider_dispatches':True},
    {'primary_provider_dispatches':3},
    {'corrective_provider_dispatches':2},
    {'completed_primary_calls':3},
    {'unknown_provider_outcomes':4},
    {'private_counter':1},
])
def test_reader_dispatch_projection_rejects_malformed_or_expanded_accounting(change):
    counts={**accounting(attempts()),**change}
    public=reader_view({'generation_accounting':counts,
        'execution':{'selection':'auto','provider_scope':'shared'}},'owner','secret')
    assert 'generation_accounting'not in public and'execution'not in public


@pytest.mark.parametrize('mutate',[
    lambda v:v['recomposition'].update(corrective_generation_calls=2),
    lambda v:v['recomposition'].update(corrective_generation_calls=True),
    lambda v:v['recomposition'].update(selected_phase='primary'),
    lambda v:v['recomposition'].update(reason=PRIVATE),
    lambda v:v['recomposition'].update(trigger_codes=[PRIVATE]),
    lambda v:v['recomposition'].update(max_corrective_generation_calls=2),
    lambda v:v['provider_attempts'].append(deepcopy(v['provider_attempts'][-1])),
    lambda v:v['provider_attempts'].reverse(),
])
def test_unbounded_or_inconsistent_phase_rows_are_not_published(mutate):
    rows=attempts();value=dict(recomposition=summary(),provider_attempts=rows,generation_accounting=accounting(rows))
    mutate(value);safe=provider_diagnostics(value)
    assert 'provider_attempts'not in safe and 'generation_accounting'not in safe
    assert PRIVATE not in json.dumps(safe)


def test_unadmitted_corrective_request_does_not_count_as_dispatched():
    rows=attempts()[:-1];safe=provider_diagnostics(dict(recomposition=summary('skipped'),
        provider_attempts=rows,generation_accounting=accounting(rows)))
    assert safe['generation_accounting']['corrective_provider_dispatches']==0
    assert safe['recomposition']['corrective_generation_calls']==0
    assert len(safe['provider_attempts'])==2


def test_unknown_corrective_outcome_is_observed_while_primary_remains_selected():
    rows=attempts('unknown');value=dict(recomposition={**summary('retained_primary'),
        'reason':'corrective_completion_failed'},provider_attempts=rows,generation_accounting=accounting(rows))
    safe=provider_diagnostics(value)
    assert safe['generation_accounting']['unknown_provider_outcomes']==1
    assert safe['generation_accounting']['completed_corrective_calls']==0
    assert safe['recomposition']['selected_phase']=='primary'


def test_accounting_never_silently_relabels_inconsistent_observations():
    rows=attempts();value=dict(recomposition=summary(),provider_attempts=rows,generation_accounting=accounting(rows))
    value['generation_accounting']['primary_provider_dispatches']=1
    assert 'generation_accounting'not in provider_diagnostics(value)


def test_lone_corrective_dispatch_requires_explicit_historical_primary():
    rows=attempts()[-1:]
    value=dict(recomposition=summary(),provider_attempts=rows,generation_accounting=accounting(rows))
    assert 'provider_attempts'not in provider_diagnostics(value)
    value['recomposition']['primary_origin']='retained'
    safe=provider_diagnostics(value)
    assert safe['provider_attempts']==rows
    assert safe['generation_accounting']['primary_provider_dispatches']==0
    assert safe['generation_accounting']['corrective_provider_dispatches']==1
    value['provider_attempts']=attempts()
    assert 'provider_attempts'not in provider_diagnostics(value)


def test_uncorrected_private_primary_remains_historical_without_new_dispatch():
    value=summary('skipped');value.update(primary_origin='retained',trigger_codes=[],reason='correction_not_dispatched')
    safe=provider_diagnostics(dict(recomposition=value,provider_attempts=[],generation_accounting=accounting([])))
    assert safe['recomposition']==value and not any(safe['generation_accounting'].values())
    writer=phases('primary');writer['phases']=writer['phases'][:1];writer['phases'][0]['origin']='retained'
    derived=derived_attempt_analysis({'stages':{'generation':{'writer':writer}}},{})
    assert derived['selected_phase_trace_complete']and derived['content_kind']=='abstention'


@pytest.mark.parametrize('selected,content,stage',[
    ('primary','abstention','returned_abstention'),
    ('corrective','substantive_candidate','supported_output'),
])
def test_derived_analysis_uses_selected_raw_and_checks_preserving_original(selected,content,stage):
    writer=phases(selected);observed={'stages':{'generation':{'writer':writer,
        'result_checks':{'validation':{'status':'consistent_abstention'}}}}}
    original=deepcopy(observed)
    derived=derived_attempt_analysis(observed,{'claims':[{'text':'Actual displayed output'}]},delivered=1)
    assert derived['selected_phase']==selected and derived['selected_phase_trace_complete']
    assert derived['content_kind']==content and derived['validation_stage']==stage
    assert derived['writer_execution']=='returned_completion'
    assert derived['delivery_stage']=='delivered'
    assert observed==original


@pytest.mark.parametrize('mutate',[
    lambda w:w.pop('phases'),lambda w:w['phases'].pop(),
    lambda w:w['phases'].append(deepcopy(w['phases'][-1])),
    lambda w:w.update(selected_phase=['corrective']),
    lambda w:w['phases'][1].update(phase='primary'),
])
def test_missing_selected_phase_is_not_silently_replaced_by_primary(mutate):
    writer=phases();mutate(writer)
    derived=derived_attempt_analysis({'stages':{'generation':{'writer':writer}}},
        {'claims':[{'text':'Actual displayed output'}]})
    assert not derived['selected_phase_trace_complete']
    assert derived['content_kind']=='unknown'and derived['writer_execution']=='unknown'
    assert derived['validation_stage']=='selected_phase_trace_missing'


def test_legacy_no_phase_record_keeps_historical_analysis_shape():
    writer=phases();writer.pop('phases');writer.pop('selected_phase')
    derived=derived_attempt_analysis({'stages':{'generation':{'writer':writer}}},{})
    assert derived['content_kind']=='abstention'
    assert 'selected_phase'not in derived


def test_owned_api_persists_both_private_phases_and_returns_only_safe_summary(tmp_path):
    gateway=ChoiceGateway();request=gateway.request
    rows=attempts()
    def generate(path,payload,base=None):
        value=request(path,payload,base)
        if path=='/internal/pilot/generate':
            value.update(recomposition=summary(),provider_attempts=deepcopy(rows),
                generation_accounting=accounting(rows),diagnostics={'writer':phases()})
        return value
    gateway.request=generate;app=app_choices(tmp_path,gateway)
    with TestClient(app,base_url=ORIGIN)as client:
        family,job=submit(app,client,login(client));run=family['short']
        assert run['recomposition']==summary()and run['generation_accounting']==accounting(rows)
        assert PRIVATE not in json.dumps(family)
        with app.state.store.connect()as db:
            saved=attempt_case(db,job['run_id'])
            stored=json.loads(db.execute('SELECT result FROM runs WHERE id=?',(job['run_id'],)).fetchone()[0])
        assert saved['observation']['stages']['generation']['writer']==phases()
        assert saved['derived_analysis']['selected_phase']=='corrective'
        assert stored['provider_attempts']==rows and PRIVATE not in json.dumps(stored)
        gateway.blocked=True
        assert client.get('/api/pilot/families/'+family['answer_family_id']).json()==family
        assert sum(path=='/internal/pilot/generate'for path,_,_ in gateway.calls)==1
