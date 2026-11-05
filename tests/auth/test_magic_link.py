"""Sign in with an e-mail link (task E7/A3), on a real PostgreSQL with an in-process SMTP server.

Demo mode is off unless a test turns it on. The client uses an https base URL (the refresh cookie
is `Secure`), and a client address per test client, for the per-address limits.
"""

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.main import create_app
from src.modules.auth import links as links_module
from src.modules.auth import service as auth_service
from src.modules.auth.cleanup import cleanup_tick
from src.modules.auth.models import AuthAttempt, AuthToken
from src.modules.auth.policies import REFRESH_COOKIE
from src.modules.property.models import User
from tests.factories import DemoBuilding, create_demo_building
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
async def demo(db_session: AsyncSession) -> DemoBuilding:
    return await create_demo_building(db_session)


@pytest.fixture
def client_at(db_app: FastAPI) -> Callable[[str], AsyncClient]:
    """A client calling from the given address."""

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
    monkeypatch.setattr(links_module, "_now", lambda: clock.now)
    monkeypatch.setattr(auth_service, "_now", lambda: clock.now)
    return clock


async def ask(client: AsyncClient, email: str) -> None:
    """Ask for a link, then wait for the detached work that sends it."""
    response = await client.post(f"{API}/magic-link", json={"email": email})
    assert (response.status_code, response.json()) == (202, {"accepted": True})
    await client._transport.app.state.auth_background.drain(within_seconds=10)  # type: ignore[attr-defined]


def tokens_sent(smtp_server: SmtpServer) -> list[str]:
    found = []
    for message in smtp_server.messages:
        match = LINK.search(message.get_content())
        assert match, "every sign-in mail carries a link"
        found.append(match.group(1))
    return found


async def redeem(client: AsyncClient, token: str):
    return await client.post(f"{API}/magic-link/redeem", json={"token": token}, headers=CSRF)


# --- The happy path -----------------------------------------------------------------------------


async def test_a_known_e_mail_gets_a_link_that_signs_in_once(
    client: AsyncClient, demo: DemoBuilding, smtp_server: SmtpServer
) -> None:
    owner_email = demo.owner_14.email
    assert owner_email is not None

    await ask(client, f"  {owner_email.upper()} ")  # canonical match: case and spaces ignored

    [message] = smtp_server.messages
    assert message["To"] == owner_email
    assert message["Subject"]  # the instance default language
    [token] = tokens_sent(smtp_server)
    signed_in = await redeem(client, token)
    assert signed_in.status_code == 200, signed_in.text
    body = signed_in.json()
    assert body["status"] == "SIGNED_IN"
    assert REFRESH_COOKIE in signed_in.headers["set-cookie"]
    sessions = await client.get(
        f"{API}/sessions",
        headers={"Authorization": f"Bearer {body['session']['access_token']}"},
    )
    assert [s["auth_method"] for s in sessions.json()] == ["MAGIC_LINK"]

    again = await redeem(client, token)
    assert (again.status_code, again.json()["error_code"]) == (400, "INVALID_LINK")


async def test_an_unknown_or_inactive_e_mail_gets_the_same_answer_and_no_mail(
    client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession, smtp_server: SmtpServer
) -> None:
    inactive = await db_session.get(User, demo.tenant_2.id)
    assert inactive is not None
    inactive.email = "gone@example.com"
    inactive.is_active = False
    await db_session.flush()

    await ask(client, "nobody@example.com")
    await ask(client, "gone@example.com")

    assert smtp_server.messages == []


@pytest.mark.parametrize(
    "email",
    ["", "no-at-sign", "a@b", "two words@example.com", "a\x00b@example.com", "x" * 250 + "@ex.com"],
)
async def test_a_malformed_e_mail_is_a_validation_error(client: AsyncClient, email: str) -> None:
    response = await client.post(f"{API}/magic-link", json={"email": email})

    assert (response.status_code, response.json()["error_code"]) == (422, "VALIDATION_ERROR")


