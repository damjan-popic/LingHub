"""Bounded applications of recorded paradigms, separate from raw predictions.

This is the ordinary attributive adjective+noun agreement rule, not a parser
and not a correctness checker. It does not apply across arbitrary neighbours,
clauses, quotations or missing lexical slots. Source editorial labels and
frequency are deliberately absent from the choice of a grammatical reading.
"""
import copy

VERSION = 'recorded-adjective-noun-agreement-v1'
RULE_ID = 'sl-adjective-noun-agreement-v1'
RULE_SOURCE = {
    'source_id': 'slo-na-dlani:147',
    'source_url': 'https://www.slo-na-dlani.si/znanje/147',
    'title': 'Pridevniška beseda in pridevnik',
    'accessed': '2026-10-18',
    'scope': 'Ordinary inflecting attributive adjective agrees with its noun in gender, number and case.',
}
FEATURES = ('case', 'number', 'gender')
CASES = {'nominative':'imenovalniku','genitive':'rodilniku','dative':'dajalniku',
         'accusative':'tožilniku','locative':'mestniku','instrumental':'orodniku'}
NUMBERS = {'singular':'ednine','dual':'dvojine','plural':'množine'}
CASE_LABELS = {'nominative':'imenovalnik','genitive':'rodilnik','dative':'dajalnik',
               'accusative':'tožilnik','locative':'mestnik','instrumental':'orodnik'}


def _readings(form, upos):
    """Retain lexeme and source slot; do not merge proper/common homographs."""
    allowed = {'ADJ': {'adjective'}, 'NOUN': {'noun'}, 'PROPN': {'noun'}}[upos]
    out = []
    for item in form.get('recorded_readings', []):
        features = item.get('features', {})
        if item.get('grammatical_type') not in allowed or item.get('exact_surface') is not True:
            continue
        if upos == 'NOUN' and features.get('type') != 'common':
            continue
        if upos == 'PROPN' and features.get('type') != 'proper':
            continue
        if all(features.get(key) for key in FEATURES):
            out.append(item)
    return out


def interpret_phrase(body, original):
    """Return new facts; never mutate the original recorded or model analyses."""
    engines = body.get('engines', {})
    primary = engines.get('classla') or engines.get('spacy') or {}
    tokens = primary.get('tokens', [])
    facts, unresolved = [], []
    for i, adjective in enumerate(tokens[:-1]):
        noun = tokens[i + 1]
        if adjective.get('upos') != 'ADJ' or noun.get('upos') not in {'NOUN', 'PROPN'}:
            continue
        a, b = adjective.get('start'), noun.get('end')
        if type(a) is not int or type(b) is not int:
            continue
        gap = original[adjective['end']:noun['start']]
        if not gap or not gap.isspace():
            continue
        # A complete selected two-word phrase explicitly supplies the candidate
        # relationship. In a longer context require an attached preposition;
        # POS adjacency by itself cannot establish a modifying relationship.
        bounded = len(tokens) == 2
        adpositional = i > 0 and tokens[i - 1].get('upos') == 'ADP' and original[tokens[i - 1]['end']:a].isspace()
        if not (bounded or adpositional):
            unresolved.append({'fact_class':'unresolved_inference', 'span':{'start':a,'end':b},
                               'reason':'adjective_noun_relationship_not_established'})
            continue
        left = _readings(body.get('recorded_forms', {}).get(adjective['text'], {}), 'ADJ')
        right = _readings(body.get('recorded_forms', {}).get(noun['text'], {}), noun['upos'])
        if not left or not right:
            unresolved.append({'fact_class':'unresolved_inference', 'span':{'start':a,'end':b},
                               'reason':'complete_exact_surface_slots_required'})
            continue
        matches = [(x, y) for x in left for y in right
                   if all(x['features'][key] == y['features'][key] for key in FEATURES)]
        values = sorted({tuple(x['features'][key] for key in FEATURES) for x, _ in matches})
        if not matches and any(body['recorded_forms'][w['text']].get('candidate_inventory_truncated') for w in (adjective,noun)):
            unresolved.append({'fact_class':'unresolved_inference','span':{'start':a,'end':b},
                               'reason':'truncated_candidate_inventory_cannot_establish_incompatibility'})
            continue
        readings = [dict(zip(FEATURES, value)) for value in values]
        identities = {(x['source_entry_id'], y['source_entry_id']) for x, y in matches}
        # Several lexemes with the same grammatical features are still distinct
        # lexical readings. They may support a shared case but not one sense.
        status = 'supported' if len(readings) == 1 else 'ambiguous' if readings else 'incompatible'
        sources = []
        for surface, selected in ((adjective['text'], left), (noun['text'], right)):
            for item in selected:
                row = {k:copy.deepcopy(item.get(k)) for k in
                       ('lemma','source_entry_id','slot_locator','source_locator','features')}
                row['surface'] = surface
                if row not in sources:
                    sources.append(row)
        expression = original[a:b]
        if status == 'supported':
            reading = readings[0]
            text = (f'V običajni pridevniško-samostalniški zvezi je »{expression}« v '
                    f'{CASES.get(reading["case"], reading["case"])} {NUMBERS.get(reading["number"], reading["number"])}. '
                    'To branje podpirajo zapisane oblike obeh besed in ujemanje v spolu, številu in sklonu.')
            isolated_cases = sorted({item['features']['case'] for item in right})
            if len(isolated_cases) > 1:
                labels = [CASE_LABELS.get(case,case) for case in isolated_cases]
                text += (f' Pri samostojni obliki »{noun["text"]}« '
                         + ('sta zapisana ' if len(labels)==2 else 'so zapisani ')
                         + ', '.join(labels[:-1]) + ' in ' + labels[-1] + '.')
        elif status == 'ambiguous':
            text = f'Zapisane oblike zveze »{expression}« dopuščajo več ujemalnih branj; za izbiro je potreben kontekst.'
        else:
            text = (f'Za »{expression}« zapisane oblike nimajo skupnega sklona, števila in spola '
                    'v običajni pridevniško-samostalniški zvezi. Drugačna skladenjska vloga zahteva kontekst.')
        facts.append({'fact_class':'supported_application', 'derivation_id':f'{RULE_ID}:{a}:{b}',
                      'rule_id':RULE_ID, 'rule_source':copy.deepcopy(RULE_SOURCE),
                      'construction':{'kind':'attributive_adjective_noun','start':a,'end':b,'text':expression,
                                      'basis':'bounded adjective+noun phrase' if bounded else 'adpositional phrase',
                                      'conditional':True},
                      'source_ids':sorted({x['source_entry_id'] for x in sources}),
                      'source_forms':sources,'readings':readings,'lexeme_pairs':[list(x) for x in sorted(identities)],
                      'lexeme_ambiguity':len(identities)>1,'status':status,'text_sl':text,
                      'scope':'Conditional grammatical application; no universal correctness or normative preference claim.'})
    return {'interpretation_version':VERSION,'supported_derivations':facts,'unresolved_inferences':unresolved}
