"""Pure rules of module auth (no database)."""

from datetime import UTC, datetime, timedelta

import pytest

from src.core.config import Settings
from src.modules.auth.policies import (
    REFRESH_GRACE,
    RefreshDecision,
    device_kind,
    refresh_decision,
    session_expiry,
    validate_auth_settings,
)
from src.modules.auth.schemas import DeviceKind

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=7)
SECRET = "x" * 32


@pytest.mark.parametrize(
    ("rotated_at", "revoked_at", "expires_at", "expected"),
    [
        (None, None, LATER, RefreshDecision.ROTATE),
        (NOW - REFRESH_GRACE, None, LATER, RefreshDecision.GRACE),
        (NOW - REFRESH_GRACE - timedelta(seconds=1), None, LATER, RefreshDecision.REUSE),
        (None, NOW, LATER, RefreshDecision.EXPIRED),
        (None, None, NOW, RefreshDecision.EXPIRED),
        (NOW - timedelta(days=1), None, NOW, RefreshDecision.EXPIRED),  # an ended session wins
    ],
)
def test_refresh_decision(rotated_at, revoked_at, expires_at, expected) -> None:
    assert (
        refresh_decision(
            now=NOW,
            rotated_at=rotated_at,
            session_revoked_at=revoked_at,
            session_expires_at=expires_at,
        )
        is expected
    )


def test_a_session_is_extended_on_use_but_never_past_its_cap() -> None:
    idle = timedelta(days=7)

    assert session_expiry(NOW, NOW + timedelta(days=90), idle) == NOW + idle
    assert session_expiry(NOW, NOW + timedelta(days=3), idle) == NOW + timedelta(days=3)


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        ("Mozilla/5.0 (X11; Linux x86_64) Firefox/131.0", DeviceKind.DESKTOP_BROWSER),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile/15E148", DeviceKind.MOBILE_BROWSER),
        ("Mozilla/5.0 (Linux; Android 14) Mobile Safari/537.36", DeviceKind.MOBILE_BROWSER),
        ("curl/8.5.0", DeviceKind.UNKNOWN),
        ("", DeviceKind.UNKNOWN),
        (None, DeviceKind.UNKNOWN),
    ],
)  # fmt: skip
def test_device_kind(user_agent: str | None, expected: DeviceKind) -> None:
    assert device_kind(user_agent) is expected


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"JWT_SECRET_KEY": ""}, "JWT_SECRET_KEY"),
        ({"AUTH_HASH_SECRET": ""}, "AUTH_HASH_SECRET"),
        ({"AUTH_HASH_SECRET": "short"}, "AUTH_HASH_SECRET"),
        ({"AUTH_ALLOW_SESSIONLESS_TOKENS": True, "ENVIRONMENT": "prod"}, "SESSIONLESS"),
        ({"AUTH_ALLOW_SESSIONLESS_TOKENS": True, "ENVIRONMENT": "production"}, "SESSIONLESS"),
        ({"AUTH_SESSION_IDLE_DAYS": 30, "AUTH_SESSION_MAX_DAYS": 7}, "MAX_DAYS"),
    ],
)
def test_the_app_refuses_unsafe_auth_settings(overrides: dict, error: str) -> None:
    values = {
        "JWT_SECRET_KEY": SECRET,
        "AUTH_HASH_SECRET": SECRET,
        "AUTH_ALLOW_SESSIONLESS_TOKENS": False,
        "ENVIRONMENT": "prod",
        **overrides,
    }

    with pytest.raises(RuntimeError, match=error):
        validate_auth_settings(Settings(_env_file=None, **values))


def test_valid_auth_settings_pass() -> None:
    validate_auth_settings(
        Settings(
            _env_file=None,
            JWT_SECRET_KEY=SECRET,
            AUTH_HASH_SECRET=SECRET,
            ENVIRONMENT="prod",
            AUTH_ALLOW_SESSIONLESS_TOKENS=False,  # the test environment turns it on
        )
    )
