"""Telegram on a real PostgreSQL with a fake Bot API: linking, the webhook, sending (task N4).

Demo mode is off and requests authenticate with a test JWT (owner decision: external channels run
nowhere yet and are accepted by these tests).
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.security import create_access_token
from src.main import create_app
from src.modules.notifications.dispatcher import Dispatcher
from src.modules.notifications.models import ChannelBinding, Delivery, LinkCode
from src.modules.property.models import User
from src.modules.property.schemas import Locale
from src.plugins.common.telegram import TelegramChannel
from tests.notifications.conftest import NotifWorld

JWT_SECRET = "telegram-tests-secret-at-least-32-bytes"
WEBHOOK_SECRET = "webhook-secret-for-tests-0123456789abcdef"
BOT_TOKEN = "123456789:AAFakeTokenForTelegramTests_0123456789"
SITE = "https://kyu.example.test"
TITLE = "Cold water off until 18:00"
BODY = "A riser in entrance 2 is being replaced."
CHAT_A, CHAT_B = 987654321001, 987654321002
API = "/api/v1/notifications"


@dataclass
class FakeBotApi:
    """Records sendMessage calls; `status` per chat simulates errors (e.g. 403: bot blocked)."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    status: dict[int, int] = field(default_factory=dict)
    description: dict[int, str] = field(default_factory=dict)

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        code = self.status.get(payload["chat_id"], 200)
        if code != 200:
            text = self.description.get(payload["chat_id"], "Forbidden: bot was blocked")
            return httpx.Response(code, json={"ok": False, "description": text})
        self.sent.append(payload)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.sent)}})

    def texts_to(self, chat_id: int) -> list[str]:
        return [m["text"] for m in self.sent if m["chat_id"] == chat_id]


@pytest.fixture
def telegram_settings() -> Settings:
    return Settings(
        **{
            **get_settings().model_dump(),
            "DEMO_MODE_ENABLED": False,
            "JWT_SECRET_KEY": JWT_SECRET,
            "ENABLED_PLUGINS": "common.csv_export,common.telegram",
            "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "TELEGRAM_BOT_USERNAME": "KyuTestBot",
            "TELEGRAM_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "NOTIFICATIONS_HASH_SECRET": "hash-secret-for-tests-0123456789abcdef",
            "PUBLIC_BASE_URL": SITE,
            "NOTIFICATIONS_DISPATCHER_ENABLED": False,
        }
    )


@pytest.fixture
def bot_api(monkeypatch: pytest.MonkeyPatch) -> FakeBotApi:
    fake = FakeBotApi()
    monkeypatch.setattr(TelegramChannel, "http_transport", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def app(span_exporter: InMemorySpanExporter, telegram_settings: Settings) -> FastAPI:
    app = create_app(telegram_settings)
    app.dependency_overrides[get_settings] = lambda: telegram_settings
    return app


@pytest.fixture
def dispatcher(db_app: FastAPI) -> Dispatcher:
    return db_app.state.notifications_dispatcher


def bearer(user: User, settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, settings)}"}


async def new_code(client: AsyncClient, user: User, settings: Settings) -> dict[str, Any]:
    response = await client.post(f"{API}/telegram/link-code", headers=bearer(user, settings))
    assert response.status_code == 201, response.text
    return response.json()


def start(code: str, chat_id: int, chat_type: str = "private") -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {"message_id": 1, "text": f"/start {code}",
                    "chat": {"id": chat_id, "type": chat_type}},
    }  # fmt: skip


async def webhook(
    client: AsyncClient, update: Any, secret: str | None = WEBHOOK_SECRET
) -> httpx.Response:
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret else {}
    content = update if isinstance(update, bytes) else json.dumps(update).encode()
    return await client.post(f"{API}/telegram/webhook", content=content, headers=headers)


async def link(client: AsyncClient, user: User, chat_id: int, settings: Settings) -> dict[str, Any]:
    code = (await new_code(client, user, settings))["code"]
    response = await webhook(client, start(code, chat_id))
    assert (response.status_code, response.json()) == (200, {})
    return {"code": code}


