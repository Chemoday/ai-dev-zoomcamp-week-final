import re

from fastapi import FastAPI
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.database import get_db_session
from src.core.exceptions import DomainError
from src.core.telemetry import REDACTED

TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _server_spans_for(exporter: InMemorySpanExporter, trace_id: str):
    return [
        span
        for span in exporter.get_finished_spans()
        if span.kind == SpanKind.SERVER and format(span.context.trace_id, "032x") == trace_id
    ]


async def test_health_returns_200_with_trace_id_header(
    client: AsyncClient, span_exporter: InMemorySpanExporter
) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    trace_id = response.headers["X-Trace-Id"]
    assert TRACE_ID_RE.match(trace_id)
    assert trace_id != "0" * 32
    # The header points at a real, exported server span.
    assert len(_server_spans_for(span_exporter, trace_id)) == 1


async def test_each_request_gets_its_own_trace_id(client: AsyncClient) -> None:
    first = await client.get("/health")
    second = await client.get("/health")

    assert first.headers["X-Trace-Id"] != second.headers["X-Trace-Id"]


async def test_request_log_line_is_correlated_by_trace_id(client: AsyncClient, log_capture) -> None:
    response = await client.get("/health?email=ivan.petrov@example.com")

    trace_id = response.headers["X-Trace-Id"]
    request_logs = [r for r in log_capture.records if r["event"] == "http_request"]
    assert len(request_logs) == 1
    log = request_logs[0]
    assert log["trace_id"] == trace_id
    assert log["span_id"]
    assert log["method"] == "GET"
    assert log["path"] == "/health"
    assert log["status_code"] == 200
    assert log["level"] == "info"
    assert "ivan.petrov@example.com" not in log_capture.raw


async def test_unhandled_exception_returns_500_with_trace_id_and_correlated_error_log(
    app: FastAPI, client: AsyncClient, span_exporter: InMemorySpanExporter, log_capture
) -> None:
    async def explode() -> None:
        raise RuntimeError("cannot notify ivan.petrov@example.com at +372 5555 1234")

    app.add_api_route("/boom", explode)

    response = await client.get("/boom")

    assert response.status_code == 500
    trace_id = response.headers["X-Trace-Id"]
    assert response.json() == {
        "error_code": "INTERNAL_SERVER_ERROR",
        "message": "An unexpected error occurred. Please try again later.",
        "trace_id": trace_id,
    }

    error_logs = [r for r in log_capture.records if r["event"] == "unhandled_exception"]
    assert len(error_logs) == 1
    error_log = error_logs[0]
    assert error_log["trace_id"] == trace_id
    assert error_log["level"] == "error"
    assert error_log["error_type"] == "RuntimeError"
    # The traceback points the on-call responder at the offending file and line.
    assert "test_health.py" in error_log["exception"]
    assert "in explode" in error_log["exception"]
    assert "ivan.petrov@example.com" not in log_capture.raw
    assert "5555 1234" not in log_capture.raw

    (server_span,) = _server_spans_for(span_exporter, trace_id)
    assert server_span.status.status_code == StatusCode.ERROR
    exception_events = [e for e in server_span.events if e.name == "exception"]
    assert exception_events
    message = exception_events[0].attributes["exception.message"]
    assert "ivan.petrov@example.com" not in message
    assert REDACTED in message


async def test_domain_error_is_rendered_as_predictable_json(
    app: FastAPI, client: AsyncClient
) -> None:
    async def conflict() -> None:
        raise DomainError(
            status_code=409,
            error_code="ANOMALY_CONFIRMATION_STEP_1",
            message="Value is unusually high.",
            extra={"required_confirmation_level": 1, "consumption_delta": "1130.000"},
        )

    app.add_api_route("/conflict", conflict)

    response = await client.get("/conflict")

    assert response.status_code == 409
    assert response.json() == {
        "error_code": "ANOMALY_CONFIRMATION_STEP_1",
        "message": "Value is unusually high.",
        "required_confirmation_level": 1,
        "consumption_delta": "1130.000",
    }
    assert TRACE_ID_RE.match(response.headers["X-Trace-Id"])


async def test_readiness_returns_503_when_database_is_unreachable(
    app: FastAPI, client: AsyncClient
) -> None:
    unreachable_engine = create_async_engine("postgresql+asyncpg://app:app@127.0.0.1:1/app")

    async def unreachable_session():
        async with AsyncSession(unreachable_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_db_session] = unreachable_session
    try:
        response = await client.get("/health/ready")
    finally:
        app.dependency_overrides.clear()
        await unreachable_engine.dispose()

    assert response.status_code == 503
    assert response.json()["error_code"] == "DATABASE_UNAVAILABLE"
    assert TRACE_ID_RE.match(response.headers["X-Trace-Id"])
