"""Pure rules of the notifications module (no database access; unit-tested directly).

docs/API.md section 6 ("Who gets what", "Default preferences") and docs/ARCHITECTURE.md 3.
"""

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
