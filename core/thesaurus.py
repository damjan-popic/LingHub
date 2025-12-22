from pathlib import Path
from typing import Dict, List, TypedDict
import xml.etree.ElementTree as ET

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
THESAURUS_XML_PATH = BASE_DIR / "data" / "thesaurus.xml"


class SenseSynonyms(TypedDict):
    """
    Single sense block with grouped synonyms / antonyms.
    """
    sense_id: str
    core: List[str]
    near: List[str]
    antonyms: List[str]


# Global in-memory index: lemma -> list of senses
THESAURUS: Dict[str, List[SenseSynonyms]] = {}
_LOADED: bool = False


def _load_thesaurus_from_xml() -> Dict[str, List[SenseSynonyms]]:
    """
    Internal helper: parse thesaurus.xml and build the lemma -> senses index.
    Uses lexical_unit_id to normalize related lemmas to headwords where possible.
    """
    tree = ET.parse(THESAURUS_XML_PATH)
    root = tree.getroot()

    # 1) lexical_unit_id -> headword lemma
    lexunit_to_head: Dict[str, str] = {}

    for entry in root.findall("entry"):
        head_lemma_el = entry.find("./head/headword/lemma")
        if head_lemma_el is None or not (head_text := head_lemma_el.text):
            continue

        head_lemma = head_text.strip()
        if not head_lemma:
            continue

        for lu in entry.findall("./head/lexicalUnit"):
            lu_id = lu.get("id")
            if lu_id:
                lexunit_to_head[lu_id] = head_lemma

    # 2) Build lemma -> list of senses
    thes: Dict[str, List[SenseSynonyms]] = {}

    for entry in root.findall("entry"):
        head_lemma_el = entry.find("./head/headword/lemma")
        if head_lemma_el is None or not (head_text := head_lemma_el.text):
            continue

        head_lemma = head_text.strip()
        if not head_lemma:
            continue

        sense_list_el = entry.find("./body/senseList")
        if sense_list_el is None:
            continue

        for sense in sense_list_el.findall("sense"):
            sense_id = sense.get("id", "")

            sense_block: SenseSynonyms = {
                "sense_id": sense_id,
                "core": [],
                "near": [],
                "antonyms": [],
            }

            rel_list_el = sense.find("relatedSenseList")
            if rel_list_el is None:
                continue

            for rel in rel_list_el.findall("relatedSense"):
                rel_type = rel.get("type")  # "synonym" / "antonym"
                if not rel_type:
                    continue

                # Prefer canonical head lemma via lexical_unit_id, fallback to text
                lu_id = rel.get("lexical_unit_id")
                raw_text = (rel.text or "").strip()
                target_lemma = lexunit_to_head.get(lu_id, raw_text)

                if not target_lemma:
                    continue

                if rel_type == "synonym":
                    syn_type = rel.get("synonymType", "core")  # "core" / "near" / None
                    if syn_type == "core":
                        sense_block["core"].append(target_lemma)
                    else:
                        sense_block["near"].append(target_lemma)
                elif rel_type == "antonym":
                    sense_block["antonyms"].append(target_lemma)

            # Only store non-empty senses
            if sense_block["core"] or sense_block["near"] or sense_block["antonyms"]:
                thes.setdefault(head_lemma, []).append(sense_block)

    return thes


def load_thesaurus() -> None:
    """
    Eagerly load the thesaurus into the global THESAURUS dict.

    Safe to call multiple times; it will only parse once per process.
    Intended to be called from FastAPI's startup event.
    """
    global THESAURUS, _LOADED

    if _LOADED:
        return

    thes = _load_thesaurus_from_xml()
    THESAURUS = thes
    _LOADED = True
    print(f"[LingHub] Thesaurus loaded: {len(THESAURUS)} headwords.")


def ensure_loaded() -> None:
    """
    Lazy safety net: load thesaurus on first access if not already loaded.

    In production you'll typically call load_thesaurus() via @app.on_event("startup"),
    so this will usually be a no-op. It just makes tests / ad-hoc scripts safer.
    """
    if not _LOADED:
        load_thesaurus()


def get_senses_for_lemma(lemma: str) -> List[SenseSynonyms]:
    """
    Public API used by routers/thesaurus.py.

    Returns a list of sense dicts for the given lemma, or [] if not found.
    """
    ensure_loaded()
    return THESAURUS.get(lemma, [])


# Optional alias if you ever want a different name in other parts of the code
def get_synonyms(lemma: str) -> List[SenseSynonyms]:
    return get_senses_for_lemma(lemma)
