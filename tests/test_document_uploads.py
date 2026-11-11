"""Documents stay owned, lossless and on the existing frozen-answer queue."""
import io
import json
import zipfile
import pytest
from argon2 import PasswordHasher, Type
from fastapi.testclient import TestClient
from pilot_backend.api import create_app
from pilot_backend.security import Failure
from pilot_backend.document_uploads import Documents, request_for, task_for

ORIGIN = 'http://127.0.0.1:18310'
PASSWORD = 'synthetic document test password'


class Adapter:
    def __init__(self):
        self.calls = []

    def freeze(self, family, policy):
        self.calls.append(family)
        return {'bundle': {'question': family['question']}, 'identity': {'snapshot': 'test-only'}}

    def generate(self, bundle, tier, model, parent=None):
        return {'status': 'validated', 'answer_md': 'Predlog z virom [1].',
                'short_answer': 'Predlog z virom [1].', 'answer_support': 'supported',
                'source_badges': [], 'limitations': [],
                'evidence': [{'evidence_id': 'E1', 'local_number': 1,
                              'title': 'Recorded rule', 'url': 'https://example.org/rule'}]}


@pytest.fixture
def rig(tmp_path):
    adapter = Adapter()
    app = create_app({'store': str(tmp_path / 'pilot.sqlite'), 'allowed_origins': [ORIGIN],
        'secure_cookies': False, 'unified_answers': True, 'document_uploads': True, 'models': ['auto'],
        'klikai_url': 'http://127.0.0.1:18313'}, adapter=adapter, start_workers=False,
        hasher=PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1, type=Type.ID))
    ids = {name: app.state.auth.create(name, PASSWORD, role='tester', origin='synthetic',
                                     must_change=False) for name in ('one', 'two')}
    with TestClient(app, base_url=ORIGIN) as client:
        yield app, client, adapter, ids


def login(client, name='one'):
    result = client.post('/api/pilot/login', json={'username': name, 'password': PASSWORD},
                         headers={'Origin': ORIGIN})
    assert result.status_code == 200
    return {'Origin': ORIGIN, 'X-CSRF-Token': result.json()['csrf_token']}


def upload(client, headers, data=b'Prvi odstavek.\n\nDrugi odstavek.', key='document-test-key'):
    response = client.post('/api/pilot/documents/upload', content=data,
        headers={**headers, 'Content-Type': 'application/octet-stream',
                 'X-Document-Name': 'test.txt', 'X-Upload-Key': key})
    assert response.status_code == 200, response.text
    return response.json()


def start(client, headers, identifier, **extra):
    return client.post('/api/pilot/documents/' + identifier + '/start', headers=headers,
        json={'mode': 'review', 'source_language': 'sl', 'locale': 'sl',
              'idempotency_key': 'document-start-key', **extra})


def finish(app):
    for _ in range(110):
        app.state.documents.advance()
        job = app.state.jobs.claim()
        if job:
            app.state.jobs.execute(job)
        else:
            break


def test_upload_requires_auth_origin_csrf_and_its_exact_content_type(rig):
    app, client, _, _ = rig
    endpoint = '/api/pilot/documents/upload'
    assert client.post(endpoint, content=b'x', headers={'Origin': ORIGIN,
        'Content-Type': 'application/octet-stream'}).status_code == 401
    headers = login(client)
    assert client.post(endpoint, content=b'x', headers={'Origin': ORIGIN,
        'Content-Type': 'application/octet-stream'}).status_code == 403
    assert client.post(endpoint, content=b'x', headers={**headers,
        'Origin': 'https://evil.example', 'Content-Type': 'application/octet-stream'}).status_code == 403
    assert client.post(endpoint, json={}, headers=headers).status_code == 415
    assert client.post('/api/pilot/families', content=b'x', headers={**headers,
        'Content-Type': 'application/octet-stream'}).status_code == 415
    response = client.post(endpoint, content=b'x' * (2 * 1024 * 1024 + 1), headers={
        **headers, 'Content-Type': 'application/octet-stream'})
    assert response.status_code == 413
    assert response.headers['cache-control'] == 'no-store'


