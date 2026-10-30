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
import unicodedata
from urllib.parse import quote, urlencode

from . import collation, editorial, terminology
from .security import Failure
from .store import canonical, digest

CONTRACT = 'klikai.pilot.unified-capture.v1'
PACKET = 'klikai.unified-evidence.v1'
MAX_BYTES = 2 * 1024 * 1024
MAX_FROZEN_BYTES = 8 * 1024 * 1024
DETAIL_WORKERS = 4
REFERENCE_LEMMA_LIMIT = 8
REFERENCE_LOOKUP_REQUEST_LIMIT = 16
TERM_ADAPTERS = {'luka_jsonl_terminology', 'luka_jsonl_decision'}
NON_DOCUMENTARY = {'linghub', 'external_tool', 'ontology_v2_jsonl', 'xlsx_source_register'}
ANSWER_PLAN_CLAIM_UNIT_TYPES = frozenset({'documentary_fact','construction_application',
    'terminology_equivalence','comparison_synthesis','curated_recommendation','bounded_attestation',
    'exact_source_excerpt','grammar_slot'})
ANSWER_PLAN_BLOCK_FIELDS = frozenset({'block_id','paragraph_id','unit_id','unit_type',
    'text','claim_id','support_ids'})
ANSWER_PLAN_CLAIM_FIELDS = frozenset({'claim_id','text','support_ids','fact_ids','section',
    'paragraph_id','depends_on','unit_id','support_mode'})
ANSWER_PLAN_CITATION_FIELDS = frozenset({'block_id','claim_id','unit_id','evidence_id',
    'support_id','source_key','snippet','locator','char_start','char_end'})
ANSWER_PLAN_CITATION_OPTIONAL_FIELDS = frozenset({'source_id','source_revision'})
ANSWER_PLAN_COVERAGE_LIMITATION_FIELDS = frozenset({'code','scope','claim_ids','unit_id',
    'message','coverage_receipt_id','coverage_receipt_sha256'})
ANSWER_PLAN_PROCESSING_LIMITATION_FIELDS = frozenset({'code','scope','claim_ids','unit_id'})
ANSWER_PLAN_TASK_LIMITATION_FIELDS = frozenset({'code','scope','claim_ids'})
ANSWER_PLAN_TASK_LIMITATION_CODES = frozenset({'comparison_coverage_incomplete',
    'comparison_explanation_incomplete'})
ANSWER_PLAN_PROCESSING_LIMITATION_CODES = frozenset({
    'partial_case_only_not_full_inflection','context_only_no_explicit_value',
    'unresolved_value_role','grammatical_number_mismatch_or_unspecified',
    'source_status_not_admitted_for_coverage',
    *{f'requested_{value}_not_in_exact_excerpt'for value in
      ('explanation','definition','frequency','example','spacing','recommendation','usage')}})
ANSWER_PLAN_RENDERED_LIMITATIONS = {
    'partial_case_only_not_full_inflection':(
        'Odlomek potrjuje le navedeni sklon, ne celotne sklanjatve.',
        'Questo estratto documenta solo il caso indicato, non l’intera flessione.'),
    **{f'requested_{facet}_not_in_exact_excerpt':(f'Odlomek {sl}.',f'L’estratto {it}.')
       for facet,sl,it in (
        ('explanation','ne pojasni razloga za obliko','non spiega il motivo della forma'),
        ('definition','ne vsebuje zahtevane definicije','non contiene la definizione richiesta'),
        ('frequency','ne vsebuje zahtevanih podatkov o pogostnosti','non contiene dati sulla frequenza richiesti'),
        ('example','ne vsebuje zahtevanega primera','non contiene l’esempio richiesto'),
        ('spacing','ne razreši zahtevanega zapisa skupaj ali narazen','non risolve la grafia unita o separata richiesta'),
        ('recommendation','ne vsebuje zahtevanega priporočila','non contiene la raccomandazione richiesta'),
        ('usage','ne vsebuje zahtevanih podatkov o rabi','non contiene le informazioni d’uso richieste'))}}

CITATION_MISMATCH_REASONS = frozenset({
    'missing_evidence','invalid_frozen_binding','evidence_text','source_key',
    'locator','range','snippet','source_id','source_revision'})


def evidence_id(row):
    return row.get('evidence_id') or row.get('passage_id') or row.get('citation_id')


def frozen_citation_binding(item, view):
    """Retain the exact CORE source tuple privately inside a new WEB freeze.

    Public source projections intentionally omit some citation fields and may
    pretty-print tool records.  This small hash-and-metadata tuple is covered by
    the family snapshot and remains private; it lets the consumer validate a
    future answer against the freeze instead of trusting redundant display
    metadata returned with that answer.
    """
    text=view.get('snippet');locator=view.get('locator')
    source_key=view.get('source_slug');external_id=view.get('external_id')
    revision=view.get('source_revision')
    identity=evidence_id(item)
    if (not isinstance(text,str)or not text
            or not isinstance(locator,dict)or not locator
            or not isinstance(source_key,str)or source_key!=item.get('source_slug')
            or not isinstance(external_id,str)or not external_id
            or not isinstance(revision,str)or not revision
            or view.get('citation_id')!=identity):
        return None
    # CORE always namespaces documentary captures, including native record IDs
    # that already contain a colon. Tool and ordinary passage IDs retain the
    # historical already-namespaced convention. `capture_sha256` is populated
    # only for documentary rows in CORE's frozen match projection.
    documentary=(isinstance(item.get('capture_sha256'),str)
                 and bool(item['capture_sha256']))
    source_id=(source_key+':'+external_id if documentary
               else external_id if ':'in external_id
               else source_key+':'+external_id)
    return {'source_key':source_key,'external_id':external_id,
        'source_id':source_id,'source_revision':revision,
        'locator_sha256':digest(locator),'text_sha256':digest(text),
        'text_length':len(text)}


