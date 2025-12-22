# routers/thesaurus.py
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Any
from core.thesaurus import get_senses_for_lemma

router = APIRouter()


class SenseOut(BaseModel):
    sense_id: str
    core: List[str]
    near: List[str]
    antonyms: List[str]


class ThesaurusResponse(BaseModel):
    lemma: str
    senses: List[SenseOut]


@router.get("/synonyms", response_model=ThesaurusResponse)
async def get_synonyms(
    lemma: str = Query(..., description="Headword lemma, e.g. 'nepretrgan'")
):
    lemma = lemma.strip()
    if not lemma:
        raise HTTPException(status_code=400, detail="Empty lemma.")

    senses_raw = get_senses_for_lemma(lemma)
    if not senses_raw:
        raise HTTPException(status_code=404, detail="Lemma not found in thesaurus.")

    senses = [
        SenseOut(
            sense_id=s["sense_id"],
            core=sorted(set(s["core"])),
            near=sorted(set(s["near"])),
            antonyms=sorted(set(s["antonyms"])),
        )
        for s in senses_raw
    ]

    return ThesaurusResponse(lemma=lemma, senses=senses)
