"""The private profile transports CORE's lexical arguments unchanged."""
from copy import deepcopy

import pytest

from core.lexical_planner import plan as lexical_plan
from pilot_backend.collation import _profile_matches_request_plan, collect


def request(question, targets, **extra):
    spans = []
    position = 0
    for target in targets:
        start = question.index(target, position)
        spans.append({'text': target, 'start': start, 'end': start + len(target),
                      'basis': 'fixture authoritative lexical argument'})
        position = start + len(target)
    return {'version': 'klikai.request-plan.v1', 'original': question, 'context': '',
            'target_spans': spans, 'requested_facets': [], 'requested_grammar_slots': [],
            'ambiguities': [], **extra}


def response(plan, analysis=None):
    interpretation = lexical_plan(plan['original'], plan['context'], 'sl',
                                  analysis or {'raw_engines': {}}, request_plan=plan)
    return {'request_schema_version': 'linghub.lexical-profile.v1',
            'query': {'text': plan['original'], 'context': plan['context']},
            'interpretation': interpretation,
            'provenance': {'resource_editions': {}}}


def test_construction_check_cannot_replace_the_named_argument_with_the_evaluative_adjective():
    question = 'Kako pravilno sklanjamo moško ime Luka? Ali je v zvezi »brez Luke« rodilnik pravilen?'
    start = question.index('brez Luke')
    plan = request(question, ['Luka'], requested_facets=['genitive'],
                   requested_grammar_slots=[{'case': 'genitive', 'number': None}],
                   explicit_lexemes=[{'text': 'Luka', 'start': question.index('Luka'),
                       'end': question.index('Luka') + 4, 'lexical_kind': 'proper_name',
                       'gender': 'masculine', 'basis': 'explicit lexical identity; original surface'}],
                   usage_context_spans=[{'text': 'brez Luke', 'start': start,
                       'end': start + len('brez Luke'), 'basis': 'quoted usage context; original surface'}])
    adjective = question.index('pravilen')
    analysis = {'raw_engines': {'classla': {'analysis': {'words': [
        {'text': 'pravilen', 'lemma': 'pravilen', 'upos': 'ADJ',
         'start': adjective, 'end': adjective + 8, 'feats': {}}]}}}}
    before = deepcopy(plan)
    profile = response(plan, analysis)
    assert [row['text'] for row in profile['interpretation']['targets']] == ['Luka']
    assert profile['interpretation']['usage_context_spans'] == plan['usage_context_spans']
    assert profile['interpretation']['explicit_lexemes'] == plan['explicit_lexemes']
    assert profile['interpretation']['slot_focus']['case'] == 'genitive'
    assert _profile_matches_request_plan(profile, plan)
    assert plan == before


@pytest.mark.parametrize('intent', ['usage_choice', 'dictionary_information'])
def test_form_choice_scope_is_copied_without_an_analyzer_voting_over_it(intent):
    question = 'Markota ali Marka?'
    choice = {'version': 'language-form-choice-v1', 'alternatives': ['Markota', 'Marka'],
              'intent': intent, 'interpretation': 'conditional',
              'explicit_lexemes': [], 'distinct_referents': False}
    plan = request(question, ['Markota', 'Marka'], form_choice=choice)
    profile = response(plan)
    assert profile['interpretation']['form_choice'] == choice
    assert profile['interpretation']['slot_focus'] is None
    assert _profile_matches_request_plan(profile, plan)
    profile['interpretation']['form_choice']['intent'] = 'different'
    assert plan['form_choice']['intent'] == intent
    assert not _profile_matches_request_plan(profile, plan)


def test_collection_carries_the_same_bound_plan_into_the_one_profile_request():
    question = 'Marka ali Markota?'
    plan = request(question, ['Marka', 'Markota'], form_choice={
        'version': 'language-form-choice-v1', 'alternatives': ['Marka', 'Markota'],
        'intent': 'usage_choice', 'interpretation': 'conditional',
        'explicit_lexemes': [], 'distinct_referents': False})
    class Gateway:
        lexical_profiles = True
        linghub_url = 'synthetic-private-profile'
        def __init__(self):
            self.requests = []
        def request(self, path, payload, base):
            self.requests.append((path, deepcopy(payload)))
            if path == '/v1/capabilities':
                return {'lexical_profile': {'available': True}}
            assert path == '/v1/lexical/profile'
            assert payload['request_plan'] == plan
            return response(payload['request_plan'])
    gateway = Gateway()
    captured = collect(gateway, {'question': question, 'input_language': 'sl',
                                'request_plan': plan}, automatic=True)
    assert [path for path, _ in gateway.requests] == ['/v1/capabilities', '/v1/lexical/profile']
    assert captured['captures'][0]['response']['interpretation']['form_choice'] == plan['form_choice']


def test_missing_new_scope_is_detected_at_the_existing_profile_binding_boundary():
    question = 'Marka ali Markota?'
    plan = request(question, ['Marka', 'Markota'], form_choice={
        'version': 'language-form-choice-v1', 'alternatives': ['Marka', 'Markota'],
        'intent': 'dictionary_information', 'interpretation': 'conditional',
        'explicit_lexemes': [], 'distinct_referents': False})
    profile = response(plan)
    profile['interpretation'].pop('form_choice')
    assert not _profile_matches_request_plan(profile, plan)
