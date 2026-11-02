"""Invitations, registration and accepting (task E7/A4), on a real PostgreSQL with an in-process
SMTP server. Demo mode is off; people sign in with real sessions (Bearer tokens).
"""

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.main import create_app
from src.modules.auth import invitations as invitations_module
from src.modules.auth import links as links_module
from src.modules.auth import service as auth_service
from src.modules.auth.models import AuthInvitation, AuthToken
from src.modules.auth.policies import REFRESH_COOKIE
from src.modules.auth.schemas import AuthMethod
from src.modules.auth.service import AuthService
from src.modules.property.models import ApartmentMembership, Building, User
from src.modules.property.schemas import BuildingRole, DisplayMode, RelationType
from tests.factories import create_apartment, create_building, create_membership, create_user
from tests.smtp_server import SmtpServer

API = "/api/v1/auth"
CSRF = {"X-Requested-With": "fetch"}
SITE = "https://kyu.example.test"
LINK = re.compile(re.escape(SITE) + r"/login/link#([A-Za-z0-9_-]+)")


@pytest.fixture
def mail_settings(smtp_server: SmtpServer) -> Settings:
    return Settings(
        **{
            **get_settings().model_dump(),
            "DEMO_MODE_ENABLED": False,
            "ENABLED_PLUGINS": "common.csv_export,common.email_smtp",
            "SMTP_HOST": smtp_server.host,
            "SMTP_PORT": smtp_server.port,
            "SMTP_TLS": "none",
            "SMTP_FROM": "KU Sopruse 12 <noreply@kyu.example.test>",
            "PUBLIC_BASE_URL": SITE,
            "NOTIFICATIONS_DISPATCHER_ENABLED": False,
        }
    )


@pytest.fixture
def app(mail_settings: Settings) -> FastAPI:
    app = create_app(mail_settings)
    app.dependency_overrides[get_settings] = lambda: mail_settings
    return app


@pytest.fixture
async def client(db_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=db_app, client=("198.51.100.7", 4000)),
        base_url="https://test",
    ) as client:
        yield client


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    class Clock:
        now = datetime.now(UTC)

        def shift(self, **delta: float) -> None:
            self.now += timedelta(**delta)

    clock = Clock()
    for module in (invitations_module, links_module, auth_service):
        monkeypatch.setattr(module, "_now", lambda: clock.now)
    return clock


@dataclass
class World:
    building: Building
    apt_14: object
    apt_2: object
    apt_21: object
    chairman: User
    owner_14: User
    accountant: User
    headers: dict[str, dict[str, str]]


def unique(name: str) -> str:
    return f"{name}.{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture
async def world(db_session: AsyncSession, mail_settings: Settings) -> World:
    """Apartments 14 (entrance 2), 2 and 21 (entrance 1); a chairman and an accountant without a
    flat; Ivan Petrov owns apartment 14 and shows his initials there."""
    building = await create_building(db_session)
    apt_14 = await create_apartment(db_session, building, "14", entrance=2)
    apt_2 = await create_apartment(db_session, building, "2", entrance=1)
    apt_21 = await create_apartment(db_session, building, "21", entrance=1)
    chairman = await create_user(
        db_session, building, "Oleg Sidorov", role=BuildingRole.CHAIRMAN, email=unique("chair")
    )
    owner_14 = await create_user(db_session, building, "Ivan Petrov", email=unique("ivan"))
    await create_membership(db_session, owner_14, apt_14, mode=DisplayMode.INITIALS)
    accountant = await create_user(
        db_session, building, "Mari Raamat", role=BuildingRole.ACCOUNTANT, email=unique("books")
    )
    world = World(building, apt_14, apt_2, apt_21, chairman, owner_14, accountant, {})
    for user in (chairman, owner_14, accountant):
        world.headers[str(user.id)] = await sign_in(db_session, mail_settings, user)
    return world


async def sign_in(session: AsyncSession, settings: Settings, user: User) -> dict[str, str]:
    issued = await AuthService(session, settings).start_session(
        user.id, AuthMethod.MAGIC_LINK, None
    )
    return {"Authorization": f"Bearer {issued.response.access_token}"}


def as_(world: World, user: User) -> dict[str, str]:
    return world.headers[str(user.id)]


async def drain(client: AsyncClient) -> None:
    await client._transport.app.state.auth_background.drain(within_seconds=10)  # type: ignore[attr-defined]


def links_sent(smtp_server: SmtpServer, to: str) -> list[str]:
    found = []
    for message in smtp_server.messages:
        if message["To"] == to:
            match = LINK.search(message.get_content())
            assert match, "every invitation mail carries a link"
            found.append(match.group(1))
    return found


async def invite(client: AsyncClient, headers: dict[str, str], **body: object):
    return await client.post(f"{API}/invitations", json=body, headers=headers)


async def redeem(client: AsyncClient, token: str):
    return await client.post(f"{API}/magic-link/redeem", json={"token": token}, headers=CSRF)


