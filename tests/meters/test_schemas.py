"""Input validation and canonical form of meter values and reading submissions (no database)."""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.modules.meters.schemas import ReadingSubmitRequest


def submit(**overrides) -> ReadingSubmitRequest:
    body = {"meter_id": str(uuid.uuid4()), "period_code": "2026-10", "value": "1254.000"}
    body.update(overrides)
    return ReadingSubmitRequest.model_validate(body)


def as_json(**overrides) -> str:
    return submit(**overrides).model_dump(mode="json")["value"]


def test_valid_submission_keeps_the_exact_decimal() -> None:
    request = submit(value="1254.123")

    assert isinstance(request.value, Decimal)
    assert str(request.value) == "1254.123"
    assert request.confirmation_level == 0
    assert request.reason is None


@pytest.mark.parametrize(
    ("sent", "canonical"),
    [
        ("1254", "1254.000"),
        ("1254.1", "1254.100"),
        ("1254.10", "1254.100"),
        ("0", "0.000"),
        ("0007.5", "7.500"),
        (1254, "1254.000"),  # JSON integer
    ],
)
def test_values_are_canonical_with_exactly_three_decimals(sent, canonical: str) -> None:
    # Responses, 409/422 payloads and events all reuse this form, so it must never vary.
    assert as_json(value=sent) == canonical
    assert str(submit(value=sent).value) == canonical


@pytest.mark.parametrize("value", [1254.1, 0.0, True])
def test_floats_and_booleans_are_rejected(value) -> None:
    with pytest.raises(ValidationError, match="not floats"):
        submit(value=value)


@pytest.mark.parametrize(
    "value",
    [
        "1.2345",  # more than 3 decimal places
        "1254.1000",  # also 4 places, even though the last one is a zero
        "-0.001",
        "-0.000",  # no signs at all, so no negative zero
        "+1",
        "1000000000.000",  # does not fit NUMERIC(12, 3)
        "1E+2",  # exponent notation
        "1_000.5",  # Python digit separators
        "١٢٣",  # non-ASCII digits that Decimal() would accept
        "12,5",
        " 12.5",
        "12.",
        ".5",
        "NaN",
        "Infinity",
        "",
    ],
)
def test_non_canonical_or_invalid_values_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        submit(value=value)


def test_largest_value_that_fits_numeric_12_3() -> None:
    assert as_json(value="999999999.999") == "999999999.999"


@pytest.mark.parametrize("code", ["2026-13", "2026-00", "26-10", "2026-1", "2026/10", ""])
def test_invalid_period_codes_are_rejected(code: str) -> None:
    with pytest.raises(ValidationError):
        submit(period_code=code)


@pytest.mark.parametrize("level", [0, 1, 2, 3, 100])
def test_confirmation_levels_from_the_contract_are_accepted(level: int) -> None:
    # docs/API.md: 0 -> popup #1, 1 -> popup #2, >= 2 saves.
    assert submit(confirmation_level=level).confirmation_level == level


@pytest.mark.parametrize("level", [-1, 101, True, "2", 1.0])
def test_confirmation_level_is_a_strict_small_integer(level) -> None:
    with pytest.raises(ValidationError):
        submit(confirmation_level=level)


def test_unknown_fields_and_long_reasons_are_rejected() -> None:
    with pytest.raises(ValidationError):
        submit(source="ADMIN_OVERRIDE")
    with pytest.raises(ValidationError):
        submit(reason="x" * 501)