def citation_mismatch(row, source, binding=None, frozen_locator=None):
    """Return one fixed, non-sensitive reason while preserving strict checks."""
    if not isinstance(source,dict):return'missing_evidence'
    source_text=source.get('snippet')
    if binding is not None:
        required={'source_key','external_id','source_id','source_revision',
                  'locator_sha256','text_sha256','text_length'}
        if (not isinstance(binding,dict)or set(binding)!=required
                or not isinstance(binding.get('source_key'),str)
                or not isinstance(binding.get('external_id'),str)
                or not isinstance(binding.get('source_id'),str)
                or not isinstance(binding.get('source_revision'),str)
                or not isinstance(binding.get('locator_sha256'),str)
                or not isinstance(binding.get('text_sha256'),str)
                or type(binding.get('text_length'))is not int
                or not isinstance(frozen_locator,dict)or not frozen_locator
                or digest(frozen_locator)!=binding['locator_sha256']):
            return'invalid_frozen_binding'
        if (not isinstance(source_text,str)
                or len(source_text)!=binding['text_length']
                or digest(source_text)!=binding['text_sha256']):
            return'evidence_text'
        source_key=binding['source_key'];source_locator=frozen_locator
        expected_source_id=binding['source_id'];source_revision=binding['source_revision']
    else:
        source_locator=source.get('locator');external_id=source.get('external_id')
        source_key=source.get('source_slug')
        expected_source_id=(external_id if isinstance(external_id,str)and':'in external_id
            else source_key+':'+external_id
            if isinstance(source_key,str)and isinstance(external_id,str)and external_id else None)
        source_revision=source.get('source_revision')
    if row.get('source_key')!=source_key:return'source_key'
    if not isinstance(source_locator,dict)or source_locator!=row.get('locator'):return'locator'
    start,end=row.get('char_start'),row.get('char_end')
    if (not isinstance(source_text,str)or type(start)is not int or type(end)is not int
            or not 0<=start<end<=len(source_text)):
        return'range'
    if source_text[start:end]!=row.get('snippet'):return'snippet'
    if (row.get('source_id')!=expected_source_id
            if expected_source_id is not None else'source_id'in row):
        return'source_id'
    if (row.get('source_revision')!=source_revision
            if isinstance(source_revision,str)and source_revision
            else'source_revision'in row):
        return'source_revision'
    return None


def citation_failure(reason):
    error=Failure('unknown_citation',502)
    if reason in CITATION_MISMATCH_REASONS:error.citation_mismatch=reason
    return error


def shared_interpretation(gateway,question,context,input_language,locale):
    """Fetch and validate the one authenticated request interpretation.

    This runs before any source or tool request.  WEB may enrich the returned
    targets in a lexical profile, but it never creates or repairs this plan.
    """
    request={'question':question,'context':context,
        'input_language':input_language,'locale':locale}
    input_identity={'contract':'klikai.request-interpretation.input.v1',**request}
    try:
        response=(gateway.interpret(**request)if hasattr(gateway,'interpret')else
                  gateway.request('/internal/pilot/interpret',request))
        if (not isinstance(response,dict)or set(response)!={
                'contract','input_sha256','request_plan','request_plan_sha256'}
                or response.get('contract')!='klikai.request-interpretation.v1'
                or response.get('input_sha256')!=digest(input_identity)):
            raise ValueError('Interpretation envelope differs')
        plan=response.get('request_plan')
        if (not isinstance(plan,dict)
                or response.get('request_plan_sha256')!=digest(plan)
                or plan.get('version')!='klikai.request-plan.v1'
                or plan.get('interpretation_contract')!=response['contract']
                or plan.get('original')!=question
                or plan.get('context','')!=context
                or plan.get('source_language')!=(
                    input_language if input_language in {'sl','it'}else None)
                or plan.get('response_language')!=locale):
            raise ValueError('Interpretation plan binding differs')
        spans=plan.get('target_spans')
        facets=plan.get('requested_facets')
        slots=plan.get('requested_grammar_slots')
        ambiguities=plan.get('ambiguities')
        if (not isinstance(spans,list)or len(spans)>4
                or not isinstance(facets,list)
                or any(not isinstance(value,str)for value in facets)
                or not isinstance(slots,list)
                or any(not isinstance(value,dict)
                       or value.get('case')not in {'nominative','genitive','dative',
                           'accusative','locative','instrumental'}
                       or value.get('number')not in {None,'singular','dual','plural'}
                       for value in slots)
                or not isinstance(ambiguities,list)
                or any(not isinstance(value,str)for value in ambiguities)):
            raise ValueError('Interpretation plan structure differs')
        effective=question+('\n'+context if context else'')
        def bound(span):
            if not isinstance(span,dict):return False
            start,end=span.get('start'),span.get('end')
            scoped=span.get('span_scope')
            if scoped not in {None,'context'}:return False
            source=effective if scoped=='context'else question
            if (type(start)is not int or type(end)is not int
                    or not 0<=start<end<=len(source)
                    or source[start:end]!=span.get('text')
                    or scoped=='context'and start<=len(question)):
                return False
            nested=span.get('context_span')
            return nested is None or bound(nested)
        if not all(bound(span)for span in spans):
            raise ValueError('Interpretation target offsets differ')
        return {'request_plan':deepcopy(plan),
                'interpretation_input_sha256':response['input_sha256'],
                'request_plan_sha256':response['request_plan_sha256']}
    except Failure:
        raise
    except (AttributeError,KeyError,TypeError,ValueError):
        raise Failure('invalid_answer',502)from None


def checked(bundle):
    if (not isinstance(bundle, dict) or bundle.get('contract') != CONTRACT or
            bundle.get('snapshot_sha256') != digest({k: v for k, v in bundle.items() if k != 'snapshot_sha256'})):
        raise Failure('resource_changed', 409)
    return bundle


def validate_coverage_limitations(blocks, limitations):
    """Bind every deterministic coverage block to one closed CORE receipt."""
    coverage={row['unit_id']:row for row in blocks
              if row.get('unit_type')=='coverage_limitation'}
    rows=limitations if isinstance(limitations,list)else []
    candidates=[row for row in rows if isinstance(row,dict)and(
        row.get('code')=='partial_evidence'or row.get('unit_id')in coverage
        or 'coverage_receipt_id'in row or 'coverage_receipt_sha256'in row)]
    if (len(coverage)!=sum(row.get('unit_type')=='coverage_limitation'for row in blocks)
            or len(candidates)!=len(coverage)):
        raise Failure('unknown_citation',502)
    seen=set()
    for row in candidates:
        if (set(row)!=ANSWER_PLAN_COVERAGE_LIMITATION_FIELDS
                or row.get('code')!='partial_evidence'or row.get('scope')!='answer'
                or row.get('claim_ids')!=[]or row.get('unit_id')not in coverage
                or row.get('message')!=coverage[row['unit_id']]['text']
                or not isinstance(row.get('coverage_receipt_id'),str)
                or not row['coverage_receipt_id']
                or not isinstance(row.get('coverage_receipt_sha256'),str)
                or not re.fullmatch(r'[0-9a-f]{64}',row['coverage_receipt_sha256'])
                or row['unit_id']in seen):
            raise Failure('unknown_citation',502)
        seen.add(row['unit_id'])
    if seen!=set(coverage):raise Failure('unknown_citation',502)


