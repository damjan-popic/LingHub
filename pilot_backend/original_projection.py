"""Closed, bounded display contract for retained ePravopis originals.

The capture hash binds this to the source record. Text is never HTML, including
table cells and publisher annotations; unknown fields are not public content.
"""
from copy import deepcopy
import json
import re

from .security import Failure

SCHEMA = 'klikai.epravopis-original-projection.v1'
SLUG = 'epravopis-slovar'
FIELDS = {'schema', 'source_slug', 'record_id', 'stable_id', 'headword',
          'homograph_marker', 'status_label', 'tables', 'citation_original',
          'citation_access_dates', 'source_date', 'source_date_role', 'tooltips',
          'entry_html_sha256', 'coverage', 'inventory_homograph_markers', 'homograph_discrepancy'}


def checked(value, *, source_slug=None, record_id=None):
    """Reject malformed/oversized originals rather than silently clipping them."""
    def require(ok):
        if not ok:
            raise Failure('invalid_answer', 502)

    def text(v, nullable=False):
        return (nullable and v is None) or isinstance(v, str)

    def sha(v):
        return isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) is not None

    require(isinstance(value, dict) and set(value) == FIELDS)
    require(len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) <= 2 * 1024 * 1024)
    require(value['schema'] == SCHEMA and value['source_slug'] == SLUG)
    require(source_slug is None or source_slug == value['source_slug'])
    require(isinstance(value['stable_id'], str) and re.fullmatch('[0-9]{6}', value['stable_id']))
    require(value['record_id'] == 'epravopis:dictionary:spt:' + value['stable_id'])
    require(record_id is None or record_id == value['record_id'])
    require(text(value['headword']) and value['headword'].strip() and value['status_label'] == 'Predlog')
    marker = value['homograph_marker']
    require(marker is None or isinstance(marker, str) and re.fullmatch('[0-9]{1,4}', marker))
    inventory_markers = value['inventory_homograph_markers']
    require(isinstance(inventory_markers, list) and len(inventory_markers) <= 32
            and all(isinstance(v, str) and re.fullmatch('[0-9]{1,4}', v) for v in inventory_markers))
    require(inventory_markers == sorted(set(inventory_markers)))
    require(type(value['homograph_discrepancy']) is bool
            and value['homograph_discrepancy'] == bool(inventory_markers and set(inventory_markers) != {marker}))
    require(text(value['source_date'], True) and value['source_date_role'] == 'publication_date_not_established'
            and value['source_date'] is None)
    require(sha(value['entry_html_sha256']))
    coverage = value['coverage']
    require(isinstance(coverage, dict) and set(coverage) == {
        'captured_entries', 'expected_observed_entries', 'accepted_missing_count', 'cumulative_complete'})
    require(all(type(coverage[k]) is int and coverage[k] >= 0 for k in
                ('captured_entries', 'expected_observed_entries', 'accepted_missing_count')))
    require(coverage['captured_entries'] + coverage['accepted_missing_count'] == coverage['expected_observed_entries']
            and coverage['cumulative_complete'] is False)
    for field in ('citation_original', 'citation_access_dates'):
        require(isinstance(value[field], list) and len(value[field]) <= 256 and all(text(v) for v in value[field]))
    require(all(re.fullmatch(r'\d{4}-\d{2}-\d{2}', v) for v in value['citation_access_dates']))
    tables = value['tables']
    require(isinstance(tables, list) and len(tables) <= 256)
    cells = 0
    for ordinal, table in enumerate(tables, 1):
        require(isinstance(table, dict) and set(table) == {'ordinal', 'html_sha256', 'rows'})
        require(type(table['ordinal']) is int and table['ordinal'] == ordinal and sha(table['html_sha256']))
        require(isinstance(table['rows'], list) and len(table['rows']) <= 10000)
        for row in table['rows']:
            require(isinstance(row, list))
            cells += len(row)
            require(cells <= 10000)
            for cell in row:
                require(isinstance(cell, dict) and {'kind', 'text', 'rowspan', 'colspan'} <= set(cell)
                        and set(cell) <= {'kind', 'text', 'rowspan', 'colspan', 'scope'})
                require(cell['kind'] in ('td', 'th') and text(cell['text']))
                require(type(cell['rowspan']) is int and 0 <= cell['rowspan'] <= 1000)
                require(type(cell['colspan']) is int and 1 <= cell['colspan'] <= 1000)
                require('scope' not in cell or cell['scope'] in ('row', 'col', 'rowgroup', 'colgroup'))
    require(isinstance(value['tooltips'], list) and len(value['tooltips']) <= 10000)
    for note in value['tooltips']:
        require(isinstance(note, dict) and set(note) == {
            'node_ordinal', 'scope_text', 'scope_html_sha256', 'label', 'target'})
        require(type(note['node_ordinal']) is int and note['node_ordinal'] > 0)
        require(text(note['scope_text']) and text(note['label']) and sha(note['scope_html_sha256']))
        require(note['target'] is None or isinstance(note['target'], str)
                and re.fullmatch(r'#[A-Za-z_][\w:-]*', note['target']))
    return deepcopy(value)


