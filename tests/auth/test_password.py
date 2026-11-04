"""The optional password (task E7/A5), on a real PostgreSQL with an in-process SMTP server.

Demo mode is off. Hashes use cheap Argon2id parameters (the defaults are checked apart), and a
clock fixture moves the time of sessions, attempts and the recency check together.
"""

import asyncio
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher, Type
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.main import create_app
from src.modules.auth import links as links_module
from src.modules.auth import passwords as passwords_module
from src.modules.auth import service as auth_service
from src.modules.auth.models import AuthAttempt, AuthSession, UserIdentity
from src.modules.auth.passwords import PasswordHashing
from src.modules.auth.policies import (
    PASSWORD_PROVIDER,
    REFRESH_COOKIE,
    email_subject,
    keyed_hash,
)
from src.modules.auth.schemas import AuthMethod
from src.modules.auth.service import AuthService
from src.modules.property.models import Building, User
from src.modules.property.schemas import BuildingRole
from tests.factories import create_apartment, create_building, create_membership, create_user
from tests.smtp_server import SmtpServer

API = "/api/v1/auth"
CSRF = {"X-Requested-With": "fetch"}
SITE = "https://kyu.example.test"
LINK = re.compile(re.escape(SITE) + r"/login/link#([A-Za-z0-9_-]+)")
GOOD = "a quiet walk by the sea"
OTHER = "seven green apples on a plate"
CHEAP = {"time_cost": 1, "memory_cost": 8, "parallelism": 1}


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
    app.state.auth_passwords = PasswordHashing(PasswordHasher(**CHEAP))
    return app


@pytest.fixture
def client_at(db_app: FastAPI) -> Callable[[str], AsyncClient]:
    def make(address: str = "198.51.100.1") -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=db_app, client=(address, 4000)), base_url="https://test"
        )

    return make


@pytest.fixture
async def client(client_at) -> AsyncIterator[AsyncClient]:
    async with client_at() as client:
        yield client


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    class Clock:
        now = datetime.now(UTC)

        def shift(self, **delta: float) -> None:
            self.now += timedelta(**delta)

    clock = Clock()
    for module in (passwords_module, links_module, auth_service):
        monkeypatch.setattr(module, "_now", lambda: clock.now)
    return clock


@dataclass
class People:
    building: Building
    ivan: User
    chairman: User
    settings: Settings
    session: AsyncSession

    async def sign_in(self, user: User) -> dict[str, str]:
        issued = await AuthService(self.session, self.settings).start_session(
            user.id, AuthMethod.MAGIC_LINK, None
        )
        return {"Authorization": f"Bearer {issued.response.access_token}"}


@pytest.fixture
async def people(db_session: AsyncSession, mail_settings: Settings, clock) -> People:
    building = await create_building(db_session)
    apartment = await create_apartment(db_session, building, "14", entrance=2)
    ivan = await create_user(
        db_session, building, "Ivan Petrov", email=f"ivan.{uuid.uuid4().hex[:8]}@example.com"
    )
    await create_membership(db_session, ivan, apartment)
    chairman = await create_user(
        db_session,
        building,
        "Oleg Sidorov",
        role=BuildingRole.CHAIRMAN,
        email=f"chair.{uuid.uuid4().hex[:8]}@example.com",
    )
    return People(building, ivan, chairman, mail_settings, db_session)


async def drain(client: AsyncClient) -> None:
    await client._transport.app.state.auth_background.drain(within_seconds=10)  # type: ignore[attr-defined]


async def set_password(client: AsyncClient, headers: dict, new: str, current: str | None = None):
    body: dict[str, str] = {"new_password": new}
    if current is not None:
        body["current_password"] = current
    return await client.put(f"{API}/password", json=body, headers=headers)


async def login(client: AsyncClient, email: str | None, password: str):
    return await client.post(
        f"{API}/password/login", json={"email": email, "password": password}, headers=CSRF
    )


async def with_password(client: AsyncClient, people: People, user: User, password: str) -> None:
    response = await set_password(client, await people.sign_in(user), password)
    assert response.status_code == 204, response.text
    await drain(client)


async def failures(db_session: AsyncSession) -> int:
    return (
        await db_session.scalar(
            select(func.count())
            .select_from(AuthAttempt)
            .where(AuthAttempt.kind == "PASSWORD", AuthAttempt.succeeded.is_(False))
        )
        or 0
    )


