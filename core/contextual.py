"""Bounded local NLP proxy and compiled collocation composition.

This module deliberately never imports the legacy in-memory XML loaders.
The NLP payload is kept in original-text coordinates; CJVT composition is
Slovene-only. Dictionary-only requests do not depend on the NLP service.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

router = APIRouter()
MAX_TEXT = 10000
MAX_RESPONSE = 8 * 1024 * 1024
MAX_TOKENS = 4096
MAX_LOOKUPS = 20
MAX_RETURNED = 2000


def configured_lemmatizer_url():
    value = os.environ.get('MY_LEMMATIZER_URL', '').rstrip('/')
    if not value:
        return None
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password
            or url.path or url.query or url.fragment or not url.port):
        raise RuntimeError('MY_LEMMATIZER_URL must be an explicit loopback HTTP origin with port')
    return value


async def upstream(request, route, payload=None):
    base = getattr(request.app.state, 'lemmatizer_url', None)
    if not base:
        raise HTTPException(503, 'Local lemmatizer is not configured; dictionary routes remain available.')
    try:
        async with asyncio.timeout(10.0), httpx.AsyncClient(base_url=base, timeout=10.0, trust_env=False,
                                     follow_redirects=False) as client:
            async with client.stream('POST' if payload is not None else 'GET', route, json=payload) as response:
                response.raise_for_status()
                chunks, length = [], 0
                async for chunk in response.aiter_bytes():
                    length += len(chunk)
                    if length > MAX_RESPONSE:
                        raise ValueError('Response exceeds bound')
                    chunks.append(chunk)
                return json.loads(b''.join(chunks))
    except (httpx.HTTPError, ValueError, TypeError, TimeoutError):
        raise HTTPException(502, 'Local lemmatizer request failed or returned invalid data.') from None


async def upstream_health(request):
    result = await upstream(request, '/health')
    if not isinstance(result, dict) or result.get('status') != 'ok' or result.get('service') != 'my_lemmatizer':
        raise HTTPException(502, 'Local lemmatizer health identity is invalid.')
    return result


class TextRequest(BaseModel):
    lang: Literal['sl', 'it']
    text: str = Field(..., min_length=1, max_length=MAX_TEXT)


def validate_analysis(data, text, route):
    """Reject upstream schema/coordinate drift before advertising a proxy result."""
    tokens = data if route == '/analyze' else data.get('tokens') if isinstance(data, dict) else None
    if not isinstance(tokens, list) or len(tokens) > MAX_TOKENS:
        raise ValueError('Invalid tokens')
    previous = 0
    for token in tokens:
        if not isinstance(token, dict) or not all(isinstance(token.get(k), str) for k in ('text', 'lemma', 'pos')):
            raise ValueError('Invalid token schema')
        start, end = token.get('start'), token.get('end')
        if type(start) is not int or type(end) is not int or not previous <= start < end <= len(text):
            raise ValueError('Invalid token coordinates')
        if text[start:end] != token['text']:
            raise ValueError('Token is not an original-text slice')
        previous = end
    if route != '/analyze':
        fields = ('spans', 'issues') if route == '/loris/check' else ('spans',)
        for field in fields:
            records = data.get(field)
            if not isinstance(records, list) or len(records) > MAX_TOKENS * 4:
                raise ValueError('Invalid span/issue list')
            for span in records:
                if not isinstance(span, dict):
                    raise ValueError('Invalid span')
                start, end = span.get('start'), span.get('end')
                if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                    raise ValueError('Invalid span coordinates')
                if field == 'spans':
                    first, last = span.get('token_start'), span.get('token_end')
                    if type(first) is not int or type(last) is not int or not 0 <= first < last <= len(tokens):
                        raise ValueError('Invalid span token coordinates')
                    if (span.get('surface') != text[start:end] or tokens[first]['start'] != start
                            or tokens[last-1]['end'] != end or span.get('token_indexes') != list(range(first, last))):
                        raise ValueError('Span boundary mismatch')
    return data


async def analysis(req, request, route='/analyze'):
    if not req.text.strip():
        raise HTTPException(400, 'Text is empty.')
    data = await upstream(request, route, {'lang': req.lang, 'text': req.text})
    try:
        return validate_analysis(data, req.text, route)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(502, 'Local lemmatizer response schema or original-text offsets are invalid.') from None


@router.post('/nlp/analyze')
async def analyze(req: TextRequest, request: Request):
    return await analysis(req, request)


@router.post('/nlp/analyze-full')
async def analyze_full(req: TextRequest, request: Request):
    return await analysis(req, request, '/analyze-full')


@router.post('/nlp/loris-check')
async def loris(req: TextRequest, request: Request):
    return await analysis(req, request, '/loris/check')


@router.post('/nlp/lemmas')
async def lemmas(req: TextRequest, request: Request):
    tokens = await analysis(req, request)
    return {'tokens': [{'text': t['text'], 'lemma': t['lemma']} for t in tokens],
            'lemmas': [t['lemma'] for t in tokens]}


class CompositionRequest(TextRequest):
    lang: Literal['sl']
    min_freq: int | None = Field(1, ge=1)
    min_logdice: float | None = Field(None, allow_inf_nan=False)
    sort: Literal['freq', 'logdice'] = 'freq'
    order: Literal['desc', 'asc'] = 'desc'
    status: str | None = Field(None, max_length=64)
    allowed_pos: list[Literal['NOUN', 'VERB', 'ADJ', 'PROPN', 'ADV', 'AUX', 'ADP', 'PRON', 'NUM', 'DET', 'CCONJ', 'SCONJ', 'PART', 'INTJ', 'X']] = Field(
        default_factory=lambda: ['NOUN', 'VERB', 'ADJ', 'PROPN'], max_length=17)


class FromTextRequest(CompositionRequest):
    max_lemmas: int = Field(5, ge=1, le=MAX_LOOKUPS)
    limit_per_lemma: int = Field(20, ge=1, le=500)


class PhraseRequest(CompositionRequest):
    headword: str | None = Field(None, max_length=256)
    limit: int = Field(50, ge=1, le=500)
    max_lemmas: int = Field(5, ge=1, le=MAX_LOOKUPS)


def norm(text):
    return ' '.join(text.strip().split()).casefold()


def content_lemmas(tokens, allowed):
    return list(dict.fromkeys(norm(t['lemma']) for t in tokens if t['pos'] in allowed and t['lemma'].strip()))


def lexical_sequence(tokens):
    return [norm(t['lemma']) for t in tokens if t['pos'] not in {'PUNCT', 'SPACE', 'SYM'} and t['lemma'].strip()]


async def lookups(request, req, headwords, limit):
    if len(headwords) * limit > MAX_RETURNED:
        raise HTTPException(400, 'Composition is bounded to 2000 returned records; lower lemmas or per-lemma limit.')
    result = {}
    for headword in headwords:
        result[headword] = await run_in_threadpool(request.app.state.collocations.get_collocations_for_lemma,
            headword, min_freq=req.min_freq, min_logdice=req.min_logdice, status=req.status,
            limit=limit, sort=req.sort, order=req.order)
    return result


@router.post('/collocations/from-text')
async def from_text(req: FromTextRequest, request: Request):
    tokens = await analysis(req, request)
    extracted = content_lemmas(tokens, req.allowed_pos)
    used = extracted[:req.max_lemmas]
    return {'input_text': req.text, 'lang': req.lang, 'tokens': tokens, 'lemmas_used': used,
            'lemmas_truncated': len(extracted) > len(used),
            'collocations': await lookups(request, req, used, req.limit_per_lemma),
            'note': 'Exploratory lemma lookup; not a full-text validator or synonym expansion.'}


@router.post('/collocations/phrase')
async def phrase(req: PhraseRequest, request: Request):
    tokens = await analysis(req, request)
    extracted = content_lemmas(tokens, req.allowed_pos)
    sequence = lexical_sequence(tokens)
    surface = norm(' '.join(t['text'] for t in tokens))
    available = [norm(req.headword)] if req.headword and req.headword.strip() else (extracted or sequence[:1])
    headwords = available[:req.max_lemmas]
    results = await lookups(request, req, headwords, req.limit)
    matches, evidence = [], {}
    for headword, data in results.items():
        evidence[headword] = {'lemma': data['lemma'], 'count_total': data['count_total'],
            'count_returned': data['count_returned'], 'top': data['collocations'], 'resource': data['resource']}
        for rank, record in enumerate(data['collocations'], 1):
            components = record['components']
            component_lemmas = [norm(c['lemma']) for c in components if c.get('lemma')]
            matched_by = []
            if surface and surface in {norm(record.get('surface') or ''), norm(' '.join(c.get('text') or '' for c in components))}:
                matched_by.append('surface')
            if sequence and component_lemmas == sequence:
                matched_by.append('lemma_sequence')
            if matched_by:
                # Preserve distinct source occurrences and full source provenance.
                matches.append({**record, 'headword': headword, 'rank': rank, 'matched_by': matched_by,
                    'match_type': 'surface_and_lemma_sequence' if len(matched_by) == 2 else matched_by[0],
                    'component_lemmas': component_lemmas, 'surface_norm': norm(record.get('surface') or '')})
    return {'input_text': req.text, 'lang': req.lang, 'tokens': tokens, 'phrase_surface_norm': surface,
        'phrase_lemma_sequence': sequence, 'phrase_lemma_norm': ' '.join(sequence),
        'lemmas_extracted': extracted, 'headwords_checked': headwords,
        'headwords_truncated': len(available) > len(headwords), 'matches': matches, 'evidence': evidence,
        'interpretation_hint': 'Matches are limited to returned top collocations, using source component order. Empty results do not establish that a phrase is invalid.'}
