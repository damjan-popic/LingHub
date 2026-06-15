from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, TypedDict, Literal, Any
import xml.etree.ElementTree as ET
from .config import LINGHUB_ENABLE_COLLOCATIONS


BASE_DIR = Path(__file__).resolve().parent.parent
COLLOC_DIR = BASE_DIR / "data" / "collocations"

_LOADED: bool = False

# Index key is normalized lemma (casefold)
_INDEX: Dict[str, List["CollocationRecord"]] = {}
_CANONICAL: Dict[str, str] = {}  # normalized -> a display lemma seen in XML (first one wins)


class CollocationComponent(TypedDict):
    num: int
    text: str
    lemma: str
    msd: str
    role: Literal["headword", "collocate", "other"]


class CollocationRecord(TypedDict):
    headword: str                 # display (original from XML)
    headword_key: str             # normalized (casefold)
    sense_id: str
    lexical_unit_id: Optional[int]
    structure_id: Optional[int]
    status: Optional[str]
    frequency: Optional[int]
    logDice: Optional[float]
    surface: str
    components: List[CollocationComponent]


def _to_int(x: Optional[str]) -> Optional[int]:
    if x is None:
        return None
    try:
        return int(x)
    except Exception:
        return None


def _to_float(x: Optional[str]) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _norm_lemma(s: str) -> str:
    # robust lowercase for users
    return s.strip().casefold()


def _iter_shard_files() -> List[Path]:
    if not COLLOC_DIR.exists():
        raise FileNotFoundError(f"Collocations directory not found: {COLLOC_DIR}")
    files = sorted(COLLOC_DIR.glob("collocations_export_*.xml"))
    if not files:
        raise FileNotFoundError(f"No collocations_export_*.xml files found in: {COLLOC_DIR}")
    return files


def load_collocations() -> None:
    """
    Eagerly load ALL collocations shard XML files into RAM and build an index:
      headword_key (casefold) -> list[CollocationRecord]
    """
    global _LOADED, _INDEX, _CANONICAL
    if _LOADED:
        return

    if not LINGHUB_ENABLE_COLLOCATIONS:
        print("[LingHub] Collocations disabled; set LINGHUB_ENABLE_COLLOCATIONS=1 to load XML shards.")
        _INDEX = {}
        _CANONICAL = {}
        _LOADED = True
        return

    print(f"[LingHub] Loading collocations from: {COLLOC_DIR}")
    index: Dict[str, List[CollocationRecord]] = {}
    canonical: Dict[str, str] = {}

    files = _iter_shard_files()
    print(f"[LingHub] Collocations shards found: {len(files)}")

    for fp in files:
        print(f"[LingHub] Parsing {fp.name} ...")
        tree = ET.parse(fp)
        root = tree.getroot()

        for entry in root.findall("./entry"):
            head_el = entry.find("./head/headword/lemma")
            if head_el is None or head_el.text is None:
                continue

            headword = head_el.text.strip()
            if not headword:
                continue

            head_key = _norm_lemma(headword)
            canonical.setdefault(head_key, headword)

            # iterate senses
            for sense in entry.findall("./body/senseList/sense"):
                sense_id = (sense.get("id") or "").strip()

                # find all multiwordExample type="collocation" under exampleContainerList
                for mw in sense.findall(
                    "./exampleContainerList/exampleContainer/multiwordExample[@type='collocation']"
                ):
                    lexical_unit_id = _to_int(mw.get("lexical_unit_id"))
                    structure_id = _to_int(mw.get("structure_id"))
                    status = mw.get("status")
                    frequency = _to_int(mw.get("frequency"))
                    logdice = _to_float(mw.get("logDice"))  # may not exist in all exports

                    comps_raw = mw.findall("./comp")
                    if not comps_raw:
                        continue

                    # components are in sequence already, but use @num as backup ordering
                    comps_sorted = sorted(
                        comps_raw,
                        key=lambda c: _to_int(c.get("num")) or 10**9
                    )

                    components: List[CollocationComponent] = []
                    surface_parts: List[str] = []

                    for c in comps_sorted:
                        text = (c.text or "").strip()
                        lemma = (c.get("lemma") or "").strip()
                        msd = (c.get("msd") or "").strip()
                        num = _to_int(c.get("num")) or 0

                        # infer role: if lemma matches headword lemma (normalized), it's headword
                        role: Literal["headword", "collocate", "other"]
                        if _norm_lemma(lemma) == head_key and lemma:
                            role = "headword"
                        elif lemma:
                            role = "collocate"
                        else:
                            role = "other"

                        components.append(
                            {
                                "num": num,
                                "text": text,
                                "lemma": lemma,
                                "msd": msd,
                                "role": role,
                            }
                        )
                        if text:
                            surface_parts.append(text)

                    surface = " ".join(surface_parts).strip()

                    rec: CollocationRecord = {
                        "headword": headword,
                        "headword_key": head_key,
                        "sense_id": sense_id,
                        "lexical_unit_id": lexical_unit_id,
                        "structure_id": structure_id,
                        "status": status,
                        "frequency": frequency,
                        "logDice": logdice,
                        "surface": surface,
                        "components": components,
                    }

                    index.setdefault(head_key, []).append(rec)

    # Optional: pre-sort each lemma list by frequency desc, then logDice desc, then surface
    for k, lst in index.items():
        lst.sort(
            key=lambda r: (
                -(r["frequency"] or 0),
                -(r["logDice"] or 0.0),
                r["surface"] or "",
            )
        )

    _INDEX = index
    _CANONICAL = canonical
    _LOADED = True
    print(f"[LingHub] Collocations loaded: {len(_INDEX)} headwords indexed.")


