"""Real concurrency on committed data: two connections, two transactions.

The usual rolled-back test transaction cannot show lock behaviour, so these tests build the
meters world in a scratch database, commit it, and drive MetersService from two sessions.

Lock order everywhere is period first, then meter (docs/API.md, POST /readings):
- submissions take the period FOR SHARE, then the meter FOR UPDATE;
- status changes and estimates take the period FOR NO KEY UPDATE (compatible with the
  FOR KEY SHARE that foreign-key checks need), then the meters.
"""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.database import build_engine
from src.core.event_bus import InMemoryEventBus
from src.core.exceptions import DomainError
from src.core.migrations import alembic_config
from src.modules.meters.schemas import PeriodStatus, ReadingSource, ReadingSubmitRequest
from src.modules.meters.service import MetersService
from src.modules.property.service import PropertyService
from tests.db_utils import scratch_database
from tests.meters.conftest import MetersWorld, build_meters_world

BLOCKED = 0.5  # seconds a waiting transaction gets to (wrongly) finish on its own


@pytest.fixture
async def committed(database_url: str) -> AsyncIterator[tuple[AsyncEngine, MetersWorld]]:
    async with scratch_database(database_url) as url:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        engine = build_engine(url)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            world = await build_meters_world(session)
            await session.commit()
        try:
            yield engine, world
        finally:
            await engine.dispose()


async def service_for(session: AsyncSession, user_id):
    property_service = PropertyService(session)
    user = await property_service.get_current_user(user_id)
    return MetersService(session, property_service, InMemoryEventBus()), user


def reading(meter, value: str, reason: str | None = None) -> ReadingSubmitRequest:
    return ReadingSubmitRequest(
        meter_id=meter.id, period_code="2026-10", value=Decimal(value), reason=reason
    )


async def test_submission_waits_for_a_concurrent_period_close(committed) -> None:
    # Regression: the submission read the period without a lock and committed a RESIDENT reading
    # into a period the chairman had just moved to REVIEW_BY_BOARD.
    engine, world = committed
    async with (
        AsyncSession(engine, expire_on_commit=False) as chair_session,
        AsyncSession(engine, expire_on_commit=False) as resident_session,
    ):
        chair, chairman = await service_for(chair_session, world.chairman.id)
        resident_service, resident = await service_for(resident_session, world.owner_14.id)

        await chair.change_period_status(chairman, "2026-10", PeriodStatus.REVIEW_BY_BOARD)
        submit = asyncio.create_task(
            resident_service.submit_reading(resident, reading(world.meter_14, "120.000"))
        )
        await asyncio.sleep(BLOCKED)
        assert not submit.done(), "the submission must wait for the status change"

        await chair_session.commit()
        with pytest.raises(DomainError) as caught:
            await submit
        assert caught.value.error_code == "PERIOD_CLOSED"


async def test_lock_before_estimate_then_submission(committed) -> None:
    engine, world = committed
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        chair, chairman = await service_for(setup, world.chairman.id)
        await chair.change_period_status(chairman, "2026-10", PeriodStatus.REVIEW_BY_BOARD)
        await setup.commit()

    async with (
        AsyncSession(engine, expire_on_commit=False) as estimate_session,
        AsyncSession(engine, expire_on_commit=False) as submit_session,
    ):
        estimator, chairman = await service_for(estimate_session, world.chairman.id)
        submitter, accountant = await service_for(submit_session, world.accountant.id)

        estimate = await estimator.estimate_missing(chairman, "2026-10")
        submit = asyncio.create_task(
            submitter.submit_reading(accountant, reading(world.meter_5, "12.000", reason="Photo"))
        )
        await asyncio.sleep(BLOCKED)
        assert not submit.done(), "the submission must wait for the estimate"

        await estimate_session.commit()
        saved = await asyncio.wait_for(submit, timeout=10)
        await submit_session.commit()

    assert estimate.estimated_count == 3
    assert saved.source == ReadingSource.ADMIN_OVERRIDE  # overrode the fresh estimate
    assert str(saved.value) == "12.000"


async def test_lock_before_submission_then_estimate(committed) -> None:
    engine, world = committed
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        chair, chairman = await service_for(setup, world.chairman.id)
        await chair.change_period_status(chairman, "2026-10", PeriodStatus.REVIEW_BY_BOARD)
        await setup.commit()

    async with (
        AsyncSession(engine, expire_on_commit=False) as submit_session,
        AsyncSession(engine, expire_on_commit=False) as estimate_session,
    ):
        submitter, accountant = await service_for(submit_session, world.accountant.id)
        estimator, chairman = await service_for(estimate_session, world.chairman.id)

        await submitter.submit_reading(accountant, reading(world.meter_5, "12.000"))
        estimate = asyncio.create_task(estimator.estimate_missing(chairman, "2026-10"))
        await asyncio.sleep(BLOCKED)
        assert not estimate.done(), "the estimate must wait for the submission"

        await submit_session.commit()
        result = await asyncio.wait_for(estimate, timeout=10)
        await estimate_session.commit()

    # Apt 5 was submitted first, so only apt 14 and apt 2 are estimated.
    assert result.estimated_count == 2
    assert world.meter_5.id not in {e.meter_id for e in result.estimated}
