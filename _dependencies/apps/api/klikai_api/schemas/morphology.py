"""Typed internal contracts for deterministic morphology lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MorphologyLanguage = Literal["sl", "it"]
MorphologyMatch = Literal["exact", "folded"]
MorphologySignatureKind = Literal["tokenized", "lemma"]


@dataclass(frozen=True, slots=True)
class LemmaCandidate:
    language: MorphologyLanguage
    lemma: str
    lemma_normalized: str
    msds: tuple[str, ...]
    pos_families: tuple[str, ...]
    matched_by: MorphologyMatch


@dataclass(frozen=True, slots=True)
class MorphTokenDetail:
    token: str
    token_normalized: str
    selected: str
    lemma_candidates: tuple[str, ...]
    matched_by: MorphologyMatch | None
    protected: bool


@dataclass(frozen=True, slots=True)
class MorphSignature:
    language: MorphologyLanguage
    signature_normalized: str
    signature_folded: str
    source_span: str
    kind: MorphologySignatureKind
    changed_tokens: int
    ambiguity_cost: int
    token_details: tuple[MorphTokenDetail, ...]


@dataclass(frozen=True, slots=True)
class InflectionGroup:
    language: MorphologyLanguage
    lemma: str
    forms: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class MorphologyRelease:
    release_id: str
    release_fingerprint: str
    release_fingerprint_inputs: dict[str, object]
    root: str
    normalization_version: str
    language_paths: dict[str, str]
    manifest: dict[str, object]