def test_original_unicode_and_idempotency_remain_owned(rig):
    app, client, adapter, ids = rig
    headers = login(client)
    original = 'Čebelica 🧑‍🏫 in človek.\r\n\r\nDrugi odstavek.\n'
    doc = upload(client, headers, original.encode())
    assert ''.join(s['text'] for s in doc['sections']) == original
    assert upload(client, headers, original.encode())['document_id'] == doc['document_id']
    conflict = client.post('/api/pilot/documents/upload', content=b'different', headers={
        **headers, 'Content-Type': 'application/octet-stream', 'X-Document-Name': 'test.txt',
        'X-Upload-Key': 'document-test-key'})
    assert conflict.status_code == 409
    assert adapter.calls == []  # Preview does not submit model/source work.
    identifier = doc['document_id']
    other = login(client, 'two')
    assert client.get('/api/pilot/documents').json() == {'documents': []}
    for suffix in ('', '/export/txt', '/export/docx'):
        assert client.get('/api/pilot/documents/' + identifier + suffix).status_code == 404
    for suffix in ('/start', '/cancel', '/delete'):
        assert client.post('/api/pilot/documents/' + identifier + suffix,
                           headers=other, json={}).status_code in {400, 404}
    with pytest.raises(Failure, match='not_found'):
        app.state.documents.get(ids['two'], identifier)


def test_document_uses_existing_queue_and_exports_all_originals_and_sources(rig):
    app, client, adapter, _ = rig
    headers = login(client)
    original = 'Prvi. ' * 700 + '\n\nDrugi.'
    doc = upload(client, headers, original.encode())
    identifier = doc['document_id']
    assert start(client, headers, identifier).status_code == 200
    assert start(client, headers, identifier).status_code == 200
    app.state.documents.advance()
    app.state.documents.advance()
    with app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM document_sections').fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM jobs WHERE state='queued'").fetchone()[0] == 1
    finish(app)
    doc = client.get('/api/pilot/documents/' + identifier).json()
    assert doc['state'] == 'complete'
    assert doc['processed'] == doc['total'] > 1
    assert ''.join(s['text'] for s in doc['sections']) == original
    assert len(adapter.calls) == doc['total']
    assert all(s['family_id'] and s['sources'][0]['title'] == 'Recorded rule' for s in doc['sections'])
    assert [r['original_question'] for r in adapter.calls] == [request_for(s, doc['options'])['question'] for s in doc['sections']]
    response = client.get('/api/pilot/documents/' + identifier + '/export/txt')
    assert response.status_code == 200
    assert 'https://example.org/rule' in response.text
    for section in doc['sections']:
        assert section['text'] in response.text
    response = client.get('/api/pilot/documents/' + identifier + '/export/docx')
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert b'Recorded rule' in archive.read('word/document.xml')


@pytest.mark.parametrize('source,target', [('sl','it'), ('it','sl')])
def test_translation_keeps_exact_text_operand_and_explicit_language(rig, source, target):
    app, client, adapter, _ = rig
    headers = login(client)
    doc = upload(client, headers, 'Besedilo »A:B« 😀'.encode())
    response = start(client, headers, doc['document_id'], mode='translate',
                     source_language=source, target_language=target)
    assert response.status_code == 200
    finish(app)
    assert len(adapter.calls) == 1
    assert adapter.calls[0]['input_language'] == source
    assert adapter.calls[0]['question'].endswith('Besedilo »A:B« 😀')
    assert ('italijanščino' if target == 'it' else 'slovenščino') in adapter.calls[0]['question']


