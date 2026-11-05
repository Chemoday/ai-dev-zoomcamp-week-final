"""Pure rules of the auth module (no database access; unit-tested directly).

docs/ARCHITECTURE.md section 4 and docs/API.md section 7.
"""

import hashlib
import hmac
import ipaddress
import math
import re
import secrets
import unicodedata
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.config import Settings
from src.modules.auth.schemas import DeviceKind, InvitationStatus, PasswordWeakness, StaffRole
from src.modules.property.schemas import BuildingRole

if TYPE_CHECKING:
    from src.plugins.registry import PluginRegistry

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
EMAIL_PLUGIN = "common.email_smtp"

# Sign-in links (docs/API.md 7): valid this long, and the silent limits on asking for them.
MAGIC_LINK_TTL = timedelta(minutes=15)
LINK_PATH = "/login/link"
LINKS_PER_EMAIL_AND_IP_PER_HOUR = 5
LINKS_PER_EMAIL_PER_DAY = 20
LINKS_PER_IP_PER_HOUR = 20
REDEEMS_PER_IP_PER_HOUR = 20
ATTEMPT_RETENTION = timedelta(hours=24)

# Invitations (docs/API.md 7): the link lives a week, the registration form half an hour.
INVITATION_TTL = timedelta(days=7)
REGISTRATION_TTL = timedelta(minutes=30)
INVITATIONS_PER_INVITER_PER_HOUR = 10
# Expired, accepted or ended invitations are kept this long, then deleted (docs/SCHEMA.md 7).
INVITATION_RETENTION = timedelta(days=30)

# Passwords (A5, docs/API.md 7). Lengths count Unicode code points after NFKC.
PASSWORD_PROVIDER = "password"
MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 128
MAX_PASSWORD_FIELD = 1024  # longer request fields are a 422 VALIDATION_ERROR
MIN_DISTINCT_CHARACTERS = 5
COMMON_PASSWORDS_FILE = Path(__file__).with_name("common_passwords.txt")
# Changing or removing a password without the current one needs a sign-in this recent.
RECENT_SIGN_IN = timedelta(minutes=10)
# Login throttling: failures within the window, then a growing delay; never a lockout. The window
# is a day (owner decision, review of A5): a shorter one than the delay cap would let the delay
# start over before it reaches the cap.
PASSWORD_WINDOW = timedelta(hours=24)
PASSWORD_FAILURES_PER_EMAIL_AND_IP = 5
PASSWORD_FAILURES_PER_IP = 30
PASSWORD_FAILURES_PER_EMAIL_PER_DAY = 100  # then the password waits for an e-mail link sign-in
PASSWORD_FIRST_DELAY = timedelta(seconds=30)
PASSWORD_MAX_DELAY = timedelta(minutes=15)


def email_subject(email: str) -> str:
    return f"email:{email}"


