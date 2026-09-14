"""Two opt-in accepted searches; all pilot state is disposable, no detail calls.

KLIKAI_RUN_LIVE_PROSE_ACCEPTANCE=1 enables this test. Set
KLIKAI_PROSE_ACCEPTANCE_RECEIPT to a NEW JSON file in the existing evidence area.
Full Q&A acceptance remains blocked by the accepted API's excerpt-only contract.
"""
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import httpx
import pytest

from pilot_backend import editorial
from pilot_backend.adapter import Gateway
from pilot_backend.api import create_app
from pilot_backend.store import digest
from test_pilot_backend_terminology_live import (
    ROOT, KLIKAI, RELEASE, EVIDENCE, APP_FILES, accepted_identity, revision, sha)

FIXTURE = ROOT / 'tests/fixtures/luka_prose_acceptance.json'
FIXTURES = json.loads(FIXTURE.read_text())
CASES = FIXTURES['cases']
ORIGIN = 'http://127.0.0.1:18310'
SCOPE = ('Live accepted KLIKai prose search + isolated pilot API/answer-block/drawer contract; '
         'no deployed-pilot writes, model generation or browser acceptance.')


def oracles():
    assert len(CASES) == 2 and {c['source_slug'] for c in CASES} == set(editorial.SOURCES)
    assert FIXTURES['retained_release_id'] == RELEASE.name
    assert sha(RELEASE / 'release-manifest.json') == FIXTURES['release_manifest_sha256']
    for case in CASES:
        path = RELEASE / case['source_file']
        assert sha(path) == case['source_file_sha256']
        record = json.loads(path.read_text().splitlines()[case['source_line'] - 1])
        assert record == case['retained_source_record']
        assert digest(record) == case['record_sha256']
        assert digest(record['payload']) == record['content_hash'] == case['payload_content_hash']
        assert record['external_id'] == case['expected']['resource_id']
        assert record['source_slug'] == case['source_slug']
        payload_text = ' '.join(json.dumps(record['payload'], ensure_ascii=False).split())
        assert case['query'].casefold() in payload_text.casefold()
        assert editorial.SOURCES[case['source_slug']] == case['source_metadata']['display_name']


def prose_identity():
    value = accepted_identity()
    root = Path(value['release'])
    manifest = json.loads((root / 'code-manifest.json').read_text())
    paths = ['apps/api/klikai_api/schemas/search.py', 'apps/api/klikai_api/retrieval/gradivo.py',
             *(case['source_yaml_path'] for case in CASES)]
    for path in paths:
        value['verified_files'][path] = sha(root / path)
        assert value['verified_files'][path] == manifest['files'][path]
    for case in CASES:
        assert value['verified_files'][case['source_yaml_path']] == case['source_yaml_sha256']
    return value


@pytest.fixture(scope='module')
def prose_run(request):
    report = {'schema': 'klikai.pilot.editorial-acceptance.v1', 'status': 'INCOMPLETE', 'scope': SCOPE,
              'search_excerpt_acceptance': 'INCOMPLETE', 'complete_qa_acceptance': 'BLOCKED',
              'remaining_gap': 'Accepted API has only bounded snippets, no full Q&A/detail reader; Pošta original question is absent from its prose projection.',
              'request_budget': {'primary_searches': 2, 'detail_requests': 0, 'automatic_retries': 0},
              'attempted_sources': [], 'cases': [], 'model_generation_calls': 0,
              'runtime_database_edition': 'Live database edition unverified by the current lookup contract.',
              'retained_release_id': RELEASE.name, 'browser_exercised': False,
              'deployed_pilot_writes': 0}
    output = os.environ.get('KLIKAI_PROSE_ACCEPTANCE_RECEIPT')
    target = Path(output) if output else None
    if target:
        assert target.parent == EVIDENCE and target.suffix == '.json' and not target.exists()
        with target.open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(report, stream)
    ready = False
    try:
        assert not os.environ.get('PYTEST_XDIST_WORKER') and not getattr(request.config.option, 'reruns', 0)
        oracles()  # Both fixed expectations are checked before either live request.
        report['independent_expectations_before_http'] = True
        report['accepted_service'] = prose_identity()
        report['tested_revisions'] = {'linghub': revision(ROOT, '29892600eb4c'), 'klikai': revision(KLIKAI, '4b0a112ef302')}
        paths = [*APP_FILES, 'pilot_backend/editorial.py', 'pilot_ui/core.mjs', 'pilot_ui/index.html',
                 'pilot_ui/i18n/sl.json', 'pilot_ui/i18n/it.json', 'tests/test_pilot_backend_editorial.py',
                 'tests/test_pilot_backend_editorial_live.py', 'tests/fixtures/luka_prose_acceptance.json']
        report['selected_files_sha256'] = {path: sha(ROOT / path) for path in paths}
        report['preserved_receipts'] = {str(EVIDENCE / name): sha(EVIDENCE / name) for name in (
            'solska-live-lookup-acceptance-20260913.json', 'luka-five-source-live-acceptance-20260914.json')}
        ready = True
        yield report
    except BaseException as error:
        report['status'] = 'BLOCKED' if isinstance(error, (PermissionError, FileNotFoundError)) or str(error).startswith('BLOCKED:') else 'FAIL'
        report['setup_error'] = type(error).__name__
        raise
    finally:
        report['actual_primary_searches'] = sum(c['primary_searches'] for c in report['cases'])
        try:
            if ready:
                assert prose_identity() == report['accepted_service']
                assert all(sha(ROOT / path) == pin for path, pin in report['selected_files_sha256'].items())
                assert all(sha(path) == pin for path, pin in report['preserved_receipts'].items())
                report['accepted_service_identity_unchanged'] = True
                states = [c['status'] for c in report['cases']]
                if len(states) == 2 and states == ['PASS', 'PASS'] and report['actual_primary_searches'] == 2:
                    report.update(status='PARTIAL', search_excerpt_acceptance='PASS')
                else:
                    report['search_excerpt_acceptance'] = report['status'] = 'FAIL' if 'FAIL' in states else 'BLOCKED'
        except BaseException as error:
            barrier = isinstance(error, (PermissionError, FileNotFoundError)) or str(error).startswith('BLOCKED:')
            report.update(status='BLOCKED' if barrier else 'FAIL', final_identity_error=type(error).__name__)
            raise
        finally:
            if target:
                with target.open('w') as stream:
                    json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
                    stream.write('\n')


