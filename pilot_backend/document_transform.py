"""Typed document transformation with frozen existing documentary evidence.

Uploaded prose is a task operand. It never enters the question interpreter and
is never admitted to the source corpus. Every returned rewrite remains a draft.
"""
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import re
import time
import threading

from . import unified, comma_tasks, terminology, morphology, document_surface
from .security import Failure
from .store import canonical, digest

CONTRACT = 'klikai.document.v1'
RESPONSE_CONTRACT = 'klikai.document-transform.v1'
MAX_TEXT = 3000
LOOKUP_WINDOW = 500
LOOKUP_WORDS = 12
LOOKUP_OVERLAP_WORDS = 5
MAX_LOOKUP_WINDOWS = 256
MAX_SOURCE_LOOKUP_SECONDS = 30
LITERAL_WINDOW_STEP = 250
# Existing terminology adapter's lexical token boundary. Twelve tokens have
# 57 spans of lengths1..6, within its native morphology cap64 and literal cap18.
LOOKUP_WORD_RE = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*", re.UNICODE)
TASK_FIELDS = {'mode', 'source_language', 'target_language', 'locale',
               'source_text', 'source_sha256'}
# The same existing SP2001 section headings used by ordinary reference discovery
# (CORE pilot_comma_request and WEB test_comma_reference_discovery). These are
# bounded discovery keys, never an assertion that a rule applies to the text.
REVIEW_REFERENCE_HEADINGS = ('Med nadrednim in odvisnim stavkom',
                            'Nestavčno-stavčni enakovredni deli')


def invalid():
    raise Failure('invalid_document_result', 502)


def sha_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def task_checked(value):
    if not isinstance(value, dict) or set(value) - {'inline_spans', 'translation_context', 'review_scope','translation_profile','translation_request_id','document_binding'} != TASK_FIELDS:
        raise Failure('invalid_request')
    mode, source, target, locale = (value[k] for k in
        ('mode', 'source_language', 'target_language', 'locale'))
    text = value['source_text']
    if (not all(isinstance(v, str) for v in (mode, source, locale))
            or mode not in {'review', 'translate'} or source not in {'sl', 'it'}
            or locale not in {'sl', 'it'} or not isinstance(text, str)
            or not 1 <= len(text) <= MAX_TEXT or '\0' in text
            or mode == 'review' and target is not None
            or mode == 'translate' and (not isinstance(target, str)
                or target not in {'sl', 'it'} or target == source)):
        raise Failure('invalid_request')
    try:
        actual = sha_text(text)
    except UnicodeError:
        raise Failure('invalid_request') from None
    if actual != value['source_sha256']:
        raise Failure('invalid_request')
    if 'document_binding' in value:
        binding=value['document_binding']
        if (not isinstance(binding,dict)or set(binding)!={'document_id','section_index'}
                or not isinstance(binding['document_id'],str)
                or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',binding['document_id'])
                or type(binding['section_index'])is not int or not 0<=binding['section_index']<=99):
            raise Failure('invalid_request')
    if 'review_scope' in value and (mode != 'review' or value['review_scope'] != 'non_comma'):
        raise Failure('invalid_request')
    spans = value.get('inline_spans', [])
    if (not isinstance(spans, list) or len(spans) > 16
            or spans and mode != 'translate'):
        raise Failure('invalid_request')
    seen, previous = set(), -1
    for span in spans:
        if (not isinstance(span, dict) or set(span) != {'id', 'kind', 'start', 'end', 'text'}
                or not isinstance(span['id'], str) or not 1 <= len(span['id']) <= 120
                or span['id'] in seen or span['kind'] not in ('hyperlink', 'emphasis')
                or type(span['start']) is not int or type(span['end']) is not int
                or not 0 <= span['start'] < span['end'] <= len(text)
                or span['start'] < previous or text[span['start']:span['end']] != span['text']):
            raise Failure('invalid_request')
        seen.add(span['id'])
        previous = span['end']
    context = value.get('translation_context', [])
    if (not isinstance(context, list) or len(context) > 2
            or 'translation_context' in value and mode != 'translate'):
        raise Failure('invalid_request')
    previous, total = -1, 0
    for pair in context:
        if (not isinstance(pair, dict) or set(pair) != {'section_index', 'source_text',
                'source_sha256', 'translated_text', 'translated_sha256'}
                or type(pair['section_index']) is not int or not previous < pair['section_index'] <= 99
                or not isinstance(pair['source_text'], str) or not 1 <= len(pair['source_text']) <= MAX_TEXT
                or not isinstance(pair['translated_text'], str) or not 1 <= len(pair['translated_text']) <= 12000
                or '\0' in pair['source_text'] or '\0' in pair['translated_text']):
            raise Failure('invalid_request')
        try:
            if (sha_text(pair['source_text']) != pair['source_sha256']
                    or sha_text(pair['translated_text']) != pair['translated_sha256']):
                raise Failure('invalid_request')
        except UnicodeError:
            raise Failure('invalid_request') from None
        total += len(pair['source_text']) + len(pair['translated_text'])
        previous = pair['section_index']
    if total > 6000:
        raise Failure('invalid_request')
    from .private_translation import task_profile
    task_profile(value)
    return deepcopy(value)


def lookup_query(text):
    """Use one bounded literal snippet, preserving adjacent source terms.

    This is a discovery window, not an interpretation or complete retrieval of
    the section. Keeping the original order and repetition lets the existing
    source adapters find multiword expressions instead of scattered unigrams.
    """
    text = text.lstrip()
    result = text[:500]
    if len(text) > 500 and not text[500].isspace():
        # End at a complete word when possible; an overlong unbroken token is
        # still bounded to the existing adapter's 500-codepoint query limit.
        boundary = max((i for i, char in enumerate(result) if char.isspace()), default=-1)
        if boundary > 0:
            result = result[:boundary]
    return result.rstrip()


def lookup_windows(text):
    """Bounded literal queries with global offsets, not question interpretation.

    Whole whitespace-delimited tokens prevent a cut word becoming an exact
    lexical hit. Overlap retains multiword expressions across normal window
    boundaries. Unqueryable tokens and a work-limit tail remain explicit.
    """
    tokens = list(re.finditer(r'\S+', text))
    word_counts = [len(LOOKUP_WORD_RE.findall(token.group())) for token in tokens]
    windows, gaps, index = [], [], 0
    while index < len(tokens):
        token = tokens[index]
        if token.end() - token.start() > LOOKUP_WINDOW or word_counts[index] > LOOKUP_WORDS:
            reason = ('token_exceeds_source_query_bound' if token.end() - token.start() > LOOKUP_WINDOW
                      else 'token_exceeds_source_word_bound')
            gaps.append({'start': token.start(), 'end': token.end(), 'reason': reason})
            index += 1
            continue
        if len(windows) == MAX_LOOKUP_WINDOWS:
            gaps.append({'start': token.start(), 'end': len(text), 'reason': 'document_lookup_work_limit'})
            break
        end_index = index
        words = word_counts[index]
        while (end_index + 1 < len(tokens) and tokens[end_index + 1].end() - token.start() <= LOOKUP_WINDOW
                and words + word_counts[end_index + 1] <= LOOKUP_WORDS):
            end_index += 1
            words += word_counts[end_index]
        a, b = token.start(), tokens[end_index].end()
        windows.append({'start': a, 'end': b, 'query': text[a:b], 'query_sha256': sha_text(text[a:b]),
                        'lexical_words': words, 'query_kind': 'lexical'})
        if end_index == len(tokens) - 1:
            break
        next_index, retained = end_index, word_counts[end_index]
        while next_index > index and retained < LOOKUP_OVERLAP_WORDS:
            next_index -= 1
            retained += word_counts[next_index]
        index = max(index + 1, next_index)
    lexical_gaps = deepcopy(gaps)
    literal_windows, index = [], 0
    while index < len(tokens) and len(literal_windows) + len(windows) < MAX_LOOKUP_WINDOWS:
        token = tokens[index]
        if token.end()-token.start() > LOOKUP_WINDOW:
            index += 1
            continue
        end_index = index
        while end_index+1 < len(tokens) and tokens[end_index+1].end()-token.start() <= LOOKUP_WINDOW:
            end_index += 1
        a, b = token.start(), tokens[end_index].end()
        literal_windows.append({'start':a,'end':b,'query':text[a:b], 'query_sha256':sha_text(text[a:b]),
            'lexical_words':sum(word_counts[index:end_index+1]),'query_kind':'literal'})
        if end_index == len(tokens)-1:
            break
        next_index=index
        while next_index+1 <= end_index and tokens[next_index+1].start() <= a+LITERAL_WINDOW_STEP:
            next_index += 1
        index=max(index+1,next_index)
    combined={}
    for window in literal_windows+windows:
        key=(window['start'],window['end'])
        if key in combined:
            combined[key]['query_kind']='both'
        else:
            combined[key]=window
    windows=list(combined.values())
    gaps=[gap for gap in gaps if not any(window['start']<=gap['start'] and gap['end']<=window['end'] for window in windows)]
    return {'version': 'document-literal-windows.v2', 'source_sha256': sha_text(text),
            'source_codepoints': len(text), 'offset_convention': 'unicode_codepoint_half_open',
            'windows': windows, 'unqueried_ranges': gaps, 'complete_nonwhitespace_input': not gaps,
            'lexical_unqueried_ranges':lexical_gaps,
            'window_limit': LOOKUP_WINDOW, 'word_limit': LOOKUP_WORDS,
            'overlap_words': LOOKUP_OVERLAP_WORDS, 'source_morphology_phrase_words': 6,
            'literal_window_step_codepoints':LITERAL_WINDOW_STEP,
            'guaranteed_literal_expression_span_codepoints':LITERAL_WINDOW_STEP,
            'work_limit': MAX_LOOKUP_WINDOWS,
            'scope': 'query_plan_coverage_only_source_execution_recorded_separately_not_applicability'}


