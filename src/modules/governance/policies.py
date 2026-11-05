"""Pure rules of the governance module (no database access; unit-tested directly).

docs/API.md section 8 and docs/epics/E3_GOVERNANCE_VOTING.md section 3. Part B (official
decisions) adds its majority and quorum rules here.
"""

import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from src.core.validation import clean_text
from src.modules.governance.schemas import (
    OPTION_MAX_LENGTH,
    OWN_TEXT_MAX_LENGTH,
    AnswerMode,
    PollStatus,
    PollUnit,
    ValueType,
)
from src.modules.property.schemas import BuildingRole, TargetScope

if TYPE_CHECKING:
    from src.modules.governance.schemas import QuestionDraft

# The longest a poll may stay open, from its opening or extension.
MAX_POLL_DURATION = timedelta(days=365)

# Who creates and runs polls (owner decision 2026-11-05: the board only) and sees every poll.
BOARD_ROLES = frozenset({BuildingRole.CHAIRMAN, BuildingRole.BOARD})

# A decimal string: up to 12 integer digits and 6 decimals, no exponent (NUMERIC-like, exact).
_NUMBER = re.compile(r"-?[0-9]{1,12}(\.[0-9]{1,6})?")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def is_board(role: BuildingRole) -> bool:
    return role in BOARD_ROLES


# --- Values --------------------------------------------------------------------------------------


def canonical_value(value_type: ValueType, raw: str, *, max_length: int) -> str:
    """The stored and returned form of a value; ValueError when it is not one.

    Numbers are exact decimals without trailing zeros ("2.50" -> "2.5", "-0" -> "0"); dates are
    calendar dates; texts are cleaned and never blank.
    """
    if value_type == ValueType.TEXT:
        text = clean_text(raw)
        if not text:
            raise ValueError("text must not be blank")
        if len(text) > max_length:
            raise ValueError(f"text must be at most {max_length} characters")
        return text
    if value_type == ValueType.NUMBER:
        text = raw.strip()
        if not _NUMBER.fullmatch(text):
            raise ValueError("a number is a decimal string such as 2 or 2.5")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:  # pragma: no cover - the pattern already guards it
            raise ValueError("not a number") from exc
        canonical = format(number.normalize(), "f")
        return "0" if canonical in ("-0", "0") else canonical
    text = raw.strip()
    if not _DATE.fullmatch(text):
        raise ValueError("a date is written as YYYY-MM-DD")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("not a calendar date") from exc


def value_key(value_type: ValueType, canonical: str) -> Decimal | date | str:
    """A canonical value as something that orders correctly (bounds, results)."""
    if value_type == ValueType.NUMBER:
        return Decimal(canonical)
    if value_type == ValueType.DATE:
        return date.fromisoformat(canonical)
    return canonical


# --- Drafts --------------------------------------------------------------------------------------


def check_question(question: "QuestionDraft") -> None:
    """Validate one draft question and make its options and bounds canonical (in place).

    Raises ValueError with a message for the 422 VALIDATION_ERROR response.
    """
    mode, value_type = question.answer_mode, question.value_type
    if mode == AnswerMode.OWN_VALUE:
        if question.options:
            raise ValueError("a question answered with an own value takes no options")
    else:
        if len(question.options) < 2:
            raise ValueError("a choice question needs at least 2 options")
        canonical = [
            canonical_value(value_type, option, max_length=OPTION_MAX_LENGTH)
            for option in question.options
        ]
        if len(set(canonical)) != len(canonical):
            raise ValueError("the options of a question must be different")
        question.options = canonical

    if mode == AnswerMode.MULTIPLE_CHOICE:
        low = 1 if question.min_choices is None else question.min_choices
        high = len(question.options) if question.max_choices is None else question.max_choices
        if not 1 <= low <= high <= len(question.options):
            raise ValueError("choices must satisfy 1 <= min_choices <= max_choices <= options")
        question.min_choices, question.max_choices = low, high
    elif question.min_choices is not None or question.max_choices is not None:
        raise ValueError("min_choices and max_choices are only for MULTIPLE_CHOICE")

    has_bounds = question.min_value is not None or question.max_value is not None
    if has_bounds:
        if mode != AnswerMode.OWN_VALUE or value_type == ValueType.TEXT:
            raise ValueError("bounds are only for own NUMBER or DATE values")
        bounds = [
            None if bound is None else canonical_value(value_type, bound, max_length=32)
            for bound in (question.min_value, question.max_value)
        ]
        if (
            bounds[0] is not None
            and bounds[1] is not None
            and value_key(value_type, bounds[0]) > value_key(value_type, bounds[1])
        ):
            raise ValueError("min_value must not be greater than max_value")
        question.min_value, question.max_value = bounds


