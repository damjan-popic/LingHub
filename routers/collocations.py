from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Literal, Any, Dict, List, Set

from core.collocations import get_collocations_for_lemma
from core.clients import analyze_text

router = APIRouter()


# ---------- Helpers ----------

def _norm(s: str) -> str:
    # normalize whitespace + robust lowercase for user input
    return " ".join(s.strip().split()).casefold()


def _is_content_pos(pos: str, allowed: Set[str]) -> bool:
    return pos in allowed


def _extract_content_lemmas(tokens: List[Dict[str, Any]], allowed_pos: Set[str]) -> List[str]:
    """
    Extract lemmas from tokens, preserving order, deduping by normalized lemma.
    """
    seen: Set[str] = set()
    out: List[str] = []

    for t in tokens:
        pos = (t.get("pos") or "").strip()
        lemma = (t.get("lemma") or "").strip()
        if not lemma:
            continue
        if not _is_content_pos(pos, allowed_pos):
            continue

        key = _norm(lemma)
        if key in seen:
            continue

        seen.add(key)
        out.append(key)

    return out


def _surface_from_tokens(tokens: List[Dict[str, Any]]) -> str:
    """
    Build a normalized surface string from token texts.
    For phrases, we keep it simple: join token texts with single spaces.
    """
    parts = []
    for t in tokens:
        txt = (t.get("text") or "").strip()
        if not txt:
            continue
        parts.append(txt)
    return _norm(" ".join(parts))


# ---------- Request models ----------

class TextRequest(BaseModel):
    lang: str = Field(..., description="Language code, e.g. 'sl' or 'it'")
    text: str = Field(..., description="Input text")


class FromTextRequest(TextRequest):
    # keep safe defaults; still fully configurable
    max_lemmas: int = Field(5, ge=1, le=50, description="Max number of distinct content lemmas to query")
    limit_per_lemma: int = Field(20, ge=1, le=500, description="Max collocations returned per lemma")
    min_freq: int = Field(1, ge=1, description="Minimum frequency")
    min_logdice: Optional[float] = Field(None, description="Minimum logDice (if available)")
    sort: Literal["freq", "logdice"] = "freq"
    order: Literal["desc", "asc"] = "desc"
    status: Optional[str] = Field(None, description="Filter by status (e.g. 'automatic')")

    # which token POS we consider “content”
    allowed_pos: List[str] = Field(
        default_factory=lambda: ["NOUN", "VERB", "ADJ", "PROPN"],
        description="Which POS tags count as 'content' for lemma extraction",
    )


class PhraseRequest(TextRequest):
    # How strict are we?
    headword: Optional[str] = Field(
        None,
        description="Optional: force headword lemma to check collocations against (case-insensitive). "
                    "If omitted, we try extracted content lemmas from the phrase.",
    )

    # Result tuning
    limit: int = Field(50, ge=1, le=500, description="How many top collocations to consider per lemma")
    min_freq: int = Field(1, ge=1, description="Minimum frequency for evidence list")
    min_logdice: Optional[float] = Field(None, description="Minimum logDice (if available)")
    sort: Literal["freq", "logdice"] = "freq"
    order: Literal["desc", "asc"] = "desc"
    status: Optional[str] = Field(None, description="Filter by status (e.g. 'automatic')")

    allowed_pos: List[str] = Field(
        default_factory=lambda: ["NOUN", "VERB", "ADJ", "PROPN"],
        description="Which POS tags count as 'content' for lemma extraction",
    )


# ---------- Existing endpoint (lemma lookup) ----------

@router.get("/collocations")
async def collocations_by_lemma(
    lemma: str = Query(..., description="Headword lemma (case-insensitive)"),
    min_freq: int = Query(1, ge=1, description="Minimum frequency"),
    min_logdice: Optional[float] = Query(None, description="Minimum logDice (if available)"),
    structure_id: Optional[int] = Query(None, description="Filter by structure_id"),
    status: Optional[str] = Query(None, description="Filter by status (e.g. 'automatic')"),
    limit: int = Query(50, ge=1, le=500, description="Max number of results"),
    sort: Literal["freq", "logdice"] = Query("freq", description="Sort by freq or logdice"),
    order: Literal["desc", "asc"] = Query("desc", description="Sort order"),
) -> Dict[str, Any]:
    lemma = lemma.strip()
    if not lemma:
        raise HTTPException(status_code=400, detail="Empty lemma.")

    result = get_collocations_for_lemma(
        lemma,
        min_freq=min_freq,
        min_logdice=min_logdice,
        structure_id=structure_id,
        status=status,
        limit=limit,
        sort=sort,
        order=order,
    )

    if result["count_total"] == 0:
        raise HTTPException(status_code=404, detail="Lemma not found in collocations.")

    return result