class _DocumentSourceCache:
    """Reuse only identical source GETs during one freeze, including failures."""
    def __init__(self, gateway):
        self.gateway, self.lock, self.requests = gateway, threading.Lock(), {}

    def __getattr__(self, name):
        return getattr(self.gateway, name)

    def request(self, path, payload, base=None):
        if payload is not None:
            raise Failure('invalid_request')
        key = (base, path)
        with self.lock:
            owner = key not in self.requests
            future = self.requests.setdefault(key, Future())
        if owner:
            try:
                future.set_result(self.gateway.request(path, None, base))
            except BaseException as error:
                future.set_exception(error)
        return deepcopy(future.result())


def _merge_document_matches(matches):
    """One complete parent per unchanged record body; keep every lookup capture.

    Query scores and nominated forms are discovery metadata. Different source
    content/revisions, senses and qualifications never merge. The original
    lookup captures remain in the reader, separate from the aggregate parent.
    """
    grouped = {}
    for row in matches:
        capture = row['capture']
        ignored = {'score'} | ({'matched_forms', 'accepted_matched_forms'}
                              if row['capture_kind'] == 'terminology_record' else set())
        body = {k: v for k, v in capture.items() if k not in ignored}
        key = (row['source_slug'], row['capture_kind'], row['external_id'], digest(body))
        if key not in grouped:
            grouped[key] = deepcopy(row)
            grouped[key]['discovery_captures'] = []
        current = grouped[key]
        original = {'capture_sha256': row['capture_sha256'], 'capture': deepcopy(capture),
                    'windows': deepcopy(row.get('discovery_windows', []))}
        previous = next((item for item in current['discovery_captures']
            if item['capture_sha256'] == original['capture_sha256'] and item['capture'] == original['capture']), None)
        if previous is None:
            current['discovery_captures'].append(original)
        else:
            previous['windows'].extend(window for window in original['windows'] if window not in previous['windows'])
        current['context_eligible'] = current.get('context_eligible', True) and row.get('context_eligible', True)
        current['score'] = max(current['score'], row['score'])
        if 'score' in current['capture']:
            current['capture']['score'] = current['score']
        for field in ignored - {'score'}:
            for nomination in capture.get(field, []):
                if nomination not in current['capture'].setdefault(field, []):
                    current['capture'][field].append(deepcopy(nomination))
        current['capture_sha256'] = digest(current['capture'])
    return unified.merge_source_matches(list(grouped.values()))


def _collect_document_source(gateway, family, definition, details, discovery):
    found, observations = [], []
    started = time.monotonic()
    unqueried = []
    for index, window in enumerate(discovery['windows']):
        if time.monotonic() - started >= MAX_SOURCE_LOOKUP_SECONDS:
            unqueried = [{**item, 'reason': 'source_discovery_time_limit'} for item in discovery['windows'][index:]]
            break
        rows, observed = unified.collect_source(gateway, family, definition, details, query_override=window['query'])
        observed = {k: v for k, v in observed.items() if k not in {'original_question', 'request_plan'}}
        observed['input_span'] = {'start': window['start'], 'end': window['end'], 'query_sha256': window['query_sha256']}
        observed['possible_result_limit'] = (observed.get('has_more') is True or
            observed.get('has_more') is not False and type(observed.get('limit')) is int
            and observed['limit'] > 0 and observed.get('returned_count', 0) >= observed['limit'])
        for row in rows:
            row['discovery_windows'] = [deepcopy(observed['input_span'])]
        found.extend(rows); observations.append(observed)
        if observed['status'] == 'not_documentary':
            break
        if observed['status'] in {'timeout', 'unavailable', 'unsupported'}:
            unqueried = [{**item, 'reason': 'source_unavailable_or_unsupported'} for item in discovery['windows'][index + 1:]]
            break
    merged = _merge_document_matches(found)
    first = observations[0] if observations else {'family': definition['slug'], 'source_label': definition['display_name'],
        'status': 'unsupported', 'reason': unqueried[0]['reason'] if unqueried else 'no_queryable_input', 'limit': 0}
    result = deepcopy(first)
    statuses = {item['status'] for item in observations}
    limited = any(item['possible_result_limit'] for item in observations)
    if statuses == {'not_documentary'}:
        result['status'] = 'not_documentary'
    elif unqueried or limited or statuses & {'partial', 'unavailable', 'unsupported', 'timeout'}:
        result['status'] = 'partial' if merged or statuses & {'ok', 'no_match'} else first['status']
    else:
        result['status'] = 'ok' if merged else 'no_match' if observations else 'unsupported'
    result.update(returned_count=len(merged), raw_returned_count=sum(item.get('returned_count', 0) for item in observations),
        limit=sum(item.get('limit') or 0 for item in observations), per_query_limit=first.get('limit'),
        has_more=True if limited else None, possible_result_limit=limited, windows=observations,
        unqueried_windows=unqueried, processing_limit_seconds=MAX_SOURCE_LOOKUP_SECONDS,
        query_variants=[item['query'] for item in observations],
        elapsed_ms=sum(item.get('elapsed_ms', 0) for item in observations),
        detail_requests=sum(item.get('detail_requests', 0) for item in observations))
    return merged, result


def _surface_occurrences(surface, text):
    yield from document_surface.occurrences(surface,text)


def _freeze_document_lexical_context(gateway, task, matches):
    """Reuse the accepted frozen morphology API only for discovered unigrams.

    Recorded POS candidates are not current-sentence analyses or source advice.
    Every token keeps its original spans; all failures and bounds remain visible.
    """
    inputs = {}
    for row in matches:
        if (row['capture_kind'] != 'terminology_record' or row.get('context_eligible') is False
                or row.get('rights_mode') in {'linked_only', 'metadata_only', 'unknown'}):
            continue
        for surface in _native_term_surfaces(row['capture'], task['source_language']):
            if not isinstance(surface, str) or len(LOOKUP_WORD_RE.findall(surface)) != 1:
                continue
            for occurrence in _surface_occurrences(surface, task['source_text']):
                spans = inputs.setdefault(occurrence['text'], [])
                span = {key: occurrence[key] for key in ('start', 'end')}
                if span not in spans:
                    spans.append(span)
    if not inputs:
        return None
    requested = [{'text': text, 'spans': spans} for text, spans in sorted(inputs.items(), key=lambda pair:min(s['start'] for s in pair[1]))]
    row = {'family': 'document_lexical_context', 'contract': 'document-lexical-context.v1',
        'source_sha256': task['source_sha256'], 'language': task['source_language'],
        'requested_inputs': requested, 'captures': [], 'unqueried_inputs': [],
        'status': 'unavailable', 'returned_count': 0, 'limit': 256, 'has_more': None,
        'scope': 'recorded_lexical_candidates_not_contextual_analysis_or_source_support'}
    eligible = []
    for item in requested:
        text = item['text']
        if len(text) > 128 or any(char.isspace() for char in text):
            row['unqueried_inputs'].append({**item, 'reason': 'morphology_token_bound'})
        elif len(eligible) == 256:
            row['unqueried_inputs'].append({**item, 'reason': 'document_lexical_work_limit'})
        else:
            eligible.append(text)
    if not getattr(gateway, 'linghub_url', None):
        row['reason'] = 'lexical_resource_not_configured'
        row['unqueried_inputs'].extend({'text': text, 'reason': row['reason']} for text in eligible)
        return row
    for index in range(0, len(eligible), 64):
        batch = eligible[index:index+64]
        payload = {'lang': task['source_language'], 'tokens': batch, 'limit': 4}
        try:
            data = gateway.request('/v1/morphology/lemmatize', payload, gateway.linghub_url)
            frozen = morphology.freeze(data, batch, task['source_language'], task['locale'])
            row['captures'].append({'request': payload, 'bundle': frozen['bundle']})
            row['returned_count'] += len(batch)
        except Exception as error:
            row.update(unified.diagnostic(error, 'document_lexical_context'))
            row['unqueried_inputs'].extend({'text': text, 'reason': 'lexical_resource_failed'} for text in eligible[index:])
            break
    row['status'] = 'partial' if row['unqueried_inputs'] else 'ok'
    return row


def capture_id(row):
    return 'unified:' + digest({k: row[k] for k in
        ('capture_kind', 'source_slug', 'external_id', 'capture_sha256')})


def checked(bundle):
    if (not isinstance(bundle, dict) or bundle.get('contract') != CONTRACT
            or bundle.get('snapshot_sha256') != digest({k: v for k, v in bundle.items()
                if k != 'snapshot_sha256'})):
        raise Failure('resource_changed', 409)
    task_checked(bundle.get('document_task'))
    unified.checked(bundle.get('source_bundle'))
    if bundle.get('review_route') == 'source_bound_comma':
        _comma_bundle(bundle)


