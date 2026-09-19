"""Ordinary question routing with a read-only synthetic analyzer, never a writer.

The payload fixtures test provenance/offset transport; they are not linguistic
rules or expected answers. Actual installed rule findings live in private proof.
"""
from copy import deepcopy

import pytest

from pilot_backend import collation, unified
from pilot_backend.security import Failure
from pilot_backend.store import digest


@pytest.mark.parametrize('question',[
    'Ali je prav Novo mesto ali Novo Mesto?',
    'Katero začetnico uporabimo pri imenu Novo mesto?',
    'Kako se pravilno napiše »v Novem mestu«?',
    'Ali je prav Nova Gorica ali Nova gorica?',
    'Ali pišemo Stari trg ali Stari Trg?',
    'Ali pišemo Nova gorica ali Nova Gorica?',
    'Katero začetnico uporabimo pri imenu Kranjska Gora?',
    'Ali je prav disleksik ali dislektik?',
    'Kaj pomeni beseda »sopomenka«?',
])
def test_ordinary_linguistic_question_gets_bounded_rule_lookup(question):
    assert collation.plan(question) is None
    plan=collation.automatic_plan(question)
    assert plan['operations']==['loris'] and plan['input_language']=='sl'
    span=plan['focus_span']
    assert plan['focus']==question[span['start']:span['end']]
    assert span['offset_basis']=='original_question_unicode_codepoint_half_open'


@pytest.mark.parametrize('question',[
    'Kakšna je danes vremenska napoved?', 'zqxv987654',
    'Napiši pesem o pomladi.', 'What is the right spelling of this name?',
    'Katera so terminološka načela?', 'Ali je prav '+'beseda '*80,
])
def test_unrelated_or_outside_bound_has_no_automatic_call(question):
    assert collation.automatic_plan(question) is None


def test_comparison_casing_and_single_quoted_subject_keep_exact_offsets():
    for question in ['Ali je prav Novo mesto ali Novo Mesto?',
                     'Je pravilna začetnica v »Novo mesto« ali Novo Mesto?',
                     'Ali je prav »Novo mesto« ali »Novo Mesto«?']:
        assert collation.automatic_plan(question)['focus']==question
    question='🙂 Kako se pravilno napiše »v Novem mestu«?'
    plan=collation.automatic_plan(question)
    assert plan['focus']=='v Novem mestu'
    assert plan['focus_span']['start']==question.index('v Novem')
    assert plan['focus_span']['end']==question.index('«')


def test_language_basis_is_input_or_question_not_answer_locale():
    assert collation.automatic_plan('Come si scrive «Nova Gorica» in sloveno?')['input_language']=='sl'
    assert collation.automatic_plan('Come si scrive «parola»?')['input_language']=='it'
    assert collation.automatic_plan('Kako pišemo »parola«?','it')['language_basis']=='explicit_input_language'
    assert collation.automatic_plan('Kako pišemo »parola«?','mixed') is None
    assert collation.automatic_plan('Kako pišemo to v slovenščini in italiano?') is None
    assert collation.automatic_plan('Kako pišemo oziroma come si scrive?') is None


class Analyzer:
    linghub_url='http://127.0.0.1:8010'
    klikai_url=None

    def __init__(self,status='no_hit',fail=False):
        self.calls=[];self.status=status;self.fail=fail

    def request(self,path,payload,base):
        assert base==self.linghub_url
        self.calls.append((path,deepcopy(payload)))
        if self.fail:raise Failure('source_timeout',503)
        if path=='/v1/capabilities':
            return {'identity':'a'*64,'analyzer_service_reachable':True}
        assert path=='/v1/explore'
        issues=[]
        if self.status=='ok':
            start=payload['text'].index('Gorica')
            issues=[{'rule_id':'synthetic-component-rule','start':start,'end':start+6,
                     'category':'toponym','payload':{'note':'Synthetic component transport fixture.'}}]
        return {'text':payload['text'],'lang':payload['lang'],
                'offset_convention':'unicode_codepoint_half_open',
                'results':{operation:{'status':self.status,'data':{'issues':issues,'spans':deepcopy(issues),'tokens':[]},
                'provenance':{'source_id':'loris','rules_sha256':'b'*64}}
                for operation in payload['include']}}


def family(question='Ali je prav Novo mesto ali Novo Mesto?'):
    return {'question':question,'input_language':'und','locale':'it','task':'reference','context':''}


def test_no_match_is_frozen_as_diagnostic_without_fabricated_rule():
    gateway=Analyzer();capture,status=unified.collect_tools(gateway,family())
    assert capture['plan']['input_language']=='sl'  # The answer locale is Italian.
    request=capture['captures'][0]['request']
    assert request['text']==family()['question'] and request['include']==['loris']
    assert status['status']=='no_match' and status['rule_count']==0
    assert status['tool_outcome']=='no_match' and status['normative_conclusion']=='not_established'
    assert status['returned_count']==0 and status['evidence_type']=='tool_result'
    assert capture['capture_set_sha256']==digest(capture['captures'])
    response=capture['captures'][0]['response']
    assert response['results']['loris']['data']['issues']==[]
    assert capture['captures'][0]['response_sha256']==digest(response)
    assert [path for path,_ in gateway.calls]==['/v1/capabilities','/v1/explore','/v1/capabilities']


