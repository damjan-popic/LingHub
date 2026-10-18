"""Generalized target/selection fixtures, without private retained question dumps."""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit
import pytest
from core.lexical_planner import plan
from core.lexical_focus import focus_profile
from pilot_backend import collation, unified


@pytest.mark.parametrize('question,target', [
    ('Kaj je barvanka?', 'barvanka'),
    ('kaj pomeni modra mapa', 'modra mapa'),
    ('Iščem sopomenke za brez težav.', 'brez težav'),
    ('Kateri je pravilen prevod za modro mapo?', 'modro mapo'),
    ('Kateri sklon je modrem zvezku?', 'modrem zvezku'),
    ('Kako prevedemo tempo pieno v slovenščino?', 'tempo pieno'),
    ('Kaj pomeni izraz non vedo l’ora?', 'non vedo l’ora'),
])
def test_natural_request_keeps_entire_case_preserving_object(question,target):
    interpretation=plan(question,'','und',{'raw_engines':{}})
    assert [x['text']for x in interpretation['targets']]==[target]
    shared=interpretation['request_plan']
    assert shared['original']==question
    for span in shared['target_spans']:
        assert question[span['start']:span['end']]==span['text']
    enriched=plan(question,'','und',{'raw_engines':{}},request_plan=shared)
    assert enriched['targets']==interpretation['targets']
    assert enriched['query_kind']==interpretation['query_kind']


def test_explicit_domain_and_direction_are_separate_from_the_target():
    question='Kako prevedemo modro mapo v bančnem kontekstu?'
    shared=plan(question,'','sl',{})['request_plan']
    assert shared['target_spans'][0]['text']=='modro mapo'
    assert shared['domain']=='bančnem'
    assert shared['translation_language']is None
    assert shared['ambiguities']==['translation_direction_unspecified']
    assert shared['jurisdiction']is None


@pytest.mark.parametrize('question,targets',[
    ('Kaj pomeni VMI v občinski upravi in kako se to pove slovensko?',['VMI']),
    ('Ali se izraz VMI uporablja samo v FVG?',['VMI']),
    ('Kako prevedemo carta verde? Ali je zelena izkaznica priporočeni izraz?',['carta verde','zelena izkaznica']),
    ('Kaj pomeni življenje v mestu?',['življenje v mestu']),
    ('Kako prevedemo društvo za kulturo in šport?',['društvo za kulturo in šport']),
    ('Kaj pomeni »dan in noč« v tem primeru?',['dan in noč']),
])
def test_task_clauses_and_scope_are_not_lexical_components(question,targets):
    original=plan(question,'','und',{})
    assert [t['text']for t in original['targets']]==targets
    enriched=plan(question,'','und',{},request_plan=original['request_plan'])
    assert original['targets']==enriched['targets'] and original['query_kind']==enriched['query_kind']
    assert all(question[t['start']:t['end']]==t['text']for t in original['targets'])
    if 'priporočeni'in question:assert 'recommendation'in original['request_plan']['requested_facets']


def test_compound_request_retains_domain_direction_and_context_binding():
    q='Kaj pomeni VMI v občinski upravi in kako se to pove slovensko?'
    shared=plan(q,'','und',{})['request_plan']
    assert shared['domain']=='občinski upravi' and shared['translation_language']=='sl'
    assert set(shared['requested_facets'])=={'meaning','translation'}
    followup='Ali se to uporablja tudi drugje?'
    bound=plan(followup,q,'und',{})['request_plan'];target,=bound['target_spans']
    assert target['span_scope']=='context' and target['text']=='VMI'
    assert (followup+'\n'+q)[target['start']:target['end']]=='VMI'
    usage=plan('Ali se izraz VMI uporablja samo v FVG?','','und',{})['request_plan']
    assert usage['geography'][0]['raw']=='v FVG' and 'usage'in usage['requested_facets']


def test_natural_routes_do_not_require_quotation_marks():
    for question in ['barvanka','kaj je barvanka?','kaj pomeni barvanka?']:
        routing=collation.automatic_plan(question)
        assert routing['focus']=='barvanka'
    routing=collation.plan('Iščem sopomenke za brez težav.','sl')
    assert routing['focus']=='brez težav'and routing['operations']==['synonyms']