def _empty_source_bundle(task):
    body = {'contract': unified.CONTRACT, 'task': 'reference',
        'query': task['source_text'], 'locale': task['locale'], 'analysis': None,
        'matches': [], 'retrieval': {'status': 'unavailable', 'coverage': [],
            'returned_count': 0}, 'gateway_bundle': None}
    return {**body, 'snapshot_sha256': digest(body)}


def _comma_bundle(bundle):
    stored = bundle.get('comma_capture')
    if stored is None:
        return None
    value = deepcopy(stored)
    value['explanation'] = bundle['source_bundle'] if bundle.get('comma_explanation_present') else None
    comma_tasks.checked(value)
    task = bundle['document_task']
    target = value.get('task', {}).get('target') or {}
    if (task['mode'] != 'review' or task['source_language'] != 'sl'
            or target.get('text') != task['source_text']
            or target.get('scope') != 'question'
            or value['question'] != 'Preveri vejice: ' + task['source_text']):
        raise Failure('resource_changed', 409)
    return value


def _freeze_comma_review(gateway, task, policy):
    """Use the existing source-bound checker for a literal server-owned task.

    The interpreter sees a fixed operation frame. Bind the complete operand
    before a provider can receive it, then reuse that exact interpretation.
    """
    if (task['mode'] != 'review' or task['source_language'] != 'sl'
            or not comma_tasks.capability(getattr(gateway, 'comma_checker', None))['configured']):
        return None, None
    question = 'Preveri vejice: ' + task['source_text']
    interpreted = unified.shared_interpretation(gateway, question, '', 'sl', task['locale'])
    target = (interpreted['request_plan'].get('comma_check') or {}).get('target') or {}
    expected = {'text': task['source_text'], 'scope': 'question',
                'start': len('Preveri vejice: '), 'end': len(question)}
    if target != expected:
        # The document contract still binds the complete original. A legacy
        # text checker that selects only part of it must not receive that slice.
        return None, {'status': 'not_bound',
                      'reason': 'literal_comma_target_does_not_cover_document'}
    frozen = None
    if target == expected:
        class BoundGateway:
            def __getattr__(self, name):
                return getattr(gateway, name)

            def interpret(self, **payload):
                return self.request('/internal/pilot/interpret', payload)

            def request(self, path, payload, base=None):
                if path == '/internal/pilot/interpret' and base is None:
                    if payload != {'question': question, 'context': '',
                            'input_language': 'sl', 'locale': task['locale']}:
                        raise Failure('invalid_request', 422)
                    return {'contract': 'klikai.request-interpretation.v1',
                        'input_sha256': interpreted['interpretation_input_sha256'],
                        'request_plan': deepcopy(interpreted['request_plan']),
                        'request_plan_sha256': interpreted['request_plan_sha256']}
                return gateway.request(path, payload, base)
        explicit = {'question': question, 'original_question': question, 'context': '',
            'locale': task['locale'], 'input_language': 'sl', 'task': 'reference',
            'request_options': {}}
        frozen = comma_tasks.freeze(BoundGateway(), explicit, policy)
    captured = frozen['bundle'] if frozen is not None else None
    if captured is not None:
        comma_tasks.checked(captured)
        observation = captured.get('observation')
        if isinstance(observation, dict) and observation.get('input_text') != task['source_text']:
            raise Failure('invalid_request', 422)
        # Zero predicted changes is not a full review. Retain the observation
        # and continue the ordinary typed source-backed document review.
        if (isinstance(observation, dict) and observation.get('status') == 'ok'
                and observation.get('edits') == []):
            return None, {key: deepcopy(captured[key]) for key in
                ('task', 'observation', 'authorization') if key in captured}
    explanation = (captured or {}).get('explanation')
    reader = deepcopy(explanation) if isinstance(explanation, dict) else _empty_source_bundle(task)
    unified.checked(reader)
    stored = deepcopy(captured)
    if stored is not None:
        stored.pop('explanation')
    retrieval = {**deepcopy(reader['retrieval']), 'route': 'typed_source_bound_comma'}
    body = {'contract': CONTRACT, 'document_task': deepcopy(task),
        'source_bundle': reader, 'retrieval': retrieval,
        'review_route': 'source_bound_comma', 'comma_capture': stored,
        'comma_explanation_present': isinstance(explanation, dict)}
    if len(canonical(body).encode()) > unified.MAX_FROZEN_BYTES:
        raise Failure('unified_capture_too_large', 413)
    pin = digest(body)
    return {'bundle': {**body, 'snapshot_sha256': pin},
            'identity': {'contract': CONTRACT, 'captured_response_sha256': pin}}, None


def _freeze_general(gateway, family, policy, *, comma_preflight=None):
    task = task_checked((family.get('request_options') or {}).get('document_task'))
    if task['locale'] != family['locale'] or task['source_language'] != family['input_language']:
        raise Failure('invalid_request')
    started = time.monotonic()
    coverage, found = [], []
    try:
        definitions = unified.registry(gateway) if gateway.klikai_url else []
        if not gateway.klikai_url:
            raise Failure('source_unavailable', 503)
    except Exception as error:
        definitions = []
        coverage.append({'family': 'configured_sources', 'status': 'unavailable',
            'returned_count': 0, 'limit': None, 'has_more': None,
            **unified.diagnostic(error, 'document_source_registry')})
    discovery = lookup_windows(task['source_text'])
    query = discovery['windows'][0]['query'] if discovery['windows'] else ''
    source_gateway = _DocumentSourceCache(gateway)
    # collect_source uses only this explicit query override; no question-plan or
    # uploaded instruction is sent to the linguistic request parser.
    source_family = {'question': task['source_text'], 'locale': task['locale'],
                     'input_language': task['source_language'], 'context': ''}
    with ThreadPoolExecutor(max_workers=unified.DETAIL_WORKERS) as details, \
            ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_collect_document_source, source_gateway, source_family,
                   definition, details, discovery) for definition in definitions]
        for future in as_completed(futures):
            rows, observed = future.result()
            found.extend(rows)
            coverage.append(observed)
        if task['mode'] == 'review':
            discovery_family = {**source_family, 'original_question': task['source_text'],
                'request_plan': {'comma_check': {'explain': True, 'reference_queries': [
                    {'source_slug': 'sp2001', 'query': heading}
                    for heading in REVIEW_REFERENCE_HEADINGS]}}}
            # Reuse exact-heading discovery and the existing registry/status
            # accounting; no interpretation or applicability verdict is added.
            unified.expand_request_reference_rules(source_gateway, discovery_family, definitions,
                found, coverage, details, pool, exact_heading_only=True)
    found = unified.merge_source_matches(found)
    # Exact source-owned term/advice matches precede generic large headings.
    # Every original remains frozen; this changes fitting order, not authority.
    found.sort(key=lambda row:_writer_source_priority(row,task))
    if len(found) > 256:
        raise Failure('unified_capture_too_large', 413)
    lexical_context = _freeze_document_lexical_context(gateway, task, found)
    if lexical_context:
        coverage.append(lexical_context)
    for row in found:
        row['evidence_id'] = capture_id(row)
        # Identify this exact captured record, independently of its mutable URL
        # or the model's selected excerpt. Native edition fields remain intact.
        view = row['source']
        view.update(evidence_id=row['evidence_id'], source_slug=row['source_slug'],
            external_id=row['external_id'], source_id=row['source_slug'] + ':' + row['external_id'],
            source_revision=row['capture_sha256'],
            locator={**view.get('locator', {}), 'capture_sha256': row['capture_sha256'],
                     'capture_kind': row['capture_kind'], 'external_id': row['external_id']})
    coverage.sort(key=lambda row: row['family'])
    external = [{k: deepcopy(v) for k, v in row.items() if k not in
                 {'source', 'discovery_captures', 'discovery_windows'}} for row in found]
    packet = {'version': unified.PACKET, 'matches': external, 'retrieval': coverage}
    if (len(found) > 256 or len(coverage) > 64
            or len(canonical(packet).encode()) > unified.MAX_BYTES):
        raise Failure('unified_capture_too_large', 413)
    statuses = {row['status'] for row in coverage}
    retrieval = {'route': 'typed_document', 'status': 'partial' if statuses &
        {'partial', 'timeout', 'unavailable', 'unsupported'} or discovery['unqueried_ranges'] else 'complete',
        'coverage': coverage, 'returned_count': len(found),
        'elapsed_ms': round((time.monotonic() - started) * 1000, 3),
        'discovery_basis': 'bounded_overlapping_source_windows_v2', 'query': query,
        'input_discovery': discovery}
    selection = _review_writer_selection(task, found)
    if selection:
        retrieval.update(status='partial', writer_evidence_selection=deepcopy(selection))
    reader = {'contract': unified.CONTRACT, 'task': 'reference',
        'query': task['source_text'], 'locale': task['locale'], 'analysis': None,
        'matches': found, 'retrieval': retrieval, 'gateway_bundle': None}
    reader['snapshot_sha256'] = digest(reader)
    body = {'contract': CONTRACT, 'document_task': task,
            'source_bundle': reader, 'unified_evidence': packet, 'retrieval': retrieval}
    if selection:
        body['writer_evidence_selection'] = selection
    if comma_preflight is not None:
        body['comma_preflight'] = comma_preflight
    if len(canonical(body).encode()) > unified.MAX_FROZEN_BYTES:
        raise Failure('unified_capture_too_large', 413)
    pin = digest(body)
    return {'bundle': {**body, 'snapshot_sha256': pin},
            'identity': {'contract': CONTRACT, 'captured_response_sha256': pin},
            'timings': {'source_collection_ms': retrieval['elapsed_ms']}}


