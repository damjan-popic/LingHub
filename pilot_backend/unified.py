"""Normal-query retrieval, owned match browsing and honest generation fallback.

Documentary families come from the configured active registry. Linguistic tools
remain tools; neither a source outage nor a bounded response proves no matches.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
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
MAX_FROZEN_BYTES = 8 * 1024 * 1024
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


def merge_source_matches(matches):
    """Interleave source-local ranks without comparing unlike score scales.

    Keep each complete immutable capture. Stable source/id tie breakers remove
    thread-completion order from the merge; gateway question relevance decides
    which records may enter the writer context afterwards.
    """
    groups={}
    for item in sorted(matches,key=lambda row:(row['source_slug'],-score(row['score']),row['external_id'],row['capture_sha256'])):
        key=(item['external_id'],item['capture_kind'],item['capture_sha256'])
        groups.setdefault(item['source_slug'],{}).setdefault(key,item)
    ranked=[list(group.values())for _,group in sorted(groups.items())]
    return [group[index]for index in range(max(map(len,ranked),default=0))for group in ranked if index<len(group)]


def diagnostic(error, stage):
    """Fixed public classifications; exception messages may contain private data."""
    codes = {'answer_unavailable', 'source_timeout', 'invalid_answer', 'unknown_citation',
             'incomplete_unified_capture', 'resource_changed', 'generation_waiting_capacity',
             'provider_identity_missing', 'provider_selection_mismatch'}
    from .human_feedback import PROVIDER_FAILURE_CODES
    codes |= PROVIDER_FAILURE_CODES
    code = error.code if isinstance(error, Failure) and error.code in codes else 'invalid_gateway_response'
    status=getattr(error,'http_status',None)
    from .human_feedback import safe_exception
    return {'technical_cause':safe_exception(error),'error_code': code, 'stage': stage, **({'http_status':status}if type(status)is int and 100<=status<=599 else{})}


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
    request_plan = family.get('request_plan') or {}
    targets = request_plan.get('target_spans', [])
    if len(targets) == 1 and request_plan.get('original') == family.get('original_question', family['question']):
        target = targets[0]
        original = request_plan['original'] + ('\n' + request_plan.get('context','') if target.get('span_scope') == 'context' else '')
        a, b = target.get('start'), target.get('end')
        if type(a) is int and type(b) is int and 0 <= a < b <= len(original) and original[a:b] == target.get('text'):
            # Exact original expression first; the resource owns any inflectional
            # expansion. Explicit domain remains visible in the request plan.
            query = target['text']
    variants = list(dict.fromkeys([query, family['question']]))
    limit = 20 if adapter in TERM_ADAPTERS else 3 if slug in editorial.SOURCES else 10
    coverage = {'family': slug, 'source_label': definition['display_name'], 'status': 'unavailable', 'returned_count': 0,
                'limit': limit, 'has_more': None, 'elapsed_ms': 0.0,
                'search_ms': 0.0, 'detail_ms': 0.0, 'detail_requests': 0,
                'detail_reused': 0, 'details': [],'query':query,'query_variants':[],'original_question':family['question'],'request_plan':deepcopy(request_plan) or None,'adapter':adapter,'score_method':'source_native_unspecified','candidates':[]}
    matches = []
    detail_started = None
    try:
        if definition.get('source_type') == 'tool' or adapter in NON_DOCUMENTARY:
            coverage['status'] = 'not_documentary'
            return matches, coverage
        minimum = 1 if adapter == 'official_reference_jsonl' else 2
        if not minimum <= len(query) <= 500:
            coverage.update(status='unsupported', reason='query_outside_search_bound')
            return matches, coverage
        search_started = time.monotonic()
        try:
            for query in variants:
                coverage['query_variants'].append(query)
                coverage['query'] = query
                if adapter in TERM_ADAPTERS:
                    params = {'q': query, 'sources': slug, 'limit': limit,
                              'allow_partial': 'true', 'include_prose': 'true', 'use_morphology': 'true'}
                    data = gateway.request('/api/terminology/lookup?' + urlencode(params), None, gateway.klikai_url)
                else:
                    data = gateway.request('/api/search?' + urlencode({'q': query, 'sources': slug, 'limit': limit}), None, gateway.klikai_url)
                if data.get('hits'):
                    break
        finally:
            coverage['search_ms'] = round((time.monotonic() - search_started) * 1000, 3)
        hits = data.get('hits')
        if (data.get('query') != query or data.get('sources') != [slug] or not isinstance(hits, list)
                or len(hits) > limit or type(data.get('count')) is not int or data['count'] != len(hits)):
            raise Failure('invalid_answer', 502)
        coverage['returned_count'] = len(hits)
        coverage['candidates']=[{'native_rank':i+1,**{k:deepcopy(h[k]) for k in ('resource_id','external_id','source_slug','score','score_method','matched_forms','accepted_matched_forms','source_revision') if k in h}} for i,h in enumerate(hits)]
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
                              **{key:hit[key]for key in ('source_revision','source_status','source_date','release_id','coverage_note','normative_status')if hit.get(key)is not None}}}
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
    """Freeze requested operations or an applicable automatic LORIS rule check."""
    started=time.monotonic()
    status={'family':'linghub_tools','source_label':'LingHub','status':'unavailable',
            'returned_count':0,'limit':None,'has_more':None}
    try:
        capture=collation.collect(gateway,family,automatic=True)
        if capture is None:return None,dict(status,status='not_documentary',tool_outcome='not_invoked',reason='no_applicable_lookup_route')
        if capture['plan']['status']!='ready':
            status.update(status='unsupported',reason='explicit_tool_context_required')
            return None,status
        # These are typed tool results, not a corpus of documentary records.
        status.update(status='not_documentary',capture_count=len(capture['captures']))
        if capture['plan'].get('routing_kind') in {'automatic_question_rule_lookup','single_word_rule_lookup'}:
            parts=[row['response']['results']['loris'] for row in capture['captures'] if row['operation']=='explore']
            part=next((p for p in parts if p['status']=='ok'),parts[0])
            outcome=part['status']
            if all(p['status']=='no_hit' for p in parts):outcome='no_hit'
            elif not any(p['status']=='ok' for p in parts) and any(p['status'] in {'unavailable','error'} for p in parts):outcome='unavailable'
            status['lookup_outcomes']=[{'language':c['request']['lang'],'status':c['response']['results']['loris']['status']} for c in capture['captures'] if c['operation']=='explore']
            if outcome not in {'ok','no_hit','unavailable','unsupported','error'}:
                raise Failure('invalid_answer',502)
            data=part.get('data')or{}
            rule_count=len({(row.get('rule_id'),row.get('start'),row.get('end'))
                            for row in data.get('issues',[])+data.get('spans',[])})
            status.update(evidence_type='tool_result',routing_kind=capture['plan']['routing_kind'],
                          operation_statuses={'loris':outcome},rule_count=rule_count,
                          match_scope='matched_span_only',normative_conclusion='not_established',
                          tool_outcome='no_match'if outcome=='no_hit'else'matched'if outcome=='ok'else'unavailable')
            if outcome=='no_hit':status['status']='no_match'
            elif outcome in {'unavailable','error'}:status['status']='unavailable'
            elif outcome=='unsupported':status['status']='unsupported'
        return capture,status
    except (Failure,ValueError,KeyError,TypeError)as error:
        status.update(status='timeout'if isinstance(error,Failure)and error.code=='source_timeout'else'unavailable',
                      **diagnostic(error,'tool_retrieval'))
        return None,status
    finally:status['elapsed_ms']=round((time.monotonic()-started)*1000,3)


def freeze(gateway, family, policy):
    # One request plan travels to source discovery, lexical profiles, writer and
    # cards. Family/history text remains immutable; only this request copy is used.
    from core.lexical_planner import plan as lexical_plan
    family = deepcopy(family)
    original = family.get('original_question', family['question'])
    family['request_plan'] = lexical_plan(original, family.get('context', ''),
        family.get('input_language', 'und'), {'raw_engines': {}})['request_plan']
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
    found = merge_source_matches(found)
    coverage.sort(key=lambda x: x['family'])
    external = [{k: deepcopy(v) for k, v in item.items() if k != 'source'} for item in found]
    packet = {'version': PACKET, 'matches': external, 'retrieval': coverage}
    if len(canonical(packet).encode()) > MAX_BYTES:
        raise Failure('unified_capture_too_large', 413)
    timings['evidence_preparation_ms'] = round((time.monotonic() - stage_started) * 1000, 3)
    child = None
    stage_started = time.monotonic()
    try:
        # Freeze the same canonical JSON representation that the durable store
        # will retain. Source values/hashes are unchanged, but field iteration
        # must not depend on insertion order lost at the storage boundary.
        freeze_request = json.loads(canonical({'question': family['question'],
            'context': family.get('context', ''), 'locale': family['locale'], 'task': 'reference',
            'policy': policy, 'unified_evidence': packet, 'request_plan': family['request_plan'],
            **({'tool_evidence':tool_capture}if tool_capture is not None else{})}))
        response = gateway.request('/internal/pilot/freeze', freeze_request)
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
            if isinstance(view.get('original_json'),dict):item['source']['excerpt']=json.dumps(view['original_json'],ensure_ascii=False,indent=2)
            if not isinstance(item['source']['excerpt'],str)or not item['source']['excerpt']:
                raise Failure('invalid_answer',502)
            item['snippet']=item.get('snippet','')[:1000]
            original=by_capture.get(item.get('capture_sha256'))
            if original:
                item['source']['bibliography'].update({key:deepcopy(value)
                    for key,value in original.get('bibliography',{}).items()
                    if key in {'source_status','source_date','release_id','coverage_note','normative_status'}})
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
    body = {'analysis':tool_capture,'contract': CONTRACT, 'task': 'reference', 'query': family['question'], 'locale': family['locale'],
            'matches': found, 'retrieval': retrieval, 'gateway_bundle': child}
    # Keep the full source explorer beside the signed gateway family. The
    # original capture/request bound remains MAX_BYTES.
    if len(canonical(body).encode()) > MAX_FROZEN_BYTES:
        raise Failure('unified_capture_too_large', 413)
    pin = digest(body)
    return {'bundle': dict(body, snapshot_sha256=pin),
            'identity': {'contract': CONTRACT, 'captured_response_sha256': pin},
            'timings': {'retrieval_and_freeze_ms': retrieval['elapsed_ms'], **timings}}


def fallback(bundle, status, elapsed=0.0, error=None):
    it = bundle['locale'] == 'it'
    no_support = ('Mi dispiace, per questa domanda non trovo nelle fonti una risposta adeguata.' if it else
                  'Oprostite, za to vprašanje v virih ne najdem pravega odgovora.')
    message = no_support if status == 'no_support' else (
        'La risposta generata non è disponibile. Puoi consultare i risultati salvati.' if it else
        'Generirani odgovor ni na voljo. Pregledate lahko shranjene zadetke.')
    code = (error or {}).get('error_code')
    if code in {'invalid_composition_envelope', 'all_claims_withheld'}:
        message = ('Non è stato possibile formulare una risposta sufficientemente supportata. Puoi consultare i risultati salvati.' if it else
                   'Ni bilo mogoče oblikovati dovolj podprtega odgovora. Pregledate lahko shranjene zadetke.')
    elif code == 'incomplete_source':
        message = ('Il testo completo delle fonti non è disponibile. Puoi consultare i risultati trovati.' if it else
                   'Celotno besedilo virov ni na voljo. Pregledate lahko najdene zadetke.')
    return {'status': 'no_hit' if status == 'no_support' else 'source_only', 'answer_kind': 'source_only',
            'generation_status': 'not_generated', 'short_answer': message, 'answer_md': message,
            'display_blocks': [{'block_id': 'unified-status', 'text': message}], 'evidence': [], 'source_badges': [],
            'limitations': [message], 'retrieval': deepcopy(bundle['retrieval']),
            'generation': {'status': status, 'provider': {}, 'elapsed_ms': elapsed, 'first_content_ms': None, **(error or {})}}


def undelivered_reason(result):
    """Classify observed stages; never infer admission failure from no prose."""
    from .human_feedback import provider_diagnostics, generation_validation_diagnostics
    provider = provider_diagnostics(result).get('provider_failure', {})
    if provider:
        return 'failed', {'error_code': provider['code'], 'stage': provider.get('stage', 'provider_result')}
    validation = generation_validation_diagnostics(result)
    status = validation.get('validation', {}).get('status')
    if status in {'invalid_composition_envelope', 'all_claims_withheld'}:
        return 'failed', {'error_code': status, 'stage': 'composition_validation'}
    reasons = validation.get('limitation_reasons', [])
    if any(reason in reasons for reason in ('empty_indexed_evidence', 'model_found_insufficient_evidence')):
        return 'no_support', {'error_code': 'insufficient_selected_evidence', 'stage': 'evidence_selection'}
    return 'failed', {'error_code': 'undelivered_answer', 'stage': 'provider_result'}


def _generate(gateway, bundle, tier, model, parent=None):
    checked(bundle)
    if not bundle['matches']:return fallback(bundle, 'no_support' if bundle['retrieval']['status'] == 'complete' else 'unavailable')
    if not any(row.get('context_eligible',True)for row in bundle['matches']):
        incomplete = any(row.get('detail_status') in {'timeout', 'unavailable', 'partial'} for row in bundle['matches'])
        return (fallback(bundle, 'unavailable', error={'error_code': 'incomplete_source', 'stage': 'source_detail'})
                if incomplete else fallback(bundle, 'no_support'))
    if bundle['gateway_bundle'] is None:return fallback(bundle, 'unavailable')
    started = time.monotonic()
    stage = 'provider_request'
    result = None
    try:
        result = gateway.request('/internal/pilot/generate', {'bundle': bundle['gateway_bundle'],
            'tier': ('lexical_overview'if tier=='short'and __import__(__package__+'.lexical',fromlist=['overview']).overview(bundle)else tier), 'model': model, 'parent_answer': parent})
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
        excerpt = (result.get('answer_kind') == 'source_only' and result.get('status') in {'validated', 'partial'}
                   and result.get('generation_status') == 'source_supported_partial'
                   and result.get('synthesis_mode') == 'source_supported_partial'
                   and isinstance(result.get('validation'), dict)
                   and result['validation'].get('status') == 'bounded_checks_completed')
        deliverable = generated or excerpt
        if deliverable:
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
        if deliverable and not evidence:raise Failure('unknown_citation', 502)
        if deliverable:
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
                    if 'paragraph_id' in block:
                        claim=next((c for c in result.get('claims',[]) if c.get('claim_id')==block.get('block_id')),None)
                        if not claim or claim.get('paragraph_id')!=block['paragraph_id']:
                            raise Failure('unknown_citation',502)
        elapsed = round((time.monotonic() - started) * 1000, 3)
        if not deliverable:
            from .human_feedback import provider_diagnostics,generation_validation_diagnostics
            diagnostics=provider_diagnostics(result)
            validation=generation_validation_diagnostics(result)
            if validation:diagnostics['generation_validation']=validation
            outcome, reason = undelivered_reason(result)
            out = fallback(bundle, outcome, elapsed, reason)
            out.update(diagnostics)
            if 'diagnostics' in result:out['diagnostics']=deepcopy(result['diagnostics'])
            for key in ('model_identity', 'execution', 'usage'):
                if key in result:out[key] = result[key]
            from .human_feedback import public_execution
            public=public_execution(out)
            out['generation']['provider']={**public.get('model_identity',{}),**public.get('execution',{})}
            return out
        result['generation_status'] = ('source_supported_partial' if excerpt else
            'mixed_generated_and_source_partial' if result.get('synthesis_mode') == 'mixed_generated_and_source_partial' else 'generated')
        if excerpt:result['status'] = 'partial'
        result['retrieval'] = deepcopy(bundle['retrieval'])
        result['generation'] = {'status': 'partial' if excerpt or result.get('status') == 'partial' else 'generated', 'provider': {}, 'elapsed_ms': elapsed, 'first_content_ms': None}
        from .human_feedback import public_execution
        public = public_execution(result)
        result['generation']['provider'] = {**public.get('model_identity', {}), **public.get('execution', {})}
        return result
    except (Failure, KeyError, TypeError, ValueError) as error:
        out = fallback(bundle, 'failed', round((time.monotonic() - started) * 1000, 3), diagnostic(error,stage))
        if isinstance(result, dict):
            from .human_feedback import provider_diagnostics,generation_validation_diagnostics
            out.update(provider_diagnostics(result))
            validation=generation_validation_diagnostics(result)
            if validation:out['generation_validation']=validation
            if 'diagnostics' in result:out['diagnostics']=deepcopy(result['diagnostics'])
        return out


def source(bundle, eid):
    checked(bundle)
    for item in bundle['matches']:
        if evidence_id(item) == eid:
            view=dict(deepcopy(item['source']), evidence_id=eid)
            try:current_original_discovery(bundle,eid)
            except Failure:pass
            else:view.update(current_original_lookup=True,original_view='historical_unavailable')
            return view
    raise Failure('not_found', 404)


def current_original_discovery(bundle,eid):
    """Resolve only an unavailable editorial identity from this frozen bundle.

    Remote gateway match projections omit full discovery captures; their pinned
    originals remain in the gateway payload. A failed gateway can instead leave
    the same capture directly in the local match. Neither form accepts a new
    resource ID from the browser or changes the answer's historical evidence.
    """
    checked(bundle)
    item=next((row for row in bundle['matches']if evidence_id(row)==eid),None)
    if (not item or item.get('source_slug')not in editorial.SOURCES or
            item.get('capture_kind')not in {None,'resource_record'} or
            item.get('detail_status')not in {'unavailable','timeout'}):
        raise Failure('not_found',404)
    child=bundle.get('gateway_bundle')or{}
    retained=(child.get('payload')or{}).get('unified_evidence')or{}
    candidates=[item,*retained.get('matches',[])]
    original=next((row for row in candidates if isinstance(row.get('capture'),dict)
        and row.get('capture_kind')=='resource_record'
        and row.get('source_slug')==item['source_slug']
        and row.get('external_id')==item.get('external_id')
        and row.get('capture_sha256')==item.get('capture_sha256')
        and digest(row['capture'])==item.get('capture_sha256')),None)
    if original is None:raise Failure('not_found',404)
    hit=original['capture']
    if hit.get('source_slug')!=item['source_slug']or hit.get('resource_id')!=item.get('external_id'):
        raise Failure('not_found',404)
    discovery=editorial.freeze({'query':bundle['query'],'sources':[item['source_slug']],
        'count':1,'hits':[hit]},bundle['query'],item['source_slug'],bundle['locale'])['bundle']
    return discovery


def current_original(gateway,bundle,eid):
    """Explicit supplementary read; no generation, persistence or recitation."""
    discovery=current_original_discovery(bundle,eid)
    if not gateway.klikai_url or not gateway.editorial_full_records:
        raise Failure('source_unavailable',503)
    hit=discovery['hits'][0]
    path='/api/editorial/records/'+quote(hit['source_slug'],safe='')+'/'+quote(hit['resource_id'],safe='')
    record=gateway.request(path,None,gateway.klikai_url)
    current=editorial.freeze_records(discovery,[record],gateway.editorial_operator_policy_id)['bundle']
    source=editorial.record_source(current,editorial.record_evidence(current)[0]['evidence_id'])
    # The owned historical evidence ID scopes the request, not the fresh text's
    # citation status. Its distinct view label is mandatory in the reader.
    return dict(source,evidence_id=eid,original_view='supplementary_current')


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


def record_groups(bundle, used, usage_final=True):
    """Group stored occurrences by native source/record/edition, never by title.

    Source slugs remain registry identities, not ontology labels. Every exact
    passage target survives in evidence_ids, including uncited/later-page hits.
    If no native record identity exists, retain the separate evidence target.
    """
    checked(bundle)
    grouped = {}
    for item in bundle['matches']:
        source = item.get('source', {})
        locator = source.get('locator') or {}
        external = item.get('external_id') or source.get('external_id') or evidence_id(item)
        edition = source.get('edition') or item.get('edition')
        revision = (item.get('source_revision') or source.get('source_revision')
                    or item.get('capture_sha256') or locator.get('capture_sha256'))
        occurrence = locator.get('post_id') or locator.get('entry_id') or source.get('entry_identity')
        key = canonical([item['source_slug'], external, edition, revision, occurrence])
        if key not in grouped:
            bibliography = source.get('bibliography') or item.get('bibliography') or {}
            grouped[key] = {'record_id': 'record-' + str(len(grouped) + 1),
                'source_slug': item['source_slug'],
                'source_label': item.get('source_label') or source.get('source_label') or item['source_slug'],
                'title': item.get('title') or source.get('title') or item.get('source_label') or source.get('source_label') or 'Vir',
                'preview': (item.get('snippet') or '')[:220],
                'evidence_id': evidence_id(item), 'evidence_ids': [],
                'edition': edition, 'status': source.get('status') or item.get('source_status'),
                'bibliography': {k: deepcopy(bibliography[k]) for k in
                    ('authors', 'date', 'signed_date_text', 'publisher', 'publication_date', 'title')
                    if k in bibliography}, 'used_in_answer': None if not usage_final else False}
        row = grouped[key]
        target = evidence_id(item)
        if target not in row['evidence_ids']:row['evidence_ids'].append(target)
        if usage_final and target in used:row['used_in_answer'] = True
    return list(grouped.values())


def source_summary(bundle, used, usage_final=True):
    groups = record_groups(bundle, used, usage_final)
    sources = {}
    for item in groups:
        source = sources.setdefault(item['source_slug'], {'source_slug': item['source_slug'],
            'source_label': item['source_label'], 'returned_count': 0})
        source['returned_count'] += 1
    return {'sources': list(sources.values()), 'returned_count': len(groups),
            'count_scope': 'returned_unique_records', 'usage_final': usage_final}


def source_records_page(bundle, used, source, offset=0, limit=20, usage_final=True):
    if (not isinstance(source, str) or not source or type(offset) is not int or offset < 0
            or type(limit) is not int or not 1 <= limit <= 50):raise Failure('invalid_request')
    rows = [r for r in record_groups(bundle, used, usage_final) if r['source_slug'] == source]
    count = len(rows)
    return {'items': rows[offset:offset + limit], 'source_slug': source, 'offset': offset,
        'limit': limit, 'returned_count': count, 'next_offset': offset + limit if offset + limit < count else None,
        'count_scope': 'returned_unique_records', 'usage_final': usage_final, 'corpus_total': None}


def generate(gateway,bundle,tier,model,parent=None):
    from .lexical import reader
    result=_generate(gateway,bundle,tier,model,parent)
    lexical=reader(bundle)
    if lexical:result['lexical_profile']=lexical
    return result
