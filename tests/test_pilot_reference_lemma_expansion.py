"""Reference discovery follows recorded morphology, not analyzer guesses."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from pilot_backend import unified
from pilot_backend.security import Failure
from pilot_backend.store import digest


def profile_for(pairs):
    question = 'Pojasnite ' + ', '.join(target for target, _ in pairs) + '.'
    targets, evidence = [], []
    cursor = 0
    for index, (target, lemma) in enumerate(pairs):
        start = question.index(target, cursor); cursor = start + len(target)
        targets.append({'text': target, 'start': start, 'end': cursor})
        entry = 'entry-' + str(index)
        evidence.append({'kind': 'morphology', 'target': target, 'match_relation': 'recorded_target',
            'captured_unit_id': 'unit-' + str(index), 'entry_id': entry,
            'provenance': {'resource_id': 'sloleks', 'source_entry_id': entry},
            'fields': {'source_entry_id': entry, 'lemma': lemma, 'matched_by': 'recorded_form',
                       'submitted_form': target, 'submitted_analyses': [{'exact_surface': True}]}})
    plan = {'original': question, 'context': '', 'target_spans': targets}
    family = {'question': question, 'original_question': question, 'context': '', 'locale': 'sl', 'request_plan': plan}
    response = {'query': {'text': question, 'context': ''},
                'interpretation': {'targets': targets, 'request_plan': plan}, 'evidence': evidence}
    return family, {'captures': [{'operation': 'lexical_profile', 'response': response}]}


@pytest.mark.parametrize('target,lemma', [('ljudmi', 'človek'), ('matere', 'mati'), ('psa', 'pes'),
                                         ('otroku', 'otrok'), ('otrokom', 'otrok'), ('Luka', 'Luka')])
def test_resource_attested_paradigm_mapping(target, lemma):
    family, capture = profile_for([(target, lemma)])
    result = unified.validated_reference_lemmas(capture, family)
    assert [row['query'] for row in result] == [lemma]
    assert result[0]['source_records'] == [{'target': target, 'resource_id': 'sloleks',
        'source_entry_id': 'entry-0', 'captured_unit_id': 'unit-0', 'match_basis': 'recorded_form'}]


@pytest.mark.parametrize('defect', ['analyzer_only', 'unrecorded_target', 'incorrect_offset', 'other_question',
                                   'entry_mismatch', 'inexact_surface', 'other_surface', 'component_only'])
def test_unattested_nominations_cannot_expand_discovery(defect):
    family, capture = profile_for([('ljudmi', 'človek')])
    profile = capture['captures'][0]['response']; unit = profile['evidence'][0]
    if defect == 'analyzer_only': profile['evidence'] = []
    if defect == 'unrecorded_target': unit['target'] = 'drugi'
    if defect == 'incorrect_offset': profile['interpretation']['targets'][0]['start'] = 0
    if defect == 'other_question': profile['query']['text'] = 'Drugo vprašanje'
    if defect == 'entry_mismatch': unit['provenance']['source_entry_id'] = 'different'
    if defect == 'inexact_surface': unit['fields']['submitted_analyses'][0]['exact_surface'] = False
    if defect == 'other_surface': unit['fields']['submitted_form'] = 'ljudi'
    if defect == 'component_only': unit['match_relation'] = 'component_only'
    profile['interpretation']['lemma_candidates'] = [{'lemma': 'človek', 'confidence': 1.0}]
    assert unified.validated_reference_lemmas(capture, family) == []


def test_distinct_attested_identities_and_case_are_preserved():
    family, capture = profile_for([('Luka', 'Luka'), ('Luka', 'luka'), ('psa', 'pes'), ('psa', 'pes')])
    result = unified.validated_reference_lemmas(capture, family)
    assert [row['query'] for row in result] == ['Luka', 'luka', 'pes']
    assert len(result[2]['source_records']) == 2


class ReferenceGateway:
    klikai_url = 'synthetic-api'
    editorial_full_records = False

    def __init__(self, *, failed=None, empty=False):
        self.calls = []; self.failed = failed; self.empty = empty

    def request(self, path, payload, base):
        assert payload is None and base == self.klikai_url
        parsed = urlsplit(path); assert parsed.path == '/api/search'
        params = parse_qs(parsed.query); query = params['q'][0]; source = params['sources'][0]
        self.calls.append((source, query))
        if query == self.failed: raise Failure('source_timeout', 503)
        hit = {'source_slug': source, 'resource_id': 'record-' + query, 'title': query,
            'rights_mode': 'owned', 'snippet': 'Complete entry: ' + query, 'original_text': 'Complete entry: ' + query,
            'score': 100, 'score_method': 'official_reference_exact_headword',
            'published_headword': query, 'semantic_unit': 'dictionary_entry', 'source_revision': 'a' * 64}
        hits = [] if self.empty else [hit]
        return {'query': query, 'sources': [source], 'count': len(hits), 'hits': hits}


def run_expansion(pairs, *, failed=None, empty=False):
    family, capture = profile_for(pairs)
    gateway = ReferenceGateway(failed=failed, empty=empty)
    definitions = [{'slug': slug, 'adapter_key': adapter, 'display_name': slug}
        for slug, adapter in [('reference', 'official_reference_jsonl'), ('other', 'other_document')]]
    with ThreadPoolExecutor(max_workers=2) as details, ThreadPoolExecutor(max_workers=4) as pool:
        found, coverage = [], []
        for definition in definitions:
            matches, status = unified.collect_source(gateway, family, definition, details)
            found.extend(matches); coverage.append(status)
        unified.expand_reference_sources(gateway, family, definitions, capture, found, coverage, details, pool)
    return family, capture, gateway, found, coverage


def test_existing_surface_hit_does_not_suppress_attested_lemma():
    family, capture, gateway, found, coverage = run_expansion([('ljudmi', 'človek')])
    assert gateway.calls == [('reference', 'ljudmi'), ('other', 'ljudmi'), ('reference', 'človek')]
    entry = next(row for row in found if row['external_id'] == 'record-človek')
    assert entry['capture']['dictionary_lookup'] == {'version': 'recorded-dictionary-lookup-v1',
        'target': 'ljudmi', 'query': 'človek', 'basis': 'recorded_morphology_lemma', 'source_entry_ids': ['entry-0']}
    assert entry['capture_sha256'] == digest(entry['capture'])
    assert family['question'] == capture['captures'][0]['response']['query']['text']
    proof = coverage[0]['lemma_expansion']['lookups'][0]
    assert proof['source_records'][0]['captured_unit_id'] == 'unit-0'
    assert coverage[0]['returned_count'] == 2 and coverage[0]['status'] == 'ok'
    assert 'lemma_expansion' not in coverage[1]


def test_direct_exact_lookup_binds_literal_target_and_preserves_original():
    family, _ = profile_for([('Luka', 'Luka')])
    found, _ = unified.collect_source(ReferenceGateway(), family,
        {'slug': 'reference', 'adapter_key': 'official_reference_jsonl', 'display_name': 'Reference'})
    hit = found[0]['capture']
    assert hit['dictionary_lookup'] == {'version': 'recorded-dictionary-lookup-v1', 'target': 'Luka',
        'query': 'Luka', 'basis': 'original_surface', 'source_entry_ids': []}
    assert hit['original_text'] == 'Complete entry: Luka' and hit['published_headword'] == 'Luka'


def test_no_claim_of_exact_dictionary_mapping_on_fulltext_hits():
    family, _ = profile_for([('Luka', 'Luka')])
    hit = {'published_headword': 'Lúka', 'semantic_unit': 'dictionary_entry', 'score_method': 'official_reference_fts5_bm25'}
    assert unified.dictionary_lookups(hit, 'Luka', family) == []
    hit['score_method'] = 'official_reference_exact_headword'; hit['semantic_unit'] = 'authored_section'
    assert unified.dictionary_lookups(hit, 'Luka', family) == []


def test_bound_and_shared_lemmas_are_recorded_honestly():
    _, _, gateway, _, coverage = run_expansion([(f'oblika{i}', f'lema{i}') for i in range(10)])
    proof = coverage[0]['lemma_expansion']
    assert proof['candidate_queries'] == 10 and proof['queried'] == 8 and proof['omitted_by_bound'] == 2
    assert len([q for source, q in gateway.calls if source == 'reference' and q.startswith('lema')]) == 8
    _, _, gateway, found, _ = run_expansion([('otroku', 'otrok'), ('otrokom', 'otrok')])
    assert gateway.calls.count(('reference', 'otrok')) == 1
    records = [row for row in found if row['external_id'] == 'record-otrok']
    assert {row['capture']['dictionary_lookup']['target'] for row in records} == {'otroku', 'otrokom'}
    assert len(unified.merge_source_matches(records + deepcopy(records))) == 2


def test_aggregate_request_bound_is_shared_fairly_across_reference_sources():
    family, capture = profile_for([(f'oblika{i}', f'lema{i}') for i in range(10)])
    definitions = [{'slug': f'reference{i}', 'adapter_key': 'official_reference_jsonl', 'display_name': 'Reference'} for i in range(4)]
    gateway = ReferenceGateway(); found, coverage = [], []
    with ThreadPoolExecutor(max_workers=2) as details, ThreadPoolExecutor(max_workers=4) as pool:
        for definition in definitions:
            rows, status = unified.collect_source(gateway, family, definition, details)
            found.extend(rows); coverage.append(status)
        unified.expand_reference_sources(gateway, family, definitions, capture, found, coverage, details, pool)
    assert len(gateway.calls) == 4 + unified.REFERENCE_LOOKUP_REQUEST_LIMIT
    assert all(row['lemma_expansion']['queried'] == 4 and row['lemma_expansion']['omitted_by_bound'] == 6 for row in coverage)


@pytest.mark.parametrize('empty,expected', [(False, 'partial'), (True, 'timeout')])
def test_failed_supplement_does_not_disguise_outage_or_discard_surface(empty, expected):
    _, _, _, found, coverage = run_expansion([('ljudmi', 'človek')], failed='človek', empty=empty)
    assert coverage[0]['status'] == expected
    assert coverage[0]['lemma_expansion']['lookups'][0]['status'] == 'timeout'
    assert bool(found) is (not empty)


def test_single_lexical_capture_and_initial_parallel_search_are_preserved(monkeypatch):
    from test_pilot_unified import UnifiedGateway
    family, capture = profile_for([('ljudmi', 'človek')]); calls = []
    def tools(gateway, received):
        calls.append(deepcopy(received))
        return capture, {'family': 'linghub_tools', 'status': 'not_documentary', 'returned_count': 0}
    monkeypatch.setattr(unified, 'collect_tools', tools)
    monkeypatch.setattr(unified, 'registry', lambda gateway: [
        {'slug': 'reference', 'adapter_key': 'official_reference_jsonl', 'display_name': 'Reference'}])
    class Gateway(UnifiedGateway):
        def request(self, path, payload, base=None):
            if path.startswith('/api/search?'):
                return reference.request(path, payload, base)
            return super().request(path, payload, base)
    gateway = Gateway(); reference = ReferenceGateway(); reference.klikai_url = gateway.klikai_url
    frozen = unified.freeze(gateway, family, {})
    assert len(calls) == 1
    assert ('reference', 'človek') in reference.calls
    assert frozen['bundle']['retrieval']['status'] == 'complete'
    assert all(path != '/internal/pilot/generate' for path, _, _ in gateway.calls)
