"""Pure meter-reading rules on Decimal (no database access; unit-tested directly).

Rules: docs/ROADMAP_MVP.md, section C "Two-Step Anomaly Confirmation".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

METER_PRECISION = Decimal("0.001")  # NUMERIC(12, 3)
ANOMALY_AVERAGE_FACTOR = Decimal(3)
ROLLING_WINDOW = 3  # "3-month average": the last three period deltas


class ReadingCheckStatus(StrEnum):
    OK = "OK"
    ERR_BELOW_PREV = "ERR_BELOW_PREV"
    WARN_STEP_1 = "WARN_STEP_1"
    WARN_STEP_2 = "WARN_STEP_2"


@dataclass(frozen=True)
class ReadingCheck:
    status: ReadingCheckStatus
    consumption_delta: Decimal
    is_anomaly: bool

    @property
    def is_anomaly_confirmed(self) -> bool:
        """True only for an anomalous delta the user confirmed twice (saved with the flag)."""
        return self.status == ReadingCheckStatus.OK and self.is_anomaly

    @property
    def required_confirmation_level(self) -> int | None:
        """The confirmation_level the client must send next, for the 409 payload."""
        return {ReadingCheckStatus.WARN_STEP_1: 1, ReadingCheckStatus.WARN_STEP_2: 2}.get(
            self.status
        )


def canonical_meter_value(value: Decimal) -> Decimal:
    """NUMERIC(12, 3) canonical form: exactly 3 decimals and never -0.

    Raises ValueError instead of rounding: losing a digit of a meter value is never silent.
    """
    quantized = value.quantize(METER_PRECISION)
    if quantized != value:
        raise ValueError(f"{value} has more than 3 decimal places")
    return quantized.copy_abs() if quantized.is_zero() else quantized


def _require_decimal(name: str, value: object) -> Decimal:
    # Guardrail: floats never enter meter arithmetic (AGENTS.md, "Precision").
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    return canonical_meter_value(value)


def is_anomalous_delta(delta: Decimal, max_delta: Decimal, avg_delta: Decimal) -> bool:
    """Strictly above the meter limit, or above 3x the historical average when one exists."""
    if delta > max_delta:
        return True
    return avg_delta > 0 and delta > ANOMALY_AVERAGE_FACTOR * avg_delta


def validate_reading_delta(
    prev_value: Decimal,
    new_value: Decimal,
    max_delta: Decimal,
    avg_delta: Decimal,
    confirmation_level: int,
) -> ReadingCheck:
    """Decide what happens to a submitted reading.

    - new < prev                         -> ERR_BELOW_PREV (at any confirmation level)
    - normal delta                       -> OK
    - anomalous delta, confirmation 0    -> WARN_STEP_1 (popup #1)
    - anomalous delta, confirmation 1    -> WARN_STEP_2 (popup #2)
    - anomalous delta, confirmation >= 2 -> OK, is_anomaly_confirmed=True
    """
    prev_value = _require_decimal("prev_value", prev_value)
    new_value = _require_decimal("new_value", new_value)
    max_delta = _require_decimal("max_delta", max_delta)
    avg_delta = _require_decimal("avg_delta", avg_delta)
    if confirmation_level < 0:
        raise ValueError("confirmation_level must be >= 0")

    delta = canonical_meter_value(new_value - prev_value)
    if delta < 0:
        return ReadingCheck(ReadingCheckStatus.ERR_BELOW_PREV, delta, is_anomaly=False)
    if not is_anomalous_delta(delta, max_delta, avg_delta):
        return ReadingCheck(ReadingCheckStatus.OK, delta, is_anomaly=False)
    if confirmation_level == 0:
        return ReadingCheck(ReadingCheckStatus.WARN_STEP_1, delta, is_anomaly=True)
    if confirmation_level == 1:
        return ReadingCheck(ReadingCheckStatus.WARN_STEP_2, delta, is_anomaly=True)
    return ReadingCheck(ReadingCheckStatus.OK, delta, is_anomaly=True)


def calculate_rolling_average_delta(
    historical_deltas: Sequence[Decimal], default_delta: Decimal
) -> Decimal:
    """Mean of the last ROLLING_WINDOW period deltas (chronological order, oldest first).

    Negative deltas inside the window are correction artefacts and are dropped; the window never
    reaches further back. Without usable deltas the caller's default applies (see
    docs/ROADMAP_MVP.md for which readings count as history). Rounded half-up to 0.001.
    """
    default_delta = _require_decimal("default_delta", default_delta)
    checked = [_require_decimal("historical delta", delta) for delta in historical_deltas]
    usable = [delta for delta in checked[-ROLLING_WINDOW:] if delta >= 0]
    if not usable:
        return default_delta
    average = sum(usable, start=Decimal(0)) / len(usable)
    return canonical_meter_value(average.quantize(METER_PRECISION, rounding=ROUND_HALF_UP))
