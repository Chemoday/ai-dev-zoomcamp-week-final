"""Integration tests: property API, authentication and the privacy masker on a real PostgreSQL."""

import uuid
from datetime import timedelta

import jwt
import pytest
from fastapi import Depends, FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.security import DEMO_USER_HEADER, create_access_token, require_building_roles
from src.modules.property.schemas import BuildingRole, CurrentUserDTO, DisplayMode, RoleBadge
from src.modules.property.service import UNKNOWN_AUTHOR_BADGE, PropertyService
from tests.factories import (
    DemoBuilding,
    create_apartment,
    create_demo_building,
    create_membership,
    create_user,
)

JWT_SECRET = "test-secret-key-with-at-least-32-bytes!"


@pytest.fixture
async def demo(db_session: AsyncSession) -> DemoBuilding:
    return await create_demo_building(db_session)


def as_user(user_id: uuid.UUID) -> dict[str, str]:
    return {DEMO_USER_HEADER: str(user_id)}


def assert_no_raw_pii(raw_json: str, demo: DemoBuilding) -> None:
    for secret in (demo.owner_14.full_name, demo.owner_14.email, demo.owner_14.phone):
        assert secret not in raw_json
    for key in ('"full_name"', '"email"', '"phone"'):
        assert key not in raw_json


@pytest.fixture
def settings_override(db_app: FastAPI):
    """Swap application settings for one test (demo mode, JWT secret)."""

    def apply(**values) -> Settings:
        settings = Settings(**{**get_settings().model_dump(), **values})
        db_app.dependency_overrides[get_settings] = lambda: settings
        return settings

    yield apply
    db_app.dependency_overrides.pop(get_settings, None)


# --- GET /me ------------------------------------------------------------------------------------


async def test_me_returns_role_memberships_permissions_and_masked_preview(
    api_client: AsyncClient, demo: DemoBuilding
) -> None:
    response = await api_client.get("/api/v1/property/me", headers=as_user(demo.owner_14.id))

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(demo.owner_14.id)
    assert body["building_id"] == str(demo.building.id)
    assert body["building_role"] == "USER"
    assert body["privacy_display_mode"] == "APARTMENT_ONLY"
    (membership,) = body["memberships"]
    assert membership == {
        "membership_id": str(demo.owner_14_membership.id),
        "apartment_id": str(demo.apt_14.id),
        "apartment_number": "14",
        "entrance": 2,
        "floor": 4,
        "relation_type": "OWNER",
        "can_submit_readings": True,
        "can_view_bills": True,
        "can_vote": True,
        "privacy_display_mode": "APARTMENT_ONLY",
        "preview_badge": {"display_label": "Кв. 14", "role_badge": "OWNER"},
    }


async def test_me_payload_never_contains_raw_pii_in_apartment_only_mode(
    api_client: AsyncClient, demo: DemoBuilding
) -> None:
    response = await api_client.get("/api/v1/property/me", headers=as_user(demo.owner_14.id))

    assert response.status_code == 200
    assert_no_raw_pii(response.text, demo)


async def test_me_for_staff_without_apartment(api_client: AsyncClient, demo: DemoBuilding) -> None:
    response = await api_client.get("/api/v1/property/me", headers=as_user(demo.chairman.id))

    assert response.status_code == 200
    body = response.json()
    assert body["building_role"] == "CHAIRMAN"
    assert body["building_id"] == str(demo.building.id)
    assert body["memberships"] == []
    assert body["privacy_display_mode"] == "APARTMENT_ONLY"


# --- PATCH /memberships/{id}/privacy ------------------------------------------------------------


async def test_privacy_update_returns_preview_and_persists(
    api_client: AsyncClient, demo: DemoBuilding
) -> None:
    url = f"/api/v1/property/memberships/{demo.owner_14_membership.id}/privacy"

    response = await api_client.patch(
        url, json={"privacy_display_mode": "INITIALS"}, headers=as_user(demo.owner_14.id)
    )

    assert response.status_code == 200
    assert response.json() == {
        "membership_id": str(demo.owner_14_membership.id),
        "privacy_display_mode": "INITIALS",
        "preview_badge": {"display_label": "И. П. (Кв. 14)", "role_badge": "OWNER"},
    }
    me = (await api_client.get("/api/v1/property/me", headers=as_user(demo.owner_14.id))).json()
    assert me["privacy_display_mode"] == "INITIALS"
    assert me["memberships"][0]["preview_badge"]["display_label"] == "И. П. (Кв. 14)"