def ensure_loaded() -> None:
    if not _LOADED:
        load_collocations()


def get_collocations_for_lemma(
    lemma: str,
    *,
    min_freq: int = 1,
    min_logdice: Optional[float] = None,
    structure_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 50,
    sort: Literal["freq", "logdice"] = "freq",
    order: Literal["desc", "asc"] = "desc",
) -> Dict[str, Any]:
    """
    Return collocations for a lemma with basic filters and sorting.
    """
    ensure_loaded()

    q = _norm_lemma(lemma)
    all_recs = _INDEX.get(q, [])
    canonical = _CANONICAL.get(q, lemma.strip())

    # filter
    filtered: List[CollocationRecord] = []
    status_norm = status.strip().casefold() if status else None

    for r in all_recs:
        f = r.get("frequency") or 0
        ld = r.get("logDice")

        if f < max(min_freq, 1):
            continue
        if min_logdice is not None:
            if ld is None or ld < min_logdice:
                continue
        if structure_id is not None and r.get("structure_id") != structure_id:
            continue
        if status_norm is not None:
            rs = (r.get("status") or "").casefold()
            if rs != status_norm:
                continue

        filtered.append(r)

    count_total = len(all_recs)

    # sort (we already pre-sorted by freq desc; but allow caller control)
    reverse = (order == "desc")
    if sort == "logdice":
        filtered.sort(
            key=lambda r: ((r.get("logDice") or 0.0), (r.get("frequency") or 0), r.get("surface") or ""),
            reverse=reverse,
        )
    else:
        filtered.sort(
            key=lambda r: ((r.get("frequency") or 0), (r.get("logDice") or 0.0), r.get("surface") or ""),
            reverse=reverse,
        )

    # limit clamp
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    returned = filtered[:limit]

    return {
        "lemma": canonical,
        "lemma_key": q,
        "count_total": count_total,
        "count_returned": len(returned),
        "filters": {
            "min_freq": max(min_freq, 1),
            "min_logdice": min_logdice,
            "structure_id": structure_id,
            "status": status,
            "limit": limit,
            "sort": sort,
            "order": order,
        },
        "collocations": returned,
    }