# --- Redeeming ----------------------------------------------------------------------------------


async def test_an_expired_or_unknown_link_does_not_sign_in(
    client: AsyncClient, demo: DemoBuilding, smtp_server: SmtpServer, clock
) -> None:
    await ask(client, demo.owner_14.email or "")
    [token] = tokens_sent(smtp_server)
    clock.shift(minutes=15, seconds=1)

    for candidate in (token, "x" * 43):
        response = await redeem(client, candidate)
        assert (response.status_code, response.json()["error_code"]) == (400, "INVALID_LINK")


async def test_redeeming_needs_the_csrf_header(
    client: AsyncClient, demo: DemoBuilding, smtp_server: SmtpServer
) -> None:
    await ask(client, demo.owner_14.email or "")
    [token] = tokens_sent(smtp_server)

    response = await client.post(f"{API}/magic-link/redeem", json={"token": token})

    assert (response.status_code, response.json()["error_code"]) == (403, "CSRF_CHECK_FAILED")
    assert (await redeem(client, token)).status_code == 200, "the link was not used up"


async def test_at_most_twenty_redeem_attempts_per_address_and_hour(
    client: AsyncClient, client_at, db_session: AsyncSession
) -> None:
    for _ in range(20):
        assert (await redeem(client, "guess")).status_code == 400

    blocked = await redeem(client, "guess")

    assert (blocked.status_code, blocked.json()["error_code"]) == (429, "RATE_LIMITED")
    assert blocked.json()["retry_after_seconds"] > 0 and blocked.headers["retry-after"]
    async with client_at("203.0.113.9") as elsewhere:
        assert (await redeem(elsewhere, "guess")).status_code == 400, (
            "another address is counted apart"
        )


# --- Silent limits on asking --------------------------------------------------------------------


async def test_somebody_else_cannot_use_up_a_persons_links(
    client: AsyncClient, client_at, demo: DemoBuilding, smtp_server: SmtpServer
) -> None:
    email = demo.owner_14.email or ""
    for _ in range(6):  # an attacker from one address: only 5 get through
        await ask(client, email)
    assert len(smtp_server.messages) == 5

    async with client_at("203.0.113.7") as own_device:
        await ask(own_device, email)

    assert len(smtp_server.messages) == 6, "the person's own address is counted apart"


async def test_at_most_twenty_links_per_e_mail_and_day(
    client_at, demo: DemoBuilding, smtp_server: SmtpServer
) -> None:
    email = demo.owner_14.email or ""
    for index in range(21):
        async with client_at(f"203.0.113.{index + 1}") as client:
            await ask(client, email)

    assert len(smtp_server.messages) == 20


async def test_at_most_twenty_links_per_address_and_hour(
    client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession, smtp_server: SmtpServer
) -> None:
    users = []
    for index in range(21):
        user = User(
            building_id=demo.building.id,
            full_name=f"Resident {index}",
            email=f"r{index}@example.com",
        )
        db_session.add(user)
        users.append(user)
    await db_session.flush()

    for user in users:
        await ask(client, user.email or "")

    assert len(smtp_server.messages) == 20


# --- Demo mode, startup and privacy -------------------------------------------------------------


