# routers/nlp.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Any

from core.clients import analyze_text, loris_check

router = APIRouter()


class AnalyzeRequest(BaseModel):
    lang: str
    text: str


class LemmaToken(BaseModel):
    text: str
    lemma: str


class LemmasResponse(BaseModel):
    tokens: List[LemmaToken]


@router.post("/analyze")
async def nlp_analyze(req: AnalyzeRequest):
    """
    Proxy to my_lemmatizer /analyze.
    Returns full token info: text, lemma, pos, start, end, ...
    """
    try:
        tokens = await analyze_text(req.lang, req.text)
        return tokens
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"lemmatizer error: {repr(e)}")


@router.post("/loris-check")
async def nlp_loris_check(req: AnalyzeRequest):
    """
    Proxy to my_lemmatizer /loris/check.
    Returns tokens + issues for Loris.
    """
    try:
        result = await loris_check(req.lang, req.text)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"lemmatizer error: {repr(e)}")


@router.post("/lemmas", response_model=LemmasResponse)
async def nlp_lemmas(req: AnalyzeRequest):
    """
    Return only surface forms + lemmas (in order) for the given text.
    Internally calls my_lemmatizer /analyze and strips other fields.
    """
    try:
        tokens = await analyze_text(req.lang, req.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"lemmatizer error: {repr(e)}")

    out_tokens = []
    for t in tokens:
        text = t.get("text", "")
        lemma = t.get("lemma", "")
        out_tokens.append({"text": text, "lemma": lemma})

    return {"tokens": out_tokens}