def _native_review_cue(row, text, source_language=None):
    """Discovery priority from exact source fields, never an edit verdict."""
    raw = row.get('capture') or {}
    folded = text.casefold()
    if row.get('context_eligible') is False or row.get('rights_mode') in {'linked_only', 'metadata_only', 'unknown'}:
        return 0
    if row.get('capture_kind') == 'terminology_record':
        return 2 if any(isinstance(term, str) and term.strip()
            and any(_surface_occurrences(term, text))
            for term in _native_term_surfaces(raw, source_language)) else 0
    if source_language is not None and raw.get('language') != source_language:
        return 0
    original = raw.get('original_text') or ''
    words = set(re.findall(r'[^\W\d_]{4,}', folded))
    common = {'kako', 'tudi', 'lahko', 'samo', 'zato', 'medtem', 'oziroma', 'kadar', 'torej'}
    token_match = False
    for advice in raw.get('advice_units') or []:
        if not isinstance(advice, dict):
            continue
        span = advice.get('source_span') or {}
        a, b = span.get('char_start'), span.get('char_end')
        if (type(a) is not int or type(b) is not int or not 0 <= a < b <= len(original)
                or sha_text(original[a:b]) != span.get('text_sha256')):
            continue
        quote = original[a:b]
        if quote.count(' | ⇨ | ') == 1:
            left = quote.split(' | ⇨ | ', 1)[0]
            if '\n' not in left and any(any(_surface_occurrences(surface.strip(), text))
                    for surface in left.split(',') if surface.strip()):
                return 2
        token_match |= bool((words - common) & set(re.findall(r'[^\W\d_]{4,}', quote.casefold())))
    return 1 if token_match else 0


def _literal_term_length(row, text, source_language=None):
    raw = row.get('capture') or {}
    return max((len(term) for term in _native_term_surfaces(raw, source_language) if isinstance(term, str) and term.strip()
        and any(_surface_occurrences(term, text))), default=0)


def _native_term_surfaces(capture, source_language=None):
    recorded = []
    for entry in capture.get('term_entries',[]):
        recorded.extend((form['text'],form['language']) for form in document_surface.entry_forms(entry))
    if not recorded and capture.get('record_kind') == 'terminology_decision':
        for key, language in [('original_terms','it'),('standardized_terms','sl'),('proposals','sl')]:
            recorded.extend((term,language) for term in capture.get(key,[]) if isinstance(term,str))
    if source_language is None and not recorded:
        # Compatibility for callers inspecting raw candidates without a task;
        # actual document admission always supplies the recorded source language.
        for key in ('original_terms','standardized_terms'):
            yield from capture.get(key,[])
    for term,language in recorded:
        if source_language is None or language == source_language:
            yield term
    for nomination in capture.get('matched_forms',[]):
        if (isinstance(nomination,dict)and nomination.get('language')in {'sl','it'}
                and isinstance(nomination.get('term'),str)
                and (source_language is None or nomination['language']==source_language)
                and (source_language is None or any(language==source_language
                    and terminology.normalized(term)==terminology.normalized(nomination.get('term',''))
                    for term,language in recorded))
                and nomination.get('match_tier')=='morphology_exact'and nomination.get('normalization')=='morphology'
                and isinstance(nomination.get('lexicon_release_id'),str)and nomination['lexicon_release_id']
                and isinstance(nomination.get('query_signature'),str)and nomination['query_signature']):
            yield nomination.get('matched_query')


def _writer_source_priority(row,task):
    cue=_native_review_cue(row,task['source_text'],task['source_language'])
    if cue>=2:return (0,-_literal_term_length(row,task['source_text'],task['source_language']))
    original=' '.join((row.get('capture',{}).get('original_text')or'').split())
    if task['mode']=='review'and row.get('source_slug')=='sp2001'and row.get('capture_kind')=='resource_record':
        for index,heading in enumerate(REVIEW_REFERENCE_HEADINGS):
            if heading in original:return (1,index)
    return (2 if cue else 3,0)


def _review_writer_selection(task, matches):
    """Nominate whole source parents by exact native fields, never edit text."""
    direct = [row['evidence_id'] for row in matches
              if row.get('context_eligible') is not False and row.get('rights_mode') != 'linked_only'
              and _native_review_cue(row, task['source_text'], task['source_language']) >= 2]
    if not direct:
        return None
    return {'basis': 'recorded_source_language_term_or_advice_occurrence', 'status': 'partial',
        'scope': 'whole_parent_candidates_not_applicability_or_preference',
        'eligible_evidence_ids': direct,
        'excluded_evidence_ids': [row['evidence_id'] for row in matches
                                  if row['evidence_id'] not in direct]}


def _selection_notice(task, selection):
    if not selection:
        return None
    if task['mode']=='translate':
        return ('Il traduttore riceve solo i record interi selezionati nell’input conservato. '
            'Gli altri risultati e le incertezze restano disponibili; non è una verifica linguistica completa.'
            if task['locale']=='it'else'Prevajalnik prejme le izbrane cele zapise iz shranjenega vhoda. '
            'Drugi zadetki in negotovosti ostanejo na voljo; to ni celovito jezikovno preverjanje.')
    return ('La revisione considera solo i record con una corrispondenza letterale '
        'nei campi terminologici o nelle indicazioni della fonte. Non copre tutti '
        'gli aspetti linguistici; gli altri record trovati restano consultabili.'
        if task['locale'] == 'it' else
        'Pregled upošteva le zapise z dobesednim ujemanjem v terminoloških poljih '
        'ali napotkih vira. Ne zajema vseh jezikovnih lastnosti; drugi najdeni '
        'zapisi ostajajo na voljo za pregled.')


def _changed_sentence_spans(comma):
    """Use only already retained ORIGINAL native sentence offsets."""
    captured = _comma_bundle(comma)
    if captured is None:
        return []
    text = comma['document_task']['source_text']
    observations = (comma['source_bundle'].get('analysis') or {}).get('captures', [])
    native = next((r.get('response') for r in observations if r.get('operation') == 'syntax'
        and isinstance(r.get('response'), dict) and r['response'].get('input_text') == text), None)
    if (not isinstance(native, dict) or native.get('status') != 'ok'
            or native.get('input_sha256') != sha_text(text)
            or (native.get('analysis') or {}).get('text') != text
            or (native.get('analysis') or {}).get('offset_convention') != 'unicode_codepoint_half_open'):
        return []
    words = ((native or {}).get('analysis') or {}).get('words')
    if not isinstance(words, list) or not words:
        return []
    groups, previous = {}, 0
    for word in words:
        if not isinstance(word, dict):
            return []
        a, b, sentence = word.get('start'), word.get('end'), word.get('sentence_id')
        if (type(a) is not int or type(b) is not int or type(sentence) is not int
                or not previous <= a < b <= len(text) or text[a:b] != word.get('text')):
            return []
        previous = b
        groups.setdefault(sentence, [a, b])[1] = b
    if len(groups) < 2:
        return []
    edits = (captured.get('observation') or {}).get('edits') or []
    changed = []
    for a, b in groups.values():
        if any(type(e.get('start')) is int and type(e.get('end')) is int
               and a <= e['start'] <= e['end'] <= b for e in edits):
            changed.append((a, b))
    return changed[:4]


def _component_body(bundle, component):
    body = deepcopy(component['body'])
    by_id = {row['evidence_id']: row for row in bundle['source_bundle']['matches']}
    reader = deepcopy(component['reader_metadata'])
    reader['matches'] = [deepcopy(by_id[eid]) for eid in component['reader_ids']]
    body['source_bundle'] = reader
    checked(body)
    return body


def freeze(gateway, family, policy):
    task = task_checked((family.get('request_options') or {}).get('document_task'))
    if task['locale'] != family['locale'] or task['source_language'] != family['input_language']:
        raise Failure('invalid_request')
    comma, preflight = _freeze_comma_review(gateway, task, policy)
    general = _freeze_general(gateway, family, policy, comma_preflight=preflight)
    if comma is None:
        return general
    original = comma['bundle']
    spans = _changed_sentence_spans(original)
    candidates = []
    if spans:
        for a, b in spans:
            subtask = {**task, 'source_text': task['source_text'][a:b],
                'source_sha256': sha_text(task['source_text'][a:b])}
            value, unused = _freeze_comma_review(gateway, subtask, policy)
            if value is not None:
                candidates.append((a, b, value['bundle']))
    else:
        candidates.append((0, len(task['source_text']), original))
    body = general['bundle']
    body.pop('snapshot_sha256')
    # Bind the scope before signing the shared freeze. Comma components keep
    # their separate original tasks and source-bound application receipts.
    body['document_task']['review_scope'] = 'non_comma'
    reader = body['source_bundle']
    reader.pop('snapshot_sha256')
    by_id = {r['evidence_id']: r for r in reader['matches']}
    components, reserved = [], {}
    for a, b, candidate in candidates:
        child_reader = candidate['source_bundle']
        if any(row['evidence_id'] in reserved and digest(row) != reserved[row['evidence_id']]
               for row in child_reader['matches']):
            continue
        # A component's exact reader wins for its unchanged source identity.
        # Other generic captures and every original packet remain available.
        for row in child_reader['matches']:
            by_id[row['evidence_id']] = deepcopy(row)
            reserved[row['evidence_id']] = digest(row)
        component = {'start': a, 'end': b, 'body': {k: deepcopy(v) for k, v in candidate.items()
            if k != 'source_bundle'}, 'reader_metadata': {k: deepcopy(v) for k, v in child_reader.items()
            if k != 'matches'}, 'reader_ids': [r['evidence_id'] for r in child_reader['matches']]}
        components.append(component)
    reader['matches'] = list(by_id.values())
    reader['retrieval'] = {**reader['retrieval'], 'returned_count': len(reader['matches'])}
    reader['snapshot_sha256'] = digest(reader)
    body.update(review_route='mixed_source_bound_review', comma_components=components,
        comma_parent_observation=deepcopy((_comma_bundle(original) or {}).get('observation')),
        comma_parent_analysis=deepcopy(original['source_bundle'].get('analysis')),
        comma_parent_source_snapshot_sha256=original['source_bundle']['snapshot_sha256'])
    body['snapshot_sha256'] = digest(body)
    if len(canonical(body).encode()) > unified.MAX_FROZEN_BYTES:
        raise Failure('unified_capture_too_large', 413)
    general['identity']['captured_response_sha256'] = body['snapshot_sha256']
    return general