def deterministic_answer_text(blocks):
    paragraphs=dict.fromkeys(row['paragraph_id']for row in blocks)
    return '\n\n'.join(' '.join(row['text']for row in blocks
        if row['paragraph_id']==paragraph)for paragraph in paragraphs)


def validate_processing_limitations(blocks, claims, limitations):
    """Close processing limitations and bind them to exact-excerpt claims."""
    coverage_ids={row['unit_id']for row in blocks
                  if row.get('unit_type')=='coverage_limitation'}
    processing=[row for row in limitations if not(isinstance(row,dict)and(
        row.get('code')=='partial_evidence'or row.get('unit_id')in coverage_ids
        or 'coverage_receipt_id'in row or 'coverage_receipt_sha256'in row))]
    task_rows=[row for row in processing if isinstance(row,dict)and
               row.get('code')in ANSWER_PLAN_TASK_LIMITATION_CODES]
    if (any(set(row)!=ANSWER_PLAN_TASK_LIMITATION_FIELDS or row.get('scope')!='answer'
            or row.get('claim_ids')!=[] for row in task_rows)
            or len({row['code']for row in task_rows})!=len(task_rows)):
        raise Failure('unknown_citation',502)
    processing=[row for row in processing if row not in task_rows]
    block_by_claim={row['claim_id']:row for row in blocks if row.get('claim_id')is not None}
    seen=set()
    for row in processing:
        ids=row.get('claim_ids')if isinstance(row,dict)else None
        claim_id=ids[0]if isinstance(ids,list)and len(ids)==1 else None
        block=block_by_claim.get(claim_id)
        key=(row.get('code'),claim_id,row.get('unit_id'))if isinstance(row,dict)else None
        if (not isinstance(row,dict)or set(row)!=ANSWER_PLAN_PROCESSING_LIMITATION_FIELDS
                or row.get('code')not in ANSWER_PLAN_PROCESSING_LIMITATION_CODES
                or row.get('scope')!='processing'or block is None
                or block.get('unit_type')!='exact_source_excerpt'
                or row.get('unit_id')!=block.get('unit_id')or key in seen):
            raise Failure('unknown_citation',502)
        seen.add(key)
        phrases=ANSWER_PLAN_RENDERED_LIMITATIONS.get(row['code'])
        if phrases and not any(phrase in block['text']for phrase in phrases):
            raise Failure('unknown_citation',502)
    for claim_id,block in block_by_claim.items():
        for code,phrases in ANSWER_PLAN_RENDERED_LIMITATIONS.items():
            if any(phrase in block['text']for phrase in phrases)and(
                    code,claim_id,block['unit_id'])not in seen:
                raise Failure('unknown_citation',502)
    return processing


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
    mismatch=getattr(error,'citation_mismatch',None)
    return {'technical_cause':safe_exception(error),'error_code': code, 'stage': stage,
            **({'citation_mismatch':mismatch}if mismatch in CITATION_MISMATCH_REASONS else{}),
            **({'http_status':status}if type(status)is int and 100<=status<=599 else{})}


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


def collect_source(gateway, family, definition, detail_pool=None, *, query_override=None, lemma_candidate=None):
    if detail_pool is None:
        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            return collect_source(gateway, family, definition, pool, query_override=query_override,
                                  lemma_candidate=lemma_candidate)
    started = time.monotonic()
    slug, adapter = definition['slug'], definition['adapter_key']
    query, locale = family['question'] if query_override is None else query_override, family['locale']
    request_plan = family.get('request_plan') or {}
    targets = request_plan.get('target_spans', [])
    if query_override is None and len(targets) == 1 and request_plan.get('original') == family.get('original_question', family['question']):
        target = targets[0]
        original = request_plan['original'] + ('\n' + request_plan.get('context','') if target.get('span_scope') == 'context' else '')
        a, b = target.get('start'), target.get('end')
        if type(a) is int and type(b) is int and 0 <= a < b <= len(original) and original[a:b] == target.get('text'):
            # Exact original expression first; the resource owns any inflectional
            # expansion. Explicit domain remains visible in the request plan.
            query = target['text']
    variants = list(dict.fromkeys([query, family['question']])) if query_override is None else [query]
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
                if hit.get('original_projection') is not None:
                    from .original_projection import checked as checked_original
                    source['original_projection'] = checked_original(hit['original_projection'],
                        source_slug=slug, record_id=hit['resource_id'])
                lookups = dictionary_lookups(hit, query, family, lemma_candidate) if adapter == 'official_reference_jsonl' else []
                for lookup in lookups or [None]:
                    capture = dict(hit, dictionary_lookup=lookup) if lookup is not None else hit
                    item=match(capture, 'resource_record', definition['display_name'], rendered, source, locale, hit.get('score'))
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


def _lookup_key(value):
    # Case and Slovene letters distinguish dictionary identities. A lookup may
    # remove display stress later, but discovery must not collapse Luka/luka.
    return ' '.join(unicodedata.normalize('NFC', value).split())


def literal_targets(rows, original, context):
    result = set()
    for target in rows:
        start, end = target.get('start'), target.get('end')
        text = target.get('text')
        source = original + '\n' + context if target.get('span_scope') == 'context' else original
        if type(start) is int and type(end) is int and 0 <= start < end <= len(source) and source[start:end] == text:
            result.add(text)
    return result


def dictionary_lookups(hit, query, family, candidate=None):
    """Bind exact source lookup provenance into each immutable entry capture."""
    if (hit.get('semantic_unit') != 'dictionary_entry'
            or hit.get('score_method') != 'official_reference_exact_headword'
            or not isinstance(hit.get('published_headword'), str) or not hit['published_headword']):
        return []
    grouped = {}
    if candidate is not None:
        bases={'recorded_morphology_identity':'recorded_morphology_lemma',
               'recorded_phrase_morphology_identity':'recorded_phrase_morphology_lemma'}
        if candidate.get('query') != query or candidate.get('basis') not in bases:
            return []
        for record in candidate['source_records']:
            grouped.setdefault(record['target'], set()).add(record['source_entry_id'])
        basis = bases[candidate['basis']]
    else:
        plan = family.get('request_plan') or {}
        original = family.get('original_question', family['question'])
        if plan.get('original') != original:
            return []
        for target in literal_targets(plan.get('target_spans', []), original, family.get('context', '')):
            if _lookup_key(query) == _lookup_key(target):
                grouped[target] = set()
        basis = 'original_surface'
    return [{'version': 'recorded-dictionary-lookup-v1', 'target': target, 'query': query,
             'basis': basis, 'source_entry_ids': sorted(entries)} for target, entries in sorted(grouped.items())]


