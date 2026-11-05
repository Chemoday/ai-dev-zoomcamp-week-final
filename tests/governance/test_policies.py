"""Unit tests of the pure governance rules (no database)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.modules.governance.policies import (
    AnswerProblem,
    GivenAnswer,
    QuestionSpec,
    canonical_value,
    check_ballot,
    effective_status,
    electorate_units,
    target_problem,
)
from src.modules.governance.schemas import AnswerMode, PollStatus, PollUnit, ValueType
from src.modules.property.schemas import TargetScope


@pytest.mark.parametrize(
    ("value_type", "raw", "canonical"),
    [
        (ValueType.NUMBER, "2", "2"),
        (ValueType.NUMBER, " 2.50 ", "2.5"),
        (ValueType.NUMBER, "-0.000", "0"),
        (ValueType.NUMBER, "100", "100"),
        (ValueType.NUMBER, "0.000001", "0.000001"),
        (ValueType.DATE, "2026-04-12", "2026-04-12"),
        (ValueType.TEXT, "  Gloves  ", "Gloves"),
    ],
)
def test_values_are_made_canonical(value_type: ValueType, raw: str, canonical: str) -> None:
    assert canonical_value(value_type, raw, max_length=200) == canonical


@pytest.mark.parametrize(
    ("value_type", "raw"),
    [
        (ValueType.NUMBER, "1e3"),
        (ValueType.NUMBER, "1,5"),
        (ValueType.NUMBER, "1234567890123"),
        (ValueType.NUMBER, "0.0000001"),
        (ValueType.DATE, "2026-02-30"),
        (ValueType.DATE, "12.04.2026"),
        (ValueType.TEXT, "   "),
        (ValueType.TEXT, "bell\x07"),
        (ValueType.TEXT, "x" * 201),
    ],
)
def test_invalid_values_are_refused(value_type: ValueType, raw: str) -> None:
    with pytest.raises(ValueError):
        canonical_value(value_type, raw, max_length=200)


def test_a_passed_deadline_closes_an_open_poll_only() -> None:
    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    past, future = now - timedelta(seconds=1), now + timedelta(seconds=1)

    assert effective_status(PollStatus.OPEN, past, now) == PollStatus.CLOSED
    assert effective_status(PollStatus.OPEN, now, now) == PollStatus.CLOSED
    assert effective_status(PollStatus.OPEN, future, now) == PollStatus.OPEN
    assert effective_status(PollStatus.DRAFT, past, now) == PollStatus.DRAFT
    assert effective_status(PollStatus.CANCELLED, past, now) == PollStatus.CANCELLED


def test_the_units_are_apartments_or_people_once_each() -> None:
    owner, family, tenant = (uuid.uuid4() for _ in range(3))
    apt_14, apt_2 = uuid.uuid4(), uuid.uuid4()
    pairs = [(owner, apt_14), (family, apt_14), (tenant, apt_2), (owner, apt_2)]

    assert set(electorate_units(PollUnit.APARTMENT, pairs)) == {apt_14, apt_2}
    assert set(electorate_units(PollUnit.PERSON, pairs)) == {owner, family, tenant}
    assert len(electorate_units(PollUnit.PERSON, pairs)) == 3


def test_targets_follow_the_rules_of_posts() -> None:
    one = [uuid.uuid4()]
    assert target_problem(TargetScope.ALL_BUILDING, None, None) is None
    assert target_problem(TargetScope.ENTRANCE, 2, None) is None
    assert target_problem(TargetScope.APARTMENT_LIST, None, one) is None
    assert target_problem(TargetScope.ALL_BUILDING, 2, None)
    assert target_problem(TargetScope.ENTRANCE, None, None)
    assert target_problem(TargetScope.ENTRANCE, 2, one)
    assert target_problem(TargetScope.APARTMENT_LIST, None, None)
    assert target_problem(TargetScope.APARTMENT_LIST, 2, one)


A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
CHOICE = QuestionSpec(
    id=uuid.uuid4(),
    answer_mode=AnswerMode.MULTIPLE_CHOICE,
    value_type=ValueType.DATE,
    required=True,
    min_choices=1,
    max_choices=2,
    min_value=None,
    max_value=None,
    option_ids=frozenset({A, B, C}),
)
DAY = QuestionSpec(
    id=uuid.uuid4(),
    answer_mode=AnswerMode.OWN_VALUE,
    value_type=ValueType.DATE,
    required=False,
    min_choices=None,
    max_choices=None,
    min_value="2026-04-01",
    max_value="2026-04-30",
    option_ids=frozenset(),
)


def test_a_ballot_is_checked_and_ordered_by_the_questions() -> None:
    checked = check_ballot(
        [CHOICE, DAY],
        [GivenAnswer(DAY.id, None, "2026-04-12"), GivenAnswer(CHOICE.id, [B, A], None)],
    )

    assert [answer.question_id for answer in checked] == [CHOICE.id, DAY.id]
    assert checked[0].option_ids == (B, A)
    assert checked[1].value == "2026-04-12"
    assert check_ballot([CHOICE, DAY], [GivenAnswer(CHOICE.id, [C], None)])[0].option_ids == (C,)


@pytest.mark.parametrize(
    ("answers", "reason"),
    [
        ([], "REQUIRED"),
        ([GivenAnswer(CHOICE.id, [A, B, C], None)], "CHOICES"),
        ([GivenAnswer(CHOICE.id, [A, A], None)], "DUPLICATE"),
        ([GivenAnswer(CHOICE.id, [uuid.uuid4()], None)], "UNKNOWN_OPTION"),
        (
            [GivenAnswer(CHOICE.id, [A], None), GivenAnswer(DAY.id, None, "2026-05-01")],
            "OUT_OF_RANGE",
        ),
        ([GivenAnswer(CHOICE.id, [A], None), GivenAnswer(DAY.id, None, "soon")], "VALUE"),
        ([GivenAnswer(CHOICE.id, [A], None), GivenAnswer(DAY.id, [A], None)], "VALUE"),
    ],
)
def test_a_ballot_that_does_not_fit_is_refused(answers: list, reason: str) -> None:
    with pytest.raises(AnswerProblem) as problem:
        check_ballot([CHOICE, DAY], answers)

    assert problem.value.reason == reason