def client_key(address: str) -> str:
    """What the per-address limits count: an IPv4 address, or the /64 of an IPv6 address (one
    household or device usually holds a whole /64, so counting /128s would be no limit)."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6 and ip.ipv4_mapped is not None:  # "::ffff:198.51.100.1" is IPv4
        return str(ip.ipv4_mapped)
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def ip_subject(address: str) -> str:
    return f"ip:{client_key(address)}"


def pair_subject(email: str, address: str) -> str:
    return f"pair:{email}|{client_key(address)}"


def user_subject(user_id: object) -> str:
    return f"user:{user_id}"


def mask_email(email: str) -> str:
    """'nina@example.com' -> 'n***@example.com': enough to recognise, not to read."""
    local, _, domain = email.rpartition("@")
    return f"{local[:1]}***@{domain}" if local else f"***@{domain}"


def invitation_status(
    *, accepted: bool, applies: bool, expires_at: datetime, now: datetime
) -> InvitationStatus:
    """What an invitation looks like to the people who may see it."""
    if accepted:
        return InvitationStatus.ACCEPTED
    if not applies:
        return InvitationStatus.NO_LONGER_VALID
    if expires_at <= now:
        return InvitationStatus.EXPIRED
    return InvitationStatus.PENDING


def staff_invitation_applies(current: BuildingRole, invited: StaffRole) -> bool:
    """Accepting never lowers anything: a staff role goes only to an account without one, except
    that a CHAIRMAN invitation raises any other role (the recovery of the first chairman)."""
    if current == BuildingRole.USER:
        return True
    return invited == StaffRole.CHAIRMAN and current != BuildingRole.CHAIRMAN


def sign_in_link(site_url: str, token: str) -> str:
    """The token travels in the fragment, so it never reaches a server or proxy log."""
    return f"{site_url}{LINK_PATH}#{token}"


def demo_mail_allowed(email: str, domains: str) -> bool:
    """In demo mode with AUTH_DEMO_MAIL_DOMAINS set, only those recipient domains get mail."""
    allowed = {d.strip().lower() for d in domains.split(",") if d.strip()}
    return not allowed or email.rpartition("@")[2] in allowed


def validate_auth_settings(settings: Settings, plugins: "PluginRegistry | None" = None) -> None:
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
    if settings.AUTH_MAIL_IN_DEMO and settings.ENVIRONMENT not in NON_PRODUCTION_ENVIRONMENTS:
        raise RuntimeError("AUTH_MAIL_IN_DEMO is only allowed for ENVIRONMENT local, dev or test")
    if plugins is not None and plugins.is_enabled(EMAIL_PLUGIN) and not settings.PUBLIC_BASE_URL:
        raise RuntimeError("PUBLIC_BASE_URL is required with common.email_smtp (sign-in links)")


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


# --- Passwords (A5) -------------------------------------------------------------------------------


def normalize_password(password: str) -> str:
    """NFKC, so the same letter typed on different keyboards gives the same password. Spaces and
    every script are kept as typed."""
    return unicodedata.normalize("NFKC", password)


def _common_form(password: str) -> str:
    """How passwords are compared with the list: case and spaces ignored."""
    return "".join(password.casefold().split())


@cache
def common_passwords() -> frozenset[str]:
    lines = COMMON_PASSWORDS_FILE.read_text(encoding="ascii").splitlines()
    return frozenset(
        _common_form(line) for line in lines if line.strip() and not line.startswith("#")
    )


def password_weakness(
    password: str, common: Collection[str] | None = None
) -> PasswordWeakness | None:
    """Why a new (normalized) password is refused, or None. No composition rules: a long phrase
    of words is the advice."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return PasswordWeakness.TOO_SHORT
    if len(password) > MAX_PASSWORD_LENGTH:
        return PasswordWeakness.TOO_LONG
    common = common_passwords() if common is None else common
    if len(set(password)) < MIN_DISTINCT_CHARACTERS or _common_form(password) in common:
        return PasswordWeakness.TOO_COMMON
    return None


def is_recent_sign_in(auth_time: datetime | None, now: datetime) -> bool:
    """A token without auth_time never counts as recent."""
    return auth_time is not None and now - auth_time <= RECENT_SIGN_IN


def password_wait(failures: Sequence[datetime], limit: int, now: datetime) -> int | None:
    """Seconds to wait before the next password attempt, or None.

    `failures` are the counted failures in the window, oldest first. From `limit` failures on,
    the next attempt waits PASSWORD_FIRST_DELAY after the last failure, doubling with each further
    failure, at most PASSWORD_MAX_DELAY.
    """
    if len(failures) < limit:
        return None
    doublings = min(len(failures) - limit, 16)  # 30 s * 2**16 is far past the cap already
    delay = min(PASSWORD_FIRST_DELAY * 2**doublings, PASSWORD_MAX_DELAY)
    remaining = (failures[-1] + delay - now).total_seconds()
    return math.ceil(remaining) if remaining > 0 else None
