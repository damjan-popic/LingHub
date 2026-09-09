"""Opt-in CJVT resource candidate. The operational app.py and analyzer stay unchanged.

This separate entry point serves the two legacy dictionary GET contracts and
versioned provenance/lexical-candidate routes. It does not replace contextual NLP.
"""
from contextlib import asynccontextmanager, ExitStack
from dataclasses import asdict
import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

EXPECTED_MORPHOLOGY = 'fa2fe5c58891375b9953ed868812d3489192602aab97fd2c2a288f9fc0c8db0b'


def configured_path(name):
    value = os.environ.get(name, '')
    path = Path(value)
    if not value or not path.is_absolute() or not path.is_dir():
        raise RuntimeError('Explicit existing candidate directory required: ' + name)
    return path

def file_identity(path):
    item = path.stat()
    return (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)


@asynccontextmanager
async def lifespan(app):
    from core.synonym_index import SynonymIndex
    from core.compiled_collocations import CompiledCollocations
    # Pure, read-only service/schema imports from the existing canonical KLIKai
    # source path supplied explicitly in PYTHONPATH, never its DB/settings module.
    from klikai_api.services.morphology import MorphologyService

    synonym_root = configured_path('CJVT_SYNONYMS_RELEASE')
    collocation_root = configured_path('CJVT_COLLOCATIONS_RELEASE')
    morphology_root = configured_path('CJVT_MORPHOLOGY_RELEASE')
    cleanup = ExitStack()
    try:
        synonyms = SynonymIndex(synonym_root)
        close = getattr(synonyms, 'close', None)
        if close:
            cleanup.callback(close)
        collocations = CompiledCollocations(collocation_root)
        close = getattr(collocations, 'close', None)
        if close:
            cleanup.callback(close)
        morphology = MorphologyService(morphology_root, required=True)
        cleanup.callback(morphology.close)
        release = morphology.load_release()
        if release is None or release.release_fingerprint != EXPECTED_MORPHOLOGY:
            raise RuntimeError('Candidate morphology differs from the validated operational release')
        app.state.synonyms, app.state.collocations, app.state.morphology = synonyms, collocations, morphology
        app.state.resource_readiness = {'thesaurus': synonyms.readiness(), 'collocations': collocations.readiness(),
                                        'morphology': morphology.health()}
        resource_evidence = ('release-manifest.json', 'qa-summary.json', 'THIRD_PARTY_NOTICES.md', 'checksums.sha256')
        observed_paths = [synonym_root / name for name in (*resource_evidence, 'synonyms.sqlite')]
        observed_paths += [collocation_root / name for name in (*resource_evidence, 'collocations.sqlite')]
        observed_paths += [morphology_root / name for name in ('release-manifest.json','sl/lexicon.sqlite','it/lexicon.sqlite')]
        app.state.resource_files = {path: file_identity(path) for path in observed_paths}
        yield
    finally:
        # Also unwind partial startup and cancellation; ExitStack attempts every
        # registered closer even if an earlier closer itself raises.
        cleanup.close()


def require_resources_unchanged(request: Request):
    try:
        unchanged = all(file_identity(path) == identity for path, identity in request.app.state.resource_files.items())
    except OSError:
        unchanged = False
    if not unchanged:
        raise HTTPException(503, 'Candidate resource files changed or became unavailable; restart validation is required.')


app = FastAPI(title='LingHub CJVT resource candidate', version='0.1.0', lifespan=lifespan,
              dependencies=[Depends(require_resources_unchanged)])


@app.get('/health')
@app.get('/v1/resources/health')
def health(request: Request):
    return {'status': 'ok', 'service': 'LingHub-CJVT-candidate', 'operational_activation': False,
            'resources': request.app.state.resource_readiness,
            'contextual_nlp': {'status': 'NOT_ACTIVATED', 'required_for_dictionary_lookup': False},
            'classla_adapter': 'NOT_STARTED'}


