"""Disposable synchronization fixtures; no live sources or model inference."""
from collections import Counter
from copy import deepcopy
import json
import threading
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from pilot_backend import editorial, unified
from pilot_backend.security import Failure
from test_pilot_unified import QUERY, UnifiedGateway, full_record


FAMILY = {'question': QUERY, 'locale': 'sl'}


def definition(slug):
    return {'slug': slug, 'display_name': editorial.SOURCES[slug],
            'adapter_key': 'luka_jsonl_qa', 'rights_mode': 'restricted',
            'source_type': 'owned', 'is_active': True}


class DetailGateway(UnifiedGateway):
    def __init__(self, rows, action=None):
        super().__init__()
        self.rows = rows
        self.detail_calls = Counter()
        self.action = action or (lambda slug, rid: None)
        self.active = self.peak = 0

    def request(self, path, payload, base=None):
        if path == '/api/sources':
            return {'sources': [definition(slug) for slug in self.rows]}
        parsed = urlsplit(path)
        if parsed.path == '/api/search':
            params = parse_qs(parsed.query)
            slug = params['sources'][0]
            assert params['limit'] == ['3']
            hits = [{'source_slug': slug, 'resource_id': rid, 'title': 'Synthetic ' + rid,
                     'rights_mode': 'restricted', 'snippet': 'Synthetic bounded discovery ' + rid,
                     'format': 'qa', 'chunk_kind': 'structured_qa', 'chunk_index': 0,
                     'score': 3.0} for rid in self.rows[slug]]
            return {'query': QUERY, 'sources': [slug], 'count': len(hits), 'hits': hits}
        if parsed.path.startswith('/api/editorial/records/'):
            _, _, _, _, slug, encoded = parsed.path.split('/')
            rid = unquote(encoded)
            with self.lock:
                self.detail_calls[slug, rid] += 1
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                self.action(slug, rid)
                record = full_record()
                record.update(source_slug=slug, source_label=editorial.SOURCES[slug], resource_id=rid)
                record['access_policy']['source_slug'] = slug
                return record
            finally:
                with self.lock:
                    self.active -= 1
        return super().request(path, payload, base)


def test_independent_editorial_details_overlap_and_merge_in_discovery_order():
    barrier = threading.Barrier(3)
    finished_last = threading.Event()

    def action(slug, rid):
        barrier.wait(timeout=3)
        if rid == 'last':
            finished_last.set()
        else:
            assert finished_last.wait(timeout=3)

    gateway = DetailGateway({'jezikovna_posta': ['first', 'middle', 'last']}, action)
    matches, status = unified.collect_source(gateway, FAMILY, definition('jezikovna_posta'))
    assert status['status'] == 'ok'
    assert gateway.peak == 3
    assert [row['external_id'] for row in matches] == ['first', 'middle', 'last']
    assert [row['external_id'] for row in status['details']] == ['first', 'middle', 'last']
    assert all(row['capture_kind'] == 'editorial_record' for row in matches)
    assert status['detail_requests'] == 3 and status['detail_reused'] == 0
    assert status['elapsed_ms'] >= status['search_ms'] >= 0
    assert status['elapsed_ms'] >= status['detail_ms'] > 0
    assert all(row['elapsed_ms'] > 0 and row['status'] == 'ok' for row in status['details'])


def test_shared_detail_pool_is_bounded_across_both_editorial_sources(monkeypatch):
    first_wave = threading.Barrier(unified.DETAIL_WORKERS)
    counter = 0
    lock = threading.Lock()

    def action(slug, rid):
        nonlocal counter
        with lock:
            counter += 1
            ordinal = counter
        if ordinal <= unified.DETAIL_WORKERS:
            first_wave.wait(timeout=3)

    gateway = DetailGateway({slug: ['c', 'b', 'a'] for slug in editorial.SOURCES}, action)
    monkeypatch.setattr(unified, 'collect_tools', lambda *args: (None, None))
    frozen = unified.freeze(gateway, FAMILY, {})
    bundle = frozen['bundle']
    assert gateway.peak == unified.DETAIL_WORKERS == 4
    assert len(gateway.detail_calls) == 6 and all(count == 1 for count in gateway.detail_calls.values())
    rows = [row for row in bundle['matches'] if row['source_slug'] in editorial.SOURCES]
    assert [(row['source_slug'], row['external_id']) for row in rows] == sorted(
        (slug, rid) for slug in editorial.SOURCES for rid in ['a', 'b', 'c'])
    assert bundle['retrieval']['status'] == 'complete'
    for key in ('registry_ms', 'source_collection_ms', 'evidence_preparation_ms', 'gateway_freeze_ms'):
        assert frozen['timings'][key] == bundle['retrieval']['timings'][key] >= 0
    assert frozen['timings']['retrieval_and_freeze_ms'] >= sum(bundle['retrieval']['timings'].values()) - .01
    # Timing does not replace returned-match pagination or add a corpus-total claim.
    first = unified.matches_page(bundle, set(), limit=2)
    assert first['returned_count'] == 7 and first['next_offset'] == 2 and first['corpus_total'] is None


def test_duplicate_detail_identity_is_reused_only_within_one_capture(monkeypatch):
    gateway = DetailGateway({'jezikovna_posta': ['same', 'same', 'other']})
    monkeypatch.setattr(unified, 'collect_tools', lambda *args: (None, None))
    first = unified.freeze(gateway, FAMILY, {})['bundle']
    coverage = next(row for row in first['retrieval']['coverage'] if row['family'] == 'jezikovna_posta')
    assert coverage['returned_count'] == 3 and coverage['detail_requests'] == 2 and coverage['detail_reused'] == 1
    assert len([row for row in first['matches'] if row['source_slug'] == 'jezikovna_posta']) == 2
    assert gateway.detail_calls == {('jezikovna_posta', 'same'): 1, ('jezikovna_posta', 'other'): 1}
    unified.freeze(gateway, FAMILY, {})
    assert gateway.detail_calls == {('jezikovna_posta', 'same'): 2, ('jezikovna_posta', 'other'): 2}


