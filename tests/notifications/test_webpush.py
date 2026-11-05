"""WebPush on a real PostgreSQL with a fake push service: subscribing and sending (task N5).

Demo mode is off and requests authenticate with a test JWT (owner decision: external channels run
nowhere yet and are accepted by these tests).
"""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.security import create_access_token
from src.main import create_app
from src.modules.notifications.dispatcher import Dispatcher
from src.modules.notifications.models import ChannelBinding, Delivery
from src.modules.property.models import User
from src.modules.property.schemas import Locale
from src.plugins.common.webpush import (
    WebPushChannel,
    b64url_encode,
    generate_vapid_keys,
    public_key_bytes,
)
from tests.notifications.conftest import NotifWorld
from tests.webpush_browser import decrypt

JWT_SECRET = "webpush-tests-secret-at-least-32-bytes"
VAPID_PUBLIC, VAPID_PRIVATE = generate_vapid_keys()
SITE = "https://kyu.example.test"
TITLE = "Cold water off until 18:00"
BODY = "A riser in entrance 2 is being replaced."
API = "/api/v1/notifications"


@dataclass
class Browser:
    """One simulated browser profile: an endpoint and the keys it would give the site."""

    endpoint: str
    key: ec.EllipticCurvePrivateKey = field(
        default_factory=lambda: ec.generate_private_key(ec.SECP256R1())
    )
    auth: bytes = b"auth-secret-0123"

    def subscription(self, label: str = "Firefox on Linux") -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "expirationTime": None,
            "keys": {
                "p256dh": b64url_encode(public_key_bytes(self.key.public_key())),
                "auth": b64url_encode(self.auth),
            },
            "label": label,
        }


def browser(name: str) -> Browser:
    return Browser(f"https://updates.push.services.mozilla.com/wpush/v2/{name}")


@dataclass
class FakePushService:
    """Records pushes per endpoint; `status` per endpoint simulates errors (e.g. 410: gone)."""

    received: dict[str, list[bytes]] = field(default_factory=dict)
    status: dict[str, int] = field(default_factory=dict)

    def handler(self, request: httpx.Request) -> httpx.Response:
        endpoint = str(request.url)
        code = self.status.get(endpoint, 201)
        if code == 201:
            self.received.setdefault(endpoint, []).append(request.content)
        return httpx.Response(code)

    def payloads(self, device: Browser) -> list[dict[str, Any]]:
        return [
            json.loads(decrypt(body, device.key, device.auth))
            for body in self.received.get(device.endpoint, [])
        ]


@pytest.fixture
def webpush_settings() -> Settings:
    return Settings(
        **{
            **get_settings().model_dump(),
            "DEMO_MODE_ENABLED": False,
            "JWT_SECRET_KEY": JWT_SECRET,
            "ENABLED_PLUGINS": "common.csv_export,common.webpush",
            "VAPID_PUBLIC_KEY": VAPID_PUBLIC,
            "VAPID_PRIVATE_KEY": VAPID_PRIVATE,
            "VAPID_SUBJECT": "mailto:board@kyu.example.test",
            "NOTIFICATIONS_HASH_SECRET": "hash-secret-for-tests-0123456789abcdef",
            "PUBLIC_BASE_URL": SITE,
            "NOTIFICATIONS_DISPATCHER_ENABLED": False,
        }
    )


@pytest.fixture
def push_service(monkeypatch: pytest.MonkeyPatch) -> FakePushService:
    fake = FakePushService()
    monkeypatch.setattr(WebPushChannel, "http_transport", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def app(span_exporter: InMemorySpanExporter, webpush_settings: Settings) -> FastAPI:
    app = create_app(webpush_settings)
    app.dependency_overrides[get_settings] = lambda: webpush_settings
    return app


@pytest.fixture
def dispatcher(db_app: FastAPI) -> Dispatcher:
    return db_app.state.notifications_dispatcher


def bearer(user: User, settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, settings)}"}