def reader_blocks(value, locale):
    """Lossless text/cell presentation; no inferred publication dates or rules."""
    p = checked(value)
    it = locale == 'it'
    c = p['coverage']
    captured = f"{c['captured_entries']:,}".replace(',', '.')
    expected = f"{c['expected_observed_entries']:,}".replace(',', '.')
    coverage = ((f"Sono conservate {captured} delle {expected} voci "
                 f"dell’elenco dell’editore. Per {c['accepted_missing_count']} voci non è stato possibile acquisire l’originale. "
                 'Non è accertato che l’elenco comprenda l’intero dizionario.') if it else
                (f"Ohranjenih je {captured} od {expected} sestavkov "
                 f"s seznama izdajatelja. Pri {c['accepted_missing_count']} sestavkih izvirnika ni bilo mogoče pridobiti. "
                 'Ni potrjeno, da seznam zajema celoten slovar.'))
    blocks = [{'heading': 'Stato della voce' if it else 'Status sestavka', 'text': p['status_label']},
              {'kind': 'collection_coverage', 'heading': 'Copertura della raccolta' if it else 'Obseg zbirke', 'text': coverage}]
    if p['homograph_discrepancy']:
        marker = p['homograph_marker'] or ('assente' if it else 'je ni')
        inventory = ', '.join(p['inventory_homograph_markers'])
        blocks.append({'heading': 'Numerazione degli omografi' if it else 'Oznake enakopisnic',
                       'text': (f'Numero nella voce originale: {marker}. Numero nell’elenco dell’editore: {inventory}. '
                                'Le indicazioni dell’editore sono conservate separatamente.' if it else
                                f'Oznaka v izvirnem sestavku: {marker}. Oznaka na seznamu izdajatelja: {inventory}. '
                                'Različni oznaki izdajatelja sta ohranjeni ločeno.')})
    for table in p['tables']:
        blocks.append({'kind': 'table', 'heading': ('Tabella originale ' if it else 'Izvirna preglednica ')
                       + str(table['ordinal']), 'rows': table['rows']})
    if p['tooltips']:
        blocks.append({'heading': 'Annotazioni originali' if it else 'Izvirne opombe',
                       'text': '\n\n'.join(note['scope_text'] + ' — ' + note['label']
                                          for note in p['tooltips'])})
    if p['citation_original']:
        blocks.append({'heading': 'Citazione originale' if it else 'Izvirni navedek vira',
                       'text': '\n\n'.join(p['citation_original'])})
    if p['citation_access_dates']:
        blocks.append({'heading': 'Date di consultazione nella citazione (non date di pubblicazione)' if it
                       else 'Datumi dostopa v navedku (niso datumi objave)',
                       'text': '; '.join(p['citation_access_dates'])})
    blocks.append({'heading': 'Data di pubblicazione' if it else 'Datum objave',
                   'text': 'Non accertata per questa voce.' if it else 'Za ta sestavek ni ugotovljen.'})
    return blocks
