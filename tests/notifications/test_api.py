"""Integration tests of in-app notifications on a real PostgreSQL: from domain events to the
feed, the feed API, preferences and the notification language (docs/API.md section 6)."""

import uuid
from contextlib import asynccontextmanager
from typing import Any

from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings
from src.core.security import DEMO_USER_HEADER
from src.main import create_app
from src.modules.communication.events import EmergencyAlertPublished
from src.modules.communication.schemas import PostCategory
from src.modules.notifications.models import Delivery, Preference
from src.modules.notifications.service import NotificationsSubscriber
from src.modules.property.schemas import TargetScope
from tests.notifications.conftest import NotifWorld

API = "/api/v1/notifications"


def as_user(user) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user.id)}


async def emergency(client: AsyncClient, author, **overrides: Any) -> str:
    payload: dict[str, Any] = {
        "post_type": "EMERGENCY",
        "category": "WATER_SHUTOFF",
        "title": "Cold water off until 18:00",
        "body": "A riser in entrance 2 is being replaced.",
        "target_scope": "ENTRANCE",
        "target_entrance": 2,
        **overrides,
    }
    response = await client.post(
        "/api/v1/communication/posts", json=payload, headers=as_user(author)
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def feed(client: AsyncClient, user, **params: Any) -> list[dict[str, Any]]:
    response = await client.get(f"{API}/feed", params=params, headers=as_user(user))
    assert response.status_code == 200, response.text
    return response.json()


async def event_types(client: AsyncClient, user) -> list[str]:
    return [item["event_type"] for item in await feed(client, user)]


# --- From events to in-app notifications ----------------------------------------------------------


async def test_an_entrance_alert_reaches_its_residents_and_the_board_once_each(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    post_id = await emergency(api_client, world.chairman)

    owner_feed = await feed(api_client, world.owner_14)
    assert owner_feed == [
        {
            "id": owner_feed[0]["id"],
            "event_type": "EMERGENCY_PUBLISHED",
            "subject_type": "post",
            "subject_id": post_id,
            "params": {
                "category": "WATER_SHUTOFF",
                "target_scope": "ENTRANCE",
                "target_entrance": 2,
            },
            "created_at": owner_feed[0]["created_at"],
            "read_at": None,
        }
    ]
    # Resident of Apt 15 and board member: one notification, not two.
    assert await event_types(api_client, world.board_15) == ["EMERGENCY_PUBLISHED"]
    assert await event_types(api_client, world.tenant_2) == [], "entrance 1 is not targeted"
    assert await event_types(api_client, world.chairman) == [], "never the author"
    assert await event_types(api_client, world.accountant) == [], "staff only by role rules"
    assert await event_types(api_client, world.other_resident) == []


async def test_recipients_match_who_sees_the_alert_in_the_app(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    # The shared targeting rule: residents notified == residents who see the banner.
    await emergency(api_client, world.chairman)

    for resident in (world.owner_14, world.tenant_2):
        banner = await api_client.get(
            "/api/v1/communication/emergencies/active", headers=as_user(resident)
        )
        sees = bool(banner.json())
        notified = bool(await feed(api_client, resident))
        assert sees == notified, resident.full_name


async def test_an_inactive_user_is_not_notified(
    api_client: AsyncClient, world: NotifWorld, db_session: AsyncSession
) -> None:
    await emergency(api_client, world.chairman)

    count = await db_session.scalar(
        select(func.count()).select_from(Delivery).where(Delivery.user_id == world.inactive_16.id)
    )
    assert count == 0


async def test_a_building_wide_alert_by_the_board(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    await emergency(api_client, world.board_15, target_scope="ALL_BUILDING", target_entrance=None)

    assert await event_types(api_client, world.owner_14) == ["EMERGENCY_PUBLISHED"]
    assert await event_types(api_client, world.tenant_2) == ["EMERGENCY_PUBLISHED"]
    assert await event_types(api_client, world.chairman) == ["EMERGENCY_PUBLISHED"], "by role"
    assert await event_types(api_client, world.board_15) == [], "never the author"
    assert await event_types(api_client, world.accountant) == []


async def test_an_apartment_list_alert(api_client: AsyncClient, world: NotifWorld) -> None:
    await emergency(
        api_client,
        world.chairman,
        target_scope="APARTMENT_LIST",
        target_entrance=None,
        target_apartment_ids=[str(world.apt_2.id)],
    )

    assert await event_types(api_client, world.tenant_2) == ["EMERGENCY_PUBLISHED"]
    assert await event_types(api_client, world.owner_14) == []
    assert await event_types(api_client, world.board_15) == ["EMERGENCY_PUBLISHED"], "by role"


async def test_resolving_tells_the_people_who_were_told_except_the_resolver(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    post_id = await emergency(api_client, world.chairman)

    resolved = await api_client.post(
        f"/api/v1/communication/emergencies/{post_id}/resolve", headers=as_user(world.board_15)
    )

    assert resolved.status_code == 200
    assert await event_types(api_client, world.owner_14) == [
        "EMERGENCY_RESOLVED",
        "EMERGENCY_PUBLISHED",
    ]
    assert await event_types(api_client, world.board_15) == ["EMERGENCY_PUBLISHED"]
    assert await event_types(api_client, world.tenant_2) == []


async def test_a_resolved_notification_respects_the_preference(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    switched_off = await api_client.put(
        f"{API}/preferences",
        json={
            "preferences": [
                {"event_type": "EMERGENCY_RESOLVED", "channel": "in_app", "enabled": False}
            ]
        },
        headers=as_user(world.owner_14),
    )
    assert switched_off.status_code == 200
    post_id = await emergency(api_client, world.chairman)
    await api_client.post(
        f"/api/v1/communication/emergencies/{post_id}/resolve", headers=as_user(world.chairman)
    )

    assert await event_types(api_client, world.owner_14) == ["EMERGENCY_PUBLISHED"]


async def test_a_period_in_review_notifies_the_chairman_and_accountants_except_the_actor(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    async def set_status(status: str) -> Response:
        return await api_client.patch(
            f"/api/v1/meters/periods/{world.period.period_code}/status",
            json={"status": status},
            headers=as_user(world.chairman),
        )

    assert (await set_status("REVIEW_BY_BOARD")).status_code == 200
    accountant_feed = await feed(api_client, world.accountant)
    assert [(n["event_type"], n["subject_type"], n["params"]) for n in accountant_feed] == [
        ("PERIOD_REVIEW", "period", {"period_code": world.period.period_code})
    ]
    assert await event_types(api_client, world.chairman) == [], "never the actor"
    assert await event_types(api_client, world.owner_14) == []

    # Reopened and sent to review again: a new notification.
    assert (await set_status("OPEN_FOR_RESIDENTS")).status_code == 200
    assert (await set_status("REVIEW_BY_BOARD")).status_code == 200
    assert await event_types(api_client, world.accountant) == ["PERIOD_REVIEW", "PERIOD_REVIEW"]


async def test_a_replayed_event_creates_no_duplicates(
    db_session: AsyncSession, world: NotifWorld
) -> None:
    @asynccontextmanager
    async def factory():
        yield db_session

    subscriber = NotificationsSubscriber(lambda: factory, "et")
    event = EmergencyAlertPublished(
        post_id=uuid.uuid4(),
        building_id=world.building.id,
        category=PostCategory.WATER_SHUTOFF,
        target_scope=TargetScope.ALL_BUILDING,
        target_entrance=None,
        target_apartment_ids=(),
        published_by_user_id=world.chairman.id,
    )

    await subscriber.on_emergency_published(event)
    await subscriber.on_emergency_published(event)  # the relay delivers at least once

    rows = list(
        await db_session.scalars(
            select(Delivery.user_id).where(Delivery.subject_id == event.post_id)
        )
    )
    assert sorted(map(str, rows)) == sorted(
        map(str, [world.owner_14.id, world.tenant_2.id, world.board_15.id])
    )


async def test_external_channels_get_no_rows_yet(
    api_client: AsyncClient, world: NotifWorld, db_session: AsyncSession
) -> None:
    await emergency(api_client, world.chairman)

    channels = set(await db_session.scalars(select(Delivery.channel).distinct()))
    assert channels == {"in_app"}


async def test_the_feed_holds_no_names_or_texts(api_client: AsyncClient, world: NotifWorld) -> None:
    await emergency(api_client, world.chairman)

    response = await api_client.get(f"{API}/feed", headers=as_user(world.owner_14))

    for secret in ("Ivan", "Petrov", "Oleg", "Cold water off", "riser", "@example.com"):
        assert secret not in response.text


# --- The feed API ---------------------------------------------------------------------------------


async def test_reading_notifications(api_client: AsyncClient, world: NotifWorld) -> None:
    await emergency(api_client, world.chairman)
    await emergency(api_client, world.chairman, title="Second alert")
    headers = as_user(world.owner_14)

    assert (await api_client.get(f"{API}/unread-count", headers=headers)).json() == {"unread": 2}
    first = (await feed(api_client, world.owner_14))[0]["id"]

    read = await api_client.post(f"{API}/feed/{first}/read", headers=headers)
    again = await api_client.post(f"{API}/feed/{first}/read", headers=headers)
    assert read.status_code == again.status_code == 200
    assert read.json()["read_at"] == again.json()["read_at"], "the first read time is kept"

    assert [n["id"] for n in await feed(api_client, world.owner_14, unread_only=True)] != [first]
    assert (await api_client.get(f"{API}/unread-count", headers=headers)).json() == {"unread": 1}
    assert (await api_client.post(f"{API}/feed/read-all", headers=headers)).json() == {"updated": 1}
    assert (await api_client.get(f"{API}/unread-count", headers=headers)).json() == {"unread": 0}


async def test_someone_elses_notification_is_not_found(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    await emergency(api_client, world.chairman)
    owners = (await feed(api_client, world.owner_14))[0]["id"]

    for notification_id in (owners, str(uuid.uuid4())):
        response = await api_client.post(
            f"{API}/feed/{notification_id}/read", headers=as_user(world.board_15)
        )
        assert response.status_code == 404
        assert response.json()["error_code"] == "NOTIFICATION_NOT_FOUND"


async def test_feed_paging_limits(api_client: AsyncClient, world: NotifWorld) -> None:
    await emergency(api_client, world.chairman)
    await emergency(api_client, world.chairman, title="Second alert")

    assert len(await feed(api_client, world.owner_14, limit=1)) == 1
    assert len(await feed(api_client, world.owner_14, limit=1, offset=1)) == 1
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        response = await api_client.get(f"{API}/feed", params=bad, headers=as_user(world.owner_14))
        assert response.status_code == 422
        assert response.json()["error_code"] == "VALIDATION_ERROR"


# --- Preferences ----------------------------------------------------------------------------------


async def test_the_preferences_matrix_with_defaults(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    response = await api_client.get(f"{API}/preferences", headers=as_user(world.owner_14))

    assert response.status_code == 200
    body = response.json()
    assert body["channels"] == [
        {"channel": "in_app", "available": True, "reason": None},
        {"channel": "email", "available": False, "reason": "DEMO_MODE"},
        {"channel": "telegram", "available": False, "reason": "DEMO_MODE"},
        {"channel": "webpush", "available": False, "reason": "DEMO_MODE"},
    ]
    assert len(body["preferences"]) == 16
    by_key = {(p["event_type"], p["channel"]): p for p in body["preferences"]}
    assert by_key[("EMERGENCY_PUBLISHED", "email")] == {
        "event_type": "EMERGENCY_PUBLISHED",
        "channel": "email",
        "enabled": True,
        "mandatory": True,
    }
    assert by_key[("EMERGENCY_RESOLVED", "email")]["enabled"] is False


async def test_changing_preferences_stores_only_deviations(
    api_client: AsyncClient, world: NotifWorld, db_session: AsyncSession
) -> None:
    async def put(*changes: tuple[str, str, bool]) -> Response:
        return await api_client.put(
            f"{API}/preferences",
            json={
                "preferences": [
                    {"event_type": e, "channel": c, "enabled": enabled} for e, c, enabled in changes
                ]
            },
            headers=as_user(world.owner_14),
        )

    async def stored() -> int:
        db_session.expunge_all()
        return await db_session.scalar(
            select(func.count())
            .select_from(Preference)
            .where(Preference.user_id == world.owner_14.id)
        )

    response = await put(("PERIOD_REVIEW", "in_app", False), ("EMERGENCY_RESOLVED", "email", True))
    assert response.status_code == 200
    by_key = {(p["event_type"], p["channel"]): p["enabled"] for p in response.json()["preferences"]}
    assert by_key[("PERIOD_REVIEW", "in_app")] is False
    assert by_key[("EMERGENCY_RESOLVED", "email")] is True
    assert await stored() == 2

    # Back to the default: the row goes away. Mandatory + true is accepted and stores nothing.
    assert (
        await put(("PERIOD_REVIEW", "in_app", True), ("EMERGENCY_PUBLISHED", "email", True))
    ).status_code == 200
    assert await stored() == 1


async def test_a_mandatory_preference_cannot_be_switched_off_and_nothing_changes(
    api_client: AsyncClient, world: NotifWorld, db_session: AsyncSession
) -> None:
    response = await api_client.put(
        f"{API}/preferences",
        json={
            "preferences": [
                {"event_type": "PERIOD_REVIEW", "channel": "in_app", "enabled": False},
                {"event_type": "EMERGENCY_PUBLISHED", "channel": "in_app", "enabled": False},
            ]
        },
        headers=as_user(world.owner_14),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "PREFERENCE_MANDATORY"
    count = await db_session.scalar(select(func.count()).select_from(Preference))
    assert count == 0


async def test_duplicate_combinations_are_rejected(
    api_client: AsyncClient, world: NotifWorld
) -> None:
    change = {"event_type": "PERIOD_REVIEW", "channel": "in_app", "enabled": False}

    response = await api_client.put(
        f"{API}/preferences",
        json={"preferences": [change, change]},
        headers=as_user(world.owner_14),
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "VALIDATION_ERROR"


# --- Language -------------------------------------------------------------------------------------


async def test_the_notification_language(
    api_client: AsyncClient, world: NotifWorld, db_session: AsyncSession
) -> None:
    headers = as_user(world.owner_14)
    assert (await api_client.get("/api/v1/property/me", headers=headers)).json()["locale"] is None

    changed = await api_client.patch(
        "/api/v1/property/me/locale", json={"locale": "ru"}, headers=headers
    )
    assert changed.status_code == 200 and changed.json() == {"locale": "ru"}
    assert (await api_client.get("/api/v1/property/me", headers=headers)).json()["locale"] == "ru"

    await emergency(api_client, world.chairman)
    locales = {
        user_id: locale
        for user_id, locale in await db_session.execute(select(Delivery.user_id, Delivery.locale))
    }
    assert locales[world.owner_14.id] == "ru"
    assert locales[world.board_15.id] == "et", "the instance default"

    assert (
        await api_client.patch("/api/v1/property/me/locale", json={"locale": None}, headers=headers)
    ).json() == {"locale": None}
    invalid = await api_client.patch(
        "/api/v1/property/me/locale", json={"locale": "de"}, headers=headers
    )
    assert invalid.status_code == 422


# --- Switched off ---------------------------------------------------------------------------------


async def test_the_module_can_be_switched_off() -> None:
    app = create_app(Settings(NOTIFICATIONS_ENABLED=False, DEMO_MODE_ENABLED=True))

    assert not any(getattr(route, "path", "").startswith(API) for route in app.routes)
    event = EmergencyAlertPublished(
        post_id=uuid.uuid4(),
        building_id=uuid.uuid4(),
        category=PostCategory.GENERAL,
        target_scope=TargetScope.ALL_BUILDING,
        target_entrance=None,
        target_apartment_ids=(),
        published_by_user_id=uuid.uuid4(),
    )
    assert app.state.event_bus.handlers_for(event) == []
