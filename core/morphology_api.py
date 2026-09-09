"""Bounded general contracts over the accepted immutable morphology projection."""
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

DEPENDENCIES = Path(__file__).resolve().parents[1] / '_dependencies'
LOCK = json.loads((DEPENDENCIES / 'morphology-code-lock.json').read_text())
for name, expected in LOCK['files_sha256'].items():
    if hashlib.sha256((DEPENDENCIES / name).read_bytes()).hexdigest() != expected:
        raise RuntimeError('Vendored morphology code identity is invalid')
sys.path.insert(0, str(DEPENDENCIES / 'apps/api'))
from klikai_api.services.morphology import normalize_text, fold_diacritics, tokenize


def provenance(service, lang):
    release = service.load_release()
    source = release.manifest['languages'][lang]['source']
    return {'resource_id': source['name'], 'version': source['version'], 'source_identifier': source['identifier'],
        'source_url': source['url'], 'license': source['license'], 'fingerprint': release.release_fingerprint,
        'normalization': release.normalization_version, 'analysis_kind': 'lexical_candidates', 'contextual': False}


def lemmatize(service, lang, tokens, limit=4):
    keys = list(dict.fromkeys(normalize_text(t) for t in tokens))
    groups = {}
    with service._connection(lang) as db:
        # One exact and, only when needed, one folded scalar batch per language.
        exact = defaultdict(list)
        placeholders = ','.join('?' for _ in keys)
        for row in db.execute('SELECT form_normalized,lemma,lemma_normalized,msd,pos_family FROM lexicon WHERE form_normalized IN ('+placeholders+') ORDER BY lemma_normalized,lemma,msd,pos_family', keys):
            exact[row['form_normalized']].append(row)
        missing = [key for key in keys if not exact[key]]
        folded = defaultdict(list)
        if missing:
            folded_keys = list(dict.fromkeys(fold_diacritics(key) for key in missing))
            for row in db.execute('SELECT form_folded,lemma,lemma_normalized,msd,pos_family FROM lexicon WHERE form_folded IN ('+','.join('?' for _ in folded_keys)+') ORDER BY lemma_normalized,lemma,msd,pos_family', folded_keys):
                folded[row['form_folded']].append(row)
        for key in keys:
            matched_by = 'exact' if exact[key] else 'folded'
            rows = exact[key] or folded[fold_diacritics(key)]
            candidates = {}
            for row in rows:
                item = candidates.setdefault(row['lemma_normalized'], {'language': lang, 'lemma': row['lemma'],
                    'lemma_normalized': row['lemma_normalized'], 'msds': set(), 'pos_families': set(), 'matched_by': matched_by})
                if row['msd']: item['msds'].add(row['msd'])
                if row['pos_family']: item['pos_families'].add(row['pos_family'])
            all_candidates = [{**item, 'msds': sorted(item['msds']), 'pos_families': sorted(item['pos_families'])} for item in candidates.values()]
            groups[key] = {'normalized': key, 'status': 'ok' if all_candidates else 'no_hit',
                'candidates': all_candidates[:limit], 'candidate_count': len(all_candidates),
                'ambiguous': len(all_candidates)>1, 'truncated': len(all_candidates)>limit}
    items = [{'input': token, **groups[normalize_text(token)]} for token in tokens]
    return {'status': 'ok' if any(i['candidates'] for i in items) else 'no_hit', 'lang': lang,
        'items': items, 'truncated': any(i['truncated'] for i in items), 'unique_queries': len(keys),
        'provenance': provenance(service, lang)}


def inflect(service, lang, lemma, limit=40, msd=None):
    key = normalize_text(lemma)
    with service._connection(lang) as db:
        rows = db.execute('SELECT min(form) AS form,form_normalized FROM lexicon WHERE lemma_normalized=? AND (? IS NULL OR msd=?) GROUP BY form_normalized ORDER BY CASE WHEN form_normalized=? THEN 0 ELSE 1 END,length(form_normalized),form_normalized,form LIMIT ?',
            (key, msd, msd, key, limit+1)).fetchall()
        forms = []
        for row in rows[:limit]:
            evidence = db.execute('SELECT DISTINCT msd,pos_family FROM lexicon WHERE lemma_normalized=? AND form_normalized=? AND (? IS NULL OR msd=?) ORDER BY msd,pos_family', (key,row['form_normalized'],msd,msd)).fetchall()
            forms.append({'form':row['form'], 'normalized':row['form_normalized'],
                'msds':sorted({r['msd'] for r in evidence if r['msd']}),
                'pos_families':sorted({r['pos_family'] for r in evidence if r['pos_family']})})
    return {'status':'ok' if forms else 'no_hit','lang':lang,'lemma':lemma,'normalized':key,
        'forms':forms,'matched_by':'exact','msd_filter':msd,'truncated':len(rows)>limit,
        'contextually_selected':False,'provenance':provenance(service,lang)}


def signatures(service, lang, text, limit=32):
    items = service.phrase_signatures(lang, text)
    return {'status':'ok' if items else 'no_hit','lang':lang,'text':text,'normalized':normalize_text(text),
        'signatures':[asdict(item) for item in items[:limit]],
        'truncated':len(items)>limit or len(items)>=service.max_signatures_per_phrase,
        'truncation_policy':'true when requested or accepted service cap is reached; not an exhaustive ambiguity count',
        'provenance':provenance(service,lang)}
