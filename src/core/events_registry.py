"""Registry of the domain event classes that may be stored in core_domain_events.

Stored events are a contract (docs/BACKEND_CONVENTIONS.md 2.B): a row written today must still
be rebuilt by the relay after a deploy. So every stored class is registered here by name, and
payloads go through a pydantic TypeAdapter, which gives UUIDs, Decimals, tuples and enums back
with their types. A new field needs a default; a renamed class keeps its old name as an alias.
Renaming a field is not supported (old rows would fail as PAYLOAD_INVALID): add a new field with a
default instead. Unknown keys in a stored payload are ignored.

Module event classes are listed in `_MODULE_EVENT_MODULES` and imported lazily, the same way
src/core/models_registry.py lists the models: core never imports a module at import time.
"""

import importlib
import inspect
import uuid
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter

from src.core.event_bus import DomainEvent

_MODULE_EVENT_MODULES = (
    "src.modules.communication.events",
    "src.modules.meters.events",
)

# Stored on the row itself, not in the payload.
_ENVELOPE_FIELDS = frozenset({"event_id", "occurred_at"})


class UnknownEventTypeError(LookupError):
    """The event class is not registered, so it cannot be stored or rebuilt."""


class EventRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, type[DomainEvent]] = {}
        self._adapters: dict[type[DomainEvent], TypeAdapter[Any]] = {}

    def register(self, event_cls: type[DomainEvent], *aliases: str) -> None:
        """Register a class under its name (and old names). Re-registering the same class is a
        no-op; a different class under a taken name is an error."""
        names = (event_cls.__name__, *aliases)
        for name in names:  # check every name first, so a conflict changes nothing
            existing = self._by_name.get(name)
            if existing is not None and existing is not event_cls:
                raise ValueError(f"event name {name!r} is already registered for {existing!r}")
        adapter = self._adapters.get(event_cls) or TypeAdapter(event_cls)
        for name in names:
            self._by_name[name] = event_cls
        self._adapters[event_cls] = adapter

    def names(self) -> frozenset[str]:
        return frozenset(self._by_name)

    def classes(self) -> frozenset[type[DomainEvent]]:
        return frozenset(self._by_name.values())

    def encode(self, event: DomainEvent) -> dict[str, Any]:
        """The JSON-ready payload: every field except the envelope (event_id, occurred_at)."""
        adapter = self._adapters.get(type(event))
        if adapter is None:
            raise UnknownEventTypeError(event.event_name)
        data = adapter.dump_python(event, mode="json")
        return {key: value for key, value in data.items() if key not in _ENVELOPE_FIELDS}

    def decode(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: uuid.UUID,
        occurred_at: datetime,
    ) -> DomainEvent:
        event_cls = self._by_name.get(event_type)
        if event_cls is None:
            raise UnknownEventTypeError(event_type)
        data = {**payload, "event_id": event_id, "occurred_at": occurred_at}
        return self._adapters[event_cls].validate_python(data)


def _register_module_events(registry: EventRegistry) -> None:
    for module_name in _MODULE_EVENT_MODULES:
        module = importlib.import_module(module_name)
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(member, DomainEvent)
                and member is not DomainEvent
                and member.__module__ == module_name
            ):
                registry.register(member)


_registry: EventRegistry | None = None


def get_event_registry() -> EventRegistry:
    """The process-wide registry, with every module event registered on first use."""
    global _registry
    if _registry is None:
        registry = EventRegistry()
        _register_module_events(registry)
        _registry = registry
    return _registry
