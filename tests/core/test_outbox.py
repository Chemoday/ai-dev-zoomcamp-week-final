"""Domain events are published only after the unit of work commits, and dropped otherwise.

Their durable rows (core_domain_events) are covered in tests/core/test_domain_events.py.
"""

from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession, get_event_outbox, session_outbox, transaction
from src.core.event_bus import DomainEvent, EventOutbox, InMemoryEventBus
from src.core.events_registry import get_event_registry
from src.core.exceptions import DomainError
from src.modules.property.models import Building


@dataclass(frozen=True, kw_only=True)
class SomethingHappened(DomainEvent):
    name: str


get_event_registry().register(SomethingHappened)


class FakeSession:
    """Just enough of AsyncSession for `transaction` and the event store: records commit and
    rollback; staged rows and statements are kept but not executed."""

    def __init__(self, log: list[str], fail_commit: bool = False) -> None:
        self.info: dict[str, Any] = {}
        self.log = log
        self.fail_commit = fail_commit
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def execute(self, statement: Any) -> None:
        return None

    async def commit(self) -> None:
        if self.fail_commit:
            raise ConnectionError("connection lost during COMMIT")
        self.log.append("commit")

    async def rollback(self) -> None:
        self.log.append("rollback")


def recording_bus(log: list[str]) -> InMemoryEventBus:
    bus = InMemoryEventBus()

    async def handler(event: SomethingHappened) -> None:
        log.append(f"handled {event.name}")

    bus.subscribe(SomethingHappened, handler)
    return bus


async def test_events_are_published_after_the_commit_in_order() -> None:
    log: list[str] = []
    session, bus = FakeSession(log), recording_bus(log)

    async with transaction(session):
        session_outbox(session, bus).add(SomethingHappened(name="a"))
        session_outbox(session, bus).add(SomethingHappened(name="b"))
        log.append("work done")

    # The second commit is the short follow-up transaction that marks the rows published.
    assert log == ["work done", "commit", "handled a", "handled b", "commit"]
    assert [row.payload for row in session.added] == [{"name": "a"}, {"name": "b"}]
    assert session.info == {}, "the outbox does not outlive its unit of work"


async def test_events_are_dropped_when_the_work_fails() -> None:
    log: list[str] = []
    session, bus = FakeSession(log), recording_bus(log)

    with pytest.raises(ValueError):
        async with transaction(session):
            session_outbox(session, bus).add(SomethingHappened(name="a"))
            raise ValueError("business rule violated")

    assert log == ["rollback"]
    assert session.info == {}


async def test_events_are_dropped_when_the_commit_fails() -> None:
    log: list[str] = []
    session, bus = FakeSession(log, fail_commit=True), recording_bus(log)

    with pytest.raises(ConnectionError):
        async with transaction(session):
            session_outbox(session, bus).add(SomethingHappened(name="a"))

    assert log == ["rollback"]


async def test_a_failing_publish_never_fails_the_committed_work(log_capture) -> None:
    class BrokenBus(InMemoryEventBus):
        async def publish(self, event: DomainEvent) -> None:
            raise RuntimeError("bus is down")

    log: list[str] = []
    session = FakeSession(log)
    async with transaction(session):
        session_outbox(session, BrokenBus()).add(SomethingHappened(name="a"))

    # The follow-up commit schedules a retry instead of marking the row published.
    assert log == ["commit", "commit"]
    assert any(r["event"] == "domain_event_publish_failed" for r in log_capture.records)


async def test_outbox_discard_drops_pending_events() -> None:
    log: list[str] = []
    outbox = EventOutbox(recording_bus(log))
    outbox.add(SomethingHappened(name="a"))
    outbox.discard()
    await outbox.publish_pending()

    assert outbox.pending == ()
    assert log == []


# --- Through the real request session ---------------------------------------------------------


@pytest.fixture
def outbox_app(db_app: FastAPI) -> FastAPI:
    async def endpoint(
        fail: bool, session: DbSession, outbox: EventOutbox = Depends(get_event_outbox)
    ) -> dict[str, bool]:
        session.add(Building(name="Outbox test", address="Somewhere 1"))
        await session.flush()
        outbox.add(SomethingHappened(name="building created"))
        if fail:
            raise DomainError(status_code=409, error_code="CONFLICT", message="Rolled back.")
        return {"ok": True}

    db_app.add_api_route("/test/outbox", endpoint, methods=["POST"])
    return db_app


async def _buildings_named(session: AsyncSession, name: str) -> int:
    return len(list(await session.scalars(select(Building.id).where(Building.name == name))))


async def test_request_events_are_published_once_the_request_committed(
    outbox_app: FastAPI, api_client: AsyncClient, db_session: AsyncSession, published_events
) -> None:
    response = await api_client.post("/test/outbox", params={"fail": False})

    assert response.status_code == 200
    assert [e.name for e in published_events] == ["building created"]
    assert await _buildings_named(db_session, "Outbox test") == 1


async def test_a_rolled_back_request_publishes_nothing(
    outbox_app: FastAPI, api_client: AsyncClient, db_session: AsyncSession, published_events
) -> None:
    response = await api_client.post("/test/outbox", params={"fail": True})

    assert response.status_code == 409
    assert published_events == []
    assert await _buildings_named(db_session, "Outbox test") == 0
