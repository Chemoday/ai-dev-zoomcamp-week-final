"""Notifications for board <-> apartment threads (epic E8, task P3; fixture: conftest.py).

Apt 14: owner Ivan Petrov. Apt 15: board member Peeter Saar (resident and board at once).
Apt 2: tenant Mari Tamm. Staff without apartments: the chairman and an accountant.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import DEMO_USER_HEADER
from src.modules.communication.events import ApartmentMessagePosted
from src.modules.notifications.models import Delivery
from src.modules.notifications.schemas import Channel, NotificationEventType
from src.modules.notifications.service import NotificationsSubscriber
from src.modules.property.models import ApartmentMembership, User
from src.modules.property.schemas import BuildingRole, RelationType
from tests.factories import create_membership, create_user
from tests.notifications.conftest import NotifWorld

COMM = "/api/v1/communication"
API = "/api/v1/notifications"


def as_user(user: User) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


async def start_thread(client: AsyncClient, author: User, apartment) -> str:
    response = await client.post(
        f"{COMM}/posts",
        json={
            "post_type": "APARTMENT_THREAD",
            "title": "Debt for October",
            "body": "You owe 120 EUR.",
            "target_scope": "APARTMENT_LIST",
            "target_apartment_ids": [str(apartment.id)],
        },
        headers=as_user(author),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def reply(client: AsyncClient, author: User, post_id: str) -> None:
    response = await client.post(
        f"{COMM}/posts/{post_id}/comments", json={"body": "Paid today."}, headers=as_user(author)
    )
    assert response.status_code == 201, response.text


async def feed(client: AsyncClient, user: User) -> list[dict[str, Any]]:
    response = await client.get(f"{API}/feed", headers=as_user(user))
    assert response.status_code == 200, response.text
    return response.json()


async def kinds(client: AsyncClient, user: User) -> list[tuple[str, bool]]:
    """(event_type, from_board) of the user's notifications, newest first."""
    return [(item["event_type"], item["params"]["from_board"]) for item in await feed(client, user)]


async def test_the_board_writes_and_the_apartment_is_told(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    post_id = await start_thread(api_client, world.chairman, world.apt_14)

    (item,) = await feed(api_client, world.owner_14)
    assert item["event_type"] == "APARTMENT_MESSAGE"
    assert (item["subject_type"], item["subject_id"]) == ("post", post_id)
    assert item["params"] == {
        "apartment_id": str(world.apt_14.id),
        "apartment_number": "14",
        "from_board": True,
    }
    # Never the author, no other board member, nobody outside the apartment.
    for user in (world.chairman, world.board_15, world.tenant_2, world.accountant):
        assert await feed(api_client, user) == []
    assert await feed(api_client, world.other_resident) == []


async def test_a_resident_writes_and_the_board_and_co_residents_are_told(
    api_client: AsyncClient, db_session: AsyncSession, world: NotifWorld
) -> None:
    family = await create_user(db_session, world.building, "Family Member")
    await create_membership(db_session, family, world.apt_14, relation=RelationType.RESIDENT)

    await start_thread(api_client, world.owner_14, world.apt_14)

    assert await kinds(api_client, world.owner_14) == [], "never the author"
    assert await kinds(api_client, family) == [("APARTMENT_MESSAGE", False)]
    for board in (world.chairman, world.board_15):
        assert await kinds(api_client, board) == [("APARTMENT_MESSAGE_TO_BOARD", False)]
    assert await feed(api_client, world.accountant) == []
    assert await feed(api_client, world.tenant_2) == []


async def test_every_reply_notifies_again(api_client: AsyncClient, world: NotifWorld) -> None:
    post_id = await start_thread(api_client, world.owner_14, world.apt_14)
    await reply(api_client, world.chairman, post_id)
    await reply(api_client, world.owner_14, post_id)

    assert await kinds(api_client, world.owner_14) == [("APARTMENT_MESSAGE", True)]
    # The chairman's own reply tells nobody on the board.
    assert await kinds(api_client, world.chairman) == [
        ("APARTMENT_MESSAGE_TO_BOARD", False),
        ("APARTMENT_MESSAGE_TO_BOARD", False),
    ]
    assert await kinds(api_client, world.board_15) == [
        ("APARTMENT_MESSAGE_TO_BOARD", False),
        ("APARTMENT_MESSAGE_TO_BOARD", False),
    ]


async def test_a_board_member_living_in_the_apartment_is_told_once_as_a_resident(
    api_client: AsyncClient, db_session: AsyncSession, world: NotifWorld
) -> None:
    await start_thread(api_client, world.chairman, world.apt_15)
    assert await kinds(api_client, world.board_15) == [("APARTMENT_MESSAGE", True)]

    co_owner = await create_user(db_session, world.building, "Co Owner")
    await create_membership(db_session, co_owner, world.apt_15)
    await start_thread(api_client, co_owner, world.apt_15)

    assert await kinds(api_client, world.board_15) == [
        ("APARTMENT_MESSAGE", False),
        ("APARTMENT_MESSAGE", True),
    ]
    assert (await kinds(api_client, world.chairman))[0] == ("APARTMENT_MESSAGE_TO_BOARD", False)


async def test_only_those_who_see_the_thread_are_told(
    api_client: AsyncClient, db_session: AsyncSession, world: NotifWorld
) -> None:
    post_id = await start_thread(api_client, world.chairman, world.apt_14)
    newcomer = await create_user(db_session, world.building, "New Tenant")
    await create_membership(db_session, newcomer, world.apt_14, relation=RelationType.TENANT)
    gone = await create_user(db_session, world.building, "Former Tenant")
    gone_membership = await create_membership(
        db_session, gone, world.apt_14, relation=RelationType.TENANT
    )
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.id == gone_membership.id)
        .values(started_at=datetime.now(UTC) - timedelta(days=30), ended_at=datetime.now(UTC))
    )

    await reply(api_client, world.board_15, post_id)

    assert await kinds(api_client, world.owner_14) == [
        ("APARTMENT_MESSAGE", True),
        ("APARTMENT_MESSAGE", True),
    ]
    assert await feed(api_client, newcomer) == [], "the thread started before they moved in"
    assert await feed(api_client, gone) == [], "a former tenant"


