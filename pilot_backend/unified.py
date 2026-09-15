"""Normal-query retrieval, owned match browsing and honest generation fallback.

Documentary families come from the configured active registry. Linguistic tools
remain tools; neither a source outage nor a bounded response proves no matches.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import math
import re
import time
from urllib.parse import quote, urlencode

from . import collation, editorial, terminology
from .security import Failure
from .store import canonical, digest

CONTRACT = 'klikai.pilot.unified-capture.v1'
PACKET = 'klikai.unified-evidence.v1'
MAX_BYTES = 2 * 1024 * 1024
DETAIL_WORKERS = 4
TERM_ADAPTERS = {'luka_jsonl_terminology', 'luka_jsonl_decision'}
NON_DOCUMENTARY = {'linghub', 'external_tool', 'ontology_v2_jsonl', 'xlsx_source_register'}


def evidence_id(row):
    return row.get('evidence_id') or row.get('passage_id') or row.get('citation_id')


def checked(bundle):
    if (not isinstance(bundle, dict) or bundle.get('contract') != CONTRACT or
            bundle.get('snapshot_sha256') != digest({k: v for k, v in bundle.items() if k != 'snapshot_sha256'})):
        raise Failure('resource_changed', 409)
    return bundle


def score(value):
    return float(value) if type(value) in {int, float} and math.isfinite(value) else 0.0


def diagnostic(error, stage):
    """Fixed public classifications; exception messages may contain private data."""
    codes = {'answer_unavailable', 'source_timeout', 'invalid_answer', 'unknown_citation',
             'incomplete_unified_capture', 'resource_changed', 'generation_waiting_capacity',
             'provider_identity_missing', 'provider_selection_mismatch'}
    from .human_feedback import PROVIDER_FAILURE_CODES
    codes |= PROVIDER_FAILURE_CODES
    code = error.code if isinstance(error, Failure) and error.code in codes else 'invalid_gateway_response'
    status=getattr(error,'http_status',None)
    return {'error_code': code, 'stage': stage, **({'http_status':status}if type(status)is int and 100<=status<=599 else{})}


def match(capture, kind, label, text, source, locale, weight=0):
    slug = capture['source_slug']
    rid = capture.get('resource_id') or capture.get('external_id')
    if not isinstance(rid, str) or not rid or (capture.get('title') is not None and not isinstance(capture['title'], str)):
        raise Failure('invalid_answer', 502)
    pin = digest(capture)
    return {'evidence_id': 'unified-local:' + digest([kind, slug, rid, pin]),
            'source_slug': slug, 'source_label': label, 'external_id': rid,
            'title': capture['title'], 'snippet': capture.get('snippet') or text[:360],
            'rights_mode': capture['rights_mode'], 'url': capture.get('url'),
            'bibliography': deepcopy(source.get('bibliography', {})), 'score': score(weight),
            'capture_kind': kind, 'capture': deepcopy(capture), 'capture_sha256': pin,
            'context_text': text, 'source': deepcopy(source)}


def registry(gateway):
    data = gateway.request('/api/sources', None, gateway.klikai_url)
    rows = data.get('sources')
    if not isinstance(rows, list) or len(rows) > 256:
        raise Failure('invalid_answer', 502)
    result = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('is_active') is not True:
            continue
        slug = row.get('slug')
        if (not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9_-]{1,100}', slug)
                or slug in seen or not isinstance(row.get('adapter_key'), str)
                or not isinstance(row.get('display_name'), str)):
            raise Failure('invalid_answer', 502)
        seen.add(slug)
        # No root paths, connection data or free-form registry descriptions enter
        # the public capture or the generation context.
        result.append({key: row.get(key) for key in ('slug', 'adapter_key', 'display_name', 'rights_mode', 'source_type')})
    return result


def fetch_editorial_detail(gateway, slug, rid):
    """One existing bounded HTTP request, measured independently of rendering."""
    started = time.monotonic()
    try:
        record = gateway.request('/api/editorial/records/' + slug + '/' + quote(rid, safe=''), None, gateway.klikai_url)
        return record, None, round((time.monotonic() - started) * 1000, 3)
    except Exception as error:
        return None, error, round((time.monotonic() - started) * 1000, 3)


def collect_source(gateway, family, definition, detail_pool=None):
    if detail_pool is None:
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            return collect_source(gateway, family, definition, pool)
    started = time.monotonic()
    slug, adapter = definition['slug'], definition['adapter_key']
    query, locale = family['question'], family['locale']
    limit = 20 if adapter in TERM_ADAPTERS else 3 if slug in editorial.SOURCES else 10
    coverage = {'family': slug, 'source_label': definition['display_name'], 'status': 'unavailable', 'returned_count': 0,
                'limit': limit, 'has_more': None, 'elapsed_ms': 0.0,
                'search_ms': 0.0, 'detail_ms': 0.0, 'detail_requests': 0,
                'detail_reused': 0, 'details': []}
    matches = []
    detail_started = None
    try:
        if definition.get('source_type') == 'tool' or adapter in NON_DOCUMENTARY:
            coverage['status'] = 'not_documentary'
            return matches, coverage
        if not 2 <= len(query) <= 500:
            coverage.update(status='unsupported', reason='query_outside_search_bound')
            return matches, coverage
        search_started = time.monotonic()
        try:
            if adapter in TERM_ADAPTERS:
                params = {'q': query, 'sources': slug, 'limit': limit,
                          'allow_partial': 'true', 'include_prose': 'true', 'use_morphology': 'true'}
                data = gateway.request('/api/terminology/lookup?' + urlencode(params), None, gateway.klikai_url)
            else:
                data = gateway.request('/api/search?' + urlencode({'q': query, 'sources': slug, 'limit': limit}), None, gateway.klikai_url)
        finally:
            coverage['search_ms'] = round((time.monotonic() - search_started) * 1000, 3)
        hits = data.get('hits')
        if (data.get('query') != query or data.get('sources') != [slug] or not isinstance(hits, list)
                or len(hits) > limit or type(data.get('count')) is not int or data['count'] != len(hits)):
            raise Failure('invalid_answer', 502)
        coverage['returned_count'] = len(hits)
        details = {}
        if adapter not in TERM_ADAPTERS and slug in editorial.SOURCES and gateway.editorial_full_records:
            # Independent full-record reads used to run serially after discovery.
            # Share four workers across this query's sources, and fetch a repeated
            # identity once. This cache ends with the capture; no answers or source
            # editions are reused across requests or users.
            for hit in hits:
                try:
                    editorial.freeze(dict(data, hits=[hit], count=1), query, slug, locale)
                except Failure:
                    # The ordered consumer below retains the existing invalid-hit
                    # boundary and any preceding valid matches.
                    break
                rid = hit['resource_id']
                if rid in details:
                    coverage['detail_reused'] += 1
                    continue
                if detail_started is None:
                    detail_started = time.monotonic()
                details[rid] = detail_pool.submit(fetch_editorial_detail, gateway, slug, rid)
            coverage['detail_requests'] = len(details)
        for hit in hits:
            if (not isinstance(hit, dict) or hit.get('source_slug') != slug or
                    hit.get('rights_mode') not in {'owned', 'open', 'restricted', 'linked_only'}):
                raise Failure('invalid_answer', 502)
            if adapter in TERM_ADAPTERS:
                for key in ('original_terms', 'standardized_terms', 'proposals', 'categories', 'subcategories', 'rationale'):
                    if not isinstance(hit.get(key), list) or any(not isinstance(x, str) for x in hit[key]):
                        raise Failure('invalid_answer', 502)
                label = hit.get('source_display_name') or definition['display_name']
                rendered = terminology.record_text(hit, locale)
                source = {'title': hit['title'], 'source_label': label, 'excerpt': rendered,
                          'url': hit.get('url'), 'bibliography': {k: deepcopy(hit[k]) for k in
                          ('external_id', 'source_date', 'rights_mode', 'references') if k in hit}}
                matches.append(match(hit, 'terminology_record', label, rendered, source, locale, hit.get('score')))
            elif slug in editorial.SOURCES:
                # Preserve discovery on individual detail failure. It remains
                # browsable but is not submitted as complete editorial evidence.
                discovery = editorial.freeze(dict(data, hits=[hit], count=1), query, slug, locale)['bundle']
                detail = {'external_id': hit['resource_id'], 'status': 'unavailable', 'elapsed_ms': 0.0}
                try:
                    if not gateway.editorial_full_records:
                        raise Failure('task_unavailable', 503)
                    record, error, detail['elapsed_ms'] = details[hit['resource_id']].result()
                    if error is not None:
                        raise error
                    frozen = editorial.freeze_records(discovery, [record], gateway.editorial_operator_policy_id)['bundle']
                    item = editorial.record_evidence(frozen)[0]
                    source = editorial.record_source(frozen, item['evidence_id'])
                    rendered = editorial.full_record_text(frozen, record)
                    matches.append(match(record, 'editorial_record', frozen['source_label'], rendered, source, locale, hit.get('score')))
                    detail['status'] = 'ok'
                except Exception as error:
                    source = {'title': hit['title'], 'source_label': definition['display_name'],
                              'excerpt': editorial.notice(locale) + '\n\n' + hit['snippet'],
                              'url': hit.get('url'), 'bibliography': {'rights_mode': hit['rights_mode']}}
                    item = match(hit, 'resource_record', definition['display_name'], hit['title'] + '\n\n' + hit['snippet'], source, locale, hit.get('score'))
                    item['context_eligible'] = False
                    item['detail_status'] = 'timeout' if isinstance(error, Failure) and error.code == 'source_timeout' else 'unavailable'
                    matches.append(item)
                    coverage['status'] = 'partial'
                    detail.update(status=item['detail_status'], **diagnostic(error, 'editorial_detail'))
                finally:
                    coverage['details'].append(detail)
            else:
                if not isinstance(hit.get('snippet'), str) or not hit['snippet'].strip():
                    raise Failure('invalid_answer', 502)
                original = hit.get('original_text') or hit['snippet']
                if not isinstance(original, str):raise Failure('invalid_answer', 502)
                status = hit.get('source_status')
                if status is not None and not isinstance(status, str):raise Failure('invalid_answer', 502)
                rendered = (hit.get('title') or definition['display_name']) + '\n\n' + (('Status: ' + status + '\n\n') if status else '') + original
                source = {'title': hit['title'], 'source_label': definition['display_name'], 'excerpt': rendered,
                          'url': hit.get('url'), 'locator': {k: hit[k] for k in ('section', 'subsection', 'page_start', 'page_end') if hit.get(k) is not None},
                          'bibliography': {'record_id': hit['resource_id'], 'rights_mode': hit['rights_mode'],
                              **{key:hit[key]for key in ('source_revision','source_status')if hit.get(key)is not None}}}
                item=match(hit, 'resource_record', definition['display_name'], rendered, source, locale, hit.get('score'))
                if hit['rights_mode']=='linked_only':item['context_eligible']=False
                matches.append(item)
        if coverage['status'] != 'partial':
            coverage['status'] = 'ok' if hits else 'no_match'
        return matches, coverage
    except Exception as error:
        coverage['status'] = 'partial' if matches else 'timeout' if isinstance(error, Failure) and error.code == 'source_timeout' else 'unavailable'
        coverage.update(diagnostic(error, 'source_retrieval'))
        return matches, coverage
    finally:
        if detail_started is not None:
            coverage['detail_ms'] = round((time.monotonic() - detail_started) * 1000, 3)
        coverage['elapsed_ms'] = round((time.monotonic() - started) * 1000, 3)


def collect_tools(gateway, family):
    """Retain existing explicitly requested CPU operations and their authority."""
    started=time.monotonic()
    status={'family':'linghub_tools','source_label':'LingHub','status':'unavailable',
            'returned_count':0,'limit':None,'has_more':None}
    try:
        capture=collation.collect(gateway,family)
        if capture is None:return None,None
        if capture['plan']['status']!='ready':
            status.update(status='unsupported',reason='explicit_tool_context_required')
            return None,status
        # These are typed tool results, not a corpus of documentary records.
        status.update(status='not_documentary',capture_count=len(capture['captures']))
        return capture,status
    except (Failure,ValueError,KeyError,TypeError)as error:
        status.update(status='timeout'if isinstance(error,Failure)and error.code=='source_timeout'else'unavailable',
                      **diagnostic(error,'explicit_tool_retrieval'))
        return None,status
    finally:status['elapsed_ms']=round((time.monotonic()-started)*1000,3)


def freeze(gateway, family, policy):
    started = time.monotonic()
    stage_started = started
    timings = {}
    coverage, found = [], []
    try:
        definitions = registry(gateway) if gateway.klikai_url else []
        if not gateway.klikai_url:
            coverage.append({'family': 'configured_sources', 'status': 'unavailable', 'returned_count': 0, 'limit': None, 'has_more': None})
    except Exception as error:
        definitions = []
        coverage.append({'family': 'configured_sources', 'status': 'timeout' if isinstance(error,Failure)and error.code == 'source_timeout' else 'unavailable', 'returned_count': 0, 'limit': None, 'has_more': None, **diagnostic(error,'source_registry')})
    timings['registry_ms'] = round((time.monotonic() - stage_started) * 1000, 3)
    stage_started = time.monotonic()
    with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as details, ThreadPoolExecutor(max_workers=8) as pool:
        tool_future=pool.submit(collect_tools,gateway,family)
        futures = [pool.submit(collect_source, gateway, family, row, details) for row in definitions]
        for future in as_completed(futures):
            matches, status = future.result()
            found.extend(matches)
            coverage.append(status)
        tool_capture,tool_status=tool_future.result()
        if tool_status is not None:coverage.append(tool_status)
    timings['source_collection_ms'] = round((time.monotonic() - stage_started) * 1000, 3)
    stage_started = time.monotonic()
    # Same wording across collections/senses is never a duplicate identity.
    dedup = {}
    for item in sorted(found, key=lambda x: (-x['score'], x['source_slug'], x['external_id'], x['capture_sha256'])):
        key = (item['source_slug'], item['external_id'], item['capture_kind'], item['capture_sha256'])
        dedup.setdefault(key, item)
    found = list(dedup.values())
    coverage.sort(key=lambda x: x['family'])
    external = [{k: deepcopy(v) for k, v in item.items() if k != 'source'} for item in found]
    packet = {'version': PACKET, 'matches': external, 'retrieval': coverage}
    if len(canonical(packet).encode()) > MAX_BYTES:
        raise Failure('unified_capture_too_large', 413)
    timings['evidence_preparation_ms'] = round((time.monotonic() - stage_started) * 1000, 3)
    child = None
    stage_started = time.monotonic()
    try:
        response = gateway.request('/internal/pilot/freeze', {'question': family['question'],
            'context': family.get('context', ''), 'locale': family['locale'], 'task': 'reference',
            'policy': policy, 'unified_evidence': packet,
            **({'tool_evidence':tool_capture}if tool_capture is not None else{})})
        remote = response.get('matches')
        if not isinstance(response.get('bundle'), dict) or not isinstance(remote, list):
            raise Failure('invalid_answer', 502)
        by_capture = {item['capture_sha256']: item for item in found}
        for item in remote:
            if not isinstance(item, dict) or not isinstance(evidence_id(item), str) or not isinstance(item.get('source'), dict):
                raise Failure('invalid_answer', 502)
            view=item['source']
            item['source']={key:deepcopy(view[key])for key in ('title','url','locator','status','edition','publication_scope','annual_scope')if key in view}
            if 'status' not in view and 'source_status' in view:
                item['source']['status']=deepcopy(view['source_status'])
            item['source'].update(source_label=view.get('source_label')or item.get('source_label')or item['source_slug'],
                excerpt=view.get('excerpt',view.get('snippet','')),
                bibliography=deepcopy(view.get('bibliography',view.get('bibliographic',{}))))
            if not isinstance(item['source']['excerpt'],str)or not item['source']['excerpt']:
                raise Failure('invalid_answer',502)
            item['snippet']=item.get('snippet','')[:1000]
            original=by_capture.get(item.get('capture_sha256'))
            if original and original.get('detail_status'):
                item['detail_status']=original['detail_status']
                item['source']['excerpt']=editorial.notice(family['locale'])+'\n\n'+item['source']['excerpt']
        # Gateway pins citation IDs and contributes its current ZRC resources.
        # Require every source-returned external capture to remain browsable.
        remote_pins = {item.get('capture_sha256') for item in remote}
        if not set(by_capture) <= remote_pins:
            raise Failure('incomplete_unified_capture', 502)
        found = deepcopy(remote)
        child = deepcopy(response['bundle'])
        extra = response.get('retrieval', [])
        if isinstance(extra, list):
            known = {row['family'] for row in coverage}
            coverage.extend(row for row in extra if isinstance(row, dict) and row.get('family') not in known)
    except (Failure, KeyError, TypeError, ValueError) as error:
        coverage.append({'family': 'reference_gateway', 'status': 'timeout' if isinstance(error, Failure) and error.code == 'source_timeout' else 'unavailable', 'returned_count': 0, 'limit': None, 'has_more': None, **diagnostic(error,'gateway_freeze')})
    timings['gateway_freeze_ms'] = round((time.monotonic() - stage_started) * 1000, 3)
    statuses = {row['status'] for row in coverage}
    retrieval = {'status': 'partial' if statuses & {'partial', 'timeout', 'unavailable', 'unsupported'} else 'complete',
                 'coverage': coverage, 'elapsed_ms': round((time.monotonic() - started) * 1000, 3),
                 'returned_count': len(found), 'timings': timings}
    if not found and retrieval['status'] == 'partial':retrieval['status'] = 'unavailable'
    body = {'contract': CONTRACT, 'task': 'reference', 'query': family['question'], 'locale': family['locale'],
            'matches': found, 'retrieval': retrieval, 'gateway_bundle': child}
    if len(canonical(body).encode()) > 2*MAX_BYTES:
        raise Failure('unified_capture_too_large', 413)
    pin = digest(body)
    return {'bundle': dict(body, snapshot_sha256=pin),
            'identity': {'contract': CONTRACT, 'captured_response_sha256': pin},
            'timings': {'retrieval_and_freeze_ms': retrieval['elapsed_ms'], **timings}}


def fallback(bundle, status, elapsed=0.0, error=None):
    it = bundle['locale'] == 'it'
    message = ('Nessuna fonte di supporto trovata.' if it else 'Ni bilo najdenih podpornih virov.') if status == 'no_support' else (
        'La risposta generata non è disponibile. Puoi consultare i risultati salvati.' if it else
        'Generirani odgovor ni na voljo. Pregledate lahko shranjene zadetke.')
    return {'status': 'no_hit' if status == 'no_support' else 'source_only', 'answer_kind': 'source_only',
            'generation_status': 'not_generated', 'short_answer': message, 'answer_md': message,
            'display_blocks': [{'block_id': 'unified-status', 'text': message}], 'evidence': [], 'source_badges': [],
            'limitations': [message], 'retrieval': deepcopy(bundle['retrieval']),
            'generation': {'status': status, 'provider': {}, 'elapsed_ms': elapsed, 'first_content_ms': None, **(error or {})}}


def generate(gateway, bundle, tier, model, parent=None):
    checked(bundle)
    if not bundle['matches']:return fallback(bundle, 'no_support' if bundle['retrieval']['status'] == 'complete' else 'unavailable')
    if not any(row.get('context_eligible',True)for row in bundle['matches']):return fallback(bundle,'no_support')
    if bundle['gateway_bundle'] is None:return fallback(bundle, 'unavailable')
    started = time.monotonic()
    stage = 'provider_request'
    result = None
    try:
        result = gateway.request('/internal/pilot/generate', {'bundle': bundle['gateway_bundle'],
            'tier': tier, 'model': model, 'parent_answer': parent})
        if not isinstance(result, dict):raise Failure('invalid_answer', 502)
        stage = 'citation_validation'
        known = {evidence_id(row) for row in bundle['matches']}
        evidence = result.get('evidence', [])
        citations = result.get('citations', [])
        if (not isinstance(evidence, list) or not isinstance(citations, list) or any(
                not isinstance(row, dict) or evidence_id(row) not in known for row in evidence + citations)):
            raise Failure('unknown_citation', 502)
        for row in evidence + citations:
            aliases = [row[key] for key in ('evidence_id', 'passage_id', 'citation_id')
                       if row.get(key) is not None and row[key] != '']
            if any(not isinstance(value, str) or value != evidence_id(row) for value in aliases):
                raise Failure('unknown_citation', 502)
        # A successful transport or an excerpt with badges is not generation.
        generated = result.get('answer_kind') in {'generated', 'mixed'} and result.get('status') in {'validated', 'partial'}
        if generated:
            stage='provider_identity'
            execution=result.get('execution',{});identity=result.get('model_identity',{})
            if not isinstance(execution,dict)or not isinstance(identity,dict):raise Failure('provider_identity_missing',502)
            selection=execution.get('selection');scope=execution.get('provider_scope')
            if model=='auto':
                if selection!='auto'or scope not in {'shared','own'}or not(identity.get('repository')or identity.get('model_id')):
                    raise Failure('provider_identity_missing',502)
            elif selection is not None or scope is not None:
                if selection!=model or scope!='own':raise Failure('provider_selection_mismatch',502)
            stage='citation_validation'
        if generated and not evidence:raise Failure('unknown_citation', 502)
        if generated:
            numbers={str(row.get('local_number')or row.get('number')or i+1)for i,row in enumerate(evidence)}
            markers=set(re.findall(r'\[(\d{1,3})\]',result.get('answer_md','')))
            if not markers<=numbers:raise Failure('unknown_citation',502)
            for i,row in enumerate(evidence):
                # The producer's source view uses citation_id. Expose the same
                # validated identity to canonical UI/probe consumers without
                # removing its aliases or changing signed prose/source metadata.
                row['evidence_id']=evidence_id(row)
                row['local_number']=row.get('local_number')or row.get('number')or i+1
            # The gateway's signed generated prose uses claim-to-source mappings,
            # not necessarily textual [n] markers. Preserve its signed answer;
            # the UI renders the mapped citations separately from source text.
            blocks=result.get('display_blocks',[])
            cited={evidence_id(row)for row in citations}
            visible={evidence_id(row)for row in evidence}
            if cited and cited!=visible:raise Failure('unknown_citation',502)
            if not markers:
                if not isinstance(blocks,list)or not blocks:raise Failure('unknown_citation',502)
                for block in blocks:
                    ids=block.get('evidence_ids')if isinstance(block,dict)else None
                    if (not isinstance(ids,list)or not ids or any(eid not in cited for eid in ids)
                            or set(ids)!={evidence_id(c)for c in citations if c.get('claim_id')==block.get('block_id')}):
                        raise Failure('unknown_citation',502)
        elapsed = round((time.monotonic() - started) * 1000, 3)
        if not generated:
            from .human_feedback import provider_diagnostics
            diagnostics=provider_diagnostics(result)
            out = fallback(bundle, 'failed', elapsed, {'error_code':diagnostics.get('provider_failure',{}).get('code','generation_not_admitted'),'stage':'provider_result'})
            out.update(diagnostics)
            for key in ('model_identity', 'execution', 'usage'):
                if key in result:out[key] = result[key]
            from .human_feedback import public_execution
            public=public_execution(out)
            out['generation']['provider']={**public.get('model_identity',{}),**public.get('execution',{})}
            return out
        result['generation_status'] = 'generated'
        result['retrieval'] = deepcopy(bundle['retrieval'])
        result['generation'] = {'status': 'generated', 'provider': {}, 'elapsed_ms': elapsed, 'first_content_ms': None}
        from .human_feedback import public_execution
        public = public_execution(result)
        result['generation']['provider'] = {**public.get('model_identity', {}), **public.get('execution', {})}
        return result
    except (Failure, KeyError, TypeError, ValueError) as error:
        out = fallback(bundle, 'failed', round((time.monotonic() - started) * 1000, 3), diagnostic(error,stage))
        if isinstance(result, dict):
            from .human_feedback import provider_diagnostics
            out.update(provider_diagnostics(result))
        return out


def source(bundle, eid):
    checked(bundle)
    for item in bundle['matches']:
        if evidence_id(item) == eid:
            return dict(deepcopy(item['source']), evidence_id=eid)
    raise Failure('not_found', 404)


def matches_page(bundle, used, offset=0, limit=20):
    checked(bundle)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
        raise Failure('invalid_request')
    rows = []
    for item in bundle['matches'][offset:offset + limit]:
        row = {key: deepcopy(item[key]) for key in ('source_slug', 'source_label', 'external_id', 'title', 'snippet', 'url', 'rights_mode', 'detail_status') if key in item}
        row.update(evidence_id=evidence_id(item), used_in_answer=evidence_id(item) in used)
        rows.append(row)
    count = len(bundle['matches'])
    return {'items': rows, 'offset': offset, 'limit': limit, 'returned_count': count,
            'next_offset': offset + limit if offset + limit < count else None,
            'corpus_total': None, 'coverage': deepcopy(bundle['retrieval']['coverage'])}
