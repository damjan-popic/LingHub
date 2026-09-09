"""Same-origin workbench and two fixed, bounded KLIKai forwarding operations."""
from contextlib import asynccontextmanager
import asyncio
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field


@asynccontextmanager
async def lifespan(app):
    target = os.environ.get('KLIKAI_URL', 'http://127.0.0.1:8000').rstrip('/')
    parsed = urlsplit(target)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port
            or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password):
        raise RuntimeError('KLIKAI_URL requires a fixed loopback HTTP origin')
    async with httpx.AsyncClient(base_url=target, timeout=httpx.Timeout(25, connect=2),
                                 limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
                                 follow_redirects=False, trust_env=False) as client:
        app.state.workbench_klikai = client
        app.state.workbench_slots = asyncio.Semaphore(4)
        yield


router = APIRouter(lifespan=lifespan)
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


@router.get('/workbench/', include_in_schema=False)
def workbench():
    return FileResponse(Path(__file__).parent / 'workbench/index.html', headers={
        'Content-Security-Policy': CSP, 'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})


class TerminologyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    q: str = Field(min_length=1, max_length=256)
    language: Literal['sl', 'it', 'fu'] | None = None
    use_morphology: bool = True
    limit: int = Field(10, ge=1, le=20)


class Span(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: Literal['text_check', 'morphology', 'synonyms', 'collocations', 'explore']
    lang: Literal['sl', 'it'] = 'sl'
    text: str = Field(min_length=1, max_length=5000)
    engine: Literal['default', 'spacy', 'classla'] = 'default'
    selection: Span | None = None
    operation: Literal['lemmatize', 'inflect', 'signatures'] | None = None
    limit: int = Field(5, ge=1, le=20)


async def forward(request, method, route, *, params=None, payload=None):
    try:
        async with asyncio.timeout(28), request.app.state.workbench_slots:
            async with request.app.state.workbench_klikai.stream(method, route, params=params, json=payload) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 4 * 1024 * 1024:
                        raise ValueError('Bounded KLIKai response exceeded')
                    chunks.append(chunk)
                if response.status_code >= 400:
                    raise HTTPException(503 if response.status_code >= 500 else response.status_code,
                                        'KLIKai could not complete the requested operation.')
                result = json.loads(b''.join(chunks))
                if not isinstance(result, dict):
                    raise ValueError('Invalid response')
                return {'via': 'LingHub workbench → KLIKai', 'result': result}
    except (httpx.HTTPError, ValueError, TimeoutError):
        raise HTTPException(503, 'KLIKai is unavailable; direct LingHub tools remain independent.') from None


@router.post('/workbench-api/terminology')
async def terminology(payload: TerminologyRequest, request: Request):
    if not payload.q.strip():
        raise HTTPException(400, 'Supply a terminology query.')
    return await forward(request, 'GET', '/api/terminology/lookup', params=payload.model_dump(exclude_none=True))


@router.post('/workbench-api/tool')
async def linguistic_tool(payload: ToolRequest, request: Request):
    return await forward(request, 'POST', '/api/tools/linguistic', payload=payload.model_dump(exclude_none=True))
