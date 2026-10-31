"""Durable domain events: stored with the change, marked after publishing, relayed after a crash
or a failing handler, given up after too many attempts, purged after the retention period
(docs/BACKEND_CONVENTIONS.md 2.B, docs/SCHEMA.md section 6)."""

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession, get_event_outbox
from src.core.domain_events import (
    MAX_PUBLISH_ATTEMPTS,
    DomainEventRecord,
    DomainEventStore,
    purge_published_events,
    relay_once,
)
from src.core.event_bus import DomainEvent, EventOutbox, InMemoryEventBus, event_bus
from src.core.events_registry import EventRegistry, UnknownEventTypeError, get_event_registry
from src.modules.communication.events import EmergencyAlertPublished, EmergencyAlertResolved
from src.modules.communication.schemas import PostCategory, TargetScope
from src.modules.meters.events import MeterReadingSubmitted, ReadingPeriodStatusChanged
from src.modules.meters.schemas import PeriodStatus, ReadingSource
from src.modules.property.models import Building


@dataclass(frozen=True, kw_only=True)
class SomethingStored(DomainEvent):
    name: str
    building_id: uuid.UUID | None = None


get_event_registry().register(SomethingStored)

TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def _now() -> datetime:
    return datetime.now(UTC)


# --- Registry and codec (no database) -------------------------------------------------------------


def _samples() -> dict[str, DomainEvent]:
    building, post, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    return {
        "EmergencyAlertPublished": EmergencyAlertPublished(
            post_id=post,
            building_id=building,
            category=PostCategory.WATER_SHUTOFF,
            target_scope=TargetScope.APARTMENT_LIST,
            target_entrance=None,
            target_apartment_ids=(uuid.uuid4(), uuid.uuid4()),
            published_by_user_id=user,
        ),
        "EmergencyAlertResolved": EmergencyAlertResolved(
            post_id=post, building_id=building, resolved_by_user_id=user
        ),
        "MeterReadingSubmitted": MeterReadingSubmitted(
            reading_id=uuid.uuid4(),
            building_id=building,
            meter_id=uuid.uuid4(),
            apartment_id=None,
            period_code="2026-10",
            value=Decimal("1254.100"),
            consumption_delta=Decimal("4.200"),
            source=ReadingSource.RESIDENT,
            is_anomaly_confirmed=False,
            submitted_by_user_id=None,
        ),
        "ReadingPeriodStatusChanged": ReadingPeriodStatusChanged(
            period_id=uuid.uuid4(),
            building_id=building,
            period_code="2026-10",
            old_status=PeriodStatus.OPEN_FOR_RESIDENTS,
            new_status=PeriodStatus.REVIEW_BY_BOARD,
            changed_by_user_id=user,
        ),
    }


