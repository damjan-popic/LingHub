"""Read-only real HTTP probes for LingHub Core with the local spaCy baseline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.contextual import validate_analysis


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--expect-missing-lemmatizer', action='store_true')
    args = parser.parse_args()
    report = {'status': 'RUNNING', 'at': datetime.now(timezone.utc).isoformat(),
        'base_url': f'http://127.0.0.1:{args.port}', 'checks': [], 'examples': {},
        'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'production_parity': 'PENDING', 'classla_adapter': 'NOT_STARTED'}

    def call(route, payload=None, expected=200, params=None):
        url = report['base_url'] + route + ('?' + urlencode(params) if params else '')
        request = Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json'})
        started = time.perf_counter()
        try:
            with urlopen(request, timeout=20) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:
            status, raw = error.code, error.read()
        elapsed = (time.perf_counter() - started) * 1000
        report['checks'].append({'route': route, 'payload': payload, 'params': params,
            'expected_status': expected, 'http_status': status, 'milliseconds': round(elapsed, 3),
            'response_sha256': hashlib.sha256(raw).hexdigest(), 'response_bytes': len(raw)})
        assert status == expected, (route, status, raw[:200])
        return json.loads(raw)

    try:
        resources = call('/v1/resources/health')
        assert resources['resources']['thesaurus']['fingerprint'] == 'de2195bde050500bce48de068f968395d5d68d75634942ef7f33dc692d007849'
        assert resources['resources']['collocations']['build_id'] == '25fd5882cde3b326208050bb6bfbd3901895a83ac6633b209e081fde0bf8f63c'
        assert resources['resources']['morphology']['release_fingerprint'] == 'fa2fe5c58891375b9953ed868812d3489192602aab97fd2c2a288f9fc0c8db0b'
        report['resource_identity'] = resources['resources']
        if args.expect_missing_lemmatizer:
            call('/health', expected=502)
            for route in ('/nlp/analyze', '/nlp/analyze-full', '/nlp/lemmas', '/nlp/loris-check', '/collocations/phrase', '/collocations/from-text'):
                result = call(route, {'lang': 'sl', 'text': 'šolskim otrokom'}, expected=502)
                assert result['detail'] == 'Local lemmatizer request failed or returned invalid data.'
            assert call('/thesaurus/synonyms', params={'lemma': 'otrok'})['senses']
            assert call('/v1/resources/collocations', params={'lemma': 'otrok'})['collocations']
        else:
            health = call('/health')
            assert health['contextual_nlp']['status'] == 'READY_LOCAL'
            for language, text, expected in [('sl', '  šolskim otrokom  ', ['šolski', 'otrok']),
                                               ('it', '  I bambini leggono libri.  ', ['bambino', 'leggere', 'libro'])]:
                payload = {'lang': language, 'text': text}
                tokens = call('/nlp/analyze', payload)
                validate_analysis(tokens, text, '/analyze')
                assert set(expected) <= {t['lemma'] for t in tokens}
                lemmas = call('/nlp/lemmas', payload)
                assert lemmas['lemmas'] == [t['lemma'] for t in tokens]
                full = call('/nlp/analyze-full', payload)
                validate_analysis(full, text, '/analyze-full')
                report['examples'][language] = full
            for text, rule, match_on in [('abonma', 'paronym_abonma', None),
                                          ('compact disc', 'odsvetovani_compact disc', None),
                                          ('gazdina', 'prepovedani_gazdina', 'surface_alias'),
                                          ('Trieste', 'toponym_Trieste', None),
                                          ('Trst', 'toponym_Trieste', None)]:
                original = '  ' + text + '  '
                data = call('/nlp/loris-check', {'lang': 'sl', 'text': original})
                validate_analysis(data, original, '/loris/check')
                span = next(s for s in data['spans'] if s['rule_id'] == rule)
                assert not match_on or span['match_on'] == match_on
                assert any(i['rule_id'] == rule for i in data['issues'])
                report['examples'][text] = data
            nohit = call('/nlp/loris-check', {'lang': 'sl', 'text': 'zqxv987654'})
            assert nohit['issues'] == [] and nohit['spans'] == []
            original = '  šolskim otrokom  '
            phrase = call('/collocations/phrase', {'lang': 'sl', 'text': original,
                'headword': 'otrok', 'status': 'manual', 'min_freq': None, 'limit': 500})
            validate_analysis(phrase['tokens'], original, '/analyze')
            assert phrase['phrase_lemma_sequence'] == ['šolski', 'otrok']
            hit = next(m for m in phrase['matches'] if m['lexical_unit_id'] == 12250092)
            assert hit['matched_by'] == ['lemma_sequence'] and hit['occurrence_id'] == 1
            assert [c['position'] for c in hit['components']] == [1, 2]
            assert hit['sense_id'] == '16268462' and hit['provenance']['handle'] == '11356/2090'
            report['examples']['phrase_lemma_sequence_match'] = hit
            text_result = call('/collocations/from-text', {'lang': 'sl', 'text': original, 'max_lemmas': 2})
            assert text_result['lemmas_used'] == ['šolski', 'otrok']
            assert all(x['collocations'] for x in text_result['collocations'].values())
            text_nohit = call('/collocations/from-text', {'lang': 'sl', 'text': 'zqxv987654'})
            assert all(x['count_total'] == 0 for x in text_nohit['collocations'].values())
            phrase_nohit = call('/collocations/phrase', {'lang': 'sl', 'text': 'zqxv987654'})
            assert not phrase_nohit['matches']
            call('/collocations/phrase', {'lang': 'it', 'text': 'bambini'}, expected=422)
            call('/nlp/analyze', {'lang': 'sl', 'text': ' '}, expected=400)
            call('/nlp/analyze', {'lang': 'de', 'text': 'a'}, expected=422)
            call('/nlp/analyze', {'lang': 'sl', 'text': 'a' * 10001}, expected=422)
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        # Avoid bloating the report with the deliberate length-bound fixture.
        for check in report['checks']:
            if check['payload'] and len(check['payload']['text']) > 10000:
                check['payload'] = {'lang': check['payload']['lang'], 'text_length': len(check['payload']['text'])}
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        args.report.chmod(0o640)
        print(json.dumps({'status': report['status'], 'checks': len(report['checks']), 'report': str(args.report)}))


if __name__ == '__main__':
    main()
