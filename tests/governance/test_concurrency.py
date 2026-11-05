"""Real concurrency on committed data for polls: two connections, two transactions."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.database import build_engine
from src.core.exceptions import DomainError
from src.core.migrations import alembic_config
from src.modules.governance.models import BallotRevision
from src.modules.governance.schemas import (
    AnswerInput,
    BallotRequest,
    Eligibility,
    PollDraftRequest,
    PollUnit,
    QuestionDraft,
)
from src.modules.governance.service import GovernanceService
from src.modules.property.service import PropertyService
from tests.db_utils import scratch_database
from tests.governance.conftest import PollWorld, build_poll_world

BLOCKED = 0.5


@pytest.fixture
async def committed(database_url: str) -> AsyncIterator[tuple[AsyncEngine, dict]]:
    async with scratch_database(database_url) as url:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        engine = build_engine(url)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            world: PollWorld = await build_poll_world(session)
            chairman = await PropertyService(session).get_current_user(world.chairman.id)
            service = GovernanceService(session, PropertyService(session))
            draft = PollDraftRequest(
                title="Clean-up",
                eligibility=Eligibility.RESIDENTS,
                unit=PollUnit.APARTMENT,
                closes_at=datetime.now(UTC) + timedelta(days=3),
                questions=[
                    QuestionDraft(text="Bags?", answer_mode="OWN_VALUE", value_type="NUMBER")
                ],
            )
            poll = await service.create_poll(chairman, draft)
            poll = await service.open_poll(chairman, poll.id)
            await session.commit()
        try:
            yield engine, {"world": world, "poll": poll}
        finally:
            await engine.dispose()


async def service_for(session: AsyncSession, user_id):
    properties = PropertyService(session)
    return GovernanceService(session, properties), await properties.get_current_user(user_id)


def bags(poll, value: str) -> BallotRequest:
    return BallotRequest(answers=[AnswerInput(question_id=poll.questions[0].id, value=value)])


async def test_two_answers_of_one_apartment_get_consecutive_versions(committed) -> None:
    engine, data = committed
    world, poll = data["world"], data["poll"]
    async with AsyncSession(engine) as first, AsyncSession(engine) as second:
        owner_service, owner = await service_for(first, world.owner_14.id)
        family_service, family = await service_for(second, world.family_14.id)
        await owner_service.answer(owner, poll.id, bags(poll, "1"))
        waiting = asyncio.create_task(family_service.answer(family, poll.id, bags(poll, "2")))
        await asyncio.sleep(BLOCKED)
        assert not waiting.done(), "the second answer waits for the unit's lock"
        await first.commit()
        await waiting
        await second.commit()

    async with AsyncSession(engine) as check:
        revisions = list(
            await check.scalars(select(BallotRevision.revision).order_by(BallotRevision.revision))
        )
    assert revisions == [1, 2]


async def test_an_answer_racing_a_close_is_either_in_or_refused(committed) -> None:
    engine, data = committed
    world, poll = data["world"], data["poll"]
    async with AsyncSession(engine) as board_session, AsyncSession(engine) as resident_session:
        board_service, chairman = await service_for(board_session, world.chairman.id)
        resident_service, owner = await service_for(resident_session, world.owner_14.id)
        await board_service.close_poll(chairman, poll.id)
        waiting = asyncio.create_task(resident_service.answer(owner, poll.id, bags(poll, "1")))
        await asyncio.sleep(BLOCKED)
        assert not waiting.done(), "the answer waits for the closing transaction"
        await board_session.commit()
        with pytest.raises(DomainError) as refused:
            await waiting
        assert refused.value.error_code == "POLL_NOT_OPEN"

    async with AsyncSession(engine) as check:
        assert await check.scalar(select(func.count()).select_from(BallotRevision)) == 0
