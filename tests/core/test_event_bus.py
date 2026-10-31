import asyncio
from dataclasses import dataclass

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from src.core.event_bus import DomainEvent, InMemoryEventBus, get_event_bus
from src.main import create_app


@dataclass(frozen=True, kw_only=True)
class ReadingSubmitted(DomainEvent):
    meter_id: str
    value: str


@dataclass(frozen=True, kw_only=True)
class AlertPublished(DomainEvent):
    post_id: str


async def test_publish_runs_subscribed_async_handler_with_the_event() -> None:
    bus = InMemoryEventBus()
    received: list[ReadingSubmitted] = []

    async def on_reading(event: ReadingSubmitted) -> None:
        await asyncio.sleep(0)
        received.append(event)

    bus.subscribe(ReadingSubmitted, on_reading)
    event = ReadingSubmitted(meter_id="m-1", value="1254.000")

    await bus.publish(event)

    assert received == [event]
    assert received[0].event_name == "ReadingSubmitted"
    assert received[0].event_id is not None
    assert received[0].occurred_at.tzinfo is not None


async def test_publish_runs_all_handlers_concurrently() -> None:
    bus = InMemoryEventBus()
    both_started = asyncio.Event()
    started: list[str] = []

    async def make_handler(name: str):
        async def handler(event: ReadingSubmitted) -> None:
            started.append(name)
            if len(started) == 2:
                both_started.set()
            # Deadlocks (and times out) if handlers were awaited one after another.
            await asyncio.wait_for(both_started.wait(), timeout=1)

        return handler

    bus.subscribe(ReadingSubmitted, await make_handler("audit"))
    bus.subscribe(ReadingSubmitted, await make_handler("notify"))

    await bus.publish(ReadingSubmitted(meter_id="m-1", value="1.000"))

    assert sorted(started) == ["audit", "notify"]


async def test_handlers_only_receive_their_event_type() -> None:
    bus = InMemoryEventBus()
    readings: list[DomainEvent] = []
    alerts: list[DomainEvent] = []

    async def on_reading(event: ReadingSubmitted) -> None:
        readings.append(event)

    async def on_alert(event: AlertPublished) -> None:
        alerts.append(event)

    bus.subscribe(ReadingSubmitted, on_reading)
    bus.subscribe(AlertPublished, on_alert)

    alert = AlertPublished(post_id="p-1")
    await bus.publish(alert)

    assert readings == []
    assert alerts == [alert]


async def test_base_class_subscription_receives_every_domain_event() -> None:
    bus = InMemoryEventBus()
    seen: list[str] = []

    async def audit_everything(event: DomainEvent) -> None:
        seen.append(event.event_name)

    bus.subscribe(DomainEvent, audit_everything)

    await bus.publish(ReadingSubmitted(meter_id="m-1", value="1.000"))
    await bus.publish(AlertPublished(post_id="p-1"))

    assert seen == ["ReadingSubmitted", "AlertPublished"]


async def test_failing_handler_does_not_break_publisher_or_other_handlers(
    span_exporter: InMemorySpanExporter,
) -> None:
    bus = InMemoryEventBus()
    delivered: list[DomainEvent] = []

    async def broken_handler(event: AlertPublished) -> None:
        raise ConnectionError("telegram is down")

    async def healthy_handler(event: AlertPublished) -> None:
        delivered.append(event)

    bus.subscribe(AlertPublished, broken_handler)
    bus.subscribe(AlertPublished, healthy_handler)
    event = AlertPublished(post_id="p-1")

    await bus.publish(event)

    assert delivered == [event]
    failed_spans = [
        span
        for span in span_exporter.get_finished_spans()
        if span.attributes.get("event.handler", "").endswith("broken_handler")
    ]
    assert len(failed_spans) == 1
    assert failed_spans[0].status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in failed_spans[0].events)


async def test_duplicate_subscription_is_ignored_and_unsubscribe_stops_delivery() -> None:
    bus = InMemoryEventBus()
    calls: list[DomainEvent] = []

    async def handler(event: ReadingSubmitted) -> None:
        calls.append(event)

    bus.subscribe(ReadingSubmitted, handler)
    bus.subscribe(ReadingSubmitted, handler)
    await bus.publish(ReadingSubmitted(meter_id="m-1", value="1.000"))
    assert len(calls) == 1

    bus.unsubscribe(ReadingSubmitted, handler)
    await bus.publish(ReadingSubmitted(meter_id="m-1", value="2.000"))
    assert len(calls) == 1


async def test_publish_without_handlers_is_a_no_op() -> None:
    await InMemoryEventBus().publish(AlertPublished(post_id="p-1"))


def test_every_app_has_its_own_event_bus() -> None:
    first, second = create_app(), create_app()

    assert isinstance(first.state.event_bus, InMemoryEventBus)
    assert first.state.event_bus is not second.state.event_bus

    class FakeRequest:
        app = first

    assert get_event_bus(FakeRequest()) is first.state.event_bus  # type: ignore[arg-type]


async def test_publish_reports_the_failed_handlers() -> None:
    bus = InMemoryEventBus()

    async def ok(event: AlertPublished) -> None:
        return None

    async def broken(event: AlertPublished) -> None:
        raise RuntimeError("down")

    bus.subscribe(AlertPublished, ok)
    bus.subscribe(AlertPublished, broken)

    failed = await bus.publish(AlertPublished(post_id="p-1"))

    assert len(failed) == 1 and "broken" in failed[0]
