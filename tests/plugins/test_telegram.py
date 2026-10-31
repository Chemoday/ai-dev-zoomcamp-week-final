"""The Telegram plugin on its own: webhook secret, update parsing, Bot API results (no database)."""

import json

import httpx
import pytest

from src.core.config import Settings
from src.plugins.common.telegram import TelegramChannel, bot_api_result
from src.plugins.registry import DeliveryResult, InboundLink, OutgoingMessage, PluginRegistry

SECRET = "s3cr3t-webhook-value-0123456789abcdef"
TOKEN = "123456789:AAFakeTokenForTestsOnly_0123456789abc"


def channel() -> TelegramChannel:
    return TelegramChannel(token=TOKEN, username="KyuTestBot", webhook_secret=SECRET)


def update(text: str, chat_type: str = "private", chat_id: int = 4242) -> bytes:
    return json.dumps(
        {"update_id": 1, "message": {"text": text, "chat": {"id": chat_id, "type": chat_type}}}
    ).encode()


def test_only_the_exact_secret_is_accepted() -> None:
    bot = channel()

    assert bot.verify({"x-telegram-bot-api-secret-token": SECRET})
    assert not bot.verify({"x-telegram-bot-api-secret-token": SECRET[:-1]})
    assert not bot.verify({})


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            update("/start K7Q2M9XA"),
            InboundLink(chat_id=4242, chat_type="private", code="K7Q2M9XA"),
        ),
        (update("/start@KyuTestBot k7q2m9xa"), InboundLink(4242, "private", "k7q2m9xa")),
        (update("/start K7Q2M9XA", chat_type="group"), InboundLink(4242, "group", "K7Q2M9XA")),
        (update("/start"), InboundLink(4242, "private", "")),  # no code: the bot gives a hint
        (update("hello"), None),
        (update("/start one two"), None),
        (b'{"update_id": 2, "edited_message": {}}', None),
        (b"not json", None),
        (b"[1, 2]", None),
    ],
)
def test_only_start_messages_with_a_code_are_link_requests(
    body: bytes, expected: InboundLink | None
) -> None:
    assert channel().parse_inbound(body) == expected


def test_the_chat_id_and_code_stay_out_of_the_repr() -> None:
    text = repr(InboundLink(chat_id=987654321, chat_type="private", code="K7Q2M9XA"))

    assert "987654321" not in text and "K7Q2M9XA" not in text


async def test_a_message_goes_to_the_bot_api_as_plain_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(TelegramChannel, "http_transport", httpx.MockTransport(handler))
    message = OutgoingMessage(
        address={"chat_id": 4242},
        locale="en",
        event_type="EMERGENCY_PUBLISHED",
        subject="Emergency: Water off",
        body="Riser.\n\n-- \nfooter",
        deep_link="https://kyu.example.test",
    )

    result = await channel().send(message)

    assert result == DeliveryResult(ok=True)
    [request] = requests
    assert request.url.path == f"/bot{TOKEN}/sendMessage"
    payload = json.loads(request.content)
    assert payload["chat_id"] == 4242
    assert payload["text"] == "Emergency: Water off\n\nRiser.\n\n-- \nfooter"
    assert "parse_mode" not in payload


async def test_a_network_error_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(TelegramChannel, "http_transport", httpx.MockTransport(handler))

    result = await channel().reply(4242, "hi")

    assert result == DeliveryResult(ok=False, retryable=True, error_code="TELEGRAM_UNAVAILABLE")


