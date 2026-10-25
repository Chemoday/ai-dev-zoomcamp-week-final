"""PostgreSQL guards of the comm_* tables: no code path can store an inconsistent post."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.communication.conftest import CommWorld

INSERT_POST = text(
    """
    INSERT INTO comm_posts (id, building_id, author_user_id, post_type, title, body,
                            target_scope, target_entrance, target_apartment_ids, is_active,
                            deleted_at, deleted_by_user_id)
    VALUES (:id, :building_id, :author, :post_type, :title, 'Body', :scope, :entrance,
            CAST(:apartments AS jsonb), :is_active, :deleted_at, :deleted_by_user_id)
    """
)


async def insert_post(session: AsyncSession, world: CommWorld, **overrides) -> None:
    values = {
        "id": uuid.uuid4(),
        "building_id": world.building.id,
        "author": world.chairman.id,
        "post_type": "EMERGENCY",
        "title": "Title",
        "scope": "ALL_BUILDING",
        "entrance": None,
        "apartments": None,
        "is_active": True,
        "deleted": False,
        "deleted_by": False,
    }
    values.update(overrides)
    values["deleted_at"] = datetime.now(UTC) if values.pop("deleted") else None
    values["deleted_by_user_id"] = values["author"] if values.pop("deleted_by") else None
    async with session.begin_nested():
        await session.execute(INSERT_POST, values)


async def test_a_consistent_apartment_list_is_accepted(
    db_session: AsyncSession, world: CommWorld
) -> None:
    await insert_post(db_session, world, scope="APARTMENT_LIST", apartments=f'["{world.apt_2.id}"]')


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"scope": "APARTMENT_LIST"}, "ck_comm_posts_target_consistent"),  # NULL list
        ({"scope": "APARTMENT_LIST", "apartments": "[]"}, "ck_comm_posts_target_consistent"),
        ({"scope": "APARTMENT_LIST", "apartments": "{}"}, "ck_comm_posts_target_consistent"),
        ({"scope": "APARTMENT_LIST", "apartments": "null"}, "ck_comm_posts_target_consistent"),
        ({"scope": "APARTMENT_LIST", "apartments": "[1]"}, "ck_comm_posts_target_consistent"),
        (
            {"scope": "APARTMENT_LIST", "apartments": '["not-a-uuid"]'},
            "ck_comm_posts_target_consistent",
        ),
        ({"scope": "ENTRANCE"}, "ck_comm_posts_target_consistent"),
        ({"scope": "ALL_BUILDING", "entrance": 2}, "ck_comm_posts_target_consistent"),
        ({"scope": "ENTRANCE", "entrance": 0}, "ck_comm_posts_target_entrance_positive"),
        (
            {"post_type": "FORUM_TOPIC", "scope": "ENTRANCE", "entrance": 2},
            "ck_comm_posts_forum_whole_building",
        ),
        (
            {"post_type": "FORUM_TOPIC", "is_active": False},
            "ck_comm_posts_only_emergency_inactive",
        ),
        ({"deleted": True, "deleted_by": True}, "ck_comm_posts_only_forum_deleted"),
        (
            {"post_type": "FORUM_TOPIC", "deleted": True},
            "ck_comm_posts_deleted_consistent",
        ),
        ({"title": "   "}, "ck_comm_posts_title_not_blank"),
        ({"post_type": "POLL"}, "ck_comm_posts_post_type"),
    ],
)
async def test_inconsistent_posts_are_rejected_by_the_database(
    db_session: AsyncSession, world: CommWorld, overrides, constraint
) -> None:
    with pytest.raises(IntegrityError) as caught:
        await insert_post(db_session, world, **overrides)

    assert constraint in str(caught.value)


async def test_a_comment_cannot_point_to_a_post_of_another_building(
    db_session: AsyncSession, world: CommWorld
) -> None:
    post_id = uuid.uuid4()
    await insert_post(db_session, world, id=post_id)

    with pytest.raises(IntegrityError) as caught:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO comm_comments (id, post_id, building_id, author_user_id, body) "
                    "VALUES (:id, :post_id, :building_id, :author, 'Hi')"
                ),
                {
                    "id": uuid.uuid4(),
                    "post_id": post_id,
                    "building_id": world.other_apartment.building_id,
                    "author": world.other_resident.id,
                },
            )

    assert "fk_comm_comments_post_building" in str(caught.value)
