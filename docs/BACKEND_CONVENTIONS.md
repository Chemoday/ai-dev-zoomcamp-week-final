# Backend Implementation Conventions & Code Blueprints

Every backend agent MUST strictly follow these code patterns. Do NOT invent alternative patterns for DB sessions, exceptions, models, or dependency injection.

---

## 1. Python & Tooling Stack (Exact Versions)
- **Python:** `3.12+`
- **Web Framework:** `fastapi >= 0.115.0`, `pydantic >= 2.9.0`, `pydantic-settings >= 2.5.0`
- **Database:** `sqlalchemy[asyncio] >= 2.0.35`, `asyncpg >= 0.30.0`, `alembic >= 1.13.0`
- **Testing:** `pytest >= 8.3.0`, `pytest-asyncio >= 0.24.0`, `httpx >= 0.27.0`
- **Observability:** `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi`, `structlog`

---

## 2. Database & SQLAlchemy 2.0 Rules (Anti-`MissingGreenlet`)

### A. Session Factory (`src/core/database.py`)
`expire_on_commit=False` is MANDATORY to prevent `MissingGreenlet` errors in async endpoints.

```python
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # CRITICAL: Never set to True!
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    # transaction(): commit on success, roll back on any exception, then publish the
    # session's domain events (see "Domain events" below).
    async with async_session_factory() as session, transaction(session):
        yield session
B. Transaction Rule
Services use await self.session.flush() to obtain generated IDs or validate constraints within a transaction.
The outer get_db_session dependency automatically commits on request success or rolls back on exception.
ALWAYS inject the session through the `DbSession` alias from `src/core/database.py` (`Annotated[AsyncSession, Depends(get_db_session, scope="function")]`), never with a bare `Depends(get_db_session)`. FastAPI's default "request" scope ends a yield-dependency only AFTER the response is sent, so the commit would run after the client already received a 2xx: a failed commit would look successful, and an immediate follow-up request could read stale data. `scope="function"` commits (or rolls back) before the response. This is covered by `tests/core/test_database.py`.
**Lock order.** When a request locks several rows, it always locks the parent first, then the child: e.g. the reading period, then the meter. Lock a period you only read `FOR SHARE`, and one you change `FOR NO KEY UPDATE` (`with_for_update(key_share=True)`). Plain `FOR UPDATE` on a parent also blocks the `FOR KEY SHARE` that foreign-key checks of child inserts need, which deadlocks against a transaction that already holds the child. `tests/meters/test_concurrency.py` drives this with two real connections.
**Domain events go out after the commit.** Services never call `event_bus.publish()` directly. They inject the request's `EventOutbox` (`Depends(get_event_outbox)` from `src/core/database.py`) and call `outbox.add(event)`. `transaction()` publishes the collected events after `COMMIT` succeeded and drops them on rollback, so a notification never goes out for a change that was rolled back, and slow handlers never run while the transaction holds row locks. Handlers still run before the response is sent; a failing handler (or bus) is logged and never turns the committed request into an error. Code outside a request uses `async with transaction(session):` and `session_outbox(session, bus)` the same way. Covered by `tests/core/test_outbox.py`.
**Domain events are durable** (`src/core/domain_events.py`, `src/core/events_registry.py`; covered by `tests/core/test_domain_events.py`).
- **Write.** `outbox.add(event)` also adds a `core_domain_events` row to the same session (docs/SCHEMA.md section 6), so the event is committed or rolled back together with the change. `add()` rejects an event class that is not in the event registry (`src/core/events_registry.py`, built like `models_registry.py`; duplicate names are rejected), so a request fails fast instead of storing an event nobody can rebuild.
- **Publish after commit.** `transaction()` publishes the events right after `COMMIT`, as today, then marks their rows in a short follow-up transaction. `bus.publish()` reports which handlers failed: a row is marked `published_at` only if every handler succeeded; otherwise `publish_attempts` is incremented and `next_attempt_at` set with backoff, so the relay retries it. A failing "mark" is logged and swallowed (the change is committed; the relay will publish again).
- **Relay** (`relay_once(session_factory, bus)`, started in the app lifespan with the switch `DOMAIN_EVENTS_RELAY_ENABLED`; one pass at startup, then every 5 s; tests call `relay_once` directly). In a short transaction it claims up to 50 rows with `published_at IS NULL AND failed_at IS NULL AND occurred_at < now() - 30 s AND next_attempt_at <= now()`, `FOR UPDATE SKIP LOCKED`, and moves their `next_attempt_at` forward (1 min, 5 min, 30 min, …) and increments `publish_attempts` before committing, so no other worker takes them while they are being handled and no lock is held during the handlers. The 30-second grace period keeps the relay away from events the request is still publishing. It rebuilds each event, publishes it under the stored `traceparent`, and marks it as above.
- **Poison rows.** When the 10th attempt fails, the row gets `failed_at` and a machine `last_error` (e.g. `UNKNOWN_EVENT_TYPE`, `PAYLOAD_INVALID`, `HANDLER_FAILED`), is skipped from then on, logged (`domain_event_gave_up`) and counted in the `domain_events.failed` metric. Every failed attempt is logged too (`domain_event_relay_failed` with the event id and code). To retry failed rows after a fix (e.g. a rollback to an image that did not know a new event type), requeue them by hand: `UPDATE core_domain_events SET failed_at = NULL, publish_attempts = 0, next_attempt_at = now() WHERE failed_at IS NOT NULL AND event_type = '...'`.
- **Codec.** Payloads are encoded and rebuilt with a pydantic `TypeAdapter(event_class)` (`dump_python(mode="json")` / `validate_python`), so UUIDs, `Decimal`, tuples and enums come back with their types; `event_id` and `occurred_at` come from the columns. A test round-trips every registered event. **Event classes are a stored contract:** a new field needs a default; a rename keeps the old name as an alias in the registry.
- **Guarantees.** Every event committed with its change reaches every subscriber at least once, unless it becomes a poison row (logged and counted). Order is **not** guaranteed: subscribers must be idempotent and must not rely on order. Duplicates are possible in two windows: a request that takes more than 30 s between building an event and publishing it (the relay may take the row meanwhile), and a relay worker whose claim (1 minute) expires while a slow handler still runs; results are marked one event at a time to keep that window small.
- **Housekeeping.** The relay also deletes published rows older than `DOMAIN_EVENTS_RETENTION_DAYS` (default 365, `0` keeps them) once an hour, and exports the age of the oldest unpublished row as a metric.
**Free text.** Every free-text input uses the types in `src/core/validation.py`: `safe_text(max_length)` for required text (never blank), `optional_safe_text(max_length)` for optional text (blank means `None`), or `SafeText` without a limit. They trim first, then check the length, and reject control characters: a NUL byte would otherwise become a 500 from PostgreSQL.
The engine is created by `build_engine()` with `hide_parameters=True`, so database error texts (which reach logs and spans) never contain bound values such as names or emails.
C. SQLAlchemy 2.0 Typed Models Pattern
NEVER use legacy Column(...). Always use Mapped[...] and mapped_column(...).
code
Python
from datetime import datetime, timezone
from decimal import Decimal
import uuid
from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from src.core.database import Base


class MeterReading(Base):
    __tablename__ = "meters_readings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("meters_devices.id"), nullable=False
    )
    value: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
D. Alembic Model Registry (src/core/models_registry.py)
To prevent empty Alembic migrations, ALL module models MUST be imported in src/core/models_registry.py, and alembic/env.py MUST import this file before referencing Base.metadata.
code
Python
# src/core/models_registry.py
from src.core.database import Base
from src.modules.communication.models import Comment, EmergencyAck, Post
from src.modules.meters.models import MeterDevice, MeterReading, MeterReadingLog, ReadingPeriod
from src.modules.property.models import Apartment, ApartmentMembership, Building, User, UserIdentity

__all__ = ["Base"]
E. Composition roots
`src/core/models_registry.py` and `src/core/seed.py` are the only places outside a module that import its models: the registry for Alembic, the seed because no module offers "create a demo building with history" as a service. Seed rows are inserted table by table, parents first (there is no ORM relationship between modules to order them), and seeding publishes no domain events.
3. Unified Error Handling (src/core/exceptions.py)
NEVER raise raw fastapi.HTTPException with arbitrary strings. Always raise DomainError subclasses so the frontend receives predictable JSON payloads.
Every endpoint documents its errors with `responses=error_responses({404: ["METER_NOT_FOUND"], 422: ["VALIDATION_ERROR"]})` (routers add 401 and 500 for all their endpoints). An endpoint with parameters must list 422, otherwise FastAPI documents its own `HTTPValidationError`, which this API never returns. After any API change run `uv run python -m src.core.openapi` and commit `docs/openapi.json`; `tests/core/test_openapi.py` enforces both.
code
Python
# src/core/exceptions.py
from typing import Any
from fastapi import Request
from fastapi.responses import JSONResponse


class DomainError(Exception):

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ):
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.extra = extra or {}


# Registered in src/main.py:
async def domain_error_handler(
    request: Request, exc: DomainError
) -> JSONResponse:
    payload = {
        "error_code": exc.error_code,
        "message": exc.message,
        **exc.extra,
    }
    return JSONResponse(status_code=exc.status_code, content=payload)
Example usage for the 2-Step Anomaly Warning in meters/service.py:
code
Python
raise DomainError(
    status_code=409,
    error_code="ANOMALY_CONFIRMATION_STEP_1",  # codes as in docs/API.md
    message=(
        f"You entered a very large value (delta: {delta} m3). Are you sure?"
    ),
    extra={
        "required_confirmation_level": 1,
        "consumption_delta": str(delta),
        "average_delta": str(avg_delta),
    },
)
4. Cross-Module Dependency Injection Pattern (No Circular Imports)
When CommunicationService needs author privacy masking from PropertyService, wire them via FastAPI Depends in service.py:
code
Python
# src/modules/communication/service.py
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.database import DbSession, get_event_outbox
from src.core.event_bus import EventOutbox
from src.modules.property.service import PropertyService, get_property_service


class CommunicationService:

    def __init__(
        self,
        session: AsyncSession,
        property_service: PropertyService,
        outbox: EventOutbox,  # outbox.add(event): published after the request commits
    ):
        self.session = session
        self.property_service = property_service
        self.outbox = outbox


def get_communication_service(
    session: DbSession,  # from src.core.database: commits before the response is sent
    property_service: PropertyService = Depends(get_property_service),
    outbox: EventOutbox = Depends(get_event_outbox),
) -> CommunicationService:
    return CommunicationService(session, property_service, outbox)
5. Authentication & Demo Role Switching (src/core/security.py)
To make both automated integration testing and UI role-switching effortless in Phase 1 while keeping the architecture production-ready:
Support Authorization: Bearer <jwt_token> for standard login.
When settings.DEMO_MODE_ENABLED == True (off by default in code; enabled explicitly in local Docker, the test suite and the public demo deployment), ALSO allow header X-Demo-User-Id: <uuid> to impersonate any seeded user (Chairman, Accountant, Owner Apt 14, Owner Apt 1, Tenant).
Provide get_current_user(session, ...) returning a CurrentUserDTO containing:
id: UUID, building_id: UUID, building_role: BuildingRole, privacy_display_mode: DisplayMode
memberships: list[ApartmentMembershipDTO] (containing apartment_id, entrance, relation_type, can_submit_readings, can_view_bills, can_vote).
6. Testing Pattern (tests/conftest.py)
All integration tests use an isolated test PostgreSQL database (or transactional rollback fixture) and httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test").
Unit tests in tests/unit/ MUST NOT touch the database; they test pure functions in policies.py directly.