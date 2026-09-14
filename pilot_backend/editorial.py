"""Owned, frozen search excerpts from the two accepted Luka Q&A collections.

The accepted search contract has no complete-question/answer or detail reader.
Never reconstruct that missing material, or turn a search hit into new advice.
"""
from copy import deepcopy
from datetime import date
from urllib.parse import urlsplit
import json

from .security import Failure
from .store import digest

SOURCES = {'jezikovna_urgenca': 'Jezikovna urgenca',
           'jezikovna_posta': 'Jezikovna pošta / Druga pomoč'}
LIMIT = 3
CONTRACT = 'klikai.pilot.editorial-search-excerpt.v1'
RECORD_CONTRACT = 'klikai.editorial-record.v1'
FULL_CONTRACT = 'klikai.pilot.editorial-record-capture.v1'
MAX_RECORD_BYTES = 65536
OPERATOR_POLICY_ID = 'damjan-local-pilot-editorial-20260914'


def freeze(data, query, source, locale):
    if source not in SOURCES or locale not in {'sl', 'it'}:
        raise Failure('invalid_request')
    if (not isinstance(data, dict) or data.get('query') != query or data.get('sources') != [source]
            or not isinstance(data.get('hits'), list) or len(data['hits']) > LIMIT
            or type(data.get('count')) is not int or data['count'] != len(data['hits'])):
        raise Failure('invalid_answer', 502)
    seen = set()
    for hit in data['hits']:
        if not isinstance(hit, dict):
            raise Failure('invalid_answer', 502)
        for key, maximum in (('resource_id', 250), ('title', 500), ('snippet', 1000)):
            value = hit.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise Failure('invalid_answer', 502)
        # These collections expose restricted search excerpts. This is a local
        # admission boundary, not permission to retrieve or reproduce full texts.
        if (hit.get('source_slug') != source or hit.get('rights_mode') not in {'restricted', 'owned', 'open'}
                or hit.get('format') != 'qa' or hit.get('chunk_kind') != 'structured_qa'
                or type(hit.get('chunk_index')) is not int or hit['chunk_index'] < 0):
            raise Failure('invalid_answer', 502)
        for key in ('url', 'source_badge', 'language', 'section', 'subsection'):
            if hit.get(key) is not None and not isinstance(hit[key], str):
                raise Failure('invalid_answer', 502)
        identity = (source, hit['resource_id'])
        if identity in seen:
            raise Failure('invalid_answer', 502)
        seen.add(identity)
    body = {'contract': CONTRACT, 'task': 'editorial_search', 'query': query, 'locale': locale,
            'source_slug': source, 'source_label': SOURCES[source], 'hits': deepcopy(data['hits']),
            'lookup_response_sha256': digest(data)}
    pin = digest(body)
    return {'bundle': dict(body, snapshot_sha256=pin),
            'identity': {'contract': CONTRACT, 'captured_response_sha256': pin}}


def checked(bundle):
    if (bundle.get('contract') not in {CONTRACT, FULL_CONTRACT} or bundle.get('snapshot_sha256') !=
            digest({k: v for k, v in bundle.items() if k != 'snapshot_sha256'})):
        raise Failure('resource_changed', 409)


def notice(locale):
    return ('Estratti di ricerca da consigli pubblicati pertinenti. Non sono una nuova risposta '
            'alla tua domanda. Il contesto può essere incompleto: la domanda originale e la risposta '
            'integrale non sono disponibili qui. Le qualificazioni omesse possono cambiare il significato.'
            if locale == 'it' else
            'Iskalni izseki iz sorodnih objavljenih jezikovnih nasvetov. To ni nov odgovor na vaše '
            'vprašanje. Kontekst je lahko nepopoln: celotno izvirno vprašanje in odgovor tukaj nista '
            'na voljo. Izpuščena pojasnila in omejitve lahko spremenijo pomen.')


