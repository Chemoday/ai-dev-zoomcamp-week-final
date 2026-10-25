"""In-memory async publish/subscribe bus for decoupling domain side effects between modules."""

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from opentelemetry import trace

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Base class for all domain events. Subclasses add their own (PII-free) payload fields."""

    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def event_name(self) -> str:
        return type(self).__name__


type EventHandler[E: DomainEvent] = Callable[[E], Awaitable[None]]


class EventBus(Protocol):
    def subscribe[E: DomainEvent](self, event_type: type[E], handler: EventHandler[E]) -> None: ...

    def unsubscribe[E: DomainEvent](
        self, event_type: type[E], handler: EventHandler[E]
    ) -> None: ...

    async def publish(self, event: DomainEvent) -> None: ...


class InMemoryEventBus:
    """Dispatches events to async handlers registered for the event's class or any base class.

    Handlers run concurrently. A failing handler is logged and recorded on its span, but never
    breaks the publisher or the other handlers: side effects must not fail the business action.
    """

    def __init__(self) -> None:
        self._handlers: defaultdict[type[DomainEvent], list[EventHandler[Any]]] = defaultdict(list)

    def subscribe[E: DomainEvent](self, event_type: type[E], handler: EventHandler[E]) -> None:
        handlers = self._handlers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    def unsubscribe[E: DomainEvent](self, event_type: type[E], handler: EventHandler[E]) -> None:
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def handlers_for(self, event: DomainEvent) -> list[EventHandler[Any]]:
        return [
            handler
            for event_type, handlers in self._handlers.items()
            if isinstance(event, event_type)
            for handler in handlers
        ]

    def clear(self) -> None:
        self._handlers.clear()

    async def publish(self, event: DomainEvent) -> None:
        handlers = self.handlers_for(event)
        logger.info(
            "domain_event_published",
            event_name=event.event_name,
            event_id=str(event.event_id),
            handler_count=len(handlers),
        )
        if handlers:
            await asyncio.gather(*(self._run_handler(handler, event) for handler in handlers))

    async def _run_handler(self, handler: EventHandler[Any], event: DomainEvent) -> None:
        handler_name = getattr(handler, "__qualname__", repr(handler))
        with tracer.start_as_current_span(
            f"event_handler {event.event_name}",
            attributes={
                "event.name": event.event_name,
                "event.id": str(event.event_id),
                "event.handler": handler_name,
            },
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            try:
                await handler(event)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
                logger.exception(
                    "domain_event_handler_failed",
                    event_name=event.event_name,
                    event_id=str(event.event_id),
                    handler=handler_name,
                )


class EventOutbox:
    """Domain events of one unit of work (one request), held back until it commits.

    Services add events while they work. The owner of the transaction publishes them after
    COMMIT, or discards them on rollback (src/core/database.py, `transaction`). So a
    notification never goes out for a change that was rolled back, and slow handlers never
    run while the transaction still holds row locks.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._pending: list[DomainEvent] = []

    @property
    def pending(self) -> tuple[DomainEvent, ...]:
        return tuple(self._pending)

    def add(self, event: DomainEvent) -> None:
        self._pending.append(event)

    def discard(self) -> None:
        self._pending.clear()

    async def publish_pending(self) -> None:
        """Publish in the order added. Never raises: the change is already committed, so a
        failure here must not turn a successful request into an error."""
        events, self._pending = self._pending, []
        for event in events:
            try:
                await self._bus.publish(event)
            except Exception:
                logger.exception(
                    "domain_event_publish_failed",
                    event_name=event.event_name,
                    event_id=str(event.event_id),
                )


event_bus = InMemoryEventBus()


def get_event_bus() -> EventBus:
    """FastAPI dependency returning the process-wide event bus."""
    return event_bus
