"""Feature-aware labels for source grammatical descriptions, without inference."""
import json

POS = {
    'noun': ('samostalnik', 'sostantivo'),
    'adjective': ('pridevnik', 'aggettivo'),
    'adverb': ('prislov', 'avverbio'),
    'pronoun': ('zaimek', 'pronome'),
    'numeral': ('števnik', 'numerale'),
    'verb': ('glagol', 'verbo'),
    'determiner': ('določilnik', 'determinante'),
}

FEATURES = {
    'case': {
        'nominative': ('imenovalnik', 'nominativo'),
        'genitive': ('rodilnik', 'genitivo'),
        'dative': ('dajalnik', 'dativo'),
        'accusative': ('tožilnik', 'accusativo'),
        'locative': ('mestnik', 'locativo'),
        'instrumental': ('orodnik', 'strumentale')},
    'number': {'singular': ('ednina', 'singolare'),
        'dual': ('dvojina', 'duale'), 'plural': ('množina', 'plurale')},
    'owner_number': {'singular': ('lastnik: ednina', 'possessore: singolare'),
        'dual': ('lastnik: dvojina', 'possessore: duale'),
        'plural': ('lastnik: množina', 'possessore: plurale')},
    'gender': {'masculine': ('moški spol', 'maschile'),
        'feminine': ('ženski spol', 'femminile'), 'neuter': ('srednji spol', 'neutro')},
    'degree': {'positive': ('osnovnik', 'positivo'),
        'comparative': ('primernik', 'comparativo'), 'superlative': ('presežnik', 'superlativo')},
    'definiteness': {'yes': ('določna oblika', 'forma definita'),
        'no': ('nedoločna oblika', 'forma indefinita')},
    'animate': {'yes': ('živo', 'animato'), 'no': ('neživo', 'inanimato')},
    'animacy': {'animate': ('živo', 'animato'), 'inanimate': ('neživo', 'inanimato')},
    'person': {'first': ('1. oseba', '1ª persona'),
        'second': ('2. oseba', '2ª persona'), 'third': ('3. oseba', '3ª persona')},
    'vform': {'infinitive': ('nedoločnik', 'infinito'),
        'supine': ('namenilnik', 'supino'), 'participle': ('deležnik', 'participio'),
        'present': ('sedanjik', 'presente'), 'future': ('prihodnjik', 'futuro'),
        'imperative': ('velelnik', 'imperativo'), 'conditional': ('pogojnik', 'condizionale')},
    'negative': {'yes': ('nikalna oblika', 'forma negativa'),
        'no': ('nenikalna oblika', 'forma non negativa')},
    'clitic': {'yes': ('naslonska oblika', 'forma clitica'),
        'no': ('nenaslonska oblika', 'forma non clitica')},
    'aspect': {'perfective': ('dovršni', 'perfettivo'),
        'progressive': ('nedovršni', 'imperfettivo'), 'biaspectual': ('dvovidski', 'biaspettuale')},
    'type': {'proper': ('lastno ime', 'nome proprio'),
        'common': ('občno ime', 'nome comune'), 'general': ('splošni', 'generale'),
        'personal': ('osebni', 'personale'), 'demonstrative': ('kazalni', 'dimostrativo'),
        'possessive': ('svojilni', 'possessivo'),
        'indefinite': ('nedoločni', 'indefinito'), 'cardinal': ('glavni', 'cardinale'),
        'ordinal': ('vrstilni', 'ordinale'), 'main': ('polnopomenski', 'lessicale'),
        'participle': ('deležniški', 'participiale'),
        'auxiliary': ('pomožni', 'ausiliare')},
    'form': {'letter': ('besedni zapis', 'in lettere'), 'digit': ('številski zapis', 'in cifre')},
}
FEATURES['definite'] = FEATURES['definiteness']
ORDER = ('vform', 'case', 'number', 'person', 'gender', 'degree',
         'definiteness', 'definite', 'animate', 'animacy', 'negative',
         'clitic', 'owner_number', 'aspect', 'type', 'form')


def source_value(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)


def pos_label(value, locale):
    pair = POS.get(value)
    return pair[locale == 'it'] if pair else ('Fonte: ' if locale == 'it' else 'Vir: ') + source_value(value)


def feature_labels(features, locale):
    """Label by key AND value; an unfamiliar source feature stays visible."""
    result = []
    keys = [key for key in ORDER if key in features]
    keys += sorted(set(features) - set(keys))
    for key in keys:
        value = features[key]
        pair = FEATURES.get(key, {}).get(value) if isinstance(value, str) else None
        result.append(pair[locale == 'it'] if pair else
            ('Fonte: ' if locale == 'it' else 'Vir: ') + key + '=' + source_value(value))
    return result


def feature_text(features, locale):
    return ' · '.join(feature_labels(features, locale))
