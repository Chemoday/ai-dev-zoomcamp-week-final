"""PostgreSQL guards of the gov_* tables: no code path can store an inconsistent poll."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.governance.conftest import PollWorld

INSERT_POLL = text(
    """
    INSERT INTO gov_polls (id, building_id, created_by_user_id, kind, title, target_scope,
                           eligibility, unit, status, closes_at, opened_at, closed_at)
    VALUES (:id, :building, :author, 'INFORMATIVE', 'T', 'ALL_BUILDING', 'RESIDENTS',
            'APARTMENT', :status, :closes_at, :opened_at, :closed_at)
    """
)


async def insert_poll(session: AsyncSession, world: PollWorld, **values) -> uuid.UUID:
    now = datetime.now(UTC)
    row = {
        "id": uuid.uuid4(),
        "building": world.building.id,
        "author": world.chairman.id,
        "status": "DRAFT",
        "closes_at": now + timedelta(days=1),
        "opened_at": None,
        "closed_at": None,
        **values,
    }
    async with session.begin_nested():
        await session.execute(INSERT_POLL, row)
    return row["id"]


@pytest.mark.parametrize(
    "values",
    [
        {"status": "OPEN"},
        {"status": "DRAFT", "opened_at": datetime.now(UTC)},
        {"status": "CLOSED", "opened_at": datetime.now(UTC)},
    ],
)
async def test_status_and_times_agree(db_session: AsyncSession, world: PollWorld, values) -> None:
    with pytest.raises(IntegrityError) as caught:
        await insert_poll(db_session, world, **values)

    assert "ck_gov_polls_status_times" in str(caught.value)


async def test_a_unit_is_an_apartment_or_a_person(
    db_session: AsyncSession, world: PollWorld
) -> None:
    poll_id = await insert_poll(db_session, world, status="OPEN", opened_at=datetime.now(UTC))
    for apartment, user in ((None, None), (world.apt_14.id, world.owner_14.id)):
        with pytest.raises(IntegrityError) as caught:
            async with db_session.begin_nested():
                await db_session.execute(
                    text(
                        "INSERT INTO gov_poll_electorate (id, poll_id, building_id, apartment_id, "
                        "user_id) VALUES (:id, :poll, :building, :apartment, :user)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "poll": poll_id,
                        "building": world.building.id,
                        "apartment": apartment,
                        "user": user,
                    },
                )
        assert "ck_gov_poll_electorate_one_unit" in str(caught.value)


async def test_an_elector_apartment_stays_in_the_polls_building(
    db_session: AsyncSession, world: PollWorld
) -> None:
    poll_id = await insert_poll(db_session, world, status="OPEN", opened_at=datetime.now(UTC))
    other_apartment = await db_session.scalar(
        text(
            "SELECT a.id FROM property_apartments a "
            "JOIN core_users u ON u.building_id = a.building_id WHERE u.id = :user"
        ),
        {"user": world.other_resident.id},
    )

    with pytest.raises(IntegrityError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO gov_poll_electorate (id, poll_id, building_id, apartment_id) "
                    "VALUES (:id, :poll, :building, :apartment)"
                ),
                {
                    "id": uuid.uuid4(),
                    "poll": poll_id,
                    "building": world.building.id,
                    "apartment": other_apartment,
                },
            )

    assert "fk_gov_poll_electorate_apartment_building" in str(caught.value)
