"""Offline retained/mock producer -> CORE ordinary units -> WEB consumer.

The lexical profile and dependencies are explicit fixtures, and the selection
is manual. No source, worker, model, server, or app is contacted.
"""
from copy import deepcopy
from types import SimpleNamespace as NS
from pathlib import Path
import os,sys
import pytest

root=Path(os.environ.get('KLIKAI_PINNED_CORE_ROOT','/home/damjan/projects/KLIKai-document-upload-20261111'))
if not(root/'tests/zrc/test_ordinary_contextual_consultation.py').is_file():
    pytest.skip('Select paired CORE contextual implementation',allow_module_level=True)
sys.path.insert(0,str(root/'tests/zrc'))
from test_ordinary_contextual_consultation import observation
from test_contextual_lexical_choices import saved
from tools.zrc_reference.request_interpretation import authoritative_request_plan,interpretation_input
from tools.zrc_reference.common import value_hash
from tools.zrc_reference.pilot_tool_evidence import validate_capture_set
from tools.zrc_reference.pilot_contracts import FreezeRequest
from tools.zrc_reference.pilot_contextual_consultation import VERSION
from pilot_backend import unified as web
from core import syntax_analysis


def family(question='Želimo narediti pogodbo.',context=''):
    return {'question':question,'context':context,'input_language':'sl','locale':'sl',
        'request_plan':authoritative_request_plan(question,context,'sl','sl')}


def lexical(f,case='COLL-P'):
    p=f['request_plan'];q={'text':f['question'],'context':f['context'],'lang':'sl','request_plan':p}
    response={'request_schema_version':'linghub.lexical-profile.v1','query':q,
        'interpretation':{'original':q['text'],'context':q['context'],'request_plan':p,
            'requested_facets':p['requested_facets'],'requested_grammar_slots':p['requested_grammar_slots'],
            'requested_morphology_features':p.get('requested_morphology_features',{}),
            'grammar_ambiguities':p['ambiguities'],'targets':deepcopy(p['target_spans']),'slot_focus':None,'query_kind':'question'},
        'evidence':[deepcopy(s['original_json'])for s in saved(case)],
        'analysis':{'analysis':{'words':[]}},'facets':{'morphology':{'data':None}},'coverage':{},'provenance':{'resource_editions':[]}}
    cap={'operation':'lexical_profile','request':q,'response':response,'response_sha256':value_hash(response),'duration_ms':0}
    slots=p.get('requested_grammar_slots')or[]
    if len(slots)==1:
        response['interpretation']['slot_focus']={k:slots[0][k]for k in ('case','number')}
        response['interpretation']['slot_focus']['basis']='grammatical question/context cue; verify against source slot'
        cap['response_sha256']=value_hash(response)
    capture={'version':'klikai.lexical-collation.v1','plan':{'status':'ready','routing_kind':'automatic_lexical_profile','focus':q['text']},
        'captures':[cap],'capture_set_sha256':value_hash([cap])}
    validate_capture_set(capture);return capture


def mock_analysis(monkeypatch,*,verb='narediti',adjective=False,absent=False):
    calls=[]
    def collect(text,config):
        calls.append(text)
        if absent:return {'status':'unavailable','reason':'mock_unavailable'}
        return observation(text,{'scope':'question','start':0,'end':len(text),'text':text},adjective=adjective,verb=verb)['captures'][0]['response']
    monkeypatch.setattr(syntax_analysis,'collect',collect)
    return calls


def test_collector_contextual_relations_get_one_exact_scope_and_simple_lists_forms_get_none(monkeypatch):
    calls=mock_analysis(monkeypatch)
    f=family('Kolokacije besede pogodbo?','   Želimo narediti pogodbo.')
    original=lexical(f);before=deepcopy(original)
    capture,status=web.collect_native_context_syntax(NS(comma_checker={'syntax_url':'mock'}),f,[],original)
    assert calls==['Želimo narediti pogodbo.']and capture is not None
    assert capture['captures'][0]['response']['target_scope']=={'scope':'context','start':3,'end':27,'text':'Želimo narediti pogodbo.'}
    assert status['capture_count']==1 and original==before
    reused,reuse_status=web.collect_native_context_syntax(NS(),f,[],original,existing_syntax=capture)
    assert reused==capture and reuse_status['tool_outcome']=='reused'and reuse_status['additional_requests']==0
    assert len(calls)==1
    for question in ['Kolokacije besede pogodbo?','Sopomenke besede hiter?','Kako se glasi tožilnik ednine imena Jupiter?','Preveri vejice: Želimo narediti pogodbo.']:
        f=family(question)
        assert web.collect_native_context_syntax(NS(),f,[],lexical(f))==(None,None)
    assert len(calls)==1