async def test_one_row_per_message_and_person(
    api_client: AsyncClient, db_session: AsyncSession, world: NotifWorld
) -> None:
    post_id = await start_thread(api_client, world.owner_14, world.apt_14)
    await reply(api_client, world.owner_14, post_id)

    keys = list(
        await db_session.scalars(
            select(Delivery.dedup_key)
            .where(Delivery.user_id == world.chairman.id, Delivery.channel == Channel.IN_APP)
            .order_by(Delivery.created_at)
        )
    )
    assert keys[0] == f"apartment_message:{post_id}"
    assert keys[1].startswith(f"apartment_message:{post_id}:") and len(keys) == 2
    rows = list(await db_session.scalars(select(Delivery).where(Delivery.dedup_key == keys[0])))
    assert {row.event_type for row in rows} == {NotificationEventType.APARTMENT_MESSAGE_TO_BOARD}
    assert all("Debt" not in str(row.params) for row in rows), "no text in the params"


async def test_the_full_preferences_matrix_can_be_sent_back(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    headers = as_user(world.chairman)
    matrix = (await api_client.get(f"{API}/preferences", headers=headers)).json()["preferences"]
    changes = [
        {"event_type": p["event_type"], "channel": p["channel"], "enabled": p["enabled"]}
        for p in matrix
    ]

    assert (
        await api_client.put(f"{API}/preferences", json={"preferences": changes}, headers=headers)
    ).status_code == 200
    too_many = changes + [changes[0]]
    response = await api_client.put(
        f"{API}/preferences", json={"preferences": too_many}, headers=headers
    )
    assert response.status_code == 422


def _subscriber(db_session: AsyncSession) -> NotificationsSubscriber:
    @asynccontextmanager
    async def factory():
        yield db_session

    return NotificationsSubscriber(lambda: factory, "et")


def _posted(
    world: NotifWorld, post_id: uuid.UUID, apartment_id: uuid.UUID
) -> ApartmentMessagePosted:
    return ApartmentMessagePosted(
        post_id=post_id,
        comment_id=None,
        building_id=world.building.id,
        apartment_id=apartment_id,
        author_user_id=world.chairman.id,
        from_board=True,
    )


async def _rows(db_session: AsyncSession, dedup_key: str) -> int:
    return len(
        list(await db_session.scalars(select(Delivery).where(Delivery.dedup_key == dedup_key)))
    )


async def test_an_event_delivered_twice_notifies_once(
    api_client: AsyncClient, db_session: AsyncSession, world: NotifWorld
) -> None:
    post_id = uuid.UUID(await start_thread(api_client, world.chairman, world.apt_14))
    key = f"apartment_message:{post_id}"
    assert await _rows(db_session, key) == 1

    await _subscriber(db_session).on_apartment_message(_posted(world, post_id, world.apt_14.id))

    assert await _rows(db_session, key) == 1, "the relay delivers at least once"


async def test_an_unknown_thread_notifies_nobody(
    db_session: AsyncSession, world: NotifWorld
) -> None:
    missing = uuid.uuid4()

    await _subscriber(db_session).on_apartment_message(_posted(world, missing, world.apt_14.id))

    assert await _rows(db_session, f"apartment_message:{missing}") == 0


async def test_who_left_the_board_is_no_longer_told(
    api_client: AsyncClient, db_session: AsyncSession, world: NotifWorld
) -> None:
    await db_session.execute(
        update(User).where(User.id == world.board_15.id).values(building_role=BuildingRole.USER)
    )

    await start_thread(api_client, world.owner_14, world.apt_14)

    assert await feed(api_client, world.board_15) == []
    assert await kinds(api_client, world.chairman) == [("APARTMENT_MESSAGE_TO_BOARD", False)]


async def test_a_returning_resident_keeps_being_told_about_their_thread(
    api_client: AsyncClient, db_session: AsyncSession, world: NotifWorld
) -> None:
    post_id = await start_thread(api_client, world.owner_14, world.apt_14)
    # Moved out and back: the reopened membership starts after the thread.
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.user_id == world.owner_14.id)
        .values(started_at=datetime.now(UTC) + timedelta(minutes=1))
    )

    await reply(api_client, world.chairman, post_id)

    assert await kinds(api_client, world.owner_14) == [("APARTMENT_MESSAGE", True)]