def recorded_phrase_lemmas(unit, interpretation, original, context):
    """Attested component paradigms, without treating a phrase as one lexeme."""
    if 'translation' in interpretation.get('requested_facets',[]):return []
    if (unit.get('kind')!='analysis' or unit.get('match_relation')!='recorded_target'
            or unit.get('selection',{}).get('state','primary') not in {'primary','alternative'}):return []
    fields=unit.get('fields',{});target=unit.get('target')
    if fields.get('submitted_expression')!=target:return []
    spans=[t for t in interpretation.get('targets',[])if t.get('text')==target
           and t.get('start')==fields.get('start')and t.get('end')==fields.get('end')]
    if not spans or target not in literal_targets(spans,original,context):return []
    source=original+'\n'+context if spans[0].get('span_scope')=='context'else original
    surfaces=set()
    for engine in fields.get('engines',{}).values():
        for token in engine.get('tokens',[]):
            start,end=token.get('start'),token.get('end')
            if (type(start)is int and type(end)is int and fields['start']<=start<end<=fields['end']
                    and source[start:end]==token.get('text')
                    and token.get('upos')in {'NOUN','PROPN','ADJ','VERB','ADV'}):surfaces.add(token['text'])
    result=[]
    for surface,data in fields.get('recorded_forms',{}).items():
        if surface not in surfaces or data.get('status')!='matched':continue
        for reading in data.get('recorded_readings',[]):
            lemma=reading.get('lemma');entry=reading.get('source_entry_id')
            if (reading.get('exact_surface')is not True or reading.get('selection')not in {'primary','alternative'}
                    or not isinstance(lemma,str)or not 1<=len(lemma.strip())<=160 or len(lemma.split())>8
                    or not isinstance(entry,str)or not entry):continue
            for recorded in data.get('source_slots',[]):
                slot=recorded.get('slot',{});provenance=recorded.get('provenance',{})
                if (recorded.get('source_entry_id')!=entry or recorded.get('lemma')!=lemma
                        or provenance.get('source_entry_id')!=entry or provenance.get('resource_id')!='sloleks'
                        or not re.fullmatch(r'[0-9a-f]{64}',str(provenance.get('source_archive_sha256','')))
                        or type(reading.get('slot_locator'))is not int
                        or slot.get('slot_locator')!=reading['slot_locator']
                        or slot.get('original_msd')!=reading.get('original_msd')
                        or slot.get('normalized_features')!=reading.get('features')):continue
                if not any(v.get('form')==surface and v.get('source_locator')==reading.get('source_locator')
                           and isinstance(v.get('source_locator'),str)and v['source_locator']
                           for v in slot.get('variants',[])):continue
                result.append((lemma,{'target':target,'surface':surface,'resource_id':'sloleks',
                    'source_entry_id':entry,'captured_unit_id':unit.get('captured_unit_id'),
                    'match_basis':'recorded_phrase_form','slot_locator':reading['slot_locator'],
                    'variant_locator':reading['source_locator']}))
    return result


def validated_reference_lemmas(tool_capture, family):
    """Use attested morphological identities for discovery, never a verdict.

    Analyzer nominations alone are insufficient. Keep each resource identity
    behind an attested form, including alternatives; do not prefer frequency,
    a parser's predicted case, or one homograph over another.
    """
    captures=(tool_capture or {}).get('captures',[])
    if len(captures)!=1 or captures[0].get('operation')!='lexical_profile':return []
    profile=captures[0].get('response',{})
    original=family.get('original_question',family['question'])
    context=family.get('context','')
    query=profile.get('query',{})
    if query.get('text')!=original or query.get('context','')!=context:return []
    interpretation=profile.get('interpretation',{})
    targets=literal_targets(interpretation.get('targets',[]),original,context)
    grouped={}
    for unit in profile.get('evidence',[]):
        for lemma,record in recorded_phrase_lemmas(unit,interpretation,original,context):
            key=(_lookup_key(lemma),'recorded_phrase_morphology_identity')
            value=grouped.setdefault(key,{'query':lemma.strip(),'basis':key[1],'source_records':[]})
            if record not in value['source_records']:value['source_records'].append(record)
        if unit.get('kind')!='morphology' or unit.get('target')not in targets or unit.get('match_relation')!='recorded_target':continue
        fields=unit.get('fields',{});provenance=unit.get('provenance',{})
        lemma=fields.get('lemma');target=unit['target'];match=fields.get('matched_by')
        if not isinstance(lemma,str) or not 1<=len(lemma.strip())<=160 or len(lemma.split())>8:continue
        entry=fields.get('source_entry_id')
        if not isinstance(entry,str) or not entry or provenance.get('source_entry_id')!=entry or not provenance.get('resource_id'):continue
        if match=='recorded_form':
            if fields.get('submitted_form')!=target or not any(row.get('exact_surface')is True for row in fields.get('submitted_analyses',[])):continue
        elif match in {'exact_headword','folded_headword'}:
            if _lookup_key(lemma)!=_lookup_key(target):continue
        else:continue
        key=(_lookup_key(lemma),'recorded_morphology_identity')
        record={'target':target,'resource_id':provenance['resource_id'],'source_entry_id':entry,
                'captured_unit_id':unit.get('captured_unit_id'),'match_basis':match}
        value=grouped.setdefault(key,{'query':lemma.strip(),'basis':'recorded_morphology_identity','source_records':[]})
        if record not in value['source_records']:value['source_records'].append(record)
    return list(grouped.values())