def record_text(bundle, hit):
    it = bundle['locale'] == 'it'
    return (('Titolo pubblicato: ' if it else 'Naslov objave: ') + hit['title'] + '\n\n' +
            ('Fonte: ' if it else 'Vir: ') + bundle['source_label'] + '\n\n' +
            ('Estratto di ricerca — contesto incompleto:\n' if it else 'Iskalni izsek — nepopoln kontekst:\n') + hit['snippet'])


def evidence(bundle):
    return [{'evidence_id': 'editorial:' + bundle['snapshot_sha256'] + ':' + str(i),
             'source_slug': hit['source_slug'], 'source_label': bundle['source_label'],
             'external_id': hit['resource_id'], 'title': hit['title'], 'snippet': hit['snippet'],
             'rights_mode': hit['rights_mode'], 'url': hit.get('url'),
             'source_revision': None, 'captured_response_sha256': bundle['snapshot_sha256'],
             'chunk_index': hit['chunk_index']}
            for i, hit in enumerate(bundle['hits'])]


def answer(bundle, tier):
    checked(bundle)
    if tier != 'short':
        raise Failure('expansion_unavailable', 409)
    if bundle['contract'] == FULL_CONTRACT:
        return record_answer(bundle)
    hits = bundle['hits']
    intro = notice(bundle['locale']) if hits else (
        'Nessun estratto pertinente nella raccolta selezionata.' if bundle['locale'] == 'it'
        else 'V izbrani zbirki ni ustreznega iskalnega izseka.')
    blocks = [{'block_id': 'summary', 'text': intro}] + [
        {'block_id': 'editorial-record-' + str(i), 'text': record_text(bundle, hit)}
        for i, hit in enumerate(hits)]
    badges = [{'source_slug': bundle['source_slug'], 'label': hits[0].get('source_badge') or bundle['source_label'],
               'display_name': bundle['source_label'], 'rights_mode': hits[0]['rights_mode']}] if hits else []
    return {'status': 'source_only' if hits else 'no_hit', 'answer_kind': 'source_only',
            'generation_status': 'not_generated', 'short_answer': intro,
            'answer_md': '\n\n'.join(block['text'] for block in blocks), 'display_blocks': blocks,
            'source_badges': badges, 'evidence': evidence(bundle), 'limitations': [notice(bundle['locale'])],
            'deterministic_result': {'task': 'editorial_search', 'query': bundle['query'],
                                     'records': deepcopy(hits), 'complete_qa_available': False}}


def source(bundle, eid, tier):
    checked(bundle)
    if tier != 'short':
        raise Failure('not_found', 404)
    if bundle['contract'] == FULL_CONTRACT:
        return record_source(bundle, eid)
    for hit, item in zip(bundle['hits'], evidence(bundle)):
        if item['evidence_id'] == eid:
            return {'evidence_id': eid, 'source_label': bundle['source_label'], 'title': hit['title'],
                    'excerpt': notice(bundle['locale']) + '\n\n' + record_text(bundle, hit),
                    'url': hit.get('url'), 'bibliography': {'record_id': hit['resource_id'],
                        'rights_mode': hit['rights_mode'], 'source_date': None}}
    raise Failure('not_found', 404)