class OneSearchGateway(Gateway):
    def __init__(self, endpoint, case, report):
        super().__init__(endpoint, secrets.token_urlsafe(40), klikai_url=endpoint)
        self.case, self.report = case, report
        self.calls, self.blocked, self.received = 0, False, None
        self.forbidden = []

    def deny(self, operation):
        self.forbidden.append(operation)
        raise AssertionError('Forbidden upstream/model operation: ' + operation)

    def request(self, path, payload, base=None):
        if self.blocked or self.calls or base != self.klikai_url or payload is not None:
            return self.deny('request')
        parsed = urlsplit(path)
        assert parsed.path == '/api/search'
        assert parse_qs(parsed.query) == {'q': [self.case['query']], 'sources': [self.case['source_slug']], 'limit': ['3']}
        attempted = self.report['attempted_sources']
        if len(attempted) >= 2 or self.case['source_slug'] in attempted:
            return self.deny('search budget')
        attempted.append(self.case['source_slug'])
        self.calls += 1
        self.received = super().request(path, payload, base)
        self.blocked = True
        return self.received

    def capabilities(self):return self.deny('capabilities')
    def tool_capabilities(self):return self.deny('tool capabilities')
    def require_generation(self, models):return self.deny('generation admission')


@pytest.mark.skipif(os.environ.get('KLIKAI_RUN_LIVE_PROSE_ACCEPTANCE') != '1', reason='Opt-in real accepted prose API only')
@pytest.mark.parametrize('case', CASES, ids=[c['source_slug'] for c in CASES])
def test_real_editorial_excerpt_owned_frozen(case, prose_run, tmp_path, monkeypatch):
    result = {'source_slug': case['source_slug'], 'query': case['query'], 'expected': case['expected'],
              'retained_record_sha256': case['record_sha256'], 'status': 'FAIL', 'checks': {}}
    gateway = OneSearchGateway(prose_run['accepted_service']['endpoint'], case, prose_run)
    network = []
    real_transport = httpx.HTTPTransport.handle_request
    stage, started = 'disposable_setup', time.monotonic()

    def transport(instance, request):
        if network or request.method != 'GET' or str(request.url).split('?', 1)[0] != gateway.klikai_url + '/api/search':
            return gateway.deny('HTTP transport')
        assert not any(key in request.headers for key in ('authorization', 'cookie', 'x-pilot-internal-token'))
        observed = {'method': request.method, 'url': str(request.url), 'timeout': request.extensions.get('timeout')}
        network.append(observed)
        try:
            response = real_transport(instance, request)
            observed['status_code'] = response.status_code
            stream = response.stream

            class ObservedStream(httpx.SyncByteStream):
                def __iter__(self):
                    try:yield from stream
                    except httpx.HTTPError as error:
                        observed['error_type'] = type(error).__name__
                        raise
                def close(self):stream.close()

            response.stream = ObservedStream()
            return response
        except httpx.HTTPError as error:
            observed['error_type'] = type(error).__name__
            raise

    try:
        monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', transport)
        app = create_app({'store': str(tmp_path / 'private/pilot.sqlite'), 'allowed_origins': [ORIGIN],
                          'secure_cookies': False, 'tasks': list(editorial.SOURCES)}, adapter=gateway,
                         hasher=PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1), start_workers=False)
        password = secrets.token_urlsafe(32)
        for name in ('editorial-owner', 'editorial-other'):
            app.state.auth.create(name, password, origin='synthetic', must_change=False)

        def login(client, name):
            response = client.post('/api/pilot/login', json={'username': name, 'password': password}, headers={'Origin': ORIGIN})
            assert response.status_code == 200
            return {'Origin': ORIGIN, 'X-CSRF-Token': response.json()['csrf_token']}

        with TestClient(app, base_url=ORIGIN) as client:
            headers = login(client, 'editorial-owner')
            stage = 'normal_pilot_search'
            response = client.post('/api/pilot/families', headers=headers, json={'question': case['query'],
                'task': case['source_slug'], 'locale': 'sl', 'idempotency_key': 'editorial-live-' + case['source_slug']})
            assert response.status_code == 200
            fid = response.json()['family']['answer_family_id']
            job = app.state.jobs.claim('cpu')
            assert job and job['model'] == 'cpu' and app.state.jobs.claim('model') is None
            app.state.jobs.execute(job)
            if network and (network[0].get('error_type') or network[0].get('status_code') != 200):
                result['status'] = 'BLOCKED'
                pytest.fail('BLOCKED: accepted prose search unavailable; no substitute response', pytrace=False)
            raw = gateway.received
            if isinstance(raw, dict):
                result['lookup_response_sha256'] = digest(raw)
                result['observed_hits'] = raw.get('hits', [])[:editorial.LIMIT]
            family = client.get('/api/pilot/families/' + fid).json()
            short = family['short']
            result['pilot_state'] = {k: short.get(k) for k in ('state', 'answer_kind', 'answer_method', 'generation_status', 'error_code')}
            assert short['state'] == 'complete' and short['answer_method'] == 'source_only'
            assert short['generation_status'] == 'not_generated' and short['deterministic_result']['complete_qa_available'] is False
            observed = [hit for hit in raw['hits'] if hit['source_slug'] == case['source_slug'] and hit['resource_id'] == case['expected']['resource_id']]
            stage = 'expected_record_identity'
            assert len(observed) == 1
            hit = observed[0]
            for key, value in case['expected'].items():
                stage = 'expected_field.' + key
                assert hit[key] == value
            stage = 'independent_editorial_content'
            for fragment in case['expected_content_fragments']:
                assert fragment in ' '.join(hit['snippet'].split())
            with app.state.store.connect() as db:
                saved = db.execute('SELECT bundle,identity FROM families WHERE id=?', (fid,)).fetchone()
            bundle, identity = json.loads(saved['bundle']), json.loads(saved['identity'])
            assert bundle['hits'] == raw['hits'] == short['deterministic_result']['records']
            assert bundle['lookup_response_sha256'] == digest(raw)
            assert identity['captured_response_sha256'] == bundle['snapshot_sha256']
            stage = 'separate_owned_capture'
            assert len(short['evidence']) == len(bundle['hits']) == len(short['blocks']) - 1
            assert len({e['evidence_id'] for e in short['evidence']}) == len(bundle['hits'])
            for index, (record, evidence) in enumerate(zip(bundle['hits'], short['evidence'])):
                assert (evidence['source_slug'], evidence['external_id']) == (record['source_slug'], record['resource_id'])
                assert evidence['source_revision'] is None and evidence['captured_response_sha256'] == bundle['snapshot_sha256']
                assert record['snippet'] in short['blocks'][index + 1]['text']
            chosen = next(e for e in short['evidence'] if e['external_id'] == hit['resource_id'])
            route = '/api/pilot/families/' + fid + '/sources/' + chosen['evidence_id']
            response = client.get(route)
            assert response.status_code == 200
            drawer = response.json()
            assert hit['snippet'] in drawer['excerpt'] and hit['title'] == drawer['title']
            assert drawer['source_label'] == case['source_metadata']['display_name']
            assert drawer['bibliography'] == {'record_id': hit['resource_id'], 'rights_mode': hit['rights_mode'], 'source_date': None}
            assert drawer['url'] is None
            assert 'source_date' not in bundle['hits'][0]  # Never backfill from the retained oracle.
            stage = 'frozen_and_cross_user'
            gateway.blocked = True
            gateway.received['hits'].clear()
            assert client.get(route).json() == drawer
            assert client.get('/api/pilot/families/' + fid).json()['short'] == short
            login(client, 'editorial-other')
            assert client.get(route).status_code == client.get('/api/pilot/families/' + fid).status_code == 404
            result['checks'] = {name: True for name in ('normal_source_preset', 'independent_record_and_content',
                'source_only_no_generation', 'separate_records_and_references', 'owned_frozen_capture',
                'owner_drawer_200', 'other_owner_drawer_and_family_404', 'frozen_after_upstream_blocked',
                'missing_provenance_not_invented')}
            result['capture'] = {'captured_response_sha256': bundle['snapshot_sha256'],
                'record_id': hit['resource_id'], 'drawer_sha256': digest(drawer), 'display_sha256': short['display_sha256']}
        assert gateway.calls == len(network) == 1 and not gateway.forbidden
        result['status'] = 'PASS'
    except BaseException as error:
        result.update(failure_stage=stage, error_type=type(error).__name__)
        raise
    finally:
        result.update(primary_searches=gateway.calls, network=network, forbidden_operations=gateway.forbidden,
                      elapsed_seconds=round(time.monotonic() - started, 3))
        prose_run['cases'].append(result)
