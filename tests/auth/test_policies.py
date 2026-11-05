"""Pure rules of module auth (no database)."""

from datetime import UTC, datetime, timedelta

import pytest

from src.core.config import Settings
from src.modules.auth.policies import (
    REFRESH_GRACE,
    RefreshDecision,
    common_passwords,
    device_kind,
    is_recent_sign_in,
    normalize_password,
    password_wait,
    password_weakness,
    refresh_decision,
    session_expiry,
    validate_auth_settings,
)
from src.modules.auth.schemas import DeviceKind, PasswordWeakness

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


# --- Passwords (A5) -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "weakness"),
    [
        ("x" * 14, PasswordWeakness.TOO_SHORT),
        ("my cat murka 19", None),  # exactly 15
        ("a few words " + "z" * 116, None),  # exactly 128
        ("a few words " + "z" * 117, PasswordWeakness.TOO_LONG),
        ("aaaabbbbccccdddd", PasswordWeakness.TOO_COMMON),  # 4 different characters
        ("aaaabbbbccccdddde", None),  # 5
        ("PASSWORD password", PasswordWeakness.TOO_COMMON),  # case and spaces ignored
    ],
)
def test_password_weakness(password: str, weakness: PasswordWeakness | None) -> None:
    assert password_weakness(password) == weakness


def test_lengths_count_code_points_after_nfkc() -> None:
    # Full-width digits become plain ones; the ligature "ffi" becomes three letters.
    assert normalize_password("\uff11\uff12\uff13") == "123"
    assert normalize_password("\ufb03") == "ffi"
    assert len(normalize_password("\u0435\u0308")) == 1  # e + diaeresis is one letter


def test_the_common_list_is_loaded_in_its_compared_form() -> None:
    assert "correcthorsebatterystaple" in common_passwords()
    assert all(entry == "".join(entry.casefold().split()) for entry in common_passwords())


def test_a_recent_sign_in_is_ten_minutes_and_needs_auth_time() -> None:
    now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

    assert is_recent_sign_in(now - timedelta(minutes=10), now)
    assert not is_recent_sign_in(now - timedelta(minutes=10, seconds=1), now)
    assert not is_recent_sign_in(None, now)


def test_the_password_delay_starts_at_the_limit_and_doubles_up_to_fifteen_minutes() -> None:
    now = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)

    def failures(count: int) -> list[datetime]:
        return [now - timedelta(seconds=count - index) for index in range(count)]

    assert password_wait(failures(4), 5, now) is None
    assert password_wait(failures(5), 5, now) == 29  # 30 s after the last failure, 1 s ago
    assert password_wait(failures(6), 5, now) == 59
    assert password_wait(failures(7), 5, now) == 119
    assert password_wait(failures(40), 5, now) == 15 * 60 - 1  # capped
    assert password_wait(failures(5), 5, now + timedelta(seconds=29)) is None