def clean_lemma(value):
    value = value.strip()
    if not value:
        raise HTTPException(400, 'Empty lemma.')
    return value


@app.get('/thesaurus/synonyms')
def legacy_synonyms(request: Request, lemma: str = Query(..., max_length=256)):
    lemma = clean_lemma(lemma)
    senses = request.app.state.synonyms.get_senses_for_lemma(lemma)
    if not senses:
        raise HTTPException(404, 'Lemma not found in thesaurus.')
    return {'lemma': lemma, 'senses': [{'sense_id': s['sense_id'],
            **{key: sorted(set(s[key])) for key in ('core', 'near', 'antonyms')}} for s in senses]}


@app.get('/v1/resources/thesaurus')
def thesaurus_evidence(request: Request, lemma: str = Query(..., max_length=256)):
    return request.app.state.synonyms.lookup_evidence(clean_lemma(lemma))


def collocation_lookup(request, lemma, **filters):
    return request.app.state.collocations.get_collocations_for_lemma(clean_lemma(lemma), **filters)


@app.get('/collocations')
@app.get('/collocations/by-lemma')
def legacy_collocations(request: Request, lemma: str = Query(..., max_length=256),
                        min_freq: int = Query(1, ge=1), min_logdice: float | None = Query(None, allow_inf_nan=False),
                        structure_id: int | None = None, status: str | None = Query(None, max_length=64),
                        limit: int = Query(50, ge=1, le=500), sort: Literal['freq','logdice'] = 'freq',
                        order: Literal['desc','asc'] = 'desc'):
    result = collocation_lookup(request, lemma, min_freq=min_freq, min_logdice=min_logdice,
                                structure_id=structure_id, status=status, limit=limit, sort=sort, order=order)
    if not result['count_total']:
        raise HTTPException(404, 'Lemma not found in collocations.')
    return result


@app.get('/v1/resources/collocations')
def collocation_evidence(request: Request, lemma: str = Query(..., max_length=256),
                         min_freq: int | None = Query(None, ge=1), min_logdice: float | None = Query(None, allow_inf_nan=False),
                         structure_id: int | None = None, status: str | None = Query(None, max_length=64),
                         limit: int = Query(20, ge=1, le=500), sort: Literal['freq','logdice'] = 'freq',
                         order: Literal['desc','asc'] = 'desc'):
    return collocation_lookup(request, lemma, min_freq=min_freq, min_logdice=min_logdice,
                               structure_id=structure_id, status=status, limit=limit, sort=sort, order=order)


class InflectedLookup(BaseModel):
    token: str = Field(..., min_length=1, max_length=128)
    language: Literal['sl', 'it'] = 'sl'
    limit_per_lemma: int = Field(5, ge=1, le=20)


@app.post('/v1/resources/lookup-inflected')
def inflected_lookup(payload: InflectedLookup, request: Request):
    token = payload.token.strip()
    if not token or any(char.isspace() for char in token):
        raise HTTPException(400, 'Supply one inflected token.')
    candidates = request.app.state.morphology.lemmatize_token(payload.language, token)
    results = []
    for candidate in candidates:
        item = {'morphology': asdict(candidate)}
        if payload.language == 'sl':
            item['thesaurus'] = request.app.state.synonyms.lookup_evidence(candidate.lemma_normalized, normalized=True)
            item['collocations'] = request.app.state.collocations.get_collocations_for_lemma(
                candidate.lemma_normalized, min_freq=None, limit=payload.limit_per_lemma)
        else:
            item['dictionary_resources'] = 'Slovene dictionaries are not applied to Italian lemmas'
        results.append(item)
    return {'token': token, 'language': payload.language, 'analysis_kind': 'lexical_candidates',
            'contextual_analysis': False, 'morphology_release_fingerprint': EXPECTED_MORPHOLOGY,
            'lemma_candidate_limit': 4, 'candidates': results}
