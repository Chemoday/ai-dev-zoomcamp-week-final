"""Pure rules of the notifications module and the shared targeting rule."""

import base64
import uuid
from datetime import UTC, datetime, timedelta
from typing import get_args
from zoneinfo import ZoneInfo

import pytest

from src.core.config import Settings
from src.modules.notifications.policies import (
    DEFAULT_PREFERENCES,
    channel_unavailable_reason,
    emergency_key,
    emergency_resolved_key,
    external_channels_allowed,
    is_enabled,
    is_mandatory,
    keyed_hash,
    next_reminder_hours,
    period_review_key,
    priority_for,
    reminder_deadline,
    reminder_due,
    unsubscribe_delivery_id,
    unsubscribe_links,
    unsubscribe_token,
)
from src.modules.notifications.schemas import Channel, NotificationEventType, UnavailableReason
from src.modules.property.policies import apartment_in_target
from src.modules.property.schemas import Locale, TargetScope

E, C = NotificationEventType, Channel


def test_the_default_matrix_covers_every_combination() -> None:
    assert set(DEFAULT_PREFERENCES) == {(e, c) for e in E for c in C}
    # docs/API.md section 6, "Default preferences"
    assert DEFAULT_PREFERENCES[(E.EMERGENCY_RESOLVED, C.EMAIL)] is False
    assert DEFAULT_PREFERENCES[(E.PERIOD_REVIEW, C.TELEGRAM)] is False
    assert DEFAULT_PREFERENCES[(E.PERIOD_REVIEW, C.WEBPUSH)] is False
    assert all(DEFAULT_PREFERENCES[(e, C.IN_APP)] for e in E)


def test_only_the_published_emergency_is_mandatory() -> None:
    assert [e for e in E if is_mandatory(e)] == [E.EMERGENCY_PUBLISHED]


def test_a_stored_preference_overrides_the_default_except_for_mandatory_events() -> None:
    stored = {(E.PERIOD_REVIEW, C.IN_APP): False, (E.EMERGENCY_PUBLISHED, C.IN_APP): False}

    assert is_enabled(E.PERIOD_REVIEW, C.IN_APP, stored) is False
    assert is_enabled(E.EMERGENCY_PUBLISHED, C.IN_APP, stored) is True
    assert is_enabled(E.READING_DEADLINE, C.IN_APP, stored) is True  # default


def test_only_the_emergency_jumps_the_queue() -> None:
    assert priority_for(E.EMERGENCY_PUBLISHED) == 0
    assert {priority_for(e) for e in E if e != E.EMERGENCY_PUBLISHED} == {1}


def test_dedup_keys() -> None:
    post, period, event = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    assert emergency_key(post) == f"emergency:{post}"
    assert emergency_resolved_key(post) == f"emergency_resolved:{post}"
    assert period_review_key(period, event) == f"period_review:{period}:{event}"
    assert period_review_key(period, event) != period_review_key(period, uuid.uuid4()), (
        "a reopened period reviewed again notifies again"
    )


def test_external_channels_are_not_used_for_late_events() -> None:
    occurred = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)

    assert external_channels_allowed(occurred, occurred + timedelta(minutes=59))
    assert not external_channels_allowed(occurred, occurred + timedelta(hours=1, seconds=1))


@pytest.mark.parametrize(
    ("channel", "kwargs", "expected"),
    [
        (C.IN_APP, {"demo_mode": True, "plugin_enabled": False}, None),
        (C.EMAIL, {"demo_mode": True, "plugin_enabled": True}, UnavailableReason.DEMO_MODE),
        (C.TELEGRAM, {"demo_mode": False, "plugin_enabled": False}, "CHANNEL_NOT_ENABLED"),
        (C.EMAIL, {"demo_mode": False, "plugin_enabled": True, "has_email": False}, "NO_EMAIL"),
        (C.EMAIL, {"demo_mode": False, "plugin_enabled": True, "has_email": True}, None),
        (C.WEBPUSH, {"demo_mode": False, "plugin_enabled": True}, "NOT_LINKED"),
        (C.WEBPUSH, {"demo_mode": False, "plugin_enabled": True, "has_binding": True}, None),
    ],
)
def test_channel_availability_first_match_wins(channel, kwargs, expected) -> None:
    arguments = {"has_email": False, "has_binding": False, **kwargs}

    assert channel_unavailable_reason(channel, **arguments) == expected


def test_the_shared_targeting_rule() -> None:
    apartment = uuid.uuid4()

    def matches(scope: TargetScope, entrance: int | None, ids: set[uuid.UUID]) -> bool:
        return apartment_in_target(scope, entrance, ids, apartment_id=apartment, entrance=2)

    assert matches(TargetScope.ALL_BUILDING, None, set())
    assert matches(TargetScope.ENTRANCE, 2, set())
    assert not matches(TargetScope.ENTRANCE, 1, set())
    assert not matches(TargetScope.ENTRANCE, None, set())
    assert matches(TargetScope.APARTMENT_LIST, None, {apartment})
    assert not matches(TargetScope.APARTMENT_LIST, None, {uuid.uuid4()})


def test_the_default_locale_setting_offers_exactly_the_supported_languages() -> None:
    annotation = Settings.model_fields["DEFAULT_LOCALE"].annotation
    assert set(get_args(annotation)) == {locale.value for locale in Locale}


