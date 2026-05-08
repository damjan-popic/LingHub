from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, Literal, Any, Dict, List, Set

from core.collocations import get_collocations_for_lemma
from core.clients import analyze_text

router = APIRouter()


# ---------- Helpers ----------

def _norm(s: str) -> str:
    # normalize whitespace + robust lowercase for user input
    return " ".join((s or "").strip().split()).casefold()


def _is_content_pos(pos: str, allowed: Set[str]) -> bool:
    return pos in allowed


def _is_lexical_token(t: Dict[str, Any]) -> bool:
    """
    Keep phrase lemma matching focused on lexical tokens.
    Punctuation and spacing should not block a collocation match.
    """
    pos = (t.get("pos") or "").strip().upper()
    lemma = (t.get("lemma") or "").strip()
    return bool(lemma) and pos not in {"PUNCT", "SPACE", "SYM"}


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


def _all_lemma_sequence(tokens: List[Dict[str, Any]]) -> List[str]:
    """Normalized lemma sequence for the full input phrase, excluding punctuation."""
    out: List[str] = []
    for t in tokens:
        if not _is_lexical_token(t):
            continue
        lemma = _norm(t.get("lemma") or "")
        if lemma:
            out.append(lemma)
    return out


def _surface_from_tokens(tokens: List[Dict[str, Any]]) -> str:
    """
    Build a normalized surface string from token texts.
    For phrases, we keep it simple: join non-empty token texts with single spaces.
    """
    parts = []
    for t in tokens:
        txt = (t.get("text") or "").strip()
        if not txt:
            continue
        parts.append(txt)
    return _norm(" ".join(parts))


def _component_lemma_sequence(collocation: Dict[str, Any]) -> List[str]:
    """Normalized lemma sequence from XML collocation components."""
    out: List[str] = []
    for comp in collocation.get("components", []) or []:
        lemma = _norm(comp.get("lemma") or "")
        if lemma:
            out.append(lemma)
    return out


def _component_surface_norm(collocation: Dict[str, Any]) -> str:
    """Normalized surface reconstructed from XML collocation components."""
    parts: List[str] = []
    for comp in collocation.get("components", []) or []:
        text = (comp.get("text") or "").strip()
        if text:
            parts.append(text)
    return _norm(" ".join(parts))


def _match_types_for_collocation(
    *,
    phrase_surface: str,
    phrase_lemma_sequence: List[str],
    collocation: Dict[str, Any],
) -> List[str]:
    """
    Decide whether the input phrase matches a collocation by surface, by lemma
    sequence, or both. Lemma-sequence matching lets inflected user input match
    stored canonical/component lemmas.
    """
    matched_by: List[str] = []

    surface_candidates = {
        _norm(collocation.get("surface") or ""),
        _component_surface_norm(collocation),
    }
    surface_candidates.discard("")
    if phrase_surface and phrase_surface in surface_candidates:
        matched_by.append("surface")

    comp_lemmas = _component_lemma_sequence(collocation)
    if phrase_lemma_sequence and comp_lemmas and phrase_lemma_sequence == comp_lemmas:
        matched_by.append("lemma_sequence")

    return matched_by


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

@router.get("/collocations/by-lemma")
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
        "tokens": tokens,
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
    phrase_lemma_sequence = _all_lemma_sequence(tokens)
    phrase_lemma_norm = " ".join(phrase_lemma_sequence)

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
        for lemma in phrase_lemma_sequence:
            if lemma in seen:
                continue
            seen.add(lemma)
            all_lemmas.append(lemma)
        headwords = all_lemmas[:1]  # keep it cheap

    matches: List[Dict[str, Any]] = []
    evidence: Dict[str, Any] = {}
    seen_matches: Set[str] = set()

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

        # Build match list + rank. Matching is now both surface-aware and lemma-sequence-aware.
        for i, c in enumerate(collocs, start=1):
            matched_by = _match_types_for_collocation(
                phrase_surface=phrase_surface,
                phrase_lemma_sequence=phrase_lemma_sequence,
                collocation=c,
            )
            if not matched_by:
                continue

            match_key = "|".join(
                [
                    str(hw),
                    str(c.get("lexical_unit_id")),
                    str(c.get("structure_id")),
                    _norm(c.get("surface") or ""),
                    ",".join(matched_by),
                ]
            )
            if match_key in seen_matches:
                continue
            seen_matches.add(match_key)

            match_type = "surface_and_lemma_sequence" if len(matched_by) > 1 else matched_by[0]
            matches.append(
                {
                    "headword": hw,
                    "rank": i,
                    "match_type": match_type,
                    "matched_by": matched_by,
                    "frequency": c.get("frequency"),
                    "logDice": c.get("logDice"),
                    "structure_id": c.get("structure_id"),
                    "lexical_unit_id": c.get("lexical_unit_id"),
                    "surface": c.get("surface"),
                    "surface_norm": _norm(c.get("surface") or ""),
                    "component_lemmas": _component_lemma_sequence(c),
                    "components": c.get("components", []),
                }
            )

    return {
        "input_text": text,
        "lang": lang,
        "tokens": tokens,
        "phrase_surface_norm": phrase_surface,
        "phrase_lemma_sequence": phrase_lemma_sequence,
        "phrase_lemma_norm": phrase_lemma_norm,
        "lemmas_extracted": extracted,
        "headwords_checked": headwords,
        "matches": matches,
        "evidence": evidence,
        "interpretation_hint": (
            "If matches is non-empty, the phrase exists among top collocations for at least one headword lemma. "
            "match_type='surface' means the surface form matched exactly after normalization; "
            "match_type='lemma_sequence' means an inflected user phrase matched the collocation component lemmas. "
            "If empty, it may still be valid; use evidence lists to suggest more typical alternatives."
        ),
    }
