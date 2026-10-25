"""Unit tests for token handling and settings validation (no database)."""

import uuid
from datetime import timedelta

import pytest
from pydantic import ValidationError

from src.core.config import Settings
from src.core.exceptions import DomainError
from src.core.security import create_access_token, decode_access_token

SECRET = "unit-test-secret-key-that-is-32-bytes+"


def test_short_jwt_secret_is_rejected_at_startup() -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        Settings(JWT_SECRET_KEY="too-short")


def test_empty_jwt_secret_is_allowed_and_disables_bearer_auth() -> None:
    settings = Settings(JWT_SECRET_KEY="")

    with pytest.raises(DomainError) as caught:
        decode_access_token("anything", settings)
    assert caught.value.status_code == 401


def test_token_roundtrip() -> None:
    settings = Settings(JWT_SECRET_KEY=SECRET)
    user_id = uuid.uuid4()

    assert decode_access_token(create_access_token(user_id, settings), settings) == user_id


def test_zero_ttl_means_already_expired_not_default_ttl() -> None:
    settings = Settings(JWT_SECRET_KEY=SECRET)
    token = create_access_token(uuid.uuid4(), settings, expires_in=timedelta(0))

    with pytest.raises(DomainError) as caught:
        decode_access_token(token, settings)
    assert caught.value.error_code == "INVALID_TOKEN"


def test_demo_mode_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEMO_MODE_ENABLED", raising=False)

    assert Settings(_env_file=None).DEMO_MODE_ENABLED is False
