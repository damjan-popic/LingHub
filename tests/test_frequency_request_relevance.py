"""Frequency absence is useful only for the requested task, not default lookup."""
from copy import deepcopy

import pytest

from core import lexical_planner
from pilot_backend.lexical import reader, frequency_availability_notes


def interpretation(question, target, facets):
    start = question.index(target)
    shared = {'version': 'klikai.request-plan.v1', 'original': question,
        'context': '', 'target_spans': [{'text': target, 'start': start,
            'end': start + len(target), 'basis': 'synthetic exact original lexical object'}],
        'requested_facets': facets, 'requested_grammar_slots': [], 'ambiguities': []}
    result = lexical_planner.plan(question, '', 'und', {'raw_engines': {}},
        request_plan=shared)
    assert 'frequency' in result['inferred_facets'], 'The shared resource lookup default is retained'
    return result


def no_match_bundle(locale, request):
    target = (request.get('targets', [{'text': 'synthetic target'}])[0]['text']
        if isinstance(request, dict) else 'synthetic target')
    profile = {'interpretation': request, 'evidence': [], 'facets': {
        'morphology': {'status': 'no_match', 'data': [{'target': target,
            'status': 'no_match', 'result': {'status': 'no_match', 'lexemes': [],
                'truncated': False, 'next_offset': None,
                'provenance': {'resource_id': 'sloleks', 'version': '3.1', 'status': 'READY'}}}]}}}
    return {'locale': locale, 'analysis': {'version': 'klikai.lexical-collation.v1',
        'captures': [{'response': profile}]}}


def notes(bundle):
    before = deepcopy(bundle)
    cards = reader(bundle)
    assert bundle == before, 'Frozen interpretation, source status, data and provenance stay unchanged'
    return cards.get('availability_notes', [])


@pytest.mark.parametrize('locale', ['sl', 'it'])
@pytest.mark.parametrize('question,target,facets', [
    ('Prevod za agriturismo', 'agriturismo', ['translation']),
    ('Prevod za scuola', 'scuola', ['translation']),
    ('Come si traduce buongiorno in sloveno?', 'buongiorno', ['translation']),
    ('Prevedi izraz prendere una decisione.', 'prendere una decisione', ['translation']),
    ('Kako sklanjamo ime Miha?', 'Miha', ['inflection']),
    ('Sopomenke za hiter.', 'hiter', ['synonyms']),
    ('Kaj pomeni beseda »odločitev«?', 'odločitev', ['explanation']),
])
def test_default_frequency_lookup_does_not_add_unrequested_absence_notice(locale, question, target, facets):
    request = interpretation(question, target, facets)
    assert not notes(no_match_bundle(locale, request))


@pytest.mark.parametrize('locale', ['sl', 'it'])
@pytest.mark.parametrize('question,target,facets', [
    ('Kako pogosta je beseda agriturismo?', 'agriturismo', ['frequency']),
    ('Qual è la frequenza della parola scuola?', 'scuola', ['frequency']),
    ('Prevedi agriturismo in navedi pogostnost.', 'agriturismo', ['translation', 'frequency']),
    ('Traduci buongiorno e indica la frequenza.', 'buongiorno', ['translation', 'frequency']),
])
def test_explicit_frequency_and_mixed_translation_requests_keep_complete_no_match_notice(locale, question, target, facets):
    value = notes(no_match_bundle(locale, interpretation(question, target, facets)))
    assert len(value) == 1 and target in value[0]
    assert ('ni primerljivega podatka' if locale == 'sl' else 'non è disponibile un dato comparabile') in value[0]


@pytest.mark.parametrize('locale', ['sl', 'it'])
@pytest.mark.parametrize('outer,inner,expected', [
    (['frequency'], ['translation'], False),
    (['translation'], ['frequency'], True),
    ([], ['translation', 'frequency'], True),
    (['frequency'], [], False),
])
def test_shared_task_facets_take_priority_over_stale_outer_profile_metadata(locale, outer, inner, expected):
    request = interpretation('Prevod za scuola.', 'scuola', inner)
    request['requested_facets'] = outer
    assert bool(notes(no_match_bundle(locale, request))) is expected


