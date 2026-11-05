"""The board's micro-forum (epic E8, task P4; fixture: tests/communication/conftest.py).

The chairman is Oleg Sidorov, the board member Peeter Saar (no apartment), the owner of Apt 14
Ivan Petrov, the accountant Anna Kask.
"""

from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.domain_events import DomainEventRecord
from src.core.security import DEMO_USER_HEADER
from src.modules.communication.events import PostDeleted
from src.modules.communication.models import Post
from src.modules.communication.schemas import PostType
from src.modules.notifications.models import Delivery
from src.modules.property.models import User
from src.modules.property.schemas import BuildingRole
from tests.communication.conftest import CommWorld
from tests.factories import create_membership

API = "/api/v1/communication"
ROOM = f"{API}/posts?post_type=BOARD_TOPIC"


def as_user(user: User) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


async def start_topic(client: AsyncClient, author: User, **overrides: Any) -> Response:
    payload = {
        "post_type": "BOARD_TOPIC",
        "title": "Where the basement key is",
        "body": "In the board's box in entrance 1.",
        **overrides,
    }
    return await client.post(f"{API}/posts", json=payload, headers=as_user(author))


async def topic(client: AsyncClient, author: User) -> dict:
    response = await start_topic(client, author)
    assert response.status_code == 201, response.text
    return response.json()


async def comment(client: AsyncClient, author: User, post_id: str, **extra: Any) -> Response:
    return await client.post(
        f"{API}/posts/{post_id}/comments",
        json={"body": "Took it on Monday.", **extra},
        headers=as_user(author),
    )


async def room_ids(client: AsyncClient, user: User) -> list[str]:
    response = await client.get(ROOM, headers=as_user(user))
    assert response.status_code == 200, response.text
    return [post["id"] for post in response.json()]


async def test_the_board_writes_by_name(api_client: AsyncClient, world: CommWorld) -> None:
    post = await topic(api_client, world.chairman)
    answer = await comment(api_client, world.board, post["id"])

    assert post["post_type"] == "BOARD_TOPIC" and post["target_scope"] == "ALL_BUILDING"
    assert post["author_badge"] == {"display_label": "Oleg Sidorov", "role_badge": "BOARD"}
    assert answer.status_code == 201
    assert answer.json()["author_badge"] == {"display_label": "Peeter Saar", "role_badge": "BOARD"}
    detail = (
        await api_client.get(f"{API}/posts/{post['id']}", headers=as_user(world.board))
    ).json()
    assert [c["author_badge"]["display_label"] for c in detail["comments"]] == ["Peeter Saar"]
    listed = (await api_client.get(ROOM, headers=as_user(world.board))).json()
    assert listed[0]["author_badge"]["display_label"] == "Oleg Sidorov"


