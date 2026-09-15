"""Isolated candidate auth/journey probe; never opens the frozen input with SQLite.

Generation is explicitly synthetic. Auth, HTTP routing, CSRF, sessions, jobs,
SQLite storage and the KLIKai signing/verification functions are real code.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import tempfile
import subprocess
import sys

from .store import canonical
from .adoption import META_KEY


class SignedFixtureAdapter:
    def __init__(self, key): self.key = key
    def freeze(self, family, policy):
        from tools.zrc_reference.pilot_evidence import sign
        payload = {'question': family['question'], 'context': family['context'], 'locale': family['locale'],
                   'snapshot_id': 'synthetic-adoption-probe-v1', 'policy': policy}
        return {'bundle': sign(payload, self.key), 'identity': {'snapshot_id': payload['snapshot_id']}}
    def generate(self, bundle, tier, model, parent=None):
        from tools.zrc_reference.pilot_evidence import verify
        payload = verify(bundle, self.key)
        text = 'SYNTHETIC ADOPTION PROBE — ž 😀 e\u0301. ' + ('Long answer.' if tier == 'long' else 'Saved answer.')
        return {'status': 'validated', 'short_answer': text, 'answer_md': text,
                'evidence': [{'evidence_id': 'probe-e1', 'source_label': 'Synthetic source', 'excerpt': payload['question']}],
                'source_badges': [{'label': 'Synthetic source'}], 'generation_status': 'generated', 'answer_kind': 'generated'}
    def source(self, bundle, evidence_id, tier):
        from tools.zrc_reference.pilot_evidence import verify
        payload = verify(bundle, self.key)
        return {'evidence_id': evidence_id, 'source_label': 'Synthetic source', 'excerpt': payload['question']}


def probe_candidate_adoption(store_path, *, operator_password, new_signing_key,
                             new_edge_token, old_edge_token, old_signing_key,
                             new_gateway_token=None, old_gateway_token=None,
                             recovery_helper=None, recovery_pins=None):
    from fastapi.testclient import TestClient
    from .api import create_app
    from .security import Failure
    from tools.zrc_reference.pilot_evidence import sign, verify
    path = Path(store_path).absolute()
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm'), path.with_suffix('.deletions.jsonl')) if p.exists()}
    checks = []
    recovery = None
    with tempfile.TemporaryDirectory(prefix='pilot-adoption-probe-') as directory:
        root = Path(directory); root.chmod(0o700)
        copy = root / 'store'
        for source, target in ((path, copy), (Path(str(path)+'-wal'), Path(str(copy)+'-wal')), (Path(str(path)+'-shm'), Path(str(copy)+'-shm')), (path.with_suffix('.deletions.jsonl'), copy.with_suffix('.deletions.jsonl'))):
            if source.exists():
                shutil.copyfile(source, target); target.chmod(0o600)
        app = create_app({'store': str(copy), 'allowed_origins': ['http://127.0.0.1:18319'],
                          'secure_cookies': False, 'edge_token': new_edge_token},
                         adapter=SignedFixtureAdapter(new_signing_key), start_workers=False)
        store, auth = app.state.store, app.state.auth
        proof = json.loads(store.meta(META_KEY))
        with store.connect() as db:
            operator = db.execute('SELECT username FROM accounts WHERE id=?', (proof['operator_id'],)).fetchone()[0]
            saved = {r['id']: r['result'] for r in db.execute('SELECT id,result FROM runs')}
        edge = {'X-Pilot-Edge-Token': new_edge_token, 'X-Pilot-Client-IP': '127.0.0.1'}
        with TestClient(app, base_url='http://127.0.0.1:18319') as client:
            response = client.get('/health', headers={'X-Pilot-Edge-Token': old_edge_token})
            assert response.status_code == 403
            checks.append('old_edge_token_rejected')
            headers = dict(edge, Origin='http://127.0.0.1:18319')
            response = client.post('/api/pilot/login', json={'username': operator, 'password': operator_password}, headers=headers)
            assert response.status_code == 200
            assert response.json()['user']['must_change_password']
            headers['X-CSRF-Token'] = response.json()['csrf_token']
            assert client.get('/api/pilot/families', headers=edge).status_code == 403
            response = client.post('/api/pilot/password', json={'current_password': operator_password, 'new_password': secrets.token_urlsafe(40)}, headers=headers)
            assert response.status_code == 200
            headers['X-CSRF-Token'] = response.json()['csrf_token']
            checks.append('new_operator_login_and_forced_change')
            history = client.get('/api/pilot/families', headers=edge)
            assert history.status_code == 200
            for family in history.json()['families']:
                assert client.get('/api/pilot/families/'+family['answer_family_id'], headers=edge).status_code == 200
            checks.append('authorized_retained_family_read')
            payload = {'question': 'SYNTHETIC ADOPTION PROBE', 'locale': 'sl', 'model': 'qwen', 'task': 'reference', 'idempotency_key': 'adoption-probe-new-family'}
            assert client.post('/api/pilot/families', json=payload, headers=dict(edge, Origin=headers['Origin'])).status_code == 403
            response = client.post('/api/pilot/families', json=payload, headers=headers)
            assert response.status_code == 200
            fid = response.json()['family']['answer_family_id']
            # Only the probe-created job is run; no retained work is dispatched.
            job = app.state.jobs.claim('model')
            assert job and job['id'] == response.json()['job']['job_id'], 'Unexpected disposable probe job'
            app.state.jobs.execute(job)
            response = client.get('/api/pilot/families/'+fid, headers=edge)
            assert response.status_code == 200 and response.json()['short']['state'] == 'complete'
            run = response.json()['short']; rid = run['run_id']
            assert client.post('/api/pilot/runs/'+rid+'/delivered', json={'display_sha256': run['display_sha256']}, headers=headers).status_code == 200
            response = client.post('/api/pilot/feedback', json={'run_id': rid, 'quality_score': 4, 'comment': 'SYNTHETIC ADOPTION PROBE', 'idempotency_key': 'adoption-probe-feedback'}, headers=headers)
            assert response.status_code == 200
            assert any(r['run_id'] == rid for r in client.get('/api/pilot/feedback', headers=edge).json()['feedback'])
            assert client.get('/api/pilot/families/'+fid+'/sources/probe-e1', headers=edge).status_code == 200
            checks.append('new_signed_answer_save_source_feedback_readback')
            with store.connect() as db:
                bundle = json.loads(db.execute('SELECT bundle FROM families WHERE id=?', (fid,)).fetchone()[0])
                verify(bundle, new_signing_key)
                for rid, content in saved.items():
                    assert db.execute('SELECT result FROM runs WHERE id=?', (rid,)).fetchone()[0] == content
            checks.append('original_saved_bytes_unchanged')
            token = client.cookies.get('klikai_session')
            assert client.post('/api/pilot/logout', json={}, headers=headers).status_code == 200
            try: auth.session(token)
            except Failure: pass
            else: raise AssertionError('Logout did not revoke the session')
            checks.append('logout_revokes_session')
        try: verify(sign({'forged': True}, old_signing_key), new_signing_key)
        except ValueError: pass
        else: raise AssertionError('Retired signing key was accepted')
        checks.append('retired_signer_forgery_rejected')
        if new_gateway_token is not None:
            gateway_token_probe(new_gateway_token, old_gateway_token)
            checks.append('real_gateway_http_old_token_rejected')
        if recovery_helper is not None:
            # A second new current-key family lets the unchanged initial-HTTP
            # proof requirement remain independent of historical family count.
            second = app.state.domain.create_family(proof['operator_id'], {
                'question': 'SYNTHETIC ADOPTION RECOVERY PROBE', 'locale': 'sl',
                'model': 'qwen', 'task': 'reference', 'idempotency_key': 'adoption-probe-recovery-family'})
            with store.connect() as db:
                job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (second['job']['job_id'],)).fetchone())
            app.state.jobs.execute(job)
            recovery = probe_recovery_copy(store, root, recovery_helper, recovery_pins, new_signing_key)
            checks.append('actual_helper_backup_support_restore_and_historical_verification')
    after = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in before}
    if before != after: raise ValueError('Frozen adoption input changed during isolated probe')
    return {'status': 'PASS', 'schema': 'pilot-adoption-application-probe-v1',
            'checks': checks, 'check_count': len(checks), 'frozen_input_unchanged': True,
            'scope': 'isolated adoption copy; real HTTP auth/store/signing; synthetic generation; no models or services started',
            **({'recovery': recovery} if recovery is not None else {})}


def probe_recovery_copy(store, root, helper, pins, new_signing_key):
    """Actual infrastructure recovery helper against this disposable app copy."""
    from .store import Store
    from tools.zrc_reference import pilot_evidence
    helper = Path(helper).absolute()
    if helper.is_symlink() or not helper.is_file():
        raise ValueError('Direct recovery helper source required')
    if not isinstance(pins, dict) or not pins.get('git_commits'):
        raise ValueError('Pinned recovery source identities required')
    root = Path(root)
    key_file = root / 'new-recovery-signing-key'
    fd = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as out: out.write(new_signing_key + b'\n')
    inputs = {'probe-backend.json': {'store': str(store.path), 'allowed_origins': ['http://127.0.0.1:18319'], 'secure_cookies': False},
              'probe-gateway.json': {'pilot': {'signing_key_file': str(key_file)}},
              'probe-pins.json': pins}
    for name, value in inputs.items():
        fd = os.open(root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as out: out.write(canonical(value) + '\n')
    command = [sys.executable, '-B', str(helper), '--config', str(root / 'probe-backend.json'),
               '--gateway-config', str(root / 'probe-gateway.json'), '--pins', str(root / 'probe-pins.json'),
               '--destination', str(root / 'recovery')]
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]) + ':' + str(Path(pilot_evidence.__file__).resolve().parents[2]))
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=120)
    if result.returncode:
        raise ValueError('Isolated recovery helper failed before a verified receipt')
    report = json.loads(result.stdout)
    if report.get('status') != 'PASS' or report.get('retired_key_loaded') is not False:
        raise ValueError('Recovery helper did not establish current-only signing identity')
    restored_root = root / 'recovery/restored'
    if (restored_root / 'evidence-signing-key').read_bytes().strip() != new_signing_key:
        raise ValueError('Restored active signing identity differs')
    restored = Store(restored_root / 'account-store/pilot.sqlite')
    with store.connect() as before, restored.connect() as after:
        for query in ('SELECT id,bundle,identity FROM families ORDER BY id',
                      'SELECT id,result FROM runs ORDER BY id',
                      'SELECT * FROM accounts ORDER BY id',
                      'SELECT * FROM feedback_events ORDER BY seq',
                      'SELECT * FROM audit ORDER BY seq',
                      'SELECT * FROM login_attempts ORDER BY key,at'):
            if [tuple(r) for r in before.execute(query)] != [tuple(r) for r in after.execute(query)]:
                raise ValueError('Recovery changed preserved application record bytes')
    return {'status': 'PASS', 'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
            'current_key_signed_families_verified': report['signed_families_verified'],
            'historical_signed_families_exact': report['historical_signed_families_exact'],
            'retired_key_loaded': False, 'original_saved_bytes_preserved': True,
            'account_feedback_audit_login_history_preserved': True,
            'scope': 'actual helper backup/support/restore on disposable application copy with two synthetic new families'}


def gateway_token_probe(new_token, old_token):
    """Real gateway middleware, without starting model/source dependencies."""
    from fastapi.testclient import TestClient
    from tools.zrc_reference.service import create_app
    app = create_app({})
    app.state.pilot_token = new_token
    # No lifespan: this request must fail at real token middleware, before
    # resource initialization or a route can dispatch any work.
    client = TestClient(app)
    try:
        response = client.post('/internal/pilot/freeze', headers={'X-Pilot-Internal-Token': old_token}, json={})
        assert response.status_code == 403
        # Valid token passes auth and reaches the real bounded request schema.
        response = client.post('/internal/pilot/freeze', headers={'X-Pilot-Internal-Token': new_token}, json={})
        assert response.status_code == 422
    finally: client.close()