async def register(client: AsyncClient, token: str, **fields: object):
    body = {"registration_token": token, "full_name": "Nina Uus", "locale": "et", **fields}
    return await client.post(f"{API}/registration", json=body, headers=CSRF)


async def invite_and_open(
    client: AsyncClient, smtp_server: SmtpServer, headers: dict[str, str], **body: object
) -> tuple[dict, str]:
    """Invite, wait for the mail, and return the invitation and its link token."""
    response = await invite(client, headers, **body)
    assert response.status_code == 201, response.text
    await drain(client)
    token = links_sent(smtp_server, str(body["email"]).lower())[-1]
    return response.json(), token


async def expire_all(session: AsyncSession) -> None:
    """Let every open invitation (and its link) run out, as 8 days would."""
    past = datetime.now(UTC) - timedelta(days=1)
    await session.execute(
        update(AuthInvitation).where(AuthInvitation.accepted_at.is_(None)).values(expires_at=past)
    )
    await session.execute(
        update(AuthToken).where(AuthToken.invitation_id.is_not(None)).values(expires_at=past)
    )
    await session.flush()


async def role_of(session: AsyncSession, user: User) -> BuildingRole:
    return await session.scalar(select(User.building_role).where(User.id == user.id))


async def is_active(session: AsyncSession, user: User) -> bool:
    return await session.scalar(select(User.is_active).where(User.id == user.id))


async def user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email))


# --- A new person ---------------------------------------------------------------------------------


async def test_a_new_owner_registers_through_the_invitation(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    email = unique("nina")
    invitation, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="OWNER", email=email.upper(), apartment_number="21",
    )  # fmt: skip
    assert invitation["email"] == email  # canonical, and in full for the person who typed it
    assert (invitation["status"], invitation["mine"], invitation["apartment_number"]) == (
        "PENDING",
        True,
        "21",
    )
    assert await user_by_email(db_session, email) is None  # nobody exists before acceptance

    opened = await redeem(client, token)
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["status"] == "REGISTRATION_REQUIRED"
    assert body["invitation"] == {"kind": "OWNER", "apartment_number": "21", "building_role": None}
    assert body["expires_in"] == 1800 and body["session"] is None
    assert REFRESH_COOKIE not in opened.headers.get("set-cookie", "")

    registered = await register(
        client, body["registration_token"], phone="+372 5000 1111", privacy_display_mode="FULL_NAME"
    )
    assert registered.status_code == 200, registered.text
    assert registered.json()["status"] == "SIGNED_IN"
    assert REFRESH_COOKIE in registered.headers["set-cookie"]
    user = await user_by_email(db_session, email)
    assert user is not None and user.full_name == "Nina Uus" and user.locale == "et"
    membership = await db_session.scalar(
        select(ApartmentMembership).where(ApartmentMembership.user_id == user.id)
    )
    assert membership is not None and membership.apartment_id == world.apt_21.id
    assert (membership.relation_type, membership.can_vote, membership.privacy_display_mode) == (
        RelationType.OWNER,
        True,
        DisplayMode.FULL_NAME,
    )

    listed = await client.get(f"{API}/invitations", headers=as_(world, world.chairman))
    assert [i["status"] for i in listed.json()] == ["ACCEPTED"]
    assert (await redeem(client, token)).json()["error_code"] == "INVALID_LINK"  # used up
    again = await register(client, body["registration_token"])
    assert again.json()["error_code"] == "INVALID_LINK"


async def test_registration_needs_the_csrf_header_and_a_fresh_form(
    client: AsyncClient, world: World, smtp_server: SmtpServer, clock
) -> None:
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="STAFF", email=unique("board"), building_role="BOARD",
    )  # fmt: skip
    first = (await redeem(client, token)).json()["registration_token"]
    second = (await redeem(client, token)).json()["registration_token"]
    # A new redeem replaces the form token; the link itself stays valid until acceptance.
    assert (await register(client, first)).json()["error_code"] == "INVALID_LINK"
    no_header = await client.post(
        f"{API}/registration",
        json={"registration_token": second, "full_name": "X", "locale": "en"},
    )
    assert no_header.json()["error_code"] == "CSRF_CHECK_FAILED"
    clock.shift(minutes=31)
    assert (await register(client, second)).json()["error_code"] == "INVALID_LINK"


async def test_a_staff_registration_gets_the_role_and_no_membership(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    email = unique("board")
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="STAFF", email=email, building_role="BOARD",
    )  # fmt: skip
    form = (await redeem(client, token)).json()
    assert form["invitation"] == {
        "kind": "STAFF",
        "apartment_number": None,
        "building_role": "BOARD",
    }
    assert (await register(client, form["registration_token"])).status_code == 200
    user = await user_by_email(db_session, email)
    assert user is not None and user.building_role == BuildingRole.BOARD
    memberships = await db_session.scalars(
        select(ApartmentMembership).where(ApartmentMembership.user_id == user.id)
    )
    assert list(memberships) == []


# --- An existing account --------------------------------------------------------------------------


