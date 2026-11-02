"""Races of task E7/A4 with real concurrent transactions (a scratch database, one connection per
transaction): the last chairman, the tenant limit, a double click, and the automatic closing."""

import asyncio
import contextvars
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.config import get_settings
from src.core.database import build_engine, session_outbox
from src.core.event_bus import InMemoryEventBus
from src.core.exceptions import DomainError
from src.core.migrations import alembic_config
from src.modules.auth.accounts import AccountService
from src.modules.auth.invitations import InvitationService
from src.modules.auth.links import LinkService
from src.modules.auth.models import AuthInvitation
from src.modules.auth.schemas import (
    InvitationKind,
    OwnerInvitationCreate,
    RegistrationRequest,
    StaffInvitationCreate,
    StaffRole,
    TenantInvitationCreate,
)
from src.modules.property.models import User
from src.modules.property.schemas import BuildingRole, Locale, RelationType
from src.modules.property.service import PropertyService
from tests.db_utils import scratch_database
from tests.factories import create_apartment, create_building, create_membership, create_user


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    async with scratch_database(database_url) as url:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        engine = build_engine(url)
        yield engine
        await engine.dispose()


@asynccontextmanager
async def unit(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def outcome(engine: AsyncEngine, work: Callable[[AsyncSession], Awaitable[Any]]) -> str:
    """'ok', or the error code of a DomainError; any other error (a deadlock) fails the test."""
    try:
        async with unit(engine) as session:
            await work(session)
    except DomainError as exc:
        return exc.error_code
    return "ok"


async def caller(session: AsyncSession, user_id: uuid.UUID):
    current = await PropertyService(session).get_current_user(user_id)
    assert current is not None
    return current


def accounts(session: AsyncSession) -> AccountService:
    return AccountService(session, get_settings(), session_outbox(session, InMemoryEventBus()))


async def test_two_chairmen_stepping_down_at_once(engine: AsyncEngine) -> None:
    for _ in range(3):
        async with unit(engine) as session:
            building = await create_building(session)
            first = await create_user(session, building, "A", role=BuildingRole.CHAIRMAN)
            second = await create_user(session, building, "B", role=BuildingRole.CHAIRMAN)

        async def step_down(user_id: uuid.UUID):
            return lambda s: PropertyService(s).set_building_role(user_id, BuildingRole.BOARD)

        results = await asyncio.gather(
            outcome(engine, await step_down(first.id)),
            outcome(engine, await step_down(second.id)),
        )
        assert sorted(results) == ["LAST_CHAIRMAN", "ok"]  # never a deadlock, never nobody


async def test_the_tenant_limit_holds_under_concurrency(engine: AsyncEngine) -> None:
    async with unit(engine) as session:
        building = await create_building(session)
        flat = await create_apartment(session, building, "14")
        owner = await create_user(session, building, "Owner")
        await create_membership(session, owner, flat)
        for name in ("T1", "T2"):
            tenant = await create_user(session, building, name)
            await create_membership(session, tenant, flat, relation=RelationType.TENANT)

    async def invite(session: AsyncSession) -> None:
        body = TenantInvitationCreate(
            kind=InvitationKind.TENANT,
            email=f"t.{uuid.uuid4().hex}@example.com",
            apartment_id=flat.id,
        )
        await InvitationService(session, get_settings()).create(
            await caller(session, owner.id), body
        )

    results = await asyncio.gather(outcome(engine, invite), outcome(engine, invite))
    assert sorted(results) == ["TENANT_LIMIT_REACHED", "ok"]


async def test_a_double_click_invites_once(engine: AsyncEngine) -> None:
    async with unit(engine) as session:
        building = await create_building(session)
        chairman = await create_user(session, building, "Chair", role=BuildingRole.CHAIRMAN)

    body = StaffInvitationCreate(
        kind=InvitationKind.STAFF, email="board@example.com", building_role=StaffRole.BOARD
    )

    async def invite(session: AsyncSession) -> None:
        await InvitationService(session, get_settings()).create(
            await caller(session, chairman.id), body
        )

    results = await asyncio.gather(outcome(engine, invite), outcome(engine, invite))
    assert sorted(results) == ["INVITATION_ALREADY_PENDING", "ok"]


async def test_ending_the_last_two_memberships_at_once_closes_the_account(
    engine: AsyncEngine,
) -> None:
    async with unit(engine) as session:
        building = await create_building(session)
        chairman = await create_user(session, building, "Chair", role=BuildingRole.CHAIRMAN)
        person = await create_user(session, building, "Two Flats")
        first = await create_membership(
            session, person, await create_apartment(session, building, "1")
        )
        second = await create_membership(
            session, person, await create_apartment(session, building, "2")
        )

    def end(membership_id: uuid.UUID):
        async def work(session: AsyncSession) -> None:
            await accounts(session).end_membership(
                await caller(session, chairman.id), person.id, membership_id
            )

        return work

    results = await asyncio.gather(outcome(engine, end(first.id)), outcome(engine, end(second.id)))
    assert results == ["ok", "ok"]
    async with unit(engine) as session:
        active = await session.scalar(select(User.is_active).where(User.id == person.id))
    assert active is False  # one of the two saw no membership left and closed it


async def _accept_against_closing(engine: AsyncEngine, round_: int) -> None:
    async with unit(engine) as session:
        building = await create_building(session)
        chairman = await create_user(session, building, "Chair", role=BuildingRole.CHAIRMAN)
        email = f"x.{round_}.{uuid.uuid4().hex[:6]}@example.com"
        person = await create_user(session, building, "X", email=email)
        last = await create_membership(
            session, person, await create_apartment(session, building, "14")
        )
        await create_apartment(session, building, "2")
    async with unit(engine) as session:
        _, mail = await InvitationService(session, get_settings()).create(
            await caller(session, chairman.id),
            OwnerInvitationCreate(kind=InvitationKind.OWNER, email=email, apartment_number="2"),
        )
    token = mail.link.rpartition("#")[2]

    async def accept(session: AsyncSession) -> None:
        current = await PropertyService(session).get_current_user(person.id)
        if current is not None:  # closed first: nobody is left to accept
            await InvitationService(session, get_settings()).accept(current, token)

    async def end(session: AsyncSession) -> None:
        await accounts(session).end_membership(
            await caller(session, chairman.id), person.id, last.id
        )

    results = await asyncio.gather(outcome(engine, accept), outcome(engine, end))
    assert set(results) <= {"ok", "INVALID_LINK"}
    async with unit(engine) as session:
        current = await PropertyService(session).get_current_user(person.id)
    # Accepted first: the person keeps flat 2. Closed first: the invitation went with it.
    assert current is None or [m.apartment_number for m in current.memberships] == ["2"]


async def test_accepting_while_the_last_membership_ends(engine: AsyncEngine) -> None:
    """Accept locks like the closing does (the people lock first): no deadlock, and the end
    state is consistent either way."""
    for round_ in range(3):
        await _accept_against_closing(engine, round_)


async def test_re_inviting_while_a_co_owner_resends(engine: AsyncEngine) -> None:
    async with unit(engine) as session:
        building = await create_building(session)
        flat = await create_apartment(session, building, "14")
        first = await create_user(session, building, "Owner A")
        second = await create_user(session, building, "Owner B")
        await create_membership(session, first, flat)
        await create_membership(session, second, flat)
    body = TenantInvitationCreate(
        kind=InvitationKind.TENANT, email="tenant@example.com", apartment_id=flat.id
    )
    async with unit(engine) as session:
        view, _ = await InvitationService(session, get_settings()).create(
            await caller(session, first.id), body
        )
    async with unit(engine) as session:
        await session.execute(
            update(AuthInvitation).values(expires_at=datetime.now(UTC) - timedelta(days=1))
        )

    async def re_invite(session: AsyncSession) -> None:
        await InvitationService(session, get_settings()).create(
            await caller(session, first.id), body
        )

    async def resend(session: AsyncSession) -> None:
        await InvitationService(session, get_settings()).resend(
            await caller(session, second.id), view.invitation_id
        )

    results = await asyncio.gather(outcome(engine, re_invite), outcome(engine, resend))
    # Whichever runs second sees the first one's result; never a deadlock.
    assert sorted(results) in (["INVITATION_ALREADY_PENDING", "ok"], ["INVITATION_NOT_FOUND", "ok"])


# A transaction marked slow waits just before it asks for the people lock, so the other one runs
# ahead and takes its own locks first: the interleaving that deadlocked when a path locked rows
# before the people lock.
_slow = contextvars.ContextVar("slow_people_lock", default=False)


@pytest.fixture
def slow_people_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    original = PropertyService.lock_people

    async def lock_people(self: PropertyService, building_id: uuid.UUID) -> None:
        if _slow.get():
            await asyncio.sleep(0.5)
        await original(self, building_id)

    monkeypatch.setattr(PropertyService, "lock_people", lock_people)


async def test_closing_while_the_person_accepts(engine: AsyncEngine, slow_people_lock) -> None:
    async with unit(engine) as session:
        building = await create_building(session)
        chairman = await create_user(session, building, "Chair", role=BuildingRole.CHAIRMAN)
        person = await create_user(session, building, "X", email="x.close@example.com")
        await create_membership(session, person, await create_apartment(session, building, "14"))
        await create_apartment(session, building, "2")
    async with unit(engine) as session:
        _, mail = await InvitationService(session, get_settings()).create(
            await caller(session, chairman.id),
            OwnerInvitationCreate(
                kind=InvitationKind.OWNER, email="x.close@example.com", apartment_number="2"
            ),
        )
    token = mail.link.rpartition("#")[2]

    async def close(session: AsyncSession) -> None:
        _slow.set(True)
        await accounts(session).close(await caller(session, chairman.id), person.id)

    async def accept(session: AsyncSession) -> None:
        await asyncio.sleep(0.15)  # after the closing started
        current = await PropertyService(session).get_current_user(person.id)
        if current is not None:
            await InvitationService(session, get_settings()).accept(current, token)

    results = await asyncio.gather(outcome(engine, close), outcome(engine, accept))
    assert set(results) <= {"ok", "INVALID_LINK"}
    async with unit(engine) as session:
        assert await PropertyService(session).get_current_user(person.id) is None


async def test_registering_while_the_inviter_resends(engine: AsyncEngine, slow_people_lock) -> None:
    async with unit(engine) as session:
        building = await create_building(session)
        chairman = await create_user(session, building, "Chair", role=BuildingRole.CHAIRMAN)
        await create_apartment(session, building, "2")
    async with unit(engine) as session:
        view, mail = await InvitationService(session, get_settings()).create(
            await caller(session, chairman.id),
            OwnerInvitationCreate(
                kind=InvitationKind.OWNER, email="new.owner@example.com", apartment_number="2"
            ),
        )
    async with unit(engine) as session:
        opened, _ = await LinkService(session, get_settings()).redeem(
            mail.link.rpartition("#")[2], "198.51.100.9", None
        )
    assert opened.registration_token is not None
    form = RegistrationRequest(
        registration_token=opened.registration_token, full_name="New Owner", locale=Locale.EN
    )

    async def register(session: AsyncSession) -> None:
        _slow.set(True)
        await InvitationService(session, get_settings()).register(form, None)

    async def resend(session: AsyncSession) -> None:
        await asyncio.sleep(0.15)  # after the registration started
        await InvitationService(session, get_settings()).resend(
            await caller(session, chairman.id), view.invitation_id
        )

    results = await asyncio.gather(outcome(engine, register), outcome(engine, resend))
    # Registered first: the resend finds it accepted. Resent first: the open form is cancelled.
    assert sorted(results) in (["INVITATION_ACCEPTED", "ok"], ["INVALID_LINK", "ok"])


@pytest.fixture
def slow_link_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transaction marked slow waits just before it deletes an invitation's links."""
    original = InvitationService._drop_links

    async def drop_links(self: InvitationService, invitation_id: uuid.UUID) -> None:
        if _slow.get():
            await asyncio.sleep(0.5)
        await original(self, invitation_id)

    monkeypatch.setattr(InvitationService, "_drop_links", drop_links)


async def test_registering_while_the_link_opens_again(
    engine: AsyncEngine, slow_link_cleanup
) -> None:
    """One tab submits the form while another opens the invitation link again: the invitation is
    locked before its tokens on both paths, so no deadlock."""
    async with unit(engine) as session:
        building = await create_building(session)
        chairman = await create_user(session, building, "Chair", role=BuildingRole.CHAIRMAN)
        await create_apartment(session, building, "2")
    async with unit(engine) as session:
        _, mail = await InvitationService(session, get_settings()).create(
            await caller(session, chairman.id),
            OwnerInvitationCreate(
                kind=InvitationKind.OWNER, email="two.tabs@example.com", apartment_number="2"
            ),
        )
    link = mail.link.rpartition("#")[2]
    async with unit(engine) as session:
        opened, _ = await LinkService(session, get_settings()).redeem(link, "198.51.100.10", None)
    assert opened.registration_token is not None
    form = RegistrationRequest(
        registration_token=opened.registration_token, full_name="Two Tabs", locale=Locale.EN
    )

    async def register(session: AsyncSession) -> None:
        _slow.set(True)
        await InvitationService(session, get_settings()).register(form, None)

    async def reopen(session: AsyncSession) -> None:
        await asyncio.sleep(0.15)  # while the registration holds the invitation
        await LinkService(session, get_settings()).redeem(link, "198.51.100.10", None)

    results = await asyncio.gather(outcome(engine, register), outcome(engine, reopen))
    assert sorted(results) == ["INVALID_LINK", "ok"]  # registered; the used link is refused
