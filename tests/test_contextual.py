"""Activation regressions: coordinates, bounded composition and dependency loss."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import candidate_app
from core import contextual as c


def tokens():
    return [{'text': 'šolskim', 'lemma': 'šolski', 'pos': 'ADJ', 'start': 2, 'end': 9},
            {'text': 'otrokom', 'lemma': 'otrok', 'pos': 'NOUN', 'start': 10, 'end': 17}]


class ContextualTests(unittest.TestCase):
    def setUp(self):
        previous = dict(candidate_app.app.state._state)
        self.addCleanup(lambda: (candidate_app.app.state._state.clear(), candidate_app.app.state._state.update(previous)))
        candidate_app.app.state.resource_files = {}
        candidate_app.app.state.lemmatizer_url = 'http://127.0.0.1:18001'
        self.record = {'occurrence_id': 1, 'lexical_unit_id': 12250092, 'sense_id': '16268462',
            'status': 'manual', 'frequency': None, 'surface': 'šolski otroci',
            'provenance': {'kind': 'official_release_acquisition'},
            'components': [{'text': 'šolski', 'lemma': 'šolski', 'position': 1},
                           {'text': 'otroci', 'lemma': 'otrok', 'position': 2}]}
        self.reader = Mock(return_value={'lemma': 'otrok', 'count_total': 2, 'count_returned': 2,
            'collocations': [self.record, {**self.record, 'occurrence_id': 2}], 'resource': {'build_id': 'test'}})
        candidate_app.app.state.collocations = SimpleNamespace(get_collocations_for_lemma=self.reader)
        self.client = TestClient(candidate_app.app)
        self.addCleanup(self.client.close)

    def test_loopback_origin_required(self):
        for invalid in ('http://example.org:80', 'http://127.0.0.1', 'https://127.0.0.1:1',
                        'http://u:p@127.0.0.1:1', 'http://127.0.0.1:1/path'):
            with self.subTest(invalid=invalid), patch.dict(os.environ, MY_LEMMATIZER_URL=invalid):
                self.assertRaises((RuntimeError, ValueError), c.configured_lemmatizer_url)
        with patch.dict(os.environ, MY_LEMMATIZER_URL='http://127.0.0.1:18001/'):
            self.assertEqual(c.configured_lemmatizer_url(), 'http://127.0.0.1:18001')

    def test_nlp_preserves_original_text_and_token_offsets(self):
        with patch.object(c, 'upstream', AsyncMock(return_value=tokens())) as proxy:
            result = self.client.post('/nlp/analyze', json={'lang': 'sl', 'text': '  šolskim otrokom '})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), tokens())
        self.assertEqual(proxy.call_args.args[2]['text'], '  šolskim otrokom ')

    def test_lemma_contract_both_shapes(self):
        with patch.object(c, 'upstream', AsyncMock(return_value=tokens())):
            data = self.client.post('/nlp/lemmas', json={'lang': 'sl', 'text': '  šolskim otrokom '}).json()
        self.assertEqual(data['lemmas'], ['šolski', 'otrok'])
        self.assertEqual(data['tokens'][1], {'text': 'otrokom', 'lemma': 'otrok'})

    def test_invalid_coordinates_fail_closed(self):
        for altered in ({**tokens()[0], 'start': 0}, {**tokens()[0], 'end': 999}, {**tokens()[0], 'lemma': 1}):
            with patch.object(c, 'upstream', AsyncMock(return_value=[altered])):
                self.assertEqual(self.client.post('/nlp/analyze', json={'lang': 'sl', 'text': '  šolskim otrokom '}).status_code, 502)

    def test_full_spans_and_issue_coordinates_are_validated(self):
        span = {'start': 2, 'end': 17, 'token_start': 0, 'token_end': 2,
                'token_indexes': [0, 1], 'surface': 'šolskim otrokom'}
        data = {'tokens': tokens(), 'spans': [span], 'issues': [{'start': 2, 'end': 17}]}
        self.assertEqual(c.validate_analysis(data, '  šolskim otrokom ', '/loris/check'), data)
        for changed in ({**span, 'surface': 'šolski otroci'}, {**span, 'token_indexes': [1, 0]}, {**span, 'token_end': 3}):
            self.assertRaises(ValueError, c.validate_analysis, {**data, 'spans': [changed]}, '  šolskim otrokom ', '/analyze-full')

    def test_phrase_lemma_match_preserves_distinct_source_occurrences_and_provenance(self):
        with patch.object(c, 'upstream', AsyncMock(return_value=tokens())):
            result = self.client.post('/collocations/phrase', json={'lang': 'sl', 'text': '  šolskim otrokom ', 'headword': 'otrok', 'min_freq': None})
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertEqual(data['input_text'], '  šolskim otrokom ')
        self.assertEqual(data['phrase_lemma_sequence'], ['šolski', 'otrok'])
        self.assertEqual([m['occurrence_id'] for m in data['matches']], [1, 2])
        self.assertTrue(all(m['matched_by'] == ['lemma_sequence'] and m['provenance'] == self.record['provenance'] for m in data['matches']))
        self.assertIsNone(self.reader.call_args.kwargs['min_freq'])

    def test_reversed_component_sequence_does_not_match(self):
        self.record['components'].reverse()
        with patch.object(c, 'upstream', AsyncMock(return_value=tokens())):
            data = self.client.post('/collocations/phrase', json={'lang': 'sl', 'text': '  šolskim otrokom ', 'headword': 'otrok'}).json()
        self.assertEqual(data['matches'], [])

    def test_from_text_caps_and_reports_lemma_truncation(self):
        with patch.object(c, 'upstream', AsyncMock(return_value=tokens())):
            data = self.client.post('/collocations/from-text', json={'lang': 'sl', 'text': '  šolskim otrokom ', 'max_lemmas': 1}).json()
        self.assertEqual(data['lemmas_used'], ['šolski'])
        self.assertTrue(data['lemmas_truncated'])
        self.reader.assert_called_once()

    def test_invalid_input_rejected_before_remote_or_resource_call(self):
        with patch.object(c, 'upstream', AsyncMock()) as remote:
            for route, value in [('/nlp/analyze', {'lang': 'en', 'text': 'a'}),
                                  ('/nlp/analyze', {'lang': 'sl', 'text': 'a' * 10001}),
                                  ('/collocations/phrase', {'lang': 'it', 'text': 'bambini'}),
                                  ('/collocations/from-text', {'lang': 'sl', 'text': 'a', 'max_lemmas': 21}),
                                  ('/collocations/phrase', {'lang': 'sl', 'text': 'a', 'min_logdice': 'NaN'})]:
                self.assertEqual(self.client.post(route, json=value).status_code, 422)
            remote.assert_not_called()
        self.reader.assert_not_called()

    def test_product_budget_rejects_before_dictionary_reads(self):
        with patch.object(c, 'MAX_RETURNED', 500), patch.object(c, 'upstream', AsyncMock(return_value=tokens())):
            result = self.client.post('/collocations/from-text', json={'lang': 'sl', 'text': '  šolskim otrokom ', 'limit_per_lemma': 500})
        self.assertEqual(result.status_code, 400)
        self.reader.assert_not_called()

    def test_missing_dependency_has_bounded_sanitized_error(self):
        candidate_app.app.state.lemmatizer_url = None
        result = self.client.post('/nlp/analyze', json={'lang': 'sl', 'text': 'a'})
        self.assertEqual(result.status_code, 503)
        with patch.object(c, 'upstream', AsyncMock(side_effect=HTTPException(502, 'Local lemmatizer request failed or returned invalid data.'))):
            result = self.client.post('/collocations/phrase', json={'lang': 'sl', 'text': 'a'})
        self.assertEqual(result.status_code, 502)
        self.reader.assert_not_called()


if __name__ == '__main__':
    unittest.main()
