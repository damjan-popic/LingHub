# routers/nlp.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

from core.clients import analyze_text, analyze_full_text, loris_check

router = APIRouter()


class AnalyzeRequest(BaseModel):
    lang: str
    text: str


class LemmaToken(BaseModel):
    text: str
    lemma: str


class LemmasResponse(BaseModel):
    # Backward-compatible detail view.
    tokens: List[LemmaToken]
    # README-compatible convenience view.
    lemmas: List[str]


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


@router.post("/analyze-full")
async def nlp_analyze_full(req: AnalyzeRequest):
    """
    Proxy to my_lemmatizer /analyze-full.
    Returns {tokens, spans}; spans are the first-class MWU/rule layer.
    """
    try:
        return await analyze_full_text(req.lang, req.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"lemmatizer error: {repr(e)}")


@router.post("/loris-check")
async def nlp_loris_check(req: AnalyzeRequest):
    """
    Proxy to my_lemmatizer /loris/check.
    Returns tokens + issues + span-level matches for LORIS.
    Existing clients can continue to read only tokens/issues.
    """
    try:
        result = await loris_check(req.lang, req.text)
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"lemmatizer error: {repr(e)}")


@router.post("/lemmas", response_model=LemmasResponse)
async def nlp_lemmas(req: AnalyzeRequest):
    """
    Return lemmas in two shapes:
      - tokens: [{text, lemma}, ...] for detailed clients
      - lemmas: [lemma, ...] for lightweight clients and README compatibility
    """
    try:
        tokens = await analyze_text(req.lang, req.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"lemmatizer error: {repr(e)}")

    out_tokens = []
    lemmas = []
    for t in tokens:
        text = t.get("text", "")
        lemma = t.get("lemma", "")
        out_tokens.append({"text": text, "lemma": lemma})
        lemmas.append(lemma)

    return {"tokens": out_tokens, "lemmas": lemmas}
