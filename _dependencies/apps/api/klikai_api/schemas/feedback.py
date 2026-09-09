"""Pydantic schemas for feedback."""

from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FeedbackRatingEnum(str, Enum):
    USEFUL = "useful"
    NOT_USEFUL = "not_useful"
    WRONG = "wrong"
    MISSING = "missing"
    UNCLEAR = "unclear"


class FeedbackTriageStatusEnum(str, Enum):
    NEW = "new"
    REVIEWING = "reviewing"
    FIXED = "fixed"
    WONTFIX = "wontfix"


class FeedbackRequest(BaseModel):
    answer_id: UUID
    rating: FeedbackRatingEnum
    comment: Optional[str] = None
    problem_code: Optional[str] = None
    allow_contact: bool = False


class FeedbackResponse(BaseModel):
    id: UUID
    answer_id: UUID
    rating: FeedbackRatingEnum
    comment: Optional[str] = None
    triage_status: FeedbackTriageStatusEnum = FeedbackTriageStatusEnum.NEW
    created_at: str

    model_config = ConfigDict(from_attributes=True)