def test_cancel_does_not_start_remaining_sections_and_delete_removes_owned_text(rig):
    app, client, adapter, _ = rig
    headers = login(client)
    doc = upload(client, headers, ('A ' * 1700).encode())
    identifier = doc['document_id']
    assert start(client, headers, identifier).status_code == 200
    app.state.documents.advance()
    result = client.post('/api/pilot/documents/' + identifier + '/cancel', headers=headers,json={})
    assert result.status_code == 200
    app.state.documents.advance()
    assert app.state.jobs.claim() is None
    assert not adapter.calls
    assert result.json()['state'] == 'cancelled'
    assert client.post('/api/pilot/documents/' + identifier + '/delete', headers=headers,json={}).status_code == 200
    assert client.get('/api/pilot/documents/' + identifier).status_code == 404
    with app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM document_sections').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM families').fetchone()[0] == 0


def test_failed_section_is_not_a_success_and_next_sections_are_not_lost(rig):
    app, client, adapter, _ = rig
    headers = login(client)
    doc = upload(client, headers, ('A ' * 1700).encode())
    assert start(client, headers, doc['document_id']).status_code == 200
    def unavailable(*args):
        raise Failure('source_timeout', 503)
    adapter.generate = unavailable
    finish(app)
    final = client.get('/api/pilot/documents/' + doc['document_id']).json()
    assert final['state'] == 'failed'
    assert final['processed'] == final['total']
    assert all(s['state'] == 'failed' and s['error_code'] == 'source_timeout' for s in final['sections'])


def test_recovery_reuses_accepted_family_after_linking_interruption(rig):
    app, client, adapter, ids = rig
    headers = login(client)
    doc = upload(client, headers)
    identifier = doc['document_id']
    assert start(client, headers, identifier).status_code == 200
    doc = app.state.documents.get(ids['one'], identifier)
    accepted = app.state.domain.create_family(ids['one'], {**request_for(doc['sections'][0], doc['options']),
        'idempotency_key': 'document-' + identifier + '-0'},document_task=task_for(doc['sections'][0],doc['options']))
    restarted = Documents(app.state.store, app.state.domain)
    restarted.advance()
    with app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM families').fetchone()[0] == 1
        assert db.execute('SELECT family_id FROM document_sections').fetchone()[0] == accepted['family']['answer_family_id']


def test_account_deletion_cascades_documents(rig):
    app, client, _, ids = rig
    headers = login(client)
    upload(client, headers)
    app.state.store.delete_account(ids['one'])
    with app.state.store.connect() as db:
        assert db.execute('SELECT count(*) FROM uploaded_documents').fetchone()[0] == 0
        assert list(db.execute('PRAGMA foreign_key_check')) == []


@pytest.mark.parametrize('key,value',[('mode',[]),('source_language',{}),('target_language',[]),('locale',{})])
def test_invalid_json_option_types_are_rejected(rig,key,value):
    _,client,_,_=rig
    headers=login(client);doc=upload(client,headers)
    assert start(client,headers,doc['document_id'],**{key:value}).status_code==400


def test_delete_removes_document_descendants_without_breaking_parent_references(rig):
    app,client,_,_=rig;headers=login(client)
    doc=upload(client,headers);identifier=doc['document_id']
    assert start(client,headers,identifier).status_code==200
    finish(app)
    doc=client.get('/api/pilot/documents/'+identifier).json()
    family=client.get('/api/pilot/families/'+doc['sections'][0]['family_id']).json()
    child=client.post('/api/pilot/families',headers=headers,json={
        'question':'Pojasni še enkrat.','context':'Izrecni kontekst','locale':'sl',
        'parent_run_id':family['short']['run_id'],'parent_relation':'followup',
        'idempotency_key':'document-linked-child'})
    assert child.status_code==200,child.text
    assert client.post('/api/pilot/documents/'+identifier+'/delete',headers=headers,json={}).status_code==409
    assert client.get('/api/pilot/documents/'+identifier).status_code==200
    finish(app)
    assert client.post('/api/pilot/documents/'+identifier+'/delete',headers=headers,json={}).status_code==200
    assert client.get('/api/pilot/families/'+child.json()['family']['answer_family_id']).status_code==404
    with app.state.store.connect()as db:assert list(db.execute('PRAGMA foreign_key_check'))==[]