@pytest.mark.parametrize(
    ("status", "description", "expected"),
    [
        (403, "Forbidden: bot was blocked by the user",
         DeliveryResult(ok=False, error_code="TELEGRAM_BLOCKED", revoke_binding=True)),
        (400, "Bad Request: chat not found",
         DeliveryResult(ok=False, error_code="TELEGRAM_CHAT_NOT_FOUND", revoke_binding=True)),
        (400, "Bad Request: message is too long",
         DeliveryResult(ok=False, error_code="TELEGRAM_BAD_REQUEST")),
        (429, "", DeliveryResult(ok=False, retryable=True, error_code="TELEGRAM_RATE_LIMITED")),
        (502, "", DeliveryResult(ok=False, retryable=True, error_code="TELEGRAM_UNAVAILABLE")),
        (401, "", DeliveryResult(ok=False, error_code="TELEGRAM_AUTH_FAILED")),
    ],
)  # fmt: skip
def test_bot_api_answers(status: int, description: str, expected: DeliveryResult) -> None:
    assert bot_api_result(status, description.lower()) == expected


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"TELEGRAM_BOT_TOKEN": ""}, "TELEGRAM_BOT_TOKEN"),
        ({"TELEGRAM_BOT_USERNAME": "x"}, "TELEGRAM_BOT_USERNAME"),
        ({"TELEGRAM_WEBHOOK_SECRET": "short"}, "TELEGRAM_WEBHOOK_SECRET"),
        ({"TELEGRAM_WEBHOOK_SECRET": "x" * 31 + "!"}, "TELEGRAM_WEBHOOK_SECRET"),
    ],
)
def test_the_plugin_refuses_incomplete_settings(overrides: dict, error: str) -> None:
    values = {
        "TELEGRAM_BOT_TOKEN": TOKEN,
        "TELEGRAM_BOT_USERNAME": "KyuTestBot",
        "TELEGRAM_WEBHOOK_SECRET": SECRET,
        **overrides,
    }

    with pytest.raises(RuntimeError, match=error):
        PluginRegistry(Settings(**values)).load(["common.telegram"])


def test_the_plugin_registers_the_telegram_channel() -> None:
    registry = PluginRegistry(
        Settings(
            TELEGRAM_BOT_TOKEN=TOKEN,
            TELEGRAM_BOT_USERNAME="KyuTestBot",
            TELEGRAM_WEBHOOK_SECRET=SECRET,
        )
    )

    registry.load(["common.telegram"])

    bot = registry.channel("telegram")
    assert isinstance(bot, TelegramChannel)
    assert bot.deep_link("K7Q2M9XA") == "https://t.me/KyuTestBot?start=K7Q2M9XA"


async def test_set_webhook_registers_the_secret_and_message_updates_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.plugins.common.telegram import set_webhook

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    monkeypatch.setattr(TelegramChannel, "http_transport", httpx.MockTransport(handler))
    settings = Settings(
        TELEGRAM_BOT_TOKEN=TOKEN,
        TELEGRAM_WEBHOOK_SECRET=SECRET,
        PUBLIC_BASE_URL="https://kyu.example.test:8443",
    )

    assert await set_webhook(settings) == {"ok": True, "result": True}
    [request] = requests
    assert request.url.path == f"/bot{TOKEN}/setWebhook"
    assert json.loads(request.content) == {
        "url": "https://kyu.example.test:8443/api/v1/notifications/telegram/webhook",
        "secret_token": SECRET,
        "allowed_updates": ["message"],
    }
    with pytest.raises(RuntimeError, match="https"):
        await set_webhook(Settings(PUBLIC_BASE_URL="http://kyu.example.test"))


def test_a_non_object_error_body_is_a_plain_bad_request() -> None:
    from src.plugins.common.telegram import _description

    assert _description(httpx.Response(400, json=["x"])) == ""
    assert _description(httpx.Response(400, text="oops")) == ""


def test_the_admin_command_prints_only_telegrams_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from src.plugins.common import telegram

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"url": "https://x", "pending": 0}})

    monkeypatch.setattr(TelegramChannel, "http_transport", httpx.MockTransport(handler))
    settings = Settings(
        TELEGRAM_BOT_TOKEN=TOKEN,
        TELEGRAM_BOT_USERNAME="KyuTestBot",
        TELEGRAM_WEBHOOK_SECRET=SECRET,
        PUBLIC_BASE_URL="https://kyu.example.test",
    )
    monkeypatch.setattr("src.core.config.get_settings", lambda: settings)

    assert telegram.main(["webhook-info"]) == 0
    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert TOKEN not in output and SECRET not in output