async def test_privacy_update_to_full_name(api_client: AsyncClient, demo: DemoBuilding) -> None:
    response = await api_client.patch(
        f"/api/v1/property/memberships/{demo.owner_14_membership.id}/privacy",
        json={"privacy_display_mode": "FULL_NAME"},
        headers=as_user(demo.owner_14.id),
    )

    assert response.json()["preview_badge"]["display_label"] == "Иван Петров (Кв. 14)"


async def test_privacy_update_back_to_apartment_only_has_no_raw_pii(
    api_client: AsyncClient, demo: DemoBuilding
) -> None:
    url = f"/api/v1/property/memberships/{demo.owner_14_membership.id}/privacy"
    headers = as_user(demo.owner_14.id)
    await api_client.patch(url, json={"privacy_display_mode": "FULL_NAME"}, headers=headers)

    response = await api_client.patch(
        url, json={"privacy_display_mode": "APARTMENT_ONLY"}, headers=headers
    )

    assert response.status_code == 200
    assert response.json()["preview_badge"]["display_label"] == "Кв. 14"
    assert_no_raw_pii(response.text, demo)


async def test_cannot_change_someone_elses_membership(
    api_client: AsyncClient, demo: DemoBuilding
) -> None:
    # Plain values up front: a failed request rolls back its SAVEPOINT, which expires the ORM
    # objects it touched, and expired objects cannot lazy-load outside the async context.
    neighbour = as_user(demo.tenant_2.id)
    foreign_membership_id = demo.owner_14_membership.id

    response = await api_client.patch(
        f"/api/v1/property/memberships/{foreign_membership_id}/privacy",
        json={"privacy_display_mode": "FULL_NAME"},
        headers=neighbour,
    )
    unknown = await api_client.patch(
        f"/api/v1/property/memberships/{uuid.uuid4()}/privacy",
        json={"privacy_display_mode": "FULL_NAME"},
        headers=neighbour,
    )

    # Indistinguishable from an unknown id, so memberships cannot be probed.
    assert response.status_code == unknown.status_code == 404
    assert (
        response.json()
        == unknown.json()
        == {
            "error_code": "MEMBERSHIP_NOT_FOUND",
            "message": "Membership not found.",
        }
    )


