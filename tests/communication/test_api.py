"""Integration tests of the communication API on a real PostgreSQL (see conftest.py)."""

import uuid
from dataclasses import fields
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import DEMO_USER_HEADER
from src.modules.communication.events import EmergencyAlertPublished, EmergencyAlertResolved
from src.modules.communication.models import Post
from src.modules.property.schemas import RelationType
from tests.communication.conftest import CommWorld
from tests.db_utils import failing_commit
from tests.factories import create_membership

API = "/api/v1/communication"


def as_user(user) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


async def publish(client: AsyncClient, user, **overrides: Any) -> Response:
    payload: dict[str, Any] = {
        "post_type": "FORUM_TOPIC",
        "title": "Let's install a bike rack in the yard",
        "body": "Neighbours, what do you think?",
    }
    payload.update(overrides)
    return await client.post(f"{API}/posts", json=payload, headers=as_user(user))


async def emergency(client: AsyncClient, user, **overrides: Any) -> dict:
    defaults = {
        "post_type": "EMERGENCY",
        "category": "WATER_SHUTOFF",
        "title": "Cold water off until 18:00",
        "body": "A riser is being replaced.",
    }
    response = await publish(client, user, **{**defaults, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def active_ids(client: AsyncClient, user) -> list[str]:
    response = await client.get(f"{API}/emergencies/active", headers=as_user(user))
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()]


async def feed_ids(client: AsyncClient, user, **params: Any) -> list[str]:
    response = await client.get(f"{API}/posts", params=params, headers=as_user(user))
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()]


# --- Targeted emergencies ------------------------------------------------------------------------


async def test_entrance_2_emergency_is_visible_to_apt_14_and_hidden_from_apt_2(
    api_client: AsyncClient, world: CommWorld
) -> None:
    # Acceptance criterion of Task 5.
    alert = await emergency(api_client, world.chairman, target_scope="ENTRANCE", target_entrance=2)

    assert await active_ids(api_client, world.owner_14) == [alert["id"]]
    assert await active_ids(api_client, world.tenant_2) == []
    assert alert["id"] not in await feed_ids(api_client, world.tenant_2)
    for hidden in (
        await api_client.get(f"{API}/posts/{alert['id']}", headers=as_user(world.tenant_2)),
        await api_client.post(
            f"{API}/emergencies/{alert['id']}/acknowledge", headers=as_user(world.tenant_2)
        ),
        await api_client.post(
            f"{API}/posts/{alert['id']}/comments",
            json={"body": "Is it us too?"},
            headers=as_user(world.tenant_2),
        ),
    ):
        assert hidden.status_code == 404
        assert hidden.json()["error_code"] == "POST_NOT_FOUND"


async def test_the_board_sees_every_targeted_emergency(
    api_client: AsyncClient, world: CommWorld
) -> None:
    alert = await emergency(api_client, world.board, target_scope="ENTRANCE", target_entrance=1)

    assert await active_ids(api_client, world.chairman) == [alert["id"]]
    assert await active_ids(api_client, world.board) == [alert["id"]]
    assert await active_ids(api_client, world.accountant) == []  # no apartment in entrance 1


async def test_whole_building_emergency_reaches_everyone_in_the_building_only(
    api_client: AsyncClient, world: CommWorld
) -> None:
    alert = await emergency(api_client, world.chairman)

    for user in (world.owner_14, world.tenant_2, world.accountant, world.board):
        assert await active_ids(api_client, user) == [alert["id"]]
    assert await active_ids(api_client, world.other_resident) == []
    other = await api_client.get(
        f"{API}/posts/{alert['id']}", headers=as_user(world.other_resident)
    )
    assert other.status_code == 404


async def test_apartment_list_emergency_reaches_only_the_listed_apartments(
    api_client: AsyncClient, world: CommWorld
) -> None:
    alert = await emergency(
        api_client,
        world.chairman,
        target_scope="APARTMENT_LIST",
        target_apartment_ids=[str(world.apt_2.id), str(world.apt_2.id), str(world.apt_15.id)],
    )

    assert alert["target_apartment_ids"] == [str(world.apt_2.id), str(world.apt_15.id)]
    assert await active_ids(api_client, world.tenant_2) == [alert["id"]]
    assert await active_ids(api_client, world.owner_14) == []


async def test_a_user_with_two_apartments_sees_the_alerts_of_both(
    api_client: AsyncClient, world: CommWorld, db_session: AsyncSession
) -> None:
    await create_membership(db_session, world.owner_14, world.apt_2, relation=RelationType.OWNER)
    entrance_1 = await emergency(
        api_client, world.chairman, target_scope="ENTRANCE", target_entrance=1
    )
    apt_2_only = await emergency(
        api_client,
        world.chairman,
        target_scope="APARTMENT_LIST",
        target_apartment_ids=[str(world.apt_2.id)],
    )

    assert set(await active_ids(api_client, world.owner_14)) == {
        entrance_1["id"],
        apt_2_only["id"],
    }


