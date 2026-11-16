"""Readable per-form source qualifications for new captured records only."""
import json

LABELS = {
    'usageStatus': ('Indicazione d’uso', 'Oznaka rabe'), 'usage': ('Uso', 'Raba'),
    'geographicalUsage': ('Uso geografico', 'Zemljepisna raba'),
    'usageField': ('Settore d’uso', 'Področje rabe'), 'usageRegister': ('Registro', 'Register'),
    'partOfSpeech': ('Parte del discorso', 'Besedna vrsta'),
    'definition': ('Definizione', 'Definicija'), 'notes': ('Note', 'Opombe'),
    'motivation': ('Motivazione', 'Utemeljitev'), 'context': ('Contesto', 'Sobesedilo'),
    'phraseology': ('Fraseologia', 'Frazeologija'), 'morphosyntax': ('Morfosintassi', 'Oblikoskladnja'),
    'variantForms': ('Varianti registrate', 'Zapisane različice'),
    'definitionSource': ('Fonte della definizione', 'Vir definicije'),
    'notesSource': ('Fonte delle note', 'Vir opomb'),
    'motivationSource': ('Fonte della motivazione', 'Vir utemeljitve'),
    'contextSource': ('Fonte del contesto', 'Vir sobesedila'),
}


def value_text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def record_text(hit, locale):
    it = locale == 'it'; language = 0 if it else 1
    status = hit['record_status'] if hit['record_status'] is not None else ('non indicato' if it else 'ni naveden')
    parts = [hit['title'], ('Stato del record nella fonte: ' if it else 'Status zapisa v viru: ') + status]
    if hit.get('record_status_source'):
        parts.append(('Fonte dello stato: ' if it else 'Vir statusa: ') + value_text(hit['record_status_source']))
    for field, title in [('categories', 'Ambito' if it else 'Področje'),
                         ('subcategories', 'Sottoambito' if it else 'Podpodročje')]:
        if hit.get(field):
            parts.append(title + ':\n' + '\n'.join(hit[field]))
    entries = hit.get('term_entries') or []
    if entries:
        parts.append('Forme registrate con le proprie indicazioni e condizioni:' if it else
                     'Zapisane oblike z njihovimi oznakami in pogoji:')
        for entry in entries:
            term = entry.get('term', '')
            language_label = ' (' + value_text(entry['lang']) + ')' if entry.get('lang') else ''
            fields = [(key, value) for key, value in entry.items() if key not in {'term', 'lang'}
                      and value not in (None, '', [], {})]
            detail = '\n\n'.join(LABELS.get(key, (key, key))[language] + ': ' + value_text(value)
                                  for key, value in fields)
            parts.append(('«' + term + '»' if it else '»' + term + '«') + language_label
                         + ('\n\n' + detail if detail else ''))
    # Preserve native list membership too. These labels assert recorded forms,
    # never a preference inferred from the concept's editorial status.
    labels = [('original_terms', 'Forme originali registrate' if it else 'Zapisane izvirne oblike'),
              ('standardized_terms', 'Forme slovene registrate' if it else 'Zapisane slovenske oblike'),
              ('recorded_terms', 'Altre forme registrate' if it else 'Druge zapisane oblike'),
              ('proposals', 'Proposte nel record' if it else 'Predlogi v zapisu'),
              ('rationale', 'Motivazione originale' if it else 'Izvirna utemeljitev')]
    for field, title in labels:
        if hit.get(field):
            parts.append(title + ':\n' + '\n'.join(hit[field]))
    parts.append(('Fonte: ' if it else 'Vir: ') + hit['source_display_name'])
    if hit.get('source_date'):
        parts.append(('Data della fonte: ' if it else 'Datum vira: ') + hit['source_date'])
    return '\n\n'.join(parts)