async def test_invalid_privacy_mode_is_a_uniform_validation_error(
    api_client: AsyncClient, demo: DemoBuilding
) -> None:
    response = await api_client.patch(
        f"/api/v1/property/memberships/{demo.owner_14_membership.id}/privacy",
        json={"privacy_display_mode": "EVERYTHING"},
        headers=as_user(demo.owner_14.id),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["loc"] == ["body", "privacy_display_mode"]
    assert "EVERYTHING" not in response.text, "submitted values are not echoed back"


# --- Authentication -----------------------------------------------------------------------------


async def test_missing_credentials_return_401(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/property/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == "NOT_AUTHENTICATED"


@pytest.mark.parametrize("header_value", ["not-a-uuid", str(uuid.uuid4())])
async def test_unknown_demo_user_returns_401(api_client: AsyncClient, header_value: str) -> None:
    response = await api_client.get("/api/v1/property/me", headers={DEMO_USER_HEADER: header_value})

    assert response.status_code == 401
    assert response.json()["error_code"] == "UNKNOWN_USER"


async def test_deactivated_user_cannot_authenticate(
    api_client: AsyncClient, db_session: AsyncSession, demo: DemoBuilding
) -> None:
    former = await create_user(db_session, demo.building, "Бывший Жилец", is_active=False)

    response = await api_client.get("/api/v1/property/me", headers=as_user(former.id))

    assert response.status_code == 401
    assert response.json()["error_code"] == "UNKNOWN_USER"


async def test_demo_header_is_ignored_when_demo_mode_is_off(
    api_client: AsyncClient, demo: DemoBuilding, settings_override
) -> None:
    settings_override(DEMO_MODE_ENABLED=False)

    response = await api_client.get("/api/v1/property/me", headers=as_user(demo.owner_14.id))

    assert response.status_code == 401
    assert response.json()["error_code"] == "NOT_AUTHENTICATED"


async def test_valid_bearer_token_authenticates(
    api_client: AsyncClient, demo: DemoBuilding, settings_override
) -> None:
    settings = settings_override(JWT_SECRET_KEY=JWT_SECRET, DEMO_MODE_ENABLED=False)
    token = create_access_token(demo.owner_14.id, settings)

    response = await api_client.get(
        "/api/v1/property/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == str(demo.owner_14.id)


async def test_bearer_token_wins_over_demo_header(
    api_client: AsyncClient, demo: DemoBuilding, settings_override
) -> None:
    settings = settings_override(JWT_SECRET_KEY=JWT_SECRET)
    token = create_access_token(demo.owner_14.id, settings)

    response = await api_client.get(
        "/api/v1/property/me",
        headers={"Authorization": f"Bearer {token}", **as_user(demo.chairman.id)},
    )

    assert response.json()["user_id"] == str(demo.owner_14.id)


async def test_expired_forged_or_unconfigured_tokens_are_rejected(
    api_client: AsyncClient, demo: DemoBuilding, settings_override
) -> None:
    settings = settings_override(JWT_SECRET_KEY=JWT_SECRET)
    expired = create_access_token(demo.owner_14.id, settings, expires_in=timedelta(seconds=-5))
    forged = jwt.encode(
        {"sub": str(demo.owner_14.id), "exp": 9999999999, "typ": "access"},
        "attacker-key-also-at-least-32-bytes-long",
        algorithm="HS256",
    )
    tokens = {"expired": expired, "forged": forged, "garbage": "abc.def.ghi"}

    for name, token in tokens.items():
        response = await api_client.get(
            "/api/v1/property/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401, name
        assert response.json()["error_code"] == "INVALID_TOKEN", name

    # Without a configured secret, no token at all is accepted (nothing can be forged).
    valid = create_access_token(demo.owner_14.id, settings)
    settings_override(JWT_SECRET_KEY="")
    response = await api_client.get(
        "/api/v1/property/me", headers={"Authorization": f"Bearer {valid}"}
    )
    assert response.status_code == 401


# --- RBAC ---------------------------------------------------------------------------------------


async def test_require_building_roles(
    db_app: FastAPI, api_client: AsyncClient, demo: DemoBuilding
) -> None:
    async def chairman_only(
        user: CurrentUserDTO = Depends(require_building_roles(BuildingRole.CHAIRMAN)),
    ) -> dict[str, str]:
        return {"role": user.building_role}

    db_app.add_api_route("/test/chairman-only", chairman_only)

    allowed = await api_client.get("/test/chairman-only", headers=as_user(demo.chairman.id))
    denied = await api_client.get("/test/chairman-only", headers=as_user(demo.owner_14.id))

    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert denied.json() == {
        "error_code": "FORBIDDEN",
        "message": "Your building role does not allow this action.",
        "required_roles": ["CHAIRMAN"],
    }


async def test_unknown_route_uses_the_uniform_error_shape(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"error_code": "NOT_FOUND", "message": "Not Found"}


# --- Privacy masker used by other modules -------------------------------------------------------


async def test_masked_author_badges_follow_each_membership_mode(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    second_apartment = await create_apartment(db_session, demo.building, "15", entrance=2)
    await create_membership(db_session, demo.owner_14, second_apartment, mode=DisplayMode.FULL_NAME)
    service = PropertyService(db_session)
    ghost = uuid.uuid4()

    badges = await service.get_masked_author_badges(
        [
            (demo.owner_14.id, demo.apt_14.id),
            (demo.owner_14.id, second_apartment.id),
            (demo.tenant_2.id, demo.apt_2.id),
            (demo.chairman.id, None),
            (demo.tenant_2.id, demo.apt_14.id),  # posted about an apartment they do not live in
            (ghost, None),
        ]
    )

    assert badges[(demo.owner_14.id, demo.apt_14.id)].display_label == "Кв. 14"
    assert badges[(demo.owner_14.id, second_apartment.id)].display_label == "Иван Петров (Кв. 15)"
    assert badges[(demo.tenant_2.id, demo.apt_2.id)].role_badge == RoleBadge.RESIDENT
    assert badges[(demo.chairman.id, None)].display_label == "Председатель"
    assert badges[(demo.chairman.id, None)].role_badge == RoleBadge.BOARD
    assert badges[(demo.tenant_2.id, demo.apt_14.id)].display_label == "Кв. 14"
    assert badges[(ghost, None)] == UNKNOWN_AUTHOR_BADGE


async def test_masked_author_badge_single_lookup(
    db_session: AsyncSession, demo: DemoBuilding
) -> None:
    badge = await PropertyService(db_session).get_masked_author_badge(
        demo.owner_14.id, demo.apt_14.id
    )

    assert badge.display_label == "Кв. 14"
    assert badge.role_badge == RoleBadge.OWNER
