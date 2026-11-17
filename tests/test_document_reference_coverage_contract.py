"""Synthetic exact-function checks, without importing any application module."""
import ast
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


REPO = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()


def exact_functions(relative, names, namespace):
    """Compile only named function definitions; no module imports/initializers."""
    path = REPO / relative
    tree = ast.parse(path.read_text(), filename=str(path))
    definitions = [node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in definitions} != set(names):
        raise AssertionError('Named function boundary differs')
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(path), 'exec'), namespace)


class Clock:
    def __init__(self, values=(0.0,)):
        self.values = iter(values)
        self.last = 0.0

    def monotonic(self):
        self.last = next(self.values, self.last)
        return self.last


def source_row(record, score=1.0):
    capture = {'record_id': record, 'original_text': 'Synthetic complete source record',
        'source_revision': 'synthetic-revision', 'score': score}
    return {'source_slug': 'sp2001', 'external_id': record, 'capture_kind': 'resource_record',
        'capture': capture, 'capture_sha256': digest(capture), 'score': score,
        'context_eligible': False, 'source': {'rights_mode': 'restricted',
            'locator': {'record_id': record}, 'snippet': capture['original_text']}}


def observation(status='no_match', rows=None, search_ms=3.0, elapsed_ms=7.0):
    rows = [] if rows is None else rows
    return rows, {'family': 'sp2001', 'source_label': 'Synthetic dictionary',
        'status': status, 'returned_count': len(rows), 'limit': 10, 'has_more': False,
        'search_ms': search_ms, 'elapsed_ms': elapsed_ms, 'detail_ms': 0.0,
        'detail_requests': 0, 'query_variants': []}


class Collector:
    def __init__(self, outputs=()):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, gateway, family, definition, details=None, *, query_override=None,
                 result_limit=None):
        self.calls.append({'query': query_override, 'result_limit': result_limit})
        if self.outputs:
            rows, status = deepcopy(self.outputs.pop(0))
        else:
            rows, status = observation(search_ms=1.0, elapsed_ms=2.0)
        status['query'] = query_override
        status['query_variants'] = [query_override]
        if result_limit is not None:
            status['limit'] = result_limit
        return rows, status


class ImmediatePool:
    """No thread, source request, worker job or network operation is created."""
    def submit(self, function, *args, **kwargs):
        value = function(*args, **kwargs)
        return SimpleNamespace(result=lambda: value)