def expand_reference_sources(gateway, family, definitions, tool_capture, found, coverage, detail_pool, pool):
    """Supplement surface searches after the single lexical capture finishes."""
    candidates=validated_reference_lemmas(tool_capture,family)
    if not candidates:return
    profile=tool_capture['captures'][0]['response']
    lookup_family=deepcopy(family)
    lookup_family['request_plan']=deepcopy(profile.get('interpretation',{}).get('request_plan') or family['request_plan'])
    jobs=[];by_source={row['family']:row for row in coverage};pending=[]
    for definition in sorted(definitions,key=lambda row:row['slug']):
        if definition['adapter_key']!='official_reference_jsonl':continue
        status=by_source[definition['slug']]
        existing={_lookup_key(value)for value in status.get('query_variants',[])}
        additions=[row for row in candidates if _lookup_key(row['query'])not in existing]
        status['lemma_expansion']={'basis':'recorded_morphology_identity','candidate_queries':len(additions),
                                  'queried':0,'omitted_by_bound':len(additions),'lookups':[]}
        pending.append((definition,additions[:REFERENCE_LEMMA_LIMIT]))
    # Round-robin sources before taking another lemma; a long comparison must
    # not let the first source consume every request or expand without bounds.
    for index in range(REFERENCE_LEMMA_LIMIT):
        for definition,additions in pending:
            if index>=len(additions) or len(jobs)>=REFERENCE_LOOKUP_REQUEST_LIMIT:continue
            candidate=additions[index];expansion=by_source[definition['slug']]['lemma_expansion']
            expansion['queried']+=1;expansion['omitted_by_bound']-=1
            jobs.append((definition['slug'],candidate,pool.submit(collect_source,gateway,lookup_family,
                definition,detail_pool,query_override=candidate['query'],lemma_candidate=candidate)))
    for slug,candidate,future in jobs:
        matches,extra=future.result();found.extend(matches);status=by_source[slug]
        status.setdefault('surface_returned_count',status['returned_count'])
        status.setdefault('per_query_limit',status['limit'])
        status['lemma_expansion']['lookups'].append({**deepcopy(candidate),
            'status':extra['status'],'returned_count':extra['returned_count'],
            'elapsed_ms':extra['elapsed_ms'],'candidates':deepcopy(extra.get('candidates',[]))})
        status['query_variants']=list(dict.fromkeys([*status['query_variants'],*extra['query_variants']]))
        status['limit']=status['per_query_limit']*(1+len(status['lemma_expansion']['lookups']))
        status['returned_count']=len({(row['external_id'],row['capture_kind'],row['capture_sha256'])for row in found if row['source_slug']==slug})
        status['search_ms']+=extra['search_ms']
        status['elapsed_ms']+=extra['elapsed_ms']
        failed={'partial','timeout','unavailable','unsupported'}
        if status['status']in failed or extra['status']in failed:
            status['status']='partial' if status['returned_count'] else (status['status'] if status['status']in failed else extra['status'])
        else:status['status']='ok' if status['returned_count'] else 'no_match'


O_NAME_DECLENSION_RULE_QUERY = (
    'Marko glavnem sklanjatvenem vzorcu korak zapis osnovnih oblik bratu')
O_NAME_DECLENSION_RULE_LOOKUP_LIMIT = 1


def o_name_declension_rule_query(family):
    """Return one class-level rule lookup from the shared request plan.

    This consumes the interpretation already obtained from CORE.  WEB does not
    maintain a second parser or infer an answer; it only freezes the two
    documentary rule scopes that CORE may later admit.
    """
    plan=family.get('request_plan') if isinstance(family,dict) else None
    if not isinstance(plan,dict) or plan.get('ambiguities'):
        return None
    slots=plan.get('requested_grammar_slots');targets=plan.get('target_spans')
    if (not isinstance(slots,list) or len(slots)!=1
            or not isinstance(targets,list) or len(targets)!=1
            or not isinstance(slots[0],dict) or not isinstance(targets[0],dict)):
        return None
    slot=slots[0];target=targets[0].get('text')
    context=targets[0].get('context_span',{})
    context_text=context.get('text') if isinstance(context,dict) else ''
    if (set(slot)!={'case','number'}
            or slot.get('case')not in {'genitive','dative'}
            or slot.get('number')not in {None,'singular'}
            or not isinstance(target,str) or len(target)<3
            or target[-1:].casefold()!='o'
            or not isinstance(context_text,str)
            or re.search(r'\b(?:ime|imena|priimek|priimka|nome|cognome)\b',
                         context_text,re.I)is None):
        return None
    return O_NAME_DECLENSION_RULE_QUERY


def o_name_declension_rule_capture(row):
    """Identify the two current SP2001 rule scopes without record-ID pinning."""
    if (not isinstance(row,dict) or row.get('source_slug')!='sp2001'
            or row.get('capture_kind')!='resource_record'):
        return False
    capture=row.get('capture')
    if (not isinstance(capture,dict)
            or capture.get('normative_status')!='current_reference'):
        return False
    text=capture.get('original_text')
    if not isinstance(text,str):return False
    first=(re.search(
        r'Nekateri\s+samostalniki,\s+ki\s+gredo\s+po\s+glavnem\s+'
        r'sklanjatvenem\s+vzorcu\s+korak,.{1,360}?'
        r'imenovalniške\s+končnice.{1,180}?-o.{1,180}?Marko',
        text,re.I|re.S)is not None)
    paradigm=all(re.search(pattern,text,re.I|re.S)is not None for pattern in (
        r'Zapisi\s+osnovnih\s+oblik.{1,260}?nakazujejo\s+cele\s+naglasne\s+tipe',
        r'rodilniška\s+končnica.{1,360}?vsej\s+sklanjatvi',
        r'br[aà]t\s+br[aá]ta.{1,260}?sledi\s+br[aá]tu'))
    return first or paradigm