# --- Setting a password and signing in ----------------------------------------------------------


async def test_a_password_set_after_a_recent_sign_in_signs_in(
    client: AsyncClient, people: People, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    headers = await people.sign_in(people.ivan)
    assert (await client.get(f"{API}/password", headers=headers)).json() == {"is_set": False}

    assert (await set_password(client, headers, GOOD)).status_code == 204
    await drain(client)

    assert (await client.get(f"{API}/password", headers=headers)).json() == {"is_set": True}
    signed_in = await login(client, f"  {people.ivan.email.upper()} ", GOOD)  # type: ignore[union-attr]
    assert signed_in.status_code == 200, signed_in.text
    assert REFRESH_COOKIE in signed_in.headers["set-cookie"]
    token = signed_in.json()["access_token"]
    sessions = await client.get(f"{API}/sessions", headers={"Authorization": f"Bearer {token}"})
    assert "PASSWORD" in [s["auth_method"] for s in sessions.json()]
    [notice] = smtp_server.messages
    assert notice["To"] == people.ivan.email
    assert SITE in notice.get_content()
    stored = await db_session.scalar(
        select(UserIdentity.secret_data).where(UserIdentity.user_id == people.ivan.id)
    )
    assert stored is not None and stored.startswith("$argon2id$") and GOOD not in stored


async def test_a_change_ends_the_other_sessions_but_not_this_one(
    client: AsyncClient, people: People, db_session: AsyncSession
) -> None:
    this = await people.sign_in(people.ivan)
    other = await people.sign_in(people.ivan)

    assert (await set_password(client, this, GOOD)).status_code == 204

    assert (await client.get(f"{API}/password", headers=this)).status_code == 200
    rejected = await client.get(f"{API}/password", headers=other)
    assert (rejected.status_code, rejected.json()["error_code"]) == (401, "INVALID_TOKEN")


async def test_any_script_works_and_nfkc_matches_the_same_letter_typed_differently(
    client: AsyncClient, people: People
) -> None:
    # "moya koshka yolochka" in Cyrillic; "yo" typed as one letter, then as "e" plus a diaeresis.
    composed = (
        "\u043c\u043e\u044f \u043a\u043e\u0448\u043a\u0430 \u0451\u043b\u043e\u0447\u043a\u0430"
    )
    decomposed = composed.replace("\u0451", "\u0435\u0308")
    await with_password(client, people, people.ivan, composed)

    response = await login(client, people.ivan.email, decomposed)

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("fourteen chars", "TOO_SHORT"),
        ("x" * 100 + "y" * 29, "TOO_LONG"),
        ("Correct Horse Battery Staple", "TOO_COMMON"),
        ("abababababababab", "TOO_COMMON"),
        ("               ", "TOO_COMMON"),
    ],
)
async def test_a_weak_password_is_refused_with_the_reason(
    client: AsyncClient, people: People, password: str, reason: str
) -> None:
    response = await set_password(client, await people.sign_in(people.ivan), password)

    assert response.status_code == 422
    assert (response.json()["error_code"], response.json()["reason"]) == (
        "PASSWORD_TOO_WEAK",
        reason,
    )


async def test_a_huge_field_is_a_validation_error(client: AsyncClient, people: People) -> None:
    response = await set_password(client, await people.sign_in(people.ivan), "x" * 1025)

    assert (response.status_code, response.json()["error_code"]) == (422, "VALIDATION_ERROR")


# --- A recent sign-in or the current password ---------------------------------------------------


