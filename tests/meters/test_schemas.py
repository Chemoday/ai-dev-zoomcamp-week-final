"""Input validation of meter values and reading submissions (no database)."""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.modules.meters.schemas import ReadingSubmitRequest


def submit(**overrides) -> ReadingSubmitRequest:
    body = {"meter_id": str(uuid.uuid4()), "period_code": "2026-10", "value": "1254.000"}
    body.update(overrides)
    return ReadingSubmitRequest.model_validate(body)


def test_valid_submission_keeps_the_exact_decimal() -> None:
    request = submit(value="1254.123")

    assert request.value == Decimal("1254.123")
    assert isinstance(request.value, Decimal)
    assert request.confirmation_level == 0
    assert request.reason is None


def test_json_round_trip_returns_values_as_strings() -> None:
    assert submit().model_dump(mode="json")["value"] == "1254.000"


def test_floats_are_rejected() -> None:
    with pytest.raises(ValidationError, match="not floats"):
        submit(value=1254.1)


def test_integers_are_accepted() -> None:
    assert submit(value=1254).value == Decimal(1254)


@pytest.mark.parametrize(
    "value",
    [
        "1.2345",  # more than 3 decimal places
        "-0.001",  # negative
        "1000000000.000",  # does not fit NUMERIC(12, 3)
        "NaN",
        "Infinity",
        "12,5",
    ],
)
def test_invalid_meter_values_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        submit(value=value)


def test_largest_value_that_fits_numeric_12_3() -> None:
    assert submit(value="999999999.999").value == Decimal("999999999.999")


@pytest.mark.parametrize("code", ["2026-13", "2026-00", "26-10", "2026-1", "2026/10", ""])
def test_invalid_period_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValidationError):
        submit(period_code=code)


@pytest.mark.parametrize("level", [-1, 3])
def test_confirmation_level_is_0_to_2(level: int) -> None:
    with pytest.raises(ValidationError):
        submit(confirmation_level=level)


def test_unknown_fields_and_long_reasons_are_rejected() -> None:
    with pytest.raises(ValidationError):
        submit(source="ADMIN_OVERRIDE")
    with pytest.raises(ValidationError):
        submit(reason="x" * 501)