def checked_result(value):
    """Validate the persisted public document result without refetching sources.

    Full text/quote binding is checked in generate against the immutable freeze.
    A null result belongs only to a recorded failed transformation envelope.
    """
    if value is None:
        return None
    required = {'mode', 'source_sha256', 'source_language', 'target_language',
                'locale', 'proposed_text', 'edits', 'notes', 'requires_human_review', 'status'}
    if not isinstance(value, dict) or set(value) - {'validation_scope', 'inline_mappings'} != required:
        invalid()
    if (not isinstance(value['mode'], str) or value['mode'] not in {'review', 'translate'}
            or not isinstance(value['source_language'], str) or value['source_language'] not in {'sl', 'it'}
            or not isinstance(value['locale'], str) or value['locale'] not in {'sl', 'it'}
            or not isinstance(value['source_sha256'], str)
            or re.fullmatch(r'[0-9a-f]{64}', value['source_sha256']) is None
            or value['mode'] == 'review' and value['target_language'] is not None
            or value['mode'] == 'translate' and (not isinstance(value['target_language'], str)
                or value['target_language'] not in {'sl', 'it'}
                or value['target_language'] == value['source_language'])
            or value['requires_human_review'] is not True
            or not isinstance(value['status'], str) or value['status'] not in {'draft', 'not_reviewed'}
            or value['status'] == 'not_reviewed' and value['mode'] != 'review'
            or not isinstance(value['proposed_text'], str) or len(value['proposed_text']) > 12000
            or not isinstance(value['notes'], list) or len(value['notes']) > 20
            or any(not isinstance(n, str) or len(n) > 2000 for n in value['notes'])
            or not isinstance(value['edits'], list) or len(value['edits']) > 64):
        invalid()
    if 'validation_scope' in value:
        allowed_scopes = ({'not_reviewed_no_eligible_sources', 'not_reviewed_no_sources_fit_native_budget',
                           'not_reviewed_no_bounded_support_units', 'not_reviewed_no_supported_comma_application'}
            if value['status'] == 'not_reviewed' else
            {'structure_exact_source_spans_source_membership_and_protected_content_only',
             'translation_structure_and_protected_content_only',
             'source_bound_comma_applications_only', 'mixed_source_bound_document_review'})
        if not isinstance(value['validation_scope'], str) or value['validation_scope'] not in allowed_scopes:
            invalid()
        if value['validation_scope']=='translation_structure_and_protected_content_only'and(
                value['mode']!='translate'or value['source_language']!='sl'or value['target_language']!='it'):
            invalid()
    for edit in value['edits']:
        if not isinstance(edit, dict) or set(edit) != {
                'start', 'end', 'original', 'replacement', 'explanation', 'evidence_ids', 'support_quote'}:
            invalid()
        a, b, ids = edit['start'], edit['end'], edit['evidence_ids']
        mixed = value.get('validation_scope') == 'mixed_source_bound_document_review'
        if (type(a) is not int or type(b) is not int or not 0 <= a <= b <= MAX_TEXT
                or not mixed and a == b
                or not isinstance(edit['original'], str) or not mixed and not edit['original']
                or not isinstance(edit['replacement'], str) or len(edit['replacement']) > 12000
                or not isinstance(edit['explanation'], str) or not edit['explanation'].strip()
                or len(edit['explanation']) > 2000 or not isinstance(ids, list)
                or not 1 <= len(ids) <= 16 or any(not isinstance(eid, str) or not eid for eid in ids)
                or not isinstance(edit['support_quote'], str) or not edit['support_quote'].strip()
                or len(edit['support_quote']) > 4000):
            invalid()
    try:
        value['proposed_text'].encode('utf-8')
        if '\0' in value['proposed_text']:
            invalid()
    except UnicodeError:
        invalid()
    mappings = value.get('inline_mappings', [])
    if not isinstance(mappings, list) or len(mappings) > 16:
        invalid()
    seen, end = set(), 0
    for row in mappings:
        if (not isinstance(row, dict) or set(row) != {'id', 'target_start', 'target_end', 'target_text'}
                or not isinstance(row['id'], str) or not 1 <= len(row['id']) <= 120
                or row['id'] in seen or type(row['target_start']) is not int or type(row['target_end']) is not int
                or not end <= row['target_start'] < row['target_end'] <= len(value['proposed_text'])
                or not isinstance(row['target_text'], str) or not row['target_text']
                or value['proposed_text'][row['target_start']:row['target_end']] != row['target_text']
                or value['proposed_text'].count(row['target_text']) != 1):
            invalid()
        seen.add(row['id'])
        end = row['target_end']
    return deepcopy(value)


def _source_strings(row):
    yield row.get('context_text', '')
    stack = [row.get('capture', {})]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def validate_document_result(value, task, matches):
    checked_result(value)
    if value is None or any(value.get(k) != task[k] for k in
            ('mode', 'source_sha256', 'source_language', 'target_language', 'locale')):
        invalid()
    text = task['source_text']
    last, rendered = 0, []
    for edit in value['edits']:
        a, b, ids = edit['start'], edit['end'], edit['evidence_ids']
        if (not last <= a < b <= len(text) or text[a:b] != edit['original']
                or text.count(edit['original']) != 1
                or edit['original'] == edit['replacement']
                or task.get('review_scope') == 'non_comma'
                    and edit['original'].replace(',', '') == edit['replacement'].replace(',', '')
                or any(eid not in matches or matches[eid].get('context_eligible') is False
                    or matches[eid].get('rights_mode') == 'linked_only' for eid in ids)
                or not any(edit['support_quote'] in source for eid in ids
                           for source in _source_strings(matches[eid]))):
            invalid()
        rendered.extend([text[last:a], edit['replacement']])
        last = b
    rendered.append(text[last:])
    if task['mode'] == 'review' and ''.join(rendered) != value['proposed_text']:
        invalid()
    if value['status'] == 'not_reviewed' and (value['edits'] or value['proposed_text'] != text):
        invalid()
    if task['mode'] == 'translate' and text.strip() and not value['proposed_text'].strip():
        invalid()
    if [r['id'] for r in value.get('inline_mappings', [])] != [r['id'] for r in task.get('inline_spans', [])]:
        invalid()
    return deepcopy(value)