async def test_an_old_sign_in_needs_the_current_password(
    client: AsyncClient, people: People, clock, db_session: AsyncSession
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    headers = await people.sign_in(people.ivan)
    clock.shift(minutes=10, seconds=1)

    missing = await set_password(client, headers, OTHER)
    wrong = await set_password(client, headers, OTHER, current="not the right phrase")
    right = await set_password(client, headers, OTHER, current=GOOD)

    for refused in (missing, wrong):
        assert (refused.status_code, refused.json()["error_code"]) == (
            403,
            "REAUTHENTICATION_REQUIRED",
        )
    assert await failures(db_session) == 3, "the wrong one counted (pair, address, e-mail)"
    assert right.status_code == 204
    assert (await login(client, people.ivan.email, OTHER)).status_code == 200


async def test_a_wrong_current_password_is_throttled_like_a_login(
    client: AsyncClient, people: People, clock
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    headers = await people.sign_in(people.ivan)
    clock.shift(minutes=11)
    for _ in range(5):
        assert (
            await set_password(client, headers, OTHER, current="wrong guess")
        ).status_code == 403

    blocked = await set_password(client, headers, OTHER, current=GOOD)

    assert (blocked.status_code, blocked.json()["error_code"]) == (429, "RATE_LIMITED")


async def test_removing_the_password_leaves_only_the_e_mail_link(
    client: AsyncClient, people: People, smtp_server: SmtpServer, db_session: AsyncSession
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    other = await people.sign_in(people.ivan)
    this = await people.sign_in(people.ivan)

    removed = await client.request("DELETE", f"{API}/password", headers=this)
    await drain(client)

    assert removed.status_code == 204
    assert (await client.get(f"{API}/password", headers=this)).json() == {"is_set": False}
    assert (await client.get(f"{API}/password", headers=other)).status_code == 401
    assert len(smtp_server.messages) == 2, "one notice for setting, one for removing"
    assert (await login(client, people.ivan.email, GOOD)).status_code == 401


async def test_removing_when_none_is_set_needs_no_check_and_sends_nothing(
    client: AsyncClient, people: People, smtp_server: SmtpServer, clock
) -> None:
    headers = await people.sign_in(people.ivan)
    clock.shift(hours=1)

    response = await client.request("DELETE", f"{API}/password", headers=headers)
    await drain(client)

    assert response.status_code == 204
    assert smtp_server.messages == []


async def test_removing_with_an_old_sign_in_needs_the_current_password(
    client: AsyncClient, people: People, clock
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    headers = await people.sign_in(people.ivan)
    clock.shift(minutes=11)

    refused = await client.request("DELETE", f"{API}/password", headers=headers)
    removed = await client.request(
        "DELETE", f"{API}/password", headers=headers, json={"current_password": GOOD}
    )

    assert (refused.status_code, refused.json()["error_code"]) == (403, "REAUTHENTICATION_REQUIRED")
    assert removed.status_code == 204


async def test_the_password_endpoints_refuse_the_demo_header(
    db_app: FastAPI, client: AsyncClient, people: People, mail_settings: Settings
) -> None:
    db_app.state.settings = Settings(**{**mail_settings.model_dump(), "DEMO_MODE_ENABLED": True})
    demo = {"X-Demo-User-Id": str(people.ivan.id)}

    for method in ("GET", "PUT", "DELETE"):
        response = await client.request(
            method,
            f"{API}/password",
            headers=demo,
            json={"new_password": GOOD} if method == "PUT" else None,
        )
        assert (response.status_code, response.json()["error_code"]) == (401, "NOT_AUTHENTICATED")


# --- One answer for every failure ---------------------------------------------------------------


async def test_every_failure_is_the_same_answer_after_exactly_one_hash(
    db_app: FastAPI,
    client: AsyncClient,
    client_at,
    people: People,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    await with_password(client, people, people.chairman, OTHER)
    refused = await create_user(
        db_session, people.building, "Anna Ilves", email=f"anna.{uuid.uuid4().hex[:8]}@example.com"
    )
    await with_password(client, people, refused, GOOD)
    await refuse(db_session, refused)
    closed = await db_session.get(User, people.chairman.id)
    assert closed is not None
    closed.is_active = False
    await db_session.flush()
    calls: list[str] = []

    class CountingHasher(PasswordHasher):
        def verify(self, hash: str | bytes, password: str | bytes) -> bool:  # noqa: A002
            calls.append(str(hash))
            return super().verify(hash, password)

    db_app.state.auth_passwords = PasswordHashing(CountingHasher(**CHEAP))
    no_password = await create_user(
        db_session, people.building, "Mari Tamm", email=f"mari.{uuid.uuid4().hex[:8]}@example.com"
    )

    cases = [
        ("unknown", "nobody@example.com", GOOD),
        ("wrong", people.ivan.email, "not the right phrase"),
        ("no password", no_password.email, GOOD),
        ("closed", closed.email, OTHER),
        ("refused, even with the right password", refused.email, GOOD),
    ]
    answers = []
    for index, (_, email, password) in enumerate(cases):
        calls.clear()
        async with client_at(f"203.0.113.{index + 1}") as fresh:
            response = await login(fresh, email, password)
        answers.append((response.status_code, response.json()))
        assert len(calls) == 1, "every failure costs exactly one hash"

    assert all(answer == answers[0] for answer in answers)
    assert answers[0][0] == 401 and answers[0][1]["error_code"] == "INVALID_CREDENTIALS"


async def test_login_needs_the_csrf_header(client: AsyncClient, people: People) -> None:
    response = await client.post(
        f"{API}/password/login", json={"email": people.ivan.email, "password": GOOD}
    )

    assert (response.status_code, response.json()["error_code"]) == (403, "CSRF_CHECK_FAILED")


# --- Throttling without lockout -----------------------------------------------------------------


async def test_five_failures_for_an_e_mail_and_address_start_a_growing_delay(
    client: AsyncClient, client_at, people: People, clock
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    email = people.ivan.email
    for _ in range(5):
        assert (await login(client, email, "wrong guess")).status_code == 401

    first = await login(client, email, GOOD)
    assert (first.status_code, first.json()["error_code"]) == (429, "RATE_LIMITED")
    assert 0 < first.json()["retry_after_seconds"] <= 30
    assert first.headers["retry-after"] == str(first.json()["retry_after_seconds"])
    async with client_at("203.0.113.50") as own_device:
        assert (await login(own_device, email, GOOD)).status_code == 200, "another address"

    clock.shift(seconds=30)
    assert (await login(client, email, "wrong again")).status_code == 401
    second = await login(client, email, GOOD)
    assert 30 < second.json()["retry_after_seconds"] <= 60, "the delay doubled"

    clock.shift(seconds=60)
    assert (await login(client, email, GOOD)).status_code == 200
    assert (await login(client, email, "one typo")).status_code == 401, "a success reset it"


async def test_the_delay_is_the_same_for_an_unknown_e_mail(client: AsyncClient) -> None:
    for _ in range(5):
        assert (await login(client, "nobody@example.com", "wrong guess")).status_code == 401

    response = await login(client, "nobody@example.com", "wrong guess")

    assert (response.status_code, response.json()["error_code"]) == (429, "RATE_LIMITED")


async def test_thirty_failures_from_one_address_slow_down_that_address(
    client: AsyncClient, client_at, people: People
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    for index in range(30):
        assert (await login(client, f"guess{index}@example.com", "wrong guess")).status_code == 401

    blocked = await login(client, people.ivan.email, GOOD)

    assert blocked.status_code == 429
    async with client_at("203.0.113.60") as elsewhere:
        assert (await login(elsewhere, people.ivan.email, GOOD)).status_code == 200


async def test_a_success_does_not_reset_the_limit_of_the_address(
    client: AsyncClient, client_at, people: People
) -> None:
    """Someone's own sign-in must not let the same address keep guessing at other e-mails."""
    await with_password(client, people, people.ivan, GOOD)
    for index in range(29):
        assert (await login(client, f"guess{index}@example.com", "wrong guess")).status_code == 401
    assert (await login(client, people.ivan.email, GOOD)).status_code == 200

    assert (await login(client, "guess29@example.com", "wrong guess")).status_code == 401
    blocked = await login(client, "guess30@example.com", "wrong guess")

    assert (blocked.status_code, blocked.json()["error_code"]) == (429, "RATE_LIMITED")


async def test_a_failure_is_counted_before_the_hash_runs(
    db_app: FastAPI,
    client: AsyncClient,
    people: People,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parallel guesses see each other's failures: the attempt is committed before the hash."""
    await with_password(client, people, people.ivan, GOOD)
    hashing: PasswordHashing = db_app.state.auth_passwords
    seen: list[int] = []
    original = hashing.verify

    async def checking_verify(encoded, password):
        seen.append(await failures(db_session))
        return await original(encoded, password)

    monkeypatch.setattr(hashing, "verify", checking_verify)

    assert (await login(client, people.ivan.email, GOOD)).status_code == 200

    assert seen == [3], "reserved as a failure (pair, address, e-mail) while the hash ran"
    assert await failures(db_session) == 0, "and turned into a success afterwards"


async def test_a_hundred_failures_in_a_day_refuse_the_password_until_a_link_sign_in(
    client: AsyncClient,
    people: People,
    smtp_server: SmtpServer,
    db_session: AsyncSession,
    mail_settings: Settings,
    clock,
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    email = people.ivan.email or ""
    subject = keyed_hash(mail_settings.AUTH_HASH_SECRET.get_secret_value(), email_subject(email))
    db_session.add_all(
        AuthAttempt(
            kind="PASSWORD",
            subject_hash=subject,
            succeeded=False,
            attempted_at=clock.now - timedelta(hours=2),
        )
        for _ in range(99)
    )
    await db_session.flush()

    assert (await login(client, email, "the hundredth guess")).status_code == 401
    clock.shift(minutes=1)
    refused = await login(client, email, GOOD)
    headers = await people.sign_in(people.ivan)

    assert (refused.status_code, refused.json()["error_code"]) == (401, "INVALID_CREDENTIALS")
    assert (await client.get(f"{API}/password", headers=headers)).json() == {"is_set": True}

    # A sign-in with an e-mail link lifts it.
    await client.post(f"{API}/magic-link", json={"email": email})
    await drain(client)
    [token] = [
        match.group(1)
        for message in smtp_server.messages
        if (match := LINK.search(message.get_content()))
    ]
    redeemed = await client.post(f"{API}/magic-link/redeem", json={"token": token}, headers=CSRF)
    assert redeemed.status_code == 200
    assert (await login(client, email, GOOD)).status_code == 200

    # The link sign-in started the day's count again: one more typo does not refuse it again.
    clock.shift(minutes=1)
    assert (await login(client, email, "one more typo")).status_code == 401
    clock.shift(minutes=1)
    assert (await login(client, email, GOOD)).status_code == 200


async def refuse(db_session: AsyncSession, user: User) -> None:
    await db_session.execute(
        update(UserIdentity)
        .where(UserIdentity.user_id == user.id)
        .values(requires_link_sign_in=True)
    )
    await db_session.flush()


async def test_a_new_password_lifts_the_refusal(
    client: AsyncClient, people: People, db_session: AsyncSession
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    await refuse(db_session, people.ivan)
    assert (await login(client, people.ivan.email, GOOD)).status_code == 401

    assert (await set_password(client, await people.sign_in(people.ivan), OTHER)).status_code == 204

    assert (await login(client, people.ivan.email, OTHER)).status_code == 200


async def test_a_refused_password_is_wrong_as_the_current_password_too(
    client: AsyncClient, people: People, db_session: AsyncSession, clock
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    headers = await people.sign_in(people.ivan)
    await refuse(db_session, people.ivan)
    clock.shift(minutes=11)

    response = await set_password(client, headers, OTHER, current=GOOD)

    assert (response.status_code, response.json()["error_code"]) == (
        403,
        "REAUTHENTICATION_REQUIRED",
    )


async def test_a_busy_server_answers_429_at_once_without_counting(
    db_app: FastAPI, client: AsyncClient, people: People, db_session: AsyncSession
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    db_app.state.auth_passwords._queued = passwords_module.MAX_QUEUED_HASHES

    response = await login(client, people.ivan.email, GOOD)

    assert (response.status_code, response.json()["retry_after_seconds"]) == (429, 5)
    assert await failures(db_session) == 0


async def test_an_account_closed_while_the_hash_runs_keeps_no_password(
    db_app: FastAPI, client: AsyncClient, people: People, db_session: AsyncSession
) -> None:
    headers = await people.sign_in(people.ivan)
    hashing: PasswordHashing = db_app.state.auth_passwords
    original = hashing.hash

    async def closing_hash(password: str) -> str:
        encoded = await original(password)
        await db_session.execute(
            update(User).where(User.id == people.ivan.id).values(is_active=False)
        )
        return encoded

    hashing.hash = closing_hash  # type: ignore[method-assign]

    response = await set_password(client, headers, GOOD)

    assert (response.status_code, response.json()["error_code"]) == (401, "UNKNOWN_USER")
    left = await db_session.scalar(
        select(func.count()).select_from(UserIdentity).where(UserIdentity.user_id == people.ivan.id)
    )
    assert left == 0


# --- Storage ------------------------------------------------------------------------------------


async def test_an_old_hash_is_upgraded_on_sign_in(
    db_app: FastAPI, client: AsyncClient, people: People, db_session: AsyncSession
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    before = await db_session.scalar(
        select(UserIdentity.secret_data).where(UserIdentity.user_id == people.ivan.id)
    )
    db_app.state.auth_passwords = PasswordHashing(PasswordHasher(**{**CHEAP, "time_cost": 2}))

    assert (await login(client, people.ivan.email, GOOD)).status_code == 200

    db_session.expunge_all()
    after = await db_session.scalar(
        select(UserIdentity.secret_data).where(UserIdentity.user_id == people.ivan.id)
    )
    assert before is not None and after is not None and before != after and ",t=2," in after
    assert (await login(client, people.ivan.email, GOOD)).status_code == 200


async def test_setting_twice_keeps_one_password_per_account(
    client: AsyncClient, people: People, db_session: AsyncSession
) -> None:
    headers = await people.sign_in(people.ivan)
    assert (await set_password(client, headers, GOOD)).status_code == 204
    assert (await set_password(client, headers, OTHER)).status_code == 204

    rows = list(
        await db_session.scalars(
            select(UserIdentity).where(
                UserIdentity.user_id == people.ivan.id, UserIdentity.provider == PASSWORD_PROVIDER
            )
        )
    )
    assert len(rows) == 1
    assert (await login(client, people.ivan.email, GOOD)).status_code == 401
    assert (await login(client, people.ivan.email, OTHER)).status_code == 200


async def test_closing_the_account_deletes_the_password(
    client: AsyncClient, people: People, db_session: AsyncSession
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    email = people.ivan.email
    chairman = await people.sign_in(people.chairman)

    closed = await client.delete(f"{API}/users/{people.ivan.id}", headers=chairman)

    assert closed.status_code == 204, closed.text
    left = await db_session.scalar(
        select(func.count()).select_from(UserIdentity).where(UserIdentity.user_id == people.ivan.id)
    )
    assert left == 0
    assert (await login(client, email, GOOD)).status_code == 401


async def test_no_password_e_mail_or_address_in_logs_or_spans(
    client: AsyncClient,
    people: People,
    log_capture,
    span_exporter: InMemorySpanExporter,
) -> None:
    await with_password(client, people, people.ivan, GOOD)
    await login(client, people.ivan.email, GOOD)
    await login(client, people.ivan.email, "a wrong guess here")
    await login(client, "nobody@example.com", GOOD)

    exported = " ".join(
        f"{span.name} {dict(span.attributes or {})}" for span in span_exporter.get_finished_spans()
    )
    assert "auth.outcome" in exported
    for secret in (GOOD, "a wrong guess here", people.ivan.email, "nobody@", "198.51.100.1"):
        assert secret not in log_capture.raw
        assert secret not in exported


async def test_sessions_of_a_password_sign_in_are_marked(
    client: AsyncClient, people: People, db_session: AsyncSession
) -> None:
    await with_password(client, people, people.ivan, GOOD)

    await login(client, people.ivan.email, GOOD)

    methods = set(
        await db_session.scalars(
            select(AuthSession.auth_method).where(AuthSession.user_id == people.ivan.id)
        )
    )
    assert "PASSWORD" in methods


# --- The hashing itself -------------------------------------------------------------------------


def test_the_default_parameters_are_argon2id_of_rfc_9106() -> None:
    hasher = PasswordHashing().hasher

    assert (hasher.type, hasher.time_cost, hasher.memory_cost, hasher.parallelism) == (
        Type.ID,
        3,
        64 * 1024,
        4,
    )


async def test_at_most_two_hashes_run_at_once() -> None:
    running = 0
    most = 0
    lock = threading.Lock()

    class SlowHasher(PasswordHasher):
        def hash(self, password: str | bytes, *, salt: bytes | None = None) -> str:
            nonlocal running, most
            with lock:
                running += 1
                most = max(most, running)
            time.sleep(0.05)
            with lock:
                running -= 1
            return "x"

    hashing = PasswordHashing(SlowHasher(**CHEAP))

    await asyncio.gather(*(hashing.hash("p") for _ in range(6)))

    assert most == 2
