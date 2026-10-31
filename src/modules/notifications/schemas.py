"""Public enums and DTOs of the notifications module (docs/API.md, section 6).

Notifications carry codes and ids only: the frontend renders localized texts from `event_type`
and `params`, and loads the post itself if it needs the title. No names, e-mails or bodies.
"""

import base64
import binascii
import re
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from src.core.validation import safe_text

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


class VapidPublicKeyResponse(BaseModel):
    """The application server key for `pushManager.subscribe` (base64url, 65 bytes)."""

    public_key: str


PUSH_ENDPOINT_MAX_LENGTH = 2048
DEVICE_LABEL_MAX_LENGTH = 100
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")
DeviceLabel = safe_text(DEVICE_LABEL_MAX_LENGTH)


def _base64url_of(length: int, *, p256_point: bool = False) -> AfterValidator:
    def check(value: str) -> str:
        if len(value) > 128 or not _BASE64URL.fullmatch(value):
            raise ValueError("must be base64url")
        stripped = value.rstrip("=")
        try:
            raw = base64.urlsafe_b64decode(stripped + "=" * (-len(stripped) % 4))
        except (binascii.Error, ValueError):
            raise ValueError("must be base64url") from None
        if len(raw) != length:
            raise ValueError(f"must be base64url of {length} bytes")
        if p256_point:  # uncompressed (0x04) and on the curve, or it could never encrypt
            try:
                if raw[0] != 0x04:
                    raise ValueError
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
            except ValueError:
                raise ValueError("must be a P-256 public key") from None
        return stripped

    return AfterValidator(check)


class WebPushKeys(BaseModel):
    # The browser's P-256 key (an uncompressed point) and the 16-byte auth secret (RFC 8291).
    p256dh: Annotated[str, _base64url_of(65, p256_point=True)]
    auth: Annotated[str, _base64url_of(16)]


class WebPushSubscriptionRequest(BaseModel):
    """`PushSubscription.toJSON()` plus a label; `expirationTime` and other fields are ignored."""

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=1, max_length=PUSH_ENDPOINT_MAX_LENGTH)
    keys: WebPushKeys
    label: DeviceLabel
