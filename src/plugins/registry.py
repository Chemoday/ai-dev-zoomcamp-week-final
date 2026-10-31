"""Plugin contracts and the loader (docs/ARCHITECTURE.md, "plugins/").

A plugin is a module under src/plugins/ with a `register(registry)` function. Which plugins run is
configuration (Settings.ENABLED_PLUGINS, e.g. "common.csv_export,countries.ee.e_arve_xml"), so a
self-hosted instance enables its country adapters without code changes.

Plugins receive plain DTOs defined here, never ORM models, so they stay independent of modules.
"""

import importlib
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from fastapi import Request

from src.core.config import Settings

_PLUGIN_NAME = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)*")


@dataclass(frozen=True)
class ReadingExportRow:
    """One meter reading of a period, as exporters see it (no personal data)."""

    period_code: str
    entrance: int | None  # None for a Building Master Meter
    apartment_number: str | None  # None for a Building Master Meter
    meter_id: uuid.UUID
    resource_type: str
    serial_number: str
    location_label: str
    unit: str
    previous_value: Decimal
    value: Decimal
    consumption_delta: Decimal
    source: str
    is_anomaly_confirmed: bool
    recorded_at: datetime


class ReadingExporter(Protocol):
    name: str
    media_type: str
    file_extension: str

    def render(self, rows: Iterable[ReadingExportRow]) -> Iterator[str]:
        """Yield the file in chunks, so large exports can be streamed."""
        ...


@dataclass(frozen=True, eq=False)
class OutgoingMessage:
    """One message to one address on one external channel (docs/ARCHITECTURE.md 3.2).

    Built by the dispatcher right before the send and never stored or logged. The repr hides the
    address, the texts and the link, so an accidental log line leaks nothing; never pass the
    message to asdict(), vars() or log fields, which would expose them.
    """

    # Channel-specific: {"email": ...}, a Telegram {"chat_id": ...}, a WebPush subscription, or an
    # FCM {"token": ..., "platform": ...}.
    address: Mapping[str, object] = field(repr=False)
    locale: str
    event_type: str  # e.g. "EMERGENCY_PUBLISHED"; push channels pass it to the app
    subject: str = field(repr=False)
    body: str = field(repr=False)
    deep_link: str = field(repr=False)
    urgent: bool = False  # emergencies: high priority on push channels
    # "post:{id}" or "{event_type}:{period_id}": a newer push with the same key replaces the older.
    collapse_key: str | None = None
    # Push channels drop the message after this moment (emergencies: 1 hour after the delivery row).
    expires_at: datetime | None = None
    # The trace of the request that caused the event; e-mail carries it as X-Trace-Id.
    trace_id: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    """What a channel reports back; error codes are machine codes, never provider text."""

    ok: bool
    retryable: bool = False
    error_code: str | None = None
    revoke_binding: bool = False


class NotificationChannel(Protocol):
    name: str  # "email", "telegram", "webpush", "mobile_push"

    async def send(self, message: OutgoingMessage) -> DeliveryResult: ...


@dataclass(frozen=True)
class InboundLink:
    """A "/start <code>" message to the bot: a request to link this chat (docs/ARCHITECTURE.md
    3.2); `code` is "" for a bare "/start". `chat_id` is personal data: never log it."""

    chat_id: int = field(repr=False)
    chat_type: str  # "private", "group", "supergroup", "channel"
    code: str = field(repr=False)


class InboundChannel(Protocol):
    """Optional second face of a channel plugin that receives webhooks (common.telegram)."""

    def verify(self, headers: Mapping[str, str]) -> bool:
        """Whether the request really comes from the provider; False -> 404."""
        ...

    def parse_inbound(self, body: bytes) -> InboundLink | None:
        """The link request in the update, or None for anything else (ignored, 200)."""
        ...

    async def reply(self, chat_id: int, text: str) -> DeliveryResult:
        """Answer the chat (e.g. "linked"); best effort."""
        ...


class PluginRegistry:
    def __init__(self, settings: Settings | None = None) -> None:
        # Plugins read their own settings from here in register(); incomplete ones raise there,
        # so the app does not start with a half-configured channel.
        self.settings = settings or Settings()
        self._exporters: dict[str, ReadingExporter] = {}
        self._channels: dict[str, NotificationChannel] = {}
        self._loaded: set[str] = set()

    def register_exporter(self, exporter: ReadingExporter) -> None:
        if exporter.name in self._exporters:
            raise ValueError(f"exporter {exporter.name!r} is already registered")
        self._exporters[exporter.name] = exporter

    def exporter(self, name: str) -> ReadingExporter:
        try:
            return self._exporters[name]
        except KeyError:
            raise LookupError(f"no exporter {name!r}; enabled: {sorted(self._exporters)}") from None

    def register_channel(self, channel: NotificationChannel) -> None:
        if channel.name in self._channels:
            raise ValueError(f"channel {channel.name!r} is already registered")
        self._channels[channel.name] = channel

    def channel(self, name: str) -> NotificationChannel | None:
        """The channel plugin, or None when it is not enabled (the channel is then unavailable)."""
        return self._channels.get(name)

    def channels(self) -> list[NotificationChannel]:
        return list(self._channels.values())

    def is_enabled(self, plugin_name: str) -> bool:
        """Whether a plugin (e.g. "common.email_smtp") is loaded in this app."""
        return plugin_name in self._loaded

    def load(self, plugin_names: Iterable[str]) -> None:
        """Import src.plugins.<name> and call its register(); loading twice is a no-op."""
        for name in plugin_names:
            if name in self._loaded:
                continue
            if not _PLUGIN_NAME.fullmatch(name):
                raise ValueError(f"invalid plugin name {name!r}")
            module = importlib.import_module(f"src.plugins.{name}")
            module.register(self)
            self._loaded.add(name)


def parse_plugin_list(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def build_plugin_registry(settings: Settings) -> PluginRegistry:
    registry = PluginRegistry(settings)
    registry.load(parse_plugin_list(settings.ENABLED_PLUGINS))
    return registry


def get_plugin_registry(request: Request) -> PluginRegistry:
    """FastAPI dependency: the registry of this app instance (built by create_app)."""
    return request.app.state.plugin_registry