def freeze_records(discovery, records, operator_policy_id=None):
    """Admit one complete record-state response per validated search identity.

    Never promote the discovery snippet into a question or a paired answer.
    Full-text denial, an invalid record, or an exceeded bound fails the request.
    """
    checked(discovery)
    if operator_policy_id is not None and operator_policy_id != OPERATOR_POLICY_ID:
        raise Failure('invalid_answer', 502)
    if discovery['contract'] != CONTRACT or not isinstance(records, list) or not records or len(records) != len(discovery['hits']):
        raise Failure('invalid_answer', 502)
    text_fields = ('title', 'question_original', 'answer_text', 'article_text', 'additional_info', 'source_date', 'url', 'source_badge')
    required = {'contract', 'resource_id', 'source_slug', 'source_label', 'rights_mode',
                'content_status', 'complete_qa', 'missing_fields', 'release_id', 'content_hash', 'references', *text_fields}
    for hit, record in zip(discovery['hits'], records):
        if not isinstance(record, dict) or set(record) not in (required, required | {'access_policy'}):
            raise Failure('invalid_answer', 502)
        policy = record.get('access_policy')
        expected_policy = {'policy_id': OPERATOR_POLICY_ID, 'scope': 'authenticated_local_pilot',
                           'decision': 'operator_enabled', 'source_slug': hit['source_slug']}
        if operator_policy_id is not None:
            if policy != expected_policy:
                raise Failure('invalid_answer', 502)
        elif policy is not None:
            raise Failure('invalid_answer', 502)
        allowed_rights = {'owned', 'open', 'restricted'} if operator_policy_id else {'owned', 'open'}
        if len(json.dumps(record, ensure_ascii=False, separators=(',', ':')).encode()) > MAX_RECORD_BYTES:
            raise Failure('editorial_record_too_large', 413)
        if (record['contract'] != RECORD_CONTRACT or record['source_slug'] != hit['source_slug']
                or record['resource_id'] != hit['resource_id'] or record['source_label'] != SOURCES[hit['source_slug']]
                or record['rights_mode'] not in allowed_rights or hit['rights_mode'] != record['rights_mode']):
            raise Failure('invalid_answer', 502)
        for field in text_fields:
            if record[field] is not None and (not isinstance(record[field], str) or not record[field].strip()):
                raise Failure('invalid_answer', 502)
        if (not isinstance(record['release_id'], str) or not 1 <= len(record['release_id']) <= 250
                or not isinstance(record['content_hash'], str) or len(record['content_hash']) != 64
                or any(c not in '0123456789abcdef' for c in record['content_hash'])):
            raise Failure('invalid_answer', 502)
        if record['source_date'] is not None:
            try:
                if date.fromisoformat(record['source_date']).isoformat() != record['source_date']:
                    raise ValueError()
            except ValueError:
                raise Failure('invalid_answer', 502) from None
        refs = record['references']
        if (not isinstance(refs, list) or len(refs) > 64 or any(
                not isinstance(ref, dict) or set(ref) != {'label', 'url'} or any(
                    v is not None and not isinstance(v, str) for v in ref.values()) for ref in refs)):
            raise Failure('invalid_answer', 502)
        for url in [record['url'], *(ref['url'] for ref in refs)]:
            if url is None:
                continue
            try:
                parsed = urlsplit(url)
                if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username is not None or parsed.password is not None:
                    raise ValueError()
            except ValueError:
                raise Failure('invalid_answer', 502) from None
        complete = bool(record['question_original'] and record['answer_text'])
        if type(record['complete_qa']) is not bool or record['complete_qa'] != complete:
            raise Failure('invalid_answer', 502)
        expected_status = 'complete_qa' if complete else 'article' if record['article_text'] and not record['question_original'] else 'incomplete'
        if record['content_status'] != expected_status or (record['article_text'] and record['answer_text']):
            raise Failure('invalid_answer', 502)
        missing = record['missing_fields']
        if not isinstance(missing, list) or len(missing) > 10 or any(not isinstance(v, str) for v in missing) or (complete and missing):
            raise Failure('invalid_answer', 502)
    body = {'contract': FULL_CONTRACT, 'task': 'editorial_search', 'query': discovery['query'],
            'locale': discovery['locale'], 'source_slug': discovery['source_slug'],
            'source_label': discovery['source_label'], 'records': deepcopy(records),
            'discovery_response_sha256': discovery['lookup_response_sha256']}
    # Normal answer storage has its own finite presentation bound; fail explicitly.
    if sum(len(full_record_text(body, record)) for record in records) > 90000:
        raise Failure('editorial_record_too_large', 413)
    pin = digest(body)
    return {'bundle': dict(body, snapshot_sha256=pin),
            'identity': {'contract': FULL_CONTRACT, 'captured_response_sha256': pin}}


def full_notice(locale):
    return ('Materiale pubblicato pertinente alla ricerca, non una nuova risposta alla tua domanda. '
            'Domande e risposte provengono dallo stesso record; eventuali campi mancanti sono indicati.'
            if locale == 'it' else
            'Objavljeno gradivo, povezano z iskanjem; to ni nov odgovor na vaše vprašanje. '
            'Vprašanje in odgovor sta iz istega zapisa; morebitna manjkajoča polja so označena.')