def test_collector_synonym_context_is_eligible_and_wrong_language_or_source_scope_is_not(monkeypatch):
    calls=mock_analysis(monkeypatch,adjective=True)
    f=family('Sopomenke besede hiter?','Po progi prihaja hiter vlak.')
    capture,status=web.collect_native_context_syntax(NS(comma_checker={'syntax_url':'mock'}),f,[],lexical(f,'SYN-P'))
    assert capture is not None and len(calls)==1
    for key,value in [('input_language','it')]:
        f[key]=value
        assert web.collect_native_context_syntax(NS(),f,[],lexical(f,'SYN-P'))==(None,None)
    assert len(calls)==1


def paired_delivery(monkeypatch,tmp_path):
    from test_native_context_producer import runtime as runtime_fixture
    from test_fulltext_grounded import store
    from test_ordinary_contextual_consultation import selected_output
    runtime=runtime_fixture.__wrapped__(store.__wrapped__(tmp_path))
    runtime.settings['unified_policy']['sources']=['gradivo']
    f=family('Kolokacije besede pogodbo?','   Želimo narediti pogodbo.')
    tool=lexical(f);before=deepcopy(tool);calls=mock_analysis(monkeypatch)
    class Gateway:
        klikai_url='mocked-registry'
        comma_checker={'syntax_url':'mock'}
        requests=[]
        def interpret(self,**kw):
            plan=authoritative_request_plan(kw['question'],kw['context'],kw['input_language'],kw['locale'])
            return {'contract':'klikai.request-interpretation.v1','input_sha256':value_hash(interpretation_input(**kw)),
                'request_plan':plan,'request_plan_sha256':value_hash(plan)}
        def request(self,path,body):
            assert path=='/internal/pilot/freeze';self.requests.append(deepcopy(body))
            try:return runtime.freeze(FreezeRequest.model_validate(body),fixed_evidence={'passages':[],'retrieval':'mocked-retained-only'})
            except Exception as error:
                self.failure=str(error)
                raise
    monkeypatch.setattr(web,'registry',lambda *a:[])
    monkeypatch.setattr(web,'collect_tools',lambda *a,**k:(deepcopy(tool),None))
    for name in ('expand_reference_sources','expand_request_reference_rules','expand_same_preposition_legend',
                 'expand_dictionary_inflection_legend','expand_o_name_declension_rules'):
        monkeypatch.setattr(web,name,lambda *a,**k:None)
    monkeypatch.setattr(web,'supplement_source_advice_morphology',lambda g,f,t,*a:t)
    gateway=Gateway();frozen=web.freeze(gateway,f,{})
    assert calls==['Želimo narediti pogodbo.']and tool==before
    assert frozen['bundle']['gateway_bundle']is not None,{'error':getattr(gateway,'failure',None),'retrieval':frozen['bundle']['retrieval']}
    payload=runtime._unpack(frozen['bundle']['gateway_bundle']);registry=runtime._answer_registry(payload,'answer')
    units=[u for u in registry.units if u.get('payload',{}).get('version')==VERSION]
    assert units,[(s.get('tool_kind'),s.get('context_eligible'))for s in payload['passages']]
    unit=next(u for u in units if u['payload']['primary']['groups'][0]['fields']['surface']=='podpisati pogodbo')
    result=selected_output(payload,runtime,registry,[unit])
    delivered=web.generate(NS(request=lambda *args:deepcopy(result)),frozen['bundle'],'answer','qwen')
    assert delivered['answer_md']==result['answer_md']and 'spremeni pomen'in delivered['answer_md']
    assert delivered['citations']and delivered['status']=='validated'
    return {'request':gateway.requests[0],'frozen':frozen,'payload':payload,
        'registry':registry.model_dump(),'core_result':result,'web_result':delivered,
        'mocked_analysis_inputs':calls,'manual_unit_selection':[unit['unit_id']]}


def test_mocked_normal_freeze_ordinary_render_and_web_consumer(monkeypatch,tmp_path):
    paired_delivery(monkeypatch,tmp_path)


def test_shared_scope_files_are_byte_identical():
    assert(root/'tools/zrc_reference/contextual_lexical_scope.py').read_bytes()==(Path(__file__).resolve().parents[1]/'pilot_backend/contextual_lexical_scope.py').read_bytes()
