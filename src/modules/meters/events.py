"""Domain events of the meters module, published on core.event_bus.

Payloads carry ids, codes and Decimal values only: never names, emails or phones.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from src.core.event_bus import DomainEvent
from src.modules.meters.schemas import PeriodStatus, ReadingSource


@dataclass(frozen=True, kw_only=True)
class MeterReadingSubmitted(DomainEvent):
    """A reading was created or changed (by a resident, an admin, an estimate or IoT)."""

    reading_id: uuid.UUID
    meter_id: uuid.UUID
    apartment_id: uuid.UUID | None  # None for a Building Master Meter
    period_code: str
    value: Decimal
    consumption_delta: Decimal
    source: ReadingSource
    is_anomaly_confirmed: bool
    submitted_by_user_id: uuid.UUID | None  # None for AUTO_ESTIMATED


@dataclass(frozen=True, kw_only=True)
class ReadingPeriodStatusChanged(DomainEvent):
    """A period moved through OPEN_FOR_RESIDENTS -> REVIEW_BY_BOARD -> LOCKED (or was reopened)."""

    period_id: uuid.UUID
    building_id: uuid.UUID
    period_code: str
    old_status: PeriodStatus
    new_status: PeriodStatus
    changed_by_user_id: uuid.UUID | None  # None for an automatic change (e.g. the deadline)
