"""Async SQLAlchemy engine, session factory, declarative Base and the request-scoped session."""

import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.core.config import settings
from src.core.event_bus import EventBus, EventOutbox, get_event_bus


def build_engine(database_url: str) -> AsyncEngine:
    # hide_parameters: DB error texts would otherwise embed bound values (names, emails, phones)
    # and reach logs and spans through the exception message.
    return create_async_engine(
        database_url, echo=False, future=True, pool_pre_ping=True, hide_parameters=True
    )


engine = build_engine(settings.DATABASE_URL)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # CRITICAL: Never set to True!
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


# --- Column helpers shared by all modules -------------------------------------------------------


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def varchar_enum(enum_cls: type[StrEnum], check_name: str, length: int = 32) -> SAEnum:
    """Store a StrEnum as VARCHAR (no native PostgreSQL enum type), as docs/SCHEMA.md specifies,
    with a named CHECK constraint so a stray value cannot be written and later crash every read.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        name=check_name,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


_OUTBOX_KEY = "event_outbox"


def session_outbox(session: AsyncSession, bus: EventBus) -> EventOutbox:
    """The event outbox of this session's unit of work (created on first use)."""
    outbox = session.info.get(_OUTBOX_KEY)
    if outbox is None:
        outbox = session.info[_OUTBOX_KEY] = EventOutbox(bus)
    return outbox


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """One unit of work: commit on success, roll back on any exception.

    Domain events collected in the session's outbox are published only after the COMMIT
    succeeded, and dropped otherwise.
    """
    committed = False
    try:
        yield session
        await session.commit()
        committed = True
    except Exception:
        await session.rollback()
        raise
    finally:
        outbox: EventOutbox | None = session.info.pop(_OUTBOX_KEY, None)
        if committed and outbox is not None:
            await outbox.publish_pending()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield one session per request: commit on success, roll back on any exception, then
    publish the request's domain events (see `transaction`)."""
    async with async_session_factory() as session, transaction(session):
        yield session


# Always depend on the session through this alias. scope="function" ends the dependency (commit or
# rollback) BEFORE the response is sent. FastAPI's default "request" scope would commit after the
# client already got a 2xx, so a failed commit would still look successful.
DbSession = Annotated[AsyncSession, Depends(get_db_session, scope="function")]


def get_event_outbox(session: DbSession, bus: EventBus = Depends(get_event_bus)) -> EventOutbox:
    """FastAPI dependency: the request's outbox. Services add events here instead of publishing
    them directly; they go out after the request's transaction commits."""
    return session_outbox(session, bus)
