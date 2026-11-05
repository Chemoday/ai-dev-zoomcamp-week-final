"""Board <-> apartment threads (epic E8, task P2; fixture: tests/communication/conftest.py)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import DEMO_USER_HEADER
from src.modules.property.models import ApartmentMembership, User
from src.modules.property.schemas import BuildingRole, RelationType
from tests.communication.conftest import CommWorld
from tests.factories import create_membership, create_user

API = "/api/v1/communication"
INBOX = f"{API}/posts?post_type=APARTMENT_THREAD"


def as_user(user: User) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


async def start_thread(
    client: AsyncClient, user: User, apartment_id: uuid.UUID, **overrides: Any
) -> Response:
    payload: dict[str, Any] = {
        "post_type": "APARTMENT_THREAD",
        "title": "Access to the meter",
        "body": "Could you check the cold water meter?",
        "target_scope": "APARTMENT_LIST",
        "target_apartment_ids": [str(apartment_id)],
    }
    payload.update(overrides)
    return await client.post(f"{API}/posts", json=payload, headers=as_user(user))


async def thread(client: AsyncClient, user: User, apartment_id: uuid.UUID) -> dict:
    response = await start_thread(client, user, apartment_id)
    assert response.status_code == 201, response.text
    return response.json()


async def reply(client: AsyncClient, user: User, post_id: str, **overrides: Any) -> Response:
    payload = {"body": "Thank you, will do.", **overrides}
    return await client.post(f"{API}/posts/{post_id}/comments", json=payload, headers=as_user(user))


async def inbox_ids(client: AsyncClient, user: User, query: str = "") -> list[str]:
    response = await client.get(INBOX + query, headers=as_user(user))
    assert response.status_code == 200, response.text
    return [post["id"] for post in response.json()]


async def sees(client: AsyncClient, user: User, post_id: str) -> bool:
    status = (await client.get(f"{API}/posts/{post_id}", headers=as_user(user))).status_code
    assert status in (200, 404)
    return status == 200


async def move_start(session: AsyncSession, membership: ApartmentMembership, **values) -> None:
    await session.execute(
        update(ApartmentMembership).where(ApartmentMembership.id == membership.id).values(**values)
    )


# --- Starting a thread ---------------------------------------------------------------------------


async def test_the_board_writes_to_an_apartment_as_the_board(
    api_client: AsyncClient, world: CommWorld
) -> None:
    for author in (world.chairman, world.board):
        post = await thread(api_client, author, world.apt_14.id)

        assert post["post_type"] == "APARTMENT_THREAD"
        assert post["target_apartment_ids"] == [str(world.apt_14.id)]
        assert post["comments_enabled"] is True
        assert post["last_activity_at"] == post["created_at"]
        assert post["author_badge"]["role_badge"] == "BOARD"
        assert post["author_badge"]["display_label"] in ("Chairman", "Board")
        assert await sees(api_client, world.owner_14, post["id"])
        assert not await sees(api_client, world.tenant_2, post["id"])


async def test_a_resident_writes_to_the_board_from_their_own_apartment(
    api_client: AsyncClient, world: CommWorld
) -> None:
    post = await thread(api_client, world.owner_14, world.apt_14.id)

    assert post["author_badge"] == {"display_label": "Apt. 14", "role_badge": "OWNER"}
    assert post["is_mine"] is True
    for viewer in (world.chairman, world.board):
        assert await sees(api_client, viewer, post["id"])
    for outsider in (world.tenant_2, world.accountant, world.other_chairman):
        assert not await sees(api_client, outsider, post["id"])

    foreign = await start_thread(api_client, world.owner_14, world.apt_2.id)
    assert foreign.status_code == 403
    assert foreign.json()["error_code"] == "FORBIDDEN"
    accountant = await start_thread(api_client, world.accountant, world.apt_14.id)
    assert accountant.status_code == 403


async def test_a_thread_needs_someone_to_read_it(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    empty = await start_thread(api_client, world.chairman, world.apt_15.id)
    assert empty.status_code == 422
    assert empty.json()["error_code"] == "APARTMENT_HAS_NO_RESIDENTS"

    closed = await create_user(db_session, world.building, "Closed Account", is_active=False)
    await create_membership(db_session, closed, world.apt_15)
    still_empty = await start_thread(api_client, world.chairman, world.apt_15.id)
    assert still_empty.json()["error_code"] == "APARTMENT_HAS_NO_RESIDENTS"


async def test_a_thread_targets_exactly_one_apartment_and_takes_replies(
    api_client: AsyncClient, world: CommWorld
) -> None:
    two = [str(world.apt_14.id), str(world.apt_2.id)]
    for overrides in (
        {"target_apartment_ids": two},
        {"target_scope": "ALL_BUILDING", "target_apartment_ids": None},
        {"target_scope": "ENTRANCE", "target_entrance": 2, "target_apartment_ids": None},
    ):
        response = await start_thread(api_client, world.chairman, world.apt_14.id, **overrides)
        assert response.status_code == 422, overrides
        assert response.json()["error_code"] == "INVALID_TARGET"

    unknown = await start_thread(api_client, world.chairman, world.other_apartment.id)
    assert unknown.status_code == 422
    assert unknown.json()["unknown_apartment_ids"] == [str(world.other_apartment.id)]

    closed = await start_thread(api_client, world.chairman, world.apt_14.id, comments_enabled=False)
    assert closed.status_code == 422
    assert closed.json()["error_code"] == "VALIDATION_ERROR"


async def test_a_thread_is_a_record(api_client: AsyncClient, world: CommWorld) -> None:
    post = await thread(api_client, world.owner_14, world.apt_14.id)

    response = await api_client.delete(f"{API}/posts/{post['id']}", headers=as_user(world.chairman))

    assert response.status_code == 409
    assert response.json() == {
        "error_code": "POST_NOT_DELETABLE",
        "message": "Only forum topics can be deleted; resolve an emergency instead.",
        "post_type": "APARTMENT_THREAD",
    }
    acknowledge = await api_client.post(
        f"{API}/emergencies/{post['id']}/acknowledge", headers=as_user(world.owner_14)
    )
    assert acknowledge.status_code == 404


# --- Who writes as whom --------------------------------------------------------------------------


async def test_the_server_decides_who_writes_as_whom(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    post = await thread(api_client, world.chairman, world.apt_14.id)

    board_reply = await reply(api_client, world.board, post["id"])
    resident_reply = await reply(api_client, world.owner_14, post["id"])

    assert board_reply.json()["author_badge"] == {"display_label": "Board", "role_badge": "BOARD"}
    assert resident_reply.json()["author_badge"] == {
        "display_label": "Apt. 14",
        "role_badge": "OWNER",
    }

    # A board member who lives in the apartment writes from it, in threads and replies alike.
    neighbour = await create_user(
        db_session, world.building, "Board Neighbour", role=BuildingRole.BOARD
    )
    await create_membership(db_session, neighbour, world.apt_14, relation=RelationType.TENANT)
    own_thread = await thread(api_client, neighbour, world.apt_14.id)
    own_reply = await reply(api_client, neighbour, post["id"])
    for badge in (own_thread["author_badge"], own_reply.json()["author_badge"]):
        assert badge == {"display_label": "Apt. 14", "role_badge": "BOARD"}


async def test_an_explicit_author_apartment_must_agree_with_the_server(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    post = await thread(api_client, world.chairman, world.apt_14.id)
    # The owner of apartment 14 also owns apartment 2, but answers a thread of apartment 14.
    await create_membership(db_session, world.owner_14, world.apt_2)
    board_member_with_flat = await create_user(
        db_session, world.building, "Board Member", role=BuildingRole.BOARD
    )
    await create_membership(db_session, board_member_with_flat, world.apt_2)

    agreeing = await reply(
        api_client, world.owner_14, post["id"], author_apartment_id=str(world.apt_14.id)
    )
    assert agreeing.status_code == 201
    for user in (world.owner_14, board_member_with_flat):
        response = await reply(
            api_client, user, post["id"], author_apartment_id=str(world.apt_2.id)
        )
        assert response.status_code == 422
        assert response.json()["error_code"] == "INVALID_AUTHOR_APARTMENT"

    starting = await start_thread(
        api_client, world.chairman, world.apt_14.id, author_apartment_id=str(world.apt_2.id)
    )
    assert starting.status_code == 422
    assert starting.json()["error_code"] == "INVALID_AUTHOR_APARTMENT"


# --- Who sees a thread ---------------------------------------------------------------------------


async def test_a_new_resident_sees_only_the_threads_started_after_moving_in(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    old = await thread(api_client, world.chairman, world.apt_2.id)
    newcomer = await create_user(db_session, world.building, "New Tenant")
    await create_membership(db_session, newcomer, world.apt_2, relation=RelationType.TENANT)
    new = await thread(api_client, world.chairman, world.apt_2.id)

    assert await inbox_ids(api_client, newcomer) == [new["id"]]
    assert not await sees(api_client, newcomer, old["id"])
    commenting = await reply(api_client, newcomer, old["id"])
    assert commenting.status_code == 404
    assert await inbox_ids(api_client, world.tenant_2) == [new["id"], old["id"]]


async def test_a_former_resident_loses_access(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    post = await thread(api_client, world.tenant_2, world.apt_2.id)
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.user_id == world.tenant_2.id)
        .values(ended_at=datetime.now(UTC))
    )

    assert not await sees(api_client, world.tenant_2, post["id"])
    assert await inbox_ids(api_client, world.tenant_2) == []
    assert await sees(api_client, world.chairman, post["id"])


async def test_taking_part_keeps_a_thread_after_the_membership_is_reopened(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    co_owner = await create_user(db_session, world.building, "Co Owner")
    co_membership = await create_membership(db_session, co_owner, world.apt_14)
    started = await thread(api_client, world.owner_14, world.apt_14.id)
    answered = await thread(api_client, world.chairman, world.apt_14.id)
    assert (await reply(api_client, co_owner, answered["id"])).status_code == 201
    silent = await thread(api_client, world.chairman, world.apt_14.id)

    # Reopening a membership moves `since` to now: only the threads they wrote in stay.
    later = datetime.now(UTC) + timedelta(days=1)
    await move_start(db_session, world.owner_14_membership, started_at=later)
    await move_start(db_session, co_membership, started_at=later)

    assert await inbox_ids(api_client, world.owner_14) == [started["id"]]
    assert await inbox_ids(api_client, co_owner) == [answered["id"]]
    assert not await sees(api_client, co_owner, silent["id"])


async def test_threads_stay_out_of_the_forum_feed(
    api_client: AsyncClient, world: CommWorld
) -> None:
    post = await thread(api_client, world.chairman, world.apt_14.id)
    topic = await api_client.post(
        f"{API}/posts",
        json={"post_type": "FORUM_TOPIC", "title": "Bike rack", "body": "Ideas?"},
        headers=as_user(world.owner_14),
    )

    for viewer in (world.chairman, world.owner_14):
        feed = (await api_client.get(f"{API}/posts", headers=as_user(viewer))).json()
        assert [item["id"] for item in feed] == [topic.json()["id"]]
        assert await inbox_ids(api_client, viewer) == [post["id"]]


# --- The inbox -----------------------------------------------------------------------------------


async def test_the_inbox_sorts_by_the_newest_activity(
    api_client: AsyncClient, world: CommWorld
) -> None:
    first = await thread(api_client, world.chairman, world.apt_14.id)
    second = await thread(api_client, world.chairman, world.apt_14.id)
    assert await inbox_ids(api_client, world.owner_14) == [second["id"], first["id"]]

    comment = (await reply(api_client, world.owner_14, first["id"])).json()

    assert await inbox_ids(api_client, world.owner_14) == [first["id"], second["id"]]
    detail = (
        await api_client.get(f"{API}/posts/{first['id']}", headers=as_user(world.chairman))
    ).json()
    assert detail["last_activity_at"] == comment["created_at"]
    assert detail["created_at"] == first["created_at"]


async def test_the_board_narrows_the_inbox_to_one_apartment(
    api_client: AsyncClient, world: CommWorld
) -> None:
    to_14 = await thread(api_client, world.chairman, world.apt_14.id)
    to_2 = await thread(api_client, world.chairman, world.apt_2.id)

    assert await inbox_ids(api_client, world.board) == [to_2["id"], to_14["id"]]
    assert await inbox_ids(api_client, world.board, f"&apartment_id={world.apt_14.id}") == [
        to_14["id"]
    ]
    assert await inbox_ids(api_client, world.owner_14, f"&apartment_id={world.apt_2.id}") == []

    response = await api_client.get(
        f"{API}/posts?apartment_id={world.apt_14.id}", headers=as_user(world.board)
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert response.json()["errors"][0]["loc"] == ["query", "apartment_id"]


async def test_another_building_never_sees_the_threads(
    api_client: AsyncClient, world: CommWorld
) -> None:
    post = await thread(api_client, world.chairman, world.apt_14.id)

    assert await inbox_ids(api_client, world.other_chairman) == []
    assert not await sees(api_client, world.other_chairman, post["id"])
    assert (await reply(api_client, world.other_chairman, post["id"])).status_code == 404


async def test_writing_as_the_board_does_not_keep_a_thread_for_a_later_resident(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    post = await thread(api_client, world.owner_14, world.apt_14.id)
    assert (await reply(api_client, world.board, post["id"])).status_code == 201
    # The board member leaves the board and later moves into apartment 14.
    await db_session.execute(
        update(User).where(User.id == world.board.id).values(building_role=BuildingRole.USER)
    )
    await create_membership(db_session, world.board, world.apt_14, relation=RelationType.TENANT)

    assert not await sees(api_client, world.board, post["id"])
    assert await inbox_ids(api_client, world.board) == []


async def test_a_co_resident_sees_a_neighbours_thread_and_outsiders_do_not(
    api_client: AsyncClient, world: CommWorld
) -> None:
    # tenant_2 lives in apartment 2 and has been a member since before the thread.
    post = await thread(api_client, world.owner_14, world.apt_14.id)

    assert await sees(api_client, world.chairman, post["id"])
    assert (await reply(api_client, world.tenant_2, post["id"])).status_code == 404
    assert await inbox_ids(api_client, world.tenant_2, f"&apartment_id={world.apt_14.id}") == []


async def test_a_co_resident_who_moved_in_earlier_sees_the_thread(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    co_owner = await create_user(db_session, world.building, "Co Owner")
    await create_membership(db_session, co_owner, world.apt_14)
    post = await thread(api_client, world.owner_14, world.apt_14.id)

    assert await inbox_ids(api_client, co_owner) == [post["id"]]
    answer = await reply(api_client, co_owner, post["id"])
    assert answer.json()["author_badge"] == {"display_label": "Apt. 14", "role_badge": "OWNER"}


async def test_an_accountant_takes_part_only_as_a_member_of_an_apartment(
    api_client: AsyncClient, db_session: AsyncSession, world: CommWorld
) -> None:
    before = await thread(api_client, world.chairman, world.apt_14.id)
    assert await inbox_ids(api_client, world.accountant) == []
    await create_membership(
        db_session, world.accountant, world.apt_14, relation=RelationType.TENANT
    )
    after = await thread(api_client, world.chairman, world.apt_14.id)

    own = await thread(api_client, world.accountant, world.apt_14.id)

    assert own["author_badge"] == {"display_label": "Apt. 14", "role_badge": "ACCOUNTANT"}
    assert await inbox_ids(api_client, world.accountant) == [own["id"], after["id"]]
    assert not await sees(api_client, world.accountant, before["id"])


async def test_the_apartment_filter_belongs_to_the_inbox_only(
    api_client: AsyncClient, world: CommWorld
) -> None:
    response = await api_client.get(
        f"{API}/posts?post_type=FORUM_TOPIC&apartment_id={world.apt_14.id}",
        headers=as_user(world.chairman),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
