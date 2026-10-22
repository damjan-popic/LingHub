"""Text-only views of retained sources; never alter their captured bytes."""
from html import escape
from html.parser import HTMLParser
import json
import re
import unicodedata

# Verified character descriptions in the publisher's Amebis/ZRCola database.
# This is a display projection for that publication, never a raw-text rewrite.
ZRCOLA_UNICODE = {'\ue466':'ə\u0300', '\ue72f':'u\u032f',
                 '\uebd5':'ž\u0304', '\ue951':'d\u0304'}


class Prose(HTMLParser):
    """Interpret known presentation tags, preserving unknown linguistic brackets."""
    inline = {'b', 'strong', 'i', 'em', 'u', 'span', 'a', 'sup', 'sub', 'small'}
    blocks = {'p', 'div', 'br', 'li', 'ul', 'ol', 'table', 'tr', 'td', 'th',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'section'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = []
        self.literal = []
        self.closed_tags = set()

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.skip.append(tag)
        elif self.skip:
            return
        elif tag in {'code', 'pre'}:
            self.literal.append(tag)
        elif self.literal:
            self.parts.append(self.get_starttag_text())
        elif tag in self.blocks:
            self.parts.append('\n' + ('• ' if tag == 'li' else ''))
        elif tag not in self.inline or tag not in self.closed_tags:
            self.parts.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {'br', 'hr', 'img'}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
        elif self.literal:
            if tag == self.literal[-1]:
                self.literal.pop()
            else:
                self.parts.append('</' + tag + '>')
        elif tag in self.blocks:
            self.parts.append('\n')
        elif tag not in self.inline and tag not in {'code', 'pre'}:
            self.parts.append('</' + tag + '>')

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)