async def test_demo_mode_sends_no_login_mail_unless_allowed(
    db_app: FastAPI, client: AsyncClient, demo: DemoBuilding, mail_settings: Settings, smtp_server
) -> None:
    email = demo.owner_14.email or ""
    db_app.state.settings = Settings(**{**mail_settings.model_dump(), "DEMO_MODE_ENABLED": True})
    await ask(client, email)
    assert smtp_server.messages == []

    db_app.state.settings = Settings(
        **{
            **mail_settings.model_dump(),
            "DEMO_MODE_ENABLED": True,
            "AUTH_MAIL_IN_DEMO": True,
            "AUTH_DEMO_MAIL_DOMAINS": "kyu.example.test",
        }
    )
    await ask(client, email)  # example.com is not on the list
    assert smtp_server.messages == []

    db_app.state.settings = Settings(
        **{**mail_settings.model_dump(), "DEMO_MODE_ENABLED": True, "AUTH_MAIL_IN_DEMO": True}
    )
    await ask(client, email)
    assert len(smtp_server.messages) == 1


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"AUTH_MAIL_IN_DEMO": True, "ENVIRONMENT": "prod"}, "AUTH_MAIL_IN_DEMO"),
        ({"PUBLIC_BASE_URL": "", "NOTIFICATIONS_ENABLED": False}, "PUBLIC_BASE_URL"),
    ],
)
def test_the_app_refuses_unsafe_mail_settings(
    mail_settings: Settings, overrides: dict, error: str
) -> None:
    values = {**mail_settings.model_dump(), "AUTH_ALLOW_SESSIONLESS_TOKENS": False, **overrides}

    with pytest.raises(RuntimeError, match=error):
        create_app(Settings(**values))


async def test_no_e_mail_token_or_address_in_logs_or_spans(
    client: AsyncClient,
    demo: DemoBuilding,
    smtp_server: SmtpServer,
    log_capture,
    span_exporter: InMemorySpanExporter,
) -> None:
    email = demo.owner_14.email or ""
    await ask(client, email)
    await ask(client, "nobody@example.com")
    [token] = tokens_sent(smtp_server)
    await redeem(client, token)
    await redeem(client, token)

    exported = " ".join(
        f"{span.name} {dict(span.attributes or {})}" for span in span_exporter.get_finished_spans()
    )
    assert "auth.outcome" in exported
    for secret in (email, "nobody@example.com", token, "198.51.100.1"):
        assert secret not in log_capture.raw
        assert secret not in exported


async def test_the_cleanup_deletes_used_links_and_old_attempts(
    db_app: FastAPI, client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession, smtp_server
) -> None:
    await ask(client, demo.owner_14.email or "")
    await ask(client, demo.owner_14.email or "")
    used, unused = tokens_sent(smtp_server)
    await redeem(client, used)
    old = AuthAttempt(
        kind="PASSWORD",
        subject_hash="0" * 64,
        succeeded=False,
        attempted_at=datetime.now(UTC) - timedelta(hours=25),
    )
    db_session.add(old)
    await db_session.flush()

    await cleanup_tick(db_app.state.auth_session_factory)

    assert len(list(await db_session.scalars(select(AuthToken.id)))) == 1, "only the unused link"
    assert old.id not in set(await db_session.scalars(select(AuthAttempt.id)))
    assert (await redeem(client, unused)).status_code == 200


# --- Timing, failures and boundaries (review of A3) ----------------------------------------------


async def test_the_answer_does_not_wait_for_the_lookup(
    client: AsyncClient, demo: DemoBuilding, monkeypatch: pytest.MonkeyPatch
) -> None:
    answered = asyncio.Event()
    looked_up = asyncio.Event()
    original = links_module.PropertyService.find_active_user_by_email

    async def slow_lookup(self, email: str):
        await asyncio.wait_for(answered.wait(), timeout=5)  # only after the 202 went out
        looked_up.set()
        return await original(self, email)

    monkeypatch.setattr(links_module.PropertyService, "find_active_user_by_email", slow_lookup)

    response = await client.post(f"{API}/magic-link", json={"email": demo.owner_14.email})
    assert response.status_code == 202
    assert not looked_up.is_set(), "the answer came before the account lookup"
    answered.set()
    await client._transport.app.state.auth_background.drain(within_seconds=10)  # type: ignore[attr-defined]
    assert looked_up.is_set()


