"""OpenTelemetry traces, metrics and logs, structured JSON logging with trace correlation,
and PII scrubbing.

Every log line, every exported span and every exported log record passes through the PII
scrubber, so names, emails, phones, national ID codes, credentials and tokens never leave the
process in telemetry. Metrics carry only route templates, methods and status codes.
"""

import json
import logging
import re
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

import structlog
from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler, LogRecordProcessor
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.core.config import Settings

REDACTED = "[REDACTED]"
TRACE_ID_HEADER = "X-Trace-Id"

# Any attribute/log key containing one of these markers has its value fully redacted.
SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "full_name",
    "fullname",
    "first_name",
    "last_name",
    "email",
    "phone",
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "isikukood",
    "national_id",
    "personal_code",
    "subject_hash",
)

# Free-text patterns masked inside any string value (log messages, URLs, exception texts).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+(?:@|%40)[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+\d{1,3}[\s-]?\d[\d\s-]{5,}\d")
# Estonian personal identification code: gender/century digit, YYMMDD, 4 more digits.
_EE_ID_CODE_RE = re.compile(r"\b[1-6]\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{4}\b")

_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None
_log_export_handler: logging.Handler | None = None


# --- PII scrubbing -----------------------------------------------------------------------------


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def mask_pii_text(text: str) -> str:
    text = _EMAIL_RE.sub(REDACTED, text)
    text = _PHONE_RE.sub(REDACTED, text)
    return _EE_ID_CODE_RE.sub(REDACTED, text)


