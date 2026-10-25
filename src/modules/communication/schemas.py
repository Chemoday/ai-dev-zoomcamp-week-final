"""Public enums and DTOs of the communication module (docs/API.md, section 4).

Authors leave this module only as an AuthorBadgeDTO resolved by PropertyService: no payload
carries a user id, a name, an email or a phone.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StrictInt

from src.core.validation import safe_text
from src.modules.property.schemas import AuthorBadgeDTO


class PostType(StrEnum):
    EMERGENCY = "EMERGENCY"
    OFFICIAL_NOTICE = "OFFICIAL_NOTICE"
    FORUM_TOPIC = "FORUM_TOPIC"


class PostCategory(StrEnum):
    GENERAL = "GENERAL"
    WATER_SHUTOFF = "WATER_SHUTOFF"
    MAINTENANCE = "MAINTENANCE"
    MARKETPLACE = "MARKETPLACE"


class TargetScope(StrEnum):
    ALL_BUILDING = "ALL_BUILDING"
    ENTRANCE = "ENTRANCE"
    APARTMENT_LIST = "APARTMENT_LIST"


TITLE_MAX_LENGTH = 255  # VARCHAR(255)
POST_BODY_MAX_LENGTH = 10_000
COMMENT_BODY_MAX_LENGTH = 4_000
MAX_TARGET_APARTMENTS = 500
FEED_PAGE_MAX = 100
COMMENTS_PAGE_MAX = 500
COMMENTS_PAGE_DEFAULT = 200

TitleText = safe_text(TITLE_MAX_LENGTH)
PostBodyText = safe_text(POST_BODY_MAX_LENGTH)
CommentBodyText = safe_text(COMMENT_BODY_MAX_LENGTH)


def _unique_apartment_ids(ids: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    """Drop duplicates (keeping order), then apply the limit; an empty list means "not given"."""
    if not ids:
        return None
    unique = list(dict.fromkeys(ids))
    if len(unique) > MAX_TARGET_APARTMENTS:
        raise ValueError(f"at most {MAX_TARGET_APARTMENTS} different apartments can be targeted")
    return unique


TargetApartmentIds = Annotated[list[uuid.UUID] | None, AfterValidator(_unique_apartment_ids)]


# --- Requests ------------------------------------------------------------------------------------


class PostCreateRequest(BaseModel):
    """Body of POST /api/v1/communication/posts."""

    model_config = ConfigDict(extra="forbid")

    post_type: PostType
    category: PostCategory = PostCategory.GENERAL
    title: TitleText
    body: PostBodyText
    target_scope: TargetScope = TargetScope.ALL_BUILDING
    target_entrance: StrictInt | None = Field(default=None, ge=1, le=1000)
    target_apartment_ids: TargetApartmentIds = None
    comments_enabled: StrictBool = True
    # The apartment the author writes from; it decides the badge (docs/API.md, section 4).
    author_apartment_id: uuid.UUID | None = None


class CommentCreateRequest(BaseModel):
    """Body of POST /api/v1/communication/posts/{post_id}/comments."""

    model_config = ConfigDict(extra="forbid")

    body: CommentBodyText
    author_apartment_id: uuid.UUID | None = None


# --- Responses -----------------------------------------------------------------------------------


class PostView(BaseModel):
    id: uuid.UUID
    post_type: PostType
    category: PostCategory
    title: str
    body: str
    author_badge: AuthorBadgeDTO
    is_mine: bool
    target_scope: TargetScope
    target_entrance: int | None
    target_apartment_ids: list[uuid.UUID] | None
    is_active: bool
    comments_enabled: bool
    comments_count: int
    created_at: datetime


class EmergencyView(PostView):
    """An active emergency for the banner; acknowledged_at is the caller's own acknowledgement."""

    acknowledged_at: datetime | None


class CommentView(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    body: str
    author_badge: AuthorBadgeDTO
    is_mine: bool
    created_at: datetime


class PostDetail(PostView):
    comments: list[CommentView]


class AcknowledgeResponse(BaseModel):
    post_id: uuid.UUID
    acknowledged_at: datetime


class ResolveResponse(BaseModel):
    post_id: uuid.UUID
    is_active: bool
