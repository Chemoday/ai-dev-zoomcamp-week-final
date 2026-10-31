"""Telegram channel (plugin "common.telegram"): a bot that links private chats and sends alerts.

docs/API.md 6 ("Telegram linking"), docs/epics/E1_NOTIFICATIONS.md 6.2 and 10.7. Updates arrive by
webhook (no long polling); the notifications module owns codes and bindings, this plugin only
verifies the webhook secret, parses "/start <code>" and talks to the Bot API. Messages are plain
text (no parse mode), so nothing people wrote can be read as markup.

The bot token is part of every Bot API URL: it is never logged (httpx request logs are silenced
and the PII scrubber masks token patterns), and errors keep no provider text.
"""

import hmac
import json
import re
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx

from src.core.config import Settings
from src.plugins.registry import DeliveryResult, InboundLink, OutgoingMessage, PluginRegistry

CHANNEL_NAME = "telegram"
API_BASE_URL = "https://api.telegram.org"
SECRET_HEADER = "x-telegram-bot-api-secret-token"
SEND_TIMEOUT_SECONDS = 10.0
MAX_MESSAGE_LENGTH = 4096  # Bot API limit for one text message
_WEBHOOK_SECRET = re.compile(r"[A-Za-z0-9_-]{32,256}")
_USERNAME = re.compile(r"[A-Za-z0-9_]{5,32}")
# "/start CODE" or "/start@SomeBot CODE" (deep links send "/start CODE"); a bare "/start" has no
# code (the user opened the bot without the link) and gets a hint.
_START = re.compile(r"^/start(?:@\w+)?(?:\s+(\S{1,64}))?\s*$")


class TelegramChannel:
    name = CHANNEL_NAME
    # Tests swap in an httpx.MockTransport (a fake Bot API); None means the real network.
    http_transport: ClassVar[httpx.AsyncBaseTransport | None] = None

    def __init__(self, *, token: str, username: str, webhook_secret: str) -> None:
        self._token = token
        self.username = username
        self._webhook_secret = webhook_secret.encode()

    # --- Inbound --------------------------------------------------------------------------------

    def verify(self, headers: Mapping[str, str]) -> bool:
        given = headers.get(SECRET_HEADER, "")
        return hmac.compare_digest(given.encode(), self._webhook_secret)

    def parse_inbound(self, body: bytes) -> InboundLink | None:
        try:
            update = json.loads(body)
        except ValueError:
            return None
        message = update.get("message") if isinstance(update, dict) else None
        if not isinstance(message, dict):
            return None
        chat, text = message.get("chat"), message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            return None
        match = _START.match(text.strip())
        chat_id, chat_type = chat.get("id"), chat.get("type")
        if not match or not isinstance(chat_id, int) or not isinstance(chat_type, str):
            return None
        return InboundLink(chat_id=chat_id, chat_type=chat_type, code=match.group(1) or "")

    def deep_link(self, code: str) -> str:
        return f"https://t.me/{self.username}?start={code}"

    # --- Outbound -------------------------------------------------------------------------------

    async def send(self, message: OutgoingMessage) -> DeliveryResult:
        text = f"{message.subject}\n\n{message.body}"
        return await self._send_text(int(message.address["chat_id"]), text)

    async def reply(self, chat_id: int, text: str) -> DeliveryResult:
        return await self._send_text(chat_id, text)

    async def _send_text(self, chat_id: int, text: str) -> DeliveryResult:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:MAX_MESSAGE_LENGTH],
            "link_preview_options": {"is_disabled": True},
        }
        try:
            async with httpx.AsyncClient(
                base_url=API_BASE_URL,
                timeout=SEND_TIMEOUT_SECONDS,
                follow_redirects=False,
                transport=self.http_transport,
            ) as client:
                response = await client.post(f"/bot{self._token}/sendMessage", json=payload)
        except httpx.HTTPError:  # timeouts and connection errors; the text may hold the URL
            return DeliveryResult(ok=False, retryable=True, error_code="TELEGRAM_UNAVAILABLE")
        return bot_api_result(response.status_code, _description(response))


