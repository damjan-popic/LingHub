"""Public schemas for precise terminology lookup."""

from pydantic import BaseModel, Field


class TerminologyMatchedForm(BaseModel):
    term: str
    match_tier: str
    field: str
    language: str
    field_priority: int
    normalization: str = "raw"
    matched_query: str | None = None
    query_signature: str | None = None
    query_language: str | None = None
    lexicon_release_id: str | None = None
    ambiguity_cost: int = 0


class TerminologyHit(BaseModel):
    source_slug: str
    source_display_name: str
    source_badge: str | None = None
    rights_mode: str
    trust_tier: int
    external_id: str
    upstream_key: str
    record_kind: str
    source_date: str | None = None
    title: str
    categories: list[str] = Field(default_factory=list)
    subcategories: list[str] = Field(default_factory=list)
    matched_forms: list[TerminologyMatchedForm] = Field(default_factory=list)
    original_terms: list[str] = Field(default_factory=list)
    standardized_terms: list[str] = Field(default_factory=list)
    proposals: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    examples: list[object] = Field(default_factory=list)
    references: list[object] = Field(default_factory=list)
    snippet: str | None = None
    score: float
    citation_id: str


class TerminologyLookupResponse(BaseModel):
    query: str
    sources: list[str]
    language: str | None = None
    count: int
    hits: list[TerminologyHit]
