"""Plugin contracts and the loader (docs/ARCHITECTURE.md, "plugins/").

A plugin is a module under src/plugins/ with a `register(registry)` function. Which plugins run is
configuration (Settings.ENABLED_PLUGINS, e.g. "common.csv_export,countries.ee.e_arve_xml"), so a
self-hosted instance enables its country adapters without code changes.

Plugins receive plain DTOs defined here, never ORM models, so they stay independent of modules.
"""

import importlib
import re
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

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


class PluginRegistry:
    def __init__(self) -> None:
        self._exporters: dict[str, ReadingExporter] = {}
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


plugin_registry = PluginRegistry()


def parse_plugin_list(value: str) -> list[str]:
    return [name.strip() for name in value.split(",") if name.strip()]


def get_plugin_registry() -> PluginRegistry:
    """FastAPI dependency; plugins are loaded once by the app factory."""
    return plugin_registry
