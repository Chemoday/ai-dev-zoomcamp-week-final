from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from src.core.telemetry import (
    REDACTED,
    PIIScrubbingSpanExporter,
    add_otel_trace_context,
    mask_pii_text,
    scrub_pii,
)


def test_scrub_pii_redacts_sensitive_keys_at_any_depth() -> None:
    payload = {
        "user_id": "5b1f0c2e-8d6a-4a57-9c1e-2f3d4b5a6c7d",
        "full_name": "Ivan Petrov",
        "author": {"email": "ivan@example.com", "phone": "+372 5555 1234", "apartment": "14"},
        "identities": [{"provider": "ee_smart_id", "subject_hash": "abc123"}],
        "Authorization": "Bearer abc.def.ghi",
        "secret_data": "$2b$12$hash",
    }

    scrubbed = scrub_pii(payload)

    assert scrubbed == {
        "user_id": "5b1f0c2e-8d6a-4a57-9c1e-2f3d4b5a6c7d",
        "full_name": REDACTED,
        "author": {"email": REDACTED, "phone": REDACTED, "apartment": "14"},
        "identities": [{"provider": "ee_smart_id", "subject_hash": REDACTED}],
        "Authorization": REDACTED,
        "secret_data": REDACTED,
    }
    assert payload["full_name"] == "Ivan Petrov", "input must not be mutated"


def test_mask_pii_text_masks_emails_phones_and_estonian_id_codes() -> None:
    text = (
        "user ivan.petrov@example.com (url-encoded: ivan%40example.com), "
        "phone +372 5555 1234, isikukood 38501011234"
    )

    masked = mask_pii_text(text)

    assert "ivan.petrov@example.com" not in masked
    assert "ivan%40example.com" not in masked
    assert "5555 1234" not in masked
    assert "38501011234" not in masked
    assert masked.count(REDACTED) == 4


def test_mask_pii_text_keeps_meter_values_ids_and_paths() -> None:
    text = (
        "POST /api/v1/meters/readings value=1254.000 delta=1130.000 "
        "trace=4bf92f3577b34da6a3ce929d0e0e4736 period=2026-10"
    )

    assert mask_pii_text(text) == text


def test_add_otel_trace_context_injects_ids_only_inside_a_span() -> None:
    assert add_otel_trace_context(None, "info", {"event": "outside"}) == {"event": "outside"}

    tracer = TracerProvider().get_tracer(__name__)
    with tracer.start_as_current_span("unit-test") as span:
        event_dict = add_otel_trace_context(None, "info", {"event": "inside"})
        expected_trace_id = format(span.get_span_context().trace_id, "032x")
        expected_span_id = format(span.get_span_context().span_id, "016x")

    assert event_dict["trace_id"] == expected_trace_id
    assert event_dict["span_id"] == expected_span_id


def test_span_exporter_never_exports_pii_in_attributes_events_or_status() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(PIIScrubbingSpanExporter(exporter)))
    tracer = provider.get_tracer(__name__)

    try:
        with tracer.start_as_current_span(
            "submit reading",
            attributes={
                "user.email": "ivan@example.com",
                "url.query": "email=ivan%40example.com&period=2026-10",
                "meter.value": "1254.000",
            },
        ) as span:
            span.add_event("lookup", {"user.full_name": "Ivan Petrov", "apartment": "14"})
            raise ValueError("duplicate key (email)=(ivan@example.com)")
    except ValueError:
        pass

    (exported,) = exporter.get_finished_spans()
    assert exported.attributes["user.email"] == REDACTED
    assert exported.attributes["url.query"] == f"email={REDACTED}&period=2026-10"
    assert exported.attributes["meter.value"] == "1254.000"
    lookup = next(e for e in exported.events if e.name == "lookup")
    assert lookup.attributes == {"user.full_name": REDACTED, "apartment": "14"}
    exception = next(e for e in exported.events if e.name == "exception")
    assert "ivan@example.com" not in exception.attributes["exception.message"]
    assert "ivan@example.com" not in (exported.status.description or "")
    assert exported.context.trace_id == span.get_span_context().trace_id


def test_mask_pii_text_masks_telegram_bot_tokens() -> None:
    token = "123456789:AAFakeTokenForTelemetryTests_012345678"
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    assert token not in mask_pii_text(f"POST {url} failed")


def test_httpx_request_logs_are_silenced(app) -> None:
    import logging

    # Bot API URLs carry the token, and httpx logs every URL at INFO.
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_push_endpoints_and_keys_are_redacted() -> None:
    scrubbed = scrub_pii({"endpoint": "https://fcm.googleapis.com/fcm/send/x", "p256dh": "BC"})

    assert scrubbed == {"endpoint": REDACTED, "p256dh": REDACTED}
