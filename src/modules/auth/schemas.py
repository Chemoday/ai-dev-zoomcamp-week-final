"""Public enums and DTOs of the auth module (docs/API.md section 7)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class AuthMethod(StrEnum):
    MAGIC_LINK = "MAGIC_LINK"
    PASSWORD = "PASSWORD"
    REGISTRATION = "REGISTRATION"


class DeviceKind(StrEnum):
    DESKTOP_BROWSER = "DESKTOP_BROWSER"
    MOBILE_BROWSER = "MOBILE_BROWSER"
    UNKNOWN = "UNKNOWN"


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
