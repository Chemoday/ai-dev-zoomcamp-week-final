"""Unit tests for the pure meter-reading rules (no database)."""

from decimal import Decimal

import pytest

from src.modules.meters.policies import (
    ReadingCheckStatus,
    calculate_rolling_average_delta,
    validate_reading_delta,
)

D = Decimal
MAX = D("25.000")
AVG = D("4.200")


def check(prev: str, new: str, level: int = 0, max_delta: Decimal = MAX, avg: Decimal = AVG):
    return validate_reading_delta(D(prev), D(new), max_delta, avg, level)


# --- Acceptance criteria (Task 3) ---------------------------------------------------------------


@pytest.mark.parametrize("level", [0, 1, 2])
def test_value_below_previous_is_rejected_at_every_confirmation_level(level: int) -> None:
    result = check("124.000", "120.000", level)

    assert result.status == ReadingCheckStatus.ERR_BELOW_PREV
    assert result.consumption_delta == D("-4.000")
    assert result.is_anomaly_confirmed is False


def test_anomaly_needs_two_confirmations() -> None:
    # The example from docs/API.md: 124.000 -> 1254.000 is a delta of 1130 m3.
    step_1 = check("124.000", "1254.000", level=0)
    step_2 = check("124.000", "1254.000", level=1)
    saved = check("124.000", "1254.000", level=2)

    assert step_1.status == ReadingCheckStatus.WARN_STEP_1
    assert step_1.required_confirmation_level == 1
    assert step_2.status == ReadingCheckStatus.WARN_STEP_2
    assert step_2.required_confirmation_level == 2
    assert saved.status == ReadingCheckStatus.OK
    assert saved.is_anomaly_confirmed is True
    assert saved.required_confirmation_level is None
    for result in (step_1, step_2, saved):
        assert result.consumption_delta == D("1130.000")


# --- Normal readings ----------------------------------------------------------------------------


def test_normal_reading_is_ok_without_any_confirmation() -> None:
    result = check("124.000", "128.200")

    assert result.status == ReadingCheckStatus.OK
    assert result.is_anomaly is False
    assert result.is_anomaly_confirmed is False
    assert result.consumption_delta == D("4.200")


def test_unchanged_value_is_valid_zero_consumption() -> None:
    result = check("124.000", "124.000")

    assert result.status == ReadingCheckStatus.OK
    assert result.consumption_delta == D("0.000")


def test_confirming_a_normal_reading_does_not_mark_it_as_anomaly() -> None:
    assert check("124.000", "128.000", level=2).is_anomaly_confirmed is False


# --- Threshold boundaries -----------------------------------------------------------------------


def test_limit_is_exclusive() -> None:
    # 3 x 8.400 = 25.200 > 25.000, so only the meter limit applies here.
    at_limit = check("100.000", "125.000", avg=D("8.400"))
    just_above = check("100.000", "125.001", avg=D("8.400"))

    assert at_limit.status == ReadingCheckStatus.OK
    assert just_above.status == ReadingCheckStatus.WARN_STEP_1


def test_three_times_the_average_is_anomalous_even_below_the_meter_limit() -> None:
    # 3 x 4.200 = 12.600; both deltas are far below the 25.000 meter limit.
    assert check("100.000", "112.600").status == ReadingCheckStatus.OK
    assert check("100.000", "112.601").status == ReadingCheckStatus.WARN_STEP_1


def test_without_history_only_the_meter_limit_applies() -> None:
    assert check("100.000", "120.000", avg=D("0")).status == ReadingCheckStatus.OK
    assert check("100.000", "125.001", avg=D("0")).status == ReadingCheckStatus.WARN_STEP_1


def test_decimal_arithmetic_is_exact() -> None:
    # 0.1 + 0.2 != 0.3 in binary floats; in Decimal the delta is exactly 0.200.
    result = check("0.100", "0.300")
    assert result.consumption_delta == D("0.200")


# --- Guardrails ---------------------------------------------------------------------------------