def scrub_pii(value: Any) -> Any:
    """Return a copy of ``value`` with sensitive keys redacted and PII patterns masked."""
    if isinstance(value, str):
        return mask_pii_text(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED if isinstance(key, str) and is_sensitive_key(key) else scrub_pii(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return type(value)(scrub_pii(item) for item in value)
    return value


def _scrub_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        key: REDACTED if is_sensitive_key(key) else scrub_pii(value)
        for key, value in (attributes or {}).items()
    }


def scrub_span(span: ReadableSpan) -> ReadableSpan:
    """Build a sanitized copy of a finished span (attributes, events and status text)."""
    description = span.status.description
    status = Status(span.status.status_code, mask_pii_text(description) if description else None)
    events = tuple(
        Event(
            name=event.name,
            attributes=_scrub_attributes(event.attributes),
            timestamp=event.timestamp,
        )
        for event in span.events
    )
    return ReadableSpan(
        name=mask_pii_text(span.name),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_scrub_attributes(span.attributes),
        events=events,
        links=span.links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class PIIScrubbingSpanExporter(SpanExporter):
    """Wraps a real exporter so that only scrubbed copies of spans are ever exported."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([scrub_span(span) for span in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


# --- Tracing -----------------------------------------------------------------------------------


def current_trace_id() -> str | None:
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return format(span_context.trace_id, "032x")


def _resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            SERVICE_NAME: settings.OTEL_SERVICE_NAME,
            SERVICE_VERSION: settings.APP_VERSION,
            "deployment.environment": settings.ENVIRONMENT,
        }
    )


def _otlp_endpoint(settings: Settings, signal: str) -> str:
    return f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip('/')}/v1/{signal}"


def configure_tracing(settings: Settings) -> TracerProvider:
    """Create and register the global tracer provider once per process."""
    global _tracer_provider
    if _tracer_provider is not None:
        return _tracer_provider

    provider = TracerProvider(resource=_resource(settings))
    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter(endpoint=_otlp_endpoint(settings, "traces"))
        provider.add_span_processor(BatchSpanProcessor(PIIScrubbingSpanExporter(exporter)))
    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    return provider


def configure_metrics(
    settings: Settings, extra_readers: Sequence[MetricReader] = ()
) -> MeterProvider:
    """Create and register the global meter provider once per process (tests add a reader).

    The FastAPI instrumentation records http.server.* metrics with the route template, method
    and status code only: no ids, query strings or user data.
    """
    global _meter_provider
    if _meter_provider is not None:
        return _meter_provider

    readers = list(extra_readers)
    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        exporter = OTLPMetricExporter(endpoint=_otlp_endpoint(settings, "metrics"))
        readers.append(PeriodicExportingMetricReader(exporter, export_interval_millis=10_000))
    provider = MeterProvider(resource=_resource(settings), metric_readers=readers)
    metrics.set_meter_provider(provider)
    _meter_provider = provider
    return provider


class PIIScrubbingLogHandler(LoggingHandler):
    """Exports log records over OTLP, with the trace context of the current span.

    The body is the already scrubbed JSON line (the same formatter as stdout). Record attributes
    are reduced to the logger name and code location: the stock handler would also export the
    raw exception text and any extra fields, unscrubbed.
    """

    @staticmethod
    def _get_attributes(record: logging.LogRecord) -> dict[str, Any]:
        return {
            "logger.name": record.name,
            "code.file.path": record.pathname,
            "code.function.name": record.funcName,
            "code.line.number": record.lineno,
        }


def build_log_export_handler(
    settings: Settings, processor: LogRecordProcessor | None = None
) -> PIIScrubbingLogHandler | None:
    """An OTLP log handler (None without an endpoint, unless a processor is given)."""
    if processor is None:
        if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
            return None
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        processor = BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=_otlp_endpoint(settings, "logs"))
        )
    provider = LoggerProvider(resource=_resource(settings))
    provider.add_log_record_processor(processor)
    handler = PIIScrubbingLogHandler(logger_provider=provider)
    handler.setFormatter(_formatter(json_logs=True))
    return handler


def configure_log_export(settings: Settings) -> None:
    """Attach the OTLP log handler to the root logger once per process."""
    global _log_export_handler
    if _log_export_handler is None:
        _log_export_handler = build_log_export_handler(settings)
    root_logger = logging.getLogger()
    if _log_export_handler is not None and _log_export_handler not in root_logger.handlers:
        root_logger.addHandler(_log_export_handler)


def shutdown_tracing() -> None:
    """Flush every signal on shutdown (traces, metrics, logs)."""
    if _tracer_provider is not None:
        _tracer_provider.force_flush()
    if _meter_provider is not None:
        _meter_provider.force_flush()
    if _log_export_handler is not None:
        _log_export_handler.flush()


# --- Structured logging ------------------------------------------------------------------------


def add_otel_trace_context(
    logger: Any, method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict.setdefault("trace_id", format(span_context.trace_id, "032x"))
        event_dict.setdefault("span_id", format(span_context.span_id, "016x"))
    return event_dict


def scrub_pii_processor(
    logger: Any, method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    return scrub_pii(event_dict)


_SHARED_PROCESSORS: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
    add_otel_trace_context,
]


def _formatter(json_logs: bool) -> logging.Formatter:
    """The one log pipeline: trace context, formatted tracebacks, PII scrubbing, rendering."""
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            scrub_pii_processor,
            renderer,
        ],
    )


def configure_logging(
    level: str = "INFO", json_logs: bool = True, stream: TextIO | None = None
) -> None:
    """Route structlog and stdlib logging (uvicorn, sqlalchemy) through one JSON pipeline."""
    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(_formatter(json_logs))

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(level.upper())

    # uvicorn installs its own handlers; let its records flow to the root JSON handler instead.
    # Access logs are replaced by the PII-safe "http_request" log of RequestTelemetryMiddleware.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


# --- Request middleware ------------------------------------------------------------------------

request_logger = structlog.get_logger("src.core.telemetry.request")


class RequestTelemetryMiddleware:
    """Adds X-Trace-Id to every response and writes one PII-safe log line per request.

    Unhandled exceptions are recorded on the server span, logged with their traceback and the
    trace_id, and turned into a JSON 500 that still carries X-Trace-Id for correlation.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Request-scoped log context (e.g. user_id bound by core.security) starts empty.
        structlog.contextvars.clear_contextvars()
        trace_id = current_trace_id()
        started_at = time.perf_counter()
        response: dict[str, Any] = {"started": False, "status_code": 500}

        async def send_with_trace_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                response["started"] = True
                response["status_code"] = message["status"]
                if trace_id:
                    MutableHeaders(scope=message).append(TRACE_ID_HEADER, trace_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace_id)
        except Exception as exc:
            span = trace.get_current_span()
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            request_logger.exception(
                "unhandled_exception",
                method=scope["method"],
                path=self._route_path(scope),
                error_type=type(exc).__name__,
            )
            if response["started"]:
                raise
            response["status_code"] = 500
            await self._send_internal_error(send, trace_id)
        finally:
            request_logger.info(
                "http_request",
                method=scope["method"],
                path=self._route_path(scope),
                status_code=response["status_code"],
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )

    @staticmethod
    def _route_path(scope: Scope) -> str:
        # Prefer the route template ("/readings/{reading_id}") over the raw path; never log
        # query strings, which may carry user input.
        route = scope.get("route")
        return getattr(route, "path", None) or scope["path"]

    @staticmethod
    async def _send_internal_error(send: Send, trace_id: str | None) -> None:
        body = json.dumps(
            {
                "error_code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred. Please try again later.",
                "trace_id": trace_id,
            }
        ).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if trace_id:
            headers.append((TRACE_ID_HEADER.lower().encode(), trace_id.encode()))
        await send({"type": "http.response.start", "status": 500, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def setup_telemetry(app: FastAPI, settings: Settings) -> None:
    """Wire traces, metrics and log export into the app. The OTel middleware must wrap
    RequestTelemetryMiddleware so the server span is already active when the trace id is read,
    hence the call order. Exporting is on only when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    tracer_provider = configure_tracing(settings)
    meter_provider = configure_metrics(settings)
    configure_log_export(settings)
    app.add_middleware(RequestTelemetryMiddleware)
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=tracer_provider, meter_provider=meter_provider
    )
