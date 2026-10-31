"""Pure rules of the notifications module (no database access; unit-tested directly).

docs/API.md section 6 ("Who gets what", "Default preferences") and docs/ARCHITECTURE.md 3.
"""

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta

from src.modules.notifications.schemas import Channel, NotificationEventType, UnavailableReason

# Only this event cannot be switched off.
MANDATORY_EVENTS = frozenset({NotificationEventType.EMERGENCY_PUBLISHED})

_E = NotificationEventType
_C = Channel
DEFAULT_PREFERENCES: Mapping[tuple[NotificationEventType, Channel], bool] = {
    (_E.EMERGENCY_PUBLISHED, _C.IN_APP): True,
    (_E.EMERGENCY_PUBLISHED, _C.EMAIL): True,
    (_E.EMERGENCY_PUBLISHED, _C.TELEGRAM): True,
    (_E.EMERGENCY_PUBLISHED, _C.WEBPUSH): True,
    (_E.EMERGENCY_RESOLVED, _C.IN_APP): True,
    (_E.EMERGENCY_RESOLVED, _C.EMAIL): False,
    (_E.EMERGENCY_RESOLVED, _C.TELEGRAM): True,
    (_E.EMERGENCY_RESOLVED, _C.WEBPUSH): True,
    (_E.READING_DEADLINE, _C.IN_APP): True,
    (_E.READING_DEADLINE, _C.EMAIL): True,
    (_E.READING_DEADLINE, _C.TELEGRAM): True,
    (_E.READING_DEADLINE, _C.WEBPUSH): True,
    (_E.PERIOD_REVIEW, _C.IN_APP): True,
    (_E.PERIOD_REVIEW, _C.EMAIL): True,
    (_E.PERIOD_REVIEW, _C.TELEGRAM): False,
    (_E.PERIOD_REVIEW, _C.WEBPUSH): False,
}

# Plugins behind the external channels (in_app is built in).
CHANNEL_PLUGINS: Mapping[Channel, str] = {
    Channel.EMAIL: "common.email_smtp",
    Channel.TELEGRAM: "common.telegram",
    Channel.WEBPUSH: "common.webpush",
}

# An emergency event processed later than this (e.g. after an outage) reaches people in the app
# only, never on external channels (owner rule: no late alerts outside the app).
LATE_EVENT_THRESHOLD = timedelta(hours=1)


# Dispatcher (docs/ARCHITECTURE.md 3.3): batches, leases and retries.
DISPATCH_BATCH_SIZE = 10
LEASE_DURATION = timedelta(minutes=5)
MAX_ATTEMPTS = 5
# Waits after attempts 1..4; the fifth attempt is the last one.
RETRY_BACKOFF = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
)
# An emergency that could not go out within this time after its delivery row was created is
# skipped on every external channel: retries must not bring an alert late (owner rule).
EMERGENCY_MAX_AGE = timedelta(hours=1)
# Push channels drop other messages after this time (docs/epics/E1_NOTIFICATIONS.md 6.4).
OTHER_MESSAGE_TTL = timedelta(hours=24)


def retry_delay(attempts: int) -> timedelta | None:
    """How long to wait after a retryable failure of attempt number `attempts`; None: give up."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return RETRY_BACKOFF[attempts - 1]


def emergency_too_old(
    event_type: NotificationEventType, created_at: datetime, now: datetime
) -> bool:
    return (
        event_type == NotificationEventType.EMERGENCY_PUBLISHED
        and now - created_at > EMERGENCY_MAX_AGE
    )


# Telegram linking (docs/API.md 6, "Telegram linking").
LINK_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U
LINK_CODE_LENGTH = 8
LINK_CODE_TTL = timedelta(minutes=10)
LINK_CODES_PER_HOUR = 5
LINK_ATTEMPTS_PER_HOUR = 10
LINK_WINDOW = timedelta(hours=1)
MIN_HASH_SECRET_BYTES = 32


def new_link_code() -> str:
    return "".join(secrets.choice(LINK_CODE_ALPHABET) for _ in range(LINK_CODE_LENGTH))


def normalize_link_code(code: str) -> str | None:
    """Upper-cased code, or None when it cannot be one (so it is not even looked up)."""
    code = code.strip().upper()
    if len(code) != LINK_CODE_LENGTH or any(c not in LINK_CODE_ALPHABET for c in code):
        return None
    return code


def keyed_hash(secret: str, value: str) -> str:
    """Hex HMAC-SHA-256: a plain hash of a 10-digit chat id or an 8-character code is reversible."""
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def retry_after_seconds(oldest_in_window: datetime, now: datetime) -> int:
    """Seconds until the oldest event leaves the rolling one-hour window (at least 1)."""
    return max(1, int((oldest_in_window + LINK_WINDOW - now).total_seconds()) + 1)


def is_mandatory(event_type: NotificationEventType) -> bool:
    return event_type in MANDATORY_EVENTS


def is_enabled(
    event_type: NotificationEventType,
    channel: Channel,
    stored: Mapping[tuple[NotificationEventType, Channel], bool],
) -> bool:
    """Effective preference: mandatory events are always on; otherwise the stored deviation,
    else the default."""
    if is_mandatory(event_type):
        return True
    return stored.get((event_type, channel), DEFAULT_PREFERENCES[(event_type, channel)])


def priority_for(event_type: NotificationEventType) -> int:
    """0 is sent first: only the emergency itself jumps the queue."""
    return 0 if event_type == NotificationEventType.EMERGENCY_PUBLISHED else 1


def emergency_key(post_id: uuid.UUID) -> str:
    return f"emergency:{post_id}"


def emergency_resolved_key(post_id: uuid.UUID) -> str:
    return f"emergency_resolved:{post_id}"


def period_review_key(period_id: uuid.UUID, event_id: uuid.UUID) -> str:
    # The event id, so a period that is reopened and reviewed again notifies again.
    return f"period_review:{period_id}:{event_id}"


def external_channels_allowed(occurred_at: datetime, processed_at: datetime) -> bool:
    return processed_at - occurred_at <= LATE_EVENT_THRESHOLD


def channel_unavailable_reason(
    channel: Channel,
    *,
    demo_mode: bool,
    plugin_enabled: bool,
    has_email: bool,
    has_binding: bool,
) -> UnavailableReason | None:
    """Why a channel cannot reach the user, first match wins; None when it can."""
    if channel == Channel.IN_APP:
        return None
    if demo_mode:
        return UnavailableReason.DEMO_MODE
    if not plugin_enabled:
        return UnavailableReason.CHANNEL_NOT_ENABLED
    if channel == Channel.EMAIL:
        return None if has_email else UnavailableReason.NO_EMAIL
    return None if has_binding else UnavailableReason.NOT_LINKED