@pytest.mark.parametrize(
    ("local", "days_before", "expected"),
    [
        ((2026, 11, 2, 9, 0), 2, True),  # the first moment of the window
        ((2026, 11, 2, 8, 59), 2, False),
        ((2026, 11, 4, 20, 59), 2, True),  # the deadline day itself
        ((2026, 11, 4, 21, 0), 2, False),
        ((2026, 11, 1, 12, 0), 2, False),  # too early
        ((2026, 11, 4, 12, 0), 0, True),  # lead time 0: the deadline day only
        ((2026, 11, 3, 12, 0), 0, False),
        ((2026, 11, 5, 12, 0), 2, False),  # after the deadline
    ],
)
def test_reminder_window(local: tuple[int, ...], days_before: int, expected: bool) -> None:
    tallinn = ZoneInfo("Europe/Tallinn")
    due = datetime(2026, 11, 4, 23, 59, 59, tzinfo=tallinn).astimezone(UTC)
    now = datetime(*local, tzinfo=tallinn).astimezone(UTC)

    assert reminder_due(due, now, tallinn, days_before) is expected


def test_reminder_window_across_the_end_of_summer_time() -> None:
    # Summer time ends on 25 October 2026 in Tallinn (UTC+3 -> UTC+2); the window is local.
    tallinn = ZoneInfo("Europe/Tallinn")
    due = datetime(2026, 10, 27, 23, 59, 59, tzinfo=tallinn).astimezone(UTC)

    assert reminder_due(due, datetime(2026, 10, 25, 7, 0, tzinfo=UTC), tallinn, 2)  # 09:00 local
    assert not reminder_due(due, datetime(2026, 10, 25, 6, 59, tzinfo=UTC), tallinn, 2)
    assert not reminder_due(due, datetime(2026, 10, 24, 6, 30, tzinfo=UTC), tallinn, 2)


def test_the_next_reminder_hours_start_at_nine_local() -> None:
    tallinn = ZoneInfo("Europe/Tallinn")
    late = datetime(2026, 11, 2, 21, 30, tzinfo=tallinn).astimezone(UTC)
    early = datetime(2026, 11, 3, 3, 0, tzinfo=tallinn).astimezone(UTC)
    nine = datetime(2026, 11, 3, 9, 0, tzinfo=tallinn).astimezone(UTC)

    assert next_reminder_hours(late, tallinn) == nine
    assert next_reminder_hours(early, tallinn) == nine
    assert reminder_deadline({"due_date": "2026-11-04T21:59:59+00:00"}) is not None
    assert reminder_deadline({}) is None
    assert reminder_deadline({"due_date": "2026-11-04T21:59:59"}) is None  # not aware
    assert reminder_deadline({"due_date": "soon"}) is None


# --- Unsubscribe tokens (N8) ----------------------------------------------------------------------

UNSUBSCRIBE_SECRET = "unsubscribe-test-secret-of-32-bytes!"


def test_an_unsubscribe_token_carries_its_delivery_and_nothing_else() -> None:
    delivery_id = uuid.uuid4()
    token = unsubscribe_token(UNSUBSCRIBE_SECRET, delivery_id)

    assert len(token) == 43 and set(token) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert unsubscribe_delivery_id(UNSUBSCRIBE_SECRET, token) == delivery_id
    assert unsubscribe_token(UNSUBSCRIBE_SECRET, delivery_id) == token, "deterministic, not stored"
    assert unsubscribe_delivery_id("another-secret-of-at-least-32-bytes", token) is None
    # Its own prefix: the MAC is not the address hash (keyed_hash) of the delivery id.
    raw = base64.urlsafe_b64decode(token + "=")
    assert raw[16:].hex() != keyed_hash(UNSUBSCRIBE_SECRET, str(delivery_id))[:32]


@pytest.mark.parametrize("token", ["", "x" * 42, "x" * 44, "+" * 43, "/" * 43, "=" * 43])
def test_a_malformed_unsubscribe_token_is_refused(token: str) -> None:
    assert unsubscribe_delivery_id(UNSUBSCRIBE_SECRET, token) is None


def test_the_unsubscribe_links_point_at_the_page_and_the_api() -> None:
    page, one_click = unsubscribe_links("https://kyu.example.test", "T")

    assert page == "https://kyu.example.test/unsubscribe#T"
    assert one_click == "https://kyu.example.test/api/v1/notifications/unsubscribe?token=T"


def test_only_the_canonical_spelling_of_a_token_is_accepted() -> None:
    token = unsubscribe_token(UNSUBSCRIBE_SECRET, uuid.uuid4())
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    # The last character carries 2 spare bits: its 3 siblings decode to the same bytes.
    base = alphabet.index(token[-1]) & ~3
    siblings = [
        token[:-1] + alphabet[base + n] for n in range(4) if alphabet[base + n] != token[-1]
    ]

    assert len(siblings) == 3
    assert all(
        base64.urlsafe_b64decode(s + "=") == base64.urlsafe_b64decode(token + "=") for s in siblings
    )
    assert all(unsubscribe_delivery_id(UNSUBSCRIBE_SECRET, s) is None for s in siblings)
