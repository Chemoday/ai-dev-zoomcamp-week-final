"""Invitations in demo mode (task E7/A4): stored and usable, but no e-mail goes out unless
AUTH_MAIL_IN_DEMO is set (docs/ARCHITECTURE.md 4.6)."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.main import create_app
from src.modules.auth.models import AuthInvitation
from src.modules.auth.schemas import AuthMethod
from src.modules.auth.service import AuthService
from src.modules.property.schemas import BuildingRole
from tests.factories import create_apartment, create_building, create_user
from tests.smtp_server import SmtpServer


@pytest.fixture
def demo_settings(smtp_server: SmtpServer) -> Settings:
    return Settings(
        **{
            **get_settings().model_dump(),
            "DEMO_MODE_ENABLED": True,
            "AUTH_MAIL_IN_DEMO": False,
            "ENABLED_PLUGINS": "common.csv_export,common.email_smtp",
            "SMTP_HOST": smtp_server.host,
            "SMTP_PORT": smtp_server.port,
            "SMTP_TLS": "none",
            "SMTP_FROM": "KU <noreply@kyu.example.test>",
            "PUBLIC_BASE_URL": "https://kyu.example.test",
        }
    )


@pytest.fixture
def app(demo_settings: Settings) -> FastAPI:
    app = create_app(demo_settings)
    app.dependency_overrides[get_settings] = lambda: demo_settings
    return app


async def test_demo_mode_stores_the_invitation_and_sends_nothing(
    api_client: AsyncClient,
    db_app: FastAPI,
    db_session: AsyncSession,
    demo_settings: Settings,
    smtp_server: SmtpServer,
) -> None:
    building = await create_building(db_session)
    await create_apartment(db_session, building, "2")
    chairman = await create_user(db_session, building, "Chair", role=BuildingRole.CHAIRMAN)
    issued = await AuthService(db_session, demo_settings).start_session(
        chairman.id, AuthMethod.MAGIC_LINK, None
    )
    bearer = {"Authorization": f"Bearer {issued.response.access_token}"}
    body = {"kind": "OWNER", "email": "demo.owner@example.com", "apartment_number": "2"}

    by_demo_header = await api_client.post(
        "/api/v1/auth/invitations", json=body, headers={"X-Demo-User-Id": str(chairman.id)}
    )
    assert by_demo_header.status_code == 401  # account endpoints need a real sign-in
    created = await api_client.post("/api/v1/auth/invitations", json=body, headers=bearer)
    assert created.status_code == 201
    await db_app.state.auth_background.drain(within_seconds=10)
    assert smtp_server.messages == []
    stored = await db_session.scalar(
        select(AuthInvitation).where(AuthInvitation.email == "demo.owner@example.com")
    )
    assert stored is not None