async def active_bindings(session: AsyncSession) -> list[ChannelBinding]:
    rows = await session.scalars(
        select(ChannelBinding)
        .where(ChannelBinding.revoked_at.is_(None))
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def telegram_rows(session: AsyncSession, **where: Any) -> list[Delivery]:
    query = (
        select(Delivery)
        .where(Delivery.channel == "telegram")
        .execution_options(populate_existing=True)
    )
    for name, value in where.items():
        query = query.where(getattr(Delivery, name) == value)
    return list(await session.scalars(query.order_by(Delivery.created_at)))


async def publish(client: AsyncClient, author: User, settings: Settings) -> str:
    response = await client.post(
        "/api/v1/communication/posts",
        json={"post_type": "EMERGENCY", "category": "WATER_SHUTOFF", "title": TITLE,
              "body": BODY, "target_scope": "ENTRANCE", "target_entrance": 2},
        headers=bearer(author, settings),
    )  # fmt: skip
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- Linking --------------------------------------------------------------------------------------


async def test_a_linked_private_chat_receives_the_alert(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    world.owner_14.locale = Locale.EN
    await db_session.flush()
    code = await new_code(api_client, world.owner_14, telegram_settings)
    assert len(code["code"]) == 8 and set(code["code"]) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    assert code["deep_link"] == f"https://t.me/KyuTestBot?start={code['code']}"
    expires = datetime.fromisoformat(code["expires_at"])
    assert timedelta(minutes=9) < expires - datetime.now(UTC) <= timedelta(minutes=10)

    response = await webhook(api_client, start(code["code"].lower(), CHAT_A))  # case-insensitive

    assert (response.status_code, response.json()) == (200, {})
    assert bot_api.texts_to(CHAT_A) == [
        "Done: this chat now receives the notifications of your apartment association."
    ]
    owner = bearer(world.owner_14, telegram_settings)
    bindings = await api_client.get(f"{API}/bindings", headers=owner)
    assert [(b["channel"], b["label"]) for b in bindings.json()] == [("telegram", "Telegram")]
    assert str(CHAT_A) not in bindings.text, "the chat id is never returned"
    prefs = await api_client.get(f"{API}/preferences", headers=owner)
    telegram = next(c for c in prefs.json()["channels"] if c["channel"] == "telegram")
    assert telegram["available"] is True

    await publish(api_client, world.chairman, telegram_settings)
    [row] = await telegram_rows(db_session)
    assert (row.user_id, row.status, row.binding_id) == (
        world.owner_14.id,
        "PENDING",
        (await active_bindings(db_session))[0].id,
    )
    assert await dispatcher.tick() == 1

    alert = bot_api.texts_to(CHAT_A)[-1]
    assert alert.startswith(f"Emergency: {TITLE}\n\n{BODY}\n\nAffected: entrance 2")
    assert SITE in alert
    [row] = await telegram_rows(db_session)
    assert row.status == "SENT"


async def test_group_chats_and_reused_or_expired_codes_link_nothing(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    db_session: AsyncSession,
) -> None:
    code = (await new_code(api_client, world.owner_14, telegram_settings))["code"]

    await webhook(api_client, start(code, CHAT_B, chat_type="group"))
    assert await active_bindings(db_session) == []
    assert bot_api.sent == [], "groups get no reply at all"

    await webhook(api_client, start(code, CHAT_A))  # links
    await webhook(api_client, start(code, CHAT_B))  # the same code again, another chat
    [binding] = await active_bindings(db_session)
    assert binding.config == {"chat_id": CHAT_A}
    assert bot_api.texts_to(CHAT_B) == [
        "See kood on vale, kasutatud või aegunud. Looge uus kood veebilehe teavituste seadetes."
    ]

    expired = (await new_code(api_client, world.board_15, telegram_settings))["code"]
    await db_session.execute(
        update(LinkCode)
        .where(LinkCode.user_id == world.board_15.id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    await webhook(api_client, start(expired, CHAT_B))
    assert [b.user_id for b in await active_bindings(db_session)] == [world.owner_14.id]


async def test_the_webhook_answers_only_with_the_secret(
    api_client: AsyncClient, world: NotifWorld, telegram_settings: Settings, bot_api: FakeBotApi
) -> None:
    code = (await new_code(api_client, world.owner_14, telegram_settings))["code"]

    for secret in (None, "wrong", WEBHOOK_SECRET[:-1]):
        response = await webhook(api_client, start(code, CHAT_A), secret=secret)
        assert (response.status_code, response.json()["error_code"]) == (404, "NOT_FOUND")
    # A real link request padded past 64 KB is dropped before parsing.
    oversized = json.dumps({**start(code, CHAT_A), "padding": "y" * 70_000}).encode()
    for body in (b"not json", b'{"update_id": 3}', oversized):
        response = await webhook(api_client, body)
        assert (response.status_code, response.json()) == (200, {})
    assert bot_api.sent == []
    response = await webhook(api_client, start(code, CHAT_A))  # the same code still works
    assert bot_api.texts_to(CHAT_A)[0].startswith("Valmis")


async def test_demo_mode_links_nothing(
    db_app: FastAPI,
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    db_session: AsyncSession,
) -> None:
    code = (await new_code(api_client, world.owner_14, telegram_settings))["code"]
    db_app.state.settings = Settings(
        **{**telegram_settings.model_dump(), "DEMO_MODE_ENABLED": True}
    )

    response = await api_client.post(
        f"{API}/telegram/link-code", headers=bearer(world.owner_14, telegram_settings)
    )
    assert (response.status_code, response.json()["error_code"]) == (404, "CHANNEL_NOT_ENABLED")
    response = await webhook(api_client, start(code, CHAT_A))
    assert (response.status_code, response.json()) == (200, {})
    assert await active_bindings(db_session) == []
    assert bot_api.sent == []


async def test_at_most_five_codes_per_hour(
    api_client: AsyncClient, world: NotifWorld, telegram_settings: Settings
) -> None:
    for _ in range(5):
        await new_code(api_client, world.owner_14, telegram_settings)

    response = await api_client.post(
        f"{API}/telegram/link-code", headers=bearer(world.owner_14, telegram_settings)
    )

    assert response.status_code == 429
    body = response.json()
    assert body["error_code"] == "RATE_LIMITED"
    assert 3500 < body["retry_after_seconds"] <= 3601
    assert response.headers["Retry-After"] == str(body["retry_after_seconds"])
    await new_code(api_client, world.board_15, telegram_settings)  # per user


async def test_a_chat_gets_at_most_ten_attempts_per_hour(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    db_session: AsyncSession,
) -> None:
    for n in range(10):
        await webhook(api_client, start(f"WRONG{n:03d}", CHAT_A))
    code = (await new_code(api_client, world.owner_14, telegram_settings))["code"]

    await webhook(api_client, start(code, CHAT_A))

    assert await active_bindings(db_session) == []
    assert len(bot_api.texts_to(CHAT_A)) == 10, "no answer after the limit"


async def test_one_chat_per_user_and_one_user_per_chat(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    db_session: AsyncSession,
) -> None:
    await link(api_client, world.owner_14, CHAT_A, telegram_settings)
    await link(api_client, world.owner_14, CHAT_B, telegram_settings)  # replaces chat A
    assert [(b.user_id, b.config) for b in await active_bindings(db_session)] == [
        (world.owner_14.id, {"chat_id": CHAT_B})
    ]

    await link(api_client, world.board_15, CHAT_B, telegram_settings)  # the chat moves
    assert [(b.user_id, b.config) for b in await active_bindings(db_session)] == [
        (world.board_15.id, {"chat_id": CHAT_B})
    ]

    await link(api_client, world.board_15, CHAT_B, telegram_settings)  # already linked
    assert bot_api.texts_to(CHAT_B)[-1] == "See vestlus on juba teie kontoga seotud."
    revoked = await db_session.scalars(
        select(ChannelBinding).where(ChannelBinding.revoked_at.is_not(None))
    )
    assert all(b.config is None for b in revoked), "a revoked binding keeps no chat id"


# --- Bindings and sending -------------------------------------------------------------------------


async def test_unlinking_skips_queued_messages(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    await link(api_client, world.owner_14, CHAT_A, telegram_settings)
    await publish(api_client, world.chairman, telegram_settings)
    [binding] = await active_bindings(db_session)
    owner = bearer(world.owner_14, telegram_settings)

    other = await api_client.delete(
        f"{API}/bindings/{binding.id}", headers=bearer(world.board_15, telegram_settings)
    )
    assert (other.status_code, other.json()["error_code"]) == (404, "BINDING_NOT_FOUND")
    assert (
        await api_client.delete(f"{API}/bindings/{binding.id}", headers=owner)
    ).status_code == 204
    again = await api_client.delete(f"{API}/bindings/{binding.id}", headers=owner)
    assert again.status_code == 404

    await dispatcher.tick()
    [row] = await telegram_rows(db_session)
    assert (row.status, row.error_code) == ("SKIPPED", "BINDING_REVOKED")
    assert bot_api.texts_to(CHAT_A)[1:] == [], "only the link reply was sent"


async def test_a_blocked_bot_revokes_the_binding(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    await link(api_client, world.owner_14, CHAT_A, telegram_settings)
    await publish(api_client, world.chairman, telegram_settings)
    bot_api.status[CHAT_A] = 403

    await dispatcher.tick()

    [row] = await telegram_rows(db_session)
    assert (row.status, row.error_code) == ("FAILED", "TELEGRAM_BLOCKED")
    assert await active_bindings(db_session) == []


async def test_resolved_goes_to_the_chat_that_got_the_alert(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    world.owner_14.locale = Locale.EN
    await db_session.flush()
    await link(api_client, world.owner_14, CHAT_A, telegram_settings)
    post_id = await publish(api_client, world.chairman, telegram_settings)
    await dispatcher.tick()

    response = await api_client.post(
        f"/api/v1/communication/emergencies/{post_id}/resolve",
        headers=bearer(world.chairman, telegram_settings),
    )
    assert response.status_code == 200
    [resolved] = await telegram_rows(db_session, event_type="EMERGENCY_RESOLVED")
    [published] = await telegram_rows(db_session, event_type="EMERGENCY_PUBLISHED")
    assert resolved.binding_id == published.binding_id

    await dispatcher.tick()
    assert bot_api.texts_to(CHAT_A)[-1].startswith(f"Resolved: {TITLE}")


# --- Startup and privacy -------------------------------------------------------------------------


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_the_app_needs_a_hash_secret_with_telegram(
    telegram_settings: Settings, secret: str
) -> None:
    settings = Settings(**{**telegram_settings.model_dump(), "NOTIFICATIONS_HASH_SECRET": secret})

    with pytest.raises(RuntimeError, match="NOTIFICATIONS_HASH_SECRET"):
        create_app(settings)


async def test_no_token_chat_id_or_code_in_logs_or_stored_codes(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
    log_capture,
) -> None:
    code = (await link(api_client, world.owner_14, CHAT_A, telegram_settings))["code"]
    await publish(api_client, world.chairman, telegram_settings)
    bot_api.status[CHAT_A] = 502
    await dispatcher.tick()

    assert BOT_TOKEN not in log_capture.raw
    assert str(CHAT_A) not in log_capture.raw
    assert code not in log_capture.raw
    stored = await db_session.scalars(select(LinkCode.code_hash))
    assert all(code not in value and len(value) == 64 for value in stored)
    [binding] = await active_bindings(db_session)
    assert str(CHAT_A) not in binding.address_hash


async def test_a_bare_start_gets_a_hint_and_an_inactive_users_code_links_nothing(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    db_session: AsyncSession,
) -> None:
    bare = {
        "update_id": 1,
        "message": {"text": "/start", "chat": {"id": CHAT_A, "type": "private"}},
    }
    await webhook(api_client, bare)
    assert bot_api.texts_to(CHAT_A)[0].startswith("Siia teavituste saamiseks")

    code = (await new_code(api_client, world.owner_14, telegram_settings))["code"]
    # The test's objects are detached after a request, so change the row itself.
    await db_session.execute(
        update(User).where(User.id == world.owner_14.id).values(is_active=False)
    )
    await webhook(api_client, start(code, CHAT_A))

    assert await active_bindings(db_session) == []
    assert bot_api.texts_to(CHAT_A)[-1].startswith("See kood on vale")
    [stored] = await db_session.scalars(
        select(LinkCode).where(LinkCode.user_id == world.owner_14.id)
    )
    assert stored.used_at is None


async def test_a_chat_that_no_longer_exists_is_unlinked(
    api_client: AsyncClient,
    world: NotifWorld,
    telegram_settings: Settings,
    bot_api: FakeBotApi,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    await link(api_client, world.owner_14, CHAT_A, telegram_settings)
    await publish(api_client, world.chairman, telegram_settings)
    bot_api.status[CHAT_A] = 400
    bot_api.description[CHAT_A] = "Bad Request: chat not found"

    await dispatcher.tick()

    [row] = await telegram_rows(db_session)
    assert (row.status, row.error_code) == ("FAILED", "TELEGRAM_CHAT_NOT_FOUND")
    assert await active_bindings(db_session) == []
