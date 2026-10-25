"""Request-session lifecycle and database error hygiene."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import DbSession, get_db_session
from tests.factories import create_building, create_user


def _recording_app(app: FastAPI, events: list[str]):
    """ASGI wrapper that records the moment the response starts being sent."""

    async def wrapper(scope, receive, send):
        async def recording_send(message):
            if message["type"] == "http.response.start":
                events.append("response.start")
            await send(message)

        await app(scope, receive, recording_send)

    return wrapper


async def test_session_commits_before_the_response_is_sent(app: FastAPI) -> None:
    # Regression: with FastAPI's default dependency scope the commit ran after the client already
    # had its 2xx. httpx's ASGITransport waits for the whole app, so only the order shows it.
    events: list[str] = []

    async def tracking_session() -> AsyncIterator[str]:
        yield "session"
        events.append("commit")

    async def endpoint(session: DbSession) -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_db_session] = tracking_session
    app.add_api_route("/test/db-scope", endpoint)
    transport = ASGITransport(app=_recording_app(app, events))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/test/db-scope")

    assert response.status_code == 200
    assert events == ["commit", "response.start"]


async def test_failed_commit_is_reported_as_an_error_not_a_success(app: FastAPI) -> None:
    async def failing_commit_session() -> AsyncIterator[str]:
        yield "session"
        raise ConnectionError("connection lost during COMMIT")

    async def endpoint(session: DbSession) -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[get_db_session] = failing_commit_session
    app.add_api_route("/test/db-commit-fails", endpoint)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/test/db-commit-fails")

    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL_SERVER_ERROR"


async def test_database_errors_do_not_carry_bound_parameters(db_session: AsyncSession) -> None:
    # Exception texts reach logs and spans; with bound parameters they would contain the name.
    building = await create_building(db_session)
    await create_user(db_session, building, "Тайный Соседов", email="secret.neighbour@example.com")

    with pytest.raises(IntegrityError) as caught:
        await create_user(
            db_session, building, "Тайный Соседов", email="secret.neighbour@example.com"
        )

    message = str(caught.value)
    assert "core_users_email_key" in message
    assert "Тайный Соседов" not in message
    assert "secret.neighbour@example.com" not in message
    await db_session.rollback()
