"""Schemas for source retrieval endpoints."""

from pydantic import BaseModel


class SearchHit(BaseModel):
    resource_id: str
    title: str
    url: str | None = None
    language: str | None = None
    rights_mode: str
    source_slug: str
    source_badge: str | None = None
    section: str | None = None
    subsection: str | None = None
    format: str | None = None
    priority: str | None = None
    chunk_kind: str
    chunk_index: int
    snippet: str
    score: float
    page_start: int | None = None
    page_end: int | None = None
    segment_start: int | None = None
    segment_end: int | None = None


class SearchResponse(BaseModel):
    query: str
    sources: list[str]
    count: int
    hits: list[SearchHit]
