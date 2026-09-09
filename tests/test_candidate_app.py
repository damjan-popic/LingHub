"""Candidate ASGI contracts with synthetic resources; no sockets or models."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

import candidate_app as candidate


@dataclass(frozen=True)
class SyntheticLemma:
    language: str
    lemma: str
    lemma_normalized: str
    msds: tuple[str, ...] = ()
    pos_families: tuple[str, ...] = ()
    matched_by: str = 'exact'


class CandidateAPITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='candidate-api-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.resource_file = self.root / 'synthetic.sqlite'
        self.resource_file.write_bytes(b'synthetic resource identity')
        old_state = dict(candidate.app.state._state)

        def restore_state():
            candidate.app.state._state.clear()
            candidate.app.state._state.update(old_state)

        self.addCleanup(restore_state)
        self.synonyms = SimpleNamespace(
            get_senses_for_lemma=Mock(return_value=[{'sense_id': 'source-10', 'core': ['b', 'a', 'b'],
                'near': ['z', 'y', 'z'], 'antonyms': ['opposite'], 'source_extra': 'preserved only in evidence'}]),
            lookup_evidence=Mock(return_value={'entries': [{'lexical_unit_id': 'source-001',
                'senses': [{'sense_id': 'source-10'}]}], 'fingerprint': 'synthetic-fingerprint'}))
        self.collocations = SimpleNamespace(get_collocations_for_lemma=Mock(return_value={
            'lemma': 'otrok', 'count_total': 2, 'count_returned': 1, 'collocations': [
                {'lexical_unit_id': 12250092, 'sense_id': '16268462', 'frequency': None,
                 'logDice': None, 'components': [{'num': 1, 'lemma': 'šolski', 'text': 'šolski'}]}]}))
        self.morphology = SimpleNamespace(lemmatize_token=Mock(return_value=(SyntheticLemma('sl', 'Otrok', 'otrok'),)))
        candidate.app.state.synonyms = self.synonyms
        candidate.app.state.collocations = self.collocations
        candidate.app.state.morphology = self.morphology
        candidate.app.state.resource_readiness = {'thesaurus': {'status': 'READY', 'build_id': 'synthetic-build'},
                                                   'collocations': {'usable': True}, 'morphology': {'available': True}}
        candidate.app.state.resource_files = {self.resource_file: candidate.file_identity(self.resource_file)}
        # Without entering TestClient's lifespan, only the actual ASGI routing and
        # dependencies run; configuration, real index constructors and models do not.
        self.client = TestClient(candidate.app)
        self.addCleanup(self.client.close)

    def test_legacy_thesaurus_shape_sorting_and_error_contract(self):
        response = self.client.get('/thesaurus/synonyms', params={'lemma': '  Otrok  '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'lemma': 'Otrok', 'senses': [
            {'sense_id': 'source-10', 'core': ['a', 'b'], 'near': ['y', 'z'], 'antonyms': ['opposite']}]})
        self.synonyms.get_senses_for_lemma.assert_called_once_with('Otrok')
        self.assertEqual(self.client.get('/thesaurus/synonyms').status_code, 422)
        self.assertEqual(self.client.get('/thesaurus/synonyms', params={'lemma': '  '}).status_code, 400)
        self.synonyms.get_senses_for_lemma.return_value = []
        self.assertEqual(self.client.get('/thesaurus/synonyms', params={'lemma': 'absent'}).status_code, 404)

    def test_collocation_aliases_preserve_filters_ids_and_nullable_evidence(self):
        parameters = {'lemma': ' otrok ', 'min_freq': 2, 'min_logdice': 0.5, 'structure_id': 34,
                      'status': 'manual', 'limit': 7, 'sort': 'logdice', 'order': 'asc'}
        first = self.client.get('/collocations', params=parameters)
        second = self.client.get('/collocations/by-lemma', params=parameters)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.collocations.get_collocations_for_lemma.assert_called_with('otrok', min_freq=2,
            min_logdice=0.5, structure_id=34, status='manual', limit=7, sort='logdice', order='asc')
        record = first.json()['collocations'][0]
        self.assertEqual(record['lexical_unit_id'], 12250092)
        self.assertEqual(record['sense_id'], '16268462')
        self.assertIsNone(record['frequency'])
        self.assertIsNone(record['logDice'])
        self.collocations.get_collocations_for_lemma.return_value = {'count_total': 0}
        self.assertEqual(self.client.get('/collocations', params={'lemma': 'absent'}).status_code, 404)

    def test_nonfinite_measures_and_unsupported_zero_threshold_are_rejected(self):
        for route in ('/collocations', '/collocations/by-lemma', '/v1/resources/collocations'):
            for invalid in ('NaN', 'Infinity', '-Infinity'):
                with self.subTest(route=route, invalid=invalid):
                    self.assertEqual(self.client.get(route, params={'lemma': 'otrok', 'min_logdice': invalid}).status_code, 422)
        self.assertEqual(self.client.get('/v1/resources/collocations', params={'lemma': 'otrok', 'min_freq': 0}).status_code, 422)
        self.collocations.get_collocations_for_lemma.assert_not_called()

    def test_versioned_routes_retain_evidence_and_default_to_nullable_frequency(self):
        response = self.client.get('/v1/resources/thesaurus', params={'lemma': ' Otrok '})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['entries'][0]['lexical_unit_id'], 'source-001')
        self.synonyms.lookup_evidence.assert_called_once_with('Otrok')
        response = self.client.get('/v1/resources/collocations', params={'lemma': 'otrok'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.collocations.get_collocations_for_lemma.call_args.kwargs['min_freq'])

    def test_inflected_slovene_join_is_explicitly_normalized_and_italian_stays_separate(self):
        response = self.client.post('/v1/resources/lookup-inflected', json={'token': ' otroci ', 'language': 'sl', 'limit_per_lemma': 3})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result['contextual_analysis'])
        self.assertEqual(result['analysis_kind'], 'lexical_candidates')
        self.assertEqual(result['candidates'][0]['morphology']['lemma'], 'Otrok')
        self.morphology.lemmatize_token.assert_called_once_with('sl', 'otroci')
        self.synonyms.lookup_evidence.assert_called_once_with('otrok', normalized=True)
        self.collocations.get_collocations_for_lemma.assert_called_once_with('otrok', min_freq=None, limit=3)
        self.synonyms.lookup_evidence.reset_mock()
        self.collocations.get_collocations_for_lemma.reset_mock()
        self.morphology.lemmatize_token.return_value = (SyntheticLemma('it', 'bambino', 'bambino'),)
        response = self.client.post('/v1/resources/lookup-inflected', json={'token': 'bambini', 'language': 'it'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('dictionary_resources', response.json()['candidates'][0])
        self.synonyms.lookup_evidence.assert_not_called()
        self.collocations.get_collocations_for_lemma.assert_not_called()

    def test_invalid_and_absent_inflections_do_not_fabricate_candidates(self):
        for payload, expected in [({'token': '  '}, 400), ({'token': 'dve besedi'}, 400),
                                  ({'token': 'x', 'language': 'de'}, 422), ({'token': 'x' * 129}, 422),
                                  ({'token': 'x', 'limit_per_lemma': 21}, 422)]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/v1/resources/lookup-inflected', json=payload).status_code, expected)
        self.morphology.lemmatize_token.assert_not_called()
        self.morphology.lemmatize_token.return_value = ()
        response = self.client.post('/v1/resources/lookup-inflected', json={'token': 'qzxqzx'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['candidates'], [])
        self.synonyms.lookup_evidence.assert_not_called()
        self.collocations.get_collocations_for_lemma.assert_not_called()

    def test_changed_or_missing_files_fail_every_resource_route_before_adapter_access(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['operational_activation'])
        self.assertEqual(response.json()['classla_adapter'], 'NOT_CONFIGURED')
        routes = ['/health', '/v1/resources/health', '/thesaurus/synonyms', '/v1/resources/thesaurus',
                  '/collocations', '/collocations/by-lemma', '/v1/resources/collocations']
        for mutation in ('changed', 'missing'):
            if mutation == 'changed':
                self.resource_file.write_bytes(b'different immutable resource content')
            else:
                self.resource_file.unlink()
            for route in routes:
                with self.subTest(route=route, mutation=mutation):
                    self.assertEqual(self.client.get(route, params={'lemma': 'otrok'}).status_code, 503)
            self.assertEqual(self.client.post('/v1/resources/lookup-inflected', json={'token': 'otroci'}).status_code, 503)
        self.synonyms.get_senses_for_lemma.assert_not_called()
        self.synonyms.lookup_evidence.assert_not_called()
        self.collocations.get_collocations_for_lemma.assert_not_called()
        self.morphology.lemmatize_token.assert_not_called()

    def test_same_real_adapter_instances_can_be_used_by_multiple_worker_threads(self):
        import test_synonym_index as synonyms_fixture
        import test_cjvt_collocations as collocations_fixture
        synonym_case = synonyms_fixture.SynonymIndexTests()
        synonym_case.setUp()
        self.addCleanup(synonym_case.doCleanups)
        _, _, synonyms = synonym_case.compile()
        collocation_case = collocations_fixture.CollocationsTests()
        collocation_case.setUp()
        self.addCleanup(collocation_case.tearDown)
        _, collocations = collocation_case.compile()

        def lookup(_):
            return (synonyms.lookup_evidence('Čas')['count_relations'],
                    collocations.get_collocations_for_lemma('otrok', min_freq=None)['count_returned'])

        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lookup, range(24)))
        self.assertEqual(results, [(6, 2)] * 24)