def _comma_document_answer(gateway, bundle, tier, model, parent):
    """Expose only changes certified by the existing owned application graph."""
    task = bundle['document_task']
    captured = _comma_bundle(bundle)
    explained = (comma_tasks.answer(gateway, captured, tier, model, parent) if captured is not None
        else {'execution': {'inference_performed': False, 'provider_scope': 'none'}})
    proposal = explained.get('comma_proposal') or {}
    connection = (explained.get('deterministic_result') or {}).get('explanation_connection') or {}
    edits = proposal.get('edits') or []
    links = connection.get('links') or []
    text = task['source_text']
    supported = (bool(edits) and proposal.get('status') == 'ok'
        and proposal.get('original_text') == text
        and connection.get('status') == 'applied'
        and connection.get('target_sha256') == task['source_sha256']
        and connection.get('unexplained_edit_indexes') == []
        and {row.get('edit_index') for row in links} == set(range(len(edits)))
        and all(row.get('relation') in {'supports_proposal', 'conditional_support'} for row in links))
    selected_blocks, citations, used = [], [], []
    if supported:
        block_ids = {row['block_id'] for row in links}
        selected_blocks = [deepcopy(row) for row in explained.get('display_blocks', [])
                           if row.get('block_id') in block_ids]
        citations = [deepcopy(row) for row in explained.get('citations', [])
                     if row.get('block_id') in block_ids]
        used = list(dict.fromkeys(eid for row in links for eid in row.get('evidence_ids', [])))
        quotes = [row.get('snippet') for row in citations
                  if row.get('evidence_id') in used and isinstance(row.get('snippet'), str)
                  and 0 < len(row['snippet']) <= 4000]
        explanation = '\n\n'.join(row['text'] for row in selected_blocks)
        proposed = proposal.get('proposed_text')
        supported = (bool(selected_blocks) and len(explanation) <= 2000 and bool(quotes)
            and 1 <= len(used) <= 16 and isinstance(proposed, str)
            and 0 < len(proposed) <= 12000
            and all(eid in {r['evidence_id'] for r in bundle['source_bundle']['matches']}
                    for eid in used))
    it = task['locale'] == 'it'
    scope = ('La proposta riguarda solo le modifiche alle virgole collegate alle fonti indicate. '
             'Gli altri aspetti linguistici del testo non sono stati revisionati.' if it else
             'Predlog zajema le spremembe vejic, vezane na navedene vire. '
             'Druge jezikovne lastnosti besedila niso bile pregledane.')
    identity = {k: task[k] for k in ('mode', 'source_language', 'target_language', 'locale', 'source_sha256')}
    document = {**identity, 'requires_human_review': True,
        'status': 'draft' if supported else 'not_reviewed',
        'proposed_text': proposed if supported else text,
        'edits': ([{'start': 0, 'end': len(text), 'original': text,
            'replacement': proposed, 'explanation': explanation, 'evidence_ids': used,
            'support_quote': quotes[0]}] if supported else []), 'notes': [scope],
        'validation_scope': 'source_bound_comma_applications_only' if supported
                            else 'not_reviewed_no_supported_comma_application'}
    checked_result(document)
    out = deepcopy(explained)
    if supported:
        notice = ('Bozza da verificare: modifica alle virgole con spiegazione e fonti.' if it else
                  'Predlog za pregled: sprememba vejic z razlago in viri.')
        blocks = [{'block_id': 'document-draft', 'text': notice},
                  {'block_id': 'document-proposal', 'text': document['proposed_text']},
                  *selected_blocks, {'block_id': 'document-scope', 'text': scope}]
        cited = {row['evidence_id'] for row in citations}
        out['evidence'] = [row for row in out.get('evidence', []) if row.get('evidence_id') in cited]
        out['citations'] = citations
        out['claims'] = [row for row in out.get('claims', [])
                         if row.get('claim_id') in {c.get('claim_id') for c in citations}]
    else:
        notice = ('Questa sezione non è stata revisionata: non è disponibile una proposta '
                  'interamente collegata a un’applicazione delle fonti. Il testo originale è invariato.' if it else
                  'Ta odsek ni jezikovno pregledan: predlog ni v celoti vezan na podprto uporabo virov. '
                  'Izvirno besedilo ostaja nespremenjeno.')
        blocks = [{'block_id': 'document-not-reviewed', 'text': notice},
                  {'block_id': 'document-proposal', 'text': text},
                  {'block_id': 'document-scope', 'text': scope}]
        out.update(evidence=[], citations=[], claims=[], source_badges=[],
                   answer_kind='source_only', generation_status='not_generated')
    out.update(document_result=document, status='partial' if supported else 'source_only',
        answer_support='partial', short_answer=document['proposed_text'],
        answer_md='\n\n'.join(row['text'] for row in blocks), display_blocks=blocks,
        blocks=deepcopy(blocks), retrieval=deepcopy(bundle['retrieval']))
    out.setdefault('limitations', []).append({'code': 'document_review_comma_scope_only', 'scope': 'answer'})
    return out


def _attach_preflight(result, bundle):
    discovery = (bundle.get('retrieval') or {}).get('input_discovery')
    if discovery:
        gaps = bool(discovery['unqueried_ranges'])
        incomplete = any(row.get('status') in {'partial', 'timeout', 'unavailable', 'unsupported'}
            for row in bundle['retrieval']['coverage'])
        if gaps or incomplete:
            it = bundle['document_task']['locale'] == 'it'
            if gaps:
                notice = ('Una parte del testo non è stata cercata perché supera i limiti di ricerca. '
                          'Questo non dimostra che manchino fonti pertinenti.' if it else
                          'Dela besedila zaradi omejitev iskanja ni bilo mogoče preiskati. '
                          'To ne pomeni, da zanj ni ustreznih virov.')
            else:
                notice = ('Le finestre di ricerca erano predisposte per tutto il testo, ma alcune ricerche '
                          'o fonti erano indisponibili o hanno raggiunto un limite. I risultati non sono esaustivi.' if it else
                          'Iskalna okna so bila pripravljena za celotno besedilo, vendar vseh poizvedb ali virov '
                          'ni bilo mogoče pregledati oziroma so dosegli omejitev rezultatov. Najdbe niso izčrpne.')
            result.setdefault('limitations', []).append({'code': 'document_source_discovery_limited',
                'scope': 'answer', 'message': notice})
            result['answer_md'] = result.get('answer_md', '') + '\n\n' + notice
            result.setdefault('display_blocks', []).append({'block_id': 'document-discovery-limit', 'text': notice})
            if result.get('document_result'):
                result['document_result']['notes'].append(notice)
    if bundle.get('comma_preflight'):
        observed = deepcopy(bundle['comma_preflight'])
        result.setdefault('deterministic_result', {})['document_comma_preflight'] = observed
        execution = comma_tasks.reader_execution((observed.get('observation') or {}).get('execution', {}))
        if execution:
            result['tool_execution'] = execution
            result.setdefault('diagnostics', {})['comma_checker'] = execution
    return result


def _checked_guidance_delivery(response, task, matches, unified_evidence):
    """Verify contextual delivery; no source-enforcement or meaning verdict."""
    execution=response.get('execution')or{}
    trace=(response.get('diagnostics')or{}).get('writer')or{}
    packet=trace.get('guidance_packet');delivery=execution.get('guidance_delivery')
    expected={key:task[key]for key in ('mode','source_language','target_language','locale','source_sha256')}
    for key in ('translation_request_id','document_binding'):
        if key in task:expected[key]=task[key]
    if (not isinstance(packet,dict)or not isinstance(delivery,dict)
            or packet.get('version')!='klikai.document-guidance.v1'
            or packet.get('packet_sha256')!=digest({k:v for k,v in packet.items()if k!='packet_sha256'})
            or packet.get('task_binding')!=expected or packet.get('source_sha256')!=task['source_sha256']
            or packet.get('profile_sha256')!=task['translation_profile']['profile_sha256']
            or packet.get('capture_envelope_sha256')!=digest(unified_evidence)
            or packet.get('compliance')!='unproven'or packet.get('support_claims')!=[]
            or packet.get('independently_checked_support')!=[]):invalid()
    admitted=packet.get('admitted_evidence_ids');retrieved=packet.get('retrieved_evidence_ids')
    omitted=packet.get('omitted');sources=packet.get('sources')
    expected_ids=[row['evidence_id']for row in unified_evidence['matches']]
    if (not isinstance(admitted,list)or not admitted or any(not isinstance(eid,str)for eid in admitted)
            or len(admitted)!=len(set(admitted))or retrieved!=expected_ids
            or not isinstance(omitted,list)or not isinstance(sources,list)
            or any(not isinstance(item,dict)or not isinstance(item.get('reason'),str)
                or not item['reason']or item.get('evidence_id')not in retrieved for item in omitted)
            or len({item['evidence_id']for item in omitted})!=len(omitted)
            or set(admitted)&{item['evidence_id']for item in omitted}
            or set(admitted)|{item['evidence_id']for item in omitted}!=set(retrieved)
            or [item.get('evidence_id')for item in sources if isinstance(item,dict)]!=admitted):invalid()
    for index,item in enumerate(sources,1):
        row=matches.get(item['evidence_id']);source=item.get('model_source')
        if (not row or row.get('context_eligible')is False
                or row.get('rights_mode')not in {'owned','open','restricted'}
                or (item.get('parent')or{}).get('source_revision')!=row['capture_sha256']
                or not isinstance(source,dict)or source.get('passage_id')!=item['evidence_id']
                or source.get('source_slug')!=row['source_slug']):invalid()
    from .private_translation import native_references
    references=native_references(packet)
    keys=('version','task_binding','source_sha256','profile_sha256','packet_sha256','capture_envelope_sha256',
          'admitted_evidence_ids','retrieved_evidence_ids','omitted','coverage','compliance','independently_checked_support')
    if any(delivery.get(key)!=packet.get(key)for key in keys):invalid()
    if delivery.get('delivery_state')!='response_received'or delivery.get('support_claims')!=[]:invalid()
    native=trace.get('native_translation')or{}
    prepared=trace.get('prepared_input')or{};rendered=prepared.get('rendered_prompt');span=prepared.get('original_span')or{}
    observed=trace.get('requests')
    if (native.get('source_evidence_ids')!=admitted
            or [row.get('evidence_id')for row in response.get('evidence',[])if isinstance(row,dict)]!=admitted
            or not isinstance(rendered,str)or prepared.get('rendered_prompt_sha256')!=sha_text(rendered)
            or prepared.get('guidance_packet_sha256')!=packet['packet_sha256']
            or prepared.get('guidance_payload')!=references
            or prepared.get('guidance_payload_sha256')!=digest(references)
            or type(span.get('start'))is not int or type(span.get('end'))is not int
            or not 0<=span['start']<span['end']<=len(rendered)
            or rendered[span['start']:span['end']]!=task['source_text']
            or span.get('sha256')!=task['source_sha256']
            or span.get('utf8_byte_start')!=len(rendered[:span['start']].encode())
            or span.get('utf8_byte_end')!=len(rendered[:span['end']].encode())
            or not isinstance(observed,list)or len(observed)!=1
            or observed[0].get('rendered_prompt')!=rendered):invalid()
    reference_block='BEGIN QUALIFIED KLIKAI REFERENCES\n'+canonical(references)+'\nEND QUALIFIED KLIKAI REFERENCES'
    if reference_block not in rendered[:span['start']]:invalid()
    return deepcopy(delivery)