async def test_an_existing_account_signs_in_and_accepts(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    assert world.owner_14.email is not None
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="OWNER", email=world.owner_14.email, apartment_number="2",
    )  # fmt: skip
    opened = (await redeem(client, token)).json()
    assert opened == {
        "status": "SIGN_IN_TO_ACCEPT",
        "session": None,
        "registration_token": None,
        "expires_in": None,
        "invitation": {"kind": "OWNER", "apartment_number": "2", "building_role": None},
    }
    # Somebody else's account cannot take it.
    stolen = await client.post(
        f"{API}/invitations/accept", json={"token": token}, headers=as_(world, world.accountant)
    )
    assert stolen.json()["error_code"] == "INVALID_LINK"

    accepted = await client.post(
        f"{API}/invitations/accept", json={"token": token}, headers=as_(world, world.owner_14)
    )
    assert accepted.json() == {"applied": True}
    flats = set(
        await db_session.scalars(
            select(ApartmentMembership.apartment_id).where(
                ApartmentMembership.user_id == world.owner_14.id
            )
        )
    )
    assert flats == {world.apt_14.id, world.apt_2.id}
    again = await client.post(
        f"{API}/invitations/accept", json={"token": token}, headers=as_(world, world.owner_14)
    )
    assert again.json()["error_code"] == "INVALID_LINK"


async def test_accepting_never_lowers_anything(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    assert world.owner_14.email is not None and world.accountant.email is not None
    # The owner of 14 invited as an owner of 14 again: nothing changes.
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="OWNER", email=world.owner_14.email, apartment_number="14",
    )  # fmt: skip
    same = await client.post(
        f"{API}/invitations/accept", json={"token": token}, headers=as_(world, world.owner_14)
    )
    assert same.json() == {"applied": False}
    # A staff role is not replaced, but CHAIRMAN raises any other role.
    _, board = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="STAFF", email=world.accountant.email, building_role="BOARD",
    )  # fmt: skip
    kept = await client.post(
        f"{API}/invitations/accept", json={"token": board}, headers=as_(world, world.accountant)
    )
    assert kept.json() == {"applied": False}
    _, chair = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="STAFF", email=world.accountant.email, building_role="CHAIRMAN",
    )  # fmt: skip
    raised = await client.post(
        f"{API}/invitations/accept", json={"token": chair}, headers=as_(world, world.accountant)
    )
    assert raised.json() == {"applied": True}
    assert await role_of(db_session, world.accountant) == BuildingRole.CHAIRMAN


async def test_an_owner_invitation_raises_a_tenant_who_bought_the_flat(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    email = unique("buyer")
    await register_tenant(client, world, smtp_server, email)
    buyer = await user_by_email(db_session, email)
    assert buyer is not None
    await db_session.execute(
        update(ApartmentMembership)
        .where(ApartmentMembership.user_id == buyer.id)
        .values(privacy_display_mode=DisplayMode.INITIALS)
    )
    await db_session.flush()
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="OWNER", email=email, apartment_number="14",
    )  # fmt: skip
    accepted = await client.post(
        f"{API}/invitations/accept",
        json={"token": token},
        headers=await sign_in(db_session, get_settings(), buyer),
    )
    assert accepted.json() == {"applied": True}
    membership = await db_session.scalar(
        select(ApartmentMembership)
        .where(ApartmentMembership.user_id == buyer.id)
        .execution_options(populate_existing=True)
    )
    assert membership is not None
    assert (membership.relation_type, membership.can_vote, membership.privacy_display_mode) == (
        RelationType.OWNER,
        True,
        DisplayMode.INITIALS,  # the person's own choice stays
    )


async def test_an_owner_invited_as_a_tenant_stays_the_owner(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    assert world.owner_14.email is not None
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.owner_14),
        kind="TENANT", email=world.owner_14.email, apartment_id=str(world.apt_14.id),
    )  # fmt: skip
    accepted = await client.post(
        f"{API}/invitations/accept", json={"token": token}, headers=as_(world, world.owner_14)
    )
    assert accepted.json() == {"applied": False}
    relation = await db_session.scalar(
        select(ApartmentMembership.relation_type).where(
            ApartmentMembership.user_id == world.owner_14.id
        )
    )
    assert relation == RelationType.OWNER


# --- Who may invite whom, and what the inviter learns ---------------------------------------------


async def test_who_may_invite_whom(client: AsyncClient, world: World) -> None:
    chairman, owner, accountant = (
        as_(world, world.chairman),
        as_(world, world.owner_14),
        as_(world, world.accountant),
    )
    cases = [
        (chairman, {"kind": "TENANT", "apartment_id": str(world.apt_14.id)}, 403),  # not an owner
        (owner, {"kind": "OWNER", "apartment_number": "2"}, 403),
        (owner, {"kind": "STAFF", "building_role": "BOARD"}, 403),
        (owner, {"kind": "TENANT", "apartment_id": str(world.apt_2.id)}, 403),  # not his flat
        (accountant, {"kind": "OWNER", "apartment_number": "2"}, 403),
        (chairman, {"kind": "OWNER", "apartment_number": "99"}, 404),
        (owner, {"kind": "TENANT", "apartment_id": str(uuid.uuid4())}, 404),
        (chairman, {"kind": "STAFF", "building_role": "USER"}, 422),  # USER is no staff role
        (chairman, {"kind": "OWNER", "apartment_number": "2", "email": "not-an-address"}, 422),
    ]
    for headers, body, status in cases:
        response = await invite(client, headers, **{"email": unique("x"), **body})
        assert response.status_code == status, (body, response.text)
    demo = await client.get(
        f"{API}/invitations", headers={"X-Demo-User-Id": str(world.chairman.id)}
    )
    assert demo.status_code == 401  # account endpoints never take the demo header