@pytest.mark.parametrize("argument", ["prev_value", "new_value", "max_delta", "avg_delta"])
def test_floats_are_refused(argument: str) -> None:
    arguments = {
        "prev_value": D("1"),
        "new_value": D("2"),
        "max_delta": MAX,
        "avg_delta": AVG,
        "confirmation_level": 0,
    }
    arguments[argument] = 1.5

    with pytest.raises(TypeError, match="must be a Decimal"):
        validate_reading_delta(**arguments)


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity")])
def test_non_finite_values_are_refused(bad: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        validate_reading_delta(D("1"), bad, MAX, AVG, 0)


def test_negative_confirmation_level_is_refused() -> None:
    with pytest.raises(ValueError, match="confirmation_level"):
        check("1.000", "2.000", level=-1)


# --- Rolling average ----------------------------------------------------------------------------


def test_average_without_history_is_the_default_in_canonical_form() -> None:
    assert str(calculate_rolling_average_delta([], D("4.2"))) == "4.200"
    assert str(calculate_rolling_average_delta([], D("0"))) == "0.000"


def test_average_uses_only_the_last_three_deltas() -> None:
    deltas = [D("100.000"), D("2.000"), D("3.000"), D("10.000")]
    assert calculate_rolling_average_delta(deltas, D("0")) == D("5.000")


def test_average_with_shorter_history_uses_what_exists() -> None:
    assert calculate_rolling_average_delta([D("4.000"), D("5.000")], D("99")) == D("4.500")


@pytest.mark.parametrize(
    ("deltas", "expected"),
    [
        (["0.001", "0.001", "0.002"], "0.001"),  # 0.001333... rounds down
        (["1.000", "1.000", "3.001"], "1.667"),  # 1.667 exactly
        (["0.001", "0.002"], "0.002"),  # 0.0015: half-up and banker's agree
        (["0.002", "0.003"], "0.003"),  # 0.0025: half-up gives 0.003, banker's would give 0.002
    ],
)
def test_average_is_rounded_half_up_to_meter_precision(deltas: list[str], expected: str) -> None:
    result = calculate_rolling_average_delta([D(d) for d in deltas], D("0"))

    assert str(result) == expected


def test_negative_deltas_inside_the_window_are_dropped() -> None:
    deltas = [D("5.000"), D("-100.000"), D("7.000")]
    assert str(calculate_rolling_average_delta(deltas, D("0"))) == "6.000"


def test_the_window_never_reaches_further_back_than_three_periods() -> None:
    # Window = the last three periods (-1, -1, 1); the 9 from four periods ago is not used.
    deltas = [D("9.000"), D("-1.000"), D("-1.000"), D("1.000")]
    assert str(calculate_rolling_average_delta(deltas, D("0"))) == "1.000"


def test_a_window_of_only_corrections_falls_back_to_the_default() -> None:
    deltas = [D("8.000"), D("-1.000"), D("-2.000"), D("-3.000")]
    assert str(calculate_rolling_average_delta(deltas, D("0"))) == "0.000"


def test_average_refuses_floats() -> None:
    with pytest.raises(TypeError):
        calculate_rolling_average_delta([4.2], D("0"))
    with pytest.raises(TypeError):
        calculate_rolling_average_delta([], 4.2)


# --- Canonical values (regressions from the independent review) --------------------------------


def test_delta_is_canonical_even_for_non_canonical_inputs() -> None:
    result = validate_reading_delta(D("124"), D("1254.1"), MAX, AVG, 2)

    assert str(result.consumption_delta) == "1130.100"


def test_zero_delta_is_never_negative_zero() -> None:
    result = validate_reading_delta(D("-0"), D("0"), MAX, AVG, 0)

    assert str(result.consumption_delta) == "0.000"
    assert result.status == ReadingCheckStatus.OK


def test_more_than_three_decimals_are_refused_not_rounded() -> None:
    with pytest.raises(ValueError, match="more than 3 decimal places"):
        validate_reading_delta(D("1.000"), D("1.0005"), MAX, AVG, 0)


def test_confirmation_above_two_saves_like_two() -> None:
    result = check("124.000", "1254.000", level=3)

    assert result.status == ReadingCheckStatus.OK
    assert result.is_anomaly_confirmed is True
