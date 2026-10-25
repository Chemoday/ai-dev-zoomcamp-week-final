"""Application settings loaded from environment variables (and an optional .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Apartment Association Platform"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "local"

    DATABASE_URL: str = "postgresql+asyncpg://app:app@localhost:5432/app"

    # Allows the X-Demo-User-Id header to impersonate seeded users (local Docker and tests only).
    DEMO_MODE_ENABLED: bool = True

    # Bearer tokens are rejected while no secret is configured (safe default: no forgeable key).
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_TTL_MINUTES: int = 60

    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = True

    OTEL_SERVICE_NAME: str = "apartment-platform-backend"
    # OTLP/HTTP collector base URL, e.g. "http://otel-collector:4318". Empty disables export.
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