async def test_a_board_member_with_an_apartment_still_writes_as_themselves(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    await create_membership(db_session, world.board, world.apt_15)

    post = await topic(api_client, world.board)
    assert post["author_badge"]["display_label"] == "Peeter Saar", "never an apartment"

    for response in (
        await start_topic(api_client, world.board, author_apartment_id=str(world.apt_15.id)),
        await comment(
            api_client, world.board, post["id"], author_apartment_id=str(world.apt_15.id)
        ),
    ):
        assert response.status_code == 422
        assert response.json()["error_code"] == "INVALID_AUTHOR_APARTMENT"


async def test_only_the_board_sees_the_micro_forum(
    api_client: AsyncClient, world: CommWorld
) -> None:
    post = await topic(api_client, world.board)

    for outsider in (world.owner_14, world.tenant_2, world.accountant, world.other_chairman):
        assert await room_ids(api_client, outsider) == []
        detail = await api_client.get(f"{API}/posts/{post['id']}", headers=as_user(outsider))
        assert detail.status_code == 404
        assert (await comment(api_client, outsider, post["id"])).status_code == 404
    for outsider in (world.owner_14, world.accountant):
        response = await start_topic(api_client, outsider)
        assert response.status_code == 403
        assert response.json()["error_code"] == "FORBIDDEN"
    assert await room_ids(api_client, world.chairman) == [post["id"]]


async def test_who_leaves_the_board_loses_the_micro_forum(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    post = await topic(api_client, world.board)
    await db_session.execute(
        update(User).where(User.id == world.board.id).values(building_role=BuildingRole.USER)
    )

    assert await room_ids(api_client, world.board) == []
    url = f"{API}/posts/{post['id']}"
    assert (await api_client.get(url, headers=as_user(world.board))).status_code == 404
    assert (await comment(api_client, world.board, post["id"])).status_code == 404
    assert (await api_client.delete(url, headers=as_user(world.board))).status_code == 403
    # The topic stays with the board, still by its author's name.
    (listed,) = (await api_client.get(ROOM, headers=as_user(world.chairman))).json()
    assert listed["id"] == post["id"]
    assert listed["author_badge"]["display_label"] == "Peeter Saar"


async def test_a_closed_account_shows_as_former_resident(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    await topic(api_client, world.board)
    await db_session.execute(
        update(User)
        .where(User.id == world.board.id)
        .values(full_name="Former resident", is_active=False)
    )

    (listed,) = (await api_client.get(ROOM, headers=as_user(world.chairman))).json()
    assert listed["author_badge"]["display_label"] == "Former resident"


async def test_board_topics_stay_out_of_the_forum_and_sort_by_activity(
    api_client: AsyncClient, world: CommWorld
) -> None:
    first = await topic(api_client, world.chairman)
    second = await topic(api_client, world.board)
    assert await room_ids(api_client, world.chairman) == [second["id"], first["id"]]

    assert (await comment(api_client, world.board, first["id"])).status_code == 201

    assert await room_ids(api_client, world.chairman) == [first["id"], second["id"]]
    feed = (await api_client.get(f"{API}/posts", headers=as_user(world.chairman))).json()
    assert {item["id"] for item in feed}.isdisjoint({first["id"], second["id"]})


async def test_a_board_topic_takes_no_target(api_client: AsyncClient, world: CommWorld) -> None:
    response = await start_topic(
        api_client, world.chairman, target_scope="ENTRANCE", target_entrance=2
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_TARGET"


async def test_any_board_member_deletes_a_topic_with_a_record(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld, published_events
) -> None:
    post = await topic(api_client, world.chairman)
    url = f"{API}/posts/{post['id']}"

    for outsider in (world.owner_14, world.accountant):
        assert (await api_client.delete(url, headers=as_user(outsider))).status_code == 403
    deleted = await api_client.delete(url, headers=as_user(world.board))

    assert deleted.status_code == 204
    for user in (world.chairman, world.board):
        assert (await api_client.get(url, headers=as_user(user))).status_code == 404
    assert (await api_client.delete(url, headers=as_user(world.chairman))).status_code == 404
    row = await db_session.scalar(
        select(Post).where(Post.id == post["id"]).execution_options(populate_existing=True)
    )
    assert row.deleted_by_user_id == world.board.id and row.deleted_at is not None
    events = [e for e in published_events if isinstance(e, PostDeleted)]
    assert [(e.post_type, e.deleted_by_user_id) for e in events] == [
        (PostType.BOARD_TOPIC, world.board.id)
    ]


async def test_forum_topics_stay_with_the_chairman_and_their_deletion_is_recorded(
    api_client: AsyncClient, world: CommWorld, published_events
) -> None:
    forum = await api_client.post(
        f"{API}/posts",
        json={"post_type": "FORUM_TOPIC", "title": "Bike rack", "body": "Ideas?"},
        headers=as_user(world.owner_14),
    )
    url = f"{API}/posts/{forum.json()['id']}"

    refused = await api_client.delete(url, headers=as_user(world.board))
    assert refused.status_code == 403
    assert refused.json()["error_code"] == "FORBIDDEN"
    assert (await api_client.delete(url, headers=as_user(world.chairman))).status_code == 204
    (event,) = [e for e in published_events if isinstance(e, PostDeleted)]
    assert (event.post_type, event.deleted_by_user_id) == (PostType.FORUM_TOPIC, world.chairman.id)


async def test_board_topics_notify_nobody(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld, published_events
) -> None:
    post = await topic(api_client, world.chairman)
    await comment(api_client, world.board, post["id"])

    assert published_events == []
    assert await db_session.scalar(select(func.count()).select_from(Delivery)) == 0


async def test_another_building_cannot_delete_a_board_topic(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    post = await topic(api_client, world.chairman)

    response = await api_client.delete(
        f"{API}/posts/{post['id']}", headers=as_user(world.other_chairman)
    )

    assert response.status_code == 404
    row = await db_session.scalar(
        select(Post).where(Post.id == post["id"]).execution_options(populate_existing=True)
    )
    assert row.deleted_at is None


async def test_a_board_member_cannot_delete_records(
    api_client: AsyncClient, world: CommWorld
) -> None:
    notice = await api_client.post(
        f"{API}/posts",
        json={"post_type": "OFFICIAL_NOTICE", "title": "Meeting", "body": "On Monday."},
        headers=as_user(world.chairman),
    )
    thread = await api_client.post(
        f"{API}/posts",
        json={
            "post_type": "APARTMENT_THREAD",
            "title": "Meter",
            "body": "Please check it.",
            "target_scope": "APARTMENT_LIST",
            "target_apartment_ids": [str(world.apt_14.id)],
        },
        headers=as_user(world.chairman),
    )

    for post in (notice.json(), thread.json()):
        response = await api_client.delete(
            f"{API}/posts/{post['id']}", headers=as_user(world.board)
        )
        assert response.status_code == 409
        assert response.json()["post_type"] == post["post_type"]


async def test_a_deletion_is_stored_in_the_buildings_event_history(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    post = await topic(api_client, world.chairman)

    await api_client.delete(f"{API}/posts/{post['id']}", headers=as_user(world.board))

    (record,) = (
        await db_session.scalars(
            select(DomainEventRecord).where(DomainEventRecord.event_type == "PostDeleted")
        )
    ).all()
    assert record.building_id == world.building.id
    assert record.payload["post_id"] == post["id"]
    assert record.payload["post_type"] == "BOARD_TOPIC"
    assert record.payload["deleted_by_user_id"] == str(world.board.id)