def test_duplicate_failed_detail_preserves_distinct_discovery_context(monkeypatch):
    class DiscoveryGateway(DetailGateway):
        def request(self, path, payload, base=None):
            value = super().request(path, payload, base)
            if path.startswith('/api/search?'):
                for i, hit in enumerate(value['hits']):
                    hit.update(snippet='Distinct discovery qualification ' + str(i), chunk_index=i)
            return value

    def unavailable(slug, rid):
        raise Failure('source_timeout', 503)

    gateway = DiscoveryGateway({'jezikovna_posta': ['same', 'same']}, unavailable)
    monkeypatch.setattr(unified, 'collect_tools', lambda *args: (None, None))
    bundle = unified.freeze(gateway, FAMILY, {})['bundle']
    matches = [row for row in bundle['matches'] if row['source_slug'] == 'jezikovna_posta']
    assert gateway.detail_calls == {('jezikovna_posta', 'same'): 1}
    assert len(matches) == 2 and len({row['capture_sha256'] for row in matches}) == 2
    assert {row['snippet'] for row in matches} == {'Distinct discovery qualification 0', 'Distinct discovery qualification 1'}
    assert all(row['context_eligible'] is False and row['detail_status'] == 'timeout' for row in matches)
    assert bundle['retrieval']['status'] == 'partial'
    for row in matches:
        assert row['snippet'] in unified.source(bundle, row['evidence_id'])['excerpt']


def test_detail_failures_preserve_full_sibling_and_ineligible_discovery_without_private_errors():
    def action(slug, rid):
        if rid == 'timeout':
            raise Failure('source_timeout', 503)
        if rid == 'invalid':
            raise ValueError('PRIVATE source payload and token must never appear')

    gateway = DetailGateway({'jezikovna_posta': ['timeout', 'invalid', 'good']}, action)
    matches, status = unified.collect_source(gateway, FAMILY, definition('jezikovna_posta'))
    assert status['status'] == 'partial' and status['returned_count'] == 3
    assert [row['detail_status'] for row in matches[:2]] == ['timeout', 'unavailable']
    assert all(row['context_eligible'] is False and row['capture_kind'] == 'resource_record' for row in matches[:2])
    assert matches[2]['capture_kind'] == 'editorial_record'
    assert [row['status'] for row in status['details']] == ['timeout', 'unavailable', 'ok']
    assert [row['error_code'] for row in status['details'][:2]] == ['source_timeout', 'invalid_gateway_response']
    assert 'PRIVATE' not in json.dumps([matches, status])


@pytest.mark.parametrize('bad_field,bad_value', [('source_slug', 'jezikovna_urgenca'), ('resource_id', 'wrong-id'),
                                               ('access_policy', None), ('answer_text', 'x' * 65536)])
def test_parallel_details_retain_source_identity_policy_and_record_bounds(bad_field, bad_value):
    class InvalidGateway(DetailGateway):
        def request(self, path, payload, base=None):
            result = super().request(path, payload, base)
            if path.endswith('/bad'):
                result[bad_field] = deepcopy(bad_value)
            return result

    gateway = InvalidGateway({'jezikovna_posta': ['bad', 'good']})
    matches, status = unified.collect_source(gateway, FAMILY, definition('jezikovna_posta'))
    assert status['status'] == 'partial'
    assert matches[0]['capture_kind'] == 'resource_record' and matches[0]['context_eligible'] is False
    assert matches[1]['capture_kind'] == 'editorial_record'
    assert status['detail_requests'] == 2


def test_disabled_full_records_make_no_detail_requests():
    gateway = DetailGateway({'jezikovna_posta': ['one', 'two', 'three']})
    gateway.editorial_full_records = False
    matches, status = unified.collect_source(gateway, FAMILY, definition('jezikovna_posta'))
    assert not gateway.detail_calls and status['detail_requests'] == 0 and status['detail_ms'] == 0
    assert status['status'] == 'partial' and len(matches) == 3
    assert all(row['context_eligible'] is False for row in matches)


def test_parallel_details_use_actual_bounded_authenticated_http_adapter():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pilot_backend.adapter import Gateway

    fixture = DetailGateway({'jezikovna_posta': ['one', 'two', 'three']})
    barrier = threading.Barrier(3)
    private_fixture_token = 'synthetic-local-detail-token-' * 2
    failures = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                if self.path.startswith('/api/editorial/records/'):
                    assert self.headers.get('X-Pilot-Internal-Token') == private_fixture_token
                    barrier.wait(timeout=3)
                value = fixture.request(self.path, None)
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as error:
                failures.append(type(error).__name__)
                self.send_error(500)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = 'http://127.0.0.1:' + str(server.server_port)
        gateway = Gateway(origin, private_fixture_token, klikai_url=origin, editorial_full_records=True,
                          editorial_operator_policy_id=editorial.OPERATOR_POLICY_ID)
        matches, status = unified.collect_source(gateway, FAMILY, definition('jezikovna_posta'))
        assert failures == []
        assert status['status'] == 'ok' and len(matches) == 3
        assert all(row['capture_kind'] == 'editorial_record' for row in matches)
        assert status['detail_requests'] == 3
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
