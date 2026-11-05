"""Public enums and DTOs of the governance module (docs/API.md section 8, epic E3 part A).

Option values and own values are canonical strings: a text as written (cleaned), a number as a
plain decimal string ("2", "2.5"), a date as YYYY-MM-DD. `policies.canonical_value` makes them.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from src.core.validation import optional_safe_text, safe_text
from src.modules.property.schemas import TargetScope


class PollKind(StrEnum):
    INFORMATIVE = "INFORMATIVE"


class PollStatus(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class Eligibility(StrEnum):
    RESIDENTS = "RESIDENTS"
    OWNERS = "OWNERS"


class PollUnit(StrEnum):
    APARTMENT = "APARTMENT"
    PERSON = "PERSON"


class AnswerMode(StrEnum):
    SINGLE_CHOICE = "SINGLE_CHOICE"
    MULTIPLE_CHOICE = "MULTIPLE_CHOICE"
    OWN_VALUE = "OWN_VALUE"


class ValueType(StrEnum):
    TEXT = "TEXT"
    NUMBER = "NUMBER"
    DATE = "DATE"


TITLE_MAX_LENGTH = 255
DESCRIPTION_MAX_LENGTH = 5000
QUESTION_MAX_LENGTH = 500
OPTION_MAX_LENGTH = 200
OWN_TEXT_MAX_LENGTH = 2000
MAX_QUESTIONS = 20
MAX_OPTIONS = 20
MAX_TARGET_APARTMENTS = 500
POLLS_PAGE_MAX = 100

TitleText = safe_text(TITLE_MAX_LENGTH)
DescriptionText = optional_safe_text(DESCRIPTION_MAX_LENGTH)
QuestionText = safe_text(QUESTION_MAX_LENGTH)


def _unique_apartment_ids(ids: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
    """Drop duplicates (keeping order), then apply the limit; an empty list means "not given"."""
    if not ids:
        return None
    unique = list(dict.fromkeys(ids))
    if len(unique) > MAX_TARGET_APARTMENTS:
        raise ValueError(f"at most {MAX_TARGET_APARTMENTS} different apartments can be targeted")
    return unique


TargetApartmentIds = Annotated[list[uuid.UUID] | None, AfterValidator(_unique_apartment_ids)]


def _in_utc(value: datetime) -> datetime:
    """Deadlines are stored and returned in UTC, whatever offset the client sent.

    A value at the edge of the calendar (year 1 or 9999) can leave the date range when moved to
    UTC; that is a validation error (422), never an OverflowError (500).
    """
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError("closes_at is out of range") from exc


Deadline = Annotated[AwareDatetime, AfterValidator(_in_utc)]


# --- Requests ------------------------------------------------------------------------------------


class QuestionDraft(BaseModel):
    """One question of a draft. `policies.check_question` validates it and makes its option
    values and bounds canonical."""

    model_config = ConfigDict(extra="forbid")

    text: QuestionText
    answer_mode: AnswerMode
    value_type: ValueType
    required: StrictBool = True
    min_choices: StrictInt | None = None
    max_choices: StrictInt | None = None
    min_value: StrictStr | None = None
    max_value: StrictStr | None = None
    options: list[StrictStr] = Field(default_factory=list, max_length=MAX_OPTIONS)

    @model_validator(mode="after")
    def _consistent(self) -> "QuestionDraft":
        from src.modules.governance.policies import check_question

        check_question(self)  # raises ValueError, reported as 422 VALIDATION_ERROR
        return self


class PollDraftRequest(BaseModel):
    """Body of POST /governance/polls and PUT /governance/polls/{id}."""

    model_config = ConfigDict(extra="forbid")

    title: TitleText
    description: DescriptionText = None
    target_scope: TargetScope = TargetScope.ALL_BUILDING
    target_entrance: StrictInt | None = Field(default=None, ge=1, le=1000)
    target_apartment_ids: TargetApartmentIds = None
    eligibility: Eligibility
    unit: PollUnit
    closes_at: Deadline
    questions: list[QuestionDraft] = Field(min_length=1, max_length=MAX_QUESTIONS)


class DeadlineUpdate(BaseModel):
    """Body of PATCH /governance/polls/{id}: extend an open poll."""

    model_config = ConfigDict(extra="forbid")

    closes_at: Deadline


class AnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: uuid.UUID
    option_ids: list[uuid.UUID] | None = Field(default=None, max_length=MAX_OPTIONS)
    value: StrictStr | None = None

    @model_validator(mode="after")
    def _one_kind(self) -> "AnswerInput":
        if (self.option_ids is None) == (self.value is None):
            raise ValueError("give either option_ids or value")
        return self


class BallotRequest(BaseModel):
    """Body of PUT /governance/polls/{id}/ballot."""

    model_config = ConfigDict(extra="forbid")

    apartment_id: uuid.UUID | None = None
    answers: list[AnswerInput] = Field(max_length=MAX_QUESTIONS)


# --- Responses -----------------------------------------------------------------------------------


class OptionView(BaseModel):
    id: uuid.UUID
    position: int
    value: str


class QuestionView(BaseModel):
    id: uuid.UUID
    position: int
    text: str
    answer_mode: AnswerMode
    value_type: ValueType
    required: bool
    min_choices: int | None
    max_choices: int | None
    min_value: str | None
    max_value: str | None
    options: list[OptionView]


class AnswerView(BaseModel):
    question_id: uuid.UUID
    option_ids: list[uuid.UUID]
    value: str | None


class BallotView(BaseModel):
    """The current answers of one of the caller's units; never who gave them."""

    apartment_id: uuid.UUID | None
    answers: list[AnswerView]
    updated_at: datetime


class PollBase(BaseModel):
    id: uuid.UUID
    kind: PollKind
    title: str
    description: str
    status: PollStatus
    target_scope: TargetScope
    target_entrance: int | None
    target_apartment_ids: list[uuid.UUID] | None
    eligibility: Eligibility
    unit: PollUnit
    closes_at: datetime
    opened_at: datetime | None
    closed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime


class PollSummary(PollBase):
    """A poll in the list: `answered` when every unit the caller may answer for has answered
    (false when there is none, and for a cancelled poll)."""

    can_answer: bool
    answerable_apartment_ids: list[uuid.UUID]
    answered: bool


class PollView(PollBase):
    questions: list[QuestionView]
    can_answer: bool
    answerable_apartment_ids: list[uuid.UUID]
    my_ballots: list[BallotView]
