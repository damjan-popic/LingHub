"""Opt-in: five real accepted lookups, each in fresh disposable pilot state.

KLIKAI_RUN_LIVE_LUKA_ACCEPTANCE=1 enables the five-entry matrix, including
the preserved Šolska case exactly once. The former standalone opt-in is retired.
KLIKAI_LUKA_ACCEPTANCE_RECEIPT may name a new JSON file in the existing
evidence directory. No operational credentials are read; no retries are allowed.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from urllib.parse import parse_qs, urlsplit

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import httpx
import pytest

from pilot_backend.adapter import Gateway
from pilot_backend.api import create_app
from pilot_backend.store import digest


ROOT = Path(__file__).resolve().parents[1]
KLIKAI = Path('/data/projects/lingstack-dev/klikai-human-testing-20260911')
CONTROL = Path('/data/services/lingstack/releases/pilot-control/20260912T205454Z-1bc89148')
RELEASE = Path('/data/datasets/raw/lingstack/klikai/luka-nonloris/releases/luka-nonloris-2026-08-24-dc0b18c2b883')
EVIDENCE = Path('/data/scratch/damjan/klikai-human-testing-20260911/linghub')
FIXTURE = ROOT / 'tests/fixtures/luka_terminology_acceptance.json'
FIXTURES = json.loads(FIXTURE.read_text())
CASES = FIXTURES['cases']
ORIGINAL_RECEIPT = EVIDENCE / 'solska-live-lookup-acceptance-20260913.json'
ORIGINAL_PROBE = EVIDENCE / 'solska-live-lookup-probe-20260913.py'
SCOPE = ('Live accepted lookup + isolated pilot API/answer-block/drawer contract; '
         'no deployed-pilot writes and no browser acceptance.')
ORIGIN = 'http://127.0.0.1:18310'  # TestClient ASGI origin, never a bound listener.
APP_FILES = ('pilot_backend/adapter.py', 'pilot_backend/api.py', 'pilot_backend/domain.py',
             'pilot_backend/jobs.py', 'pilot_backend/terminology.py', 'pilot_backend/store.py',
             'pilot_backend/security.py', 'pilot_ui/pilot.mjs')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def command(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=5).stdout.strip()


def revision(path, recorded):
    head = command(['git', '--no-optional-locks', '-C', str(path), 'rev-parse', 'HEAD'])
    return {'path': str(path), 'head': head,
            'branch': command(['git', '--no-optional-locks', '-C', str(path), 'branch', '--show-current']),
            'recorded_head_prefix': recorded, 'drift': not head.startswith(recorded)}


def expected_record(case):
    """Read and pin an independent source oracle before any network access."""
    path = RELEASE / 'normalized' / (case['source_slug'] + '.jsonl')
    assert sha(path) == case['source_file_sha256']
    manifest = json.loads((RELEASE / 'release-manifest.json').read_text())
    assert sha(RELEASE / 'release-manifest.json') == FIXTURES['release_manifest_sha256']
    assert manifest['release_id'] == RELEASE.name == FIXTURES['retained_release_id']
    assert manifest['excluded_sources'] == ['loris']
    records = [json.loads(line) for line in path.read_text().splitlines()]
    expected = case['expected']
    matches = [r for r in records if r['source_slug'] == case['source_slug'] and
               r['external_id'] == expected['external_id']]
    assert len(matches) == 1
    record = matches[0]
    assert record == records[case['source_line'] - 1]
    assert record['upstream_line'] == case['source_line']
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    assert hashlib.sha256(canonical).hexdigest() == case['record_sha256']
    assert digest(record['payload']) == record['content_hash'] == case['payload_content_hash']
    for key in ('source_slug', 'external_id', 'upstream_key', 'record_kind', 'source_date', 'categories', 'subcategories'):
        assert expected[key] == record[key]
    entries = record['payload']['entries']
    if record['record_kind'] == 'terminology_concept':
        assert expected['original_terms'] == [e['term'].strip() for e in entries if e['lang'] != 'sl']
        assert expected['standardized_terms'] == [e['term'].strip() for e in entries if e['lang'] == 'sl']
        assert case['source_languages'] == sorted({e['lang'] for e in entries})
        for key in ('proposals', 'rationale', 'examples', 'references'):
            assert expected[key] == []
    else:
        for key, original in (('original_terms', 'original'), ('standardized_terms', 'standardized'),
                              ('proposals', 'proposals'), ('rationale', 'rationale'),
                              ('examples', 'examples'), ('references', 'sources')):
            assert expected[key] == entries[0].get(original, [])
    match = case['expected_match']
    field = 'standardized_terms' if match['language'] == 'sl' else 'original_terms'
    assert match['term'] in expected[field] and match['matched_query'] == case['query']
    if case['source_slug'] == 'solska':
        assert case['query'] == 'učiteljskemu zboru' and expected['external_id'] == 'solska:abdb6f2b0975'
    else:
        assert match['match_tier'] == 'exact' and match['normalization'] == 'raw'
        assert case['query'] == match['term']
    return {'expectation': expected, 'source': {'path': str(path), 'sha256': sha(path),
            'line': case['source_line'], 'release_id': manifest['release_id'],
            'record_sha256': case['record_sha256'], 'payload_content_hash': record['content_hash']}}


def accepted_identity():
    fields = 'ActiveState,SubState,MainPID,InvocationID,WorkingDirectory,ExecStart,FragmentPath'
    state = dict(line.split('=', 1) for line in command(
        ['/usr/bin/systemctl', 'show', 'klikai.service', '--property=' + fields]).splitlines())
    if state['ActiveState'] != 'active' or state['SubState'] != 'running' or state['MainPID'] == '0':
        pytest.fail('BLOCKED: accepted KLIKai service is not running', pytrace=False)
    release = Path(state['WorkingDirectory'])
    assert release.parent == Path('/data/services/lingstack/releases/klikai')
    manifest_path = release / 'code-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    assert manifest['git_sha'] == release.name
    files = ('apps/api/klikai_api/main.py', 'apps/api/klikai_api/api/routes_search.py',
             'apps/api/klikai_api/retrieval/terminology.py', 'apps/api/klikai_api/schemas/terminology.py',
             'apps/api/klikai_api/services/terminology_morph_index.py',
             'scripts/ingest_luka_nonloris.py', 'data/config/sources.yaml',
             *(case['source_yaml_path'] for case in CASES))
    pins = {name: sha(release / name) for name in files}
    assert all(pin == manifest['files'][name] for name, pin in pins.items())
    # Independently read source metadata, never learn expected rights/label from HTTP.
    for case in CASES:
        assert pins[case['source_yaml_path']] == case['source_yaml_sha256']
    config = json.loads((CONTROL / 'config/pilot.json').read_text())
    endpoint = config['klikai_url']
    parsed = urlsplit(endpoint)
    assert parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and not parsed.username
    assert f'--host {parsed.hostname} --port {parsed.port}' in state['ExecStart']
    return {'endpoint': endpoint, 'state': state, 'release': str(release), 'git_sha': manifest['git_sha'],
            'recorded_release_prefix': 'ad16811dbb61', 'drift': not release.name.startswith('ad16811dbb61'),
            'manifest_sha256': sha(manifest_path), 'verified_files': pins,
            'endpoint_config': str(CONTROL / 'config/pilot.json')}


@pytest.fixture(scope='module')
def matrix(request):
    """Pin every expectation before I/O; preserve all outcomes in one new receipt."""
    report = {'schema': 'klikai.luka-terminology-matrix.v1', 'status': 'INCOMPLETE',
              'scope': SCOPE, 'cases': [], 'attempted_sources': [],
              'runtime_database_edition': 'Live database edition unverified by the current lookup contract.',
              'browser_exercised': False, 'operational_pilot_state_writes': 0,
              'remaining_open': ['Urgenca and Pošta pilot prose/Q&A integration',
                  'Release/manifest immutability and repeated-import/projection integrity',
                  'Release-level provenance in lookup/capture contracts',
                  'Stale six-sources/schema-v1 documentation']}
    output = os.environ.get('KLIKAI_LUKA_ACCEPTANCE_RECEIPT')
    target = Path(output) if output else None
    if target:
        assert target.parent == EVIDENCE and target.suffix == '.json' and not target.exists()
        with target.open('x') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(report, stream)
    ready = False
    try:
        assert not os.environ.get('PYTEST_XDIST_WORKER'), 'Run the bounded matrix serially'
        assert not getattr(request.config.option, 'reruns', 0), 'Automatic live retries are forbidden'
        assert len(CASES) == 5 and {c['source_slug'] for c in CASES} == {
            'solska', 'zvezek', 'crossterm', 'zkb', 'tretji_sektor'}
        assert sha(ORIGINAL_RECEIPT) == 'd989b63dc0e9bd7bf681e7bbebea9bd0cf5f979b287dc265608ff86357286f92'
        assert sha(ORIGINAL_PROBE) == 'cafdaa27d24e7b393c299c68b5bfbf27a791596542e5586b6c33a68b9bbe4c78'
        report['prior_solska_evidence'] = {str(p): sha(p) for p in (ORIGINAL_RECEIPT, ORIGINAL_PROBE)}
        report['oracles'] = {case['source_slug']: expected_record(case) for case in CASES}
        report['all_expectations_verified_before_any_lookup'] = True
        report['accepted_service'] = accepted_identity()
        report['revisions'] = {'linghub': revision(ROOT, '4759fc0a1962'),
                               'klikai': revision(KLIKAI, '4b0a112ef302')}
        report['test_files_sha256'] = {str(p.relative_to(ROOT)): sha(p) for p in (Path(__file__), FIXTURE)}
        report['pilot_application_files'] = {name: sha(ROOT / name) for name in APP_FILES}
        ready = True
        yield report
    except BaseException as error:
        report['setup_or_teardown_error'] = type(error).__name__
        report['status'] = 'BLOCKED' if isinstance(error, (PermissionError, FileNotFoundError)) or str(error).startswith('BLOCKED:') else 'FAIL'
        raise
    finally:
        report['live_lookup_calls'] = sum(case['live_lookup_calls'] for case in report['cases'])
        report['model_generation_calls'] = 0
        try:
            if ready:
                report['accepted_service_identity_unchanged'] = accepted_identity() == report['accepted_service']
                assert report['accepted_service_identity_unchanged']
                assert all(sha(ROOT / name) == pin for name, pin in report['pilot_application_files'].items())
                assert all(sha(ROOT / name) == pin for name, pin in report['test_files_sha256'].items())
                assert all(sha(path) == pin for path, pin in report['prior_solska_evidence'].items())
                outcomes = [case['status'] for case in report['cases']]
                complete = len(outcomes) == 5 and set(report['attempted_sources']) == {c['source_slug'] for c in CASES}
                report['status'] = ('PASS' if complete and all(s == 'PASS' for s in outcomes)
                    and report['live_lookup_calls'] == 5 else 'FAIL' if 'FAIL' in outcomes
                    else 'BLOCKED' if 'BLOCKED' in outcomes else 'INCOMPLETE')
        except BaseException as error:
            barrier = isinstance(error, (PermissionError, FileNotFoundError)) or str(error).startswith('BLOCKED:')
            report.update(status='BLOCKED' if barrier else 'FAIL', final_identity_error=type(error).__name__)
            raise
        finally:
            if target:
                with target.open('w') as stream:
                    json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
                    stream.write('\n')


class OneLookupGateway(Gateway):
    """Preserve production freeze/generate/source; restrict external I/O only."""
    def __init__(self, endpoint, case, matrix):
        super().__init__(endpoint, secrets.token_urlsafe(40), klikai_url=endpoint)
        self.calls = 0
        self.forbidden = []
        self.received = None
        self.blocked = False
        self.case = case
        self.matrix = matrix

    def deny(self, operation):
        self.forbidden.append(operation)
        raise AssertionError('Unexpected upstream/model operation: ' + operation)

    def request(self, path, payload, base=None):
        parsed = urlsplit(path)
        if self.blocked or self.calls or base != self.klikai_url or payload is not None:
            return self.deny('request')
        assert parsed.path == '/api/terminology/lookup'
        assert parse_qs(parsed.query) == {'q': [self.case['query']], 'allow_partial': ['false'],
            'include_prose': ['false'], 'use_morphology': ['true'], 'limit': ['20']}
        attempted = self.matrix['attempted_sources']
        if self.case['source_slug'] in attempted or len(attempted) >= 5:
            return self.deny('matrix request budget')
        attempted.append(self.case['source_slug'])
        self.calls += 1  # Consume before I/O: failure cannot trigger a retry.
        self.received = super().request(path, payload, base)
        self.blocked = True
        return self.received

    def capabilities(self):
        return self.deny('capabilities')

    def tool_capabilities(self):
        return self.deny('tool_capabilities')

    def require_generation(self, models):
        return self.deny('require_generation')


@pytest.mark.skipif(os.environ.get('KLIKAI_RUN_LIVE_LUKA_ACCEPTANCE') != '1',
                    reason='Opt-in accepted-service probe; ordinary tests never call live services')
@pytest.mark.parametrize('case', CASES, ids=[case['source_slug'] for case in CASES])
def test_real_lookup_isolated_pilot_owned_frozen_drawer(case, matrix, tmp_path, monkeypatch):
    query, slug = case['query'], case['source_slug']
    expected = matrix['oracles'][slug]['expectation']
    record_id = expected['external_id']
    receipt = {'source_slug': slug, 'query': query, 'expected_record_id': record_id,
               'status': 'FAIL', 'checks': {}, 'expected_match': case['expected_match'],
               'source_languages': case['source_languages'], 'scope_note': case['scope_note'],
               'missing_provenance': case['missing_provenance']}
    stage = 'disposable_pilot_setup'
    adapter = None
    network = []
    started = time.monotonic()
    try:
        receipt['checks']['independent_expectation_before_lookup'] = True
        accepted = matrix['accepted_service']
        adapter = OneLookupGateway(accepted['endpoint'], case, matrix)
        real_transport = httpx.HTTPTransport.handle_request

        def guarded_transport(transport, request):
            # TestClient uses an ASGI transport. Only this one request reaches a socket.
            if network or request.method != 'GET' or str(request.url).split('?', 1)[0] != accepted['endpoint'] + '/api/terminology/lookup':
                return adapter.deny('HTTP transport')
            assert not any(key in request.headers for key in ('authorization', 'cookie', 'x-pilot-internal-token'))
            observation = {'method': request.method, 'url': str(request.url), 'timeout': request.extensions.get('timeout')}
            network.append(observation)
            try:
                response = real_transport(transport, request)
                observation['status_code'] = response.status_code
                stream = response.stream

                class ObservedStream(httpx.SyncByteStream):
                    def __iter__(self):
                        try:
                            yield from stream
                        except httpx.HTTPError as error:
                            observation['error_type'] = type(error).__name__
                            raise

                    def close(self):
                        stream.close()

                response.stream = ObservedStream()
                return response
            except httpx.HTTPError as error:
                observation['error_type'] = type(error).__name__
                raise

        monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', guarded_transport)
        app = create_app({'store': str(tmp_path / 'private' / 'pilot.sqlite'),
                          'allowed_origins': [ORIGIN], 'secure_cookies': False, 'tasks': ['terminology'],
                          'human_testing_mode': True, 'comparisons_enabled': False}, adapter=adapter,
                         hasher=PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1), start_workers=False)
        password = secrets.token_urlsafe(32)
        for name in ('fixture-owner', 'fixture-other'):
            app.state.auth.create(name, password, origin='synthetic', must_change=False)

        def login(client, name):
            response = client.post('/api/pilot/login', json={'username': name, 'password': password},
                                   headers={'Origin': ORIGIN})
            assert response.status_code == 200
            return {'Origin': ORIGIN, 'X-CSRF-Token': response.json()['csrf_token']}

        stage = 'real_lookup_and_pilot_job'
        with TestClient(app, base_url=ORIGIN) as client:
            headers = login(client, 'fixture-owner')
            response = client.post('/api/pilot/families', json={'question': query, 'task': 'terminology',
                'locale': 'sl', 'idempotency_key': 'luka-real-lookup-' + slug}, headers=headers)
            assert response.status_code == 200
            fid = response.json()['family']['answer_family_id']
            job = app.state.jobs.claim('cpu')
            assert job and job['model'] == 'cpu' and app.state.jobs.claim('model') is None
            app.state.jobs.execute(job)
            if network and (network[0].get('error_type') or network[0].get('status_code') != 200):
                receipt['status'] = 'BLOCKED'
                pytest.fail('BLOCKED: accepted lookup unavailable or access denied; no substitute response', pytrace=False)
            live = adapter.received
            if isinstance(live, dict):
                receipt['lookup_response_sha256'] = digest(live)
                receipt['returned_record_identities'] = [
                    {'source_slug': hit.get('source_slug'), 'external_id': hit.get('external_id')}
                    for hit in live.get('hits', [])[:20] if isinstance(hit, dict)]
                candidates = [hit for hit in live.get('hits', []) if isinstance(hit, dict)
                              and hit.get('source_slug') == slug and hit.get('external_id') == record_id]
                if len(candidates) == 1:
                    receipt['lookup_observed'] = {**{key: candidates[0].get(key) for key in expected},
                        'matched_forms': candidates[0].get('matched_forms', [])[:20]}
            family = client.get('/api/pilot/families/' + fid).json()
            short = family['short']
            receipt['pilot_result'] = {key: short.get(key) for key in
                ('state', 'answer_kind', 'answer_method', 'generation_status', 'capability')}
            assert short['state'] == 'complete' and adapter.calls == 1 and not adapter.forbidden
            assert short['answer_kind'] == short['answer_method'] == 'deterministic'
            assert short['generation_status'] == 'not_generated' and short['capability'] == 'terminology'
            stage = 'lookup_query'
            assert live['query'] == query
            observed = [hit for hit in live['hits'] if hit['source_slug'] == slug and hit['external_id'] == record_id]
            stage = 'lookup_expected_record_identity'
            assert len(observed) == 1
            observed = observed[0]
            for key, value in expected.items():
                stage = 'lookup_record_field.' + key
                assert observed[key] == value, 'Source/lookup field differs: ' + key
            exact = [form for form in observed['matched_forms'] if all(
                form.get(key) == value for key, value in case['expected_match'].items())]
            stage = 'lookup_expected_retrieval_path'
            assert exact, 'Independently expected retrieval path absent'
            if slug == 'solska':
                assert all(form.get('lexicon_release_id') and form.get('query_signature') for form in exact)
            # Other returned forms may be ambiguous. Admission follows the existing
            # production contract; raw exact cases need no morphology identity.
            receipt['checks']['independent_record_matches_real_lookup'] = True
            stage = 'frozen_capture_and_presentation'
            with app.state.store.connect() as db:
                saved = db.execute('SELECT bundle,identity FROM families WHERE id=?', (fid,)).fetchone()
            bundle, identity = json.loads(saved['bundle']), json.loads(saved['identity'])
            assert bundle['lookup_response_sha256'] == digest(live)
            assert identity['captured_response_sha256'] == bundle['snapshot_sha256']
            assert short['deterministic_result']['records'] == bundle['hits']
            keys = [(hit['source_slug'], hit['external_id']) for hit in bundle['hits']]
            assert len(keys) == len(set(keys)) == len(short['evidence'])
            assert len({e['evidence_id'] for e in short['evidence']}) == len(keys)
            record_blocks = [block for block in short['blocks'] if block['block_id'].startswith('record-')]
            assert len(record_blocks) == len(keys)
            for index, (hit, item, block) in enumerate(zip(bundle['hits'], short['evidence'], record_blocks)):
                assert (item['source_slug'], item['external_id']) == keys[index]
                assert block['block_id'] == 'record-' + str(index)
                assert hit['title'] in block['text'] and hit['source_display_name'] in block['text']
            captured = [hit for hit in bundle['hits'] if (hit['source_slug'], hit['external_id']) == (slug, record_id)]
            assert len(captured) == 1
            assert captured[0] == {**observed, 'accepted_matched_forms': captured[0]['accepted_matched_forms']}
            assert any(form in captured[0]['accepted_matched_forms'] for form in exact)
            if slug == 'solska':
                assert all(form in captured[0]['accepted_matched_forms'] for form in exact)
            evidence = [item for item in short['evidence'] if item['source_slug'] == slug and item['external_id'] == record_id]
            assert len(evidence) == 1
            text = '\n'.join(block['text'] for block in short['blocks'])
            for value in expected['original_terms'] + expected['standardized_terms'] + expected['categories'] + expected['subcategories']:
                assert value in text
            eid = evidence[0]['evidence_id']
            route = '/api/pilot/families/' + fid + '/sources/' + eid
            first = client.get(route)
            assert first.status_code == 200
            drawer = first.json()
            assert drawer['source_label'] == expected['source_display_name']
            assert drawer['bibliography'] == {'source_date': expected['source_date'],
                'record_id': record_id, 'rights_mode': expected['rights_mode']}
            for value in expected['original_terms'] + expected['standardized_terms'] + expected['rationale']:
                assert value in drawer['excerpt']
            stage = 'frozen_read_and_ownership'
            adapter.blocked = True
            adapter.received['hits'].clear()  # Test memory only; never mutate the actual service.
            assert client.get(route).json() == drawer
            assert client.get('/api/pilot/families/' + fid).json()['short'] == short
            login(client, 'fixture-other')
            assert client.get(route).status_code == 404
            assert client.get('/api/pilot/families/' + fid).status_code == 404
            receipt['checks'].update(deterministic_answer=True, persisted_frozen_capture=True,
                unique_record_source_references=True, owner_drawer_200=True, other_owner_drawer_404=True,
                other_owner_family_404=True,
                frozen_drawer_after_upstream_disabled=True, answer_block_and_drawer_contract=True)
            receipt['capture'] = {'lookup_response_sha256': bundle['lookup_response_sha256'],
                'captured_response_sha256': identity['captured_response_sha256'],
                'record_id': record_id, 'retained_record_count': len(keys),
                'retained_record_identities': [{'source_slug': source, 'external_id': rid} for source, rid in keys],
                'drawer_sha256': digest(drawer), 'display_sha256': short['display_sha256'],
                'bibliography': drawer['bibliography']}
        stage = 'final_request_budget'
        assert len(network) == 1 and adapter.calls == 1 and not adapter.forbidden
        receipt['checks']['one_real_lookup_zero_forbidden_calls'] = True
        receipt['status'] = 'PASS'
    except BaseException as error:
        receipt.update(failure_stage=stage, error_type=type(error).__name__)
        if str(error).startswith('BLOCKED:'):
            receipt['status'] = 'BLOCKED'
        raise
    finally:
        receipt.update(elapsed_seconds=round(time.monotonic() - started, 3), network=network,
                       forbidden_operations=adapter.forbidden if adapter else [],
                       live_lookup_calls=adapter.calls if adapter else 0)
        matrix['cases'].append(receipt)
        if output := os.environ.get('KLIKAI_LUKA_ACCEPTANCE_RECEIPT'):
            # Preserve completed cases even if the bounded invocation is interrupted.
            with Path(output).open('w') as stream:
                json.dump(matrix, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write('\n')