def expand_o_name_declension_rules(
        gateway,family,definitions,found,coverage,detail_pool,pool):
    """Freeze one bounded SP2001 rule lookup beside the surface lookup."""
    query=o_name_declension_rule_query(family)
    if query is None:return
    definition=next((row for row in definitions
        if row.get('slug')=='sp2001'
        and row.get('adapter_key')=='official_reference_jsonl'),None)
    by_source={row['family']:row for row in coverage}
    status=by_source.get('sp2001')
    if definition is None or status is None:return
    # This is a separate construction-level cap, not an unbounded morphology
    # expansion: exactly one additional source request may be issued.
    matches,extra=pool.submit(
        collect_source,gateway,family,definition,detail_pool,
        query_override=query).result()
    scoped=[row for row in matches if o_name_declension_rule_capture(row)]
    known={(row['source_slug'],row['external_id'],row['capture_sha256'])
           for row in found}
    added=[]
    for row in scoped:
        identity=(row['source_slug'],row['external_id'],row['capture_sha256'])
        if identity not in known:
            found.append(row);added.append(row);known.add(identity)
    status['declension_rule_expansion']={
        'basis':'shared_request_plan_o_name_singular_slot',
        'query':query,'status':extra['status'],
        'scoped_returned_count':len(scoped),'added_count':len(added),
        'candidates':deepcopy(extra.get('candidates',[]))}
    status.setdefault('surface_returned_count',status['returned_count'])
    status.setdefault('per_query_limit',status['limit'])
    status['query_variants']=list(dict.fromkeys(
        [*status.get('query_variants',[]),*extra.get('query_variants',[])]))
    status['limit']+=status['per_query_limit']*O_NAME_DECLENSION_RULE_LOOKUP_LIMIT
    status['returned_count']=len({
        (row['external_id'],row['capture_kind'],row['capture_sha256'])
        for row in found if row['source_slug']=='sp2001'})
    status['search_ms']+=extra['search_ms'];status['elapsed_ms']+=extra['elapsed_ms']
    failed={'partial','timeout','unavailable','unsupported'}
    if status['status']in failed or extra['status']in failed:
        status['status']=('partial' if status['returned_count'] else
            (status['status'] if status['status']in failed else extra['status']))
    else:status['status']='ok' if status['returned_count'] else 'no_match'


