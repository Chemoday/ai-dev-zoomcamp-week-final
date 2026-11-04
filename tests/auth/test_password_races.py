"""Parallel password guesses with real concurrent transactions (task E7/A5): a scratch database, one
connection per request, so commits are real and other connections see them."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
from alembic import command
from argon2 import PasswordHasher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.config import get_settings
from src.core.database import build_engine
from src.core.exceptions import DomainError
from src.core.migrations import alembic_config
from src.modules.auth.models import AuthAttempt
from src.modules.auth.passwords import PasswordHashing, PasswordService
from tests.db_utils import scratch_database
from tests.factories import create_building, create_user

PARALLEL = 10
CHEAP = {"time_cost": 1, "memory_cost": 8, "parallelism": 1}


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    async with scratch_database(database_url) as url:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        engine = build_engine(url)
        yield engine
        await engine.dispose()


class GatedHashing(PasswordHashing):
    """Every check waits at the gate, so all guesses are in flight at the same time."""

    def __init__(self) -> None:
        super().__init__(PasswordHasher(**CHEAP))
        self.gate = asyncio.Event()
        self.changed = asyncio.Event()
        self.waiting = 0

    async def verify(self, encoded: str | None, password: str) -> bool:
        self.waiting += 1
        self.changed.set()
        await self.gate.wait()
        return await super().verify(encoded, password)


async def committed_failures(engine: AsyncEngine) -> int:
    async with AsyncSession(engine) as other:
        return (
            await other.scalar(
                select(func.count())
                .select_from(AuthAttempt)
                .where(AuthAttempt.kind == "PASSWORD", AuthAttempt.succeeded.is_(False))
            )
            or 0
        )


async def test_parallel_guesses_cannot_slip_past_the_limit(engine: AsyncEngine) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        building = await create_building(setup)
        user = await create_user(
            setup, building, "Ivan Petrov", email=f"ivan.{uuid.uuid4().hex[:8]}@example.com"
        )
        await setup.commit()
    email = user.email or ""
    hashing = GatedHashing()

    async def guess() -> str:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                await PasswordService(session, get_settings(), hashing).login(
                    email, f"wrong guess {uuid.uuid4().hex}", "198.51.100.1", None
                )
            except DomainError as exc:
                await session.rollback()
                return exc.error_code
            await session.commit()
            return "SIGNED_IN"

    tasks = [asyncio.create_task(guess()) for _ in range(PARALLEL)]
    for task in tasks:
        task.add_done_callback(lambda _: hashing.changed.set())
    async with asyncio.timeout(30):  # until every guess is either throttled or at the gate
        while hashing.waiting + sum(task.done() for task in tasks) < PARALLEL:
            hashing.changed.clear()
            await hashing.changed.wait()

    # While the hashes are still blocked, the reserved failures are committed and visible.
    assert hashing.waiting == 5
    assert await committed_failures(engine) == 5 * 3  # the pair, the address and the e-mail
    hashing.gate.set()
    outcomes = sorted(await asyncio.gather(*tasks))

    assert outcomes == ["INVALID_CREDENTIALS"] * 5 + ["RATE_LIMITED"] * (PARALLEL - 5)