def test_partial_name_rule_keeps_exact_component_and_no_whole_name_claim():
    capture,status=unified.collect_tools(Analyzer('ok'),family('Ali je prav Nova Gorica ali Nova gorica?'))
    response=capture['captures'][0]['response']
    issue=response['results']['loris']['data']['issues'][0]
    assert response['text'][issue['start']:issue['end']]=='Gorica'
    assert status['rule_count']==1  # Same issue/span is one matched rule.
    assert status['match_scope']=='matched_span_only'
    assert status['normative_conclusion']=='not_established'
    assert issue['payload']=={'note':'Synthetic component transport fixture.'}


@pytest.mark.parametrize('status',['unavailable','unsupported','error'])
def test_analyzer_failure_is_not_no_match(status):
    capture,coverage=unified.collect_tools(Analyzer(status),family())
    assert capture is not None
    assert coverage['tool_outcome']=='unavailable'
    assert coverage['status']==('unsupported'if status=='unsupported'else'unavailable')


def test_automatic_route_is_opt_in_and_explicit_requested_tools_are_unchanged():
    assert collation.collect(Analyzer(),family()) is None
    explicit=family('Poišči sopomenke za »beseda«.');explicit['input_language']='sl'
    capture=collation.collect(Analyzer(),explicit,automatic=True)
    assert capture['plan']['routing_kind']=='explicit_requested_operations_and_quoted_focus'
    assert capture['captures'][0]['request']['include']==['synonyms']
    ambiguous=family('Poišči sopomenke za »beseda«.');gateway=Analyzer()
    assert collation.collect(gateway,ambiguous,automatic=True)['plan']['reason']=='input_language_required'
    assert gateway.calls==[]


def test_normal_unified_freeze_retains_documentary_sources_with_rule_no_match_or_outage():
    from test_pilot_unified import UnifiedGateway
    class OrdinaryGateway(UnifiedGateway):
        linghub_url='http://127.0.0.1:8010'
        def __init__(self,fail=False):
            super().__init__();self.linghub_url='http://127.0.0.1:8010';self.analyzer=Analyzer(fail=fail)
        def request(self,path,payload,base=None):
            if path in {'/v1/capabilities','/v1/explore'}:
                return self.analyzer.request(path,payload,base)
            return super().request(path,payload,base)
    for fail in (False,True):
        gateway=OrdinaryGateway(fail)
        result=gateway.freeze(family(),{})
        assert result['bundle']['matches']
        assert any(row['source_slug']=='zadrege'for row in result['bundle']['matches'])
        request=next(payload for path,payload,_ in gateway.calls if path=='/internal/pilot/freeze')
        assert request['question']==family()['question']
        coverage=next(row for row in request['unified_evidence']['retrieval']if row['family']=='linghub_tools')
        assert coverage['status']==('timeout'if fail else'no_match')
        assert ('tool_evidence'in request) is (not fail)
        assert not any(path.endswith('/generate')for path,_,_ in gateway.calls)


def test_source_local_ranks_interleave_without_cross_source_score_comparison():
    def row(source,number,score):
        return {'source_slug':source,'external_id':str(number),'capture_kind':'resource_record',
                'capture_sha256':source+str(number),'score':score,'context_text':'Immutable original.'}
    rows=[row('large',n,1_000_000-n)for n in range(3)]+[row('small',n,0.03-n/100)for n in range(3)]
    merged=unified.merge_source_matches(rows+[deepcopy(rows[0])])
    assert [row['source_slug']for row in merged]==['large','small']*3
    assert [row['external_id']for row in merged]==['0','0','1','1','2','2']
    assert unified.merge_source_matches(list(reversed(rows)))==merged
    assert {digest(row)for row in rows}=={digest(row)for row in merged}


def test_normative_capture_metadata_survives_gateway_round_trip_and_reader_projection():
    from test_pilot_unified import UnifiedGateway
    from pilot_backend.presentation import tester_view
    metadata={'source_status':'Dated current reference fixture','source_date':'2001',
              'release_id':'SP 2001 — pravila','coverage_note':'Limited captured sections only.',
              'normative_status':'current_reference'}
    class NormativeGateway(UnifiedGateway):
        def request(self,path,payload,base=None):
            result=super().request(path,payload,base)
            if path.startswith('/api/search?'):
                for hit in result['hits']:
                    if hit['source_slug']=='zadrege':hit.update(metadata)
            return result
    result=NormativeGateway().freeze(family('synthetic common expression'),{})
    record=next(row for row in result['bundle']['matches']if row['source_slug']=='zadrege')
    bibliography=record['source']['bibliography']
    assert all(bibliography[key]==value for key,value in metadata.items())
    reader=tester_view(record['source'],'synthetic-owner','synthetic-secret')
    assert reader['bibliography']['coverage_note']==metadata['coverage_note']
    assert reader['bibliography']['source_date']=='2001'
    assert reader['bibliography']['normative_status']=='current_reference'
    assert 'release_id'not in reader['bibliography']
