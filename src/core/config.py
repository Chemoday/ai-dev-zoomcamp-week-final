"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# HS256 keys shorter than the hash output are brute-forceable (RFC 7518, section 3.2).
MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Apartment Association Platform"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "local"

    DATABASE_URL: str = "postgresql+asyncpg://app:app@localhost:5432/app"

    # Allows the X-Demo-User-Id header to impersonate any user. Off unless enabled explicitly:
    # docker-compose.yml, the test suite and the public demo deployment turn it on.
    DEMO_MODE_ENABLED: bool = False
    # Seed the demo building "KÜ Sõpruse 12" into an EMPTY database at startup (src/core/seed.py).
    # Off unless enabled explicitly: a real association must never get fictitious residents.
    SEED_DEMO_DATA: bool = False

    # Bearer tokens are rejected while no secret is configured (safe default: no forgeable key).
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_TTL_MINUTES: int = 60

    # Comma-separated plugin modules under src/plugins/ (docs/ARCHITECTURE.md).
    ENABLED_PLUGINS: str = "common.csv_export"

    # The built client (docs/FRONTEND_GIT_WORKFLOW.md). Served at / when it holds an index.html.
    FRONTEND_DIST_DIR: str = "frontend/dist"

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    # Notifications (docs/ARCHITECTURE.md section 3): the whole module, the language of messages
    # sent outside the app when a user chose none, and the local time zone of the building
    # (reading deadlines end at local midnight).
    NOTIFICATIONS_ENABLED: bool = True
    DEFAULT_LOCALE: Literal["et", "ru", "en"] = "et"
    INSTANCE_TIMEZONE: str = "Europe/Tallinn"

    # Durable domain events (docs/BACKEND_CONVENTIONS.md 2.B): the relay that publishes events a
    # crash or a failing handler left behind, and how long published events are kept (0 = always).
    DOMAIN_EVENTS_RELAY_ENABLED: bool = True
    DOMAIN_EVENTS_RETENTION_DAYS: int = Field(default=365, ge=0)

    OTEL_SERVICE_NAME: str = "apartment-platform-backend"
    # OTLP/HTTP collector base URL, e.g. "http://otel-collector:4318". Empty disables export.
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""

    @field_validator("INSTANCE_TIMEZONE")
    @classmethod
    def _timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown INSTANCE_TIMEZONE {value!r}") from exc
        return value

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _jwt_secret_is_strong_enough(cls, value: str) -> str:
        if value and len(value.encode()) < MIN_JWT_SECRET_BYTES:
            raise ValueError(f"JWT_SECRET_KEY must be at least {MIN_JWT_SECRET_BYTES} bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
