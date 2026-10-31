"""In-memory async publish/subscribe bus for decoupling domain side effects between modules."""

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from fastapi import Request
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

    async def publish(self, event: DomainEvent) -> list[str]:
        """Run every handler of the event; return the names of the handlers that failed."""
        ...


class EventStore(Protocol):
    """The durable part of an outbox (src/core/domain_events.py): rows in core_domain_events."""

    def add(self, event: DomainEvent) -> None:
        """Stage the event's row in the unit of work (no I/O; committed with the change)."""
        ...

    async def record_results(
        self, published: Sequence[DomainEvent], failed: Sequence[DomainEvent]
    ) -> None:
        """After publishing: mark rows published, or schedule a retry for the failed ones."""
        ...


class InMemoryEventBus:
    """Dispatches events to async handlers registered for the event's class or any base class.

    Handlers run concurrently. A failing handler is logged and recorded on its span, but never
    breaks the publisher or the other handlers: side effects must not fail the business action.
    `publish` reports the failed handlers, so a durable outbox can retry the event.
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

    async def publish(self, event: DomainEvent) -> list[str]:
        handlers = self.handlers_for(event)
        logger.info(
            "domain_event_published",
            event_name=event.event_name,
            event_id=str(event.event_id),
            handler_count=len(handlers),
        )
        if not handlers:
            return []
        results = await asyncio.gather(*(self._run_handler(handler, event) for handler in handlers))
        return [name for name, ok in results if not ok]

    async def _run_handler(
        self, handler: EventHandler[Any], event: DomainEvent
    ) -> tuple[str, bool]:
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
                return handler_name, False
        return handler_name, True


class EventOutbox:
    """Domain events of one unit of work (one request), held back until it commits.

    Services add events while they work. The owner of the transaction publishes them after
    COMMIT, or discards them on rollback (src/core/database.py, `transaction`). So a
    notification never goes out for a change that was rolled back, and slow handlers never
    run while the transaction still holds row locks.

    With a store, every added event is also staged as a core_domain_events row in the same
    unit of work, and after publishing the store marks it published or schedules a retry, so a
    crash or a failing handler never loses it (docs/BACKEND_CONVENTIONS.md 2.B).
    """

    def __init__(self, bus: EventBus, store: EventStore | None = None) -> None:
        self._bus = bus
        self._store = store
        self._pending: list[DomainEvent] = []

    @property
    def pending(self) -> tuple[DomainEvent, ...]:
        return tuple(self._pending)

    def add(self, event: DomainEvent) -> None:
        if self._store is not None:
            self._store.add(event)  # rejects unregistered events before anything is pending
        self._pending.append(event)

    def discard(self) -> None:
        self._pending.clear()

    async def publish_pending(self) -> None:
        """Publish in the order added. Never raises: the change is already committed, so a
        failure here must not turn a successful request into an error."""
        events, self._pending = self._pending, []
        published: list[DomainEvent] = []
        failed: list[DomainEvent] = []
        for event in events:
            try:
                failed_handlers = await self._bus.publish(event)
            except Exception:
                logger.exception(
                    "domain_event_publish_failed",
                    event_name=event.event_name,
                    event_id=str(event.event_id),
                )
                failed.append(event)
                continue
            (failed if failed_handlers else published).append(event)
        if self._store is None or not events:
            return
        try:
            await self._store.record_results(published, failed)
        except Exception:
            # The relay publishes the rows again later; the request stays successful.
            logger.exception("domain_event_mark_failed", event_count=len(events))


def get_event_bus(request: Request) -> EventBus:
    """FastAPI dependency: the event bus of this app (`app.state.event_bus`, set in create_app).

    One bus per app instance, so handlers subscribed by one app (or test) never leak into another.
    """
    return request.app.state.event_bus