def fixture_profile(kind='terminology'):
    unit={'record_id':'synthetic','entry_id':'test:term','target':'modro mapo',
          'kind':kind,'match_relation':'recorded_target',
          'provenance':{'resource_id':'synthetic','version':'fixture'},
          'fields':{'matched_forms':[{'term':'modra mapa','matched_query':'modro mapo',
            'match_tier':'morphology_exact','ambiguity_cost':0,
            'query_signature':'moder mapa','lexicon_release_id':'synthetic-lexicon'}]}}
    return {'interpretation':{'targets':[{'text':'modro mapo'}],'query_kind':'phrase',
              'requested_facets':['translation'],'slot_focus':None},
            'facets':{'morphology':{'data':[]}},'evidence':[unit]}


def test_inflected_translation_match_stays_primary_without_changing_capture():
    original=fixture_profile();before=deepcopy(original)
    selected=focus_profile(original)
    assert selected['evidence'][0]['selection']['state']=='primary'
    assert original==before
    for mutation in ['matched_query','lexicon_release_id','ambiguity_cost']:
        value=deepcopy(original)
        value['evidence'][0]['fields']['matched_forms'][0][mutation]=1 if mutation=='ambiguity_cost'else''
        assert focus_profile(value)['evidence'][0]['selection']['state']=='excluded'


def test_unverified_analyzer_alias_does_not_become_primary_identity():
    value=fixture_profile('synonyms')
    value['interpretation'].update(targets=[{'text':'Marina'}],requested_facets=['synonyms'],query_kind='word')
    value['evidence'][0].update(target='Marina',match_relation='headword_candidate',fields={'headword':'morje'})
    assert focus_profile(value)['evidence'][0]['selection']['state']=='alternative'


def test_retrieval_receives_same_original_target_as_profiles():
    question='Kaj pomeni modra mapa?'
    shared=plan(question,'','sl',{})['request_plan']
    class Gateway:
        klikai_url='http://127.0.0.1:8000'
        def __init__(self):self.queries=[]
        def request(self,path,payload,base):
            query=parse_qs(urlsplit(path).query)['q'][0];self.queries.append(query)
            hit={'source_slug':'fixture','source_display_name':'Fixture','external_id':'fixture:1','title':'modra mapa',
                 'rights_mode':'restricted','original_terms':['cartella blu'],
                 'standardized_terms':['modra mapa'],'proposals':[],'categories':[],
                 'subcategories':[],'rationale':['Synthetic retained domain qualification.']}
            return {'query':query,'sources':['fixture'],'count':1,'hits':[hit]}
    gateway=Gateway()
    rows,coverage=unified.collect_source(gateway,{'question':question,'locale':'sl','request_plan':shared},
        {'slug':'fixture','adapter_key':'luka_jsonl_terminology','display_name':'Fixture'})
    assert gateway.queries==['modra mapa']
    assert coverage['request_plan']==shared and coverage['original_question']==question
    assert rows[0]['capture']['rationale']==['Synthetic retained domain qualification.']


def test_explicit_context_resolves_one_target_without_rewriting_question():
    q='Ali se to uporablja tudi drugje?';context='Kaj pomeni modra mapa?'
    p=plan(q,context,'sl',{})
    target=p['targets'][0];effective=q+'\n'+context
    assert target['text']=='modra mapa'and target['span_scope']=='context'
    assert effective[target['start']:target['end']]=='modra mapa'
    assert p['original']==q and p['context']==context
    assert plan(q,context,'sl',{},request_plan=p['request_plan'])['targets']==p['targets']


@pytest.mark.parametrize('context',['','Nekaj ni jasno. Imam drugo vprašanje?','»modra mapa« ali »rdeča torba«'])
def test_unresolved_reference_does_not_promote_other_question_tokens(context):
    p=plan('Ali se to uporablja tudi drugje?',context,'sl',{})
    assert p['targets']==[] and 'unresolved_reference'in p['request_plan']['ambiguities']