def full_record_text(bundle, record):
    it = bundle['locale'] == 'it'
    labels = {'title': 'Titolo pubblicato' if it else 'Naslov objave',
              'question_original': 'Domanda originale' if it else 'Izvirno vprašanje',
              'answer_text': 'Risposta originale completa' if it else 'Celoten izvirni odgovor',
              'article_text': 'Articolo originale (nessuna domanda originale disponibile)' if it else 'Izvirni članek (izvirno vprašanje ni na voljo)',
              'additional_info': 'Ulteriori spiegazioni originali' if it else 'Dodatna izvirna pojasnila'}
    parts = [('Fonte: ' if it else 'Vir: ') + record['source_label']]
    for field, label in labels.items():
        if record[field] is not None:
            parts.append(label + ':\n' + record[field])
    if not record['complete_qa']:
        parts.append('Coppia originale domanda/risposta incompleta.' if it else 'Izvirni par vprašanja in odgovora ni popoln.')
    if record['source_date']:
        parts.append(('Data della fonte: ' if it else 'Datum vira: ') + record['source_date'])
    if record['references']:
        parts.append(('Riferimenti originali:\n' if it else 'Izvirne reference:\n') + '\n'.join(
            ' — '.join(v for v in (ref['label'], ref['url']) if v) for ref in record['references']))
    return '\n\n'.join(parts)


def record_evidence(bundle):
    return [{'evidence_id': 'editorial:' + bundle['snapshot_sha256'] + ':' + str(i),
             'source_slug': record['source_slug'], 'source_label': record['source_label'],
             'external_id': record['resource_id'], 'title': record['title'],
             'rights_mode': record['rights_mode'], 'url': record['url'], 'source_date': record['source_date'],
             'source_revision': None, 'source_content_hash': record['content_hash'],
             'stored_release_id': record['release_id'], 'captured_response_sha256': bundle['snapshot_sha256'],
             **({'access_policy': deepcopy(record['access_policy'])} if record.get('access_policy') else {})}
            for i, record in enumerate(bundle['records'])]


def record_answer(bundle):
    intro = full_notice(bundle['locale'])
    blocks = [{'block_id': 'summary', 'text': intro}] + [
        {'block_id': 'editorial-record-' + str(i), 'text': full_record_text(bundle, record)}
        for i, record in enumerate(bundle['records'])]
    records = bundle['records']
    return {'status': 'source_only', 'answer_kind': 'source_only', 'generation_status': 'not_generated',
            'short_answer': intro, 'answer_md': '\n\n'.join(block['text'] for block in blocks),
            'display_blocks': blocks, 'source_badges': [{'source_slug': bundle['source_slug'],
                'label': records[0]['source_badge'] or bundle['source_label'], 'display_name': bundle['source_label'],
                'rights_mode': records[0]['rights_mode']}], 'evidence': record_evidence(bundle),
            'limitations': [intro], 'deterministic_result': {'task': 'editorial_search', 'presentation': 'records',
                'query': bundle['query'], 'records': deepcopy(records),
                'complete_qa_available': all(record['complete_qa'] for record in records)}}


def record_source(bundle, eid):
    for record, item in zip(bundle['records'], record_evidence(bundle)):
        if item['evidence_id'] == eid:
            return {'evidence_id': eid, 'source_label': record['source_label'], 'title': record['title'],
                    'excerpt': full_notice(bundle['locale']) + '\n\n' + full_record_text(bundle, record),
                    'url': record['url'],
                    'bibliography': {'record_id': record['resource_id'], 'source_date': record['source_date'],
                        'rights_mode': record['rights_mode'], 'stored_release_id': record['release_id'],
                        'source_content_hash': record['content_hash'],
                        **({'access_policy': deepcopy(record['access_policy'])} if record.get('access_policy') else {})}}
    raise Failure('not_found', 404)
