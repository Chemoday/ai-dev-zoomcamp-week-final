"""Exported logs and metrics (OTLP) carry no PII; logs keep the trace context (Task 8)."""

import json
import logging

import structlog
from httpx import AsyncClient
from opentelemetry import trace
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from src.core.config import get_settings
from src.core.telemetry import build_log_export_handler

PII = ("Ivan Petrov", "ivan.petrov@example.com", "+372 5555 1234", "38501010000", "s3cr3t-token")


def test_exported_log_records_are_scrubbed_and_keep_the_trace_id(log_capture) -> None:
    exporter = InMemoryLogRecordExporter()
    handler = build_log_export_handler(get_settings(), SimpleLogRecordProcessor(exporter))
    root = logging.getLogger()
    root.addHandler(handler)
    tracer = trace.get_tracer(__name__)
    try:
        with tracer.start_as_current_span("request") as span:
            structlog.get_logger("test.export").info(
                "user_contacted",
                full_name="Ivan Petrov",
                note="mail ivan.petrov@example.com or call +372 5555 1234, code 38501010000",
                api_token="s3cr3t-token",
            )
            try:
                raise ValueError("lookup failed for ivan.petrov@example.com")
            except ValueError:
                structlog.get_logger("test.export").exception("lookup_failed")
            trace_id = span.get_span_context().trace_id
    finally:
        root.removeHandler(handler)

    records = [item.log_record for item in exporter.get_finished_logs()]
    assert [json.loads(r.body)["event"] for r in records] == ["user_contacted", "lookup_failed"]
    exported = " ".join(f"{r.body} {dict(r.attributes)}" for r in records)
    for fragment in PII:
        assert fragment not in exported, f"{fragment!r} leaked"
    assert all(r.trace_id == trace_id for r in records), "logs link to their trace"
    assert "ValueError" in records[1].body, "the scrubbed traceback stays in the body"


def _metric_points(reader: InMemoryMetricReader) -> list[tuple[str, dict]]:
    data = reader.get_metrics_data()
    return [
        (metric.name, dict(point.attributes))
        for resource in (data.resource_metrics if data else [])
        for scope in resource.scope_metrics
        for metric in scope.metrics
        for point in metric.data.data_points
    ]


async def test_exported_metrics_carry_route_templates_not_user_data(
    client: AsyncClient, metric_reader: InMemoryMetricReader
) -> None:
    await client.get("/health")
    await client.get("/api/v1/meters/readings/ivan.petrov@example.com/history")

    points = _metric_points(metric_reader)
    names = {name for name, _ in points}
    assert any(name.startswith("http.server") for name in names), names
    exported = json.dumps([attributes for _, attributes in points], default=str)
    assert "ivan.petrov" not in exported
    assert "@example.com" not in exported
