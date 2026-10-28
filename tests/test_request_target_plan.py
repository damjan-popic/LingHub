"""Generalized target/selection fixtures, without private retained question dumps."""
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit
import pytest
from core.lexical_planner import plan
from core.lexical_focus import focus_profile
from pilot_backend import collation, unified
from pilot_backend.security import Failure


@pytest.mark.parametrize('question,target', [
    ('Kaj je barvanka?', 'barvanka'),
    ('kaj pomeni modra mapa', 'modra mapa'),
    ('Iščem sopomenke za brez težav.', 'brez težav'),
    ('Kateri je pravilen prevod za modro mapo?', 'modro mapo'),
    ('Kateri sklon je modrem zvezku?', 'modrem zvezku'),
    ('Kako prevedemo tempo pieno v slovenščino?', 'tempo pieno'),
    ('Kaj pomeni izraz non vedo l’ora?', 'non vedo l’ora'),
    ('Kako sklanjamo ime Luka?', 'Luka'),
    ('Kako sklanjamo ime Miha?', 'Miha'),
    ('Kako sklanjamo osebno ime Aljoša?', 'Aljoša'),
    ('Kako se sklanja vojvoda?', 'vojvoda'),
    ('Kako pregibamo samostalnik mati?', 'mati'),
    ('Kako lahko sklanjamo ime Oto?', 'Oto'),
    ('Kako se sklanja Novo mesto?', 'Novo mesto'),
    ('Kako sklanjamo modri zvezek?', 'modri zvezek'),
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


@pytest.mark.parametrize('description,name',[
    ('italijansko moško ime','Andrea'),('žensko ime','Andrea'),
    ('slovensko moško ime','Miha'),('tuje osebno ime','René'),
    ('francosko osebno ime','Jean Paul'),('poljski priimek','Kowalski'),
    ('MOŠKO IME','Aljoša'),('rojstno ime','Neznan'),
])
def test_named_object_preserves_descriptors_as_literal_context(description,name):
    question=f'Kako sklanjamo {description} {name}?'
    p=plan(question,'','sl',{});target,=p['targets']
    assert target['text']==name
    assert question[target['start']:target['end']]==name
    context=target['context_span']
    assert question[context['start']:context['end']]==f'{description} {name}'
    # The already-running analyzer takes these exact portable spans, even when
    # its local planner has not acquired the descriptor extraction change.
    assert plan(question,'','sl',{},request_plan=p['request_plan'])['targets']==p['targets']


@pytest.mark.parametrize('question,target',[
    ('Kako sklanjamo »italijansko moško ime Andrea«?','italijansko moško ime Andrea'),
    ('Kako prevedemo italijansko moško ime Andrea v slovenščino?','italijansko moško ime Andrea'),
    ('Kako sklanjamo dobro ime podjetja?','dobro ime podjetja'),
    ('Kako sklanjamo modri zvezek?','modri zvezek'),
    ('Kako sklanjamo novi direktor?','novi direktor'),
])
def test_name_descriptors_do_not_rewrite_literal_or_other_phrase_tasks(question,target):
    assert [t['text']for t in plan(question,'','sl',{})['targets']]==[target]


def test_name_descriptor_context_offsets_survive_explicit_followup():
    q='Kako se to sklanja?';context='Kako sklanjamo italijansko moško ime Andrea?'
    p=plan(q,context,'sl',{});target,=p['targets'];combined=q+'\n'+context
    assert target['text']=='Andrea'and target['span_scope']=='context'
    assert combined[target['start']:target['end']]=='Andrea'
    wrapper=target['context_span']
    assert combined[wrapper['start']:wrapper['end']]=='italijansko moško ime Andrea'
    assert plan(q,context,'sl',{},request_plan=p['request_plan'])['targets']==p['targets']


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


def test_supplied_plan_is_projected_and_preserved_while_analyzer_only_enriches_targets():
    question='Kako se sklanja ime Aljoša?'
    start=question.index('Aljoša')
    supplied={'version':'klikai.request-plan.v1','original':question,'context':'',
        'target_spans':[{'text':'Aljoša','start':start,'end':start+6,
                         'basis':'authenticated CORE interpretation'}],
        'candidate_lemmas':[],'operation':'rule_lookup','source_language':'sl',
        'translation_language':None,'response_language':None,
        'requested_facets':['genitive'],
        'requested_grammar_slots':[{'case':'genitive','number':None}],
        'domain':None,'jurisdiction':None,'requested_source':None,
        'requested_edition':None,'ambiguities':['synthetic_preserved_ambiguity'],
        'confidence':'bounded_surface'}
    analysis={'raw_engines':{'fixture':{'analysis':{'words':[{
        'text':'Aljoša','start':start,'end':start+6,'lemma':'Aljoša',
        'upos':'PROPN','feats':{'Case':'Nom'}}]}}}}
    result=plan(question,'','sl',analysis,request_plan=supplied)
    assert result['request_plan']==supplied and result['request_plan']is not supplied
    assert result['requested_facets']==supplied['requested_facets']
    assert result['requested_grammar_slots']==supplied['requested_grammar_slots']
    assert result['grammar_ambiguities']==supplied['ambiguities']
    assert result['targets'][0]['text']=='Aljoša'
    assert result['targets'][0]['lemma_candidates']==['Aljoša']


def authoritative_empty_plan(question,facets=None,ambiguities=None):
    return {'version':'klikai.request-plan.v1','interpretation_version':'fixture-v1',
        'original':question,'context':'','target_spans':[],
        'candidate_lemmas':[],'operation':None,'source_language':'sl',
        'translation_language':None,'response_language':'sl',
        'requested_facets':facets or [],'requested_grammar_slots':[],
        'domain':None,'jurisdiction':None,'requested_source':None,
        'requested_edition':None,'ambiguities':ambiguities or [],
        'confidence':'broad_routes'}


@pytest.mark.parametrize(('question','facets','ambiguities'),[
    ('Ali lahko danes pridem ali jutri?',[],[]),
    ('Kako se sklanjata imeni Marko in Hugo?',['inflection'],
     ['coordinated_target_unresolved']),
])
def test_authenticated_empty_plan_never_falls_through_to_web_targets(
        question,facets,ambiguities):
    supplied=authoritative_empty_plan(question,facets,ambiguities)
    result=plan(question,'','sl',{'raw_engines':{}},request_plan=supplied)
    assert result['targets']==[]
    assert result['request_plan']==supplied
    assert result['requested_facets']==facets
    assert result['grammar_ambiguities']==ambiguities


class ProfileProjectionGateway:
    lexical_profiles=True
    linghub_url='http://127.0.0.1:8010'
    klikai_url=None

    def __init__(self,response=None):
        self.response=response
        self.calls=[]

    def request(self,path,payload,base=None):
        self.calls.append((path,deepcopy(payload),base))
        if path=='/v1/capabilities':
            return {'lexical_profile':{'available':self.response is not None}}
        if path=='/v1/lexical/profile':return deepcopy(self.response)
        raise AssertionError(path)


def profile_response(question,request_plan):
    return {'request_schema_version':'linghub.lexical-profile.v1',
        'query':{'text':question},
        'interpretation':{'targets':[],
            'requested_facets':deepcopy(request_plan['requested_facets']),
            'requested_grammar_slots':[],
            'grammar_ambiguities':deepcopy(request_plan['ambiguities']),
            'request_plan':deepcopy(request_plan)},
        'provenance':{'resource_editions':{}}}


@pytest.mark.parametrize(('question','facets','ambiguities'),[
    ('Ali lahko danes pridem ali jutri?',[],[]),
    ('Kako se sklanjata imeni Marko in Hugo?',['inflection'],
     ['coordinated_target_unresolved']),
])
def test_enabled_profile_keeps_authenticated_empty_projection(
        question,facets,ambiguities):
    supplied=authoritative_empty_plan(question,facets,ambiguities)
    gateway=ProfileProjectionGateway(profile_response(question,supplied))
    result=collation.collect(gateway,{'question':question,'context':'',
        'input_language':'sl','locale':'sl','request_plan':supplied},automatic=True)
    capture,=result['captures']
    assert capture['response']['interpretation']['targets']==[]
    assert capture['response']['interpretation']['request_plan']==supplied
    assert [row[0]for row in gateway.calls]==[
        '/v1/capabilities','/v1/lexical/profile']


@pytest.mark.parametrize(('question','facets','ambiguities'),[
    ('Ali lahko danes pridem ali jutri?',[],[]),
    ('Kako se sklanjata imeni Marko in Hugo?',['inflection'],
     ['coordinated_target_unresolved']),
])
def test_unavailable_profile_does_not_use_web_semantic_fallback(
        question,facets,ambiguities):
    supplied=authoritative_empty_plan(question,facets,ambiguities)
    gateway=ProfileProjectionGateway()
    assert collation.collect(gateway,{'question':question,'context':'',
        'input_language':'sl','locale':'sl','request_plan':supplied},automatic=True) is None
    assert [row[0]for row in gateway.calls]==['/v1/capabilities']


@pytest.mark.parametrize('field',[
    'targets','requested_facets','requested_grammar_slots','grammar_ambiguities',
    'request_plan',
])
def test_enabled_profile_rejects_projection_outside_authenticated_plan(field):
    question='Ali lahko danes pridem ali jutri?'
    supplied=authoritative_empty_plan(question)
    response=profile_response(question,supplied)
    mutations={
        'targets':[{'text':'pridem','start':10,'end':16,
                    'basis':'local fallback'}],
        'requested_facets':['usage'],
        'requested_grammar_slots':[{'case':'genitive','number':None}],
        'grammar_ambiguities':['local_ambiguity'],
        'request_plan':dict(supplied,confidence='local_override'),
    }
    response['interpretation'][field]=mutations[field]
    gateway=ProfileProjectionGateway(response)
    with pytest.raises(Failure,match='invalid_answer'):
        collation.collect(gateway,{'question':question,'context':'',
            'input_language':'sl','locale':'sl','request_plan':supplied},
            automatic=True)


@pytest.mark.parametrize('context',['','Nekaj ni jasno. Imam drugo vprašanje?','»modra mapa« ali »rdeča torba«'])
def test_unresolved_reference_does_not_promote_other_question_tokens(context):
    p=plan('Ali se to uporablja tudi drugje?',context,'sl',{})
    assert p['targets']==[] and 'unresolved_reference'in p['request_plan']['ambiguities']
