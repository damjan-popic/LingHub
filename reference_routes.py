"""Additive source workbench API; accepted deterministic tools keep their routes."""
import asyncio
import json
import os
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

router=APIRouter()


class ReferenceRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    question:str=Field(min_length=1,max_length=5000)
    sources:list[Literal['jezikovna_svetovalnica','epravopis']]=Field(default_factory=lambda:['jezikovna_svetovalnica','epravopis'],min_length=1,max_length=2)
    retrieval:Literal['lexical','hybrid','hybrid_rerank']='hybrid_rerank'
    model:Literal['primary','challenger']='primary'
    answer_language:Literal['sl','it']='sl'
    limit:int=Field(default=8,ge=1,le=10)

    @model_validator(mode='after')
    def distinct_sources(self):
        if not self.question.strip() or len(set(self.sources))!=len(self.sources):
            raise ValueError('Supply a nonempty question and distinct sources')
        return self


async def forward(route,payload=None,params=None,klikai=False):
    origin=os.environ.get('KLIKAI_REFERENCE_URL','http://127.0.0.1:8000') if klikai else os.environ.get('ZRC_REFERENCE_URL','http://127.0.0.1:8020')
    try:
        u=urlsplit(origin)
        if (u.scheme!='http' or u.hostname!='127.0.0.1' or not u.port or u.path or u.query or u.fragment or u.username or u.password):
            raise HTTPException(503,'Reference origin is not configured as loopback')
        async with asyncio.timeout(120),httpx.AsyncClient(timeout=httpx.Timeout(118,connect=2),trust_env=False,follow_redirects=False) as client:
            path=('/api/references/' if klikai else '/v1/references/')+route
            async with client.stream('POST' if payload is not None else 'GET',origin+path,json=payload,params=params) as response:
                if response.status_code!=200:raise HTTPException(response.status_code,'Reference service could not complete this operation')
                parts=[];size=0
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>2*1024*1024:raise ValueError('Reference response exceeds bound')
                    parts.append(chunk)
        result=json.loads(b''.join(parts))
        if not isinstance(result,dict) or (route!='passage' and not isinstance(result.get('status'),str)):
            raise ValueError('Invalid reference response')
        return result
    except (httpx.HTTPError,TimeoutError,ValueError):
        raise HTTPException(503,'Reference service is unavailable; other language tools remain available.') from None


@router.get('/v1/references/health')
async def health():
    return await forward('health')


@router.post('/v1/references/search')
async def search(payload:ReferenceRequest):
    return await forward('search',payload.model_dump())


@router.post('/v1/references/answer')
async def answer(payload:ReferenceRequest):
    result=await forward('answer',payload.model_dump(),klikai=True)
    result['via']='LingHub → KLIKai → local reference gateway'
    return result


@router.get('/v1/references/passage')
async def passage(id:str=Query(min_length=1,max_length=512)):
    return await forward('passage',params={'id':id})
