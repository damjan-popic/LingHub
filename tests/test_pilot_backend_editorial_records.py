"""Offline negative and capture regressions for explicitly enabled full records."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from pilot_backend import editorial
from pilot_backend.adapter import Gateway
from pilot_backend.api import create_app, config_checked
from pilot_backend.security import Failure
from test_pilot_backend_editorial import hit, app_for, login, submit, ORIGIN

SOURCE = 'jezikovna_posta'


def detail(rid='jezikovna_posta:fixture-1'):
    return {'contract': editorial.RECORD_CONTRACT, 'resource_id': rid,
            'source_slug': SOURCE, 'source_label': editorial.SOURCES[SOURCE], 'source_badge': 'POŠTA',
            'rights_mode': 'owned', 'title': 'Published title', 'question_original': 'Original question?',
            'answer_text': 'Original answer.\n\nOnly under condition A. <script>x</script> [1]',
            'article_text': None, 'additional_info': None, 'references': [], 'source_date': None,
            'url': None, 'content_status': 'complete_qa', 'complete_qa': True, 'missing_fields': [],
            'release_id': 'disposable-fixture-only', 'content_hash': 'a' * 64}


def discovered(records=None):
    records = records if records is not None else [detail()]
    hits = [dict(hit(SOURCE, r['resource_id'], snippet='Old bounded discovery text only.'), rights_mode='owned') for r in records]
    return editorial.freeze({'query': 'query', 'sources': [SOURCE], 'count': len(hits), 'hits': hits}, 'query', SOURCE, 'sl')['bundle']


class RecordGateway(Gateway):
    def __init__(self, records=None, enabled=True, failure=False):
        super().__init__('http://127.0.0.1:18320', 'synthetic-token-' * 4,
                         klikai_url='http://127.0.0.1:8000', editorial_full_records=enabled)
        self.records = records if records is not None else [detail()]
        self.calls, self.blocked, self.failure = [], False, failure

    def request(self, path, payload, base=None):
        assert not self.blocked and payload is None and base == self.klikai_url
        self.calls.append(path)
        if path.startswith('/api/search?'):
            hits = discovered(self.records)['hits']
            return {'query': 'query', 'sources': [SOURCE], 'count': len(hits), 'hits': deepcopy(hits)}
        assert '/api/editorial/records/jezikovna_posta/jezikovna_posta%3A' in path
        if self.failure:
            raise Failure('answer_unavailable', 503)
        from urllib.parse import unquote
        rid = unquote(path.rsplit('/', 1)[1])
        return deepcopy(next(r for r in self.records if r['resource_id'] == rid))

    def require_generation(self, models):
        raise AssertionError('No model calls authorized')


def test_full_records_use_normal_owned_frozen_flow(tmp_path):
    records = [detail(), dict(detail('jezikovna_posta:fixture-2'), question_original='Second question?', answer_text='Separate answer.')]
    adapter = RecordGateway(records)
    app = app_for(tmp_path, adapter)
    with TestClient(app, base_url=ORIGIN) as client:
        fid, short = submit(app, client, login(client), SOURCE)
        assert short['state'] == 'complete' and short['generation_status'] == 'not_generated'
        assert short['deterministic_result']['complete_qa_available'] is True
        assert short['deterministic_result']['records'] == records
        assert len(short['evidence']) == 2
        for i, record in enumerate(records):
            text = short['blocks'][i + 1]['text']
            assert record['question_original'] in text and record['answer_text'] in text
            assert records[1-i]['question_original'] not in text
            assert 'Old bounded discovery text' not in text
            eid = short['evidence'][i]['evidence_id']
            route = '/api/pilot/families/' + fid + '/sources/' + eid
            drawer = client.get(route).json()
            assert record['question_original'] in drawer['excerpt'] and record['answer_text'] in drawer['excerpt']
            assert drawer['bibliography']['source_date'] is None
            assert short['evidence'][i]['source_revision'] is None
        adapter.blocked = True
        adapter.records = []
        assert client.get(route).json() == drawer
        assert client.get('/api/pilot/families/' + fid).json()['short'] == short
        login(client, 'editorial-other')
        assert client.get(route).status_code == client.get('/api/pilot/families/' + fid).status_code == 404
    assert len(adapter.calls) == 3


@pytest.mark.parametrize('enabled,nohit,failure', [(False, False, False), (True, True, False), (True, False, True)])
def test_old_capability_no_hit_and_unavailable_never_fabricate_full_records(tmp_path, enabled, nohit, failure):
    adapter = RecordGateway([] if nohit else None, enabled, failure)
    app = app_for(tmp_path, adapter)
    with TestClient(app, base_url=ORIGIN) as client:
        fid, short = submit(app, client, login(client), SOURCE)
        if failure:
            assert short['state'] == 'failed' and short['error_code'] == 'answer_unavailable'
            assert client.get('/api/pilot/families/' + fid + '/sources/guessed').status_code == 404
        else:
            assert short['state'] == 'complete'
            assert short['deterministic_result']['complete_qa_available'] is False
            assert short['generation_status'] == 'not_generated'
            assert short['status'] == ('no_hit' if nohit else 'source_only')
    assert len(adapter.calls) == (2 if failure else 1)


@pytest.mark.parametrize('change', [
    {'source_slug': 'jezikovna_urgenca'}, {'resource_id': 'jezikovna_posta:other'},
    {'rights_mode': 'restricted'}, {'rights_mode': 'linked_only'}, {'rights_mode': 'unknown'},
    {'complete_qa': False}, {'question_original': None}, {'article_text': 'Different unrelated article'},
    {'source_date': 'yesterday'}, {'content_hash': 'invented'}, {'references': [{'private_path': '/internal'}]},
    {'url': 'javascript:alert(1)'}, {'references': [{'label': None, 'url': 'https://:secret@example.test'}]},
    {'question_original': ['invented']}, {'missing_fields': ['question_original']}, {'private_metadata': {}},
])
def test_inconsistent_or_denied_detail_fails_without_excerpt_downgrade(change):
    record = {**detail(), **change}
    with pytest.raises(Failure):
        editorial.freeze_records(discovered(), [record])


@pytest.mark.parametrize('article', [False, True])
def test_missing_original_question_never_becomes_a_title_based_pair(article):
    record = detail()
    record.update(question_original=None, complete_qa=False, missing_fields=['question_original'],
                  content_status='article' if article else 'incomplete')
    if article:
        record.update(article_text=record['answer_text'], answer_text=None)
    bundle = editorial.freeze_records(discovered(), [record])['bundle']
    answer = editorial.answer(bundle, 'short')
    assert answer['deterministic_result']['complete_qa_available'] is False
    assert 'Izvirni par vprašanja in odgovora ni popoln.' in answer['display_blocks'][1]['text']
    drawer = editorial.source(bundle, answer['evidence'][0]['evidence_id'], 'short')
    assert bundle['records'][0]['question_original'] is None
    assert drawer['title'] == 'Published title'
    assert 'Izvirno vprašanje:\nPublished title' not in drawer['excerpt']


def test_record_payload_and_aggregate_limits_are_explicit_not_truncated():
    record = dict(detail(), answer_text='x' * editorial.MAX_RECORD_BYTES)
    with pytest.raises(Failure) as error:
        editorial.freeze_records(discovered(), [record])
    assert error.value.code == 'editorial_record_too_large'
    records = [dict(detail('jezikovna_posta:record-' + str(i)), answer_text='x' * 31000) for i in range(3)]
    with pytest.raises(Failure) as error:
        editorial.freeze_records(discovered(records), records)
    assert error.value.code == 'editorial_record_too_large'


def test_full_capture_mutation_and_cross_source_identity_are_rejected():
    record = detail()
    bundle = editorial.freeze_records(discovered(), [record])['bundle']
    original = deepcopy(bundle)
    record['answer_text'] = 'external changed'
    assert bundle == original
    bundle['records'][0]['answer_text'] = 'tampered'
    with pytest.raises(Failure):
        editorial.answer(bundle, 'short')


def test_full_record_config_is_explicit_boolean_and_default_remains_off(tmp_path):
    config = {'store': str(tmp_path / 'private/pilot.sqlite'), 'allowed_origins': [ORIGIN],
              'secure_cookies': False, 'gateway_url': 'http://127.0.0.1:18320', 'gateway_token': 'synthetic-token-' * 4,
              'klikai_url': 'http://127.0.0.1:8000'}
    assert config_checked(config)['editorial_full_records'] is False
    for bad in ['true', 1, None]:
        with pytest.raises(ValueError):config_checked(dict(config, editorial_full_records=bad))
    app = create_app(dict(config, editorial_full_records=True), start_workers=False)
    assert app.state.jobs.adapter.editorial_full_records is True
