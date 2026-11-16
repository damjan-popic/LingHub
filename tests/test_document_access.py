"""Document availability follows configured account identities on every host."""
import pytest
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient

from pilot_backend.api import config_checked, create_app
from pilot_backend.backup import backup, restore
from test_document_uploads import Adapter, ORIGIN, PASSWORD, finish, login, rig, start, upload


@pytest.mark.parametrize('value', ['all', True, [1], [''], ['same', 'same'], [['nested']]])
def test_document_account_configuration_is_explicit(value):
    with pytest.raises(ValueError, match='Document account IDs'):
        config_checked({'allowed_origins': [ORIGIN], 'secure_cookies': False,
                        'document_account_ids': value})


def test_account_entitlement_gates_capabilities_upload_and_all_document_routes(rig):
    app, client, adapter, ids = rig
    app.state.config['document_account_ids'] = [ids['one']]
    owner_headers = login(client, 'one')
    assert client.get('/api/pilot/session').json()['capabilities']['documents']['enabled']
    document = upload(client, owner_headers)
    identifier = document['document_id']
    assert client.get('/api/pilot/documents/' + identifier).status_code == 200
    headers = login(client, 'two')
    assert 'documents' not in client.get('/api/pilot/session').json()['capabilities']
    assert 'documents' not in client.get('/api/pilot/capabilities').json()
    endpoint = '/api/pilot/documents/' + identifier
    for path in ['/api/pilot/documents', endpoint, endpoint + '/export/txt',
                 endpoint + '/draft/docx', endpoint + '/selected/txt']:
        assert client.get(path).status_code == 403, path
    for suffix in ['/start', '/cancel', '/delete', '/ai/prepare', '/ai/start',
                   '/ai/decision', '/ai/cancel', '/summary/prepare', '/summary']:
        assert client.post(endpoint + suffix, json={}, headers=headers).status_code == 403, suffix
    # Refuse before reading or parsing a binary request from an excluded account.
    assert client.post('/api/pilot/documents/upload', content=b'not a document', headers={
        **headers, 'Content-Type': 'application/octet-stream',
        'Content-Length': str(3 * 1024 * 1024)}).status_code == 403
    assert adapter.calls == []
    login(client, 'one')
    assert client.get(endpoint).json()['sections'] == document['sections']
    app.state.config['document_account_ids'] = []
    assert client.get(endpoint).status_code == 403


def test_same_allowlisted_account_and_saved_document_survive_backup_to_new_https_host(rig, tmp_path):
    app, client, adapter, ids = rig
    headers = login(client)
    original = 'Prvi izvirnik.\n\nDrugi odstavek.\n'.encode('utf-8')
    document = upload(client, headers, data=original)
    assert start(client, headers, document['document_id']).status_code == 200
    finish(app)
    document = client.get('/api/pilot/documents/' + document['document_id']).json()
    assert document['state'] == 'complete'
    families = {row['family_id']: client.get('/api/pilot/families/' + row['family_id']).json()
                for row in document['sections']}
    archive = tmp_path / 'portable.tar.gz'
    result = backup(app.state.store, archive)
    restored = tmp_path / 'another-server'
    restore(archive, restored, expected_sha256=result['sha256'])
    origin = 'https://new-host.example'
    config = {'store': str(restored / 'pilot.sqlite'), 'allowed_origins': [origin],
              'secure_cookies': True, 'base_path': '/klikai', 'unified_answers': True,
              'models': ['auto'], 'klikai_url': 'http://source-provider.example:9000',
              'document_uploads': True, 'document_account_ids': [ids['one']]}
    moved = create_app(config, adapter=Adapter(), start_workers=False,
                       hasher=PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1, type=Type.ID))
    with TestClient(moved, base_url=origin) as other:
        assert not other.get('/klikai/api/pilot/session').json()['authenticated']
        response = other.post('/klikai/api/pilot/login', json={
            'username': 'one', 'password': PASSWORD}, headers={'Origin': origin})
        assert response.status_code == 200
        session = response.json()
        assert session['user']['id'] == ids['one']
        assert session['capabilities']['documents']['enabled']
        saved = other.get('/klikai/api/pilot/documents/' + document['document_id']).json()
        assert saved['sections'] == document['sections']
        assert ''.join(row['text'] for row in saved['sections']).encode('utf-8') == original
        assert other.get('/klikai/api/pilot/documents').status_code == 200
        for identifier, family in families.items():
            assert other.get('/klikai/api/pilot/families/' + identifier).json() == family
        report = other.get('/klikai/api/pilot/documents/' + document['document_id'] + '/export/txt')
        assert report.status_code == 200
        assert origin + '/klikai/' in report.text
        assert ORIGIN not in report.text
