"""Sessions on a real PostgreSQL: refresh with rotation, the grace window, reuse, logout, lists.

Sessions are started through AuthService directly; the sign-in endpoints come with A3-A5. The
client uses an https base URL, because the refresh cookie is `Secure`.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.security import create_access_token
from src.modules.auth import cleanup as cleanup_module
from src.modules.auth import service as auth_service
from src.modules.auth.cleanup import cleanup_tick
from src.modules.auth.models import AuthSession, RefreshToken
from src.modules.auth.policies import REFRESH_COOKIE
from src.modules.auth.schemas import AuthMethod, IssuedSession
from src.modules.auth.service import AuthService
from src.modules.property.models import User
from tests.factories import DemoBuilding, create_demo_building

API = "/api/v1/auth"
CSRF = {"X-Requested-With": "fetch"}
DESKTOP = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/131.0"


@pytest.fixture
async def demo(db_session: AsyncSession) -> DemoBuilding:
    return await create_demo_building(db_session)


@pytest.fixture
async def https_client(db_app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=db_app), base_url="https://test") as client:
        yield client


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Move the service's clock: clock.shift(seconds=61)."""

    class Clock:
        now = datetime.now(UTC)

        def shift(self, **delta: float) -> None:
            self.now += timedelta(**delta)

    clock = Clock()
    monkeypatch.setattr(auth_service, "_now", lambda: clock.now)
    return clock


async def start(db_session: AsyncSession, user: User, settings: Settings) -> IssuedSession:
    issued = await AuthService(db_session, settings).start_session(
        user.id, AuthMethod.MAGIC_LINK, DESKTOP
    )
    await db_session.flush()
    return issued


def bearer(issued: IssuedSession) -> dict[str, str]:
    return {"Authorization": f"Bearer {issued.response.access_token}"}


def with_cookie(client: AsyncClient, token: str) -> None:
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE, token, domain="test.local", path=API)


async def refresh(client: AsyncClient, token: str) -> Response:
    with_cookie(client, token)
    return await client.post(f"{API}/refresh", headers=CSRF)


def set_cookie(response: Response) -> str:
    return response.headers.get("set-cookie", "")


# --- Refresh ------------------------------------------------------------------------------------