def target_problem(
    scope: TargetScope, entrance: int | None, apartment_ids: Sequence[uuid.UUID] | None
) -> str | None:
    """Why a poll's target is inconsistent, or None (the same rules as for posts); whether the
    entrance or the apartments exist is checked by the service."""
    if scope == TargetScope.ALL_BUILDING:
        if entrance is not None or apartment_ids:
            return "ALL_BUILDING takes neither target_entrance nor target_apartment_ids."
    elif scope == TargetScope.ENTRANCE:
        if entrance is None:
            return "ENTRANCE needs target_entrance."
        if apartment_ids:
            return "ENTRANCE does not take target_apartment_ids."
    elif not apartment_ids:
        return "APARTMENT_LIST needs at least one apartment in target_apartment_ids."
    elif entrance is not None:
        return "APARTMENT_LIST does not take target_entrance."
    return None


# --- Lifecycle -----------------------------------------------------------------------------------


def effective_status(status: PollStatus, closes_at: datetime, now: datetime) -> PollStatus:
    """An open poll whose deadline passed is closed, without any write."""
    if status == PollStatus.OPEN and now >= closes_at:
        return PollStatus.CLOSED
    return status


def electorate_units(
    unit: PollUnit, electors: Iterable[tuple[uuid.UUID, uuid.UUID]]
) -> list[uuid.UUID]:
    """The units of the snapshot from (user_id, apartment_id) pairs: apartments or people, once
    each, in a stable order."""
    index = 1 if unit == PollUnit.APARTMENT else 0
    return sorted({pair[index] for pair in electors}, key=str)


# --- Ballots -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuestionSpec:
    """A question as the ballot check needs it (built from the stored question)."""

    id: uuid.UUID
    answer_mode: AnswerMode
    value_type: ValueType
    required: bool
    min_choices: int | None
    max_choices: int | None
    min_value: str | None
    max_value: str | None
    option_ids: frozenset[uuid.UUID]


@dataclass(frozen=True)
class GivenAnswer:
    question_id: uuid.UUID
    option_ids: Sequence[uuid.UUID] | None
    value: str | None


@dataclass(frozen=True)
class CheckedAnswer:
    question_id: uuid.UUID
    option_ids: tuple[uuid.UUID, ...]
    value: str | None


class AnswerProblem(ValueError):
    """Why a ballot is refused: the question and one of the documented reasons."""

    def __init__(self, question_id: uuid.UUID, reason: str):
        super().__init__(reason)
        self.question_id = question_id
        self.reason = reason


def check_ballot(
    questions: Sequence[QuestionSpec], answers: Iterable[GivenAnswer]
) -> list[CheckedAnswer]:
    """Validate a ballot against the poll's questions; returns the answers in question order,
    with own values made canonical. Raises AnswerProblem."""
    by_id: Mapping[uuid.UUID, QuestionSpec] = {question.id: question for question in questions}
    given: dict[uuid.UUID, GivenAnswer] = {}
    for answer in answers:
        if answer.question_id not in by_id:
            raise AnswerProblem(answer.question_id, "UNKNOWN_QUESTION")
        if answer.question_id in given:
            raise AnswerProblem(answer.question_id, "DUPLICATE")
        given[answer.question_id] = answer

    checked: list[CheckedAnswer] = []
    for question in questions:
        answer = given.get(question.id)
        if answer is None:
            if question.required:
                raise AnswerProblem(question.id, "REQUIRED")
            continue
        checked.append(_check_answer(question, answer))
    return checked


def _check_answer(question: QuestionSpec, answer: GivenAnswer) -> CheckedAnswer:
    if question.answer_mode == AnswerMode.OWN_VALUE:
        if answer.value is None:
            raise AnswerProblem(question.id, "VALUE")
        max_length = OWN_TEXT_MAX_LENGTH if question.value_type == ValueType.TEXT else 32
        try:
            value = canonical_value(question.value_type, answer.value, max_length=max_length)
        except ValueError as exc:
            raise AnswerProblem(question.id, "VALUE") from exc
        key = value_key(question.value_type, value)
        low, high = question.min_value, question.max_value
        if (low is not None and key < value_key(question.value_type, low)) or (
            high is not None and key > value_key(question.value_type, high)
        ):
            raise AnswerProblem(question.id, "OUT_OF_RANGE")
        return CheckedAnswer(question_id=question.id, option_ids=(), value=value)

    if answer.option_ids is None:
        raise AnswerProblem(question.id, "CHOICES")
    chosen = tuple(dict.fromkeys(answer.option_ids))
    if len(chosen) != len(answer.option_ids):
        raise AnswerProblem(question.id, "DUPLICATE")
    if any(option_id not in question.option_ids for option_id in chosen):
        raise AnswerProblem(question.id, "UNKNOWN_OPTION")
    if question.answer_mode == AnswerMode.SINGLE_CHOICE:
        low = high = 1
    else:
        low, high = question.min_choices or 1, question.max_choices or len(question.option_ids)
    if not low <= len(chosen) <= high:
        raise AnswerProblem(question.id, "CHOICES")
    return CheckedAnswer(question_id=question.id, option_ids=chosen, value=None)
