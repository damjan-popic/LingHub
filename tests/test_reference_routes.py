"""Isolated HTTP contract checks for the additive loopback reference proxy."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

import reference_routes


@pytest.fixture
def proxy(monkeypatch):
    state = {'requests': [], 'options': [], 'response': {'status': 'ok', 'passages': [], 'snapshot_id': 'fixture'}}
    def handler(request):
        state['requests'].append(request)
        result = state['response']
        if callable(result): return result(request)
        return httpx.Response(200, json=result)
    real_client = httpx.AsyncClient
    def factory(**kwargs):
        state['options'].append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(reference_routes.httpx, 'AsyncClient', factory)
    monkeypatch.setenv('ZRC_REFERENCE_URL', 'http://127.0.0.1:18220')
    monkeypatch.setenv('KLIKAI_REFERENCE_URL', 'http://127.0.0.1:18000')
    app = FastAPI(); app.include_router(reference_routes.router)
    state['client'] = TestClient(app, raise_server_exceptions=False)
    return state


def test_search_and_answer_use_distinct_fixed_upstream_routes(proxy):
    payload = {'question': 'Kako se sklanja Hugo?', 'sources': ['jezikovna_svetovalnica'],
               'retrieval': 'lexical', 'model': 'challenger', 'answer_language': 'it', 'limit': 2}
    for operation, port, path in [('search', 18220, '/v1/references/search'),
                                  ('answer', 18000, '/api/references/answer')]:
        response = proxy['client'].post('/v1/references/' + operation, json=payload)
        assert response.status_code == 200
        request = proxy['requests'][-1]
        assert request.url.host == '127.0.0.1' and request.url.port == port and request.url.path == path
        assert json.loads(request.content) == payload
    assert response.json()['via'] == 'LingHub → KLIKai → local reference gateway'
    assert all(x['trust_env'] is False and x['follow_redirects'] is False for x in proxy['options'])


def test_health_and_passage_id_are_fixed_route_and_encoded_parameter(proxy):
    assert proxy['client'].get('/v1/references/health').status_code == 200
    identifier = 'epravopis:page:11:?url=http://other.invalid/#x'
    response = proxy['client'].get('/v1/references/passage', params={'id': identifier})
    assert response.status_code == 200
    request = proxy['requests'][-1]
    assert request.url.path == '/v1/references/passage' and request.url.host == '127.0.0.1'
    assert request.url.params['id'] == identifier


@pytest.mark.parametrize('change', [
    {'question': ''}, {'question': 'x' * 5001}, {'sources': []}, {'sources': ['unknown']},
    {'sources': ['epravopis'] * 3}, {'model': 'remote'}, {'retrieval': 'unbounded'},
    {'answer_language': 'en'}, {'limit': 11}, {'url': 'https://attacker.invalid'},
])
def test_invalid_request_never_reaches_upstream(proxy, change):
    response = proxy['client'].post('/v1/references/search', json={'question': 'test'} | change)
    assert response.status_code == 422 and not proxy['requests']


@pytest.mark.parametrize('origin', [
    'https://127.0.0.1:8020', 'http://example.invalid:8020', 'http://localhost:8020',
    'http://127.0.0.1:8020/path', 'http://user:pass@127.0.0.1:8020',
    'http://127.0.0.1:8020?target=http://attacker.invalid', 'http://127.0.0.1',
    'http://127.0.0.1:invalid',
])
def test_invalid_origin_is_unavailable_without_transport(proxy, monkeypatch, origin):
    monkeypatch.setenv('ZRC_REFERENCE_URL', origin)
    response = proxy['client'].post('/v1/references/search', json={'question': 'test'})
    assert response.status_code == 503 and not proxy['requests']


@pytest.mark.parametrize('failure', ['json', 'oversize', 'offline', 'array', 'missing_status'])
def test_bad_upstream_never_becomes_success_or_fake_answer(proxy, failure):
    def handler(request):
        if failure == 'offline': raise httpx.ConnectError('PRIVATE_PATH', request=request)
        if failure == 'array': return httpx.Response(200, json=[])
        if failure == 'missing_status': return httpx.Response(200, json={})
        return httpx.Response(200, content=b'[' if failure == 'json' else b'x' * (2 * 1024 * 1024 + 1))
    proxy['response'] = handler
    response = proxy['client'].post('/v1/references/answer', json={'question': 'test'})
    assert response.status_code == 503
    assert 'PRIVATE_PATH' not in response.text
    assert len(proxy['requests']) == 1


def test_redirect_does_not_contact_external_location(proxy):
    proxy['response'] = lambda request: httpx.Response(302, headers={'location': 'https://example.invalid'})
    response = proxy['client'].post('/v1/references/search', json={'question': 'test'})
    assert response.status_code != 200 and len(proxy['requests']) == 1
    assert 'location' not in response.headers


@pytest.mark.parametrize('status', ['model_unavailable', 'generation_rejected', 'no_hit'])
def test_upstream_source_and_generation_failure_status_is_preserved(proxy, status):
    proxy['response'] = {'status': status, 'answer': None, 'citations': [], 'passages': [],
                         'degraded': ['fixture_reason'], 'snapshot_id': 'fixture'}
    response = proxy['client'].post('/v1/references/answer', json={'question': 'test'})
    assert response.status_code == 200
    assert response.json()['status'] == status and response.json()['answer'] is None
