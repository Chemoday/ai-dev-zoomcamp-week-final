"""Durable domain events: the core_domain_events table, the outbox store and the relay.

docs/BACKEND_CONVENTIONS.md 2.B describes the rules; docs/SCHEMA.md section 6 the table.
- `DomainEventStore` stages a row for every event a service adds to its outbox, in the same
  transaction as the change, and after publishing marks it published or schedules a retry.
- `relay_once` publishes rows that are still unpublished (a crash between COMMIT and publish,
  or a failing handler), claiming them without holding locks while handlers run.
- `purge_published_events` applies the retention period.
- `run_relay_loop` drives both in the app lifespan; tests call `relay_once` directly.

Delivery is at least once and unordered, so subscribers must be idempotent.
"""

import asyncio
import uuid
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from opentelemetry import metrics, propagate, trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import ValidationError
from sqlalchemy import DateTime, Index, Integer, String, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database import Base
from src.core.event_bus import DomainEvent, EventBus
from src.core.events_registry import EventRegistry, UnknownEventTypeError, get_event_registry

logger = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)

MAX_PUBLISH_ATTEMPTS = 10
RELAY_GRACE_PERIOD = timedelta(seconds=30)
RELAY_BATCH_SIZE = 50
RELAY_INTERVAL_SECONDS = 5.0
PURGE_INTERVAL_SECONDS = 3600.0
# Delay before the next attempt, by the number of attempts made so far (the last one repeats).
RETRY_BACKOFF = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)

type SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class DomainEventRecord(Base):
    """One stored domain event. Infrastructure owned by src/core, not a domain model."""

    __tablename__ = "core_domain_events"
    __table_args__ = (
        Index(
            "ix_core_domain_events_unpublished",
            "next_attempt_at",
            postgresql_where=text("published_at IS NULL AND failed_at IS NULL"),
        ),
        Index("ix_core_domain_events_building_time", "building_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    building_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    traceparent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)


def retry_delay(attempts: int) -> timedelta:
    """Backoff after `attempts` attempts (at least 1)."""
    return RETRY_BACKOFF[min(max(attempts, 1), len(RETRY_BACKOFF)) - 1]


def _current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Metrics -----------------------------------------------------------------------------------

_meter = metrics.get_meter(__name__)
_oldest_unpublished_age = _meter.create_gauge(
    "domain_events.oldest_unpublished_age",
    unit="s",
    description="Age of the oldest domain event that is still waiting to be published",
)
_failed_events = _meter.create_counter(
    "domain_events.failed",
    description="Domain events given up after the maximum number of publish attempts",
)


# --- The store behind the request outbox --------------------------------------------------------


class DomainEventStore:
    """The durable side of one unit of work's outbox (see EventOutbox)."""

    def __init__(self, session: AsyncSession, registry: EventRegistry | None = None) -> None:
        self._session = session
        self._registry = registry or get_event_registry()

    def add(self, event: DomainEvent) -> None:
        payload = self._registry.encode(event)  # UnknownEventTypeError for unregistered classes
        self._session.add(
            DomainEventRecord(
                id=event.event_id,
                event_type=event.event_name,
                building_id=getattr(event, "building_id", None),
                payload=payload,
                traceparent=_current_traceparent(),
                occurred_at=event.occurred_at,
                next_attempt_at=event.occurred_at,
            )
        )

    async def record_results(
        self, published: Sequence[DomainEvent], failed: Sequence[DomainEvent]
    ) -> None:
        now = _utcnow()
        try:
            if published:
                await self._session.execute(
                    update(DomainEventRecord)
                    .where(
                        DomainEventRecord.id.in_([e.event_id for e in published]),
                        DomainEventRecord.published_at.is_(None),
                    )
                    .values(published_at=now)
                )
            if failed:
                await self._session.execute(
                    update(DomainEventRecord)
                    .where(DomainEventRecord.id.in_([e.event_id for e in failed]))
                    .values(
                        publish_attempts=DomainEventRecord.publish_attempts + 1,
                        next_attempt_at=now + retry_delay(1),
                        last_error="HANDLER_FAILED",
                    )
                )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise


# --- Relay ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Claimed:
    id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    occurred_at: datetime
    traceparent: str | None
    attempts: int  # including this one


def _log_gave_up(event_type: str, event_id: uuid.UUID, last_error: str | None) -> None:
    _failed_events.add(1, {"event.name": event_type})
    logger.error(
        "domain_event_gave_up", event_name=event_type, event_id=str(event_id), last_error=last_error
    )


async def _claim(session: AsyncSession, now: datetime, batch: int) -> list[_Claimed]:
    """Take due rows and move their next attempt forward, then commit: no lock is held while
    handlers run, and no other worker takes the same rows meanwhile."""
    rows = (
        await session.scalars(
            select(DomainEventRecord)
            .where(
                DomainEventRecord.published_at.is_(None),
                DomainEventRecord.failed_at.is_(None),
                DomainEventRecord.occurred_at < now - RELAY_GRACE_PERIOD,
                DomainEventRecord.next_attempt_at <= now,
            )
            .order_by(DomainEventRecord.next_attempt_at)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
    ).all()
    claimed: list[_Claimed] = []
    for row in rows:
        # Normally a row is given up right after its last failed attempt (relay_once); this
        # catches rows that reached the limit some other way.
        if row.publish_attempts >= MAX_PUBLISH_ATTEMPTS:
            row.failed_at = now
            row.last_error = row.last_error or "MAX_ATTEMPTS"
            _log_gave_up(row.event_type, row.id, row.last_error)
            continue
        row.publish_attempts += 1
        row.next_attempt_at = now + retry_delay(row.publish_attempts)
        claimed.append(
            _Claimed(
                row.id,
                row.event_type,
                dict(row.payload),
                row.occurred_at,
                row.traceparent,
                row.publish_attempts,
            )
        )
    await session.commit()
    return claimed


async def _publish_claimed(item: _Claimed, bus: EventBus, registry: EventRegistry) -> str | None:
    """Publish one claimed event; return None on success, else a machine error code."""
    try:
        event = registry.decode(
            item.event_type, item.payload, event_id=item.id, occurred_at=item.occurred_at
        )
    except UnknownEventTypeError:
        return "UNKNOWN_EVENT_TYPE"
    except ValidationError:
        return "PAYLOAD_INVALID"

    parent = TraceContextTextMapPropagator().extract(
        {"traceparent": item.traceparent} if item.traceparent else {}
    )
    with tracer.start_as_current_span(
        f"domain_event_relay {item.event_type}",
        context=parent,
        attributes={"event.name": item.event_type, "event.id": str(item.id)},
    ) as span:
        try:
            failed_handlers = await bus.publish(event)
        except Exception:
            logger.exception(
                "domain_event_publish_failed", event_name=item.event_type, event_id=str(item.id)
            )
            failed_handlers = ["<bus>"]
        if failed_handlers:
            span.set_status(trace.Status(trace.StatusCode.ERROR, "HANDLER_FAILED"))
            return "HANDLER_FAILED"
    return None


async def relay_once(
    session_factory: SessionFactory,
    bus: EventBus,
    *,
    registry: EventRegistry | None = None,
    now: datetime | None = None,
    batch: int = RELAY_BATCH_SIZE,
) -> int:
    """One relay pass: publish due unpublished events. Returns how many were published."""
    registry = registry or get_event_registry()
    now = now or _utcnow()
    async with session_factory() as session:
        claimed = await _claim(session, now, batch)
    if not claimed:
        return 0

    published = 0
    for item in claimed:
        error = await _publish_claimed(item, bus, registry)
        # Marked right away, one event at a time, so a slow batch never outlives its claim.
        values: dict[str, Any]
        if error is None:
            published += 1
            values = {"published_at": _utcnow(), "last_error": None}
        else:
            logger.warning(
                "domain_event_relay_failed",
                event_name=item.event_type,
                event_id=str(item.id),
                error=error,
                attempt=item.attempts,
            )
            values = {"last_error": error}
            if item.attempts >= MAX_PUBLISH_ATTEMPTS:
                values["failed_at"] = _utcnow()
                _log_gave_up(item.event_type, item.id, error)
        async with session_factory() as session:
            await session.execute(
                update(DomainEventRecord)
                .where(DomainEventRecord.id == item.id, DomainEventRecord.published_at.is_(None))
                .values(**values)
            )
            await session.commit()
    return published


async def purge_published_events(
    session_factory: SessionFactory, retention_days: int, *, now: datetime | None = None
) -> int:
    """Delete published events older than the retention period (0 keeps them forever)."""
    if retention_days <= 0:
        return 0
    cutoff = (now or _utcnow()) - timedelta(days=retention_days)
    async with session_factory() as session:
        result = await session.execute(
            delete(DomainEventRecord).where(
                DomainEventRecord.published_at.is_not(None),
                DomainEventRecord.occurred_at < cutoff,
            )
        )
        await session.commit()
    return result.rowcount or 0


async def record_backlog_metric(session_factory: SessionFactory) -> None:
    async with session_factory() as session:
        oldest = await session.scalar(
            select(func.min(DomainEventRecord.occurred_at)).where(
                DomainEventRecord.published_at.is_(None), DomainEventRecord.failed_at.is_(None)
            )
        )
    age = (_utcnow() - oldest).total_seconds() if oldest is not None else 0.0
    _oldest_unpublished_age.set(max(age, 0.0))


async def run_relay_loop(
    session_factory: SessionFactory, bus: EventBus, *, retention_days: int
) -> None:
    """Run until cancelled: a relay pass at startup, then every few seconds; hourly retention."""
    loop = asyncio.get_running_loop()
    next_purge = loop.time()
    while True:
        try:
            await relay_once(session_factory, bus)
            await record_backlog_metric(session_factory)
            if loop.time() >= next_purge:
                await purge_published_events(session_factory, retention_days)
                next_purge = loop.time() + PURGE_INTERVAL_SECONDS
        except Exception:  # CancelledError is not an Exception, so cancelling still stops the loop
            logger.exception("domain_event_relay_pass_failed")
        await asyncio.sleep(RELAY_INTERVAL_SECONDS)