def _app_event_classes() -> set[type[DomainEvent]]:
    """Every DomainEvent subclass defined in the application (not in tests)."""
    import src.main  # noqa: F401  (imports every module, so all event classes exist)

    found: set[type[DomainEvent]] = set()
    pending = list(DomainEvent.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if cls.__module__.startswith("src."):
            found.add(cls)
    return found


def test_every_application_event_class_is_registered() -> None:
    # An event class outside _MODULE_EVENT_MODULES would fail only at runtime, in outbox.add().
    assert _app_event_classes() <= get_event_registry().classes()


def test_every_module_event_is_registered_and_round_trips_through_json() -> None:
    registry = EventRegistry()
    for event in _samples().values():
        registry.register(type(event))
    # A new module event must be added to _samples(), so its stored form is tested too.
    assert {cls.__name__ for cls in _app_event_classes()} == set(_samples())

    for event in _samples().values():
        stored = json.loads(json.dumps(registry.encode(event)))
        rebuilt = registry.decode(
            event.event_name, stored, event_id=event.event_id, occurred_at=event.occurred_at
        )
        assert rebuilt == event
        assert type(rebuilt) is type(event)


def test_the_payload_holds_only_the_event_fields_and_no_text() -> None:
    payload = get_event_registry().encode(_samples()["EmergencyAlertPublished"])

    assert set(payload) == {
        "post_id",
        "building_id",
        "category",
        "target_scope",
        "target_entrance",
        "target_apartment_ids",
        "published_by_user_id",
    }, "no titles, bodies or names; event_id and occurred_at live in their own columns"


def test_a_second_class_cannot_take_a_registered_name() -> None:
    registry = EventRegistry()
    registry.register(SomethingStored)
    registry.register(SomethingStored)  # the same class again is fine

    @dataclass(frozen=True, kw_only=True)
    class SomethingStored2(DomainEvent):
        pass

    with pytest.raises(ValueError, match="already registered"):
        registry.register(SomethingStored2, "SomethingStored")
    assert "SomethingStored2" not in registry.names(), "a conflict changes nothing"


def test_an_alias_rebuilds_events_stored_under_an_old_name() -> None:
    registry = EventRegistry()
    registry.register(SomethingStored, "SomethingSaved")
    event = SomethingStored(name="x")

    rebuilt = registry.decode(
        "SomethingSaved", {"name": "x"}, event_id=event.event_id, occurred_at=event.occurred_at
    )

    assert rebuilt == event


class _StagingSession:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


def test_an_unregistered_event_is_refused_before_anything_is_pending() -> None:
    @dataclass(frozen=True, kw_only=True)
    class NotRegistered(DomainEvent):
        pass

    session = _StagingSession()
    outbox = EventOutbox(InMemoryEventBus(), DomainEventStore(session))  # type: ignore[arg-type]

    with pytest.raises(UnknownEventTypeError):
        outbox.add(NotRegistered())

    assert outbox.pending == ()
    assert session.added == []


# --- Through the real request session -------------------------------------------------------------


@pytest.fixture
def events_app(db_app: FastAPI) -> FastAPI:
    async def endpoint(
        fail: bool, session: DbSession, outbox: EventOutbox = Depends(get_event_outbox)
    ) -> dict[str, str]:
        building = Building(name="Events test", address="Somewhere 1")
        session.add(building)
        await session.flush()
        event = SomethingStored(name="building created", building_id=building.id)
        outbox.add(event)
        if fail:
            from src.core.exceptions import DomainError

            raise DomainError(status_code=409, error_code="CONFLICT", message="Rolled back.")
        return {"event_id": str(event.event_id)}

    db_app.add_api_route("/test/events", endpoint, methods=["POST"])
    return db_app


async def _record(session: AsyncSession, event_id: uuid.UUID) -> DomainEventRecord | None:
    session.expunge_all()
    return await session.get(DomainEventRecord, event_id)


async def _records_of(session: AsyncSession, event_type: str) -> list[DomainEventRecord]:
    session.expunge_all()
    rows = await session.scalars(
        select(DomainEventRecord).where(DomainEventRecord.event_type == event_type)
    )
    return list(rows)


async def test_a_committed_event_is_stored_and_marked_published(
    events_app: FastAPI, api_client: AsyncClient, db_session: AsyncSession, published_events
) -> None:
    response = await api_client.post("/test/events", params={"fail": False})

    assert response.status_code == 200
    row = await _record(db_session, uuid.UUID(response.json()["event_id"]))
    assert row is not None
    assert row.event_type == "SomethingStored"
    assert row.payload["name"] == "building created"
    assert row.building_id is not None
    assert row.published_at is not None
    assert row.publish_attempts == 0
    assert row.traceparent and row.traceparent.startswith("00-"), "the request trace is kept"
    assert [e.name for e in published_events] == ["building created"]


async def test_a_rolled_back_request_stores_no_event(
    events_app: FastAPI, api_client: AsyncClient, db_session: AsyncSession, published_events
) -> None:
    response = await api_client.post("/test/events", params={"fail": True})

    assert response.status_code == 409
    assert await _records_of(db_session, "SomethingStored") == []
    assert published_events == []


async def test_a_failing_handler_leaves_the_event_for_a_retry(
    events_app: FastAPI, api_client: AsyncClient, db_session: AsyncSession
) -> None:
    async def broken(event: SomethingStored) -> None:
        raise RuntimeError("subscriber is down")

    event_bus.subscribe(SomethingStored, broken)
    try:
        response = await api_client.post("/test/events", params={"fail": False})
    finally:
        event_bus.unsubscribe(SomethingStored, broken)

    assert response.status_code == 200, "the committed request stays successful"
    row = await _record(db_session, uuid.UUID(response.json()["event_id"]))
    assert row is not None
    assert row.published_at is None
    assert row.publish_attempts == 1
    assert row.last_error == "HANDLER_FAILED"
    assert row.next_attempt_at > row.occurred_at


async def test_a_failing_mark_never_fails_the_committed_request(
    events_app: FastAPI,
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    log_capture,
) -> None:
    async def broken(self, published, failed) -> None:
        raise ConnectionError("database went away after the commit")

    monkeypatch.setattr(DomainEventStore, "record_results", broken)
    response = await api_client.post("/test/events", params={"fail": False})

    assert response.status_code == 200
    assert any(r["event"] == "domain_event_mark_failed" for r in log_capture.records)
    row = await _record(db_session, uuid.UUID(response.json()["event_id"]))
    assert row is not None and row.published_at is None, "left for the relay"


# --- Relay ----------------------------------------------------------------------------------------


@pytest.fixture
def session_factory(db_session: AsyncSession):
    """The relay's session factory, bound to the test transaction."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        yield db_session

    return factory


async def _store_unpublished(
    session: AsyncSession,
    *,
    age: timedelta,
    event_type: str = "SomethingStored",
    payload: dict[str, Any] | None = None,
    attempts: int = 0,
    traceparent: str | None = TRACEPARENT,
) -> uuid.UUID:
    occurred_at = _now() - age
    event_id = uuid.uuid4()
    session.add(
        DomainEventRecord(
            id=event_id,
            event_type=event_type,
            payload=payload
            if payload is not None
            else {"name": "left behind", "building_id": None},
            traceparent=traceparent,
            occurred_at=occurred_at,
            next_attempt_at=occurred_at,
            publish_attempts=attempts,
        )
    )
    await session.commit()
    return event_id


async def test_the_relay_publishes_an_event_a_crash_left_behind(
    db_session: AsyncSession, session_factory, published_events, span_exporter
) -> None:
    event_id = await _store_unpublished(db_session, age=timedelta(minutes=2))

    published = await relay_once(session_factory, event_bus)

    assert published == 1
    assert [(e.event_id, e.name) for e in published_events] == [(event_id, "left behind")]
    row = await _record(db_session, event_id)
    assert row is not None and row.published_at is not None and row.publish_attempts == 1
    assert row.last_error is None
    relay_spans = [
        s for s in span_exporter.get_finished_spans() if s.name.startswith("domain_event_relay")
    ]
    assert relay_spans, "the relay publishes inside a span"
    assert format(relay_spans[0].context.trace_id, "032x") == TRACEPARENT.split("-")[1], (
        "the relay continues the trace of the request that caused the event"
    )


async def test_an_event_that_failed_in_the_request_is_relayed_once_it_is_due(
    db_session: AsyncSession, session_factory, published_events
) -> None:
    event_id = await _store_unpublished(db_session, age=timedelta(minutes=5), attempts=1)
    row = await db_session.get(DomainEventRecord, event_id)
    assert row is not None
    row.last_error = "HANDLER_FAILED"
    row.next_attempt_at = _now() + timedelta(minutes=1)
    await db_session.commit()

    assert await relay_once(session_factory, event_bus) == 0, "not due yet"

    row = await _record(db_session, event_id)
    assert row is not None
    row.next_attempt_at = _now() - timedelta(seconds=1)
    await db_session.commit()
    assert await relay_once(session_factory, event_bus) == 1

    row = await _record(db_session, event_id)
    assert row is not None and row.published_at is not None
    assert row.publish_attempts == 2 and row.last_error is None
    assert [e.event_id for e in published_events] == [event_id]


async def test_the_relay_leaves_fresh_events_to_the_request(
    db_session: AsyncSession, session_factory, published_events
) -> None:
    event_id = await _store_unpublished(db_session, age=timedelta(seconds=5))

    assert await relay_once(session_factory, event_bus) == 0
    assert published_events == []
    row = await _record(db_session, event_id)
    assert row is not None and row.publish_attempts == 0


async def test_a_relayed_event_with_a_failing_handler_is_retried_later(
    db_session: AsyncSession, session_factory
) -> None:
    event_id = await _store_unpublished(db_session, age=timedelta(minutes=2))

    async def broken(event: SomethingStored) -> None:
        raise RuntimeError("subscriber is down")

    event_bus.subscribe(SomethingStored, broken)
    try:
        assert await relay_once(session_factory, event_bus) == 0
        # Not due again yet: the claim moved next_attempt_at forward.
        assert await relay_once(session_factory, event_bus) == 0
    finally:
        event_bus.unsubscribe(SomethingStored, broken)

    row = await _record(db_session, event_id)
    assert row is not None
    assert row.published_at is None
    assert row.publish_attempts == 1
    assert row.last_error == "HANDLER_FAILED"
    assert row.next_attempt_at > _now()


async def test_a_poison_event_is_given_up_after_the_maximum_attempts(
    db_session: AsyncSession, session_factory, published_events
) -> None:
    event_id = await _store_unpublished(
        db_session, age=timedelta(minutes=2), event_type="RemovedLongAgo", payload={}
    )

    assert await relay_once(session_factory, event_bus) == 0
    row = await _record(db_session, event_id)
    assert row is not None
    assert row.last_error == "UNKNOWN_EVENT_TYPE" and row.failed_at is None

    # The last allowed attempt fails: the row is given up at once, not one backoff later.
    row.publish_attempts = MAX_PUBLISH_ATTEMPTS - 1
    row.next_attempt_at = _now() - timedelta(seconds=1)
    await db_session.commit()
    assert await relay_once(session_factory, event_bus) == 0

    row = await _record(db_session, event_id)
    assert row is not None and row.failed_at is not None and row.published_at is None
    assert row.publish_attempts == MAX_PUBLISH_ATTEMPTS
    assert published_events == []

    # Skipped from now on, even when due.
    row.next_attempt_at = _now() - timedelta(seconds=1)
    failed_at = row.failed_at
    await db_session.commit()
    assert await relay_once(session_factory, event_bus) == 0
    row = await _record(db_session, event_id)
    assert row is not None
    assert row.publish_attempts == MAX_PUBLISH_ATTEMPTS and row.failed_at == failed_at


async def test_an_invalid_payload_is_reported_as_such(
    db_session: AsyncSession, session_factory
) -> None:
    event_id = await _store_unpublished(
        db_session, age=timedelta(minutes=2), payload={"unexpected": True}
    )

    assert await relay_once(session_factory, event_bus) == 0
    row = await _record(db_session, event_id)
    assert row is not None and row.last_error == "PAYLOAD_INVALID"


# --- Retention ------------------------------------------------------------------------------------


async def test_retention_deletes_old_published_events_only(
    db_session: AsyncSession, session_factory
) -> None:
    old_published = await _store_unpublished(db_session, age=timedelta(days=400))
    old_unpublished = await _store_unpublished(db_session, age=timedelta(days=400))
    young_published = await _store_unpublished(db_session, age=timedelta(days=10))
    for event_id in (old_published, young_published):
        row = await db_session.get(DomainEventRecord, event_id)
        assert row is not None
        row.published_at = _now()
    await db_session.commit()

    assert await purge_published_events(session_factory, 0) == 0, "0 keeps everything"
    assert await purge_published_events(session_factory, 365) == 1

    assert await _record(db_session, old_published) is None
    assert await _record(db_session, old_unpublished) is not None
    assert await _record(db_session, young_published) is not None


# --- Two workers (real connections, committed data) -----------------------------------------------


async def test_a_row_locked_by_one_worker_is_skipped_by_another(
    db_engine, published_events
) -> None:
    """SKIP LOCKED: while one worker holds a row, another relay pass neither waits nor takes it."""

    @asynccontextmanager
    async def fresh_session() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(db_engine, expire_on_commit=False) as session:
            yield session

    occurred_at = _now() - timedelta(minutes=2)
    event_id = uuid.uuid4()
    async with fresh_session() as session:
        session.add(
            DomainEventRecord(
                id=event_id,
                event_type="SomethingStored",
                payload={"name": "contended"},
                occurred_at=occurred_at,
                next_attempt_at=occurred_at,
            )
        )
        await session.commit()
    try:
        async with db_engine.connect() as holder:
            transaction = await holder.begin()
            await holder.execute(
                select(DomainEventRecord.id)
                .where(DomainEventRecord.id == event_id)
                .with_for_update()
            )
            assert await relay_once(fresh_session, event_bus) == 0
            await transaction.rollback()

        assert await relay_once(fresh_session, event_bus) == 1
        assert [e.event_id for e in published_events] == [event_id]
    finally:
        async with fresh_session() as session:
            await session.execute(delete(DomainEventRecord).where(DomainEventRecord.id == event_id))
            await session.commit()