def test_uploaded_word_original_is_used_by_authenticated_draft_export(rig):
    from pilot_backend import document_files
    app,client,adapter,_=rig;headers=login(client)
    original=document_files._text_docx(['Gentili, chiedo un certificato.'])
    response=client.post('/api/pilot/documents/upload',content=original,headers={
        **headers,'Content-Type':'application/octet-stream','X-Document-Name':'lettera.docx',
        'X-Upload-Key':'word-export-source'})
    assert response.status_code==200;doc=response.json();assert doc['has_word_original']is True
    def freeze(family,policy):
        return {'bundle':{'task':family['request_options']['document_task']},'identity':{'synthetic':'test'}}
    def generate(bundle,tier,model,parent=None):
        task=bundle['task'];proposal='Spoštovani, prosim za potrdilo.'
        return {'status':'partial','answer_md':proposal,'short_answer':proposal,
            'document_result':{**{k:task[k]for k in ('mode','source_language','target_language','locale','source_sha256')},
                'proposed_text':proposal,'edits':[],'notes':[],'requires_human_review':True,'status':'draft'}}
    adapter.freeze=freeze;adapter.generate=generate
    assert start(client,headers,doc['document_id'],mode='translate',source_language='it',target_language='sl').status_code==200
    finish(app)
    response=client.get('/api/pilot/documents/'+doc['document_id']+'/draft/docx')
    assert response.status_code==200
    assert document_files.extract(response.content,'draft.docx')['blocks'][0]['text']=='Spoštovani, prosim za potrdilo.'
    with app.state.store.connect()as db:
        assert db.execute('SELECT package FROM document_originals').fetchone()[0]==original


def test_failed_suboperation_blocks_complete_word_but_explicit_partial_is_labelled(rig):
    from pilot_backend import document_files
    app,client,adapter,_=rig;headers=login(client)
    doc=upload(client,headers,b'Original text.')
    def freeze(family,policy):
        return {'bundle':{'task':family['request_options']['document_task']},'identity':{'test':'partial'}}
    def generate(bundle,tier,model,parent=None):
        task=bundle['task']
        return {'status':'partial','answer_support':'partial','answer_md':'Original text.',
            'limitations':[{'code':'document_transform_failed','scope':'answer'}],
            'document_result':{**{k:task[k]for k in ('mode','source_language','target_language','locale','source_sha256')},
                'proposed_text':'Original text.','edits':[],'notes':[],'requires_human_review':True,'status':'draft'}}
    adapter.freeze=freeze;adapter.generate=generate
    assert start(client,headers,doc['document_id']).status_code==200;finish(app)
    before=client.get('/api/pilot/documents/'+doc['document_id']).json()
    assert before['state']=='complete'and before['sections'][0]['answer_support']=='partial'
    assert client.get('/api/pilot/documents/'+doc['document_id']+'/draft/docx').status_code==409
    partial=client.get('/api/pilot/documents/'+doc['document_id']+'/draft/docx?partial=true')
    assert partial.status_code==200 and'klikai-partial-draft.docx'in partial.headers['content-disposition']
    blocks=document_files.extract(partial.content,'partial.docx')['blocks']
    assert blocks[0]['text']=='Delni osnutek jezikovnega pregleda'
    assert blocks[-1]['text']=='Original text.'
    assert client.get('/api/pilot/documents/'+doc['document_id']).json()==before
    report=client.get('/api/pilot/documents/'+doc['document_id']+'/export/txt').text
    assert ORIGIN+'/api/pilot/documents/'+doc['document_id']in report
    assert ORIGIN+'/api/pilot/families/'+before['sections'][0]['family_id']in report