async def test_the_inviter_cannot_tell_whether_an_e_mail_has_an_account(
    client: AsyncClient, world: World, smtp_server: SmtpServer
) -> None:
    owner = as_(world, world.owner_14)
    assert world.accountant.email is not None
    known = await invite(
        client,
        owner,
        kind="TENANT",
        email=world.accountant.email,
        apartment_id=str(world.apt_14.id),
    )
    unknown_email = unique("nobody")
    unknown = await invite(
        client, owner, kind="TENANT", email=unknown_email, apartment_id=str(world.apt_14.id)
    )
    assert known.status_code == unknown.status_code == 201
    drop = {"invitation_id", "email", "created_at", "expires_at"}
    assert {k: v for k, v in known.json().items() if k not in drop} == {
        k: v for k, v in unknown.json().items() if k not in drop
    }
    await drain(client)
    # Both get the same invitation mail; only the link page tells the mailbox owner apart.
    assert len(links_sent(smtp_server, world.accountant.email)) == 1
    assert len(links_sent(smtp_server, unknown_email)) == 1
    subjects = {m["Subject"] for m in smtp_server.messages}
    assert len(subjects) == 1


async def test_tenants_are_limited_with_pending_invitations(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    owner = as_(world, world.owner_14)
    apartment = str(world.apt_14.id)
    for _ in range(3):
        ok = await invite(client, owner, kind="TENANT", email=unique("t"), apartment_id=apartment)
        assert ok.status_code == 201
    full = await invite(client, owner, kind="TENANT", email=unique("t"), apartment_id=apartment)
    assert (full.status_code, full.json()["error_code"]) == (409, "TENANT_LIMIT_REACHED")
    await expire_all(db_session)  # expired invitations no longer hold a place
    later = await invite(client, owner, kind="TENANT", email=unique("t"), apartment_id=apartment)
    assert later.status_code == 201


async def test_one_open_invitation_per_target(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    chairman = as_(world, world.chairman)
    email = unique("twice")
    first = await invite(client, chairman, kind="OWNER", email=email, apartment_number="2")
    second = await invite(client, chairman, kind="OWNER", email=email, apartment_number="2")
    assert (second.status_code, second.json()["error_code"]) == (409, "INVITATION_ALREADY_PENDING")
    other_flat = await invite(client, chairman, kind="OWNER", email=email, apartment_number="21")
    assert other_flat.status_code == 201  # another target
    await expire_all(db_session)
    replaced = await invite(client, chairman, kind="OWNER", email=email, apartment_number="2")
    assert replaced.status_code == 201
    ids = [
        i["invitation_id"]
        for i in (await client.get(f"{API}/invitations", headers=chairman)).json()
    ]
    assert first.json()["invitation_id"] not in ids  # the expired one was replaced


async def test_invitations_are_limited_per_inviter(client: AsyncClient, world: World) -> None:
    chairman = as_(world, world.chairman)
    for _ in range(10):
        ok = await invite(client, chairman, kind="STAFF", email=unique("s"), building_role="BOARD")
        assert ok.status_code == 201
    limited = await invite(client, chairman, kind="STAFF", email=unique("s"), building_role="BOARD")
    assert (limited.status_code, limited.json()["error_code"]) == (429, "RATE_LIMITED")
    assert int(limited.headers["Retry-After"]) == limited.json()["retry_after_seconds"] > 0


# --- Seeing, resending and deleting ---------------------------------------------------------------


async def test_who_sees_which_invitation_and_which_e_mail(
    client: AsyncClient, world: World, db_session: AsyncSession, mail_settings: Settings
) -> None:
    tenant_email = unique("tenant")
    await invite(
        client, as_(world, world.owner_14),
        kind="TENANT", email=tenant_email, apartment_id=str(world.apt_14.id),
    )  # fmt: skip
    await invite(
        client, as_(world, world.chairman), kind="OWNER", email=unique("o"), apartment_number="2"
    )

    mine = (await client.get(f"{API}/invitations", headers=as_(world, world.owner_14))).json()
    assert [(i["kind"], i["email"], i["mine"]) for i in mine] == [("TENANT", tenant_email, True)]
    board = (await client.get(f"{API}/invitations", headers=as_(world, world.chairman))).json()
    tenant_row = next(i for i in board if i["kind"] == "TENANT")
    assert (
        tenant_row["mine"] is False and tenant_row["email"] == f"{tenant_email[0]}***@example.com"
    )
    assert len(board) == 2
    # A co-owner sees it masked; someone without a flat sees nothing.
    co_owner = await create_user(db_session, world.building, "Kai Kask", email=unique("kai"))
    await create_membership(db_session, co_owner, world.apt_14)
    co = (
        await client.get(
            f"{API}/invitations", headers=await sign_in(db_session, mail_settings, co_owner)
        )
    ).json()
    assert [(i["email"], i["mine"]) for i in co] == [(tenant_row["email"], False)]
    assert (
        await client.get(f"{API}/invitations", headers=as_(world, world.accountant))
    ).json() == []


async def test_resend_and_delete(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    chairman = as_(world, world.chairman)
    email = unique("again")
    invitation, old = await invite_and_open(
        client, smtp_server, chairman, kind="OWNER", email=email, apartment_number="2"
    )
    await expire_all(db_session)
    expired = (await client.get(f"{API}/invitations", headers=chairman)).json()[0]
    assert expired["status"] == "EXPIRED"
    resent = await client.post(
        f"{API}/invitations/{invitation['invitation_id']}/resend", headers=chairman
    )
    assert resent.status_code == 200 and resent.json()["status"] == "PENDING"
    assert datetime.fromisoformat(resent.json()["expires_at"]) > datetime.now(UTC) + timedelta(
        days=6
    )
    await drain(client)
    new = links_sent(smtp_server, email)[-1]
    assert new != old
    assert (await redeem(client, old)).json()["error_code"] == "INVALID_LINK"
    assert (await redeem(client, new)).json()["status"] == "REGISTRATION_REQUIRED"

    # Owners do not reach the board's invitations.
    hidden = await client.delete(
        f"{API}/invitations/{invitation['invitation_id']}", headers=as_(world, world.owner_14)
    )
    assert hidden.json()["error_code"] == "INVITATION_NOT_FOUND"
    deleted = await client.delete(
        f"{API}/invitations/{invitation['invitation_id']}", headers=chairman
    )
    assert deleted.status_code == 204
    assert (await redeem(client, new)).json()["error_code"] == "INVALID_LINK"
    assert (await client.get(f"{API}/invitations", headers=chairman)).json() == []


async def test_an_accepted_invitation_cannot_be_resent_or_deleted(
    client: AsyncClient, world: World, smtp_server: SmtpServer
) -> None:
    chairman = as_(world, world.chairman)
    invitation, token = await invite_and_open(
        client, smtp_server, chairman, kind="OWNER", email=unique("n"), apartment_number="21"
    )
    form = (await redeem(client, token)).json()
    await register(client, form["registration_token"])
    path = f"{API}/invitations/{invitation['invitation_id']}"
    for response in (
        await client.post(f"{path}/resend", headers=chairman),
        await client.delete(path, headers=chairman),
    ):
        assert (response.status_code, response.json()["error_code"]) == (409, "INVITATION_ACCEPTED")


async def test_a_tenant_invitation_stops_applying_without_an_owner(
    client: AsyncClient, world: World, smtp_server: SmtpServer
) -> None:
    invitation, token = await invite_and_open(
        client, smtp_server, as_(world, world.owner_14),
        kind="TENANT", email=unique("t"), apartment_id=str(world.apt_14.id),
    )  # fmt: skip
    chairman = as_(world, world.chairman)
    people = (await client.get(f"{API}/users", headers=chairman)).json()["people"]
    owner = next(p for p in people if p["user_id"] == str(world.owner_14.id))
    ended = await client.delete(
        f"{API}/users/{world.owner_14.id}/memberships/{owner['memberships'][0]['membership_id']}",
        headers=chairman,
    )
    assert ended.status_code == 204
    listed = (await client.get(f"{API}/invitations", headers=chairman)).json()
    assert listed[0]["status"] == "NO_LONGER_VALID"
    assert (await redeem(client, token)).json()["error_code"] == "INVALID_LINK"
    resend = await client.post(
        f"{API}/invitations/{invitation['invitation_id']}/resend", headers=chairman
    )
    assert resend.json()["error_code"] == "INVITATION_NO_LONGER_VALID"


async def test_asking_for_a_sign_in_link_resends_a_pending_invitation(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    email = unique("lost")
    invitation, old = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="OWNER", email=email, apartment_number="2",
    )  # fmt: skip
    response = await client.post(f"{API}/magic-link", json={"email": email})
    assert response.status_code == 202
    await drain(client)
    new = links_sent(smtp_server, email)[-1]
    assert new != old
    assert (await redeem(client, old)).json()["error_code"] == "INVALID_LINK"
    assert (await redeem(client, new)).json()["status"] == "REGISTRATION_REQUIRED"
    row = await db_session.get(AuthInvitation, uuid.UUID(invitation["invitation_id"]))
    assert row is not None
    assert row.expires_at == datetime.fromisoformat(invitation["expires_at"])  # same expiry


# --- My residents, replacing and removing tenants -------------------------------------------------


async def register_tenant(
    client: AsyncClient, world: World, smtp_server: SmtpServer, email: str
) -> None:
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.owner_14),
        kind="TENANT", email=email, apartment_id=str(world.apt_14.id),
    )  # fmt: skip
    form = (await redeem(client, token)).json()
    assert (await register(client, form["registration_token"])).status_code == 200


async def test_my_residents_replace_and_remove(
    client: AsyncClient,
    world: World,
    smtp_server: SmtpServer,
    db_session: AsyncSession,
    published_events: list,
) -> None:
    owner = as_(world, world.owner_14)
    first, second = unique("mari"), unique("jaan")
    await register_tenant(client, world, smtp_server, first)
    residents = (
        await client.get(f"{API}/apartments/{world.apt_14.id}/residents", headers=owner)
    ).json()
    assert [
        (r["relation_type"], r["display_label"], r["invited_email"]) for r in residents["residents"]
    ] == [
        ("OWNER", "I. P. (Apt. 14)", None),
        ("TENANT", "Apt. 14", first),
    ]
    tenant_id = residents["residents"][1]["user_id"]

    replaced = await client.post(
        f"{API}/apartments/{world.apt_14.id}/tenants/{tenant_id}/replace",
        json={"email": second},
        headers=owner,
    )
    assert replaced.status_code == 201 and replaced.json()["email"] == second
    old = await db_session.get(User, uuid.UUID(tenant_id))
    await db_session.refresh(old)
    # The old tenant had nothing else, so the account is closed and wiped.
    assert old is not None and (old.is_active, old.email, old.phone, old.full_name) == (
        False,
        None,
        None,
        "Former resident",
    )
    assert any(type(e).__name__ == "UserAccountClosed" for e in published_events)
    after = (
        await client.get(f"{API}/apartments/{world.apt_14.id}/residents", headers=owner)
    ).json()
    assert [r["relation_type"] for r in after["residents"]] == ["OWNER"]
    assert [i["email"] for i in after["pending_invitations"]] == [second]

    others = await client.get(
        f"{API}/apartments/{world.apt_14.id}/residents", headers=as_(world, world.accountant)
    )
    assert others.json()["error_code"] == "FORBIDDEN"
    missing = await client.delete(
        f"{API}/apartments/{world.apt_14.id}/tenants/{world.owner_14.id}", headers=owner
    )
    assert missing.json()["error_code"] == "MEMBERSHIP_NOT_FOUND"  # an owner is not a tenant


async def test_closing_an_account_signs_out_and_drops_notification_rows(
    client: AsyncClient,
    world: World,
    smtp_server: SmtpServer,
    db_session: AsyncSession,
    mail_settings: Settings,
) -> None:
    from src.modules.notifications.models import ChannelBinding, Delivery, LinkCode, Preference

    email = unique("leaving")
    await register_tenant(client, world, smtp_server, email)
    tenant = await user_by_email(db_session, email)
    assert tenant is not None
    db_session.add_all(
        [
            ChannelBinding(
                user_id=tenant.id, channel="telegram", config={"chat_id": 1},
                address_hash="d" * 64, label="Telegram",
            ),
            Preference(
                user_id=tenant.id, event_type="READING_DEADLINE", channel="email", enabled=False
            ),
            LinkCode(
                user_id=tenant.id, code_hash="f" * 64,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            ),
            Delivery(
                user_id=tenant.id, channel="in_app", event_type="PERIOD_REVIEW",
                dedup_key=f"period_review:{uuid.uuid4()}", subject_type="period",
                subject_id=uuid.uuid4(), params={}, locale="en", priority=1, status="SENT",
                attempts=0, next_attempt_at=datetime.now(UTC),
            ),
        ]
    )  # fmt: skip
    await db_session.flush()
    tenant_headers = await sign_in(db_session, mail_settings, tenant)
    closed = await client.delete(f"{API}/users/{tenant.id}", headers=as_(world, world.chairman))
    assert closed.status_code == 204
    assert (await client.get(f"{API}/sessions", headers=tenant_headers)).status_code == 401
    for model in (ChannelBinding, Preference, LinkCode, Delivery):  # bindings, feed and codes
        rows = await db_session.scalars(select(model).where(model.user_id == tenant.id))
        assert list(rows) == []
    # The e-mail can be invited again.
    again = await invite(
        client,
        as_(world, world.owner_14),
        kind="TENANT",
        email=email,
        apartment_id=str(world.apt_14.id),
    )
    assert again.status_code == 201


# --- The chairman's administration ----------------------------------------------------------------


async def test_the_people_list(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    accountant_email = unique("second-books")
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="STAFF", email=accountant_email, building_role="ACCOUNTANT",
    )  # fmt: skip
    form = (await redeem(client, token)).json()
    await register(client, form["registration_token"])
    two_flats = await create_user(db_session, world.building, "Anna Kuusk", email=unique("anna"))
    await create_membership(db_session, two_flats, world.apt_21, mode=DisplayMode.FULL_NAME)
    await create_membership(db_session, two_flats, world.apt_2)
    gone = await create_user(db_session, world.building, "Old One", is_active=False)
    await create_membership(db_session, gone, world.apt_2)

    response = await client.get(f"{API}/users", headers=as_(world, world.chairman))
    assert response.status_code == 200
    people = response.json()["people"]
    rows = [
        (p["building_role"], p["display_label"], p["is_me"], p["invited_email"],
         [(m["apartment_number"], m["display_label"]) for m in p["memberships"]])
        for p in people
    ]  # fmt: skip
    assert rows[0] == ("CHAIRMAN", "Chairman", True, None, [])
    accountants = [r for r in rows if r[0] == "ACCOUNTANT"]
    assert sorted(r[3] or "" for r in accountants) == sorted(["", accountant_email])
    assert rows[-2:] == [
        # Entrance 1 first, apartments in natural order; each label follows its own membership.
        ("USER", "Resident", False, None, [("2", "Apt. 2"), ("21", "Anna Kuusk (Apt. 21)")]),
        ("USER", "I. P.", False, None, [("14", "I. P. (Apt. 14)")]),
    ]
    assert str(gone.id) not in {p["user_id"] for p in people}
    denied = await client.get(f"{API}/users", headers=as_(world, world.owner_14))
    assert denied.json()["error_code"] == "FORBIDDEN"


async def test_the_last_chairman_stays(client: AsyncClient, world: World) -> None:
    chairman = as_(world, world.chairman)
    for response in (
        await client.put(
            f"{API}/users/{world.chairman.id}/role",
            json={"building_role": "BOARD"},
            headers=chairman,
        ),
        await client.delete(f"{API}/users/{world.chairman.id}", headers=chairman),
    ):
        assert (response.status_code, response.json()["error_code"]) == (409, "LAST_CHAIRMAN")
    handover = await client.put(
        f"{API}/users/{world.owner_14.id}/role",
        json={"building_role": "CHAIRMAN"},
        headers=chairman,
    )
    assert handover.json() == {
        "user_id": str(world.owner_14.id),
        "building_role": "CHAIRMAN",
        "account_closed": False,
    }
    step_down = await client.put(
        f"{API}/users/{world.chairman.id}/role", json={"building_role": "USER"}, headers=chairman
    )
    # The old chairman has no flat: becoming USER closes the account.
    assert step_down.json()["account_closed"] is True


async def test_ending_memberships_closes_only_the_last_one(
    client: AsyncClient, world: World, db_session: AsyncSession
) -> None:
    chairman = as_(world, world.chairman)
    m14 = await db_session.scalar(
        select(ApartmentMembership).where(ApartmentMembership.user_id == world.owner_14.id)
    )
    assert m14 is not None
    m2 = await create_membership(db_session, world.owner_14, world.apt_2)
    path = f"{API}/users/{world.owner_14.id}/memberships"
    assert (await client.delete(f"{path}/{m2.id}", headers=chairman)).status_code == 204
    assert await is_active(db_session, world.owner_14)
    assert (await client.delete(f"{path}/{m2.id}", headers=chairman)).json()["error_code"] == (
        "MEMBERSHIP_NOT_FOUND"
    )
    assert (await client.delete(f"{path}/{m14.id}", headers=chairman)).status_code == 204
    assert not await is_active(db_session, world.owner_14)
    unknown = await client.delete(f"{API}/users/{uuid.uuid4()}", headers=chairman)
    assert unknown.json()["error_code"] == "USER_NOT_FOUND"


# --- Retention and the operator command -----------------------------------------------------------


async def test_old_invitations_are_deleted(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession, clock
) -> None:
    chairman = as_(world, world.chairman)
    stale = (
        await invite(client, chairman, kind="OWNER", email=unique("a"), apartment_number="2")
    ).json()
    staff, token = await invite_and_open(
        client, smtp_server, chairman, kind="STAFF", email=unique("b"), building_role="BOARD"
    )
    await register(client, (await redeem(client, token)).json()["registration_token"])
    owner, owner_token = await invite_and_open(
        client, smtp_server, chairman, kind="OWNER", email=unique("c"), apartment_number="21"
    )
    await register(client, (await redeem(client, owner_token)).json()["registration_token"])
    assert world.owner_14.email is not None
    nothing, same_token = await invite_and_open(
        client, smtp_server, chairman,
        kind="OWNER", email=world.owner_14.email, apartment_number="14",
    )  # fmt: skip
    unchanged = await client.post(
        f"{API}/invitations/accept", json={"token": same_token}, headers=as_(world, world.owner_14)
    )
    assert unchanged.json() == {"applied": False}

    async def remaining(now: datetime) -> set[str]:
        await invitations_module.delete_old_invitations(db_session, now)
        return {str(i) for i in await db_session.scalars(select(AuthInvitation.id))}

    # Kept while they explain something: the STAFF role, the active membership of apartment 21.
    later = clock.now + timedelta(days=40)
    assert await remaining(later) == {staff["invitation_id"], owner["invitation_id"]}
    assert stale["invitation_id"] not in await remaining(clock.now)
    assert nothing["invitation_id"] not in await remaining(clock.now)  # applied=false, 30 days
    membership = await db_session.scalar(
        select(ApartmentMembership).where(ApartmentMembership.apartment_id == world.apt_21.id)
    )
    assert membership is not None
    membership.ended_at = clock.now - timedelta(days=31)
    await db_session.flush()
    assert owner["invitation_id"] not in await remaining(clock.now)
    # The applied STAFF invitation goes once the person no longer holds that role.
    board = await db_session.scalar(select(User).where(User.building_role == BuildingRole.BOARD))
    assert board is not None
    board.building_role = BuildingRole.USER
    await db_session.flush()
    assert await remaining(clock.now) == set()


async def test_registration_after_the_invitation_ran_out_is_refused(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    """An expired invitation no longer holds a tenant's place, so its open form cannot be used."""
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.owner_14),
        kind="TENANT", email=unique("late"), apartment_id=str(world.apt_14.id),
    )  # fmt: skip
    form = (await redeem(client, token)).json()
    await db_session.execute(update(AuthInvitation).values(expires_at=datetime.now(UTC)))
    await db_session.flush()
    assert (await register(client, form["registration_token"])).json()["error_code"] == (
        "INVALID_LINK"
    )