def _description(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    return str(data.get("description", "")).lower() if isinstance(data, dict) else ""


def bot_api_result(status: int, description: str = "") -> DeliveryResult:
    """Map a Bot API answer to a delivery result (machine codes only, no provider text)."""
    if status == 200:
        return DeliveryResult(ok=True)
    if status == 403:  # the user blocked the bot or deleted the chat
        return DeliveryResult(ok=False, error_code="TELEGRAM_BLOCKED", revoke_binding=True)
    if status == 400 and "chat not found" in description:
        return DeliveryResult(ok=False, error_code="TELEGRAM_CHAT_NOT_FOUND", revoke_binding=True)
    if status == 429:
        return DeliveryResult(ok=False, retryable=True, error_code="TELEGRAM_RATE_LIMITED")
    if status >= 500:
        return DeliveryResult(ok=False, retryable=True, error_code="TELEGRAM_UNAVAILABLE")
    if status in (401, 404):  # a wrong bot token: a configuration error
        return DeliveryResult(ok=False, error_code="TELEGRAM_AUTH_FAILED")
    return DeliveryResult(ok=False, error_code="TELEGRAM_BAD_REQUEST")


def register(registry: PluginRegistry) -> None:
    settings = registry.settings
    token = settings.TELEGRAM_BOT_TOKEN.get_secret_value()
    secret = settings.TELEGRAM_WEBHOOK_SECRET.get_secret_value()
    if not token:
        raise RuntimeError("plugin common.telegram needs TELEGRAM_BOT_TOKEN")
    if not _USERNAME.fullmatch(settings.TELEGRAM_BOT_USERNAME):
        raise RuntimeError("plugin common.telegram needs TELEGRAM_BOT_USERNAME (the bot's name)")
    if not _WEBHOOK_SECRET.fullmatch(secret):
        # A short or missing secret would leave the webhook open to anyone.
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET must be 32-256 characters of [A-Za-z0-9_-]")
    registry.register_channel(
        TelegramChannel(token=token, username=settings.TELEGRAM_BOT_USERNAME, webhook_secret=secret)
    )


# --- Admin command: register the webhook (never done on startup) --------------------------------

WEBHOOK_PATH = "/api/v1/notifications/telegram/webhook"


async def _bot_api(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=API_BASE_URL,
        timeout=SEND_TIMEOUT_SECONDS,
        follow_redirects=False,
        transport=TelegramChannel.http_transport,
    ) as client:
        response = await client.post(f"/bot{token}/{method}", json=payload)
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


async def set_webhook(settings: Settings) -> dict[str, Any]:
    """Point the bot at this instance: PUBLIC_BASE_URL + WEBHOOK_PATH, with the secret Telegram
    sends back on every update, and only message updates."""
    if not settings.PUBLIC_BASE_URL.startswith("https://"):
        raise RuntimeError("Telegram needs an https PUBLIC_BASE_URL (ports 443, 80, 88 or 8443)")
    return await _bot_api(
        settings.TELEGRAM_BOT_TOKEN.get_secret_value(),
        "setWebhook",
        {
            "url": settings.PUBLIC_BASE_URL + WEBHOOK_PATH,
            "secret_token": settings.TELEGRAM_WEBHOOK_SECRET.get_secret_value(),
            "allowed_updates": ["message"],
        },
    )


async def webhook_info(settings: Settings) -> dict[str, Any]:
    return await _bot_api(settings.TELEGRAM_BOT_TOKEN.get_secret_value(), "getWebhookInfo", {})


def main(argv: list[str] | None = None) -> int:
    """`uv run python -m src.plugins.common.telegram set-webhook | webhook-info`."""
    import argparse
    import asyncio

    from src.core.config import get_settings

    parser = argparse.ArgumentParser(prog="python -m src.plugins.common.telegram")
    parser.add_argument("command", choices=["set-webhook", "webhook-info"])
    args = parser.parse_args(argv)
    settings = get_settings()
    registry = PluginRegistry(settings)
    register(registry)  # the same checks as at startup
    action = set_webhook if args.command == "set-webhook" else webhook_info
    result = asyncio.run(action(settings))
    # Only Telegram's own answer (ok, description, the webhook state); never the token.
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
