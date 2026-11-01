"""Pure rules of the auth module (no database access; unit-tested directly).

docs/ARCHITECTURE.md section 4 and docs/API.md section 7.
"""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from enum import StrEnum

from src.core.config import Settings
from src.modules.auth.schemas import DeviceKind

MIN_SECRET_BYTES = 32
REFRESH_TOKEN_BYTES = 32
# Two tabs refreshing at once: the token just replaced still gets an access token (no cookie).
REFRESH_GRACE = timedelta(seconds=60)
# Environments that may run with test-only shortcuts (fail closed for any other name).
NON_PRODUCTION_ENVIRONMENTS = frozenset({"local", "dev", "test"})
REFRESH_COOKIE = "__Secure-refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"
CSRF_HEADER = "X-Requested-With"
# Expired or revoked sessions are kept this long for the sessions list, then deleted.
SESSION_RETENTION = timedelta(days=7)


def validate_auth_settings(settings: Settings) -> None:
    """Refuse to start without the secrets every sign-in depends on (docs/ARCHITECTURE.md 4.7).

    Called by create_app, not by a field validator: Settings() is built at import time, and tools
    such as migrations must keep working without a .env.
    """
    if len(settings.JWT_SECRET_KEY.encode()) < MIN_SECRET_BYTES:
        raise RuntimeError(f"JWT_SECRET_KEY of at least {MIN_SECRET_BYTES} bytes is required")
    if len(settings.AUTH_HASH_SECRET.get_secret_value().encode()) < MIN_SECRET_BYTES:
        raise RuntimeError(f"AUTH_HASH_SECRET of at least {MIN_SECRET_BYTES} bytes is required")
    if (
        settings.AUTH_ALLOW_SESSIONLESS_TOKENS
        and settings.ENVIRONMENT not in NON_PRODUCTION_ENVIRONMENTS
    ):
        raise RuntimeError(
            "AUTH_ALLOW_SESSIONLESS_TOKENS is only allowed for ENVIRONMENT local, dev or test"
        )
    if settings.AUTH_SESSION_MAX_DAYS < settings.AUTH_SESSION_IDLE_DAYS:
        raise RuntimeError("AUTH_SESSION_MAX_DAYS must not be shorter than AUTH_SESSION_IDLE_DAYS")


def keyed_hash(secret: str, value: str) -> str:
    """Hex HMAC-SHA-256 of a token (or a prefixed subject such as "ip:..."); never stored plain."""
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


_MOBILE = re.compile(r"Mobi|Android|iPhone|iPad|iPod", re.IGNORECASE)
_BROWSER = re.compile(r"Mozilla/|Opera/", re.IGNORECASE)


def device_kind(user_agent: str | None) -> DeviceKind:
    """A coarse device class for the sessions list; the header itself is never stored."""
    if not user_agent or not _BROWSER.search(user_agent):
        return DeviceKind.UNKNOWN
    return DeviceKind.MOBILE_BROWSER if _MOBILE.search(user_agent) else DeviceKind.DESKTOP_BROWSER


def session_expiry(now: datetime, max_expires_at: datetime, idle: timedelta) -> datetime:
    """When a session expires after a use at `now`: the idle period, never past the cap."""
    return min(now + idle, max_expires_at)


class RefreshDecision(StrEnum):
    ROTATE = "ROTATE"  # the current token: replace it
    GRACE = "GRACE"  # replaced moments ago (another tab): a new access token, no new cookie
    REUSE = "REUSE"  # replaced long ago: treat as stolen and revoke the session
    EXPIRED = "EXPIRED"  # the session ended (revoked, idle or capped)


def refresh_decision(
    *,
    now: datetime,
    rotated_at: datetime | None,
    session_revoked_at: datetime | None,
    session_expires_at: datetime,
) -> RefreshDecision:
    if session_revoked_at is not None or now >= session_expires_at:
        return RefreshDecision.EXPIRED
    if rotated_at is None:
        return RefreshDecision.ROTATE
    if now - rotated_at <= REFRESH_GRACE:
        return RefreshDecision.GRACE
    return RefreshDecision.REUSE