@pytest.mark.parametrize('locale', ['sl', 'it'])
@pytest.mark.parametrize('explicit,inferred,expected', [
    (['translation'], ['frequency'], False),
    ([], ['frequency'], False),
    (['frequency'], [], True),
    (['translation', 'frequency'], ['frequency'], True),
])
def test_legacy_snapshot_uses_explicit_request_metadata_without_reinterpreting_old_text(locale, explicit, inferred, expected):
    request = {'original': 'Saved text is not reparsed by the reader.',
        'targets': [{'text': 'saved target'}], 'requested_facets': explicit,
        'inferred_facets': inferred, 'slot_focus': None}
    assert bool(notes(no_match_bundle(locale, request))) is expected


@pytest.mark.parametrize('case', ['unknown', 'unavailable', 'partial', 'truncated', 'next_page'])
def test_even_requested_frequency_does_not_relabel_unknown_or_incomplete_source_result_as_absence(case):
    bundle = no_match_bundle('sl', interpretation('Pogostnost besede šola.', 'šola', ['frequency']))
    profile = bundle['analysis']['captures'][0]['response']
    facet = profile['facets']['morphology']
    row = facet['data'][0]['result']
    if case in {'unknown', 'unavailable', 'partial'}:
        facet['status'] = case
    elif case == 'truncated':
        row['truncated'] = True
    else:
        row['next_offset'] = 10
    assert not notes(bundle)


@pytest.mark.parametrize('request_metadata', [None, {}, {'requested_facets': None},
    {'requested_facets': 'frequency'}, {'requested_facets': ['translation'], 'inferred_facets': ['frequency']},
    {'requested_facets': ['frequency'], 'request_plan': {'requested_facets': 'frequency'}}])
def test_unknown_or_malformed_relevance_metadata_cannot_claim_a_requested_frequency_absence(request_metadata):
    profile = no_match_bundle('sl', request_metadata)['analysis']['captures'][0]['response']
    assert profile['facets']['morphology']['status'] == 'no_match'
    assert not frequency_availability_notes(profile, 'sl')


@pytest.mark.parametrize('question', ['Prevod za agriturismo', 'Prevod za buongiorno',
    'Traduci scuola in sloveno.', 'Traduzione di prendere una decisione.'])
def test_existing_unshared_translation_planning_keeps_default_frequency_lookup_without_notice(question):
    request = lexical_planner.plan(question, '', 'und', {'raw_engines': {}})
    assert 'translation' in request['requested_facets']
    assert 'frequency' in request['inferred_facets']
    assert not notes(no_match_bundle('sl', request))


