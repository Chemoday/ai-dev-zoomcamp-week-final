"""Domain events of module auth, stored durably in the transaction that raises them.

Payloads carry ids only: never an e-mail, a name or a token.
"""

import uuid
from dataclasses import dataclass

from src.core.event_bus import DomainEvent


@dataclass(frozen=True, kw_only=True)
class UserAccountClosed(DomainEvent):
    """An account was closed (its personal data removed); notifications drops what it kept for
    the person: bindings, preferences, link codes and the feed."""

    user_id: uuid.UUID
    building_id: uuid.UUID
