"""Reference CLI and private JSONL identity/resumption contracts; no real services."""
import json
import stat
from unittest.mock import Mock

import pytest

import linghub_client as module
from linghub_client import ClientError, LingHubClient, execute_record, process_jsonl


class ReferenceFixtureClient:
    def __init__(self):
        self.calls = []
        self.snapshot = 'fixture-snapshot-one'
        self.model_root = '/fixed/models/fixture-revision-one'
        self.retrieval_revision = 'fixture-reranker-revision-one'
        self.implementation = {'generation':'fixture-protocol-one'}
        self.allowed_sources = ['epravopis','jezikovna_svetovalnica']
        self.effect = None

    def request(self, path):
        if path == '/v1/capabilities': return {'identity': 'accepted-deterministic-fixture'}
        assert path == '/v1/references/health'
        return {'snapshot_id': self.snapshot, 'collection': {'contract_id': 'fixture-1024'},
                'implementation':self.implementation,'allowed_sources':self.allowed_sources,
                'retrieval': {'embedding': {'revision': 'fixture-embedding-one', 'dimension': 1024},
                              'reranker': {'revision': self.retrieval_revision}},
                'models': {'primary': {'data': [{'id': 'fixture-primary', 'root': self.model_root}]}}}

    def reference(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.effect: self.effect(text, kwargs)
        return {'status': 'ok', 'snapshot_id': self.snapshot, 'model': kwargs['model'],
                'answer': {'short_answer': 'Fixture result'},
                'citations': [{'passage_id': 'fixture:p1', 'source_revision': 'a' * 64}],
                'diagnostics': {'served_model': 'fixture-primary', 'generator_fingerprint': self.model_root}}


@pytest.fixture
def batch(tmp_path):
    records = [{'id': 'ref-one', 'task': 'reference', 'lang': 'sl', 'engine': 'default', 'text': 'Kako se sklanja Hugo?'},
               {'id': 'find-two', 'task': 'reference-search', 'lang': 'it', 'engine': 'default', 'text': 'Grafia Théophile'},
               {'id': 'ref-three', 'task': 'reference', 'lang': 'sl', 'engine': 'default', 'text': 'Letni prirastek?'}]
    path = tmp_path / 'input.jsonl'; output = tmp_path / 'output'
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    return ReferenceFixtureClient(), path, output, records


def rows(output):
    return [json.loads(line) for line in (output / 'results.jsonl').open()]


@pytest.mark.parametrize('task', ['reference', 'reference-search'])
def test_task_mapping_preserves_source_language_model_and_original_text(task):
    client = ReferenceFixtureClient()
    record = {'id': 'one', 'task': task, 'lang': 'it', 'engine': 'default',
              'text': 'Théophile\u2028Gautier', 'sources': ['epravopis'],
              'retrieval': 'lexical', 'model': 'challenger', 'limit': 2}
    execute_record(client, record)
    assert client.calls == [(record['text'], {'answer': task == 'reference', 'language': 'it',
        'sources': ['epravopis'], 'retrieval': 'lexical', 'model': 'challenger', 'limit': 2})]


@pytest.mark.parametrize('answer,route', [(True, 'answer'), (False, 'search')])
def test_reference_client_fixed_versioned_route(answer, route):
    client = LingHubClient(); client.request = Mock(return_value={'status': 'ok'})
    client.reference('Théophile', answer=answer, language='it', sources=['epravopis'], model='challenger')
    path, payload = client.request.call_args.args
    assert path == '/v1/references/' + route
    assert payload['question'] == 'Théophile' and payload['sources'] == ['epravopis']
    assert payload['answer_language'] == 'it' and payload['model'] == 'challenger'
    assert payload['retrieval']==('hybrid_rerank' if answer else 'lexical')


@pytest.mark.parametrize('task', ['reference', 'reference-search'])
def test_actual_cli_parsing_and_printed_citations(monkeypatch, capsys, task):
    client = ReferenceFixtureClient()
    monkeypatch.setattr(module, 'LingHubClient', lambda *a, **k: client)
    code = module.main([task, 'Kako se sklanja Hugo?', '--lang', 'it', '--model', 'challenger',
                        '--retrieval', 'lexical', '--source', 'jezikovna_svetovalnica', '--limit', '3'])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result['citations'][0]['passage_id'] == 'fixture:p1'
    assert client.calls[0][1]['answer'] is (task == 'reference')
    assert client.calls[0][1]['sources'] == ['jezikovna_svetovalnica']


def test_interrupted_reference_batch_resumes_ids_order_and_private_receipts(batch):
    client, path, output, records = batch
    def interrupt(text, options):
        if len(client.calls) == 2: raise KeyboardInterrupt()
    client.effect = interrupt
    with pytest.raises(KeyboardInterrupt): process_jsonl(client, path, output)
    manifest = json.loads((output / 'progress.json').read_text())
    assert manifest['status'] == 'interrupted' and list(manifest['completed']) == ['ref-one']
    client.effect = None
    result = process_jsonl(client, path, output, resume=True)
    assert result['reused'] == 1 and result['processed'] == 2
    assert [r['id'] for r in rows(output)] == [r['id'] for r in records]
    assert all(r['result']['citations'][0]['source_revision'] == 'a' * 64 for r in rows(output))
    assert process_jsonl(client, path, output, resume=True)['reused'] == 3
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for name in ['progress.json', 'results.jsonl']:
        assert stat.S_IMODE((output / name).stat().st_mode) == 0o600
    assert records[0]['text'] not in (output / 'progress.json').read_text()


@pytest.mark.parametrize('identity', ['snapshot', 'model_root', 'retrieval_revision','implementation','allowed_sources'])
def test_snapshot_or_model_pin_change_invalidates_all_cached_successes(batch, identity):
    client, path, output, records = batch
    first = process_jsonl(client, path, output)
    setattr(client, identity, 'fixture-new-identity')
    second = process_jsonl(client, path, output, resume=True)
    assert second['previous_identity_changed'] is True
    assert second['reused'] == 0 and second['processed'] == 3
    assert second['capability_identity'] != first['capability_identity']


@pytest.mark.parametrize('identity', ['snapshot', 'model_root', 'retrieval_revision'])
def test_mid_batch_identity_change_is_per_item_failure_then_resumable(batch, identity):
    client, path, output, records = batch
    def change(text, options):
        if len(client.calls) == 2: setattr(client, identity, 'fixture-new-identity')
    client.effect = change
    result = process_jsonl(client, path, output)
    assert result['failed_ids'] == ['find-two', 'ref-three']
    assert rows(output)[0]['status'] == 'ok'
    assert all(r['status'] == 'error' for r in rows(output)[1:])
    client.effect = None
    resumed = process_jsonl(client, path, output, resume=True)
    assert resumed['status'] == 'complete' and resumed['reused'] == 0


def test_failure_and_rejection_keep_stable_ids_and_successes_continue(batch):
    client, path, output, records = batch
    def fail(text, options):
        if text == records[1]['text']: raise ClientError('Reference source unavailable', 503)
    client.effect = fail
    first = process_jsonl(client, path, output)
    assert first['status'] == 'complete_with_errors' and first['failed_ids'] == ['find-two']
    assert rows(output)[1]['error']['http_status'] == 503
    client.effect = None
    second = process_jsonl(client, path, output, resume=True)
    assert second['status'] == 'complete' and second['reused'] == 2 and second['processed'] == 1


def test_generation_rejected_is_not_cached_as_a_success(batch):
    client, path, output, records = batch
    original = client.reference
    client.reference = lambda text, **kw: {'status': 'generation_rejected', 'answer': None, 'citations': []}
    first = process_jsonl(client, path, output)
    assert first['failed_ids'] == [r['id'] for r in records]
    assert all(r['result']['answer'] is None for r in rows(output))
    client.reference = original
    second = process_jsonl(client, path, output, resume=True)
    assert second['processed'] == 3 and second['reused'] == 0


def test_changed_question_only_recomputes_that_document(batch):
    client, path, output, records = batch
    process_jsonl(client, path, output)
    records[1]['text'] = 'Drugo vprašanje o Théophilu'
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records))
    result = process_jsonl(client, path, output, resume=True)
    assert result['previous_input_changed'] and result['reused'] == 2 and result['processed'] == 1


def test_malformed_upstream_object_is_per_item_error_not_whole_batch_abort(batch):
    client, path, output, records = batch
    original = client.reference
    client.reference = lambda text, **kw: [] if text == records[1]['text'] else original(text, **kw)
    result = process_jsonl(client, path, output)
    assert result['status'] == 'complete_with_errors' and result['failed_ids'] == ['find-two']
    assert [r['id'] for r in rows(output)] == [r['id'] for r in records]
