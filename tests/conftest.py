import asyncio
import io
import json
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from src.core.config import get_settings
from src.core.database import get_db_session
from src.core.migrations import alembic_config
from src.core.telemetry import PIIScrubbingSpanExporter, configure_logging, configure_tracing
from src.main import create_app

# --- Database (integration tests) --------------------------------------------------------------


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """TEST_DATABASE_URL if set (CI service), otherwise a throwaway PostgreSQL container.

    The container keeps `pytest` a single command locally: only Docker is required.
    """
    if url := os.environ.get("TEST_DATABASE_URL"):
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
async def db_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """Engine for a database migrated with the real Alembic migrations (tested on every run)."""
    # Alembic's env.py calls asyncio.run(), so it runs outside this event loop.
    await asyncio.to_thread(command.upgrade, alembic_config(database_url), "head")
    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session inside an outer transaction that is rolled back after the test.

    Commits issued by the app only release a SAVEPOINT, so tests never leak data.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
def db_app(app: FastAPI, db_session: AsyncSession) -> Iterator[FastAPI]:
    """The app with get_db_session bound to the test transaction (same commit/rollback rules).

    Each request starts by committing (releasing the SAVEPOINT of) the test's own setup data, so a
    failing request rolls back only its own changes, as it would with a real per-request session.
    """

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        await db_session.commit()
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db_session] = override_db_session
    yield app
    app.dependency_overrides.pop(get_db_session, None)


@pytest.fixture
async def api_client(db_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=db_app), base_url="http://test") as client:
        yield client


# --- App, telemetry and logs -------------------------------------------------------------------


@pytest.fixture(scope="session")
def _session_span_exporter() -> InMemorySpanExporter:
    """Collects every finished span, already scrubbed, exactly as a real exporter would see it."""
    provider = configure_tracing(get_settings())
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(PIIScrubbingSpanExporter(exporter)))
    return exporter


@pytest.fixture
def span_exporter(_session_span_exporter: InMemorySpanExporter) -> InMemorySpanExporter:
    _session_span_exporter.clear()
    return _session_span_exporter


@pytest.fixture
def app(span_exporter: InMemorySpanExporter) -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


class LogCapture:
    def __init__(self) -> None:
        self.stream = io.StringIO()

    @property
    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.stream.getvalue().splitlines() if line.strip()]

    @property
    def raw(self) -> str:
        return self.stream.getvalue()


@pytest.fixture
def log_capture(app: FastAPI) -> Iterator[LogCapture]:
    """Redirects the real JSON logging pipeline (with PII scrubbing) into memory."""
    capture = LogCapture()
    configure_logging(level="INFO", json_logs=True, stream=capture.stream)
    yield capture
    configure_logging(level="INFO", json_logs=True)