async def test_an_account_created_meanwhile_gets_409(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    email = unique("twice")
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="OWNER", email=email, apartment_number="2",
    )  # fmt: skip
    form = (await redeem(client, token)).json()
    await create_user(db_session, world.building, "Already Here", email=email)
    taken = await register(client, form["registration_token"])
    assert (taken.status_code, taken.json()["error_code"]) == (409, "EMAIL_ALREADY_REGISTERED")


async def test_an_account_of_another_building_cannot_use_the_link(
    client: AsyncClient, world: World, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    other = await create_building(db_session, name="KU Teine")
    email = unique("elsewhere")
    await create_user(db_session, other, "Elsewhere", email=email)
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="OWNER", email=email, apartment_number="2",
    )  # fmt: skip
    assert (await redeem(client, token)).json()["error_code"] == "INVALID_LINK"


async def test_replacing_a_tenant_in_a_full_apartment(
    client: AsyncClient, world: World, smtp_server: SmtpServer
) -> None:
    owner = as_(world, world.owner_14)
    await register_tenant(client, world, smtp_server, unique("first"))
    for _ in range(2):
        await invite(
            client, owner, kind="TENANT", email=unique("p"), apartment_id=str(world.apt_14.id)
        )
    residents = (
        await client.get(f"{API}/apartments/{world.apt_14.id}/residents", headers=owner)
    ).json()
    tenant = next(r for r in residents["residents"] if r["relation_type"] == "TENANT")
    full = await invite(
        client, owner, kind="TENANT", email=unique("p"), apartment_id=str(world.apt_14.id)
    )
    assert full.json()["error_code"] == "TENANT_LIMIT_REACHED"
    # The limit counts without the replaced tenant.
    replaced = await client.post(
        f"{API}/apartments/{world.apt_14.id}/tenants/{tenant['user_id']}/replace",
        json={"email": unique("new")},
        headers=owner,
    )
    assert replaced.status_code == 201


