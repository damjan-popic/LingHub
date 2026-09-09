"""Read-only real HTTP regression probes for the opt-in CJVT candidate."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--pid', type=int, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--expect-local-persistent', action='store_true')
    args = p.parse_args()
    base = f'http://127.0.0.1:{args.port}'
    proc = Path('/proc') / str(args.pid)
    command = proc.joinpath('cmdline').read_bytes().decode().split('\0')[:-1]
    assert 'candidate_app:app' in command and '127.0.0.1' in command and str(args.port) in command
    report = {'status': 'RUNNING', 'at': datetime.now(timezone.utc).isoformat(),
              'base_url': base, 'pid': args.pid, 'command': command,
              'working_directory': str(proc.joinpath('cwd').resolve()),
              'candidate_app_sha256': hashlib.sha256(Path('candidate_app.py').read_bytes()).hexdigest(),
              'probe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'checks': [], 'operational_activation': False, 'classla_adapter': None}

    def request(path, query=None, payload=None, expected=200, record=True):
        route = path + ('?' + urlencode(query) if query else '')
        req = Request(base + route, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
        t = time.perf_counter()
        try:
            with urlopen(req, timeout=20) as response:
                status, raw = response.status, response.read()
        except HTTPError as error:
            status, raw = error.code, error.read()
        assert status == expected, (route, status, raw[:200])
        data = json.loads(raw)
        ms = (time.perf_counter() - t) * 1000
        if record:
            report['checks'].append({'request': route, 'payload': payload, 'http_status': status,
                                     'response_sha256': hashlib.sha256(raw).hexdigest(),
                                     'response_bytes': len(raw), 'milliseconds': round(ms, 3)})
        return data, ms

    def get(path, **query):
        return request(path, query)[0]

    def relations(data):
        return [r for entry in data['entries'] for sense in entry['senses'] for r in sense['relations']]

    try:
        health = get('/health')
        assert health['status'] == 'ok' and health['operational_activation'] == args.expect_local_persistent
        assert health['resources']['thesaurus']['available'] and health['resources']['collocations']['usable']
        assert health['resources']['morphology']['release_fingerprint'] == 'fa2fe5c58891375b9953ed868812d3489192602aab97fd2c2a288f9fc0c8db0b'
        report['resource_readiness'] = health
        report['classla_adapter'] = health['classla_adapter']
        report['operational_activation'] = health['operational_activation']
        syn = get('/v1/resources/thesaurus', lemma='otrok')
        assert syn['count_senses'] == 5 and {r['synonym_type'] for r in relations(syn)} >= {'core', 'near', None}
        assert len({s['sense_id'] for e in syn['entries'] for s in e['senses']}) == 5
        ambiguous = get('/v1/resources/thesaurus', lemma='na')
        assert {u['lexical_unit_id'] for e in ambiguous['entries'] for u in e['lexical_units']} == {'216', '217'}
        assert any(r['relation_type'] == 'antonym' for r in relations(get('/v1/resources/thesaurus', lemma='abolirati')))
        assert any(r['synonym_type'] == 'near' for r in relations(get('/v1/resources/thesaurus', lemma='Shakespeare')))
        assert relations(get('/v1/resources/thesaurus', lemma='Jemen'))[0]['synonym_type'] is None
        assert get('/v1/resources/thesaurus', lemma='qzxqzx')['count_entries'] == 0
        legacy = get('/thesaurus/synonyms', lemma='otrok')
        assert len(legacy['senses']) == 5 and all(set(s) == {'sense_id', 'core', 'near', 'antonyms'} for s in legacy['senses'])
        request('/thesaurus/synonyms', {'lemma': 'qzxqzx'}, expected=404)
        request('/thesaurus/synonyms', {'lemma': ' '}, expected=400)

        manual = get('/v1/resources/collocations', lemma='otrok', status='manual', limit=500)
        null_record = next(r for r in manual['collocations'] if r['lexical_unit_id'] == 12250092)
        assert null_record['frequency'] is None and null_record['logDice'] is None
        assert null_record['collocation_id'] is None and null_record['sense_id'] == '16268462'
        assert [c['text'] for c in null_record['components']] == ['šolski', 'otroci']
        assert [c['position'] for c in null_record['components']] == [1, 2]
        numeric = next(r for r in manual['collocations'] if r['lexical_unit_id'] == 9561883)
        assert numeric['frequency'] == 6839 and numeric['logDice'] == 12.10279
        auto = get('/v1/resources/collocations', lemma='otrok', status='automatic', limit=20)
        assert auto['collocations'] and all(r['status'] == 'automatic' for r in auto['collocations'])
        assert all(r['provenance']['archive_sha256'] == 'd7010533ae5cb1a80377af592a9592b6137c4128198faf8974cd9ee0b3c9d164' for r in auto['collocations'])
        filtered = get('/collocations/by-lemma', lemma='otrok', min_freq=100, min_logdice=10, structure_id=34)
        assert filtered['collocations'] and all(r['frequency'] >= 100 and r['logDice'] >= 10 and r['structure_id'] == 34 for r in filtered['collocations'])
        assert all(r['frequency'] is not None for r in get('/collocations', lemma='otrok', limit=500)['collocations'])
        assert get('/v1/resources/collocations', lemma='qzxqzx')['count_total'] == 0
        request('/collocations', {'lemma': 'qzxqzx'}, expected=404)
        for params in ({'limit': 501}, {'min_freq': 0}, {'min_logdice': 'NaN'}, {'min_logdice': 'Infinity'}):
            request('/v1/resources/collocations', {'lemma': 'otrok', **params}, expected=422)

        report['inflected_evidence'] = []
        for token, lemma, headword in [('otrokom', 'otrok', 'otrok'), ('Jemnu', 'jemen', 'Jemen')]:
            data, _ = request('/v1/resources/lookup-inflected', payload={'token': token})
            assert data['contextual_analysis'] is False and len(data['candidates']) <= 4
            hit = next(c for c in data['candidates'] if c['morphology']['lemma_normalized'] == lemma)
            assert any(e['headword'] == headword for e in hit['thesaurus']['entries'])
            assert hit['collocations']['count_total'] > 0 and hit['thesaurus']['lookup_mode'] == 'normalized'
            report['inflected_evidence'].append({'token': token, 'morphology': hit['morphology'],
                'thesaurus_headwords': [e['headword'] for e in hit['thesaurus']['entries']],
                'collocations_count': hit['collocations']['count_total']})
        italian, _ = request('/v1/resources/lookup-inflected', payload={'token': 'bambini', 'language': 'it'})
        assert italian['candidates'] and all('dictionary_resources' in c and 'thesaurus' not in c for c in italian['candidates'])
        nohit, _ = request('/v1/resources/lookup-inflected', payload={'token': 'qzxqzx'})
        assert nohit['candidates'] == []
        for token, status in [('', 422), ('due parole', 400), ('x' * 129, 422)]:
            request('/v1/resources/lookup-inflected', payload={'token': token}, expected=status)
        request('/v1/resources/lookup-inflected', payload={'token': 'otrok', 'language': 'en'}, expected=422)
        report['source_derived_examples'] = {'manual_missing_frequency': null_record,
                                           'manual_numeric_frequency': numeric,
                                           'automatic': auto['collocations'][0]}
        timings = {}
        probes = [('/thesaurus/synonyms', {'lemma': 'otrok'}, None),
                  ('/v1/resources/collocations', {'lemma': 'otrok', 'limit': 20}, None),
                  ('/v1/resources/lookup-inflected', None, {'token': 'otrokom'})]
        for path, query, payload in probes:
            for _ in range(5):
                request(path, query, payload, record=False)
            samples = [request(path, query, payload, record=False)[1] for _ in range(50)]
            timings[path] = {'samples': 50, 'warmups': 5, 'p50_ms': round(statistics.median(samples), 3),
                             'p95_ms': round(sorted(samples)[math.ceil(.95 * len(samples))-1], 3),
                             'max_ms': round(max(samples), 3)}
        report['warm_http_latency'] = {'protocol': 'Sequential urllib loopback requests, response read and JSON decoded; one worker, warmed OS cache, no contextual models. Not the KLIKai acceptance benchmark.', 'routes': timings}
        report['process_memory'] = [line for line in proc.joinpath('status').read_text().splitlines() if line.startswith(('VmRSS:', 'VmHWM:'))]
        report['status'] = 'PASS'
    except Exception as error:
        report.update(status='FAIL', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        args.report.chmod(0o640)
        print(json.dumps({'status': report['status'], 'checks': len(report['checks']), 'report': str(args.report)}))


if __name__ == '__main__':
    main()