async def subscribe(
    client: AsyncClient, user: User, settings: Settings, device: Browser, **overrides: Any
) -> httpx.Response:
    return await client.post(
        f"{API}/webpush/subscriptions",
        json=device.subscription() | overrides,
        headers=bearer(user, settings),
    )


async def active_bindings(session: AsyncSession) -> list[ChannelBinding]:
    rows = await session.scalars(
        select(ChannelBinding)
        .where(ChannelBinding.revoked_at.is_(None))
        .order_by(ChannelBinding.created_at)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def webpush_rows(session: AsyncSession) -> list[Delivery]:
    rows = await session.scalars(
        select(Delivery)
        .where(Delivery.channel == "webpush")
        .order_by(Delivery.created_at, Delivery.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def publish(client: AsyncClient, author: User, settings: Settings) -> str:
    response = await client.post(
        "/api/v1/communication/posts",
        json={"post_type": "EMERGENCY", "category": "WATER_SHUTOFF", "title": TITLE,
              "body": BODY, "target_scope": "ENTRANCE", "target_entrance": 2},
        headers=bearer(author, settings),
    )  # fmt: skip
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- Subscribing ----------------------------------------------------------------------------------


async def test_the_vapid_key_and_a_subscription(
    api_client: AsyncClient,
    world: NotifWorld,
    webpush_settings: Settings,
    db_session: AsyncSession,
) -> None:
    owner = bearer(world.owner_14, webpush_settings)
    key = await api_client.get(f"{API}/webpush/vapid-public-key", headers=owner)
    assert key.json() == {"public_key": VAPID_PUBLIC}

    device = browser("a")
    created = await subscribe(api_client, world.owner_14, webpush_settings, device)

    assert created.status_code == 201, created.text
    assert created.json()["channel"] == "webpush"
    assert created.json()["label"] == "Firefox on Linux"
    again = await subscribe(
        api_client, world.owner_14, webpush_settings, Browser(device.endpoint), label="New name"
    )
    assert again.status_code == 200
    assert again.json()["binding_id"] == created.json()["binding_id"]
    [binding] = await active_bindings(db_session)
    assert binding.label == "New name"
    assert binding.config["p256dh"] != device.subscription()["keys"]["p256dh"], "keys updated"
    assert device.endpoint not in binding.address_hash and len(binding.address_hash) == 64

    listed = await api_client.get(f"{API}/bindings", headers=owner)
    assert [b["channel"] for b in listed.json()] == ["webpush"]
    assert device.endpoint not in listed.text, "the endpoint is never returned"
    prefs = await api_client.get(f"{API}/preferences", headers=owner)
    webpush = next(c for c in prefs.json()["channels"] if c["channel"] == "webpush")
    assert webpush["available"] is True


async def test_a_shared_browser_moves_to_the_new_user(
    api_client: AsyncClient,
    world: NotifWorld,
    webpush_settings: Settings,
    push_service: FakePushService,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    device = browser("shared")
    first = await subscribe(api_client, world.owner_14, webpush_settings, device)
    await publish(api_client, world.chairman, webpush_settings)  # queued for the first user

    response = await subscribe(api_client, world.board_15, webpush_settings, device)

    assert response.status_code == 201
    [binding] = await active_bindings(db_session)
    assert binding.user_id == world.board_15.id
    old = await db_session.get(ChannelBinding, first.json()["binding_id"], populate_existing=True)
    assert old is not None and old.revoked_at is not None and old.config is None
    await dispatcher.tick()
    [row] = await webpush_rows(db_session)
    assert (row.user_id, row.status, row.error_code) == (
        world.owner_14.id,
        "SKIPPED",
        "BINDING_REVOKED",
    )
    assert push_service.received == {}, "the previous user's alert never reaches the browser"


async def test_at_most_ten_devices_per_user(
    api_client: AsyncClient, world: NotifWorld, webpush_settings: Settings
) -> None:
    for number in range(10):
        response = await subscribe(
            api_client, world.owner_14, webpush_settings, browser(f"d{number}")
        )
        assert response.status_code == 201

    response = await subscribe(api_client, world.owner_14, webpush_settings, browser("d10"))

    assert (response.status_code, response.json()["error_code"]) == (409, "DEVICE_LIMIT_REACHED")
    again = await subscribe(api_client, world.owner_14, webpush_settings, browser("d3"))
    assert again.status_code == 200, "a known device still updates at the limit"


@pytest.mark.parametrize(
    ("overrides", "error_code"),
    [
        ({"endpoint": "https://push.evil.test/x"}, "INVALID_PUSH_ENDPOINT"),
        ({"endpoint": "https://169.254.169.254/latest/meta-data"}, "INVALID_PUSH_ENDPOINT"),
        ({"endpoint": "http://fcm.googleapis.com/fcm/send/x"}, "INVALID_PUSH_ENDPOINT"),
        ({"endpoint": "https://fcm.googleapis.com/" + "x" * 2048}, "VALIDATION_ERROR"),
        ({"keys": {"p256dh": "AAAA", "auth": "AAAAAAAAAAAAAAAAAAAAAA"}}, "VALIDATION_ERROR"),
        ({"keys": {"p256dh": b64url_encode(b"\x04" + b"1" * 64), "auth": "AA"}},
         "VALIDATION_ERROR"),
        ({"keys": {"p256dh": b64url_encode(b"\x04" + b"\0" * 64),  # not on the curve
                   "auth": "AAAAAAAAAAAAAAAAAAAAAA"}}, "VALIDATION_ERROR"),
        ({"keys": {"p256dh": "not base64!", "auth": "AAAAAAAAAAAAAAAAAAAAAA"}},
         "VALIDATION_ERROR"),
        ({"label": ""}, "VALIDATION_ERROR"),
        ({"label": "x" * 101}, "VALIDATION_ERROR"),
        ({"label": "bad\u0000label"}, "VALIDATION_ERROR"),
    ],
)  # fmt: skip
async def test_invalid_subscriptions_are_rejected(
    api_client: AsyncClient,
    world: NotifWorld,
    webpush_settings: Settings,
    db_session: AsyncSession,
    overrides: dict[str, Any],
    error_code: str,
) -> None:
    response = await subscribe(
        api_client, world.owner_14, webpush_settings, browser("x"), **overrides
    )

    assert (response.status_code, response.json()["error_code"]) == (422, error_code)
    assert await active_bindings(db_session) == []


async def test_demo_mode_subscribes_nothing(
    db_app: FastAPI,
    api_client: AsyncClient,
    world: NotifWorld,
    webpush_settings: Settings,
    db_session: AsyncSession,
) -> None:
    db_app.state.settings = Settings(**{**webpush_settings.model_dump(), "DEMO_MODE_ENABLED": True})
    owner = bearer(world.owner_14, webpush_settings)

    key = await api_client.get(f"{API}/webpush/vapid-public-key", headers=owner)
    response = await subscribe(api_client, world.owner_14, webpush_settings, browser("a"))

    assert (key.status_code, key.json()["error_code"]) == (404, "CHANNEL_NOT_ENABLED")
    assert (response.status_code, response.json()["error_code"]) == (404, "CHANNEL_NOT_ENABLED")
    assert await active_bindings(db_session) == []


# --- Sending --------------------------------------------------------------------------------------


async def test_an_alert_reaches_every_device_of_a_user(
    api_client: AsyncClient,
    world: NotifWorld,
    webpush_settings: Settings,
    push_service: FakePushService,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    world.owner_14.locale = Locale.EN
    await db_session.flush()
    phone, laptop = browser("phone"), browser("laptop")
    await subscribe(api_client, world.owner_14, webpush_settings, phone)
    await subscribe(api_client, world.owner_14, webpush_settings, laptop)

    post_id = await publish(api_client, world.chairman, webpush_settings)
    rows = await webpush_rows(db_session)
    assert len(rows) == 2 and {r.binding_id for r in rows} == {
        b.id for b in await active_bindings(db_session)
    }
    assert await dispatcher.tick() == 2

    for device in (phone, laptop):
        [payload] = push_service.payloads(device)
        assert payload["title"] == f"Emergency: {TITLE}"
        assert payload["body"].startswith(f"{BODY}\n\nAffected: entrance 2")
        assert "-- " not in payload["body"], "no e-mail footer in a push"
        assert (payload["url"], payload["urgent"]) == (SITE, True)
        assert payload["tag"] == f"post:{post_id}"
    assert {r.status for r in await webpush_rows(db_session)} == {"SENT"}


async def test_a_gone_subscription_is_revoked(
    api_client: AsyncClient,
    world: NotifWorld,
    webpush_settings: Settings,
    push_service: FakePushService,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    device = browser("expired")
    await subscribe(api_client, world.owner_14, webpush_settings, device)
    post_id = await publish(api_client, world.chairman, webpush_settings)
    push_service.status[device.endpoint] = 410

    await dispatcher.tick()

    [row] = await webpush_rows(db_session)
    assert (row.status, row.error_code) == ("FAILED", "WEBPUSH_GONE")
    assert await active_bindings(db_session) == []
    revoked = await db_session.scalar(select(ChannelBinding))
    assert revoked is not None and revoked.config is None, "the endpoint is wiped"

    response = await api_client.post(
        f"/api/v1/communication/emergencies/{post_id}/resolve",
        headers=bearer(world.chairman, webpush_settings),
    )
    assert response.status_code == 200
    assert len(await webpush_rows(db_session)) == 1, "nothing more for a revoked device"


async def test_an_unsubscribed_device_skips_queued_messages(
    api_client: AsyncClient,
    world: NotifWorld,
    webpush_settings: Settings,
    push_service: FakePushService,
    dispatcher: Dispatcher,
    db_session: AsyncSession,
) -> None:
    device = browser("logout")
    created = await subscribe(api_client, world.owner_14, webpush_settings, device)
    await publish(api_client, world.chairman, webpush_settings)

    deleted = await api_client.delete(
        f"{API}/bindings/{created.json()['binding_id']}",
        headers=bearer(world.owner_14, webpush_settings),
    )
    assert deleted.status_code == 204
    await dispatcher.tick()

    [row] = await webpush_rows(db_session)
    assert (row.status, row.error_code) == ("SKIPPED", "BINDING_REVOKED")
    assert push_service.received == {}


async def test_no_endpoint_or_keys_in_logs_or_spans(
    api_client: AsyncClient,
    world: NotifWorld,
    webpush_settings: Settings,
    push_service: FakePushService,
    dispatcher: Dispatcher,
    span_exporter: InMemorySpanExporter,
    log_capture,
) -> None:
    device = browser("private-endpoint-token")
    await subscribe(api_client, world.owner_14, webpush_settings, device)
    await publish(api_client, world.chairman, webpush_settings)
    push_service.status[device.endpoint] = 503
    await dispatcher.tick()

    assert "private-endpoint-token" not in log_capture.raw
    assert device.subscription()["keys"]["p256dh"] not in log_capture.raw
    assert VAPID_PRIVATE not in log_capture.raw
    spans = span_exporter.get_finished_spans()
    assert any(s.name == "notifications.deliver" for s in spans)
    exported = " ".join(f"{s.name} {dict(s.attributes or {})}" for s in spans)
    assert "private-endpoint-token" not in exported and "mozilla" not in exported


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_the_app_needs_a_hash_secret_with_webpush(webpush_settings: Settings, secret: str) -> None:
    settings = Settings(**{**webpush_settings.model_dump(), "NOTIFICATIONS_HASH_SECRET": secret})

    with pytest.raises(RuntimeError, match="NOTIFICATIONS_HASH_SECRET"):
        create_app(settings)