async def test_residents_and_the_accountant_cannot_publish_board_posts(
    api_client: AsyncClient, world: CommWorld, published_events
) -> None:
    for user in (world.owner_14, world.accountant):
        for post_type in ("EMERGENCY", "OFFICIAL_NOTICE"):
            response = await publish(api_client, user, post_type=post_type)
            assert response.status_code == 403
            assert response.json()["error_code"] == "FORBIDDEN"

    assert published_events == []


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"post_type": "FORUM_TOPIC", "target_scope": "ENTRANCE", "target_entrance": 2}, "whole"),
        ({"target_scope": "ENTRANCE"}, "needs target_entrance"),
        ({"target_scope": "ENTRANCE", "target_entrance": 9}, "no apartments in entrance 9"),
        ({"target_scope": "APARTMENT_LIST", "target_apartment_ids": []}, "at least one"),
        ({"target_scope": "ALL_BUILDING", "target_entrance": 2}, "ALL_BUILDING"),
    ],
)
async def test_inconsistent_or_unknown_targets_are_rejected(
    api_client: AsyncClient, world: CommWorld, published_events, overrides, fragment
) -> None:
    response = await publish(api_client, world.chairman, **{"post_type": "EMERGENCY", **overrides})

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_TARGET"
    assert fragment in response.json()["message"]
    assert published_events == []


