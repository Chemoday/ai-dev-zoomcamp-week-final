"""Pure meter-reading rules on Decimal (no database access; unit-tested directly).

Rules: docs/ROADMAP_MVP.md, section C "Two-Step Anomaly Confirmation".
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from src.modules.meters.schemas import (
    METER_PRECISION,
    PeriodStatus,
    ReadingSource,
    SubmissionStatus,
    canonical_meter_value,
)

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
    average = rolling_average_delta(historical_deltas)
    return default_delta if average is None else average


def rolling_average_delta(historical_deltas: Sequence[Decimal]) -> Decimal | None:
    """The same mean as calculate_rolling_average_delta, or None without usable deltas."""
    checked = [_require_decimal("historical delta", delta) for delta in historical_deltas]
    usable = [delta for delta in checked[-ROLLING_WINDOW:] if delta >= 0]
    if not usable:
        return None
    average = sum(usable, start=Decimal(0)) / len(usable)
    return canonical_meter_value(average.quantize(METER_PRECISION, rounding=ROUND_HALF_UP))


# --- Period state machine ------------------------------------------------------------------------

PERIOD_TRANSITIONS: dict[PeriodStatus, frozenset[PeriodStatus]] = {
    PeriodStatus.OPEN_FOR_RESIDENTS: frozenset({PeriodStatus.REVIEW_BY_BOARD}),
    PeriodStatus.REVIEW_BY_BOARD: frozenset({PeriodStatus.OPEN_FOR_RESIDENTS, PeriodStatus.LOCKED}),
    # Reopen before invoices are sent; never straight back to residents.
    PeriodStatus.LOCKED: frozenset({PeriodStatus.REVIEW_BY_BOARD}),
}


def can_transition_period(current: PeriodStatus, target: PeriodStatus) -> bool:
    return target in PERIOD_TRANSITIONS[PeriodStatus(current)]


# --- Matrix statuses -----------------------------------------------------------------------------

# Most urgent first: what the chairman must look at before an apartment counts as done.
_URGENCY = (
    SubmissionStatus.MISSING,
    SubmissionStatus.ANOMALY_CONFIRMED,
    SubmissionStatus.AUTO_ESTIMATED,
    SubmissionStatus.SUBMITTED,
)


def meter_submission_status(
    source: ReadingSource | None, is_anomaly_confirmed: bool
) -> SubmissionStatus:
    """Status of one meter in a period; source None means there is no reading."""
    if source is None:
        return SubmissionStatus.MISSING
    if is_anomaly_confirmed:
        return SubmissionStatus.ANOMALY_CONFIRMED
    if source == ReadingSource.AUTO_ESTIMATED:
        return SubmissionStatus.AUTO_ESTIMATED
    return SubmissionStatus.SUBMITTED


def apartment_submission_status(meter_statuses: Iterable[SubmissionStatus]) -> SubmissionStatus:
    """The most urgent status among the apartment's meters."""
    statuses = set(meter_statuses)
    return next((status for status in _URGENCY if status in statuses), SubmissionStatus.MISSING)


def period_due_date(period_code: str, deadline_day: int, timezone: str) -> datetime:
    """End of the local deadline day in the month after the period, in UTC.

    "2026-10" with day 4 in Europe/Tallinn is 2026-11-04 23:59:59 local = 21:59:59 UTC
    (docs/SCHEMA.md, meters_periods.due_date).
    """
    year, month = (int(part) for part in period_code.split("-"))
    year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    local = datetime(year, month, deadline_day, 23, 59, 59, tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC)
