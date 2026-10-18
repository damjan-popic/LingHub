"""Testing handover: private offline cases, grouping, failures and LORIS routing."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pilot_backend.api import create_app
from pilot_backend.human_feedback import attempt_case,export_attempts,record_stage
from pilot_backend.collation import automatic_plan,plan,collect
from pilot_backend.security import Failure
from test_pilot_backend import Adapter,PASSWORD,drain

ORIGIN='http://127.0.0.1:18310'

class DiagnosticAdapter(Adapter):
    mode='normal'
    def freeze(self,family,policy):
        if self.mode=='source_outage':raise Failure('source_timeout',503)
        out=super().freeze(family,policy)
        out['bundle']['analysis']={'status':'failed' if self.mode=='analyzer_failure' else 'no_match','reason':'injected_test' if self.mode=='analyzer_failure' else 'fixture_no_rule'}
        return out
    def generate(self,*args):
        if self.mode=='writer_failure':raise RuntimeError('Do not retain credentials token=SECRET')
        if self.mode=='writer_timeout':raise TimeoutError('Do not retain credentials token=SECRET')
        out=super().generate(*args)
        out['diagnostics']={'writer':{'status':'returned','requests':[{'request':{'messages':[{'role':'user','content':'Fixture only'}],'temperature':0},'actual_model':{'model_id':'fixture'}}],'raw_final_content':'{"claims":[]}'}}
        if self.mode=='empty':out.update(status='no_hit',generation={'status':'no_support'})
        return out

@pytest.fixture
def testing(tmp_path):
    adapter=DiagnosticAdapter()
    app=create_app({'store':str(tmp_path/'state'/'pilot.sqlite'),'allowed_origins':[ORIGIN],'secure_cookies':False,'human_testing_mode':True,'release_id':'fixture-testing-r1'},adapter,PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1),start_workers=False)
    ids={name:app.state.auth.create(name,PASSWORD,origin='human',cohort='project_beta',must_change=False) for name in ('cu','slori','tehno')}
    return app,adapter,ids

def submit(app,owner,question='Fixture query',**extra):
    import uuid
    return app.state.domain.create_family(owner,{'question':question,'idempotency_key':str(uuid.uuid4()),**extra})

def case(app,rid):
    with app.state.store.connect() as db:return attempt_case(db,rid)

@pytest.mark.parametrize('word,focus',[('lesenka','lesenka'),('lesenko','lesenko'),(' lesenka? ','lesenka'),('  “Živjo”?  ','Živjo'),('«città»','città'),('slovens\u030cc\u030cina','slovens\u030cc\u030cina'),('loris','loris'),('morfologija','morfologija')])
def test_single_word_original_span_and_explicit_policy(word,focus):
    p=automatic_plan(word);assert p['focus']==focus
    assert p['lookup_languages']==['sl','it'] and p['routing_kind']=='single_word_rule_lookup'
    assert 'not_detection' in p['language_basis']
    span=p['focus_span'];assert word[span['start']:span['end']]==focus
    assert automatic_plan(word,'it')['lookup_languages']==['it']
    assert plan(word) is None

@pytest.mark.parametrize('value',['','  ','123','?!','https://example.com','lesenka test','foo/bar','45.6','a_b'])
def test_nonlexical_is_not_single_word(value):
    p=automatic_plan(value);assert not p or p['routing_kind']!='single_word_rule_lookup'

def test_ordinary_full_question_route_preserved():
    assert automatic_plan('Kaj pomeni lesenka?')['reason']=='lexical_meaning'
    assert automatic_plan('Ali pišemo Novo mesto ali Novo Mesto?')['routing_kind']=='automatic_question_rule_lookup'

def test_two_language_captures_and_one_analyzer_failure_do_not_erase_other_result():
    class G:
        linghub_url='http://fixture';klikai_url=None
        def request(self,path,payload=None,base=None):
            if path=='/v1/capabilities':return {'identity':'a'*64,'analyzer_service_reachable':True}
            if payload['lang']=='it':raise Failure('source_timeout',503)
            return {'text':payload['text'],'lang':'sl','offset_convention':'unicode_codepoint_half_open','results':{'loris':{'status':'no_hit','data':{'tokens':[{'text':'lesenka','lemma':'lesenek','pos':'PROPN','start':0,'end':7}]}}}}
    c=collect(G(),{'question':'lesenka','locale':'sl','input_language':'und'},automatic=True)
    assert [x['request']['lang'] for x in c['captures']]==['sl','it']
    assert c['captures'][1]['response']['results']['loris']['status']=='unavailable'
    assert c['captures'][0]['response']['results']['loris']['data']['tokens'][0]['lemma']=='lesenek'

def test_every_attempt_group_spoofing_and_offline_export(testing,tmp_path):
    app,adapter,ids=testing;created={name:submit(app,owner) for name,owner in ids.items()}
    for name,c in created.items():
        before=case(app,c['family']['short']['run_id']);assert before['summary']['group']==name
        assert before['summary']['outcome']=='queued' and before['summary']['rating']=='unrated'
    with pytest.raises(Failure):submit(app,ids['cu'],group='tehno')
    with pytest.raises(Failure):submit(app,ids['cu'],origin='project_use')
    automated=submit(app,ids['cu'],origin='automated_test')
    drain(app)
    assert case(app,automated['family']['short']['run_id'])['summary']['origin']=='automated_test'
    # Model/search denied for the entire export, including case details.
    with patch.object(adapter,'freeze',side_effect=AssertionError('offline')),patch.object(adapter,'generate',side_effect=AssertionError('offline')):
        result=export_attempts(app.state.store,tmp_path/'project')
        assert result['counts']['attempts']==2 and result['counts']['rated_outputs']==0
        rows=[json.loads(x) for x in (tmp_path/'project/attempts.jsonl').read_text().splitlines()]
        assert {r['group'] for r in rows}=={'cu','slori'}
        export_attempts(app.state.store,tmp_path/'technical',groups=['tehno'])
        rid=created['tehno']['family']['short']['run_id'];saved=json.loads((tmp_path/'technical'/f'{rid}.json').read_text())
        assert saved['observation']['stages']['generation']['writer']['requests']
        assert saved['display']['blocks'] and saved['frozen_sources']
        assert (tmp_path/'technical'/f'{rid}.json').stat().st_mode&0o077==0

@pytest.mark.parametrize('mode,outcome,stage',[('source_outage','timed_out','retrieval_and_freeze'),('writer_failure','failed','writer'),('writer_timeout','timed_out','writer'),('empty','no_support',None),('analyzer_failure','completed',None)])
def test_injected_failures_survive_with_no_feedback(testing,mode,outcome,stage):
    app,adapter,ids=testing;adapter.mode=mode;c=submit(app,ids['cu']);drain(app)
    saved=case(app,c['family']['short']['run_id']);assert saved['summary']['outcome']==outcome
    assert saved['summary']['rating']=='unrated' and 'SECRET' not in json.dumps(saved)
    if stage:assert saved['observation']['stages']['failure']['stage']==stage
    if mode=='analyzer_failure':assert saved['frozen_sources']['analysis']['status']=='failed'

def test_cancel_retained_and_missing_capture_is_explicit(testing,tmp_path):
    app,adapter,ids=testing;c=submit(app,ids['cu']);app.state.domain.cancel(ids['cu'],c['job']['job_id'])
    saved=case(app,c['family']['short']['run_id']);assert saved['summary']['outcome']=='cancelled'
    c=submit(app,ids['cu']);drain(app)
    assert not record_stage(app.state.store,c['family']['short']['run_id'],'broken',{'not_json':set()})
    saved=case(app,c['family']['short']['run_id']);assert saved['summary']['trace_incomplete']
    assert saved['missing_fields'][0]['cause']['error_type']=='TypeError'

def test_failure_feedback_delivery_hash_and_ownership(testing):
    app,adapter,ids=testing;adapter.mode='writer_failure';c=submit(app,ids['cu']);drain(app)
    d=app.state.domain;f=d.family(ids['cu'],c['family']['answer_family_id']);r=f['short']
    with pytest.raises(Failure):d.family(ids['slori'],f['answer_family_id'])
    with pytest.raises(Failure):d.delivered(ids['cu'],r['run_id'],{'display_sha256':'wrong'})
    d.delivered(ids['cu'],r['run_id'],{'display_sha256':r['display_sha256']})
    payload={'run_id':r['run_id'],'rating':'not_useful','comment':'Technical failure fixture','idempotency_key':'fixture-feedback-1'}
    d.feedback(ids['cu'],payload)
    d.feedback(ids['cu'],{**payload,'comment':'Updated fixture','idempotency_key':'fixture-feedback-2'})
    saved=case(app,r['run_id']);assert saved['summary']['rating']=='not_useful'
    assert len(saved['feedback_revisions'])==2
    assert {e['display_hash'] for e in saved['feedback_revisions']}=={r['display_sha256']}
    with pytest.raises(Failure):d.feedback(ids['slori'],payload)

def test_same_login_independent_sessions_and_retry_link(testing):
    app,adapter,ids=testing
    with TestClient(app,base_url=ORIGIN) as a,TestClient(app,base_url=ORIGIN) as b:
        sa=a.post('/api/pilot/login',headers={'Origin':ORIGIN},json={'username':'cu','password':PASSWORD}).json()
        sb=b.post('/api/pilot/login',headers={'Origin':ORIGIN},json={'username':'cu','password':PASSWORD}).json()
        assert sa['csrf_token']!=sb['csrf_token'] and sa['user']['id']==sb['user']['id']==ids['cu']
        first=submit(app,ids['cu']);second=submit(app,ids['cu'],parent_run_id=first['family']['short']['run_id'])
        assert first['family']['short']['run_id']!=second['family']['short']['run_id']
        assert case(app,second['family']['short']['run_id'])['summary']['parent_run_id']==first['family']['short']['run_id']
        with pytest.raises(Failure):submit(app,ids['slori'],parent_run_id=first['family']['short']['run_id'])


def test_derived_stages_preserve_returned_refusal_and_historical_labels():
    import copy
    from pilot_backend.human_feedback import derived_attempt_analysis
    observed={'stages':{'generation':{'writer':{'status':'failed','raw_final_content':json.dumps({
        'claims':[{'text':'Priloženi viri ne vsebujejo podatkov.'}],'no_sufficient_evidence':True})},
        'result_checks':{'validation':{'status':'invalid_composition_envelope','reason':'abstention_contradicts_claims'}}}}}
    old=copy.deepcopy(observed)
    result=derived_attempt_analysis(observed,{'status':'failed'})
    assert observed==old
    assert result['writer_execution']=='returned_completion'
    assert result['content_kind']=='abstention'
    assert result['validation_stage']=='invalid_composition'
    assert result['delivery_stage']=='not_observed'


def test_derived_stages_separate_no_writer_all_withheld_partial_and_delivery():
    from pilot_backend.human_feedback import derived_attempt_analysis
    assert derived_attempt_analysis({'stages':{'generation':{'writer':{'status':'not_invoked'}}}}, {})['validation_stage']=='no_writer_invoked'
    observed={'stages':{'generation':{'writer':{'status':'returned','raw_final_content':'{"claims":[{"text":"Some candidate"}]}'},
        'result_checks':{'validation':{'status':'all_claims_withheld'}}}}}
    assert derived_attempt_analysis(observed, {})['validation_stage']=='all_claims_withheld'
    observed['stages']['generation']['result_checks']={'omitted_claims':[{'claim_id':'C2'}]}
    result=derived_attempt_analysis(observed,{'claims':[{'claim_id':'C1'}]},delivered=0)
    assert result['validation_stage']=='partial_supported_output' and result['delivery_stage']=='delivered'


def test_inconsistent_abstention_flag_does_not_relabel_substantive_content():
    from pilot_backend.human_feedback import derived_attempt_analysis
    observed={'stages':{'generation':{'writer':{'status':'returned','raw_final_content':json.dumps({
        'claims':[{'text':'Izraz A je samostalnik.'}],'no_sufficient_evidence':True})},
        'result_checks':{'validation':{'status':'invalid_composition_envelope','reason':'abstention_contradicts_claims'}}}}}
    result=derived_attempt_analysis(observed,{})
    assert result['writer_execution']=='returned_completion'
    assert result['content_kind']=='substantive_candidate'
    assert result['validation_stage']=='invalid_composition'
