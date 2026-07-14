from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


EXTERNAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SENSITIVE_PATTERNS = (
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:https?|s3|gs|azure)://", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?key|token|password|secret)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


def to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


def validate_external_id(value: str) -> str:
    normalized = value.strip()
    if not EXTERNAL_ID_PATTERN.fullmatch(normalized):
        raise ValueError("must be an opaque external identifier")
    return normalized


def validate_public_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if any(pattern.search(normalized) for pattern in SENSITIVE_PATTERNS):
        raise ValueError("contains content that is not allowed in public fields")
    return normalized


class SourceType(str, Enum):
    publication = "publication"
    scenario = "scenario"
    edit = "edit"
    external = "external"


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class StrictRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class ApprovalRequestCreate(StrictRequest):
    source_type: SourceType
    source_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    reviewer_user_ids: list[str] = Field(min_length=1, max_length=50)

    _safe_source_id = field_validator("source_id")(validate_external_id)
    _safe_title = field_validator("title")(validate_public_text)
    _safe_description = field_validator("description")(validate_public_text)

    @field_validator("reviewer_user_ids")
    @classmethod
    def validate_reviewers(cls, values: list[str]) -> list[str]:
        normalized = [validate_external_id(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("reviewerUserIds must not contain duplicates")
        return normalized


class ApproveRequest(StrictRequest):
    comment: str | None = Field(default=None, max_length=2000)

    _safe_comment = field_validator("comment")(validate_public_text)


class ReasonRequest(StrictRequest):
    reason: str = Field(min_length=1, max_length=2000)

    _safe_reason = field_validator("reason")(validate_public_text)


class DecisionResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    type: ApprovalStatus
    actor_user_id: str
    comment: str | None = None
    reason: str | None = None
    decided_at: datetime


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    action: str
    actor_user_id: str
    previous_status: ApprovalStatus | None
    new_status: ApprovalStatus
    details: dict[str, Any]
    occurred_at: datetime


class ApprovalRequestResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    workspace_id: str
    source_type: SourceType
    source_id: str
    title: str
    description: str | None
    reviewer_user_ids: list[str]
    status: ApprovalStatus
    created_by_user_id: str
    decision: DecisionResponse | None
    version: int
    created_at: datetime
    updated_at: datetime
    audit_trail: list[AuditEntryResponse]


class ApprovalRequestPage(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    items: list[ApprovalRequestResponse]
    total: int
    limit: int
    offset: int
