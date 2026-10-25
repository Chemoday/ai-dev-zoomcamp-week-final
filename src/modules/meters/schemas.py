"""Public enums, value types and DTOs of the meters module.

Meter values are always Decimal with at most 3 decimal places (NUMERIC(12, 3)). JSON floats are
rejected on input: a float like 1254.1 cannot be represented exactly, and the value goes to
billing. Clients send values as strings ("1254.100"), and responses return them as strings.
"""

import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class ResourceType(StrEnum):
    COLD_WATER = "COLD_WATER"
    HOT_WATER = "HOT_WATER"
    ELECTRICITY = "ELECTRICITY"
    HEAT = "HEAT"
    GAS = "GAS"


class MeterUnit(StrEnum):
    M3 = "m3"
    KWH = "kWh"
    MWH = "MWh"


class PeriodStatus(StrEnum):
    OPEN_FOR_RESIDENTS = "OPEN_FOR_RESIDENTS"
    REVIEW_BY_BOARD = "REVIEW_BY_BOARD"
    LOCKED = "LOCKED"


class ReadingSource(StrEnum):
    RESIDENT = "RESIDENT"
    ADMIN_OVERRIDE = "ADMIN_OVERRIDE"
    AUTO_ESTIMATED = "AUTO_ESTIMATED"
    IOT_WEBHOOK = "IOT_WEBHOOK"


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError('meter values must be sent as strings (e.g. "1254.100"), not floats')
    return value


# NUMERIC(12, 3): up to 999 999 999.999, never negative.
MeterValue = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(ge=0, max_digits=12, decimal_places=3, allow_inf_nan=False),
]

PERIOD_CODE_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"
PeriodCode = Annotated[str, Field(pattern=PERIOD_CODE_PATTERN, examples=["2026-10"])]


class ReadingSubmitRequest(BaseModel):
    """Body of POST /api/v1/meters/readings (the 2-step anomaly flow uses confirmation_level)."""

    model_config = ConfigDict(extra="forbid")

    meter_id: uuid.UUID
    period_code: PeriodCode
    value: MeterValue
    # docs/API.md: 0 -> popup #1, 1 -> popup #2, >= 2 -> save as a confirmed anomaly.
    confirmation_level: int = Field(default=0, ge=0)
    reason: str | None = Field(default=None, max_length=500)