PRE_EDIT_PLANNER_IDENTITIES = [{'case': 'translation_sl', 'question': 'Prevod za agriturismo', 'shared': {'version': 'klikai.request-plan.v1', 'original': 'Prevod za agriturismo', 'context': '', 'target_spans': [{'text': 'agriturismo', 'start': 10, 'end': 21, 'basis': 'synthetic original target'}], 'requested_facets': ['translation'], 'requested_grammar_slots': [], 'ambiguities': []}, 'expected_plan_sha256': '48ae97d49984c639b4762fb5a5907cb79e0781465d03c07e2a545abf62915078'}, {'case': 'translation_it', 'question': 'Come si traduce buongiorno in sloveno?', 'shared': {'version': 'klikai.request-plan.v1', 'original': 'Come si traduce buongiorno in sloveno?', 'context': '', 'target_spans': [{'text': 'buongiorno', 'start': 16, 'end': 26, 'basis': 'synthetic original target'}], 'requested_facets': ['translation'], 'requested_grammar_slots': [], 'ambiguities': []}, 'expected_plan_sha256': 'dce8d4ea2ee633f86ed6c0101d1f6bf4916b76dc9e9c5bde0cdb34c66084b2a2'}, {'case': 'frequency_sl', 'question': 'Pogostnost besede šola.', 'shared': {'version': 'klikai.request-plan.v1', 'original': 'Pogostnost besede šola.', 'context': '', 'target_spans': [{'text': 'šola', 'start': 18, 'end': 22, 'basis': 'synthetic original target'}], 'requested_facets': ['frequency'], 'requested_grammar_slots': [], 'ambiguities': []}, 'expected_plan_sha256': '3fc8db1ff3253001f47f3cb56d9bd896b83586212f4f381db42479c3cccaf9a3'}, {'case': 'frequency_it', 'question': 'Frequenza della parola scuola.', 'shared': {'version': 'klikai.request-plan.v1', 'original': 'Frequenza della parola scuola.', 'context': '', 'target_spans': [{'text': 'scuola', 'start': 23, 'end': 29, 'basis': 'synthetic original target'}], 'requested_facets': ['frequency'], 'requested_grammar_slots': [], 'ambiguities': []}, 'expected_plan_sha256': '120974ccdd186b6b796cb6ee0e08ff5bb53806c7146767a10e5a228f4ce5b499'}, {'case': 'mixed_sl', 'question': 'Prevedi agriturismo in navedi pogostnost.', 'shared': {'version': 'klikai.request-plan.v1', 'original': 'Prevedi agriturismo in navedi pogostnost.', 'context': '', 'target_spans': [{'text': 'agriturismo', 'start': 8, 'end': 19, 'basis': 'synthetic original target'}], 'requested_facets': ['translation', 'frequency'], 'requested_grammar_slots': [], 'ambiguities': []}, 'expected_plan_sha256': '33089361fab1a8e4eb080c2e3ef685a904f3b6110fb61faa84ea913621245885'}, {'case': 'mixed_it', 'question': 'Traduci scuola e indica la frequenza.', 'shared': {'version': 'klikai.request-plan.v1', 'original': 'Traduci scuola e indica la frequenza.', 'context': '', 'target_spans': [{'text': 'scuola', 'start': 8, 'end': 14, 'basis': 'synthetic original target'}], 'requested_facets': ['translation', 'frequency'], 'requested_grammar_slots': [], 'ambiguities': []}, 'expected_plan_sha256': '3703d6ffcbd261c5e682aa66dfb9fc3caed85ffc633d93c3c4cc2ee1b7aaa361'}, {'case': 'morphology', 'question': 'Rodilnik imena Miha.', 'shared': {'version': 'klikai.request-plan.v1', 'original': 'Rodilnik imena Miha.', 'context': '', 'target_spans': [{'text': 'Miha', 'start': 15, 'end': 19, 'basis': 'synthetic original target'}], 'requested_facets': ['genitive'], 'requested_grammar_slots': [], 'ambiguities': []}, 'expected_plan_sha256': 'ef8beca004c119ff9e566629496233055cc11c0832aacc6e9beb2011b4e56687'}, {'case': 'synonyms', 'question': 'Sopomenke za hiter.', 'shared': {'version': 'klikai.request-plan.v1', 'original': 'Sopomenke za hiter.', 'context': '', 'target_spans': [{'text': 'hiter', 'start': 13, 'end': 18, 'basis': 'synthetic original target'}], 'requested_facets': ['synonyms'], 'requested_grammar_slots': [], 'ambiguities': []}, 'expected_plan_sha256': 'b1b855a179bc606425c92dd08c458908dfcbe74590fc935340652fbabcfb5da4'}]


@pytest.mark.parametrize('case', PRE_EDIT_PLANNER_IDENTITIES, ids=lambda row: row['case'])
def test_shared_relevance_helper_does_not_rewrite_any_planner_output(case):
    from hashlib import sha256
    import json
    value = lexical_planner.plan(case['question'], '', 'und', {'raw_engines': {}},
        request_plan=case['shared'])
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')
    assert sha256(encoded).hexdigest() == case['expected_plan_sha256']