def _generate_general(gateway, bundle, tier, model, parent=None):
    checked(bundle)
    if tier not in {'short', 'answer'} or parent is not None:
        raise Failure('expansion_unavailable', 409)
    if bundle.get('review_route') == 'source_bound_comma':
        return _comma_document_answer(gateway, bundle, tier, model, parent)
    task = bundle['document_task']
    request = {'text': task['source_text'], **{k: task[k] for k in
        ('mode', 'source_language', 'target_language', 'locale', 'source_sha256')},
        'unified_evidence': deepcopy(bundle['unified_evidence'])}
    if 'review_scope' in task:
        request['review_scope'] = task['review_scope']
    selection = bundle.get('writer_evidence_selection') or _review_writer_selection(
        task, bundle['unified_evidence']['matches'])
    if selection:
        eligible = set(selection['eligible_evidence_ids'])
        request['unified_evidence']['matches'] = [row for row in request['unified_evidence']['matches']
            if row['evidence_id'] in eligible]
    if 'inline_spans' in task:
        request['inline_spans'] = deepcopy(task['inline_spans'])
    if 'translation_context' in task:
        request['translation_context'] = deepcopy(task['translation_context'])
    if 'document_binding' in task:
        request['document_binding']=deepcopy(task['document_binding'])
    if 'translation_profile' in task:
        request['translation_profile']=deepcopy(task['translation_profile'])
        request['translation_request_id']=task['translation_request_id']
    # Only an explicitly inspected, server-owned plan in the hash-bound frozen
    # bundle can request excerpts. The public document-task schema admits no
    # such client field, and the writer's response cannot provide one.
    if 'writer_source_selections' in bundle:
        request['source_selections'] = deepcopy(bundle['writer_source_selections'])
    response = gateway.request('/internal/pilot/document-transform', request)
    if not isinstance(response, dict) or response.get('contract') != RESPONSE_CONTRACT:
        invalid()
    if response.get('status') == 'failed':
        if (response.get('document_result') is not None or
                not isinstance(response.get('error_code'), str)):
            invalid()
        it = task['locale'] == 'it'
        message = ('Elaborazione non riuscita. Nessuna proposta disponibile.' if it else
                   'Obdelava ni uspela. Predlog ni na voljo.')
        result = {'status': 'failed', 'answer_kind': 'source_only', 'answer_support': 'none',
            'generation_status': 'failed', 'short_answer': message, 'answer_md': message,
            'evidence': [], 'source_badges': [], 'document_result': None,
            'limitations': [{'code': 'document_transform_failed', 'scope': 'answer'}],
            'retrieval': deepcopy(bundle['retrieval'])}
        for key in ('execution', 'model_identity', 'diagnostics', 'provider_attempts', 'usage', 'timings'):
            if key in response:
                result[key] = deepcopy(response[key])
        return _attach_preflight(result, bundle)
    if 'translation_profile' in task:
        from .private_translation import profile_hash,guided
        expected=profile_hash(task['translation_profile'])
        execution=response.get('execution')or{}
        receipt=((response.get('diagnostics')or{}).get('writer')or{}).get('native_translation')or{}
        if (execution.get('translation_profile')!=task['translation_profile']
                or execution.get('profile_hash')!=expected
                or receipt.get('source_sha256')!=task['source_sha256']
                or receipt.get('translation_profile_sha256')!=expected
                or not guided(task['translation_profile'])and(receipt.get('source_evidence_ids')!=[]or response.get('evidence')!=[])):invalid()
    submitted_ids = {row['evidence_id'] for row in request['unified_evidence']['matches']}
    matches = {row['evidence_id']: row for row in bundle['source_bundle']['matches']
               if row['evidence_id'] in submitted_ids}
    if 'translation_profile'in task and guided(task['translation_profile']):
        _checked_guidance_delivery(response,task,matches,request['unified_evidence'])
    document = validate_document_result(response.get('document_result'), task, matches)
    if 'translation_profile'in task and (document.get('validation_scope')!='translation_structure_and_protected_content_only'
            or document['status']!='draft' or document['edits']or document.get('inline_mappings')
            or set(document['notes'])!={'private_translation_candidate',
                'qualified_source_guidance_supplied'if guided(task['translation_profile'])else'terminology_sources_not_supplied',
                'semantic_inline_alignment_not_supported'}):invalid()
    coverage_notice = _selection_notice(task, selection)
    if coverage_notice:
        document['notes'].append(coverage_notice)
        checked_result(document)
    evidence = response.get('evidence')
    if (not isinstance(evidence, list) or len(evidence) > 100
            or any(not isinstance(row, dict) or not isinstance(row.get('evidence_id'), str)
                or row['evidence_id'] not in matches for row in evidence)):
        invalid()
    used = list(dict.fromkeys(row['evidence_id'] for row in evidence))
    edit_ids = {eid for edit in document['edits'] for eid in edit['evidence_ids']}
    if not edit_ids <= set(used):
        invalid()
    sources = []
    for number, eid in enumerate(used, 1):
        row = matches[eid]
        sources.append({'evidence_id': eid, 'local_number': number,
            **{k: deepcopy(row[k]) for k in ('title', 'source_slug', 'source_label', 'url') if k in row},
            **{k: deepcopy(row['source'][k]) for k in ('source_id', 'source_revision', 'locator')
               if k in row['source']}})
    it = task['locale'] == 'it'
    unreviewed = document['status'] == 'not_reviewed'
    notice = (('Questa sezione non è stata revisionata. Il testo originale è riportato invariato.' if it else
              'Ta odsek ni jezikovno pregledan. Prikazano je nespremenjeno izvirno besedilo.')
              if unreviewed else
              ('Bozza da verificare. Controlla il significato e ogni modifica prima dell’uso.' if it else
              'Predlog za pregled. Pred uporabo preverite pomen in vsako spremembo.'))
    blocks = [{'block_id': 'document-draft', 'text': notice},
              {'block_id': 'document-proposal', 'text': document['proposed_text']}]
    for index, edit in enumerate(document['edits']):
        blocks.append({'block_id': f'document-edit-{index}',
                       'text': edit['explanation'], 'evidence_ids': edit['evidence_ids']})
    fixed_notes = {
        'document_source_applicability_unresolved': (
            'Sono stati trovati record, ma i dati disponibili non ne stabiliscono l’uso pertinente in questo contesto. Non è una conferma linguistica; gli originali restano consultabili.' if it else
            'Zapisi so bili najdeni, vendar iz razpoložljivih podatkov ni mogoče določiti njihove primerne uporabe v tem sobesedilu. To ni jezikovna potrditev; izvirni zapisi ostajajo na voljo za pregled.'),
        'source_support_unavailable': (
            'Non è disponibile materiale dalle fonti ammesse per verificare questa sezione. La traduzione, se presente, è una bozza senza verifica terminologica.' if it else
            'Za preverjanje tega odseka ni gradiva iz dovoljenih virov. Morebitni prevod je osnutek brez terminološke potrditve.'),
        'evidence_excluded_from_writer': (
            'Parte del materiale trovato non rientrava nello spazio disponibile per questa elaborazione ed è rimasta esclusa. Le fonti originali restano consultabili.' if it else
            'Del najdenega gradiva je presegal razpoložljivi prostor za to obdelavo in ni bil uporabljen. Izvirni viri so še vedno na voljo za pregled.'),
        'private_translation_candidate':('Traduzione privata sperimentale: controlla integralmente il significato e lo stile.'if it else'Zasebni preizkus prevoda: preverite celoten pomen in slog.'),
        'terminology_sources_not_supplied':('Nessuna fonte terminologica KLIKai è stata inviata al traduttore. Le fonti eventualmente elencate sono solo materiale aggiuntivo da consultare.'if it else'Prevajalniku niso bili poslani terminološki viri KLIKai. Morebitni navedeni viri so le dodatno gradivo za vpogled.'),
        'qualified_source_guidance_supplied':('Il traduttore ha ricevuto i brani KLIKai indicati con le loro qualificazioni. La consegna non dimostra che ogni scelta segua le fonti; il rispetto delle indicazioni e il significato richiedono verifica.'if it else'Prevajalnik je prejel navedene odlomke KLIKai z njihovimi omejitvami. Posredovanje gradiva ne potrjuje uporabe pri vsaki izbiri; upoštevanje virov in pomen je treba preveriti.'),
        'semantic_inline_alignment_not_supported':('Questo percorso non collega il testo tradotto a collegamenti o enfasi nel testo originale.'if it else'Ta pot ne povezuje prevedenih izrazov s povezavami ali poudarki v izvirniku.')}
    for index, note in enumerate(document['notes']):
        blocks.append({'block_id': f'document-note-{index}', 'text': fixed_notes.get(note, note)})
    result = {'status': 'source_only' if unreviewed else 'partial',
        'answer_kind': 'source_only' if unreviewed else 'generated', 'answer_support': 'partial',
        'generation_status': 'not_generated' if unreviewed else 'generated', 'short_answer': document['proposed_text'],
        'answer_md': '\n\n'.join(block['text'] for block in blocks),
        'display_blocks': blocks, 'evidence': sources,
        'source_badges': list({row['source_slug']: {'source_slug': row['source_slug'],
            'label': row.get('source_label', row['source_slug'])} for row in sources}.values()),
        'limitations': [{'code': 'document_draft_requires_review', 'scope': 'answer'}],
        'document_result': document, 'retrieval': deepcopy(bundle['retrieval'])}
    if selection:
        result['limitations'].append({'code': 'document_review_direct_source_matches_only', 'scope': 'answer'})
    for key in ('execution', 'model_identity', 'diagnostics', 'provider_attempts', 'usage', 'timings'):
        if key in response:
            result[key] = deepcopy(response[key])
    return _attach_preflight(result, bundle)


