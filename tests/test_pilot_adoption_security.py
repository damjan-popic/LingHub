"""Real application migration/HTTP tests on synthetic records only."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient

from pilot_backend.adoption import META_KEY, historical_record, migrate_adoption, validate_adoption
from pilot_backend.adoption_probe import SignedFixtureAdapter, probe_candidate_adoption, gateway_token_probe
from pilot_backend.api import create_app
from pilot_backend.security import Auth, Failure
from pilot_backend.store import Store, canonical
from tools.zrc_reference.pilot_evidence import sign, verify

OLD_PASSWORD = 'old synthetic password - only fixture'
NEW_PASSWORD = 'new synthetic individual password - only fixture'
OLD_KEY = b'old-exposed-synthetic-signing-key-for-test'
NEW_KEY = b'new-private-synthetic-signing-key-for-test'
ORIGIN = 'http://127.0.0.1:18319'


def snapshot(source, target):
    source_db = sqlite3.connect(source)
    target_db = sqlite3.connect(target)
    try: source_db.backup(target_db)
    finally: target_db.close(); source_db.close()


@pytest.fixture
def retained(tmp_path):
    folder = tmp_path / 'private'; folder.mkdir(mode=0o700)
    before = folder / 'before'
    hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1, type=Type.ID)
    app = create_app({'store': str(before), 'allowed_origins': [ORIGIN], 'secure_cookies': False},
                     adapter=SignedFixtureAdapter(OLD_KEY), hasher=hasher, start_workers=False)
    auth, store = app.state.auth, app.state.store
    operator = auth.create('fixture-human-admin', OLD_PASSWORD, role='admin', origin='human', must_change=False)
    other = auth.create('fixture-other', OLD_PASSWORD, origin='synthetic', must_change=False)
    token, _ = auth.login('fixture-human-admin', OLD_PASSWORD, 'fixture')
    first = app.state.domain.create_family(operator, {'question': 'SYNTHETIC retained ž 😀 e\u0301', 'task': 'reference', 'model': 'qwen', 'idempotency_key': 'retained-family-key'})
    job = app.state.jobs.claim(); app.state.jobs.execute(job)
    fid = first['family']['answer_family_id']; family = app.state.domain.family(operator, fid)
    rid = family['short']['run_id']
    app.state.domain.delivered(operator, rid, {'display_sha256': family['short']['display_sha256']})
    app.state.domain.feedback(operator, {'run_id': rid, 'quality_score': 4, 'comment': 'SYNTHETIC retained feedback', 'idempotency_key': 'retained-feedback-key'})
    with store.tx() as db:
        db.execute('CREATE TABLE test_duplicates(value BLOB, typed)')
        db.executemany('INSERT INTO test_duplicates VALUES(?,?)', [(b'\0\xff', 1), (b'\0\xff', 1), (b'\0\xff', 1.0), (None, '1')])
    after = folder / 'after'; snapshot(before, after)
    inputs = {'operator_id': operator, 'operator_password': NEW_PASSWORD, 'migration_id': 'synthetic-migration-01',
              'source_identity_sha256': '1'*64, 'receipt_sha256s': {'backup': '2'*64, 'preflight': '3'*64},
              'legacy_key': OLD_KEY, 'hasher': hasher}
    proof = migrate_adoption(after, **inputs)
    return {'before': before, 'after': after, 'proof': proof, 'inputs': inputs, 'operator': operator,
            'other': other, 'old_session': token, 'fid': fid, 'rid': rid, 'hasher': hasher}


def test_exact_declared_migration_and_repeat(retained):
    r = retained
    result = validate_adoption(r['before'], r['after'], r['proof'])
    assert result['status'] == 'PASS'
    assert {'sessions', 'login_attempts', 'audit', 'sqlite_sequence', 'test_duplicates'} <= set(result['all_tables_checked'])
    before = r['after'].read_bytes()
    assert migrate_adoption(r['after'], **r['inputs']) == r['proof']
    assert r['after'].read_bytes() == before
    changed = dict(r['inputs'], source_identity_sha256='9'*64)
    with pytest.raises(ValueError, match='input conflict'): migrate_adoption(r['after'], **changed)
    assert r['after'].read_bytes() == before


def test_real_old_credentials_and_sessions_fail_new_operator_works(retained):
    r = retained; store = Store(r['after']); auth = Auth(store, r['hasher'])
    with pytest.raises(Failure): auth.session(r['old_session'])
    for username in ('fixture-human-admin', 'fixture-other'):
        with pytest.raises(Failure): auth.login(username, OLD_PASSWORD, 'fixture')
    token, session = auth.login('fixture-human-admin', NEW_PASSWORD, 'fixture')
    assert session['id'] == r['operator'] and session['must_change']
    auth.logout(token)
    with pytest.raises(Failure): auth.session(token)


def test_http_candidate_probe_preserves_input_and_saved_bytes(retained):
    r = retained
    result = probe_candidate_adoption(r['after'], operator_password=NEW_PASSWORD,
        new_signing_key=NEW_KEY, old_signing_key=OLD_KEY, new_edge_token='n'*40,
        old_edge_token='o'*40, new_gateway_token='g'*40, old_gateway_token='x'*40)
    assert result['status'] == 'PASS' and result['frozen_input_unchanged']
    assert 'real_gateway_http_old_token_rejected' in result['checks']


def test_authorized_exact_historical_source_without_retired_key(retained):
    r = retained
    app = create_app({'store': str(r['after']), 'allowed_origins': [ORIGIN], 'secure_cookies': False},
                     adapter=SignedFixtureAdapter(NEW_KEY), hasher=r['hasher'], start_workers=False)
    token, session = app.state.auth.login('fixture-human-admin', NEW_PASSWORD, 'fixture')
    # An actual new-password change is required before the read APIs.
    token, session = app.state.auth.change_password(r['operator'], NEW_PASSWORD, 'replacement permanent fixture password')
    with TestClient(app, base_url=ORIGIN) as c:
        c.cookies.set('klikai_session', token)
        response = c.get('/api/pilot/families/'+r['fid'])
        assert response.status_code == 200
        assert response.json()['short']['historical_evidence']['fresh_origin_assurance'] is False
        response = c.get('/api/pilot/families/'+r['fid']+'/sources/probe-e1')
        assert response.status_code == 200 and response.json()['excerpt'] == 'SYNTHETIC retained ž 😀 e\u0301'
        h = {'Origin': ORIGIN, 'X-CSRF-Token': session['csrf']}
        assert c.post('/api/pilot/families/'+r['fid']+'/expand', json={'idempotency_key': 'history-no-new-signing'}, headers=h).status_code == 409
        auth = app.state.auth
        # Privately provision the other synthetic test account only to prove
        # that authenticated ownership still gates retained IDs.
        auth.reset_password(r['other'], 'other individual fixture password', temporary=False)
        token, _ = auth.login('fixture-other', 'other individual fixture password', 'fixture-other')
        c.cookies.set('klikai_session', token)
        assert c.get('/api/pilot/families/'+r['fid']).status_code == 404
        assert c.get('/api/pilot/families/'+r['fid']+'/sources/probe-e1').status_code == 404


@pytest.mark.parametrize('change', ['context', 'owner', 'result', 'run_id', 'tombstone'])
def test_historical_changed_or_replayed_record_fails(retained, change):
    r = retained; store = Store(r['after'])
    with store.tx() as db:
        if change in ('context', 'owner'):
            db.execute('DROP TRIGGER family_identity_immutable')
            value = 'forged context' if change == 'context' else r['other']
            db.execute('UPDATE families SET '+change+'=? WHERE id=?', (value, r['fid']))
        elif change == 'result':
            db.execute('DROP TRIGGER run_result_immutable')
            db.execute('UPDATE runs SET result=? WHERE id=?', ('{}', r['rid']))
        elif change == 'tombstone':
            db.execute('INSERT INTO tombstones VALUES(?,?,?,?,?)', (1, r['operator'], 1.0, '0'*64, '1'*64))
        with pytest.raises(Failure):
            historical_record(db, r['fid'], owner=r['operator'], run_id='reused-uninventoried-id' if change == 'run_id' else r['rid'])


def test_legacy_forgery_rejected_by_real_signer_and_gateway_boundary():
    packet = sign({'context': 'forged legacy but mathematically valid'}, OLD_KEY)
    assert verify(packet, OLD_KEY)['context']
    with pytest.raises(ValueError): verify(packet, NEW_KEY)
    gateway_token_probe('new-token-'+'n'*32, 'old-token-'+'o'*32)


@pytest.mark.parametrize('table', ['login_attempts', 'audit', 'test_duplicates', 'accounts'])
def test_undeclared_mutation_refused(retained, table):
    r = retained
    with sqlite3.connect(r['after']) as db:
        if table == 'accounts': db.execute("UPDATE accounts SET cohort='changed'")
        else: db.execute('DELETE FROM '+table)
    with pytest.raises(ValueError): validate_adoption(r['before'], r['after'], r['proof'])


def test_real_gateway_runtime_new_key_and_legacy_forgery_http(tmp_path):
    """Real snapshot build, signer, registry, runtime and HTTP consumers; CPU only."""
    import asyncio
    from tools.zrc_reference.fulltext import Store as SourceStore, build, normalize
    from tools.zrc_reference.grounded import ReferenceEngine
    from tools.zrc_reference.pilot_runtime import PilotRuntime
    from tools.zrc_reference.snapshot_registry import SnapshotRegistry
    from tools.zrc_reference.service import create_app as gateway_app
    passage = normalize({'passage_id': 'jezikovna_svetovalnica:fixture:post:2:chars:0-20:abc',
        'source_id': 'jezikovna_svetovalnica:fixture', 'source_revision': 'a'*64,
        'source_slug': 'jezikovna_svetovalnica', 'role': 'expert_answer',
        'title': 'Synthetic Hugo', 'url': 'https://svetovalnica.zrc-sazu.si/topic/1/synthetic',
        'text': 'Slovensko ime: Hugo, rodilnik Huga.', 'authors': ['Synthetic fixture'],
        'context': {'question': 'Hugo?', 'title': 'Synthetic Hugo'},
        'span': {'post_id': '2', 'char_start': 0, 'char_end': 33}, 'edition': None})
    raw = tmp_path/'passages.jsonl'; raw.write_text(canonical(passage)+'\n')
    source = SourceStore(build([raw], tmp_path/'sources'))
    engine = ReferenceEngine(source, None, {})
    runtime = PilotRuntime(engine, {}, NEW_KEY)
    app = gateway_app({})
    app.state.pilot_token = 'n'*40; app.state.pilot = runtime
    app.state.pilot_dispatch = SnapshotRegistry(runtime, NEW_KEY)
    app.state.pilot_freeze_slots = asyncio.Semaphore(2)
    app.state.pilot_model_slots = {m: asyncio.Semaphore(2) for m in ('qwen', 'gemma')}
    # No lifespan: fixture-configured real runtime, no external dependencies.
    with _gateway_client(app) as client:
        headers = {'X-Pilot-Internal-Token': 'n'*40}
        response = client.post('/internal/pilot/freeze', headers=headers,
                               json={'question': 'Hugo', 'task': 'source_search'})
        assert response.status_code == 200, response.text
        bundle = response.json()['bundle']; verify(bundle, NEW_KEY)
        request = {'bundle': bundle, 'model': 'qwen', 'tier': 'short'}
        assert client.post('/internal/pilot/generate', headers=headers, json=request).status_code == 200
        source_request = {'bundle': bundle, 'evidence_id': passage['passage_id'], 'tier': 'short'}
        assert client.post('/internal/pilot/source', headers=headers, json=source_request).status_code == 200
        old_valid = sign(bundle['payload'], OLD_KEY)
        assert verify(old_valid, OLD_KEY) == bundle['payload']
        for route, request in [('/internal/pilot/generate', request), ('/internal/pilot/source', source_request)]:
            assert client.post(route, headers=headers, json=dict(request, bundle=old_valid)).status_code == 422
        forged = sign(dict(bundle['payload'], question='forged fresh legacy packet'), OLD_KEY)
        assert client.post('/internal/pilot/generate', headers=headers, json={'bundle': forged, 'model': 'qwen', 'tier': 'short'}).status_code == 422


from contextlib import contextmanager
@contextmanager
def _gateway_client(app):
    client = TestClient(app, base_url='http://127.0.0.1')
    try: yield client
    finally: client.close()