def prose(value):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'`[^`\n]*<[^`\n]*`|[»«“”"][^»«“”"\n]*<[^»«“”"\n]*[»«“”"]',
                   lambda m: escape(m[0], quote=False), value)
    # Decode entities once after parsing: encoded literal markup is not executed
    # or mistaken for actual formatting. Angle-bracket linguistic notation stays.
    parser = Prose()
    parser.closed_tags = set(re.findall(r'</([A-Za-z][A-Za-z0-9]*)\s*>', value.lower()))
    parser.feed(value)
    value = ''.join(parser.parts).replace('\xa0', ' ')
    value = re.sub(r'(?m)^[ \t]*#{1,6}[ \t]+', '', value)
    value = re.sub(r'\*\*([^\n]+?)\*\*', r'\1', value)
    value = re.sub(r'[ \t]+\n', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


FIELDS = {
    'title': ('Naslov', 'Titolo'),
    'source_status': ('Status zapisa', 'Stato della voce'),
    'categories': ('Področje', 'Ambito'),
    'subcategories': ('Podpodročje', 'Sottoambito'),
    'question_original': ('Izvirno vprašanje', 'Domanda originale'),
    'answer_text': ('Izvirni odgovor', 'Risposta originale'),
    'article_text': ('Izvirni članek', 'Articolo originale'),
    'additional_info': ('Dodatna pojasnila', 'Ulteriori spiegazioni'),
    'original_terms': ('Izvirni izrazi', 'Termini originali'),
    'standardized_terms': ('Standardizirani izrazi', 'Termini standardizzati'),
    'proposals': ('Predlogi v zapisu', 'Proposte nella voce'),
    'rationale': ('Utemeljitev', 'Motivazione'),
    'original_text': ('Izvirno besedilo', 'Testo originale'),
    'text': ('Besedilo', 'Testo'),
    'snippet': ('Izsek', 'Estratto'),
    'references': ('Navedeni viri', 'Riferimenti'),
    'examples': ('Primeri', 'Esempi'),
}
TERM_FIELDS = {
    'term': ('Izraz', 'Termine'), 'language': ('Jezik', 'Lingua'),
    'lang': ('Jezik', 'Lingua'), 'status': ('Status', 'Stato'),
    'normativeStatus': ('Status', 'Stato'), 'definition': ('Razlaga', 'Definizione'),
    'note': ('Opomba', 'Nota'), 'notes': ('Opombe', 'Note'),
    'usageNote': ('Opomba o rabi', 'Nota d’uso'),
    'rationale': ('Utemeljitev', 'Motivazione'),
    'context': ('Sobesedilo', 'Contesto'),
    'example': ('Primer', 'Esempio'), 'variantForms': ('Različice', 'Varianti'),
    'motivation': ('Utemeljitev', 'Motivazione'),
    'usage': ('Raba', 'Uso'), 'geographicalUsage': ('Območje rabe', 'Area d’uso'),
    'phraseology': ('Besedne zveze', 'Locuzioni'),
    'partOfSpeech': ('Besedna vrsta', 'Parte del discorso'),
    'administrativeStatus': ('Status zapisa', 'Stato della voce'),
    'source': ('Vir', 'Fonte'),
    'motivationSource': ('Vir utemeljitve', 'Fonte della motivazione'),
    'definitionSource': ('Vir razlage', 'Fonte della definizione'),
    'contextSource': ('Vir sobesedila', 'Fonte del contesto'),
}
LORIS_FIELDS = {
    'Iscete': ('Iskani izraz', 'Espressione cercata'),
    'Izhodisce': ('Izhodišče', 'Forma di partenza'),
    'SteMislili': ('Predlagani izraz', 'Espressione proposta'),
    'Pravilno': ('Priporočena oblika', 'Forma consigliata'),
    'Vslo': ('Razlaga v slovenščini', 'Spiegazione in sloveno'),
    'Razlaga': ('Razlaga', 'Spiegazione'), 'Npr': ('Primeri', 'Esempi'),
    'Opomba': ('Opomba', 'Nota'), 'Vir': ('Vir', 'Fonte'),
    'ItaIme': ('Italijansko ime', 'Nome italiano'),
    'SloIme': ('Slovensko ime', 'Nome sloveno'),
    'Kaj': ('Vrsta', 'Tipo'), 'Obcina': ('Občina', 'Comune'),
    'Pridevnik': ('Pridevnik', 'Aggettivo'),
    'PreM': ('Prebivalsko ime — moško', 'Abitante — maschile'),
    'PreZ': ('Prebivalsko ime — žensko', 'Abitante — femminile'),
}


def content(value):
    if isinstance(value, str):
        return prose(value)
    if isinstance(value, list):
        return '\n'.join(x for item in value if (x := content(item)))
    if isinstance(value, dict):
        # A source reference, never an inferred or generated URL.
        return ' — '.join(prose(value[k]) for k in
                          ('label', 'text', 'url', 'example', 'original', 'translation', 'sl', 'it')
                          if isinstance(value.get(k), str))
    return ''


def structured(record, locale):
    it = locale == 'it'
    if isinstance(record.get('payload'), dict):
        return [{'heading': labels[it], 'text': content(record['payload'][key])}
                for key, labels in LORIS_FIELDS.items() if content(record['payload'].get(key))]
    if isinstance(record.get('fields'), dict) and record.get('kind'):
        from .lexical import reader, GRAMMAR
        if record['kind'] == 'morphology':
            field = record['fields']; slot = field.get('slot', {})
            grammar = GRAMMAR['it' if it else 'sl']
            description = ' · '.join(grammar[str(v)] for v in
                slot.get('normalized_features', {}).values() if str(v) in grammar)
            forms = ' / '.join(dict.fromkeys(v['form'] for v in slot.get('variants', [])))
            if forms:
                return [{'heading': field.get('lemma', record.get('target', '')),
                         'text': description + '\n' + forms}]
            if field.get('lemma'):
                return [{'heading': field['lemma'], 'text': ' · '.join(
                    grammar[str(v)] for v in field.get('grammatical_features', {}).values()
                    if str(v) in grammar)}]
        if record['kind'] == 'loris':
            return structured({'payload':record['fields'].get('payload',{})},locale)
        unit = dict(record)
        unit.setdefault('match_relation', 'recorded_target')
        unit.setdefault('selection', {'state': 'primary'})
        unit.setdefault('target', '')
        unit.setdefault('record_id', '')
        profile = {'interpretation': {'slot_focus': None},
                   'facets': {'morphology': {'data': []}}, 'evidence': [unit]}
        try:
            view = reader({'locale': locale, 'analysis': {
                'version': 'klikai.lexical-collation.v1',
                'captures': [{'response': profile}]}})
        except (KeyError, TypeError):
            view = None
        if view and view['sections']:
            return [{'heading': section['title'], 'text': '\n\n'.join(
                '\n'.join(prose(row.get(k, '')) for k in ('title', 'text'))
                for row in section['items'])} for section in view['sections']]
    rows = []
    for field, labels in FIELDS.items():
        if field == 'snippet' and record.get('original_text'):
            continue
        text = content(record.get(field))
        if text:
            rows.append({'heading': labels[it], 'text': text})
    for entry in record.get('term_entries', []):
        if not isinstance(entry, dict):
            continue
        text = '\n'.join(labels[it] + ': ' + content(entry[key])
                         for key, labels in TERM_FIELDS.items() if content(entry.get(key)))
        if text:
            rows.append({'heading': 'Izraz v viru' if not it else 'Termine nella fonte',
                         'text': text})
    return rows


def source_view(source, locale='sl'):
    """An additive display projection, leaving excerpt and frozen hashes intact."""
    original = source.get('excerpt') or source.get('text') or ''
    parsed = None
    if isinstance(original, str) and original.lstrip().startswith('{'):
        try:
            parsed = json.loads(original)
        except ValueError:
            pass
    rows = structured(parsed, locale) if isinstance(parsed, dict) else []
    if not rows:
        rendered=prose(original)
        # Older captures used a plain-text serialization of grouped term fields.
        # Localize only closed schema labels at line boundaries, never prose.
        for key,labels in TERM_FIELDS.items():
            rendered=re.sub(r'(?m)^'+re.escape(key)+r'(?=\s*(?:—|:))',labels[locale=='it'],rendered)
        lines=[]
        for line in rendered.splitlines():
            if line.strip().startswith('['):
                try:
                    value=json.loads(line)
                    if isinstance(value,list):line=content(value)or line
                except ValueError:
                    pass
            lines.append(line)
        rows = [{'heading': '', 'text': '\n'.join(lines)}]
    identity = ' '.join(str(source.get(k, '')) for k in
                        ('source_id', 'source_slug', 'source_label', 'title')).casefold()
    if 'epravopis' in identity:
        rows = [{k: unicodedata.normalize('NFC', ''.join(ZRCOLA_UNICODE.get(c,c) for c in v))
                 for k,v in row.items()} for row in rows]
    if source.get('original_projection') is not None:
        from .original_projection import reader_blocks
        rows.extend(reader_blocks(source['original_projection'], locale))
    # The display contract is projected into bounded, text-only reader blocks;
    # raw captures and internal checksum/binding fields are not browser content.
    return dict({k: v for k, v in source.items() if k != 'original_projection'}, reader_blocks=rows)