def _overlap(a, b):
    left, right = (a['start'], a['end']), (b['start'], b['end'])
    if left[0] == left[1]:
        return right[0] <= left[0] < right[1] or left == right
    if right[0] == right[1]:
        return left[0] <= right[0] < left[1]
    return left[0] < right[1] and right[0] < left[1]


def _comma_only(before, after):
    return (',' in before or ',' in after) and re.sub(r'[,\s]', '', before) == re.sub(r'[,\s]', '', after)


def _mixed_document_answer(gateway, bundle, tier, model, parent):
    """Combine independently bound edits against the unchanged section input."""
    task = bundle['document_task']
    text = task['source_text']
    component_results, comma_edits = [], []
    for component in bundle['comma_components']:
        child = _component_body(bundle, component)
        result = _comma_document_answer(gateway, child, tier, model, parent)
        component_results.append(result)
        if result['document_result']['status'] != 'draft':
            continue
        proposal = result['comma_proposal']
        graph = result['deterministic_result']['explanation_connection']
        for index, change in enumerate(proposal['edits']):
            links = [r for r in graph['links'] if r['edit_index'] == index]
            ids = list(dict.fromkeys(eid for r in links for eid in r['evidence_ids']))
            block_ids = {r['block_id'] for r in links}
            explanation = '\n\n'.join(r['text'] for r in result['display_blocks']
                if r.get('block_id') in block_ids)
            quote = next(r['snippet'] for r in result['citations']
                         if r['evidence_id'] in ids and r.get('snippet'))
            comma_edits.append({'start': component['start'] + change['start'],
                'end': component['start'] + change['end'], 'original': change['original'],
                'replacement': change['replacement'], 'explanation': explanation,
                'evidence_ids': ids, 'support_quote': quote})
    try:
        generic = _generate_general(gateway, bundle, tier, model, parent)
    except Failure as error:
        # A failure in the independent draft operation does not erase already
        # supported changes. An unacknowledged provider attempt stays unknown.
        generic = {'status': 'failed', 'document_result': None, 'evidence': [],
            'execution': {'inference_performed': None, 'route_reason': 'document_review_unacknowledged'},
            'diagnostics': {'writer': {'status': 'unacknowledged', 'error_code': error.code,
                'trace_incomplete': True}}}
    generic_document = generic.get('document_result') or {}
    independent = []
    conflicts = False
    for edit in generic_document.get('edits', []):
        if ',' not in edit['original'] and ',' not in edit['replacement']:
            pieces = [deepcopy(edit)]
        else:
            pieces = []
            for operation, a, b, c, d in SequenceMatcher(None, edit['original'],
                    edit['replacement'], autojunk=False).get_opcodes():
                before, after = edit['original'][a:b], edit['replacement'][c:d]
                if operation == 'equal' or _comma_only(before, after):
                    continue
                pieces.append({**deepcopy(edit), 'start': edit['start'] + a,
                    'end': edit['start'] + b, 'original': before, 'replacement': after})
        for piece in pieces:
            if any(_overlap(piece, known) for known in [*comma_edits, *independent]):
                conflicts = True
            else:
                independent.append(piece)
    edits = sorted([*comma_edits, *independent], key=lambda r: (r['start'], r['end']))
    cursor, pieces = 0, []
    for edit in edits:
        if edit['start'] < cursor or text[edit['start']:edit['end']] != edit['original']:
            invalid()
        pieces.extend((text[cursor:edit['start']], edit['replacement']))
        cursor = edit['end']
    pieces.append(text[cursor:])
    proposed = ''.join(pieces)
    it = task['locale'] == 'it'
    scope = ('Sono applicate solo le modifiche collegate alle fonti indicate. Le virgole usano '
        'le applicazioni già verificate del controllo esistente; le altre modifiche restano '
        'proposte da verificare. Il testo invariato non costituisce una certificazione di correttezza.' if it else
        'Uporabljene so le spremembe, vezane na navedene vire. Vejice temeljijo na preverjenih '
        'uporabah obstoječega preverjevalnika, druge spremembe ostajajo predlogi za pregled. '
        'Nespremenjeno besedilo ni potrdilo o jezikovni pravilnosti.')
    notes = [scope]
    coverage_notice = _selection_notice(task, bundle.get('writer_evidence_selection'))
    if coverage_notice:
        notes.append(coverage_notice)
    if conflicts:
        notes.append('Predlogi, ki se prekrivajo s podprto spremembo, niso bili uporabljeni.' if not it else
                     'Le proposte in conflitto con una modifica supportata non sono state applicate.')
    pending_commas = len((bundle.get('comma_parent_observation') or {}).get('edits') or []) > len(comma_edits)
    if not comma_edits or pending_commas:
        notes.append('Predlagane spremembe vejic niso bile v celoti podprte; ostale so neuporabljene.' if not it else
                     'Le modifiche alle virgole non interamente supportate non sono state applicate.')
    if generic.get('status') == 'failed':
        notes.append('Neodvisni pregled drugih jezikovnih lastnosti ni uspel.' if not it else
                     'La revisione indipendente degli altri aspetti linguistici non è riuscita.')
    status = 'draft' if edits else 'not_reviewed'
    document = {**{k: task[k] for k in ('mode', 'source_language', 'target_language', 'locale', 'source_sha256')},
        'status': status, 'proposed_text': proposed, 'edits': edits, 'notes': notes,
        'requires_human_review': True, 'validation_scope': 'mixed_source_bound_document_review'
            if status == 'draft' else 'not_reviewed_no_supported_comma_application'}
    checked_result(document)
    out = deepcopy(generic)
    if generic.get('status')=='failed'and not any(r.get('code')=='document_transform_failed'
            for r in out.get('limitations',[])if isinstance(r,dict)):
        out.setdefault('limitations',[]).append({'code':'document_transform_failed','scope':'answer'})
    evidence = {r['evidence_id']: deepcopy(r) for result in [generic, *component_results]
                for r in result.get('evidence', [])}
    used = set(eid for edit in edits for eid in edit['evidence_ids'])
    out['evidence'] = [row for eid, row in evidence.items() if eid in used]
    for number, row in enumerate(out['evidence'], 1):
        row['local_number'] = number
    blocks = [{'block_id': 'document-draft', 'text': scope},
              {'block_id': 'document-proposal', 'text': proposed}]
    if coverage_notice:
        blocks.append({'block_id': 'document-source-coverage', 'text': coverage_notice})
    for index, edit in enumerate(edits):
        blocks.append({'block_id': f'document-edit-{index}', 'text': edit['explanation'],
                       'evidence_ids': edit['evidence_ids']})
    out.update(status='partial' if edits else 'source_only', document_result=document,
        answer_kind='mixed' if independent else 'generated' if comma_edits else 'source_only',
        answer_support='partial', generation_status='generated' if independent else 'not_generated',
        short_answer=proposed, answer_md='\n\n'.join(b['text'] for b in blocks),
        display_blocks=blocks, blocks=deepcopy(blocks), claims=[], citations=[],
        synthesis_mode='mixed_generated_and_source_partial',
        source_badges=list({r.get('source_slug'): {'source_slug': r.get('source_slug'),
            'label': r.get('source_label', r.get('source_slug'))} for r in out['evidence']}.values()))
    out.setdefault('deterministic_result', {})['document_review_components'] = {
        'comma_results': deepcopy(component_results),
        'original_section_observation': deepcopy(bundle.get('comma_parent_observation')),
        'general_document_result': deepcopy(generic_document)}
    out.setdefault('diagnostics', {})['document_review_components'] = {
        'comma': [deepcopy(r.get('diagnostics', {})) for r in component_results],
        'general': deepcopy(generic.get('diagnostics', {}))}
    executions = [r.get('execution', {}).get('inference_performed') for r in [generic, *component_results]]
    out['execution'] = {**out.get('execution', {}), 'inference_performed':
        True if True in executions else False if executions and all(v is False for v in executions) else None,
        'route_reason': 'mixed_source_bound_document_review'}
    attempts = [deepcopy(a) for r in [generic, *component_results] for a in r.get('provider_attempts', [])]
    if attempts:
        out['provider_attempts'] = attempts
    tools = [r.get('tool_execution') for r in component_results if isinstance(r.get('tool_execution'), dict)]
    if tools:
        out['tool_execution'] = {**deepcopy(tools[0]),
            'writer_inference_performed': out['execution']['inference_performed']}
    return out


def generate(gateway, bundle, tier, model, parent=None):
    checked(bundle)
    if tier not in {'short', 'answer'} or parent is not None:
        raise Failure('expansion_unavailable', 409)
    if bundle.get('review_route') == 'mixed_source_bound_review':
        return _mixed_document_answer(gateway, bundle, tier, model, parent)
    return _generate_general(gateway, bundle, tier, model, parent)


def source(bundle, evidence_id, tier=None):
    checked(bundle)
    return unified.source(bundle['source_bundle'], evidence_id)
