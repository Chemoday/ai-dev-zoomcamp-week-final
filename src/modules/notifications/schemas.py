"""Public enums and DTOs of the notifications module (docs/API.md, section 6).

Notifications carry codes and ids only: the frontend renders localized texts from `event_type`
and `params`, and loads the post itself if it needs the title. No names, e-mails or bodies.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

FEED_PAGE_DEFAULT = 50
FEED_PAGE_MAX = 100
FEED_OFFSET_MAX = 100_000


class Channel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    TELEGRAM = "telegram"
    WEBPUSH = "webpush"


class NotificationEventType(StrEnum):
    EMERGENCY_PUBLISHED = "EMERGENCY_PUBLISHED"
    EMERGENCY_RESOLVED = "EMERGENCY_RESOLVED"
    READING_DEADLINE = "READING_DEADLINE"
    PERIOD_REVIEW = "PERIOD_REVIEW"


class DeliveryStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class SubjectType(StrEnum):
    POST = "post"
    PERIOD = "period"


class UnavailableReason(StrEnum):
    DEMO_MODE = "DEMO_MODE"
    CHANNEL_NOT_ENABLED = "CHANNEL_NOT_ENABLED"
    NO_EMAIL = "NO_EMAIL"
    NOT_LINKED = "NOT_LINKED"


class NotificationView(BaseModel):
    id: uuid.UUID
    event_type: NotificationEventType
    subject_type: SubjectType
    subject_id: uuid.UUID
    params: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class UnreadCountResponse(BaseModel):
    unread: int


class ReadResponse(BaseModel):
    id: uuid.UUID
    read_at: datetime


class ReadAllResponse(BaseModel):
    updated: int


class ChannelAvailability(BaseModel):
    channel: Channel
    available: bool
    reason: UnavailableReason | None = None


class PreferenceView(BaseModel):
    event_type: NotificationEventType
    channel: Channel
    enabled: bool
    mandatory: bool


class PreferencesResponse(BaseModel):
    channels: list[ChannelAvailability]
    # Always all 16 combinations of event type and channel, defaults filled in.
    preferences: list[PreferenceView]


class PreferenceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: NotificationEventType
    channel: Channel
    enabled: bool


class PreferencesUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferences: list[PreferenceChange] = Field(max_length=16)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "PreferencesUpdateRequest":
        keys = [(change.event_type, change.channel) for change in self.preferences]
        if len(keys) != len(set(keys)):
            raise ValueError("each event_type and channel combination may appear only once")
        return self


class LinkCodeResponse(BaseModel):
    """A one-time Telegram link code; shown once, only its hash is stored."""

    code: str
    expires_at: datetime
    deep_link: str


class BindingView(BaseModel):
    """A linked chat or device; the address itself (chat id, endpoint) is never returned."""

    binding_id: uuid.UUID
    channel: Channel
    label: str | None
    created_at: datetime