async def test_the_first_chairman_command(
    world: World, db_session: AsyncSession, mail_settings: Settings, capsys
) -> None:
    import io
    from contextlib import asynccontextmanager

    from src.modules.auth.__main__ import create_chairman
    from src.plugins.registry import build_plugin_registry

    @asynccontextmanager
    async def factory():
        yield db_session

    plugins = build_plugin_registry(mail_settings)
    out = io.StringIO()
    refused = await create_chairman(
        factory, mail_settings, plugins,
        email=unique("new-chair"), force=False, print_link=False, out=out,
    )  # fmt: skip
    assert refused == 1 and "active chairman" in out.getvalue()

    out = io.StringIO()
    done = await create_chairman(
        factory, mail_settings, plugins,
        email=unique("new-chair"), force=True, print_link=True, out=out,
    )  # fmt: skip
    assert done == 0
    assert "invitation" in out.getvalue() and f"link: {SITE}/login/link#" in out.getvalue()
    row = await db_session.scalar(
        select(AuthInvitation).where(AuthInvitation.invited_by_user_id.is_(None))
    )
    assert row is not None and row.building_role == "CHAIRMAN"


async def test_no_e_mail_token_or_name_in_logs_or_spans(
    client: AsyncClient,
    world: World,
    smtp_server: SmtpServer,
    log_capture,
    span_exporter,
) -> None:
    email = unique("secret-person")
    _, token = await invite_and_open(
        client, smtp_server, as_(world, world.chairman),
        kind="OWNER", email=email, apartment_number="21",
    )  # fmt: skip
    form = (await redeem(client, token)).json()
    await register(client, form["registration_token"], full_name="Salajane Nimi")
    await client.get(f"{API}/users", headers=as_(world, world.chairman))

    exported = " ".join(
        f"{span.name} {dict(span.attributes or {})}" for span in span_exporter.get_finished_spans()
    )
    for secret in (email, token, form["registration_token"], "Salajane Nimi", "198.51.100.7"):
        assert secret not in log_capture.raw
        assert secret not in exported
