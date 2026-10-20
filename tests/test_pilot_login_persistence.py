"""Browser-native sign-in and opt-in, revocable tester sessions."""
import pytest
from fastapi.testclient import TestClient
from pilot_backend.security import Failure, Auth
from pilot_backend.store import Store
from test_pilot_backend import rig, PASSWORD

ORIGIN = 'http://127.0.0.1:18310'


def sign_in(client, **extra):
    return client.post('/api/pilot/login', json={
        'username': 'tester-user', 'password': PASSWORD, **extra},
        headers={'Origin': ORIGIN})


def test_opt_in_survives_idle_and_process_restart_then_expires(rig, monkeypatch):
    app, client, _, _ = rig
    response = sign_in(client, remember=True)
    assert response.status_code == 200
    assert response.json()['remembered'] is True
    assert 'Max-Age=2592000' in response.headers['set-cookie']
    token = client.cookies.get('klikai_session')
    original = app.state.auth.session(token, touch=False)
    now = original['session_created']
    monkeypatch.setattr('pilot_backend.security.stamp', lambda: now + 29 * 86400)
    reopened = Auth(Store(app.state.store.path), app.state.auth.hasher)
    assert reopened.session(token)['id'] == original['id']
    monkeypatch.setattr('pilot_backend.security.stamp', lambda: now + 30 * 86400 + 1)
    with pytest.raises(Failure):
        reopened.session(token)
    with app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM remembered_sessions').fetchone()[0] == 0


def test_default_session_keeps_existing_idle_limit(rig, monkeypatch):
    app, client, _, _ = rig
    response = sign_in(client)
    assert response.json()['remembered'] is False
    token = client.cookies.get('klikai_session')
    s = app.state.auth.session(token, touch=False)
    monkeypatch.setattr('pilot_backend.security.stamp', lambda: s['last_seen'] + 1801)
    with pytest.raises(Failure):
        app.state.auth.session(token)


@pytest.mark.parametrize('action', ['logout', 'disable', 'reset_password'])
def test_remembered_credentials_remain_revocable(rig, action):
    app, client, _, ids = rig
    response = sign_in(client, remember=True)
    token = client.cookies.get('klikai_session')
    if action == 'logout':
        assert client.post('/api/pilot/logout', json={}, headers={
            'Origin': ORIGIN, 'X-CSRF-Token': response.json()['csrf_token']}).status_code == 200
    elif action == 'disable':
        with app.state.store.tx() as db:
            db.execute('UPDATE accounts SET enabled=0 WHERE id=?', (ids['tester'],))
    else:
        app.state.auth.reset_password(ids['tester'], 'replacement synthetic password', temporary=False)
    with pytest.raises(Failure):
        app.state.auth.session(token)
    with app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM remembered_sessions').fetchone()[0] == 0


def test_privileged_and_temporary_accounts_cannot_extend_sessions(rig):
    app, client, _, ids = rig
    for role in ('admin', 'reviewer'):
        r = client.post('/api/pilot/login', json={'username': role+'-user',
            'password': PASSWORD, 'remember': True}, headers={'Origin': ORIGIN})
        assert r.json()['remembered'] is False
        assert 'Max-Age=28800' in r.headers['set-cookie']
    with app.state.store.tx() as db:
        db.execute('UPDATE accounts SET must_change=1 WHERE id=?', (ids['tester'],))
    assert sign_in(client, remember=True).json()['remembered'] is False


def test_native_password_manager_form_navigates_without_credentials_in_url(rig):
    app, client, _, _ = rig
    answer = '2a759697-0a4b-4be3-b167-3447088eccef'
    r = client.post('/api/pilot/login', data={'username': 'tester-user',
        'password': PASSWORD, 'remember': 'on', 'locale': 'it', 'answer': answer,
        'return_to': 'https://untrusted.example/'},
        headers={'Origin': ORIGIN}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers['location'] == '/it/?answer='+answer
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    session = client.get('/api/pilot/session').json()
    assert session['authenticated'] and session['remembered']
    # All following mutations still require the CSRF token.
    assert client.post('/api/pilot/logout', json={}, headers={'Origin': ORIGIN}).status_code == 403


def test_native_errors_and_request_boundaries(rig):
    _, client, _, _ = rig
    data = {'username': 'tester-user', 'password': 'incorrect secret', 'locale': 'it'}
    r = client.post('/api/pilot/login', data=data,
        headers={'Origin': ORIGIN}, follow_redirects=False)
    assert r.status_code == 303 and r.headers['location'] == '/it/?login_error=login_failed'
    assert 'incorrect' not in str(r.headers) and 'incorrect' not in r.text
    assert client.post('/api/pilot/login', data=data, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/pilot/families', data=data, headers={'Origin': ORIGIN}).status_code == 415
    assert sign_in(client, remember='true').status_code == 400
    r = client.post('/api/pilot/login', content='username=x&username=y&password=z',
        headers={'Origin': ORIGIN, 'Content-Type': 'application/x-www-form-urlencoded'})
    assert r.status_code == 400
    r = client.post('/api/pilot/login', data={'username': 'x', 'password': 'z'*65537},
        headers={'Origin': ORIGIN})
    assert r.status_code == 413


def test_old_store_session_is_preserved_during_additive_migration(rig):
    app, client, _, _ = rig
    sign_in(client)
    token = client.cookies.get('klikai_session')
    with app.state.store.tx() as db:
        db.execute('DROP TABLE remembered_sessions')
    reopened = Store(app.state.store.path)
    auth = Auth(reopened, app.state.auth.hasher)
    assert auth.session(token)['remembered'] == 0


def test_backup_removes_remembered_session_material(rig, tmp_path):
    import sqlite3
    import tarfile
    from pilot_backend.backup import backup, restore
    app, client, _, _ = rig
    sign_in(client, remember=True)
    token = client.cookies.get('klikai_session')
    record = app.state.auth.session(token)
    archive = tmp_path/'sessions-excluded.tar.gz'
    backup(app.state.store, archive)
    with tarfile.open(archive) as tar:
        raw = tar.extractfile('pilot.sqlite').read()
    with sqlite3.connect(':memory:') as db:
        db.deserialize(raw)
        assert db.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM remembered_sessions').fetchone()[0] == 0
        assert list(db.execute('PRAGMA foreign_key_check')) == []
    assert record['csrf'].encode() not in raw and record['token_hash'].encode() not in raw
    assert restore(archive, tmp_path/'restored')['foreign_key_violations'] == 0
    assert app.state.auth.session(token)['remembered'] == 1


def test_native_form_prefix_secure_cookie_and_password_manager_attributes(rig):
    from pilot_backend.api import create_app
    app, _, adapter, _ = rig
    config = {**app.state.config, 'base_path': '/pilot', 'secure_cookies': True,
              'allowed_origins': ['https://pilot.example']}
    prefixed = create_app(config, adapter=adapter, hasher=app.state.auth.hasher, start_workers=False)
    with TestClient(prefixed, base_url='https://pilot.example') as client:
        page = client.get('/pilot/it/').text
        assert 'method="post" action="/pilot/api/pilot/login" autocomplete="on"' in page
        assert 'autocomplete="username"' in page and 'autocomplete="current-password"' in page
        r = client.post('/pilot/api/pilot/login', data={'username': 'tester-user',
            'password': PASSWORD, 'remember': 'on', 'locale': 'it'},
            headers={'Origin': 'https://pilot.example'}, follow_redirects=False)
        assert r.status_code == 303 and r.headers['location'] == '/pilot/it/'
        assert 'Secure' in r.headers['set-cookie'] and 'Path=/pilot/' in r.headers['set-cookie']
