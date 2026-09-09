"""Pydantic schemas for chat API."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IntentLabel(StrEnum):
    LANGUAGE_QUESTION = "language_question"
    TEXT_CHECK = "text_check"
    TERM_TRANSLATION = "term_translation"
    DOCUMENT_TEMPLATE = "document_template"
    SOURCE_NAVIGATION = "source_navigation"
    TEACHING_EXPLANATION = "teaching_explanation"
    LOCAL_USAGE = "local_usage"
    EXTERNAL_REFERENCE = "external_reference"
    FEEDBACK_OR_REPORT = "feedback_or_report"
    OUT_OF_SCOPE = "out_of_scope"


class AnswerMode(StrEnum):
    DIRECT_ANSWER = "direct_answer"
    TEXT_CHECK_RESULT = "text_check_result"
    SOURCE_NAVIGATION = "source_navigation"
    COMPARISON = "comparison"
    LINKOUT_REQUIRED = "linkout_required"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class ConfidenceLevel(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class UserContext(BaseModel):
    audience: str | None = None
    register_name: str | None = Field(default=None, alias="register")
    language_pair: str | None = None
    model_config = ConfigDict(populate_by_name=True)


class ChatRequest(BaseModel):
    session_id: UUID | None = None
    message: str = Field(..., min_length=1, max_length=5000)
    mode: str = "auto"
    user_context: UserContext | None = None


class SourceBadge(BaseModel):
    label: str
    source_slug: str
    display_name: str
    url: str | None = None
    trust_tier: int
    rights_mode: str


class EvidenceItem(BaseModel):
    source_slug: str
    title: str
    url: str | None = None
    snippet: str | None = None
    citation_id: str | None = None
    external_id: str | None = None
    upstream_key: str | None = None
    matched_field: str | None = None


class NextAction(BaseModel):
    label: str
    url: str | None = None


class ChatAnswer(BaseModel):
    answer_id: UUID
    mode: AnswerMode
    title: str
    short_answer: str
    answer_md: str
    source_badges: list[SourceBadge] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    limitations: list[str] = Field(default_factory=list)
    next_actions: list[NextAction] = Field(default_factory=list)
    feedback_prompt: str = "Je bil odgovor uporaben?"


class ChatResponse(BaseModel):
    answer: ChatAnswer