def fixture(outputs=(), clock=(0.0,)):
    collector = Collector(outputs)
    namespace = {'deepcopy': deepcopy, 'digest': digest, 're': re, 'math': math,
        'hashlib': hashlib, 'time': Clock(clock)}
    exact_functions('pilot_backend/unified.py', ['score', 'merge_source_matches',
        'expand_request_reference_rules'], namespace)
    unified = SimpleNamespace(NON_DOCUMENTARY={'linghub', 'external_tool',
        'ontology_v2_jsonl', 'xlsx_source_register'}, source_result_limit=lambda definition: 10,
        collect_source=collector, merge_source_matches=namespace['merge_source_matches'])
    namespace.update(unified=unified, collect_source=collector,
        MAX_SOURCE_LOOKUP_SECONDS=30, LOOKUP_WINDOW=500, LOOKUP_WORDS=12,
        LOOKUP_OVERLAP_WORDS=5, MAX_LOOKUP_WINDOWS=256, LITERAL_WINDOW_STEP=250,
        LOOKUP_WORD_RE=re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*", re.UNICODE))
    exact_functions('pilot_backend/document_transform.py', ['sha_text', 'lookup_windows',
        '_merge_document_matches', '_collect_document_source'], namespace)
    return namespace, collector


DEFINITION = {'slug': 'sp2001', 'display_name': 'Synthetic dictionary',
    'adapter_key': 'official_reference_jsonl', 'source_type': 'external'}
FAMILY = {'question': 'Synthetic document operand', 'locale': 'sl',
    'input_language': 'sl', 'context': ''}


def windows(count=1):
    return {'windows': [{'start': i * 5, 'end': i * 5 + 4, 'query': 'word' + str(i),
        'query_sha256': 'a' * 64} for i in range(count)]}


def collected(namespace, discovery, record_limit=10):
    return namespace['_collect_document_source'](object(), deepcopy(FAMILY),
        deepcopy(DEFINITION), None, deepcopy(discovery), record_limit=record_limit)


class DocumentReferenceCoverageContract(unittest.TestCase):
    def test_unqueryable_but_bounded_literal_input_has_zero_search_timing(self):
        namespace, collector = fixture()
        # A 501-codepoint token is below MAX_TEXT=3000 yet outside the declared
        # source query bound. The literal planner explicitly represents its gap.
        discovery = namespace['lookup_windows']('x' * 501)
        self.assertEqual(discovery['windows'], [])
        self.assertEqual(len(discovery['unqueried_ranges']), 1)
        self.assertFalse(discovery['complete_nonwhitespace_input'])
        rows, status = collected(namespace, discovery)
        self.assertEqual(collector.calls, [])
        self.assertEqual(rows, [])
        self.assertEqual(status['status'], 'unsupported')
        self.assertEqual(status['reason'], 'no_queryable_input')
        self.assertEqual(status['search_ms'], 0.0)
        self.assertEqual(status['elapsed_ms'], 0)
        self.assertEqual(status['windows'], [])

    def test_valid_zero_observation_status_survives_exact_heading_reference_expansion(self):
        namespace, collector = fixture()
        rows, status = collected(namespace, {'windows': []})
        family = {**deepcopy(FAMILY), 'request_plan': {'comma_check': {
            'explain': True, 'reference_queries': [
                {'source_slug': 'sp2001', 'query': 'Synthetic heading one'},
                {'source_slug': 'sp2001', 'query': 'Synthetic heading two'}]}}}
        coverage = [status]
        namespace['expand_request_reference_rules'](object(), family, [deepcopy(DEFINITION)],
            rows, coverage, None, ImmediatePool(), exact_heading_only=True)
        self.assertEqual(len(collector.calls), 2)
        self.assertEqual(status['search_ms'], 2.0)
        self.assertEqual(status['elapsed_ms'], 4.0)
        self.assertEqual(status['status'], 'unavailable')
        self.assertEqual(status['returned_count'], 0)
        self.assertEqual(len(status['reference_scope_lookups']), 2)
        self.assertEqual(rows, [])

    def test_zero_record_allocation_keeps_unqueried_reason_and_zero_actual_search(self):
        namespace, collector = fixture()
        discovery = windows(2)
        rows, status = collected(namespace, discovery, record_limit=0)
        self.assertEqual(collector.calls, [])
        self.assertEqual(rows, [])
        self.assertEqual(status['search_ms'], 0.0)
        self.assertEqual(status['reason'], 'document_source_record_limit')
        self.assertEqual(len(status['unqueried_windows']), 2)
        self.assertEqual(status['record_limit'], 0)
        self.assertEqual(status['status'], 'unsupported')

    def test_expired_discovery_budget_before_first_lookup_is_not_a_source_absence(self):
        namespace, collector = fixture(clock=(0.0, 31.0))
        rows, status = collected(namespace, windows(2))
        self.assertEqual(collector.calls, [])
        self.assertEqual(rows, [])
        self.assertEqual(status['search_ms'], 0.0)
        self.assertEqual(status['reason'], 'source_discovery_time_limit')
        self.assertEqual(status['status'], 'unsupported')
        self.assertEqual(len(status['unqueried_windows']), 2)

    def test_one_window_retains_actual_timing_and_complete_original_capture(self):
        original = source_row('synthetic-one', score=2.0)
        namespace, collector = fixture([observation('ok', [original], 3.5, 8.0)])
        rows, status = collected(namespace, windows(1))
        self.assertEqual(status['search_ms'], 3.5)
        self.assertEqual(status['elapsed_ms'], 8.0)
        self.assertEqual(status['status'], 'ok')
        self.assertEqual(rows[0]['capture'], original['capture'])
        self.assertEqual(rows[0]['source'], original['source'])
        self.assertFalse(rows[0]['context_eligible'])
        self.assertEqual(len(collector.calls), 1)
        self.assertEqual(collector.calls[0]['result_limit'], 10)

    def test_multiple_windows_sum_actual_searches_without_merging_distinct_source_records(self):
        first, second = source_row('synthetic-one'), source_row('synthetic-two')
        namespace, collector = fixture([observation('ok', [first], 3.0, 7.0),
            observation('ok', [second], 5.0, 11.0)])
        rows, status = collected(namespace, windows(2))
        self.assertEqual(status['search_ms'], 8.0)
        self.assertEqual(status['elapsed_ms'], 18.0)
        self.assertEqual(status['returned_count'], 2)
        self.assertEqual({r['external_id'] for r in rows}, {'synthetic-one', 'synthetic-two'})
        self.assertEqual([r['source'] for r in rows], [first['source'], second['source']])
        self.assertEqual([c['result_limit'] for c in collector.calls], [10, 9])
        self.assertEqual(len(status['windows']), 2)

    def test_multiple_no_match_windows_stay_no_match_with_all_search_time_recorded(self):
        namespace, _ = fixture([observation(search_ms=2.0), observation(search_ms=4.0)])
        rows, status = collected(namespace, windows(2))
        self.assertEqual(rows, [])
        self.assertEqual(status['status'], 'no_match')
        self.assertEqual(status['search_ms'], 6.0)
        self.assertEqual(len(status['windows']), 2)

    def test_timeout_after_a_success_preserves_partial_evidence_and_unqueried_windows(self):
        original = source_row('synthetic-preserved')
        namespace, collector = fixture([observation('ok', [original], 2.0),
            observation('timeout', [], 4.0)])
        rows, status = collected(namespace, windows(3))
        self.assertEqual(len(collector.calls), 2)
        self.assertEqual(status['status'], 'partial')
        self.assertEqual(status['search_ms'], 6.0)
        self.assertEqual(status['returned_count'], 1)
        self.assertEqual(rows[0]['capture'], original['capture'])
        self.assertEqual(status['unqueried_windows'][0]['reason'], 'source_unavailable_or_unsupported')

    def test_initial_outage_remains_unavailable_not_fabricated_no_match(self):
        namespace, collector = fixture([observation('unavailable', [], 1.5)])
        rows, status = collected(namespace, windows(2))
        self.assertEqual(rows, [])
        self.assertEqual(len(collector.calls), 1)
        self.assertEqual(status['status'], 'unavailable')
        self.assertEqual(status['search_ms'], 1.5)
        self.assertEqual(len(status['unqueried_windows']), 1)

    def test_missing_observed_search_timing_is_not_silently_defaulted_to_zero(self):
        rows, observed = observation()
        del observed['search_ms']
        namespace, _ = fixture([(rows, observed)])
        with self.assertRaises(KeyError):
            collected(namespace, windows(1))

    def test_non_numeric_observed_search_timing_is_not_silently_defaulted(self):
        namespace, _ = fixture([observation(search_ms=None)])
        with self.assertRaises(TypeError):
            collected(namespace, windows(1))

    def test_invalid_reference_query_does_not_collect_or_invent_a_missing_status(self):
        namespace, collector = fixture()
        family = {**deepcopy(FAMILY), 'request_plan': {'comma_check': {'explain': True,
            'reference_queries': [{'source_slug': 'not-enabled', 'query': 'Synthetic heading'}]}}}
        found, coverage = [], []
        namespace['expand_request_reference_rules'](object(), family, [deepcopy(DEFINITION)],
            found, coverage, None, ImmediatePool(), exact_heading_only=True)
        self.assertEqual(collector.calls, [])
        self.assertEqual(found, [])
        self.assertEqual(coverage, [])



class DocumentReferencePreservation(unittest.TestCase):
    def test_all_non_search_timing_output_is_identical_to_pre_edit_exact_functions(self):
        pins = {'empty': '4d38910956366e49dbf30639e0277c041d503882ae031f2aabc4522b422659e9', 'one': '373c0027d2efadd37093db2d4c9461133e792934b19b78dc871e80d17afd5722', 'multiple': '233fe8907cd599fffbb13260140ea411665be385fa5ceff2b1be72e00bd21a51', 'outage': '3094aa3bfeefb6cc2d5c0be2180db070bb27cb7f421909ea86826e5d71ecf423'}
        cases = {
            'empty': ([], {'windows': []}),
            'one': ([observation('ok', [source_row('synthetic-one', 2.0)], 3.5, 8.0)], windows(1)),
            'multiple': ([observation('ok', [source_row('synthetic-one')], 3.0, 7.0),
                observation('ok', [source_row('synthetic-two')], 5.0, 11.0)], windows(2)),
            'outage': ([observation('unavailable', [], 1.5)], windows(2)),
        }
        for name, (observed, discovery) in cases.items():
            with self.subTest(name=name):
                namespace, _ = fixture(observed)
                rows, status = collected(namespace, discovery)
                status.pop('search_ms', None)
                self.assertEqual(digest({'rows': rows, 'status': status}), pins[name])

    def test_isolated_execution_imports_no_application_module(self):
        import sys
        before = set(sys.modules)
        namespace, _ = fixture()
        collected(namespace, {'windows': []})
        added = set(sys.modules) - before
        self.assertFalse(any(name == 'pilot_backend' or name.startswith('pilot_backend.')
            or name == 'core' or name.startswith('core.') for name in added))


if __name__ == '__main__':
    unittest.main(verbosity=2)