async def test_apartments_of_another_building_cannot_be_targeted(
    api_client: AsyncClient, world: CommWorld
) -> None:
    foreign = str(world.other_apartment.id)
    response = await publish(
        api_client,
        world.chairman,
        post_type="EMERGENCY",
        target_scope="APARTMENT_LIST",
        target_apartment_ids=[str(world.apt_2.id), foreign],
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_TARGET"
    assert response.json()["unknown_apartment_ids"] == [foreign]


# --- Emergency events ----------------------------------------------------------------------------


async def test_creating_an_emergency_publishes_an_event_without_free_text_or_pii(
    api_client: AsyncClient, world: CommWorld, published_events
) -> None:
    alert = await emergency(api_client, world.chairman, target_scope="ENTRANCE", target_entrance=2)
    await publish(api_client, world.owner_14)  # forum topics emit nothing

    (event,) = published_events
    assert isinstance(event, EmergencyAlertPublished)
    assert str(event.post_id) == alert["id"]
    assert event.building_id == world.building.id
    assert (event.target_scope, event.target_entrance) == ("ENTRANCE", 2)
    assert event.target_apartment_ids == ()
    assert event.published_by_user_id == world.chairman.id
    assert {"title", "body", "full_name", "email", "phone"}.isdisjoint(
        f.name for f in fields(event)
    )
    assert alert["title"] not in repr(event)


async def test_no_emergency_event_when_the_commit_fails(
    api_client: AsyncClient, world: CommWorld, db_session: AsyncSession, published_events
) -> None:
    async with failing_commit(db_session, Post):
        response = await publish(api_client, world.chairman, post_type="EMERGENCY")

    assert response.status_code == 500
    assert published_events == []
    assert await db_session.scalar(select(func.count()).select_from(Post)) == 0


# --- Acknowledge and resolve ---------------------------------------------------------------------


async def test_acknowledging_is_per_user_and_idempotent(
    api_client: AsyncClient, world: CommWorld
) -> None:
    alert = await emergency(api_client, world.chairman)
    url = f"{API}/emergencies/{alert['id']}/acknowledge"

    first = await api_client.post(url, headers=as_user(world.owner_14))
    again = await api_client.post(url, headers=as_user(world.owner_14))

    assert first.status_code == 200
    assert first.json()["post_id"] == alert["id"]
    assert again.json()["acknowledged_at"] == first.json()["acknowledged_at"]
    mine = await api_client.get(f"{API}/emergencies/active", headers=as_user(world.owner_14))
    theirs = await api_client.get(f"{API}/emergencies/active", headers=as_user(world.tenant_2))
    assert mine.json()[0]["acknowledged_at"] == first.json()["acknowledged_at"]
    assert theirs.json()[0]["acknowledged_at"] is None


async def test_only_emergencies_can_be_acknowledged(
    api_client: AsyncClient, world: CommWorld
) -> None:
    topic = (await publish(api_client, world.owner_14)).json()

    response = await api_client.post(
        f"{API}/emergencies/{topic['id']}/acknowledge", headers=as_user(world.tenant_2)
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "POST_NOT_FOUND"


async def test_resolving_ends_the_banner_once(
    api_client: AsyncClient, world: CommWorld, published_events
) -> None:
    alert = await emergency(api_client, world.chairman)
    url = f"{API}/emergencies/{alert['id']}/resolve"

    by_resident = await api_client.post(url, headers=as_user(world.owner_14))
    resolved = await api_client.post(url, headers=as_user(world.board))
    again = await api_client.post(url, headers=as_user(world.chairman))

    assert by_resident.status_code == 403
    assert resolved.status_code == again.status_code == 200
    assert resolved.json() == {"post_id": alert["id"], "is_active": False}
    assert await active_ids(api_client, world.owner_14) == []
    detail = await api_client.get(f"{API}/posts/{alert['id']}", headers=as_user(world.owner_14))
    assert detail.json()["is_active"] is False, "a resolved alert stays readable in the feed"
    resolutions = [e for e in published_events if isinstance(e, EmergencyAlertResolved)]
    assert len(resolutions) == 1
    assert resolutions[0].resolved_by_user_id == world.board.id


async def test_only_existing_emergencies_of_the_building_can_be_resolved(
    api_client: AsyncClient, world: CommWorld
) -> None:
    topic = (await publish(api_client, world.owner_14)).json()

    for post_id in (topic["id"], str(uuid.uuid4())):
        response = await api_client.post(
            f"{API}/emergencies/{post_id}/resolve", headers=as_user(world.chairman)
        )
        assert response.status_code == 404
        assert response.json()["error_code"] == "POST_NOT_FOUND"


# --- Forum, badges and privacy -------------------------------------------------------------------

PII_FRAGMENTS = ("Ivan", "Petrov", "Mari", "Tamm", "Oleg", "Sidorov", "@example.com", "+372")


def assert_no_pii(response: Response, world: CommWorld) -> None:
    text = response.text
    for fragment in PII_FRAGMENTS:
        assert fragment not in text, f"{fragment!r} leaked"
    for user in (world.owner_14, world.tenant_2, world.chairman):
        assert str(user.id) not in text, "author user ids are never returned"
    assert "author_user_id" not in text


async def test_forum_posts_and_comments_show_masked_badges_without_pii(
    api_client: AsyncClient, world: CommWorld
) -> None:
    # Acceptance criterion of Task 5: "Apt. 14 • OWNER", no names, emails, phones or user ids.
    created = await publish(api_client, world.owner_14)
    topic = created.json()
    tenant_comment = await api_client.post(
        f"{API}/posts/{topic['id']}/comments",
        json={"body": "Good idea, I have two bikes."},
        headers=as_user(world.tenant_2),
    )
    chairman_comment = await api_client.post(
        f"{API}/posts/{topic['id']}/comments",
        json={"body": "The board will put it on the agenda."},
        headers=as_user(world.chairman),
    )
    feed = await api_client.get(f"{API}/posts", headers=as_user(world.tenant_2))
    detail = await api_client.get(f"{API}/posts/{topic['id']}", headers=as_user(world.tenant_2))

    assert created.status_code == tenant_comment.status_code == chairman_comment.status_code == 201
    (item,) = feed.json()
    badge = item["author_badge"]
    assert f"{badge['display_label']} • {badge['role_badge']}" == "Apt. 14 • OWNER"
    assert item["comments_count"] == 2
    assert item["is_mine"] is False
    assert created.json()["is_mine"] is True
    comments = detail.json()["comments"]
    assert [c["author_badge"] for c in comments] == [
        {"display_label": "Apt. 2", "role_badge": "RESIDENT"},
        {"display_label": "Chairman", "role_badge": "BOARD"},
    ]
    assert [c["body"] for c in comments] == [
        "Good idea, I have two bikes.",
        "The board will put it on the agenda.",
    ]
    for response in (created, tenant_comment, chairman_comment, feed, detail):
        assert_no_pii(response, world)


async def test_badges_follow_the_current_privacy_mode(
    api_client: AsyncClient, world: CommWorld
) -> None:
    topic = (await publish(api_client, world.owner_14)).json()
    await api_client.patch(
        f"/api/v1/property/memberships/{world.owner_14_membership.id}/privacy",
        json={"privacy_display_mode": "INITIALS"},
        headers=as_user(world.owner_14),
    )

    detail = await api_client.get(f"{API}/posts/{topic['id']}", headers=as_user(world.tenant_2))

    assert detail.json()["author_badge"] == {
        "display_label": "I. P. (Apt. 14)",
        "role_badge": "OWNER",
    }
    assert "Petrov" not in detail.text
    assert "@example.com" not in detail.text


async def test_board_posts_speak_as_the_board(api_client: AsyncClient, world: CommWorld) -> None:
    notice = await publish(
        api_client,
        world.chairman,
        post_type="OFFICIAL_NOTICE",
        category="MAINTENANCE",
        title="Stairwell painting on Monday",
        body="Please keep the stairwell free.",
    )

    assert notice.status_code == 201
    assert notice.json()["author_badge"] == {"display_label": "Chairman", "role_badge": "BOARD"}
    assert notice.json()["category"] == "MAINTENANCE"


async def test_writing_on_behalf_of_someone_elses_apartment_is_rejected(
    api_client: AsyncClient, world: CommWorld
) -> None:
    topic = await publish(api_client, world.owner_14, author_apartment_id=str(world.apt_2.id))
    comment = await api_client.post(
        f"{API}/posts/{uuid.uuid4()}/comments",
        json={"body": "Hello", "author_apartment_id": str(world.apt_2.id)},
        headers=as_user(world.owner_14),
    )
    own = await publish(api_client, world.owner_14, author_apartment_id=str(world.apt_14.id))

    assert topic.status_code == 422
    assert topic.json()["error_code"] == "INVALID_AUTHOR_APARTMENT"
    assert comment.status_code == 404  # the post is checked first
    assert own.status_code == 201


async def test_comments_can_be_disabled_on_a_notice(
    api_client: AsyncClient, world: CommWorld
) -> None:
    notice = (
        await publish(
            api_client, world.chairman, post_type="OFFICIAL_NOTICE", comments_enabled=False
        )
    ).json()

    response = await api_client.post(
        f"{API}/posts/{notice['id']}/comments",
        json={"body": "Why?"},
        headers=as_user(world.owner_14),
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "COMMENTS_DISABLED"


# --- Free text -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "Water\u0000leak"},
        {"body": "Ring the bell\x07 twice"},
        {"title": "   "},
        {"body": ""},
        {"title": "x" * 256},
        {"body": "x" * 10_001},
        {"comments_enabled": "false"},
        {"unexpected": True},
    ],
)
async def test_invalid_post_input_is_a_validation_error_not_a_500(
    api_client: AsyncClient, world: CommWorld, overrides
) -> None:
    response = await publish(api_client, world.owner_14, **overrides)

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"
    assert "\u0000" not in response.text, "submitted values are not echoed back"


@pytest.mark.parametrize("body", ["Hi\u0000there", "  ", "x" * 4_001])
async def test_invalid_comment_input_is_a_validation_error(
    api_client: AsyncClient, world: CommWorld, body: str
) -> None:
    topic = (await publish(api_client, world.owner_14)).json()

    response = await api_client.post(
        f"{API}/posts/{topic['id']}/comments", json={"body": body}, headers=as_user(world.tenant_2)
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


async def test_titles_and_bodies_are_trimmed(api_client: AsyncClient, world: CommWorld) -> None:
    response = await publish(api_client, world.owner_14, title="  Bike rack \n", body="\tPlease ")

    assert (response.json()["title"], response.json()["body"]) == ("Bike rack", "Please")


# --- Feed ----------------------------------------------------------------------------------------


async def test_feed_filters_and_pages_newest_first(
    api_client: AsyncClient, world: CommWorld
) -> None:
    topics = [
        (await publish(api_client, world.owner_14, title=f"Topic {n}")).json()["id"]
        for n in range(3)
    ]
    market = (await publish(api_client, world.tenant_2, category="MARKETPLACE")).json()["id"]
    notice = (await publish(api_client, world.chairman, post_type="OFFICIAL_NOTICE")).json()["id"]

    newest_first = [market, *reversed(topics)]
    assert await feed_ids(api_client, world.owner_14) == [notice, *newest_first]
    assert await feed_ids(api_client, world.owner_14, post_type="FORUM_TOPIC") == newest_first
    assert await feed_ids(api_client, world.owner_14, category="MARKETPLACE") == [market]
    assert (
        await feed_ids(api_client, world.owner_14, post_type="FORUM_TOPIC", limit=2, offset=1)
        == newest_first[1:3]
    )
    too_big = await api_client.get(
        f"{API}/posts", params={"limit": 101}, headers=as_user(world.owner_14)
    )
    assert too_big.status_code == 422


async def test_unknown_post_and_anonymous_access(api_client: AsyncClient, world: CommWorld) -> None:
    unknown = await api_client.get(f"{API}/posts/{uuid.uuid4()}", headers=as_user(world.owner_14))
    anonymous = await api_client.get(f"{API}/emergencies/active")

    assert unknown.status_code == 404
    assert unknown.json()["error_code"] == "POST_NOT_FOUND"
    assert anonymous.status_code == 401
    assert anonymous.json()["error_code"] == "NOT_AUTHENTICATED"
