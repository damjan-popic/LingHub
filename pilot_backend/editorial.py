"""Owned, frozen search excerpts from the two accepted Luka Q&A collections.

The accepted search contract has no complete-question/answer or detail reader.
Never reconstruct that missing material, or turn a search hit into new advice.
"""
from copy import deepcopy

from .security import Failure
from .store import digest

SOURCES = {'jezikovna_urgenca': 'Jezikovna urgenca',
           'jezikovna_posta': 'Jezikovna pošta / Druga pomoč'}
LIMIT = 3
CONTRACT = 'klikai.pilot.editorial-search-excerpt.v1'


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
        if (hit.get('source_slug') != source or hit.get('rights_mode') != 'restricted'
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
    if (bundle.get('contract') != CONTRACT or bundle.get('snapshot_sha256') !=
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
    hits = bundle['hits']
    intro = notice(bundle['locale']) if hits else (
        'Nessun estratto pertinente nella raccolta selezionata.' if bundle['locale'] == 'it'
        else 'V izbrani zbirki ni ustreznega iskalnega izseka.')
    blocks = [{'block_id': 'summary', 'text': intro}] + [
        {'block_id': 'editorial-record-' + str(i), 'text': record_text(bundle, hit)}
        for i, hit in enumerate(hits)]
    badges = [{'source_slug': bundle['source_slug'], 'label': hits[0].get('source_badge') or bundle['source_label'],
               'display_name': bundle['source_label'], 'rights_mode': 'restricted'}] if hits else []
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
    for hit, item in zip(bundle['hits'], evidence(bundle)):
        if item['evidence_id'] == eid:
            return {'evidence_id': eid, 'source_label': bundle['source_label'], 'title': hit['title'],
                    'excerpt': notice(bundle['locale']) + '\n\n' + record_text(bundle, hit),
                    'url': hit.get('url'), 'bibliography': {'record_id': hit['resource_id'],
                        'rights_mode': hit['rights_mode'], 'source_date': None}}
    raise Failure('not_found', 404)
