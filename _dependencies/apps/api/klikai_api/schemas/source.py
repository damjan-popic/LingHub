"""Pydantic schemas for sources."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class SourceBase(BaseModel):
    slug: str
    display_name: str
    source_type: str
    trust_tier: int
    rights_mode: str
    adapter_key: str
    base_url: Optional[str] = None
    description: Optional[str] = None
    badge_label: Optional[str] = None
    root_path: Optional[str] = None
    is_active: bool = True


class SourceResponse(SourceBase):
    id: str
    model_config = ConfigDict(from_attributes=True)


class SourceListResponse(BaseModel):
    sources: list[SourceResponse]