def freeze(gateway, family, policy):
    # One request plan travels to source discovery, lexical profiles, writer and
    # cards. Family/history text remains immutable; only this request copy is used.
    family = deepcopy(family)
    original = family.get('original_question', family['question'])
    context=family.get('context','')
    input_language=family.get('input_language','und')
    interpretation=shared_interpretation(
        gateway,original,context,input_language,family['locale'])
    family['request_plan']=interpretation['request_plan']
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
        expand_reference_sources(gateway,family,definitions,tool_capture,found,coverage,details,pool)
        expand_o_name_declension_rules(
            gateway,family,definitions,found,coverage,details,pool)
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
        freeze_request = json.loads(canonical({'question': original,
            'context': family.get('context', ''), 'input_language':input_language,
            'locale': family['locale'], 'task': 'reference',
            'policy': policy, 'unified_evidence': packet, 'request_plan': family['request_plan'],
            'interpretation_input_sha256': interpretation['interpretation_input_sha256'],
            'request_plan_sha256': interpretation['request_plan_sha256'],
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
            binding=frozen_citation_binding(item,view)
            if binding is not None:item['_citation_binding']=binding
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
            projection = view.get('original_projection')
            captured_projection = (original or {}).get('source', {}).get('original_projection')
            if projection is not None or captured_projection is not None:
                from .original_projection import checked as checked_original
                require_projection = checked_original(projection, source_slug=item['source_slug'],
                    record_id=item.get('external_id'))
                if captured_projection is not None and require_projection != captured_projection:
                    raise Failure('invalid_answer', 502)
                item['source']['original_projection'] = require_projection
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
    except Failure as error:
        if error.code=='request_plan_mismatch':
            raise
        coverage.append({'family': 'reference_gateway', 'status': 'timeout' if error.code == 'source_timeout' else 'unavailable', 'returned_count': 0, 'limit': None, 'has_more': None, **diagnostic(error,'gateway_freeze')})
    except (KeyError, TypeError, ValueError) as error:
        coverage.append({'family': 'reference_gateway', 'status': 'unavailable', 'returned_count': 0, 'limit': None, 'has_more': None, **diagnostic(error,'gateway_freeze')})
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
        # These links offer relevant originals when synthesis is incomplete;
        # they are not citations and may not invent a source or its title.
        decision_sources = result.get('decision_sources', [])
        comparison_codes = {'comparison_coverage_incomplete', 'comparison_explanation_incomplete'}
        scoped_partial = result.get('status') == 'partial' and any(
            isinstance(row, dict) and row.get('code') in comparison_codes
            for row in (result.get('limitations', []) if isinstance(result.get('limitations'), list) else []))
        if (not isinstance(decision_sources, list) or len(decision_sources) > 64
                or (decision_sources and not scoped_partial)):
            raise Failure('unknown_citation', 502)
        matched = {evidence_id(row): row for row in bundle['matches']}
        if len(matched)!=len(bundle['matches']):raise Failure('unknown_citation',502)
        checked_sources, seen_sources = [], set()
        cited_ids = {evidence_id(row) for row in evidence + citations}
        for row in decision_sources:
            if (not isinstance(row, dict) or set(row) != {'evidence_id', 'title'}
                    or not isinstance(row.get('evidence_id'), str) or row['evidence_id'] not in known
                    or not isinstance(row.get('title'), str) or len(row['title']) > 2000):
                raise Failure('unknown_citation', 502)
            eid = row['evidence_id']
            match = matched[eid]
            title = match.get('title') or (match.get('source') or {}).get('title')
            if not isinstance(title, str) or not title.strip() or len(title) > 2000:
                raise Failure('unknown_citation', 502)
            if eid not in seen_sources and eid not in cited_ids:
                checked_sources.append({'evidence_id': eid, 'title': title})
                seen_sources.add(eid)
        if 'decision_sources' in result:result['decision_sources'] = checked_sources
        # A successful transport or an excerpt with badges is not generation.
        generated = result.get('answer_kind') in {'generated', 'mixed'} and result.get('status') in {'validated', 'partial'}
        excerpt = (result.get('answer_kind') == 'source_only' and result.get('status') in {'validated', 'partial'}
                   and result.get('generation_status') == 'source_supported_partial'
                   and result.get('synthesis_mode') == 'source_supported_partial'
                   and isinstance(result.get('validation'), dict)
                   and result['validation'].get('status') == 'bounded_checks_completed')
        # Current writers can return a bounded source-only outcome when the
        # selected material is visible but cannot safely resolve the requested
        # interpretation.  Admit only the exact closed tuple used by the public
        # projection; private unresolved-unit diagnostics remain downstream of
        # that projection.
        from .presentation import reader_evidence_state
        evidence_state = reader_evidence_state(result, evidence)
        bounded_source_only = evidence_state is not None
        bounded_answer_plan = (result.get('generation_status') == 'deterministic_bounded_answer_plan'
            and result.get('synthesis_mode') == 'deterministic_bounded_answer_plan')
        coverage_answer_plan=(bounded_answer_plan and result.get('answer_kind')=='source_only')
        if bounded_answer_plan and not generated and not coverage_answer_plan:
            raise Failure('unknown_citation',502)
        deliverable = generated or excerpt or bounded_source_only or bounded_answer_plan
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
        if ((generated or excerpt) or bounded_source_only and
                evidence_state['status'] != 'no_selected_evidence') and not evidence:
            raise Failure('unknown_citation', 502)
        if deliverable:
            for i,row in enumerate(evidence):
                # The producer's source view uses citation_id. Expose the same
                # validated identity to canonical UI/probe consumers without
                # removing its aliases or changing signed prose/source metadata.
                row['evidence_id']=evidence_id(row)
                row['local_number']=row.get('local_number')or row.get('number')or i+1
        if coverage_answer_plan:
            blocks=result.get('display_blocks');claims=result.get('claims')
            limitations=result.get('limitations');markers=set(re.findall(
                r'\[(\d{1,3})\]',result.get('answer_md','')))
            if (result.get('status')!='partial'or result.get('answer_support')!='partial'
                    or markers or claims!=[]or citations!=[]or evidence!=[]
                    or result.get('source_badges')!=[]
                    or not isinstance(blocks,list)or not blocks
                    or not isinstance(limitations,list)or len(limitations)!=len(blocks)
                    or result.get('validation')!={'status':'bounded_answer_plan_rendered',
                        'semantic_entailment_certified':False}):
                raise Failure('unknown_citation',502)
            block_ids=[];unit_ids=[]
            for block in blocks:
                if (not isinstance(block,dict)or set(block)!=ANSWER_PLAN_BLOCK_FIELDS
                        or not isinstance(block.get('block_id'),str)
                        or not re.fullmatch(r'B(?:[1-9]|1[0-4])',block['block_id'])
                        or not isinstance(block.get('paragraph_id'),str)
                        or not re.fullmatch(r'P(?:[1-9]|1[0-4])',block['paragraph_id'])
                        or not isinstance(block.get('unit_id'),str)
                        or not re.fullmatch(r'U[0-9a-f]{16}',block['unit_id'])
                        or block.get('unit_type')!='coverage_limitation'
                        or block.get('claim_id')is not None or block.get('support_ids')!=[]
                        or not isinstance(block.get('text'),str)or not block['text']):
                    raise Failure('unknown_citation',502)
                block_ids.append(block['block_id']);unit_ids.append(block['unit_id'])
                block['evidence_ids']=[]
            if len(set(block_ids))!=len(block_ids)or len(set(unit_ids))!=len(unit_ids):
                raise Failure('unknown_citation',502)
            validate_coverage_limitations(blocks,limitations)
            text=deterministic_answer_text(blocks)
            if (result.get('answer_md')!=text or result.get('short_answer')!=text):
                raise Failure('unknown_citation',502)
        if generated or excerpt:
            numbers={str(row.get('local_number')or row.get('number')or i+1)for i,row in enumerate(evidence)}
            markers=set(re.findall(r'\[(\d{1,3})\]',result.get('answer_md','')))
            if not markers<=numbers:raise Failure('unknown_citation',502)
            # The gateway's signed generated prose uses claim-to-source mappings,
            # not necessarily textual [n] markers. Preserve its signed answer;
            # the UI renders the mapped citations separately from source text.
            blocks=result.get('display_blocks',[])
            cited={evidence_id(row)for row in citations}
            visible={evidence_id(row)for row in evidence}
            if bounded_answer_plan:
                # The current CORE producer returns a structured AnswerPlan,
                # never prose with inline citation markers. Validate its whole
                # claim/block/citation graph even when the prose is hostile or
                # malformed enough to contain an otherwise valid marker.
                claims=result.get('claims')
                limitations=result.get('limitations')
                if (result.get('answer_kind')!='generated'
                        or result.get('status')not in {'validated','partial'}
                        or result.get('answer_support')not in {'supported','partial'}
                        or result.get('validation')!={'status':'bounded_answer_plan_rendered',
                            'semantic_entailment_certified':False}
                        or not isinstance(limitations,list)
                        or markers or not isinstance(blocks,list)or not blocks
                        or not isinstance(claims,list)or not claims or not citations):
                    raise Failure('unknown_citation',502)
                if any(not isinstance(row,dict)for row in blocks+claims):
                    raise Failure('unknown_citation',502)
                claim_ids=[row.get('claim_id')for row in claims]
                block_ids=[row.get('block_id')for row in blocks]
                if (any(not isinstance(value,str)or not re.fullmatch(r'C(?:[1-9]|1[0-4])',value)
                        for value in claim_ids)
                        or any(not isinstance(value,str)or not re.fullmatch(r'B(?:[1-9]|1[0-4])',value)
                               for value in block_ids)
                        or len(set(claim_ids))!=len(claim_ids)
                        or len(set(block_ids))!=len(block_ids)
                        or any(set(row)!=ANSWER_PLAN_CLAIM_FIELDS
                               or not isinstance(row.get('paragraph_id'),str)
                               or not re.fullmatch(r'P(?:[1-9]|1[0-4])',row['paragraph_id'])
                               or not isinstance(row.get('unit_id'),str)
                               or not re.fullmatch(r'U[0-9a-f]{16}',row['unit_id'])
                               or not isinstance(row.get('text'),str)or not row['text']
                               or not isinstance(row.get('support_ids'),list)or not row['support_ids']
                               or any(not isinstance(value,str)or not value for value in row['support_ids'])
                               or len(set(row['support_ids']))!=len(row['support_ids'])
                               or not isinstance(row.get('fact_ids'),list)
                               or row.get('section')!='answer'or row.get('depends_on')!=[]
                               or row.get('support_mode')!='deterministic_bounded_answer_plan'
                               for row in claims)
                        or any(set(row)!=ANSWER_PLAN_BLOCK_FIELDS
                               or not isinstance(row.get('paragraph_id'),str)
                               or not re.fullmatch(r'P(?:[1-9]|1[0-4])',row['paragraph_id'])
                               or not isinstance(row.get('unit_id'),str)
                               or not re.fullmatch(r'U[0-9a-f]{16}',row['unit_id'])
                               or not isinstance(row.get('text'),str)
                               or not row['text'] for row in blocks)):
                    raise Failure('unknown_citation',502)
                claim_by_id={row['claim_id']:row for row in claims}
                claim_blocks=[]
                unit_ids=[]
                for block in blocks:
                    unit_ids.append(block['unit_id'])
                    claim_id=block.get('claim_id')
                    if claim_id is None:
                        if (block.get('unit_type')!='coverage_limitation'
                                or block.get('support_ids')!=[]):
                            raise Failure('unknown_citation',502)
                        block['evidence_ids']=[]
                        continue
                    support_ids=block.get('support_ids')
                    if (claim_id not in claim_by_id
                            or block.get('unit_type')not in ANSWER_PLAN_CLAIM_UNIT_TYPES
                            or not isinstance(support_ids,list)or not support_ids
                            or any(not isinstance(value,str)or not value for value in support_ids)
                            or len(set(support_ids))!=len(support_ids)):
                        raise Failure('unknown_citation',502)
                    claim_blocks.append(block)
                if len(set(unit_ids))!=len(unit_ids):
                    raise Failure('unknown_citation',502)
                validate_coverage_limitations(blocks,limitations)
                validate_processing_limitations(blocks,claims,limitations)
                partial=bool(limitations)
                if ((result['status']=='partial')!=partial
                        or result['answer_support']!=('partial'if partial else'supported')
                        or result.get('answer_md')!=deterministic_answer_text(blocks)
                        or result.get('short_answer')!=deterministic_answer_text(blocks)):
                    raise Failure('unknown_citation',502)
                bound_claim_ids=[row['claim_id']for row in claim_blocks]
                if (len(set(bound_claim_ids))!=len(bound_claim_ids)
                        or set(bound_claim_ids)!=set(claim_ids)):
                    raise Failure('unknown_citation',502)
                if any(not isinstance(row.get('claim_id'),str)
                       or row['claim_id'] not in claim_by_id for row in citations):
                    raise Failure('unknown_citation',502)
                if any(not ANSWER_PLAN_CITATION_FIELDS<=set(row)
                       or not set(row)<=ANSWER_PLAN_CITATION_FIELDS|ANSWER_PLAN_CITATION_OPTIONAL_FIELDS
                       or not isinstance(row.get('block_id'),str)
                       or not re.fullmatch(r'B(?:[1-9]|1[0-4])',row['block_id'])
                       or not isinstance(row.get('unit_id'),str)
                       or not re.fullmatch(r'U[0-9a-f]{16}',row['unit_id'])
                       or not isinstance(row.get('support_id'),str)or not row['support_id']
                       or not isinstance(row.get('source_key'),str)or not row['source_key']
                       or not isinstance(row.get('snippet'),str)or not row['snippet']
                       or not isinstance(row.get('locator'),dict)or not row['locator']
                       or type(row.get('char_start'))is not int
                       or type(row.get('char_end'))is not int
                       or not 0<=row['char_start']<row['char_end']
                       or any(not isinstance(row.get(key),str)or not row[key]
                              for key in ANSWER_PLAN_CITATION_OPTIONAL_FIELDS if key in row)
                       for row in citations):
                    raise Failure('unknown_citation',502)
                if cited!=visible:
                    raise Failure('unknown_citation',502)
                support_evidence={}
                evidence_by_id={evidence_id(row):row for row in evidence}
                for row in citations:
                    source=evidence_by_id.get(row['evidence_id'])
                    frozen=matched.get(row['evidence_id'])
                    binding=(frozen.get('_citation_binding')
                             if isinstance(frozen,dict)else None)
                    frozen_source=(frozen.get('source')
                                   if isinstance(frozen,dict)else None)
                    frozen_locator=(frozen_source.get('locator')
                                    if isinstance(frozen_source,dict)else None)
                    mismatch=citation_mismatch(row,source,binding,frozen_locator)
                    if mismatch:raise citation_failure(mismatch)
                    if binding is not None:
                        # Only the redundant reader projection is normalized;
                        # answer text and citations already matched the signed
                        # frozen source tuple exactly above.
                        source.update(source_slug=binding['source_key'],
                            external_id=binding['external_id'],
                            source_revision=binding['source_revision'],
                            locator=deepcopy(frozen_locator))
                for block in claim_blocks:
                    claim=claim_by_id[block['claim_id']]
                    bound=[row for row in citations if row['claim_id']==block['claim_id']]
                    ids=list(dict.fromkeys(evidence_id(row)for row in bound))
                    support_ids=[row['support_id']for row in bound]
                    citation_spans={(row['support_id'],row['evidence_id'],
                                     row['char_start'],row['char_end'])for row in bound}
                    if (not ids or len(citation_spans)!=len(bound)
                            or (len(set(support_ids))!=len(support_ids)
                                and block['unit_type']!='comparison_synthesis')
                            or set(support_ids)!=set(block['support_ids'])
                            or any(row['block_id']!=block['block_id']
                                   or row['unit_id']!=block['unit_id'] for row in bound)
                            or claim.get('paragraph_id')!=block['paragraph_id']
                            or claim.get('text')!=block['text']
                            or claim.get('unit_id')!=block['unit_id']
                            or claim.get('support_ids')!=block['support_ids']):
                        raise Failure('unknown_citation',502)
                    for row in bound:
                        prior=support_evidence.setdefault(row['support_id'],row['evidence_id'])
                        if prior!=row['evidence_id']:
                            raise Failure('unknown_citation',502)
                    block['evidence_ids']=ids
            else:
                if cited and cited!=visible:raise Failure('unknown_citation',502)
            if not bounded_answer_plan and not markers:
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
        if not bounded_source_only and not bounded_answer_plan:
            result['generation_status'] = ('source_supported_partial' if excerpt else
                'mixed_generated_and_source_partial' if result.get('synthesis_mode') == 'mixed_generated_and_source_partial' else 'generated')
            if excerpt:result['status'] = 'partial'
        result['retrieval'] = deepcopy(bundle['retrieval'])
        result['generation'] = {'status': (
            'no_support' if bounded_source_only and evidence_state['status'] == 'no_selected_evidence'
            else 'partial' if bounded_source_only or excerpt or result.get('status') == 'partial'
            else 'generated'), 'provider': {}, 'elapsed_ms': elapsed, 'first_content_ms': None}
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
                'edition': edition, 'status': source.get('status') or item.get('source_status') or bibliography.get('source_status'),
                'bibliography': {k: deepcopy(bibliography[k]) for k in
                    ('authors', 'date', 'signed_date_text', 'publisher', 'publication_date', 'title',
                     'source_date', 'source_status', 'normative_status', 'coverage_note')
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