async def test_a_failing_mail_server_is_logged_without_the_address(
    db_app: FastAPI, client: AsyncClient, demo: DemoBuilding, mail_settings: Settings, log_capture
) -> None:
    broken = Settings(**{**mail_settings.model_dump(), "SMTP_PORT": 1})  # nothing listens there
    from src.plugins.registry import build_plugin_registry

    db_app.state.plugin_registry = build_plugin_registry(broken)

    await ask(client, demo.owner_14.email or "")

    assert "auth_mail_failed" in log_capture.raw and "SMTP_UNAVAILABLE" in log_capture.raw
    assert (demo.owner_14.email or "") not in log_capture.raw


async def test_a_failing_issue_still_answers_202_and_logs_only_the_type(
    client: AsyncClient,
    demo: DemoBuilding,
    monkeypatch: pytest.MonkeyPatch,
    smtp_server,
    log_capture,
) -> None:
    async def broken(self, email: str, address: str):
        raise RuntimeError(f"boom {email}")

    monkeypatch.setattr(links_module.LinkService, "issue_magic_link", broken)

    await ask(client, demo.owner_14.email or "")

    assert smtp_server.messages == []
    assert "auth_magic_link_failed" in log_capture.raw and "RuntimeError" in log_capture.raw
    assert "boom" not in log_capture.raw, "no exception text, not even scrubbed"


async def test_a_link_works_until_its_last_second_and_not_for_a_deactivated_user(
    client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession, smtp_server, clock
) -> None:
    await ask(client, demo.owner_14.email or "")
    await ask(client, demo.owner_14.email or "")
    first, second = tokens_sent(smtp_server)
    clock.shift(minutes=14, seconds=59)
    assert (await redeem(client, first)).status_code == 200

    await db_session.execute(
        User.__table__.update().where(User.id == demo.owner_14.id).values(is_active=False)
    )
    response = await redeem(client, second)
    assert (response.status_code, response.json()["error_code"]) == (400, "INVALID_LINK")


async def test_the_limits_roll_off_after_their_window(
    client: AsyncClient, demo: DemoBuilding, smtp_server, clock
) -> None:
    email = demo.owner_14.email or ""
    for _ in range(6):
        await ask(client, email)
    assert len(smtp_server.messages) == 5

    clock.shift(hours=1, seconds=1)
    await ask(client, email)

    assert len(smtp_server.messages) == 6


async def test_the_cleanup_keeps_recent_attempts_and_deletes_expired_links(
    db_app: FastAPI, client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession, smtp_server
) -> None:
    await ask(client, demo.owner_14.email or "")
    for row in await db_session.scalars(select(AuthToken)):
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    await cleanup_tick(db_app.state.auth_session_factory)

    assert list(await db_session.scalars(select(AuthToken.id))) == []
    assert len(list(await db_session.scalars(select(AuthAttempt.id)))) == 3, "recent attempts stay"


async def test_every_answer_carries_the_referrer_policy(client: AsyncClient) -> None:
    response = await client.post(f"{API}/magic-link", json={"email": "x@example.com"})
    await client._transport.app.state.auth_background.drain(within_seconds=10)  # type: ignore[attr-defined]

    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("::ffff:198.51.100.1", "198.51.100.1", True),  # IPv4-mapped is the IPv4 address
        ("::ffff:198.51.100.1", "::ffff:203.0.113.9", False),
        ("2001:db8:1:2::1", "2001:db8:1:2::ffff", True),  # one /64
        ("2001:db8:1:2::1", "2001:db8:1:3::1", False),
    ],
)
def test_the_per_address_limit_key(first: str, second: str, same: bool) -> None:
    from src.modules.auth.policies import client_key

    assert (client_key(first) == client_key(second)) is same


async def test_background_work_is_bounded() -> None:
    from src.modules.auth import background as background_module

    work = background_module.BackgroundWork()
    running = 0
    peak = 0
    release = asyncio.Event()

    async def job() -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await release.wait()
        running -= 1

    for index in range(background_module.MAX_RUNNING + 2):
        work.spawn(job(), name=f"job-{index}")
    await asyncio.sleep(0.05)
    assert peak == background_module.MAX_RUNNING
    release.set()
    await work.drain(within_seconds=5)
    assert running == 0
