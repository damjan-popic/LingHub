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
from core.contextual import router as contextual_router, configured_lemmatizer_url, upstream_health
from core.platform_api import router as platform_router, PARTIAL_PATHS, public, bounded_thesaurus
from core.request_limits import RequestBodyLimit
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

EXPECTED_MORPHOLOGY = 'fa2fe5c58891375b9953ed868812d3489192602aab97fd2c2a288f9fc0c8db0b'
EXPECTED_SYNONYMS = 'de2195bde050500bce48de068f968395d5d68d75634942ef7f33dc692d007849'
EXPECTED_COLLOCATIONS = '25fd5882cde3b326208050bb6bfbd3901895a83ac6633b209e081fde0bf8f63c'


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
        if synonyms.manifest['fingerprint'] != EXPECTED_SYNONYMS or collocations.manifest['build_id'] != EXPECTED_COLLOCATIONS:
            raise RuntimeError('Dictionary identities differ from the verified Part A releases')
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
        app.state.resource_files_by_kind = {
            'synonyms': {p:app.state.resource_files[p] for p in observed_paths[:5]},
            'collocations': {p:app.state.resource_files[p] for p in observed_paths[5:10]},
            'morphology': {p:app.state.resource_files[p] for p in observed_paths[10:]}}
        app.state.morphology_release = release
        app.state.lemmatizer_url = configured_lemmatizer_url()
        scope = os.environ.get('LINGHUB_DEPLOYMENT_SCOPE', 'candidate')
        if scope not in ('candidate', 'local_persistent'):
            raise RuntimeError('Unknown LingHub deployment scope')
        if scope == 'local_persistent' and app.state.lemmatizer_url is None:
            raise RuntimeError('Persistent local Core requires explicit MY_LEMMATIZER_URL')
        app.state.deployment_scope = scope
        yield
    finally:
        # Also unwind partial startup and cancellation; ExitStack attempts every
        # registered closer even if an earlier closer itself raises.
        cleanup.close()


def require_resources_unchanged(request: Request):
    if request.url.path in PARTIAL_PATHS or request.url.path.startswith(('/workbench','/workbench-api','/workbench-static')):
        return
    try:
        unchanged = all(file_identity(path) == identity for path, identity in request.app.state.resource_files.items())
    except OSError:
        unchanged = False
    if not unchanged:
        raise HTTPException(503, 'Candidate resource files changed or became unavailable; restart validation is required.')


app = FastAPI(title='LingHub local Core candidate', version='0.2.0', lifespan=lifespan,
              dependencies=[Depends(require_resources_unchanged)])
app.include_router(contextual_router)
app.include_router(platform_router)
app.add_middleware(RequestBodyLimit,limit=131072)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
if Path(__file__).with_name('workbench_routes.py').is_file():
    from workbench_routes import router as workbench_router
    app.include_router(workbench_router)
    app.mount('/workbench-static',StaticFiles(directory=Path(__file__).with_name('workbench')),name='workbench-static')


@app.get('/v1/resources/health')
def resource_health(request: Request):
    scope = getattr(request.app.state, 'deployment_scope', 'candidate')
    return {'status': 'ok', 'service': 'LingHub-CJVT-candidate', 'operational_activation': scope == 'local_persistent',
            'deployment_scope': scope,
            'resources': public(request.app.state.resource_readiness),
            'contextual_nlp': {'status': 'NOT_ACTIVATED', 'required_for_dictionary_lookup': False},
            'classla_adapter': 'NOT_STARTED',
            'versioned_analyzer_capabilities': '/v1/capabilities',
            'legacy_analyzer_scope': 'Legacy contextual routes remain spaCy; selectable engine readiness is versioned separately.'}


@app.get('/health')
async def health(request: Request):
    result = resource_health(request)
    if getattr(request.app.state, 'lemmatizer_url', None):
        await upstream_health(request)
        result.update(service='LingHub-Core-local', contextual_nlp={'status': 'READY_LOCAL',
            'backend': 'my_lemmatizer', 'production_parity': 'PENDING', 'required_for_dictionary_lookup': False})
    return result


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
def thesaurus_evidence(request: Request, lemma: str = Query(..., max_length=256), limit: int | None = Query(None,ge=1,le=20)):
    if limit is not None:
        return bounded_thesaurus(request.app.state.synonyms,clean_lemma(lemma),limit,normalized=False)
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
                         order: Literal['desc','asc'] = 'desc',
                         kind: Literal['collocation','grammaticalCombination','all'] = 'collocation'):
    return collocation_lookup(request, lemma, min_freq=min_freq, min_logdice=min_logdice,
                               structure_id=structure_id, status=status, limit=limit, sort=sort, order=order, kind=kind)


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