async def test_a_refresh_rotates_the_cookie_and_returns_a_session_token(
    https_client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession
) -> None:
    settings = get_settings()
    issued = await start(db_session, demo.owner_14, settings)
    assert issued.response.expires_in == 15 * 60

    response = await refresh(https_client, issued.refresh_token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["session_id"] == str(issued.response.session_id)
    cookie = set_cookie(response)
    assert cookie.startswith(f"{REFRESH_COOKIE}=")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/api/v1/auth"):
        assert flag.lower() in cookie.lower(), flag
    max_age = int(cookie.lower().split("max-age=")[1].split(";")[0])
    assert 7 * 86400 - 60 <= max_age <= 7 * 86400
    assert "domain" not in cookie.lower()
    new_token = cookie.split(";")[0].split("=", 1)[1]
    assert new_token != issued.refresh_token
    sessions = await https_client.get(
        f"{API}/sessions", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert sessions.status_code == 200
    [view] = sessions.json()
    assert (view["current"], view["device_kind"], view["auth_method"]) == (
        True,
        "DESKTOP_BROWSER",
        "MAGIC_LINK",
    )


async def test_two_tabs_refreshing_at_once_both_stay_signed_in(
    https_client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession, clock
) -> None:
    issued = await start(db_session, demo.owner_14, get_settings())

    first = await refresh(https_client, issued.refresh_token)
    clock.shift(seconds=30)
    second = await refresh(https_client, issued.refresh_token)  # the other tab, same old cookie

    assert first.status_code == second.status_code == 200
    assert set_cookie(first) and not set_cookie(second), "the browser already holds the successor"
    assert second.json()["session_id"] == first.json()["session_id"]


async def test_a_replaced_token_used_later_revokes_the_session(
    https_client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession, clock
) -> None:
    issued = await start(db_session, demo.owner_14, get_settings())
    first = await refresh(https_client, issued.refresh_token)
    current_token = set_cookie(first).split(";")[0].split("=", 1)[1]
    access = {"Authorization": f"Bearer {first.json()['access_token']}"}

    clock.shift(seconds=61)
    stolen = await refresh(https_client, issued.refresh_token)

    assert (stolen.status_code, stolen.json()["error_code"]) == (401, "SESSION_EXPIRED")
    assert (
        f'{REFRESH_COOKIE}=""' in set_cookie(stolen) and "max-age=0" in set_cookie(stolen).lower()
    )
    row = await db_session.get(AuthSession, issued.response.session_id, populate_existing=True)
    assert row is not None and row.revoked_at is not None
    after = await refresh(https_client, current_token)
    assert after.status_code == 401, "the legitimate successor dies with the session"
    denied = await https_client.get(f"{API}/sessions", headers=access)
    assert (denied.status_code, denied.json()["error_code"]) == (401, "INVALID_TOKEN")


async def test_a_session_expires_after_a_week_without_use_and_after_ninety_days(
    https_client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession, clock
) -> None:
    started = clock.now
    issued = await start(db_session, demo.owner_14, get_settings())
    token = issued.refresh_token
    for _ in range(20):  # used every 6 days: extended each time, until the 90-day cap
        clock.shift(days=6)
        response = await refresh(https_client, token)
        if response.status_code != 200:
            break
        token = set_cookie(response).split(";")[0].split("=", 1)[1]
    assert response.status_code == 401, "capped even with regular use"
    assert clock.now - started == timedelta(days=90)

    idle = await start(db_session, demo.tenant_2, get_settings())
    clock.shift(days=7, seconds=1)
    expired = await refresh(https_client, idle.refresh_token)
    assert (expired.status_code, expired.json()["error_code"]) == (401, "SESSION_EXPIRED")


@pytest.mark.parametrize("cookie", [None, "not-a-token"])
async def test_a_missing_or_unknown_cookie_is_a_401(https_client: AsyncClient, cookie) -> None:
    https_client.cookies.clear()
    if cookie:
        with_cookie(https_client, cookie)

    response = await https_client.post(f"{API}/refresh", headers=CSRF)

    assert (response.status_code, response.json()["error_code"]) == (401, "SESSION_EXPIRED")


async def test_refresh_logout_and_sign_out_everywhere_need_the_csrf_header(
    https_client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession
) -> None:
    issued = await start(db_session, demo.owner_14, get_settings())
    with_cookie(https_client, issued.refresh_token)

    for path in ("/refresh", "/logout"):
        response = await https_client.post(f"{API}{path}")
        assert (response.status_code, response.json()["error_code"]) == (403, "CSRF_CHECK_FAILED")
    everywhere = await https_client.delete(f"{API}/sessions", headers=bearer(issued))
    assert (everywhere.status_code, everywhere.json()["error_code"]) == (403, "CSRF_CHECK_FAILED")
    still = await refresh(https_client, issued.refresh_token)
    assert still.status_code == 200, "nothing changed without the header"


# --- Logout and the sessions list ---------------------------------------------------------------


async def test_logout_ends_the_session_and_clears_the_cookie(
    https_client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession
) -> None:
    issued = await start(db_session, demo.owner_14, get_settings())
    with_cookie(https_client, issued.refresh_token)

    response = await https_client.post(f"{API}/logout", headers=CSRF)

    assert response.status_code == 204
    assert "max-age=0" in set_cookie(response).lower()
    assert (await refresh(https_client, issued.refresh_token)).status_code == 401
    denied = await https_client.get(f"{API}/sessions", headers=bearer(issued))
    assert denied.status_code == 401
    https_client.cookies.clear()
    assert (await https_client.post(f"{API}/logout", headers=CSRF)).status_code == 204


async def test_the_sessions_list_and_ending_one_or_all(
    https_client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession
) -> None:
    settings = get_settings()
    laptop = await start(db_session, demo.owner_14, settings)
    phone = await AuthService(db_session, settings).start_session(
        demo.owner_14.id, AuthMethod.PASSWORD, "Mozilla/5.0 (Linux; Android 14) Mobile"
    )
    other = await start(db_session, demo.tenant_2, settings)
    await db_session.flush()

    listed = await https_client.get(f"{API}/sessions", headers=bearer(laptop))
    assert {(s["device_kind"], s["current"]) for s in listed.json()} == {
        ("DESKTOP_BROWSER", True),
        ("MOBILE_BROWSER", False),
    }
    foreign = await https_client.delete(
        f"{API}/sessions/{other.response.session_id}", headers=bearer(laptop)
    )
    assert (foreign.status_code, foreign.json()["error_code"]) == (404, "SESSION_NOT_FOUND")
    ended = await https_client.delete(
        f"{API}/sessions/{phone.response.session_id}", headers=bearer(laptop)
    )
    assert ended.status_code == 204
    assert (await https_client.get(f"{API}/sessions", headers=bearer(phone))).status_code == 401

    everywhere = await https_client.delete(f"{API}/sessions", headers={**bearer(laptop), **CSRF})
    assert everywhere.status_code == 204 and "max-age=0" in set_cookie(everywhere).lower()
    assert (await https_client.get(f"{API}/sessions", headers=bearer(laptop))).status_code == 401
    assert (await https_client.get(f"{API}/sessions", headers=bearer(other))).status_code == 200


async def test_the_demo_header_does_not_reach_the_account_endpoints(
    https_client: AsyncClient, demo: DemoBuilding, db_app: FastAPI
) -> None:
    demo_header = {"X-Demo-User-Id": str(demo.owner_14.id), **CSRF}
    responses = [
        await https_client.get(f"{API}/sessions", headers=demo_header),
        await https_client.delete(f"{API}/sessions", headers=demo_header),
        await https_client.delete(f"{API}/sessions/{uuid.uuid4()}", headers=demo_header),
    ]
    for response in responses:
        assert (response.status_code, response.json()["error_code"]) == (401, "NOT_AUTHENTICATED")
    contract = db_app.openapi()
    for path, method in (
        ("/sessions", "get"),
        ("/sessions", "delete"),
        ("/sessions/{session_id}", "delete"),
    ):
        schemes = {
            name for entry in contract["paths"][API + path][method]["security"] for name in entry
        }
        assert schemes == {"HTTPBearer"}, (path, method)


async def test_a_token_of_another_users_session_or_an_expired_one_is_refused(
    https_client: AsyncClient, demo: DemoBuilding, db_session: AsyncSession
) -> None:
    settings = get_settings()
    owner = await start(db_session, demo.owner_14, settings)
    tenant = await start(db_session, demo.tenant_2, settings)
    forged = create_access_token(demo.owner_14.id, settings, session_id=tenant.response.session_id)
    response = await https_client.get(
        f"{API}/sessions", headers={"Authorization": f"Bearer {forged}"}
    )
    assert (response.status_code, response.json()["error_code"]) == (401, "INVALID_TOKEN")

    row = await db_session.get(AuthSession, owner.response.session_id)
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    expired = await https_client.get(f"{API}/sessions", headers=bearer(owner))
    assert (expired.status_code, expired.json()["error_code"]) == (401, "INVALID_TOKEN")


async def test_a_token_without_a_session_needs_the_test_flag(
    db_app: FastAPI, https_client: AsyncClient, demo: DemoBuilding
) -> None:
    strict = Settings(**{**get_settings().model_dump(), "AUTH_ALLOW_SESSIONLESS_TOKENS": False})
    token = create_access_token(demo.owner_14.id, strict)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await https_client.get("/api/v1/property/me", headers=headers)).status_code == 200

    db_app.dependency_overrides[get_settings] = lambda: strict
    try:
        response = await https_client.get("/api/v1/property/me", headers=headers)
    finally:
        db_app.dependency_overrides.pop(get_settings)

    assert (response.status_code, response.json()["error_code"]) == (401, "INVALID_TOKEN")


# --- Clean-up and privacy -----------------------------------------------------------------------


async def test_the_cleanup_deletes_sessions_ended_more_than_a_week_ago(
    db_app: FastAPI, demo: DemoBuilding, db_session: AsyncSession
) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    revoked_long_ago, expired_long_ago, revoked_recently, expired_recently, active = [
        await start(db_session, demo.owner_14, settings) for _ in range(5)
    ]
    for issued, values in (
        (revoked_long_ago, {"revoked_at": now - timedelta(days=8)}),
        (expired_long_ago, {"expires_at": now - timedelta(days=8)}),
        (revoked_recently, {"revoked_at": now - timedelta(days=6)}),
        (expired_recently, {"expires_at": now - timedelta(days=6)}),
    ):
        row = await db_session.get(AuthSession, issued.response.session_id)
        assert row is not None
        for name, value in values.items():
            setattr(row, name, value)
    await db_session.flush()

    deleted = await cleanup_tick(db_app.state.notifications_session_factory)

    assert deleted == 2
    kept = (revoked_recently, expired_recently, active)
    remaining = set(await db_session.scalars(select(AuthSession.id)))
    assert remaining == {i.response.session_id for i in kept}
    tokens = set(await db_session.scalars(select(RefreshToken.session_id)))
    assert tokens == remaining, "the refresh tokens cascade"


async def test_the_cleanup_loop_survives_a_failing_tick(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def broken(factory) -> None:
        calls.append("tick")
        raise RuntimeError("boom")

    async def stop(seconds: float) -> None:
        calls.append(f"sleep {seconds:.0f}")
        raise asyncio.CancelledError

    monkeypatch.setattr(cleanup_module, "cleanup_tick", broken)
    monkeypatch.setattr(cleanup_module, "_sleep", stop)

    with pytest.raises(asyncio.CancelledError):
        await cleanup_module.run_cleanup_loop(lambda: None)  # type: ignore[arg-type,return-value]

    assert calls == ["tick", "sleep 3600"]


async def test_no_token_cookie_or_user_agent_in_logs_or_spans(
    https_client: AsyncClient,
    demo: DemoBuilding,
    db_session: AsyncSession,
    clock,
    log_capture,
    span_exporter: InMemorySpanExporter,
) -> None:
    issued = await start(db_session, demo.owner_14, get_settings())
    with_cookie(https_client, issued.refresh_token)
    first = await https_client.post(f"{API}/refresh", headers={**CSRF, "User-Agent": DESKTOP})
    successor = set_cookie(first).split(";")[0].split("=", 1)[1]
    clock.shift(seconds=61)
    with_cookie(https_client, issued.refresh_token)
    await https_client.post(f"{API}/refresh", headers={**CSRF, "User-Agent": DESKTOP})

    assert "auth_refresh_token_reused" in log_capture.raw
    exported = " ".join(
        f"{span.name} {dict(span.attributes or {})}" for span in span_exporter.get_finished_spans()
    )
    for secret in (issued.refresh_token, successor, first.json()["access_token"], "Firefox"):
        assert secret not in log_capture.raw
        assert secret not in exported
