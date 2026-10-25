"""Public enums, value types and DTOs of the meters module.

Meter values are always Decimal with at most 3 decimal places (NUMERIC(12, 3)). JSON floats are
rejected on input: a float like 1254.1 cannot be represented exactly, and the value goes to
billing. Clients send values as strings ("1254.100"), and responses return them as strings.
"""

import re
import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    WithJsonSchema,
)

from src.core.validation import optional_safe_text
from src.modules.property.schemas import AuthorBadgeDTO


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


class SubmissionStatus(StrEnum):
    """Per meter and per apartment in the chairman matrix."""

    SUBMITTED = "SUBMITTED"
    MISSING = "MISSING"
    AUTO_ESTIMATED = "AUTO_ESTIMATED"
    ANOMALY_CONFIRMED = "ANOMALY_CONFIRMED"


class ReadingSource(StrEnum):
    RESIDENT = "RESIDENT"
    ADMIN_OVERRIDE = "ADMIN_OVERRIDE"
    AUTO_ESTIMATED = "AUTO_ESTIMATED"
    IOT_WEBHOOK = "IOT_WEBHOOK"


# ASCII digits only, at most 9 integer digits and 3 decimals: no exponents, signs, separators
# ("1_000"), commas or non-ASCII digits that Python's Decimal would otherwise accept.
_METER_VALUE_TEXT = re.compile(r"[0-9]{1,9}(\.[0-9]{1,3})?")


METER_PRECISION = Decimal("0.001")  # NUMERIC(12, 3)
METER_VALUE_MAX = Decimal("999999999.999")


def canonical_meter_value(value: Decimal) -> Decimal:
    """NUMERIC(12, 3) canonical form: exactly 3 decimals and never -0.

    Raises ValueError instead of rounding: losing a digit of a meter value is never silent.
    """
    quantized = value.quantize(METER_PRECISION)
    if quantized != value:
        raise ValueError(f"{value} has more than 3 decimal places")
    return quantized.copy_abs() if quantized.is_zero() else quantized


def _parse_meter_value(value: Any) -> Any:
    if isinstance(value, bool | float):
        raise ValueError('meter values must be sent as strings (e.g. "1254.100"), not floats')
    if isinstance(value, str) and not _METER_VALUE_TEXT.fullmatch(value):
        raise ValueError('meter values are plain decimals with up to 3 places, e.g. "1254.100"')
    return value


ReasonText = optional_safe_text(max_length=500)

# NUMERIC(12, 3): 0 .. 999 999 999.999, always returned with exactly 3 decimals ("1254.000").
MeterValue = Annotated[
    Decimal,
    BeforeValidator(_parse_meter_value),
    Field(ge=0, max_digits=12, decimal_places=3, allow_inf_nan=False),
    AfterValidator(canonical_meter_value),
    # The contract (docs/openapi.json) says what the API really accepts: a string, never a number.
    WithJsonSchema(
        {"type": "string", "pattern": f"^{_METER_VALUE_TEXT.pattern}$", "examples": ["1254.100"]},
        mode="validation",
    ),
]

# A meter value or delta in a response: a canonical string with exactly 3 decimals. Deltas such as
# unallocated_loss can be negative.
MeterAmount = Annotated[
    Decimal,
    WithJsonSchema(
        {"type": "string", "pattern": r"^-?[0-9]+\.[0-9]{3}$", "examples": ["1254.000"]},
        mode="serialization",
    ),
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
    # Strict: true, "2" and 1.0 are not confirmation levels.
    confirmation_level: StrictInt = Field(default=0, ge=0, le=100)
    # No control characters (a NUL byte would be a 500 from PostgreSQL); blank means None.
    reason: ReasonText = None


# --- Response DTOs (docs/API.md, sections 2 and 3) -----------------------------------------------
# Decimal fields serialize as canonical strings ("1254.000"), never as JSON numbers.


class PeriodDTO(BaseModel):
    period_code: str
    status: PeriodStatus
    due_date: datetime


class ReadingSummary(BaseModel):
    reading_id: uuid.UUID
    value: MeterAmount
    consumption_delta: MeterAmount
    source: ReadingSource
    is_anomaly_confirmed: bool


class ReadingResponse(BaseModel):
    reading_id: uuid.UUID
    meter_id: uuid.UUID
    period_code: str
    value: MeterAmount
    consumption_delta: MeterAmount
    source: ReadingSource
    is_anomaly_confirmed: bool


class DashboardMeter(BaseModel):
    meter_id: uuid.UUID
    resource_type: ResourceType
    serial_number: str
    location_label: str
    unit: MeterUnit
    previous_value: MeterAmount
    reading: ReadingSummary | None


class DashboardApartment(BaseModel):
    apartment_id: uuid.UUID
    apartment_number: str
    can_submit_readings: bool
    meters: list[DashboardMeter]


class DashboardResponse(BaseModel):
    period: PeriodDTO | None
    apartments: list[DashboardApartment]


class MatrixMeter(BaseModel):
    meter_id: uuid.UUID
    resource_type: ResourceType
    location_label: str
    unit: MeterUnit
    status: SubmissionStatus
    reading: ReadingSummary | None


class MatrixApartment(BaseModel):
    apartment_id: uuid.UUID
    apartment_number: str
    entrance: int
    floor: int
    status: SubmissionStatus
    meters: list[MatrixMeter]


class MasterMeterRow(BaseModel):
    """A Building Master Meter in the matrix: its id is what the board submits against."""

    meter_id: uuid.UUID
    resource_type: ResourceType
    location_label: str
    unit: MeterUnit
    previous_value: MeterAmount
    status: SubmissionStatus
    reading: ReadingSummary | None


class ResourceBalance(BaseModel):
    resource_type: ResourceType
    unit: MeterUnit
    master_total: MeterAmount | None  # None while any master meter of this type has no reading
    apartments_sum: MeterAmount
    unallocated_loss: MeterAmount | None


class MatrixResponse(BaseModel):
    period: PeriodDTO
    balances: list[ResourceBalance]
    master_meters: list[MasterMeterRow]
    apartments: list[MatrixApartment]


class EstimatedReading(BaseModel):
    reading_id: uuid.UUID
    meter_id: uuid.UUID
    apartment_id: uuid.UUID
    value: MeterAmount
    consumption_delta: MeterAmount


class EstimateResponse(BaseModel):
    period_code: str
    estimated_count: int
    estimated: list[EstimatedReading]


class PeriodStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PeriodStatus


class PeriodStatusUpdateResponse(BaseModel):
    period_code: str
    previous_status: PeriodStatus
    status: PeriodStatus


class HistoryEntry(BaseModel):
    changed_at: datetime
    old_value: MeterAmount | None
    new_value: MeterAmount
    source: ReadingSource
    reason: str | None
    changed_by: AuthorBadgeDTO | None  # masked; None for the system


class ReadingHistoryResponse(BaseModel):
    reading_id: uuid.UUID
    meter_id: uuid.UUID
    period_code: str
    entries: list[HistoryEntry]
