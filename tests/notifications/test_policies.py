"""Pure rules of the notifications module and the shared targeting rule."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import get_args

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
    period_review_key,
    priority_for,
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
