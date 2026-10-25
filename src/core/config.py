"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache

from pydantic import field_validator
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

    # Bearer tokens are rejected while no secret is configured (safe default: no forgeable key).
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_TTL_MINUTES: int = 60

    # Comma-separated plugin modules under src/plugins/ (docs/ARCHITECTURE.md).
    ENABLED_PLUGINS: str = "common.csv_export"

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    OTEL_SERVICE_NAME: str = "apartment-platform-backend"
    # OTLP/HTTP collector base URL, e.g. "http://otel-collector:4318". Empty disables export.
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""

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
