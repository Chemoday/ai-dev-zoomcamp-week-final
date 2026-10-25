import io
import json
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.core.config import get_settings
from src.core.telemetry import PIIScrubbingSpanExporter, configure_logging, configure_tracing
from src.main import create_app


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
