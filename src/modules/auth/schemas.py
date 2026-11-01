"""Public enums and DTOs of the auth module (docs/API.md section 7)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.validation import canonical_email, clean_text

MAX_EMAIL_LENGTH = 254


class AuthMethod(StrEnum):
    MAGIC_LINK = "MAGIC_LINK"
    PASSWORD = "PASSWORD"
    REGISTRATION = "REGISTRATION"


class DeviceKind(StrEnum):
    DESKTOP_BROWSER = "DESKTOP_BROWSER"
    MOBILE_BROWSER = "MOBILE_BROWSER"
    UNKNOWN = "UNKNOWN"


class TokenPurpose(StrEnum):
    MAGIC_LINK = "MAGIC_LINK"
    INVITATION = "INVITATION"
    REGISTRATION = "REGISTRATION"


class AttemptKind(StrEnum):
    MAGIC_LINK = "MAGIC_LINK"
    REDEEM = "REDEEM"
    PASSWORD = "PASSWORD"
    INVITATION = "INVITATION"


class SignInStatus(StrEnum):
    SIGNED_IN = "SIGNED_IN"
    REGISTRATION_REQUIRED = "REGISTRATION_REQUIRED"  # A4
    SIGN_IN_TO_ACCEPT = "SIGN_IN_TO_ACCEPT"  # A4


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, value: str) -> str:
        canonical = canonical_email(clean_text(value))  # control characters (NUL) are refused
        local, _, domain = canonical.rpartition("@")
        if (
            not local
            or "." not in domain
            or any(c.isspace() for c in canonical)
            or len(canonical) > MAX_EMAIL_LENGTH
        ):
            raise ValueError("not an e-mail address")
        return canonical


class MagicLinkAccepted(BaseModel):
    accepted: bool = True


class RedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128)


class SessionResponse(BaseModel):
    """What a sign-in or a refresh returns; the refresh cookie travels next to it."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    session_id: uuid.UUID


class SessionView(BaseModel):
    session_id: uuid.UUID
    device_kind: DeviceKind
    auth_method: AuthMethod
    authenticated_at: datetime
    last_used_at: datetime
    current: bool


@dataclass(frozen=True)
class IssuedSession:
    """A session just started or refreshed: the answer, and the refresh token for the cookie
    (None when the browser already holds the current one: the grace window)."""

    response: SessionResponse
    refresh_token: str | None
    expires_at: datetime  # of the session, for the cookie's Max-Age


class SignInResponse(BaseModel):
    """The answer of redeeming a link (docs/API.md 7); A3 knows only SIGNED_IN."""

    status: SignInStatus
    session: SessionResponse | None = None
