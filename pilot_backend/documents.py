"""Find published documents through the recorded Gradivo search contract.

Search stems are approximate retrieval keys, never linguistic analyses. Each
answer retains the query, returned candidates and chosen original URLs.
"""
import re
import unicodedata
from urllib.parse import urlencode, urlsplit

from .security import Failure

GENRES = r'(?:obraz(?:ec|c\w*)|predlog(?:[aeiou]\w*)?|vzor(?:ec|c\w*)|samoizjav\w*|pooblastil\w*|izjav\w*|dokument\w*|zapisnik\w*|statut\w*|pravilnik\w*|zakon\w*|gradiv\w*|publikacij\w*|organigram\w*|seznam\w*|priročnik\w*|modul[oi]|modell[oi]|autodichiarazion\w*|dichiarazion\w*|deleg[ae]|document\w*|verbal[ei]|statut[oi]|regolament\w*|pubblicazion\w*)'
STOP = set('kje kam kako najdem najti najdite poišči poiščite iščem iščemo dobim dobiti prenesem prenesti prenos potrebujem potrebujemo rabim želim hočem prosim pokaži pokažite imam bi lahko ali mi nam za na v z s iz od do je so sem si in ter pa ta to te tisti samo me zanima kje a delle della del dell di da dei degli le la il lo i gli un una uno dove trovo trovare cercare cerco cercando scaricare scarico vorrei voglio bisogno posso puoi per con nel nella in e che come please find download pdf docx slovenski slovensko slovenščini italijanski italijansko italijanščini slovenščino italiano italiana sloveno slovena dvojezični dvojezično bilingue obrazec obrazca obrazci obrazce obrazcev obrazcu obrazcem predloga predlogo predloge predlogi predlog vzorec vzorca vzorec dokument dokumenta dokumenti documento documenti modulo moduli modello modelli'.split())
HOSTS = {'www.jeziknaklik.it', 'jeziknaklik.it', 'www.regione.fvg.it', 'drive.google.com'}


def tokens(value):
    return re.findall(r'[^\W_]+', unicodedata.normalize('NFKC', value).casefold())


def stem(word):
    # Bounded surface expansion handles OOV titles such as samoizjavo. It is
    # deliberately not exported as a lemma or used to justify a language answer.
    if len(word) >= 5:
        candidate = re.sub(r'(?:ami|ama|ega|emu|oma|ov|om|em|ih|ah|[aeiou])$', '', word)
        return candidate if len(candidate) >= 5 else re.sub(r'[aeiou]$', '', word)
    return word


def plan(question):
    words = [w for w in tokens(question) if w not in STOP and len(w) >= 2]
    terms = list(dict.fromkeys(stem(w) for w in words))[:12]
    formats = [w.upper() for w in tokens(question) if w in {'pdf', 'docx', 'zip', 'jpg'}]
    q = question.casefold()
    section = None
    for expression, name in [
        (r'\b(?:obraz(?:ec|c\w*)|modul[oi]|pooblastil\w*|izjav\w*|samoizjav\w*|dichiarazion\w*|autodichiarazion\w*|deleg[ae])\b', 'Obrazci'),
        (r'\b(?:predlog(?:[aeiou]\w*)?|vzor(?:ec|c\w*))\b', 'Predloge'),
        (r'\b(?:zapisnik\w*|verbal[ei])\b', 'Zapisniki'),
    ]:
        if re.search(expression, q):
            section = name
            break
    return {'query': ' '.join(terms) or 'obrazec', 'terms': terms,
            'formats': list(dict.fromkeys(formats)), 'section': section}


def lookup(gateway, question, locale):
    if gateway is None or not gateway.klikai_url:
        raise Failure('source_unavailable', 503)
    query = plan(question)
    params = {'q': query['query'], 'sources': 'gradivo', 'limit': 20}
    if query['formats']:
        params['formats'] = query['formats']
    if query['section']:
        params['section'] = query['section']
    response = gateway.request('/api/search?' + urlencode(params, doseq=True),
                               None, base=gateway.klikai_url)
    hits = response.get('hits')
    if not isinstance(hits, list) or len(hits) > 20:
        raise Failure('source_unavailable', 503)
    selected = []
    seen = set()
    for hit in hits:
        if not isinstance(hit, dict) or hit.get('source_slug') != 'gradivo':
            raise Failure('source_unavailable', 503)
        url = hit.get('url') or ''
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password:
            continue
        title = hit.get('title') or ''
        title_words = tokens(title + ' ' + (hit.get('subsection') or ''))
        body_words = tokens(hit.get('snippet') or '')
        def matched(term, words):
            return any(stem(w).startswith(term) for w in words)
        # SQL supplies OR candidates. A document recommendation needs every
        # meaningful requested term, rather than an incidental matching word.
        if not all(matched(t, title_words + body_words) for t in query['terms']):
            continue
        if url in seen:
            continue
        seen.add(url)
        exact = sum(matched(t, title_words) for t in query['terms'])
        selected.append((exact, hit))
    selected.sort(key=lambda pair: (-pair[0], -float(pair[1].get('score', 0)),
                                    pair[1]['resource_id']))
    if query['terms'] and selected and selected[0][0] == len(query['terms']):
        selected = [pair for pair in selected if pair[0] == len(query['terms'])]
    resources = []
    for _, hit in selected[:12]:
        fmt = hit.get('format') or 'HTML'
        title = hit['title'] + ' · ' + fmt
        resources.append({'key': hit['resource_id'], 'title': title, 'url': hit['url'],
                          'verified_at': '2026-10-20', 'capability': 'web_access' if fmt == 'HTML' else 'download',
                          'source_slug': 'gradivo', 'source_label': 'Obrazci in gradivo',
                          'format': fmt, 'preview': hit.get('snippet', ''),
                          'chunk_kind': hit.get('chunk_kind'), 'chunk_index': hit.get('chunk_index')})
    it = locale == 'it'
    if resources:
        text = ('Ho trovato queste risorse. Apri il riferimento per accedere al file originale:' if it else
                'Našel sem naslednje gradivo. S klikom na navedbo vira odprete povezavo do izvirnika:')
        text += '\n\n' + '\n'.join(f'{r["title"]} [{i + 1}]' for i, r in enumerate(resources))
        if any('covid' in r['title'].casefold() for r in resources):
            text += ('\n\nI documenti contrassegnati COVID-19 sono esempi per specifiche procedure durante la pandemia; non sono moduli generici.' if it else
                     '\n\nGradivo z oznako COVID-19 vsebuje primere za posamezne postopke med pandemijo; to niso splošni obrazci za vse namene.')
    else:
        text = ('Mi dispiace, nelle fonti non trovo una risposta adeguata a questa domanda. Puoi precisare il titolo o lo scopo del documento? Lascia un commento sulla risposta per segnalarci il modulo mancante.' if it else
                'Oprostite, za to vprašanje v virih ne najdem pravega odgovora. Lahko navedete naslov ali namen dokumenta? V povratni informaciji nam sporočite, kateri obrazec manjka.')
    return {'kind': 'document_search', 'text': text, 'resources': resources,
            'retrieval': {'contract': 'gradivo.document-lookup.v1', 'plan': query,
                          'response': response, 'selected_ids': [r['key'] for r in resources]}}