# ---------- New: collocations from text (exploratory, not full-text validation) ----------

@router.post("/collocations/from-text")
async def collocations_from_text(req: FromTextRequest) -> Dict[str, Any]:
    lang = req.lang.strip().lower()
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty.")

    # 1) lemmatize once
    try:
        tokens = await analyze_text(lang, text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"lemmatizer error: {repr(e)}")

    allowed_pos = set(req.allowed_pos)
    lemmas = _extract_content_lemmas(tokens, allowed_pos)[: req.max_lemmas]

    # 2) lookup collocations per lemma
    results: Dict[str, Any] = {}
    for lk in lemmas:
        results[lk] = get_collocations_for_lemma(
            lk,
            min_freq=req.min_freq,
            min_logdice=req.min_logdice,
            structure_id=None,
            status=req.status,
            limit=req.limit_per_lemma,
            sort=req.sort,
            order=req.order,
        )

    return {
        "input_text": text,
        "lang": lang,
        "lemmas_used": lemmas,
        "collocations": results,
        "note": "Exploratory helper endpoint. Not a full-text validator.",
    }


# ---------- New: phrase evidence check (chatbot-friendly) ----------

@router.post("/collocations/phrase")
async def collocations_phrase(req: PhraseRequest) -> Dict[str, Any]:
    lang = req.lang.strip().lower()
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty.")

    # 1) lemmatize phrase
    try:
        tokens = await analyze_text(lang, text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"lemmatizer error: {repr(e)}")

    allowed_pos = set(req.allowed_pos)
    phrase_surface = _surface_from_tokens(tokens)

    # Candidate lemmas from the phrase
    extracted = _extract_content_lemmas(tokens, allowed_pos)

    # If user forces a headword, use only that
    if req.headword and req.headword.strip():
        headwords = [_norm(req.headword)]
    else:
        # otherwise: use extracted content lemmas as possible headwords (in order)
        headwords = extracted

    if not headwords:
        # fallback: if nothing content-like, try using all lemmas
        all_lemmas = []
        seen = set()
        for t in tokens:
            lemma = (t.get("lemma") or "").strip()
            if not lemma:
                continue
            k = _norm(lemma)
            if k in seen:
                continue
            seen.add(k)
            all_lemmas.append(k)
        headwords = all_lemmas[:1]  # keep it cheap

    matches: List[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {}

    # 2) For each candidate headword: retrieve top collocations and check if phrase matches any
    for hw in headwords:
        data = get_collocations_for_lemma(
            hw,
            min_freq=req.min_freq,
            min_logdice=req.min_logdice,
            structure_id=None,
            status=req.status,
            limit=req.limit,
            sort=req.sort,
            order=req.order,
        )

        collocs = data.get("collocations", [])
        evidence[hw] = {
            "lemma": data.get("lemma"),
            "count_total": data.get("count_total"),
            "count_returned": data.get("count_returned"),
            "top": collocs,  # full structured evidence (frontend/chatbot can pick what it wants)
        }

        # Build match list + rank
        for i, c in enumerate(collocs, start=1):
            surf = _norm(c.get("surface", ""))
            if surf and surf == phrase_surface:
                matches.append(
                    {
                        "headword": hw,
                        "rank": i,
                        "frequency": c.get("frequency"),
                        "logDice": c.get("logDice"),
                        "structure_id": c.get("structure_id"),
                        "lexical_unit_id": c.get("lexical_unit_id"),
                        "surface": c.get("surface"),
                    }
                )

    return {
        "input_text": text,
        "lang": lang,
        "phrase_surface_norm": phrase_surface,
        "lemmas_extracted": extracted,
        "headwords_checked": headwords,
        "matches": matches,
        "evidence": evidence,
        "interpretation_hint": (
            "If matches is non-empty, the phrase exists among top collocations for at least one headword lemma. "
            "If empty, it may still be valid; use evidence lists to suggest more typical alternatives."
        ),
    }
